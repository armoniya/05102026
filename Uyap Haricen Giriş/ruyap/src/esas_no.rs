//! Ortak "gerçek esas no" sorgusu (`uyap_core/esas_no.py` 1:1 portu)
//! =====================================================================
//! xml_takip/ipotek/mts akışlarının HER BİRİ "tevzi" adımından sonra yalnızca
//! UYAP'ın oturumluk/opak dosyaId'sini bilir — kullanıcının UYAP ekranında (ör.
//! "Tamamlanmayan Dosyalar" veya harç ödemesi sonrası) gördüğü KALICI esas no
//! ("2026/894734" gibi) ayrı bir adımda doğrulanır.

use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Value};

use crate::ipotek::{_guvenli_liste, _temiz_buyuk};
use crate::jobs::JobContext;

pub static _ESAS_NO_DESENI: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^\d{4}/\d+$").expect("geçerli esas no regex"));

pub async fn _api_json(
    ctx: &JobContext,
    path: &str,
    payload: Option<&Value>,
) -> Result<Value, String> {
    let body = payload.cloned().unwrap_or_else(|| json!({}));
    let resp = ctx
        .uyap_json("POST", path, &body)
        .await
        .map_err(|e| e.to_string())?;
    if resp.status_code >= 400 {
        return Err(format!("UYAP '{}' HTTP {} döndürdü.", path, resp.status_code));
    }
    serde_json::from_str(&resp.text).map_err(|e| format!("JSON ayrıştırma hatası ({}): {}", path, e))
}

/// `hedef_adlar`: eşleştirilecek taraf ad(soyad)/unvan metinleri (ör.
/// `["AYŞE YUNUSOĞLU"]` ya da `["ABC TİCARET LTD ŞTİ"]`) — 'Tamamlanmayan
/// Dosyalar' kaydındaki 'taraflar' serbest metninde HEPSİ geçen dosya aranır
/// (büyük/küçük harf ve TR karakter farkına duyarsız alt-dize eşleşmesi).
///
/// Eşleşen kayıtta gerçek esas no varsa (`"2026/894734"` gibi; `"Ödeme
/// Yapılmadı!"` gibi yer tutucu DEĞİL) o no'yu, yoksa `None` döner. Birden fazla
/// dosya eşleşirse (ör. aynı borçluya karşı başka açık dosyalar) İLK gerçek
/// esas no'lu olanı döner.
pub async fn bul(
    ctx: &JobContext,
    hedef_adlar: &[String],
    dosya_tur_kod: i64,
) -> Result<Option<String>, String> {
    let temiz_hedefler: Vec<String> = hedef_adlar
        .iter()
        .filter(|a| !a.is_empty())
        .map(|a| _temiz_buyuk(a))
        .filter(|a| !a.is_empty())
        .collect();
    if temiz_hedefler.is_empty() {
        return Ok(None);
    }

    let veri = _api_json(
        ctx,
        "tamamlanmayanDosyalar_brd.ajx",
        Some(&json!({ "dosyaTurKod": dosya_tur_kod })),
    )
    .await?;
    let liste = _guvenli_liste(&veri);

    for k in liste {
        let taraflar = k
            .get("taraflar")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let taraflar_buyuk = _temiz_buyuk(taraflar);
        if temiz_hedefler.iter().all(|ad| taraflar_buyuk.contains(ad.as_str())) {
            let dn = k
                .get("dosyaNo")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim();
            if _ESAS_NO_DESENI.is_match(dn) {
                return Ok(Some(dn.to_string()));
            }
        }
    }

    Ok(None)
}
