# -*- coding: utf-8 -*-
"""
PTT Barkod Sorgu — BAĞIMSIZ program (PTT resmî SOAP servisi)
=============================================================
Bir Excel dosyasındaki PTT barkod numaralarını PTT'nin resmî "GonderiHareketV2"
web servisinde (`barkodSorgu`) sorgular ve sonucu yeni bir Excel dosyasına
yazar. Ana UyapMasa/UYAP modüllerinden HİÇBİR ŞEY import etmez; tek bağımlılık
`openpyxl`'dir (pip install openpyxl).

NEDEN SOAP? ptt.gov.tr'nin herkese açık takip kutusu (api.ptt.gov.tr/api/
ShipmentTracking) artık Cloudflare Turnstile bot doğrulaması ve
`X-Tracking-Token` istiyor — otomatik programla kullanılamaz. Bu program onun
yerine PTT'nin kurumsal müşterilere açtığı resmî servisi kullanır.

KİMLİK BİLGİSİ GEREKİR (PTT'den alınır): kullanıcı adı, müşteri no, şifre.
  * Arayüzde alanlara yazılır (şifre diske KAYDEDİLMEZ), ya da ortam
    değişkenleri: PTT_KULLANICI, PTT_MUSTERI_NO, PTT_SIFRE.
  * Komut satırı: python ptt_barkod_sorgu.py barkodlar.xlsx --kullanici X --musteri-no 123
    (şifre PTT_SIFRE ortam değişkeninden ya da istemde sorulur; komut satırına
    yazılmaz — kabuk geçmişinde kalmasın diye).

Kullanım
--------
  * Arayüzlü:     python ptt_barkod_sorgu.py
  * Komut satırı: python ptt_barkod_sorgu.py barkodlar.xlsx [--bekleme 0.3] [--cikti sonuc.xlsx]

Excel girdisi
-------------
Barkodlar Excel'in AKTİF sayfasında H sütunundan okunur (değiştirmek için
--sutun ya da arayüzdeki "Barkod sütunu"). Sütunun ilk dolu hücresi "barkod"
içeren bir başlıksa atlanır. Hücre metin ya da sayı olabilir; boşluk ve Code-39
yıldızları (*...*) temizlenir. UYAP bağlantısı YOKTUR.
Çıktı: girdiyle aynı klasöre `<ad>_PTT_Sonuc_<tarih_saat>.xlsx`.

Servis (WSDL'den ve canlı bir istekle doğrulandı, 2026-09-21)
-------------------------------------------------------------
  POST https://pttws.ptt.gov.tr/GonderiHareketV2/services/Sorgu
  SOAP 1.1, document/literal, SOAPAction "urn:barkodSorgu", ad alanları
  http://hareket.ptt.gov.tr (sarmalayıcı) ve http://hareket.ptt.gov.tr/xsd
  (alanlar). Girdi: barkod, kullanici, musteri_no, sifre.
  Yanıt alanları: aciklama, hataKodu, kabulTarih/kabulIsyeri,
  sonIslemTarih/sonIslemIsyeri/sonIslemAciklama, aliciTeslim*, gonTeslim*,
  iade*, ucret.
  DOĞRULANAN: geçersiz kimlikle "KULLANICI SIFRE HATALI" / hataKodu -1 döner.
  DOĞRULANMAYAN (gerçek hesap gerekir): başarılı yanıttaki alan içerikleri ve
  tarih biçimleri, başarı/"kayıt yok" hata kodları, hesabın YALNIZ kendi
  gönderilerini mi yoksa herhangi bir barkodu mu sorgulayabildiği.
"""

import argparse
import getpass
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from xml.sax.saxutils import escape

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import column_index_from_string, get_column_letter

VARSAYILAN_SUTUN = "H"      # barkodların okunduğu Excel sütunu
SOAP_URL = "https://pttws.ptt.gov.tr/GonderiHareketV2/services/Sorgu"
NS_HR = "http://hareket.ptt.gov.tr"
NS_XSD = "http://hareket.ptt.gov.tr/xsd"
VARSAYILAN_BEKLEME_SN = 0.3
DENEME_SAYISI = 3            # geçici ağ/sunucu hatasında toplam deneme
BARKOD_RE = re.compile(r"[A-Za-z0-9]{8,30}")

