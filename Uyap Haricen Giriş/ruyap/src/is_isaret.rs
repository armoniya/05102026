//! İçeriksiz bildirim sinyali (plan L6, 16.11.2026): Telegram ve telefon için.
//!
//! Ofis programı buluta YALNIZ "şu hesaba şu türde bildirim var" der:
//! `{"type": "isaret", "hesap": "katip@buro", "tur": "is" | "onay" | "hatirlatma"}` (office_agent.rs, mevcut
//! sinyal bağlantısı). İş başlığı, dosya numarası, kişi adı, not buluta GİTMEZ; bulut sabit bir metin iletir
//! ("Çoklu UYAP'ta yeni bir işiniz var." …) ve hesabın bu ofisin üyesi olduğunu kendisi denetler.
//!
//! Ne zaman: başkası size iş açtı / devretti; bir iş onaya düştü ya da erteleme istendi (onaylayacaklara);
//! hatırlatıcının zamanı geldi (`hatirlatici.gonderildi` işaretlenir, bir kez gider).
//! Birleştirme: aynı kişiye 10 dakikada en çok bir sinyal; arada biriken olaylar tek sinyal olur.
//! Sessiz saat: hafta sonu ve 20:00–08:30 arası iş/onay sinyali bekler (sabah tek sinyal); hatırlatıcı
//! kurulduğu saatte gider. Bulut bağlantısı yoksa en çok 30 dakika beklenir, sonra düşer (web rozeti doğru kalır).

use chrono::{Datelike, NaiveDateTime, Timelike, Weekday};
use postgres::GenericClient;
use serde_json::Value;
use std::collections::{BTreeSet, HashMap};
use std::sync::atomic::{AtomicBool, Ordering};

pub const BIRLESTIRME_SN: i64 = 600;
pub const KUYRUK_SN: i64 = 1800;

/// Sinyal türü. Sıra önceliktir: birleştirilen olaylardan en yükseği gönderilir.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Tur {
    Is,
    Onay,
    Hatirlatma,
}

impl Tur {
    pub fn kod(self) -> &'static str {
        match self {
            Tur::Is => "is",
            Tur::Onay => "onay",
            Tur::Hatirlatma => "hatirlatma",
        }
    }
}

/// Hafta sonu, 20:00 ve sonrası, 08:30 öncesi.
pub fn sessiz_mi(t: NaiveDateTime) -> bool {
    if matches!(t.weekday(), Weekday::Sat | Weekday::Sun) {
        return true;
    }
    let dk = t.hour() * 60 + t.minute();
    !(8 * 60 + 30..20 * 60).contains(&dk)
}

fn hata(e: postgres::Error) -> String {
    match e.as_db_error() {
        Some(d) => format!("veritabanı: {}", d.message()),
        None => format!("veritabanı: {e}"),
    }
}

pub fn son_olay<C: GenericClient>(c: &mut C) -> Result<i64, String> {
    Ok(c.query_one("SELECT coalesce(max(id), 0) FROM gorev_olay", &[]).map_err(hata)?.get(0))
}

/// Bir işi onaylayabilecek kişiler: gizli işte yalnız açan; değilse atanmış onaylayıcılar ve işi başkasına
/// açan kişi; kimse yoksa büronun yöneticileri.
fn onaylayanlar<C: GenericClient>(c: &mut C, gizli: bool, sorumlu: Option<i64>, olusturan: Option<i64>, gorev: i64) -> Result<BTreeSet<i64>, String> {
    let mut l = BTreeSet::new();
    if gizli {
        l.extend(olusturan);
        return Ok(l);
    }
    for r in c
        .query("SELECT kullanici_id FROM gorev_kisi WHERE gorev_id = $1 AND rol = 'onaylayici'", &[&gorev])
        .map_err(hata)?
    {
        l.insert(r.get::<_, i64>(0));
    }
    if let Some(o) = olusturan.filter(|o| Some(*o) != sorumlu) {
        l.insert(o);
    }
    if l.is_empty() {
        for r in c.query("SELECT id FROM kullanici WHERE rol = 'yonetici' AND aktif", &[]).map_err(hata)? {
            l.insert(r.get::<_, i64>(0));
        }
    }
    Ok(l)
}

