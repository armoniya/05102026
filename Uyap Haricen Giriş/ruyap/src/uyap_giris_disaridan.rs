//! UYAP Login Replication Script (E-Signature)
//! -------------------------------------------
//! `uyap_core/uyap_giris_dışarıdan.py`'nin 1:1 Rust karşılığı.

use std::collections::HashMap;
use std::io::{self, BufRead, Write};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use clap::Parser;
use reqwest::blocking::Client;
use reqwest::header::{HeaderMap, HeaderValue, ACCEPT, ACCEPT_LANGUAGE, CACHE_CONTROL, CONNECTION, CONTENT_TYPE, PRAGMA, REFERER, USER_AGENT};

pub const STATIC_AUTH_CONTENT: &str =
    "QWRhbGV0IEJha2FubMSxxJ/EsSBCaWxnaSDEsMWfbGVtIEdlbmVsIE3DvGTDvHJsw7zEn8O8IHRhcmFmxLFuZ\
mFuIGdlbGnFn3RpcmlsZW4gdXlndWxhbWF5YSBlLWltemEgZ2lyacWfIHlhcG1hayBpc3RpeW9ydW0u";

#[derive(Debug, Clone)]
pub struct StoredCookie {
    pub name: String,
    pub value: String,
    pub domain: String,
    pub path: String,
}

#[derive(Debug, Clone)]
pub struct LoginArgs {
    pub pin: String,
    pub cert_id: Option<String>,
    pub no_verify: bool,
    pub once: bool,
    pub interval: u64,
}

impl Default for LoginArgs {
    fn default() -> Self {
        Self {
            pin: std::env::var("UYAP_PIN").unwrap_or_default(),
            cert_id: None,
            no_verify: false,
            once: false,
            interval: 60,
        }
    }
}

pub struct UyapSession {
    pub client: Client,
    pub jar: Arc<reqwest::cookie::Jar>,
    pub cookies: Arc<Mutex<Vec<StoredCookie>>>,
    pub headers: HeaderMap,
    pub verify_ssl: bool,
}

impl UyapSession {
    pub fn cookies_dict(&self) -> HashMap<String, String> {
        let guard = self.cookies.lock().unwrap();
        let mut m = HashMap::new();
        for c in guard.iter() {
            m.insert(c.name.clone(), c.value.clone());
        }
        m
    }

    fn record_response_cookies(&self, resp: &reqwest::blocking::Response) {
        let mut guard = self.cookies.lock().unwrap();
        for c in resp.cookies() {
            let name = c.name().to_string();
            let value = c.value().to_string();
            let domain = c.domain().unwrap_or("avukat.uyap.gov.tr").to_string();
            let path = c.path().unwrap_or("/").to_string();
            if let Some(existing) = guard.iter_mut().find(|x| x.name == name) {
                existing.value = value;
                existing.domain = domain;
                existing.path = path;
            } else {
                guard.push(StoredCookie {
                    name,
                    value,
                    domain,
                    path,
                });
            }
        }
    }
}

