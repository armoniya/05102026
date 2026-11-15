//! TURN kurulumu uçtan uca doğrulama (`turn_dogrula.py` birebir Rust portu).
//!
//! Satıcı sunucusunun /ice ucundan efemeral TURN kimliğini çeker ve YALNIZ TURN
//! sunucularıyla (STUN'suz) aday toplar: SDP'de "typ relay" adayı görünüyorsa
//! coturn ayakta, sır doğru ve istemcilerin izlediği yol çalışıyor demektir.

use std::sync::Arc;
use std::time::Duration;

use clap::Parser;
use serde_json::Value;
use webrtc::api::APIBuilder;
use webrtc::peer_connection::configuration::RTCConfiguration;

use crate::p2p_wire::RTCIceServerConfig;

#[derive(Parser, Debug)]
#[command(about = "TURN kurulumu uçtan uca doğrulama.")]
struct Args {
    /// satıcı sunucusu kökü (varsayılan: https://www.cokluuyap.com)
    #[arg(long, default_value = "https://www.cokluuyap.com")]
    server: String,
}

pub async fn run_turn_dogrula(server: &str) -> i32 {
    let ice_url = format!("{}/ice", server.trim_end_matches('/'));
    println!("[*] {ice_url} sorgulanıyor…");

    let client = match reqwest::Client::builder()
        .timeout(Duration::from_secs(15))
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            eprintln!("TURN-HATA: HTTP istemcisi kurulamadı: {e}");
            return 1;
        }
    };

    let resp_json: Value = match client.get(&ice_url).send().await {
        Ok(r) => match r.json::<Value>().await {
            Ok(v) => v,
            Err(e) => {
                eprintln!("TURN-HATA: /ice JSON çözülemedi: {e}");
                return 1;
            }
        },
        Err(e) => {
            eprintln!("TURN-HATA: {ice_url} isteği başarısız: {e}");
            return 1;
        }
    };

    let data = resp_json
        .get("iceServers")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    let turn: Vec<RTCIceServerConfig> = data
        .iter()
        .filter(|s| {
            s.get("username")
                .and_then(|u| u.as_str())
                .map(|u| !u.is_empty())
                .unwrap_or(false)
                && s.get("credential")
                    .and_then(|c| c.as_str())
                    .map(|c| !c.is_empty())
                    .unwrap_or(false)
        })
        .filter_map(RTCIceServerConfig::from_json_value)
        .collect();

    if turn.is_empty() {
        println!("TURN-HATA: /ice yanıtında kimlikli TURN kaydı yok.");
        println!("           Render'da UYAP_TURN_SECRET + UYAP_TURN_URLS tanımlı mı?");
        println!(
            "           Gelen liste: {}",
            serde_json::to_string(&data).unwrap_or_else(|_| "[]".to_string())
        );
        return 1;
    }

    println!(
        "[*] TURN kaydı bulundu: urls={:?} kullanıcı={}",
        turn[0].urls,
        turn[0].username.as_deref().unwrap_or("")
    );

    let servers: Vec<_> = turn.iter().map(|s| s.to_webrtc()).collect();
    let api = APIBuilder::new().build();
    let config = RTCConfiguration {
        ice_servers: servers,
        ..Default::default()
    };

    let pc = match api.new_peer_connection(config).await {
        Ok(pc) => Arc::new(pc),
        Err(e) => {
            eprintln!("TURN-HATA: RTCPeerConnection kurulamadı: {e}");
            return 2;
        }
    };

    let relays: Vec<String> = {
        let mut collected = Vec::new();
        if pc.create_data_channel("dogrulama", None).await.is_ok() {
            if let Ok(offer) = pc.create_offer(None).await {
                let mut gather_complete = pc.gathering_complete_promise().await;
                if pc.set_local_description(offer).await.is_ok() {
                    let _ = tokio::time::timeout(Duration::from_secs(30), gather_complete.recv()).await;
                    if let Some(desc) = pc.local_description().await {
                        collected = desc
                            .sdp
                            .lines()
                            .map(|ln| ln.trim())
                            .filter(|ln| format!("{ln} ").contains(" typ relay "))
                            .map(|ln| ln.to_string())
                            .collect();
                    }
                }
            }
        }
        let _ = pc.close().await;
        collected
    };

    if !relays.is_empty() {
        println!("TURN-OK: relay adayı alındı ({})", relays[0]);
        0
    } else {
        println!("TURN-HATA: kimlik alındı ama relay adayı toplanamadı.");
        println!("           VPS firewall'unda UDP/TCP 3478 açık mı? coturn ayakta mı");
        println!("           (journalctl -u coturn)? Saatler NTP ile senkron mu?");
        2
    }
}

pub fn main() {
    let args = Args::parse();
    let rt = crate::build_tokio_runtime().expect("Tokio runtime başlatılamadı");
    let code = rt.block_on(run_turn_dogrula(&args.server));
    if code != 0 {
        std::process::exit(code);
    }
}
