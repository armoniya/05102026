"""sira_kapisi testleri: python -m unittest uyap_core.test_sira_kapisi (Uyap Haricen Giriş klasöründen).
UYAP'a gitmez: sahte (MockTransport) UYAP + gerçek TLS el sıkışması (127.0.0.1, rastgele port)."""
import os
import ssl
import socket
import asyncio
import logging
import tempfile
import unittest

import httpx

from uyap_core import sira_kapisi, _runtime

_SESSIZ = logging.getLogger("test.sira_kapisi")
_SESSIZ.addHandler(logging.NullHandler())
_SESSIZ.propagate = False


def _oku(yol):
    with open(yol, "rb") as f:
        return f.read()


async def _asgi(app, method="GET", path="/x.ajx", host="avukat.uyap.gov.tr", body=b"",
                istemci="192.168.1.50", headers=()):
    """ASGI uygulamasını doğrudan çağırır; (status, [(k, v)], gövde)."""
    gonderilen = [{"type": "http.request", "body": body, "more_body": False}]
    cikis = []

    async def receive():
        return gonderilen.pop(0) if gonderilen else {"type": "http.disconnect"}

    async def send(m):
        cikis.append(m)

    scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(),
             "query_string": b"", "client": (istemci, 5555),
             "headers": [(b"host", host.encode())] + [(k.encode(), v.encode()) for k, v in headers]}
    await app(scope, receive, send)
    bas = cikis[0]
    return bas["status"], bas["headers"], b"".join(m.get("body", b"") for m in cikis[1:])


class TestIletim(unittest.TestCase):
    def test_seffaf_iletim(self):
        gorulen = {}

        def uyap(req):
            gorulen["host"] = req.headers["host"]
            gorulen["cookie"] = req.headers.get("cookie")
            gorulen["expect"] = req.headers.get("expect")
            gorulen["clen"] = req.headers.get("content-length")
            gorulen["xff"] = req.headers.get("x-forwarded-for")
            gorulen["url"] = str(req.url)
            gorulen["body"] = req.content
            return httpx.Response(200, headers=[("set-cookie", "a=1"), ("set-cookie", "b=2"),
                                                ("connection", "close")], content=b"tamam")

        app = sira_kapisi.uygulama_yap({"aralik_sn": 0, "izinli_ipler": ["192.168.1.50"]},
                                       transport=httpx.MockTransport(uyap), gunluk=_SESSIZ)
        st, hd, govde = asyncio.run(_asgi(app, "POST", "/avukat_dosya.ajx", body=b"q=1",
                                          headers=[("Cookie", "JSESSIONID=bot"), ("Expect", "100-continue"),
                                                   ("Content-Length", "999"), ("X-Forwarded-For", "10.0.0.9")]))
        self.assertEqual((st, govde), (200, b"tamam"))
        self.assertEqual(gorulen["host"], "avukat.uyap.gov.tr")
        self.assertEqual(gorulen["cookie"], "JSESSIONID=bot")  # botun kendi çerezi, bizimki değil
        self.assertIsNone(gorulen["expect"])
        self.assertEqual(gorulen["clen"], "3")                # uzunluk gövdeden yeniden hesaplanır
        self.assertIsNone(gorulen["xff"])                     # bot LAN adresi UYAP'a sızmaz
        self.assertEqual(gorulen["url"], "https://avukat.uyap.gov.tr/avukat_dosya.ajx")
        self.assertEqual(gorulen["body"], b"q=1")
        cerezler = [v for k, v in hd if k.lower() == b"set-cookie"]
        self.assertEqual(cerezler, [b"a=1", b"b=2"])          # çoklu Set-Cookie korunur
        self.assertFalse(any(k.lower() == b"connection" for k, _ in hd))

    def test_redler(self):
        app = sira_kapisi.uygulama_yap({"izinli_ipler": ["192.168.1.50"]},
                                       transport=httpx.MockTransport(lambda r: httpx.Response(200)),
                                       gunluk=_SESSIZ)
        self.assertEqual(asyncio.run(_asgi(app, host="ornek.com"))[0], 421)
        self.assertEqual(asyncio.run(_asgi(app, istemci="192.168.1.99"))[0], 403)
        bos = sira_kapisi.uygulama_yap({}, transport=httpx.MockTransport(lambda r: httpx.Response(200)),
                                       gunluk=_SESSIZ)
        self.assertEqual(asyncio.run(_asgi(bos, istemci="8.8.8.8"))[0], 403)
        self.assertEqual(asyncio.run(_asgi(bos, istemci="10.0.0.7"))[0], 403)  # boş liste = herkese kapalı

    def test_ssrf_hedefleri_reddedilir(self):
        gidilen = []
        app = sira_kapisi.uygulama_yap(
            {"izinli_ipler": ["192.168.1.50"]},
            transport=httpx.MockTransport(lambda r: gidilen.append(str(r.url)) or httpx.Response(200)),
            gunluk=_SESSIZ)
        for yol in ("@kotu.ornek/x", "https://kotu.ornek/x", "//kotu.ornek/x", "/\\kotu.ornek/x",
                    "*", ":8080/x"):
            self.assertEqual(asyncio.run(_asgi(app, path=yol))[0], 400, yol)
        self.assertEqual(gidilen, [])
        self.assertEqual(asyncio.run(_asgi(app, path="/a@b/c"))[0], 200)
        self.assertEqual(httpx.URL(gidilen[0]).host, "avukat.uyap.gov.tr")

    def test_govde_siniri(self):
        app = sira_kapisi.uygulama_yap({"izinli_ipler": ["192.168.1.50"]},
                                       transport=httpx.MockTransport(lambda r: httpx.Response(200)),
                                       gunluk=_SESSIZ)
        eski = sira_kapisi.EN_BUYUK_GOVDE
        sira_kapisi.EN_BUYUK_GOVDE = 10
        try:
            self.assertEqual(asyncio.run(_asgi(app, "POST", body=b"x" * 11))[0], 413)
        finally:
            sira_kapisi.EN_BUYUK_GOVDE = eski


