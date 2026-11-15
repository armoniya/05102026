//! /zs İş Takip — Web Push (zs_push.py 1:1 port)
//! ------------------------------------------------
//! Tarayıcı/telefon bildirimi: RFC 8291 (aes128gcm içerik şifreleme) + RFC 8292 (VAPID kimliği).
//!
//! Anahtar: ZS_VAPID_PRIVATE = P-256 özel anahtarın 32 baytı, base64url. Canlıda Render ortam
//! değişkeni olarak SABİT verilmeli; değişirse tüm cihazların aboneliği geçersiz olur (kullanıcılar
//! bildirimleri yeniden açar). Yerelde ayarlı değilse veri klasöründe bir kez üretilip saklanır.
//! ZS_VAPID_SUB: iletişim adresi (mailto: ya da https:), bildirim servisleri sorun olursa buna yazar.

use std::env;
use std::fmt;
use std::fs;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use aes_gcm::aead::{Aead, KeyInit};
use aes_gcm::{Aes128Gcm, Nonce};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use hmac::{Hmac, Mac};
use p256::ecdh::diffie_hellman;
use p256::ecdsa::signature::Signer;
use p256::ecdsa::{Signature, SigningKey};
use p256::elliptic_curve::sec1::ToEncodedPoint;
use p256::{PublicKey, SecretKey};
use rand::RngCore;
use serde_json::{json, Value};
use sha2::Sha256;
use url::Url;

use crate::accounts;

type HmacSha256 = Hmac<Sha256>;

#[derive(Debug, Clone)]
pub enum PushSendError {
    /// Abonelik artık geçersiz (404/410) — kaydı silinmeli.
    Gone(String),
    /// Diğer gönderim/şifreleme hatası.
    Other(String),
}

impl fmt::Display for PushSendError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            PushSendError::Gone(s) => f.write_str(s),
            PushSendError::Other(s) => f.write_str(s),
        }
    }
}

impl std::error::Error for PushSendError {}

/// Abonelik adresi yalnız tarayıcıların push servisleri olabilir: sunucu bu adrese POST attığı için
/// serbest https adresi, sunucudan istenen yere istek attırmaya (kör SSRF) açıktı.
pub const PUSH_HOSTS: &[&str] = &[
    "fcm.googleapis.com",
    "android.googleapis.com",
    "updates.push.services.mozilla.com",
    "web.push.apple.com",
];
pub const PUSH_HOST_SUFFIXES: &[&str] = &[
    ".push.services.mozilla.com",
    ".notify.windows.com",
    ".push.apple.com",
];

pub fn endpoint_ok(endpoint: &str) -> bool {
    let Ok(u) = Url::parse(endpoint) else {
        return false;
    };
    if u.scheme() != "https" || !u.username().is_empty() || u.password().is_some() {
        return false;
    }
    if let Some(port) = u.port() {
        if port != 443 {
            return false;
        }
    }
    let host = u.host_str().unwrap_or("").to_lowercase();
    PUSH_HOSTS.contains(&host.as_str())
        || PUSH_HOST_SUFFIXES.iter().any(|sfx| host.ends_with(sfx))
}

pub fn b64u(b: &[u8]) -> String {
    URL_SAFE_NO_PAD.encode(b)
}

pub fn unb64u(s: &str) -> Result<Vec<u8>, base64::DecodeError> {
    let trimmed = s.trim().trim_end_matches('=');
    URL_SAFE_NO_PAD.decode(trimmed.as_bytes())
}

pub fn _pub_bytes(key: &SecretKey) -> Vec<u8> {
    key.public_key().to_encoded_point(false).as_bytes().to_vec()
}

fn _hmac(key: &[u8], data: &[u8]) -> [u8; 32] {
    let mut mac = <HmacSha256 as hmac::Mac>::new_from_slice(key).expect("HMAC key size");
    mac.update(data);
    let bytes = mac.finalize().into_bytes();
    let mut out = [0u8; 32];
    out.copy_from_slice(&bytes);
    out
}

pub fn private_from_raw(raw: &[u8]) -> Result<SecretKey, String> {
    if raw.len() != 32 {
        return Err("P-256 özel anahtar 32 bayt olmalı".to_string());
    }
    SecretKey::from_slice(raw).map_err(|e| format!("Geçersiz P-256 anahtarı: {e}"))
}

pub fn new_private_raw() -> [u8; 32] {
    let sk = SecretKey::random(&mut rand::thread_rng());
    let mut out = [0u8; 32];
    out.copy_from_slice(&sk.to_bytes());
    out
}

