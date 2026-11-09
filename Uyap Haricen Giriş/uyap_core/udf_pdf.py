# -*- coding: utf-8 -*-
"""
UDF -> PDF dönüştürücü
========================
`UDF Converter GUI/converter.py`'nin TERSİ (kullanıcı isteği, 2026-08-27).

UDF dosyası bir ZIP'tir, içinde `content.xml` taşır:

  <template format_id="1.8">
    <content><![CDATA[TÜM belge metni, TEK blok]]></content>
    <properties><pageFormat leftMargin rightMargin topMargin bottomMargin
                            paperOrientation headerFOffset footerFOffset /></properties>
    <elements resolver="hvl-default">
      <header startPage=".." foreground=".."><paragraph>..</paragraph></header>
      <paragraph Alignment="0|1|2|3" LeftIndent RightIndent FirstLineIndent
                 SpaceAbove SpaceBelow LineSpacing TabSet="pos:hiza:dolgu,.."
                 Bulleted/Numbered NumberType ListId resolver="stil">
        <content|field|space|tab startOffset length family size bold italic
                 underline foreground resolver />
        <image imageData="base64" width height />
      </paragraph>
      <table columnSpans="a,b,.." border="borderCell|borderNone|borderTable">
        <row columnSpans=".." height=".."><cell><paragraph/>|<table/>..</cell></row>
      </table>
      <footer pageNumber-spec=".." pageNumber-fontFace ..><paragraph/></footer>
    </elements>
    <styles><style name=".." resolver=".." family size bold .. /></styles>
  </template>

`startOffset`/`length` TÜM belge boyunca GLOBAL'dir. Şablondan üretilen
evraklarda (duruşma tutanağı vb.) asıl veriler (dosya no, tarih, hâkim adları)
`<field>` parçalarında, kelime araları `<space>` parçalarındadır — bunlar
ATLANIRSA belge boş satırlarla basılır (2026-11-09 hatası).

Yerleşim UYAP editörünün (Java Swing tabanlı) davranışına göre yapılır:
  • Paragraf içindeki bütün parçalar AYNI satır akışında, ölçülen gerçek glif
    genişlikleriyle (fitz.Font.text_length) sözcük sözcük sarılır.
  • Sekme durakları TabSet'ten (hiza: 0 sol, 1 sağ, 2 orta, 4 ondalık);
    son duraktan sonraki her sekme +5pt, TabSet yoksa 72pt ızgara (Swing
    ParagraphView.nextTabStop ile aynı).
  • Stil zinciri: parça → resolver stilleri → paragraf → paragraf stilleri.
  • Tablolar (iç içe dahil) sütun oranlarıyla; uzun satırlar sayfa arasında
    bölünür. Üst bilgi startPage'den itibaren basılır.

Piksel-birebir UYAP dökümü DEĞİLDİR (satır yüksekliği/yazı tipi eksikleri),
ama hiçbir içerik atlanmaz ya da uydurulmaz. Sayfa numarası biçimi
(`pageNumber-spec="BSP32_n"`) belgelenmemiştir; bit anlamları TAHMİNDİR
(32 orta, 64 sağ, 2048 "n/N").

Yazı tipleri `C:\\Windows\\Fonts` altındaki gerçek TTF'lerden yüklenir
(Base-14 Türkçe ı/İ/ğ/ş içermez). Bulunamazsa MuPDF'in yerleşik fontlarına
düşülür.
"""

import base64
import io
import os
import re
import xml.etree.ElementTree as ET
import zipfile

import fitz  # PyMuPDF

try:
    _mupdf = fitz.mupdf
    _DIL_YOK = _mupdf.fz_text_language_from_string(None)
except Exception:  # eski PyMuPDF: _Cizici.yaz TextWriter.append'e düşer
    _mupdf = None
    _DIL_YOK = None

_A4_W, _A4_H = 595.2756, 841.8898
_VARSAYILAN_MARJ = 42.525
_HUCRE_PAD_X, _HUCRE_PAD_Y = 2.0, 1.0

_WINFONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")

