//! CokluUyap Satıcı Sunucusu (vendor_server.rs, 1:1 port of vendor_server.py)

use std::collections::{HashMap, HashSet};
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, RwLock};
use std::time::{Duration, Instant};

use axum::body::{Body, Bytes};
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{ConnectInfo, Path as AxumPath, Query, State};
use axum::http::{header, HeaderMap, HeaderValue, Method, Request, StatusCode};
use axum::middleware::{self, Next};
use axum::response::{Html, IntoResponse, Redirect, Response};
use axum::routing::{get, post};
use axum::Router;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use futures_util::{SinkExt, StreamExt};
use hmac::{Hmac, Mac};
use lettre::message::header::ContentType;
use lettre::transport::smtp::authentication::Credentials;
use lettre::{Message as MailMessage, SmtpTransport, Transport};
use rand::RngCore;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use tokio::sync::mpsc;

use crate::accounts::{self, password_policy_error, totp_verify, AccountStore};
use crate::zs::{self, LoginGuardTrait};

type HmacSha256 = Hmac<Sha256>;

pub const OWNER_COOKIE: &str = "uyap_owner";
pub const ADMIN_COOKIE: &str = "uyap_admin";
pub const OFFICE_COOKIE: &str = "uyap_office";
pub const DOWNLOAD_COOKIE: &str = "uyap_dl";

pub const DEFAULT_ICE: &[&str] = &[
    "stun:stun.l.google.com:19302",
    "stun:stun1.l.google.com:19302",
];

#[derive(Clone, Default)]
pub struct LoginGuard {
    inner: Arc<Mutex<HashMap<(String, String), (i64, i64)>>>,
}

impl LoginGuardTrait for LoginGuard {
    fn blocked_for(&self, ip: &str, user: &str) -> i64 {
        let now = accounts::now_ts();
        let Ok(mut g) = self.inner.lock() else { return 0; };
        let key = (ip.to_string(), user.to_lowercase());
        if let Some((fails, until)) = g.get(&key).copied() {
            if until > now {
                return until - now;
            }
            if fails >= 5 && until <= now {
                g.remove(&key);
            }
        }
        0
    }

    fn fail(&self, ip: &str, user: &str) {
        let now = accounts::now_ts();
        if let Ok(mut g) = self.inner.lock() {
            let key = (ip.to_string(), user.to_lowercase());
            let (fails, _) = g.get(&key).copied().unwrap_or((0, 0));
            let nf = fails + 1;
            let wait = if nf >= 5 {
                let exp = (nf - 5).min(6) as u32;
                (30i64 * 2i64.pow(exp)).min(900)
            } else {
                0
            };
            g.insert(key, (nf, now + wait));
        }
    }

    fn ok(&self, ip: &str, user: &str) {
        if let Ok(mut g) = self.inner.lock() {
            g.remove(&(ip.to_string(), user.to_lowercase()));
        }
    }
}

#[derive(Clone)]
pub struct HizSiniri {
    inner: Arc<Mutex<HashMap<String, Vec<f64>>>>,
    limit: usize,
    window: f64,
}

impl HizSiniri {
    pub fn new(limit: usize, window: f64) -> Self {
        Self {
            inner: Arc::new(Mutex::new(HashMap::new())),
            limit,
            window,
        }
    }

    pub fn allow(&self, ip: &str) -> bool {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0);
        let Ok(mut g) = self.inner.lock() else { return true; };
        let vec = g.entry(ip.to_string()).or_default();
        vec.retain(|&t| now - t < self.window);
        if vec.len() >= self.limit {
            return false;
        }
        vec.push(now);
        true
    }
}

struct RoomState {
    office: Option<mpsc::UnboundedSender<String>>,
    homes: HashMap<String, mpsc::UnboundedSender<String>>,
    ticket_secret: String,
    office_name: String,
    office_id: String,
}

impl RoomState {
    fn new() -> Self {
        let mut rnd = [0u8; 24];
        rand::thread_rng().fill_bytes(&mut rnd);
        Self {
            office: None,
            homes: HashMap::new(),
            ticket_secret: URL_SAFE_NO_PAD.encode(rnd),
            office_name: String::new(),
            office_id: String::new(),
        }
    }
}

pub struct AppState {
    pub accounts: Arc<Mutex<AccountStore>>,
    pub guard: Arc<LoginGuard>,
    pub ice_limiter: HizSiniri,
    pub reset_limiter: HizSiniri,
    pub reg_limiter: HizSiniri,
    pub pay_limiter: HizSiniri,
    pub rooms: Arc<Mutex<HashMap<String, RoomState>>>,
    pub base_dir: PathBuf,
    pub session_secret: String,
    pub default_room: String,
    pub ice_urls: Vec<String>,
    pub canonical_host: String,
    pub zs_state: RwLock<Option<Arc<zs::ZsState>>>,
}

fn env_str(k: &str) -> String {
    std::env::var(k).unwrap_or_default().trim().to_string()
}

pub fn client_ip(headers: &HeaderMap, addr: Option<SocketAddr>) -> String {
    let trust = env_str("UYAP_TRUST_PROXY") == "1" || !env_str("RENDER").is_empty();
    if trust {
        if let Some(cf) = headers.get("cf-connecting-ip").and_then(|v| v.to_str().ok()) {
            let t = cf.trim();
            if !t.is_empty() {
                return t.to_string();
            }
        }
        if let Some(xff) = headers.get("x-forwarded-for").and_then(|v| v.to_str().ok()) {
            if let Some(first) = xff.split(',').next() {
                let t = first.trim();
                if !t.is_empty() {
                    return t.to_string();
                }
            }
        }
    }
    addr.map(|a| a.ip().to_string()).unwrap_or_else(|| "unknown".to_string())
}

fn sign_session(secret: &str, kind: &str, payload: &str, ttl: i64) -> String {
    let exp = accounts::now_ts() + ttl;
    let raw = format!("{}|{}|{}", kind, payload, exp);
    let b64 = URL_SAFE_NO_PAD.encode(raw.as_bytes());
    let mut mac = HmacSha256::new_from_slice(secret.as_bytes()).unwrap();
    mac.update(b64.as_bytes());
    let sig = URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes());
    format!("{}.{}", b64, sig)
}

