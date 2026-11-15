//! /zs İş Takip — veri katmanı (zs_store.rs, 1:1 port of zs_store.py)
//!
//! Ayrı kullanıcı sistemi + iş/alt iş + not + belge kaydı + olay geçmişi.
//! Kalıcılık: DATABASE_URL varsa PostgreSQL (`zs_` önekli AYRI tablolar);
//! yoksa yerel SQLite (`zs.sqlite3`).

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use chrono::{NaiveDate, TimeZone, Utc};
use postgres::{Client as PgClient, NoTls};
use rand::RngCore;
use regex::Regex;
use rusqlite::{params_from_iter, types::Value as SqliteValue, Connection};
use serde_json::{json, Map, Value};

use crate::accounts::{self, hash_password_str as hash_password, slugify, verify_password_str as verify_password, AccountStore};

pub const ROLES: &[&str] = &["admin", "user"];
pub const STATUSES: &[&str] = &["acik", "devam", "onayda", "onaylandi", "tamamlandi"];
pub const DONE_STATUSES: &[&str] = &["onaylandi", "tamamlandi"];
pub const SUB_STATUSES: &[&str] = &["acik", "tamam"];
pub const PRIORITIES: &[i64] = &[0, 1, 2];

pub const DOSYA_FIELDS: &[&str] = &[
    "dosya_id",
    "birim_id",
    "birim_adi",
    "dosya_no",
    "dosya_tur",
    "dosya_uyap",
    "evrak_id",
    "evrak_adi",
    "taraf_adi",
    "barkod",
];

pub const UYAP_KART_ALANLARI: &[&str] = &[
    "dosyaId",
    "dosyaNo",
    "birimId",
    "birimAdi",
    "dosyaTurKod",
    "dosyaTurKodAciklama",
    "dosyaDurum",
    "dosyaDurumKod",
    "birimTuru3",
];

pub const TEMPLATE_TARAF: &[&str] = &["borclu", "alacakli", "herhangi"];
pub const TEMPLATE_SONUCLAR: &[&str] = &["olumlu", "olumsuz", "bekliyor"];
pub const ETIKET_RENKLERI: &[&str] = &[
    "#2563eb", "#7c3aed", "#dc2626", "#059669", "#d97706", "#e11d48", "#0891b2", "#4b5563",
];
pub const ETIKET_DERINLIK: usize = 3;
pub const KESINLESME_SABLONU: &str = "h-kes-tarih";

#[derive(Debug, Clone)]
pub struct ZsError(pub String);

impl std::fmt::Display for ZsError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::error::Error for ZsError {}

pub type ZsResult<T> = Result<T, ZsError>;

#[derive(Debug, Clone)]
pub enum Patch<T> {
    Keep,
    Set(T),
}

impl<T> Default for Patch<T> {
    fn default() -> Self {
        Patch::Keep
    }
}

pub fn now_ts() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

pub fn gun_sonra(gun: i64, saat: i64) -> i64 {
    let tr = 3 * 3600;
    let gece = (now_ts() + tr).div_euclid(86400) * 86400 - tr;
    gece + gun * 86400 + saat * 3600
}

pub fn tarih_17(s: &str, saat: i64) -> ZsResult<i64> {
    let trimmed = s.trim();
    let prefix: String = trimmed.chars().take(10).collect();
    let gun = NaiveDate::parse_from_str(&prefix, "%Y-%m-%d")
        .map_err(|_| ZsError("Kesinleşme günü geçersiz.".to_string()))?;
    let y = chrono::Datelike::year(&gun);
    if !(2000..=2100).contains(&y) {
        return Err(ZsError("Kesinleşme günü geçersiz.".to_string()));
    }
    let epoch = NaiveDate::from_ymd_opt(1970, 1, 1).unwrap();
    let days = (gun - epoch).num_days();
    Ok(days * 86400 + (saat - 3) * 3600)
}

pub fn tg_uname(v: Option<&str>) -> Option<String> {
    let s = v.unwrap_or("").trim().trim_start_matches('@').to_lowercase();
    let truncated: String = s.chars().take(64).collect();
    if truncated.is_empty() {
        None
    } else {
        Some(truncated)
    }
}

pub fn tr_lower(s: &str) -> String {
    s.replace('İ', "i").replace('I', "ı").to_lowercase()
}

pub fn ad_key(s: &str) -> String {
    let lowered = s.replace('İ', "i").replace('I', "i").to_lowercase().replace('ı', "i");
    lowered.split_whitespace().collect::<Vec<_>>().join(" ")
}

pub fn is_adi(title: &str) -> String {
    if let Some(i) = title.rfind(" – ") {
        title[i + " – ".len()..].to_string()
    } else {
        title.to_string()
    }
}

pub fn office_of(user: Option<&Value>) -> String {
    user.and_then(|u| u.get("office_id"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

pub fn sqlite_path() -> PathBuf {
    accounts::data_dir().join("zs.sqlite3")
}

pub fn dosya_turleri() -> &'static [(&'static str, &'static str, &'static str, &'static str)] {
    &[
        ("arabuluculuk", "Arabuluculuk", "#0d9488", r"arabulucu"),
        ("icra", "İcra", "#4f46e5", r"icra dosya|icra dairesi|icra müdürlüğü|^icra"),
        ("cbs", "CBS", "#b45309", r"başsavcı|savcılı|soruşturma"),
        ("ceza", "Ceza", "#be123c", r"ceza"),
        ("idare", "İdare", "#0369a1", r"idare|vergi mahkeme|danıştay"),
        ("hukuk", "Hukuk", "#15803d", r"hukuk|asliye|sulh|aile|iş mahkeme|tüketici|ticaret|kadastro"),
    ]
}

pub fn dosya_turu(dosya_tur: Option<&str>, birim_adi: Option<&str>) -> Option<String> {
    let dt = tr_lower(dosya_tur.unwrap_or("")).trim().to_string();
    let ba = tr_lower(birim_adi.unwrap_or("")).trim().to_string();
    for metin in [dt, ba] {
        if !metin.is_empty() {
            for (key, _, _, desen) in dosya_turleri() {
                if let Ok(re) = Regex::new(desen) {
                    if re.is_match(&metin) {
                        return Some((*key).to_string());
                    }
                }
            }
        }
    }
    None
}

pub fn task_turu(t: Option<&Value>) -> Option<String> {
    let t = t?;
    let did = t.get("dosya_id").and_then(|v| v.as_str()).unwrap_or("");
    if did.is_empty() {
        return None;
    }
    dosya_turu(
        t.get("dosya_tur").and_then(|v| v.as_str()),
        t.get("birim_adi").and_then(|v| v.as_str()),
    )
}

fn asama_obj(key: &str, title: &str, otomatik: &str, dal: Option<&str>) -> Value {
    json!({
        "key": key,
        "title": title,
        "etiket": format!("e-ik-{}", key),
        "otomatik": otomatik,
        "dal": dal,
    })
}

pub fn hazir_surecler() -> Vec<Value> {
    vec![json!({
        "key": "icra-kesinlesme",
        "title": "Tebligat → Kesinleştirme",
        "tur": "icra",
        "etiket": "e-icra-kesinlesme",
        "taraf": "borclu",
        "asamalar": [
            asama_obj("teb-olustu", "Tebligat oluşturuldu", "db:tebligat_olustu", None),
            asama_obj("ptt", "Tebligat PTT'ye verildi", "db:ptt_kabul", None),
            asama_obj("yolda", "Tebligat yola çıktı", "db:ptt_yolda", None),
            asama_obj("teblig", "Tebliğ kontrol", "db:teblig", None),
            asama_obj("mazbata", "Mazbata dönüşü", "db:mazbata", None),
            asama_obj("mernis", "Mernis adresine tebliğ", "uyap:talep_mernis", Some("iade")),
            asama_obj("21-2", "21/2 adresine tebliğ", "uyap:talep_21_2", Some("iade")),
            asama_obj("talep-kabul", "Talep kabul", "uyap:talep_kabul", Some("iade")),
            asama_obj("itiraz", "İtiraz kontrol", "db:itiraz", None),
            asama_obj("kes-tarih", "Kesinleşme tarihi", "db:kesinlesme_tarihi", None),
            asama_obj("kes-gonder", "Kesinleştirme talebi gönderme", "uyap:kesinlestirme_gonder", None),
            asama_obj("kes-kabul", "Kesinleştirme talebi kabul", "uyap:kesinlestirme_talebi", None),
            asama_obj("kesinlesti", "Kesinleşme", "uyap:sorgu_kesinlesme", None),
        ]
    })]
}

pub fn tag_zinciri(key: &str, tags: &HashMap<String, Value>) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = Some(key.to_string());
    while let Some(k) = cur {
        if k.is_empty() || out.contains(&k) || out.len() > ETIKET_DERINLIK {
            break;
        }
        out.push(k.clone());
        cur = tags
            .get(&k)
            .and_then(|v| v.get("parent"))
            .and_then(|p| p.as_str())
            .map(|s| s.to_string());
    }
    out
}

pub fn tag_turu(key: &str, tags: &HashMap<String, Value>) -> Option<String> {
    let ana: HashMap<String, String> = dosya_turleri()
        .iter()
        .map(|(k, _, _, _)| (format!("e-{}", k), (*k).to_string()))
        .collect();
    for k in tag_zinciri(key, tags) {
        if let Some(t) = ana.get(&k) {
            return Some(t.clone());
        }
    }
    None
}

pub fn tag_uyar(tag: &Value, tur: Option<&str>) -> bool {
    let Some(tur) = tur else { return true; };
    match tag.get("tur").and_then(|v| v.as_str()) {
        None => true,
        Some(t) => t == tur,
    }
}

pub fn hazir_etiketler() -> Vec<Value> {
    let mut raw = Vec::new();
    for (k, ad, renk, _) in dosya_turleri() {
        raw.push(json!({"key": format!("e-{}", k), "name": ad, "color": renk, "parent": Value::Null}));
    }
    raw.push(json!({
        "key": "e-icra-kesinlesme",
        "name": "Tebligat – Kesinleştirme",
        "color": "#4338ca",
        "parent": "e-icra",
        "surec": "icra-kesinlesme",
    }));
    for s in hazir_surecler() {
        let s_etiket = s["etiket"].as_str().unwrap_or("");
        let s_key = s["key"].as_str().unwrap_or("");
        if let Some(asamalar) = s["asamalar"].as_array() {
            for (i, a) in asamalar.iter().enumerate() {
                let dal = a.get("dal").and_then(|v| v.as_str());
                let color = if dal.is_some() {
                    "#be123c"
                } else if i < 5 {
                    "#2563eb"
                } else {
                    "#7c3aed"
                };
                raw.push(json!({
                    "key": a["etiket"],
                    "name": a["title"],
                    "color": color,
                    "parent": s_etiket,
                    "surec": s_key,
                    "asama": a["key"],
                }));
            }
        }
    }
    raw.push(json!({"key": "e-haciz", "name": "Haciz", "color": "#dc2626", "parent": "e-icra"}));
    raw.push(json!({"key": "e-tahsilat", "name": "Tahsilat", "color": "#059669", "parent": "e-icra"}));
    raw.push(json!({"key": "e-muvekkil", "name": "Müvekkil bildirimi", "color": "#d97706", "parent": Value::Null}));
    raw.push(json!({"key": "e-acil-dosya", "name": "Acil dosya", "color": "#e11d48", "parent": Value::Null}));

    let mut agac = HashMap::new();
    for e in &raw {
        if let Some(k) = e["key"].as_str() {
            agac.insert(k.to_string(), e.clone());
        }
    }
    raw.into_iter()
        .map(|mut e| {
            let key = e["key"].as_str().unwrap_or("").to_string();
            let tur = tag_turu(&key, &agac);
            if let Some(obj) = e.as_object_mut() {
                obj.insert("tur".to_string(), tur.map(Value::String).unwrap_or(Value::Null));
            }
            e
        })
        .collect()
}

pub fn asama_etiketleri() -> HashSet<String> {
    let mut set = HashSet::new();
    for s in hazir_surecler() {
        if let Some(arr) = s["asamalar"].as_array() {
            for a in arr {
                if let Some(e) = a["etiket"].as_str() {
                    set.insert(e.to_string());
                }
            }
        }
    }
    set
}

pub fn asama_coz(v: Option<&str>) -> ZsResult<(Option<String>, Option<String>)> {
    let v = v.unwrap_or("").trim();
    if v.is_empty() {
        return Ok((None, None));
    }
    for s in hazir_surecler() {
        let sk = s["key"].as_str().unwrap_or("");
        if let Some(arr) = s["asamalar"].as_array() {
            for a in arr {
                let ak = a["key"].as_str().unwrap_or("");
                if format!("{}/{}", sk, ak) == v {
                    return Ok((Some(sk.to_string()), Some(ak.to_string())));
                }
            }
        }
    }
    Err(ZsError("Tanımsız süreç aşaması.".to_string()))
}

pub fn asama_etiketi(surec: Option<&str>, asama: Option<&str>) -> Option<String> {
    let (Some(surec), Some(asama)) = (surec, asama) else {
        return None;
    };
    for s in hazir_surecler() {
        if s["key"].as_str() == Some(surec) {
            if let Some(arr) = s["asamalar"].as_array() {
                for a in arr {
                    if a["key"].as_str() == Some(asama) {
                        return a["etiket"].as_str().map(|x| x.to_string());
                    }
                }
            }
        }
    }
    None
}

enum SonucSpec<'a> {
    None,
    SameDays(i64),
    Branch(&'a str, i64, Option<&'a str>),
}

fn sonuc_one(x: SonucSpec<'_>) -> Value {
    match x {
        SonucSpec::None => Value::Null,
        SonucSpec::SameDays(g) => json!({"baslik": Value::Null, "gun": g, "ad": Value::Null}),
        SonucSpec::Branch(b, g, ad) => json!({"baslik": b, "gun": g, "ad": ad}),
    }
}

fn mk_sonuc(ol: SonucSpec<'_>, om: SonucSpec<'_>, bk: SonucSpec<'_>) -> Value {
    json!({
        "olumlu": sonuc_one(ol),
        "olumsuz": sonuc_one(om),
        "bekliyor": sonuc_one(bk),
    })
}

fn mk_kontrol(
    id: &str,
    title: &str,
    asama: &str,
    kontrol: &str,
    sonuclar: Value,
    description: &str,
    action: Option<&str>,
    due_days: i64,
    eski: &[&str],
    kesinlesme: Option<&str>,
) -> Value {
    json!({
        "id": id,
        "title": title,
        "due_days": due_days,
        "priority": 1,
        "taraf": "borclu",
        "description": description,
        "action": action,
        "kontrol": kontrol,
        "asama": format!("icra-kesinlesme/{}", asama),
        "etiketler": [],
        "sonuclar": sonuclar,
        "eski": eski,
        "kesinlesme": kesinlesme,
    })
}

pub fn hazir_sablonlar() -> Vec<Value> {
    vec![
        mk_kontrol(
            "h-odeme-emri",
            "Tebligat oluşturuldu kontrol",
            "teb-olustu",
            "Ödeme emri tebligatı oluşturuldu mu?",
            mk_sonuc(
                SonucSpec::Branch("PTT'ye verildi kontrol", 2, Some("Oluşturuldu")),
                SonucSpec::Branch("", 1, Some("Oluşturulmadı")), // wait: baslik None! Let's fix below
                SonucSpec::SameDays(2),
            ),
            "Ödeme emri tebligatının UYAP'ta oluşturulup gönderildiğini evrak listesinden kontrol et.",
            Some("ekran:evrak"),
            3,
            &["Ödeme emri kontrol"],
            None,
        ),
        mk_kontrol(
            "h-ptt-teslim",
            "PTT'ye verildi kontrol",
            "ptt",
            "Barkodda tebligat PTT'ye verilmiş görünüyor mu (“kayıt yok” değil)?",
            mk_sonuc(
                SonucSpec::Branch("Yola çıktı kontrol", 1, Some("PTT'ye verildi")),
                SonucSpec::None,
                SonucSpec::SameDays(2),
            ),
            "PTT barkodunda tebligatın “kayıt yok” durumundan çıkıp PTT'ye verildiğini kontrol et.",
            Some("ptt:barkod"),
            3,
            &["PTT teslim kontrol"],
            None,
        ),
        mk_kontrol(
            "h-yolda",
            "Yola çıktı kontrol",
            "yolda",
            "Barkodda tebligat yola çıkmış (dağıtımda) görünüyor mu?",
            mk_sonuc(
                SonucSpec::Branch("Tebliğ kontrol", 3, Some("Yola çıktı")),
                SonucSpec::None,
                SonucSpec::SameDays(2),
            ),
            "PTT barkodunda tebligatın dağıtıma çıktığını kontrol et.",
            Some("ptt:barkod"),
            2,
            &[],
            None,
        ),
        mk_kontrol(
            "h-teblig-durum",
            "Tebliğ kontrol",
            "teblig",
            "Barkodda tebliğ sonucu (tebliğ ya da iade) belli oldu mu?",
            mk_sonuc(
                SonucSpec::Branch("Mazbata dönüşü kontrol", 3, Some("Tebliğ edildi")),
                SonucSpec::Branch("Mazbata dönüşü kontrol", 3, Some("İade")),
                SonucSpec::SameDays(2),
            ),
            "Tebligatın PTT barkodundan tebliğ durumunu sorgula. Tebliğ edildiyse kesinleşme gününü gir.",
            Some("ptt:barkod"),
            3,
            &["Tebliğ durumu kontrol", "Tebligat kontrol", "Barkod takip"],
            Some("olumlu"),
        ),
        mk_kontrol(
            "h-mazbata",
            "Mazbata dönüşü kontrol",
            "mazbata",
            "Mazbata icra dosyasına döndü mü; tebliğ mi, iade mi?",
            mk_sonuc(
                SonucSpec::Branch("İtiraz kontrol", 7, Some("Tebliğ edildi")),
                SonucSpec::Branch("Mernis adresine tebliğ", 1, Some("İade")),
                SonucSpec::SameDays(3),
            ),
            "Tebliğ mazbatasının icra dosyasına dönüp dönmediğini evrak listesinden kontrol et.",
            Some("ekran:evrak"),
            3,
            &["Mazbata kontrol"],
            None,
        ),
        mk_kontrol(
            "h-mernis",
            "Mernis adresine tebliğ",
            "mernis",
            "Mernis adresine tebligat talebi gönderildi mi?",
            mk_sonuc(
                SonucSpec::Branch("Talep kabul kontrol", 2, Some("Talep gönderildi")),
                SonucSpec::Branch("21/2 adresine tebliğ", 1, Some("Mernis adresi aynı")),
                SonucSpec::SameDays(1),
            ),
            "İade dönen tebligat için borçlunun Mernis adresine tebligat çıkarılmasını talep et. Tebligat zaten Mernis adresine yapıldıysa 21/2 tebliğe geç.",
            Some("talep:adres_arastirmasi"),
            1,
            &[],
            None,
        ),
        mk_kontrol(
            "h-21-2",
            "21/2 adresine tebliğ",
            "21-2",
            "Tebligat Kanunu 21/2'ye göre tebligat talebi gönderildi mi?",
            mk_sonuc(
                SonucSpec::Branch("Talep kabul kontrol", 2, Some("Talep gönderildi")),
                SonucSpec::None,
                SonucSpec::SameDays(1),
            ),
            "Mernis adresine iade dönen tebligat için 21/2 tebligat talebi gönder.",
            None,
            1,
            &["21/2 akıbet kontrol"],
            None,
        ),
        mk_kontrol(
            "h-talep-kabul",
            "Talep kabul kontrol",
            "talep-kabul",
            "Yeniden tebligat talebi icra dairesince kabul edildi mi?",
            mk_sonuc(
                SonucSpec::Branch("Tebligat oluşturuldu kontrol", 2, Some("Kabul edildi")),
                SonucSpec::Branch("Mernis adresine tebliğ", 1, Some("Reddedildi")),
                SonucSpec::SameDays(2),
            ),
            "Talebin kabul edildiğini evrak listesinden kontrol et; kabul edilince tebligat süreci baştan izlenir.",
            Some("ekran:evrak"),
            2,
            &[],
            None,
        ),
        mk_kontrol(
            "h-itiraz",
            "İtiraz kontrol",
            "itiraz",
            "Süresinde itiraz edilmedi mi?",
            mk_sonuc(
                SonucSpec::Branch("Kesinleşme tarihi kontrol", 0, Some("İtiraz yok")),
                SonucSpec::Branch("Müvekkile bildirim", 1, Some("İtiraz var")),
                SonucSpec::SameDays(2),
            ),
            "İtiraz süresi dolunca borçlunun itiraz edip etmediğini evrak / safahattan kontrol et.",
            Some("ekran:evrak"),
            7,
            &[],
            None,
        ),
        mk_kontrol(
            KESINLESME_SABLONU,
            "Kesinleşme tarihi kontrol",
            "kes-tarih",
            "Kesinleşme günü geldi ve süresinde itiraz edilmedi mi?",
            mk_sonuc(
                SonucSpec::Branch("Kesinleştirme talebi gönder", 0, Some("İtiraz yok")),
                SonucSpec::Branch("Müvekkile bildirim", 1, Some("İtiraz var")),
                SonucSpec::SameDays(2),
            ),
            "Kesinleşme gününe kadar itiraz edilip edilmediğini evrak / safahattan kontrol et; itiraz yoksa kesinleştirme talebini gönder.",
            Some("ekran:evrak"),
            1,
            &[],
            None,
        ),
        mk_kontrol(
            "h-kes-gonder",
            "Kesinleştirme talebi gönder",
            "kes-gonder",
            "Kesinleştirme talebi gönderildi mi?",
            mk_sonuc(
                SonucSpec::Branch("Kesinleştirme talebi kabul kontrol", 2, Some("Gönderildi")),
                SonucSpec::None,
                SonucSpec::SameDays(1),
            ),
            "İcra dairesine takibin kesinleştirilmesi talebini gönder.",
            Some("talep:kesinlestirme"),
            1,
            &[],
            None,
        ),
        mk_kontrol(
            "h-kes-talep",
            "Kesinleştirme talebi kabul kontrol",
            "kes-kabul",
            "Kesinleştirme talebi kabul edildi mi?",
            mk_sonuc(
                SonucSpec::Branch("Kesinleşme kontrol", 1, Some("Kabul edildi")),
                SonucSpec::Branch("Kesinleştirme talebi gönder", 1, Some("Reddedildi")),
                SonucSpec::SameDays(2),
            ),
            "Kesinleştirme talebinin icra dairesince kabul edilip edilmediğini kontrol et.",
            Some("ekran:evrak"),
            2,
            &["Kesinleştirme talebi kontrol"],
            None,
        ),
        mk_kontrol(
            "h-kesinlesme",
            "Kesinleşme kontrol",
            "kesinlesti",
            "Takip kesinleşti mi (sorgu yapılabiliyor mu)?",
            mk_sonuc(
                SonucSpec::Branch("Haciz talepleri", 2, Some("Kesinleşti")),
                SonucSpec::Branch("Kesinleştirme talebi kabul kontrol", 2, Some("Kesinleşmedi")),
                SonucSpec::SameDays(3),
            ),
            "Sorgu ekranında bir sorgu dene: “kesinleşmediği için sorgu yapılamaz” uyarısı gelmiyorsa takip borçlu yönünden kesinleşmiştir.",
            Some("ekran:safahat"),
            2,
            &[],
            None,
        ),
        json!({
            "id": "h-adres", "title": "Adres araştırması", "due_days": 2, "priority": 1, "taraf": "borclu",
            "description": "MERNİS / SGK / GSM adres araştırması yap, yeni adrese tebligat çıkar.",
            "action": "talep:adres_arastirmasi", "kontrol": "Yeni adres bulundu mu?", "asama": Value::Null, "etiketler": [],
            "sonuclar": mk_sonuc(SonucSpec::Branch("Talep kabul kontrol", 3, None), SonucSpec::Branch("21/2 adresine tebliğ", 1, None), SonucSpec::SameDays(3)),
        }),
        json!({
            "id": "h-takyidat", "title": "Takyidat kontrol", "due_days": 2, "priority": 1, "taraf": "borclu",
            "description": "Araç / tapu takyidat sorgu sonucunu kontrol et.", "action": "sorgu:takyidat",
            "kontrol": "Borçlu adına araç / taşınmaz var mı?", "asama": Value::Null, "etiketler": ["e-haciz"],
            "sonuclar": mk_sonuc(SonucSpec::Branch("Haciz / yakalama talebi", 2, None), SonucSpec::Branch("Mal varlığı sorgusu", 3, None), SonucSpec::SameDays(2)),
        }),
        json!({
            "id": "h-reddiyat", "title": "Reddiyat kontrol", "due_days": 2, "priority": 1, "taraf": Value::Null,
            "description": "Reddiyatın hesaba geçip geçmediğini kontrol et.", "action": Value::Null,
            "kontrol": "Reddiyat hesaba geçti mi?", "asama": Value::Null, "etiketler": ["e-tahsilat"],
            "sonuclar": mk_sonuc(SonucSpec::Branch("Müvekkile bildirim", 1, None), SonucSpec::Branch("Muhasebe ile görüş", 1, None), SonucSpec::SameDays(2)),
        }),
        json!({
            "id": "h-tahsilat", "title": "Tahsilat kontrol", "due_days": 2, "priority": 1, "taraf": "borclu",
            "description": "Tahsilatı kontrol et, müvekkile bildir.", "action": Value::Null,
            "kontrol": "Tahsilat hesaba geçti mi?", "asama": Value::Null, "etiketler": ["e-tahsilat"],
            "sonuclar": mk_sonuc(SonucSpec::Branch("Müvekkile bildirim", 1, None), SonucSpec::Branch("Muhasebe ile görüş", 1, None), SonucSpec::SameDays(2)),
        }),
        json!({
            "id": "h-aranacak", "title": "Borçlu aranacak", "due_days": 1, "priority": 1, "taraf": "borclu",
            "description": "Borçluya ulaş, sonucu nota yaz.", "action": Value::Null,
            "kontrol": "Borçluya ulaşıldı mı?", "asama": Value::Null, "etiketler": [],
            "sonuclar": mk_sonuc(SonucSpec::Branch("Ödeme planı takibi", 7, None), SonucSpec::SameDays(2), SonucSpec::None),
        }),
        json!({
            "id": "h-haciz", "title": "Haciz talepleri", "due_days": 2, "priority": 1, "taraf": "borclu",
            "description": "SGK, banka, araç ve tapu haciz taleplerini hazırla.", "action": Value::Null,
            "kontrol": Value::Null, "asama": Value::Null, "etiketler": ["e-haciz"], "sonuclar": mk_sonuc(SonucSpec::None, SonucSpec::None, SonucSpec::None),
        }),
        json!({
            "id": "h-muvekkil", "title": "Müvekkile bildirim", "due_days": 1, "priority": 1, "taraf": Value::Null,
            "description": "Dosyadaki gelişmeyi müvekkile bildir.", "action": Value::Null,
            "kontrol": Value::Null, "asama": Value::Null, "etiketler": ["e-muvekkil"], "sonuclar": mk_sonuc(SonucSpec::None, SonucSpec::None, SonucSpec::None),
        }),
    ]
}

