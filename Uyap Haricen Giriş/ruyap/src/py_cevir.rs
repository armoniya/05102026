//! Python/PyMuPDF ile evrak dönüştürme ve birleştirme (eski Python sürümündeki kod; kullanıcı isteği 2026-11-16).
//! Betikler exe'ye gömülüdür; ilk kullanımda %TEMP%\CokluUyap\py klasörüne yazılır ve bu bilgisayardaki Python ile
//! çalıştırılır. Python ya da PyMuPDF yoksa / hata verirse çağıran Rust dönüştürücüsüne düşer. Belge dışarı çıkmaz.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use once_cell::sync::Lazy;

const BETIK: &str = include_str!("../python/evrak_cevir.py");
const UDF_PDF: &str = include_str!("../python/udf_pdf.py");

fn klasor() -> PathBuf {
    std::env::temp_dir().join("CokluUyap").join("py")
}

fn betikleri_yaz() -> Option<PathBuf> {
    let k = klasor();
    std::fs::create_dir_all(&k).ok()?;
    for (ad, icerik) in [("evrak_cevir.py", BETIK), ("udf_pdf.py", UDF_PDF)] {
        let yol = k.join(ad);
        if std::fs::read_to_string(&yol).ok().as_deref() != Some(icerik) {
            std::fs::write(&yol, icerik).ok()?;
        }
    }
    Some(k.join("evrak_cevir.py"))
}

fn komut(python: &str) -> Command {
    let mut c = Command::new(python);
    c.arg("-X").arg("utf8").stdin(Stdio::null()).stdout(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        c.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: konsol penceresi açılmasın
    }
    c
}

/// Çalışan Python (PyMuPDF yüklü) ve betik yolu; bir kez aranır.
static PYTHON: Lazy<Option<(String, PathBuf)>> = Lazy::new(|| {
    let betik = betikleri_yaz()?;
    let mut adaylar: Vec<String> = Vec::new();
    if let Ok(p) = std::env::var("UYAP_PYTHON") {
        adaylar.push(p);
    }
    adaylar.extend(["python", "py", "python3"].iter().map(|s| s.to_string()));
    for p in adaylar {
        let r = calistir(&p, &betik, &["dene".into()], Duration::from_secs(30));
        if r.is_ok() {
            println!("[evrak] Python dönüştürücü: {}", p);
            return Some((p, betik));
        }
    }
    println!("[evrak] Python/PyMuPDF bulunamadı; Rust dönüştürücü kullanılacak.");
    None
});

fn calistir(python: &str, betik: &Path, args: &[std::ffi::OsString], sinir: Duration) -> Result<(), String> {
    let mut c = komut(python);
    // hata çıktısı dosyaya (boru dolarsa süreç kilitlenirdi)
    let hata_yol = gecici("hata.txt");
    let hata_dosya = std::fs::File::create(&hata_yol).map_err(|e| e.to_string())?;
    c.stderr(Stdio::from(hata_dosya)).arg(betik).args(args);
    let sonuc = bekle(c, sinir);
    let hata = std::fs::read_to_string(&hata_yol).unwrap_or_default();
    let _ = std::fs::remove_file(&hata_yol);
    sonuc.map_err(|e| if e.is_empty() { hata.trim().lines().last().unwrap_or("Python hatası").to_string() } else { e })
}

fn bekle(mut c: Command, sinir: Duration) -> Result<(), String> {
    let mut ch = c.spawn().map_err(|e| e.to_string())?;
    let bas = Instant::now();
    loop {
        match ch.try_wait() {
            Ok(Some(st)) => {
                if st.success() {
                    return Ok(());
                }
                return Err(String::new());
            }
            Ok(None) if bas.elapsed() > sinir => {
                let _ = ch.kill();
                let _ = ch.wait();
                return Err("Python süre sınırını aştı".into());
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(30)),
            Err(e) => return Err(e.to_string()),
        }
    }
}

fn gecici(ad: &str) -> PathBuf {
    let n = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
    klasor().join(format!("{}_{}_{}", std::process::id(), n, ad))
}

/// Python ile tek belgeyi PDF'e çevirir (tiff | jpeg | png | udf | html). Python yoksa None.
pub fn cevir(tur: &str, bayt: &[u8]) -> Option<Result<Vec<u8>, String>> {
    let (py, betik) = PYTHON.as_ref()?;
    let girdi = gecici("girdi");
    let cikti = gecici("cikti.pdf");
    let sonuc = (|| {
        std::fs::write(&girdi, bayt).map_err(|e| e.to_string())?;
        calistir(py, betik, &["cevir".into(), tur.into(), girdi.clone().into(), cikti.clone().into()], Duration::from_secs(180))?;
        std::fs::read(&cikti).map_err(|e| e.to_string())
    })();
    let _ = std::fs::remove_file(&girdi);
    let _ = std::fs::remove_file(&cikti);
    Some(sonuc)
}

/// Python ile PDF'leri birleştirir. Python yoksa None.
pub fn birlestir(yollar: &[PathBuf], hedef: &Path) -> Option<Result<(), String>> {
    let (py, betik) = PYTHON.as_ref()?;
    let liste = gecici("liste.txt");
    let metin: String = yollar.iter().map(|y| format!("{}\n", y.display())).collect();
    let sonuc = (|| {
        std::fs::write(&liste, metin).map_err(|e| e.to_string())?;
        calistir(py, betik, &["birlestir".into(), hedef.into(), liste.clone().into()], Duration::from_secs(1800))
    })();
    let _ = std::fs::remove_file(&liste);
    Some(sonuc)
}
