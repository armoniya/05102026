//! Ofis tarafı paylaşılan çalışma-zamanı durumu (`uyap_core/_runtime.py` ve `uyap_core/__init__.py` 1:1 portu)
//! ---------------------------------------------------------------------------------------------------------
//! UYAP'a giden DEĞİŞTİREN istekleri tek tek (FIFO) işlemek için TEK bir yazma kilidi.
//! Hem anlık proxy istekleri (`office_agent::handle_uyap_request`, `uyap_proxy::proxy`) hem de iş
//! kuyruğu işleyicileri (`jobs::JobContext::uyap`) AYNI kilidi paylaşır; böylece bir kullanıcının
//! formu, bir başkasının çok adımlı sihirbazı ve arka plandaki toplu iş (ör. çoklu takip açma)
//! birbirinin UYAP sunucu-tarafı oturum durumunu BOZMAZ. GET'ler (okuma) kilitlenmez.
//!
//! Ayrı bir modülde durması, `office_agent` ↔ `jobs` arasında döngüsel bağımlılık oluşmadan ikisinin
//! de aynı kilit nesnesine erişmesini sağlar.
//!
//! ── ADİL DAĞITIM (bireysel-öncelikli geçit) ───────────────────────────────────────────────
//! Toplu iş (batch), bireysel kullanıcıları kilitlememeli. Çözüm: yazma kilidini iki tür
//! "geçit" üzerinden veriyoruz:
//!
//!   • `interactive_write()`  — bireysel kullanıcı (anlık proxy/tünel isteği). YÜKSEK öncelik:
//!         gelince "bekleyen bireysel" sayacını artırır, sonra kilidi normal alır.
//!   • `batch_write()`        — toplu iş (`jobs::JobContext::uyap`). DÜŞÜK öncelik: kilidi almadan
//!         ÖNCE, bekleyen bir bireysel istek varsa o(lar) bitene kadar bekler (drenaj).
//!
//! Toplu iş her yazma ADIMINI ayrı bir `batch_write()` ile geçtiği için, bir kullanıcı isteği
//! geldiğinde toplu iş sıradaki adımdan ÖNCE duraklar, kullanıcının işi bitince devam eder.
//! GET'ler hiçbir geçide girmediği için bireysel okuma asla beklemez.
//!
//! ── X-Uyap-Read (adil sıralama) ARTIK KİLİDİ HİÇ ATLAMAZ ─────────────────────────────────
//! Okuma-ipuçlu POST'lar artık SIRADAN bireysel istek gibi `interactive_write()`'a girer
//! (bkz. `is_read_hint` — hâlâ ayrıştırılabiliyor ama artık kilitleme kararını ETKİLEMİYOR,
//! yalnız belgesel/geriye-uyumlu).

use std::collections::{BTreeMap, HashMap};
use std::future::Future;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use once_cell::sync::Lazy;
use parking_lot::Mutex as SyncMutex;
use tokio::sync::{Mutex as AsyncMutex, Notify, OwnedMutexGuard};

/// Tek tek (sırayla) işlenmesi gereken HTTP metotları.
pub const WRITE_METHODS: &[&str] = &["POST", "PUT", "PATCH", "DELETE"];

/// İstemci "okuma ipucu" başlığı — ARTIK YALNIZ BELGESEL, bkz. modül docstring'i.
pub const READ_HINT_HEADER: &str = "x-uyap-read";

/// HTTP metodunun `WRITE_METHODS` içinde olup olmadığını büyük/küçük harf duyarsız kontrol eder.
#[inline]
pub fn is_write_method(method: &str) -> bool {
    let m = method.trim();
    WRITE_METHODS.iter().any(|w| w.eq_ignore_ascii_case(m))
}

#[derive(Debug)]
struct RuntimeState {
    write_lock: Arc<AsyncMutex<()>>,
    interactive_pending: AtomicUsize,
    no_interactive_notify: Arc<Notify>,
    aktif_istek: AtomicUsize,
    aktif_yok_notify: Arc<Notify>,
}

impl RuntimeState {
    fn new() -> Self {
        Self {
            write_lock: Arc::new(AsyncMutex::new(())),
            interactive_pending: AtomicUsize::new(0),
            no_interactive_notify: Arc::new(Notify::new()),
            aktif_istek: AtomicUsize::new(0),
            aktif_yok_notify: Arc::new(Notify::new()),
        }
    }
}

static STATE: Lazy<SyncMutex<Arc<RuntimeState>>> =
    Lazy::new(|| SyncMutex::new(Arc::new(RuntimeState::new())));

#[inline]
fn current_state() -> Arc<RuntimeState> {
    STATE.lock().clone()
}