KOLONLAR = ["Sıra", "Excel Satırı", "Barkod", "PTT Durumu", "Son İşlem Tarihi",
            "Son İşlem İşyeri", "Kabul Tarihi", "Kabul İşyeri", "Alıcı Teslim",
            "Gönderici Teslim", "İade", "PTT Mesajı", "Hata Kodu", "Durum"]


class YetkiHatasi(Exception):
    """PTT kimlik bilgisini reddetti — kalan barkodları sorgulamanın anlamı yok."""


@dataclass
class Kimlik:
    kullanici: str
    musteri_no: str
    sifre: str

    def dogrula(self):
        if not (self.kullanici and self.musteri_no and self.sifre):
            raise ValueError("Kullanıcı adı, müşteri no ve şifre gerekli.")
        if not str(self.musteri_no).strip().isdigit():
            raise ValueError("Müşteri no yalnızca rakamlardan oluşmalı.")


# ── Excel okuma ──────────────────────────────────────────────────────────────
def barkod_temizle(deger):
    """Hücre değerini barkod metnine çevirir (boşsa "")."""
    if deger is None:
        return ""
    if isinstance(deger, float) and deger.is_integer():
        deger = int(deger)
    return str(deger).strip().strip("*").replace(" ", "").strip()


def sutun_indeksi(sutun):
    """'H' ya da '8' -> 8 (1 tabanlı sütun numarası)."""
    s = str(sutun).strip().upper()
    try:
        n = int(s) if s.isdigit() else column_index_from_string(s)
    except ValueError:
        n = 0
    if n < 1:
        raise ValueError(f"Geçersiz sütun: {sutun!r} (örn. H)")
    return n


def excel_oku(yol, sutun=VARSAYILAN_SUTUN):
    """Excel'in AKTİF sayfasında `sutun` (varsayılan H) sütunundan barkodları okur:
    [(excel_satiri, ham_deger, barkod), ...]. Boş hücreler atlanır; sütunun ilk dolu
    hücresi 'barkod' içeren bir başlıksa (örn. 'Barkod No') o satır atlanır."""
    n = sutun_indeksi(sutun)
    wb = openpyxl.load_workbook(yol, data_only=True, read_only=True)
    try:
        kayitlar, ilk = [], True
        for r, satir in enumerate(wb.active.iter_rows(values_only=True), start=1):
            ham = satir[n - 1] if len(satir) >= n else None
            barkod = barkod_temizle(ham)
            if not barkod:
                continue
            if ilk:
                ilk = False
                if "barkod" in barkod.lower():
                    continue
            kayitlar.append((r, ham, barkod))
        return kayitlar
    finally:
        wb.close()


# ── PTT SOAP sorgusu ─────────────────────────────────────────────────────────
def zarf_olustur(barkod, kimlik):
    """barkodSorgu SOAP 1.1 zarfı (document/literal, elementFormDefault=qualified)."""
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        f'xmlns:hr="{NS_HR}" xmlns:xsd="{NS_XSD}"><soapenv:Body><hr:barkodSorgu><hr:input>'
        f"<xsd:barkod>{escape(barkod)}</xsd:barkod>"
        f"<xsd:kullanici>{escape(kimlik.kullanici)}</xsd:kullanici>"
        f"<xsd:musteri_no>{escape(str(kimlik.musteri_no).strip())}</xsd:musteri_no>"
        f"<xsd:sifre>{escape(kimlik.sifre)}</xsd:sifre>"
        "</hr:input></hr:barkodSorgu></soapenv:Body></soapenv:Envelope>"
    )


def _yerel_ad(etiket):
    return etiket.rsplit("}", 1)[-1]


