// Konsol penceresi açılmasın (program tepside çalışır); hata ayıklama derlemesinde konsol kalır.
#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]
//! Çoklu UYAP — Masaüstü Ofis Uygulaması (`CokluUyap.exe` / `uyap_app.py` 1:1 ana giriş noktası).
//! `cokluuyap_app.spec` uyarınca `--sira-yonetici` ve `--sira-kapisi` alt komutlarını
//! pencere açmadan çalıştırır; normal çağrıda masaüstü arayüzünü (`uyap_app::main`) başlatır.

fn main() {
    ruyap::ensure_rustls_provider();
    ruyap::uyap_app::main();
}
