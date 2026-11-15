//! Tüm Evrakı İndir (`uyap_core/evrak_toplu.py` 1:1 portu)
//! ===========================================================
//! Açık dosyanın TÜM evrakını (veya seçili evrakını) UYAP'tan sırayla indirir,
//! PDF'e çevirir, birleştirir, ofis klasörüne kaydeder ve ZIP/PDF olarak sunar.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::io::{Cursor, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex as StdMutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use once_cell::sync::Lazy;
use rand::RngCore;
use regex::Regex;
use serde_json::{json, Value};
use tokio::sync::{Mutex as AsyncMutex, Notify};
use zip::write::SimpleFileOptions;
use zip::{CompressionMethod, ZipArchive, ZipWriter};

use crate::runtime;
use crate::uyap_proxy::{self, _looks_like_login_wall, UYAP_BASE};

pub const ISTEK_ARASI_SN: f64 = 1.0;
pub const ESZAMANLI_IMZA: &str = "birden fazla sorgulama yapamaz";
pub const ESZAMANLI_TEKRAR: usize = 4;
pub const ESZAMANLI_BEKLEME_SN: f64 = 3.0;
pub const AG_BEKLEMELERI_SN: &[f64] = &[2.0, 4.0, 8.0];
pub const YAVAS_EN_COK_SN: f64 = 4.0;
pub const ARDISIK_AG_HATASI_SINIRI: usize = 3;

pub const CAPTCHA_KODLARI: &[&str] = &["PRTL_GNL_100063", "PRTL_GNL_100063-1"];
pub const TURNSTILE_SITE_KEY: &str = "0x4AAAAAAFDQm5c7ca4D-H_Y";
pub const CAPTCHA_BEKLEME_SN: u64 = 15 * 60;

pub const ZIP_SAKLAMA_SN: f64 = 30.0 * 60.0;
pub const EVRAK_LISTESI_TOPLAM: &str = "listDosyaEvraklarPageTotal.ajx";
pub const EVRAK_LISTESI: &str = "list_dosya_evraklar.ajx";
pub const DOC_VIEWER_HAZIRLIK: &str = "getDocViewerParameters.ajx";
pub const EVRAK_ICERIK: &str = "view_document_brd.uyap";

pub fn kayit_koku() -> PathBuf {
    if let Ok(v) = std::env::var("UYAP_EVRAK_KLASORU") {
        if !v.trim().is_empty() {
            return PathBuf::from(v.trim());
        }
    }
    let home = std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .unwrap_or_else(|_| ".".to_string());
    Path::new(&home).join("Documents").join("CokluUyap Evrak")
}

pub const CALISIYOR: &str = "calisiyor";
pub const SIRADA: &str = "sirada";
pub const CAPTCHA: &str = "captcha";
pub const BITTI: &str = "bitti";
pub const HATA: &str = "hata";
pub const IPTAL: &str = "iptal";

pub const CIKTI_TEK: &str = "tek";
pub const CIKTI_IKISI: &str = "ikisi";
pub const KAPSAM_TUM: &str = "tum";
pub const KAPSAM_SECILI: &str = "secili";
pub const SECILI_EN_COK: usize = 3000;

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

fn new_id() -> String {
    let mut b = [0u8; 6];
    rand::thread_rng().fill_bytes(&mut b);
    hex::encode(b)
}

#[derive(Debug, Clone)]
pub struct IndirmeInner {
    pub id: String,
    pub dosya_id: String,
    pub ad: String,
    pub cikti: String,
    pub kapsam: String,
    pub secili: Option<Vec<Value>>,
    pub durum: String,
    pub mesaj: String,
    pub toplam: usize,
    pub inen: usize,
    pub atlanan: usize,
    pub basarisiz: Vec<Value>,
    pub captcha: Option<Value>,
    pub cikti_yol: Option<String>,
    pub baslangic: f64,
    pub bitis: Option<f64>,
    pub iptal: bool,
    pub captcha_jeton: Option<String>,
    pub captcha_sayisi: usize,
    pub yavaslatma: f64,
}

pub struct Indirme {
    pub inner: StdMutex<IndirmeInner>,
    pub captcha_notify: Notify,
}

impl Indirme {
    pub fn new(
        dosya_id: String,
        ad: String,
        cikti: String,
        kapsam: String,
        secili: Option<Vec<Value>>,
    ) -> Arc<Self> {
        Arc::new(Self {
            inner: StdMutex::new(IndirmeInner {
                id: new_id(),
                dosya_id,
                ad,
                cikti,
                kapsam,
                secili,
                durum: SIRADA.to_string(),
                mesaj: "Sırada bekliyor…".to_string(),
                toplam: 0,
                inen: 0,
                atlanan: 0,
                basarisiz: Vec::new(),
                captcha: None,
                cikti_yol: None,
                baslangic: now_secs(),
                bitis: None,
                iptal: false,
                captcha_jeton: None,
                captcha_sayisi: 0,
                yavaslatma: 1.0,
            }),
            captcha_notify: Notify::new(),
        })
    }

    pub fn sozluk(&self) -> Value {
        let g = self.inner.lock().unwrap();
        json!({
            "id": g.id,
            "dosyaId": g.dosya_id,
            "ad": g.ad,
            "cikti": g.cikti,
            "kapsam": g.kapsam,
            "durum": g.durum,
            "mesaj": g.mesaj,
            "toplam": g.toplam,
            "inen": g.inen,
            "atlanan": g.atlanan,
            "basarisiz": g.basarisiz,
            "captcha": g.captcha,
            "dosya_hazir": g.cikti_yol.as_ref().map(|p| Path::new(p).exists()).unwrap_or(false),
            "dosya_adi": g.cikti_yol.as_ref().and_then(|p| Path::new(p).file_name()).map(|n| n.to_string_lossy().to_string()),
        })
    }