/// `(özel anahtar nesnesi, açık anahtar base64url, kaynak açıklaması)` ya da `(None, None, neden)`.
pub fn load_vapid() -> (Option<SecretKey>, Option<String>, String) {
    let mut raw = env::var("ZS_VAPID_PRIVATE")
        .unwrap_or_default()
        .trim()
        .to_string();
    let mut src = "ortam değişkeni".to_string();
    if raw.is_empty() {
        let path = accounts::data_dir().join("zs_vapid.key");
        if path.is_file() {
            match fs::read_to_string(&path) {
                Ok(s) => raw = s.trim().to_string(),
                Err(e) => return (None, None, format!("anahtar dosyası yazılamadı: {e}")),
            }
        } else {
            raw = b64u(&new_private_raw());
            if let Some(parent) = path.parent() {
                let _ = fs::create_dir_all(parent);
            }
            if let Err(e) = fs::write(&path, &raw) {
                return (None, None, format!("anahtar dosyası yazılamadı: {e}"));
            }
        }
        src = format!(
            "yerel dosya ({}) — canlıda ZS_VAPID_PRIVATE verin",
            path.display()
        );
    }
    let raw_bytes = match unb64u(&raw) {
        Ok(b) => b,
        Err(_) => return (None, None, "ZS_VAPID_PRIVATE geçersiz".to_string()),
    };
    let key = match private_from_raw(&raw_bytes) {
        Ok(k) => k,
        Err(_) => return (None, None, "ZS_VAPID_PRIVATE geçersiz".to_string()),
    };
    let pub_b64 = b64u(&_pub_bytes(&key));
    (Some(key), Some(pub_b64), src)
}

/// RFC 8291 aes128gcm gövdesi (tek kayıt).
pub fn encrypt(
    payload: &[u8],
    p256dh: &str,
    auth: &str,
    as_key_opt: Option<&SecretKey>,
    salt_opt: Option<[u8; 16]>,
) -> Result<Vec<u8>, String> {
    let ua_pub = unb64u(p256dh).map_err(|_| "abonelik anahtarları geçersiz".to_string())?;
    let auth_secret = unb64u(auth).map_err(|_| "abonelik anahtarları geçersiz".to_string())?;
    if ua_pub.len() != 65 || auth_secret.len() < 16 {
        return Err("abonelik anahtarları geçersiz".to_string());
    }
    let ua_key = PublicKey::from_sec1_bytes(&ua_pub)
        .map_err(|_| "abonelik anahtarları geçersiz".to_string())?;
    let generated_as;
    let as_key = match as_key_opt {
        Some(k) => k,
        None => {
            generated_as = SecretKey::random(&mut rand::thread_rng());
            &generated_as
        }
    };
    let as_pub = _pub_bytes(as_key);
    let shared = diffie_hellman(as_key.to_nonzero_scalar(), ua_key.as_affine());
    let prk_key = _hmac(&auth_secret, shared.raw_secret_bytes());
    let mut info = Vec::with_capacity(14 + ua_pub.len() + as_pub.len() + 1);
    info.extend_from_slice(b"WebPush: info\x00");
    info.extend_from_slice(&ua_pub);
    info.extend_from_slice(&as_pub);
    info.push(0x01);
    let ikm = _hmac(&prk_key, &info);
    let salt = match salt_opt {
        Some(s) => s,
        None => {
            let mut s = [0u8; 16];
            rand::thread_rng().fill_bytes(&mut s);
            s
        }
    };
    let prk = _hmac(&salt, &ikm);
    let cek_full = _hmac(&prk, b"Content-Encoding: aes128gcm\x00\x01");
    let nonce_full = _hmac(&prk, b"Content-Encoding: nonce\x00\x01");
    let cek = &cek_full[..16];
    let nonce = Nonce::from_slice(&nonce_full[..12]);
    let cipher = Aes128Gcm::new_from_slice(cek).map_err(|e| e.to_string())?;
    let mut pt = Vec::with_capacity(payload.len() + 1);
    pt.extend_from_slice(payload);
    pt.push(0x02);
    let ct = cipher
        .encrypt(nonce, pt.as_ref())
        .map_err(|e| format!("şifreleme hatası: {e}"))?;
    let mut out = Vec::with_capacity(16 + 4 + 1 + as_pub.len() + ct.len());
    out.extend_from_slice(&salt);
    out.extend_from_slice(&4096u32.to_be_bytes());
    out.push(as_pub.len() as u8);
    out.extend_from_slice(&as_pub);
    out.extend_from_slice(&ct);
    Ok(out)
}

