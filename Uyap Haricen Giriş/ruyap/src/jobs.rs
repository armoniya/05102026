//! Ofis iş kuyruğu (`uyap_core/jobs.py` 1:1 portu)
//! ==================================================
//! Tarayıcıdaki veya otomasyondaki uzun süreli toplu işleri ofis tarafında
//! arka planda çalıştırır, durum/log akışı sunar ve `runtime::batch_write()`
//! ile bireysel kullanıcı istekleriyle çakışmadan UYAP'a yazar.

use std::collections::{BTreeMap, HashMap};
use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex as StdMutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use once_cell::sync::Lazy;
use rand::RngCore;
use serde_json::{json, Value};
use tokio::sync::{Mutex as AsyncMutex, Notify};

use crate::p2p_wire::AGENT_PREFIX;
use crate::runtime;
use crate::udf_signer;
use crate::uyap_proxy::{self, _looks_like_login_wall, UYAP_BASE};

pub const JOBS_PREFIX: &str = "/__uyap_agent__/jobs";
pub const SIGN_UDF_PATH: &str = "/__uyap_agent__/sign_udf";
pub const TUM_EVRAK_PREFIX: &str = "/__uyap_agent__/tum_evrak";
pub const GEREKCELI_PREFIX: &str = "/__uyap_agent__/gerekceli_karar";
pub const PTT_SORGU_PATH: &str = "/__uyap_agent__/ptt_sorgu";

pub const QUEUED: &str = "queued";
pub const RUNNING: &str = "running";
pub const AWAITING_APPROVAL: &str = "awaiting_approval";
pub const DONE: &str = "done";
pub const ERROR: &str = "error";
pub const CANCELLED: &str = "cancelled";

pub const _MAX_LOG_LINES: usize = 500;
pub const _MAX_STORED_JOBS: usize = 50;

pub fn toplu_is_acik() -> bool {
    let val = std::env::var("UYAP_TOPLU_IS_ACIK")
        .unwrap_or_else(|_| "1".to_string())
        .trim()
        .to_lowercase();
    !matches!(val.as_str(), "0" | "false" | "no" | "off")
}

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

fn new_job_id() -> String {
    let mut b = [0u8; 6];
    rand::thread_rng().fill_bytes(&mut b);
    hex::encode(b)
}

#[derive(Debug, Clone)]
pub enum JobError {
    Cancelled(String),
    Failed(String),
}

impl std::fmt::Display for JobError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            JobError::Cancelled(s) => write!(f, "{}", s),
            JobError::Failed(s) => write!(f, "{}", s),
        }
    }
}

impl std::error::Error for JobError {}

impl From<String> for JobError {
    fn from(s: String) -> Self {
        JobError::Failed(s)
    }
}

impl From<&str> for JobError {
    fn from(s: &str) -> Self {
        JobError::Failed(s.to_string())
    }
}

#[derive(Debug, Clone)]
pub struct UyapResponse {
    pub status_code: u16,
    pub headers: HashMap<String, String>,
    pub body: Vec<u8>,
    pub text: String,
}

#[derive(Debug, Clone)]
pub struct JobInner {
    pub id: String,
    pub job_type: String,
    pub params: Value,
    pub owner: String,
    pub status: String,
    pub created_at: f64,
    pub started_at: Option<f64>,
    pub finished_at: Option<f64>,
    pub progress_done: i64,
    pub progress_total: i64,
    pub progress_label: String,
    pub logs: Vec<String>,
    pub result: Option<Value>,
    pub error: Option<String>,
    pub pending_approval: Option<Value>,
    pub approval_response: Option<(bool, String)>,
}

pub struct Job {
    pub inner: StdMutex<JobInner>,
    pub cancel_flag: AtomicBool,
    pub approval_notify: Notify,
}

impl Job {
    pub fn new(id: String, job_type: String, params: Value, owner: String) -> Arc<Self> {
        Arc::new(Self {
            inner: StdMutex::new(JobInner {
                id,
                job_type,
                params,
                owner,
                status: QUEUED.to_string(),
                created_at: now_secs(),
                started_at: None,
                finished_at: None,
                progress_done: 0,
                progress_total: 0,
                progress_label: String::new(),
                logs: Vec::new(),
                result: None,
                error: None,
                pending_approval: None,
                approval_response: None,
            }),
            cancel_flag: AtomicBool::new(false),
            approval_notify: Notify::new(),
        })
    }

