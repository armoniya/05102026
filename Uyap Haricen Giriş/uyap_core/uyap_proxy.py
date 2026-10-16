#!/usr/bin/env python3
"""
UYAP Office-Side Gateway (Transparent Reverse Proxy)
----------------------------------------------------
Runs on the OFFICE machine that holds the e-imza smart card. It:

  1. Logs in to UYAP through the local e-imza client (reusing uyap_giris_dışarıdan.py).
  2. Keeps that single session alive: it is probed on every cycle and re-authenticated
     automatically (the card must stay inserted) whenever UYAP drops it.
  3. Transparently forwards authorized requests coming from home to UYAP, injecting the
     office session cookies *server-side*. UYAP therefore only ever sees ONE shared
     session leaving ONE IP (the office), so concurrent requests land on the same F5
     backend and never trip the "another session is active" guard.

Why a transparent proxy instead of a hand-built UI: UYAP is a large SPA with hundreds of
endpoints. By forwarding everything, the browser at home loads UYAP's own real frontend
and we never have to reverse-engineer each operation. The home browser never receives the
UYAP session cookies — they live only in this process, so the session can't leak to the
client side.

SECURITY: the session this process holds is a bearer credential for the entire judicial
system and client case data. Never expose this port directly to the internet. Put it
behind a private channel (WireGuard / VPN) and keep the Basic-Auth credentials secret.
Only people the e-imza holder has authorized should be able to reach it.

Run (office):
    pip install fastapi "uvicorn[standard]" httpx
    set GW_PASS=some-strong-secret            (Windows cmd)     # password for home clients
    $env:GW_PASS = "some-strong-secret"       (PowerShell)
    python uyap_proxy.py --host 0.0.0.0 --port 8800 --cert-id <certificateId>

Connect (home):
    Point a browser (over the VPN) at  http://<office-vpn-ip>:8800/giris
    and log in with GW_USER / GW_PASS when the browser prompts.
"""

import os
import sys
import json
import time
import asyncio
import secrets
import threading
import argparse
import ipaddress
import mimetypes
import subprocess
import urllib.request
import importlib.util
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from ._runtime import interactive_write, batch_write, WRITE_METHODS, uyap_istek_izi

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# --------------------------------------------------------------------------------------
# Load the e-imza login module. Its filename contains a Turkish character, so we load it
# by path instead of `import` to avoid any identifier/encoding surprises.
# --------------------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_LOGIN_PATH = os.path.join(_HERE, "uyap_giris_dışarıdan.py")
if os.path.exists(_LOGIN_PATH):
    _spec = importlib.util.spec_from_file_location("uyap_login", _LOGIN_PATH)
    uyap_login = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(uyap_login)
else:
    # Derlenmiş kurulum (kurulum/uyapmasa_paketle.py): modül ASCII adla .pyd olarak gelir.
    uyap_login = importlib.import_module(
        (__package__ + "." if __package__ else "") + "uyap_giris_disaridan")

UYAP_BASE = "https://avukat.uyap.gov.tr"
UYAP_HOST = "avukat.uyap.gov.tr"

# ── Yerel Django arayüzü (uyap_gecit/web, ayrı süreç, 127.0.0.1:8000) ─────────────────
# uyap_app.py paylaşım başlarken bu ayrı süreci kendisi başlatır (start_gecit_server).
# Bu önekle gelen istekler UYAP'a HİÇ gitmez; aynı tünelin üzerinden (ikinci bir P2P
# bağlantı AÇMADAN) ham HTTP proxy'lenir. Django FORCE_SCRIPT_NAME=/__gecit__ ile
# kendi ürettiği tüm bağlantıları (statik, yönlendirme, {% url %}) bu önekle üretir.
GECIT_PREFIX = "__gecit__"
GECIT_BASE = "http://127.0.0.1:8000"
# Django projesinin STATİK kökü — statik varlıklar Django'ya HİÇ gitmez, doğrudan
# diskten servis edilir (bkz handle_gecit_proxy docstring: runserver'ın kendi statik
# sunumu FORCE_SCRIPT_NAME ile bir arada çalışmıyor).
GECIT_STATIC_ROOT = os.path.join(
    os.path.dirname(_HERE), "uyap_gecit", "web", "gecit", "static")


def is_gecit_path(path: str) -> bool:
    path = (path or "").lstrip("/")
    return path == GECIT_PREFIX or path.startswith(GECIT_PREFIX + "/")


def _serve_gecit_static(rel_path: str) -> tuple:
    """/__gecit__/static/... varlıklarını Django'ya UĞRAMADAN diskten verir.
    NEDEN: Django 6'da STATIC_URL göreliyse (ör. 'static/') ayarlar FORCE_SCRIPT_NAME'i
    otomatik önüne ekler (django/conf/__init__.py _add_script_prefix), ama runserver'ın
    StaticFilesHandler'ı isteği HAM PATH_INFO ile eşleştirip request.path'i (script_name+
    path_info) dosya yoluna çevirir — bu ikisi FORCE_SCRIPT_NAME'le çakışıp ya 404 ya da
    çift önekli SuspiciousFileOperation üretir (canlıda denenip doğrulandı). Django'nun
    kendi statik sunumunu hiç devreye sokmadan tek statik kök (gecit/static/) diskten
    okunarak bu çakışma tamamen atlanır."""
    norm = os.path.normpath(rel_path)
    if norm.startswith("..") or os.path.isabs(norm):
        return 404, {"Content-Type": "text/plain; charset=utf-8"}, b"Not found"
    fs_path = os.path.join(GECIT_STATIC_ROOT, norm)
    if not os.path.isfile(fs_path):
        return 404, {"Content-Type": "text/plain; charset=utf-8"}, b"Not found"
    ctype = mimetypes.guess_type(fs_path)[0] or "application/octet-stream"
    with open(fs_path, "rb") as f:
        data = f.read()
    return 200, {"Content-Type": ctype, "Cache-Control": "public, max-age=3600"}, data


async def handle_gecit_proxy(method: str, path: str, query: str, body: bytes,
                              headers: dict) -> tuple:
    """/__gecit__/... isteklerini yerel Django arayüzüne ham HTTP ile iletir.
    BİLEREK _build_forward_headers/_build_response_headers KULLANMAZ: onlar UYAP'ın
    TEK PAYLAŞILAN oturumu için cookie/set-cookie'yi bilerek siler (oturum tarayıcıda
    değil ofiste tutulur). Django arayüzü ise normal tarayıcı-başına oturum kullanır;
    çerezler İKİ yönde de aynen geçmeli, yoksa giriş ekranı/CSRF hiç çalışmaz."""
    sub_path = (path or "").lstrip("/")[len(GECIT_PREFIX):].lstrip("/")
    if sub_path.startswith("static/") and method.upper() in ("GET", "HEAD"):
        return _serve_gecit_static(sub_path[len("static/"):])
    target = f"{GECIT_BASE}/{sub_path}"
    if query:
        target += "?" + query
    drop_req = HOP_BY_HOP | {"host", "content-length"}
    fwd_headers = {k: v for k, v in (headers or {}).items() if k.lower() not in drop_req}
    fwd_headers["Host"] = "127.0.0.1:8000"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.request(method, target, content=body, headers=fwd_headers)
    except httpx.HTTPError as e:
        return (502, {"Content-Type": "text/plain; charset=utf-8"},
                f"Geçit arayüzüne ulaşılamadı: {e}".encode("utf-8"))
    drop_resp = HOP_BY_HOP | {"content-length"}
    out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in drop_resp}
    return resp.status_code, out_headers, resp.content

# Hop-by-hop headers must never be forwarded (RFC 7230 §6.1).
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}
# Request headers we replace/manage ourselves.
# x-uyap-local-token: yerel yetki jetonu UYAP'a İLETİLMEZ (eskiden iletiliyordu; güvenlik taraması 2026-10-01).
DROP_REQUEST = HOP_BY_HOP | {"host", "cookie", "authorization", "content-length",
                            "accept-encoding", "x-forwarded-host", "x-forwarded-proto",
                            "x-uyap-local-token"}
# Response headers we strip: cookies stay server-side; CSP/HSTS/X-Frame would block the
# proxied origin; encoding/length are recomputed after any body rewrite.
DROP_RESPONSE = HOP_BY_HOP | {
    "content-encoding", "content-length", "set-cookie",
    "content-security-policy", "content-security-policy-report-only",
    "strict-transport-security", "x-frame-options",
}
# Content types whose absolute UYAP URLs we rewrite back to the proxy origin.
TEXT_TYPES = (
    "text/", "application/javascript", "application/x-javascript",
    "application/json", "application/xml", "image/svg",
)

# Globals wired up in main() before the server starts serving.
gw = None              # type: GatewaySession | None
IDLE_INTERVAL = 5      # seconds between idle session probes
GW_USER = os.environ.get("GW_USER", "uyap")
GW_PASS = os.environ.get("GW_PASS")  # required; refuse requests if unset

# Yerel-yetki jetonu (aynı makinedeki API istemcileri için). DNS-rebinding/CSRF guard'ı
# tarayıcı tabanlı saldırıları eler; ama localhost'a soket açabilen TARAYICI-DIŞI bir süreç
# hâlâ parolasız tam yetki alabiliyordu. Bunu daraltmak için ofis açılışta rastgele bir jeton
# üretir, yalnızca bu kullanıcının okuyabileceği bir dosyaya (+ aynı süreçteki istemciler için
# ortam değişkenine) yazar; yerel Python istemcileri (SorguMotoru vb.) bu jetonu
# 'X-Uyap-Local-Token' başlığıyla sunar. Jetonu OKUYAMAYAN bir süreç (farklı kullanıcı / düşük
# bütünlük) artık localhost yolundan tam yetki alamaz. configure() içinde kurulur.
GW_LOCAL_TOKEN = None

# LAN-direct bileti. Üye istemcilerin (home_client) parolaları SATICI sunucusunda durur;
# bu proxy onları doğrulayamaz (GW_USER/GW_PASS yalnızca ofisin ana kimliğidir). Ofis ajanı
# paylaşımı yerel ağa açarken oturumluk bir bilet üretir (office_agent), bilet signaling
# üzerinden YALNIZCA kimliği doğrulanmış oda üyelerine dağıtılır ve burada Basic-Auth
# parolası olarak kabul edilir. Böylece "signaling'e girebilen üye" ile "LAN proxy'ye
# girebilen istemci" aynı küme olur. None iken bu yol tamamen kapalıdır.
GW_LAN_TICKET = None


class GatewaySession:
    """Owns the single authenticated UYAP session shared by all home clients."""

    def __init__(self, login_args, verify_ssl):
        self.login_args = login_args
        self.verify_ssl = verify_ssl
        self.cert_id = login_args.cert_id
        # follow_redirects=False so UYAP's login redirects surface to us instead of being
        # silently chased; the home browser handles navigation.
        self.client = httpx.AsyncClient(
            verify=verify_ssl,
            timeout=httpx.Timeout(30.0, connect=15.0),
            follow_redirects=False,
        )
        self.lock = asyncio.Lock()          # serialize re-login so cookies aren't swapped mid-flight
        self.last_request_ts = time.monotonic()
        self.logged_in = False

    def touch(self):
        self.last_request_ts = time.monotonic()

    async def _probe(self):
        """Cheap liveness check against the authenticated endpoint. BİLEREK
        kilitsiz: `relogin()` (bkz. altta) bunu bazen `interactive_write()`'ın
        ZATEN tuttuğu `UYAP_WRITE_LOCK` içinden çağırır (login-duvarı bir yazma
        isteğinin ORTASINDA tespit edilirse) — burada da kilit almaya kalksa
        asyncio.Lock yeniden-girilebilir OLMADIĞINDAN kilitlenip (deadlock)
        sunucuyu tamamen durdururdu. Gerçek düzeltme: bu fonksiyonu DEĞİL,
        onu kilitsiz bağlamdan çağıran `_keepalive_loop`'u kilitle (bkz. altta)."""
        try:
            r = await self.client.post(
                f"{UYAP_BASE}/get_avukat_id.ajx",
                json={},
                headers={"Content-Type": "application/json", "Referer": f"{UYAP_BASE}/"},
            )
        except Exception:
            return False
        if r.status_code != 200:
            return False
        txt = r.text.strip()
        return bool(txt) and txt.isdigit()

    async def _login_blocking(self):
        """Runs the synchronous e-imza login in a worker thread, then copies the fresh
        cookies into the async client so all forwarding uses the new session."""
        loop = asyncio.get_running_loop()
        try:
            session, cert_id = await loop.run_in_executor(
                None, uyap_login.perform_login, self.login_args, self.verify_ssl, self.cert_id
            )
        except SystemExit as se:
            raise RuntimeError(f"Giris basarisiz (SystemExit: {se.code})") from se
        except Exception as e:
            raise RuntimeError(f"Giris hatasi: {e}") from e

        self.cert_id = cert_id
        self.client.cookies.clear()
        for c in session.cookies:
            self.client.cookies.set(c.name, c.value, domain=c.domain or UYAP_HOST, path=c.path or "/")
        # Çerezler yalnızca bellekte (self.client.cookies) tutulur; diske YAZILMAZ.
        # İstemciye de hiç gönderilmez — oturum tamamen ofis tarafında kalır.
        self.logged_in = True

    async def ensure_alive(self):
        """Probe and, only if needed, re-authenticate. Used at startup and by the idle loop."""
        if self.logged_in and await self._probe():
            return
        await self.relogin()

    async def relogin(self):
        """Re-authenticate under a lock. A second waiter re-checks first so we don't
        log in twice in a row when several requests detected the drop simultaneously."""
        async with self.lock:
            if self.logged_in and await self._probe():
                return
            print("[*] Oturum yok/koptu — e-imza ile yeniden giriş yapılıyor...")
            await self._login_blocking()
            print("[+] Oturum hazır.")


def _looks_like_login_wall(resp):
    """Heuristic: did UYAP answer with 'you are not logged in' instead of real content?"""
    if resp.status_code in (401, 403):
        return True
    if resp.status_code in (301, 302, 303, 307, 308):
        loc = resp.headers.get("location", "").lower()
        if "/giris" in loc or "login" in loc:
            return True
    return False


def _build_forward_headers(request: Request) -> dict:
    headers = {k: v for k, v in request.headers.items() if k.lower() not in DROP_REQUEST}
    headers["Host"] = UYAP_HOST
    # Accept-Encoding'i set ETMİYORUZ: httpx kendi varsayılanını (gzip/deflate/br) ekler ve
    # gövdeyi şeffafça açar (resp.content çözülmüş gelir), böylece UYAP→ofis transferi
    # sıkıştırılmış akar ama yeniden yazım yine düz metin üzerinde çalışır.
    return headers


