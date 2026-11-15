//! Yerel HTTP kapısı için tarayıcı kaynaklı saldırı kontrolleri (DNS rebinding / çapraz-site).
//!
//! Saf fonksiyonlar — ağ, UYAP ya da global durum kullanmaz; bu yüzden birim testiyle sınanabilir.
//! Kurallar `uyap_proxy.require_auth`'un tarayıcı korumasıyla AYNIDIR:
//!
//!   • Host başlığı gerçek bir DNS adıysa → ret (DNS rebinding: saldırganın alan adı 127.0.0.1'e
//!     çözülse bile tarayıcı Host'ta O ADI gönderir; meşru istemciler hep IP/localhost kullanır).
//!   • Sec-Fetch-Site: cross-site ve üst-düzey belge gezinmesi DEĞİLSE → ret (başka sitenin
//!     <img>/<script>/fetch'i).
//!   • Origin "null" ya da yerel olmayan bir adsa → ret.
//!
//! Python/Rust istemcileri Origin/Sec-Fetch göndermez ve Host'ta IP kullanır →
//! etkilenmez. `UYAP_ALLOWED_HOSTS` ortam değişkeniyle ek ad beyaz listeye alınabilir.

use std::collections::{HashMap, HashSet};
use std::net::IpAddr;

/// `UYAP_ALLOWED_HOSTS` ortam değişkeninden izinli ek ana makine adlarını döndürür.
pub fn izinli_adlar() -> HashSet<String> {
    std::env::var("UYAP_ALLOWED_HOSTS")
        .unwrap_or_default()
        .split(',')
        .map(|h| h.trim().to_lowercase())
        .filter(|h| !h.is_empty())
        .collect()
}

/// Host ya da Origin başlığından yalnız ana makine adı
/// (`http://127.0.0.1:8800` → `127.0.0.1`, `[::1]:8800` → `::1`).
pub fn hostname_of(value: &str) -> String {
    let mut v = value.trim();
    if v.is_empty() {
        return String::new();
    }
    if let Some((_, after)) = v.split_once("://") {
        v = after;
    }
    if let Some((before, _)) = v.split_once('/') {
        v = before;
    }
    if let Some(rest) = v.strip_prefix('[') {
        return rest.split(']').next().unwrap_or("").to_string();
    }
    if v.matches(':').count() == 1 {
        if let Some((before, _)) = v.split_once(':') {
            v = before;
        }
    }
    v.to_string()
}

/// Ana makine adı boş, `localhost`, izinli listede veya geçerli bir IP adresi mi?
pub fn ip_ya_da_yerel(hostname: &str, ek_adlar: Option<&HashSet<String>>) -> bool {
    let h = hostname.trim().to_lowercase();
    if h.is_empty() || h == "localhost" {
        return true;
    }
    let varsayilan;
    let liste = match ek_adlar {
        Some(s) => s,
        None => {
            varsayilan = izinli_adlar();
            &varsayilan
        }
    };
    if liste.contains(&h) {
        return true;
    }
    h.parse::<IpAddr>().is_ok()
}

/// Başlık okuma soyutlaması (`http::HeaderMap` ve `HashMap<String, String>` için).
pub trait HeaderLookup {
    fn get_header_ci(&self, name: &str) -> String;
}

impl HeaderLookup for http::HeaderMap {
    fn get_header_ci(&self, name: &str) -> String {
        self.get(name)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
            .to_string()
    }
}

impl<S: std::hash::BuildHasher> HeaderLookup for HashMap<String, String, S> {
    fn get_header_ci(&self, name: &str) -> String {
        if let Some(v) = self.get(name) {
            return v.clone();
        }
        let lower = name.to_lowercase();
        if let Some(v) = self.get(&lower) {
            return v.clone();
        }
        for (k, v) in self.iter() {
            if k.eq_ignore_ascii_case(name) {
                return v.clone();
            }
        }
        String::new()
    }
}

