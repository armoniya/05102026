// Konsol penceresi açılmasın (program tepside çalışır); hata ayıklama derlemesinde konsol kalır.
#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]
//! `uyap_app.py` ikili giriş noktası.

fn main() {
    ruyap::ensure_rustls_provider();
    ruyap::uyap_app::main();
}
