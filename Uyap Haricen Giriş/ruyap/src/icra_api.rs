//! Kendi ekranlarımız için büro veritabanı uçları (Aşama 7, veri katmanı).
//!
//! `/__cu__/veri/…` yolları bütün büro kullanıcılarına açıktır (bu bilgisayar, ofis ağı ve tünel);
//! veritabanı ofis bilgisayarındadır. Yalnız büro veritabanını okur / yazar; UYAP'a istek atmaz. Ekranlar (borçlu listesi, dosya penceresi, geçmiş, ayarlar) bu
//! uçlardan beslenir. Yanıt biçimi: `{"ok": true, "veri": …}` ya da `{"ok": false, "error": "…"}`.
//!
//!   GET  ozet                 sayılar ve takip aşamalarına göre dağılım
//!   GET  borclular            ?q=&asama=&durum=acik|kapali&birim=&alacakli=&borclu=&yil=&dosya_tur=&acilis_bas=&acilis_son=
//!                             &tur=&teblig=&mazbata=&kesinlesme=&sayfa=1&boyut=50 — borçlu başına satır (borçlusuz dosya
//!                             tek satır) + borçlunun bütün tebligatları + süzgeç seçenekleri (adetli)
//!   GET  dosya                ?id= — künye, takip türü, taraflar, tebligatlar, uygulanan süre
//!   GET  gecmis               ?dosya= — durum olayları (yeniden eskiye)
//!   GET  ptt                  ?tebligat= — PTT hareketleri
//!   GET  kurallar             kesinleşme süreleri + veritabanındaki takip türü dağılımı
//!   POST kural / kural_sil    süre ekle, düzenle, sil (ardından bütün borçlular yeniden hesaplanır)
//!   GET  tatiller · POST tatil / tatil_sil
//!   POST itiraz               {bag, edildi, tarih} — elle itiraz
//!   POST kesinlesme_elle      {bag, durum} — "", konkordato, vefat, vasi
//!   POST islem_durumu         {dosya, durum, notu, bekleme_bitis}
//!   POST hesapla              {dosya?} — kesinleşme / tebliğ / takip aşamasını yeniden hesapla

use crate::icra_durum as durum;
use crate::icra_tebligat::{self as teb, tr_kucuk};
use crate::icra_veri::{
    self as veri, durum_olayi_yaz, IslemDurumu, KesinlesmeDurumu, OlayKaynak, OlayTur, Rol, TakipAsamasi, YeniOlay,
};
use chrono::NaiveDate;
use postgres::GenericClient;
use serde_json::{json, Value};
use std::collections::{BTreeSet, HashMap};

pub const ON_EK: &str = "__cu__/veri/";

pub fn is_path(path: &str) -> bool {
    path.trim_start_matches('/').starts_with(ON_EK)
}

fn hata(e: postgres::Error) -> String {
    format!("veritabanı: {e}")
}

fn gun(t: Option<NaiveDate>) -> Value {
    t.map(|t| json!(t.format("%Y-%m-%d").to_string())).unwrap_or(Value::Null)
}

fn gun_coz(v: &Value) -> Result<Option<NaiveDate>, String> {
    match v {
        Value::Null => Ok(None),
        Value::String(s) if s.trim().is_empty() => Ok(None),
        Value::String(s) => veri::tarih::gun_coz(s).map(Some).ok_or_else(|| format!("Tarih anlaşılamadı: {s}")),
        _ => Err("Tarih metin olmalı.".into()),
    }
}

/// Tarafın görünen adı (SQL): tüzelde unvan, gerçekte ad soyad; yoksa kimlik.
fn ad_sql(t: &str) -> String {
    format!(
        "CASE WHEN {t}.tur = 'tuzel'
              THEN COALESCE(NULLIF(btrim({t}.unvan), ''), {t}.mersis_no, NULLIF({t}.vergi_no, ''), 'Taraf #' || {t}.id)
              ELSE COALESCE(NULLIF(btrim({t}.ad || ' ' || {t}.soyad), ''), {t}.tckn, 'Taraf #' || {t}.id) END"
    )
}

/// UYAP'ın dosya kartını açmak için gereken künye (web arayüzü dosyayı UYAP'ta güncel kimliğiyle yeniden
/// bulup kartı açar; saklanan oturumluk `dosyaId` kullanılmaz).
#[allow(clippy::too_many_arguments)]
fn uyap_karti(birim_id: &str, birim_adi: &str, dosya_no: &str, tur_kod: i32, tur: &str, turu3: &str, durum: &str, durum_kod: i32) -> Value {
    json!({"birimId": birim_id, "birimAdi": birim_adi, "dosyaNo": dosya_no, "dosyaTurKod": tur_kod,
           "dosyaTurKodAciklama": tur, "birimTuru3": turu3, "dosyaDurum": durum, "dosyaDurumKod": durum_kod})
}

// ── Okuma ───────────────────────────────────────────────────────────────────────────────────────

/// Sayılar ve icra borçlularının takip aşamalarına göre dağılımı (aşama sırasıyla).
pub fn ozet<C: GenericClient>(c: &mut C) -> Result<Value, String> {
    let s = c
        .query_one(
            "SELECT (SELECT count(*) FROM dosya), (SELECT count(*) FROM dosya WHERE yargi_turu = 2),
                    (SELECT count(*) FROM dosya WHERE yargi_turu = 2 AND durum_kod = 0),
                    (SELECT count(*) FROM tebligat_barkod),
                    (SELECT to_char(max(son_senkron), 'YYYY-MM-DD HH24:MI') FROM dosya)",
            &[],
        )
        .map_err(hata)?;
    let mut sayilar: HashMap<String, i64> = HashMap::new();
    for r in c
        .query(
            "SELECT dt.takip_asamasi, count(*) FROM dosya_taraf dt JOIN dosya d ON d.id = dt.dosya_id
              WHERE dt.rol = 'borclu' AND d.yargi_turu = 2 GROUP BY dt.takip_asamasi",
            &[],
        )
        .map_err(hata)?
    {
        sayilar.insert(r.get(0), r.get(1));
    }
    let mut asamalar: Vec<Value> = TakipAsamasi::HEPSI
        .iter()
        .map(|a| json!({"kod": a.kod(), "etiket": a.etiket(), "adet": sayilar.get(a.kod()).copied().unwrap_or(0)}))
        .collect();
    if let Some(n) = sayilar.get("").filter(|n| **n > 0) {
        asamalar.push(json!({"kod": "", "etiket": "Henüz hesaplanmadı", "adet": n}));
    }
    // İcra daireleri (UYAP'tan güncelleme kapsamı için UYAP birim kimliğiyle).
    let birimler: Vec<Value> = c
        .query(
            "SELECT b.ad, b.birim_id, count(*) FILTER (WHERE d.durum_kod = 0) FROM dosya d JOIN birim b ON b.id = d.birim_id
              WHERE d.yargi_turu = 2 GROUP BY b.ad, b.birim_id ORDER BY b.ad",
            &[],
        )
        .map_err(hata)?
        .iter()
        .map(|r| json!({"ad": r.get::<_, String>(0), "birim_id": r.get::<_, String>(1), "acik": r.get::<_, i64>(2)}))
        .collect();
    Ok(json!({
        "birimler": birimler,
        "dosya": s.get::<_, i64>(0), "icra_dosya": s.get::<_, i64>(1), "acik_icra_dosya": s.get::<_, i64>(2),
        "tebligat": s.get::<_, i64>(3), "son_guncelleme": s.get::<_, Option<String>>(4),
        "borclu": sayilar.values().sum::<i64>(), "asamalar": asamalar,
    }))
}

#[derive(Debug, Default, Clone)]
pub struct Filtre {
    /// Dosya no, birim, borçlu ya da alacaklı adında aranır (Türkçe-duyarsız).
    pub q: String,
    /// Takip aşaması kodu; boş = hepsi.
    pub asama: String,
    /// Dosyanın UYAP durumu: "acik" | "kapali" | "" (hepsi).
    pub durum: String,
    /// İcra dairesi. Düz metin: içinde geçen; `=` ile başlarsa tam eşleşme (listeden seçilen).
    pub birim: String,
    /// Dosyanın alacaklılarından biri; aynı kural.
    pub alacakli: String,
    /// Borçlunun adı; aynı kural.
    pub borclu: String,
    /// Dosya yılı (tam değer); "" = hepsi.
    pub yil: String,
    /// Dosya türü (tam değer: İcra Dosyası, MTS, Talimat Dosyası …); "" = hepsi.
    pub dosya_tur: String,
    /// Açılış tarihi aralığı (iki uç dahil); boş uç sınırsız.
    pub acilis_bas: Option<NaiveDate>,
    pub acilis_son: Option<NaiveDate>,
    /// Tebligat türü (borçlunun tebligatlarından biri). Düz metin: içinde geçen; `=` ile başlarsa tam eşleşme.
    pub tur: String,
    /// Tebligatın PTT / tebliğ durumu; aynı kural. `=` tek başına: durumu boş (henüz sorgulanmamış).
    pub teblig: String,
    /// Mazbata dosyaya döndü mü: "var" | "yok" | "" (hepsi).
    pub mazbata: String,
    /// Kesinleşme durumu kodu; "-" = henüz hesaplanmamış; "" = hepsi.
    pub kesinlesme: String,
    pub sayfa: usize,
    pub boyut: usize,
}

/// Listedeki bir tebligatın süzgeçlerde kullanılan alanları.
struct ListeTebligati {
    tur: String,
    ptt: String,
    mazbata: bool,
    veri: Value,
}

/// Listeden seçilen değerler satır sonuyla ayrılır (birden çok seçim: herhangi birine uyan).
pub const AYRAC: char = '\n';

