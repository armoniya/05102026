"""
uyap_core.ptt_sorgu — tek barkod için PTT gönderi takip sorgusu (Playwright'sız)
================================================================================
UYAP sayfasında "Kapalı Tebligat" belgesindeki barkod seçilip sağ tık → "PTT barkod
sorgula" denince ofis bu uçla sorgular:

    GET /__uyap_agent__/ptt_sorgu?barkod=4007770084489  → {ok, barkod, durum, tarih, ...}

Yöntem UyapMasa/modules/ptt_tarayici.py ile aynıdır (canlı çalışan): PTT doğrudan HTTP
isteğini kapattı (X-Tracking-Token + Cloudflare Turnstile), bu yüzden ptt.gov.tr'deki
"Gönderi Takip" kutusu gerçek bir Chrome'da, insan hızında kullanılır ve sayfanın kendi
POST /api/ShipmentTracking yanıtı okunur. Fark: cokluuyap paketinde Playwright yok
(cokluuyap_app.spec excludes), Chrome'a DevTools protokolüyle (CDP) bir boru üzerinden
doğrudan bağlanılır.

  • Sistemdeki Chrome AYRI süreç, ÖZEL profil (%LOCALAPPDATA%\\CokluUyap\\ptt_profili),
    pencere ekran dışında (headless'ı Turnstile reddediyor). CDP için TCP portu AÇILMAZ:
    --remote-debugging-pipe ile yalnız bu sürecin elindeki borulardan konuşulur (yerel port
    açık kalsaydı makinedeki başka bir süreç/kullanıcı tarayıcıyı ele geçirebilirdi).
    UYAP_PTT_GORUNUR=1 → pencere ekranda (sorun ararken).
  • Tek tarayıcı paylaşılır; sorgular sırayla (kilit) ve en az BEKLEME_SN arayla yapılır,
    barkod rakam rakam yazılır. Chrome her sorgudan sonra kapanır (açık pencere kalmasın).
  • Çözücü/hile YOK: doğrulama geçmezse hata döner, kullanıcı sonra yeniden dener.
"""

import asyncio
import json
import os
import random
import re
import struct
import subprocess
import tempfile
import threading
import shutil
import time

SITE_URL = "https://www.ptt.gov.tr/"
TAKIP_YOLU = "/api/ShipmentTracking"
DOGRULAMA_HATASI = "Doğrulama tamamlanamadı"
BARKOD_RE = re.compile(r"\d{13}")
BEKLEME_SN = 3.0          # iki sorgu arası en az (insan hızı)
ZAMAN_ASIMI_SN = 25
SAYFA_ZAMAN_ASIMI_SN = 45
BEKLEYEN_AZAMI = 2        # süren sorgunun arkasında bekleyebilecek en çok istek

# Sayfadaki takip kutusu ve yanındaki SORGULA düğmesi (ptt_tarayici.py seçicileriyle aynı).
_KUTU_JS = r"""
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
"""


def _chrome_exe_bul():
    adaylar = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ]
    return next((c for c in adaylar if c and os.path.exists(c)), None)


def _profil_klasoru():
    kok = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    yol = os.path.join(kok, "CokluUyap", "ptt_profili")
    os.makedirs(yol, exist_ok=True)
    return yol