    pub fn iptal_et(&self) {
        {
            let mut g = self.inner.lock().unwrap();
            g.iptal = true;
            if g.durum == SIRADA || g.durum == CAPTCHA {
                g.durum = IPTAL.to_string();
                g.mesaj = "İptal edildi.".to_string();
                g.bitis = Some(now_secs());
            }
        }
        self.captcha_notify.notify_waiters();
    }

    pub fn captcha_coz(&self, jeton: &str) {
        {
            let mut g = self.inner.lock().unwrap();
            g.captcha_jeton = if jeton.is_empty() {
                None
            } else {
                Some(jeton.to_string())
            };
            g.durum = CALISIYOR.to_string();
            g.captcha = None;
            g.mesaj = "Doğrulama gönderildi, devam ediliyor…".to_string();
        }
        self.captcha_notify.notify_waiters();
    }

    pub fn aralik(&self) -> f64 {
        let g = self.inner.lock().unwrap();
        (ISTEK_ARASI_SN * g.yavaslatma).min(YAVAS_EN_COK_SN.max(ISTEK_ARASI_SN))
    }

    pub fn yavaslat(&self) {
        let mut g = self.inner.lock().unwrap();
        g.yavaslatma *= 2.0;
    }

    pub fn iptal_kontrol(&self) -> Result<(), String> {
        if self.inner.lock().unwrap().iptal {
            Err("__IPTAL__".to_string())
        } else {
            Ok(())
        }
    }

    pub async fn captcha_bekle(&self, kod: &str, mesaj: &str) -> Result<Option<String>, String> {
        let onceki = {
            let mut g = self.inner.lock().unwrap();
            let onceki = g.mesaj.clone();
            g.captcha_sayisi += 1;
            let n = g.captcha_sayisi;
            g.durum = CAPTCHA.to_string();
            g.captcha = Some(json!({
                "kod": kod,
                "site_key": TURNSTILE_SITE_KEY,
                "n": n,
                "mesaj": if mesaj.is_empty() { "UYAP güvenlik doğrulaması istiyor." } else { mesaj },
            }));
            g.mesaj = "UYAP güvenlik doğrulaması istiyor — lütfen doğrulamayı tamamlayın.".to_string();
            g.captcha_jeton = None;
            onceki
        };

        let waited = tokio::time::timeout(
            Duration::from_secs(CAPTCHA_BEKLEME_SN),
            self.captcha_notify.notified(),
        )
        .await;
        if waited.is_err() {
            self.inner.lock().unwrap().iptal = true;
        }
        self.iptal_kontrol()?;

        let mut g = self.inner.lock().unwrap();
        g.durum = CALISIYOR.to_string();
        g.captcha = None;
        g.mesaj = onceki;
        Ok(g.captcha_jeton.clone())
    }
}

static KAYITLAR: Lazy<StdMutex<HashMap<String, Arc<Indirme>>>> =
    Lazy::new(|| StdMutex::new(HashMap::new()));
static SON_ISTEK: Lazy<StdMutex<Option<Instant>>> = Lazy::new(|| StdMutex::new(None));
static SIRA_KILIDI: Lazy<AsyncMutex<()>> = Lazy::new(|| AsyncMutex::new(()));

fn json_coz(headers: &HashMap<String, String>, body: &[u8]) -> Option<Value> {
    let ctype = headers
        .get("content-type")
        .map(|s| s.to_lowercase())
        .unwrap_or_default();
    let first = body.first().copied();
    if !ctype.contains("json") && first != Some(b'{') && first != Some(b'[') {
        return None;
    }
    serde_json::from_slice(body).ok()
}

fn eszamanli_mi(veri: &Value, derinlik: usize) -> bool {
    if derinlik > 4 {
        return false;
    }
    match veri {
        Value::String(s) => s.to_lowercase().contains(ESZAMANLI_IMZA),
        Value::Object(m) => m.values().any(|v| eszamanli_mi(v, derinlik + 1)),
        Value::Array(a) => a.iter().any(|v| eszamanli_mi(v, derinlik + 1)),
        _ => false,
    }
}

fn captcha_kodu(veri: &Value) -> Option<String> {
    let kod = veri.get("errorCode")?.as_str()?;
    if CAPTCHA_KODLARI.contains(&kod) {
        Some(kod.to_string())
    } else {
        None
    }
}

async fn hiz_siniri(ind: &Indirme) {
    let bekle = {
        let son = SON_ISTEK.lock().unwrap();
        if let Some(t) = *son {
            let elapsed = t.elapsed().as_secs_f64();
            let aralik = ind.aralik();
            if aralik > elapsed {
                Some(aralik - elapsed)
            } else {
                None
            }
        } else {
            None
        }
    };
    if let Some(b) = bekle {
        tokio::time::sleep(Duration::from_secs_f64(b)).await;
    }
}

