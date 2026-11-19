//! İcra dosyalarını UYAP'tan güncelleme (Python `UyapMasa/modules/icra_core.py::IcraSorgu.ara` portu).
//!
//! İki ayrı iş (kullanıcı isteği 18.11: "bir buton dosya listesini, bir buton taraf bilgilerini sorgulasın"):
//!   • `liste`: 1 → 2 → 4 → 5 → 6 (taraf istenmez; tarafı eksik dosya sayısı rapora yazılır)
//!   • `taraf`: 1 → 2 → 3 → 5 → 6 (UYAP dosya kimliği oturumluk olduğu için liste yine alınır; kapanan kontrolü yok)
//! Kapsam: bütün açık dosyalar ya da tek daire; isteğe bağlı açılış tarihi aralığı (UYAP `dosyaAcilisTarihiStart/End`).
//! Tarih aralığı verilince kapanan dosya kontrolü yapılmaz (aralık dışı açık dosyalar "listede yok" sayılırdı).
//!
//! Adımlar (Python ile aynı sıra):
//!   1. Liste: `search_phrase_detayli` ile UYAP'taki AÇIK icra dosyaları, 500'lük sayfalar. Hata zarfı ya da
//!      tükenmeyen "eş zamanlı sorgulama" reddi "son sayfa" SANILMAZ: tarama eksik işaretlenir.
//!   2. Künye: her kayıt `icra_veri::dosya_kunyesi_kaydet` ile eklenir / güncellenir (kalıcı anahtar).
//!   3. Taraflar: alacaklısı ya da borçlusu olmayan dosyalarda borçlular `dosya_borclu_list` (TCKN'li) + diğer roller
//!      `dosya_taraf_bilgileri_brd`; yalnız borçlusu kimliksiz (TCKN / vergi no yok) dosyalarda yalnız borçlu listesi.
//!      Tarafı tam dosyaya istek atılmaz.
//!   4. Kapanan dosyalar: liste eksiksizse, veritabanında açık olup UYAP'ın açık listesinde olmayanlar Kapalı
//!      listede TEK TEK doğrulanır. Bulunamayana dokunulmaz. Aday sayısı beklenmeyecek kadar çoksa adım atlanır.
//!   5. Sayım raporu (UYAP toplamı ↔ alınan ↔ yazılan ↔ veritabanındaki açık dosya).
//!   6. Kesinleşme / tebliğ / takip aşaması yeniden hesaplanır (`icra_durum::durumlari_guncelle`; L7 işleri de).
//!
//! UYAP'A SAYGI: her istek `evrak_toplu::_uyap` kapısından (eş zamanlı red tekrarı, ağ geri çekilmesi, yeniden
//! giriş, doğrulamayı KULLANICI çözer, kullanıcının tıklamaları önce); sabit hız ve düzenli mola; Tüm Evrakı
//! İndir ve Gerekçeli Kararlar ile aynı sıra kilidi.
//!
//!   POST /__uyap_agent__/icra_guncelle            {"mod": "liste"|"taraf", "birim_id"?, "birim_adi"?, "acilis_bas"?,
//!                                                  "acilis_son"?} → 201 {tarama} (süren varsa o döner)
//!   GET  /__uyap_agent__/icra_guncelle            → {tarama|null} (süren ya da son tarama)
//!   GET  /__uyap_agent__/icra_guncelle/<id>       → {tarama}
//!   POST /__uyap_agent__/icra_guncelle/<id>/iptal → {tarama}
//!   POST /__uyap_agent__/icra_guncelle/<id>/captcha {"token"|null} → {tarama}