def _rewrite_body(content_type: str, body: bytes, proxy_base: str, inject: bool = True) -> bytes:
    """Rewrite absolute UYAP URLs to the proxy origin so follow-up calls stay proxied.
    Only applied to text-like payloads; binary (PDF/UDF downloads) passes through untouched.

    inject=False: yalnızca URL'leri yeniden yaz, istemci tarafı script'i (IndexedDB/FakeXHR)
    GÖMME. Service Worker tabanlı tarayıcı istemcisi (web_server.py) zaten tüm istekleri
    ağ katmanında yakalayıp tünelediği için o script gereksiz ve onunla çakışır."""
    if not content_type or not any(t in content_type for t in TEXT_TYPES):
        return body
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body
    host_only = proxy_base.split("://", 1)[-1]
    text = text.replace("https://avukat.uyap.gov.tr", proxy_base)
    text = text.replace("//avukat.uyap.gov.tr", "//" + host_only)

    # Inject loginState and Stale-While-Revalidate script to bypass client-side login check and render pages instantly
    if inject and "text/html" in content_type.lower():
        script = r"""<script>
(function() {
    // 1. IndexedDB SWR Cache Setup
    const DB_NAME = 'UYAP_SWR_CACHE';
    const DB_VERSION = 1;
    const STORE_NAME = 'responses';

    function getDB() {
        return new Promise((resolve) => {
            const req = indexedDB.open(DB_NAME, DB_VERSION);
            req.onupgradeneeded = (e) => {
                const db = e.target.result;
                if (!db.objectStoreNames.contains(STORE_NAME)) {
                    db.createObjectStore(STORE_NAME);
                }
            };
            req.onsuccess = (e) => resolve(e.target.result);
            req.onerror = () => resolve(null);
        });
    }

    // Açık db tutamacı üzerinde ham oku/yaz (oturum-kapsamı kurulurken kullanılır;
    // bunlar cacheReady'yi BEKLEMEZ — yoksa init kilitlenir).
    function rawGet(db, key) {
        return new Promise((resolve) => {
            try {
                const tx = db.transaction(STORE_NAME, 'readonly');
                const req = tx.objectStore(STORE_NAME).get(key);
                req.onsuccess = () => resolve(req.result);
                req.onerror = () => resolve(null);
            } catch (e) { resolve(null); }
        });
    }
    function rawSet(db, key, value) {
        return new Promise((resolve) => {
            try {
                const tx = db.transaction(STORE_NAME, 'readwrite');
                const req = tx.objectStore(STORE_NAME).put(value, key);
                req.onsuccess = () => resolve(true);
                req.onerror = () => resolve(false);
            } catch (e) { resolve(false); }
        });
    }

    // GÜVENLİK (bulgu #5): Cache'i tarayıcı OTURUMUNA bağla. Hassas UYAP yanıtları artık
    // "sonsuza kadar kalıcı" değil; yalnızca aktif oturum süresince yaşar. sessionStorage
    // sekme kapanınca silinir → yeni sekme/tarayıcı/SONRAKİ KULLANICI farklı SID üretir →
    // miras kalan kalıcı IndexedDB ilk erişimde tamamen SİLİNİR (ortak/halka açık makinede
    // veri sızıntısını engeller). Aynı oturum içi navigasyon/yeniden yükleme hızı korunur.
    const SID_KEY = 'UYAP_SWR_SID';
    let SID = null;
    try { SID = sessionStorage.getItem(SID_KEY); } catch (e) {}
    if (!SID) {
        SID = (window.crypto && crypto.randomUUID) ? crypto.randomUUID()
              : (Date.now().toString(36) + Math.random().toString(36).slice(2));
        try { sessionStorage.setItem(SID_KEY, SID); } catch (e) {}
    }

    function purgeCache() {
        return new Promise((resolve) => {
            try {
                const r = indexedDB.deleteDatabase(DB_NAME);
                r.onsuccess = r.onerror = r.onblocked = () => resolve();
            } catch (e) { resolve(); }
        });
    }

    // Oturum mührünü doğrula: depodaki SID mührü mevcut oturumunkiyle uyuşmuyorsa cache
    // ÖNCEKİ bir oturuma aittir → tümünü sil, taze aç, yeni mührü bas. Tüm dbGet/dbSet bunu
    // bekler; böylece sızdıran eski veri hiçbir okumada görünmez.
    const cacheReady = (async () => {
        let db = await getDB();
        if (!db) return;
        let storedSid = null;
        try { storedSid = await rawGet(db, '__sid__'); } catch (e) {}
        if (storedSid !== SID) {
            try { db.close(); } catch (e) {}
            await purgeCache();
            db = await getDB();
            if (db) { try { await rawSet(db, '__sid__', SID); } catch (e) {} }
        }
    })();

    function dbGet(key) {
        return new Promise(async (resolve) => {
            await cacheReady;
            const db = await getDB();
            if (!db) { resolve(null); return; }
            resolve(await rawGet(db, key));
        });
    }

    function dbSet(key, value) {
        return new Promise(async (resolve) => {
            await cacheReady;
            const db = await getDB();
            if (!db) { resolve(false); return; }
            resolve(await rawSet(db, key, value));
        });
    }

    // 2. Premium Glassmorphic Toast Notification System
    function showToast(message, action) {
        let container = document.getElementById('uyap-toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'uyap-toast-container';
            container.style.cssText = `
                position: fixed;
                bottom: 24px;
                right: 24px;
                z-index: 2147483647;
                display: flex;
                flex-direction: column;
                gap: 12px;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                pointer-events: none;
            `;
            document.body.appendChild(container);
        }
        
        if (container.querySelector(`[data-msg="${message}"]`)) {
            return;
        }
        
        const toast = document.createElement('div');
        toast.setAttribute('data-msg', message);
        toast.style.cssText = `
            background: rgba(18, 18, 24, 0.85);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            color: #f3f4f6;
            padding: 14px 24px;
            border-radius: 16px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25);
            display: flex;
            align-items: center;
            gap: 16px;
            transform: translateY(20px);
            opacity: 0;
            transition: all 0.5s cubic-bezier(0.16, 1, 0.3, 1);
            pointer-events: auto;
        `;
        
        const textSpan = document.createElement('span');
        textSpan.textContent = message;
        textSpan.style.cssText = `
            font-size: 14px;
            font-weight: 500;
            letter-spacing: -0.01em;
        `;
        toast.appendChild(textSpan);
        
        if (action) {
            const btn = document.createElement('button');
            btn.textContent = 'Yenile';
            btn.style.cssText = `
                background: linear-gradient(135deg, #4f46e5, #6366f1);
                color: #ffffff;
                border: none;
                padding: 8px 16px;
                border-radius: 10px;
                cursor: pointer;
                font-size: 13px;
                font-weight: 600;
                box-shadow: 0 4px 12px rgba(79, 70, 229, 0.3);
                transition: all 0.2s ease;
                white-space: nowrap;
            `;
            btn.onmouseover = () => {
                btn.style.transform = 'translateY(-1px)';
                btn.style.boxShadow = '0 6px 16px rgba(79, 70, 229, 0.4)';
            };
            btn.onmouseout = () => {
                btn.style.transform = 'translateY(0)';
                btn.style.boxShadow = '0 4px 12px rgba(79, 70, 229, 0.3)';
            };
            btn.onclick = () => {
                action();
                toast.remove();
            };
            toast.appendChild(btn);
        }
        
        container.appendChild(toast);
        
        requestAnimationFrame(() => {
            toast.style.transform = 'translateY(0)';
            toast.style.opacity = '1';
        });
        
        setTimeout(() => {
            toast.style.transform = 'translateY(20px)';
            toast.style.opacity = '0';
            setTimeout(() => { toast.remove(); }, 500);
        }, 8000);
    }

    // 3. Smart Comparison Logic
    function isIgnoredKey(key) {
        const ignored = [
            'sistemtarihi', 'sorgutarihi', 'sorgusaati', 'time', 'timestamp', 
            'date', 'token', 'sectoken', 'transactionid', 'nonce', 'uuid', 
            'sorgu_saati', 'sorgu_tarihi', 'sistem_tarihi', 'last_sync', 'sync_time'
        ];
        return ignored.includes(key.toLowerCase());
    }

    function isDeepEqual(a, b) {
        if (a === b) return true;
        if (a && b && typeof a === 'object' && typeof b === 'object') {
            if (a.constructor !== b.constructor) return false;
            if (Array.isArray(a)) {
                if (a.length !== b.length) return false;
                for (let i = 0; i < a.length; i++) {
                    if (!isDeepEqual(a[i], b[i])) return false;
                }
                return true;
            }
            const keysA = Object.keys(a).filter(key => !isIgnoredKey(key));
            const keysB = Object.keys(b).filter(key => !isIgnoredKey(key));
            if (keysA.length !== keysB.length) return false;
            for (let key of keysA) {
                if (!b.hasOwnProperty(key)) return false;
                if (!isDeepEqual(a[key], b[key])) return false;
            }
            return true;
        }
        return false;
    }

    function hasDataChanged(oldText, newText) {
        if (oldText === newText) return false;
        try {
            const objA = JSON.parse(oldText);
            const objB = JSON.parse(newText);
            return !isDeepEqual(objA, objB);
        } catch (e) {
            return oldText !== newText;
        }
    }

    // 4. Cache Filter and Blacklist
    function isCacheable(url, method) {
        if (!url || !method) return false;
        if (method.toUpperCase() !== 'POST' && method.toUpperCase() !== 'GET') return false;
        const url_lower = url.toLowerCase();
        
        // Must be a UYAP JSON/text API endpoint
        if (!url_lower.includes('.ajx') && !url_lower.includes('.uyap')) return false;

        // İkili belgeler (PDF/zip/UDF/TIFF) ASLA önbelleğe alınmaz: önbellek yanıtı resp.text()
        // ile saklayıp 'application/json' diye geri verdiği için ikinci açılışta belge bozuk
        // geliyordu (canlı, 2026-10-14: 70 KB zip → 128 KB bozuk metin).
        if (url_lower.includes('view_document') || url_lower.includes('_document_')) return false;

        // Blacklist for state-modifying actions, auth, live ws and balance checks
        const blacklist = [
            'kaydet', 'gonder', 'sil', 'ekle', 'guncelle', 'imza', 'odeme',
            'harc', 'tahsilat', 'baslat', 'onayla', 'reddet', 'iptal',
            'save', 'delete', 'update', 'add', 'sign', 'payment', 'confirm', 'cancel',
            'create', 'send', 'upload', 'download', 'cikis', 'logout',
            'sifre', 'pin', 'parola', 'odemeYap', 'kart', 'nats', 'bakiye', 'bakiyesi'
        ];
        for (let word of blacklist) {
            if (url_lower.includes(word)) return false;
        }
        return true;
    }

    // GÜVENLİK (bulgu #5): Oturum kapanışı (çıkış) işareti — görülürse hassas cache'i hemen
    // sil. Oturum-mührü zaten sonraki oturumda temizler; bu, aynı makinede oturum kapatıp
    // bırakan kullanıcı için ek koruma.
    function isLogout(url) {
        if (!url) return false;
        const u = url.toLowerCase();
        return u.includes('cikis') || u.includes('logout') || u.includes('oturumkapat');
    }

    // 5. Bypass loginState check in localStorage
    localStorage.setItem('loginState', 'true');

    // 6. XMLHttpRequest Interceptor
    const RealXHR = window.XMLHttpRequest;
    
    function FakeXHR() {
        const xhr = new RealXHR();
        const self = this;
        
        let isFake = false;
        let fakeResponse = null;
        let method = '';
        let url = '';
        let requestHeaders = [];
        const listeners = {};
        
        const props = [
            'readyState', 'status', 'statusText', 'responseText', 'responseXML',
            'response', 'responseURL', 'upload', 'timeout', 'withCredentials',
            'responseType'  // ikili belgeler (blob/arraybuffer) gerçek XHR'a iletilmeli
        ];
        
        props.forEach(prop => {
            Object.defineProperty(self, prop, {
                get: function() {
                    if (isFake && fakeResponse) {
                        if (prop === 'readyState') return 4;
                        if (prop === 'status') return 200;
                        if (prop === 'statusText') return 'OK';
                        if (prop === 'responseText' || prop === 'response') return fakeResponse.responseText;
                        if (prop === 'responseXML') return null;
                    }
                    return xhr[prop];
                },
                set: function(val) {
                    try {
                        xhr[prop] = val;
                    } catch (e) {}
                },
                configurable: true,
                enumerable: true
            });
        });
        
        const eventProps = [
            'onreadystatechange', 'onload', 'onloadstart', 'onloadend',
            'onprogress', 'onerror', 'onabort', 'ontimeout'
        ];
        
        const appHandlers = {};
        eventProps.forEach(evt => {
            Object.defineProperty(self, evt, {
                get: function() {
                    return appHandlers[evt] || null;
                },
                set: function(val) {
                    appHandlers[evt] = val;
                    if (!isFake) {
                        xhr[evt] = function() {
                            if (appHandlers[evt]) {
                                appHandlers[evt].apply(self, arguments);
                            }
                        };
                    }
                },
                configurable: true,
                enumerable: true
            });
        });
        
        self.addEventListener = function(type, listener, options) {
            if (!listeners[type]) listeners[type] = [];
            listeners[type].push({ listener, options });
            
            const wrappedListener = function(e) {
                if (isFake) return;
                return listener.apply(self, arguments);
            };
            
            xhr.addEventListener(type, wrappedListener, options);
        };
        
        self.removeEventListener = function(type, listener, options) {
            if (listeners[type]) {
                listeners[type] = listeners[type].filter(l => l.listener !== listener);
            }
            xhr.removeEventListener(type, listener, options);
        };
        
        self.open = function(m, u, async, user, password) {
            method = m;
            url = u;
            return xhr.open.apply(xhr, arguments);
        };
        
        self.setRequestHeader = function(header, value) {
            requestHeaders.push({ header, value });
            return xhr.setRequestHeader.apply(xhr, arguments);
        };
        
        self.getResponseHeader = function(header) {
            if (isFake) {
                if (header.toLowerCase() === 'content-type') return 'application/json';
                return null;
            }
            return xhr.getResponseHeader.apply(xhr, arguments);
        };
        
        self.getAllResponseHeaders = function() {
            if (isFake) {
                return 'content-type: application/json\\r\\n';
            }
            return xhr.getAllResponseHeaders.apply(xhr);
        };
        
        self.abort = function() {
            return xhr.abort.apply(xhr);
        };
        
        self.overrideMimeType = function(mime) {
            return xhr.overrideMimeType.apply(xhr, arguments);
        };
        
        self.send = async function(data) {
            if (isLogout(url)) { purgeCache(); }
            if (isCacheable(url, method)) {
                const cacheKey = `xhr::${url}::${data || ''}`;
                const cached = await dbGet(cacheKey);
                
                if (cached) {
                    isFake = true;
                    fakeResponse = cached;

                    setTimeout(() => {
                        triggerFakeEvents();
                    }, 1);
                    
                    // Perform background revalidation using a separate originalFetch call!
                    const revalidate = async () => {
                        try {
                            const headersObj = {};
                            requestHeaders.forEach(h => {
                                headersObj[h.header] = h.value;
                            });
                            
                            const response = await originalFetch(url, {
                                method: method,
                                headers: headersObj,
                                body: data
                            });
                            
                            if (response.status === 200) {
                                const newText = await response.text();
                                if (hasDataChanged(cached.responseText, newText)) {
                                    await dbSet(cacheKey, { responseText: newText });
                                }
                            }
                        } catch(e) {}
                    };
                    revalidate();
                    
                    // Do NOT call native xhr.send()! We return immediately.
                    return;
                } else {
                    xhr.addEventListener('load', async function() {
                        if (xhr.status === 200) {
                            await dbSet(cacheKey, { responseText: xhr.responseText });
                        }
                    });
                }
            }

            return xhr.send.apply(xhr, arguments);
        };
        
        function triggerFakeEvents() {
            if (appHandlers['onreadystatechange']) {
                try { appHandlers['onreadystatechange'].call(self); } catch(e) {}
            }
            if (appHandlers['onload']) {
                try { appHandlers['onload'].call(self); } catch(e) {}
            }
            if (listeners['load']) {
                listeners['load'].forEach(l => {
                    try { l.listener.call(self, new Event('load')); } catch(e) {}
                });
            }
            if (listeners['readystatechange']) {
                listeners['readystatechange'].forEach(l => {
                    try { l.listener.call(self, new Event('readystatechange')); } catch(e) {}
                });
            }
            if (appHandlers['onloadend']) {
                try { appHandlers['onloadend'].call(self); } catch(e) {}
            }
        }
    }
    
    FakeXHR.prototype = RealXHR.prototype;
    for (let key in RealXHR) {
        if (RealXHR.hasOwnProperty(key)) {
            FakeXHR[key] = RealXHR[key];
        }
    }
    FakeXHR.toString = function() { return 'function XMLHttpRequest() { [native code] }'; };
    window.XMLHttpRequest = FakeXHR;

    // 7. Fetch API Interceptor
    // window'a BAĞLA: native fetch, this !== window ile çağrılırsa Chrome "Illegal
    // invocation" fırlatır. Belge görüntüleyici fetch'i farklı bir bağlamda çağırdığı
    // için bind şart; bağlı fonksiyon apply/call'daki this'i yok sayar.
    const originalFetch = window.fetch.bind(window);
    window.fetch = async function(resource, init) {
        const url = typeof resource === 'string' ? resource : resource.url;
        const method = init && init.method ? init.method : 'GET';

        if (isLogout(url)) { purgeCache(); }
        if (isCacheable(url, method)) {
            const data = (init && init.body) ? init.body : '';
            const cacheKey = `fetch::${url}::${data}`;
            const cached = await dbGet(cacheKey);
            
            if (cached) {
                originalFetch(resource, init).then(async (resp) => {
                    if (resp.status === 200) {
                        const text = await resp.text();
                        const cachedVal = await dbGet(cacheKey);
                        if (cachedVal && hasDataChanged(cachedVal.text, text)) {
                            await dbSet(cacheKey, { text });
                        }
                    }
                }).catch(() => {});
                
                return new Response(cached.text, {
                    status: 200,
                    statusText: 'OK',
                    headers: new Headers({ 'content-type': 'application/json' })
                });
            } else {
                const resp = await originalFetch(resource, init);
                if (resp.status === 200) {
                    const clone = resp.clone();
                    clone.text().then(async (text) => {
                        await dbSet(cacheKey, { text });
                    });
                }
                return resp;
            }
        }
        return originalFetch.apply(this, arguments);
    };
})();

// ── UDF E-İMZA KÖPRÜSÜ (sürükle-bırak) ───────────────────────────────────────────────
// Sağ altta HER ZAMAN duran bir alan. Kullanıcı bir .udf / .doc / .docx / .pdf dosyasını
// oraya sürükler ya da tıklayıp seçer. Dosya ofise gider; ofis gerekiyorsa UDF'ye çevirip
// e-imzayla imzalar (headless) ve imzalı .udf geri gelir. Sonra UYAP'ta bir dosya yükleme
// alanına tıklandığında imzalı UDF otomatik yerleştirilir (DataTransfer).
(function() {
    const SIGN_ENDPOINT = '/__uyap_agent__/sign_udf';
    const ACCEPT = ['.udf', '.doc', '.docx', '.pdf'];
    let staged = null;                  // {name, bytes(Uint8Array)} — imzalanmış, yüklemeye hazır
    let pending = null;                 // {name, bytes(Uint8Array)} — onay bekleyen (henüz imzalanmadı)
    let collapsed = true;               // varsayılan: küçük buton; tıklayınca açılır

    function whenBody(cb) {
        if (document.body) { cb(); return; }
        document.addEventListener('DOMContentLoaded', cb, { once: true });
    }

    // sayfa yenilense bile (SPA içi) imzalı belgeyi koru
    try {
        const s = sessionStorage.getItem('uyapStagedUdf');
        if (s) { const o = JSON.parse(s); staged = { name: o.name, bytes: Uint8Array.from(atob(o.b64), c => c.charCodeAt(0)) }; }
    } catch (e) {}

    function persistStaged() {
        try {
            if (!staged) { sessionStorage.removeItem('uyapStagedUdf'); return; }
            let bin = ''; staged.bytes.forEach(b => bin += String.fromCharCode(b));
            sessionStorage.setItem('uyapStagedUdf', JSON.stringify({ name: staged.name, b64: btoa(bin) }));
        } catch (e) {}
    }

    function setStatus(text, kind) {
        const el = document.getElementById('uyap-eimza-status');
        if (!el) return;
        el.textContent = text || '';
        el.style.color = kind === 'err' ? '#fca5a5' : (kind === 'ok' ? '#86efac' : '#cbd5e1');
    }

    function extOf(name) { const m = /\.[^.]+$/.exec((name || '').toLowerCase()); return m ? m[0] : ''; }

    // ── Açılır/kapanır görünüm ────────────────────────────────────────────────────────
    function render() {
        const widget = document.getElementById('uyap-eimza-widget');
        const fab = document.getElementById('uyap-eimza-fab');
        if (!widget || !fab) return;
        // Yalnız ONAY bekleyen belge varken açık kalmaya zorlanır. İmzalı belge hazır
        // beklerken (staged) kutu küçülebilir; düğme ✅ ile "hazır belge var" der
        // (2026-09-23: eskiden staged da zorla açık tutuyor, ekranda yer kaplıyordu).
        if (pending) collapsed = false;
        widget.style.display = collapsed ? 'none' : 'block';
        fab.style.display = collapsed ? 'flex' : 'none';
        fab.textContent = staged ? '✅' : '🖋️';
        fab.title = staged ? 'İmzalı belge hazır — UYAP yükleme alanına tıklayınca eklenir'
                           : 'E-İmza';
    }
    function expand() { collapsed = false; render(); }
    function collapse() { if (pending) return; collapsed = true; render(); }

    // ── Dosya seçilince: ÖNCE onay iste (otomatik imzalama yok) ────────────────────────
    function stageForConfirm(file) {
        if (!file) return;
        const ext = extOf(file.name);
        if (ACCEPT.indexOf(ext) === -1) {
            expand();
            setStatus('Desteklenmeyen tür: ' + (ext || '?') + ' (.udf/.doc/.docx/.pdf)', 'err');
            return;
        }
        pending = file;
        expand();
        setStatus('');
        const wrap = document.getElementById('uyap-eimza-actions');
        if (!wrap) return;
        wrap.innerHTML = '';
        const info = document.createElement('div');
        info.style.cssText = 'font-size:12px;color:#e5e7eb;margin-bottom:8px;word-break:break-all;';
        info.textContent = '📄 ' + file.name;
        const row = document.createElement('div');
        row.style.cssText = 'display:flex;gap:6px;';
        const ok = mkBtn('E-imzala', '#4f46e5', () => { const f = pending; pending = null; processFile(f); });
        const no = mkBtn('Vazgeç', 'rgba(255,255,255,0.10)', () => { pending = null; resetActions(); collapse(); });
        row.appendChild(ok); row.appendChild(no);
        wrap.appendChild(info); wrap.appendChild(row);
    }

    function mkBtn(label, bg, onclick) {
        const b = document.createElement('button');
        b.textContent = label;
        b.style.cssText = 'flex:1;background:' + bg + ';color:#fff;border:1px solid rgba(255,255,255,0.15);padding:6px 10px;border-radius:8px;cursor:pointer;font-size:12px;font-weight:600;';
        b.onclick = (ev) => { ev.stopPropagation(); onclick(); };
        return b;
    }
    function resetActions() {
        const wrap = document.getElementById('uyap-eimza-actions');
        if (wrap) wrap.innerHTML = '';
        setStatus('');
    }

    // ── İmzalama akışı (onaylandıktan sonra) ──────────────────────────────────────────
    async function processFile(file) {
        if (!file) return;
        resetActions();
        setStatus('"' + file.name + '" ofise gönderiliyor, e-imzalanıyor…');
        try {
            const buf = new Uint8Array(await file.arrayBuffer());
            const url = SIGN_ENDPOINT + '?name=' + encodeURIComponent(file.name);
            const resp = await window.fetch(url, {
                method: 'POST', body: buf,
                headers: { 'Content-Type': 'application/octet-stream' }
            });
            if (!resp.ok) {
                let msg = 'İmzalama başarısız (' + resp.status + ').';
                try { const j = await resp.json(); if (j && j.error) msg = j.error; } catch (e) {}
                setStatus(msg, 'err');
                return;
            }
            const signed = new Uint8Array(await resp.arrayBuffer());
            const signedName = file.name.replace(/\.[^.]+$/i, '') + '_imzali.udf';
            staged = { name: signedName, bytes: signed };
            persistStaged();
            setStatus('Hazır ✓ "Dosya Ekle/Yükle" alanına tıklayınca otomatik eklenecek.', 'ok');
            showDownload(signed, signedName);
            // Sonucu kısa süre göster, sonra küçük düğmeye (✅) dön; indirme için
            // düğmeye tıklayıp yeniden açılabilir.
            setTimeout(() => { if (!pending) { collapsed = true; render(); } }, 3000);
        } catch (e) {
            setStatus('Hata: ' + e, 'err');
        }
    }

    function showDownload(signed, signedName) {
        const wrap = document.getElementById('uyap-eimza-actions');
        if (!wrap) return;
        wrap.innerHTML = '';
        const b = mkBtn('İmzalıyı indir', 'rgba(255,255,255,0.12)', () => {
            const u = URL.createObjectURL(new Blob([signed], { type: 'application/octet-stream' }));
            const a = document.createElement('a'); a.href = u; a.download = signedName;
            a.setAttribute('data-uyap-internal', '1');   // kendi indirmemiz — yakalama dışı
            document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(u), 4000);
        });
        wrap.appendChild(b);
    }

    // ── Sağ alttaki açılır/kapanır sürükle-bırak alanı ────────────────────────────────
    function buildWidget() {
        if (document.getElementById('uyap-eimza-root')) return;
        const w = document.createElement('div');
        // 2026-09-23: dış kap AYRI id taşır. Eskiden iç panelle aynı id'yi
        // ('uyap-eimza-widget') paylaşıyordu; render() getElementById ile DIŞ kabı
        // bulup gizliyor, mor 🖋️ düğmesi de onunla birlikte kayboluyordu.
        w.id = 'uyap-eimza-root';
        w.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:2147483647;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;';

        // Küçük başlatıcı buton (kapalıyken)
        const fab = document.createElement('div');
        fab.id = 'uyap-eimza-fab';
        fab.title = 'E-İmza';
        fab.style.cssText = 'width:46px;height:46px;border-radius:50%;background:rgba(79,70,229,0.95);box-shadow:0 6px 20px rgba(0,0,0,0.3);display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:20px;';
        fab.textContent = '🖋️';
        fab.addEventListener('click', expand);

        // Açık widget
        const widget = document.createElement('div');
        widget.id = 'uyap-eimza-widget';
        widget.style.cssText = 'display:none;width:210px;padding:12px;border-radius:14px;background:rgba(18,18,24,0.92);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border:1px dashed rgba(255,255,255,0.18);box-shadow:0 10px 30px rgba(0,0,0,0.35);color:#f3f4f6;cursor:pointer;transition:border-color .2s,background .2s;';
        widget.innerHTML =
            '<div style="display:flex;align-items:center;justify-content:space-between;">' +
              '<div style="font-size:13px;font-weight:700;display:flex;align-items:center;gap:6px;"><span style="font-size:15px;">🖋️</span> E-İmza</div>' +
              '<span id="uyap-eimza-collapse" title="Küçült" style="font-size:16px;line-height:1;color:#9ca3af;cursor:pointer;padding:0 4px;">–</span>' +
            '</div>' +
            '<div style="font-size:11.5px;color:#cbd5e1;margin-top:6px;line-height:1.4;">Belgeyi <b>sürükle</b> ya da tıkla.<br>.udf · .doc · .docx · .pdf</div>' +
            '<div id="uyap-eimza-status" style="font-size:11.5px;margin-top:8px;min-height:14px;line-height:1.4;color:#cbd5e1;"></div>' +
            '<div id="uyap-eimza-actions" style="margin-top:8px;"></div>';

        const input = document.createElement('input');
        input.type = 'file';
        input.accept = ACCEPT.join(',');
        input.style.display = 'none';
        input.addEventListener('change', () => { if (input.files && input.files[0]) stageForConfirm(input.files[0]); input.value = ''; });
        widget.appendChild(input);

        // widget boşluğuna tıklayınca dosya seçici (buton/küçült/eylem alanına tıklayınca değil)
        widget.addEventListener('click', (ev) => {
            const t = ev.target;
            if (t && (t.id === 'uyap-eimza-collapse' || (t.closest && t.closest('#uyap-eimza-actions')))) return;
            input.click();
        });

        // sürükle-bırak widget üzerine
        ['dragenter', 'dragover'].forEach(t => widget.addEventListener(t, (ev) => {
            ev.preventDefault(); ev.stopPropagation();
            widget.style.borderColor = '#6366f1'; widget.style.background = 'rgba(79,70,229,0.18)';
        }));
        ['dragleave', 'dragend'].forEach(t => widget.addEventListener(t, (ev) => {
            ev.preventDefault(); ev.stopPropagation();
            widget.style.borderColor = 'rgba(255,255,255,0.18)'; widget.style.background = 'rgba(18,18,24,0.92)';
        }));
        widget.addEventListener('drop', (ev) => {
            ev.preventDefault(); ev.stopPropagation();
            widget.style.borderColor = 'rgba(255,255,255,0.18)'; widget.style.background = 'rgba(18,18,24,0.92)';
            const f = ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0];
            if (f) stageForConfirm(f);
        });

        w.appendChild(fab);
        w.appendChild(widget);
        document.body.appendChild(w);

        const cb = document.getElementById('uyap-eimza-collapse');
        if (cb) cb.addEventListener('click', (ev) => { ev.stopPropagation(); collapse(); });

        if (staged) { setStatus('İmzalı belge hazır — yükleme alanına tıklayın.', 'ok'); showDownload(staged.bytes, staged.name); }
        render();
    }

    // ── Dosya yükleme alanını otomatik doldur ────────────────────────────────────────
    function fillFileInput(input) {
        if (!staged) return false;
        try {
            const dt = new DataTransfer();
            dt.items.add(new File([staged.bytes], staged.name, { type: 'application/octet-stream' }));
            input.files = dt.files;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
            setStatus('İmzalı belge yükleme alanına eklendi ✓', 'ok');
            const actions = document.getElementById('uyap-eimza-actions'); if (actions) actions.innerHTML = '';
            staged = null; persistStaged();   // tek seferlik: tüketildi
            setTimeout(() => { collapsed = true; render(); }, 1500);  // tüketilince tekrar küçülebilir
            return true;
        } catch (e) { return false; }
    }
    document.addEventListener('click', function(ev) {
        const el = ev.target;
        // kendi gizli inputumuza dokunma
        if (el && el.tagName === 'INPUT' && el.type === 'file' && staged
            && !(el.closest && el.closest('#uyap-eimza-widget'))) {
            ev.preventDefault();
            ev.stopPropagation();
            fillFileInput(el);
        }
    }, true);

    // ── İndirilen belgeyi yakalayıp "imzalayayım mı?" diye sor ───────────────────────
    // UYAP'ta bir .udf/.doc/.docx/.pdf indirme bağlantısına tıklanınca: dosya normal
    // iner AMA biz de baytları çekip sağ alttaki onay akışına (stageForConfirm) sokarız.
    let _nativeFetch = null;
    function nativeFetch() {
        if (_nativeFetch) return _nativeFetch;
        try {
            const f = document.createElement('iframe');
            f.style.display = 'none';
            (document.body || document.documentElement).appendChild(f);
            _nativeFetch = f.contentWindow.fetch.bind(window);   // cache katmanına uğramayan ham fetch
            // iframe'i bilerek DOM'da bırakırız ki fetch realm'i yaşasın
        } catch (e) { _nativeFetch = window.fetch.bind(window); }
        return _nativeFetch;
    }
    function nameFromUrl(href) {
        try { const u = new URL(href, location.href); return decodeURIComponent((u.pathname.split('/').pop()) || ''); }
        catch (e) { return ''; }
    }
    async function offerSignForDownload(href, name) {
        try {
            const resp = await nativeFetch()(href, { credentials: 'include' });
            if (!resp || !resp.ok) return;
            const u8 = new Uint8Array(await resp.arrayBuffer());
            if (!u8.length) return;
            stageForConfirm(new File([u8], name, { type: 'application/octet-stream' }));
        } catch (e) {}
    }
    document.addEventListener('click', function(ev) {
        const a = ev.target && ev.target.closest && ev.target.closest('a[href]');
        if (!a || a.getAttribute('data-uyap-internal')) return;
        const href = a.href;
        if (!href || href.toLowerCase().indexOf('javascript:') === 0) return;
        let name = a.getAttribute('download') || nameFromUrl(href);
        const ext = extOf(name) || extOf(href);
        if (ACCEPT.indexOf(ext) === -1) return;
        if (!name || extOf(name) === '') name = 'belge' + ext;
        // indirme normal devam etsin; biz ek olarak imza için soralım
        offerSignForDownload(href, name);
    }, true);

    whenBody(buildWidget);
})();

// ── DOSYA SORGULAMA: Yargı Türü/Birimi otomatik aç-seç ───────────────────────────────
// Bu ekran DevExtreme SelectBox kullanır. Dosya Sorgulama -> "yargiTur"/"yargiBirim",
// dava/takip açma -> "yargiTuru"/"yargiBirimi" (id'ler ekrana göre değişir).
// İstenen davranış:
//   1) Ekran açılınca Yargı Türü açılır (kullanıcı fazladan tık yapmasın).
//   2) Bir tür seçilince Yargı Birimi (asenkron yüklenir) açılır; TEK seçenek varsa
//      (ör. İcra) otomatik seçilir, birden çoksa kullanıcının seçmesi için açık kalır.
// Native <select> programatik açılamadığından DevExtreme'in DOM'una gerçekçi pointer/mouse
// olayları gönderiyoruz (openOnFieldClick etkin). Instance API'sine bağımlı değiliz.
(function() {
    function q(id) { return document.getElementById(id); }
    function rootOf(input) { return input && input.closest ? input.closest('.dx-dropdowneditor') : null; }
    function isDisabled(input) { const r = rootOf(input); return !!(r && r.classList.contains('dx-state-disabled')); }
    function isOpen(input) { const r = rootOf(input); return !!(r && r.classList.contains('dx-dropdowneditor-active')); }

    function dispatch(el, ctor, type, extra) {
        try { el.dispatchEvent(new ctor(type, Object.assign({ bubbles: true, cancelable: true, view: window }, extra || {}))); }
        catch (e) {}
    }
    function clickLike(el) {
        if (!el) return;
        dispatch(el, window.PointerEvent || MouseEvent, 'pointerdown', { pointerId: 1, pointerType: 'mouse', isPrimary: true });
        dispatch(el, MouseEvent, 'mousedown');
        dispatch(el, window.PointerEvent || MouseEvent, 'pointerup', { pointerId: 1, pointerType: 'mouse', isPrimary: true });
        dispatch(el, MouseEvent, 'mouseup');
        dispatch(el, MouseEvent, 'click');
    }
    function openSelect(input) {
        if (!input || isDisabled(input) || isOpen(input)) return;
        const r = rootOf(input);
        const btn = r && r.querySelector('.dx-dropdowneditor-button');
        clickLike(btn || input);
    }
    function visiblePopup() {
        const ws = document.querySelectorAll('.dx-selectbox-popup-wrapper');
        for (let i = 0; i < ws.length; i++) {
            const c = ws[i].querySelector('.dx-overlay-content');
            if (c && c.offsetParent !== null) return ws[i];
        }
        return null;
    }
    function selectItem(item) { clickLike(item); }

    // Ekrana göre id'ler değişir: Dosya Sorgulama -> "yargiTur"/"yargiBirim",
    // Dava/takip açma -> "yargiTuru"/"yargiBirimi". İlk var olan eşleşmeyi kullan.
    const TUR_IDS = ['yargiTur', 'yargiTuru'];
    const BIRIM_IDS = ['yargiBirim', 'yargiBirimi'];
    function firstPresent(ids) { for (let i = 0; i < ids.length; i++) { const e = q(ids[i]); if (e) return e; } return null; }
    function birimEl() { return firstPresent(BIRIM_IDS); }
    function ilEl() { return q('il'); }   // yalnız Ceza/Cbs (yargiTur=3) iken render edilir

    // Genel: bir SelectBox'ı aç; popup dolunca TEK seçenek varsa otomatik seç, çok seçenek
    // varsa açık bırak (kullanıcı seçsin). Boşsa (liste async yükleniyor / başka alana
    // bağlı) yüklenmeyi bekler, gerçekten boşsa sessizce bırakır — boş kutuyu zorlamayız.
    // getEl her turda yeniden çözülür: React re-render edip elemanı değiştirse de takip eder.
    function handleSelect(getEl, autoSelectSingle) {
        let tries = 0;
        const iv = setInterval(function() {
            tries++;
            const input = getEl();
            if (!input || tries > 55) { clearInterval(iv); return; }   // ~10 sn
            if (isDisabled(input)) return;            // hazır/etkin olana dek bekle
            if (!isOpen(input) && !visiblePopup()) { openSelect(input); return; }
            const wrap = visiblePopup();
            if (!wrap) return;                        // popup henüz çizilmedi
            const items = wrap.querySelectorAll('.dx-list-item');
            if (items.length === 1 && autoSelectSingle !== false) { clearInterval(iv); selectItem(items[0]); return; }
            if (items.length >= 1) { clearInterval(iv); return; }   // çok seçenek: kullanıcı seçsin
            // boş: liste henüz yüklenmemiş olabilir; uzun süre boşsa bırak (kapatma).
            const empty = wrap.querySelector('.dx-empty-message');
            if (empty && tries > 45) { clearInterval(iv); }
        }, 180);
    }

    // Bir SelectBox'ı açık olana / zaman aşımına dek tekrar tekrar açmayı dener.
    // (Tek seferlik tık, DevExtreme widget'ı henüz hazır değilse kaçabiliyor.)
    function openWithRetry(getEl, maxTries) {
        let tries = 0;
        const iv = setInterval(function() {
            tries++;
            const el = getEl();
            if (!el || tries > (maxTries || 40)) { clearInterval(iv); return; }
            if (isOpen(el) || visiblePopup()) { clearInterval(iv); return; }
            if (!isDisabled(el)) openSelect(el);
        }, 150);
    }

    // Tür seçilince: Ceza/Cbs (il alanı render edilmişse) BİRİM il'e bağlı olduğundan ÖNCE
    // il'i aç; aksi halde doğrudan birimi aç (tek seçenekse — ör. İcra — otomatik seç).
    // Re-render'ın oturması için kısa gecikme.
    function afterTurChange() {
        setTimeout(function() {
            if (ilEl()) handleSelect(ilEl);          // il çok seçenekli → kullanıcı seçer
            else handleSelect(birimEl);              // birim: tek seçenekse otomatik
        }, 500);
    }
    function afterIlChange() {
        setTimeout(function() { handleSelect(birimEl); }, 400);  // il seçilince birim yüklenir
    }

    let lastTur = null;
    let lastIl = null;
    let opened = false;
    setInterval(function() {
        const t = firstPresent(TUR_IDS);
        if (!t) { lastTur = null; lastIl = null; opened = false; return; }  // ekran yok
        if (!opened) {                               // Yargı Türü kutusu ilk göründü
            opened = true;
            lastTur = t.value || '';
            const il = ilEl(); lastIl = il ? (il.value || '') : null;
            if (!t.value) openWithRetry(function() { return firstPresent(TUR_IDS); });
            return;
        }
        const ct = t.value || '';
        if (ct !== lastTur) {                         // tür değişti (kullanıcı seçti)
            lastTur = ct;
            const il = ilEl(); lastIl = il ? (il.value || '') : null;
            if (ct) afterTurChange();
            return;
        }
        const il = ilEl();                            // Ceza akışında il değişimini izle
        const ci = il ? (il.value || '') : null;
        if (ci !== lastIl) {
            lastIl = ci;
            if (ci) afterIlChange();
        }
    }, 400);
})();

// ── TÜM EVRAKI İNDİR ─────────────────────────────────────────────────────────────────
// Her dosya kartının (.dosya-sorgula-popup) başlığına UYAP'ın kendi düğme stiliyle "Tüm
// Evrakı İndir" eklenir. Tıklanınca dosya bilgisi O KARTIN React verisinden (props.data:
// dosyaId, birimAdi, dosyaNo…) okunur — tahmin yok. Ofis tüm evrakı sabit hızla, tek tek
// indirip PDF'e çevirir (bkz. uyap_core/evrak_toplu.py), ofiste klasöre kaydeder, PDF/ZIP'i
// buraya indirir. İlerleme sağ üstteki panelde (kart kapansa da) görünür. UYAP güvenlik
// doğrulaması isterse panel açılır ve doğrulamayı KULLANICI çözer — otomatik çözüm yok.
(function() {
    const EP = '/__uyap_agent__/tum_evrak';
    const BITMIS = ['bitti', 'hata', 'iptal'];
    const isler = {};          // indirme id -> {veri: sunucu durumu, ad, zipIndirildi}
    let tsYukleniyor = null;

    // ── Kart verisi: kart başlığındaki UYAP düğmesinin React fiber'ından yukarı yürü ──
    function kartVerisi(kart) {
        const kaynaklar = kart.querySelectorAll('.dx-popup-title [role=button], .dx-popup-title div');
        for (const k of kaynaklar) {
            const anahtar = Object.keys(k).find(function(a) { return a.indexOf('__reactFiber') === 0; });
            let f = anahtar ? k[anahtar] : null;
            for (let i = 0; f && i < 80; i++, f = f.return) {
                const p = f.memoizedProps;
                const d = p && p.data;
                if (d && typeof d === 'object' && d.dosyaId) return d;
            }
        }
        return null;
    }
    function dosyaAdi(d) {
        return [d.birimAdi, d.dosyaNo, d.dosyaTurKodAciklama].filter(Boolean).join(' ')
            .replace(/\s+/g, ' ').trim();
    }

    // ── Kartlara düğme ekle (React kartı yeniden çizerse düğme de yeniden eklenir) ──
    function kartlariTara() {
        document.querySelectorAll('.dosya-sorgula-popup .dx-popup-title').forEach(function(baslik) {
            if (baslik.querySelector('[data-tumevrak]')) return;
            const kartGorunumu = Array.from(baslik.querySelectorAll('[role=button]'))
                .find(function(b) { return /Kart Görünümü/.test(b.textContent); });
            const yer = kartGorunumu ? kartGorunumu.parentElement : null;
            if (!yer) return;
            const b = document.createElement('div');
            b.setAttribute('data-tumevrak', '1');
            b.setAttribute('role', 'button');
            b.setAttribute('tabindex', '0');
            b.title = 'Bu dosyadaki tüm evrakı PDF olarak indir';
            b.className = 'dx-widget dx-button dx-button-mode-outlined dx-button-normal dx-button-has-text dx-button-has-icon adalet-button';
            b.style.marginRight = '6px';
            b.innerHTML = '<div class="dx-button-content"><span class="dx-icon fe icon-download"></span> Tüm Evrakı İndir</div>';
            b.addEventListener('click', function(ev) {
                ev.stopPropagation();
                const kart = baslik.closest('.dosya-sorgula-popup');
                const d = kart && kartVerisi(kart);
                if (!d) { panelMesaj('Bu kartın dosya bilgisi okunamadı; kartı kapatıp yeniden açın.'); return; }
                secenekAc(String(d.dosyaId), dosyaAdi(d));
            });
            yer.insertBefore(b, kartGorunumu);
        });
    }
    setInterval(kartlariTara, 700);

    // ── Sunucu ──
    async function api(yol, opts) {
        const r = await window.fetch(EP + yol, Object.assign({ cache: 'no-store' }, opts || {}));
        let j = null;
        try { j = await r.json(); } catch (e) {}
        if (!r.ok) throw new Error((j && j.error) || ('HTTP ' + r.status));
        return j;
    }
    function post(yol, govde) {
        return api(yol, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                          body: JSON.stringify(govde || {}) });
    }
    async function baslat(dosyaId, ad, cikti, kapsam) {
        panelAc();
        try {
            const j = await post('', { dosyaId: dosyaId, ad: ad, cikti: cikti, kapsam: kapsam });
            const v = j.indirme;
            isler[v.id] = isler[v.id] || { ad: ad || 'UYAP dosyası', zipIndirildi: false };
            isler[v.id].veri = v;
            render();
            izle(v.id);
        } catch (e) { panelMesaj('Başlatılamadı: ' + e.message); }
    }
    const izlenen = {};
    async function izle(id) {
        if (izlenen[id]) return;
        izlenen[id] = true;
        try {
            while (isler[id] && BITMIS.indexOf(isler[id].veri.durum) === -1) {
                await new Promise(function(r) { setTimeout(r, 1500); });
                if (!isler[id]) break;
                try { isler[id].veri = (await api('/' + id)).indirme; } catch (e) { continue; }
                render();
            }
            const is = isler[id];
            if (is && is.veri.dosya_hazir && !is.zipIndirildi) dosyaIndir(id);
        } finally { delete izlenen[id]; }
    }
    function dosyaIndir(id) {
        if (!isler[id]) return;
        isler[id].zipIndirildi = true;
        const a = document.createElement('a');
        a.href = EP + '/' + id + '/dosya';
        a.download = '';
        a.setAttribute('data-uyap-internal', '1');   // E-İmza indirme yakalayıcısının dışında
        document.body.appendChild(a); a.click(); a.remove();
    }

    // ── Panel (sağ üst; E-İmza sağ altta — çakışmaz) ──
    function el(tag, css, metin) {
        const e = document.createElement(tag);
        if (css) e.style.cssText = css;
        if (metin != null) e.textContent = metin;
        return e;
    }
    function dugme(metin, renk, fn) {
        const b = el('button', 'margin:6px 6px 0 0;padding:5px 10px;border:0;border-radius:7px;cursor:pointer;font-size:12px;font-weight:600;color:#fff;background:' + renk + ';', metin);
        b.addEventListener('click', function(ev) { ev.stopPropagation(); fn(); });
        return b;
    }
    let panel, govde, mesajEl, form;
    function panelKur() {
        if (panel || !document.body) return;
        panel = el('div', 'position:fixed;top:72px;right:24px;z-index:2147483646;width:320px;max-height:70vh;overflow:auto;padding:12px;border-radius:12px;background:rgba(18,18,24,0.95);border:1px solid rgba(255,255,255,0.15);box-shadow:0 10px 30px rgba(0,0,0,0.35);color:#f3f4f6;font:12px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;display:none;');
        panel.id = 'uyap-tumevrak-panel';
        // UYAP'ın label/span/a renk kuralları paneldeki yazıyı koyu yapıyordu (koyu zeminde
        // okunmuyordu): panel içinde renk panelden miras alınır; satır içi renkler yine geçerli.
        const stil = document.createElement('style');
        stil.textContent =
            '#uyap-tumevrak-panel{color:#f3f4f6!important}' +
            '#uyap-tumevrak-panel div,#uyap-tumevrak-panel span,#uyap-tumevrak-panel label,' +
            '#uyap-tumevrak-panel a,#uyap-tumevrak-panel p{color:inherit;font-family:inherit;font-size:inherit;margin-bottom:0}' +
            '#uyap-tumevrak-panel input{accent-color:#10b981;margin:0}';
        document.head.appendChild(stil);
        const ust = el('div', 'display:flex;align-items:center;justify-content:space-between;');
        ust.appendChild(el('div', 'font-size:13px;font-weight:700;', 'Tüm Evrakı İndir'));
        const kapat = el('span', 'font-size:16px;color:#9ca3af;cursor:pointer;padding:0 4px;', '×');
        kapat.title = 'Gizle (indirme sürer)';
        kapat.addEventListener('click', function() { panel.style.display = 'none'; });
        ust.appendChild(kapat);
        mesajEl = el('div', 'margin-top:6px;color:#fca5a5;');
        form = el('div', '');
        govde = el('div', '');
        panel.appendChild(ust); panel.appendChild(mesajEl); panel.appendChild(form); panel.appendChild(govde);
        document.body.appendChild(panel);
    }

    // ── Seçenek formu: Çıktı (tek PDF | tek PDF + parçalar) × Kapsam (tüm | seçtiğim) ──
    function tercih(ad, varsayilan) {
        try { return localStorage.getItem('uyapTumEvrak.' + ad) || varsayilan; } catch (e) { return varsayilan; }
    }
    function tercihYaz(ad, deger) { try { localStorage.setItem('uyapTumEvrak.' + ad, deger); } catch (e) {} }
    function radyoGrubu(baslik, ad, secenekler, secili) {
        const kutu = el('div', 'margin-top:8px;');
        kutu.appendChild(el('div', 'font-weight:600;color:#cbd5e1;margin-bottom:2px;', baslik));
        secenekler.forEach(function(s) {
            const etiket = el('label', 'display:flex;align-items:center;gap:6px;margin-top:3px;cursor:pointer;');
            const r = document.createElement('input');
            r.type = 'radio'; r.name = 'uyapTumEvrak_' + ad; r.value = s[0]; r.checked = s[0] === secili;
            etiket.appendChild(r); etiket.appendChild(document.createTextNode(s[1]));
            kutu.appendChild(etiket);
        });
        return kutu;
    }
    function secilenDeger(ad) {
        const r = form.querySelector('input[name="uyapTumEvrak_' + ad + '"]:checked');
        return r ? r.value : null;
    }
    function secenekAc(dosyaId, ad) {
        panelAc();
        form.innerHTML = '';
        const kutu = el('div', 'margin-top:8px;padding:8px;border-radius:8px;background:rgba(255,255,255,0.06);');
        kutu.appendChild(el('div', 'font-weight:600;word-break:break-word;', ad || 'UYAP dosyası'));
        kutu.appendChild(radyoGrubu('Çıktı', 'cikti', [
            ['tek', 'Tek PDF'],
            ['ikisi', 'Tek PDF + ayrı ayrı PDF\'ler (ZIP)'],
        ], tercih('cikti', 'tek')));
        kutu.appendChild(radyoGrubu('Belgeler', 'kapsam', [
            ['tum', 'Tüm belgeler'],
            ['secili', 'Seçtiğim belgeler (liste gelince işaretlersiniz)'],
        ], tercih('kapsam', 'tum')));
        kutu.appendChild(dugme('İndir', '#059669', function() {
            const cikti = secilenDeger('cikti'), kapsam = secilenDeger('kapsam');
            tercihYaz('cikti', cikti); tercihYaz('kapsam', kapsam);
            form.innerHTML = '';
            baslat(dosyaId, ad, cikti, kapsam);
        }));
        kutu.appendChild(dugme('Vazgeç', 'rgba(255,255,255,0.15)', function() {
            form.innerHTML = '';
            if (!Object.keys(isler).length) panel.style.display = 'none';
        }));
        form.appendChild(kutu);
    }

    // ── Belge seçimi (kapsam "secili"): onay kutulu liste ──
    function secimListesi(id, kutu, liste) {
        kutu.innerHTML = '';
        const ust = el('div', 'margin-top:6px;display:flex;gap:10px;align-items:center;');
        const sayac = el('span', 'color:#cbd5e1;');
        const tumu = el('a', 'color:#93c5fd;cursor:pointer;', 'Tümünü seç');
        const hicbiri = el('a', 'color:#93c5fd;cursor:pointer;', 'Hiçbiri');
        ust.appendChild(tumu); ust.appendChild(hicbiri); ust.appendChild(sayac);
        kutu.appendChild(ust);
        const liste_ = el('div', 'margin-top:6px;max-height:40vh;overflow:auto;border:1px solid rgba(255,255,255,0.1);border-radius:6px;padding:4px 6px;');
        const kutular = [];
        const say = function() {
            sayac.textContent = kutular.filter(function(k) { return k.checked; }).length + '/' + kutular.length + ' seçili';
        };
        liste.forEach(function(e, i) {
            const etiket = el('label', 'display:flex;gap:6px;align-items:flex-start;padding:3px 0;cursor:pointer;' + (e.ek ? 'padding-left:16px;' : ''));
            const c = document.createElement('input');
            c.type = 'checkbox'; c.value = String(i); c.style.marginTop = '2px';
            c.addEventListener('change', say);
            kutular.push(c);
            const metin = el('span', '');
            metin.appendChild(el('span', '', e.tur));
            const alt = [e.tarih, e.no].filter(Boolean).join(' · ');
            if (alt) metin.appendChild(el('span', 'display:block;color:#9ca3af;font-size:11px;', alt));
            etiket.appendChild(c); etiket.appendChild(metin);
            liste_.appendChild(etiket);
        });
        tumu.addEventListener('click', function() { kutular.forEach(function(k) { k.checked = true; }); say(); });
        hicbiri.addEventListener('click', function() { kutular.forEach(function(k) { k.checked = false; }); say(); });
        kutu.appendChild(liste_);
        say();
        return function() {
            return kutular.filter(function(k) { return k.checked; }).map(function(k) { return parseInt(k.value, 10); });
        };
    }
    async function secimGonder(id, secilen) {
        try { isler[id].veri = (await post('/' + id + '/secim', { secilen: secilen })).indirme; } catch (e) {}
        render();
        izle(id);
    }
    function panelAc() { panelKur(); if (panel) { panel.style.display = 'block'; mesajEl.textContent = ''; } }
    function panelMesaj(m) { panelAc(); if (mesajEl) mesajEl.textContent = m; }

    const satirlar = {};   // id -> {kok, durum, cubukIc, eylem, captcha, anahtar}
    function render() {
        panelKur();
        if (!panel) return;
        Object.keys(isler).forEach(function(id) {
            const is = isler[id], v = is.veri;
            let s = satirlar[id];
            if (!s) {
                s = satirlar[id] = { kok: el('div', 'margin-top:10px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.1);') };
                s.kok.appendChild(el('div', 'font-weight:600;word-break:break-word;', is.ad));
                s.durum = el('div', 'margin-top:4px;'); s.kok.appendChild(s.durum);
                const cubuk = el('div', 'margin-top:6px;height:6px;border-radius:3px;background:rgba(255,255,255,0.12);overflow:hidden;');
                s.cubukIc = el('div', 'height:100%;width:0;background:#10b981;transition:width .3s;');
                cubuk.appendChild(s.cubukIc); s.kok.appendChild(cubuk); s.cubuk = cubuk;
                s.captcha = el('div', ''); s.kok.appendChild(s.captcha);
                s.eylem = el('div', ''); s.kok.appendChild(s.eylem);
                govde.insertBefore(s.kok, govde.firstChild);
            }
            const d = v.durum;
            if (d === 'captcha' || d === 'secim') panel.style.display = 'block';   // kullanıcıdan saklanmaz
            s.durum.textContent = v.mesaj || '';
            s.durum.style.color = d === 'hata' ? '#fca5a5' : d === 'captcha' ? '#fcd34d' : d === 'bitti' ? '#6ee7b7' : '#e5e7eb';
            s.cubuk.style.display = (d === 'calisiyor' || d === 'captcha') && v.toplam ? 'block' : 'none';
            s.cubukIc.style.width = (v.toplam ? Math.round(100 * v.i / v.toplam) : 0) + '%';

            const anahtar = d + '|' + (v.dosya_hazir ? 1 : 0) + '|' + (v.captcha ? v.captcha.n : '');
            if (anahtar === s.anahtar) return;
            s.anahtar = anahtar;
            s.eylem.innerHTML = '';
            if (d !== 'captcha' && d !== 'secim') s.captcha.innerHTML = '';
            if (d === 'secim' && v.liste) {
                const secilenler = secimListesi(id, s.captcha, v.liste);
                s.eylem.appendChild(dugme('Seçilenleri indir', '#059669', function() {
                    const sec = secilenler();
                    if (!sec.length) { s.durum.textContent = 'En az bir belge seçin.'; return; }
                    s.captcha.innerHTML = '';
                    secimGonder(id, sec);
                }));
                s.eylem.appendChild(dugme('Vazgeç', '#b91c1c', function() { iptal(id); }));
            } else if (d === 'calisiyor' || d === 'sirada') {
                s.eylem.appendChild(dugme('Durdur', '#b91c1c', function() { iptal(id); }));
            } else if (d === 'captcha') {
                captchaGoster(id, s.captcha);
                s.eylem.appendChild(dugme('Tekrar dene', 'rgba(255,255,255,0.15)', function() { captchaCevap(id, null); }));
                s.eylem.appendChild(dugme('Durdur', '#b91c1c', function() { iptal(id); }));
            } else {
                if (v.uyarilar && v.uyarilar.length) {
                    s.eylem.appendChild(el('div', 'margin-top:6px;color:#fcd34d;max-height:80px;overflow:auto;',
                        v.uyarilar.length + ' uyarı: ' + v.uyarilar.join(' • ')));
                }
                if (v.dosya_hazir) s.eylem.appendChild(dugme(v.cikti === 'tek' ? 'PDF\'i indir' : 'ZIP\'i indir',
                    '#059669', function() { dosyaIndir(id); }));
                s.eylem.appendChild(dugme('Listeden kaldır', 'rgba(255,255,255,0.15)', function() {
                    s.kok.remove(); delete satirlar[id]; delete isler[id];
                    if (!Object.keys(isler).length) panel.style.display = 'none';
                }));
            }
        });
    }
    async function iptal(id) {
        try { isler[id].veri = (await post('/' + id + '/iptal')).indirme; } catch (e) {}
        render();
    }

    // ── Güvenlik doğrulaması (UYAP'ın kendi Turnstile site anahtarıyla, kullanıcı çözer) ──
    function tsYukle() {
        if (window.turnstile) return Promise.resolve(window.turnstile);
        if (tsYukleniyor) return tsYukleniyor;
        tsYukleniyor = new Promise(function(res, rej) {
            const sc = document.createElement('script');
            sc.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
            sc.async = true;
            sc.onload = function() { res(window.turnstile); };
            sc.onerror = function() { tsYukleniyor = null; rej(new Error('doğrulama betiği yüklenemedi')); };
            document.head.appendChild(sc);
        });
        return tsYukleniyor;
    }
    async function captchaGoster(id, kutu) {
        kutu.innerHTML = '';
        const c = isler[id] && isler[id].veri.captcha;
        if (!c) return;
        kutu.appendChild(el('div', 'margin-top:6px;color:#fcd34d;', c.mesaj || 'UYAP güvenlik doğrulaması istiyor.'));
        const yer = el('div', 'margin-top:6px;min-height:65px;');
        kutu.appendChild(yer);
        const hata = function(m) {
            kutu.appendChild(el('div', 'margin-top:6px;color:#fca5a5;', m +
                ' Birkaç dakika bekleyip "Tekrar dene"ye basabilir ya da indirmeyi durdurabilirsiniz.'));
        };
        try {
            const ts = await tsYukle();
            ts.render(yer, {
                sitekey: c.site_key,
                callback: function(jeton) { captchaCevap(id, jeton); },
                'error-callback': function(kod) { hata('Doğrulama bu adreste açılamadı (kod: ' + kod + ').'); return true; },
            });
        } catch (e) { hata('Doğrulama gösterilemedi (' + e.message + ').'); }
    }
    async function captchaCevap(id, jeton) {
        try { isler[id].veri = (await post('/' + id + '/captcha', { token: jeton })).indirme; } catch (e) {}
        render();
        izle(id);
    }
})();
</script>"""
        if "<head>" in text:
            text = text.replace("<head>", f"<head>{script}", 1)
        elif "<body>" in text:
            text = text.replace("<body>", f"<body>{script}", 1)
        else:
            text = script + text
            
    return text.encode("utf-8")


