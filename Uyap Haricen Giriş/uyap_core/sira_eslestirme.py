#!/usr/bin/env python3
"""
Sıra kapısı — tek tıkla eşleştirme (müşteri için komut satırı / BAT yok)
------------------------------------------------------------------------
Aynı Çoklu UYAP kurulumu iki bilgisayarda da çalışır:

  • OFİS bilgisayarı (paylaşımı yapan): "Sıra Kapısı" sekmesinde "Bot Bilgisayarı Ekle" →
    10 dakikalık eşleştirme penceresi açılır (TCP+UDP 8845).
  • BOT bilgisayarı (UYAP'a istek atan başka otomasyon): giriş ekranındaki "Bot bilgisayarı
    kurulumu" → ofis bilgisayarı ağda bulunur → iki ekranda AYNI 6 haneli kod görünür → iki
    tarafta da onaylanır → Windows'un tek yönetici onayıyla (UAC) kök sertifika + hosts satırı
    kurulur. Ofis tarafında bot IP'si izin listesine kendiliğinden eklenir; kapı canlı güncellenir.

Güvenlik:
  • Kod ağda GÖNDERİLMEZ; iki taraf (kök sertifika, iki IP, iki rastgele sayı) üzerinden
    kendisi hesaplar. Bot rastgele sayısını önce yalnız ÖZETİYLE taahhüt eder (commitment):
    aradaki saldırgan iki tarafta aynı kodu tutturamaz (Bluetooth sayısal karşılaştırma gibi).
  • Bot, aldığı kökü kurmadan önce sıkı doğrular: kendinden imzalı CA, path_len=0, KRİTİK
    Name Constraints ile YALNIZ avukat.uyap.gov.tr. Başka her şey reddedilir. Yönetici olarak
    çalışan yardımcı süreç aynı doğrulamayı kendi başına yeniden yapar (girdisi komut satırında,
    sonradan değiştirilemez).
  • Eşleştirme portu yalnız pencere açıkken dinler; tek seferde tek bağlantı, ileti ≤64 KB,
    zaman aşımları, 3 başarısız denemede pencere kapanır; yalnız LAN IPv4 adresleri.
  • Ofis güvenlik duvarı kuralı (tek UAC) programa bağlı ve yalnız yerel alt ağdan (localsubnet);
    asıl kısıt uygulamadaki bot IP izin listesidir.
"""

import os
import sys
import json
import time
import queue
import base64
import socket
import hashlib
import secrets
import threading
import datetime
import subprocess

from . import sira_kapisi as sk

ESLESTIRME_PORT = 8845
PENCERE_SN = 600          # ofis tarafında eşleştirme penceresi
KARAR_SN = 300            # kullanıcı kararı için bekleme
ILETI_EN_BUYUK = 64 * 1024
_ARA = b"CUSK-ARA-1"
_BURADA = b"CUSK-BURADA-1"
_ARA_BOY = 128            # yanıt istekten küçük kalsın (yansıtma/çoğaltma olmasın)
KURAL_ADI = "cokluuyap-sira-kapisi"
KOK_ADI = "cokluuyap Sira Kapisi"

# Yönetici yardımcısının çıkış kodları → kullanıcıya gösterilecek metin
CIKIS_METNI = {
    0: "Tamam.",
    10: "Ofis bilgisayarından gelen sertifika geçersiz; kurulum yapılmadı.",
    11: "Ofis bilgisayarının adresi geçersiz; kurulum yapılmadı.",
    12: "hosts dosyası yazılamadı (antivirüs engellemiş olabilir).",
    13: "Windows sertifika deposuna yazılamadı.",
    14: "Windows güvenlik duvarı kuralı eklenemedi.",
    1: "Beklenmeyen bir hata oluştu.",
}


class EslestirmeHatasi(Exception):
    pass


# ── Ortak yardımcılar ────────────────────────────────────────────────────────────────────

def _ip_kabul(ip) -> bool:
    try:
        sk._lan_ipv4(ip, "IP")
        return True
    except SystemExit:
        return False


