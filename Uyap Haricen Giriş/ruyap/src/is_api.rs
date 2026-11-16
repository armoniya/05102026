//! İş Listesi uçları (plan L3, 16.11.2026): `/__cu__/veri/is/…`, büro veritabanından (is_veri.rs).
//!
//! Buluttaki İş Listesi'nin yerini alır; veri ofis bilgisayarında kalır. Her istek bir KİŞİ adına
//! çalışır (kimlik.rs: bu bilgisayar / tünel bileti / yerel ağ çerezi); kimliği bilinmeyen istek
//! reddedilir. Yetki kuralları veri katmanındadır (gizli iş yalnız sahibine, onayı yalnız yönetici /
//! onaylayıcı / işi başkasına açan). Başka siteden gelen yazma isteği reddedilir.
//! Yanıt: `{"ok": true, "veri": …}` ya da `{"ok": false, "error": "…"}`.
//!
//!   GET  ben                  kişi, sayaçlar, kişiler, etiketler, şablonlar, sabit listeler
//!   GET  liste                ?gorunum=bugun|onay|tum&ara=&durum=&tur=&sorumlu=&etiket=&dosya_no=&birim_adi=&ilk=&son=
//!   GET  gorev                ?id=  — ayrıntı (alt işler, notlar, kişiler, ertelemeler, geçmiş, yetki)
//!   GET  takvim               ?ilk=&son=&herkes=1
//!   GET  ozet · sablonlar · surecler · etiketler · kisiler · tekrarlar
//!   GET  evrak_sablon         ?ad=  — belge adına uyan şablonlar (İş ata penceresi)
//!   POST ac                   yeni iş (is_veri::YeniGorev alanları)
//!   POST guncelle             {id, …değişen alanlar}
//!   POST durum                {id, durum, sonuc, not} → {durum, oneri}
//!   POST oneriden_ac          {onceki, sablon_id, son_tarih, son_saat, sorumlu_id}
//!   POST ertele               {id, yeni_tarih, yeni_saat, gerekce} → {uygulandi}
//!   POST erteleme_karar       {id, kabul, notu}
//!   POST not                  {id, metin}
//!   POST kisi_ekle / kisi_cikar {id, kullanici, rol}
//!   POST sablon               şablon ekle / düzenle
//!   POST tekrar · tekrar_durdur {id, aktif}

use crate::is_veri::{self as iv, Kisi};
use chrono::NaiveDate;
use postgres::GenericClient;
use serde_json::{json, Value};
use std::collections::HashMap;

pub const ON_EK: &str = "__cu__/veri/is/";

pub fn is_path(path: &str) -> bool {
    path.trim_start_matches('/').starts_with(ON_EK)
}

type Yanit = (u16, HashMap<String, String>, Vec<u8>);

fn yanit(durum: u16, govde: Value) -> Yanit {
    let mut h = HashMap::new();
    h.insert("Content-Type".to_string(), "application/json; charset=utf-8".to_string());
    h.insert("Cache-Control".to_string(), "no-store".to_string());
    (durum, h, serde_json::to_vec(&govde).unwrap_or_else(|_| b"{}".to_vec()))
}

fn hata(e: postgres::Error) -> String {
    match e.as_db_error() {
        Some(d) => format!("veritabanı: {}", d.message()),
        None => format!("veritabanı: {e}"),
    }
}

fn sayi(v: &Value) -> Option<i64> {
    v.as_i64().or_else(|| v.as_str().and_then(|s| s.trim().parse().ok()))
}
fn tam(g: &Value, k: &str) -> Result<i64, String> {
    sayi(&g[k]).ok_or_else(|| format!("'{k}' eksik."))
}
fn metin(g: &Value, k: &str) -> String {
    g[k].as_str().unwrap_or("").trim().to_string()
}
fn tarih(s: &str, ad: &str) -> Result<Option<NaiveDate>, String> {
    iv::tarih_coz(s).map_err(|_| format!("{ad} anlaşılamadı."))
}

