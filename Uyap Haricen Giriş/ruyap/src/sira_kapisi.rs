//! Sıra Kapısı (`uyap_core/sira_kapisi.py` 1:1 portu)
//! ===================================================
//! Aynı ofis ağındaki bot bilgisayarının `avukat.uyap.gov.tr` isteklerini
//! çokluuyap üzerinden geçirip `_runtime.batch_write()` + `_runtime.aktif_istekler_bitsin()`
//! ile sıraya alır; böylece bot ile kullanıcıların UYAP oturumları çakışmaz.

use std::collections::{HashMap, HashSet};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::net::{IpAddr, Ipv4Addr, SocketAddr, TcpStream, UdpSocket};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex as StdMutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use aes_gcm::aead::{Aead, KeyInit};
use aes_gcm::{Aes256Gcm, Nonce};
use base64::Engine;
use once_cell::sync::Lazy;
use pbkdf2::pbkdf2_hmac;
use rand::RngCore;
use rcgen::{
    BasicConstraints, CertificateParams, CustomExtension, DistinguishedName, DnType,
    ExtendedKeyUsagePurpose, IsCa, KeyPair, KeyUsagePurpose, SanType,
};
use rustls::pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha1::Sha1;
use sha2::{Digest, Sha256};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::sync::{Mutex as AsyncMutex, Notify, Semaphore};
use tokio_rustls::TlsAcceptor;
use x509_parser::prelude::*;

use crate::runtime;

pub const UYAP_HOST: &str = "avukat.uyap.gov.tr";
pub const HOSTS_ETIKET: &str = "# cokluuyap-sira-kapisi";
pub const EN_BUYUK_GOVDE: usize = 32 * 1024 * 1024;
pub const EN_BUYUK_YANIT: usize = 128 * 1024 * 1024;
pub const EN_COK_BAGLANTI: usize = 200;
pub const KILIT_SURE_SINIRI: f64 = 60.0;

pub const _ISTEK_AT: &[&str] = &[
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
];

