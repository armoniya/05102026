//! `uyap_core.ptt_sorgu` — tek barkod için PTT gönderi takip sorgusu (Playwright'sız)
//! (`uyap_core/ptt_sorgu.py` 1:1 portu)

use std::collections::{HashMap, HashSet};
use std::fs::File;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex as StdMutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use base64::Engine;
use once_cell::sync::Lazy;
use rand::Rng;
use regex::Regex;
use serde_json::{json, Value};
use tokio::sync::{mpsc, oneshot, Mutex as AsyncMutex};

pub const SITE_URL: &str = "https://www.ptt.gov.tr/";
pub const TAKIP_YOLU: &str = "/api/ShipmentTracking";
pub const DOGRULAMA_HATASI: &str = "Doğrulama tamamlanamadı";
pub static BARKOD_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\d{13}$").unwrap());
pub const BEKLEME_SN: f64 = 3.0;
pub const ZAMAN_ASIMI_SN: f64 = 25.0;
pub const SAYFA_ZAMAN_ASIMI_SN: f64 = 45.0;
pub const BEKLEYEN_AZAMI: usize = 2;

pub const _KUTU_JS: &str = r#"
(() => {
  const gor = e => !!(e && e.offsetParent !== null && e.getBoundingClientRect().width > 0);
  const k = [...document.querySelectorAll('input[placeholder="Gönderi takip no giriniz"], input[name="value"]')].find(gor);
  if (!k) return null;
  const b = [...k.parentElement.querySelectorAll('button')].find(x => /sorgula/i.test(x.textContent || '') && gor(x));
  if (!b) return null;
  k.scrollIntoView({block: 'center'});
  const r = b.getBoundingClientRect();
  return {x: r.left + r.width / 2, y: r.top + r.height / 2};
})()
"#;

pub fn _chrome_exe_bul() -> Option<String> {
    let local = std::env::var("LOCALAPPDATA").unwrap_or_default();
    let adaylar = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe".to_string(),
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe".to_string(),
        if local.is_empty() {
            String::new()
        } else {
            Path::new(&local)
                .join("Google")
                .join("Chrome")
                .join("Application")
                .join("chrome.exe")
                .to_string_lossy()
                .to_string()
        },
    ];
    adaylar.into_iter().find(|c| !c.is_empty() && Path::new(c).exists())
}

pub fn _profil_klasoru() -> PathBuf {
    let kok = std::env::var("LOCALAPPDATA")
        .ok()
        .filter(|s| !s.is_empty())
        .or_else(|| std::env::var("USERPROFILE").ok())
        .unwrap_or_else(|| ".".to_string());
    let yol = Path::new(&kok).join("CokluUyap").join("ptt_profili");
    let _ = std::fs::create_dir_all(&yol);
    yol
}

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

fn quote_win_arg(arg: &str) -> String {
    if !arg.is_empty() && !arg.chars().any(|c| c == ' ' || c == '\t' || c == '"') {
        return arg.to_string();
    }
    let mut out = String::from("\"");
    let mut backslashes = 0usize;
    for ch in arg.chars() {
        if ch == '\\' {
            backslashes += 1;
        } else if ch == '"' {
            out.push_str(&"\\".repeat(backslashes * 2 + 1));
            out.push('"');
            backslashes = 0;
        } else {
            out.push_str(&"\\".repeat(backslashes));
            backslashes = 0;
            out.push(ch);
        }
    }
    out.push_str(&"\\".repeat(backslashes * 2));
    out.push('"');
    out
}

fn build_cmdline(komut: &[String]) -> String {
    komut.iter().map(|a| quote_win_arg(a)).collect::<Vec<_>>().join(" ")
}

#[cfg(windows)]
mod win_chrome {
    use super::*;
    use std::ffi::c_void;
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::FromRawHandle;
    use std::ptr::{null, null_mut};
    use windows_sys::Win32::Foundation::{
        CloseHandle, GetLastError, SetHandleInformation, HANDLE, HANDLE_FLAG_INHERIT,
        INVALID_HANDLE_VALUE, WAIT_OBJECT_0,
    };
    use windows_sys::Win32::System::Pipes::CreatePipe;
    use windows_sys::Win32::System::Threading::{
        CreateProcessW, DeleteProcThreadAttributeList, GetExitCodeProcess,
        InitializeProcThreadAttributeList, TerminateProcess, UpdateProcThreadAttribute,
        WaitForSingleObject, EXTENDED_STARTUPINFO_PRESENT, PROCESS_INFORMATION,
        PROC_THREAD_ATTRIBUTE_HANDLE_LIST, STARTUPINFOEXW, STARTUPINFOW,
    };

    fn to_wide(s: &str) -> Vec<u16> {
        std::ffi::OsStr::new(s)
            .encode_wide()
            .chain(std::iter::once(0))
            .collect()
    }

    pub struct ChromeSureci {
        process_handle: HANDLE,
        pub pid: u32,
        pub returncode: Option<u32>,
        pub oku_file: Option<File>,
        pub yaz_file: Option<File>,
    }

    unsafe impl Send for ChromeSureci {}
    unsafe impl Sync for ChromeSureci {}

