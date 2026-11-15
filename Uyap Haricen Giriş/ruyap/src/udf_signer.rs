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

/// Ofis makinesine takılı, sertifikası olan bir kart var mı? (headless ön kontrol)
pub fn card_present() -> bool {
    let Some(lib) = _detect_pkcs11_lib() else {
        return false;
    };
    let mut pkcs11 = PyKCS11Lib::new();
    if pkcs11.load(&lib).is_err() {
        return false;
    }
    match pkcs11.getSlotList(true) {
        Ok(slots) => !slots.is_empty(),
        Err(_) => false,
    }
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
    let lib = _detect_pkcs11_lib().ok_or_else(|| {
        SignError(
            "Kart sürücüsü (PKCS#11 DLL: akisp11.dll/etpkcs11.dll) bulunamadı. UYAP_PKCS11_LIB ortam değişkeniyle yolu belirtebilirsiniz.".to_string(),
        )
    })?;

    let mut pkcs11 = PyKCS11Lib::new();
    pkcs11
        .load(&lib)
        .map_err(|e| SignError(format!("PKCS#11 modülü yüklenemedi: {}", e)))?;
    let slots = pkcs11
        .getSlotList(true)
        .map_err(|e| SignError(format!("Slot listesi alınamadı: {}", e)))?;
    if slots.is_empty() {
        return Err(SignError(
            "Kartı olan okuyucu bulunamadı. E-imza kartı takılı mı?".to_string(),
        ));
    }

    let session = pkcs11
        .openSession(slots[0], PyKCS11::CKF_SERIAL_SESSION)
        .map_err(|e| SignError(format!("Oturum açılamadı: {}", e)))?;

    let sign_result: Result<(Vec<u8>, Vec<u8>, Vec<u8>, Vec<u8>, bool), SignError> = (|| {
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

        Ok((cert_der, signed_attrs, signature, to_sign, is_ecdsa))
    })();

    session.logout();
    session.closeSession();

    let (cert_der, signed_attrs, mut signature, _to_sign, is_ecdsa) = sign_result?;
    if is_ecdsa && signature.len() % 2 == 0 {
        signature = _p1363_to_der(&signature);
    }

    cades::build_cms(content_xml, &cert_der, &signed_attrs, &signature, is_ecdsa)
        .map_err(SignError)
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