pub fn initialize_session(verify_ssl: bool) -> Result<UyapSession, String> {
    println!("[*] Step 1: Initializing UYAP Session...");

    let jar = Arc::new(reqwest::cookie::Jar::default());
    let mut headers = HeaderMap::new();
    headers.insert(
        USER_AGENT,
        HeaderValue::from_static("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"),
    );
    headers.insert(
        ACCEPT,
        HeaderValue::from_static("text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8"),
    );
    headers.insert(
        ACCEPT_LANGUAGE,
        HeaderValue::from_static("tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"),
    );
    headers.insert(CACHE_CONTROL, HeaderValue::from_static("no-cache"));
    headers.insert(PRAGMA, HeaderValue::from_static("no-cache"));
    headers.insert(CONNECTION, HeaderValue::from_static("keep-alive"));

    let client = Client::builder()
        .cookie_provider(Arc::clone(&jar))
        .danger_accept_invalid_certs(!verify_ssl)
        .timeout(Duration::from_secs(15))
        .build()
        .map_err(|e| format!("Failed to build HTTP client: {}", e))?;

    let session = UyapSession {
        client,
        jar,
        cookies: Arc::new(Mutex::new(Vec::new())),
        headers: headers.clone(),
        verify_ssl,
    };

    let url = "https://avukat.uyap.gov.tr/giris";
    match session.client.get(url).headers(headers).send() {
        Ok(resp) => {
            println!("[+] Landing page retrieved. Status code: {}", resp.status().as_u16());
            session.record_response_cookies(&resp);
            Ok(session)
        }
        Err(e) => {
            if e.is_connect() || e.to_string().to_lowercase().contains("ssl") || e.to_string().to_lowercase().contains("tls") {
                println!("[!] SSL verification / connection failed: {}", e);
                println!("[!] Tip: If your environment lacks Turkish government root certificates (Kamusm),");
                println!("[!] you may run with '--no-verify' to bypass SSL checks.");
            } else {
                println!("[!] Failed to connect to UYAP: {}", e);
            }
            Err(format!("Failed to connect to UYAP: {}", e))
        }
    }
}

pub fn get_certificates() -> Result<Vec<serde_json::Value>, String> {
    println!("[*] Step 2: Querying local e-imza client for certificates...");
    let url = "http://localhost:5975/api/v1/signature/getCertificates";

    let client = Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(10))
        .build()
        .map_err(|e| format!("HTTP client error: {}", e))?;

    let resp = client.get(url).send().map_err(|e| {
        if e.is_connect() {
            println!("[!] Connection error: Could not reach the local e-imza client on port 5975.");
            println!("[!] Make sure ArkSigner or UYAP e-signature client is open and running.");
        } else {
            println!("[!] Unexpected error querying local client: {}", e);
        }
        format!("Local e-imza client error: {}", e)
    })?;

    if resp.status().as_u16() != 200 {
        let msg = format!(
            "[!] Local client returned unexpected HTTP status {}.",
            resp.status().as_u16()
        );
        println!("{}", msg);
        return Err(msg);
    }

    let res_json: serde_json::Value = resp.json().map_err(|_| {
        let msg = "[!] Invalid JSON returned by the local e-signature client.";
        println!("{}", msg);
        msg.to_string()
    })?;

    let metadata = res_json.get("metadata").cloned().unwrap_or(serde_json::Value::Null);
    if metadata.get("STATUS").and_then(|v| v.as_str()) != Some("SUCCESS") {
        let err_m = metadata
            .get("MESSAGE")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error");
        let msg = format!("[!] Local client reported an error: {}", err_m);
        println!("{}", msg);
        return Err(msg);
    }

    let mut certificates = Vec::new();
    if let Some(arr) = res_json.get("data").and_then(|v| v.as_array()) {
        for terminal_data in arr {
            let terminal_name = terminal_data
                .get("terminal")
                .and_then(|v| v.as_str())
                .unwrap_or("Unknown Reader");
            let is_ready = terminal_data
                .get("ready")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);

            if !is_ready {
                println!(
                    "[!] Reader found but not ready: {}. Please insert your smart card.",
                    terminal_name
                );
                continue;
            }

            if let Some(certs) = terminal_data.get("certificates").and_then(|v| v.as_array()) {
                for cert in certs {
                    let mut c = cert.clone();
                    if let Some(obj) = c.as_object_mut() {
                        obj.insert(
                            "_terminal".to_string(),
                            serde_json::Value::String(terminal_name.to_string()),
                        );
                    }
                    certificates.push(c);
                }
            }
        }
    }

    if certificates.is_empty() {
        println!("[!] No active certificates detected.");
        println!("[!] Please verify that your smart card reader is connected and card is inserted.");
        return Err("No active certificates detected.".to_string());
    }

    Ok(certificates)
}

