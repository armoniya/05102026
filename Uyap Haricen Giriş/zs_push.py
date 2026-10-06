"""
/zs İş Takip — Web Push (zs_push.py)
-----------------------------------
Tarayıcı/telefon bildirimi: RFC 8291 (aes128gcm içerik şifreleme) + RFC 8292 (VAPID kimliği).
Ek paket yok: yalnız `cryptography` (belge şifrelemesi için zaten gerekli) ve aiohttp.

Anahtar: ZS_VAPID_PRIVATE = P-256 özel anahtarın 32 baytı, base64url. Canlıda Render ortam
değişkeni olarak SABİT verilmeli; değişirse tüm cihazların aboneliği geçersiz olur (kullanıcılar
bildirimleri yeniden açar). Yerelde ayarlı değilse veri klasöründe bir kez üretilip saklanır.
ZS_VAPID_SUB: iletişim adresi (mailto: ya da https:), bildirim servisleri sorun olursa buna yazar.
"""

import os
import json
import time
import base64
import struct

import aiohttp

import accounts


class PushGone(Exception):
    """Abonelik artık geçersiz (404/410) — kaydı silinmeli."""


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def unb64u(s: str) -> bytes:
    s = (s or "").strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _ec():
    from cryptography.hazmat.primitives.asymmetric import ec
    return ec


def _pub_bytes(key) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _hmac(key: bytes, data: bytes) -> bytes:
    import hmac
    import hashlib
    return hmac.new(key, data, hashlib.sha256).digest()


def private_from_raw(raw: bytes):
    ec = _ec()
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


def new_private_raw() -> bytes:
    ec = _ec()
    return ec.generate_private_key(ec.SECP256R1()).private_numbers().private_value.to_bytes(32, "big")


def load_vapid():
    """(özel anahtar nesnesi, açık anahtar base64url, kaynak açıklaması) ya da (None, None, neden)."""
    raw = (os.environ.get("ZS_VAPID_PRIVATE") or "").strip()
    src = "ortam değişkeni"
    if not raw:
        path = os.path.join(accounts.DATA_DIR, "zs_vapid.key")
        try:
            if os.path.isfile(path):
                with open(path, encoding="ascii") as f:
                    raw = f.read().strip()
            else:
                raw = b64u(new_private_raw())
                with open(path, "w", encoding="ascii") as f:
                    f.write(raw)
            src = f"yerel dosya ({path}) — canlıda ZS_VAPID_PRIVATE verin"
        except OSError as e:
            return None, None, f"anahtar dosyası yazılamadı: {e}"
    try:
        key = private_from_raw(unb64u(raw))
    except Exception:
        return None, None, "ZS_VAPID_PRIVATE geçersiz"
    return key, b64u(_pub_bytes(key)), src


def encrypt(payload: bytes, p256dh: str, auth: str, *, _as_key=None, _salt=None) -> bytes:
    """RFC 8291 aes128gcm gövdesi (tek kayıt)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    ua_pub = unb64u(p256dh)
    auth_secret = unb64u(auth)
    if len(ua_pub) != 65 or len(auth_secret) < 16:
        raise ValueError("abonelik anahtarları geçersiz")
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub)
    as_key = _as_key or ec.generate_private_key(ec.SECP256R1())
    as_pub = _pub_bytes(as_key)
    shared = as_key.exchange(ec.ECDH(), ua_key)
    prk_key = _hmac(auth_secret, shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_pub + as_pub + b"\x01")[:32]
    salt = _salt or os.urandom(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack(">I", 4096) + bytes([len(as_pub)]) + as_pub + ct


def decrypt(body: bytes, ua_key, auth: str) -> bytes:
    """Yalnız test için: alıcı tarafı çözme."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, idlen = body[:16], body[20]
    as_pub = body[21:21 + idlen]
    ct = body[21 + idlen:]
    ua_pub = _pub_bytes(ua_key)
    shared = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    ikm = _hmac(_hmac(unb64u(auth), shared), b"WebPush: info\x00" + ua_pub + as_pub + b"\x01")[:32]
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    pt = AESGCM(cek).decrypt(nonce, ct, None)
    return pt[:pt.rindex(b"\x02")]


def vapid_header(endpoint: str, key, pub_b64: str, sub: str) -> str:
    """Authorization: vapid t=<ES256 JWT>, k=<açık anahtar>."""
    from urllib.parse import urlsplit
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    u = urlsplit(endpoint)
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": sub},
                             separators=(",", ":")).encode())
    signing = f"{head}.{claims}".encode()
    r, s = decode_dss_signature(key.sign(signing, _ec().ECDSA(hashes.SHA256())))
    sig = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={head}.{claims}.{sig}, k={pub_b64}"


async def send(session: aiohttp.ClientSession, sub: dict, payload: dict, key, pub_b64: str, vapid_sub: str,
               ttl: int = 24 * 3600):
    """sub: {endpoint, p256dh, auth}. Başarıda None; geçersiz abonelikte PushGone; diğer hatada RuntimeError."""
    endpoint = sub["endpoint"]
    if not endpoint.startswith("https://"):
        raise PushGone("https olmayan uç")
    body = encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8"), sub["p256dh"], sub["auth"])
    headers = {"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(ttl),
               "Urgency": "high", "Authorization": vapid_header(endpoint, key, pub_b64, vapid_sub)}
    async with session.post(endpoint, data=body, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as r:
        if r.status in (200, 201, 202):
            return None
        txt = (await r.text())[:300]
        if r.status in (404, 410):
            raise PushGone(f"{r.status} {txt}")
        raise RuntimeError(f"push {r.status}: {txt}")