/// Başlık kutusundaki metin süzgeci: boş = hepsi; `=değer` tam eşleşme (listeden seçilen; birden çok değer
/// satır sonuyla ayrılır, herhangi biri yeter); düz metin içinde geçen.
fn metin_uyar(suzgec: &str, deger: &str) -> bool {
    match suzgec.strip_prefix('=') {
        Some(tamlar) => {
            let d = tr_kucuk(deger.trim());
            tamlar.split(AYRAC).any(|tam| tr_kucuk(tam.trim()) == d)
        }
        None => suzgec.trim().is_empty() || tr_kucuk(deger).contains(tr_kucuk(suzgec.trim()).as_str()),
    }
}

/// Kodlu (yalnız listeden seçilen) süzgeç: boş = hepsi, yoksa seçilen değerlerden biri.
fn kod_uyar(suzgec: &str, deger: &str) -> bool {
    suzgec.is_empty() || suzgec.split(AYRAC).any(|s| s == deger)
}

fn mazbata_uyar(suzgec: &str, var: bool) -> bool {
    suzgec.is_empty()
        || suzgec.split(AYRAC).any(|s| match s {
            "var" => var,
            "yok" => !var,
            _ => false,
        })
}

fn kesinlesme_uyar(suzgec: &str, kod: &str) -> bool {
    suzgec.is_empty()
        || suzgec.split(AYRAC).any(|s| match s {
            "-" => kod.is_empty(),
            k => k == kod,
        })
}

/// Seçenek listesi: değere göre adet, çoktan aza (eşitlikte ada göre).
fn secenek_listesi(sayim: HashMap<String, i64>, etiket: &dyn Fn(&str) -> String) -> Vec<Value> {
    secenek_sinirli(sayim, etiket, usize::MAX)
}

/// Uzun olabilecek listeler (borçlu, alacaklı, daire) için en çok `en_cok` seçenek; kalanına yazarak ulaşılır
/// (yazılan metin o kutunun kendi seçeneklerini de daraltır, bkz. [`borclu_listesi`]).
pub const SECENEK_SINIRI: usize = 300;

fn secenek_sinirli(sayim: HashMap<String, i64>, etiket: &dyn Fn(&str) -> String, en_cok: usize) -> Vec<Value> {
    let mut v: Vec<(String, i64)> = sayim.into_iter().collect();
    v.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    v.into_iter().take(en_cok).map(|(d, n)| json!({"deger": d, "etiket": etiket(&d), "adet": n})).collect()
}

/// Bir satırın künye süzgeçleri (daire, alacaklı, borçlu, yıl, tür). Seçenek sayımında bir kutunun
/// kendi süzgeci dışarıda tutulur; ancak düz yazıysa (listeden seçilmemişse) uygulanır, böylece yazılan
/// metin o kutunun seçeneklerini daraltır ve sınırın ötesindeki adlar da bulunur.
#[derive(Clone, Copy, PartialEq)]
enum Kunye {
    Birim,
    Alacakli,
    Borclu,
    Yil,
    Tur,
}