/// notAfter okunabiliyorsa ve geçmişteyse true. Okunamayan biçimde false (sertifika elenmez).
fn sertifika_suresi_dolmus(cert: &serde_json::Value) -> bool {
    use chrono::{NaiveDate, NaiveDateTime, Utc};
    let simdi = Utc::now().naive_utc();
    let bitis: Option<NaiveDateTime> = match cert.get("notAfter") {
        Some(serde_json::Value::Number(n)) => n
            .as_i64()
            .and_then(|ms| chrono::DateTime::from_timestamp_millis(ms))
            .map(|d| d.naive_utc()),
        Some(serde_json::Value::String(s)) => {
            let s = s.trim();
            chrono::DateTime::parse_from_rfc3339(s)
                .map(|d| d.naive_utc())
                .ok()
                .or_else(|| {
                    ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S"]
                        .iter()
                        .find_map(|f| NaiveDateTime::parse_from_str(s, f).ok())
                })
                .or_else(|| {
                    ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"]
                        .iter()
                        .find_map(|f| NaiveDate::parse_from_str(s, f).ok())
                        .and_then(|d| d.and_hms_opt(23, 59, 59))
                })
        }
        _ => None,
    };
    matches!(bitis, Some(b) if b < simdi)
}

pub fn select_certificate(
    certs: &[serde_json::Value],
    auto_cert_id: Option<&str>,
) -> Result<serde_json::Value, String> {
    if let Some(cid) = auto_cert_id {
        if !cid.is_empty() {
            for cert in certs {
                if cert.get("certificateId").and_then(|v| v.as_str()) == Some(cid) {
                    let subj = cert.get("subject").and_then(|v| v.as_str()).unwrap_or("");
                    println!("[+] Automatically selected certificate ID: {} ({})", cid, subj);
                    return Ok(cert.clone());
                }
            }
            println!(
                "[!] Certificate with ID '{}' not found. Falling back to selection.",
                cid
            );
        }
    }

    if certs.len() == 1 {
        let selected = &certs[0];
        let subj = selected.get("subject").and_then(|v| v.as_str()).unwrap_or("");
        let term = selected.get("_terminal").and_then(|v| v.as_str()).unwrap_or("");
        println!(
            "[+] Found one certificate. Subject: {} (Terminal: {})",
            subj, term
        );
        return Ok(selected.clone());
    }

    println!("\nAvailable Certificates:");
    for (idx, cert) in certs.iter().enumerate() {
        println!("  [{}] Subject  : {}", idx, cert.get("subject").and_then(|v| v.as_str()).unwrap_or(""));
        println!("      Issuer   : {}", cert.get("issuer").and_then(|v| v.as_str()).unwrap_or(""));
        println!("      Terminal : {}", cert.get("_terminal").and_then(|v| v.as_str()).unwrap_or(""));
        println!("      ID       : {}", cert.get("certificateId").and_then(|v| v.as_str()).unwrap_or(""));
        println!(
            "      Valid    : {} to {}",
            cert.get("notBefore").and_then(|v| v.as_str()).unwrap_or(""),
            cert.get("notAfter").and_then(|v| v.as_str()).unwrap_or("")
        );
        println!("{}", "-".repeat(50));
    }

    if !ETKILESIMLI_SECIM.load(std::sync::atomic::Ordering::SeqCst) {
        // Süresi geçmemiş ilk sertifika (yoksa ilki). notAfter biçimi istemciye göre değişebilir;
        // karşılaştırma yapılamazsa sıra korunur.
        let secilen = certs
            .iter()
            .find(|c| !sertifika_suresi_dolmus(c))
            .unwrap_or(&certs[0]);
        println!(
            "[+] {} sertifika bulundu; otomatik seçildi: {}",
            certs.len(),
            secilen.get("subject").and_then(|v| v.as_str()).unwrap_or("")
        );
        return Ok(secilen.clone());
    }

    let stdin = io::stdin();
    loop {
        print!("\nSelect a certificate [0-{}] (default 0): ", certs.len() - 1);
        let _ = io::stdout().flush();
        let mut line = String::new();
        if stdin.lock().read_line(&mut line).is_err() {
            return Ok(certs[0].clone());
        }
        let choice = line.trim();
        if choice.is_empty() {
            return Ok(certs[0].clone());
        }
        match choice.parse::<usize>() {
            Ok(idx) if idx < certs.len() => return Ok(certs[idx].clone()),
            Ok(_) => println!("[!] Choice must be between 0 and {}.", certs.len() - 1),
            Err(_) => println!("[!] Invalid input. Please enter a valid number."),
        }
    }
}