# aile -> (normal, kalın, italik, kalın-italik) TTF dosya adları
_AILE_TTF = {
    "times new roman": ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"),
    "arial": ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    "arial narrow": ("arialn.ttf", "arialnb.ttf", "arialni.ttf", "arialnbi.ttf"),
    "arial black": ("ariblk.ttf", "ariblk.ttf", "ariblk.ttf", "ariblk.ttf"),
    "calibri": ("calibri.ttf", "calibrib.ttf", "calibrii.ttf", "calibriz.ttf"),
    "cambria": ("cambria.ttf", "cambriab.ttf", "cambriai.ttf", "cambriaz.ttf"),
    "candara": ("candara.ttf", "candarab.ttf", "candarai.ttf", "candaraz.ttf"),
    "tahoma": ("tahoma.ttf", "tahomabd.ttf", "tahoma.ttf", "tahomabd.ttf"),
    "verdana": ("verdana.ttf", "verdanab.ttf", "verdanai.ttf", "verdanaz.ttf"),
    "trebuchet ms": ("trebuc.ttf", "trebucbd.ttf", "trebucit.ttf", "trebucbi.ttf"),
    "segoe ui": ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf"),
    "courier new": ("cour.ttf", "courbd.ttf", "couri.ttf", "courbi.ttf"),
    "georgia": ("georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf"),
    "bookman old style": ("bookos.ttf", "bookosb.ttf", "bookosi.ttf", "bookosbi.ttf"),
    "century gothic": ("gothic.ttf", "gothicb.ttf", "gothici.ttf", "gothicbi.ttf"),
    "century": ("century.ttf", "century.ttf", "century.ttf", "century.ttf"),
    "garamond": ("gara.ttf", "garabd.ttf", "garait.ttf", "garabd.ttf"),
    "monotype corsiva": ("mtcorsva.ttf",) * 4,
    "segoe script": ("segoesc.ttf", "segoescb.ttf", "segoesc.ttf", "segoescb.ttf"),
    "brush script mt": ("brushsci.ttf",) * 4,
    "vladimir script": ("vladimir.ttf",) * 4,
    "rage italic": ("rage.ttf",) * 4,
    "harlow solid italic": ("harlowsi.ttf",) * 4,
}
# Java mantıksal/yaygın olmayan aileler -> sırayla denenecek yedekler
_AILE_YEDEK = {
    "dialog": ["arial"], "sansserif": ["arial"], "dialoginput": ["courier new"],
    "serif": ["times new roman"], "monospaced": ["courier new"],
    ".applesystemuifont": ["arial"], "editor": ["times new roman"],
    "coronetps": ["monotype corsiva", "segoe script"],
    "source serif pro": ["times new roman"], "albertus medium": ["times new roman"],
}
_BASE14 = {  # TTF hiç bulunamazsa (Windows dışı) MuPDF yerleşikleri
    "sans": ("helv", "hebo", "heit", "hebi"),
    "serif": ("tiro", "tibo", "tiit", "tibi"),
    "mono": ("cour", "cobo", "coit", "cobi"),
}


# ---------------------------------------------------------------- yardımcılar

def _f(deger, varsayilan=0.0):
    try:
        return float(deger)
    except (TypeError, ValueError):
        return varsayilan


def _argb_rgb(argb):
    try:
        v = int(argb)
    except (TypeError, ValueError):
        return None
    if v < 0:
        v += 1 << 32
    return (((v >> 16) & 255) / 255.0, ((v >> 8) & 255) / 255.0, (v & 255) / 255.0)


def _oranlar(metin):
    try:
        l = [float(x) for x in (metin or "").split(",") if x.strip()]
    except ValueError:
        return []
    return l if l and sum(l) > 0 else []


_FONT_DOSYA = {}   # yol -> fitz.Font (süreç boyunca; TTF her belgede yeniden yüklenmez)
_GLIF_GEN = {}     # id(font) -> {karakter: ilerleme (1pt için)}


class _Fontlar:
    def __init__(self):
        self._dosya = _FONT_DOSYA
        self._coz = {}

    def _yukle(self, yol):
        if yol not in self._dosya:
            try:
                self._dosya[yol] = fitz.Font(fontfile=yol)
            except Exception:
                self._dosya[yol] = None
        return self._dosya[yol]

    def al(self, aile, kalin, egik):
        anahtar = ((aile or "").strip().lower(), bool(kalin), bool(egik))
        if anahtar in self._coz:
            return self._coz[anahtar]
        ad, kalin, egik = anahtar
        idx = (2 if egik else 0) + (1 if kalin else 0)
        adaylar = [ad] + _AILE_YEDEK.get(ad, []) + ["times new roman"]
        font = None
        for a in adaylar:
            dosyalar = _AILE_TTF.get(a)
            if not dosyalar:
                continue
            for d in (dosyalar[idx], dosyalar[0]):
                yol = os.path.join(_WINFONTS, d)
                if os.path.isfile(yol):
                    font = self._yukle(yol)
                    if font is not None:
                        break
            if font is not None:
                break
        if font is None:
            grup = "mono" if "cour" in ad or "mono" in ad else (
                "sans" if any(k in ad for k in ("arial", "dialog", "sans", "tahoma", "verdana", "calibri")) else "serif")
            ad14 = _BASE14[grup][idx]
            font = self._dosya.get("base14:" + ad14)
            if font is None:
                font = self._dosya["base14:" + ad14] = fitz.Font(ad14)
        self._coz[anahtar] = font
        return font