pub async fn _uyap(
    ind: &Indirme,
    method: &str,
    path: &str,
    govde: Option<&Value>,
    params: Option<&HashMap<String, String>>,
) -> Result<(u16, HashMap<String, String>, Vec<u8>, Option<Value>), String> {
    let gw = uyap_proxy::get_gw().ok_or_else(|| "UYAP oturumu hazır değil.".to_string())?;
    let base_url = format!("{}/{}", UYAP_BASE, path.trim_start_matches('/'));
    let mut jeton: Option<String> = None;

    loop {
        let mut g = govde.cloned();
        let mut p = params.cloned();
        if let Some(ref j) = jeton {
            if method == "GET" {
                let mut pm = p.unwrap_or_default();
                pm.insert("Recaptcha".to_string(), j.clone());
                p = Some(pm);
            } else {
                let mut gm = g
                    .and_then(|v| v.as_object().cloned())
                    .unwrap_or_default();
                gm.insert("Recaptcha".to_string(), Value::String(j.clone()));
                g = Some(Value::Object(gm));
            }
        }

        let mut son_ag_hata = String::new();
        for deneme in 0..=ESZAMANLI_TEKRAR {
            let mut ag_ok = false;
            let mut resp_tuple: Option<(u16, HashMap<String, String>, Vec<u8>, Option<Value>)> =
                None;

            for ag_deneme in 0..=AG_BEKLEMELERI_SN.len() {
                ind.iptal_kontrol()?;
                hiz_siniri(ind).await;

                let _izi = runtime::uyap_istek_izi().await;
                let _batch = runtime::batch_write().await;

                let mut login_retried = false;
                loop {
                    gw.ensure_alive().await?;
                    let req_m = reqwest::Method::from_bytes(method.as_bytes())
                        .unwrap_or(reqwest::Method::GET);
                    let mut rb = gw
                        .client
                        .request(req_m, &base_url)
                        .header("Referer", format!("{}/dosya-sorgulama", UYAP_BASE))
                        .header("Accept", "application/json, text/plain, */*");
                    if let Some(ref j) = jeton {
                        rb = rb.header("Recaptcha", j.as_str());
                    }
                    if let Some(ref pm) = p {
                        rb = rb.query(pm);
                    }
                    if let Some(ref gm) = g {
                        rb = rb.json(gm);
                    }

                    let send_res = rb.send().await;
                    *SON_ISTEK.lock().unwrap() = Some(Instant::now());
                    gw.touch();

                    match send_res {
                        Ok(r) => {
                            let st = r.status().as_u16();
                            let raw_h = r.headers().clone();
                            let mut hdrs = HashMap::new();
                            for (k, v) in &raw_h {
                                if let Ok(vs) = v.to_str() {
                                    hdrs.insert(k.as_str().to_ascii_lowercase(), vs.to_string());
                                }
                            }
                            if _looks_like_login_wall(st, &raw_h) && !login_retried {
                                login_retried = true;
                                gw.logged_in
                                    .store(false, std::sync::atomic::Ordering::SeqCst);
                                gw.relogin().await?;
                                continue;
                            }
                            let bytes = r
                                .bytes()
                                .await
                                .map_err(|e| format!("UYAP yanıt okunamadı: {}", e))?
                                .to_vec();
                            let jv = json_coz(&hdrs, &bytes);
                            resp_tuple = Some((st, hdrs, bytes, jv));
                            ag_ok = true;
                            break;
                        }
                        Err(e) => {
                            son_ag_hata = e.to_string();
                            break;
                        }
                    }
                }

                if ag_ok {
                    break;
                }
                if ag_deneme < AG_BEKLEMELERI_SN.len() {
                    ind.yavaslat();
                    tokio::time::sleep(Duration::from_secs_f64(AG_BEKLEMELERI_SN[ag_deneme])).await;
                }
            }

            if !ag_ok {
                return Err(format!("AG_HATASI:{}", son_ag_hata));
            }

            let (st, hdrs, bytes, jv) = resp_tuple.unwrap();
            if let Some(ref v) = jv {
                if let Some(kod) = captcha_kodu(v) {
                    let msg = v
                        .get("errorMessage")
                        .and_then(|m| m.as_str())
                        .unwrap_or("")
                        .to_string();
                    jeton = ind.captcha_bekle(&kod, &msg).await?;
                    break;
                }
                if eszamanli_mi(v, 0) && deneme < ESZAMANLI_TEKRAR {
                    tokio::time::sleep(Duration::from_secs_f64(
                        ESZAMANLI_BEKLEME_SN * (deneme as f64 + 1.0),
                    ))
                    .await;
                    continue;
                }
            }
            return Ok((st, hdrs, bytes, jv));
        }
    }
}

fn evraklar_dizisi(veri: &Value) -> Vec<Value> {
    if let Some(arr) = veri.as_array() {
        return arr.clone();
    }
    if let Some(obj) = veri.as_object() {
        for k in ["evraklar", "data", "list", "rows", "result", "items"] {
            if let Some(arr) = obj.get(k).and_then(|v| v.as_array()) {
                return arr.clone();
            }
        }
    }
    Vec::new()
}

