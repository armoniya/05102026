//! `uyap_core.ipotek` — İpotek (İlamlı, İpoteğin Paraya Çevrilmesi) modülü
//! (`ipotek/parse.py`, `ipotek/xml_parse.py`, `ipotek/takip.py` 1:1 portu)

use std::collections::HashMap;
use std::io::{Cursor, Read};

use base64::Engine;
use once_cell::sync::Lazy;
use regex::Regex;
use roxmltree::Document;
use serde_json::{json, Value};
use zip::ZipArchive;

use crate::jobs::JobContext;
use crate::mts::evrak::{items_kur, mime_belirle};
use crate::mts::models::format_date_with_slashes;
use crate::udf_signer;
use crate::uyap_proxy;

pub use takip::{_guvenli_liste, _temiz_buyuk};

// ══════════════════════════════════════════════════════════════════════════════
// parse.py — Excel (.xlsx) okuyucu
// ══════════════════════════════════════════════════════════════════════════════
pub mod parse {
    use super::*;

    pub const _ASIL_ALACAK: &str = "asıl alacak";
    pub const _GECMIS_GUN_FAIZI: &str = "geçmiş gün faizi";
    pub const _BSMV: &str = "bsmv";
    pub const _TAZMINAT: &str = "tazminat";

    pub fn _temiz_str(val: &str) -> String {
        val.trim().to_string()
    }

    pub fn _tutar_oku(ham: &str, alan_adi: &str) -> Result<f64, String> {
        let mut s = ham.trim().replace("TL", "").replace("tl", "").replace('₺', "");
        s = s.trim().replace(' ', "").replace('\u{00a0}', "");
        if s.is_empty() {
            return Ok(0.0);
        }
        if s.contains(',') && s.contains('.') {
            if s.rfind(',') > s.rfind('.') {
                s = s.replace('.', "").replace(',', ".");
            } else {
                s = s.replace(',', "");
            }
        } else if s.contains(',') {
            let parcalar: Vec<&str> = s.split(',').collect();
            if parcalar.len() == 2 && parcalar[1].len() <= 2 {
                s = s.replace(',', ".");
            } else if parcalar.len() > 2 || (parcalar.len() == 2 && parcalar[1].len() == 3) {
                s = s.replace(',', "");
            } else {
                s = s.replace(',', ".");
            }
        }
        let val = s
            .parse::<f64>()
            .map_err(|_| format!("'{}' değeri sayıya çevrilemedi: {:?}", alan_adi, ham))?;
        Ok((val * 100.0).round() / 100.0)
    }

    pub fn _faiz_orani_oku(ham: &str) -> Result<f64, String> {
        let mut s = ham.trim().replace('%', "").replace(',', ".").trim().to_string();
        if s.is_empty() {
            return Ok(0.0);
        }
        s.parse::<f64>()
            .map_err(|_| format!("Faiz oranı okunamadı: {:?}", ham))
    }

    pub fn _tarih_oku(ham: &str) -> String {
        let s = ham.trim();
        if s.is_empty() {
            return String::new();
        }
        // Excel seri tarih sayısıysa (ör. 45000) YYYY-AA-GG -> GG.AA.YYYY'ye çevir
        if let Ok(serial) = s.parse::<f64>() {
            if serial > 20000.0 && serial < 90000.0 {
                let days = serial.trunc() as i64 - 25569;
                if let Some(dt) = chrono::DateTime::from_timestamp(days * 86400, 0) {
                    return dt.format("%d.%m.%Y").to_string();
                }
            }
        }
        format_date_with_slashes(s)
    }

    fn col_index_from_ref(cell_ref: &str) -> usize {
        let mut col = 0usize;
        for ch in cell_ref.chars() {
            if ch.is_ascii_alphabetic() {
                col = col * 26 + ((ch.to_ascii_uppercase() as u8 - b'A') as usize + 1);
            } else {
                break;
            }
        }
        col.saturating_sub(1)
    }