    impl ChromeSureci {
        pub fn spawn(komut: &[String], creationflags: u32) -> Result<Self, String> {
            let hata = |ne: &str| -> String {
                let err = unsafe { GetLastError() };
                format!("Chrome başlatılamadı ({}: {})", ne, err)
            };

            let boru = || -> Result<(HANDLE, HANDLE), String> {
                let mut r: HANDLE = 0 as HANDLE;
                let mut w: HANDLE = 0 as HANDLE;
                let ok = unsafe { CreatePipe(&mut r, &mut w, null_mut(), 0) };
                if ok == 0 {
                    return Err(hata("CreatePipe"));
                }
                Ok((r, w))
            };

            const FOPEN: u8 = 0x01;
            const FPIPE: u8 = 0x08;

            let (cocuk_okur, biz_yazariz) = boru()?;
            let (biz_okuruz, cocuk_yazar) = match boru() {
                Ok(p) => p,
                Err(e) => {
                    unsafe {
                        CloseHandle(cocuk_okur);
                        CloseHandle(biz_yazariz);
                    }
                    return Err(e);
                }
            };

            let cocuk_uclari = [cocuk_okur, cocuk_yazar];
            for h in cocuk_uclari {
                let ok = unsafe { SetHandleInformation(h, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT) };
                if ok == 0 {
                    unsafe {
                        CloseHandle(cocuk_okur);
                        CloseHandle(cocuk_yazar);
                        CloseHandle(biz_yazariz);
                        CloseHandle(biz_okuruz);
                    }
                    return Err(hata("SetHandleInformation"));
                }
            }

            // lpReserved2: int32 count (5) | 5 bytes CRT flags | 5 * HANDLE (unaligned)
            let mut ham: Vec<u8> = Vec::with_capacity(4 + 5 + 5 * std::mem::size_of::<isize>());
            ham.extend_from_slice(&5i32.to_le_bytes());
            ham.extend_from_slice(&[0u8, 0u8, 0u8, FOPEN | FPIPE, FOPEN | FPIPE]);
            let handles_raw: [isize; 5] = [-1, -1, -1, cocuk_okur as isize, cocuk_yazar as isize];
            for hr in handles_raw {
                ham.extend_from_slice(&hr.to_le_bytes());
            }

            let mut attr_size: usize = 0;
            unsafe {
                InitializeProcThreadAttributeList(null_mut(), 1, 0, &mut attr_size);
            }
            let mut attr_buf = vec![0u8; attr_size.max(64)];
            let attr_list = attr_buf.as_mut_ptr() as *mut c_void;
            if unsafe { InitializeProcThreadAttributeList(attr_list, 1, 0, &mut attr_size) } == 0 {
                unsafe {
                    CloseHandle(cocuk_okur);
                    CloseHandle(cocuk_yazar);
                    CloseHandle(biz_yazariz);
                    CloseHandle(biz_okuruz);
                }
                return Err(hata("InitializeProcThreadAttributeList"));
            }

            let mut inheritable = cocuk_uclari;
            let ok_upd = unsafe {
                UpdateProcThreadAttribute(
                    attr_list,
                    0,
                    PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
                    inheritable.as_mut_ptr() as *const c_void,
                    std::mem::size_of::<[HANDLE; 2]>(),
                    null_mut(),
                    null_mut(),
                )
            };
            if ok_upd == 0 {
                unsafe {
                    DeleteProcThreadAttributeList(attr_list);
                    CloseHandle(cocuk_okur);
                    CloseHandle(cocuk_yazar);
                    CloseHandle(biz_yazariz);
                    CloseHandle(biz_okuruz);
                }
                return Err(hata("UpdateProcThreadAttribute"));
            }

            let mut si: STARTUPINFOEXW = unsafe { std::mem::zeroed() };
            si.StartupInfo.cb = std::mem::size_of::<STARTUPINFOEXW>() as u32;
            si.StartupInfo.cbReserved2 = ham.len() as u16;
            si.StartupInfo.lpReserved2 = ham.as_mut_ptr();
            si.lpAttributeList = attr_list;

            let mut pi: PROCESS_INFORMATION = unsafe { std::mem::zeroed() };
            let app_w = to_wide(&komut[0]);
            let mut cmd_w = to_wide(&build_cmdline(komut));

            let created = unsafe {
                CreateProcessW(
                    app_w.as_ptr(),
                    cmd_w.as_mut_ptr(),
                    null_mut(),
                    null_mut(),
                    1,
                    creationflags | EXTENDED_STARTUPINFO_PRESENT,
                    null(),
                    null(),
                    &mut si as *mut STARTUPINFOEXW as *mut STARTUPINFOW,
                    &mut pi,
                )
            };

            unsafe {
                DeleteProcThreadAttributeList(attr_list);
                CloseHandle(cocuk_okur);
                CloseHandle(cocuk_yazar);
            }

            if created == 0 {
                let err_msg = hata("CreateProcessW");
                unsafe {
                    CloseHandle(biz_yazariz);
                    CloseHandle(biz_okuruz);
                }
                return Err(err_msg);
            }

            unsafe {
                CloseHandle(pi.hThread);
            }

            let yaz_file = unsafe { File::from_raw_handle(biz_yazariz as *mut c_void) };
            let oku_file = unsafe { File::from_raw_handle(biz_okuruz as *mut c_void) };

            Ok(Self {
                process_handle: pi.hProcess,
                pid: pi.dwProcessId,
                returncode: None,
                oku_file: Some(oku_file),
                yaz_file: Some(yaz_file),
            })
        }