/// `son`'dan sonraki iş olaylarının alıcıları (hesap, tür) ve işlenen son olay kimliği.
/// Olayı yapan kişiye sinyal gitmez.
pub fn olaylardan<C: GenericClient>(c: &mut C, son: i64) -> Result<(Vec<(String, Tur)>, i64), String> {
    let satirlar = c
        .query(
            "SELECT o.id, o.tur, o.kullanici_id, o.ayrinti, g.id, g.sorumlu_id, g.olusturan_id, g.gizli, g.durum
             FROM gorev_olay o JOIN gorev g ON g.id = o.gorev_id
             WHERE o.id > $1 AND o.tur IN ('acildi', 'sorumlu', 'durum', 'erteleme_istendi')
             ORDER BY o.id LIMIT 1000",
            &[&son],
        )
        .map_err(hata)?;
    let mut yeni_son = son;
    let mut hedef: Vec<(i64, Tur)> = Vec::new();
    for r in &satirlar {
        yeni_son = yeni_son.max(r.get::<_, i64>(0));
        let tur: String = r.get(1);
        let yapan: Option<i64> = r.get(2);
        let ayrinti: Value = r.get(3);
        let (gorev, sorumlu, olusturan, gizli, durum): (i64, Option<i64>, Option<i64>, bool, String) =
            (r.get(4), r.get(5), r.get(6), r.get(7), r.get(8));
        let mut alicilar: Vec<(i64, Tur)> = Vec::new();
        match tur.as_str() {
            // Başkasının açtığı / devrettiği iş (sistemin açtığı işlerde yapan yok → sinyal yok)
            "acildi" | "sorumlu" if yapan.is_some() && matches!(durum.as_str(), "acik" | "devam") => {
                alicilar.extend(sorumlu.map(|s| (s, Tur::Is)));
            }
            "durum" if ayrinti["yeni"].as_str() == Some("onayda") => {
                alicilar.extend(onaylayanlar(c, gizli, sorumlu, olusturan, gorev)?.into_iter().map(|k| (k, Tur::Onay)));
            }
            "erteleme_istendi" => {
                alicilar.extend(onaylayanlar(c, gizli, sorumlu, olusturan, gorev)?.into_iter().map(|k| (k, Tur::Onay)));
            }
            _ => {}
        }
        hedef.extend(alicilar.into_iter().filter(|(k, _)| Some(*k) != yapan));
    }
    if hedef.is_empty() {
        return Ok((vec![], yeni_son));
    }
    let idler: Vec<i64> = hedef.iter().map(|(k, _)| *k).collect();
    let hesaplar: HashMap<i64, String> = c
        .query("SELECT id, hesap_adi FROM kullanici WHERE id = ANY($1) AND aktif", &[&idler])
        .map_err(hata)?
        .iter()
        .map(|r| (r.get(0), r.get(1)))
        .collect();
    Ok((hedef.into_iter().filter_map(|(k, t)| hesaplar.get(&k).map(|h| (h.clone(), t))).collect(), yeni_son))
}

/// Zamanı gelmiş, henüz sinyali gitmemiş hatırlatıcılar (son 1 gün; kapanmış işlerinkiler hariç).
/// `gonderildi` burada işaretlenir: her hatırlatıcı için en çok bir sinyal.
pub fn hatirlaticilar<C: GenericClient>(c: &mut C) -> Result<Vec<String>, String> {
    Ok(c
        .query(
            "UPDATE hatirlatici h SET gonderildi = now()
             FROM kullanici k
             WHERE k.id = h.kullanici_id AND k.aktif AND h.gonderildi IS NULL AND h.goruldu IS NULL
               AND h.zaman <= now() AND h.zaman > now() - interval '1 day'
               AND (h.gorev_id IS NULL OR EXISTS (SELECT 1 FROM gorev g WHERE g.id = h.gorev_id AND g.durum IN ('acik', 'devam', 'onayda')))
             RETURNING k.hesap_adi",
            &[],
        )
        .map_err(hata)?
        .iter()
        .map(|r| r.get(0))
        .collect())
}

#[derive(Default, Debug)]
struct Bekleyen {
    turler: BTreeSet<Tur>,
    son_gonderim: Option<i64>,
    baglantisiz_bas: Option<i64>,
}

/// Kişi başına birleştirme, sessiz saat ve bağlantısız kuyruk kuralları (saf; zaman saniye olarak verilir).
#[derive(Default, Debug)]
pub struct Birlestirici {
    kisiler: HashMap<String, Bekleyen>,
}

impl Birlestirici {
    pub fn ekle(&mut self, hesap: &str, tur: Tur) {
        self.kisiler.entry(hesap.to_string()).or_default().turler.insert(tur);
    }

