//! İcra durum olayından şablonla otomatik iş (plan L7, 17.11.2026).
//!
//! `icra_durum::durumlari_guncelle` bir borçlunun tebliğ / kesinleşme durumunu değiştirdiğinde, aynı
//! veritabanı işleminde `sablon.olay_turu` eşleşen etkin şablonlardan iş açılır:
//!
//! | olay                       | hazır şablon                          | son gün                         |
//! |----------------------------|---------------------------------------|---------------------------------|
//! | `tebligat:teblig_edildi`   | Kesinleşme tarihi kontrol (h-kes-tarih)| hesaplanan kesinleşme tarihi    |
//! | `tebligat:iade`            | Mernis adresine tebliğ (h-mernis)     | şablonun süresi                 |
//! | `kesinlesme:kesinlesti`    | Kesinleştirme talebi gönder (h-kes-gonder) | şablonun süresi            |
//!
//! Kurallar:
//! - Yalnız GERÇEK geçişte: önceki durum boşsa (ilk hesap) iş açılmaz; yoksa ilk taramada yüzlerce iş açılırdı.
//! - Olay günü 30 günden eskiyse açılmaz (uzun aradan sonraki tarama geçmişi işe çevirmesin).
//! - Aynı dosya + borçlu + şablon için açık (açık / devam / onayda) iş varsa ikincisi açılmaz.
//! - Sorumlu: o dosyadaki en son işin sorumlusu; dosyada hiç iş yoksa boş (yönetici "Tüm işler"de görür).
//! - Hata icra taramasını durdurmaz: iş açılamazsa savepoint geri alınır, günlüğe yazılır.

use chrono::{Duration, NaiveDate};
use postgres::GenericClient;

use crate::is_veri::{self, YeniGorev};

/// Olay kodu → hazır şablon anahtarı (yeni kurulumda ve 0006 göçünde `sablon.olay_turu` buna göre dolar).
pub const HAZIR_OLAY_SABLONLARI: &[(&str, &str)] = &[
    ("tebligat:teblig_edildi", "h-kes-tarih"),
    ("tebligat:iade", "h-mernis"),
    ("kesinlesme:kesinlesti", "h-kes-gonder"),
];

/// Bu kadar günden eski olaydan iş açılmaz.
pub const EN_ESKI_OLAY_GUN: i64 = 30;

pub fn hazir_olay_turu(anahtar: &str) -> &'static str {
    HAZIR_OLAY_SABLONLARI.iter().find(|(_, a)| *a == anahtar).map(|(o, _)| *o).unwrap_or("")
}

/// Bir borçlunun durum geçişi.
pub struct Gecis<'a> {
    pub dosya_id: i64,
    pub taraf_id: i64,
    /// `durum_olayi` kaydı (iş ona bağlanır).
    pub durum_olayi_id: Option<i64>,
    /// `tebligat:teblig_edildi` vb.
    pub olay: &'a str,
    pub olay_tarihi: Option<NaiveDate>,
    /// Şablon süresi yerine kullanılacak son gün (ör. kesinleşme tarihi).
    pub son_gun: Option<NaiveDate>,
    /// Açıklamaya eklenecek olay metni ("Tebliğ Edildi (05.10.2026) — …").
    pub olay_metni: &'a str,
}

/// Önceki → yeni durum kodlarından olay kodu (yoksa None). Önceki boşsa (ilk hesap) olay yok.
pub fn tebligat_olayi(eski: &str, yeni: &str) -> Option<&'static str> {
    if eski.is_empty() || eski == yeni {
        return None;
    }
    match yeni {
        "teblig_edildi" => Some("tebligat:teblig_edildi"),
        "iade" => Some("tebligat:iade"),
        _ => None,
    }
}

pub fn kesinlesme_olayi(eski: &str, yeni: &str) -> Option<&'static str> {
    if eski.is_empty() || eski == yeni {
        return None;
    }
    (yeni == "kesinlesti").then_some("kesinlesme:kesinlesti")
}

/// Olay gününe göre iş açılmalı mı (gün yoksa açılır).
pub fn guncel_mi(olay_tarihi: Option<NaiveDate>, bugun: NaiveDate) -> bool {
    olay_tarihi.is_none_or(|t| t >= bugun - Duration::days(EN_ESKI_OLAY_GUN))
}

