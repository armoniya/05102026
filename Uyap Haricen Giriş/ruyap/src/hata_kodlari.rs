//! Masaüstü programın kullanıcıya gösterdiği hata kodları (2026-11-14).
//!
//! Ekranda günlük yerine tek satır durum gösterilir; hata olduğunda kullanıcı bu kodu destekle paylaşır,
//! ayrıntı şifreli günlükte durur. Kodlar sabittir — anlamını değiştirmeyin, yenisini ekleyin.
//!
//! | Aralık | Alan |
//! |--------|------|
//! | E-1xx  | E-imza (PIN, kart, imza istemcisi) |
//! | E-2xx  | UYAP (erişim, giriş) |
//! | E-3xx  | Çoklu UYAP sunucusu / ağ |
//! | E-4xx  | Bu bilgisayar (port, dosya) |
//! | E-9xx  | Beklenmeyen |

/// Bir günlük / hata satırını (kod, kısa açıklama) çiftine eşler.
pub fn cozumle(satir: &str) -> (&'static str, &'static str) {
    let s = satir.to_lowercase();
    let var = |k: &[&str]| k.iter().any(|x| s.contains(x));
    if var(&["pin yanlış", "pin hatalı", "ckr_pin_incorrect", "imzası alınamadı", "pin_incorrect"]) {
        ("E-101", "E-imza PIN'i hatalı olabilir")
    } else if var(&["ckr_pin_locked", "pin kilitli", "pin_locked"]) {
        ("E-102", "E-imza kartı kilitlendi")
    } else if var(&["kart takılı değil", "kart bulunamadı", "token bulunamadı", "slot bulunamadı", "token_not_present"]) {
        ("E-103", "E-imza kartı takılı değil")
    } else if var(&["5975", "imza istemcisi", "e-imza istemcisi", "arksigner", "imzager"]) {
        ("E-104", "E-imza istemcisi çalışmıyor")
    } else if var(&["uyap'a ulaşılamadı", "uyap erişilemedi", "avukat.uyap.gov.tr"]) {
        ("E-201", "UYAP'a ulaşılamadı")
    } else if var(&["uyap girişi başarısız", "ilk giriş başarısız", "giriş reddedildi"]) {
        ("E-202", "UYAP girişi yapılamadı")
    } else if var(&["signaling", "sinyal", "sunucuya bağlanılamadı", "websocket", "wss://"]) {
        ("E-301", "Çoklu UYAP sunucusuna bağlanılamadı")
    } else if var(&["kullanıcı adı veya parola", "oturum süresi", "yetkisiz", "401"]) {
        ("E-302", "Hesap doğrulanamadı")
    } else if var(&["dinleme hatası", "address already in use", "adres zaten kullanımda", "10048"]) {
        ("E-401", "Yerel bağlantı noktası (8800) kullanımda")
    } else if var(&["tabani]", "büro veritabanı", "postgresql", "pg_ctl", "initdb"]) {
        ("E-402", "Büro veritabanı başlatılamadı")
    } else if var(&["zaman aşımı", "timed out", "timeout"]) {
        ("E-303", "Bağlantı zaman aşımına uğradı")
    } else {
        ("E-900", "Beklenmeyen bir hata oluştu")
    }
}

#[cfg(test)]
mod tests {
    use super::cozumle;

    #[test]
    fn kodlar() {
        assert_eq!(cozumle("[!] UYAP girişi başarısız: CKR_PIN_INCORRECT").0, "E-101");
        assert_eq!(cozumle("[!] Dinleme hatası (0.0.0.0:8800): adres zaten kullanımda").0, "E-401");
        assert_eq!(cozumle("[HATA] Paylaşım durdu: signaling bağlantısı koptu").0, "E-301");
        assert_eq!(cozumle("[!] İlk giriş başarısız (UYAP'a ulaşılamadı)").0, "E-201");
        assert_eq!(cozumle("[VERİTABANI] Başlatılamadı: initdb başarısız").0, "E-402");
        assert_eq!(cozumle("tuhaf bir şey").0, "E-900");
    }
}