/// `UYAP_WRITE_LOCK` sarmalayıcısı (`_runtime.UYAP_WRITE_LOCK.locked()` ve `async with UYAP_WRITE_LOCK` uyumu).
pub struct UyapWriteLock;

impl UyapWriteLock {
    /// Kilidin o anda tutulup tutulmadığını döndürür (`_runtime.UYAP_WRITE_LOCK.locked()`).
    #[inline]
    pub fn locked(&self) -> bool {
        let st = current_state();
        let res = st.write_lock.try_lock().is_err();
        res
    }

    /// Kilidi doğrudan alır (`async with UYAP_WRITE_LOCK`).
    #[inline]
    pub async fn lock(&self) -> OwnedMutexGuard<()> {
        let st = current_state();
        st.write_lock.clone().lock_owned().await
    }
}

/// Süreç boyunca tek örnek (`_runtime.UYAP_WRITE_LOCK`).
pub static UYAP_WRITE_LOCK: UyapWriteLock = UyapWriteLock;

/// Kilidin o anda tutulup tutulmadığını döndürür.
#[inline]
pub fn uyap_write_lock_locked() -> bool {
    UYAP_WRITE_LOCK.locked()
}

/// Bekleyen/işlenen bireysel (`interactive_write`) yazma isteği sayısı.
#[inline]
pub fn interactive_pending_sayisi() -> usize {
    current_state().interactive_pending.load(Ordering::SeqCst)
}

/// Yeni bir çalışma döngüsünde (ör. masaüstünde "Durdur → Paylaş") paylaşım başlarken çağrılır.
/// `UYAP_WRITE_LOCK`, `_interactive_pending`, `_no_interactive`, `_aktif_istek`, `_aktif_yok`
/// durumlarını temiz bir örnekle sıfırlar.
pub fn dongu_icin_sifirla() {
    let mut guard = STATE.lock();
    let old = guard.clone();
    old.interactive_pending.store(0, Ordering::SeqCst);
    old.no_interactive_notify.notify_waiters();
    old.aktif_istek.store(0, Ordering::SeqCst);
    old.aktif_yok_notify.notify_waiters();
    *guard = Arc::new(RuntimeState::new());
}

/// `interactive_write()` çağrısından dönen RAII kilit bekçisi.
/// Düşürüldüğünde (`Drop`) önce `UYAP_WRITE_LOCK` kilidini bırakır, ardından
/// `_interactive_pending` sayacını azaltır ve sıfıra indiyse bekleyen `batch_write` görevlerini uyandırır.
pub struct InteractiveWriteGuard {
    _lock_guard: Option<OwnedMutexGuard<()>>,
    state: Arc<RuntimeState>,
}

impl Drop for InteractiveWriteGuard {
    fn drop(&mut self) {
        // Önce yazma kilidini serbest bırak
        self._lock_guard.take();
        // Ardından bekleyen bireysel sayacını düşür ve 0 ise _no_interactive olayını tetikle
        let prev = self
            .state
            .interactive_pending
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |v| {
                Some(v.saturating_sub(1))
            })
            .unwrap_or(1);
        if prev <= 1 {
            self.state.no_interactive_notify.notify_waiters();
        }
    }
}

/// Bireysel kullanıcının yazma isteği için yüksek öncelikli geçit (`async with interactive_write():`).
/// Sayacı artırır (batch'in görmesi için), sonra yazma kilidini alır.
pub async fn interactive_write() -> InteractiveWriteGuard {
    let st = current_state();
    st.interactive_pending.fetch_add(1, Ordering::SeqCst);
    // Kilit beklenirken görev iptal edilirse bile sayacın düşmesi için önce bekçiyi oluşturuyoruz.
    let mut guard = InteractiveWriteGuard {
        _lock_guard: None,
        state: st.clone(),
    };
    let lock_guard = st.write_lock.clone().lock_owned().await;
    guard._lock_guard = Some(lock_guard);
    guard
}

/// Closure tabanlı yardımcı: `interactive_write` kilidi altında verilen async bloğu çalıştırır.
pub async fn with_interactive_write<F, Fut, T>(f: F) -> T
where
    F: FnOnce() -> Fut,
    Fut: Future<Output = T>,
{
    let _guard = interactive_write().await;
    f().await
}

/// `batch_write()` çağrısından dönen RAII kilit bekçisi.
pub struct BatchWriteGuard {
    _lock_guard: OwnedMutexGuard<()>,
}