/// Hesap adından büro kullanıcısı. Hiç görülmemiş hesap üye olarak eklenir (ad, buluttan eşitlenince
/// düzelir); pasifleştirilmiş hesap reddedilir.
pub fn kisi_getir<C: GenericClient>(c: &mut C, hesap: &str) -> Result<Kisi, String> {
    if let Some(k) = iv::kisi_bul(c, hesap)? {
        return Ok(k);
    }
    let ad = hesap.split('@').next().unwrap_or(hesap);
    c.execute(
        "INSERT INTO kullanici (hesap_adi, ad, rol) VALUES ($1, $2, 'uye') ON CONFLICT (hesap_adi) DO NOTHING",
        &[&hesap.trim(), &ad],
    )
    .map_err(hata)?;
    iv::kisi_bul(c, hesap)?.ok_or_else(|| "Bu hesap büroda pasif; İş Listesi'ni kullanamaz.".to_string())
}

fn sabitler() -> Value {
    let liste = |l: &[(&str, &str)]| l.iter().map(|(k, a)| json!({"kod": k, "ad": a})).collect::<Vec<_>>();
    json!({
        "turler": liste(iv::TURLER), "durumlar": liste(iv::DURUMLAR), "sonuclar": liste(iv::SONUCLAR),
        "sure_turleri": liste(iv::SURE_TURLERI), "tekrar_kurallari": liste(iv::TEKRAR_KURALLARI),
    })
}

