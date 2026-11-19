//! UDF e-imza köprüsü (ofis tarafı)
//! --------------------------------
//! İstemci tarayıcıdan gelen bir `.udf` (ya da düz `content.xml`) belgesini, ofis makinesine
//! TAKILI e-imza KARTIYLA (PKCS#11) HEADLESS olarak imzalar ve imzalı `.udf`'i geri döndürür.

use std::fmt;
use std::io::{Cursor, Read, Write};
use std::path::Path;

use sha2::{Digest, Sha256};
use zip::write::SimpleFileOptions;
use zip::{CompressionMethod, ZipArchive, ZipWriter};

use crate::akis_pkcs11;
use crate::akis_pkcs11 as PyKCS11;
use crate::akis_pkcs11::{AttrValue, Mechanism, PyKCS11Lib, TemplateVal};
use crate::cades;
use crate::udf_converter;

pub const _PKCS11_CANDIDATES: &[&str] = &[
    "akisp11.dll",
    "etpkcs11.dll",
    "libakisp11.so",
    "akisp11_64.dll",
];

pub const CONVERTIBLE_EXTS: &[&str] = &[".docx", ".doc", ".pdf"];

#[derive(Debug, Clone)]
pub struct SignError(pub String);

impl fmt::Display for SignError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::error::Error for SignError {}

pub fn _diag(body: &[u8]) -> String {
    let n = body.len();
    let head = &body[..n.min(16)];
    let hexs: Vec<String> = head.iter().map(|b| format!("{:02x}", b)).collect();
    let hexs = hexs.join(" ");
    let mut txt = String::with_capacity(head.len());
    for &b in head {
        let ch = b as char;
        if ch == '\n' {
            txt.push_str("\\n");
        } else if ch == '\r' {
            txt.push_str("\\r");
        } else {
            txt.push(ch);
        }
    }
    let trimmed_start = {
        let mut s = body;
        while !s.is_empty() && s[0].is_ascii_whitespace() {
            s = &s[1..];
        }
        s
    };
    let lower5: String = trimmed_start
        .iter()
        .take(5)
        .map(|b| (*b as char).to_ascii_lowercase())
        .collect();
    let lower6: String = trimmed_start
        .iter()
        .take(6)
        .map(|b| (*b as char).to_ascii_lowercase())
        .collect();

    let kind = if body.starts_with(b"PK") {
        "ZIP/PK"
    } else if lower5.starts_with("<!doc") || lower6.starts_with("<html") {
        "HTML"
    } else if body.first().is_some_and(|&b| b == b'{' || b == b'[') {
        "JSON"
    } else if body.starts_with(b"%PDF") {
        "PDF"
    } else {
        "?"
    };
    format!(
        "boyut={} bayt, ilk16=[{}] ('{}'), tür≈{}",
        n, hexs, txt, kind
    )
}

pub fn _detect_pkcs11_lib() -> Option<String> {
    if let Ok(env) = std::env::var("UYAP_PKCS11_LIB") {
        if !env.is_empty() && Path::new(&env).exists() {
            return Some(env);
        }
    }
    let win = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".to_string());
    let search_dirs = [
        Path::new(&win).join("System32"),
        Path::new(&win).join("SysWOW64"),
        Path::new("/usr/lib").to_path_buf(),
        Path::new("/usr/local/lib").to_path_buf(),
        Path::new("/usr/lib/x86_64-linux-gnu").to_path_buf(),
    ];
    for d in &search_dirs {
        for name in _PKCS11_CANDIDATES {
            let p = d.join(name);
            if p.exists() {
                return Some(p.to_string_lossy().into_owned());
            }
        }
    }
    None
}

// ── Kart iş parçacığı ────────────────────────────────────────────────────────────────────────────
//
// Ölçüm (19.11.2026, AKIS kart): sürücüyü her imzada yükleyip bırakmak kartı baştan okutuyordu (oturum +
// sertifika ~0,4–0,55 sn) ve yeniden yüklemede takılabiliyordu; Python sürümü (ctypes) DLL'i süreç boyunca
// tutar. `C_CloseSession` ise her iki dilde ~1 sn (sürücü).
// Bu yüzden: bütün kart işleri TEK bir iş parçacığında, sürücü bir kez yüklenip hiç bırakılmadan yapılır
// (AKIS `C_Initialize(NULL)` ile açılır → çağrılar sıralı olmalı; tek iş parçacığı bunu garanti eder). İmza
// hazır olunca sonuç hemen döner; oturum kapatma ondan SONRA yapılır, sıradaki iş onu bekler.

