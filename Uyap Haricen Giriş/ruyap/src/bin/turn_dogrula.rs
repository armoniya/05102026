//! `turn_dogrula.py` ikili giriş noktası (TURN kurulumu uçtan uca doğrulama).

fn main() {
    ruyap::ensure_rustls_provider();
    ruyap::turn_dogrula::main();
}
