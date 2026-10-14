#!/usr/bin/env python3
"""
Sıra kapısı — ofisteki BAŞKA bir otomasyon programının UYAP isteklerini sıraya alır
-------------------------------------------------------------------------------------
Sorun: ofiste ayrı bir PC'de çalışan (bizim yazmadığımız) bir otomasyon programı
avukat.uyap.gov.tr'ye doğrudan HTTP istekleri atıyor. Bu istekler cokluuyap'ın kendi
istekleriyle (cokluuyap.com üzerinden UYAP arayüzünde yapılan tıklamalar, toplu işler)
AYNI ANDA UYAP'a ulaşınca UYAP eşzamanlılık reddi/uyarısı veriyor (bkz. _runtime docstring).

Çözüm (kullanıcı kararı 2026-10-12, "hosts" yolu):
  • Bot PC'sinin hosts dosyasında  avukat.uyap.gov.tr → <cokluuyap PC'sinin LAN IP'si>.
  • Bot PC'sine, YALNIZ avukat.uyap.gov.tr için sertifika üretebilen (Name Constraints)
    kök sertifikamız kurulur. CA'nın özel anahtarı sunucu sertifikası imzalandıktan sonra
    SİLİNİR — başka bir alan için sertifika üretilemez.
  • Bu modül cokluuyap PC'sinde :443'te TLS ile dinler, botun isteğini OLDUĞU GİBİ
    (botun kendi çerezleri, başlıkları — bizim UYAP oturumumuza DOKUNMADAN) gerçek
    avukat.uyap.gov.tr'ye iletir. Gövde yeniden yazılmaz, yönlendirme izlenmez.
  • Sıralama: bot istekleri (a) kendi aralarında tek tek, (b) aralarında en az `aralik_sn`
    bekleyerek, (c) `_runtime.batch_write()` DÜŞÜK öncelikli geçidinden — yani bizim
    bireysel isteklerimiz varken onlara yol vererek — ve (d) bizim uçuştaki GET'lerimiz
    bitince gönderilir. Bot isteği UYAP'tayken bizim POST'larımız kilitte bekler.
    KALAN RİSK: bizim GET'lerimiz kilide girmez; bot isteği UYAP'tayken bizim bir GET
    başlarsa yine çakışabilir (POST'larda değil).

Mod: "sirala" (varsayılan) ya da "izle" (kilit yok, yalnız günlük — ölçüm için; her isteğin
bizim istekle çakışıp çakışmadığını kaydeder).

Kurulum (cokluuyap PC'sinde, Uyap Haricen Giriş klasöründen):
    python -m uyap_core.sira_kapisi kur --bot-ip 192.168.1.50 [--kapi-ip 192.168.1.10]
  (light/MSI kurulumunda: CokluUyap.exe --sira-kapisi kur --bot-ip … — uyap_app.py)
Güvenlik (13.10.2026): varsayılan RET (izin listesi boşsa kapalı), yalnız kapi_ip'de dinler,
istek hedefi yalnız "/…" (SSRF yok), proxy başlıklarına güvenilmez, gövde/bağlantı sınırlı.
  → %LOCALAPPDATA%\\UyapIcra\\sira_kapisi\\bot_pc_icin\\ klasörünü bot PC'sine kopyalayın,
    orada KUR.bat'ı YÖNETİCİ olarak çalıştırın (geri almak: KALDIR.bat).
  → Paylaşım (run_office) bir sonraki başlatılışında kapı otomatik açılır.
    python -m uyap_core.sira_kapisi durum   |   kapat   |   ac
Kapı yalnız paylaşım açıkken çalışır; cokluuyap kapalıyken bot UYAP'a ULAŞAMAZ
(bot PC'sinde KALDIR.bat ile eski hâle dönülür).
"""

import os
import sys
import json
import time
import socket
import asyncio
import ipaddress
import argparse
import datetime
import logging
import logging.handlers

import httpx

from . import _runtime

UYAP_HOST = "avukat.uyap.gov.tr"
HOSTS_ETIKET = "# cokluuyap-sira-kapisi"

VARSAYILAN = {
    "etkin": False,
    "mod": "sirala",          # "sirala" | "izle"
    "port": 443,
    "aralik_sn": 1.0,         # iki bot isteği arasındaki en kısa süre
    "izinli_ipler": [],       # boşsa kapı HİÇBİR isteği kabul etmez (kapalı kalır)
}

# Güvenlik sınırları (13.10.2026 sıkılaştırma): izinli bot bile kapıyı bellek/bağlantı
# taşmasıyla düşüremesin. UYAP evrak yüklemeleri bu sınırın çok altında.
EN_BUYUK_GOVDE = 100 * 1024 * 1024
EN_BUYUK_YANIT = 100 * 1024 * 1024
EN_COK_BAGLANTI = 16
# Bot isteği bizim kilidimizi en çok bu kadar tutar (UYAP yanıtı bu sürede tamamlanmazsa 504).
# Yanıt kilit içinde belleğe alınır, bota kilit BIRAKILDIKTAN sonra gönderilir: yavaş okuyan
# (ya da hiç okumayan) bir istemci bizim UYAP işlemlerimizi bekletemez.
KILIT_SURE_SINIRI = 180.0

