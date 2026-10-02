"""yerel_kapi birim testleri: python -m unittest uyap_core.test_yerel_kapi (Uyap Haricen Giriş klasöründen)."""
import unittest

from uyap_core.yerel_kapi import hostname_of, tarayici_reddi

YOK = set()


class TestHostname(unittest.TestCase):
    def test_bicimler(self):
        self.assertEqual(hostname_of("http://127.0.0.1:8800"), "127.0.0.1")
        self.assertEqual(hostname_of("127.0.0.1:8800"), "127.0.0.1")
        self.assertEqual(hostname_of("[::1]:8800"), "::1")
        self.assertEqual(hostname_of("https://kotu.example.com/yol"), "kotu.example.com")
        self.assertEqual(hostname_of(""), "")


class TestTarayiciReddi(unittest.TestCase):
    def r(self, headers, method="GET"):
        return tarayici_reddi(headers, method, ek_adlar=YOK)

    # — meşru istemciler geçmeli —
    def test_python_istemcisi(self):
        self.assertIsNone(self.r({"Host": "127.0.0.1:8800"}, "POST"))

    def test_yerel_tarayici_ayni_kaynak(self):
        self.assertIsNone(self.r({"Host": "127.0.0.1:8800", "Origin": "http://127.0.0.1:8800",
                                  "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors"}, "POST"))

    def test_uyapmasa_dan_gecis(self):  # 127.0.0.1:8090 → 127.0.0.1:8800 aynı site
        self.assertIsNone(self.r({"Host": "127.0.0.1:8800", "Sec-Fetch-Site": "same-site",
                                  "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}))

    def test_baska_siteden_ust_duzey_gezinme(self):
        self.assertIsNone(self.r({"Host": "127.0.0.1:8800", "Sec-Fetch-Site": "cross-site",
                                  "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}))

    def test_localhost_ve_ipv6(self):
        self.assertIsNone(self.r({"Host": "localhost:8800"}))
        self.assertIsNone(self.r({"Host": "[::1]:8800", "Origin": "http://[::1]:8800"}))

    def test_beyaz_liste(self):
        self.assertIsNone(tarayici_reddi({"Host": "ofis.local:8800"}, "GET", ek_adlar={"ofis.local"}))

    def test_kucuk_harfli_dict(self):
        self.assertIsNotNone(self.r({"host": "kotu.example.com"}))

    # — saldırılar reddedilmeli —
    def test_dns_rebinding(self):
        self.assertIsNotNone(self.r({"Host": "kotu.example.com:8800"}))

    def test_capraz_site_fetch(self):
        self.assertIsNotNone(self.r({"Host": "127.0.0.1:8800", "Sec-Fetch-Site": "cross-site",
                                     "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}))

    def test_capraz_site_form_post(self):
        self.assertIsNotNone(self.r({"Host": "127.0.0.1:8800", "Sec-Fetch-Site": "cross-site",
                                     "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}, "POST"))

    def test_yabanci_origin(self):
        self.assertIsNotNone(self.r({"Host": "127.0.0.1:8800", "Origin": "https://kotu.example.com"}, "POST"))

    def test_null_origin(self):
        self.assertIsNotNone(self.r({"Host": "127.0.0.1:8800", "Origin": "null"}, "POST"))


if __name__ == "__main__":
    unittest.main()
