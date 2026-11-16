//! Eski UyapMasa veritabanından (Django `icra_models_*` tabloları) büro veritabanına icra verisi aktarımı.
//!
//! `sema/0002_icra.sql` UyapMasa modelinden alan alan üretildiği için (araclar/sema_uret.py) tablolar
//! birebir eşleşir: `icra_models_dosyataraf` → `dosya_taraf`. Kimlikler (id) korunur, böylece yabancı
//! anahtarlar olduğu gibi geçer; sonunda kimlik dizileri en büyük id'ye ilerletilir.
//!
//! Güvenlik: hedef icra tabloları boş değilse hiçbir şey yazılmaz (aynı id'li farklı kayıt, çocuk
//! kayıtları yanlış dosyaya bağlardı). Tüm aktarım tek işlemdir: yarıda kalırsa hiçbir şey kalmaz.
//! Kaynak yalnız okunur. 2026-11-16.

use postgres::Client;
use std::collections::{BTreeMap, BTreeSet};
use std::io::{Read, Write};
use std::path::PathBuf;

const KAYNAK_ONEKI: &str = "icra_models_";

#[derive(Debug, Clone)]
pub struct TabloSonucu {
    pub tablo: String,
    pub kaynak_satir: i64,
    pub aktarilan: i64,
    /// Kaynakta olup hedefte olmayan sütunlar (aktarılmaz)
    pub atlanan_sutunlar: Vec<String>,
}

fn hata(e: postgres::Error) -> String {
    match e.as_db_error() {
        Some(d) => format!("{} ({})", d.message(), d.detail().unwrap_or("")),
        None => e.to_string(),
    }
}

/// UyapMasa'nın kendi ayarlarıyla bağlantı dizesi: `%LOCALAPPDATA%\UyapIcra\dbpass.secret`,
/// 127.0.0.1, port UYAP_DB_PORT (varsayılan 5432), veritabanı uyap_icra.
pub fn uyapmasa_dsn() -> Result<String, String> {
    let la = std::env::var("LOCALAPPDATA").map_err(|_| "LOCALAPPDATA tanımlı değil".to_string())?;
    let yol = PathBuf::from(la).join("UyapIcra").join("dbpass.secret");
    let parola = std::fs::read_to_string(&yol)
        .map_err(|e| format!("UyapMasa parolası okunamadı ({}): {e}", yol.display()))?;
    let port = std::env::var("UYAP_DB_PORT").unwrap_or_else(|_| "5432".into());
    let ad = std::env::var("UYAP_DB_NAME").unwrap_or_else(|_| "uyap_icra".into());
    Ok(format!(
        "host=127.0.0.1 port={port} user=postgres password='{}' dbname={ad} connect_timeout=10 application_name=CokluUyap-aktar",
        parola.trim().replace('\\', "\\\\").replace('\'', "\\'")
    ))
}

fn tablolar(c: &mut Client) -> Result<BTreeSet<String>, String> {
    Ok(c
        .query(
            "SELECT table_name::text FROM information_schema.tables
             WHERE table_schema = 'public' AND table_type = 'BASE TABLE'",
            &[],
        )
        .map_err(hata)?
        .iter()
        .map(|r| r.get::<_, String>(0))
        .collect())
}

fn sutunlar(c: &mut Client, tablo: &str) -> Result<Vec<String>, String> {
    Ok(c
        .query(
            "SELECT column_name::text FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = $1 ORDER BY ordinal_position",
            &[&tablo],
        )
        .map_err(hata)?
        .iter()
        .map(|r| r.get::<_, String>(0))
        .collect())
}

fn say(c: &mut Client, tablo: &str) -> Result<i64, String> {
    Ok(c.query_one(&format!("SELECT count(*) FROM \"{tablo}\""), &[]).map_err(hata)?.get(0))
}

/// Kaynak tablo adı → hedef tablo adı. `icra_models_dosyataraf_vekiller` → `dosya_taraf_vekiller`:
/// alt çizgiler atılarak karşılaştırılır (sema_uret.py yalnız büyük harf sınırına alt çizgi koyar).
pub fn hedef_adi(kaynak: &str, hedefler: &BTreeSet<String>) -> Option<String> {
    let govde = kaynak.strip_prefix(KAYNAK_ONEKI)?;
    let anahtar = govde.replace('_', "");
    let mut adaylar = hedefler.iter().filter(|h| h.replace('_', "") == anahtar);
    let ilk = adaylar.next()?.clone();
    if adaylar.next().is_some() {
        return None; // belirsiz eşleşme: aktarma
    }
    Some(ilk)
}