    pub fn to_dict(&self, since_log: usize) -> Value {
        let g = self.inner.lock().unwrap();
        let logs_slice = if since_log < g.logs.len() {
            &g.logs[since_log..]
        } else {
            &[]
        };
        json!({
            "id": g.id,
            "type": g.job_type,
            "owner": g.owner,
            "status": g.status,
            "created_at": g.created_at,
            "started_at": g.started_at,
            "finished_at": g.finished_at,
            "progress": {
                "done": g.progress_done,
                "total": g.progress_total,
                "label": g.progress_label,
            },
            "log_offset": g.logs.len(),
            "logs": logs_slice,
            "result": g.result,
            "error": g.error,
            "pending_approval": g.pending_approval,
        })
    }
}

#[derive(Clone)]
pub struct JobContext {
    pub job: Arc<Job>,
}

impl JobContext {
    pub fn new(job: Arc<Job>) -> Self {
        Self { job }
    }

    pub fn check_cancel(&self) -> Result<(), JobError> {
        if self.job.cancel_flag.load(Ordering::SeqCst) {
            return Err(JobError::Cancelled("Kullanıcı tarafından iptal edildi.".to_string()));
        }
        Ok(())
    }

    pub fn log(&self, msg: &str) {
        let now = chrono::Local::now().format("%H:%M:%S");
        let line = format!("[{}] {}", now, msg);
        let mut g = self.job.inner.lock().unwrap();
        g.logs.push(line);
        if g.logs.len() > _MAX_LOG_LINES {
            let excess = g.logs.len() - _MAX_LOG_LINES;
            g.logs.drain(0..excess);
        }
    }

    pub fn progress(&self, done: i64, total: i64, label: &str) {
        let mut g = self.job.inner.lock().unwrap();
        g.progress_done = done;
        g.progress_total = total;
        g.progress_label = label.to_string();
    }

    pub async fn request_approval(
        &self,
        prompt: &str,
        payload: Option<Value>,
    ) -> Result<(bool, String), JobError> {
        self.check_cancel()?;
        {
            let mut g = self.job.inner.lock().unwrap();
            g.approval_response = None;
            g.pending_approval = Some(json!({
                "prompt": prompt,
                "payload": payload.unwrap_or_else(|| json!({})),
            }));
            g.status = AWAITING_APPROVAL.to_string();
        }
        self.log(&format!("⏳ Onay bekleniyor: {}", prompt));

        loop {
            self.check_cancel()?;
            {
                let mut g = self.job.inner.lock().unwrap();
                if let Some(resp) = g.approval_response.take() {
                    g.pending_approval = None;
                    g.status = RUNNING.to_string();
                    return Ok(resp);
                }
            }
            self.job.approval_notify.notified().await;
        }
    }

    pub async fn uyap(
        &self,
        method: &str,
        path: &str,
        query: Option<&str>,
        json_body: Option<&Value>,
        data_body: Option<&[(String, String)]>,
        raw_body: Option<&[u8]>,
        headers: Option<&HashMap<String, String>>,
    ) -> Result<UyapResponse, JobError> {
        self.check_cancel()?;
        let gw = uyap_proxy::get_gw()
            .ok_or_else(|| JobError::Failed("Ofis UYAP oturumu henüz hazır değil.".to_string()))?;

        let m = method.trim().to_uppercase();
        let is_write = runtime::is_write_method(&m);
        let clean_path = path.trim_start_matches('/');
        let url = match query {
            Some(q) if !q.is_empty() => format!("{}/{}?{}", UYAP_BASE, clean_path, q),
            _ => format!("{}/{}", UYAP_BASE, clean_path),
        };

        let do_once = || async {
            gw.ensure_alive().await.map_err(JobError::Failed)?;
            let req_method =
                reqwest::Method::from_bytes(m.as_bytes()).unwrap_or(reqwest::Method::GET);
            let mut rb = gw.client.request(req_method, &url);
            rb = rb.header("Referer", format!("{}/", UYAP_BASE));
            if let Some(hdrs) = headers {
                for (k, v) in hdrs {
                    rb = rb.header(k.as_str(), v.as_str());
                }
            }
            if let Some(jb) = json_body {
                rb = rb.json(jb);
            } else if let Some(db) = data_body {
                rb = rb.form(db);
            } else if let Some(raw) = raw_body {
                rb = rb.body(raw.to_vec());
            }

            let resp = rb
                .send()
                .await
                .map_err(|e| JobError::Failed(format!("UYAP isteği hatası ({}): {}", path, e)))?;
            let status = resp.status().as_u16();
            let resp_headers_raw = resp.headers().clone();
            let mut resp_headers = HashMap::new();
            for (k, v) in &resp_headers_raw {
                if let Ok(vs) = v.to_str() {
                    resp_headers.insert(k.as_str().to_ascii_lowercase(), vs.to_string());
                }
            }
            let bytes = resp
                .bytes()
                .await
                .map_err(|e| JobError::Failed(format!("UYAP yanıtı okunamadı: {}", e)))?;
            let text = String::from_utf8_lossy(&bytes).into_owned();
            gw.touch();
            Ok::<(UyapResponse, reqwest::header::HeaderMap), JobError>((
                UyapResponse {
                    status_code: status,
                    headers: resp_headers,
                    body: bytes.to_vec(),
                    text,
                },
                resp_headers_raw,
            ))
        };

        let (mut uyap_resp, raw_hdrs) = if is_write {
            let _guard = runtime::batch_write().await;
            do_once().await?
        } else {
            do_once().await?
        };

        if _looks_like_login_wall(uyap_resp.status_code, &raw_hdrs) {
            self.log("⚠️ UYAP oturumu düştü; yeniden giriş yapılıyor...");
            gw.logged_in.store(false, Ordering::SeqCst);
            gw.relogin().await.map_err(JobError::Failed)?;
            let (retry_resp, _) = if is_write {
                let _guard = runtime::batch_write().await;
                do_once().await?
            } else {
                do_once().await?
            };
            uyap_resp = retry_resp;
        }

        Ok(uyap_resp)
    }