/// İcra dosyaları borçlu başına satır: dosya, borçlu, alacaklı, takip aşaması, kesinleşme ve tebliğ
/// durumu, borçluya giden BÜTÜN tebligatlar (barkod, tür, PTT durumu, mazbata). Borçlu kaydı olmayan
/// dosya da tek satırla gelir (`bag` boş) — kapalı dosyaların çoğunda yalnız künye kayıtlıdır.
/// Sıra: aşama günü yeniden eskiye, sonra dosya numarası.
///
/// Künye süzgeçleri: daire, alacaklı, borçlu (yazı: içinde geçen; `=`: tam), yıl ve dosya türü (tam),
/// açılış tarihi aralığı, dosya durumu (açık / kapalı / hepsi).
///
/// Tebligat süzgeçleri (tür, tebliğ, mazbata) aynı tebligatta birlikte aranır: "Ödeme Emri + İADE" seçilince
/// iade dönen ödeme emri olan borçlular gelir. Satırdaki tebligatların hepsi gösterilir; süzgece uyanlar
/// `uyan: true` ile işaretlenir.
///
/// `secenekler`: her süzgeç için, DİĞER süzgeçler uygulanmışken kalan değerler ve satır adetleri — kullanıcı
/// boş sonuç verecek bir seçenek görmez.
pub fn borclu_listesi<C: GenericClient>(c: &mut C, f: &Filtre) -> Result<Value, String> {
    let durum_kosulu = match f.durum.as_str() {
        "acik" => "AND d.durum_kod = 0",
        "kapali" => "AND d.durum_kod <> 0",
        _ => "",
    };
    let sql = format!(
        "SELECT dt.id, d.id, b.ad, d.dosya_no, d.durum, d.durum_kod, d.islem_durumu, COALESCE({borclu}, ''),
                COALESCE(dt.takip_asamasi, ''), dt.takip_asamasi_tarihi, COALESCE(dt.kesinlesme_durumu, ''), dt.kesinlesme_tarihi,
                COALESCE(dt.tebligat_durumu, ''), dt.tebligat_durum_tarihi, COALESCE(dt.tebligat_sekli, ''),
                COALESCE(dt.uyapta_gorulmedi IS NOT NULL, false), COALESCE(dt.itiraz_edildi_mi, false),
                ARRAY(SELECT {alacakli} FROM dosya_taraf x JOIN taraf a ON a.id = x.taraf_id
                       WHERE x.dosya_id = d.id AND x.rol = 'alacakli' ORDER BY x.sira, x.id),
                b.birim_id, b.turu3, d.tur_kod, d.tur, d.acilis_tarihi::date, d.yil
           FROM dosya d JOIN birim b ON b.id = d.birim_id
           LEFT JOIN dosya_taraf dt ON dt.dosya_id = d.id AND dt.rol = 'borclu'
           LEFT JOIN taraf t ON t.id = dt.taraf_id
          WHERE d.yargi_turu = 2 {durum_kosulu}
            AND ($1::text = '' OR dt.takip_asamasi = $1)
          ORDER BY dt.takip_asamasi_tarihi DESC NULLS LAST, d.yil DESC, d.sira_no DESC, dt.sira, dt.id",
        borclu = ad_sql("t"),
        alacakli = ad_sql("a"),
    );
    let satir_kayitlari = c.query(&sql, &[&f.asama]).map_err(hata)?;

    // Borçluya bağlı tebligatlar (bankalara giden haciz ihbarnameleri gibi borçlusuz kayıtlar dosya panelinde kalır).
    let mut tebligatlar: HashMap<i64, Vec<ListeTebligati>> = HashMap::new();
    for s in c
        .query(
            "SELECT tb.borclu_id, tb.id, tb.tebligat_turu, tb.evrak_tarihi, tb.barkod, tb.elektronik_tebligat, tb.ptt_durumu,
                    tb.son_islem_tarihi, tb.teslim_sekli, tb.tebligat_mazbatasi_var, tb.tebligat_mazbatasi_aciklama
               FROM tebligat_barkod tb JOIN dosya d ON d.id = tb.dosya_id
              WHERE tb.borclu_id IS NOT NULL AND d.yargi_turu = 2
              ORDER BY tb.evrak_gunu DESC NULLS LAST, tb.birim_evrak_no DESC",
            &[],
        )
        .map_err(hata)?
    {
        let (tur, ptt, mazbata): (String, String, bool) = (s.get(2), s.get(6), s.get(9));
        let veri = json!({
            "id": s.get::<_, i64>(1), "tur": tur, "evrak_tarihi": s.get::<_, String>(3), "barkod": s.get::<_, String>(4),
            "elektronik": s.get::<_, bool>(5), "ptt_durumu": ptt, "son_islem_tarihi": s.get::<_, String>(7),
            "teslim_sekli": s.get::<_, String>(8), "mazbata_var": mazbata, "mazbata": s.get::<_, String>(10),
        });
        tebligatlar.entry(s.get(0)).or_default().push(ListeTebligati { tur, ptt, mazbata, veri });
    }

    let (tur_var, teblig_var, mazbata_var) = (!f.tur.trim().is_empty(), !f.teblig.trim().is_empty(), !f.mazbata.is_empty());
    let aranan: Vec<String> = tr_kucuk(&f.q).split_whitespace().map(str::to_string).collect();
    let bos: Vec<ListeTebligati> = Vec::new();
    let mut say_tur: HashMap<String, i64> = HashMap::new();
    let mut say_teblig: HashMap<String, i64> = HashMap::new();
    let mut say_mazbata: HashMap<String, i64> = HashMap::new();
    let mut say_kes: HashMap<String, i64> = HashMap::new();
    let mut say_birim: HashMap<String, i64> = HashMap::new();
    let mut say_alacakli: HashMap<String, i64> = HashMap::new();
    let mut say_borclu: HashMap<String, i64> = HashMap::new();
    let mut say_yil: HashMap<String, i64> = HashMap::new();
    let mut say_dosya_tur: HashMap<String, i64> = HashMap::new();
    let ekle = |sayim: &mut HashMap<String, i64>, degerler: BTreeSet<String>| {
        for d in degerler {
            *sayim.entry(d).or_default() += 1;
        }
    };
    // Bir kutunun kendi seçenekleri sayılırken o kutunun süzgeci: listeden seçildiyse (`=` / kodlu) yok sayılır,
    // düz yazıysa uygulanır.
    let yazi = |s: &str| !s.is_empty() && !s.starts_with('=');
    let mut satirlar: Vec<(Option<i64>, Value)> = Vec::new();
    let mut dosyalar: BTreeSet<i64> = BTreeSet::new();
    for s in satir_kayitlari {
        let (birim, dosya_no, borclu): (String, String, String) = (s.get(2), s.get(3), s.get(7));
        let alacaklilar: Vec<String> = s.get(17);
        let alacakli = alacaklilar.join(", ");
        let (dosya_tur, acilis, yil): (String, Option<NaiveDate>, i32) = (s.get(21), s.get(22), s.get(23));
        let yil_metni = if yil > 0 { yil.to_string() } else { String::new() };
        if !aranan.is_empty() {
            let metin = tr_kucuk(&format!("{birim} {dosya_no} {borclu} {alacakli}"));
            if !aranan.iter().all(|k| metin.contains(k)) {
                continue;
            }
        }
        if f.acilis_bas.is_some_and(|b| acilis.is_none_or(|a| a < b)) || f.acilis_son.is_some_and(|son| acilis.is_none_or(|a| a > son)) {
            continue;
        }
        // Künye süzgeçleri; `haric` verilen kutu (kendi seçenekleri sayılırken) yalnız düz yazıysa uygulanır.
        let kunye_uyar = |haric: Option<Kunye>| {
            let uygula = |k: Kunye, deger: &str| haric != Some(k) || yazi(deger);
            (!uygula(Kunye::Birim, &f.birim) || metin_uyar(&f.birim, &birim))
                && (!uygula(Kunye::Alacakli, &f.alacakli) || f.alacakli.is_empty() || alacaklilar.iter().any(|a| metin_uyar(&f.alacakli, a)))
                && (!uygula(Kunye::Borclu, &f.borclu) || metin_uyar(&f.borclu, &borclu))
                && (haric == Some(Kunye::Yil) || kod_uyar(&f.yil, &yil_metni))
                && (haric == Some(Kunye::Tur) || kod_uyar(&f.dosya_tur, &dosya_tur))
        };
        let kunye_tamam = kunye_uyar(None);
        let bag: Option<i64> = s.get(0);
        let (kes, kes_t): (String, Option<NaiveDate>) = (s.get(10), s.get(11));
        let tebl = bag.and_then(|b| tebligatlar.get(&b)).unwrap_or(&bos);
        let kes_uyar = kesinlesme_uyar(&f.kesinlesme, &kes);
        // Hangi süzgeçler hesaba katılsın: bir boyutun seçenekleri hesaplanırken o boyut dışarıda tutulur.
        let teb_uyar = |t: &ListeTebligati, tur: bool, teblig: bool, mazbata: bool| {
            (!tur || metin_uyar(&f.tur, &t.tur)) && (!teblig || metin_uyar(&f.teblig, &t.ptt)) && (!mazbata || mazbata_uyar(&f.mazbata, t.mazbata))
        };
        if kes_uyar && kunye_tamam {
            ekle(&mut say_tur, tebl.iter().filter(|t| teb_uyar(t, false, teblig_var, mazbata_var)).map(|t| t.tur.clone()).collect());
            ekle(&mut say_teblig, tebl.iter().filter(|t| teb_uyar(t, tur_var, false, mazbata_var)).map(|t| t.ptt.clone()).collect());
            ekle(
                &mut say_mazbata,
                tebl.iter()
                    .filter(|t| teb_uyar(t, tur_var, teblig_var, false))
                    .map(|t| if t.mazbata { "var" } else { "yok" }.to_string())
                    .collect(),
            );
        }
        let suzgec_var = tur_var || teblig_var || mazbata_var;
        let teb_tamam = !suzgec_var || tebl.iter().any(|t| teb_uyar(t, tur_var, teblig_var, mazbata_var));
        if teb_tamam && kunye_tamam {
            *say_kes.entry(if kes.is_empty() { "-".to_string() } else { kes.clone() }).or_default() += 1;
        }
        if kes_uyar && teb_tamam {
            if kunye_uyar(Some(Kunye::Birim)) {
                *say_birim.entry(birim.clone()).or_default() += 1;
            }
            if kunye_uyar(Some(Kunye::Alacakli)) {
                let uyanlar: BTreeSet<String> =
                    alacaklilar.iter().filter(|a| !yazi(&f.alacakli) || metin_uyar(&f.alacakli, a)).cloned().collect();
                ekle(&mut say_alacakli, uyanlar);
            }
            if !borclu.is_empty() && kunye_uyar(Some(Kunye::Borclu)) {
                *say_borclu.entry(borclu.clone()).or_default() += 1;
            }
            if !yil_metni.is_empty() && kunye_uyar(Some(Kunye::Yil)) {
                *say_yil.entry(yil_metni.clone()).or_default() += 1;
            }
            if !dosya_tur.is_empty() && kunye_uyar(Some(Kunye::Tur)) {
                *say_dosya_tur.entry(dosya_tur.clone()).or_default() += 1;
            }
        }
        if !(kes_uyar && teb_tamam && kunye_tamam) {
            continue;
        }
        let dosya: i64 = s.get(1);
        dosyalar.insert(dosya);
        let (asama, asama_t): (String, Option<NaiveDate>) = (s.get(8), s.get(9));
        let (tebl_d, tebl_t, tebl_s): (String, Option<NaiveDate>, String) = (s.get(12), s.get(13), s.get(14));
        satirlar.push((bag, json!({
            "bag": bag, "dosya": dosya, "birim": birim, "dosya_no": dosya_no, "dosya_tur": dosya_tur,
            "dosya_durumu": s.get::<_, String>(4), "acik": s.get::<_, i32>(5) == 0, "acilis_tarihi": gun(acilis),
            "islem_durumu": s.get::<_, String>(6), "borclu": borclu, "alacakli": alacakli,
            "asama": asama, "asama_etiket": if bag.is_some() { TakipAsamasi::etiket_bul(&asama) } else { String::new() },
            "asama_tarihi": gun(asama_t),
            "kesinlesme": if bag.is_some() { durum::kesinlesme_metni(&kes, kes_t) } else { String::new() },
            "kesinlesme_durumu": kes, "kesinlesme_tarihi": gun(kes_t),
            "tebligat": if bag.is_some() { durum::tebligat_metni(&tebl_d, tebl_t, &tebl_s) } else { String::new() },
            "tebligat_durumu": tebl_d,
            "uyapta_gorulmedi": s.get::<_, bool>(15), "itiraz": s.get::<_, bool>(16),
            "uyap": uyap_karti(&s.get::<_, String>(18), &birim, &dosya_no, s.get(20), &dosya_tur, &s.get::<_, String>(19),
                               s.get::<_, String>(4).as_str(), s.get(5)),
        })));
    }
    let toplam = satirlar.len();
    let boyut = f.boyut.clamp(1, 500);
    let sayfa = f.sayfa.max(1);
    let suzgec_var = tur_var || teblig_var || mazbata_var;
    let dilim: Vec<Value> = satirlar
        .into_iter()
        .skip((sayfa - 1) * boyut)
        .take(boyut)
        .map(|(bag, mut satir)| {
            // Borçlunun bütün tebligatları; süzgeç varsa uyanlar işaretlenir.
            let liste: Vec<Value> = bag
                .and_then(|b| tebligatlar.get(&b))
                .unwrap_or(&bos)
                .iter()
                .map(|t| {
                    let mut v = t.veri.clone();
                    v["uyan"] = json!(
                        suzgec_var && metin_uyar(&f.tur, &t.tur) && metin_uyar(&f.teblig, &t.ptt) && mazbata_uyar(&f.mazbata, t.mazbata)
                    );
                    v
                })
                .collect();
            satir["tebligatlar"] = Value::Array(liste);
            satir
        })
        .collect();
    let ayni = |d: &str| d.to_string();
    let mut yillar = secenek_listesi(say_yil, &ayni);
    yillar.sort_by(|a, b| b["deger"].as_str().cmp(&a["deger"].as_str()));
    Ok(json!({
        "toplam": toplam, "dosya_sayisi": dosyalar.len(), "sayfa": sayfa, "boyut": boyut, "satirlar": dilim,
        "secenek_siniri": SECENEK_SINIRI,
        "secenekler": {
            "birim": secenek_sinirli(say_birim, &ayni, SECENEK_SINIRI),
            "alacakli": secenek_sinirli(say_alacakli, &ayni, SECENEK_SINIRI),
            "borclu": secenek_sinirli(say_borclu, &ayni, SECENEK_SINIRI),
            "yil": yillar,
            "dosya_tur": secenek_listesi(say_dosya_tur, &ayni),
            "tur": secenek_listesi(say_tur, &|d| if d.is_empty() { "(türü yok)".to_string() } else { d.to_string() }),
            "teblig": secenek_listesi(say_teblig, &|d| if d.is_empty() { "Henüz sorgulanmadı".to_string() } else { d.to_string() }),
            "mazbata": secenek_listesi(say_mazbata, &|d| if d == "var" { "Döndü" } else { "Dönmedi" }.to_string()),
            "kesinlesme": secenek_listesi(say_kes, &|d| if d == "-" { "Henüz hesaplanmadı".to_string() } else { KesinlesmeDurumu::etiket_bul(d) }),
        },
    }))
}