pub fn sign_payload(certificate_id: &str, pin: &str) -> Result<(String, String), String> {
    println!("[*] Step 3: Sending signing request to local client...");
    let url = "http://localhost:5975/api/v1/signature/sign";

    let payload = serde_json::json!({
        "certificateId": certificate_id,
        "password": pin,
        "content": STATIC_AUTH_CONTENT,
    });

    let client = Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(|e| format!("HTTP client error: {}", e))?;

    let resp = client
        .post(url)
        .header(CONTENT_TYPE, "application/json")
        .header(ACCEPT, "application/json")
        .json(&payload)
        .send()
        .map_err(|e| {
            let msg = format!("[!] Error occurred during signing: {}", e);
            println!("{}", msg);
            msg
        })?;

    if resp.status().as_u16() != 200 {
        let msg = format!(
            "[!] Sign request failed with status code {}.",
            resp.status().as_u16()
        );
        println!("{}", msg);
        return Err(msg);
    }

    let res_json: serde_json::Value = resp.json().map_err(|_| {
        let msg = "[!] Invalid response JSON from sign service.";
        println!("{}", msg);
        msg.to_string()
    })?;

    let metadata = res_json.get("metadata").cloned().unwrap_or(serde_json::Value::Null);
    if metadata.get("STATUS").and_then(|v| v.as_str()) != Some("SUCCESS") {
        let err_m = metadata
            .get("MESSAGE")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown error");
        println!("[!] Signing failed: {}", err_m);
        println!("[!] Please make sure your PIN is correct and the card is not locked.");
        return Err(format!("Signing failed: {}", err_m));
    }

    let data = res_json.get("data").cloned().unwrap_or(serde_json::Value::Null);
    let signed_data = data
        .get("signedData")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let tx_id = data
        .get("txId")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    if signed_data.is_empty() || tx_id.is_empty() {
        let msg = "[!] Response did not contain 'signedData' or 'txId'.";
        println!("{}", msg);
        return Err(msg.to_string());
    }

    println!("[+] Payload successfully signed.");
    Ok((signed_data, tx_id))
}

pub fn submit_signature(
    session: &mut UyapSession,
    signed_data: &str,
    tx_id: &str,
) -> Result<(), String> {
    println!("[*] Step 4: Submitting signature to UYAP (web1.uyap)...");
    let url = "https://avukat.uyap.gov.tr/web1.uyap";

    session.headers.insert(
        REFERER,
        HeaderValue::from_static("https://avukat.uyap.gov.tr/giris"),
    );
    session.headers.insert(
        ACCEPT,
        HeaderValue::from_static("application/json, text/plain, */*"),
    );

    let resp = session
        .client
        .get(url)
        .headers(session.headers.clone())
        .query(&[("signature", signed_data), ("txId", tx_id)])
        .timeout(Duration::from_secs(20))
        .send()
        .map_err(|e| {
            let msg = format!("[!] Failed to submit signature: {}", e);
            println!("{}", msg);
            msg
        })?;

    session.record_response_cookies(&resp);

    if resp.status().as_u16() != 200 {
        let msg = format!(
            "[!] UYAP returned error status {} on signature submission.",
            resp.status().as_u16()
        );
        println!("{}", msg);
        return Err(msg);
    }

    let text = resp.text().unwrap_or_default();
    match serde_json::from_str::<serde_json::Value>(&text) {
        Ok(res_json) => {
            if !res_json.get("success").and_then(|v| v.as_bool()).unwrap_or(false) {
                let msg = format!("[!] UYAP reported signature validation failure: {}", res_json);
                println!("{}", msg);
                return Err(msg);
            }
        }
        Err(_) => {
            if !text.to_lowercase().contains("success") {
                let preview: String = text.chars().take(200).collect();
                let msg = format!(
                    "[!] UYAP response did not contain verification success: {}",
                    preview
                );
                println!("{}", msg);
                return Err(msg);
            }
        }
    }

    println!("[+] Signature verified by UYAP.");
    Ok(())
}