import inspect as _inspect
import hashlib as _hashlib


def _compute_inject_version():
    """Enjekte edilen script değişince değişen kısa sürüm damgası. office_agent bunu
    KABUK (HTML) önbellek anahtarına ekler; böylece enjekte JS güncellenince eski kabuklar
    otomatik geçersiz olur ve uzak istemciler bayat enjeksiyon almaz. _rewrite_body'nin
    kaynağı hash'lenir (enjekte script bu fonksiyonun içinde olduğu için her değişiklikte
    sürüm değişir; elle bump gerekmez)."""
    try:
        veri = _inspect.getsource(_rewrite_body).encode("utf-8")
    except Exception:
        # Derlenmiş (.pyd) kurulumda kaynak yok: derlenmiş dosyanın kendisi hash'lenir
        # (her yeni sürümde değişir → önbellek yine kendiliğinden geçersizleşir).
        try:
            with open(__spec__.origin, "rb") as f:
                veri = f.read()
        except Exception:
            veri = b""
    return _hashlib.md5(veri, usedforsecurity=False).hexdigest()[:8]  # sürüm etiketi


INJECT_VERSION = _compute_inject_version()


def _build_response_headers(resp, proxy_base: str) -> dict:
    out = {}
    for k, v in resp.headers.multi_items():
        lk = k.lower()
        if lk in DROP_RESPONSE:
            continue
        if lk == "location":
            v = v.replace("https://avukat.uyap.gov.tr", proxy_base)
        out[k] = v
    return out


