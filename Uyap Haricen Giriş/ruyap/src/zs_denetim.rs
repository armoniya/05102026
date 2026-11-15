//! /zs denetim dökümü (zs_denetim.rs, 1:1 port of zs_denetim.py)
//!
//! Geliştirici aracı: arayüzde ve API'de görünmeyen zs_audit tablosunu okur.

use std::io::Write;
use std::path::Path;

use chrono::{FixedOffset, TimeZone};
use serde_json::Value;

use crate::zs_store::{now_ts, ZsStore};

const CSV_COLS: &[&str] = &[
    "id",
    "at",
    "username",
    "role",
    "ip",
    "action",
    "target_id",
    "ok",
    "status",
    "error",
    "ms",
    "method",
    "path",
    "data",
    "result",
    "before",
    "user_agent",
];

fn ts_tr(ts: i64) -> String {
    if ts <= 0 {
        return "-".to_string();
    }
    let tr = FixedOffset::east_opt(3 * 3600).unwrap();
    tr.timestamp_opt(ts, 0)
        .single()
        .map(|dt| dt.format("%Y-%m-%d %H:%M:%S").to_string())
        .unwrap_or_else(|| "-".to_string())
}

fn csv_escape(v: &str) -> String {
    if v.contains(';') || v.contains('"') || v.contains('\n') || v.contains('\r') {
        format!("\"{}\"", v.replace('"', "\"\""))
    } else {
        v.to_string()
    }
}

fn export_rows(path: &str, rows: &[Value]) -> Result<(), String> {
    let p = Path::new(path);
    if p.extension().and_then(|e| e.to_str()).map(|s| s.eq_ignore_ascii_case("csv")).unwrap_or(false) {
        let mut f = std::fs::File::create(p).map_err(|e| e.to_string())?;
        // UTF-8-SIG BOM for Excel compatibility
        f.write_all(&[0xEF, 0xBB, 0xBF]).map_err(|e| e.to_string())?;
        f.write_all(format!("{}\n", CSV_COLS.join(";")).as_bytes())
            .map_err(|e| e.to_string())?;
        for r in rows {
            let mut vals = Vec::with_capacity(CSV_COLS.len());
            for &col in CSV_COLS {
                let s = if col == "at" {
                    ts_tr(r["at"].as_i64().unwrap_or(0))
                } else {
                    match &r[col] {
                        Value::Null => String::new(),
                        Value::String(st) => st.clone(),
                        Value::Number(n) => n.to_string(),
                        Value::Bool(b) => b.to_string(),
                        other => other.to_string(),
                    }
                };
                vals.push(csv_escape(&s));
            }
            f.write_all(format!("{}\n", vals.join(";")).as_bytes())
                .map_err(|e| e.to_string())?;
        }
    } else {
        let mut f = std::fs::File::create(p).map_err(|e| e.to_string())?;
        for r in rows {
            let line = serde_json::to_string(r).unwrap_or_default();
            f.write_all(format!("{}\n", line).as_bytes())
                .map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

pub fn main(argv: Option<Vec<String>>) -> i32 {
    let args: Vec<String> = argv.unwrap_or_else(|| std::env::args().skip(1).collect());
    let mut son: i64 = 50;
    let mut gun: Option<f64> = None;
    let mut kisi: Option<String> = None;
    let mut islem: Option<String> = None;
    let mut hatalar = false;
    let mut ayrinti = false;
    let mut dosya: Option<String> = None;

    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--son" => {
                i += 1;
                if let Some(v) = args.get(i) {
                    son = v.parse().unwrap_or(50);
                }
            }
            "--gun" => {
                i += 1;
                if let Some(v) = args.get(i) {
                    gun = v.parse().ok();
                }
            }
            "--kisi" => {
                i += 1;
                kisi = args.get(i).cloned();
            }
            "--islem" => {
                i += 1;
                islem = args.get(i).cloned();
            }
            "--hatalar" => {
                hatalar = true;
            }
            "--ayrinti" => {
                ayrinti = true;
            }
            "--dosya" => {
                i += 1;
                dosya = args.get(i).cloned();
            }
            "-h" | "--help" => {
                println!(
                    "Kullanım: zs_denetim [--son N] [--gun G] [--kisi K] [--islem A] [--hatalar] [--ayrinti] [--dosya YOL]"
                );
                return 0;
            }
            _ => {}
        }
        i += 1;
    }

    let store = match ZsStore::new(None, None) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("Veritabanı hatası: {}", e);
            return 1;
        }
    };
    let since = gun.map(|g| now_ts() - (g * 86400.0) as i64);
    let rows = match store.audit_rows(
        son,
        since,
        kisi.as_deref(),
        islem.as_deref(),
        hatalar,
    ) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("Sorgu hatası: {}", e);
            return 1;
        }
    };

    if let Some(ref dpath) = dosya {
        if let Err(e) = export_rows(dpath, &rows) {
            eprintln!("Yazma hatası: {}", e);
            return 1;
        }
        println!("{} satır → {}", rows.len(), dpath);
        return 0;
    }

    if rows.is_empty() {
        println!("Kayıt yok.");
        return 0;
    }

    for r in rows.iter().rev() {
        let durum = if r["ok"].as_i64().unwrap_or(0) != 0 {
            format!("OK  {}", r["status"].as_i64().unwrap_or(0))
        } else {
            format!("ERR {}", r["status"].as_i64().unwrap_or(0))
        };
        let target = match r["target_id"].as_i64() {
            Some(tid) => format!("#{}", tid),
            None => "-".to_string(),
        };
        let kisi_str = r["username"].as_str().unwrap_or("-");
        let ms = match r["ms"].as_i64() {
            Some(m) => format!("{}ms", m),
            None => "-".to_string(),
        };
        let at_s = ts_tr(r["at"].as_i64().unwrap_or(0));
        let action_s = r["action"].as_str().unwrap_or("-");
        let ip_s = r["ip"].as_str().unwrap_or("-");
        println!(
            "{} | {} | {:<12} | {:<22} | {:>5} | {:<15} | {}",
            at_s, durum, kisi_str, action_s, target, ip_s, ms
        );
        if let Some(err) = r["error"].as_str().filter(|s| !s.is_empty()) {
            println!("  hata: {}", err);
        }
        if ayrinti {
            for k in ["data", "result", "before"] {
                if let Some(v) = r[k].as_str().filter(|s| !s.is_empty()) {
                    println!("  {}: {}", k, v);
                }
            }
        }
    }
    0
}
