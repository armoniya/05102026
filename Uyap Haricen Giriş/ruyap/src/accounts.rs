//! Hesap / Lisans Deposu (accounts.py 1:1 port) — v2: Ofis + Kullanıcı (master/üye) modeli
//! ------------------------------------------------------------------------------------------
//! Satıcı sunucusunun (vendor_server.rs) kullandığı küçük hesap deposu.

use std::collections::{HashMap, HashSet};
use std::env;
use std::fmt;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use base64::{engine::general_purpose::STANDARD as B64, engine::general_purpose::URL_SAFE_NO_PAD as B64U, Engine as _};
use data_encoding::BASE32_NOPAD;
use hmac::{Hmac, Mac};
use once_cell::sync::Lazy;
use pbkdf2::pbkdf2_hmac;
use rand::RngCore;
use regex::Regex;
use serde_json::{json, Map, Value};
use sha1::Sha1;
use sha2::Sha256;
use subtle::ConstantTimeEq;

type HmacSha1 = Hmac<Sha1>;

pub const _PBKDF2_ITERS: u32 = 600_000;
pub const TOTP_STEP: u64 = 30;
pub const TOTP_DIGITS: u32 = 6;
pub const TOTP_ISSUER: &str = "UYAP";
pub const _GENERIC_AUTH_FAIL: &str = "Kullanıcı adı veya parola hatalı.";

pub fn data_dir() -> PathBuf {
    if let Ok(d) = env::var("UYAP_DATA_DIR") {
        let t = d.trim();
        if !t.is_empty() {
            return PathBuf::from(t);
        }
    }
    if let Ok(exe) = env::current_exe() {
        if let Some(p) = exe.parent() {
            return p.to_path_buf();
        }
    }
    env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

pub fn accounts_path() -> PathBuf {
    data_dir().join("accounts.json")
}

pub fn database_url() -> String {
    env::var("DATABASE_URL").unwrap_or_default().trim().to_string()
}

pub fn owner_username() -> String {
    let u = env::var("UYAP_OWNER_USERNAME").unwrap_or_default().trim().to_string();
    if u.is_empty() {
        "utku".to_string()
    } else {
        u
    }
}

pub fn min_password_length() -> usize {
    let raw = env::var("UYAP_MIN_PASSWORD_LEN").unwrap_or_else(|_| "8".to_string());
    let parsed = raw.trim().parse::<usize>().unwrap_or(8);
    parsed.max(8)
}

pub fn password_policy_hint() -> String {
    format!(
        "en az {} karakter; en az bir büyük harf, bir küçük harf, bir rakam ve bir özel işaret",
        min_password_length()
    )
}

pub fn now_ts() -> i64 {
    now_epoch()
}

fn now_epoch() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64
}

fn token_urlsafe(nbytes: usize) -> String {
    let mut buf = vec![0u8; nbytes];
    rand::thread_rng().fill_bytes(&mut buf);
    B64U.encode(&buf)
}

// ──────────────────────────────────────────────────────────────────────────────────────
// Parola / jeton yardımcıları
// ──────────────────────────────────────────────────────────────────────────────────────
pub fn hash_password_str(password: &str, _salt_opt: Option<&[u8]>) -> String {
    serde_json::to_string(&_hash_password(password, None, _PBKDF2_ITERS)).unwrap_or_default()
}

pub fn verify_password_str(password: &str, stored: &str) -> bool {
    let Ok(val) = serde_json::from_str::<Value>(stored) else {
        return false;
    };
    _verify_password(password, &val)
}

pub fn _hash_password(password: &str, salt_opt: Option<&[u8]>, iters: u32) -> Value {
    let mut rand_salt = [0u8; 16];
    let salt = match salt_opt {
        Some(s) => s,
        None => {
            rand::thread_rng().fill_bytes(&mut rand_salt);
            &rand_salt
        }
    };
    let mut dk = [0u8; 32];
    pbkdf2_hmac::<Sha256>(password.as_bytes(), salt, iters, &mut dk);
    json!({
        "algo": "pbkdf2_sha256",
        "iters": iters,
        "salt": B64.encode(salt),
        "hash": B64.encode(dk),
    })
}

pub fn _verify_password(password: &str, rec: &Value) -> bool {
    let Some(obj) = rec.as_object() else {
        return false;
    };
    if obj.get("algo").and_then(|v| v.as_str()) != Some("pbkdf2_sha256") {
        return false;
    }
    let Some(salt_b64) = obj.get("salt").and_then(|v| v.as_str()) else {
        return false;
    };
    let Some(hash_b64) = obj.get("hash").and_then(|v| v.as_str()) else {
        return false;
    };
    let Ok(salt) = B64.decode(salt_b64.as_bytes()) else {
        return false;
    };
    let Ok(expected) = B64.decode(hash_b64.as_bytes()) else {
        return false;
    };
    let iters = obj
        .get("iters")
        .and_then(|v| v.as_u64())
        .map(|v| v as u32)
        .unwrap_or(_PBKDF2_ITERS);
    let mut dk = vec![0u8; expected.len()];
    pbkdf2_hmac::<Sha256>(password.as_bytes(), &salt, iters, &mut dk);
    bool::from(dk.ct_eq(&expected))
}

pub static _DUMMY_PW_REC: Lazy<Value> = Lazy::new(|| {
    let tok = token_urlsafe(16);
    _hash_password(&tok, None, _PBKDF2_ITERS)
});

pub fn generate_room_key() -> String {
    format!("uyap_{}", token_urlsafe(18))
}

pub fn generate_office_id() -> String {
    format!("off_{}", token_urlsafe(9))
}

pub fn generate_password() -> String {
    token_urlsafe(9)
}

pub fn generate_order_id() -> String {
    format!("ord_{}", token_urlsafe(9))
}

// ──────────────────────────────────────────────────────────────────────────────────────
// TOTP (RFC 6238)
// ──────────────────────────────────────────────────────────────────────────────────────
pub fn generate_totp_secret() -> String {
    let mut buf = [0u8; 20];
    rand::thread_rng().fill_bytes(&mut buf);
    BASE32_NOPAD.encode(&buf)
}

pub fn _totp_at(secret_b32: &str, counter: u64) -> Option<String> {
    let cleaned = secret_b32.trim().replace(' ', "").trim_end_matches('=').to_uppercase();
    let key = BASE32_NOPAD.decode(cleaned.as_bytes()).ok()?;
    let mut mac = HmacSha1::new_from_slice(&key).ok()?;
    mac.update(&counter.to_be_bytes());
    let result = mac.finalize().into_bytes();
    let off = (result[result.len() - 1] & 0x0f) as usize;
    let bin_code = u32::from_be_bytes([
        result[off] & 0x7f,
        result[off + 1],
        result[off + 2],
        result[off + 3],
    ]);
    let modulo = 10u32.pow(TOTP_DIGITS);
    let code = bin_code % modulo;
    Some(format!("{:0width$}", code, width = TOTP_DIGITS as usize))
}

