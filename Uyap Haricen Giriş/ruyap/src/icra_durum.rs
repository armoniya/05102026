//! Borçlu başına kesinleşme, tebliğ durumu ve takip aşaması hesabı (Aşama 4).
//!
//! Yalnız büro veritabanındaki kayıtlardan hesaplar; UYAP'a ya da PTT'ye istek atmaz. Kurallar
//! örnek koddan (UyapMasa `kesinlesme_core` / `dosya_core`) taşınmıştır:
//!   • Kesinleşme: tebliğ günü sayılmaz; itiraz süresi (takip türüne göre `kesinlesme_kurali`)
//!     kadar gün sonra son gün gelir; son gün hafta sonu / tatile denk gelirse ilk iş gününe
//!     kayar; kesinleşme son günden sonraki ilk iş günüdür. Aradaki tatiller süreyi durdurmaz.
//!   • Kesinleşti için bir ödeme emrinde üçü birden gerekir: tebliğ gerçekleşmiş, mazbatası
//!     dosyaya dönmüş ve süre dolmuş. Ödeme emri gönderilmiş borçlu o güne dek Kesinleşme
//!     Bekliyor'dur. Elle itiraz ve dosyanın "Durdurulmuş : X" durumu bunlardan önce gelir.
//!   • E-tebligatta tebliğ tarihi evrak tarihidir; süre 5 gün sonra başlar.
//!   • Tebliğ durumu borçlu başına ve ödeme emrine göredir.
//!   • Takip aşaması bunlardan türetilir: Takip Talebi → Ödeme Emri → Tebligat Oluşturuluyor →
//!     PTT'ye Veriliyor → Tebliğe Çıktı → Tebliğ / İade / İtiraz / Durduruldu → Kesinleşti.
//! Her değişiklik durum geçmişine (`durum_olayi`) eski → yeni olarak yazılır; değişiklik yoksa
//! yazılmaz. İş Listesi'ne otomatik iş açma (Aşama 6) bu olaylardan doğacak.

use crate::icra_kayit as kayit;
use crate::icra_tebligat::{self as teb, tr_kucuk, E_TEBLIG_OKUMA_SURESI_GUN};
use crate::is_otomatik as otomatik;
use crate::icra_veri::{
    self as veri, durum_olayi_yaz, KesinlesmeDurumu as K, OlayKaynak, OlayTur, TakipAsamasi as A, TebligatDurumu as T, YeniOlay,
};
use chrono::{Datelike, Duration, NaiveDate, Weekday};
use postgres::GenericClient;
use serde_json::Value;
use std::collections::{BTreeSet, HashMap};

fn hata(e: postgres::Error) -> String {
    format!("veritabanı: {e}")
}

// ── Süre hesabı ─────────────────────────────────────────────────────────────────────────────────

pub type Tatiller = BTreeSet<NaiveDate>;

/// Hafta sonu ve resmî tatil dışındaki gün.
pub fn is_gunu_mu(tarih: NaiveDate, tatiller: &Tatiller) -> bool {
    !matches!(tarih.weekday(), Weekday::Sat | Weekday::Sun) && !tatiller.contains(&tarih)
}

/// Tarih iş günüyse kendisi, değilse sonraki ilk iş günü.
pub fn sonraki_is_gunu(mut tarih: NaiveDate, tatiller: &Tatiller) -> NaiveDate {
    while !is_gunu_mu(tarih, tatiller) {
        tarih += Duration::days(1);
    }
    tarih
}

/// Tarihten sonraki (kendisi hariç) ilk iş günü.
pub fn ilk_is_gunu_sonrasi(tarih: NaiveDate, tatiller: &Tatiller) -> NaiveDate {
    sonraki_is_gunu(tarih + Duration::days(1), tatiller)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct KesinlesmeTarihleri {
    /// İtiraz süresinin son günü (iş günü olacak şekilde kaydırılmış).
    pub itiraz_son_gunu: NaiveDate,
    /// Takibin kesinleştirilebileceği gün: son günden sonraki ilk iş günü.
    pub kesinlesebilir_tarihi: NaiveDate,
}

pub fn kesinlesme_hesapla(teblig_tarihi: NaiveDate, sure_gun: i64, tatiller: &Tatiller) -> KesinlesmeTarihleri {
    let itiraz_son_gunu = sonraki_is_gunu(teblig_tarihi + Duration::days(sure_gun), tatiller);
    KesinlesmeTarihleri { itiraz_son_gunu, kesinlesebilir_tarihi: ilk_is_gunu_sonrasi(itiraz_son_gunu, tatiller) }
}

// ── Ödeme emri tebligatı ────────────────────────────────────────────────────────────────────────

/// Borçluya bağlı bir ödeme emri tebligatı (`tebligat_barkod` satırının hesapta kullanılan alanları).
#[derive(Clone, Debug, Default)]
pub struct OdemeEmri {
    pub birim_evrak_no: i32,
    pub evrak_aciklama: String,
    pub evrak_tarihi: String,
    pub evrak_gunu: Option<NaiveDate>,
    pub barkod: String,
    pub elektronik_tebligat: bool,
    pub ptt_durumu: String,
    pub son_islem_tarihi: String,
    /// `son_islem_zamani` alanının günü.
    pub son_islem_gunu: Option<NaiveDate>,
    pub teslim_sekli: String,
    pub tebligat_mazbatasi_var: bool,
    pub tebligat_mazbatasi_aciklama: String,
}

/// UYAP mazbata dönünce evrak açıklamasının sonuna eklediği sonuç.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MazbataSonucu {
    Yok,
    Edildi,
    Edilemedi,
}

pub fn mazbata_sonucu(aciklama: &str) -> MazbataSonucu {
    let son = tr_kucuk(aciklama.rsplit(']').next().unwrap_or(""));
    if son.contains("teblig edilemedi") {
        MazbataSonucu::Edilemedi
    } else if son.contains("teblig edildi") {
        MazbataSonucu::Edildi
    } else {
        MazbataSonucu::Yok
    }
}

impl OdemeEmri {
    /// Son işlem (tebliğ / iade) günü: tarih alanından, yoksa ham metinden.
    pub fn son_islem(&self) -> Option<NaiveDate> {
        self.son_islem_gunu.or_else(|| teb::teblig_tarihini_coz(&self.son_islem_tarihi))
    }

    pub fn evrak(&self) -> Option<NaiveDate> {
        self.evrak_gunu.or_else(|| teb::teblig_tarihini_coz(&self.evrak_tarihi))
    }

    /// Sürenin sayıldığı tebliğ günü: fizikide tebliğ tarihi, e-tebligatta tebliğ tarihi + 5 gün.
    pub fn sure_baslangici(&self) -> Option<NaiveDate> {
        let t = self.son_islem()?;
        Some(if self.elektronik_tebligat { t + Duration::days(E_TEBLIG_OKUMA_SURESI_GUN) } else { t })
    }

    /// Gerçekten teslim edilmiş / okunmuş sayılmış mı? Dağıtımda ya da iade gibi durumlarda süre
    /// başlamaz. Fizikide PTT durumunda "teslim edildi" aranır.
    pub fn gerceklesti_mi(&self, bugun: NaiveDate) -> bool {
        if self.elektronik_tebligat {
            return self.sure_baslangici().is_some_and(|t| t <= bugun);
        }
        tr_kucuk(&self.ptt_durumu).contains("teslim edildi")
    }

    /// Bu tebligatın kendi mazbatası dosyaya dönmüş mü? E-tebligatta ayrı dönüş beklenmez.
    /// Fizikide yalnız bu barkoda ait kanıt sayılır; barkodsuz "Var" işareti sayılmaz.
    pub fn mazbatasi_dondu_mu(&self) -> bool {
        self.elektronik_tebligat
            || mazbata_sonucu(&self.evrak_aciklama) == MazbataSonucu::Edildi
            || teb::kendi_mazbatasi_dondu_mu(&self.barkod, self.tebligat_mazbatasi_var, &self.tebligat_mazbatasi_aciklama)
    }

    fn teslim(&self) -> String {
        if self.elektronik_tebligat { "E-tebligat".to_string() } else { self.teslim_sekli.clone() }
    }
}

// ── Borçlu başına hesaplar ──────────────────────────────────────────────────────────────────────