/// Dosya penceresi: künye, takip türü ve uygulanan itiraz süresi, taraflar (borçlularda kesinleşme
/// / tebliğ / takip aşaması), tebligatlar. Dosya yoksa `None`.
pub fn dosya_ayrintisi<C: GenericClient>(c: &mut C, dosya: i64) -> Result<Option<Value>, String> {
    let Some(d) = c
        .query_opt(
            "SELECT b.ad, d.dosya_no, d.durum, d.durum_kod, d.tur, to_char(d.acilis_tarihi, 'YYYY-MM-DD'), d.yargi_turu,
                    d.islem_durumu, d.islem_durumu_notu, d.bekleme_bitis_tarihi, to_char(d.son_senkron, 'YYYY-MM-DD HH24:MI'),
                    i.takibin_turu, i.takibin_yolu, i.takibin_sekli, i.alacak_kalemi_toplam::text, i.vekalet_ucreti::text,
                    i.tahsil_harci::text, b.birim_id, b.turu3, d.tur_kod
               FROM dosya d JOIN birim b ON b.id = d.birim_id LEFT JOIN icra_takip_detay i ON i.dosya_id = d.id
              WHERE d.id = $1",
            &[&dosya],
        )
        .map_err(hata)?
    else {
        return Ok(None);
    };
    let kod = |i: usize| d.get::<_, Option<String>>(i).unwrap_or_default();
    let kurallar = veri::kesinlesme_kurallari(c)?;
    let kural = veri::kesinlesme_kurali_sec(&kurallar, &kod(11), &kod(12), &kod(13));
    let islem: String = d.get(7);
    let kunye = json!({
        "id": dosya, "birim": d.get::<_, String>(0), "dosya_no": d.get::<_, String>(1), "durum": d.get::<_, String>(2),
        "acik": d.get::<_, i32>(3) == 0, "tur": d.get::<_, String>(4), "acilis_tarihi": d.get::<_, Option<String>>(5),
        "yargi_turu": veri::YargiTuru::coz(d.get(6)).map(|y| y.etiket()),
        "islem_durumu": islem, "islem_durumu_etiket": IslemDurumu::etiket_bul(&islem),
        "islem_durumu_notu": d.get::<_, String>(8), "bekleme_bitis": gun(d.get(9)), "son_guncelleme": d.get::<_, String>(10),
        "uyap": uyap_karti(&d.get::<_, String>(17), &d.get::<_, String>(0), &d.get::<_, String>(1), d.get(19),
                           &d.get::<_, String>(4), &d.get::<_, String>(18), &d.get::<_, String>(2), d.get(3)),
    });
    let takip = json!({
        "okundu": d.get::<_, Option<String>>(11).is_some(), "turu": kod(11), "yolu": kod(12), "sekli": kod(13),
        "alacak_toplam": d.get::<_, Option<String>>(14), "vekalet_ucreti": d.get::<_, Option<String>>(15),
        "tahsil_harci": d.get::<_, Option<String>>(16),
        "itiraz_suresi_gun": kural.and_then(|k| k.itiraz_suresi_gun), "kural": kural.map(|k| k.ad.clone()),
    });

    let sql = format!(
        "SELECT dt.id, dt.rol, {ad}, t.tur, t.tckn, t.mersis_no, t.vergi_no, dt.uyapta_gorulmedi IS NOT NULL,
                dt.takip_asamasi, dt.takip_asamasi_tarihi, dt.kesinlesme_durumu, dt.kesinlesme_tarihi,
                dt.tebligat_durumu, dt.tebligat_durum_tarihi, dt.tebligat_sekli, dt.itiraz_edildi_mi, dt.itiraz_tarihi,
                COALESCE((SELECT string_agg(btrim(v.ad || ' ' || v.soyad), ', ' ORDER BY v.id) FROM dosya_taraf_vekiller dv
                           JOIN vekil v ON v.id = dv.vekil_id WHERE dv.dosyataraf_id = dt.id), '')
           FROM dosya_taraf dt JOIN taraf t ON t.id = dt.taraf_id WHERE dt.dosya_id = $1 ORDER BY dt.sira, dt.id",
        ad = ad_sql("t")
    );
    let mut taraflar = Vec::new();
    for s in c.query(&sql, &[&dosya]).map_err(hata)? {
        let rol: String = s.get(1);
        let (asama, asama_t): (String, Option<NaiveDate>) = (s.get(8), s.get(9));
        let (kes, kes_t): (String, Option<NaiveDate>) = (s.get(10), s.get(11));
        let (tebl, tebl_t, tebl_s): (String, Option<NaiveDate>, String) = (s.get(12), s.get(13), s.get(14));
        let kimlik = [s.get::<_, Option<String>>(4), s.get::<_, Option<String>>(5), Some(s.get::<_, String>(6))]
            .into_iter()
            .flatten()
            .find(|k| !k.is_empty());
        taraflar.push(json!({
            "bag": s.get::<_, i64>(0), "rol": rol, "rol_etiket": Rol::etiket_bul(&rol), "ad": s.get::<_, String>(2),
            "tuzel": s.get::<_, String>(3) == "tuzel", "kimlik": kimlik, "uyapta_gorulmedi": s.get::<_, bool>(7),
            "vekiller": s.get::<_, String>(17),
            "asama": asama, "asama_metni": durum::takip_asamasi_metni(&asama, asama_t),
            "kesinlesme_durumu": kes, "kesinlesme": durum::kesinlesme_metni(&kes, kes_t),
            "tebligat": durum::tebligat_metni(&tebl, tebl_t, &tebl_s),
            "itiraz": s.get::<_, bool>(15), "itiraz_tarihi": gun(s.get(16)),
        }));
    }

    let sql = format!(
        "SELECT tb.id, tb.birim_evrak_no, tb.tebligat_turu, tb.evrak_aciklama, tb.evrak_tarihi, tb.barkod,
                tb.elektronik_tebligat, tb.ptt_durumu, tb.son_islem_tarihi, tb.teslim_sekli, tb.tebligat_mazbatasi_var,
                tb.tebligat_mazbatasi_aciklama, tb.yeniden_teblig_talep_edildi, tb.borclu_id, {ad},
                (SELECT count(*) FROM ptt_hareket h WHERE h.tebligat_id = tb.id)
           FROM tebligat_barkod tb LEFT JOIN dosya_taraf dt ON dt.id = tb.borclu_id LEFT JOIN taraf t ON t.id = dt.taraf_id
          WHERE tb.dosya_id = $1 ORDER BY tb.evrak_gunu DESC NULLS LAST, tb.birim_evrak_no DESC",
        ad = ad_sql("t")
    );
    let mut tebligatlar = Vec::new();
    for s in c.query(&sql, &[&dosya]).map_err(hata)? {
        let aciklama: String = s.get(3);
        let borclu: Option<i64> = s.get(13);
        tebligatlar.push(json!({
            "id": s.get::<_, i64>(0), "evrak_no": s.get::<_, i32>(1), "tur": s.get::<_, String>(2),
            "muhatap": teb::tebligat_muhatabi(&aciklama).0, "aciklama": aciklama, "evrak_tarihi": s.get::<_, String>(4),
            "barkod": s.get::<_, String>(5), "elektronik": s.get::<_, bool>(6), "ptt_durumu": s.get::<_, String>(7),
            "son_islem_tarihi": s.get::<_, String>(8), "teslim_sekli": s.get::<_, String>(9),
            "mazbata_var": s.get::<_, bool>(10), "mazbata": s.get::<_, String>(11),
            "yeniden_teblig_talebi": s.get::<_, bool>(12), "borclu_bag": borclu,
            "borclu": borclu.map(|_| s.get::<_, String>(14)), "hareket_sayisi": s.get::<_, i64>(15),
        }));
    }
    Ok(Some(json!({"kunye": kunye, "takip": takip, "taraflar": taraflar, "tebligatlar": tebligatlar})))
}

/// Dosyanın durum geçmişi, yeniden eskiye.
pub fn gecmis<C: GenericClient>(c: &mut C, dosya: i64) -> Result<Value, String> {
    let satirlar = c
        .query(
            "SELECT to_char(zaman, 'YYYY-MM-DD HH24:MI'), tur, taraf_adi, eski, yeni, olay_tarihi, kaynak, aciklama
               FROM durum_olayi WHERE dosya_id = $1 ORDER BY zaman DESC, id DESC",
            &[&dosya],
        )
        .map_err(hata)?;
    Ok(Value::Array(
        satirlar
            .iter()
            .map(|s| {
                let (tur, kaynak): (String, String) = (s.get(1), s.get(6));
                json!({
                    "zaman": s.get::<_, String>(0), "tur": tur, "tur_etiket": OlayTur::etiket_bul(&tur),
                    "taraf": s.get::<_, String>(2), "eski": s.get::<_, String>(3), "yeni": s.get::<_, String>(4),
                    "olay_tarihi": gun(s.get(5)), "kaynak": kaynak, "kaynak_etiket": OlayKaynak::etiket_bul(&kaynak),
                    "aciklama": s.get::<_, String>(7),
                })
            })
            .collect(),
    ))
}

/// Bir tebligatın PTT hareketleri, sırasıyla.
pub fn ptt_hareketleri<C: GenericClient>(c: &mut C, tebligat: i64) -> Result<Value, String> {
    let satirlar = c
        .query(
            "SELECT to_char(zaman, 'YYYY-MM-DD HH24:MI'), aciklama, yer, islem_detay FROM ptt_hareket
              WHERE tebligat_id = $1 ORDER BY sira",
            &[&tebligat],
        )
        .map_err(hata)?;
    Ok(Value::Array(
        satirlar
            .iter()
            .map(|s| {
                json!({"zaman": s.get::<_, Option<String>>(0), "aciklama": s.get::<_, String>(1),
                       "yer": s.get::<_, String>(2), "detay": s.get::<_, String>(3)})
            })
            .collect(),
    ))
}

// ── Kesinleşme süreleri ─────────────────────────────────────────────────────────────────────────

/// Kesinleşme süreleri ve veritabanındaki icra dosyalarının takip kodu dağılımı (kural girerken
/// hangi kodun kaç dosyada olduğu görülsün); takip türü hiç okunmamış dosya sayısı ayrıca.
pub fn kurallar<C: GenericClient>(c: &mut C) -> Result<Value, String> {
    let liste: Vec<Value> = veri::kesinlesme_kurallari(c)?
        .iter()
        .map(|k| {
            json!({"id": k.id, "takibin_turu": k.takibin_turu, "takibin_yolu": k.takibin_yolu, "takibin_sekli": k.takibin_sekli,
                   "ad": k.ad, "gun": k.itiraz_suresi_gun, "aktif": k.aktif, "aciklama": k.aciklama})
        })
        .collect();
    let dagilim: Vec<Value> = c
        .query(
            "SELECT i.takibin_turu, i.takibin_yolu, i.takibin_sekli, count(*),
                    (SELECT b.ad || ' ' || d2.dosya_no FROM dosya d2 JOIN birim b ON b.id = d2.birim_id WHERE d2.id = min(d.id))
               FROM icra_takip_detay i JOIN dosya d ON d.id = i.dosya_id WHERE d.yargi_turu = 2
              GROUP BY 1, 2, 3 ORDER BY 4 DESC",
            &[],
        )
        .map_err(hata)?
        .iter()
        .map(|s| {
            json!({"turu": s.get::<_, String>(0), "yolu": s.get::<_, String>(1), "sekli": s.get::<_, String>(2),
                   "adet": s.get::<_, i64>(3), "ornek": s.get::<_, Option<String>>(4)})
        })
        .collect();
    let bilinmeyen: i64 = c
        .query_one(
            "SELECT count(*) FROM dosya d WHERE d.yargi_turu = 2 AND NOT EXISTS (SELECT 1 FROM icra_takip_detay i WHERE i.dosya_id = d.id)",
            &[],
        )
        .map_err(hata)?
        .get(0);
    Ok(json!({"kurallar": liste, "dagilim": dagilim, "takip_turu_bilinmeyen": bilinmeyen}))
}

