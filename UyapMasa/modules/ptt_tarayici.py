# -*- coding: utf-8 -*-
"""
PTT Gönderi Takip — Playwright ile (tarayıcı üzerinden) sorgu
==============================================================
`Desktop\\PTT Playwright Sorgu\\ptt_playwright_sorgu.py` (kullanıcıyla birlikte
yazılan, canlı çalışan bağımsız program, 21.09.2026) yönteminin UyapMasa'ya
uyarlanmasıdır. Neden: `api.ptt.gov.tr/api/ShipmentTracking`'e doğrudan HTTP
isteği artık `X-Tracking-Token` + Cloudflare Turnstile istiyor (404 döner);
PTT, seri/çok sayıda doğrudan istek geldiği için bunu kapattı. Burada ise
ptt.gov.tr'deki "Gönderi Takip" kutusu bir kullanıcının yapacağı gibi, İNSAN
HIZINDA kullanılır: barkod kutuya yazılır, SORGULA'ya basılır, sayfanın kendi
takip isteğinin (POST .../api/ShipmentTracking) yanıtı okunur.

Yöntem (bağımsız programla aynı):
  • Sistemdeki gerçek Chrome AYRI bir süreç olarak, UyapMasa'ya ÖZEL bir
    profille (kullanıcının kendi Chrome profili DEĞİL) açılır; Playwright ona
    CDP ile bağlanır. Tarayıcı kimliği (UA vb.) değiştirilmez.
  • Sorgular arası ortalama BEKLEME_SN (±%30) beklenir; barkod rakam rakam yazılır.
  • Doğrulama/yanıt ARDISIK_HATA_SINIRI kez üst üste gelmezse PTT'ye bir süre
    HİÇ sorgu atılmaz (SOGUMA_SN: 10 → 20 → 40 dk), süre dolunca sayfa
    yenilenip normal hızda yeniden denenir; tüm bekleme dönemleri tükenirse o
    tur için PTT sorgusu DURUR. Çözücü/hile YOK — yalnız beklenir. (2026-09-24:
    eskiden tek bir aksaklıkta PTT bütün tur boyunca kapanıyordu.) Bekleme
    sırasında UYAP tarafındaki barkod çıkarma işi sürer; o barkodların PTT
    durumu bu turda tazelenmez. Bekleme dönemindeki yanıtlar `_beklemede`
    (kalan saniye) taşır — yalnız-PTT turu buna bakıp bekler ve aynı barkodu
    yeniden dener (bkz. barkod_sorgu._yalniz_ptt_dosya_isle).

UyapMasa'ya göre farklar:
  • CDP portu SABİT 9222 değil, her açılışta boş rastgele bir yerel port
    (güvenlik incelemesi: sabit 9222'ye bilgisayardaki her süreç bağlanabiliyordu).
  • Profil `%LOCALAPPDATA%\\UyapMasa\\ptt_tarayici_profili`; kullanımdaysa
    (ör. masaüstü ve web aynı anda) geçici bir profil açılır, iş bitince silinir.
  • Süreç boyunca TEK tarayıcı: turlar (`with ptt_oturumu(log): ...`) onu
    paylaşır, tur bitince kapanmaz; BOSTA_KAPAT_SN boşta kalınca kapanır.
  • Pencere varsayılan olarak EKRAN DIŞINDA açılır (UYAP_PTT_GORUNUR=1 → ekranda).
  • UYAP_PTT_HEADLESS=1 ile headless mod — 2026-09-28 canlı: Turnstile
    "Doğrulama tamamlanamadı" ile REDDEDİYOR, kullanılamaz; yalnız deneme için.

Dönüş: `sorgula(barkod)` PTT'nin listesinin İLK elemanı (dict) — eski
`barkod_sorgu.ptt_sorgula` ile aynı sözleşme; hata halinde {"_hata": "..."}.
"""

import atexit
import os
import random
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

SITE_URL = "https://www.ptt.gov.tr/"
TAKIP_YOLU = "/api/ShipmentTracking"
GIRIS_SECICI = 'input[placeholder="Gönderi takip no giriniz"]:visible'
GIRIS_YEDEK_SECICI = 'input[name="value"]:visible'
DOGRULAMA_HATASI = "Doğrulama tamamlanamadı"
ARDISIK_HATA_SINIRI = 3
SOGUMA_SN = (600, 1200, 2400)   # ardışık hata sonrası bekleme dönemleri (10/20/40 dk)
BEKLEME_SN = 3.0           # sorgular arası ortalama bekleme (insan hızı)
ZAMAN_ASIMI_SN = 25
BARKOD_RE = re.compile(r"[A-Za-z0-9]{8,30}")