pub fn confirm_login(session: &mut UyapSession) -> Result<(), String> {
    println!("[*] Step 5: Confirming login state (login.uyap)...");
    let url = "https://avukat.uyap.gov.tr/login.uyap";

    session
        .headers
        .insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
    session.headers.insert(
        REFERER,
        HeaderValue::from_static("https://avukat.uyap.gov.tr/giris"),
    );

    let resp = session
        .client
        .post(url)
        .headers(session.headers.clone())
        .body("{}")
        .timeout(Duration::from_secs(20))
        .send()
        .map_err(|e| {
            let msg = format!("[!] Error during login confirmation: {}", e);
            println!("{}", msg);
            msg
        })?;

    session.record_response_cookies(&resp);

    if resp.status().as_u16() != 200 {
        let msg = format!(
            "[!] Login confirmation failed with status {}.",
            resp.status().as_u16()
        );
        println!("{}", msg);
        return Err(msg);
    }

    println!("[+] Login state confirmed.");
    Ok(())
}

pub fn verify_login_success(session: &mut UyapSession) -> Result<String, String> {
    println!("[*] Step 6: Verifying login success & fetching Lawyer ID...");
    let url = "https://avukat.uyap.gov.tr/get_avukat_id.ajx";

    session
        .headers
        .insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
    session.headers.insert(
        REFERER,
        HeaderValue::from_static("https://avukat.uyap.gov.tr/"),
    );

    let mut last_error_info = String::new();
    for attempt in 0..3 {
        match session
            .client
            .post(url)
            .headers(session.headers.clone())
            .body("{}")
            .timeout(Duration::from_secs(20))
            .send()
        {
            Ok(resp) => {
                session.record_response_cookies(&resp);
                let status = resp.status().as_u16();
                let body_txt = resp.text().unwrap_or_default();
                if status == 200 {
                    let avukat_id = body_txt.trim().to_string();
                    if !avukat_id.is_empty() && avukat_id.chars().all(|c| c.is_ascii_digit()) {
                        println!(
                            "[SUCCESS] LOGIN SUCCESSFUL! Verified Lawyer ID: {}",
                            avukat_id
                        );
                        return Ok(avukat_id);
                    } else {
                        last_error_info = format!("Unexpected response: '{}'", avukat_id);
                    }
                } else {
                    let preview: String = body_txt.chars().take(200).collect();
                    last_error_info = format!("HTTP {} - Body: {}", status, preview);
                }
                println!(
                    "[!] Step 6 attempt {} failed: {}. Retrying...",
                    attempt + 1,
                    last_error_info
                );
            }
            Err(e) => {
                last_error_info = e.to_string();
                println!(
                    "[!] Step 6 attempt {} error: {}. Retrying...",
                    attempt + 1,
                    e
                );
            }
        }
        std::thread::sleep(Duration::from_secs(1));
    }

    Err(format!(
        "Failed to query lawyer ID endpoint: {}",
        last_error_info
    ))
}

