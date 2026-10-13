"""
Yerel ağ (LAN) doğrudan erişimi için TLS — kendinden imzalı sertifika + parmak izi sabitleme.

Güvenlik taraması 2026-09-30 bulgu 4.2: "Yerel ağdan doğrudan erişim" açıkken ofis proxy'si
LAN'a düz HTTP ile açılıyordu → aynı ağdaki biri LAN biletini / ofis parolasını ve UYAP
trafiğini okuyabilirdi. Artık:

  • Düz HTTP (8800) yalnız 127.0.0.1'de kalır; LAN'a yalnız HTTPS kapısı (vars. 8843) açılır.
  • Sertifika ofiste bir kez üretilir (EC P-256, 3 yıl) ve LOCALAPPDATA altında saklanır.
  • SHA-256 parmak izi, signaling (wss, kimliği doğrulanmış kanal) üzerinden zaten giden LAN
    biletine eklenir: "<bilet>.<parmakizi>". Sunucu bileti olduğu gibi iletir — sunucu
    değişikliği gerekmez. Üye (home_client) önce sertifikayı çeker, parmak izini karşılaştırır,
    tutarsa YALNIZ o sertifikaya güvenen bir SSL bağlamıyla bağlanır; bileti ancak bundan
    sonra gönderir. Araya giren biri başka sertifika sunarsa bağlantı kurulmaz (tünele düşer).
  • Tarayıcıyla LAN girişi (https://ofis-ip:8843/__uyap_lan__/giris) sertifika uyarısı verir;
    ofis programının günlüğündeki parmak iziyle karşılaştırılıp kabul edilebilir.
"""
import asyncio
import datetime
import hashlib
import ipaddress
import os
import ssl

LAN_TLS_PORT = int(os.environ.get("UYAP_LAN_TLS_PORT") or 8843)
_AYIRAC = "."  # bilet token_urlsafe → [A-Za-z0-9_-]; nokta içermez
_YENILEME_PAYI = datetime.timedelta(days=30)


def _dizin():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "UyapIcra", "lan_tls")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "uyapicra", "lan_tls")


def parmak_izi(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def _sertifika_uret(ips):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    anahtar = ec.generate_private_key(ec.SECP256R1())
    ad = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Coklu UYAP LAN")])
    san = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    for ip in ips:
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass
    simdi = datetime.datetime.now(datetime.timezone.utc)
    sert = (x509.CertificateBuilder()
            .subject_name(ad).issuer_name(ad)
            .public_key(anahtar.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(simdi - datetime.timedelta(minutes=5))
            .not_valid_after(simdi + datetime.timedelta(days=3 * 365))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .sign(anahtar, hashes.SHA256()))
    anahtar_pem = anahtar.private_bytes(serialization.Encoding.PEM,
                                        serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption())
    return sert.public_bytes(serialization.Encoding.PEM), anahtar_pem


def _gecerli_mi(sert_pem: bytes) -> bool:
    from cryptography import x509
    try:
        sert = x509.load_pem_x509_certificate(sert_pem)
    except Exception:
        return False
    bitis = sert.not_valid_after_utc
    return bitis - datetime.datetime.now(datetime.timezone.utc) > _YENILEME_PAYI


def sertifika_hazirla(ips=()):
    """(sertifika_yolu, anahtar_yolu, parmak_izi) döndürür; yoksa/süresi dolmak üzereyse üretir."""
    d = _dizin()
    os.makedirs(d, exist_ok=True)
    sert_yol = os.path.join(d, "lan_cert.pem")
    anahtar_yol = os.path.join(d, "lan_key.pem")
    sert_pem = b""
    if os.path.exists(sert_yol) and os.path.exists(anahtar_yol):
        with open(sert_yol, "rb") as f:
            sert_pem = f.read()
    if not sert_pem or not _gecerli_mi(sert_pem):
        sert_pem, anahtar_pem = _sertifika_uret(ips)
        # Anahtar yalnız sahibine açık oluşturulur (POSIX); Windows'ta LOCALAPPDATA zaten
        # kullanıcıya özel (bkz. GUVENLIK_TARAMASI §7.4 dbpass.secret ACL denetimi).
        fd = os.open(anahtar_yol, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(anahtar_pem)
        with open(sert_yol, "wb") as f:
            f.write(sert_pem)
    der = ssl.PEM_cert_to_DER_cert(sert_pem.decode("ascii"))
    return sert_yol, anahtar_yol, parmak_izi(der)


# ── Bilet + parmak izi ────────────────────────────────────────────────────────────────

def bilet_birlestir(bilet: str, izi: str) -> str:
    return f"{bilet}{_AYIRAC}{izi}"


def bilet_ayir(lan_token: str):
    """(bilet, parmak_izi | None). Parmak izi yoksa ofis eski sürümdür (düz HTTP LAN)."""
    tok = (lan_token or "").strip()
    bilet, ayr, izi = tok.rpartition(_AYIRAC)
    if ayr and len(izi) == 64 and all(c in "0123456789abcdef" for c in izi):
        return bilet, izi
    return tok, None


# ── İstemci tarafı: sertifikayı çek, parmak iziyle doğrula, sabitlenmiş bağlam üret ──────

async def sabitli_baglam(ip: str, port: int, beklenen_izi: str, timeout: float = 1.5):
    """Ofisin sunduğu sertifikanın parmak izi beklenenle aynıysa YALNIZ o sertifikaya güvenen
    bir ssl.SSLContext döndürür; değilse / ulaşılamazsa None. Bu aşamada hiçbir sır gönderilmez."""
    yokla = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    yokla.check_hostname = False
    yokla.verify_mode = ssl.CERT_NONE
    try:
        _r, w = await asyncio.wait_for(
            asyncio.open_connection(ip, port, ssl=yokla, server_hostname=None), timeout)
    except (OSError, asyncio.TimeoutError, ssl.SSLError):
        return None
    try:
        der = w.get_extra_info("ssl_object").getpeercert(binary_form=True)
    finally:
        w.close()
    if not der or parmak_izi(der) != beklenen_izi:
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False  # IP değişebilir; güven, tam sertifikanın sabitlenmesinden gelir
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(der))
    return ctx

