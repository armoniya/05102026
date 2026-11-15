//! İş Listesi köprüsü (`uyap_core/is_listesi.py` birebir Rust portu)
//! ------------------------------------------------------------------
//! UYAP dosya kartındaki "İş Ata" düğmesi ve "İş Listesi" bağlantısı (uyap_proxy'nin enjekte ettiği
//! JS) bu yerel uçları çağırır; köprü, isteği KULLANICININ KENDİ CokluUyap hesabıyla cokluuyap.com
//! /is-listesi/api/* uçlarına iletir.

use std::collections::HashMap;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use once_cell::sync::Lazy;
use parking_lot::Mutex;
use regex::Regex;
use reqwest::{Client, Method};
use serde_json::{json, Map, Value};
use tokio::sync::Mutex as AsyncMutex;

pub const PREFIX: &str = "/__uyap_kisi__/is-listesi";
pub const _TIMEOUT: f64 = 20.0;

/// İş Listesi'nden UYAP dosya kartını açma
pub const DOSYA_AC_PATH: &str = "/__uyap_kisi__/dosya-ac";
pub const DOSYA_AC_SAYFASI: &[u8] = concat!(
    "<!doctype html><html lang='tr'><head><meta charset='utf-8'><title>UYAP dosyası açılıyor…</title>",
    "<meta name='referrer' content='no-referrer'></head><body style='font:14px sans-serif;padding:24px'>",
    "UYAP dosyası açılıyor…<script>(function(){try{var h=decodeURIComponent((location.hash||'').slice(1));",
    "var d=JSON.parse(h);if(d&&typeof d==='object'&&d.dosyaId){sessionStorage.setItem('cokluuyapDosyaAc',",
    "JSON.stringify({t:Date.now(),dosya:d}));}}catch(e){}location.replace('/');})();</script></body></html>"
)
.as_bytes();

