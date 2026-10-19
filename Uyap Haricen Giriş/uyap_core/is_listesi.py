"""
İş Listesi köprüsü (uyap_core/is_listesi.py)
--------------------------------------------
UYAP dosya kartındaki "İş Ata" düğmesi ve "İş Listesi" bağlantısı (uyap_proxy'nin enjekte ettiği
JS) bu yerel uçları çağırır; köprü, isteği KULLANICININ KENDİ CokluUyap hesabıyla cokluuyap.com
/is-listesi/api/* uçlarına iletir.

Neden yerel: /__uyap_agent__/* yolları tünelle OFİSE gider ve ofis ajanı isteği kimin yaptığını
bilmez. /__uyap_kisi__/* ise tünele HİÇ girmez; tarayıcının bağlı olduğu bilgisayardaki süreçte
(istemcide home_client, ofiste uyap_proxy) işlenir — kimlik o bilgisayarda giriş yapan kişidir.

    GET  /__uyap_kisi__/is-listesi/me                → {me, people, office}
    GET  /__uyap_kisi__/is-listesi/tasks?dosya_id=…  → bu dosyaya bağlı işler (görebildikleri)
    POST /__uyap_kisi__/is-listesi/tasks             {title, description, assignee_id, due_at, priority, dosya}
    GET  /__uyap_kisi__/is-listesi/ac                → 303 → cokluuyap.com'da şifresiz oturum (tek kullanımlık kod)

Kimlik uyap_app girişinde configure() ile verilir, çıkışta clear() ile silinir. Jeton bellekte
tutulur, süresi dolmadan yenilenir; sunucu 401 dönerse bir kez yeniden alınır.
"""

import json
import time
import asyncio

import httpx

PREFIX = "/__uyap_kisi__/is-listesi"
_TIMEOUT = 20.0

# ── İş Listesi'nden UYAP dosya kartını açma ──────────────────────────────────────────
# Web arayüzü (cokluuyap.com) "UYAP'ta aç" ile 127.0.0.1:8800/__uyap_kisi__/dosya-ac#<kart JSON>
# açar. Yerel kapı çerezi SameSite=Strict olduğundan başka siteden gelen bu ilk istekte gönderilmez;
# bu yüzden sayfa yerel yetki İSTEMEDEN verilir ve hiçbir şey yapmaz/göstermez: kart verisini
# (# kısmı — sunucuya hiç gitmez) bu sekmenin sessionStorage'ına koyar ve UYAP ana sayfasına geçer.
# Bu geçişi 127.0.0.1'in kendisi başlattığı için çerez gönderilir; kartı UYAP'ın kendi arama
# bileşeni açar (uyap_proxy enjekte betiği, bkz. "İŞ LİSTESİ" bloğu → bekleyenDosyaAc).
DOSYA_AC_PATH = "/__uyap_kisi__/dosya-ac"
DOSYA_AC_SAYFASI = (
    "<!doctype html><html lang='tr'><head><meta charset='utf-8'><title>UYAP dosyası açılıyor…</title>"
    "<meta name='referrer' content='no-referrer'></head><body style='font:14px sans-serif;padding:24px'>"
    "UYAP dosyası açılıyor…<script>(function(){try{var h=decodeURIComponent((location.hash||'').slice(1));"
    "var d=JSON.parse(h);if(d&&typeof d==='object'&&d.dosyaId){sessionStorage.setItem('cokluuyapDosyaAc',"
    "JSON.stringify({t:Date.now(),dosya:d}));}}catch(e){}location.replace('/');})();</script></body></html>"
).encode("utf-8")

_cfg = {"base": "", "office_code": "", "username": "", "password": ""}
_tok = {"value": "", "exp": 0.0}
# Kilit oluşturulduğu olay döngüsüne bağlanır; uyap_app paylaşım/alıcı için ayrı (ve yeniden
# başlatmada yeni) döngüler açtığından döngü değişince yenisi kurulur.
_lock = {"lock": None, "loop": None}


def _get_lock():
    loop = asyncio.get_running_loop()
    if _lock["loop"] is not loop:
        _lock.update(lock=asyncio.Lock(), loop=loop)
    return _lock["lock"]


