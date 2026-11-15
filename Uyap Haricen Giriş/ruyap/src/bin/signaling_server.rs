//! `signaling_server.py` ikili giriş noktası (UYAP P2P buluşturma / signaling sunucusu).

fn main() {
    ruyap::ensure_rustls_provider();
    ruyap::signaling::main();
}