/// İş Listesi'nden PTT sorgusu
pub const PTT_PATH: &str = "/__uyap_kisi__/ptt";
pub const PTT_SAYFASI: &[u8] = r#"<!doctype html><html lang='tr'><head><meta charset='utf-8'>
<meta name='referrer' content='no-referrer'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>PTT Gönderi Takip</title>
<style>
body{margin:0;background:#111827;color:#e5e7eb;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
main{max-width:560px;margin:0 auto;padding:20px 16px}
h1{font-size:17px;margin:0 0 12px}
.s{display:flex;gap:10px;margin-top:4px}.s .a{flex:none;width:120px;color:#9ca3af}.s .d{font-weight:600;word-break:break-word}
.h{margin-top:6px;padding:7px 10px;border-radius:8px;background:rgba(255,255,255,.06)}
.h .t{color:#9ca3af;font-size:12px}.h .x{color:#cbd5e1;font-size:12px}
.hata{color:#fca5a5;word-break:break-word}
button{margin:14px 8px 0 0;padding:7px 14px;border:0;border-radius:8px;background:#7d9a7d;color:#fff;font:inherit;cursor:pointer}
button.ik{background:rgba(255,255,255,.15)}
</style></head><body><main><h1 id='bas'>PTT Gönderi Takip</h1><div id='govde'></div></main>
<script>
(function(){
  var barkod = decodeURIComponent((location.hash || '').slice(1)).replace(/\D/g, '');
  var govde = document.getElementById('govde');
  function el(t, c, m){ var e = document.createElement(t); if (c) e.className = c; if (m != null) e.textContent = m; return e; }
  function dugme(ad, ik, fn){ var b = el('button', ik ? 'ik' : null, ad); b.onclick = fn; govde.appendChild(b); }
  function satir(ad, deger, renk){
    if (!deger) return;
    var s = el('div', 's'); s.appendChild(el('div', 'a', ad));
    var d = el('div', 'd', deger); d.style.color = renk || '#f3f4f6'; s.appendChild(d); govde.appendChild(s);
  }
  function sorgula(){
    govde.replaceChildren(el('div', null, 'PTT sorgulanıyor… (ptt.gov.tr, 10–20 sn sürebilir)'));
    fetch('/__uyap_agent__/ptt_sorgu?barkod=' + encodeURIComponent(barkod), {cache: 'no-store', credentials: 'same-origin'})
      .then(function(r){ return r.json().catch(function(){ return {ok: false, error: r.status === 401 || r.status === 403
          ? 'Bu tarayıcıda CokluUyap girişi yok. CokluUyap penceresinden "Tarayıcıda aç" ile bir kez açıp yeniden deneyin.'
          : 'PTT sorgusu yanıt vermedi (HTTP ' + r.status + ').'}; }); })
      .catch(function(){ return {ok: false, error: 'CokluUyap\'a ulaşılamadı. Program bu bilgisayarda açık mı?'}; })
      .then(goster);
  }
  function goster(j){
    govde.replaceChildren();
    if (!j || !j.ok) {
      govde.appendChild(el('div', 'hata', (j && j.error) || 'PTT sorgusu başarısız.'));
      dugme('Yeniden dene', false, sorgula);
      return;
    }
    var d = (j.durum || '').toLocaleLowerCase('tr');
    satir('Durum', j.durum || '—', /teslim edildi/.test(d) ? '#86efac' : /iade/.test(d) ? '#fca5a5' : '#fde68a');
    satir(/teslim edildi/.test(d) ? 'Teslim tarihi' : 'Son işlem', j.tarih);
    satir(/iade/.test(d) ? 'İade nedeni' : 'Teslim şekli', j.teslim_sekli);
    if (j.kabul) { satir('Kabul tarihi', j.kabul.tarih); satir('Gönderici', j.kabul.gonderici); satir('Alıcı', j.kabul.alici); }
    if (j.hareketler && j.hareketler.length) {
      var b = el('div', null, 'Hareketler'); b.style.cssText = 'margin-top:14px;font-weight:600;color:#cbd5e1'; govde.appendChild(b);
      j.hareketler.slice().reverse().forEach(function(hr){      // en yeni üstte
        var s = el('div', 'h'); s.appendChild(el('div', 'd', hr.aciklama || '—'));
        s.appendChild(el('div', 't', [hr.tarih, hr.yer].filter(Boolean).join(' · ')));
        if (hr.detay) s.appendChild(el('div', 'x', hr.detay));
        govde.appendChild(s);
      });
    }
    dugme('Yeniden sorgula', true, sorgula);
    dugme('Barkodu kopyala', true, function(){ navigator.clipboard && navigator.clipboard.writeText(barkod); });
  }
  if (!/^\d{13}$/.test(barkod)) { govde.appendChild(el('div', 'hata', 'Geçersiz barkod.')); return; }
  document.getElementById('bas').textContent = 'PTT Gönderi Takip · ' + barkod;
  document.title = 'PTT · ' + barkod;
  // Sayfa yerel yetkisiz açılır (başka bir site de bu adrese yönlendirebilir); sorgu ofiste Chrome
  // açtırdığı için kendiliğinden başlamaz, kullanıcı düğmeye basar. Sayfa başka siteye gömülemez
  // (çapraz-site iframe'i yerel kapı reddeder) → tıklama da dışarıdan yaptırılamaz.
  govde.appendChild(el('div', null, 'Barkod ' + barkod + ' ptt.gov.tr\'de sorgulanacak.'));
  dugme('Sorgula', false, sorgula);
})();
</script></body></html>"#
    .as_bytes();

#[derive(Debug, Clone, Default)]
struct ConfigState {
    base: String,
    office_code: String,
    username: String,
    password: String,
}

#[derive(Debug, Clone, Default)]
struct TokenState {
    value: String,
    exp: f64,
}

static CFG: Lazy<Mutex<ConfigState>> = Lazy::new(|| Mutex::new(ConfigState::default()));
static TOK: Lazy<Mutex<TokenState>> = Lazy::new(|| Mutex::new(TokenState::default()));
static TOKEN_LOCK: Lazy<AsyncMutex<()>> = Lazy::new(|| AsyncMutex::new(()));
static HTTP_CLIENT: Lazy<Client> = Lazy::new(|| {
    Client::builder()
        .timeout(Duration::from_secs_f64(_TIMEOUT))
        .tcp_nodelay(true)
        .pool_max_idle_per_host(16)
        .build()
        .expect("is_listesi reqwest client build")
});

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

pub fn configure(api_base: &str, office_code: &str, username: &str, password: &str) {
    {
        let mut cfg = CFG.lock();
        cfg.base = api_base.trim_end_matches('/').to_string();
        cfg.office_code = office_code.to_string();
        cfg.username = username.to_string();
        cfg.password = password.to_string();
    }
    {
        let mut tok = TOK.lock();
        tok.value.clear();
        tok.exp = 0.0;
    }
}

pub fn clear() {
    configure("", "", "", "");
}

pub fn baglantidan_configure(signaling: &str, office_code: &str, username: &str, password: &str) {
    let mut u = signaling.trim().to_string();
    if let Some(rest) = u.strip_prefix("wss://") {
        u = format!("https://{}", rest);
    } else if let Some(rest) = u.strip_prefix("ws://") {
        u = format!("http://{}", rest);
    }
    if let Some(rest) = u.strip_suffix("/ws") {
        u = rest.to_string();
    }
    configure(&u, office_code, username, password);
}

pub fn is_path(path: &str) -> bool {
    let p = format!("/{}", path.trim_start_matches('/'));
    p == PREFIX || p.starts_with(&format!("{}/", PREFIX))
}

fn _json(status: u16, data: &Value) -> (u16, HashMap<String, String>, Vec<u8>) {
    let mut headers = HashMap::new();
    headers.insert(
        "Content-Type".to_string(),
        "application/json; charset=utf-8".to_string(),
    );
    headers.insert("Cache-Control".to_string(), "no-store".to_string());
    let body = serde_json::to_vec(data).unwrap_or_else(|_| b"{}".to_vec());
    (status, headers, body)
}

fn _err(status: u16, msg: &str) -> (u16, HashMap<String, String>, Vec<u8>) {
    _json(status, &json!({ "ok": false, "error": msg }))
}

#[derive(Debug)]
struct Fail {
    status: u16,
    msg: String,
}

#[derive(Debug)]
enum CallError {
    Fail(Fail),
    Http(String),
}

async fn _safe_json(r: reqwest::Response) -> Value {
    match r.json::<Value>().await {
        Ok(Value::Object(map)) => Value::Object(map),
        _ => Value::Object(Map::new()),
    }
}

async fn _token(client: &Client, force: bool) -> Result<String, CallError> {
    let _guard = TOKEN_LOCK.lock().await;
    if !force {
        let tok = TOK.lock().clone();
        if !tok.value.is_empty() && tok.exp - 60.0 > now_secs() {
            return Ok(tok.value);
        }
    }
    let cfg = CFG.lock().clone();
    let url = format!("{}/is-listesi/api/token", cfg.base);
    let payload = json!({
        "office_code": cfg.office_code,
        "username": cfg.username,
        "password": cfg.password,
    });
    let r = client
        .post(&url)
        .json(&payload)
        .send()
        .await
        .map_err(|e| CallError::Http(http_error_type_name(&e)))?;
    let status = r.status().as_u16();
    let d = _safe_json(r).await;
    let tok_str = d
        .get("token")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if status != 200 || tok_str.is_empty() {
        let err_msg = d
            .get("error")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .unwrap_or("İş Listesi'ne giriş yapılamadı.")
            .to_string();
        return Err(CallError::Fail(Fail {
            status: if status >= 400 { status } else { 502 },
            msg: err_msg,
        }));
    }
    let expires_in = d
        .get("expires_in")
        .and_then(|v| {
            v.as_i64()
                .or_else(|| v.as_str().and_then(|s| s.parse::<i64>().ok()))
        })
        .filter(|&x| x != 0)
        .unwrap_or(3600);
    {
        let mut tok = TOK.lock();
        tok.value = tok_str.clone();
        tok.exp = now_secs() + expires_in as f64;
    }
    Ok(tok_str)
}

fn http_error_type_name(e: &reqwest::Error) -> String {
    if e.is_timeout() {
        "TimeoutException".to_string()
    } else if e.is_connect() {
        "ConnectError".to_string()
    } else {
        "HTTPError".to_string()
    }
}

fn _obj(body: &[u8]) -> Option<Map<String, Value>> {
    let slice = if body.is_empty() { b"{}" } else { body };
    match serde_json::from_slice::<Value>(slice) {
        Ok(Value::Object(map)) => Some(map),
        _ => None,
    }
}

fn _sablon_alanlari(d: &Map<String, Value>) -> Value {
    let keys = [
        "title",
        "description",
        "due_days",
        "priority",
        "taraf",
        "kontrol",
        "sonuclar",
        "asama",
        "etiketler",
    ];
    let mut out = Map::new();
    for k in keys {
        if let Some(v) = d.get(k) {
            out.insert(k.to_string(), v.clone());
        }
    }
    Value::Object(out)
}

async fn _call(
    method: Method,
    api_path: &str,
    query: &str,
    body: Option<&Value>,
) -> Result<(u16, Value), CallError> {
    let base = CFG.lock().base.clone();
    let url = if query.is_empty() {
        format!("{}/is-listesi/api/{}", base, api_path)
    } else {
        format!("{}/is-listesi/api/{}?{}", base, api_path, query)
    };
    let client = &*HTTP_CLIENT;
    for attempt in [0, 1] {
        let tok = _token(client, attempt == 1).await?;
        let mut req = client
            .request(method.clone(), &url)
            .header("Authorization", format!("Bearer {}", tok));
        if let Some(b) = body {
            req = req.json(b);
        }
        let r = req
            .send()
            .await
            .map_err(|e| CallError::Http(http_error_type_name(&e)))?;
        let status = r.status().as_u16();
        if status != 401 || attempt == 1 {
            let data = _safe_json(r).await;
            return Ok((status, data));
        }
    }
    Ok((502, Value::Object(Map::new())))
}

fn parse_qs(query: &str) -> HashMap<String, Vec<String>> {
    let mut map: HashMap<String, Vec<String>> = HashMap::new();
    for (k, v) in url::form_urlencoded::parse(query.as_bytes()) {
        if !v.is_empty() {
            map.entry(k.into_owned()).or_default().push(v.into_owned());
        }
    }
    map
}

fn truncate_chars(s: &str, max_chars: usize) -> String {
    s.chars().take(max_chars).collect()
}

fn val_to_str(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Bool(false)) => String::new(),
        Some(Value::Bool(true)) => "True".to_string(),
        Some(other) => other.to_string(),
    }
}

pub async fn handle(
    method: &str,
    path: &str,
    query: &str,
    body: &[u8],
) -> (u16, HashMap<String, String>, Vec<u8>) {
    let cfg = CFG.lock().clone();
    if cfg.base.is_empty() || cfg.username.is_empty() || cfg.password.is_empty() {
        return _err(
            503,
            "CokluUyap'a giriş yapılmamış; İş Listesi kullanılamıyor.",
        );
    }
    let full_sub = format!("/{}", path.trim_start_matches('/'));
    let sub = full_sub
        .get(PREFIX.len()..)
        .unwrap_or("")
        .trim_matches('/');

    let res: Result<(u16, HashMap<String, String>, Vec<u8>), CallError> = async {
        if sub == "me" && method == "GET" {
            let (st, data) = _call(Method::GET, "me", "", None).await?;
            return Ok(_json(st, &data));
        }
        if sub == "tasks" && method == "GET" {
            let q = parse_qs(query);
            let dosya_id = q
                .get("dosya_id")
                .and_then(|v| v.first())
                .map(|s| truncate_chars(s, 200))
                .unwrap_or_default();
            let birim = q
                .get("birim_id")
                .and_then(|v| v.first())
                .map(|s| truncate_chars(s, 200))
                .unwrap_or_default();
            let dosya_no = q
                .get("dosya_no")
                .and_then(|v| v.first())
                .map(|s| truncate_chars(s, 200))
                .unwrap_or_default();
            if !dosya_id.is_empty() || (!birim.is_empty() && !dosya_no.is_empty()) {
                let encoded = {
                    let mut ser = url::form_urlencoded::Serializer::new(String::new());
                    ser.append_pair("filtre", "tumu");
                    if !dosya_id.is_empty() {
                        ser.append_pair("dosya_id", &dosya_id);
                    }
                    if !birim.is_empty() && !dosya_no.is_empty() {
                        ser.append_pair("birim_id", &birim);
                        ser.append_pair("dosya_no", &dosya_no);
                    }
                    ser.finish()
                };
                let (st, data) = _call(Method::GET, "tasks", &encoded, None).await?;
                return Ok(_json(st, &data));
            }
            let filtre = q
                .get("filtre")
                .and_then(|v| v.first())
                .map(|s| s.as_str())
                .unwrap_or("benim");
            if !matches!(filtre, "benim" | "onay" | "tumu") {
                return Ok(_err(400, "Geçersiz süzgeç."));
            }
            let encoded = url::form_urlencoded::Serializer::new(String::new())
                .append_pair("filtre", filtre)
                .finish();
            let (st, data) = _call(Method::GET, "tasks", &encoded, None).await?;
            return Ok(_json(st, &data));
        }
        if sub == "tasks" && method == "POST" {
            let Some(d) = _obj(body) else {
                return Ok(_err(400, "Geçersiz istek verisi."));
            };
            let keys = [
                "title",
                "description",
                "assignee_id",
                "due_at",
                "priority",
                "dosya",
                "sablon",
                "etiketler",
            ];
            let mut keep = Map::new();
            for k in keys {
                if let Some(v) = d.get(k) {
                    keep.insert(k.to_string(), v.clone());
                }
            }
            let (st, data) =
                _call(Method::POST, "tasks", "", Some(&Value::Object(keep))).await?;
            return Ok(_json(st, &data));
        }
        if sub == "sablonlar" && method == "GET" {
            let (st, data) = _call(Method::GET, "sablonlar", "", None).await?;
            return Ok(_json(st, &data));
        }
        if (sub == "surecler" || sub == "etiketler") && method == "GET" {
            let (st, data) = _call(Method::GET, sub, "", None).await?;
            return Ok(_json(st, &data));
        }
        if sub == "sablonlar" && method == "POST" {
            let Some(d) = _obj(body) else {
                return Ok(_err(400, "Geçersiz istek verisi."));
            };
            let payload = _sablon_alanlari(&d);
            let (st, data) = _call(Method::POST, "sablonlar", "", Some(&payload)).await?;
            return Ok(_json(st, &data));
        }

        static RE_SABLON: Lazy<Regex> =
            Lazy::new(|| Regex::new(r"^sablonlar/(\d{1,12})$").unwrap());
        if let Some(caps) = RE_SABLON.captures(sub) {
            let id = &caps[1];
            if method == "POST" {
                let Some(d) = _obj(body) else {
                    return Ok(_err(400, "Geçersiz istek verisi."));
                };
                let payload = _sablon_alanlari(&d);
                let (st, data) = _call(
                    Method::POST,
                    &format!("sablonlar/{}", id),
                    "",
                    Some(&payload),
                )
                .await?;
                return Ok(_json(st, &data));
            }
            if method == "DELETE" {
                let (st, data) =
                    _call(Method::DELETE, &format!("sablonlar/{}", id), "", None).await?;
                return Ok(_json(st, &data));
            }
        }

        static RE_DURUM: Lazy<Regex> =
            Lazy::new(|| Regex::new(r"^tasks/(\d{1,12})/durum$").unwrap());
        if let Some(caps) = RE_DURUM.captures(sub) {
            if method == "POST" {
                let id = &caps[1];
                let Some(d) = _obj(body) else {
                    return Ok(_err(400, "Geçersiz işlem."));
                };
                let action = d.get("action").and_then(|v| v.as_str()).unwrap_or("");
                if !matches!(action, "gonder" | "basla" | "tamamla" | "onayla" | "reddet") {
                    return Ok(_err(400, "Geçersiz işlem."));
                }
                let reason = truncate_chars(&val_to_str(d.get("reason")), 2000);
                let mut req_body = Map::new();
                req_body.insert("action".to_string(), Value::String(action.to_string()));
                req_body.insert("reason".to_string(), Value::String(reason));

                if let Some(sonuc) = d.get("sonuc").and_then(|v| v.as_str()) {
                    if matches!(sonuc, "olumlu" | "olumsuz" | "bekliyor") {
                        req_body.insert("sonuc".to_string(), Value::String(sonuc.to_string()));
                        if let Some(s) = d.get("sonraki") {
                            if s == &Value::Bool(false) {
                                req_body.insert("sonraki".to_string(), Value::Bool(false));
                            } else if let Value::Object(s_map) = s {
                                let baslik =
                                    truncate_chars(&val_to_str(s_map.get("baslik")), 200);
                                let gun = s_map.get("gun").cloned().unwrap_or(Value::Null);
                                req_body.insert(
                                    "sonraki".to_string(),
                                    json!({ "baslik": baslik, "gun": gun }),
                                );
                            }
                        }
                        static RE_DATE: Lazy<Regex> =
                            Lazy::new(|| Regex::new(r"^\d{4}-\d{2}-\d{2}$").unwrap());
                        if let Some(k) = d.get("kesinlesme").and_then(|v| v.as_str()) {
                            if RE_DATE.is_match(k) {
                                req_body.insert(
                                    "kesinlesme".to_string(),
                                    Value::String(k.to_string()),
                                );
                            }
                        }
                    }
                }
                let (st, data) = _call(
                    Method::POST,
                    &format!("tasks/{}/durum", id),
                    "",
                    Some(&Value::Object(req_body)),
                )
                .await?;
                return Ok(_json(st, &data));
            }
        }

        static RE_EK_SURE: Lazy<Regex> =
            Lazy::new(|| Regex::new(r"^tasks/(\d{1,12})/ek-sure$").unwrap());
        if let Some(caps) = RE_EK_SURE.captures(sub) {
            if method == "POST" {
                let id = &caps[1];
                let Some(d) = _obj(body) else {
                    return Ok(_err(400, "Geçersiz istek verisi."));
                };
                let Some(new_due) = d.get("new_due").and_then(|v| v.as_i64()) else {
                    return Ok(_err(400, "Geçersiz istek verisi."));
                };
                let reason = truncate_chars(&val_to_str(d.get("reason")), 2000);
                let payload = json!({ "new_due": new_due, "reason": reason });
                let (st, data) = _call(
                    Method::POST,
                    &format!("tasks/{}/ek-sure", id),
                    "",
                    Some(&payload),
                )
                .await?;
                return Ok(_json(st, &data));
            }
        }

        static RE_HATIRLATICI: Lazy<Regex> =
            Lazy::new(|| Regex::new(r"^tasks/(\d{1,12})/hatirlatici$").unwrap());
        if let Some(caps) = RE_HATIRLATICI.captures(sub) {
            if method == "POST" {
                let id = &caps[1];
                let Some(d) = _obj(body) else {
                    return Ok(_err(400, "Geçersiz istek verisi."));
                };
                let Some(remind_at) = d.get("remind_at").and_then(|v| v.as_i64()) else {
                    return Ok(_err(400, "Geçersiz istek verisi."));
                };
                let note = truncate_chars(&val_to_str(d.get("note")), 500);
                let payload = json!({ "remind_at": remind_at, "note": note });
                let (st, data) = _call(
                    Method::POST,
                    &format!("tasks/{}/hatirlatici", id),
                    "",
                    Some(&payload),
                )
                .await?;
                return Ok(_json(st, &data));
            }
        }

        static RE_KARAR: Lazy<Regex> =
            Lazy::new(|| Regex::new(r"^ek-sure/(\d{1,12})/karar$").unwrap());
        if let Some(caps) = RE_KARAR.captures(sub) {
            if method == "POST" {
                let id = &caps[1];
                let Some(d) = _obj(body) else {
                    return Ok(_err(400, "Geçersiz istek verisi."));
                };
                let Some(approve) = d.get("approve").and_then(|v| v.as_bool()) else {
                    return Ok(_err(400, "Geçersiz istek verisi."));
                };
                let note = truncate_chars(&val_to_str(d.get("note")), 2000);
                let payload = json!({ "approve": approve, "note": note });
                let (st, data) = _call(
                    Method::POST,
                    &format!("ek-sure/{}/karar", id),
                    "",
                    Some(&payload),
                )
                .await?;
                return Ok(_json(st, &data));
            }
        }

        if sub == "ac" && method == "GET" {
            let empty_obj = json!({});
            let (status, d) = _call(Method::POST, "sso-kod", "", Some(&empty_obj)).await?;
            let url_part = d.get("url").and_then(|v| v.as_str()).unwrap_or("");
            if status != 200 || url_part.is_empty() {
                let err_msg = d
                    .get("error")
                    .and_then(|v| v.as_str())
                    .filter(|s| !s.is_empty())
                    .unwrap_or("İş Listesi açılamadı.");
                return Ok(_err(if status >= 400 { status } else { 502 }, err_msg));
            }
            let q = parse_qs(query);
            let task = q
                .get("is")
                .and_then(|v| v.first())
                .map(|s| s.as_str())
                .unwrap_or("");
            let mut extra = if !task.is_empty()
                && task.chars().all(|c| c.is_ascii_digit())
                && task.len() < 12
            {
                format!("&is={}", task)
            } else {
                String::new()
            };
            if q.get("sekme").and_then(|v| v.first()).map(|s| s.as_str()) == Some("sablonlar") {
                extra = "&sekme=sablonlar".to_string();
            }
            let mut headers = HashMap::new();
            headers.insert(
                "Location".to_string(),
                // yerel=1: site bu tarayıcının bu bilgisayardaki programdan geldiğini hatırlar; "UYAP'ta aç"
                // yoklaması yanıtsız kalsa da cokluuyap.com yerine 127.0.0.1'e gider (istek B-16).
                format!("{}{}{}&yerel=1", cfg.base, url_part, extra),
            );
            headers.insert("Cache-Control".to_string(), "no-store".to_string());
            headers.insert("Referrer-Policy".to_string(), "no-referrer".to_string());
            return Ok((303, headers, Vec::new()));
        }

        Ok(_err(404, "Bulunamadı."))
    }
    .await;

    match res {
        Ok(out) => out,
        Err(CallError::Fail(e)) => _err(e.status, &e.msg),
        Err(CallError::Http(name)) => {
            _err(503, &format!("cokluuyap.com'a ulaşılamadı: {}", name))
        }
    }
}
