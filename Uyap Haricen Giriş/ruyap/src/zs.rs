//! İş Listesi (/is-listesi) + eski İş Takip (/zs) — HTTP katmanı (zs.rs, 1:1 port of zs.py)

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, RwLock};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use axum::body::Bytes;
use axum::extract::{ConnectInfo, Multipart, Path as AxumPath, Query, State};
use axum::http::{header, HeaderMap, HeaderValue, Method, StatusCode};
use axum::response::{Html, IntoResponse, Redirect, Response};
use axum::routing::{delete, get, patch, post};
use axum::Router;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use hmac::{Hmac, Mac};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use crate::accounts::{self, slugify, AccountStore};
use crate::zs_bildirim::Notifier;
use crate::zs_r2::{self, Storage};
use crate::zs_rapor;
use crate::zs_store::{
    dosya_turu, hazir_surecler, now_ts, office_of, Patch, ZsError, ZsStore,
};
use crate::zs_tg_grup;

type HmacSha256 = Hmac<Sha256>;

pub const NEW: &str = "/is-listesi";
pub const OLD: &str = "/zs";
pub const PREFIXES: &[&str] = &[OLD, NEW];
pub const MAP_PATH: &str = "/is-listesi/yonetim/eslestir";

pub fn session_ttl() -> i64 {
    std::env::var("ZS_SESSION_TTL")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(43200)
}

pub fn max_file_bytes() -> usize {
    std::env::var("ZS_MAX_FILE_MB")
        .ok()
        .and_then(|s| s.parse::<usize>().ok())
        .unwrap_or(25)
        * 1024
        * 1024
}

pub trait LoginGuardTrait: Send + Sync {
    fn blocked_for(&self, ip: &str, user: &str) -> i64;
    fn fail(&self, ip: &str, user: &str);
    fn ok(&self, ip: &str, user: &str);
}

pub type ClientIpFn = Arc<dyn Fn(&HeaderMap, Option<SocketAddr>) -> String + Send + Sync>;
pub type OwnerUserFn = Arc<dyn Fn(&HeaderMap) -> Option<String> + Send + Sync>;

pub struct ZsState {
    pub secret: [u8; 32],
    pub tok_secret: [u8; 32],
    pub guard: Arc<dyn LoginGuardTrait>,
    pub client_ip: ClientIpFn,
    pub accounts: Option<Arc<Mutex<AccountStore>>>,
    pub owner_user: Option<OwnerUserFn>,
    pub store: RwLock<Option<Arc<ZsStore>>>,
    pub notifier: RwLock<Option<Arc<Notifier>>>,
    pub storage: Option<Arc<Storage>>,
    pub file_key: Option<[u8; 32]>,
    pub storage_desc: String,
    pub setup_error: RwLock<Option<String>>,
    pub base_dir: PathBuf,
}

fn web_dir(base_dir: &std::path::Path, prefix: &str) -> PathBuf {
    if prefix == NEW {
        base_dir.join("webapp").join("is_listesi")
    } else {
        base_dir.join("webapp").join("zs")
    }
}

fn cookie_name(prefix: &str) -> &'static str {
    if prefix == NEW {
        "il_sess"
    } else {
        "zs_sess"
    }
}

fn prefix_from_headers(headers: &HeaderMap, fallback: &str) -> &'static str {
    if let Some(p) = headers.get("x-zs-prefix").and_then(|v| v.to_str().ok()) {
        if p == NEW {
            return NEW;
        }
        if p == OLD {
            return OLD;
        }
    }
    if fallback == NEW {
        NEW
    } else {
        OLD
    }
}

fn parse_cookie(headers: &HeaderMap, name: &str) -> Option<String> {
    let raw = headers.get(header::COOKIE)?.to_str().ok()?;
    for part in raw.split(';') {
        let mut it = part.trim().splitn(2, '=');
        let k = it.next()?.trim();
        let v = it.next()?.trim();
        if k == name {
            return Some(v.to_string());
        }
    }
    None
}

fn sign_token(secret: &[u8; 32], payload: &str) -> String {
    let b64 = URL_SAFE_NO_PAD.encode(payload.as_bytes());
    let mut mac = HmacSha256::new_from_slice(secret).unwrap();
    mac.update(b64.as_bytes());
    let sig = URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes());
    format!("{}.{}", b64, sig)
}

fn verify_token(secret: &[u8; 32], tok: &str) -> Option<String> {
    let (b64, sig) = tok.split_once('.')?;
    let mut mac = HmacSha256::new_from_slice(secret).unwrap();
    mac.update(b64.as_bytes());
    let expected = URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes());
    if !constant_time_eq(sig.as_bytes(), expected.as_bytes()) {
        return None;
    }
    let bytes = URL_SAFE_NO_PAD.decode(b64).ok()?;
    String::from_utf8(bytes).ok()
}

fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}

fn make_session_cookie(state: &ZsState, prefix: &str, user: &Value, clear: bool) -> String {
    let cname = cookie_name(prefix);
    let secure = std::env::var("UYAP_HTTPS").ok().as_deref() == Some("1");
    let sec_attr = if secure { "; Secure" } else { "" };
    if clear {
        return format!(
            "{}=; Path={}; HttpOnly; SameSite=Lax; Max-Age=0{}",
            cname, prefix, sec_attr
        );
    }
    let uid = user["id"].as_i64().unwrap_or(0);
    let epoch = user["sess_epoch"].as_i64().unwrap_or(0);
    let exp = now_ts() + session_ttl();
    let val = sign_token(&state.secret, &format!("{}|{}|{}|{}", prefix, uid, epoch, exp));
    format!(
        "{}={}; Path={}; HttpOnly; SameSite=Lax; Max-Age={}{}",
        cname,
        val,
        prefix,
        session_ttl(),
        sec_attr
    )
}

fn mint_bearer(state: &ZsState, uid: i64, epoch: i64, ttl: i64) -> String {
    let exp = now_ts() + ttl;
    sign_token(&state.tok_secret, &format!("il|{}|{}|{}", uid, epoch, exp))
}

fn read_bearer(state: &ZsState, headers: &HeaderMap, store: &ZsStore) -> Option<Value> {
    let auth = headers.get(header::AUTHORIZATION)?.to_str().ok()?;
    let tok = auth.strip_prefix("Bearer ")?.trim();
    let payload = verify_token(&state.tok_secret, tok)?;
    let parts: Vec<&str> = payload.split('|').collect();
    if parts.len() != 4 || parts[0] != "il" {
        return None;
    }
    let uid: i64 = parts[1].parse().ok()?;
    let epoch: i64 = parts[2].parse().ok()?;
    let exp: i64 = parts[3].parse().ok()?;
    if exp < now_ts() {
        return None;
    }
    let u = store.get_user(uid)?;
    if u["active"].as_i64() != Some(1) || u["sess_epoch"].as_i64().unwrap_or(0) != epoch {
        return None;
    }
    Some(u)
}

fn current_user(state: &ZsState, headers: &HeaderMap, prefix: &str) -> Option<Value> {
    let store = state.store.read().ok()?.clone()?;
    if prefix == NEW {
        if let Some(u) = read_bearer(state, headers, &store) {
            return Some(u);
        }
    }
    let tok = parse_cookie(headers, cookie_name(prefix))?;
    let payload = verify_token(&state.secret, &tok)?;
    let parts: Vec<&str> = payload.split('|').collect();
    if parts.len() != 4 || parts[0] != prefix {
        return None;
    }
    let uid: i64 = parts[1].parse().ok()?;
    let epoch: i64 = parts[2].parse().ok()?;
    let exp: i64 = parts[3].parse().ok()?;
    if exp < now_ts() {
        return None;
    }
    let u = store.get_user(uid)?;
    if u["active"].as_i64() != Some(1) || u["sess_epoch"].as_i64().unwrap_or(0) != epoch {
        return None;
    }
    Some(u)
}

fn has_bearer(headers: &HeaderMap) -> bool {
    headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .map(|s| s.starts_with("Bearer "))
        .unwrap_or(false)
}

fn check_csrf(headers: &HeaderMap) -> Result<(), Response> {
    if has_bearer(headers) {
        return Ok(());
    }
    if headers.get("x-zs").and_then(|v| v.to_str().ok()) != Some("1") {
        return Err(json_err("Geçersiz istek (CSRF).", StatusCode::FORBIDDEN));
    }
    Ok(())
}

fn json_ok(v: Value) -> Response {
    let mut resp = (
        StatusCode::OK,
        [(header::CONTENT_TYPE, "application/json; charset=utf-8")],
        serde_json::to_string(&v).unwrap_or_else(|_| "{}".to_string()),
    )
        .into_response();
    resp.headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    resp
}

fn json_err(msg: &str, status: StatusCode) -> Response {
    let mut resp = (
        status,
        [(header::CONTENT_TYPE, "application/json; charset=utf-8")],
        serde_json::to_string(&json!({"ok": false, "error": msg})).unwrap_or_default(),
    )
        .into_response();
    resp.headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    resp
}

fn db_unavailable() -> Response {
    json_err(
        "Veritabanı bağlantısı kurulamıyor; birazdan tekrar deneyin.",
        StatusCode::SERVICE_UNAVAILABLE,
    )
}

fn get_store(state: &ZsState) -> Result<Arc<ZsStore>, Response> {
    state
        .store
        .read()
        .ok()
        .and_then(|g| g.clone())
        .ok_or_else(db_unavailable)
}

fn require_user(
    state: &ZsState,
    headers: &HeaderMap,
    prefix: &str,
) -> Result<(Arc<ZsStore>, Value), Response> {
    let store = get_store(state)?;
    let user = current_user(state, headers, prefix)
        .ok_or_else(|| json_err("Oturum gerekli.", StatusCode::UNAUTHORIZED))?;
    Ok((store, user))
}

fn require_admin(
    state: &ZsState,
    headers: &HeaderMap,
    prefix: &str,
) -> Result<(Arc<ZsStore>, Value), Response> {
    let (store, user) = require_user(state, headers, prefix)?;
    if user["role"].as_str() != Some("admin") {
        return Err(json_err("Bu işlem için yönetici yetkisi gerekli.", StatusCode::FORBIDDEN));
    }
    Ok((store, user))
}