class TestKurulumDogrulama(unittest.TestCase):
    def test_lan_disi_ya_da_bozuk_ip_reddedilir(self):
        for ip in ("8.8.8.8", "127.0.0.1", "0.0.0.0", "169.254.1.1", "fd00::1",
                   "192.168.1.5 & calc", "1.2.3.4'; Remove-Item x"):
            with self.assertRaises(SystemExit):
                sira_kapisi._lan_ipv4(ip, "test")
        self.assertEqual(sira_kapisi._lan_ipv4(" 192.168.1.50 ", "test"), "192.168.1.50")

    def test_kur_gecersiz_girdide_dosya_uretmez(self):
        with tempfile.TemporaryDirectory() as d:
            eski = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = d
            try:
                for bot, kapi in ((["8.8.8.8"], "192.168.1.10"), (["192.168.1.50"], "1.1.1.1"),
                                  (["192.168.1.10"], "192.168.1.10")):
                    with self.assertRaises(SystemExit):
                        sira_kapisi.kur(bot, kapi)
                self.assertFalse(os.path.exists(os.path.join(d, "UyapIcra")))
                sira_kapisi.kur(["192.168.1.50", "192.168.1.50"], "192.168.1.10")
                ayar = sira_kapisi.ayar_oku()
                self.assertEqual(ayar["izinli_ipler"], ["192.168.1.50"])
                self.assertNotIn("dinle", ayar)
                with open(os.path.join(d, "UyapIcra", "sira_kapisi", "bot_pc_icin", "KUR.ps1")) as f:
                    self.assertIn("192.168.1.10 avukat.uyap.gov.tr", f.read())
            finally:
                if eski is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = eski

    def test_sunucu_eksik_ayarda_acilmaz(self):
        with tempfile.TemporaryDirectory() as d:
            eski = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = d
            try:
                sira_kapisi.sertifika_uret(os.path.join(d, "bot"))
                self.assertIsNone(sira_kapisi.sunucu_yap({"etkin": True, "izinli_ipler": [],
                                                          "kapi_ip": "192.168.1.10"}))
                self.assertIsNone(sira_kapisi.sunucu_yap({"etkin": True, "izinli_ipler": ["192.168.1.50"]}))
                self.assertIsNone(sira_kapisi.sunucu_yap({"etkin": True, "izinli_ipler": ["8.8.8.8"],
                                                          "kapi_ip": "192.168.1.10"}))
            finally:
                if eski is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = eski


