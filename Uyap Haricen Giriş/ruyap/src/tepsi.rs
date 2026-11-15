//! Windows bildirim alanı (tepsi) simgesi (2026-11-14, kullanıcı isteği: "programı öncelikli olarak Windows'ta
//! sağ altta simgeler olarak olan yerde çalıştıralım; kullanıcı isterse pencere olarak açabilsin").
//!
//! Gizleme `SW_HIDE` ile DEĞİL: gizli pencereye Windows çizim iletisi göndermez, eframe `update`'i çağırmaz;
//! otomatik giriş, paylaşım takibi ve durum güncellemesi o döngüde çalıştığı için durur. Bunun yerine pencere
//! ekran dışına alınır ve görev çubuğu düğmesi kaldırılır (WS_EX_TOOLWINDOW) — döngü çalışmaya devam eder.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use once_cell::sync::Lazy;
use tray_icon::{Icon, MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};

const PENCERE_BASLIGI: &str = "Çoklu UYAP - Kontrol Ekranı";
const GIZLI_X: i32 = -32000;

static GIZLI: AtomicBool = AtomicBool::new(false);
static ONCEKI_KONUM: Lazy<Mutex<Option<(i32, i32)>>> = Lazy::new(|| Mutex::new(None));
/// Sağ tıkla istenen temalı panelin konumu (fiziksel piksel); arayüz döngüsü alıp paneli çizer.
static PANEL_ISTEGI: Lazy<Mutex<Option<(f32, f32)>>> = Lazy::new(|| Mutex::new(None));
/// "Günlüğü gönder" için giriş yapmış kullanıcının bilgileri: (api_base, kullanıcı, ofis, parola).
static KIMLIK: Lazy<Mutex<Option<(String, String, String, String)>>> = Lazy::new(|| Mutex::new(None));

pub struct Tepsi {
    simge: TrayIcon,
    son_ipucu: String,
}

impl Tepsi {
    /// İpucu metnini (fare simgenin üstündeyken görünen) yalnız değiştiğinde günceller.
    pub fn ipucu(&mut self, metin: &str) {
        if self.son_ipucu != metin {
            let _ = self.simge.set_tooltip(Some(metin));
            self.son_ipucu = metin.to_string();
        }
    }
}

pub fn kimlik_ayarla(api_base: &str, kullanici: &str, ofis: &str, parola: &str) {
    if let Ok(mut k) = KIMLIK.lock() {
        *k = Some((api_base.to_string(), kullanici.to_string(), ofis.to_string(), parola.to_string()));
    }
}

pub fn kimlik_temizle() {
    if let Ok(mut k) = KIMLIK.lock() {
        *k = None;
    }
}

pub fn gizli_mi() -> bool {
    GIZLI.load(Ordering::SeqCst)
}

/// Tepsi simgesini kurar (ana iş parçacığında, eframe başlarken çağrılmalı).
pub fn kur(ctx: egui::Context) -> Option<Tepsi> {
    static RGBA: &[u8] = include_bytes!("../assets/armonia-simge-32.rgba");
    let icon = Icon::from_rgba(RGBA.to_vec(), 32, 32).ok()?;
    // Windows'un kendi menüsü yalnız açık/koyu olabildiği için sağ tıkta temalı kendi panelimiz açılır
    // (uyap_app::tepsi_paneli). Sol tık / çift tık pencereyi açar.
    let simge = TrayIconBuilder::new()
        .with_icon(icon)
        .with_tooltip("Çoklu UYAP")
        .build()
        .ok()?;
    TrayIconEvent::set_event_handler(Some(move |e: TrayIconEvent| match e {
        TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. }
        | TrayIconEvent::DoubleClick { .. } => {
            goster();
            ctx.request_repaint();
        }
        TrayIconEvent::Click { button: MouseButton::Right, button_state: MouseButtonState::Up, position, .. } => {
            if let Ok(mut k) = PANEL_ISTEGI.lock() {
                *k = Some((position.x as f32, position.y as f32));
            }
            ctx.request_repaint();
        }
        _ => {}
    }));
    Some(Tepsi { simge, son_ipucu: String::new() })
}

/// Bekleyen panel isteğini alır (fiziksel piksel konumu).
pub fn panel_istegi() -> Option<(f32, f32)> {
    PANEL_ISTEGI.lock().ok().and_then(|mut k| k.take())
}

/// Panel eylemi: günlüğü Masaüstüne kaydet (arka planda; sonucu Windows ileti kutusuyla).
pub fn gunluk_kaydet() {
    std::thread::spawn(|| match crate::sifreli_gunluk::masaustune_kaydet() {
        Ok(yol) => crate::sira_kapisi_arayuz::msg_info(
            "Çoklu UYAP",
            &format!(
                "Günlük kaydedildi:\n{}\n\nBu dosyayı destek ekibine iletebilirsiniz. Dosya şifrelidir; \
                 yalnız Çoklu UYAP destek ekibi açabilir.",
                yol.display()
            ),
        ),
        Err(h) => crate::sira_kapisi_arayuz::msg_error("Çoklu UYAP", &h),
    });
}

