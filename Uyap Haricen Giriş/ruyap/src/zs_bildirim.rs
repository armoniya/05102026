//! /zs İş Takip — bildirimler (zs_bildirim.py 1:1 port)
//! ------------------------------------------------------
//! Kanallar: Web Push (telefon/bilgisayar; zs_push.rs) ve Telegram botu. Bir kişi ikisini birden
//! açabilir; her bildirim kişinin açık tüm kanallarına gider.

use std::collections::{HashMap, HashSet};
use std::env;
use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use chrono::{FixedOffset, TimeZone};
use p256::SecretKey;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tokio::sync::{Mutex, Notify, RwLock};

use crate::zs_push;
use crate::zs_store::ZsStore;

pub const TG_API: &str = "https://api.telegram.org";
pub const REMIND_EVERY: u64 = 30;

pub type GroupHandlerFn =
    Arc<dyn Fn(Value) -> Pin<Box<dyn Future<Output = ()> + Send>> + Send + Sync>;

fn tr_tz() -> FixedOffset {
    FixedOffset::east_opt(3 * 3600).unwrap()
}

fn now_epoch() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64
}

pub fn _fmt(ts: Option<i64>) -> String {
    match ts {
        Some(t) if t != 0 => tr_tz()
            .timestamp_opt(t, 0)
            .single()
            .map(|dt| dt.format("%d.%m.%Y %H:%M").to_string())
            .unwrap_or_else(|| "tarihsiz".to_string()),
        _ => "tarihsiz".to_string(),
    }
}

pub fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#x27;")
}