    pub fn read_xlsx_rows(xlsx_bytes: &[u8]) -> Result<Vec<Vec<String>>, String> {
        let mut archive = ZipArchive::new(Cursor::new(xlsx_bytes))
            .map_err(|e| format!("Excel (.xlsx) açılamadı: {}", e))?;

        let mut shared_strings: Vec<String> = Vec::new();
        if let Ok(mut ss_file) = archive.by_name("xl/sharedStrings.xml") {
            let mut ss_xml = String::new();
            let _ = ss_file.read_to_string(&mut ss_xml);
            if let Ok(doc) = Document::parse(&ss_xml) {
                for si in doc.descendants().filter(|n| n.has_tag_name("si")) {
                    let mut text = String::new();
                    for t in si.descendants().filter(|n| n.has_tag_name("t")) {
                        if let Some(s) = t.text() {
                            text.push_str(s);
                        }
                    }
                    shared_strings.push(text);
                }
            }
        }

        let mut sheet_xml = String::new();
        let sheet_names = ["xl/worksheets/sheet1.xml", "xl/worksheets/Sheet1.xml"];
        let mut found = false;
        for sname in sheet_names {
            if let Ok(mut f) = archive.by_name(sname) {
                f.read_to_string(&mut sheet_xml)
                    .map_err(|e| format!("Sayfa okunamadı: {}", e))?;
                found = true;
                break;
            }
        }
        if !found {
            for i in 0..archive.len() {
                if let Ok(mut f) = archive.by_index(i) {
                    if f.name().starts_with("xl/worksheets/sheet") && f.name().ends_with(".xml") {
                        f.read_to_string(&mut sheet_xml)
                            .map_err(|e| format!("Sayfa okunamadı: {}", e))?;
                        found = true;
                        break;
                    }
                }
            }
        }
        if !found {
            return Err("Excel içinde çalışma sayfası bulunamadı.".to_string());
        }

        let doc = Document::parse(&sheet_xml)
            .map_err(|e| format!("Excel XML ayrıştırma hatası: {}", e))?;

        let mut rows_out: Vec<Vec<String>> = Vec::new();
        for row_node in doc.descendants().filter(|n| n.has_tag_name("row")) {
            let mut row_cells: Vec<String> = Vec::new();
            for c_node in row_node.children().filter(|n| n.has_tag_name("c")) {
                let r_attr = c_node.attribute("r").unwrap_or("");
                let col_idx = if r_attr.is_empty() {
                    row_cells.len()
                } else {
                    col_index_from_ref(r_attr)
                };
                while row_cells.len() <= col_idx {
                    row_cells.push(String::new());
                }
                let t_attr = c_node.attribute("t").unwrap_or("");
                let mut val = String::new();
                if t_attr == "inlineStr" {
                    for t in c_node.descendants().filter(|n| n.has_tag_name("t")) {
                        if let Some(s) = t.text() {
                            val.push_str(s);
                        }
                    }
                } else if let Some(v_node) = c_node.children().find(|n| n.has_tag_name("v")) {
                    let raw = v_node.text().unwrap_or("");
                    if t_attr == "s" {
                        if let Ok(idx) = raw.trim().parse::<usize>() {
                            val = shared_strings.get(idx).cloned().unwrap_or_default();
                        }
                    } else {
                        val = raw.to_string();
                    }
                }
                row_cells[col_idx] = val;
            }
            rows_out.push(row_cells);
        }
        Ok(rows_out)
    }

