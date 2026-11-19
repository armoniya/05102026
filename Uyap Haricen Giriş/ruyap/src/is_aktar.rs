//! Buluttaki (Render) İş Listesi'ni büro veritabanına taşıma (plan L5, 16.11.2026).
//!
//! `uyap_app --is-listesi-aktar [--deneme] [--dosya <arsiv.json>]`
//!
//! 1. Kayıtlı girişle (beni hatırla; yoksa `COKLUUYAP_IS_PAROLA`) buluttan jeton alınır ve yönetici
//!    dışa aktarma ucu (`/is-listesi/api/disa-aktar`, vendor_deploy/zs.py) bir kez çağrılır. `--dosya`
//!    verilirse buluta gidilmez, daha önce alınmış arşiv okunur.
//! 2. Gelen verinin TAMAMI (bitmiş işler dahil) ofis diskine arşivlenir:
//!    `%LOCALAPPDATA%\CokluUyap\arsiv\is_listesi_bulut_<zaman>.json`.
//! 3. Kullanıcı kararı (15.11): AÇIK işler (acik / devam / onayda) alt işleri, notları, kişileri, ek
//!    süre talepleri, etiketleri, geçmişi, bekleyen hatırlatıcıları ve belgeleriyle büro veritabanına
//!    yazılır; bitmiş işler yalnız arşivde kalır. Büro şablonları ve etiketleri de taşınır.
//!    Belgeler `%LOCALAPPDATA%\CokluUyap\ekler\<sha256>` olarak saklanır.
//! 4. Tek işlem (transaction): bir hata olursa hiçbir şey yazılmaz. `gorev.eski_id` sayesinde ikinci
//!    çalıştırma taşınmış işleri atlar (tekrar kayıt oluşmaz). `--deneme`: yazar, sayar, geri alır.
//!
//! Eski modelde her iş onaya gidiyordu; taşınan (özel olmayan) ana işler bu yüzden `onay_gerekir`
//! işaretli gelir. Bulut kopyası burada SİLİNMEZ (ayrı onayla, sayılar karşılaştırıldıktan sonra).

use crate::is_veri::{self, olay};
use chrono::{DateTime, Duration, NaiveDate, NaiveTime, Utc};
use postgres::GenericClient;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;

const ACIK: &[&str] = &["acik", "devam", "onayda"];

fn hata(e: postgres::Error) -> String {
    match e.as_db_error() {
        Some(d) => format!("veritabanı: {}{}", d.message(), d.detail().map(|x| format!(" ({x})")).unwrap_or_default()),
        None => format!("veritabanı: {e}"),
    }
}

// ── JSON yardımcıları ───────────────────────────────────────────────────────────────────────────

fn metin(v: &Value, k: &str) -> String {
    match &v[k] {
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        _ => String::new(),
    }
}
fn kes(s: &str, n: usize) -> String {
    s.trim().chars().take(n).collect()
}
fn sayi(v: &Value, k: &str) -> Option<i64> {
    match &v[k] {
        Value::Number(n) => n.as_i64(),
        Value::String(s) => s.trim().parse().ok(),
        Value::Bool(b) => Some(*b as i64),
        _ => None,
    }
}
fn liste<'a>(d: &'a Value, k: &str) -> &'a [Value] {
    d[k].as_array().map(|a| a.as_slice()).unwrap_or(&[])
}
fn zaman(epoch: Option<i64>) -> Option<DateTime<Utc>> {
    epoch.and_then(|e| DateTime::<Utc>::from_timestamp(e, 0))
}
/// Unix zamanı → Türkiye (UTC+3) günü ve saati; gece yarısıysa saat yok.
fn tr_gun_saat(epoch: i64) -> (NaiveDate, Option<NaiveTime>) {
    let t = DateTime::<Utc>::from_timestamp(epoch, 0).unwrap_or_default().naive_utc() + Duration::hours(3);
    let saat = t.time();
    (t.date(), if saat == NaiveTime::MIN { None } else { Some(saat) })
}
fn json_alan(v: &Value, k: &str) -> Value {
    match &v[k] {
        Value::String(s) => serde_json::from_str(s).unwrap_or(Value::String(s.clone())),
        Value::Null => Value::Object(Map::new()),
        x => x.clone(),
    }
}

/// Eski olay türü → yeni tek tür listesi (is_veri::olay). Ayrıntıda eski tür de saklanır.
fn olay_turu(eski: &str) -> &'static str {
    match eski {
        "created" | "subtask" => olay::ACILDI,
        "assigned" | "transferred" => olay::SORUMLU,
        "note" => olay::NOT,
        "file" => olay::EK,
        "member_added" => olay::KISI_EKLENDI,
        "member_removed" => olay::KISI_CIKTI,
        "ext_requested" => olay::ERTELEME_ISTENDI,
        "ext_approved" => olay::ERTELENDI,
        "ext_rejected" => olay::ERTELEME_REDDEDILDI,
        "started" | "submitted" | "approved" | "rejected" | "completed" | "reopened" | "status" | "done" => olay::DURUM,
        _ => olay::GUNCELLENDI,
    }
}

// ── İçe aktarma ─────────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Default)]
pub struct Sonuc {
    pub kullanici: usize,
    pub sablon: usize,
    pub etiket: usize,
    pub ana_is: usize,
    pub alt_is: usize,
    pub atlanan: usize,
    pub not_: usize,
    pub ek: usize,
    pub kisi: usize,
    pub erteleme: usize,
    pub olay: usize,
    pub hatirlatici: usize,
    pub bulut_acik: usize,
    pub bulut_biten: usize,
}

/// Belgesi indirilmiş dosyalar: eski belge kimliği → (sha256, boyut).
pub type Ekler = HashMap<i64, (String, i64)>;