pub fn totp_verify(secret_b32: &str, code: &str, last_counter: i64) -> (bool, i64) {
    let code = code.trim();
    if secret_b32.is_empty()
        || code.len() != TOTP_DIGITS as usize
        || !code.chars().all(|c| c.is_ascii_digit())
    {
        return (false, last_counter);
    }
    let now = (now_epoch() as u64) / TOTP_STEP;
    for offset in [-1i64, 0, 1] {
        let ctr_i64 = (now as i64) + offset;
        if ctr_i64 <= last_counter || ctr_i64 < 0 {
            continue;
        }
        if let Some(expected) = _totp_at(secret_b32, ctr_i64 as u64) {
            if bool::from(expected.as_bytes().ct_eq(code.as_bytes())) {
                return (true, ctr_i64);
            }
        }
    }
    (false, last_counter)
}

pub fn totp_uri(secret_b32: &str, account: &str, issuer: &str) -> String {
    let raw_label = format!("{}:{}", issuer, account);
    let label = urlencoding::encode(&raw_label);
    let iss = urlencoding::encode(issuer);
    format!(
        "otpauth://totp/{}?secret={}&issuer={}&digits={}&period={}",
        label, secret_b32, iss, TOTP_DIGITS, TOTP_STEP
    )
}

pub fn normalize_phone(phone: &str) -> String {
    let p = phone.trim();
    if p.is_empty() {
        return String::new();
    }
    let plus = p.starts_with('+');
    let digits: String = p.chars().filter(|c| c.is_ascii_digit()).collect();
    if plus {
        format!("+{}", digits)
    } else {
        digits
    }
}

pub fn slugify(text: &str) -> String {
    let mut s = String::with_capacity(text.len());
    for c in text.trim().chars() {
        let mapped = match c {
            'ç' | 'Ç' => 'c',
            'ğ' | 'Ğ' => 'g',
            'ı' | 'İ' | 'I' => 'i',
            'ö' | 'Ö' => 'o',
            'ş' | 'Ş' => 's',
            'ü' | 'Ü' => 'u',
            other => other,
        };
        for lc in mapped.to_lowercase() {
            s.push(lc);
        }
    }
    let re = Regex::new(r"[^a-z0-9]+").unwrap();
    let replaced = re.replace_all(&s, "-");
    let re2 = Regex::new(r"-{2,}").unwrap();
    let collapsed = re2.replace_all(&replaced, "-");
    collapsed.trim_matches('-').to_string()
}

/// Çıplak kullanıcı adını karşılaştırma/kayıt biçimine indirger: kırp + Türkçe→ASCII + küçük harf.
/// 'Şükrü' → 'sukru'. Harf/rakam dışı ('.', '_', '-') korunur. Python accounts.normalize_bare ile aynı.
pub fn normalize_bare(bare_username: &str) -> String {
    let mut s = String::new();
    for c in bare_username.trim().chars() {
        let mapped = match c {
            'ç' | 'Ç' => 'c',
            'ğ' | 'Ğ' => 'g',
            'ı' | 'İ' | 'I' => 'i',
            'ö' | 'Ö' => 'o',
            'ş' | 'Ş' => 's',
            'ü' | 'Ü' => 'u',
            other => other,
        };
        for lc in mapped.to_lowercase() {
            s.push(lc);
        }
    }
    s
}

pub fn compose_username(office_code: &str, bare_username: &str) -> String {
    format!("{}@{}", bare_username.trim(), slugify(office_code))
}

pub fn password_policy_error(password: &str) -> Option<String> {
    let p = password.trim();
    let min_len = min_password_length();
    if p.chars().count() < min_len {
        return Some(format!("Parola en az {} karakter olmalı.", min_len));
    }
    if !p.chars().any(|c| c.is_lowercase()) {
        return Some("Parola en az bir küçük harf içermeli.".to_string());
    }
    if !p.chars().any(|c| c.is_uppercase()) {
        return Some("Parola en az bir büyük harf içermeli.".to_string());
    }
    if !p.chars().any(|c| c.is_ascii_digit()) {
        return Some("Parola en az bir rakam içermeli.".to_string());
    }
    if !p.chars().any(|c| !c.is_alphanumeric()) {
        return Some("Parola en az bir özel işaret içermeli (ör. ! ? . - _ *).".to_string());
    }
    None
}

// ──────────────────────────────────────────────────────────────────────────────────────
// Kalıcılık arka uçları
// ──────────────────────────────────────────────────────────────────────────────────────
pub trait AccountBackend: Send + Sync {
    fn load(&self) -> Result<Value, String>;
    fn save(&self, doc: &Value) -> Result<(), String>;
}

pub struct FileBackend {
    pub path: PathBuf,
}

impl FileBackend {
    pub fn new(path: impl AsRef<Path>) -> Self {
        Self {
            path: path.as_ref().to_path_buf(),
        }
    }
}

impl AccountBackend for FileBackend {
    fn load(&self) -> Result<Value, String> {
        if !self.path.exists() {
            return Ok(json!({}));
        }
        match fs::read_to_string(&self.path) {
            Ok(txt) => {
                let v: Value = serde_json::from_str(&txt).unwrap_or_else(|_| json!({}));
                if v.is_object() {
                    Ok(v)
                } else {
                    Ok(json!({}))
                }
            }
            Err(_) => Ok(json!({})),
        }
    }

    fn save(&self, doc: &Value) -> Result<(), String> {
        if let Some(parent) = self.path.parent() {
            if !parent.as_os_str().is_empty() {
                fs::create_dir_all(parent).map_err(|e| e.to_string())?;
            }
        }
        let tmp = PathBuf::from(format!("{}.tmp", self.path.display()));
        let payload = serde_json::to_string_pretty(doc).map_err(|e| e.to_string())?;
        fs::write(&tmp, payload.as_bytes()).map_err(|e| e.to_string())?;
        fs::rename(&tmp, &self.path).map_err(|e| e.to_string())?;
        Ok(())
    }
}

pub struct PostgresBackend {
    pub dsn: String,
}

impl PostgresBackend {
    const KEY: &'static str = "accounts";
    const TABLE: &'static str = "uyap_kv";

    pub fn new(dsn: &str) -> Result<Self, String> {
        let me = Self {
            dsn: dsn.to_string(),
        };
        me.ensure_table()?;
        Ok(me)
    }

    fn connect(&self) -> Result<postgres::Client, String> {
        postgres::Client::connect(&self.dsn, postgres::NoTls).map_err(|e| e.to_string())
    }