/// Toplu işin yazma adımı için düşük öncelikli geçit (`async with batch_write():`).
/// Kilidi almadan ÖNCE, bekleyen bireysel istek kalmayana kadar bekler; böylece her adımdan
/// önce kullanıcıya yol verir.
pub async fn batch_write() -> BatchWriteGuard {
    let st = current_state();
    loop {
        let notified = st.no_interactive_notify.notified();
        if st.interactive_pending.load(Ordering::SeqCst) == 0 {
            break;
        }
        notified.await;
    }
    let lock_guard = st.write_lock.clone().lock_owned().await;
    BatchWriteGuard {
        _lock_guard: lock_guard,
    }
}

/// Closure tabanlı yardımcı: `batch_write` kilidi altında verilen async bloğu çalıştırır.
pub async fn with_batch_write<F, Fut, T>(f: F) -> T
where
    F: FnOnce() -> Fut,
    Fut: Future<Output = T>,
{
    let _guard = batch_write().await;
    f().await
}

/// `uyap_istek_izi()` çağrısından dönen RAII bekçisi.
/// Düşürüldüğünde `_aktif_istek` sayacını azaltır ve sıfıra indiyse `_aktif_yok` bekleyicilerini uyandırır.
pub struct UyapIstekIziGuard {
    state: Arc<RuntimeState>,
}

impl Drop for UyapIstekIziGuard {
    fn drop(&mut self) {
        let prev = self
            .state
            .aktif_istek
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |v| {
                Some(v.saturating_sub(1))
            })
            .unwrap_or(1);
        if prev <= 1 {
            self.state.aktif_yok_notify.notify_waiters();
        }
    }
}

/// Bizim (bireysel) UYAP isteğimizin uçuşta olduğunu işaretler; kilitlemez (`async with uyap_istek_izi():`).
pub async fn uyap_istek_izi() -> UyapIstekIziGuard {
    let st = current_state();
    st.aktif_istek.fetch_add(1, Ordering::SeqCst);
    UyapIstekIziGuard { state: st }
}

/// Senkron bağlamda `uyap_istek_izi` bekçisi başlatmak isteyen çağıranlar için yardımcı.
pub fn uyap_istek_izi_sync() -> UyapIstekIziGuard {
    let st = current_state();
    st.aktif_istek.fetch_add(1, Ordering::SeqCst);
    UyapIstekIziGuard { state: st }
}

/// Closure tabanlı yardımcı: `uyap_istek_izi` kapsamında verilen async bloğu çalıştırır.
pub async fn with_uyap_istek_izi<F, Fut, T>(f: F) -> T
where
    F: FnOnce() -> Fut,
    Fut: Future<Output = T>,
{
    let _guard = uyap_istek_izi().await;
    f().await
}

/// Uçuştaki bireysel UYAP isteklerinin anlık sayısı (`aktif_istek_sayisi() -> int`).
#[inline]
pub fn aktif_istek_sayisi() -> usize {
    current_state().aktif_istek.load(Ordering::SeqCst)
}

/// `aktif_istekler_bitsin` için varsayılan zaman aşımı (5.0 saniye) veya özel süre argümanı.
pub trait EnCokSnArg {
    fn into_en_cok_sn(self) -> f64;
}

impl EnCokSnArg for f64 {
    #[inline]
    fn into_en_cok_sn(self) -> f64 {
        self
    }
}

impl EnCokSnArg for Option<f64> {
    #[inline]
    fn into_en_cok_sn(self) -> f64 {
        self.unwrap_or(5.0)
    }
}

impl EnCokSnArg for () {
    #[inline]
    fn into_en_cok_sn(self) -> f64 {
        5.0
    }
}

/// Uçuştaki bireysel istekler bitene kadar (en çok `en_cok_sn`, varsayılan 5.0 sn) bekler; bittiyse `true`.
pub async fn aktif_istekler_bitsin<T: EnCokSnArg>(en_cok_sn: T) -> bool {
    let sn = en_cok_sn.into_en_cok_sn();
    let st = current_state();
    if st.aktif_istek.load(Ordering::SeqCst) == 0 {
        return true;
    }
    let dur = Duration::from_secs_f64(sn.max(0.0));
    let wait_fut = async {
        loop {
            let notified = st.aktif_yok_notify.notified();
            if st.aktif_istek.load(Ordering::SeqCst) == 0 {
                return true;
            }
            notified.await;
        }
    };
    tokio::time::timeout(dur, wait_fut).await.unwrap_or(false)
}

/// Varsayılan 5.0 saniye zaman aşımı ile `aktif_istekler_bitsin(5.0)` kısayolu.
#[inline]
pub async fn aktif_istekler_bitsin_varsayilan() -> bool {
    aktif_istekler_bitsin(5.0).await
}

