//! P2P taşıma yardımcıları (ofis ajanı ve ev istemcisi ortak kullanır)
//! -------------------------------------------------------------------
//! `uyap_core/p2p_wire.py` dosyasının birebir (1:1) Rust karşılığı.

use std::collections::{BTreeMap, HashMap};
use std::future::Future;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::Arc;
use std::time::Duration;

use flate2::read::ZlibDecoder;
use flate2::write::ZlibEncoder;
use flate2::Compression;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

/// Ham gövde parçası başına bayt. Çerçeveler İKİLİ (base64 yok), bu yüzden bu
/// doğrudan tel üstündeki parça boyutu. SCTP/DataChannel güvenli mesaj sınırının altında.
pub const CHUNK: usize = 48 * 1024;

/// Boştayken bağlantıyı canlı tutmak için kalp atışı (heartbeat) aralığı (saniye).
pub const HEARTBEAT_INTERVAL: u64 = 15;

/// Ofis ajanı KONTROL DÜZLEMİ önek'i: bu önekle başlayan HTTP yolları UYAP'a İLETİLMEZ;
/// ofiste yerel olarak (iş kuyruğu vb.) işlenir.
pub const AGENT_PREFIX: &str = "__uyap_agent__";

/// Zaten sıkıştırılmış içerik türleri; bunları tekrar zlib'den geçirmek boşa CPU yakar.
pub const _PRECOMPRESSED: &[&str] = &[
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/gif",
    "image/webp",
    "font/woff",
    "font/woff2",
    "application/font-woff",
    "application/font-woff2",
    "application/zip",
    "application/pdf",
    "application/octet-stream",
    "video/",
    "audio/",
];

#[derive(Debug, Clone, PartialEq)]
pub struct Message {
    pub id: String,
    pub kind: String,
    pub meta: Option<Value>,
    pub body: Vec<u8>,
}

impl Message {
    pub fn new(id: impl Into<String>, kind: impl Into<String>, meta: Option<Value>, body: Vec<u8>) -> Self {
        Self {
            id: id.into(),
            kind: kind.into(),
            meta,
            body,
        }
    }
}

/// meta içindeki Content-Type zaten sıkıştırılmış bir tür mü?
pub fn _is_precompressed(meta: Option<&Value>) -> bool {
    let Some(m) = meta else {
        return false;
    };
    let Some(headers) = m.get("headers").and_then(|h| h.as_object()) else {
        return false;
    };
    for (k, v) in headers {
        if k.to_lowercase() == "content-type" {
            let ctype = v.as_str().unwrap_or("").to_lowercase();
            return _PRECOMPRESSED.iter().any(|p| ctype.contains(p));
        }
    }
    false
}

/// Bir mantıksal mesajı DataChannel'a gönderilecek İKİLİ çerçevelere böler.
/// Boyut 1024 bayttan büyük ve içerik zaten sıkıştırılmış değilse zlib uygular.
pub fn encode_frames(msg_id: &str, kind: &str, meta: Option<Value>, body: &[u8]) -> Vec<Vec<u8>> {
    let mut compressed = false;
    let mut actual_body: Vec<u8> = body.to_vec();

    if !body.is_empty() && body.len() > 1024 && !_is_precompressed(meta.as_ref()) {
        let mut encoder = ZlibEncoder::new(Vec::new(), Compression::fast());
        if encoder.write_all(body).is_ok() {
            if let Ok(compressed_body) = encoder.finish() {
                if compressed_body.len() < body.len() {
                    actual_body = compressed_body;
                    compressed = true;
                }
            }
        }
    }

    let total = std::cmp::max(1, (actual_body.len() + CHUNK - 1) / CHUNK);
    let mut frames = Vec::with_capacity(total);

    for seq in 0..total {
        let start = seq * CHUNK;
        let end = std::cmp::min((seq + 1) * CHUNK, actual_body.len());
        let chunk = if start < end { &actual_body[start..end] } else { &[][..] };

        let mut header = serde_json::Map::new();
        header.insert("id".to_string(), Value::String(msg_id.to_string()));
        header.insert("kind".to_string(), Value::String(kind.to_string()));
        header.insert("seq".to_string(), Value::Number(serde_json::Number::from(seq)));
        header.insert("total".to_string(), Value::Number(serde_json::Number::from(total)));

        if seq == 0 {
            let mut m = match meta.clone() {
                Some(Value::Object(map)) => map,
                _ => serde_json::Map::new(),
            };
            m.insert("_comp".to_string(), Value::Bool(compressed));
            header.insert("meta".to_string(), Value::Object(m));
        }

        let header_bytes = serde_json::to_vec(&Value::Object(header)).unwrap_or_default();
        let hlen = (header_bytes.len() as u32).to_be_bytes();

        let mut frame = Vec::with_capacity(4 + header_bytes.len() + chunk.len());
        frame.extend_from_slice(&hlen);
        frame.extend_from_slice(&header_bytes);
        frame.extend_from_slice(chunk);
        frames.push(frame);
    }

    frames
}