/// Yalnız test için: alıcı tarafı çözme.
pub fn decrypt(body: &[u8], ua_key: &SecretKey, auth: &str) -> Result<Vec<u8>, String> {
    if body.len() < 21 {
        return Err("gövde çok kısa".to_string());
    }
    let salt = &body[..16];
    let idlen = body[20] as usize;
    if body.len() < 21 + idlen {
        return Err("gövde bozuk".to_string());
    }
    let as_pub = &body[21..21 + idlen];
    let ct = &body[21 + idlen..];
    let ua_pub = _pub_bytes(ua_key);
    let as_key = PublicKey::from_sec1_bytes(as_pub).map_err(|e| e.to_string())?;
    let shared = diffie_hellman(ua_key.to_nonzero_scalar(), as_key.as_affine());
    let auth_secret = unb64u(auth).map_err(|e| e.to_string())?;
    let prk_key = _hmac(&auth_secret, shared.raw_secret_bytes());
    let mut info = Vec::with_capacity(14 + ua_pub.len() + as_pub.len() + 1);
    info.extend_from_slice(b"WebPush: info\x00");
    info.extend_from_slice(&ua_pub);
    info.extend_from_slice(as_pub);
    info.push(0x01);
    let ikm = _hmac(&prk_key, &info);
    let prk = _hmac(salt, &ikm);
    let cek_full = _hmac(&prk, b"Content-Encoding: aes128gcm\x00\x01");
    let nonce_full = _hmac(&prk, b"Content-Encoding: nonce\x00\x01");
    let cipher = Aes128Gcm::new_from_slice(&cek_full[..16]).map_err(|e| e.to_string())?;
    let pt = cipher
        .decrypt(Nonce::from_slice(&nonce_full[..12]), ct)
        .map_err(|e| format!("çözme hatası: {e}"))?;
    let idx = pt
        .iter()
        .rposition(|&b| b == 0x02)
        .ok_or_else(|| "dolgu ayırıcısı yok".to_string())?;
    Ok(pt[..idx].to_vec())
}

/// `Authorization: vapid t=<ES256 JWT>, k=<açık anahtar>`.
pub fn vapid_header(endpoint: &str, key: &SecretKey, pub_b64: &str, sub: &str) -> Result<String, String> {
    let u = Url::parse(endpoint).map_err(|e| format!("geçersiz endpoint: {e}"))?;
    let host = u.host_str().unwrap_or("");
    let netloc = if let Some(port) = u.port() {
        format!("{}:{}", host, port)
    } else {
        host.to_string()
    };
    let aud = format!("{}://{}", u.scheme(), netloc);
    let exp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
        + 12 * 3600;
    let head = b64u(r#"{"typ":"JWT","alg":"ES256"}"#.as_bytes());
    let claims_json = json!({
        "aud": aud,
        "exp": exp,
        "sub": sub,
    });
    let claims = b64u(serde_json::to_string(&claims_json).unwrap().as_bytes());
    let signing = format!("{}.{}", head, claims);
    let signing_key = SigningKey::from(key);
    let sig: Signature = signing_key.sign(signing.as_bytes());
    let sig_b64 = b64u(&sig.to_bytes());
    Ok(format!("vapid t={}.{}.{}, k={}", head, claims, sig_b64, pub_b64))
}

/// `sub: {endpoint, p256dh, auth}`. Başarıda `Ok(())`; geçersiz abonelikte `Err(PushSendError::Gone)`;
/// diğer hatada `Err(PushSendError::Other)`.
pub async fn send(
    client: &reqwest::Client,
    sub: &Value,
    payload: &Value,
    key: &SecretKey,
    pub_b64: &str,
    vapid_sub: &str,
    ttl: u64,
) -> Result<(), PushSendError> {
    let endpoint = sub
        .get("endpoint")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if !endpoint_ok(endpoint) {
        return Err(PushSendError::Gone(
            "izinli push servisi olmayan uç".to_string(),
        ));
    }
    let p256dh = sub.get("p256dh").and_then(|v| v.as_str()).unwrap_or("");
    let auth = sub.get("auth").and_then(|v| v.as_str()).unwrap_or("");
    let payload_bytes = serde_json::to_vec(payload)
        .map_err(|e| PushSendError::Other(format!("JSON hatası: {e}")))?;
    let body = encrypt(&payload_bytes, p256dh, auth, None, None)
        .map_err(PushSendError::Other)?;
    let auth_hdr = vapid_header(endpoint, key, pub_b64, vapid_sub)
        .map_err(PushSendError::Other)?;
    let resp = client
        .post(endpoint)
        .header("Content-Encoding", "aes128gcm")
        .header("Content-Type", "application/octet-stream")
        .header("TTL", ttl.to_string())
        .header("Urgency", "high")
        .header("Authorization", auth_hdr)
        .timeout(Duration::from_secs(15))
        .body(body)
        .send()
        .await
        .map_err(|e| PushSendError::Other(format!("push istek hatası: {e}")))?;
    let status = resp.status().as_u16();
    if matches!(status, 200 | 201 | 202) {
        return Ok(());
    }
    let txt = resp.text().await.unwrap_or_default();
    let short_txt: String = txt.chars().take(300).collect();
    if matches!(status, 404 | 410) {
        return Err(PushSendError::Gone(format!("{} {}", status, short_txt)));
    }
    Err(PushSendError::Other(format!(
        "push {}: {}",
        status, short_txt
    )))
}
