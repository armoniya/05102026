"""İş Listesi köprüsüne kimlik verilmesi: python -m unittest uyap_core.test_is_listesi_kimlik
(Uyap Haricen Giriş klasöründen). Paylaşım ve Bağlantıyı Al, ön yüz ne olursa olsun kimliği vermeli."""
import argparse
import asyncio
import json
import unittest
from unittest import mock

from uyap_core import home_client, is_listesi, office_agent


class _Dur(Exception):
    """Girişin kimlik verdikten sonraki ağ/sunucu adımlarına geçmesini keser."""


class TestBaglantiKimligi(unittest.TestCase):
    def setUp(self):
        is_listesi.clear()
        self.addCleanup(is_listesi.clear)

    def test_adres_cevirimi(self):
        for signaling, taban in (("wss://www.cokluuyap.com/ws", "https://www.cokluuyap.com"),
                                 ("ws://127.0.0.1:9000/ws", "http://127.0.0.1:9000")):
            is_listesi.baglantidan_configure(signaling, "", "a@b", "p")
            self.assertEqual(is_listesi._cfg["base"], taban)

    def test_kimliksiz_kopru_uyarir(self):
        st, _, out = asyncio.run(is_listesi.handle("GET", is_listesi.PREFIX + "/ac", "", b""))
        self.assertEqual(st, 503)
        self.assertIn("giriş yapılmamış", json.loads(out)["error"])

    def _dogrula(self):
        self.assertEqual(is_listesi._cfg, {"base": "https://www.cokluuyap.com", "office_code": "",
                                           "username": "ahmet@kemal", "password": "p"})

    def test_paylasim_kimlik_verir(self):
        # UyapMasa'nın argümanları: office_code yok, kullanıcı adı ofisle birlikte.
        args = argparse.Namespace(signaling="wss://www.cokluuyap.com/ws", room="ahmet@kemal", user="ahmet@kemal",
                                  password="p")
        with mock.patch.object(office_agent._runtime, "dongu_icin_sifirla", side_effect=_Dur):
            with self.assertRaises(_Dur):
                asyncio.run(office_agent.run_office(args))
        self._dogrula()

    def test_baglanti_al_kimlik_verir(self):
        args = argparse.Namespace(signaling="wss://www.cokluuyap.com/ws", room="ahmet@kemal", password="p")
        with mock.patch.object(home_client, "Tunnel", side_effect=_Dur):
            with self.assertRaises(_Dur):
                asyncio.run(home_client.amain(args))
        self._dogrula()


if __name__ == "__main__":
    unittest.main()