# KEP ile gelen evrak ekleri (.eyp / .eml) UYAP'tan view_document_brd.uyap ile
# 'application/zip' olarak geliyor (canlı, 2026-10-14, 2026/26385 Alanya İcra 89/1 cevabı:
# eyp zip = ana_evrak.pdf; eml zip = ana_evrak.html + "(ek evrak 1)ustyazi.pdf.pdf").
# UYAP görüntüleyicisi MIME türünü yanıtın Content-Type başlığından alıyor; zip için
# "Desteklenmeyen belge türü" diyor. Zipteki PDF'i application/pdf olarak verince UYAP'ın
# kendi PDF görüntüleyicisi açıyor (sayfa içi kancayla canlı denendi). PDF yoksa / zip
# bozuksa / çok büyükse orijinal zip DEĞİŞMEDEN döner (UYAP eskisi gibi davranır).
_EK_ZIP_ACILMIS_SINIR = 50 * 1024 * 1024  # zip bombası koruması (açılmış toplam bayt)


def _ek_zip_pdf_ac(path: str, content_type: str, body: bytes):
    """(content_type, body) döner — yalnız view_document_brd.uyap zip yanıtını PDF'e çevirir."""
    if not path.split("?", 1)[0].rstrip("/").endswith("view_document_brd.uyap"):
        return content_type, body
    if "zip" not in (content_type or "").lower() or body[:2] != b"PK":
        return content_type, body
    try:
        import io
        import hashlib
        import zipfile
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            adaylar = [i for i in z.infolist()
                       if not i.is_dir() and i.filename.lower().endswith(".pdf")]
            if sum(i.file_size for i in adaylar) > _EK_ZIP_ACILMIS_SINIR:
                return content_type, body
            pdfler, gorulen = [], set()
            for i in adaylar:
                veri = z.read(i)
                ozet = hashlib.sha256(veri).digest()
                if veri[:4] == b"%PDF" and ozet not in gorulen:
                    gorulen.add(ozet)
                    pdfler.append(veri)
        if not pdfler:
            return content_type, body
        if len(pdfler) == 1:
            return "application/pdf", pdfler[0]
        from pypdf import PdfReader, PdfWriter
        yazici = PdfWriter()
        for veri in pdfler:
            yazici.append(PdfReader(io.BytesIO(veri)))
        cikti = io.BytesIO()
        yazici.write(cikti)
        return "application/pdf", cikti.getvalue()
    except Exception:
        return content_type, body


