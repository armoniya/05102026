"""gerekceli_karar testleri: python -m unittest uyap_core.test_gerekceli_karar (Uyap Haricen Giriş klasöründen).
UYAP'a gitmez; uyap_proxy.gw sahte bir istemciyle değiştirilir."""
import asyncio
import json
import os
import shutil
import tempfile
import time
import unittest
import zipfile

from uyap_core import evrak_toplu, gerekceli_karar as gk, uyap_proxy
from uyap_core.test_evrak_toplu import _Yanit, _udf

TURLER = [{"tablo": "0991", "kod": "Asliye Hukuk Mahkemesi"}, {"tablo": "0992", "kod": "İş Mahkemesi"}]
# (birimTuru2, dosyaDurumKod) -> kayıtlar
DOSYALAR = {
    ("0991", 0): [{"dosyaId": "D1", "dosyaNo": "2025/10", "birimAdi": "Ankara 1. Asliye Hukuk", "birimId": "B1",
                   "dosyaDurum": "Karara Çıkmış", "dosyaTur": "Esas", "dosyaAcilisTarihi": {"date": {"day": 3, "month": 2, "year": 2025}}},
                  {"dosyaId": "D2", "dosyaNo": "2025/11", "birimAdi": "Ankara 1. Asliye Hukuk", "birimId": "B1",
                   "dosyaDurum": "Açık"},
                  {"dosyaId": "D3", "dosyaNo": "2025/12", "birimAdi": "Ankara 1. Asliye Hukuk", "birimId": "B1"}],
    ("0991", 1): [{"dosyaId": "D4", "dosyaNo": "2023/5", "birimAdi": "Ankara 2. Asliye Hukuk", "birimId": "B2"}],
    ("0992", 1): [{"dosyaId": "D5", "dosyaNo": "2022/7", "birimAdi": "Ankara 3. İş", "birimId": "B3"}],
}
EVRAKLAR = {
    "D1": [{"evrakId": "K1", "tur": "Gerekçeli Karar", "birimEvrakNo": "55"},
           {"evrakId": "M1", "tur": "Gerekçeli Karar Tebliğ Mazbatası"},
           {"evrakId": "X1", "tur": "Dilekçe"}],
    "D2": [{"evrakId": "X2", "tur": "Dilekçe"}],
    "D4": [{"evrakId": "K4", "tur": "GEREKÇELİ KARAR"}],
    "D5": [{"evrakId": "K5", "tur": "Gerekçeli Karar", "birimEvrakNo": "9"}],
}
TARAFLAR = {
    "D1": [{"adi": "AHMET YILMAZ", "rol": "Davacı", "vekil": "[AYŞE KAYA]", "kisiKurum": "Kişi"},
           {"adi": "MEHMET DEMİR", "rol": "Davalı", "kisiKurum": "Kişi"},
           {"adi": "XYZ SİGORTA A.Ş.", "rol": "Davalı", "kisiKurum": "Kurum"},
           {"adi": "ALİ VELİ", "rol": "Fer'i Müdahil", "kisiKurum": "Kişi"}],
}


class _SahteUyap:
    def __init__(self):
        self.kayit = []
        self.client = self

    def touch(self):
        pass

    async def relogin(self):
        pass

    async def request(self, method, url, params=None, json=None, headers=None):
        await asyncio.sleep(0.005)
        yol = url.rsplit("/", 1)[-1]
        self.kayit.append((time.monotonic(), yol, params, json))
        if yol == gk.BIRIM_TURLERI:
            return _Yanit(govde=TURLER)
        if yol == gk.TARAF_BILGILERI:
            return _Yanit(govde=TARAFLAR.get(json["dosyaId"], []))
        if yol == gk.ARAMA:
            tum = DOSYALAR.get((json["birimTuru2"], json["dosyaDurumKod"]), [])
            n, s = json["pageSize"], json["pageNumber"]
            return _Yanit(govde=[tum[(s - 1) * n:s * n], len(tum)])
        if yol == evrak_toplu.EVRAK_LISTESI_TOPLAM:
            return _Yanit(govde=1)
        if yol == evrak_toplu.EVRAK_LISTESI:
            return _Yanit(govde={"tumEvraklar": {"g": [dict(e) for e in EVRAKLAR.get(json["dosyaId"], [])]}})
        if yol == evrak_toplu.DOC_VIEWER_HAZIRLIK:
            return _Yanit(govde={})
        if yol == evrak_toplu.EVRAK_ICERIK:
            return _Yanit(govde=_udf("Gerekçeli karar " + params["evrakId"]), ctype="application/octet-stream")
        return _Yanit(404, {"error": "yok"})