fn dagit<C: GenericClient>(
    c: &mut C,
    hesap: &str,
    yontem: &str,
    uc: &str,
    q: &HashMap<String, String>,
    g: &Value,
    bugun: NaiveDate,
) -> Result<Option<Value>, String> {
    let k = kisi_getir(c, hesap)?;
    let qs = |a: &str| q.get(a).map(|s| s.trim().to_string()).unwrap_or_default();
    let qi = |a: &str| q.get(a).and_then(|s| s.trim().parse::<i64>().ok());
    Ok(Some(match (yontem, uc) {
        ("GET", "ben") => {
            // Rutinlerin bugünkü örnekleri burada üretilir (sayfa açılışı); her kural en çok bir açık örnek.
            iv::tekrarlari_uret(c, bugun)?;
            let r = c
                .query_one("SELECT id, hesap_adi, ad, rol FROM kullanici WHERE id = $1", &[&k.id])
                .map_err(hata)?;
            json!({
                "ben": {"id": r.get::<_, i64>(0), "hesap": r.get::<_, String>(1), "ad": r.get::<_, String>(2),
                        "yonetici": r.get::<_, String>(3) == "yonetici"},
                "bugun": bugun.to_string(),
                "ozet": iv::ozet(c, &k, bugun)?,
                "kisiler": iv::kullanicilar(c)?,
                "etiketler": iv::etiketler(c, &k)?,
                "sablonlar": iv::sablonlar(c)?,
                "sabitler": sabitler(),
            })
        }
        ("GET", "ozet") => iv::ozet(c, &k, bugun)?,
        ("GET", "liste") => {
            let durumlar: Vec<String> =
                qs("durum").split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect();
            let s = iv::Suzgec {
                gorunum: qs("gorunum"),
                sorumlu_id: qi("sorumlu"),
                durumlar,
                tur: qs("tur"),
                ara: qs("ara"),
                dosya_id: qi("dosya"),
                dosya_no: qs("dosya_no"),
                birim_adi: qs("birim_adi"),
                etiket_id: qi("etiket"),
                ilk: tarih(&qs("ilk"), "Başlangıç tarihi")?,
                son: tarih(&qs("son"), "Bitiş tarihi")?,
                sinir: qi("sinir").unwrap_or(300),
            };
            json!(iv::gorevler(c, &k, &s, bugun)?)
        }
        ("GET", "gorev") => {
            let id = qi("id").ok_or("'id' eksik.")?;
            iv::gorev_ayrinti(c, &k, id, bugun)?.ok_or("İş bulunamadı.")?
        }
        ("GET", "takvim") => {
            let ilk = tarih(&qs("ilk"), "Başlangıç tarihi")?.unwrap_or(bugun);
            let son = tarih(&qs("son"), "Bitiş tarihi")?.unwrap_or(ilk + chrono::Duration::days(41));
            if (son - ilk).num_days() > 120 {
                return Err("Takvim en çok 120 gün gösterir.".into());
            }
            iv::takvim(c, &k, ilk, son, qs("herkes") == "1", bugun)?
        }
        ("GET", "sablonlar") => json!(iv::sablonlar(c)?),
        ("GET", "surecler") => json!(iv::surecler(c)?),
        ("GET", "etiketler") => json!(iv::etiketler(c, &k)?),
        ("GET", "kisiler") => json!(iv::kullanicilar(c)?),
        ("GET", "tekrarlar") => json!(iv::tekrar_kurallari(c, &k)?),
        ("GET", "evrak_sablon") => json!(iv::evraga_uyan_sablonlar(c, &qs("ad"))?),
        ("POST", "ac") => {
            let mut y = iv::YeniGorev::jsondan(g)?;
            // Dosya kartından açılan iş: UYAP birim kimliği + dosya no ile icra şemasındaki dosyaya bağlanır
            // (dosya veritabanında yoksa künye metinleriyle bağsız kalır).
            let birim_uyap = metin(g, "birim_uyap_id");
            if y.dosya_id.is_none() && !birim_uyap.is_empty() && !y.dosya_no.is_empty() {
                y.dosya_id = c
                    .query_opt(
                        "SELECT d.id FROM dosya d JOIN birim b ON b.id = d.birim_id WHERE b.birim_id = $1 AND d.dosya_no = $2 LIMIT 1",
                        &[&birim_uyap, &y.dosya_no],
                    )
                    .map_err(hata)?
                    .map(|r| r.get(0));
            }
            json!({"id": iv::gorev_ac(c, Some(&k), &y, bugun)?})
        }
        ("POST", "guncelle") => {
            iv::gorev_guncelle(c, &k, tam(g, "id")?, g)?;
            Value::Null
        }
        ("POST", "durum") => {
            let s = iv::durum_degistir(c, &k, tam(g, "id")?, &metin(g, "durum"), &metin(g, "sonuc"), &metin(g, "not"), bugun)?;
            json!({"durum": s.durum, "oneri": s.oneri.map(|o| o.json())})
        }
        ("POST", "oneriden_ac") => {
            let son_tarih = tarih(&metin(g, "son_tarih"), "Son tarih")?.ok_or("Son tarih seçilmeli.")?;
            let son_saat = iv::saat_coz(&metin(g, "son_saat"))?;
            let id = iv::oneriden_ac(c, &k, tam(g, "onceki")?, tam(g, "sablon_id")?, son_tarih, son_saat, sayi(&g["sorumlu_id"]), bugun)?;
            json!({"id": id})
        }
        ("POST", "ertele") => {
            let yeni = tarih(&metin(g, "yeni_tarih"), "Yeni tarih")?.ok_or("Yeni tarih seçilmeli.")?;
            let saat = iv::saat_coz(&metin(g, "yeni_saat"))?;
            json!({"uygulandi": iv::ertele(c, &k, tam(g, "id")?, yeni, saat, &metin(g, "gerekce"))?})
        }
        ("POST", "erteleme_karar") => {
            iv::erteleme_karar(c, &k, tam(g, "id")?, g["kabul"].as_bool().unwrap_or(false), &metin(g, "notu"))?;
            Value::Null
        }
        ("POST", "not") => json!({"id": iv::not_ekle(c, &k, tam(g, "id")?, &metin(g, "metin"))?}),
        ("POST", "kisi_ekle") => {
            iv::kisi_ekle(c, &k, tam(g, "id")?, tam(g, "kullanici")?, &metin(g, "rol"))?;
            Value::Null
        }
        ("POST", "kisi_cikar") => {
            iv::kisi_cikar(c, &k, tam(g, "id")?, tam(g, "kullanici")?, &metin(g, "rol"))?;
            Value::Null
        }
        ("POST", "sablon") => json!({"id": iv::sablon_kaydet(c, &k, g)?}),
        ("POST", "tekrar") => json!({"id": iv::tekrar_kurali_ekle(c, &k, g)?}),
        ("POST", "tekrar_durdur") => {
            iv::tekrar_kurali_durdur(c, &k, tam(g, "id")?, g["aktif"].as_bool().unwrap_or(false))?;
            Value::Null
        }
        _ => return Ok(None),
    }))
}