    /// Excel dosyasını okuyup `{borclu_tckn_ler, alacak_kalemleri, takip_tarihi, ...}` döndürür.
    pub fn excel_oku(xlsx_bytes: &[u8]) -> Result<Value, String> {
        let rows = read_xlsx_rows(xlsx_bytes)?;
        if rows.len() < 2 {
            return Err("Excel dosyasında veri satırı bulunamadı.".to_string());
        }

        let mut borclular: Vec<String> = Vec::new();
        let mut takip_tarihi = String::new();
        let mut asil_alacak = 0.0f64;
        let mut gecmis_gun_faizi = 0.0f64;
        let mut bsmv = 0.0f64;
        let mut faiz_orani = 0.0f64;
        let mut faiz_baslangic_tarihi = String::new();

        for row in rows.iter().skip(1) {
            let get = |i: usize| row.get(i).map(|s| s.trim()).unwrap_or("");
            let a = get(0);
            let b = get(1);
            let c = get(2);
            let d = get(3);
            let e = get(4);
            let f = get(5);
            let g = get(6);

            if a.is_empty() && b.is_empty() && c.is_empty() && d.is_empty() && e.is_empty() {
                continue;
            }

            if !a.is_empty() {
                let digits: String = a.chars().filter(|ch| ch.is_ascii_digit()).collect();
                if digits.len() == 11 && !borclular.contains(&digits) {
                    borclular.push(digits);
                }
            }
            if takip_tarihi.is_empty() && !b.is_empty() {
                takip_tarihi = _tarih_oku(b);
            }

            let kalem_turu = c.to_lowercase();
            let tutar = if !d.is_empty() {
                _tutar_oku(d, "Tutar")?
            } else {
                0.0
            };

            if kalem_turu.contains(_ASIL_ALACAK) || kalem_turu.contains("asil alacak") {
                asil_alacak += tutar;
                if !e.is_empty() && faiz_orani == 0.0 {
                    faiz_orani = _faiz_orani_oku(e)?;
                }
                if !f.is_empty() && faiz_baslangic_tarihi.is_empty() {
                    faiz_baslangic_tarihi = _tarih_oku(f);
                }
            } else if kalem_turu.contains(_GECMIS_GUN_FAIZI)
                || kalem_turu.contains("gecmis gun faizi")
                || kalem_turu.contains("faiz")
            {
                gecmis_gun_faizi += tutar;
            } else if kalem_turu.contains(_BSMV) {
                bsmv += tutar;
            } else if !g.is_empty() {
                asil_alacak += _tutar_oku(g, "Tutar")?;
            }
        }

        if borclular.is_empty() {
            return Err("Excel içinde geçerli borçlu TCKN bulunamadı.".to_string());
        }

        Ok(json!({
            "kaynak": "excel",
            "borclu_tckn_ler": borclular,
            "takip_tarihi": takip_tarihi,
            "asil_alacak": (asil_alacak * 100.0).round() / 100.0,
            "gecmis_gun_faizi": (gecmis_gun_faizi * 100.0).round() / 100.0,
            "bsmv": (bsmv * 100.0).round() / 100.0,
            "faiz_orani": faiz_orani,
            "faiz_baslangic_tarihi": faiz_baslangic_tarihi,
        }))
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// xml_parse.py — İpotek XML okuyucu
// ══════════════════════════════════════════════════════════════════════════════
pub mod xml_parse {
    use super::*;

    fn attr<'a>(node: roxmltree::Node<'a, 'a>, name: &str) -> String {
        node.attribute(name).unwrap_or("").trim().to_string()
    }

    pub fn _tutar(s: &str) -> Result<f64, String> {
        let mut clean = s.trim().replace(' ', "");
        if clean.is_empty() {
            return Ok(0.0);
        }
        if clean.contains(',') && clean.contains('.') {
            if clean.rfind(',') > clean.rfind('.') {
                clean = clean.replace('.', "").replace(',', ".");
            } else {
                clean = clean.replace(',', "");
            }
        } else if clean.contains(',') {
            clean = clean.replace(',', ".");
        }
        clean
            .parse::<f64>()
            .map(|v| (v * 100.0).round() / 100.0)
            .map_err(|_| format!("XML tutar ayrıştırılamadı: {:?}", s))
    }

    pub fn xml_ayristir(xml_bytes: &[u8]) -> Result<Value, String> {
        let text = String::from_utf8_lossy(xml_bytes);
        let doc =
            Document::parse(&text).map_err(|e| format!("XML ayrıştırma hatası: {}", e))?;

        let mut alacaklilar = Vec::new();
        let mut borclular = Vec::new();
        let mut kalemler = Vec::new();
        let mut takip_sekli = String::new();
        let mut takip_yolu = String::new();
        let mut takip_tarihi = String::new();

        for node in doc.descendants() {
            match node.tag_name().name() {
                "dosya" => {
                    takip_sekli = attr(node, "takipSekli");
                    takip_yolu = attr(node, "takipYolu");
                    takip_tarihi = attr(node, "takipTarihi");
                }
                "taraf" => {
                    let mut kisi = json!({});
                    let mut rol = String::new();
                    for ch in node.children().filter(|n| n.is_element()) {
                        match ch.tag_name().name() {
                            "rolTur" => rol = attr(ch, "rol"),
                            "kisiKurumBilgileri" => {
                                if let Some(kisi_node) =
                                    ch.children().find(|n| n.has_tag_name("kisiTumBilgileri"))
                                {
                                    kisi = json!({
                                        "tur": "gercek",
                                        "tckn": attr(kisi_node, "tcKimlikNo"),
                                        "ad": attr(kisi_node, "adi"),
                                        "soyad": attr(kisi_node, "soyadi"),
                                        "babaAdi": attr(kisi_node, "babaAdi"),
                                        "dogumTarihi": attr(kisi_node, "dogumTarihi"),
                                    });
                                } else if let Some(kurum_node) =
                                    ch.children().find(|n| n.has_tag_name("kurum"))
                                {
                                    kisi = json!({
                                        "tur": "tuzel",
                                        "unvan": attr(kurum_node, "kurumAdi"),
                                        "vergiNo": attr(kurum_node, "vergiNo"),
                                        "mersisNo": attr(kurum_node, "mersisNo"),
                                    });
                                }
                            }
                            _ => {}
                        }
                    }
                    if rol == "1" || rol.eq_ignore_ascii_case("alacakli") {
                        alacaklilar.push(kisi);
                    } else if rol == "2" || rol.eq_ignore_ascii_case("borclu") {
                        borclular.push(kisi);
                    }
                }
                "alacakKalemi" => {
                    let tutar = _tutar(&attr(node, "alacakKalemTutar"))?;
                    let oran_str = attr(node, "faizOrani");
                    let faiz_orani = if oran_str.is_empty() {
                        0.0
                    } else {
                        _tutar(&oran_str).unwrap_or(0.0)
                    };
                    kalemler.push(json!({
                        "kod": attr(node, "alacakKalemKod"),
                        "kodAciklama": attr(node, "alacakKalemKodAciklama"),
                        "aciklama": attr(node, "alacakKalemAdi"),
                        "tutar": tutar,
                        "faizOrani": faiz_orani,
                        "baslangicTarihi": attr(node, "faizBaslangicTarihi"),
                    }));
                }
                _ => {}
            }
        }

        if borclular.is_empty() {
            return Err("XML içinde borçlu bulunamadı.".to_string());
        }

        Ok(json!({
            "kaynak": "xml",
            "takipSekli": takip_sekli,
            "takipYolu": takip_yolu,
            "takipTarihi": format_date_with_slashes(&takip_tarihi),
            "alacaklilar": alacaklilar,
            "borclular": borclular,
            "kalemler": kalemler,
        }))
    }
}

// ══════════════════════════════════════════════════════════════════════════════
// takip.py — İpotek icra takibi açma akışı
// ══════════════════════════════════════════════════════════════════════════════
pub mod takip {
    use super::*;