/// Taşınacak açık ana işler ve alt işlerinin eski kimlikleri.
pub fn tasinacak_isler(d: &Value) -> HashSet<i64> {
    let kokler: HashSet<i64> = liste(d, "tasks")
        .iter()
        .filter(|t| t["parent_id"].is_null() && ACIK.contains(&metin(t, "status").as_str()))
        .filter_map(|t| sayi(t, "id"))
        .collect();
    let mut hepsi = kokler.clone();
    for t in liste(d, "tasks") {
        if let (Some(id), Some(p)) = (sayi(t, "id"), sayi(t, "parent_id")) {
            if kokler.contains(&p) {
                hepsi.insert(id);
            }
        }
    }
    hepsi
}

pub fn ice_aktar<C: GenericClient>(c: &mut C, d: &Value, ekler: &Ekler) -> Result<Sonuc, String> {
    if d["ok"] != json!(true) || !d["tasks"].is_array() {
        return Err("Dışa aktarma verisi beklenen biçimde değil.".into());
    }
    let mut s = Sonuc::default();
    is_veri::hazir_verileri_yukle(c)?;

    // Kullanıcılar: ana CokluUyap hesabı (ahmet@ofis) hesap adı olur; yalnız ad + rol.
    let mut kisi: HashMap<i64, i64> = HashMap::new();
    for u in liste(d, "users") {
        let Some(eski) = sayi(u, "id") else { continue };
        let hesap = match metin(u, "account_username") {
            h if !h.is_empty() => h,
            _ => format!("zs:{}", metin(u, "username")),
        };
        let id = is_veri::kullanici_esitle(c, &hesap, &metin(u, "display_name"), metin(u, "role") == "admin",
                                           sayi(u, "active").unwrap_or(1) != 0)?;
        kisi.insert(eski, id);
        s.kullanici += 1;
    }
    let k = |e: Option<i64>| e.and_then(|x| kisi.get(&x).copied());

    // Büro etiketleri: anahtar "zs-<eski id>". Aynı adlı etiket varsa o kullanılır.
    let mut etiket: HashMap<String, Option<i64>> = HashMap::new();
    for t in liste(d, "tags") {
        let Some(eski) = sayi(t, "id") else { continue };
        let anahtar = format!("zs-{eski}");
        let ad = kes(&metin(t, "name"), 100);
        if ad.is_empty() {
            continue;
        }
        let sahip = k(sayi(t, "owner_id"));
        let ust = metin(t, "parent");
        let n = c
            .execute(
                "INSERT INTO etiket (anahtar, ad, renk, sahip_id, ust_id)
                 VALUES ($1, $2, $3, $4, (SELECT id FROM etiket WHERE anahtar = NULLIF($5, '')))
                 ON CONFLICT DO NOTHING",
                &[&anahtar, &ad, &kes(&metin(t, "color"), 20), &sahip, &ust],
            )
            .map_err(hata)?;
        s.etiket += n as usize;
        let id: Option<i64> = c
            .query_opt(
                "SELECT id FROM etiket WHERE anahtar = $1
                 UNION ALL SELECT id FROM etiket WHERE coalesce(sahip_id, 0) = coalesce($2::bigint, 0) AND lower(ad) = lower($3)
                 LIMIT 1",
                &[&anahtar, &sahip, &ad],
            )
            .map_err(hata)?
            .map(|r| r.get(0));
        etiket.insert(eski.to_string(), id);
    }
    let mut etiket_bul = |c: &mut C, tag: &str| -> Result<Option<i64>, String> {
        if let Some(v) = etiket.get(tag) {
            return Ok(*v);
        }
        let id = c
            .query_opt("SELECT id FROM etiket WHERE anahtar = $1", &[&tag])
            .map_err(hata)?
            .map(|r| r.get::<_, i64>(0));
        etiket.insert(tag.to_string(), id);
        Ok(id)
    };

    // Süreç aşaması: "surec/asama" ya da (surec, asama)
    let asama_bul = |c: &mut C, surec: &str, asama: &str| -> Result<Option<i64>, String> {
        if surec.is_empty() || asama.is_empty() {
            return Ok(None);
        }
        Ok(c.query_opt(
            "SELECT sa.id FROM surec_asama sa JOIN surec s ON s.id = sa.surec_id WHERE s.anahtar = $1 AND sa.anahtar = $2",
            &[&surec, &asama],
        )
        .map_err(hata)?
        .map(|r| r.get(0)))
    };

    // Büro şablonları: anahtar "b-<eski id>" (eski işlerdeki sablon alanı da böyle yazılıyordu).
    let mut yeni_sablon: Vec<(i64, Value)> = Vec::new();
    for t in liste(d, "templates") {
        let Some(eski) = sayi(t, "id") else { continue };
        let anahtar = format!("b-{eski}");
        let baslik = kes(&metin(t, "title"), 300);
        if baslik.is_empty() {
            continue;
        }
        let asama = metin(t, "asama");
        let (su, a) = asama.split_once('/').unwrap_or(("", ""));
        let asama_id = asama_bul(c, su, a)?;
        let gun = sayi(t, "due_days");
        let (sure_turu, sure_deger) = match gun {
            Some(g) => ("is_gunu", g.max(0) as i32),
            None => ("tarih_sor", 0),
        };
        let oncelik = sayi(t, "priority").unwrap_or(1).clamp(0, 2) as i16;
        let r = c
            .query_opt(
                "INSERT INTO sablon (anahtar, baslik, aciklama, kontrol, oncelik, sure_turu, sure_deger, taraf, eylem,
                     surec_asama_id, olusturan_id, olusturulma)
                 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, coalesce($12, now()))
                 ON CONFLICT (anahtar) DO NOTHING RETURNING id",
                &[&anahtar, &baslik, &metin(t, "description"), &metin(t, "kontrol"), &oncelik, &sure_turu, &sure_deger,
                  &kes(&metin(t, "taraf"), 20), &kes(&metin(t, "action"), 60), &asama_id, &k(sayi(t, "created_by")),
                  &zaman(sayi(t, "created_at"))],
            )
            .map_err(hata)?;
        if let Some(r) = r {
            let id: i64 = r.get(0);
            s.sablon += 1;
            if let Value::Array(et) = json_alan(t, "etiketler") {
                for e in et.iter().filter_map(|x| x.as_str().map(String::from).or_else(|| x.as_i64().map(|n| n.to_string()))) {
                    if let Some(eid) = etiket_bul(c, &e)? {
                        c.execute("INSERT INTO sablon_etiket VALUES ($1, $2) ON CONFLICT DO NOTHING", &[&id, &eid])
                            .map_err(hata)?;
                    }
                }
            }
            yeni_sablon.push((id, json_alan(t, "sonuclar")));
        }
    }
    // Sonraki iş dalları (hedef şablon başlıkla eşlenir; bulunamayan dal atlanır).
    for (id, sonuclar) in &yeni_sablon {
        for (sonuc, _) in is_veri::SONUCLAR {
            let dal = &sonuclar[*sonuc];
            if !dal.is_object() {
                continue;
            }
            let hedef = dal["baslik"].as_str().unwrap_or("").trim().to_string();
            let sonraki: Option<i64> = if hedef.is_empty() {
                None
            } else {
                match c
                    .query_opt("SELECT id FROM sablon WHERE baslik = $1 OR $1 = ANY(eski_adlar) ORDER BY hazir DESC, id LIMIT 1", &[&hedef])
                    .map_err(hata)?
                {
                    Some(r) => Some(r.get(0)),
                    None => continue,
                }
            };
            let gun = dal["gun"].as_i64().map(|g| g.max(0) as i32);
            let sure_turu: Option<&str> = gun.map(|_| "is_gunu");
            c.execute(
                "INSERT INTO sablon_sonraki (sablon_id, sonuc, sonraki_sablon_id, sonuc_adi, sure_turu, sure_deger)
                 VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT DO NOTHING",
                &[id, sonuc, &sonraki, &kes(dal["ad"].as_str().unwrap_or(""), 120), &sure_turu, &gun],
            )
            .map_err(hata)?;
        }
    }

    // İşler
    let gorevler = liste(d, "tasks");
    for t in gorevler {
        let durum = metin(t, "status");
        if t["parent_id"].is_null() {
            if ACIK.contains(&durum.as_str()) {
                s.bulut_acik += 1;
            } else {
                s.bulut_biten += 1;
            }
        }
    }
    let tasinacak = tasinacak_isler(d);
    let mut gorev: HashMap<i64, i64> = HashMap::new(); // eski → yeni (bu çalıştırmada eklenenler)
    let mut gizli_kok: HashMap<i64, bool> = HashMap::new();
    let mut sirali: Vec<&Value> = gorevler.iter().filter(|t| sayi(t, "id").is_some_and(|i| tasinacak.contains(&i))).collect();
    sirali.sort_by_key(|t| (!t["parent_id"].is_null(), sayi(t, "id").unwrap_or(0))); // önce ana işler
    for t in sirali {
        let eski = sayi(t, "id").unwrap_or(0);
        let ust_eski = sayi(t, "parent_id");
        let ust = match ust_eski {
            Some(p) => match gorev.get(&p) {
                Some(&u) => Some(u),
                None => continue, // ana işi daha önce taşınmış: alt işler de onunla taşınmıştı
            },
            None => None,
        };
        if ust.is_none() && c.query_opt("SELECT 1 FROM gorev WHERE eski_id = $1", &[&eski]).map_err(hata)?.is_some() {
            s.atlanan += 1;
            continue;
        }
        let gizli = match ust_eski {
            Some(p) => gizli_kok.get(&p).copied().unwrap_or(false),
            None => sayi(t, "is_private").unwrap_or(0) != 0,
        };
        let durum = match (ust.is_some(), metin(t, "status").as_str()) {
            (true, "tamam") => "tamamlandi",
            (true, _) => "acik",
            (false, x) if ACIK.contains(&x) => match x {
                "devam" => "devam",
                "onayda" => "onayda",
                _ => "acik",
            },
            _ => "tamamlandi",
        };
        let olusturulma = zaman(sayi(t, "created_at"));
        let (son_tarih, son_saat) = match sayi(t, "due_at") {
            Some(e) => tr_gun_saat(e),
            None => (tr_gun_saat(sayi(t, "created_at").unwrap_or(0)).0, None),
        };
        let birim_id = metin(t, "birim_id");
        let dosya_no = kes(&metin(t, "dosya_no"), 60);
        let dosya_id: Option<i64> = if birim_id.is_empty() || dosya_no.is_empty() {
            None
        } else {
            c.query_opt(
                "SELECT d.id FROM dosya d JOIN birim b ON b.id = d.birim_id WHERE b.birim_id = $1 AND d.dosya_no = $2 LIMIT 1",
                &[&birim_id, &dosya_no],
            )
            .map_err(hata)?
            .map(|r| r.get(0))
        };
        let uyap_karti: Option<Value> = match json_alan(t, "dosya_uyap") {
            v @ Value::Object(_) if !v.as_object().is_some_and(|m| m.is_empty()) => Some(v),
            _ => None,
        };
        let sablon = metin(t, "sablon");
        let sablon_id: Option<i64> = if sablon.is_empty() {
            None
        } else {
            c.query_opt("SELECT id FROM sablon WHERE anahtar = $1", &[&sablon]).map_err(hata)?.map(|r| r.get(0))
        };
        let asama_id = asama_bul(c, &metin(t, "surec"), &metin(t, "asama"))?;
        let sonuc = match metin(t, "sonuc").as_str() {
            x @ ("olumlu" | "olumsuz" | "bekliyor") => x.to_string(),
            _ => String::new(),
        };
        let onay = ust.is_none() && !gizli;
        let oncelik = sayi(t, "priority").unwrap_or(1).clamp(0, 2) as i16;
        let sira = sayi(t, "sort_order").unwrap_or(0).clamp(i32::MIN as i64, i32::MAX as i64) as i32;
        let onaya = if durum == "onayda" { zaman(sayi(t, "submitted_at")) } else { None };
        let tamam = if durum == "tamamlandi" { zaman(sayi(t, "updated_at")) } else { None };
        let baslik = match kes(&metin(t, "title"), 300) {
            b if b.is_empty() => "(başlıksız iş)".to_string(),
            b => b,
        };
        let id: i64 = c
            .query_one(
                "INSERT INTO gorev (ust_gorev_id, baslik, aciklama, durum, onay_gerekir, oncelik, gizli, sorumlu_id,
                     olusturan_id, son_tarih, son_saat, sonuc, dosya_id, birim_adi, dosya_no, dosya_turu, taraf_adi,
                     evrak_adi, barkod, uyap_karti, sablon_id, surec_asama_id, eski_id, sira, onaya_sunulma,
                     tamamlanma, olusturulma, guncelleme)
                 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,
                     coalesce($27, now()), coalesce($28, $27, now()))
                 RETURNING id",
                &[&ust, &baslik, &metin(t, "description"), &durum, &onay, &oncelik, &gizli, &k(sayi(t, "assignee_id")),
                  &k(sayi(t, "created_by")), &son_tarih, &son_saat, &sonuc, &dosya_id, &kes(&metin(t, "birim_adi"), 255),
                  &dosya_no, &kes(&metin(t, "dosya_tur"), 60), &kes(&metin(t, "taraf_adi"), 300),
                  &kes(&metin(t, "evrak_adi"), 500), &kes(&metin(t, "barkod"), 32), &uyap_karti, &sablon_id, &asama_id,
                  &eski, &sira, &onaya, &tamam, &olusturulma, &zaman(sayi(t, "updated_at"))],
            )
            .map_err(hata)?
            .get(0);
        is_veri::arama_metnini_yenile(c, id)?;
        gorev.insert(eski, id);
        if ust.is_none() {
            gizli_kok.insert(eski, gizli);
            s.ana_is += 1;
        } else {
            s.alt_is += 1;
        }
    }
    let g = |e: Option<i64>| e.and_then(|x| gorev.get(&x).copied());

    for m in liste(d, "members") {
        let (Some(gid), Some(uid)) = (g(sayi(m, "task_id")), k(sayi(m, "user_id"))) else { continue };
        let rol = if metin(m, "role") == "approver" { "onaylayici" } else { "sorumlu" };
        s.kisi += c
            .execute(
                "INSERT INTO gorev_kisi (gorev_id, kullanici_id, rol, ekleyen_id, olusturulma)
                 VALUES ($1, $2, $3, $4, coalesce($5, now())) ON CONFLICT DO NOTHING",
                &[&gid, &uid, &rol, &k(sayi(m, "added_by")), &zaman(sayi(m, "created_at"))],
            )
            .map_err(hata)? as usize;
    }
    for n in liste(d, "notes") {
        let Some(gid) = g(sayi(n, "task_id")) else { continue };
        let ne_zaman = zaman(sayi(n, "note_at").or(sayi(n, "created_at")));
        c.execute(
            "INSERT INTO gorev_not (gorev_id, yazan_id, metin, olusturulma) VALUES ($1, $2, $3, coalesce($4, now()))",
            &[&gid, &k(sayi(n, "author_id")), &metin(n, "body"), &ne_zaman],
        )
        .map_err(hata)?;
        s.not_ += 1;
    }
    for f in liste(d, "files") {
        let (Some(gid), Some(fid)) = (g(sayi(f, "task_id")), sayi(f, "id")) else { continue };
        let Some((sha, boyut)) = ekler.get(&fid) else { continue };
        c.execute(
            "INSERT INTO gorev_ek (gorev_id, yukleyen_id, ad, mime, boyut, sha256, olusturulma)
             VALUES ($1, $2, $3, $4, $5, $6, coalesce($7, now()))",
            &[&gid, &k(sayi(f, "uploader_id")), &kes(&metin(f, "filename"), 300), &kes(&metin(f, "mime"), 120), boyut, sha,
              &zaman(sayi(f, "created_at"))],
        )
        .map_err(hata)?;
        s.ek += 1;
    }
    for x in liste(d, "extensions") {
        let Some(gid) = g(sayi(x, "task_id")) else { continue };
        let Some(yeni) = sayi(x, "new_due") else { continue };
        let (yt, ys) = tr_gun_saat(yeni);
        let (et, es) = tr_gun_saat(sayi(x, "old_due").unwrap_or(yeni));
        let durum = match metin(x, "status").as_str() {
            "kabul" => "kabul",
            "ret" => "ret",
            _ => "bekliyor",
        };
        s.erteleme += c
            .execute(
                "INSERT INTO gorev_erteleme (gorev_id, isteyen_id, eski_tarih, eski_saat, yeni_tarih, yeni_saat, gerekce, durum,
                     karar_veren_id, karar_notu, karar_zamani, olusturulma)
                 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, coalesce($12, now())) ON CONFLICT DO NOTHING",
                &[&gid, &k(sayi(x, "requester_id")), &et, &es, &yt, &ys, &metin(x, "reason"), &durum, &k(sayi(x, "decided_by")),
                  &metin(x, "decision_note"), &zaman(sayi(x, "decided_at")), &zaman(sayi(x, "created_at"))],
            )
            .map_err(hata)? as usize;
    }
    for e in liste(d, "events") {
        let Some(gid) = g(sayi(e, "task_id")) else { continue };
        let eski_tur = metin(e, "kind");
        let ayrinti = json!({"eski_tur": eski_tur, "eski": json_alan(e, "detail"), "eski_id": sayi(e, "id")});
        c.execute(
            "INSERT INTO gorev_olay (gorev_id, kullanici_id, tur, ayrinti, olusturulma) VALUES ($1, $2, $3, $4, coalesce($5, now()))",
            &[&gid, &k(sayi(e, "actor_id")), &olay_turu(&eski_tur), &ayrinti, &zaman(sayi(e, "created_at"))],
        )
        .map_err(hata)?;
        s.olay += 1;
    }
    for tt in liste(d, "task_tags") {
        let Some(gid) = g(sayi(tt, "task_id")) else { continue };
        if let Some(eid) = etiket_bul(c, &metin(tt, "tag"))? {
            c.execute("INSERT INTO gorev_etiket VALUES ($1, $2) ON CONFLICT DO NOTHING", &[&gid, &eid]).map_err(hata)?;
        }
    }
    // Bekleyen hatırlatıcılar: taşınan işlerinkiler + işten bağımsız olanlar (aynısı varsa eklenmez).
    for h in liste(d, "reminders") {
        if !h["sent_at"].is_null() {
            continue;
        }
        let (Some(uid), Some(ne_zaman)) = (k(sayi(h, "user_id")), zaman(sayi(h, "remind_at"))) else { continue };
        let gid = match sayi(h, "task_id") {
            Some(_) => match g(sayi(h, "sub_id")).or(g(sayi(h, "task_id"))) {
                Some(x) => Some(x),
                None => continue,
            },
            None => None,
        };
        let baslik = kes(&metin(h, "title"), 300);
        s.hatirlatici += c
            .execute(
                "INSERT INTO hatirlatici (gorev_id, kullanici_id, olusturan_id, zaman, baslik, notu, olusturulma)
                 SELECT $1::bigint, $2::bigint, $3::bigint, $4::timestamptz, $5::varchar, $6::text, coalesce($7, now())
                 WHERE NOT EXISTS (SELECT 1 FROM hatirlatici WHERE gorev_id IS NOT DISTINCT FROM $1::bigint
                                   AND kullanici_id = $2::bigint AND zaman = $4::timestamptz AND baslik = $5::varchar)",
                &[&gid, &uid, &k(sayi(h, "created_by")), &ne_zaman, &baslik, &metin(h, "note"), &zaman(sayi(h, "created_at"))],
            )
            .map_err(hata)? as usize;
    }
    for (eski, yeni) in &gorev {
        is_veri::olay_yaz(c, Some(*yeni), None, olay::TASINDI, json!({"kaynak": "bulut", "eski_id": eski}))?;
    }
    Ok(s)
}