pub const _HOP: &[&str] = &[
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
];

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct BotKaydi {
    #[serde(default)]
    pub ip: String,
    #[serde(default)]
    pub ad: String,
    #[serde(default)]
    pub tarih: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Ayar {
    #[serde(default)]
    pub etkin: bool,
    #[serde(rename = "mod", default = "default_mod")]
    pub mod_: String,
    #[serde(default = "default_port")]
    pub port: u16,
    #[serde(default = "default_aralik")]
    pub aralik_sn: f64,
    #[serde(default)]
    pub izinli_ipler: Vec<String>,
    #[serde(default)]
    pub kapi_ip: Option<String>,
    #[serde(default)]
    pub kok_parmak_izi: Option<String>,
    #[serde(default)]
    pub kok_sha256: Option<String>,
    #[serde(default)]
    pub bot_adlari: HashMap<String, BotKaydi>,
}

fn default_mod() -> String {
    "sirala".to_string()
}
fn default_port() -> u16 {
    443
}
fn default_aralik() -> f64 {
    1.0
}

impl Default for Ayar {
    fn default() -> Self {
        Self {
            etkin: false,
            mod_: "sirala".to_string(),
            port: 443,
            aralik_sn: 1.0,
            izinli_ipler: Vec::new(),
            kapi_ip: None,
            kok_parmak_izi: None,
            kok_sha256: None,
            bot_adlari: HashMap::new(),
        }
    }
}

pub fn varsayilan_ayar() -> Value {
    json!({
        "etkin": false,
        "mod": "sirala",
        "port": 443,
        "aralik_sn": 1.0,
        "izinli_ipler": [],
        "kapi_ip": "",
        "kok_parmak_izi": "",
    })
}

pub fn veri_klasoru() -> PathBuf {
    if let Ok(env_dir) = std::env::var("UYAP_SIRA_KAPISI_DIR") {
        if !env_dir.trim().is_empty() {
            return PathBuf::from(env_dir.trim());
        }
    }
    let appdata = std::env::var("APPDATA")
        .ok()
        .filter(|s| !s.is_empty())
        .or_else(|| std::env::var("USERPROFILE").ok())
        .unwrap_or_else(|| ".".to_string());
    Path::new(&appdata).join("cokluuyap").join("sira_kapisi")
}

pub fn _yol(ad: &str) -> PathBuf {
    veri_klasoru().join(ad)
}

pub fn ayar_oku() -> Ayar {
    let p = _yol("ayar.json");
    if let Ok(txt) = fs::read_to_string(&p) {
        if let Ok(ayar) = serde_json::from_str::<Ayar>(&txt) {
            return ayar;
        }
    }
    Ayar::default()
}

pub fn ayar_yaz(ayar: &Ayar) {
    let dir = veri_klasoru();
    let _ = fs::create_dir_all(&dir);
    let tmp = _yol("ayar.json.tmp");
    let target = _yol("ayar.json");
    if let Ok(txt) = serde_json::to_string_pretty(ayar) {
        if fs::write(&tmp, txt.as_bytes()).is_ok() {
            let _ = fs::rename(&tmp, &target);
        }
    }
}

pub fn _lan_ipv4(ip: &str, etiket: &str) -> Result<String, String> {
    let parsed: Ipv4Addr = ip
        .trim()
        .parse()
        .map_err(|_| format!("{} geçerli bir IPv4 adresi değil: {:?}", etiket, ip))?;
    if parsed.is_unspecified() || parsed.is_loopback() || parsed.is_multicast() {
        return Err(format!(
            "{} LAN adresi olmalı (0.0.0.0 / 127.x kabul edilmez): {}",
            etiket, ip
        ));
    }
    if !(parsed.is_private() || parsed.is_link_local()) {
        return Err(format!(
            "{} özel ağ (LAN) adresi olmalı, genel IP kabul edilmez: {}",
            etiket, ip
        ));
    }
    Ok(parsed.to_string())
}

pub fn varsayilan_lan_ip() -> String {
    let sock = match UdpSocket::bind("0.0.0.0:0") {
        Ok(s) => s,
        Err(_) => return String::new(),
    };
    if sock.connect("10.255.255.255:1").is_err() {
        return String::new();
    }
    match sock.local_addr() {
        Ok(SocketAddr::V4(v4)) => {
            let ip = v4.ip().to_string();
            if _lan_ipv4(&ip, "Kapı IP'si").is_ok() {
                ip
            } else {
                String::new()
            }
        }
        _ => String::new(),
    }
}

pub fn botlar(ayar: Option<&Ayar>) -> Vec<BotKaydi> {
    let owned;
    let a = match ayar {
        Some(a) => a,
        None => {
            owned = ayar_oku();
            &owned
        }
    };
    a.izinli_ipler
        .iter()
        .map(|ip| {
            if let Some(kayit) = a.bot_adlari.get(ip) {
                BotKaydi {
                    ip: ip.clone(),
                    ad: kayit.ad.clone(),
                    tarih: kayit.tarih.clone(),
                }
            } else {
                BotKaydi {
                    ip: ip.clone(),
                    ad: String::new(),
                    tarih: String::new(),
                }
            }
        })
        .collect()
}

pub fn bot_ekle(ip: &str, ad: Option<&str>) -> Result<Vec<String>, String> {
    let clean = _lan_ipv4(ip, "Bot IP'si")?;
    let mut ayar = ayar_oku();
    if !ayar.izinli_ipler.contains(&clean) {
        ayar.izinli_ipler.push(clean.clone());
    }
    let tarih = chrono::Local::now().format("%d.%m.%Y %H:%M").to_string();
    ayar.bot_adlari.insert(
        clean.clone(),
        BotKaydi {
            ip: clean,
            ad: ad.unwrap_or("").to_string(),
            tarih,
        },
    );
    let liste = ayar.izinli_ipler.clone();
    ayar_yaz(&ayar);
    yenile();
    Ok(liste)
}

pub fn bot_sil(ip: &str) -> Vec<String> {
    let mut ayar = ayar_oku();
    ayar.izinli_ipler.retain(|x| x != ip);
    ayar.bot_adlari.remove(ip);
    let liste = ayar.izinli_ipler.clone();
    ayar_yaz(&ayar);
    yenile();
    liste
}

pub fn etkin_ayarla(etkin: bool) {
    let mut ayar = ayar_oku();
    ayar.etkin = etkin;
    ayar_yaz(&ayar);
    yenile();
}

pub fn kok_der() -> Option<Vec<u8>> {
    let p = _yol("kok.crt");
    let txt = fs::read_to_string(&p).ok()?;
    let mut cursor = std::io::Cursor::new(txt.as_bytes());
    let certs: Vec<CertificateDer<'static>> = rustls_pemfile::certs(&mut cursor)
        .filter_map(|r| r.ok())
        .collect();
    certs.into_iter().next().map(|c| c.as_ref().to_vec())
}

pub fn sertifika_bitis_ts(yol: &Path) -> Option<f64> {
    let txt = fs::read_to_string(yol).ok()?;
    let mut cursor = std::io::Cursor::new(txt.as_bytes());
    let cert_der = rustls_pemfile::certs(&mut cursor).next()?.ok()?;
    let (_, cert) = X509Certificate::from_der(cert_der.as_ref()).ok()?;
    let ts = cert.validity().not_after.timestamp();
    Some(ts as f64)
}

pub fn sertifika_bitis() -> Option<chrono::DateTime<chrono::Local>> {
    let ts = sertifika_bitis_ts(&_yol("sunucu.crt"))?;
    chrono::DateTime::from_timestamp(ts as i64, 0)
        .map(|dt| dt.with_timezone(&chrono::Local))
}

pub fn kapi_hazirla(aralik_sn: f64, mod_: &str) -> Result<Ayar, String> {
    if mod_ != "sirala" && mod_ != "izle" {
        return Err("mod 'sirala' ya da 'izle' olmalı.".to_string());
    }
    let aralik = aralik_sn.clamp(0.0, 60.0);
    let kapi_ip = varsayilan_lan_ip();
    if kapi_ip.is_empty() {
        return Err("Bu bilgisayarın LAN IP'si bulunamadı (ağ bağlantısı yok?).".to_string());
    }

    let mut parmak = String::new();
    let mut sha256_hex = String::new();
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64();
    let cok_yakinda = now + 30.0 * 86400.0;

    let sunucu_crt = _yol("sunucu.crt");
    let sunucu_key = _yol("sunucu.key");
    let kok_crt = _yol("kok.crt");

    let needs_gen = !sunucu_crt.exists()
        || !sunucu_key.exists()
        || !kok_crt.exists()
        || sertifika_bitis_ts(&sunucu_crt)
            .map(|t| t < cok_yakinda)
            .unwrap_or(true)
        || sertifika_bitis_ts(&kok_crt)
            .map(|t| t < cok_yakinda)
            .unwrap_or(true);

    if needs_gen {
        let (_, _, _, p, s256) = sertifika_uret(None)?;
        parmak = p;
        sha256_hex = s256;
    } else if let Some(der) = kok_der() {
        let mut h1 = Sha1::new();
        h1.update(&der);
        parmak = hex::encode(h1.finalize()).to_uppercase();
        let mut h2 = Sha256::new();
        h2.update(&der);
        sha256_hex = hex::encode(h2.finalize()).to_uppercase();
    }

    let mut ayar = ayar_oku();
    ayar.etkin = true;
    ayar.mod_ = mod_.to_string();
    ayar.port = 443;
    ayar.aralik_sn = aralik;
    ayar.kapi_ip = Some(kapi_ip);
    if !parmak.is_empty() {
        ayar.kok_parmak_izi = Some(parmak);
    }
    if !sha256_hex.is_empty() {
        ayar.kok_sha256 = Some(sha256_hex);
    }
    ayar_yaz(&ayar);
    yenile();
    Ok(ayar)
}

// ── Windows DPAPI ───────────────────────────────────────────────────────────────────────────

#[cfg(windows)]
fn _dpapi_koru(veri: &[u8]) -> Result<Vec<u8>, String> {
    use std::ptr::{null, null_mut};
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{
        CryptProtectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
    };

    let in_blob = CRYPT_INTEGER_BLOB {
        cbData: veri.len() as u32,
        pbData: veri.as_ptr() as *mut u8,
    };
    let mut out_blob = CRYPT_INTEGER_BLOB {
        cbData: 0,
        pbData: null_mut(),
    };
    let ok = unsafe {
        CryptProtectData(
            &in_blob,
            null(),
            null(),
            null(),
            null(),
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut out_blob,
        )
    };
    if ok == 0 {
        return Err("CryptProtectData başarısız".to_string());
    }
    let slice = unsafe { std::slice::from_raw_parts(out_blob.pbData, out_blob.cbData as usize) };
    let res = slice.to_vec();
    unsafe {
        LocalFree(out_blob.pbData as _);
    }
    Ok(res)
}

#[cfg(windows)]
fn _dpapi_ac(veri: &[u8]) -> Result<Vec<u8>, String> {
    use std::ptr::{null, null_mut};
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{
        CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
    };

    let in_blob = CRYPT_INTEGER_BLOB {
        cbData: veri.len() as u32,
        pbData: veri.as_ptr() as *mut u8,
    };
    let mut out_blob = CRYPT_INTEGER_BLOB {
        cbData: 0,
        pbData: null_mut(),
    };
    let ok = unsafe {
        CryptUnprotectData(
            &in_blob,
            null_mut(),
            null(),
            null(),
            null(),
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut out_blob,
        )
    };
    if ok == 0 {
        return Err("CryptUnprotectData başarısız".to_string());
    }
    let slice = unsafe { std::slice::from_raw_parts(out_blob.pbData, out_blob.cbData as usize) };
    let res = slice.to_vec();
    unsafe {
        LocalFree(out_blob.pbData as _);
    }
    Ok(res)
}

#[cfg(not(windows))]
fn _dpapi_koru(veri: &[u8]) -> Result<Vec<u8>, String> {
    Ok(veri.to_vec())
}

#[cfg(not(windows))]
fn _dpapi_ac(veri: &[u8]) -> Result<Vec<u8>, String> {
    Ok(veri.to_vec())
}

pub fn _parola_kaydet(parola: &str) -> Result<(), String> {
    let dir = veri_klasoru();
    let _ = fs::create_dir_all(&dir);
    let korunan = _dpapi_koru(parola.as_bytes())?;
    fs::write(_yol("sunucu.parola"), korunan).map_err(|e| e.to_string())
}

pub fn anahtar_parolasi() -> Result<String, String> {
    let p = _yol("sunucu.parola");
    let ham = fs::read(&p).map_err(|e| format!("sunucu.parola okunamadı: {}", e))?;
    let cozulmus = _dpapi_ac(&ham)?;
    String::from_utf8(cozulmus).map_err(|e| e.to_string())
}

// ── Sunucu özel anahtarı şifreleme (PBKDF2-HMAC-SHA256 + AES-256-GCM) ─────────────────────

const KEY_MAGIC: &[u8] = b"UYAPKEY1";

fn encrypt_private_key(pem_bytes: &[u8], parola: &str) -> Result<Vec<u8>, String> {
    let mut salt = [0u8; 16];
    let mut nonce_bytes = [0u8; 12];
    rand::thread_rng().fill_bytes(&mut salt);
    rand::thread_rng().fill_bytes(&mut nonce_bytes);

    let mut key = [0u8; 32];
    pbkdf2_hmac::<Sha256>(parola.as_bytes(), &salt, 100_000, &mut key);
    let cipher = Aes256Gcm::new_from_slice(&key).map_err(|e| e.to_string())?;
    let nonce = Nonce::from_slice(&nonce_bytes);
    let ct = cipher
        .encrypt(nonce, pem_bytes)
        .map_err(|e| format!("Anahtar şifreleme hatası: {}", e))?;

    let mut out = Vec::with_capacity(KEY_MAGIC.len() + 16 + 12 + ct.len());
    out.extend_from_slice(KEY_MAGIC);
    out.extend_from_slice(&salt);
    out.extend_from_slice(&nonce_bytes);
    out.extend_from_slice(&ct);
    Ok(out)
}

fn decrypt_private_key(raw: &[u8], parola: &str) -> Result<Vec<u8>, String> {
    if raw.starts_with(KEY_MAGIC) && raw.len() > KEY_MAGIC.len() + 28 {
        let rest = &raw[KEY_MAGIC.len()..];
        let salt = &rest[..16];
        let nonce_bytes = &rest[16..28];
        let ct = &rest[28..];
        let mut key = [0u8; 32];
        pbkdf2_hmac::<Sha256>(parola.as_bytes(), salt, 100_000, &mut key);
        let cipher = Aes256Gcm::new_from_slice(&key).map_err(|e| e.to_string())?;
        let nonce = Nonce::from_slice(nonce_bytes);
        cipher
            .decrypt(nonce, ct)
            .map_err(|_| "Sunucu anahtarı çözülemedi (parola uyumsuz)".to_string())
    } else if raw.starts_with(b"-----BEGIN") {
        // Şifresiz PEM desteği
        Ok(raw.to_vec())
    } else {
        Err("Desteklenmeyen sunucu.key biçimi".to_string())
    }
}

/// X.509 NameConstraints (OID 2.5.29.30) critical extension DER encoding:
/// SEQUENCE { [0] EXPLICIT { SEQUENCE { [2] IMPLICIT "avukat.uyap.gov.tr" } } }
fn build_name_constraints_der(dns_name: &str) -> Vec<u8> {
    let name_bytes = dns_name.as_bytes();
    // GeneralName dNSName [2] IMPLICIT IA5String
    let mut gn = Vec::with_capacity(2 + name_bytes.len());
    gn.push(0x82);
    gn.push(name_bytes.len() as u8);
    gn.extend_from_slice(name_bytes);

    // GeneralSubtree SEQUENCE { base GeneralName }
    let mut subtree = Vec::with_capacity(2 + gn.len());
    subtree.push(0x30);
    subtree.push(gn.len() as u8);
    subtree.extend_from_slice(&gn);

    // permittedSubtrees [0] IMPLICIT GeneralSubtrees (SEQUENCE OF GeneralSubtree)
    let mut permitted = Vec::with_capacity(2 + subtree.len());
    permitted.push(0xA0);
    permitted.push(subtree.len() as u8);
    permitted.extend_from_slice(&subtree);

    // NameConstraints SEQUENCE
    let mut nc = Vec::with_capacity(2 + permitted.len());
    nc.push(0x30);
    nc.push(permitted.len() as u8);
    nc.extend_from_slice(&permitted);
    nc
}

/// Kök CA + `avukat.uyap.gov.tr` sunucu sertifikası üretir.
/// Döndürür: `(ca_cer_path, sunucu_crt_path, sunucu_key_path, sha1_parmak, sha256_parmak)`.
pub fn sertifika_uret(
    bot_klasor: Option<&Path>,
) -> Result<(PathBuf, PathBuf, PathBuf, String, String), String> {
    let dir = veri_klasoru();
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;

    let mut rand_bytes = [0u8; 24];
    rand::thread_rng().fill_bytes(&mut rand_bytes);
    let parola = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(rand_bytes);
    _parola_kaydet(&parola)?;

    // 1) Kök CA
    let ca_key = KeyPair::generate().map_err(|e| format!("CA anahtar üretimi: {}", e))?;
    let mut ca_params = CertificateParams::default();
    let mut ca_dn = DistinguishedName::new();
    ca_dn.push(DnType::CommonName, "cokluuyap Sira Kapisi Koku");
    ca_dn.push(DnType::OrganizationName, "cokluuyap");
    ca_params.distinguished_name = ca_dn;
    ca_params.is_ca = IsCa::Ca(BasicConstraints::Constrained(0));
    ca_params.key_usages = vec![KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];
    let mut nc_ext = CustomExtension::from_oid_content(
        &[2, 5, 29, 30],
        build_name_constraints_der(UYAP_HOST),
    );
    nc_ext.set_criticality(true);
    ca_params.custom_extensions = vec![nc_ext];

    let now_offset = ::time::OffsetDateTime::now_utc();
    ca_params.not_before = now_offset - ::time::Duration::hours(1);
    ca_params.not_after = now_offset + ::time::Duration::days(1825);

    let ca_cert = ca_params
        .self_signed(&ca_key)
        .map_err(|e| format!("CA sertifika imzalama: {}", e))?;

    // 2) Sunucu sertifikası (avukat.uyap.gov.tr)
    let srv_key = KeyPair::generate().map_err(|e| format!("Sunucu anahtar üretimi: {}", e))?;
    let mut srv_params = CertificateParams::new(vec![UYAP_HOST.to_string()])
        .map_err(|e| format!("Sunucu sertifika params: {}", e))?;
    let mut srv_dn = DistinguishedName::new();
    srv_dn.push(DnType::CommonName, UYAP_HOST);
    srv_params.distinguished_name = srv_dn;
    srv_params.is_ca = IsCa::NoCa;
    srv_params.key_usages = vec![
        KeyUsagePurpose::DigitalSignature,
        KeyUsagePurpose::KeyEncipherment,
    ];
    srv_params.extended_key_usages = vec![ExtendedKeyUsagePurpose::ServerAuth];
    srv_params.subject_alt_names = vec![SanType::DnsName(
        UYAP_HOST.try_into().map_err(|e| format!("SAN: {:?}", e))?,
    )];
    srv_params.not_before = now_offset - ::time::Duration::hours(1);
    srv_params.not_after = now_offset + ::time::Duration::days(825);

    let srv_cert = srv_params
        .signed_by(&srv_key, &ca_cert, &ca_key)
        .map_err(|e| format!("Sunucu sertifika imzalama: {}", e))?;

    let ca_pem = ca_cert.pem();
    let srv_pem = srv_cert.pem();
    let srv_key_pem = srv_key.serialize_pem();
    let enc_key = encrypt_private_key(srv_key_pem.as_bytes(), &parola)?;

    let kok_crt = _yol("kok.crt");
    let sunucu_crt = _yol("sunucu.crt");
    let sunucu_key = _yol("sunucu.key");

    fs::write(&kok_crt, ca_pem.as_bytes()).map_err(|e| e.to_string())?;
    fs::write(&sunucu_crt, srv_pem.as_bytes()).map_err(|e| e.to_string())?;
    fs::write(&sunucu_key, &enc_key).map_err(|e| e.to_string())?;

    let ca_cer_path = if let Some(bk) = bot_klasor {
        fs::create_dir_all(bk).map_err(|e| e.to_string())?;
        let p = bk.join("kok.cer");
        fs::write(&p, ca_cert.der().as_ref()).map_err(|e| e.to_string())?;
        p
    } else {
        kok_crt
    };

    let ca_der = ca_cert.der().as_ref();
    let mut h1 = Sha1::new();
    h1.update(ca_der);
    let parmak = hex::encode(h1.finalize()).to_uppercase();

    let mut h2 = Sha256::new();
    h2.update(ca_der);
    let sha256_hex = hex::encode(h2.finalize()).to_uppercase();

    Ok((ca_cer_path, sunucu_crt, sunucu_key, parmak, sha256_hex))
}

pub fn _kur_bat() -> String {
    "@echo off\r\n\
chcp 65001 >nul\r\n\
net session >nul 2>&1 || (echo Yonetici olarak calistirin: sag tik -^> Yonetici olarak calistir & pause & exit /b 1)\r\n\
powershell -NoProfile -ExecutionPolicy Bypass -File \"%~dp0KUR.ps1\"\r\n\
pause\r\n"
        .to_string()
}

pub fn _kur_ps1(kapi_ip: &str, kok_sha256: &str) -> String {
    format!(
        "$ErrorActionPreference = 'Stop'\r\n\
$cer = Join-Path $PSScriptRoot 'kok.cer'\r\n\
$cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($cer)\r\n\
$hash = ([BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash($cert.RawData)) -replace '-','').ToUpper()\r\n\
$beklenen = '{kok_sha256}'\r\n\
if ($hash -ne $beklenen) {{ throw \"Sertifika SHA256 uyusmuyor: $hash != $beklenen\" }}\r\n\
$store = New-Object System.Security.Cryptography.X509Certificates.X509Store('Root','LocalMachine')\r\n\
$store.Open('ReadWrite')\r\n\
$store.Add($cert)\r\n\
$store.Close()\r\n\
$hosts = \"$env:SystemRoot\\System32\\drivers\\etc\\hosts\"\r\n\
$satirlar = Get-Content $hosts | Where-Object {{ $_ -notmatch 'cokluuyap-sira-kapisi' }}\r\n\
$satirlar += \"{kapi_ip} {UYAP_HOST} {HOSTS_ETIKET}\"\r\n\
Set-Content -Path $hosts -Value $satirlar -Encoding ASCII\r\n\
Write-Host 'Tamam. Sira kapisi aktif.'\r\n"
    )
}

pub fn _kaldir_bat() -> String {
    "@echo off\r\n\
chcp 65001 >nul\r\n\
net session >nul 2>&1 || (echo Yonetici olarak calistirin: sag tik -^> Yonetici olarak calistir & pause & exit /b 1)\r\n\
powershell -NoProfile -ExecutionPolicy Bypass -File \"%~dp0KALDIR.ps1\"\r\n\
pause\r\n"
        .to_string()
}

pub fn _kaldir_ps1() -> String {
    format!(
        "$ErrorActionPreference = 'SilentlyContinue'\r\n\
$hosts = \"$env:SystemRoot\\System32\\drivers\\etc\\hosts\"\r\n\
$satirlar = Get-Content $hosts | Where-Object {{ $_ -notmatch 'cokluuyap-sira-kapisi' }}\r\n\
Set-Content -Path $hosts -Value $satirlar -Encoding ASCII\r\n\
$store = New-Object System.Security.Cryptography.X509Certificates.X509Store('Root','LocalMachine')\r\n\
$store.Open('ReadWrite')\r\n\
foreach ($c in @($store.Certificates)) {{\r\n\
  if ($c.Subject -like '*cokluuyap Sira Kapisi*') {{ $store.Remove($c) }}\r\n\
}}\r\n\
$store.Close()\r\n\
Write-Host 'Kaldirildi.'\r\n"
    )
}

pub fn kur(
    bot_ipler: &[String],
    kapi_ip: Option<&str>,
    aralik_sn: f64,
    mod_: &str,
) -> Result<PathBuf, String> {
    if bot_ipler.is_empty() {
        return Err("En az bir --bot-ip gerekli.".to_string());
    }
    for ip in bot_ipler {
        _lan_ipv4(ip, "Bot IP'si")?;
    }
    let kip = match kapi_ip {
        Some(s) if !s.trim().is_empty() => _lan_ipv4(s, "Kapı IP'si")?,
        _ => {
            let v = varsayilan_lan_ip();
            if v.is_empty() {
                return Err("Kapı IP'si tahmin edilemedi; --kapi-ip ile verin.".to_string());
            }
            v
        }
    };
    if mod_ != "sirala" && mod_ != "izle" {
        return Err("mod 'sirala' ya da 'izle' olmalı.".to_string());
    }
    let aralik = aralik_sn.clamp(0.0, 60.0);
    let klasor = _yol("bot_pc_icin");
    let (_, _, _, parmak, kok_sha256) = sertifika_uret(Some(&klasor))?;

    for (ad, icerik) in [
        ("KUR.bat", _kur_bat()),
        ("KUR.ps1", _kur_ps1(&kip, &kok_sha256)),
        ("KALDIR.bat", _kaldir_bat()),
        ("KALDIR.ps1", _kaldir_ps1()),
    ] {
        fs::write(klasor.join(ad), icerik.as_bytes()).map_err(|e| e.to_string())?;
    }

    let mut ayar = ayar_oku();
    ayar.etkin = true;
    ayar.mod_ = mod_.to_string();
    ayar.port = 443;
    ayar.aralik_sn = aralik;
    ayar.izinli_ipler = bot_ipler.to_vec();
    ayar.kapi_ip = Some(kip);
    ayar.kok_parmak_izi = Some(parmak);
    ayar.kok_sha256 = Some(kok_sha256);
    ayar_yaz(&ayar);
    Ok(klasor)
}

// ── Sıralama durumu & HTTP Proxy ──────────────────────────────────────────────────────────

pub struct Durum {
    pub aralik_sn: f64,
    pub sira: AsyncMutex<()>,
    pub son_bitis: StdMutex<Option<Instant>>,
    pub sayac: StdMutex<usize>,
    pub cakisma: StdMutex<usize>,
}

impl Durum {
    pub fn new(aralik_sn: f64) -> Self {
        Self {
            aralik_sn: aralik_sn.max(0.0),
            sira: AsyncMutex::new(()),
            son_bitis: StdMutex::new(None),
            sayac: StdMutex::new(0),
            cakisma: StdMutex::new(0),
        }
    }
}

pub fn _istemci_izinli(ip: &str, izinli: &HashSet<String>) -> bool {
    !izinli.is_empty() && izinli.contains(ip)
}

pub fn _yol_dogru(yol: &[u8]) -> bool {
    yol.starts_with(b"/") && !yol.starts_with(b"//") && !yol.starts_with(b"/\\")
}

fn log_kapi(satir: &str) {
    let dir = veri_klasoru();
    let _ = fs::create_dir_all(&dir);
    let path = _yol("kapi.log");
    if let Ok(meta) = fs::metadata(&path) {
        if meta.len() > 5_000_000 {
            let b3 = _yol("kapi.log.3");
            let b2 = _yol("kapi.log.2");
            let b1 = _yol("kapi.log.1");
            let _ = fs::remove_file(&b3);
            let _ = fs::rename(&b2, &b3);
            let _ = fs::rename(&b1, &b2);
            let _ = fs::rename(&path, &b1);
        }
    }
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&path) {
        let now = chrono::Local::now().format("%Y-%m-%d %H:%M:%S");
        let _ = writeln!(f, "{} {}", now, satir);
    }
}

