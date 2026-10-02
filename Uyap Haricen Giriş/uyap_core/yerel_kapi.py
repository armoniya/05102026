"""
Yerel HTTP kapısı için tarayıcı kaynaklı saldırı kontrolleri (DNS rebinding / çapraz-site).

Saf fonksiyonlar — ağ, UYAP ya da global durum kullanmaz; bu yüzden birim testiyle sınanabilir
(bkz. test_yerel_kapi.py). Kurallar `uyap_proxy.require_auth`'un tarayıcı korumasıyla AYNIDIR:

  • Host başlığı gerçek bir DNS adıysa → ret (DNS rebinding: saldırganın alan adı 127.0.0.1'e
    çözülse bile tarayıcı Host'ta O ADI gönderir; meşru istemciler hep IP/localhost kullanır).
  • Sec-Fetch-Site: cross-site ve üst-düzey belge gezinmesi DEĞİLSE → ret (başka sitenin
    <img>/<script>/fetch'i).
  • Origin "null" ya da yerel olmayan bir adsa → ret.

Python istemcileri (UyapMasa modülleri) Origin/Sec-Fetch göndermez ve Host'ta IP kullanır →
etkilenmez. UYAP_ALLOWED_HOSTS ortam değişkeniyle ek ad beyaz listeye alınabilir.
"""
import ipaddress
import os


def izinli_adlar():
    return {h.strip().lower() for h in os.environ.get("UYAP_ALLOWED_HOSTS", "").split(",") if h.strip()}


def hostname_of(value):
    """Host ya da Origin başlığından yalnız ana makine adı ('http://127.0.0.1:8800' → '127.0.0.1',
    '[::1]:8800' → '::1')."""
    v = (value or "").strip()
    if not v:
        return ""
    if "://" in v:
        v = v.split("://", 1)[1]
    v = v.split("/", 1)[0]
    if v.startswith("["):
        return v[1:].split("]", 1)[0]
    if v.count(":") == 1:
        v = v.split(":", 1)[0]
    return v


def ip_ya_da_yerel(hostname, ek_adlar=None):
    h = (hostname or "").strip().lower()
    if h in ("", "localhost"):
        return True
    if h in (izinli_adlar() if ek_adlar is None else ek_adlar):
        return True
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def tarayici_reddi(headers, method, ek_adlar=None):
    """İstek reddedilmeliyse sebep metni, değilse None. `headers`: büyük/küçük harf duyarsız
    .get() destekleyen eşleme (aiohttp CIMultiDict, starlette Headers) ya da küçük harfli dict."""
    def h(ad):
        return headers.get(ad) or headers.get(ad.lower()) or ""

    host_hn = hostname_of(h("Host"))
    if host_hn and not ip_ya_da_yerel(host_hn, ek_adlar):
        return "Beklenmeyen Host başlığı (olası DNS rebinding)."
    sfs = h("Sec-Fetch-Site").strip().lower()
    if sfs == "cross-site":
        mode = h("Sec-Fetch-Mode").strip().lower()
        dest = h("Sec-Fetch-Dest").strip().lower()
        if not (mode == "navigate" and dest in ("document", "") and (method or "").upper() in ("GET", "HEAD")):
            return "Çapraz-site alt-kaynak isteği reddedildi."
    origin = h("Origin")
    if origin and (origin.lower() == "null" or not ip_ya_da_yerel(hostname_of(origin), ek_adlar)):
        return "Yetkisiz Origin (çapraz-site istek reddedildi)."
    return None