# Bağlantıya özgü (hop-by-hop) başlıklar iki yönde de iletilmez.
_HOP = {"connection", "keep-alive", "proxy-connection", "proxy-authenticate",
        "proxy-authorization", "te", "trailer", "trailers", "transfer-encoding",
        "upgrade", "expect"}
# İstekte ayrıca atılanlar: uzunluğu httpx gövdeden yeniden hesaplar (iki uzunluk başlığı
# uyuşmazlığıyla istek kaçakçılığı olmasın); X-Forwarded-*/Forwarded bot PC'sinin LAN
# adresini UYAP'a sızdırmasın.
_ISTEK_AT = _HOP | {"content-length", "forwarded", "x-forwarded-for", "x-forwarded-host",
                    "x-forwarded-proto", "x-real-ip"}


def _lan_ipv4(ip, ad: str) -> str:
    """Kurulumda verilen adres yalnız özel (LAN) bir IPv4 olabilir; aksi hâlde kapı internete
    açılmış ya da KUR.bat'a beklenmedik metin yazılmış olurdu."""
    try:
        a = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        raise SystemExit(f"{ad} geçerli bir IP adresi değil: {ip!r}")
    if (a.version != 4 or not a.is_private or a.is_loopback or a.is_link_local
            or a.is_unspecified or a.is_multicast):
        raise SystemExit(f"{ad} yerel ağ (LAN) IPv4 adresi olmalı (ör. 192.168.1.50): {ip}")
    return str(a)


def veri_klasoru() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "UyapIcra", "sira_kapisi")


def _yol(ad: str) -> str:
    return os.path.join(veri_klasoru(), ad)


def ayar_oku() -> dict:
    ayar = dict(VARSAYILAN)
    try:
        with open(_yol("ayar.json"), encoding="utf-8") as f:
            ayar.update(json.load(f))
    except (OSError, ValueError):
        pass
    return ayar


def ayar_yaz(ayar: dict) -> None:
    # Atomik: arayüz yazarken paylaşım döngüsü yarım dosya okumasın.
    os.makedirs(veri_klasoru(), exist_ok=True)
    gecici = _yol("ayar.json.yeni")
    with open(gecici, "w", encoding="utf-8") as f:
        json.dump(ayar, f, ensure_ascii=False, indent=2)
    os.replace(gecici, _yol("ayar.json"))


# ── Bot listesi (arayüzden eşleştirme ile yönetilir, bkz. sira_eslestirme.py) ─────────────

def botlar(ayar: dict = None) -> list:
    ayar = ayar or ayar_oku()
    liste = list(ayar.get("botlar") or [])
    # CLI ('kur --bot-ip') ile eklenmiş, listede adı olmayan IP'ler de görünsün.
    bilinen = {b.get("ip") for b in liste}
    for ip in ayar.get("izinli_ipler") or []:
        if ip not in bilinen:
            liste.append({"ip": ip, "ad": "", "tarih": ""})
    return liste


def bot_ekle(ip: str, ad: str, kapi_ip: str) -> None:
    ip = _lan_ipv4(ip, "Bot IP'si")
    kapi_ip = _lan_ipv4(kapi_ip, "Kapı IP'si")
    ayar = ayar_oku()
    liste = [b for b in botlar(ayar) if b.get("ip") != ip]
    liste.append({"ip": ip, "ad": ad[:64], "tarih": datetime.date.today().strftime("%d.%m.%Y")})
    ayar.pop("dinle", None)
    ayar.update(etkin=True, kapi_ip=kapi_ip, botlar=liste, izinli_ipler=[b["ip"] for b in liste])
    ayar.setdefault("mod", "sirala")
    ayar.setdefault("aralik_sn", 1.0)
    ayar_yaz(ayar)
    yenile()


def bot_sil(ip: str) -> None:
    ayar = ayar_oku()
    liste = [b for b in botlar(ayar) if b.get("ip") != ip]
    ayar.update(botlar=liste, izinli_ipler=[b["ip"] for b in liste])
    ayar_yaz(ayar)
    yenile()


def etkin_ayarla(etkin: bool) -> None:
    ayar = ayar_oku()
    ayar["etkin"] = bool(etkin)
    ayar_yaz(ayar)
    yenile()


def kok_der() -> bytes:
    for yol in (_yol("kok.cer"), os.path.join(_yol("bot_pc_icin"), "cokluuyap_sira_kapisi_kok.cer")):
        try:
            with open(yol, "rb") as f:
                return f.read()
        except OSError:
            continue
    raise FileNotFoundError("kök sertifika yok")


def sertifika_bitis():
    """Sunucu sertifikasının bitiş tarihi (yoksa None)."""
    try:
        from cryptography import x509
        with open(_yol("sunucu.crt"), "rb") as f:
            return x509.load_pem_x509_certificate(f.read()).not_valid_after_utc
    except Exception:
        return None


def kapi_hazirla() -> bytes:
    """Eşleştirmeden önce: sertifika yoksa (ya da 14 günden az kaldıysa) üretir; kök DER'i döndürür.
    Yenilemede eski botların yeniden eşleştirilmesi gerekir (kök değişir)."""
    bitis = sertifika_bitis()
    kalan = (bitis - datetime.datetime.now(datetime.timezone.utc)) if bitis else None
    if (bitis is None or kalan < datetime.timedelta(days=14)
            or not os.path.exists(_yol("sunucu.parola")) and not os.path.exists(_yol("sunucu.key"))):
        sertifika_uret(veri_klasoru())
        yenile()
    return kok_der()