def _ad_temizle(ad) -> str:
    ad = "".join(ch for ch in str(ad or "") if ch.isprintable())
    return ad.strip()[:64]


_KOPTU = "Karşı bilgisayarla bağlantı koptu ya da yanıt gelmedi."


def _gonder(sock, ileti: dict) -> None:
    try:
        sock.sendall(json.dumps(ileti, ensure_ascii=True).encode("ascii") + b"\n")
    except OSError:
        raise EslestirmeHatasi(_KOPTU)


def _al(akis) -> dict:
    try:
        satir = akis.readline(ILETI_EN_BUYUK + 1)
    except OSError:
        raise EslestirmeHatasi(_KOPTU)
    if not satir:
        raise EslestirmeHatasi("Bağlantı kapandı.")
    if len(satir) > ILETI_EN_BUYUK or not satir.endswith(b"\n"):
        raise EslestirmeHatasi("Geçersiz ileti.")
    try:
        ileti = json.loads(satir)
    except ValueError:
        raise EslestirmeHatasi("Geçersiz ileti.")
    if not isinstance(ileti, dict):
        raise EslestirmeHatasi("Geçersiz ileti.")
    if ileti.get("t") == "hata":
        raise EslestirmeHatasi(str(ileti.get("mesaj", "Ofis bilgisayarı eşleştirmeyi reddetti."))[:300])
    return ileti


def dogrulama_kodu(cer: bytes, kapi_ip: str, bot_ip: str, ns: bytes, nb: bytes) -> str:
    h = hashlib.sha256(b"CUSK-SAS-1\0" + cer + b"\0" + kapi_ip.encode() + b"\0"
                       + bot_ip.encode() + b"\0" + ns + nb).digest()
    n = int.from_bytes(h[:8], "big") % 1_000_000
    return f"{n // 1000:03d} {n % 1000:03d}"


def kok_dogrula(der: bytes):
    """Bota kurulacak kökü sıkı doğrular; uygun değilse EslestirmeHatasi."""
    from cryptography import x509
    try:
        c = x509.load_der_x509_certificate(der)
        if c.issuer != c.subject:
            raise ValueError("kendinden imzalı değil")
        c.verify_directly_issued_by(c)
        bc = c.extensions.get_extension_for_class(x509.BasicConstraints)
        if not (bc.critical and bc.value.ca and bc.value.path_length == 0):
            raise ValueError("BasicConstraints uygun değil")
        ku = c.extensions.get_extension_for_class(x509.KeyUsage)
        if not ku.value.key_cert_sign:
            raise ValueError("KeyUsage uygun değil")
        nc = c.extensions.get_extension_for_class(x509.NameConstraints)
        izinli = nc.value.permitted_subtrees or []
        if not (nc.critical and not nc.value.excluded_subtrees and len(izinli) == 1
                and isinstance(izinli[0], x509.DNSName) and izinli[0].value == sk.UYAP_HOST):
            raise ValueError("Name Constraints yalnız avukat.uyap.gov.tr olmalı")
        cn = c.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)
        if not cn or KOK_ADI not in cn[0].value:
            raise ValueError("adı uygun değil")
        simdi = datetime.datetime.now(datetime.timezone.utc)
        if not (c.not_valid_before_utc <= simdi <= c.not_valid_after_utc):
            raise ValueError("süresi geçerli değil")
    except EslestirmeHatasi:
        raise
    except Exception as e:
        raise EslestirmeHatasi(f"Sertifika reddedildi: {e}")
    return c


def _sistem_klasoru() -> str:
    if os.name != "nt":
        return "/usr/bin"
    import ctypes
    tampon = ctypes.create_unicode_buffer(260)
    ctypes.windll.kernel32.GetSystemDirectoryW(tampon, 260)
    return tampon.value


def hosts_yolu() -> str:
    return os.path.join(_sistem_klasoru(), "drivers", "etc", "hosts")


def _yerel_ipler() -> list:
    ipler = set()
    try:
        for ai in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ipler.add(ai[4][0])
    except OSError:
        pass
    ipler.add(sk._lan_ip_tahmin())
    return sorted(ip for ip in ipler if _ip_kabul(ip))