        pub fn poll(&mut self) -> Option<u32> {
            if self.returncode.is_none() && self.process_handle != INVALID_HANDLE_VALUE && self.process_handle != (0 as HANDLE) {
                let res = unsafe { WaitForSingleObject(self.process_handle, 0) };
                if res == WAIT_OBJECT_0 {
                    let mut kod: u32 = 0;
                    unsafe {
                        GetExitCodeProcess(self.process_handle, &mut kod);
                        CloseHandle(self.process_handle);
                    }
                    self.process_handle = 0 as HANDLE;
                    self.returncode = Some(kod);
                }
            }
            self.returncode
        }

        pub fn wait(&mut self, timeout_secs: Option<f64>) -> Result<Option<u32>, String> {
            if self.returncode.is_none() && self.process_handle != INVALID_HANDLE_VALUE && self.process_handle != (0 as HANDLE) {
                let ms = match timeout_secs {
                    None => 0xFFFF_FFFFu32,
                    Some(t) => (t.max(0.0) * 1000.0) as u32,
                };
                let res = unsafe { WaitForSingleObject(self.process_handle, ms) };
                if res != WAIT_OBJECT_0 {
                    return Err("Chrome kapanma zaman aşımı".to_string());
                }
            }
            Ok(self.poll())
        }

        pub fn terminate(&mut self) {
            if self.returncode.is_none() && self.process_handle != INVALID_HANDLE_VALUE && self.process_handle != (0 as HANDLE) {
                unsafe {
                    TerminateProcess(self.process_handle, 1);
                }
            }
        }
    }

    impl Drop for ChromeSureci {
        fn drop(&mut self) {
            if self.process_handle != INVALID_HANDLE_VALUE && self.process_handle != (0 as HANDLE) {
                unsafe {
                    CloseHandle(self.process_handle);
                }
                self.process_handle = 0 as HANDLE;
            }
        }
    }
}

#[cfg(not(windows))]
mod win_chrome {
    use super::*;
    pub struct ChromeSureci {
        pub pid: u32,
        pub returncode: Option<u32>,
        pub oku_file: Option<File>,
        pub yaz_file: Option<File>,
    }
    impl ChromeSureci {
        pub fn spawn(_komut: &[String], _creationflags: u32) -> Result<Self, String> {
            Err("PTT sorgusu yalnız Windows'ta destekleniyor.".to_string())
        }
        pub fn poll(&mut self) -> Option<u32> {
            self.returncode
        }
        pub fn wait(&mut self, _timeout_secs: Option<f64>) -> Result<Option<u32>, String> {
            Ok(self.returncode)
        }
        pub fn terminate(&mut self) {}
    }
}

use win_chrome::ChromeSureci;

struct BoruBaglantisi {
    yaz_file: Arc<StdMutex<Option<File>>>,
    kuyruk: AsyncMutex<mpsc::UnboundedReceiver<Option<String>>>,
}

impl BoruBaglantisi {
    fn new(mut oku_file: File, yaz_file: File) -> Self {
        let (tx, rx) = mpsc::unbounded_channel();
        std::thread::Builder::new()
            .name("ptt-cdp-boru".to_string())
            .spawn(move || {
                let mut tampon = Vec::new();
                let mut buf = [0u8; 65536];
                loop {
                    match oku_file.read(&mut buf) {
                        Ok(0) | Err(_) => break,
                        Ok(n) => {
                            tampon.extend_from_slice(&buf[..n]);
                            while let Some(pos) = tampon.iter().position(|&b| b == 0) {
                                let ileti = String::from_utf8_lossy(&tampon[..pos]).into_owned();
                                tampon.drain(..=pos);
                                if tx.send(Some(ileti)).is_err() {
                                    return;
                                }
                            }
                        }
                    }
                }
                let _ = tx.send(None);
            })
            .ok();

        Self {
            yaz_file: Arc::new(StdMutex::new(Some(yaz_file))),
            kuyruk: AsyncMutex::new(rx),
        }
    }

    async fn next_msg(&self) -> Option<String> {
        let mut rx = self.kuyruk.lock().await;
        rx.recv().await.flatten()
    }

    async fn send(&self, metin: String) -> Result<(), String> {
        let yaz_arc = self.yaz_file.clone();
        tokio::task::spawn_blocking(move || {
            let mut guard = yaz_arc.lock().map_err(|_| "Kilit hatası".to_string())?;
            let file = guard
                .as_mut()
                .ok_or_else(|| "Chrome bağlantısı kapandı".to_string())?;
            let mut data = metin.into_bytes();
            data.push(0);
            file.write_all(&data)
                .map_err(|e| format!("Chrome boru yazma hatası: {}", e))?;
            file.flush().ok();
            Ok(())
        })
        .await
        .map_err(|e| e.to_string())?
    }

    fn close(&self) {
        if let Ok(mut guard) = self.yaz_file.lock() {
            guard.take();
        }
    }
}

type EventListener = Arc<dyn Fn(&str, &Value) + Send + Sync>;

struct Cdp {
    ws: Arc<BoruBaglantisi>,
    oturum: Arc<StdMutex<Option<String>>>,
    sira: AtomicUsize,
    bekleyen: Arc<StdMutex<HashMap<usize, oneshot::Sender<Value>>>>,
    dinleyiciler: Arc<StdMutex<Vec<(usize, EventListener)>>>,
    next_listener_id: AtomicUsize,
    okuyucu_handle: tokio::task::JoinHandle<()>,
}