/// Süre ekler ya da günceller. `gun` boşsa o türde kesinleşme hesaplanmaz. Aynı takip kodu için
/// ikinci kural açılmaz. Döner: kuralın kimliği.
#[allow(clippy::too_many_arguments)]
pub fn kural_kaydet<C: GenericClient>(
    c: &mut C,
    id: Option<i64>,
    turu: &str,
    yolu: &str,
    sekli: &str,
    ad: &str,
    gun: Option<i64>,
    aktif: bool,
    aciklama: &str,
) -> Result<i64, String> {
    let (turu, yolu, sekli) = (turu.trim(), yolu.trim(), sekli.trim());
    if [turu, yolu, sekli].iter().any(|k| k.chars().count() > 8) {
        return Err("Takip kodu en çok 8 karakter olabilir.".into());
    }
    let gun = match gun {
        Some(g) if !(1..=365).contains(&g) => return Err("Süre 1–365 gün arasında olmalı.".into()),
        Some(g) => Some(g as i16),
        None => None,
    };
    let cakisan = c
        .query_opt(
            "SELECT 1 FROM kesinlesme_kurali WHERE takibin_turu = $1 AND takibin_yolu = $2 AND takibin_sekli = $3
                AND ($4::bigint IS NULL OR id <> $4)",
            &[&turu, &yolu, &sekli, &id],
        )
        .map_err(hata)?;
    if cakisan.is_some() {
        return Err("Bu takip kodu için zaten bir süre var; onu düzenleyin.".into());
    }
    let ad = veri::kirp(ad.trim(), 160);
    match id {
        Some(id) => {
            let n = c
                .execute(
                    "UPDATE kesinlesme_kurali SET takibin_turu = $2, takibin_yolu = $3, takibin_sekli = $4, ad = $5,
                            itiraz_suresi_gun = $6, aktif = $7, aciklama = $8, guncelleme = now() WHERE id = $1",
                    &[&id, &turu, &yolu, &sekli, &ad, &gun, &aktif, &aciklama],
                )
                .map_err(hata)?;
            if n == 0 { Err("Kayıt bulunamadı.".into()) } else { Ok(id) }
        }
        None => Ok(c
            .query_one(
                "INSERT INTO kesinlesme_kurali (takibin_turu, takibin_yolu, takibin_sekli, ad, itiraz_suresi_gun, aktif, aciklama)
                 VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
                &[&turu, &yolu, &sekli, &ad, &gun, &aktif, &aciklama],
            )
            .map_err(hata)?
            .get(0)),
    }
}

pub fn kural_sil<C: GenericClient>(c: &mut C, id: i64) -> Result<(), String> {
    c.execute("DELETE FROM kesinlesme_kurali WHERE id = $1", &[&id]).map_err(hata)?;
    Ok(())
}

// ── Resmî tatiller ──────────────────────────────────────────────────────────────────────────────

pub fn tatiller<C: GenericClient>(c: &mut C) -> Result<Value, String> {
    let satirlar = c.query("SELECT tarih, aciklama FROM resmi_tatil ORDER BY tarih", &[]).map_err(hata)?;
    Ok(Value::Array(satirlar.iter().map(|s| json!({"tarih": gun(s.get(0)), "aciklama": s.get::<_, String>(1)})).collect()))
}

pub fn tatil_kaydet<C: GenericClient>(c: &mut C, tarih: NaiveDate, aciklama: &str) -> Result<(), String> {
    c.execute(
        "INSERT INTO resmi_tatil (tarih, aciklama) VALUES ($1, $2) ON CONFLICT (tarih) DO UPDATE SET aciklama = EXCLUDED.aciklama",
        &[&tarih, &veri::kirp(aciklama.trim(), 120)],
    )
    .map_err(hata)?;
    Ok(())
}

pub fn tatil_sil<C: GenericClient>(c: &mut C, tarih: NaiveDate) -> Result<(), String> {
    c.execute("DELETE FROM resmi_tatil WHERE tarih = $1", &[&tarih]).map_err(hata)?;
    Ok(())
}

// ── Elle girilenler ─────────────────────────────────────────────────────────────────────────────

fn bagin_dosyasi<C: GenericClient>(c: &mut C, bag: i64) -> Result<i64, String> {
    c.query_opt("SELECT dosya_id FROM dosya_taraf WHERE id = $1 AND rol = 'borclu'", &[&bag])
        .map_err(hata)?
        .map(|s| s.get(0))
        .ok_or_else(|| "Borçlu kaydı bulunamadı.".to_string())
}

/// Elle itiraz işareti (UYAP'tan otomatik anlaşılamıyor). Ardından borçlunun durumu yeniden
/// hesaplanır; değişiklik geçmişe yazılır.
pub fn itiraz_ata<C: GenericClient>(c: &mut C, bag: i64, edildi: bool, tarih: Option<NaiveDate>, bugun: NaiveDate) -> Result<(), String> {
    let dosya = bagin_dosyasi(c, bag)?;
    let tarih = if edildi { tarih } else { None };
    c.execute("UPDATE dosya_taraf SET itiraz_edildi_mi = $2, itiraz_tarihi = $3 WHERE id = $1", &[&bag, &edildi, &tarih]).map_err(hata)?;
    durum::durumlari_guncelle(c, Some(dosya), bugun, &|_| {}).map(|_| ())
}

/// Elle girilen kesinleşme değeri: Konkordato, Vefat, Vasi (hesap bunları ezmez) ya da boş
/// (yeniden hesaba bırak).
pub fn kesinlesme_elle<C: GenericClient>(c: &mut C, bag: i64, yeni: &str, bugun: NaiveDate) -> Result<(), String> {
    let izinli = [KesinlesmeDurumu::Konkordato.kod(), KesinlesmeDurumu::Vefat.kod(), KesinlesmeDurumu::Vasi.kod(), ""];
    if !izinli.contains(&yeni) {
        return Err("Elle yalnız Konkordato, Vefat ya da Vasi girilebilir.".into());
    }
    let dosya = bagin_dosyasi(c, bag)?;
    let mut tx = c.transaction().map_err(hata)?;
    let s = tx.query_one("SELECT kesinlesme_durumu, kesinlesme_tarihi FROM dosya_taraf WHERE id = $1 FOR UPDATE", &[&bag]).map_err(hata)?;
    let (eski, eski_t): (String, Option<NaiveDate>) = (s.get(0), s.get(1));
    if eski != yeni {
        tx.execute("UPDATE dosya_taraf SET kesinlesme_durumu = $2, kesinlesme_tarihi = NULL WHERE id = $1", &[&bag, &yeni]).map_err(hata)?;
        if !yeni.is_empty() {
            durum_olayi_yaz(
                &mut tx,
                &YeniOlay {
                    dosya,
                    dosya_taraf: Some(bag),
                    tur: OlayTur::Kesinlesme,
                    eski: &durum::kesinlesme_metni(&eski, eski_t),
                    yeni: &durum::kesinlesme_metni(yeni, None),
                    olay_tarihi: None,
                    kaynak: OlayKaynak::Elle,
                    aciklama: "",
                },
            )?;
        }
    }
    durum::durumlari_guncelle(&mut tx, Some(dosya), bugun, &|_| {})?;
    tx.commit().map_err(hata)
}

// ── HTTP ────────────────────────────────────────────────────────────────────────────────────────

type Yanit = (u16, HashMap<String, String>, Vec<u8>);

fn yanit(durum: u16, govde: Value) -> Yanit {
    let mut h = HashMap::new();
    h.insert("Content-Type".to_string(), "application/json; charset=utf-8".to_string());
    h.insert("Cache-Control".to_string(), "no-store".to_string());
    (durum, h, serde_json::to_vec(&govde).unwrap_or_else(|_| b"{}".to_vec()))
}

fn tam(v: &Value, anahtar: &str) -> Result<i64, String> {
    teb::tam(v, anahtar).ok_or_else(|| format!("'{anahtar}' eksik."))
}

fn sorgu_tam(q: &HashMap<String, String>, anahtar: &str) -> Result<i64, String> {
    q.get(anahtar).and_then(|v| v.trim().parse().ok()).ok_or_else(|| format!("'{anahtar}' eksik."))
}