/// Kart işinin sonucu: (sertifika DER, imzalı öznitelikler, ham imza, ECDSA mı).
type KartImzasi = (Vec<u8>, Vec<u8>, Vec<u8>, bool);

enum KartIsi {
    Imza { content_xml: Vec<u8>, pin: String, prefer_serial: Option<String>, cevap: std::sync::mpsc::Sender<Result<KartImzasi, SignError>> },
    KartVar { cevap: std::sync::mpsc::Sender<bool> },
}

fn kart_kuyrugu() -> &'static std::sync::Mutex<std::sync::mpsc::Sender<KartIsi>> {
    static K: std::sync::OnceLock<std::sync::Mutex<std::sync::mpsc::Sender<KartIsi>>> = std::sync::OnceLock::new();
    K.get_or_init(|| {
        let (tx, rx) = std::sync::mpsc::channel::<KartIsi>();
        std::thread::Builder::new()
            .name("e-imza-kart".into())
            .spawn(move || {
                let mut kitaplik: Option<PyKCS11Lib> = None;
                for is_ in rx {
                    match is_ {
                        KartIsi::KartVar { cevap } => {
                            let var = kitaplik_yukle(&mut kitaplik)
                                .ok()
                                .and_then(|p| p.getSlotList(true).ok())
                                .is_some_and(|s| !s.is_empty());
                            let _ = cevap.send(var);
                        }
                        KartIsi::Imza { content_xml, pin, prefer_serial, cevap } => {
                            let (sonuc, oturum) = match kitaplik_yukle(&mut kitaplik) {
                                Ok(p) => kartla_imzala(p, &content_xml, &pin, prefer_serial.as_deref()),
                                Err(e) => (Err(e), None),
                            };
                            let _ = cevap.send(sonuc);
                            // Sonuç gitti; kapatma (~1 sn) sıradaki işten önce, kullanıcıyı bekletmeden.
                            if let Some(s) = oturum {
                                s.logout();
                                s.closeSession();
                            }
                        }
                    }
                }
            })
            .expect("e-imza iş parçacığı başlatılamadı");
        std::sync::Mutex::new(tx)
    })
}

/// Sürücüyü ilk kullanımda yükler; sonra hep aynısını verir (bırakılmaz). Yükleme başarısızsa sonraki
/// işte yeniden denenir.
fn kitaplik_yukle(k: &mut Option<PyKCS11Lib>) -> Result<&PyKCS11Lib, SignError> {
    if k.is_none() {
        let lib = _detect_pkcs11_lib().ok_or_else(|| {
            SignError(
                "Kart sürücüsü (PKCS#11 DLL: akisp11.dll/etpkcs11.dll) bulunamadı. UYAP_PKCS11_LIB ortam değişkeniyle yolu belirtebilirsiniz.".to_string(),
            )
        })?;
        let mut p = PyKCS11Lib::new();
        p.load(&lib).map_err(|e| SignError(format!("PKCS#11 modülü yüklenemedi: {}", e)))?;
        *k = Some(p);
    }
    Ok(k.as_ref().expect("yüklendi"))
}

fn kart_isi_gonder(is_: KartIsi) -> Result<(), SignError> {
    kart_kuyrugu()
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .send(is_)
        .map_err(|_| SignError("E-imza iş parçacığı çalışmıyor; programı yeniden başlatın.".to_string()))
}

/// Ofis makinesine takılı, sertifikası olan bir kart var mı? (headless ön kontrol)
pub fn card_present() -> bool {
    let (tx, rx) = std::sync::mpsc::channel();
    kart_isi_gonder(KartIsi::KartVar { cevap: tx }).is_ok() && rx.recv().unwrap_or(false)
}