fn verify_session(secret: &str, kind: &str, tok: &str) -> Option<String> {
    let (b64, sig) = tok.split_once('.')?;
    let mut mac = HmacSha256::new_from_slice(secret.as_bytes()).unwrap();
    mac.update(b64.as_bytes());
    let expected = URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes());
    if sig.len() != expected.len()
        || sig.as_bytes().iter().zip(expected.as_bytes()).fold(0u8, |a, (x, y)| a | (x ^ y)) != 0
    {
        return None;
    }
    let decoded = String::from_utf8(URL_SAFE_NO_PAD.decode(b64).ok()?).ok()?;
    let parts: Vec<&str> = decoded.splitn(3, '|').collect();
    if parts.len() != 3 || parts[0] != kind {
        return None;
    }
    let exp: i64 = parts[2].parse().ok()?;
    if exp < accounts::now_ts() {
        return None;
    }
    Some(parts[1].to_string())
}

fn get_cookie(headers: &HeaderMap, name: &str) -> Option<String> {
    let raw = headers.get(header::COOKIE)?.to_str().ok()?;
    for p in raw.split(';') {
        let mut it = p.trim().splitn(2, '=');
        if it.next()?.trim() == name {
            return Some(it.next()?.trim().to_string());
        }
    }
    None
}

fn make_cookie(name: &str, val: &str, path: &str, max_age: i64) -> String {
    let secure = env_str("UYAP_HTTPS") == "1" || !env_str("RENDER").is_empty();
    let sec = if secure { "; Secure" } else { "" };
    format!(
        "{}={}; Path={}; HttpOnly; SameSite=Lax; Max-Age={}{}",
        name, val, path, max_age, sec
    )
}

fn current_owner(state: &AppState, headers: &HeaderMap) -> Option<String> {
    let tok = get_cookie(headers, OWNER_COOKIE)?;
    let uname = verify_session(&state.session_secret, "owner", &tok)?;
    let g = state.accounts.lock().ok()?;
    let rec = g.users.get(&uname)?;
    if rec["role"].as_str() == Some("owner") && rec["active"].as_bool().unwrap_or(true) {
        Some(uname)
    } else {
        None
    }
}

fn current_admin(state: &AppState, headers: &HeaderMap) -> Option<String> {
    let tok = get_cookie(headers, ADMIN_COOKIE)?;
    let uname = verify_session(&state.session_secret, "admin", &tok)?;
    let g = state.accounts.lock().ok()?;
    let rec = g.users.get(&uname)?;
    if rec["role"].as_str() == Some("owner") && rec["active"].as_bool().unwrap_or(true) {
        Some(uname)
    } else {
        None
    }
}

fn current_office_user(state: &AppState, headers: &HeaderMap) -> Option<(String, Value)> {
    let tok = get_cookie(headers, OFFICE_COOKIE)?;
    let uname = verify_session(&state.session_secret, "office", &tok)?;
    let g = state.accounts.lock().ok()?;
    let rec = g.users.get(&uname)?.clone();
    if rec["role"].as_str() == Some("owner") || !rec["active"].as_bool().unwrap_or(true) {
        return None;
    }
    Some((uname, rec))
}

fn json_resp(v: Value, status: StatusCode) -> Response {
    let mut r = (
        status,
        [(header::CONTENT_TYPE, "application/json; charset=utf-8")],
        serde_json::to_string(&v).unwrap_or_else(|_| "{}".to_string()),
    )
        .into_response();
    r.headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    r
}

fn escape_html(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

pub fn send_email(to_addr: &str, subject: &str, body_text: &str) -> Result<(), String> {
    let test_path = env_str("UYAP_MAIL_TEST_PATH");
    if !test_path.is_empty() {
        let mut line = serde_json::to_string(&json!({
            "to": to_addr,
            "subject": subject,
            "body": body_text,
            "ts": accounts::now_ts()
        }))
        .unwrap_or_default();
        line.push('\n');
        use std::io::Write;
        let mut f = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&test_path)
            .map_err(|e| e.to_string())?;
        f.write_all(line.as_bytes()).map_err(|e| e.to_string())?;
        return Ok(());
    }

    let host = env_str("SMTP_HOST");
    if host.is_empty() {
        println!("[MAIL DEV] -> {} | {} | {}", to_addr, subject, body_text);
        return Ok(());
    }
    let port: u16 = env_str("SMTP_PORT").parse().unwrap_or(587);
    let user = env_str("SMTP_USER");
    let pass = env_str("SMTP_PASS");
    let from_addr = {
        let f = env_str("SMTP_FROM");
        if f.is_empty() { user.clone() } else { f }
    };

    let email = MailMessage::builder()
        .from(from_addr.parse().map_err(|e| format!("SMTP from: {}", e))?)
        .to(to_addr.parse().map_err(|e| format!("SMTP to: {}", e))?)
        .subject(subject)
        .header(ContentType::TEXT_PLAIN)
        .body(body_text.to_string())
        .map_err(|e| e.to_string())?;

    let mut builder = SmtpTransport::starttls_relay(&host)
        .map_err(|e| e.to_string())?
        .port(port);
    if !user.is_empty() {
        builder = builder.credentials(Credentials::new(user, pass));
    }
    let mailer = builder.build();
    mailer.send(&email).map_err(|e| e.to_string())?;
    Ok(())
}

// ── Security Middleware ─────────────────────────────────────────────────────────────

async fn security_mw(
    State(state): State<Arc<AppState>>,
    req: Request<Body>,
    next: Next,
) -> Response {
    let path = req.uri().path().to_string();
    let host_hdr = req
        .headers()
        .get(header::HOST)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let host_no_port = host_hdr.split(':').next().unwrap_or("");

    if !state.canonical_host.is_empty()
        && !host_no_port.is_empty()
        && !host_no_port.eq_ignore_ascii_case(&state.canonical_host)
        && !matches!(host_no_port, "localhost" | "127.0.0.1" | "::1")
    {
        let pq = req
            .uri()
            .path_and_query()
            .map(|x| x.as_str())
            .unwrap_or("/");
        let target = format!("https://{}{}", state.canonical_host, pq);
        return Redirect::permanent(&target).into_response();
    }

    if let Some(sfs) = req.headers().get("sec-fetch-site").and_then(|v| v.to_str().ok()) {
        if sfs == "cross-site" && req.method() != Method::GET && req.method() != Method::HEAD {
            if !path.starts_with("/zs/telegram/hook") && !path.starts_with("/odeme/webhook") {
                return (StatusCode::FORBIDDEN, "Cross-site request blocked").into_response();
            }
        }
    }

    let mut resp = next.run(req).await;
    let h = resp.headers_mut();
    h.insert("Server", HeaderValue::from_static("cokluuyap"));
    h.insert("X-Content-Type-Options", HeaderValue::from_static("nosniff"));
    h.insert("X-Frame-Options", HeaderValue::from_static("DENY"));
    h.insert(
        "Referrer-Policy",
        HeaderValue::from_static("strict-origin-when-cross-origin"),
    );
    h.insert(
        "Permissions-Policy",
        HeaderValue::from_static("camera=(), microphone=(), geolocation=()"),
    );
    h.insert(
        "X-Robots-Tag",
        HeaderValue::from_static("noindex, nofollow"),
    );
    if env_str("UYAP_HTTPS") == "1" || !env_str("RENDER").is_empty() {
        h.insert(
            header::STRICT_TRANSPORT_SECURITY,
            HeaderValue::from_static("max-age=31536000; includeSubDomains"),
        );
    }
    resp
}

