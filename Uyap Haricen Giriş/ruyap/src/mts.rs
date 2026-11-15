//! `uyap_core.mts` — MTS Takip Açma, SGK Sorgulama & Excel Raporu
//! (`mts/models.py`, `mts/evrak.py`, `mts/parse.py`, `mts/sgk.py`, `mts/sgk_excel.py`, `mts/takip.py` 1:1 portu)

use std::collections::{HashMap, HashSet};
use std::io::Write;
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use base64::Engine;
use once_cell::sync::Lazy;
use regex::Regex;
use roxmltree::Document;
use serde_json::{json, Value};
use zip::write::SimpleFileOptions;
use zip::{CompressionMethod, ZipWriter};

use crate::ipotek::{_guvenli_liste, _temiz_buyuk};
use crate::jobs::JobContext;
use crate::udf_signer;
use crate::uyap_proxy;

// ══════════════════════════════════════════════════════════════════════════════
// models.py
// ══════════════════════════════════════════════════════════════════════════════
pub mod models {
    use super::*;

    pub const ALACAKLI_AD: &str = "YEDİEMİN";
    pub const ALACAKLI_TCKN: &str = "";
    pub const ALACAKLI_IBAN: &str = "";

    pub fn format_date_with_slashes(raw: &str) -> String {
        let s = raw.trim();
        if s.is_empty() {
            return String::new();
        }
        let clean: String = s.chars().filter(|c| c.is_ascii_digit()).collect();
        if clean.len() == 8 {
            // YYYYMMDD -> DD/MM/YYYY veya DDMMYYYY -> DD/MM/YYYY
            if clean.starts_with("19") || clean.starts_with("20") {
                return format!("{}/{}/{}", &clean[6..8], &clean[4..6], &clean[0..4]);
            } else {
                return format!("{}/{}/{}", &clean[0..2], &clean[2..4], &clean[4..8]);
            }
        }
        let normalized = s.replace('.', "/").replace('-', "/");
        let parts: Vec<&str> = normalized.split('/').collect();
        if parts.len() == 3 {
            if parts[0].len() == 4 {
                return format!(
                    "{:0>2}/{:0>2}/{}",
                    parts[2], parts[1], parts[0]
                );
            }
            return format!("{:0>2}/{:0>2}/{}", parts[0], parts[1], parts[2]);
        }
        s.to_string()
    }

    pub fn format_date_with_dots(raw: &str) -> String {
        format_date_with_slashes(raw).replace('/', ".")
    }