fn fix_hazir_sablonlar_nulls(mut v: Vec<Value>) -> Vec<Value> {
    // Fix baslik: null where baslik was "" in h-odeme-emri / h-ptt-teslim / h-teblig-durum / h-mazbata
    // Wait: let's match exact Python values for baslik and ad on bekliyor/olumsuz!
    // h-odeme-emri: olumsuz = (None, 1, "Oluşturulmadı")
    if let Some(s) = v.get_mut(0) {
        s["sonuclar"]["olumsuz"] = json!({"baslik": Value::Null, "gun": 1, "ad": "Oluşturulmadı"});
    }
    // h-ptt-teslim: olumsuz = (None, 2, "Kayıt yok")
    if let Some(s) = v.get_mut(1) {
        s["sonuclar"]["olumsuz"] = json!({"baslik": Value::Null, "gun": 2, "ad": "Kayıt yok"});
    }
    // h-teblig-durum: bekliyor = (None, 2, "Henüz sonuç yok")
    if let Some(s) = v.get_mut(3) {
        s["sonuclar"]["bekliyor"] = json!({"baslik": Value::Null, "gun": 2, "ad": "Henüz sonuç yok"});
    }
    // h-mazbata: bekliyor = (None, 3, "Henüz dönmedi")
    if let Some(s) = v.get_mut(4) {
        s["sonuclar"]["bekliyor"] = json!({"baslik": Value::Null, "gun": 3, "ad": "Henüz dönmedi"});
    }
    // h-kes-tarih: bekliyor = (None, 2, "Henüz gelmedi")
    if let Some(s) = v.get_mut(9) {
        s["sonuclar"]["bekliyor"] = json!({"baslik": Value::Null, "gun": 2, "ad": "Henüz gelmedi"});
    }
    v
}

pub fn hazir_sablonlar_exact() -> Vec<Value> {
    fix_hazir_sablonlar_nulls(hazir_sablonlar())
}

pub fn clean_sonuclar(d: Option<&Value>) -> ZsResult<Value> {
    let mut out = Map::new();
    let obj = d.and_then(|v| v.as_object());
    for &k in TEMPLATE_SONUCLAR {
        let x = obj.and_then(|m| m.get(k)).and_then(|v| v.as_object());
        let Some(x) = x else {
            out.insert(k.to_string(), Value::Null);
            continue;
        };
        let baslik_str = x
            .get("baslik")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ");
        let baslik: String = baslik_str.chars().take(200).collect();
        let baslik_opt = if baslik.is_empty() { None } else { Some(baslik) };

        let gun_opt = match x.get("gun") {
            None | Some(Value::Null) => None,
            Some(Value::String(s)) if s.trim().is_empty() => None,
            Some(Value::Number(n)) => {
                let g = n
                    .as_i64()
                    .ok_or_else(|| ZsError("Sonraki iş süresi gün sayısı olmalı.".to_string()))?;
                if !(0..=365).contains(&g) {
                    return Err(ZsError("Sonraki iş süresi 0–365 gün olmalı.".to_string()));
                }
                Some(g)
            }
            Some(Value::String(s)) => {
                let g: i64 = s
                    .trim()
                    .parse()
                    .map_err(|_| ZsError("Sonraki iş süresi gün sayısı olmalı.".to_string()))?;
                if !(0..=365).contains(&g) {
                    return Err(ZsError("Sonraki iş süresi 0–365 gün olmalı.".to_string()));
                }
                Some(g)
            }
            _ => return Err(ZsError("Sonraki iş süresi gün sayısı olmalı.".to_string())),
        };

        let ad_str = x
            .get("ad")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ");
        let ad: String = ad_str.chars().take(40).collect();
        let ad_opt = if ad.is_empty() { None } else { Some(ad) };

        if baslik_opt.is_some() || gun_opt.is_some() || ad_opt.is_some() {
            out.insert(
                k.to_string(),
                json!({"baslik": baslik_opt, "gun": gun_opt, "ad": ad_opt}),
            );
        } else {
            out.insert(k.to_string(), Value::Null);
        }
    }
    Ok(Value::Object(out))
}

pub fn clean_dosya(d: Option<&Value>) -> Option<Map<String, Value>> {
    let obj = d?.as_object()?;
    let mut out = Map::new();
    for &k in DOSYA_FIELDS {
        if k == "dosya_uyap" {
            continue;
        }
        let s = obj
            .get(k)
            .and_then(|v| match v {
                Value::String(s) => Some(s.clone()),
                Value::Number(n) => Some(n.to_string()),
                _ => None,
            })
            .unwrap_or_default();
        let trimmed: String = s.trim().chars().take(200).collect();
        if trimmed.is_empty() {
            out.insert(k.to_string(), Value::Null);
        } else {
            out.insert(k.to_string(), Value::String(trimmed));
        }
    }
    let barkod_raw = out.get("barkod").and_then(|v| v.as_str()).unwrap_or("");
    let digits: String = barkod_raw.chars().filter(|c| c.is_ascii_digit()).take(30).collect();
    out.insert(
        "barkod".to_string(),
        if digits.is_empty() {
            Value::Null
        } else {
            Value::String(digits)
        },
    );

    let mut kart = Map::new();
    if let Some(u) = obj.get("uyap").and_then(|v| v.as_object()) {
        for (k, v) in u {
            if UYAP_KART_ALANLARI.contains(&k.as_str()) {
                match v {
                    Value::String(s) => {
                        let t: String = s.chars().take(200).collect();
                        kart.insert(k.clone(), Value::String(t));
                    }
                    Value::Number(n) => {
                        kart.insert(k.clone(), Value::Number(n.clone()));
                    }
                    _ => {}
                }
            }
        }
    }
    let dosya_uyap = if kart.get("dosyaId").map(|v| !v.is_null()).unwrap_or(false) {
        Value::String(serde_json::to_string(&Value::Object(kart)).unwrap_or_default())
    } else {
        Value::Null
    };
    out.insert("dosya_uyap".to_string(), dosya_uyap);
    if out.get("evrak_id").map(|v| v.is_null()).unwrap_or(true) {
        out.insert("evrak_adi".to_string(), Value::Null);
    }
    if out.get("dosya_id").and_then(|v| v.as_str()).map(|s| !s.is_empty()).unwrap_or(false) {
        Some(out)
    } else {
        None
    }
}

#[derive(Debug, Clone)]
pub enum SqlParam {
    Null,
    Int(i64),
    Text(String),
}

impl From<i64> for SqlParam {
    fn from(v: i64) -> Self {
        SqlParam::Int(v)
    }
}
impl From<i32> for SqlParam {
    fn from(v: i32) -> Self {
        SqlParam::Int(v as i64)
    }
}
impl From<bool> for SqlParam {
    fn from(v: bool) -> Self {
        SqlParam::Int(if v { 1 } else { 0 })
    }
}
impl From<String> for SqlParam {
    fn from(v: String) -> Self {
        SqlParam::Text(v)
    }
}
impl From<&str> for SqlParam {
    fn from(v: &str) -> Self {
        SqlParam::Text(v.to_string())
    }
}
impl<T: Into<SqlParam>> From<Option<T>> for SqlParam {
    fn from(v: Option<T>) -> Self {
        match v {
            Some(x) => x.into(),
            None => SqlParam::Null,
        }
    }
}

impl SqlParam {
    fn to_sqlite(&self) -> SqliteValue {
        match self {
            SqlParam::Null => SqliteValue::Null,
            SqlParam::Int(i) => SqliteValue::Integer(*i),
            SqlParam::Text(s) => SqliteValue::Text(s.clone()),
        }
    }
}

#[derive(Debug)]
struct PgParam<'a>(&'a SqlParam);

impl<'a> postgres::types::ToSql for PgParam<'a> {
    fn to_sql(
        &self,
        ty: &postgres::types::Type,
        out: &mut postgres::types::private::BytesMut,
    ) -> Result<postgres::types::IsNull, Box<dyn std::error::Error + Sync + Send>> {
        match self.0 {
            SqlParam::Null => Ok(postgres::types::IsNull::Yes),
            SqlParam::Int(i) => {
                if *ty == postgres::types::Type::INT4 {
                    (*i as i32).to_sql(ty, out)
                } else {
                    i.to_sql(ty, out)
                }
            }
            SqlParam::Text(s) => s.to_sql(ty, out),
        }
    }

    fn accepts(ty: &postgres::types::Type) -> bool {
        matches!(
            *ty,
            postgres::types::Type::INT8
                | postgres::types::Type::INT4
                | postgres::types::Type::TEXT
                | postgres::types::Type::VARCHAR
        )
    }

    postgres::types::to_sql_checked!();
}

fn to_pg_placeholders(q: &str) -> String {
    let mut out = String::with_capacity(q.len() + 16);
    let mut idx = 1usize;
    let mut in_quote = false;
    let mut chars = q.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\'' {
            out.push(c);
            if in_quote && chars.peek() == Some(&'\'') {
                out.push(chars.next().unwrap());
            } else {
                in_quote = !in_quote;
            }
        } else if c == '?' && !in_quote {
            out.push('$');
            out.push_str(&idx.to_string());
            idx += 1;
        } else {
            out.push(c);
        }
    }
    out
}

enum DbConn {
    Sqlite(Connection),
    Pg(PgClient),
}

struct StoreInner {
    dsn: String,
    sqlite_path: PathBuf,
    is_pg: bool,
    conn: Option<DbConn>,
}

impl StoreInner {
    fn ensure_conn(&mut self) -> ZsResult<()> {
        if self.conn.is_none() {
            if self.is_pg {
                let c = PgClient::connect(&self.dsn, NoTls)
                    .map_err(|e| ZsError(format!("PostgreSQL bağlantı hatası: {}", e)))?;
                self.conn = Some(DbConn::Pg(c));
            } else {
                if let Some(parent) = self.sqlite_path.parent() {
                    let _ = std::fs::create_dir_all(parent);
                }
                let c = Connection::open(&self.sqlite_path)
                    .map_err(|e| ZsError(format!("SQLite bağlantı hatası: {}", e)))?;
                let _ = c.execute_batch("PRAGMA busy_timeout = 5000;");
                self.conn = Some(DbConn::Sqlite(c));
            }
        }
        Ok(())
    }

    fn exec(&mut self, q: &str, params: &[SqlParam]) -> ZsResult<u64> {
        self.ensure_conn()?;
        match self.conn.as_mut().unwrap() {
            DbConn::Sqlite(c) => {
                let vals: Vec<SqliteValue> = params.iter().map(|p| p.to_sqlite()).collect();
                c.execute(q, params_from_iter(vals))
                    .map(|n| n as u64)
                    .map_err(|e| ZsError(format!("SQLite hata: {}", e)))
            }
            DbConn::Pg(c) => {
                let pq = to_pg_placeholders(q);
                let pg_params: Vec<PgParam<'_>> = params.iter().map(PgParam).collect();
                let refs: Vec<&(dyn postgres::types::ToSql + Sync)> = pg_params
                    .iter()
                    .map(|p| p as &(dyn postgres::types::ToSql + Sync))
                    .collect();
                match c.execute(&pq, &refs) {
                    Ok(n) => Ok(n),
                    Err(e) => {
                        if c.is_closed() {
                            self.conn = None;
                        }
                        Err(ZsError(format!("PostgreSQL hata: {}", e)))
                    }
                }
            }
        }
    }

    fn insert_returning_id(&mut self, q: &str, params: &[SqlParam]) -> ZsResult<i64> {
        self.ensure_conn()?;
        match self.conn.as_mut().unwrap() {
            DbConn::Sqlite(c) => {
                let vals: Vec<SqliteValue> = params.iter().map(|p| p.to_sqlite()).collect();
                c.execute(q, params_from_iter(vals))
                    .map_err(|e| ZsError(format!("SQLite hata: {}", e)))?;
                Ok(c.last_insert_rowid())
            }
            DbConn::Pg(c) => {
                let pq = format!("{} RETURNING id", to_pg_placeholders(q));
                let pg_params: Vec<PgParam<'_>> = params.iter().map(PgParam).collect();
                let refs: Vec<&(dyn postgres::types::ToSql + Sync)> = pg_params
                    .iter()
                    .map(|p| p as &(dyn postgres::types::ToSql + Sync))
                    .collect();
                match c.query_one(&pq, &refs) {
                    Ok(row) => {
                        if let Ok(v) = row.try_get::<_, i64>(0) {
                            Ok(v)
                        } else if let Ok(v) = row.try_get::<_, i32>(0) {
                            Ok(v as i64)
                        } else {
                            Ok(0)
                        }
                    }
                    Err(e) => {
                        if c.is_closed() {
                            self.conn = None;
                        }
                        Err(ZsError(format!("PostgreSQL hata: {}", e)))
                    }
                }
            }
        }
    }

    fn query_all(&mut self, q: &str, params: &[SqlParam]) -> ZsResult<Vec<Value>> {
        self.ensure_conn()?;
        match self.conn.as_mut().unwrap() {
            DbConn::Sqlite(c) => {
                let mut stmt = c
                    .prepare(q)
                    .map_err(|e| ZsError(format!("SQLite prepare hata: {}", e)))?;
                let col_names: Vec<String> =
                    stmt.column_names().iter().map(|s| s.to_string()).collect();
                let vals: Vec<SqliteValue> = params.iter().map(|p| p.to_sqlite()).collect();
                let rows = stmt
                    .query_map(params_from_iter(vals), |row| {
                        let mut m = Map::new();
                        for (i, name) in col_names.iter().enumerate() {
                            let val = match row.get_ref(i)? {
                                rusqlite::types::ValueRef::Null => Value::Null,
                                rusqlite::types::ValueRef::Integer(n) => json!(n),
                                rusqlite::types::ValueRef::Real(f) => json!(f),
                                rusqlite::types::ValueRef::Text(b) => {
                                    Value::String(String::from_utf8_lossy(b).into_owned())
                                }
                                rusqlite::types::ValueRef::Blob(b) => {
                                    Value::String(String::from_utf8_lossy(b).into_owned())
                                }
                            };
                            m.insert(name.clone(), val);
                        }
                        Ok(Value::Object(m))
                    })
                    .map_err(|e| ZsError(format!("SQLite query hata: {}", e)))?;
                let mut out = Vec::new();
                for r in rows {
                    out.push(r.map_err(|e| ZsError(format!("SQLite row hata: {}", e)))?);
                }
                Ok(out)
            }
            DbConn::Pg(c) => {
                let pq = to_pg_placeholders(q);
                let pg_params: Vec<PgParam<'_>> = params.iter().map(PgParam).collect();
                let refs: Vec<&(dyn postgres::types::ToSql + Sync)> = pg_params
                    .iter()
                    .map(|p| p as &(dyn postgres::types::ToSql + Sync))
                    .collect();
                let rows = match c.query(&pq, &refs) {
                    Ok(r) => r,
                    Err(e) => {
                        if c.is_closed() {
                            self.conn = None;
                        }
                        return Err(ZsError(format!("PostgreSQL query hata: {}", e)));
                    }
                };
                let mut out = Vec::with_capacity(rows.len());
                for row in rows {
                    let mut m = Map::new();
                    for (i, col) in row.columns().iter().enumerate() {
                        let ty = col.type_();
                        let val = if *ty == postgres::types::Type::INT8 {
                            row.try_get::<_, Option<i64>>(i)
                                .ok()
                                .flatten()
                                .map(|v| json!(v))
                                .unwrap_or(Value::Null)
                        } else if *ty == postgres::types::Type::INT4 {
                            row.try_get::<_, Option<i32>>(i)
                                .ok()
                                .flatten()
                                .map(|v| json!(v as i64))
                                .unwrap_or(Value::Null)
                        } else if *ty == postgres::types::Type::INT2 {
                            row.try_get::<_, Option<i16>>(i)
                                .ok()
                                .flatten()
                                .map(|v| json!(v as i64))
                                .unwrap_or(Value::Null)
                        } else if *ty == postgres::types::Type::FLOAT8 {
                            row.try_get::<_, Option<f64>>(i)
                                .ok()
                                .flatten()
                                .map(|v| json!(v))
                                .unwrap_or(Value::Null)
                        } else if *ty == postgres::types::Type::BOOL {
                            row.try_get::<_, Option<bool>>(i)
                                .ok()
                                .flatten()
                                .map(|v| json!(v))
                                .unwrap_or(Value::Null)
                        } else {
                            row.try_get::<_, Option<String>>(i)
                                .ok()
                                .flatten()
                                .map(Value::String)
                                .unwrap_or(Value::Null)
                        };
                        m.insert(col.name().to_string(), val);
                    }
                    out.push(Value::Object(m));
                }
                Ok(out)
            }
        }
    }

    fn query_one(&mut self, q: &str, params: &[SqlParam]) -> ZsResult<Option<Value>> {
        let mut rows = self.query_all(q, params)?;
        if rows.is_empty() {
            Ok(None)
        } else {
            Ok(Some(rows.remove(0)))
        }
    }

    fn schema_init(&mut self) -> ZsResult<()> {
        let pk = if self.is_pg {
            "BIGSERIAL PRIMARY KEY"
        } else {
            "INTEGER PRIMARY KEY AUTOINCREMENT"
        };
        let schema: Vec<String> = vec![
            format!("CREATE TABLE IF NOT EXISTS zs_users (id {pk}, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL, pw_hash TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, sess_epoch INTEGER NOT NULL DEFAULT 0, created_at BIGINT NOT NULL)"),
            format!("CREATE TABLE IF NOT EXISTS zs_tasks (id {pk}, parent_id BIGINT, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', assignee_id BIGINT, created_by BIGINT, due_at BIGINT, status TEXT NOT NULL, sort_order BIGINT NOT NULL DEFAULT 0, created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL)"),
            "CREATE INDEX IF NOT EXISTS zs_tasks_parent ON zs_tasks(parent_id)".to_string(),
            format!("CREATE TABLE IF NOT EXISTS zs_notes (id {pk}, task_id BIGINT NOT NULL, author_id BIGINT, body TEXT NOT NULL, note_at BIGINT, created_at BIGINT NOT NULL)"),
            "CREATE INDEX IF NOT EXISTS zs_notes_task ON zs_notes(task_id)".to_string(),
            format!("CREATE TABLE IF NOT EXISTS zs_files (id {pk}, task_id BIGINT NOT NULL, uploader_id BIGINT, filename TEXT NOT NULL, mime TEXT NOT NULL, size BIGINT NOT NULL, storage_key TEXT NOT NULL, sha256 TEXT NOT NULL, created_at BIGINT NOT NULL)"),
            "CREATE INDEX IF NOT EXISTS zs_files_task ON zs_files(task_id)".to_string(),
            format!("CREATE TABLE IF NOT EXISTS zs_events (id {pk}, task_id BIGINT, actor_id BIGINT, kind TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{{}}', created_at BIGINT NOT NULL)"),
            "CREATE INDEX IF NOT EXISTS zs_events_task ON zs_events(task_id)".to_string(),
            format!("CREATE TABLE IF NOT EXISTS zs_transfers (id {pk}, task_id BIGINT NOT NULL, from_user BIGINT, to_user BIGINT NOT NULL, by_user BIGINT NOT NULL, hide_subtasks INTEGER NOT NULL DEFAULT 0, hide_notes INTEGER NOT NULL DEFAULT 0, hide_files INTEGER NOT NULL DEFAULT 0, hide_history INTEGER NOT NULL DEFAULT 0, cut_task BIGINT NOT NULL DEFAULT 0, cut_note BIGINT NOT NULL DEFAULT 0, cut_file BIGINT NOT NULL DEFAULT 0, cut_event BIGINT NOT NULL DEFAULT 0, created_at BIGINT NOT NULL)"),
            "CREATE INDEX IF NOT EXISTS zs_transfers_task ON zs_transfers(task_id)".to_string(),
            format!("CREATE TABLE IF NOT EXISTS zs_members (id {pk}, task_id BIGINT NOT NULL, user_id BIGINT NOT NULL, role TEXT NOT NULL, added_by BIGINT, created_at BIGINT NOT NULL)"),
            "CREATE INDEX IF NOT EXISTS zs_members_task ON zs_members(task_id)".to_string(),
            format!("CREATE TABLE IF NOT EXISTS zs_extensions (id {pk}, task_id BIGINT NOT NULL, requester_id BIGINT NOT NULL, old_due BIGINT, new_due BIGINT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL, decided_by BIGINT, decision_note TEXT NOT NULL DEFAULT '', decided_at BIGINT, created_at BIGINT NOT NULL)"),
            "CREATE INDEX IF NOT EXISTS zs_extensions_task ON zs_extensions(task_id)".to_string(),
            "CREATE TABLE IF NOT EXISTS zs_seen (user_id BIGINT NOT NULL, task_id BIGINT NOT NULL, last_event BIGINT NOT NULL DEFAULT 0, PRIMARY KEY (user_id, task_id))".to_string(),
            format!("CREATE TABLE IF NOT EXISTS zs_audit (id {pk}, at BIGINT NOT NULL, user_id BIGINT, username TEXT, role TEXT, ip TEXT, user_agent TEXT, method TEXT NOT NULL, path TEXT NOT NULL, action TEXT NOT NULL, target_id BIGINT, ok INTEGER NOT NULL, status INTEGER NOT NULL, error TEXT, data TEXT, result TEXT, before TEXT, ms INTEGER)"),
            "CREATE INDEX IF NOT EXISTS zs_audit_at ON zs_audit(at)".to_string(),
            format!("CREATE TABLE IF NOT EXISTS zs_push_subs (id {pk}, user_id BIGINT NOT NULL, endpoint TEXT NOT NULL UNIQUE, p256dh TEXT NOT NULL, auth TEXT NOT NULL, user_agent TEXT, created_at BIGINT NOT NULL, last_ok BIGINT, fails INTEGER NOT NULL DEFAULT 0)"),
            "CREATE INDEX IF NOT EXISTS zs_push_subs_user ON zs_push_subs(user_id)".to_string(),
            "CREATE TABLE IF NOT EXISTS zs_telegram (user_id BIGINT PRIMARY KEY, chat_id BIGINT NOT NULL, tg_name TEXT, linked_at BIGINT NOT NULL)".to_string(),
            "CREATE TABLE IF NOT EXISTS zs_tg_codes (code TEXT PRIMARY KEY, user_id BIGINT NOT NULL, expires_at BIGINT NOT NULL)".to_string(),
            "CREATE TABLE IF NOT EXISTS zs_tg_msgs (chat_id BIGINT NOT NULL, message_id BIGINT NOT NULL, task_id BIGINT NOT NULL, created_at BIGINT NOT NULL, PRIMARY KEY (chat_id, message_id))".to_string(),
            format!("CREATE TABLE IF NOT EXISTS zs_reminders (id {pk}, task_id BIGINT, sub_id BIGINT, user_id BIGINT NOT NULL, created_by BIGINT NOT NULL, remind_at BIGINT NOT NULL, note TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', sent_at BIGINT, created_at BIGINT NOT NULL)"),
            "CREATE INDEX IF NOT EXISTS zs_reminders_user ON zs_reminders(user_id)".to_string(),
            "CREATE INDEX IF NOT EXISTS zs_reminders_task ON zs_reminders(task_id)".to_string(),
            "CREATE INDEX IF NOT EXISTS zs_reminders_due ON zs_reminders(sent_at, remind_at)".to_string(),
            "CREATE TABLE IF NOT EXISTS zs_sso_codes (code TEXT PRIMARY KEY, user_id BIGINT NOT NULL, expires_at BIGINT NOT NULL)".to_string(),
            format!("CREATE TABLE IF NOT EXISTS zs_templates (id {pk}, office_id TEXT, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', due_days INTEGER, priority INTEGER NOT NULL DEFAULT 1, taraf TEXT, action TEXT, created_by BIGINT, created_at BIGINT NOT NULL)"),
            "CREATE INDEX IF NOT EXISTS zs_templates_office ON zs_templates(office_id)".to_string(),
            format!("CREATE TABLE IF NOT EXISTS zs_tags (id {pk}, office_id TEXT, owner_id BIGINT, name TEXT NOT NULL, color TEXT NOT NULL DEFAULT '', created_by BIGINT, created_at BIGINT NOT NULL)"),
            "CREATE INDEX IF NOT EXISTS zs_tags_office ON zs_tags(office_id)".to_string(),
            "CREATE TABLE IF NOT EXISTS zs_task_tags (task_id BIGINT NOT NULL, tag TEXT NOT NULL, added_by BIGINT, created_at BIGINT NOT NULL, PRIMARY KEY (task_id, tag))".to_string(),
        ];
        for s in schema {
            self.exec(&s, &[])?;
        }

        let added_cols: &[(&str, &[(&str, &str)])] = &[
            ("zs_tasks", &[
                ("is_private", "INTEGER NOT NULL DEFAULT 0"),
                ("priority", "INTEGER NOT NULL DEFAULT 1"),
                ("submitted_at", "BIGINT"),
                ("office_id", "TEXT"),
                ("dosya_id", "TEXT"),
                ("birim_id", "TEXT"),
                ("birim_adi", "TEXT"),
                ("dosya_no", "TEXT"),
                ("dosya_tur", "TEXT"),
                ("dosya_uyap", "TEXT"),
                ("evrak_id", "TEXT"),
                ("evrak_adi", "TEXT"),
                ("taraf_adi", "TEXT"),
                ("barkod", "TEXT"),
                ("sablon", "TEXT"),
                ("surec", "TEXT"),
                ("asama", "TEXT"),
                ("sonuc", "TEXT"),
            ]),
            ("zs_reminders", &[
                ("sub_id", "BIGINT"),
                ("title", "TEXT NOT NULL DEFAULT ''"),
            ]),
            ("zs_templates", &[
                ("kontrol", "TEXT"),
                ("sonuclar", "TEXT"),
                ("asama", "TEXT"),
                ("etiketler", "TEXT"),
            ]),
            ("zs_telegram", &[("tg_username", "TEXT")]),
            ("zs_tags", &[("parent", "TEXT")]),
            ("zs_users", &[("account_username", "TEXT"), ("office_id", "TEXT")]),
        ];
        for (tbl, cols) in added_cols {
            let existing: HashSet<String> = if self.is_pg {
                self.query_all(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                    &[SqlParam::Text((*tbl).to_string())],
                )?
                .into_iter()
                .filter_map(|r| r["column_name"].as_str().map(|s| s.to_string()))
                .collect()
            } else {
                self.query_all(&format!("PRAGMA table_info({})", tbl), &[])?
                    .into_iter()
                    .filter_map(|r| r["name"].as_str().map(|s| s.to_string()))
                    .collect()
            };
            for (col, defn) in *cols {
                if !existing.contains(*col) {
                    self.exec(&format!("ALTER TABLE {} ADD COLUMN {} {}", tbl, col, defn), &[])?;
                }
            }
        }

        let post_indexes = [
            "CREATE UNIQUE INDEX IF NOT EXISTS zs_users_account ON zs_users(account_username) WHERE account_username IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS zs_tasks_office_dosya ON zs_tasks(office_id, dosya_id)",
            "CREATE INDEX IF NOT EXISTS zs_tasks_office_birim_dosya ON zs_tasks(office_id, birim_id, dosya_no)",
            "CREATE INDEX IF NOT EXISTS zs_tasks_office_surec ON zs_tasks(office_id, surec, asama)",
        ];
        for idx in post_indexes {
            self.exec(idx, &[])?;
        }
        Ok(())
    }
}