// ── Buluttan alma ───────────────────────────────────────────────────────────────────────────────

struct Bulut {
    base: String,
    jeton: String,
    istemci: reqwest::blocking::Client,
}

fn buluta_baglan() -> Result<Bulut, String> {
    let cfg = crate::uyap_app::load_config();
    let mut base = cfg.server_url.trim().to_string();
    if let Some(r) = base.strip_prefix("wss://") {
        base = format!("https://{r}");
    } else if let Some(r) = base.strip_prefix("ws://") {
        base = format!("http://{r}");
    }
    let base = base.trim_end_matches('/').trim_end_matches("/ws").trim_end_matches('/').to_string();
    let parola = match std::env::var("COKLUUYAP_IS_PAROLA") {
        Ok(p) if !p.is_empty() => p,
        _ => crate::uyap_app::decrypt_secret(&cfg.password_enc),
    };
    if cfg.username.is_empty() || cfg.office_code.is_empty() || parola.is_empty() {
        return Err("Kayıtlı giriş yok: programa \"Beni hatırla\" ile giriş yapın (ya da COKLUUYAP_IS_PAROLA verin).".into());
    }
    let istemci = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(180))
        .build()
        .map_err(|e| e.to_string())?;
    let r = istemci
        .post(format!("{base}/is-listesi/api/token"))
        .json(&json!({"office_code": cfg.office_code, "username": cfg.username, "password": parola}))
        .send()
        .map_err(|e| format!("buluta ulaşılamadı: {e}"))?;
    let d: Value = r.json().unwrap_or(Value::Null);
    let jeton = d["token"].as_str().unwrap_or("").to_string();
    if jeton.is_empty() {
        return Err(format!("İş Listesi girişi yapılamadı: {}", d["error"].as_str().unwrap_or("bilinmeyen hata")));
    }
    if d["me"]["role"].as_str() != Some("admin") {
        return Err(format!("{}@{} İş Listesi'nde yönetici değil; taşıma yalnız yöneticiyle yapılır.", cfg.username, cfg.office_code));
    }
    Ok(Bulut { base, jeton, istemci })
}