static CANLI_IZINLI: Lazy<StdMutex<HashSet<String>>> = Lazy::new(|| StdMutex::new(HashSet::new()));
static DENETCI_ACIK: AtomicBool = AtomicBool::new(false);
static DENETCI_NOTIFY: Lazy<Notify> = Lazy::new(Notify::new);

pub fn _izinli_ayarla(liste: &[String]) {
    if let Ok(mut g) = CANLI_IZINLI.lock() {
        *g = liste.iter().cloned().collect();
    }
}

pub fn yenile() {
    DENETCI_NOTIFY.notify_one();
}

pub fn calisiyor_mu() -> bool {
    DENETCI_ACIK.load(Ordering::SeqCst)
}

pub fn _dongu_korumasi() -> Result<(), String> {
    use std::net::ToSocketAddrs;
    let Ok(addrs) = (UYAP_HOST, 443).to_socket_addrs() else {
        return Ok(());
    };
    for addr in addrs {
        let is_local = match addr.ip() {
            IpAddr::V4(v4) => v4.is_private() || v4.is_loopback() || v4.is_link_local(),
            IpAddr::V6(v6) => v6.is_loopback(),
        };
        if is_local {
            return Err(format!(
                "{} bu bilgisayarda {} adresine çözülüyor (hosts?). Kapı kendine bağlanırdı; bu PC'nin hosts dosyasındaki satırı kaldırın.",
                UYAP_HOST,
                addr.ip()
            ));
        }
    }
    Ok(())
}