class _Stiller:
    def __init__(self, root):
        self.s = {}
        for st in root.findall("styles/style"):
            if st.get("name"):
                self.s[st.get("name")] = st.attrib

    def zincir(self, ad):
        out, gorulen = [], set()
        while ad and ad in self.s and ad not in gorulen:
            gorulen.add(ad)
            out.append(self.s[ad])
            ad = self.s[ad].get("resolver")
        return out


class _Baglam:
    def __init__(self, root, full_text):
        self.metin = full_text
        self.stiller = _Stiller(root)
        self.fontlar = _Fontlar()
        self.varsayilan_renk = (0.0, 0.0, 0.0)
        self.liste_sayac = {}

    def dilim(self, el):
        try:
            s, n = int(el.get("startOffset", "0")), int(el.get("length", "0"))
        except ValueError:
            return ""
        return self.metin[s:s + n]

    def par_zincir(self, par):
        return [par.attrib] + self.stiller.zincir(par.get("resolver"))

    def par_attr(self, par, ad, varsayilan=None):
        for d in self.par_zincir(par):
            if ad in d:
                return d[ad]
        for c in par:  # bazı belgelerde paragraf öznitelikleri yalnız parçalarda
            if ad in c.attrib:
                return c.get(ad)
            break
        return varsayilan

    def stil(self, run, par):
        zincir = [run.attrib] + self.stiller.zincir(run.get("resolver")) + self.par_zincir(par) \
            + self.stiller.zincir("hvl-default")

        def bul(ad, vars_):
            for d in zincir:
                if ad in d and d[ad] != "":
                    return d[ad]
            return vars_

        aile = bul("family", "Times New Roman")
        boyut = _f(bul("size", "12"), 12.0)
        if boyut <= 0:
            boyut = 12.0
        kalin = bul("bold", "false") == "true"
        egik = bul("italic", "false") == "true"
        renk = None
        fg = run.get("foreground")
        if fg is None:
            for d in zincir[1:]:
                if d.get("foreground") and d is not self.stiller.s.get("default"):
                    fg = d.get("foreground")
                    break
        if fg is not None:
            renk = _argb_rgb(fg)
        return _Stil(self.fontlar.al(aile, kalin, egik), boyut, renk or self.varsayilan_renk,
                     bul("underline", "false") == "true")


class _Stil:
    __slots__ = ("font", "boyut", "renk", "alti_cizili")

    def __init__(self, font, boyut, renk, alti_cizili):
        self.font, self.boyut, self.renk, self.alti_cizili = font, boyut, renk, alti_cizili

    def gen(self, metin):
        tablo = _GLIF_GEN.get(id(self.font))
        if tablo is None:
            tablo = _GLIF_GEN[id(self.font)] = {}
        toplam = 0.0
        for ch in metin:
            g = tablo.get(ch)
            if g is None:
                try:
                    g = self.font.glyph_advance(ord(ch))
                except Exception:
                    g = 0.5
                tablo[ch] = g
            toplam += g
        return toplam * self.boyut

    @property
    def ust(self):
        return self.font.ascender * self.boyut

    @property
    def alt(self):
        return -self.font.descender * self.boyut


class _Parca:
    """tur: 'soz' | 'bosluk' | 'sekme' | 'resim'"""
    __slots__ = ("tur", "metin", "stil", "gen", "resim", "yuk")

    def __init__(self, tur, metin, stil, gen, resim=None, yuk=0.0):
        self.tur, self.metin, self.stil, self.gen, self.resim, self.yuk = tur, metin, stil, gen, resim, yuk


# ------------------------------------------------------------------ bloklar

