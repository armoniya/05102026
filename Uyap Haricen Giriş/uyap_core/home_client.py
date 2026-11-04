#!/usr/bin/env python3
"""
UYAP Ev/İstemci (bağlantı alan taraf)
-------------------------------------
İstemci bilgisayarda çalışır. İki işi var:

  1. 127.0.0.1:8800'de küçük bir YEREL HTTP sunucusu açar. Tarayıcı UYAP'ı bu adresten
     kullanır (http://127.0.0.1:8800/giris).

  2. Ofise iki yoldan ulaşmayı dener; HANGİSİ uygunsa onu kullanır:
       • YEREL AĞ (LAN): ofis aynı ağdaysa signaling onun yerel IP'lerini bildirir; bu
         IP'lere doğrudan (tünelsiz) bağlanır — en hızlı yol.
       • DIŞ AĞ (WebRTC): ofis uzaktaysa signaling üzerinden buluşup bir WebRTC DataChannel
         açar ve istekleri DOĞRUDAN ofise tüneller.

UYAP trafiği signaling'den geçmez; ofisle istemci arasında doğrudan akar. UYAP oturum
çerezleri istemciye hiç gelmez (ofiste server-side kalır).

Çalıştırma (istemci):
    pip install aiortc aiohttp websockets httpx
    python home_client.py --signaling wss://senin-sunucun:9000/ws --room <ODA> --password <PAROLA>
    # sonra tarayıcıda: http://127.0.0.1:8800/giris
"""

import os
import sys
import uuid
import json
import base64
import hashlib
import asyncio
import argparse

import httpx
import websockets
from aiohttp import web
from aiortc import RTCPeerConnection, RTCConfiguration, RTCSessionDescription

from .p2p_wire import Reassembler, send_message, load_ice_servers, fetch_ice_servers, HEARTBEAT_INTERVAL, AGENT_PREFIX
from .yerel_kapi import tarayici_reddi
from . import lan_tls
from . import is_listesi
from . import uyap_proxy as _yp  # yerel jeton + tek kullanımlık tarayıcı girişi (ofisle AYNI mekanizma)

_YEREL_ADRESLER = {"127.0.0.1", "::1", "localhost"}
# Ofise (tünel/LAN) İLETİLMEYECEK istek başlıkları: üyenin yerel yetki bilgileri ofise sızmasın.
_ILETILMEZ = {"x-uyap-local-token", "cookie", "authorization"}


def _yerel_yetki(request) -> bool:
    """Üye bilgisayarındaki yerel istemci yetkili mi: program jetonu ya da tarayıcı çerezi."""
    tok = request.headers.get("X-Uyap-Local-Token", "")
    if tok and _yp.GW_LOCAL_TOKEN and _yp.secrets.compare_digest(tok, _yp.GW_LOCAL_TOKEN):
        return True
    cerez = request.cookies.get(_yp.YEREL_CEREZ, "")
    return bool(cerez) and _yp.secrets.compare_digest(cerez, _yp.GW_BROWSER_SECRET)


def _yerel_giris_sayfasi(status=403):
    return web.Response(status=status, text=_yp._YEREL_GIRIS_SAYFASI, content_type="text/html",
                        charset="utf-8", headers={"Cache-Control": "no-store"})

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REQUEST_TIMEOUT = 120.0   # büyük UDF/PDF indirmeleri için cevap bekleme süresi

# P2P (WebRTC) bu süre içinde AÇILMAZSA sunucu üzerinden aktarmaya (WS-relay) düşülür.
# CGNAT/simetrik NAT arkasında STUN tek başına yetmez; TURN da yoksa P2P hiç kurulamaz.
# Ofis ajanı relay_hello/relay yolunu zaten destekler — fallback olmadan istemci sonsuza
# kadar "kanal hazır değil" hatasında kalıyordu.
RELAY_FALLBACK_TIMEOUT = float(os.environ.get("UYAP_RELAY_FALLBACK", "12"))

# ── Yönlendirme durumu (LAN mı tünel mi) ──────────────────────────────────────────────
use_lan = False
lan_ip = None
lan_port = None
# LAN isteklerinde kullanılacak Basic-Auth (kullanıcı, parola). Ofis LAN bileti dağıttıysa
# parola o bilettir (üyenin kendi parolası ofis proxy'sinde doğrulanamaz — satıcı
# sunucusunda durur). Bilet + sertifika parmak izi yoksa LAN kullanılmaz (bkz. ensure_probed).
lan_auth = None
# LAN TLS: ofis sertifikasına sabitlenmiş SSL bağlamı (lan_tls.sabitli_baglam). Düz HTTP LAN yok.
lan_ssl = None
room_user = ""
room_password = ""

