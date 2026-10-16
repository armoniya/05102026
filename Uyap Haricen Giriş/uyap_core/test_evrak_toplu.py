"""evrak_toplu testleri: python -m unittest uyap_core.test_evrak_toplu (Uyap Haricen Giriş klasöründen).
UYAP'a gitmez; uyap_proxy.gw sahte bir istemciyle değiştirilir, evraklar sentetik üretilir."""
import asyncio
import io
import json
import os
import shutil
import tempfile
import time
import unittest
import zipfile

import fitz
import httpx

from uyap_core import evrak_toplu, uyap_proxy


def _pdf(metin="sayfa", sayfa=1):
    doc = fitz.open()
    for i in range(sayfa):
        doc.new_page().insert_text((72, 72), f"{metin} {i}")
    veri = doc.tobytes()
    doc.close()
    return veri


def _tiff(sayfa=2):
    from PIL import Image
    kareler = [Image.new("RGB", (60, 40), (i * 80, 10, 10)) for i in range(sayfa)]
    b = io.BytesIO()
    kareler[0].save(b, format="TIFF", save_all=True, append_images=kareler[1:])
    return b.getvalue()


def _udf(metin="Sayın Mahkeme, ğüşıöç"):
    xml = ('<?xml version="1.0" encoding="UTF-8"?><template format_id="1.8">'
           f'<content><![CDATA[{metin}]]></content>'
           '<properties><pageFormat leftMargin="42" rightMargin="42" topMargin="42" bottomMargin="42"/></properties>'
           '<elements resolver="hvl-default"><paragraph Alignment="0">'
           f'<content startOffset="0" length="{len(metin)}" family="Times New Roman" size="12"/>'
           '</paragraph></elements></template>')
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("content.xml", xml)
    return b.getvalue()


def _kep_zip(pdf):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("ana_evrak.pdf", pdf)
    return b.getvalue()


def _sayfa_sayisi(pdf):
    with fitz.open("pdf", pdf) as d:
        return d.page_count


class DonusturmeTest(unittest.TestCase):
    def test_pdf_aynen(self):
        p = _pdf()
        self.assertEqual(evrak_toplu._tur_bul("application/pdf", p), "pdf")
        self.assertEqual(evrak_toplu._pdfe_cevir("pdf", p), (p, None))

    def test_cok_sayfali_tiff(self):
        t = _tiff(3)
        self.assertEqual(evrak_toplu._tur_bul("image/tiff", t), "tiff")
        pdf, hata = evrak_toplu._pdfe_cevir("tiff", t)
        self.assertIsNone(hata)
        self.assertEqual(_sayfa_sayisi(pdf), 3)

    def test_udf(self):
        u = _udf()
        self.assertEqual(evrak_toplu._tur_bul("application/octet-stream", u), "udf")
        pdf, hata = evrak_toplu._pdfe_cevir("udf", u)
        self.assertIsNone(hata)
        with fitz.open("pdf", pdf) as d:
            self.assertIn("Mahkeme", d[0].get_text())

    def test_html_turkce(self):
        h = "<html><body><h3>Vekalet Pulu Makbuzu</h3><p>Tutar: 123,40 TL — ğüşıöç</p></body></html>"
        ham = h.encode("cp1254", "replace")
        self.assertEqual(evrak_toplu._tur_bul("text/html", ham), "html")
        pdf, hata = evrak_toplu._pdfe_cevir("html", ham)
        self.assertIsNone(hata)
        with fitz.open("pdf", pdf) as d:
            self.assertIn("Makbuzu", d[0].get_text())

    def test_kep_zip(self):
        p = _pdf("kep")
        z = _kep_zip(p)
        self.assertEqual(evrak_toplu._tur_bul("application/zip", z), "zip")
        self.assertEqual(evrak_toplu._pdfe_cevir("zip", z), (p, None))

    def test_bilinmeyen_isaret_sayfasi(self):
        pdf, hata = evrak_toplu._pdfe_cevir("bilinmiyor", b"\x00\x01")
        self.assertIsNone(pdf)
        self.assertTrue(hata)
        self.assertEqual(_sayfa_sayisi(evrak_toplu._isaret_sayfasi("001 - X", hata)), 1)


class _Yanit:
    def __init__(self, status=200, govde=b"", ctype="application/json"):
        self.status_code = status
        self.content = govde if isinstance(govde, bytes) else json.dumps(govde).encode()
        self.headers = {"content-type": ctype}