/// Olaylardan `[(alıcı, {title, body, task_id, tag})]` üretir. Saf fonksiyon (test edilebilir).
/// Aynı istekte aynı kişiye aynı iş için birden çok olay varsa yalnız ilki gider.
pub fn build_messages(
    events: &[Value],
    admins: &[i64],
    names: &HashMap<i64, String>,
) -> Vec<(i64, Value)> {
    let mut out: Vec<(i64, Value)> = Vec::new();
    let mut seen: HashSet<(i64, i64)> = HashSet::new();

    let mut add = |uid_opt: Option<i64>, e: &Value, title: String, body: String| {
        let Some(uid) = uid_opt else { return };
        if uid == 0 {
            return;
        }
        let actor_id = e.get("actor_id").and_then(|v| v.as_i64());
        if actor_id == Some(uid) {
            return;
        }
        if !names.contains_key(&uid) {
            return;
        }
        let task_id = e.get("task_id").and_then(|v| v.as_i64()).unwrap_or(0);
        if !seen.insert((uid, task_id)) {
            return;
        }
        out.push((
            uid,
            json!({
                "title": title,
                "body": body,
                "task_id": task_id,
                "tag": format!("is-{}", task_id),
            }),
        ));
    };

    for e in events {
        let is_priv = e
            .get("is_private")
            .and_then(|v| v.as_bool().or_else(|| v.as_i64().map(|n| n != 0)))
            .unwrap_or(false);
        if is_priv {
            continue;
        }
        let d = e.get("detail").cloned().unwrap_or_else(|| json!({}));
        let k = e.get("kind").and_then(|v| v.as_str()).unwrap_or("");
        let t = e.get("title").and_then(|v| v.as_str()).unwrap_or("");
        let actor_id = e.get("actor_id").and_then(|v| v.as_i64());
        let who = actor_id
            .and_then(|id| names.get(&id))
            .map(|s| s.as_str())
            .unwrap_or("Biri");

        let approvers: Vec<i64> = e
            .get("approvers")
            .and_then(|v| v.as_array())
            .map(|arr| arr.iter().filter_map(|x| x.as_i64()).collect())
            .unwrap_or_default();
        let e_admins: Vec<i64> = e
            .get("admins")
            .and_then(|v| v.as_array())
            .map(|arr| arr.iter().filter_map(|x| x.as_i64()).collect())
            .unwrap_or_else(|| admins.to_vec());
        let deciders = if !approvers.is_empty() {
            approvers
        } else {
            e_admins
        };
        let assignees: Vec<i64> = e
            .get("assignees")
            .and_then(|v| v.as_array())
            .map(|arr| arr.iter().filter_map(|x| x.as_i64()).collect())
            .unwrap_or_default();
        let due_at = e.get("due_at").and_then(|v| v.as_i64()).filter(|&x| x != 0);

        match k {
            "assigned" => {
                let d_title = d.get("title").and_then(|v| v.as_str()).unwrap_or("");
                let sub = !d_title.is_empty() && d_title != t;
                let head = if sub {
                    format!("{} (alt iş) — {}", d_title, t)
                } else {
                    t.to_string()
                };
                let due_part = if due_at.is_some() && !sub {
                    format!(" · Son tarih: {}", _fmt(due_at))
                } else {
                    String::new()
                };
                add(
                    d.get("assignee_id").and_then(|v| v.as_i64()),
                    e,
                    "Size yeni bir iş atandı".to_string(),
                    format!("{}\nAtayan: {}{}", head, who, due_part),
                );
            }
            "member_added" => {
                let role = if d.get("role").and_then(|v| v.as_str()) == Some("approver") {
                    "onay yetkilisi"
                } else {
                    "ek atanan"
                };
                add(
                    d.get("user_id").and_then(|v| v.as_i64()),
                    e,
                    format!("Bir işe {} olarak eklendiniz", role),
                    format!("{}\nEkleyen: {}", t, who),
                );
            }
            "submitted" => {
                for uid in deciders {
                    add(
                        Some(uid),
                        e,
                        "Onayınızı bekleyen bir iş var".to_string(),
                        format!("{}\n{} onaya gönderdi", t, who),
                    );
                }
            }
            "approved" | "rejected" | "reopened" => {
                let title = match k {
                    "approved" => "İşiniz onaylandı",
                    "rejected" => "İşiniz reddedildi",
                    _ => "İş yeniden açıldı",
                };
                let reason = d.get("reason").and_then(|v| v.as_str()).unwrap_or("");
                let reason_part = if !reason.is_empty() {
                    format!(" · Gerekçe: {}", reason)
                } else {
                    String::new()
                };
                let body = format!("{}\n{}{}", t, who, reason_part);
                for uid in assignees {
                    add(Some(uid), e, title.to_string(), body.clone());
                }
            }
            "ext_requested" => {
                let old_due = d.get("old_due").and_then(|v| v.as_i64()).filter(|&x| x != 0);
                let new_due = d.get("new_due").and_then(|v| v.as_i64()).filter(|&x| x != 0);
                for uid in deciders {
                    add(
                        Some(uid),
                        e,
                        "Ek süre talebi".to_string(),
                        format!("{}\n{}: {} → {}", t, who, _fmt(old_due), _fmt(new_due)),
                    );
                }
            }
            "ext_approved" | "ext_rejected" => {
                let ok = k == "ext_approved";
                let new_due = d.get("new_due").and_then(|v| v.as_i64()).filter(|&x| x != 0);
                let note = d.get("note").and_then(|v| v.as_str()).unwrap_or("");
                let mid = if ok {
                    format!("Yeni son tarih: {}", _fmt(new_due))
                } else {
                    format!("Karar: {}", who)
                };
                let note_part = if !note.is_empty() {
                    format!(" · {}", note)
                } else {
                    String::new()
                };
                add(
                    d.get("requester_id").and_then(|v| v.as_i64()),
                    e,
                    if ok {
                        "Ek süre talebiniz kabul edildi"
                    } else {
                        "Ek süre talebiniz reddedildi"
                    }
                    .to_string(),
                    format!("{}\n{}{}", t, mid, note_part),
                );
            }
            "transferred" => {
                add(
                    d.get("to_id").and_then(|v| v.as_i64()),
                    e,
                    "Size bir iş devredildi".to_string(),
                    format!("{}\nDevreden: {}", t, who),
                );
            }
            _ => {}
        }
    }
    out
}

