//! Gerekçeli Kararlar (`uyap_core/gerekceli_karar.py` portu) — hukuk dosyalarında gerekçeli kararı
//! bulup UDF + PDF indirir, kontrol edilen dosyaların Excel listesini hazırlar.
//!
//! Ölçü Python sürümüdür: sayfadaki panel (uyap_proxy.rs "GEREKÇELİ KARARLAR") o sürümün adreslerini ve
//! veri biçimlerini konuşur. Akış: liste → (dosya seçimi) → kontrol → (karar seçimi) → indirme.
//!
//!   POST   /__uyap_agent__/gerekceli               → 201 {tarama}  (hukuk dosyalarını listeler)
//!   GET    /__uyap_agent__/gerekceli               → {tarama|null} (süren/son tarama)
//!   GET    /__uyap_agent__/gerekceli/<id>          → {tarama}
//!   GET    /__uyap_agent__/gerekceli/<id>/liste    → {turler, dosyalar}
//!   POST   /__uyap_agent__/gerekceli/<id>/baslat   {"dosyalar": [dosyaId…]} → {tarama}  (yalnız kontrol)
//!   GET    /__uyap_agent__/gerekceli/<id>/sonuclar → {dosyalar (kararı olanlar), karar_yok, …}
//!   POST   /__uyap_agent__/gerekceli/<id>/indir    {"dosyalar": [dosyaId…]|[]} → {tarama}
//!   POST   /__uyap_agent__/gerekceli/<id>/captcha  {"token"|null} → {tarama}
//!   POST   /__uyap_agent__/gerekceli/<id>/iptal    → {tarama}
//!   GET    /__uyap_agent__/gerekceli/<id>/dosya    → application/zip (kararlar + Excel)
//!   GET    /__uyap_agent__/gerekceli/<id>/excel    → .xlsx (kontrol edilen dosyaların listesi)
//!
//! UYAP'A SAYGI (evrak_toplu ile aynı kapıdan): her istek `evrak_toplu::_uyap`'tan geçer (eşzamanlı istek
//! yok, kullanıcının işlemleri önce, bağlantı koparsa geri çekilme, doğrulamayı KULLANICI çözer); sabit ve
//! yavaş hız (ISTEK_ARASI_SN), her MOLA_HER_DOSYA dosyada MOLA_SN mola; Tüm Evrakı İndir ile aynı sıra
//! kilidi. Kaldığı yer `_durum.json`'da: inmiş karar yeniden indirilmez, kararı olmayan dosya
//! YENIDEN_KONTROL_SN geçmeden yeniden sorulmaz.
//!
//! PDF'e çevirme `evrak_toplu::_pdfe_cevir` ile: önce Python/PyMuPDF (py_cevir.rs), yoksa Rust.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::io::{Cursor, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex as StdMutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use once_cell::sync::Lazy;
use serde_json::{json, Map, Value};
use zip::write::SimpleFileOptions;
use zip::{CompressionMethod, ZipWriter};

use crate::evrak_toplu::{
    self, Indirme, _pdfe_cevir, _tur_bul, kayit_koku, BITTI, CALISIYOR, CAPTCHA, CIKTI_IKISI,
    DOC_VIEWER_HAZIRLIK, EVRAK_ICERIK, HATA, IPTAL, KAPSAM_TUM, SIRADA,
};

pub const ISTEK_ARASI_SN: f64 = 5.0;
pub const YAVAS_EN_COK_SN: f64 = 20.0;
pub const MOLA_HER_DOSYA: usize = 25;
pub const MOLA_SN: u64 = 60;
pub const YENIDEN_KONTROL_SN: f64 = 7.0 * 86400.0;
pub const ARDISIK_AG_HATASI_SINIRI: usize = 3;
pub const ZIP_SAKLAMA_SN: f64 = 30.0 * 60.0;

pub const HUKUK: &str = "1";
pub const DURUMLAR: &[(i64, &str)] = &[(0, "Açık"), (1, "Kapalı")];
pub const BIRIM_TURLERI: &str = "yargiBirimleriSorgula_brd.ajx";
pub const ARAMA: &str = "search_phrase_detayli.ajx";
/// [{"adi", "rol": "Davacı"|"Davalı"|…, "vekil": "[AD SOYAD, …]" (yoksa anahtar yok), "kisiKurum"}]
pub const TARAF_BILGILERI: &str = "dosya_taraf_bilgileri_brd.ajx";
pub const SAYFA_BOYU: usize = 500;
pub const SAYFA_EN_COK: usize = 40;

pub fn kok_klasor() -> PathBuf {
    kayit_koku().join("Gerekçeli Kararlar")
}

/// Liste hazır, kullanıcının taranacak dosyaları işaretlemesi bekleniyor.
pub const SECIM: &str = "secim";
/// Kontrol bitti, kullanıcının indirilecekleri işaretlemesi bekleniyor.
pub const KARAR_SECIM: &str = "karar_secim";
const SON: &[&str] = &[BITTI, HATA, IPTAL];
const BEKLEYEN: &[&str] = &[SECIM, KARAR_SECIM];
pub const _DISLA: &[&str] = &["tebliğ", "teblig", "mazbata", "zarf", "şerh", "serh"];

const IPTAL_HATASI: &str = "__IPTAL__";
const AG_ONEKI: &str = "AG_HATASI:";

pub const EXCEL_BASLIKLARI: &[&str] = &[
    "Mahkeme Türü",
    "Birim",
    "Dosya No",
    "Dosya Türü",
    "Dosya Durumu",
    "Açılış Tarihi",
    "Davacı",
    "Davalı",
    "Diğer Taraflar",
    "Gerekçeli Karar",
    "Karar Dosyaları",
];
pub const EXCEL_GENISLIK: &[u32] = &[22, 34, 12, 18, 12, 13, 40, 40, 40, 16, 50];
const XLSX_TURU: &str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

pub fn tr_kucuk(s: &str) -> String {
    s.chars()
        .map(|c| match c {
            'İ' => 'i',
            'I' => 'ı',
            'Ç' => 'ç',
            'Ğ' => 'ğ',
            'Ö' => 'ö',
            'Ş' => 'ş',
            'Ü' => 'ü',
            'Â' | 'â' => 'a',
            'Î' | 'î' => 'i',
            'Û' | 'û' => 'u',
            other => other,
        })
        .collect::<String>()
        .to_lowercase()
}

/// Evrak türü gerekçeli kararın kendisi mi? (tebliğ mazbatası, zarf, şerh vb. değil)
pub fn gerekceli_mi(tur: &str) -> bool {
    let t = tr_kucuk(tur);
    (t.contains("gerekçeli karar") || t.contains("gerekceli karar"))
        && !_DISLA.iter().any(|d| t.contains(d))
}

/// `search_phrase_detayli` yanıtı → kayıt listesi ([kayıtlar, toplam] ya da düz liste).
pub fn kayitlar_listesi(veri: &Value) -> Vec<Value> {
    let mut v = veri.clone();
    if let Some(obj) = veri.as_object() {
        let mut found = None;
        for k in ["data", "veri", "list", "rows", "dosyalar"] {
            if let Some(arr) = obj.get(k).and_then(|x| x.as_array()) {
                found = Some(Value::Array(arr.clone()));
                break;
            }
        }
        v = match found {
            Some(arr) => arr,
            None => return Vec::new(),
        };
    }
    let Some(arr) = v.as_array() else {
        return Vec::new();
    };
    let inner = if let Some(first) = arr.first().and_then(|x| x.as_array()) {
        first
    } else {
        arr
    };
    inner.iter().filter(|r| r.is_object()).cloned().collect()
}

/// Python `str(x or "")`: metin ya da sayı → metin; yoksa boş.
fn metin(v: Option<&Value>) -> String {
    match v {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) => n.to_string(),
        Some(Value::Bool(true)) => "True".to_string(),
        _ => String::new(),
    }
}

/// Dosya kaydının (liste aşamasında kurulan sözlük) metin alanı.
fn ds(d: &Value, anahtar: &str) -> String {
    metin(d.get(anahtar))
}

fn hata_var(veri: &Option<Value>) -> Option<String> {
    let o = veri.as_ref()?.as_object()?;
    for k in ["error", "errorCode"] {
        let s = metin(o.get(k));
        if !s.is_empty() {
            return Some(s);
        }
    }
    None
}

/// UYAP tarih alanı ({"date": {"day","month","year"}} ya da metin) → GG.AA.YYYY
fn tarih(v: Option<&Value>) -> String {
    match v {
        Some(Value::Object(o)) => {
            let sayi = |k: &str| -> Option<i64> {
                let x = o.get("date")?.get(k)?;
                x.as_i64().or_else(|| x.as_str()?.trim().parse().ok())
            };
            match (sayi("day"), sayi("month"), sayi("year")) {
                (Some(g), Some(a), Some(y)) => format!("{:02}.{:02}.{:04}", g, a, y),
                _ => String::new(),
            }
        }
        other => metin(other),
    }
}

/// Windows dosya adında yasak karakterleri "-" yapar, uzun adı kırpar — Python `_guvenli_ad` ile
/// harfi harfine aynı (ofiste önceden inmiş kararlar aynı adla bulunabilsin diye
/// `evrak_toplu::_guvenli_ad` kullanılmaz; o boşluk koyar).
fn guvenli_ad(s: &str, en_cok: usize) -> String {
    let temiz: String = s
        .chars()
        .map(|c| match c {
            '\\' | '/' | ':' | '*' | '?' | '"' | '<' | '>' | '|' => '-',
            c if (c as u32) < 0x20 => '-',
            c => c,
        })
        .collect();
    let temiz = temiz.trim().trim_end_matches(['.', ' ']);
    let kirpik: String = temiz.chars().take(en_cok).collect();
    if kirpik.is_empty() {
        "adsiz".to_string()
    } else {
        kirpik
    }
}