class _ChromeSureci:
    """chrome.exe'yi --remote-debugging-pipe ile başlatır. Chrome CDP'yi 3 (okur) ve 4 (yazar)
    numaralı CRT tanıtıcılarından konuşur; bunlar STARTUPINFO.lpReserved2 ile miras verilir
    (Node/libuv ve Playwright'ın yöntemi). Python'un subprocess'i lpReserved2 kuramadığı için
    CreateProcessW ctypes ile çağrılır; miras PROC_THREAD_ATTRIBUTE_HANDLE_LIST ile yalnız bu iki
    boruya sınırlanır. Popen'ın kullanılan kısmıyla aynı arayüz: poll / wait / terminate / kill."""

    def __init__(self, komut, creationflags=0):
        if os.name != "nt":
            raise RuntimeError("PTT sorgusu yalnız Windows'ta destekleniyor.")
        import ctypes
        import msvcrt
        from ctypes import wintypes as w
        self._ct = ctypes
        k32 = self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreatePipe.argtypes = [ctypes.POINTER(w.HANDLE), ctypes.POINTER(w.HANDLE), ctypes.c_void_p, w.DWORD]
        k32.SetHandleInformation.argtypes = [w.HANDLE, w.DWORD, w.DWORD]
        k32.CloseHandle.argtypes = [w.HANDLE]
        k32.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD,
                                                          ctypes.POINTER(ctypes.c_size_t)]
        k32.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, w.DWORD, ctypes.c_size_t, ctypes.c_void_p,
                                                  ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
        k32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        k32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        k32.WaitForSingleObject.restype = w.DWORD
        k32.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
        k32.TerminateProcess.argtypes = [w.HANDLE, w.UINT]

        class STARTUPINFOW(ctypes.Structure):
            _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
                        ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD), ("dwXSize", w.DWORD),
                        ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD), ("dwYCountChars", w.DWORD),
                        ("dwFillAttribute", w.DWORD), ("dwFlags", w.DWORD), ("wShowWindow", w.WORD),
                        ("cbReserved2", w.WORD), ("lpReserved2", ctypes.c_void_p),
                        ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE)]

        class STARTUPINFOEXW(ctypes.Structure):
            _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE),
                        ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]

        k32.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL,
                                       w.DWORD, ctypes.c_void_p, w.LPCWSTR, ctypes.POINTER(STARTUPINFOEXW),
                                       ctypes.POINTER(PROCESS_INFORMATION)]

        def hata(ne):
            return RuntimeError(f"Chrome başlatılamadı ({ne}: {ctypes.get_last_error()})")

        def boru():
            r, wr = w.HANDLE(), w.HANDLE()
            if not k32.CreatePipe(ctypes.byref(r), ctypes.byref(wr), None, 0):
                raise hata("CreatePipe")
            return r.value, wr.value

        HANDLE_FLAG_INHERIT, FOPEN, FPIPE = 0x1, 0x01, 0x08
        cocuk_okur, biz_yazariz = boru()            # fd 3: Chrome komutları buradan okur
        try:
            biz_okuruz, cocuk_yazar = boru()        # fd 4: Chrome yanıt/olayları buraya yazar
        except BaseException:
            k32.CloseHandle(cocuk_okur)
            k32.CloseHandle(biz_yazariz)
            raise
        cocuk_uclari = (cocuk_okur, cocuk_yazar)
        liste = None
        try:
            for h in cocuk_uclari:
                if not k32.SetHandleInformation(h, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT):
                    raise hata("SetHandleInformation")
            # lpReserved2: int adet | adet bayt CRT bayrağı | adet HANDLE (hizasız). 0-2 boş.
            bicim = "<i5B5" + ("q" if ctypes.sizeof(ctypes.c_void_p) == 8 else "i")
            ham = struct.pack(bicim, 5, 0, 0, 0, FOPEN | FPIPE, FOPEN | FPIPE,
                              -1, -1, -1, cocuk_okur, cocuk_yazar)
            miras = ctypes.create_string_buffer(ham, len(ham))

            boy = ctypes.c_size_t(0)
            k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(boy))
            tampon = ctypes.create_string_buffer(boy.value)
            if not k32.InitializeProcThreadAttributeList(tampon, 1, 0, ctypes.byref(boy)):
                raise hata("InitializeProcThreadAttributeList")
            liste = tampon
            miras_listesi = (w.HANDLE * 2)(*cocuk_uclari)
            PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x20002
            if not k32.UpdateProcThreadAttribute(liste, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, miras_listesi,
                                                 ctypes.sizeof(miras_listesi), None, None):
                raise hata("UpdateProcThreadAttribute")

            si = STARTUPINFOEXW()
            si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
            si.StartupInfo.cbReserved2 = len(ham)
            si.StartupInfo.lpReserved2 = ctypes.cast(miras, ctypes.c_void_p)
            si.lpAttributeList = ctypes.cast(liste, ctypes.c_void_p)
            pi = PROCESS_INFORMATION()
            EXTENDED_STARTUPINFO_PRESENT = 0x00080000
            satir = ctypes.create_unicode_buffer(subprocess.list2cmdline(komut))
            if not k32.CreateProcessW(komut[0], satir, None, None, True,
                                      creationflags | EXTENDED_STARTUPINFO_PRESENT,
                                      None, None, ctypes.byref(si), ctypes.byref(pi)):
                raise hata("CreateProcessW")
        except BaseException:
            k32.CloseHandle(biz_yazariz)
            k32.CloseHandle(biz_okuruz)
            raise
        finally:
            if liste is not None:
                k32.DeleteProcThreadAttributeList(liste)
            for h in cocuk_uclari:                  # çocukta kopyaları var; bizdekiler kapanmalı
                k32.CloseHandle(h)
        k32.CloseHandle(pi.hThread)
        self._h = pi.hProcess
        self.pid = pi.dwProcessId
        self.returncode = None
        self.yaz_fd = msvcrt.open_osfhandle(biz_yazariz, os.O_WRONLY | os.O_BINARY)
        self.oku_fd = msvcrt.open_osfhandle(biz_okuruz, os.O_RDONLY | os.O_BINARY)

    def poll(self):
        if self.returncode is None and self._h:
            if self._k32.WaitForSingleObject(self._h, 0) == 0:          # WAIT_OBJECT_0
                kod = self._ct.c_ulong()
                self._k32.GetExitCodeProcess(self._h, self._ct.byref(kod))
                self.returncode = kod.value
                self._k32.CloseHandle(self._h)
                self._h = None
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None and self._h:
            ms = 0xFFFFFFFF if timeout is None else int(timeout * 1000)
            if self._k32.WaitForSingleObject(self._h, ms) != 0:
                raise subprocess.TimeoutExpired("chrome", timeout)
        return self.poll()

    def terminate(self):
        if self.returncode is None and self._h:
            self._k32.TerminateProcess(self._h, 1)

    kill = terminate