# ── OFİS tarafı: eşleştirme sunucusu ─────────────────────────────────────────────────────

class EslestirmeSunucusu:
    """Arayüz iş parçacığından kullanılır. Olaylar `olaylar` kuyruğuna düşer:
       ("istek", {"ip","ad","kod"}) → arayüz karar_ver(True/False) çağırır
       ("bitti", {"basarili": bool, "mesaj": str})"""

    def __init__(self):
        self.olaylar = queue.Queue()
        self._dur = threading.Event()
        self._karar_olay = threading.Event()
        self._karar = False
        self._tcp = self._udp = None
        self.bitis = 0.0

    def baslat(self) -> None:
        self.cer = sk.kapi_hazirla()
        self._tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._tcp.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
        self._tcp.bind(("0.0.0.0", ESLESTIRME_PORT))
        self._tcp.listen(1)
        self._tcp.settimeout(1.0)
        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp.bind(("0.0.0.0", ESLESTIRME_PORT))
        self._udp.settimeout(1.0)
        self.bitis = time.monotonic() + PENCERE_SN
        threading.Thread(target=self._tcp_dongu, daemon=True).start()
        threading.Thread(target=self._udp_dongu, daemon=True).start()

    def durdur(self) -> None:
        self._dur.set()
        self._karar = False
        self._karar_olay.set()
        for s in (self._tcp, self._udp):
            try:
                if s is not None:
                    s.close()
            except OSError:
                pass

    def karar_ver(self, onay: bool) -> None:
        self._karar = bool(onay)
        self._karar_olay.set()

    def kalan_sn(self) -> int:
        return max(0, int(self.bitis - time.monotonic()))

    def _udp_dongu(self):
        ad = _ad_temizle(socket.gethostname())
        while not self._dur.is_set() and time.monotonic() < self.bitis:
            try:
                veri, (ip, port) = self._udp.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            if veri.startswith(_ARA) and len(veri) >= _ARA_BOY and _ip_kabul(ip):
                try:
                    self._udp.sendto(_BURADA + b" " + json.dumps({"ad": ad}).encode("ascii"), (ip, port))
                except OSError:
                    pass

    def _tcp_dongu(self):
        basarisiz = 0
        sonuc = {"basarili": False, "mesaj": "Süre doldu; bot bilgisayarı bağlanmadı."}
        try:
            while not self._dur.is_set() and time.monotonic() < self.bitis:
                try:
                    baglanti, (bot_ip, _) = self._tcp.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                with baglanti:
                    try:
                        if self._bir_bot(baglanti, bot_ip):
                            sonuc = {"basarili": True, "mesaj": f"Bot bilgisayarı eklendi ({bot_ip})."}
                            return
                    except (EslestirmeHatasi, OSError, ValueError) as e:
                        self.olaylar.put(("uyari", {"mesaj": f"{bot_ip}: {e}"}))
                    basarisiz += 1
                    if basarisiz >= 3:
                        sonuc = {"basarili": False, "mesaj": "Üç başarısız deneme; eşleştirme kapatıldı."}
                        return
            if self._dur.is_set():
                sonuc = {"basarili": False, "mesaj": "Eşleştirme iptal edildi."}
        finally:
            self.durdur()
            self.olaylar.put(("bitti", sonuc))

    def _bir_bot(self, baglanti, bot_ip) -> bool:
        baglanti.settimeout(30)
        kapi_ip = baglanti.getsockname()[0]
        if not _ip_kabul(bot_ip) or not _ip_kabul(kapi_ip) or bot_ip == kapi_ip:
            raise EslestirmeHatasi("yerel ağ dışından bağlantı reddedildi")
        ayar = sk.ayar_oku()
        if ayar.get("kapi_ip") and sk.botlar(ayar) and ayar["kapi_ip"] != kapi_ip:
            _gonder(baglanti, {"t": "hata", "mesaj": (
                "Ofis bilgisayarının adresi değişmiş. Ofis bilgisayarında mevcut botları "
                "kaldırıp yeniden ekleyin.")})
            raise EslestirmeHatasi("kapı adresi farklı")
        akis = baglanti.makefile("rb")
        m = _al(akis)
        taahhut = str(m.get("taahhut", ""))
        if m.get("t") != "merhaba" or m.get("v") != 1 or len(taahhut) != 64:
            raise EslestirmeHatasi("geçersiz eşleştirme isteği")
        ad = _ad_temizle(m.get("ad"))
        ns = secrets.token_bytes(32)
        _gonder(baglanti, {"t": "kok", "cer": base64.b64encode(self.cer).decode("ascii"),
                           "ns": ns.hex(), "kapi_ip": kapi_ip})
        m = _al(akis)
        nb = bytes.fromhex(str(m.get("nb", "")))
        if m.get("t") != "acik" or len(nb) != 32 or hashlib.sha256(nb).hexdigest() != taahhut.lower():
            raise EslestirmeHatasi("doğrulama başarısız")
        kod = dogrulama_kodu(self.cer, kapi_ip, bot_ip, ns, nb)

        self._karar_olay.clear()
        self._karar = False
        self.olaylar.put(("istek", {"ip": bot_ip, "ad": ad, "kod": kod}))
        if not self._karar_olay.wait(KARAR_SN) or not self._karar or self._dur.is_set():
            try:
                _gonder(baglanti, {"t": "karar", "onay": False})
            except OSError:
                pass
            raise EslestirmeHatasi("onaylanmadı")
        # Bot tarafındaki kullanıcı da kodları karşılaştırıp onaylamış olmalı (iki taraflı).
        baglanti.settimeout(KARAR_SN)
        m = _al(akis)
        if m.get("t") != "bot_karar" or m.get("onay") is not True:
            raise EslestirmeHatasi("bot bilgisayarında onaylanmadı")
        sk.bot_ekle(bot_ip, ad, kapi_ip)
        _gonder(baglanti, {"t": "karar", "onay": True})
        return True