/// İstek reddedilmeliyse sebep metni, değilse `None`.
pub fn tarayici_reddi<H: HeaderLookup + ?Sized>(
    headers: &H,
    method: &str,
    ek_adlar: Option<&HashSet<String>>,
) -> Option<&'static str> {
    let host_hn = hostname_of(&headers.get_header_ci("Host"));
    if !host_hn.is_empty() && !ip_ya_da_yerel(&host_hn, ek_adlar) {
        return Some("Beklenmeyen Host başlığı (olası DNS rebinding).");
    }
    let sfs = headers.get_header_ci("Sec-Fetch-Site").trim().to_lowercase();
    if sfs == "cross-site" {
        let mode = headers.get_header_ci("Sec-Fetch-Mode").trim().to_lowercase();
        let dest = headers.get_header_ci("Sec-Fetch-Dest").trim().to_lowercase();
        let m = method.to_uppercase();
        if !(mode == "navigate" && (dest == "document" || dest.is_empty()) && (m == "GET" || m == "HEAD")) {
            return Some("Çapraz-site alt-kaynak isteği reddedildi.");
        }
    }
    let origin = headers.get_header_ci("Origin");
    if !origin.is_empty()
        && (origin.eq_ignore_ascii_case("null") || !ip_ya_da_yerel(&hostname_of(&origin), ek_adlar))
    {
        return Some("Yetkisiz Origin (çapraz-site istek reddedildi).");
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn map(pairs: &[(&str, &str)]) -> HashMap<String, String> {
        pairs.iter().map(|(k, v)| ((*k).to_string(), (*v).to_string())).collect()
    }

    #[test]
    fn test_bicimler() {
        assert_eq!(hostname_of("http://127.0.0.1:8800"), "127.0.0.1");
        assert_eq!(hostname_of("127.0.0.1:8800"), "127.0.0.1");
        assert_eq!(hostname_of("[::1]:8800"), "::1");
        assert_eq!(hostname_of("https://kotu.example.com/yol"), "kotu.example.com");
        assert_eq!(hostname_of(""), "");
    }

    #[test]
    fn test_tarayici_reddi() {
        let yok = HashSet::new();
        assert!(tarayici_reddi(&map(&[("Host", "127.0.0.1:8800")]), "POST", Some(&yok)).is_none());
        assert!(tarayici_reddi(
            &map(&[
                ("Host", "127.0.0.1:8800"),
                ("Origin", "http://127.0.0.1:8800"),
                ("Sec-Fetch-Site", "same-origin"),
                ("Sec-Fetch-Mode", "cors"),
            ]),
            "POST",
            Some(&yok)
        )
        .is_none());
        assert!(tarayici_reddi(
            &map(&[
                ("Host", "127.0.0.1:8800"),
                ("Sec-Fetch-Site", "cross-site"),
                ("Sec-Fetch-Mode", "navigate"),
                ("Sec-Fetch-Dest", "document"),
            ]),
            "GET",
            Some(&yok)
        )
        .is_none());
        assert!(tarayici_reddi(&map(&[("Host", "localhost:8800")]), "GET", Some(&yok)).is_none());
        assert!(tarayici_reddi(
            &map(&[("Host", "[::1]:8800"), ("Origin", "http://[::1]:8800")]),
            "GET",
            Some(&yok)
        )
        .is_none());
        let mut wl = HashSet::new();
        wl.insert("ofis.local".to_string());
        assert!(tarayici_reddi(&map(&[("Host", "ofis.local:8800")]), "GET", Some(&wl)).is_none());
        assert!(tarayici_reddi(&map(&[("host", "kotu.example.com")]), "GET", Some(&yok)).is_some());
        assert!(tarayici_reddi(&map(&[("Host", "kotu.example.com:8800")]), "GET", Some(&yok)).is_some());
        assert!(tarayici_reddi(
            &map(&[
                ("Host", "127.0.0.1:8800"),
                ("Sec-Fetch-Site", "cross-site"),
                ("Sec-Fetch-Mode", "cors"),
                ("Sec-Fetch-Dest", "empty"),
            ]),
            "GET",
            Some(&yok)
        )
        .is_some());
        assert!(tarayici_reddi(
            &map(&[
                ("Host", "127.0.0.1:8800"),
                ("Sec-Fetch-Site", "cross-site"),
                ("Sec-Fetch-Mode", "navigate"),
                ("Sec-Fetch-Dest", "document"),
            ]),
            "POST",
            Some(&yok)
        )
        .is_some());
        assert!(tarayici_reddi(
            &map(&[("Host", "127.0.0.1:8800"), ("Origin", "https://kotu.example.com")]),
            "POST",
            Some(&yok)
        )
        .is_some());
        assert!(tarayici_reddi(
            &map(&[("Host", "127.0.0.1:8800"), ("Origin", "null")]),
            "POST",
            Some(&yok)
        )
        .is_some());
    }
}
