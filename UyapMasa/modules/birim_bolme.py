# -*- coding: utf-8 -*-
"""
Birim (icra dairesi / mahkeme) adını Yer / No / Birim Türü olarak bölme
======================================================================
Kullanıcı isteği (2026-09-24): "İzmir 10. İcra Dairesi" gibi NUMARALI
birimlerde yer (il/ilçe/adliye) ile daire numarası ayrı sütunlarda
gösterilebilsin — ekranda bir düğmeyle (Dosyalarım (Tümü), İcra Dosyalarım,
Barkod Sorgu) ve Excel'e Aktar'da seçimle (tek dosya / bölünmüş / numarasız
+ numaralı iki ayrı dosya). Numarasız birimler (MTS, "İzmir Banka Alacakları
İcra Dairesi", "Urla İcra Dairesi" …) TEK veri kalır: adın tamamı "Yer"
sütununa yazılır (kullanıcı: "tek veri olabiliyor, sorun yok").

DB'deki birim adlarıyla doğrulanan kalıplar (2026-09-24):
  "İzmir 10. İcra Dairesi", "Ankara Batı 1. Asliye Hukuk Mahkemesi",
  "Adana Bölge Adliye Mahkemesi 1. Ceza Dairesi",
  "Fatsa 2. (Sulh Hukuk Mah.) Satış Memurluğu",
  "(Kapatılan)Balıkesir 2. İcra Dairesi"  → Not: "Kapatılan".
"Yer" sayıdan önce ne yazıyorsa odur — her zaman il/ilçe DEĞİL ("Ankara Batı",
"İstanbul Anadolu" adliye adı); ayrıca bir il/ilçe listesine çevrilmez.
Yalnız OKUR/GÖSTERİR — veritabanına hiçbir şey yazmaz.
"""

import os
import re
import tkinter as tk

import openpyxl

from theme import C, RoundButton

_KAPATILAN_RE = re.compile(r"^\(\s*Kapatılan\s*\)\s*")
_NUMARALI_RE = re.compile(r"^(?P<yer>.+?)\s+(?P<no>\d+)\s*\.\s*(?P<tur>\S.*)$")

# Bölünmüş sütunlar (anahtar, başlık, genişlik) — birim sütununun YERİNE geçer.
BOLUNMUS_KOLONLAR = [
    ("birimYer", "Yer", 150),
    ("birimNo", "No", 45),
    ("birimTuru", "Birim Türü", 170),
    ("birimNot", "Not", 75),
]
BOLUNMUS_ANAHTARLAR = tuple(k for k, _l, _w in BOLUNMUS_KOLONLAR)


def birim_adi_bol(ad):
    """{"birimYer", "birimNo" (int|""), "birimTuru", "birimNot", "numarali"}."""
    ad = (ad or "").strip()
    not_ = ""
    m0 = _KAPATILAN_RE.match(ad)
    if m0:
        not_ = "Kapatılan"
        ad = ad[m0.end():].strip()
    m = _NUMARALI_RE.match(ad)
    if m:
        return {"birimYer": m.group("yer").strip(), "birimNo": int(m.group("no")),
                "birimTuru": m.group("tur").strip(), "birimNot": not_, "numarali": True}
    return {"birimYer": ad, "birimNo": "", "birimTuru": "", "birimNot": not_, "numarali": False}


def alanlari_ekle(kayitlar, birim_anahtari="birimAdi"):
    """Kayıtlara (yerinde) bölünmüş alanları ekler — ekran düğmesi açıkken
    filtre/sıralama bu alanlarla çalışsın diye. Aynı ad için tek kez ayrıştırır."""
    onbellek = {}
    for r in kayitlar:
        ad = r.get(birim_anahtari) or r.get("birim_adi") or ""
        b = onbellek.get(ad)
        if b is None:
            b = onbellek[ad] = birim_adi_bol(ad)
        r.update({k: b[k] for k in BOLUNMUS_ANAHTARLAR})
    return kayitlar


def kolonlari_bol(kolonlar, birim_anahtari="birimAdi"):
    """(anahtar, başlık, genişlik) listesinde birim sütununu bölünmüş
    sütunlarla değiştirir (zaten bölünmüşse aynen döner)."""
    if any(k in BOLUNMUS_ANAHTARLAR for k, _l, _w in kolonlar):
        return list(kolonlar)
    out = []
    for kol in kolonlar:
        if kol[0] == birim_anahtari:
            out.extend(BOLUNMUS_KOLONLAR)
        else:
            out.append(kol)
    return out