def yaniti_coz(xml_metni):
    """SOAP yanıtından `return` düğümünün alanlarını {ad: metin} sözlüğü yapar.
    SOAP Fault gelirse {"_hata": "..."} döner."""
    try:
        kok = ET.fromstring(xml_metni)
    except ET.ParseError as e:
        return {"_hata": f"PTT yanıtı XML değil ({e})"}
    for d in kok.iter():
        if _yerel_ad(d.tag) == "Fault":
            fs = next((x.text for x in d.iter() if _yerel_ad(x.tag) == "faultstring"), "") or "SOAP Fault"
            return {"_hata": f"PTT SOAP hatası: {fs}"}
    dugum = next((d for d in kok.iter() if _yerel_ad(d.tag) == "return"), None)
    if dugum is None:
        return {"_hata": "PTT yanıtında 'return' yok"}
    return {_yerel_ad(c.tag): (c.text or "").strip() for c in dugum}


def _yetki_reddi_mi(veri):
    aciklama = veri.get("aciklama", "").upper().replace("Ş", "S").replace("İ", "I")
    return veri.get("hataKodu") == "-1" and "SIFRE" in aciklama


def ptt_sorgula(barkod, kimlik, timeout=30):
    """Bir barkodu sorgular. Başarıda alan sözlüğü, hatada {"_hata": "..."} döner.
    Kimlik reddedilirse YetkiHatasi fırlatır. Yalnız geçici hatalarda (ağ,
    502/503/504) yeniden dener — 500 SOAP Fault kalıcı sayılır."""
    govde = zarf_olustur(barkod, kimlik).encode("utf-8")
    son_hata = ""
    for deneme in range(DENEME_SAYISI):
        if deneme:
            time.sleep(2 ** deneme)
        istek = urllib.request.Request(SOAP_URL, data=govde, method="POST")
        istek.add_header("Content-Type", "text/xml; charset=utf-8")
        istek.add_header("SOAPAction", "urn:barkodSorgu")
        try:
            with urllib.request.urlopen(istek, timeout=timeout) as r:
                metin = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (502, 503, 504):
                son_hata = f"PTT HTTP {e.code}"
                continue
            metin = e.read().decode("utf-8", "replace")
            veri = yaniti_coz(metin)
            return veri if veri.get("_hata") else {"_hata": f"PTT HTTP {e.code}"}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            son_hata = f"PTT'ye ulaşılamadı ({getattr(e, 'reason', e)})"
            continue
        veri = yaniti_coz(metin)
        if _yetki_reddi_mi(veri):
            raise YetkiHatasi(veri.get("aciklama") or "Kullanıcı/şifre hatalı")
        return veri
    return {"_hata": son_hata or "Bilinmeyen hata"}


def _birlestir(*parcalar):
    return " ".join(p for p in parcalar if p)


def sonuc_satiri(sira, excel_satiri, barkod, veri):
    """Bir barkodun PTT yanıtını çıktı satırına (KOLONLAR sırasıyla) çevirir.
    `veri` None ise barkod biçimi geçersizdi ve PTT'ye sorulmadı."""
    bos = [""] * 10
    if veri is None:
        return [sira, excel_satiri, barkod, *bos[:10], "Geçersiz barkod (atlandı)"]
    if veri.get("_hata"):
        return [sira, excel_satiri, barkod, veri["_hata"], *bos[:9], "Hata"]
    g = veri.get
    son_durum = g("sonIslemAciklama", "")
    kayit_var = bool(son_durum or g("kabulTarih") or g("sonIslemTarih"))
    durum = "Sorgulandı" if kayit_var else \
        f"Sonuç yok (PTT: {g('aciklama', '') or 'açıklama yok'})"
    return [
        sira, excel_satiri, barkod,
        son_durum or g("aciklama", ""),
        g("sonIslemTarih", ""), g("sonIslemIsyeri", ""),
        g("kabulTarih", ""), g("kabulIsyeri", ""),
        _birlestir(g("aliciTeslimAdSoyad"), f"({g('aliciTeslimIlgisi')})" if g("aliciTeslimIlgisi") else "",
                   g("aliciTeslimTarih")),
        _birlestir(g("gonTeslimAdSoyad"), f"({g('gonTeslimIlgisi')})" if g("gonTeslimIlgisi") else "",
                   g("gonTeslimTarih")),
        _birlestir(g("iadeTarih"), g("iadeNedeni")),
        g("aciklama", ""), g("hataKodu", ""), durum,
    ]