class SuzgecTest(unittest.TestCase):
    def test_gerekceli_mi(self):
        for tur in ("Gerekçeli Karar", "GEREKÇELİ KARAR", "gerekceli karar (ek)", "İstinaf Gerekçeli Karar"):
            self.assertTrue(gk.gerekceli_mi(tur), tur)
        for tur in ("Gerekçeli Karar Tebliğ Mazbatası", "Gerekçeli Karar Zarfı", "Kısa Karar",
                    "Karar", "Gerekçeli Karar Şerhi", None, ""):
            self.assertFalse(gk.gerekceli_mi(tur), tur)


class AkisTest(unittest.TestCase):
    def setUp(self):
        self.kok = tempfile.mkdtemp()
        self._eski = (gk.KOK, gk.ISTEK_ARASI_SN, gk.MOLA_HER_DOSYA, gk.MOLA_SN, gk.SAYFA_BOYU,
                      gk._uyu, uyap_proxy.gw)
        gk.KOK = self.kok
        gk.ISTEK_ARASI_SN = 0.02
        gk.MOLA_HER_DOSYA = 2
        gk.MOLA_SN = 2
        gk.SAYFA_BOYU = 2                # D1-D3 iki sayfaya bölünsün
        self.molalar = []

        async def uyu(sn):
            self.molalar.append(sn)
        gk._uyu = uyu
        gk._KAYITLAR.clear()
        self.sahte = uyap_proxy.gw = _SahteUyap()

    def tearDown(self):
        (gk.KOK, gk.ISTEK_ARASI_SN, gk.MOLA_HER_DOSYA, gk.MOLA_SN, gk.SAYFA_BOYU,
         gk._uyu, uyap_proxy.gw) = self._eski
        shutil.rmtree(self.kok, ignore_errors=True)

    async def _bekle(self, t, durumlar):
        while t.durum not in durumlar:
            await asyncio.sleep(0.01)

    def _tam_tur(self, secilenler, indirilecek=None):
        """liste → kontrol → (indirilecek; None: kararı olan hepsi) indirme. (tarama, liste,
        kontrol sonrası sonuçlar, kontrol bitince yapılmış istekler) döner."""
        async def ana():
            t = gk.liste_baslat()
            await self._bekle(t, (gk.SECIM, gk.HATA))
            liste = t.liste_dict()
            gk.tarama_baslat(t, secilenler)
            await self._bekle(t, (gk.KARAR_SECIM, gk.HATA, gk.IPTAL))
            sonuclar = t.sonuclar_dict()
            kontrol_istekleri = [k[1] for k in self.sahte.kayit]
            gk.indir_baslat(t, [d["id"] for d in sonuclar["dosyalar"]] if indirilecek is None else indirilecek)
            await self._bekle(t, (gk.BITTI, gk.HATA, gk.IPTAL))
            return t, liste, sonuclar, kontrol_istekleri
        return asyncio.run(ana())

    def test_liste_kontrol_secerek_indirme_ve_tekrar(self):
        t, liste, sonuclar, kontrol_istekleri = self._tam_tur(["D1", "D2", "D4", "D5"], ["D1", "D5"])
        self.assertEqual(t.durum, gk.BITTI, t.mesaj)
        self.assertEqual([x["ad"] for x in liste["turler"]], ["Asliye Hukuk Mahkemesi", "İş Mahkemesi"])
        self.assertEqual(sorted(d["id"] for d in liste["dosyalar"]), ["D1", "D2", "D3", "D4", "D5"])
        durum = {d["id"]: (d["durum"], d["arama"]) for d in liste["dosyalar"]}
        # "Açık" aramasından gelen dosyada UYAP'ın kendi durumu gösterilir.
        self.assertEqual(durum["D1"], ("Karara Çıkmış", "Açık"))
        self.assertEqual(durum["D2"], ("Açık", "Açık"))
        self.assertEqual(durum["D4"], ("Kapalı", "Kapalı"))          # dosyaDurum yoksa aramanın adı
        aramalar = [k[3] for k in self.sahte.kayit if k[1] == gk.ARAMA]
        self.assertIn(2, [a["pageNumber"] for a in aramalar])          # sayfalama
        self.assertTrue(all(a["birimTuru3"] == "1" for a in aramalar))

        # Kontrol hiçbir şey indirmez; kararı olanlar seçim için listelenir (mazbata karar değil).
        self.assertNotIn(evrak_toplu.EVRAK_ICERIK, kontrol_istekleri)
        self.assertEqual([d["id"] for d in sonuclar["dosyalar"]], ["D1", "D4", "D5"])
        self.assertEqual((sonuclar["karar_yok"], sonuclar["kontrol_edilemedi"]), (1, 0))
        self.assertEqual(sonuclar["dosyalar"][0]["kararlar"], [{"ad": "Gerekçeli Karar (55)", "inmis": False}])

        # Yalnız seçilen dosyaların kararları indi (D4 seçilmedi) — UDF + PDF.
        icerik = [k[2]["evrakId"] for k in self.sahte.kayit if k[1] == evrak_toplu.EVRAK_ICERIK]
        self.assertEqual(icerik, ["K1", "K5"])
        self.assertEqual((t.bulunan, t.yeni), (3, 2))
        klasor = os.path.join(self.kok, "Ankara 1. Asliye Hukuk 2025-10")
        self.assertEqual(sorted(os.listdir(klasor)), ["Gerekçeli Karar (55).pdf", "Gerekçeli Karar (55).udf"])
        with open(os.path.join(klasor, "Gerekçeli Karar (55).pdf"), "rb") as f:
            self.assertEqual(f.read(4), b"%PDF")
        self.assertFalse(os.path.exists(os.path.join(self.kok, "Ankara 2. Asliye Hukuk 2023-5")))

        # Hız: ardışık iki UYAP isteği arasında en az ISTEK_ARASI_SN; kontrolde 2 dosyada bir mola.
        zamanlar = [k[0] for k in self.sahte.kayit]
        self.assertGreaterEqual(min(b - a for a, b in zip(zamanlar, zamanlar[1:])), gk.ISTEK_ARASI_SN * 0.9)
        self.assertEqual(self.molalar, [1, 1])

        # ZIP: Excel listesi + indirilen kararlar.
        veri, ad, tur = gk.dosya_oku(t)
        self.assertEqual(tur, "application/zip")
        with zipfile.ZipFile(t.cikti_yol) as z:
            adlar = z.namelist()
        self.assertIn("Ankara 3. İş 2022-7/Gerekçeli Karar (9).udf", adlar)
        self.assertEqual(len([a for a in adlar if not a.endswith(".xlsx")]), 4)
        self.assertIn(os.path.basename(t.excel_yol), adlar)

        # Excel: kontrol edilen her dosya bir satır; taraflar rolüne göre sütunlarda.
        import openpyxl
        veri, ad, tur = gk.dosya_oku(t, excel=True)
        self.assertTrue(ad.endswith(".xlsx"))
        self.assertEqual(tur, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        sayfa = openpyxl.load_workbook(t.excel_yol).active
        satirlar = [[c.value for c in r] for r in sayfa.iter_rows()]
        self.assertEqual(satirlar[0], gk.EXCEL_BASLIKLARI)
        self.assertTrue(sayfa["A1"].font.b)
        self.assertEqual(sayfa.freeze_panes, "A2")
        self.assertEqual(len(satirlar), 5)
        s = {r[2]: dict(zip(gk.EXCEL_BASLIKLARI, r)) for r in satirlar[1:]}
        d1 = s["2025/10"]
        self.assertEqual((d1["Birim"], d1["Dosya Türü"], d1["Dosya Durumu"], d1["Açılış Tarihi"]),
                         ("Ankara 1. Asliye Hukuk", "Esas", "Karara Çıkmış", "03.02.2025"))
        self.assertEqual(d1["Davacı"], "AHMET YILMAZ (Vekil: AYŞE KAYA)")
        self.assertEqual(d1["Davalı"], "MEHMET DEMİR; XYZ SİGORTA A.Ş.")
        self.assertEqual(d1["Diğer Taraflar"], "Fer'i Müdahil: ALİ VELİ")
        self.assertEqual(d1["Gerekçeli Karar"], "Var (1)")
        self.assertIn("Gerekçeli Karar (55).udf", d1["Karar Dosyaları"])
        self.assertEqual(s["2023/5"]["Gerekçeli Karar"], "Var (1) — indirilmedi")
        self.assertFalse(s["2023/5"]["Karar Dosyaları"])
        self.assertEqual((s["2025/11"]["Gerekçeli Karar"], s["2025/11"]["Dosya Durumu"]), ("Yok", "Açık"))
        with open(os.path.join(self.kok, "_durum.json"), encoding="utf-8") as f:
            kayit = json.load(f)
        self.assertTrue(kayit["B1|2025/11"]["karar_yok"])
        self.assertEqual(len(kayit["B1|2025/10"]["taraflar"]), 4)

        # Tekrar: inmiş karar "ofiste var" görünür ve yeniden indirilmez; kararı olmayan dosya
        # yeniden sorulmaz; taraflar önbellekten.
        self.sahte.kayit.clear()
        gk._KAYITLAR.clear()
        t2, _, sonuclar2, _ = self._tam_tur(["D1", "D2"])
        self.assertEqual(t2.durum, gk.BITTI, t2.mesaj)
        self.assertEqual(sonuclar2["dosyalar"][0]["kararlar"], [{"ad": "Gerekçeli Karar (55)", "inmis": True}])
        yollar = [k[1] for k in self.sahte.kayit]
        self.assertNotIn(evrak_toplu.EVRAK_ICERIK, yollar)
        listelenen = [k[3]["dosyaId"] for k in self.sahte.kayit if k[1] == evrak_toplu.EVRAK_LISTESI]
        self.assertEqual(listelenen, ["D1"])
        self.assertNotIn(gk.TARAF_BILGILERI, yollar)
        self.assertEqual((t2.atlanan, t2.yeni, t2.bulunan), (1, 0, 1))
        sayfa = openpyxl.load_workbook(t2.excel_yol).active
        self.assertEqual(sayfa["G2"].value, "AHMET YILMAZ (Vekil: AYŞE KAYA)")
        with zipfile.ZipFile(t2.cikti_yol) as z:     # ofisteki karar da ZIP'te
            self.assertIn("Ankara 1. Asliye Hukuk 2025-10/Gerekçeli Karar (55).udf", z.namelist())

    def test_indirmeden_bitir_yalniz_excel(self):
        async def ana():
            t = gk.liste_baslat()
            await self._bekle(t, (gk.SECIM, gk.HATA))
            with self.assertRaises(ValueError):
                gk.indir_baslat(t, [])                    # kontrol bitmeden indirme yok
            gk.tarama_baslat(t, ["D1", "D2"])
            await self._bekle(t, (gk.KARAR_SECIM, gk.HATA))
            with self.assertRaises(ValueError):
                gk.indir_baslat(t, ["D2"])                # kararı olmayan dosya seçilemez
            gk.indir_baslat(t, [])
            await self._bekle(t, (gk.BITTI, gk.HATA))
            return t
        t = asyncio.run(ana())
        self.assertEqual(t.durum, gk.BITTI, t.mesaj)
        self.assertNotIn(evrak_toplu.EVRAK_ICERIK, [k[1] for k in self.sahte.kayit])
        d = t.to_dict()
        self.assertEqual((d["dosya_hazir"], d["excel_hazir"]), (False, True))
        import openpyxl
        satirlar = list(openpyxl.load_workbook(t.excel_yol).active.iter_rows(values_only=True))
        self.assertEqual(len(satirlar), 3)
        self.assertEqual(satirlar[1][9], "Var (1) — indirilmedi")

    def test_yalniz_kapali_listeleme(self):
        async def ana():
            for kotu in ([], [2], ["1"], [True], "1"):
                with self.assertRaises(ValueError):
                    gk.liste_baslat(kotu)
            t = gk.liste_baslat([1])
            await self._bekle(t, (gk.SECIM, gk.HATA))
            return t
        t = asyncio.run(ana())
        self.assertEqual(t.durum, gk.SECIM, t.mesaj)
        self.assertEqual(t.to_dict()["durumlar"], ["Kapalı"])
        aramalar = [k[3] for k in self.sahte.kayit if k[1] == gk.ARAMA]
        self.assertEqual({a["dosyaDurumKod"] for a in aramalar}, {1})   # açıklar UYAP'a hiç sorulmadı
        self.assertEqual(sorted(d["id"] for d in t.liste_dict()["dosyalar"]), ["D4", "D5"])

    def test_secim_dogrulama_ve_vazgec(self):
        async def ana():
            t = gk.liste_baslat()
            self.assertIs(gk.liste_baslat(), t)          # aynı anda ikinci liste açılmaz
            await self._bekle(t, (gk.SECIM, gk.HATA))
            with self.assertRaises(ValueError):
                gk.tarama_baslat(t, ["YOK"])
            with self.assertRaises(ValueError):
                gk.tarama_baslat(t, [])
            gk.iptal(t)
            self.assertEqual(t.durum, gk.IPTAL)
            with self.assertRaises(ValueError):
                gk.tarama_baslat(t, ["D1"])
            self.assertIsNot(gk.liste_baslat(), t)       # vazgeçilen yerine yenisi
        asyncio.run(ana())


if __name__ == "__main__":
    unittest.main()