has_probed = False
probe_lock = asyncio.Lock()


def reset_lan_state():
    global use_lan, lan_ip, lan_port, lan_auth, lan_ssl, has_probed
    use_lan = False
    lan_ip = None
    lan_port = None
    lan_auth = None
    lan_ssl = None
    has_probed = False


async def ensure_probed(msg, start_offer_func):
    """Ofisin yerel IP'lerini dener: ulaşılırsa LAN'ı seç, ulaşılamazsa WebRTC teklifini başlat.
    Tek seferlik (has_probed) ve kilitli; aynı anda gelen start/joined'ler çift denemesin."""
    global use_lan, lan_ip, lan_port, lan_auth, lan_ssl, has_probed
    local_ips = msg.get("local_ips")
    port = msg.get("port", 8800)
    # LAN bileti: ofis paylaşımı yerel ağa açtıysa signaling üzerinden "<bilet>.<parmakizi>"
    # olarak gelir; bilet Basic-Auth parolasıdır, parmak izi ofisin LAN TLS sertifikasını
    # sabitler (lan_tls). Parmak izi yoksa ofis eski sürümdür ve LAN'ı düz HTTP'dir → parola
    # ağda açık gitmesin diye LAN DENENMEZ, tünel kullanılır (bulgu 4.2).
    bilet, izi = lan_tls.bilet_ayir(msg.get("lan_token") or "")
    auth = (room_user, bilet)
    if not izi:
        local_ips = []

    async with probe_lock:
        if has_probed:
            # Daha önce karar verildi; LAN seçilmediyse tünel açılmalı.
            if msg.get("type") == "start" and not use_lan:
                await start_offer_func()
            return

        if not local_ips:
            # Ofis yerel IP (ya da şifreli LAN kapısı) bildirmedi → doğrudan dış ağ tüneli.
            if msg.get("type") == "start":
                has_probed = True
                use_lan = False
                print("[SİSTEM] Ofis şifreli yerel ağ adresi bildirmedi. Dış ağ tüneli (WebRTC) kuruluyor.")
                await start_offer_func()
            return

        success = False

        async def probe_ip(ip):
            # Önce sertifika parmak izi doğrulanır; bilet ancak sabitlenmiş bağlamla gönderilir.
            ctx = await lan_tls.sabitli_baglam(ip, port, izi)
            if ctx is None:
                return None
            url = f"https://{ip}:{port}/__ping__"
            try:
                async with httpx.AsyncClient(timeout=1.5, verify=ctx) as client:
                    resp = await client.get(url, auth=auth)
                    if resp.status_code == 200:
                        return ip, ctx
            except Exception:
                pass
            return None

        tasks = [probe_ip(ip) for ip in local_ips]
        for completed_task in asyncio.as_completed(tasks):
            result = await completed_task
            if result:
                lan_ip, lan_ssl = result
                lan_port = port
                lan_auth = auth
                use_lan = True
                success = True
                print(f"[SİSTEM] Ofis yerel ağda bulundu ({lan_ip}:{lan_port}). Doğrudan LAN bağlantısı kullanılıyor.")
                break

        has_probed = True
        if not success:
            use_lan = False
            print("[SİSTEM] Ofise yerel ağdan ulaşılamadı. Dış ağ tüneli (WebRTC) kuruluyor.")
            await start_offer_func()


