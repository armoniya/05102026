//! Gerekçeli Kararlar (`uyap_core/gerekceli_karar.py` 1:1 portu)
//! ===================================================================
//! Hukuk dosyalarında gerekçeli kararı bulup UDF + PDF + Excel listesi indirir.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex as StdMutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use once_cell::sync::Lazy;
use rand::RngCore;
use serde_json::{json, Value};
use tokio::sync::{Mutex as AsyncMutex, Notify};
use zip::write::SimpleFileOptions;
use zip::{CompressionMethod, ZipWriter};

use crate::evrak_toplu::{
    _guvenli_ad, _pdfe_cevir, _tur_bul, kayit_koku, BITTI, CALISIYOR, CAPTCHA, DOC_VIEWER_HAZIRLIK,
    EVRAK_ICERIK, EVRAK_LISTESI, EVRAK_LISTESI_TOPLAM, HATA, IPTAL, SIRADA, TURNSTILE_SITE_KEY,
};
use crate::runtime;
use crate::uyap_proxy::{self, _looks_like_login_wall, UYAP_BASE};

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
pub const TARAF_BILGILERI: &str = "dosya_taraf_bilgileri_brd.ajx";
pub const SAYFA_BOYU: usize = 500;
pub const SAYFA_EN_COK: usize = 40;

pub fn kok_klasor() -> PathBuf {
    kayit_koku().join("Gerekçeli Kararlar")
}

pub const SECIM: &str = "secim";
pub const KARAR_SECIM: &str = "karar_secim";
pub const _DISLA: &[&str] = &["tebliğ", "teblig", "mazbata", "zarf", "şerh", "serh"];

pub const EXCEL_BASLIKLARI: &[&str] = &[
    "Birim",
    "Dosya No",
    "Dosya Türü",
    "Durum",
    "Açılış Tarihi",
    "Taraflar",
    "Gerekçeli Karar",
    "Karar Tarihi",
    "Dosya Klasörü",
];

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

pub fn gerekceli_mi(tur: &str) -> bool {
    let t = tr_kucuk(tur);
    (t.contains("gerekçeli karar") || t.contains("gerekceli karar"))
        && !_DISLA.iter().any(|d| t.contains(d))
}