    pub const IL_VARSAYILAN: &str = "İzmir";
    pub const ADLIYE_VARSAYILAN: &str = "İzmir";

    pub fn _belge_coz(d: &Value) -> Option<(String, Vec<u8>)> {
        let obj = d.as_object()?;
        let b64 = obj.get("b64")?.as_str()?;
        if b64.is_empty() {
            return None;
        }
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(b64.as_bytes())
            .ok()?;
        let fname = obj
            .get("filename")
            .and_then(|v| v.as_str())
            .unwrap_or("belge.pdf")
            .to_string();
        Some((fname, bytes))
    }

    pub fn _temiz_buyuk(s: &str) -> String {
        let up = s
            .replace('i', "İ")
            .replace('ı', "I")
            .to_uppercase();
        let re_ws = Regex::new(r"\s+").unwrap();
        re_ws.replace_all(up.trim(), " ").to_string()
    }

    pub fn _guvenli_liste(veri: &Value) -> Vec<Value> {
        if let Some(arr) = veri.as_array() {
            if let Some(first) = arr.first().and_then(|x| x.as_array()) {
                return first.iter().filter(|x| x.is_object()).cloned().collect();
            }
            return arr.iter().filter(|x| x.is_object()).cloned().collect();
        }
        if let Some(obj) = veri.as_object() {
            for k in ["data", "veri", "list", "rows", "result"] {
                if let Some(arr) = obj.get(k).and_then(|x| x.as_array()) {
                    return arr.iter().filter(|x| x.is_object()).cloned().collect();
                }
            }
        }
        Vec::new()
    }