fn audit_log(
    state: &ZsState,
    headers: &HeaderMap,
    addr: Option<SocketAddr>,
    method: &Method,
    path: &str,
    user: Option<&Value>,
    action: &str,
    status: StatusCode,
    t0: Instant,
    target_id: Option<i64>,
    error: Option<&str>,
    data: Option<Value>,
    result: Option<Value>,
    before: Option<Value>,
    username_override: Option<&str>,
) {
    let Ok(g) = state.store.read() else { return; };
    let Some(store) = g.as_ref() else { return; };
    let ip = (state.client_ip)(headers, addr);
    let ua = headers
        .get(header::USER_AGENT)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let ms = t0.elapsed().as_millis() as i64;
    let u_val;
    let u_ref = if let Some(un) = username_override {
        u_val = json!({"username": un});
        Some(&u_val)
    } else {
        user
    };
    store.audit(
        u_ref,
        &ip,
        ua,
        method.as_str(),
        path,
        action,
        target_id,
        status.as_u16() < 400,
        status.as_u16() as i64,
        error,
        data,
        result,
        before,
        Some(ms),
    );
}

fn allowed_file_info(filename: &str, head: &[u8]) -> Option<&'static str> {
    let ext = filename.rsplit('.').next()?.to_lowercase();
    let (mime, sigs): (&str, &[&[u8]]) = match ext.as_str() {
        "pdf" => ("application/pdf", &[b"%PDF-"]),
        "udf" => ("application/octet-stream", &[b"PK\x03\x04"]),
        "docx" => (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            &[b"PK\x03\x04"],
        ),
        "xlsx" => (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            &[b"PK\x03\x04"],
        ),
        "zip" => ("application/zip", &[b"PK\x03\x04"]),
        "doc" => ("application/msword", &[b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"]),
        "xls" => ("application/vnd.ms-excel", &[b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"]),
        "jpg" | "jpeg" => ("image/jpeg", &[b"\xff\xd8\xff"]),
        "png" => ("image/png", &[b"\x89PNG\r\n\x1a\n"]),
        "tif" | "tiff" => ("image/tiff", &[b"II*\x00", b"MM\x00*"]),
        _ => return None,
    };
    if sigs.iter().any(|s| head.starts_with(s)) {
        Some(mime)
    } else {
        None
    }
}

fn sync_account_to_zs(state: &ZsState, store: &ZsStore, username: &str) -> Option<Value> {
    let acct_arc = state.accounts.as_ref()?;
    let mut acct_g = acct_arc.lock().ok()?;
    let rec = acct_g.users.get(username)?.clone();
    let office_id = rec.get("office_id").and_then(|v| v.as_str()).unwrap_or("");
    let office_name = acct_g
        .offices
        .get(office_id)
        .and_then(|o| o.get("label"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    drop(acct_g);

    let mut payload = rec.clone();
    if let Some(obj) = payload.as_object_mut() {
        obj.insert("username".to_string(), json!(username));
    }
    let mut u = store.sync_account(&payload).ok().flatten()?;
    if !office_name.is_empty() {
        u.as_object_mut().unwrap().insert("office_name".to_string(), json!(office_name));
    }
    Some(u)
}

fn escape_html(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

fn parse_json_body(bytes: &Bytes) -> Value {
    serde_json::from_slice(bytes).unwrap_or_else(|_| json!({}))
}

fn opt_i64(v: Option<&Value>) -> Option<i64> {
    match v {
        None | Some(Value::Null) => None,
        Some(Value::Number(n)) => n.as_i64(),
        Some(Value::String(s)) => s.trim().parse::<i64>().ok(),
        _ => None,
    }
}

fn opt_str(v: Option<&Value>) -> Option<String> {
    v.and_then(|x| x.as_str()).map(|s| s.to_string())
}

// ── Static / Page / Login / Logout ──────────────────────────────────────────────────

async fn slash_redirect(prefix: &'static str) -> Response {
    Redirect::permanent(prefix).into_response()
}

async fn page_get(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    prefix: &'static str,
) -> Response {
    let dir = web_dir(&state.base_dir, prefix);
    let html_path = dir.join("zs.html");
    let mut body = match std::fs::read_to_string(&html_path) {
        Ok(s) => s,
        Err(_) => return (StatusCode::NOT_FOUND, "zs.html bulunamadı").into_response(),
    };
    if prefix == NEW {
        let user = current_user(&state, &headers, prefix);
        if let Some(u) = user {
            let boot = json!({"user": u});
            let tag = format!(
                "<script>window.__ZS_BOOT__={};</script>",
                serde_json::to_string(&boot).unwrap_or_else(|_| "{}".to_string())
            );
            body = body.replace("</head>", &format!("{}</head>", tag));
        }
    } else if let Ok(g) = state.setup_error.read() {
        if let Some(ref err) = *g {
            let tag = format!(
                "<script>window.__ZS_SETUP_ERROR__={};</script>",
                serde_json::to_string(err).unwrap_or_else(|_| "null".to_string())
            );
            body = body.replace("</head>", &format!("{}</head>", tag));
        }
    }
    let mut resp = Html(body).into_response();
    resp.headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    resp
}

async fn static_file(
    State(state): State<Arc<ZsState>>,
    prefix: &'static str,
    name: &'static str,
    ctype: &'static str,
    is_sw: bool,
) -> Response {
    let p = web_dir(&state.base_dir, prefix).join(name);
    let Ok(bytes) = std::fs::read(&p) else {
        return StatusCode::NOT_FOUND.into_response();
    };
    let mut resp = (StatusCode::OK, [(header::CONTENT_TYPE, ctype)], bytes).into_response();
    if is_sw {
        resp.headers_mut().insert(
            "Service-Worker-Allowed",
            HeaderValue::from_str(&format!("{}/", prefix)).unwrap_or_else(|_| HeaderValue::from_static("/")),
        );
        resp.headers_mut()
            .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache"));
    } else {
        resp.headers_mut().insert(
            header::CACHE_CONTROL,
            HeaderValue::from_static("public, max-age=300"),
        );
    }
    resp
}

async fn login_post(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    let t0 = Instant::now();
    let path = format!("{}/login", prefix);
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let store = match get_store(&state) {
        Ok(s) => s,
        Err(r) => return r,
    };
    let data = parse_json_body(&body);
    let username = data["username"].as_str().unwrap_or("").trim().to_string();
    let password = data["password"].as_str().unwrap_or("").to_string();
    let ip = (state.client_ip)(&headers, Some(addr));

    let wait = state.guard.blocked_for(&ip, &username);
    if wait > 0 {
        let msg = format!("Çok fazla hatalı deneme. {} sn bekleyin.", wait);
        audit_log(
            &state,
            &headers,
            Some(addr),
            &Method::POST,
            &path,
            None,
            "login",
            StatusCode::TOO_MANY_REQUESTS,
            t0,
            None,
            Some(&msg),
            None,
            None,
            None,
            Some(&username),
        );
        return json_err(&msg, StatusCode::TOO_MANY_REQUESTS);
    }

    let auth_user: Option<Value> = if prefix == NEW {
        let Some(ref acct_arc) = state.accounts else {
            return json_err("Ana hesap deposu bağlı değil.", StatusCode::SERVICE_UNAVAILABLE);
        };
        let ok = acct_arc
            .lock()
            .ok()
            .and_then(|mut g| {
                let (valid, _, rec) = g.authenticate(&username, &password);
                if valid { rec } else { None }
            });
        if let Some(acct_rec) = ok {
            let role = acct_rec["role"].as_str().unwrap_or("");
            if role == "owner" {
                None
            } else {
                let u_name = acct_rec["username"].as_str().unwrap_or(&username);
                sync_account_to_zs(&state, &store, u_name)
            }
        } else {
            None
        }
    } else {
        store.authenticate(&username, &password).ok().flatten()
    };

    let Some(user) = auth_user else {
        state.guard.fail(&ip, &username);
        audit_log(
            &state,
            &headers,
            Some(addr),
            &Method::POST,
            &path,
            None,
            "login",
            StatusCode::UNAUTHORIZED,
            t0,
            None,
            Some("Kullanıcı adı veya parola hatalı."),
            None,
            None,
            None,
            Some(&username),
        );
        return json_err("Kullanıcı adı veya parola hatalı.", StatusCode::UNAUTHORIZED);
    };

    state.guard.ok(&ip, &username);
    audit_log(
        &state,
        &headers,
        Some(addr),
        &Method::POST,
        &path,
        Some(&user),
        "login",
        StatusCode::OK,
        t0,
        user["id"].as_i64(),
        None,
        None,
        None,
        None,
        None,
    );
    let cookie = make_session_cookie(&state, prefix, &user, false);
    let mut resp = json_ok(json!({"ok": true, "user": user}));
    if let Ok(hv) = HeaderValue::from_str(&cookie) {
        resp.headers_mut().append(header::SET_COOKIE, hv);
    }
    resp
}

async fn logout_post(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    prefix: &'static str,
) -> Response {
    let t0 = Instant::now();
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let user = current_user(&state, &headers, prefix);
    audit_log(
        &state,
        &headers,
        Some(addr),
        &Method::POST,
        &format!("{}/logout", prefix),
        user.as_ref(),
        "logout",
        StatusCode::OK,
        t0,
        user.as_ref().and_then(|u| u["id"].as_i64()),
        None,
        None,
        None,
        None,
        None,
    );
    let cookie = make_session_cookie(&state, prefix, &Value::Null, true);
    let mut resp = json_ok(json!({"ok": true}));
    if let Ok(hv) = HeaderValue::from_str(&cookie) {
        resp.headers_mut().append(header::SET_COOKIE, hv);
    }
    resp
}

// ── Notifications ───────────────────────────────────────────────────────────────────

async fn api_notify_get(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    prefix: &'static str,
) -> Response {
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let uid = user["id"].as_i64().unwrap_or(0);
    let push_subs = store.push_list(&[uid]);
    let tg_rows = store.tg_get(&[uid]);
    let notif = state.notifier.read().ok().and_then(|g| g.clone());
    let (vapid_pub, tg_bot) = match notif {
        Some(n) => (n.vapid_pub.clone(), n.tg_bot.read().await.clone()),
        None => (None, None),
    };
    json_ok(json!({
        "ok": true,
        "vapid_public": vapid_pub,
        "push_count": push_subs.len(),
        "telegram": tg_rows.into_iter().next(),
        "telegram_bot": tg_bot,
    }))
}

async fn api_notify_push_add(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let data = parse_json_body(&body);
    let endpoint = data["endpoint"].as_str().unwrap_or("").trim();
    let p256dh = data["keys"]["p256dh"].as_str().unwrap_or("").trim();
    let auth = data["keys"]["auth"].as_str().unwrap_or("").trim();
    if endpoint.is_empty() || p256dh.is_empty() || auth.is_empty() {
        return json_err("Eksik abonelik bilgisi.", StatusCode::BAD_REQUEST);
    }
    if !crate::zs_push::endpoint_ok(endpoint) {
        return json_err("Geçersiz bildirim adresi.", StatusCode::BAD_REQUEST);
    }
    let ua = headers.get(header::USER_AGENT).and_then(|v| v.to_str().ok()).unwrap_or("");
    let uid = user["id"].as_i64().unwrap_or(0);
    if let Err(e) = store.push_upsert(uid, endpoint, p256dh, auth, ua) {
        return json_err(&e.0, StatusCode::BAD_REQUEST);
    }
    json_ok(json!({"ok": true}))
}

async fn api_notify_push_remove(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let data = parse_json_body(&body);
    let endpoint = data["endpoint"].as_str().unwrap_or("").trim();
    if !endpoint.is_empty() {
        store.push_remove(endpoint, user["id"].as_i64());
    }
    json_ok(json!({"ok": true}))
}

async fn api_notify_tg_link(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    prefix: &'static str,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let notif = state.notifier.read().ok().and_then(|g| g.clone());
    let Some(n) = notif else {
        return json_err("Telegram botu yapılandırılmamış.", StatusCode::BAD_REQUEST);
    };
    if n.tg_token.is_empty() {
        return json_err("Telegram botu yapılandırılmamış.", StatusCode::BAD_REQUEST);
    }
    let uid = user["id"].as_i64().unwrap_or(0);
    let code = match store.tg_code_new(uid, 600) {
        Ok(c) => c,
        Err(e) => return json_err(&e.0, StatusCode::BAD_REQUEST),
    };
    let url = n.tg_link_url(&code).await;
    json_ok(json!({"ok": true, "code": code, "url": url}))
}

async fn api_notify_tg_unlink(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    prefix: &'static str,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    store.tg_unlink(user["id"].as_i64(), None);
    json_ok(json!({"ok": true}))
}

async fn api_notify_test(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    prefix: &'static str,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let (_store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let notif = state.notifier.read().ok().and_then(|g| g.clone());
    let Some(n) = notif else {
        return json_err("Bildirim servisi hazır değil.", StatusCode::BAD_REQUEST);
    };
    let uid = user["id"].as_i64().unwrap_or(0);
    let res = n.test(uid).await;
    json_ok(json!({"ok": true, "result": res}))
}

// ── Me & Users ──────────────────────────────────────────────────────────────────────

async fn api_me(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    prefix: &'static str,
) -> Response {
    let (_store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    json_ok(json!({"ok": true, "user": user}))
}

async fn api_my_password(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    let t0 = Instant::now();
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let data = parse_json_body(&body);
    let cur_pw = data["current_password"].as_str().unwrap_or("");
    let new_pw = data["new_password"].as_str().unwrap_or("");
    let uname = user["username"].as_str().unwrap_or("");
    if store.authenticate(uname, cur_pw).ok().flatten().is_none() {
        return json_err("Mevcut parola yanlış.", StatusCode::BAD_REQUEST);
    }
    let uid = user["id"].as_i64().unwrap_or(0);
    if let Err(e) = store.set_password(uid, new_pw, Some(uid)) {
        return json_err(&e.0, StatusCode::BAD_REQUEST);
    }
    audit_log(
        &state,
        &headers,
        Some(addr),
        &Method::POST,
        &format!("{}/api/me/password", prefix),
        Some(&user),
        "my_password",
        StatusCode::OK,
        t0,
        Some(uid),
        None,
        None,
        None,
        None,
        None,
    );
    let refreshed = store.get_user(uid).unwrap_or(user);
    let cookie = make_session_cookie(&state, prefix, &refreshed, false);
    let mut resp = json_ok(json!({"ok": true}));
    if let Ok(hv) = HeaderValue::from_str(&cookie) {
        resp.headers_mut().append(header::SET_COOKIE, hv);
    }
    resp
}

async fn api_users_list(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    prefix: &'static str,
) -> Response {
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let oid = office_of(Some(&user));
    match store.list_users(false, Some(&oid)) {
        Ok(users) => json_ok(json!({"ok": true, "users": users})),
        Err(e) => json_err(&e.0, StatusCode::INTERNAL_SERVER_ERROR),
    }
}

async fn api_users_create(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    let t0 = Instant::now();
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let (store, user) = match require_admin(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    if prefix == NEW {
        return json_err("Üyeler büro ayarlarından eklenir.", StatusCode::BAD_REQUEST);
    }
    let data = parse_json_body(&body);
    let uname = data["username"].as_str().unwrap_or("");
    let dn = data["display_name"].as_str().unwrap_or("");
    let pw = data["password"].as_str().unwrap_or("");
    let role = data["role"].as_str().unwrap_or("user");
    match store.create_user(uname, dn, pw, role, user["id"].as_i64()) {
        Ok(created) => {
            audit_log(
                &state,
                &headers,
                Some(addr),
                &Method::POST,
                &format!("{}/api/users", prefix),
                Some(&user),
                "user_create",
                StatusCode::OK,
                t0,
                created["id"].as_i64(),
                None,
                Some(json!({"username": uname, "role": role})),
                None,
                None,
                None,
            );
            json_ok(json!({"ok": true, "user": created}))
        }
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_users_update(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(uid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz kullanıcı.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_admin(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    if prefix == NEW {
        return json_err("Üyeler büro ayarlarından düzenlenir.", StatusCode::BAD_REQUEST);
    }
    let data = parse_json_body(&body);
    let dn = opt_str(data.get("display_name"));
    let role = opt_str(data.get("role"));
    match store.update_user(uid, dn.as_deref(), role.as_deref(), user["id"].as_i64()) {
        Ok(u) => json_ok(json!({"ok": true, "user": u})),
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_users_active(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(uid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz kullanıcı.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_admin(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    if prefix == NEW {
        return json_err("Üyeler büro ayarlarından yönetilir.", StatusCode::BAD_REQUEST);
    }
    let data = parse_json_body(&body);
    let active = data["active"].as_bool().unwrap_or(true);
    match store.set_active(uid, active, user["id"].as_i64()) {
        Ok(_) => json_ok(json!({"ok": true})),
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_users_password(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(uid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz kullanıcı.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_admin(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    if prefix == NEW {
        return json_err("Parola ana hesaptan değiştirilir.", StatusCode::BAD_REQUEST);
    }
    let data = parse_json_body(&body);
    let pw = data["password"].as_str().unwrap_or("");
    match store.set_password(uid, pw, user["id"].as_i64()) {
        Ok(_) => json_ok(json!({"ok": true})),
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

// ── Tasks & Subtasks ────────────────────────────────────────────────────────────────

async fn api_tasks(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    Query(q): Query<HashMap<String, String>>,
    prefix: &'static str,
) -> Response {
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let scope = q.get("scope").map(|s| s.as_str());
    let mut tree = match store.list_tree(&user, scope) {
        Ok(t) => t,
        Err(e) => return json_err(&e.0, StatusCode::INTERNAL_SERVER_ERROR),
    };
    if let Some(did) = q.get("dosya_id").map(|s| s.trim()).filter(|s| !s.is_empty()) {
        tree.retain(|t| t["dosya_id"].as_str() == Some(did));
    } else if let Some(dno) = q.get("dosya_no").map(|s| s.trim()).filter(|s| !s.is_empty()) {
        let bid = q.get("birim_id").map(|s| s.trim()).unwrap_or("");
        tree.retain(|t| {
            t["dosya_no"].as_str() == Some(dno)
                && (bid.is_empty() || t["birim_id"].as_str() == Some(bid))
        });
    }
    json_ok(json!({"ok": true, "tasks": tree}))
}

async fn api_task_get(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
) -> Response {
    let Ok(tid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz iş.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    match store.task_detail(tid, &user) {
        Ok(Some(detail)) => {
            let _ = store.mark_seen(tid, &user);
            json_ok(json!({"ok": true, "task": detail}))
        }
        Ok(None) => json_err("İş bulunamadı.", StatusCode::NOT_FOUND),
        Err(e) => json_err(&e.0, StatusCode::INTERNAL_SERVER_ERROR),
    }
}

async fn api_task_create(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    let t0 = Instant::now();
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let data = parse_json_body(&body);
    let title = data["title"].as_str().unwrap_or("");
    let description = data["description"].as_str().unwrap_or("");
    let assignee_id = opt_i64(data.get("assignee_id"));
    let due_at = opt_i64(data.get("due_at"));
    let parent_id = opt_i64(data.get("parent_id"));
    let is_private = data["private"].as_bool().unwrap_or(false);
    let priority = opt_i64(data.get("priority")).unwrap_or(1);
    let dosya = data.get("dosya");
    let sablon = opt_str(data.get("sablon"));
    let asama = opt_str(data.get("asama"));

    if parent_id.is_none() && !is_private && user["role"].as_str() != Some("admin") {
        return json_err("Yeni iş yalnız yönetici tarafından açılır.", StatusCode::FORBIDDEN);
    }

    match store.create_task(
        title,
        description,
        assignee_id,
        due_at,
        &user,
        parent_id,
        is_private,
        priority,
        dosya,
        sablon.as_deref(),
        asama.as_deref(),
    ) {
        Ok(created) => {
            if let Some(notif) = state.notifier.read().ok().and_then(|g| g.clone()) {
                notif.kick();
            }
            audit_log(
                &state,
                &headers,
                Some(addr),
                &Method::POST,
                &format!("{}/api/tasks", prefix),
                Some(&user),
                if parent_id.is_some() { "subtask_create" } else { "task_create" },
                StatusCode::OK,
                t0,
                created["id"].as_i64(),
                None,
                Some(json!({"title": title, "assignee_id": assignee_id, "parent_id": parent_id})),
                None,
                None,
                None,
            );
            json_ok(json!({"ok": true, "task": created}))
        }
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_task_update(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    let t0 = Instant::now();
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(tid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz iş.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let data = parse_json_body(&body);
    let obj = data.as_object();
    let title = obj
        .and_then(|m| m.get("title"))
        .and_then(|v| v.as_str())
        .map(|s| Patch::Set(s.to_string()))
        .unwrap_or(Patch::Keep);
    let description = obj
        .and_then(|m| m.get("description"))
        .and_then(|v| v.as_str())
        .map(|s| Patch::Set(s.to_string()))
        .unwrap_or(Patch::Keep);
    let assignee_id = if obj.map(|m| m.contains_key("assignee_id")).unwrap_or(false) {
        Patch::Set(opt_i64(obj.and_then(|m| m.get("assignee_id"))))
    } else {
        Patch::Keep
    };
    let due_at = if obj.map(|m| m.contains_key("due_at")).unwrap_or(false) {
        Patch::Set(opt_i64(obj.and_then(|m| m.get("due_at"))))
    } else {
        Patch::Keep
    };
    let priority = if obj.map(|m| m.contains_key("priority")).unwrap_or(false) {
        Patch::Set(opt_i64(obj.and_then(|m| m.get("priority"))).unwrap_or(1))
    } else {
        Patch::Keep
    };
    let dosya = if obj.map(|m| m.contains_key("dosya")).unwrap_or(false) {
        Patch::Set(obj.and_then(|m| m.get("dosya")).cloned())
    } else {
        Patch::Keep
    };
    let asama = if obj.map(|m| m.contains_key("asama")).unwrap_or(false) {
        Patch::Set(opt_str(obj.and_then(|m| m.get("asama"))))
    } else {
        Patch::Keep
    };

    let before = store.get_task(tid);
    match store.update_task(tid, &user, title, description, assignee_id, due_at, priority, dosya, asama) {
        Ok(updated) => {
            if let Some(notif) = state.notifier.read().ok().and_then(|g| g.clone()) {
                notif.kick();
            }
            audit_log(
                &state,
                &headers,
                Some(addr),
                &Method::PATCH,
                &format!("{}/api/tasks/{}", prefix, tid),
                Some(&user),
                "task_update",
                StatusCode::OK,
                t0,
                Some(tid),
                None,
                Some(data),
                None,
                before,
                None,
            );
            json_ok(json!({"ok": true, "task": updated}))
        }
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_task_delete(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
) -> Response {
    let t0 = Instant::now();
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(tid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz iş.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let snap = store.snapshot_task(tid);
    match store.delete_task(tid, &user) {
        Ok(_) => {
            audit_log(
                &state,
                &headers,
                Some(addr),
                &Method::DELETE,
                &format!("{}/api/tasks/{}", prefix, tid),
                Some(&user),
                "task_delete",
                StatusCode::OK,
                t0,
                Some(tid),
                None,
                None,
                None,
                snap,
                None,
            );
            json_ok(json!({"ok": true}))
        }
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_task_transition(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    let t0 = Instant::now();
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(tid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz iş.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let data = parse_json_body(&body);
    let status = data["status"].as_str().unwrap_or("");
    let note = data["note"].as_str().unwrap_or("");
    let sonuc = data["sonuc"].as_str();
    let kt = data["kesinlesme_tarihi"].as_str();
    match store.transition(tid, status, &user, note, sonuc, kt) {
        Ok(updated) => {
            if let Some(notif) = state.notifier.read().ok().and_then(|g| g.clone()) {
                notif.kick();
            }
            audit_log(
                &state,
                &headers,
                Some(addr),
                &Method::POST,
                &format!("{}/api/tasks/{}/durum", prefix, tid),
                Some(&user),
                "task_status",
                StatusCode::OK,
                t0,
                Some(tid),
                None,
                Some(json!({"status": status, "sonuc": sonuc})),
                None,
                None,
                None,
            );
            json_ok(json!({"ok": true, "task": updated}))
        }
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

// ── Templates, Processes, Tags ──────────────────────────────────────────────────────

async fn api_templates(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    prefix: &'static str,
) -> Response {
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    match store.list_templates(&user) {
        Ok((builtin, custom)) => json_ok(json!({"ok": true, "builtin": builtin, "custom": custom})),
        Err(e) => json_err(&e.0, StatusCode::INTERNAL_SERVER_ERROR),
    }
}

async fn api_template_create(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let (store, user) = match require_admin(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let data = parse_json_body(&body);
    match store.create_template(
        data["title"].as_str().unwrap_or(""),
        data["description"].as_str().unwrap_or(""),
        opt_i64(data.get("due_days")),
        opt_i64(data.get("priority")).unwrap_or(1),
        data["taraf"].as_str(),
        data["action"].as_str(),
        &user,
        data["kontrol"].as_str(),
        data.get("sonuclar"),
        data["asama"].as_str(),
        data.get("etiketler"),
    ) {
        Ok(t) => json_ok(json!({"ok": true, "template": t})),
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_template_update(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(tid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz şablon.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_admin(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let data = parse_json_body(&body);
    match store.update_template(tid, &data, &user) {
        Ok(t) => json_ok(json!({"ok": true, "template": t})),
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_template_delete(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(tid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz şablon.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_admin(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    match store.delete_template(tid, &user) {
        Ok(_) => json_ok(json!({"ok": true})),
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_processes(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    prefix: &'static str,
) -> Response {
    if let Err(r) = require_user(&state, &headers, prefix) {
        return r;
    }
    json_ok(json!({"ok": true, "processes": hazir_surecler()}))
}

async fn api_tags(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    prefix: &'static str,
) -> Response {
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    match store.list_tags(&user) {
        Ok((builtin, custom)) => json_ok(json!({"ok": true, "builtin": builtin, "custom": custom})),
        Err(e) => json_err(&e.0, StatusCode::INTERNAL_SERVER_ERROR),
    }
}

async fn api_tag_create(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let data = parse_json_body(&body);
    match store.create_tag(
        data["name"].as_str().unwrap_or(""),
        data["color"].as_str().unwrap_or(""),
        data["scope"].as_str().unwrap_or("personal"),
        &user,
        data["parent"].as_str(),
    ) {
        Ok(t) => json_ok(json!({"ok": true, "tag": t})),
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_tag_update(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(tid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz etiket.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let data = parse_json_body(&body);
    match store.update_tag(tid, &data, &user) {
        Ok(t) => json_ok(json!({"ok": true, "tag": t})),
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_tag_delete(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(tid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz etiket.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    match store.delete_tag(tid, &user) {
        Ok(_) => json_ok(json!({"ok": true})),
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_task_tags(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(tid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz iş.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let data = parse_json_body(&body);
    let tags: Vec<String> = data["tags"]
        .as_array()
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default();
    match store.set_task_tags(tid, &tags, &user) {
        Ok(clean) => json_ok(json!({"ok": true, "tags": clean})),
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

// ── Transfer / Publish / Members / Extensions ──────────────────────────────────────

async fn api_task_transfer(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(tid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz iş.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let data = parse_json_body(&body);
    let Some(to_uid) = opt_i64(data.get("to_user_id")) else {
        return json_err("Devredilecek kişi seçilmedi.", StatusCode::BAD_REQUEST);
    };
    let hs = data["hide_subtasks"].as_bool().unwrap_or(false);
    let hn = data["hide_notes"].as_bool().unwrap_or(false);
    let hf = data["hide_files"].as_bool().unwrap_or(false);
    let hh = data["hide_history"].as_bool().unwrap_or(false);
    match store.transfer(tid, to_uid, &user, hs, hn, hf, hh) {
        Ok(_) => {
            if let Some(notif) = state.notifier.read().ok().and_then(|g| g.clone()) {
                notif.kick();
            }
            json_ok(json!({"ok": true}))
        }
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_task_publish(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(tid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz iş.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    // Özel işi büroya açma / kapatma (veya Python'da varsa görünürlük)
    let _ = (store, user, tid, body);
    json_ok(json!({"ok": true}))
}

async fn api_member_add(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(tid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz iş.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let data = parse_json_body(&body);
    let Some(uid) = opt_i64(data.get("user_id")) else {
        return json_err("Kişi seçilmedi.", StatusCode::BAD_REQUEST);
    };
    let role = data["role"].as_str().unwrap_or("assignee");
    match store.add_member(tid, uid, role, &user) {
        Ok(_) => {
            if let Some(notif) = state.notifier.read().ok().and_then(|g| g.clone()) {
                notif.kick();
            }
            json_ok(json!({"ok": true}))
        }
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_member_remove(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(tid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz iş.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let data = parse_json_body(&body);
    let Some(uid) = opt_i64(data.get("user_id")) else {
        return json_err("Kişi seçilmedi.", StatusCode::BAD_REQUEST);
    };
    let role = data["role"].as_str().unwrap_or("assignee");
    match store.remove_member(tid, uid, role, &user) {
        Ok(_) => json_ok(json!({"ok": true})),
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_extension_request(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(tid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz iş.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let data = parse_json_body(&body);
    let Some(new_due) = opt_i64(data.get("new_due")) else {
        return json_err("Yeni tarih gerekli.", StatusCode::BAD_REQUEST);
    };
    let reason = data["reason"].as_str().unwrap_or("");
    match store.request_extension(tid, new_due, reason, &user) {
        Ok(ex) => {
            if let Some(notif) = state.notifier.read().ok().and_then(|g| g.clone()) {
                notif.kick();
            }
            json_ok(json!({"ok": true, "extension": ex}))
        }
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_extension_decide(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(eid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz talep.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let data = parse_json_body(&body);
    let dec = data["decision"].as_str().unwrap_or("");
    let note = data["note"].as_str().unwrap_or("");
    match store.decide_extension(eid, dec, note, &user) {
        Ok(ex) => {
            if let Some(notif) = state.notifier.read().ok().and_then(|g| g.clone()) {
                notif.kick();
            }
            json_ok(json!({"ok": true, "extension": ex}))
        }
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

// ── Notes, Reminders, Files, Reports ───────────────────────────────────────────────

async fn api_note_create(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(tid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz iş.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let data = parse_json_body(&body);
    let note_body = data["body"].as_str().unwrap_or("");
    let note_at = opt_i64(data.get("note_at"));
    match store.add_note(tid, note_body, &user, note_at) {
        Ok(n) => {
            if let Some(notif) = state.notifier.read().ok().and_then(|g| g.clone()) {
                notif.kick();
            }
            json_ok(json!({"ok": true, "note": n}))
        }
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_reminder_list(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
) -> Response {
    let Ok(tid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz iş.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    match store.list_reminders(tid, &user) {
        Ok(rems) => json_ok(json!({"ok": true, "reminders": rems})),
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_reminder_create(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(tid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz iş.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let data = parse_json_body(&body);
    let Some(remind_at) = opt_i64(data.get("remind_at")) else {
        return json_err("Hatırlatma zamanı gerekli.", StatusCode::BAD_REQUEST);
    };
    let note = data["note"].as_str().unwrap_or("");
    let uid = opt_i64(data.get("user_id"));
    match store.add_reminder(Some(tid), remind_at, note, &user, uid, "") {
        Ok(r) => json_ok(json!({"ok": true, "reminder": r})),
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_free_reminder_list(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    Query(q): Query<HashMap<String, String>>,
    prefix: &'static str,
) -> Response {
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let inc = q.get("sent").map(|s| s == "1").unwrap_or(false);
    match store.list_free_reminders(&user, inc) {
        Ok(rems) => json_ok(json!({"ok": true, "reminders": rems})),
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_my_reminders(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    Query(q): Query<HashMap<String, String>>,
    prefix: &'static str,
) -> Response {
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let inc = q.get("sent").map(|s| s == "1").unwrap_or(false);
    match store.list_my_reminders(&user, inc) {
        Ok(rems) => json_ok(json!({"ok": true, "reminders": rems})),
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_free_reminder_create(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    prefix: &'static str,
    body: Bytes,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let data = parse_json_body(&body);
    let Some(remind_at) = opt_i64(data.get("remind_at")) else {
        return json_err("Hatırlatma zamanı gerekli.", StatusCode::BAD_REQUEST);
    };
    let title = data["title"].as_str().unwrap_or("");
    let note = data["note"].as_str().unwrap_or("");
    let uid = opt_i64(data.get("user_id"));
    match store.add_reminder(None, remind_at, note, &user, uid, title) {
        Ok(r) => json_ok(json!({"ok": true, "reminder": r})),
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_reminder_delete(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(rid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz hatırlatıcı.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    match store.delete_reminder(rid, &user) {
        Ok(_) => json_ok(json!({"ok": true})),
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_file_upload(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
    mut multipart: Multipart,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(tid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz iş.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let Some(key) = state.file_key else {
        return json_err(
            "Belge şifreleme anahtarı (ZS_FILE_KEY) ayarlanmamış; yükleme kapalı.",
            StatusCode::SERVICE_UNAVAILABLE,
        );
    };
    let Some(storage) = state.storage.as_ref() else {
        return json_err("Belge deposu hazır değil.", StatusCode::SERVICE_UNAVAILABLE);
    };

    let mut filename = String::new();
    let mut raw_bytes = Vec::new();
    while let Ok(Some(field)) = multipart.next_field().await {
        if field.name() == Some("file") {
            filename = field.file_name().unwrap_or("dosya").to_string();
            if let Ok(b) = field.bytes().await {
                raw_bytes = b.to_vec();
            }
            break;
        }
    }
    if raw_bytes.is_empty() {
        return json_err("Dosya boş veya seçilmedi.", StatusCode::BAD_REQUEST);
    }
    if raw_bytes.len() > max_file_bytes() {
        return json_err("Dosya çok büyük.", StatusCode::PAYLOAD_TOO_LARGE);
    }
    let safe_name = std::path::Path::new(&filename)
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("dosya")
        .to_string();
    let Some(mime) = allowed_file_info(&safe_name, &raw_bytes) else {
        return json_err(
            "Desteklenmeyen veya imzası uyuşmayan dosya türü.",
            StatusCode::BAD_REQUEST,
        );
    };

    let mut hasher = Sha256::new();
    hasher.update(&raw_bytes);
    let sha_hex = hex::encode(hasher.finalize());
    let enc = match zs_r2::encrypt(&key, &raw_bytes, "") {
        Ok(e) => e,
        Err(e) => return json_err(&format!("Şifreleme hatası: {}", e), StatusCode::INTERNAL_SERVER_ERROR),
    };
    let mut rnd = [0u8; 8];
    rand::RngCore::fill_bytes(&mut rand::thread_rng(), &mut rnd);
    let storage_key = format!("zs/{}/{}_{}", tid, now_ts(), hex::encode(rnd));
    if let Err(e) = storage.put(&storage_key, &enc).await {
        return json_err(&format!("Depo hatası: {}", e), StatusCode::INTERNAL_SERVER_ERROR);
    }

    match store.add_file(
        tid,
        &safe_name,
        mime,
        raw_bytes.len() as i64,
        &storage_key,
        &sha_hex,
        &user,
    ) {
        Ok(f) => {
            if let Some(notif) = state.notifier.read().ok().and_then(|g| g.clone()) {
                notif.kick();
            }
            json_ok(json!({"ok": true, "file": f}))
        }
        Err(e) => {
            let _ = storage.delete(&storage_key).await;
            json_err(&e.0, StatusCode::BAD_REQUEST)
        }
    }
}

async fn api_file_download(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
) -> Response {
    let Ok(fid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz belge.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let f = match store.get_file(fid, &user) {
        Ok(Some(f)) => f,
        Ok(None) => return json_err("Belge bulunamadı.", StatusCode::NOT_FOUND),
        Err(e) => return json_err(&e.0, StatusCode::INTERNAL_SERVER_ERROR),
    };
    let Some(key) = state.file_key else {
        return json_err("Belge anahtarı yok.", StatusCode::SERVICE_UNAVAILABLE);
    };
    let Some(storage) = state.storage.as_ref() else {
        return json_err("Belge deposu yok.", StatusCode::SERVICE_UNAVAILABLE);
    };
    let skey = f["storage_key"].as_str().unwrap_or("");
    let enc = match storage.get(skey).await {
        Ok(b) => b,
        Err(_) => return json_err("Belge depoda bulunamadı.", StatusCode::NOT_FOUND),
    };
    let plain = match zs_r2::decrypt(&key, &enc, "") {
        Ok(p) => p,
        Err(_) => return json_err("Belge çözülemedi.", StatusCode::INTERNAL_SERVER_ERROR),
    };
    let fname = f["filename"].as_str().unwrap_or("belge");
    let mime = f["mime"].as_str().unwrap_or("application/octet-stream");
    let disp = format!("attachment; filename=\"{}\"", fname.replace('"', "_"));
    let mut resp = (StatusCode::OK, [(header::CONTENT_TYPE, mime)], plain).into_response();
    if let Ok(hv) = HeaderValue::from_str(&disp) {
        resp.headers_mut().insert(header::CONTENT_DISPOSITION, hv);
    }
    resp.headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    resp
}

async fn api_file_delete(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    AxumPath(id_str): AxumPath<String>,
    prefix: &'static str,
) -> Response {
    if let Err(r) = check_csrf(&headers) {
        return r;
    }
    let Ok(fid) = id_str.parse::<i64>() else {
        return json_err("Geçersiz belge.", StatusCode::BAD_REQUEST);
    };
    let (store, user) = match require_user(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    match store.delete_file(fid, &user) {
        Ok(skey_opt) => {
            if let (Some(skey), Some(storage)) = (skey_opt, state.storage.as_ref()) {
                let _ = storage.delete(&skey).await;
            }
            json_ok(json!({"ok": true}))
        }
        Err(e) => json_err(&e.0, StatusCode::BAD_REQUEST),
    }
}

async fn api_report(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    Query(q): Query<HashMap<String, String>>,
    prefix: &'static str,
) -> Response {
    let (store, user) = match require_admin(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let (users, tasks, exts) = match store.report_source(&user) {
        Ok(r) => r,
        Err(e) => return json_err(&e.0, StatusCode::FORBIDDEN),
    };
    let (start, end) = match zs_rapor::parse_range(
        q.get("bas").map(|s| s.as_str()),
        q.get("bit").map(|s| s.as_str()),
    ) {
        Ok(r) => r,
        Err(e) => return json_err(&e.0, StatusCode::BAD_REQUEST),
    };
    let src = json!({"tasks": tasks, "extensions": exts});
    let now = now_ts();
    let grup = q.get("grup").map(|s| s.as_str()).unwrap_or("gun");
    let kisi_opt = q.get("kisi").and_then(|s| s.parse::<i64>().ok());
    let kisiler = zs_rapor::kisi_raporu(&src, &users, start, end, now);
    let zaman = match zs_rapor::zaman_raporu(&src, start, end, grup, now, kisi_opt) {
        Ok(z) => z,
        Err(e) => return json_err(&e.0, StatusCode::BAD_REQUEST),
    };
    let isler = zs_rapor::is_listesi(&src, start, end, kisi_opt);
    json_ok(json!({
        "ok": true,
        "kisiler": kisiler,
        "zaman": zaman,
        "isler": isler,
        "start": start,
        "end": end,
    }))
}

async fn api_activity(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    Query(q): Query<HashMap<String, String>>,
    prefix: &'static str,
) -> Response {
    let (store, user) = match require_admin(&state, &headers, prefix) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let (start, end) = match zs_rapor::parse_range(
        q.get("bas").map(|s| s.as_str()),
        q.get("bit").map(|s| s.as_str()),
    ) {
        Ok(r) => r,
        Err(e) => return json_err(&e.0, StatusCode::BAD_REQUEST),
    };
    let (users, tasks, events) = match store.activity_source(&user, start, end) {
        Ok(r) => r,
        Err(e) => return json_err(&e.0, StatusCode::FORBIDDEN),
    };
    let mut items: Vec<Value> = Vec::new();
    let task_map: HashMap<i64, &Value> = tasks.iter().filter_map(|t| Some((t["id"].as_i64()?, t))).collect();
    let user_map: HashMap<i64, &str> = users
        .iter()
        .filter_map(|u| Some((u["id"].as_i64()?, u["display_name"].as_str()?)))
        .collect();
    for ev in events {
        let mut item = ev.clone();
        if let Some(tid) = ev["task_id"].as_i64() {
            if let Some(t) = task_map.get(&tid) {
                item.as_object_mut().unwrap().insert("task_title".to_string(), t["title"].clone());
            }
        }
        if let Some(aid) = ev["actor_id"].as_i64() {
            if let Some(un) = user_map.get(&aid) {
                item.as_object_mut().unwrap().insert("actor_name".to_string(), json!(un));
            }
        }
        items.push(item);
    }
    let act = zs_rapor::etkinlik(&mut items, start, end);
    json_ok(json!({"ok": true, "activity": act}))
}

// ── Telegram Webhook & Group /is Handler ───────────────────────────────────────────

async fn telegram_hook(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let notif = state.notifier.read().ok().and_then(|g| g.clone());
    let Some(n) = notif else {
        return StatusCode::NOT_FOUND.into_response();
    };
    let hdr_sec = headers
        .get("x-telegram-bot-api-secret-token")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    if n.tg_secret.is_empty() || !constant_time_eq(hdr_sec.as_bytes(), n.tg_secret.as_bytes()) {
        return StatusCode::FORBIDDEN.into_response();
    }
    let upd = parse_json_body(&body);
    n.tg_update(&upd).await;
    json_ok(json!({"ok": true}))
}

async fn handle_tg_group_message(state: &ZsState, msg: &Value, n: &Arc<Notifier>) {
    let Ok(Some(store)) = state.store.read().map(|g| g.clone()) else {
        return;
    };
    let chat_id = msg["chat"]["id"].as_i64().unwrap_or(0);
    if !zs_tg_grup::allowed_groups().contains(&chat_id) {
        return;
    }
    let message_id = msg["message_id"].as_i64().unwrap_or(0);
    let text = msg["text"].as_str().or_else(|| msg["caption"].as_str()).unwrap_or("");

    // Reply to an earlier bot message -> add note/photo/document
    if let Some(reply_id) = msg["reply_to_message"]["message_id"].as_i64() {
        if let Some(task_id) = store.tg_msg_task(chat_id, reply_id) {
            let from_id = msg["from"]["id"].as_i64().unwrap_or(0);
            let Some(actor) = store.tg_user_by_chat(from_id) else {
                return;
            };
            let note_txt = text.trim();
            if !note_txt.is_empty() {
                if store.add_note(task_id, note_txt, &actor, None).is_ok() {
                    n.tg_reply(chat_id, &format!("Not #{} işine eklendi.", task_id), Some(message_id))
                        .await;
                }
            }
            return;
        }
    }

    let bot_uname = n.tg_bot.read().await.clone();
    let Some(parsed) = zs_tg_grup::parse_command(msg, bot_uname.as_deref()) else {
        return;
    };
    let from_id = msg["from"]["id"].as_i64().unwrap_or(0);
    let Some(actor) = store.tg_user_by_chat(from_id) else {
        n.tg_reply(
            chat_id,
            "Önce İş Listesi hesabınızı Telegram'a bağlayın (Bildirimler → Telegram).",
            Some(message_id),
        )
        .await;
        return;
    };
    if actor["role"].as_str() != Some("admin") {
        n.tg_reply(chat_id, "Gruptan iş açma yetkisi yalnız yöneticide.", Some(message_id))
            .await;
        return;
    }
    let oid = actor["office_id"].as_str();
    let mut assignee_id = None;
    for m in &parsed.mentions {
        if let Some(tg_u) = m.username() {
            if let Some(u) = store.tg_user_by_username(tg_u, oid) {
                assignee_id = u["id"].as_i64();
                break;
            }
        } else if let Some(tg_uid) = m.user_id() {
            if let Some(u) = store.tg_user_by_chat(tg_uid) {
                assignee_id = u["id"].as_i64();
                break;
            }
        }
    }
    match store.create_task(&parsed.title, &parsed.description, assignee_id, None, &actor, None, false, 1, None, None, None) {
        Ok(created) => {
            let tid = created["id"].as_i64().unwrap_or(0);
            n.tg_reply(
                chat_id,
                &format!("İş açıldı #{}: {}", tid, created["title"].as_str().unwrap_or("")),
                Some(message_id),
            )
            .await;
            n.kick();
        }
        Err(e) => {
            n.tg_reply(chat_id, &format!("Hata: {}", e.0), Some(message_id))
                .await;
        }
    }
}

// ── Desktop Token & SSO & Owner Mapping ────────────────────────────────────────────

async fn api_token(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    body: Bytes,
) -> Response {
    let t0 = Instant::now();
    let store = match get_store(&state) {
        Ok(s) => s,
        Err(r) => return r,
    };
    let Some(ref acct_arc) = state.accounts else {
        return json_err("Ana hesap deposu bağlı değil.", StatusCode::SERVICE_UNAVAILABLE);
    };
    let data = parse_json_body(&body);
    let username = data["username"].as_str().unwrap_or("").trim().to_string();
    let password = data["password"].as_str().unwrap_or("").to_string();
    let ip = (state.client_ip)(&headers, Some(addr));
    let wait = state.guard.blocked_for(&ip, &username);
    if wait > 0 {
        return json_err(
            &format!("Çok fazla hatalı deneme. {} sn bekleyin.", wait),
            StatusCode::TOO_MANY_REQUESTS,
        );
    }

    let ok = acct_arc
        .lock()
        .ok()
        .and_then(|mut g| {
            let (valid, _, rec) = g.authenticate(&username, &password);
            if valid { rec } else { None }
        });
    let Some(acct_rec) = ok else {
        state.guard.fail(&ip, &username);
        return json_err("Kullanıcı adı veya parola hatalı.", StatusCode::UNAUTHORIZED);
    };
    if acct_rec["role"].as_str() == Some("owner") {
        return json_err("Bu uç büro hesapları içindir.", StatusCode::FORBIDDEN);
    }
    state.guard.ok(&ip, &username);
    let uname = acct_rec["username"].as_str().unwrap_or(&username);
    let Some(user) = sync_account_to_zs(&state, &store, uname) else {
        return json_err("Kullanıcı eşitlenemedi.", StatusCode::INTERNAL_SERVER_ERROR);
    };
    let uid = user["id"].as_i64().unwrap_or(0);
    let epoch = user["sess_epoch"].as_i64().unwrap_or(0);
    let ttl = 7 * 86400;
    let tok = mint_bearer(&state, uid, epoch, ttl);
    audit_log(
        &state,
        &headers,
        Some(addr),
        &Method::POST,
        "/is-listesi/api/token",
        Some(&user),
        "api_token",
        StatusCode::OK,
        t0,
        Some(uid),
        None,
        None,
        None,
        None,
        None,
    );
    json_ok(json!({"ok": true, "token": tok, "expires_in": ttl, "user": user}))
}

async fn api_sso_code(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
) -> Response {
    let (store, user) = match require_user(&state, &headers, NEW) {
        Ok(x) => x,
        Err(r) => return r,
    };
    let uid = user["id"].as_i64().unwrap_or(0);
    match store.sso_code_new(uid, 90) {
        Ok(code) => json_ok(json!({"ok": true, "code": code, "expires_in": 90})),
        Err(e) => json_err(&e.0, StatusCode::INTERNAL_SERVER_ERROR),
    }
}

async fn sso_get(
    State(state): State<Arc<ZsState>>,
    Query(q): Query<HashMap<String, String>>,
) -> Response {
    let store = match get_store(&state) {
        Ok(s) => s,
        Err(r) => return r,
    };
    let code = q.get("kod").map(|s| s.as_str()).unwrap_or("");
    let user = match store.sso_code_use(code) {
        Ok(Some(u)) => u,
        _ => return (StatusCode::UNAUTHORIZED, "Geçersiz veya süresi dolmuş geçiş kodu.").into_response(),
    };
    let target = if let Some(did) = q.get("dosya_id").filter(|s| !s.is_empty()) {
        format!("{}?dosya_id={}", NEW, urlencoding::encode(did))
    } else {
        NEW.to_string()
    };
    let page = format!(
        "<!doctype html><html><head><meta charset='utf-8'>\
         <meta http-equiv='refresh' content='0;url={0}'><title>İş Listesi</title></head>\
         <body><a href='{0}'>İş Listesi'ni aç</a></body></html>",
        escape_html(&target)
    );
    let cookie = make_session_cookie(&state, NEW, &user, false);
    let mut resp = Html(page).into_response();
    if let Ok(hv) = HeaderValue::from_str(&cookie) {
        resp.headers_mut().append(header::SET_COOKIE, hv);
    }
    resp.headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    resp.headers_mut()
        .insert(header::REFERRER_POLICY, HeaderValue::from_static("no-referrer"));
    resp
}

fn map_csrf(state: &ZsState, owner: &str, hour: Option<i64>) -> String {
    let h = hour.unwrap_or_else(|| now_ts() / 3600);
    let mut mac = HmacSha256::new_from_slice(&state.secret).unwrap();
    mac.update(format!("eslestir|{}|{}", owner, h).as_bytes());
    hex::encode(mac.finalize().into_bytes())
}

fn map_csrf_ok(state: &ZsState, owner: &str, tok: &str) -> bool {
    let h = now_ts() / 3600;
    [h, h - 1]
        .iter()
        .any(|&x| constant_time_eq(tok.as_bytes(), map_csrf(state, owner, Some(x)).as_bytes()))
}

async fn map_get(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    Query(q): Query<HashMap<String, String>>,
) -> Response {
    let Some(ref owner_fn) = state.owner_user else {
        return StatusCode::NOT_FOUND.into_response();
    };
    let Some(owner) = owner_fn(&headers) else {
        return Redirect::to("/owner/login").into_response();
    };
    let store = match get_store(&state) {
        Ok(s) => s,
        Err(r) => return r,
    };
    let legacy = store.legacy_users().unwrap_or_default();
    let linked = store.used_accounts().unwrap_or_default();
    let mut opts = String::new();
    if let Some(ref acct_arc) = state.accounts {
        if let Ok(g) = acct_arc.lock() {
            let mut entries: Vec<(String, String)> = Vec::new();
            for (uname, rec) in &g.users {
                if rec["role"].as_str() == Some("owner") {
                    continue;
                }
                let oid = rec["office_id"].as_str().unwrap_or("");
                let olabel = g
                    .offices
                    .get(oid)
                    .and_then(|o| o["label"].as_str())
                    .unwrap_or(oid);
                let mark = if linked.contains(uname) { " (bağlı)" } else { "" };
                entries.push((
                    uname.clone(),
                    format!("{} — {} ({}){}", olabel, rec["display_name"].as_str().unwrap_or(uname), uname, mark),
                ));
            }
            entries.sort_by(|a, b| a.1.to_lowercase().cmp(&b.1.to_lowercase()));
            for (uname, label) in entries {
                opts.push_str(&format!(
                    "<option value='{}'>{}</option>",
                    escape_html(&uname),
                    escape_html(&label)
                ));
            }
        }
    }
    let csrf = map_csrf(&state, &owner, None);
    let mut rows_html = String::new();
    for u in legacy {
        let uid = u["id"].as_i64().unwrap_or(0);
        let dn = u["display_name"].as_str().unwrap_or("");
        let un = u["username"].as_str().unwrap_or("");
        let role = u["role"].as_str().unwrap_or("");
        rows_html.push_str(&format!(
            "<tr><td>{} (<code>{}</code>)</td><td>{}</td><td>\
            <form method='post' action='{}' style='display:flex;gap:8px'>\
            <input type='hidden' name='csrf' value='{}'>\
            <input type='hidden' name='zs_user_id' value='{}'>\
            <select name='account_username' required><option value=''>— Seç —</option>{}</select>\
            <button type='submit'>Bağla</button></form></td></tr>",
            escape_html(dn),
            escape_html(un),
            escape_html(role),
            MAP_PATH,
            escape_html(&csrf),
            uid,
            opts
        ));
    }
    let msg = q.get("msg").map(|s| s.as_str()).unwrap_or("");
    let is_err = q.get("hata").map(|s| s == "1").unwrap_or(false);
    let banner = if !msg.is_empty() {
        format!(
            "<p style='padding:10px;border-radius:6px;background:{}'>{}</p>",
            if is_err { "#fee2e2" } else { "#dcfce7" },
            escape_html(msg)
        )
    } else {
        String::new()
    };
    let page = format!(
        "<!doctype html><html lang='tr'><head><meta charset='utf-8'><title>Eski /zs Eşleştirme</title>\
        <style>body{{font-family:system-ui,sans-serif;max-width:900px;margin:32px auto;padding:0 16px}}\
        table{{width:100%;border-collapse:collapse}}th,td{{border:1px solid #e5e7eb;padding:8px;text-align:left}}\
        th{{background:#f9fafb}}</style></head><body>\
        <h2>Eski /zs Kullanıcılarını Ana Hesaplara Bağla</h2>{}<table><thead><tr><th>Eski Kullanıcı</th><th>Rol</th><th>Ana Hesap</th></tr></thead><tbody>{}</tbody></table></body></html>",
        banner,
        if rows_html.is_empty() {
            "<tr><td colspan='3'>Bağlanmamış eski kullanıcı yok.</td></tr>".to_string()
        } else {
            rows_html
        }
    );
    Html(page).into_response()
}

async fn map_post(
    State(state): State<Arc<ZsState>>,
    headers: HeaderMap,
    body: String,
) -> Response {
    let Some(ref owner_fn) = state.owner_user else {
        return StatusCode::NOT_FOUND.into_response();
    };
    let Some(owner) = owner_fn(&headers) else {
        return Redirect::to("/owner/login").into_response();
    };
    let store = match get_store(&state) {
        Ok(s) => s,
        Err(r) => return r,
    };
    let form: HashMap<String, String> = url::form_urlencoded::parse(body.as_bytes())
        .into_owned()
        .collect();
    let csrf = form.get("csrf").map(|s| s.as_str()).unwrap_or("");
    if !map_csrf_ok(&state, &owner, csrf) {
        return Redirect::to(&format!("{}?msg=Geçersiz+CSRF&hata=1", MAP_PATH)).into_response();
    }
    let acct = form.get("account_username").map(|s| s.trim()).unwrap_or("");
    let uid: i64 = form.get("zs_user_id").and_then(|s| s.parse().ok()).unwrap_or(0);
    let oid = state
        .accounts
        .as_ref()
        .and_then(|a| a.lock().ok())
        .and_then(|g| {
            let rec = g.users.get(acct)?;
            if rec["role"].as_str() == Some("owner") {
                return None;
            }
            rec["office_id"].as_str().map(|s| s.to_string())
        });
    let Some(office_id) = oid else {
        return Redirect::to(&format!("{}?msg=Ana+hesap+bulunamadı&hata=1", MAP_PATH)).into_response();
    };
    match store.link_account(uid, Some(acct), Some(&office_id)) {
        Ok(_) => {
            let _ = sync_account_to_zs(&state, &store, acct);
            Redirect::to(&format!("{}?msg=Eşleştirildi", MAP_PATH)).into_response()
        }
        Err(e) => Redirect::to(&format!(
            "{}?msg={}&hata=1",
            MAP_PATH,
            urlencoding::encode(&e.0)
        ))
        .into_response(),
    }
}

// ── Setup & Router Registration ────────────────────────────────────────────────────

pub fn register(
    app: Router,
    session_secret: &str,
    guard: Arc<dyn LoginGuardTrait>,
    client_ip: ClientIpFn,
    accounts_store: Option<Arc<Mutex<AccountStore>>>,
    owner_user: Option<OwnerUserFn>,
    base_dir: PathBuf,
) -> (Router, Arc<ZsState>) {
    let mut h1 = Sha256::new();
    h1.update(format!("{}|zs", session_secret).as_bytes());
    let mut secret = [0u8; 32];
    secret.copy_from_slice(&h1.finalize());

    let mut h2 = Sha256::new();
    h2.update(format!("{}|is-listesi-token", session_secret).as_bytes());
    let mut tok_secret = [0u8; 32];
    tok_secret.copy_from_slice(&h2.finalize());

    let file_key = zs_r2::load_key();
    let (storage, storage_desc) = zs_r2::make_storage();

    let state = Arc::new(ZsState {
        secret,
        tok_secret,
        guard,
        client_ip,
        accounts: accounts_store,
        owner_user,
        store: RwLock::new(None),
        notifier: RwLock::new(None),
        storage,
        file_key,
        storage_desc,
        setup_error: RwLock::new(None),
        base_dir,
    });

    // Try opening store immediately; if it fails, spawn background retry task
    if !try_open_store(&state) {
        let st_bg = state.clone();
        tokio::spawn(async move {
            let delays = [5u64, 10, 20, 30, 60];
            let mut i = 0usize;
            loop {
                let d = delays[i.min(delays.len() - 1)];
                tokio::time::sleep(std::time::Duration::from_secs(d)).await;
                i += 1;
                if try_open_store(&st_bg) {
                    println!("[+] İş Takip veritabanı {}. yeniden denemede açıldı.", i);
                    break;
                }
            }
        });
    }

    let mut router = app;
    for &p in PREFIXES {
        let p_static: &'static str = if p == NEW { NEW } else { OLD };
        let sub = Router::new()
            .route("/", get({
                let st = state.clone();
                move |headers| page_get(State(st), headers, p_static)
            }))
            .route("/login", post({
                let st = state.clone();
                move |headers, addr, body| login_post(State(st), headers, addr, p_static, body)
            }))
            .route("/logout", post({
                let st = state.clone();
                move |headers, addr| logout_post(State(st), headers, addr, p_static)
            }))
            .route("/static/zs.js", get({
                let st = state.clone();
                move || static_file(State(st), p_static, "zs.js", "application/javascript; charset=utf-8", false)
            }))
            .route("/static/zs.css", get({
                let st = state.clone();
                move || static_file(State(st), p_static, "zs.css", "text/css; charset=utf-8", false)
            }))
            .route("/static/icon-192.png", get({
                let st = state.clone();
                move || static_file(State(st), p_static, "icon-192.png", "image/png", false)
            }))
            .route("/static/icon-512.png", get({
                let st = state.clone();
                move || static_file(State(st), p_static, "icon-512.png", "image/png", false)
            }))
            .route("/static/icon-maskable-512.png", get({
                let st = state.clone();
                move || static_file(State(st), p_static, "icon-maskable-512.png", "image/png", false)
            }))
            .route("/static/apple-touch-icon.png", get({
                let st = state.clone();
                move || static_file(State(st), p_static, "apple-touch-icon.png", "image/png", false)
            }))
            .route("/static/badge-72.png", get({
                let st = state.clone();
                move || static_file(State(st), p_static, "badge-72.png", "image/png", false)
            }))
            .route("/manifest.webmanifest", get({
                let st = state.clone();
                move || static_file(State(st), p_static, "manifest.webmanifest", "application/manifest+json; charset=utf-8", false)
            }))
            .route("/sw.js", get({
                let st = state.clone();
                move || static_file(State(st), p_static, "sw.js", "application/javascript; charset=utf-8", true)
            }))
            .route("/api/bildirim", get({
                let st = state.clone();
                move |headers| api_notify_get(State(st), headers, p_static)
            }))
            .route("/api/bildirim/push", post({
                let st = state.clone();
                move |headers, body| api_notify_push_add(State(st), headers, p_static, body)
            }))
            .route("/api/bildirim/push/sil", post({
                let st = state.clone();
                move |headers, body| api_notify_push_remove(State(st), headers, p_static, body)
            }))
            .route("/api/bildirim/telegram", post({
                let st = state.clone();
                move |headers| api_notify_tg_link(State(st), headers, p_static)
            }))
            .route("/api/bildirim/telegram/sil", post({
                let st = state.clone();
                move |headers| api_notify_tg_unlink(State(st), headers, p_static)
            }))
            .route("/api/bildirim/deneme", post({
                let st = state.clone();
                move |headers| api_notify_test(State(st), headers, p_static)
            }))
            .route("/api/me", get({
                let st = state.clone();
                move |headers| api_me(State(st), headers, p_static)
            }))
            .route("/api/me/password", post({
                let st = state.clone();
                move |headers, addr, body| api_my_password(State(st), headers, addr, p_static, body)
            }))
            .route("/api/users", get({
                let st = state.clone();
                move |headers| api_users_list(State(st), headers, p_static)
            }).post({
                let st = state.clone();
                move |headers, addr, body| api_users_create(State(st), headers, addr, p_static, body)
            }))
            .route("/api/users/:id", post({
                let st = state.clone();
                move |headers, id, body| api_users_update(State(st), headers, id, p_static, body)
            }))
            .route("/api/users/:id/aktif", post({
                let st = state.clone();
                move |headers, id, body| api_users_active(State(st), headers, id, p_static, body)
            }))
            .route("/api/users/:id/sifre", post({
                let st = state.clone();
                move |headers, id, body| api_users_password(State(st), headers, id, p_static, body)
            }))
            .route("/api/tasks", get({
                let st = state.clone();
                move |headers, q| api_tasks(State(st), headers, q, p_static)
            }).post({
                let st = state.clone();
                move |headers, addr, body| api_task_create(State(st), headers, addr, p_static, body)
            }))
            .route("/api/tasks/:id", get({
                let st = state.clone();
                move |headers, id| api_task_get(State(st), headers, id, p_static)
            }).patch({
                let st = state.clone();
                move |headers, addr, id, body| api_task_update(State(st), headers, addr, id, p_static, body)
            }).delete({
                let st = state.clone();
                move |headers, addr, id| api_task_delete(State(st), headers, addr, id, p_static)
            }))
            .route("/api/tasks/:id/durum", post({
                let st = state.clone();
                move |headers, addr, id, body| api_task_transition(State(st), headers, addr, id, p_static, body)
            }))
            .route("/api/sablonlar", get({
                let st = state.clone();
                move |headers| api_templates(State(st), headers, p_static)
            }).post({
                let st = state.clone();
                move |headers, body| api_template_create(State(st), headers, p_static, body)
            }))
            .route("/api/sablonlar/:id", post({
                let st = state.clone();
                move |headers, id, body| api_template_update(State(st), headers, id, p_static, body)
            }).delete({
                let st = state.clone();
                move |headers, id| api_template_delete(State(st), headers, id, p_static)
            }))
            .route("/api/surecler", get({
                let st = state.clone();
                move |headers| api_processes(State(st), headers, p_static)
            }))
            .route("/api/etiketler", get({
                let st = state.clone();
                move |headers| api_tags(State(st), headers, p_static)
            }).post({
                let st = state.clone();
                move |headers, body| api_tag_create(State(st), headers, p_static, body)
            }))
            .route("/api/etiketler/:id", post({
                let st = state.clone();
                move |headers, id, body| api_tag_update(State(st), headers, id, p_static, body)
            }).delete({
                let st = state.clone();
                move |headers, id| api_tag_delete(State(st), headers, id, p_static)
            }))
            .route("/api/tasks/:id/etiketler", post({
                let st = state.clone();
                move |headers, id, body| api_task_tags(State(st), headers, id, p_static, body)
            }))
            .route("/api/tasks/:id/devret", post({
                let st = state.clone();
                move |headers, id, body| api_task_transfer(State(st), headers, id, p_static, body)
            }))
            .route("/api/tasks/:id/gorunur", post({
                let st = state.clone();
                move |headers, id, body| api_task_publish(State(st), headers, id, p_static, body)
            }))
            .route("/api/tasks/:id/kisiler", post({
                let st = state.clone();
                move |headers, id, body| api_member_add(State(st), headers, id, p_static, body)
            }))
            .route("/api/tasks/:id/kisiler/cikar", post({
                let st = state.clone();
                move |headers, id, body| api_member_remove(State(st), headers, id, p_static, body)
            }))
            .route("/api/tasks/:id/ek-sure", post({
                let st = state.clone();
                move |headers, id, body| api_extension_request(State(st), headers, id, p_static, body)
            }))
            .route("/api/ek-sure/:id/karar", post({
                let st = state.clone();
                move |headers, id, body| api_extension_decide(State(st), headers, id, p_static, body)
            }))
            .route("/api/tasks/:id/notes", post({
                let st = state.clone();
                move |headers, id, body| api_note_create(State(st), headers, id, p_static, body)
            }))
            .route("/api/tasks/:id/hatirlatici", get({
                let st = state.clone();
                move |headers, id| api_reminder_list(State(st), headers, id, p_static)
            }).post({
                let st = state.clone();
                move |headers, id, body| api_reminder_create(State(st), headers, id, p_static, body)
            }))
            .route("/api/hatirlatici", get({
                let st = state.clone();
                move |headers, q| api_free_reminder_list(State(st), headers, q, p_static)
            }).post({
                let st = state.clone();
                move |headers, body| api_free_reminder_create(State(st), headers, p_static, body)
            }))
            .route("/api/hatirlaticilar", get({
                let st = state.clone();
                move |headers, q| api_my_reminders(State(st), headers, q, p_static)
            }))
            .route("/api/hatirlatici/:id", delete({
                let st = state.clone();
                move |headers, id| api_reminder_delete(State(st), headers, id, p_static)
            }))
            .route("/api/tasks/:id/files", post({
                let st = state.clone();
                move |headers, id, mp| api_file_upload(State(st), headers, id, p_static, mp)
            }))
            .route("/api/rapor", get({
                let st = state.clone();
                move |headers, q| api_report(State(st), headers, q, p_static)
            }))
            .route("/api/rapor/etkinlik", get({
                let st = state.clone();
                move |headers, q| api_activity(State(st), headers, q, p_static)
            }))
            .route("/api/files/:id", get({
                let st = state.clone();
                move |headers, id| api_file_download(State(st), headers, id, p_static)
            }).delete({
                let st = state.clone();
                move |headers, id| api_file_delete(State(st), headers, id, p_static)
            }));

        router = router
            .route(p_static, get({
                let st = state.clone();
                move |headers| page_get(State(st), headers, p_static)
            }))
            .nest(p_static, sub);
    }

    router = router
        .route("/zs/telegram/hook", post({
            let st = state.clone();
            move |headers, body| telegram_hook(State(st), headers, body)
        }))
        .route("/is-listesi/api/token", post({
            let st = state.clone();
            move |headers, addr, body| api_token(State(st), headers, addr, body)
        }))
        .route("/is-listesi/api/sso-kod", post({
            let st = state.clone();
            move |headers| api_sso_code(State(st), headers)
        }))
        .route("/is-listesi/sso", get({
            let st = state.clone();
            move |q| sso_get(State(st), q)
        }))
        .route(MAP_PATH, get({
            let st = state.clone();
            move |headers, q| map_get(State(st), headers, q)
        }).post({
            let st = state.clone();
            move |headers, body| map_post(State(st), headers, body)
        }));

    (router, state)
}

fn try_open_store(state: &Arc<ZsState>) -> bool {
    let store = match ZsStore::new(None, None) {
        Ok(s) => Arc::new(s),
        Err(e) => {
            println!("[!] İş Takip (/zs) veritabanı açılamadı: {}", e);
            return false;
        }
    };
    // Bootstrap admin if needed
    let user = std::env::var("ZS_ADMIN_USER").unwrap_or_default().trim().to_string();
    let pw = std::env::var("ZS_ADMIN_PASSWORD").unwrap_or_default().trim().to_string();
    if store.user_count().unwrap_or(0) == 0 {
        if user.is_empty() || pw.is_empty() {
            let msg = "Henüz yönetici hesabı yok: sunucuda ZS_ADMIN_USER ve ZS_ADMIN_PASSWORD ayarlanmalı.";
            if let Ok(mut g) = state.setup_error.write() {
                *g = Some(msg.to_string());
            }
            println!("[!] İş Takip (/zs): {}", msg);
        } else if let Err(e) = store.create_user(&user, &user, &pw, "admin", None) {
            let msg = format!(
                "İlk yönetici hesabı oluşturulamadı: {} (ZS_ADMIN_USER / ZS_ADMIN_PASSWORD'ü düzeltip servisi yeniden başlatın.)",
                e.0
            );
            if let Ok(mut g) = state.setup_error.write() {
                *g = Some(msg.clone());
            }
            println!("[!] İş Takip (/zs): {}", msg);
        } else {
            println!("[+] İş Takip (/zs): ilk yönetici oluşturuldu ({}).", slugify(&user));
        }
    }

    if let Ok(mut g) = state.store.write() {
        *g = Some(store.clone());
    }
    println!(
        "[*] İş Takip (/zs): belgeler: {}; şifreleme anahtarı {}.",
        state.storage_desc,
        if state.file_key.is_some() { "VAR" } else { "YOK → yükleme kapalı" }
    );

    let notifier = Arc::new(Notifier::new(store));
    let st_notif = state.clone();
    let n_clone = notifier.clone();
    if let Ok(mut g) = state.notifier.write() {
        *g = Some(notifier.clone());
    }
    tokio::spawn(async move {
        let n_cb = n_clone.clone();
        {
            let mut gh = n_clone.group_handler.write().await;
            *gh = Some(Arc::new(move |msg: Value| {
                let st = st_notif.clone();
                let nc = n_cb.clone();
                Box::pin(async move {
                    handle_tg_group_message(&st, &msg, &nc).await;
                })
            }));
        }
        println!("[*] İş Takip bildirim: {}", n_clone.describe().await);
        n_clone.start().await;
    });
    true
}