# ── BOT tarafı: bulma + eşleştirme istemcisi ─────────────────────────────────────────────

def ofisleri_bul(sure: float = 2.0) -> list:
    """Eşleştirme penceresi açık ofis bilgisayarlarını yerel ağda bulur: [(ip, ad)]."""
    bulunan = {}
    soketler = []
    for yerel in _yerel_ipler() or ["0.0.0.0"]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind((yerel, 0))
            s.settimeout(0.2)
            s.sendto(_ARA.ljust(_ARA_BOY, b"\0"), ("255.255.255.255", ESLESTIRME_PORT))
            soketler.append(s)
        except OSError:
            continue
    son = time.monotonic() + sure
    try:
        while time.monotonic() < son and soketler:
            for s in soketler:
                try:
                    veri, (ip, _) = s.recvfrom(2048)
                except (socket.timeout, OSError):
                    continue
                if veri.startswith(_BURADA + b" ") and _ip_kabul(ip):
                    try:
                        ad = _ad_temizle(json.loads(veri[len(_BURADA) + 1:]).get("ad"))
                    except ValueError:
                        ad = ""
                    bulunan[ip] = ad
    finally:
        for s in soketler:
            s.close()
    return sorted(bulunan.items())


class EslestirmeIstemcisi:
    def __init__(self, ofis_ip: str, _kaynak=None):
        if not _ip_kabul(ofis_ip):
            raise EslestirmeHatasi("Ofis bilgisayarının adresi yerel ağ (LAN) IPv4 adresi olmalı.")
        self.kapi_ip = ofis_ip
        self.cer = None
        self._sock = None
        self._kaynak = _kaynak   # yalnız testler (kaynak adres seçimi)

    def baglan(self) -> str:
        """Bağlanır, kökü alıp doğrular; ekranda gösterilecek doğrulama kodunu döndürür."""
        try:
            self._sock = socket.create_connection((self.kapi_ip, ESLESTIRME_PORT), timeout=10,
                                                  source_address=self._kaynak)
        except OSError:
            raise EslestirmeHatasi(
                "Ofis bilgisayarına bağlanılamadı. Orada 'Bot Bilgisayarı Ekle' açık mı?")
        self._sock.settimeout(30)
        akis = self._akis = self._sock.makefile("rb")
        bot_ip = self._sock.getsockname()[0]
        nb = secrets.token_bytes(32)
        _gonder(self._sock, {"t": "merhaba", "v": 1, "ad": _ad_temizle(socket.gethostname()),
                             "taahhut": hashlib.sha256(nb).hexdigest()})
        m = _al(akis)
        if m.get("t") != "kok" or m.get("kapi_ip") != self.kapi_ip:
            raise EslestirmeHatasi("Ofis bilgisayarından beklenmeyen yanıt.")
        try:
            cer = base64.b64decode(str(m.get("cer", "")), validate=True)
            ns = bytes.fromhex(str(m.get("ns", "")))
        except ValueError:
            raise EslestirmeHatasi("Ofis bilgisayarından beklenmeyen yanıt.")
        if len(ns) != 32:
            raise EslestirmeHatasi("Ofis bilgisayarından beklenmeyen yanıt.")
        kok_dogrula(cer)
        self.cer = cer
        _gonder(self._sock, {"t": "acik", "nb": nb.hex()})
        return dogrulama_kodu(cer, self.kapi_ip, bot_ip, ns, nb)

    def karar_bildir_ve_bekle(self, onay: bool) -> bool:
        """Bot kullanıcısının kararını gönderir; onayladıysa ofisin kararını bekler."""
        _gonder(self._sock, {"t": "bot_karar", "onay": bool(onay)})
        if not onay:
            return False
        self._sock.settimeout(KARAR_SN + 30)
        m = _al(self._akis)
        return m.get("t") == "karar" and m.get("onay") is True

    def kapat(self):
        try:
            if self._sock is not None:
                self._sock.close()
        except OSError:
            pass