# ── Excel yazma ──────────────────────────────────────────────────────────────
def cikti_yolu_uret(girdi_yolu, istenen=None):
    if istenen:
        return istenen
    klasor, ad = os.path.split(os.path.abspath(girdi_yolu))
    taban = os.path.splitext(ad)[0]
    dosya = f"{taban}_PTT_Sonuc_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"
    if os.access(klasor, os.W_OK):
        return os.path.join(klasor, dosya)
    return os.path.join(os.path.expanduser("~"), dosya)


def excel_yaz(satirlar, yol):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "PTT Sonuc"
    ws.append(KOLONLAR)
    barkod_sutunu = KOLONLAR.index("Barkod") + 1
    for s in satirlar:
        ws.append(s)
        # Barkod metin: Excel 13+ haneli sayıyı bilimsel gösterime çevirmesin.
        ws.cell(row=ws.max_row, column=barkod_sutunu).number_format = "@"
    baslik_dolgu = PatternFill("solid", fgColor="1F4E78")
    for hucre in ws[1]:
        hucre.font = Font(bold=True, color="FFFFFF")
        hucre.fill = baslik_dolgu
        hucre.alignment = Alignment(vertical="center")
    genislik = {"Sıra": 6, "Excel Satırı": 11, "Barkod": 20, "PTT Durumu": 28,
                "Son İşlem Tarihi": 17, "Son İşlem İşyeri": 24, "Kabul Tarihi": 14,
                "Kabul İşyeri": 24, "Alıcı Teslim": 34, "Gönderici Teslim": 30,
                "İade": 28, "PTT Mesajı": 28, "Hata Kodu": 10, "Durum": 36}
    for i, k in enumerate(KOLONLAR, start=1):
        ws.column_dimensions[get_column_letter(i)].width = genislik[k]
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(yol)


# ── Ana iş akışı (GUI ve CLI ortak) ──────────────────────────────────────────
def isle(girdi_yolu, kimlik, cikti_yolu=None, bekleme=VARSAYILAN_BEKLEME_SN, log=print,
         ilerleme=None, durdur=None, sutun=VARSAYILAN_SUTUN):
    """Excel'i okur, barkodları sorgular, sonucu yazar; çıktı yolunu döner.
    `durdur` bir threading.Event ise set edilince kalan barkodlar atlanır ve o ana
    kadarki sonuç yine de yazılır. Aynı barkod birden çok satırdaysa PTT'ye BİR kez
    sorulur. Kimlik reddedilirse ilk satırda durur (hiçbir şey yazılmaz)."""
    kimlik.dogrula()
    kayitlar = excel_oku(girdi_yolu, sutun)
    if not kayitlar:
        raise ValueError(f"Excel'in aktif sayfasında {str(sutun).upper()} sütununda barkod bulunamadı.")
    log(f"{len(kayitlar)} barkod okundu ({str(sutun).upper()} sütunu).")

    onbellek = {}
    satirlar = []
    hatali = 0
    for sira, (excel_satiri, _ham, barkod) in enumerate(kayitlar, start=1):
        if durdur is not None and durdur.is_set():
            log("Durduruldu — o ana kadarki sonuçlar yazılıyor.")
            break
        if not BARKOD_RE.fullmatch(barkod):
            log(f"[{sira}/{len(kayitlar)}] {barkod!r}: geçersiz barkod biçimi, atlandı.")
            satirlar.append(sonuc_satiri(sira, excel_satiri, barkod, None))
            hatali += 1
        else:
            if barkod not in onbellek:
                try:
                    onbellek[barkod] = ptt_sorgula(barkod, kimlik)
                except YetkiHatasi as e:
                    if not satirlar:
                        raise ValueError(f"PTT kimlik bilgisini reddetti: {e}. "
                                         "Kullanıcı adı / müşteri no / şifreyi kontrol edin.") from None
                    log(f"PTT kimlik bilgisini reddetti ({e}) — durduruldu, o ana kadarki sonuçlar yazılıyor.")
                    break
                if bekleme > 0:
                    time.sleep(bekleme)
            veri = onbellek[barkod]
            satir = sonuc_satiri(sira, excel_satiri, barkod, veri)
            satirlar.append(satir)
            if veri.get("_hata"):
                hatali += 1
            log(f"[{sira}/{len(kayitlar)}] {barkod}: {satir[3] or satir[-1]}"
                + (f" ({satir[4]})" if satir[4] else ""))
        if ilerleme:
            ilerleme(sira, len(kayitlar))

    cikti = cikti_yolu_uret(girdi_yolu, cikti_yolu)
    excel_yaz(satirlar, cikti)
    log(f"Bitti: {len(satirlar)} satır, {hatali} hatalı/atlanan. Çıktı: {cikti}")
    return cikti