pub fn is_session_alive(session: &UyapSession) -> bool {
    let url = "https://avukat.uyap.gov.tr/get_avukat_id.ajx";
    let resp = match session
        .client
        .post(url)
        .header(CONTENT_TYPE, "application/json")
        .header(REFERER, "https://avukat.uyap.gov.tr/")
        .body("{}")
        .timeout(Duration::from_secs(15))
        .send()
    {
        Ok(r) => r,
        Err(_) => return false,
    };
    if resp.status().as_u16() != 200 {
        return false;
    }
    let body = resp.text().unwrap_or_default();
    let avukat_id = body.trim();
    !avukat_id.is_empty() && avukat_id.chars().all(|c| c.is_ascii_digit())
}

/// perform_login hatası bu önekle başlıyorsa PIN karta hiç gitmemiştir; yeniden denemek kartı kilitlemez.
pub const GECICI_HATA: &str = "[geçici] ";

fn gecici_hata_turkce(e: &str) -> String {
    let l = e.to_ascii_lowercase();
    let neden = if l.contains("failed to connect to uyap") {
        "UYAP sunucusuna (avukat.uyap.gov.tr) ulaşılamadı; ağ/DNS bağlantısını denetleyin"
    } else if l.contains("local e-imza client error") || l.contains("unexpected http status") || l.contains("invalid json") {
        "E-imza istemcisine (127.0.0.1:5975) ulaşılamadı; istemci açılıyor olabilir, biraz sonra yeniden denenecek"
    } else if l.contains("no active certificates") {
        "Kart okuyucuda sertifika bulunamadı; okuyucunun takılı, kartın içinde olduğundan emin olun"
    } else if l.contains("local client reported an error") {
        "E-imza istemcisi hata bildirdi"
    } else {
        "Giriş hazırlığı başarısız"
    };
    format!("{neden} ({e})")
}

pub fn perform_login(
    args: &LoginArgs,
    verify_ssl: bool,
    cert_id: Option<&str>,
) -> Result<(UyapSession, String), String> {
    // PIN karta gönderilmeden önceki adımlar (UYAP'a ulaşma, e-imza istemcisi, kart/sertifika) geçicidir:
    // istemci açılışta henüz hazır değilken ya da kart sonradan takılınca yeniden denenebilir.
    let gecici = |e: String| format!("{GECICI_HATA}{}", gecici_hata_turkce(&e));
    let mut session = initialize_session(verify_ssl).map_err(gecici)?;
    let certs = get_certificates().map_err(gecici)?;

    let pref = cert_id.or(args.cert_id.as_deref());
    let selected_cert = select_certificate(&certs, pref).map_err(gecici)?;
    let used_cert_id = selected_cert
        .get("certificateId")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let (signed_data, tx_id) = sign_payload(&used_cert_id, &args.pin)?;
    submit_signature(&mut session, &signed_data, &tx_id)?;
    confirm_login(&mut session)?;
    verify_login_success(&mut session)?;
    // Giriş yapan e-imza avukatı: Dosya Listesi "Rolüm" sütunu için büro ayarına (20.11.2026).
    crate::icra_dosyalar::eimza_avukatini_kaydet(selected_cert.get("subject").and_then(|v| v.as_str()).unwrap_or(""));
    Ok((session, used_cert_id))
}

pub fn run_keepalive(
    mut session: UyapSession,
    mut cert_id: String,
    args: &LoginArgs,
    verify_ssl: bool,
) {
    let interval = args.interval;
    println!(
        "\n[*] Keep-alive mode active. Checking every {}s. Press Ctrl+C to stop.",
        interval
    );
    loop {
        std::thread::sleep(Duration::from_secs(interval));
        if is_session_alive(&session) {
            println!("[+] Session still valid.");
        } else {
            println!("[!] Session dropped. Re-authenticating with e-imza...");
            match perform_login(args, verify_ssl, Some(&cert_id)) {
                Ok((new_s, new_c)) => {
                    session = new_s;
                    cert_id = new_c;
                    println!("[+] Session renewed.");
                }
                Err(e) => {
                    println!(
                        "[!] Keep-alive cycle error: {}. Stopping; run again to retry.",
                        e
                    );
                    return;
                }
            }
        }
    }
}

