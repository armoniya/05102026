"""_ek_zip_pdf_ac testleri: python -m unittest uyap_core.test_ek_zip_pdf (Uyap Haricen Giriş klasöründen).
UYAP'a gitmez; zipler testte sentetik üretilir (gerçek evrak depoya girmez)."""
import io
import zipfile
import unittest

from pypdf import PdfReader, PdfWriter

from uyap_core.uyap_proxy import _ek_zip_pdf_ac, _ek_pdf_basliklari

YOL = "view_document_brd.uyap"
ZIP = "application/zip"


def _pdf(sayfa=1, genislik=200):
    w = PdfWriter()
    for _ in range(sayfa):
        w.add_blank_page(width=genislik, height=200)
    b = io.BytesIO()
    w.write(b)
    return b.getvalue()


def _zip(**dosyalar):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        for ad, veri in dosyalar.items():
            z.writestr(ad, veri)
    return b.getvalue()


class EkZipPdfTest(unittest.TestCase):
    def test_eyp_tek_pdf(self):
        pdf = _pdf()
        ct, govde = _ek_zip_pdf_ac(YOL, ZIP, _zip(**{"ana_evrak.pdf": pdf}))
        self.assertEqual((ct, govde), ("application/pdf", pdf))

    def test_eml_html_ve_pdf(self):
        pdf = _pdf()
        zip_ = _zip(**{"ana_evrak.html": b"<html>PTT KEPHS</html>", "(ek evrak 1)ustyazi.pdf.pdf": pdf})
        self.assertEqual(_ek_zip_pdf_ac(YOL, "application/zip;charset=UTF-8", zip_), ("application/pdf", pdf))

    def test_ayni_pdf_iki_kez_tek_sayilir(self):
        pdf = _pdf()
        ct, govde = _ek_zip_pdf_ac(YOL, ZIP, _zip(**{"a.pdf": pdf, "b.PDF": pdf}))
        self.assertEqual(govde, pdf)

    def test_farkli_pdfler_birlesir(self):
        zip_ = _zip(**{"a.pdf": _pdf(1, 200), "b.pdf": _pdf(2, 300)})
        ct, govde = _ek_zip_pdf_ac(YOL, ZIP, zip_)
        self.assertEqual(ct, "application/pdf")
        self.assertEqual(len(PdfReader(io.BytesIO(govde)).pages), 3)

    def test_pdfsiz_zip_degismez(self):
        zip_ = _zip(**{"ana_evrak.html": b"<html></html>"})
        self.assertEqual(_ek_zip_pdf_ac(YOL, ZIP, zip_), (ZIP, zip_))

    def test_pdf_uzantili_ama_pdf_olmayan_atlanir(self):
        zip_ = _zip(**{"sahte.pdf": b"pdf degil"})
        self.assertEqual(_ek_zip_pdf_ac(YOL, ZIP, zip_), (ZIP, zip_))

    def test_bozuk_zip_degismez(self):
        bozuk = b"PK\x03\x04bozuk"
        self.assertEqual(_ek_zip_pdf_ac(YOL, ZIP, bozuk), (ZIP, bozuk))

    def test_baska_yol_ve_tur_dokunulmaz(self):
        zip_ = _zip(**{"ana_evrak.pdf": _pdf()})
        self.assertEqual(_ek_zip_pdf_ac("download_document_brd.uyap", ZIP, zip_), (ZIP, zip_))
        self.assertEqual(_ek_zip_pdf_ac(YOL, "application/pdf", zip_), ("application/pdf", zip_))

    def test_baslik_degisimi(self):
        out = _ek_pdf_basliklari({"Content-Type": ZIP, "X-A": "1"}, "application/pdf")
        self.assertEqual(out, {"X-A": "1", "content-type": "application/pdf"})


if __name__ == "__main__":
    unittest.main()