struct KapiState {
    durum: Arc<Durum>,
    izle: bool,
    client: reqwest::Client,
    izinli_sabit: Option<HashSet<String>>,
}

async fn write_http_response<W: AsyncWriteExt + Unpin>(
    writer: &mut W,
    status: u16,
    reason: &str,
    headers: &[(String, String)],
    body: &[u8],
) -> std::io::Result<()> {
    let mut head = format!("HTTP/1.1 {} {}\r\n", status, reason);
    let mut has_cl = false;
    for (k, v) in headers {
        if k.eq_ignore_ascii_case("content-length") {
            has_cl = true;
        }
        head.push_str(&format!("{}: {}\r\n", k, v));
    }
    if !has_cl {
        head.push_str(&format!("content-length: {}\r\n", body.len()));
    }
    head.push_str("connection: close\r\n\r\n");
    writer.write_all(head.as_bytes()).await?;
    writer.write_all(body).await?;
    writer.flush().await
}

async fn write_plain_err<W: AsyncWriteExt + Unpin>(
    writer: &mut W,
    status: u16,
    metin: &str,
) -> std::io::Result<()> {
    let headers = vec![(
        "content-type".to_string(),
        "text/plain; charset=utf-8".to_string(),
    )];
    write_http_response(writer, status, "Error", &headers, metin.as_bytes()).await
}