/// İsteği veritabanı bağlantısıyla işler. `Ok(None)` = böyle bir uç yok.
fn dagit(c: &mut postgres::Client, yontem: &str, uc: &str, q: &HashMap<String, String>, g: &Value) -> Result<Option<Value>, String> {
    let bugun = chrono::Local::now().date_naive();
    let metin = |a: &str| teb::metin(g, a);
    let evet = |a: &str| g.get(a).and_then(Value::as_bool).unwrap_or(false);
    Ok(Some(match (yontem, uc) {
        ("GET", "ozet") => ozet(c)?,
        ("GET", "borclular") => borclu_listesi(
            c,
            &Filtre {
                q: q.get("q").cloned().unwrap_or_default(),
                asama: q.get("asama").cloned().unwrap_or_default(),
                // `acik=1` eski istemciler için (yalnız açık); yeni istemci `durum` gönderir.
                durum: q.get("durum").cloned().unwrap_or_else(|| if q.get("acik").is_some_and(|v| v == "1") { "acik".into() } else { String::new() }),
                birim: q.get("birim").cloned().unwrap_or_default(),
                alacakli: q.get("alacakli").cloned().unwrap_or_default(),
                borclu: q.get("borclu").cloned().unwrap_or_default(),
                yil: q.get("yil").cloned().unwrap_or_default(),
                dosya_tur: q.get("dosya_tur").cloned().unwrap_or_default(),
                acilis_bas: q.get("acilis_bas").and_then(|v| veri::tarih::gun_coz(v)),
                acilis_son: q.get("acilis_son").and_then(|v| veri::tarih::gun_coz(v)),
                tur: q.get("tur").cloned().unwrap_or_default(),
                teblig: q.get("teblig").cloned().unwrap_or_default(),
                mazbata: q.get("mazbata").cloned().unwrap_or_default(),
                kesinlesme: q.get("kesinlesme").cloned().unwrap_or_default(),
                sayfa: q.get("sayfa").and_then(|v| v.parse().ok()).unwrap_or(1),
                boyut: q.get("boyut").and_then(|v| v.parse().ok()).unwrap_or(50),
            },
        )?,
        ("GET", "dosya") => dosya_ayrintisi(c, sorgu_tam(q, "id")?)?.ok_or("Dosya bulunamadı.")?,
        ("GET", "gecmis") => gecmis(c, sorgu_tam(q, "dosya")?)?,
        ("GET", "ptt") => ptt_hareketleri(c, sorgu_tam(q, "tebligat")?)?,
        ("GET", "kurallar") => kurallar(c)?,
        ("GET", "tatiller") => tatiller(c)?,
        ("POST", "kural") => {
            let id = kural_kaydet(
                c,
                teb::tam(g, "id"),
                &metin("takibin_turu"),
                &metin("takibin_yolu"),
                &metin("takibin_sekli"),
                &metin("ad"),
                teb::tam(g, "gun"),
                g.get("aktif").and_then(Value::as_bool).unwrap_or(true),
                &metin("aciklama"),
            )?;
            let s = durum::durumlari_guncelle(c, None, bugun, &|_| {})?;
            json!({"id": id, "guncellenen": s.guncellenen})
        }
        ("POST", "kural_sil") => {
            kural_sil(c, tam(g, "id")?)?;
            let s = durum::durumlari_guncelle(c, None, bugun, &|_| {})?;
            json!({"guncellenen": s.guncellenen})
        }
        ("POST", "tatil") => {
            tatil_kaydet(c, gun_coz(&g["tarih"])?.ok_or("Tarih eksik.")?, &metin("aciklama"))?;
            let s = durum::durumlari_guncelle(c, None, bugun, &|_| {})?;
            json!({"guncellenen": s.guncellenen})
        }
        ("POST", "tatil_sil") => {
            tatil_sil(c, gun_coz(&g["tarih"])?.ok_or("Tarih eksik.")?)?;
            let s = durum::durumlari_guncelle(c, None, bugun, &|_| {})?;
            json!({"guncellenen": s.guncellenen})
        }
        ("POST", "itiraz") => {
            itiraz_ata(c, tam(g, "bag")?, evet("edildi"), gun_coz(&g["tarih"])?, bugun)?;
            Value::Null
        }
        ("POST", "kesinlesme_elle") => {
            kesinlesme_elle(c, tam(g, "bag")?, &metin("durum"), bugun)?;
            Value::Null
        }
        ("POST", "islem_durumu") => {
            let d = IslemDurumu::coz(&metin("durum")).ok_or("Geçersiz işlem durumu.")?;
            veri::islem_durumu_ata(c, tam(g, "dosya")?, d, &metin("notu"), gun_coz(&g["bekleme_bitis"])?)?;
            Value::Null
        }
        ("POST", "hesapla") => {
            let s = durum::durumlari_guncelle(c, teb::tam(g, "dosya"), bugun, &|_| {})?;
            json!({"guncellenen": s.guncellenen, "olay": s.olay})
        }
        _ => return Ok(None),
    }))
}

/// `/__cu__/veri/…` isteği. `capraz_site`: tarayıcı isteğin başka bir siteden geldiğini bildirdi
/// (yazma istekleri o zaman reddedilir).
pub async fn handle(method: &str, path: &str, query: &str, body: &[u8], capraz_site: bool) -> Yanit {
    let yontem = method.trim().to_uppercase();
    let uc = path.trim_start_matches('/').trim_start_matches(ON_EK).trim_matches('/').to_string();
    if yontem != "GET" && capraz_site {
        return yanit(403, json!({"ok": false, "error": "Bu istek başka bir siteden geldi; reddedildi."}));
    }
    if !crate::veritabani::hazir() {
        return yanit(503, json!({"ok": false, "kod": "E-402", "error": "Büro veritabanı hazır değil."}));
    }
    let q: HashMap<String, String> = url::form_urlencoded::parse(query.as_bytes()).into_owned().collect();
    let g: Value = if body.is_empty() {
        json!({})
    } else {
        match serde_json::from_slice(body) {
            Ok(v) => v,
            Err(_) => return yanit(400, json!({"ok": false, "error": "İstek gövdesi JSON değil."})),
        }
    };
    let sonuc = tokio::task::spawn_blocking(move || {
        let mut c = crate::veritabani::baglanti()?;
        dagit(&mut c, &yontem, &uc, &q, &g)
    })
    .await;
    match sonuc {
        Ok(Ok(Some(veri))) => yanit(200, json!({"ok": true, "veri": veri})),
        Ok(Ok(None)) => yanit(404, json!({"ok": false, "error": "Böyle bir uç yok."})),
        Ok(Err(e)) => {
            let sunucu_hatasi = e.starts_with("veritabanı") || e.contains("büro veritabanı");
            yanit(if sunucu_hatasi { 500 } else { 400 }, json!({"ok": false, "error": e}))
        }
        Err(e) => yanit(500, json!({"ok": false, "error": format!("İşlem tamamlanamadı: {e}")})),
    }
}

// ── Testler ─────────────────────────────────────────────────────────────────────────────────────