/// Dosya durum metninden kesinleşme kodu: "Açık (Durdurulmuş : X)" → X'in kodu; bilinmeyen X →
/// "durd_tanimsiz"; durdurulmamışsa `None`.
pub fn durdurma_kesinlesme_kodu(durum: &str) -> Option<&'static str> {
    let d = tr_kucuk(durum).split_whitespace().collect::<Vec<_>>().join(" ");
    let (_, sonrasi) = d.split_once("durdurulmus")?;
    let neden = sonrasi.trim_start().strip_prefix(':')?.trim().trim_end_matches(')').trim();
    Some(match neden {
        "takibe itiraz" => K::Itiraz.kod(),
        "icra mahkemesine itiraz" => K::DurdurulmusIcraMahItiraz.kod(),
        "genel mahkemeye dava acilmasi" => K::DurdurulmusGenelMahDava.kod(),
        "tehir-i icra karari" => K::DurdurulmusTehiriIcra.kod(),
        "ihtiyati tedbir" => K::DurdurulmusIhtiyatiTedbir.kod(),
        "iflas" => K::DurdurulmusIflas.kod(),
        "borclunun olumu" => K::DurdurulmusBorclununOlumu.kod(),
        "diger" => K::DurdurulmusDiger.kod(),
        _ => K::DurdurulmusTanimsiz.kod(),
    })
}

/// Borçlunun kesinleşme durumu ve günü. Borçluya bağlı ödeme emri yoksa ya da takip türü için
/// süre tanımlı değilse boş döner.
pub fn borclu_kesinlesme_hesapla(
    itiraz_edildi_mi: bool,
    odeme_emirleri: &[OdemeEmri],
    tatiller: &Tatiller,
    bugun: NaiveDate,
    durdurma: Option<&str>,
    sure_gun: Option<i16>,
) -> (String, Option<NaiveDate>) {
    if itiraz_edildi_mi {
        return (K::Itiraz.kod().into(), None);
    }
    if let Some(d) = durdurma {
        return (d.into(), None);
    }
    let Some(sure) = sure_gun.filter(|_| !odeme_emirleri.is_empty()) else { return (String::new(), None) };
    let (mut mazbatali, mut teblig): (Option<NaiveDate>, Option<NaiveDate>) = (None, None);
    for k in odeme_emirleri {
        if !k.gerceklesti_mi(bugun) || mazbata_sonucu(&k.evrak_aciklama) == MazbataSonucu::Edilemedi {
            continue;
        }
        let Some(t) = k.sure_baslangici() else { continue };
        teblig = teblig.max(Some(t));
        if k.mazbatasi_dondu_mu() {
            mazbatali = mazbatali.max(Some(t));
        }
    }
    if let Some(t) = mazbatali {
        let gun = kesinlesme_hesapla(t, i64::from(sure), tatiller).kesinlesebilir_tarihi;
        if bugun >= gun {
            return (K::Kesinlesti.kod().into(), Some(gun));
        }
    }
    (K::KesinlesmeBekliyor.kod().into(), teblig.map(|t| kesinlesme_hesapla(t, i64::from(sure), tatiller).kesinlesebilir_tarihi))
}

/// Borçlunun ödeme emri tebligatlarından (tebliğ durumu, günü, teslim şekli / iade nedeni):
///   • biri tebliğ edilmişse Tebliğ Edildi, en yeni tebliğ günü;
///   • yoksa mazbatası "Tebliğ Edildi" dönmüş olan varsa Tebliğ Edildi (gün yok);
///   • yoksa evrak tarihi en yeni olan ve bilgi taşıyan tebligata göre: PTT iade ya da mazbata
///     "Tebliğ Edilemedi" → İade; başka bir PTT hareketi ya da günü gelmemiş e-tebligat → Dağıtımda;
///   • hiç bilgi yoksa boş.
pub fn borclu_tebligat_hesapla(odeme_emirleri: &[OdemeEmri], bugun: NaiveDate) -> (String, Option<NaiveDate>, String) {
    let mut en_yeni: Option<(NaiveDate, &OdemeEmri)> = None;
    for k in odeme_emirleri {
        if !k.gerceklesti_mi(bugun) {
            continue;
        }
        if let Some(t) = k.son_islem() {
            if en_yeni.map_or(true, |(e, _)| t > e) {
                en_yeni = Some((t, k));
            }
        }
    }
    if let Some((t, k)) = en_yeni {
        return (T::TebligEdildi.kod().into(), Some(t), k.teslim());
    }
    if odeme_emirleri.iter().any(|k| mazbata_sonucu(&k.evrak_aciklama) == MazbataSonucu::Edildi) {
        return (T::TebligEdildi.kod().into(), None, String::new());
    }
    let mut sirali: Vec<&OdemeEmri> = odeme_emirleri.iter().collect();
    sirali.sort_by_key(|k| std::cmp::Reverse(k.evrak()));
    for k in sirali {
        let ptt = tr_kucuk(&k.ptt_durumu);
        if ptt.contains("iade") || mazbata_sonucu(&k.evrak_aciklama) == MazbataSonucu::Edilemedi {
            return (T::Iade.kod().into(), k.son_islem(), k.teslim());
        }
        if k.elektronik_tebligat || (!ptt.is_empty() && !ptt.contains("kayit yok")) {
            return (T::Dagitimda.kod().into(), None, String::new());
        }
    }
    (String::new(), None, String::new())
}

/// Borçlunun bu turda hesaplanmış durumu (takip aşamasının girdisi).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct BorcluDurumu {
    pub kesinlesme_durumu: String,
    pub kesinlesme_tarihi: Option<NaiveDate>,
    pub tebligat_durumu: String,
    pub tebligat_durum_tarihi: Option<NaiveDate>,
    pub tebligat_sekli: String,
    pub itiraz_tarihi: Option<NaiveDate>,
}

/// Borçlunun takip aşaması ve günü. Öncelik: Kesinleşti / İtiraz / Durdurulmuş (kesinleşme
/// durumundan) → Tebliğ / İade (tebliğ durumundan) → en yeni ödeme emri tebligatının hâli:
/// e-tebligat ya da kabul ötesi hareket → Tebliğe Çıktı; barkod var, PTT'de kayıt yok ya da yalnız
/// kabul → PTT'ye Veriliyor; barkod yok → Tebligat Oluşturuluyor → tebligat yok ama dosyada ödeme
/// emri evrakı var (`odeme_emri_evraki`: evrak günü) → Ödeme Emri → o da yok → Takip Talebi.
pub fn takip_asamasi_hesapla(
    b: &BorcluDurumu,
    odeme_emirleri: &[OdemeEmri],
    odeme_emri_evraki: Option<Option<NaiveDate>>,
) -> (A, Option<NaiveDate>) {
    let kes = b.kesinlesme_durumu.as_str();
    if kes == K::Kesinlesti.kod() {
        return (A::Kesinlesti, b.kesinlesme_tarihi);
    }
    if kes == K::Itiraz.kod() {
        return (A::Itiraz, b.itiraz_tarihi);
    }
    if kes.starts_with("durd_") {
        return (A::Durduruldu, None);
    }
    if b.tebligat_durumu == T::TebligEdildi.kod() {
        return (A::Teblig, b.tebligat_durum_tarihi);
    }
    if b.tebligat_durumu == T::Iade.kod() {
        return (A::Iade, b.tebligat_durum_tarihi);
    }
    let Some(k) = odeme_emirleri.iter().max_by_key(|k| (k.evrak(), k.birim_evrak_no)) else {
        return match odeme_emri_evraki {
            Some(gun) => (A::OdemeEmri, gun),
            None => (A::TakipTalebi, None),
        };
    };
    if k.elektronik_tebligat {
        return (A::TebligeCikti, k.evrak());
    }
    if k.barkod.trim().is_empty() {
        return (A::TebligatOlusturuluyor, k.evrak());
    }
    let ptt = tr_kucuk(&k.ptt_durumu);
    if ptt.is_empty() {
        return (A::PttVeriliyor, k.evrak());
    }
    if ptt.contains("kayit yok") || ptt.contains("kabul") {
        return (A::PttVeriliyor, k.son_islem());
    }
    (A::TebligeCikti, k.son_islem())
}