pub struct ZsStore {
    inner: Mutex<StoreInner>,
}

impl ZsStore {
    pub fn new(dsn: Option<String>, sqlite_path_override: Option<PathBuf>) -> ZsResult<Self> {
        let dsn_val = dsn.unwrap_or_else(accounts::database_url);
        let sp = sqlite_path_override.unwrap_or_else(sqlite_path);
        let is_pg = !dsn_val.is_empty();
        let mut inner = StoreInner {
            dsn: dsn_val,
            sqlite_path: sp,
            is_pg,
            conn: None,
        };
        inner.schema_init()?;
        Ok(Self {
            inner: Mutex::new(inner),
        })
    }

    fn ev(
        inner: &mut StoreInner,
        task_id: Option<i64>,
        actor_id: Option<i64>,
        kind: &str,
        detail: Option<Value>,
    ) -> ZsResult<()> {
        let d = detail
            .map(|v| serde_json::to_string(&v).unwrap_or_else(|_| "{}".to_string()))
            .unwrap_or_else(|| "{}".to_string());
        inner.exec(
            "INSERT INTO zs_events(task_id, actor_id, kind, detail, created_at) VALUES (?,?,?,?,?)",
            &[
                task_id.into(),
                actor_id.into(),
                kind.into(),
                d.into(),
                now_ts().into(),
            ],
        )?;
        Ok(())
    }

    fn clean_user(r: Option<Value>) -> Option<Value> {
        let mut u = r?;
        if let Some(obj) = u.as_object_mut() {
            obj.remove("pw_hash");
        }
        Some(u)
    }

    pub fn user_count(&self) -> ZsResult<i64> {
        let mut g = self.inner.lock().unwrap();
        let row = g.query_one("SELECT COUNT(*) AS n FROM zs_users", &[])?;
        Ok(row.and_then(|r| r["n"].as_i64()).unwrap_or(0))
    }

    pub fn get_user(&self, user_id: i64) -> Option<Value> {
        let mut g = self.inner.lock().ok()?;
        let row = g
            .query_one(
                "SELECT id, username, display_name, role, active, sess_epoch, created_at, account_username, office_id FROM zs_users WHERE id = ?",
                &[user_id.into()],
            )
            .ok()??;
        Self::clean_user(Some(row))
    }

    pub fn get_user_by_username(&self, username: &str) -> ZsResult<Option<Value>> {
        let mut g = self.inner.lock().unwrap();
        let u = slugify(username);
        let row = g.query_one(
            "SELECT id, username, display_name, role, active, sess_epoch, created_at, account_username, office_id FROM zs_users WHERE username = ?",
            &[u.into()],
        )?;
        Ok(Self::clean_user(row))
    }

    pub fn list_users(&self, active_only: bool, office_id: Option<&str>) -> ZsResult<Vec<Value>> {
        let mut g = self.inner.lock().unwrap();
        let mut conds = Vec::new();
        let mut params: Vec<SqlParam> = Vec::new();
        if active_only {
            conds.push("active = 1");
        }
        if let Some(oid) = office_id {
            if !oid.is_empty() {
                conds.push("office_id = ?");
                params.push(oid.into());
            } else {
                conds.push("(office_id IS NULL OR office_id = '')");
            }
        }
        let where_sql = if conds.is_empty() {
            String::new()
        } else {
            format!(" WHERE {}", conds.join(" AND "))
        };
        let sql = format!(
            "SELECT id, username, display_name, role, active, created_at, account_username, office_id FROM zs_users{} ORDER BY role, display_name",
            where_sql
        );
        g.query_all(&sql, &params)
    }

    pub fn user_names(&self, user_ids: Option<&[i64]>) -> ZsResult<HashMap<i64, String>> {
        let mut g = self.inner.lock().unwrap();
        let rows = if let Some(ids) = user_ids {
            if ids.is_empty() {
                return Ok(HashMap::new());
            }
            let ph = vec!["?"; ids.len()].join(",");
            let params: Vec<SqlParam> = ids.iter().map(|&i| SqlParam::Int(i)).collect();
            g.query_all(
                &format!("SELECT id, display_name FROM zs_users WHERE id IN ({})", ph),
                &params,
            )?
        } else {
            g.query_all("SELECT id, display_name FROM zs_users", &[])?
        };
        let mut map = HashMap::new();
        for r in rows {
            if let (Some(id), Some(dn)) = (r["id"].as_i64(), r["display_name"].as_str()) {
                map.insert(id, dn.to_string());
            }
        }
        Ok(map)
    }

    pub fn sync_account(&self, acct: &Value) -> ZsResult<Option<Value>> {
        let mut g = self.inner.lock().unwrap();
        let au = acct
            .get("username")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        if au.is_empty() {
            return Ok(None);
        }
        let office_id = acct
            .get("office_id")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .filter(|s| !s.is_empty());
        let display_name_raw = acct
            .get("display_name")
            .and_then(|v| v.as_str())
            .unwrap_or(&au)
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ");
        let dn: String = display_name_raw.chars().take(60).collect();
        let dn = if dn.is_empty() { au.clone() } else { dn };
        let role = if acct.get("office_role").and_then(|v| v.as_str()) == Some("manager") {
            "admin"
        } else {
            "user"
        };
        let active = if acct.get("active").and_then(|v| v.as_bool()).unwrap_or(true) {
            1i64
        } else {
            0i64
        };

        let existing = g.query_one(
            "SELECT id, username, display_name, role, active, office_id FROM zs_users WHERE account_username = ?",
            &[au.as_str().into()],
        )?;
        let uid = if let Some(row) = existing {
            let id = row["id"].as_i64().unwrap_or(0);
            if row["display_name"].as_str() != Some(&dn)
                || row["role"].as_str() != Some(role)
                || row["active"].as_i64() != Some(active)
                || row["office_id"].as_str() != office_id.as_deref()
            {
                g.exec(
                    "UPDATE zs_users SET display_name=?, role=?, active=?, office_id=? WHERE id=?",
                    &[
                        dn.as_str().into(),
                        role.into(),
                        active.into(),
                        office_id.clone().into(),
                        id.into(),
                    ],
                )?;
            }
            id
        } else {
            let base = if slugify(&au).is_empty() {
                "uye".to_string()
            } else {
                slugify(&au)
            };
            let mut cand = base.clone();
            let mut n = 2;
            while g
                .query_one("SELECT 1 AS x FROM zs_users WHERE username=?", &[cand.as_str().into()])?
                .is_some()
            {
                cand = format!("{}_{}", base, n);
                n += 1;
            }
            let mut rnd = [0u8; 24];
            rand::thread_rng().fill_bytes(&mut rnd);
            let dummy_pw = format!("!{}", URL_SAFE_NO_PAD.encode(rnd));
            let id = g.insert_returning_id(
                "INSERT INTO zs_users(username, display_name, pw_hash, role, active, created_at, account_username, office_id) VALUES (?,?,?,?,?,?,?,?)",
                &[
                    cand.into(),
                    dn.into(),
                    dummy_pw.into(),
                    role.into(),
                    active.into(),
                    now_ts().into(),
                    au.into(),
                    office_id.into(),
                ],
            )?;
            Self::ev(&mut g, None, Some(id), "user_synced", Some(json!({"account": acct["username"]})))?;
            id
        };
        drop(g);
        Ok(self.get_user(uid))
    }

    pub fn legacy_users(&self) -> ZsResult<Vec<Value>> {
        let mut g = self.inner.lock().unwrap();
        g.query_all(
            "SELECT id, username, display_name, role, active, created_at FROM zs_users WHERE account_username IS NULL ORDER BY role, id",
            &[],
        )
    }

    pub fn used_accounts(&self) -> ZsResult<HashSet<String>> {
        let mut g = self.inner.lock().unwrap();
        let rows = g.query_all(
            "SELECT account_username FROM zs_users WHERE account_username IS NOT NULL",
            &[],
        )?;
        Ok(rows
            .into_iter()
            .filter_map(|r| r["account_username"].as_str().map(|s| s.to_string()))
            .collect())
    }

    pub fn link_account(
        &self,
        user_id: i64,
        account_username: Option<&str>,
        office_id: Option<&str>,
    ) -> ZsResult<()> {
        let mut g = self.inner.lock().unwrap();
        let u = g
            .query_one(
                "SELECT id, account_username FROM zs_users WHERE id=?",
                &[user_id.into()],
            )?
            .ok_or_else(|| ZsError("Kullanıcı bulunamadı.".to_string()))?;
        let au = account_username
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty());
        let oid = office_id
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty());
        if let Some(ref au_str) = au {
            let clash = g.query_one(
                "SELECT id FROM zs_users WHERE account_username=? AND id<>?",
                &[au_str.as_str().into(), user_id.into()],
            )?;
            if clash.is_some() {
                return Err(ZsError("Bu CokluUyap hesabı başka bir iş kullanıcısına bağlı.".to_string()));
            }
        }
        g.exec(
            "UPDATE zs_users SET account_username=?, office_id=? WHERE id=?",
            &[au.clone().into(), oid.clone().into(), user_id.into()],
        )?;
        g.exec(
            "UPDATE zs_tasks SET office_id=? WHERE created_by=? AND (office_id IS NULL OR office_id='')",
            &[oid.clone().into(), user_id.into()],
        )?;
        Self::ev(
            &mut g,
            None,
            None,
            "user_linked",
            Some(json!({"user_id": user_id, "account": au, "office_id": oid, "prev": u["account_username"]})),
        )?;
        Ok(())
    }

    pub fn sso_code_new(&self, user_id: i64, ttl: i64) -> ZsResult<String> {
        let mut g = self.inner.lock().unwrap();
        let now = now_ts();
        g.exec("DELETE FROM zs_sso_codes WHERE expires_at < ?", &[now.into()])?;
        let mut buf = [0u8; 24];
        rand::thread_rng().fill_bytes(&mut buf);
        let code = URL_SAFE_NO_PAD.encode(buf);
        g.exec(
            "INSERT INTO zs_sso_codes(code, user_id, expires_at) VALUES (?,?,?)",
            &[code.as_str().into(), user_id.into(), (now + ttl).into()],
        )?;
        Ok(code)
    }

    pub fn sso_code_use(&self, code: &str) -> ZsResult<Option<Value>> {
        let mut g = self.inner.lock().unwrap();
        let code = code.trim();
        if code.is_empty() {
            return Ok(None);
        }
        let row = g.query_one(
            "SELECT user_id, expires_at FROM zs_sso_codes WHERE code=?",
            &[code.into()],
        )?;
        let Some(row) = row else {
            return Ok(None);
        };
        g.exec("DELETE FROM zs_sso_codes WHERE code=?", &[code.into()])?;
        if row["expires_at"].as_i64().unwrap_or(0) < now_ts() {
            return Ok(None);
        }
        let uid = row["user_id"].as_i64().unwrap_or(0);
        drop(g);
        let u = self.get_user(uid);
        if let Some(ref user) = u {
            if user["active"].as_i64() == Some(1) {
                return Ok(u);
            }
        }
        Ok(None)
    }

    pub fn create_user(
        &self,
        username: &str,
        display_name: &str,
        password: &str,
        role: &str,
        actor_id: Option<i64>,
    ) -> ZsResult<Value> {
        let u = slugify(username);
        if u.len() < 2 {
            return Err(ZsError("Kullanıcı adı en az 2 karakter olmalı.".to_string()));
        }
        if !ROLES.contains(&role) {
            return Err(ZsError("Geçersiz rol.".to_string()));
        }
        let dn: String = display_name
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
            .chars()
            .take(60)
            .collect();
        let dn = if dn.is_empty() { u.clone() } else { dn };
        if password.len() < 8 {
            return Err(ZsError("Şifre en az 8 karakter olmalı.".to_string()));
        }
        let pw_hash = hash_password(password, None);
        let mut g = self.inner.lock().unwrap();
        if g.query_one("SELECT 1 AS x FROM zs_users WHERE username = ?", &[u.as_str().into()])?
            .is_some()
        {
            return Err(ZsError("Bu kullanıcı adı zaten var.".to_string()));
        }
        let uid = g.insert_returning_id(
            "INSERT INTO zs_users(username, display_name, pw_hash, role, active, created_at) VALUES (?,?,?,?,1,?)",
            &[u.as_str().into(), dn.into(), pw_hash.into(), role.into(), now_ts().into()],
        )?;
        Self::ev(&mut g, None, actor_id, "user_created", Some(json!({"user_id": uid, "username": u, "role": role})))?;
        drop(g);
        Ok(self.get_user(uid).unwrap_or(Value::Null))
    }

    pub fn authenticate(&self, username: &str, password: &str) -> ZsResult<Option<Value>> {
        let mut g = self.inner.lock().unwrap();
        let u = slugify(username);
        let row = g.query_one("SELECT * FROM zs_users WHERE username = ?", &[u.into()])?;
        let Some(row) = row else {
            return Ok(None);
        };
        if row["active"].as_i64() != Some(1) {
            return Ok(None);
        }
        let pw_hash = row["pw_hash"].as_str().unwrap_or("");
        if !verify_password(password, pw_hash) {
            return Ok(None);
        }
        Ok(Self::clean_user(Some(row)))
    }

    pub fn set_password(&self, user_id: i64, new_password: &str, actor_id: Option<i64>) -> ZsResult<()> {
        if new_password.len() < 8 {
            return Err(ZsError("Şifre en az 8 karakter olmalı.".to_string()));
        }
        let pw_hash = hash_password(new_password, None);
        let mut g = self.inner.lock().unwrap();
        let cur = g
            .query_one("SELECT id FROM zs_users WHERE id = ?", &[user_id.into()])?
            .ok_or_else(|| ZsError("Kullanıcı bulunamadı.".to_string()))?;
        let _ = cur;
        g.exec(
            "UPDATE zs_users SET pw_hash = ?, sess_epoch = sess_epoch + 1 WHERE id = ?",
            &[pw_hash.into(), user_id.into()],
        )?;
        Self::ev(&mut g, None, actor_id, "password_changed", Some(json!({"user_id": user_id})))?;
        Ok(())
    }

    pub fn update_user(
        &self,
        user_id: i64,
        display_name: Option<&str>,
        role: Option<&str>,
        actor_id: Option<i64>,
    ) -> ZsResult<Value> {
        let mut g = self.inner.lock().unwrap();
        let cur = g
            .query_one(
                "SELECT id, display_name, role FROM zs_users WHERE id = ?",
                &[user_id.into()],
            )?
            .ok_or_else(|| ZsError("Kullanıcı bulunamadı.".to_string()))?;
        let dn = if let Some(d) = display_name {
            let s: String = d
                .split_whitespace()
                .collect::<Vec<_>>()
                .join(" ")
                .chars()
                .take(60)
                .collect();
            if s.is_empty() {
                return Err(ZsError("Görünen ad boş olamaz.".to_string()));
            }
            s
        } else {
            cur["display_name"].as_str().unwrap_or("").to_string()
        };
        let r = if let Some(r_in) = role {
            if !ROLES.contains(&r_in) {
                return Err(ZsError("Geçersiz rol.".to_string()));
            }
            r_in.to_string()
        } else {
            cur["role"].as_str().unwrap_or("user").to_string()
        };
        if cur["role"].as_str() == Some("admin") && r != "admin" {
            let admins = g
                .query_one("SELECT COUNT(*) AS n FROM zs_users WHERE role='admin' AND active=1", &[])?
                .and_then(|x| x["n"].as_i64())
                .unwrap_or(0);
            if admins <= 1 {
                return Err(ZsError("Son aktif yönetici düşürülemez.".to_string()));
            }
        }
        let bump = if r != cur["role"].as_str().unwrap_or("") {
            1
        } else {
            0
        };
        g.exec(
            "UPDATE zs_users SET display_name = ?, role = ?, sess_epoch = sess_epoch + ? WHERE id = ?",
            &[dn.into(), r.clone().into(), bump.into(), user_id.into()],
        )?;
        Self::ev(&mut g, None, actor_id, "user_updated", Some(json!({"user_id": user_id, "role": r})))?;
        drop(g);
        Ok(self.get_user(user_id).unwrap_or(Value::Null))
    }

    pub fn set_active(&self, user_id: i64, active: bool, actor_id: Option<i64>) -> ZsResult<()> {
        let mut g = self.inner.lock().unwrap();
        let cur = g
            .query_one("SELECT role, active FROM zs_users WHERE id = ?", &[user_id.into()])?
            .ok_or_else(|| ZsError("Kullanıcı bulunamadı.".to_string()))?;
        if !active && cur["role"].as_str() == Some("admin") {
            let admins = g
                .query_one("SELECT COUNT(*) AS n FROM zs_users WHERE role='admin' AND active=1", &[])?
                .and_then(|x| x["n"].as_i64())
                .unwrap_or(0);
            if admins <= 1 {
                return Err(ZsError("Son aktif yönetici pasife alınamaz.".to_string()));
            }
        }
        let val = if active { 1i64 } else { 0i64 };
        let bump = if !active { 1i64 } else { 0i64 };
        g.exec(
            "UPDATE zs_users SET active = ?, sess_epoch = sess_epoch + ? WHERE id = ?",
            &[val.into(), bump.into(), user_id.into()],
        )?;
        Self::ev(&mut g, None, actor_id, "user_active", Some(json!({"user_id": user_id, "active": active})))?;
        Ok(())
    }

    pub fn sync_from_account_store(&self, acct_store: &mut AccountStore) -> ZsResult<()> {
        for (uname, rec) in &acct_store.users {
            let mut a = rec.clone();
            if let Some(obj) = a.as_object_mut() {
                obj.insert("username".to_string(), json!(uname));
            }
            let _ = self.sync_account(&a);
        }
        Ok(())
    }
}

impl ZsStore {
    fn get_task_inner(inner: &mut StoreInner, task_id: i64) -> ZsResult<Option<Value>> {
        inner.query_one("SELECT * FROM zs_tasks WHERE id = ?", &[task_id.into()])
    }

    pub fn get_task(&self, task_id: i64) -> Option<Value> {
        let mut g = self.inner.lock().ok()?;
        Self::get_task_inner(&mut g, task_id).ok().flatten()
    }

    fn root_of_inner(inner: &mut StoreInner, task_id: i64) -> ZsResult<Option<Value>> {
        let mut t = Self::get_task_inner(inner, task_id)?;
        let mut guard = 0;
        while let Some(ref task) = t {
            let pid = task.get("parent_id").and_then(|v| v.as_i64());
            let Some(pid) = pid else {
                break;
            };
            guard += 1;
            if guard > 32 {
                break;
            }
            t = Self::get_task_inner(inner, pid)?;
        }
        Ok(t)
    }

    pub fn root_of(&self, task_id: i64) -> Option<Value> {
        let mut g = self.inner.lock().ok()?;
        Self::root_of_inner(&mut g, task_id).ok().flatten()
    }

    fn task_roles_inner(
        inner: &mut StoreInner,
        root_id: i64,
        root: Option<&Value>,
    ) -> ZsResult<(HashSet<i64>, HashSet<i64>)> {
        let r = match root {
            Some(v) => Some(v.clone()),
            None => Self::get_task_inner(inner, root_id)?,
        };
        let mut assignees = HashSet::new();
        if let Some(ref rt) = r {
            if let Some(aid) = rt.get("assignee_id").and_then(|v| v.as_i64()) {
                assignees.insert(aid);
            }
        }
        let mut approvers = HashSet::new();
        let rows = inner.query_all(
            "SELECT user_id, role FROM zs_members WHERE task_id = ?",
            &[root_id.into()],
        )?;
        for row in rows {
            if let (Some(uid), Some(role)) = (row["user_id"].as_i64(), row["role"].as_str()) {
                if role == "assignee" {
                    assignees.insert(uid);
                } else if role == "approver" {
                    approvers.insert(uid);
                }
            }
        }
        Ok((assignees, approvers))
    }