#[derive(Debug)]
struct ReasmEntry {
    chunks: BTreeMap<usize, Vec<u8>>,
    total: usize,
    meta: Option<Value>,
    kind: String,
}

/// Gelen çerçeveleri `id`'ye göre biriktirir; bir mesaj tamamlanınca Message döndürür.
/// Parçalar `seq`'e göre saklanıp sırayla birleştirildiği için sırasız (unordered)
/// DataChannel teslimi de güvenle desteklenir.
#[derive(Debug, Default)]
pub struct Reassembler {
    _buf: HashMap<String, ReasmEntry>,
}

impl Reassembler {
    pub fn new() -> Self {
        Self {
            _buf: HashMap::new(),
        }
    }

    /// Bir ikili çerçeveyi işler. Mesaj tamamlandıysa Some(Message), değilse None döndürür.
    pub fn feed(&mut self, frame: &[u8]) -> Result<Option<Message>, String> {
        if frame.len() < 4 {
            return Err("Çerçeve çok kısa (< 4 bayt)".to_string());
        }
        let hlen = u32::from_be_bytes([frame[0], frame[1], frame[2], frame[3]]) as usize;
        if frame.len() < 4 + hlen {
            return Err("Çerçeve başlığı eksik".to_string());
        }
        let header_slice = &frame[4..4 + hlen];
        let header: Value = serde_json::from_slice(header_slice)
            .map_err(|e| format!("JSON başlık hatası: {e}"))?;
        let chunk = &frame[4 + hlen..];

        let mid = header
            .get("id")
            .and_then(|v| v.as_str())
            .ok_or_else(|| "Başlıkta 'id' alanı yok".to_string())?
            .to_string();
        let total = header
            .get("total")
            .and_then(|v| v.as_u64())
            .ok_or_else(|| "Başlıkta 'total' alanı yok".to_string())? as usize;
        let seq = header
            .get("seq")
            .and_then(|v| v.as_u64())
            .ok_or_else(|| "Başlıkta 'seq' alanı yok".to_string())? as usize;
        let kind = header
            .get("kind")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        let entry = self._buf.entry(mid.clone()).or_insert_with(|| ReasmEntry {
            chunks: BTreeMap::new(),
            total,
            meta: None,
            kind,
        });

        entry.chunks.insert(seq, chunk.to_vec());
        if seq == 0 {
            entry.meta = header.get("meta").cloned();
        }

        if entry.chunks.len() >= entry.total {
            let completed = self._buf.remove(&mid).unwrap();
            let total_bytes: usize = (0..completed.total)
                .filter_map(|i| completed.chunks.get(&i).map(|c| c.len()))
                .sum();
            let mut body = Vec::with_capacity(total_bytes);
            for i in 0..completed.total {
                if let Some(c) = completed.chunks.get(&i) {
                    body.extend_from_slice(c);
                }
            }

            let meta = completed.meta;
            let is_comp = meta
                .as_ref()
                .and_then(|m| m.get("_comp"))
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            if is_comp {
                let mut decoder = ZlibDecoder::new(&body[..]);
                let mut decompressed = Vec::new();
                decoder
                    .read_to_end(&mut decompressed)
                    .map_err(|e| format!("zlib açma hatası: {e}"))?;
                body = decompressed;
            }

            return Ok(Some(Message {
                id: mid,
                kind: completed.kind,
                meta,
                body,
            }));
        }

        Ok(None)
    }
}