def _chrome_exe_bul():
    adaylar = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ]
    return next((c for c in adaylar if c and os.path.exists(c)), None)


def _bos_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _pencere_gorunur():
    """UYAP_PTT_GORUNUR=1 → PTT penceresi ekranda açılır (sorun ararken gözle bakmak için)."""
    return (os.environ.get("UYAP_PTT_GORUNUR", "") or "").strip().lower() in ("1", "true", "yes", "on")


def _profil_klasoru():
    kok = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    yol = os.path.join(kok, "UyapMasa", "ptt_tarayici_profili")
    os.makedirs(yol, exist_ok=True)
    return yol


class PttTarayici:
    """Tek bir tarayıcı oturumu. Playwright'ın senkron API'si oluşturulduğu
    thread'e bağlıdır — bu nesne yalnız kendisini açan thread'de kullanılır."""

    def __init__(self, log_fn=None, headless=None):
        self.log = log_fn or (lambda *a, **k: None)
        if headless is None:
            headless = (os.environ.get("UYAP_PTT_HEADLESS", "") or "").strip().lower() in ("1", "true", "yes", "on")
        self.headless = headless
        self._pw = None
        self._browser = None
        self._ctx = None
        self._page = None
        self._giris = None
        self._buton = None
        self._proc = None
        self._gecici_profil = None
        self._son_sorgu = 0.0
        self._ardisik_hata = 0
        self._soguma_bitis = 0.0  # >0 ise bu ana kadar PTT'ye sorgu atılmaz
        self._soguma_sayisi = 0   # art arda kaçıncı bekleme dönemi (başarıda sıfırlanır)
        self.durduruldu = ""      # dolu ise bu oturumda PTT sorgusu yapılmaz (neden)
        self.sorgu_sayisi = 0

    # ── açma / kapama ────────────────────────────────────────────────────────
    def _chrome_baslat(self, profil):
        chrome = _chrome_exe_bul()
        if not chrome:
            raise RuntimeError("Google Chrome bulunamadı (PTT sorgusu için gerekli).")
        port = _bos_port()
        komut = [chrome, f"--remote-debugging-port={port}", f"--user-data-dir={profil}",
                 "--no-first-run", "--no-default-browser-check", "--no-service-autorun",
                 "--password-store=basic", "--lang=tr-TR",
                 "--window-size=1366,900"]  # dar pencerede site takip kutusunu gizliyor
        if self.headless:
            komut.append("--headless=new")
        elif not _pencere_gorunur():
            # Normal (başlıklı) Chrome, pencere ekranın dışında: kullanıcı görmez,
            # tarayıcı kimliği değişmez. 2026-09-28 canlı: headless Turnstile'a
            # takılıyor ("HeadlessChrome" UA), ekran dışı pencere geçiyor.
            komut.append("--window-position=-32000,-32000")
        self._proc = subprocess.Popen(komut, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        son_hata = None
        for _ in range(20):                       # en çok ~10 sn
            time.sleep(0.5)
            if self._proc.poll() is not None:     # profil başka Chrome'da açık → süreç hemen çıkar
                raise RuntimeError("Chrome hemen kapandı (profil kullanımda olabilir).")
            try:
                self._browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                return
            except Exception as e:  # noqa: BLE001 — port henüz açılmadı
                son_hata = e
        raise RuntimeError(f"Chrome'a bağlanılamadı: {son_hata}")

    def ac(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        try:
            try:
                self._chrome_baslat(_profil_klasoru())
            except RuntimeError as e:
                self._sureci_kapat()
                self.log(f"  ℹ️ PTT tarayıcısı ortak profille açılamadı ({e}); geçici profil deneniyor.")
                self._gecici_profil = tempfile.mkdtemp(prefix="uyapmasa_ptt_")
                self._chrome_baslat(self._gecici_profil)
            self._ctx = (self._browser.contexts[0] if self._browser.contexts
                         else self._browser.new_context(locale="tr-TR", timezone_id="Europe/Istanbul"))
            self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
            self._sayfayi_hazirla()
            self.log("  🌐 PTT tarayıcısı hazır (ptt.gov.tr Gönderi Takip).")
        except Exception:
            self.kapat()
            raise
        return self

    def _sureci_kapat(self):
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    self._proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            self._proc = None

    def kapat(self):
        for kapat_fn in (lambda: self._browser and self._browser.close(),
                         lambda: self._pw and self._pw.stop()):
            try:
                kapat_fn()
            except Exception:  # noqa: BLE001
                pass
        self._browser = self._pw = self._ctx = self._page = None
        self._sureci_kapat()
        if self._gecici_profil:
            shutil.rmtree(self._gecici_profil, ignore_errors=True)
            self._gecici_profil = None

    def canli(self):
        """Tarayıcı hâlâ kullanılabilir mi (kullanıcı kapatmadı, çökmedi)?"""
        try:
            return (self._proc is not None and self._proc.poll() is None
                    and self._browser is not None and self._browser.is_connected()
                    and self._page is not None and not self._page.is_closed())
        except Exception:  # noqa: BLE001
            return False

    def tur_basla(self, log_fn=None):
        """Açık tarayıcı yeni bir turda yeniden kullanılırken tur sayaçlarını
        sıfırlar — eskiden her tur taze tarayıcıyla (taze durumla) başlardı."""
        if log_fn:
            self.log = log_fn
        self.durduruldu = ""
        self._ardisik_hata = 0
        self._soguma_bitis = 0.0
        self._soguma_sayisi = 0
        self.sorgu_sayisi = 0

    # ── sayfa ────────────────────────────────────────────────────────────────
    def _sayfayi_hazirla(self):
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        page = self._page
        page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60000)
        giris = page.locator(GIRIS_SECICI).first
        try:
            giris.wait_for(state="visible", timeout=30000)
        except PlaywrightTimeout:
            giris = page.locator(GIRIS_YEDEK_SECICI).first
            giris.wait_for(state="visible", timeout=15000)
        self._giris = giris
        self._buton = giris.locator("xpath=..").get_by_role("button", name=re.compile("sorgula", re.I))

    def _sayfa_mesaji(self):
        try:
            metin = self._page.locator("body").inner_text(timeout=3000)
            return DOGRULAMA_HATASI if DOGRULAMA_HATASI in metin else ""
        except Exception:  # noqa: BLE001
            return ""

    def _bekle(self):
        """İnsan hızı: son sorgudan bu yana ortalama BEKLEME_SN (±%30) geçmesini bekler."""
        hedef = BEKLEME_SN * random.uniform(0.7, 1.3)
        gecen = time.time() - self._son_sorgu
        if self._son_sorgu and gecen < hedef:
            time.sleep(hedef - gecen)

    def sorgula(self, barkod):
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        if self.durduruldu:
            return {"_hata": f"PTT sorgusu bu turda durduruldu ({self.durduruldu})"}
        barkod = str(barkod or "").strip()
        if not BARKOD_RE.fullmatch(barkod):
            return {"_hata": "Geçersiz barkod biçimi"}
        if self._soguma_bitis:
            kalan = self._soguma_bitis - time.time()
            if kalan > 0:
                return {"_hata": f"PTT bekleme döneminde ({int(kalan // 60) + 1} dk kaldı)",
                        "_beklemede": kalan}
            self._soguma_bitis = 0.0
            self.log("  ▶ PTT bekleme dönemi bitti — sayfa yenilenip yeniden deneniyor.")
            if not self._tazele():
                return {"_hata": "PTT sayfası yeniden yüklenemedi", "_beklemede": self._kalan()}
        self._bekle()
        yanit_json, neden = None, ""
        try:
            self._giris.click()
            self._giris.fill("")
            self._giris.press_sequentially(barkod, delay=random.randint(60, 140))
            with self._page.expect_response(
                    lambda r: TAKIP_YOLU in r.url and r.request.method == "POST",
                    timeout=ZAMAN_ASIMI_SN * 1000) as bilgi:
                self._buton.click()
            yanit = bilgi.value
            if yanit.status == 200:
                try:
                    yanit_json = yanit.json()
                except (ValueError, PlaywrightError):
                    neden = "PTT yanıtı JSON değil"
            else:
                neden = f"PTT HTTP {yanit.status}"
        except PlaywrightTimeout:
            neden = "yanıt gelmedi"
        except PlaywrightError as e:
            neden = str(e).splitlines()[0]
        finally:
            self._son_sorgu = time.time()
            self.sorgu_sayisi += 1

        if yanit_json is None:
            neden = self._sayfa_mesaji() or neden or "yanıt gelmedi"
            self._hata_kaydet(neden)
            if not self._soguma_bitis and not self.durduruldu:
                self._tazele()
            hata = {"_hata": f"PTT yanıtı alınamadı ({neden})"}
            if self._soguma_bitis:
                hata["_beklemede"] = self._kalan()
            return hata

        self._ardisik_hata = 0
        self._soguma_sayisi = 0
        veri = yanit_json[0] if isinstance(yanit_json, list) and yanit_json else yanit_json
        if not isinstance(veri, dict):
            return {"_hata": "PTT beklenmeyen yanıt biçimi"}
        if veri.get("errorState"):
            self._tazele()          # sonuç penceresi açıldı → temiz başlangıç
        return veri

    def _kalan(self):
        return max(0.0, self._soguma_bitis - time.time()) if self._soguma_bitis else 0.0

    def _hata_kaydet(self, neden, zorla=False):
        """Ardışık hatayı sayar; sınır aşılınca (ya da `zorla`) sıradaki bekleme
        dönemini başlatır, dönemler tükendiyse bu turda PTT'yi durdurur."""
        self._ardisik_hata = ARDISIK_HATA_SINIRI if zorla else self._ardisik_hata + 1
        if self._ardisik_hata < ARDISIK_HATA_SINIRI:
            return
        ek = " — PTT doğrulaması otomasyonu reddediyor olabilir" if neden == DOGRULAMA_HATASI else ""
        if self._soguma_sayisi < len(SOGUMA_SN):
            sure = SOGUMA_SN[self._soguma_sayisi]
            self._soguma_sayisi += 1
            self._soguma_bitis = time.time() + sure
            # Bekleme sonrası TEK hata bir sonraki döneme geçirsin (3 hata daha beklenmez).
            self._ardisik_hata = ARDISIK_HATA_SINIRI - 1
            self.log(f"  ⏸ PTT yanıt vermiyor ({neden}{ek}). {sure // 60} dk hiç sorgu atılmayacak, "
                     f"sonra yeniden denenecek ({self._soguma_sayisi}/{len(SOGUMA_SN)}). "
                     "Barkodlar kaydedilmeye devam ediyor.")
        else:
            self.durduruldu = f"{len(SOGUMA_SN)} bekleme döneminden sonra da yanıt yok: {neden}{ek}"
            self.log(f"  ⛔ {self.durduruldu}. Bu turda PTT sorgusu durduruldu; "
                     "barkodlar yine kaydediliyor.")

    def _tazele(self):
        """Sayfayı yeniden yükler; başarısızsa bir hata sayıp (bekleme dönemi
        başlatarak) False döner — eskiden tek başarısızlıkta PTT tur boyu kapanıyordu."""
        try:
            self._sayfayi_hazirla()
            return True
        except Exception as e:  # noqa: BLE001
            self._hata_kaydet(f"PTT sayfası yeniden yüklenemedi: {str(e).splitlines()[0]}", zorla=True)
            return False


# ── tur boyu tek oturum ──────────────────────────────────────────────────────
# Tarayıcı ÇAĞIRANIN thread'inde DEĞİL, oturuma özel TEK bir işçi thread'inde
# yaşar. Kullanıcı bulgusu (2026-09-23, 2024/11435): Playwright'ın senkron API'si
# bulunduğu thread'de bir asyncio olay döngüsü çalıştırır; Django o thread'den
# yapılan HER DB çağrısını "You cannot call this from an async context" ile
# REDDEDİYORDU — PTT sonuçları, barkod kayıtları ve kesinleşme hesabı hiç
# yazılmıyordu. Artık çağıran thread yalnız sonucu bekler, döngü ona bulaşmaz.
#
# 2026-09-28 (kullanıcı isteği): tarayıcı artık tur bitince KAPANMIYOR — süreç
# boyunca tek Chrome açık kalır, sonraki turlar/tek sorgular onu kullanır (her
# "Sorgula"da Chrome'u yeniden açmak zaman kaybıydı). Son tur bittikten
# BOSTA_KAPAT_SN sonra hâlâ yeni tur yoksa kapanır; süreç biterken de kapanır.
# Tarayıcı ve işçi thread'i bu yüzden süreç geneli (_ortak); tur sayaçları
# her turun başında sıfırlanır (PttTarayici.tur_basla).
BOSTA_KAPAT_SN = 15 * 60
TAZELE_SN = 5 * 60   # bu kadar boşta kalmış sayfa yeni turdan önce yenilenir

_yerel = threading.local()
_ortak = {"tarayici": None, "isci": None, "aktif": 0, "zamanlayici": None}
_ortak_kilit = threading.Lock()


def _iscide(fn):
    with _ortak_kilit:
        if _ortak["isci"] is None:
            _ortak["isci"] = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ptt_tarayici")
        isci = _ortak["isci"]
    return isci.submit(fn).result()


def _tarayici_hazirla(log_fn):
    """(İşçi thread'inde) açık ve canlı ortak tarayıcıyı döner; yoksa açar."""
    t = _ortak["tarayici"]
    if t is not None and not t.canli():
        if log_fn:
            log_fn("  ℹ️ PTT tarayıcısı kapanmış — yeniden açılıyor.")
        t.kapat()
        t = _ortak["tarayici"] = None
    if t is None:
        t = _ortak["tarayici"] = PttTarayici(log_fn).ac()
        t.tur_basla(log_fn)
        return t
    t.tur_basla(log_fn)
    if t._son_sorgu and time.time() - t._son_sorgu > TAZELE_SN:
        t._tazele()
    return t


def _bosta_kapat():
    with _ortak_kilit:
        if _ortak["aktif"]:
            return
        _ortak["zamanlayici"] = None
    try:
        _iscide(_ortak_tarayiciyi_kapat)
    except Exception:  # noqa: BLE001 — süreç kapanırken işçi kapanmış olabilir
        pass


def _ortak_tarayiciyi_kapat():
    t, _ortak["tarayici"] = _ortak["tarayici"], None
    if t is not None:
        t.kapat()


@atexit.register
def _cikista_kapat():
    # İşçi thread'i çıkışta durmuş olabilir; Chrome süreci ve geçici profil
    # doğrudan temizlenir (Playwright sürücüsü boru kapanınca kendisi çıkar).
    t = _ortak["tarayici"]
    if t is not None:
        t._sureci_kapat()
        if t._gecici_profil:
            shutil.rmtree(t._gecici_profil, ignore_errors=True)


@contextmanager
def ptt_oturumu(log_fn=None):
    """Bu blok (bir tur) boyunca (aynı thread'deki) `ptt_sorgula` çağrıları
    ortak tarayıcıyı kullanır. Tarayıcı ilk PTT sorgusunda açılır (hiç sorgu
    yoksa Chrome açılmaz); blok bitince AÇIK KALIR, BOSTA_KAPAT_SN boyunca yeni
    tur gelmezse kapanır. İç içe kullanılırsa dıştaki geçerli."""
    if getattr(_yerel, "oturum", None) is not None:
        yield _yerel.oturum
        return
    kutu = {"tarayici": None, "log": log_fn, "acma_hatasi": ""}
    with _ortak_kilit:
        _ortak["aktif"] += 1
        if _ortak["zamanlayici"] is not None:
            _ortak["zamanlayici"].cancel()
            _ortak["zamanlayici"] = None
    _yerel.oturum = kutu
    try:
        yield kutu
    finally:
        _yerel.oturum = None
        t = kutu["tarayici"]
        if log_fn and t is not None and t.sorgu_sayisi:
            log_fn(f"  🌐 PTT turu bitti ({t.sorgu_sayisi} sorgu); tarayıcı sonraki sorgular için açık "
                   f"(boşta {BOSTA_KAPAT_SN // 60} dk sonra kapanır).")
        with _ortak_kilit:
            _ortak["aktif"] -= 1
            if not _ortak["aktif"] and _ortak["tarayici"] is not None:
                z = threading.Timer(BOSTA_KAPAT_SN, _bosta_kapat)
                z.daemon = True
                _ortak["zamanlayici"] = z
                z.start()


def ptt_sorgula(barkod, log_fn=None):
    """Tek barkod sorgusu. `ptt_oturumu` dışında çağrılırsa tek sorguluk bir
    tur açılır (tarayıcı yine açık kalır)."""
    kutu = getattr(_yerel, "oturum", None)
    if kutu is None:
        with ptt_oturumu(log_fn):
            return ptt_sorgula(barkod, log_fn)
    if kutu["acma_hatasi"]:
        return {"_hata": kutu["acma_hatasi"]}
    if kutu["tarayici"] is None:
        try:
            kutu["tarayici"] = _iscide(lambda: _tarayici_hazirla(kutu["log"] or log_fn))
        except Exception as e:  # noqa: BLE001
            kutu["acma_hatasi"] = f"PTT tarayıcısı açılamadı: {e}"
            (log_fn or (lambda *a: None))(f"  ⛔ {kutu['acma_hatasi']}")
            return {"_hata": kutu["acma_hatasi"]}
    t = kutu["tarayici"]
    return _iscide(lambda: t.sorgula(barkod))