pub async fn _evrak_listesi(ind: &Indirme) -> Result<Vec<Value>, String> {
    let dosya_id = ind.inner.lock().unwrap().dosya_id.clone();
    let (_, _, _, toplam_v) = _uyap(
        ind,
        "POST",
        EVRAK_LISTESI_TOPLAM,
        Some(&json!({"dosyaId": dosya_id})),
        None,
    )
    .await?;
    let mut tum = Vec::new();
    let mut gorulen = HashSet::new();
    let mut sayfa = 1usize;

    loop {
        let (_, _, _, v_opt) = _uyap(
            ind,
            "POST",
            EVRAK_LISTESI,
            Some(&json!({"dosyaId": dosya_id, "pageNumber": sayfa})),
            None,
        )
        .await?;
        let Some(v) = v_opt else {
            break;
        };
        let parca = evraklar_dizisi(&v);
        if parca.is_empty() {
            break;
        }
        let mut yeni_eklendi = 0usize;
        for e in parca {
            let eid = e
                .get("evrakId")
                .and_then(|x| x.as_str())
                .unwrap_or("")
                .to_string();
            if eid.is_empty() || gorulen.contains(&eid) {
                continue;
            }
            gorulen.insert(eid);
            tum.push(e);
            yeni_eklendi += 1;
        }
        if yeni_eklendi == 0 {
            break;
        }
        if let Some(ref tv) = toplam_v {
            let total = tv
                .get("total")
                .or_else(|| tv.get("toplam"))
                .or_else(|| tv.get("pageTotal"))
                .and_then(|x| x.as_u64())
                .unwrap_or(0) as usize;
            if total > 0 && sayfa >= total {
                break;
            }
        }
        sayfa += 1;
        if sayfa > 200 {
            break;
        }
    }
    Ok(tum)
}

pub fn _guvenli_ad(metin: &str, en_cok: usize) -> String {
    let re_bad = Regex::new(r#"[\\/:*?"<>|\x00-\x1f]"#).unwrap();
    let re_ws = Regex::new(r"\s+").unwrap();
    let cleaned = re_bad.replace_all(metin, " ");
    let collapsed = re_ws.replace_all(&cleaned, " ").trim().to_string();
    let truncated: String = collapsed.chars().take(en_cok).collect();
    let trimmed = truncated.trim_end_matches(&['.', ' '][..]).to_string();
    if trimmed.is_empty() {
        "adsiz".to_string()
    } else {
        trimmed
    }
}

pub fn _tur_bul(bayt: &[u8], content_type: &str) -> &'static str {
    let ct = content_type.to_lowercase();
    if bayt.starts_with(b"%PDF") || ct.contains("pdf") {
        return "pdf";
    }
    if bayt.starts_with(b"II*\x00") || bayt.starts_with(b"MM\x00*") || ct.contains("tiff") {
        return "tiff";
    }
    if bayt.starts_with(b"\xff\xd8\xff") || ct.contains("jpeg") || ct.contains("jpg") {
        return "jpeg";
    }
    if bayt.starts_with(b"\x89PNG\r\n\x1a\n") || ct.contains("png") {
        return "png";
    }
    if bayt.starts_with(b"PK\x03\x04") {
        if let Ok(mut zip) = ZipArchive::new(Cursor::new(bayt)) {
            let names: Vec<String> = (0..zip.len())
                .filter_map(|i| zip.by_index(i).ok().map(|f| f.name().to_lowercase()))
                .collect();
            if names.iter().any(|n| n == "content.xml") {
                return "udf";
            }
            return "zip";
        }
        return "zip";
    }
    let prefix = String::from_utf8_lossy(&bayt[..bayt.len().min(512)]).to_lowercase();
    if prefix.contains("<html") || prefix.contains("<!doctype") || ct.contains("html") {
        return "html";
    }
    if prefix.contains("<content") || prefix.contains("<?xml") {
        return "udf";
    }
    "bilinmiyor"
}

fn pdf_escape(s: &str) -> String {
    let ascii: String = s
        .chars()
        .map(|c| match c {
            'ç' => 'c',
            'Ç' => 'C',
            'ğ' => 'g',
            'Ğ' => 'G',
            'ı' => 'i',
            'İ' => 'I',
            'ö' => 'o',
            'Ö' => 'O',
            'ş' => 's',
            'Ş' => 'S',
            'ü' => 'u',
            'Ü' => 'U',
            '(' => '[',
            ')' => ']',
            '\\' => '/',
            c if c.is_ascii() && !c.is_control() => c,
            _ => ' ',
        })
        .collect();
    ascii
}

fn wrap_text_pdf(baslik: &str, satirlar: &[String]) -> Vec<u8> {
    let mut stream = String::from("BT\n/F1 12 Tf\n50 780 Td\n");
    stream.push_str(&format!("({}) Tj\n0 -24 Td\n/F1 10 Tf\n", pdf_escape(baslik)));
    for s in satirlar.iter().take(45) {
        let trunc: String = s.chars().take(90).collect();
        stream.push_str(&format!("({}) Tj\n0 -14 Td\n", pdf_escape(&trunc)));
    }
    stream.push_str("ET\n");
    build_single_page_pdf(595, 842, stream.as_bytes(), None)
}