use std::collections::{HashMap, HashSet};
use std::future::Future;
use std::sync::{Arc, Mutex as StdMutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use once_cell::sync::Lazy;
use chrono::NaiveDate;
use serde_json::{json, Value};

use crate::evrak_toplu::{self, Indirme, BITTI, CALISIYOR, CAPTCHA, CIKTI_TEK, HATA, IPTAL, KAPSAM_TUM, SIRADA};
use crate::icra_kayit as kayit;
use crate::icra_tebligat as teb;
use crate::icra_veri::{self as veri, Rol, YargiTuru};

pub const ON_EK: &str = "/__uyap_agent__/icra_guncelle";
pub const ISTEK_ARASI_SN: f64 = 3.0;
pub const YAVAS_EN_COK_SN: f64 = 15.0;
pub const MOLA_HER_DOSYA: usize = 50;
pub const MOLA_SN: u64 = 60;
pub const SAYFA_BOYU: usize = 500;
pub const SAYFA_EN_COK: usize = 200;
pub const ARDISIK_AG_HATASI_SINIRI: usize = 3;
/// Bitmiş tarama bu kadar süre sonra kayıttan düşer (sonucu ekranda görülebilsin diye bir süre tutulur).
pub const SAKLAMA_SN: f64 = 6.0 * 3600.0;

pub const ARAMA: &str = "search_phrase_detayli.ajx";
pub const BORCLU_LISTESI: &str = "dosya_borclu_list.ajx";
pub const TARAF_BILGILERI: &str = crate::gerekceli_karar::TARAF_BILGILERI;
/// İcra Dairesi / İcra (Python `sgk_sorgu_gui.BIRIM_TURU2/3`).
pub const BIRIM_TURU2: &str = "1101";
pub const BIRIM_TURU3: &str = "2";

const IPTAL_HATASI: &str = "__IPTAL__";
const AG_ONEKI: &str = "AG_HATASI:";
const SON: &[&str] = &[BITTI, HATA, IPTAL];

fn now_secs() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

/// UYAP'a POST atan katman. Gerçekte `evrak_toplu::_uyap`; testte hazır yanıt veren sahte.
/// Dönen değer: yanıtın JSON'u (JSON değilse None).
pub trait Uyap: Send + Sync {
    fn post(&self, uc: &'static str, govde: Value) -> impl Future<Output = Result<Option<Value>, String>> + Send;
}

struct GercekUyap(Arc<Indirme>);

impl Uyap for GercekUyap {
    fn post(&self, uc: &'static str, govde: Value) -> impl Future<Output = Result<Option<Value>, String>> + Send {
        let ind = self.0.clone();
        async move { evrak_toplu::_uyap(&ind, "POST", uc, Some(&govde), None).await.map(|(_, _, _, v)| v) }
    }
}

// ── Saf yardımcılar ─────────────────────────────────────────────────────────────────────────────

/// Sayfa yanıtı gerçek bir kayıt listesi değilse nedeni: boş / JSON değil, UYAP hata zarfı ya da tekrarlara
/// rağmen süren "eş zamanlı sorgulama" reddi. Bunlar eskiden "0/1 kayıtlık son sayfa" sanılıyordu.
pub fn sayfa_gecersiz(veri: &Option<Value>) -> Option<String> {
    let Some(v) = veri else {
        return Some("UYAP boş ya da JSON olmayan yanıt verdi".into());
    };
    if evrak_toplu::eszamanli_mi(v, 0) {
        return Some("UYAP 'eş zamanlı sorgulama' reddi tekrarlara rağmen sürdü".into());
    }
    if v.is_object() {
        let (aciklama, kod) = (teb::metin(v, "error"), teb::metin(v, "errorCode"));
        if !aciklama.is_empty() || !kod.is_empty() {
            let ek = if !aciklama.is_empty() && !kod.is_empty() { format!(" [{kod}]") } else { String::new() };
            return Some(format!("UYAP hatası: {}{ek}", if aciklama.is_empty() { kod.trim() } else { aciklama.trim() }));
        }
    }
    None
}

/// `[kayıtlar, toplam]` biçiminde UYAP'ın bildirdiği toplam; yoksa None (uydurulmaz).
pub fn uyap_toplami(veri: &Value) -> Option<i64> {
    let a = veri.as_array()?;
    if a.len() < 2 || !a[0].is_array() {
        return None;
    }
    match &a[1] {
        Value::Number(n) => n.as_i64(),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

/// Python `sayim_satiri`: tek kapsamın okunur sayım satırı + eksik mi.
pub fn sayim_satiri(etiket: &str, alinan: usize, uyap_toplam: Option<i64>, tam: bool) -> (String, bool) {
    if !tam {
        return (format!("⚠️ {etiket}: {alinan} dosya alındı — tarama YARIM kaldı (bir sayfa alınamadı), liste EKSİK olabilir."), true);
    }
    match uyap_toplam {
        None => (format!("… {etiket}: {alinan} dosya alındı (UYAP toplam sayı bildirmedi — karşılaştırılamadı)."), false),
        Some(t) if t == alinan as i64 => (format!("✔ {etiket}: UYAP {t} dosya bildirdi, {alinan} dosyanın tamamı alındı."), false),
        Some(t) => (format!("⚠️ {etiket}: UYAP {t} dosya bildirdi ama {alinan} alındı — {} fark var.", (t - alinan as i64).abs()), true),
    }
}

/// Kapanan dosya kontrolünün emniyet freni: aday sayısı görülen açık dosyalardan (en az 50) fazlaysa liste
/// şüphelidir (UYAP boş / eksik yanıt vermiş olabilir) — hiçbir dosya kapatılmaz.
pub fn fren_asildi(aday: usize, gorulen: usize) -> bool {
    aday > gorulen.max(50)
}

/// Güncellemenin türü: dosya listesi ya da taraf bilgileri (ayrı düğmeler).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Mod {
    #[default]
    Liste,
    Taraf,
}

impl Mod {
    pub fn kod(self) -> &'static str {
        match self {
            Mod::Liste => "liste",
            Mod::Taraf => "taraf",
        }
    }
    pub fn coz(s: &str) -> Option<Mod> {
        match s.trim() {
            "" | "liste" => Some(Mod::Liste),
            "taraf" => Some(Mod::Taraf),
            _ => None,
        }
    }
}

/// Taramanın kapsamı: daire (yoksa bütün açık dosyalar) ve açılış tarihi aralığı.
#[derive(Debug, Clone, Default)]
pub struct Kapsam {
    pub birim_id: Option<String>,
    pub birim_adi: String,
    pub acilis_bas: Option<NaiveDate>,
    pub acilis_son: Option<NaiveDate>,
}

impl Kapsam {
    pub fn tarihli(&self) -> bool {
        self.acilis_bas.is_some() || self.acilis_son.is_some()
    }
    pub fn metin(&self) -> String {
        let mut m = if self.birim_id.is_some() {
            if self.birim_adi.is_empty() { format!("Birim {}", self.birim_id.as_deref().unwrap_or("")) } else { self.birim_adi.clone() }
        } else {
            "Bütün açık dosyalar".to_string()
        };
        if self.tarihli() {
            let g = |d: Option<NaiveDate>| d.map(|d| d.format("%d.%m.%Y").to_string()).unwrap_or_else(|| "…".into());
            m.push_str(&format!(" · açılış {}–{}", g(self.acilis_bas), g(self.acilis_son)));
        }
        m
    }
}

fn liste_govdesi(sayfa: usize, boy: usize, k: &Kapsam) -> Value {
    let mut g = json!({"dosyaDurumKod": 0, "pageSize": boy, "pageNumber": sayfa,
                       "birimTuru2": BIRIM_TURU2, "birimTuru3": BIRIM_TURU3});
    if let Some(b) = &k.birim_id {
        g["birimId"] = json!(b);
    }
    if let Some(d) = k.acilis_bas {
        g["dosyaAcilisTarihiStart"] = json!(d.format("%d.%m.%Y").to_string());
    }
    if let Some(d) = k.acilis_son {
        g["dosyaAcilisTarihiEnd"] = json!(d.format("%d.%m.%Y").to_string());
    }
    g
}

// ── Tarama durumu ───────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Default)]
pub struct TaramaInner {
    /// liste | kunye | taraf | kapanan | hesap | bitti
    pub asama: String,
    pub mod_: Mod,
    pub kapsam: Kapsam,
    /// Liste güncellemesinde tarafı eksik kalan dosya sayısı (taraf düğmesine yönlendirmek için).
    pub taraf_eksik: usize,
    pub i: usize,
    pub toplam: usize,
    pub uyap_toplam: Option<i64>,
    pub alinan: usize,
    pub yazilan: usize,
    pub yeni_dosya: usize,
    pub taraf_cekilen: usize,
    pub aday: usize,
    pub kapandi: usize,
    pub bulunamadi: usize,
    /// Liste eksiksiz alındı mı.
    pub tam: bool,
    pub guncellenen: usize,
    pub otomatik_is: usize,
    pub mola_bitis: Option<f64>,
    pub uyarilar: Vec<String>,
    pub rapor: Vec<String>,
}

/// `evrak_toplu::Indirme`'nin iptal / doğrulama / yavaşlatma davranışını aynen kullanır (durum, mesaj, captcha
/// orada); yalnız hız ve akış farklıdır.
pub struct Tarama {
    pub ind: Arc<Indirme>,
    pub inner: StdMutex<TaramaInner>,
    pub sayfa_boyu: usize,
    pub mola_her: usize,
    pub mola_sn: u64,
}

impl Tarama {
    pub fn new(mod_: Mod, kapsam: Kapsam) -> Arc<Self> {
        let ind = Indirme::new(String::new(), "İcra güncelleme".into(), CIKTI_TEK.into(), KAPSAM_TUM.into(), None);
        {
            let mut g = ind.inner.lock().unwrap();
            g.istek_arasi_sn = ISTEK_ARASI_SN;
            g.yavas_en_cok_sn = YAVAS_EN_COK_SN;
            g.mesaj = "Sırada bekliyor…".into();
        }
        Arc::new(Self {
            ind,
            inner: StdMutex::new(TaramaInner { asama: "liste".into(), mod_, kapsam, tam: true, ..Default::default() }),
            sayfa_boyu: SAYFA_BOYU,
            mola_her: MOLA_HER_DOSYA,
            mola_sn: MOLA_SN,
        })
    }

    pub fn id(&self) -> String {
        self.ind.inner.lock().unwrap().id.clone()
    }

    pub fn durum(&self) -> String {
        self.ind.inner.lock().unwrap().durum.clone()
    }

    fn mesaj_yaz(&self, m: impl Into<String>) {
        self.ind.inner.lock().unwrap().mesaj = m.into();
    }

    fn durum_mesaj(&self, durum: &str, mesaj: impl Into<String>) {
        let mut g = self.ind.inner.lock().unwrap();
        g.durum = durum.to_string();
        g.mesaj = mesaj.into();
    }

    fn uyari(&self, u: impl Into<String>) {
        self.inner.lock().unwrap().uyarilar.push(u.into());
    }

    fn asama(&self, a: &str, toplam: usize) {
        let mut g = self.inner.lock().unwrap();
        g.asama = a.to_string();
        g.i = 0;
        g.toplam = toplam;
    }

    fn ilerle(&self) {
        self.inner.lock().unwrap().i += 1;
    }

    fn bitti_mi(&self) -> bool {
        SON.contains(&self.durum().as_str())
    }

    pub fn sozluk(&self) -> Value {
        let (id, durum, mesaj, captcha, baslangic, bitis) = {
            let g = self.ind.inner.lock().unwrap();
            (g.id.clone(), g.durum.clone(), g.mesaj.clone(), g.captcha.clone(), g.baslangic, g.bitis)
        };
        let aralik = self.ind.aralik();
        let g = self.inner.lock().unwrap();
        let kalan = g.toplam.saturating_sub(g.i);
        // taraf: dosya başına 2 istek; kapanan: 1 istek. Mola süresi eklenir.
        let kalan_sn = match (g.asama.as_str(), SON.contains(&durum.as_str())) {
            (_, true) => None,
            ("taraf", _) => Some((kalan as f64 * 2.0 * aralik + (kalan / self.mola_her.max(1)) as f64 * self.mola_sn as f64) as i64),
            ("kapanan", _) => Some((kalan as f64 * aralik + (kalan / self.mola_her.max(1)) as f64 * self.mola_sn as f64) as i64),
            _ => None,
        };
        let uyarilar: Vec<&String> = g.uyarilar.iter().skip(g.uyarilar.len().saturating_sub(20)).collect();
        json!({
            "id": id, "durum": durum, "mesaj": mesaj, "asama": g.asama, "mod": g.mod_.kod(), "kapsam": g.kapsam.metin(),
            "birim_id": g.kapsam.birim_id, "taraf_eksik": g.taraf_eksik,
            "i": g.i, "toplam": g.toplam, "kalan_sn": kalan_sn, "aralik_sn": aralik,
            "mola_kalan_sn": g.mola_bitis.map(|b| (b - now_secs()).max(0.0) as i64),
            "uyap_toplam": g.uyap_toplam, "alinan": g.alinan, "yazilan": g.yazilan, "yeni_dosya": g.yeni_dosya,
            "taraf_cekilen": g.taraf_cekilen, "aday": g.aday, "kapandi": g.kapandi, "bulunamadi": g.bulunamadi,
            "tam": g.tam, "guncellenen": g.guncellenen, "otomatik_is": g.otomatik_is,
            "uyari_sayisi": g.uyarilar.len(), "uyarilar": uyarilar, "rapor": g.rapor, "captcha": captcha,
            "baslangic": baslangic, "bitis": bitis,
        })
    }
}

// ── Veritabanı (eşzamanlı istemci; ayrı iş parçacığında) ───────────────────────────────────────

async fn vt<R: Send + 'static>(f: impl FnOnce(&mut postgres::Client) -> Result<R, String> + Send + 'static) -> Result<R, String> {
    tokio::task::spawn_blocking(move || {
        let mut c = crate::veritabani::baglanti()?;
        f(&mut c)
    })
    .await
    .map_err(|e| format!("İşlem tamamlanamadı: {e}"))?
}

/// Künye yazımından sonra her dosya için taraf ihtiyacı.
struct TarafIsi {
    dosya: i64,
    uyap_id: String,
    etiket: String,
    /// true: alacaklı / borçlu yok → iki uç da; false: yalnız borçlusu kimliksiz → yalnız borçlu listesi.
    tum: bool,
}

// ── Akış ────────────────────────────────────────────────────────────────────────────────────────

async fn mola(t: &Tarama) -> Result<(), String> {
    t.inner.lock().unwrap().mola_bitis = Some(now_secs() + t.mola_sn as f64);
    let onceki = t.ind.inner.lock().unwrap().mesaj.clone();
    t.mesaj_yaz(format!("Mola: UYAP'ı yormamak için {} sn bekleniyor…", t.mola_sn));
    let mut sonuc = Ok(());
    for _ in 0..t.mola_sn {
        if let Err(e) = t.ind.iptal_kontrol() {
            sonuc = Err(e);
            break;
        }
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
    t.inner.lock().unwrap().mola_bitis = None;
    t.mesaj_yaz(onceki);
    sonuc
}

/// Tek dosyalık isteklerde ağ hatası taramayı hemen durdurmaz; art arda sınırı aşarsa durdurur.
fn ag_hatasi_mi(e: &str, t: &Tarama, etiket: &str, ardisik: &mut usize) -> Result<(), String> {
    let Some(ag) = e.strip_prefix(AG_ONEKI) else {
        return Err(e.to_string());
    };
    *ardisik += 1;
    t.uyari(format!("{etiket}: bağlantı koptu ({ag})"));
    if *ardisik >= ARDISIK_AG_HATASI_SINIRI {
        return Err(format!("{AG_ONEKI}art arda {ardisik} dosyada bağlantı koptu"));
    }
    Ok(())
}

/// 1. Liste: UYAP'taki açık icra dosyaları (dosyaId ile tekil).
async fn listele<U: Uyap>(t: &Tarama, u: &U) -> Result<Vec<Value>, String> {
    let kapsam = t.inner.lock().unwrap().kapsam.clone();
    t.asama("liste", 0);
    t.mesaj_yaz("UYAP'taki açık icra dosyaları listeleniyor…");
    let mut tum: Vec<Value> = Vec::new();
    let mut gorulen: HashSet<String> = HashSet::new();
    for sayfa in 1..=SAYFA_EN_COK {
        t.ind.iptal_kontrol()?;
        let veri = u.post(ARAMA, liste_govdesi(sayfa, t.sayfa_boyu, &kapsam)).await?;
        if let Some(neden) = sayfa_gecersiz(&veri) {
            t.inner.lock().unwrap().tam = false;
            t.uyari(format!("Liste sayfası {sayfa} alınamadı: {neden}. Liste eksik kaldı; kapanan dosya kontrolü yapılmayacak."));
            break;
        }
        let veri = veri.unwrap_or(Value::Null);
        if sayfa == 1 {
            t.inner.lock().unwrap().uyap_toplam = uyap_toplami(&veri);
        }
        let parca = crate::gerekceli_karar::kayitlar_listesi(&veri);
        let n = parca.len();
        for r in parca {
            let did = teb::metin(&r, "dosyaId");
            if !did.is_empty() && !gorulen.insert(did) {
                continue;
            }
            tum.push(r);
        }
        {
            let mut g = t.inner.lock().unwrap();
            g.alinan = tum.len();
            g.i = tum.len();
        }
        t.mesaj_yaz(format!("{} dosya alındı (sayfa {sayfa})…", tum.len()));
        if n < t.sayfa_boyu {
            break;
        }
        if sayfa == SAYFA_EN_COK {
            t.inner.lock().unwrap().tam = false;
            t.uyari(format!("Sayfa sınırına ({SAYFA_EN_COK}) ulaşıldı; bazı dosyalar alınmamış olabilir."));
        }
    }
    Ok(tum)
}

/// 2. Künye: her kayıt eklenir / güncellenir. Döner: görülen dosya kimlikleri ve taraf isteyecek dosyalar.
async fn kunyeleri_yaz(t: &Tarama, kayitlar: Vec<Value>) -> Result<(Vec<i64>, Vec<TarafIsi>), String> {
    t.asama("kunye", kayitlar.len());
    t.mesaj_yaz(format!("{} dosyanın künyesi veritabanına yazılıyor…", kayitlar.len()));
    let mut gorulen: Vec<i64> = Vec::new();
    let mut eslesme: Vec<(i64, String, String)> = Vec::new();
    for parca in kayitlar.chunks(250) {
        t.ind.iptal_kontrol()?;
        let parca = parca.to_vec();
        let (yazilan, uyarilar, yeni) = vt(move |c| {
            let mut yazilan: Vec<(i64, String, String)> = Vec::new();
            let mut uyarilar = Vec::new();
            let mut yeni = 0usize;
            for rec in &parca {
                let etiket = format!("{} {}", teb::metin(rec, "birimAdi"), teb::metin(rec, "dosyaNo"));
                match veri::dosya_kunyesi_kaydet(c, rec, Some(YargiTuru::Icra.kod())) {
                    Ok((id, y)) => {
                        yeni += y as usize;
                        yazilan.push((id, teb::metin(rec, "dosyaId"), etiket));
                    }
                    Err(e) => uyarilar.push(format!("{etiket}: künye yazılamadı ({e})")),
                }
            }
            Ok((yazilan, uyarilar, yeni))
        })
        .await?;
        {
            let mut g = t.inner.lock().unwrap();
            g.i += parca_len(&yazilan, &uyarilar);
            g.yazilan += yazilan.len();
            g.yeni_dosya += yeni;
            g.uyarilar.extend(uyarilar);
        }
        gorulen.extend(yazilan.iter().map(|x| x.0));
        eslesme.extend(yazilan);
    }
    // Taraf ihtiyacı: alacaklısı ya da borçlusu olmayan (her icra dosyasında ikisi de vardır; yoksa kayıt eksik) →
    // tam; borçlusu kimliksiz → yalnız borçlu listesi.
    let idler = gorulen.clone();
    let eksik: HashMap<i64, (bool, bool)> = vt(move |c| {
        Ok(c.query(
            "SELECT d.id,
                    NOT EXISTS (SELECT 1 FROM dosya_taraf x WHERE x.dosya_id = d.id AND x.rol = 'alacakli')
                    OR NOT EXISTS (SELECT 1 FROM dosya_taraf x WHERE x.dosya_id = d.id AND x.rol = 'borclu'),
                    EXISTS (SELECT 1 FROM dosya_taraf x JOIN taraf tr ON tr.id = x.taraf_id
                             WHERE x.dosya_id = d.id AND x.rol = 'borclu' AND x.uyapta_gorulmedi IS NULL
                               AND tr.tckn IS NULL AND tr.mersis_no IS NULL AND tr.vergi_no = '')
               FROM dosya d WHERE d.id = ANY($1)",
            &[&idler],
        )
        .map_err(|e| format!("veritabanı: {e}"))?
        .iter()
        .map(|s| (s.get::<_, i64>(0), (s.get::<_, bool>(1), s.get::<_, bool>(2))))
        .collect())
    })
    .await?;
    let mod_ = t.inner.lock().unwrap().mod_;
    if mod_ == Mod::Liste {
        t.inner.lock().unwrap().taraf_eksik = eksik.values().filter(|(tum, kimliksiz)| *tum || *kimliksiz).count();
        return Ok((gorulen, Vec::new()));
    }
    let isler = eslesme
        .into_iter()
        .filter(|(_, uyap_id, _)| !uyap_id.is_empty())
        .filter_map(|(dosya, uyap_id, etiket)| match eksik.get(&dosya) {
            Some((true, _)) => Some(TarafIsi { dosya, uyap_id, etiket, tum: true }),
            Some((false, true)) => Some(TarafIsi { dosya, uyap_id, etiket, tum: false }),
            _ => None,
        })
        .collect();
    Ok((gorulen, isler))
}

fn parca_len(a: &[(i64, String, String)], b: &[String]) -> usize {
    a.len() + b.len()
}

/// 3. Taraflar: borçlular (TCKN'li) + borçlu dışındaki roller.
async fn taraflari_cek<U: Uyap>(t: &Tarama, u: &U, isler: Vec<TarafIsi>) -> Result<(), String> {
    t.asama("taraf", isler.len());
    if isler.is_empty() {
        return Ok(());
    }
    let borclu_disi: Vec<&'static str> = Rol::HEPSI.iter().map(|r| r.kod()).filter(|k| *k != Rol::Borclu.kod()).collect();
    let mut ardisik = 0usize;
    for (sira, is_) in isler.into_iter().enumerate() {
        if sira > 0 && sira % t.mola_her == 0 {
            mola(t).await?;
        }
        t.ind.iptal_kontrol()?;
        t.mesaj_yaz(format!("Taraflar alınıyor: {} ({}/{})", is_.etiket, sira + 1, t.inner.lock().unwrap().toplam));
        let govde = json!({"dosyaId": is_.uyap_id});
        // Borçlular: TCKN / vergi no veren uç.
        match u.post(BORCLU_LISTESI, govde.clone()).await {
            Ok(Some(Value::Array(liste))) => {
                ardisik = 0;
                let (dosya, etiket) = (is_.dosya, is_.etiket.clone());
                let notlar = vt(move |c| {
                    let notlar = StdMutex::new(Vec::new());
                    kayit::borclu_kimliklerini_kaydet(c, dosya, &liste, &|m: &str| notlar.lock().unwrap().push(format!("{etiket}: {}", m.trim())))?;
                    Ok(notlar.into_inner().unwrap())
                })
                .await?;
                t.inner.lock().unwrap().uyarilar.extend(notlar);
            }
            Ok(v) => {
                ardisik = 0;
                t.uyari(format!("{}: borçlu listesi alınamadı ({})", is_.etiket, sayfa_gecersiz(&v).unwrap_or_else(|| "beklenmeyen yanıt".into())));
            }
            Err(e) => ag_hatasi_mi(&e, t, &is_.etiket, &mut ardisik)?,
        }
        if !is_.tum {
            t.inner.lock().unwrap().taraf_cekilen += 1;
            t.ilerle();
            continue;
        }
        // Alacaklı ve diğer roller (bu uç TCKN vermez; borçlular yukarıda yazıldı, burada atlanır).
        match u.post(TARAF_BILGILERI, govde).await {
            Ok(Some(Value::Array(liste))) => {
                ardisik = 0;
                let secilen: Vec<Value> =
                    liste.into_iter().filter(|x| x.is_object() && teb::tr_kucuk(&teb::metin(x, "rol")) != Rol::Borclu.kod()).collect();
                if !secilen.is_empty() {
                    let (dosya, etiket, roller) = (is_.dosya, is_.etiket.clone(), borclu_disi.clone());
                    let notlar = vt(move |c| {
                        let notlar = StdMutex::new(Vec::new());
                        kayit::dosya_taraf_kaydet_roller(c, dosya, &secilen, Some(&roller), &|m: &str| {
                            notlar.lock().unwrap().push(format!("{etiket}: {}", m.trim()))
                        })?;
                        Ok(notlar.into_inner().unwrap())
                    })
                    .await?;
                    let mut g = t.inner.lock().unwrap();
                    g.uyarilar.extend(notlar);
                    g.taraf_cekilen += 1;
                } else {
                    t.uyari(format!("{}: UYAP taraf listesinde alacaklı yok", is_.etiket));
                }
            }
            Ok(v) => {
                ardisik = 0;
                t.uyari(format!("{}: taraflar alınamadı ({})", is_.etiket, sayfa_gecersiz(&v).unwrap_or_else(|| "beklenmeyen yanıt".into())));
            }
            Err(e) => ag_hatasi_mi(&e, t, &is_.etiket, &mut ardisik)?,
        }
        t.ilerle();
    }
    Ok(())
}

/// 4. Kapanan dosyalar: açık listede olmayan ama veritabanında açık duranlar Kapalı listede tek tek aranır.
async fn kapananlari_uzlastir<U: Uyap>(t: &Tarama, u: &U, gorulen: Vec<i64>) -> Result<(), String> {
    let birim = t.inner.lock().unwrap().kapsam.birim_id.clone();
    let gorulen_sayisi = gorulen.len();
    let adaylar: Vec<(i64, i32, i32, i32, String, String)> = vt(move |c| {
        Ok(c.query(
            "SELECT d.id, d.yil, d.sira_no, d.tur_kod, b.birim_id, b.ad FROM dosya d JOIN birim b ON b.id = d.birim_id
              WHERE d.yargi_turu = 2 AND d.durum_kod = 0 AND NOT (d.id = ANY($1))
                AND ($2::text IS NULL OR b.birim_id = $2) ORDER BY d.yil DESC, d.sira_no DESC",
            &[&gorulen, &birim],
        )
        .map_err(|e| format!("veritabanı: {e}"))?
        .iter()
        .map(|s| (s.get(0), s.get(1), s.get(2), s.get(3), s.get(4), s.get(5)))
        .collect())
    })
    .await?;
    t.inner.lock().unwrap().aday = adaylar.len();
    if adaylar.is_empty() {
        return Ok(());
    }
    if fren_asildi(adaylar.len(), gorulen_sayisi) {
        t.uyari(format!(
            "{} açık dosya UYAP listesinde görünmedi — sayı beklenmeyecek kadar yüksek, kapanan dosya kontrolü bu tur ATLANDI.",
            adaylar.len()
        ));
        return Ok(());
    }
    t.asama("kapanan", adaylar.len());
    let mut ardisik = 0usize;
    for (sira, (id, yil, sira_no, tur_kod, birim_id, birim_ad)) in adaylar.into_iter().enumerate() {
        if sira > 0 && sira % t.mola_her == 0 {
            mola(t).await?;
        }
        t.ind.iptal_kontrol()?;
        let etiket = format!("{birim_ad} {yil}/{sira_no}");
        t.mesaj_yaz(format!("Kapanan dosya kontrolü: {etiket} ({}/{})", sira + 1, t.inner.lock().unwrap().toplam));
        let govde = json!({"dosyaDurumKod": 1, "pageSize": t.sayfa_boyu, "pageNumber": 1, "dosyaYil": yil, "dosyaSira": sira_no,
                           "birimId": birim_id, "birimTuru2": BIRIM_TURU2, "birimTuru3": BIRIM_TURU3});
        let v = match u.post(ARAMA, govde).await {
            Ok(v) => {
                ardisik = 0;
                v
            }
            Err(e) => {
                ag_hatasi_mi(&e, t, &etiket, &mut ardisik)?;
                t.ilerle();
                continue;
            }
        };
        if let Some(neden) = sayfa_gecersiz(&v) {
            t.uyari(format!("{etiket}: kapalı listede aranamadı ({neden}); dokunulmadı"));
            t.ilerle();
            continue;
        }
        let no = format!("{yil}/{sira_no}");
        let eslesen = crate::gerekceli_karar::kayitlar_listesi(&v.unwrap_or(Value::Null))
            .into_iter()
            .find(|r| teb::metin(r, "dosyaNo").trim() == no && veri::yil_sira(&no) == (yil, sira_no) && tur_kodu(r) == tur_kod);
        match eslesen {
            Some(rec) => {
                let durum = teb::metin(&rec, "dosyaDurum");
                vt(move |c| veri::dosya_kunyesi_kaydet(c, &rec, Some(YargiTuru::Icra.kod())).map(|_| ())).await?;
                t.inner.lock().unwrap().kapandi += 1;
                let _ = id;
                t.uyari(format!("✔ {etiket} kapalı olarak güncellendi ({}).", if durum.is_empty() { "Kapalı".into() } else { durum }));
            }
            None => t.inner.lock().unwrap().bulunamadi += 1,
        }
        t.ilerle();
    }
    Ok(())
}

fn tur_kodu(r: &Value) -> i32 {
    match r.get("dosyaTurKod") {
        Some(Value::Number(n)) => n.as_i64().unwrap_or(0) as i32,
        Some(Value::String(s)) => s.trim().parse().unwrap_or(0),
        _ => 0,
    }
}

/// 5. Sayım raporu.
async fn rapor_yaz(t: &Tarama, durduruldu: bool) -> Result<(), String> {
    let (alinan, uyap_toplam, tam, yazilan, kapsam, mod_, taraf_eksik) = {
        let g = t.inner.lock().unwrap();
        (g.alinan, g.uyap_toplam, g.tam, g.yazilan, g.kapsam.clone(), g.mod_, g.taraf_eksik)
    };
    let mut satirlar = vec![sayim_satiri(&format!("İcra ({})", kapsam.metin()), alinan, uyap_toplam, tam).0];
    if durduruldu {
        satirlar.push(format!("⚠️ Tarama durduruldu: {yazilan}/{alinan} dosya veritabanına yazıldı."));
    } else if yazilan < alinan {
        satirlar.push(format!("⚠️ {} dosya veritabanına yazılamadı (uyarılara bakın).", alinan - yazilan));
    } else {
        satirlar.push(format!("✔ Alınan {alinan} dosyanın tamamı veritabanına yazıldı."));
    }
    if tam && !durduruldu {
        let (birim, bas, son) = (kapsam.birim_id.clone(), kapsam.acilis_bas, kapsam.acilis_son);
        let db_acik: i64 = vt(move |c| {
            Ok(c.query_one(
                "SELECT count(*) FROM dosya d JOIN birim b ON b.id = d.birim_id
                  WHERE d.yargi_turu = 2 AND d.durum_kod = 0 AND ($1::text IS NULL OR b.birim_id = $1)
                    AND ($2::date IS NULL OR d.acilis_tarihi::date >= $2) AND ($3::date IS NULL OR d.acilis_tarihi::date <= $3)",
                &[&birim, &bas, &son],
            )
            .map_err(|e| format!("veritabanı: {e}"))?
            .get(0))
        })
        .await?;
        satirlar.push(if db_acik == alinan as i64 {
            format!("✔ Veritabanındaki açık icra dosyası sayısı ({db_acik}) UYAP ile birebir aynı.")
        } else {
            format!(
                "⚠️ Veritabanında {db_acik} açık icra dosyası var, UYAP'ta {alinan} — {} fark (UYAP'ta hiçbir listede bulunamayan ya da yazılamayan dosyalar).",
                (db_acik - alinan as i64).abs()
            )
        });
    }
    if mod_ == Mod::Liste && kapsam.tarihli() {
        satirlar.push("Tarih aralıklı güncellemede kapanan dosya kontrolü yapılmaz (bütün dosyalar ya da daire seçin).".into());
    }
    if mod_ == Mod::Liste && taraf_eksik > 0 {
        satirlar.push(format!("{taraf_eksik} dosyanın tarafları ya da borçlu kimlikleri eksik — \"Taraf bilgilerini güncelle\" ile alınabilir."));
    }
    let g = &mut t.inner.lock().unwrap();
    if g.yeni_dosya > 0 || g.kapandi > 0 || g.bulunamadi > 0 || g.taraf_cekilen > 0 {
        satirlar.push(format!(
            "Yeni dosya: {} · tarafları / borçlu kimlikleri alınan: {} · kapandı: {} · hiçbir listede bulunamayan (dokunulmadı): {}",
            g.yeni_dosya, g.taraf_cekilen, g.kapandi, g.bulunamadi
        ));
    }
    g.rapor = satirlar;
    Ok(())
}

/// 6. Kesinleşme / tebliğ / takip aşaması hesabı (L7 otomatik işleri de buradan).
async fn hesapla(t: &Tarama) -> Result<(), String> {
    t.asama("hesap", 0);
    t.mesaj_yaz("Kesinleşme, tebliğ durumu ve takip aşamaları yeniden hesaplanıyor…");
    let (s, notlar) = vt(|c| {
        let notlar = StdMutex::new(Vec::new());
        let s = crate::icra_durum::durumlari_guncelle(c, None, chrono::Local::now().date_naive(), &|m: &str| {
            notlar.lock().unwrap().push(m.trim().to_string())
        })?;
        Ok((s, notlar.into_inner().unwrap()))
    })
    .await?;
    let mut g = t.inner.lock().unwrap();
    g.guncellenen = s.guncellenen;
    g.otomatik_is = s.otomatik_is;
    g.uyarilar.extend(notlar.into_iter().filter(|m| m.starts_with('⚠')));
    g.rapor.push(format!("Durum hesabı: {} borçlunun durumu değişti, {} otomatik iş açıldı.", s.guncellenen, s.otomatik_is));
    Ok(())
}

/// Bütün akış. Durdurulunca / koparsa da o ana kadarki sayım raporu yazılır.
pub async fn akis<U: Uyap>(t: &Tarama, u: &U) -> Result<(), String> {
    let kayitlar = listele(t, u).await?;
    let sonuc: Result<(), String> = async {
        let (gorulen, isler) = kunyeleri_yaz(t, kayitlar).await?;
        let (mod_, kapanan) = {
            let g = t.inner.lock().unwrap();
            (g.mod_, g.mod_ == Mod::Liste && g.tam && !g.kapsam.tarihli())
        };
        if mod_ == Mod::Taraf {
            taraflari_cek(t, u, isler).await?;
        }
        if kapanan {
            kapananlari_uzlastir(t, u, gorulen).await?;
        }
        Ok(())
    }
    .await;
    rapor_yaz(t, sonuc.is_err()).await?;
    sonuc?;
    hesapla(t).await?;
    t.asama("bitti", 0);
    Ok(())
}

// ── Çalıştırma ve uçlar ─────────────────────────────────────────────────────────────────────────

static KAYITLAR: Lazy<StdMutex<HashMap<String, Arc<Tarama>>>> = Lazy::new(|| StdMutex::new(HashMap::new()));

async fn calistir(t: Arc<Tarama>) {
    let sonuc = {
        let _sira = evrak_toplu::sira_kilidi().lock().await;
        match t.ind.iptal_kontrol() {
            Err(e) => Err(e),
            Ok(()) => {
                t.durum_mesaj(CALISIYOR, "Başladı…");
                akis(&t, &GercekUyap(t.ind.clone())).await
            }
        }
    };
    t.ind.inner.lock().unwrap().captcha = None;
    t.inner.lock().unwrap().mola_bitis = None;
    match sonuc {
        Ok(()) => {
            let (alinan, yeni, kapandi, mod_, cekilen) = {
                let g = t.inner.lock().unwrap();
                (g.alinan, g.yeni_dosya, g.kapandi, g.mod_, g.taraf_cekilen)
            };
            t.durum_mesaj(BITTI, match mod_ {
                Mod::Liste => format!("Tamamlandı: {alinan} açık dosya güncellendi ({yeni} yeni, {kapandi} kapandı)."),
                Mod::Taraf => format!("Tamamlandı: {cekilen} dosyanın tarafları / borçlu kimlikleri alındı ({alinan} dosya tarandı)."),
            });
        }
        Err(e) if e == IPTAL_HATASI => t.durum_mesaj(IPTAL, "Durduruldu. Yeniden başlatınca tarafları tamam olan dosyalara istek atılmaz."),
        Err(e) => match e.strip_prefix(AG_ONEKI) {
            Some(ag) => t.durum_mesaj(HATA, format!("UYAP bağlantısı tekrar tekrar koptu ({ag}). Bir süre sonra yeniden deneyin.")),
            None => t.durum_mesaj(HATA, format!("Hata: {e}")),
        },
    }
    t.ind.inner.lock().unwrap().bitis = Some(now_secs());
}

fn temizle() {
    let simdi = now_secs();
    KAYITLAR.lock().unwrap().retain(|_, t| t.ind.inner.lock().unwrap().bitis.is_none_or(|b| simdi - b < SAKLAMA_SN));
}

/// Süren tarama; yoksa en son başlayan.
pub fn aktif() -> Option<Arc<Tarama>> {
    temizle();
    let k = KAYITLAR.lock().unwrap();
    k.values().find(|t| !t.bitti_mi()).cloned().or_else(|| {
        k.values()
            .max_by(|a, b| a.ind.inner.lock().unwrap().baslangic.total_cmp(&b.ind.inner.lock().unwrap().baslangic))
            .cloned()
    })
}

pub fn baslat(mod_: Mod, kapsam: Kapsam) -> Arc<Tarama> {
    if let Some(t) = aktif().filter(|t| !t.bitti_mi()) {
        return t;
    }
    let t = Tarama::new(mod_, kapsam);
    KAYITLAR.lock().unwrap().insert(t.id(), t.clone());
    tokio::spawn(calistir(t.clone()));
    t
}

/// Başlatma gövdesi → (tür, kapsam). Tarihler GG.AA.YYYY ya da YYYY-AA-GG.
pub fn baslatma_istegi(govde: &Value) -> Result<(Mod, Kapsam), String> {
    let mod_ = Mod::coz(&teb::metin(govde, "mod")).ok_or("Güncelleme türü geçersiz.")?;
    let birim_id = teb::metin(govde, "birim_id").trim().to_string();
    if !birim_id.is_empty() && (birim_id.len() > 32 || !birim_id.chars().all(|c| c.is_ascii_digit())) {
        return Err("Birim kimliği geçersiz.".into());
    }
    let tarih = |a: &str| -> Result<Option<NaiveDate>, String> {
        let s = teb::metin(govde, a);
        let s = s.trim();
        if s.is_empty() {
            return Ok(None);
        }
        veri::tarih::gun_coz(s).map(Some).ok_or_else(|| format!("Tarih anlaşılamadı: {s}"))
    };
    let (bas, son) = (tarih("acilis_bas")?, tarih("acilis_son")?);
    if let (Some(b), Some(s)) = (bas, son) {
        if b > s {
            return Err("Açılış tarihi aralığı ters: başlangıç bitişten sonra.".into());
        }
    }
    Ok((mod_, Kapsam {
        birim_id: (!birim_id.is_empty()).then_some(birim_id),
        birim_adi: veri::kirp(teb::metin(govde, "birim_adi").trim(), 120),
        acilis_bas: bas,
        acilis_son: son,
    }))
}

fn json_resp(status: u16, val: Value) -> (u16, HashMap<String, String>, Vec<u8>) {
    let mut h = HashMap::new();
    h.insert("content-type".to_string(), "application/json; charset=utf-8".to_string());
    h.insert("cache-control".to_string(), "no-store".to_string());
    (status, h, serde_json::to_vec(&val).unwrap_or_default())
}

pub async fn handle_http(method: &str, path: &str, _qp: &HashMap<String, String>, body: &[u8]) -> (u16, HashMap<String, String>, Vec<u8>) {
    let rest = path.trim_start_matches('/').strip_prefix(ON_EK.trim_start_matches('/')).unwrap_or("").trim_matches('/');
    let parts: Vec<&str> = rest.split('/').filter(|s| !s.is_empty()).collect();
    let mut govde = Value::Null;
    if method == "POST" && !body.is_empty() {
        match serde_json::from_slice::<Value>(body) {
            Ok(v) if v.is_object() => govde = v,
            Ok(_) => {}
            Err(_) => return json_resp(400, json!({"error": "Geçersiz JSON gövdesi."})),
        }
    }
    if parts.is_empty() {
        return match method {
            "GET" => json_resp(200, json!({"tarama": aktif().map(|t| t.sozluk())})),
            "POST" => match baslatma_istegi(&govde) {
                Ok((mod_, kapsam)) => json_resp(201, json!({"tarama": baslat(mod_, kapsam).sozluk()})),
                Err(e) => json_resp(400, json!({"error": e})),
            },
            _ => json_resp(405, json!({"error": "Yöntem desteklenmiyor."})),
        };
    }
    let Some(t) = KAYITLAR.lock().unwrap().get(parts[0]).cloned() else {
        return json_resp(404, json!({"error": "Tarama bulunamadı (süresi dolmuş olabilir)."}));
    };
    match (parts.get(1).copied().unwrap_or(""), method) {
        ("", "GET") => json_resp(200, json!({"tarama": t.sozluk()})),
        ("iptal", "POST") => {
            t.ind.iptal_et();
            if t.durum() == SIRADA {
                t.ind.inner.lock().unwrap().bitis = Some(now_secs());
            }
            json_resp(200, json!({"tarama": t.sozluk()}))
        }
        ("captcha", "POST") => {
            let jeton = govde.get("token").or_else(|| govde.get("jeton")).and_then(|j| j.as_str()).filter(|j| j.len() < 8192).unwrap_or("");
            if t.durum() == CAPTCHA {
                t.ind.captcha_coz(jeton);
            }
            json_resp(200, json!({"tarama": t.sozluk()}))
        }
        _ => json_resp(405, json!({"error": "Yöntem/eylem desteklenmiyor."})),
    }
}

// ── Testler ─────────────────────────────────────────────────────────────────────────────────────

/// Hazır yanıt veren sahte UYAP: her çağrı kaydedilir, yanıt verilen işlevden gelir.
#[cfg(test)]
pub(crate) struct SahteUyap {
    pub cagrilar: StdMutex<Vec<(String, Value)>>,
    pub yanit: Box<dyn Fn(&str, &Value) -> Result<Option<Value>, String> + Send + Sync>,
}

#[cfg(test)]
impl Uyap for SahteUyap {
    fn post(&self, uc: &'static str, govde: Value) -> impl Future<Output = Result<Option<Value>, String>> + Send {
        let y = (self.yanit)(uc, &govde);
        self.cagrilar.lock().unwrap().push((uc.to_string(), govde));
        async move { y }
    }
}

/// Gerçek veritabanı üzerinde sahte UYAP ile senaryo; `veritabani::tests::uctan_uca` içinden çağrılır.
#[cfg(test)]
pub(crate) fn veritabani_senaryosu(c: &mut postgres::Client) {
    use crate::icra_veri::dosya_kunyesi_kaydet;
    let sessiz = |_: &str| {};
    let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
    let kunye = |no: &str, durum_kod: i64, did: &str| {
        json!({"dosyaId": did, "dosyaNo": no, "dosyaDurumKod": durum_kod, "dosyaDurum": if durum_kod == 0 { "Açık" } else { "Kapalı" },
               "dosyaTurKod": 35, "dosyaTur": "İcra Dosyası", "birimAdi": "İzmir 9. İcra Dairesi", "birimId": 9009,
               "dosyaAcilisTarihi": "01.11.2026"})
    };
    // Var olan: A (alacaklı + kimliksiz borçlu, listede), B (listede yok, kapalı listede var), C (hiçbir yerde yok)
    let (a, _) = dosya_kunyesi_kaydet(c, &kunye("2026/10", 0, "eski-a"), Some(YargiTuru::Icra.kod())).unwrap();
    kayit::dosya_taraf_kaydet(c, a, &[json!({"adi": "X BANKASI A.Ş.", "rol": "Alacaklı", "kisiKurum": "Kurum"}),
                                      json!({"adi": "ALİ VELİ", "rol": "Borçlu", "kisiKurum": "Kişi"})], &sessiz).unwrap();
    let (b, _) = dosya_kunyesi_kaydet(c, &kunye("2026/11", 0, "eski-b"), Some(YargiTuru::Icra.kod())).unwrap();
    kayit::dosya_taraf_kaydet(c, b, &[json!({"adi": "X BANKASI A.Ş.", "rol": "Alacaklı", "kisiKurum": "Kurum"})], &sessiz).unwrap();
    let (cc, _) = dosya_kunyesi_kaydet(c, &kunye("2026/12", 0, "eski-c"), Some(YargiTuru::Icra.kod())).unwrap();
    let durum_kod = |c: &mut postgres::Client, id: i64| -> i32 { c.query_one("SELECT durum_kod FROM dosya WHERE id = $1", &[&id]).unwrap().get(0) };

    // UYAP: açık liste 2'lik sayfalar → [A, D] + toplam 3, sonra [E]. D yeni (taraflar gelir), E yeni (taraf ucu hata verir).
    let yanit = move |eszamanli_ikinci_sayfa: bool| -> Box<dyn Fn(&str, &Value) -> Result<Option<Value>, String> + Send + Sync> {
        Box::new(move |uc: &str, g: &Value| -> Result<Option<Value>, String> {
            let k = |no: &str, durum: i64, did: &str| {
                json!({"dosyaId": did, "dosyaNo": no, "dosyaDurumKod": durum, "dosyaDurum": if durum == 0 { "Açık" } else { "Kapalı" },
                       "dosyaTurKod": 35, "dosyaTur": "İcra Dosyası", "birimAdi": "İzmir 9. İcra Dairesi", "birimId": 9009})
            };
            Ok(Some(match (uc, g["dosyaDurumKod"].as_i64(), g["pageNumber"].as_i64(), g["dosyaSira"].as_i64()) {
                (ARAMA, Some(0), Some(1), _) => json!([[k("2026/10", 0, "u-a"), k("2026/20", 0, "u-d")], 3]),
                (ARAMA, Some(0), Some(2), _) if eszamanli_ikinci_sayfa => {
                    json!({"errorCode": "PRTL_GNL_10001", "error": "Eş zamanlı olarak birden fazla sorgulama yapamazsınız! "})
                }
                (ARAMA, Some(0), Some(2), _) => json!([[k("2026/21", 0, "u-e")], 3]),
                (ARAMA, Some(1), _, Some(11)) => json!([[k("2026/11", 1, "u-b")], 1]),
                (ARAMA, Some(1), _, _) => json!([[], 0]),
                (BORCLU_LISTESI, ..) if g["dosyaId"] == "u-d" => {
                    json!([{"adi": "AYŞE", "soyadi": "KARA", "tcKimlikNo": "12345678901", "kisiKurum": "Kişi"}])
                }
                (BORCLU_LISTESI, ..) if g["dosyaId"] == "u-a" => {
                    json!([{"adi": "ALİ", "soyadi": "VELİ", "tcKimlikNo": "10987654321", "kisiKurum": "Kişi"}])
                }
                (TARAF_BILGILERI, ..) if g["dosyaId"] == "u-d" => json!([
                    {"adi": "Y FAKTORİNG A.Ş.", "rol": "Alacaklı", "kisiKurum": "Kurum", "vekil": "[UTKU AVUKAT]"},
                    {"adi": "AYŞE KARA", "rol": "Borçlu", "kisiKurum": "Kişi"}
                ]),
                (BORCLU_LISTESI | TARAF_BILGILERI, ..) if g["dosyaId"] == "u-e" => json!({"errorCode": "PRTL_GNL_500", "error": "Sunucu hatası"}),
                _ => return Err(format!("beklenmeyen istek: {uc} {g}")),
            }))
        })
    };
    let calistir = |mod_: Mod, eszamanli: bool, bas: Option<NaiveDate>| {
        let kapsam = Kapsam { birim_id: Some("9009".into()), birim_adi: "İzmir 9. İcra Dairesi".into(), acilis_bas: bas, acilis_son: None };
        let t = Tarama::new(mod_, kapsam);
        let mut t = Arc::try_unwrap(t).ok().unwrap();
        t.sayfa_boyu = 2;
        t.mola_her = 1000;
        let u = SahteUyap { cagrilar: StdMutex::new(Vec::new()), yanit: yanit(eszamanli) };
        let r = rt.block_on(akis(&t, &u));
        (t, u, r)
    };
    let taraf_istegi = |u: &SahteUyap| u.cagrilar.lock().unwrap().iter().filter(|(uc, _)| uc != ARAMA).count();
    let kapanan_istegi = |u: &SahteUyap| u.cagrilar.lock().unwrap().iter().any(|(uc, g)| uc == ARAMA && g["dosyaDurumKod"] == 1);

    // 1) Dosya listesi, ikinci sayfa reddedilir: liste eksik → kapanan kontrolü YOK; taraf hiç istenmez
    let (t, u, r) = calistir(Mod::Liste, true, None);
    r.unwrap();
    let g = t.inner.lock().unwrap();
    assert!(!g.tam);
    assert_eq!((g.alinan, g.uyap_toplam, g.yeni_dosya), (2, Some(3), 1));
    assert!(g.rapor[0].contains("YARIM"), "{:?}", g.rapor);
    assert!(g.uyarilar.iter().any(|x| x.contains("eş zamanlı")), "{:?}", g.uyarilar);
    assert_eq!(g.taraf_eksik, 2, "A (kimliksiz borçlu) + D (tarafsız)");
    assert!(g.rapor.iter().any(|x| x.contains("Taraf bilgilerini güncelle")), "{:?}", g.rapor);
    drop(g);
    assert!(!kapanan_istegi(&u), "eksik listede kapanan kontrolü yapılmaz");
    assert_eq!(taraf_istegi(&u), 0, "dosya listesi düğmesi taraf istemez");
    assert_eq!((durum_kod(c, b), durum_kod(c, cc)), (0, 0));

    // 2) Dosya listesi, tarih aralıklı: UYAP'a aralık gider, kapanan kontrolü yapılmaz
    let (t, u, r) = calistir(Mod::Liste, false, NaiveDate::from_ymd_opt(2026, 11, 1));
    r.unwrap();
    assert!(t.inner.lock().unwrap().tam);
    assert_eq!(u.cagrilar.lock().unwrap()[0].1["dosyaAcilisTarihiStart"], "01.11.2026");
    assert!(!kapanan_istegi(&u), "tarih aralığında kapanan kontrolü yapılmaz");
    assert!(t.inner.lock().unwrap().rapor.iter().any(|x| x.contains("kapanan dosya kontrolü yapılmaz")));
    assert_eq!(durum_kod(c, b), 0);

    // 3) Dosya listesi, tam: B kapandı + Pasif, C'ye dokunulmadı
    let (t, u, r) = calistir(Mod::Liste, false, None);
    r.unwrap();
    let g = t.inner.lock().unwrap();
    assert_eq!((g.alinan, g.yeni_dosya, g.kapandi, g.bulunamadi, g.aday), (3, 0, 1, 1, 2));
    assert!(g.rapor[0].starts_with('✔'), "{:?}", g.rapor);
    assert!(g.rapor.iter().any(|x| x.contains("Veritabanında 4 açık")), "{:?}", g.rapor);
    assert!(g.rapor.iter().any(|x| x.starts_with("Durum hesabı")), "{:?}", g.rapor);
    assert_eq!(g.asama, "bitti");
    drop(g);
    assert_eq!(taraf_istegi(&u), 0);
    assert_eq!((durum_kod(c, b), durum_kod(c, cc)), (1, 0));
    let islem: String = c.query_one("SELECT islem_durumu FROM dosya WHERE id = $1", &[&b]).unwrap().get(0);
    assert_eq!(islem, "pasif");

    // 4) Taraf bilgileri: D (tarafsız) iki uç; A (kimliksiz borçlu) yalnız borçlu listesi; E hata → uyarı; kapanan YOK
    let (t, u, r) = calistir(Mod::Taraf, false, None);
    r.unwrap();
    let g = t.inner.lock().unwrap();
    assert_eq!(g.taraf_cekilen, 2, "{:?}", g.uyarilar);
    assert!(g.uyarilar.iter().any(|x| x.contains("2026/21") && x.contains("taraflar alınamadı")), "{:?}", g.uyarilar);
    drop(g);
    assert!(!kapanan_istegi(&u));
    let cagri: Vec<(String, String)> = u.cagrilar.lock().unwrap().iter().filter(|(uc, _)| uc != ARAMA)
        .map(|(uc, g)| (uc.clone(), g["dosyaId"].as_str().unwrap_or("").to_string())).collect();
    assert!(cagri.contains(&(BORCLU_LISTESI.into(), "u-a".into())) && !cagri.contains(&(TARAF_BILGILERI.into(), "u-a".into())), "{cagri:?}");
    assert!(cagri.contains(&(TARAF_BILGILERI.into(), "u-d".into())), "{cagri:?}");
    let d: i64 = c.query_one("SELECT id FROM dosya WHERE dosya_no = '2026/20'", &[]).unwrap().get(0);
    let taraflar = |c: &mut postgres::Client, id: i64| -> Vec<(String, Option<String>, bool)> {
        c.query(
            "SELECT dt.rol, t.tckn, dt.uyapta_gorulmedi IS NOT NULL FROM dosya_taraf dt JOIN taraf t ON t.id = dt.taraf_id
              WHERE dt.dosya_id = $1 ORDER BY dt.rol",
            &[&id],
        )
        .unwrap()
        .iter()
        .map(|s| (s.get(0), s.get(1), s.get(2)))
        .collect()
    };
    assert_eq!(taraflar(c, d), vec![("alacakli".into(), None, false), ("borclu".into(), Some("12345678901".into()), false)]);
    assert_eq!(taraflar(c, a), vec![("alacakli".into(), None, false), ("borclu".into(), Some("10987654321".into()), false)], "kimlik eklendi, borçlu çoğalmadı");

    // 5) Taraf bilgileri yeniden: tarafı tam dosyalara (A, D) istek atılmaz
    let (_, u, r) = calistir(Mod::Taraf, false, None);
    r.unwrap();
    assert!(!u.cagrilar.lock().unwrap().iter().any(|(uc, g)| uc != ARAMA && (g["dosyaId"] == "u-a" || g["dosyaId"] == "u-d")));

    c.batch_execute("DELETE FROM dosya; DELETE FROM taraf; DELETE FROM vekil; DELETE FROM birim").unwrap();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sayfa_ve_toplam() {
        assert!(sayfa_gecersiz(&None).is_some());
        assert!(sayfa_gecersiz(&Some(json!({"errorCode": "X", "error": "olmaz"}))).unwrap().contains("olmaz"));
        assert!(sayfa_gecersiz(&Some(json!({"error": "Eş zamanlı olarak birden fazla sorgulama yapamazsınız!"}))).unwrap().contains("eş zamanlı"));
        assert!(sayfa_gecersiz(&Some(json!([[{"dosyaId": "1"}], 1]))).is_none());
        assert!(sayfa_gecersiz(&Some(json!([]))).is_none(), "boş liste geçerli son sayfadır");
        assert_eq!(uyap_toplami(&json!([[{"a": 1}], 1234])), Some(1234));
        assert_eq!(uyap_toplami(&json!([[], "56"])), Some(56));
        assert_eq!(uyap_toplami(&json!([{"a": 1}, {"b": 2}])), None);
        assert_eq!(uyap_toplami(&json!([[], true])), None);
    }

    #[test]
    fn sayim_ve_fren() {
        assert!(sayim_satiri("İcra", 5, Some(5), true).0.starts_with('✔'));
        let (m, eksik) = sayim_satiri("İcra", 4, Some(5), true);
        assert!(eksik && m.contains("1 fark"));
        assert!(sayim_satiri("İcra", 4, None, false).1);
        assert!(!sayim_satiri("İcra", 4, None, true).1);
        assert!(!fren_asildi(50, 0) && fren_asildi(51, 0));
        assert!(!fren_asildi(300, 6000) && fren_asildi(6001, 6000));
    }

    #[test]
    fn liste_govdesi_ve_kapsam() {
        let k = Kapsam { birim_id: Some("9009".into()), birim_adi: "İzmir 9. İcra Dairesi".into(),
                         acilis_bas: NaiveDate::from_ymd_opt(2026, 11, 1), acilis_son: NaiveDate::from_ymd_opt(2026, 11, 30) };
        let g = liste_govdesi(3, 500, &k);
        assert_eq!((g["pageNumber"].clone(), g["birimId"].clone(), g["birimTuru3"].clone()), (json!(3), json!("9009"), json!("2")));
        assert_eq!((g["dosyaAcilisTarihiStart"].clone(), g["dosyaAcilisTarihiEnd"].clone()), (json!("01.11.2026"), json!("30.11.2026")));
        assert_eq!(k.metin(), "İzmir 9. İcra Dairesi · açılış 01.11.2026–30.11.2026");
        let bos = Kapsam::default();
        let g = liste_govdesi(1, 500, &bos);
        assert!(g.get("birimId").is_none() && g.get("dosyaAcilisTarihiStart").is_none());
        assert_eq!(bos.metin(), "Bütün açık dosyalar");
    }

    #[test]
    fn baslatma_govdesi() {
        let (m, k) = baslatma_istegi(&json!({})).unwrap();
        assert_eq!((m, k.birim_id.clone(), k.tarihli()), (Mod::Liste, None, false));
        let (m, k) = baslatma_istegi(&json!({"mod": "taraf", "birim_id": "2002", "birim_adi": "Ankara 3. İcra Dairesi",
                                              "acilis_bas": "2026-11-01", "acilis_son": "30.11.2026"})).unwrap();
        assert_eq!((m, k.birim_id.as_deref(), k.acilis_bas, k.acilis_son), (Mod::Taraf, Some("2002"),
                   NaiveDate::from_ymd_opt(2026, 11, 1), NaiveDate::from_ymd_opt(2026, 11, 30)));
        assert!(baslatma_istegi(&json!({"mod": "olmaz"})).is_err());
        assert!(baslatma_istegi(&json!({"birim_id": "12a"})).is_err());
        assert!(baslatma_istegi(&json!({"acilis_bas": "2026-12-01", "acilis_son": "2026-11-01"})).unwrap_err().contains("ters"));
        assert!(baslatma_istegi(&json!({"acilis_bas": "olmaz"})).is_err());
    }
}