/// Soyut kanal arayüzü: hem WebRTC DataChannel hem WSRelaySender/WSRelayChannel bu trait'i uygular.
pub trait FrameChannel: Send + Sync {
    fn buffered_amount(&self) -> Pin<Box<dyn Future<Output = usize> + Send + '_>>;
    fn send_frame(&self, frame: Vec<u8>) -> Pin<Box<dyn Future<Output = Result<(), String>> + Send + '_>>;
    fn is_open(&self) -> bool {
        true
    }
}

impl FrameChannel for Arc<webrtc::data_channel::RTCDataChannel> {
    fn buffered_amount(&self) -> Pin<Box<dyn Future<Output = usize> + Send + '_>> {
        Box::pin(async move {
            webrtc::data_channel::RTCDataChannel::buffered_amount(self.as_ref()).await
        })
    }

    fn send_frame(&self, frame: Vec<u8>) -> Pin<Box<dyn Future<Output = Result<(), String>> + Send + '_>> {
        Box::pin(async move {
            let bytes = bytes::Bytes::from(frame);
            self.send(&bytes).await.map(|_| ()).map_err(|e| e.to_string())
        })
    }

    fn is_open(&self) -> bool {
        use webrtc::data_channel::data_channel_state::RTCDataChannelState;
        self.ready_state() == RTCDataChannelState::Open
    }
}

/// Geri-basınç: DataChannel'ın gönderim tamponu şişmişse boşalana kadar bekle.
/// Büyük cevaplarda belleği ve ağı korur.
pub async fn drain<C: FrameChannel + ?Sized>(channel: &C, threshold: usize, poll_secs: f64) {
    let poll_dur = Duration::from_secs_f64(poll_secs);
    while channel.buffered_amount().await > threshold {
        tokio::time::sleep(poll_dur).await;
    }
}

/// Bir mantıksal mesajı geri-basınca saygı göstererek tüm çerçeveleriyle gönderir.
pub async fn send_message<C: FrameChannel + ?Sized>(
    channel: &C,
    msg_id: &str,
    kind: &str,
    meta: Option<Value>,
    body: &[u8],
) -> Result<(), String> {
    for frame in encode_frames(msg_id, kind, meta, body) {
        drain(channel, 1_000_000, 0.01).await;
        channel.send_frame(frame).await?;
        // sleep(0) ile event loop'a geçiş izni veririz
        tokio::task::yield_now().await;
    }
    Ok(())
}

/// ICE (STUN/TURN) sunucu tanımı (aiortc.RTCIceServer eşdeğeri).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RTCIceServerConfig {
    pub urls: Vec<String>,
    pub username: Option<String>,
    pub credential: Option<String>,
}

impl RTCIceServerConfig {
    pub fn from_json_value(v: &Value) -> Option<Self> {
        let urls_val = v.get("urls")?;
        let urls = if let Some(s) = urls_val.as_str() {
            vec![s.to_string()]
        } else if let Some(arr) = urls_val.as_array() {
            arr.iter()
                .filter_map(|x| x.as_str().map(|s| s.to_string()))
                .collect()
        } else {
            return None;
        };
        if urls.is_empty() {
            return None;
        }
        let username = v
            .get("username")
            .and_then(|u| u.as_str())
            .map(|s| s.to_string());
        let credential = v
            .get("credential")
            .and_then(|c| c.as_str())
            .map(|s| s.to_string());
        Some(Self {
            urls,
            username,
            credential,
        })
    }