/// Geçişe uyan şablonlardan iş açar; açılan iş kimliklerini döner.
pub fn olaydan_is_ac<C: GenericClient>(c: &mut C, g: &Gecis, bugun: NaiveDate) -> Result<Vec<i64>, String> {
    let hata = |e: postgres::Error| format!("veritabanı: {e}");
    if !guncel_mi(g.olay_tarihi, bugun) {
        return Ok(Vec::new());
    }
    let sablonlar = c
        .query("SELECT id, baslik, aciklama FROM sablon WHERE olay_turu = $1 AND aktif ORDER BY id", &[&g.olay])
        .map_err(hata)?;
    if sablonlar.is_empty() {
        return Ok(Vec::new());
    }
    let taraf_adi = crate::icra_veri::taraf_adi(c, g.taraf_id)?;
    let sorumlu: Option<i64> = c
        .query_opt(
            "SELECT sorumlu_id FROM gorev WHERE dosya_id = $1 AND sorumlu_id IS NOT NULL ORDER BY olusturulma DESC LIMIT 1",
            &[&g.dosya_id],
        )
        .map_err(hata)?
        .and_then(|r| r.get(0));
    let mut acilan = Vec::new();
    for s in sablonlar {
        let sablon_id: i64 = s.get(0);
        let var = c
            .query_opt(
                "SELECT 1 FROM gorev WHERE dosya_id = $1 AND taraf_id = $2 AND sablon_id = $3
                    AND durum IN ('acik', 'devam', 'onayda') LIMIT 1",
                &[&g.dosya_id, &g.taraf_id, &sablon_id],
            )
            .map_err(hata)?
            .is_some();
        if var {
            continue;
        }
        let baslik: String = s.get(1);
        let aciklama: String = s.get(2);
        let ek = format!("Otomatik açıldı (UYAP durum değişikliği): {}", g.olay_metni);
        let y = YeniGorev {
            baslik: if taraf_adi.is_empty() { baslik.clone() } else { format!("{taraf_adi} – {baslik}") },
            aciklama: if aciklama.trim().is_empty() { ek } else { format!("{aciklama}\n\n{ek}") },
            sorumlu_id: sorumlu,
            son_tarih: g.son_gun.filter(|t| *t >= bugun),
            dosya_id: Some(g.dosya_id),
            taraf_id: Some(g.taraf_id),
            taraf_adi: taraf_adi.clone(),
            sablon_id: Some(sablon_id),
            durum_olayi_id: g.durum_olayi_id,
            oncelik: 1,
            ..Default::default()
        };
        acilan.push(is_veri::gorev_ac(c, None, &y, bugun)?);
    }
    Ok(acilan)
}