/// Gerçek veritabanı üzerinde senaryo; `veritabani::tests::uctan_uca` içinden çağrılır.
#[cfg(test)]
pub(crate) fn veritabani_senaryosu(c: &mut postgres::Client) {
    use crate::icra_kayit as kayit;
    use crate::icra_veri::{dosya_kunyesi_kaydet, ham_tarihleri_turet, YargiTuru};
    let g = |y, a, d| NaiveDate::from_ymd_opt(y, a, d).unwrap();
    let sessiz = |_: &str| {};
    let bos = HashMap::new();
    let al = |c: &mut postgres::Client, uc: &str, q: &[(&str, &str)]| -> Value {
        let q: HashMap<String, String> = q.iter().map(|(a, b)| (a.to_string(), b.to_string())).collect();
        dagit(c, "GET", uc, &q, &json!({})).unwrap().unwrap()
    };
    let gonder = |c: &mut postgres::Client, uc: &str, govde: Value| dagit(c, "POST", uc, &HashMap::new(), &govde);

    let kunye = |no: &str| json!({"dosyaId": "x", "dosyaNo": no, "dosyaDurumKod": 0, "dosyaDurum": "Açık", "dosyaTurKod": 35,
                                   "dosyaTur": "İcra Dosyası", "birimAdi": "İzmir 1. İcra Dairesi", "birimId": 1001});
    let (d1, _) = dosya_kunyesi_kaydet(c, &kunye("2026/5"), Some(YargiTuru::Icra.kod())).unwrap();
    let (d2, _) = dosya_kunyesi_kaydet(c, &kunye("2026/6"), None).unwrap();
    kayit::dosya_taraf_kaydet(
        c,
        d1,
        &[json!({"adi": "X BANKASI A.Ş.", "rol": "Alacaklı", "kisiKurum": "Kurum", "vekil": "[AYŞE KAYA]"}),
          json!({"adi": "AHMET YILMAZ", "rol": "Borçlu", "kisiKurum": "Kişi"}),
          json!({"adi": "IŞIL ÇİÇEK", "rol": "Borçlu", "kisiKurum": "Kişi"})],
        &sessiz,
    )
    .unwrap();
    kayit::dosya_taraf_kaydet(c, d2, &[json!({"adi": "MEHMET DEMİR", "rol": "Borçlu", "kisiKurum": "Kişi"})], &sessiz).unwrap();
    kayit::dosya_ayrinti_kaydet(c, d1, &json!({"takibinTuru": "0", "takibinYolu": "1", "takibinSekli": "7", "alacakKalemToplamTutar": 3762.9})).unwrap();
    let teb_id: i64 = c
        .query_one(
            "INSERT INTO tebligat_barkod (dosya_id, birim_evrak_no, tebligat_turu, evrak_tarihi, evrak_aciklama, barkod, ptt_durumu,
                                          son_islem_tarihi, teslim_sekli)
             VALUES ($1, 10, 'Ödeme Emri', '01/10/2026', 'Normal Tebligat[BORÇLU AHMET YILMAZ]', '4007663523293', 'TESLİM EDİLDİ',
                     '05/10/2026 14:30', 'Muhataba bizzat teslim') RETURNING id",
            &[&d1],
        )
        .unwrap()
        .get(0);
    ham_tarihleri_turet(c, "tebligat_barkod", teb_id).unwrap();
    veri::ptt_hareketlerini_yaz(c, teb_id, &json!({"hareketDongu": [{"tarih": "05/10/2026", "saat": "14:30", "aciklama": "TESLİM EDİLDİ", "il": "İZMİR"}]})).unwrap();

    // Süreler: doğrulama, çakışma, dağılım
    assert!(gonder(c, "kural", json!({"gun": 0})).is_err());
    assert!(gonder(c, "kural", json!({"gun": 400})).is_err());
    let v = gonder(c, "kural", json!({"ad": "Varsayılan", "gun": 7})).unwrap().unwrap();
    let varsayilan = v["id"].as_i64().unwrap();
    assert_eq!(v["guncellenen"], 3, "kural girilince bütün borçlular hesaplanır");
    assert!(gonder(c, "kural", json!({"ad": "çift", "gun": 5})).unwrap_err().contains("zaten"));
    let ozel = gonder(c, "kural", json!({"takibin_turu": "0", "takibin_yolu": " 1 ", "ad": "İlamsız", "gun": "10"})).unwrap().unwrap()["id"].as_i64().unwrap();
    assert!(gonder(c, "kural", json!({"id": varsayilan, "takibin_turu": "0", "takibin_yolu": "1", "gun": 7})).is_err(), "düzenlerken de çakışma denetlenir");
    gonder(c, "kural", json!({"id": varsayilan, "ad": "Varsayılan (7 gün)", "gun": 7})).unwrap();
    let k = al(c, "kurallar", &[]);
    assert_eq!(k["kurallar"].as_array().unwrap().len(), 2);
    assert_eq!(k["kurallar"][0]["ad"], "Varsayılan (7 gün)");
    assert_eq!(k["dagilim"][0], json!({"turu": "0", "yolu": "1", "sekli": "7", "adet": 1, "ornek": "İzmir 1. İcra Dairesi 2026/5"}));
    assert_eq!(k["takip_turu_bilinmeyen"], 1);

    // Dosya penceresi: en özel kural uygulanır
    let d = al(c, "dosya", &[("id", &d1.to_string())]);
    assert_eq!(d["kunye"]["dosya_no"], "2026/5");
    assert_eq!((&d["takip"]["itiraz_suresi_gun"], &d["takip"]["kural"], &d["takip"]["alacak_toplam"]), (&json!(10), &json!("İlamsız"), &json!("3762.90")));
    let taraflar = d["taraflar"].as_array().unwrap();
    assert_eq!(taraflar.len(), 3);
    assert_eq!((&taraflar[0]["rol_etiket"], &taraflar[0]["vekiller"], &taraflar[0]["tuzel"]), (&json!("Alacaklı"), &json!("AYŞE KAYA"), &json!(true)));
    assert_eq!(taraflar[1]["ad"], "AHMET YILMAZ");
    assert_eq!(d["tebligatlar"][0]["muhatap"], "BORÇLU AHMET YILMAZ");
    assert_eq!((&d["tebligatlar"][0]["borclu"], &d["tebligatlar"][0]["hareket_sayisi"]), (&json!("AHMET YILMAZ"), &json!(1)));
    assert_eq!(al(c, "ptt", &[("tebligat", &teb_id.to_string())])[0]["yer"], "İZMİR");
    assert!(dagit(c, "GET", "dosya", &bos, &json!({})).is_err(), "id eksik");
    assert!(dagit(c, "GET", "olmayan", &bos, &json!({})).unwrap().is_none());

    // Hesap: 10 günlük süreyle 05.10 tebliğ → 15.10 son gün, 16.10 kesinleşme
    durum::durumlari_guncelle(c, None, g(2026, 10, 6), &sessiz).unwrap();
    let ahmet = al(c, "dosya", &[("id", &d1.to_string())])["taraflar"][1].clone();
    assert_eq!(ahmet["kesinlesme"], "Kesinleşme Bekliyor (16.10.2026)");
    assert_eq!(ahmet["tebligat"], "Tebliğ Edildi (05.10.2026) — Muhataba bizzat teslim");
    assert_eq!(ahmet["asama_metni"], "Tebliğ (05.10.2026)");
    // Tatil eklenince gün kayar (16.10 Cuma tatil → 19.10 Pazartesi)
    gonder(c, "tatil", json!({"tarih": "16.10.2026", "aciklama": "Deneme tatili"})).unwrap();
    assert_eq!(al(c, "tatiller", &[]), json!([{"tarih": "2026-10-16", "aciklama": "Deneme tatili"}]));
    assert!(gonder(c, "tatil", json!({"tarih": "olmaz"})).is_err());
    let ahmet_bag = ahmet["bag"].as_i64().unwrap();
    assert_eq!(al(c, "dosya", &[("id", &d1.to_string())])["taraflar"][1]["kesinlesme"], "Kesinleşme Bekliyor (19.10.2026)");
    gonder(c, "tatil_sil", json!({"tarih": "2026-10-16"})).unwrap();
    assert_eq!(al(c, "tatiller", &[]), json!([]));

    // Özet ve liste
    let o = al(c, "ozet", &[]);
    assert_eq!((&o["icra_dosya"], &o["borclu"], &o["tebligat"]), (&json!(2), &json!(3), &json!(1)));
    assert_eq!(o["birimler"], json!([{"ad": "İzmir 1. İcra Dairesi", "birim_id": "1001", "acik": 2}]));
    let adet = |o: &Value, kod: &str| o["asamalar"].as_array().unwrap().iter().find(|a| a["kod"] == kod).map(|a| a["adet"].clone());
    assert_eq!((adet(&o, "teblig"), adet(&o, "takip_talebi")), (Some(json!(1)), Some(json!(2))));
    let l = al(c, "borclular", &[]);
    assert_eq!(l["toplam"], 3);
    assert_eq!((&l["satirlar"][0]["borclu"], &l["satirlar"][0]["alacakli"]), (&json!("AHMET YILMAZ"), &json!("X BANKASI A.Ş.")), "aşama günü olan önce");
    assert_eq!(al(c, "borclular", &[("asama", "teblig")])["toplam"], 1);
    assert_eq!(al(c, "borclular", &[("q", "isil cicek")])["satirlar"][0]["borclu"], "IŞIL ÇİÇEK", "Türkçe-duyarsız arama");
    assert_eq!(al(c, "borclular", &[("q", "2026/6")])["satirlar"][0]["borclu"], "MEHMET DEMİR");
    assert_eq!(al(c, "borclular", &[("q", "x bankası ahmet")])["toplam"], 1);
    assert_eq!(al(c, "borclular", &[("q", "yok böyle biri")])["toplam"], 0);
    // Borçlunun tebligatları ve başlık süzgeçleri
    let ahmet_satir = l["satirlar"][0].clone();
    assert_eq!(ahmet_satir["tebligatlar"].as_array().unwrap().len(), 1, "borçlunun bütün tebligatları satırda");
    assert_eq!((&ahmet_satir["tebligatlar"][0]["barkod"], &ahmet_satir["tebligatlar"][0]["uyan"]), (&json!("4007663523293"), &json!(false)));
    assert_eq!(l["satirlar"][1]["tebligatlar"], json!([]));
    assert_eq!(l["secenekler"]["tur"], json!([{"deger": "Ödeme Emri", "etiket": "Ödeme Emri", "adet": 1}]));
    assert_eq!(l["secenekler"]["teblig"][0]["deger"], "TESLİM EDİLDİ");
    assert_eq!(l["secenekler"]["mazbata"], json!([{"deger": "yok", "etiket": "Dönmedi", "adet": 1}]));
    let kes = l["secenekler"]["kesinlesme"].as_array().unwrap();
    assert_eq!(kes[0], json!({"deger": "-", "etiket": "Henüz hesaplanmadı", "adet": 2}));
    assert_eq!(kes[1], json!({"deger": "kesinlesme_bekliyor", "etiket": "Kesinleşme Bekliyor", "adet": 1}));
    let t = al(c, "borclular", &[("tur", "odeme")]);
    assert_eq!((&t["toplam"], &t["satirlar"][0]["tebligatlar"][0]["uyan"]), (&json!(1), &json!(true)), "yazarak: içinde geçen, Türkçe-duyarsız");
    assert_eq!(al(c, "borclular", &[("tur", "=Ödeme Emri"), ("teblig", "=TESLİM EDİLDİ")])["toplam"], 1);
    assert_eq!(al(c, "borclular", &[("tur", "=Ödeme")])["toplam"], 0, "listeden seçilen: tam eşleşme");
    assert_eq!(al(c, "borclular", &[("tur", "odeme"), ("teblig", "iade")])["toplam"], 0, "aynı tebligatta birlikte aranır");
    assert_eq!(al(c, "borclular", &[("mazbata", "yok")])["toplam"], 1);
    let m = al(c, "borclular", &[("mazbata", "var")]);
    assert_eq!(m["toplam"], 0);
    assert_eq!(m["secenekler"]["mazbata"][0]["deger"], "yok", "bir süzgecin seçenekleri kendi seçiminden etkilenmez");
    assert_eq!(m["secenekler"]["tur"], json!([]), "diğer süzgeçlerin seçenekleri daralır");
    assert_eq!(al(c, "borclular", &[("kesinlesme", "kesinlesme_bekliyor")])["satirlar"][0]["borclu"], "AHMET YILMAZ");
    let k = al(c, "borclular", &[("kesinlesme", "-")]);
    assert_eq!((&k["toplam"], &k["secenekler"]["tur"]), (&json!(2), &json!([])));
    assert_eq!(al(c, "borclular", &[("kesinlesme", "-"), ("tur", "odeme")])["toplam"], 0);
    let sayfa = al(c, "borclular", &[("boyut", "2"), ("sayfa", "2")]);
    assert_eq!((sayfa["toplam"].clone(), sayfa["satirlar"].as_array().unwrap().len()), (json!(3), 1));
    c.execute("UPDATE dosya SET durum_kod = 1 WHERE id = $1", &[&d2]).unwrap();
    assert_eq!(al(c, "borclular", &[("acik", "1")])["toplam"], 2);

    // Künye süzgeçleri: daire, alacaklı, borçlu, yıl, dosya türü, açılış tarihi, durum; borçlusuz dosya
    let (d3, _) = dosya_kunyesi_kaydet(
        c,
        &json!({"dosyaId": "y", "dosyaNo": "2025/77", "dosyaDurumKod": 1, "dosyaDurum": "Kapalı", "dosyaTurKod": 36, "dosyaTur": "MTS",
                "birimAdi": "Ankara 3. İcra Dairesi", "birimId": 2002, "dosyaAcilisTarihi": "15.03.2025"}),
        Some(YargiTuru::Icra.kod()),
    )
    .unwrap();
    kayit::dosya_taraf_kaydet(c, d3, &[json!({"adi": "Y FAKTORİNG A.Ş.", "rol": "Alacaklı", "kisiKurum": "Kurum"})], &sessiz).unwrap();
    assert_eq!(al(c, "borclular", &[("durum", "acik")])["toplam"], 2);
    let k = al(c, "borclular", &[("durum", "kapali")]);
    assert_eq!((&k["toplam"], &k["dosya_sayisi"]), (&json!(2), &json!(2)), "kapalı: MEHMET'in dosyası + borçlusuz dosya");
    let h = al(c, "borclular", &[]);
    assert_eq!((&h["toplam"], &h["dosya_sayisi"]), (&json!(4), &json!(3)));
    let bos_satir = h["satirlar"].as_array().unwrap().iter().find(|s| s["dosya"] == d3).unwrap().clone();
    assert_eq!((&bos_satir["bag"], &bos_satir["borclu"], &bos_satir["alacakli"]), (&Value::Null, &json!(""), &json!("Y FAKTORİNG A.Ş.")));
    assert_eq!((&bos_satir["acilis_tarihi"], &bos_satir["dosya_tur"], &bos_satir["tebligatlar"]), (&json!("2025-03-15"), &json!("MTS"), &json!([])));
    assert_eq!(al(c, "borclular", &[("asama", "teblig")])["toplam"], 1, "aşama seçilince borçlusuz dosya gelmez");
    // Daire: yazı = içinde geçen, Türkçe-duyarsız; `=` tam
    assert_eq!(al(c, "borclular", &[("birim", "izmir")])["toplam"], 3);
    assert_eq!(al(c, "borclular", &[("birim", "=Ankara 3. İcra Dairesi")])["toplam"], 1);
    assert_eq!(al(c, "borclular", &[("birim", "=Ankara")])["toplam"], 0);
    // Alacaklı: dosyanın alacaklılarından biri
    assert_eq!(al(c, "borclular", &[("alacakli", "x bankasi")])["toplam"], 2, "X BANKASI'nın dosyasındaki iki borçlu");
    assert_eq!(al(c, "borclular", &[("alacakli", "=Y FAKTORİNG A.Ş.")])["dosya_sayisi"], 1);
    // Borçlu
    assert_eq!(al(c, "borclular", &[("borclu", "isil")])["satirlar"][0]["borclu"], "IŞIL ÇİÇEK");
    assert_eq!(al(c, "borclular", &[("borclu", "=AHMET YILMAZ")])["toplam"], 1);
    // Yıl ve tür (kodlu: tam değer)
    assert_eq!(al(c, "borclular", &[("yil", "2025")])["toplam"], 1);
    assert_eq!(al(c, "borclular", &[("yil", "2026"), ("dosya_tur", "İcra Dosyası")])["toplam"], 3);
    assert_eq!(al(c, "borclular", &[("dosya_tur", "MTS")])["satirlar"][0]["dosya"], d3);
    // Açılış tarihi aralığı (açılışı bilinmeyen dosya aralık verilince dışarıda kalır)
    assert_eq!(al(c, "borclular", &[("acilis_bas", "2025-03-01"), ("acilis_son", "15.03.2025")])["toplam"], 1);
    assert_eq!(al(c, "borclular", &[("acilis_bas", "2025-03-16")])["toplam"], 0);
    // Seçenekler: diğer süzgeçlere göre daralır; listeden seçilen kendi kutusunu daraltmaz, yazı daraltır
    let s = al(c, "borclular", &[("birim", "=Ankara 3. İcra Dairesi")]);
    assert_eq!(s["secenekler"]["birim"].as_array().unwrap().len(), 2, "seçilen daire diğer daireleri gizlemez");
    assert_eq!(s["secenekler"]["alacakli"], json!([{"deger": "Y FAKTORİNG A.Ş.", "etiket": "Y FAKTORİNG A.Ş.", "adet": 1}]));
    assert_eq!(s["secenekler"]["borclu"], json!([]));
    assert_eq!(s["secenekler"]["yil"], json!([{"deger": "2025", "etiket": "2025", "adet": 1}]));
    let s = al(c, "borclular", &[("birim", "ankara")]);
    assert_eq!(s["secenekler"]["birim"].as_array().unwrap().len(), 1, "yazılan metin kendi seçeneklerini daraltır");
    let s = al(c, "borclular", &[]);
    assert_eq!(s["secenekler"]["yil"][0]["deger"], "2026", "yıllar yeniden eskiye");
    assert_eq!(s["secenekler"]["borclu"].as_array().unwrap().len(), 3);
    assert_eq!(s["secenekler"]["dosya_tur"][0], json!({"deger": "İcra Dosyası", "etiket": "İcra Dosyası", "adet": 3}));
    // Birden çok seçim (satır sonuyla): herhangi birine uyan
    assert_eq!(al(c, "borclular", &[("birim", "=Ankara 3. İcra Dairesi\nİzmir 1. İcra Dairesi")])["toplam"], 4);
    assert_eq!(al(c, "borclular", &[("borclu", "=AHMET YILMAZ\nIŞIL ÇİÇEK")])["toplam"], 2);
    assert_eq!(al(c, "borclular", &[("dosya_tur", "MTS\nİcra Dosyası"), ("yil", "2025\n2026")])["toplam"], 4);
    assert_eq!(al(c, "borclular", &[("yil", "2025\n2024")])["toplam"], 1);
    let s = al(c, "borclular", &[("alacakli", "=Y FAKTORİNG A.Ş.\nX BANKASI A.Ş.")]);
    assert_eq!((&s["toplam"], &s["dosya_sayisi"]), (&json!(3), &json!(2)));
    assert_eq!(s["secenekler"]["alacakli"].as_array().unwrap().len(), 2, "seçilenler kendi listesini daraltmaz");
    assert_eq!(al(c, "borclular", &[("kesinlesme", "-\nkesinlesme_bekliyor")])["toplam"], al(c, "borclular", &[])["toplam"]);
    assert_eq!(al(c, "borclular", &[("mazbata", "var\nyok")])["toplam"], 1, "mazbata süzgeci tebligatı olanlar");

    // Elle itiraz ve elle kesinleşme değeri; geçmiş
    gonder(c, "itiraz", json!({"bag": ahmet_bag, "edildi": true, "tarih": "2026-10-08"})).unwrap();
    let a = al(c, "dosya", &[("id", &d1.to_string())])["taraflar"][1].clone();
    assert_eq!((&a["kesinlesme"], &a["asama_metni"], &a["itiraz_tarihi"]), (&json!("İtiraz"), &json!("İtiraz (08.10.2026)"), &json!("2026-10-08")));
    gonder(c, "itiraz", json!({"bag": ahmet_bag, "edildi": false})).unwrap();
    assert!(gonder(c, "kesinlesme_elle", json!({"bag": ahmet_bag, "durum": "kesinlesti"})).is_err());
    gonder(c, "kesinlesme_elle", json!({"bag": ahmet_bag, "durum": "konkordato"})).unwrap();
    assert_eq!(al(c, "dosya", &[("id", &d1.to_string())])["taraflar"][1]["kesinlesme"], "Konkordato");
    gonder(c, "hesapla", json!({})).unwrap();
    assert_eq!(al(c, "dosya", &[("id", &d1.to_string())])["taraflar"][1]["kesinlesme"], "Konkordato", "hesap elle girileni ezmez");
    gonder(c, "kesinlesme_elle", json!({"bag": ahmet_bag, "durum": ""})).unwrap();
    assert!(al(c, "dosya", &[("id", &d1.to_string())])["taraflar"][1]["kesinlesme"].as_str().unwrap().starts_with("Kesinleşme Bekliyor"));
    assert!(gonder(c, "itiraz", json!({"bag": 999999, "edildi": true})).is_err());
    gonder(c, "islem_durumu", json!({"dosya": d1, "durum": "beklemede", "notu": "müvekkil bekleniyor", "bekleme_bitis": "01/12/2026"})).unwrap();
    assert!(gonder(c, "islem_durumu", json!({"dosya": d1, "durum": "olmaz"})).is_err());
    let d = al(c, "dosya", &[("id", &d1.to_string())]);
    assert_eq!((&d["kunye"]["islem_durumu_etiket"], &d["kunye"]["bekleme_bitis"]), (&json!("Beklemede"), &json!("2026-12-01")));
    let gecmis = al(c, "gecmis", &[("dosya", &d1.to_string())]);
    let gecmis = gecmis.as_array().unwrap();
    assert_eq!(gecmis[0]["tur_etiket"], "İşlem durumu");
    assert!(gecmis.iter().any(|o| o["kaynak"] == "elle" && o["yeni"] == "Konkordato" && o["taraf"] == "AHMET YILMAZ"));
    assert!(gecmis.iter().any(|o| o["tur"] == "kesinlesme" && o["yeni"] == "İtiraz" && o["kaynak_etiket"] == "Hesap"));

    // Süre silinince yeniden hesaplanır
    gonder(c, "kural_sil", json!({"id": ozel})).unwrap();
    assert_eq!(al(c, "dosya", &[("id", &d1.to_string())])["takip"]["itiraz_suresi_gun"], 7);

    c.batch_execute("DELETE FROM dosya; DELETE FROM taraf; DELETE FROM vekil; DELETE FROM birim; DELETE FROM kesinlesme_kurali").unwrap();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn yol_ve_tarih() {
        assert!(is_path("__cu__/veri/ozet") && is_path("/__cu__/veri/borclular"));
        assert!(!is_path("__cu__/tema") && !is_path("main/avukat/x.ajx"));
        assert_eq!(gun_coz(&json!("2026-10-16")).unwrap(), NaiveDate::from_ymd_opt(2026, 10, 16));
        assert_eq!(gun_coz(&json!("16.10.2026")).unwrap(), NaiveDate::from_ymd_opt(2026, 10, 16));
        assert_eq!(gun_coz(&json!("")).unwrap(), None);
        assert_eq!(gun_coz(&Value::Null).unwrap(), None);
        assert!(gun_coz(&json!("olmaz")).is_err() && gun_coz(&json!(5)).is_err());
        assert_eq!(gun(NaiveDate::from_ymd_opt(2026, 1, 5)), json!("2026-01-05"));
    }
}
