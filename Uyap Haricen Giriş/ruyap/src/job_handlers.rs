//! UYAP İş Kuyruğu İşleyicileri (`uyap_core/job_handlers.py`, `teblig_212/gonder.py`, `xml_takip/{parse.py, takip.py}` 1:1 portu)

use std::sync::Arc;
use std::time::Duration;

use base64::Engine;
use serde_json::{json, Value};

use crate::dosya_kaydet;
use crate::esas_no;
use crate::ipotek;
use crate::jobs::{JobContext, JobError, JobManager};
use crate::mts;
use crate::udf_signer;
use crate::uyap_proxy;

// ══════════════════════════════════════════════════════════════════════════════
// teblig_212/gonder.py — TK m.21/2 MERNİS Tebligatı Gönderimi
// ══════════════════════════════════════════════════════════════════════════════
pub mod teblig_212 {
    use super::*;

    pub const TALEP_KODU: &str = "TK212_TEBLIGAT";
    pub const TEBLIGAT_TUR_KODU: i64 = 212;

    pub async fn hazirla_ve_gonder(
        ctx: &JobContext,
        dosya_id: &str,
        taraf_id: &str,
        adres: &str,
        aciklama: &str,
    ) -> Result<Value, String> {
        let gw = uyap_proxy::get_gw()
            .ok_or_else(|| "UYAP oturumu hazır değil (gw=None).".to_string())?;

        ctx.log(&format!("21/2 tebligat talebi hazırlanıyor (dosyaId={}, tarafId={})...", dosya_id, taraf_id));
        let talep_resp = ctx
            .uyap_json(
                "POST",
                "tebligatTalepOlustur_brd.ajx",
                &json!({
                    "dosyaId": dosya_id,
                    "tarafId": taraf_id,
                    "adres": adres,
                    "tebligatTurKodu": TEBLIGAT_TUR_KODU,
                    "talepKodu": TALEP_KODU,
                    "aciklama": aciklama,
                }),
            )
            .await
            .map_err(|e| e.to_string())?;
        if talep_resp.status_code >= 400 {
            return Err(format!(
                "tebligatTalepOlustur_brd.ajx HTTP {} döndürdü.",
                talep_resp.status_code
            ));
        }

        let talep_json: Value = serde_json::from_str(&talep_resp.text).unwrap_or_else(|_| json!({}));
        let q = format!("dosyaId={}", urlencoding::encode(dosya_id));
        let udf_resp = ctx
            .uyap_get("tebligatTalepIndir.uyap", Some(&q))
            .await
            .map_err(|e| e.to_string())?;
        if udf_resp.status_code >= 400 || udf_resp.body.is_empty() {
            return Err(format!(
                "21/2 tebligat talebi UDF indirilemedi (HTTP {}).",
                udf_resp.status_code
            ));
        }

        let cert_id = gw.cert_id.lock().clone();
        let pin = gw.login_args.pin.clone().unwrap_or_default();
        let udf_bytes = udf_resp.body;
        let signed = tokio::task::spawn_blocking(move || {
            udf_signer::sign_document(&udf_bytes, "Tebligat_212.udf", cert_id.as_deref(), &pin)
                .map_err(|e| e.0)
        })
        .await
        .map_err(|e| e.to_string())??;

        let fields = vec![
            (
                "file".to_string(),
                Some("Tebligat_212.udf".to_string()),
                signed,
                Some("application/octet-stream".to_string()),
            ),
            (
                "dosyaId".to_string(),
                None,
                dosya_id.as_bytes().to_vec(),
                None,
            ),
            (
                "tarafId".to_string(),
                None,
                taraf_id.as_bytes().to_vec(),
                None,
            ),
        ];

        let gonder_resp = ctx
            .uyap_post_multipart("tebligatTalepGonder_brd.ajx", fields)
            .await
            .map_err(|e| e.to_string())?;
        let sonuc: Value = serde_json::from_str(&gonder_resp.text)
            .unwrap_or_else(|_| json!({"raw": gonder_resp.text, "talep": talep_json}));
        Ok(sonuc)
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// xml_takip/{parse.py, takip.py} — UYAP Standart İcra XML Takip Açılışı
// ══════════════════════════════════════════════════════════════════════════════
pub mod xml_takip {
    use super::*;
    use roxmltree::Document;

    fn attr<'a>(node: roxmltree::Node<'a, 'a>, name: &str) -> String {
        node.attribute(name).unwrap_or("").trim().to_string()
    }

    pub fn parse_xml(xml_bytes: &[u8]) -> Result<Value, String> {
        let text = String::from_utf8_lossy(xml_bytes);
        let doc =
            Document::parse(&text).map_err(|e| format!("XML ayrıştırma hatası: {}", e))?;

        let mut dosyalar = Vec::new();
        for d_node in doc.descendants().filter(|n| n.has_tag_name("dosya")) {
            let mut alacaklilar = Vec::new();
            let mut borclular = Vec::new();
            let mut kalemler = Vec::new();

            for ch in d_node.descendants() {
                match ch.tag_name().name() {
                    "taraf" => {
                        let mut rol = String::new();
                        let mut kisi = json!({});
                        for tc in ch.children().filter(|n| n.is_element()) {
                            match tc.tag_name().name() {
                                "rolTur" => rol = attr(tc, "rol"),
                                "kisiKurumBilgileri" => {
                                    if let Some(kn) =
                                        tc.children().find(|n| n.has_tag_name("kisiTumBilgileri"))
                                    {
                                        kisi = json!({
                                            "tur": "gercek",
                                            "tckn": attr(kn, "tcKimlikNo"),
                                            "ad": attr(kn, "adi"),
                                            "soyad": attr(kn, "soyadi"),
                                        });
                                    } else if let Some(kr) =
                                        tc.children().find(|n| n.has_tag_name("kurum"))
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
                        let tutar: f64 = attr(ch, "alacakKalemTutar")
                            .replace(',', ".")
                            .parse()
                            .unwrap_or(0.0);
                        let oran: f64 = attr(ch, "faizOrani")
                            .replace(',', ".")
                            .parse()
                            .unwrap_or(0.0);
                        kalemler.push(json!({
                            "kod": attr(ch, "alacakKalemKod"),
                            "kodAciklama": attr(ch, "alacakKalemKodAciklama"),
                            "aciklama": attr(ch, "alacakKalemAdi"),
                            "tutar": (tutar * 100.0).round() / 100.0,
                            "faizOrani": oran,
                        }));
                    }
                    _ => {}
                }
            }

            dosyalar.push(json!({
                "dosyaTipi": attr(d_node, "dosyaTipi"),
                "takipSekli": attr(d_node, "takipSekli"),
                "takipYolu": attr(d_node, "takipYolu"),
                "takipTarihi": mts::models::format_date_with_slashes(&attr(d_node, "takipTarihi")),
                "alacaklilar": alacaklilar,
                "borclular": borclular,
                "kalemler": kalemler,
            }));
        }

        if dosyalar.is_empty() {
            return Err("XML içinde <dosya> öğesi bulunamadı.".to_string());
        }
        Ok(json!({ "dosyalar": dosyalar }))
    }

    pub async fn prepare_and_finalize(
        ctx: &JobContext,
        dosya: &Value,
        params: &Value,
    ) -> Result<Value, String> {
        let gw = uyap_proxy::get_gw()
            .ok_or_else(|| "UYAP oturumu hazır değil (gw=None).".to_string())?;
        let vekalet = params
            .get("vekalet")
            .and_then(ipotek::takip::_belge_coz)
            .ok_or_else(|| "Vekaletname belgesi zorunludur.".to_string())?;

        let borclular = dosya
            .get("borclular")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        let kalemler = dosya
            .get("kalemler")
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

        let tevzi_sonuc = ipotek::takip::_api_json(
            ctx,
            "icra_takip_tevzi_islemleri.ajx",
            Some(&json!({
                "TarafList": serde_json::to_string(&taraf_list).unwrap_or_default(),
                "IlamsizList": serde_json::to_string(&kalemler).unwrap_or_default(),
                "IlamliList": [],
                "TahsilatList": [],
            })),
            false,
        )
        .await?;

        let dosya_id = tevzi_sonuc
            .get("dosyaId")
            .and_then(|v| v.as_str())
            .ok_or_else(|| format!("UYAP tevzi numarası döndürmedi: {}", tevzi_sonuc))?
            .trim()
            .trim_matches('"')
            .to_string();

        let q = format!("dosyaId={}", urlencoding::encode(&dosya_id));
        let resp = ctx
            .uyap_get("icraTakipTalebiIndir.uyap", Some(&q))
            .await
            .map_err(|e| e.to_string())?;
        if resp.status_code >= 400 || resp.body.is_empty() {
            return Err(format!(
                "Takip talebi indirilemedi (HTTP {}, Dosya ID: {}).",
                resp.status_code, dosya_id
            ));
        }

        let guvenli_isim = "Takip_Talebi.udf";
        let cert_id = gw.cert_id.lock().clone();
        let pin = gw.login_args.pin.clone().unwrap_or_default();
        let udf_bytes = resp.body;
        let signed = tokio::task::spawn_blocking(move || {
            udf_signer::sign_document(&udf_bytes, guvenli_isim, cert_id.as_deref(), &pin)
                .map_err(|e| e.0)
        })
        .await
        .map_err(|e| e.to_string())??;

        let mut evraklar = vec![
            (
                "ICR_TAKIP_TLP".to_string(),
                guvenli_isim.to_string(),
                signed,
            ),
            ("CZM_VEKALETNAME".to_string(), vekalet.0, vekalet.1),
        ];
        if let Some(arr) = params.get("dayanak_listesi").and_then(|v| v.as_array()) {
            for d in arr {
                if let Some((fnm, b)) = ipotek::takip::_belge_coz(d) {
                    evraklar.push(("MTS_TAKIBIN_DAYANAGI".to_string(), fnm, b));
                }
            }
        }

        let meta: Vec<(&str, &str)> = evraklar
            .iter()
            .map(|(t, f, _)| (t.as_str(), f.as_str()))
            .collect();
        let (items_json, alanlar) = mts::evrak::items_kur(&meta);

        let mut fields = Vec::new();
        for ((alan, fname), (_, _, bytes)) in alanlar.iter().zip(evraklar.iter()) {
            let mime = mts::evrak::mime_belirle(fname).to_string();
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
            .uyap_post_multipart(mts::evrak::URL_PATH, fields)
            .await
            .map_err(|e| e.to_string())?;
        let sonuc: Value = serde_json::from_str(&ev_resp.text)
            .unwrap_or_else(|_| json!({"type": "unknown", "message": ev_resp.text}));
        if sonuc.get("type").and_then(|v| v.as_str()) != Some("success") {
            return Err(format!(
                "Evrak gönderme başarısız (Dosya ID: {}): {}",
                dosya_id, sonuc
            ));
        }

        Ok(json!({"dosya_id": dosya_id, "evrak_sonuc": sonuc}))
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// Job Handlers
// ══════════════════════════════════════════════════════════════════════════════

async fn handle_bekle(ctx: JobContext, params: Value) -> Result<Value, JobError> {
    let adimlar = params.get("adimlar").and_then(|v| v.as_i64()).unwrap_or(5).max(1);
    let sn = params.get("sn").and_then(|v| v.as_f64()).unwrap_or(1.0).max(0.05);
    for i in 1..=adimlar {
        ctx.check_cancel()?;
        ctx.progress(i, adimlar, &format!("Adım {}/{}", i, adimlar));
        ctx.log(&format!("Adım {}/{} çalışıyor...", i, adimlar));
        tokio::time::sleep(Duration::from_secs_f64(sn)).await;
    }
    Ok(json!({"tamamlanan": adimlar}))
}

async fn handle_coklu_takip_ac(ctx: JobContext, params: Value) -> Result<Value, JobError> {
    let xml_b64 = params
        .get("xml_b64")
        .and_then(|v| v.as_str())
        .ok_or_else(|| JobError::Failed("xml_b64 gerekli.".to_string()))?;
    let xml_bytes = base64::engine::general_purpose::STANDARD
        .decode(xml_b64.as_bytes())
        .map_err(|e| JobError::Failed(format!("xml_b64 çözme hatası: {}", e)))?;
    let parsed = mts::parse::parse_mts_xml(&xml_bytes).map_err(JobError::Failed)?;
    let (ozet, state) = mts::takip::prepare(&ctx, &parsed, &params)
        .await
        .map_err(JobError::Failed)?;

    let (approved, note) = ctx
        .request_approval("MTS takip açılışını onaylıyor musunuz?", Some(ozet))
        .await?;
    if !approved {
        return Err(JobError::Cancelled(format!(
            "Kullanıcı onaylamadı{}",
            if note.is_empty() {
                String::new()
            } else {
                format!(": {}", note)
            }
        )));
    }

    let res = mts::takip::finalize(&ctx, &state)
        .await
        .map_err(JobError::Failed)?;
    Ok(res)
}

async fn handle_mts_odenmis_dosyalari_bul(
    ctx: JobContext,
    params: Value,
) -> Result<Value, JobError> {
    let adlar: Vec<String> = params
        .get("hedef_adlar")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|x| x.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();
    let dn = mts::takip::odenmis_dosyalari_bul(&ctx, &adlar)
        .await
        .map_err(JobError::Failed)?;
    Ok(json!({"dosyaNo": dn}))
}

async fn handle_mts_odeme_tamamla(ctx: JobContext, params: Value) -> Result<Value, JobError> {
    let dosya_id = params
        .get("dosyaId")
        .and_then(|v| v.as_str())
        .ok_or_else(|| JobError::Failed("dosyaId gerekli.".to_string()))?;
    let resp = ctx
        .uyap_json(
            "POST",
            "mts_odeme_tamamla_brd.ajx",
            &json!({"dosyaId": dosya_id}),
        )
        .await?;
    Ok(json!({"status": resp.status_code, "body": resp.text}))
}

async fn handle_mts_tebligat_gonder(ctx: JobContext, params: Value) -> Result<Value, JobError> {
    let dosya_id = params
        .get("dosyaId")
        .and_then(|v| v.as_str())
        .ok_or_else(|| JobError::Failed("dosyaId gerekli.".to_string()))?;
    let resp = ctx
        .uyap_json(
            "POST",
            "mts_tebligat_gonder_brd.ajx",
            &json!({"dosyaId": dosya_id}),
        )
        .await?;
    Ok(json!({"status": resp.status_code, "body": resp.text}))
}

async fn handle_normal_tebligat_gonder(ctx: JobContext, params: Value) -> Result<Value, JobError> {
    let dosya_id = params
        .get("dosyaId")
        .and_then(|v| v.as_str())
        .ok_or_else(|| JobError::Failed("dosyaId gerekli.".to_string()))?;
    let taraf_id = params
        .get("tarafId")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let adres = params.get("adres").and_then(|v| v.as_str()).unwrap_or("");
    let resp = ctx
        .uyap_json(
            "POST",
            "tebligatGonder_brd.ajx",
            &json!({"dosyaId": dosya_id, "tarafId": taraf_id, "adres": adres}),
        )
        .await?;
    Ok(json!({"status": resp.status_code, "body": resp.text}))
}

async fn handle_ipotek_takip_ac(ctx: JobContext, params: Value) -> Result<Value, JobError> {
    let parsed = if let Some(xlsx_b64) = params.get("xlsx_b64").and_then(|v| v.as_str()) {
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(xlsx_b64.as_bytes())
            .map_err(|e| JobError::Failed(format!("xlsx_b64 çözme hatası: {}", e)))?;
        ipotek::parse::excel_oku(&bytes).map_err(JobError::Failed)?
    } else if let Some(xml_b64) = params.get("xml_b64").and_then(|v| v.as_str()) {
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(xml_b64.as_bytes())
            .map_err(|e| JobError::Failed(format!("xml_b64 çözme hatası: {}", e)))?;
        ipotek::xml_parse::xml_ayristir(&bytes).map_err(JobError::Failed)?
    } else {
        return Err(JobError::Failed("xlsx_b64 veya xml_b64 gerekli.".to_string()));
    };

    let (ozet, state) = ipotek::takip::prepare(&ctx, &parsed, &params)
        .await
        .map_err(JobError::Failed)?;

    let (approved, note) = ctx
        .request_approval("İpotek takibi açılışını onaylıyor musunuz?", Some(ozet))
        .await?;
    if !approved {
        return Err(JobError::Cancelled(format!(
            "Kullanıcı onaylamadı{}",
            if note.is_empty() {
                String::new()
            } else {
                format!(": {}", note)
            }
        )));
    }

    let res = ipotek::takip::finalize(&ctx, &state)
        .await
        .map_err(JobError::Failed)?;

    // Gerçek esas no doğrulaması + DB senkronu
    let hedef_adlar: Vec<String> = parsed
        .get("borclular")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|b| {
                    let ad = b.get("ad").and_then(|x| x.as_str()).unwrap_or("");
                    let soyad = b.get("soyad").and_then(|x| x.as_str()).unwrap_or("");
                    let full = format!("{} {}", ad, soyad).trim().to_string();
                    if full.is_empty() { None } else { Some(full) }
                })
                .collect()
        })
        .unwrap_or_default();

    if let Ok(Some(esas)) = esas_no::bul(&ctx, &hedef_adlar, 2).await {
        ctx.log(&format!("✓ Gerçek esas no: {}", esas));
        let log_cb = |m: &str| ctx.log(m);
        let _ = dosya_kaydet::kaydet(&ctx, &esas, None, Some(&log_cb)).await;
    }

    Ok(res)
}

async fn handle_teblig_212_gonder(ctx: JobContext, params: Value) -> Result<Value, JobError> {
    let dosya_id = params
        .get("dosyaId")
        .and_then(|v| v.as_str())
        .ok_or_else(|| JobError::Failed("dosyaId gerekli.".to_string()))?;
    let taraf_id = params
        .get("tarafId")
        .and_then(|v| v.as_str())
        .ok_or_else(|| JobError::Failed("tarafId gerekli.".to_string()))?;
    let adres = params.get("adres").and_then(|v| v.as_str()).unwrap_or("");
    let aciklama = params
        .get("aciklama")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    let res = teblig_212::hazirla_ve_gonder(&ctx, dosya_id, taraf_id, adres, aciklama)
        .await
        .map_err(JobError::Failed)?;
    Ok(res)
}

async fn handle_xml_takip_ac(ctx: JobContext, params: Value) -> Result<Value, JobError> {
    let xml_b64 = params
        .get("xml_b64")
        .and_then(|v| v.as_str())
        .ok_or_else(|| JobError::Failed("xml_b64 gerekli.".to_string()))?;
    let xml_bytes = base64::engine::general_purpose::STANDARD
        .decode(xml_b64.as_bytes())
        .map_err(|e| JobError::Failed(format!("xml_b64 çözme hatası: {}", e)))?;
    let parsed = xml_takip::parse_xml(&xml_bytes).map_err(JobError::Failed)?;
    let dosyalar = parsed
        .get("dosyalar")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    let (approved, note) = ctx
        .request_approval(
            &format!("{} adet XML takibi açılacak. Onaylıyor musunuz?", dosyalar.len()),
            Some(json!({"adet": dosyalar.len()})),
        )
        .await?;
    if !approved {
        return Err(JobError::Cancelled(format!(
            "Kullanıcı onaylamadı{}",
            if note.is_empty() {
                String::new()
            } else {
                format!(": {}", note)
            }
        )));
    }

    let mut acilan = Vec::new();
    for (idx, d) in dosyalar.iter().enumerate() {
        ctx.check_cancel()?;
        ctx.progress(
            (idx + 1) as i64,
            dosyalar.len() as i64,
            &format!("XML Takip {}/{}", idx + 1, dosyalar.len()),
        );
        let res = xml_takip::prepare_and_finalize(&ctx, d, &params)
            .await
            .map_err(JobError::Failed)?;
        acilan.push(res);
    }
    Ok(json!({"acilan": acilan}))
}

async fn handle_dava_ac(ctx: JobContext, params: Value) -> Result<Value, JobError> {
    let resp = ctx
        .uyap_json("POST", "davaAcilis_brd.ajx", &params)
        .await?;
    Ok(json!({"status": resp.status_code, "body": resp.text}))
}

async fn handle_dosya_sorgula_indir(ctx: JobContext, params: Value) -> Result<Value, JobError> {
    let dosya_id = params
        .get("dosyaId")
        .and_then(|v| v.as_str())
        .ok_or_else(|| JobError::Failed("dosyaId gerekli.".to_string()))?;
    let ad = params
        .get("ad")
        .and_then(|v| v.as_str())
        .unwrap_or("Dosya");
    let ind = crate::evrak_toplu::baslat(
        dosya_id,
        ad,
        crate::evrak_toplu::CIKTI_IKISI,
        crate::evrak_toplu::KAPSAM_TUM,
        None,
    )
    .map_err(JobError::Failed)?;
    Ok(ind.sozluk())
}

async fn handle_sgk_toplu_sorgu(ctx: JobContext, params: Value) -> Result<Value, JobError> {
    let dosya_idleri: Vec<String> = params
        .get("dosyaIdleri")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|x| x.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();
    if dosya_idleri.is_empty() {
        return Err(JobError::Failed("dosyaIdleri boş.".to_string()));
    }
    let sonuclar = mts::sgk::toplu_sgk_sorgula(&ctx, &dosya_idleri)
        .await
        .map_err(JobError::Failed)?;
    let excel_yol = crate::evrak_toplu::kayit_koku().join(format!(
        "SGK_Toplu_Sorgu_{}.xlsx",
        chrono::Local::now().format("%Y%m%d_%H%M%S")
    ));
    mts::sgk_excel::excel_uret(&sonuclar, &excel_yol).map_err(JobError::Failed)?;
    Ok(json!({
        "sonuc_sayisi": sonuclar.len(),
        "excel_yol": excel_yol.to_string_lossy(),
        "sonuclar": sonuclar,
    }))
}

pub fn register_all(mgr: &JobManager) {
    mgr.register_type("bekle", Arc::new(|ctx, p| Box::pin(handle_bekle(ctx, p))));
    mgr.register_type(
        "coklu_takip_ac",
        Arc::new(|ctx, p| Box::pin(handle_coklu_takip_ac(ctx, p))),
    );
    mgr.register_type(
        "mts_odenmis_dosyalari_bul",
        Arc::new(|ctx, p| Box::pin(handle_mts_odenmis_dosyalari_bul(ctx, p))),
    );
    mgr.register_type(
        "mts_odeme_tamamla",
        Arc::new(|ctx, p| Box::pin(handle_mts_odeme_tamamla(ctx, p))),
    );
    mgr.register_type(
        "mts_tebligat_gonder",
        Arc::new(|ctx, p| Box::pin(handle_mts_tebligat_gonder(ctx, p))),
    );
    mgr.register_type(
        "normal_tebligat_gonder",
        Arc::new(|ctx, p| Box::pin(handle_normal_tebligat_gonder(ctx, p))),
    );
    mgr.register_type(
        "ipotek_takip_ac",
        Arc::new(|ctx, p| Box::pin(handle_ipotek_takip_ac(ctx, p))),
    );
    mgr.register_type(
        "teblig_212_gonder",
        Arc::new(|ctx, p| Box::pin(handle_teblig_212_gonder(ctx, p))),
    );
    mgr.register_type(
        "xml_takip_ac",
        Arc::new(|ctx, p| Box::pin(handle_xml_takip_ac(ctx, p))),
    );
    mgr.register_type("dava_ac", Arc::new(|ctx, p| Box::pin(handle_dava_ac(ctx, p))));
    mgr.register_type(
        "dosya_sorgula_indir",
        Arc::new(|ctx, p| Box::pin(handle_dosya_sorgula_indir(ctx, p))),
    );
    mgr.register_type(
        "sgk_toplu_sorgu",
        Arc::new(|ctx, p| Box::pin(handle_sgk_toplu_sorgu(ctx, p))),
    );
}
