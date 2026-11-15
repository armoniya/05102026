//! /zs İş Takip — Telegram grubundan iş açma: mesaj ayrıştırma (zs_tg_grup.py 1:1 port)
//! ------------------------------------------------------------------------------------
//! Yalnız Telegram mesaj sözlüğünü çözer; veritabanına/ağa dokunmaz (iş mantığı zs.rs'de).
//!
//! Kullanım (izin verilen grupta, botla özel sohbetten bağlanmış kişi):
//!   /is @ayse Tebligatı dosyaya işle           → Ayşe'ye iş açar
//!   (bir fotoğrafa/belgeye YANIT olarak) /is @ayse ...  → iş açılır, o dosya işe belge olarak eklenir
//!   /is Kendime not                            → etiket yoksa iş yazan kişiye açılır
//!   Botun "İş açıldı" mesajına yanıt (belge/fotoğraf/metin) → o işe belge / not eklenir
//!
//! Telegram varlık (entity) konumları UTF-16 birimiyle verilir; Türkçe harfler tek birim ama
//! emoji iki birim olduğundan metin UTF-16'ya çevrilerek kesilir.

use std::collections::HashSet;
use std::env;

use chrono::{Local, TimeZone};
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

pub const COMMAND: &str = "is";
pub const TG_MAX_DOWNLOAD: usize = 20 * 1024 * 1024; // Bot API getFile sınırı

/// Mention in a parsed `/is` command:
/// Either `("username", "ayse")` or `("id", 12345, "Ayşe")`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Mention {
    Username(String, String),
    Id(String, i64, String),
}

impl Mention {
    pub fn kind(&self) -> &str {
        match self {
            Mention::Username(k, _) => k.as_str(),
            Mention::Id(k, _, _) => k.as_str(),
        }
    }

    pub fn username(&self) -> Option<&str> {
        match self {
            Mention::Username(_, u) => Some(u.as_str()),
            Mention::Id(_, _, _) => None,
        }
    }

    pub fn user_id(&self) -> Option<i64> {
        match self {
            Mention::Username(_, _) => None,
            Mention::Id(_, id, _) => Some(*id),
        }
    }

    pub fn display_name(&self) -> Option<&str> {
        match self {
            Mention::Username(_, _) => None,
            Mention::Id(_, _, name) => Some(name.as_str()),
        }
    }