// ── Görünen metinler ────────────────────────────────────────────────────────────────────────────

fn tarihli(etiket: String, tarih: Option<NaiveDate>) -> String {
    match tarih {
        Some(t) => format!("{etiket} ({})", t.format("%d.%m.%Y")),
        None => etiket,
    }
}

/// "Kesinleşme Bekliyor (14.10.2026)"
pub fn kesinlesme_metni(durum: &str, tarih: Option<NaiveDate>) -> String {
    if durum.is_empty() { String::new() } else { tarihli(K::etiket_bul(durum), tarih) }
}

/// "Tebliğ Edildi (12.09.2026) — Muhtara teslim (21. md.)"
pub fn tebligat_metni(durum: &str, tarih: Option<NaiveDate>, sekli: &str) -> String {
    if durum.is_empty() {
        return String::new();
    }
    let m = tarihli(T::etiket_bul(durum), tarih);
    if sekli.is_empty() { m } else { format!("{m} — {sekli}") }
}

/// "Tebliğ (05.10.2026)"
pub fn takip_asamasi_metni(asama: &str, tarih: Option<NaiveDate>) -> String {
    if asama.is_empty() { String::new() } else { tarihli(A::etiket_bul(asama), tarih) }
}

// ── Veritabanı ──────────────────────────────────────────────────────────────────────────────────

/// Tebligatların borçlusunu muhatap kuralına göre yeniden hesaplar ve farklıysa düzeltir; yanlış
/// bağlanmış kayıt (ör. tek borçlulu dosyada bankaya giden tebligat) da çözülür. Döner: değişen
/// kayıt sayısı.
pub fn tebligat_borclularini_duzelt<C: GenericClient>(c: &mut C, dosya: Option<i64>) -> Result<usize, String> {
    let borclular = kayit::borclu_haritasi(c, dosya)?;
    let satirlar = c
        .query(
            "SELECT id, dosya_id, evrak_aciklama, ham_yanit, borclu_id FROM tebligat_barkod
              WHERE $1::bigint IS NULL OR dosya_id = $1 ORDER BY id",
            &[&dosya],
        )
        .map_err(hata)?;
    let bos = Vec::new();
    let mut degisen = 0;
    for s in satirlar {
        let (id, dosya_id): (i64, i64) = (s.get(0), s.get(1));
        let aciklama: String = s.get(2);
        let ham: Option<Value> = s.get(3);
        let eski: Option<i64> = s.get(4);
        let yeni = teb::tebligat_borclusunu_bul(&aciklama, borclular.get(&dosya_id).unwrap_or(&bos), ham.as_ref());
        if yeni != eski {
            c.execute("UPDATE tebligat_barkod SET borclu_id = $2 WHERE id = $1", &[&id, &yeni]).map_err(hata)?;
            degisen += 1;
        }
    }
    Ok(degisen)
}

/// Dosyalardaki ödeme emri evrakının günü (evrak listesinden): dosya → en yeni gün. Evrak türü
/// adının UYAP'taki tam yazılışı canlı doğrulanmadı; "Ödeme Emri" / "Ödeme İcra Emri" kabul edilir.
fn odeme_emri_evraklari<C: GenericClient>(c: &mut C, dosya: Option<i64>) -> Result<HashMap<i64, Option<NaiveDate>>, String> {
    Ok(c.query(
        "SELECT dosya_id, max(evrak_tarihi)::date FROM evrak
          WHERE ($1::bigint IS NULL OR dosya_id = $1) AND evrak_turu ~ '^\\s*[öÖ]deme\\s+([iİ]cra\\s+)?[eE]mr[iİ]\\s*$'
          GROUP BY dosya_id",
        &[&dosya],
    )
    .map_err(hata)?
    .iter()
    .map(|s| (s.get(0), s.get(1)))
    .collect())
}

/// Hesap sonucu.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Sonuc {
    /// Değeri değişen borçlu satırı sayısı.
    pub guncellenen: usize,
    /// Durum geçmişine yazılan olay sayısı.
    pub olay: usize,
    /// Durum geçişinden şablonla açılan iş sayısı (L7).
    pub otomatik_is: usize,
}