    pub fn task_roles(&self, root_id: i64, root: Option<&Value>) -> (HashSet<i64>, HashSet<i64>) {
        let mut g = self.inner.lock().unwrap();
        Self::task_roles_inner(&mut g, root_id, root).unwrap_or_default()
    }

    fn visible_to_inner(inner: &mut StoreInner, task: Option<&Value>, user: &Value) -> ZsResult<bool> {
        let Some(task) = task else { return Ok(false); };
        let tid = task["id"].as_i64().unwrap_or(0);
        let root = if task.get("parent_id").and_then(|v| v.as_i64()).is_some() {
            Self::root_of_inner(inner, tid)?
        } else {
            Some(task.clone())
        };
        let Some(root) = root else { return Ok(false); };
        let uid = user["id"].as_i64().unwrap_or(0);
        let u_office = office_of(Some(user));
        if office_of(Some(&root)) != u_office {
            return Ok(false);
        }
        if root["is_private"].as_i64().unwrap_or(0) != 0 {
            return Ok(root["created_by"].as_i64() == Some(uid));
        }
        if user["role"].as_str() == Some("admin") {
            return Ok(true);
        }
        let root_id = root["id"].as_i64().unwrap_or(0);
        let (assignees, approvers) = Self::task_roles_inner(inner, root_id, Some(&root))?;
        if assignees.contains(&uid) || approvers.contains(&uid) || root["created_by"].as_i64() == Some(uid) {
            return Ok(true);
        }
        let sub_hit = inner.query_one(
            "SELECT 1 AS x FROM zs_tasks WHERE parent_id = ? AND (assignee_id = ? OR created_by = ?) LIMIT 1",
            &[root_id.into(), uid.into(), uid.into()],
        )?;
        Ok(sub_hit.is_some())
    }

    pub fn visible_to(&self, task: Option<&Value>, user: &Value) -> bool {
        let mut g = self.inner.lock().unwrap();
        Self::visible_to_inner(&mut g, task, user).unwrap_or(false)
    }

    fn hide_cuts_inner(
        inner: &mut StoreInner,
        root_id: i64,
        user: &Value,
    ) -> ZsResult<HashMap<String, i64>> {
        if user["role"].as_str() == Some("admin") {
            return Ok(HashMap::new());
        }
        let uid = user["id"].as_i64().unwrap_or(0);
        let rows = inner.query_all(
            "SELECT by_user, hide_subtasks, hide_notes, hide_files, hide_history, cut_task, cut_note, cut_file, cut_event FROM zs_transfers WHERE task_id = ?",
            &[root_id.into()],
        )?;
        let mut cuts: HashMap<String, i64> = HashMap::new();
        for tr in rows {
            if tr["by_user"].as_i64() == Some(uid) {
                continue;
            }
            let pairs = [
                ("task", "hide_subtasks", "cut_task"),
                ("note", "hide_notes", "cut_note"),
                ("file", "hide_files", "cut_file"),
                ("event", "hide_history", "cut_event"),
                ("ev_task", "hide_subtasks", "cut_event"),
                ("ev_note", "hide_notes", "cut_event"),
                ("ev_file", "hide_files", "cut_event"),
            ];
            for (kind, flag, cut_col) in pairs {
                if tr[flag].as_i64().unwrap_or(0) != 0 {
                    let cv = tr[cut_col].as_i64().unwrap_or(0);
                    let entry = cuts.entry(kind.to_string()).or_insert(0);
                    if cv > *entry {
                        *entry = cv;
                    }
                }
            }
        }
        Ok(cuts)
    }

    pub fn item_hidden(&self, root_id: i64, user: &Value, kind: &str, item_id: i64) -> bool {
        let mut g = self.inner.lock().unwrap();
        let cuts = Self::hide_cuts_inner(&mut g, root_id, user).unwrap_or_default();
        item_id <= *cuts.get(kind).unwrap_or(&0)
    }

    fn seen_map_inner(
        inner: &mut StoreInner,
        user_id: i64,
        root_ids: &[i64],
    ) -> ZsResult<HashMap<i64, (i64, i64)>> {
        if root_ids.is_empty() {
            return Ok(HashMap::new());
        }
        let ph = vec!["?"; root_ids.len()].join(",");
        let mut max_ev: HashMap<i64, i64> = HashMap::new();
        let mut seen_ev: HashMap<i64, i64> = HashMap::new();
        let p_ids: Vec<SqlParam> = root_ids.iter().map(|&i| SqlParam::Int(i)).collect();

        let ev_rows = inner.query_all(
            &format!(
                "SELECT task_id, MAX(id) AS mx FROM zs_events WHERE task_id IN ({}) GROUP BY task_id",
                ph
            ),
            &p_ids,
        )?;
        for r in ev_rows {
            if let (Some(tid), Some(mx)) = (r["task_id"].as_i64(), r["mx"].as_i64()) {
                max_ev.insert(tid, mx);
            }
        }

        let mut p_seen = vec![SqlParam::Int(user_id)];
        p_seen.extend(p_ids);
        let seen_rows = inner.query_all(
            &format!(
                "SELECT task_id, last_event FROM zs_seen WHERE user_id = ? AND task_id IN ({})",
                ph
            ),
            &p_seen,
        )?;
        for r in seen_rows {
            if let (Some(tid), Some(le)) = (r["task_id"].as_i64(), r["last_event"].as_i64()) {
                seen_ev.insert(tid, le);
            }
        }

        let mut out = HashMap::new();
        for &tid in root_ids {
            out.insert(
                tid,
                (*max_ev.get(&tid).unwrap_or(&0), *seen_ev.get(&tid).unwrap_or(&0)),
            );
        }
        Ok(out)
    }

    pub fn list_tree(&self, user: &Value, scope: Option<&str>) -> ZsResult<Vec<Value>> {
        let mut g = self.inner.lock().unwrap();
        let uid = user["id"].as_i64().unwrap_or(0);
        let u_office = office_of(Some(user));
        let mut roots = if u_office.is_empty() {
            g.query_all(
                "SELECT * FROM zs_tasks WHERE parent_id IS NULL AND (office_id IS NULL OR office_id = '') ORDER BY sort_order, id DESC",
                &[],
            )?
        } else {
            g.query_all(
                "SELECT * FROM zs_tasks WHERE parent_id IS NULL AND office_id = ? ORDER BY sort_order, id DESC",
                &[u_office.into()],
            )?
        };
        if scope == Some("private") {
            roots.retain(|t| t["is_private"].as_i64().unwrap_or(0) != 0 && t["created_by"].as_i64() == Some(uid));
        } else if scope == Some("team") {
            roots.retain(|t| t["is_private"].as_i64().unwrap_or(0) == 0);
        }
        if roots.is_empty() {
            return Ok(Vec::new());
        }

        let root_ids: Vec<i64> = roots.iter().filter_map(|t| t["id"].as_i64()).collect();
        let ph = vec!["?"; root_ids.len()].join(",");
        let p_ids: Vec<SqlParam> = root_ids.iter().map(|&i| SqlParam::Int(i)).collect();

        let subs = g.query_all(
            &format!("SELECT * FROM zs_tasks WHERE parent_id IN ({}) ORDER BY sort_order, id", ph),
            &p_ids,
        )?;
        let mut subs_by_root: HashMap<i64, Vec<Value>> = HashMap::new();
        for s in subs {
            if let Some(pid) = s["parent_id"].as_i64() {
                subs_by_root.entry(pid).or_default().push(s);
            }
        }

        let mem_rows = g.query_all(
            &format!("SELECT task_id, user_id, role FROM zs_members WHERE task_id IN ({})", ph),
            &p_ids,
        )?;
        let mut extra_assignees: HashMap<i64, Vec<i64>> = HashMap::new();
        let mut approvers: HashMap<i64, Vec<i64>> = HashMap::new();
        for m in mem_rows {
            if let (Some(tid), Some(muid), Some(role)) =
                (m["task_id"].as_i64(), m["user_id"].as_i64(), m["role"].as_str())
            {
                if role == "assignee" {
                    extra_assignees.entry(tid).or_default().push(muid);
                } else if role == "approver" {
                    approvers.entry(tid).or_default().push(muid);
                }
            }
        }

        let tag_rows = g.query_all(
            &format!("SELECT task_id, tag FROM zs_task_tags WHERE task_id IN ({}) ORDER BY created_at, tag", ph),
            &p_ids,
        )?;
        let mut tags_by_root: HashMap<i64, Vec<String>> = HashMap::new();
        for r in tag_rows {
            if let (Some(tid), Some(tag)) = (r["task_id"].as_i64(), r["tag"].as_str()) {
                tags_by_root.entry(tid).or_default().push(tag.to_string());
            }
        }

        let seen = Self::seen_map_inner(&mut g, uid, &root_ids)?;

        let mut visible_roots = Vec::new();
        for mut t in roots {
            let tid = t["id"].as_i64().unwrap_or(0);
            let priv_flag = t["is_private"].as_i64().unwrap_or(0) != 0;
            let my_subs = subs_by_root.get(&tid).cloned().unwrap_or_default();
            let ex_a = extra_assignees.get(&tid).cloned().unwrap_or_default();
            let app = approvers.get(&tid).cloned().unwrap_or_default();
            let mut all_a = Vec::new();
            if let Some(aid) = t["assignee_id"].as_i64() {
                all_a.push(aid);
            }
            for x in &ex_a {
                if !all_a.contains(x) {
                    all_a.push(*x);
                }
            }
            let is_vis = if priv_flag {
                t["created_by"].as_i64() == Some(uid)
            } else if user["role"].as_str() == Some("admin")
                || all_a.contains(&uid)
                || app.contains(&uid)
                || t["created_by"].as_i64() == Some(uid)
                || my_subs.iter().any(|s| s["assignee_id"].as_i64() == Some(uid) || s["created_by"].as_i64() == Some(uid))
            {
                true
            } else {
                false
            };
            if !is_vis {
                continue;
            }
            let cuts = Self::hide_cuts_inner(&mut g, tid, user)?;
            let cut_t = *cuts.get("task").unwrap_or(&0);
            let filt_subs: Vec<Value> = my_subs
                .into_iter()
                .filter(|s| s["id"].as_i64().unwrap_or(0) > cut_t)
                .collect();
            let (mx, sn) = seen.get(&tid).copied().unwrap_or((0, 0));
            let obj = t.as_object_mut().unwrap();
            obj.insert("subtasks".to_string(), Value::Array(filt_subs));
            obj.insert("assignees".to_string(), json!(all_a));
            obj.insert("approvers".to_string(), json!(app));
            obj.insert("tags".to_string(), json!(tags_by_root.get(&tid).cloned().unwrap_or_default()));
            obj.insert("unread".to_string(), json!(mx > sn));
            obj.insert("last_event".to_string(), json!(mx));
            visible_roots.push(t);
        }

        if visible_roots.is_empty() {
            return Ok(Vec::new());
        }

        let vis_ids: Vec<i64> = visible_roots.iter().filter_map(|t| t["id"].as_i64()).collect();
        let ph2 = vec!["?"; vis_ids.len()].join(",");
        let p_vis: Vec<SqlParam> = vis_ids.iter().map(|&i| SqlParam::Int(i)).collect();
        let note_cnts = g.query_all(
            &format!("SELECT task_id, COUNT(*) AS n FROM zs_notes WHERE task_id IN ({}) GROUP BY task_id", ph2),
            &p_vis,
        )?;
        let file_cnts = g.query_all(
            &format!("SELECT task_id, COUNT(*) AS n FROM zs_files WHERE task_id IN ({}) GROUP BY task_id", ph2),
            &p_vis,
        )?;
        let ext_cnts = g.query_all(
            &format!("SELECT task_id, COUNT(*) AS n FROM zs_extensions WHERE task_id IN ({}) AND status='bekliyor' GROUP BY task_id", ph2),
            &p_vis,
        )?;
        let note_map: HashMap<i64, i64> = note_cnts
            .into_iter()
            .filter_map(|r| Some((r["task_id"].as_i64()?, r["n"].as_i64()?)))
            .collect();
        let file_map: HashMap<i64, i64> = file_cnts
            .into_iter()
            .filter_map(|r| Some((r["task_id"].as_i64()?, r["n"].as_i64()?)))
            .collect();
        let ext_map: HashMap<i64, i64> = ext_cnts
            .into_iter()
            .filter_map(|r| Some((r["task_id"].as_i64()?, r["n"].as_i64()?)))
            .collect();

        for t in &mut visible_roots {
            let tid = t["id"].as_i64().unwrap_or(0);
            let obj = t.as_object_mut().unwrap();
            obj.insert("note_count".to_string(), json!(*note_map.get(&tid).unwrap_or(&0)));
            obj.insert("file_count".to_string(), json!(*file_map.get(&tid).unwrap_or(&0)));
            obj.insert("pending_extensions".to_string(), json!(*ext_map.get(&tid).unwrap_or(&0)));
        }
        Ok(visible_roots)
    }

    pub fn task_detail(&self, task_id: i64, user: &Value) -> ZsResult<Option<Value>> {
        let mut g = self.inner.lock().unwrap();
        let t = Self::get_task_inner(&mut g, task_id)?;
        let Some(mut t) = t else { return Ok(None); };
        if !Self::visible_to_inner(&mut g, Some(&t), user)? {
            return Ok(None);
        }
        let root_id = t["parent_id"].as_i64().unwrap_or(task_id);
        let cuts = Self::hide_cuts_inner(&mut g, root_id, user)?;
        let cut_t = *cuts.get("task").unwrap_or(&0);
        let cut_n = *cuts.get("note").unwrap_or(&0);
        let cut_f = *cuts.get("file").unwrap_or(&0);
        let cut_e = *cuts.get("event").unwrap_or(&0);

        let is_root = t["parent_id"].is_null();
        let subtasks = if is_root {
            g.query_all(
                "SELECT * FROM zs_tasks WHERE parent_id = ? AND id > ? ORDER BY sort_order, id",
                &[task_id.into(), cut_t.into()],
            )?
        } else {
            Vec::new()
        };
        let notes = g.query_all(
            "SELECT * FROM zs_notes WHERE task_id = ? AND id > ? ORDER BY COALESCE(note_at, created_at) DESC, id DESC",
            &[task_id.into(), cut_n.into()],
        )?;
        let files = g.query_all(
            "SELECT id, task_id, uploader_id, filename, mime, size, sha256, created_at FROM zs_files WHERE task_id = ? AND id > ? ORDER BY id DESC",
            &[task_id.into(), cut_f.into()],
        )?;
        let raw_events = g.query_all(
            "SELECT * FROM zs_events WHERE task_id = ? AND id > ? ORDER BY id DESC LIMIT 120",
            &[task_id.into(), cut_e.into()],
        )?;
        let event_kind_of = |k: &str| -> Option<&str> {
            match k {
                "note" => Some("ev_note"),
                "file" | "file_deleted" => Some("ev_file"),
                "subtask" | "subtask_deleted" => Some("ev_task"),
                _ => None,
            }
        };
        let events: Vec<Value> = raw_events
            .into_iter()
            .filter(|ev| {
                let k = ev["kind"].as_str().unwrap_or("");
                if let Some(ek) = event_kind_of(k) {
                    ev["id"].as_i64().unwrap_or(0) > *cuts.get(ek).unwrap_or(&0)
                } else {
                    true
                }
            })
            .collect();

        let (assignees_set, approvers_set) = Self::task_roles_inner(&mut g, root_id, None)?;
        let mut assignees: Vec<i64> = Vec::new();
        if let Some(aid) = t["assignee_id"].as_i64() {
            assignees.push(aid);
        }
        for a in assignees_set {
            if !assignees.contains(&a) {
                assignees.push(a);
            }
        }
        let mut approvers: Vec<i64> = approvers_set.into_iter().collect();
        approvers.sort();

        let extensions = if is_root {
            g.query_all(
                "SELECT * FROM zs_extensions WHERE task_id = ? ORDER BY id DESC",
                &[task_id.into()],
            )?
        } else {
            Vec::new()
        };
        let tags: Vec<String> = if is_root {
            g.query_all(
                "SELECT tag FROM zs_task_tags WHERE task_id = ? ORDER BY created_at, tag",
                &[task_id.into()],
            )?
            .into_iter()
            .filter_map(|r| r["tag"].as_str().map(|s| s.to_string()))
            .collect()
        } else {
            Vec::new()
        };

        let obj = t.as_object_mut().unwrap();
        obj.insert("subtasks".to_string(), Value::Array(subtasks));
        obj.insert("notes".to_string(), Value::Array(notes));
        obj.insert("files".to_string(), Value::Array(files));
        obj.insert("events".to_string(), Value::Array(events));
        obj.insert("assignees".to_string(), json!(assignees));
        obj.insert("approvers".to_string(), json!(approvers));
        obj.insert("extensions".to_string(), Value::Array(extensions));
        obj.insert("tags".to_string(), json!(tags));
        Ok(Some(t))
    }

    pub fn add_member(&self, task_id: i64, user_id: i64, role: &str, actor: &Value) -> ZsResult<()> {
        if role != "assignee" && role != "approver" {
            return Err(ZsError("Geçersiz üye rolü.".to_string()));
        }
        if actor["role"].as_str() != Some("admin") {
            return Err(ZsError("Yalnız yönetici görevli / onay yetkilisi ekler.".to_string()));
        }
        let mut g = self.inner.lock().unwrap();
        let t = Self::get_task_inner(&mut g, task_id)?
            .ok_or_else(|| ZsError("İş bulunamadı.".to_string()))?;
        if !t["parent_id"].is_null() {
            return Err(ZsError("Ek üye yalnız ana işe eklenir.".to_string()));
        }
        if t["is_private"].as_i64().unwrap_or(0) != 0 {
            return Err(ZsError("Özel işe üye eklenemez.".to_string()));
        }
        let u = g
            .query_one(
                "SELECT id, display_name, office_id FROM zs_users WHERE id = ? AND active = 1",
                &[user_id.into()],
            )?
            .ok_or_else(|| ZsError("Kullanıcı aktif değil.".to_string()))?;
        if office_of(Some(&u)) != office_of(Some(&t)) {
            return Err(ZsError("Farklı bürodan kişi eklenemez.".to_string()));
        }
        if role == "assignee" && t["assignee_id"].as_i64() == Some(user_id) {
            return Ok(());
        }
        let exists = g.query_one(
            "SELECT 1 AS x FROM zs_members WHERE task_id=? AND user_id=? AND role=?",
            &[task_id.into(), user_id.into(), role.into()],
        )?;
        if exists.is_some() {
            return Ok(());
        }
        let actor_id = actor["id"].as_i64().unwrap_or(0);
        g.exec(
            "INSERT INTO zs_members(task_id, user_id, role, added_by, created_at) VALUES (?,?,?,?,?)",
            &[task_id.into(), user_id.into(), role.into(), actor_id.into(), now_ts().into()],
        )?;
        Self::ev(
            &mut g,
            Some(task_id),
            Some(actor_id),
            "member_added",
            Some(json!({"user_id": user_id, "user_name": u["display_name"], "role": role})),
        )?;
        Ok(())
    }

    pub fn remove_member(&self, task_id: i64, user_id: i64, role: &str, actor: &Value) -> ZsResult<()> {
        if role != "assignee" && role != "approver" {
            return Err(ZsError("Geçersiz üye rolü.".to_string()));
        }
        if actor["role"].as_str() != Some("admin") {
            return Err(ZsError("Yalnız yönetici çıkarır.".to_string()));
        }
        let mut g = self.inner.lock().unwrap();
        let t = Self::get_task_inner(&mut g, task_id)?
            .ok_or_else(|| ZsError("İş bulunamadı.".to_string()))?;
        let u = g.query_one("SELECT display_name FROM zs_users WHERE id=?", &[user_id.into()])?;
        let uname = u.and_then(|x| x["display_name"].as_str().map(|s| s.to_string()));
        let actor_id = actor["id"].as_i64().unwrap_or(0);
        if role == "assignee" && t["assignee_id"].as_i64() == Some(user_id) {
            let extra = g.query_one(
                "SELECT id, user_id FROM zs_members WHERE task_id=? AND role='assignee' ORDER BY id LIMIT 1",
                &[task_id.into()],
            )?;
            let next_a = extra.as_ref().and_then(|x| x["user_id"].as_i64());
            if let Some(ex) = extra {
                if let Some(eid) = ex["id"].as_i64() {
                    g.exec("DELETE FROM zs_members WHERE id=?", &[eid.into()])?;
                }
            }
            g.exec(
                "UPDATE zs_tasks SET assignee_id=?, updated_at=? WHERE id=?",
                &[next_a.into(), now_ts().into(), task_id.into()],
            )?;
        } else {
            g.exec(
                "DELETE FROM zs_members WHERE task_id=? AND user_id=? AND role=?",
                &[task_id.into(), user_id.into(), role.into()],
            )?;
        }
        Self::ev(
            &mut g,
            Some(task_id),
            Some(actor_id),
            "member_removed",
            Some(json!({"user_id": user_id, "user_name": uname, "role": role})),
        )?;
        Ok(())
    }

    pub fn request_extension(
        &self,
        task_id: i64,
        new_due: i64,
        reason: &str,
        actor: &Value,
    ) -> ZsResult<Value> {
        let mut g = self.inner.lock().unwrap();
        let t = Self::get_task_inner(&mut g, task_id)?
            .ok_or_else(|| ZsError("İş bulunamadı.".to_string()))?;
        if !t["parent_id"].is_null() {
            return Err(ZsError("Ek süre yalnız ana iş için istenir.".to_string()));
        }
        if t["is_private"].as_i64().unwrap_or(0) != 0 {
            return Err(ZsError("Özel işte ek süre istenmez.".to_string()));
        }
        if !Self::visible_to_inner(&mut g, Some(&t), actor)? {
            return Err(ZsError("Bu işe erişiminiz yok.".to_string()));
        }
        let st = t["status"].as_str().unwrap_or("");
        if DONE_STATUSES.contains(&st) {
            return Err(ZsError("Tamamlanmış iş için ek süre istenemez.".to_string()));
        }
        let reason = reason.trim();
        if reason.is_empty() {
            return Err(ZsError("Gerekçe boş olamaz.".to_string()));
        }
        let old_due = t["due_at"].as_i64();
        if let Some(od) = old_due {
            if new_due <= od {
                return Err(ZsError("Yeni tarih mevcut son tarihten sonra olmalı.".to_string()));
            }
        }
        let actor_id = actor["id"].as_i64().unwrap_or(0);
        let eid = g.insert_returning_id(
            "INSERT INTO zs_extensions(task_id, requester_id, old_due, new_due, reason, status, created_at) VALUES (?,?,?,?,?,'bekliyor',?)",
            &[
                task_id.into(),
                actor_id.into(),
                old_due.into(),
                new_due.into(),
                reason.chars().take(1000).collect::<String>().into(),
                now_ts().into(),
            ],
        )?;
        Self::ev(
            &mut g,
            Some(task_id),
            Some(actor_id),
            "extension_requested",
            Some(json!({"id": eid, "new_due": new_due, "reason": reason.chars().take(160).collect::<String>()})),
        )?;
        Ok(g.query_one("SELECT * FROM zs_extensions WHERE id=?", &[eid.into()])?.unwrap_or(Value::Null))
    }

    pub fn get_extension(&self, ext_id: i64) -> Option<Value> {
        let mut g = self.inner.lock().ok()?;
        g.query_one("SELECT * FROM zs_extensions WHERE id=?", &[ext_id.into()]).ok().flatten()
    }

