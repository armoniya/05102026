//! /zs İş Takip — belge deposu (zs_r2.py 1:1 port)
//! ------------------------------------------------
//! Belgeler müvekkil bilgisi içerebilir → sunucuda AES-256-GCM ile şifrelenir, depoya yalnız
//! şifreli bayt yazılır. Anahtar: ZS_FILE_KEY (base64, 32 bayt). KAYBOLURSA BELGELER AÇILAMAZ.
//!
//! Depo:
//!   • Cloudflare R2 (S3 uyumlu) — R2_ACCOUNT_ID + R2_ACCESS_KEY_ID + R2_SECRET_ACCESS_KEY + R2_BUCKET
//!     ayarlıysa. AB yetki alanlı bucket için R2_ENDPOINT=https://<hesap>.eu.r2.cloudflarestorage.com.
//!     İmza (SigV4) hmac/sha2 ile atılır.
//!   • Yerel klasör (zs_files/) — R2 ayarlı değilse ve Render'da DEĞİLSEK (yalnız geliştirme).
//!   • Render'da R2 yoksa belge yükleme KAPALI (efemeral diske müvekkil belgesi yazılmaz).
//!
//! Şifreli biçim: b"ZS1" + 12 bayt nonce + şifreli metin(+16 bayt etiket); AAD = depolama anahtarı
//! (bir nesne başka bir kayıtla yer değiştirilirse çözülmez).

use std::collections::BTreeMap;
use std::env;
use std::fmt;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use aes_gcm::aead::{Aead, KeyInit, Payload};
use aes_gcm::{Aes256Gcm, Nonce};
use base64::{engine::general_purpose::STANDARD as B64, Engine as _};
use chrono::Utc;
use hmac::{Hmac, Mac};
use percent_encoding::{utf8_percent_encode, AsciiSet, NON_ALPHANUMERIC};
use rand::RngCore;
use sha2::{Digest, Sha256};

use crate::accounts;

pub const MAGIC: &[u8; 3] = b"ZS1";

type HmacSha256 = Hmac<Sha256>;

/// `urllib.parse.quote(..., safe="/-_.~")` karşılığı
const S3_SAFE: &AsciiSet = &NON_ALPHANUMERIC
    .remove(b'/')
    .remove(b'-')
    .remove(b'_')
    .remove(b'.')
    .remove(b'~');

#[derive(Debug, Clone)]
pub struct StorageError(pub String);

impl fmt::Display for StorageError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for StorageError {}

/// ZS_FILE_KEY'i çözer. Yoksa/bozuksa None (yükleme kapalı).
pub fn load_key() -> Option<[u8; 32]> {
    let raw = env::var("ZS_FILE_KEY").ok()?.trim().to_string();
    if raw.is_empty() {
        return None;
    }
    let key = B64.decode(raw.as_bytes()).ok()?;
    if key.len() != 32 {
        return None;
    }
    let mut out = [0u8; 32];
    out.copy_from_slice(&key);
    Some(out)
}

pub fn encrypt(key: &[u8; 32], data: &[u8], aad: &str) -> Result<Vec<u8>, StorageError> {
    let cipher = Aes256Gcm::new_from_slice(key)
        .map_err(|e| StorageError(format!("Şifreleme anahtarı geçersiz: {e}")))?;
    let mut nonce_bytes = [0u8; 12];
    rand::thread_rng().fill_bytes(&mut nonce_bytes);
    let nonce = Nonce::from_slice(&nonce_bytes);
    let ct = cipher
        .encrypt(
            nonce,
            Payload {
                msg: data,
                aad: aad.as_bytes(),
            },
        )
        .map_err(|e| StorageError(format!("Şifreleme hatası: {e}")))?;
    let mut out = Vec::with_capacity(MAGIC.len() + 12 + ct.len());
    out.extend_from_slice(MAGIC);
    out.extend_from_slice(&nonce_bytes);
    out.extend_from_slice(&ct);
    Ok(out)
}

