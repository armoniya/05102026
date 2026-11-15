//! UYAP P2P Buluşturma (Signaling) Sunucusu (`signaling_server.py` birebir Rust portu)
//! --------------------------------------------------------------------------------------
//! Görevi yalnızca iki tarafı aynı "oda anahtarı" (room key / lisans) üzerinden buluşturup
//! WebRTC el sıkışmasını (SDP offer/answer) aktarmaktır. UYAP trafiği buradan GEÇMEZ.

use std::collections::{HashMap, HashSet};
use std::fs::File;
use std::io::BufReader;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;

use clap::Parser;
use futures_util::{SinkExt, StreamExt};
use once_cell::sync::Lazy;
use parking_lot::Mutex as SyncMutex;
use rand::RngCore;
use serde_json::{json, Map, Value};
use tokio::io::{AsyncRead, AsyncWrite};
use tokio::net::TcpListener;
use tokio::sync::{mpsc, Mutex as AsyncMutex};
use tokio_rustls::TlsAcceptor;
use tokio_tungstenite::tungstenite::protocol::WebSocketConfig;
use tokio_tungstenite::tungstenite::Message as WsMessage;

use crate::accounts::AccountStore;

pub static CONFIG_PATH: Lazy<PathBuf> = Lazy::new(|| PathBuf::from("signaling_config.json"));

/// Hesap deposu (vendor_server ile aynı). Doluysa oda+parola doğrulanır; boşsa eski davranış.
pub static STORE: Lazy<SyncMutex<AccountStore>> = Lazy::new(|| SyncMutex::new(AccountStore::new(None, None)));

#[derive(Debug, Clone)]
struct PeerConn {
    conn_id: u64,
    tx: mpsc::UnboundedSender<WsMessage>,
}

#[derive(Debug, Default)]
struct RoomSlot {
    office: Option<PeerConn>,
    office_user: String,
    office_meta: Option<Map<String, Value>>,
    homes: HashMap<String, PeerConn>,
}

static ROOMS: Lazy<AsyncMutex<HashMap<String, RoomSlot>>> =
    Lazy::new(|| AsyncMutex::new(HashMap::new()));
static ALLOWED: Lazy<SyncMutex<Option<HashSet<String>>>> = Lazy::new(|| SyncMutex::new(None));
static OPEN_MODE: AtomicBool = AtomicBool::new(false);
static NEXT_CONN_ID: AtomicU64 = AtomicU64::new(1);

fn gen_hex_32() -> String {
    let mut b = [0u8; 16];
    rand::thread_rng().fill_bytes(&mut b);
    hex::encode(b)
}

pub fn _env_open_mode() -> bool {
    let v = std::env::var("UYAP_SIGNALING_OPEN").unwrap_or_default();
    matches!(v.trim().to_lowercase().as_str(), "1" | "true" | "yes" | "on")
}

pub fn load_allowed(path: &Path) {
    let mut allowed_guard = ALLOWED.lock();
    if path.exists() {
        match std::fs::read_to_string(path) {
            Ok(text) => match serde_json::from_str::<Value>(&text) {
                Ok(cfg) => {
                    let rooms: HashSet<String> = cfg
                        .get("allowed_rooms")
                        .and_then(|v| v.as_array())
                        .map(|arr| {
                            arr.iter()
                                .filter_map(|x| x.as_str().map(|s| s.to_string()))
                                .collect()
                        })
                        .unwrap_or_default();
                    *allowed_guard = if rooms.is_empty() { None } else { Some(rooms) };
                }
                Err(e) => {
                    println!("[!] signaling_config.json okunamadı ({e}); tüm odalar serbest.");
                    *allowed_guard = None;
                }
            },
            Err(e) => {
                println!("[!] signaling_config.json okunamadı ({e}); tüm odalar serbest.");
                *allowed_guard = None;
            }
        }
    } else {
        *allowed_guard = None;
    }

    match &*allowed_guard {
        None => println!("[*] Oda allowlist'i yok: ortak anahtarı bilen her çift buluşabilir."),
        Some(set) => println!("[*] {} oda anahtarı izinli (allowlist aktif).", set.len()),
    }
}