/// Farklı HTTP başlık taşıyıcılarından (`http::HeaderMap`, `HashMap`, `BTreeMap`, dilimler)
/// başlık okumak için soyutlama (`is_read_hint(headers)`).
pub trait HeaderLookup {
    fn lookup_read_hint(&self) -> Option<String>;
}

impl HeaderLookup for http::HeaderMap {
    fn lookup_read_hint(&self) -> Option<String> {
        if let Some(val) = self.get(READ_HINT_HEADER) {
            if let Ok(s) = val.to_str() {
                return Some(s.to_string());
            }
        }
        for (k, v) in self.iter() {
            if k.as_str().eq_ignore_ascii_case(READ_HINT_HEADER) {
                if let Ok(s) = v.to_str() {
                    return Some(s.to_string());
                }
            }
        }
        None
    }
}

impl<S: std::hash::BuildHasher> HeaderLookup for HashMap<String, String, S> {
    fn lookup_read_hint(&self) -> Option<String> {
        if let Some(v) = self.get(READ_HINT_HEADER) {
            return Some(v.clone());
        }
        for (k, v) in self.iter() {
            if k.eq_ignore_ascii_case(READ_HINT_HEADER) {
                return Some(v.clone());
            }
        }
        None
    }
}

impl HeaderLookup for BTreeMap<String, String> {
    fn lookup_read_hint(&self) -> Option<String> {
        if let Some(v) = self.get(READ_HINT_HEADER) {
            return Some(v.clone());
        }
        for (k, v) in self.iter() {
            if k.eq_ignore_ascii_case(READ_HINT_HEADER) {
                return Some(v.clone());
            }
        }
        None
    }
}

impl HeaderLookup for [(String, String)] {
    fn lookup_read_hint(&self) -> Option<String> {
        for (k, v) in self.iter() {
            if k.eq_ignore_ascii_case(READ_HINT_HEADER) {
                return Some(v.clone());
            }
        }
        None
    }
}

impl HeaderLookup for Vec<(String, String)> {
    fn lookup_read_hint(&self) -> Option<String> {
        self.as_slice().lookup_read_hint()
    }
}

impl HeaderLookup for [(&str, &str)] {
    fn lookup_read_hint(&self) -> Option<String> {
        for (k, v) in self.iter() {
            if k.eq_ignore_ascii_case(READ_HINT_HEADER) {
                return Some((*v).to_string());
            }
        }
        None
    }
}

impl<T: HeaderLookup + ?Sized> HeaderLookup for &T {
    fn lookup_read_hint(&self) -> Option<String> {
        (**self).lookup_read_hint()
    }
}

impl<T: HeaderLookup> HeaderLookup for Option<T> {
    fn lookup_read_hint(&self) -> Option<String> {
        self.as_ref().and_then(|inner| inner.lookup_read_hint())
    }
}

/// `headers` içinde okuma-ipucu başlığı (`x-uyap-read`) VAR ve doğru mu?
/// Büyük/küçük harf duyarsız; değer `'0'`/`'false'`/`'no'`/`''` ise yok sayılır.
pub fn is_read_hint<H: HeaderLookup>(headers: H) -> bool {
    match headers.lookup_read_hint() {
        None => false,
        Some(val) => {
            let norm = val.trim().to_ascii_lowercase();
            !matches!(norm.as_str(), "" | "0" | "false" | "no")
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_interactive_and_batch_priority() {
        dongu_icin_sifirla();
        assert!(!UYAP_WRITE_LOCK.locked());
        assert_eq!(aktif_istek_sayisi(), 0);

        {
            let _izi = uyap_istek_izi().await;
            assert_eq!(aktif_istek_sayisi(), 1);
            let _g = interactive_write().await;
            assert!(UYAP_WRITE_LOCK.locked());
            assert_eq!(interactive_pending_sayisi(), 1);
        }

        assert!(!UYAP_WRITE_LOCK.locked());
        assert_eq!(interactive_pending_sayisi(), 0);
        assert_eq!(aktif_istek_sayisi(), 0);
        assert!(aktif_istekler_bitsin(0.1).await);
    }

    #[test]
    fn test_is_read_hint() {
        let mut h = HashMap::new();
        assert!(!is_read_hint(&h));
        h.insert("X-Uyap-Read".to_string(), "1".to_string());
        assert!(is_read_hint(&h));
        h.insert("X-Uyap-Read".to_string(), "false".to_string());
        assert!(!is_read_hint(&h));
        h.insert("X-Uyap-Read".to_string(), "0".to_string());
        assert!(!is_read_hint(&h));
    }
}