def configure(api_base, office_code, username, password):
    """api_base: https://www.cokluuyap.com (uyap_app._api_base())."""
    _cfg.update(base=(api_base or "").rstrip("/"), office_code=office_code or "",
                username=username or "", password=password or "")
    _tok.update(value="", exp=0.0)


def clear():
    configure("", "", "", "")


def is_path(path) -> bool:
    p = "/" + (path or "").lstrip("/")
    return p == PREFIX or p.startswith(PREFIX + "/")


def _json(status, data):
    return status, {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}, \
        json.dumps(data, ensure_ascii=False).encode("utf-8")


def _err(status, msg):
    return _json(status, {"ok": False, "error": msg})


async def _token(client, force=False):
    async with _get_lock():
        if not force and _tok["value"] and _tok["exp"] - 60 > time.time():
            return _tok["value"]
        r = await client.post(f"{_cfg['base']}/is-listesi/api/token", json={
            "office_code": _cfg["office_code"], "username": _cfg["username"], "password": _cfg["password"]})
        d = _safe_json(r)
        if r.status_code != 200 or not d.get("token"):
            raise _Fail(r.status_code if r.status_code >= 400 else 502, d.get("error") or "İş Listesi'ne giriş yapılamadı.")
        _tok.update(value=d["token"], exp=time.time() + int(d.get("expires_in") or 3600))
        return _tok["value"]


class _Fail(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


def _safe_json(r):
    try:
        d = r.json()
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


async def _call(method, api_path, query="", body=None):
    """Sunucu ucunu jetonla çağırır; 401'de jetonu bir kez yeniler. (status, dict)"""
    url = f"{_cfg['base']}/is-listesi/api/{api_path}" + (f"?{query}" if query else "")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in (0, 1):
            tok = await _token(client, force=attempt == 1)
            r = await client.request(method, url, json=body, headers={"Authorization": f"Bearer {tok}"})
            if r.status_code != 401 or attempt == 1:
                return r.status_code, _safe_json(r)
    return 502, {}


async def handle(method, path, query, body):
    """(status, headers, body) — uyap_proxy / home_client yanıta çevirir."""
    if not (_cfg["base"] and _cfg["username"] and _cfg["password"]):
        return _err(503, "CokluUyap'a giriş yapılmamış; İş Listesi kullanılamıyor.")
    sub = "/" + (path or "").lstrip("/")
    sub = sub[len(PREFIX):].strip("/")
    try:
        if sub == "me" and method == "GET":
            return _json(*await _call("GET", "me"))
        if sub == "tasks" and method == "GET":
            from urllib.parse import parse_qs, urlencode
            q = parse_qs(query or "")
            dosya_id = (q.get("dosya_id") or [""])[0][:200]
            if not dosya_id:
                return _err(400, "Dosya kimliği gerekli.")
            # Yönetici büronun tüm işlerini, kullanıcı kendisininkileri görür (sunucu süzer).
            return _json(*await _call("GET", "tasks", urlencode({"filtre": "tumu", "dosya_id": dosya_id})))
        if sub == "tasks" and method == "POST":
            try:
                d = json.loads(body or b"{}")
            except ValueError:
                return _err(400, "Geçersiz istek verisi.")
            if not isinstance(d, dict):
                return _err(400, "Geçersiz istek verisi.")
            keep = {k: d[k] for k in ("title", "description", "assignee_id", "due_at", "priority", "dosya") if k in d}
            return _json(*await _call("POST", "tasks", body=keep))
        if sub == "ac" and method == "GET":
            status, d = await _call("POST", "sso-kod", body={})
            if status != 200 or not d.get("url"):
                return _err(status if status >= 400 else 502, d.get("error") or "İş Listesi açılamadı.")
            return 303, {"Location": _cfg["base"] + d["url"], "Cache-Control": "no-store",
                         "Referrer-Policy": "no-referrer"}, b""
    except _Fail as e:
        return _err(e.status, str(e))
    except httpx.HTTPError as e:
        return _err(503, f"cokluuyap.com'a ulaşılamadı: {type(e).__name__}")
    return _err(404, "Bulunamadı.")
