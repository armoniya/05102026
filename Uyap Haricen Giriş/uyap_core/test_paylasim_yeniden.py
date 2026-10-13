"""Paylaş → Durdur → Paylaş (her biri YENİ event loop'ta, masaüstündeki gibi) birim testi:
python -m unittest uyap_core.test_paylasim_yeniden (Uyap Haricen Giriş klasöründen).

13.10.2026 light canlı denemesi: durdurulan paylaşımın 8843 (LAN TLS) soketi açık kalıyor,
sonraki paylaşım WinError 10048 ile düşüyordu; yarıda kesilen girişin kilidi yeni döngüde
"bound to a different event loop" veriyordu. UYAP'a gidilmez (sahte oturum, aynı kilitler)."""
import asyncio
import os
import shutil
import socket
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

import httpx

from uyap_core import _runtime, lan_tls, office_agent, uyap_proxy

HTTP_PORT, TLS_PORT = 18800, 18843


class _SahteOturum:
    def __init__(self, sure):
        self.lock = asyncio.Lock()
        self.client = httpx.AsyncClient()
        self.cert_id = None
        self.sure = sure

    async def ensure_alive(self):
        async with self.lock:
            async with _runtime.UYAP_WRITE_LOCK:
                await asyncio.sleep(self.sure)

    async def _probe(self):
        return True

    def touch(self):
        pass


def _port_bos(host, port):
    s = socket.socket()
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


class TestPaylasimYenidenBaslat(unittest.TestCase):
    def setUp(self):
        self.dizin = tempfile.mkdtemp()
        self.giris_suresi = 3.0
        self.hatalar = []
        sig = asyncio.Event
        yamalar = [
            mock.patch.object(lan_tls, "_dizin", lambda: self.dizin),
            mock.patch.object(lan_tls, "LAN_TLS_PORT", TLS_PORT),
            mock.patch.dict(os.environ, {"UYAP_LAN_SHARE": "1"}),
            mock.patch.object(uyap_proxy, "configure",
                              lambda **kw: setattr(uyap_proxy, "gw", _SahteOturum(self.giris_suresi))),
            mock.patch.object(office_agent, "run_signaling", lambda a, i: sig().wait()),
            mock.patch.object(office_agent, "fetch_ice_servers", self._ice),
        ]
        for y in yamalar:
            y.start()
            self.addCleanup(y.stop)
        self.addCleanup(shutil.rmtree, self.dizin, True)
        self.addCleanup(setattr, uyap_proxy, "gw", uyap_proxy.gw)

    @staticmethod
    async def _ice(_):
        return []

    def _paylas_ve_durdur(self, bekle):
        loop = asyncio.new_event_loop()
        kutu = {}
        args = types.SimpleNamespace(host="127.0.0.2", proxy_port=HTTP_PORT, interval=5,
                                     signaling="wss://x/ws", room="r", pin=None, cert_id=None,
                                     no_verify=False, user="u", password="p")

        def calis():
            asyncio.set_event_loop(loop)
            kutu["t"] = loop.create_task(office_agent.run_office(args))
            try:
                loop.run_until_complete(kutu["t"])
            except asyncio.CancelledError:
                pass
            except BaseException as e:  # uvicorn bağlanamazsa SystemExit/OSError
                self.hatalar.append(repr(e))
            finally:
                loop.close()

        th = threading.Thread(target=calis, daemon=True)
        th.start()
        time.sleep(bekle)
        loop.call_soon_threadsafe(kutu["t"].cancel)  # = "Paylaşımı Durdur"
        th.join(15)
        self.assertFalse(th.is_alive(), "paylaşım iş parçacığı kapanmadı")

    def test_durdur_portlari_bosaltir_ve_yeniden_paylasim_calisir(self):
        self._paylas_ve_durdur(1.0)  # giriş sürerken (kilit tutulurken) durdur
        self.assertTrue(_port_bos("127.0.0.1", HTTP_PORT))
        self.assertTrue(_port_bos("127.0.0.2", TLS_PORT))
        self.giris_suresi = 0.2
        eski_gw = uyap_proxy.gw
        self._paylas_ve_durdur(2.0)
        self.assertIsNot(uyap_proxy.gw, eski_gw, "yeni döngüde oturum yeniden kurulmalı")
        self.assertTrue(_port_bos("127.0.0.2", TLS_PORT))
        self.assertEqual(self.hatalar, [])


if __name__ == "__main__":
    unittest.main()