impl Bulut {
    fn disa_aktar(&self) -> Result<Value, String> {
        let r = self
            .istemci
            .get(format!("{}/is-listesi/api/disa-aktar", self.base))
            .bearer_auth(&self.jeton)
            .send()
            .map_err(|e| format!("buluta ulaşılamadı: {e}"))?;
        let durum = r.status();
        let d: Value = r.json().unwrap_or(Value::Null);
        if !durum.is_success() || d["ok"] != json!(true) {
            let neden = d["error"].as_str().map(String::from).unwrap_or_else(|| format!("HTTP {durum}"));
            return Err(if durum.as_u16() == 404 {
                "Bulutta dışa aktarma ucu yok (sunucu güncellenmemiş).".into()
            } else {
                format!("dışa aktarma başarısız: {neden}")
            });
        }
        Ok(d)
    }
    fn belge(&self, id: i64) -> Result<Vec<u8>, String> {
        let r = self
            .istemci
            .get(format!("{}/is-listesi/api/disa-aktar/belge/{id}", self.base))
            .bearer_auth(&self.jeton)
            .send()
            .map_err(|e| format!("belge {id} indirilemedi: {e}"))?;
        if !r.status().is_success() {
            return Err(format!("belge {id} indirilemedi: HTTP {} {}", r.status(), r.text().unwrap_or_default()));
        }
        r.bytes().map(|b| b.to_vec()).map_err(|e| format!("belge {id} indirilemedi: {e}"))
    }
}