pub fn _p1363_to_der(sig: &[u8]) -> Vec<u8> {
    let n = sig.len() / 2;
    let r = cades::integer_be(&sig[..n]);
    let s = cades::integer_be(&sig[n..]);
    cades::seq(&[&r, &s])
}

pub fn _make_sign_sgn(
    content_xml: &[u8],
    pin: &str,
    prefer_serial: Option<&str>,
) -> Result<Vec<u8>, SignError> {
    if pin.is_empty() {
        return Err(SignError(
            "Kart PIN'i bulunamadı (ofis henüz giriş yapmamış olabilir).".to_string(),
        ));
    }
    let (tx, rx) = std::sync::mpsc::channel();
    kart_isi_gonder(KartIsi::Imza {
        content_xml: content_xml.to_vec(),
        pin: pin.to_string(),
        prefer_serial: prefer_serial.map(str::to_string),
        cevap: tx,
    })?;
    let (cert_der, signed_attrs, mut signature, is_ecdsa) =
        rx.recv().map_err(|_| SignError("E-imza iş parçacığı yanıt vermedi.".to_string()))??;
    if is_ecdsa && signature.len() % 2 == 0 {
        signature = _p1363_to_der(&signature);
    }
    cades::build_cms(content_xml, &cert_der, &signed_attrs, &signature, is_ecdsa)
        .map_err(SignError)
}

/// Kart iş parçacığında: oturum aç → giriş → sertifika → imza. Açılan oturum (varsa) kapatılmak üzere döner.
fn kartla_imzala(
    pkcs11: &PyKCS11Lib,
    content_xml: &[u8],
    pin: &str,
    prefer_serial: Option<&str>,
) -> (Result<KartImzasi, SignError>, Option<akis_pkcs11::Session>) {
    let slots = match pkcs11.getSlotList(true) {
        Ok(s) => s,
        Err(e) => return (Err(SignError(format!("Slot listesi alınamadı: {}", e))), None),
    };
    if slots.is_empty() {
        return (Err(SignError("Kartı olan okuyucu bulunamadı. E-imza kartı takılı mı?".to_string())), None);
    }
    let session = match pkcs11.openSession(slots[0], PyKCS11::CKF_SERIAL_SESSION) {
        Ok(s) => s,
        Err(e) => return (Err(SignError(format!("Oturum açılamadı: {}", e))), None),
    };

    let sign_result: Result<KartImzasi, SignError> = (|| {
        session.login_user(pin).map_err(|e| {
            SignError(format!(
                "Karta giriş yapılamadı (PIN hatalı ya da kart kilitli olabilir). Detay: {}",
                e
            ))
        })?;

        let certs = session
            .findObjects(&[(
                PyKCS11::CKA_CLASS,
                TemplateVal::Ulong(PyKCS11::CKO_CERTIFICATE),
            )])
            .map_err(|e| SignError(format!("Sertifika araması başarısız: {}", e)))?;

        let mut cert_der: Option<Vec<u8>> = None;
        let mut cid: Vec<u8> = Vec::new();

        for &c in &certs {
            let attrs = session
                .getAttributeValue(c, &[PyKCS11::CKA_VALUE, PyKCS11::CKA_ID])
                .map_err(|e| SignError(format!("Sertifika özniteliği okunamadı: {}", e)))?;
            let der = attrs[0].as_bytes().to_vec();
            let id_bytes = attrs[1].as_bytes().to_vec();
            let (ok, serial_dec) = match cades::parse_cert_summary(&der) {
                Ok(summary) => (summary.key_usage_ok, Some(summary.serial_decimal)),
                Err(_) => (true, None),
            };
            if let Some(pref) = prefer_serial {
                if serial_dec.as_deref() != Some(pref) {
                    continue;
                }
            }
            if ok {
                cert_der = Some(der);
                cid = id_bytes;
                break;
            }
        }

        if cert_der.is_none() && !certs.is_empty() {
            let attrs = session
                .getAttributeValue(certs[0], &[PyKCS11::CKA_VALUE, PyKCS11::CKA_ID])
                .map_err(|e| SignError(format!("Sertifika özniteliği okunamadı: {}", e)))?;
            cert_der = Some(attrs[0].as_bytes().to_vec());
            cid = attrs[1].as_bytes().to_vec();
        }

        let cert_der = cert_der.ok_or_else(|| SignError("Kartta sertifika bulunamadı.".to_string()))?;

        let (signed_attrs, _, _) =
            cades::to_be_signed(content_xml, &cert_der, None).map_err(SignError)?;

        let mut priv_keys = session
            .findObjects(&[
                (
                    PyKCS11::CKA_CLASS,
                    TemplateVal::Ulong(PyKCS11::CKO_PRIVATE_KEY),
                ),
                (PyKCS11::CKA_ID, TemplateVal::Bytes(cid)),
            ])
            .unwrap_or_default();
        if priv_keys.is_empty() {
            priv_keys = session
                .findObjects(&[(
                    PyKCS11::CKA_CLASS,
                    TemplateVal::Ulong(PyKCS11::CKO_PRIVATE_KEY),
                )])
                .unwrap_or_default();
        }
        if priv_keys.is_empty() {
            return Err(SignError("Kartta özel anahtar bulunamadı.".to_string()));
        }
        let priv_key = priv_keys[0];
        let ktype_attrs = session
            .getAttributeValue(priv_key, &[PyKCS11::CKA_KEY_TYPE])
            .map_err(|e| SignError(format!("Anahtar türü okunamadı: {}", e)))?;
        let ktype = match ktype_attrs.first() {
            Some(AttrValue::Ulong(Some(v))) => *v,
            _ => PyKCS11::CKK_RSA,
        };
        let is_ecdsa = ktype == PyKCS11::CKK_ECDSA || ktype == PyKCS11::CKK_EC;

        let h = Sha256::digest(&signed_attrs);
        let (to_sign, mech) = if is_ecdsa {
            (h.to_vec(), Mechanism::new(PyKCS11::CKM_ECDSA, None))
        } else {
            let prefix: [u8; 19] = [
                0x30, 0x31, 0x30, 0x0d, 0x06, 0x09, 0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04,
                0x02, 0x01, 0x05, 0x00, 0x04, 0x20,
            ];
            let mut v = Vec::with_capacity(prefix.len() + h.len());
            v.extend_from_slice(&prefix);
            v.extend_from_slice(&h);
            (v, Mechanism::new(PyKCS11::CKM_RSA_PKCS, None))
        };

        let signature = session
            .sign(priv_key, &to_sign, &mech)
            .map_err(|e| SignError(format!("İmzalama başarısız: {}", e)))?;

        Ok((cert_der, signed_attrs, signature, is_ecdsa))
    })();

    (sign_result, Some(session))
}