/// Panel eylemi: günlüğü destek ekibine gönder.
pub fn gunluk_gonder() {
    std::thread::spawn(|| {
        let kimlik = KIMLIK.lock().ok().and_then(|k| k.clone());
        let Some((api, k, o, p)) = kimlik else {
            crate::sira_kapisi_arayuz::msg_error(
                "Çoklu UYAP",
                "Günlüğü göndermek için önce giriş yapın ya da 'Günlüğü dosyaya kaydet'i kullanın.",
            );
            return;
        };
        match crate::sifreli_gunluk::sunucuya_gonder(&api, &k, &o, &p) {
            Ok(no) => crate::sira_kapisi_arayuz::msg_info(
                "Çoklu UYAP",
                &format!("Günlük destek ekibine gönderildi.\nDestek numaranız: {no}"),
            ),
            Err(h) => crate::sira_kapisi_arayuz::msg_error("Çoklu UYAP", &h),
        }
    });
}

/// Panel eylemi: programdan tamamen çık.
pub fn cikis() -> ! {
    crate::uyap_app::stop_gecit_server(|_| {});
    crate::veritabani::durdur(&|_| {});
    std::process::exit(0);
}

/// Başlığı verilen kendi penceremizi (panel, alt pencereler) öne alır.
pub fn one_al(baslik: &str) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{FindWindowW, SetForegroundWindow};
    let b: Vec<u16> = baslik.encode_utf16().chain(Some(0)).collect();
    // SAFETY: NUL sonlu başlık tamponu; bulunan pencere öne alınır.
    unsafe {
        let h = FindWindowW(std::ptr::null(), b.as_ptr());
        if !h.is_null() {
            SetForegroundWindow(h);
        }
    }
}

fn pencere() -> Option<windows_sys::Win32::Foundation::HWND> {
    use windows_sys::Win32::System::Threading::GetCurrentProcessId;
    use windows_sys::Win32::UI::WindowsAndMessaging::{FindWindowW, GetWindowThreadProcessId};
    let baslik: Vec<u16> = PENCERE_BASLIGI.encode_utf16().chain(Some(0)).collect();
    // SAFETY: başlık tamponu geçerli ve NUL sonlu; yalnız kendi sürecimizin penceresi döndürülür.
    unsafe {
        let h = FindWindowW(std::ptr::null(), baslik.as_ptr());
        if h.is_null() {
            return None;
        }
        let mut pid = 0u32;
        GetWindowThreadProcessId(h, &mut pid);
        (pid == GetCurrentProcessId()).then_some(h)
    }
}

/// Pencereyi tepsiye gizler (ekran dışı + görev çubuğu düğmesi yok).
pub fn gizle() {
    use windows_sys::Win32::Foundation::RECT;
    use windows_sys::Win32::UI::WindowsAndMessaging::*;
    let Some(h) = pencere() else { return };
    if GIZLI.swap(true, Ordering::SeqCst) {
        return;
    }
    // SAFETY: geçerli pencere tanıtıcısında konum/stil okuma-yazma.
    unsafe {
        let mut r = RECT { left: 0, top: 0, right: 0, bottom: 0 };
        if GetWindowRect(h, &mut r) != 0 && r.left > GIZLI_X + 1000 {
            if let Ok(mut k) = ONCEKI_KONUM.lock() {
                *k = Some((r.left, r.top));
            }
        }
        ShowWindow(h, SW_HIDE);
        let ex = GetWindowLongW(h, GWL_EXSTYLE) as u32;
        SetWindowLongW(h, GWL_EXSTYLE, ((ex | WS_EX_TOOLWINDOW) & !WS_EX_APPWINDOW) as i32);
        SetWindowPos(h, std::ptr::null_mut(), GIZLI_X, GIZLI_X, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);
        ShowWindow(h, SW_SHOWNOACTIVATE);
    }
}

/// Pencereyi tepsiden geri getirir ve öne alır.
pub fn goster() {
    use windows_sys::Win32::UI::WindowsAndMessaging::*;
    let Some(h) = pencere() else { return };
    let (x, y) = ONCEKI_KONUM.lock().ok().and_then(|k| *k).unwrap_or((160, 120));
    // SAFETY: geçerli pencere tanıtıcısında stil/konum değişikliği.
    unsafe {
        if GIZLI.swap(false, Ordering::SeqCst) {
            ShowWindow(h, SW_HIDE);
            let ex = GetWindowLongW(h, GWL_EXSTYLE) as u32;
            SetWindowLongW(h, GWL_EXSTYLE, ((ex & !WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW) as i32);
            SetWindowPos(h, std::ptr::null_mut(), x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER);
        }
        ShowWindow(h, SW_SHOW);
        if IsIconic(h) != 0 {
            ShowWindow(h, SW_RESTORE);
        }
        SetForegroundWindow(h);
    }
}