class Tunnel:
    """Aktif DataChannel'ı ve bekleyen istek future'larını tutar; ofise istek tüneller."""

    def __init__(self):
        self.channel = None
        self.reasm = Reassembler()
        self.pending = {}           # id -> Future((meta, body))
        self.ready = asyncio.Event()
        self._hb_task = None        # boştayken kanalı canlı tutan kalp atışı görevi

    def attach(self, channel):
        """Yeni bir kanalı bağla (önceki bağlantı koptuysa yenisiyle değiştir). Kanal bir
        WebRTC DataChannel ya da WSRelayChannel olabilir (ikisi de aynı arayüzü sunar).
        Eski kanalın geciken open/close olayları yeni kanalı bozmasın diye her işleyici
        'hâlâ aktif kanal ben miyim' kontrolü yapar."""
        self.channel = channel
        self.reasm = Reassembler()

        @channel.on("open")
        def _open():
            if self.channel is not channel:
                return
            self.ready.set()
            print("[+] Kanal açık; tarayıcı istekleri ofise tünelleniyor.")
            self._start_heartbeat()

        @channel.on("message")
        def _msg(message):
            if self.channel is not channel:
                return
            try:
                msg = self.reasm.feed(message)
            except Exception as e:
                print(f"[!] Çerçeve çözme hatası: {e}")
                return
            if not msg:
                return
            if msg.kind == "pong":
                return  # boşta canlılık yanıtı; başka işlem gerekmez
            if msg.kind == "res":
                fut = self.pending.pop(msg.id, None)
                if fut and not fut.done():
                    fut.set_result((msg.meta, msg.body))

        @channel.on("close")
        def _close():
            if self.channel is not channel:
                return
            self.ready.clear()
            self._stop_heartbeat()
            self._fail_all("Kanal kapandı.")

    def _start_heartbeat(self):
        self._stop_heartbeat()
        self._hb_task = asyncio.ensure_future(self._heartbeat())

    def _stop_heartbeat(self):
        if self._hb_task is not None:
            self._hb_task.cancel()
            self._hb_task = None

    async def _heartbeat(self):
        """Boştayken bile kanalı (ve NAT eşlemesini) canlı tutmak için düzenli ping yollar.
        Hiç UYAP isteği akmasa da HEARTBEAT_INTERVAL'da bir küçük bir çerçeve gider; ofis
        'pong' ile yanıtlar. İki yönde de trafik olduğu için router'ın UDP eşlemesi düşmez."""
        try:
            while self.channel is not None and getattr(self.channel, "readyState", "closed") == "open":
                try:
                    await send_message(self.channel, uuid.uuid4().hex, "ping", {}, b"")
                except Exception:
                    break  # kanal kapanmış olabilir; reconnect ayrı yönetilir
                await asyncio.sleep(HEARTBEAT_INTERVAL)
        except asyncio.CancelledError:
            pass

    def detach(self):
        self.ready.clear()
        self._stop_heartbeat()
        self.channel = None
        self._fail_all("Bağlantı sıfırlandı.")

    def _fail_all(self, reason):
        for fut in self.pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError(reason))
        self.pending.clear()

    async def request(self, meta, body):
        """Bir HTTP isteğini ofise yollar, cevabı (status, headers, body) bekler."""
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=30)
        except asyncio.TimeoutError:
            raise ConnectionError("Ofis bağlantısı kurulamadı (kanal hazır değil).")
        mid = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self.pending[mid] = fut
        try:
            await send_message(self.channel, mid, "req", meta, body)
            res_meta, res_body = await asyncio.wait_for(fut, timeout=REQUEST_TIMEOUT)
        finally:
            self.pending.pop(mid, None)
        return res_meta.get("status", 502), res_meta.get("headers", {}), res_body


class WSRelayChannel:
    """Signaling WebSocket'ini istemci tarafında bir DataChannel gibi gösterir
    (office_agent.WSRelaySender'ın ev karşılığı): p2p_wire ikili çerçevelerini base64'leyip
    {"type":"relay","b64":...} TEXT mesajı olarak signaling üzerinden ofise taşır. Tunnel
    değişmeden çalışır (attach/on/send/bufferedAmount arayüzü DataChannel ile aynı).

    P2P kurulamayınca (TURN yok + simetrik NAT/CGNAT) SON ÇARE yoludur: veri satıcı
    sunucusundan GEÇER (yalnızca bu modda) ama bağlantı her ağda çalışır."""

    def __init__(self, ws):
        self.ws = ws
        self.readyState = "connecting"
        self.bufferedAmount = 0
        self._handlers = {}
        self._q = asyncio.Queue()
        self._task = asyncio.ensure_future(self._writer())

    def on(self, event):
        def deco(fn):
            self._handlers[event] = fn
            return fn
        return deco

    def _emit(self, event, *a):
        fn = self._handlers.get(event)
        if fn is not None:
            fn(*a)

    def send(self, frame):
        self.bufferedAmount += len(frame)
        self._q.put_nowait(frame)

    async def _writer(self):
        while True:
            frame = await self._q.get()
            if frame is None:
                break
            try:
                await self.ws.send(json.dumps(
                    {"type": "relay", "b64": base64.b64encode(frame).decode("ascii")}))
            except Exception as e:
                print(f"[!] Relay gönderim hatası: {e}")
            finally:
                self.bufferedAmount -= len(frame)

    def mark_open(self):
        """Ofisten relay_ready gelince çağrılır; Tunnel'ın 'open' işleyicisini tetikler."""
        if self.readyState != "open":
            self.readyState = "open"
            self._emit("open")

    def feed(self, frame: bytes):
        """Ofisten gelen bir relay çerçevesini Tunnel'ın 'message' işleyicisine verir."""
        self._emit("message", frame)

    def close(self):
        if self.readyState != "closed":
            self.readyState = "closed"
            self._q.put_nowait(None)
            self._emit("close")