impl Cdp {
    fn new(ws: Arc<BoruBaglantisi>) -> Self {
        let bekleyen = Arc::new(StdMutex::new(HashMap::<usize, oneshot::Sender<Value>>::new()));
        let dinleyiciler = Arc::new(StdMutex::new(Vec::<(usize, EventListener)>::new()));
        let oturum = Arc::new(StdMutex::new(None::<String>));

        let ws_clone = ws.clone();
        let bekleyen_clone = bekleyen.clone();
        let dinleyiciler_clone = dinleyiciler.clone();
        let oturum_clone = oturum.clone();

        let okuyucu_handle = tokio::spawn(async move {
            while let Some(ham) = ws_clone.next_msg().await {
                let Ok(m) = serde_json::from_str::<Value>(&ham) else {
                    continue;
                };
                if let Some(id) = m.get("id").and_then(|v| v.as_u64()).map(|v| v as usize) {
                    if let Ok(mut map) = bekleyen_clone.lock() {
                        if let Some(tx) = map.remove(&id) {
                            let _ = tx.send(m);
                        }
                    }
                } else {
                    let cur_oturum = oturum_clone.lock().ok().and_then(|g| g.clone());
                    let msg_oturum = m.get("sessionId").and_then(|v| v.as_str()).map(String::from);
                    if msg_oturum == cur_oturum {
                        let method = m.get("method").and_then(|v| v.as_str()).unwrap_or("");
                        let params = m.get("params").cloned().unwrap_or_else(|| json!({}));
                        let list = dinleyiciler_clone
                            .lock()
                            .map(|g| g.clone())
                            .unwrap_or_default();
                        for (_, cb) in list {
                            cb(method, &params);
                        }
                    }
                }
            }
            if let Ok(mut map) = bekleyen_clone.lock() {
                map.clear();
            }
        });

        Self {
            ws,
            oturum,
            sira: AtomicUsize::new(0),
            bekleyen,
            dinleyiciler,
            next_listener_id: AtomicUsize::new(1),
            okuyucu_handle,
        }
    }

    fn set_oturum(&self, s: Option<String>) {
        if let Ok(mut g) = self.oturum.lock() {
            *g = s;
        }
    }

    fn add_listener(&self, cb: EventListener) -> usize {
        let id = self.next_listener_id.fetch_add(1, Ordering::SeqCst);
        if let Ok(mut g) = self.dinleyiciler.lock() {
            g.push((id, cb));
        }
        id
    }

    fn remove_listener(&self, id: usize) {
        if let Ok(mut g) = self.dinleyiciler.lock() {
            g.retain(|(lid, _)| *lid != id);
        }
    }

    async fn cmd(
        &self,
        method: &str,
        params: Option<Value>,
        timeout_secs: f64,
        tarayici: bool,
    ) -> Result<Value, String> {
        let id = self.sira.fetch_add(1, Ordering::SeqCst) + 1;
        let (tx, rx) = oneshot::channel();
        {
            let mut map = self.bekleyen.lock().map_err(|_| "Kilit hatası".to_string())?;
            map.insert(id, tx);
        }
        let mut ileti = json!({
            "id": id,
            "method": method,
            "params": params.unwrap_or_else(|| json!({})),
        });
        if !tarayici {
            if let Ok(g) = self.oturum.lock() {
                if let Some(ref s) = *g {
                    ileti["sessionId"] = Value::String(s.clone());
                }
            }
        }
        let metin = serde_json::to_string(&ileti).map_err(|e| e.to_string())?;
        if let Err(e) = self.ws.send(metin).await {
            if let Ok(mut map) = self.bekleyen.lock() {
                map.remove(&id);
            }
            return Err(e);
        }

        let m = match tokio::time::timeout(Duration::from_secs_f64(timeout_secs.max(0.1)), rx).await
        {
            Ok(Ok(val)) => val,
            Ok(Err(_)) => return Err("Chrome bağlantısı kapandı".to_string()),
            Err(_) => {
                if let Ok(mut map) = self.bekleyen.lock() {
                    map.remove(&id);
                }
                return Err(format!("{} zaman aşımı", method));
            }
        };

        if let Some(err) = m.get("error") {
            let msg = err
                .get("message")
                .and_then(|v| v.as_str())
                .unwrap_or("bilinmeyen CDP hatası");
            return Err(format!("{}: {}", method, msg));
        }
        Ok(m.get("result").cloned().unwrap_or_else(|| json!({})))
    }

    async fn degerlendir(&self, ifade: &str) -> Result<Value, String> {
        let r = self
            .cmd(
                "Runtime.evaluate",
                Some(json!({"expression": ifade, "returnByValue": true})),
                15.0,
                false,
            )
            .await?;
        Ok(r.get("result")
            .and_then(|v| v.get("value"))
            .cloned()
            .unwrap_or(Value::Null))
    }

    fn canli(&self) -> bool {
        !self.okuyucu_handle.is_finished()
    }

    async fn kapat(&self) {
        self.okuyucu_handle.abort();
        self.ws.close();
    }
}

struct PttTarayici {
    proc: Option<ChromeSureci>,
    cdp: Option<Arc<Cdp>>,
    gecici_profil: Option<PathBuf>,
    son_sorgu: f64,
}