async fn handle_tls_conn<S: AsyncReadExt + AsyncWriteExt + Unpin>(
    mut stream: S,
    peer_ip: String,
    state: Arc<KapiState>,
) {
    // Basit HTTP/1.1 istek okuyucu (headers + Content-Length / chunked)
    let mut buf = Vec::with_capacity(8192);
    let mut tmp = [0u8; 4096];
    let header_end = loop {
        match tokio::time::timeout(Duration::from_secs(30), stream.read(&mut tmp)).await {
            Ok(Ok(0)) | Ok(Err(_)) | Err(_) => return,
            Ok(Ok(n)) => {
                buf.extend_from_slice(&tmp[..n]);
                if let Some(pos) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                    break pos;
                }
                if buf.len() > 64 * 1024 {
                    let _ = write_plain_err(&mut stream, 431, "Istek basliklari cok buyuk.").await;
                    return;
                }
            }
        }
    };

    let head_str = String::from_utf8_lossy(&buf[..header_end]);
    let mut lines = head_str.split("\r\n");
    let req_line = match lines.next() {
        Some(l) => l,
        None => return,
    };
    let parts: Vec<&str> = req_line.split_whitespace().collect();
    if parts.len() < 2 {
        let _ = write_plain_err(&mut stream, 400, "Gecersiz istek.").await;
        return;
    }
    let method = parts[0].to_string();
    let target = parts[1].to_string();

    let izinli = match &state.izinli_sabit {
        Some(s) => _istemci_izinli(&peer_ip, s),
        None => CANLI_IZINLI
            .lock()
            .map(|s| _istemci_izinli(&peer_ip, &s))
            .unwrap_or(false),
    };
    if !izinli {
        log_kapi(&format!("RET istemci={} (izinli değil)", peer_ip));
        let _ = write_plain_err(&mut stream, 403, "Bu bilgisayar sira kapisini kullanamaz.").await;
        return;
    }

    let mut host = String::new();
    let mut content_length: usize = 0;
    let mut chunked = false;
    let mut fwd_headers: Vec<(String, String)> = Vec::new();

    for line in lines {
        if let Some((k, v)) = line.split_once(':') {
            let k_trim = k.trim();
            let k_lower = k_trim.to_ascii_lowercase();
            let v_trim = v.trim();
            if k_lower == "host" {
                host = v_trim.split(':').next().unwrap_or("").to_ascii_lowercase();
                continue;
            }
            if k_lower == "content-length" {
                content_length = v_trim.parse::<usize>().unwrap_or(0);
                continue;
            }
            if k_lower == "transfer-encoding" && v_trim.to_ascii_lowercase().contains("chunked") {
                chunked = true;
            }
            if _ISTEK_AT.contains(&k_lower.as_str()) {
                continue;
            }
            fwd_headers.push((k_trim.to_string(), v_trim.to_string()));
        }
    }

    if host != UYAP_HOST {
        let _ = write_plain_err(&mut stream, 421, "Yalniz avukat.uyap.gov.tr iletilir.").await;
        return;
    }
    fwd_headers.push(("host".to_string(), UYAP_HOST.to_string()));

    let (yol, sorgu) = match target.split_once('?') {
        Some((y, q)) => (y.to_string(), Some(q.to_string())),
        None => (target.clone(), None),
    };
    if !_yol_dogru(yol.as_bytes()) {
        log_kapi(&format!("RET istemci={} geçersiz hedef {:?}", peer_ip, yol));
        let _ = write_plain_err(&mut stream, 400, "Gecersiz istek hedefi.").await;
        return;
    }

    let mut govde = buf[header_end + 4..].to_vec();
    if chunked {
        // Chunked gövdeyi sonuna kadar oku (0\r\n\r\n)
        while !govde.windows(5).any(|w| w == b"0\r\n\r\n") {
            match tokio::time::timeout(Duration::from_secs(30), stream.read(&mut tmp)).await {
                Ok(Ok(0)) | Ok(Err(_)) | Err(_) => break,
                Ok(Ok(n)) => {
                    govde.extend_from_slice(&tmp[..n]);
                    if govde.len() > EN_BUYUK_GOVDE {
                        log_kapi(&format!("RET istemci={} gövde sınırı aşıldı", peer_ip));
                        let _ = write_plain_err(&mut stream, 413, "Istek govdesi cok buyuk.").await;
                        return;
                    }
                }
            }
        }
    } else {
        if content_length > EN_BUYUK_GOVDE {
            log_kapi(&format!("RET istemci={} gövde sınırı aşıldı", peer_ip));
            let _ = write_plain_err(&mut stream, 413, "Istek govdesi cok buyuk.").await;
            return;
        }
        while govde.len() < content_length {
            match tokio::time::timeout(Duration::from_secs(30), stream.read(&mut tmp)).await {
                Ok(Ok(0)) | Ok(Err(_)) | Err(_) => return,
                Ok(Ok(n)) => {
                    govde.extend_from_slice(&tmp[..n]);
                    if govde.len() > EN_BUYUK_GOVDE {
                        log_kapi(&format!("RET istemci={} gövde sınırı aşıldı", peer_ip));
                        let _ = write_plain_err(&mut stream, 413, "Istek govdesi cok buyuk.").await;
                        return;
                    }
                }
            }
        }
        govde.truncate(content_length);
    }

    let url = match sorgu {
        Some(ref q) if !q.is_empty() => format!("https://{}{}?{}", UYAP_HOST, yol, q),
        _ => format!("https://{}{}", UYAP_HOST, yol),
    };

    let req_method = reqwest::Method::from_bytes(method.as_bytes()).unwrap_or(reqwest::Method::GET);
    let gelis = Instant::now();
    let mut cakisti = false;

    let uyaptan_al = || {
        let client = state.client.clone();
        let req_method = req_method.clone();
        let url = url.clone();
        let fwd_headers = fwd_headers.clone();
        let govde = govde.clone();
        async move {
            let mut rb = client.request(req_method, &url);
            for (k, v) in fwd_headers {
                rb = rb.header(&k, &v);
            }
            if !govde.is_empty() {
                rb = rb.body(govde);
            }
            let resp = rb
                .send()
                .await
                .map_err(|e| (502u16, format!("UYAP'a ulasilamadi: {}", e)))?;
            let status = resp.status().as_u16();
            let mut resp_headers = Vec::new();
            for (k, v) in resp.headers() {
                let kl = k.as_str().to_ascii_lowercase();
                if _HOP.contains(&kl.as_str()) {
                    continue;
                }
                if let Ok(vs) = v.to_str() {
                    resp_headers.push((k.as_str().to_string(), vs.to_string()));
                }
            }
            let bytes = resp
                .bytes()
                .await
                .map_err(|e| (502u16, format!("UYAP yanit okuma hatasi: {}", e)))?;
            if bytes.len() > EN_BUYUK_YANIT {
                return Err((502u16, "UYAP yaniti cok buyuk.".to_string()));
            }
            Ok::<(u16, Vec<(String, String)>, Vec<u8>), (u16, String)>((
                status,
                resp_headers,
                bytes.to_vec(),
            ))
        }
    };

    let basla: Instant;
    let bitis: Instant;

    let result = if state.izle {
        cakisti = runtime::UYAP_WRITE_LOCK.locked() || runtime::aktif_istek_sayisi() > 0;
        basla = Instant::now();
        let r = tokio::time::timeout(Duration::from_secs_f64(KILIT_SURE_SINIRI), uyaptan_al()).await;
        bitis = Instant::now();
        match r {
            Ok(res) => res,
            Err(_) => {
                log_kapi(&format!(
                    "HATA {} {} {} sn içinde bitmedi",
                    method, yol, KILIT_SURE_SINIRI as i64
                ));
                let _ = write_plain_err(&mut stream, 504, "UYAP yaniti zamaninda tamamlanmadi.").await;
                return;
            }
        }
    } else {
        let _sira_guard = state.durum.sira.lock().await;
        let bekle_dur = {
            let son = state.durum.son_bitis.lock().ok().and_then(|g| *g);
            if let Some(sb) = son {
                let aralik = Duration::from_secs_f64(state.durum.aralik_sn);
                let hedef = sb + aralik;
                let now = Instant::now();
                if hedef > now {
                    Some(hedef - now)
                } else {
                    None
                }
            } else {
                None
            }
        };
        if let Some(d) = bekle_dur {
            tokio::time::sleep(d).await;
        }
        let _batch_guard = runtime::batch_write().await;
        let _ = runtime::aktif_istekler_bitsin(5.0).await;
        basla = Instant::now();
        let r = tokio::time::timeout(Duration::from_secs_f64(KILIT_SURE_SINIRI), uyaptan_al()).await;
        bitis = Instant::now();
        if let Ok(mut g) = state.durum.son_bitis.lock() {
            *g = Some(bitis);
        }
        match r {
            Ok(res) => res,
            Err(_) => {
                log_kapi(&format!(
                    "HATA {} {} {} sn içinde bitmedi",
                    method, yol, KILIT_SURE_SINIRI as i64
                ));
                let _ = write_plain_err(&mut stream, 504, "UYAP yaniti zamaninda tamamlanmadi.").await;
                return;
            }
        }
    };

    match result {
        Ok((status, resp_headers, resp_body)) => {
            let _ = write_http_response(&mut stream, status, "OK", &resp_headers, &resp_body).await;
            if let Ok(mut c) = state.durum.sayac.lock() {
                *c += 1;
            }
            if cakisti {
                if let Ok(mut c) = state.durum.cakisma.lock() {
                    *c += 1;
                }
            }
            let bekleme_ms = basla.saturating_duration_since(gelis).as_millis();
            let sure_ms = bitis.saturating_duration_since(basla).as_millis();
            let mut log_line = format!(
                "{} {} {} {} {} bekleme={}ms sure={}ms",
                if state.izle { "IZLE" } else { "SIRA" },
                peer_ip,
                method,
                yol,
                status,
                bekleme_ms,
                sure_ms
            );
            if cakisti {
                log_line.push_str(" CAKISMA(bizim istek uctaydi)");
            }
            log_kapi(&log_line);
        }
        Err((code, msg)) => {
            log_kapi(&format!("HATA {} {} {}", method, yol, msg));
            let _ = write_plain_err(&mut stream, code, &msg).await;
        }
    }
}