// ── WebSocket Signaling (/ws) ───────────────────────────────────────────────────────

fn mint_ticket(secret: &str, room: &str, office_name: &str, ttl: i64) -> String {
    let exp = accounts::now_ts() + ttl;
    let payload = json!({"room": room, "office": office_name, "exp": exp});
    let b64 = URL_SAFE_NO_PAD.encode(serde_json::to_vec(&payload).unwrap_or_default());
    let mut mac = HmacSha256::new_from_slice(secret.as_bytes()).unwrap();
    mac.update(b64.as_bytes());
    let sig = URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes());
    format!("{}.{}", b64, sig)
}

async fn ws_handler(
    State(state): State<Arc<AppState>>,
    ws: WebSocketUpgrade,
) -> Response {
    ws.on_upgrade(move |socket| handle_ws(state, socket))
}

async fn handle_ws(state: Arc<AppState>, socket: WebSocket) {
    let (mut sender, mut receiver) = socket.split();
    let (tx, mut rx) = mpsc::unbounded_channel::<String>();

    let write_task = tokio::spawn(async move {
        while let Some(msg) = rx.recv().await {
            if sender.send(Message::Text(msg)).await.is_err() {
                break;
            }
        }
    });

    let mut role = String::new();
    let mut room = String::new();
    let mut peer_id = String::new();

    while let Some(Ok(msg)) = receiver.next().await {
        let Message::Text(txt) = msg else { continue; };
        let Ok(v) = serde_json::from_str::<Value>(&txt) else { continue; };
        let typ = v["type"].as_str().unwrap_or("");

        if typ == "hello" {
            let r_role = v["role"].as_str().unwrap_or("");
            let r_room = v["room"].as_str().unwrap_or(&state.default_room).trim().to_string();
            let username = v["username"].as_str().unwrap_or("").trim().to_string();
            let password = v["password"].as_str().unwrap_or("").to_string();

            if r_role == "probe" {
                let mut rooms_g = state.rooms.lock().unwrap();
                let rs = rooms_g.entry(r_room.clone()).or_insert_with(RoomState::new);
                let _ = tx.send(
                    json!({
                        "type": "probe_result",
                        "room": r_room,
                        "office_online": rs.office.is_some(),
                        "homes": rs.homes.len(),
                    })
                    .to_string(),
                );
                break;
            }

            if !username.is_empty() {
                let auth = state
                    .accounts
                    .lock()
                    .ok()
                    .and_then(|mut g| {
                        let (valid, _, rec) = g.authenticate(&username, &password);
                        if valid { rec } else { None }
                    });
                let Some(rec) = auth else {
                    let _ = tx.send(json!({"type": "error", "code": "auth_failed", "message": "Kullanıcı adı veya parola hatalı."}).to_string());
                    break;
                };
                let oid = rec["office_id"].as_str().unwrap_or("");
                let off_room = state
                    .accounts
                    .lock()
                    .ok()
                    .and_then(|g| g.offices.get(oid).and_then(|o| o["room"].as_str().map(|s| s.to_string())))
                    .unwrap_or_else(|| r_room.clone());
                let off_label = state
                    .accounts
                    .lock()
                    .ok()
                    .and_then(|g| g.offices.get(oid).and_then(|o| o["label"].as_str().map(|s| s.to_string())))
                    .unwrap_or_default();

                role = r_role.to_string();
                room = off_room;

                let mut rooms_g = state.rooms.lock().unwrap();
                let rs = rooms_g.entry(room.clone()).or_insert_with(RoomState::new);
                rs.office_id = oid.to_string();
                rs.office_name = off_label.clone();

                if role == "office" {
                    rs.office = Some(tx.clone());
                    let ticket = mint_ticket(&rs.ticket_secret, &room, &off_label, 3600);
                    let _ = tx.send(json!({"type": "welcome", "role": "office", "room": room, "ticket": ticket, "office": off_label}).to_string());
                    for h in rs.homes.values() {
                        let _ = h.send(json!({"type": "office_online", "room": room}).to_string());
                    }
                } else {
                    let mut rnd = [0u8; 8];
                    rand::thread_rng().fill_bytes(&mut rnd);
                    peer_id = hex::encode(rnd);
                    rs.homes.insert(peer_id.clone(), tx.clone());
                    let ticket = mint_ticket(&rs.ticket_secret, &room, &off_label, 3600);
                    let _ = tx.send(json!({"type": "welcome", "role": "home", "peer_id": peer_id, "room": room, "office_online": rs.office.is_some(), "ticket": ticket}).to_string());
                    if let Some(ref off) = rs.office {
                        let _ = off.send(json!({"type": "peer_joined", "peer_id": peer_id}).to_string());
                    }
                }
            } else {
                role = r_role.to_string();
                room = r_room;
                let mut rooms_g = state.rooms.lock().unwrap();
                let rs = rooms_g.entry(room.clone()).or_insert_with(RoomState::new);
                if role == "office" {
                    rs.office = Some(tx.clone());
                    let _ = tx.send(json!({"type": "welcome", "role": "office", "room": room}).to_string());
                } else {
                    let mut rnd = [0u8; 8];
                    rand::thread_rng().fill_bytes(&mut rnd);
                    peer_id = hex::encode(rnd);
                    rs.homes.insert(peer_id.clone(), tx.clone());
                    let _ = tx.send(json!({"type": "welcome", "role": "home", "peer_id": peer_id, "room": room, "office_online": rs.office.is_some()}).to_string());
                }
            }
            continue;
        }

        if typ == "ping" {
            let _ = tx.send(json!({"type": "pong"}).to_string());
            continue;
        }

        // Relay offer / answer / candidate between office and home
        let rooms_g = state.rooms.lock().unwrap();
        if let Some(rs) = rooms_g.get(&room) {
            if role == "home" {
                if let Some(ref off) = rs.office {
                    let mut out = v.clone();
                    if let Some(obj) = out.as_object_mut() {
                        obj.insert("peer_id".to_string(), json!(peer_id));
                    }
                    let _ = off.send(out.to_string());
                }
            } else if role == "office" {
                let target = v["peer_id"].as_str().unwrap_or("");
                if let Some(h) = rs.homes.get(target) {
                    let _ = h.send(v.to_string());
                }
            }
        }
    }

    if !room.is_empty() {
        let mut rooms_g = state.rooms.lock().unwrap();
        if let Some(rs) = rooms_g.get_mut(&room) {
            if role == "office" {
                rs.office = None;
                for h in rs.homes.values() {
                    let _ = h.send(json!({"type": "office_offline", "room": room}).to_string());
                }
            } else if !peer_id.is_empty() {
                rs.homes.remove(&peer_id);
                if let Some(ref off) = rs.office {
                    let _ = off.send(json!({"type": "peer_left", "peer_id": peer_id}).to_string());
                }
            }
        }
    }
    drop(tx);
    let _ = write_task.await;
}