    pub async fn uyap_json(
        &self,
        method: &str,
        path: &str,
        payload: &Value,
    ) -> Result<UyapResponse, JobError> {
        self.uyap(method, path, None, Some(payload), None, None, None)
            .await
    }

    pub async fn uyap_post_form(
        &self,
        path: &str,
        form: &[(String, String)],
    ) -> Result<UyapResponse, JobError> {
        self.uyap("POST", path, None, None, Some(form), None, None)
            .await
    }

    pub async fn uyap_get(
        &self,
        path: &str,
        query: Option<&str>,
    ) -> Result<UyapResponse, JobError> {
        self.uyap("GET", path, query, None, None, None, None).await
    }

    pub async fn uyap_post_multipart(
        &self,
        path: &str,
        fields: Vec<(String, Option<String>, Vec<u8>, Option<String>)>,
    ) -> Result<UyapResponse, JobError> {
        self.check_cancel()?;
        let gw = uyap_proxy::get_gw()
            .ok_or_else(|| JobError::Failed("Ofis UYAP oturumu henüz hazır değil.".to_string()))?;
        let clean_path = path.trim_start_matches('/');
        let url = format!("{}/{}", UYAP_BASE, clean_path);

        let build_form = || {
            let mut form = reqwest::multipart::Form::new();
            for (name, filename, bytes, mime) in &fields {
                if let Some(fname) = filename {
                    let part = reqwest::multipart::Part::bytes(bytes.clone())
                        .file_name(fname.clone());
                    let part = if let Some(m) = mime {
                        part.mime_str(m).unwrap_or_else(|_| {
                            reqwest::multipart::Part::bytes(bytes.clone()).file_name(fname.clone())
                        })
                    } else {
                        part
                    };
                    form = form.part(name.clone(), part);
                } else {
                    form = form.text(name.clone(), String::from_utf8_lossy(bytes).into_owned());
                }
            }
            form
        };

        let do_once = || async {
            gw.ensure_alive().await.map_err(JobError::Failed)?;
            let resp = gw
                .client
                .post(&url)
                .header("Referer", format!("{}/", UYAP_BASE))
                .multipart(build_form())
                .send()
                .await
                .map_err(|e| JobError::Failed(format!("UYAP isteği hatası ({}): {}", path, e)))?;
            let status = resp.status().as_u16();
            let raw_h = resp.headers().clone();
            let mut hdrs = HashMap::new();
            for (k, v) in &raw_h {
                if let Ok(vs) = v.to_str() {
                    hdrs.insert(k.as_str().to_ascii_lowercase(), vs.to_string());
                }
            }
            let bytes = resp
                .bytes()
                .await
                .map_err(|e| JobError::Failed(format!("UYAP yanıtı okunamadı: {}", e)))?;
            let text = String::from_utf8_lossy(&bytes).into_owned();
            gw.touch();
            Ok::<(UyapResponse, reqwest::header::HeaderMap), JobError>((
                UyapResponse {
                    status_code: status,
                    headers: hdrs,
                    body: bytes.to_vec(),
                    text,
                },
                raw_h,
            ))
        };

        let (mut uyap_resp, raw_hdrs) = {
            let _guard = runtime::batch_write().await;
            do_once().await?
        };
        if _looks_like_login_wall(uyap_resp.status_code, &raw_hdrs) {
            self.log("⚠️ UYAP oturumu düştü; yeniden giriş yapılıyor...");
            gw.logged_in.store(false, Ordering::SeqCst);
            gw.relogin().await.map_err(JobError::Failed)?;
            let _guard = runtime::batch_write().await;
            let (retry_resp, _) = do_once().await?;
            uyap_resp = retry_resp;
        }
        Ok(uyap_resp)
    }
}