fn load_tls_acceptor() -> Result<TlsAcceptor, String> {
    let crt_bytes = fs::read(_yol("sunucu.crt")).map_err(|e| format!("sunucu.crt: {}", e))?;
    let key_raw = fs::read(_yol("sunucu.key")).map_err(|e| format!("sunucu.key: {}", e))?;
    let parola = anahtar_parolasi()?;
    let key_pem = decrypt_private_key(&key_raw, &parola)?;

    let mut crt_cur = std::io::Cursor::new(&crt_bytes);
    let certs: Vec<CertificateDer<'static>> = rustls_pemfile::certs(&mut crt_cur)
        .filter_map(|r| r.ok())
        .collect();
    if certs.is_empty() {
        return Err("sunucu.crt içinde sertifika bulunamadı".to_string());
    }

    let mut key_cur = std::io::Cursor::new(&key_pem);
    let key_der = rustls_pemfile::private_key(&mut key_cur)
        .map_err(|e| format!("sunucu.key ayrıştırma: {}", e))?
        .ok_or_else(|| "sunucu.key içinde özel anahtar yok".to_string())?;

    let mut config = rustls::ServerConfig::builder()
        .with_no_client_auth()
        .with_single_cert(certs, key_der)
        .map_err(|e| format!("TLS yapılandırma hatası: {}", e))?;
    config.alpn_protocols = vec![b"http/1.1".to_vec()];
    Ok(TlsAcceptor::from(Arc::new(config)))
}