/// `/__cu__/veri/is/…` isteği. `hesap`: isteğin sahibi (kimlik.rs); yoksa reddedilir.
pub async fn handle(method: &str, path: &str, query: &str, body: &[u8], capraz_site: bool, hesap: Option<&str>) -> Yanit {
    let yontem = method.trim().to_uppercase();
    let uc = path.trim_start_matches('/').trim_start_matches(ON_EK).trim_matches('/').to_string();
    if yontem != "GET" && capraz_site {
        return yanit(403, json!({"ok": false, "error": "Bu istek başka bir siteden geldi; reddedildi."}));
    }
    let Some(hesap) = hesap.map(str::trim).filter(|h| !h.is_empty()).map(String::from) else {
        return yanit(
            401,
            json!({"ok": false, "kod": "KIMLIK",
                   "error": "Kim olduğunuz doğrulanamadı. Yerel ağdan bağlanıyorsanız kendi Çoklu UYAP hesabınızla yeniden giriş yapın."}),
        );
    };
    if !crate::veritabani::hazir() {
        return yanit(503, json!({"ok": false, "kod": "E-402", "error": "Büro veritabanı hazır değil."}));
    }
    let q: HashMap<String, String> = url::form_urlencoded::parse(query.as_bytes()).into_owned().collect();
    let g: Value = if body.is_empty() {
        json!({})
    } else {
        match serde_json::from_slice(body) {
            Ok(v) => v,
            Err(_) => return yanit(400, json!({"ok": false, "error": "İstek gövdesi JSON değil."})),
        }
    };
    let sonuc = tokio::task::spawn_blocking(move || {
        let mut c = crate::veritabani::baglanti()?;
        let bugun = chrono::Local::now().date_naive();
        dagit(&mut *c, &hesap, &yontem, &uc, &q, &g, bugun)
    })
    .await;
    match sonuc {
        Ok(Ok(Some(veri))) => yanit(200, json!({"ok": true, "veri": veri})),
        Ok(Ok(None)) => yanit(404, json!({"ok": false, "error": "Böyle bir uç yok."})),
        Ok(Err(e)) => {
            let sunucu_hatasi = e.starts_with("veritabanı") || e.contains("büro veritabanı");
            yanit(if sunucu_hatasi { 500 } else { 400 }, json!({"ok": false, "error": e}))
        }
        Err(e) => yanit(500, json!({"ok": false, "error": format!("İşlem tamamlanamadı: {e}")})),
    }
}

/// Büro üyelerini buluttaki İş Listesi hesaplarından (yalnız ad + rol) eşitler. Ofis programı paylaşımı
/// başlatınca çağrılır; yönetici değilse yalnız kendi hesabı eklenir.
pub async fn uyeleri_esitle() {
    let (ben, uyeler) = match crate::is_listesi::uye_listesi().await {
        Ok(x) => x,
        Err(e) => {
            println!("[İŞ LİSTESİ] Büro üyeleri eşitlenemedi: {e}");
            return;
        }
    };
    if let Some(h) = ben.as_ref().and_then(|b| b["account_username"].as_str()) {
        crate::kimlik::ofis_hesabi_ayarla(h);
    }
    let sonuc = tokio::task::spawn_blocking(move || -> Result<usize, String> {
        for _ in 0..30 {
            if crate::veritabani::hazir() {
                break;
            }
            std::thread::sleep(std::time::Duration::from_secs(2));
        }
        let mut c = crate::veritabani::baglanti()?;
        let mut n = 0;
        for u in uyeler.iter().chain(ben.iter()) {
            let Some(h) = u["account_username"].as_str().filter(|h| !h.is_empty()) else { continue };
            iv::kullanici_esitle(&mut *c, h, u["display_name"].as_str().unwrap_or(""), u["role"].as_str() == Some("admin"),
                                 u["active"].as_bool().unwrap_or(true))?;
            n += 1;
        }
        Ok(n)
    })
    .await;
    match sonuc {
        Ok(Ok(n)) => println!("[İŞ LİSTESİ] {n} büro üyesi eşitlendi."),
        Ok(Err(e)) => println!("[İŞ LİSTESİ] Büro üyeleri yazılamadı: {e}"),
        Err(e) => println!("[İŞ LİSTESİ] Büro üyeleri yazılamadı: {e}"),
    }
}