    pub fn decide_extension(
        &self,
        ext_id: i64,
        decision: &str,
        note: &str,
        actor: &Value,
    ) -> ZsResult<Value> {
        if decision != "kabul" && decision != "ret" {
            return Err(ZsError("Karar 'kabul' veya 'ret' olmalı.".to_string()));
        }
        let mut g = self.inner.lock().unwrap();
        let ex = g
            .query_one("SELECT * FROM zs_extensions WHERE id=?", &[ext_id.into()])?
            .ok_or_else(|| ZsError("Ek süre talebi bulunamadı.".to_string()))?;
        if ex["status"].as_str() != Some("bekliyor") {
            return Err(ZsError("Bu talep zaten sonuçlanmış.".to_string()));
        }
        let tid = ex["task_id"].as_i64().unwrap_or(0);
        let t = Self::get_task_inner(&mut g, tid)?
            .ok_or_else(|| ZsError("İş bulunamadı.".to_string()))?;
        if office_of(Some(&t)) != office_of(Some(actor)) {
            return Err(ZsError("Bu işe erişiminiz yok.".to_string()));
        }
        let (_, approvers) = Self::task_roles_inner(&mut g, tid, Some(&t))?;
        let actor_id = actor["id"].as_i64().unwrap_or(0);
        if actor["role"].as_str() != Some("admin") && !approvers.contains(&actor_id) {
            return Err(ZsError("Ek süre talebini yalnız yönetici veya işin onay yetkilisi karara bağlar.".to_string()));
        }
        let now = now_ts();
        let note_clean: String = note.trim().chars().take(1000).collect();
        g.exec(
            "UPDATE zs_extensions SET status=?, decided_by=?, decision_note=?, decided_at=? WHERE id=?",
            &[decision.into(), actor_id.into(), note_clean.as_str().into(), now.into(), ext_id.into()],
        )?;
        if decision == "kabul" {
            g.exec(
                "UPDATE zs_tasks SET due_at=?, updated_at=? WHERE id=?",
                &[ex["new_due"].as_i64().into(), now.into(), tid.into()],
            )?;
        }
        Self::ev(
            &mut g,
            Some(tid),
            Some(actor_id),
            "extension_decided",
            Some(json!({"id": ext_id, "decision": decision, "new_due": ex["new_due"], "note": note_clean.chars().take(160).collect::<String>()})),
        )?;
        Ok(g.query_one("SELECT * FROM zs_extensions WHERE id=?", &[ext_id.into()])?.unwrap_or(Value::Null))
    }

    pub fn mark_seen(&self, task_id: i64, user: &Value) -> ZsResult<()> {
        let mut g = self.inner.lock().unwrap();
        let t = Self::get_task_inner(&mut g, task_id)?;
        let Some(t) = t else { return Ok(()); };
        if !Self::visible_to_inner(&mut g, Some(&t), user)? {
            return Ok(());
        }
        let root_id = t["parent_id"].as_i64().unwrap_or(task_id);
        let mx = g
            .query_one("SELECT COALESCE(MAX(id),0) AS mx FROM zs_events WHERE task_id=?", &[root_id.into()])?
            .and_then(|r| r["mx"].as_i64())
            .unwrap_or(0);
        let uid = user["id"].as_i64().unwrap_or(0);
        g.exec(
            "INSERT INTO zs_seen(user_id, task_id, last_event) VALUES (?,?,?) ON CONFLICT(user_id, task_id) DO UPDATE SET last_event = excluded.last_event",
            &[uid.into(), root_id.into(), mx.into()],
        )?;
        Ok(())
    }

    pub fn transfer(
        &self,
        task_id: i64,
        to_user_id: i64,
        actor: &Value,
        hide_subtasks: bool,
        hide_notes: bool,
        hide_files: bool,
        hide_history: bool,
    ) -> ZsResult<()> {
        let mut g = self.inner.lock().unwrap();
        let t = Self::get_task_inner(&mut g, task_id)?
            .ok_or_else(|| ZsError("İş bulunamadı.".to_string()))?;
        if !t["parent_id"].is_null() {
            return Err(ZsError("Devir yalnız ana iş için yapılır.".to_string()));
        }
        if t["is_private"].as_i64().unwrap_or(0) != 0 {
            return Err(ZsError("Özel iş devredilemez.".to_string()));
        }
        if !Self::visible_to_inner(&mut g, Some(&t), actor)? {
            return Err(ZsError("Bu işe erişiminiz yok.".to_string()));
        }
        let actor_id = actor["id"].as_i64().unwrap_or(0);
        if actor["role"].as_str() != Some("admin") && t["assignee_id"].as_i64() != Some(actor_id) {
            return Err(ZsError("İşi yalnız mevcut görevli veya yönetici devredebilir.".to_string()));
        }
        let target = g
            .query_one(
                "SELECT id, display_name, office_id FROM zs_users WHERE id = ? AND active = 1",
                &[to_user_id.into()],
            )?
            .ok_or_else(|| ZsError("Devredilecek kişi bulunamadı veya pasif.".to_string()))?;
        if office_of(Some(&target)) != office_of(Some(&t)) {
            return Err(ZsError("Farklı bürodan kişiye devredilemez.".to_string()));
        }
        let cut_t = g.query_one("SELECT COALESCE(MAX(id),0) AS m FROM zs_tasks", &[])?.and_then(|r| r["m"].as_i64()).unwrap_or(0);
        let cut_n = g.query_one("SELECT COALESCE(MAX(id),0) AS m FROM zs_notes WHERE task_id=?", &[task_id.into()])?.and_then(|r| r["m"].as_i64()).unwrap_or(0);
        let cut_f = g.query_one("SELECT COALESCE(MAX(id),0) AS m FROM zs_files WHERE task_id=?", &[task_id.into()])?.and_then(|r| r["m"].as_i64()).unwrap_or(0);
        let cut_e = g.query_one("SELECT COALESCE(MAX(id),0) AS m FROM zs_events WHERE task_id=?", &[task_id.into()])?.and_then(|r| r["m"].as_i64()).unwrap_or(0);

        let now = now_ts();
        let from_u = t["assignee_id"].as_i64();
        g.exec(
            "INSERT INTO zs_transfers(task_id, from_user, to_user, by_user, hide_subtasks, hide_notes, hide_files, hide_history, cut_task, cut_note, cut_file, cut_event, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            &[
                task_id.into(),
                from_u.into(),
                to_user_id.into(),
                actor_id.into(),
                hide_subtasks.into(),
                hide_notes.into(),
                hide_files.into(),
                hide_history.into(),
                cut_t.into(),
                cut_n.into(),
                cut_f.into(),
                cut_e.into(),
                now.into(),
            ],
        )?;
        g.exec(
            "UPDATE zs_tasks SET assignee_id=?, updated_at=? WHERE id=?",
            &[to_user_id.into(), now.into(), task_id.into()],
        )?;
        g.exec(
            "DELETE FROM zs_members WHERE task_id=? AND user_id=? AND role='assignee'",
            &[task_id.into(), to_user_id.into()],
        )?;
        Self::ev(
            &mut g,
            Some(task_id),
            Some(actor_id),
            "transferred",
            Some(json!({
                "from": from_u,
                "to": to_user_id,
                "to_name": target["display_name"],
                "hidden": {
                    "subtasks": hide_subtasks,
                    "notes": hide_notes,
                    "files": hide_files,
                    "history": hide_history
                }
            })),
        )?;
        Ok(())
    }

    pub fn create_task(
        &self,
        title: &str,
        description: &str,
        assignee_id: Option<i64>,
        due_at: Option<i64>,
        actor: &Value,
        parent_id: Option<i64>,
        is_private: bool,
        priority: i64,
        dosya: Option<&Value>,
        sablon: Option<&str>,
        asama: Option<&str>,
    ) -> ZsResult<Value> {
        let title = title.trim();
        if title.is_empty() {
            return Err(ZsError("Başlık boş olamaz.".to_string()));
        }
        let title_c: String = title.chars().take(200).collect();
        let desc_c: String = description.trim().chars().take(10000).collect();

        let mut g = self.inner.lock().unwrap();
        let mut priv_flag = is_private;
        let mut prio = priority;
        let mut office = office_of(Some(actor));
        let mut d = clean_dosya(dosya);

        if let Some(pid) = parent_id {
            let parent = Self::get_task_inner(&mut g, pid)?
                .ok_or_else(|| ZsError("Ana iş bulunamadı.".to_string()))?;
            if !parent["parent_id"].is_null() {
                return Err(ZsError("Alt işin altına alt iş açılamaz (tek seviye).".to_string()));
            }
            if !Self::visible_to_inner(&mut g, Some(&parent), actor)? {
                return Err(ZsError("Bu işe erişiminiz yok.".to_string()));
            }
            priv_flag = parent["is_private"].as_i64().unwrap_or(0) != 0;
            office = office_of(Some(&parent));
            d = None;
        }

        let actor_id = actor["id"].as_i64().unwrap_or(0);
        let final_assignee = if priv_flag && parent_id.is_none() {
            if actor["role"].as_str() == Some("admin") {
                return Err(ZsError("Özel iş alanı yalnız çalışanlar içindir.".to_string()));
            }
            Some(actor_id)
        } else {
            if !PRIORITIES.contains(&prio) {
                prio = 1;
            }
            if let Some(aid) = assignee_id {
                let u = g
                    .query_one("SELECT id, office_id FROM zs_users WHERE id=? AND active=1", &[aid.into()])?
                    .ok_or_else(|| ZsError("Atanacak kişi bulunamadı veya pasif.".to_string()))?;
                if office_of(Some(&u)) != office {
                    return Err(ZsError("Farklı bürodan kişiye atanamaz.".to_string()));
                }
                Some(aid)
            } else {
                None
            }
        };
        if parent_id.is_some() {
            prio = 1;
        }

        let (mut sab_key, mut surec, mut asama_key) = (None::<String>, None::<String>, None::<String>);
        let mut sab_tags: Vec<String> = Vec::new();
        let mut asama_input = asama.map(|s| s.to_string());
        if parent_id.is_none() {
            if let Some(sk) = sablon {
                let sk_t = sk.trim();
                if !sk_t.is_empty() {
                    let s_obj = Self::resolve_sablon_inner(&mut g, sk_t, &office)?;
                    if let Some(s) = s_obj {
                        sab_key = Some(s["id"].as_str().unwrap_or(sk_t).to_string());
                        if asama_input.as_deref().unwrap_or("").is_empty() {
                            asama_input = s["asama"].as_str().map(|x| x.to_string());
                        }
                        if let Some(arr) = s["etiketler"].as_array() {
                            for e in arr {
                                if let Some(es) = e.as_str() {
                                    sab_tags.push(es.to_string());
                                }
                            }
                        }
                    }
                }
            } else if let Some(m_id) = Self::match_sablon_inner(&mut g, &title_c, &office)? {
                sab_key = Some(m_id);
            }
            if let Some(ref ai) = asama_input {
                if !ai.trim().is_empty() {
                    let (su, ak) = asama_coz(Some(ai))?;
                    surec = su;
                    asama_key = ak;
                }
            }
        }

        let now = now_ts();
        let d_get = |k: &str| -> Option<String> {
            d.as_ref()
                .and_then(|m| m.get(k))
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
        };
        let oid_param: Option<String> = if office.is_empty() { None } else { Some(office.clone()) };
        let tid = g.insert_returning_id(
            "INSERT INTO zs_tasks(parent_id, title, description, assignee_id, created_by, due_at, status, sort_order, is_private, priority, office_id, dosya_id, birim_id, birim_adi, dosya_no, dosya_tur, dosya_uyap, evrak_id, evrak_adi, taraf_adi, barkod, sablon, surec, asama, created_at, updated_at) VALUES (?,?,?,?,?,?,'acik',0,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            &[
                parent_id.into(),
                title_c.as_str().into(),
                desc_c.as_str().into(),
                final_assignee.into(),
                actor_id.into(),
                due_at.into(),
                priv_flag.into(),
                prio.into(),
                oid_param.into(),
                d_get("dosya_id").into(),
                d_get("birim_id").into(),
                d_get("birim_adi").into(),
                d_get("dosya_no").into(),
                d_get("dosya_tur").into(),
                d_get("dosya_uyap").into(),
                d_get("evrak_id").into(),
                d_get("evrak_adi").into(),
                d_get("taraf_adi").into(),
                d_get("barkod").into(),
                sab_key.into(),
                surec.clone().into(),
                asama_key.clone().into(),
                now.into(),
                now.into(),
            ],
        )?;

        if parent_id.is_none() {
            let mut auto_tags = Vec::new();
            if let Some(ref dm) = d {
                if let Some(tur) = dosya_turu(
                    dm.get("dosya_tur").and_then(|v| v.as_str()),
                    dm.get("birim_adi").and_then(|v| v.as_str()),
                ) {
                    auto_tags.push(format!("e-{}", tur));
                }
            }
            if let Some(ref su) = surec {
                for hs in hazir_surecler() {
                    if hs["key"].as_str() == Some(su) {
                        if let Some(ek) = hs["etiket"].as_str() {
                            auto_tags.push(ek.to_string());
                        }
                    }
                }
            }
            if let Some(ae) = asama_etiketi(surec.as_deref(), asama_key.as_deref()) {
                auto_tags.push(ae);
            }
            for t in sab_tags {
                auto_tags.push(t);
            }
            let asama_set = asama_etiketleri();
            let mut seen_t = HashSet::new();
            for tg in auto_tags {
                if seen_t.contains(&tg) {
                    continue;
                }
                if asama_set.contains(&tg) && asama_etiketi(surec.as_deref(), asama_key.as_deref()).as_deref() != Some(&tg) {
                    continue;
                }
                seen_t.insert(tg.clone());
                let _ = g.exec(
                    "INSERT INTO zs_task_tags(task_id, tag, added_by, created_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                    &[tid.into(), tg.into(), actor_id.into(), now.into()],
                );
            }
        }

        let target = parent_id.unwrap_or(tid);
        let kind = if parent_id.is_some() { "subtask" } else { "created" };
        Self::ev(
            &mut g,
            Some(target),
            Some(actor_id),
            kind,
            Some(json!({
                "id": tid,
                "title": title_c,
                "assignee_id": final_assignee,
                "due_at": due_at,
                "private": priv_flag
            })),
        )?;
        Ok(Self::get_task_inner(&mut g, tid)?.unwrap_or(Value::Null))
    }

    pub fn update_task(
        &self,
        task_id: i64,
        actor: &Value,
        title: Patch<String>,
        description: Patch<String>,
        assignee_id: Patch<Option<i64>>,
        due_at: Patch<Option<i64>>,
        priority: Patch<i64>,
        dosya: Patch<Option<Value>>,
        asama: Patch<Option<String>>,
    ) -> ZsResult<Value> {
        let mut g = self.inner.lock().unwrap();
        let t = Self::get_task_inner(&mut g, task_id)?
            .ok_or_else(|| ZsError("İş bulunamadı.".to_string()))?;
        let is_sub = !t["parent_id"].is_null();
        let root = if is_sub {
            Self::root_of_inner(&mut g, task_id)?.unwrap_or_else(|| t.clone())
        } else {
            t.clone()
        };
        let is_priv = root["is_private"].as_i64().unwrap_or(0) != 0;
        let actor_id = actor["id"].as_i64().unwrap_or(0);
        if is_priv {
            if root["created_by"].as_i64() != Some(actor_id) {
                return Err(ZsError("Özel işi yalnız sahibi düzenler.".to_string()));
            }
        } else {
            if office_of(Some(&root)) != office_of(Some(actor)) {
                return Err(ZsError("Bu işe erişiminiz yok.".to_string()));
            }
            if actor["role"].as_str() != Some("admin") {
                return Err(ZsError("İşi yalnız yönetici düzenleyebilir.".to_string()));
            }
        }

        let mut fields: Vec<&str> = Vec::new();
        let mut params: Vec<SqlParam> = Vec::new();
        let mut changed: Map<String, Value> = Map::new();

        if let Patch::Set(v) = title {
            let v_t = v.trim();
            if v_t.is_empty() {
                return Err(ZsError("Başlık boş olamaz.".to_string()));
            }
            let vc: String = v_t.chars().take(200).collect();
            fields.push("title=?");
            params.push(vc.as_str().into());
            changed.insert("title".to_string(), json!(vc));
        }
        if let Patch::Set(v) = description {
            let vc: String = v.trim().chars().take(10000).collect();
            fields.push("description=?");
            params.push(vc.as_str().into());
            changed.insert("description".to_string(), json!(true));
        }
        if let Patch::Set(v) = assignee_id {
            if is_priv && !is_sub {
                return Err(ZsError("Özel iş başkasına atanamaz.".to_string()));
            }
            if let Some(aid) = v {
                let u = g
                    .query_one("SELECT id, office_id FROM zs_users WHERE id=? AND active=1", &[aid.into()])?
                    .ok_or_else(|| ZsError("Atanacak kişi bulunamadı.".to_string()))?;
                if office_of(Some(&u)) != office_of(Some(&root)) {
                    return Err(ZsError("Farklı bürodan kişiye atanamaz.".to_string()));
                }
            }
            fields.push("assignee_id=?");
            params.push(v.into());
            changed.insert("assignee_id".to_string(), json!(v));
        }
        if let Patch::Set(v) = due_at {
            fields.push("due_at=?");
            params.push(v.into());
            changed.insert("due_at".to_string(), json!(v));
        }
        if let Patch::Set(v) = priority {
            if !is_sub && PRIORITIES.contains(&v) {
                fields.push("priority=?");
                params.push(v.into());
                changed.insert("priority".to_string(), json!(v));
            }
        }
        let mut new_dosya_tur: Option<Option<String>> = None;
        if let Patch::Set(v) = dosya {
            if !is_sub {
                let d = clean_dosya(v.as_ref());
                for &k in DOSYA_FIELDS {
                    fields.push(match k {
                        "dosya_id" => "dosya_id=?",
                        "birim_id" => "birim_id=?",
                        "birim_adi" => "birim_adi=?",
                        "dosya_no" => "dosya_no=?",
                        "dosya_tur" => "dosya_tur=?",
                        "dosya_uyap" => "dosya_uyap=?",
                        "evrak_id" => "evrak_id=?",
                        "evrak_adi" => "evrak_adi=?",
                        "taraf_adi" => "taraf_adi=?",
                        "barkod" => "barkod=?",
                        _ => unreachable!(),
                    });
                    let val = d.as_ref().and_then(|m| m.get(k)).and_then(|x| x.as_str()).map(|s| s.to_string());
                    params.push(val.into());
                }
                changed.insert("dosya".to_string(), json!(d.as_ref().and_then(|m| m.get("dosya_no")).cloned().unwrap_or(Value::Null)));
                let dt = d.as_ref().and_then(|m| {
                    dosya_turu(
                        m.get("dosya_tur").and_then(|x| x.as_str()),
                        m.get("birim_adi").and_then(|x| x.as_str()),
                    )
                });
                new_dosya_tur = Some(dt);
            }
        }
        let mut new_asama_pair: Option<(Option<String>, Option<String>)> = None;
        if let Patch::Set(v) = asama {
            if !is_sub {
                let (su, ak) = asama_coz(v.as_deref())?;
                fields.push("surec=?");
                params.push(su.clone().into());
                fields.push("asama=?");
                params.push(ak.clone().into());
                changed.insert("asama".to_string(), json!(v));
                new_asama_pair = Some((su, ak));
            }
        }

        if fields.is_empty() {
            return Ok(t);
        }
        let now = now_ts();
        fields.push("updated_at=?");
        params.push(now.into());
        params.push(task_id.into());
        let sql = format!("UPDATE zs_tasks SET {} WHERE id=?", fields.join(", "));
        g.exec(&sql, &params)?;

        if let Some(dt_opt) = new_dosya_tur {
            let ana_keys: Vec<String> = dosya_turleri().iter().map(|(k, _, _, _)| format!("e-{}", k)).collect();
            let ph = vec!["?"; ana_keys.len()].join(",");
            let mut p: Vec<SqlParam> = vec![task_id.into()];
            p.extend(ana_keys.into_iter().map(SqlParam::Text));
            g.exec(&format!("DELETE FROM zs_task_tags WHERE task_id=? AND tag IN ({})", ph), &p)?;
            if let Some(dt) = dt_opt {
                g.exec(
                    "INSERT INTO zs_task_tags(task_id, tag, added_by, created_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                    &[task_id.into(), format!("e-{}", dt).into(), actor_id.into(), now.into()],
                )?;
            }
        }
        if let Some((su, ak)) = new_asama_pair {
            let a_set: Vec<String> = asama_etiketleri().into_iter().collect();
            if !a_set.is_empty() {
                let ph = vec!["?"; a_set.len()].join(",");
                let mut p: Vec<SqlParam> = vec![task_id.into()];
                p.extend(a_set.into_iter().map(SqlParam::Text));
                g.exec(&format!("DELETE FROM zs_task_tags WHERE task_id=? AND tag IN ({})", ph), &p)?;
            }
            if let Some(ref su_s) = su {
                for hs in hazir_surecler() {
                    if hs["key"].as_str() == Some(su_s) {
                        if let Some(ek) = hs["etiket"].as_str() {
                            let _ = g.exec(
                                "INSERT INTO zs_task_tags(task_id, tag, added_by, created_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                                &[task_id.into(), ek.into(), actor_id.into(), now.into()],
                            );
                        }
                    }
                }
            }
            if let Some(ae) = asama_etiketi(su.as_deref(), ak.as_deref()) {
                let _ = g.exec(
                    "INSERT INTO zs_task_tags(task_id, tag, added_by, created_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                    &[task_id.into(), ae.into(), actor_id.into(), now.into()],
                );
            }
        }

        let target = t["parent_id"].as_i64().unwrap_or(task_id);
        Self::ev(&mut g, Some(target), Some(actor_id), "updated", Some(Value::Object(changed)))?;
        Ok(Self::get_task_inner(&mut g, task_id)?.unwrap_or(Value::Null))
    }

    pub fn delete_task(&self, task_id: i64, actor: &Value) -> ZsResult<()> {
        let mut g = self.inner.lock().unwrap();
        let t = Self::get_task_inner(&mut g, task_id)?
            .ok_or_else(|| ZsError("İş bulunamadı.".to_string()))?;
        let is_sub = !t["parent_id"].is_null();
        let root = if is_sub {
            Self::root_of_inner(&mut g, task_id)?.unwrap_or_else(|| t.clone())
        } else {
            t.clone()
        };
        let is_priv = root["is_private"].as_i64().unwrap_or(0) != 0;
        let actor_id = actor["id"].as_i64().unwrap_or(0);
        if is_priv {
            if root["created_by"].as_i64() != Some(actor_id) {
                return Err(ZsError("Özel işi yalnız sahibi siler.".to_string()));
            }
        } else {
            if office_of(Some(&root)) != office_of(Some(actor)) {
                return Err(ZsError("Bu işe erişiminiz yok.".to_string()));
            }
            if actor["role"].as_str() != Some("admin") {
                return Err(ZsError("Silme yetkisi yalnız yöneticide.".to_string()));
            }
        }
        let ids_to_del: Vec<i64> = if !is_sub {
            let mut ids = vec![task_id];
            let subs = g.query_all("SELECT id FROM zs_tasks WHERE parent_id=?", &[task_id.into()])?;
            for r in subs {
                if let Some(sid) = r["id"].as_i64() {
                    ids.push(sid);
                }
            }
            ids
        } else {
            vec![task_id]
        };
        for tid in &ids_to_del {
            g.exec("DELETE FROM zs_notes WHERE task_id=?", &[(*tid).into()])?;
            g.exec("DELETE FROM zs_files WHERE task_id=?", &[(*tid).into()])?;
            g.exec("DELETE FROM zs_transfers WHERE task_id=?", &[(*tid).into()])?;
            g.exec("DELETE FROM zs_members WHERE task_id=?", &[(*tid).into()])?;
            g.exec("DELETE FROM zs_extensions WHERE task_id=?", &[(*tid).into()])?;
            g.exec("DELETE FROM zs_seen WHERE task_id=?", &[(*tid).into()])?;
            g.exec("DELETE FROM zs_reminders WHERE task_id=? OR sub_id=?", &[(*tid).into(), (*tid).into()])?;
            g.exec("DELETE FROM zs_task_tags WHERE task_id=?", &[(*tid).into()])?;
            g.exec("DELETE FROM zs_tasks WHERE id=?", &[(*tid).into()])?;
        }
        if is_sub {
            Self::ev(
                &mut g,
                t["parent_id"].as_i64(),
                Some(actor_id),
                "subtask_deleted",
                Some(json!({"id": task_id, "title": t["title"]})),
            )?;
        }
        Ok(())
    }

    pub fn transition(
        &self,
        task_id: i64,
        new_status: &str,
        actor: &Value,
        note: &str,
        sonuc: Option<&str>,
        kesinlesme_tarihi: Option<&str>,
    ) -> ZsResult<Value> {
        let mut g = self.inner.lock().unwrap();
        let t = Self::get_task_inner(&mut g, task_id)?
            .ok_or_else(|| ZsError("İş bulunamadı.".to_string()))?;
        if !Self::visible_to_inner(&mut g, Some(&t), actor)? {
            return Err(ZsError("Bu işe erişiminiz yok.".to_string()));
        }
        let is_sub = !t["parent_id"].is_null();
        let cur = t["status"].as_str().unwrap_or("");
        let root = if is_sub {
            Self::root_of_inner(&mut g, task_id)?.unwrap_or_else(|| t.clone())
        } else {
            t.clone()
        };
        let is_priv = root["is_private"].as_i64().unwrap_or(0) != 0;
        let actor_id = actor["id"].as_i64().unwrap_or(0);
        let is_admin = actor["role"].as_str() == Some("admin");
        let root_id = root["id"].as_i64().unwrap_or(0);
        let (assignees, approvers) = Self::task_roles_inner(&mut g, root_id, Some(&root))?;
        let is_approver = !is_priv && (is_admin || approvers.contains(&actor_id));
        let is_assignee = assignees.contains(&actor_id) || t["assignee_id"].as_i64() == Some(actor_id);

        if is_sub {
            if !SUB_STATUSES.contains(&new_status) {
                return Err(ZsError("Geçersiz alt iş durumu.".to_string()));
            }
            if is_priv {
                if root["created_by"].as_i64() != Some(actor_id) {
                    return Err(ZsError("Özel alt işi yalnız sahibi işaretler.".to_string()));
                }
            } else if !(is_admin || is_assignee) {
                return Err(ZsError("Bu alt işi değiştirme yetkiniz yok.".to_string()));
            }
        } else if is_priv {
            if root["created_by"].as_i64() != Some(actor_id) {
                return Err(ZsError("Özel işin durumunu yalnız sahibi değiştirir.".to_string()));
            }
            if new_status != "acik" && new_status != "devam" && new_status != "tamamlandi" {
                return Err(ZsError("Özel iş için geçersiz durum.".to_string()));
            }
        } else {
            if !STATUSES.contains(&new_status) || new_status == "tamamlandi" {
                return Err(ZsError("Geçersiz durum.".to_string()));
            }
            if new_status == "onaylandi" || (cur == "onayda" && new_status == "devam") {
                if !is_approver {
                    return Err(ZsError("Onay/ret yetkisi yalnız yönetici veya işin onay yetkilisinde.".to_string()));
                }
            } else if new_status == "devam" || new_status == "onayda" {
                if cur == "onaylandi" && !is_admin {
                    return Err(ZsError("Onaylanmış işi yalnız yönetici yeniden açabilir.".to_string()));
                }
                if !(is_admin || is_assignee) {
                    return Err(ZsError("Bu işi değiştirme yetkiniz yok.".to_string()));
                }
            } else if new_status == "acik" && !is_admin {
                return Err(ZsError("Yalnız yönetici 'açık'a alır.".to_string()));
            }
        }

        let mut sonuc_val = sonuc.map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
        if let Some(ref sv) = sonuc_val {
            if is_sub || !["onayda", "onaylandi", "tamamlandi"].contains(&new_status) {
                sonuc_val = None;
            } else if !TEMPLATE_SONUCLAR.contains(&sv.as_str()) {
                return Err(ZsError("Geçersiz kontrol sonucu.".to_string()));
            }
        }
        let kesin_ts = if let Some(kt) = kesinlesme_tarihi {
            if !kt.trim().is_empty() && !is_sub && new_status == "onaylandi" {
                Some(tarih_17(kt, 17)?)
            } else {
                None
            }
        } else {
            None
        };

        let now = now_ts();
        let sub_sql = if !is_sub && new_status == "onayda" {
            ", submitted_at = ?"
        } else {
            ""
        };
        let sonuc_sql = if sonuc_val.is_some() { ", sonuc = ?" } else { "" };
        let mut p: Vec<SqlParam> = vec![new_status.into(), now.into()];
        if !sub_sql.is_empty() {
            p.push(now.into());
        }
        if let Some(ref sv) = sonuc_val {
            p.push(sv.as_str().into());
        }
        p.push(task_id.into());
        g.exec(
            &format!("UPDATE zs_tasks SET status=?, updated_at=?{}{} WHERE id=?", sub_sql, sonuc_sql),
            &p,
        )?;

        let target = t["parent_id"].as_i64().unwrap_or(task_id);
        let note_t = note.trim();
        let mut detail = json!({"id": task_id, "from": cur, "to": new_status});
        if !note_t.is_empty() {
            detail.as_object_mut().unwrap().insert("note".to_string(), json!(note_t.chars().take(400).collect::<String>()));
        }
        if let Some(ref sv) = sonuc_val {
            detail.as_object_mut().unwrap().insert("sonuc".to_string(), json!(sv));
        }
        Self::ev(&mut g, Some(target), Some(actor_id), "status", Some(detail))?;
        if !note_t.is_empty() {
            g.exec(
                "INSERT INTO zs_notes(task_id, author_id, body, note_at, created_at) VALUES (?,?,?,?,?)",
                &[target.into(), actor_id.into(), note_t.chars().take(10000).collect::<String>().into(), now.into(), now.into()],
            )?;
        }

        let mut updated = Self::get_task_inner(&mut g, task_id)?.unwrap_or(Value::Null);
        let next_sonuc = sonuc_val.as_deref().or_else(|| updated["sonuc"].as_str());
        if !is_sub && new_status == "onaylandi" {
            if let Some(ns) = next_sonuc {
                if let Some(next_t) = Self::spawn_next_task(&mut g, &updated, ns, actor, kesin_ts)? {
                    updated.as_object_mut().unwrap().insert("sonraki_is".to_string(), next_t);
                }
            }
        }
        Ok(updated)
    }
}