impl PttTarayici {
    fn new() -> Self {
        Self {
            proc: None,
            cdp: None,
            gecici_profil: None,
            son_sorgu: 0.0,
        }
    }

    async fn baslat(&mut self, profil: &Path) -> Result<(), String> {
        let chrome = _chrome_exe_bul()
            .ok_or_else(|| "Google Chrome bulunamadı (PTT sorgusu için gerekli).".to_string())?;
        let mut komut = vec![
            chrome,
            "--remote-debugging-pipe".to_string(),
            format!("--user-data-dir={}", profil.to_string_lossy()),
            "--no-first-run".to_string(),
            "--no-default-browser-check".to_string(),
            "--no-service-autorun".to_string(),
            "--password-store=basic".to_string(),
            "--lang=tr-TR".to_string(),
            "--window-size=1366,900".to_string(),
            "--disable-blink-features=AutomationControlled".to_string(),
            "about:blank".to_string(),
        ];
        let gorunur = std::env::var("UYAP_PTT_GORUNUR")
            .unwrap_or_default()
            .trim()
            .to_lowercase();
        if !matches!(gorunur.as_str(), "1" | "true" | "yes" | "on") {
            let pos = komut.len() - 1;
            komut.insert(pos, "--window-position=-32000,-32000".to_string());
        }

        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let mut surec = ChromeSureci::spawn(&komut, CREATE_NO_WINDOW)?;
        let oku_file = surec
            .oku_file
            .take()
            .ok_or_else(|| "Boru okuma ucu eksik".to_string())?;
        let yaz_file = surec
            .yaz_file
            .take()
            .ok_or_else(|| "Boru yazma ucu eksik".to_string())?;
        self.proc = Some(surec);

        let boru = Arc::new(BoruBaglantisi::new(oku_file, yaz_file));
        let cdp = Arc::new(Cdp::new(boru));
        self.cdp = Some(cdp.clone());

        let mut sayfa: Option<Value> = None;
        for _ in 0..20 {
            match cdp.cmd("Target.getTargets", None, 10.0, false).await {
                Ok(res) => {
                    if let Some(arr) = res.get("targetInfos").and_then(|v| v.as_array()) {
                        if let Some(found) = arr
                            .iter()
                            .find(|h| h.get("type").and_then(|v| v.as_str()) == Some("page"))
                        {
                            sayfa = Some(found.clone());
                            break;
                        }
                    }
                }
                Err(e) => {
                    if let Some(ref mut p) = self.proc {
                        if p.poll().is_some() {
                            return Err(format!(
                                "Chrome hemen kapandı (profil kullanımda olabilir): {}",
                                e
                            ));
                        }
                    }
                    return Err(format!("Chrome'a bağlanılamadı: {}", e));
                }
            }
            tokio::time::sleep(Duration::from_millis(500)).await;
        }

        let sayfa = match sayfa {
            Some(s) => s,
            None => cdp
                .cmd("Target.createTarget", Some(json!({"url": "about:blank"})), 15.0, false)
                .await
                .map_err(|e| format!("Chrome'a bağlanılamadı: {}", e))?,
        };
        let target_id = sayfa
            .get("targetId")
            .and_then(|v| v.as_str())
            .ok_or_else(|| "targetId alınamadı".to_string())?;

        let r = cdp
            .cmd(
                "Target.attachToTarget",
                Some(json!({"targetId": target_id, "flatten": true})),
                15.0,
                false,
            )
            .await
            .map_err(|e| format!("Chrome'a bağlanılamadı: {}", e))?;
        let session_id = r
            .get("sessionId")
            .and_then(|v| v.as_str())
            .ok_or_else(|| "sessionId alınamadı".to_string())?
            .to_string();
        cdp.set_oturum(Some(session_id));

        for m in [
            "Page.enable",
            "Network.enable",
            "Runtime.enable",
            "Page.bringToFront",
        ] {
            cdp.cmd(m, None, 15.0, false).await?;
        }
        cdp.cmd(
            "Emulation.setFocusEmulationEnabled",
            Some(json!({"enabled": true})),
            15.0,
            false,
        )
        .await?;
        Ok(())
    }

    async fn ac(&mut self) -> Result<(), String> {
        let alive = self.cdp.as_ref().map(|c| c.canli()).unwrap_or(false)
            && self.proc.as_mut().map(|p| p.poll().is_none()).unwrap_or(false);
        if alive {
            return Ok(());
        }
        self.kapat().await;
        let profil = _profil_klasoru();
        if self.baslat(&profil).await.is_err() {
            self.kapat().await;
            let tmp = std::env::temp_dir().join(format!(
                "cokluuyap_ptt_{}_{}",
                std::process::id(),
                rand::thread_rng().gen::<u32>()
            ));
            let _ = std::fs::create_dir_all(&tmp);
            self.gecici_profil = Some(tmp.clone());
            self.baslat(&tmp).await?;
        }
        Ok(())
    }

    async fn kapat(&mut self) {
        if let Some(cdp) = self.cdp.take() {
            if cdp.canli() {
                let _ = cdp.cmd("Browser.close", None, 3.0, true).await;
            }
            cdp.kapat().await;
        }
        if let Some(mut proc) = self.proc.take() {
            proc.terminate();
            let _ = tokio::task::spawn_blocking(move || {
                let _ = proc.wait(Some(5.0));
            })
            .await;
        }
        if let Some(tmp) = self.gecici_profil.take() {
            let _ = std::fs::remove_dir_all(tmp);
        }
    }