# ── Yönetici işlemleri (UAC ile yükseltilmiş yardımcı süreçte çalışır) ───────────────────

def _crypt32():
    import ctypes
    from ctypes import wintypes
    c = ctypes.WinDLL("crypt32", use_last_error=True)
    c.CertOpenStore.restype = ctypes.c_void_p
    c.CertOpenStore.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                ctypes.c_void_p]
    c.CertEnumCertificatesInStore.restype = ctypes.c_void_p
    c.CertEnumCertificatesInStore.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    c.CertDuplicateCertificateContext.restype = ctypes.c_void_p
    c.CertDuplicateCertificateContext.argtypes = [ctypes.c_void_p]
    c.CertDeleteCertificateFromStore.argtypes = [ctypes.c_void_p]
    c.CertGetNameStringW.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                     ctypes.c_wchar_p, wintypes.DWORD]
    c.CertAddEncodedCertificateToStore.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_char_p,
                                                   wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    c.CertCloseStore.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    c.CertFreeCertificateContext.argtypes = [ctypes.c_void_p]
    return c


_YEREL_MAKINE = 0x20000      # CERT_SYSTEM_STORE_LOCAL_MACHINE
_GECERLI_KULLANICI = 0x10000  # CERT_SYSTEM_STORE_CURRENT_USER (yalnız testler)


def _depo_ac(konum: int, ad: str, salt_okunur: bool = False):
    import ctypes
    c = _crypt32()
    bayrak = konum | (0x8000 if salt_okunur else 0)  # CERT_STORE_READONLY_FLAG
    depo = c.CertOpenStore(ctypes.c_void_p(10), 0, None, bayrak, ctypes.c_wchar_p(ad))  # PROV_SYSTEM_W
    if not depo:
        raise OSError(f"sertifika deposu açılamadı (hata {ctypes.get_last_error()})")
    return c, depo


def _kokleri_listele_sil(c, depo, sil: bool) -> int:
    import ctypes
    sayi = 0
    ctx = c.CertEnumCertificatesInStore(depo, None)
    while ctx:
        tampon = ctypes.create_unicode_buffer(512)
        c.CertGetNameStringW(ctx, 4, 0, None, tampon, 512)  # CERT_NAME_SIMPLE_DISPLAY_TYPE
        if KOK_ADI in tampon.value:
            sayi += 1
            if sil:
                c.CertDeleteCertificateFromStore(c.CertDuplicateCertificateContext(ctx))
        ctx = c.CertEnumCertificatesInStore(depo, ctx)
    return sayi