    fn ensure_table(&self) -> Result<(), String> {
        let mut client = self.connect()?;
        let sql = format!(
            "CREATE TABLE IF NOT EXISTS {} (k TEXT PRIMARY KEY, v JSONB NOT NULL)",
            Self::TABLE
        );
        client.batch_execute(&sql).map_err(|e| e.to_string())?;
        Ok(())
    }
}

impl AccountBackend for PostgresBackend {
    fn load(&self) -> Result<Value, String> {
        let mut client = self.connect()?;
        let sql = format!("SELECT v FROM {} WHERE k = $1", Self::TABLE);
        let row = client
            .query_opt(&sql, &[&Self::KEY])
            .map_err(|e| e.to_string())?;
        match row {
            Some(r) => {
                let v: Value = r.get(0);
                if v.is_object() {
                    Ok(v)
                } else {
                    Ok(json!({}))
                }
            }
            None => Ok(json!({})),
        }
    }

    fn save(&self, doc: &Value) -> Result<(), String> {
        let mut client = self.connect()?;
        let payload = serde_json::to_string(doc).map_err(|e| e.to_string())?;
        let sql = format!(
            "INSERT INTO {} (k, v) VALUES ($1, $2::jsonb) \
             ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v",
            Self::TABLE
        );
        client
            .execute(&sql, &[&Self::KEY, &payload])
            .map_err(|e| e.to_string())?;
        Ok(())
    }
}

pub fn make_backend(path: &Path) -> Arc<dyn AccountBackend> {
    let db_url = database_url();
    if !db_url.is_empty() {
        match PostgresBackend::new(&db_url) {
            Ok(pg) => {
                println!("[+] Hesap deposu: PostgreSQL (kalıcı).");
                return Arc::new(pg);
            }
            Err(e) => {
                println!("[!] PostgreSQL bağlanamadı ({e}); dosya deposuna düşülüyor.");
            }
        }
    }
    println!(
        "[!] Hesap deposu: DOSYA ({}). DATABASE_URL yok — PaaS'ta EFEMERAL olabilir!",
        path.display()
    );
    Arc::new(FileBackend::new(path))
}

#[derive(Debug, Clone)]
pub struct AccountError(pub String);

impl fmt::Display for AccountError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for AccountError {}

#[derive(Debug, Clone)]
pub struct ResetTokenEntry {
    pub username: String,
    pub exp: i64,
}

pub struct AccountStore {
    pub path: PathBuf,
    pub backend: Arc<dyn AccountBackend>,
    pub offices: HashMap<String, Value>,
    pub users: HashMap<String, Value>,
    pub orders: HashMap<String, Value>,
    pub _room_index: HashMap<String, String>,
    pub _slug_index: HashMap<String, String>,
    pub reset_tokens: HashMap<String, ResetTokenEntry>,
}

impl AccountStore {
    pub fn new(path: Option<PathBuf>, backend: Option<Arc<dyn AccountBackend>>) -> Self {
        let p = path.unwrap_or_else(accounts_path);
        let b = backend.unwrap_or_else(|| make_backend(&p));
        let mut store = Self {
            path: p,
            backend: b,
            offices: HashMap::new(),
            users: HashMap::new(),
            orders: HashMap::new(),
            _room_index: HashMap::new(),
            _slug_index: HashMap::new(),
            reset_tokens: HashMap::new(),
        };
        store.load();
        store
    }