    async fn sayfayi_hazirla(&self) -> Result<(f64, f64), String> {
        let cdp = self.cdp.as_ref().ok_or("CDP yok")?;
        cdp.cmd("Page.navigate", Some(json!({"url": SITE_URL})), 15.0, false)
            .await?;
        let son = now_secs() + SAYFA_ZAMAN_ASIMI_SN;
        while now_secs() < son {
            tokio::time::sleep(Duration::from_millis(500)).await;
            if let Ok(konum) = cdp.degerlendir(_KUTU_JS).await {
                if let (Some(x), Some(y)) = (
                    konum.get("x").and_then(|v| v.as_f64()),
                    konum.get("y").and_then(|v| v.as_f64()),
                ) {
                    let wait_ms = rand::thread_rng().gen_range(2000..=3000);
                    tokio::time::sleep(Duration::from_millis(wait_ms)).await;
                    return Ok((x, y));
                }
            }
        }
        Err("ptt.gov.tr'de gönderi takip kutusu bulunamadı".to_string())
    }

    async fn tikla(&self, x: f64, y: f64) -> Result<(), String> {
        let cdp = self.cdp.as_ref().ok_or("CDP yok")?;
        for tur in ["mouseMoved", "mousePressed", "mouseReleased"] {
            let mut p = json!({"type": tur, "x": x, "y": y});
            if tur != "mouseMoved" {
                p["button"] = Value::String("left".to_string());
                p["clickCount"] = json!(1);
            }
            cdp.cmd("Input.dispatchMouseEvent", Some(p), 15.0, false)
                .await?;
            let wait_ms = rand::thread_rng().gen_range(40..=120);
            tokio::time::sleep(Duration::from_millis(wait_ms)).await;
        }
        Ok(())
    }

