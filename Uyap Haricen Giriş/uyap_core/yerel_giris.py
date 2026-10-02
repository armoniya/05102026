"""
Tarayıcıyı yerel UYAP proxy'sine (127.0.0.1:8800) GÜVENLİ biçimde açmak için bağlantı üretir.

Güvenlik taraması 2026-09-30 bulgu 4.1: proxy, yerel tarayıcıyı eskiden başlıklarına bakarak
(Origin/Sec-Fetch/Accept) tanıyordu — bu başlıkları her yerel program taklit edebildiği için yerel
yetki jetonu tek bir başlıkla atlatılabiliyordu. Artık tarayıcı yalnız proxy'nin verdiği güvenli
çerezle (HttpOnly, SameSite=Strict) kabul edilir. Çerezi almanın tek yolu bu modülün ürettiği
TEK KULLANIMLIK bağlantıdır:

  1. Program (jetonu okuyabilen aynı Windows kullanıcısı) proxy'ye jetonla POST atar →
     60 sn geçerli, tek kullanımlık bir kod alır.
  2. Tarayıcı  /__uyap_yerel__/giris?k=<kod>&hedef=/giris  adresine açılır → proxy kodu doğrular,
     çerezi verir ve /giris'e yönlendirir. Asıl jeton URL'ye/geçmişe hiç düşmez.

Kod alınamazsa (alıcı/üye modunda home_client'ta bu uç yok, proxy kapalı, jeton yok) düz
/giris adresi döner — üye tarafı eskisi gibi çalışır.
"""
import json
import os
import urllib.parse
import urllib.request

KOD_YOLU = "/__uyap_yerel__/kod"
GIRIS_YOLU = "/__uyap_yerel__/giris"


def _jeton_dosyasi():
    """uyap_proxy._local_token_file ile AYNI yol."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "UyapIcra", "gw_local_token")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "uyapicra", "gw_local_token")


def _jeton():
    tok = (os.environ.get("UYAP_LOCAL_TOKEN") or "").strip()
    if tok:
        return tok
    try:
        with open(_jeton_dosyasi(), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def tarayici_giris_url(port=8800, hedef="/giris", timeout=3.0):
    """Tarayıcıda açılacak adres: mümkünse tek kullanımlık güvenli giriş bağlantısı, değilse düz adres."""
    base = f"http://127.0.0.1:{int(port)}"
    duz = base + hedef
    tok = _jeton()
    if not tok:
        return duz
    try:
        req = urllib.request.Request(base + KOD_YOLU, data=b"{}", method="POST",
                                     headers={"X-Uyap-Local-Token": tok, "Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # sistem proxy'sini atla
        with opener.open(req, timeout=timeout) as r:
            kod = (json.loads(r.read().decode("utf-8")) or {}).get("kod")
    except Exception:
        return duz
    if not kod:
        return duz
    return base + GIRIS_YOLU + "?" + urllib.parse.urlencode({"k": kod, "hedef": hedef})