# ── Arayüz ───────────────────────────────────────────────────────────────────
def arayuz():
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk

    kok = tk.Tk()
    kok.title("PTT Barkod Sorgu")
    kok.geometry("780x560")
    kuyruk = queue.Queue()
    durdur = threading.Event()
    durum = {"cikti": None}

    yol_var = tk.StringVar()
    kul_var = tk.StringVar(value=os.environ.get("PTT_KULLANICI", ""))
    mus_var = tk.StringVar(value=os.environ.get("PTT_MUSTERI_NO", ""))
    sif_var = tk.StringVar(value=os.environ.get("PTT_SIFRE", ""))

    ust = ttk.Frame(kok, padding=10)
    ust.pack(fill="x")
    ttk.Entry(ust, textvariable=yol_var).pack(side="left", fill="x", expand=True)

    def sec():
        y = filedialog.askopenfilename(title="Barkod Excel'i seç",
                                       filetypes=[("Excel", "*.xlsx *.xlsm"), ("Tümü", "*.*")])
        if y:
            yol_var.set(y)

    ttk.Button(ust, text="Excel Seç…", command=sec).pack(side="left", padx=(8, 0))
    sut_var = tk.StringVar(value=VARSAYILAN_SUTUN)
    ttk.Label(ust, text="Barkod sütunu").pack(side="left", padx=(12, 4))
    ttk.Entry(ust, textvariable=sut_var, width=4).pack(side="left")

    kim =ttk.LabelFrame(kok, text="PTT kimlik bilgisi (diske kaydedilmez)", padding=8)
    kim.pack(fill="x", padx=10)
    for i, (etiket, var, gizli) in enumerate([("Kullanıcı", kul_var, False),
                                              ("Müşteri No", mus_var, False),
                                              ("Şifre", sif_var, True)]):
        ttk.Label(kim, text=etiket).grid(row=0, column=i * 2, padx=(0 if i == 0 else 12, 4))
        ttk.Entry(kim, textvariable=var, width=18, show="*" if gizli else "").grid(row=0, column=i * 2 + 1)

    orta = ttk.Frame(kok, padding=(10, 8))
    orta.pack(fill="x")
    sorgu_dgm = ttk.Button(orta, text="Sorgula")
    sorgu_dgm.pack(side="left")
    dur_dgm = ttk.Button(orta, text="Durdur", state="disabled", command=durdur.set)
    dur_dgm.pack(side="left", padx=6)
    ac_dgm = ttk.Button(orta, text="Sonucu Aç", state="disabled",
                        command=lambda: durum["cikti"] and os.startfile(durum["cikti"]))
    ac_dgm.pack(side="left")
    bar = ttk.Progressbar(orta, mode="determinate")
    bar.pack(side="left", fill="x", expand=True, padx=(12, 0))

    kutu = scrolledtext.ScrolledText(kok, state="disabled", height=20, font=("Consolas", 9))
    kutu.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def yaz(metin):
        kutu.configure(state="normal")
        kutu.insert("end", metin + "\n")
        kutu.see("end")
        kutu.configure(state="disabled")

    def calis():
        yol = yol_var.get().strip().strip('"')
        if not os.path.isfile(yol):
            messagebox.showwarning("PTT Barkod Sorgu", "Önce geçerli bir Excel dosyası seç.")
            return
        kimlik = Kimlik(kul_var.get().strip(), mus_var.get().strip(), sif_var.get())
        try:
            kimlik.dogrula()
            sutun = sut_var.get().strip() or VARSAYILAN_SUTUN
            sutun_indeksi(sutun)
        except ValueError as e:
            messagebox.showwarning("PTT Barkod Sorgu", str(e))
            return
        durdur.clear()
        durum["cikti"] = None
        sorgu_dgm.configure(state="disabled")
        dur_dgm.configure(state="normal")
        ac_dgm.configure(state="disabled")
        bar["value"] = 0

        def is_parcacigi():
            try:
                cikti = isle(yol, kimlik, log=lambda m: kuyruk.put(("log", m)),
                             ilerleme=lambda a, t: kuyruk.put(("ilerleme", (a, t))), durdur=durdur,
                             sutun=sutun)
                kuyruk.put(("bitti", cikti))
            except Exception as e:  # noqa: BLE001 — kullanıcıya gösterilir
                kuyruk.put(("hata", str(e)))

        threading.Thread(target=is_parcacigi, daemon=True).start()

    sorgu_dgm.configure(command=calis)

    def kuyruk_bosalt():
        try:
            while True:
                tur, veri = kuyruk.get_nowait()
                if tur == "log":
                    yaz(veri)
                elif tur == "ilerleme":
                    bar["maximum"], bar["value"] = veri[1], veri[0]
                elif tur in ("bitti", "hata"):
                    sorgu_dgm.configure(state="normal")
                    dur_dgm.configure(state="disabled")
                    if tur == "bitti":
                        durum["cikti"] = veri
                        ac_dgm.configure(state="normal")
                    else:
                        yaz(f"HATA: {veri}")
                        messagebox.showerror("PTT Barkod Sorgu", veri)
        except queue.Empty:
            pass
        kok.after(100, kuyruk_bosalt)

    kok.after(100, kuyruk_bosalt)
    kok.mainloop()