    /// Şimdi gönderilecek sinyaller. `sessiz`: iş/onay bekler; `bagli`: bulut bağlantısı var mı.
    pub fn tur(&mut self, simdi: i64, sessiz: bool, bagli: bool) -> Vec<(String, Tur)> {
        let mut cikti = Vec::new();
        for (hesap, b) in self.kisiler.iter_mut() {
            let uygun: Vec<Tur> = b.turler.iter().copied().filter(|t| !sessiz || *t == Tur::Hatirlatma).collect();
            if uygun.is_empty() {
                continue;
            }
            if b.son_gonderim.is_some_and(|s| simdi - s < BIRLESTIRME_SN) {
                continue;
            }
            if !bagli {
                let bas = *b.baslantisiz_bas_mut(simdi);
                if simdi - bas > KUYRUK_SN {
                    uygun.iter().for_each(|t| {
                        b.turler.remove(t);
                    });
                    b.baglantisiz_bas = None;
                }
                continue;
            }
            b.baglantisiz_bas = None;
            uygun.iter().for_each(|t| {
                b.turler.remove(t);
            });
            b.son_gonderim = Some(simdi);
            cikti.push((hesap.clone(), *uygun.iter().max().expect("boş değil")));
        }
        self.kisiler
            .retain(|_, b| !b.turler.is_empty() || b.son_gonderim.is_some_and(|s| simdi - s < BIRLESTIRME_SN));
        cikti.sort();
        cikti
    }
}

impl Bekleyen {
    fn baslantisiz_bas_mut(&mut self, simdi: i64) -> &mut i64 {
        self.baglantisiz_bas.get_or_insert(simdi)
    }
}

static BASLADI: AtomicBool = AtomicBool::new(false);

/// Arka plan döngüsünü bir kez başlatır (ofis paylaşımı başlayınca). Dakikada bir çalışır.
pub fn baslat() {
    if BASLADI.swap(true, Ordering::SeqCst) {
        return;
    }
    tokio::spawn(dongu());
}