fn kayitlar_listesi(veri: &Value) -> Vec<Value> {
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

#[derive(Debug, Clone)]
pub struct TaramaInner {
    pub id: String,
    pub durumlar: Vec<i64>,
    pub durum: String,
    pub asama: String,
    pub mesaj: String,
    pub i: usize,
    pub toplam: usize,
    pub dosyalar: HashMap<String, Value>,
    pub sonuclar: HashMap<String, Value>,
    pub captcha: Option<Value>,
    pub captcha_jeton: Option<String>,
    pub captcha_sayisi: usize,
    pub cikti_yol: Option<String>,
    pub excel_yol: Option<String>,
    pub olusturma: f64,
    pub bitis: Option<f64>,
    pub iptal: bool,
    pub yavaslatma: f64,
}

pub struct Tarama {
    pub inner: StdMutex<TaramaInner>,
    pub captcha_notify: Notify,
}

impl Tarama {
    pub fn new(durumlar: Vec<i64>) -> Arc<Self> {
        Arc::new(Self {
            inner: StdMutex::new(TaramaInner {
                id: new_id(),
                durumlar,
                durum: SIRADA.to_string(),
                asama: "liste".to_string(),
                mesaj: "Liste sırada…".to_string(),
                i: 0,
                toplam: 0,
                dosyalar: HashMap::new(),
                sonuclar: HashMap::new(),
                captcha: None,
                captcha_jeton: None,
                captcha_sayisi: 0,
                cikti_yol: None,
                excel_yol: None,
                olusturma: now_secs(),
                bitis: None,
                iptal: false,
                yavaslatma: 1.0,
            }),
            captcha_notify: Notify::new(),
        })
    }

    pub fn sozluk(&self) -> Value {
        let g = self.inner.lock().unwrap();
        let dosya_list: Vec<&Value> = g.dosyalar.values().collect();
        let sonuc_list: Vec<&Value> = g.sonuclar.values().collect();
        json!({
            "id": g.id,
            "durumlar": g.durumlar,
            "durum": g.durum,
            "asama": g.asama,
            "mesaj": g.mesaj,
            "i": g.i,
            "toplam": g.toplam,
            "dosya_sayisi": g.dosyalar.len(),
            "dosyalar": dosya_list,
            "sonuclar": sonuc_list,
            "captcha": g.captcha,
            "zip_hazir": g.cikti_yol.as_ref().map(|p| Path::new(p).exists()).unwrap_or(false),
            "excel_hazir": g.excel_yol.as_ref().map(|p| Path::new(p).exists()).unwrap_or(false),
        })
    }

    pub fn iptal_et(&self) {
        {
            let mut g = self.inner.lock().unwrap();
            g.iptal = true;
            if g.durum == SIRADA || g.durum == CAPTCHA || g.durum == SECIM || g.durum == KARAR_SECIM
            {
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

    pub fn iptal_kontrol(&self) -> Result<(), String> {
        if self.inner.lock().unwrap().iptal {
            Err("__IPTAL__".to_string())
        } else {
            Ok(())
        }
    }
}

static KAYITLAR: Lazy<StdMutex<HashMap<String, Arc<Tarama>>>> =
    Lazy::new(|| StdMutex::new(HashMap::new()));
static SIRA_KILIDI: Lazy<AsyncMutex<()>> = Lazy::new(|| AsyncMutex::new(()));

async fn uyap_istek(
    t: &Tarama,
    method: &str,
    path: &str,
    govde: Option<&Value>,
    params: Option<&HashMap<String, String>>,
) -> Result<(u16, HashMap<String, String>, Vec<u8>, Option<Value>), String> {
    t.iptal_kontrol()?;
    tokio::time::sleep(Duration::from_secs_f64(ISTEK_ARASI_SN)).await;
    t.iptal_kontrol()?;

    let gw = uyap_proxy::get_gw().ok_or_else(|| "UYAP oturumu hazır değil.".to_string())?;
    let url = format!("{}/{}", UYAP_BASE, path.trim_start_matches('/'));

    let _izi = runtime::uyap_istek_izi().await;
    let _batch = runtime::batch_write().await;

    let mut login_retried = false;
    loop {
        gw.ensure_alive().await?;
        let req_m =
            reqwest::Method::from_bytes(method.as_bytes()).unwrap_or(reqwest::Method::GET);
        let mut rb = gw
            .client
            .request(req_m, &url)
            .header("Referer", format!("{}/dosya-sorgulama", UYAP_BASE))
            .header("Accept", "application/json, text/plain, */*");
        if let Some(pm) = params {
            rb = rb.query(pm);
        }
        if let Some(gm) = govde {
            rb = rb.json(gm);
        }

        let resp = rb
            .send()
            .await
            .map_err(|e| format!("UYAP isteği hatası ({}): {}", path, e))?;
        let st = resp.status().as_u16();
        let raw_h = resp.headers().clone();
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
        let bytes = resp
            .bytes()
            .await
            .map_err(|e| format!("UYAP yanıtı okunamadı: {}", e))?
            .to_vec();
        gw.touch();
        let jv = serde_json::from_slice::<Value>(&bytes).ok();
        return Ok((st, hdrs, bytes, jv));
    }
}

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

async fn listele(t: &Tarama) -> Result<(), String> {
    let durumlar = {
        let mut g = t.inner.lock().unwrap();
        g.durum = CALISIYOR.to_string();
        g.asama = "liste".to_string();
        g.mesaj = "Mahkeme birimleri alınıyor…".to_string();
        g.durumlar.clone()
    };

    let (_, _, _, birimler_opt) = uyap_istek(
        t,
        "POST",
        BIRIM_TURLERI,
        Some(&json!({"yargiTuru": HUKUK})),
        None,
    )
    .await?;

    let birim_list: Vec<Value> = birimler_opt
        .as_ref()
        .and_then(|v| v.as_array().cloned())
        .or_else(|| {
            birimler_opt
                .as_ref()
                .and_then(|v| v.get("data").and_then(|d| d.as_array()).cloned())
        })
        .unwrap_or_else(|| vec![json!({"birimTuru2": "", "birimTuru3": HUKUK})]);

    let mut dosyalar = HashMap::new();
    for &durum_kod in &durumlar {
        let durum_ad = DURUMLAR
            .iter()
            .find(|(k, _)| *k == durum_kod)
            .map(|(_, a)| *a)
            .unwrap_or("Açık");

        for birim in &birim_list {
            t.iptal_kontrol()?;
            let bt2 = birim
                .get("birimTuru2")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let bt3 = birim
                .get("birimTuru3")
                .and_then(|v| v.as_str())
                .unwrap_or(HUKUK);

            for sayfa in 1..=SAYFA_EN_COK {
                t.iptal_kontrol()?;
                let payload = json!({
                    "dosyaDurumKod": durum_kod,
                    "birimTuru2": bt2,
                    "birimTuru3": bt3,
                    "pageSize": SAYFA_BOYU,
                    "pageNumber": sayfa,
                });
                let (_, _, _, v_opt) = uyap_istek(t, "POST", ARAMA, Some(&payload), None).await?;
                let Some(v) = v_opt else { break };
                let recs = kayitlar_listesi(&v);
                if recs.is_empty() {
                    break;
                }
                let rec_len = recs.len();
                for mut r in recs {
                    let did = r
                        .get("dosyaId")
                        .and_then(|x| x.as_str())
                        .unwrap_or("")
                        .to_string();
                    if did.is_empty() {
                        continue;
                    }
                    if let Some(obj) = r.as_object_mut() {
                        obj.entry("durumAd")
                            .or_insert_with(|| Value::String(durum_ad.to_string()));
                    }
                    dosyalar.insert(did, r);
                }
                if rec_len < SAYFA_BOYU {
                    break;
                }
            }
        }
    }

    let mut g = t.inner.lock().unwrap();
    g.dosyalar = dosyalar;
    g.durum = SECIM.to_string();
    g.mesaj = format!("{} dosya listelendi. Taranacakları seçin.", g.dosyalar.len());
    Ok(())
}

async fn kontrol(t: &Tarama, secilen: Vec<Value>) -> Result<(), String> {
    {
        let mut g = t.inner.lock().unwrap();
        g.durum = CALISIYOR.to_string();
        g.asama = "kontrol".to_string();
        g.toplam = secilen.len();
        g.i = 0;
        g.sonuclar.clear();
    }

    for (idx, dosya) in secilen.iter().enumerate() {
        t.iptal_kontrol()?;
        if idx > 0 && idx % MOLA_HER_DOSYA == 0 {
            {
                let mut g = t.inner.lock().unwrap();
                g.mesaj = format!("Mola veriliyor ({} sn)…", MOLA_SN);
            }
            tokio::time::sleep(Duration::from_secs(MOLA_SN)).await;
        }

        let did = dosya
            .get("dosyaId")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let dno = dosya
            .get("dosyaNo")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let birim = dosya
            .get("birimAdi")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        {
            let mut g = t.inner.lock().unwrap();
            g.i = idx + 1;
            g.mesaj = format!("({}/{}) {} {} kontrol ediliyor…", idx + 1, secilen.len(), birim, dno);
        }

        let _ = uyap_istek(
            t,
            "POST",
            EVRAK_LISTESI_TOPLAM,
            Some(&json!({"dosyaId": did})),
            None,
        )
        .await;
        let (_, _, _, evrak_v) = uyap_istek(
            t,
            "POST",
            EVRAK_LISTESI,
            Some(&json!({"dosyaId": did, "pageNumber": 1})),
            None,
        )
        .await?;

        let evraklar = evrak_v
            .as_ref()
            .map(kayitlar_listesi)
            .unwrap_or_default();
        let kararlar: Vec<Value> = evraklar
            .into_iter()
            .filter(|e| {
                let tur = e.get("tur").and_then(|v| v.as_str()).unwrap_or("");
                gerekceli_mi(tur)
            })
            .collect();

        let (_, _, _, taraf_v) = uyap_istek(
            t,
            "POST",
            TARAF_BILGILERI,
            Some(&json!({"dosyaId": did})),
            None,
        )
        .await?;
        let taraflar_str = taraf_v
            .as_ref()
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|x| {
                        let adi = x.get("adi").and_then(|a| a.as_str()).unwrap_or("");
                        let rol = x.get("rol").and_then(|r| r.as_str()).unwrap_or("");
                        if adi.is_empty() {
                            None
                        } else if rol.is_empty() {
                            Some(adi.to_string())
                        } else {
                            Some(format!("{} ({})", adi, rol))
                        }
                    })
                    .collect::<Vec<_>>()
                    .join("; ")
            })
            .unwrap_or_default();

        let mut g = t.inner.lock().unwrap();
        g.sonuclar.insert(
            did.clone(),
            json!({
                "dosya": dosya,
                "kararlar": kararlar,
                "taraflar": taraflar_str,
            }),
        );
    }

    let mut g = t.inner.lock().unwrap();
    g.durum = KARAR_SECIM.to_string();
    g.mesaj = "Kontrol tamamlandı. İndirilecek kararları seçin.".to_string();
    Ok(())
}

async fn indir(t: &Tarama, secilen: Vec<String>) -> Result<(), String> {
    {
        let mut g = t.inner.lock().unwrap();
        g.durum = CALISIYOR.to_string();
        g.asama = "indirme".to_string();
    }

    let root = kok_klasor();
    fs::create_dir_all(&root).map_err(|e| e.to_string())?;

    let mut excel_satirlari = vec![EXCEL_BASLIKLARI.iter().map(|s| (*s).to_string()).collect()];
    let mut indirilen_klasorler = Vec::new();

    for did in &secilen {
        t.iptal_kontrol()?;
        let (dosya, kararlar, taraflar_str) = {
            let g = t.inner.lock().unwrap();
            let s = g
                .sonuclar
                .get(did)
                .cloned()
                .ok_or_else(|| "Sonuç kaydı bulunamadı.".to_string())?;
            (
                s.get("dosya").cloned().unwrap_or_else(|| json!({})),
                s.get("kararlar")
                    .and_then(|v| v.as_array())
                    .cloned()
                    .unwrap_or_default(),
                s.get("taraflar")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
            )
        };

        let birim = dosya.get("birimAdi").and_then(|v| v.as_str()).unwrap_or("");
        let dno = dosya.get("dosyaNo").and_then(|v| v.as_str()).unwrap_or("");
        let dtur = dosya.get("dosyaTuru").and_then(|v| v.as_str()).unwrap_or("");
        let durum_ad = dosya.get("durumAd").and_then(|v| v.as_str()).unwrap_or("");
        let acilis = dosya
            .get("acilisTarihi")
            .and_then(|v| v.as_str())
            .unwrap_or("");

        let klasor_adi = _guvenli_ad(&format!("{} {}", birim, dno), 80);
        let d_klasor = root.join(&klasor_adi);
        fs::create_dir_all(&d_klasor).map_err(|e| e.to_string())?;
        indirilen_klasorler.push((klasor_adi.clone(), d_klasor.clone()));

        let _ = uyap_istek(
            t,
            "POST",
            DOC_VIEWER_HAZIRLIK,
            Some(&json!({"dosyaId": did})),
            None,
        )
        .await;

        let mut karar_adlari = Vec::new();
        let mut karar_tarihleri = Vec::new();

        for (kidx, k) in kararlar.iter().enumerate() {
            t.iptal_kontrol()?;
            let eid = k.get("evrakId").and_then(|v| v.as_str()).unwrap_or("");
            let ktur = k
                .get("tur")
                .and_then(|v| v.as_str())
                .unwrap_or("Gerekceli Karar");
            let ktarih = k
                .get("onaylandigiTarih")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            karar_adlari.push(ktur.to_string());
            if !ktarih.is_empty() {
                karar_tarihleri.push(ktarih.to_string());
            }

            {
                let mut g = t.inner.lock().unwrap();
                g.i += 1;
                g.mesaj = format!("({}/{}) {} {} indiriliyor…", g.i, g.toplam, dno, ktur);
            }

            let mut params = HashMap::new();
            params.insert("dosyaId".to_string(), did.clone());
            params.insert("evrakId".to_string(), eid.to_string());
            if let Ok((st, hdrs, bytes, _)) =
                uyap_istek(t, "GET", EVRAK_ICERIK, None, Some(&params)).await
            {
                if st == 200 && !bytes.is_empty() {
                    let ct = hdrs.get("content-type").map(|s| s.as_str()).unwrap_or("");
                    let tur = _tur_bul(&bytes, ct);
                    let taban = _guvenli_ad(&format!("{:02} - {}", kidx + 1, ktur), 60);
                    let ext = match tur {
                        "udf" => "udf",
                        "pdf" => "pdf",
                        "html" => "html",
                        _ => "bin",
                    };
                    let _ = fs::write(d_klasor.join(format!("{}.{}", taban, ext)), &bytes);
                    if let Ok(pdf_bytes) = _pdfe_cevir(&bytes, tur, &taban) {
                        let _ = fs::write(d_klasor.join(format!("{}.pdf", taban)), &pdf_bytes);
                    }
                }
            }
        }

        excel_satirlari.push(vec![
            birim.to_string(),
            dno.to_string(),
            dtur.to_string(),
            durum_ad.to_string(),
            acilis.to_string(),
            taraflar_str,
            karar_adlari.join(", "),
            karar_tarihleri.join(", "),
            klasor_adi,
        ]);
    }

    let excel_yol = root.join(format!("Gerekceli_Kararlar_{}.xlsx", t.inner.lock().unwrap().id));
    _xlsx(&excel_satirlari, &excel_yol)?;

    let zip_yol = root.join(format!("Gerekceli_Kararlar_{}.zip", t.inner.lock().unwrap().id));
    {
        let f = fs::File::create(&zip_yol).map_err(|e| e.to_string())?;
        let mut zw = ZipWriter::new(f);
        let opts = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);

        if let Ok(data) = fs::read(&excel_yol) {
            zw.start_file("Gerekceli_Kararlar.xlsx", opts)
                .map_err(|e| e.to_string())?;
            zw.write_all(&data).map_err(|e| e.to_string())?;
        }
        for (k_ad, k_yol) in indirilen_klasorler {
            if let Ok(entries) = fs::read_dir(&k_yol) {
                for entry in entries.flatten() {
                    let p = entry.path();
                    if p.is_file() {
                        if let Ok(data) = fs::read(&p) {
                            let fname = p
                                .file_name()
                                .map(|n| n.to_string_lossy().to_string())
                                .unwrap_or_default();
                            zw.start_file(format!("{}/{}", k_ad, fname), opts)
                                .map_err(|e| e.to_string())?;
                            zw.write_all(&data).map_err(|e| e.to_string())?;
                        }
                    }
                }
            }
        }
        zw.finish().map_err(|e| e.to_string())?;
    }

    let mut g = t.inner.lock().unwrap();
    g.excel_yol = Some(excel_yol.to_string_lossy().to_string());
    g.cikti_yol = Some(zip_yol.to_string_lossy().to_string());
    g.durum = BITTI.to_string();
    g.bitis = Some(now_secs());
    g.mesaj = "Tamamlandı.".to_string();
    Ok(())
}