/// Borçluların kesinleşme, tebliğ durumu ve takip aşamasını kayıtlı tebligatlardan hesaplayıp
/// yazar. `dosya` verilirse yalnız o dosyanın borçluları; verilmezse tebligatı, durdurması,
/// itirazı olan ya da takip aşaması henüz yazılmamış bütün icra borçluları. Elle girilen
/// Konkordato / Vefat / Vasi değerleri ezilmez. `bugun` sürenin dolup dolmadığını belirler.
pub fn durumlari_guncelle<C: GenericClient>(c: &mut C, dosya: Option<i64>, bugun: NaiveDate, log: kayit::Gunluk) -> Result<Sonuc, String> {
    let mut tx = c.transaction().map_err(hata)?;
    let duzeltilen = tebligat_borclularini_duzelt(&mut tx, dosya)?;
    if duzeltilen > 0 {
        log(&format!("… tebligat → borçlu eşlemesi güncellendi: {duzeltilen} tebligat kaydı."));
    }
    let tatiller = veri::resmi_tatiller(&mut tx)?;
    let kurallar = veri::kesinlesme_kurallari(&mut tx)?;
    let evraklar = odeme_emri_evraklari(&mut tx, dosya)?;

    let mut odeme_emirleri: HashMap<i64, Vec<OdemeEmri>> = HashMap::new();
    for s in tx
        .query(
            "SELECT borclu_id, birim_evrak_no, evrak_aciklama, evrak_tarihi, evrak_gunu, barkod, elektronik_tebligat,
                    ptt_durumu, son_islem_tarihi, son_islem_zamani::date, teslim_sekli, tebligat_mazbatasi_var,
                    tebligat_mazbatasi_aciklama
               FROM tebligat_barkod
              WHERE tebligat_turu = 'Ödeme Emri' AND borclu_id IS NOT NULL AND ($1::bigint IS NULL OR dosya_id = $1)
              ORDER BY id",
            &[&dosya],
        )
        .map_err(hata)?
    {
        odeme_emirleri.entry(s.get(0)).or_default().push(OdemeEmri {
            birim_evrak_no: s.get(1),
            evrak_aciklama: s.get(2),
            evrak_tarihi: s.get(3),
            evrak_gunu: s.get(4),
            barkod: s.get(5),
            elektronik_tebligat: s.get(6),
            ptt_durumu: s.get(7),
            son_islem_tarihi: s.get(8),
            son_islem_gunu: s.get(9),
            teslim_sekli: s.get(10),
            tebligat_mazbatasi_var: s.get(11),
            tebligat_mazbatasi_aciklama: s.get(12),
        });
    }

    let borclular = tx
        .query(
            "SELECT dt.id, dt.dosya_id, dt.itiraz_edildi_mi, dt.itiraz_tarihi, dt.kesinlesme_durumu, dt.kesinlesme_tarihi,
                    dt.tebligat_durumu, dt.tebligat_durum_tarihi, dt.tebligat_sekli, dt.takip_asamasi,
                    dt.takip_asamasi_tarihi, d.durum, d.yargi_turu,
                    COALESCE(i.takibin_turu, ''), COALESCE(i.takibin_yolu, ''), COALESCE(i.takibin_sekli, '')
               FROM dosya_taraf dt JOIN dosya d ON d.id = dt.dosya_id
               LEFT JOIN icra_takip_detay i ON i.dosya_id = d.id
              WHERE dt.rol = 'borclu'
                AND (dt.dosya_id = $1
                     OR ($1::bigint IS NULL
                         AND (EXISTS (SELECT 1 FROM tebligat_barkod tb WHERE tb.dosya_id = d.id)
                              OR d.durum LIKE '%Durdurulmuş%'
                              OR dt.kesinlesme_durumu = 'itiraz' OR dt.kesinlesme_durumu LIKE 'durd\\_%'
                              OR (d.yargi_turu = 2 AND dt.takip_asamasi = ''))))
              ORDER BY dt.id",
            &[&dosya],
        )
        .map_err(hata)?;

    let icra = veri::YargiTuru::Icra.kod();
    let elle = [K::Konkordato.kod(), K::Vefat.kod(), K::Vasi.kod()];
    let bos: Vec<OdemeEmri> = Vec::new();
    let (mut kuralsiz, mut tanimsiz) = (BTreeSet::new(), BTreeSet::new());
    let mut sonuc = Sonuc::default();
    for s in borclular {
        let (id, dosya_id): (i64, i64) = (s.get(0), s.get(1));
        let itiraz: bool = s.get(2);
        let eski = BorcluDurumu {
            itiraz_tarihi: s.get(3),
            kesinlesme_durumu: s.get(4),
            kesinlesme_tarihi: s.get(5),
            tebligat_durumu: s.get(6),
            tebligat_durum_tarihi: s.get(7),
            tebligat_sekli: s.get(8),
        };
        let (eski_asama, eski_asama_tarihi): (String, Option<NaiveDate>) = (s.get(9), s.get(10));
        let durum: String = s.get(11);
        let icra_mi = s.get::<_, i16>(12) == icra;
        let (turu, yolu, sekli): (String, String, String) = (s.get(13), s.get(14), s.get(15));
        let oe = odeme_emirleri.get(&id).unwrap_or(&bos);

        let durdurma = if icra_mi { durdurma_kesinlesme_kodu(&durum) } else { None };
        if durdurma == Some(K::DurdurulmusTanimsiz.kod()) {
            tanimsiz.insert(durum.clone());
        }
        let kural = veri::kesinlesme_kurali_sec(&kurallar, &turu, &yolu, &sekli);
        if kural.is_none() {
            let ya = |x: &str| if x.is_empty() { "?".to_string() } else { x.to_string() };
            kuralsiz.insert(format!("{}/{}/{}", ya(&turu), ya(&yolu), ya(&sekli)));
        }
        let mut yeni = eski.clone();
        (yeni.kesinlesme_durumu, yeni.kesinlesme_tarihi) =
            borclu_kesinlesme_hesapla(itiraz, oe, &tatiller, bugun, durdurma, kural.and_then(|k| k.itiraz_suresi_gun));
        if elle.contains(&eski.kesinlesme_durumu.as_str()) && !itiraz {
            (yeni.kesinlesme_durumu, yeni.kesinlesme_tarihi) = (eski.kesinlesme_durumu.clone(), eski.kesinlesme_tarihi);
        }
        (yeni.tebligat_durumu, yeni.tebligat_durum_tarihi, yeni.tebligat_sekli) = borclu_tebligat_hesapla(oe, bugun);
        let (asama, asama_tarihi) = if icra_mi {
            let (a, t) = takip_asamasi_hesapla(&yeni, oe, evraklar.get(&dosya_id).copied());
            (a.kod().to_string(), t)
        } else {
            (String::new(), None)
        };

        // Olaylar: (tür, eski metin, yeni metin, olay günü)
        let mut olaylar: Vec<(OlayTur, String, String, Option<NaiveDate>)> = Vec::new();
        if (&yeni.kesinlesme_durumu, yeni.kesinlesme_tarihi) != (&eski.kesinlesme_durumu, eski.kesinlesme_tarihi) {
            olaylar.push((
                OlayTur::Kesinlesme,
                kesinlesme_metni(&eski.kesinlesme_durumu, eski.kesinlesme_tarihi),
                kesinlesme_metni(&yeni.kesinlesme_durumu, yeni.kesinlesme_tarihi),
                yeni.kesinlesme_tarihi,
            ));
        }
        if (&yeni.tebligat_durumu, yeni.tebligat_durum_tarihi, &yeni.tebligat_sekli)
            != (&eski.tebligat_durumu, eski.tebligat_durum_tarihi, &eski.tebligat_sekli)
        {
            olaylar.push((
                OlayTur::Tebligat,
                tebligat_metni(&eski.tebligat_durumu, eski.tebligat_durum_tarihi, &eski.tebligat_sekli),
                tebligat_metni(&yeni.tebligat_durumu, yeni.tebligat_durum_tarihi, &yeni.tebligat_sekli),
                yeni.tebligat_durum_tarihi,
            ));
        }
        let asama_degisti = (&asama, asama_tarihi) != (&eski_asama, eski_asama_tarihi);
        if asama_degisti && !eski_asama.is_empty() && !asama.is_empty() && asama != eski_asama {
            // Geçmişe yalnız aşama değişince yazılır (gün değişimi değil; ilk hesap da yazılmaz).
            // Tebliğ / iade / kesinleşme / itiraz / durdurma bu turdaki kesinleşme ya da tebliğ
            // olayıyla zaten görünüyorsa tekrar edilmez.
            let sonuc_asamasi = [A::Teblig, A::Iade, A::Kesinlesti, A::Itiraz, A::Durduruldu].iter().any(|a| a.kod() == asama);
            if !(sonuc_asamasi && !olaylar.is_empty()) {
                olaylar.push((
                    OlayTur::TakipAsamasi,
                    takip_asamasi_metni(&eski_asama, eski_asama_tarihi),
                    takip_asamasi_metni(&asama, asama_tarihi),
                    asama_tarihi,
                ));
            }
        }
        if yeni == eski && !asama_degisti {
            continue;
        }
        tx.execute(
            "UPDATE dosya_taraf SET kesinlesme_durumu = $2, kesinlesme_tarihi = $3, tebligat_durumu = $4,
                    tebligat_durum_tarihi = $5, tebligat_sekli = $6, takip_asamasi = $7, takip_asamasi_tarihi = $8
              WHERE id = $1",
            &[
                &id,
                &yeni.kesinlesme_durumu,
                &yeni.kesinlesme_tarihi,
                &yeni.tebligat_durumu,
                &yeni.tebligat_durum_tarihi,
                &veri::kirp(&yeni.tebligat_sekli, 150),
                &asama,
                &asama_tarihi,
            ],
        )
        .map_err(hata)?;
        sonuc.guncellenen += 1;
        let mut olay_kimlikleri: Vec<(OlayTur, i64)> = Vec::new();
        for (tur, eski_metin, yeni_metin, tarih) in &olaylar {
            let oid = durum_olayi_yaz(
                &mut tx,
                &YeniOlay {
                    dosya: dosya_id,
                    dosya_taraf: Some(id),
                    tur: *tur,
                    eski: eski_metin,
                    yeni: yeni_metin,
                    olay_tarihi: *tarih,
                    kaynak: OlayKaynak::Hesap,
                    aciklama: "",
                },
            )?;
            olay_kimlikleri.push((*tur, oid));
            sonuc.olay += 1;
        }
        // L7: tebliğ / iade / kesinleşme geçişinde şablondan otomatik iş (bkz. is_otomatik).
        let olay_bilgisi = |t: OlayTur| {
            let kimlik = olay_kimlikleri.iter().find(|(x, _)| *x == t).map(|(_, i)| *i);
            let metin = olaylar.iter().find(|o| o.0 == t).map(|o| o.2.clone()).unwrap_or_default();
            (kimlik, metin)
        };
        let mut gecisler: Vec<(&str, OlayTur, Option<NaiveDate>, Option<NaiveDate>)> = Vec::new();
        if let Some(o) = otomatik::tebligat_olayi(&eski.tebligat_durumu, &yeni.tebligat_durumu) {
            let son_gun = if o == "tebligat:teblig_edildi" { yeni.kesinlesme_tarihi } else { None };
            gecisler.push((o, OlayTur::Tebligat, yeni.tebligat_durum_tarihi, son_gun));
        }
        if let Some(o) = otomatik::kesinlesme_olayi(&eski.kesinlesme_durumu, &yeni.kesinlesme_durumu) {
            gecisler.push((o, OlayTur::Kesinlesme, yeni.kesinlesme_tarihi, None));
        }
        for (olay, tur, olay_tarihi, son_gun) in gecisler {
            let (kimlik, metin) = olay_bilgisi(tur);
            let g = otomatik::Gecis {
                dosya_id,
                taraf_id: id,
                durum_olayi_id: kimlik,
                olay,
                olay_tarihi,
                son_gun,
                olay_metni: &metin,
            };
            // Ayrı savepoint: iş açılamazsa icra hesabı yine kaydedilir.
            let mut sp = tx.transaction().map_err(hata)?;
            match otomatik::olaydan_is_ac(&mut sp, &g, bugun) {
                Ok(acilan) => {
                    sp.commit().map_err(hata)?;
                    sonuc.otomatik_is += acilan.len();
                }
                Err(e) => {
                    drop(sp);
                    log(&format!("⚠️ Otomatik iş açılamadı ({olay}, borçlu {id}): {e}"));
                }
            }
        }
    }
    tx.commit().map_err(hata)?;
    if !kuralsiz.is_empty() {
        log(&format!(
            "⚠️ Kesinleşme süresi tanımlı olmayan takip türü (kesinleşme hesaplanmadı): {}",
            kuralsiz.into_iter().collect::<Vec<_>>().join(", ")
        ));
    }
    if !tanimsiz.is_empty() {
        log(&format!(
            "⚠️ Tanınmayan durdurma nedeni (\"Durdurulmuş: Tanımsız Neden\" yazıldı): {}",
            tanimsiz.into_iter().collect::<Vec<_>>().join("; ")
        ));
    }
    if sonuc.guncellenen > 0 {
        log(&format!("… kesinleşme / tebliğ / takip aşaması güncellendi: {} borçlu.", sonuc.guncellenen));
    }
    Ok(sonuc)
}