def _ek_pdf_basliklari(headers: dict, content_type: str) -> dict:
    """Yanıt başlıklarındaki Content-Type'ı (büyük/küçük harf fark etmeksizin) değiştirir."""
    out = {k: v for k, v in headers.items() if k.lower() != "content-type"}
    out["content-type"] = content_type
    return out


# --------------------------------------------------------------------------------------
# FastAPI app
# --------------------------------------------------------------------------------------
# auto_error=False: yetkilendirmeyi require_auth içinde kendimiz yönetiriz; böylece
# localhost (aynı makine) için Basic-Auth'u parolasız geçebiliriz.
security = HTTPBasic(auto_error=False)

# Aynı makinedeki süreçler (ör. masaüstü GUI, yerel yardımcı programlar) muaf: bu makine
# zaten e-imza kartını tutuyor ve tam yetkili. LAN/dış erişim (0.0.0.0) PAROLALI kalır.
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}

# İleri kullanıcı için kaçış kapısı: ofise gerçek bir DNS adıyla (ör. mDNS "ofis.local")
# bağlanmak gerekirse UYAP_ALLOWED_HOSTS="ofis.local,ofis2.local" ile beyaz listeye alınır.
_ALLOWED_HOSTNAMES = {
    h.strip().lower()
    for h in os.environ.get("UYAP_ALLOWED_HOSTS", "").split(",")
    if h.strip()
}


