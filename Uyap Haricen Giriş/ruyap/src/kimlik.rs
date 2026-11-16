//! İsteği kimin yaptığı (İş Listesi L2, 16.11.2026).
//!
//! İş Listesi büro veritabanında kişiye göre çalışır (gizli iş, onay yetkisi, "benim işlerim"); bu yüzden
//! ofis programı her isteğin sahibini bilmeli. Üç yol:
//!   • Bu bilgisayar (yerel jeton / "Tarayıcıda aç" çerezi): programa girişli hesap (`ofis_hesabi`).
//!   • Tünel (WebRTC / WS-relay): sinyal sunucusu her mesaja `hesap` + `hesap_bilet` =
//!     HMAC(oda bilet anahtarı, "cid:exp:hesap") iğneler (vendor_server.py `_mint_account_ticket`). Ofis bunu
//!     doğrular, bağlantı kimliğini (cid) hesaba eşler.
//!   • Yerel ağ (LAN HTTPS kapısı): giriş sayfasında kişi kendi Çoklu UYAP hesabıyla girer (parola bulutta
//!     doğrulanır, burada saklanmaz); çerez kişiye özel ve bu oturumun gizli anahtarıyla imzalıdır.
//! Hesap adı biçimi buluttakiyle aynı: `ahmet@buro`.

use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use base64::engine::general_purpose::URL_SAFE_NO_PAD as B64;
use base64::Engine;
use hmac::{Hmac, Mac};
use once_cell::sync::Lazy;
use parking_lot::Mutex;
use serde_json::Value;
use sha2::Sha256;

type HmacSha256 = Hmac<Sha256>;

static OFIS_HESABI: Lazy<Mutex<String>> = Lazy::new(|| Mutex::new(String::new()));
static TUNEL: Lazy<Mutex<HashMap<String, String>>> = Lazy::new(|| Mutex::new(HashMap::new()));

const LAN_CEREZ_OMRU: i64 = 7 * 86400;

fn simdi() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

/// Kullanıcı adı + ofis kodu → `ahmet@buro` (adda @ varsa olduğu gibi).
pub fn hesap_adi(kullanici: &str, ofis: &str) -> String {
    let k = kullanici.trim();
    if k.contains('@') || ofis.trim().is_empty() {
        k.to_string()
    } else {
        format!("{k}@{}", ofis.trim())
    }
}

pub fn ofis_hesabi_ayarla(hesap: &str) {
    *OFIS_HESABI.lock() = hesap.trim().to_string();
}

/// Bu bilgisayarda programa girişli hesap (paylaşım başlamadıysa yok).
pub fn ofis_hesabi() -> Option<String> {
    let h = OFIS_HESABI.lock().clone();
    (!h.is_empty()).then_some(h)
}

pub fn tunel_ayarla(cid: &str, hesap: &str) {
    let mut t = TUNEL.lock();
    if t.len() > 2000 {
        t.clear(); // kopuk bağlantılardan birikme olmasın (bir sonraki mesajda yeniden eşlenir)
    }
    t.insert(cid.to_string(), hesap.to_string());
}
pub fn tunel_sil(cid: &str) {
    TUNEL.lock().remove(cid);
}
pub fn tunel_temizle() {
    TUNEL.lock().clear();
}
pub fn tunel_hesabi(cid: &str) -> Option<String> {
    TUNEL.lock().get(cid).cloned()
}

fn sabit_esit(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    a.iter().zip(b).fold(0u8, |d, (x, y)| d | (x ^ y)) == 0
}

fn imza(anahtar: &[u8], veri: &str) -> String {
    let mut mac = HmacSha256::new_from_slice(anahtar).expect("hmac anahtarı");
    mac.update(veri.as_bytes());
    hex::encode(mac.finalize().into_bytes())
}