class _BoruBaglantisi:
    """_Cdp'nin kullandığı websocket arayüzü (async for / send / close), Chrome'un CDP borusu
    üstünde. İletiler NUL baytıyla ayrılır; okuma bloklayan bir iş parçacığında yapılır."""

    def __init__(self, oku_fd, yaz_fd):
        self._yaz_fd, self._yaz_kilit = yaz_fd, threading.Lock()
        self._kuyruk = asyncio.Queue()
        threading.Thread(target=self._okuyucu, args=(oku_fd, asyncio.get_running_loop()),
                         daemon=True, name="ptt-cdp-boru").start()

    def _okuyucu(self, fd, loop):
        tampon = bytearray()
        try:
            while True:
                parca = os.read(fd, 1 << 16)
                if not parca:
                    break
                tampon += parca
                while (i := tampon.find(b"\0")) >= 0:
                    ileti = bytes(tampon[:i]).decode("utf-8", "replace")
                    del tampon[:i + 1]
                    loop.call_soon_threadsafe(self._kuyruk.put_nowait, ileti)
        except OSError:
            pass                                        # Chrome kapandı (boru koptu)
        finally:
            os.close(fd)
            try:
                loop.call_soon_threadsafe(self._kuyruk.put_nowait, None)
            except RuntimeError:
                pass                                    # döngü zaten kapanmış

    def __aiter__(self):
        return self

    async def __anext__(self):
        ileti = await self._kuyruk.get()
        if ileti is None:
            raise StopAsyncIteration
        return ileti

    def _yaz(self, veri):
        with self._yaz_kilit:
            if self._yaz_fd is None:
                raise ConnectionError("Chrome bağlantısı kapandı")
            while veri:
                veri = veri[os.write(self._yaz_fd, veri):]

    async def send(self, metin):
        await asyncio.to_thread(self._yaz, metin.encode("utf-8") + b"\0")

    async def close(self):
        with self._yaz_kilit:
            if self._yaz_fd is not None:
                os.close(self._yaz_fd)      # okuma ucu, Chrome kapanınca okuyucuda kapanır
                self._yaz_fd = None