def _hostname_of(value: str) -> str:
    """Bir Host ya da Origin başlığından yalnızca ana makine adını (şema/port/yol olmadan)
    çıkarır. Örn. 'http://127.0.0.1:8800' → '127.0.0.1', '[::1]:8800' → '::1'."""
    v = (value or "").strip()
    if not v:
        return ""
    if "://" in v:                      # Origin: "http://host:port"
        v = v.split("://", 1)[1]
    v = v.split("/", 1)[0]              # olası yol kuyruğunu at
    if v.startswith("["):              # köşeli parantezli IPv6: [::1]:8800
        return v[1:].split("]", 1)[0]
    if v.count(":") == 1:              # host:port (IPv4/ad)
        v = v.split(":", 1)[0]
    return v


def _is_ip_or_local(hostname: str) -> bool:
    """Ana makine adı bir IP-literali (v4/v6) veya 'localhost'/boş ise True. Meşru
    istemciler (yerel GUI, LAN home_client, aynı-origin UYAP sayfası) HER ZAMAN böyledir;
    gerçek bir DNS adı yalnızca DNS-rebinding/çapraz-site girişiminde görünür."""
    h = (hostname or "").strip().lower()
    if h in ("", "localhost"):
        return True
    if h in _ALLOWED_HOSTNAMES:
        return True
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def _local_token_file() -> str:
    """Aynı-makine istemcilerinin (ofisle aynı kullanıcı) okuyacağı, kullanıcıya özel jeton
    dosyasının yolu. Windows'ta %LOCALAPPDATA%\\UyapIcra varsayılan ACL'iyle zaten yalnızca
    bu kullanıcıya (ve yöneticilere) açıktır."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "UyapIcra", "gw_local_token")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "uyapicra", "gw_local_token")


class _LocalTokenInjector(urllib.request.BaseHandler):
    """Aynı SÜREÇTEKİ urllib istemcilerinin (paylaşım modunda ofisle aynı süreçte koşan GUI
    runner modülleri: SorguMotoru, is_kuyrugu, Mts_evirme, logger_core, htiyati_Haciz, …)
    yerel ofise (127.0.0.1) yaptığı her isteğe X-Uyap-Local-Token başlığını OTOMATİK ekler.
    Böylece onlarca istemci dosyasını tek tek değiştirmeden hepsi jetonu sunar. Yalnız yerel
    loopback host'unu hedefler; başka adresler dokunulmadan geçer."""

    def http_request(self, req):
        try:
            hn = _hostname_of(req.host or "").lower()
            if hn in _LOCAL_HOSTS and GW_LOCAL_TOKEN and not req.has_header("X-uyap-local-token"):
                req.add_unredirected_header("X-Uyap-Local-Token", GW_LOCAL_TOKEN)
        except Exception:
            pass
        return req

    https_request = http_request


def _windows_acl_sinirla(path) -> None:
    """os.chmod Windows'ta sessiz no-op olduğundan (bulgu #11) gerçek ACL kısıtlaması:
    kalıtımı kes, dosyayı yalnızca mevcut kullanıcıya (tam yetki) sınırla."""
    if os.name != "nt":
        return
    kullanici = os.environ.get("USERNAME", "").strip()
    if not kullanici:
        return
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{kullanici}:(F)"],
            capture_output=True, timeout=5, check=False,
        )
    except Exception:
        pass


def _install_local_token_opener():
    """Süreç-global urllib opener'ını kur: aynı süreçteki tüm urllib istemcileri yerel ofise
    giderken jetonu taşısın. Varsayılan handler'ları korur (yalnızca loopback isteklerine
    başlık ekler), diğer adreslere dokunmaz."""
    try:
        opener = urllib.request.build_opener(_LocalTokenInjector())
        urllib.request.install_opener(opener)
    except Exception as e:
        print(f"[!] Yerel jeton urllib opener'ı kurulamadı ({e}).")


def _init_local_token() -> str:
    """Yerel-yetki jetonunu üretir/yükler, ortam değişkenine + kullanıcıya özel dosyaya yazar
    ve aynı-süreç urllib istemcileri için jeton-enjekte eden opener'ı kurar.
    Öncelik: UYAP_LOCAL_TOKEN ortam değişkeni (ileri kullanıcı/çoklu-süreç için sabitlenebilir)
    → yoksa rastgele üret. Aynı süreçteki istemciler ortam değişkeninden, ayrı süreçteki
    aynı-makine istemcileri dosyadan okur."""
    global GW_LOCAL_TOKEN
    token = (os.environ.get("UYAP_LOCAL_TOKEN") or "").strip() or secrets.token_urlsafe(32)
    GW_LOCAL_TOKEN = token
    os.environ["UYAP_LOCAL_TOKEN"] = token  # aynı süreçteki GUI runner doğrudan buradan okur
    _install_local_token_opener()
    try:
        path = _local_token_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(token)
        try:
            os.chmod(path, 0o600)  # POSIX; Windows'ta LOCALAPPDATA zaten kullanıcıya özel
        except OSError:
            pass
        _windows_acl_sinirla(path)
    except OSError as e:
        print(f"[!] Yerel jeton dosyası yazılamadı ({e}); aynı-süreç ortam değişkeni kullanılacak.")
    return token


# ── Yerel tarayıcı oturumu (güvenlik taraması 2026-09-30, bulgu 4.1) ─────────────────────
# Eskiden yerel tarayıcı BAŞLIKLARINDAN tanınıyordu (Origin/Sec-Fetch/Accept); her yerel program
# bu başlıkları taklit edip jetonsuz tam yetki alabiliyordu. Artık tarayıcı yalnız bu süreçte
# üretilen gizli değeri taşıyan çerezle kabul edilir. Çerez, jetonu bilen bir programın aldığı
# TEK KULLANIMLIK kodla açılan /__uyap_yerel__/giris bağlantısında verilir (bkz. yerel_giris.py).
GW_BROWSER_SECRET = secrets.token_urlsafe(32)   # süreç başına; proxy yeniden başlarsa eski çerez geçersiz
YEREL_CEREZ = "uyap_yerel"
_GIRIS_KOD_OMRU_SN = 60
_giris_kodlari = {}                             # kod -> son geçerlilik (monotonic)
_giris_kilit = threading.Lock()


def _giris_kodu_uret() -> str:
    simdi = time.monotonic()
    kod = secrets.token_urlsafe(24)
    with _giris_kilit:
        for k in [k for k, t in _giris_kodlari.items() if t < simdi]:
            _giris_kodlari.pop(k, None)
        _giris_kodlari[kod] = simdi + _GIRIS_KOD_OMRU_SN
    return kod


def _giris_kodu_kullan(kod: str) -> bool:
    """Kod geçerliyse True ve kod SİLİNİR (tek kullanım)."""
    if not kod:
        return False
    with _giris_kilit:
        son = _giris_kodlari.pop(kod, None)
    return son is not None and son >= time.monotonic()


def _yerel_tarayici_cerezi_gecerli(request: Request) -> bool:
    deger = request.cookies.get(YEREL_CEREZ, "")
    return bool(deger) and secrets.compare_digest(deger, GW_BROWSER_SECRET)


def _guvenli_hedef(hedef: str) -> str:
    """Açık yönlendirmeyi önle: yalnız bu sunucuda göreli yol ('/…', '//' ve '/\\' değil)."""
    h = (hedef or "").strip()
    if not h.startswith("/") or h.startswith("//") or h.startswith("/\\"):
        return "/giris"
    return h


class YerelGirisGerekli(Exception):
    """Jetonsuz/çerezsiz yerel tarayıcı isteği — kullanıcıya açıklayıcı sayfa gösterilir."""


# ── Yerel ağ (LAN) tarayıcı girişi (kullanıcı isteği 2026-10-01) ─────────────────────────
# "Yerel ağdan doğrudan erişim" açıkken (UYAP_LAN_SHARE=1) ağdaki tarayıcılar eskiden ham
# Basic-Auth açılır penceresiyle karşılaşıyordu. Artık programdan bağlanmayan (LAN bileti
# taşımayan) tarayıcıya kullanıcı adı + parola isteyen bir GİRİŞ SAYFASI gösterilir; doğru
# girilince süreç başına gizli değeri taşıyan çerez (uyap_lan, HttpOnly, SameSite=Strict) verilir.
# Program (home_client) eskisi gibi LAN biletiyle Basic-Auth yapar — etkilenmez.
GW_LAN_SECRET = secrets.token_urlsafe(32)
LAN_CEREZ = "uyap_lan"


class LanGirisGerekli(Exception):
    """Kimliksiz LAN tarayıcı isteği — giriş sayfası gösterilir."""

    def __init__(self, hedef="/giris"):
        super().__init__(hedef)
        self.hedef = hedef


def _lan_cerezi_gecerli(request: Request) -> bool:
    deger = request.cookies.get(LAN_CEREZ, "")
    return bool(deger) and secrets.compare_digest(deger, GW_LAN_SECRET)


def _lan_giris_sayfasi(hedef="/giris", hata="") -> str:
    import html as _html
    hata_html = (f"<p style='background:#F7E8DE;border:1px solid #C18A66;color:#9C6B47;padding:10px;"
                 f"border-radius:8px'>{_html.escape(hata)}</p>") if hata else ""
    return (
        "<!doctype html><html lang='tr'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'><title>UYAP — Ofis Girişi</title>"
        "<body style='font-family:Segoe UI,Arial,sans-serif;background:#F1EEE8;color:#43423D;margin:0;padding:24px'>"
        "<div style='max-width:420px;margin:48px auto;background:#FCFBF8;border:1px solid #E8E2D7;"
        "border-radius:16px;padding:24px 26px;box-shadow:0 14px 40px rgba(96,86,66,.14)'>"
        "<h2 style='margin-top:0'>Ofis UYAP Girişi</h2>"
        "<p style='font-size:13px;color:#8C867B'>Bu bilgisayar ofisin UYAP oturumunu yerel ağda paylaşıyor. "
        "Programdan (<b>Bağlantıyı Al → Tarayıcıyı Aç</b>) bağlanıyorsanız bu ekranı görmezsiniz. "
        "Doğrudan bağlanmak için ofis programına girilen kullanıcı adı ve parolayı yazın.</p>"
        f"{hata_html}"
        "<form method='post' action='/__uyap_lan__/giris' autocomplete='off'>"
        f"<input type='hidden' name='hedef' value='{_html.escape(hedef, quote=True)}'>"
        "<label style='font-size:12px;color:#8C867B'>Kullanıcı adı</label>"
        "<input name='kullanici' required autofocus style='width:100%;box-sizing:border-box;padding:10px;"
        "border:1px solid #E3DDD2;border-radius:9px;margin:4px 0 12px'>"
        "<label style='font-size:12px;color:#8C867B'>Parola</label>"
        "<input name='parola' type='password' required style='width:100%;box-sizing:border-box;padding:10px;"
        "border:1px solid #E3DDD2;border-radius:9px;margin:4px 0 16px'>"
        "<button style='width:100%;background:#7C9A7E;color:#fff;border:0;padding:12px;border-radius:11px;"
        "font-weight:600;cursor:pointer'>Giriş</button></form>"
        "<p style='font-size:11px;color:#B6AFA2;margin-bottom:0'>Not: yerel ağ bağlantısı şifrelenmemiş HTTP'dir; "
        "yalnız güvendiğiniz ofis ağında kullanın.</p></div></body></html>")