// ── Testler ─────────────────────────────────────────────────────────────────────────────────────

/// Gerçek veritabanı üzerinde senaryo; `veritabani::tests::uctan_uca` içinden çağrılır.
#[cfg(test)]
pub(crate) fn veritabani_senaryosu(c: &mut postgres::Client) {
    use crate::icra_veri::{dosya_kunyesi_kaydet, ham_tarihleri_turet, YargiTuru};
    use serde_json::json;
    let g = |y, a, d| NaiveDate::from_ymd_opt(y, a, d).unwrap();
    let say = |c: &mut postgres::Client, sql: &str| -> i64 { c.query_one(sql, &[]).unwrap().get(0) };
    let sessiz = |_: &str| {};
    let kunye = |no: &str, birim: i64, ad: &str| {
        json!({"dosyaId": "x", "dosyaNo": no, "dosyaDurumKod": 0, "dosyaDurum": "Açık", "dosyaTurKod": 35, "birimAdi": ad, "birimId": birim})
    };
    let (d, _) = dosya_kunyesi_kaydet(c, &kunye("2026/5", 1001, "İzmir 1. İcra Dairesi"), Some(YargiTuru::Icra.kod())).unwrap();
    let taraflar = vec![
        json!({"adi": "AHMET YILMAZ", "rol": "Borçlu", "kisiKurum": "Kişi"}),
        json!({"adi": "X BANKASI A.Ş.", "rol": "Alacaklı", "kisiKurum": "Kurum"}),
    ];
    kayit::dosya_taraf_kaydet(c, d, &taraflar, &sessiz).unwrap();
    let bag: i64 = c.query_one("SELECT id FROM dosya_taraf WHERE dosya_id = $1 AND rol = 'borclu'", &[&d]).unwrap().get(0);
    let durum = |c: &mut postgres::Client| -> (String, Option<NaiveDate>, String, Option<NaiveDate>, String, Option<NaiveDate>) {
        let s = c
            .query_one(
                "SELECT takip_asamasi, takip_asamasi_tarihi, kesinlesme_durumu, kesinlesme_tarihi, tebligat_durumu, tebligat_durum_tarihi
                   FROM dosya_taraf WHERE id = $1",
                &[&bag],
            )
            .unwrap();
        (s.get(0), s.get(1), s.get(2), s.get(3), s.get(4), s.get(5))
    };
    let hesapla = |c: &mut postgres::Client, bugun: NaiveDate| {
        let s = durumlari_guncelle(c, Some(d), bugun, &sessiz).unwrap();
        assert_eq!(durumlari_guncelle(c, Some(d), bugun, &sessiz).unwrap(), Sonuc::default(), "ikinci hesap bir şey değiştirmez");
        s
    };
    let teb_guncelle = |c: &mut postgres::Client, sql: &str| {
        c.execute(&format!("UPDATE tebligat_barkod SET {sql} WHERE dosya_id = $1 AND birim_evrak_no = 10"), &[&d]).unwrap();
        let id: i64 = c.query_one("SELECT id FROM tebligat_barkod WHERE dosya_id = $1 AND birim_evrak_no = 10", &[&d]).unwrap().get(0);
        ham_tarihleri_turet(c, "tebligat_barkod", id).unwrap();
    };
    c.batch_execute("INSERT INTO kesinlesme_kurali (itiraz_suresi_gun, ad) VALUES (7, 'Varsayılan')").unwrap();

    // Takip talebi: ilk hesap geçmişe yazılmaz
    assert_eq!(hesapla(c, g(2026, 10, 1)), Sonuc { guncellenen: 1, olay: 0, otomatik_is: 0 });
    assert_eq!(durum(c).0, "takip_talebi");
    // Ödeme emri evrakı düzenlendi, tebligatı henüz yok
    kayit::evrak_listesi_kaydet(c, d, &[json!({"birimEvrakNo": 9, "tur": "Ödeme İcra Emri", "onaylandigiTarih": "01/10/2026 09:00"})]).unwrap();
    assert_eq!(hesapla(c, g(2026, 10, 1)).olay, 1);
    assert_eq!((durum(c).0.as_str(), durum(c).1), ("odeme_emri", Some(g(2026, 10, 1))));
    // Tebligat oluşturuldu (barkod yok); borçlusu açıklamadan bulunur
    c.execute(
        "INSERT INTO tebligat_barkod (dosya_id, birim_evrak_no, tebligat_turu, evrak_tarihi, evrak_aciklama)
         VALUES ($1, 10, 'Ödeme Emri', '01/10/2026', 'Normal Tebligat[BORÇLU AHMET YILMAZ]')",
        &[&d],
    )
    .unwrap();
    teb_guncelle(c, "barkod = ''");
    hesapla(c, g(2026, 10, 1));
    assert_eq!(say(c, "SELECT count(*) FROM tebligat_barkod WHERE borclu_id IS NOT NULL"), 1);
    let s = durum(c);
    assert_eq!((s.0.as_str(), s.1, s.2.as_str(), s.3, s.4.as_str()), ("teb_olusturuluyor", Some(g(2026, 10, 1)), "kesinlesme_bekliyor", None, ""));
    teb_guncelle(c, "barkod = '4007663523293'");
    hesapla(c, g(2026, 10, 1));
    assert_eq!(durum(c).0, "ptt_veriliyor");
    teb_guncelle(c, "ptt_durumu = 'KABUL EDİLDİ', son_islem_tarihi = '02/10/2026'");
    assert_eq!(hesapla(c, g(2026, 10, 2)).guncellenen, 1);
    let s = durum(c);
    assert_eq!((s.0.as_str(), s.1, s.4.as_str()), ("ptt_veriliyor", Some(g(2026, 10, 2)), "dagitimda"));
    teb_guncelle(c, "ptt_durumu = 'DAĞITIMDA', son_islem_tarihi = '03/10/2026'");
    hesapla(c, g(2026, 10, 3));
    assert_eq!(durum(c).0, "teblige_cikti");
    // Teslim edildi (mazbata henüz dönmedi): tebliğ, kesinleşme bekliyor ve beklenen gün
    teb_guncelle(c, "ptt_durumu = 'TESLİM EDİLDİ', son_islem_tarihi = '05/10/2026 14:30', teslim_sekli = 'Muhataba bizzat teslim'");
    hesapla(c, g(2026, 10, 6));
    let s = durum(c);
    assert_eq!((s.0.as_str(), s.1), ("teblig", Some(g(2026, 10, 5))));
    assert_eq!((s.2.as_str(), s.3, s.4.as_str(), s.5), ("kesinlesme_bekliyor", Some(g(2026, 10, 13)), "teblig_edildi", Some(g(2026, 10, 5))));
    // Süre doldu ama mazbata dönmedi: hâlâ bekliyor
    hesapla(c, g(2026, 10, 20));
    assert_eq!(durum(c).2, "kesinlesme_bekliyor");
    // Mazbata döndü: süre dolmadan kesinleşmez, dolunca kesinleşir
    teb_guncelle(c, "evrak_aciklama = evrak_aciklama || ' - Tebliğ Edildi'");
    hesapla(c, g(2026, 10, 12));
    assert_eq!(durum(c).2, "kesinlesme_bekliyor");
    hesapla(c, g(2026, 10, 13));
    let s = durum(c);
    assert_eq!((s.0.as_str(), s.1, s.2.as_str(), s.3), ("kesinlesti", Some(g(2026, 10, 13)), "kesinlesti", Some(g(2026, 10, 13))));

    // Geçmiş: aşama olayları yalnız kendi başına görünen geçişler için
    let asamalar: Vec<String> = c
        .query("SELECT yeni FROM durum_olayi WHERE tur = 'takip_asamasi' ORDER BY id", &[])
        .unwrap()
        .iter()
        .map(|s| s.get::<_, String>(0).split(" (").next().unwrap().to_string())
        .collect();
    assert_eq!(asamalar, ["Ödeme Emri", "Ödeme Emri Tebligatı Oluşturuluyor", "Ödeme Emri PTT'ye Veriliyor", "Ödeme Emri Tebliğe Çıktı"]);
    let s = c.query_one("SELECT eski, yeni, olay_tarihi, taraf_adi, kaynak FROM durum_olayi WHERE tur = 'kesinlesme' ORDER BY id DESC LIMIT 1", &[]).unwrap();
    assert_eq!(s.get::<_, String>(0), "Kesinleşme Bekliyor (13.10.2026)");
    assert_eq!(s.get::<_, String>(1), "Kesinleşti (13.10.2026)");
    assert_eq!((s.get::<_, Option<NaiveDate>>(2), s.get::<_, String>(3).as_str(), s.get::<_, String>(4).as_str()), (Some(g(2026, 10, 13)), "AHMET YILMAZ", "hesap"));
    assert_eq!(
        say(c, "SELECT count(*) FROM durum_olayi WHERE tur = 'tebligat' AND yeni = 'Tebliğ Edildi (05.10.2026) — Muhataba bizzat teslim'"),
        1
    );

    // Elle itiraz
    c.execute("UPDATE dosya_taraf SET itiraz_edildi_mi = true, itiraz_tarihi = '2026-10-08' WHERE id = $1", &[&bag]).unwrap();
    hesapla(c, g(2026, 10, 14));
    let s = durum(c);
    assert_eq!((s.0.as_str(), s.1, s.2.as_str(), s.3), ("itiraz", Some(g(2026, 10, 8)), "itiraz", None));
    // İade
    c.execute("UPDATE dosya_taraf SET itiraz_edildi_mi = false WHERE id = $1", &[&bag]).unwrap();
    teb_guncelle(c, "ptt_durumu = 'İADE EDİLDİ', son_islem_tarihi = '06/10/2026', teslim_sekli = 'ADRESTE TANINMIYOR', evrak_aciklama = 'Normal Tebligat[BORÇLU AHMET YILMAZ] - Tebliğ Edilemedi'");
    hesapla(c, g(2026, 10, 14));
    let s = durum(c);
    assert_eq!((s.0.as_str(), s.1, s.2.as_str(), s.4.as_str(), s.5), ("iade", Some(g(2026, 10, 6)), "kesinlesme_bekliyor", "iade", Some(g(2026, 10, 6))));
    assert_eq!(say(c, "SELECT count(*) FROM dosya_taraf WHERE tebligat_sekli = 'ADRESTE TANINMIYOR'"), 1);
    // Dosya UYAP'ta durduruldu
    c.execute("UPDATE dosya SET durum = 'Açık (Durdurulmuş : İhtiyati Tedbir)' WHERE id = $1", &[&d]).unwrap();
    hesapla(c, g(2026, 10, 14));
    assert_eq!((durum(c).0.as_str(), durum(c).2.as_str()), ("durduruldu", "durd_ihtiyati_tedbir"));
    // Elle girilen değer (Konkordato) hesapla ezilmez
    c.execute("UPDATE dosya SET durum = 'Açık' WHERE id = $1", &[&d]).unwrap();
    c.execute("UPDATE dosya_taraf SET kesinlesme_durumu = 'konkordato' WHERE id = $1", &[&bag]).unwrap();
    hesapla(c, g(2026, 10, 14));
    assert_eq!(durum(c).2, "konkordato");

    // Süre tanımlı değilse kesinleşme hesaplanmaz (uyarı verilir); tebliğ durumu yine yazılır
    c.batch_execute("DELETE FROM kesinlesme_kurali; UPDATE dosya_taraf SET kesinlesme_durumu = ''").unwrap();
    let uyarilar = std::cell::RefCell::new(Vec::new());
    durumlari_guncelle(c, Some(d), g(2026, 10, 14), &|m: &str| uyarilar.borrow_mut().push(m.to_string())).unwrap();
    assert!(uyarilar.borrow().iter().any(|m| m.contains("?/?/?")), "{uyarilar:?}");
    assert_eq!((durum(c).2.as_str(), durum(c).4.as_str()), ("", "iade"));

    // Yanlış bağlanmış tebligat çözülür (bankaya giden tebligat borçluya bağlı kalmaz)
    c.execute(
        "INSERT INTO tebligat_barkod (dosya_id, birim_evrak_no, tebligat_turu, borclu_id, evrak_aciklama)
         VALUES ($1, 11, 'Banka', $2, 'Elektronik Tebligat[Tebligat Yapılacak 3. Taraf DENİZBANK A.Ş.]')",
        &[&d, &bag],
    )
    .unwrap();
    assert_eq!(tebligat_borclularini_duzelt(c, Some(d)).unwrap(), 1);
    assert_eq!(say(c, "SELECT count(*) FROM tebligat_barkod WHERE birim_evrak_no = 11 AND borclu_id IS NULL"), 1);

    // Bütün dosyalar kipi: icra dışı dosyada takip aşaması yazılmaz; yeni icra borçlusu yakalanır
    let (h, _) = dosya_kunyesi_kaydet(c, &kunye("2026/7", 2002, "İzmir 3. Asliye Hukuk Mahkemesi"), Some(YargiTuru::Hukuk.kod())).unwrap();
    kayit::dosya_taraf_kaydet(c, h, &[json!({"adi": "MEHMET KAYA", "rol": "Borçlu", "kisiKurum": "Kişi"})], &sessiz).unwrap();
    let (d3, _) = dosya_kunyesi_kaydet(c, &kunye("2026/8", 1001, "İzmir 1. İcra Dairesi"), None).unwrap();
    kayit::dosya_taraf_kaydet(c, d3, &[json!({"adi": "AYŞE DEMİR", "rol": "Borçlu", "kisiKurum": "Kişi"})], &sessiz).unwrap();
    durumlari_guncelle(c, None, g(2026, 10, 14), &sessiz).unwrap();
    assert_eq!(say(c, "SELECT count(*) FROM dosya_taraf dt JOIN dosya d ON d.id = dt.dosya_id WHERE d.yargi_turu = 1 AND dt.takip_asamasi = ''"), 1);
    assert_eq!(say(c, "SELECT count(*) FROM dosya_taraf WHERE takip_asamasi = 'takip_talebi'"), 1);
    assert_eq!(durumlari_guncelle(c, None, g(2026, 10, 14), &sessiz).unwrap(), Sonuc::default());

    c.batch_execute("DELETE FROM dosya; DELETE FROM taraf; DELETE FROM birim; DELETE FROM kesinlesme_kurali").unwrap();
}