pub type JobHandlerFn = Arc<
    dyn Fn(JobContext, Value) -> Pin<Box<dyn Future<Output = Result<Value, JobError>> + Send>>
        + Send
        + Sync,
>;

pub struct JobManager {
    jobs: StdMutex<HashMap<String, Arc<Job>>>,
    order: StdMutex<Vec<String>>,
    handlers: StdMutex<HashMap<String, JobHandlerFn>>,
    worker_lock: Arc<AsyncMutex<()>>,
}

impl JobManager {
    pub fn new() -> Self {
        Self {
            jobs: StdMutex::new(HashMap::new()),
            order: StdMutex::new(Vec::new()),
            handlers: StdMutex::new(HashMap::new()),
            worker_lock: Arc::new(AsyncMutex::new(())),
        }
    }

    pub fn register_type(&self, name: &str, handler: JobHandlerFn) {
        let mut h = self.handlers.lock().unwrap();
        h.insert(name.to_string(), handler);
    }

    pub fn registered_types(&self) -> Vec<String> {
        let h = self.handlers.lock().unwrap();
        let mut keys: Vec<String> = h.keys().cloned().collect();
        keys.sort();
        keys
    }

    pub fn submit(&self, job_type: &str, params: Value, owner: &str) -> Result<Arc<Job>, String> {
        let handler = {
            let h = self.handlers.lock().unwrap();
            h.get(job_type).cloned()
        }
        .ok_or_else(|| format!("Bilinmeyen iş türü: {}", job_type))?;

        let id = new_job_id();
        let job = Job::new(id.clone(), job_type.to_string(), params.clone(), owner.to_string());

        {
            let mut jobs = self.jobs.lock().unwrap();
            let mut order = self.order.lock().unwrap();
            jobs.insert(id.clone(), job.clone());
            order.push(id.clone());
            while order.len() > _MAX_STORED_JOBS {
                let oldest = order.remove(0);
                if let Some(old_job) = jobs.get(&oldest) {
                    let st = old_job.inner.lock().unwrap().status.clone();
                    if st == DONE || st == ERROR || st == CANCELLED {
                        jobs.remove(&oldest);
                    } else {
                        order.push(oldest);
                        break;
                    }
                }
            }
        }

        let job_clone = job.clone();
        let worker_lock = self.worker_lock.clone();
        tokio::spawn(async move {
            let _guard = worker_lock.lock().await;
            {
                let mut g = job_clone.inner.lock().unwrap();
                if job_clone.cancel_flag.load(Ordering::SeqCst) {
                    g.status = CANCELLED.to_string();
                    g.finished_at = Some(now_secs());
                    return;
                }
                g.status = RUNNING.to_string();
                g.started_at = Some(now_secs());
            }
            let ctx = JobContext::new(job_clone.clone());
            match handler(ctx, params).await {
                Ok(res) => {
                    let mut g = job_clone.inner.lock().unwrap();
                    g.status = DONE.to_string();
                    g.result = Some(res);
                    g.finished_at = Some(now_secs());
                }
                Err(JobError::Cancelled(msg)) => {
                    let mut g = job_clone.inner.lock().unwrap();
                    g.status = CANCELLED.to_string();
                    g.error = Some(msg);
                    g.finished_at = Some(now_secs());
                }
                Err(JobError::Failed(msg)) => {
                    let mut g = job_clone.inner.lock().unwrap();
                    g.status = ERROR.to_string();
                    g.error = Some(msg);
                    g.finished_at = Some(now_secs());
                }
            }
        });

        Ok(job)
    }