    async fn sorgula_ic(&mut self, barkod: &str) -> Result<Value, String> {
        let jitter = rand::thread_rng().gen_range(0.7..=1.3);
        let bekle = BEKLEME_SN * jitter - (now_secs() - self.son_sorgu);
        if self.son_sorgu > 0.0 && bekle > 0.0 {
            tokio::time::sleep(Duration::from_secs_f64(bekle)).await;
        }
        self.ac().await?;
        self.sayfayi_hazirla().await?;

        let cdp = self.cdp.as_ref().ok_or("CDP yok")?.clone();
        cdp.degerlendir(
            "(() => { const k = [...document.querySelectorAll('input[placeholder=\"Gönderi takip no giriniz\"], input[name=\"value\"]')].find(e => e.offsetParent !== null); k.focus(); k.select(); return 1; })()",
        )
        .await?;

        for ch in barkod.chars() {
            cdp.cmd(
                "Input.insertText",
                Some(json!({"text": ch.to_string()})),
                15.0,
                false,
            )
            .await?;
            let wait_ms = rand::thread_rng().gen_range(60..=140);
            tokio::time::sleep(Duration::from_millis(wait_ms)).await;
        }

        let konum = cdp.degerlendir(_KUTU_JS).await?;
        let (x, y) = match (
            konum.get("x").and_then(|v| v.as_f64()),
            konum.get("y").and_then(|v| v.as_f64()),
        ) {
            (Some(x), Some(y)) => (x, y),
            _ => return Err("SORGULA düğmesi bulunamadı".to_string()),
        };

        let istekler = Arc::new(StdMutex::new(HashSet::<String>::new()));
        let yanitlar = Arc::new(StdMutex::new(HashMap::<String, u16>::new()));
        let (done_tx, done_rx) = oneshot::channel::<Result<String, String>>();
        let done_tx = Arc::new(StdMutex::new(Some(done_tx)));

        let istekler_c = istekler.clone();
        let yanitlar_c = yanitlar.clone();
        let done_tx_c = done_tx.clone();

        let listener_id = cdp.add_listener(Arc::new(move |method, p| {
            let rid = p
                .get("requestId")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            if rid.is_empty() {
                return;
            }
            if method == "Network.requestWillBeSent" {
                let req = p.get("request").and_then(|v| v.as_object());
                let url = req
                    .and_then(|r| r.get("url"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let m = req
                    .and_then(|r| r.get("method"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                if url.contains(TAKIP_YOLU) && m == "POST" {
                    if let Ok(mut s) = istekler_c.lock() {
                        s.insert(rid);
                    }
                }
            } else if method == "Network.responseReceived" {
                let tracked = istekler_c.lock().map(|s| s.contains(&rid)).unwrap_or(false);
                if tracked {
                    let st = p
                        .get("response")
                        .and_then(|r| r.get("status"))
                        .and_then(|v| v.as_u64())
                        .unwrap_or(0) as u16;
                    if let Ok(mut y) = yanitlar_c.lock() {
                        y.insert(rid, st);
                    }
                }
            } else if method == "Network.loadingFinished" {
                let has_resp = yanitlar_c.lock().map(|y| y.contains_key(&rid)).unwrap_or(false);
                if has_resp {
                    if let Ok(mut tx) = done_tx_c.lock() {
                        if let Some(sender) = tx.take() {
                            let _ = sender.send(Ok(rid));
                        }
                    }
                }
            } else if method == "Network.loadingFailed" {
                let tracked = istekler_c.lock().map(|s| s.contains(&rid)).unwrap_or(false);
                if tracked {
                    let err_txt = p
                        .get("errorText")
                        .and_then(|v| v.as_str())
                        .unwrap_or("istek başarısız")
                        .to_string();
                    if let Ok(mut tx) = done_tx_c.lock() {
                        if let Some(sender) = tx.take() {
                            let _ = sender.send(Err(err_txt));
                        }
                    }
                }
            }
        }));

        let click_res = self.tikla(x, y).await;
        let rid_res = if let Err(e) = click_res {
            Err(e)
        } else {
            match tokio::time::timeout(Duration::from_secs_f64(ZAMAN_ASIMI_SN), done_rx).await {
                Ok(Ok(r)) => r,
                Ok(Err(_)) => Err("Chrome bağlantısı kapandı".to_string()),
                Err(_) => {
                    let metin = cdp
                        .degerlendir("document.body ? document.body.innerText : ''")
                        .await
                        .ok()
                        .and_then(|v| v.as_str().map(String::from))
                        .unwrap_or_default();
                    if metin.contains(DOGRULAMA_HATASI) {
                        Err("PTT doğrulaması tamamlanamadı; biraz sonra yeniden deneyin".to_string())
                    } else {
                        Err("PTT yanıt vermedi".to_string())
                    }
                }
            }
        };

        cdp.remove_listener(listener_id);
        let rid = rid_res?;

        let status = yanitlar.lock().ok().and_then(|y| y.get(&rid).copied());
        if status != Some(200) {
            return Err(format!("PTT HTTP {:?}", status));
        }

        let govde = cdp
            .cmd(
                "Network.getResponseBody",
                Some(json!({"requestId": rid})),
                15.0,
                false,
            )
            .await?;
        let mut ham = govde
            .get("body")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if govde
            .get("base64Encoded")
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
        {
            let bytes = base64::engine::general_purpose::STANDARD
                .decode(ham.as_bytes())
                .map_err(|e| format!("base64 çözme hatası: {}", e))?;
            ham = String::from_utf8_lossy(&bytes).into_owned();
        }
        let mut veri: Value =
            serde_json::from_str(&ham).map_err(|e| format!("PTT JSON hatası: {}", e))?;
        if let Some(arr) = veri.as_array() {
            if let Some(first) = arr.first() {
                veri = first.clone();
            }
        }
        if !veri.is_object() {
            return Err("PTT beklenmeyen yanıt biçimi".to_string());
        }
        Ok(veri)
    }
}

static KILIT: Lazy<AsyncMutex<PttTarayici>> = Lazy::new(|| AsyncMutex::new(PttTarayici::new()));
static BEKLEYEN: AtomicUsize = AtomicUsize::new(0);

// ── Yanıtın özeti ───────────────────────────────────────────────────────────

pub fn _tr_kucuk(s: &str) -> String {
    let lower = s.replace('I', "ı").replace('İ', "i").to_lowercase();
    lower
        .chars()
        .map(|c| match c {
            'ç' => 'c',
            'ğ' => 'g',
            'ı' => 'i',
            'ö' => 'o',
            'ş' => 's',
            'ü' => 'u',
            _ => c,
        })
        .collect()
}

pub fn _tarih_bicimle(ham: &str) -> String {
    let s = ham.trim();
    if s.len() == 8 && s.chars().all(|c| c.is_ascii_digit()) {
        format!("{}/{}/{}", &s[6..8], &s[4..6], &s[0..4])
    } else {
        s.to_string()
    }
}

pub const _TESLIM_SEKILLERI: &[(&str, &str)] = &[
    ("21/2", "Muhtara teslim (21/2 – MERNİS adresi)"),
    ("ihtiyar heyeti", "İhtiyar heyeti/azaya teslim (21. md.)"),
    ("muhtara", "Muhtara teslim (21. md.)"),
    ("bizzat", "Muhataba bizzat teslim"),
    ("ayni konutta yakina", "Aynı konutta yakına teslim"),
    ("daimi calisana", "İşyerinde daimi çalışana teslim"),
    ("amirine", "İşyerindeki amirine teslim"),
    ("kapisina yapistirma", "Kapıya yapıştırma (35. md.)"),
];

pub const _ARA_HAREKETLER: &[&str] = &[
    "gondericisine teslim edildi",
    "koy dagitimina tabi bekliyor",
];

static DETAY_TEMIZLE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"\s+/(\s+.*)?$").expect("detay temizle regex"));

pub fn _detay_temizle(s: &str) -> String {
    DETAY_TEMIZLE_RE.replace(s.trim(), "").trim().to_string()
}

fn val_str(v: Option<&Value>) -> String {
    match v {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) => n.to_string(),
        Some(Value::Bool(b)) => b.to_string(),
        _ => String::new(),
    }
}

/// PTT ham yanıtını ekranda gösterilecek biçime çevirir.
pub fn ozet(veri: &Value) -> Value {
    let kayit = veri
        .get("errorState")
        .map(|v| match v {
            Value::Bool(b) => *b,
            Value::Number(n) => n.as_i64().unwrap_or(0) != 0,
            Value::String(s) => !s.is_empty(),
            _ => false,
        })
        .unwrap_or(false);

    if !kayit {
        let msg = val_str(veri.get("errorMessage"));
        return json!({
            "kayit": false,
            "durum": if msg.is_empty() { "Kayıt yok".to_string() } else { msg },
            "tarih": "",
            "teslim_sekli": "",
            "hareketler": [],
            "kabul": {},
        });
    }

    let son = veri.get("sondurum").and_then(|v| v.as_object());
    let hareket: Vec<&serde_json::Map<String, Value>> = veri
        .get("hareketDongu")
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().filter_map(|h| h.as_object()).collect())
        .unwrap_or_default();

