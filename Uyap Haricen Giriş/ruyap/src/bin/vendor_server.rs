//! `vendor_server.py` ikili giriş noktası (satıcı sunucusu: webapp + /ws signaling + hesap deposu + ZS).

fn main() {
    ruyap::ensure_rustls_provider();
    ruyap::vendor_server::main();
}
