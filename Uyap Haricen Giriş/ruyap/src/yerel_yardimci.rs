//! Uzak bilgisayardaki yerel yardımcı (19.11.2026): cokluuyap.com'dan açılan UYAP sayfasında "Paylaş"a basılınca
//! belge, O bilgisayarda çalışan Çoklu UYAP programına verilir ve DOSYA OLARAK panoya konur (ofisteki gibi Ctrl+V).
//!
//! Neden ayrı bir kapı: ofis vekili (127.0.0.1:8800) yalnız paylaşım açıkken çalışır; istemci rolünde yerel sunucu
//! yoktu. Bu kapı program açık olduğu sürece her rolde 127.0.0.1:8801'de dinler, yalnız iki uç sunar:
//!   GET  /__yardimci__/ping    → {"app":"cokluuyap-yardimci"}
//!   POST /__yardimci__/paylas  (gövde: belge; başlık X-Cu-Ad = Base64(UTF-8 ad)) → "pano"
//!
//! Güvenlik:
//!   • Yalnız döngü adresinden dinlenir (127.0.0.1).
//!   • Yalnız izinli kökenler (cokluuyap.com; `uyap_proxy::agent_koken_izinli`) — tarayıcı Origin başlığını sahteleyemez.
//!     Origin'siz istek (tarayıcı dışı) reddedilir.
//!   • Host başlığı 127.0.0.1 / localhost olmalı (DNS rebinding).
//!   • Tek yaptığı iş belgeyi panoya koymak; dosya okumaz, komut çalıştırmaz.

use axum::body::Body;
use axum::extract::DefaultBodyLimit;
use axum::http::{HeaderMap, Method, StatusCode};
use axum::response::Response;
use axum::routing::get;
use axum::Router;
use base64::Engine;

pub const PORT: u16 = 8801;
const GOVDE_EN_COK: usize = 60 * 1024 * 1024;

/// İzinli kökenden gelmişse CORS / yerel ağ (Private Network Access) yanıt başlıkları; değilse None.
pub fn cors_basliklari(h: &HeaderMap) -> Option<Vec<(&'static str, String)>> {
    let origin = h.get("origin").and_then(|v| v.to_str().ok())?.trim_end_matches('/').to_string();
    if !crate::uyap_proxy::agent_koken_izinli(&origin) {
        return None;
    }
    Some(vec![
        ("Access-Control-Allow-Origin", origin),
        ("Vary", "Origin".into()),
        ("Access-Control-Allow-Methods", "GET, POST, OPTIONS".into()),
        ("Access-Control-Allow-Headers", "Content-Type, X-Cu-Ad, X-Cu-Aciklama".into()),
        ("Access-Control-Allow-Private-Network", "true".into()),
        ("Access-Control-Max-Age", "600".into()),
        ("Cache-Control", "no-store".into()),
    ])
}

/// Host başlığı döngü adresi mi (DNS rebinding: saldırganın alan adı 127.0.0.1'e çözülse de Host'ta o ad gelir).
pub fn host_yerel(h: &HeaderMap) -> bool {
    let host = h.get("host").and_then(|v| v.to_str().ok()).unwrap_or("");
    let ad = host.rsplit_once(':').map(|(a, _)| a).unwrap_or(host);
    matches!(ad, "127.0.0.1" | "localhost" | "[::1]")
}

