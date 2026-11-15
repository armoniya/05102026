//! Tebligat taramasının UYAP'a ve veritabanına dokunmayan bölümü (Aşama 3, çevrimdışı yarı).
//!
//! Buradaki her şey elde hazır veriyi yorumlar: evrak listesi, tebligat PDF'i, PTT yanıtı,
//! taraf satırları. Ağ isteği ve veritabanı yoktur; bu yüzden tamamı örnek veriyle sınanır.
//!   • mazbata eşleştirme (mazbata açıklamasındaki barkod, "- Tebliğ Edildi" sonucu)
//!   • PDF'ten barkod (Code-39, `(*…*) Tj`) ve gövde metni
//!   • tebligat türü (Ödeme Emri, 103 Davetiyesi, Haciz İhbarnamesi …) ve tür eşleştirme anahtarı
//!   • PTT yanıtından durum, tarih, teslim şekli / iade nedeni
//!   • taraf satırlarının ayrıştırılması, tebligatın hangi borçluya ait olduğu

use regex::bytes::Regex as BaytRegex;
use regex::Regex;
use serde_json::{json, Value};
use std::collections::{BTreeSet, HashMap};
use std::io::Read;
use std::sync::OnceLock;

fn re(hucre: &'static OnceLock<Regex>, desen: &str) -> &'static Regex {
    hucre.get_or_init(|| Regex::new(desen).expect("desen"))
}

fn bre(hucre: &'static OnceLock<BaytRegex>, desen: &str) -> &'static BaytRegex {
    hucre.get_or_init(|| BaytRegex::new(desen).expect("desen"))
}

// ── Evrak türleri ve sabitler ───────────────────────────────────────────────────────────────────

/// Barkodu taşıyan tebligat evrakı.
pub const HEDEF_TUR: &str = "Kapalı Tebligat";
/// Merkezi Takip Sistemi dosyalarında tebligat bu türle gelir.
pub const MTS_TEBLIGAT_TUR: &str = "PTT MTS Tebligat Belgesi";
/// Avukatın portaldan gönderdiği (21/2 dahil) tebligat talebi.
pub const TALEP_EVRAK_TUR: &str = "Avukat Portal Tebligat Talebi";
pub const TEBLIG_MAZBATASI_TUR: &str = "Tebliğ Mazbatası";
pub const MTS_TEBLIG_MAZBATASI_TUR: &str = "PTT MTS Tebliğ Mazbatası";
/// Canlı örneği görülmedi; yazım farkına dayanıklı karşılaştırılır ([`tur_anahtar`]).
pub const KAPALI_TEBLIG_MAZBATASI_TUR: &str = "Kapalı Tebliğ Mazbatası";
pub const ELEKTRONIK_ANAHTAR: &str = "Elektronik Tebligat";
/// E-tebligatta tebliğ tarihi evrak tarihidir; süre bu kadar gün sonra başlar.
pub const E_TEBLIG_OKUMA_SURESI_GUN: i64 = 5;
pub const E_TEBLIG_DURUM_METNI: &str = "E-Tebligat: tebliğ edildi (süre 5 gün sonra başlar)";
pub const E_TEBLIG_TARIHSIZ_METNI: &str = "Elektronik Tebligat (evrak tarihi yok)";
/// Artık değişmeyecek PTT sonuçları: bu durumdaki barkod yeniden sorgulanmaz.
pub const SONUCLANMIS_PTT_DURUMLARI: [&str; 2] = ["TESLİM EDİLDİ", "İADE"];
/// Sabit tebligat türleri; dışındakiler zarf içeriği metni ya da "Belirsiz"dir.
pub const TEMEL_TURLER: [&str; 10] = [
    "Ödeme Emri",
    "103 Davetiyesi",
    "Kıymet Takdiri Raporu",
    "Bilirkişi Raporu",
    "Banka",
    "Banka (Haciz İhbarnamesi)",
    "Maaş",
    "Maaş (Haciz İhbarnamesi)",
    "Maaş (Haciz Müzekkeresi)",
    "Haciz İhbarnamesi",
];
pub const BELIRSIZ: &str = "Belirsiz";

// ── Metin yardımcıları ──────────────────────────────────────────────────────────────────────────

/// Türkçe-duyarlı küçük harf: İ/I/ı→i, ş→s, ğ→g, ü→u, ö→o, ç→c. Her karakter tek karaktere
/// döner; böylece küçültülmüş metindeki konum özgün metinde aynı karaktere denk gelir.
pub fn tr_kucuk(s: &str) -> String {
    s.chars()
        .map(|c| match c {
            'İ' | 'I' | 'ı' => 'i',
            'Ş' | 'ş' => 's',
            'Ğ' | 'ğ' => 'g',
            'Ü' | 'ü' => 'u',
            'Ö' | 'ö' => 'o',
            'Ç' | 'ç' => 'c',
            o => o.to_lowercase().next().unwrap_or(o),
        })
        .collect()
}

/// `kucuk` (= `tr_kucuk(ozgun)`) üzerindeki bayt aralığının özgün metindeki karşılığı.
fn ozgun_dilim(ozgun: &str, kucuk: &str, bas: usize, son: usize) -> String {
    let i = kucuk[..bas].chars().count();
    let n = kucuk[bas..son].chars().count();
    ozgun.chars().skip(i).take(n).collect()
}

/// Yalnız [0-9a-z] kelimeleri (Türkçe-duyarsız, noktalama farkı yok).
fn kelimeler(s: &str) -> Vec<String> {
    tr_kucuk(s)
        .split(|c: char| !(c.is_ascii_digit() || c.is_ascii_lowercase()))
        .filter(|k| !k.is_empty())
        .map(str::to_string)
        .collect()
}

pub(crate) fn metin(v: &Value, anahtar: &str) -> String {
    match v.get(anahtar) {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) => n.to_string(),
        _ => String::new(),
    }
}

pub(crate) fn tam(v: &Value, anahtar: &str) -> Option<i64> {
    match v.get(anahtar)? {
        Value::Number(n) => n.as_i64(),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

/// JSON değerinin "dolu" sayılması (boş metin, 0, false, null, boş liste/nesne dolu değildir).
pub(crate) fn dolu(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// Evrak türü adları için yazım farkına (Türkçe karakter, boşluk) dayanıklı anahtar.
pub fn tur_anahtar(tur: &str) -> String {
    tr_kucuk(tur).split_whitespace().collect::<Vec<_>>().join(" ")
}

/// `list_dosya_evraklar` yanıtındaki `tumEvraklar` gruplarını tek listeye indirir.
pub fn evraklari_duzle(sayfa: &Value) -> Vec<Value> {
    let mut tum = Vec::new();
    if let Some(gruplar) = sayfa.get("tumEvraklar").and_then(Value::as_object) {
        for grup in gruplar.values() {
            if let Some(liste) = grup.as_array() {
                tum.extend(liste.iter().filter(|e| e.is_object()).cloned());
            }
        }
    }
    tum
}

// ── Mazbata ─────────────────────────────────────────────────────────────────────────────────────

/// Mazbata açıklamasındaki barkod. İki biçim: "… - Barkod Numarası: 3000120018142 - Tebliğ
/// Edildi" ve "4100140832731 Barkod Numaralı Tebligat Mazbatası." (MTS).
pub fn mazbata_barkodu(aciklama: &str) -> Option<String> {
    static R: OnceLock<Regex> = OnceLock::new();
    let m = re(&R, r"(?i)Barkod\s+Numaras[ıi]\s*:\s*(\d{8,20})|(\d{8,20})\s*Barkod\s+Numaral").captures(aciklama)?;
    m.get(1).or(m.get(2)).map(|x| x.as_str().to_string())
}

/// Bu barkodun kendi mazbatası dosyaya dönmüş mü (açıklamadaki barkod = bu barkod)?
pub fn kendi_mazbatasi_dondu_mu(barkod: &str, mazbata_var: bool, mazbata_aciklama: &str) -> bool {
    mazbata_var && !barkod.is_empty() && mazbata_barkodu(mazbata_aciklama).as_deref() == Some(barkod)
}

/// UYAP, mazbata dönünce tebligat evrakının açıklamasına sonucu ekler:
/// "Normal Tebligat[BORÇLU …] - Tebliğ Edildi" / "- Tebliğ Edilemedi".
pub fn teblig_sonucu_aciklamada_mi(evrak_aciklama: &str) -> bool {
    static R: OnceLock<Regex> = OnceLock::new();
    re(&R, r"-\s*teblig\s+edil(di|emedi)\s*$").is_match(&tr_kucuk(evrak_aciklama))
}

/// Mazbatası icra dairesine dönmüş tebligat PTT'ye sorulmaz.
pub fn mazbatasi_dondu_mu(barkod: &str, evrak_aciklama: &str, mazbata_var: bool, mazbata_aciklama: &str) -> bool {
    teblig_sonucu_aciklamada_mi(evrak_aciklama) || mazbata_var || kendi_mazbatasi_dondu_mu(barkod, mazbata_var, mazbata_aciklama)
}

/// Dosyanın mazbata evrakları: barkoda göre ve açıklamasında barkod olmayanlar.
#[derive(Debug, Default, Clone)]
pub struct Mazbatalar {
    barkodlu: HashMap<String, String>,
    barkodsuz: Vec<String>,
    tebligat_sayisi: usize,
}

impl Mazbatalar {
    pub fn kur(evraklar: &[Value]) -> Self {
        let mut m = Mazbatalar::default();
        let mut tebligatlar = BTreeSet::new();
        for e in evraklar {
            let tur = metin(e, "tur");
            if tur == HEDEF_TUR || tur == MTS_TEBLIGAT_TUR {
                tebligatlar.insert(metin(e, "birimEvrakNo"));
            }
            if tur != TEBLIG_MAZBATASI_TUR && tur != MTS_TEBLIG_MAZBATASI_TUR {
                continue;
            }
            let aciklama = metin(e, "aciklama");
            match mazbata_barkodu(&aciklama) {
                Some(b) => {
                    m.barkodlu.entry(b).or_insert(aciklama);
                }
                None => m.barkodsuz.push(aciklama),
            }
        }
        m.tebligat_sayisi = tebligatlar.len();
        m
    }

    pub fn sayi(&self) -> usize {
        self.barkodlu.len() + self.barkodsuz.len()
    }

    /// Barkodun mazbata açıklaması (boşsa "Var"); mazbata yoksa `None`. Açıklamasında barkod
    /// olmayan tek mazbata, yalnız dosyada tek tebligat varsa ona bağlanır (tahmin yok).
    pub fn bul(&self, barkod: &str) -> Option<String> {
        let ac = match self.barkodlu.get(barkod) {
            Some(a) => a,
            None if self.tebligat_sayisi == 1 && self.barkodsuz.len() == 1 => &self.barkodsuz[0],
            None => return None,
        };
        Some(if ac.is_empty() { "Var".to_string() } else { ac.clone() })
    }
}

// ── PDF ─────────────────────────────────────────────────────────────────────────────────────────

/// PDF'teki bütün FlateDecode içerik akışlarını çözüp birleştirir (çözülemeyenler atlanır).
pub fn pdf_metin_coz(pdf: &[u8]) -> Vec<u8> {
    static R: OnceLock<BaytRegex> = OnceLock::new();
    let mut cikti = Vec::new();
    for m in bre(&R, r"(?s-u)\d+ 0 obj\s*<<(.*?)>>\s*stream\r?\n").find_iter(pdf) {
        let bas = m.end();
        let Some(uz) = pdf[bas..].windows(9).position(|w| w == b"endstream") else { continue };
        let mut parca = Vec::new();
        if flate2::read::ZlibDecoder::new(&pdf[bas..bas + uz]).read_to_end(&mut parca).is_ok() {
            if !cikti.is_empty() {
                cikti.push(b'\n');
            }
            cikti.extend(parca);
        }
    }
    cikti
}

/// PDF içinde Code-39 barkod fontuyla çizilen değer (`*…*` arasında). Birden fazla eşleşmede
/// ilki (mazbatanın iki nüshası aynı barkodu taşır). PDF değilse ya da bulunamazsa `None`.
pub fn barkod_bul(pdf: &[u8]) -> Option<String> {
    static R: OnceLock<BaytRegex> = OnceLock::new();
    if !pdf.starts_with(b"%PDF") {
        return None;
    }
    let akis = pdf_metin_coz(pdf);
    let m = bre(&R, r"(?-u)\(\*([0-9A-Za-z]{6,20})\*\)\s*Tj").captures(&akis)?;
    String::from_utf8(m.get(1)?.as_bytes().to_vec()).ok()
}

/// PDF metin operandındaki kaçışları çözer: `\(` `\)` `\\` ve sekizli `\ddd`.
fn pdf_dize_coz(parantezli: &[u8]) -> Vec<u8> {
    let ic = &parantezli[1..parantezli.len() - 1];
    let mut cikti = Vec::with_capacity(ic.len());
    let mut i = 0;
    while i < ic.len() {
        if ic[i] != b'\\' || i + 1 >= ic.len() {
            cikti.push(ic[i]);
            i += 1;
            continue;
        }
        let mut j = i + 1;
        while j < ic.len() && j < i + 4 && (b'0'..=b'7').contains(&ic[j]) {
            j += 1;
        }
        if j > i + 1 {
            let deger = ic[i + 1..j].iter().fold(0u32, |t, b| t * 8 + u32::from(b - b'0'));
            cikti.push((deger & 0xFF) as u8);
            i = j;
        } else {
            if matches!(ic[i + 1], b'(' | b')' | b'\\') {
                cikti.push(ic[i + 1]);
            }
            i += 2;
        }
    }
    cikti
}

/// PDF'teki metin gösterme operandlarını sırayla birleştirir (kaba düz metin; tam bir PDF
/// ayrıştırıcı değil — tür tespiti ve ad araması için yeterli). Windows-1254 olarak çözülür.
pub fn pdf_govde_metni(pdf: &[u8]) -> String {
    static R: OnceLock<BaytRegex> = OnceLock::new();
    let akis = pdf_metin_coz(pdf);
    let mut ham = Vec::new();
    for m in bre(&R, r"(?-u)\((?:[^()\\]|\\.)*\)").find_iter(&akis) {
        ham.extend(pdf_dize_coz(m.as_bytes()));
    }
    encoding_rs::WINDOWS_1254.decode(&ham).0.into_owned()
}

// ── Tebligat türü ───────────────────────────────────────────────────────────────────────────────

/// Tür eşleştirme anahtarı: tarih ve "tarihli / ve / yazısı" gibi dolgu kelimeleri atılır,
/// kelimeler tekilleştirilip sıralanır ("Satış İlanı Satış İlanı" = "SATIŞ İLANI").
pub fn tur_eslesme_anahtari(ham: &str) -> String {
    static R: OnceLock<Regex> = OnceLock::new();
    const GEREKSIZ: [&str; 5] = ["tarihli", "gunlu", "yazisi", "yazi", "ve"];
    let kucuk = tr_kucuk(ham);
    let s = re(&R, r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b").replace_all(&kucuk, " ");
    let tekil: BTreeSet<String> = kelimeler(&s).into_iter().filter(|k| !GEREKSIZ.contains(&k.as_str())).collect();
    tekil.into_iter().collect::<Vec<_>>().join(" ")
}

/// Ham yazılış kullanıcı tarafından bir türe bağlandıysa o tür (`eslesmeler`: anahtar → tür).
pub fn tur_eslestir(ham: &str, eslesmeler: &HashMap<String, String>) -> Option<String> {
    if ham.is_empty() {
        return None;
    }
    eslesmeler.get(&tur_eslesme_anahtari(ham)).cloned()
}

/// "Satış İlanı - [VELİ …] - Barkod Numarası: …" → "Satış İlanı" (yoksa boş).
pub fn mazbata_belge_adi(aciklama: &str) -> String {
    let ac = aciklama.trim();
    ac.split_once(" - [").map(|(bas, _)| bas.trim().to_string()).unwrap_or_default()
}

/// "X X X" → "X" (PDF metni aynı ifadeyi art arda tekrarlayabiliyor).
fn tekrari_tekille(s: &str) -> String {
    let k: Vec<char> = s.chars().collect();
    for uz in 1..=k.len() / 2 {
        // Toplam = birim + (boşluk + birim) × n
        if (k.len() + 1) % (uz + 1) != 0 {
            continue;
        }
        let birim = &k[..uz];
        let ayni = k.chunks(uz + 1).all(|p| p[..uz.min(p.len())] == *birim && (p.len() == uz || p[uz] == ' '));
        if ayni {
            return birim.iter().collect();
        }
    }
    s.to_string()
}

const MAAS_KELIMELERI: [&str; 5] = ["maas", "ucret", "isveren", "is yeri", "hizmet"];

/// Evrak açıklaması ve (varsa) PDF gövde metninden tebligat türü. Döner: sabit türlerden biri,
/// zarfın beyan ettiği içerik ("BU ZARFTA <X> VARDIR"; kullanıcı eşleştirdiyse o tür),
/// "Belirsiz" (PDF okundu, ipucu yok) ya da boş (PDF yok, ipucu yok).
pub fn tebligat_turunu_belirle(aciklama: &str, pdf_govde: Option<&str>, eslesmeler: &HashMap<String, String>) -> String {
    static UCUNCU: OnceLock<Regex> = OnceLock::new();
    static ZARF: OnceLock<Regex> = OnceLock::new();
    static MAAS: OnceLock<Regex> = OnceLock::new();
    let govde = pdf_govde.unwrap_or("");
    let aciklama_l = tr_kucuk(aciklama);
    let govde_l = tr_kucuk(govde);
    let birlesik = format!("{aciklama_l} {govde_l}");

    // "Ödeme İcra Emri" de ödeme emridir (ilamsız ödeme emri ile ilamlı icra emrinin çatı adı).
    if birlesik.contains("odeme emri") || birlesik.contains("odeme icra emri") {
        return "Ödeme Emri".into();
    }
    if birlesik.contains("kiymet takdir") {
        return "Kıymet Takdiri Raporu".into();
    }
    if birlesik.contains("bilirkisi") {
        return "Bilirkişi Raporu".into();
    }
    if birlesik.contains("103") && ["davet", "mal beyan", "madde"].iter().any(|k| birlesik.contains(k)) {
        return "103 Davetiyesi".into();
    }

    // "3. Taraf" parantezi yalnız kime gönderildiğini söyler; ne gönderildiği zarf içeriğindedir.
    let ucuncu_taraf = re(&UCUNCU, r"(?:ucuncu taraf|3\.\s*taraf)\s*:?\s*([^\]]*)\]")
        .captures(&aciklama_l)
        .and_then(|m| m.get(1))
        .map(|x| x.as_str().to_string())
        .unwrap_or_default();
    let zarf_m = re(&ZARF, r"bu zarfta\s+(.*?)\s+vardir").captures(&govde_l).and_then(|m| m.get(1));
    let zarf_icerigi = zarf_m.map(|x| x.as_str()).unwrap_or("");
    let maas_var = MAAS_KELIMELERI.iter().any(|k| zarf_icerigi.contains(k));

    if re(&MAAS, r"maas\s*haciz\s*muzekkere").is_match(&govde_l) {
        return "Maaş (Haciz Müzekkeresi)".into();
    }
    if birlesik.contains("haciz ihbarname") {
        // "banka" değil "bank": DENİZBANK, AKBANK, ING BANK …
        let bakilan = if ucuncu_taraf.is_empty() { birlesik.as_str() } else { ucuncu_taraf.as_str() };
        if bakilan.contains("bank") {
            return "Banka (Haciz İhbarnamesi)".into();
        }
        if maas_var {
            return "Maaş (Haciz İhbarnamesi)".into();
        }
        return "Haciz İhbarnamesi".into();
    }
    if ucuncu_taraf.contains("bank") {
        return "Banka".into();
    }
    if maas_var {
        return "Maaş".into();
    }

    if pdf_govde.is_some_and(|g| !g.is_empty()) {
        if let Some(m) = zarf_m {
            let ham = ozgun_dilim(govde, &govde_l, m.start(), m.end());
            let ham = ham.split_whitespace().collect::<Vec<_>>().join(" ");
            let ham = ham.trim_matches(|c: char| " .,:;\"'()[]".contains(c));
            let ham = tekrari_tekille(ham);
            let uz = ham.chars().count();
            if uz > 2 && uz <= 120 {
                return tur_eslestir(&ham, eslesmeler).unwrap_or(ham);
            }
        }
        return BELIRSIZ.into();
    }
    String::new()
}

// ── PTT ─────────────────────────────────────────────────────────────────────────────────────────

/// PTT'ye gidilmediğini gösteren işaretler (veritabanındaki PTT durumu bunlarla ezilmez).
pub fn ptt_atlandi() -> Value {
    json!({"_hata": "PTT sorgulanmadı (yalnız UYAP tebligat verisi)", "_atlandi": true})
}

pub fn ptt_sonuclanmis() -> Value {
    json!({"_hata": "PTT sorgulanmadı (sonuç kesin)", "_atlandi": true, "_sonuclanmis": true})
}

/// PTT'nin "YYYYMMDD" tarihini "GG/AA/YYYY" yapar; başka biçim olduğu gibi kalır.
pub fn ptt_tarih_bicimle(ham: &str) -> String {
    let s = ham.trim();
    if s.len() == 8 && s.bytes().all(|b| b.is_ascii_digit()) {
        format!("{}/{}/{}", &s[6..8], &s[4..6], &s[0..4])
    } else {
        s.to_string()
    }
}

/// PTT yanıtından (durum metni, tarih metni). `errorState` "kayıt var" demektir (ad yanıltıcı).
/// Teslim edilmemiş gönderide `teslim_tarihi` "0" gelir; o zaman `son_islem_tarihi` kullanılır.
pub fn ptt_durum_ve_tarih(veri: &Value) -> (String, String) {
    let Some(o) = veri.as_object() else { return (veri.to_string(), String::new()) };
    if dolu(o.get("_hata")) {
        return (metin(veri, "_hata"), String::new());
    }
    let hata_metni = || {
        let m = metin(veri, "errorMessage");
        if m.is_empty() { None } else { Some(m) }
    };
    if !dolu(o.get("errorState")) {
        return (hata_metni().unwrap_or_else(|| "Kayıt yok".into()), String::new());
    }
    let bos = json!({});
    let son = o.get("sondurum").filter(|s| s.is_object()).unwrap_or(&bos);
    let durum = metin(son, "son_durum_aciklama");
    if !durum.is_empty() {
        let mut t = metin(son, "teslim_tarihi");
        if t.trim().is_empty() || t.trim() == "0" {
            t = metin(son, "son_islem_tarihi");
        }
        return (durum, ptt_tarih_bicimle(&t));
    }
    if let Some(h) = o.get("hareketDongu").and_then(Value::as_array).and_then(|a| a.last()) {
        let mut tarih = metin(h, "tarih");
        let saat = metin(h, "saat");
        if !saat.is_empty() {
            tarih = format!("{tarih} {saat}").trim().to_string();
        }
        let ac = metin(h, "aciklama");
        let durum = if ac.is_empty() { hata_metni().unwrap_or_else(|| "Bilinmeyen durum".into()) } else { ac };
        return (durum, tarih);
    }
    (hata_metni().unwrap_or_else(|| "Bilinmeyen durum".into()), String::new())
}

const TESLIM_SEKILLERI: [(&str, &str); 8] = [
    ("21/2", "Muhtara teslim (21/2 – MERNİS adresi)"),
    ("ihtiyar heyeti", "İhtiyar heyeti/azaya teslim (21. md.)"),
    ("muhtara", "Muhtara teslim (21. md.)"),
    ("bizzat", "Muhataba bizzat teslim"),
    ("ayni konutta yakina", "Aynı konutta yakına teslim"),
    ("daimi calisana", "İşyerinde daimi çalışana teslim"),
    ("amirine", "İşyerindeki amirine teslim"),
    ("kapisina yapistirma", "Kapıya yapıştırma (35. md.)"),
];
const ARA_HAREKETLER: [&str; 2] = ["gondericisine teslim edildi", "koy dagitimina tabi bekliyor"];

/// PTT ham yanıtından teslim şekli (TESLİM EDİLDİ) ya da iade nedeni (İADE); başka durumda boş.
pub fn ptt_teslim_sekli(veri: &Value) -> String {
    static R: OnceLock<Regex> = OnceLock::new();
    if !veri.is_object() || dolu(veri.get("_hata")) {
        return String::new();
    }
    let durum = tr_kucuk(&veri.get("sondurum").map(|s| metin(s, "son_durum_aciklama")).unwrap_or_default());
    let (teslim, iade) = (durum.contains("teslim edildi"), durum.contains("iade"));
    if !(teslim || iade) {
        return String::new();
    }
    // "21.MAD. GORE MUHTARA TESLiM / m* m*" → sondaki " / …" atılır ("21/2. Madde" korunur).
    let temizle = |s: String| re(&R, r"\s+/(\s+.*)?$").replace(s.trim(), "").trim().to_string();
    let detaylar: Vec<String> = veri
        .get("hareketDongu")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter(|h| h.is_object()).map(|h| temizle(metin(h, "islem_detay"))).collect())
        .unwrap_or_default();
    let detaylar: Vec<String> =
        detaylar.into_iter().filter(|d| !d.is_empty() && !ARA_HAREKETLER.contains(&tr_kucuk(d).as_str())).collect();
    if teslim {
        for d in detaylar.iter().rev() {
            let k = tr_kucuk(d);
            if let Some((_, etiket)) = TESLIM_SEKILLERI.iter().find(|(a, _)| k.contains(a)) {
                return (*etiket).to_string();
            }
        }
    }
    detaylar.last().cloned().unwrap_or_default()
}

/// Kayıtlı PTT durumu artık değişmeyecek bir sonuç mu?
pub fn ptt_sonuclandi(durum: &str) -> bool {
    let d = tr_kucuk(durum);
    SONUCLANMIS_PTT_DURUMLARI.iter().any(|s| tr_kucuk(s) == d.trim())
}

/// Elektronik tebligatın tebliğ tarihi kayıtlı mı (varsa sonuç kesindir)?
pub fn e_teblig_sonuclandi(durum: &str, tarih: &str) -> bool {
    !tarih.is_empty() && tr_kucuk(durum).starts_with("e-tebligat:")
}

/// "GG/AA/YYYY…" ile başlayan metnin günü.
pub fn teblig_tarihini_coz(deger: &str) -> Option<chrono::NaiveDate> {
    static R: OnceLock<Regex> = OnceLock::new();
    let m = re(&R, r"^(\d{2})/(\d{2})/(\d{4})").captures(deger.trim())?;
    chrono::NaiveDate::from_ymd_opt(m[3].parse().ok()?, m[2].parse().ok()?, m[1].parse().ok()?)
}

/// E-tebligatın tebliğ tarihi = evrak tarihi ("GG/AA/YYYY"); çözülemezse boş.
pub fn e_teblig_teblig_tarihi(evrak_tarihi: &str) -> String {
    teblig_tarihini_coz(evrak_tarihi).map(|t| t.format("%d/%m/%Y").to_string()).unwrap_or_default()
}

// ── Taraflar ────────────────────────────────────────────────────────────────────────────────────

/// UYAP taraf satırından çıkarılan bilgi. Kimlikler ham hâldedir; [`kimlik_temizle`] ile süzülür.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct TarafBilgisi {
    /// "gercek" ya da "tuzel"
    pub tur: String,
    pub ad: String,
    pub soyad: String,
    pub unvan: String,
    pub tckn: String,
    pub vergi_no: String,
    pub mersis_no: String,
}

impl TarafBilgisi {
    pub fn tuzel_mi(&self) -> bool {
        self.tur == "tuzel"
    }

    /// Kaydedilmeye değer bir ad ya da kimlik var mı?
    pub fn bos_mu(&self) -> bool {
        [&self.ad, &self.soyad, &self.unvan, &self.tckn, &self.vergi_no, &self.mersis_no].iter().all(|x| x.trim().is_empty())
    }
}

/// "AD … SOYAD" → (ad, soyad): son kelime soyaddır.
fn ad_soyad_ayir(tam_ad: &str) -> (String, String) {
    let t = tam_ad.trim();
    match t.rsplit_once(char::is_whitespace) {
        Some((ad, soyad)) if !ad.trim().is_empty() => (ad.trim_end().to_string(), soyad.to_string()),
        _ => (t.to_string(), String::new()),
    }
}

/// `dosya_taraf_bilgileri_brd` satırı (adi, rol, kisiKurum, vekil). Bu uç kimlik vermez.
pub fn taraf_bilgisi_ayristir(item: &Value) -> TarafBilgisi {
    let adi = metin(item, "adi").trim().to_string();
    if tr_kucuk(&metin(item, "kisiKurum")) == "kurum" {
        return TarafBilgisi { tur: "tuzel".into(), unvan: adi, ..Default::default() };
    }
    let (ad, soyad) = ad_soyad_ayir(&adi);
    TarafBilgisi { tur: "gercek".into(), ad, soyad, ..Default::default() }
}

fn rakamlar(s: &str) -> String {
    s.chars().filter(char::is_ascii_digit).collect()
}

fn ilk_dolu(v: &Value, anahtarlar: &[&str]) -> String {
    anahtarlar.iter().map(|a| metin(v, a).trim().to_string()).find(|s| !s.is_empty()).unwrap_or_default()
}

/// Tüzel kişi adında tam kelime olarak geçen işaretler (küçük harf, ASCII).
const KURUM_KELIMELERI: [&str; 12] =
    ["ltd", "sti", "anonim", "as", "sirket", "sirketi", "kooperatif", "kooperatifi", "mudurlugu", "banka", "bankasi", "san"];

/// `dosya_borclu_list` satırı: borçlu, kimliğiyle (kisiTumDVO.tcKimlikNo / kurumTumDVO vergiNo,
/// mersisNo). Kişi mi kurum mu olduğu önce satırın hangi bölümü taşıdığına, sonra kimliğe
/// bakılarak belirlenir; ad içindeki kelimeler yalnız bunlar yoksa ve tam kelime olarak sayılır
/// (örnek kodda "aş" hece araması YAŞAR gibi adları şirket sayıyordu).
pub fn borclu_ayristir(b: &Value) -> TarafBilgisi {
    let nesne = |a: &str| b.get(a).filter(|v| v.is_object());
    let (kb, bolum) = if let Some(v) = nesne("kisiTumDVO") {
        (v, Some(false))
    } else if let Some(v) = nesne("kurumTumDVO") {
        (v, Some(true))
    } else {
        (nesne("tarafTumDVO").unwrap_or(b), None)
    };
    let tckn = rakamlar(&ilk_dolu(kb, &["tcKimlikNo", "tckn", "tc"]));
    let vergi_no = rakamlar(&ilk_dolu(kb, &["vergiNo", "vergiNumarasi"]));
    let mersis_no = rakamlar(&ilk_dolu(kb, &["mersisNo", "mersis"]));
    let mut adi = ilk_dolu(kb, &["adi", "ad"]);
    let mut soyadi = ilk_dolu(kb, &["soyadi", "soyad"]);
    let unvan = ilk_dolu(kb, &["unvan", "kurumAdi", "unvani"]);
    let tam_ad = format!("{adi} {soyadi}").trim().to_string();
    let kurum = bolum.unwrap_or_else(|| {
        if !vergi_no.is_empty() || !mersis_no.is_empty() {
            true
        } else if tckn.len() == 11 {
            false
        } else {
            // "A.Ş." kelimelere "a", "s" olarak ayrılır.
            let k = kelimeler(&format!("{tam_ad} {unvan}"));
            k.iter().any(|x| KURUM_KELIMELERI.contains(&x.as_str())) || k.windows(2).any(|w| w[0] == "a" && w[1] == "s")
        }
    });
    if kurum {
        return TarafBilgisi {
            tur: "tuzel".into(),
            unvan: if unvan.is_empty() { tam_ad } else { unvan },
            vergi_no,
            mersis_no,
            ..Default::default()
        };
    }
    if adi.is_empty() && !unvan.is_empty() {
        (adi, soyadi) = ad_soyad_ayir(&unvan);
    }
    TarafBilgisi { tur: "gercek".into(), ad: adi, soyad: soyadi, tckn, ..Default::default() }
}

/// Yalnız rakam; beklenen uzunlukta değilse (ör. UYAP'ın `***********` maskesi) `None`.
/// Maskeli ya da eksik kimlik asla kimlik sayılmaz.
pub fn kimlik_temizle(deger: &str, uzunluklar: &[usize]) -> Option<String> {
    let d = rakamlar(deger);
    uzunluklar.contains(&d.len()).then_some(d)
}

/// Taraf karşılaştırma anahtarı: Türkçe-duyarsız, boşluk / noktalama farkı yok.
pub fn ad_anahtari_taraf(tur: &str, ad: &str, soyad: &str, unvan: &str) -> String {
    let m = if tur == "tuzel" { unvan.to_string() } else { format!("{ad} {soyad}") };
    kelimeler(&m).join(" ")
}

/// Vekil alanı: "[AD1 SOYAD1, AD2 SOYAD2]" → [(ad, soyad)].
pub fn vekil_adlari(vekil_ham: &str) -> Vec<(String, String)> {
    let mut m = vekil_ham.trim();
    if m.starts_with('[') && m.ends_with(']') {
        m = m[1..m.len() - 1].trim();
    }
    m.split(',').map(str::trim).filter(|p| !p.is_empty()).map(ad_soyad_ayir).filter(|(ad, _)| !ad.is_empty()).collect()
}

// ── Tebligat → borçlu ───────────────────────────────────────────────────────────────────────────

fn muhatap_parantezi(aciklama: &str) -> Option<String> {
    static R: OnceLock<Regex> = OnceLock::new();
    re(&R, r"\[(.*?)\]").captures(aciklama).map(|m| m[1].trim().to_string())
}

/// Evrak açıklamasından (muhatap metni, borçlu adı). Muhatap metni ekranda gösterilir
/// ("BORÇLU VE MÜFLİS X", "3. Taraf DENİZBANK" …); borçlu adı yalnız etiket BORÇLU ise doludur.
pub fn tebligat_muhatabi(aciklama: &str) -> (String, Option<String>) {
    static R: OnceLock<Regex> = OnceLock::new();
    let Some(ic) = muhatap_parantezi(aciklama) else { return (String::new(), None) };
    let gorunen = re(&R, r"^Tebligat Yapılacak\s+").replace(&ic, "").into_owned();
    let kucuk = tr_kucuk(&ic);
    for etiket in ["borclu ve muflis ", "borclu "] {
        if kucuk.starts_with(etiket) {
            let ad: String = ic.chars().skip(etiket.chars().count()).collect();
            let ad = ad.trim();
            return (gorunen, (!ad.is_empty()).then(|| ad.to_string()));
        }
    }
    (gorunen, None)
}

fn tek<T: Copy>(adaylar: &[T]) -> Option<T> {
    if adaylar.len() == 1 { Some(adaylar[0]) } else { None }
}

/// MTS ödeme emirlerinde açıklamada muhatap yoktur; PTT yanıtındaki alıcı baş harfleriyle
/// maskelidir: "B* V* M* F* B*" = BORÇLU VE MÜFLİS FATMA BİROLOĞLU. Baş harfler tek bir
/// borçluyla aynıysa (ya da en az 3 harflik baş kısım tek borçlunun başıyla aynıysa) bağlanır.
fn ptt_alici_borclusu(ham_yanit: &Value, borclular: &[(i64, String)]) -> Option<i64> {
    let alici = ham_yanit.get("kabul").map(|k| metin(k, "alici")).unwrap_or_default();
    let harfler: Vec<char> = alici
        .split_whitespace()
        .filter_map(|p| p.chars().next())
        .filter(|c| c.is_alphanumeric())
        .filter_map(|c| tr_kucuk(&c.to_string()).chars().next())
        .collect();
    if harfler.len() < 5 || harfler[..3] != ['b', 'v', 'm'] {
        return None;
    }
    let hedef = &harfler[3..];
    let bas = |ad: &str| -> Vec<char> { kelimeler(ad).iter().filter_map(|k| k.chars().next()).collect() };
    let mut adaylar: Vec<i64> = borclular.iter().filter(|(_, ad)| bas(ad) == hedef).map(|b| b.0).collect();
    if adaylar.is_empty() && hedef.len() >= 3 {
        adaylar = borclular.iter().filter(|(_, ad)| bas(ad).starts_with(hedef)).map(|b| b.0).collect();
    }
    tek(&adaylar)
}

/// Tebligatın ait olduğu borçlu. `borclular`: dosyanın borçlu bağları (bağ kimliği, görünen ad).
/// Açıklamadaki BORÇLU muhatabının adı tam bir borçluyla eşleşirse o; değilse `None` (tahmin
/// yok). Açıklamada muhatap parantezi hiç yoksa (MTS) ve PTT yanıtı varsa alıcıya bakılır.
pub fn tebligat_borclusunu_bul(aciklama: &str, borclular: &[(i64, String)], ham_yanit: Option<&Value>) -> Option<i64> {
    if let Some(h) = ham_yanit.filter(|h| dolu(Some(*h))) {
        if muhatap_parantezi(aciklama).is_none() {
            return ptt_alici_borclusu(h, borclular);
        }
    }
    let (_, ad) = tebligat_muhatabi(aciklama);
    let hedef = kelimeler(&ad?);
    if hedef.is_empty() {
        return None;
    }
    let anahtarli: Vec<(i64, Vec<String>)> = borclular.iter().map(|(id, ad)| (*id, kelimeler(ad))).collect();
    let mut adaylar: Vec<i64> = anahtarli.iter().filter(|(_, k)| *k == hedef).map(|b| b.0).collect();
    if adaylar.is_empty() {
        let mut sirali = hedef.clone();
        sirali.sort();
        adaylar = anahtarli
            .iter()
            .filter(|(_, k)| {
                let mut s = k.clone();
                s.sort();
                s == sirali
            })
            .map(|b| b.0)
            .collect();
    }
    if adaylar.is_empty() && hedef.len() >= 5 {
        adaylar = anahtarli.iter().filter(|(_, k)| k.starts_with(&hedef)).map(|b| b.0).collect();
    }
    tek(&adaylar)
}

// ── Testler ─────────────────────────────────────────────────────────────────────────────────────

/// Sınamalar için küçük bir PDF üretir: verilen içerik akışı FlateDecode ile sıkıştırılır.
#[cfg(test)]
pub(crate) fn ornek_pdf(icerik: &[u8]) -> Vec<u8> {
    use std::io::Write;
    let mut z = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::default());
    z.write_all(icerik).unwrap();
    let sikisik = z.finish().unwrap();
    let mut pdf = b"%PDF-1.4\n1 0 obj\n<< /Type /Font /Length 5 >>\nstream\nabcde\nendstream\nendobj\n".to_vec();
    pdf.extend(format!("2 0 obj\n<< /Filter /FlateDecode /Length {} >>\nstream\n", sikisik.len()).bytes());
    pdf.extend(sikisik);
    pdf.extend(b"\nendstream\nendobj\n%%EOF\n");
    pdf
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bos() -> HashMap<String, String> {
        HashMap::new()
    }

    #[test]
    fn kucuk_harf_ve_dilim() {
        assert_eq!(tr_kucuk("İZMİR Işık ÇĞÖŞÜ"), "izmir isik cgosu");
        assert_eq!(tr_kucuk("İIıŞ").chars().count(), 4, "karakter sayısı korunur");
        let ozgun = "BU ZARFTA Ödeme İcra Emri VARDIR";
        let k = tr_kucuk(ozgun);
        let i = k.find("odeme").unwrap();
        assert_eq!(ozgun_dilim(ozgun, &k, i, i + "odeme icra emri".len()), "Ödeme İcra Emri");
        assert_eq!(tur_anahtar("  Kapalı   TEBLİĞ Mazbatası "), "kapali teblig mazbatasi");
    }

    #[test]
    fn mazbata_eslesmesi() {
        let a = "Satış İlanı - [VELİ KAYA] - Barkod Numarası: 3000120018142 - Tebliğ Edildi";
        assert_eq!(mazbata_barkodu(a).as_deref(), Some("3000120018142"));
        assert_eq!(mazbata_barkodu("4100140832731 Barkod Numaralı Tebligat Mazbatası.").as_deref(), Some("4100140832731"));
        assert_eq!(mazbata_barkodu("Tebliğ Mazbatası"), None);
        assert!(kendi_mazbatasi_dondu_mu("3000120018142", true, a));
        assert!(!kendi_mazbatasi_dondu_mu("3000120018143", true, a));
        assert!(!kendi_mazbatasi_dondu_mu("", true, a));
        assert!(teblig_sonucu_aciklamada_mi("Normal Tebligat[BORÇLU AHMET YILMAZ] - Tebliğ Edildi"));
        assert!(teblig_sonucu_aciklamada_mi("Normal Tebligat[BORÇLU X] -  TEBLİĞ EDİLEMEDİ "));
        assert!(!teblig_sonucu_aciklamada_mi("Normal Tebligat[BORÇLU AHMET YILMAZ]"));
        assert!(mazbatasi_dondu_mu("1", "x", true, ""), "barkodsuz 'Var' da dönmüş sayılır");
        assert!(!mazbatasi_dondu_mu("1", "x", false, a));

        let evraklar = vec![
            json!({"tur": HEDEF_TUR, "birimEvrakNo": 10, "aciklama": "Normal Tebligat[BORÇLU A B]"}),
            json!({"tur": TEBLIG_MAZBATASI_TUR, "birimEvrakNo": 12, "aciklama": a}),
            json!({"tur": TEBLIG_MAZBATASI_TUR, "birimEvrakNo": 13, "aciklama": ""}),
        ];
        let m = Mazbatalar::kur(&evraklar);
        assert_eq!(m.sayi(), 2);
        assert_eq!(m.bul("3000120018142").as_deref(), Some(a));
        assert_eq!(m.bul("999").as_deref(), Some("Var"), "tek tebligat + tek barkodsuz mazbata");
        let mut iki = evraklar.clone();
        iki.push(json!({"tur": MTS_TEBLIGAT_TUR, "birimEvrakNo": 11}));
        assert_eq!(Mazbatalar::kur(&iki).bul("999"), None, "iki tebligat varken tahmin yok");
    }

    #[test]
    fn pdf_barkod_ve_govde() {
        let pdf = ornek_pdf(b"BT /F4 12 Tf (*4007663523293*) Tj ET\nBT (BU ZARFTA \\326deme \\335cra Emri VARDIR) Tj (A\\(b\\)\\\\) Tj ET\n(*4007663523293*)Tj");
        assert_eq!(barkod_bul(&pdf).as_deref(), Some("4007663523293"));
        let govde = pdf_govde_metni(&pdf);
        assert!(govde.contains("BU ZARFTA Ödeme İcra Emri VARDIR"), "{govde}");
        assert!(govde.contains("A(b)\\"), "{govde}");
        assert_eq!(barkod_bul(b"<html>oturum</html>"), None, "PDF olmayan içerik");
        assert_eq!(barkod_bul(&ornek_pdf(b"(Merhaba) Tj")), None);
        assert_eq!(barkod_bul(&ornek_pdf(b"(*123*) Tj")), None, "çok kısa değer barkod değil");
        let mut bozuk = b"%PDF-1.4\n3 0 obj\n<< /Filter /FlateDecode >>\nstream\nbozukveri\nendstream\n".to_vec();
        bozuk.extend(&pdf[8..]);
        assert_eq!(barkod_bul(&bozuk).as_deref(), Some("4007663523293"), "bozuk akış atlanır");
    }

    #[test]
    fn tebligat_turleri() {
        let b = bos();
        let t = |a: &str, g: Option<&str>| tebligat_turunu_belirle(a, g, &b);
        assert_eq!(t("Normal Tebligat[BORÇLU X]", Some("BU ZARFTA Ödeme İcra Emri VARDIR")), "Ödeme Emri");
        assert_eq!(t("Ödeme Emri", None), "Ödeme Emri");
        assert_eq!(t("x", Some("Kıymet Takdiri raporu")), "Kıymet Takdiri Raporu");
        assert_eq!(t("Bilirkişi raporu tebliği", None), "Bilirkişi Raporu");
        assert_eq!(t("103 davetiyesi", None), "103 Davetiyesi");
        assert_eq!(t("Dosya 2026/103", None), "", "yalnız 103 yetmez");
        assert_eq!(
            t("Elektronik Tebligat[Tebligat Yapılacak 3. Taraf DENİZBANK ANONİM ŞİRKETİ]", Some("BU ZARFTA 89/1 Haciz İhbarnamesi VARDIR")),
            "Banka (Haciz İhbarnamesi)"
        );
        assert_eq!(t("Normal Tebligat[3. Taraf ABC LTD]", Some("BU ZARFTA maaş haciz ihbarnamesi VARDIR")), "Maaş (Haciz İhbarnamesi)");
        assert_eq!(t("Normal Tebligat[3. Taraf ABC LTD]", Some("BU ZARFTA Haciz İhbarnamesi VARDIR")), "Haciz İhbarnamesi");
        assert_eq!(t("x", Some("Maaşhaciz müzekkeresi vardır. BU ZARFTA Haciz Karar Yazısı VARDIR")), "Maaş (Haciz Müzekkeresi)");
        assert_eq!(t("Elektronik Tebligat[3. Taraf AKBANK T.A.Ş.]", None), "Banka");
        assert_eq!(t("x", Some("BU ZARFTA ücret kesinti yazısı VARDIR")), "Maaş");
        assert_eq!(t("Normal Tebligat[ALACAKLI X]", Some("BU ZARFTA  Satış İlanı Satış İlanı  VARDIR")), "Satış İlanı");
        assert_eq!(t("Normal Tebligat[ALACAKLI X]", Some("Mazbata formu şablon metni")), "Belirsiz");
        assert_eq!(t("Normal Tebligat[ALACAKLI X]", None), "");
        assert_eq!(t("Normal Tebligat[ALACAKLI X]", Some("")), "");
        // Kullanıcı eşleştirmesi: yazım ve tarih farkı gözetilmez
        let mut e = HashMap::new();
        e.insert(tur_eslesme_anahtari("13/01/2025 tarihli sıra cetveli ve derece kararı"), "Sıra Cetveli".to_string());
        assert_eq!(
            tebligat_turunu_belirle("x", Some("BU ZARFTA 05.02.2026 TARİHLİ DERECE KARARI VE SIRA CETVELİ VARDIR"), &e),
            "Sıra Cetveli"
        );
    }

    #[test]
    fn tur_anahtarlari() {
        assert_eq!(tur_eslesme_anahtari("Satış İlanı Satış İlanı"), tur_eslesme_anahtari("SATIŞ İLANI"));
        assert_eq!(
            tur_eslesme_anahtari("Ödeme İcra Emri Takibin Dayanagi Belge"),
            tur_eslesme_anahtari("Takibin Dayanagi Belge Ödeme İcra Emri")
        );
        assert_eq!(tur_eslesme_anahtari("13/01/2025 tarihli tensip zaptı"), "tensip zapti");
        assert_eq!(mazbata_belge_adi("Satış İlanı - [VELİ KAYA] - Barkod Numarası: 1"), "Satış İlanı");
        assert_eq!(mazbata_belge_adi("Var"), "");
        assert_eq!(tekrari_tekille("Satış İlanı Satış İlanı Satış İlanı"), "Satış İlanı");
        assert_eq!(tekrari_tekille("a a"), "a");
        assert_eq!(tekrari_tekille("Tensip zaptı"), "Tensip zaptı");
        assert_eq!(tekrari_tekille("aba"), "aba");
    }

    #[test]
    fn ptt_yaniti() {
        let teslim = json!({"errorState": true, "sondurum": {"son_durum_aciklama": "TESLİM EDİLDİ", "teslim_tarihi": 20260727, "son_islem_tarihi": "20260726"},
            "hareketDongu": [
                {"tarih": "25/07/2026", "aciklama": "KABUL EDİLDİ", "islem_detay": ""},
                {"tarih": "27/07/2026", "aciklama": "TESLİM", "islem_detay": "21.MAD. GORE MUHTARA TESLiM / m* m*"},
                {"tarih": "27/07/2026", "aciklama": "x", "islem_detay": "Göndericisine Teslim Edildi"}]});
        assert_eq!(ptt_durum_ve_tarih(&teslim), ("TESLİM EDİLDİ".into(), "27/07/2026".into()));
        assert_eq!(ptt_teslim_sekli(&teslim), "Muhtara teslim (21. md.)");
        let dagitim = json!({"errorState": true, "sondurum": {"son_durum_aciklama": "DAĞITIMDA", "teslim_tarihi": "0", "son_islem_tarihi": "20260726"}});
        assert_eq!(ptt_durum_ve_tarih(&dagitim), ("DAĞITIMDA".into(), "26/07/2026".into()));
        assert_eq!(ptt_teslim_sekli(&dagitim), "");
        let iade = json!({"errorState": true, "sondurum": {"son_durum_aciklama": "İADE EDİLDİ", "teslim_tarihi": "0", "son_islem_tarihi": "20260801"},
            "hareketDongu": [{"islem_detay": "ADRESTE TANINMIYOR"}, {"islem_detay": "KÖY DAĞITIMINA TABİ BEKLİYOR"}]});
        assert_eq!(ptt_teslim_sekli(&iade), "ADRESTE TANINMIYOR");
        assert_eq!(ptt_durum_ve_tarih(&json!({"errorState": false, "errorMessage": "Gönderi bulunamadı"})).0, "Gönderi bulunamadı");
        assert_eq!(ptt_durum_ve_tarih(&json!({"errorState": false})).0, "Kayıt yok");
        assert_eq!(ptt_durum_ve_tarih(&json!({"_hata": "PTT HTTP 404"})), ("PTT HTTP 404".into(), String::new()));
        let hareketli = json!({"errorState": true, "hareketDongu": [{"tarih": "01/08/2026", "saat": "10:15", "aciklama": "YOLDA"}]});
        assert_eq!(ptt_durum_ve_tarih(&hareketli), ("YOLDA".into(), "01/08/2026 10:15".into()));
        assert_eq!(ptt_teslim_sekli(&ptt_sonuclanmis()), "");
        assert!(ptt_sonuclandi("TESLİM EDİLDİ") && ptt_sonuclandi(" iade ") && ptt_sonuclandi("İADE"));
        assert!(!ptt_sonuclandi("İADE EDİLDİ") && !ptt_sonuclandi("DAĞITIMDA") && !ptt_sonuclandi(""));
        assert!(e_teblig_sonuclandi(E_TEBLIG_DURUM_METNI, "01/10/2026"));
        assert!(!e_teblig_sonuclandi(E_TEBLIG_DURUM_METNI, "") && !e_teblig_sonuclandi("TESLİM EDİLDİ", "01/10/2026"));
        assert_eq!(e_teblig_teblig_tarihi("01/10/2026 14:05"), "01/10/2026");
        assert_eq!(e_teblig_teblig_tarihi("31/02/2026"), "");
        assert_eq!(e_teblig_teblig_tarihi("2026-10-01"), "");
    }

    #[test]
    fn taraf_ayristirma() {
        let k = taraf_bilgisi_ayristir(&json!({"adi": " AHMET CAN YILMAZ ", "rol": "Borçlu", "kisiKurum": "Kişi"}));
        assert_eq!((k.tur.as_str(), k.ad.as_str(), k.soyad.as_str()), ("gercek", "AHMET CAN", "YILMAZ"));
        let k = taraf_bilgisi_ayristir(&json!({"adi": "MADONNA", "kisiKurum": "Kişi"}));
        assert_eq!((k.ad.as_str(), k.soyad.as_str()), ("MADONNA", ""));
        let k = taraf_bilgisi_ayristir(&json!({"adi": "X BANKASI A.Ş.", "kisiKurum": "KURUM"}));
        assert_eq!((k.tur.as_str(), k.unvan.as_str()), ("tuzel", "X BANKASI A.Ş."));
        assert!(taraf_bilgisi_ayristir(&json!({"rol": "Borçlu"})).bos_mu());

        let b = borclu_ayristir(&json!({"kisiTumDVO": {"adi": "YAŞAR", "soyadi": "BAŞARAN", "tcKimlikNo": "123 456 789 01"}}));
        assert_eq!((b.tur.as_str(), b.ad.as_str(), b.soyad.as_str(), b.tckn.as_str()), ("gercek", "YAŞAR", "BAŞARAN", "12345678901"));
        let b = borclu_ayristir(&json!({"kurumTumDVO": {"kurumAdi": "SARKA KABLO A.Ş.", "vergiNo": "1234567890", "mersisNo": "0123456789012345"}}));
        assert_eq!((b.tur.as_str(), b.unvan.as_str(), b.vergi_no.as_str(), b.mersis_no.as_str()), ("tuzel", "SARKA KABLO A.Ş.", "1234567890", "0123456789012345"));
        let b = borclu_ayristir(&json!({"adi": "YAŞAR", "soyadi": "KAYA"}));
        assert_eq!(b.tur, "gercek", "ad içindeki 'aş' hecesi şirket yapmaz");
        let b = borclu_ayristir(&json!({"adi": "ABC İNŞAAT LTD. ŞTİ."}));
        assert_eq!((b.tur.as_str(), b.unvan.as_str()), ("tuzel", "ABC İNŞAAT LTD. ŞTİ."));
        let b = borclu_ayristir(&json!({"unvan": "MEHMET ALİ DEMİR"}));
        assert_eq!((b.tur.as_str(), b.ad.as_str(), b.soyad.as_str()), ("gercek", "MEHMET ALİ", "DEMİR"));

        assert_eq!(kimlik_temizle("12345678901", &[11]).as_deref(), Some("12345678901"));
        assert_eq!(kimlik_temizle("***********", &[11]), None);
        assert_eq!(kimlik_temizle("123*****901", &[11]), None);
        assert_eq!(kimlik_temizle("1234567890", &[10, 11]).as_deref(), Some("1234567890"));
        assert_eq!(ad_anahtari_taraf("gercek", "Ahmet  Can", "YILMAZ", ""), "ahmet can yilmaz");
        assert_eq!(ad_anahtari_taraf("tuzel", "", "", "X Bankası A.Ş."), "x bankasi a s");
        assert_eq!(vekil_adlari("[HAKAN TOLUNAY BURHAN, AYŞE KAYA]"), vec![("HAKAN TOLUNAY".into(), "BURHAN".into()), ("AYŞE".into(), "KAYA".into())]);
        assert!(vekil_adlari("[]").is_empty() && vekil_adlari("").is_empty());
    }

    #[test]
    fn tebligat_borclusu() {
        let borclular = vec![(1, "AHMET YILMAZ".to_string()), (2, "FATMA BİROLOĞLU".to_string()), (3, "SARKA ENDÜSTRİYEL ELEKTRİK KABLO A.Ş.".to_string())];
        let bul = |a: &str| tebligat_borclusunu_bul(a, &borclular, None);
        assert_eq!(tebligat_muhatabi("Normal Tebligat[BORÇLU VE MÜFLİS FATMA BİROLOĞLU] - Tebliğ Edildi"), ("BORÇLU VE MÜFLİS FATMA BİROLOĞLU".into(), Some("FATMA BİROLOĞLU".into())));
        assert_eq!(tebligat_muhatabi("Elektronik Tebligat[Tebligat Yapılacak 3. Taraf DENİZBANK]"), ("3. Taraf DENİZBANK".into(), None));
        assert_eq!(tebligat_muhatabi("PTT MTS Tebligat Belgesi"), (String::new(), None));
        assert_eq!(bul("Normal Tebligat[BORÇLU AHMET YILMAZ]"), Some(1));
        assert_eq!(bul("Normal Tebligat[BORÇLU YILMAZ AHMET]"), Some(1), "sırasız eşleşme");
        assert_eq!(bul("Normal Tebligat[BORÇLU VE MÜFLİS Fatma Biroloğlu]"), Some(2));
        assert_eq!(bul("Normal Tebligat[BORÇLU MEHMET YILMAZ]"), None);
        assert_eq!(bul("Normal Tebligat[ALACAKLI AHMET YILMAZ]"), None);
        assert_eq!(bul("Normal Tebligat[BORÇLU SARKA ENDÜSTRİYEL ELEKTRİK KABLO A]"), Some(3), "kesilmiş uzun unvan");
        let adas = vec![(1, "AHMET YILMAZ".to_string()), (2, "Ahmet Yılmaz".to_string())];
        assert_eq!(tebligat_borclusunu_bul("x[BORÇLU AHMET YILMAZ]", &adas, None), None, "iki aday varken tahmin yok");
        // MTS: açıklamada muhatap yok, PTT alıcısı maskeli
        let ptt = json!({"kabul": {"alici": "B* V* M* F* B*"}});
        assert_eq!(tebligat_borclusunu_bul("PTT MTS Tebligat Belgesi", &borclular, Some(&ptt)), Some(2));
        let ptt = json!({"kabul": {"alici": "B* V* M* S* E* E*"}});
        assert_eq!(tebligat_borclusunu_bul("PTT MTS Tebligat Belgesi", &borclular, Some(&ptt)), Some(3), "kesilmiş unvan baş harfleri");
        let ptt = json!({"kabul": {"alici": "A* Y*"}});
        assert_eq!(tebligat_borclusunu_bul("PTT MTS Tebligat Belgesi", &borclular, Some(&ptt)), None);
        assert_eq!(tebligat_borclusunu_bul("PTT MTS Tebligat Belgesi", &borclular, None), None);
    }

    #[test]
    fn evrak_listesi_duzleme() {
        let sayfa = json!({"tumEvraklar": {"son": [{"evrakId": "1"}, "bozuk"], "eski": [{"evrakId": "2"}], "x": 5}});
        assert_eq!(evraklari_duzle(&sayfa).len(), 2);
        assert!(evraklari_duzle(&json!({"errorCode": "X"})).is_empty());
    }
}