/// Hatırlatıcı satırından bildirim metni. Saf fonksiyon.
pub fn reminder_message(r: &Value, now_opt: Option<i64>) -> Value {
    let now = now_opt.unwrap_or_else(now_epoch);
    let remind_at = r.get("remind_at").and_then(|v| v.as_i64()).unwrap_or(0);
    let late = now - remind_at > 10 * 60;
    let task_id = r.get("task_id").and_then(|v| v.as_i64()).filter(|&x| x != 0);
    let head = if task_id.is_none() {
        r.get("title")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string()
    } else {
        let task_title = r
            .get("task_title")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let sub_title = r.get("sub_title").and_then(|v| v.as_str()).unwrap_or("");
        if !sub_title.is_empty() {
            format!("{} › {}", task_title, sub_title)
        } else {
            task_title.to_string()
        }
    };
    let mut lines = vec![head];
    if let Some(note) = r.get("note").and_then(|v| v.as_str()) {
        if !note.is_empty() {
            lines.push(note.to_string());
        }
    }
    if let Some(due_at) = r.get("due_at").and_then(|v| v.as_i64()).filter(|&x| x != 0) {
        lines.push(format!("Son tarih: {}", _fmt(Some(due_at))));
    }
    let created_by = r.get("created_by").and_then(|v| v.as_i64());
    let user_id = r.get("user_id").and_then(|v| v.as_i64());
    if created_by != user_id {
        if let Some(by_name) = r.get("by_name").and_then(|v| v.as_str()) {
            if !by_name.is_empty() {
                lines.push(format!("Hatırlatan: {}", by_name));
            }
        }
    }
    if late {
        lines.push(format!(
            "(Gecikmeli — kurulan saat: {})",
            _fmt(Some(remind_at))
        ));
    }
    let rid = r.get("id").and_then(|v| v.as_i64()).unwrap_or(0);
    json!({
        "title": "Hatırlatma",
        "body": lines.join("\n"),
        "task_id": r.get("task_id").cloned().unwrap_or(Value::Null),
        "tag": format!("hat-{}", rid),
    })
}

pub struct Notifier {
    pub store: Arc<ZsStore>,
    pub vapid_key: Option<SecretKey>,
    pub vapid_pub: Option<String>,
    pub vapid_src: String,
    pub vapid_sub: String,
    pub public_url: String,
    pub tg_token: String,
    pub tg_secret: String,
    pub tg_bot: RwLock<Option<String>>,
    pub tg_mode: RwLock<String>,
    pub last_id: Mutex<i64>,
    pub lock: Mutex<()>,
    pub client: reqwest::Client,
    pub test_sink: Mutex<Option<Vec<(i64, Value)>>>,
    pub group_handler: RwLock<Option<GroupHandlerFn>>,
    stopped: AtomicBool,
    stop_notify: Notify,
}