impl ZsStore {
    fn all_templates_for_office(inner: &mut StoreInner, office: &str) -> ZsResult<Vec<Value>> {
        let rows = if office.is_empty() {
            inner.query_all(
                "SELECT * FROM zs_templates WHERE office_id IS NULL OR office_id = '' ORDER BY id",
                &[],
            )?
        } else {
            inner.query_all(
                "SELECT * FROM zs_templates WHERE office_id = ? ORDER BY id",
                &[office.into()],
            )?
        };
        let mut custom = Vec::with_capacity(rows.len());
        for mut r in rows {
            let son_raw = r["sonuclar"].as_str().unwrap_or("");
            let son = serde_json::from_str::<Value>(son_raw)
                .ok()
                .and_then(|v| clean_sonuclar(Some(&v)).ok())
                .unwrap_or_else(|| json!({"olumlu": null, "olumsuz": null, "bekliyor": null}));
            let et_raw = r["etiketler"].as_str().unwrap_or("");
            let et = serde_json::from_str::<Vec<String>>(et_raw).unwrap_or_default();
            let id_num = r["id"].as_i64().unwrap_or(0);
            let obj = r.as_object_mut().unwrap();
            obj.insert("id".to_string(), json!(format!("b-{}", id_num)));
            obj.insert("db_id".to_string(), json!(id_num));
            obj.insert("builtin".to_string(), json!(false));
            obj.insert("sonuclar".to_string(), son);
            obj.insert("etiketler".to_string(), json!(et));
            custom.push(r);
        }
        let mut all: Vec<Value> = hazir_sablonlar_exact()
            .into_iter()
            .map(|mut h| {
                h.as_object_mut().unwrap().insert("builtin".to_string(), json!(true));
                h
            })
            .collect();
        all.extend(custom);
        Ok(all)
    }

    fn resolve_sablon_inner(inner: &mut StoreInner, key: &str, office: &str) -> ZsResult<Option<Value>> {
        for s in Self::all_templates_for_office(inner, office)? {
            if s["id"].as_str() == Some(key) {
                return Ok(Some(s));
            }
        }
        Ok(None)
    }

    fn match_sablon_inner(inner: &mut StoreInner, title: &str, office: &str) -> ZsResult<Option<String>> {
        let key = ad_key(&is_adi(title));
        if key.is_empty() {
            return Ok(None);
        }
        for s in Self::all_templates_for_office(inner, office)? {
            if ad_key(s["title"].as_str().unwrap_or("")) == key {
                return Ok(s["id"].as_str().map(|x| x.to_string()));
            }
            if let Some(eski) = s.get("eski").and_then(|v| v.as_array()) {
                if eski.iter().any(|e| ad_key(e.as_str().unwrap_or("")) == key) {
                    return Ok(s["id"].as_str().map(|x| x.to_string()));
                }
            }
        }
        Ok(None)
    }

    fn sablon_by_ad_inner(inner: &mut StoreInner, baslik: &str, office: &str) -> ZsResult<Option<Value>> {
        let key = ad_key(baslik);
        if key.is_empty() {
            return Ok(None);
        }
        for s in Self::all_templates_for_office(inner, office)? {
            if ad_key(s["title"].as_str().unwrap_or("")) == key {
                return Ok(Some(s));
            }
            if let Some(eski) = s.get("eski").and_then(|v| v.as_array()) {
                if eski.iter().any(|e| ad_key(e.as_str().unwrap_or("")) == key) {
                    return Ok(Some(s));
                }
            }
        }
        Ok(None)
    }

    fn copy_dosya_map(t: &Value) -> Option<Value> {
        let did = t["dosya_id"].as_str().filter(|s| !s.is_empty())?;
        let mut m = Map::new();
        for &k in DOSYA_FIELDS {
            if k == "dosya_uyap" {
                continue;
            }
            if let Some(v) = t.get(k) {
                m.insert(k.to_string(), v.clone());
            }
        }
        m.insert("dosya_id".to_string(), json!(did));
        if let Some(u_str) = t["dosya_uyap"].as_str() {
            if let Ok(u_val) = serde_json::from_str::<Value>(u_str) {
                m.insert("uyap".to_string(), u_val);
            }
        }
        Some(Value::Object(m))
    }