    pub fn to_value(&self) -> Value {
        match self {
            Mention::Username(k, u) => json!([k, u]),
            Mention::Id(k, id, name) => json!([k, id, name]),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ParsedCommand {
    pub title: String,
    pub description: String,
    pub mentions: Vec<Mention>,
}

impl ParsedCommand {
    pub fn to_value(&self) -> Value {
        json!({
            "title": self.title,
            "description": self.description,
            "mentions": self.mentions.iter().map(|m| m.to_value()).collect::<Vec<_>>(),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Attachment {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub file_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub filename: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub size: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub unsupported: Option<String>,
}

impl Attachment {
    pub fn to_value(&self) -> Value {
        if let Some(ref u) = self.unsupported {
            json!({ "unsupported": u })
        } else {
            json!({
                "file_id": self.file_id.clone().unwrap_or_default(),
                "filename": self.filename.clone().unwrap_or_default(),
                "size": self.size.unwrap_or(0),
            })
        }
    }
}

/// ZS_TELEGRAM_GROUPS="-1001234,-1005678" → {-1001234, -1005678}. Boşsa grup özelliği kapalı.
pub fn allowed_groups() -> HashSet<i64> {
    let raw = env::var("ZS_TELEGRAM_GROUPS").unwrap_or_default();
    allowed_groups_from_str(&raw)
}

pub fn allowed_groups_from_str(raw: &str) -> HashSet<i64> {
    let mut out = HashSet::new();
    let re = Regex::new(r"[,\s;]+").unwrap();
    for p in re.split(raw) {
        if let Ok(id) = p.trim().parse::<i64>() {
            out.insert(id);
        }
    }
    out
}

pub fn _u16(text: &str) -> Vec<u8> {
    let mut out = Vec::with_capacity(text.len() * 2);
    for u in text.encode_utf16() {
        out.extend_from_slice(&u.to_le_bytes());
    }
    out
}

pub fn _slice(raw: &[u8], off: usize, ln: usize) -> String {
    let start = (off * 2).min(raw.len());
    let end = ((off + ln) * 2).min(raw.len());
    if start >= end {
        return String::new();
    }
    let slice = &raw[start..end];
    let u16s: Vec<u16> = slice
        .chunks_exact(2)
        .map(|c| u16::from_le_bytes([c[0], c[1]]))
        .collect();
    // decode("utf-16-le", errors="ignore")
    char::decode_utf16(u16s.into_iter())
        .filter_map(|r| r.ok())
        .collect()
}

/// Mesajın metni ve varlıkları (fotoğraf/belge mesajında açıklama = caption).
pub fn text_and_entities(msg: &Value) -> (String, Vec<Value>) {
    if let Some(t) = msg.get("text") {
        if !t.is_null() {
            let text = t.as_str().unwrap_or("").to_string();
            let ents = msg
                .get("entities")
                .and_then(|e| e.as_array())
                .cloned()
                .unwrap_or_default();
            return (text, ents);
        }
    }
    let caption = msg
        .get("caption")
        .and_then(|c| c.as_str())
        .unwrap_or("")
        .to_string();
    let ents = msg
        .get("caption_entities")
        .and_then(|e| e.as_array())
        .cloned()
        .unwrap_or_default();
    (caption, ents)
}

/// '/is' komutuysa `ParsedCommand` döndürür, değilse `None`.
/// mentions: sıralı, tekrarsız `[("username", "ayse") | ("id", 12345, "Ayşe")]`. Botun kendisi atlanır.
pub fn parse_command(msg: &Value, bot_username: Option<&str>) -> Option<ParsedCommand> {
    let (text, ents) = text_and_entities(msg);
    let raw = _u16(&text);
    let cmd = ents.iter().find(|e| {
        e.get("type").and_then(|v| v.as_str()) == Some("bot_command")
            && e.get("offset").and_then(|v| v.as_u64()) == Some(0)
    })?;
    let cmd_len = cmd.get("length").and_then(|v| v.as_u64())? as usize;
    let sliced = _slice(&raw, 0, cmd_len);
    let name = sliced.chars().skip(1).collect::<String>();
    let (base, target) = match name.split_once('@') {
        Some((b, t)) => (b, t),
        None => (name.as_str(), ""),
    };
    if base.to_lowercase() != COMMAND {
        return None;
    }
    let bot = bot_username.unwrap_or("").to_lowercase();
    if !target.is_empty() && target.to_lowercase() != bot {
        return None; // başka bota yazılmış
    }

    let mut cut: Vec<(usize, usize)> = vec![(0, cmd_len)];
    let mut mentions: Vec<Mention> = Vec::new();
    let mut seen: HashSet<(String, String)> = HashSet::new();

    let mut sorted_ents = ents.clone();
    sorted_ents.sort_by_key(|e| e.get("offset").and_then(|v| v.as_u64()).unwrap_or(0));

    for e in sorted_ents {
        let t = e.get("type").and_then(|v| v.as_str()).unwrap_or("");
        let off = e.get("offset").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
        let ln = e.get("length").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
        if t == "mention" {
            let uname = _slice(&raw, off, ln)
                .trim_start_matches('@')
                .to_lowercase();
            if uname == bot {
                cut.push((off, ln));
                continue;
            }
            cut.push((off, ln));
            let seen_key = ("username".to_string(), uname.clone());
            if seen.insert(seen_key) {
                mentions.push(Mention::Username("username".to_string(), uname));
            }
        } else if t == "text_mention" {
            if let Some(u) = e.get("user") {
                if let Some(uid) = u.get("id").and_then(|v| v.as_i64()) {
                    if u.get("is_bot").and_then(|v| v.as_bool()).unwrap_or(false) {
                        continue;
                    }
                    cut.push((off, ln));
                    let seen_key = ("id".to_string(), uid.to_string());
                    if seen.insert(seen_key) {
                        let first = u.get("first_name").and_then(|v| v.as_str()).unwrap_or("");
                        let last = u.get("last_name").and_then(|v| v.as_str()).unwrap_or("");
                        let full = [first, last]
                            .iter()
                            .filter(|x| !x.is_empty())
                            .copied()
                            .collect::<Vec<_>>()
                            .join(" ");
                        mentions.push(Mention::Id("id".to_string(), uid, full));
                    }
                }
            }
        }
    }

    // Komut ve etiketler çıkarılınca kalan metin: ilk satır başlık, gerisi açıklama.
    cut.sort();
    let mut keep: Vec<u8> = Vec::new();
    let mut pos: usize = 0;
    for (off, ln) in cut {
        let start = (pos * 2).min(raw.len());
        let end = (off * 2).min(raw.len());
        if start < end {
            keep.extend_from_slice(&raw[start..end]);
        }
        pos = pos.max(off + ln);
    }
    let tail_start = (pos * 2).min(raw.len());
    keep.extend_from_slice(&raw[tail_start..]);

    let u16s: Vec<u16> = keep
        .chunks_exact(2)
        .map(|c| u16::from_le_bytes([c[0], c[1]]))
        .collect();
    let rest: String = char::decode_utf16(u16s.into_iter())
        .filter_map(|r| r.ok())
        .collect();

    let ws_re = Regex::new(r"[ \t]+").unwrap();
    let strip_chars: &[char] = &[' ', '\t', ':', ',', '-'];
    let mut lines: Vec<String> = rest
        .lines()
        .map(|ln| {
            let collapsed = ws_re.replace_all(ln, " ");
            collapsed.trim_matches(strip_chars).to_string()
        })
        .collect();
    while !lines.is_empty() && lines[0].is_empty() {
        lines.remove(0);
    }
    let title = if !lines.is_empty() {
        lines[0].clone()
    } else {
        String::new()
    };
    let desc = if lines.len() > 1 {
        lines[1..].join("\n").trim().to_string()
    } else {
        String::new()
    };
    Some(ParsedCommand {
        title,
        description: desc,
        mentions,
    })
}

/// Mesajdaki dosya: `{"file_id", "filename", "size"}` ya da `None`.
/// Desteklenmeyen tür (video, ses vb.) için `{"unsupported": "video"}`.
pub fn attachment(msg: &Value) -> Option<Attachment> {
    if msg.is_null() || !msg.is_object() {
        return None;
    }
    if let Some(d) = msg.get("document").and_then(|v| v.as_object()) {
        let file_id = d
            .get("file_id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let filename = d
            .get("file_name")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let size = d.get("file_size").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
        return Some(Attachment {
            file_id: Some(file_id),
            filename: Some(filename),
            size: Some(size),
            unsupported: None,
        });
    }
    if let Some(photos) = msg.get("photo").and_then(|v| v.as_array()) {
        if !photos.is_empty() {
            // Aynı fotoğrafın boyutları küçükten büyüğe gelir; en büyüğü alınır.
            let p = photos.iter().max_by_key(|x| {
                let fs = x.get("file_size").and_then(|v| v.as_u64()).unwrap_or(0);
                let w = x.get("width").and_then(|v| v.as_u64()).unwrap_or(0);
                let h = x.get("height").and_then(|v| v.as_u64()).unwrap_or(0);
                (fs, w * h)
            })?;
            let ts = msg
                .get("date")
                .and_then(|v| v.as_i64())
                .unwrap_or_else(|| Local::now().timestamp());
            let dt = Local
                .timestamp_opt(ts, 0)
                .single()
                .unwrap_or_else(Local::now);
            let stamp = dt.format("%Y%m%d_%H%M%S").to_string();
            let file_id = p
                .get("file_id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let size = p.get("file_size").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
            return Some(Attachment {
                file_id: Some(file_id),
                filename: Some(format!("telegram_foto_{}.jpg", stamp)),
                size: Some(size),
                unsupported: None,
            });
        }
    }
    for kind in ["video", "animation", "audio", "voice", "video_note", "sticker"] {
        if let Some(v) = msg.get(kind) {
            if !v.is_null() && v != &json!(false) {
                return Some(Attachment {
                    file_id: None,
                    filename: None,
                    size: None,
                    unsupported: Some(kind.to_string()),
                });
            }
        }
    }
    None
}

pub fn sender_name(user: Option<&Value>) -> String {
    let Some(u) = user else {
        return "?".to_string();
    };
    let first = u.get("first_name").and_then(|v| v.as_str()).unwrap_or("");
    let last = u.get("last_name").and_then(|v| v.as_str()).unwrap_or("");
    let name = [first, last]
        .iter()
        .filter(|x| !x.is_empty())
        .copied()
        .collect::<Vec<_>>()
        .join(" ");
    if !name.is_empty() {
        return name;
    }
    if let Some(uname) = u.get("username").and_then(|v| v.as_str()) {
        if !uname.is_empty() {
            return format!("@{}", uname);
        }
    }
    "?".to_string()
}