fn _safe_send(tx: &mpsc::UnboundedSender<WsMessage>, payload: &Value) {
    if let Ok(s) = serde_json::to_string(payload) {
        let _ = tx.send(WsMessage::Text(s));
    }
}

fn _safe_send_raw(tx: &mpsc::UnboundedSender<WsMessage>, text: String) {
    let _ = tx.send(WsMessage::Text(text));
}

async fn handle_ws<S>(stream: S)
where
    S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
{
    let cfg = WebSocketConfig {
        max_message_size: Some(4 * 1024 * 1024),
        max_frame_size: Some(4 * 1024 * 1024),
        ..Default::default()
    };
    let ws_stream = match tokio_tungstenite::accept_async_with_config(stream, Some(cfg)).await {
        Ok(ws) => ws,
        Err(_) => return,
    };

    let (mut ws_sink, mut ws_source) = ws_stream.split();
    let (tx, mut rx) = mpsc::unbounded_channel::<WsMessage>();

    let writer_task = tokio::spawn(async move {
        while let Some(msg) = rx.recv().await {
            let is_close = matches!(msg, WsMessage::Close(_));
            if ws_sink.send(msg).await.is_err() {
                break;
            }
            if is_close {
                break;
            }
        }
    });

    let conn_id = NEXT_CONN_ID.fetch_add(1, Ordering::Relaxed);
    let mut role = String::new();
    let mut room = String::new();
    let mut cid = String::new();
    let mut rk = String::new();
    let mut joined_room = false;

    'session: {
        let first_msg = match ws_source.next().await {
            Some(Ok(WsMessage::Text(t))) => t,
            _ => break 'session,
        };
        let join: Value = match serde_json::from_str(&first_msg) {
            Ok(v) => v,
            Err(_) => break 'session,
        };

        role = join
            .get("role")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        room = join
            .get("room")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        if !matches!(role.as_str(), "office" | "home" | "probe") || room.is_empty() {
            _safe_send(
                &tx,
                &json!({"type": "error", "error": "Geçersiz katılım (role/kullanıcı adı)."}),
            );
            break 'session;
        }

        let password = join
            .get("password")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        let mut info: Option<Value> = None;
        let store_empty = {
            let mut store = STORE.lock();
            if !store.is_empty() {
                // Masaüstü ad ile ofis kodunu ayrı gönderir: depodaki gerçek anahtara çöz (Python _login_id).
                let office_code = join.get("office_code").and_then(|v| v.as_str()).unwrap_or("");
                let login_id = store
                    .resolve_username(&room, office_code)
                    .unwrap_or_else(|| {
                        if !office_code.is_empty() && !room.contains('@') {
                            crate::accounts::compose_username(office_code, &room)
                        } else {
                            room.clone()
                        }
                    });
                let (ok, reason, auth_info) = store.authenticate(&login_id, &password);
                if !ok {
                    _safe_send(&tx, &json!({"type": "error", "error": reason}));
                    break 'session;
                }
                if auth_info
                    .as_ref()
                    .and_then(|i| i.get("role"))
                    .and_then(|r| r.as_str())
                    == Some("owner")
                {
                    _safe_send(
                        &tx,
                        &json!({"type": "error", "error": "Bu hesap paylaşıma katılamaz."}),
                    );
                    break 'session;
                }
                rk = auth_info
                    .as_ref()
                    .and_then(|i| i.get("office_id"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                info = auth_info;
                false
            } else {
                true
            }
        };

        if store_empty {
            let allowed_guard = ALLOWED.lock();
            if let Some(allowed) = &*allowed_guard {
                if !allowed.contains(&room) {
                    _safe_send(
                        &tx,
                        &json!({"type": "error", "error": "Tanınmayan kullanıcı adı."}),
                    );
                    break 'session;
                }
                rk = room.clone();
            } else if !OPEN_MODE.load(Ordering::Relaxed) {
                _safe_send(
                    &tx,
                    &json!({
                        "type": "error",
                        "error": "Sunucu yapılandırılmamış: hesap deposu boş ve oda allowlist'i yok. Kimliksiz 'serbest oda' yalnızca açık-modda (yerel/dev) çalışır."
                    }),
                );
                break 'session;
            } else {
                rk = room.clone();
            }
        }

        if role == "probe" {
            let rooms = ROOMS.lock().await;
            let peer_present = rooms
                .get(&rk)
                .map(|s| s.office.is_some())
                .unwrap_or(false);
            let mut joined_msg = json!({"type": "joined", "peer_present": peer_present});
            if let (Some(obj), Some(info_val)) = (joined_msg.as_object_mut(), info.as_ref()) {
                if let Some(r) = info_val.get("role") {
                    obj.insert("role".to_string(), r.clone());
                }
                if let Some(oc) = info_val.get("office_code") {
                    obj.insert("office_code".to_string(), oc.clone());
                }
                if let Some(ol) = info_val.get("office_label") {
                    obj.insert("office_label".to_string(), ol.clone());
                }
            }
            _safe_send(&tx, &joined_msg);
            break 'session;
        }

        joined_room = true;
        {
            let mut rooms = ROOMS.lock().await;
            let slot = rooms.entry(rk.clone()).or_default();

            if role == "office" {
                let gelen_kullanici = info
                    .as_ref()
                    .and_then(|i| i.get("username"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();

                if info.is_some()
                    && slot.office.is_some()
                    && !slot.office_user.is_empty()
                    && slot.office_user != gelen_kullanici
                {
                    _safe_send(
                        &tx,
                        &json!({
                            "type": "error",
                            "error": "Bu ofiste paylaşım şu an başka bir kullanıcı tarafından yapılıyor. O paylaşım kapandıktan sonra yeniden deneyin."
                        }),
                    );
                    joined_room = false;
                    break 'session;
                }

                let mut meta = Map::new();
                meta.insert(
                    "local_ips".to_string(),
                    join.get("local_ips")
                        .cloned()
                        .unwrap_or_else(|| Value::Array(vec![])),
                );
                meta.insert(
                    "port".to_string(),
                    join.get("port").cloned().unwrap_or_else(|| json!(8800)),
                );
                meta.insert(
                    "lan_token".to_string(),
                    join.get("lan_token")
                        .cloned()
                        .unwrap_or_else(|| Value::String(String::new())),
                );
                slot.office_meta = Some(meta.clone());

                if let Some(old) = slot.office.take() {
                    if OPEN_MODE.load(Ordering::Relaxed) && store_empty {
                        let short: String = room.chars().take(8).collect();
                        println!(
                            "[!] UYARI: room={short}… için mevcut OFIS bağlantısı yenisiyle değiştiriliyor (açık-mod, kimlik doğrulaması yok)."
                        );
                    }
                    let _ = old.tx.send(WsMessage::Close(None));
                }

                slot.office = Some(PeerConn {
                    conn_id,
                    tx: tx.clone(),
                });
                slot.office_user = gelen_kullanici;

                _safe_send(
                    &tx,
                    &json!({"type": "joined", "peer_present": !slot.homes.is_empty()}),
                );

                for (hcid, hpeer) in &slot.homes {
                    let mut start = Map::new();
                    start.insert("type".to_string(), json!("start"));
                    start.insert("cid".to_string(), json!(hcid));
                    for (k, v) in &meta {
                        start.insert(k.clone(), v.clone());
                    }
                    _safe_send(&hpeer.tx, &Value::Object(start));
                }
            } else {
                cid = gen_hex_32();
                slot.homes.insert(
                    cid.clone(),
                    PeerConn {
                        conn_id,
                        tx: tx.clone(),
                    },
                );

                let mut joined = Map::new();
                joined.insert("type".to_string(), json!("joined"));
                joined.insert("cid".to_string(), json!(cid));
                joined.insert("peer_present".to_string(), json!(slot.office.is_some()));
                if let Some(meta) = &slot.office_meta {
                    for (k, v) in meta {
                        joined.insert(k.clone(), v.clone());
                    }
                }
                _safe_send(&tx, &Value::Object(joined));

                if slot.office.is_some() {
                    let mut start = Map::new();
                    start.insert("type".to_string(), json!("start"));
                    start.insert("cid".to_string(), json!(cid));
                    if let Some(meta) = &slot.office_meta {
                        for (k, v) in meta {
                            start.insert(k.clone(), v.clone());
                        }
                    }
                    _safe_send(&tx, &Value::Object(start));
                }
            }

            let room_short: String = room.chars().take(8).collect();
            if !cid.is_empty() {
                let cid_short: String = cid.chars().take(6).collect();
                println!("[+] Katıldı: room={room_short}… role={role} cid={cid_short}");
            } else {
                println!(
                    "[+] Katıldı: room={room_short}… role={role} istemci={}",
                    slot.homes.len()
                );
            }
        }

        // Aktarım döngüsü
        while let Some(item) = ws_source.next().await {
            let msg_text = match item {
                Ok(WsMessage::Text(t)) => t,
                Ok(WsMessage::Close(_)) | Err(_) => break,
                _ => continue,
            };

            if role == "home" {
                let mut m: Value = match serde_json::from_str(&msg_text) {
                    Ok(v) => v,
                    Err(_) => continue,
                };
                if let Some(obj) = m.as_object_mut() {
                    obj.insert("cid".to_string(), Value::String(cid.clone()));
                }
                let rooms = ROOMS.lock().await;
                if let Some(slot) = rooms.get(&rk) {
                    if let Some(office) = &slot.office {
                        _safe_send(&office.tx, &m);
                    }
                }
            } else {
                let target_cid = match serde_json::from_str::<Value>(&msg_text) {
                    Ok(v) => v
                        .get("cid")
                        .and_then(|c| c.as_str())
                        .unwrap_or("")
                        .to_string(),
                    Err(_) => continue,
                };
                let rooms = ROOMS.lock().await;
                if let Some(slot) = rooms.get(&rk) {
                    if let Some(home) = slot.homes.get(&target_cid) {
                        _safe_send_raw(&home.tx, msg_text);
                    }
                }
            }
        }
    }

    if joined_room && !rk.is_empty() {
        let mut rooms = ROOMS.lock().await;
        if let Some(slot) = rooms.get_mut(&rk) {
            if role == "office"
                && slot
                    .office
                    .as_ref()
                    .map(|o| o.conn_id == conn_id)
                    .unwrap_or(false)
            {
                slot.office = None;
                slot.office_meta = None;
                for hpeer in slot.homes.values() {
                    _safe_send(&hpeer.tx, &json!({"type": "peer_left"}));
                }
            } else if role == "home"
                && !cid.is_empty()
                && slot
                    .homes
                    .get(&cid)
                    .map(|h| h.conn_id == conn_id)
                    .unwrap_or(false)
            {
                slot.homes.remove(&cid);
                if let Some(office) = &slot.office {
                    _safe_send(&office.tx, &json!({"type": "peer_left", "cid": cid}));
                }
            }

            if slot.office.is_none() && slot.homes.is_empty() {
                rooms.remove(&rk);
            }
        }
        let room_short: String = room.chars().take(8).collect();
        if !cid.is_empty() {
            let cid_short: String = cid.chars().take(6).collect();
            println!("[!] Ayrıldı: room={room_short}… role={role} cid={cid_short}");
        } else {
            println!("[!] Ayrıldı: room={room_short}… role={role}");
        }
    }

    drop(tx);
    let _ = writer_task.await;
}

fn load_tls_acceptor(certfile: &str, keyfile: &str) -> Result<TlsAcceptor, String> {
    let cert_f = File::open(certfile).map_err(|e| format!("Sertifika açılamadı: {e}"))?;
    let mut cert_reader = BufReader::new(cert_f);
    let certs: Vec<_> = rustls_pemfile::certs(&mut cert_reader)
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| format!("Sertifika okunamadı: {e}"))?;

    let key_f = File::open(keyfile).map_err(|e| format!("Anahtar açılamadı: {e}"))?;
    let mut key_reader = BufReader::new(key_f);
    let key = rustls_pemfile::private_key(&mut key_reader)
        .map_err(|e| format!("Anahtar okunamadı: {e}"))?
        .ok_or_else(|| "Özel anahtar bulunamadı".to_string())?;

    let _ = rustls::crypto::ring::default_provider().install_default();
    let cfg = rustls::ServerConfig::builder()
        .with_no_client_auth()
        .with_single_cert(certs, key)
        .map_err(|e| format!("TLS ServerConfig hatası: {e}"))?;

    Ok(TlsAcceptor::from(Arc::new(cfg)))
}

#[derive(Parser, Debug)]
#[command(about = "UYAP P2P buluşturma (signaling) sunucusu.")]
pub struct SignalingArgs {
    #[arg(long, default_value = "0.0.0.0")]
    pub host: String,
    #[arg(long, default_value_t = 9000)]
    pub port: u16,
    #[arg(long)]
    pub config: Option<PathBuf>,
    /// Hesap deposu boş + allowlist yokken kimliksiz 'serbest oda'a izin ver (YALNIZCA yerel/dev; üretimde kullanmayın).
    #[arg(long = "open")]
    pub open_mode: bool,
    #[arg(long)]
    pub ssl_certfile: Option<String>,
    #[arg(long)]
    pub ssl_keyfile: Option<String>,
}

pub async fn amain(args: SignalingArgs) {
    let cfg_path = args.config.unwrap_or_else(|| CONFIG_PATH.clone());
    load_allowed(&cfg_path);
    let open = args.open_mode || _env_open_mode();
    OPEN_MODE.store(open, Ordering::Relaxed);

    let has_allowed = ALLOWED.lock().is_some();
    let store_empty = STORE.lock().is_empty();
    if !has_allowed && store_empty {
        if open {
            println!("[!] AÇIK-MOD: hesap deposu boş + allowlist yok → oda adını bilen herkes kimliksiz katılabilir. Yalnızca yerel/dev için; üretimde KAPATIN.");
        } else {
            println!("[i] Hesap deposu boş + allowlist yok → kimliksiz buluşma REDDEDİLİR (güvenli varsayılan). Yerel/dev için --open ya da UYAP_SIGNALING_OPEN=1.");
        }
    }

    let tls_acceptor = match (&args.ssl_certfile, &args.ssl_keyfile) {
        (Some(c), Some(k)) => match load_tls_acceptor(c, k) {
            Ok(acc) => Some(acc),
            Err(e) => {
                eprintln!("[!] TLS yüklenemedi: {e}");
                return;
            }
        },
        _ => None,
    };

    let scheme = if tls_acceptor.is_some() { "wss" } else { "ws" };
    let bind_addr: SocketAddr = format!("{}:{}", args.host, args.port)
        .parse()
        .unwrap_or_else(|_| SocketAddr::from(([0, 0, 0, 0], args.port)));

    let listener = match TcpListener::bind(bind_addr).await {
        Ok(l) => l,
        Err(e) => {
            if e.kind() == std::io::ErrorKind::AddrInUse {
                println!(
                    "[!] Port {} zaten kullanımda — büyük ihtimalle BAŞKA bir signaling penceresi açık.",
                    args.port
                );
                println!("[!] İki signaling gerekmez: açık olanı kullan, ya da onu kapatıp tekrar başlat.");
                return;
            }
            panic!("Signaling dinleme hatası: {e}");
        }
    };

    println!(
        "[*] Signaling {scheme}://{}:{} dinliyor (yalnızca SDP taşır, veri YOK).",
        args.host, args.port
    );

    loop {
        let Ok((stream, _peer)) = listener.accept().await else {
            continue;
        };
        let acceptor = tls_acceptor.clone();
        tokio::spawn(async move {
            if let Some(acc) = acceptor {
                if let Ok(tls) = acc.accept(stream).await {
                    handle_ws(tls).await;
                }
            } else {
                handle_ws(stream).await;
            }
        });
    }
}

pub fn main() {
    let args = SignalingArgs::parse();
    let rt = crate::build_tokio_runtime().expect("Tokio runtime başlatılamadı");
    rt.block_on(async move {
        tokio::select! {
            _ = amain(args) => {},
            _ = tokio::signal::ctrl_c() => {
                println!("\n[!] Signaling kapatıldı.");
            }
        }
    });
}