pub fn decrypt(key: &[u8; 32], blob: &[u8], aad: &str) -> Result<Vec<u8>, StorageError> {
    if !blob.starts_with(MAGIC) || blob.len() < MAGIC.len() + 12 + 16 {
        return Err(StorageError("Şifreli belge biçimi tanınmadı.".to_string()));
    }
    let cipher = Aes256Gcm::new_from_slice(key)
        .map_err(|e| StorageError(format!("Şifreleme anahtarı geçersiz: {e}")))?;
    let nonce = Nonce::from_slice(&blob[MAGIC.len()..MAGIC.len() + 12]);
    cipher
        .decrypt(
            nonce,
            Payload {
                msg: &blob[MAGIC.len() + 12..],
                aad: aad.as_bytes(),
            },
        )
        .map_err(|_| StorageError("Belge şifresi çözülemedi.".to_string()))
}

// ── AWS SigV4 (R2 için) ──────────────────────────────────────────────────────────────
fn _h(key: &[u8], msg: &str) -> Vec<u8> {
    let mut mac = <HmacSha256 as hmac::Mac>::new_from_slice(key).expect("HMAC can take key of any size");
    mac.update(msg.as_bytes());
    mac.finalize().into_bytes().to_vec()
}

fn sha256_hex(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    hex::encode(hasher.finalize())
}

/// İmzalı istek başlıklarını döndürür (Authorization dahil). path zaten URI-kodlu olmalı.
#[allow(clippy::too_many_arguments)]
pub fn sigv4_headers(
    method: &str,
    host: &str,
    path: &str,
    payload_hash: Option<&str>,
    access_key: &str,
    secret_key: &str,
    region: &str,
    service: &str,
    amz_date: Option<&str>,
    extra_headers: Option<&BTreeMap<String, String>>,
    query: &str,
) -> BTreeMap<String, String> {
    let amz_date_owned: String;
    let amz_date = match amz_date {
        Some(d) => d,
        None => {
            amz_date_owned = Utc::now().format("%Y%m%dT%H%M%SZ").to_string();
            &amz_date_owned
        }
    };
    let date = &amz_date[..8];
    let mut headers: BTreeMap<String, String> = BTreeMap::new();
    headers.insert("host".to_string(), host.to_string());
    headers.insert("x-amz-date".to_string(), amz_date.to_string());
    if let Some(ph) = payload_hash {
        if service == "s3" {
            headers.insert("x-amz-content-sha256".to_string(), ph.to_string());
        }
    }
    if let Some(extra) = extra_headers {
        for (k, v) in extra {
            headers.insert(k.to_lowercase(), v.clone());
        }
    }
    let signed_keys: Vec<String> = headers.keys().cloned().collect();
    let signed = signed_keys.join(";");
    let mut canon_headers = String::new();
    for k in &signed_keys {
        let val = headers
            .get(k)
            .map(|v| v.split_whitespace().collect::<Vec<_>>().join(" "))
            .unwrap_or_default();
        canon_headers.push_str(&format!("{}:{}\n", k, val));
    }
    let empty_hash = sha256_hex(b"");
    let ph_str = payload_hash.unwrap_or(&empty_hash);
    let canon = [method, path, query, &canon_headers, &signed, ph_str].join("\n");
    let scope = format!("{}/{}/{}/aws4_request", date, region, service);
    let canon_hash = sha256_hex(canon.as_bytes());
    let sts = ["AWS4-HMAC-SHA256", amz_date, &scope, &canon_hash].join("\n");

    let k_date = _h(format!("AWS4{}", secret_key).as_bytes(), date);
    let k_region = _h(&k_date, region);
    let k_service = _h(&k_region, service);
    let k_signing = _h(&k_service, "aws4_request");
    let sig = hex::encode(_h(&k_signing, &sts));

    let mut out = BTreeMap::new();
    for (k, v) in headers {
        if k != "host" {
            out.insert(k, v);
        }
    }
    out.insert(
        "Authorization".to_string(),
        format!(
            "AWS4-HMAC-SHA256 Credential={}/{}, SignedHeaders={}, Signature={}",
            access_key, scope, signed, sig
        ),
    );
    out
}