#[cfg(test)]
mod tests {
    use super::*;

    fn g(y: i32, a: u32, d: u32) -> NaiveDate {
        NaiveDate::from_ymd_opt(y, a, d).unwrap()
    }

    fn fiziki(ptt: &str, son_islem: Option<NaiveDate>, aciklama: &str) -> OdemeEmri {
        OdemeEmri {
            birim_evrak_no: 10,
            evrak_aciklama: aciklama.into(),
            evrak_gunu: Some(g(2026, 10, 1)),
            barkod: "4007663523293".into(),
            ptt_durumu: ptt.into(),
            son_islem_gunu: son_islem,
            ..Default::default()
        }
    }

    #[test]
    fn sure_hesabi() {
        let bos = Tatiller::new();
        // 05.10.2026 Pazartesi: +7 = 12.10 Pazartesi; kesinleşme 13.10 Salı
        assert_eq!(
            kesinlesme_hesapla(g(2026, 10, 5), 7, &bos),
            KesinlesmeTarihleri { itiraz_son_gunu: g(2026, 10, 12), kesinlesebilir_tarihi: g(2026, 10, 13) }
        );
        // Cuma tebliğ: son gün Cuma, kesinleşme Pazartesi
        assert_eq!(kesinlesme_hesapla(g(2026, 10, 2), 7, &bos).kesinlesebilir_tarihi, g(2026, 10, 12));
        // Cumartesi tebliğ: son gün Cumartesi → Pazartesi'ye kayar, kesinleşme Salı
        let k = kesinlesme_hesapla(g(2026, 10, 3), 7, &bos);
        assert_eq!((k.itiraz_son_gunu, k.kesinlesebilir_tarihi), (g(2026, 10, 12), g(2026, 10, 13)));
        // Son gün resmî tatil (29.10 Perşembe) → Cuma; kesinleşme Pazartesi
        let tatil: Tatiller = [g(2026, 10, 29)].into_iter().collect();
        let k = kesinlesme_hesapla(g(2026, 10, 22), 7, &tatil);
        assert_eq!((k.itiraz_son_gunu, k.kesinlesebilir_tarihi), (g(2026, 10, 30), g(2026, 11, 2)));
        // Aradaki tatil süreyi durdurmaz
        let ara: Tatiller = [g(2026, 10, 7), g(2026, 10, 8)].into_iter().collect();
        assert_eq!(kesinlesme_hesapla(g(2026, 10, 5), 7, &ara).itiraz_son_gunu, g(2026, 10, 12));
        // Kesinleşme günü tatilse bir sonraki iş günü
        let k = kesinlesme_hesapla(g(2026, 10, 5), 7, &[g(2026, 10, 13)].into_iter().collect());
        assert_eq!(k.kesinlesebilir_tarihi, g(2026, 10, 14));
        // Başka süre (5 gün)
        assert_eq!(kesinlesme_hesapla(g(2026, 10, 5), 5, &bos).itiraz_son_gunu, g(2026, 10, 12), "10.10 Cumartesi → Pazartesi");
        assert!(is_gunu_mu(g(2026, 10, 5), &bos) && !is_gunu_mu(g(2026, 10, 4), &bos) && !is_gunu_mu(g(2026, 10, 29), &tatil));
        assert_eq!(sonraki_is_gunu(g(2026, 10, 5), &bos), g(2026, 10, 5));
        assert_eq!(ilk_is_gunu_sonrasi(g(2026, 10, 9), &bos), g(2026, 10, 12));
    }

