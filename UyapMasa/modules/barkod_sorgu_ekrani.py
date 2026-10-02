# -*- coding: utf-8 -*-
"""
Modül: Barkod Sorgu (Kapalı Tebligat — PTT)
=============================================
`barkod_sorgu.py`'nin (çekirdek mantık: PDF'ten barkod çıkarma + PTT sorgusu +
TebligatBarkod'a DB kaydı) jenerik `uretilmis_runner` YERİNE özel ekranı.
`dosyalarim_genel.py` ile AYNI desende iki bölüm sunar (kullanıcı isteği,
2026-08-03): "Dosyalarım (Tümü)" ekranındaki gibi yerel DB'den İcra
dosyalarını filtrelenip sıralanabilir bir tabloda listeler — kullanıcı satır
seçip "Seçilenleri Sorgula" ile o dosyalar için barkod_sorgu'yu tetikler
(Excel yükleme/tarih aralığı girmeye ALTERNATİF, üçüncü bir giriş yolu — bkz.
barkod_sorgu.calistir'in "seçili dosyalar modu"). Alt bölümde ise DB'ye daha
önce yazılmış TebligatBarkod sonuçları (barkod/PTT durumu/mazbata) bir geçmiş
raporu olarak gösterilir.

`icra_dosyalarim.py`/`dosyalarim_genel.py` AYRIMINDAKİ AYNI karar (paralel
yaz, ortak soyutlamaya zorlama) burada da geçerli — bu dosya kendi başına,
`dosyalarim_genel.py`'nin sütun-başlığı canlı filtre/sıralama deseni
KOPYALANARAK (paylaşılan bir taban sınıfa çıkarılmadan) uygulanmıştır.
"""

import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from theme import C, RoundButton
from . import dosya_core, barkod_sorgu, teblig_21_2_core, teblig_21_2_gonder, normal_tebligat_gonder
from . import birim_bolme
from . import dosya_karti
from . import tur_eslestirme

# BARE (paket-göreli DEĞİL) — toplu_is_kontrol/toplu_is_dialog'un TÜM
# tüketicilerde AYNI tekil (KAYIT_DEFTERI) nesneyi görmesi için şart: bu iki
# modül HER YERDE bare import edilir (bkz. toplu_is_kontrol.py başlığı).
_HERE_TIK = os.path.dirname(os.path.abspath(__file__))
if _HERE_TIK not in sys.path:
    sys.path.insert(0, _HERE_TIK)
import toplu_is_kontrol  # noqa: E402
import toplu_is_dialog as _tid  # noqa: E402

_ACILIS_TARIHI_RE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")
_EVRAK_TARIHI_RE = re.compile(r"^(\d{2})[./](\d{2})[./](\d{4})$")
_TARIH_SAAT_RE = re.compile(r"(\d{2})[./](\d{2})[./](\d{4})(?:[ T](\d{2}):(\d{2}))?")


def _evrak_tarihi_anahtari(tarih):
    """'GG/AA/YYYY' ya da 'GG.AA.YYYY' -> sıralanabilir 'YYYYAAGG'. Ayrıştırılamazsa
    boş dize döner (en küçük/en eski sayılır — bkz. `_dosya_ici_sira`)."""
    m = _EVRAK_TARIHI_RE.match(str(tarih or "").strip())
    if not m:
        return ""
    return f"{m.group(3)}{m.group(2)}{m.group(1)}"


def _tarih_saat_anahtari(deger):
    """'GG/AA/YYYY[ SS:DD]' ya da 'GG.AA.YYYY[ SS:DD]' -> sıralanabilir
    'YYYYAAGGSSDD'. `barkod_sorgu.py`'nin ürettiği "Tebliğ/İade Tarihi"/"Son
    İşlem Tarihi"/"Sorgu Zamanı" sütunları GG/AA/YYYY (gün önce) biçiminde
    geldiğinden düz metin sıralaması AY'ı yok sayıp yalnız GÜN basamağına
    bakıyordu (kullanıcı bulgusu, 2026-08-03: eskiden-yeniye sıralamada
    '01/07' '25/02'den önceymiş gibi görünüyordu — ASCII sıralamada '0' < '2'
    olduğundan gün ay'dan bağımsız karşılaştırılıyordu). `re.search` kullanır
    (yalnız `re.match` DEĞİL) — böylece metnin BAŞINDA olması gerekmez,
    sondaki fazladan metni de tolere eder. Ayrıştırılamazsa boş dize döner
    (en küçük/en eski sayılır — `_evrak_tarihi_anahtari` ile AYNI disiplin)."""
    m = _TARIH_SAAT_RE.search(str(deger or ""))
    if not m:
        return ""
    gg, aa, yyyy, ss, dd = m.groups()
    return f"{yyyy}{aa}{gg}{ss or '00'}{dd or '00'}"


# 21/2 rapor penceresi — sonuç grupları (süzgeç + özet, 2026-09-24).
_T212_GRUPLARI = ("Hatalı", "Elle inceleyin", "21/2 için uygun", "Farklı yöntem", "Gönderilmiş", "Diğer")


def _t212_grup(kategori):
    k = kategori or ""
    if k.startswith("hata"):
        return "Hatalı"
    if k.startswith("elle inceleyin"):
        return "Elle inceleyin"
    if k == "21/2 için uygun":
        return "21/2 için uygun"
    if k.startswith("farklı yöntem"):
        return "Farklı yöntem"
    if "gönderil" in k:
        return "Gönderilmiş"
    return "Diğer"


def _gonderim_kalemleri(satirlar):
    """21/2 / normal tebligat raporu satırlarından gönderim kalemleri — BORÇLU
    BAŞINA tek kalem (2026-09-24: aynı dosya+borçlu iki kez seçilirse çift ücret
    riski vardı). `birimId`/`borcluTckn` ofis tarafında dosyayı BİRİMLE bulmak ve
    borçluyu kesin seçmek için taşınır (bkz. uyap_core.teblig_212.gonder.prepare)."""
    gorulen, out = set(), []
    for s in satirlar:
        anahtar = (s.get("birimId") or s.get("birim"), s.get("dosyaNo"),
                   s.get("borcluTckn") or s.get("borclu"))
        if anahtar in gorulen:
            continue
        gorulen.add(anahtar)
        out.append({"birim": s.get("birim", ""), "birimId": s.get("birimId", ""),
                    "dosyaNo": s.get("dosyaNo", ""), "borclu": s.get("borclu", ""),
                    "borcluTckn": s.get("borcluTckn", "")})
    return out


def _dosya_ici_sira(g):
    """Bir dosyanın barkodlarını kronolojik (eskiden yeniye) dizmek için
    anahtar: evrak tarihi, eşitse birim evrak no. `g` bir TebligatBarkod
    satırı sözlüğüdür (bkz. dosya_core.tebligat_barkod_gecmisi_listele)."""
    try:
        no = int(g.get("birimEvrakNo") or 0)
    except (TypeError, ValueError):
        no = 0
    return (_evrak_tarihi_anahtari(g.get("evrakTarihi")), no)


def _dosyaya_gore_grupla(satirlar, grup_fn, ic_fn):
    """Kullanıcı isteği (2026-09-24): bir dosyanın TÜM barkodları art arda
    gelsin, dosyalar birbirine karışmasın. `satirlar` hangi sırayla
    geldiyse (ör. bir sütuna göre sıralanmış) dosyalar İLK göründükleri
    sırayla dizilir; her dosyanın kendi satırları `ic_fn` sırasıyla
    (kronolojik) bir arada kalır."""
    sira = {}
    for r in satirlar:
        sira.setdefault(grup_fn(r), len(sira))
    return sorted(satirlar, key=lambda r: (sira[grup_fn(r)], ic_fn(r)))


