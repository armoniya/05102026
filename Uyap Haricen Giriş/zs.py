"""
/zs İş Takip — HTTP katmanı (zs.py)
-----------------------------------
Yapılacak iş listesi: ana iş + alt işler, kullanıcıya atama, yönetici onayı, tarih-saatli
notlar, şifreli belge yükleme. UYAP/ofis hesaplarından BAĞIMSIZ kullanıcı sistemi.

vendor_server.make_app içinden `zs.register(app, ...)` ile bağlanır; yalnız /zs ile başlayan
rotalar ekler. vendor_server'ı içe AKTARMAZ (döngüsel import olmasın) — oturum anahtarı, giriş
sınırlayıcı ve IP fonksiyonu parametre olarak gelir.

Ortam değişkenleri:
  ZS_ADMIN_USER / ZS_ADMIN_PASSWORD  → hiç kullanıcı yokken ilk yöneticiyi açar
  ZS_FILE_KEY                        → belge şifreleme anahtarı (base64 32 bayt)
  R2_*                               → bkz. zs_r2.py
  ZS_TELEGRAM_GROUPS                 → /is ile iş açılabilen Telegram grup kimlikleri (virgülle); bkz. zs_tg_grup.py
"""

import os
import re
import html
import time
import hmac
import json
import base64
import asyncio
import hashlib
import secrets

from aiohttp import web

import zs_r2
import zs_rapor
import zs_bildirim
import zs_tg_grup
from zs_store import ZsStore, ZsError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ZS_WEB_DIR = os.path.join(BASE_DIR, "webapp", "zs")

COOKIE = "zs_sess"
SESSION_TTL = int(os.environ.get("ZS_SESSION_TTL", "43200"))  # 12 saat
MAX_FILE = int(os.environ.get("ZS_MAX_FILE_MB", "25")) * 1024 * 1024

# Uzantı → (mime, içerik imzaları). UDF/DOCX/XLSX/ZIP birer zip arşividir.
_ZIP = (b"PK\x03\x04",)
_OLE = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",)
ALLOWED = {
    "pdf": ("application/pdf", (b"%PDF-",)),
    "udf": ("application/octet-stream", _ZIP),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", _ZIP),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", _ZIP),
    "zip": ("application/zip", _ZIP),
    "doc": ("application/msword", _OLE),
    "xls": ("application/vnd.ms-excel", _OLE),
    "jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    "jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
    "png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    "tif": ("image/tiff", (b"II*\x00", b"MM\x00*")),
    "tiff": ("image/tiff", (b"II*\x00", b"MM\x00*")),
}


class _Ctx:
    store = None
    secret = b""
    guard = None
    client_ip = None
    storage = None
    file_key = None
    storage_desc = ""
    notifier = None
    setup_error = None      # hiç kullanıcı yokken giriş sayfasında gösterilecek kurulum sorunu


C = _Ctx()


# ── yardımcılar ──────────────────────────────────────────────────────────────────────
async def _db(fn, *a, **kw):
    return await asyncio.to_thread(fn, *a, **kw)


def _json(data, status=200):
    return web.json_response(data, status=status, headers={"Cache-Control": "no-store"},
                             dumps=lambda o: json.dumps(o, ensure_ascii=False))


def _err(msg, status=400):
    return _json({"ok": False, "error": msg}, status=status)