impl Notifier {
    pub fn new(store: Arc<ZsStore>) -> Self {
        let (vapid_key, vapid_pub, vapid_src) = zs_push::load_vapid();
        let vapid_sub = env::var("ZS_VAPID_SUB")
            .ok()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "https://cokluuyap.com".to_string());
        let public_url = env::var("ZS_PUBLIC_URL")
            .unwrap_or_default()
            .trim()
            .trim_end_matches('/')
            .to_string();
        let tg_token = env::var("ZS_TELEGRAM_TOKEN")
            .unwrap_or_default()
            .trim()
            .to_string();
        let tg_secret = if tg_token.is_empty() {
            String::new()
        } else {
            let mut hasher = Sha256::new();
            hasher.update(format!("zs-tg|{}", tg_token).as_bytes());
            let hex_s = hex::encode(hasher.finalize());
            hex_s[..48].to_string()
        };
        let last_id = store.max_event_id();
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(70))
            .build()
            .unwrap_or_else(|_| reqwest::Client::new());
        Self {
            store,
            vapid_key,
            vapid_pub,
            vapid_src,
            vapid_sub,
            public_url,
            tg_token,
            tg_secret,
            tg_bot: RwLock::new(None),
            tg_mode: RwLock::new("kapalı".to_string()),
            last_id: Mutex::new(last_id),
            lock: Mutex::new(()),
            client,
            test_sink: Mutex::new(None),
            group_handler: RwLock::new(None),
            stopped: AtomicBool::new(false),
            stop_notify: Notify::new(),
        }
    }

    pub async fn describe(&self) -> String {
        let push = if self.vapid_key.is_some() {
            format!("Web Push açık ({})", self.vapid_src)
        } else {
            format!("Web Push KAPALI ({})", self.vapid_src)
        };
        let mode = self.tg_mode.read().await;
        format!("{}; Telegram: {}", push, *mode)
    }

    pub async fn start(self: &Arc<Self>) {
        let me_rem = Arc::clone(self);
        tokio::spawn(async move {
            me_rem._remind_loop().await;
        });
        if self.tg_token.is_empty() {
            return;
        }
        match self._tg("getMe", json!({})).await {
            Ok(me) => {
                let uname = me
                    .get("username")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string());
                *self.tg_bot.write().await = uname;
            }
            Err(e) => {
                let msg = format!("HATA (getMe: {})", e);
                *self.tg_mode.write().await = msg.clone();
                println!("[!] zs Telegram: {}", msg);
                return;
            }
        }
        let bot_name = self.tg_bot.read().await.clone().unwrap_or_default();
        if self.public_url.starts_with("https://") {
            let hook_url = format!("{}/zs/telegram/hook", self.public_url);
            match self
                ._tg(
                    "setWebhook",
                    json!({
                        "url": hook_url,
                        "secret_token": self.tg_secret,
                        "allowed_updates": ["message"],
                        "drop_pending_updates": false,
                    }),
                )
                .await
            {
                Ok(_) => {
                    *self.tg_mode.write().await = format!("@{}, webhook", bot_name);
                }
                Err(e) => {
                    *self.tg_mode.write().await =
                        format!("@{}, webhook HATASI: {}", bot_name, e);
                }
            }
        } else {
            let _ = self._tg("deleteWebhook", json!({})).await;
            let me_poll = Arc::clone(self);
            tokio::spawn(async move {
                me_poll._poll().await;
            });
            *self.tg_mode.write().await = format!("@{}, yoklama (yerel)", bot_name);
        }
        println!("[*] İş Takip bildirim: {}", self.describe().await);
    }

    pub fn stop(&self) {
        self.stopped.store(true, Ordering::SeqCst);
        self.stop_notify.notify_waiters();
    }

    pub fn kick(self: &Arc<Self>) {
        let me = Arc::clone(self);
        tokio::spawn(async move {
            me.process().await;
        });
    }

    pub async fn process(&self) {
        let msgs = {
            let _guard = self.lock.lock().await;
            let mut last = self.last_id.lock().await;
            let cur_last = *last;
            let store = Arc::clone(&self.store);
            let res = tokio::task::spawn_blocking(move || store.events_for_notify(cur_last)).await;
            let (events, admins, names) = match res {
                Ok(Ok(v)) => v,
                Ok(Err(e)) => {
                    println!("[!] zs bildirim olayları okunamadı: {}", e);
                    return;
                }
                Err(e) => {
                    println!("[!] zs bildirim olayları okunamadı: {}", e);
                    return;
                }
            };
            if events.is_empty() {
                return;
            }
            let max_id = events
                .iter()
                .filter_map(|e| e.get("id").and_then(|v| v.as_i64()))
                .max()
                .unwrap_or(cur_last);
            *last = max_id;
            build_messages(&events, &admins, &names)
        };
        for (uid, m) in msgs {
            self.deliver(uid, m).await;
        }
    }

    pub async fn deliver(&self, uid: i64, m: Value) {
        {
            let mut sink = self.test_sink.lock().await;
            if let Some(ref mut vec) = *sink {
                vec.push((uid, m));
                return;
            }
        }
        let store = Arc::clone(&self.store);
        let linked = tokio::task::spawn_blocking(move || store.is_linked(uid))
            .await
            .unwrap_or(false);
        let base = if linked { "/is-listesi" } else { "/zs" };
        let url = match m.get("task_id").and_then(|v| v.as_i64()).filter(|&x| x != 0) {
            Some(tid) => format!("{}#is-{}", base, tid),
            None => base.to_string(),
        };

        if let (Some(ref key), Some(ref pub_b64)) = (&self.vapid_key, &self.vapid_pub) {
            let store_p = Arc::clone(&self.store);
            let subs = tokio::task::spawn_blocking(move || store_p.push_list(&[uid]))
                .await
                .unwrap_or_default();
            let payload = json!({
                "title": m.get("title").and_then(|v| v.as_str()).unwrap_or(""),
                "body": m.get("body").and_then(|v| v.as_str()).unwrap_or(""),
                "url": url,
                "tag": m.get("tag").and_then(|v| v.as_str()).unwrap_or("zs"),
            });
            for s in subs {
                self._push_one(s, &payload, key, pub_b64).await;
            }
        }

        if !self.tg_token.is_empty() {
            let store_t = Arc::clone(&self.store);
            let links = tokio::task::spawn_blocking(move || store_t.tg_get(&[uid]))
                .await
                .unwrap_or_default();
            let title = m.get("title").and_then(|v| v.as_str()).unwrap_or("");
            let body = m.get("body").and_then(|v| v.as_str()).unwrap_or("");
            let mut text = format!("<b>{}</b>\n{}", html_escape(title), html_escape(body));
            if !self.public_url.is_empty() {
                let full_url = format!("{}{}", self.public_url, url);
                text.push_str(&format!("\n<a href=\"{}\">İşi aç</a>", html_escape(&full_url)));
            }
            for link in links {
                if let Some(cid) = link.get("chat_id").and_then(|v| v.as_i64()) {
                    self._tg_send(cid, &text).await;
                }
            }
        }
    }

    async fn _push_one(&self, sub: Value, payload: &Value, key: &SecretKey, pub_b64: &str) {
        let sub_id = sub.get("id").and_then(|v| v.as_i64()).unwrap_or(0);
        let endpoint = sub
            .get("endpoint")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let fails = sub.get("fails").and_then(|v| v.as_i64()).unwrap_or(0);
        match zs_push::send(
            &self.client,
            &sub,
            payload,
            key,
            pub_b64,
            &self.vapid_sub,
            24 * 3600,
        )
        .await
        {
            Ok(()) => {
                let st = Arc::clone(&self.store);
                let _ = tokio::task::spawn_blocking(move || st.push_result(sub_id, true)).await;
            }
            Err(zs_push::PushSendError::Gone(_)) => {
                let st = Arc::clone(&self.store);
                let ep = endpoint.clone();
                let _ = tokio::task::spawn_blocking(move || st.push_remove(&ep, None)).await;
            }
            Err(zs_push::PushSendError::Other(e)) => {
                println!("[!] zs push gönderilemedi (abonelik {}): {}", sub_id, e);
                let st = Arc::clone(&self.store);
                let _ = tokio::task::spawn_blocking(move || st.push_result(sub_id, false)).await;
                if fails >= 20 {
                    let st2 = Arc::clone(&self.store);
                    let ep = endpoint.clone();
                    let _ = tokio::task::spawn_blocking(move || st2.push_remove(&ep, None)).await;
                }
            }
        }
    }

    // ── hatırlatıcılar ──
    pub async fn remind_tick(&self, now: Option<i64>) -> usize {
        let st = Arc::clone(&self.store);
        let rows = tokio::task::spawn_blocking(move || st.claim_due_reminders(now, 100))
            .await
            .unwrap_or_default();
        let mut n = 0;
        for r in rows {
            let active = r
                .get("user_active")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            if !active {
                continue;
            }
            let uid = r.get("user_id").and_then(|v| v.as_i64()).unwrap_or(0);
            self.deliver(uid, reminder_message(&r, now)).await;
            n += 1;
        }
        n
    }

    async fn _remind_loop(&self) {
        while !self.stopped.load(Ordering::SeqCst) {
            self.remind_tick(None).await;
            tokio::select! {
                _ = tokio::time::sleep(Duration::from_secs(REMIND_EVERY)) => {}
                _ = self.stop_notify.notified() => break,
            }
        }
    }

    // ── Telegram ──
    pub async fn _tg(&self, method: &str, params: Value) -> Result<Value, String> {
        let url = format!("{}/bot{}/{}", TG_API, self.tg_token, method);
        let resp = self
            .client
            .post(&url)
            .json(&params)
            .timeout(Duration::from_secs(70))
            .send()
            .await
            .map_err(|e| e.to_string())?;
        let status = resp.status().as_u16();
        let d: Value = resp.json().await.map_err(|e| e.to_string())?;
        if !d.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
            let desc = d
                .get("description")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
                .unwrap_or_else(|| format!("HTTP {}", status));
            return Err(desc);
        }
        Ok(d.get("result").cloned().unwrap_or(Value::Null))
    }

    async fn _tg_send(&self, chat_id: i64, text: &str) {
        let res = self
            ._tg(
                "sendMessage",
                json!({
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": true,
                }),
            )
            .await;
        if let Err(msg) = res {
            println!("[!] zs Telegram gönderilemedi: {}", msg);
            if msg.contains("blocked")
                || msg.contains("chat not found")
                || msg.contains("deactivated")
            {
                let st = Arc::clone(&self.store);
                let _ = tokio::task::spawn_blocking(move || st.tg_unlink(None, Some(chat_id))).await;
            }
        }
    }

    pub async fn tg_link_url(&self, code: &str) -> Option<String> {
        let bot = self.tg_bot.read().await;
        bot.as_ref().map(|b| format!("https://t.me/{}?start={}", b, code))
    }

    pub async fn tg_reply(&self, chat_id: i64, text: &str, reply_to: Option<i64>) -> Option<i64> {
        let mut params = json!({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": true,
        });
        if let Some(mid) = reply_to {
            params["reply_parameters"] = json!({
                "message_id": mid,
                "allow_sending_without_reply": true,
            });
        }
        match self._tg("sendMessage", params).await {
            Ok(r) => r.get("message_id").and_then(|v| v.as_i64()),
            Err(e) => {
                println!("[!] zs Telegram grup yanıtı gönderilemedi: {}", e);
                None
            }
        }
    }

    pub async fn tg_download(&self, file_id: &str, limit: usize) -> Result<Vec<u8>, String> {
        let info = self._tg("getFile", json!({ "file_id": file_id })).await?;
        let fsize = info.get("file_size").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
        if fsize > limit {
            return Err("büyük".to_string());
        }
        let fpath = info
            .get("file_path")
            .and_then(|v| v.as_str())
            .ok_or_else(|| "file_path yok".to_string())?;
        let url = format!("{}/file/bot{}/{}", TG_API, self.tg_token, fpath);
        let mut resp = self
            .client
            .get(&url)
            .timeout(Duration::from_secs(120))
            .send()
            .await
            .map_err(|e| e.to_string())?;
        let status = resp.status().as_u16();
        if status != 200 {
            return Err(format!("HTTP {}", status));
        }
        let mut buf: Vec<u8> = Vec::new();
        while let Some(chunk) = resp.chunk().await.map_err(|e| e.to_string())? {
            buf.extend_from_slice(&chunk);
            if buf.len() > limit {
                return Err("büyük".to_string());
            }
        }
        Ok(buf)
    }

    pub async fn tg_update(&self, upd: &Value) {
        let msg = upd.get("message").cloned().unwrap_or_else(|| json!({}));
        let chat = msg.get("chat").cloned().unwrap_or_else(|| json!({}));
        let text = msg
            .get("text")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        let ctype = chat.get("type").and_then(|v| v.as_str()).unwrap_or("");
        let cid_opt = chat.get("id").and_then(|v| v.as_i64());
        if (ctype == "group" || ctype == "supergroup") && cid_opt.is_some() {
            let gh = self.group_handler.read().await.clone();
            if let Some(handler) = gh {
                handler(msg).await;
            }
            return;
        }
        if ctype != "private" {
            return;
        }
        let Some(cid) = cid_opt else { return };
        let tg_uname = chat
            .get("username")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());
        let first_name = chat
            .get("first_name")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());
        if text.starts_with("/start") {
            let code = text[6..].trim().to_string();
            let uid_opt = if !code.is_empty() {
                let st = Arc::clone(&self.store);
                let tg_name = tg_uname.clone().or(first_name.clone()).unwrap_or_default();
                let u_clone = tg_uname.clone();
                tokio::task::spawn_blocking(move || {
                    st.tg_link(&code, cid, &tg_name, u_clone.as_deref())
                })
                .await
                .unwrap_or(None)
            } else {
                None
            };
            if let Some(uid) = uid_opt {
                let st = Arc::clone(&self.store);
                let u = tokio::task::spawn_blocking(move || st.get_user(uid))
                    .await
                    .unwrap_or(None);
                let dn = u
                    .as_ref()
                    .and_then(|x| x.get("display_name"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                self._tg_send(
                    cid,
                    &format!(
                        "Bağlandı: <b>{}</b>\nİş Takip bildirimleri buraya gelecek. Durdurmak için /stop yazın.",
                        html_escape(dn)
                    ),
                )
                .await;
            } else {
                let st = Arc::clone(&self.store);
                let linked = tokio::task::spawn_blocking(move || st.tg_user_by_chat(cid))
                    .await
                    .unwrap_or(None);
                if let Some(lu) = linked {
                    let st2 = Arc::clone(&self.store);
                    let u_clone = tg_uname.clone();
                    let _ = tokio::task::spawn_blocking(move || {
                        st2.tg_touch(cid, u_clone.as_deref())
                    })
                    .await;
                    let dn = lu
                        .get("display_name")
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    self._tg_send(
                        cid,
                        &format!(
                            "Zaten bağlısınız: <b>{}</b>\nYeniden bağlamanız gerekmez. Durdurmak için /stop yazın.",
                            html_escape(dn)
                        ),
                    )
                    .await;
                } else {
                    self._tg_send(
                        cid,
                        "Bağlantı kodu geçersiz ya da süresi dolmuş.\nİş Takip'te Bildirimler → “Telegram'ı bağla” düğmesiyle yeniden deneyin.",
                    )
                    .await;
                }
            }
        } else if text.starts_with("/stop") {
            let st = Arc::clone(&self.store);
            let _ = tokio::task::spawn_blocking(move || st.tg_unlink(None, Some(cid))).await;
            self._tg_send(
                cid,
                "Bildirimler durduruldu. Yeniden açmak için İş Takip'ten bağlayın.",
            )
            .await;
        } else {
            let st = Arc::clone(&self.store);
            let u_clone = tg_uname.clone();
            let _ = tokio::task::spawn_blocking(move || st.tg_touch(cid, u_clone.as_deref())).await;
            self._tg_send(
                cid,
                "Bu bot İş Takip bildirimlerini gönderir. Grupta iş açmak için <code>/is @kişi açıklama</code> yazın (belgeye yanıt verirseniz belge de eklenir).",
            )
            .await;
        }
    }

    async fn _poll(&self) {
        let mut offset: i64 = 0;
        while !self.stopped.load(Ordering::SeqCst) {
            let res = self
                ._tg(
                    "getUpdates",
                    json!({
                        "offset": offset,
                        "timeout": 50,
                        "allowed_updates": ["message"],
                    }),
                )
                .await;
            match res {
                Ok(ups) => {
                    if let Some(arr) = ups.as_array() {
                        for u in arr {
                            if let Some(uid) = u.get("update_id").and_then(|v| v.as_i64()) {
                                offset = offset.max(uid + 1);
                            }
                            self.tg_update(u).await;
                        }
                    }
                }
                Err(e) => {
                    println!("[!] zs Telegram yoklama: {}", e);
                    tokio::select! {
                        _ = tokio::time::sleep(Duration::from_secs(10)) => {}
                        _ = self.stop_notify.notified() => break,
                    }
                }
            }
        }
    }

    pub async fn test(&self, uid: i64) -> (usize, bool) {
        let st1 = Arc::clone(&self.store);
        let subs = tokio::task::spawn_blocking(move || st1.push_list(&[uid]))
            .await
            .unwrap_or_default();
        let st2 = Arc::clone(&self.store);
        let links = tokio::task::spawn_blocking(move || st2.tg_get(&[uid]))
            .await
            .unwrap_or_default();
        self.deliver(
            uid,
            json!({
                "title": "Deneme bildirimi",
                "body": format!("İş Takip bildirimleri bu cihaza geliyor.\n{}", _fmt(Some(now_epoch()))),
                "task_id": Value::Null,
                "tag": "zs-deneme",
            }),
        )
        .await;
        (subs.len(), !links.is_empty())
    }
}