/// dosyaId oturuma bağlı (her girişte değişir): kalıcı kayıt birim + dosya no ile.
fn anahtar(d: &Value) -> String {
    let birim_id = ds(d, "birim_id");
    format!(
        "{}|{}",
        if birim_id.is_empty() { ds(d, "birim") } else { birim_id },
        ds(d, "no")
    )
}

fn klasor(d: &Value) -> PathBuf {
    kok_klasor().join(guvenli_ad(
        &format!("{} {}", ds(d, "birim"), ds(d, "no").replace('/', "-")),
        120,
    ))
}

fn uzanti(tur: &str) -> &'static str {
    match tur {
        "tiff" => ".tiff",
        "jpeg" => ".jpg",
        "png" => ".png",
        "udf" => ".udf",
        "zip" => ".zip",
        "html" => ".html",
        _ => ".bin",
    }
}

// ── Kalıcı durum (_durum.json) ─────────────────────────────────────────────────────────
fn durum_yolu() -> PathBuf {
    kok_klasor().join("_durum.json")
}

fn durum_oku() -> Map<String, Value> {
    fs::read(durum_yolu())
        .ok()
        .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default()
}

fn durum_yaz(durum: &Map<String, Value>) -> Result<(), String> {
    fs::create_dir_all(kok_klasor()).map_err(|e| e.to_string())?;
    let yol = durum_yolu();
    let gecici = yol.with_extension("json.tmp");
    let veri = serde_json::to_vec(durum).map_err(|e| e.to_string())?;
    fs::write(&gecici, veri).map_err(|e| e.to_string())?;
    fs::rename(&gecici, &yol).map_err(|e| e.to_string())
}

// ── Tarama kaydı ───────────────────────────────────────────────────────────────────────
#[derive(Debug, Clone)]
pub struct Karar {
    pub evrak_id: String,
    pub tur: String,
    /// Kayıt adı (uzantısız).
    pub ad: String,
    /// Kontrol sırasında ofiste zaten bulunan dosyalar (yeniden indirilmez).
    pub mevcut: Vec<PathBuf>,
    /// İndirme sonunda bu karara ait dosyalar.
    pub yollar: Vec<PathBuf>,
}

#[derive(Debug, Clone)]
pub struct Sonuc {
    pub sonuc: String,
    pub kararlar: Vec<Karar>,
    /// [{"rol","ad","vekil"}]; alınamadıysa None.
    pub taraflar: Option<Vec<Value>>,
    pub indirilecek: bool,
}

#[derive(Debug, Default)]
pub struct TaramaInner {
    pub durumlar: Vec<(i64, &'static str)>,
    pub asama: String,
    /// [{"kod", "ad"}]
    pub turler: Vec<Value>,
    pub dosya_sira: Vec<String>,
    /// dosyaId → {"id","no","birim","birim_id","tur","tur_kod","arama","durum","dosya_tur","acilis"}
    pub dosyalar: HashMap<String, Value>,
    pub sonuc_sira: Vec<String>,
    pub sonuclar: HashMap<String, Sonuc>,
    pub i: usize,
    pub toplam: usize,
    /// Kontrolde bulunan karar.
    pub bulunan: usize,
    /// Bu taramada indirilen karar.
    pub yeni: usize,
    /// Yakın zamanda kontrol edilip kararı olmayan.
    pub atlanan: usize,
    pub mola_bitis: Option<f64>,
    /// ZIP'e girecek dosya yolları.
    pub cikti: Vec<PathBuf>,
    pub excel_yol: Option<PathBuf>,
    pub uyarilar: Vec<String>,
}

/// `evrak_toplu::Indirme`'nin doğrulama/iptal/yavaşlatma davranışını aynen kullanır (durum, mesaj,
/// captcha, çıktı yolu orada); yalnız hız (5 sn) ve akış farklıdır.
pub struct Tarama {
    pub ind: Arc<Indirme>,
    pub inner: StdMutex<TaramaInner>,
}

impl Tarama {
    pub fn new(durumlar: Vec<i64>) -> Arc<Self> {
        let ind = Indirme::new(
            String::new(),
            "Gerekçeli Kararlar".to_string(),
            CIKTI_IKISI.to_string(),
            KAPSAM_TUM.to_string(),
            None,
        );
        {
            let mut g = ind.inner.lock().unwrap();
            g.istek_arasi_sn = ISTEK_ARASI_SN;
            g.yavas_en_cok_sn = YAVAS_EN_COK_SN;
            g.mesaj = "Hukuk dosyaları listeleniyor…".to_string();
        }
        Arc::new(Self {
            ind,
            inner: StdMutex::new(TaramaInner {
                durumlar: DURUMLAR
                    .iter()
                    .filter(|(k, _)| durumlar.contains(k))
                    .copied()
                    .collect(),
                asama: "liste".to_string(),
                ..Default::default()
            }),
        })
    }

    pub fn id(&self) -> String {
        self.ind.inner.lock().unwrap().id.clone()
    }

    pub fn durum(&self) -> String {
        self.ind.inner.lock().unwrap().durum.clone()
    }

    fn mesaj(&self) -> String {
        self.ind.inner.lock().unwrap().mesaj.clone()
    }

    fn mesaj_yaz(&self, m: String) {
        self.ind.inner.lock().unwrap().mesaj = m;
    }

    fn durum_mesaj(&self, durum: &str, mesaj: String) {
        let mut g = self.ind.inner.lock().unwrap();
        g.durum = durum.to_string();
        g.mesaj = mesaj;
    }

    fn uyari(&self, u: String) {
        self.inner.lock().unwrap().uyarilar.push(u);
    }

    fn sonuc_ekle(&self, did: &str, s: Sonuc) {
        let mut g = self.inner.lock().unwrap();
        if !g.sonuclar.contains_key(did) {
            g.sonuc_sira.push(did.to_string());
        }
        g.sonuclar.insert(did.to_string(), s);
    }

    pub fn sozluk(&self) -> Value {
        let (id, durum, mesaj, captcha, cikti_yol) = {
            let g = self.ind.inner.lock().unwrap();
            (
                g.id.clone(),
                g.durum.clone(),
                g.mesaj.clone(),
                g.captcha.clone(),
                g.cikti_yol.clone(),
            )
        };
        let aralik = self.ind.aralik();
        let g = self.inner.lock().unwrap();
        let surer = (g.asama == "kontrol" || g.asama == "indirme") && !SON.contains(&durum.as_str());
        let kalan_sn = if surer {
            let kalan = g.toplam.saturating_sub(g.i);
            // kontrol: dosya başına evrak listesi (2) + taraflar (1); indirme: karar başına 2 istek.
            let istek = if g.asama == "kontrol" { 3.0 } else { 2.0 };
            Some(
                (kalan as f64 * istek * aralik
                    + ((kalan / MOLA_HER_DOSYA) as u64 * MOLA_SN) as f64) as i64,
            )
        } else {
            None
        };
        let uyarilar: Vec<&String> = g.uyarilar.iter().skip(g.uyarilar.len().saturating_sub(20)).collect();
        json!({
            "id": id,
            "durum": durum,
            "asama": g.asama,
            "mesaj": mesaj,
            "i": g.i,
            "toplam": g.toplam,
            "bulunan": g.bulunan,
            "yeni": g.yeni,
            "atlanan": g.atlanan,
            "dosya_sayisi": g.dosyalar.len(),
            "kontrol_edilen": g.sonuclar.len(),
            "karar_dosyasi": g.sonuclar.values().filter(|s| !s.kararlar.is_empty()).count(),
            "durumlar": g.durumlar.iter().map(|(_, ad)| *ad).collect::<Vec<_>>(),
            "kalan_sn": kalan_sn,
            "aralik_sn": aralik,
            "mola_kalan_sn": g.mola_bitis.map(|b| (b - now_secs()).max(0.0) as i64),
            "uyarilar": uyarilar,
            "klasor": kok_klasor().to_string_lossy(),
            "dosya_hazir": cikti_yol.as_ref().map(|p| Path::new(p).exists()).unwrap_or(false),
            "excel_hazir": g.excel_yol.as_ref().map(|p| p.exists()).unwrap_or(false),
            "captcha": captcha,
        })
    }

    pub fn liste_sozluk(&self) -> Value {
        let g = self.inner.lock().unwrap();
        let dosyalar: Vec<&Value> = g.dosya_sira.iter().filter_map(|id| g.dosyalar.get(id)).collect();
        json!({"turler": g.turler, "dosyalar": dosyalar})
    }

    /// Kontrol sonucu: kararı bulunan dosyalar (indirme seçimi için) + diğerlerinin sayısı.
    pub fn sonuclar_sozluk(&self) -> Value {
        let g = self.inner.lock().unwrap();
        let mut var = Vec::new();
        for did in &g.sonuc_sira {
            let (Some(s), Some(d)) = (g.sonuclar.get(did), g.dosyalar.get(did)) else {
                continue;
            };
            if s.kararlar.is_empty() {
                continue;
            }
            let mut kayit = d.clone();
            if let Some(o) = kayit.as_object_mut() {
                o.insert("sonuc".to_string(), json!(s.sonuc));
                o.insert(
                    "kararlar".to_string(),
                    s.kararlar
                        .iter()
                        .map(|k| json!({"ad": k.ad, "inmis": !k.mevcut.is_empty()}))
                        .collect(),
                );
            }
            var.push(kayit);
        }
        json!({
            "dosyalar": var,
            "karar_yok": g.sonuclar.values()
                .filter(|s| s.kararlar.is_empty() && s.sonuc.starts_with("Yok")).count(),
            "kontrol_edilemedi": g.sonuclar.values()
                .filter(|s| s.sonuc.starts_with("Kontrol edilemedi")).count(),
        })
    }
}

static KAYITLAR: Lazy<StdMutex<HashMap<String, Arc<Tarama>>>> =
    Lazy::new(|| StdMutex::new(HashMap::new()));

type Yanit = (u16, HashMap<String, String>, Vec<u8>, Option<Value>);

async fn uyap(
    t: &Tarama,
    method: &str,
    path: &str,
    govde: Option<&Value>,
    params: Option<&HashMap<String, String>>,
) -> Result<Yanit, String> {
    evrak_toplu::_uyap(&t.ind, method, path, govde, params).await
}

// ── 1. aşama: liste ────────────────────────────────────────────────────────────────────
/// Hukuk yargı türünün mahkeme türleri: (kod, ad).
async fn mahkeme_turleri(t: &Tarama) -> Result<Vec<(String, String)>, String> {
    let (_, _, _, veri) = uyap(t, "POST", BIRIM_TURLERI, Some(&json!({"yargiTuru": HUKUK})), None).await?;
    Ok(turleri_ayikla(&veri))
}

fn turleri_ayikla(veri: &Option<Value>) -> Vec<(String, String)> {
    veri.as_ref()
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|b| {
                    let tablo = metin(b.get("tablo"));
                    if !b.is_object() || tablo.is_empty() {
                        return None;
                    }
                    let ad = metin(b.get("kod"));
                    Some((tablo.clone(), if ad.is_empty() { tablo } else { ad }))
                })
                .collect()
        })
        .unwrap_or_default()
}