class _Cdp:
    """Tek sayfa hedefine bağlı en küçük CDP istemcisi: komut gönder, olay dinle. Bağlantı
    tarayıcı düzeyindedir; oturum (Target.attachToTarget, flatten) kurulunca komutlar o sayfaya
    gider ve yalnız o sayfanın olayları dinleyicilere iletilir."""

    def __init__(self, ws):
        self.ws = ws
        self.oturum = None
        self._sira = 0
        self._bekleyen = {}
        self._dinleyiciler = []
        self._okuyucu = asyncio.create_task(self._oku())

    async def _oku(self):
        try:
            async for ham in self.ws:
                m = json.loads(ham)
                if "id" in m:
                    f = self._bekleyen.pop(m["id"], None)
                    if f and not f.done():
                        f.set_result(m)
                elif m.get("sessionId") == self.oturum:
                    for d in list(self._dinleyiciler):
                        d(m.get("method"), m.get("params") or {})
        except Exception:
            pass
        finally:
            for f in self._bekleyen.values():
                if not f.done():
                    f.set_exception(ConnectionError("Chrome bağlantısı kapandı"))
            self._bekleyen.clear()

    async def cmd(self, method, params=None, timeout=15, tarayici=False):
        self._sira += 1
        f = asyncio.get_running_loop().create_future()
        self._bekleyen[self._sira] = f
        ileti = {"id": self._sira, "method": method, "params": params or {}}
        if self.oturum and not tarayici:
            ileti["sessionId"] = self.oturum
        await self.ws.send(json.dumps(ileti))
        m = await asyncio.wait_for(f, timeout)
        if "error" in m:
            raise RuntimeError(f"{method}: {m['error'].get('message')}")
        return m.get("result") or {}

    async def degerlendir(self, ifade):
        r = await self.cmd("Runtime.evaluate", {"expression": ifade, "returnByValue": True})
        return (r.get("result") or {}).get("value")

    def canli(self):
        return not self._okuyucu.done()

    async def kapat(self):
        self._okuyucu.cancel()
        try:
            await self.ws.close()
        except Exception:
            pass