    pub fn format_money(val: f64) -> String {
        format!("{:.2}", val).replace('.', ",")
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// evrak.py
// ══════════════════════════════════════════════════════════════════════════════
pub mod evrak {
    use super::*;

    pub const URL_PATH: &str = "davaAcilisEvrakGonderme_brd.ajx";
    pub const VARSAYILAN_EVRAK_SIRASI: &[&str] = &[
        "ICR_TAKIP_TLP",
        "CZM_VEKALETNAME",
        "MTS_TAKIBIN_DAYANAGI",
    ];

    pub fn evrak_turu_sablon(tur: &str) -> Option<Value> {
        let accept = json!([
            ".udf", ".pdf", ".jpg", ".png", ".jpeg", ".image/jpg", ".image/jpeg",
            ".tif", ".tiff", ".UDF", ".PDF", ".JPG", ".JPEG", ".PNG", ".TIF", ".TIFF"
        ]);
        match tur {
            "ICR_TAKIP_TLP" => Some(json!({
                "tur": "ICR_TAKIP_TLP",
                "label": "Takip Talebi",
                "mandatory": true,
                "max": 1,
                "ekEvrakMax": 5,
                "sablonTurKodu": "",
                "acceptTypeList": [".udf", ".UDF"],
                "ekEvrakAcceptTypeList": accept,
            })),
            "CZM_VEKALETNAME" => Some(json!({
                "tur": "CZM_VEKALETNAME",
                "label": "Vekaletname",
                "mandatory": true,
                "max": 1,
                "ekEvrakMax": 5,
                "sablonTurKodu": "",
                "acceptTypeList": accept,
                "ekEvrakAcceptTypeList": accept,
            })),
            "MTS_TAKIBIN_DAYANAGI" => Some(json!({
                "tur": "MTS_TAKIBIN_DAYANAGI",
                "label": "Takibin Dayanağı",
                "mandatory": true,
                "max": 1,
                "ekEvrakMax": 5,
                "sablonTurKodu": "",
                "acceptTypeList": accept,
                "ekEvrakAcceptTypeList": accept,
            })),
            _ => None,
        }
    }

    pub fn mime_belirle(filename: &str) -> &'static str {
        let lower = filename.trim().to_lowercase();
        if lower.ends_with(".pdf") {
            "application/pdf"
        } else if lower.ends_with(".jpg") || lower.ends_with(".jpeg") {
            "image/jpeg"
        } else if lower.ends_with(".png") {
            "image/png"
        } else if lower.ends_with(".tif") || lower.ends_with(".tiff") {
            "image/tiff"
        } else {
            "application/octet-stream"
        }
    }

    pub fn items_kur(evraklar: &[(&str, &str)]) -> (String, Vec<(String, String)>) {
        let now_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as i64;
        let mut items = Vec::with_capacity(evraklar.len());
        let mut alanlar = Vec::with_capacity(evraklar.len());
        for (idx, &(tur, dosya_adi)) in evraklar.iter().enumerate() {
            let i = idx + 1;
            let fname = if dosya_adi.is_empty() {
                format!("evrak_{}", i)
            } else {
                dosya_adi.to_string()
            };
            let sablon = evrak_turu_sablon(tur).unwrap_or_else(|| {
                json!({
                    "tur": tur,
                    "label": tur,
                    "mandatory": true,
                    "max": 1,
                    "ekEvrakMax": 5,
                    "sablonTurKodu": "",
                })
            });
            let label = sablon
                .get("label")
                .and_then(|v| v.as_str())
                .unwrap_or(tur)
                .to_string();
            let mandatory = sablon
                .get("mandatory")
                .and_then(|v| v.as_bool())
                .unwrap_or(true);
            items.push(json!({
                "id": now_ms + i as i64,
                "evrakTuruOptionDVO": sablon,
                "tur": tur,
                "turAciklama": label,
                "mandatory": mandatory,
                "file": {},
                "path": format!("C:/fakepath/{}", fname),
                "kullaniciEvrakAciklama": "",
                "parentId": -1,
                "isVekalet": false,
                "isVekaletBilgiFormu": false,
                "fileId": i,
            }));
            alanlar.push((format!("file_{}", i), fname));
        }
        (
            serde_json::to_string(&items).unwrap_or_else(|_| "[]".to_string()),
            alanlar,
        )
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// parse.py
// ══════════════════════════════════════════════════════════════════════════════
pub mod parse {
    use super::*;

    fn attr<'a>(node: roxmltree::Node<'a, 'a>, name: &str) -> String {
        node.attribute(name).unwrap_or("").trim().to_string()
    }

    pub fn parse_mts_xml(xml_bytes: &[u8]) -> Result<Value, String> {
        let text = String::from_utf8_lossy(xml_bytes);
        let doc =
            Document::parse(&text).map_err(|e| format!("MTS XML ayrıştırma hatası: {}", e))?;

        let mut borclular = Vec::new();
        let mut alacaklilar = Vec::new();
        let mut kalemler = Vec::new();
        let mut takip_tarihi = String::new();
        let mut abone_no = String::new();

        for node in doc.descendants() {
            match node.tag_name().name() {
                "dosya" => {
                    takip_tarihi = models::format_date_with_slashes(&attr(node, "takipTarihi"));
                    abone_no = attr(node, "aboneNo");
                }
                "taraf" => {
                    let mut rol = String::new();
                    let mut kisi = json!({});
                    for ch in node.children().filter(|n| n.is_element()) {
                        match ch.tag_name().name() {
                            "rolTur" => rol = attr(ch, "rol"),
                            "kisiKurumBilgileri" => {
                                if let Some(kn) =
                                    ch.children().find(|n| n.has_tag_name("kisiTumBilgileri"))
                                {
                                    kisi = json!({
                                        "tur": "gercek",
                                        "tckn": attr(kn, "tcKimlikNo"),
                                        "ad": attr(kn, "adi"),
                                        "soyad": attr(kn, "soyadi"),
                                    });
                                } else if let Some(kr) =
                                    ch.children().find(|n| n.has_tag_name("kurum"))
                                {
                                    kisi = json!({
                                        "tur": "tuzel",
                                        "unvan": attr(kr, "kurumAdi"),
                                        "vergiNo": attr(kr, "vergiNo"),
                                        "mersisNo": attr(kr, "mersisNo"),
                                    });
                                }
                            }
                            _ => {}
                        }
                    }
                    if rol == "1" {
                        alacaklilar.push(kisi);
                    } else if rol == "2" {
                        borclular.push(kisi);
                    }
                }
                "alacakKalemi" => {
                    let tutar: f64 = attr(node, "alacakKalemTutar")
                        .replace(',', ".")
                        .parse()
                        .unwrap_or(0.0);
                    let oran: f64 = attr(node, "faizOrani")
                        .replace(',', ".")
                        .parse()
                        .unwrap_or(0.0);
                    kalemler.push(json!({
                        "kod": attr(node, "alacakKalemKod"),
                        "kodAciklama": attr(node, "alacakKalemKodAciklama"),
                        "aciklama": attr(node, "alacakKalemAdi"),
                        "tutar": (tutar * 100.0).round() / 100.0,
                        "faizOrani": oran,
                    }));
                }
                _ => {}
            }
        }

        Ok(json!({
            "takipTarihi": takip_tarihi,
            "aboneNo": abone_no,
            "alacaklilar": alacaklilar,
            "borclular": borclular,
            "kalemler": kalemler,
        }))
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// sgk.py
// ══════════════════════════════════════════════════════════════════════════════
pub mod sgk {
    use super::*;

    pub const LIST_FILE_ENDPOINT: &str = "search_phrase_detayli.ajx";
    pub const TARAF_BILGILERI_ENDPOINT: &str = "dosya_taraf_bilgileri_brd.ajx";
    pub const SORGU_ENDPOINT: &str = "sgk_sorgula_brd.ajx";
    pub const KISI_SORGULAMA_DELAY_SN: f64 = 1.5;

    pub async fn sgk_sorgula(
        ctx: &JobContext,
        dosya_id: &str,
        kisi_id: &str,
        tckn: &str,
    ) -> Result<Value, String> {
        tokio::time::sleep(Duration::from_secs_f64(KISI_SORGULAMA_DELAY_SN)).await;
        let resp = ctx
            .uyap_json(
                "POST",
                SORGU_ENDPOINT,
                &json!({
                    "dosyaId": dosya_id,
                    "kisiId": kisi_id,
                    "tcKimlikNo": tckn,
                }),
            )
            .await
            .map_err(|e| e.to_string())?;
        if resp.status_code >= 400 {
            return Err(format!("SGK sorgusu HTTP {} döndürdü.", resp.status_code));
        }
        serde_json::from_str(&resp.text)
            .map_err(|e| format!("SGK yanıtı JSON hatası: {}", e))
    }

    pub async fn toplu_sgk_sorgula(
        ctx: &JobContext,
        dosya_idleri: &[String],
    ) -> Result<Vec<Value>, String> {
        let mut sonuclar = Vec::new();
        let toplam = dosya_idleri.len();
        for (idx, did) in dosya_idleri.iter().enumerate() {
            ctx.check_cancel().map_err(|e| e.to_string())?;
            ctx.progress((idx + 1) as i64, toplam as i64, &format!("SGK: {}", did));

            let taraf_resp = ctx
                .uyap_json("POST", TARAF_BILGILERI_ENDPOINT, &json!({"dosyaId": did}))
                .await
                .map_err(|e| e.to_string())?;
            let taraflar: Vec<Value> = serde_json::from_str(&taraf_resp.text).unwrap_or_default();

            for t in taraflar {
                let rol = t.get("rol").and_then(|v| v.as_str()).unwrap_or("");
                if !rol.to_lowercase().contains("borçlu") && !rol.to_lowercase().contains("borclu")
                {
                    continue;
                }
                let tckn = t
                    .get("tcKimlikNo")
                    .or_else(|| t.get("tckn"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let kisi_id = t.get("kisiId").and_then(|v| v.as_str()).unwrap_or("");
                let adi = t.get("adi").and_then(|v| v.as_str()).unwrap_or("");
                if tckn.is_empty() {
                    continue;
                }
                match sgk_sorgula(ctx, did, kisi_id, tckn).await {
                    Ok(veri) => {
                        sonuclar.push(json!({
                            "dosyaId": did,
                            "adi": adi,
                            "tckn": tckn,
                            "veri": veri,
                        }));
                    }
                    Err(e) => {
                        ctx.log(&format!("⚠️ SGK hatası ({} / {}): {}", did, tckn, e));
                        sonuclar.push(json!({
                            "dosyaId": did,
                            "adi": adi,
                            "tckn": tckn,
                            "hata": e,
                        }));
                    }
                }
            }
        }
        Ok(sonuclar)
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// sgk_excel.py
// ══════════════════════════════════════════════════════════════════════════════
pub mod sgk_excel {
    use super::*;

    pub const BASLIKLAR: &[&str] = &[
        "Dosya ID",
        "Borçlu Adı",
        "TCKN",
        "Durum",
        "İşyeri Ünvanı",
        "İşyeri Adresi",
        "İşe Giriş Tarihi",
        "Hata",
    ];

    pub fn excel_uret(sonuclar: &[Value], cikti_yol: &Path) -> Result<(), String> {
        let mut satirlar = vec![BASLIKLAR.iter().map(|s| (*s).to_string()).collect()];
        for s in sonuclar {
            let did = s.get("dosyaId").and_then(|v| v.as_str()).unwrap_or("");
            let adi = s.get("adi").and_then(|v| v.as_str()).unwrap_or("");
            let tckn = s.get("tckn").and_then(|v| v.as_str()).unwrap_or("");
            let hata = s.get("hata").and_then(|v| v.as_str()).unwrap_or("");
            let veri = s.get("veri");
            let isyeri = veri
                .and_then(|v| v.get("isyeriAdi").or_else(|| v.get("isyeriUnvani")))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let adres = veri
                .and_then(|v| v.get("isyeriAdresi"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let giris = veri
                .and_then(|v| v.get("iseGirisTarihi"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let durum = if !hata.is_empty() {
                "Hata"
            } else if !isyeri.is_empty() {
                "Aktif Çalışan"
            } else {
                "Kayıt Yok"
            };
            satirlar.push(vec![
                did.to_string(),
                adi.to_string(),
                tckn.to_string(),
                durum.to_string(),
                isyeri.to_string(),
                adres.to_string(),
                giris.to_string(),
                hata.to_string(),
            ]);
        }
        crate::gerekceli_karar::_xlsx(&satirlar, cikti_yol)
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// takip.py
// ══════════════════════════════════════════════════════════════════════════════
pub mod takip {
    use super::*;

    pub const MTS_DOSYA_TUR_KOD: i64 = 35;

    pub async fn _api_json(
        ctx: &JobContext,
        path: &str,
        payload: Option<&Value>,
    ) -> Result<Value, String> {
        let body = payload.cloned().unwrap_or_else(|| json!({}));
        let resp = ctx
            .uyap_json("POST", path, &body)
            .await
            .map_err(|e| e.to_string())?;
        if resp.status_code >= 400 {
            return Err(format!("UYAP '{}' HTTP {} döndürdü.", path, resp.status_code));
        }
        serde_json::from_str(&resp.text)
            .map_err(|e| format!("UYAP '{}' JSON hatası: {}", path, e))
    }

    pub async fn odenmis_dosyalari_bul(
        ctx: &JobContext,
        hedef_adlar: &[String],
    ) -> Result<Option<String>, String> {
        crate::esas_no::bul(ctx, hedef_adlar, MTS_DOSYA_TUR_KOD).await
    }

    pub async fn prepare(
        ctx: &JobContext,
        parsed: &Value,
        params: &Value,
    ) -> Result<(Value, Value), String> {
        let log = |m: &str| ctx.log(m);

        let vekalet = params
            .get("vekalet")
            .and_then(crate::ipotek::takip::_belge_coz)
            .ok_or_else(|| "Vekaletname belgesi zorunludur.".to_string())?;
        let mut dayanak_listesi = Vec::new();
        if let Some(arr) = params.get("dayanak_listesi").and_then(|v| v.as_array()) {
            for item in arr {
                if let Some(b) = crate::ipotek::takip::_belge_coz(item) {
                    dayanak_listesi.push(b);
                }
            }
        }
        if dayanak_listesi.is_empty() {
            return Err("En az 1 adet dayanak belgesi zorunludur.".to_string());
        }

        log("MTS: Kurum ve borçlu bilgileri hazırlanıyor...");
        let kurum_v = _api_json(ctx, "kurumBilgisiSorgula_brd.ajx", None)
            .await
            .unwrap_or_else(|_| json!({}));
        let iban = params
            .get("iban")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        let borclular = parsed
            .get("borclular")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        let mut taraf_list = Vec::new();
        for b in &borclular {
            taraf_list.push(json!({
                "rol": 2,
                "kisi": {
                    "temelBilgileri": {
                        "tcKimlikNo": b.get("tckn").and_then(|v| v.as_str()).unwrap_or(""),
                        "adi": b.get("ad").and_then(|v| v.as_str()).unwrap_or(""),
                        "soyadi": b.get("soyad").and_then(|v| v.as_str()).unwrap_or(""),
                    }
                }
            }));
        }

        let kalemler = parsed
            .get("kalemler")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        let toplam_alacak: f64 = kalemler
            .iter()
            .filter_map(|k| k.get("tutar").and_then(|v| v.as_f64()))
            .sum();
        let toplam_alacak = (toplam_alacak * 100.0).round() / 100.0;

        let harc_resp = _api_json(
            ctx,
            "icra_takip_harc_hesapla.ajx",
            Some(&json!({"alacakTutari": toplam_alacak, "takipTuru": 35})),
        )
        .await
        .unwrap_or_else(|_| json!({}));
        let harc_listesi = _guvenli_liste(&harc_resp);
        let harc_toplam: f64 = harc_listesi
            .iter()
            .filter_map(|h| h.get("hesapMiktar").and_then(|v| v.as_f64()))
            .sum();

        let ozet = json!({
            "alacakli": kurum_v.get("kurumAdi"),
            "iban": iban,
            "borclular": borclular,
            "kalemler": kalemler,
            "toplam_alacak": toplam_alacak,
            "harc_toplam": (harc_toplam * 100.0).round() / 100.0,
        });

        let state = json!({
            "taraf_list": taraf_list,
            "ilamsiz_list": kalemler,
            "iban": iban,
            "vekalet": {
                "filename": vekalet.0,
                "b64": base64::engine::general_purpose::STANDARD.encode(&vekalet.1),
            },
            "dayanak_listesi": dayanak_listesi.iter().map(|(fnm, b)| {
                json!({
                    "filename": fnm,
                    "b64": base64::engine::general_purpose::STANDARD.encode(b),
                })
            }).collect::<Vec<_>>(),
        });

        Ok((ozet, state))
    }

    pub async fn finalize(ctx: &JobContext, state: &Value) -> Result<Value, String> {
        let log = |m: &str| ctx.log(m);
        let gw = uyap_proxy::get_gw()
            .ok_or_else(|| "UYAP oturumu hazır değil (gw=None).".to_string())?;

        let taraf_list = state.get("taraf_list").cloned().unwrap_or_else(|| json!([]));
        let ilamsiz_list = state.get("ilamsiz_list").cloned().unwrap_or_else(|| json!([]));

        log("MTS tevzi numarası alınıyor...");
        let tevzi_sonuc = _api_json(
            ctx,
            "mts_takip_tevzi_islemleri.ajx",
            Some(&json!({
                "TarafList": serde_json::to_string(&taraf_list).unwrap_or_default(),
                "IlamsizList": serde_json::to_string(&ilamsiz_list).unwrap_or_default(),
            })),
        )
        .await?;

        let dosya_id = tevzi_sonuc
            .get("dosyaId")
            .and_then(|v| v.as_str())
            .ok_or_else(|| format!("UYAP MTS tevzi numarası döndürmedi: {}", tevzi_sonuc))?
            .trim()
            .trim_matches('"')
            .to_string();
        log(&format!("MTS Tevzi tamamlandı. Dosya ID: {}", dosya_id));

        let q = format!("dosyaId={}", urlencoding::encode(&dosya_id));
        let resp = ctx
            .uyap_get("icraTakipTalebiIndir.uyap", Some(&q))
            .await
            .map_err(|e| e.to_string())?;
        if resp.status_code >= 400 || resp.body.is_empty() {
            return Err(format!(
                "MTS takip talebi indirilemedi (HTTP {}, Dosya ID: {}).",
                resp.status_code, dosya_id
            ));
        }

        let guvenli_isim = "MTS_Takip_Talebi.udf";
        log("MTS takip talebi e-imzalanıyor...");
        let cert_id = gw.cert_id.lock().clone();
        let pin = gw.login_args.pin.clone().unwrap_or_default();
        let udf_bytes = resp.body;
        let signed = tokio::task::spawn_blocking(move || {
            udf_signer::sign_document(&udf_bytes, guvenli_isim, cert_id.as_deref(), &pin)
                .map_err(|e| e.0)
        })
        .await
        .map_err(|e| e.to_string())??;

        let vekalet = state
            .get("vekalet")
            .and_then(crate::ipotek::takip::_belge_coz)
            .ok_or_else(|| "Vekaletname bulunamadı.".to_string())?;
        let dayanak_arr = state
            .get("dayanak_listesi")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();

        let mut evraklar = vec![
            (
                "ICR_TAKIP_TLP".to_string(),
                guvenli_isim.to_string(),
                signed,
            ),
            ("CZM_VEKALETNAME".to_string(), vekalet.0, vekalet.1),
        ];
        for d in &dayanak_arr {
            if let Some((fnm, b)) = crate::ipotek::takip::_belge_coz(d) {
                evraklar.push(("MTS_TAKIBIN_DAYANAGI".to_string(), fnm, b));
            }
        }

        let meta: Vec<(&str, &str)> = evraklar
            .iter()
            .map(|(t, f, _)| (t.as_str(), f.as_str()))
            .collect();
        let (items_json, alanlar) = evrak::items_kur(&meta);

        let mut fields: Vec<(String, Option<String>, Vec<u8>, Option<String>)> = Vec::new();
        for ((alan, fname), (_, _, bytes)) in alanlar.iter().zip(evraklar.iter()) {
            let mime = evrak::mime_belirle(fname).to_string();
            fields.push((
                alan.clone(),
                Some(fname.clone()),
                bytes.clone(),
                Some(mime),
            ));
        }
        fields.push(("items".to_string(), None, items_json.into_bytes(), None));
        fields.push((
            "dosyaId".to_string(),
            None,
            dosya_id.clone().into_bytes(),
            None,
        ));

        let ev_resp = ctx
            .uyap_post_multipart(evrak::URL_PATH, fields)
            .await
            .map_err(|e| e.to_string())?;
        let sonuc: Value = serde_json::from_str(&ev_resp.text)
            .unwrap_or_else(|_| json!({"type": "unknown", "message": ev_resp.text}));
        if sonuc.get("type").and_then(|v| v.as_str()) != Some("success") {
            return Err(format!(
                "MTS evrak gönderme başarısız (Dosya ID: {}): {}",
                dosya_id, sonuc
            ));
        }
        log(&format!(
            "✓ MTS evrak gönderildi: {}",
            sonuc.get("message").and_then(|v| v.as_str()).unwrap_or("")
        ));

        Ok(json!({"dosya_id": dosya_id, "evrak_sonuc": sonuc}))
    }
}
