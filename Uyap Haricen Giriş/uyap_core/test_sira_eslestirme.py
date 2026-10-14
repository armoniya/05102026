"""sira_eslestirme testleri: python -m unittest uyap_core.test_sira_eslestirme
Sistem hosts dosyasına ve makine kök deposuna DOKUNMAZ (geçici dosya + geçici kullanıcı deposu)."""
import os
import json
import socket
import asyncio
import hashlib
import datetime
import tempfile
import threading
import unittest
from unittest import mock

from uyap_core import sira_kapisi as sk
from uyap_core import sira_eslestirme as se


def _oku(yol):
    with open(yol, "rb") as f:
        return f.read()


class _GeciciVeri(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self._eski = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self.d

    def tearDown(self):
        if self._eski is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self._eski


def _ca(kisit=True, ad="cokluuyap Sira Kapisi (yalniz avukat.uyap.gov.tr)", izinli=("avukat.uyap.gov.tr",)):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    n = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ad)])
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    b = (x509.CertificateBuilder().subject_name(n).issuer_name(n).public_key(k.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(t)
         .not_valid_after(t + datetime.timedelta(days=30))
         .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
         .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True))
    if kisit:
        b = b.add_extension(x509.NameConstraints([x509.DNSName(i) for i in izinli], None), critical=True)
    return b.sign(k, hashes.SHA256()).public_bytes(serialization.Encoding.DER)


class TestKokDogrulama(_GeciciVeri):
    def test_bizim_kok_kabul_digerleri_red(self):
        sk.sertifika_uret(self.d)
        se.kok_dogrula(sk.kok_der())
        for der in (_ca(kisit=False),                                   # her siteye geçerli kök
                    _ca(izinli=("avukat.uyap.gov.tr", "google.com")),   # fazladan alan
                    _ca(izinli=("uyap.gov.tr",)),                       # üst alan
                    _ca(ad="Baska Kok"),
                    b"\x30\x03\x02\x01\x00"):
            with self.assertRaises(se.EslestirmeHatasi):
                se.kok_dogrula(der)


class TestEslestirme(_GeciciVeri):
    def setUp(self):
        super().setUp()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.yamalar = [mock.patch.object(se, "ESLESTIRME_PORT", self.port),
                        mock.patch.object(se, "_ip_kabul", lambda ip: ip.startswith("127.")),
                        mock.patch.object(sk, "bot_ekle")]
        for y in self.yamalar:
            y.start()
        self.bot_ekle = sk.bot_ekle

    def tearDown(self):
        for y in self.yamalar:
            y.stop()
        super().tearDown()

    def _senaryo(self, ofis_onay=True, bot_onay=True, nb_boz=False):
        sunucu = se.EslestirmeSunucusu()
        sunucu.baslat()
        sonuc = {}

        def ofis():
            while True:
                tur, veri = sunucu.olaylar.get(timeout=20)
                if tur == "istek":
                    sonuc["ofis_kod"] = veri["kod"]
                    sonuc["ofis_ip"] = veri["ip"]
                    sunucu.karar_ver(ofis_onay)
                elif tur == "bitti":
                    sonuc["bitti"] = veri
                    return
        t = threading.Thread(target=ofis)
        t.start()
        try:
            ist = se.EslestirmeIstemcisi("127.0.0.1", _kaynak=("127.0.0.2", 0))
            if nb_boz:
                with mock.patch.object(se.secrets, "token_bytes", side_effect=[b"a" * 32, b"b" * 32]):
                    orijinal = se._gonder

                    def boz(sock, ileti):
                        if ileti.get("t") == "acik":
                            ileti = dict(ileti, nb=(b"c" * 32).hex())
                        orijinal(sock, ileti)
                    with mock.patch.object(se, "_gonder", boz):
                        with self.assertRaises(se.EslestirmeHatasi):
                            ist.baglan()
                            ist.karar_bildir_ve_bekle(True)
                sonuc["bot_kod"] = None
            else:
                sonuc["bot_kod"] = ist.baglan()
                try:
                    sonuc["bot_karar"] = ist.karar_bildir_ve_bekle(bot_onay)
                except se.EslestirmeHatasi:
                    sonuc["bot_karar"] = False
            ist.kapat()
        finally:
            if nb_boz or not (ofis_onay and bot_onay):
                sunucu.durdur()
            t.join(25)
        return sonuc

    def test_iki_taraf_onaylarsa_bot_eklenir(self):
        s = self._senaryo()
        self.assertEqual(s["ofis_kod"], s["bot_kod"])
        self.assertRegex(s["bot_kod"], r"^\d{3} \d{3}$")
        self.assertTrue(s["bot_karar"])
        self.assertTrue(s["bitti"]["basarili"])
        self.bot_ekle.assert_called_once()
        self.assertEqual(self.bot_ekle.call_args[0][0], "127.0.0.2")

    def test_ofis_reddederse_eklenmez(self):
        s = self._senaryo(ofis_onay=False)
        self.assertFalse(s["bot_karar"])
        self.bot_ekle.assert_not_called()

    def test_bot_reddederse_eklenmez(self):
        s = self._senaryo(bot_onay=False)
        self.assertFalse(s.get("bot_karar"))
        self.bot_ekle.assert_not_called()

    def test_taahhut_bozulursa_kod_gosterilmez(self):
        s = self._senaryo(nb_boz=True)
        self.assertNotIn("ofis_kod", s)
        self.bot_ekle.assert_not_called()

    def test_kod_iki_tarafin_rastgelesine_bagli(self):
        k1 = se.dogrulama_kodu(b"c", "1", "2", b"a" * 32, b"b" * 32)
        self.assertNotEqual(k1, se.dogrulama_kodu(b"c", "1", "2", b"a" * 32, b"x" * 32))
        self.assertNotEqual(k1, se.dogrulama_kodu(b"c", "1", "3", b"a" * 32, b"b" * 32))