def kok_kur(der: bytes, konum: int = _YEREL_MAKINE, ad: str = "ROOT") -> None:
    """Eski cokluuyap köklerini siler, yenisini ekler."""
    import ctypes
    c, depo = _depo_ac(konum, ad)
    try:
        _kokleri_listele_sil(c, depo, sil=True)
        if not c.CertAddEncodedCertificateToStore(depo, 0x10001, der, len(der), 3, None):
            raise OSError(f"sertifika eklenemedi (hata {ctypes.get_last_error()})")
    finally:
        c.CertCloseStore(depo, 0)


def kok_kaldir(konum: int = _YEREL_MAKINE, ad: str = "ROOT") -> int:
    c, depo = _depo_ac(konum, ad)
    try:
        return _kokleri_listele_sil(c, depo, sil=True)
    finally:
        c.CertCloseStore(depo, 0)


def kok_kurulu_mu(konum: int = _YEREL_MAKINE, ad: str = "ROOT") -> bool:
    try:
        c, depo = _depo_ac(konum, ad, salt_okunur=True)
    except OSError:
        return False
    try:
        return _kokleri_listele_sil(c, depo, sil=False) > 0
    finally:
        c.CertCloseStore(depo, 0)


def hosts_guncelle(kapi_ip, yol: str = None) -> None:
    """Bizim satırımızı (etiketli) kaldırır; kapi_ip verilirse yenisini ekler. Diğer baytlara
    dokunulmaz (kod sayfası sorunu yok)."""
    yol = yol or hosts_yolu()
    with open(yol, "rb") as f:
        satirlar = f.read().splitlines(keepends=True)
    etiket = sk.HOSTS_ETIKET.encode("ascii")
    kalan = [s for s in satirlar if etiket not in s]
    if kalan and not kalan[-1].endswith(b"\n"):
        kalan[-1] += b"\r\n"
    if kapi_ip:
        kalan.append(f"{kapi_ip} {sk.UYAP_HOST} {sk.HOSTS_ETIKET}\r\n".encode("ascii"))
    with open(yol, "r+b") as f:
        f.write(b"".join(kalan))
        f.truncate()


def hosts_kapi_ip(yol: str = None):
    """hosts'taki bizim satırımızın IP'si (yoksa None)."""
    try:
        with open(yol or hosts_yolu(), "rb") as f:
            for s in f.read().splitlines():
                if sk.HOSTS_ETIKET.encode("ascii") in s:
                    return s.split()[0].decode("ascii", "replace")
    except OSError:
        pass
    return None