// ── Misc Public Routes ──────────────────────────────────────────────────────────────

async fn app_config_js(State(state): State<Arc<AppState>>) -> Response {
    let ice: Vec<Value> = state.ice_urls.iter().map(|u| json!({"urls": u})).collect();
    let js = format!(
        "window.__UYAP_APP__={{room:{},ice:{}}};",
        serde_json::to_string(&state.default_room).unwrap_or_else(|_| "\"\"".to_string()),
        serde_json::to_string(&ice).unwrap_or_else(|_| "[]".to_string())
    );
    (
        StatusCode::OK,
        [(header::CONTENT_TYPE, "application/javascript; charset=utf-8")],
        js,
    )
        .into_response()
}

async fn ice_get(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
) -> Response {
    let ip = client_ip(&headers, Some(addr));
    if !state.ice_limiter.allow(&ip) {
        return json_resp(json!({"ok": false, "error": "Çok sık istek."}), StatusCode::TOO_MANY_REQUESTS);
    }
    let ice: Vec<Value> = state.ice_urls.iter().map(|u| json!({"urls": u})).collect();
    json_resp(json!({"ok": true, "ice": ice}), StatusCode::OK)
}

async fn desktop_is_listesi_ac(
    State(state): State<Arc<AppState>>,
    Query(q): Query<HashMap<String, String>>,
) -> Response {
    let kod = q.get("kod").map(|s| s.as_str()).unwrap_or("");
    let did = q.get("dosya_id").map(|s| s.as_str()).unwrap_or("");
    let mut url = format!("{}/sso?kod={}", zs::NEW, urlencoding::encode(kod));
    if !did.is_empty() {
        url.push_str(&format!("&dosya_id={}", urlencoding::encode(did)));
    }
    Redirect::temporary(&url).into_response()
}

async fn robots_txt() -> Response {
    (
        StatusCode::OK,
        [(header::CONTENT_TYPE, "text/plain; charset=utf-8")],
        "User-agent: *\nDisallow: /\n",
    )
        .into_response()
}

async fn sitemap_xml() -> Response {
    (
        StatusCode::OK,
        [(header::CONTENT_TYPE, "application/xml; charset=utf-8")],
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?><urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\"></urlset>",
    )
        .into_response()
}

async fn serve_webapp(
    State(state): State<Arc<AppState>>,
    rel_path: &'static str,
    ctype: &'static str,
) -> Response {
    let full = state.base_dir.join("webapp").join(rel_path);
    match std::fs::read(&full) {
        Ok(bytes) => (StatusCode::OK, [(header::CONTENT_TYPE, ctype)], bytes).into_response(),
        Err(_) => (StatusCode::NOT_FOUND, "Bulunamadı").into_response(),
    }
}

// ── Owner Login / Logout ────────────────────────────────────────────────────────────