class _Cizici:
    def __init__(self, sayfa):
        self.sayfa = sayfa
        self._yazicilar = {}

    def yaz(self, x, y, metin, stil):
        tw = self._yazicilar.get(stil.renk)
        if tw is None:
            tw = self._yazicilar[stil.renk] = fitz.TextWriter(self.sayfa.rect)
        try:
            # TextWriter.append her çağrıda TÜM birikmiş metnin sınır kutusunu
            # yeniden hesaplar (O(n²); 120 sayfalık kararda ~15 sn) — aynı
            # MuPDF çağrısı kutu hesabı olmadan yapılır. write_text o alanları
            # kullanmaz.
            trm = _mupdf.fz_make_matrix(stil.boyut, 0, 0, stil.boyut, x, tw.rect.height - y)
            _mupdf.fz_show_string(tw.this, stil.font.this, trm, metin, 0, 0, 0, _DIL_YOK)
        except Exception:
            try:
                tw.append((x, y), metin, font=stil.font, fontsize=stil.boyut)
            except Exception:
                pass

    def bitir(self):
        for renk, tw in self._yazicilar.items():
            tw.write_text(self.sayfa, color=renk)
        self._yazicilar = {}


class _Bosluk:
    def __init__(self, yuk):
        self.yuk = max(0.0, yuk)

    def bol(self, kalan, zorla):
        return (self, None) if self.yuk <= kalan else (None, None)  # sayfa sonunda boşluk düşer

    def ciz(self, c, x, y):
        pass


class _Satir:
    def __init__(self, ogeler, ust, yuk):
        self.ogeler, self.ust, self.yuk = ogeler, ust, yuk  # ogeler: [(x, _Parca)]

    def bol(self, kalan, zorla):
        return (self, None) if (self.yuk <= kalan + 0.01 or zorla) else (None, self)

    def ciz(self, c, x0, y0):
        taban = y0 + self.ust
        # bitişik, aynı stilli sözcük/boşlukları TEK yazım çağrısında birleştir
        kosu = None  # [x, metin, stil, bitis]
        for x, p in self.ogeler:
            if p.tur in ("soz", "bosluk") and kosu is not None and p.stil is kosu[2]                     and abs(x - kosu[3]) < 0.05 and (p.tur == "soz" or p.gen <= p.stil.gen(p.metin) + 0.05):
                kosu[1] += p.metin
                kosu[3] = x + p.gen
                continue
            if kosu is not None and kosu[1].strip():
                c.yaz(x0 + kosu[0], taban, kosu[1].rstrip(" "), kosu[2])
            kosu = [x, p.metin, p.stil, x + p.gen] if p.tur == "soz" else None
        if kosu is not None and kosu[1].strip():
            c.yaz(x0 + kosu[0], taban, kosu[1].rstrip(" "), kosu[2])
        for x, p in self.ogeler:
            if p.tur == "resim":
                try:
                    c.sayfa.insert_image(fitz.Rect(x0 + x, taban - p.yuk, x0 + x + p.gen, taban), stream=p.resim)
                except Exception:
                    pass
            if p.tur in ("soz", "bosluk") and p.stil is not None and p.stil.alti_cizili:
                yy = taban + p.stil.boyut * 0.12
                c.sayfa.draw_line((x0 + x, yy), (x0 + x + p.gen, yy), color=p.stil.renk,
                                  width=max(0.5, p.stil.boyut * 0.05))


class _Dikey:
    def __init__(self, cocuklar):
        self.cocuklar = cocuklar

    @property
    def yuk(self):
        return sum(c.yuk for c in self.cocuklar)

    def bol(self, kalan, zorla):
        bas, kullanilan = [], 0.0
        for i, c in enumerate(self.cocuklar):
            if kullanilan + c.yuk <= kalan + 0.01:
                bas.append(c)
                kullanilan += c.yuk
                continue
            h, t = c.bol(kalan - kullanilan, zorla and not bas)
            if h is not None:
                bas.append(h)
            kuyruk = ([t] if t is not None else []) + self.cocuklar[i + 1:]
            if not bas:
                return None, self
            return _Dikey(bas), (_Dikey(kuyruk) if kuyruk else None)
        return self, None

    def ciz(self, c, x, y):
        for b in self.cocuklar:
            b.ciz(c, x, y)
            y += b.yuk