def kolonlari_birlestir(kolonlar, taban_kolonlar, birim_anahtari="birimAdi"):
    """Bölünmüş sütunları özgün birim sütununa geri çevirir."""
    if not any(k in BOLUNMUS_ANAHTARLAR for k, _l, _w in kolonlar):
        return list(kolonlar)
    birim = next((kol for kol in taban_kolonlar if kol[0] == birim_anahtari), (birim_anahtari, "Birim", 200))
    out, eklendi = [], False
    for kol in kolonlar:
        if kol[0] in BOLUNMUS_ANAHTARLAR:
            if not eklendi:
                out.append(birim)
                eklendi = True
        else:
            out.append(kol)
    return out


# ─────────────────────────── ekran düğmesi (başlık şeritli tablolar) ───────────────────────────
def ekran_toggle(ekran, birim_anahtari="birimAdi"):
    """Dosyalarım (Tümü) / Barkod Sorgu ekranları için "Daireyi Böl" düğmesi:
    `ekran.KOLONLAR`'ı (örnek özniteliği olarak) bölünmüş/özgün hâle çevirir,
    ağacın sütunlarını ve başlık şeridini yeniden kurar. Ekranın `col_vars`,
    `_basligi_kur`, `_combolari_doldur`, `_yerel_uygula`, `tree`, `sort_key`
    üyelerini kullanır (iki ekranda AYNI desen). Döner: yeni durum (bölünmüş mü)."""
    taban = type(ekran).KOLONLAR
    bolunmus = not getattr(ekran, "_daire_bolunmus", False)
    ekran.KOLONLAR = kolonlari_bol(taban, birim_anahtari) if bolunmus else list(taban)
    ekran._daire_bolunmus = bolunmus
    for k, _l, _w in ekran.KOLONLAR:
        if k not in ekran.col_vars:
            ekran.col_vars[k] = tk.StringVar()
    anahtarlar = [k for k, _l, _w in ekran.KOLONLAR]
    if ekran.sort_key and ekran.sort_key not in anahtarlar:
        ekran.sort_key = None
    # displaycolumns eski sütun adlarını tutuyorsa columns değişimi TclError
    # verir ("Invalid column index") — önce "#all"a çekilir.
    ekran.tree["displaycolumns"] = "#all"
    ekran.tree["columns"] = anahtarlar
    ekran.tree["displaycolumns"] = anahtarlar
    alanlari_ekle(getattr(ekran, "kayitlar_ham", []) or [], birim_anahtari)
    ekran._basligi_kur()
    ekran._combolari_doldur()
    ekran._yerel_uygula()
    return bolunmus


# ─────────────────────────── Excel: seçim penceresi + yazıcı ───────────────────────────
MOD_EKRANDAKI = "ekrandaki"
MOD_BOLUNMUS = "bolunmus"
MOD_IKI_DOSYA = "iki_dosya"


