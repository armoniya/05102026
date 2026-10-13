"""lan_tls birim testleri: python -m unittest uyap_core.test_lan_tls (Uyap Haricen Giriş klasöründen)."""
import asyncio
import os
import shutil
import ssl
import tempfile
import unittest
from unittest import mock

from uyap_core import lan_tls

IZI = "ab" * 32


class TestBilet(unittest.TestCase):
    def test_gidis_donus(self):
        self.assertEqual(lan_tls.bilet_ayir(lan_tls.bilet_birlestir("Ab-c_9", IZI)), ("Ab-c_9", IZI))

    def test_eski_ofis_izi_yok(self):
        self.assertEqual(lan_tls.bilet_ayir("Ab-c_9"), ("Ab-c_9", None))
        self.assertEqual(lan_tls.bilet_ayir(""), ("", None))

    def test_bozuk_iz_kabul_edilmez(self):
        self.assertEqual(lan_tls.bilet_ayir("x.1234")[1], None)
        self.assertEqual(lan_tls.bilet_ayir("x." + "Z" * 64)[1], None)


class TestSertifikaVeSabitleme(unittest.TestCase):
    def setUp(self):
        self.dizin = tempfile.mkdtemp()
        self.yama = mock.patch.object(lan_tls, "_dizin", lambda: self.dizin)
        self.yama.start()

    def tearDown(self):
        self.yama.stop()
        shutil.rmtree(self.dizin, ignore_errors=True)

    def test_uretilir_ve_yeniden_kullanilir(self):
        s1, k1, iz1 = lan_tls.sertifika_hazirla(["192.168.1.10"])
        self.assertTrue(os.path.exists(s1) and os.path.exists(k1))
        _, _, iz2 = lan_tls.sertifika_hazirla(["192.168.1.10"])
        self.assertEqual(iz1, iz2)
        self.assertEqual(len(iz1), 64)

    def _sunucu_ile(self, sert, anahtar, izi):
        """Gerçek TLS sunucusuna sabitli_baglam ile bağlanır; (bağlam, ilk satır) döndürür."""
        async def calis():
            sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            sctx.load_cert_chain(sert, anahtar)

            async def isle(r, w):
                w.write(b"ok\n")
                await w.drain()
                w.close()

            srv = await asyncio.start_server(isle, "127.0.0.1", 0, ssl=sctx)
            port = srv.sockets[0].getsockname()[1]
            try:
                ctx = await lan_tls.sabitli_baglam("127.0.0.1", port, izi)
                satir = None
                if ctx is not None:  # sabitlenmiş bağlam gerçekten doğrulayarak bağlanabilmeli
                    r, w = await asyncio.open_connection("127.0.0.1", port, ssl=ctx)
                    satir = await r.readline()
                    w.close()
                return ctx, satir
            finally:
                srv.close()
                await srv.wait_closed()
        return asyncio.run(calis())

    def test_dogru_iz_baglanir(self):
        sert, anahtar, izi = lan_tls.sertifika_hazirla()
        ctx, satir = self._sunucu_ile(sert, anahtar, izi)
        self.assertIsNotNone(ctx)
        self.assertEqual(satir, b"ok\n")

    def test_yanlis_iz_reddedilir(self):
        sert, anahtar, _ = lan_tls.sertifika_hazirla()
        ctx, _ = self._sunucu_ile(sert, anahtar, IZI)
        self.assertIsNone(ctx)

    def test_baska_sertifikaya_sabitli_baglam_guvenmez(self):
        """Araya giren (başka sertifika) — eski sertifikaya sabitli bağlam el sıkışmayı reddeder."""
        sert, anahtar, izi = lan_tls.sertifika_hazirla()
        ctx, _ = self._sunucu_ile(sert, anahtar, izi)
        sahte_dizin = tempfile.mkdtemp()
        try:
            with mock.patch.object(lan_tls, "_dizin", lambda: sahte_dizin):
                s2, k2, _ = lan_tls.sertifika_hazirla()

            async def dene():
                sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                sctx.load_cert_chain(s2, k2)
                srv = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0, ssl=sctx)
                port = srv.sockets[0].getsockname()[1]
                try:
                    await asyncio.open_connection("127.0.0.1", port, ssl=ctx)
                finally:
                    srv.close()
                    await srv.wait_closed()
            with self.assertRaises(ssl.SSLCertVerificationError):
                asyncio.run(dene())
        finally:
            shutil.rmtree(sahte_dizin, ignore_errors=True)

    def test_ulasilamayan_adres(self):
        self.assertIsNone(asyncio.run(lan_tls.sabitli_baglam("127.0.0.1", 1, IZI, timeout=0.5)))


if __name__ == "__main__":
    unittest.main()
