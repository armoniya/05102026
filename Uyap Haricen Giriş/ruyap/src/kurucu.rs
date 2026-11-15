//! Çoklu UYAP Kurucu (CokluUyapKur.exe) — tek dosyalık kurulum programı
//! ====================================================================
//! kurucu.py'nin 1:1 Rust karşılığı.

use clap::Parser;
use eframe::egui;
use std::fs::{self, File};
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use zip::ZipArchive;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

pub const APP_ADI: &str = "Çoklu UYAP";
pub const EXE_ADI: &str = "CokluUyap.exe";
pub const KLASOR: &str = "CokluUyap";

pub fn payload_yolu() -> PathBuf {
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let p = dir.join("payload.zip");
            if p.exists() {
                return p;
            }
        }
    }
    std::env::current_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("payload.zip")
}

pub fn varsayilan_hedef() -> PathBuf {
    let root = std::env::var("LOCALAPPDATA")
        .ok()
        .filter(|s| !s.is_empty())
        .or_else(|| std::env::var("USERPROFILE").ok())
        .or_else(|| std::env::var("HOME").ok())
        .unwrap_or_else(|| ".".to_string());
    PathBuf::from(root).join(KLASOR)
}

pub fn kisayol(lnk_yolu: &Path, hedef_exe: &Path) -> Result<(), String> {
    let lnk = lnk_yolu.to_string_lossy().replace('\'', "''");
    let exe = hedef_exe.to_string_lossy().replace('\'', "''");
    let wd = hedef_exe
        .parent()
        .map(|p| p.to_string_lossy().replace('\'', "''"))
        .unwrap_or_default();
    let ps = format!(
        "$w = New-Object -ComObject WScript.Shell; \
         $s = $w.CreateShortcut('{lnk}'); \
         $s.TargetPath = '{exe}'; $s.WorkingDirectory = '{wd}'; $s.Save()"
    );
    let mut cmd = Command::new("powershell");
    cmd.args(["-NoProfile", "-NonInteractive", "-Command", &ps]);
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    let status = cmd
        .status()
        .map_err(|e| format!("PowerShell başlatılamadı: {e}"))?;
    if !status.success() {
        return Err(format!("Kısayol komutu başarısız: {status}"));
    }
    Ok(())
}

pub fn kur<F>(hedef: &Path, mut durum: F, kisayollar: bool) -> Result<PathBuf, String>
where
    F: FnMut(&str),
{
    let zpath = payload_yolu();
    if !zpath.exists() {
        return Err(
            "Gömülü paket (payload.zip) bulunamadı — kurucu bozuk derlenmiş.".to_string(),
        );
    }

    durum("Dosyalar açılıyor…");
    let yeni = PathBuf::from(format!("{}.yeni", hedef.to_string_lossy()));
    if yeni.exists() {
        let _ = fs::remove_dir_all(&yeni);
    }
    fs::create_dir_all(&yeni).map_err(|e| format!("Klasör oluşturulamadı: {e}"))?;

    let file = File::open(&zpath).map_err(|e| format!("payload.zip açılamadı: {e}"))?;
    let mut archive = ZipArchive::new(file).map_err(|e| format!("payload.zip geçersiz: {e}"))?;
    for i in 0..archive.len() {
        let mut entry = archive
            .by_index(i)
            .map_err(|e| format!("Zip girişi okunamadı: {e}"))?;
        let outpath = match entry.enclosed_name() {
            Some(p) => yeni.join(p),
            None => continue,
        };
        if entry.name().ends_with('/') || entry.is_dir() {
            let _ = fs::create_dir_all(&outpath);
        } else {
            if let Some(p) = outpath.parent() {
                let _ = fs::create_dir_all(p);
            }
            let mut outfile =
                File::create(&outpath).map_err(|e| format!("Dosya yazılamadı: {e}"))?;
            io::copy(&mut entry, &mut outfile)
                .map_err(|e| format!("Dosya kopyalanamadı: {e}"))?;
        }
    }

    durum("Eski sürüm değiştiriliyor…");
    if hedef.exists() {
        let mut tk = Command::new("taskkill");
        tk.args(["/IM", EXE_ADI, "/F"])
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        #[cfg(windows)]
        tk.creation_flags(CREATE_NO_WINDOW);
        let _ = tk.status();
        let _ = fs::remove_dir_all(hedef);
    }
    fs::rename(&yeni, hedef).map_err(|e| format!("Kurulum klasörü taşınamadı: {e}"))?;

    let mut exe = hedef.join(EXE_ADI);
    if !exe.exists() {
        let ic = hedef.join(KLASOR).join(EXE_ADI);
        if ic.exists() {
            exe = ic;
        } else {
            return Err(format!("{EXE_ADI} paket içinde bulunamadı."));
        }
    }

    if kisayollar {
        durum("Kısayollar oluşturuluyor…");
        let home = std::env::var("USERPROFILE")
            .or_else(|_| std::env::var("HOME"))
            .unwrap_or_default();
        if !home.is_empty() {
            let masaustu = PathBuf::from(&home).join("Desktop");
            let _ = kisayol(&masaustu.join(format!("{APP_ADI}.lnk")), &exe);
        }
        if let Ok(appdata) = std::env::var("APPDATA") {
            let baslat_menu = PathBuf::from(appdata)
                .join("Microsoft")
                .join("Windows")
                .join("Start Menu")
                .join("Programs");
            let _ = kisayol(&baslat_menu.join(format!("{APP_ADI}.lnk")), &exe);
        }
    }

    Ok(exe)
}