pub fn ekler_dizini() -> PathBuf {
    crate::veritabani::kok_dizin().join("ekler")
}

fn arsivle(d: &Value) -> Result<PathBuf, String> {
    let dizin = crate::veritabani::kok_dizin().join("arsiv");
    std::fs::create_dir_all(&dizin).map_err(|e| format!("arşiv dizini açılamadı: {e}"))?;
    let yol = dizin.join(format!("is_listesi_bulut_{}.json", chrono::Local::now().format("%Y%m%d_%H%M%S")));
    let veri = serde_json::to_vec_pretty(d).map_err(|e| e.to_string())?;
    std::fs::write(&yol, veri).map_err(|e| format!("arşiv yazılamadı: {e}"))?;
    Ok(yol)
}

/// `--bulutu-kapat`: taşıma bittikten sonra büronun bulut İş Listesi'ni salt okunur yapar (bildirimler de durur).
/// Veri silinmez.
fn bulutu_kapat() -> Result<(), String> {
    let b = buluta_baglan()?;
    let r = b
        .istemci
        .post(format!("{}/is-listesi/api/tasindi", b.base))
        .bearer_auth(&b.jeton)
        .json(&json!({}))
        .send()
        .map_err(|e| format!("buluta ulaşılamadı: {e}"))?;
    let durum = r.status();
    let d: Value = r.json().unwrap_or(Value::Null);
    if !durum.is_success() || d["ok"] != json!(true) {
        return Err(d["error"].as_str().map(String::from).unwrap_or_else(|| format!("HTTP {durum}")));
    }
    println!("Bulut İş Listesi kapatıldı (büro: {}): salt okunur, bildirim gitmez. Veri silinmedi.", d["office_id"].as_str().unwrap_or(""));
    Ok(())
}