# --------------------------------------------------------------------------------------
# Yerel HTTP sunucusu: tarayıcı -> (LAN ya da tünel) -> ofis -> UYAP
# --------------------------------------------------------------------------------------
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static_cache")

# Parmak izli/sabit statik varlıklar: tarayıcıya uzun ömürlü Cache-Control verilir,
# böylece tekrar gezinmelerde istek 8800'e (ve tünele) hiç gelmez.
STATIC_ASSET_EXTS = (".js", ".css", ".woff2", ".woff", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".gif")


def is_static_asset(path: str) -> bool:
    pl = path.lower()
    return "static/" in pl or any(pl.endswith(e) for e in STATIC_ASSET_EXTS)


def apply_browser_cache(resp, path: str):
    """Statik varlıklara uzun tarayıcı önbelleği uygular; UYAP'ın no-cache başlıklarını ezer."""
    if is_static_asset(path):
        resp.headers["Cache-Control"] = "public, max-age=86400"
        resp.headers.pop("Pragma", None)
        resp.headers.pop("Expires", None)


def should_cache(method: str, path: str) -> bool:
    if method.upper() != "GET":
        return False
    # İş-kuyruğu kontrol yolları ASLA önbelleğe alınmaz (durum sorguları taze dönmeli).
    if path.lstrip("/").startswith(AGENT_PREFIX):
        return False
    path_lower = path.lower()
    if "static/" in path_lower:
        return True
    if any(path_lower.endswith(ext) for ext in [".js", ".css", ".woff2", ".woff", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".gif"]):
        return True
    # HTML shell paths (root, no extension, or .html/.jsp)
    if path == "" or path_lower in ("index.html", "index.jsp", "giris"):
        return True
    # If the last segment of the path doesn't contain a dot, it's a SPA page route
    last_segment = path_lower.split("/")[-1]
    if last_segment and "." not in last_segment:
        return True
    return False


def get_cached_response(path: str):
    if not os.path.exists(CACHE_DIR):
        return None
    h = hashlib.md5(path.encode("utf-8"), usedforsecurity=False).hexdigest()  # önbellek dosya adı
    cache_path = os.path.join(CACHE_DIR, h)
    meta_path = cache_path + ".meta"
    if os.path.exists(cache_path) and os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            with open(cache_path, "rb") as f:
                body = f.read()
            return meta["status"], meta["headers"], body
        except Exception:
            pass
    return None


def save_response_to_cache(path: str, status: int, headers: dict, body: bytes):
    if status != 200:
        return
    os.makedirs(CACHE_DIR, exist_ok=True)
    h = hashlib.md5(path.encode("utf-8"), usedforsecurity=False).hexdigest()  # önbellek dosya adı
    cache_path = os.path.join(CACHE_DIR, h)
    meta_path = cache_path + ".meta"
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"status": status, "headers": headers}, f, ensure_ascii=False)
        with open(cache_path, "wb") as f:
            f.write(body)
    except Exception:
        pass