async fn dongu() {
    let mut b = Birlestirici::default();
    let mut son: Option<i64> = None;
    let mut bekle = std::time::Duration::from_secs(20);
    loop {
        tokio::time::sleep(bekle).await;
        bekle = std::time::Duration::from_secs(60);
        if !crate::veritabani::hazir() {
            continue;
        }
        let onceki = son;
        let sonuc = tokio::task::spawn_blocking(move || -> Result<(Vec<(String, Tur)>, i64), String> {
            let mut c = crate::veritabani::baglanti()?;
            let (mut l, yeni_son) = match onceki {
                // İlk turda geçmiş olaylar sinyale çevrilmez (program açılınca eski işler için yağmur olmasın).
                None => (vec![], son_olay(&mut *c)?),
                Some(s) => olaylardan(&mut *c, s)?,
            };
            l.extend(hatirlaticilar(&mut *c)?.into_iter().map(|h| (h, Tur::Hatirlatma)));
            Ok((l, yeni_son))
        })
        .await;
        match sonuc {
            Ok(Ok((l, yeni_son))) => {
                son = Some(yeni_son);
                for (h, t) in l {
                    b.ekle(&h, t);
                }
            }
            Ok(Err(e)) => println!("[BİLDİRİM] Sinyal olayları okunamadı: {e}"),
            Err(e) => println!("[BİLDİRİM] Sinyal olayları okunamadı: {e}"),
        }
        let simdi = chrono::Local::now();
        for (hesap, tur) in b.tur(simdi.timestamp(), sessiz_mi(simdi.naive_local()), crate::office_agent::isaret_bagli()) {
            if !crate::office_agent::isaret_gonder(&hesap, tur.kod()) {
                println!("[BİLDİRİM] Sinyal gönderilemedi (bulut bağlantısı yok).");
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::NaiveDate;

    fn t(g: u32, s: u32, d: u32) -> NaiveDateTime {
        NaiveDate::from_ymd_opt(2026, 11, g).unwrap().and_hms_opt(s, d, 0).unwrap()
    }

    #[test]
    fn sessiz_saat() {
        // 16.11.2026 Pazartesi
        assert!(!sessiz_mi(t(16, 8, 30)));
        assert!(sessiz_mi(t(16, 8, 29)));
        assert!(!sessiz_mi(t(16, 19, 59)));
        assert!(sessiz_mi(t(16, 20, 0)));
        assert!(sessiz_mi(t(21, 11, 0)), "cumartesi");
        assert!(sessiz_mi(t(22, 11, 0)), "pazar");
    }

    #[test]
    fn birlestirme_ve_oncelik() {
        let mut b = Birlestirici::default();
        b.ekle("a", Tur::Is);
        b.ekle("a", Tur::Onay);
        b.ekle("b", Tur::Is);
        assert_eq!(b.tur(1000, false, true), vec![("a".into(), Tur::Onay), ("b".into(), Tur::Is)], "tek sinyal, yüksek tür");
        b.ekle("a", Tur::Is);
        assert!(b.tur(1300, false, true).is_empty(), "10 dk dolmadı");
        assert_eq!(b.tur(1600, false, true), vec![("a".into(), Tur::Is)], "10 dk sonra biriken gider");
        assert!(b.tur(5000, false, true).is_empty(), "birikmiş yok");
    }

    #[test]
    fn sessiz_saatte_yalniz_hatirlatma() {
        let mut b = Birlestirici::default();
        b.ekle("a", Tur::Is);
        b.ekle("b", Tur::Hatirlatma);
        assert_eq!(b.tur(1000, true, true), vec![("b".into(), Tur::Hatirlatma)]);
        // Gece boyunca bekleyen iş sinyali kuyruk süresiyle düşmez; sabah gider.
        assert!(b.tur(1000 + 10 * 3600, true, true).is_empty());
        assert_eq!(b.tur(1000 + 11 * 3600, false, true), vec![("a".into(), Tur::Is)]);
    }

    #[test]
    fn baglanti_yokken_30_dk_bekler() {
        let mut b = Birlestirici::default();
        b.ekle("a", Tur::Is);
        assert!(b.tur(1000, false, false).is_empty());
        assert_eq!(b.tur(1000 + 1200, false, true), vec![("a".into(), Tur::Is)], "bağlantı gelince gider");
        b.ekle("c", Tur::Onay);
        assert!(b.tur(5000, false, false).is_empty());
        assert!(b.tur(5000 + KUYRUK_SN + 60, false, false).is_empty(), "30 dk sonra düştü");
        assert!(b.tur(5000 + KUYRUK_SN + 120, false, true).is_empty(), "bağlantı gelse de gitmez");
    }
}

// ── Test (gömülü PostgreSQL uçtan uca: veritabani::tests::uctan_uca) ────────────────────────────

#[cfg(test)]
pub(crate) fn veritabani_senaryosu(c: &mut postgres::Client) {
    use crate::is_veri as iv;
    let bugun = chrono::Local::now().date_naive();
    iv::kullanici_esitle(c, "isr_mudur", "Müdür", true, true).unwrap();
    iv::kullanici_esitle(c, "isr_katip", "Katip", false, true).unwrap();
    let mudur = iv::kisi_bul(c, "isr_mudur").unwrap().unwrap();
    let katip = iv::kisi_bul(c, "isr_katip").unwrap().unwrap();
    let son = son_olay(c).unwrap();
    // Müdür katibe onaylı iş açar → katibe "is"
    let y = iv::YeniGorev::jsondan(&serde_json::json!({"baslik": "İşaret deneme", "son_tarih": bugun.to_string(),
                                                        "sorumlu_id": katip.id, "onay_gerekir": true})).unwrap();
    let id = iv::gorev_ac(c, Some(&mudur), &y, bugun).unwrap();
    let (l, son2) = olaylardan(c, son).unwrap();
    assert_eq!(l, vec![("isr_katip".to_string(), Tur::Is)]);
    // Katip kendine iş açar → sinyal yok
    let y2 = iv::YeniGorev::jsondan(&serde_json::json!({"baslik": "İşaret kendime", "son_tarih": bugun.to_string()})).unwrap();
    iv::gorev_ac(c, Some(&katip), &y2, bugun).unwrap();
    let (l, son3) = olaylardan(c, son2).unwrap();
    assert!(l.is_empty(), "{l:?}");
    // Katip tamamlar → onaya düşer → açan müdüre "onay"
    iv::durum_degistir(c, &katip, id, "tamamla", "", "", bugun).unwrap();
    let (l, son4) = olaylardan(c, son3).unwrap();
    assert_eq!(l, vec![("isr_mudur".to_string(), Tur::Onay)]);
    assert!(olaylardan(c, son4).unwrap().0.is_empty(), "aynı olay ikinci kez işlenmez");
    // Hatırlatıcı: zamanı gelince bir kez
    let simdi = chrono::Local::now().naive_local();
    let h = iv::hatirlatici_kur(c, &katip, Some(id), simdi + chrono::Duration::hours(1), "", simdi).unwrap();
    assert!(hatirlaticilar(c).unwrap().is_empty(), "zamanı gelmedi");
    c.execute("UPDATE hatirlatici SET zaman = now() - interval '1 minute' WHERE id = $1", &[&h]).unwrap();
    assert_eq!(hatirlaticilar(c).unwrap(), vec!["isr_katip".to_string()]);
    assert!(hatirlaticilar(c).unwrap().is_empty(), "ikinci kez gitmez");
    c.batch_execute("DELETE FROM gorev WHERE baslik IN ('İşaret deneme', 'İşaret kendime')").unwrap();
}