class _PttTarayici:
    def __init__(self):
        self.proc = None
        self.cdp = None
        self.gecici_profil = None
        self.son_sorgu = 0.0
        self.kilit = asyncio.Lock()
        self.bekleyen = 0

    # ── Chrome ──
    async def _baslat(self, profil):
        chrome = _chrome_exe_bul()
        if not chrome:
            raise RuntimeError("Google Chrome bulunamadı (PTT sorgusu için gerekli).")
        komut = [chrome, "--remote-debugging-pipe", f"--user-data-dir={profil}",
                 "--no-first-run", "--no-default-browser-check", "--no-service-autorun",
                 "--password-store=basic", "--lang=tr-TR", "--window-size=1366,900",
                 # Boru modu Chrome'da navigator.webdriver'ı true yapar (port modunda false); Turnstile
                 # bunu 600010 ile reddediyor (canlı, Chrome 155, 2026-10-22). UyapMasa'nın port moduyla aynı kalsın.
                 "--disable-blink-features=AutomationControlled",
                 "about:blank"]
        if (os.environ.get("UYAP_PTT_GORUNUR", "") or "").strip().lower() not in ("1", "true", "yes", "on"):
            komut.insert(-1, "--window-position=-32000,-32000")
        self.proc = _ChromeSureci(komut, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.cdp = _Cdp(_BoruBaglantisi(self.proc.oku_fd, self.proc.yaz_fd))
        sayfa = None
        try:
            for _ in range(20):                               # en çok ~10 sn
                hedefler = (await self.cdp.cmd("Target.getTargets", timeout=10)).get("targetInfos") or []
                sayfa = next((h for h in hedefler if h.get("type") == "page"), None)
                if sayfa:
                    break
                await asyncio.sleep(0.5)
            if not sayfa:
                sayfa = await self.cdp.cmd("Target.createTarget", {"url": "about:blank"})
            r = await self.cdp.cmd("Target.attachToTarget", {"targetId": sayfa["targetId"], "flatten": True})
        except (ConnectionError, asyncio.TimeoutError, RuntimeError) as e:
            if self.proc.poll() is not None:
                raise RuntimeError("Chrome hemen kapandı (profil kullanımda olabilir).") from e
            raise RuntimeError(f"Chrome'a bağlanılamadı: {e}") from e
        self.cdp.oturum = r["sessionId"]
        for m in ("Page.enable", "Network.enable", "Runtime.enable", "Page.bringToFront"):
            await self.cdp.cmd(m)
        # Pencere ekran dışında ve odaksız: odak taklidi olmadan SORGULA tıklaması doğrulamayı
        # (Turnstile) hiç başlatmıyor (canlı denendi, 2026-10-20).
        await self.cdp.cmd("Emulation.setFocusEmulationEnabled", {"enabled": True})

    async def _ac(self):
        if self.cdp and self.cdp.canli() and self.proc and self.proc.poll() is None:
            return
        await self._kapat()
        try:
            await self._baslat(_profil_klasoru())
        except RuntimeError:
            await self._kapat()
            self.gecici_profil = tempfile.mkdtemp(prefix="cokluuyap_ptt_")
            await self._baslat(self.gecici_profil)

    async def _kapat(self):
        if self.cdp:
            if self.cdp.canli():
                try:                                    # düzgün kapanış: profil yarım yazılmasın
                    await self.cdp.cmd("Browser.close", timeout=3, tarayici=True)
                except Exception:  # noqa: BLE001 — yanıt gelmeden boru kopabilir
                    pass
            await self.cdp.kapat()
            self.cdp = None
        if self.proc is not None:
            try:
                self.proc.terminate()
                await asyncio.to_thread(self.proc.wait, 5)
            except Exception:  # noqa: BLE001
                try:
                    self.proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            self.proc = None
        if self.gecici_profil:
            shutil.rmtree(self.gecici_profil, ignore_errors=True)
            self.gecici_profil = None

    # ── Sorgu ──
    async def _sayfayi_hazirla(self):
        """Her sorgu temiz sayfayla başlar (sonuç penceresi / eski doğrulama kalmasın)."""
        await self.cdp.cmd("Page.navigate", {"url": SITE_URL})
        son = time.time() + SAYFA_ZAMAN_ASIMI_SN
        while time.time() < son:
            await asyncio.sleep(0.5)
            try:
                konum = await self.cdp.degerlendir(_KUTU_JS)
            except Exception:  # noqa: BLE001 — sayfa yüklenirken bağlam değişir
                konum = None
            if konum:
                await asyncio.sleep(random.uniform(2.0, 3.0))   # sayfanın doğrulama betikleri yüklensin
                return konum
        raise RuntimeError("ptt.gov.tr'de gönderi takip kutusu bulunamadı")

    async def _tikla(self, x, y):
        for tur in ("mouseMoved", "mousePressed", "mouseReleased"):
            p = {"type": tur, "x": x, "y": y}
            if tur != "mouseMoved":
                p.update(button="left", clickCount=1)
            await self.cdp.cmd("Input.dispatchMouseEvent", p)
            await asyncio.sleep(random.uniform(0.04, 0.12))

    async def sorgula(self, barkod):
        # Sorgular sırayla yapılır; sürenin arkasında en çok BEKLEYEN_AZAMI istek bekler. Fazlası
        # hemen reddedilir: her sorgu ofiste Chrome açtırır, sınırsız kuyruk ofisi meşgul eder ve
        # ptt.gov.tr'nin ofis IP'sini engellemesine yol açabilir.
        if self.bekleyen >= BEKLEYEN_AZAMI:
            raise RuntimeError("Sırada başka PTT sorguları var; biraz sonra yeniden deneyin")
        self.bekleyen += 1
        try:
            await self.kilit.acquire()
        finally:
            self.bekleyen -= 1
        try:
            return await self._sorgula(barkod)
        finally:
            try:
                self.son_sorgu = time.time()
                await self._kapat()          # pencere görev çubuğunda açık kalmasın
            finally:
                self.kilit.release()

    async def _sorgula(self, barkod):
        bekle = BEKLEME_SN * random.uniform(0.7, 1.3) - (time.time() - self.son_sorgu)
        if self.son_sorgu and bekle > 0:
            await asyncio.sleep(bekle)
        await self._ac()
        await self._sayfayi_hazirla()

        # Barkod kutuya rakam rakam yazılır.
        await self.cdp.degerlendir(
            "(() => { const k = [...document.querySelectorAll('input[placeholder=\"Gönderi takip no giriniz\"],"
            " input[name=\"value\"]')].find(e => e.offsetParent !== null); k.focus(); k.select(); return 1; })()")
        for r in barkod:
            await self.cdp.cmd("Input.insertText", {"text": r})
            await asyncio.sleep(random.uniform(0.06, 0.14))
        konum = await self.cdp.degerlendir(_KUTU_JS)
        if not konum:
            raise RuntimeError("SORGULA düğmesi bulunamadı")

        # Sayfanın kendi takip isteğinin yanıtı beklenir.
        loop = asyncio.get_running_loop()
        bitti = loop.create_future()
        istekler, yanitlar = set(), {}

        def dinle(method, p):
            rid = p.get("requestId")
            if method == "Network.requestWillBeSent":
                req = p.get("request") or {}
                if TAKIP_YOLU in req.get("url", "") and req.get("method") == "POST":
                    istekler.add(rid)
            elif method == "Network.responseReceived" and rid in istekler:
                yanitlar[rid] = (p.get("response") or {}).get("status")
            elif method == "Network.loadingFinished" and rid in yanitlar and not bitti.done():
                bitti.set_result(rid)
            elif method == "Network.loadingFailed" and rid in istekler and not bitti.done():
                bitti.set_exception(RuntimeError(p.get("errorText") or "istek başarısız"))

        self.cdp._dinleyiciler.append(dinle)
        try:
            await self._tikla(konum["x"], konum["y"])
            try:
                rid = await asyncio.wait_for(bitti, ZAMAN_ASIMI_SN)
            except asyncio.TimeoutError:
                metin = await self.cdp.degerlendir("document.body ? document.body.innerText : ''") or ""
                if DOGRULAMA_HATASI in metin:
                    raise RuntimeError("PTT doğrulaması tamamlanamadı; biraz sonra yeniden deneyin")
                raise RuntimeError("PTT yanıt vermedi")
        finally:
            self.cdp._dinleyiciler.remove(dinle)
        if yanitlar.get(rid) != 200:
            raise RuntimeError(f"PTT HTTP {yanitlar.get(rid)}")
        govde = await self.cdp.cmd("Network.getResponseBody", {"requestId": rid})
        ham = govde.get("body") or ""
        if govde.get("base64Encoded"):
            import base64
            ham = base64.b64decode(ham).decode("utf-8", "replace")
        veri = json.loads(ham)
        veri = veri[0] if isinstance(veri, list) and veri else veri
        if not isinstance(veri, dict):
            raise RuntimeError("PTT beklenmeyen yanıt biçimi")
        return veri


_TARAYICI = None


def _tarayici():
    global _TARAYICI
    if _TARAYICI is None:
        _TARAYICI = _PttTarayici()
    return _TARAYICI


# ── Yanıtın özeti (UyapMasa/modules/barkod_sorgu.py _ptt_durum_ve_tarih / ptt_teslim_sekli ile aynı kurallar) ──
def _tr_kucuk(s):
    s = str(s or "").replace("I", "ı").replace("İ", "i").lower()
    return s.translate(str.maketrans("çğıöşü", "cgiosu"))


def _tarih_bicimle(ham):
    s = str(ham or "").strip()
    return f"{s[6:8]}/{s[4:6]}/{s[0:4]}" if len(s) == 8 and s.isdigit() else s


_TESLIM_SEKILLERI = (
    ("21/2", "Muhtara teslim (21/2 – MERNİS adresi)"),
    ("ihtiyar heyeti", "İhtiyar heyeti/azaya teslim (21. md.)"),
    ("muhtara", "Muhtara teslim (21. md.)"),
    ("bizzat", "Muhataba bizzat teslim"),
    ("ayni konutta yakina", "Aynı konutta yakına teslim"),
    ("daimi calisana", "İşyerinde daimi çalışana teslim"),
    ("amirine", "İşyerindeki amirine teslim"),
    ("kapisina yapistirma", "Kapıya yapıştırma (35. md.)"),
)
_ARA_HAREKETLER = ("gondericisine teslim edildi", "koy dagitimina tabi bekliyor")


def _detay_temizle(s):
    return re.sub(r"\s+/(\s+.*)?$", "", (s or "").strip()).strip()


def ozet(veri):
    """PTT ham yanıtını ekranda gösterilecek biçime çevirir."""
    sonuc = {"kayit": bool(veri.get("errorState")), "durum": "", "tarih": "", "teslim_sekli": "",
             "hareketler": [], "kabul": {}}
    if not sonuc["kayit"]:
        sonuc["durum"] = veri.get("errorMessage") or "Kayıt yok"
        return sonuc
    son = veri.get("sondurum") or {}
    hareket = [h for h in (veri.get("hareketDongu") or []) if isinstance(h, dict)]
    if son.get("son_durum_aciklama"):
        t = son.get("teslim_tarihi")
        if not t or str(t).strip() == "0":
            t = son.get("son_islem_tarihi")
        sonuc["durum"], sonuc["tarih"] = son["son_durum_aciklama"], _tarih_bicimle(t)
    elif hareket:
        h = hareket[-1]
        sonuc["durum"] = h.get("aciklama") or veri.get("errorMessage") or "Bilinmeyen durum"
        sonuc["tarih"] = f"{h.get('tarih', '')} {h.get('saat', '')}".strip()
    durum = _tr_kucuk(sonuc["durum"])
    teslim, iade = "teslim edildi" in durum, "iade" in durum
    if teslim or iade:
        detaylar = [_detay_temizle(h.get("islem_detay")) for h in hareket]
        detaylar = [d for d in detaylar if d and _tr_kucuk(d) not in _ARA_HAREKETLER]
        if teslim:
            for d in reversed(detaylar):
                etiket = next((e for a, e in _TESLIM_SEKILLERI if a in _tr_kucuk(d)), None)
                if etiket:
                    sonuc["teslim_sekli"] = etiket
                    break
        if not sonuc["teslim_sekli"] and detaylar:
            sonuc["teslim_sekli"] = detaylar[-1]
    for h in hareket:
        yer = " / ".join(str(h[k]).strip() for k in ("islem_yeri", "merkez", "birim", "ilce", "il")
                         if h.get(k) and str(h[k]).strip())
        sonuc["hareketler"].append({"tarih": f"{h.get('tarih', '')} {h.get('saat', '')}".strip(),
                                    "aciklama": h.get("aciklama") or "", "yer": yer,
                                    "detay": _detay_temizle(h.get("islem_detay"))})
    k = veri.get("kabul") or {}
    sonuc["kabul"] = {"tarih": _tarih_bicimle(k.get("kabul_tarihi")),
                      "gonderici": k.get("gonderici") or "", "alici": k.get("alici") or ""}
    return sonuc


async def sorgula(barkod):
    """{ok, barkod, ...ozet} | {ok: False, error}."""
    barkod = re.sub(r"\D", "", str(barkod or ""))
    if not BARKOD_RE.fullmatch(barkod):
        return {"ok": False, "error": "Geçersiz barkod (13 haneli olmalı)."}
    try:
        veri = await _tarayici().sorgula(barkod)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "barkod": barkod, "error": str(e) or type(e).__name__}
    return dict(ozet(veri), ok=True, barkod=barkod)
