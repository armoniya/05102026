//! Tarayıcıyı yerel UYAP proxy'sine (127.0.0.1:8800) GÜVENLİ biçimde açmak için bağlantı üretir.
//!
//! 1. Program (jetonu okuyabilen aynı Windows kullanıcısı) proxy'ye jetonla POST atar →
//!    60 sn geçerli, tek kullanımlık bir kod alır.
//! 2. Tarayıcı  `/__uyap_yerel__/giris?k=<kod>&hedef=/giris`  adresine açılır → proxy kodu doğrular,
//!    çerezi verir ve `/giris`'e yönlendirir. Asıl jeton URL'ye/geçmişe hiç düşmez.
//!
//! Kod alınamazsa (alıcı/üye modunda home_client'ta bu uç yok, proxy kapalı, jeton yok) düz
//! `/giris` adresi döner — üye tarafı eskisi gibi çalışır.

use std::path::PathBuf;
use std::time::Duration;

pub const KOD_YOLU: &str = "/__uyap_yerel__/kod";
pub const GIRIS_YOLU: &str = "/__uyap_yerel__/giris";

fn home_dir() -> PathBuf {
    #[cfg(windows)]
    {
        if let Ok(p) = std::env::var("USERPROFILE") {
            if !p.is_empty() {
                return PathBuf::from(p);
            }
        }
        let drive = std::env::var("HOMEDRIVE").unwrap_or_default();
        let path = std::env::var("HOMEPATH").unwrap_or_default();
        if !drive.is_empty() || !path.is_empty() {
            return PathBuf::from(format!("{}{}", drive, path));
        }
        PathBuf::from(".")
    }
    #[cfg(not(windows))]
    {
        std::env::var("HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from("."))
    }
}

/// `uyap_proxy._local_token_file` ile AYNI yol.
pub fn _jeton_dosyasi() -> PathBuf {
    #[cfg(windows)]
    {
        let base = std::env::var("LOCALAPPDATA")
            .ok()
            .filter(|s| !s.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(home_dir);
        base.join("UyapIcra").join("gw_local_token")
    }
    #[cfg(not(windows))]
    {
        let base = std::env::var("XDG_CONFIG_HOME")
            .ok()
            .filter(|s| !s.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| home_dir().join(".config"));
        base.join("uyapicra").join("gw_local_token")
    }
}

pub fn _jeton() -> String {
    if let Ok(tok) = std::env::var("UYAP_LOCAL_TOKEN") {
        let t = tok.trim().to_string();
        if !t.is_empty() {
            return t;
        }
    }
    std::fs::read_to_string(_jeton_dosyasi())
        .map(|s| s.trim().to_string())
        .unwrap_or_default()
}

/// Tarayıcıda açılacak adres: mümkünse tek kullanımlık güvenli giriş bağlantısı, değilse düz adres.
pub fn tarayici_giris_url(port: u16, hedef: &str, timeout_secs: f64) -> String {
    let base = format!("http://127.0.0.1:{}", port);
    let duz = format!("{}{}", base, hedef);
    let tok = _jeton();
    if tok.is_empty() {
        return duz;
    }

    let client = match reqwest::blocking::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs_f64(timeout_secs.max(0.1)))
        .build()
    {
        Ok(c) => c,
        Err(_) => return duz,
    };

    let resp = match client
        .post(format!("{}{}", base, KOD_YOLU))
        .header("X-Uyap-Local-Token", tok)
        .header("Content-Type", "application/json")
        .body("{}")
        .send()
    {
        Ok(r) => r,
        Err(_) => return duz,
    };

    if !resp.status().is_success() {
        return duz;
    }

    let v: serde_json::Value = match resp.json() {
        Ok(j) => j,
        Err(_) => return duz,
    };
    let kod = v.get("kod").and_then(|k| k.as_str()).unwrap_or("");
    if kod.is_empty() {
        return duz;
    }

    format!(
        "{}{}?k={}&hedef={}",
        base,
        GIRIS_YOLU,
        urlencoding::encode(kod),
        urlencoding::encode(hedef)
    )
}