async fn run_kapi_server(
    ayar: Ayar,
    shutdown_notify: Arc<Notify>,
) -> Result<(), String> {
    if !ayar.etkin {
        return Ok(());
    }
    let kapi_ip = _lan_ipv4(ayar.kapi_ip.as_deref().unwrap_or(""), "Kapı IP'si")?;
    let izinli = ayar.izinli_ipler.clone();
    if izinli.is_empty() {
        return Err("izinli bot IP'si yok".to_string());
    }
    for ip in &izinli {
        _lan_ipv4(ip, "Bot IP'si")?;
    }
    _dongu_korumasi()?;
    let acceptor = load_tls_acceptor()?;

    let client = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(15))
        .timeout(Duration::from_secs(120))
        .build()
        .map_err(|e| format!("HTTP istemcisi hatası: {}", e))?;

    let mod_ = ayar.mod_.as_str();
    let aralik_sn = ayar.aralik_sn;

    let state = Arc::new(KapiState {
        durum: Arc::new(Durum::new(aralik_sn)),
        izle: mod_ == "izle",
        client,
        izinli_sabit: None,
    });

    let addr = format!("{}:443", kapi_ip);
    let listener = TcpListener::bind(&addr)
        .await
        .map_err(|e| format!("{} dinlenemedi: {}", addr, e))?;
    let sem = Arc::new(Semaphore::new(EN_COK_BAGLANTI));

    println!(
        "[i] Sıra kapısı açık: {}:443 mod={} aralık={}sn izinli={:?} (günlük: {})",
        kapi_ip,
        mod_,
        aralik_sn,
        izinli,
        _yol("kapi.log").display()
    );

    DENETCI_ACIK.store(true, Ordering::SeqCst);

    loop {
        tokio::select! {
            _ = shutdown_notify.notified() => {
                break;
            }
            accept_res = listener.accept() => {
                match accept_res {
                    Ok((tcp_stream, peer_addr)) => {
                        let permit = match sem.clone().try_acquire_owned() {
                            Ok(p) => p,
                            Err(_) => continue,
                        };
                        let acc = acceptor.clone();
                        let st = state.clone();
                        let peer_ip = peer_addr.ip().to_string();
                        tokio::spawn(async move {
                            let _permit = permit;
                            if let Ok(tls_stream) = acc.accept(tcp_stream).await {
                                handle_tls_conn(tls_stream, peer_ip, st).await;
                            }
                        });
                    }
                    Err(e) => {
                        println!("[!] Sıra kapısı kabul hatası: {}", e);
                        break;
                    }
                }
            }
        }
    }

    DENETCI_ACIK.store(false, Ordering::SeqCst);
    Ok(())
}