    fn spawn_next_task(
        inner: &mut StoreInner,
        t: &Value,
        sonuc: &str,
        actor: &Value,
        kesin_ts: Option<i64>,
    ) -> ZsResult<Option<Value>> {
        let office = office_of(Some(t));
        let mut sab: Option<Value> = None;
        if let Some(sk) = t["sablon"].as_str().filter(|s| !s.is_empty()) {
            sab = Self::resolve_sablon_inner(inner, sk, &office)?;
        }
        if sab.is_none() {
            sab = Self::sablon_by_ad_inner(inner, &is_adi(t["title"].as_str().unwrap_or("")), &office)?;
        }
        let Some(sab) = sab else { return Ok(None); };
        let dal = sab.get("sonuclar").and_then(|s| s.get(sonuc)).filter(|v| !v.is_null());
        if dal.is_none() && kesin_ts.is_none() {
            return Ok(None);
        }
        let d_val = Self::copy_dosya_map(t);
        let prefix = t["taraf_adi"]
            .as_str()
            .filter(|s| !s.is_empty())
            .map(|s| format!("{} – ", s))
            .unwrap_or_default();

        let create_from_spec = |inner: &mut StoreInner,
                                hedef: Option<&Value>,
                                baslik: &str,
                                due_ts: Option<i64>,
                                prio: i64|
         -> ZsResult<Value> {
            let full_title: String = format!("{}{}", prefix, baslik).chars().take(200).collect();
            let desc = hedef
                .and_then(|h| h["description"].as_str())
                .unwrap_or(t["description"].as_str().unwrap_or(""));
            let sab_id = hedef.and_then(|h| h["id"].as_str()).map(|s| s.to_string());
            let asama_s = hedef.and_then(|h| h["asama"].as_str()).map(|s| s.to_string());
            let mut sab_tags = Vec::new();
            if let Some(arr) = hedef.and_then(|h| h["etiketler"].as_array()) {
                for e in arr {
                    if let Some(es) = e.as_str() {
                        sab_tags.push(es.to_string());
                    }
                }
            }
            let (surec, asama_k) = if let Some(ref a) = asama_s {
                asama_coz(Some(a))?
            } else {
                (None, None)
            };
            let clean_d = clean_dosya(d_val.as_ref());
            let d_get = |k: &str| -> Option<String> {
                clean_d
                    .as_ref()
                    .and_then(|m| m.get(k))
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string())
            };
            let now = now_ts();
            let actor_id = actor["id"].as_i64().unwrap_or(0);
            let assignee_id = t["assignee_id"].as_i64();
            let oid_p: Option<String> = if office.is_empty() { None } else { Some(office.clone()) };
            let new_id = inner.insert_returning_id(
                "INSERT INTO zs_tasks(parent_id, title, description, assignee_id, created_by, due_at, status, sort_order, is_private, priority, office_id, dosya_id, birim_id, birim_adi, dosya_no, dosya_tur, dosya_uyap, evrak_id, evrak_adi, taraf_adi, barkod, sablon, surec, asama, created_at, updated_at) VALUES (NULL,?,?,?,?,?,'acik',0,0,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                &[
                    full_title.as_str().into(),
                    desc.into(),
                    assignee_id.into(),
                    actor_id.into(),
                    due_ts.into(),
                    prio.into(),
                    oid_p.into(),
                    d_get("dosya_id").into(),
                    d_get("birim_id").into(),
                    d_get("birim_adi").into(),
                    d_get("dosya_no").into(),
                    d_get("dosya_tur").into(),
                    d_get("dosya_uyap").into(),
                    d_get("evrak_id").into(),
                    d_get("evrak_adi").into(),
                    d_get("taraf_adi").into(),
                    d_get("barkod").into(),
                    sab_id.into(),
                    surec.clone().into(),
                    asama_k.clone().into(),
                    now.into(),
                    now.into(),
                ],
            )?;
            if let Some(ref dm) = clean_d {
                if let Some(tur) = dosya_turu(
                    dm.get("dosya_tur").and_then(|v| v.as_str()),
                    dm.get("birim_adi").and_then(|v| v.as_str()),
                ) {
                    let _ = inner.exec(
                        "INSERT INTO zs_task_tags(task_id, tag, added_by, created_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                        &[new_id.into(), format!("e-{}", tur).into(), actor_id.into(), now.into()],
                    );
                }
            }
            if let Some(ref su) = surec {
                for hs in hazir_surecler() {
                    if hs["key"].as_str() == Some(su) {
                        if let Some(ek) = hs["etiket"].as_str() {
                            let _ = inner.exec(
                                "INSERT INTO zs_task_tags(task_id, tag, added_by, created_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                                &[new_id.into(), ek.into(), actor_id.into(), now.into()],
                            );
                        }
                    }
                }
            }
            if let Some(ae) = asama_etiketi(surec.as_deref(), asama_k.as_deref()) {
                let _ = inner.exec(
                    "INSERT INTO zs_task_tags(task_id, tag, added_by, created_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                    &[new_id.into(), ae.into(), actor_id.into(), now.into()],
                );
            }
            for tg in sab_tags {
                let _ = inner.exec(
                    "INSERT INTO zs_task_tags(task_id, tag, added_by, created_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                    &[new_id.into(), tg.into(), actor_id.into(), now.into()],
                );
            }
            Self::ev(
                inner,
                Some(new_id),
                Some(actor_id),
                "created",
                Some(json!({
                    "id": new_id,
                    "title": full_title,
                    "assignee_id": assignee_id,
                    "due_at": due_ts,
                    "private": false
                })),
            )?;
            Ok(Self::get_task_inner(inner, new_id)?.unwrap_or(Value::Null))
        };

        let mut next_task = None;
        if let Some(d) = dal {
            let baslik_opt = d["baslik"].as_str().filter(|s| !s.is_empty());
            let (hedef, baslik) = if let Some(b) = baslik_opt {
                (Self::sablon_by_ad_inner(inner, b, &office)?, b.to_string())
            } else {
                (Some(sab.clone()), sab["title"].as_str().unwrap_or("").to_string())
            };
            let gun = d["gun"]
                .as_i64()
                .or_else(|| hedef.as_ref().and_then(|h| h["due_days"].as_i64()));
            let due_ts = gun.map(|g| gun_sonra(g, 17));
            let prio = t["priority"].as_i64().unwrap_or(1);
            let nt = create_from_spec(inner, hedef.as_ref(), &baslik, due_ts, prio)?;
            Self::ev(
                inner,
                Some(t["id"].as_i64().unwrap_or(0)),
                Some(actor["id"].as_i64().unwrap_or(0)),
                "next_task",
                Some(json!({"sonuc": sonuc, "next_id": nt["id"], "title": nt["title"]})),
            )?;
            next_task = Some(nt);
        }

        if let Some(kts) = kesin_ts {
            if sab["kesinlesme"].as_str() == Some(sonuc) {
                let ks = Self::resolve_sablon_inner(inner, KESINLESME_SABLONU, &office)?;
                let kt_title = ks
                    .as_ref()
                    .and_then(|s| s["title"].as_str())
                    .unwrap_or("Kesinleşme tarihi kontrol");
                let kt = create_from_spec(inner, ks.as_ref(), kt_title, Some(kts), 1)?;
                Self::ev(
                    inner,
                    Some(t["id"].as_i64().unwrap_or(0)),
                    Some(actor["id"].as_i64().unwrap_or(0)),
                    "next_task",
                    Some(json!({"sonuc": "kesinlesme", "next_id": kt["id"], "title": kt["title"]})),
                )?;
                if next_task.is_none() {
                    next_task = Some(kt);
                }
            }
        }
        Ok(next_task)
    }

    pub fn report_source(&self, actor: &Value) -> ZsResult<(Vec<Value>, Vec<Value>, Vec<Value>)> {
        if actor["role"].as_str() != Some("admin") {
            return Err(ZsError("Rapor yetkisi yalnız yöneticide.".to_string()));
        }
        let users = self.list_users(false, Some(&office_of(Some(actor))))?;
        let tasks = self.list_tree(actor, Some("team"))?;
        let mut g = self.inner.lock().unwrap();
        let exts = g.query_all("SELECT * FROM zs_extensions", &[])?;
        Ok((users, tasks, exts))
    }

    pub fn activity_source(
        &self,
        actor: &Value,
        since: i64,
        until: i64,
    ) -> ZsResult<(Vec<Value>, Vec<Value>, Vec<Value>)> {
        if actor["role"].as_str() != Some("admin") {
            return Err(ZsError("Rapor yetkisi yalnız yöneticide.".to_string()));
        }
        let oid = office_of(Some(actor));
        let users = self.list_users(false, Some(&oid))?;
        let mut g = self.inner.lock().unwrap();
        let tasks = if oid.is_empty() {
            g.query_all(
                "SELECT id, parent_id, title, assignee_id, created_by, status, due_at, is_private, priority, created_at, updated_at, submitted_at FROM zs_tasks WHERE (office_id IS NULL OR office_id = '')",
                &[],
            )?
        } else {
            g.query_all(
                "SELECT id, parent_id, title, assignee_id, created_by, status, due_at, is_private, priority, created_at, updated_at, submitted_at FROM zs_tasks WHERE office_id = ?",
                &[oid.clone().into()],
            )?
        };
        let events = if oid.is_empty() {
            g.query_all(
                "SELECT e.id, e.task_id, e.actor_id, e.kind, e.detail, e.created_at FROM zs_events e JOIN zs_users u ON u.id = e.actor_id WHERE (u.office_id IS NULL OR u.office_id = '') AND e.created_at >= ? AND e.created_at <= ? ORDER BY e.created_at DESC, e.id DESC",
                &[since.into(), until.into()],
            )?
        } else {
            g.query_all(
                "SELECT e.id, e.task_id, e.actor_id, e.kind, e.detail, e.created_at FROM zs_events e JOIN zs_users u ON u.id = e.actor_id WHERE u.office_id = ? AND e.created_at >= ? AND e.created_at <= ? ORDER BY e.created_at DESC, e.id DESC",
                &[oid.into(), since.into(), until.into()],
            )?
        };
        Ok((users, tasks, events))
    }

    pub fn audit(
        &self,
        user: Option<&Value>,
        ip: &str,
        user_agent: &str,
        method: &str,
        path: &str,
        action: &str,
        target_id: Option<i64>,
        ok: bool,
        status: i64,
        error: Option<&str>,
        data: Option<Value>,
        result: Option<Value>,
        before: Option<Value>,
        ms: Option<i64>,
    ) {
        let uid = user.and_then(|u| u["id"].as_i64());
        let uname = user.and_then(|u| u["username"].as_str()).map(|s| s.to_string());
        let role = user.and_then(|u| u["role"].as_str()).map(|s| s.to_string());
        let err_s = error.map(|s| s.chars().take(400).collect::<String>());
        let data_s = data.map(|v| serde_json::to_string(&v).unwrap_or_default());
        let res_s = result.map(|v| serde_json::to_string(&v).unwrap_or_default());
        let bef_s = before.map(|v| serde_json::to_string(&v).unwrap_or_default());
        if let Ok(mut g) = self.inner.lock() {
            let _ = g.exec(
                "INSERT INTO zs_audit(at, user_id, username, role, ip, user_agent, method, path, action, target_id, ok, status, error, data, result, before, ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                &[
                    now_ts().into(),
                    uid.into(),
                    uname.into(),
                    role.into(),
                    ip.chars().take(64).collect::<String>().into(),
                    user_agent.chars().take(240).collect::<String>().into(),
                    method.chars().take(12).collect::<String>().into(),
                    path.chars().take(200).collect::<String>().into(),
                    action.chars().take(80).collect::<String>().into(),
                    target_id.into(),
                    ok.into(),
                    status.into(),
                    err_s.into(),
                    data_s.into(),
                    res_s.into(),
                    bef_s.into(),
                    ms.into(),
                ],
            );
        }
    }

    pub fn audit_rows(
        &self,
        limit: i64,
        since: Option<i64>,
        username: Option<&str>,
        action: Option<&str>,
        only_errors: bool,
    ) -> ZsResult<Vec<Value>> {
        let mut conds = Vec::new();
        let mut params: Vec<SqlParam> = Vec::new();
        if let Some(s) = since {
            conds.push("at >= ?");
            params.push(s.into());
        }
        if let Some(u) = username {
            conds.push("username = ?");
            params.push(u.into());
        }
        if let Some(a) = action {
            conds.push("action = ?");
            params.push(a.into());
        }
        if only_errors {
            conds.push("ok = 0");
        }
        let where_sql = if conds.is_empty() {
            String::new()
        } else {
            format!(" WHERE {}", conds.join(" AND "))
        };
        params.push(limit.into());
        let mut g = self.inner.lock().unwrap();
        g.query_all(
            &format!("SELECT * FROM zs_audit{} ORDER BY id DESC LIMIT ?", where_sql),
            &params,
        )
    }

    pub fn snapshot_task(&self, task_id: i64) -> Option<Value> {
        let mut g = self.inner.lock().ok()?;
        let t = Self::get_task_inner(&mut g, task_id).ok().flatten()?;
        let subs = g.query_all("SELECT * FROM zs_tasks WHERE parent_id=?", &[task_id.into()]).ok()?;
        let notes = g.query_all("SELECT * FROM zs_notes WHERE task_id=?", &[task_id.into()]).ok()?;
        let files = g.query_all("SELECT id, task_id, uploader_id, filename, mime, size, sha256, created_at FROM zs_files WHERE task_id=?", &[task_id.into()]).ok()?;
        let members = g.query_all("SELECT * FROM zs_members WHERE task_id=?", &[task_id.into()]).ok()?;
        Some(json!({
            "task": t,
            "subtasks": subs,
            "notes": notes,
            "files": files,
            "members": members,
        }))
    }

    pub fn snapshot_file(&self, file_id: i64) -> Option<Value> {
        let mut g = self.inner.lock().ok()?;
        g.query_one(
            "SELECT id, task_id, uploader_id, filename, mime, size, sha256, created_at FROM zs_files WHERE id=?",
            &[file_id.into()],
        )
        .ok()
        .flatten()
    }

    pub fn snapshot_extension(&self, ext_id: i64) -> Option<Value> {
        self.get_extension(ext_id)
    }

    pub fn push_upsert(
        &self,
        user_id: i64,
        endpoint: &str,
        p256dh: &str,
        auth: &str,
        ua: &str,
    ) -> ZsResult<()> {
        let mut g = self.inner.lock().unwrap();
        g.exec(
            "INSERT INTO zs_push_subs(user_id, endpoint, p256dh, auth, user_agent, created_at, fails) VALUES (?,?,?,?,?,?,0) ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id, p256dh=excluded.p256dh, auth=excluded.auth, user_agent=excluded.user_agent, fails=0",
            &[
                user_id.into(),
                endpoint.chars().take(600).collect::<String>().into(),
                p256dh.chars().take(200).collect::<String>().into(),
                auth.chars().take(100).collect::<String>().into(),
                ua.chars().take(240).collect::<String>().into(),
                now_ts().into(),
            ],
        )?;
        Ok(())
    }

    pub fn push_remove(&self, endpoint: &str, user_id: Option<i64>) {
        if let Ok(mut g) = self.inner.lock() {
            if let Some(uid) = user_id {
                let _ = g.exec(
                    "DELETE FROM zs_push_subs WHERE endpoint=? AND user_id=?",
                    &[endpoint.into(), uid.into()],
                );
            } else {
                let _ = g.exec("DELETE FROM zs_push_subs WHERE endpoint=?", &[endpoint.into()]);
            }
        }
    }

    pub fn push_list(&self, user_ids: &[i64]) -> Vec<Value> {
        if user_ids.is_empty() {
            return Vec::new();
        }
        let Ok(mut g) = self.inner.lock() else { return Vec::new(); };
        let ph = vec!["?"; user_ids.len()].join(",");
        let params: Vec<SqlParam> = user_ids.iter().map(|&i| SqlParam::Int(i)).collect();
        g.query_all(
            &format!("SELECT id, user_id, endpoint, p256dh, auth, fails FROM zs_push_subs WHERE user_id IN ({})", ph),
            &params,
        )
        .unwrap_or_default()
    }

    pub fn push_result(&self, sub_id: i64, ok: bool) {
        if let Ok(mut g) = self.inner.lock() {
            if ok {
                let _ = g.exec(
                    "UPDATE zs_push_subs SET last_ok=?, fails=0 WHERE id=?",
                    &[now_ts().into(), sub_id.into()],
                );
            } else {
                let _ = g.exec(
                    "UPDATE zs_push_subs SET fails=fails+1 WHERE id=?",
                    &[sub_id.into()],
                );
                let _ = g.exec("DELETE FROM zs_push_subs WHERE id=? AND fails>=5", &[sub_id.into()]);
            }
        }
    }

    pub fn tg_code_new(&self, user_id: i64, ttl: i64) -> ZsResult<String> {
        let mut g = self.inner.lock().unwrap();
        let now = now_ts();
        g.exec("DELETE FROM zs_tg_codes WHERE expires_at < ?", &[now.into()])?;
        g.exec("DELETE FROM zs_tg_codes WHERE user_id = ?", &[user_id.into()])?;
        let mut buf = [0u8; 18];
        rand::thread_rng().fill_bytes(&mut buf);
        let code = URL_SAFE_NO_PAD.encode(buf);
        g.exec(
            "INSERT INTO zs_tg_codes(code, user_id, expires_at) VALUES (?,?,?)",
            &[code.as_str().into(), user_id.into(), (now + ttl).into()],
        )?;
        Ok(code)
    }

    pub fn tg_link(
        &self,
        code: &str,
        chat_id: i64,
        tg_name: &str,
        tg_username: Option<&str>,
    ) -> Option<i64> {
        let mut g = self.inner.lock().ok()?;
        let row = g
            .query_one("SELECT user_id, expires_at FROM zs_tg_codes WHERE code=?", &[code.into()])
            .ok()??;
        let _ = g.exec("DELETE FROM zs_tg_codes WHERE code=?", &[code.into()]);
        if row["expires_at"].as_i64().unwrap_or(0) < now_ts() {
            return None;
        }
        let uid = row["user_id"].as_i64()?;
        let uname = tg_uname(tg_username);
        let tname: String = tg_name.chars().take(120).collect();
        let _ = g.exec(
            "INSERT INTO zs_telegram(user_id, chat_id, tg_name, tg_username, linked_at) VALUES (?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id, tg_name=excluded.tg_name, tg_username=excluded.tg_username, linked_at=excluded.linked_at",
            &[uid.into(), chat_id.into(), tname.into(), uname.into(), now_ts().into()],
        );
        Some(uid)
    }

    pub fn tg_unlink(&self, user_id: Option<i64>, chat_id: Option<i64>) {
        if let Ok(mut g) = self.inner.lock() {
            if let Some(uid) = user_id {
                let _ = g.exec("DELETE FROM zs_telegram WHERE user_id=?", &[uid.into()]);
            } else if let Some(cid) = chat_id {
                let _ = g.exec("DELETE FROM zs_telegram WHERE chat_id=?", &[cid.into()]);
            }
        }
    }

    pub fn tg_get(&self, user_ids: &[i64]) -> Vec<Value> {
        if user_ids.is_empty() {
            return Vec::new();
        }
        let Ok(mut g) = self.inner.lock() else { return Vec::new(); };
        let ph = vec!["?"; user_ids.len()].join(",");
        let params: Vec<SqlParam> = user_ids.iter().map(|&i| SqlParam::Int(i)).collect();
        g.query_all(
            &format!("SELECT user_id, chat_id, tg_name, tg_username, linked_at FROM zs_telegram WHERE user_id IN ({})", ph),
            &params,
        )
        .unwrap_or_default()
    }

    pub fn tg_user_by_chat(&self, chat_id: i64) -> Option<Value> {
        let mut g = self.inner.lock().ok()?;
        let row = g
            .query_one(
                "SELECT u.id, u.username, u.display_name, u.role, u.active, u.office_id FROM zs_telegram t JOIN zs_users u ON u.id = t.user_id WHERE t.chat_id = ? AND u.active = 1 LIMIT 1",
                &[chat_id.into()],
            )
            .ok()??;
        Some(row)
    }

    pub fn tg_user_by_username(&self, tg_username: &str, office_id: Option<&str>) -> Option<Value> {
        let uname = tg_uname(Some(tg_username))?;
        let mut g = self.inner.lock().ok()?;
        let oid = office_id.unwrap_or("");
        if oid.is_empty() {
            g.query_one(
                "SELECT u.id, u.username, u.display_name, u.role, u.active, u.office_id FROM zs_telegram t JOIN zs_users u ON u.id = t.user_id WHERE t.tg_username = ? AND u.active = 1 AND (u.office_id IS NULL OR u.office_id = '') LIMIT 1",
                &[uname.into()],
            )
            .ok()
            .flatten()
        } else {
            g.query_one(
                "SELECT u.id, u.username, u.display_name, u.role, u.active, u.office_id FROM zs_telegram t JOIN zs_users u ON u.id = t.user_id WHERE t.tg_username = ? AND u.active = 1 AND u.office_id = ? LIMIT 1",
                &[uname.into(), oid.into()],
            )
            .ok()
            .flatten()
        }
    }

    pub fn tg_touch(&self, chat_id: i64, tg_username: Option<&str>) {
        if let Some(uname) = tg_uname(tg_username) {
            if let Ok(mut g) = self.inner.lock() {
                let _ = g.exec(
                    "UPDATE zs_telegram SET tg_username=? WHERE chat_id=? AND (tg_username IS NULL OR tg_username<>?)",
                    &[uname.as_str().into(), chat_id.into(), uname.as_str().into()],
                );
            }
        }
    }

    pub fn tg_msg_save(&self, chat_id: i64, message_id: i64, task_id: i64) {
        if let Ok(mut g) = self.inner.lock() {
            let _ = g.exec(
                "INSERT INTO zs_tg_msgs(chat_id, message_id, task_id, created_at) VALUES (?,?,?,?) ON CONFLICT(chat_id, message_id) DO UPDATE SET task_id=excluded.task_id",
                &[chat_id.into(), message_id.into(), task_id.into(), now_ts().into()],
            );
        }
    }

    pub fn tg_msg_task(&self, chat_id: i64, message_id: i64) -> Option<i64> {
        let mut g = self.inner.lock().ok()?;
        let r = g
            .query_one(
                "SELECT task_id FROM zs_tg_msgs WHERE chat_id=? AND message_id=?",
                &[chat_id.into(), message_id.into()],
            )
            .ok()??;
        r["task_id"].as_i64()
    }

    pub fn is_linked(&self, user_id: i64) -> bool {
        let Ok(mut g) = self.inner.lock() else { return false; };
        let r = g
            .query_one(
                "SELECT 1 AS x FROM zs_push_subs WHERE user_id=? UNION ALL SELECT 1 AS x FROM zs_telegram WHERE user_id=? LIMIT 1",
                &[user_id.into(), user_id.into()],
            )
            .ok()
            .flatten();
        r.is_some()
    }

    pub fn max_event_id(&self) -> i64 {
        let Ok(mut g) = self.inner.lock() else { return 0; };
        g.query_one("SELECT COALESCE(MAX(id),0) AS m FROM zs_events", &[])
            .ok()
            .flatten()
            .and_then(|r| r["m"].as_i64())
            .unwrap_or(0)
    }

    pub fn events_for_notify(&self, after_id: i64) -> ZsResult<(Vec<Value>, Vec<i64>, HashMap<i64, String>)> {
        let mut g = self.inner.lock().unwrap();
        let events = g.query_all(
            "SELECT * FROM zs_events WHERE id > ? ORDER BY id LIMIT 100",
            &[after_id.into()],
        )?;
        if events.is_empty() {
            return Ok((Vec::new(), Vec::new(), HashMap::new()));
        }
        let admins: Vec<i64> = g
            .query_all("SELECT id FROM zs_users WHERE role='admin' AND active=1", &[])?
            .into_iter()
            .filter_map(|r| r["id"].as_i64())
            .collect();
        let active_users: HashSet<i64> = g
            .query_all("SELECT id FROM zs_users WHERE active=1", &[])?
            .into_iter()
            .filter_map(|r| r["id"].as_i64())
            .collect();
        let names: HashMap<i64, String> = g
            .query_all("SELECT id, display_name FROM zs_users", &[])?
            .into_iter()
            .filter_map(|r| Some((r["id"].as_i64()?, r["display_name"].as_str()?.to_string())))
            .collect();

        let mut task_cache: HashMap<i64, Value> = HashMap::new();
        let mut out = Vec::new();
        for mut ev in events {
            let Some(tid) = ev["task_id"].as_i64() else { continue; };
            if !task_cache.contains_key(&tid) {
                if let Some(t) = Self::get_task_inner(&mut g, tid)? {
                    let is_priv = t["is_private"].as_i64().unwrap_or(0) != 0;
                    if is_priv {
                        continue;
                    }
                    let (assignees, approvers) = Self::task_roles_inner(&mut g, tid, Some(&t))?;
                    let mut rec: HashSet<i64> = assignees.into_iter().chain(approvers).collect();
                    if let Some(cb) = t["created_by"].as_i64() {
                        rec.insert(cb);
                    }
                    let t_office = office_of(Some(&t));
                    let office_admins: Vec<i64> = g
                        .query_all("SELECT id, office_id FROM zs_users WHERE role='admin' AND active=1", &[])?
                        .into_iter()
                        .filter(|r| office_of(Some(r)) == t_office)
                        .filter_map(|r| r["id"].as_i64())
                        .collect();
                    for a in office_admins {
                        rec.insert(a);
                    }
                    rec.retain(|uid| active_users.contains(uid));
                    let mut rec_vec: Vec<i64> = rec.into_iter().collect();
                    rec_vec.sort();
                    let mut t_obj = t.clone();
                    t_obj.as_object_mut().unwrap().insert("recipients".to_string(), json!(rec_vec));
                    task_cache.insert(tid, t_obj);
                }
            }
            let Some(t) = task_cache.get(&tid) else { continue; };
            let detail: Value = serde_json::from_str(ev["detail"].as_str().unwrap_or("{}")).unwrap_or_else(|_| json!({}));
            ev.as_object_mut().unwrap().insert("task".to_string(), t.clone());
            ev.as_object_mut().unwrap().insert("detail_obj".to_string(), detail);
            out.push(ev);
        }
        Ok((out, admins, names))
    }

    pub fn add_note(
        &self,
        task_id: i64,
        body: &str,
        actor: &Value,
        note_at: Option<i64>,
    ) -> ZsResult<Value> {
        let body_t = body.trim();
        if body_t.is_empty() {
            return Err(ZsError("Not boş olamaz.".to_string()));
        }
        let body_c: String = body_t.chars().take(10000).collect();
        let mut g = self.inner.lock().unwrap();
        let t = Self::get_task_inner(&mut g, task_id)?
            .ok_or_else(|| ZsError("İş bulunamadı.".to_string()))?;
        if !Self::visible_to_inner(&mut g, Some(&t), actor)? {
            return Err(ZsError("Bu işe erişiminiz yok.".to_string()));
        }
        let now = now_ts();
        let at = note_at.unwrap_or(now);
        let actor_id = actor["id"].as_i64().unwrap_or(0);
        let nid = g.insert_returning_id(
            "INSERT INTO zs_notes(task_id, author_id, body, note_at, created_at) VALUES (?,?,?,?,?)",
            &[task_id.into(), actor_id.into(), body_c.as_str().into(), at.into(), now.into()],
        )?;
        g.exec("UPDATE zs_tasks SET updated_at=? WHERE id=?", &[now.into(), task_id.into()])?;
        let root_id = t["parent_id"].as_i64().unwrap_or(task_id);
        Self::ev(
            &mut g,
            Some(root_id),
            Some(actor_id),
            "note",
            Some(json!({"id": nid, "preview": body_c.chars().take(120).collect::<String>()})),
        )?;
        Ok(g.query_one("SELECT * FROM zs_notes WHERE id=?", &[nid.into()])?.unwrap_or(Value::Null))
    }

    pub fn add_file(
        &self,
        task_id: i64,
        filename: &str,
        mime: &str,
        size: i64,
        storage_key: &str,
        sha256: &str,
        actor: &Value,
    ) -> ZsResult<Value> {
        let mut g = self.inner.lock().unwrap();
        let t = Self::get_task_inner(&mut g, task_id)?
            .ok_or_else(|| ZsError("İş bulunamadı.".to_string()))?;
        if !Self::visible_to_inner(&mut g, Some(&t), actor)? {
            return Err(ZsError("Bu işe erişiminiz yok.".to_string()));
        }
        let now = now_ts();
        let actor_id = actor["id"].as_i64().unwrap_or(0);
        let fid = g.insert_returning_id(
            "INSERT INTO zs_files(task_id, uploader_id, filename, mime, size, storage_key, sha256, created_at) VALUES (?,?,?,?,?,?,?,?)",
            &[
                task_id.into(),
                actor_id.into(),
                filename.chars().take(240).collect::<String>().into(),
                mime.chars().take(120).collect::<String>().into(),
                size.into(),
                storage_key.into(),
                sha256.into(),
                now.into(),
            ],
        )?;
        g.exec("UPDATE zs_tasks SET updated_at=? WHERE id=?", &[now.into(), task_id.into()])?;
        let root_id = t["parent_id"].as_i64().unwrap_or(task_id);
        Self::ev(
            &mut g,
            Some(root_id),
            Some(actor_id),
            "file",
            Some(json!({"id": fid, "filename": filename, "size": size})),
        )?;
        Ok(g.query_one(
            "SELECT id, task_id, uploader_id, filename, mime, size, sha256, created_at FROM zs_files WHERE id=?",
            &[fid.into()],
        )?
        .unwrap_or(Value::Null))
    }

    pub fn get_file(&self, file_id: i64, user: &Value) -> ZsResult<Option<Value>> {
        let mut g = self.inner.lock().unwrap();
        let f = g.query_one("SELECT * FROM zs_files WHERE id=?", &[file_id.into()])?;
        let Some(f) = f else { return Ok(None); };
        let tid = f["task_id"].as_i64().unwrap_or(0);
        let t = Self::get_task_inner(&mut g, tid)?;
        if !Self::visible_to_inner(&mut g, t.as_ref(), user)? {
            return Ok(None);
        }
        let root_id = t.as_ref().and_then(|x| x["parent_id"].as_i64()).unwrap_or(tid);
        let cuts = Self::hide_cuts_inner(&mut g, root_id, user)?;
        if file_id <= *cuts.get("file").unwrap_or(&0) {
            return Ok(None);
        }
        Ok(Some(f))
    }

    pub fn delete_file(&self, file_id: i64, actor: &Value) -> ZsResult<Option<String>> {
        let mut g = self.inner.lock().unwrap();
        let f = g
            .query_one("SELECT * FROM zs_files WHERE id=?", &[file_id.into()])?
            .ok_or_else(|| ZsError("Belge bulunamadı.".to_string()))?;
        let tid = f["task_id"].as_i64().unwrap_or(0);
        let t = Self::get_task_inner(&mut g, tid)?;
        let root = Self::root_of_inner(&mut g, tid)?;
        let is_priv = root.as_ref().map(|r| r["is_private"].as_i64().unwrap_or(0) != 0).unwrap_or(false);
        let actor_id = actor["id"].as_i64().unwrap_or(0);
        if is_priv {
            if root.as_ref().and_then(|r| r["created_by"].as_i64()) != Some(actor_id) {
                return Err(ZsError("Özel belgesini yalnız sahibi siler.".to_string()));
            }
        } else {
            if let Some(ref rt) = root {
                if office_of(Some(rt)) != office_of(Some(actor)) {
                    return Err(ZsError("Bu işe erişiminiz yok.".to_string()));
                }
            }
            if actor["role"].as_str() != Some("admin") && f["uploader_id"].as_i64() != Some(actor_id) {
                return Err(ZsError("Silme yetkisi yönetici veya yükleyende.".to_string()));
            }
        }
        g.exec("DELETE FROM zs_files WHERE id=?", &[file_id.into()])?;
        let target = t.as_ref().and_then(|x| x["parent_id"].as_i64()).unwrap_or(tid);
        Self::ev(
            &mut g,
            Some(target),
            Some(actor_id),
            "file_deleted",
            Some(json!({"id": file_id, "filename": f["filename"]})),
        )?;
        Ok(f["storage_key"].as_str().map(|s| s.to_string()))
    }

    pub fn add_reminder(
        &self,
        task_id: Option<i64>,
        remind_at: i64,
        note: &str,
        actor: &Value,
        user_id: Option<i64>,
        title: &str,
    ) -> ZsResult<Value> {
        let mut g = self.inner.lock().unwrap();
        let actor_id = actor["id"].as_i64().unwrap_or(0);
        let is_admin = actor["role"].as_str() == Some("admin");
        let target_uid = user_id.unwrap_or(actor_id);
        let now = now_ts();
        if remind_at <= now - 60 {
            return Err(ZsError("Hatırlatma zamanı geçmiş olamaz.".to_string()));
        }
        if remind_at > now + 5 * 365 * 86400 {
            return Err(ZsError("Hatırlatma zamanı en fazla 5 yıl sonrası olabilir.".to_string()));
        }

        let (root_id, sub_id, title_clean) = if let Some(tid) = task_id {
            let t = Self::get_task_inner(&mut g, tid)?
                .ok_or_else(|| ZsError("İş bulunamadı.".to_string()))?;
            if !Self::visible_to_inner(&mut g, Some(&t), actor)? {
                return Err(ZsError("Bu işe erişiminiz yok.".to_string()));
            }
            let pid = t["parent_id"].as_i64();
            let rid = pid.unwrap_or(tid);
            let sid = pid.map(|_| tid);
            let root = Self::get_task_inner(&mut g, rid)?;
            if root.as_ref().map(|r| r["is_private"].as_i64().unwrap_or(0) != 0).unwrap_or(false) && target_uid != actor_id {
                return Err(ZsError("Özel işte başkasına hatırlatıcı kurulamaz.".to_string()));
            }
            if target_uid != actor_id && !is_admin {
                return Err(ZsError("Başkasına hatırlatıcı yalnız yönetici kurabilir.".to_string()));
            }
            if let Some(ref rt) = root {
                if office_of(Some(rt)) != office_of(Some(actor)) {
                    return Err(ZsError("Farklı bürodan kişiye hatırlatıcı kurulamaz.".to_string()));
                }
            }
            (Some(rid), sid, String::new())
        } else {
            let tc: String = title.trim().chars().take(160).collect();
            if tc.is_empty() {
                return Err(ZsError("Hatırlatma başlığı boş olamaz.".to_string()));
            }
            if target_uid != actor_id && !is_admin {
                return Err(ZsError("Başkasına hatırlatıcı yalnız yönetici kurabilir.".to_string()));
            }
            (None, None, tc)
        };

        let u = g
            .query_one("SELECT id, office_id FROM zs_users WHERE id=? AND active=1", &[target_uid.into()])?
            .ok_or_else(|| ZsError("Kullanıcı bulunamadı veya pasif.".to_string()))?;
        if office_of(Some(&u)) != office_of(Some(actor)) {
            return Err(ZsError("Farklı bürodan kişiye hatırlatıcı kurulamaz.".to_string()));
        }
        let note_c: String = note.trim().chars().take(300).collect();
        let rid_new = g.insert_returning_id(
            "INSERT INTO zs_reminders(task_id, sub_id, user_id, created_by, remind_at, note, title, created_at) VALUES (?,?,?,?,?,?,?,?)",
            &[
                root_id.into(),
                sub_id.into(),
                target_uid.into(),
                actor_id.into(),
                remind_at.into(),
                note_c.as_str().into(),
                title_clean.as_str().into(),
                now.into(),
            ],
        )?;
        if let Some(r_id) = root_id {
            Self::ev(
                &mut g,
                Some(r_id),
                Some(actor_id),
                "reminder_added",
                Some(json!({"id": rid_new, "user_id": target_uid, "remind_at": remind_at, "note": note_c, "sub_id": sub_id})),
            )?;
        }
        Ok(g.query_one("SELECT * FROM zs_reminders WHERE id=?", &[rid_new.into()])?.unwrap_or(Value::Null))
    }

    pub fn list_reminders(&self, task_id: i64, user: &Value) -> ZsResult<Vec<Value>> {
        let mut g = self.inner.lock().unwrap();
        let t = Self::get_task_inner(&mut g, task_id)?;
        let Some(t) = t else { return Ok(Vec::new()); };
        if !Self::visible_to_inner(&mut g, Some(&t), user)? {
            return Ok(Vec::new());
        }
        let root_id = t["parent_id"].as_i64().unwrap_or(task_id);
        let uid = user["id"].as_i64().unwrap_or(0);
        if user["role"].as_str() == Some("admin") {
            g.query_all(
                "SELECT * FROM zs_reminders WHERE task_id=? ORDER BY sent_at IS NOT NULL, remind_at, id",
                &[root_id.into()],
            )
        } else {
            g.query_all(
                "SELECT * FROM zs_reminders WHERE task_id=? AND (user_id=? OR created_by=?) ORDER BY sent_at IS NOT NULL, remind_at, id",
                &[root_id.into(), uid.into(), uid.into()],
            )
        }
    }

    pub fn list_my_reminders(&self, user: &Value, include_sent: bool) -> ZsResult<Vec<Value>> {
        let mut g = self.inner.lock().unwrap();
        let uid = user["id"].as_i64().unwrap_or(0);
        let sent_sql = if include_sent { "" } else { " AND r.sent_at IS NULL" };
        g.query_all(
            &format!(
                "SELECT r.*, t.title AS task_title, t.status AS task_status, s.title AS sub_title FROM zs_reminders r LEFT JOIN zs_tasks t ON t.id = r.task_id LEFT JOIN zs_tasks s ON s.id = r.sub_id WHERE r.user_id=?{} ORDER BY r.sent_at IS NOT NULL, r.remind_at, r.id",
                sent_sql
            ),
            &[uid.into()],
        )
    }

    pub fn list_free_reminders(&self, user: &Value, include_sent: bool) -> ZsResult<Vec<Value>> {
        let mut g = self.inner.lock().unwrap();
        let uid = user["id"].as_i64().unwrap_or(0);
        let sent_sql = if include_sent { "" } else { " AND sent_at IS NULL" };
        g.query_all(
            &format!(
                "SELECT * FROM zs_reminders WHERE user_id=? AND task_id IS NULL{} ORDER BY sent_at IS NOT NULL, remind_at, id",
                sent_sql
            ),
            &[uid.into()],
        )
    }

    pub fn get_reminder(&self, rem_id: i64) -> Option<Value> {
        let mut g = self.inner.lock().ok()?;
        g.query_one("SELECT * FROM zs_reminders WHERE id=?", &[rem_id.into()]).ok().flatten()
    }

    pub fn delete_reminder(&self, rem_id: i64, actor: &Value) -> ZsResult<()> {
        let mut g = self.inner.lock().unwrap();
        let r = g
            .query_one("SELECT * FROM zs_reminders WHERE id=?", &[rem_id.into()])?
            .ok_or_else(|| ZsError("Hatırlatıcı bulunamadı.".to_string()))?;
        let actor_id = actor["id"].as_i64().unwrap_or(0);
        if actor["role"].as_str() != Some("admin")
            && r["user_id"].as_i64() != Some(actor_id)
            && r["created_by"].as_i64() != Some(actor_id)
        {
            return Err(ZsError("Bu hatırlatıcıyı silme yetkiniz yok.".to_string()));
        }
        g.exec("DELETE FROM zs_reminders WHERE id=?", &[rem_id.into()])?;
        Ok(())
    }

    pub fn claim_due_reminders(&self, now: Option<i64>, limit: i64) -> Vec<Value> {
        let ts = now.unwrap_or_else(now_ts);
        let Ok(mut g) = self.inner.lock() else { return Vec::new(); };
        let Ok(rows) = g.query_all(
            "SELECT r.*, t.title AS task_title, t.status AS task_status, s.title AS sub_title FROM zs_reminders r LEFT JOIN zs_tasks t ON t.id = r.task_id LEFT JOIN zs_tasks s ON s.id = r.sub_id WHERE r.sent_at IS NULL AND r.remind_at <= ? ORDER BY r.remind_at, r.id LIMIT ?",
            &[ts.into(), limit.into()],
        ) else {
            return Vec::new();
        };
        if rows.is_empty() {
            return Vec::new();
        }
        let ids: Vec<i64> = rows.iter().filter_map(|r| r["id"].as_i64()).collect();
        let ph = vec!["?"; ids.len()].join(",");
        let mut params = vec![SqlParam::Int(ts)];
        params.extend(ids.iter().map(|&i| SqlParam::Int(i)));
        let _ = g.exec(
            &format!("UPDATE zs_reminders SET sent_at=? WHERE id IN ({}) AND sent_at IS NULL", ph),
            &params,
        );
        rows
    }

    pub fn list_templates(&self, user: &Value) -> ZsResult<(Vec<Value>, Vec<Value>)> {
        let office = office_of(Some(user));
        let mut g = self.inner.lock().unwrap();
        let rows = if office.is_empty() {
            g.query_all(
                "SELECT * FROM zs_templates WHERE office_id IS NULL OR office_id = '' ORDER BY title, id",
                &[],
            )?
        } else {
            g.query_all(
                "SELECT * FROM zs_templates WHERE office_id = ? ORDER BY title, id",
                &[office.into()],
            )?
        };
        let mut custom = Vec::with_capacity(rows.len());
        for mut r in rows {
            let son = serde_json::from_str::<Value>(r["sonuclar"].as_str().unwrap_or(""))
                .ok()
                .and_then(|v| clean_sonuclar(Some(&v)).ok())
                .unwrap_or_else(|| json!({"olumlu": null, "olumsuz": null, "bekliyor": null}));
            let et = serde_json::from_str::<Vec<String>>(r["etiketler"].as_str().unwrap_or("")).unwrap_or_default();
            let obj = r.as_object_mut().unwrap();
            obj.insert("sonuclar".to_string(), son);
            obj.insert("etiketler".to_string(), json!(et));
            custom.push(r);
        }
        Ok((hazir_sablonlar_exact(), custom))
    }

    fn clean_etiketler_inner(
        inner: &mut StoreInner,
        vals: Option<&Value>,
        office: &str,
        actor_id: i64,
    ) -> ZsResult<Vec<String>> {
        let Some(arr) = vals.and_then(|v| v.as_array()) else {
            return Ok(Vec::new());
        };
        let asama_set = asama_etiketleri();
        let hazir_set: HashSet<String> = hazir_etiketler()
            .into_iter()
            .filter_map(|e| e["key"].as_str().map(|s| s.to_string()))
            .collect();
        let custom_rows = if office.is_empty() {
            inner.query_all(
                "SELECT id FROM zs_tags WHERE (office_id IS NULL OR office_id = '') AND (owner_id IS NULL OR owner_id = ?)",
                &[actor_id.into()],
            )?
        } else {
            inner.query_all(
                "SELECT id FROM zs_tags WHERE office_id = ? AND (owner_id IS NULL OR owner_id = ?)",
                &[office.into(), actor_id.into()],
            )?
        };
        let valid_custom: HashSet<String> = custom_rows
            .into_iter()
            .filter_map(|r| r["id"].as_i64().map(|id| id.to_string()))
            .collect();
        let mut out = Vec::new();
        for item in arr {
            if let Some(k) = item.as_str().map(|s| s.trim()) {
                if !k.is_empty() && !asama_set.contains(k) && (hazir_set.contains(k) || valid_custom.contains(k)) && !out.contains(&k.to_string()) {
                    out.push(k.to_string());
                    if out.len() >= 12 {
                        break;
                    }
                }
            }
        }
        Ok(out)
    }

    pub fn create_template(
        &self,
        title: &str,
        description: &str,
        due_days: Option<i64>,
        priority: i64,
        taraf: Option<&str>,
        action: Option<&str>,
        actor: &Value,
        kontrol: Option<&str>,
        sonuclar: Option<&Value>,
        asama: Option<&str>,
        etiketler: Option<&Value>,
    ) -> ZsResult<Value> {
        if actor["role"].as_str() != Some("admin") {
            return Err(ZsError("Büro şablonunu yalnız yönetici ekler.".to_string()));
        }
        let title_c: String = title
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
            .chars()
            .take(120)
            .collect();
        if title_c.is_empty() {
            return Err(ZsError("Şablon başlığı boş olamaz.".to_string()));
        }
        if let Some(dd) = due_days {
            if !(0..=365).contains(&dd) {
                return Err(ZsError("Süre gün sayısı 0–365 olmalı.".to_string()));
            }
        }
        let prio = if PRIORITIES.contains(&priority) { priority } else { 1 };
        let tf = taraf.map(|s| s.trim()).filter(|s| TEMPLATE_TARAF.contains(s));
        let act = action.map(|s| s.trim()).filter(|s| !s.is_empty()).map(|s| s.chars().take(60).collect::<String>());
        let kt = kontrol.map(|s| s.trim()).filter(|s| !s.is_empty()).map(|s| s.chars().take(200).collect::<String>());
        let son = clean_sonuclar(sonuclar)?;
        let asama_s = if let Some(a) = asama {
            if !a.trim().is_empty() {
                let (su, ak) = asama_coz(Some(a))?;
                match (su, ak) {
                    (Some(s), Some(k)) => Some(format!("{}/{}", s, k)),
                    _ => None,
                }
            } else {
                None
            }
        } else {
            None
        };
        let office = office_of(Some(actor));
        let actor_id = actor["id"].as_i64().unwrap_or(0);
        let mut g = self.inner.lock().unwrap();
        let et_clean = Self::clean_etiketler_inner(&mut g, etiketler, &office, actor_id)?;
        let oid_p: Option<String> = if office.is_empty() { None } else { Some(office) };
        let desc_c: String = description.trim().chars().take(2000).collect();
        let son_s = serde_json::to_string(&son).unwrap_or_default();
        let et_s = serde_json::to_string(&et_clean).unwrap_or_else(|_| "[]".to_string());
        let tid = g.insert_returning_id(
            "INSERT INTO zs_templates(office_id, title, description, due_days, priority, taraf, action, kontrol, sonuclar, asama, etiketler, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            &[
                oid_p.into(),
                title_c.as_str().into(),
                desc_c.as_str().into(),
                due_days.into(),
                prio.into(),
                tf.into(),
                act.into(),
                kt.into(),
                son_s.into(),
                asama_s.into(),
                et_s.into(),
                actor_id.into(),
                now_ts().into(),
            ],
        )?;
        let mut row = g.query_one("SELECT * FROM zs_templates WHERE id=?", &[tid.into()])?.unwrap_or(Value::Null);
        row.as_object_mut().unwrap().insert("sonuclar".to_string(), son);
        row.as_object_mut().unwrap().insert("etiketler".to_string(), json!(et_clean));
        Ok(row)
    }

    pub fn update_template(&self, tpl_id: i64, patch: &Value, actor: &Value) -> ZsResult<Value> {
        if actor["role"].as_str() != Some("admin") {
            return Err(ZsError("Büro şablonunu yalnız yönetici düzenler.".to_string()));
        }
        let office = office_of(Some(actor));
        let actor_id = actor["id"].as_i64().unwrap_or(0);
        let mut g = self.inner.lock().unwrap();
        let cur = g
            .query_one("SELECT * FROM zs_templates WHERE id=?", &[tpl_id.into()])?
            .ok_or_else(|| ZsError("Şablon bulunamadı.".to_string()))?;
        if cur["office_id"].as_str().unwrap_or("") != office {
            return Err(ZsError("Bu şablona erişiminiz yok.".to_string()));
        }
        let mut sets = Vec::new();
        let mut params: Vec<SqlParam> = Vec::new();
        if let Some(t) = patch.get("title").and_then(|v| v.as_str()) {
            let tc: String = t.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(120).collect();
            if tc.is_empty() {
                return Err(ZsError("Şablon başlığı boş olamaz.".to_string()));
            }
            sets.push("title=?");
            params.push(tc.into());
        }
        if let Some(d) = patch.get("description").and_then(|v| v.as_str()) {
            sets.push("description=?");
            params.push(d.trim().chars().take(2000).collect::<String>().into());
        }
        if let Some(ddv) = patch.get("due_days") {
            let dd = if ddv.is_null() || ddv.as_str().map(|s| s.trim().is_empty()).unwrap_or(false) {
                None
            } else if let Some(n) = ddv.as_i64() {
                if !(0..=365).contains(&n) {
                    return Err(ZsError("Süre gün sayısı 0–365 olmalı.".to_string()));
                }
                Some(n)
            } else {
                return Err(ZsError("Süre gün sayısı sayı olmalı.".to_string()));
            };
            sets.push("due_days=?");
            params.push(dd.into());
        }
        if let Some(pr) = patch.get("priority").and_then(|v| v.as_i64()) {
            sets.push("priority=?");
            params.push((if PRIORITIES.contains(&pr) { pr } else { 1 }).into());
        }
        if let Some(tfv) = patch.get("taraf") {
            let tf = tfv.as_str().map(|s| s.trim()).filter(|s| TEMPLATE_TARAF.contains(s));
            sets.push("taraf=?");
            params.push(tf.into());
        }
        if let Some(av) = patch.get("action") {
            let act = av.as_str().map(|s| s.trim()).filter(|s| !s.is_empty()).map(|s| s.chars().take(60).collect::<String>());
            sets.push("action=?");
            params.push(act.into());
        }
        if let Some(kv) = patch.get("kontrol") {
            let kt = kv.as_str().map(|s| s.trim()).filter(|s| !s.is_empty()).map(|s| s.chars().take(200).collect::<String>());
            sets.push("kontrol=?");
            params.push(kt.into());
        }
        if let Some(sv) = patch.get("sonuclar") {
            let son = clean_sonuclar(Some(sv))?;
            sets.push("sonuclar=?");
            params.push(serde_json::to_string(&son).unwrap_or_default().into());
        }
        if let Some(asv) = patch.get("asama") {
            let a_s = if let Some(s) = asv.as_str() {
                if !s.trim().is_empty() {
                    let (su, ak) = asama_coz(Some(s))?;
                    match (su, ak) {
                        (Some(su), Some(ak)) => Some(format!("{}/{}", su, ak)),
                        _ => None,
                    }
                } else {
                    None
                }
            } else {
                None
            };
            sets.push("asama=?");
            params.push(a_s.into());
        }
        if let Some(ev) = patch.get("etiketler") {
            let et = Self::clean_etiketler_inner(&mut g, Some(ev), &office, actor_id)?;
            sets.push("etiketler=?");
            params.push(serde_json::to_string(&et).unwrap_or_else(|_| "[]".to_string()).into());
        }
        if !sets.is_empty() {
            params.push(tpl_id.into());
            g.exec(&format!("UPDATE zs_templates SET {} WHERE id=?", sets.join(", ")), &params)?;
        }
        let mut row = g.query_one("SELECT * FROM zs_templates WHERE id=?", &[tpl_id.into()])?.unwrap_or(Value::Null);
        let son = serde_json::from_str::<Value>(row["sonuclar"].as_str().unwrap_or(""))
            .ok()
            .and_then(|v| clean_sonuclar(Some(&v)).ok())
            .unwrap_or_else(|| json!({"olumlu": null, "olumsuz": null, "bekliyor": null}));
        let et = serde_json::from_str::<Vec<String>>(row["etiketler"].as_str().unwrap_or("")).unwrap_or_default();
        row.as_object_mut().unwrap().insert("sonuclar".to_string(), son);
        row.as_object_mut().unwrap().insert("etiketler".to_string(), json!(et));
        Ok(row)
    }

    pub fn delete_template(&self, tpl_id: i64, actor: &Value) -> ZsResult<()> {
        if actor["role"].as_str() != Some("admin") {
            return Err(ZsError("Büro şablonunu yalnız yönetici siler.".to_string()));
        }
        let mut g = self.inner.lock().unwrap();
        let cur = g
            .query_one("SELECT id, office_id FROM zs_templates WHERE id=?", &[tpl_id.into()])?
            .ok_or_else(|| ZsError("Şablon bulunamadı.".to_string()))?;
        if cur["office_id"].as_str().unwrap_or("") != office_of(Some(actor)) {
            return Err(ZsError("Bu şablona erişiminiz yok.".to_string()));
        }
        g.exec("DELETE FROM zs_templates WHERE id=?", &[tpl_id.into()])?;
        Ok(())
    }

    pub fn backfill_sablon(&self) -> ZsResult<i64> {
        let mut g = self.inner.lock().unwrap();
        let rows = g.query_all(
            "SELECT id, title, office_id FROM zs_tasks WHERE parent_id IS NULL AND (sablon IS NULL OR sablon = '')",
            &[],
        )?;
        let mut n = 0;
        for r in rows {
            let tid = r["id"].as_i64().unwrap_or(0);
            let title = r["title"].as_str().unwrap_or("");
            let off = r["office_id"].as_str().unwrap_or("");
            if let Some(m) = Self::match_sablon_inner(&mut g, title, off)? {
                g.exec("UPDATE zs_tasks SET sablon=? WHERE id=?", &[m.into(), tid.into()])?;
                n += 1;
            }
        }
        Ok(n)
    }

    pub fn list_tags(&self, user: &Value) -> ZsResult<(Vec<Value>, Vec<Value>)> {
        let office = office_of(Some(user));
        let uid = user["id"].as_i64().unwrap_or(0);
        let mut g = self.inner.lock().unwrap();
        let rows = if office.is_empty() {
            g.query_all(
                "SELECT * FROM zs_tags WHERE (office_id IS NULL OR office_id = '') AND (owner_id IS NULL OR owner_id = ?) ORDER BY name, id",
                &[uid.into()],
            )?
        } else {
            g.query_all(
                "SELECT * FROM zs_tags WHERE office_id = ? AND (owner_id IS NULL OR owner_id = ?) ORDER BY name, id",
                &[office.into(), uid.into()],
            )?
        };
        let hazir = hazir_etiketler();
        let mut agac: HashMap<String, Value> = HashMap::new();
        for e in &hazir {
            if let Some(k) = e["key"].as_str() {
                agac.insert(k.to_string(), e.clone());
            }
        }
        for r in &rows {
            if let Some(id) = r["id"].as_i64() {
                agac.insert(id.to_string(), r.clone());
            }
        }
        let custom: Vec<Value> = rows
            .into_iter()
            .map(|mut r| {
                let key = r["id"].as_i64().map(|i| i.to_string()).unwrap_or_default();
                let tur = tag_turu(&key, &agac);
                r.as_object_mut().unwrap().insert("tur".to_string(), tur.map(Value::String).unwrap_or(Value::Null));
                r
            })
            .collect();
        Ok((hazir, custom))
    }

    fn clean_parent_tag_inner(
        inner: &mut StoreInner,
        parent: Option<&str>,
        office: &str,
        uid: i64,
        self_id: Option<i64>,
    ) -> ZsResult<Option<String>> {
        let p = parent.unwrap_or("").trim();
        if p.is_empty() {
            return Ok(None);
        }
        let hazir = hazir_etiketler();
        let mut agac: HashMap<String, Value> = HashMap::new();
        for e in &hazir {
            if let Some(k) = e["key"].as_str() {
                agac.insert(k.to_string(), e.clone());
            }
        }
        let rows = if office.is_empty() {
            inner.query_all(
                "SELECT id, parent FROM zs_tags WHERE (office_id IS NULL OR office_id = '') AND (owner_id IS NULL OR owner_id = ?)",
                &[uid.into()],
            )?
        } else {
            inner.query_all(
                "SELECT id, parent FROM zs_tags WHERE office_id = ? AND (owner_id IS NULL OR owner_id = ?)",
                &[office.into(), uid.into()],
            )?
        };
        for r in rows {
            if let Some(id) = r["id"].as_i64() {
                agac.insert(id.to_string(), r);
            }
        }
        if !agac.contains_key(p) || asama_etiketleri().contains(p) {
            return Err(ZsError("Geçersiz üst etiket.".to_string()));
        }
        let chain = tag_zinciri(p, &agac);
        if let Some(sid) = self_id {
            if chain.contains(&sid.to_string()) {
                return Err(ZsError("Etiket kendi altına bağlanamaz.".to_string()));
            }
        }
        if chain.len() >= ETIKET_DERINLIK {
            return Err(ZsError(format!("Etiket ağacı en çok {} katman olabilir.", ETIKET_DERINLIK)));
        }
        Ok(Some(p.to_string()))
    }

    pub fn create_tag(
        &self,
        name: &str,
        color: &str,
        scope: &str,
        actor: &Value,
        parent: Option<&str>,
    ) -> ZsResult<Value> {
        let nm: String = name.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(40).collect();
        if nm.is_empty() {
            return Err(ZsError("Etiket adı boş olamaz.".to_string()));
        }
        let col = if ETIKET_RENKLERI.contains(&color.trim()) {
            color.trim()
        } else {
            ETIKET_RENKLERI[0]
        };
        let is_office = scope == "office";
        if is_office && actor["role"].as_str() != Some("admin") {
            return Err(ZsError("Büro etiketini yalnız yönetici ekler.".to_string()));
        }
        let office = office_of(Some(actor));
        let actor_id = actor["id"].as_i64().unwrap_or(0);
        let owner_id: Option<i64> = if is_office { None } else { Some(actor_id) };
        let mut g = self.inner.lock().unwrap();
        let parent_clean = Self::clean_parent_tag_inner(&mut g, parent, &office, actor_id, None)?;
        let oid_p: Option<String> = if office.is_empty() { None } else { Some(office) };
        let tid = g.insert_returning_id(
            "INSERT INTO zs_tags(office_id, owner_id, name, color, parent, created_by, created_at) VALUES (?,?,?,?,?,?,?)",
            &[
                oid_p.into(),
                owner_id.into(),
                nm.as_str().into(),
                col.into(),
                parent_clean.into(),
                actor_id.into(),
                now_ts().into(),
            ],
        )?;
        Ok(g.query_one("SELECT * FROM zs_tags WHERE id=?", &[tid.into()])?.unwrap_or(Value::Null))
    }

    pub fn update_tag(&self, tag_id: i64, patch: &Value, actor: &Value) -> ZsResult<Value> {
        let office = office_of(Some(actor));
        let actor_id = actor["id"].as_i64().unwrap_or(0);
        let mut g = self.inner.lock().unwrap();
        let cur = g
            .query_one("SELECT * FROM zs_tags WHERE id=?", &[tag_id.into()])?
            .ok_or_else(|| ZsError("Etiket bulunamadı.".to_string()))?;
        if cur["office_id"].as_str().unwrap_or("") != office {
            return Err(ZsError("Bu etikete erişiminiz yok.".to_string()));
        }
        let owner = cur["owner_id"].as_i64();
        if owner.is_none() && actor["role"].as_str() != Some("admin") {
            return Err(ZsError("Büro etiketini yalnız yönetici düzenler.".to_string()));
        }
        if owner.is_some() && owner != Some(actor_id) {
            return Err(ZsError("Bu etiketi düzenleyemezsiniz.".to_string()));
        }
        let mut sets = Vec::new();
        let mut params: Vec<SqlParam> = Vec::new();
        if let Some(n) = patch.get("name").and_then(|v| v.as_str()) {
            let nm: String = n.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(40).collect();
            if nm.is_empty() {
                return Err(ZsError("Etiket adı boş olamaz.".to_string()));
            }
            sets.push("name=?");
            params.push(nm.into());
        }
        if let Some(c) = patch.get("color").and_then(|v| v.as_str()) {
            if ETIKET_RENKLERI.contains(&c.trim()) {
                sets.push("color=?");
                params.push(c.trim().into());
            }
        }
        if let Some(pv) = patch.get("parent") {
            let p_str = pv.as_str();
            let pc = Self::clean_parent_tag_inner(&mut g, p_str, &office, actor_id, Some(tag_id))?;
            sets.push("parent=?");
            params.push(pc.into());
        }
        if !sets.is_empty() {
            params.push(tag_id.into());
            g.exec(&format!("UPDATE zs_tags SET {} WHERE id=?", sets.join(", ")), &params)?;
        }
        Ok(g.query_one("SELECT * FROM zs_tags WHERE id=?", &[tag_id.into()])?.unwrap_or(Value::Null))
    }

    pub fn delete_tag(&self, tag_id: i64, actor: &Value) -> ZsResult<()> {
        let office = office_of(Some(actor));
        let actor_id = actor["id"].as_i64().unwrap_or(0);
        let mut g = self.inner.lock().unwrap();
        let cur = g
            .query_one("SELECT * FROM zs_tags WHERE id=?", &[tag_id.into()])?
            .ok_or_else(|| ZsError("Etiket bulunamadı.".to_string()))?;
        if cur["office_id"].as_str().unwrap_or("") != office {
            return Err(ZsError("Bu etikete erişiminiz yok.".to_string()));
        }
        let owner = cur["owner_id"].as_i64();
        if owner.is_none() && actor["role"].as_str() != Some("admin") {
            return Err(ZsError("Büro etiketini yalnız yönetici siler.".to_string()));
        }
        if owner.is_some() && owner != Some(actor_id) {
            return Err(ZsError("Bu etiketi silemezsiniz.".to_string()));
        }
        g.exec("DELETE FROM zs_task_tags WHERE tag=?", &[tag_id.to_string().into()])?;
        g.exec("UPDATE zs_tags SET parent=NULL WHERE parent=?", &[tag_id.to_string().into()])?;
        g.exec("DELETE FROM zs_tags WHERE id=?", &[tag_id.into()])?;
        Ok(())
    }

    pub fn set_task_tags(&self, task_id: i64, tags: &[String], actor: &Value) -> ZsResult<Vec<String>> {
        let mut g = self.inner.lock().unwrap();
        let t = Self::get_task_inner(&mut g, task_id)?
            .ok_or_else(|| ZsError("İş bulunamadı.".to_string()))?;
        if !t["parent_id"].is_null() {
            return Err(ZsError("Etiket yalnız ana işe takılır.".to_string()));
        }
        if !Self::visible_to_inner(&mut g, Some(&t), actor)? {
            return Err(ZsError("Bu işe erişiminiz yok.".to_string()));
        }
        let office = office_of(Some(actor));
        let actor_id = actor["id"].as_i64().unwrap_or(0);
        let tur = task_turu(Some(&t));
        let asama_set = asama_etiketleri();
        let auto_asama = asama_etiketi(t["surec"].as_str(), t["asama"].as_str());
        let hazir = hazir_etiketler();
        let mut agac: HashMap<String, Value> = HashMap::new();
        for e in &hazir {
            if let Some(k) = e["key"].as_str() {
                agac.insert(k.to_string(), e.clone());
            }
        }
        let custom_rows = if office.is_empty() {
            g.query_all(
                "SELECT id, parent FROM zs_tags WHERE (office_id IS NULL OR office_id = '') AND (owner_id IS NULL OR owner_id = ?)",
                &[actor_id.into()],
            )?
        } else {
            g.query_all(
                "SELECT id, parent FROM zs_tags WHERE office_id = ? AND (owner_id IS NULL OR owner_id = ?)",
                &[office.into(), actor_id.into()],
            )?
        };
        for r in &custom_rows {
            if let Some(id) = r["id"].as_i64() {
                agac.insert(id.to_string(), r.clone());
            }
        }
        let valid_custom: HashSet<String> = custom_rows
            .into_iter()
            .filter_map(|r| r["id"].as_i64().map(|i| i.to_string()))
            .collect();

        let mut clean = Vec::new();
        for tg in tags {
            let k = tg.trim();
            if k.is_empty() || asama_set.contains(k) {
                continue;
            }
            if let Some(he) = hazir.iter().find(|e| e["key"].as_str() == Some(k)) {
                if !tag_uyar(he, tur.as_deref()) {
                    continue;
                }
                if !clean.contains(&k.to_string()) {
                    clean.push(k.to_string());
                }
            } else if valid_custom.contains(k) {
                let c_tur = tag_turu(k, &agac);
                let dummy = json!({"tur": c_tur});
                if !tag_uyar(&dummy, tur.as_deref()) {
                    continue;
                }
                if !clean.contains(&k.to_string()) {
                    clean.push(k.to_string());
                }
            }
            if clean.len() >= 12 {
                break;
            }
        }
        if let Some(aa) = auto_asama {
            if !clean.contains(&aa) {
                clean.push(aa);
            }
        }
        let now = now_ts();
        g.exec("DELETE FROM zs_task_tags WHERE task_id=?", &[task_id.into()])?;
        for k in &clean {
            g.exec(
                "INSERT INTO zs_task_tags(task_id, tag, added_by, created_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                &[task_id.into(), k.as_str().into(), actor_id.into(), now.into()],
            )?;
        }
        Ok(clean)
    }
}
