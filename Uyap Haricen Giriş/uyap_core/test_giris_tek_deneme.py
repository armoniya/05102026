"""Başarısız girişte otomatik yeniden deneme olmadığının birim testi:
python -m unittest uyap_core.test_giris_tek_deneme (Uyap Haricen Giriş klasöründen).

02.11.2026: kart hatasında döngü ~10 sn'de bir UYAP'a ve imza istemcisine (PIN'le) gidiyordu.
UYAP'a ve karta gidilmez (sahte giriş, sahte taşıma)."""
import argparse
import asyncio
import unittest
from unittest import mock

import httpx

from uyap_core import uyap_proxy


class GirisTekDenemeTest(unittest.TestCase):
    def _oturum(self):
        return uyap_proxy.GatewaySession(argparse.Namespace(pin="0000", cert_id=None), verify_ssl=True)

    def test_basarisiz_giris_bir_kez_denenir_ve_uyap_istegi_kesilir(self):
        async def senaryo():
            gw = self._oturum()
            istekler = []

            def tasima(request):
                istekler.append(request)
                return httpx.Response(200)

            gw.client._transport = httpx.MockTransport(tasima)
            with mock.patch.object(uyap_proxy.uyap_login, "perform_login",
                                   side_effect=SystemExit(1)) as giris:
                for _ in range(3):
                    with self.assertRaises(RuntimeError):
                        await gw.ensure_alive()
                self.assertEqual(giris.call_count, 1)
            with self.assertRaises(httpx.HTTPError):
                await gw.client.get("https://avukat.uyap.gov.tr/x")
            self.assertEqual(istekler, [])
            await gw.client.aclose()

        asyncio.run(senaryo())

    def test_keepalive_basarisiz_giristen_sonra_biter(self):
        async def senaryo():
            gw = self._oturum()
            gw.last_request_ts = 0
            with mock.patch.object(uyap_proxy.uyap_login, "perform_login",
                                   side_effect=SystemExit(1)) as giris:
                await asyncio.wait_for(uyap_proxy._keepalive_loop(gw, 0.01), timeout=5)
                self.assertEqual(giris.call_count, 1)
            await gw.client.aclose()

        asyncio.run(senaryo())

    def test_yeni_oturum_kapiyi_acar(self):
        async def senaryo():
            eski, yeni = self._oturum(), self._oturum()
            eski.giris_hatasi = "kart"
            self.assertIsNone(yeni.giris_hatasi)
            await eski.client.aclose()
            await yeni.client.aclose()

        asyncio.run(senaryo())


if __name__ == "__main__":
    unittest.main()