def _make_cookie(user) -> str:
    exp = int(time.time()) + SESSION_TTL
    payload = f"{user['id']}|{user['sess_epoch']}|{exp}"
    sig = hmac.new(C.secret, payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode("ascii")


async def _current_user(request):
    tok = request.cookies.get(COOKIE)
    if not tok:
        return None
    try:
        uid, epoch, exp, sig = base64.urlsafe_b64decode(tok.encode("ascii")).decode().split("|")
        expected = hmac.new(C.secret, f"{uid}|{epoch}|{exp}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected) or int(exp) < time.time():
            return None
        u = await _db(C.store.get_user, int(uid))
    except Exception:
        return None
    if not u or not u["active"] or str(u["sess_epoch"]) != epoch:
        return None
    return u


# ── denetim kaydı (geliştirici) ──────────────────────────────────────────────────────
# Her değiştiren istek (başarılı ya da reddedilmiş), giriş/çıkış ve belge indirme zs_audit tablosuna
# yazılır. Arayüzde ve API'de okuma ucu YOKTUR; yalnız zs_denetim.py ile (sunucuda) okunur.
# Parolalar kaydedilmez. Silme/düzenlemede işlemden önceki hâl ("before") saklanır.
_ACTIONS = {
    ("POST", "/zs/api/me/password"): "parola_degistir",
    ("POST", "/zs/api/users"): "kullanici_ekle",
    ("POST", "/zs/api/users/{id}"): "kullanici_duzenle",
    ("POST", "/zs/api/users/{id}/aktif"): "kullanici_aktiflik",
    ("POST", "/zs/api/users/{id}/sifre"): "kullanici_parola_sifirla",
    ("POST", "/zs/api/tasks"): "is_ac",
    ("PATCH", "/zs/api/tasks/{id}"): "is_duzenle",
    ("DELETE", "/zs/api/tasks/{id}"): "is_sil",
    ("POST", "/zs/api/tasks/{id}/durum"): "is_durum",
    ("POST", "/zs/api/tasks/{id}/devret"): "is_devret",
    ("POST", "/zs/api/tasks/{id}/gorunur"): "is_gorunur_yap",
    ("POST", "/zs/api/tasks/{id}/kisiler"): "kisi_ekle",
    ("POST", "/zs/api/tasks/{id}/kisiler/cikar"): "kisi_cikar",
    ("POST", "/zs/api/tasks/{id}/ek-sure"): "ek_sure_iste",
    ("POST", "/zs/api/ek-sure/{id}/karar"): "ek_sure_karar",
    ("POST", "/zs/api/tasks/{id}/notes"): "not_ekle",
    ("POST", "/zs/api/tasks/{id}/hatirlatici"): "hatirlatici_kur",
    ("POST", "/zs/api/hatirlatici"): "hatirlatici_kur_bagimsiz",
    ("DELETE", "/zs/api/hatirlatici/{id}"): "hatirlatici_sil",
    ("POST", "/zs/api/tasks/{id}/files"): "belge_yukle",
    ("DELETE", "/zs/api/files/{id}"): "belge_sil",
    ("GET", "/zs/api/files/{id}"): "belge_indir",
    ("POST", "/zs/api/bildirim/push"): "bildirim_cihaz_ekle",
    ("POST", "/zs/api/bildirim/push/sil"): "bildirim_cihaz_sil",
    ("POST", "/zs/api/bildirim/telegram"): "bildirim_telegram_kod",
    ("POST", "/zs/api/bildirim/telegram/sil"): "bildirim_telegram_sil",
    ("POST", "/zs/api/bildirim/deneme"): "bildirim_deneme",
}
_SECRET_KEYS = {"password", "old", "new", "pw", "parola", "sifre"}


def _redact(v):
    if isinstance(v, dict):
        return {k: ("***" if str(k).lower() in _SECRET_KEYS else _redact(x)) for k, x in v.items()}
    if isinstance(v, list):
        return [_redact(x) for x in v[:200]]
    if isinstance(v, str) and len(v) > 6000:
        return v[:6000] + "…"
    return v


def _route(request) -> str:
    try:
        return request.match_info.route.resource.canonical
    except Exception:
        return request.path


def _target(request):
    try:
        return int(request.match_info["id"])
    except (KeyError, ValueError, TypeError):
        return None


async def _req_data(request):
    """İstek gövdesi (parolalar maskeli). Çok parçalı yükleme okunmaz (akışı tüketmemek için)."""
    if request.content_type == "multipart/form-data":
        return {"multipart": True}
    try:
        raw = await request.read()  # aiohttp gövdeyi önbelleğe alır; uç sonra request.json() ile yine okur
        return _redact(json.loads(raw)) if raw else None
    except Exception:
        return {"okunamadi": True}


async def _before(request, method, route):
    """İşlemden önceki hâl: silmede işin/belgenin tam kopyası, diğerlerinde satırın kendisi."""
    tid = _target(request)
    if tid is None:
        return None
    try:
        if route.startswith("/zs/api/tasks/{id}"):
            return await _db(C.store.snapshot_task, tid, method == "DELETE")
        if route.startswith("/zs/api/files/{id}"):
            return await _db(C.store.snapshot_file, tid)
        if route.startswith("/zs/api/hatirlatici/{id}"):
            return await _db(C.store.get_reminder, tid)
        if route.startswith("/zs/api/ek-sure/{id}"):
            return await _db(C.store.snapshot_extension, tid)
        if route.startswith("/zs/api/users/{id}"):
            return C.store._public_user(await _db(C.store.get_user, tid))
    except Exception as e:
        return {"snapshot_hatasi": str(e)[:300]}
    return None


def _resp_info(resp):
    """(hata metni, oluşturulan/değişen kaydın kimliği vb.) — yanıtın tamamı saklanmaz
    (ör. kullanıcı eklemede gösterilen parola yanıtta olabilir)."""
    try:
        if resp.content_type != "application/json" or not isinstance(resp.body, (bytes, bytearray)) \
                or len(resp.body) > 500_000:
            return None, None
        d = json.loads(resp.body)
    except Exception:
        return None, None
    res = {}
    for k in ("task", "note", "file", "user", "extension"):
        v = d.get(k)
        if isinstance(v, dict) and "id" in v:
            res[k] = {x: v[x] for x in ("id", "parent_id", "title", "filename", "username", "status", "role") if x in v}
    return d.get("error"), res or None


async def _audit(request, user, action, status, *, t0=None, error=None, data=None, result=None, before=None,
                 target=None, username=None):
    """Denetim satırı yazar; yazılamazsa isteği bozmaz, sunucu günlüğüne düşer."""
    if C.store is None:
        return
    row = dict(user_id=user["id"] if user else None, username=user["username"] if user else username,
               role=user["role"] if user else None, ip=C.client_ip(request),
               user_agent=(request.headers.get("User-Agent") or "")[:300], method=request.method,
               path=request.path_qs[:500], action=action, target_id=target, ok=1 if status < 400 else 0,
               status=status, error=(error or None) and str(error)[:1000], data=data, result=result, before=before,
               ms=int((time.monotonic() - t0) * 1000) if t0 else None)
    try:
        await _db(lambda: C.store.audit(**row))
    except Exception as e:
        print(f"[!] zs denetim kaydı yazılamadı ({action}): {e}")


def _api(admin=False):
    """API uç dekoratörü: oturum + (durum değiştiren isteklerde) X-ZS başlığı + hata çevirisi + denetim kaydı."""
    def deco(fn):
        async def run(request, st):
            if request.method not in ("GET", "HEAD") and request.headers.get("X-ZS") != "1":
                return _err("Geçersiz istek.", 403)
            user = st["user"] = await _current_user(request)
            if not user:
                return _err("Oturum yok ya da süresi doldu.", 401)
            if admin and user["role"] != "admin":
                return _err("Bu işlem yalnız yöneticiye açık.", 403)
            if st["audit"]:
                st["before"] = await _before(request, request.method, st["route"])
            try:
                return await fn(request, user)
            except ZsError as e:
                return _err(str(e))
            except (KeyError, ValueError, TypeError):
                return _err("Geçersiz istek verisi.")

        async def handler(request):
            t0 = time.monotonic()
            route = _route(request)
            st = {"user": None, "before": None, "route": route, "audit": request.method not in ("GET", "HEAD")}
            if not st["audit"]:
                return await run(request, st)
            data = await _req_data(request)
            action = _ACTIONS.get((request.method, route), f"{request.method} {route}")
            if action == "is_durum" and isinstance(data, dict) and data.get("action"):
                action = f"is_durum:{str(data['action'])[:30]}"
            try:
                resp = await run(request, st)
            except Exception as e:
                await _audit(request, st["user"], action, 500, t0=t0, error=f"{type(e).__name__}: {e}", data=data,
                             before=st["before"], target=_target(request))
                raise
            b = st["before"]
            if action == "is_sil" and isinstance(b, dict) and b.get("parent_id"):
                action = "alt_is_sil"
            error, result = _resp_info(resp)
            if resp.status < 400 and C.notifier:
                C.notifier.kick()
            await _audit(request, st["user"], action, resp.status, t0=t0, error=error, data=data, result=result,
                         before=b if resp.status < 400 else None, target=_target(request))
            return resp
        return handler
    return deco


async def _body(request) -> dict:
    try:
        data = await request.json()
    except Exception:
        raise ValueError("json")
    if not isinstance(data, dict):
        raise ValueError("json")
    return data


def _opt_int(v):
    if v is None or v == "":
        return None
    return int(v)


async def _visible_task(user, task_id):
    """(task, root) — görünmüyorsa ZsError (var olup olmadığını sızdırmadan)."""
    t = await _db(C.store.get_task, task_id)
    root = await _db(C.store.root_of, t) if t else None
    if not t or not await _db(C.store.visible_to, root, user):
        raise ZsError("İş bulunamadı.")
    if t["parent_id"] and await _db(C.store.item_hidden, root["id"], "task", t["id"], user["id"]):
        raise ZsError("İş bulunamadı.")
    root.update(await _db(C.store.task_roles, root["id"]))  # assignees + approvers
    return t, root


def _own_task(user, t) -> bool:
    """Kullanıcının kendi açıp kendine atadığı ana iş mi?"""
    return t["created_by"] == user["id"] and t["assignee_id"] == user["id"]


def _locked(root) -> bool:
    return root["status"] in ("onayda", "onaylandi", "tamamlandi")


def _can_edit_sub(user, sub, root) -> bool:
    if user["role"] == "admin":
        return True
    if _locked(root):
        return False
    return user["id"] in root["assignees"] or sub["assignee_id"] == user["id"]


# ── sayfalar ─────────────────────────────────────────────────────────────────────────
_PWA_HEAD = ("<link rel='manifest' href='/zs/manifest.webmanifest'>"
             "<link rel='icon' href='/zs/static/icon-192.png' type='image/png'>"
             "<link rel='apple-touch-icon' href='/zs/static/apple-touch-icon.png'>"
             "<meta name='theme-color' content='#4f46e5'>"
             "<meta name='apple-mobile-web-app-capable' content='yes'>"
             "<meta name='mobile-web-app-capable' content='yes'>"
             "<meta name='apple-mobile-web-app-title' content='İş Takip'>"
             "<meta name='apple-mobile-web-app-status-bar-style' content='default'>")

_LOGIN_CSS = """<style>
:root{--bg:#f3f4f8;--card:#fff;--text:#111827;--muted:#6b7280;--border:#d3d8e3;--accent:#4f46e5;--accent2:#4338ca;--soft:#eef0ff;--err:#dc2626;--errbg:#fee2e2}
@media (prefers-color-scheme:dark){:root{--bg:#0b0e15;--card:#131824;--text:#e8ebf2;--muted:#8b94a7;--border:#2e3850;--accent:#818cf8;--accent2:#a5b4fc;--soft:#1f2347;--err:#f87171;--errbg:#3b1416}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;padding:16px;background:var(--bg);color:var(--text);
 font-family:"Segoe UI Variable Text","Segoe UI",system-ui,sans-serif;
 background-image:radial-gradient(circle at 15% 10%,rgba(99,102,241,.18),transparent 40%),radial-gradient(circle at 85% 90%,rgba(139,92,246,.14),transparent 40%)}
.box{width:100%;max-width:380px;background:var(--card);border:1px solid var(--border);border-radius:16px;padding:32px 28px;box-shadow:0 20px 50px rgba(16,24,40,.12)}
.logo{width:44px;height:44px;border-radius:12px;background:linear-gradient(135deg,#6366f1,#8b5cf6);display:grid;place-items:center;box-shadow:0 6px 16px rgba(99,102,241,.4);margin-bottom:18px}
.logo svg{width:24px;height:24px;stroke:#fff;fill:none;stroke-width:2.5;stroke-linecap:round;stroke-linejoin:round}
h1{font-size:22px;margin:0 0 4px;letter-spacing:-.02em} p{color:var(--muted);font-size:14px;margin:0 0 22px}
label{display:block;font-size:12px;font-weight:600;color:var(--muted);margin:14px 0 6px}
input{width:100%;background:var(--card);border:1px solid var(--border);color:var(--text);padding:11px 12px;border-radius:9px;font:inherit;font-size:15px}
input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--soft)}
button{margin-top:22px;width:100%;background:var(--accent);border:0;color:#fff;padding:12px;border-radius:9px;font:inherit;font-weight:600;font-size:15px;cursor:pointer}
button:hover{background:var(--accent2)}
.msg{background:var(--errbg);color:var(--err);padding:10px 12px;border-radius:9px;font-size:13px;font-weight:500;margin-bottom:4px}
.foot{text-align:center;color:var(--muted);font-size:12px;margin-top:18px}
</style>"""


def _login_page(msg=None, status=200):
    if not msg and C.setup_error and C.store is not None:
        try:
            if C.store.user_count() == 0:
                msg = C.setup_error
            else:
                C.setup_error = None
        except Exception:
            pass
    note = f"<div class='msg'>{html.escape(msg)}</div>" if msg else ""
    body = f"""<!doctype html><html lang='tr'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'><meta name='color-scheme' content='light dark'>
{_PWA_HEAD}<title>İş Takip — Giriş</title>{_LOGIN_CSS}</head><body><form class='box' method='post' action='/zs/login'>
<div class='logo'><svg viewBox='0 0 24 24' aria-hidden='true'><path d='M20 6 9 17l-5-5'/></svg></div>
<h1>İş Takip</h1><p>Görev, onay ve belge takibi</p>{note}
<label for='u'>Kullanıcı adı</label><input id='u' name='username' autocomplete='username' required autofocus>
<label for='p'>Parola</label><input id='p' name='password' type='password' autocomplete='current-password' required>
<button type='submit'>Giriş yap</button><div class='foot'>cokluuyap</div></form></body></html>"""
    return web.Response(text=body, content_type="text/html", charset="utf-8", status=status,
                        headers={"Cache-Control": "no-store"})


async def page_get(request):
    if C.store is None:
        return web.Response(status=503, text="İş Takip şu an kullanılamıyor (veritabanı hatası).")
    user = await _current_user(request)
    if not user:
        return _login_page()
    with open(os.path.join(ZS_WEB_DIR, "index.html"), "rb") as f:
        data = f.read()
    return web.Response(body=data, content_type="text/html", charset="utf-8",
                        headers={"Cache-Control": "no-store"})


async def login_post(request):
    if C.store is None:
        return web.Response(status=503, text="İş Takip şu an kullanılamıyor.")
    key = f"zs-login:{C.client_ip(request)}"
    t0 = time.monotonic()
    wait = C.guard.blocked_for(key)
    if wait:
        await _audit(request, None, "giris_engellendi", 429, t0=t0, error=f"{wait} sn bekleme")
        return _login_page(f"Çok fazla hatalı deneme. {wait} sn sonra tekrar deneyin.", status=429)
    form = await request.post()
    uname = str(form.get("username", ""))[:100]
    user = await _db(C.store.authenticate, uname, form.get("password", ""))
    if not user:
        C.guard.record_fail(key)
        await _audit(request, None, "giris", 401, t0=t0, error="Kullanıcı adı veya parola hatalı.", username=uname)
        return _login_page("Kullanıcı adı veya parola hatalı.", status=401)
    C.guard.record_ok(key)
    await _audit(request, user, "giris", 303, t0=t0)
    resp = _see_other("/zs")
    resp.set_cookie(COOKIE, _make_cookie(user), max_age=SESSION_TTL, httponly=True, samesite="Strict", path="/zs")
    return resp


async def logout_post(request):
    if C.store is not None:
        await _audit(request, await _current_user(request), "cikis", 303)
    resp = _see_other("/zs")
    resp.del_cookie(COOKIE, path="/zs")
    return resp


def _see_other(location):
    # raise HTTPSeeOther yerine düz yanıt: güvenlik middleware'i (Secure çerez, CSP) atlanmasın.
    return web.Response(status=303, headers={"Location": location, "Cache-Control": "no-store"})


async def _slash_redirect(_request):
    return _see_other("/zs")


def _static(name, ctype, binary=False, headers=None):
    async def handler(_request):
        path = os.path.join(ZS_WEB_DIR, name)
        if not os.path.isfile(path):
            return web.Response(status=404, text="Bulunamadı.")
        with open(path, "rb") as f:
            data = f.read()
        hdr = {"Cache-Control": "public, max-age=86400" if binary else "no-cache", **(headers or {})}
        if binary:
            return web.Response(body=data, content_type=ctype, headers=hdr)
        return web.Response(body=data, content_type=ctype, charset="utf-8", headers=hdr)
    return handler


# ── API: oturum / kullanıcılar ───────────────────────────────────────────────────────
@_api()
async def api_me(request, user):
    users = await _db(C.store.list_users)
    people = [{"id": u["id"], "display_name": u["display_name"], "active": u["active"]} for u in users]
    return _json({"ok": True, "me": ZsStore._public_user(user), "people": people,
                  "uploads": bool(C.storage and C.file_key), "max_file_mb": MAX_FILE // (1024 * 1024)})


@_api(admin=True)
async def api_users_list(request, user):
    return _json({"ok": True, "users": await _db(C.store.list_users)})


@_api(admin=True)
async def api_users_create(request, user):
    d = await _body(request)
    pw = (d.get("password") or "").strip() or _gen_password()
    u = await _db(C.store.create_user, d.get("username"), d.get("display_name"), pw, d.get("role") or "user")
    return _json({"ok": True, "user": u, "password": pw})


@_api(admin=True)
async def api_users_active(request, user):
    d = await _body(request)
    uid = int(request.match_info["id"])
    if uid == user["id"] and not d.get("active"):
        raise ZsError("Kendi hesabınızı kapatamazsınız.")
    await _db(C.store.set_active, uid, bool(d.get("active")))
    return _json({"ok": True})


@_api(admin=True)
async def api_users_update(request, user):
    d = await _body(request)
    uid = int(request.match_info["id"])
    role = d.get("role")
    if uid == user["id"] and role is not None and role != user["role"]:
        raise ZsError("Kendi rolünüzü değiştiremezsiniz.")
    u = await _db(C.store.update_user, uid, d.get("display_name"), role)
    return _json({"ok": True, "user": u})


@_api(admin=True)
async def api_users_password(request, user):
    d = await _body(request)
    pw = (d.get("password") or "").strip() or _gen_password()
    await _db(C.store.set_password, int(request.match_info["id"]), pw)
    return _json({"ok": True, "password": pw})


@_api()
async def api_my_password(request, user):
    d = await _body(request)
    if not await _db(C.store.authenticate, user["username"], d.get("old") or ""):
        raise ZsError("Mevcut parola hatalı.")
    await _db(C.store.set_password, user["id"], d.get("new") or "")
    fresh = await _db(C.store.get_user, user["id"])
    resp = _json({"ok": True})
    resp.set_cookie(COOKIE, _make_cookie(fresh), max_age=SESSION_TTL, httponly=True, samesite="Strict", path="/zs")
    return resp


def _gen_password() -> str:
    # Politikayı (büyük/küçük/rakam/işaret) kesin sağlayan rastgele parola.
    while True:
        p = secrets.token_urlsafe(12) + "-7a"
        if any(c.isupper() for c in p) and any(c.islower() for c in p) and any(c.isdigit() for c in p):
            return p


# ── API: işler ───────────────────────────────────────────────────────────────────────
@_api()
async def api_tasks(request, user):
    filtre = request.query.get("filtre", "benim")
    if filtre not in ("benim", "onay", "tumu"):
        filtre = "benim"
    return _json({"ok": True, "tasks": await _db(C.store.list_tree, user, filtre)})


@_api()
async def api_task_get(request, user):
    t, root = await _visible_task(user, int(request.match_info["id"]))
    if t["parent_id"]:
        t = root
    d = await _db(C.store.task_detail, t["id"], user)
    await _db(C.store.mark_seen, user["id"], t["id"])
    # Devirde/görünür yapmada neyin gizlendiğini yalnız işlemi yapan görsün (gizli kayıt
    # olduğunu da ele vermesin) — başka yöneticiler dahil.
    for e in d["events"]:
        if e["kind"] in ("transferred", "published") and e["actor_id"] != user["id"]:
            e["detail"].pop("hidden", None)
            e["detail"].pop("was_private", None)
    return _json({"ok": True, "task": d})


@_api()
async def api_task_create(request, user):
    d = await _body(request)
    parent_id = _opt_int(d.get("parent_id"))
    if parent_id:
        _, root = await _visible_task(user, parent_id)
        if user["role"] != "admin" and (user["id"] not in root["assignees"] or _locked(root)):
            raise ZsError("Bu işe alt iş ekleyemezsiniz.")
        assignee = _opt_int(d.get("assignee_id")) if user["role"] == "admin" else None
    else:
        # Kullanıcı yalnız kendine iş açar; yönetici dilediğine atar.
        assignee = _opt_int(d.get("assignee_id")) if user["role"] == "admin" else user["id"]
    pr = _opt_int(d.get("priority"))
    private = bool(d.get("private")) and not parent_id
    t = await _db(C.store.create_task, user, d.get("title"), parent_id, d.get("description") or "",
                  assignee, _opt_int(d.get("due_at")), private=private,
                  priority=1 if pr is None else pr)
    # Yönetici işi açarken ek atanan / onay yetkilisi de seçebilir.
    if user["role"] == "admin" and not parent_id and not private:
        for role, key in (("assignee", "assignees"), ("approver", "approvers")):
            skip = {t["assignee_id"]} if role == "assignee" else set()
            for uid in sorted({int(x) for x in (d.get(key) or [])} - skip):
                await _db(C.store.add_member, user, t["id"], uid, role, "İş açılırken eklendi.")
    return _json({"ok": True, "task": t})


@_api()
async def api_task_update(request, user):
    d = await _body(request)
    t, root = await _visible_task(user, int(request.match_info["id"]))
    fields = {}
    for k in ("title", "description"):
        if k in d:
            fields[k] = d[k]
    for k in ("assignee_id", "due_at", "priority"):
        if k in d:
            fields[k] = _opt_int(d[k])
    if "done" in d:
        fields["done"] = bool(d["done"])
    if d.get("move") in (-1, 1):
        fields["move"] = d["move"]
    if user["role"] != "admin":
        if not t["parent_id"]:
            # Kullanıcı yalnız kendi açıp kendine atadığı işi (başlık/açıklama/son tarih) düzenler.
            if not _own_task(user, t) or _locked(t):
                raise ZsError("Bu işi yalnız yönetici düzenler.")
            if "assignee_id" in fields or "move" in fields:
                raise ZsError("Atamayı yalnız yönetici değiştirir.")
        else:
            if not _can_edit_sub(user, t, root):
                raise ZsError("Bu alt işi değiştiremezsiniz.")
            if "assignee_id" in fields or "due_at" in fields:
                raise ZsError("Atama ve son tarihi yalnız yönetici değiştirir.")
    return _json({"ok": True, "task": await _db(C.store.update_task, user, t["id"], fields)})


@_api()
async def api_task_delete(request, user):
    t, root = await _visible_task(user, int(request.match_info["id"]))
    if user["role"] != "admin":
        if _locked(root) or user["id"] not in root["assignees"] or (not t["parent_id"] and not _own_task(user, t)):
            raise ZsError("Bu işi silemezsiniz.")
    keys = await _db(C.store.delete_task, user, t["id"])
    for k in keys:
        await _safe_storage_delete(k)
    return _json({"ok": True})


@_api()
async def api_task_transition(request, user):
    d = await _body(request)
    t, _ = await _visible_task(user, int(request.match_info["id"]))
    out = await _db(C.store.transition, user, t["id"], d.get("action") or "", d.get("reason") or "")
    return _json({"ok": True, "task": out})


@_api(admin=True)
async def api_task_transfer(request, user):
    """Yönetici işi devreder; devir öncesi kayıtların hangilerinin yeni kişiye görüneceğini seçer."""
    d = await _body(request)
    t, _ = await _visible_task(user, int(request.match_info["id"]))
    show = d.get("show") or {}
    hide = {k: not bool(show.get(k, True)) for k in ("subtasks", "notes", "files", "history")}
    to = _opt_int(d.get("to_user_id"))
    if to is None:
        raise ZsError("Devredilecek kişiyi seçin.")
    out = await _db(C.store.transfer, user, t["id"], to, hide)
    return _json({"ok": True, "task": out})


@_api()
async def api_task_publish(request, user):
    """Özel işin sahibi işi kimseye atamadan görünür yapar; önceki kayıtlardan hangilerinin
    görüneceğini seçer (gizlenenleri yalnız kendisi görmeye devam eder)."""
    d = await _body(request)
    t, _ = await _visible_task(user, int(request.match_info["id"]))
    if not t["is_private"] or t["created_by"] != user["id"]:
        raise ZsError("Yalnız kendi özel işinizi görünür yapabilirsiniz.")
    show = d.get("show") or {}
    hide = {k: not bool(show.get(k, True)) for k in ("subtasks", "notes", "files", "history")}
    return _json({"ok": True, "task": await _db(C.store.transfer, user, t["id"], None, hide)})


@_api(admin=True)
async def api_member_add(request, user):
    """Yönetici işe ek atanan (role=assignee) ya da onay yetkilisi (role=approver) ekler; not zorunlu."""
    d = await _body(request)
    t, _ = await _visible_task(user, int(request.match_info["id"]))
    roles = await _db(C.store.add_member, user, t["id"], _opt_int(d.get("user_id")), d.get("role"), d.get("note"))
    return _json({"ok": True, **roles})


@_api(admin=True)
async def api_member_remove(request, user):
    d = await _body(request)
    t, _ = await _visible_task(user, int(request.match_info["id"]))
    roles = await _db(C.store.remove_member, user, t["id"], _opt_int(d.get("user_id")), d.get("role"), d.get("note"))
    return _json({"ok": True, **roles})


@_api()
async def api_extension_request(request, user):
    d = await _body(request)
    t, _ = await _visible_task(user, int(request.match_info["id"]))
    x = await _db(C.store.request_extension, user, t["id"], _opt_int(d.get("new_due")), d.get("reason"))
    return _json({"ok": True, "extension": x})


@_api()
async def api_extension_decide(request, user):
    d = await _body(request)
    x = await _db(C.store.get_extension, int(request.match_info["id"]))
    if not x:
        raise ZsError("Talep bulunamadı.")
    await _visible_task(user, x["task_id"])
    out = await _db(C.store.decide_extension, user, x["id"], bool(d.get("approve")), d.get("note"))
    return _json({"ok": True, "extension": out})


@_api()
async def api_note_create(request, user):
    d = await _body(request)
    t, root = await _visible_task(user, int(request.match_info["id"]))
    n = await _db(C.store.add_note, user, root["id"], d.get("body"), _opt_int(d.get("note_at")))
    return _json({"ok": True, "note": n})


# ── API: hatırlatıcılar ──────────────────────────────────────────────────────────────
# Kurallar (kime kurulabilir):
#   işten bağımsız      → kullanıcı kendine; yönetici herkese. Kişiseldir: yalnız alıcı ve kuran görür/siler.
#   ana iş              → kullanıcı kendine; yönetici işi görebilen herkese (atadığı kişiler dahil).
#   alt iş              → işi görebilen herkes, işi görebilen herkese.
# Silme: kuran ya da alıcı; işe bağlı olanları yönetici de silebilir.
def _reminder_public(r):
    out = {k: r.get(k) for k in ("id", "task_id", "sub_id", "user_id", "created_by", "remind_at", "note", "title",
                                 "sent_at", "created_at")}
    for k in ("task_title", "sub_title", "task_private"):
        if k in r:
            out[k] = r[k]
    return out


async def _create_reminder(user, task_id, d):
    target = _opt_int(d.get("user_id")) or user["id"]
    other = None
    if target != user["id"]:
        other = await _db(C.store.get_user, target)
        if not other or not other["active"]:
            raise ZsError("Kişi bulunamadı.")
    when, note = _opt_int(d.get("remind_at")), d.get("note") or ""
    if task_id is None:
        if other and user["role"] != "admin":
            raise ZsError("İşe bağlı olmayan hatırlatıcıyı yalnız kendinize kurabilirsiniz.")
        return await _db(C.store.add_reminder, user, None, target, when, note, d.get("title") or "")
    t, root = await _visible_task(user, task_id)
    sub = t if t["id"] != root["id"] else None
    if other:
        if not sub and user["role"] != "admin":
            raise ZsError("Bu işte yalnız kendinize hatırlatıcı kurabilirsiniz (alt işlerde herkese kurulabilir).")
        if not await _db(C.store.visible_to, root, other):
            raise ZsError("Bu kişi işi göremiyor; ona hatırlatıcı kurulamaz.")
    return await _db(C.store.add_reminder, user, root["id"], target, when, note, "", sub["id"] if sub else None)


@_api()
async def api_reminder_list(request, user):
    t, root = await _visible_task(user, int(request.match_info["id"]))
    rows = await _db(C.store.list_reminders, root["id"], user)
    return _json({"ok": True, "reminders": [_reminder_public(r) for r in rows]})


@_api()
async def api_reminder_create(request, user):
    r = await _create_reminder(user, int(request.match_info["id"]), await _body(request))
    return _json({"ok": True, "reminder": _reminder_public(r)})


@_api()
async def api_free_reminder_list(request, user):
    rows = await _db(C.store.list_free_reminders, user, int(time.time()) - 60 * 86400)
    return _json({"ok": True, "reminders": [_reminder_public(r) for r in rows]})


@_api()
async def api_free_reminder_create(request, user):
    """Genel "Hatırlatıcı kur": task_id verilirse o işe/alt işe bağlanır, verilmezse bağımsızdır."""
    d = await _body(request)
    r = await _create_reminder(user, _opt_int(d.get("task_id")), d)
    return _json({"ok": True, "reminder": _reminder_public(r)})


@_api()
async def api_my_reminders(request, user):
    """Hatırlatıcılar ekranı: bana kurulanlar + benim kurduklarım (işe bağlı ve bağımsız). Gönderilmişler
    son 60 gün. İşe bağlı olanlar yalnız iş hâlâ görünürse listelenir."""
    rows = await _db(C.store.list_my_reminders, user, int(time.time()) - 60 * 86400)
    seen = {}
    out = []
    for r in rows:
        if r["task_id"] is not None:
            if r["task_id"] not in seen:
                root = await _db(C.store.get_task, r["task_id"])
                seen[r["task_id"]] = bool(root) and await _db(C.store.visible_to, root, user)
            if not seen[r["task_id"]]:
                continue
        out.append(_reminder_public(r))
    return _json({"ok": True, "reminders": out})


@_api()
async def api_reminder_delete(request, user):
    r = await _db(C.store.get_reminder, int(request.match_info["id"]))
    admin_ok = user["role"] == "admin" and r and r["task_id"] is not None
    if not r or (not admin_ok and user["id"] not in (r["user_id"], r["created_by"])):
        raise ZsError("Hatırlatıcı bulunamadı.")
    await _db(C.store.delete_reminder, r["id"])
    return _json({"ok": True})


# ── API: raporlar ────────────────────────────────────────────────────────────────────
@_api()
async def api_report(request, user):
    """Yönetici herkesi (kişi süzgeci isteğe bağlı) görür; kullanıcı yalnız kendi işlerini."""
    q = request.query
    admin = user["role"] == "admin"
    try:
        start, end = zs_rapor.parse_range(q.get("bas"), q.get("bit"))
        group = q.get("grup") or "hafta"
        person = _opt_int(q.get("kisi")) if admin else user["id"]
        now = int(time.time())
        src = await _db(C.store.report_source, user["id"])
        users = await _db(C.store.list_users) if admin else []
        return _json({"ok": True,
                      "kisiler": zs_rapor.kisi_raporu(src, users, start, end, now),
                      "donemler": zs_rapor.zaman_raporu(src, start, end, group, now, person),
                      "isler": zs_rapor.is_listesi(src, start, end, person), "now": now})
    except zs_rapor.RaporError as e:
        raise ZsError(str(e))


@_api()
async def api_activity(request, user):
    """Kişinin bir dönemde yaptıkları. Yönetici herkesinkini, kullanıcı yalnız kendisininkini görür."""
    q = request.query
    person = (_opt_int(q.get("kisi")) or user["id"]) if user["role"] == "admin" else user["id"]
    try:
        start, end = zs_rapor.parse_range(q.get("bas"), q.get("bit"))
        if end - start > 366 * 86400:
            raise zs_rapor.RaporError("Etkinlik raporu en fazla 1 yıl olabilir.")
    except zs_rapor.RaporError as e:
        raise ZsError(str(e))
    items = await _db(C.store.activity_source, user["id"], person, start, end)
    return _json({"ok": True, "kisi": person, "items": items, **zs_rapor.etkinlik(items, start, end),
                  "now": int(time.time())})


# ── API: bildirim ayarları ───────────────────────────────────────────────────────────
def _notifier():
    if not C.notifier:
        raise ZsError("Bildirimler bu sunucuda kapalı.")
    return C.notifier


@_api()
async def api_notify_get(request, user):
    n = C.notifier
    links = await _db(C.store.tg_get, [user["id"]]) if n else []
    return _json({"ok": True,
                  "push": {"enabled": bool(n and n.vapid_key), "key": n.vapid_pub if n else None,
                           "devices": await _db(C.store.push_count, user["id"]) if n else 0},
                  "telegram": {"enabled": bool(n and n.tg_bot), "bot": n.tg_bot if n else None,
                               "linked": bool(links), "name": links[0]["tg_name"] if links else None}})


@_api()
async def api_notify_push_add(request, user):
    n = _notifier()
    if not n.vapid_key:
        raise ZsError("Telefon bildirimi bu sunucuda kapalı.")
    d = await _body(request)
    ep, keys = str(d.get("endpoint") or ""), d.get("keys") or {}
    if not ep.startswith("https://") or len(ep) > 1000 or not keys.get("p256dh") or not keys.get("auth"):
        raise ZsError("Bildirim aboneliği geçersiz.")
    await _db(C.store.push_add, user["id"], ep, str(keys["p256dh"])[:200], str(keys["auth"])[:100],
              request.headers.get("User-Agent", ""))
    return _json({"ok": True, "devices": await _db(C.store.push_count, user["id"])})


@_api()
async def api_notify_push_remove(request, user):
    d = await _body(request)
    await _db(C.store.push_remove, str(d.get("endpoint") or ""), user["id"])
    return _json({"ok": True, "devices": await _db(C.store.push_count, user["id"])})


@_api()
async def api_notify_tg_link(request, user):
    n = _notifier()
    if not n.tg_bot:
        raise ZsError("Telegram bildirimi bu sunucuda kapalı.")
    code = await _db(C.store.tg_code_new, user["id"])
    return _json({"ok": True, "url": n.tg_link_url(code)})


@_api()
async def api_notify_tg_unlink(request, user):
    await _db(C.store.tg_unlink, user["id"])
    return _json({"ok": True})


@_api()
async def api_notify_test(request, user):
    devices, tg = await _notifier().test(user["id"])
    if not devices and not tg:
        raise ZsError("Açık bildirim kanalınız yok.")
    return _json({"ok": True, "devices": devices, "telegram": tg})


async def telegram_hook(request):
    """Telegram güncellemeleri (webhook). Gizli başlık eşleşmezse yok sayılır."""
    n = C.notifier
    if not n or not n.tg_token or not hmac.compare_digest(
            request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), n.tg_secret):
        return web.Response(status=404, text="Bulunamadı.")
    try:
        upd = await request.json()
    except Exception:
        return web.Response(text="ok")

    async def run():
        try:
            await n.tg_update(upd)
        except Exception as e:
            print(f"[!] zs Telegram güncellemesi işlenemedi: {e}")
    # Grupta belge indirme uzun sürebilir; Telegram yanıt gecikirse güncellemeyi yeniden gönderir.
    n._spawn(run())
    return web.Response(text="ok")


# ── API: belgeler ────────────────────────────────────────────────────────────────────
def _clean_filename(name: str) -> str:
    name = os.path.basename((name or "").replace("\\", "/"))
    name = re.sub(r"[\x00-\x1f\x7f\"<>|:*?/]", "_", name).strip(" .")
    return (name or "belge")[-150:]


@_api()
async def api_file_upload(request, user):
    if not (C.storage and C.file_key):
        raise ZsError("Belge yükleme bu sunucuda kapalı (depo ya da şifreleme anahtarı ayarlı değil).")
    t, root = await _visible_task(user, int(request.match_info["id"]))
    if user["role"] != "admin" and _locked(root):
        raise ZsError("Onaydaki/onaylanmış işe belge eklenemez.")
    reader = await request.multipart()
    part = await reader.next()
    while part is not None and part.name != "file":
        part = await reader.next()
    if part is None or not part.filename:
        raise ZsError("Dosya seçilmedi.")
    filename = _clean_filename(part.filename)
    _file_ext(filename)
    buf = bytearray()
    while True:
        chunk = await part.read_chunk(256 * 1024)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > MAX_FILE:
            raise ZsError(_too_big())
    f = await _save_file(user, root["id"], filename, bytes(buf))
    return _json({"ok": True, "file": f})


def _too_big():
    return f"Dosya {MAX_FILE // (1024 * 1024)} MB sınırını aşıyor."


def _file_ext(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED:
        raise ZsError("Bu dosya türü kabul edilmiyor (pdf, udf, doc/docx, xls/xlsx, jpg/png, tif, zip).")
    return ext


def _check_file(filename, data):
    """Tür + boyut + içerik imzası denetimi; mime döndürür (web yüklemesi ve Telegram ortak)."""
    ext = _file_ext(filename)
    if len(data) > MAX_FILE:
        raise ZsError(_too_big())
    if not data:
        raise ZsError("Dosya boş.")
    mime, sigs = ALLOWED[ext]
    if not any(data[:len(s)] == s for s in sigs):
        raise ZsError("Dosya içeriği uzantısıyla uyuşmuyor.")
    return mime


async def _save_file(user, task_id, filename, data):
    """Denetler, şifreler, depoya yazar, işe kaydeder (filename önceden _clean_filename'den geçmiş olmalı)."""
    mime = _check_file(filename, data)
    key = secrets.token_hex(16)
    blob = await asyncio.to_thread(zs_r2.encrypt, C.file_key, data, key)
    try:
        await C.storage.put(key, blob)
    except Exception as e:
        print(f"[!] zs belge yükleme hatası: {e}")
        raise ZsError("Belge depoya yazılamadı; tekrar deneyin.")
    return await _db(C.store.add_file, user, task_id, filename, mime, len(data), key,
                     hashlib.sha256(data).hexdigest())


async def api_file_download(request):
    t0 = time.monotonic()
    user = await _current_user(request)
    resp = await _file_download(request, user)
    meta = None
    if resp.status < 400:
        meta = await _db(C.store.snapshot_file, _target(request))
    await _audit(request, user, "belge_indir", resp.status, t0=t0, target=_target(request), before=meta,
                 error=None if resp.status < 400 else resp.text)
    return resp


async def _file_download(request, user):
    if not user:
        return web.Response(status=401, text="Oturum yok.")
    try:
        f = await _db(C.store.get_file, int(request.match_info["id"]))
        if not f:
            raise ZsError("Belge bulunamadı.")
        await _visible_task(user, f["task_id"])
        if await _db(C.store.item_hidden, f["task_id"], "file", f["id"], user["id"]):
            raise ZsError("Belge bulunamadı.")
    except (ZsError, ValueError):
        return web.Response(status=404, text="Belge bulunamadı.")
    if not (C.storage and C.file_key):
        return web.Response(status=503, text="Belge deposu kapalı.")
    try:
        blob = await C.storage.get(f["storage_key"])
        data = await asyncio.to_thread(zs_r2.decrypt, C.file_key, blob, f["storage_key"])
    except Exception as e:
        print(f"[!] zs belge okuma hatası (id={f['id']}): {e}")
        return web.Response(status=502, text="Belge okunamadı.")
    from urllib.parse import quote
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", f["filename"])
    return web.Response(body=data, headers={
        "Content-Type": "application/octet-stream",
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(f['filename'])}",
        "Cache-Control": "no-store"})


@_api()
async def api_file_delete(request, user):
    f = await _db(C.store.get_file, int(request.match_info["id"]))
    if not f:
        raise ZsError("Belge bulunamadı.")
    _, root = await _visible_task(user, f["task_id"])
    if await _db(C.store.item_hidden, f["task_id"], "file", f["id"], user["id"]):
        raise ZsError("Belge bulunamadı.")
    if user["role"] != "admin" and (f["uploader_id"] != user["id"] or _locked(root)):
        raise ZsError("Bu belgeyi silemezsiniz.")
    key = await _db(C.store.delete_file, user, f["id"])
    await _safe_storage_delete(key)
    return _json({"ok": True})


async def _safe_storage_delete(key):
    if not C.storage:
        return
    try:
        await C.storage.delete(key)
    except Exception as e:
        print(f"[!] zs depodan silinemedi ({key}): {e}")


# ── Telegram grubu: /is ile iş açma, bot mesajına yanıtla belge/not ekleme ───────────
# Ayrıştırma zs_tg_grup.py'de. Yetkiler web ile aynı: kullanıcı yalnız kendine iş açar, yönetici
# dilediğine; belge ekleme kilitli (onaydaki/bitmiş) işte yalnız yönetici. Gönderen ve etiketlenenler
# botla özel sohbetten bağlanmış olmalı (zs_telegram). Yalnız ZS_TELEGRAM_GROUPS'taki gruplarda çalışır.
_TG_LINK_HELP = "İş Takip → Bildirimler → “Telegram'ı bağla” ile botla özel sohbetten bağlanılmalı."


async def _tg_audit(msg, user, action, ok, *, target=None, error=None, data=None, result=None):
    if C.store is None:
        return
    chat = msg.get("chat") or {}
    row = dict(user_id=user["id"] if user else None, username=user["username"] if user else None,
               role=user["role"] if user else None, ip=None, user_agent="Telegram", method="TG",
               path=f"telegram/{chat.get('id')}/{msg.get('message_id')}", action=action, target_id=target,
               ok=1 if ok else 0, status=200 if ok else 400, error=(error or None) and str(error)[:1000],
               data=data, result=result)
    try:
        await _db(lambda: C.store.audit(**row))
    except Exception as e:
        print(f"[!] zs denetim kaydı yazılamadı ({action}): {e}")


async def _tg_fetch(att):
    """Telegram ekini indirir, denetler → (dosya adı, bayt)."""
    if att.get("unsupported"):
        raise ZsError(f"Bu tür ek ({att['unsupported']}) işe eklenemez; belge ya da fotoğraf gönderin.")
    if not (C.storage and C.file_key):
        raise ZsError("Belge yükleme bu sunucuda kapalı.")
    limit = min(MAX_FILE, zs_tg_grup.TG_MAX_DOWNLOAD)
    filename = _clean_filename(att["filename"])
    _file_ext(filename)
    if att["size"] > limit:
        raise ZsError(f"Dosya {limit // (1024 * 1024)} MB sınırını aşıyor (Telegram botları daha büyüğünü indiremez).")
    try:
        data = await C.notifier.tg_download(att["file_id"], limit)
    except ValueError:
        raise ZsError(f"Dosya {limit // (1024 * 1024)} MB sınırını aşıyor.")
    except Exception as e:
        print(f"[!] zs Telegram dosyası indirilemedi: {e}")
        raise ZsError("Dosya Telegram'dan indirilemedi; tekrar deneyin.")
    _check_file(filename, data)
    return filename, data


async def _tg_resolve(mentions):
    """Etiketlenenler → zs kullanıcıları (sıralı, tekrarsız). Bağlı olmayan varsa ZsError."""
    users, missing = [], []
    for m in mentions:
        u = await _db(C.store.tg_user_by_username, m[1]) if m[0] == "username" \
            else await _db(C.store.tg_user_by_chat, m[1])
        if not u:
            missing.append("@" + m[1] if m[0] == "username" else (m[2] or "?"))
        elif u["id"] not in [x["id"] for x in users]:
            users.append(u)
    if missing:
        raise ZsError(f"İş Takip'e bağlı değil: {', '.join(missing)}. {_TG_LINK_HELP}")
    return users


def _tg_task_link(task_id):
    base = C.notifier.public_url if C.notifier else ""
    return f"\n<a href=\"{html.escape(base)}/zs#is-{task_id}\">İşi aç</a>" if base else ""


async def _tg_open_task(user, msg, cmd):
    reply = msg.get("reply_to_message") or {}
    att = zs_tg_grup.attachment(msg) or zs_tg_grup.attachment(reply)
    people = await _tg_resolve(cmd["mentions"])
    if user["role"] != "admin" and any(u["id"] != user["id"] for u in people):
        raise ZsError("Başkasına iş yalnız yönetici atayabilir; kendinize açmak için kimseyi etiketlemeyin.")
    assignee = people[0] if people else user
    title = cmd["title"] or (f"Telegram belgesi: {att['filename']}" if att and att.get("filename") else "")
    if not title:
        raise ZsError("Ne yapılacağını yazın: /is @kişi açıklama")
    desc = cmd["description"]
    quoted, _ = zs_tg_grup.text_and_entities(reply)
    if quoted.strip() and not (reply.get("from") or {}).get("is_bot"):
        who = zs_tg_grup.sender_name(reply.get("from"))
        desc = (desc + "\n\n" if desc else "") + f"Yanıtlanan mesaj ({who}):\n{quoted.strip()}"
    file = await _tg_fetch(att) if att else None     # dosya sorunluysa iş hiç açılmaz
    t = await _db(C.store.create_task, user, title, None, desc, assignee["id"])
    for u in people[1:]:
        await _db(C.store.add_member, user, t["id"], u["id"], "assignee", "Telegram'dan iş açılırken eklendi.")
    lines = [f"✅ İş açıldı: <b>{html.escape(t['title'])}</b>",
             "Atanan: " + ", ".join(html.escape(u["display_name"]) for u in (people or [user]))]
    f, file_err = None, None
    if file:
        try:
            f = await _save_file(user, t["id"], *file)
            lines.append(f"Belge: {html.escape(f['filename'])}")
        except ZsError as e:
            file_err = str(e)
            lines.append(f"⚠️ Belge eklenemedi: {html.escape(file_err)}")
    await _tg_audit(msg, user, "tg_is_ac", True, target=t["id"],
                    data={"title": title, "mentions": [list(m) for m in cmd["mentions"]],
                          "file": file[0] if file else None},
                    result={"task": {"id": t["id"]}, "file": {"id": f["id"]} if f else None}, error=file_err)
    return "\n".join(lines) + _tg_task_link(t["id"]) + \
        "\n<i>Bu mesaja yanıt vererek işe belge ya da not ekleyebilirsiniz.</i>", t["id"]


async def _tg_add_to_task(user, msg, task_id):
    att = zs_tg_grup.attachment(msg)
    text, _ = zs_tg_grup.text_and_entities(msg)
    text = text.strip()
    if not att and not text:
        return None, None
    t, root = await _visible_task(user, task_id)
    if att and user["role"] != "admin" and _locked(root):
        raise ZsError("Onaydaki/onaylanmış işe belge eklenemez.")
    file = await _tg_fetch(att) if att else None
    lines = [f"📎 <b>{html.escape(root['title'])}</b>"]
    f = n = None
    if file:
        f = await _save_file(user, root["id"], *file)
        lines.append(f"Belge eklendi: {html.escape(f['filename'])}")
    if text:
        n = await _db(C.store.add_note, user, root["id"], text)
        lines.append("Not eklendi.")
    await _tg_audit(msg, user, "tg_ekle", True, target=root["id"], data={"file": file[0] if file else None, "note": bool(text)},
                    result={"file": {"id": f["id"]} if f else None, "note": {"id": n["id"]} if n else None})
    return "\n".join(lines) + _tg_task_link(root["id"]), root["id"]


async def tg_group_message(msg):
    """Notifier.tg_update grup mesajlarını buraya yollar."""
    n = C.notifier
    if C.store is None or not n:
        return
    chat_id, mid, frm = msg["chat"]["id"], msg.get("message_id"), msg.get("from") or {}
    if frm.get("is_bot") or not frm.get("id"):
        return
    cmd = zs_tg_grup.parse_command(msg, n.tg_bot)
    reply = msg.get("reply_to_message") or {}
    task_id = None
    if not cmd and (reply.get("from") or {}).get("is_bot"):
        task_id = await _db(C.store.tg_msg_task, chat_id, reply.get("message_id"))
    if not cmd and not task_id:
        return                          # sıradan grup sohbeti
    if chat_id not in zs_tg_grup.allowed_groups():
        if cmd:
            await n.tg_reply(chat_id, "Bu grupta iş açma kapalı. Grup kimliği: "
                                      f"<code>{chat_id}</code> (sunucuda ZS_TELEGRAM_GROUPS'a eklenmeli).", mid)
        return
    user = await _db(C.store.tg_user_by_chat, frm["id"])
    if not user:
        await n.tg_reply(chat_id, f"Sizi İş Takip'te tanımıyorum. {_TG_LINK_HELP}", mid)
        return
    await _db(C.store.tg_touch, frm["id"], frm.get("username"))
    try:
        if cmd:
            text, tid = await _tg_open_task(user, msg, cmd)
        else:
            text, tid = await _tg_add_to_task(user, msg, task_id)
    except ZsError as e:
        await _tg_audit(msg, user, "tg_is_ac" if cmd else "tg_ekle", False, target=task_id, error=str(e))
        await n.tg_reply(chat_id, f"⚠️ {html.escape(str(e))}", mid)
        return
    if not text:
        return
    n.kick()
    sent = await n.tg_reply(chat_id, text, mid)
    if sent:
        await _db(C.store.tg_msg_save, chat_id, sent, tid)


# ── kurulum ──────────────────────────────────────────────────────────────────────────
def _bootstrap_admin():
    user = (os.environ.get("ZS_ADMIN_USER") or "").strip()
    pw = (os.environ.get("ZS_ADMIN_PASSWORD") or "").strip()  # yapıştırırken kalan boşluklar girişi bozmasın
    C.setup_error = None
    if C.store.user_count() > 0:
        return
    if not (user and pw):
        C.setup_error = "Henüz yönetici hesabı yok: sunucuda ZS_ADMIN_USER ve ZS_ADMIN_PASSWORD ayarlanmalı."
        print(f"[!] İş Takip (/zs): {C.setup_error}")
        return
    try:
        C.store.create_user(user, user, pw, "admin")
        print(f"[+] İş Takip (/zs): ilk yönetici oluşturuldu ({user.lower()}).")
    except ZsError as e:
        # Değerler değil, yalnız kural ihlali gösterilir (ör. "Parola en az bir özel işaret içermeli").
        C.setup_error = f"İlk yönetici hesabı oluşturulamadı: {e} (ZS_ADMIN_USER / ZS_ADMIN_PASSWORD'ü düzeltip servisi yeniden başlatın.)"
        print(f"[!] İş Takip (/zs): {C.setup_error}")


def register(app, session_secret, guard, client_ip, store=None):
    """Rotaları ekler. Veritabanı açılamazsa site çökmez; /zs 503 döner."""
    C.secret = hashlib.sha256((session_secret + "|zs").encode()).digest()
    C.guard, C.client_ip = guard, client_ip
    try:
        C.store = store or ZsStore()
        _bootstrap_admin()
    except Exception as e:
        C.store = None
        print(f"[!] İş Takip (/zs) veritabanı açılamadı: {e}")
    C.file_key = zs_r2.load_key()
    C.storage, C.storage_desc = zs_r2.make_storage()
    if C.store:
        print(f"[*] İş Takip (/zs): {C.store.backend_name}; belgeler: {C.storage_desc}; "
              f"şifreleme anahtarı {'VAR' if C.file_key else 'YOK → yükleme kapalı'}.")
        try:
            C.notifier = zs_bildirim.Notifier(C.store)
            C.notifier.group_handler = tg_group_message
            app.on_startup.append(C.notifier.start)
            app.on_cleanup.append(C.notifier.stop)
            print(f"[*] İş Takip bildirim: {C.notifier.describe()}")
        except Exception as e:
            C.notifier = None
            print(f"[!] İş Takip bildirimleri başlatılamadı: {e}")

    r = app.router
    r.add_get("/zs", page_get)
    # Sonda eğik çizgiyle yazılan/paylaşılan adres 404 vermesin.
    r.add_get("/zs/", _slash_redirect)
    r.add_post("/zs/login", login_post)
    r.add_post("/zs/logout", logout_post)
    r.add_get("/zs/static/zs.js", _static("zs.js", "application/javascript"))
    r.add_get("/zs/static/zs.css", _static("zs.css", "text/css"))
    for icon_name in ("icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png", "badge-72.png"):
        r.add_get(f"/zs/static/{icon_name}", _static(icon_name, "image/png", binary=True))
    r.add_get("/zs/manifest.webmanifest", _static("manifest.webmanifest", "application/manifest+json"))
    r.add_get("/zs/sw.js", _static("sw.js", "application/javascript", headers={"Service-Worker-Allowed": "/zs"}))
    r.add_get("/zs/api/bildirim", api_notify_get)
    r.add_post("/zs/api/bildirim/push", api_notify_push_add)
    r.add_post("/zs/api/bildirim/push/sil", api_notify_push_remove)
    r.add_post("/zs/api/bildirim/telegram", api_notify_tg_link)
    r.add_post("/zs/api/bildirim/telegram/sil", api_notify_tg_unlink)
    r.add_post("/zs/api/bildirim/deneme", api_notify_test)
    r.add_post("/zs/telegram/hook", telegram_hook)
    r.add_get("/zs/api/me", api_me)
    r.add_post("/zs/api/me/password", api_my_password)
    r.add_get("/zs/api/users", api_users_list)
    r.add_post("/zs/api/users", api_users_create)
    r.add_post("/zs/api/users/{id:\\d+}", api_users_update)
    r.add_post("/zs/api/users/{id:\\d+}/aktif", api_users_active)
    r.add_post("/zs/api/users/{id:\\d+}/sifre", api_users_password)
    r.add_get("/zs/api/tasks", api_tasks)
    r.add_post("/zs/api/tasks", api_task_create)
    r.add_get("/zs/api/tasks/{id:\\d+}", api_task_get)
    r.add_patch("/zs/api/tasks/{id:\\d+}", api_task_update)
    r.add_delete("/zs/api/tasks/{id:\\d+}", api_task_delete)
    r.add_post("/zs/api/tasks/{id:\\d+}/durum", api_task_transition)
    r.add_post("/zs/api/tasks/{id:\\d+}/devret", api_task_transfer)
    r.add_post("/zs/api/tasks/{id:\\d+}/gorunur", api_task_publish)
    r.add_post("/zs/api/tasks/{id:\\d+}/kisiler", api_member_add)
    r.add_post("/zs/api/tasks/{id:\\d+}/kisiler/cikar", api_member_remove)
    r.add_post("/zs/api/tasks/{id:\\d+}/ek-sure", api_extension_request)
    r.add_post("/zs/api/ek-sure/{id:\\d+}/karar", api_extension_decide)
    r.add_post("/zs/api/tasks/{id:\\d+}/notes", api_note_create)
    r.add_get("/zs/api/tasks/{id:\\d+}/hatirlatici", api_reminder_list)
    r.add_post("/zs/api/tasks/{id:\\d+}/hatirlatici", api_reminder_create)
    r.add_get("/zs/api/hatirlatici", api_free_reminder_list)
    r.add_get("/zs/api/hatirlaticilar", api_my_reminders)
    r.add_post("/zs/api/hatirlatici", api_free_reminder_create)
    r.add_delete("/zs/api/hatirlatici/{id:\\d+}", api_reminder_delete)
    r.add_post("/zs/api/tasks/{id:\\d+}/files", api_file_upload)
    r.add_get("/zs/api/rapor", api_report)
    r.add_get("/zs/api/rapor/etkinlik", api_activity)
    r.add_get("/zs/api/files/{id:\\d+}", api_file_download)
    r.add_delete("/zs/api/files/{id:\\d+}", api_file_delete)