class TestHosts(unittest.TestCase):
    def test_ekle_degistir_kaldir_diger_baytlar_korunur(self):
        with tempfile.TemporaryDirectory() as d:
            yol = os.path.join(d, "hosts")
            ilk = "# kendi notum: çğüşıö\r\n10.0.0.5 nas.local\r\n".encode("cp1254")
            with open(yol, "wb") as f:
                f.write(ilk)
            se.hosts_guncelle("192.168.1.10", yol)
            self.assertEqual(se.hosts_kapi_ip(yol), "192.168.1.10")
            se.hosts_guncelle("192.168.1.11", yol)
            veri = _oku(yol)
            self.assertEqual(veri.count(b"cokluuyap-sira-kapisi"), 1)
            self.assertTrue(veri.startswith(ilk))
            se.hosts_guncelle(None, yol)
            self.assertEqual(_oku(yol), ilk)
            self.assertIsNone(se.hosts_kapi_ip(yol))


@unittest.skipUnless(os.name == "nt", "Windows sertifika deposu")
class TestSertifikaDeposu(_GeciciVeri):
    DEPO = "CokluUyapTestDeposu"

    def tearDown(self):
        import ctypes
        try:
            ctypes.WinDLL("crypt32").CertUnregisterSystemStore(
                ctypes.c_wchar_p(self.DEPO), 0x10000 | 0x10)  # CURRENT_USER | DELETE
        except Exception:
            pass
        super().tearDown()

    def test_kur_eskiyi_siler_kaldir_temizler(self):
        konum = se._GECERLI_KULLANICI
        sk.sertifika_uret(self.d)
        eski = sk.kok_der()
        sk.sertifika_uret(self.d)
        yeni = sk.kok_der()
        se.kok_kur(eski, konum, self.DEPO)
        se.kok_kur(yeni, konum, self.DEPO)
        c, depo = se._depo_ac(konum, self.DEPO, salt_okunur=True)
        try:
            self.assertEqual(se._kokleri_listele_sil(c, depo, sil=False), 1)   # eski silindi
        finally:
            c.CertCloseStore(depo, 0)
        self.assertTrue(se.kok_kurulu_mu(konum, self.DEPO))
        self.assertEqual(se.kok_kaldir(konum, self.DEPO), 1)
        self.assertFalse(se.kok_kurulu_mu(konum, self.DEPO))


class TestYoneticiGirdisi(unittest.TestCase):
    def test_bozuk_girdi_sisteme_dokunmadan_reddedilir(self):
        import base64
        with mock.patch.object(se, "kok_kur") as kk, mock.patch.object(se, "hosts_guncelle") as hg:
            self.assertEqual(se.yonetici_main(["bot-kur", "8.8.8.8", base64.b64encode(b"x").decode()]), 11)
            self.assertEqual(se.yonetici_main(["bot-kur", "192.168.1.10", base64.b64encode(_ca(kisit=False)).decode()]), 10)
            self.assertEqual(se.yonetici_main(["bot-kur", "192.168.1.10", "%%%"]), 10)
            self.assertEqual(se.yonetici_main(["baska"]), 1)
            kk.assert_not_called()
            hg.assert_not_called()


class TestBotListesiVeDenetci(_GeciciVeri):
    def test_bot_ekle_sil(self):
        sk.bot_ekle("192.168.1.50", "BOT-PC", "192.168.1.10")
        sk.bot_ekle("192.168.1.51", "BOT2", "192.168.1.10")
        a = sk.ayar_oku()
        self.assertTrue(a["etkin"])
        self.assertEqual(a["izinli_ipler"], ["192.168.1.50", "192.168.1.51"])
        sk.bot_sil("192.168.1.50")
        self.assertEqual(sk.ayar_oku()["izinli_ipler"], ["192.168.1.51"])
        with self.assertRaises(SystemExit):
            sk.bot_ekle("8.8.8.8", "x", "192.168.1.10")

    def test_denetci_acar_canli_gunceller_kapatir(self):
        olaylar = []

        class SahteSunucu:
            def __init__(self):
                self.should_exit = False

            async def serve(self):
                olaylar.append("ac")
                while not self.should_exit:
                    await asyncio.sleep(0.01)
                olaylar.append("kapat")

        async def senaryo():
            with mock.patch.object(sk, "sunucu_yap", lambda ayar, in_thread=True: SahteSunucu()):
                gorev = asyncio.create_task(sk.denetci())
                await asyncio.sleep(0.05)
                self.assertEqual(olaylar, [])                       # bot yok → kapı yok
                sk.bot_ekle("192.168.1.50", "B", "192.168.1.10")    # yenile() çağırır
                await asyncio.sleep(0.1)
                self.assertEqual(olaylar, ["ac"])
                self.assertIn("192.168.1.50", sk._canli_izinli[0])
                sk.bot_ekle("192.168.1.51", "C", "192.168.1.10")    # yalnız liste → yeniden başlamaz
                await asyncio.sleep(0.1)
                self.assertEqual(olaylar, ["ac"])
                self.assertIn("192.168.1.51", sk._canli_izinli[0])
                sk.etkin_ayarla(False)
                await asyncio.sleep(0.1)
                self.assertEqual(olaylar, ["ac", "kapat"])
                sk.etkin_ayarla(True)
                await asyncio.sleep(0.1)
                gorev.cancel()
                await asyncio.gather(gorev, return_exceptions=True)
                self.assertEqual(olaylar, ["ac", "kapat", "ac", "kapat"])  # iptalde kapanır
        asyncio.run(senaryo())


if __name__ == "__main__":
    unittest.main()