class BarkodSorguEkrani:
    # ── Bölüm 1: dosya seçici (yalnız İcra — dosyalarim_genel'in İcra-daraltılmış hâli) ──
    # Kullanıcı bulgusu (2026-08-03, dördüncü tur): dosya seçip sorguladıktan
    # sonra sonucu görmek için ayrıca aşağıdaki "Geçmiş Sonuçlar" bölümüne
    # bakmak gerekiyordu — bu sütunlar artık BURADA da gösterilir (2026-09-24'ten
    # beri dosyanın HER barkodu ayrı satır, bkz. `_barkod_satirlarina_ac`).
    # "Tebligat Türü" (beşinci tur) barkod_sorgu._tebligat_turunu_belirle
    # ile sınıflandırılır (Ödeme Emri/Banka/Maaş/103 Davetiyesi/...).
    KOLONLAR = [
        ("birimAdi", "İcra Dairesi", 220),
        ("dosyaNo", "Dosya No", 90),
        ("dosyaTur", "Dosya Türü", 110),
        ("dosyaDurum", "Durum", 70),
        ("acilisTarihi", "Açılış Tarihi", 90),
        ("alacakli", "Alacaklı", 200),
        ("borclu", "Borçlu / Muhatap", 200),
        ("kesinlesme", "Kesinleşme", 120),
        ("barkod", "Barkod No", 110),
        ("tebligatTuru", "Tebligat Türü", 160),
        ("pttDurumu", "Tebliğ / Son Durum", 180),
        ("sonIslemTarihi", "Tebliğ/İade Tarihi", 110),
        ("tebligMazbatasiVar", "Mazbata Döndü mü", 110),
        ("yenidenTebligTalebi", "Yeniden Tebliğ Talebi", 130),
    ]
    HEAD_H = 52

    # ── Bölüm 2: geçmiş TebligatBarkod sonuçları ──
    GECMIS_KOLONLAR = [
        ("birimAdi", "İcra Dairesi", 200),
        ("dosyaNo", "Dosya No", 90),
        ("muhatap", "Muhatap", 200),
        ("barkod", "Barkod", 120),
        ("tebligatTuru", "Tebligat Türü", 160),
        ("elektronikTebligat", "Elektronik mi", 90),
        ("pttDurumu", "PTT Durumu", 200),
        ("sonIslemTarihi", "Son İşlem Tarihi", 110),
        ("tebligMazbatasiVar", "Tebliğ Mazbatası", 100),
        ("kapaliTebligMazbatasiVar", "Kapalı Mazbata", 100),
        ("yenidenTebligTalebi", "Yeniden Tebliğ Talebi", 130),
        ("sorguZamani", "Sorgu Zamanı", 130),
    ]
    GECMIS_HEAD_H = 30

    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.kontrol = toplu_is_kontrol.TopluIsKontrolu(ad="Barkod Sorgulama")
        self.result_q = queue.Queue()

        self.kayitlar_ham = []   # Bölüm 1: DB'den gelen (sunucu filtreli) TAM liste
        self.kayitlar = []       # Bölüm 1: ekrandaki (yerel filtre+sıralama uygulanmış) liste
        self._by_uid = {}        # Bölüm 1: _uid -> kayıt (Treeview iid'si buradan üretilir)
        self.col_vars = {k: tk.StringVar() for k, _l, _w in self.KOLONLAR}
        self.col_combos = {}
        self.col_labels = {}
        self.sort_key = None
        self.sort_reverse = False

        self.gecmis_ham = []     # Bölüm 2
        self.gecmis = []
        self.gecmis_sort_key = None
        self.gecmis_sort_reverse = False

        self.birim_var = tk.StringVar(value="Tümü")
        self.durum_var = tk.StringVar(value="Tümü")
        self.tarih_bas_var = tk.StringVar()
        self.tarih_bit_var = tk.StringVar()
        self.alacakli_var = tk.StringVar()
        self.borclu_var = tk.StringVar()
        self._birim_kod = {}
        self._durum_kod = {}

        self._calisiyor = False

        self._init_style()
        self._build()
        threading.Thread(target=self._alanlar_yukle_bg, daemon=True).start()
        self.app.after(200, self._poll)
        # 2026-09-25: 20 sn'lik otomatik DB tazelemesi kullanıcı kararıyla KALDIRILDI.

    # ─────────────────────────── ttk stilleri ───────────────────────────
    def _init_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Barkod.Treeview", background=C.CARD, fieldbackground=C.CARD,
                        foreground=C.INK, bordercolor=C.CARD_EDGE, borderwidth=0,
                        rowheight=26, font=self.app.f_body)
        style.map("Barkod.Treeview",
                  background=[("selected", C.SAGE_TINT)], foreground=[("selected", C.INK)])

    # ─────────────────────────── arayüz ───────────────────────────
    def _build(self):
        wrap = tk.Frame(self.parent, bg=C.BG)
        wrap.pack(fill="both", expand=True, padx=40, pady=(28, 20))

        tk.Label(wrap, text="Barkod Sorgu (Kapalı Tebligat — PTT)", bg=C.BG, fg=C.INK,
                 font=self.app.f_h1).pack(anchor="w")
        tk.Label(wrap, text="Aşağıdan İcra dosyalarınızı (yerel veritabanından, "
                            "\"Dosyalarım (Tümü)\" ile aynı kaynaktan) filtreleyin. "
                            "\"Tümünü Sorgula\" filtrelenmiş listedeki TÜM dosyaları tek "
                            "seferde sorgular — tek tek satır seçmenize gerek yok. Belirli "
                            "dosyaları seçip sorgulamak isterseniz Ctrl/Shift ile satır(lar) "
                            "seçip \"Seçilenleri Sorgula\"yı kullanın. Her iki yolda da her "
                            "dosyadaki 'Kapalı Tebligat' evrakının barkodu PTT'de sorgulanır "
                            "ve sonuç veritabanına kaydedilir.",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_sub, wraplength=820,
                 justify="left").pack(anchor="w", pady=(6, 14))

        # ── Bölüm 1: filtre çubuğu ──
        ust = tk.Frame(wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        ust.pack(fill="x")
        ui = tk.Frame(ust, bg=C.CARD)
        ui.pack(fill="x", padx=18, pady=12)

        self.birim_cb = self._combo(ui, "İcra Dairesi", self.birim_var, ["Tümü"])
        self.birim_cb.bind("<<ComboboxSelected>>", lambda e: self._filtrele())
        self.durum_cb = self._combo(ui, "Durum", self.durum_var, ["Tümü"])
        self.durum_cb.bind("<<ComboboxSelected>>", lambda e: self._filtrele())

        tk.Label(ui, text="Açılış Tarihi", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(0, 6))
        self._tarih_entry(ui, self.tarih_bas_var)
        tk.Label(ui, text="–", bg=C.CARD, fg=C.INK_FAINT, font=self.app.f_body).pack(side="left", padx=4)
        self._tarih_entry(ui, self.tarih_bit_var)

        tk.Label(ui, text="Alacaklı", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(18, 6))
        alacakli_e = tk.Entry(ui, textvariable=self.alacakli_var, bg="#FFFFFF", fg=C.INK,
                              relief="flat", insertbackground=C.INK, font=self.app.f_body,
                              width=16, highlightthickness=1, highlightbackground=C.LINE,
                              highlightcolor=C.SAGE)
        alacakli_e.pack(side="left", ipady=3)
        alacakli_e.bind("<Return>", lambda ev: self._filtrele())

        tk.Label(ui, text="Borçlu", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(18, 6))
        borclu_e = tk.Entry(ui, textvariable=self.borclu_var, bg="#FFFFFF", fg=C.INK,
                            relief="flat", insertbackground=C.INK, font=self.app.f_body,
                            width=16, highlightthickness=1, highlightbackground=C.LINE,
                            highlightcolor=C.SAGE)
        borclu_e.pack(side="left", ipady=3)
        borclu_e.bind("<Return>", lambda ev: self._filtrele())

        bar = tk.Frame(wrap, bg=C.BG)
        bar.pack(fill="x", pady=(12, 0))
        self._btn(bar, "Filtrele", self._filtrele, "primary").pack(side="left", ipadx=6)
        self._btn(bar, "Temizle", self._temizle, "ghost").pack(side="left", padx=(8, 0), ipadx=2)
        # 2026-09-30: İcra Dosyalarım / Tüm Dosyalarım ile AYNI dosya kartı (dosya_karti.py).
        self.detay_btn = self._btn(bar, "Dosya Görüntüle", self._dosya_goruntule, "ghost")
        self.detay_btn.pack(side="left", padx=(8, 0), ipadx=2)

        # Üç işlem düğmesi (kullanıcı isteği, 2026-09-23) — ayrı satırda; seçili
        # dosyalar üzerinde, seçim yoksa (onayla) filtrelenmiş listenin tümünde.
        islem_bar = tk.Frame(wrap, bg=C.BG)
        islem_bar.pack(fill="x", pady=(8, 0))
        tk.Label(islem_bar, text="Seçili dosyalar (seçim yoksa listedekilerin tümü):",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_small).pack(side="left", padx=(0, 8))
        self.islem_btnlari = []
        for etiket, mod, tur in (("UYAP'tan Tebligat Bilgisi Çek", barkod_sorgu.MOD_UYAP, "ghost"),
                                 ("Barkodları PTT'de Sorgula", barkod_sorgu.MOD_PTT, "ghost"),
                                 ("İkisini Birden Yap", barkod_sorgu.MOD_IKISI, "primary")):
            b = self._btn(islem_bar, etiket, lambda m=mod: self._islem_baslat(m), tur)
            b.pack(side="left", padx=(0, 8), ipadx=4)
            self.islem_btnlari.append(b)
        # 2026-09-30: üç ekranda AYNI düğme (bkz. icra_dosyalarim / dosyalarim_genel) —
        # YALNIZ seçili dosyalar; taraflar da tazelenir.
        b = self._btn(islem_bar, "Seçilenleri UYAP'tan Güncelle", self._secilenleri_guncelle, "ghost")
        b.pack(side="left", padx=(0, 8), ipadx=4)
        self.islem_btnlari.append(b)
        # Dosya seçip TOPLU 21/2 uygunluk taraması (kullanıcı isteği,
        # 2026-08-13 — "barkod sorgu menüsünde dosya seçerek toplu 21/2 talep
        # etme butonu"): YALNIZ tarar/raporlar, hiçbir talep GÖNDERMEZ/ÖDEME
        # YAPMAZ (bkz. teblig_21_2_core.py modül başlığı). Yukarıdaki dosya
        # seçici tablodan (Bölüm 1) seçilen satırlar üzerinde çalışır —
        # Geçmiş bölümündeki (Bölüm 2, aşağıda) aynı işlevin ayrı bir girişi.
        self.t212_btn1 = self._btn(bar, "21/2 Uygunluk Taraması (Seçili Dosyalar)",
                                    self._t212_tara_bolum1, "ghost")
        self.t212_btn1.pack(side="left", padx=(8, 0), ipadx=2)
        # Kullanıcı bulgusu (2026-08-13): tarama sonucu penceresi kapatılınca
        # tekrar görmek için sıfırdan sorgu gerekiyordu — sonuçlar artık HER
        # taramada veritabanına da yazılıyor (bkz. teblig_21_2_core.
        # son_tarama_sonuclarini_getir), bu buton yeniden sorgu ATMADAN,
        # DB'den okuyarak açar; program yeniden başlasa bile kaybolmaz.
        self.t212_rapor_btn1 = self._btn(bar, "Son 21/2 Raporu", self._t212_son_raporu_goster, "ghost")
        self.t212_rapor_btn1.pack(side="left", padx=(8, 0), ipadx=2)
        # Şablon dışı tebligat türlerini kullanıcıya sorar (2026-09-25).
        self._btn(bar, "Tür Eşleştir", self._tur_eslestir_penceresi, "ghost").pack(
            side="left", padx=(8, 0), ipadx=2)
        self.duraklat_btn = self._btn(bar, "Duraklat", self.duraklat_toggle, "ghost")
        self.duraklat_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.duraklat_btn.set_state("disabled")
        self.durdur_btn = self._btn(bar, "Durdur", self.durdur, "ghost")
        self.durdur_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.durdur_btn.set_state("disabled")
        self.excel_btn = self._btn(bar, "Excel'e Aktar", self._excel_aktar, "ghost")
        self.excel_btn.pack(side="left", padx=(8, 0), ipadx=2)
        # "İzmir 10. İcra Dairesi" → Yer | No | Birim Türü (kullanıcı isteği, 2026-09-24).
        self.daire_bol_btn = self._btn(bar, "Daireyi Böl ⇄", self._daire_bol_toggle, "ghost")
        self.daire_bol_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.durum_lbl = tk.Label(bar, text="", bg=C.BG, fg=C.INK_SOFT, font=self.app.f_small)
        self.durum_lbl.pack(side="right")

        card = tk.Frame(wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        card.pack(fill="both", expand=True, pady=(14, 0))
        self.head_canvas = tk.Canvas(card, bg=C.SIDEBAR, highlightthickness=0, height=self.HEAD_H)
        self.head_inner = tk.Frame(self.head_canvas, bg=C.SIDEBAR)
        self.head_win = self.head_canvas.create_window((0, 0), window=self.head_inner, anchor="nw")
        self.head_canvas.grid(row=0, column=0, sticky="ew")

        cols = [k for k, _l, _w in self.KOLONLAR]
        self.tree = ttk.Treeview(card, columns=cols, show="", height=9,
                                 selectmode="extended", style="Barkod.Treeview")
        self.tree.grid(row=1, column=0, sticky="nsew")
        ysb = tk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        ysb.grid(row=1, column=1, sticky="ns")
        xsb = tk.Scrollbar(card, orient="horizontal")
        xsb.grid(row=2, column=0, sticky="ew")

        def _xset(first, last):
            xsb.set(first, last)
            try:
                self.head_canvas.xview_moveto(float(first))
            except Exception:
                pass
        xsb.configure(command=self.tree.xview)
        self.tree.configure(yscrollcommand=ysb.set, xscrollcommand=_xset)
        card.rowconfigure(1, weight=1)
        card.columnconfigure(0, weight=1)
        self._basligi_kur()
        # Çift tık = "Dosya Görüntüle" (diğer iki ekranla aynı).
        self.tree.bind("<Double-1>", lambda e: self._dosya_goruntule())

        # ── günlük ──
        lbox = tk.Frame(wrap, bg="#FBFAF7", highlightbackground=C.CARD_EDGE, highlightthickness=1)
        lbox.pack(fill="x", pady=(10, 0))
        self.log = tk.Text(lbox, bg="#FBFAF7", fg=C.INK, relief="flat",
                           font=self.app.f_mono, wrap="word", height=4,
                           padx=12, pady=6, state="disabled", highlightthickness=0)
        self.log.pack(side="left", fill="both", expand=True)
        lsb = tk.Scrollbar(lbox, command=self.log.yview)
        lsb.pack(side="right", fill="y")
        self.log.config(yscrollcommand=lsb.set)

        # ── Bölüm 2: geçmiş sonuçlar ──
        tk.Label(wrap, text="Geçmiş Barkod Sorgu Sonuçları", bg=C.BG, fg=C.INK,
                 font=self.app.f_card_t).pack(anchor="w", pady=(20, 4))
        tk.Label(wrap, text="Veritabanına daha önce kaydedilmiş sonuçlar (barkod/PTT durumu/mazbata).",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_small).pack(anchor="w", pady=(0, 8))

        gbar = tk.Frame(wrap, bg=C.BG)
        gbar.pack(fill="x")
        self.gecmis_yenile_btn = self._btn(gbar, "Geçmişi Yenile", self._gecmis_yenile, "ghost")
        self.gecmis_yenile_btn.pack(side="left", ipadx=2)
        # 21/2 şerhli yeniden tebliğ talebi UYGUNLUK TARAMASI (kullanıcı isteği,
        # 2026-08-13) — YALNIZ tarar/raporlar, hiçbir talep GÖNDERMEZ/ÖDEME
        # YAPMAZ (bkz. teblig_21_2_core.py modül başlığı). Seçili "İADE"
        # satırları üzerinde çalışır.
        self.t212_btn = self._btn(gbar, "21/2 Uygunluk Taraması", self._t212_tara, "ghost")
        self.t212_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.gecmis_durum_lbl = tk.Label(gbar, text="", bg=C.BG, fg=C.INK_SOFT, font=self.app.f_small)
        self.gecmis_durum_lbl.pack(side="right")

        gcard = tk.Frame(wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        gcard.pack(fill="both", expand=True, pady=(8, 0))
        gcols = [k for k, _l, _w in self.GECMIS_KOLONLAR]
        self.gecmis_tree = ttk.Treeview(gcard, columns=gcols, show="headings", height=8,
                                        style="Barkod.Treeview")
        for k, lbl, w in self.GECMIS_KOLONLAR:
            self.gecmis_tree.heading(k, text=lbl, command=lambda col=k: self._gecmis_sirala(col))
            self.gecmis_tree.column(k, width=w, anchor="w", stretch=False)
        self.gecmis_tree.grid(row=0, column=0, sticky="nsew")
        gysb = tk.Scrollbar(gcard, orient="vertical", command=self.gecmis_tree.yview)
        gysb.grid(row=0, column=1, sticky="ns")
        gxsb = tk.Scrollbar(gcard, orient="horizontal", command=self.gecmis_tree.xview)
        gxsb.grid(row=1, column=0, sticky="ew")
        self.gecmis_tree.configure(yscrollcommand=gysb.set, xscrollcommand=gxsb.set)
        self.gecmis_tree.bind("<Double-1>", lambda e: self._gecmis_dosya_goruntule())
        gcard.rowconfigure(0, weight=1)
        gcard.columnconfigure(0, weight=1)

    def _combo(self, parent, label, var, values):
        tk.Label(parent, text=label, bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(0, 6))
        cb = ttk.Combobox(parent, textvariable=var, state="readonly", values=values,
                          font=self.app.f_body, width=18)
        cb.pack(side="left", padx=(0, 18))
        return cb

    def _tarih_entry(self, parent, var):
        e = tk.Entry(parent, textvariable=var, bg="#FFFFFF", fg=C.INK, relief="flat",
                    insertbackground=C.INK, font=self.app.f_body, width=11,
                    highlightthickness=1, highlightbackground=C.LINE, highlightcolor=C.SAGE)
        e.pack(side="left", ipady=3)
        e.bind("<Return>", lambda ev: self._filtrele())
        return e

    def _btn(self, parent, text, cmd, kind):
        return RoundButton(parent, text, command=cmd, kind=kind, font=self.app.f_nav_b, height=36)

    # ─────────────────────────── Bölüm 1: başlık şeridi (canlı filtre + sıralama) ──
    # dosyalarim_genel.py'deki AYNI desen (bkz. modül başlığı — bilerek kopyalandı).
    def _basligi_kur(self):
        for w in self.head_inner.winfo_children():
            w.destroy()
        self.col_combos = {}
        self.col_labels = {}
        total = 0
        for i, (k, lbl, w) in enumerate(self.KOLONLAR):
            total += w
            cell = tk.Frame(self.head_inner, bg=C.SIDEBAR, width=w, height=self.HEAD_H)
            cell.grid(row=0, column=i, sticky="nsew")
            cell.grid_propagate(False)
            cell.columnconfigure(0, weight=1)

            baslik = tk.Label(cell, text=lbl, bg=C.SIDEBAR, fg=C.INK,
                              font=self.app.f_nav_b, anchor="w", cursor="hand2")
            baslik.grid(row=0, column=0, sticky="ew", padx=6, pady=(5, 2))
            baslik.bind("<Button-1>", lambda e, col=k: self._sirala(col))
            self.col_labels[k] = (baslik, lbl)

            cb = ttk.Combobox(cell, textvariable=self.col_vars[k], state="normal",
                              font=self.app.f_body, width=4)
            cb.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
            cb.bind("<KeyRelease>", lambda ev: self._yerel_uygula())
            cb.bind("<<ComboboxSelected>>", lambda ev: self._yerel_uygula())
            cb.bind("<Return>", lambda ev: self._yerel_uygula())
            self.col_combos[k] = cb

            self.tree.column(k, width=w, anchor="w", stretch=False)
        self._sirala_etiket_guncelle()
        self.head_canvas.itemconfigure(self.head_win, width=total, height=self.HEAD_H)
        self.head_canvas.configure(scrollregion=(0, 0, total, self.HEAD_H))

    def _yerel_filtreleri_sifirla(self):
        for var in self.col_vars.values():
            var.set("")
        self.sort_key = None
        self.sort_reverse = False
        self._sirala_etiket_guncelle()

    def _sirala_etiket_guncelle(self):
        for k, (widget, taban) in self.col_labels.items():
            ok = ""
            if k == self.sort_key:
                ok = "  ▲" if not self.sort_reverse else "  ▼"
            widget.config(text=taban + ok)

    def _sirala(self, col):
        if self.sort_key == col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_key = col
            self.sort_reverse = False
        self._sirala_etiket_guncelle()
        self._yerel_uygula()

    def _combolari_doldur(self):
        for k, _l, _w in self.KOLONLAR:
            cb = self.col_combos.get(k)
            if cb is None:
                continue
            degerler = sorted({str(r.get(k, "") or "") for r in self.kayitlar_ham} - {""},
                               key=lambda s: dosya_core.tr_lower(s))
            cb["values"] = degerler

    def _yerel_kriterler(self):
        out = {}
        for k, _l, _w in self.KOLONLAR:
            cb = self.col_combos.get(k)
            if cb is not None:
                q = dosya_core.tr_lower(cb.get().strip())
                if q:
                    out[k] = q
        return out

    def _yerel_uygula(self):
        kayitlar = self.kayitlar_ham
        kriter = self._yerel_kriterler()
        if kriter:
            def uyar(r):
                for k, q in kriter.items():
                    if q not in dosya_core.tr_lower(str(r.get(k, "") or "")):
                        return False
                return True
            kayitlar = [r for r in kayitlar if uyar(r)]
        if self.sort_key:
            col = self.sort_key
            if col == "acilisTarihi":
                def gecerli_mi(r):
                    m = _ACILIS_TARIHI_RE.match(str(r.get(col, "") or "").strip())
                    return f"{m.group(3)}{m.group(2)}{m.group(1)}" if m else None
            elif col == "sonIslemTarihi":
                def gecerli_mi(r):
                    return _tarih_saat_anahtari(r.get(col, "")) or None
            else:
                def gecerli_mi(r):
                    v = str(r.get(col, "") or "").strip()
                    if not v:
                        return None
                    try:
                        return (0, float(v.replace(",", ".")))
                    except ValueError:
                        return (1, dosya_core.tr_lower(v))
            gecerliler, gecersizler = [], []
            for r in kayitlar:
                a = gecerli_mi(r)
                (gecersizler if a is None else gecerliler).append((a, r))
            gecerliler.sort(key=lambda p: p[0], reverse=self.sort_reverse)
            kayitlar = [r for _a, r in gecerliler] + [r for _a, r in gecersizler]
            # Sıralama DOSYALARIN sırasını belirler; bir dosyanın barkodları
            # bölünmez, kendi içinde kronolojik kalır (kullanıcı isteği, 2026-09-24).
            kayitlar = _dosyaya_gore_grupla(kayitlar, lambda r: r["_grup"], lambda r: r["_grup_ici"])

        self.kayitlar = kayitlar
        secili_iid = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for rec in kayitlar:
            vals = [rec.get(k, "") for k, _l, _w in self.KOLONLAR]
            iid = str(rec["_uid"])
            self.tree.insert("", "end", iid=iid, values=vals)
            if iid in secili_iid:
                self.tree.selection_add(iid)
        toplam = len(self.kayitlar_ham)
        toplam_dosya = len({r["_grup"] for r in self.kayitlar_ham})
        gorunen_dosya = len({r["_grup"] for r in kayitlar})
        if not toplam:
            self.durum_lbl.config(text="")
        elif len(kayitlar) != toplam:
            self.durum_lbl.config(text=f"{gorunen_dosya} / {toplam_dosya} dosya · "
                                       f"{len(kayitlar)} / {toplam} satır")
        else:
            self.durum_lbl.config(text=f"{toplam_dosya} dosya · {toplam} satır")

    # ─────────────────────────── Bölüm 2: sıralama ───────────────────────────
    def _gecmis_sirala(self, col):
        if self.gecmis_sort_key == col:
            self.gecmis_sort_reverse = not self.gecmis_sort_reverse
        else:
            self.gecmis_sort_key = col
            self.gecmis_sort_reverse = False
        self._gecmis_uygula()

    def _gecmis_uygula(self):
        kayitlar = list(self.gecmis_ham)
        if self.gecmis_sort_key:
            col = self.gecmis_sort_key
            if col in ("sorguZamani", "sonIslemTarihi"):
                def anahtar(r):
                    return _tarih_saat_anahtari(r.get(col, ""))
            else:
                def anahtar(r):
                    return dosya_core.tr_lower(str(r.get(col, "") or ""))
            kayitlar.sort(key=anahtar, reverse=self.gecmis_sort_reverse)
        # Aynı dosyanın barkodları bir arada ve kronolojik (kullanıcı isteği,
        # 2026-09-24); sıralama yalnız DOSYALARIN sırasını belirler.
        kayitlar = _dosyaya_gore_grupla(kayitlar, lambda r: r.get("dbId"), _dosya_ici_sira)
        self.gecmis = kayitlar
        self.gecmis_tree.delete(*self.gecmis_tree.get_children())
        for i, rec in enumerate(kayitlar):
            vals = [rec.get(k, "") for k, _l, _w in self.GECMIS_KOLONLAR]
            self.gecmis_tree.insert("", "end", iid=str(i), values=vals)
        for k, lbl, _w in self.GECMIS_KOLONLAR:
            ok = ""
            if k == self.gecmis_sort_key:
                ok = "  ▲" if not self.gecmis_sort_reverse else "  ▼"
            self.gecmis_tree.heading(k, text=lbl + ok)
        self.gecmis_durum_lbl.config(text=f"{len(kayitlar)} sonuç" if kayitlar else "")

    # ─────────────────────────── günlük ───────────────────────────
    def _log(self, mesaj):
        self.result_q.put(("log", str(mesaj)))

    def _append_log(self, text):
        if not self.log.winfo_exists():
            return
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    # ─────────────────────────── veri yükleme: Bölüm 1 ───────────────────────────
    def _alanlar_yukle_bg(self):
        try:
            birimler = dosya_core.mahkeme_secenekleri(dosya_core.YARGI_TURU_ICRA, None)
            durumlar = dosya_core.dosya_durum_secenekleri()
        except Exception as e:
            self._log(f"⚠️ Alanlar yüklenemedi: {e}")
            birimler, durumlar = [], []
        self.result_q.put(("alanlar", (birimler, durumlar)))
        self._filtrele_bg({})
        self._gecmis_yukle_bg()

    def _filtreleri_topla(self):
        f = {"yargi_turu": dosya_core.YARGI_TURU_ICRA}
        birim_ad = self.birim_var.get()
        if birim_ad != "Tümü":
            f["mahkeme_id"] = self._birim_kod.get(birim_ad)
        durum_ad = self.durum_var.get()
        if durum_ad != "Tümü":
            f["durum_kod"] = self._durum_kod.get(durum_ad)
        if self.tarih_bas_var.get().strip():
            f["tarih_baslangic"] = self.tarih_bas_var.get().strip()
        if self.tarih_bit_var.get().strip():
            f["tarih_bitis"] = self.tarih_bit_var.get().strip()
        if self.alacakli_var.get().strip():
            f["alacakli_adi"] = self.alacakli_var.get().strip()
        if self.borclu_var.get().strip():
            f["borclu_adi"] = self.borclu_var.get().strip()
        return f

    def _filtrele(self):
        self.durum_lbl.config(text="Yükleniyor…")
        self._filtrele_bg(self._filtreleri_topla())

    def ekran_gosterildi(self):
        """Menüden bu ekrana dönülünce listeyi DB'den bir kez tazeler (bkz. uyapmasa.select)."""
        self._filtrele()

    def _filtrele_bg(self, filtreler):
        def bg():
            try:
                kayitlar = dosya_core.dosyalarim_db_listele(filtreler)
            except Exception as e:
                self._log(f"⚠️ Liste okunamadı: {e}")
                kayitlar = []
            self.result_q.put(("kayitlar", self._barkod_satirlarina_ac(kayitlar)))
        threading.Thread(target=bg, daemon=True).start()

    # Dosya satırına barkod başına eklenen sütunlar (KOLONLAR'daki barkod alanları).
    _BARKOD_ALANLARI = ("barkod", "tebligatTuru", "pttDurumu", "sonIslemTarihi",
                        "tebligMazbatasiVar", "yenidenTebligTalebi")

    def _barkod_satirlarina_ac(self, kayitlar):
        """Dosya listesini BARKOD BAŞINA BİR SATIR olacak şekilde açar ve YENİ
        bir liste döner (veritabanına yazmaz, yalnız okur).

        Kullanıcı isteği (2026-09-24): "bir dosyanın kaç tebligatı olursa olsun
        tek bir satır ve tek bir barkod çıkıyor; Excel'e de eksik gidiyor —
        bir dosyada kaç barkod varsa hepsi listelensin, bir dosyanın tüm
        barkodları sıralansın sonra diğer dosyaya geçsin." Eskiden
        (`_barkod_ozetini_birlestir`) dosya başına YALNIZ evrak tarihi en yeni
        kayıt gösteriliyordu; üstelik kaynak `tebligat_barkod_gecmisi_listele`
        varsayılan 3000 satır sınırıyla çağrıldığından (14 bin+ satır varken)
        barkodu olan birçok dosya BOŞ görünüyordu — artık limit=None.

        Her dosyanın barkodları kronolojik (evrak tarihi, sonra birim evrak
        no — bkz. `_dosya_ici_sira`) dizilir; barkodu olmayan dosya barkod
        sütunları boş TEK satır olarak kalır. Eşleştirme kalıcı `dbId`
        (Dosya.pk) ile yapılır — `dosyaId` oturumluktur. Her satır `_dosya`
        (işlemlere gönderilecek ÖZGÜN dosya kaydı), `_grup` (dosya sırası) ve
        `_grup_ici` (dosya içi sıra) taşır.

        BORÇLU BAŞINA (kullanıcı isteği, 2026-09-24: "her borçlu için tebliğ,
        iade ve kesinleşme ayrı önem taşıyor"): bir dosyada önce her borçlunun
        tebligatları (o borçlunun Kesinleşme'siyle), sonra hiçbir borçluya
        bağlanmayan tebligatlar (banka/işveren/vekil… — "Borçlu / Muhatap"
        sütununda muhatap yazar) listelenir. Tebligatı olmayan borçlu da tek
        satırla görünür. Eşleşme: TebligatBarkod.borclu (bkz.
        dosya_core.tebligat_borclusunu_bul)."""
        if not kayitlar:
            return []
        gecmis = dosya_core.tebligat_barkod_gecmisi_listele(limit=None, log_fn=self._log)
        dosya_barkodlari = {}
        for g in gecmis:
            dosya_barkodlari.setdefault(g.get("dbId"), []).append(g)
        borclu_haritasi = dosya_core.dosya_borclulari_haritasi()
        satirlar = []
        for grup, r in enumerate(kayitlar):
            barkodlar = sorted(dosya_barkodlari.get(r.get("dbId"), []), key=_dosya_ici_sira)
            borclular = borclu_haritasi.get(r.get("dbId"), [])
            bloklar = []   # [(borclu_adi, kesinlesme, [barkod...])]
            kalan = list(barkodlar)
            for b in borclular:
                kendi = [g for g in kalan if g.get("borcluId") == b["id"]]
                kalan = [g for g in kalan if g.get("borcluId") != b["id"]]
                bloklar.append((b["ad"], b["kesinlesme"], kendi or [None]))
            for g in kalan:   # hiçbir borçluya bağlanmayan tebligatlar
                bloklar.append((g.get("muhatap") or "(muhatap belirsiz)", "", [g]))
            if not bloklar:   # borçlusu da barkodu da olmayan dosya
                bloklar.append((r.get("borclu", ""), "", [None]))
            sira = 0
            for ad, kesinlesme, gler in bloklar:
                for g in gler:
                    s = dict(r)
                    for k in self._BARKOD_ALANLARI:
                        s[k] = (g.get(k, "") or "") if g else ""
                    s["borclu"] = ad
                    s["kesinlesme"] = kesinlesme
                    s["_dosya"] = r
                    s["_grup"] = grup
                    s["_grup_ici"] = sira
                    sira += 1
                    satirlar.append(s)
        return satirlar

    def _dosyalar(self, satirlar):
        """Barkod satırlarından ÖZGÜN dosya kayıtlarını (tekrarsız, ilk görülme
        sırasıyla) döner — işlemler (Sorgula/21-2) dosya bazlıdır; aynı
        dosyanın birden çok barkod satırı seçilse de dosya BİR KEZ işlenir."""
        gorulen, out = set(), []
        for s in satirlar:
            if s["_grup"] not in gorulen:
                gorulen.add(s["_grup"])
                out.append(s["_dosya"])
        return out

    # ─────────────────────────── Dosya Görüntüle (dosya kartı) ───────────────
    def _dosya_goruntule(self):
        """Seçili barkod satırının DOSYASININ kartı — İcra Dosyalarım / Tüm
        Dosyalarım ile AYNI pencere (dosya_karti.py)."""
        secim = self.tree.selection()
        if not secim:
            self.durum_lbl.config(text="Önce listeden bir dosya seçin.")
            return
        try:
            rec = self._by_uid[int(secim[0])]["_dosya"]
        except (ValueError, KeyError):
            self.durum_lbl.config(text="Seçili satır bulunamadı; listeyi yenileyin.")
            return
        self._kart_ac(rec)

    def _gecmis_dosya_goruntule(self):
        secim = self.gecmis_tree.selection()
        try:
            rec = self.gecmis[int(secim[0])]
        except (ValueError, IndexError):
            return
        self._kart_ac(rec)

    def _kart_ac(self, rec):
        self.detay_btn.set_state("disabled")
        self.durum_lbl.config(text="Dosya kartı açılıyor…")

        def bitti():
            self.detay_btn.set_state("normal")
            self.durum_lbl.config(text="Dosya kartı açıldı (veritabanından)")
        dosya_karti.ac(self.app, rec, log_fn=self._log, liste_yenile_fn=self._filtrele, bitti_fn=bitti)

    def _temizle(self):
        self.birim_var.set("Tümü")
        self.durum_var.set("Tümü")
        self.tarih_bas_var.set("")
        self.tarih_bit_var.set("")
        self.alacakli_var.set("")
        self.borclu_var.set("")
        self._yerel_filtreleri_sifirla()
        self._filtrele()

    def _tabloyu_doldur(self, kayitlar):
        # Treeview satır kimliği (iid) DAHA ÖNCE listedeki SIRA konumuydu
        # (str(i)) — bu, kullanıcı satır(lar) seçtikten SONRA bir sütun
        # filtresine yazdığında `_yerel_uygula` yeniden çizerken aynı konuma
        # düşen FARKLI bir kaydı "seçili" gösteriyordu (kullanıcı bulgusu,
        # 2026-08-03: filtrelemeden sonra "Seçilenleri Sorgula" yalnızca tek
        # dosyayı işliyordu). Bunun yerine kayda SABİT bir kimlik (`_uid`,
        # ham listedeki özgün konum) verilip iid HER ZAMAN bu kimlikten
        # üretilir — filtre/sıralama kaydın iid'sini asla değiştirmez.
        for i, r in enumerate(kayitlar):
            r["_uid"] = i
        self.kayitlar_ham = birim_bolme.alanlari_ekle(kayitlar, "birimAdi")
        self._by_uid = {r["_uid"]: r for r in kayitlar}
        self._combolari_doldur()
        self._yerel_uygula()

    def _daire_bol_toggle(self):
        bolunmus = birim_bolme.ekran_toggle(self, "birimAdi")
        self.daire_bol_btn.set_text("Daireyi Birleştir ⇄" if bolunmus else "Daireyi Böl ⇄")

    # ─────────────────────────── Excel'e Aktar (Bölüm 1) ───────────────────────────
    # dosyalarim_genel.py'deki AYNI desen (bkz. modül başlığı — bilerek kopyalandı).
    def _excel_aktar(self):
        """O an EKRANDA GÖRÜNEN listeyi (sütun-başlığı filtreleri + sıralama
        DAHİL — `self.kayitlar`, Barkod/PTT/Mazbata sütunları dahil) Excel'e
        yazar. 2026-09-24'ten beri liste BARKOD BAŞINA satırdır (bkz.
        `_barkod_satirlarina_ac`) — Excel'e de her barkod ayrı satır gider,
        bir dosyanın barkodları art arda."""
        if not self.kayitlar:
            self.durum_lbl.config(text="Aktarılacak kayıt yok — önce filtreleyin.")
            return
        # 2026-09-24: daire sütununun biçimi sorulur (tek / bölünmüş / iki dosya).
        mod = birim_bolme.excel_secenek_sor(self.app, getattr(self, "_daire_bolunmus", False))
        if not mod:
            return
        varsayilan_ad = f"Barkod_Sorgu_Dosyalar_{time.strftime('%Y%m%d_%H%M')}.xlsx"
        yol = filedialog.asksaveasfilename(
            title="Excel'e Aktar", defaultextension=".xlsx",
            initialfile=varsayilan_ad, filetypes=[("Excel", "*.xlsx")])
        if not yol:
            return
        self.excel_btn.set_state("disabled")
        self.durum_lbl.config(text="Excel'e yazılıyor…")
        threading.Thread(target=self._excel_aktar_bg, args=(yol, list(self.kayitlar), mod), daemon=True).start()

    def _excel_aktar_bg(self, yol, kayitlar, mod=birim_bolme.MOD_EKRANDAKI):
        try:
            yazilan = birim_bolme.excel_yaz(yol, mod, list(self.KOLONLAR), type(self).KOLONLAR,
                                            kayitlar, "Barkod Sorgu Dosyalar", "birimAdi")
        except PermissionError:
            self.result_q.put(("excel_hata", "Dosya başka bir programda (ör. Excel'de) açık olabilir, kapatıp tekrar deneyin."))
            return
        except Exception as e:
            self.result_q.put(("excel_hata", str(e)))
            return
        self.result_q.put(("excel_tamam", yazilan))

    def _excel_aktarildi(self, yazilan):
        self.excel_btn.set_state("normal")
        self.durum_lbl.config(text="✔ Excel'e aktarıldı: " + " · ".join(
            f"{os.path.basename(y)} ({n} satır)" for y, n in yazilan))
        for y, _n in yazilan:
            try:
                os.startfile(y)
            except Exception:
                pass

    def _excel_hata(self, hata):
        self.excel_btn.set_state("normal")
        self.durum_lbl.config(text=f"Excel'e aktarılamadı: {hata}")

    # ─────────────────────────── veri yükleme: Bölüm 2 ───────────────────────────
    def _gecmis_yenile(self):
        self.gecmis_durum_lbl.config(text="Yükleniyor…")
        self._gecmis_yukle_bg()

    def _gecmis_yukle_bg(self):
        def bg():
            try:
                # limit=None (2026-09-24): 3000 satır sınırı 14 bin+ kaydın çoğunu gizliyordu.
                kayitlar = dosya_core.tebligat_barkod_gecmisi_listele(limit=None, log_fn=self._log)
            except Exception as e:
                self._log(f"⚠️ Geçmiş okunamadı: {e}")
                kayitlar = []
            self.result_q.put(("gecmis", kayitlar))
        threading.Thread(target=bg, daemon=True).start()

    # ───────────────── 21/2 Uygunluk Taraması (yalnız tarar/raporlar) ─────────────────
    # İki giriş noktası var: Bölüm 1 (dosya seçici, yukarıda) ve Bölüm 2
    # (Geçmiş/İADE listesi, aşağıda) — ikisi de AYNI arka plan taramasını
    # (teblig_21_2_core.iade_tarama) çağırır, yalnız seçili satırların
    # kaynağı farklıdır. `iade_tarama` zaten yalnız `ptt_durumu="İADE"` olan
    # adayları işlediğinden Bölüm 1'den (genel dosya listesi) seçilen
    # kayıtlar arasında İADE olmayanlar taramada otomatik elenir.
    def _t212_tara_bolum1(self):
        secim = self.tree.selection()
        if not secim:
            self.durum_lbl.config(
                text="Önce listeden en az bir dosya seçin (İADE tebligatlı olanlar taranır).")
            return
        try:
            secili_kayitlar = self._dosyalar([self._by_uid[int(iid)] for iid in secim])
        except (ValueError, KeyError):
            self.durum_lbl.config(text="Seçili satırlar bulunamadı; listeyi yenileyin.")
            return
        self._t212_baslat(secili_kayitlar, self.t212_btn1, self.durum_lbl)

    def _t212_tara(self):
        secim = self.gecmis_tree.selection()
        if not secim:
            self.gecmis_durum_lbl.config(
                text="Önce geçmiş listeden en az bir dosya seçin (İADE olanlar taranır).")
            return
        try:
            secili = [self.gecmis[int(iid)] for iid in secim]
        except (ValueError, IndexError):
            self.gecmis_durum_lbl.config(text="Seçili satırlar bulunamadı; listeyi yenileyin.")
            return
        self._t212_baslat(secili, self.t212_btn, self.gecmis_durum_lbl)

    def _t212_baslat(self, secili, btn, durum_lbl):
        self._t212_btns = (self.t212_btn1, self.t212_btn)
        for b in self._t212_btns:
            b.set_state("disabled")
        durum_lbl.config(text="21/2 uygunluk taraması yapılıyor… (canlı UYAP sorguları)")
        threading.Thread(target=self._t212_tara_bg, args=(secili,), daemon=True).start()

    def _t212_tara_bg(self, secili):
        try:
            sonuc = teblig_21_2_core.iade_tarama(secili_kayitlar=secili, log_fn=self._log)
        except teblig_21_2_core.UyapBaglantisiYok as e:
            self.result_q.put(("t212_hata", ("baglanti", str(e))))
            return
        except Exception as e:
            self.result_q.put(("t212_hata", str(e)))
            return
        self.result_q.put(("t212_sonuc", sonuc))

    def _t212_hata(self, hata):
        for b in getattr(self, "_t212_btns", (self.t212_btn1, self.t212_btn)):
            b.set_state("normal")
        if isinstance(hata, tuple) and hata[0] == "baglanti":
            # 2026-09-24: bağlantı yokken tarama hiç başlamaz, satırlara "hata" yazılmaz.
            self.gecmis_durum_lbl.config(text="Tarama başlatılmadı: UYAP bağlantısı yok.")
            self.durum_lbl.config(text="Tarama başlatılmadı: UYAP bağlantısı yok.")
            messagebox.showwarning("UYAP bağlantısı yok", hata[1])
            return
        self.gecmis_durum_lbl.config(text=f"Tarama sırasında hata: {hata}")
        self.durum_lbl.config(text=f"Tarama sırasında hata: {hata}")

    def _t212_sonuc_goster(self, sonuc):
        for b in getattr(self, "_t212_btns", (self.t212_btn1, self.t212_btn)):
            b.set_state("normal")
        self.gecmis_durum_lbl.config(text=f"Tarama tamam: {len(sonuc)} sonuç")
        self.durum_lbl.config(text=f"Tarama tamam: {len(sonuc)} sonuç")
        if not sonuc:
            return
        self._t212_rapor_penceresi_ac(sonuc)

    def _t212_son_raporu_goster(self):
        # Kullanıcı bulgusu (2026-08-14): "son 21/2 raporu silindi" — eskiden
        # bu buton yalnız UyapMasa BELLEĞİNDEKİ son sonucu gösteriyordu, program
        # yeniden başlayınca kayboluyordu. Artık veritabanından okur (bkz.
        # teblig_21_2_core.son_tarama_sonuclarini_getir — iade_tarama HER
        # taramada oraya yazıyor) — kalıcı, yeniden başlatmadan etkilenmez.
        self.durum_lbl.config(text="Son 21/2 raporu yükleniyor…")

        def bg():
            try:
                sonuc = teblig_21_2_core.son_tarama_sonuclarini_getir()
            except Exception as e:
                self.result_q.put(("t212_son_rapor_hata", str(e)))
                return
            self.result_q.put(("t212_son_rapor", sonuc))
        threading.Thread(target=bg, daemon=True).start()

    def _t212_son_rapor_goster(self, sonuc):
        if not sonuc:
            self.durum_lbl.config(text="Veritabanında henüz bir 21/2 tarama sonucu yok; önce tarama yapın.")
            return
        self.durum_lbl.config(text=f"Son 21/2 raporu yüklendi: {len(sonuc)} sonuç (veritabanından).")
        self._t212_rapor_penceresi_ac(sonuc)

    def _t212_rapor_penceresi_ac(self, sonuc):
        top = tk.Toplevel(self.app)
        top.title("21/2 Uygunluk Tarama Sonuçları")
        top.configure(bg=C.CARD)
        top.transient(self.app)
        top.geometry("1180x480")

        tk.Label(top, text="Bu pencere YALNIZ bir rapordur — hiçbir talep gönderilmedi, "
                            "ücret harcanmadı.", bg=C.CARD, fg=C.CLAY,
                 font=self.app.f_card_t).pack(anchor="w", padx=16, pady=(14, 8))

        kolonlar = [
            ("birim", "İcra Dairesi", 200), ("dosyaNo", "Dosya No", 90),
            ("borclu", "Borçlu", 180), ("iadeTarihi", "İade Tarihi", 90),
            ("kategori", "Sonuç", 260), ("taranma", "Tarandı", 115),
            ("tebligAdresi", "Tebligat Adresi", 220), ("mernisAdresi", "Mernis Adresi", 220),
        ]
        cols = [k for k, _l, _w in kolonlar]

        # ── Özet + "Sonuç" süzgeci + Hatalıları Yeniden Tara (kullanıcı isteği,
        # 2026-09-24: "hatalıları ayıklayamıyorum"). Ağaç süzülerek yeniden
        # doldurulur ama iid HER ZAMAN `sonuc` listesindeki özgün sıradır —
        # gönder düğmelerinin `sonuc[int(iid)]` kullanımı değişmez.
        ust = tk.Frame(top, bg=C.CARD)
        ust.pack(fill="x", padx=16, pady=(0, 6))
        tk.Label(ust, text="Sonuç:", bg=C.CARD, fg=C.SAGE_DK, font=self.app.f_nav_b).pack(side="left")
        suzgec_var = tk.StringVar(value="Tümü")
        ttk.Combobox(ust, textvariable=suzgec_var, state="readonly", width=16, font=self.app.f_body,
                     values=["Tümü"] + list(_T212_GRUPLARI)).pack(side="left", padx=(6, 12))
        ozet_lbl = tk.Label(ust, text="", bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_small)
        ozet_lbl.pack(side="left")

        tree = ttk.Treeview(top, columns=cols, show="headings", height=14, selectmode="extended")
        for k, lbl, w in kolonlar:
            tree.heading(k, text=lbl)
            tree.column(k, width=w, anchor="w", stretch=False)
        tree.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        ysb = tk.Scrollbar(top, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=ysb.set)

        def _doldur():
            secili_once = set(tree.selection())
            tree.delete(*tree.get_children())
            sec = suzgec_var.get()
            for i, s in enumerate(sonuc):
                if sec == "Tümü" or _t212_grup(s.get("kategori")) == sec:
                    tree.insert("", "end", iid=str(i), values=[s.get(k, "") for k in cols])
                    if str(i) in secili_once:
                        tree.selection_add(str(i))
            sayac = {}
            for s in sonuc:
                g = _t212_grup(s.get("kategori"))
                sayac[g] = sayac.get(g, 0) + 1
            ozet_lbl.config(text=f"{len(sonuc)} satır — " + " · ".join(
                f"{g}: {sayac[g]}" for g in _T212_GRUPLARI if sayac.get(g)))
        suzgec_var.trace_add("write", lambda *a: _doldur())
        _doldur()

        yeniden_q = queue.Queue()

        def _yeniden_tara():
            nonlocal gonderim_aktif
            if gonderim_aktif:
                return
            hedef_iid = tree.selection() or tree.get_children()
            hedef = [sonuc[int(i)] for i in hedef_iid
                     if _t212_grup(sonuc[int(i)].get("kategori")) in ("Hatalı", "Elle inceleyin")]
            if not hedef:
                messagebox.showinfo("Yeniden taranacak satır yok",
                                    "Seçili (ya da listedeki) satırlarda hatalı ya da 'elle inceleyin' "
                                    "sonucu yok.", parent=top)
                return
            hazir, mesaj = teblig_21_2_core.uyap_baglantisi_hazir_mi()
            if not hazir:
                messagebox.showwarning("UYAP bağlantısı yok", mesaj, parent=top)
                return
            dosyalar, gorulen = [], set()
            for s in hedef:
                anahtar = (s.get("birim"), s.get("dosyaNo"))
                if anahtar not in gorulen:
                    gorulen.add(anahtar)
                    dosyalar.append({"birimAdi": s.get("birim", ""), "dosyaNo": s.get("dosyaNo", "")})
            gonderim_aktif = True
            for b in _tum_gonder_btnlari() + (yeniden_btn,):
                b.set_state("disabled")
            ozet_lbl.config(text=f"{len(dosyalar)} dosya yeniden taranıyor… (canlı UYAP sorguları)")

            def bg():
                try:
                    yeniden_q.put(("tamam", teblig_21_2_core.iade_tarama(secili_kayitlar=dosyalar,
                                                                          log_fn=self._log)))
                except Exception as e:
                    yeniden_q.put(("hata", str(e)))
            threading.Thread(target=bg, daemon=True).start()
            top.after(300, _yeniden_bekle)

        def _yeniden_bekle():
            nonlocal gonderim_aktif
            try:
                tip, veri = yeniden_q.get_nowait()
            except queue.Empty:
                if top.winfo_exists():
                    top.after(300, _yeniden_bekle)
                return
            gonderim_aktif = False
            if not top.winfo_exists():
                return
            for b in _tum_gonder_btnlari() + (yeniden_btn,):
                b.set_state("normal")
            if tip == "hata":
                _doldur()
                messagebox.showwarning("Yeniden tarama yapılamadı", veri, parent=top)
                return
            # Aynı (birim, dosya no, borçlu) satırı yerinde güncellenir; yeni çıkan eklenir.
            yer = {(s.get("birim"), s.get("dosyaNo"), s.get("borclu")): i for i, s in enumerate(sonuc)}
            for r in veri:
                i = yer.get((r.get("birim"), r.get("dosyaNo"), r.get("borclu")))
                if i is None:
                    sonuc.append(r)
                else:
                    sonuc[i].update(r)
            _doldur()

        # GERÇEK gönderim (kullanıcı isteği, 2026-08-13): rapor penceresinden
        # "21/2 için uygun" satırlar seçilip GERÇEKTEN gönderilebilsin —
        # bkz. teblig_21_2_gonder.py (ücretli, ayrı bir modül; bu rapor
        # penceresi ve teblig_21_2_core.py hâlâ yalnız TARAR/raporlar).
        #
        # Kullanıcı bulgusu (2026-08-14, ilk tur): eskiden bu AYRI bir
        # Toplevel açıyordu; o pencere bu rapor penceresinin ARKASINDA kalıp
        # "kayboluyor" gibi görünmüştü — kullanıcı "olmadı" sanıp butona
        # TEKRAR TEKRAR bastı, aynı dosya için 6 ayrı gönderim işi sıraya
        # girdi (hiçbiri onaylanmadığı için ücret harcanmadı, ama karışıklık
        # büyüktü). Artık AYRI pencere YOK — gönderim ekranı bu pencerenin
        # İÇİNE, alt tarafa gömülür.
        #
        # Kullanıcı bulgusu (2026-08-14, ikinci tur): buton bir gönderimden
        # sonra KALICI olarak kilitli kalıyordu — başka bir dosya seçip
        # TEKRAR gönderilemiyordu. Artık yalnız o AN çalışan bir iş varken
        # kilitli (bkz. _bitti — iş done/error/cancelled olunca yeniden
        # aktif eder); aynı anda İKİ iş aynı pencereden başlatılamaz ama
        # ardışık gönderimler serbesttir.
        #
        # Kullanıcı bulgusu (2026-08-14, üçüncü tur): "gönderildi ama Barkod
        # Sorgu tablosunda Yeniden Tebliğ Talebi hâlâ Yok görünüyor, rapor
        # penceresinde de hâlâ '21/2 için uygun' (yapılacak iş) gibi
        # duruyor" — _bitti artık (a) yerel DB'yi tazeler (bkz.
        # teblig_21_2_core.dosyalari_gonderildi_isaretle — CANLI SORGU
        # ATMAZ, finalize'ın kendi başarı yanıtına güvenir), (b) Barkod
        # Sorgu ekranının HER İKİ tablosunu (Bölüm 1 + Geçmiş) tazeler,
        # (c) BU rapor penceresindeki satırın kategorisini "gönderildi"
        # olarak günceller ki yapılmış iş, yapılacak iş gibi görünüp kafa
        # karıştırmasın.
        alt = tk.Frame(top, bg=C.CARD)
        alt.pack(fill="x", padx=16, pady=(0, 8))

        gonder_kapsayici = tk.Frame(top, bg=C.CARD)
        gonder_kapsayici.pack(fill="both", expand=False, padx=16, pady=(0, 14))

        gonderim_aktif = False

        def _bitti(sonuclar):
            nonlocal gonderim_aktif
            gonderim_aktif = False
            for b in _tum_gonder_btnlari():
                b.set_state("normal")

            gonderilenler = [s.get("ozet") or {} for s in sonuclar if s.get("durum") == "gonderildi"]
            if not gonderilenler:
                return
            gonderilen_anahtarlar = {(g.get("dosyaNo"), g.get("borclu")) for g in gonderilenler}
            for i, s in enumerate(sonuc):
                if (s.get("dosyaNo"), s.get("borclu")) in gonderilen_anahtarlar:
                    s["kategori"] = "✅ gönderildi"
                    try:
                        tree.item(str(i), values=[s.get(k, "") for k in cols])
                    except Exception:
                        pass
            try:
                n = teblig_21_2_core.dosyalari_gonderildi_isaretle(gonderilenler)
                # Sessiz "0 eşleşme" artık GÖRÜNÜR (kullanıcı bulgusu, 2026-08-17:
                # "gönderme çalıştı ama durum değişmedi" hiçbir hata/log OLMADAN
                # oluyordu — eşleşme 0 çıksa bile önceden sessizce geçilirdi).
                if n < len(gonderilenler):
                    self._log(f"⚠️ Yeniden Tebliğ Talebi: {len(gonderilenler)} gönderilen dosyadan "
                              f"yalnız {n} tanesi yerel DB'de eşleşti/güncellendi.")
            except Exception as e:
                self._log(f"⚠️ Yerel DB tazelenemedi (Yeniden Tebliğ Talebi): {e}")
            # Barkod Sorgu ekranının HER İKİ tablosu da (o anki filtre
            # KORUNARAK) tazelenir — kullanıcı "yenidenTebligTalebi"
            # sütununu hemen "Var" görsün diye (bkz. _sorgu_bitti — AYNI
            # tazeleme çağrıları, bulk sorgu tamamlanınca da kullanılıyor).
            _doldur()   # özet + süzgeç gönderim sonrası güncel kalsın
            self._gecmis_yukle_bg()
            self._filtrele_bg(self._filtreleri_topla())

        def _tum_gonder_btnlari():
            return (gonder_btn, yine_de_btn, normal_tebligat_btn, yine_de_normal_btn)

        def _gonderimi_baslat(kalemler, modul, on_bitti):
            nonlocal gonderim_aktif
            for b in _tum_gonder_btnlari():
                b.set_state("disabled")
            top.geometry("1180x780")
            basladi = modul.gonderim_baslat(
                gonder_kapsayici, self.app, kalemler, on_bitti=on_bitti)
            if basladi:
                gonderim_aktif = True
            else:
                for b in _tum_gonder_btnlari():
                    b.set_state("normal")
                top.geometry("1180x480")

        def _gonder_tetikle():
            nonlocal gonderim_aktif
            if gonderim_aktif:
                return
            secim = tree.selection()
            if not secim:
                messagebox.showinfo("Seçim yok", "Önce listeden en az bir satır seçin.")
                return
            secili = [sonuc[int(iid)] for iid in secim]
            uygun = [s for s in secili if s.get("kategori") == "21/2 için uygun"]
            uygun_olmayan = len(secili) - len(uygun)
            if not uygun:
                messagebox.showinfo(
                    "Uygun satır yok",
                    "Seçtiğiniz satırların hiçbiri \"21/2 için uygun\" değil — yalnız bu "
                    "kategorideki satırlar gönderilebilir. Adresin aslında aynı olduğuna "
                    "eminseniz \"Yine de Gönder\" düğmesini kullanın.")
                return
            if uygun_olmayan:
                if not messagebox.askyesno(
                        "Bazı satırlar atlanacak",
                        f"Seçtiğiniz {len(secili)} satırdan {uygun_olmayan} tanesi "
                        "\"21/2 için uygun\" değil ve gönderilmeyecek. Kalan "
                        f"{len(uygun)} uygun dosya için devam edilsin mi?"):
                    return
            kalemler = _gonderim_kalemleri(uygun)
            _gonderimi_baslat(kalemler, teblig_21_2_gonder, _bitti)

        # "Yine de Gönder" (kullanıcı isteği, 2026-08-17): adres kıyaslaması
        # büyük/küçük harf ya da boşluk farkı gibi küçük biçim farklılıkları
        # yüzünden otomatik EŞLEŞMEYEBİLİR (bkz. teblig_21_2_core._adresler_
        # eslesiyor_mu — normalize eder ama HER varyasyonu YAKALAYAMAZ),
        # oysa adresler gözle bakıldığında GERÇEKTEN aynı olabilir. Bu düğme
        # "21/2 için uygun" DIŞINDAKİ (zaten gönderilmiş/başarıyla gönderilmiş
        # HARİÇ) seçili satırları, kullanıcı GÖZÜYLE kontrol edip onayladıktan
        # SONRA aynı gönderim işine (teblig_21_2_gonder) sokar — o iş kendi
        # başına, HER dosya için AYRICA "zaten gönderilmiş mi" diye CANLI
        # kontrol eder ve göndermeden önce yine TEK TEK kullanıcı onayı ister
        # (bkz. job_handlers._teblig_212_gonder) — yani bu düğme otomatik
        # adres kontrolünü ATLAR, ama tekrarlı/mükerrer gönderim ve son onay
        # güvenliğini ATLAMAZ.
        _GONDERILMEYECEK_KATEGORILER = {"zaten gönderilmiş", "✅ gönderildi",
                                        "✅ gönderildi (normal tebligat)"}

        def _yine_de_gonder_tetikle():
            nonlocal gonderim_aktif
            if gonderim_aktif:
                return
            secim = tree.selection()
            if not secim:
                messagebox.showinfo("Seçim yok", "Önce listeden en az bir satır seçin.")
                return
            secili = [sonuc[int(iid)] for iid in secim]
            adaylar = [s for s in secili if s.get("kategori") not in _GONDERILMEYECEK_KATEGORILER]
            if not adaylar:
                messagebox.showinfo(
                    "Gönderilecek satır yok",
                    "Seçtiğiniz satırların tamamı zaten gönderilmiş görünüyor.")
                return
            if not messagebox.askyesno(
                    "Adres otomatik eşleşmedi — yine de gönderilsin mi?",
                    f"Seçtiğiniz {len(adaylar)} dosya \"21/2 için uygun\" olarak "
                    "İŞARETLENMEDİ (büyük/küçük harf, boşluk gibi bir biçim farkı "
                    "yüzünden olabilir). Tebligat Adresi ve Mernis Adresi sütunlarını "
                    "GÖZÜNÜZLE kontrol edip aynı adres olduğuna eminseniz devam edin.\n\n"
                    "Devam ederseniz her dosya için ayrıca UYAP'tan \"zaten gönderilmiş "
                    "mi\" kontrol edilecek ve gerçek gönderimden ÖNCE yine tek tek "
                    "onayınız istenecek."):
                return
            kalemler = _gonderim_kalemleri(adaylar)
            _gonderimi_baslat(kalemler, teblig_21_2_gonder, _bitti)

        # "Normal Tebligat Gönder" (kullanıcı isteği, 2026-08-17): "farklı yöntem
        # gerekli (mernis'e çıkmamış)" kategorisindeki dosyalar 21/2 şerhli YENİDEN
        # tebliğ talebine uygun DEĞİL — bunlara ilk/normal tebligat (icra/ödeme
        # emrinin mernis adresine tebliğe çıkarılması) gönderilmeli. Ayrı bir iş
        # türü (normal_tebligat_gonder, bkz. job_handlers.py) ve ayrı bir ekran
        # modülü (normal_tebligat_gonder.py) kullanır — teblig_212_gonder'a
        # DOKUNULMAZ, "21/2 için uygun" ile "normal tebligat gerekiyor" FARKLI
        # belge türleri, aynı akıştan geçirilirse (yanlışlıkla 21/2 şerhli talep
        # gönderilirse) hukuken YANLIŞ bir belge UYAP'a gider.
        _NORMAL_TEBLIGAT_KATEGORISI = "farklı yöntem gerekli (mernis'e çıkmamış)"

        def _bitti_normal(sonuclar):
            nonlocal gonderim_aktif
            gonderim_aktif = False
            for b in _tum_gonder_btnlari():
                b.set_state("normal")

            gonderilenler = [s for s in sonuclar if s.get("durum") == "gonderildi"]
            if not gonderilenler:
                return
            gonderilen_dosya_nolari = {g.get("dosyaNo") for g in gonderilenler}
            for i, s in enumerate(sonuc):
                if s.get("dosyaNo") in gonderilen_dosya_nolari:
                    s["kategori"] = "✅ gönderildi (normal tebligat)"
                    try:
                        tree.item(str(i), values=[s.get(k, "") for k in cols])
                    except Exception:
                        pass
            # DÜZELTME (kullanıcı bulgusu, 2026-08-17): önceki sürüm burada
            # dosyalari_gonderildi_isaretle'i BİLEREK çağırmıyordu ("bu
            # gönderim 21/2 türünden değil" gerekçesiyle) — ama UYAP'ta HER
            # avukat portal talebi (21/2 DE, Normal Tebligat DA) AYNI evrak
            # türünü ("Avukat Portal Tebligat Talebi", bkz. TALEP_EVRAK_TUR)
            # üretir; "Yeniden Tebliğ Talebi" sütunu bu evrak türünün VARLIĞINI
            # gösterir, tebligat türünü AYIRT ETMEZ. Yani bir sonraki Barkod
            # Sorgu taraması bu alanı ZATEN "Var"a çevirecekti — burada da
            # işaretleyip kullanıcının ANINDA görmesini sağlıyoruz.
            ozetler = [g.get("ozet") or {} for g in gonderilenler]
            try:
                n = teblig_21_2_core.dosyalari_gonderildi_isaretle(ozetler)
                if n < len(ozetler):
                    self._log(f"⚠️ Yeniden Tebliğ Talebi: {len(ozetler)} gönderilen dosyadan "
                              f"yalnız {n} tanesi yerel DB'de eşleşti/güncellendi.")
            except Exception as e:
                self._log(f"⚠️ Yerel DB tazelenemedi (Yeniden Tebliğ Talebi): {e}")
            # Barkod Sorgu ekranının tabloları (dosya durumu değişmiş olabilir
            # diye) tazelenir.
            _doldur()   # özet + süzgeç gönderim sonrası güncel kalsın
            self._gecmis_yukle_bg()
            self._filtrele_bg(self._filtreleri_topla())

        def _normal_tebligat_tetikle():
            nonlocal gonderim_aktif
            if gonderim_aktif:
                return
            secim = tree.selection()
            if not secim:
                messagebox.showinfo("Seçim yok", "Önce listeden en az bir satır seçin.")
                return
            secili = [sonuc[int(iid)] for iid in secim]
            uygun = [s for s in secili if s.get("kategori") == _NORMAL_TEBLIGAT_KATEGORISI]
            uygun_olmayan = len(secili) - len(uygun)
            if not uygun:
                messagebox.showinfo(
                    "Uygun satır yok",
                    f"Seçtiğiniz satırların hiçbiri \"{_NORMAL_TEBLIGAT_KATEGORISI}\" "
                    "değil — normal tebligat yalnız bu kategorideki dosyalar içindir.")
                return
            if uygun_olmayan:
                if not messagebox.askyesno(
                        "Bazı satırlar atlanacak",
                        f"Seçtiğiniz {len(secili)} satırdan {uygun_olmayan} tanesi "
                        f"\"{_NORMAL_TEBLIGAT_KATEGORISI}\" değil ve gönderilmeyecek. "
                        f"Kalan {len(uygun)} dosya için devam edilsin mi?"):
                    return
            kalemler = _gonderim_kalemleri(uygun)
            _gonderimi_baslat(kalemler, normal_tebligat_gonder, _bitti_normal)

        # "Yine de Gönder" (Normal Tebligat) — kullanıcı isteği (2026-08-17):
        # bazı satırlar "farklı yöntem gerekli (mernis'e çıkmamış)" DEĞİL,
        # "elle inceleyin — adres ayrıştırılamadı" gibi BAŞKA bir kategoride
        # çıkıyor (ör. giden tebligattaki adres PDF'ten ayrıştırılamadı —
        # bkz. teblig_21_2_core._teblig_adresi_cikar, desen uymazsa None
        # döner) — oysa kullanıcı dosyayı GÖZÜYLE kontrol edip mernis
        # adresinin doğru olduğunu, normal tebligat gönderilebileceğini
        # görebilir. "21/2 Yine de Gönder" ile AYNI mantık — otomatik
        # kategori kontrolünü ATLAR, mükerrer gönderim/son onay güvenliğini
        # ATLAMAZ (aynı prepare()+ctx.request_approval zinciri).
        def _yine_de_normal_tetikle():
            nonlocal gonderim_aktif
            if gonderim_aktif:
                return
            secim = tree.selection()
            if not secim:
                messagebox.showinfo("Seçim yok", "Önce listeden en az bir satır seçin.")
                return
            secili = [sonuc[int(iid)] for iid in secim]
            adaylar = [s for s in secili if s.get("kategori") not in _GONDERILMEYECEK_KATEGORILER]
            if not adaylar:
                messagebox.showinfo(
                    "Gönderilecek satır yok",
                    "Seçtiğiniz satırların tamamı zaten gönderilmiş görünüyor.")
                return
            if not messagebox.askyesno(
                    "Kategori uygun değil — yine de normal tebligat gönderilsin mi?",
                    f"Seçtiğiniz {len(adaylar)} dosya \"{_NORMAL_TEBLIGAT_KATEGORISI}\" "
                    "kategorisinde İŞARETLENMEDİ (ör. adres ayrıştırılamadığı için "
                    "\"elle inceleyin\" çıkmış olabilir). Mernis Adresi sütununu "
                    "GÖZÜNÜZLE kontrol edip normal tebligat gönderilebileceğinden "
                    "eminseniz devam edin.\n\n"
                    "Devam ederseniz her dosya için ayrıca UYAP'tan \"zaten gönderilmiş "
                    "mi\" kontrol edilecek ve gerçek (ücretli) gönderimden ÖNCE yine "
                    "tek tek onayınız istenecek."):
                return
            kalemler = _gonderim_kalemleri(adaylar)
            _gonderimi_baslat(kalemler, normal_tebligat_gonder, _bitti_normal)

        gonder_btn = self._btn(alt, "Seçilenler İçin GERÇEKTEN Gönder (Ücretli)",
                               _gonder_tetikle, "primary")
        gonder_btn.pack(side="left")
        yine_de_btn = self._btn(alt, "⚠ Yine de Gönder (Uygun İşaretlenmeyenler)",
                                _yine_de_gonder_tetikle, "stop")
        yine_de_btn.pack(side="left", padx=(8, 0))
        normal_tebligat_btn = self._btn(
            alt, "📨 Normal Tebligat Gönder (Mernis'e Çıkmamışlar)",
            _normal_tebligat_tetikle, "ghost")
        normal_tebligat_btn.pack(side="left", padx=(8, 0))
        yine_de_normal_btn = self._btn(
            alt, "⚠ Yine de Gönder (Normal Tebligat)",
            _yine_de_normal_tetikle, "stop")
        yine_de_normal_btn.pack(side="left", padx=(8, 0))
        yeniden_btn = self._btn(alt, "↻ Hatalıları Yeniden Tara", _yeniden_tara, "ghost")
        yeniden_btn.pack(side="left", padx=(8, 0))
        self._btn(alt, "Kapat", top.destroy, "ghost").pack(side="left", padx=(8, 0))
        top.grab_set()

    # ─────────────────────────── Tür Eşleştir (2026-09-25) ───────────────────────────
    def _tur_eslestir_penceresi(self):
        """Şablon dışı tebligat türlerini (zarftaki ham belge adı ya da kaydın
        kendi mazbatasındaki belge adı) GRUP GRUP gösterir; kullanıcı her grup
        için mevcut bir türü seçer ya da yeni tür adı yazar. Kaydet ve Uygula:
        eşleştirme kalıcı olur, mevcut kayıtlar düzeltilir, sonraki turlar aynı
        yazılışı otomatik o türe çevirir (bkz. tur_eslestirme)."""
        top = tk.Toplevel(self.app)
        top.title("Tebligat Türü Eşleştir")
        top.configure(bg=C.CARD)
        top.transient(self.app)
        top.geometry("1160x600")
        tk.Label(top, text="Şablon dışı tebligat türleri — her grup için türü siz belirleyin", bg=C.CARD,
                 fg=C.INK, font=self.app.f_card_t).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(top, text="Tarih ve yazım farkları aynı grupta toplanır. Satır(lar)ı seçip aşağıdan türü "
                           "seçin ya da yeni bir tür adı yazın, 'Seçili gruplara ata' deyin; bitince 'Kaydet ve "
                           "Uygula'. Eşleştirilen yazılış bir daha sorulmaz.",
                 bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_small, wraplength=1100, justify="left"
                 ).pack(anchor="w", padx=16, pady=(0, 8))
        kolonlar = [("ornek", "En sık yazılış", 360), ("adet", "Kayıt", 60), ("cesit", "Yazılış", 60),
                    ("kaynak", "Kaynak", 80), ("ornek_dosya", "Örnek dosya", 250), ("secim", "Seçilen tür", 230)]
        cols = [k for k, _l, _w in kolonlar]
        tree = ttk.Treeview(top, columns=cols, show="headings", height=16, selectmode="extended")
        for k, lbl, w in kolonlar:
            tree.heading(k, text=lbl)
            tree.column(k, width=w, anchor="w", stretch=(k == "ornek"))
        tree.pack(fill="both", expand=True, padx=16)
        detay = tk.Label(top, text="", bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_small,
                         wraplength=1100, justify="left", anchor="w")
        detay.pack(fill="x", padx=16, pady=(4, 0))
        alt = tk.Frame(top, bg=C.CARD)
        alt.pack(fill="x", padx=16, pady=(8, 14))
        tk.Label(alt, text="Tür:", bg=C.CARD, fg=C.SAGE_DK, font=self.app.f_nav_b).pack(side="left")
        tur_var = tk.StringVar()
        tur_cb = ttk.Combobox(alt, textvariable=tur_var, width=32, font=self.app.f_body,
                              values=tur_eslestirme.kategoriler())
        tur_cb.pack(side="left", padx=(6, 8))
        durum = tk.Label(alt, text="Yükleniyor…", bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_small)

        gruplar, secimler = [], {}      # secimler: anahtar -> tür
        kuyruk = queue.Queue()

        def _doldur():
            tree.delete(*tree.get_children())
            for i, g in enumerate(gruplar):
                tree.insert("", "end", iid=str(i), values=[
                    g["ornek"], g["adet"], len(g["yazilislar"]), g["kaynak"], g["ornek_dosya"],
                    secimler.get(g["anahtar"], "")])
            atanan = sum(1 for g in gruplar if g["anahtar"] in secimler)
            durum.config(text=f"{len(gruplar)} grup, {sum(g['adet'] for g in gruplar)} kayıt bekliyor · "
                              f"{atanan} gruba tür atandı (henüz kaydedilmedi)" if gruplar
                         else "Eşleştirilecek şablon dışı tür kalmadı. ✅")

        def _secim_degisti(_e=None):
            sec = tree.selection()
            if len(sec) == 1:
                g = gruplar[int(sec[0])]
                detay.config(text="Yazılışlar: " + " · ".join(
                    f"{y} ({n})" for y, n in sorted(g["yazilislar"].items(), key=lambda p: -p[1])[:12]))
            else:
                detay.config(text=f"{len(sec)} grup seçili." if sec else "")
        tree.bind("<<TreeviewSelect>>", _secim_degisti)

        def _ata():
            tur = tur_var.get().strip()
            sec = tree.selection()
            if not sec:
                messagebox.showinfo("Seçim yok", "Önce listeden en az bir grup seçin.", parent=top)
                return
            if not tur:
                messagebox.showinfo("Tür yok", "Listeden bir tür seçin ya da yeni tür adı yazın.", parent=top)
                return
            for iid in sec:
                secimler[gruplar[int(iid)]["anahtar"]] = tur
            if tur not in tur_cb["values"]:
                tur_cb["values"] = list(tur_cb["values"]) + [tur]
            _doldur()

        def _yukle():
            def bg():
                try:
                    kuyruk.put(("gruplar", tur_eslestirme.bekleyen_gruplar()))
                except Exception as e:
                    kuyruk.put(("hata", str(e)))
            threading.Thread(target=bg, daemon=True).start()
            top.after(200, _bekle)

        def _kaydet():
            if not secimler:
                messagebox.showinfo("Atama yok", "Hiçbir gruba tür atanmadı.", parent=top)
                return
            kaydet_btn.set_state("disabled")
            durum.config(text="Kaydediliyor ve kayıtlara uygulanıyor…")
            atamalar = dict(secimler)

            def bg():
                try:
                    n = tur_eslestirme.uygula(atamalar, log_fn=self._log)
                    kuyruk.put(("uygulandi", n))
                except Exception as e:
                    kuyruk.put(("hata", str(e)))
            threading.Thread(target=bg, daemon=True).start()
            top.after(200, _bekle)

        def _bekle():
            try:
                tip, veri = kuyruk.get_nowait()
            except queue.Empty:
                if top.winfo_exists():
                    top.after(200, _bekle)
                return
            if not top.winfo_exists():
                return
            if tip == "gruplar":
                gruplar[:] = veri
                _doldur()
            elif tip == "uygulandi":
                secimler.clear()
                kaydet_btn.set_state("normal")
                self._log(f"✔ Tür eşleştirmesi kaydedildi: {veri} kayıt güncellendi.")
                tur_cb["values"] = tur_eslestirme.kategoriler()
                durum.config(text=f"✔ {veri} kayıt güncellendi. Liste yenileniyor…")
                self._gecmis_yukle_bg()
                self._filtrele_bg(self._filtreleri_topla())
                _yukle()
            else:
                kaydet_btn.set_state("normal")
                durum.config(text=f"Hata: {veri}")

        self._btn(alt, "Seçili gruplara ata", _ata, "ghost").pack(side="left")
        kaydet_btn = self._btn(alt, "Kaydet ve Uygula", _kaydet, "primary")
        kaydet_btn.pack(side="left", padx=(8, 0))
        self._btn(alt, "Kapat", top.destroy, "ghost").pack(side="left", padx=(8, 0))
        durum.pack(side="left", padx=(14, 0))
        _yukle()

    # ─────────────────────────── Tümünü / Seçilenleri Sorgula ───────────────────────────
    _MOD_ADI = {barkod_sorgu.MOD_UYAP: "UYAP tebligat bilgisi",
                barkod_sorgu.MOD_PTT: "PTT barkod sorgusu",
                barkod_sorgu.MOD_IKISI: "UYAP tebligat bilgisi + PTT barkod sorgusu"}

    def _islem_baslat(self, mod):
        """Üç işlem düğmesinin ortak girişi. Seçim varsa seçili satırlar;
        yoksa (kullanıcı bulgusu 2026-08-03: hiç seçim yapmadan tümünü
        sorgulatabilmek) onay alınarak ekrandaki filtrelenmiş listenin TAMAMI."""
        secim = self.tree.selection()
        if secim:
            try:
                kayitlar = self._dosyalar([self._by_uid[int(iid)] for iid in secim])
            except (ValueError, KeyError):
                self.durum_lbl.config(text="Seçili satırlar bulunamadı; listeyi yenileyin.")
                return
            aciklama = "seçili"
        else:
            if not self.kayitlar:
                self.durum_lbl.config(text="İşlenecek dosya yok; önce filtreleyin.")
                return
            # Liste artık barkod başına satır (2026-09-24) — işlem dosya bazlı.
            kayitlar = self._dosyalar(self.kayitlar)
            if not messagebox.askyesno(
                    self._MOD_ADI[mod],
                    f"Listeden seçim yapılmadı.\n\nFiltrelenmiş listedeki {len(kayitlar)} "
                    f"dosyanın TÜMÜ için \"{self._MOD_ADI[mod]}\" yapılsın mı?",
                    parent=self.parent):
                return
            aciklama = "filtrelenmiş listedeki TÜM"
        # Yarım kalmış tur varsa "devam / baştan" sorulur (2026-09-24).
        devam = barkod_sorgu.devam_sor(self.parent, kayitlar, mod)
        if devam is None:
            return
        self._sorgu_baslat(kayitlar, aciklama, mod, devam)

    def _secilenleri_guncelle(self):
        """Seçili dosyalar için künye + taraflar + evrak/tebligat (UYAP+PTT) +
        itiraz + kesinleşme. Seçim zorunlu (tüm listeye düşmez)."""
        secim = self.tree.selection()
        if not secim:
            self.durum_lbl.config(text="Önce listeden en az bir dosya seçin (Ctrl/Shift ile çoklu seçim).")
            return
        try:
            kayitlar = self._dosyalar([self._by_uid[int(iid)] for iid in secim])
        except (ValueError, KeyError):
            self.durum_lbl.config(text="Seçili satırlar bulunamadı; listeyi yenileyin.")
            return
        devam = barkod_sorgu.devam_sor(self.parent, kayitlar, barkod_sorgu.MOD_IKISI)
        if devam is None:
            return
        self._sorgu_baslat(kayitlar, "seçili", barkod_sorgu.MOD_IKISI, devam, taraf_da=True)

    def _sorgu_baslat(self, kayitlar, aciklama, mod=barkod_sorgu.MOD_IKISI, devam=True, taraf_da=False):
        if self._calisiyor:
            return
        akis = _tid.basvur_ile_cakisma_akisi(self.app, self.kontrol.ad, self.kontrol)
        if akis == "iptal":
            return
        if akis == "sirada":
            self.durum_lbl.config(text="Sırada bekliyor…")
            _tid.sira_bekle_ve_baslat(
                self.app, self.kontrol.ad, self.kontrol,
                lambda: self._sorgu_gercek_baslat(kayitlar, aciklama, mod, devam, taraf_da),
                durum_fn=lambda t: self.durum_lbl.config(text=t))
            return
        self._sorgu_gercek_baslat(kayitlar, aciklama, mod, devam, taraf_da)

    def _sorgu_gercek_baslat(self, kayitlar, aciklama, mod=barkod_sorgu.MOD_IKISI, devam=True, taraf_da=False):
        self.kontrol.sifirla()
        self._calisiyor = True
        for b in self.islem_btnlari:
            b.set_state("disabled")
        self.duraklat_btn.set_state("normal")
        self.duraklat_btn.set_text("Duraklat")
        self.durdur_btn.set_state("normal")
        self.durum_lbl.config(text=f"{len(kayitlar)} dosya işleniyor ({self._MOD_ADI[mod]})…")
        self._append_log(f"▶ {len(kayitlar)} {aciklama} dosya için {self._MOD_ADI[mod]} başlıyor…")

        def bg():
            try:
                sonuc = barkod_sorgu.calistir(kayitlar, self._log, kontrol=self.kontrol, mod=mod, devam=devam,
                                              taraf_da=taraf_da)
                self.result_q.put(("sorgu_bitti", sonuc))
            except Exception as e:
                self.result_q.put(("sorgu_hata", str(e)))
        threading.Thread(target=bg, daemon=True).start()

    def duraklat_toggle(self):
        if not self._calisiyor:
            return
        paused = self.kontrol.toggle_pause()
        self.duraklat_btn.set_text("Devam" if paused else "Duraklat")

    def durdur(self):
        if self._calisiyor:
            self.kontrol.durdur()

    def _sorgu_bitti_ui(self):
        self._calisiyor = False
        for b in self.islem_btnlari:
            b.set_state("normal")
        self.duraklat_btn.set_state("disabled")
        self.duraklat_btn.set_text("Duraklat")
        self.durdur_btn.set_state("disabled")
        toplu_is_kontrol.KAYIT_DEFTERI.sil(self.kontrol.ad)

    def _sorgu_bitti(self, sonuc):
        self._sorgu_bitti_ui()
        adet = len(sonuc) if sonuc else 0
        self.durum_lbl.config(text=f"✔ Sorgu tamamlandı ({adet} satır sonuç).")
        self._append_log(f"✅ Sorgu tamamlandı — {adet} satır sonuç.")
        self._gecmis_yukle_bg()  # yeni yazılan sonuçlar geçmiş bölümüne yansısın
        # … ve üstteki dosya seçici listedeki Barkod/Durum sütunlarına da
        # (bkz. `_barkod_satirlarina_ac`) — o anki filtre KORUNARAK.
        self._filtrele_bg(self._filtreleri_topla())

    def _sorgu_hata(self, hata):
        self._sorgu_bitti_ui()
        self.durum_lbl.config(text="Sorgu sırasında hata oluştu.")
        self._append_log(f"❌ Hata: {hata}")

    # ─────────────────────────── polling ───────────────────────────
    def _poll(self):
        try:
            while True:
                tip, veri = self.result_q.get_nowait()
                try:
                    if tip == "alanlar":
                        birimler, durumlar = veri
                        self._birim_kod = {b.get("ad", b.get("birimId", "")): b.get("birimId", "") for b in birimler}
                        self.birim_cb["values"] = ["Tümü"] + list(self._birim_kod.keys())
                        self._durum_kod = {ad: kod for kod, ad in durumlar}
                        self.durum_cb["values"] = ["Tümü"] + list(self._durum_kod.keys())
                    elif tip == "kayitlar":
                        self._tabloyu_doldur(veri)
                    elif tip == "gecmis":
                        self.gecmis_ham = veri
                        self._gecmis_uygula()
                    elif tip == "t212_sonuc":
                        self._t212_sonuc_goster(veri)
                    elif tip == "t212_hata":
                        self._t212_hata(veri)
                    elif tip == "t212_son_rapor":
                        self._t212_son_rapor_goster(veri)
                    elif tip == "t212_son_rapor_hata":
                        self.durum_lbl.config(text=f"Son rapor yüklenemedi: {veri}")
                    elif tip == "sorgu_bitti":
                        self._sorgu_bitti(veri)
                    elif tip == "sorgu_hata":
                        self._sorgu_hata(veri)
                    elif tip == "excel_tamam":
                        self._excel_aktarildi(veri)
                    elif tip == "excel_hata":
                        self._excel_hata(veri)
                    elif tip == "log":
                        self._append_log(str(veri))
                except Exception as e:
                    self._append_log(f"⚠️ Ekran güncellenemedi ({tip}): {e}")
        except queue.Empty:
            pass
        if self.parent.winfo_exists():
            self.app.after(300, self._poll)