fn _sign_content(
    content_xml: &[u8],
    _certificate_id: Option<&str>,
    pin: &str,
    preserved: &[(String, Vec<u8>)],
) -> Result<Vec<u8>, SignError> {
    let sgn = _make_sign_sgn(content_xml, pin, None)?;

    let mut out = Cursor::new(Vec::new());
    {
        let mut zout = ZipWriter::new(&mut out);
        let opts = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);
        let mut has_content = false;
        for (name, data) in preserved {
            if name != "sign.sgn" {
                if name == "content.xml" {
                    has_content = true;
                }
                zout.start_file(name, opts)
                    .map_err(|e| SignError(format!("ZIP yazma hatası: {}", e)))?;
                zout.write_all(data)
                    .map_err(|e| SignError(format!("ZIP yazma hatası: {}", e)))?;
            }
        }
        if !has_content {
            zout.start_file("content.xml", opts)
                .map_err(|e| SignError(format!("ZIP yazma hatası: {}", e)))?;
            zout.write_all(content_xml)
                .map_err(|e| SignError(format!("ZIP yazma hatası: {}", e)))?;
        }
        zout.start_file("sign.sgn", opts)
            .map_err(|e| SignError(format!("ZIP yazma hatası: {}", e)))?;
        zout.write_all(&sgn)
            .map_err(|e| SignError(format!("ZIP yazma hatası: {}", e)))?;
        zout.finish()
            .map_err(|e| SignError(format!("ZIP bitirme hatası: {}", e)))?;
    }
    Ok(out.into_inner())
}