def main():
    ap = argparse.ArgumentParser(description="Excel'deki PTT barkodlarını PTT SOAP servisinde sorgular.")
    ap.add_argument("excel", nargs="?", help="Barkod içeren .xlsx (verilmezse arayüz açılır)")
    ap.add_argument("--cikti", help="Çıktı .xlsx yolu (varsayılan: girdinin yanına)")
    ap.add_argument("--bekleme", type=float, default=VARSAYILAN_BEKLEME_SN,
                    help=f"İstekler arası bekleme, saniye (varsayılan {VARSAYILAN_BEKLEME_SN})")
    ap.add_argument("--sutun", default=VARSAYILAN_SUTUN,
                    help=f"Barkodların okunduğu Excel sütunu (varsayılan {VARSAYILAN_SUTUN})")
    ap.add_argument("--kullanici", default=os.environ.get("PTT_KULLANICI", ""),
                    help="PTT kullanıcı adı (ya da PTT_KULLANICI)")
    ap.add_argument("--musteri-no", default=os.environ.get("PTT_MUSTERI_NO", ""),
                    help="PTT müşteri no (ya da PTT_MUSTERI_NO)")
    args = ap.parse_args()
    if not args.excel:
        arayuz()
        return 0
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    sifre = os.environ.get("PTT_SIFRE") or (getpass.getpass("PTT şifresi: ") if sys.stdin.isatty() else "")
    try:
        isle(args.excel, Kimlik(args.kullanici, args.musteri_no, sifre), args.cikti, args.bekleme,
             sutun=args.sutun)
    except Exception as e:  # noqa: BLE001
        print(f"HATA: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