def _dns_temizle() -> None:
    try:
        subprocess.run([os.path.join(_sistem_klasoru(), "ipconfig.exe"), "/flushdns"],
                       capture_output=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass


def bot_kur(kapi_ip: str, der: bytes) -> int:
    """YÖNETİCİ: bot bilgisayarına kök + hosts. Girdileri baştan doğrular. Çıkış kodu döndürür."""
    if not _ip_kabul(kapi_ip):
        return 11
    try:
        kok_dogrula(der)
    except EslestirmeHatasi:
        return 10
    try:
        kok_kur(der)              # önce sertifika: hosts'u değiştirip sertifikasız bırakmayalım
    except OSError:
        return 13
    try:
        hosts_guncelle(kapi_ip)
    except OSError:
        return 12
    _dns_temizle()
    return 0


def bot_kaldir() -> int:
    try:
        hosts_guncelle(None)      # önce hosts: bot hemen doğrudan UYAP'a dönsün
    except OSError:
        return 12
    try:
        kok_kaldir()
    except OSError:
        return 13
    _dns_temizle()
    return 0


def _netsh(*arglar):
    return subprocess.run([os.path.join(_sistem_klasoru(), "netsh.exe"), "advfirewall", "firewall", *arglar],
                          capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def guvenlik_duvari_var() -> bool:
    try:
        return _netsh("show", "rule", f"name={KURAL_ADI}").returncode == 0
    except Exception:
        return False


def guvenlik_duvari_kur() -> int:
    """YÖNETİCİ: 443 (kapı) + 8845 (eşleştirme) yalnız bu program ve yerel alt ağ için."""
    program = sys.executable
    try:
        _netsh("delete", "rule", f"name={KURAL_ADI}")
        ortak = ["dir=in", "action=allow", "remoteip=localsubnet", f"program={program}",
                 "profile=any", "enable=yes"]
        r1 = _netsh("add", "rule", f"name={KURAL_ADI}", "protocol=TCP",
                    f"localport=443,{ESLESTIRME_PORT}", *ortak)
        r2 = _netsh("add", "rule", f"name={KURAL_ADI}", "protocol=UDP",
                    f"localport={ESLESTIRME_PORT}", *ortak)
        return 0 if r1.returncode == 0 and r2.returncode == 0 else 14
    except Exception:
        return 14


def yonetici_main(argv) -> int:
    """`CokluUyap.exe --sira-yonetici <komut> …` (yalnız UAC ile yükseltilmiş süreçte)."""
    try:
        komut = argv[0] if argv else ""
        if komut == "bot-kur" and len(argv) == 3:
            try:
                der = base64.b64decode(argv[2], validate=True)
            except ValueError:
                return 10
            return bot_kur(argv[1], der)
        if komut == "bot-kaldir":
            return bot_kaldir()
        if komut == "guvenlik-duvari":
            return guvenlik_duvari_kur()
        return 1
    except Exception:
        return 1


# ── UAC ile kendini yükseltilmiş çalıştırma ──────────────────────────────────────────────

class YetkiReddedildi(Exception):
    pass


def yonetici_olarak_calistir(arglar: list) -> int:
    """Aynı programı yönetici olarak `--sira-yonetici …` ile çalıştırır, bitmesini bekler ve
    çıkış kodunu döndürür. Kullanıcı UAC'ı reddederse YetkiReddedildi."""
    import ctypes
    from ctypes import wintypes

    if getattr(sys, "frozen", False):
        dosya, parametre = sys.executable, ["--sira-yonetici", *arglar]
    else:
        ana = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uyap_app.py")
        dosya, parametre = sys.executable, [ana, "--sira-yonetici", *arglar]

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
                    ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                    ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR),
                    ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
                    ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    sei = SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = 0x40 | 0x100          # NOCLOSEPROCESS | NOASYNC
    sei.lpVerb = "runas"
    sei.lpFile = dosya
    sei.lpParameters = subprocess.list2cmdline(parametre)
    sei.nShow = 0                      # SW_HIDE
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    if not shell32.ShellExecuteExW(ctypes.byref(sei)):
        hata = ctypes.get_last_error()
        if hata == 1223:               # ERROR_CANCELLED
            raise YetkiReddedildi()
        raise OSError(f"yönetici olarak başlatılamadı (hata {hata})")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    try:
        k32.WaitForSingleObject(sei.hProcess, 0xFFFFFFFF)
        kod = wintypes.DWORD()
        k32.GetExitCodeProcess(sei.hProcess, ctypes.byref(kod))
        return int(kod.value)
    finally:
        k32.CloseHandle(sei.hProcess)


# ── Bot tarafı: bağlantıyı sına ──────────────────────────────────────────────────────────

def kapi_sina(kapi_ip: str, der: bytes = None, zaman: float = 5.0) -> str:
    """Bot bilgisayarından kapıya TLS el sıkışması. 'tamam' | 'kapali' | 'sertifika' döndürür."""
    import ssl
    ctx = ssl.create_default_context()
    if der is not None:
        ctx = ssl.create_default_context(cadata=der)
    try:
        with socket.create_connection((kapi_ip, 443), timeout=zaman) as s:
            with ctx.wrap_socket(s, server_hostname=sk.UYAP_HOST):
                return "tamam"
    except ssl.SSLError:
        return "sertifika"
    except OSError:
        return "kapali"