    pub fn get(&self, job_id: &str) -> Option<Arc<Job>> {
        self.jobs.lock().unwrap().get(job_id).cloned()
    }

    pub fn list_jobs(&self) -> Vec<Value> {
        let jobs = self.jobs.lock().unwrap();
        let order = self.order.lock().unwrap();
        order
            .iter()
            .rev()
            .filter_map(|id| jobs.get(id).map(|j| j.to_dict(usize::MAX)))
            .collect()
    }

    pub fn cancel(&self, job_id: &str) -> Option<Arc<Job>> {
        let job = self.get(job_id)?;
        job.cancel_flag.store(true, Ordering::SeqCst);
        {
            let mut g = job.inner.lock().unwrap();
            if g.status == QUEUED {
                g.status = CANCELLED.to_string();
                g.finished_at = Some(now_secs());
            }
        }
        job.approval_notify.notify_waiters();
        Some(job)
    }

    pub fn respond_approval(&self, job_id: &str, approved: bool, note: &str) -> Option<Arc<Job>> {
        let job = self.get(job_id)?;
        {
            let mut g = job.inner.lock().unwrap();
            if g.status != AWAITING_APPROVAL {
                return None;
            }
            g.approval_response = Some((approved, note.to_string()));
        }
        job.approval_notify.notify_waiters();
        Some(job)
    }
}

pub static MANAGER: Lazy<JobManager> = Lazy::new(|| {
    let mgr = JobManager::new();
    crate::job_handlers::register_all(&mgr);
    mgr
});

pub fn registered_types() -> Vec<String> {
    MANAGER.registered_types()
}

pub fn is_control_path(path: &str) -> bool {
    let p = path.trim_start_matches('/');
    p == AGENT_PREFIX || p.starts_with(&format!("{}/", AGENT_PREFIX))
}

fn json_resp(status: u16, val: Value) -> (u16, HashMap<String, String>, Vec<u8>) {
    let mut headers = HashMap::new();
    headers.insert(
        "content-type".to_string(),
        "application/json; charset=utf-8".to_string(),
    );
    let body = serde_json::to_vec(&val).unwrap_or_else(|_| b"{}".to_vec());
    (status, headers, body)
}

fn parse_query_params(query: &str) -> HashMap<String, String> {
    url::form_urlencoded::parse(query.as_bytes())
        .into_owned()
        .collect()
}