    pub async fn _api_text(
        ctx: &JobContext,
        path: &str,
        payload: Option<&Value>,
        multipart: bool,
    ) -> Result<String, String> {
        let resp = if multipart {
            let mut fields = Vec::new();
            if let Some(obj) = payload.and_then(|v| v.as_object()) {
                for (k, v) in obj {
                    let s = match v {
                        Value::String(st) => st.clone(),
                        Value::Object(_) | Value::Array(_) => {
                            serde_json::to_string(v).unwrap_or_default()
                        }
                        _ => v.to_string(),
                    };
                    fields.push((k.clone(), None, s.into_bytes(), None));
                }
            }
            ctx.uyap_post_multipart(path, fields)
                .await
                .map_err(|e| e.to_string())?
        } else {
            let body = payload.cloned().unwrap_or_else(|| json!({}));
            ctx.uyap_json("POST", path, &body)
                .await
                .map_err(|e| e.to_string())?
        };
        if resp.status_code >= 400 {
            return Err(format!("UYAP '{}' HTTP {} döndürdü.", path, resp.status_code));
        }
        Ok(resp.text)
    }

    pub async fn _api_json(
        ctx: &JobContext,
        path: &str,
        payload: Option<&Value>,
        multipart: bool,
    ) -> Result<Value, String> {
        let txt = _api_text(ctx, path, payload, multipart).await?;
        serde_json::from_str(&txt)
            .map_err(|e| format!("UYAP '{}' JSON ayrıştırma hatası: {}", path, e))
    }