// ── Depolar ──────────────────────────────────────────────────────────────────────────
#[derive(Clone)]
pub enum Storage {
    Local(LocalStorage),
    R2(R2Storage),
}

impl Storage {
    pub fn name(&self) -> &'static str {
        match self {
            Storage::Local(_) => "yerel klasör",
            Storage::R2(_) => "Cloudflare R2",
        }
    }

    pub async fn put(&self, key: &str, data: &[u8]) -> Result<(), StorageError> {
        match self {
            Storage::Local(s) => s.put(key, data).await,
            Storage::R2(s) => s.put(key, data).await,
        }
    }

    pub async fn get(&self, key: &str) -> Result<Vec<u8>, StorageError> {
        match self {
            Storage::Local(s) => s.get(key).await,
            Storage::R2(s) => s.get(key).await,
        }
    }

    pub async fn delete(&self, key: &str) -> Result<(), StorageError> {
        match self {
            Storage::Local(s) => s.delete(key).await,
            Storage::R2(s) => s.delete(key).await,
        }
    }
}

#[derive(Clone)]
pub struct LocalStorage {
    pub root: PathBuf,
}

impl LocalStorage {
    pub fn new(root: impl AsRef<Path>) -> Self {
        let root_path = root.as_ref().to_path_buf();
        let _ = fs::create_dir_all(&root_path);
        Self { root: root_path }
    }

    pub fn _path(&self, key: &str) -> PathBuf {
        let safe: String = key
            .chars()
            .filter(|c| c.is_alphanumeric() || *c == '-' || *c == '_')
            .collect();
        self.root.join(safe)
    }

    pub async fn put(&self, key: &str, data: &[u8]) -> Result<(), StorageError> {
        let p = self._path(key);
        fs::write(&p, data).map_err(|e| StorageError(format!("Yerel yazma hatası: {e}")))
    }

    pub async fn get(&self, key: &str) -> Result<Vec<u8>, StorageError> {
        let p = self._path(key);
        match fs::read(&p) {
            Ok(b) => Ok(b),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                Err(StorageError("Belge depoda bulunamadı.".to_string()))
            }
            Err(e) => Err(StorageError(format!("Yerel okuma hatası: {e}"))),
        }
    }

    pub async fn delete(&self, key: &str) -> Result<(), StorageError> {
        let p = self._path(key);
        match fs::remove_file(&p) {
            Ok(()) => Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(e) => Err(StorageError(format!("Yerel silme hatası: {e}"))),
        }
    }
}

#[derive(Clone)]
pub struct R2Storage {
    pub endpoint: String,
    pub host: String,
    pub access_key: String,
    pub secret_key: String,
    pub bucket: String,
    pub prefix: String,
    client: reqwest::Client,
}