def make_app(tunnel: Tunnel):
    app = web.Application(client_max_size=256 * 1024 * 1024)

    async def handle(request: web.Request):
        # Tarayıcı kaynaklı saldırılar (DNS rebinding / başka sitenin isteği) — ofis proxy'sindeki
        # require_auth ile aynı kurallar. Bu yerel sunucuda kimlik doğrulama YOK; koruma olmazsa
        # herhangi bir web sitesi kullanıcının tarayıcısı üzerinden UYAP'a erişebilirdi.
        red = tarayici_reddi(request.headers, request.method)
        if red:
            print(f"[!] Reddedildi ({request.method} {request.rel_url.path}): {red}")
            return web.Response(status=403, text=red)
        # Yerel yetki (güvenlik taraması 2026-10-01): eskiden bu bilgisayardaki HER program ofis
        # tüneli üzerinden UYAP'ı kullanabiliyordu. Artık ofis proxy'siyle aynı kural: program
        # jetonu (X-Uyap-Local-Token) ya da "Tarayıcıyı Aç" tek kullanımlık bağlantısının çerezi.
        if (request.remote or "") not in _YEREL_ADRESLER:
            return web.Response(status=403, text="Yalnız bu bilgisayardan erişilebilir.")
        if request.rel_url.path == "/__uyap_yerel__/kod" and request.method == "POST":
            tok = request.headers.get("X-Uyap-Local-Token", "")
            if not (tok and _yp.GW_LOCAL_TOKEN and _yp.secrets.compare_digest(tok, _yp.GW_LOCAL_TOKEN)):
                return web.Response(status=403, text="Yerel yetki jetonu gerekli.")
            return web.json_response({"kod": _yp._giris_kodu_uret()}, headers={"Cache-Control": "no-store"})
        if request.rel_url.path == "/__uyap_yerel__/giris" and request.method == "GET":
            if not _yp._giris_kodu_kullan(request.query.get("k", "")):
                return _yerel_giris_sayfasi()
            resp = web.Response(status=303, headers={
                "Location": _yp._guvenli_hedef(request.query.get("hedef", "/giris")), "Cache-Control": "no-store"})
            resp.set_cookie(_yp.YEREL_CEREZ, _yp.GW_BROWSER_SECRET, httponly=True, samesite="Strict", path="/")
            return resp
        if request.rel_url.path == is_listesi.DOSYA_AC_PATH and request.method == "GET":
            # Yerel yetki aranmaz (çerez çapraz-site gezinmede gelmez); sayfa yalnız yönlendirir.
            return web.Response(body=is_listesi.DOSYA_AC_SAYFASI, content_type="text/html", charset="utf-8",
                                headers={"Cache-Control": "no-store"})
        if request.rel_url.path == is_listesi.PTT_PATH and request.method == "GET":
            # Aynı kural; PTT sorgusunu sayfa kendisi (aynı köken, çerezle) yapar.
            return web.Response(body=is_listesi.PTT_SAYFASI, content_type="text/html", charset="utf-8",
                                headers={"Cache-Control": "no-store"})
        if not _yerel_yetki(request):
            if request.method in ("GET", "HEAD") and "text/html" in request.headers.get("Accept", ""):
                return _yerel_giris_sayfasi()
            return web.Response(status=403, text="Yerel yetki gerekli: program jetonu ya da 'Tarayıcıyı Aç'.")
        body = await request.read()
        # İş Listesi köprüsü: tünele girmez, BU bilgisayarda giriş yapan kişinin hesabıyla
        # cokluuyap.com'a gider (ofis ajanı isteği kimin yaptığını bilemez).
        if is_listesi.is_path(request.rel_url.path):
            st, hdr, out = await is_listesi.handle(request.method, request.rel_url.path,
                                                  request.rel_url.query_string, body)
            return web.Response(status=st, body=out, headers=hdr)
        host = request.headers.get("Host", "127.0.0.1:8800")
        path = request.rel_url.path.lstrip("/")
        query = request.rel_url.query_string
        # Sürüm parametreli varlıklar (?v=123) güncellemeden sonra bayat servis edilmesin
        # diye query'yi de önbellek anahtarına dahil ediyoruz.
        cache_key = path + ("?" + query if query else "")
        method = request.method

        meta = {
            "method": method,
            "path": request.rel_url.path,
            "query": query,
            "headers": {k: v for k, v in request.headers.items() if k.lower() not in _ILETILMEZ},
            "proxy_base": f"http://{host}",
        }

        cacheable = should_cache(method, path)
        if cacheable:
            cached = get_cached_response(cache_key)
            if cached is not None:
                status, headers, cached_body = cached

                # Browser cache validation
                req_headers = dict(request.headers)
                if_modified_since = req_headers.get("If-Modified-Since") or req_headers.get("if-modified-since")
                if_none_match = req_headers.get("If-None-Match") or req_headers.get("if-none-match")

                cached_last_modified = headers.get("last-modified") or headers.get("Last-Modified")
                cached_etag = headers.get("etag") or headers.get("ETag")

                use_304 = False
                if if_none_match and cached_etag and if_none_match.strip() == cached_etag.strip():
                    use_304 = True
                elif if_modified_since and cached_last_modified and if_modified_since.strip() == cached_last_modified.strip():
                    use_304 = True

                if use_304:
                    resp = web.Response(status=304)
                else:
                    resp = web.Response(status=status, body=cached_body)

                for k, v in headers.items():
                    if k.lower() in ("content-length", "transfer-encoding"):
                        continue
                    resp.headers[k] = v
                apply_browser_cache(resp, path)
                return resp

        t0 = asyncio.get_running_loop().time()
        try:
            if use_lan:
                # Doğrudan LAN: ofisin FastAPI proxy'sine httpx + Basic-Auth ile bağlan.
                target_url = f"https://{lan_ip}:{lan_port}{request.rel_url.path}"
                if query:
                    target_url += f"?{query}"
                req_headers = {k: v for k, v in request.headers.items()
                               if k.lower() != "host" and k.lower() not in _ILETILMEZ}
                # Ofis, UYAP URL'lerini tarayıcının gördüğü origin'e (127.0.0.1:port) yeniden
                # yazsın diye gerçek origin'i X-Forwarded-Host ile bildiriyoruz; aksi halde
                # ofisin LAN IP'sine rewrite eder, tarayıcı oraya parolasız gidip 401 alırdı.
                req_headers["X-Forwarded-Host"] = host
                req_headers["X-Forwarded-Proto"] = "http"
                async with httpx.AsyncClient(timeout=120.0, verify=lan_ssl) as client:
                    resp_val = await client.request(
                        method=method, url=target_url, headers=req_headers,
                        content=body, auth=lan_auth,
                    )
                    status = resp_val.status_code
                    headers = dict(resp_val.headers)
                    out_body = resp_val.content
            else:
                status, headers, out_body = await tunnel.request(meta, body)

            elapsed = asyncio.get_running_loop().time() - t0
            yol = "LAN" if use_lan else "tünel"
            print(f"[*] Istek ({yol}): {meta['method']} {meta['path']} -> Durum: {status} | Sure: {elapsed:.3f} sn")

            if cacheable and status == 200:
                save_response_to_cache(cache_key, status, headers, out_body)
        except httpx.HTTPError as e:
            print(f"[!] LAN Hatası ({meta['method']} {meta['path']}): {e}")
            return web.Response(status=503, text=f"Yerel ağ üzerinden ofise bağlanılamadı: {e}")
        except ConnectionError as e:
            print(f"[!] Hata ({meta['method']} {meta['path']}): Ofis baglantisi kurulamadi ({e})")
            return web.Response(status=503, text=f"Ofis bilgisayarına bağlanılamadı: {e}")
        except asyncio.TimeoutError:
            print(f"[!] Zaman Asimi ({meta['method']} {meta['path']})")
            return web.Response(status=504, text="Ofis zamanında yanıt vermedi.")

        resp = web.Response(status=status, body=out_body)
        for k, v in headers.items():
            if k.lower() in ("content-length", "transfer-encoding"):
                continue  # gövdeden otomatik hesaplanır
            resp.headers[k] = v
        apply_browser_cache(resp, path)
        return resp

    app.router.add_route("*", "/{tail:.*}", handle)
    return app