_YEREL_GIRIS_SAYFASI = (
    "<!doctype html><html lang='tr'><meta charset='utf-8'><title>UYAP — programdan açın</title>"
    "<body style='font-family:Segoe UI,Arial,sans-serif;max-width:560px;margin:60px auto;line-height:1.5'>"
    "<h2>UYAP'ı programdan açın</h2>"
    "<p>Güvenlik nedeniyle bu adres artık doğrudan (yer imi ya da elle yazarak) açılamıyor.</p>"
    "<p>UyapMasa'daki <b>UYAP Bağlantısı</b> ekranında <b>“Tarayıcıda Aç”</b> düğmesine basın. "
    "Program yeniden başlatıldıysa açık sekmeleri de bu düğmeyle yeniden açmanız gerekir.</p>"
    "</body></html>")


# Ağ (LAN) Basic-Auth için IP başına başarısız deneme sınırı — 0.0.0.0'da dinlendiği için parola
# kaba kuvvetle denenebiliyordu (güvenlik taraması 2026-09-30, bulgu 4.2). Yerel istekler etkilenmez.
_AUTH_PENCERE_SN = 15 * 60
_AUTH_AZAMI_HATA = 10
_auth_hatalari = {}            # ip -> [zaman damgaları]
_auth_kilit = threading.Lock()


def _auth_engelli_mi(ip: str) -> bool:
    simdi = time.monotonic()
    with _auth_kilit:
        kayit = [t for t in _auth_hatalari.get(ip, []) if simdi - t < _AUTH_PENCERE_SN]
        if kayit:
            _auth_hatalari[ip] = kayit
        else:
            _auth_hatalari.pop(ip, None)
        return len(kayit) >= _AUTH_AZAMI_HATA


def _auth_hata_kaydet(ip: str) -> None:
    with _auth_kilit:
        _auth_hatalari.setdefault(ip, []).append(time.monotonic())


def require_auth(request: Request, creds: HTTPBasicCredentials = Depends(security)) -> str:
    """Gate every request behind Basic-Auth so only authorized people reach UYAP.
    Aynı makineden (127.0.0.1/::1) gelen istekler parolasızdır ama yerel yetki ister: program
    jetonu (X-Uyap-Local-Token) ya da 'Tarayıcıda Aç' ile verilen çerez. Uzaktan erişim parolalı."""
    # DNS-REBINDING / ÇAPRAZ-SİTE TARAYICI KORUMASI — localhost muafiyetinden ÖNCE çalışır.
    # Bir web sitesi tarayıcıda 127.0.0.1:8800'e istek atıp (CSRF) ya da kendi alan adını
    # 127.0.0.1'e çözümleyip (DNS rebinding) parolasız localhost yolunu kötüye kullanamasın
    # diye: Host ya da Origin başlığı gerçek bir DNS adıysa reddedilir. Meşru istemciler hep
    # IP-literali/localhost kullanır; Python istemcileri (runner, home_client) Origin göndermez.
    host_hn = _hostname_of(request.headers.get("host", ""))
    if host_hn and not _is_ip_or_local(host_hn):
        raise HTTPException(status_code=403,
                            detail="Beklenmeyen Host başlığı (olası DNS rebinding).")
    # 2026-09-23: çapraz-site ALT-KAYNAK istekleri (başka bir sitenin <img>/<script>/
    # fetch'i) reddedilir. Bunlar Origin taşımayabilir ama tarayıcı Sec-Fetch-Site:
    # cross-site gönderir; eskiden _looks_like_local_browser bu başlığın VARLIĞINI
    # tarayıcı kanıtı sayıp jetonsuz geçiriyordu (GET-CSRF). Üst-düzey belge gezinmesi
    # (navigate/document) görünür olduğu ve olası yönlendirmeleri bozmamak için serbest.
    sfs = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if sfs == "cross-site":
        mode = (request.headers.get("sec-fetch-mode") or "").strip().lower()
        dest = (request.headers.get("sec-fetch-dest") or "").strip().lower()
        if not (mode == "navigate" and dest in ("document", "")
                and request.method.upper() in ("GET", "HEAD")):
            raise HTTPException(status_code=403,
                                detail="Çapraz-site alt-kaynak isteği reddedildi.")
    origin = request.headers.get("origin")
    if origin:
        # "null" origin (sandbox'lı iframe, file://, yönlendirilmiş form) meşru yerel UI'nin
        # ASLA göndermeyeceği bir değerdir; güvenilir sayılırsa CSRF/DNS-rebinding koruması delinir.
        if origin.lower() == "null" or not _is_ip_or_local(_hostname_of(origin)):
            raise HTTPException(status_code=403,
                                detail="Yetkisiz Origin (çapraz-site istek reddedildi).")

    client_host = request.client.host if request.client else ""
    if client_host in _LOCAL_HOSTS:
        # Yerel (aynı makine) istemci: ya yerel-yetki jetonu (Python istemcileri) ya da
        # 'Tarayıcıda Aç' ile verilen çerez (tarayıcı) gerekir. Başlıklara bakarak "tarayıcı"
        # tahmini YAPILMAZ — başlıklar her yerel programca taklit edilebilir (bulgu 4.1).
        presented = request.headers.get("x-uyap-local-token", "")
        if presented and GW_LOCAL_TOKEN and secrets.compare_digest(presented, GW_LOCAL_TOKEN):
            return GW_USER
        if _yerel_tarayici_cerezi_gecerli(request):
            return GW_USER
        accept = request.headers.get("accept", "")
        if request.method.upper() in ("GET", "HEAD") and "text/html" in accept:
            raise YerelGirisGerekli()
        raise HTTPException(status_code=403,
                            detail="Yerel yetki gerekli: program jetonu ya da 'Tarayıcıda Aç' ile verilen çerez.")
    if _lan_cerezi_gecerli(request):  # LAN giriş sayfasından girmiş tarayıcı
        return GW_USER
    if _auth_engelli_mi(client_host):
        raise HTTPException(status_code=429,
                            detail="Çok fazla hatalı giriş denemesi; 15 dakika sonra yeniden deneyin.")
    # LAN-direct bileti: üye istemci (home_client) ofisin ana parolasını BİLMEZ; signaling'den
    # aldığı oturumluk bileti Basic-Auth parolası olarak sunar (kullanıcı adı serbest — günlüğe
    # üyenin kendi adı düşsün diye aynen kabul edilir).
    if creds is not None and GW_LAN_TICKET and secrets.compare_digest(creds.password, GW_LAN_TICKET):
        return creds.username or "lan"
    if not GW_PASS:
        raise HTTPException(status_code=503,
                            detail="Sunucu parolası (GW_PASS ortam değişkeni) ayarlanmamış.")
    if creds is None:
        # Kimliksiz ilk istek — deneme sayılmaz. Tarayıcı gezinmesine giriş SAYFASI (ham Basic-Auth
        # penceresi yerine); programlar (Accept: text/html göndermez) eskisi gibi 401 + Basic alır.
        if request.method.upper() in ("GET", "HEAD") and "text/html" in request.headers.get("accept", ""):
            hedef = request.url.path + (("?" + request.url.query) if request.url.query else "")
            raise LanGirisGerekli(hedef)
        raise HTTPException(status_code=401, detail="Kimlik bilgisi gerekli.",
                            headers={"WWW-Authenticate": "Basic"})
    ok_user = secrets.compare_digest(creds.username, GW_USER)
    ok_pass = secrets.compare_digest(creds.password, GW_PASS)
    if not (ok_user and ok_pass):
        _auth_hata_kaydet(client_host)
        raise HTTPException(status_code=401, detail="Yetkisiz erişim.",
                            headers={"WWW-Authenticate": "Basic"})
    return creds.username


async def _keepalive_loop(session: "GatewaySession", idle_interval: int):
    """Background task: while no requests are flowing, probe every `idle_interval` seconds
    and silently re-login if UYAP has dropped the session. Active traffic already keeps the
    session warm, so we only spend a probe when genuinely idle.

    BULGU (2026-07-14): bu yoklama `session._probe()`'u DOĞRUDAN, `UYAP_WRITE_LOCK`'a hiç
    girmeden çağırıyordu — proxy'nin (bkz. proxy() içindeki `interactive_write()`) VE iş
    kuyruğunun (bkz. `batch_write`) paylaştığı TEK kilidi tamamen atlayan ÜÇÜNCÜ bir yazar.
    Uzun süren çok-sayfalı bir "Yenile" taramasında sayfalar/kapsamlar ARASINDA UyapMasa'nın
    kendi işleme süresi `idle_interval`i (5sn) kolayca aşıyor; tam o boşlukta kilitsiz atılan
    bu yoklama, hemen ardından gelen bir sonraki (kilitli) asıl istekle GERÇEKTEN eşzamanlı
    UYAP'a ulaşıp UYAP'ın "eşzamanlı sorgulama" reddini tetikliyordu — 30sn'lik geniş
    yeniden-deneme penceresi bile bunu açamadı çünkü sorun bir YARIŞ değil, HER turda
    yeniden oluşan bir çakışmaydı. `batch_write()` (düşük öncelik) ile sarmalanır: bekleyen
    bir bireysel/toplu istek varsa yoklama ona yol verir — `session._probe()`'un KENDİSİ
    kilitlenmedi (bkz. o fonksiyonun docstring'i — `relogin()` onu bazen ZATEN tutulan
    kilidin içinden çağırıyor, orada kilitlemek deadlock olurdu)."""
    while True:
        await asyncio.sleep(idle_interval)
        if time.monotonic() - session.last_request_ts < idle_interval:
            continue  # recent traffic kept it warm; skip the redundant probe
        try:
            async with batch_write():
                await session.ensure_alive()
        except BaseException as e:  # SystemExit from a login helper must not kill the loop
            print(f"[!] Keepalive hatası: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[*] UYAP ağ geçidi başlatılıyor, ilk giriş deneniyor...")
    try:
        await gw.ensure_alive()
        print("[+] İlk giriş tamam, ağ geçidi hazır.")
    except BaseException as e:
        print(f"[!] İlk giriş başarısız ({e}). Keepalive döngüsü tekrar deneyecek.")
    task = asyncio.create_task(_keepalive_loop(gw, IDLE_INTERVAL))
    try:
        yield
    finally:
        task.cancel()
        await gw.client.aclose()


app = FastAPI(title="UYAP Office Gateway", lifespan=lifespan)


@app.get("/__ping__")
async def lan_ping(_user: str = Depends(require_auth)):
    """Yerel ağ keşfi için hafif sağlık ucu: UYAP'a hiç gitmez, sadece 'ofis burada ve
    parola doğru' der. home_client bu adrese Basic-Auth ile ulaşabiliyorsa LAN'ı seçer."""
    return Response(content=b"ok", media_type="text/plain")


# ── Pano "bu bilgisayarda ofis programı çalışıyor mu?" tespiti ─────────────────────────
# cokluuyap.com'daki ana sayfa panosu, tarayıcıdan 127.0.0.1:8800'e bu ucu yoklayarak
# programın bu makinede çalışıp çalışmadığını anlar (kuruluysa "bağlan", değilse "indir"
# akışı). Uç require_auth'tan MUAFTIR ama yalnızca programın VARLIĞINI söyler; kimlik,
# oturum ya da UYAP verisi sızdırmaz. Tarayıcının yanıtı OKUYABİLMESİ için yalnızca
# bilinen satıcı origin'lerine CORS izni verilir; Chrome'un Private Network Access (PNA)
# preflight'ı için Access-Control-Allow-Private-Network de döndürülür.
_AGENT_PING_ORIGINS = {
    "https://www.cokluuyap.com", "https://cokluuyap.com",
    "http://127.0.0.1:8080", "http://localhost:8080",  # yerel/dev vendor_server
}
_AGENT_PING_ORIGINS |= {
    o.strip().rstrip("/")
    for o in os.environ.get("UYAP_AGENT_PING_ORIGINS", "").split(",")
    if o.strip()
}


def _agent_ping_headers(request: Request) -> dict:
    origin = (request.headers.get("origin") or "").rstrip("/")
    h = {"Cache-Control": "no-store"}
    if origin in _AGENT_PING_ORIGINS:
        h["Access-Control-Allow-Origin"] = origin
        h["Vary"] = "Origin"
        h["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        h["Access-Control-Allow-Private-Network"] = "true"
        h["Access-Control-Max-Age"] = "600"
    return h


@app.options("/__agent__/ping")
async def agent_ping_preflight(request: Request):
    return Response(status_code=204, headers=_agent_ping_headers(request))


@app.get("/__agent__/ping")
async def agent_ping(request: Request):
    # Proxy yalnızca paylaşım/alma açıkken ayakta olduğundan "çalışıyor" = paylaşım aktif.
    return Response(content=b'{"app":"cokluuyap-ofis","sharing":true}',
                    media_type="application/json",
                    headers=_agent_ping_headers(request))


# ── Yerel tarayıcı girişi (bulgu 4.1) — catch-all proxy'den ÖNCE tanımlı olmalı ──────────
@app.exception_handler(YerelGirisGerekli)
async def _yerel_giris_gerekli(request: Request, exc: YerelGirisGerekli):
    return Response(content=_YEREL_GIRIS_SAYFASI.encode("utf-8"), status_code=403,
                    media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})


def _yerel_istemci_mi(request: Request) -> bool:
    client_host = request.client.host if request.client else ""
    host_hn = _hostname_of(request.headers.get("host", ""))
    return client_host in _LOCAL_HOSTS and (not host_hn or _is_ip_or_local(host_hn))


@app.post("/__uyap_yerel__/kod")
async def yerel_giris_kodu(request: Request):
    """Jetonu bilen yerel programa 60 sn geçerli, tek kullanımlık tarayıcı-giriş kodu verir."""
    presented = request.headers.get("x-uyap-local-token", "")
    if not (_yerel_istemci_mi(request) and presented and GW_LOCAL_TOKEN
            and secrets.compare_digest(presented, GW_LOCAL_TOKEN)):
        raise HTTPException(status_code=403, detail="Yerel yetki jetonu gerekli.")
    return Response(content=json.dumps({"kod": _giris_kodu_uret()}).encode("utf-8"),
                    media_type="application/json", headers={"Cache-Control": "no-store"})


@app.get("/__uyap_yerel__/giris")
async def yerel_giris(request: Request, k: str = "", hedef: str = "/giris"):
    """Tek kullanımlık kodu doğrular, yerel tarayıcı çerezini verir ve hedefe yönlendirir."""
    if not _yerel_istemci_mi(request) or not _giris_kodu_kullan(k):
        raise YerelGirisGerekli()
    resp = Response(status_code=303, headers={"Location": _guvenli_hedef(hedef), "Cache-Control": "no-store"})
    resp.set_cookie(YEREL_CEREZ, GW_BROWSER_SECRET, httponly=True, samesite="strict", path="/")
    return resp