    pub fn to_webrtc(&self) -> webrtc::ice_transport::ice_server::RTCIceServer {
        webrtc::ice_transport::ice_server::RTCIceServer {
            urls: self.urls.clone(),
            username: self.username.clone().unwrap_or_default(),
            credential: self.credential.clone().unwrap_or_default(),
        }
    }
}

fn find_ice_servers_json_path() -> PathBuf {
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let p1 = dir.join("ice_servers.json");
            if p1.exists() {
                return p1;
            }
            let p2 = dir.join("uyap_core").join("ice_servers.json");
            if p2.exists() {
                return p2;
            }
        }
    }
    let p3 = Path::new("uyap_core").join("ice_servers.json");
    if p3.exists() {
        return p3;
    }
    PathBuf::from("ice_servers.json")
}

/// ICE (STUN/TURN) sunucularını ortam değişkeni ya da dosyadan yükler.
pub fn load_ice_servers() -> Vec<RTCIceServerConfig> {
    let mut data: Option<Vec<Value>> = None;

    if let Ok(raw) = std::env::var("UYAP_ICE") {
        if !raw.trim().is_empty() {
            if let Ok(parsed) = serde_json::from_str::<Vec<Value>>(&raw) {
                data = Some(parsed);
            }
        }
    } else {
        let path = find_ice_servers_json_path();
        if path.exists() {
            if let Ok(content) = std::fs::read_to_string(&path) {
                if let Ok(parsed) = serde_json::from_str::<Vec<Value>>(&content) {
                    data = Some(parsed);
                }
            }
        }
    }

    let list = match data {
        Some(v) if !v.is_empty() => v,
        _ => vec![json!({"urls": "stun:stun.l.google.com:19302"})],
    };

    list.iter()
        .filter_map(RTCIceServerConfig::from_json_value)
        .collect()
}

/// wss://host/ws  →  https://host/ice  (ws→http, /ws son ekini /ice yapar).
pub fn _signaling_to_ice_url(signaling_url: &str) -> Option<String> {
    let u = signaling_url.trim();
    if u.is_empty() {
        return None;
    }
    let u = if let Some(rest) = u.strip_prefix("wss://") {
        format!("https://{rest}")
    } else if let Some(rest) = u.strip_prefix("ws://") {
        format!("http://{rest}")
    } else {
        return None;
    };
    if let Some(base) = u.strip_suffix("/ws") {
        Some(format!("{base}/ice"))
    } else {
        Some(format!("{}/ice", u.trim_end_matches('/')))
    }
}

/// Satıcı sunucusunun /ice ucundan ICE listesini (efemeral TURN dahil) çeker ve
/// RTCIceServerConfig listesine çevirir. Ulaşılamazsa yerel load_ice_servers()'a düşer.
pub async fn fetch_ice_servers(signaling_url: &str, timeout_secs: f64) -> Vec<RTCIceServerConfig> {
    if let Some(ice_url) = _signaling_to_ice_url(signaling_url) {
        if !ice_url.contains("127.0.0.1") && !ice_url.contains("localhost") {
            let client_res = reqwest::Client::builder()
                .timeout(Duration::from_secs_f64(timeout_secs))
                .build();
            if let Ok(client) = client_res {
                match client.get(&ice_url).send().await {
                    Ok(resp) => {
                        if resp.status().as_u16() == 200 {
                            if let Ok(val) = resp.json::<Value>().await {
                                if let Some(arr) = val.get("iceServers").and_then(|v| v.as_array()) {
                                    let servers: Vec<RTCIceServerConfig> = arr
                                        .iter()
                                        .filter_map(RTCIceServerConfig::from_json_value)
                                        .collect();
                                    if !servers.is_empty() {
                                        return servers;
                                    }
                                }
                            }
                        }
                    }
                    Err(e) => {
                        println!("[*] ICE sunucudan çekilemedi ({e}); yerel ayara düşülüyor.");
                    }
                }
            }
        }
    }
    load_ice_servers()
}