impl R2Storage {
    pub fn new(
        account_id: &str,
        access_key: &str,
        secret_key: &str,
        bucket: &str,
        endpoint: Option<&str>,
    ) -> Self {
        let ep = endpoint
            .map(|s| s.to_string())
            .unwrap_or_else(|| format!("https://{}.r2.cloudflarestorage.com", account_id))
            .trim_end_matches('/')
            .to_string();
        let host = url::Url::parse(&ep)
            .ok()
            .and_then(|u| u.host_str().map(|h| {
                if let Some(p) = u.port() {
                    format!("{}:{}", h, p)
                } else {
                    h.to_string()
                }
            }))
            .unwrap_or_default();
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(120))
            .build()
            .unwrap_or_else(|_| reqwest::Client::new());
        Self {
            endpoint: ep,
            host,
            access_key: access_key.to_string(),
            secret_key: secret_key.to_string(),
            bucket: bucket.to_string(),
            prefix: "zs/".to_string(),
            client,
        }
    }

    async fn _req(&self, method: &str, key: &str, data: &[u8]) -> Result<Vec<u8>, StorageError> {
        let raw_path = format!("{}/{}{}", self.bucket, self.prefix, key);
        let path = format!(
            "/{}",
            utf8_percent_encode(&raw_path, S3_SAFE).to_string()
        );
        let ph = sha256_hex(data);
        let hdrs = sigv4_headers(
            method,
            &self.host,
            &path,
            Some(&ph),
            &self.access_key,
            &self.secret_key,
            "auto",
            "s3",
            None,
            None,
            "",
        );
        let url = format!("{}{}", self.endpoint, path);
        let req_method = reqwest::Method::from_bytes(method.as_bytes())
            .map_err(|e| StorageError(format!("Geçersiz HTTP yöntemi: {e}")))?;
        let mut req = self.client.request(req_method, &url);
        for (k, v) in hdrs {
            req = req.header(k, v);
        }
        if method == "PUT" {
            req = req.body(data.to_vec());
        }
        let resp = req
            .send()
            .await
            .map_err(|e| StorageError(format!("R2 {} hatası: {}", method, e)))?;
        let status = resp.status().as_u16();
        let body = resp
            .bytes()
            .await
            .map_err(|e| StorageError(format!("R2 yanıt okuma hatası: {e}")))?
            .to_vec();
        if status == 404 && method == "GET" {
            return Err(StorageError("Belge depoda bulunamadı.".to_string()));
        }
        if status >= 300 && !(method == "DELETE" && status == 404) {
            let preview = String::from_utf8_lossy(&body[..body.len().min(200)]);
            return Err(StorageError(format!(
                "R2 {} hatası: HTTP {} {:?}",
                method, status, preview
            )));
        }
        Ok(body)
    }

    pub async fn put(&self, key: &str, data: &[u8]) -> Result<(), StorageError> {
        self._req("PUT", key, data).await?;
        Ok(())
    }

    pub async fn get(&self, key: &str) -> Result<Vec<u8>, StorageError> {
        self._req("GET", key, b"").await
    }

    pub async fn delete(&self, key: &str) -> Result<(), StorageError> {
        self._req("DELETE", key, b"").await?;
        Ok(())
    }
}

/// (storage | None, açıklama). None → yükleme kapalı.
pub fn make_storage() -> (Option<Arc<Storage>>, String) {
    let acc = env::var("R2_ACCOUNT_ID").unwrap_or_default().trim().to_string();
    let ak = env::var("R2_ACCESS_KEY_ID").unwrap_or_default().trim().to_string();
    let sk = env::var("R2_SECRET_ACCESS_KEY").unwrap_or_default().trim().to_string();
    let bucket = env::var("R2_BUCKET").unwrap_or_default().trim().to_string();
    let endpoint_raw = env::var("R2_ENDPOINT").unwrap_or_default().trim().to_string();
    let endpoint = if endpoint_raw.is_empty() {
        None
    } else {
        Some(endpoint_raw.as_str())
    };
    if !ak.is_empty() && !sk.is_empty() && !bucket.is_empty() && (!acc.is_empty() || endpoint.is_some()) {
        let st = R2Storage::new(&acc, &ak, &sk, &bucket, endpoint);
        let desc = format!("Cloudflare R2 ({}/{})", st.host, bucket);
        return (Some(Arc::new(Storage::R2(st))), desc);
    }
    if env::var("RENDER").map(|v| !v.is_empty()).unwrap_or(false) {
        return (
            None,
            "R2 ayarlı değil → belge yükleme KAPALI (Render diski efemeral)".to_string(),
        );
    }
    let root = accounts::data_dir().join("zs_files");
    let desc = format!("yerel klasör ({}) — yalnız geliştirme", root.display());
    (Some(Arc::new(Storage::Local(LocalStorage::new(&root)))), desc)
}