/// Paylaşım döngüsünde koşar; ayar değişince kapıyı yeniden kurar.
pub async fn denetci() {
    let mut current_stop: Option<Arc<Notify>> = None;
    let mut current_task: Option<tokio::task::JoinHandle<()>> = None;
    let mut imza: Option<(bool, String, String, String, Option<SystemTime>, bool)> = None;

    loop {
        let ayar = ayar_oku();
        let crt_zaman = fs::metadata(_yol("sunucu.crt"))
            .and_then(|m| m.modified())
            .ok();
        let etkin = ayar.etkin;
        let kapi_ip = ayar.kapi_ip.clone().unwrap_or_default();
        let mod_ = ayar.mod_.clone();
        let aralik = format!("{:.3}", ayar.aralik_sn);
        let izinli_list = ayar.izinli_ipler.clone();
        let has_izinli = !izinli_list.is_empty();
        _izinli_ayarla(&izinli_list);

        let yeni_imza = (etkin, kapi_ip, mod_, aralik, crt_zaman, has_izinli);
        let task_done = current_task
            .as_ref()
            .map(|h| h.is_finished())
            .unwrap_or(true);

        if imza.as_ref() != Some(&yeni_imza) || task_done {
            if let Some(stop) = current_stop.take() {
                stop.notify_waiters();
            }
            if let Some(h) = current_task.take() {
                let _ = tokio::time::timeout(Duration::from_secs(5), h).await;
            }
            DENETCI_ACIK.store(false, Ordering::SeqCst);

            if etkin && has_izinli {
                let stop = Arc::new(Notify::new());
                let stop_c = stop.clone();
                let ayar_c = ayar.clone();
                let h = tokio::spawn(async move {
                    if let Err(e) = run_kapi_server(ayar_c, stop_c).await {
                        println!("[!] Sıra kapısı durdu: {}", e);
                    }
                    DENETCI_ACIK.store(false, Ordering::SeqCst);
                });
                current_stop = Some(stop);
                current_task = Some(h);
            }
            imza = Some(yeni_imza);
        }

        if etkin && has_izinli {
            let _ = tokio::time::timeout(Duration::from_secs(300), DENETCI_NOTIFY.notified()).await;
        } else {
            DENETCI_NOTIFY.notified().await;
        }
    }
}

pub fn komut_calistir_metin(args: &[String]) -> String {
    match main_cli(args) {
        Ok(()) => String::new(),
        Err(e) => e,
    }
}

pub fn main_cli(args: &[String]) -> Result<(), String> {
    if args.is_empty() {
        return Err("Komut gerekli: kur | durum | ac | kapat | mod".to_string());
    }
    match args[0].as_str() {
        "kur" => {
            let mut bot_ipler = Vec::new();
            let mut kapi_ip: Option<String> = None;
            let mut aralik = 1.0;
            let mut mod_ = "sirala".to_string();
            let mut i = 1;
            while i < args.len() {
                match args[i].as_str() {
                    "--bot-ip" if i + 1 < args.len() => {
                        bot_ipler.push(args[i + 1].clone());
                        i += 2;
                    }
                    "--kapi-ip" if i + 1 < args.len() => {
                        kapi_ip = Some(args[i + 1].clone());
                        i += 2;
                    }
                    "--aralik" if i + 1 < args.len() => {
                        aralik = args[i + 1].parse().unwrap_or(1.0);
                        i += 2;
                    }
                    "--mod" if i + 1 < args.len() => {
                        mod_ = args[i + 1].clone();
                        i += 2;
                    }
                    _ => i += 1,
                }
            }
            let klasor = kur(&bot_ipler, kapi_ip.as_deref(), aralik, &mod_)?;
            let ayar = ayar_oku();
            println!(
                "Kapı IP'si: {}  (yanlışsa --kapi-ip ile yeniden kurun)",
                ayar.kapi_ip.as_deref().unwrap_or("")
            );
            println!("Bot PC'sine kopyalanacak klasör: {}", klasor.display());
            println!("Orada KUR.bat'ı yönetici olarak çalıştırın; sonra cokluuyap paylaşımını yeniden başlatın.");
            let sha = ayar.kok_sha256.as_deref().unwrap_or("");
            println!("KUR.bat'ın göstereceği kök sertifika parmak izi (SHA256) BUNUNLA AYNI olmalı:");
            let chunks: Vec<&str> = sha
                .as_bytes()
                .chunks(8)
                .map(|c| std::str::from_utf8(c).unwrap_or(""))
                .collect();
            println!("  {}", chunks.join(" "));
            Ok(())
        }
        "durum" => {
            let ayar = ayar_oku();
            println!("{}", serde_json::to_string_pretty(&ayar).unwrap_or_default());
            Ok(())
        }
        "ac" | "kapat" => {
            let mut ayar = ayar_oku();
            ayar.etkin = args[0] == "ac";
            ayar_yaz(&ayar);
            println!("{}", serde_json::to_string_pretty(&ayar).unwrap_or_default());
            println!("Değişiklik paylaşım yeniden başlatılınca geçerli olur.");
            Ok(())
        }
        "mod" => {
            let val = args.get(1).map(|s| s.as_str()).unwrap_or("");
            if val != "sirala" && val != "izle" {
                return Err("mod 'sirala' ya da 'izle' olmalı.".to_string());
            }
            let mut ayar = ayar_oku();
            ayar.mod_ = val.to_string();
            ayar_yaz(&ayar);
            println!("{}", serde_json::to_string_pretty(&ayar).unwrap_or_default());
            println!("Değişiklik paylaşım yeniden başlatılınca geçerli olur.");
            Ok(())
        }
        other => Err(format!("Bilinmeyen komut: {}", other)),
    }
}