async fn ara(t: &Tarama, tur_kod: &str, tur_ad: &str, durum_kod: i64) -> Result<Vec<Value>, String> {
    let mut tum = Vec::new();
    for sayfa in 1..=SAYFA_EN_COK {
        let govde = json!({
            "dosyaDurumKod": durum_kod,
            "pageSize": SAYFA_BOYU,
            "pageNumber": sayfa,
            "birimTuru2": tur_kod,
            "birimTuru3": HUKUK,
        });
        let (_, _, _, veri) = uyap(t, "POST", ARAMA, Some(&govde), None).await?;
        if let Some(h) = hata_var(&veri) {
            t.uyari(format!("{}: {}", tur_ad, h));
            break;
        }
        let parca = veri.as_ref().map(kayitlar_listesi).unwrap_or_default();
        let n = parca.len();
        tum.extend(parca);
        if n < SAYFA_BOYU {
            break;
        }
    }
    Ok(tum)
}

/// Arama kaydı → panelin ve Excel'in kullandığı dosya sözlüğü.
fn dosya_kaydi(r: &Value, did: &str, tur_kod: &str, tur_ad: &str, durum_ad: &str) -> Value {
    // "Açık" aramasında Karara Çıkmış / İstinafta dosyalar da gelir: UYAP'ın kayıttaki kendi durum
    // metni gösterilir (arama: hangi aramadan geldi).
    let durum = metin(r.get("dosyaDurum"));
    let acilis = match r.get("dosyaAcilisTarihi") {
        Some(v) if !v.is_null() && v.as_str() != Some("") => Some(v),
        _ => r.get("acilisTarihi"),
    };
    json!({
        "id": did,
        "no": metin(r.get("dosyaNo")),
        "birim": metin(r.get("birimAdi")),
        "birim_id": metin(r.get("birimId")),
        "tur": tur_ad,
        "tur_kod": tur_kod,
        "arama": durum_ad,
        "durum": if durum.is_empty() { durum_ad.to_string() } else { durum },
        "dosya_tur": metin(r.get("dosyaTur")),
        "acilis": tarih(acilis),
    })
}

async fn listele(t: &Tarama) -> Result<(), String> {
    let turler = mahkeme_turleri(t).await?;
    if turler.is_empty() {
        return Err("UYAP hukuk mahkemesi türlerini döndürmedi.".to_string());
    }
    let durumlar = {
        let mut g = t.inner.lock().unwrap();
        g.turler = turler.iter().map(|(k, a)| json!({"kod": k, "ad": a})).collect();
        g.durumlar.clone()
    };
    for (n, (tur_kod, tur_ad)) in turler.iter().enumerate() {
        for (durum_kod, durum_ad) in &durumlar {
            let sayi = t.inner.lock().unwrap().dosyalar.len();
            t.mesaj_yaz(format!(
                "Listeleniyor: {} ({}) — {}/{} tür, {} dosya",
                tur_ad,
                durum_ad,
                n + 1,
                turler.len(),
                sayi
            ));
            let kayitlar = ara(t, tur_kod, tur_ad, *durum_kod).await?;
            let mut g = t.inner.lock().unwrap();
            for r in kayitlar {
                let did = metin(r.get("dosyaId"));
                if did.is_empty() || g.dosyalar.contains_key(&did) {
                    continue;
                }
                let kayit = dosya_kaydi(&r, &did, tur_kod, tur_ad, durum_ad);
                g.dosya_sira.push(did.clone());
                g.dosyalar.insert(did, kayit);
            }
        }
    }
    let sayi = {
        let mut g = t.inner.lock().unwrap();
        g.asama = "secim".to_string();
        g.dosyalar.len()
    };
    t.durum_mesaj(SECIM, format!("{} hukuk dosyası bulundu. İşaretleyip taramayı başlatın.", sayi));
    Ok(())
}