# --------------------------------------------------------------------------------------
# Signaling + WebRTC (offerer)
# --------------------------------------------------------------------------------------
async def run_signaling(args, tunnel: Tunnel, ice_servers):
    """Signaling'e bağlı kal; 'start'/'joined' gelince ofisi yokla (LAN mı tünel mi),
    'answer' gelince tüneli tamamla. P2P zamanında açılmazsa WS-relay'e düş."""
    backoff = 1
    while True:
        pc = None
        relay = None
        fallback_task = None
        try:
            async with websockets.connect(
                args.signaling, max_size=4 * 1024 * 1024,
                ping_interval=20, ping_timeout=60, close_timeout=10,
            ) as ws:
                reset_lan_state()
                join_msg = {"role": "home", "room": args.room}
                if getattr(args, "office_code", ""):
                    join_msg["office_code"] = args.office_code
                if getattr(args, "password", None):
                    join_msg["password"] = args.password
                await ws.send(json.dumps(join_msg))
                print(f"[+] Signaling'e bağlandı: {args.signaling} (ofis bekleniyor).")
                backoff = 1

                async def start_relay():
                    """Sunucu üzerinden aktarmaya (WS-relay) geç: P2P denemesini kapat,
                    signaling ws'ini kanal olarak Tunnel'a bağla ve ofise relay_hello yolla.
                    Ofisin relay_ready yanıtı mesaj döngüsünde kanalı 'open' yapar."""
                    nonlocal pc, relay
                    if tunnel.ready.is_set():
                        return  # P2P (ya da önceki relay) zaten çalışıyor
                    if relay is not None and relay.readyState != "closed":
                        return  # relay el sıkışması zaten sürüyor
                    print("[!] P2P kurulamadı; sunucu üzerinden aktarma (WS-relay) deneniyor.")
                    if pc is not None:
                        try:
                            await pc.close()
                        except Exception:
                            pass
                        pc = None
                    relay = WSRelayChannel(ws)
                    tunnel.attach(relay)
                    await ws.send(json.dumps({"type": "relay_hello"}))

                def schedule_relay_fallback():
                    """P2P açılmazsa RELAY_FALLBACK_TIMEOUT sonra relay'e düşen bekçi görevi."""
                    nonlocal fallback_task
                    if fallback_task is not None:
                        fallback_task.cancel()

                    async def _watch():
                        try:
                            await asyncio.wait_for(tunnel.ready.wait(),
                                                   timeout=RELAY_FALLBACK_TIMEOUT)
                        except asyncio.TimeoutError:
                            await start_relay()
                        except asyncio.CancelledError:
                            pass

                    fallback_task = asyncio.ensure_future(_watch())

                async def start_offer():
                    nonlocal pc
                    # Signaling yeniden bağlandığında sağlıklı bağlantıyı boşuna yıkma.
                    if relay is not None and relay.readyState == "open":
                        return
                    if pc is not None and pc.connectionState == "connected":
                        return
                    if pc is not None:
                        await pc.close()
                    pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
                    # ordered=False: büyük bir PDF/UDF inerken arkasındaki küçük AJAX
                    # istekleri SCTP sırasında beklemesin (head-of-line blocking yok).
                    # Kanal yine güvenilir (retransmit) kalır; Reassembler parçaları seq ile
                    # birleştirdiği için sırasız teslim güvenli.
                    channel = pc.createDataChannel("uyap", ordered=False)
                    tunnel.attach(channel)
                    this_pc = pc  # kapanış (closure) bu pc'yi görsün; dış pc relay'de None olabilir

                    @this_pc.on("connectionstatechange")
                    async def _state():
                        print(f"[*] P2P durumu: {this_pc.connectionState}")
                        if this_pc.connectionState in ("failed", "closed", "disconnected"):
                            # Yalnızca hâlâ bu P2P kanalı aktifse tüneli düşür; relay'e
                            # geçildiyse (channel değişti) eski pc'nin kapanışı ona dokunmasın.
                            if tunnel.channel is channel:
                                tunnel.detach()
                            # P2P kesin başarısızsa bekçiyi bekleme, hemen relay'e düş.
                            if this_pc.connectionState == "failed":
                                await start_relay()

                    offer = await pc.createOffer()
                    await pc.setLocalDescription(offer)  # ICE toplanır; SDP "tam" olur
                    await ws.send(json.dumps({
                        "type": "offer",
                        "sdp": pc.localDescription.sdp,
                        "sdptype": pc.localDescription.type,
                    }))
                    print("[*] Teklif (offer) gönderildi; ofisin yanıtı bekleniyor…")
                    schedule_relay_fallback()

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        mtype = msg.get("type")
                        if mtype == "start":
                            # Teklifi YALNIZCA "start"/"joined(peer_present)" yolu tetikler;
                            # ensure_probed önce LAN'ı dener, olmazsa start_offer'ı çağırır.
                            await ensure_probed(msg, start_offer)
                        elif mtype == "joined":
                            print(f"[*] Odaya katıldı (ofis hazır: {msg.get('peer_present')}).")
                            if msg.get("peer_present"):
                                asyncio.create_task(ensure_probed(msg, start_offer))
                        elif mtype == "answer":
                            # Yalnızca kendi teklifimizi beklerken answer'ı uygula.
                            if pc is not None and pc.signalingState == "have-local-offer":
                                await pc.setRemoteDescription(RTCSessionDescription(
                                    sdp=msg["sdp"], type=msg.get("sdptype", "answer")))
                            else:
                                state = pc.signalingState if pc else "yok"
                                print(f"[*] Beklenmeyen answer (durum={state}); yok sayıldı.")
                        elif mtype == "relay_ready":
                            # Ofis relay el sıkışmasını kabul etti; kanal artık açık.
                            if relay is not None and tunnel.channel is relay:
                                relay.mark_open()
                                print("[+] Sunucu üzerinden aktarma (WS-relay) kuruldu.")
                        elif mtype == "relay":
                            # Ofisten gelen bir cevap çerçevesi (base64 ikili).
                            if relay is not None and tunnel.channel is relay:
                                relay.feed(base64.b64decode(msg["b64"]))
                        elif mtype == "peer_left":
                            print("[*] Ofis ayrıldı; bağlantı bekleniyor.")
                            if fallback_task is not None:
                                fallback_task.cancel()
                                fallback_task = None
                            if relay is not None:
                                relay.close()
                                relay = None
                            tunnel.detach()
                            reset_lan_state()
                            if pc is not None:
                                await pc.close()
                                pc = None
                        elif mtype == "error":
                            print(f"[!] Signaling reddetti: {msg.get('error')}")
                            return
                    except Exception as e:
                        # Tek bir bozuk mesaj signaling bağlantısını düşürmesin.
                        print(f"[!] Signaling mesaj hatası ({e}); bağlantı korunuyor.")
        except asyncio.CancelledError:
            tunnel.detach()
            if pc is not None:
                try:
                    await pc.close()
                except Exception:
                    pass
            raise
        except Exception as e:
            print(f"[!] Signaling bağlantısı koptu ({e}); {backoff}s sonra yeniden denenecek.")
            tunnel.detach()
            reset_lan_state()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
        finally:
            # Relay signaling ws'i üzerinde yaşar; ws düştüyse relay de ölüdür.
            if fallback_task is not None:
                fallback_task.cancel()
            if relay is not None:
                try:
                    relay.close()
                except Exception:
                    pass
            if pc is not None:
                try:
                    await pc.close()
                except Exception:
                    pass