class _TabloSatiri:
    def __init__(self, x, hucreler, min_yuk, kenarlik):
        self.x = x
        self.hucreler = hucreler  # [(dx, genislik, _Dikey)]
        self.min_yuk = min_yuk
        self.kenarlik = kenarlik  # None | 'hucre' | 'tablo'

    @property
    def yuk(self):
        ic = max([d.yuk for _, _, d in self.hucreler] or [0.0])
        return max(self.min_yuk, ic + 2 * _HUCRE_PAD_Y)

    def bol(self, kalan, zorla):
        if self.yuk <= kalan + 0.01:
            return self, None
        ic = kalan - 2 * _HUCRE_PAD_Y
        baslar, kuyruklar, ilerledi = [], [], False
        for dx, w, d in self.hucreler:
            h, t = d.bol(ic, zorla) if ic > 0 or zorla else (None, d)
            ilerledi = ilerledi or h is not None
            baslar.append((dx, w, h or _Dikey([])))
            kuyruklar.append((dx, w, t or _Dikey([])))
        if not ilerledi:
            return None, self
        return (_TabloSatiri(self.x, baslar, 0.0, self.kenarlik),
                _TabloSatiri(self.x, kuyruklar, 0.0, self.kenarlik))

    def ciz(self, c, x0, y0):
        h = self.yuk
        for dx, w, d in self.hucreler:
            if self.kenarlik == "hucre":
                c.sayfa.draw_rect(fitz.Rect(x0 + self.x + dx, y0, x0 + self.x + dx + w, y0 + h),
                                  color=(0, 0, 0), width=0.5)
            d.ciz(c, x0 + self.x + dx + _HUCRE_PAD_X, y0 + _HUCRE_PAD_Y)
        if self.kenarlik == "tablo" and self.hucreler:
            sag = max(dx + w for dx, w, _ in self.hucreler)
            c.sayfa.draw_rect(fitz.Rect(x0 + self.x, y0, x0 + self.x + sag, y0 + h), color=(0, 0, 0), width=0.5)


# --------------------------------------------------------- paragraf yerleşimi

def _sekme_duraklari(tabset):
    out = []
    for parca in (tabset or "").split(","):
        alanlar = parca.split(":")
        if alanlar and alanlar[0].strip():
            out.append((_f(alanlar[0]), int(_f(alanlar[1], 0)) if len(alanlar) > 1 else 0))
    return sorted(out)


