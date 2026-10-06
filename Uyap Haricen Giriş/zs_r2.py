"""
/zs İş Takip — belge deposu (zs_r2.py)
--------------------------------------
Belgeler müvekkil bilgisi içerebilir → sunucuda AES-256-GCM ile şifrelenir, depoya yalnız
şifreli bayt yazılır. Anahtar: ZS_FILE_KEY (base64, 32 bayt). KAYBOLURSA BELGELER AÇILAMAZ.

Depo:
  • Cloudflare R2 (S3 uyumlu) — R2_ACCOUNT_ID + R2_ACCESS_KEY_ID + R2_SECRET_ACCESS_KEY + R2_BUCKET
    ayarlıysa. AB yetki alanlı bucket için R2_ENDPOINT=https://<hesap>.eu.r2.cloudflarestorage.com.
    İmza (SigV4) stdlib hmac ile atılır; boto3 gerekmez.
  • Yerel klasör (zs_files/) — R2 ayarlı değilse ve Render'da DEĞİLSEK (yalnız geliştirme).
  • Render'da R2 yoksa belge yükleme KAPALI (efemeral diske müvekkil belgesi yazılmaz).

Şifreli biçim: b"ZS1" + 12 bayt nonce + şifreli metin(+16 bayt etiket); AAD = depolama anahtarı
(bir nesne başka bir kayıtla yer değiştirilirse çözülmez).
"""

import os
import base64
import hashlib
import hmac
import datetime
import urllib.parse

import aiohttp

import accounts

MAGIC = b"ZS1"


# ── Şifreleme ────────────────────────────────────────────────────────────────────────
def load_key():
    """ZS_FILE_KEY'i çözer. Yoksa/bozuksa None (yükleme kapalı)."""
    raw = (os.environ.get("ZS_FILE_KEY") or "").strip()
    if not raw:
        return None
    try:
        key = base64.b64decode(raw)
    except Exception:
        return None
    return key if len(key) == 32 else None


def encrypt(key: bytes, data: bytes, aad: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, data, aad.encode("utf-8"))


def decrypt(key: bytes, blob: bytes, aad: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 12 + 16:
        raise ValueError("Şifreli belge biçimi tanınmadı.")
    nonce = blob[len(MAGIC):len(MAGIC) + 12]
    return AESGCM(key).decrypt(nonce, blob[len(MAGIC) + 12:], aad.encode("utf-8"))


# ── AWS SigV4 (R2 için) ──────────────────────────────────────────────────────────────
def _h(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sigv4_headers(method, host, path, payload_hash, access_key, secret_key,
                  region="auto", service="s3", amz_date=None, extra_headers=None, query=""):
    """İmzalı istek başlıklarını döndürür (Authorization dahil). path zaten URI-kodlu olmalı."""
    if amz_date is None:
        amz_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    date = amz_date[:8]
    headers = {"host": host, "x-amz-date": amz_date}
    if payload_hash is not None and service == "s3":
        headers["x-amz-content-sha256"] = payload_hash
    for k, v in (extra_headers or {}).items():
        headers[k.lower()] = v
    signed = ";".join(sorted(headers))
    canon_headers = "".join(f"{k}:{' '.join(str(headers[k]).split())}\n" for k in sorted(headers))
    canon = "\n".join([method, path, query, canon_headers, signed,
                       payload_hash if payload_hash is not None else hashlib.sha256(b"").hexdigest()])
    scope = f"{date}/{region}/{service}/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canon.encode("utf-8")).hexdigest()])
    k = _h(_h(_h(_h(("AWS4" + secret_key).encode("utf-8"), date), region), service), "aws4_request")
    sig = hmac.new(k, sts.encode("utf-8"), hashlib.sha256).hexdigest()
    out = {k2: v for k2, v in headers.items() if k2 != "host"}
    out["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
                            f"SignedHeaders={signed}, Signature={sig}")
    return out


# ── Depolar ──────────────────────────────────────────────────────────────────────────
class StorageError(Exception):
    pass


class LocalStorage:
    name = "yerel klasör"

    def __init__(self, root):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, key):
        safe = "".join(c for c in key if c.isalnum() or c in "-_")
        return os.path.join(self.root, safe)

    async def put(self, key, data: bytes):
        with open(self._path(key), "wb") as f:
            f.write(data)

    async def get(self, key) -> bytes:
        try:
            with open(self._path(key), "rb") as f:
                return f.read()
        except FileNotFoundError:
            raise StorageError("Belge depoda bulunamadı.")

    async def delete(self, key):
        try:
            os.remove(self._path(key))
        except FileNotFoundError:
            pass


class R2Storage:
    name = "Cloudflare R2"

    def __init__(self, account_id, access_key, secret_key, bucket, endpoint=None):
        self.endpoint = (endpoint or f"https://{account_id}.r2.cloudflarestorage.com").rstrip("/")
        self.host = urllib.parse.urlparse(self.endpoint).netloc
        self.access_key, self.secret_key, self.bucket = access_key, secret_key, bucket
        self.prefix = "zs/"

    async def _req(self, method, key, data=b""):
        path = "/" + urllib.parse.quote(f"{self.bucket}/{self.prefix}{key}", safe="/-_.~")
        ph = hashlib.sha256(data).hexdigest()
        headers = sigv4_headers(method, self.host, path, ph, self.access_key, self.secret_key)
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.request(method, self.endpoint + path, data=data if method == "PUT" else None,
                                 headers=headers) as r:
                body = await r.read()
                if r.status == 404 and method == "GET":
                    raise StorageError("Belge depoda bulunamadı.")
                if r.status >= 300 and not (method == "DELETE" and r.status == 404):
                    raise StorageError(f"R2 {method} hatası: HTTP {r.status} {body[:200]!r}")
                return body

    async def put(self, key, data: bytes):
        await self._req("PUT", key, data)

    async def get(self, key) -> bytes:
        return await self._req("GET", key)

    async def delete(self, key):
        await self._req("DELETE", key)


def make_storage():
    """(storage | None, açıklama). None → yükleme kapalı."""
    acc = (os.environ.get("R2_ACCOUNT_ID") or "").strip()
    ak = (os.environ.get("R2_ACCESS_KEY_ID") or "").strip()
    sk = (os.environ.get("R2_SECRET_ACCESS_KEY") or "").strip()
    bucket = (os.environ.get("R2_BUCKET") or "").strip()
    endpoint = (os.environ.get("R2_ENDPOINT") or "").strip() or None
    if ak and sk and bucket and (acc or endpoint):
        st = R2Storage(acc, ak, sk, bucket, endpoint)
        return st, f"Cloudflare R2 ({st.host}/{bucket})"
    if os.environ.get("RENDER"):
        return None, "R2 ayarlı değil → belge yükleme KAPALI (Render diski efemeral)"
    root = os.path.join(accounts.DATA_DIR, "zs_files")
    return LocalStorage(root), f"yerel klasör ({root}) — yalnız geliştirme"