fn trim_ascii_start(b: &[u8]) -> &[u8] {
    let mut i = 0;
    while i < b.len() && b[i].is_ascii_whitespace() {
        i += 1;
    }
    &b[i..]
}

fn starts_with_ignore_ascii_case(haystack: &[u8], needle: &[u8]) -> bool {
    if haystack.len() < needle.len() {
        return false;
    }
    haystack[..needle.len()].eq_ignore_ascii_case(needle)
}

fn contains_ignore_ascii_case(haystack: &[u8], needle: &[u8]) -> bool {
    if needle.is_empty() {
        return true;
    }
    haystack
        .windows(needle.len())
        .any(|w| w.eq_ignore_ascii_case(needle))
}

pub fn sign_udf_bytes(
    udf_bytes: &[u8],
    certificate_id: Option<&str>,
    pin: &str,
) -> Result<Vec<u8>, SignError> {
    if pin.is_empty() {
        return Err(SignError(
            "Kart PIN'i bulunamadı (ofis henüz giriş yapmamış olabilir).".to_string(),
        ));
    }

    let head = trim_ascii_start(udf_bytes);
    if !udf_bytes.starts_with(b"PK") && starts_with_ignore_ascii_case(head, b"<?xml") {
        return _sign_content(udf_bytes, certificate_id, pin, &[]);
    }

    let mut zin = match ZipArchive::new(Cursor::new(udf_bytes)) {
        Ok(z) => z,
        Err(_) => {
            let first64 = &udf_bytes[..udf_bytes.len().min(64)];
            let first256 = &udf_bytes[..udf_bytes.len().min(256)];
            if first64.contains(&b'<')
                && (contains_ignore_ascii_case(first64, b"<?xml")
                    || contains_ignore_ascii_case(first256, b"<template"))
            {
                return _sign_content(udf_bytes, certificate_id, pin, &[]);
            }
            return Err(SignError(format!(
                "Geçersiz UDF: ZIP olarak açılamadı. ({})",
                _diag(udf_bytes)
            )));
        }
    };

    let mut has_content_xml = false;
    for i in 0..zin.len() {
        if let Ok(f) = zin.by_index(i) {
            if f.name() == "content.xml" {
                has_content_xml = true;
                break;
            }
        }
    }
    if !has_content_xml {
        return Err(SignError(
            "UDF içinde content.xml bulunamadı; bu bir UYAP belgesi olmayabilir.".to_string(),
        ));
    }

    let mut content = Vec::new();
    let mut preserved: Vec<(String, Vec<u8>)> = Vec::new();
    for i in 0..zin.len() {
        let mut f = zin
            .by_index(i)
            .map_err(|e| SignError(format!("UDF okuma hatası: {}", e)))?;
        let name = f.name().to_string();
        if name == "sign.sgn" {
            continue;
        }
        let mut data = Vec::new();
        f.read_to_end(&mut data)
            .map_err(|e| SignError(format!("UDF girdi okuma hatası: {}", e)))?;
        if name == "content.xml" {
            content = data.clone();
        }
        preserved.push((name, data));
    }

    _sign_content(&content, certificate_id, pin, &preserved)
}

fn _convert_to_content_xml(body: &[u8], ext: &str) -> Result<Vec<u8>, SignError> {
    let dir = std::env::temp_dir();
    let fname = format!("ruyap_conv_{}{}", std::process::id(), ext);
    let path = dir.join(fname);
    std::fs::write(&path, body)
        .map_err(|e| SignError(format!("Geçici dosya yazılamadı: {}", e)))?;
    let res = udf_converter::convert_to_udf_xml(&path.to_string_lossy())
        .map_err(|e| SignError(format!("Belge UDF'ye çevrilemedi: {}", e)));
    let _ = std::fs::remove_file(&path);
    res
}