def _sonraki_durak(x_goreli, duraklar):
    """Swing ParagraphView.nextTabStop: (konum, hiza)."""
    if not duraklar:
        return (int(x_goreli // 72) + 1) * 72.0, 0
    for poz, hiza in duraklar:
        if poz > x_goreli + 0.01:
            return poz, hiza
    return x_goreli + 5.0, 0


def _liste_oneki(par, ctx):
    if ctx.par_attr(par, "Bulleted") == "true":
        return "»" if "ARROW" in (ctx.par_attr(par, "BulletType") or "") else "•"
    if ctx.par_attr(par, "Numbered") == "true":
        lid = ctx.par_attr(par, "ListId") or "_"
        n = ctx.liste_sayac[lid] = ctx.liste_sayac.get(lid, 0) + 1
        tur = ctx.par_attr(par, "NumberType") or ""
        return f"{n}-" if "TRE" in tur else (f"{n})" if "PARANTHESE" in tur else f"{n}.")
    return None


def _resim_parcasi(el, ctx, stil, maks_gen):
    try:
        veri = base64.b64decode(el.get("imageData", ""))
    except Exception:
        return None
    if not veri:
        return None
    w, h = _f(el.get("width")), _f(el.get("height"))
    if w <= 0 or h <= 0:
        try:
            px = fitz.Pixmap(veri)
            w, h = px.width * 0.75, px.height * 0.75
        except Exception:
            return None
    if w > maks_gen > 0:
        h, w = h * maks_gen / w, maks_gen
    if h > 700:
        w, h = w * 700 / h, 700.0
    return _Parca("resim", "", stil, w, resim=veri, yuk=h)


def _paragraf(par, genislik, ctx):
    sol = _f(ctx.par_attr(par, "LeftIndent"))
    sag = _f(ctx.par_attr(par, "RightIndent"))
    ilk = _f(ctx.par_attr(par, "FirstLineIndent"))
    hiza = ctx.par_attr(par, "Alignment", "0")
    ls = min(3.0, max(0.0, _f(ctx.par_attr(par, "LineSpacing"))))
    duraklar = _sekme_duraklari(ctx.par_attr(par, "TabSet"))
    sinir = max(sol + 10.0, genislik - sag)

    # 1) parçalar
    parcalar, son_stil = [], None
    for el in par:
        if el.tag in ("content", "field", "space", "tab"):
            stil = ctx.stil(el, par)
            son_stil = stil
            metin = ctx.dilim(el).replace("\r", "").replace("\n", "")
            if el.tag == "tab" and not metin:
                metin = "\t"
            for t in re.findall(r"\t| +|[^\t ]+", metin):
                if t == "\t":
                    parcalar.append(_Parca("sekme", t, stil, 0.0))
                elif t[0] == " ":
                    parcalar.append(_Parca("bosluk", t, stil, stil.gen(t)))
                else:
                    parcalar.append(_Parca("soz", t, stil, stil.gen(t)))
        elif el.tag == "image":
            stil = ctx.stil(el, par)
            p = _resim_parcasi(el, ctx, stil, sinir - sol)
            if p:
                parcalar.append(p)
    if son_stil is None:
        son_stil = ctx.stil(par, par)
    onek = _liste_oneki(par, ctx)
    if onek:
        ilk_stil = next((p.stil for p in parcalar if p.tur == "soz"), son_stil)
        parcalar[:0] = [_Parca("soz", onek, ilk_stil, ilk_stil.gen(onek)),
                        _Parca("bosluk", " ", ilk_stil, ilk_stil.gen(" "))]

    # 2) sözcük grupları (aynı sözcüğün farklı stilli parçaları ayrılmaz)
    atomlar = []
    for p in parcalar:
        if p.tur == "soz" and atomlar and atomlar[-1][0] == "soz":
            atomlar[-1][1].append(p)
        else:
            atomlar.append((p.tur, [p]))

    # 3) satırlara sar
    satirlar = []  # (ogeler, son_sekme_idx, son_satir_mi)
    cur, x, son_sekme = [], sol + ilk, -1

    def satir_bitir(son=False):
        nonlocal cur, x, son_sekme
        while cur and cur[-1][1].tur in ("bosluk", "sekme"):
            cur.pop()
        satirlar.append((cur, son_sekme, son))
        cur, x, son_sekme = [], sol, -1

    def icerik_var():
        return any(p.tur in ("soz", "resim", "sekme") for _, p in cur)

    for i, (tur, grup) in enumerate(atomlar):
        if tur == "soz":
            w = sum(p.gen for p in grup)
            if x + w > sinir + 0.01 and icerik_var():
                satir_bitir()
            if w > sinir - sol:  # satırdan uzun sözcük: harf harf böl
                for p in grup:
                    for ch in p.metin:
                        cw = p.stil.gen(ch)
                        if x + cw > sinir + 0.01 and icerik_var():
                            satir_bitir()
                        cur.append([x, _Parca("soz", ch, p.stil, cw)])
                        x += cw
                continue
            for p in grup:
                cur.append([x, p])
                x += p.gen
        elif tur == "bosluk":
            if not cur and satirlar:
                continue
            p = grup[0]
            cur.append([x, p])
            x += p.gen
        elif tur == "sekme":
            p = grup[0]
            poz, th = _sonraki_durak(x - sol, duraklar)
            yeni = sol + poz
            if th in (1, 2, 4):
                seg = 0.0
                for t2, g2 in atomlar[i + 1:]:
                    if t2 == "sekme":
                        break
                    seg += sum(q.gen for q in g2)
                yeni = max(x, yeni - (seg / 2 if th == 2 else seg))
            if yeni > sinir and icerik_var():
                satir_bitir()
                continue
            cur.append([x, _Parca("sekme", "\t", p.stil, yeni - x)])
            x = yeni
            son_sekme = len(cur)
        else:  # resim
            p = grup[0]
            if x + p.gen > sinir + 0.01 and icerik_var():
                satir_bitir()
            cur.append([x, p])
            x += p.gen
    satir_bitir(son=True)

    # 4) hizalama + yükseklik
    bloklar = [_Bosluk(_f(ctx.par_attr(par, "SpaceAbove")))]
    for ogeler, son_sekme, son in satirlar:
        bit = max([ox + p.gen for ox, p in ogeler] or [0.0])
        fazla = sinir - bit
        if ogeler and fazla > 0:
            if hiza == "1":
                for o in ogeler:
                    o[0] += fazla / 2
            elif hiza == "2":
                for o in ogeler:
                    o[0] += fazla
            elif hiza == "3" and not son:
                bas = max(0, son_sekme)
                araliklar = [j for j in range(bas, len(ogeler)) if ogeler[j][1].tur == "bosluk"]
                if araliklar:
                    pay, ek = fazla / len(araliklar), 0.0
                    for j in range(bas, len(ogeler)):
                        ogeler[j][0] += ek
                        if ogeler[j][1].tur == "bosluk":
                            ogeler[j][1] = _Parca("bosluk", ogeler[j][1].metin, ogeler[j][1].stil,
                                                  ogeler[j][1].gen + pay)
                            ek += pay
        metrik = [p.stil for _, p in ogeler if p.tur != "resim" and p.stil is not None] or [son_stil]
        ust = max(s.ust for s in metrik)
        alt = max(s.alt for s in metrik)
        resim_ust = max([p.yuk for _, p in ogeler if p.tur == "resim"] or [0.0])
        ust = max(ust, resim_ust)
        yuk = (ust + alt + 0.04 * max(s.boyut for s in metrik)) * (1 + ls)
        bloklar.append(_Satir([(o[0], o[1]) for o in ogeler], ust, yuk))
    bloklar.append(_Bosluk(_f(ctx.par_attr(par, "SpaceBelow"))))
    return bloklar


def _tablo(tbl, genislik, ctx):
    sol = _f(tbl.get("LeftIndent"))
    tg = max(20.0, genislik - sol - _f(tbl.get("RightIndent")))
    oran_t = _oranlar(tbl.get("columnSpans"))
    kenar = {"borderCell": "hucre", "borderTable": "tablo"}.get(tbl.get("border", "borderCell"))
    bloklar = [_Bosluk(_f(tbl.get("SpaceAbove")))]
    for row in tbl.findall("row"):
        hucreler = row.findall("cell")
        if not hucreler:
            continue
        oran = _oranlar(row.get("columnSpans"))
        if len(oran) != len(hucreler):
            oran = oran_t if len(oran_t) == len(hucreler) else [1.0] * len(hucreler)
        top = sum(oran)
        dx, liste = 0.0, []
        for cell, o in zip(hucreler, oran):
            w = tg * o / top
            liste.append((dx, w, _Dikey(_kap(list(cell), max(5.0, w - 2 * _HUCRE_PAD_X), ctx))))
            dx += w
        bloklar.append(_TabloSatiri(sol, liste, _f(row.get("height")), kenar))
    bloklar.append(_Bosluk(_f(tbl.get("SpaceBelow"))))
    return bloklar


def _kap(elemanlar, genislik, ctx):
    out = []
    for el in elemanlar:
        if el.tag == "paragraph":
            out.extend(_paragraf(el, genislik, ctx))
        elif el.tag == "table":
            out.extend(_tablo(el, genislik, ctx))
    return out


def _gorunur(el, ctx):
    for c in el.iter():
        if c.tag == "image":
            return True
        if c.tag in ("content", "field", "space") and ctx.dilim(c).strip():
            return True
    return False


def _sayfa_no_metni(el, n, toplam):
    m = re.search(r"(\d+)$", el.get("pageNumber-spec", "") or "")
    spec = int(m.group(1)) if m else 0
    if not spec:
        return None, None
    try:
        bas = int(el.get("pageNumber-pageStartNumStr") or 1)
    except ValueError:
        bas = 1
    n2, top2 = n + bas - 1, toplam + bas - 1
    metin = str(n2)
    if spec & 2048:
        metin += (el.get("pageNumber-seperator") or "/") + str(top2)
    on = (el.get("pageNumber-foreStr") or "").strip()
    if on:
        metin = on + " " + metin
    hiza = "2" if spec & 64 else ("1" if spec & 32 else "0")
    return metin, hiza


# --------------------------------------------------------------------- API

def udf_zip_mi(ham_bayt):
    """`ham_bayt` bir UDF (zip + content.xml) mi? — gerçek ZIP imzasına bakar."""
    if not ham_bayt or ham_bayt[:2] != b"PK":
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(ham_bayt)) as z:
            return "content.xml" in z.namelist()
    except Exception:
        return False


def udf_pdf_uret(ham_bayt):
    """UDF baytlarından PDF üretir, PDF baytlarını döner. Şema beklenenden
    farklıysa istisna fırlatır — çağıran placeholder-sayfa fallback'i için
    yakalamalı (uydurma render YAPILMAZ)."""
    with zipfile.ZipFile(io.BytesIO(ham_bayt)) as z:
        xml_bytes = z.read("content.xml")
    root = ET.fromstring(xml_bytes)

    content_el = root.find("content")
    full_text = (content_el.text or "") if content_el is not None else ""
    ctx = _Baglam(root, full_text)

    pf = root.find("properties/pageFormat")
    pa = pf.attrib if pf is not None else {}
    left_m = _f(pa.get("leftMargin"), _VARSAYILAN_MARJ)
    right_m = _f(pa.get("rightMargin"), _VARSAYILAN_MARJ)
    top_m = _f(pa.get("topMargin"), _VARSAYILAN_MARJ)
    bottom_m = _f(pa.get("bottomMargin"), _VARSAYILAN_MARJ)
    hdr_off = _f(pa.get("headerFOffset"), 15.0)
    ftr_off = _f(pa.get("footerFOffset"), 15.0)
    sw, sh = (_A4_H, _A4_W) if pa.get("paperOrientation") == "0" else (_A4_W, _A4_H)
    usable_w = max(50.0, sw - left_m - right_m)

    elements_el = root.find("elements")
    elemanlar = list(elements_el) if elements_el is not None else []
    header_el = next((e for e in elemanlar if e.tag == "header"), None)
    footer_el = next((e for e in elemanlar if e.tag == "footer"), None)

    def kenar_blok(el):
        if el is None or not _gorunur(el, ctx):
            return None
        ctx.varsayilan_renk = _argb_rgb(el.get("foreground")) or (0.0, 0.0, 0.0)
        try:
            return _Dikey(_kap(list(el), usable_w, ctx))
        finally:
            ctx.varsayilan_renk = (0.0, 0.0, 0.0)

    hdr = kenar_blok(header_el)
    ftr = kenar_blok(footer_el)

    def aralikta(el, n):
        if el is None:
            return False
        bas = int(_f(el.get("startPage"), 1))
        bit = int(_f(el.get("stopPage"), 0))
        return n >= bas and (bit <= 0 or n <= bit)

    govde = _Dikey(_kap([e for e in elemanlar if e.tag not in ("header", "footer")], usable_w, ctx))

    doc = fitz.open()
    sayfalar = []
    kalan = govde
    while True:
        n = len(sayfalar) + 1
        ust = top_m
        if hdr is not None and aralikta(header_el, n):
            ust = max(top_m, hdr_off + hdr.yuk)
        alt = sh - bottom_m
        if ftr is not None and aralikta(footer_el, n):
            alt = min(alt, sh - ftr_off - ftr.yuk)
        kullanilabilir = max(20.0, alt - ust)
        bas = None
        if kalan is not None:
            bas, kuyruk = kalan.bol(kullanilabilir, False)
            if bas is None:
                bas, kuyruk = kalan.bol(kullanilabilir, True)
            kalan = kuyruk
        page = doc.new_page(width=sw, height=sh)
        c = _Cizici(page)
        if bas is not None:
            bas.ciz(c, left_m, ust)
        c.bitir()
        sayfalar.append(n)
        del c, page
        if kalan is None or n >= 3000:
            break

    toplam = len(sayfalar)
    for n in sayfalar:
        c = _Cizici(doc[n - 1])
        if hdr is not None and aralikta(header_el, n):
            hdr.ciz(c, left_m, hdr_off)
        if ftr is not None and aralikta(footer_el, n):
            ftr.ciz(c, left_m, sh - ftr_off - ftr.yuk)
        if footer_el is not None and aralikta(footer_el, n):
            metin, hiza = _sayfa_no_metni(footer_el, n, toplam)
            if metin:
                fe = footer_el
                stil = _Stil(ctx.fontlar.al(fe.get("pageNumber-fontFace") or "Arial",
                                            fe.get("pageNumber-fontBold") == "true",
                                            fe.get("pageNumber-fontItalic") == "true"),
                             _f(fe.get("pageNumber-fontSize"), 10.0) or 10.0,
                             _argb_rgb(fe.get("pageNumber-color")) or (0.0, 0.0, 0.0), False)
                w = stil.gen(metin)
                x = left_m + {"1": (usable_w - w) / 2, "2": usable_w - w}.get(hiza, 0.0)
                c.yaz(x, sh - ftr_off, metin, stil)
        c.bitir()

    try:  # yalnız kullanılan glifler kalsın (tam TTF gömülürse sayfa başı ~2.5 MB)
        doc.subset_fonts()
    except Exception:
        pass
    buf = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return buf