# ── Yerel ağ (LAN) tarayıcı giriş sayfası ───────────────────────────────────────────────
def _lan_sayfa(icerik: str, status: int = 200) -> Response:
    return Response(content=icerik.encode("utf-8"), status_code=status,
                    media_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                             "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                                        "form-action 'self'; frame-ancestors 'none'"})


@app.exception_handler(LanGirisGerekli)
async def _lan_giris_gerekli(request: Request, exc: LanGirisGerekli):
    return _lan_sayfa(_lan_giris_sayfasi(_guvenli_hedef(exc.hedef)), status=401)


def _lan_istegi_reddi(request: Request):
    """Giriş uçları için tarayıcı kaynaklı saldırı kontrolleri (DNS rebinding / çapraz-site)."""
    host_hn = _hostname_of(request.headers.get("host", ""))
    if host_hn and not _is_ip_or_local(host_hn):
        return "Beklenmeyen Host başlığı."
    if (request.headers.get("sec-fetch-site") or "").strip().lower() == "cross-site":
        return "Çapraz-site istek reddedildi."
    return None


@app.get("/__uyap_lan__/giris")
async def lan_giris_formu(request: Request, hedef: str = "/giris"):
    red = _lan_istegi_reddi(request)
    if red:
        raise HTTPException(status_code=403, detail=red)
    return _lan_sayfa(_lan_giris_sayfasi(_guvenli_hedef(hedef)))


@app.post("/__uyap_lan__/giris")
async def lan_giris(request: Request):
    red = _lan_istegi_reddi(request)
    if red:
        raise HTTPException(status_code=403, detail=red)
    from urllib.parse import parse_qs
    alanlar = parse_qs((await request.body()).decode("utf-8", "replace"))
    kullanici = (alanlar.get("kullanici") or [""])[0].strip()
    parola = (alanlar.get("parola") or [""])[0]
    hedef = _guvenli_hedef((alanlar.get("hedef") or ["/giris"])[0])
    ip = request.client.host if request.client else ""
    if _auth_engelli_mi(ip):
        return _lan_sayfa(_lan_giris_sayfasi(hedef, "Çok fazla hatalı deneme; 15 dakika sonra yeniden deneyin."), 429)
    dogru = bool(GW_PASS) and secrets.compare_digest(kullanici, GW_USER or "") \
        and secrets.compare_digest(parola, GW_PASS)
    if not dogru:
        _auth_hata_kaydet(ip)
        return _lan_sayfa(_lan_giris_sayfasi(hedef, "Kullanıcı adı veya parola hatalı."), 401)
    resp = Response(status_code=303, headers={"Location": hedef, "Cache-Control": "no-store"})
    resp.set_cookie(LAN_CEREZ, GW_LAN_SECRET, httponly=True, samesite="strict", path="/",
                    secure=request.url.scheme == "https")
    return resp


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy(full_path: str, request: Request, _user: str = Depends(require_auth)):
    body = await request.body()

    # GEÇİT: yerel Django arayüzü (127.0.0.1:8000) — UYAP'a hiç gitmez. LAN istemcisi
    # (home_client) doğrudan buraya bağlandığı için bu yönlendirme tünel yolundaki
    # (office_agent.handle_uyap_request) ile aynı olmalı.
    if is_gecit_path(full_path):
        headers_dict = dict(request.headers)
        status, headers, out_body = await handle_gecit_proxy(
            request.method, full_path, request.url.query, body, headers_dict)
        return Response(content=out_body, status_code=status, headers=headers)

    # KONTROL DÜZLEMİ: iş kuyruğu + UDF e-imza köprüsü yolları UYAP'a İLETİLMEZ; ofiste
    # yerel işlenir. LAN istemcisi (home_client) doğrudan buraya bağlandığı için bu
    # yönlendirme tünel yolundaki (office_agent.handle_uyap_request) ile aynı olmalı.
    from . import jobs  # döngüsel import'tan kaçınmak için tembel
    if jobs.is_control_path(full_path):
        status, headers, out_body = await jobs.handle_control(
            request.method, full_path, request.url.query, body)
        return Response(content=out_body, status_code=status, headers=headers)

    gw.touch()
    target = f"{UYAP_BASE}/{full_path}"
    fwd_headers = _build_forward_headers(request)
    # LAN istemcisi (home_client) doğrudan bu sunucuya bağlanır ama UYAP URL'lerinin
    # tarayıcının gördüğü origin'e (127.0.0.1:port) yeniden yazılmasını ister; X-Forwarded-Host
    # ile bunu bildirir. Yoksa istek geldiği origin (request.base_url) kullanılır.
    xf_host = request.headers.get("x-forwarded-host")
    if xf_host:
        xf_proto = request.headers.get("x-forwarded-proto", "http")
        proxy_base = f"{xf_proto}://{xf_host}".rstrip("/")
    else:
        proxy_base = str(request.base_url).rstrip("/")

    async def forward():
        return await gw.client.request(
            request.method, target,
            params=request.query_params,
            content=body,
            headers=fwd_headers,
        )

    async def forward_with_relogin():
        async with uyap_istek_izi():
            return await _forward_with_relogin()

    async def _forward_with_relogin():
        resp = await forward()
        # "Check the session while forwarding": if UYAP answered with a login wall,
        # re-authenticate (locked) and replay the request once.
        if _looks_like_login_wall(resp):
            await gw.relogin()
            resp = await forward()
        return resp

    try:
        # Değiştiren (POST/PUT/PATCH/DELETE) istekler bireysel-öncelikli yazma geçidinden
        # geçer: hem tek-tek sıraya girer (oturum bozulmaz) hem de arka plandaki toplu işi
        # (iş kuyruğu) bu istek bitene kadar bekletir. GET'ler serbest akar (kilitlenmez).
        # NOT: LAN'dan doğrudan bu FastAPI proxy'sine bağlanan istemci de artık aynı yazma
        # kilidini paylaşır (önceden bu yol kilide hiç girmiyordu).
        # Durum-DEĞİŞTİRMEYEN sorgular (POST'la yapılan okuma, ör. icra/SGK dosya sorgusu,
        # X-Uyap-Read başlıklı) ARTIK istisna DEĞİL — bkz. _runtime modül docstring'i: UYAP
        # eşzamanlı HİÇBİR isteği (yazma/okuma fark etmeksizin) kabul etmiyor ("Eş zamanlı
        # olarak birden fazla sorgulama yapamazsınız!" — kullanıcı bulgusu, 2026-07-12; ör.
        # arka plandaki 30dk'lık otomatik senkron turu ile elle basılan "Yenile" aynı anda
        # UYAP'a gidince ikincisi reddediliyordu). Bu yüzden TÜM POST/PUT/PATCH/DELETE
        # (okuma-ipuçlu olsun olmasın) aynı `interactive_write()` geçidinden geçer.
        if request.method.upper() in WRITE_METHODS:
            async with interactive_write():
                resp = await forward_with_relogin()
        else:
            resp = await forward_with_relogin()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"UYAP'a ulaşılamadı: {e}")

    ctype, govde = _ek_zip_pdf_ac(full_path, resp.headers.get("content-type", ""), resp.content)
    out_body = _rewrite_body(ctype, govde, proxy_base)
    out_headers = _build_response_headers(resp, proxy_base)
    if ctype != resp.headers.get("content-type", ""):
        out_headers = _ek_pdf_basliklari(out_headers, ctype)
    return Response(
        content=out_body,
        status_code=resp.status_code,
        headers=out_headers,
        media_type=ctype,
    )


def resolve_verify_ssl(no_verify: bool) -> bool:
    """UYAP'a karşı TLS doğrulamasını çözer (güvenlik raporu #10). `--no-verify` (verify kapat)
    YALNIZCA `UYAP_ALLOW_INSECURE_TLS=1` açıkça ayarlıyken geçerlidir — bu, üretim derlemelerinde
    bayrağı etkisiz kılar (env yoksa istek YOK SAYILIR ve doğrulama AÇIK kalır: fail‑safe).
    Her iki durumda da belirgin bir MITM uyarısı basılır."""
    if not no_verify:
        return True
    allowed = (os.environ.get("UYAP_ALLOW_INSECURE_TLS", "") or "").strip().lower() in ("1", "true", "yes", "on")
    bar = "!" * 72
    if not allowed:
        print("\n" + bar)
        print("[GUVENLIK] --no-verify YOK SAYILDI: UYAP TLS dogrulamasi ACIK tutuldu.")
        print("  Yargi sistemi trafiginde sertifika dogrulamasini kapatmak MITM'e acik birakir.")
        print("  Gercekten gerekiyorsa (yalnizca gelistirme) UYAP_ALLOW_INSECURE_TLS=1 ayarlayin.")
        print(bar + "\n")
        return True
    print("\n" + bar)
    print("[GUVENLIK][UYARI] UYAP TLS sertifika dogrulamasi DEVRE DISI (--no-verify).")
    print("  Baglanti MITM'e ACIK. Yalnizca guvenilir gelistirme aginda kullanin!")
    print(bar + "\n")
    return False


def configure(host="0.0.0.0", port=8800, pin=None, cert_id=None,
              interval=5, no_verify=False, user="uyap", password=None):
    """Wire up the gateway globals before the server starts. Both the CLI and the GUI
    call this so they launch the exact same engine with the same defaults."""
    global gw, IDLE_INTERVAL, GW_USER, GW_PASS
    IDLE_INTERVAL = interval
    GW_USER = user
    GW_PASS = password
    _init_local_token()  # aynı-makine API istemcileri için yerel-yetki jetonunu kur
    login_args = argparse.Namespace(pin=pin, cert_id=cert_id)
    gw = GatewaySession(login_args, verify_ssl=resolve_verify_ssl(no_verify))
    return gw


def _lan_share_enabled() -> bool:
    """LAN-direct paylaşımı (ofisi tüm yerel ağa açmak) yalnızca açıkça istenirse aktiftir.
    Açık onay: UYAP_LAN_SHARE = 1/true/yes/on."""
    return (os.environ.get("UYAP_LAN_SHARE", "") or "").strip().lower() in ("1", "true", "yes", "on")


def _resolve_bind_host(requested: str) -> str:
    """İstenen bind adresini güvenli varsayılana indirger. Tüm-arayüz adresleri
    (0.0.0.0 / boş / ::) yalnızca LAN paylaşımı AÇIKÇA açıkken (UYAP_LAN_SHARE) korunur;
    aksi halde 127.0.0.1'e düşürülür. Belirli bir adres (ör. seçili LAN IP'si) verildiyse
    bu çağıranın bilinçli tercihidir, dokunulmaz. Not: dış-ağ (WebRTC) paylaşımı bu soketi
    HİÇ kullanmaz (istekler ofiste süreç-içi işlenir), bu yüzden loopback bind onu bozmaz —
    yalnızca aynı-LAN'daki istemcinin doğrudan-LAN kısayolu devre dışı kalır (relay'e düşer)."""
    req = (requested or "").strip()
    wildcard = req in ("", "0.0.0.0", "::", "[::]", "*")
    if wildcard and not _lan_share_enabled():
        return "127.0.0.1"
    if wildcard:
        return req or "0.0.0.0"
    return req


def build_server(host="0.0.0.0", port=8800, in_thread=False, ssl_cert=None, ssl_key=None,
                 lifespan="auto"):
    """Return a configured uvicorn.Server for `app`. Pass in_thread=True when running off
    the main thread (e.g. from the GUI) so uvicorn does not try to install signal
    handlers (which only work on the main thread). ssl_cert/ssl_key verilirse HTTPS
    (LAN kapısı, bkz. lan_tls); aynı app'in ikinci sunucusu lifespan="off" ile kurulmalı
    ki UYAP girişi/keepalive iki kez başlamasın."""
    host = _resolve_bind_host(host)
    if ssl_cert:
        print(f"[i] Yerel ağ kapısı HTTPS olarak açık ({host}:{port}).")
    elif host in ("127.0.0.1", "::1", "localhost") and _lan_share_enabled():
        print(f"[i] Düz HTTP yalnızca yerel arayüzde ({host}:{port}); yerel ağ HTTPS kapısından girer.")
    elif host in ("127.0.0.1", "::1", "localhost"):
        print(f"[i] Proxy yalnızca yerel arayüzde ({host}:{port}) dinliyor; LAN-direct kapalı "
              f"(açmak için UYAP_LAN_SHARE=1). Dış-ağ paylaşımı bundan etkilenmez.")
    else:
        print(f"[!] Proxy TÜM AĞA açık dinliyor ({host}:{port}); aynı LAN'daki cihazlar "
              f"Basic-Auth (GW_PASS) ile erişebilir. İnternete port-forward ETMEYİN.")
    config = uvicorn.Config(app, host=host, port=port, log_level="info", lifespan=lifespan,
                            ssl_certfile=ssl_cert, ssl_keyfile=ssl_key)
    server = uvicorn.Server(config)
    if in_thread:
        server.install_signal_handlers = lambda: None
    return server


def main():
    parser = argparse.ArgumentParser(description="UYAP office-side gateway (reverse proxy).")
    parser.add_argument("--host", default="0.0.0.0",
                        help="Bind address. Reachable only over your private VPN/LAN; never port-forward to the internet.")
    parser.add_argument("--port", type=int, default=8800, help="Listen port (default: 8800)")
    parser.add_argument("--pin", default=os.environ.get("UYAP_PIN"),
                        help="E-imza PIN (ya da UYAP_PIN ortam değişkeni). Kaynağa YAZILMAZ.")
    parser.add_argument("--cert-id", default=None, help="Certificate ID for fully unattended (re)login")
    parser.add_argument("--no-verify", action="store_true", help="Bypass SSL verification toward UYAP")
    parser.add_argument("--interval", type=int, default=5, help="Idle session probe interval in seconds")
    parser.add_argument("--user", default=os.environ.get("GW_USER", "uyap"),
                        help="Basic-Auth username for home clients")
    parser.add_argument("--password", default=os.environ.get("GW_PASS"),
                        help="Basic-Auth password for home clients")
    args = parser.parse_args()

    if not args.pin:
        print("[!] PIN verilmedi. --pin ile ya da UYAP_PIN ortam değişkeniyle sağlayın "
              "(e-imza/giriş için gerekli). Güvenlik: PIN kaynağa GÖMÜLMEZ.")
        sys.exit(2)

    configure(host=args.host, port=args.port, pin=args.pin, cert_id=args.cert_id,
              interval=args.interval, no_verify=args.no_verify,
              user=args.user, password=args.password)

    if not GW_PASS:
        print("[!] UYARI: parola ayarlı değil (--password ya da GW_PASS) — sunucu istekleri reddedecek.")

    print(f"[*] Ağ geçidi http://{args.host}:{args.port} adresinde dinleyecek (sadece VPN/LAN).")
    print("[*] Evden bağlanmak için tarayıcıyı bu adrese yönlendir.")
    build_server(host=args.host, port=args.port).run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Ağ geçidi kapatıldı.")
        sys.exit(0)