async def amain(args):
    global room_user, room_password, probe_lock
    # Her "Bağlantıyı Al" yeni bir event loop'ta başlar; önceki döngüye bağlı kilit/durum kalmasın.
    probe_lock = asyncio.Lock()
    reset_lan_state()
    room_user = args.room
    room_password = args.password or ""
    # İş Listesi köprüsü: BU bilgisayarda giriş yapan kişinin hesabıyla (ofisin kimliği kullanılmaz).
    is_listesi.baglantidan_configure(args.signaling, getattr(args, "office_code", ""), args.room, args.password)

    tunnel = Tunnel()
    # ICE'ı satıcı sunucusundan çek (efemeral TURN dahil); ulaşılamazsa yerel ayara düşer.
    if args.signaling and ("127.0.0.1" in args.signaling or "localhost" in args.signaling):
        ice_servers = []
    else:
        ice_servers = await fetch_ice_servers(args.signaling)

    # Yerel yetki jetonu: bu bilgisayardaki programlar (UyapMasa modülleri) jetonu kullanıcıya
    # özel dosyadan okur; aynı süreçteki urllib istemcilerine otomatik eklenir (ofisle aynı).
    _yp._init_local_token()

    # Yerel HTTP sunucusunu başlat (sadece 127.0.0.1).
    app = make_app(tunnel)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, args.host, args.port)
    await site.start()
    print(f"[+] Yerel sunucu http://{args.host}:{args.port} hazır.")
    print("[*] UYAP'ı programdaki 'Tarayıcıyı Aç' düğmesiyle açın (tek kullanımlık güvenli bağlantı; "
          f"düz http://{args.host}:{args.port}/giris doğrudan açılmaz).")

    try:
        await run_signaling(args, tunnel, ice_servers)
    finally:
        await runner.cleanup()


def main():
    parser = argparse.ArgumentParser(description="UYAP istemcisi (LAN + WebRTC).")
    parser.add_argument("--signaling", default=os.environ.get("UYAP_SIGNALING"),
                        help="Signaling sunucusu, ör. wss://senin-sunucun:9000/ws")
    parser.add_argument("--room", default=os.environ.get("UYAP_ROOM"),
                        help="Oda/lisans anahtarı (ofisle aynı olmalı)")
    parser.add_argument("--password", default=os.environ.get("UYAP_PASSWORD"),
                        help="Paylaşım parolası (LAN Basic-Auth + signaling için)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Yerel dinleme adresi (varsayılan 127.0.0.1; sadece bu makine).")
    parser.add_argument("--port", type=int, default=8800, help="Yerel port (varsayılan 8800)")
    args = parser.parse_args()

    if not args.signaling or not args.room:
        print("[!] --signaling ve --room zorunlu (ya da UYAP_SIGNALING / UYAP_ROOM).")
        sys.exit(2)

    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        print("\n[!] İstemci kapatıldı.")


if __name__ == "__main__":
    main()