/// Sinyal mesajındaki hesap biletini doğrular; geçerliyse hesap adını döner. Bilet anahtarı yoksa
/// (sunucu kimlik doğrulaması bildirmedi) hesap kabul edilmez.
pub fn hesap_bileti(anahtar: Option<&[u8]>, msg: &Value) -> Option<String> {
    let anahtar = anahtar?;
    let cid = msg.get("cid")?.as_str()?;
    let hesap = msg.get("hesap")?.as_str()?.trim();
    let bilet = msg.get("hesap_bilet")?.as_str()?;
    let exp = match msg.get("ticket_exp")? {
        Value::Number(n) => n.as_i64()?,
        Value::String(s) => s.parse().ok()?,
        _ => return None,
    };
    if cid.is_empty() || hesap.is_empty() || exp < simdi() {
        return None;
    }
    sabit_esit(bilet.as_bytes(), imza(anahtar, &format!("{cid}:{exp}:{hesap}")).as_bytes()).then(|| hesap.to_string())
}

/// Kişiye özel LAN çerezi: `h1.<hesap b64>.<bitiş>.<imza>`.
pub fn lan_cerezi(hesap: &str, gizli: &str) -> String {
    let exp = simdi() + LAN_CEREZ_OMRU;
    let h = B64.encode(hesap.as_bytes());
    format!("h1.{h}.{exp}.{}", imza(gizli.as_bytes(), &format!("lan:{h}:{exp}")))
}

pub fn lan_cerezi_coz(deger: &str, gizli: &str) -> Option<String> {
    let mut p = deger.split('.');
    if p.next()? != "h1" {
        return None;
    }
    let (h, exp, im) = (p.next()?, p.next()?, p.next()?);
    if p.next().is_some() {
        return None;
    }
    let exp_n: i64 = exp.parse().ok()?;
    if exp_n < simdi() || !sabit_esit(im.as_bytes(), imza(gizli.as_bytes(), &format!("lan:{h}:{exp}")).as_bytes()) {
        return None;
    }
    String::from_utf8(B64.decode(h).ok()?).ok().filter(|s| !s.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn hesap_bileti_dogrulama() {
        let k = b"oda-anahtari";
        let exp = simdi() + 60;
        let b = imza(k, &format!("c1:{exp}:ali@buro"));
        let m = json!({"cid": "c1", "ticket_exp": exp, "hesap": "ali@buro", "hesap_bilet": b});
        assert_eq!(hesap_bileti(Some(k), &m).as_deref(), Some("ali@buro"));
        assert_eq!(hesap_bileti(None, &m), None, "anahtarsız kabul yok");
        let mut sahte = m.clone();
        sahte["hesap"] = json!("yonetici@buro");
        assert_eq!(hesap_bileti(Some(k), &sahte), None, "hesap değiştirilemez");
        let mut baska = m.clone();
        baska["cid"] = json!("c2");
        assert_eq!(hesap_bileti(Some(k), &baska), None, "başka bağlantıya taşınamaz");
        let eski = simdi() - 1;
        let m2 = json!({"cid": "c1", "ticket_exp": eski, "hesap": "ali@buro", "hesap_bilet": imza(k, &format!("c1:{eski}:ali@buro"))});
        assert_eq!(hesap_bileti(Some(k), &m2), None, "süresi geçmiş");
    }

    #[test]
    fn lan_cerezi_gidis_donus() {
        let c = lan_cerezi("ayşe@büro", "gizli");
        assert_eq!(lan_cerezi_coz(&c, "gizli").as_deref(), Some("ayşe@büro"));
        assert_eq!(lan_cerezi_coz(&c, "baska"), None);
        let bozuk = c.replacen("h1.", "h1.YQ", 1);
        assert_eq!(lan_cerezi_coz(&bozuk, "gizli"), None);
        assert_eq!(lan_cerezi_coz("eski-ortak-cerez", "gizli"), None);
    }

    #[test]
    fn hesap_adi_birlesimi() {
        assert_eq!(hesap_adi("utku", "savas"), "utku@savas");
        assert_eq!(hesap_adi("utku@savas", "x"), "utku@savas");
    }
}