/// `--bulut-kopyasini-sil [--evet]`: taşınmış büronun buluttaki İş Listesi kayıtlarını siler (kullanıcı onayı
/// 17.11.2026; GERİ ALINAMAZ). Önce buluttan son kez dışa aktarılıp ofis diskine arşivlenir; buluttaki her açık
/// ana iş büro veritabanında (eski_id) bulunmalı ve bitmiş işlerde belge olmamalıdır, yoksa hiçbir şey silinmez.
/// Sunucu da iş sayısını yeniden sayar (arada değişiklik varsa reddeder). Kullanıcılar (giriş) ve bildirim
/// abonelikleri bulutta kalır. `--evet` verilmezse yalnız denetler ve ne silineceğini yazar.
fn bulut_kopyasini_sil(evet: bool) -> Result<(), String> {
    let b = buluta_baglan()?;
    println!("Silmeden önce buluttan son kez dışa aktarılıyor ({})…", b.base);
    let d = b.disa_aktar()?;
    println!("Arşiv: {}", arsivle(&d)?.display());
    let isler = liste(&d, "tasks");
    let tasinacak = tasinacak_isler(&d);
    let biten_belge = liste(&d, "files")
        .iter()
        .filter(|f| sayi(f, "task_id").is_some_and(|t| !tasinacak.contains(&t)))
        .count();
    if biten_belge > 0 {
        return Err(format!("bitmiş işlerde {biten_belge} belge var; arşive yalnız kayıtları giriyor, önce belgeler indirilmeli"));
    }
    let acik_kokler: Vec<i64> = isler
        .iter()
        .filter(|t| t["parent_id"].is_null() && ACIK.contains(&metin(t, "status").as_str()))
        .filter_map(|t| sayi(t, "id"))
        .collect();
    let gunluk: crate::veritabani::Gunluk = std::sync::Arc::new(|m: &str| println!("{m}"));
    crate::veritabani::baslat(gunluk)?;
    let dsn = crate::veritabani::dsn().ok_or("büro veritabanı hazır değil")?;
    let mut c = postgres::Client::connect(&dsn, postgres::NoTls).map_err(hata)?;
    let bulunan: i64 = c
        .query_one("SELECT count(*) FROM gorev WHERE eski_id = ANY($1)", &[&acik_kokler])
        .map_err(hata)?
        .get(0);
    if bulunan as usize != acik_kokler.len() {
        return Err(format!(
            "buluttaki {} açık ana işin yalnız {bulunan} tanesi büro veritabanında; önce --is-listesi-aktar çalıştırın",
            acik_kokler.len()
        ));
    }
    println!(
        "Denetim tamam: {} iş (alt işler dahil), {} açık ana işin hepsi büro veritabanında; {} not, {} geçmiş kaydı, {} hatırlatıcı, {} şablon.",
        isler.len(), acik_kokler.len(), liste(&d, "notes").len(), liste(&d, "events").len(),
        liste(&d, "reminders").len(), liste(&d, "templates").len()
    );
    if !evet {
        println!("Silinmedi (deneme). Silmek için --evet ekleyin.");
        return Ok(());
    }
    let r = b
        .istemci
        .post(format!("{}/is-listesi/api/tasindi/sil", b.base))
        .bearer_auth(&b.jeton)
        .json(&json!({"onay": "SIL", "is_sayisi": isler.len()}))
        .send()
        .map_err(|e| format!("buluta ulaşılamadı: {e}"))?;
    let durum = r.status();
    let y: Value = r.json().unwrap_or(Value::Null);
    if !durum.is_success() || y["ok"] != json!(true) {
        return Err(if durum.as_u16() == 404 {
            "bulutta silme ucu yok (sunucu güncellenmemiş)".into()
        } else {
            y["error"].as_str().map(String::from).unwrap_or_else(|| format!("HTTP {durum}"))
        });
    }
    println!("Bulut İş Listesi kopyası silindi (büro: {}): {}", y["office_id"].as_str().unwrap_or(""), y["silinen"]);
    Ok(())
}

