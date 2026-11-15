//! dist/CokluUyap içeriğini build/payload.zip'e sıkıştırır (kurucuya gömülür).
//! Zip KÖKÜ doğrudan uygulama dosyalarıdır (CokluUyap.exe, _internal/…) — kurucu
//! hedef klasöre açar açmaz çalışır durumda olur.

use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use zip::write::SimpleFileOptions;
use zip::CompressionMethod;
use zip::ZipWriter;

pub fn here_dir() -> PathBuf {
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            let candidate = parent.join("dist").join("CokluUyap");
            if candidate.is_dir() {
                return parent.to_path_buf();
            }
        }
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

pub fn src_dir(here: &Path) -> PathBuf {
    here.join("dist").join("CokluUyap")
}

pub fn out_file(here: &Path) -> PathBuf {
    here.join("build").join("payload.zip")
}

fn walk_files(dir: &Path, out: &mut Vec<PathBuf>) -> std::io::Result<()> {
    let mut entries: Vec<_> = fs::read_dir(dir)?.filter_map(|e| e.ok()).collect();
    entries.sort_by_key(|e| e.file_name());
    let mut subdirs = Vec::new();
    for entry in entries {
        let path = entry.path();
        if let Ok(ft) = entry.file_type() {
            if ft.is_dir() {
                subdirs.push(path);
            } else if ft.is_file() {
                out.push(path);
            }
        }
    }
    for subdir in subdirs {
        walk_files(&subdir, out)?;
    }
    Ok(())
}

pub fn main() {
    let here = here_dir();
    let src = src_dir(&here);
    let out = out_file(&here);

    if !src.is_dir() {
        eprintln!("dist/CokluUyap yok — önce cokluuyap_app.spec derleyin.");
        std::process::exit(1);
    }

    if let Some(parent) = out.parent() {
        let _ = fs::create_dir_all(parent);
    }

    let file = File::create(&out).unwrap_or_else(|e| {
        eprintln!("payload.zip oluşturulamadı: {e}");
        std::process::exit(1);
    });
    let mut zip = ZipWriter::new(file);
    let options = SimpleFileOptions::default()
        .compression_method(CompressionMethod::Deflated)
        .compression_level(Some(6));

    let mut files = Vec::new();
    if let Err(e) = walk_files(&src, &mut files) {
        eprintln!("Dosyalar okunamadı: {e}");
        std::process::exit(1);
    }

    let mut n: usize = 0;
    let mut buf = Vec::new();
    for tam in files {
        let rel = tam.strip_prefix(&src).unwrap_or(&tam);
        let arcname = rel
            .components()
            .map(|c| c.as_os_str().to_string_lossy().into_owned())
            .collect::<Vec<_>>()
            .join("/");
        zip.start_file(arcname, options).unwrap_or_else(|e| {
            eprintln!("Zip kaydı başlatılamadı: {e}");
            std::process::exit(1);
        });
        buf.clear();
        File::open(&tam)
            .and_then(|mut f| f.read_to_end(&mut buf))
            .unwrap_or_else(|e| {
                eprintln!("Dosya okunamadı ({}): {e}", tam.display());
                std::process::exit(1);
            });
        zip.write_all(&buf).unwrap_or_else(|e| {
            eprintln!("Zip yazılamadı: {e}");
            std::process::exit(1);
        });
        n += 1;
    }

    zip.finish().unwrap_or_else(|e| {
        eprintln!("Zip kapatılamadı: {e}");
        std::process::exit(1);
    });

    let size_mb = fs::metadata(&out).map(|m| m.len() as f64 / 1e6).unwrap_or(0.0);
    println!(
        "payload.zip hazır: {} dosya, {:.1} MB -> {}",
        n,
        size_mb,
        out.display()
    );
}