// ── 2. aşama: kontrol ──────────────────────────────────────────────────────────────────
async fn mola(t: &Tarama) -> Result<(), String> {
    t.inner.lock().unwrap().mola_bitis = Some(now_secs() + MOLA_SN as f64);
    let onceki = t.mesaj();
    t.mesaj_yaz(format!("Mola: UYAP'ı yormamak için {} sn bekleniyor…", MOLA_SN));
    let mut sonuc = Ok(());
    for _ in 0..MOLA_SN {
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

fn taraf_listesi(veri: &Option<Value>) -> Option<Vec<Value>> {
    let arr = veri.as_ref()?.as_array()?;
    Some(
        arr.iter()
            .filter(|x| x.is_object())
            .map(|x| {
                json!({
                    "rol": metin(x.get("rol")).trim(),
                    "ad": metin(x.get("adi")).trim(),
                    "vekil": metin(x.get("vekil")).trim_matches(['[', ']', ' ']),
                })
            })
            .collect(),
    )
}

/// Dosyanın tarafları; önceki taramadan `_durum.json`'da varsa UYAP'a yeniden sorulmaz.
/// Alınamazsa None (Excel'de "alınamadı" yazar, önbelleğe girmez).
async fn taraflar_al(t: &Tarama, d: &Value, kayit: &Value) -> Result<Option<Vec<Value>>, String> {
    if let Some(arr) = kayit.get("taraflar").and_then(|v| v.as_array()) {
        return Ok(Some(arr.clone()));
    }
    match uyap(t, "POST", TARAF_BILGILERI, Some(&json!({"dosyaId": ds(d, "id")})), None).await {
        Ok((_, _, _, veri)) => Ok(taraf_listesi(&veri)),
        Err(e) => match e.strip_prefix(AG_ONEKI) {
            Some(ag) => {
                t.uyari(format!("{} {}: taraflar alınamadı ({})", ds(d, "birim"), ds(d, "no"), ag));
                Ok(None)
            }
            None => Err(e),
        },
    }
}

fn kaydet(durum: &mut Map<String, Value>, anahtar: &str, karar_yok: bool, taraflar: Option<&Vec<Value>>) {
    let mut kayit = json!({"zaman": now_secs(), "karar_yok": karar_yok});
    if let Some(tl) = taraflar {
        kayit["taraflar"] = Value::Array(tl.clone());
    }
    durum.insert(anahtar.to_string(), kayit);
}

/// Bulunan kararların kayıt adı + ofiste zaten inmiş dosyaları (yeniden indirilmez).
fn karar_bilgileri(d: &Value, kararlar: &[Value]) -> Vec<Karar> {
    let kl = klasor(d);
    kararlar
        .iter()
        .enumerate()
        .map(|(n, e)| {
            let no = metin(e.get("birimEvrakNo"));
            let tur = metin(e.get("tur"));
            let ek = if !no.is_empty() {
                format!(" ({})", guvenli_ad(&no, 30))
            } else if kararlar.len() > 1 {
                format!(" ({})", n + 1)
            } else {
                String::new()
            };
            let ad = guvenli_ad(if tur.is_empty() { "Gerekçeli Karar" } else { &tur }, 80) + &ek;
            let mevcut = [".udf", ".pdf"]
                .iter()
                .map(|u| kl.join(format!("{}{}", ad, u)))
                .filter(|p| p.exists())
                .collect();
            Karar { evrak_id: metin(e.get("evrakId")), tur, ad, mevcut, yollar: Vec::new() }
        })
        .collect()
}

/// İşaretli dosyalarda yalnız KONTROL: evrak listesi + taraflar. Hiçbir şey indirmez; sonuç
/// `sonuclar`'a yazılır, indirilecekleri kullanıcı sonra seçer.
async fn kontrol(t: &Tarama, secilen: Vec<Value>) -> Result<(), String> {
    {
        let mut g = t.inner.lock().unwrap();
        g.asama = "kontrol".to_string();
        g.toplam = secilen.len();
        g.i = 0;
    }
    let mut durum = durum_oku();
    let mut ardisik_ag = 0usize;
    for (idx, d) in secilen.iter().enumerate() {
        let sira = idx + 1;
        t.inner.lock().unwrap().i = sira;
        if sira > 1 && (sira - 1) % MOLA_HER_DOSYA == 0 {
            mola(t).await?;
        }
        let (did, birim, no) = (ds(d, "id"), ds(d, "birim"), ds(d, "no"));
        let anahtar = anahtar(d);
        let kayit = durum.get(&anahtar).cloned().unwrap_or_else(|| json!({}));
        t.ind.inner.lock().unwrap().dosya_id = did.clone();

        let karar_yok = kayit.get("karar_yok").and_then(|v| v.as_bool()).unwrap_or(false);
        let zaman = kayit.get("zaman").and_then(|v| v.as_f64()).unwrap_or(0.0);
        if karar_yok && now_secs() - zaman < YENIDEN_KONTROL_SN {
            t.inner.lock().unwrap().atlanan += 1;
            let taraflar = taraflar_al(t, d, &kayit).await?;
            if let (Some(tl), None) = (&taraflar, kayit.get("taraflar")) {
                if let Some(o) = durum.get_mut(&anahtar).and_then(|v| v.as_object_mut()) {
                    o.insert("taraflar".to_string(), Value::Array(tl.clone()));
                }
                let _ = durum_yaz(&durum);
            }
            t.sonuc_ekle(
                &did,
                Sonuc {
                    sonuc: "Yok (yakın zamanda kontrol edildi)".to_string(),
                    kararlar: Vec::new(),
                    taraflar,
                    indirilecek: false,
                },
            );
            continue;
        }

        t.mesaj_yaz(format!("{}/{}: {} {} kontrol ediliyor…", sira, secilen.len(), birim, no));
        let evraklar = match evrak_toplu::_evrak_listesi(&t.ind).await {
            Ok(e) => e,
            Err(e) => {
                let Some(ag) = e.strip_prefix(AG_ONEKI) else {
                    return Err(e);
                };
                t.uyari(format!("{} {}: bağlantı koptu ({})", birim, no, ag));
                t.sonuc_ekle(
                    &did,
                    Sonuc {
                        sonuc: "Kontrol edilemedi (bağlantı)".to_string(),
                        kararlar: Vec::new(),
                        taraflar: kayit.get("taraflar").and_then(|v| v.as_array()).cloned(),
                        indirilecek: false,
                    },
                );
                ardisik_ag += 1;
                if ardisik_ag >= ARDISIK_AG_HATASI_SINIRI {
                    return Err(format!("{}art arda {} dosyada bağlantı koptu", AG_ONEKI, ardisik_ag));
                }
                continue;
            }
        };
        ardisik_ag = 0;
        let taraflar = taraflar_al(t, d, &kayit).await?;
        let gerekceli: Vec<Value> = evraklar
            .into_iter()
            .filter(|e| gerekceli_mi(&metin(e.get("tur"))) && !metin(e.get("evrakId")).is_empty())
            .collect();
        let kararlar = karar_bilgileri(d, &gerekceli);
        kaydet(&mut durum, &anahtar, kararlar.is_empty(), taraflar.as_ref());
        let _ = durum_yaz(&durum);
        t.inner.lock().unwrap().bulunan += kararlar.len();
        t.sonuc_ekle(
            &did,
            Sonuc {
                sonuc: if kararlar.is_empty() {
                    "Yok".to_string()
                } else {
                    format!("Var ({})", kararlar.len())
                },
                kararlar,
                taraflar,
                indirilecek: false,
            },
        );
    }
    Ok(())
}

// ── 3. aşama: indirme ──────────────────────────────────────────────────────────────────
fn yaz(yol: &Path, veri: &[u8]) -> Result<(), String> {
    if let Some(ust) = yol.parent() {
        fs::create_dir_all(ust).map_err(|e| e.to_string())?;
    }
    fs::write(yol, veri).map_err(|e| format!("{}: {}", yol.display(), e))
}

/// Tek kararı indirir; yazılan dosya yollarını döndürür (alınamazsa boş).
async fn karar_indir(t: &Tarama, d: &Value, k: &Karar, kl: &Path) -> Result<Vec<PathBuf>, String> {
    let kim = format!("{} {} — {}", ds(d, "birim"), ds(d, "no"), k.ad);
    uyap(t, "POST", DOC_VIEWER_HAZIRLIK, Some(&json!({})), None).await?;
    let mut params = HashMap::new();
    params.insert("evrakId".to_string(), k.evrak_id.clone());
    params.insert("dosyaId".to_string(), ds(d, "id"));
    let (st, hdrs, ham, veri) = uyap(t, "GET", EVRAK_ICERIK, None, Some(&params)).await?;
    let hata = hata_var(&veri);
    if st >= 400 || hata.is_some() || ham.is_empty() {
        t.uyari(format!(
            "{}: alınamadı ({})",
            kim,
            hata.unwrap_or_else(|| if st >= 400 { format!("HTTP {}", st) } else { "boş yanıt".to_string() })
        ));
        return Ok(Vec::new());
    }
    let ct = hdrs.get("content-type").map(|s| s.to_lowercase()).unwrap_or_default();
    let tur = _tur_bul(&ham, &ct);

    // Çeviri (UDF/TIFF) ağır olabilir: ayrı iş parçacığında. Önce Python/PyMuPDF, yoksa Rust
    // (evrak_toplu::_pdfe_cevir). Tanınmayan biçimde uydurma PDF üretilmez.
    let pdf: Result<Vec<u8>, String> = if tur == "bilinmiyor" {
        Err("tanınmayan biçim".to_string())
    } else if tur == "pdf" {
        Ok(Vec::new())
    } else {
        let (ham2, ad2) = (ham.clone(), k.ad.clone());
        tokio::task::spawn_blocking(move || _pdfe_cevir(&ham2, tur, &ad2))
            .await
            .unwrap_or_else(|e| Err(format!("çeviri yarıda kaldı: {}", e)))
            .and_then(|p| if p.starts_with(b"%PDF") { Ok(p) } else { Err("çıktı PDF değil".to_string()) })
    };

    let mut yollar = Vec::new();
    if tur != "pdf" {
        let yol = kl.join(format!("{}{}", k.ad, uzanti(tur)));
        yaz(&yol, &ham)?;
        yollar.push(yol);
    }
    match pdf {
        Ok(p) => {
            let yol = kl.join(format!("{}.pdf", k.ad));
            yaz(&yol, if tur == "pdf" { &ham } else { &p })?;
            yollar.push(yol);
        }
        Err(neden) => t.uyari(format!("{}: PDF'e çevrilemedi ({}), orijinali kaydedildi", kim, neden)),
    }
    Ok(yollar)
}

/// Kullanıcının seçtiği dosyalardaki kararları indirir (ofiste olanlar atlanır).
async fn indir(t: &Tarama, dosya_idleri: Vec<String>) -> Result<(), String> {
    let isler: Vec<(String, usize)> = {
        let mut g = t.inner.lock().unwrap();
        let mut isler = Vec::new();
        for did in &dosya_idleri {
            if let Some(s) = g.sonuclar.get_mut(did) {
                s.indirilecek = true;
                isler.extend((0..s.kararlar.len()).map(|n| (did.clone(), n)));
            }
        }
        g.asama = "indirme".to_string();
        g.toplam = isler.len();
        g.i = 0;
        isler
    };
    let toplam = isler.len();
    for (idx, (did, kn)) in isler.iter().enumerate() {
        let sira = idx + 1;
        t.inner.lock().unwrap().i = sira;
        if sira > 1 && (sira - 1) % MOLA_HER_DOSYA == 0 {
            mola(t).await?;
        }
        let (d, k) = {
            let g = t.inner.lock().unwrap();
            let (Some(d), Some(k)) = (g.dosyalar.get(did), g.sonuclar.get(did).and_then(|s| s.kararlar.get(*kn)))
            else {
                continue;
            };
            (d.clone(), k.clone())
        };
        let mut yollar: Vec<PathBuf> = k.mevcut.iter().filter(|y| y.exists()).cloned().collect();
        if yollar.is_empty() {
            t.mesaj_yaz(format!(
                "{}/{}: {} {} — gerekçeli karar indiriliyor…",
                sira,
                toplam,
                ds(&d, "birim"),
                ds(&d, "no")
            ));
            t.ind.inner.lock().unwrap().dosya_id = did.clone();
            yollar = match karar_indir(t, &d, &k, &klasor(&d)).await {
                Ok(y) => y,
                Err(e) => {
                    let Some(ag) = e.strip_prefix(AG_ONEKI) else {
                        return Err(e);
                    };
                    t.uyari(format!("{} {} — {}: bağlantı koptu ({})", ds(&d, "birim"), ds(&d, "no"), k.ad, ag));
                    Vec::new()
                }
            };
            if !yollar.is_empty() {
                t.inner.lock().unwrap().yeni += 1;
            }
        }
        let mut g = t.inner.lock().unwrap();
        g.cikti.extend(yollar.iter().cloned());
        if let Some(kk) = g.sonuclar.get_mut(did).and_then(|s| s.kararlar.get_mut(*kn)) {
            kk.yollar = yollar;
        }
    }
    Ok(())
}

// ── Excel (.xlsx) ──────────────────────────────────────────────────────────────────────
fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

fn col_letter(mut col_idx: usize) -> String {
    let mut res = String::new();
    col_idx += 1;
    while col_idx > 0 {
        let rem = (col_idx - 1) % 26;
        res.insert(0, (b'A' + rem as u8) as char);
        col_idx = (col_idx - 1) / 26;
    }
    res
}

/// Saf Rust OpenXML `.xlsx` üreticisi (`gerekceli_karar.py::_xlsx` 1:1 karşılığı).
pub fn _xlsx(satirlar: &[Vec<String>], yol: &Path) -> Result<(), String> {
    if let Some(parent) = yol.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let f = fs::File::create(yol).map_err(|e| e.to_string())?;
    let mut zw = ZipWriter::new(f);
    let opts = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);

    let content_types = r#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"#;

    let rels = r#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"#;

    let workbook = r#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Gerekçeli Kararlar" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>"#;

    let wb_rels = r#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"#;

    let styles = r#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2">
    <font><sz val="11"/><name val="Calibri"/></font>
    <font><b/><sz val="11"/><name val="Calibri"/></font>
  </fonts>
  <fills count="2">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFE8EEF5"/></patternFill></fill>
  </fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="2">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="1" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
  </cellXfs>
</styleSheet>"#;

    let mut sheet = String::from(
        r#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>"#,
    );

    for (r_idx, row) in satirlar.iter().enumerate() {
        let r_num = r_idx + 1;
        let style_attr = if r_idx == 0 { " s=\"1\"" } else { "" };
        sheet.push_str(&format!("<row r=\"{}\">", r_num));
        for (c_idx, val) in row.iter().enumerate() {
            let cell_ref = format!("{}{}", col_letter(c_idx), r_num);
            sheet.push_str(&format!(
                "<c r=\"{}\" t=\"inlineStr\"{}><is><t>{}</t></is></c>",
                cell_ref,
                style_attr,
                xml_escape(val)
            ));
        }
        sheet.push_str("</row>");
    }
    sheet.push_str("</sheetData></worksheet>");

    for (name, data) in [
        ("[Content_Types].xml", content_types.as_bytes()),
        ("_rels/.rels", rels.as_bytes()),
        ("xl/workbook.xml", workbook.as_bytes()),
        ("xl/_rels/workbook.xml.rels", wb_rels.as_bytes()),
        ("xl/styles.xml", styles.as_bytes()),
        ("xl/worksheets/sheet1.xml", sheet.as_bytes()),
    ] {
        zw.start_file(name, opts).map_err(|e| e.to_string())?;
        zw.write_all(data).map_err(|e| e.to_string())?;
    }
    zw.finish().map_err(|e| e.to_string())?;
    Ok(())
}

/// Tek sayfalık .xlsx baytları: metin hücreleri, kalın + dondurulmuş başlık, süzgeç, sütun genişlikleri
/// (`gerekceli_karar.py::_xlsx`).
pub fn xlsx_bayt(basliklar: &[&str], satirlar: &[Vec<String>], genislikler: &[u32]) -> Result<Vec<u8>, String> {
    const NS: &str = r#"xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main""#;
    const RNS: &str = r#"xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships""#;
    const PKG: &str = "http://schemas.openxmlformats.org/package/2006";
    const OD: &str = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
    const SML: &str = "application/vnd.openxmlformats-officedocument.spreadsheetml";
    const BAS: &str = r#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>"#;

    let hucre = |r: usize, c: usize, v: &str| -> String {
        let temiz: String = v
            .chars()
            .filter(|ch| !matches!(*ch as u32, 0x00..=0x08 | 0x0b | 0x0c | 0x0e..=0x1f))
            .collect();
        let temiz = temiz.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;");
        format!(
            r#"<c r="{}{}" t="inlineStr"{}><is><t xml:space="preserve">{}</t></is></c>"#,
            col_letter(c),
            r,
            if r == 1 { r#" s="1""# } else { "" },
            temiz
        )
    };
    let mut satir_xml = String::new();
    let baslik_satiri: Vec<String> = basliklar.iter().map(|s| s.to_string()).collect();
    for (n, satir) in std::iter::once(&baslik_satiri).chain(satirlar.iter()).enumerate() {
        let r = n + 1;
        satir_xml.push_str(&format!(r#"<row r="{}">"#, r));
        for (c, v) in satir.iter().enumerate() {
            satir_xml.push_str(&hucre(r, c, v));
        }
        satir_xml.push_str("</row>");
    }
    let sutunlar: String = genislikler
        .iter()
        .enumerate()
        .map(|(i, w)| format!(r#"<col min="{0}" max="{0}" width="{1}" customWidth="1"/>"#, i + 1, w))
        .collect();
    let son = format!("{}{}", col_letter(basliklar.len().saturating_sub(1)), satirlar.len() + 1);
    let sayfa = format!(
        "{BAS}<worksheet {NS} {RNS}><sheetViews><sheetView workbookViewId=\"0\">\
         <pane ySplit=\"1\" topLeftCell=\"A2\" activePane=\"bottomLeft\" state=\"frozen\"/></sheetView>\
         </sheetViews><cols>{sutunlar}</cols><sheetData>{satir_xml}</sheetData>\
         <autoFilter ref=\"A1:{son}\"/></worksheet>"
    );
    let parcalar: Vec<(&str, String)> = vec![
        (
            "[Content_Types].xml",
            format!(
                "{BAS}<Types xmlns=\"{PKG}/content-types\">\
                 <Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>\
                 <Default Extension=\"xml\" ContentType=\"application/xml\"/>\
                 <Override PartName=\"/xl/workbook.xml\" ContentType=\"{SML}.sheet.main+xml\"/>\
                 <Override PartName=\"/xl/worksheets/sheet1.xml\" ContentType=\"{SML}.worksheet+xml\"/>\
                 <Override PartName=\"/xl/styles.xml\" ContentType=\"{SML}.styles+xml\"/></Types>"
            ),
        ),
        (
            "_rels/.rels",
            format!(
                "{BAS}<Relationships xmlns=\"{PKG}/relationships\">\
                 <Relationship Id=\"rId1\" Type=\"{OD}/officeDocument\" Target=\"xl/workbook.xml\"/></Relationships>"
            ),
        ),
        (
            "xl/workbook.xml",
            format!(
                "{BAS}<workbook {NS} {RNS}>\
                 <sheets><sheet name=\"Hukuk Dosyaları\" sheetId=\"1\" r:id=\"rId1\"/></sheets></workbook>"
            ),
        ),
        (
            "xl/_rels/workbook.xml.rels",
            format!(
                "{BAS}<Relationships xmlns=\"{PKG}/relationships\">\
                 <Relationship Id=\"rId1\" Type=\"{OD}/worksheet\" Target=\"worksheets/sheet1.xml\"/>\
                 <Relationship Id=\"rId2\" Type=\"{OD}/styles\" Target=\"styles.xml\"/></Relationships>"
            ),
        ),
        (
            // stil 0: düz, stil 1: kalın (başlık satırı)
            "xl/styles.xml",
            format!(
                "{BAS}<styleSheet {NS}>\
                 <fonts count=\"2\"><font><sz val=\"11\"/><name val=\"Calibri\"/></font>\
                 <font><b/><sz val=\"11\"/><name val=\"Calibri\"/></font></fonts>\
                 <fills count=\"2\"><fill><patternFill patternType=\"none\"/></fill>\
                 <fill><patternFill patternType=\"gray125\"/></fill></fills>\
                 <borders count=\"1\"><border><left/><right/><top/><bottom/><diagonal/></border></borders>\
                 <cellStyleXfs count=\"1\"><xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\"/></cellStyleXfs>\
                 <cellXfs count=\"2\"><xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\" xfId=\"0\"/>\
                 <xf numFmtId=\"0\" fontId=\"1\" fillId=\"0\" borderId=\"0\" xfId=\"0\" applyFont=\"1\"/></cellXfs>\
                 <cellStyles count=\"1\"><cellStyle name=\"Normal\" xfId=\"0\" builtinId=\"0\"/></cellStyles></styleSheet>"
            ),
        ),
        ("xl/worksheets/sheet1.xml", sayfa),
    ];
    let mut zw = ZipWriter::new(Cursor::new(Vec::new()));
    let opts = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);
    for (ad, icerik) in parcalar {
        zw.start_file(ad, opts).map_err(|e| e.to_string())?;
        zw.write_all(icerik.as_bytes()).map_err(|e| e.to_string())?;
    }
    Ok(zw.finish().map_err(|e| e.to_string())?.into_inner())
}

/// (Davacı, Davalı, Diğer taraflar) hücreleri; vekil varsa adın yanında.
fn taraf_sutunlari(taraflar: Option<&Vec<Value>>) -> (String, String, String) {
    let Some(taraflar) = taraflar else {
        return ("(alınamadı)".to_string(), String::new(), String::new());
    };
    let (mut davaci, mut davali, mut diger) = (Vec::new(), Vec::new(), Vec::new());
    for x in taraflar {
        let vekil = metin(x.get("vekil"));
        let mut ad = metin(x.get("ad"));
        if !vekil.is_empty() {
            ad = format!("{} (Vekil: {})", ad, vekil);
        }
        let rol_ham = metin(x.get("rol"));
        let rol = tr_kucuk(&rol_ham);
        if rol.starts_with("davacı") || rol.starts_with("davaci") {
            davaci.push(ad);
        } else if rol.starts_with("davalı") || rol.starts_with("davali") {
            davali.push(ad);
        } else if rol_ham.is_empty() {
            diger.push(ad);
        } else {
            diger.push(format!("{}: {}", rol_ham, ad));
        }
    }
    (davaci.join("; "), davali.join("; "), diger.join("; "))
}

fn goreli(yol: &Path, kok: &Path) -> String {
    yol.strip_prefix(kok).unwrap_or(yol).to_string_lossy().to_string()
}

fn excel_satiri(d: &Value, s: &Sonuc, kok: &Path) -> Vec<String> {
    let yollar: Vec<&PathBuf> = s.kararlar.iter().flat_map(|k| k.yollar.iter()).collect();
    let mut sonuc = s.sonuc.clone();
    if !s.kararlar.is_empty() && !s.indirilecek {
        sonuc.push_str(" — indirilmedi");
    } else if !s.kararlar.is_empty() && yollar.is_empty() {
        sonuc.push_str(" — indirilemedi");
    }
    let (davaci, davali, diger) = taraf_sutunlari(s.taraflar.as_ref());
    vec![
        ds(d, "tur"),
        ds(d, "birim"),
        ds(d, "no"),
        ds(d, "dosya_tur"),
        ds(d, "durum"),
        ds(d, "acilis"),
        davaci,
        davali,
        diger,
        sonuc,
        yollar.iter().map(|y| goreli(y, kok)).collect::<Vec<_>>().join(" | "),
    ]
}

/// Excel (kontrol edilen TÜM dosyalar, karar var/yok, indirilsin indirilmesin) her zaman; ZIP yalnız
/// karar indirildiyse (Excel + kararlar; KOK/_zip/, ZIP_SAKLAMA_SN sonra silinir). Excel ofiste kalır.
fn ciktilari_hazirla(t: &Tarama) -> Result<(), String> {
    let kok = kok_klasor();
    let damga = chrono::Local::now().format("%Y-%m-%d %H.%M").to_string();
    let (satirlar, cikti) = {
        let g = t.inner.lock().unwrap();
        let satirlar: Vec<Vec<String>> = g
            .sonuc_sira
            .iter()
            .filter_map(|did| Some(excel_satiri(g.dosyalar.get(did)?, g.sonuclar.get(did)?, &kok)))
            .collect();
        (satirlar, g.cikti.clone())
    };
    let excel_yol = kok.join(format!("Hukuk Dosyalari {}.xlsx", damga));
    yaz(&excel_yol, &xlsx_bayt(EXCEL_BASLIKLARI, &satirlar, EXCEL_GENISLIK)?)?;
    t.inner.lock().unwrap().excel_yol = Some(excel_yol.clone());

    let mut gorulen = HashSet::new();
    let yollar: Vec<PathBuf> = cikti
        .into_iter()
        .filter(|y| gorulen.insert(y.clone()) && y.exists())
        .collect();
    if yollar.is_empty() {
        return Ok(());
    }
    let zip_yol = kok.join("_zip").join(format!("Gerekceli Kararlar {}.zip", damga));
    fs::create_dir_all(kok.join("_zip")).map_err(|e| e.to_string())?;
    let mut zw = ZipWriter::new(fs::File::create(&zip_yol).map_err(|e| e.to_string())?);
    let opts = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);
    let mut ekle = |ad: String, yol: &Path| -> Result<(), String> {
        let veri = fs::read(yol).map_err(|e| format!("{}: {}", yol.display(), e))?;
        zw.start_file(ad, opts).map_err(|e| e.to_string())?;
        zw.write_all(&veri).map_err(|e| e.to_string())
    };
    ekle(
        excel_yol.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default(),
        &excel_yol,
    )?;
    for yol in &yollar {
        ekle(goreli(yol, &kok).replace('\\', "/"), yol)?;
    }
    zw.finish().map_err(|e| e.to_string())?;
    t.ind.inner.lock().unwrap().cikti_yol = Some(zip_yol.to_string_lossy().to_string());
    Ok(())
}

// ── Çalıştırma ─────────────────────────────────────────────────────────────────────────
enum Is {
    Liste,
    Kontrol(Vec<Value>),
    Indir(Vec<String>),
}

async fn calistir(t: Arc<Tarama>, is_: Is) {
    let sonuc = {
        let _sira = evrak_toplu::sira_kilidi().lock().await;
        match t.ind.iptal_kontrol() {
            Err(e) => Err(e),
            Ok(()) => {
                t.ind.inner.lock().unwrap().durum = CALISIYOR.to_string();
                match is_ {
                    Is::Liste => listele(&t).await,
                    Is::Kontrol(secilen) => kontrol(&t, secilen).await,
                    Is::Indir(idler) => indir(&t, idler).await,
                }
            }
        }
    };
    let tamam = sonuc.is_ok();
    if let Err(e) = &sonuc {
        if e == IPTAL_HATASI {
            t.durum_mesaj(IPTAL, "Durduruldu.".to_string());
        } else if let Some(ag) = e.strip_prefix(AG_ONEKI) {
            t.durum_mesaj(
                HATA,
                format!("UYAP bağlantısı tekrar tekrar koptu ({}). Bir süre sonra yeniden deneyin.", ag),
            );
        } else {
            t.durum_mesaj(HATA, format!("Hata: {}", e));
        }
    }
    t.ind.inner.lock().unwrap().captcha = None;
    let (asama, kontrol_edilen, toplam, karar_dosyasi, bulunan, i, yeni) = {
        let mut g = t.inner.lock().unwrap();
        g.mola_bitis = None;
        (
            g.asama.clone(),
            g.sonuclar.len(),
            g.toplam,
            g.sonuclar.values().filter(|s| !s.kararlar.is_empty()).count(),
            g.bulunan,
            g.i,
            g.yeni,
        )
    };
    let kok = kok_klasor().to_string_lossy().to_string();
    if asama == "kontrol" && (tamam || kontrol_edilen > 0) {
        // Durdurulsa/kopsa da kontrol edilenler boşa gitmesin: indirme seçimine geçilir.
        let yarim = if tamam {
            String::new()
        } else {
            format!("{} {}/{} dosya kontrol edildi. ", t.mesaj(), kontrol_edilen, toplam)
        };
        t.inner.lock().unwrap().asama = "karar_secim".to_string();
        let mut g = t.ind.inner.lock().unwrap();
        g.durum = KARAR_SECIM.to_string();
        g.bitis = None;
        g.mesaj = format!(
            "{}Kontrol bitti: {} dosyanın {} tanesinde {} gerekçeli karar var. İndirmek istediklerinizi seçin.",
            yarim, kontrol_edilen, karar_dosyasi, bulunan
        );
    } else if asama == "indirme" {
        let t2 = t.clone();
        let hazir = tokio::task::spawn_blocking(move || ciktilari_hazirla(&t2))
            .await
            .unwrap_or_else(|e| Err(e.to_string()));
        if let Err(e) = hazir {
            t.uyari(format!("Excel/ZIP hazırlanamadı: {}", e));
        }
        if tamam {
            t.durum_mesaj(
                BITTI,
                if toplam > 0 {
                    format!("Tamamlandı: {} gerekçeli karar ({} yeni indirildi). Kayıt: {}", toplam, yeni, kok)
                } else {
                    format!("Excel listesi hazır (karar indirilmedi). Kayıt: {}", kok)
                },
            );
        } else {
            let m = t.mesaj();
            t.mesaj_yaz(format!("{} {}/{} kararda kalındı; yeniden taranınca inenler atlanır.", m, i, toplam));
        }
    }
    let mut g = t.ind.inner.lock().unwrap();
    if !BEKLEYEN.contains(&g.durum.as_str()) {
        g.bitis = Some(now_secs());
    }
}

fn _temizle() {
    let simdi = now_secs();
    KAYITLAR.lock().unwrap().retain(|_, t| {
        let g = t.ind.inner.lock().unwrap();
        match g.bitis {
            Some(bitis) if simdi - bitis > ZIP_SAKLAMA_SN => {
                if let Some(p) = &g.cikti_yol {
                    let _ = fs::remove_file(p);
                }
                false
            }
            _ => true,
        }
    });
}

/// Süren ya da en son tarama (panel sayfa yenilenince kaldığı yerden göstersin).
pub fn aktif() -> Option<Arc<Tarama>> {
    _temizle();
    let map = KAYITLAR.lock().unwrap();
    map.values()
        .max_by(|a, b| {
            let ta = a.ind.inner.lock().unwrap().baslangic;
            let tb = b.ind.inner.lock().unwrap().baslangic;
            ta.partial_cmp(&tb).unwrap_or(std::cmp::Ordering::Equal)
        })
        .cloned()
}

/// durumlar: aranacak dosyaDurumKod'lar (0 Açık, 1 Kapalı); yoksa ikisi. "Açık" aramasında Karara
/// Çıkmış / İstinafta dosyalar da gelir — gerekçeli karar çoğu zaman bunlardadır.
pub fn liste_baslat(durumlar: Option<&Value>) -> Result<Arc<Tarama>, String> {
    _temizle();
    let gecersiz = || "Geçersiz dosya durumu seçimi.".to_string();
    let mut kodlar: Vec<i64> = match durumlar {
        None | Some(Value::Null) => DURUMLAR.iter().map(|(k, _)| *k).collect(),
        Some(Value::Array(arr)) if !arr.is_empty() => arr
            .iter()
            .map(|x| x.as_i64().filter(|k| DURUMLAR.iter().any(|(d, _)| d == k)).ok_or_else(gecersiz))
            .collect::<Result<_, _>>()?,
        _ => return Err(gecersiz()),
    };
    {
        let map = KAYITLAR.lock().unwrap();
        for t in map.values() {
            let d = t.durum();
            if [SIRADA, CALISIYOR, CAPTCHA, SECIM, KARAR_SECIM].contains(&d.as_str()) {
                return Ok(t.clone());
            }
        }
    }
    kodlar.sort();
    kodlar.dedup();
    let t = Tarama::new(kodlar);
    KAYITLAR.lock().unwrap().insert(t.id(), t.clone());
    tokio::spawn(calistir(t.clone(), Is::Liste));
    Ok(t)
}

/// İstek gövdesindeki "dosyalar" → doğrulanmış, yinelenmesiz dosyaId listesi.
fn secimi_dogrula(dosyalar: Option<&Value>, gecerli: &HashSet<String>, bos_olabilir: bool) -> Result<Vec<String>, String> {
    let arr = dosyalar.and_then(|v| v.as_array()).filter(|a| !a.is_empty() || bos_olabilir);
    let Some(arr) = arr else {
        return Err("İşaretli dosya yok.".to_string());
    };
    let mut secilen: Vec<String> = Vec::new();
    for x in arr {
        let did = metin(Some(x));
        if !gecerli.contains(&did) {
            return Err("Listede olmayan dosya seçildi.".to_string());
        }
        if !secilen.contains(&did) {
            secilen.push(did);
        }
    }
    Ok(secilen)
}

/// İşaretli dosyalarda gerekçeli karar KONTROLÜ (indirme yok).
pub fn tarama_baslat(t: &Arc<Tarama>, dosyalar: Option<&Value>) -> Result<(), String> {
    if t.durum() != SECIM {
        return Err("Tarama zaten başladı ya da liste hazır değil.".to_string());
    }
    let secilen: Vec<Value> = {
        let mut g = t.inner.lock().unwrap();
        let gecerli: HashSet<String> = g.dosyalar.keys().cloned().collect();
        let idler = secimi_dogrula(dosyalar, &gecerli, false)?;
        let secilen: Vec<Value> = idler.iter().filter_map(|id| g.dosyalar.get(id).cloned()).collect();
        g.asama = "kontrol".to_string();
        g.toplam = secilen.len();
        g.i = 0;
        secilen
    };
    {
        let mut g = t.ind.inner.lock().unwrap();
        g.durum = SIRADA.to_string();
        g.mesaj = "Kontrol sırada…".to_string();
        g.iptal = false;
    }
    tokio::spawn(calistir(t.clone(), Is::Kontrol(secilen)));
    Ok(())
}

/// Kontrolde kararı bulunan dosyalardan işaretlenenlerin kararlarını indirir. Boş liste: hiçbir şey
/// indirmeden yalnız Excel listesi hazırlanır.
pub fn indir_baslat(t: &Arc<Tarama>, dosyalar: Option<&Value>) -> Result<(), String> {
    if t.durum() != KARAR_SECIM {
        return Err("Kontrol bitmedi ya da indirme zaten başladı.".to_string());
    }
    let secilen = {
        let mut g = t.inner.lock().unwrap();
        let karar_var: HashSet<String> = g
            .sonuclar
            .iter()
            .filter(|(_, s)| !s.kararlar.is_empty())
            .map(|(did, _)| did.clone())
            .collect();
        let secilen = secimi_dogrula(dosyalar, &karar_var, true)?;
        g.asama = "indirme".to_string();
        g.i = 0;
        g.toplam = secilen.iter().filter_map(|did| g.sonuclar.get(did)).map(|s| s.kararlar.len()).sum();
        secilen
    };
    {
        // Kontrol "Durdur" ile yarıda kesilmiş olabilir: indirme yeni bir iştir, iptal işareti sıfırlanır.
        let mut g = t.ind.inner.lock().unwrap();
        g.durum = SIRADA.to_string();
        g.iptal = false;
        g.mesaj = if secilen.is_empty() {
            "Excel listesi hazırlanıyor…".to_string()
        } else {
            "İndirme sırada…".to_string()
        };
    }
    tokio::spawn(calistir(t.clone(), Is::Indir(secilen)));
    Ok(())
}

pub fn iptal(t: &Tarama) {
    {
        // Çalışan görev yok: seçim ekranından vazgeçildi.
        let mut g = t.ind.inner.lock().unwrap();
        if BEKLEYEN.contains(&g.durum.as_str()) {
            g.durum = IPTAL.to_string();
            g.mesaj = "Vazgeçildi.".to_string();
            g.bitis = Some(now_secs());
            return;
        }
    }
    t.ind.iptal_et();
}

pub fn getir(t_id: &str) -> Option<Arc<Tarama>> {
    KAYITLAR.lock().unwrap().get(t_id).cloned()
}

pub fn dosya_oku(t: &Tarama, excel: bool) -> Result<(Vec<u8>, String, String), String> {
    let yol: Option<PathBuf> = if excel {
        t.inner.lock().unwrap().excel_yol.clone()
    } else {
        t.ind.inner.lock().unwrap().cikti_yol.clone().map(PathBuf::from)
    };
    let yol = yol.filter(|p| p.exists()).ok_or_else(|| "Dosya henüz hazır değil.".to_string())?;
    let veri = fs::read(&yol).map_err(|e| format!("Dosya okunamadı: {}", e))?;
    let ad = yol.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
    Ok((veri, ad, if excel { XLSX_TURU } else { "application/zip" }.to_string()))
}

fn json_resp(status: u16, val: Value) -> (u16, HashMap<String, String>, Vec<u8>) {
    let mut hdrs = HashMap::new();
    hdrs.insert("content-type".to_string(), "application/json; charset=utf-8".to_string());
    (status, hdrs, serde_json::to_vec(&val).unwrap_or_else(|_| b"{}".to_vec()))
}

pub async fn handle_http(
    method: &str,
    path: &str,
    _qp: &HashMap<String, String>,
    body: &[u8],
) -> (u16, HashMap<String, String>, Vec<u8>) {
    let rest = path
        .trim_start_matches('/')
        .strip_prefix("__uyap_agent__/gerekceli")
        .unwrap_or("")
        .trim_matches('/');
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
            "POST" => match liste_baslat(govde.get("durumlar")) {
                Ok(t) => json_resp(201, json!({"tarama": t.sozluk()})),
                Err(e) => json_resp(400, json!({"error": e})),
            },
            _ => json_resp(405, json!({"error": "Yöntem desteklenmiyor."})),
        };
    }

    let Some(t) = getir(parts[0]) else {
        return json_resp(404, json!({"error": "Tarama bulunamadı (süresi dolmuş olabilir)."}));
    };
    let eylem = if parts.len() == 2 { parts[1] } else { "" };
    match (parts.len(), eylem, method) {
        (1, _, "GET") => json_resp(200, json!({"tarama": t.sozluk()})),
        (2, "liste", "GET") => json_resp(200, t.liste_sozluk()),
        (2, "sonuclar", "GET") => json_resp(200, t.sonuclar_sozluk()),
        (2, "baslat", "POST") | (2, "indir", "POST") => {
            let r = if eylem == "baslat" {
                tarama_baslat(&t, govde.get("dosyalar"))
            } else {
                indir_baslat(&t, govde.get("dosyalar"))
            };
            match r {
                Ok(()) => json_resp(200, json!({"tarama": t.sozluk()})),
                Err(e) => json_resp(400, json!({"error": e})),
            }
        }
        (2, "iptal", "POST") => {
            iptal(&t);
            json_resp(200, json!({"tarama": t.sozluk()}))
        }
        (2, "captcha", "POST") => {
            // Panel "token" gönderir; "Tekrar dene" null gönderir (jetonsuz yeniden dener).
            let jeton = govde
                .get("token")
                .or_else(|| govde.get("jeton"))
                .and_then(|j| j.as_str())
                .filter(|j| j.len() < 8192)
                .unwrap_or("");
            if t.durum() == CAPTCHA {
                t.ind.captcha_coz(jeton);
            }
            json_resp(200, json!({"tarama": t.sozluk()}))
        }
        (2, "dosya", "GET") | (2, "excel", "GET") => {
            let excel = eylem == "excel";
            let t2 = t.clone();
            match tokio::task::spawn_blocking(move || dosya_oku(&t2, excel)).await {
                Ok(Ok((veri, ad, tur))) => {
                    let mut hdrs = HashMap::new();
                    hdrs.insert("content-type".to_string(), tur);
                    hdrs.insert(
                        "content-disposition".to_string(),
                        format!(
                            "attachment; filename*=UTF-8''{}",
                            percent_encoding::utf8_percent_encode(&ad, percent_encoding::NON_ALPHANUMERIC)
                        ),
                    );
                    hdrs.insert("cache-control".to_string(), "no-store".to_string());
                    (200, hdrs, veri)
                }
                Ok(Err(e)) => json_resp(409, json!({"error": e})),
                Err(e) => json_resp(500, json!({"error": e.to_string()})),
            }
        }
        _ => json_resp(405, json!({"error": "Yöntem/eylem desteklenmiyor."})),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gerekceli_tur_ayrimi() {
        assert!(gerekceli_mi("Gerekçeli Karar"));
        assert!(gerekceli_mi("GEREKÇELİ KARAR"));
        assert!(gerekceli_mi("Gerekceli Karar (ek)"));
        assert!(!gerekceli_mi("Gerekçeli Karar Tebliğ Mazbatası"));
        assert!(!gerekceli_mi("Gerekçeli Karar Tebliğ Zarfı"));
        assert!(!gerekceli_mi("Kısa Karar"));
    }

    #[test]
    fn guvenli_ad_python_ile_ayni() {
        assert_eq!(guvenli_ad("Gerekçeli Karar", 80), "Gerekçeli Karar");
        assert_eq!(guvenli_ad("2024/123", 30), "2024-123");
        assert_eq!(guvenli_ad("  a:b*c?  ", 100), "a-b-c-");
        assert_eq!(guvenli_ad("ad. . ", 100), "ad");
        assert_eq!(guvenli_ad("", 100), "adsiz");
        assert_eq!(guvenli_ad("abcdef", 3), "abc");
    }

    #[test]
    fn tarih_ve_kayit_cozumu() {
        assert_eq!(tarih(Some(&json!({"date": {"day": 5, "month": 3, "year": 2024}}))), "05.03.2024");
        assert_eq!(tarih(Some(&json!("01.02.2023"))), "01.02.2023");
        assert_eq!(tarih(Some(&json!({"date": {}}))), "");
        assert_eq!(tarih(None), "");
        // [kayıtlar, toplam] biçimi ve düz liste
        assert_eq!(kayitlar_listesi(&json!([[{"dosyaId": "1"}, {"dosyaId": "2"}], 2])).len(), 2);
        assert_eq!(kayitlar_listesi(&json!([{"dosyaId": "1"}, "x"])).len(), 1);
        assert_eq!(kayitlar_listesi(&json!({"data": [{"dosyaId": "1"}]})).len(), 1);
        assert!(kayitlar_listesi(&json!({"error": "x"})).is_empty());
    }

    #[test]
    fn mahkeme_turleri_ve_dosya_kaydi() {
        let turler = turleri_ayikla(&Some(json!([
            {"tablo": "0991", "kod": "Asliye Hukuk Mahkemesi"},
            {"tablo": 15},
            {"kod": "tablosuz"},
            "bozuk"
        ])));
        assert_eq!(
            turler,
            vec![
                ("0991".to_string(), "Asliye Hukuk Mahkemesi".to_string()),
                ("15".to_string(), "15".to_string())
            ]
        );
        let r = json!({"dosyaId": "abc", "dosyaNo": "2023/45", "birimAdi": "İzmir 3. Asliye Hukuk Mahkemesi",
            "birimId": 1234, "dosyaDurum": "Karara Çıkmış", "dosyaTur": "Hukuk Dava Dosyası",
            "dosyaAcilisTarihi": {"date": {"day": 1, "month": 12, "year": 2023}}});
        let d = dosya_kaydi(&r, "abc", "0991", "Asliye Hukuk Mahkemesi", "Açık");
        assert_eq!(d["durum"], "Karara Çıkmış");
        assert_eq!(d["arama"], "Açık");
        assert_eq!(d["tur_kod"], "0991");
        assert_eq!(d["birim_id"], "1234");
        assert_eq!(d["acilis"], "01.12.2023");
        assert_eq!(anahtar(&d), "1234|2023/45");
        assert!(klasor(&d).ends_with("İzmir 3. Asliye Hukuk Mahkemesi 2023-45"));
        // durum metni yoksa aramanın durumu
        assert_eq!(dosya_kaydi(&json!({}), "x", "1", "T", "Kapalı")["durum"], "Kapalı");
    }

    #[test]
    fn taraflar_ve_excel_satiri() {
        let tl = taraf_listesi(&Some(json!([
            {"adi": " Ali Veli ", "rol": "Davacı", "vekil": "[Av. Ayşe Kaya]"},
            {"adi": "X A.Ş.", "rol": "DAVALI"},
            {"adi": "Mehmet", "rol": "İhbar Olunan"},
            {"adi": "Rolsüz"}
        ])))
        .unwrap();
        assert_eq!(tl[0], json!({"rol": "Davacı", "ad": "Ali Veli", "vekil": "Av. Ayşe Kaya"}));
        let (davaci, davali, diger) = taraf_sutunlari(Some(&tl));
        assert_eq!(davaci, "Ali Veli (Vekil: Av. Ayşe Kaya)");
        assert_eq!(davali, "X A.Ş.");
        assert_eq!(diger, "İhbar Olunan: Mehmet; Rolsüz");
        assert_eq!(taraf_sutunlari(None).0, "(alınamadı)");
        assert!(taraf_listesi(&Some(json!({"error": "x"}))).is_none());

        let kok = PathBuf::from("kok");
        let d = json!({"tur": "Asliye Hukuk", "birim": "B", "no": "1/2", "durum": "Kapalı"});
        let karar = Karar {
            evrak_id: "9".into(),
            tur: "Gerekçeli Karar".into(),
            ad: "Gerekçeli Karar".into(),
            mevcut: vec![],
            yollar: vec![],
        };
        let mut s = Sonuc { sonuc: "Var (1)".into(), kararlar: vec![karar], taraflar: Some(tl), indirilecek: false };
        assert_eq!(excel_satiri(&d, &s, &kok)[9], "Var (1) — indirilmedi");
        s.indirilecek = true;
        assert_eq!(excel_satiri(&d, &s, &kok)[9], "Var (1) — indirilemedi");
        s.kararlar[0].yollar = vec![kok.join("B 1-2").join("Gerekçeli Karar.pdf")];
        let satir = excel_satiri(&d, &s, &kok);
        assert_eq!(satir.len(), EXCEL_BASLIKLARI.len());
        assert_eq!(satir[9], "Var (1)");
        assert!(satir[10].starts_with("B 1-2") && satir[10].ends_with("Gerekçeli Karar.pdf"));
    }

    #[test]
    fn xlsx_gecerli_zip() {
        let bayt = xlsx_bayt(EXCEL_BASLIKLARI, &[vec!["a<b&c".to_string(); 11]], EXCEL_GENISLIK).unwrap();
        let mut z = zip::ZipArchive::new(Cursor::new(bayt)).unwrap();
        let mut sayfa = String::new();
        std::io::Read::read_to_string(&mut z.by_name("xl/worksheets/sheet1.xml").unwrap(), &mut sayfa).unwrap();
        assert!(sayfa.contains("a&lt;b&amp;c"));
        assert!(sayfa.contains(r#"<autoFilter ref="A1:K2"/>"#));
        assert!(z.by_name("xl/styles.xml").is_ok() && z.by_name("[Content_Types].xml").is_ok());
    }

    #[test]
    fn secim_dogrulama() {
        let gecerli: HashSet<String> = ["a", "b"].iter().map(|s| s.to_string()).collect();
        assert_eq!(secimi_dogrula(Some(&json!(["a", "b", "a"])), &gecerli, false).unwrap(), vec!["a", "b"]);
        assert!(secimi_dogrula(Some(&json!(["a", "z"])), &gecerli, false).is_err());
        assert!(secimi_dogrula(Some(&json!([])), &gecerli, false).is_err());
        assert!(secimi_dogrula(Some(&json!([])), &gecerli, true).unwrap().is_empty());
        assert!(secimi_dogrula(None, &gecerli, true).is_err());
    }

    /// Panelin okuduğu alanlar (uyap_proxy.rs "GEREKÇELİ KARARLAR") tarama sözlüğünde eksiksiz olmalı.
    #[test]
    fn sozluk_panelin_bekledigi_alanlar() {
        let t = Tarama::new(vec![0, 1]);
        let s = t.sozluk();
        for alan in [
            "id", "durum", "asama", "mesaj", "i", "toplam", "bulunan", "yeni", "atlanan", "karar_dosyasi",
            "kalan_sn", "aralik_sn", "mola_kalan_sn", "uyarilar", "dosya_hazir", "excel_hazir", "captcha",
        ] {
            assert!(s.get(alan).is_some(), "eksik alan: {}", alan);
        }
        assert_eq!(s["aralik_sn"], 5.0);
        assert_eq!(s["asama"], "liste");
        assert_eq!(s["durumlar"], json!(["Açık", "Kapalı"]));

        // kontrol sonucu → sonuclar sözlüğü
        {
            let mut g = t.inner.lock().unwrap();
            g.dosya_sira = vec!["d1".into(), "d2".into()];
            g.dosyalar.insert("d1".into(), json!({"id": "d1", "birim": "B", "no": "1/1", "durum": "Kapalı", "tur": "T"}));
            g.dosyalar.insert("d2".into(), json!({"id": "d2", "birim": "B", "no": "2/2", "durum": "Kapalı", "tur": "T"}));
        }
        let karar = Karar { evrak_id: "9".into(), tur: "Gerekçeli Karar".into(), ad: "Gerekçeli Karar".into(), mevcut: vec![], yollar: vec![] };
        t.sonuc_ekle("d1", Sonuc { sonuc: "Var (1)".into(), kararlar: vec![karar], taraflar: None, indirilecek: false });
        t.sonuc_ekle("d2", Sonuc { sonuc: "Yok".into(), kararlar: vec![], taraflar: None, indirilecek: false });
        let s = t.sonuclar_sozluk();
        assert_eq!(s["karar_yok"], 1);
        assert_eq!(s["kontrol_edilemedi"], 0);
        assert_eq!(s["dosyalar"].as_array().unwrap().len(), 1);
        assert_eq!(s["dosyalar"][0]["id"], "d1");
        assert_eq!(s["dosyalar"][0]["kararlar"], json!([{"ad": "Gerekçeli Karar", "inmis": false}]));
        assert_eq!(t.liste_sozluk()["dosyalar"].as_array().unwrap().len(), 2);
    }

    /// Panelin kullandığı adresler (Python sürümüyle aynı) yanıt vermeli.
    #[tokio::test]
    async fn adresler_panelle_uyumlu() {
        let qp = HashMap::new();
        let kok = "/__uyap_agent__/gerekceli";
        let coz = |y: &(u16, HashMap<String, String>, Vec<u8>)| serde_json::from_slice::<Value>(&y.2).unwrap();

        let y = handle_http("GET", kok, &qp, b"").await;
        assert_eq!(y.0, 200);
        assert!(coz(&y).get("tarama").is_some());

        let y = handle_http("POST", kok, &qp, br#"{"durumlar": [7]}"#).await;
        assert_eq!(y.0, 400);
        let y = handle_http("POST", kok, &qp, b"{bozuk").await;
        assert_eq!(y.0, 400);

        let y = handle_http("GET", &format!("{}/yok/liste", kok), &qp, b"").await;
        assert_eq!(y.0, 404);
        assert!(coz(&y)["error"].as_str().unwrap().contains("Tarama bulunamadı"));

        // Kayıtlı bir tarama üzerinden seçim akışı (UYAP'a istek gitmeden).
        let t = Tarama::new(vec![0, 1]);
        let id = t.id();
        {
            let mut g = t.inner.lock().unwrap();
            g.asama = "secim".into();
            g.turler = vec![json!({"kod": "0991", "ad": "Asliye Hukuk Mahkemesi"})];
            g.dosya_sira = vec!["d1".into()];
            g.dosyalar.insert("d1".into(), json!({"id": "d1", "birim": "B", "no": "1/1", "durum": "Kapalı", "tur_kod": "0991"}));
        }
        t.ind.inner.lock().unwrap().durum = SECIM.to_string();
        KAYITLAR.lock().unwrap().insert(id.clone(), t.clone());

        let y = handle_http("GET", &format!("{}/{}", kok, id), &qp, b"").await;
        assert_eq!(coz(&y)["tarama"]["durum"], "secim");
        let y = handle_http("GET", &format!("{}/{}/liste", kok, id), &qp, b"").await;
        assert_eq!(y.0, 200);
        assert_eq!(coz(&y)["turler"][0]["kod"], "0991");
        assert_eq!(coz(&y)["dosyalar"][0]["id"], "d1");
        let y = handle_http("POST", &format!("{}/{}/baslat", kok, id), &qp, br#"{"dosyalar": ["yok"]}"#).await;
        assert_eq!(y.0, 400);
        let y = handle_http("POST", &format!("{}/{}/indir", kok, id), &qp, br#"{"dosyalar": []}"#).await;
        assert_eq!(y.0, 400); // kontrol bitmeden indirme olmaz
        let y = handle_http("GET", &format!("{}/{}/dosya", kok, id), &qp, b"").await;
        assert_eq!(y.0, 409);
        let y = handle_http("GET", &format!("{}/{}/sonuclar", kok, id), &qp, b"").await;
        assert_eq!(y.0, 200);
        let y = handle_http("POST", &format!("{}/{}/captcha", kok, id), &qp, br#"{"token": null}"#).await;
        assert_eq!(y.0, 200);
        let y = handle_http("POST", &format!("{}/{}/iptal", kok, id), &qp, b"").await;
        assert_eq!(coz(&y)["tarama"]["durum"], "iptal");
        assert_eq!(coz(&y)["tarama"]["mesaj"], "Vazgeçildi.");
        KAYITLAR.lock().unwrap().remove(&id);
    }
}