    #[test]
    fn durdurma_kodlari() {
        assert_eq!(durdurma_kesinlesme_kodu("Açık (Durdurulmuş : Takibe İtiraz)"), Some("itiraz"));
        assert_eq!(durdurma_kesinlesme_kodu("Açık (Durdurulmuş: İhtiyati  Tedbir )"), Some("durd_ihtiyati_tedbir"));
        assert_eq!(durdurma_kesinlesme_kodu("Açık (Durdurulmuş : Tehir-i İcra Kararı)"), Some("durd_tehiri_icra"));
        assert_eq!(durdurma_kesinlesme_kodu("Açık (Durdurulmuş : Borçlunun Ölümü)"), Some("durd_borclu_olumu"));
        assert_eq!(durdurma_kesinlesme_kodu("Açık (Durdurulmuş : Yeni Bir Neden)"), Some("durd_tanimsiz"));
        assert_eq!(durdurma_kesinlesme_kodu("Açık"), None);
        assert_eq!(durdurma_kesinlesme_kodu("Kapalı"), None);
    }

    #[test]
    fn mazbata_sonuclari() {
        assert_eq!(mazbata_sonucu("Normal Tebligat[BORÇLU X] - Tebliğ Edildi"), MazbataSonucu::Edildi);
        assert_eq!(mazbata_sonucu("Normal Tebligat[BORÇLU X] - TEBLİĞ EDİLEMEDİ"), MazbataSonucu::Edilemedi);
        assert_eq!(mazbata_sonucu("Normal Tebligat[BORÇLU TEBLİĞ EDİLDİ A.Ş.]"), MazbataSonucu::Yok, "ad içindeki ifade sayılmaz");
        assert_eq!(mazbata_sonucu("Tebliğ Edildi"), MazbataSonucu::Edildi);
        assert_eq!(mazbata_sonucu(""), MazbataSonucu::Yok);
    }

    #[test]
    fn kesinlesme_kurallari() {
        let bos = Tatiller::new();
        let h = |itiraz, oe: &[OdemeEmri], bugun, durdurma, sure| borclu_kesinlesme_hesapla(itiraz, oe, &bos, bugun, durdurma, sure);
        let teslim = fiziki("TESLİM EDİLDİ", Some(g(2026, 10, 5)), "Normal Tebligat[BORÇLU X]");
        let mazbatali = fiziki("TESLİM EDİLDİ", Some(g(2026, 10, 5)), "Normal Tebligat[BORÇLU X] - Tebliğ Edildi");
        assert_eq!(h(true, &[mazbatali.clone()], g(2026, 11, 1), Some("durd_iflas"), Some(7)), ("itiraz".into(), None), "elle itiraz her şeyden önce");
        assert_eq!(h(false, &[mazbatali.clone()], g(2026, 11, 1), Some("durd_iflas"), Some(7)), ("durd_iflas".into(), None));
        assert_eq!(h(false, &[], g(2026, 11, 1), None, Some(7)), (String::new(), None), "ödeme emri yok");
        assert_eq!(h(false, &[mazbatali.clone()], g(2026, 11, 1), None, None), (String::new(), None), "süre tanımlı değil");
        assert_eq!(h(false, &[fiziki("DAĞITIMDA", Some(g(2026, 10, 3)), "x")], g(2026, 11, 1), None, Some(7)), ("kesinlesme_bekliyor".into(), None));
        assert_eq!(h(false, &[teslim.clone()], g(2026, 11, 1), None, Some(7)), ("kesinlesme_bekliyor".into(), Some(g(2026, 10, 13))), "mazbata dönmeden kesinleşmez");
        assert_eq!(h(false, &[mazbatali.clone()], g(2026, 10, 12), None, Some(7)), ("kesinlesme_bekliyor".into(), Some(g(2026, 10, 13))));
        assert_eq!(h(false, &[mazbatali.clone()], g(2026, 10, 13), None, Some(7)), ("kesinlesti".into(), Some(g(2026, 10, 13))));
        // Mazbata açıklamasındaki barkod da kanıttır; barkodsuz "Var" değildir
        let mut barkodlu = teslim.clone();
        barkodlu.tebligat_mazbatasi_var = true;
        barkodlu.tebligat_mazbatasi_aciklama = "Ödeme Emri - [X] - Barkod Numarası: 4007663523293 - Tebliğ Edildi".into();
        assert_eq!(h(false, &[barkodlu.clone()], g(2026, 10, 13), None, Some(7)).0, "kesinlesti");
        barkodlu.tebligat_mazbatasi_aciklama = "Var".into();
        assert_eq!(h(false, &[barkodlu], g(2026, 10, 13), None, Some(7)).0, "kesinlesme_bekliyor");
        // Tebliğ edilemedi dönmüşse PTT "teslim edildi" dese de sayılmaz
        let edilemedi = fiziki("TESLİM EDİLDİ", Some(g(2026, 10, 5)), "Normal Tebligat[BORÇLU X] - Tebliğ Edilemedi");
        assert_eq!(h(false, &[edilemedi], g(2026, 11, 1), None, Some(7)), ("kesinlesme_bekliyor".into(), None));
        // İki ödeme emri: mazbatası dönmüş eski tebliğ kesinleştirir
        let yeni_teslim = fiziki("TESLİM EDİLDİ", Some(g(2026, 10, 20)), "Normal Tebligat[BORÇLU X]");
        assert_eq!(h(false, &[mazbatali.clone(), yeni_teslim.clone()], g(2026, 10, 21), None, Some(7)), ("kesinlesti".into(), Some(g(2026, 10, 13))));
        assert_eq!(h(false, &[yeni_teslim], g(2026, 10, 21), None, Some(7)), ("kesinlesme_bekliyor".into(), Some(g(2026, 10, 28))));
        // E-tebligat: tebliğ = evrak tarihi, süre 5 gün sonra başlar, mazbata beklenmez
        let e = OdemeEmri { elektronik_tebligat: true, son_islem_gunu: Some(g(2026, 10, 1)), evrak_gunu: Some(g(2026, 10, 1)), ..Default::default() };
        assert_eq!(e.sure_baslangici(), Some(g(2026, 10, 6)));
        assert_eq!(h(false, &[e.clone()], g(2026, 10, 5), None, Some(7)), ("kesinlesme_bekliyor".into(), None), "okuma süresi dolmadı");
        assert_eq!(h(false, &[e.clone()], g(2026, 10, 6), None, Some(7)), ("kesinlesme_bekliyor".into(), Some(g(2026, 10, 14))));
        assert_eq!(h(false, &[e], g(2026, 10, 14), None, Some(7)), ("kesinlesti".into(), Some(g(2026, 10, 14))));
        // Ham metinden tarih (türetilmiş alan boşsa)
        let ham = OdemeEmri { ptt_durumu: "TESLİM EDİLDİ".into(), son_islem_tarihi: "05/10/2026 14:30".into(), ..Default::default() };
        assert_eq!(ham.son_islem(), Some(g(2026, 10, 5)));
    }