pub fn komut(argv: &[String]) -> i32 {
    if argv.iter().any(|a| a == "--bulut-kopyasini-sil") {
        return match bulut_kopyasini_sil(argv.iter().any(|a| a == "--evet")) {
            Ok(()) => 0,
            Err(e) => {
                eprintln!("BULUT KOPYASI SİLİNMEDİ: {e}");
                2
            }
        };
    }
    if argv.iter().any(|a| a == "--bulutu-kapat") {
        return match bulutu_kapat() {
            Ok(()) => 0,
            Err(e) => {
                eprintln!("BULUT KAPATILAMADI: {e}");
                2
            }
        };
    }
    let deneme = argv.iter().any(|a| a == "--deneme");
    let dosya = argv.iter().position(|a| a == "--dosya").and_then(|i| argv.get(i + 1)).cloned();
    match calistir(deneme, dosya) {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("TAŞIMA YAPILMADI: {e}");
            2
        }
    }
}

fn calistir(deneme: bool, dosya: Option<String>) -> Result<(), String> {
    let (d, bulut) = match &dosya {
        Some(p) => {
            let ham = std::fs::read(p).map_err(|e| format!("{p} okunamadı: {e}"))?;
            (serde_json::from_slice::<Value>(&ham).map_err(|e| format!("{p} JSON değil: {e}"))?, None)
        }
        None => {
            let b = buluta_baglan()?;
            println!("Buluttan İş Listesi alınıyor ({})…", b.base);
            (b.disa_aktar()?, Some(b))
        }
    };
    if bulut.is_some() {
        println!("Arşiv: {}", arsivle(&d)?.display());
    }
    let tasinacak = tasinacak_isler(&d);
    // Belgeler önce indirilir (veritabanı işlemi açıkken ağ beklenmesin); deneme kipinde indirilmez.
    let mut ekler = Ekler::new();
    let istenen: Vec<&Value> = liste(&d, "files")
        .iter()
        .filter(|f| sayi(f, "task_id").is_some_and(|t| tasinacak.contains(&t)))
        .collect();
    if !istenen.is_empty() {
        match (&bulut, deneme) {
            (Some(b), false) => {
                let dizin = ekler_dizini();
                std::fs::create_dir_all(&dizin).map_err(|e| format!("ekler dizini açılamadı: {e}"))?;
                for f in &istenen {
                    let id = sayi(f, "id").unwrap_or(0);
                    let veri = b.belge(id)?;
                    let sha = hex::encode(Sha256::digest(&veri));
                    let yol = dizin.join(&sha);
                    if !yol.exists() {
                        std::fs::write(&yol, &veri).map_err(|e| format!("belge yazılamadı: {e}"))?;
                    }
                    let bulut_sha = metin(f, "sha256");
                    if !bulut_sha.is_empty() && !bulut_sha.eq_ignore_ascii_case(&sha) {
                        println!("  uyarı: belge {id} özeti buluttakinden farklı ({}); indirilen kullanıldı", metin(f, "filename"));
                    }
                    ekler.insert(id, (sha, veri.len() as i64));
                }
                println!("{} belge indirildi → {}", ekler.len(), dizin.display());
            }
            _ => println!("{} belge {}", istenen.len(), if deneme { "(deneme: indirilmedi)" } else { "(arşivden: indirilemez, atlandı)" }),
        }
    }

    let gunluk: crate::veritabani::Gunluk = std::sync::Arc::new(|m: &str| println!("{m}"));
    crate::veritabani::baslat(gunluk)?;
    let dsn = crate::veritabani::dsn().ok_or("büro veritabanı hazır değil")?;
    let mut c = postgres::Client::connect(&dsn, postgres::NoTls).map_err(hata)?;
    let mut tx = c.transaction().map_err(hata)?;
    let s = ice_aktar(&mut tx, &d, &ekler)?;
    // Doğrulama: buluttaki her açık ana iş veritabanında (eski_id ile) olmalı.
    let acik_kokler: Vec<i64> = liste(&d, "tasks")
        .iter()
        .filter(|t| t["parent_id"].is_null() && ACIK.contains(&metin(t, "status").as_str()))
        .filter_map(|t| sayi(t, "id"))
        .collect();
    let bulunan: i64 = tx
        .query_one("SELECT count(*) FROM gorev WHERE eski_id = ANY($1)", &[&acik_kokler])
        .map_err(hata)?
        .get(0);
    println!("Bulutta: {} açık ana iş, {} bitmiş ana iş (bitmişler yalnız arşivde).", s.bulut_acik, s.bulut_biten);
    println!(
        "Yazılan: {} kullanıcı, {} şablon, {} etiket, {} ana iş, {} alt iş, {} not, {} belge, {} kişi, {} ek süre, {} geçmiş kaydı, {} hatırlatıcı; {} iş zaten taşınmıştı.",
        s.kullanici, s.sablon, s.etiket, s.ana_is, s.alt_is, s.not_, s.ek, s.kisi, s.erteleme, s.olay, s.hatirlatici, s.atlanan
    );
    if bulunan as usize != acik_kokler.len() {
        tx.rollback().map_err(hata)?;
        return Err(format!("doğrulama tutmadı: {} açık işin {} tanesi veritabanında; geri alındı", acik_kokler.len(), bulunan));
    }
    println!("Doğrulama: {} açık işin hepsi veritabanında.", bulunan);
    if deneme {
        tx.rollback().map_err(hata)?;
        println!("DENEME: hiçbir şey yazılmadı (geri alındı).");
    } else {
        tx.commit().map_err(hata)?;
        println!("Taşıma tamamlandı. Bulut kopyası silinmedi.");
    }
    Ok(())
}