class _SahteUyap:
    """Sırayla kaydeder; eşzamanlılığı (aynı anda iki istek) yakalar."""
    def __init__(self, evraklar, captcha_evrak=None, eszamanli_bir_kez=True, kopan=None):
        self.kopan = dict(kopan or {})      # evrakId -> kaç kez SSL kopması (-1: hep)
        self.evraklar = evraklar            # evrakId -> (ctype, bayt)
        self.captcha_evrak = captcha_evrak
        self.eszamanli_bir_kez = eszamanli_bir_kez
        self.kayit = []                     # (zaman, yöntem, yol, params, json, headers)
        self.ucusta = 0
        self.en_cok_ucusta = 0
        self.client = self

    def touch(self):
        pass

    async def relogin(self):
        pass

    async def request(self, method, url, params=None, json=None, headers=None):
        self.ucusta += 1
        self.en_cok_ucusta = max(self.en_cok_ucusta, self.ucusta)
        try:
            await asyncio.sleep(0.01)
            yol = url.rsplit("/", 1)[-1]
            self.kayit.append((time.monotonic(), method, yol, params, json, headers or {}))
            if yol == evrak_toplu.EVRAK_LISTESI_TOPLAM:
                return _Yanit(govde=1)
            if yol == evrak_toplu.EVRAK_LISTESI:
                if self.eszamanli_bir_kez:
                    self.eszamanli_bir_kez = False
                    return _Yanit(govde={"error": "Eş zamanlı olarak birden fazla sorgulama yapamazsınız!"})
                liste = [{"evrakId": k, "tur": f"Evrak {k}", "birimEvrakNo": k} for k in self.evraklar]
                liste[0]["ekEvrakListesi"] = [{"evrakId": "EK1", "sira": 1}]
                return _Yanit(govde={"tumEvraklar": {"g": liste}})
            if yol == evrak_toplu.DOC_VIEWER_HAZIRLIK:
                return _Yanit(govde={})
            if yol == evrak_toplu.EVRAK_ICERIK:
                eid = params["evrakId"]
                if self.kopan.get(eid):
                    self.kopan[eid] -= 1
                    raise httpx.ConnectError("[SSL] record layer failure (_ssl.c:1032)")
                if eid == self.captcha_evrak and params.get("Recaptcha") != "JETON":
                    return _Yanit(govde={"errorCode": "PRTL_GNL_100063", "error": "Güvenlik doğrulaması gerekli"})
                if eid == "EK1":
                    return _Yanit(govde=_pdf("ek"), ctype="application/pdf")
                ctype, bayt = self.evraklar[eid]
                return _Yanit(govde=bayt, ctype=ctype)
            return _Yanit(404, {"error": "yok"})
        finally:
            self.ucusta -= 1