fn yanit(kod: StatusCode, basliklar: &[(&'static str, String)], govde: impl Into<Body>, tur: &str) -> Response {
    let mut b = Response::builder().status(kod).header("Content-Type", tur);
    for (k, v) in basliklar {
        b = b.header(*k, v);
    }
    b.body(govde.into()).unwrap()
}

/// Ön kontrol (OPTIONS) ve ortak denetim: izinsizse yanıt döner.
fn denetle(h: &HeaderMap) -> Result<Vec<(&'static str, String)>, Response> {
    if !host_yerel(h) {
        return Err(yanit(StatusCode::FORBIDDEN, &[], "Geçersiz adres.", "text/plain; charset=utf-8"));
    }
    cors_basliklari(h).ok_or_else(|| yanit(StatusCode::FORBIDDEN, &[], "İzinsiz köken.", "text/plain; charset=utf-8"))
}

async fn on_kontrol(h: HeaderMap) -> Response {
    match denetle(&h) {
        Ok(b) => yanit(StatusCode::NO_CONTENT, &b, Body::empty(), "text/plain"),
        Err(r) => r,
    }
}

async fn ping(h: HeaderMap) -> Response {
    match denetle(&h) {
        Ok(b) => yanit(StatusCode::OK, &b, r#"{"app":"cokluuyap-yardimci"}"#, "application/json"),
        Err(r) => r,
    }
}

async fn paylas(method: Method, h: HeaderMap, govde: axum::body::Bytes) -> Response {
    let b = match denetle(&h) {
        Ok(b) => b,
        Err(r) => return r,
    };
    if method != Method::POST {
        return yanit(StatusCode::METHOD_NOT_ALLOWED, &b, "Yalnız POST.", "text/plain; charset=utf-8");
    }
    if govde.is_empty() {
        return yanit(StatusCode::BAD_REQUEST, &b, "Belge boş.", "text/plain; charset=utf-8");
    }
    let ad = h
        .get("x-cu-ad")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| base64::engine::general_purpose::STANDARD.decode(v.trim()).ok())
        .and_then(|v| String::from_utf8(v).ok())
        .filter(|s| !s.trim().is_empty())
        .unwrap_or_else(|| "belge".into());
    let ad = crate::paylas_win::guvenli_ad(&ad);
    let sonuc = tokio::task::spawn_blocking(move || crate::paylas_win::panoya_kopyala(&ad, &govde)).await;
    match sonuc {
        Ok(Ok(_)) => yanit(StatusCode::OK, &b, "pano", "text/plain; charset=utf-8"),
        Ok(Err(e)) => yanit(StatusCode::INTERNAL_SERVER_ERROR, &b, e, "text/plain; charset=utf-8"),
        Err(_) => yanit(StatusCode::INTERNAL_SERVER_ERROR, &b, "Kopyalama yarıda kaldı.", "text/plain; charset=utf-8"),
    }
}

pub fn router() -> Router {
    Router::new()
        .route("/__yardimci__/ping", get(ping).options(on_kontrol))
        .route("/__yardimci__/paylas", axum::routing::post(paylas).options(on_kontrol))
        .layer(DefaultBodyLimit::max(GOVDE_EN_COK))
}

/// Yardımcıyı kendi iş parçacığında başlatır. Kapı doluysa (ikinci program kopyası) sessizce vazgeçer.
pub fn baslat() {
    let _ = std::thread::Builder::new().name("yerel-yardimci".into()).spawn(|| {
        let Ok(rt) = tokio::runtime::Builder::new_current_thread().enable_all().build() else { return };
        rt.block_on(async {
            match tokio::net::TcpListener::bind(("127.0.0.1", PORT)).await {
                Ok(l) => {
                    let _ = axum::serve(l, router()).await;
                }
                Err(e) => eprintln!("[yardımcı] 127.0.0.1:{PORT} açılamadı: {e}"),
            }
        });
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderValue;

    fn bas(origin: Option<&str>, host: &str) -> HeaderMap {
        let mut h = HeaderMap::new();
        h.insert("host", HeaderValue::from_str(host).unwrap());
        if let Some(o) = origin {
            h.insert("origin", HeaderValue::from_str(o).unwrap());
        }
        h
    }

    #[test]
    fn koken_ve_host_denetimi() {
        assert!(denetle(&bas(Some("https://www.cokluuyap.com"), "127.0.0.1:8801")).is_ok());
        assert!(denetle(&bas(Some("https://cokluuyap.com/"), "localhost:8801")).is_ok());
        assert!(denetle(&bas(Some("https://kotu.example"), "127.0.0.1:8801")).is_err(), "başka site");
        assert!(denetle(&bas(None, "127.0.0.1:8801")).is_err(), "Origin'siz istek");
        assert!(denetle(&bas(Some("https://www.cokluuyap.com"), "kotu.example:8801")).is_err(), "DNS rebinding");
        let b = cors_basliklari(&bas(Some("https://www.cokluuyap.com"), "127.0.0.1:8801")).unwrap();
        assert!(b.iter().any(|(k, v)| *k == "Access-Control-Allow-Private-Network" && v == "true"));
        assert!(b.iter().any(|(k, v)| *k == "Access-Control-Allow-Headers" && v.contains("X-Cu-Ad")));
    }
}