// ── Test (gömülü PostgreSQL uçtan uca: veritabani::tests::uctan_uca) ────────────────────────────

#[cfg(test)]
pub(crate) fn veritabani_senaryosu(c: &mut postgres::Client) {
    let bugun = NaiveDate::from_ymd_opt(2026, 11, 16).unwrap();
    let bos = HashMap::new();
    let al = |c: &mut postgres::Client, h: &str, uc: &str, q: &[(&str, &str)]| -> Result<Value, String> {
        let q: HashMap<String, String> = q.iter().map(|(a, b)| (a.to_string(), b.to_string())).collect();
        dagit(c, h, "GET", uc, &q, &json!({}), bugun).map(|v| v.expect("uç"))
    };
    let gonder = |c: &mut postgres::Client, h: &str, uc: &str, g: Value| -> Result<Value, String> {
        dagit(c, h, "POST", uc, &HashMap::new(), &g, bugun).map(|v| v.expect("uç"))
    };
    iv::kullanici_esitle(c, "mudur@api", "Müdür", true, true).unwrap();
    // Görülmemiş hesap üye olarak eklenir
    let ben = al(c, "katip@api", "ben", &[]).unwrap();
    assert_eq!((ben["ben"]["ad"].as_str(), ben["ben"]["yonetici"].as_bool()), (Some("katip"), Some(false)));
    assert!(ben["sabitler"]["turler"].as_array().unwrap().len() >= 5);
    let katip = ben["ben"]["id"].as_i64().unwrap();
    // Müdür katibe onaylı iş açar
    let id = gonder(c, "mudur@api", "ac", json!({"baslik": "API deneme işi", "son_tarih": "2026-11-16", "sorumlu_id": katip,
                                                 "onay_gerekir": true})).unwrap()["id"].as_i64().unwrap();
    let liste = al(c, "katip@api", "liste", &[("gorunum", "bugun")]).unwrap();
    assert!(liste.as_array().unwrap().iter().any(|g| g["id"] == id), "katibin bugünkü işlerinde");
    // Katip tamamlayınca onaya gider; müdür onaylar
    assert_eq!(gonder(c, "katip@api", "durum", json!({"id": id, "durum": "tamamla"})).unwrap()["durum"], "onayda");
    assert!(gonder(c, "katip@api", "durum", json!({"id": id, "durum": "tamamla"})).is_err(), "kendi işini onaylayamaz");
    assert_eq!(al(c, "mudur@api", "ozet", &[]).unwrap()["onay"], 1);
    assert_eq!(gonder(c, "mudur@api", "durum", json!({"id": id, "durum": "tamamla"})).unwrap()["durum"], "tamamlandi");
    // Not, ayrıntı, gizli iş başkasına görünmez
    gonder(c, "katip@api", "not", json!({"id": id, "metin": "Bitti"})).unwrap();
    assert_eq!(al(c, "mudur@api", "gorev", &[("id", &id.to_string())]).unwrap()["notlar"][0]["metin"], "Bitti");
    let gizli = gonder(c, "katip@api", "ac", json!({"baslik": "Kişisel", "son_tarih": "2026-11-17", "gizli": true})).unwrap()["id"]
        .as_i64()
        .unwrap();
    assert!(al(c, "mudur@api", "gorev", &[("id", &gizli.to_string())]).is_err(), "gizli iş yöneticiye de görünmez");
    // Erteleme (onay gerekmeyen iş: doğrudan uygulanır)
    assert_eq!(gonder(c, "katip@api", "ertele", json!({"id": gizli, "yeni_tarih": "2026-11-18", "gerekce": "yarın"})).unwrap()["uygulandi"], true);
    assert!(dagit(c, "katip@api", "GET", "olmayan", &bos, &json!({}), bugun).unwrap().is_none());
    // Pasif hesap reddedilir
    iv::kullanici_esitle(c, "eski@api", "Eski", false, false).unwrap();
    assert!(al(c, "eski@api", "ben", &[]).is_err());
    c.batch_execute("DELETE FROM gorev WHERE baslik IN ('API deneme işi', 'Kişisel')").unwrap();
}