// ── Test (gömülü PostgreSQL uçtan uca: veritabani::tests::uctan_uca) ────────────────────────────

#[cfg(test)]
pub(crate) fn veritabani_senaryosu(c: &mut postgres::Client) {
    let d: Value = serde_json::from_str(include_str!("../test_veri/zs_disa_aktar_ornek.json")).unwrap();
    let mut ekler = Ekler::new();
    for f in liste(&d, "files") {
        ekler.insert(sayi(f, "id").unwrap(), ("ab".repeat(32), 3));
    }
    let mut tx = c.transaction().unwrap();
    let s = ice_aktar(&mut tx, &d, &ekler).unwrap();
    tx.commit().unwrap();
    assert_eq!((s.bulut_acik, s.bulut_biten), (2, 1), "{s:?}");
    assert_eq!((s.ana_is, s.alt_is, s.atlanan), (2, 1, 0), "{s:?}");
    assert_eq!((s.not_, s.kisi, s.erteleme, s.hatirlatici, s.sablon), (1, 1, 1, 1, 1), "{s:?}");
    let r = c
        .query_one(
            "SELECT g.durum, g.onay_gerekir, g.oncelik, g.dosya_no, g.uyap_karti->>'dosyaNo', s.anahtar, u.hesap_adi,
                    (SELECT count(*) FROM gorev a WHERE a.ust_gorev_id = g.id),
                    (SELECT count(*) FROM gorev_etiket e WHERE e.gorev_id = g.id), g.arama_metni
             FROM gorev g LEFT JOIN sablon s ON s.id = g.sablon_id LEFT JOIN kullanici u ON u.id = g.sorumlu_id
             WHERE g.baslik = 'Tebligat kontrol'",
            &[],
        )
        .unwrap();
    assert_eq!(r.get::<_, String>(0), "acik");
    assert!(r.get::<_, bool>(1), "eski işler onaylı gelir");
    assert_eq!(r.get::<_, i16>(2), 2);
    assert_eq!(r.get::<_, String>(3), "2026/123");
    assert_eq!(r.get::<_, Option<String>>(4).as_deref(), Some("2026/123"));
    assert_eq!(r.get::<_, Option<String>>(5).as_deref(), Some("h-teblig-durum"));
    assert_eq!(r.get::<_, Option<String>>(6).as_deref(), Some("ali@ofis1"));
    assert_eq!(r.get::<_, i64>(7), 1, "alt iş");
    assert!(r.get::<_, i64>(8) >= 1, "etiket");
    assert!(r.get::<_, String>(9).contains("tebligat"), "arama metni");
    let ozel: (bool, bool) = c
        .query_one("SELECT gizli, onay_gerekir FROM gorev WHERE baslik = 'Özel iş'", &[])
        .map(|r| (r.get(0), r.get(1)))
        .unwrap();
    assert_eq!(ozel, (true, false));
    assert_eq!(c.query_one("SELECT count(*) FROM gorev WHERE baslik = 'Bitmiş iş'", &[]).unwrap().get::<_, i64>(0), 0);
    assert!(c.query_one("SELECT count(*) FROM gorev_olay WHERE tur = 'tasindi'", &[]).unwrap().get::<_, i64>(0) >= 3);
    // İkinci çalıştırma: tekrar kayıt yok.
    let mut tx = c.transaction().unwrap();
    let s2 = ice_aktar(&mut tx, &d, &ekler).unwrap();
    tx.commit().unwrap();
    assert_eq!((s2.ana_is, s2.alt_is, s2.atlanan, s2.not_, s2.hatirlatici, s2.sablon), (0, 0, 2, 0, 0, 0), "{s2:?}");
    // Senaryo sonrası temizlik (diğer senaryolar boş tablo bekleyebilir)
    c.batch_execute("DELETE FROM gorev WHERE eski_id IS NOT NULL; DELETE FROM sablon WHERE anahtar LIKE 'b-%'; DELETE FROM etiket WHERE anahtar LIKE 'zs-%';")
        .unwrap();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gun_saat_turkiye() {
        // 2026-11-16 14:00 UTC = 17:00 TR
        let e = DateTime::parse_from_rfc3339("2026-11-16T14:00:00Z").unwrap().timestamp();
        assert_eq!(tr_gun_saat(e), (NaiveDate::from_ymd_opt(2026, 11, 16).unwrap(), NaiveTime::from_hms_opt(17, 0, 0)));
        // TR gece yarısı → saat yok, gün bir sonraki UTC günü değil TR günü
        let e = DateTime::parse_from_rfc3339("2026-11-16T21:00:00Z").unwrap().timestamp();
        assert_eq!(tr_gun_saat(e), (NaiveDate::from_ymd_opt(2026, 11, 17).unwrap(), None));
    }

    #[test]
    fn tasinacaklar_acik_kok_ve_alt_isleri() {
        let d = json!({"tasks": [
            {"id": 1, "parent_id": null, "status": "acik"}, {"id": 2, "parent_id": 1, "status": "tamam"},
            {"id": 3, "parent_id": null, "status": "tamamlandi"}, {"id": 4, "parent_id": 3, "status": "acik"},
            {"id": 5, "parent_id": null, "status": "onayda"}]});
        let t = tasinacak_isler(&d);
        assert_eq!(t, [1, 2, 5].into_iter().collect::<HashSet<i64>>());
    }

    #[test]
    fn olay_turleri() {
        assert_eq!(olay_turu("created"), olay::ACILDI);
        assert_eq!(olay_turu("submitted"), olay::DURUM);
        assert_eq!(olay_turu("bilinmeyen"), olay::GUNCELLENDI);
    }
}