/// Gerçek veritabanı üzerinde senaryo; `veritabani::tests::uctan_uca` içinden (hazır şablonlar
/// yüklendikten ve icra senaryosu bir borçlu kaydettikten sonra) çağrılır.
#[cfg(test)]
pub(crate) fn veritabani_senaryosu(c: &mut postgres::Client) {
    let bugun = NaiveDate::from_ymd_opt(2026, 11, 17).unwrap();
    let say = |c: &mut postgres::Client, sql: &str| -> i64 { c.query_one(sql, &[]).unwrap().get(0) };
    assert_eq!(say(c, "SELECT count(*) FROM sablon WHERE olay_turu <> ''"), 3, "hazır şablonların olay bağı");
    assert_eq!(crate::is_veri::hazir_verileri_ilk_kez(c).unwrap(), 0, "hazır şablon varken açılışta yükleme yapılmaz");
    // Kendi dosyası ve borçlusu (önceki senaryolar kendi kayıtlarını silebilir).
    let kunye = serde_json::json!({"dosyaId": "x", "dosyaNo": "2026/777", "dosyaDurumKod": 0, "dosyaDurum": "Açık",
                                   "dosyaTurKod": 35, "birimAdi": "İzmir 9. İcra Dairesi", "birimId": 1909});
    let (dosya, _) = crate::icra_veri::dosya_kunyesi_kaydet(c, &kunye, Some(crate::icra_veri::YargiTuru::Icra.kod())).unwrap();
    let taraflar = vec![serde_json::json!({"adi": "VELİ KAYA", "rol": "Borçlu", "kisiKurum": "Kişi"})];
    crate::icra_kayit::dosya_taraf_kaydet(c, dosya, &taraflar, &|_: &str| {}).unwrap();
    let taraf: i64 = c.query_one("SELECT id FROM dosya_taraf WHERE dosya_id = $1", &[&dosya]).unwrap().get(0);
    let kes_gunu = NaiveDate::from_ymd_opt(2026, 11, 24).unwrap();
    let gecis = |olay, olay_tarihi, son_gun| Gecis {
        dosya_id: dosya,
        taraf_id: taraf,
        durum_olayi_id: None,
        olay,
        olay_tarihi,
        son_gun,
        olay_metni: "Tebliğ Edildi (16.11.2026)",
    };
    let g = gecis("tebligat:teblig_edildi", Some(bugun - Duration::days(1)), Some(kes_gunu));
    let acilan = olaydan_is_ac(c, &g, bugun).unwrap();
    assert_eq!(acilan.len(), 1);
    let s = c
        .query_one(
            "SELECT g.baslik, g.son_tarih, s.anahtar, g.aciklama, g.durum FROM gorev g JOIN sablon s ON s.id = g.sablon_id WHERE g.id = $1",
            &[&acilan[0]],
        )
        .unwrap();
    assert!(s.get::<_, String>(0).ends_with("– Kesinleşme tarihi kontrol"), "başlık: {}", s.get::<_, String>(0));
    assert_eq!(s.get::<_, NaiveDate>(1), kes_gunu, "son gün kesinleşme tarihi");
    assert_eq!(s.get::<_, String>(2), "h-kes-tarih");
    assert!(s.get::<_, String>(3).contains("Otomatik açıldı"));
    assert_eq!(s.get::<_, String>(4), "acik");
    assert!(olaydan_is_ac(c, &g, bugun).unwrap().is_empty(), "açık iş varken ikincisi açılmaz");
    // Eski olay iş açmaz; iade → Mernis şablonu (son gün şablon süresinden)
    assert!(olaydan_is_ac(c, &gecis("tebligat:iade", Some(bugun - Duration::days(40)), None), bugun).unwrap().is_empty());
    let m = olaydan_is_ac(c, &gecis("tebligat:iade", Some(bugun), None), bugun).unwrap();
    assert_eq!(m.len(), 1);
    let sg: NaiveDate = c.query_one("SELECT son_tarih FROM gorev WHERE id = $1", &[&m[0]]).unwrap().get(0);
    assert!(sg > bugun);
    // Pasif şablon iş açmaz
    c.execute("UPDATE sablon SET aktif = false WHERE anahtar = 'h-kes-gonder'", &[]).unwrap();
    assert!(olaydan_is_ac(c, &gecis("kesinlesme:kesinlesti", Some(bugun), None), bugun).unwrap().is_empty());
    c.execute("UPDATE sablon SET aktif = true WHERE anahtar = 'h-kes-gonder'", &[]).unwrap();
    // Temizlik: sonraki senaryoların sayımlarını bozmasın
    c.execute("DELETE FROM gorev WHERE id = ANY($1)", &[&[acilan[0], m[0]].to_vec()]).unwrap();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn olay_kodlari() {
        assert_eq!(tebligat_olayi("dagitimda", "teblig_edildi"), Some("tebligat:teblig_edildi"));
        assert_eq!(tebligat_olayi("dagitimda", "iade"), Some("tebligat:iade"));
        assert_eq!(tebligat_olayi("", "teblig_edildi"), None, "ilk hesap iş açmaz");
        assert_eq!(tebligat_olayi("iade", "iade"), None);
        assert_eq!(tebligat_olayi("iade", "dagitimda"), None);
        assert_eq!(kesinlesme_olayi("kesinlesme_bekliyor", "kesinlesti"), Some("kesinlesme:kesinlesti"));
        assert_eq!(kesinlesme_olayi("", "kesinlesti"), None);
        assert_eq!(kesinlesme_olayi("kesinlesti", "itiraz"), None);
        assert_eq!(hazir_olay_turu("h-mernis"), "tebligat:iade");
        assert_eq!(hazir_olay_turu("h-adres"), "");
    }

    #[test]
    fn eski_olay_is_acmaz() {
        let bugun = NaiveDate::from_ymd_opt(2026, 11, 17).unwrap();
        assert!(guncel_mi(None, bugun));
        assert!(guncel_mi(Some(bugun - Duration::days(30)), bugun));
        assert!(!guncel_mi(Some(bugun - Duration::days(31)), bugun));
    }
}
