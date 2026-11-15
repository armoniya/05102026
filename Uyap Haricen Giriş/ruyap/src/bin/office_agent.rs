//! `uyap_core/office_agent.py` ikili giriş noktası (UYAP ofis ajanı — LAN + WebRTC paylaşım).

fn main() {
    ruyap::ensure_rustls_provider();
    ruyap::office_agent::main();
}