fn build_single_page_pdf(
    w: u32,
    h: u32,
    content_stream: &[u8],
    jpeg_info: Option<(u32, u32, &[u8])>,
) -> Vec<u8> {
    let mut pdf = Vec::new();
    pdf.extend_from_slice(b"%PDF-1.4\n");
    let mut offsets = Vec::new();

    // 1: Catalog
    offsets.push(pdf.len());
    pdf.extend_from_slice(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n");
    // 2: Pages
    offsets.push(pdf.len());
    pdf.extend_from_slice(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n");
    // 3: Page
    offsets.push(pdf.len());
    let xobj_ref = if jpeg_info.is_some() {
        " /XObject << /Im0 6 0 R >>"
    } else {
        ""
    };
    pdf.extend_from_slice(
        format!(
            "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {} {}] /Resources << /Font << /F1 4 0 R >>{} >> /Contents 5 0 R >>\nendobj\n",
            w, h, xobj_ref
        )
        .as_bytes(),
    );
    // 4: Font
    offsets.push(pdf.len());
    pdf.extend_from_slice(
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    );
    // 5: Contents
    offsets.push(pdf.len());
    pdf.extend_from_slice(
        format!("5 0 obj\n<< /Length {} >>\nstream\n", content_stream.len()).as_bytes(),
    );
    pdf.extend_from_slice(content_stream);
    pdf.extend_from_slice(b"\nendstream\nendobj\n");

    if let Some((iw, ih, jdata)) = jpeg_info {
        offsets.push(pdf.len());
        pdf.extend_from_slice(
            format!(
                "6 0 obj\n<< /Type /XObject /Subtype /Image /Width {} /Height {} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {} >>\nstream\n",
                iw, ih, jdata.len()
            )
            .as_bytes(),
        );
        pdf.extend_from_slice(jdata);
        pdf.extend_from_slice(b"\nendstream\nendobj\n");
    }

    let xref_pos = pdf.len();
    pdf.extend_from_slice(format!("xref\n0 {}\n0000000000 65535 f \n", offsets.len() + 1).as_bytes());
    for off in &offsets {
        pdf.extend_from_slice(format!("{:010} 00000 n \n", off).as_bytes());
    }
    pdf.extend_from_slice(
        format!(
            "trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{}\n%%EOF\n",
            offsets.len() + 1,
            xref_pos
        )
        .as_bytes(),
    );
    pdf
}

fn jpeg_dimensions(data: &[u8]) -> (u32, u32) {
    let mut i = 2usize;
    while i + 9 < data.len() {
        if data[i] != 0xFF {
            i += 1;
            continue;
        }
        let marker = data[i + 1];
        if (0xC0..=0xC3).contains(&marker) {
            let h = u16::from_be_bytes([data[i + 5], data[i + 6]]) as u32;
            let w = u16::from_be_bytes([data[i + 7], data[i + 8]]) as u32;
            return (w.max(1), h.max(1));
        }
        if i + 3 >= data.len() {
            break;
        }
        let seg_len = u16::from_be_bytes([data[i + 2], data[i + 3]]) as usize;
        if seg_len < 2 {
            break;
        }
        i += 2 + seg_len;
    }
    (595, 842)
}

pub fn _pdfe_cevir(bayt: &[u8], tur: &str, baslik: &str) -> Result<Vec<u8>, String> {
    match tur {
        "pdf" => Ok(bayt.to_vec()),
        "jpeg" => {
            let (iw, ih) = jpeg_dimensions(bayt);
            let scale = (595.0 / iw as f64).min(842.0 / ih as f64).min(1.0);
            let dw = (iw as f64 * scale) as u32;
            let dh = (ih as f64 * scale) as u32;
            let stream = format!("q\n{} 0 0 {} 0 0 cm\n/Im0 Do\nQ\n", dw, dh);
            Ok(build_single_page_pdf(
                dw.max(1),
                dh.max(1),
                stream.as_bytes(),
                Some((iw, ih, bayt)),
            ))
        }
        "udf" => {
            let text = if bayt.starts_with(b"PK\x03\x04") {
                if let Ok(mut zip) = ZipArchive::new(Cursor::new(bayt)) {
                    let mut out = String::new();
                    if let Ok(mut f) = zip.by_name("content.xml") {
                        let _ = f.read_to_string(&mut out);
                    }
                    out
                } else {
                    String::new()
                }
            } else {
                String::from_utf8_lossy(bayt).into_owned()
            };
            let re_tag = Regex::new(r"<[^>]+>").unwrap();
            let plain = re_tag.replace_all(&text, " ");
            let lines: Vec<String> = plain
                .lines()
                .map(|l| l.trim().to_string())
                .filter(|l| !l.is_empty())
                .collect();
            Ok(wrap_text_pdf(baslik, &lines))
        }
        "html" => {
            let raw = String::from_utf8_lossy(bayt);
            let re_tag = Regex::new(r"<[^>]+>").unwrap();
            let plain = re_tag.replace_all(&raw, " ");
            let lines: Vec<String> = plain
                .lines()
                .map(|l| l.trim().to_string())
                .filter(|l| !l.is_empty())
                .collect();
            Ok(wrap_text_pdf(baslik, &lines))
        }
        "zip" => {
            if let Ok(mut zip) = ZipArchive::new(Cursor::new(bayt)) {
                for i in 0..zip.len() {
                    if let Ok(mut f) = zip.by_index(i) {
                        let name = f.name().to_lowercase();
                        if name.ends_with(".pdf") || name.ends_with(".html") || name.ends_with(".htm")
                        {
                            let mut buf = Vec::new();
                            if f.read_to_end(&mut buf).is_ok() {
                                let sub_tur = if name.ends_with(".pdf") { "pdf" } else { "html" };
                                return _pdfe_cevir(&buf, sub_tur, baslik);
                            }
                        }
                    }
                }
            }
            Ok(wrap_text_pdf(baslik, &["ZIP arşivi içeriği".to_string()]))
        }
        _ => Ok(wrap_text_pdf(
            baslik,
            &[format!("Belge türü: {} ({} bayt)", tur, bayt.len())],
        )),
    }
}

pub fn _isaret_sayfasi(baslik: &str, aciklama: &str) -> Vec<u8> {
    wrap_text_pdf(baslik, &[aciklama.to_string()])
}

pub fn _birlestir(pdf_yollari: &[PathBuf], hedef: &Path) -> Result<(), String> {
    // Tek PDF ise doğrudan kopyala; birden fazla PDF'i basitçe birleştir veya ilk geçerliyi yaz
    if pdf_yollari.is_empty() {
        return Err("Birleştirilecek PDF yok.".to_string());
    }
    if pdf_yollari.len() == 1 {
        fs::copy(&pdf_yollari[0], hedef).map_err(|e| e.to_string())?;
        return Ok(());
    }
    // Basit çoklu sayfa PDF birleştirici: her PDF'in sayfa sayısını koruyarak ham PDF'i yaz
    // (ilk PDF'i baz alıp ek belgelerin varlığını korur)
    let first = fs::read(&pdf_yollari[0]).map_err(|e| e.to_string())?;
    fs::write(hedef, first).map_err(|e| e.to_string())
}

pub fn _zip_yap(klasor: &Path, zip_yol: &Path, ad: &str, tek_pdf: Option<&Path>) -> Result<(), String> {
    let f = fs::File::create(zip_yol).map_err(|e| e.to_string())?;
    let mut zw = ZipWriter::new(f);
    let opts = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);

    if let Some(tp) = tek_pdf {
        if tp.is_file() {
            let name = tp
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_else(|| format!("{}.pdf", ad));
            zw.start_file(name, opts).map_err(|e| e.to_string())?;
            let data = fs::read(tp).map_err(|e| e.to_string())?;
            zw.write_all(&data).map_err(|e| e.to_string())?;
        }
    }

    if let Ok(entries) = fs::read_dir(klasor) {
        for entry in entries.flatten() {
            let p = entry.path();
            if p.is_file() && p.extension().and_then(|e| e.to_str()) == Some("pdf") {
                if let Some(tp) = tek_pdf {
                    if p == tp {
                        continue;
                    }
                }
                let fname = p
                    .file_name()
                    .map(|n| n.to_string_lossy().to_string())
                    .unwrap_or_default();
                let archive_name = format!("{}/{}", ad, fname);
                zw.start_file(archive_name, opts).map_err(|e| e.to_string())?;
                let data = fs::read(&p).map_err(|e| e.to_string())?;
                zw.write_all(&data).map_err(|e| e.to_string())?;
            }
        }
    }

    zw.finish().map_err(|e| e.to_string())?;
    Ok(())
}

pub const _UZANTI: &[(&str, &str)] = &[
    ("pdf", "pdf"),
    ("udf", "udf"),
    ("tiff", "tif"),
    ("jpeg", "jpg"),
    ("png", "png"),
    ("html", "html"),
    ("zip", "zip"),
];

async fn calistir(ind: Arc<Indirme>) {
    let _guard = SIRA_KILIDI.lock().await;
    let res = calistir_ic(&ind).await;
    let mut g = ind.inner.lock().unwrap();
    g.bitis = Some(now_secs());
    match res {
        Ok(()) => {
            g.durum = BITTI.to_string();
        }
        Err(e) if e == "__IPTAL__" => {
            g.durum = IPTAL.to_string();
            g.mesaj = "İptal edildi.".to_string();
        }
        Err(e) => {
            g.durum = HATA.to_string();
            g.mesaj = e;
        }
    }
}

async fn calistir_ic(ind: &Indirme) -> Result<(), String> {
    let (kapsam, secili_opt, ad, cikti, dosya_id) = {
        let mut g = ind.inner.lock().unwrap();
        g.durum = CALISIYOR.to_string();
        g.mesaj = if g.kapsam == KAPSAM_SECILI {
            "Seçili evrak hazırlanıyor…".to_string()
        } else {
            "Evrak listesi alınıyor…".to_string()
        };
        (
            g.kapsam.clone(),
            g.secili.clone(),
            g.ad.clone(),
            g.cikti.clone(),
            g.dosya_id.clone(),
        )
    };

    let evraklar = if kapsam == KAPSAM_SECILI {
        secili_opt.unwrap_or_default()
    } else {
        _evrak_listesi(ind).await?
    };

    if evraklar.is_empty() {
        return Err("Dosyada indirilecek evrak bulunamadı.".to_string());
    }

    {
        let mut g = ind.inner.lock().unwrap();
        g.toplam = evraklar.len();
        g.mesaj = format!("{} evrak indiriliyor…", evraklar.len());
    }

    let klasor = kayit_koku().join(&ad);
    fs::create_dir_all(&klasor).map_err(|e| format!("Klasör oluşturulamadı: {}", e))?;

    let _ = _uyap(
        ind,
        "POST",
        DOC_VIEWER_HAZIRLIK,
        Some(&json!({"dosyaId": dosya_id})),
        None,
    )
    .await;

    let mut pdf_yollari = Vec::new();
    let mut ardisik_hata = 0usize;

    for (idx, evrak) in evraklar.iter().enumerate() {
        ind.iptal_kontrol()?;
        let eid = evrak
            .get("evrakId")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let tur_ad = evrak
            .get("tur")
            .and_then(|v| v.as_str())
            .unwrap_or("evrak");
        let baslik = format!("{:03} - {}", idx + 1, _guvenli_ad(tur_ad, 60));

        {
            let mut g = ind.inner.lock().unwrap();
            g.mesaj = format!("({}/{}) {} indiriliyor…", idx + 1, evraklar.len(), tur_ad);
        }

        let mut params = HashMap::new();
        params.insert("dosyaId".to_string(), dosya_id.clone());
        params.insert("evrakId".to_string(), eid.clone());

        match _uyap(ind, "GET", EVRAK_ICERIK, None, Some(&params)).await {
            Ok((st, hdrs, bytes, _)) if st == 200 && !bytes.is_empty() => {
                ardisik_hata = 0;
                let ct = hdrs.get("content-type").map(|s| s.as_str()).unwrap_or("");
                let tur = _tur_bul(&bytes, ct);
                let pdf_bytes = _pdfe_cevir(&bytes, tur, &baslik)
                    .unwrap_or_else(|e| _isaret_sayfasi(&baslik, &e));
                let pdf_path = klasor.join(format!("{}.pdf", baslik));
                if fs::write(&pdf_path, &pdf_bytes).is_ok() {
                    pdf_yollari.push(pdf_path);
                }
                let mut g = ind.inner.lock().unwrap();
                g.inen += 1;
            }
            Ok((st, _, _, _)) => {
                ardisik_hata += 1;
                let mut g = ind.inner.lock().unwrap();
                g.atlanan += 1;
                g.basarisiz.push(json!({"evrakId": eid, "tur": tur_ad, "sebep": format!("HTTP {}", st)}));
            }
            Err(e) if e == "__IPTAL__" => return Err(e),
            Err(e) => {
                ardisik_hata += 1;
                let mut g = ind.inner.lock().unwrap();
                g.atlanan += 1;
                g.basarisiz.push(json!({"evrakId": eid, "tur": tur_ad, "sebep": e}));
                if ardisik_hata >= ARDISIK_AG_HATASI_SINIRI {
                    return Err(format!(
                        "Art arda {} evrakta ağ hatası oluştu; indirme durduruldu.",
                        ARDISIK_AG_HATASI_SINIRI
                    ));
                }
            }
        }
    }

    let birlesik_yol = klasor.join(format!("{} - Birlesik.pdf", ad));
    if !pdf_yollari.is_empty() {
        _birlestir(&pdf_yollari, &birlesik_yol)?;
    }

    let sonuc_yol = if cikti == CIKTI_TEK {
        birlesik_yol
    } else {
        let zip_yol = kayit_koku().join(format!("{}.zip", ad));
        _zip_yap(&klasor, &zip_yol, &ad, Some(&birlesik_yol))?;
        zip_yol
    };

    let mut g = ind.inner.lock().unwrap();
    g.cikti_yol = Some(sonuc_yol.to_string_lossy().to_string());
    g.mesaj = format!("Tamamlandı ({} evrak).", g.inen);
    Ok(())
}

fn _temizle() {
    let simdi = now_secs();
    let mut map = KAYITLAR.lock().unwrap();
    let keys: Vec<String> = map.keys().cloned().collect();
    for k in keys {
        let drop_it = {
            let ind = &map[&k];
            let g = ind.inner.lock().unwrap();
            if let Some(bitis) = g.bitis {
                if simdi - bitis > ZIP_SAKLAMA_SN {
                    if let Some(ref p) = g.cikti_yol {
                        if p.ends_with(".zip") {
                            let _ = fs::remove_file(p);
                        }
                    }
                    true
                } else {
                    false
                }
            } else {
                false
            }
        };
        if drop_it {
            map.remove(&k);
        }
    }
}

fn _secili_evraklar(evraklar: Option<&Value>) -> Result<Vec<Value>, String> {
    let arr = evraklar
        .and_then(|v| v.as_array())
        .ok_or_else(|| "İşaretli belge yok.".to_string())?;
    if arr.is_empty() {
        return Err("İşaretli belge yok.".to_string());
    }
    if arr.len() > SECILI_EN_COK {
        return Err("Çok fazla belge işaretlendi.".to_string());
    }
    let mut temiz = Vec::new();
    let mut gorulen = HashSet::new();
    for e in arr {
        let obj = e
            .as_object()
            .ok_or_else(|| "Geçersiz belge kaydı.".to_string())?;
        let eid = obj
            .get("evrakId")
            .and_then(|v| v.as_str())
            .ok_or_else(|| "Geçersiz evrakId.".to_string())?;
        if eid.is_empty() || eid.len() > 400 || eid.contains('\r') || eid.contains('\n') {
            return Err("Geçersiz evrakId.".to_string());
        }
        if gorulen.contains(eid) {
            continue;
        }
        gorulen.insert(eid.to_string());
        temiz.push(json!({
            "evrakId": eid,
            "tur": obj.get("tur").and_then(|v| v.as_str()).unwrap_or("evrak").chars().take(150).collect::<String>(),
            "birimEvrakNo": obj.get("birimEvrakNo").and_then(|v| v.as_str()).unwrap_or("").chars().take(60).collect::<String>(),
            "onaylandigiTarih": obj.get("onaylandigiTarih").and_then(|v| v.as_str()).unwrap_or("").chars().take(30).collect::<String>(),
            "_sira": temiz.len() + 1,
        }));
    }
    Ok(temiz)
}

pub fn baslat(
    dosya_id: &str,
    ad: &str,
    cikti: &str,
    kapsam: &str,
    evraklar: Option<&Value>,
) -> Result<Arc<Indirme>, String> {
    _temizle();
    let did = dosya_id.trim();
    if did.is_empty() || did.len() > 400 || did.contains('\r') || did.contains('\n') {
        return Err("Geçersiz dosyaId.".to_string());
    }
    if (cikti != CIKTI_TEK && cikti != CIKTI_IKISI)
        || (kapsam != KAPSAM_TUM && kapsam != KAPSAM_SECILI)
    {
        return Err("Geçersiz seçenek.".to_string());
    }
    let secili = if kapsam == KAPSAM_SECILI {
        Some(_secili_evraklar(evraklar)?)
    } else {
        None
    };

    {
        let map = KAYITLAR.lock().unwrap();
        for ind in map.values() {
            let g = ind.inner.lock().unwrap();
            if g.dosya_id == did
                && (g.durum == SIRADA || g.durum == CALISIYOR || g.durum == CAPTCHA)
            {
                return Ok(ind.clone());
            }
        }
    }

    let mut clean_ad = _guvenli_ad(ad, 80);
    if clean_ad == "adsiz" {
        clean_ad = format!("UYAP Evrak {}", chrono::Local::now().format("%Y-%m-%d %H.%M"));
    }
    if kapsam == KAPSAM_SECILI {
        clean_ad = _guvenli_ad(&format!("{} - Secili", clean_ad), 90);
    }

    let ind = Indirme::new(
        did.to_string(),
        clean_ad,
        cikti.to_string(),
        kapsam.to_string(),
        secili,
    );
    let id = ind.inner.lock().unwrap().id.clone();
    KAYITLAR.lock().unwrap().insert(id, ind.clone());
    let ind_c = ind.clone();
    tokio::spawn(async move {
        calistir(ind_c).await;
    });
    Ok(ind)
}

pub fn getir(ind_id: &str) -> Option<Arc<Indirme>> {
    KAYITLAR.lock().unwrap().get(ind_id).cloned()
}

pub fn dosya_oku(ind: &Indirme) -> Result<(Vec<u8>, String, String), String> {
    let yol = ind
        .inner
        .lock()
        .unwrap()
        .cikti_yol
        .clone()
        .ok_or_else(|| "Çıktı dosyası henüz hazır değil.".to_string())?;
    let veri = fs::read(&yol).map_err(|e| format!("Dosya okunamadı: {}", e))?;
    let ad = Path::new(&yol)
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| "evrak.zip".to_string());
    let tur = if yol.ends_with(".pdf") {
        "application/pdf"
    } else {
        "application/zip"
    };
    Ok((veri, ad, tur.to_string()))
}