class TestSira(unittest.TestCase):
    def test_bot_bizim_istege_yol_verir_ve_ust_uste_binmez(self):
        async def senaryo():
            olaylar, ayni_anda, en_cok = [], [0], [0]

            async def uyap_yanit(req):
                ayni_anda[0] += 1
                en_cok[0] = max(en_cok[0], ayni_anda[0])
                olaylar.append(("bot", req.url.path))
                await asyncio.sleep(0.05)
                ayni_anda[0] -= 1
                return httpx.Response(200, content=b"ok")

            app = sira_kapisi.uygulama_yap({"aralik_sn": 0.1, "izinli_ipler": ["192.168.1.50"]},
                                           transport=httpx.MockTransport(uyap_yanit), gunluk=_SESSIZ)

            async def biz():
                async with _runtime.interactive_write():
                    async with _runtime.uyap_istek_izi():
                        olaylar.append(("biz", "basla"))
                        await asyncio.sleep(0.2)
                        olaylar.append(("biz", "bitti"))

            gorev_biz = asyncio.create_task(biz())
            await asyncio.sleep(0.01)
            t0 = asyncio.get_running_loop().time()
            await asyncio.gather(_asgi(app, path="/1"), _asgi(app, path="/2"))
            sure = asyncio.get_running_loop().time() - t0
            await gorev_biz
            return olaylar, en_cok[0], sure

        olaylar, en_cok, sure = asyncio.run(senaryo())
        self.assertEqual(olaylar[:2], [("biz", "basla"), ("biz", "bitti")])  # bot bizi bekledi
        self.assertEqual(en_cok, 1)                                          # botlar üst üste binmedi
        self.assertGreaterEqual(sure, 0.2 + 0.05 + 0.1 + 0.05 - 0.02)        # aralık uygulandı

    def test_izle_modu_kilitlemez_ama_cakismayi_kaydeder(self):
        kayit = []

        class Topla(logging.Handler):
            def emit(self, r):
                kayit.append(r.getMessage())

        log = logging.getLogger("test.sira_kapisi.izle")
        log.addHandler(Topla())
        log.setLevel(logging.INFO)
        log.propagate = False

        async def senaryo():
            app = sira_kapisi.uygulama_yap({"mod": "izle", "izinli_ipler": ["192.168.1.50"]},
                                           transport=httpx.MockTransport(lambda r: httpx.Response(204)),
                                           gunluk=log)
            async with _runtime.interactive_write():
                return await asyncio.wait_for(_asgi(app), 1.0)   # kilit tutulurken de geçer

        self.assertEqual(asyncio.run(senaryo())[0], 204)
        self.assertIn("CAKISMA", kayit[-1])


class TestKilitSuresi(unittest.TestCase):
    def test_yavas_okuyan_bot_kilidi_tutamaz(self):
        async def senaryo():
            app = sira_kapisi.uygulama_yap(
                {"aralik_sn": 0, "izinli_ipler": ["192.168.1.50"]},
                transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 100)),
                gunluk=_SESSIZ)
            istek = [{"type": "http.request", "body": b"", "more_body": False}]

            async def receive():
                return istek.pop(0) if istek else {"type": "http.disconnect"}

            async def send(m):          # bot yanıtı çok yavaş okuyor
                await asyncio.sleep(2)

            scope = {"type": "http", "method": "GET", "path": "/x", "raw_path": b"/x",
                     "query_string": b"", "client": ("192.168.1.50", 1),
                     "headers": [(b"host", b"avukat.uyap.gov.tr")]}
            bot = asyncio.create_task(app(scope, receive, send))
            await asyncio.sleep(0.2)
            t = asyncio.get_running_loop().time()
            async with _runtime.interactive_write():
                gecen = asyncio.get_running_loop().time() - t
            bot.cancel()
            return gecen

        self.assertLess(asyncio.run(senaryo()), 0.5)

    def test_uyap_takilirsa_sure_siniri(self):
        async def yavas_uyap(req):
            await asyncio.sleep(5)
            return httpx.Response(200)

        eski = sira_kapisi.KILIT_SURE_SINIRI
        sira_kapisi.KILIT_SURE_SINIRI = 0.2
        try:
            app = sira_kapisi.uygulama_yap({"aralik_sn": 0, "izinli_ipler": ["192.168.1.50"]},
                                           transport=httpx.MockTransport(yavas_uyap), gunluk=_SESSIZ)
            self.assertEqual(asyncio.run(asyncio.wait_for(_asgi(app), 2))[0], 504)
            self.assertFalse(_runtime.UYAP_WRITE_LOCK.locked())
        finally:
            sira_kapisi.KILIT_SURE_SINIRI = eski