    let mut durum_str = String::new();
    let mut tarih_str = String::new();

    if let Some(son_obj) = son {
        let aciklama = val_str(son_obj.get("son_durum_aciklama"));
        if !aciklama.is_empty() {
            let mut t = val_str(son_obj.get("teslim_tarihi"));
            if t.is_empty() || t.trim() == "0" {
                t = val_str(son_obj.get("son_islem_tarihi"));
            }
            durum_str = aciklama;
            tarih_str = _tarih_bicimle(&t);
        }
    }
    if durum_str.is_empty() {
        if let Some(h) = hareket.last() {
            let aciklama = val_str(h.get("aciklama"));
            durum_str = if !aciklama.is_empty() {
                aciklama
            } else {
                let em = val_str(veri.get("errorMessage"));
                if em.is_empty() {
                    "Bilinmeyen durum".to_string()
                } else {
                    em
                }
            };
            tarih_str = format!("{} {}", val_str(h.get("tarih")), val_str(h.get("saat")))
                .trim()
                .to_string();
        }
    }

    let mut teslim_sekli = String::new();
    let durum_norm = _tr_kucuk(&durum_str);
    let teslim = durum_norm.contains("teslim edildi");
    let iade = durum_norm.contains("iade");
    if teslim || iade {
        let detaylar: Vec<String> = hareket
            .iter()
            .map(|h| _detay_temizle(&val_str(h.get("islem_detay"))))
            .filter(|d| !d.is_empty() && !_ARA_HAREKETLER.contains(&_tr_kucuk(d).as_str()))
            .collect();
        if teslim {
            'outer: for d in detaylar.iter().rev() {
                let d_norm = _tr_kucuk(d);
                for (a, e) in _TESLIM_SEKILLERI {
                    if d_norm.contains(a) {
                        teslim_sekli = (*e).to_string();
                        break 'outer;
                    }
                }
            }
        }
        if teslim_sekli.is_empty() {
            if let Some(last) = detaylar.last() {
                teslim_sekli = last.clone();
            }
        }
    }

    let mut hareketler_out = Vec::with_capacity(hareket.len());
    for h in &hareket {
        let yer_parcalar: Vec<String> = ["islem_yeri", "merkez", "birim", "ilce", "il"]
            .iter()
            .filter_map(|k| {
                let s = val_str(h.get(*k)).trim().to_string();
                if s.is_empty() { None } else { Some(s) }
            })
            .collect();
        hareketler_out.push(json!({
            "tarih": format!("{} {}", val_str(h.get("tarih")), val_str(h.get("saat"))).trim(),
            "aciklama": val_str(h.get("aciklama")),
            "yer": yer_parcalar.join(" / "),
            "detay": _detay_temizle(&val_str(h.get("islem_detay"))),
        }));
    }

    let k = veri.get("kabul").and_then(|v| v.as_object());
    let kabul = json!({
        "tarih": _tarih_bicimle(&k.map(|m| val_str(m.get("kabul_tarihi"))).unwrap_or_default()),
        "gonderici": k.map(|m| val_str(m.get("gonderici"))).unwrap_or_default(),
        "alici": k.map(|m| val_str(m.get("alici"))).unwrap_or_default(),
    });

    json!({
        "kayit": true,
        "durum": durum_str,
        "tarih": tarih_str,
        "teslim_sekli": teslim_sekli,
        "hareketler": hareketler_out,
        "kabul": kabul,
    })
}

/// `{ok, barkod, ...ozet}` | `{ok: false, error}`.
pub async fn sorgula(barkod: &str) -> Value {
    let temiz: String = barkod.chars().filter(|c| c.is_ascii_digit()).collect();
    if !BARKOD_RE.is_match(&temiz) {
        return json!({"ok": false, "error": "Geçersiz barkod (13 haneli olmalı)."});
    }

    let prev = BEKLEYEN
        .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |v| {
            if v >= BEKLEYEN_AZAMI {
                None
            } else {
                Some(v + 1)
            }
        });
    if prev.is_err() {
        return json!({
            "ok": false,
            "barkod": temiz,
            "error": "Sırada başka PTT sorguları var; biraz sonra yeniden deneyin"
        });
    }

    let mut tarayici = KILIT.lock().await;
    BEKLEYEN.fetch_sub(1, Ordering::SeqCst);

    let res = tarayici.sorgula_ic(&temiz).await;
    tarayici.son_sorgu = now_secs();
    tarayici.kapat().await;
    drop(tarayici);

    match res {
        Ok(veri) => {
            let mut out = ozet(&veri);
            if let Some(obj) = out.as_object_mut() {
                obj.insert("ok".to_string(), Value::Bool(true));
                obj.insert("barkod".to_string(), Value::String(temiz));
            }
            out
        }
        Err(e) => json!({
            "ok": false,
            "barkod": temiz,
            "error": e,
        }),
    }
}