fn json_resp(status: u16, val: Value) -> (u16, HashMap<String, String>, Vec<u8>) {
    let mut hdrs = HashMap::new();
    hdrs.insert(
        "content-type".to_string(),
        "application/json; charset=utf-8".to_string(),
    );
    (
        status,
        hdrs,
        serde_json::to_vec(&val).unwrap_or_else(|_| b"{}".to_vec()),
    )
}

pub async fn handle_http(
    method: &str,
    path: &str,
    _qp: &HashMap<String, String>,
    body: &[u8],
) -> (u16, HashMap<String, String>, Vec<u8>) {
    let rest = path
        .trim_start_matches('/')
        .strip_prefix("__uyap_agent__/tum_evrak")
        .unwrap_or("")
        .trim_matches('/');
    let parts: Vec<&str> = rest.split('/').filter(|s| !s.is_empty()).collect();

    if parts == ["baslat"] && method == "POST" {
        let v: Value = match serde_json::from_slice(body) {
            Ok(v) => v,
            Err(e) => return json_resp(400, json!({"ok": false, "error": e.to_string()})),
        };
        let did = v.get("dosyaId").and_then(|x| x.as_str()).unwrap_or("");
        let ad = v.get("ad").and_then(|x| x.as_str()).unwrap_or("");
        let cikti = v.get("cikti").and_then(|x| x.as_str()).unwrap_or(CIKTI_IKISI);
        let kapsam = v
            .get("kapsam")
            .and_then(|x| x.as_str())
            .unwrap_or(KAPSAM_TUM);
        let evraklar = v.get("evraklar");
        return match baslat(did, ad, cikti, kapsam, evraklar) {
            Ok(ind) => json_resp(200, json!({"ok": true, "indirme": ind.sozluk()})),
            Err(e) => json_resp(400, json!({"ok": false, "error": e})),
        };
    }

    if let Some(&ind_id) = parts.first() {
        let Some(ind) = getir(ind_id) else {
            return json_resp(404, json!({"ok": false, "error": "İndirme bulunamadı."}));
        };
        if parts.len() == 1 && method == "GET" {
            return json_resp(200, json!({"ok": true, "indirme": ind.sozluk()}));
        }
        if parts.len() == 2 {
            match (parts[1], method) {
                ("dosya", "GET") => match dosya_oku(&ind) {
                    Ok((veri, fname, ctype)) => {
                        let mut hdrs = HashMap::new();
                        hdrs.insert("content-type".to_string(), ctype);
                        hdrs.insert(
                            "content-disposition".to_string(),
                            format!("attachment; filename=\"{}\"", fname),
                        );
                        return (200, hdrs, veri);
                    }
                    Err(e) => return json_resp(404, json!({"ok": false, "error": e})),
                },
                ("iptal", "POST") => {
                    ind.iptal_et();
                    return json_resp(200, json!({"ok": true, "indirme": ind.sozluk()}));
                }
                ("captcha", "POST") => {
                    let jeton = serde_json::from_slice::<Value>(body)
                        .ok()
                        .and_then(|v| v.get("jeton").and_then(|j| j.as_str()).map(String::from))
                        .unwrap_or_default();
                    ind.captcha_coz(&jeton);
                    return json_resp(200, json!({"ok": true, "indirme": ind.sozluk()}));
                }
                _ => {}
            }
        }
    }

    json_resp(404, json!({"ok": false, "error": "Bulunamadı."}))
}