    #[test]
    fn tebligat_durumu() {
        let h = |oe: &[OdemeEmri]| borclu_tebligat_hesapla(oe, g(2026, 10, 10));
        assert_eq!(h(&[]), (String::new(), None, String::new()));
        let mut teslim = fiziki("TESLİM EDİLDİ", Some(g(2026, 10, 5)), "x");
        teslim.teslim_sekli = "Muhataba bizzat teslim".into();
        assert_eq!(h(&[teslim.clone()]), ("teblig_edildi".into(), Some(g(2026, 10, 5)), "Muhataba bizzat teslim".into()));
        let iade = fiziki("İADE EDİLDİ", Some(g(2026, 10, 7)), "x");
        assert_eq!(h(&[iade.clone(), teslim.clone()]).0, "teblig_edildi", "biri tebliğ edildiyse tebliğ edildi");
        assert_eq!(h(&[fiziki("DAĞITIMDA", None, "x - Tebliğ Edildi")]), ("teblig_edildi".into(), None, String::new()), "mazbatadan, tarihsiz");
        let mut iade_nedenli = iade.clone();
        iade_nedenli.teslim_sekli = "ADRESTE TANINMIYOR".into();
        assert_eq!(h(&[iade_nedenli]), ("iade".into(), Some(g(2026, 10, 7)), "ADRESTE TANINMIYOR".into()));
        assert_eq!(h(&[fiziki("DAĞITIMDA", None, "x - Tebliğ Edilemedi")]).0, "iade");
        assert_eq!(h(&[fiziki("DAĞITIMDA", Some(g(2026, 10, 3)), "x")]), ("dagitimda".into(), None, String::new()));
        assert_eq!(h(&[fiziki("Kayıt yok", None, "x")]).0, "");
        assert_eq!(h(&[fiziki("", None, "x")]).0, "");
        // Evrak tarihi en yeni olan belirler: eski iade + yeni dağıtımda → dağıtımda
        let mut yeni = fiziki("DAĞITIMDA", None, "x");
        yeni.evrak_gunu = Some(g(2026, 10, 8));
        assert_eq!(h(&[iade, yeni]).0, "dagitimda");
        // E-tebligat: günü gelmeden dağıtımda, gelince tebliğ edildi
        let e = OdemeEmri { elektronik_tebligat: true, son_islem_gunu: Some(g(2026, 10, 6)), ..Default::default() };
        assert_eq!(h(&[e.clone()]).0, "dagitimda");
        assert_eq!(borclu_tebligat_hesapla(&[e], g(2026, 10, 11)), ("teblig_edildi".into(), Some(g(2026, 10, 6)), "E-tebligat".into()));
    }

    #[test]
    fn takip_asamalari() {
        let b = |kes: &str, teb: &str| BorcluDurumu {
            kesinlesme_durumu: kes.into(),
            kesinlesme_tarihi: Some(g(2026, 10, 13)),
            tebligat_durumu: teb.into(),
            tebligat_durum_tarihi: Some(g(2026, 10, 5)),
            itiraz_tarihi: Some(g(2026, 10, 8)),
            ..Default::default()
        };
        let oe = [fiziki("DAĞITIMDA", Some(g(2026, 10, 3)), "x")];
        let h = |d: &BorcluDurumu, oe: &[OdemeEmri]| takip_asamasi_hesapla(d, oe, None);
        assert_eq!(h(&b("kesinlesti", "teblig_edildi"), &oe), (A::Kesinlesti, Some(g(2026, 10, 13))));
        assert_eq!(h(&b("itiraz", "teblig_edildi"), &oe), (A::Itiraz, Some(g(2026, 10, 8))));
        assert_eq!(h(&b("durd_iflas", "teblig_edildi"), &oe), (A::Durduruldu, None));
        assert_eq!(h(&b("kesinlesme_bekliyor", "teblig_edildi"), &oe), (A::Teblig, Some(g(2026, 10, 5))));
        assert_eq!(h(&b("kesinlesme_bekliyor", "iade"), &oe), (A::Iade, Some(g(2026, 10, 5))));
        assert_eq!(h(&b("konkordato", "dagitimda"), &oe), (A::TebligeCikti, Some(g(2026, 10, 3))));
        assert_eq!(h(&b("", ""), &[]), (A::TakipTalebi, None));
        assert_eq!(takip_asamasi_hesapla(&b("", ""), &[], Some(Some(g(2026, 10, 1)))), (A::OdemeEmri, Some(g(2026, 10, 1))));
        assert_eq!(takip_asamasi_hesapla(&b("", ""), &[], Some(None)), (A::OdemeEmri, None));
        let mut k = fiziki("", None, "x");
        k.barkod = " ".into();
        assert_eq!(h(&b("", ""), &[k.clone()]), (A::TebligatOlusturuluyor, Some(g(2026, 10, 1))));
        k.barkod = "4007663523293".into();
        assert_eq!(h(&b("", ""), &[k.clone()]), (A::PttVeriliyor, Some(g(2026, 10, 1))));
        k.ptt_durumu = "Kayıt yok".into();
        assert_eq!(h(&b("", ""), &[k.clone()]).0, A::PttVeriliyor);
        k.ptt_durumu = "KABUL EDİLDİ".into();
        k.son_islem_gunu = Some(g(2026, 10, 2));
        assert_eq!(h(&b("", "dagitimda"), &[k.clone()]), (A::PttVeriliyor, Some(g(2026, 10, 2))));
        k.elektronik_tebligat = true;
        assert_eq!(h(&b("", "dagitimda"), &[k]), (A::TebligeCikti, Some(g(2026, 10, 1))));
        // En yeni ödeme emri belirler
        let mut yeni = fiziki("", None, "x");
        yeni.birim_evrak_no = 20;
        yeni.evrak_gunu = Some(g(2026, 10, 9));
        assert_eq!(h(&b("", ""), &[oe[0].clone(), yeni]), (A::PttVeriliyor, Some(g(2026, 10, 9))));
    }

    #[test]
    fn metinler() {
        assert_eq!(kesinlesme_metni("kesinlesme_bekliyor", Some(g(2026, 10, 14))), "Kesinleşme Bekliyor (14.10.2026)");
        assert_eq!(kesinlesme_metni("durd_iflas", None), "Durdurulmuş: İflas");
        assert_eq!(kesinlesme_metni("", Some(g(2026, 10, 14))), "");
        assert_eq!(tebligat_metni("teblig_edildi", Some(g(2026, 9, 12)), "Muhtara teslim (21. md.)"), "Tebliğ Edildi (12.09.2026) — Muhtara teslim (21. md.)");
        assert_eq!(tebligat_metni("dagitimda", None, ""), "Dağıtımda");
        assert_eq!(takip_asamasi_metni("teblig", Some(g(2026, 10, 5))), "Tebliğ (05.10.2026)");
        assert_eq!(takip_asamasi_metni("", None), "");
    }
}