# ── Sunucu anahtarının korunması ─────────────────────────────────────────────────────────
# sunucu.key PAROLAYLA şifreli (PKCS#8) yazılır; parola sunucu.parola'da Windows DPAPI ile
# YALNIZ bu Windows kullanıcısına ve bu bilgisayara bağlı saklanır. Dosyalar kopyalansa
# (yedek, başka hesap, başka PC) anahtar açılamaz → bot PC'sine karşı MITM için kullanılamaz.
_DPAPI_EK = b"cokluuyap-sira-kapisi"


def _dpapi(veri: bytes, sifrele: bool) -> bytes:
    if os.name != "nt":
        return veri  # yalnız test/geliştirme; kapı yalnız Windows'ta çalışır
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(b):
        tampon = ctypes.create_string_buffer(b, len(b))
        return _Blob(len(b), ctypes.cast(tampon, ctypes.POINTER(ctypes.c_char))), tampon

    giris, _t1 = _blob(veri)
    ek, _t2 = _blob(_DPAPI_EK)
    cikis = _Blob()
    crypt32 = ctypes.windll.crypt32
    islev = crypt32.CryptProtectData if sifrele else crypt32.CryptUnprotectData
    if not islev(ctypes.byref(giris), None, ctypes.byref(ek), None, None, 0x1,  # UI_FORBIDDEN
                 ctypes.byref(cikis)):
        raise OSError(f"DPAPI {'şifreleme' if sifrele else 'çözme'} başarısız "
                      f"(hata {ctypes.GetLastError()})")
    try:
        return ctypes.string_at(cikis.pbData, cikis.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(cikis.pbData, ctypes.c_void_p))


def _acl_sinirla(yol: str) -> None:
    try:
        from .uyap_proxy import _windows_acl_sinirla
        _windows_acl_sinirla(yol)
    except Exception:
        pass