/// Hedef tablolar arası yabancı anahtar bağımlılığına göre sıralama (önce ebeveyn).
fn sirala(c: &mut Client, secilen: &BTreeSet<String>) -> Result<Vec<String>, String> {
    let mut bagimli: BTreeMap<String, BTreeSet<String>> = secilen.iter().map(|t| (t.clone(), BTreeSet::new())).collect();
    for r in c
        .query(
            "SELECT cl.relname::text, ref.relname::text FROM pg_constraint k
             JOIN pg_class cl ON cl.oid = k.conrelid JOIN pg_class ref ON ref.oid = k.confrelid
             JOIN pg_namespace n ON n.oid = cl.relnamespace
             WHERE k.contype = 'f' AND n.nspname = 'public'",
            &[],
        )
        .map_err(hata)?
    {
        let (t, ebeveyn): (String, String) = (r.get(0), r.get(1));
        if t != ebeveyn && secilen.contains(&t) && secilen.contains(&ebeveyn) {
            bagimli.get_mut(&t).unwrap().insert(ebeveyn);
        }
    }
    let mut sira = Vec::new();
    let mut kalan = bagimli;
    while !kalan.is_empty() {
        let hazir: Vec<String> = kalan
            .iter()
            .filter(|(_, ebeveynler)| ebeveynler.iter().all(|e| !kalan.contains_key(e)))
            .map(|(t, _)| t.clone())
            .collect();
        if hazir.is_empty() {
            return Err(format!("tablolar arasında döngüsel bağ var: {:?}", kalan.keys().collect::<Vec<_>>()));
        }
        for t in hazir {
            kalan.remove(&t);
            sira.push(t);
        }
    }
    Ok(sira)
}

/// Aktarımı yapar. `deneme` = true ise her şey yazılır, sayılar karşılaştırılır, sonra işlem geri alınır.
pub fn aktar(kaynak: &mut Client, hedef: &mut Client, deneme: bool, log: &dyn Fn(&str)) -> Result<Vec<TabloSonucu>, String> {
    let k_tablolar = tablolar(kaynak)?;
    let h_tablolar = tablolar(hedef)?;
    let mut eslesme: BTreeMap<String, String> = BTreeMap::new(); // hedef → kaynak
    for kt in k_tablolar.iter().filter(|t| t.starts_with(KAYNAK_ONEKI)) {
        match hedef_adi(kt, &h_tablolar) {
            Some(ht) => {
                eslesme.insert(ht, kt.clone());
            }
            None => log(&format!("[AKTAR] Hedefte karşılığı yok, atlandı: {kt}")),
        }
    }
    if eslesme.is_empty() {
        return Err("kaynakta icra_models_ tablosu bulunamadı (doğru veritabanı mı?)".into());
    }

    // Hedef boş olmalı
    let mut dolu = Vec::new();
    for ht in eslesme.keys() {
        let n = say(hedef, ht)?;
        if n > 0 {
            dolu.push(format!("{ht} ({n})"));
        }
    }
    if !dolu.is_empty() {
        return Err(format!(
            "büro veritabanında icra kaydı zaten var, aktarım yapılmadı: {}",
            dolu.join(", ")
        ));
    }

    let secilen: BTreeSet<String> = eslesme.keys().cloned().collect();
    let sira = sirala(hedef, &secilen)?;
    let mut sonuc = Vec::new();
    let mut tx = hedef.transaction().map_err(hata)?;
    for ht in &sira {
        let kt = &eslesme[ht];
        let k_sut = sutunlar(kaynak, kt)?;
        let h_sut: BTreeSet<String> = {
            let mut c2 = tx
                .query(
                    "SELECT column_name::text FROM information_schema.columns WHERE table_schema = 'public' AND table_name = $1",
                    &[ht],
                )
                .map_err(hata)?;
            c2.drain(..).map(|r| r.get::<_, String>(0)).collect()
        };
        let ortak: Vec<String> = k_sut.iter().filter(|s| h_sut.contains(*s)).cloned().collect();
        let atlanan: Vec<String> = k_sut.iter().filter(|s| !h_sut.contains(*s)).cloned().collect();
        let kaynak_satir = say(kaynak, kt)?;
        if kaynak_satir > 0 {
            let liste = ortak.iter().map(|s| format!("\"{s}\"")).collect::<Vec<_>>().join(", ");
            // Metin biçimli COPY: tür adları aynı, ama metin biçimi küçük tür farklarına (varchar uzunluğu vb.) dayanıklı.
            let mut okur = kaynak
                .copy_out(&format!("COPY (SELECT {liste} FROM \"{kt}\" ORDER BY 1) TO STDOUT"))
                .map_err(hata)?;
            let mut yazar = tx.copy_in(&format!("COPY \"{ht}\" ({liste}) FROM STDIN")).map_err(hata)?;
            let mut tampon = vec![0u8; 1 << 16];
            loop {
                let n = okur.read(&mut tampon).map_err(|e| format!("{kt} okunamadı: {e}"))?;
                if n == 0 {
                    break;
                }
                yazar.write_all(&tampon[..n]).map_err(|e| format!("{ht} yazılamadı: {e}"))?;
            }
            drop(okur);
            yazar.finish().map_err(|e| format!("{ht}: {}", hata(e)))?;
        }
        let aktarilan: i64 = tx.query_one(&format!("SELECT count(*) FROM \"{ht}\""), &[]).map_err(hata)?.get(0);
        log(&format!(
            "[AKTAR] {ht}: {aktarilan}/{kaynak_satir}{}",
            if atlanan.is_empty() { String::new() } else { format!(" (aktarılmayan sütun: {})", atlanan.join(", ")) }
        ));
        if aktarilan != kaynak_satir {
            return Err(format!("{ht}: sayı tutmadı ({aktarilan} ≠ {kaynak_satir}); işlem geri alındı"));
        }
        sonuc.push(TabloSonucu { tablo: ht.clone(), kaynak_satir, aktarilan, atlanan_sutunlar: atlanan });
    }

    // Kimlik dizileri: yeni kayıtlar aktarılan en büyük id'nin üstünden devam etsin.
    // setval işlemle geri alınmaz: denemede atlanır.
    let diziler = if deneme { Vec::new() } else {
        tx.query(
            "SELECT c.table_name::text, c.column_name::text, pg_get_serial_sequence(format('%I', c.table_name), c.column_name)
             FROM information_schema.columns c
             WHERE c.table_schema = 'public' AND c.table_name = ANY($1)
               AND pg_get_serial_sequence(format('%I', c.table_name), c.column_name) IS NOT NULL",
            &[&sira],
        )
        .map_err(hata)?
    };
    for r in diziler {
        let (t, s, dizi): (String, String, String) = (r.get(0), r.get(1), r.get(2));
        tx.execute(
            &format!("SELECT setval($1::text::regclass, GREATEST(COALESCE((SELECT max(\"{s}\") FROM \"{t}\"), 0), 1), (SELECT count(*) > 0 FROM \"{t}\"))"),
            &[&dizi],
        )
        .map_err(|e| format!("{t}.{s} dizisi ayarlanamadı: {}", hata(e)))?;
    }

    if deneme {
        tx.rollback().map_err(hata)?;
        log("[AKTAR] Deneme: sayılar tuttu, işlem GERİ ALINDI (büro veritabanı değişmedi).");
    } else {
        tx.commit().map_err(hata)?;
        log("[AKTAR] Aktarım tamamlandı.");
    }
    Ok(sonuc)
}