    pub fn load(&mut self) {
        let mut doc = match self.backend.load() {
            Ok(v) => v,
            Err(e) => {
                println!("[!] Hesap deposu yüklenemedi: {e}");
                json!({})
            }
        };
        if let Some(obj) = doc.as_object() {
            if !obj.contains_key("offices")
                && !obj.contains_key("users")
                && obj.contains_key("accounts")
            {
                println!(
                    "[!] Eski (v1) hesap formatı bulundu; v2'ye geçiş için yeni ofis/kullanıcı \
                     oluşturun. Eski kayıtlar yok sayıldı."
                );
                doc = json!({});
            }
        }
        self.offices = doc
            .get("offices")
            .and_then(|v| v.as_object())
            .map(|m| m.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
            .unwrap_or_default();
        self.users = doc
            .get("users")
            .and_then(|v| v.as_object())
            .map(|m| m.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
            .unwrap_or_default();
        self.orders = doc
            .get("orders")
            .and_then(|v| v.as_object())
            .map(|m| m.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
            .unwrap_or_default();
        if self._backfill_slugs() {
            if let Err(e) = self.save() {
                println!("[!] Slug geçişi kaydedilemedi: {e}");
            }
        }
        self._reindex();
    }

    pub fn _backfill_slugs(&mut self) -> bool {
        let mut changed = false;
        let mut used: HashSet<String> = self
            .offices
            .values()
            .filter_map(|o| o.get("slug").and_then(|v| v.as_str()).map(|s| s.to_string()))
            .filter(|s| !s.is_empty())
            .collect();
        for o in self.offices.values_mut() {
            let has_slug = o
                .get("slug")
                .and_then(|v| v.as_str())
                .map(|s| !s.is_empty())
                .unwrap_or(false);
            if has_slug {
                continue;
            }
            let lbl = o.get("label").and_then(|v| v.as_str()).unwrap_or("");
            let base = {
                let s = slugify(lbl);
                if s.is_empty() {
                    "ofis".to_string()
                } else {
                    s
                }
            };
            let mut slug = base.clone();
            let mut i = 2;
            while used.contains(&slug) {
                slug = format!("{}-{}", base, i);
                i += 1;
            }
            if let Some(obj) = o.as_object_mut() {
                obj.insert("slug".to_string(), json!(slug));
            }
            used.insert(slug);
            changed = true;
        }
        changed
    }

    pub fn _reindex(&mut self) {
        self._room_index.clear();
        self._slug_index.clear();
        for (oid, o) in &self.offices {
            if let Some(rk) = o.get("room_key").and_then(|v| v.as_str()) {
                if !rk.is_empty() {
                    self._room_index.insert(rk.to_string(), oid.clone());
                }
            }
            if let Some(sl) = o.get("slug").and_then(|v| v.as_str()) {
                if !sl.is_empty() {
                    self._slug_index.insert(sl.to_string(), oid.clone());
                }
            }
        }
    }

    pub fn _unique_slug(&self, base_text: &str) -> String {
        let base = {
            let s = slugify(base_text);
            if s.is_empty() {
                "ofis".to_string()
            } else {
                s
            }
        };
        let used: HashSet<&str> = self
            .offices
            .values()
            .filter_map(|o| o.get("slug").and_then(|v| v.as_str()))
            .filter(|s| !s.is_empty())
            .collect();
        if !used.contains(base.as_str()) {
            return base;
        }
        let mut i = 2;
        loop {
            let cand = format!("{}-{}", base, i);
            if !used.contains(cand.as_str()) {
                return cand;
            }
            i += 1;
        }
    }

    pub fn save(&self) -> Result<(), String> {
        let mut offices_m = Map::new();
        for (k, v) in &self.offices {
            offices_m.insert(k.clone(), v.clone());
        }
        let mut users_m = Map::new();
        for (k, v) in &self.users {
            users_m.insert(k.clone(), v.clone());
        }
        let mut orders_m = Map::new();
        for (k, v) in &self.orders {
            orders_m.insert(k.clone(), v.clone());
        }
        let doc = json!({
            "offices": offices_m,
            "users": users_m,
            "orders": orders_m,
        });
        self.backend.save(&doc)
    }

    fn save_quiet(&self) {
        let _ = self.save();
    }

    // ── Sorgular ────────────────────────────────────────────────────────────────────
    pub fn is_empty(&self) -> bool {
        !self
            .users
            .values()
            .any(|r| r.get("role").and_then(|v| v.as_str()) != Some("owner"))
    }

    pub fn get_office(&self, office_id: &str) -> Option<&Value> {
        self.offices.get(office_id)
    }

    pub fn office_by_room_key(&self, room_key: &str) -> Option<&Value> {
        let oid = self._room_index.get(room_key)?;
        self.offices.get(oid)
    }

    /// Girişte yazılan kimliği depodaki GERÇEK kullanıcı anahtarına çözer (Python resolve_username ile
    /// aynı kurallar; 2026-11-14 "@savas eşleşmiyor"). Anahtarlar yeniden adlandırılmaz.
    pub fn resolve_username(&self, raw: &str, office_code: &str) -> Option<String> {
        let raw = raw.trim();
        if self.users.contains_key(raw) {
            return Some(raw.to_string());
        }
        let (bare, ofis) = match raw.rsplit_once('@') {
            Some((b, o)) => (b, o),
            None => (raw, office_code.trim()),
        };
        let (bare_n, ofis_n) = (normalize_bare(bare), slugify(ofis));
        if bare_n.is_empty() || ofis_n.is_empty() {
            return None;
        }
        let slug_of = |o: &Value| o.get("slug").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let mut adaylar: Vec<&String> = self.offices.iter().filter(|(_, o)| slug_of(o) == ofis_n).map(|(k, _)| k).collect();
        if adaylar.is_empty() {
            adaylar = self
                .offices
                .iter()
                .filter(|(_, o)| slugify(o.get("label").and_then(|v| v.as_str()).unwrap_or("")) == ofis_n)
                .map(|(k, _)| k)
                .collect();
        }
        if adaylar.is_empty() {
            let onek = format!("{ofis_n}-");
            let onekli: Vec<&String> = self.offices.iter().filter(|(_, o)| slug_of(o).starts_with(&onek)).map(|(k, _)| k).collect();
            if onekli.len() == 1 {
                adaylar = onekli;
            }
        }
        if adaylar.len() != 1 {
            return None;
        }
        let oid = adaylar[0].as_str();
        let bulunan: Vec<&String> = self
            .users
            .iter()
            .filter(|(k, u)| {
                u.get("office_id").and_then(|v| v.as_str()) == Some(oid)
                    && k.rsplit_once('@').map(|(b, _)| normalize_bare(b) == bare_n).unwrap_or(false)
            })
            .map(|(k, _)| k)
            .collect();
        if bulunan.len() == 1 { Some(bulunan[0].clone()) } else { None }
    }

    pub fn office_by_slug(&self, slug: &str) -> Option<&Value> {
        let oid = self._slug_index.get(&slugify(slug))?;
        self.offices.get(oid)
    }

    // ── Ofis işlemleri (vendor /admin) ────────────────────────────────────────────────
    #[allow(clippy::too_many_arguments)]
    pub fn create_office(
        &mut self,
        label: &str,
        master_username: &str,
        master_password: Option<&str>,
        room_key: Option<&str>,
        master_email: &str,
        reset_to_master: bool,
        slug: Option<&str>,
        master_password_hash: Option<Value>,
        phone: &str,
    ) -> Result<Value, AccountError> {
        let master_username_n = normalize_bare(master_username); // 'Utku' → 'utku' (2026-11-14)
        let master_username = master_username_n.as_str();
        if master_username.is_empty() {
            return Err(AccountError("Master kullanıcı adı gerekli.".to_string()));
        }
        if master_username.contains('@') {
            return Err(AccountError(
                "Kullanıcı adı '@' içeremez (e-posta değil, çıplak ad girin).".to_string(),
            ));
        }
        let base_slug_src = slug
            .filter(|s| !s.is_empty())
            .or(if !label.is_empty() { Some(label) } else { None })
            .unwrap_or(master_username);
        let office_slug = self._unique_slug(base_slug_src);
        let scoped_master = compose_username(&office_slug, master_username);
        if self.users.contains_key(&scoped_master) {
            return Err(AccountError(format!("Kullanıcı adı dolu: {}", scoped_master)));
        }
        let mut office_id = generate_office_id();
        while self.offices.contains_key(&office_id) {
            office_id = generate_office_id();
        }
        let mut rk = room_key
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string())
            .unwrap_or_else(generate_room_key);
        while self._room_index.contains_key(&rk) {
            rk = generate_room_key();
        }

        let (pw_rec, plain_pw): (Value, Option<String>) = if let Some(h) = master_password_hash {
            (h, None)
        } else {
            let plain = match master_password.filter(|s| !s.is_empty()) {
                Some(p) => {
                    if let Some(err) = password_policy_error(p) {
                        return Err(AccountError(err));
                    }
                    p.to_string()
                }
                None => generate_password(),
            };
            (_hash_password(&plain, None, _PBKDF2_ITERS), Some(plain))
        };

        let now = now_epoch();
        self.offices.insert(
            office_id.clone(),
            json!({
                "label": label,
                "slug": office_slug,
                "room_key": rk,
                "active": true,
                "created": now,
                "rotated": now,
                "reset_to_master": reset_to_master,
            }),
        );
        self.users.insert(
            scoped_master.clone(),
            json!({
                "office_id": office_id,
                "role": "master",
                "password": pw_rec,
                "label": label,
                "email": master_email.trim(),
                "phone": normalize_phone(phone),
                "active": true,
                "created": now,
            }),
        );
        self._reindex();
        self.save_quiet();
        Ok(json!({
            "office_id": office_id,
            "room_key": rk,
            "office_code": office_slug,
            "master_username": scoped_master,
            "master_login": master_username,
            "password": plain_pw,
            "label": label,
        }))
    }

    pub fn mark_agent_seen(&mut self, office_id: &str) {
        if let Some(o) = self.offices.get_mut(office_id) {
            if let Some(obj) = o.as_object_mut() {
                obj.insert("agent_last_seen".to_string(), json!(now_epoch()));
            }
            self.save_quiet();
        }
    }

    pub fn agent_last_seen(&self, office_id: &str) -> Option<i64> {
        self.offices
            .get(office_id)
            .and_then(|o| o.get("agent_last_seen"))
            .and_then(|v| v.as_i64())
    }

    pub fn set_office_active(&mut self, office_id: &str, active: bool) -> bool {
        if let Some(o) = self.offices.get_mut(office_id) {
            if let Some(obj) = o.as_object_mut() {
                obj.insert("active".to_string(), json!(active));
            }
            self.save_quiet();
            true
        } else {
            false
        }
    }

    pub fn delete_office(&mut self, office_id: &str) -> bool {
        if self.offices.remove(office_id).is_none() {
            return false;
        }
        self.users
            .retain(|_, r| r.get("office_id").and_then(|v| v.as_str()) != Some(office_id));
        self._reindex();
        self.save_quiet();
        true
    }

    pub fn rotate_room_key(&mut self, office_id: &str) -> Option<String> {
        if !self.offices.contains_key(office_id) {
            return None;
        }
        let mut new_key = generate_room_key();
        while self._room_index.contains_key(&new_key) {
            new_key = generate_room_key();
        }
        if let Some(o) = self.offices.get_mut(office_id) {
            if let Some(obj) = o.as_object_mut() {
                obj.insert("room_key".to_string(), json!(new_key));
                obj.insert("rotated".to_string(), json!(now_epoch()));
            }
        }
        self._reindex();
        self.save_quiet();
        Some(new_key)
    }

    // ── Siparişler ──────────────────────────────────────────────────────────────────
    #[allow(clippy::too_many_arguments)]
    pub fn create_order(
        &mut self,
        label: &str,
        master_username: &str,
        email: &str,
        plan: &str,
        amount: &str,
        provider: &str,
        phone: &str,
        password: Option<&str>,
        slug: Option<&str>,
    ) -> Result<Value, AccountError> {
        let master_username = master_username.trim();
        if master_username.is_empty() {
            return Err(AccountError("Kullanıcı adı gerekli.".to_string()));
        }
        if master_username.contains('@') {
            return Err(AccountError(
                "Kullanıcı adı '@' içeremez (e-posta değil, çıplak ad girin).".to_string(),
            ));
        }
        let pw_hash = match password.filter(|s| !s.is_empty()) {
            Some(p) => {
                if let Some(err) = password_policy_error(p) {
                    return Err(AccountError(err));
                }
                Some(_hash_password(p, None, _PBKDF2_ITERS))
            }
            None => None,
        };
        let mut oid = generate_order_id();
        while self.orders.contains_key(&oid) {
            oid = generate_order_id();
        }
        let now = now_epoch();
        let slug_val = slug.map(slugify).unwrap_or_default();
        let prov = if provider.trim().is_empty() {
            "manual"
        } else {
            provider.trim()
        };
        let rec = json!({
            "order_id": oid,
            "status": "pending",
            "label": label.trim(),
            "master_username": master_username,
            "slug": slug_val,
            "email": email.trim(),
            "phone": normalize_phone(phone),
            "password_hash": pw_hash,
            "plan": plan.trim(),
            "amount": amount.trim(),
            "provider": prov,
            "created": now,
            "updated": now,
            "office_id": "",
            "office_code": "",
        });
        self.orders.insert(oid, rec.clone());
        self.save_quiet();
        Ok(rec)
    }

    pub fn slug_taken(&self, slug: &str) -> bool {
        let s = slugify(slug);
        if s.is_empty() {
            return false;
        }
        if self._slug_index.contains_key(&s) {
            return true;
        }
        for o in self.orders.values() {
            if o.get("status").and_then(|v| v.as_str()) == Some("pending") {
                let osl = o
                    .get("slug")
                    .and_then(|v| v.as_str())
                    .filter(|x| !x.is_empty())
                    .or_else(|| o.get("label").and_then(|v| v.as_str()))
                    .unwrap_or("");
                if slugify(osl) == s {
                    return true;
                }
            }
        }
        false
    }

    pub fn get_order(&self, order_id: &str) -> Option<&Value> {
        self.orders.get(order_id.trim())
    }

    pub fn update_order(&mut self, order_id: &str, fields: Vec<(&str, Value)>) -> Option<Value> {
        let rec = self.orders.get_mut(order_id.trim())?;
        if let Some(obj) = rec.as_object_mut() {
            for (k, v) in fields {
                obj.insert(k.to_string(), v);
            }
            obj.insert("updated".to_string(), json!(now_epoch()));
        }
        let cloned = rec.clone();
        self.save_quiet();
        Some(cloned)
    }

    pub fn delete_order(&mut self, order_id: &str) -> bool {
        if self.orders.remove(order_id.trim()).is_some() {
            self.save_quiet();
            true
        } else {
            false
        }
    }

    pub fn listing_orders(&self) -> Vec<Value> {
        let mut out: Vec<Value> = self.orders.values().cloned().collect();
        out.sort_by(|a, b| {
            let ca = a.get("created").and_then(|v| v.as_i64()).unwrap_or(0);
            let cb = b.get("created").and_then(|v| v.as_i64()).unwrap_or(0);
            cb.cmp(&ca)
        });
        out
    }

    // ── Kullanıcı işlemleri ─────────────────────────────────────────────────────────
    #[allow(clippy::too_many_arguments)]
    pub fn create_user(
        &mut self,
        office_id: &str,
        username: &str,
        password: Option<&str>,
        role: &str,
        label: &str,
        email: &str,
        phone: &str,
    ) -> Result<Value, AccountError> {
        // Yeni kullanıcılar normalize adla ('Ahmet' → 'ahmet'; Python ile aynı, 2026-11-14).
        let username_n = normalize_bare(username);
        let username = username_n.as_str();
        if username.is_empty() {
            return Err(AccountError("Kullanıcı adı gerekli.".to_string()));
        }
        if username.contains('@') {
            return Err(AccountError(
                "Kullanıcı adı '@' içeremez (e-posta değil, çıplak ad girin).".to_string(),
            ));
        }
        let office_slug = {
            let Some(office) = self.offices.get(office_id) else {
                return Err(AccountError("Ofis bulunamadı.".to_string()));
            };
            office
                .get("slug")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string()
        };
        let office_slug = if office_slug.is_empty() {
            let lbl = self
                .offices
                .get(office_id)
                .and_then(|o| o.get("label").and_then(|v| v.as_str()))
                .filter(|s| !s.is_empty())
                .unwrap_or(office_id)
                .to_string();
            let s = self._unique_slug(&lbl);
            if let Some(o) = self.offices.get_mut(office_id) {
                if let Some(obj) = o.as_object_mut() {
                    obj.insert("slug".to_string(), json!(s));
                }
            }
            self._reindex();
            s
        } else {
            office_slug
        };

        let scoped = compose_username(&office_slug, username);
        let normalize_cakisma = self.users.iter().any(|(k, u)| {
            u.get("office_id").and_then(|v| v.as_str()) == Some(office_id)
                && k.rsplit_once('@').map(|(b, _)| normalize_bare(b) == username).unwrap_or(false)
        });
        if self.users.contains_key(&scoped) || normalize_cakisma {
            return Err(AccountError(format!("Kullanıcı adı dolu: {}", scoped)));
        }
        let role = if role == "master" || role == "member" {
            role
        } else {
            "member"
        };
        let plain_pw = match password.filter(|s| !s.is_empty()) {
            Some(p) => {
                if let Some(err) = password_policy_error(p) {
                    return Err(AccountError(err));
                }
                p.to_string()
            }
            None => generate_password(),
        };
        self.users.insert(
            scoped.clone(),
            json!({
                "office_id": office_id,
                "role": role,
                "password": _hash_password(&plain_pw, None, _PBKDF2_ITERS),
                "label": label,
                "email": email.trim(),
                "phone": normalize_phone(phone),
                "active": true,
                "created": now_epoch(),
            }),
        );
        self.save_quiet();
        Ok(json!({
            "username": scoped,
            "login": username,
            "office_code": office_slug,
            "password": plain_pw,
            "role": role,
        }))
    }

    pub fn set_user_email(&mut self, username: &str, email: &str) -> bool {
        let Some(u) = self.users.get_mut(username) else {
            return false;
        };
        if let Some(obj) = u.as_object_mut() {
            obj.insert("email".to_string(), json!(email.trim()));
        }
        self.save_quiet();
        true
    }

    pub fn set_office_reset_policy(&mut self, office_id: &str, reset_to_master: bool) -> bool {
        let Some(o) = self.offices.get_mut(office_id) else {
            return false;
        };
        if let Some(obj) = o.as_object_mut() {
            obj.insert("reset_to_master".to_string(), json!(reset_to_master));
        }
        self.save_quiet();
        true
    }

    pub fn set_user_active(&mut self, username: &str, active: bool) -> Result<bool, AccountError> {
        let Some(u) = self.users.get_mut(username) else {
            return Ok(false);
        };
        if u.get("role").and_then(|v| v.as_str()) == Some("owner") {
            return Err(AccountError(
                "Owner (platform sahibi) hesabı pasifleştirilemez.".to_string(),
            ));
        }
        if let Some(obj) = u.as_object_mut() {
            obj.insert("active".to_string(), json!(active));
        }
        self.save_quiet();
        Ok(true)
    }

    pub fn bump_sess_epoch(&mut self, username: &str) {
        if let Some(u) = self.users.get_mut(username) {
            let cur = u.get("sess_epoch").and_then(|v| v.as_i64()).unwrap_or(0);
            if let Some(obj) = u.as_object_mut() {
                obj.insert("sess_epoch".to_string(), json!(cur + 1));
            }
            self.save_quiet();
        }
    }

    pub fn reset_user_password(
        &mut self,
        username: &str,
        password: Option<&str>,
    ) -> Result<Option<String>, AccountError> {
        if !self.users.contains_key(username) {
            return Ok(None);
        }
        let plain_pw = match password.filter(|s| !s.is_empty()) {
            Some(p) => {
                if let Some(err) = password_policy_error(p) {
                    return Err(AccountError(err));
                }
                p.to_string()
            }
            None => generate_password(),
        };
        let hashed = _hash_password(&plain_pw, None, _PBKDF2_ITERS);
        if let Some(u) = self.users.get_mut(username) {
            let cur = u.get("sess_epoch").and_then(|v| v.as_i64()).unwrap_or(0);
            if let Some(obj) = u.as_object_mut() {
                obj.insert("password".to_string(), hashed);
                obj.insert("sess_epoch".to_string(), json!(cur + 1));
            }
        }
        self.save_quiet();
        Ok(Some(plain_pw))
    }

    pub fn delete_user(&mut self, username: &str) -> Result<bool, AccountError> {
        let Some(u) = self.users.get(username) else {
            return Ok(false);
        };
        let role = u.get("role").and_then(|v| v.as_str()).unwrap_or("");
        if role == "owner" {
            return Err(AccountError(
                "Owner (platform sahibi) hesabı silinemez.".to_string(),
            ));
        }
        if role == "master" {
            let oid = u.get("office_id").and_then(|v| v.as_str()).unwrap_or("");
            let master_count = self
                .users
                .values()
                .filter(|r| {
                    r.get("office_id").and_then(|v| v.as_str()) == Some(oid)
                        && r.get("role").and_then(|v| v.as_str()) == Some("master")
                })
                .count();
            if master_count <= 1 {
                return Err(AccountError(
                    "Ofisin tek master'ı silinemez.".to_string(),
                ));
            }
        }
        self.users.remove(username);
        self.save_quiet();
        Ok(true)
    }

    // ── Owner ───────────────────────────────────────────────────────────────────────
    pub fn get_owner(&self, username: Option<&str>) -> Option<&Value> {
        let uname = username.map(|s| s.to_string()).unwrap_or_else(owner_username);
        self.users.get(&uname)
    }

    pub fn ensure_owner(
        &mut self,
        password: &str,
        username: Option<&str>,
        totp_secret: Option<&str>,
    ) -> Result<Value, AccountError> {
        let uname = username
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(owner_username);
        if uname.contains('@') {
            return Err(AccountError("Owner kullanıcı adı '@' içeremez.".to_string()));
        }
        if let Some(u) = self.users.get(&uname) {
            if u.get("role").and_then(|v| v.as_str()) != Some("owner") {
                return Err(AccountError(format!(
                    "'{}' zaten owner olmayan bir hesap; owner bootstrap iptal.",
                    uname
                )));
            }
            return Ok(json!({"username": uname, "created": false}));
        }
        if let Some(err) = password_policy_error(password) {
            return Err(AccountError(err));
        }
        let secret = totp_secret
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string())
            .unwrap_or_else(generate_totp_secret);
        self.users.insert(
            uname.clone(),
            json!({
                "office_id": "",
                "role": "owner",
                "password": _hash_password(password, None, _PBKDF2_ITERS),
                "label": "Platform Sahibi",
                "email": "",
                "phone": "",
                "totp_secret": secret,
                "totp_last": -1,
                "active": true,
                "created": now_epoch(),
            }),
        );
        self.save_quiet();
        Ok(json!({"username": uname, "totp_secret": secret, "created": true}))
    }

    pub fn sync_owner_password(&mut self, username: &str, password: &str) -> Result<bool, AccountError> {
        let Some(u) = self.users.get(username) else {
            return Ok(false);
        };
        if u.get("role").and_then(|v| v.as_str()) != Some("owner") {
            return Ok(false);
        }
        let pw_rec = u.get("password").cloned().unwrap_or(Value::Null);
        if _verify_password(password, &pw_rec) {
            return Ok(false);
        }
        if let Some(err) = password_policy_error(password) {
            return Err(AccountError(err));
        }
        let hashed = _hash_password(password, None, _PBKDF2_ITERS);
        if let Some(u_mut) = self.users.get_mut(username) {
            let cur = u_mut.get("sess_epoch").and_then(|v| v.as_i64()).unwrap_or(0);
            if let Some(obj) = u_mut.as_object_mut() {
                obj.insert("password".to_string(), hashed);
                obj.insert("sess_epoch".to_string(), json!(cur + 1));
            }
        }
        self.save_quiet();
        Ok(true)
    }

    pub fn reactivate_owner(&mut self, username: &str) -> bool {
        let Some(u) = self.users.get_mut(username) else {
            return false;
        };
        if u.get("role").and_then(|v| v.as_str()) != Some("owner") {
            return false;
        }
        if u.get("active").and_then(|v| v.as_bool()).unwrap_or(true) {
            return false;
        }
        if let Some(obj) = u.as_object_mut() {
            obj.insert("active".to_string(), json!(true));
        }
        self.save_quiet();
        true
    }

    pub fn set_owner_totp_secret(&mut self, username: &str, secret_b32: &str) -> Result<bool, AccountError> {
        let Some(u) = self.users.get(username) else {
            return Ok(false);
        };
        if u.get("role").and_then(|v| v.as_str()) != Some("owner") {
            return Ok(false);
        }
        let s = secret_b32.trim().replace(' ', "").trim_end_matches('=').to_uppercase();
        let decoded = BASE32_NOPAD.decode(s.as_bytes()).map_err(|_| {
            AccountError("TOTP anahtarı geçerli base32 değil ya da çok kısa (en az 16 karakter).".to_string())
        })?;
        if decoded.len() < 10 {
            return Err(AccountError(
                "TOTP anahtarı geçerli base32 değil ya da çok kısa (en az 16 karakter).".to_string(),
            ));
        }
        if let Some(u_mut) = self.users.get_mut(username) {
            if let Some(obj) = u_mut.as_object_mut() {
                obj.insert("totp_secret".to_string(), json!(s));
                obj.insert("totp_last".to_string(), json!(-1));
            }
        }
        self.save_quiet();
        Ok(true)
    }

    pub fn set_owner_totp_last(&mut self, username: &str, counter: i64) -> bool {
        let Some(u) = self.users.get_mut(username) else {
            return false;
        };
        if u.get("role").and_then(|v| v.as_str()) != Some("owner") {
            return false;
        }
        if let Some(obj) = u.as_object_mut() {
            obj.insert("totp_last".to_string(), json!(counter));
        }
        self.save_quiet();
        true
    }

    pub fn verify_owner_totp(&mut self, username: &str, code: &str) -> bool {
        let Some(u) = self.users.get(username) else {
            return false;
        };
        if u.get("role").and_then(|v| v.as_str()) != Some("owner") {
            return false;
        }
        let secret = u.get("totp_secret").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let last = u.get("totp_last").and_then(|v| v.as_i64()).unwrap_or(-1);
        let (ok, used) = totp_verify(&secret, code, last);
        if ok {
            self.set_owner_totp_last(username, used);
        }
        ok
    }

    // ── Doğrulama (signaling) ─────────────────────────────────────────────────────────
    pub fn authenticate(&mut self, username: &str, password: &str) -> (bool, String, Option<Value>) {
        let uname = username.trim().to_string();
        let u_opt = self.users.get(&uname).cloned();
        let rec = u_opt
            .as_ref()
            .and_then(|u| u.get("password"))
            .filter(|v| !v.is_null())
            .unwrap_or(&*_DUMMY_PW_REC);
        let pw_ok = _verify_password(password, rec);
        let Some(u) = u_opt.as_ref() else {
            return (false, _GENERIC_AUTH_FAIL.to_string(), None);
        };
        if !pw_ok {
            return (false, _GENERIC_AUTH_FAIL.to_string(), None);
        }
        let iters = rec.get("iters").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
        if iters < _PBKDF2_ITERS {
            let new_hash = _hash_password(password, None, _PBKDF2_ITERS);
            if let Some(u_mut) = self.users.get_mut(&uname) {
                if let Some(obj) = u_mut.as_object_mut() {
                    obj.insert("password".to_string(), new_hash);
                }
            }
            if let Err(e) = self.save() {
                println!("[!] Parola özeti yükseltilemedi ({}): {}", uname, e);
            }
        }
        if !u.get("active").and_then(|v| v.as_bool()).unwrap_or(true) {
            return (false, "Kullanıcı pasif (askıda/iptal).".to_string(), None);
        }
        if u.get("role").and_then(|v| v.as_str()) == Some("owner") {
            return (
                true,
                "Başarılı".to_string(),
                Some(json!({
                    "username": uname,
                    "office_id": "",
                    "room_key": "",
                    "role": "owner",
                    "office_label": "",
                    "office_code": "",
                })),
            );
        }
        let oid = u.get("office_id").and_then(|v| v.as_str()).unwrap_or("");
        let Some(office) = self.offices.get(oid) else {
            return (false, "Bağlı ofis bulunamadı.".to_string(), None);
        };
        if !office.get("active").and_then(|v| v.as_bool()).unwrap_or(true) {
            return (false, "Ofis lisansı pasif.".to_string(), None);
        }
        (
            true,
            "Başarılı".to_string(),
            Some(json!({
                "username": uname,
                "office_id": oid,
                "room_key": office.get("room_key").and_then(|v| v.as_str()).unwrap_or(""),
                "role": u.get("role").and_then(|v| v.as_str()).unwrap_or("member"),
                "office_label": office.get("label").and_then(|v| v.as_str()).unwrap_or(""),
                "office_code": office.get("slug").and_then(|v| v.as_str()).unwrap_or(""),
            })),
        )
    }

    // ── Parola sıfırlama ──────────────────────────────────────────────────────────────
    pub fn request_password_reset(&mut self, identifier: &str) -> (Option<Value>, Option<String>) {
        let ident = identifier.trim();
        if ident.is_empty() {
            return (None, Some("Kullanıcı adı veya e-posta gerekli.".to_string()));
        }
        let mut found: Option<(String, Value)> = self
            .users
            .get(ident)
            .map(|u| (ident.to_string(), u.clone()));
        if found.is_none() {
            let ident_low = ident.to_lowercase();
            for (n, r) in &self.users {
                let em = r.get("email").and_then(|v| v.as_str()).unwrap_or("").trim().to_lowercase();
                if !em.is_empty() && em == ident_low {
                    found = Some((n.clone(), r.clone()));
                    break;
                }
            }
        }
        let Some((uname, user)) = found else {
            return (None, Some("Kullanıcı bulunamadı.".to_string()));
        };
        let oid = user.get("office_id").and_then(|v| v.as_str()).unwrap_or("");
        let office = self.offices.get(oid);
        let to_master = office
            .and_then(|o| o.get("reset_to_master"))
            .and_then(|v| v.as_bool())
            .unwrap_or(true);
        let (recipient_email, recipient_username) = if to_master {
            let m = self.users.iter().find(|(_, r)| {
                r.get("office_id").and_then(|v| v.as_str()) == Some(oid)
                    && r.get("role").and_then(|v| v.as_str()) == Some("master")
            });
            match m {
                Some((mn, mr)) => (
                    mr.get("email").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                    mn.clone(),
                ),
                None => (String::new(), String::new()),
            }
        } else {
            (
                user.get("email").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                uname.clone(),
            )
        };
        if recipient_email.is_empty() {
            return (
                None,
                Some("Alıcı e-posta adresi tanımlı değil (yöneticiden e-posta eklemesini isteyin).".to_string()),
            );
        }
        let token = token_urlsafe(24);
        self.reset_tokens.insert(
            token.clone(),
            ResetTokenEntry {
                username: uname.clone(),
                exp: now_epoch() + 3600,
            },
        );
        (
            Some(json!({
                "token": token,
                "target_username": uname,
                "recipient_email": recipient_email,
                "recipient_username": recipient_username,
                "to_master": to_master,
            })),
            None,
        )
    }

    pub fn peek_reset_token(&self, token: &str) -> Option<String> {
        let rec = self.reset_tokens.get(token.trim())?;
        if rec.exp < now_epoch() {
            return None;
        }
        Some(rec.username.clone())
    }

    pub fn reset_password_with_token(&mut self, token: &str, new_password: &str) -> (bool, String) {
        let tok = token.trim();
        let rec = self.reset_tokens.get(tok).cloned();
        let Some(rec) = rec else {
            return (
                false,
                "Sıfırlama bağlantısı geçersiz ya da süresi dolmuş.".to_string(),
            );
        };
        if rec.exp < now_epoch() {
            self.reset_tokens.remove(tok);
            return (
                false,
                "Sıfırlama bağlantısı geçersiz ya da süresi dolmuş.".to_string(),
            );
        }
        let new_pw = new_password.trim();
        if let Some(err) = password_policy_error(new_pw) {
            return (false, err);
        }
        if !self.users.contains_key(&rec.username) {
            self.reset_tokens.remove(tok);
            return (false, "Kullanıcı bulunamadı.".to_string());
        }
        let hashed = _hash_password(new_pw, None, _PBKDF2_ITERS);
        if let Some(u) = self.users.get_mut(&rec.username) {
            let cur = u.get("sess_epoch").and_then(|v| v.as_i64()).unwrap_or(0);
            if let Some(obj) = u.as_object_mut() {
                obj.insert("password".to_string(), hashed);
                obj.insert("sess_epoch".to_string(), json!(cur + 1));
            }
        }
        self.reset_tokens.remove(tok);
        self.save_quiet();
        (true, rec.username)
    }

    // ── Listeler ────────────────────────────────────────────────────────────────────
    pub fn listing_offices(&self) -> Vec<Value> {
        let mut items: Vec<(&String, &Value)> = self.offices.iter().collect();
        items.sort_by(|a, b| {
            let ca = a.1.get("created").and_then(|v| v.as_i64()).unwrap_or(0);
            let cb = b.1.get("created").and_then(|v| v.as_i64()).unwrap_or(0);
            cb.cmp(&ca)
        });
        items
            .into_iter()
            .map(|(oid, o)| {
                let members_count = self
                    .users
                    .values()
                    .filter(|r| r.get("office_id").and_then(|v| v.as_str()) == Some(oid.as_str()))
                    .count();
                let master = self
                    .users
                    .iter()
                    .find(|(_, r)| {
                        r.get("office_id").and_then(|v| v.as_str()) == Some(oid.as_str())
                            && r.get("role").and_then(|v| v.as_str()) == Some("master")
                    })
                    .map(|(u, _)| u.as_str())
                    .unwrap_or("");
                let master_email = if !master.is_empty() {
                    self.users
                        .get(master)
                        .and_then(|u| u.get("email").and_then(|v| v.as_str()))
                        .unwrap_or("")
                } else {
                    ""
                };
                json!({
                    "office_id": oid,
                    "label": o.get("label").and_then(|v| v.as_str()).unwrap_or(""),
                    "slug": o.get("slug").and_then(|v| v.as_str()).unwrap_or(""),
                    "room_key": o.get("room_key").and_then(|v| v.as_str()).unwrap_or(""),
                    "master_username": master,
                    "master_email": master_email,
                    "reset_to_master": o.get("reset_to_master").and_then(|v| v.as_bool()).unwrap_or(true),
                    "active": o.get("active").and_then(|v| v.as_bool()).unwrap_or(true),
                    "created": o.get("created").and_then(|v| v.as_i64()).unwrap_or(0),
                    "rotated": o.get("rotated").and_then(|v| v.as_i64()).unwrap_or(0),
                    "user_count": members_count,
                })
            })
            .collect()
    }

    pub fn listing_users(&self, office_id: &str) -> Vec<Value> {
        let mut items: Vec<(&String, &Value)> = self
            .users
            .iter()
            .filter(|(_, r)| r.get("office_id").and_then(|v| v.as_str()) == Some(office_id))
            .collect();
        items.sort_by(|a, b| {
            let ca = a.1.get("created").and_then(|v| v.as_i64()).unwrap_or(0);
            let cb = b.1.get("created").and_then(|v| v.as_i64()).unwrap_or(0);
            cb.cmp(&ca)
        });
        items
            .into_iter()
            .map(|(uname, r)| {
                json!({
                    "username": uname,
                    "label": r.get("label").and_then(|v| v.as_str()).unwrap_or(""),
                    "email": r.get("email").and_then(|v| v.as_str()).unwrap_or(""),
                    "phone": r.get("phone").and_then(|v| v.as_str()).unwrap_or(""),
                    "role": r.get("role").and_then(|v| v.as_str()).unwrap_or("member"),
                    "active": r.get("active").and_then(|v| v.as_bool()).unwrap_or(true),
                    "created": r.get("created").and_then(|v| v.as_i64()).unwrap_or(0),
                })
            })
            .collect()
    }
}