#[derive(Parser, Debug)]
#[command(about = "Programmatically login to UYAP Portal via Local E-Imza client.")]
struct Cli {
    #[arg(long, env = "UYAP_PIN")]
    pin: Option<String>,
    #[arg(long = "cert-id")]
    cert_id: Option<String>,
    #[arg(long = "no-verify")]
    no_verify: bool,
    #[arg(long)]
    once: bool,
    #[arg(long, default_value_t = 60)]
    interval: u64,
}

/// Sertifika seçimi yalnız komut satırı aracında (main_cli) stdin'den sorulur. Uygulama/vekil içinde
/// stdin bir konsol olabilir (Armonia kiosk: tty1) ve read_line sonsuza dek bekler → giriş hiç bitmez.
/// Windows GUI'de stdin yok (anında hata → ilk sertifika); aynı davranış her yerde varsayılan. 2026-11-14.
static ETKILESIMLI_SECIM: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

pub fn main_cli() {
    ETKILESIMLI_SECIM.store(true, std::sync::atomic::Ordering::SeqCst);
    let cli = Cli::parse();
    let pin = match cli.pin {
        Some(p) if !p.is_empty() => p,
        _ => {
            println!(
                "[!] PIN verilmedi. --pin ile ya da UYAP_PIN ortam değişkeniyle sağlayın \
                 (güvenlik gereği kaynağa gömülmez)."
            );
            std::process::exit(2);
        }
    };

    let mut verify_ssl = true;
    if cli.no_verify {
        let insecure_env = std::env::var("UYAP_ALLOW_INSECURE_TLS")
            .unwrap_or_default()
            .trim()
            .to_lowercase();
        let insecure_ok = matches!(insecure_env.as_str(), "1" | "true" | "yes" | "on");
        let bar = "!".repeat(72);
        if insecure_ok {
            println!("\n{}", bar);
            println!("[GUVENLIK][UYARI] UYAP TLS sertifika dogrulamasi DEVRE DISI (--no-verify).");
            println!("  Baglanti MITM'e ACIK. Yalnizca guvenilir gelistirme aginda kullanin!");
            println!("{}\n", bar);
            verify_ssl = false;
        } else {
            println!("\n{}", bar);
            println!("[GUVENLIK] --no-verify YOK SAYILDI: UYAP TLS dogrulamasi ACIK tutuldu.");
            println!("  Gercekten gerekiyorsa (yalnizca gelistirme) UYAP_ALLOW_INSECURE_TLS=1 ayarlayin.");
            println!("{}\n", bar);
        }
    }

    println!("==================================================");
    println!("      UYAP E-IMZA PROGRAMMATIC LOGIN SIMULATION   ");
    println!("==================================================");

    let args = LoginArgs {
        pin,
        cert_id: cli.cert_id,
        no_verify: cli.no_verify,
        once: cli.once,
        interval: cli.interval,
    };

    let (session, cert_id) = match perform_login(&args, verify_ssl, None) {
        Ok(res) => res,
        Err(e) => {
            println!("[!] {}", e);
            std::process::exit(1);
        }
    };

    println!("\n--- Active Session Cookies ---");
    for (name, val) in session.cookies_dict() {
        println!("  {}: {}", name, val);
    }

    println!("\n[SUCCESS] Login process completed successfully. The session is held in memory only.");

    if args.once {
        let mut s = String::new();
        let _ = io::stdin().read_line(&mut s);
    } else {
        run_keepalive(session, cert_id, &args, verify_ssl);
    }
}
