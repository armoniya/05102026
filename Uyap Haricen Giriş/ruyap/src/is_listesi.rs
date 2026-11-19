//! Bulut hesap köprüsü (eski adı: İş Listesi köprüsü)
//! -------------------------------------------------
//! Bulut (cokluuyap.com) yalnız giriş / kimlik için kullanılır: yerel ağ girişinde üyenin kendi kullanıcı adı
//! ve parolasını doğrulamak (`hesap_dogrula`) ve büro üyelerinin listesini almak (`uye_listesi`). İş verisi
//! bulutta tutulmaz; buluttaki eski İş Listesine istek ileten köprü (`/__uyap_kisi__/is-listesi`) ile
//! "UYAP'ta aç" / "PTT sorgula" sayfaları 17.11.2026'da kaldırıldı (İş Listesi büro veritabanında).

use std::time::{Duration, SystemTime, UNIX_EPOCH};

use once_cell::sync::Lazy;
use parking_lot::Mutex;
use reqwest::{Client, Method};
use serde_json::{json, Map, Value};
use tokio::sync::Mutex as AsyncMutex;

const _TIMEOUT: f64 = 20.0;

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

/// Yerel ağ girişi (İş Listesi L2): büro üyesinin kendi Çoklu UYAP kullanıcı adı + parolası bulutta doğrulanır
/// (ofis kodu bu programınki); parola saklanmaz. Dönüş: hesap adı (`ahmet@buro`).
pub async fn hesap_dogrula(kullanici: &str, parola: &str) -> Result<String, String> {
    let cfg = CFG.lock().clone();
    if cfg.base.is_empty() || cfg.office_code.is_empty() {
        return Err("bulut adresi ayarlı değil".into());
    }
    let r = HTTP_CLIENT
        .post(format!("{}/is-listesi/api/token", cfg.base))
        .json(&json!({"office_code": cfg.office_code, "username": kullanici, "password": parola}))
        .send()
        .await
        .map_err(|e| http_error_type_name(&e))?;
    let durum = r.status().as_u16();
    let d = _safe_json(r).await;
    let hesap = d["me"]["account_username"].as_str().unwrap_or("").trim().to_string();
    if durum != 200 || hesap.is_empty() {
        return Err(d["error"].as_str().unwrap_or("Kullanıcı adı veya parola hatalı.").to_string());
    }
    Ok(hesap)
}

/// Büro üyeleri (İş Listesi L2): kendi hesabım (`me`) ve yöneticiysem bürodaki bütün İş Listesi
/// kullanıcıları (`account_username`, `display_name`, `role`, `active`). Yalnız ad ve rol; iş verisi yok.
pub async fn uye_listesi() -> Result<(Option<Value>, Vec<Value>), String> {
    if CFG.lock().base.is_empty() {
        return Err("bulut adresi ayarlı değil".into());
    }
    let hata = |e: CallError| match e {
        CallError::Fail(f) => f.msg,
        CallError::Http(h) => h,
    };
    let (durum, d) = _call(Method::GET, "me", "", None).await.map_err(hata)?;
    if durum != 200 {
        return Err(d["error"].as_str().unwrap_or("İş Listesi hesabı okunamadı").to_string());
    }
    let ben = d.get("me").cloned().filter(|v| v.is_object());
    let mut uyeler = Vec::new();
    if ben.as_ref().and_then(|b| b["role"].as_str()) == Some("admin") {
        let (durum, d) = _call(Method::GET, "users", "", None).await.map_err(hata)?;
        if durum == 200 {
            uyeler = d["users"].as_array().cloned().unwrap_or_default();
        }
    }
    Ok((ben, uyeler))
}