pub async fn handle_control(
    method: &str,
    path: &str,
    query: &str,
    body: &[u8],
    kaynak: Option<&str>,
) -> (u16, HashMap<String, String>, Vec<u8>) {
    let clean = format!("/{}", path.trim_start_matches('/'));
    let m = method.trim().to_uppercase();
    let qp = parse_query_params(query);

    // 1) /__uyap_agent__/sign_udf
    if clean == SIGN_UDF_PATH {
        if m != "POST" {
            return json_resp(405, json!({"ok": false, "error": "Yalnız POST desteklenir."}));
        }
        let gw = match uyap_proxy::get_gw() {
            Some(g) => g,
            None => {
                return json_resp(
                    503,
                    json!({"ok": false, "error": "Ofis UYAP oturumu henüz hazır değil."}),
                )
            }
        };
        let pin = gw.login_args.pin.clone().unwrap_or_default();
        let cert_id = gw.cert_id.lock().clone();
        let filename = qp
            .get("filename")
            .or_else(|| qp.get("ext"))
            .cloned()
            .unwrap_or_else(|| ".udf".to_string());
        let body_vec = body.to_vec();
        let sign_res = tokio::task::spawn_blocking(move || {
            udf_signer::sign_document(&body_vec, &filename, cert_id.as_deref(), &pin)
        })
        .await;
        return match sign_res {
            Ok(Ok(signed_bytes)) => {
                let mut headers = HashMap::new();
                headers.insert(
                    "content-type".to_string(),
                    "application/octet-stream".to_string(),
                );
                (200, headers, signed_bytes)
            }
            Ok(Err(e)) => json_resp(400, json!({"ok": false, "error": e.0})),
            Err(e) => json_resp(500, json!({"ok": false, "error": e.to_string()})),
        };
    }

    // 2) /__uyap_agent__/ptt_sorgu
    if clean == PTT_SORGU_PATH {
        let barkod = qp.get("barkod").map(|s| s.as_str()).unwrap_or("");
        let res = crate::ptt_sorgu::sorgula(barkod).await;
        return json_resp(200, res);
    }

    // 3) /__uyap_agent__/tum_evrak/...
    if clean == TUM_EVRAK_PREFIX || clean.starts_with(&format!("{}/", TUM_EVRAK_PREFIX)) {
        if !toplu_is_acik() && m == "POST" {
            return json_resp(403, json!({"ok": false, "error": "Toplu işlem şu anda kapalı."}));
        }
        return crate::evrak_toplu::handle_http(&m, &clean, &qp, body).await;
    }

    // 4) /__uyap_agent__/gerekceli_karar/...
    if clean == GEREKCELI_PREFIX || clean.starts_with(&format!("{}/", GEREKCELI_PREFIX)) {
        if !toplu_is_acik() && m == "POST" {
            return json_resp(403, json!({"ok": false, "error": "Toplu işlem şu anda kapalı."}));
        }
        return crate::gerekceli_karar::handle_http(&m, &clean, &qp, body).await;
    }

    // 5) /__uyap_agent__/jobs...
    if clean == JOBS_PREFIX || clean == format!("{}/", JOBS_PREFIX) {
        if m == "GET" {
            return json_resp(
                200,
                json!({
                    "ok": true,
                    "jobs": MANAGER.list_jobs(),
                    "types": MANAGER.registered_types(),
                    "toplu_is_acik": toplu_is_acik(),
                }),
            );
        }
        if m == "POST" {
            if !toplu_is_acik() {
                return json_resp(
                    403,
                    json!({"ok": false, "error": "Toplu işlem şu anda kapalı."}),
                );
            }
            let payload: Value = match serde_json::from_slice(body) {
                Ok(v) => v,
                Err(e) => {
                    return json_resp(400, json!({"ok": false, "error": format!("Geçersiz JSON: {}", e)}))
                }
            };
            let job_type = payload
                .get("type")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim();
            if job_type.is_empty() {
                return json_resp(400, json!({"ok": false, "error": "İş türü ('type') gerekli."}));
            }
            let params = payload.get("params").cloned().unwrap_or_else(|| json!({}));
            let owner = kaynak.unwrap_or("local");
            return match MANAGER.submit(job_type, params, owner) {
                Ok(job) => json_resp(200, json!({"ok": true, "job": job.to_dict(0)})),
                Err(e) => json_resp(400, json!({"ok": false, "error": e})),
            };
        }
        return json_resp(405, json!({"ok": false, "error": "Desteklenmeyen metot."}));
    }

    if let Some(rest) = clean.strip_prefix(&format!("{}/", JOBS_PREFIX)) {
        let parts: Vec<&str> = rest.split('/').filter(|s| !s.is_empty()).collect();
        if parts.is_empty() {
            return json_resp(404, json!({"ok": false, "error": "Bulunamadı."}));
        }
        let job_id = parts[0];
        if parts.len() == 1 && m == "GET" {
            let since = qp
                .get("since")
                .and_then(|s| s.parse::<usize>().ok())
                .unwrap_or(0);
            return match MANAGER.get(job_id) {
                Some(job) => json_resp(200, json!({"ok": true, "job": job.to_dict(since)})),
                None => json_resp(404, json!({"ok": false, "error": "İş bulunamadı."})),
            };
        }
        if parts.len() == 2 && m == "POST" {
            let action = parts[1];
            match action {
                "cancel" => {
                    return match MANAGER.cancel(job_id) {
                        Some(job) => json_resp(200, json!({"ok": true, "job": job.to_dict(0)})),
                        None => json_resp(404, json!({"ok": false, "error": "İş bulunamadı."})),
                    };
                }
                "approve" | "reject" => {
                    let approved = action == "approve";
                    let note = serde_json::from_slice::<Value>(body)
                        .ok()
                        .and_then(|v| v.get("note").and_then(|n| n.as_str()).map(String::from))
                        .unwrap_or_default();
                    return match MANAGER.respond_approval(job_id, approved, &note) {
                        Some(job) => json_resp(200, json!({"ok": true, "job": job.to_dict(0)})),
                        None => json_resp(
                            404,
                            json!({"ok": false, "error": "Onay bekleyen iş bulunamadı."}),
                        ),
                    };
                }
                _ => {}
            }
        }
    }

    json_resp(404, json!({"ok": false, "error": format!("Bilinmeyen kontrol yolu: {}", clean)}))
}