def excel_secenek_sor(app, ekran_bolunmus=False):
    """Excel'e Aktar'dan önce sorulan seçim (kullanıcı isteği, 2026-09-24).
    Döner: MOD_* ya da None (vazgeçildi)."""
    sonuc = {"mod": None}
    top = tk.Toplevel(app)
    top.title("Excel Çıktısı")
    top.configure(bg=C.CARD)
    top.transient(app)
    top.resizable(False, False)
    var = tk.StringVar(value=MOD_IKI_DOSYA)
    tk.Label(top, text="İcra dairesi / mahkeme sütunu nasıl aktarılsın?", bg=C.CARD, fg=C.INK,
             font=app.f_card_t).pack(anchor="w", padx=18, pady=(16, 8))
    secenekler = [
        (MOD_EKRANDAKI, "Ekrandaki gibi, tek dosya"
         + (" (şu an bölünmüş görünüm)" if ekran_bolunmus else "")),
        (MOD_BOLUNMUS, "Tek dosya — daire bölünmüş: Yer | No | Birim Türü"
                       " (numarasızlarda adın tamamı Yer'de)"),
        (MOD_IKI_DOSYA, "İki ayrı dosya: numarasız daireler (tek sütun) +"
                        " numaralı daireler (Yer | No | Birim Türü)"),
    ]
    for deger, metin in secenekler:
        tk.Radiobutton(top, text=metin, value=deger, variable=var, bg=C.CARD, fg=C.INK,
                       activebackground=C.CARD, selectcolor=C.CARD, font=app.f_body,
                       anchor="w", justify="left").pack(fill="x", padx=18, pady=2)
    alt = tk.Frame(top, bg=C.CARD)
    alt.pack(fill="x", padx=18, pady=(12, 16))

    def tamam():
        sonuc["mod"] = var.get()
        top.destroy()
    RoundButton(alt, "Aktar", command=tamam, kind="primary",
                font=app.f_nav_b, height=34).pack(side="right", ipadx=6)
    RoundButton(alt, "İptal", command=top.destroy, kind="ghost",
                font=app.f_nav_b, height=34).pack(side="right", padx=(0, 8), ipadx=4)
    top.bind("<Return>", lambda e: tamam())
    top.bind("<Escape>", lambda e: top.destroy())
    top.grab_set()
    app.wait_window(top)
    return sonuc["mod"]


def _deger(rec, k, birim_anahtari):
    if k in BOLUNMUS_ANAHTARLAR and k not in rec:
        return birim_adi_bol(rec.get(birim_anahtari) or "")[k]
    v = rec.get(k, "")
    return "" if v is None else v


def _sayfa_yaz(yol, sayfa_adi, kolonlar, kayitlar, birim_anahtari):
    # "Not" sütunu hiç dolu değilse Excel'e konmaz (gereksiz boş sütun).
    if not any(_deger(r, "birimNot", birim_anahtari) for r in kayitlar):
        kolonlar = [kol for kol in kolonlar if kol[0] != "birimNot"]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sayfa_adi[:31]
    ws.append([lbl for _k, lbl, _w in kolonlar])
    for rec in kayitlar:
        ws.append([_deger(rec, k, birim_anahtari) for k, _l, _w in kolonlar])
    for i, (_k, _l, w) in enumerate(kolonlar, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = max(6, min(w // 6, 45))
    ws.freeze_panes = "A2"
    wb.save(yol)


def excel_yaz(yol, mod, ekran_kolonlari, taban_kolonlar, kayitlar, sayfa_adi, birim_anahtari="birimAdi"):
    """Seçilen moda göre bir ya da iki .xlsx yazar. Döner: [(yol, satır sayısı), ...].
    MOD_IKI_DOSYA'da dosyalar `<ad>_Numarasiz.xlsx` / `<ad>_Numarali.xlsx`;
    boş kalan grup için dosya yazılmaz."""
    if mod == MOD_EKRANDAKI:
        _sayfa_yaz(yol, sayfa_adi, ekran_kolonlari, kayitlar, birim_anahtari)
        return [(yol, len(kayitlar))]
    bolunmus = kolonlari_bol(ekran_kolonlari, birim_anahtari)
    if mod == MOD_BOLUNMUS:
        _sayfa_yaz(yol, sayfa_adi, bolunmus, kayitlar, birim_anahtari)
        return [(yol, len(kayitlar))]
    tek = kolonlari_birlestir(ekran_kolonlari, taban_kolonlar, birim_anahtari)
    numarali = [r for r in kayitlar if birim_adi_bol(r.get(birim_anahtari) or "")["numarali"]]
    numarasiz = [r for r in kayitlar if not birim_adi_bol(r.get(birim_anahtari) or "")["numarali"]]
    kok, uzanti = os.path.splitext(yol)
    uzanti = uzanti or ".xlsx"
    yazilan = []
    if numarasiz:
        y = f"{kok}_Numarasiz{uzanti}"
        _sayfa_yaz(y, f"{sayfa_adi} (numarasız)", tek, numarasiz, birim_anahtari)
        yazilan.append((y, len(numarasiz)))
    if numarali:
        y = f"{kok}_Numarali{uzanti}"
        _sayfa_yaz(y, f"{sayfa_adi} (numaralı)", bolunmus, numarali, birim_anahtari)
        yazilan.append((y, len(numarali)))
    return yazilan