async fn owner_login_get(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    if current_owner(&state, &headers).is_some() {
        return Redirect::to("/admin").into_response();
    }
    let page = r#"<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Owner Giriş — CokluUyap</title>
<style>body{font-family:system-ui,sans-serif;background:#f3f4f6;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.card{background:#fff;padding:28px;border-radius:12px;box-shadow:0 4px 20px rgba(0,0,0,.08);width:340px}
input,button{width:100%;padding:10px;margin:8px 0;border:1px solid #d1d5db;border-radius:8px;box-sizing:border-box}
button{background:#2563eb;color:#fff;border:none;font-weight:600;cursor:pointer}</style></head>
<body><div class="card"><h2>Yönetici Girişi</h2>
<form method="post" action="/owner/login">
<input name="username" placeholder="Kullanıcı adı" required autocomplete="username">
<input name="password" type="password" placeholder="Parola" required autocomplete="current-password">
<input name="totp" placeholder="2FA Kodu (varsa)" inputmode="numeric" autocomplete="one-time-code">
<button type="submit">Giriş Yap</button>
</form></div></body></html>"#;
    Html(page).into_response()
}

async fn owner_login_post(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    body: String,
) -> Response {
    let ip = client_ip(&headers, Some(addr));
    let form: HashMap<String, String> = url::form_urlencoded::parse(body.as_bytes())
        .into_owned()
        .collect();
    let username = form.get("username").map(|s| s.trim()).unwrap_or("");
    let password = form.get("password").map(|s| s.as_str()).unwrap_or("");
    let totp = form.get("totp").map(|s| s.trim()).unwrap_or("");

    let wait = state.guard.blocked_for(&ip, username);
    if wait > 0 {
        return (
            StatusCode::TOO_MANY_REQUESTS,
            format!("Çok fazla hatalı deneme. {} sn bekleyin.", wait),
        )
            .into_response();
    }

    let ok = state
        .accounts
        .lock()
        .ok()
        .and_then(|mut g| {
            let (valid, _, rec) = g.authenticate(username, password);
            if valid { rec } else { None }
        });
    let Some(rec) = ok else {
        state.guard.fail(&ip, username);
        return (StatusCode::UNAUTHORIZED, "Kullanıcı adı veya parola hatalı.").into_response();
    };
    if rec["role"].as_str() != Some("owner") {
        state.guard.fail(&ip, username);
        return (StatusCode::FORBIDDEN, "Owner yetkisi gerekli.").into_response();
    }
    if let Some(sec) = rec["totp_secret"].as_str().filter(|s| !s.is_empty()) {
        if !totp_verify(sec, totp, 0).0 {
            state.guard.fail(&ip, username);
            return (StatusCode::UNAUTHORIZED, "2FA kodu hatalı.").into_response();
        }
    }
    state.guard.ok(&ip, username);
    let uname = rec["username"].as_str().unwrap_or(username);
    let tok = sign_session(&state.session_secret, "owner", uname, 43200);
    let adm_tok = sign_session(&state.session_secret, "admin", uname, 43200);
    let mut resp = Redirect::to("/admin").into_response();
    if let Ok(hv) = HeaderValue::from_str(&make_cookie(OWNER_COOKIE, &tok, "/", 43200)) {
        resp.headers_mut().append(header::SET_COOKIE, hv);
    }
    if let Ok(hv) = HeaderValue::from_str(&make_cookie(ADMIN_COOKIE, &adm_tok, "/", 43200)) {
        resp.headers_mut().append(header::SET_COOKIE, hv);
    }
    resp
}

async fn owner_logout() -> Response {
    let mut resp = Redirect::to("/owner/login").into_response();
    if let Ok(hv) = HeaderValue::from_str(&make_cookie(OWNER_COOKIE, "", "/", 0)) {
        resp.headers_mut().append(header::SET_COOKIE, hv);
    }
    if let Ok(hv) = HeaderValue::from_str(&make_cookie(ADMIN_COOKIE, "", "/", 0)) {
        resp.headers_mut().append(header::SET_COOKIE, hv);
    }
    resp
}

// ── Registration (/uye-ol) & Downloads (/indir) ─────────────────────────────────────

async fn uye_ol_get(State(state): State<Arc<AppState>>) -> Response {
    let full = state.base_dir.join("webapp").join("uye_ol.html");
    if let Ok(bytes) = std::fs::read(&full) {
        return (StatusCode::OK, [(header::CONTENT_TYPE, "text/html; charset=utf-8")], bytes)
            .into_response();
    }
    let page = r#"<!doctype html><html lang="tr"><head><meta charset="utf-8"><title>Üye Ol — CokluUyap</title></head>
<body><h2>Yeni Büro Kaydı</h2>
<form method="post" action="/uye-ol">
<input name="office_name" placeholder="Büro Adı" required><br>
<input name="display_name" placeholder="Ad Soyad" required><br>
<input name="username" placeholder="Kullanıcı adı" required><br>
<input name="email" type="email" placeholder="E-posta" required><br>
<input name="password" type="password" placeholder="Parola" required><br>
<button type="submit">Kayıt Ol</button>
</form></body></html>"#;
    Html(page).into_response()
}

async fn uye_ol_post(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    body: Bytes,
) -> Response {
    let ip = client_ip(&headers, Some(addr));
    if !state.reg_limiter.allow(&ip) {
        return json_resp(
            json!({"ok": false, "error": "Çok sık kayıt denemesi. Lütfen bekleyin."}),
            StatusCode::TOO_MANY_REQUESTS,
        );
    }
    let ct = headers
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let data: Value = if ct.contains("application/json") {
        serde_json::from_slice(&body).unwrap_or_else(|_| json!({}))
    } else {
        let form: HashMap<String, String> = url::form_urlencoded::parse(&body)
            .into_owned()
            .collect();
        json!(form)
    };
    let office_name = data["office_name"].as_str().unwrap_or("").trim();
    let display_name = data["display_name"].as_str().unwrap_or("").trim();
    let username = data["username"].as_str().unwrap_or("").trim();
    let email = data["email"].as_str().unwrap_or("").trim();
    let password = data["password"].as_str().unwrap_or("");
    if office_name.is_empty() || username.is_empty() || password.is_empty() {
        return json_resp(
            json!({"ok": false, "error": "Büro adı, kullanıcı adı ve parola zorunlu."}),
            StatusCode::BAD_REQUEST,
        );
    }
    if let Some(err) = password_policy_error(password) {
        return json_resp(json!({"ok": false, "error": err}), StatusCode::BAD_REQUEST);
    }
    let mut g = state.accounts.lock().unwrap();
    let office_val = match g.create_office(
        office_name,
        username,
        Some(password),
        None,
        email,
        false,
        None,
        None,
        "",
    ) {
        Ok(o) => o,
        Err(e) => return json_resp(json!({"ok": false, "error": e.0}), StatusCode::BAD_REQUEST),
    };
    let oid = office_val["office_id"].as_str().unwrap_or("").to_string();
    let full_uname = office_val["master_username"].as_str().unwrap_or(username).to_string();
    if let Some(zs_st) = state.zs_state.read().ok().and_then(|z| z.clone()) {
        if let Some(store) = zs_st.store.read().ok().and_then(|s| s.clone()) {
            if let Some(rec) = g.users.get(&full_uname).cloned() {
                let mut p = rec;
                p.as_object_mut().unwrap().insert("username".to_string(), json!(full_uname));
                let _ = store.sync_account(&p);
            }
        }
    }
    json_resp(
        json!({"ok": true, "username": full_uname, "office_id": oid}),
        StatusCode::OK,
    )
}

async fn indir_get(State(state): State<Arc<AppState>>) -> Response {
    let full = state.base_dir.join("webapp").join("indir.html");
    if let Ok(bytes) = std::fs::read(&full) {
        return (StatusCode::OK, [(header::CONTENT_TYPE, "text/html; charset=utf-8")], bytes)
            .into_response();
    }
    Html("<!doctype html><html lang='tr'><head><meta charset='utf-8'><title>İndir — CokluUyap</title></head><body><h2>CokluUyap İndirme</h2><a href='/indir/dosya'>Kurulum Dosyasını İndir</a></body></html>").into_response()
}

async fn indir_dosya(
    State(state): State<Arc<AppState>>,
    Query(q): Query<HashMap<String, String>>,
) -> Response {
    let name = q.get("dosya").map(|s| s.as_str()).unwrap_or("CokluUyap-Kur.exe");
    let safe = Path::new(name)
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("CokluUyap-Kur.exe");
    let dist_dir = state.base_dir.join("dist");
    let path = dist_dir.join(safe);
    match std::fs::read(&path) {
        Ok(bytes) => {
            let disp = format!("attachment; filename=\"{}\"", safe);
            let mut resp = (
                StatusCode::OK,
                [(header::CONTENT_TYPE, "application/octet-stream")],
                bytes,
            )
                .into_response();
            if let Ok(hv) = HeaderValue::from_str(&disp) {
                resp.headers_mut().insert(header::CONTENT_DISPOSITION, hv);
            }
            resp
        }
        Err(_) => (StatusCode::NOT_FOUND, "Dosya bulunamadı.").into_response(),
    }
}

async fn indir_light() -> Response {
    Redirect::to("/indir").into_response()
}

// ── Admin Routes (/admin*) ──────────────────────────────────────────────────────────

async fn admin_get(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    if current_admin(&state, &headers).is_none() {
        let full = state.base_dir.join("webapp").join("admin_login.html");
        if let Ok(bytes) = std::fs::read(&full) {
            return (StatusCode::OK, [(header::CONTENT_TYPE, "text/html; charset=utf-8")], bytes)
                .into_response();
        }
        return Redirect::to("/owner/login").into_response();
    }
    let full = state.base_dir.join("webapp").join("admin.html");
    match std::fs::read(&full) {
        Ok(bytes) => (StatusCode::OK, [(header::CONTENT_TYPE, "text/html; charset=utf-8")], bytes)
            .into_response(),
        Err(_) => Html("<h2>Admin Panel</h2>").into_response(),
    }
}

async fn admin_login_post(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    body: Bytes,
) -> Response {
    let ip = client_ip(&headers, Some(addr));
    let v: Value = serde_json::from_slice(&body).unwrap_or_else(|_| json!({}));
    let username = v["username"].as_str().unwrap_or("").trim();
    let password = v["password"].as_str().unwrap_or("");
    let totp = v["totp"].as_str().unwrap_or("").trim();
    let wait = state.guard.blocked_for(&ip, username);
    if wait > 0 {
        return json_resp(
            json!({"ok": false, "error": format!("Çok fazla hatalı deneme. {} sn bekleyin.", wait)}),
            StatusCode::TOO_MANY_REQUESTS,
        );
    }
    let ok = state
        .accounts
        .lock()
        .ok()
        .and_then(|mut g| {
            let (valid, _, rec) = g.authenticate(username, password);
            if valid { rec } else { None }
        });
    let Some(rec) = ok else {
        state.guard.fail(&ip, username);
        return json_resp(
            json!({"ok": false, "error": "Kullanıcı adı veya parola hatalı."}),
            StatusCode::UNAUTHORIZED,
        );
    };
    if rec["role"].as_str() != Some("owner") {
        state.guard.fail(&ip, username);
        return json_resp(
            json!({"ok": false, "error": "Yetkiniz yok."}),
            StatusCode::FORBIDDEN,
        );
    }
    if let Some(sec) = rec["totp_secret"].as_str().filter(|s| !s.is_empty()) {
        if !totp_verify(sec, totp, 0).0 {
            state.guard.fail(&ip, username);
            return json_resp(
                json!({"ok": false, "error": "2FA kodu hatalı."}),
                StatusCode::UNAUTHORIZED,
            );
        }
    }
    state.guard.ok(&ip, username);
    let uname = rec["username"].as_str().unwrap_or(username);
    let tok = sign_session(&state.session_secret, "admin", uname, 43200);
    let mut resp = json_resp(json!({"ok": true, "username": uname}), StatusCode::OK);
    if let Ok(hv) = HeaderValue::from_str(&make_cookie(ADMIN_COOKIE, &tok, "/", 43200)) {
        resp.headers_mut().append(header::SET_COOKIE, hv);
    }
    resp
}

async fn admin_logout() -> Response {
    let mut resp = json_resp(json!({"ok": true}), StatusCode::OK);
    if let Ok(hv) = HeaderValue::from_str(&make_cookie(ADMIN_COOKIE, "", "/", 0)) {
        resp.headers_mut().append(header::SET_COOKIE, hv);
    }
    resp
}

async fn admin_api_state(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    if current_admin(&state, &headers).is_none() {
        return json_resp(json!({"ok": false, "error": "Oturum gerekli."}), StatusCode::UNAUTHORIZED);
    }
    let g = state.accounts.lock().unwrap();
    let offices = g.listing_offices();
    let users: Vec<Value> = g.users.values().cloned().collect();
    let orders = g.listing_orders();
    json_resp(
        json!({"ok": true, "offices": offices, "users": users, "orders": orders}),
        StatusCode::OK,
    )
}

async fn admin_api_office_create(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if current_admin(&state, &headers).is_none() {
        return json_resp(json!({"ok": false, "error": "Oturum gerekli."}), StatusCode::UNAUTHORIZED);
    }
    let v: Value = serde_json::from_slice(&body).unwrap_or_else(|_| json!({}));
    let label = v["label"].as_str().unwrap_or("");
    let master_u = v["master_username"].as_str().unwrap_or("admin");
    let master_p = v["master_password"].as_str();
    let room = v["room"].as_str();
    let mut g = state.accounts.lock().unwrap();
    match g.create_office(label, master_u, master_p, room, "", false, None, None, "") {
        Ok(o) => json_resp(json!({"ok": true, "office": o}), StatusCode::OK),
        Err(e) => json_resp(json!({"ok": false, "error": e.0}), StatusCode::BAD_REQUEST),
    }
}

async fn admin_api_user_create(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if current_admin(&state, &headers).is_none() {
        return json_resp(json!({"ok": false, "error": "Oturum gerekli."}), StatusCode::UNAUTHORIZED);
    }
    let v: Value = serde_json::from_slice(&body).unwrap_or_else(|_| json!({}));
    let mut g = state.accounts.lock().unwrap();
    match g.create_user(
        v["office_id"].as_str().unwrap_or(""),
        v["username"].as_str().unwrap_or(""),
        v["password"].as_str(),
        v["role"].as_str().unwrap_or("member"),
        v["label"].as_str().or_else(|| v["display_name"].as_str()).unwrap_or(""),
        v["email"].as_str().unwrap_or(""),
        v["phone"].as_str().unwrap_or(""),
    ) {
        Ok(u) => json_resp(json!({"ok": true, "user": u}), StatusCode::OK),
        Err(e) => json_resp(json!({"ok": false, "error": e.0}), StatusCode::BAD_REQUEST),
    }
}

async fn admin_api_user_active(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if current_admin(&state, &headers).is_none() {
        return json_resp(json!({"ok": false, "error": "Oturum gerekli."}), StatusCode::UNAUTHORIZED);
    }
    let v: Value = serde_json::from_slice(&body).unwrap_or_else(|_| json!({}));
    let uname = v["username"].as_str().unwrap_or("");
    let active = v["active"].as_bool().unwrap_or(true);
    let mut g = state.accounts.lock().unwrap();
    match g.set_user_active(uname, active) {
        Ok(_) => json_resp(json!({"ok": true}), StatusCode::OK),
        Err(e) => json_resp(json!({"ok": false, "error": e.0}), StatusCode::BAD_REQUEST),
    }
}

// ── Office Portal (/ofis* & /api/office) ────────────────────────────────────────────

async fn api_office_get(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    let Some((uname, rec)) = current_office_user(&state, &headers) else {
        return json_resp(json!({"ok": false, "error": "Oturum gerekli."}), StatusCode::UNAUTHORIZED);
    };
    let oid = rec["office_id"].as_str().unwrap_or("");
    let g = state.accounts.lock().unwrap();
    let office = g.offices.get(oid).cloned().unwrap_or(Value::Null);
    let members = if rec["office_role"].as_str() == Some("manager") {
        g.listing_users(oid)
    } else {
        Vec::new()
    };
    json_resp(
        json!({"ok": true, "username": uname, "user": rec, "office": office, "members": members}),
        StatusCode::OK,
    )
}

async fn ofis_get(State(state): State<Arc<AppState>>, headers: HeaderMap) -> Response {
    if current_office_user(&state, &headers).is_none() {
        let full = state.base_dir.join("webapp").join("ofis_login.html");
        if let Ok(bytes) = std::fs::read(&full) {
            return (StatusCode::OK, [(header::CONTENT_TYPE, "text/html; charset=utf-8")], bytes)
                .into_response();
        }
        return Html("<h2>Büro Girişi</h2>").into_response();
    }
    let full = state.base_dir.join("webapp").join("ofis.html");
    match std::fs::read(&full) {
        Ok(bytes) => (StatusCode::OK, [(header::CONTENT_TYPE, "text/html; charset=utf-8")], bytes)
            .into_response(),
        Err(_) => Html("<h2>Büro Paneli</h2>").into_response(),
    }
}

async fn ofis_login_post(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    body: Bytes,
) -> Response {
    let ip = client_ip(&headers, Some(addr));
    let v: Value = serde_json::from_slice(&body).unwrap_or_else(|_| json!({}));
    let username = v["username"].as_str().unwrap_or("").trim();
    let password = v["password"].as_str().unwrap_or("");
    let wait = state.guard.blocked_for(&ip, username);
    if wait > 0 {
        return json_resp(
            json!({"ok": false, "error": format!("Çok fazla hatalı deneme. {} sn bekleyin.", wait)}),
            StatusCode::TOO_MANY_REQUESTS,
        );
    }
    let ok = state
        .accounts
        .lock()
        .ok()
        .and_then(|mut g| {
            let (valid, _, rec) = g.authenticate(username, password);
            if valid { rec } else { None }
        });
    let Some(rec) = ok else {
        state.guard.fail(&ip, username);
        return json_resp(
            json!({"ok": false, "error": "Kullanıcı adı veya parola hatalı."}),
            StatusCode::UNAUTHORIZED,
        );
    };
    if rec["role"].as_str() == Some("owner") {
        return json_resp(
            json!({"ok": false, "error": "Owner hesabı ile buradan girilemez."}),
            StatusCode::FORBIDDEN,
        );
    }
    state.guard.ok(&ip, username);
    let uname = rec["username"].as_str().unwrap_or(username);
    let tok = sign_session(&state.session_secret, "office", uname, 43200);
    let mut resp = json_resp(json!({"ok": true, "username": uname}), StatusCode::OK);
    if let Ok(hv) = HeaderValue::from_str(&make_cookie(OFFICE_COOKIE, &tok, "/", 43200)) {
        resp.headers_mut().append(header::SET_COOKIE, hv);
    }
    resp
}

async fn ofis_logout() -> Response {
    let mut resp = json_resp(json!({"ok": true}), StatusCode::OK);
    if let Ok(hv) = HeaderValue::from_str(&make_cookie(OFFICE_COOKIE, "", "/", 0)) {
        resp.headers_mut().append(header::SET_COOKIE, hv);
    }
    resp
}

// ── Password Reset (/reset, /api/reset) ─────────────────────────────────────────────

async fn reset_get(State(state): State<Arc<AppState>>) -> Response {
    let full = state.base_dir.join("webapp").join("reset.html");
    match std::fs::read(&full) {
        Ok(bytes) => (StatusCode::OK, [(header::CONTENT_TYPE, "text/html; charset=utf-8")], bytes)
            .into_response(),
        Err(_) => Html("<h2>Parola Sıfırlama</h2>").into_response(),
    }
}

async fn api_reset_post(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    ConnectInfo(addr): ConnectInfo<SocketAddr>,
    body: Bytes,
) -> Response {
    let ip = client_ip(&headers, Some(addr));
    if !state.reset_limiter.allow(&ip) {
        return json_resp(
            json!({"ok": false, "error": "Çok sık deneme. Lütfen bekleyin."}),
            StatusCode::TOO_MANY_REQUESTS,
        );
    }
    let v: Value = serde_json::from_slice(&body).unwrap_or_else(|_| json!({}));
    let token = v["token"].as_str().unwrap_or("").trim();
    if !token.is_empty() {
        let new_pw = v["password"].as_str().unwrap_or("");
        let mut g = state.accounts.lock().unwrap();
        let (ok, msg) = g.reset_password_with_token(token, new_pw);
        return if ok {
            json_resp(json!({"ok": true, "message": msg}), StatusCode::OK)
        } else {
            json_resp(json!({"ok": false, "error": msg}), StatusCode::BAD_REQUEST)
        };
    }
    let ident = v["username"].as_str().or_else(|| v["email"].as_str()).unwrap_or("").trim();
    if ident.is_empty() {
        return json_resp(
            json!({"ok": false, "error": "Kullanıcı adı veya e-posta gerekli."}),
            StatusCode::BAD_REQUEST,
        );
    }
    let mut g = state.accounts.lock().unwrap();
    let (tok_opt, _) = g.request_password_reset(ident);
    if let Some(info) = tok_opt {
        let tok = info["token"].as_str().unwrap_or("");
        let email = info["email"].as_str().unwrap_or("");
        let uname = info["username"].as_str().unwrap_or("");
        if !tok.is_empty() && !email.is_empty() {
            let link = format!("/reset?token={}", urlencoding::encode(tok));
            let _ = send_email(
                email,
                "CokluUyap Parola Sıfırlama",
                &format!("Merhaba {},\n\nParolanızı sıfırlamak için bağlantı: {}\n(30 dakika geçerlidir.)", uname, link),
            );
        }
    }
    json_resp(
        json!({"ok": true, "message": "Eğer kayıtlı e-posta varsa sıfırlama bağlantısı gönderildi."}),
        StatusCode::OK,
    )
}

// ── Payment (/satin-al, /odeme/webhook) ─────────────────────────────────────────────

async fn satin_al_get(State(state): State<Arc<AppState>>) -> Response {
    let full = state.base_dir.join("webapp").join("satin_al.html");
    match std::fs::read(&full) {
        Ok(bytes) => (StatusCode::OK, [(header::CONTENT_TYPE, "text/html; charset=utf-8")], bytes)
            .into_response(),
        Err(_) => Html("<h2>Satın Al</h2>").into_response(),
    }
}

async fn odeme_webhook(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let secret = env_str("PAYTR_MERCHANT_KEY");
    let v: Value = serde_json::from_slice(&body).unwrap_or_else(|_| json!({}));
    if !secret.is_empty() {
        let sig = headers
            .get("x-webhook-signature")
            .and_then(|x| x.to_str().ok())
            .unwrap_or("");
        let mut mac = HmacSha256::new_from_slice(secret.as_bytes()).unwrap();
        mac.update(&body);
        let expected = hex::encode(mac.finalize().into_bytes());
        if sig != expected {
            return StatusCode::FORBIDDEN.into_response();
        }
    }
    let oid = v["order_id"].as_str().unwrap_or("");
    let status = v["status"].as_str().unwrap_or("");
    if !oid.is_empty() && status == "paid" {
        let mut g = state.accounts.lock().unwrap();
        let _ = g.update_order(oid, vec![("status", json!("paid")), ("paid_at", json!(accounts::now_ts()))]);
    }
    json_resp(json!({"ok": true}), StatusCode::OK)
}

// ── App Builder & Main ──────────────────────────────────────────────────────────────

pub fn build_app(
    accounts: Arc<Mutex<AccountStore>>,
    base_dir: PathBuf,
    default_room: String,
    ice_urls: Vec<String>,
) -> (Router, Arc<AppState>) {
    let session_secret = {
        let s = env_str("UYAP_SESSION_SECRET");
        if s.is_empty() {
            let mut rnd = [0u8; 32];
            rand::thread_rng().fill_bytes(&mut rnd);
            URL_SAFE_NO_PAD.encode(rnd)
        } else {
            s
        }
    };
    let canonical_host = env_str("UYAP_CANONICAL_HOST");

    let state = Arc::new(AppState {
        accounts: accounts.clone(),
        guard: Arc::new(LoginGuard::default()),
        ice_limiter: HizSiniri::new(30, 60.0),
        reset_limiter: HizSiniri::new(5, 300.0),
        reg_limiter: HizSiniri::new(5, 300.0),
        pay_limiter: HizSiniri::new(10, 60.0),
        rooms: Arc::new(Mutex::new(HashMap::new())),
        base_dir: base_dir.clone(),
        session_secret: session_secret.clone(),
        default_room,
        ice_urls,
        canonical_host,
        zs_state: RwLock::new(None),
    });

    let client_ip_fn: zs::ClientIpFn = Arc::new(|h, addr| client_ip(h, addr));
    let st_owner = state.clone();
    let owner_fn: zs::OwnerUserFn = Arc::new(move |h| current_owner(&st_owner, h));

    let mut router = Router::new()
        .route("/ws", get(ws_handler))
        .route("/__app__/config.js", get(app_config_js))
        .route("/ice", get(ice_get))
        .route("/__uyap_kisi__/is-listesi/ac", get(desktop_is_listesi_ac))
        .route("/robots.txt", get(robots_txt))
        .route("/sitemap.xml", get(sitemap_xml))
        .route("/owner/login", get(owner_login_get).post(owner_login_post))
        .route("/owner/logout", post(owner_logout))
        .route("/uye-ol", get(uye_ol_get).post(uye_ol_post))
        .route("/indir", get(indir_get))
        .route("/indir/dosya", get(indir_dosya))
        .route("/indir/light", get(indir_light))
        .route("/admin", get(admin_get))
        .route("/admin/login", post(admin_login_post))
        .route("/admin/logout", post(admin_logout))
        .route("/admin/api/state", get(admin_api_state))
        .route("/admin/api/offices", post(admin_api_office_create))
        .route("/admin/api/users", post(admin_api_user_create))
        .route("/admin/api/users/active", post(admin_api_user_active))
        .route("/api/office", get(api_office_get))
        .route("/ofis", get(ofis_get))
        .route("/ofis/login", post(ofis_login_post))
        .route("/ofis/logout", post(ofis_logout))
        .route("/reset", get(reset_get))
        .route("/api/reset", post(api_reset_post))
        .route("/satin-al", get(satin_al_get))
        .route("/odeme/webhook", post(odeme_webhook))
        .route("/", get({
            let st = state.clone();
            move || serve_webapp(State(st), "index.html", "text/html; charset=utf-8")
        }))
        .route("/app.js", get({
            let st = state.clone();
            move || serve_webapp(State(st), "app.js", "application/javascript; charset=utf-8")
        }))
        .route("/style.css", get({
            let st = state.clone();
            move || serve_webapp(State(st), "style.css", "text/css; charset=utf-8")
        }));

    let router = router.with_state(state.clone());
    let (mut router, zs_st) = zs::register(
        router,
        &session_secret,
        state.guard.clone(),
        client_ip_fn,
        Some(accounts),
        Some(owner_fn),
        base_dir,
    );
    if let Ok(mut g) = state.zs_state.write() {
        *g = Some(zs_st);
    }

    let app = router
        .layer(middleware::from_fn_with_state(state.clone(), security_mw));
    (app, state)
}

pub fn main() {
    let args: Vec<String> = std::env::args().collect();
    let mut host = env_str("HOST");
    if host.is_empty() {
        host = "0.0.0.0".to_string();
    }
    let mut port: u16 = env_str("PORT").parse().unwrap_or(8765);
    let mut default_room = env_str("UYAP_ROOM");
    if default_room.is_empty() {
        default_room = "varsayilan".to_string();
    }
    let mut ice_urls: Vec<String> = DEFAULT_ICE.iter().map(|s| s.to_string()).collect();
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--host" => {
                i += 1;
                if let Some(v) = args.get(i) {
                    host = v.clone();
                }
            }
            "--port" => {
                i += 1;
                if let Some(v) = args.get(i) {
                    port = v.parse().unwrap_or(port);
                }
            }
            "--room" => {
                i += 1;
                if let Some(v) = args.get(i) {
                    default_room = v.clone();
                }
            }
            "--ice" => {
                i += 1;
                if let Some(v) = args.get(i) {
                    ice_urls = v.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect();
                }
            }
            _ => {}
        }
        i += 1;
    }

    let base_dir = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let accounts_store = Arc::new(Mutex::new(AccountStore::new(None, None)));

    let rt = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .expect("Tokio runtime");

    rt.block_on(async move {
        let (app, state) = build_app(accounts_store, base_dir, default_room, ice_urls);

        // Optional room rotation task
        let rot_sec: u64 = env_str("UYAP_ROTATE_INTERVAL").parse().unwrap_or(0);
        if rot_sec > 0 {
            let st_rot = state.clone();
            tokio::spawn(async move {
                loop {
                    tokio::time::sleep(Duration::from_secs(rot_sec)).await;
                    let mut g = st_rot.accounts.lock().unwrap();
                    let ids: Vec<String> = g.offices.keys().cloned().collect();
                    for oid in ids {
                        let _ = g.rotate_room_key(&oid);
                    }
                }
            });
        }

        let addr: SocketAddr = format!("{}:{}", host, port)
            .parse()
            .expect("Invalid bind address");
        println!("[+] CokluUyap sunucusu dinliyor: http://{}", addr);
        let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
        axum::serve(
            listener,
            app.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .await
        .expect("serve");
    });
}