class TestAnahtarVeBetikler(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.eski = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self.d

    def tearDown(self):
        if self.eski is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self.eski

    def test_anahtar_sifreli_ve_parola_dpapi(self):
        from cryptography.hazmat.primitives import serialization
        _, _, key, _ = sira_kapisi.sertifika_uret(os.path.join(self.d, "bot"))
        veri = _oku(key)
        self.assertIn(b"ENCRYPTED PRIVATE KEY", veri)
        with self.assertRaises(TypeError):
            serialization.load_pem_private_key(veri, password=None)
        parola = sira_kapisi.anahtar_parolasi()
        self.assertNotIn(parola.encode(), _oku(sira_kapisi._yol("sunucu.parola")))
        serialization.load_pem_private_key(veri, password=parola.encode())

    def test_eski_sifresiz_anahtar_yerinde_sifrelenir(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        os.makedirs(sira_kapisi.veri_klasoru())
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with open(sira_kapisi._yol("sunucu.key"), "wb") as f:
            f.write(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                    serialization.NoEncryption()))
        sira_kapisi._eski_anahtari_sifrele()
        yeni = serialization.load_pem_private_key(_oku(sira_kapisi._yol("sunucu.key")),
                                                  password=sira_kapisi.anahtar_parolasi().encode())
        self.assertEqual(yeni.public_key().public_numbers(), k.public_key().public_numbers())

    def test_bot_betikleri(self):
        import hashlib
        from cryptography import x509
        klasor = sira_kapisi.kur(["192.168.1.50"], "192.168.1.10")
        cer = _oku(os.path.join(klasor, "cokluuyap_sira_kapisi_kok.cer"))
        sha = hashlib.sha256(cer).hexdigest().upper()
        ps1 = _oku(os.path.join(klasor, "KUR.ps1")).decode("ascii")
        self.assertIn(f"$beklenen = '{sha}'", ps1)
        self.assertIn("'192.168.1.10 avukat.uyap.gov.tr # cokluuyap-sira-kapisi'", ps1)
        self.assertEqual(sira_kapisi.ayar_oku()["kok_sha256"], sha)
        for ad in ("KUR.bat", "KALDIR.bat"):
            bat = _oku(os.path.join(klasor, ad)).decode("ascii")
            # Sistem araçları tam yolla; klasöre (cd) geçilmez.
            self.assertIn(r'"%SystemRoot%\System32\net.exe" session', bat)
            self.assertIn(r'"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"', bat)
            self.assertNotIn("cd /d", bat)
            self.assertNotIn("certutil", bat)
        ca = x509.load_der_x509_certificate(cer)
        omur = ca.not_valid_after_utc - ca.not_valid_before_utc
        self.assertLessEqual(omur.days, 826)                     # kök artık 10 yıl değil


class TestTls(unittest.TestCase):
    def test_sertifika_yalniz_uyap_icin_ve_tls_calisir(self):
        from cryptography import x509
        with tempfile.TemporaryDirectory() as d:
            eski = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = d
            try:
                ca_cer, crt, key, _ = sira_kapisi.sertifika_uret(os.path.join(d, "bot"))
                with open(ca_cer, "rb") as f:
                    ca = x509.load_der_x509_certificate(f.read())
                nc = ca.extensions.get_extension_for_class(x509.NameConstraints)
                self.assertTrue(nc.critical)
                self.assertEqual([n.value for n in nc.value.permitted_subtrees], ["avukat.uyap.gov.tr"])
                self.assertFalse(any(n.endswith(".key") for n in os.listdir(os.path.join(d, "bot"))))

                import uvicorn
                with socket.socket() as s:
                    s.bind(("127.0.0.1", 0))
                    port = s.getsockname()[1]
                app = sira_kapisi.uygulama_yap(
                    {"aralik_sn": 0, "izinli_ipler": ["127.0.0.1"]},
                    transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"uyap")),
                    gunluk=_SESSIZ)
                cfg = uvicorn.Config(app, host="127.0.0.1", port=port, ssl_certfile=crt,
                                     ssl_keyfile=key, ssl_keyfile_password=sira_kapisi.anahtar_parolasi(),
                                     log_level="error", lifespan="off")
                srv = uvicorn.Server(cfg)

                async def senaryo():
                    gorev = asyncio.create_task(srv.serve())
                    while not srv.started:
                        await asyncio.sleep(0.02)
                    ctx = ssl.create_default_context(cafile=None)
                    ctx.load_verify_locations(cadata=ca.public_bytes(
                        __import__("cryptography.hazmat.primitives.serialization",
                                   fromlist=["Encoding"]).Encoding.PEM).decode())
                    try:
                        async with httpx.AsyncClient(verify=ctx) as c:
                            r = await c.get(f"https://127.0.0.1:{port}/x",
                                            headers={"Host": "avukat.uyap.gov.tr"},
                                            extensions={"sni_hostname": "avukat.uyap.gov.tr"})
                            return r.status_code, r.content
                    finally:
                        srv.should_exit = True
                        await gorev

                self.assertEqual(asyncio.run(senaryo()), (200, b"uyap"))
            finally:
                if eski is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = eski


if __name__ == "__main__":
    unittest.main()