def _anahtar_yaz(key) -> None:
    import secrets
    from cryptography.hazmat.primitives import serialization
    parola = secrets.token_urlsafe(32)
    with open(_yol("sunucu.parola"), "wb") as f:
        f.write(_dpapi(parola.encode("ascii"), True))
    with open(_yol("sunucu.key"), "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.BestAvailableEncryption(parola.encode("ascii"))))
    _acl_sinirla(_yol("sunucu.parola"))
    _acl_sinirla(_yol("sunucu.key"))


def anahtar_parolasi() -> str:
    with open(_yol("sunucu.parola"), "rb") as f:
        return _dpapi(f.read(), False).decode("ascii")


def _eski_anahtari_sifrele() -> None:
    """13.10.2026 öncesi kurulumlar anahtarı şifresiz yazıyordu: yerinde şifrele (bot PC'sinde
    yeniden kurulum gerekmez; sertifika aynı kalır)."""
    if os.path.exists(_yol("sunucu.parola")):
        return
    from cryptography.hazmat.primitives import serialization
    with open(_yol("sunucu.key"), "rb") as f:
        key = serialization.load_pem_private_key(f.read(), password=None)
    _anahtar_yaz(key)
    print("[i] Sıra kapısı: sunucu anahtarı şifrelendi (DPAPI).")


# ── Sertifikalar ─────────────────────────────────────────────────────────────────────────

def sertifika_uret(klasor: str) -> tuple:
    """Name-Constraints'li tek amaçlı CA + avukat.uyap.gov.tr sunucu sertifikası üretir.
    CA'nın özel anahtarı DİSKE YAZILMAZ.
    (ca_cer_yolu, sunucu_crt, sunucu_key, kok_sha1_parmak_izi) döndürür."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    simdi = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    # Kök de sunucu sertifikasıyla aynı gün geçersizleşir: bot PC'sindeki güven süresiz kalmaz.
    bitis = simdi + datetime.timedelta(days=825)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_ad = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "cokluuyap Sira Kapisi (yalniz avukat.uyap.gov.tr)"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "cokluuyap yerel"),
    ])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_ad).issuer_name(ca_ad)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(simdi)
        .not_valid_after(bitis)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=False, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
            encipher_only=False, decipher_only=False), critical=True)
        # Bu kök, bot PC'sinde YALNIZ avukat.uyap.gov.tr için geçerli sertifika doğrulayabilir.
        .add_extension(x509.NameConstraints(
            permitted_subtrees=[x509.DNSName(UYAP_HOST)], excluded_subtrees=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, UYAP_HOST)]))
        .issuer_name(ca_ad)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(simdi)
        .not_valid_after(bitis)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(UYAP_HOST)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    del ca_key  # bilerek: CA anahtarı hiçbir yerde saklanmaz

    os.makedirs(klasor, exist_ok=True)
    os.makedirs(veri_klasoru(), exist_ok=True)
    ca_cer = os.path.join(klasor, "cokluuyap_sira_kapisi_kok.cer")
    with open(ca_cer, "wb") as f:
        f.write(ca_cert.public_bytes(serialization.Encoding.DER))
    with open(_yol("kok.cer"), "wb") as f:   # eşleştirmede bota verilen kök (yalnız açık anahtar)
        f.write(ca_cert.public_bytes(serialization.Encoding.DER))
    crt = _yol("sunucu.crt")
    anahtar = _yol("sunucu.key")
    with open(crt, "wb") as f:
        # Zincir: sunucu + CA (istemci ara sertifika aramasın)
        f.write(cert.public_bytes(serialization.Encoding.PEM))
        f.write(ca_cert.public_bytes(serialization.Encoding.PEM))
    _anahtar_yaz(key)
    return ca_cer, crt, anahtar, ca_cert.fingerprint(hashes.SHA1()).hex().upper()


# Bot PC betikleri (yalnız ASCII: cmd/PowerShell kod sayfası sürprizi olmasın).
# Güvenlik (13.10.2026): sistem araçları TAM YOLLA çağrılır (klasöre bırakılmış sahte
# certutil.exe/powershell.exe çalışmaz); sertifika belleğe okunup KUR.ps1'e gömülü SHA256 ile
# doğrulanır ve aynı baytlar kurulur (dosyayı sonradan değiştirme penceresi yok); parmak izi
# kullanıcıya gösterilip cokluuyap PC'sindeki ile karşılaştırması istenir; eski cokluuyap
# kökleri silinir. Klasörün tamamını ele geçiren biri KUR.bat'ı da değiştirebilir — bu yüzden
# klasör USB/özel bir yerle taşınmalı (kılavuz §3).
_PS = r'"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"'
_YONETICI = (r'"%SystemRoot%\System32\net.exe" session >nul 2>&1 || '
             r'(echo Bu dosyayi sag tik ^> Yonetici olarak calistir ile acin. & pause & exit /b 1)')
_KOK_ADI = "cokluuyap Sira Kapisi"


def _kur_bat() -> str:
    return f"""@echo off
REM cokluuyap Sira Kapisi - bot bilgisayari kurulumu (YONETICI olarak calistirin)
{_YONETICI}
{_PS} -NoProfile -ExecutionPolicy Bypass -File "%~dp0KUR.ps1"
pause
"""


def _kur_ps1(kapi_ip: str, kok_sha256: str) -> str:
    return r"""# cokluuyap Sira Kapisi - bot bilgisayari kurulumu (KUR.bat cagirir, YONETICI gerekir)
$ErrorActionPreference = 'Stop'
$beklenen = '@SHA@'
try {
    $b = [IO.File]::ReadAllBytes((Join-Path $PSScriptRoot 'cokluuyap_sira_kapisi_kok.cer'))
    $h = -join ([Security.Cryptography.SHA256]::Create().ComputeHash($b) | ForEach-Object { $_.ToString('X2') })
    if ($h -ne $beklenen) {
        Write-Host 'HATA: sertifika dosyasi degistirilmis. Kurulum DURDURULDU.' -ForegroundColor Red
        exit 2
    }
    Write-Host ''
    Write-Host 'Kok sertifika parmak izi (SHA256):'
    Write-Host ('  ' + (($h -replace '(.{8})', '$1 ').Trim()))
    Write-Host 'Bu deger cokluuyap bilgisayarindaki son_komut.txt ile AYNI olmali.'
    $cevap = Read-Host 'Ayniysa E yazip Enter''a basin'
    if ($cevap -ne 'E' -and $cevap -ne 'e') { Write-Host 'Iptal edildi, hicbir sey degismedi.'; exit 3 }

    $c = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList (,$b)
    $st = New-Object System.Security.Cryptography.X509Certificates.X509Store('Root', 'LocalMachine')
    $st.Open('ReadWrite')
    try {
        @($st.Certificates | Where-Object { $_.Subject -like '*@KOK@*' -and $_.Thumbprint -ne $c.Thumbprint }) |
            ForEach-Object { $st.Remove($_) }
        $st.Add($c)
    } finally { $st.Close() }

    $hosts = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
    $satirlar = @(Get-Content -LiteralPath $hosts -Encoding Default | Where-Object { $_ -notmatch 'cokluuyap-sira-kapisi' })
    $satirlar += '@KAPI@ @HOST@ @ETIKET@'
    Set-Content -LiteralPath $hosts -Value $satirlar -Encoding Default
    & (Join-Path $env:SystemRoot 'System32\ipconfig.exe') /flushdns | Out-Null

    Write-Host ''
    Write-Host 'Tamam. @HOST@ artik @KAPI@ (cokluuyap) uzerinden gidiyor.' -ForegroundColor Green
    Write-Host 'Kontrol: tarayicida https://@HOST@ acilmali, sertifika "@KOK@" olmali.'
    Write-Host 'DIKKAT: Bu bilgisayardan TARAYICI ile UYAP''a girenler de cokluuyap uzerinden gider.'
    Write-Host 'Not: cokluuyap paylasimi kapaliyken bu bilgisayar UYAP''a ulasamaz. Geri almak: KALDIR.bat'
    Write-Host 'Kurulum bitti: bu klasoru USB/paylasimdan SILIN (KALDIR.bat icin bir kopya saklayin).'
} catch {
    Write-Host ('HATA: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
""".replace("@SHA@", kok_sha256).replace("@KAPI@", kapi_ip).replace("@HOST@", UYAP_HOST) \
   .replace("@ETIKET@", HOSTS_ETIKET).replace("@KOK@", _KOK_ADI)


def _kaldir_bat() -> str:
    return f"""@echo off
REM cokluuyap Sira Kapisi - bot bilgisayarinda eski hale donus (YONETICI olarak calistirin)
{_YONETICI}
{_PS} -NoProfile -ExecutionPolicy Bypass -File "%~dp0KALDIR.ps1"
pause
"""


def _kaldir_ps1() -> str:
    return r"""# cokluuyap Sira Kapisi - eski hale donus (KALDIR.bat cagirir, YONETICI gerekir)
$ErrorActionPreference = 'Stop'
try {
    $hosts = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
    $satirlar = @(Get-Content -LiteralPath $hosts -Encoding Default | Where-Object { $_ -notmatch 'cokluuyap-sira-kapisi' })
    Set-Content -LiteralPath $hosts -Value $satirlar -Encoding Default
    # Hangi kurulumdan kalmis olursa olsun TUM cokluuyap kokleri silinir.
    $st = New-Object System.Security.Cryptography.X509Certificates.X509Store('Root', 'LocalMachine')
    $st.Open('ReadWrite')
    try {
        @($st.Certificates | Where-Object { $_.Subject -like '*@KOK@*' }) | ForEach-Object { $st.Remove($_) }
    } finally { $st.Close() }
    & (Join-Path $env:SystemRoot 'System32\ipconfig.exe') /flushdns | Out-Null
    Write-Host 'Tamam. @HOST@ yeniden dogrudan gidiyor.' -ForegroundColor Green
} catch {
    Write-Host ('HATA: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
""".replace("@KOK@", _KOK_ADI).replace("@HOST@", UYAP_HOST)


def _lan_ip_tahmin() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # paket gönderilmez; yalnız çıkış arayüzünü seçer
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def kur(bot_ipler, kapi_ip=None, port=443, aralik_sn=1.0, mod="sirala") -> str:
    kapi_ip = _lan_ipv4(kapi_ip or _lan_ip_tahmin(), "Kapı IP'si (--kapi-ip)")
    if port != 443:
        raise SystemExit("hosts yolu yalnız 443 ile çalışır (bot https://avukat.uyap.gov.tr'ye gider).")
    bot_ipler = sorted({_lan_ipv4(ip, "Bot IP'si (--bot-ip)") for ip in bot_ipler})
    if not bot_ipler:
        raise SystemExit("En az bir --bot-ip gerekli.")
    if kapi_ip in bot_ipler:
        raise SystemExit("Bot IP'si bu bilgisayarın kendi adresi olamaz (KUR.bat yalnız bot PC'sinde çalışır).")
    if mod not in ("sirala", "izle"):
        raise SystemExit("mod 'sirala' ya da 'izle' olmalı.")
    aralik_sn = min(max(float(aralik_sn), 0.0), 60.0)
    klasor = _yol("bot_pc_icin")
    ca_cer, _crt, _key, parmak = sertifika_uret(klasor)
    import hashlib
    with open(ca_cer, "rb") as f:
        kok_sha256 = hashlib.sha256(f.read()).hexdigest().upper()
    for ad, icerik in (("KUR.bat", _kur_bat()), ("KUR.ps1", _kur_ps1(kapi_ip, kok_sha256)),
                       ("KALDIR.bat", _kaldir_bat()), ("KALDIR.ps1", _kaldir_ps1())):
        with open(os.path.join(klasor, ad), "w", encoding="ascii", newline="\r\n") as f:
            f.write(icerik)
    ayar = ayar_oku()
    ayar.pop("dinle", None)   # eski sürümün 0.0.0.0 ayarı: artık yalnız kapi_ip'de dinlenir
    ayar.update(etkin=True, mod=mod, port=port, aralik_sn=aralik_sn,
                izinli_ipler=list(bot_ipler), kapi_ip=kapi_ip, kok_parmak_izi=parmak,
                kok_sha256=kok_sha256)
    ayar_yaz(ayar)
    return klasor


# ── Sıralama durumu ───────────────────────────────────────────────────────────────────────

class _Durum:
    def __init__(self, aralik_sn: float):
        self.aralik_sn = max(0.0, float(aralik_sn))
        self.sira = asyncio.Lock()       # bot istekleri kendi aralarında FIFO
        self.son_bitis = 0.0
        self.sayac = 0
        self.cakisma = 0


def _istemci_izinli(ip: str, izinli) -> bool:
    # Varsayılan RET: izin listesi boşsa (ayar elle bozulmuş vb.) kimse geçemez; eskiden
    # bütün LAN'a açılıyordu.
    return bool(izinli) and ip in izinli


def _yol_dogru(yol: bytes) -> bool:
    """İstek hedefi yalnız köke göre yol olabilir ("/..."). "@kotu.site/x" ya da mutlak
    "https://kotu.site/x" hedefi URL'yi başka bir sunucuya çevirebilirdi (SSRF)."""
    return yol.startswith(b"/") and not yol.startswith((b"//", b"/\\"))


def _gunluk() -> logging.Logger:
    log = logging.getLogger("uyap.sira_kapisi")
    if not log.handlers:
        os.makedirs(veri_klasoru(), exist_ok=True)
        h = logging.handlers.RotatingFileHandler(
            _yol("kapi.log"), maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


def uygulama_yap(ayar: dict, transport=None, gunluk=None, izinli_kaynak=None):
    """Ham ASGI uygulaması (çoklu Set-Cookie başlıklarını bozmadan iletmek için FastAPI yok).
    izinli_kaynak: çağrılabilir → güncel izinli IP kümesi (eşleştirmede canlı güncellenir)."""
    durum = _Durum(ayar.get("aralik_sn", 1.0))
    izle = (ayar.get("mod") == "izle")
    if izinli_kaynak is None:
        _sabit = frozenset(ayar.get("izinli_ipler") or [])
        izinli_kaynak = lambda: _sabit  # noqa: E731
    gunluk = gunluk or _gunluk()
    transport = transport or httpx.AsyncHTTPTransport(
        verify=True, retries=0)
    zaman_asimi = {"connect": 15.0, "read": 120.0, "write": 60.0, "pool": 15.0}

    async def _hata(send, kod: int, metin: str):
        govde = metin.encode("utf-8")
        await send({"type": "http.response.start", "status": kod, "headers": [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"content-length", str(len(govde)).encode())]})
        await send({"type": "http.response.body", "body": govde})

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                m = await receive()
                if m["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif m["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return

        istemci = (scope.get("client") or ("", 0))[0]
        if not _istemci_izinli(istemci, izinli_kaynak()):
            gunluk.info(f"RET istemci={istemci} (izinli değil)")
            return await _hata(send, 403, "Bu bilgisayar sira kapisini kullanamaz.")

        basliklar = []
        host = ""
        for k, v in scope["headers"]:
            ad = k.decode("latin-1").lower()
            if ad == "host":
                host = v.decode("latin-1").split(":")[0].lower()
                continue
            if ad in _ISTEK_AT:
                continue
            basliklar.append((k, v))
        if host != UYAP_HOST:
            return await _hata(send, 421, "Yalniz avukat.uyap.gov.tr iletilir.")
        basliklar.append((b"host", UYAP_HOST.encode()))

        yol = scope.get("raw_path") or scope["path"].encode()
        if not _yol_dogru(yol):
            gunluk.info(f"RET istemci={istemci} geçersiz hedef {yol[:80]!r}")
            return await _hata(send, 400, "Gecersiz istek hedefi.")
        sorgu = scope.get("query_string") or b""
        try:
            url = httpx.URL(scheme="https", host=UYAP_HOST,
                            raw_path=yol + (b"?" + sorgu if sorgu else b""))
        except Exception:
            url = None
        # Son savunma: URL ne olursa olsun yalnız https://avukat.uyap.gov.tr (443) olabilir.
        if url is None or url.scheme != "https" or url.host != UYAP_HOST or url.port is not None:
            gunluk.info(f"RET istemci={istemci} geçersiz hedef {yol[:80]!r}")
            return await _hata(send, 400, "Gecersiz istek hedefi.")

        govde = bytearray()
        while True:
            m = await receive()
            if m["type"] == "http.disconnect":
                return
            govde += m.get("body", b"")
            if len(govde) > EN_BUYUK_GOVDE:
                gunluk.info(f"RET istemci={istemci} gövde sınırı aşıldı")
                return await _hata(send, 413, "Istek govdesi cok buyuk.")
            if not m.get("more_body"):
                break

        istek = httpx.Request(scope["method"], url, headers=basliklar, content=bytes(govde),
                              extensions={"timeout": zaman_asimi})

        gelis = time.monotonic()
        cakisti = False
        yanit = None

        class _YanitBuyuk(Exception):
            pass

        async def uyaptan_al():
            """UYAP yanıtını tümüyle belleğe alır (ham: content-encoding çözülmeden, başlıklarla
            birebir tutarlı). Bota gönderim KİLİT DIŞINDA yapılır."""
            nonlocal yanit
            yanit = await transport.handle_async_request(istek)
            parcalar, boy = [], 0
            try:
                async for parca in yanit.stream:
                    boy += len(parca)
                    if boy > EN_BUYUK_YANIT:
                        raise _YanitBuyuk()
                    parcalar.append(parca)
            finally:
                await yanit.aclose()
            return b"".join(parcalar)

        try:
            if izle:
                cakisti = _runtime.UYAP_WRITE_LOCK.locked() or _runtime.aktif_istek_sayisi() > 0
                basla = time.monotonic()
                yanit_govde = await asyncio.wait_for(uyaptan_al(), KILIT_SURE_SINIRI)
                bitis = time.monotonic()
            else:
                async with durum.sira:
                    bekle = durum.son_bitis + durum.aralik_sn - time.monotonic()
                    if bekle > 0:
                        await asyncio.sleep(bekle)
                    async with _runtime.batch_write():
                        await _runtime.aktif_istekler_bitsin()
                        basla = time.monotonic()
                        try:
                            yanit_govde = await asyncio.wait_for(uyaptan_al(), KILIT_SURE_SINIRI)
                        finally:
                            durum.son_bitis = bitis = time.monotonic()
        except httpx.HTTPError as e:
            gunluk.info(f"HATA {scope['method']} {scope['path']} {type(e).__name__}: {e}")
            return await _hata(send, 502, "UYAP'a ulasilamadi.")
        except asyncio.TimeoutError:
            gunluk.info(f"HATA {scope['method']} {scope['path']} {int(KILIT_SURE_SINIRI)} sn içinde bitmedi")
            return await _hata(send, 504, "UYAP yaniti zamaninda tamamlanmadi.")
        except _YanitBuyuk:
            gunluk.info(f"HATA {scope['method']} {scope['path']} yanıt sınırı aşıldı")
            return await _hata(send, 502, "UYAP yaniti cok buyuk.")

        # Kilit bırakıldı: bota gönderim artık bizim UYAP işlerimizi bekletmez.
        cikis = [(k, v) for k, v in yanit.headers.raw if k.decode("latin-1").lower() not in _HOP]
        await send({"type": "http.response.start", "status": yanit.status_code, "headers": cikis})
        await send({"type": "http.response.body", "body": yanit_govde})
        durum.sayac += 1
        durum.cakisma += int(cakisti)
        gunluk.info(
            f"{'IZLE' if izle else 'SIRA'} {istemci} {scope['method']} {scope['path']} "
            f"{yanit.status_code} bekleme={int((basla - gelis) * 1000)}ms "
            f"sure={int((bitis - basla) * 1000)}ms"
            + (" CAKISMA(bizim istek uctaydi)" if cakisti else ""))

    app.durum = durum
    return app


def _dongu_korumasi() -> None:
    """Bu PC'nin kendisi avukat.uyap.gov.tr'yi yerel bir adrese çözüyorsa (hosts burada da
    değiştirilmiş) kapı kendine istek atar → başlatmayı reddet."""
    try:
        ipler = {ai[4][0] for ai in socket.getaddrinfo(UYAP_HOST, 443, proto=socket.IPPROTO_TCP)}
    except OSError:
        return  # DNS geçici yoksa engelleme; istekler zaten hata döner
    for ip in ipler:
        a = ipaddress.ip_address(ip)
        if a.is_private or a.is_loopback:
            raise RuntimeError(
                f"{UYAP_HOST} bu bilgisayarda {ip} adresine çözülüyor (hosts?). Kapı kendine "
                f"bağlanırdı; bu PC'nin hosts dosyasındaki satırı kaldırın.")


# İzinli IP'ler canlı: bot eklenip silinince kapı yeniden başlamadan güncellenir.
_canli_izinli = [frozenset()]


def _izinli_ayarla(liste) -> None:
    _canli_izinli[0] = frozenset(liste or [])


# ── Denetçi: paylaşım döngüsünde kapıyı ayara göre açar/kapatır/yeniden başlatır ──────────
_denetci = {"dongu": None, "olay": None}


def yenile() -> None:
    """Herhangi bir iş parçacığından: denetçi ayarı yeniden okusun."""
    dongu, olay = _denetci["dongu"], _denetci["olay"]
    if dongu is not None and olay is not None:
        try:
            dongu.call_soon_threadsafe(olay.set)
        except RuntimeError:
            pass


def calisiyor_mu() -> bool:
    return _denetci.get("acik", False)


async def denetci() -> None:
    """run_office içinde koşar (aynı döngü → _runtime kilidi paylaşılır). Ayar değişince kapıyı
    yeniden kurar; yalnız izin listesi değiştiyse yeniden başlatmadan günceller. Kapı açılamazsa
    paylaşımı düşürmez, yalnız günlüğe yazar."""
    _denetci["dongu"] = asyncio.get_running_loop()
    _denetci["olay"] = olay = asyncio.Event()
    srv = gorev = None
    imza = object()

    async def _durdur():
        nonlocal srv, gorev
        if srv is not None:
            srv.should_exit = True
            if gorev is not None and not gorev.done():
                await asyncio.wait({gorev}, timeout=5)
        srv = gorev = None
        _denetci["acik"] = False

    async def _calis(s):
        try:
            await s.serve()
        except (Exception, SystemExit) as e:  # uvicorn bağlanamazsa SystemExit atar
            print(f"[!] Sıra kapısı durdu: {e}")
        finally:
            _denetci["acik"] = False

    try:
        while True:
            ayar = ayar_oku()
            try:
                crt_zaman = os.path.getmtime(_yol("sunucu.crt"))
            except OSError:
                crt_zaman = None
            yeni = (bool(ayar.get("etkin")), ayar.get("kapi_ip"), ayar.get("mod"),
                    ayar.get("aralik_sn"), crt_zaman, bool(ayar.get("izinli_ipler")))
            _izinli_ayarla(ayar.get("izinli_ipler"))
            if yeni != imza or gorev is None or gorev.done():
                await _durdur()
                if ayar.get("etkin") and ayar.get("izinli_ipler"):
                    try:
                        srv = sunucu_yap(ayar, in_thread=True)
                    except Exception as e:
                        print(f"[!] Sıra kapısı başlatılamadı: {e}")
                        srv = None
                    if srv is not None:
                        gorev = asyncio.create_task(_calis(srv))
                        _denetci["acik"] = True
                imza = yeni
            olay.clear()
            # Kapı açık olması gerekirken hata ile durduysa (ör. 443 dolu, IP değişti) 5 dk'da bir
            # yeniden dener; aksi hâlde yalnız yenile() ile uyanır.
            bekle = 300 if (ayar.get("etkin") and ayar.get("izinli_ipler")) else None
            try:
                await asyncio.wait_for(olay.wait(), bekle)
            except asyncio.TimeoutError:
                pass  # döngü başı: kapı durmuşsa (gorev.done()) yeniden kurulur
    finally:
        await _durdur()
        _denetci["dongu"] = _denetci["olay"] = None


def sunucu_yap(ayar: dict = None, in_thread: bool = True):
    """Ayar etkinse yapılandırılmış uvicorn.Server, değilse None döndürür."""
    import uvicorn
    ayar = ayar or ayar_oku()
    if not ayar.get("etkin"):
        return None
    # Varsayılan RET: izinli bot ya da geçerli LAN kapı adresi yoksa hiç dinleme.
    try:
        kapi_ip = _lan_ipv4(ayar.get("kapi_ip", ""), "Kapı IP'si")
        if not ayar.get("izinli_ipler"):
            raise SystemExit("izinli bot IP'si yok")
        for ip in ayar["izinli_ipler"]:
            _lan_ipv4(ip, "Bot IP'si")
    except SystemExit as e:
        print(f"[!] Sıra kapısı açılmadı: {e}. Kurulumu ('kur --bot-ip …') yeniden yapın.")
        return None
    if not (os.path.exists(_yol("sunucu.crt")) and os.path.exists(_yol("sunucu.key"))):
        print("[!] Sıra kapısı etkin ama sertifika yok; 'python -m uyap_core.sira_kapisi kur' çalıştırın.")
        return None
    _dongu_korumasi()
    try:
        _eski_anahtari_sifrele()
        parola = anahtar_parolasi()
    except Exception as e:
        print(f"[!] Sıra kapısı açılmadı: sunucu anahtarı açılamadı ({e}). Kurulumu yeniden yapın.")
        return None
    config = uvicorn.Config(
        # Yalnız LAN kartında dinler (0.0.0.0 değil). proxy_headers KAPALI: izin kontrolündeki
        # istemci adresi yalnız soketten gelir, X-Forwarded-For ile taklit edilemez.
        uygulama_yap(ayar, izinli_kaynak=lambda: _canli_izinli[0]), host=kapi_ip, port=443,
        ssl_certfile=_yol("sunucu.crt"), ssl_keyfile=_yol("sunucu.key"), ssl_keyfile_password=parola,
        log_level="warning", lifespan="off", proxy_headers=False, server_header=False,
        limit_concurrency=EN_COK_BAGLANTI, http="h11", ws="none")
    server = uvicorn.Server(config)
    if in_thread:
        server.install_signal_handlers = lambda: None
    print(f"[i] Sıra kapısı açık: {kapi_ip}:443 mod={ayar.get('mod')} "
          f"aralık={ayar.get('aralik_sn')}sn izinli={ayar.get('izinli_ipler')} "
          f"(günlük: {_yol('kapi.log')})")
    return server


def main(argv=None):
    p = argparse.ArgumentParser(description="cokluuyap sıra kapısı (bot isteklerini sıraya alır)")
    alt = p.add_subparsers(dest="komut", required=True)
    k = alt.add_parser("kur", help="sertifika + bot PC kurulum dosyalarını üret, kapıyı etkinleştir")
    k.add_argument("--bot-ip", action="append", required=True, help="otomasyonun çalıştığı PC'nin IP'si")
    k.add_argument("--kapi-ip", help="bu (cokluuyap) PC'nin LAN IP'si; boşsa tahmin edilir")
    k.add_argument("--aralik", type=float, default=1.0, help="bot istekleri arası en kısa süre (sn)")
    k.add_argument("--mod", choices=("sirala", "izle"), default="sirala")
    alt.add_parser("durum")
    alt.add_parser("ac")
    alt.add_parser("kapat")
    m = alt.add_parser("mod")
    m.add_argument("deger", choices=("sirala", "izle"))
    a = p.parse_args(argv)

    if a.komut == "kur":
        klasor = kur(a.bot_ip, a.kapi_ip, aralik_sn=a.aralik, mod=a.mod)
        ayar = ayar_oku()
        print(f"Kapı IP'si: {ayar['kapi_ip']}  (yanlışsa --kapi-ip ile yeniden kurun)")
        print(f"Bot PC'sine kopyalanacak klasör: {klasor}")
        print("Orada KUR.bat'ı yönetici olarak çalıştırın; sonra cokluuyap paylaşımını yeniden başlatın.")
        sha = ayar["kok_sha256"]
        print("KUR.bat'ın göstereceği kök sertifika parmak izi (SHA256) BUNUNLA AYNI olmalı:")
        print("  " + " ".join(sha[i:i + 8] for i in range(0, len(sha), 8)))
        print("Klasörü USB ya da yalnız sizin erişebildiğiniz bir yerle taşıyın; ortak paylaşımda bırakmayın.")
        print("Bu PC'de güvenlik duvarı izni (bir kez, YÖNETİCİ PowerShell/cmd):")
        print(f'  netsh advfirewall firewall add rule name="cokluuyap sira kapisi" dir=in action=allow '
              f'protocol=TCP localport=443 remoteip={",".join(ayar["izinli_ipler"])}')
        return
    ayar = ayar_oku()
    if a.komut in ("ac", "kapat"):
        ayar["etkin"] = (a.komut == "ac")
        ayar_yaz(ayar)
    elif a.komut == "mod":
        ayar["mod"] = a.deger
        ayar_yaz(ayar)
    print(json.dumps(ayar, ensure_ascii=False, indent=2))
    if a.komut != "durum":
        print("Değişiklik paylaşım yeniden başlatılınca geçerli olur.")


if __name__ == "__main__":
    main()