class AkisTest(unittest.TestCase):
    def setUp(self):
        self.kok = tempfile.mkdtemp()
        self._eski = (evrak_toplu.KAYIT_KOKU, evrak_toplu.ISTEK_ARASI_SN,
                      evrak_toplu.ESZAMANLI_BEKLEME_SN, uyap_proxy.gw, evrak_toplu.AG_BEKLEMELERI_SN,
                      evrak_toplu.YAVAS_EN_COK_SN)
        evrak_toplu.AG_BEKLEMELERI_SN = (0.01, 0.01, 0.01)
        evrak_toplu.YAVAS_EN_COK_SN = 0.1
        evrak_toplu.KAYIT_KOKU = self.kok
        evrak_toplu.ISTEK_ARASI_SN = 0.05
        evrak_toplu.ESZAMANLI_BEKLEME_SN = 0.01
        evrak_toplu._KAYITLAR.clear()

    def tearDown(self):
        (evrak_toplu.KAYIT_KOKU, evrak_toplu.ISTEK_ARASI_SN, evrak_toplu.ESZAMANLI_BEKLEME_SN,
         uyap_proxy.gw, evrak_toplu.AG_BEKLEMELERI_SN, evrak_toplu.YAVAS_EN_COK_SN) = self._eski
        shutil.rmtree(self.kok, ignore_errors=True)

    def _calistir(self, sahte, kullanici=None, **secenek):
        uyap_proxy.gw = sahte

        async def ana():
            ind = evrak_toplu.baslat("DOSYA1", "Ankara 1. Asliye Hukuk 2026/12 Esas", **secenek)
            while ind.durum not in (evrak_toplu.BITTI, evrak_toplu.HATA, evrak_toplu.IPTAL):
                if kullanici:
                    kullanici(ind)
                await asyncio.sleep(0.02)
            return ind
        return asyncio.run(ana())

    def test_tum_akis_captcha_ve_kurallar(self):
        sahte = _SahteUyap({
            "E1": ("application/pdf", _pdf("bir", 2)),
            "E2": ("image/tiff", _tiff(2)),
            "E3": ("application/octet-stream", _udf()),
            "E4": ("text/html", "<p>Makbuz ğ</p>".encode()),
            "E5": ("application/zip", _kep_zip(_pdf("kep"))),
            "E6": ("application/octet-stream", b"\x00garip"),
        }, captcha_evrak="E3")
        gorulen_captcha = []

        def kullanici(ind):                  # kullanıcı panelde doğrulamayı çözer
            if ind.durum == evrak_toplu.CAPTCHA:
                gorulen_captcha.append(ind.to_dict()["captcha"])
                ind.captcha_cevapla("JETON")

        ind = self._calistir(sahte, kullanici)
        self.assertEqual(ind.durum, evrak_toplu.BITTI, ind.mesaj)

        # Captcha kullanıcıya UYAP'ın site anahtarıyla gösterildi, jeton başlık+parametreyle gitti.
        self.assertEqual(len(gorulen_captcha), 1)
        self.assertEqual(gorulen_captcha[0]["site_key"], evrak_toplu.TURNSTILE_SITE_KEY)
        e3 = [k for k in sahte.kayit if k[2] == evrak_toplu.EVRAK_ICERIK and k[3]["evrakId"] == "E3"]
        self.assertEqual(len(e3), 2)
        self.assertEqual(e3[1][5].get("Recaptcha"), "JETON")

        # Hiçbir an iki istek birden yok; ardışık istekler arasında sabit asgari aralık.
        self.assertEqual(sahte.en_cok_ucusta, 1)
        zamanlar = [k[0] for k in sahte.kayit]
        araliklar = [b - a for a, b in zip(zamanlar, zamanlar[1:])]
        self.assertGreaterEqual(min(araliklar), evrak_toplu.ISTEK_ARASI_SN * 0.9)

        # Diske kayıt: 7 evrak (6 + 1 ek) PDF, birleşik, orijinaller, ZIP.
        self.assertTrue(os.path.isdir(ind.klasor))
        pdfler = sorted(f for f in os.listdir(ind.klasor) if f.endswith(".pdf"))
        self.assertEqual(len(pdfler), 8)     # 7 evrak + Birlesik
        self.assertTrue(any(f.endswith("Birlesik.pdf") for f in pdfler))
        orijinal = os.listdir(os.path.join(ind.klasor, "Orijinal"))
        self.assertEqual(len(orijinal), 5)   # tiff, udf, html, zip, bin
        birlesik = os.path.join(ind.klasor, [f for f in pdfler if f.endswith("Birlesik.pdf")][0])
        with fitz.open(birlesik) as d:
            # 2 (pdf) + 1 (ek) + 2 (tiff) + udf + html + kep + işaret sayfası
            self.assertGreaterEqual(d.page_count, 9)
        self.assertEqual(len(ind.uyarilar), 1)   # yalnız tanınmayan biçim

        veri, ad, tur = evrak_toplu.dosya_oku(ind)
        self.assertTrue(ad.endswith(".zip"))
        self.assertEqual(tur, "application/zip")
        with zipfile.ZipFile(io.BytesIO(veri)) as z:
            self.assertEqual(len([n for n in z.namelist() if n.endswith(".pdf")]), 8)

    def test_iptal_captcha_sirasinda(self):
        sahte = _SahteUyap({"E1": ("application/pdf", _pdf())}, captcha_evrak="E1",
                           eszamanli_bir_kez=False)

        def kullanici(ind):
            if ind.durum == evrak_toplu.CAPTCHA:
                ind.iptal_et()

        ind = self._calistir(sahte, kullanici)
        self.assertEqual(ind.durum, evrak_toplu.IPTAL)
        self.assertIsNone(ind.cikti_yol)

    def test_ssl_kopmasi_tekrar_denenir_ve_yavaslar(self):
        sahte = _SahteUyap({"E1": ("application/pdf", _pdf()), "E2": ("application/pdf", _pdf("iki"))},
                           eszamanli_bir_kez=False, kopan={"E2": 2})
        ind = self._calistir(sahte)
        self.assertEqual(ind.durum, evrak_toplu.BITTI, ind.mesaj)
        self.assertEqual(ind.uyarilar, [])
        self.assertGreater(ind.aralik(), evrak_toplu.ISTEK_ARASI_SN)   # kopmadan sonra yavaşladı
        self.assertLessEqual(ind.aralik(), evrak_toplu.YAVAS_EN_COK_SN)

    def test_kalici_kopma_uc_evrakta_durur_kismi_zip(self):
        evr = {f"E{i}": ("application/pdf", _pdf(str(i))) for i in range(1, 7)}
        sahte = _SahteUyap(evr, eszamanli_bir_kez=False, kopan={"E3": -1, "E4": -1, "E5": -1})
        ind = self._calistir(sahte)
        self.assertEqual(ind.durum, evrak_toplu.HATA)
        self.assertIn("art arda 3", ind.mesaj)
        self.assertTrue(ind.to_dict()["dosya_hazir"])           # alınabilenler yine de ZIP'te
        e6 = [k for k in sahte.kayit if k[2] == evrak_toplu.EVRAK_ICERIK and k[3]["evrakId"] == "E6"]
        self.assertEqual(e6, [])                              # durduktan sonra istek yok

    def test_tek_pdf(self):
        sahte = _SahteUyap({"E1": ("application/pdf", _pdf("bir", 2)),
                            "E2": ("image/tiff", _tiff(2)),
                            "E3": ("application/octet-stream", b"\x00garip")}, eszamanli_bir_kez=False)
        ind = self._calistir(sahte, cikti=evrak_toplu.CIKTI_TEK)
        self.assertEqual(ind.durum, evrak_toplu.BITTI, ind.mesaj)
        veri, ad, tur = evrak_toplu.dosya_oku(ind)
        self.assertEqual(tur, "application/pdf")
        self.assertTrue(ad.endswith("Birlesik.pdf"))
        # 2 + 1 (ek) + 2 (tiff) + işaret sayfası
        self.assertEqual(_sayfa_sayisi(veri), 6)
        # Kayıtta yalnız birleşik PDF (+ dönüştürülemeyenlerin orijinali) kalır, ZIP yok.
        self.assertEqual(sorted(os.listdir(ind.klasor)), sorted([ad, "Orijinal"]))
        self.assertFalse(os.path.exists(ind.klasor + ".zip"))

    def test_secili_belgeler(self):
        evr = {f"E{i}": ("application/pdf", _pdf(str(i))) for i in range(1, 5)}
        sahte = _SahteUyap(evr, eszamanli_bir_kez=False)
        gorulen = []

        def kullanici(ind):
            if ind.durum == evrak_toplu.SECIM:
                liste = ind.to_dict()["liste"]
                gorulen.append(liste)
                ind.secim_yap([1, 3, 99])         # E1'in eki ve E3; 99 yok sayılır

        ind = self._calistir(sahte, kullanici, kapsam=evrak_toplu.KAPSAM_SECILI)
        self.assertEqual(ind.durum, evrak_toplu.BITTI, ind.mesaj)
        self.assertEqual(len(gorulen), 1)
        liste = gorulen[0]
        self.assertEqual(len(liste), 5)           # 4 evrak + 1 ek
        self.assertTrue(liste[1]["ek"])
        # Yalnız seçilenler istendi; dosya adı tam listedeki sırayı korur.
        istenen = [k[3]["evrakId"] for k in sahte.kayit if k[2] == evrak_toplu.EVRAK_ICERIK]
        self.assertEqual(istenen, ["EK1", "E3"])
        dosyalar = sorted(os.listdir(ind.klasor))
        self.assertTrue(dosyalar[0].startswith("002 - "), dosyalar)
        self.assertTrue(dosyalar[1].startswith("004 - "), dosyalar)
        self.assertIn("Secili", ind.ad)

    def test_secimde_vazgec(self):
        sahte = _SahteUyap({"E1": ("application/pdf", _pdf())}, eszamanli_bir_kez=False)

        def kullanici(ind):
            if ind.durum == evrak_toplu.SECIM:
                ind.iptal_et()

        ind = self._calistir(sahte, kullanici, kapsam=evrak_toplu.KAPSAM_SECILI)
        self.assertEqual(ind.durum, evrak_toplu.IPTAL)
        self.assertEqual([k for k in sahte.kayit if k[2] == evrak_toplu.EVRAK_ICERIK], [])

    def test_secimde_kilit_serbest(self):
        """Seçim beklenirken başka dosyanın indirmesi bekletilmez."""
        uyap_proxy.gw = _SahteUyap({"E1": ("application/pdf", _pdf())}, eszamanli_bir_kez=False)

        async def ana():
            a = evrak_toplu.baslat("A", "A dosyasi", kapsam=evrak_toplu.KAPSAM_SECILI)
            while a.durum != evrak_toplu.SECIM:
                await asyncio.sleep(0.02)
            b = evrak_toplu.baslat("B", "B dosyasi")
            for _ in range(500):
                if b.durum in (evrak_toplu.BITTI, evrak_toplu.HATA):
                    break
                await asyncio.sleep(0.02)
            durum_b, durum_a = b.durum, a.durum
            a.iptal_et()
            await asyncio.sleep(0.05)
            return durum_a, durum_b
        durum_a, durum_b = asyncio.run(ana())
        self.assertEqual(durum_a, evrak_toplu.SECIM)
        self.assertEqual(durum_b, evrak_toplu.BITTI)

    def test_gecersiz_secenek(self):
        with self.assertRaises(ValueError):
            evrak_toplu.baslat("D", "x", cikti="hepsi")

    def test_gecersiz_dosya_id(self):
        with self.assertRaises(ValueError):
            evrak_toplu.baslat("a\nb", "x")


if __name__ == "__main__":
    unittest.main()