fn spawn_installed_exe(exe: &Path) {
    let mut cmd = Command::new(exe);
    if let Some(dir) = exe.parent() {
        cmd.current_dir(dir);
    }
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    let _ = cmd.spawn();
}

#[cfg(windows)]
fn msgbox_error(title: &str, text: &str) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK};
    let t: Vec<u16> = title.encode_utf16().chain(std::iter::once(0)).collect();
    let m: Vec<u16> = text.encode_utf16().chain(std::iter::once(0)).collect();
    unsafe {
        MessageBoxW(std::ptr::null_mut(), m.as_ptr(), t.as_ptr(), MB_OK | MB_ICONERROR);
    }
}

#[cfg(not(windows))]
fn msgbox_error(title: &str, text: &str) {
    eprintln!("[{title}] {text}");
}

struct KurulumState {
    durum: String,
    sonuc_exe: Option<PathBuf>,
    hata: Option<String>,
    bitti: bool,
}

struct KurucuGui {
    state: Arc<Mutex<KurulumState>>,
    baslat: bool,
    started_time: std::time::Instant,
}

impl eframe::App for KurucuGui {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        let (durum, bitti, hata, exe) = {
            let st = self.state.lock().unwrap();
            (
                st.durum.clone(),
                st.bitti,
                st.hata.clone(),
                st.sonuc_exe.clone(),
            )
        };

        if bitti {
            if let Some(err) = hata {
                msgbox_error(APP_ADI, &format!("Kurulum başarısız:\n{err}"));
                std::process::exit(1);
            }
            if self.baslat {
                if let Some(exe_path) = exe {
                    spawn_installed_exe(&exe_path);
                }
            }
            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
            return;
        }

        let bg = egui::Color32::from_rgb(0xF6, 0xF7, 0xF4);
        let fg_title = egui::Color32::from_rgb(0x3B, 0x4A, 0x3F);
        let fg_sub = egui::Color32::from_rgb(0x5C, 0x6B, 0x60);