    pub async fn prepare(
        ctx: &JobContext,
        parsed: &Value,
        params: &Value,
    ) -> Result<(Value, Value), String> {
        let log = |m: &str| ctx.log(m);

        let vekalet = params
            .get("vekalet")
            .and_then(_belge_coz)
            .ok_or_else(|| "Vekaletname PDF/UDF belgesi zorunludur.".to_string())?;
        let mut dayanak_listesi = Vec::new();
        if let Some(arr) = params.get("dayanak_listesi").and_then(|v| v.as_array()) {
            for item in arr {
                if let Some(b) = _belge_coz(item) {
                    dayanak_listesi.push(b);
                }
            }
        }
        if dayanak_listesi.is_empty() {
            return Err("En az 1 adet dayanak belgesi zorunludur.".to_string());
        }

        let il_adi = params
            .get("il")
            .and_then(|v| v.as_str())
            .filter(|s| !s.trim().is_empty())
            .unwrap_or(IL_VARSAYILAN);
        let adliye_adi = params
            .get("adliye")
            .and_then(|v| v.as_str())
            .filter(|s| !s.trim().is_empty())
            .unwrap_or(ADLIYE_VARSAYILAN);

        log("İller ve adliyeler sorgulanıyor...");
        let iller = _guvenli_liste(&_api_json(ctx, "illerIlcelerGetir.ajx", None, false).await?);
        let il_hedef = _temiz_buyuk(il_adi);
        let il_kod = iller
            .iter()
            .find(|x| {
                _temiz_buyuk(x.get("ilAdi").and_then(|v| v.as_str()).unwrap_or("")) == il_hedef
            })
            .and_then(|x| x.get("ilKodu").cloned())
            .unwrap_or_else(|| json!(35));

        let adliyeler = _guvenli_liste(
            &_api_json(
                ctx,
                "getBirimlerByIlKodu.ajx",
                Some(&json!({"ilKodu": il_kod})),
                false,
            )
            .await?,
        );
        let adl_hedef = _temiz_buyuk(adliye_adi);
        let adliye_obj = adliyeler
            .iter()
            .find(|x| {
                _temiz_buyuk(x.get("birimAdi").and_then(|v| v.as_str()).unwrap_or(""))
                    .contains(&adl_hedef)
            })
            .cloned()
            .unwrap_or_else(|| adliyeler.first().cloned().unwrap_or_else(|| json!({})));

        log("Kurum/banka bilgileri ve IBAN çekiliyor...");
        let kurum_v = _api_json(ctx, "kurumBilgisiSorgula_brd.ajx", None, false)
            .await
            .unwrap_or_else(|_| json!({}));
        let kurum = kurum_v.as_object().cloned().unwrap_or_default();
        let iban_numarasi = params
            .get("iban")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        let mut kisi_taraflari = Vec::new();
        let mut taraf_list = Vec::new();

        if let Some(tckns) = parsed.get("borclu_tckn_ler").and_then(|v| v.as_array()) {
            for t in tckns {
                if let Some(tckn) = t.as_str() {
                    log(&format!("MERNİS sorgusu: {}", tckn));
                    let mernis = _api_json(
                        ctx,
                        "mernisKisiSorgula.ajx",
                        Some(&json!({"tcKimlikNo": tckn})),
                        false,
                    )
                    .await
                    .unwrap_or_else(|_| json!({}));
                    let ad = mernis
                        .get("adi")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    let soyad = mernis
                        .get("soyadi")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    let borclu_obj = json!({
                        "temelBilgileri": {
                            "tcKimlikNo": tckn,
                            "adi": ad,
                            "soyadi": soyad,
                        }
                    });
                    kisi_taraflari.push(borclu_obj.clone());
                    taraf_list.push(json!({"rol": 2, "kisi": borclu_obj}));
                }
            }
        } else if let Some(borclular) = parsed.get("borclular").and_then(|v| v.as_array()) {
            for b in borclular {
                let tckn = b.get("tckn").and_then(|v| v.as_str()).unwrap_or("");
                let ad = b.get("ad").and_then(|v| v.as_str()).unwrap_or("");
                let soyad = b.get("soyad").and_then(|v| v.as_str()).unwrap_or("");
                let borclu_obj = json!({
                    "temelBilgileri": {
                        "tcKimlikNo": tckn,
                        "adi": ad,
                        "soyad": soyad,
                    }
                });
                kisi_taraflari.push(borclu_obj.clone());
                taraf_list.push(json!({"rol": 2, "kisi": borclu_obj}));
            }
        }

        let asil_alacak = parsed
            .get("asil_alacak")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0);
        let faiz = parsed
            .get("gecmis_gun_faizi")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0);
        let bsmv = parsed.get("bsmv").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let faiz_orani = parsed
            .get("faiz_orani")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0);

        let mut alacak_kalemleri = Vec::new();
        if asil_alacak > 0.0 {
            alacak_kalemleri.push(json!({
                "temelBilgileri": {
                    "alacakKalemKod": 7172,
                    "alacakKalemKodAciklama": "Asıl Alacak",
                    "aciklama": "Asıl Alacak",
                    "alacakTutariTL": asil_alacak,
                },
                "faizBilgileri": if faiz_orani > 0.0 {
                    json!({"faizOrani": faiz_orani})
                } else {
                    Value::Null
                }
            }));
        }
        if faiz > 0.0 {
            alacak_kalemleri.push(json!({
                "temelBilgileri": {
                    "alacakKalemKod": 7182,
                    "alacakKalemKodAciklama": "Geçmiş Gün Faizi",
                    "aciklama": "Faiz",
                    "alacakTutariTL": faiz,
                }
            }));
        }
        if bsmv > 0.0 {
            alacak_kalemleri.push(json!({
                "temelBilgileri": {
                    "alacakKalemKod": 7178,
                    "alacakKalemKodAciklama": "BSMV",
                    "aciklama": "BSMV",
                    "alacakTutariTL": bsmv,
                }
            }));
        }

        if let Some(xml_kalemler) = parsed.get("kalemler").and_then(|v| v.as_array()) {
            if alacak_kalemleri.is_empty() {
                for xk in xml_kalemler {
                    let tutar = xk.get("tutar").and_then(|v| v.as_f64()).unwrap_or(0.0);
                    let aciklama = xk
                        .get("aciklama")
                        .and_then(|v| v.as_str())
                        .unwrap_or("Alacak");
                    let kod_aciklama = xk
                        .get("kodAciklama")
                        .and_then(|v| v.as_str())
                        .unwrap_or(aciklama);
                    let oran = xk.get("faizOrani").and_then(|v| v.as_f64()).unwrap_or(0.0);
                    alacak_kalemleri.push(json!({
                        "temelBilgileri": {
                            "alacakKalemKod": xk.get("kod").and_then(|v| v.as_str()).unwrap_or("7172"),
                            "alacakKalemKodAciklama": kod_aciklama,
                            "aciklama": aciklama,
                            "alacakTutariTL": tutar,
                        },
                        "faizBilgileri": if oran > 0.0 { json!({"faizOrani": oran}) } else { Value::Null }
                    }));
                }
            }
        }

        let toplam_alacak: f64 = alacak_kalemleri
            .iter()
            .filter_map(|k| {
                k.get("temelBilgileri")
                    .and_then(|tb| tb.get("alacakTutariTL"))
                    .and_then(|t| t.as_f64())
            })
            .sum();
        let toplam_alacak = (toplam_alacak * 100.0).round() / 100.0;

        log("Harç hesabı yapılıyor...");
        let harc_resp = _api_json(
            ctx,
            "icra_takip_harc_hesapla.ajx",
            Some(&json!({"alacakTutari": toplam_alacak, "takipTuru": 1})),
            false,
        )
        .await
        .unwrap_or_else(|_| json!({}));
        let harc_listesi = _guvenli_liste(&harc_resp);
        let harc_toplam: f64 = harc_listesi
            .iter()
            .filter_map(|h| h.get("hesapMiktar").and_then(|v| v.as_f64()))
            .sum();
        let harc_toplam = (harc_toplam * 100.0).round() / 100.0;

        let kalemler_ozet: Vec<Value> = alacak_kalemleri
            .iter()
            .map(|k| {
                let tb = &k["temelBilgileri"];
                let mut row = json!({
                    "ad": tb["aciklama"],
                    "tutar": tb["alacakTutariTL"],
                });
                if let Some(oran) = k
                    .get("faizBilgileri")
                    .and_then(|fb| fb.get("faizOrani"))
                    .and_then(|v| v.as_f64())
                {
                    row["faiz_orani"] = json!(oran);
                }
                row
            })
            .collect();

        let harc_ozet: Vec<Value> = harc_listesi
            .iter()
            .map(|h| {
                json!({
                    "ad": h.get("harcMasrafAdi").and_then(|v| v.as_str()).unwrap_or("Masraf"),
                    "miktar": h.get("hesapMiktar").and_then(|v| v.as_f64()).unwrap_or(0.0),
                })
            })
            .collect();

        let borclular_ozet: Vec<Value> = kisi_taraflari
            .iter()
            .map(|b| {
                let tb = &b["temelBilgileri"];
                json!({
                    "tckn": tb.get("tcKimlikNo"),
                    "ad": tb.get("adi"),
                    "soyad": tb.get("soyadi"),
                })
            })
            .collect();

        let ozet = json!({
            "alacakli": kurum.get("kurumAdi"),
            "mersis_no": kurum.get("mersisNo"),
            "iban": iban_numarasi,
            "borclular": borclular_ozet,
            "kalemler": kalemler_ozet,
            "toplam_alacak": toplam_alacak,
            "harclar": harc_ozet,
            "harc_toplam": harc_toplam,
        });

        let state = json!({
            "taraf_list": taraf_list,
            "ilamli_list": alacak_kalemleri,
            "adliye": adliye_obj,
            "iban": iban_numarasi,
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
        let ilamli_list = state.get("ilamli_list").cloned().unwrap_or_else(|| json!([]));
        let adliye = state.get("adliye").cloned().unwrap_or_else(|| json!({}));

        log("Tevzi numarası alınıyor — bu adımdan sonra dosya UYAP'ta GERÇEKTEN açılmış olacak...");
        let tevzi_sonuc = _api_json(
            ctx,
            "icra_takip_tevzi_islemleri.ajx",
            Some(&json!({
                "IcraDosyaBilgileri": serde_json::to_string(&json!({
                    "birimId": adliye.get("birimId"),
                    "kotaKullanimSekli": 0,
                })).unwrap_or_default(),
                "TarafList": serde_json::to_string(&taraf_list).unwrap_or_default(),
                "IlamliList": serde_json::to_string(&ilamli_list).unwrap_or_default(),
                "IlamsizList": [],
                "TahsilatList": [],
            })),
            false,
        )
        .await?;

        let mut dosya_id = tevzi_sonuc
            .get("dosyaId")
            .and_then(|v| v.as_str())
            .ok_or_else(|| format!("UYAP tevzi numarası döndürmedi. Yanıt: {}", tevzi_sonuc))?
            .trim()
            .trim_matches('"')
            .trim_matches('\'')
            .to_string();
        if dosya_id.is_empty() {
            dosya_id = tevzi_sonuc
                .get("dosyaId")
                .map(|v| v.to_string())
                .unwrap_or_default();
        }
        log(&format!("Tevzi tamamlandı. Dosya ID: {}", dosya_id));

        let _ = _api_json(
            ctx,
            "tamamlanmayanDosyalar_brd.ajx",
            Some(&json!({"dosyaTurKod": 35})),
            false,
        )
        .await;

        let q = format!("dosyaId={}", urlencoding::encode(&dosya_id));
        let resp = ctx
            .uyap_get("icraTakipTalebiIndir.uyap", Some(&q))
            .await
            .map_err(|e| e.to_string())?;
        if resp.status_code >= 400 {
            return Err(format!(
                "Takip talebi indirilemedi (HTTP {}). Dosya UYAP'ta açık kaldı (ID: {}) — 'Tamamlanmayan Dosyalar' ekranından elle devam edin.",
                resp.status_code, dosya_id
            ));
        }
        let udf_bytes = resp.body;
        if udf_bytes.is_empty() {
            return Err(format!(
                "Takip talebi taslağı boş indi (Dosya ID: {}).",
                dosya_id
            ));
        }

        let guvenli_isim = "Takip_Talebi.udf";
        log("Takip talebi e-imzalanıyor (headless)...");
        let cert_id = gw.cert_id.lock().clone();
        let pin = gw.login_args.pin.clone().unwrap_or_default();
        let signed = tokio::task::spawn_blocking(move || {
            udf_signer::sign_document(&udf_bytes, guvenli_isim, cert_id.as_deref(), &pin)
                .map_err(|e| e.0)
        })
        .await
        .map_err(|e| e.to_string())??;
        log(&format!("İmzalandı ({} bayt).", signed.len()));

        let vekalet = state
            .get("vekalet")
            .and_then(_belge_coz)
            .ok_or_else(|| "Vekaletname bulunamadı.".to_string())?;
        let dayanak_arr = state
            .get("dayanak_listesi")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();

        let mut evraklar: Vec<(String, String, Vec<u8>)> = vec![
            (
                "ICR_TAKIP_TLP".to_string(),
                guvenli_isim.to_string(),
                signed,
            ),
            ("CZM_VEKALETNAME".to_string(), vekalet.0, vekalet.1),
        ];
        for d in &dayanak_arr {
            if let Some((fnm, b)) = _belge_coz(d) {
                evraklar.push(("MTS_TAKIBIN_DAYANAGI".to_string(), fnm, b));
            }
        }

        let evrak_meta: Vec<(&str, &str)> = evraklar
            .iter()
            .map(|(t, f, _)| (t.as_str(), f.as_str()))
            .collect();
        let (items_json, alanlar) = items_kur(&evrak_meta);

        let mut fields: Vec<(String, Option<String>, Vec<u8>, Option<String>)> = Vec::new();
        for ((alan, fname), (_, _, bytes)) in alanlar.iter().zip(evraklar.iter()) {
            let mime = mime_belirle(fname).to_string();
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

        log(&format!("Evrak gönderiliyor ({} adet)...", alanlar.len()));
        let ev_resp = ctx
            .uyap_post_multipart("davaAcilisEvrakGonderme_brd.ajx", fields)
            .await
            .map_err(|e| e.to_string())?;
        let sonuc: Value = serde_json::from_str(&ev_resp.text)
            .unwrap_or_else(|_| json!({"type": "unknown", "message": ev_resp.text}));
        if sonuc.get("type").and_then(|v| v.as_str()) != Some("success") {
            return Err(format!(
                "Evrak gönderme başarısız (Dosya ID: {}). UYAP yanıtı: {} — 'Tamamlanmayan Dosyalar' ekranından elle devam edin.",
                dosya_id, sonuc
            ));
        }
        log(&format!(
            "✓ Evrak gönderildi: {}",
            sonuc.get("message").and_then(|v| v.as_str()).unwrap_or("")
        ));

        let _ = _api_json(
            ctx,
            "tamamlanmayanDosyalar_brd.ajx",
            Some(&json!({"dosyaTurKod": 35})),
            false,
        )
        .await;

        Ok(json!({"dosya_id": dosya_id, "evrak_sonuc": sonuc}))
    }
}
