"""Çoklu UYAP şifreli günlük aracı (2026-11-14).

Masaüstü program günlüğünü .cul dosyalarına şifreli yazar; kullanıcı okuyamaz. Program "Günlüğü kaydet /
gönder" ile bunları bir .zip paketinde (içinde .cul dosyaları) paylaşır. Bu araç paketi ya da tek .cul'u
GİZLİ anahtarla çözer.

Kullanım:
  python gunluk_coz.py anahtar-uret <gizli_anahtar.pem>      # bir kez; açık anahtarı (hex) yazdırır
  python gunluk_coz.py coz <gizli_anahtar.pem> <paket.zip | dosya.cul> [cikti.txt]

.cul biçimi:  "CUL1" | geçici açık anahtar (65 bayt, P-256 sıkıştırılmamış)
              ardından kayıtlar: [u32 LE uzunluk][12 bayt nonce][AES-256-GCM şifreli metin + 16 bayt etiket]
Anahtar:      AES anahtarı = SHA-256("cokluuyap-gunluk-v1" || ECDH(geçici gizli, satıcı açık).x)
Gizli anahtar asla repoya / sunucuya konmaz.
"""
import hashlib, io, os, struct, sys, zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ETIKET = b"cokluuyap-gunluk-v1"


def anahtar_uret(yol):
    if os.path.exists(yol):
        sys.exit(f"Zaten var, üzerine yazılmaz: {yol}")
    gizli = ec.generate_private_key(ec.SECP256R1())
    pem = gizli.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())
    os.makedirs(os.path.dirname(os.path.abspath(yol)), exist_ok=True)
    with open(yol, "wb") as f:
        f.write(pem)
    acik = gizli.public_key().public_bytes(serialization.Encoding.X962,
                                          serialization.PublicFormat.UncompressedPoint)
    print(acik.hex())


def cul_coz(gizli, veri: bytes) -> str:
    if veri[:4] != b"CUL1":
        raise ValueError("CUL1 başlığı yok")
    gecici = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), veri[4:69])
    ortak = gizli.exchange(ec.ECDH(), gecici)
    aes = AESGCM(hashlib.sha256(ETIKET + ortak).digest())
    i, satirlar = 69, []
    while i + 4 <= len(veri):
        (n,) = struct.unpack_from("<I", veri, i)
        i += 4
        if n < 28 or i + n > len(veri):
            satirlar.append("[!] kesik kayıt (dosya sonu)")
            break
        nonce, govde = veri[i:i + 12], veri[i + 12:i + n]
        i += n
        try:
            satirlar.append(aes.decrypt(nonce, govde, None).decode("utf-8", "replace"))
        except Exception:
            satirlar.append("[!] bozuk kayıt")
    return "\n".join(satirlar)


def coz(pem_yol, girdi, cikti=None):
    with open(pem_yol, "rb") as f:
        gizli = serialization.load_pem_private_key(f.read(), password=None)
    parcalar = []
    if zipfile.is_zipfile(girdi):
        with zipfile.ZipFile(girdi) as z:
            for ad in sorted(z.namelist()):
                if ad.endswith(".cul"):
                    parcalar.append(f"===== {ad} =====\n" + cul_coz(gizli, z.read(ad)))
                elif ad.endswith(".txt"):
                    parcalar.append(f"===== {ad} =====\n" + z.read(ad).decode("utf-8", "replace"))
    else:
        with open(girdi, "rb") as f:
            parcalar.append(cul_coz(gizli, f.read()))
    metin = "\n\n".join(parcalar)
    if cikti:
        with open(cikti, "w", encoding="utf-8") as f:
            f.write(metin)
        print(f"Yazıldı: {cikti}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(metin)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "anahtar-uret":
        anahtar_uret(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "coz":
        coz(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
    else:
        print(__doc__)