        egui::CentralPanel::default()
            .frame(egui::Frame::none().fill(bg))
            .show(ctx, |ui| {
                ui.vertical_centered(|ui| {
                    ui.add_space(22.0);
                    ui.label(
                        egui::RichText::new(format!("{APP_ADI} kuruluyor…"))
                            .size(17.3)
                            .strong()
                            .color(fg_title),
                    );
                    ui.add_space(6.0);
                    ui.label(
                        egui::RichText::new(&durum)
                            .size(13.3)
                            .color(fg_sub),
                    );
                    ui.add_space(14.0);

                    // Indeterminate progress bar (width=340)
                    let bar_w = 340.0;
                    let bar_h = 14.0;
                    let (rect, _) = ui.allocate_exact_size(
                        egui::Vec2::new(bar_w, bar_h),
                        egui::Sense::hover(),
                    );
                    let painter = ui.painter();
                    painter.rect_filled(
                        rect,
                        3.0,
                        egui::Color32::from_rgb(0xE0, 0xE4, 0xDF),
                    );
                    let t = self.started_time.elapsed().as_secs_f32();
                    let pos = (t * 0.7) % 1.0;
                    let seg_w = bar_w * 0.28;
                    let x1 = rect.min.x + (bar_w - seg_w) * pos;
                    let seg_rect = egui::Rect::from_min_size(
                        egui::Pos2::new(x1, rect.min.y),
                        egui::Vec2::new(seg_w, bar_h),
                    );
                    painter.rect_filled(
                        seg_rect,
                        3.0,
                        crate::uyap_theme::C::SAGE(),
                    );
                });
            });

        ctx.request_repaint_after(std::time::Duration::from_millis(50));
    }
}

pub fn run_gui(hedef: PathBuf, baslat: bool) {
    let state = Arc::new(Mutex::new(KurulumState {
        durum: "Hazırlanıyor…".to_string(),
        sonuc_exe: None,
        hata: None,
        bitti: false,
    }));

    let state_bg = Arc::clone(&state);
    std::thread::spawn(move || {
        let res = kur(
            &hedef,
            |s| {
                if let Ok(mut st) = state_bg.lock() {
                    st.durum = s.to_string();
                }
            },
            true,
        );
        if let Ok(mut st) = state_bg.lock() {
            match res {
                Ok(exe) => {
                    st.durum = "Kurulum tamamlandı.".to_string();
                    st.sonuc_exe = Some(exe);
                }
                Err(e) => {
                    st.hata = Some(e);
                }
            }
            st.bitti = true;
        }
    });

    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_title(format!("{APP_ADI} Kurulumu"))
            .with_inner_size([420.0, 170.0])
            .with_resizable(false),
        ..Default::default()
    };

    let _ = eframe::run_native(
        &format!("{APP_ADI} Kurulumu"),
        options,
        Box::new(move |cc| {
            crate::uyap_theme::configure_egui_fonts(&cc.egui_ctx);
            Ok(Box::new(KurucuGui {
                state,
                baslat,
                started_time: std::time::Instant::now(),
            }))
        }),
    );
}

#[derive(Parser, Debug)]
#[command(about = "Çoklu UYAP kurucu")]
struct Args {
    /// Kurulum klasörü (varsayılan: %LOCALAPPDATA%\CokluUyap)
    #[arg(long)]
    hedef: Option<PathBuf>,

    /// Pencere göstermeden kur
    #[arg(long)]
    sessiz: bool,

    /// Kurulum sonrası uygulamayı açma
    #[arg(long)]
    baslatma: bool,

    /// Masaüstü/Başlat kısayolu kurma
    #[arg(long)]
    kisayolsuz: bool,
}

pub fn main() {
    let args = Args::parse();
    let hedef = args.hedef.unwrap_or_else(varsayilan_hedef);

    if args.sessiz {
        match kur(&hedef, |s| println!("{s}"), !args.kisayolsuz) {
            Ok(exe) => {
                println!("Kuruldu: {}", exe.display());
                if !args.baslatma {
                    spawn_installed_exe(&exe);
                }
            }
            Err(e) => {
                eprintln!("Hata: {e}");
                std::process::exit(1);
            }
        }
    } else {
        run_gui(hedef, !args.baslatma);
    }
}
