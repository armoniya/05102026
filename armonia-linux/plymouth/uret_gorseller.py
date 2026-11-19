"""Armonia Plymouth temasının PNG görsellerini üretir (font kullanmaz, lisans sorunu yok).
Renkler armonia_acilis_v2 görselinden alındı. Claude, 2026-11-14.
Kullanım: python -I uret_gorseller.py  (çıktı: ./armonia/*.png)
"""
import os
from PIL import Image, ImageDraw, ImageFilter

HEDEF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "armonia")
os.makedirs(HEDEF, exist_ok=True)

YESIL = (95, 122, 97, 255)       # #5F7A61
KART = (250, 248, 244, 255)      # #FAF8F4
KENAR = (216, 207, 192, 255)     # #D8CFC0
KOYU = (47, 47, 47, 255)
OLCEK = 4  # yumuşak kenar için büyük çizip küçült


def kaydet(img, ad, boyut):
    img.resize(boyut, Image.LANCZOS).save(os.path.join(HEDEF, ad))


def logo(n=112):
    s = n * OLCEK
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=YESIL)
    # Geometrik "A": iki bacak + ara çizgi (çokgen)
    w = s * 0.13  # bacak kalınlığı
    tepe = (s / 2, s * 0.18)
    sol = (s * 0.20, s * 0.82)
    sag = (s * 0.80, s * 0.82)
    d.polygon([(tepe[0] - w * 0.6, tepe[1]), (tepe[0] + w * 0.6, tepe[1]),
               (sag[0], sag[1]), (sag[0] - w * 1.2, sag[1]),
               (tepe[0], tepe[1] + w * 1.9),
               (sol[0] + w * 1.2, sol[1]), (sol[0], sol[1])], fill=(255, 255, 255, 255))
    # ara çizgi
    y = s * 0.60
    d.rectangle((s * 0.33, y, s * 0.67, y + w * 0.75), fill=(255, 255, 255, 255))
    kaydet(im, "logo.png", (n, n))


def kart(w=440, h=430):
    pay = 24
    s = OLCEK
    im = Image.new("RGBA", ((w + 2 * pay) * s, (h + 2 * pay) * s), (0, 0, 0, 0))
    golge = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(golge).rounded_rectangle(
        (pay * s, (pay + 6) * s, (pay + w) * s, (pay + h + 6) * s), radius=16 * s, fill=(60, 50, 40, 70))
    golge = golge.filter(ImageFilter.GaussianBlur(10 * s))
    im.alpha_composite(golge)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((pay * s, pay * s, (pay + w) * s, (pay + h) * s), radius=16 * s,
                        fill=KART, outline=KENAR, width=3 * s)
    kaydet(im, "kart.png", (w + 2 * pay, h + 2 * pay))


def kutu(w=360, h=46):
    s = OLCEK
    im = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((s, s, w * s - s, h * s - s), radius=9 * s,
                                         fill=(255, 255, 255, 255), outline=YESIL, width=2 * s)
    kaydet(im, "kutu.png", (w, h))


def nokta(n=12):
    s = n * OLCEK
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((0, 0, s - 1, s - 1), fill=KOYU)
    kaydet(im, "nokta.png", (n, n))


if __name__ == "__main__":
    logo(); kart(); kutu(); nokta()
    print("Üretildi:", sorted(os.listdir(HEDEF)))
