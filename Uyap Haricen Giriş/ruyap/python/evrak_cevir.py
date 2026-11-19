"""Çoklu UYAP — evrak dönüştürücü (Python/PyMuPDF; eski Python sürümünün evrak_toplu.py kodu).

Rust program (src/py_cevir.rs) bu betiği geçici klasöre yazıp çağırır; Python ya da PyMuPDF yoksa
Rust'taki dönüştürücü kullanılır. Belge bu bilgisayardan çıkmaz.

  cevir <tur> <girdi> <cikti>      tiff | jpeg | png | udf | html  →  PDF
  birlestir <cikti> <liste.txt>    liste.txt: her satırda bir PDF yolu (UTF-8)
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fitz  # PyMuPDF


def _html_metni(ham):
    for kod in ("utf-8", "cp1254"):
        try:
            metin = ham.decode(kod)
            break
        except UnicodeDecodeError:
            continue
    else:
        metin = ham.decode("utf-8", "replace")
    return re.sub(r"(?is)<script.*?</script>", "", metin)


def _html_pdf(ham):
    story = fitz.Story(html=_html_metni(ham))
    buf = io.BytesIO()
    yazici = fitz.DocumentWriter(buf)
    sayfa = fitz.paper_rect("a4")
    alan = sayfa + (40, 40, -40, -40)
    devam = 1
    while devam:
        aygit = yazici.begin_page(sayfa)
        devam, _ = story.place(alan)
        story.draw(aygit)
        yazici.end_page()
    yazici.close()
    return buf.getvalue()


def cevir(tur, ham):
    if tur in ("tiff", "jpeg", "png"):
        with fitz.open(stream=ham, filetype=tur) as gorsel:
            return gorsel.convert_to_pdf()
    if tur == "udf":
        import udf_pdf
        return udf_pdf.udf_pdf_uret(ham)
    if tur == "html":
        return _html_pdf(ham)
    raise ValueError("tanınmayan biçim: " + tur)


def birlestir(cikti, yollar):
    birlesik = fitz.open()
    try:
        for yol in yollar:
            try:
                with fitz.open(yol) as src:
                    birlesik.insert_pdf(src)
            except Exception as e:
                print(f"atlandı: {yol}: {e}", file=sys.stderr)
        if not birlesik.page_count:
            raise ValueError("sayfa yok")
        birlesik.save(cikti, garbage=3, deflate=True)
    finally:
        birlesik.close()


def main():
    a = sys.argv[1:]
    if a and a[0] == "dene":
        print("ok", fitz.VersionBind)
        return
    if len(a) == 4 and a[0] == "cevir":
        with open(a[2], "rb") as f:
            pdf = cevir(a[1], f.read())
        with open(a[3], "wb") as f:
            f.write(pdf)
        return
    if len(a) == 3 and a[0] == "birlestir":
        with open(a[2], encoding="utf-8") as f:
            yollar = [s.rstrip("\r\n") for s in f if s.strip()]
        birlestir(a[1], yollar)
        return
    raise SystemExit("kullanım: cevir <tur> <girdi> <cikti> | birlestir <cikti> <liste>")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