fn _temizle() {
    let simdi = now_secs();
    let mut map = KAYITLAR.lock().unwrap();
    let keys: Vec<String> = map.keys().cloned().collect();
    for k in keys {
        let drop_it = {
            let t = &map[&k];
            let g = t.inner.lock().unwrap();
            if let Some(bitis) = g.bitis {
                if simdi - bitis > ZIP_SAKLAMA_SN {
                    if let Some(ref p) = g.cikti_yol {
                        let _ = fs::remove_file(p);
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

pub fn aktif() -> Option<Arc<Tarama>> {
    _temizle();
    let map = KAYITLAR.lock().unwrap();
    map.values()
        .max_by(|a, b| {
            let ta = a.inner.lock().unwrap().olusturma;
            let tb = b.inner.lock().unwrap().olusturma;
            ta.partial_cmp(&tb).unwrap_or(std::cmp::Ordering::Equal)
        })
        .cloned()
}

pub fn liste_baslat(durumlar_opt: Option<Vec<i64>>) -> Result<Arc<Tarama>, String> {
    _temizle();
    let durumlar = durumlar_opt.unwrap_or_else(|| DURUMLAR.iter().map(|(k, _)| *k).collect());
    if durumlar.is_empty()
        || durumlar
            .iter()
            .any(|d| !DURUMLAR.iter().any(|(k, _)| k == d))
    {
        return Err("Geçersiz dosya durumu seçimi.".to_string());
    }
    {
        let map = KAYITLAR.lock().unwrap();
        for t in map.values() {
            let d = &t.inner.lock().unwrap().durum;
            if matches!(
                d.as_str(),
                SIRADA | CALISIYOR | CAPTCHA | SECIM | KARAR_SECIM
            ) {
                return Ok(t.clone());
            }
        }
    }
    let mut uniq = durumlar;
    uniq.sort();
    uniq.dedup();
    let t = Tarama::new(uniq);
    let id = t.inner.lock().unwrap().id.clone();
    KAYITLAR.lock().unwrap().insert(id, t.clone());
    let tc = t.clone();
    tokio::spawn(async move {
        let _g = SIRA_KILIDI.lock().await;
        if let Err(e) = listele(&tc).await {
            let mut g = tc.inner.lock().unwrap();
            g.bitis = Some(now_secs());
            if e == "__IPTAL__" {
                g.durum = IPTAL.to_string();
                g.mesaj = "İptal edildi.".to_string();
            } else {
                g.durum = HATA.to_string();
                g.mesaj = e;
            }
        }
    });
    Ok(t)
}

fn secimi_dogrula(
    dosya_idleri: &[String],
    gecerli: &HashSet<String>,
    bos_olabilir: bool,
) -> Result<Vec<String>, String> {
    if dosya_idleri.is_empty() && !bos_olabilir {
        return Err("İşaretli dosya yok.".to_string());
    }
    let mut secilen = Vec::new();
    for did in dosya_idleri {
        if !gecerli.contains(did) {
            return Err("Listede olmayan dosya seçildi.".to_string());
        }
        if !secilen.contains(did) {
            secilen.push(did.clone());
        }
    }
    Ok(secilen)
}

pub fn tarama_baslat(t: Arc<Tarama>, dosya_idleri: &[String]) -> Result<Arc<Tarama>, String> {
    let secilen = {
        let mut g = t.inner.lock().unwrap();
        if g.durum != SECIM {
            return Err("Tarama zaten başladı ya da liste hazır değil.".to_string());
        }
        let gecerli: HashSet<String> = g.dosyalar.keys().cloned().collect();
        let ids = secimi_dogrula(dosya_idleri, &gecerli, false)?;
        let vals: Vec<Value> = ids.iter().filter_map(|id| g.dosyalar.get(id).cloned()).collect();
        g.durum = SIRADA.to_string();
        g.asama = "kontrol".to_string();
        g.toplam = vals.len();
        g.i = 0;
        g.mesaj = "Kontrol sırada…".to_string();
        vals
    };
    let tc = t.clone();
    tokio::spawn(async move {
        let _g = SIRA_KILIDI.lock().await;
        if let Err(e) = kontrol(&tc, secilen).await {
            let mut g = tc.inner.lock().unwrap();
            g.bitis = Some(now_secs());
            if e == "__IPTAL__" {
                g.durum = IPTAL.to_string();
                g.mesaj = "İptal edildi.".to_string();
            } else {
                g.durum = HATA.to_string();
                g.mesaj = e;
            }
        }
    });
    Ok(t)
}

pub fn indir_baslat(t: Arc<Tarama>, dosya_idleri: &[String]) -> Result<Arc<Tarama>, String> {
    let secilen = {
        let mut g = t.inner.lock().unwrap();
        if g.durum != KARAR_SECIM {
            return Err("Kontrol bitmedi ya da indirme zaten başladı.".to_string());
        }
        let karar_var: HashSet<String> = g
            .sonuclar
            .iter()
            .filter_map(|(did, s)| {
                let has = s
                    .get("kararlar")
                    .and_then(|v| v.as_array())
                    .map(|a| !a.is_empty())
                    .unwrap_or(false);
                if has { Some(did.clone()) } else { None }
            })
            .collect();
        let ids = secimi_dogrula(dosya_idleri, &karar_var, true)?;
        let toplam_karar: usize = ids
            .iter()
            .map(|did| {
                g.sonuclar
                    .get(did)
                    .and_then(|s| s.get("kararlar"))
                    .and_then(|v| v.as_array())
                    .map(|a| a.len())
                    .unwrap_or(0)
            })
            .sum();
        g.durum = SIRADA.to_string();
        g.asama = "indirme".to_string();
        g.i = 0;
        g.toplam = toplam_karar;
        g.mesaj = if ids.is_empty() {
            "Excel listesi hazırlanıyor…".to_string()
        } else {
            "İndirme sırada…".to_string()
        };
        ids
    };
    let tc = t.clone();
    tokio::spawn(async move {
        let _g = SIRA_KILIDI.lock().await;
        if let Err(e) = indir(&tc, secilen).await {
            let mut g = tc.inner.lock().unwrap();
            g.bitis = Some(now_secs());
            if e == "__IPTAL__" {
                g.durum = IPTAL.to_string();
                g.mesaj = "İptal edildi.".to_string();
            } else {
                g.durum = HATA.to_string();
                g.mesaj = e;
            }
        }
    });
    Ok(t)
}

pub fn iptal(t: &Tarama) {
    t.iptal_et();
}

pub fn getir(t_id: &str) -> Option<Arc<Tarama>> {
    KAYITLAR.lock().unwrap().get(t_id).cloned()
}

pub fn dosya_oku(t: &Tarama, excel: bool) -> Result<(Vec<u8>, String, String), String> {
    let g = t.inner.lock().unwrap();
    let yol = if excel {
        g.excel_yol.clone()
    } else {
        g.cikti_yol.clone()
    }
    .ok_or_else(|| "Dosya henüz hazır değil.".to_string())?;
    let veri = fs::read(&yol).map_err(|e| format!("Dosya okunamadı: {}", e))?;
    let ad = Path::new(&yol)
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| {
            if excel {
                "Gerekceli_Kararlar.xlsx".to_string()
            } else {
                "Gerekceli_Kararlar.zip".to_string()
            }
        });
    let ctype = if excel {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    } else {
        "application/zip"
    };
    Ok((veri, ad, ctype.to_string()))
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
        .strip_prefix("__uyap_agent__/gerekceli_karar")
        .unwrap_or("")
        .trim_matches('/');
    let parts: Vec<&str> = rest.split('/').filter(|s| !s.is_empty()).collect();

    if parts.is_empty() && method == "GET" {
        let cur = aktif().map(|t| t.sozluk());
        return json_resp(200, json!({"ok": true, "tarama": cur}));
    }

    if parts == ["liste"] && method == "POST" {
        let durumlar = serde_json::from_slice::<Value>(body)
            .ok()
            .and_then(|v| {
                v.get("durumlar").and_then(|d| d.as_array()).map(|arr| {
                    arr.iter().filter_map(|x| x.as_i64()).collect::<Vec<i64>>()
                })
            });
        return match liste_baslat(durumlar) {
            Ok(t) => json_resp(200, json!({"ok": true, "tarama": t.sozluk()})),
            Err(e) => json_resp(400, json!({"ok": false, "error": e})),
        };
    }

    if let Some(&t_id) = parts.first() {
        let Some(t) = getir(t_id) else {
            return json_resp(404, json!({"ok": false, "error": "Tarama bulunamadı."}));
        };
        if parts.len() == 1 && method == "GET" {
            return json_resp(200, json!({"ok": true, "tarama": t.sozluk()}));
        }
        if parts.len() == 2 {
            match (parts[1], method) {
                ("tara", "POST") => {
                    let ids = serde_json::from_slice::<Value>(body)
                        .ok()
                        .and_then(|v| {
                            v.get("dosyaIdleri").and_then(|a| a.as_array()).map(|arr| {
                                arr.iter()
                                    .filter_map(|x| x.as_str().map(String::from))
                                    .collect::<Vec<String>>()
                            })
                        })
                        .unwrap_or_default();
                    return match tarama_baslat(t, &ids) {
                        Ok(tr) => json_resp(200, json!({"ok": true, "tarama": tr.sozluk()})),
                        Err(e) => json_resp(400, json!({"ok": false, "error": e})),
                    };
                }
                ("indir", "POST") => {
                    let ids = serde_json::from_slice::<Value>(body)
                        .ok()
                        .and_then(|v| {
                            v.get("dosyaIdleri").and_then(|a| a.as_array()).map(|arr| {
                                arr.iter()
                                    .filter_map(|x| x.as_str().map(String::from))
                                    .collect::<Vec<String>>()
                            })
                        })
                        .unwrap_or_default();
                    return match indir_baslat(t, &ids) {
                        Ok(tr) => json_resp(200, json!({"ok": true, "tarama": tr.sozluk()})),
                        Err(e) => json_resp(400, json!({"ok": false, "error": e})),
                    };
                }
                ("iptal", "POST") => {
                    iptal(&t);
                    return json_resp(200, json!({"ok": true, "tarama": t.sozluk()}));
                }
                ("captcha", "POST") => {
                    let jeton = serde_json::from_slice::<Value>(body)
                        .ok()
                        .and_then(|v| v.get("jeton").and_then(|j| j.as_str()).map(String::from))
                        .unwrap_or_default();
                    t.captcha_coz(&jeton);
                    return json_resp(200, json!({"ok": true, "tarama": t.sozluk()}));
                }
                ("dosya", "GET") | ("excel", "GET") => {
                    let is_excel = parts[1] == "excel";
                    return match dosya_oku(&t, is_excel) {
                        Ok((veri, fname, ctype)) => {
                            let mut hdrs = HashMap::new();
                            hdrs.insert("content-type".to_string(), ctype);
                            hdrs.insert(
                                "content-disposition".to_string(),
                                format!("attachment; filename=\"{}\"", fname),
                            );
                            (200, hdrs, veri)
                        }
                        Err(e) => json_resp(404, json!({"ok": false, "error": e})),
                    };
                }
                _ => {}
            }
        }
    }

    json_resp(404, json!({"ok": false, "error": "Bulunamadı."}))
}