/// Komut satırı: `uyap_app --uyapmasa-aktar [--deneme]`. Büro veritabanı yoksa başlatır/çalışana bağlanır.
pub fn komut(argv: &[String]) -> i32 {
    let deneme = argv.iter().any(|a| a == "--deneme");
    let log = |m: &str| println!("{m}");
    let gunluk: crate::veritabani::Gunluk = std::sync::Arc::new(|m: &str| println!("{m}"));
    if let Err(e) = crate::veritabani::baslat(gunluk) {
        eprintln!("Büro veritabanı açılamadı: {e}");
        return 1;
    }
    let dsn = match uyapmasa_dsn() {
        Ok(d) => d,
        Err(e) => {
            eprintln!("{e}");
            return 1;
        }
    };
    let mut kaynak = match Client::connect(&dsn, postgres::NoTls) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("UyapMasa veritabanına bağlanılamadı (açık mı? python db_baslat.py): {}", hata(e));
            return 1;
        }
    };
    // Kaynak salt okunur oturum: yanlışlıkla yazma olmasın.
    if let Err(e) = kaynak.batch_execute("SET default_transaction_read_only = on") {
        eprintln!("{}", hata(e));
        return 1;
    }
    let Some(hedef_dsn) = crate::veritabani::dsn() else {
        eprintln!("büro veritabanı hazır değil");
        return 1;
    };
    let mut hedef = match Client::connect(&hedef_dsn, postgres::NoTls) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("büro veritabanına bağlanılamadı: {}", hata(e));
            return 1;
        }
    };
    let bas = std::time::Instant::now();
    match aktar(&mut kaynak, &mut hedef, deneme, &log) {
        Ok(s) => {
            let toplam: i64 = s.iter().map(|t| t.aktarilan).sum();
            println!("Toplam {toplam} satır, {} tablo, {:.1} sn", s.len(), bas.elapsed().as_secs_f64());
            0
        }
        Err(e) => {
            eprintln!("AKTARIM YAPILMADI: {e}");
            2
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tablo_adi_eslesmesi() {
        let h: BTreeSet<String> = ["dosya", "dosya_taraf", "dosya_taraf_vekiller", "icra_takip_detay", "ptt_hareket", "taraf"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        assert_eq!(hedef_adi("icra_models_dosyataraf", &h).as_deref(), Some("dosya_taraf"));
        assert_eq!(hedef_adi("icra_models_dosyataraf_vekiller", &h).as_deref(), Some("dosya_taraf_vekiller"));
        assert_eq!(hedef_adi("icra_models_icratakipdetay", &h).as_deref(), Some("icra_takip_detay"));
        assert_eq!(hedef_adi("icra_models_dosya", &h).as_deref(), Some("dosya"));
        assert_eq!(hedef_adi("icra_models_yok", &h), None);
        assert_eq!(hedef_adi("django_migrations", &h), None);
    }
}

/// Komut satırı: `uyap_app --icra-durum-guncelle [--deneme]`. Aktarılan veride borçluların kesinleşme /
/// tebliğ durumu ve takip aşamasını hesaplar (`icra_durum::durumlari_guncelle`); önce ve sonra dağılımı yazar.
/// `--deneme`: hesaplar, dağılımı gösterir, geri alır.
pub fn durum_komut(argv: &[String]) -> i32 {
    let deneme = argv.iter().any(|a| a == "--deneme");
    let gunluk: crate::veritabani::Gunluk = std::sync::Arc::new(|m: &str| println!("{m}"));
    if let Err(e) = crate::veritabani::baslat(gunluk) {
        eprintln!("Büro veritabanı açılamadı: {e}");
        return 1;
    }
    let Some(d) = crate::veritabani::dsn() else {
        eprintln!("büro veritabanı hazır değil");
        return 1;
    };
    let mut c = match Client::connect(&d, postgres::NoTls) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("büro veritabanına bağlanılamadı: {}", hata(e));
            return 1;
        }
    };
    let dagilim = |c: &mut Client, baslik: &str| {
        println!("── {baslik}");
        for sutun in ["kesinlesme_durumu", "tebligat_durumu", "takip_asamasi"] {
            let q = format!(
                "SELECT coalesce(nullif({sutun}, ''), '(boş)'), count(*) FROM dosya_taraf GROUP BY 1 ORDER BY 2 DESC LIMIT 12"
            );
            match c.query(&q, &[]) {
                Ok(r) => {
                    let s: Vec<String> = r.iter().map(|x| format!("{}={}", x.get::<_, String>(0), x.get::<_, i64>(1))).collect();
                    println!("  {sutun}: {}", s.join(", "));
                }
                Err(e) => println!("  {sutun}: okunamadı ({})", hata(e)),
            }
        }
    };
    dagilim(&mut c, "ÖNCE");
    let bugun = chrono::Local::now().date_naive();
    let bas = std::time::Instant::now();
    let mut tx = match c.transaction() {
        Ok(t) => t,
        Err(e) => {
            eprintln!("{}", hata(e));
            return 1;
        }
    };
    let log = |m: &str| println!("{m}");
    let sonuc = crate::icra_durum::durumlari_guncelle(&mut tx, None, bugun, &log);
    match sonuc {
        Ok(s) => println!("Hesaplandı: {} borçlu satırı değişti, {} olay yazıldı ({:.1} sn)", s.guncellenen, s.olay, bas.elapsed().as_secs_f64()),
        Err(e) => {
            eprintln!("HESAP BAŞARISIZ: {e}");
            return 2;
        }
    }
    {
        // Dağılımı işlem içinden göster (geri alınacaksa da görünsün)
        println!("── SONRA");
        for sutun in ["kesinlesme_durumu", "tebligat_durumu", "takip_asamasi"] {
            let q = format!(
                "SELECT coalesce(nullif({sutun}, ''), '(boş)'), count(*) FROM dosya_taraf GROUP BY 1 ORDER BY 2 DESC LIMIT 12"
            );
            if let Ok(r) = tx.query(&q, &[]) {
                let s: Vec<String> = r.iter().map(|x| format!("{}={}", x.get::<_, String>(0), x.get::<_, i64>(1))).collect();
                println!("  {sutun}: {}", s.join(", "));
            }
        }
    }
    let r = if deneme { tx.rollback() } else { tx.commit() };
    if let Err(e) = r {
        eprintln!("{}", hata(e));
        return 1;
    }
    println!("{}", if deneme { "Deneme: GERİ ALINDI." } else { "Kaydedildi." });
    0
}