pub fn sign_document(
    body: &[u8],
    filename_or_ext: &str,
    certificate_id: Option<&str>,
    pin: &str,
) -> Result<Vec<u8>, SignError> {
    if pin.is_empty() {
        return Err(SignError(
            "Kart PIN'i bulunamadı (ofis henüz giriş yapmamış olabilir).".to_string(),
        ));
    }
    if body.is_empty() {
        return Err(SignError("Boş belge.".to_string()));
    }

    let name = filename_or_ext.trim().to_lowercase();
    let ext = if name.starts_with('.') && !name[1..].contains('.') {
        name.clone()
    } else {
        Path::new(&name)
            .extension()
            .and_then(|s| s.to_str())
            .map(|s| format!(".{}", s))
            .unwrap_or_default()
    };

    let looks_xml = starts_with_ignore_ascii_case(trim_ascii_start(body), b"<?xml");
    if ext == ".udf" || (ext.is_empty() && (body.starts_with(b"PK") || looks_xml)) {
        return sign_udf_bytes(body, certificate_id, pin);
    }
    if CONVERTIBLE_EXTS.contains(&ext.as_str()) {
        let content_xml = _convert_to_content_xml(body, &ext)?;
        return _sign_content(&content_xml, certificate_id, pin, &[]);
    }
    Err(SignError(format!(
        "Desteklenmeyen dosya türü: {} — yalnızca .udf, .docx, .doc, .pdf kabul edilir. ({})",
        if ext.is_empty() { "?" } else { &ext },
        _diag(body)
    )))
}

/// İmza adımlarının süre ölçümü (elle, PIN'siz; karta giriş yapılmaz):
///   OLCUM_PDF=<pdf> OLCUM_UDF=<udf> cargo test --release --lib -- udf_signer::olcum --ignored --nocapture
#[cfg(test)]
mod olcum {
    use super::*;
    use std::time::Instant;

    #[test]
    #[ignore]
    fn imza_adim_sureleri() {
        // Yeni tasarım: sürücü bir kez yüklenir; kullanıcının beklediği kısım (oturum → sertifika) ile sonradan
        // yapılan kapatma ayrı ölçülür. (İmza ve giriş PIN ister; ölçülmez.)
        let t = Instant::now();
        let mut p = PyKCS11Lib::new();
        p.load(&_detect_pkcs11_lib().expect("kart sürücüsü yok")).expect("yükleme");
        println!("RUST sürücü yükle (bir kez): {:?}", t.elapsed());
        for tur in 1..=3 {
            let t0 = Instant::now();
            let slots = p.getSlotList(true).expect("slot");
            if slots.is_empty() {
                println!("RUST kart: takılı kart yok");
                break;
            }
            let s = p.openSession(slots[0], PyKCS11::CKF_SERIAL_SESSION).expect("oturum");
            let certs = s.findObjects(&[(PyKCS11::CKA_CLASS, TemplateVal::Ulong(PyKCS11::CKO_CERTIFICATE))]).expect("bul");
            for &c in &certs {
                let _ = s.getAttributeValue(c, &[PyKCS11::CKA_VALUE, PyKCS11::CKA_ID]);
            }
            let hazir = t0.elapsed();
            s.closeSession();
            println!("RUST kart tur {tur}: kullanıcının beklediği {:?} | sonradan kapatma {:?}", hazir, t0.elapsed() - hazir);
        }
        for tur in 1..=2 {
            let t0 = Instant::now();
            let var = card_present();
            println!("RUST card_present (kart iş parçacığı) tur {tur}: {var} {:?}", t0.elapsed());
        }
        drop(p);
        if let Ok(pdf) = std::env::var("OLCUM_PDF") {
            let t = Instant::now();
            let r = udf_converter::convert_to_udf_xml(&pdf);
            println!("RUST pdf→udf: {:?} ({})", t.elapsed(), r.map(|x| format!("{} bayt", x.len())).unwrap_or_else(|e| e));
        }
        if let Ok(udf) = std::env::var("OLCUM_UDF") {
            let b = std::fs::read(&udf).unwrap();
            let t = Instant::now();
            let mut z = ZipArchive::new(Cursor::new(&b[..])).unwrap();
            let mut content = Vec::new();
            z.by_name("content.xml").unwrap().read_to_end(&mut content).unwrap();
            let cms_oncesi = t.elapsed();
            println!("RUST udf aç + content.xml: {:?} ({} bayt)", cms_oncesi, content.len());
        }
    }
}
