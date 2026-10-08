# -*- coding: utf-8 -*-
"""
Modül: İcra > Dosyalarım  (Dosya Sorgulama)
===========================================
SAKİN tarzda arayüz; ÇALIŞMA MANTIĞI icra_core üzerinden, suite'in KANITLI
tekniğiyle çalışır (orijinal sgk_sorgu_gui.SorguMotoru._post → 127.0.0.1:8800
ofis proxy'si → canlı e-imza UYAP oturumu). Tarayıcı/Playwright YOK.

Tasarım — TEK alan:
• Sonuç tablosunun en üstünde her kolon için: BAŞLIK (label) + hemen altında o
  kolonun "yazılabilir liste-kutusu" (combobox). Aynı kutu hem sunucu kriteridir
  (Sorgula) hem de dönen sonuçları anında daraltan filtredir. Giriş ve sonuç
  artık iki ayrı yerde değil; tek hizalı tablodadır.
• Durum (Açık/Kapalı) üstte, ayrı ve önemli ilk filtre (dosyaDurumKod).
• Açılış Tarih Aralığı üstte ayrı bir alanda (ileride ayrı işlenecek).
• Ham yanıt / JSON detay bölmesi YOKTUR.

Alanlar OPSİYONEL, tek tek açılıp kapatılır; tercih kaydedilir
(uyap_app_config.json → "icra_sorgu_alanlari"). UYAP Bağlantısı (Paylaş/Al)
açık değilse sorgu ofis proxy'sine ulaşamaz.
"""

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog

from theme import C, RoundButton
from . import icra_core
from . import dosya_core
from . import birim_bolme
from . import dosya_karti
from . import evrak_indirici

# BARE (paket-göreli DEĞİL) — toplu_is_kontrol/toplu_is_dialog'un TÜM
# tüketicilerde AYNI tekil (KAYIT_DEFTERI) nesneyi görmesi için şart: bu iki
# modül HER YERDE bare import edilir (bkz. toplu_is_kontrol.py başlığı).
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import toplu_is_kontrol  # noqa: E402
import toplu_is_dialog as _tid  # noqa: E402


def _sayac_metni(tum_kayitlar, gorunen_satirlar):
    """"120 dosya · 134 satır" — satırlar borçlu başına açıldığından dosya ve
    satır sayısı ayrı yazılır; süzgeçte "gösterilen / toplam"."""
    toplam = len(tum_kayitlar)
    if not toplam:
        return ""
    tum_satir = sum(max(len(r.get("borclu_satirlari") or []), 1) for r in tum_kayitlar)
    ek = f" · {tum_satir} satır" if tum_satir != toplam else ""
    if len(gorunen_satirlar) != tum_satir:
        return f"{len(gorunen_satirlar)} / {tum_satir} satır ({toplam} dosya)"
    return f"{toplam} dosya{ek}"


class IcraDosyalarimEkrani:
    # Açılış tarihi alanları kolon/filtre değil; üstte ayrı durur.
    DATE_KEYS = ("dosyaAcilisTarihiStart", "dosyaAcilisTarihiEnd")
    HEAD_H = 56                 # başlık+combobox şeridi yüksekliği (px)

    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.engine = icra_core.IcraSorgu(self._log)
        self.kontrol = toplu_is_kontrol.TopluIsKontrolu(ad="Dosya Sorgulama")

        self.vars = {f["key"]: tk.StringVar() for f in icra_core.FIELDS}
        self.combos = {}           # key -> ttk.Combobox (tablo başlığındaki kutular)
        self._combo_full = {}      # key -> tam değer listesi (oto-tamamlama için)
        self._ac = None            # oto-tamamlama açılır penceresi (Toplevel)
        self._ac_list = None       # içindeki Listbox
        self._ac_key = None        # o an aktif kutu anahtarı
        self.active = self._load_active()
        self.edit_acik = False

        self.durum_var = tk.StringVar(value=icra_core.DURUMLAR[0][0])
        self._durum_kod = {lbl: kod for lbl, kod in icra_core.DURUMLAR}

        # Açık "Taraf ile Ara" alanları (tahminsiz manuel arama).
        self.taraf_tur_var = tk.StringVar(value="gercek")
        self.taraf_vars = {k: tk.StringVar() for k in
                           ("taraf_ad", "taraf_soyad", "taraf_tckn",
                            "taraf_kurum", "taraf_vergi", "taraf_mersis")}

        # "Daireyi Böl" (2026-09-24): birimAdi sütunu Yer | No | Birim Türü | Not
        # olarak gösterilir. Bu sütunların kutuları YALNIZ yerel filtredir —
        # Sorgula'nın birim kriteri (self.vars["birimAdi"]) değişmez.
        self._daire_bolunmus = False
        self._bol_vars = {k: tk.StringVar() for k in birim_bolme.BOLUNMUS_ANAHTARLAR}
        self._bol_onbellek = {}

        self.all_records = []
        self._gorunen_kayitlar = []  # tablo satırı (iid) -> ham kayıt eşlemesi ("Dosya Görüntüle")
        self.columns = []          # tablo kolonları = aktif alanlar (tarih hariç)
        self.sort_key = None
        self.sort_reverse = False
        # Son çalıştırılan sorgunun kriterleri — "Dosya Görüntüle" bir kayıt
        # kaydettiğinde (bkz. _dosya_goruntule) tabloyu AYNI kriterlerle DB'den
        # tazelemek için (kullanıcı bulgusu: dosyalarim_genel.py'deki eşi
        # 2026-07-12'de düzeltilmişti, bu ekrana hiç taşınmamıştı).
        self._son_degerler = {}
        self._son_durum_kod = icra_core.DURUM_VARSAYILAN

        self.result_q = queue.Queue()
        self._logbuf = []
        self._loglock = threading.Lock()
        self.running = False

        self._init_style()
        self._build()
        self._poll()
        # İcra daireleri açılır listesini arka planda yükle (GUI'yi bloklamaz).
        threading.Thread(target=self._birim_listesi_yukle_bg, daemon=True).start()
        # UyapMasa açılır açılmaz eldeki DB kayıtlarını göster (UYAP beklemeden).
        threading.Thread(target=self._acilista_db_yukle_bg, daemon=True).start()
        # 2026-09-25 kullanıcı kararı: 20 sn'lik otomatik DB tazelemesi KALDIRILDI
        # ("sürekli değişiklik kontrolü programı, bağlantıyı, diski yorar") —
        # liste açılışta ve kullanıcı Sorgula/Filtrele'ye basınca okunur.

    # ─────────────────────────── tercih kalıcı ───────────────────────────
    def _auth(self):
        return getattr(self.app, "_auth", None)

    def _load_active(self):
        auth = self._auth()
        if auth:
            try:
                cfg = auth.load_config()
                kayitli = cfg.get("icra_sorgu_alanlari")
                if isinstance(kayitli, list):
                    gecerli = icra_core.gecerli_alanlar(kayitli)
                    if gecerli:
                        # YENİ eklenen varsayılan sütunlar (ör. kesinlesme_durumu/
                        # tebligat_durumu, 2026-08-14) eski KAYDEDİLMİŞ tercihte
                        # yoksa burada eklenir — yoksa "FIELDS'e ekledim" YETMEZ,
                        # daha önce bu ekranda kolon seçimini özelleştirmiş
                        # kullanıcı yeni varsayılan sütunları HİÇ GÖRMEZ (kayıtlı
                        # tercih eskiyi aynen korur, yeniyi bilmez).
                        for k in icra_core.DEFAULT_ACTIVE:
                            if k not in gecerli:
                                gecerli.append(k)
                        return gecerli
            except Exception:
                pass
        return list(icra_core.DEFAULT_ACTIVE)

    def _save_active(self):
        auth = self._auth()
        if not auth:
            return
        try:
            cfg = auth.load_config()
            cfg["icra_sorgu_alanlari"] = list(self.active)
            auth.save_config(cfg)
        except Exception:
            pass

    def _kolonlar(self):
        """Tablo kolonları = kullanıcının seçtiği alanlar (self.active — "Alanları
        Düzenle" checkbox'larından, bkz. _edit_degisti). ÖNCEDEN burada sabit 7
        kolonluk bir liste vardı (kullanıcı bulgusu, 2026-08-14: "Kesinleşme/
        Tebliğ Durumu'nu İcra Dosyalarım'a eklememişsin" — FIELDS'e eklenmiş
        olmaları YETMİYORDU, çünkü tablo self.active'i HİÇ OKUMUYORDU; "Alanları
        Düzenle" checkbox'ları da bu YÜZDEN görünüşte hiçbir şey değiştirmiyordu)."""
        kolonlar = [k for k in self.active if k not in self.DATE_KEYS]
        if self._daire_bolunmus and "birimAdi" in kolonlar:
            i = kolonlar.index("birimAdi")
            kolonlar[i:i + 1] = list(birim_bolme.BOLUNMUS_ANAHTARLAR)
        return kolonlar

    def _deger(self, rec, k):
        """Hücre metni — bölünmüş daire sütunlarını da karşılar."""
        if k in birim_bolme.BOLUNMUS_ANAHTARLAR:
            ad = icra_core.kolon_degeri(rec, "birimAdi")
            b = self._bol_onbellek.get(ad)
            if b is None:
                b = self._bol_onbellek[ad] = birim_bolme.birim_adi_bol(ad)
            return str(b[k])
        return icra_core.kolon_degeri(rec, k)

    def _etiket(self, k):
        if k in birim_bolme.BOLUNMUS_ANAHTARLAR:
            return dict((a, l) for a, l, _w in birim_bolme.BOLUNMUS_KOLONLAR)[k]
        return icra_core.FIELD_BY_KEY[k]["label"]

    def _daire_bol_toggle(self):
        self._daire_bolunmus = not self._daire_bolunmus
        if self.sort_key in birim_bolme.BOLUNMUS_ANAHTARLAR and not self._daire_bolunmus:
            self.sort_key = None
        self.daire_bol_btn.set_text("Daireyi Birleştir ⇄" if self._daire_bolunmus else "Daireyi Böl ⇄")
        self._tabloyu_kur()

    # ─────────────────────────── ttk stilleri ───────────────────────────
    def _init_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Icra.Treeview", background=C.CARD, fieldbackground=C.CARD,
                        foreground=C.INK, bordercolor=C.CARD_EDGE, borderwidth=0,
                        rowheight=26, font=self.app.f_body)
        style.map("Icra.Treeview",
                  background=[("selected", C.SAGE_TINT)], foreground=[("selected", C.INK)])

    # ─────────────────────────── arayüz ───────────────────────────
    def _build(self):
        dis = tk.Frame(self.parent, bg=C.BG)
        dis.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(dis, bg=C.BG, highlightthickness=0)
        vbar = tk.Scrollbar(dis, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._wrap = tk.Frame(self._canvas, bg=C.BG)
        self._cwin = self._canvas.create_window((0, 0), window=self._wrap, anchor="nw")
        self._son_h = None
        self._wrap.bind("<Configure>", lambda e: self._scroll_guncelle())
        self._canvas.bind("<Configure>", lambda e: (
            self._canvas.itemconfigure(self._cwin, width=e.width), self._scroll_guncelle()))
        self._scroll_wheel_bagla()

        wrap = tk.Frame(self._wrap, bg=C.BG)
        wrap.pack(fill="both", expand=True, padx=40, pady=(34, 24))

        tk.Label(wrap, text="İcra · Dosyalarım", bg=C.BG, fg=C.INK,
                 font=self.app.f_h1).pack(anchor="w")
        tk.Label(wrap, text="UYAP Dosya Sorgulama (İcra · İcra Dairesi). İstekler yerel "
                            "ofis bağlantısı üzerinden gönderilir; UYAP Bağlantısı açık olmalı.",
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_sub).pack(anchor="w", pady=(6, 14))

        # ── üst kriter çubuğu: Durum + Açılış Tarih Aralığı ──
        ust = tk.Frame(wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        ust.pack(fill="x")
        ui = tk.Frame(ust, bg=C.CARD)
        ui.pack(fill="x", padx=18, pady=12)

        tk.Label(ui, text="Durum", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(0, 6))
        ttk.Combobox(ui, textvariable=self.durum_var, state="readonly",
                     values=[lbl for lbl, _ in icra_core.DURUMLAR],
                     font=self.app.f_body, width=9).pack(side="left", padx=(0, 22))

        tk.Label(ui, text="Açılış Tarihi", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(0, 6))
        self._tarih_entry(ui, "dosyaAcilisTarihiStart")
        tk.Label(ui, text="–", bg=C.CARD, fg=C.INK_FAINT,
                 font=self.app.f_body).pack(side="left", padx=4)
        self._tarih_entry(ui, "dosyaAcilisTarihiEnd")

        # ── ikinci satır: Taraf ile Ara (gerçek kişi / kurum) ──
        tk.Frame(ust, bg=C.LINE, height=1).pack(fill="x", padx=18)
        self._taraf_section(ust)

        # ── düğmeler ──
        bar = tk.Frame(wrap, bg=C.BG)
        bar.pack(fill="x", pady=(12, 0))
        self.sorgula_btn = self._btn(bar, "Sorgula", self.sorgula, "primary")
        self.sorgula_btn.pack(side="left", ipadx=6)
        # 2026-09-30 kullanıcı isteği: künye + taraf + barkod + kesinleşme birlikte
        # (Tüm Dosyalarım'ın Yenile'si, İcra'ya sınırlı ve barkod katmanı açık).
        self.guncelle_btn = self._btn(bar, "UYAP'tan Güncelle", self._uyap_guncelle, "ghost")
        self.guncelle_btn.pack(side="left", padx=(8, 0), ipadx=2)
        # 2026-09-30: üç ekranda AYNI düğme (bkz. dosyalarim_genel / barkod_sorgu_ekrani).
        self.secili_guncelle_btn = self._btn(bar, "Seçilenleri UYAP'tan Güncelle", self._secilenleri_guncelle, "ghost")
        self.secili_guncelle_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.duraklat_btn = self._btn(bar, "Duraklat", self.duraklat_toggle, "ghost")
        self.duraklat_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.duraklat_btn.set_state("disabled")
        self.durdur_btn = self._btn(bar, "Durdur", self.durdur, "ghost")
        self.durdur_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.durdur_btn.set_state("disabled")
        self._btn(bar, "Temizle", self.temizle, "ghost").pack(side="left", padx=(8, 0), ipadx=2)
        self.edit_btn = self._btn(bar, "Alanları Düzenle", self.edit_toggle, "ghost")
        self.edit_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.detay_btn = self._btn(bar, "Dosya Görüntüle", self._dosya_goruntule, "ghost")
        self.detay_btn.pack(side="left", padx=(8, 0), ipadx=2)
        # 2026-10-06 kullanıcı isteği: Hukuk Dosyalarım'daki "Tüm Belgeleri İndir"in
        # eşi — aynı evrak_indirici.calistir (ham evrak + Birlesik.pdf).
        self.evrak_indir_btn = self._btn(bar, "Seçilenlerin Tüm Evrakını İndir",
                                         self._secilenlerin_evrakini_indir, "ghost")
        self.evrak_indir_btn.pack(side="left", padx=(8, 0), ipadx=2)
        # Web'deki "Barkod'u da Güncelle" düğmesinin eşi (2026-09-23): DB'deki
        # TÜM açık İcra dosyalarının barkodlarını Sorgula'dan BAĞIMSIZ tazeler.
        self.barkod_btn = self._btn(bar, "Barkod'u da Güncelle", self._barkod_hepsini_guncelle, "ghost")
        self.barkod_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.daire_bol_btn = self._btn(bar, "Daireyi Böl ⇄", self._daire_bol_toggle, "ghost")
        self.daire_bol_btn.pack(side="left", padx=(8, 0), ipadx=2)
        self.durum_lbl = tk.Label(bar, text="", bg=C.BG, fg=C.INK_SOFT,
                                  font=self.app.f_small)
        self.durum_lbl.pack(side="right")

        # ── alan seçimi (katlanır) ──
        self.edit_frame = tk.Frame(wrap, bg=C.BG)

        # ── sayaç satırı ──
        fb = tk.Frame(wrap, bg=C.BG)
        fb.pack(fill="x", pady=(14, 0))
        tk.Label(fb, text="Sonuçlar", bg=C.BG, fg=C.INK, font=self.app.f_card_t).pack(side="left")
        tk.Label(fb, text="(başlık altındaki kutulara yazarak/seçerek daraltın)", bg=C.BG,
                 fg=C.INK_FAINT, font=self.app.f_small).pack(side="left", padx=(8, 0))
        self.sayac_lbl = tk.Label(fb, text="", bg=C.BG, fg=C.INK_FAINT,
                                  font=self.app.f_small)
        self.sayac_lbl.pack(side="right")

        # ── TEK tablo: başlık şeridi (label+combobox) + veri ──
        card = tk.Frame(wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        card.pack(fill="both", expand=True, pady=(8, 0))

        self.head_canvas = tk.Canvas(card, bg=C.SIDEBAR, highlightthickness=0,
                                     height=self.HEAD_H)
        self.head_inner = tk.Frame(self.head_canvas, bg=C.SIDEBAR)
        self.head_win = self.head_canvas.create_window((0, 0), window=self.head_inner,
                                                       anchor="nw")
        self.head_canvas.grid(row=0, column=0, sticky="ew")

        self.tree = ttk.Treeview(card, columns=("_ph",), show="", style="Icra.Treeview",
                                 height=14)
        self.tree.grid(row=1, column=0, sticky="nsew")
        ysb = tk.Scrollbar(card, command=self.tree.yview)
        ysb.grid(row=1, column=1, sticky="ns")
        xsb = tk.Scrollbar(card, orient="horizontal", command=self.tree.xview)
        xsb.grid(row=2, column=0, sticky="ew")

        def _xset(first, last):
            xsb.set(first, last)
            try:
                self.head_canvas.xview_moveto(float(first))
            except Exception:
                pass
        self.tree.configure(xscrollcommand=_xset, yscrollcommand=ysb.set)
        card.rowconfigure(1, weight=1)
        card.columnconfigure(0, weight=1)

        # ── günlük (yalnızca hata/ilerleme) ──
        lbox = tk.Frame(wrap, bg="#FBFAF7", highlightbackground=C.CARD_EDGE, highlightthickness=1)
        lbox.pack(fill="x", pady=(12, 0))
        self.log = tk.Text(lbox, bg="#FBFAF7", fg=C.INK, relief="flat",
                           font=self.app.f_mono, wrap="word", height=4,
                           padx=12, pady=8, state="disabled", highlightthickness=0)
        self.log.pack(side="left", fill="both", expand=True)
        lsb = tk.Scrollbar(lbox, command=self.log.yview)
        lsb.pack(side="right", fill="y")
        self.log.config(yscrollcommand=lsb.set)

        self._render_edit()
        self._tabloyu_kur()

    def _tarih_entry(self, parent, key):
        e = tk.Entry(parent, textvariable=self.vars[key], bg="#FFFFFF", fg=C.INK,
                     relief="flat", insertbackground=C.INK, font=self.app.f_body,
                     width=12, highlightthickness=1, highlightbackground=C.LINE,
                     highlightcolor=C.SAGE)
        e.pack(side="left", ipady=3)
        e.bind("<Return>", lambda ev: self.sorgula())
        return e

    # ─────────────────────────── Taraf ile Ara (tahminsiz) ───────────────────────────
    def _taraf_section(self, parent):
        """Açık taraf arama şeridi: Gerçek Kişi (Ad/Soyad/TCKN) ↔ Kurum
        (Kurum Adı/Vergi/MERSİS). Tür kullanıcı tarafından seçildiği için
        sunucuya tahminsiz, kesin alanlar gider."""
        ti = tk.Frame(parent, bg=C.CARD)
        ti.pack(fill="x", padx=18, pady=12)

        tk.Label(ti, text="Taraf ile Ara", bg=C.CARD, fg=C.SAGE_DK,
                 font=self.app.f_nav_b).pack(side="left", padx=(0, 10))

        for lbl, val in (("Gerçek Kişi", "gercek"), ("Kurum", "kurum")):
            tk.Radiobutton(ti, text=lbl, value=val, variable=self.taraf_tur_var,
                           bg=C.CARD, fg=C.INK, activebackground=C.CARD,
                           activeforeground=C.INK, selectcolor=C.CARD,
                           font=self.app.f_body, bd=0, highlightthickness=0,
                           cursor="hand2", command=self._taraf_tur_degisti
                           ).pack(side="left", padx=(0, 4))

        # alan grupları — yalnızca seçili tür görünür
        self.taraf_kisi_frame = tk.Frame(ti, bg=C.CARD)
        self.taraf_kurum_frame = tk.Frame(ti, bg=C.CARD)

        self._taraf_alan(self.taraf_kisi_frame, "Ad", "taraf_ad", 14)
        self._taraf_alan(self.taraf_kisi_frame, "Soyad", "taraf_soyad", 14)
        self._taraf_alan(self.taraf_kisi_frame, "TCKN", "taraf_tckn", 12)

        self._taraf_alan(self.taraf_kurum_frame, "Kurum Adı", "taraf_kurum", 26)
        self._taraf_alan(self.taraf_kurum_frame, "Vergi No", "taraf_vergi", 12)
        self._taraf_alan(self.taraf_kurum_frame, "MERSİS", "taraf_mersis", 16)

        self._taraf_tur_degisti()

    def _taraf_alan(self, parent, label, key, width):
        tk.Label(parent, text=label, bg=C.CARD, fg=C.INK_SOFT,
                 font=self.app.f_small).pack(side="left", padx=(0, 4))
        e = tk.Entry(parent, textvariable=self.taraf_vars[key], bg="#FFFFFF", fg=C.INK,
                     relief="flat", insertbackground=C.INK, font=self.app.f_body,
                     width=width, highlightthickness=1, highlightbackground=C.LINE,
                     highlightcolor=C.SAGE)
        e.pack(side="left", ipady=3, padx=(0, 12))
        e.bind("<Return>", lambda ev: self.sorgula())
        return e

    def _taraf_tur_degisti(self):
        if self.taraf_tur_var.get() == "kurum":
            self.taraf_kisi_frame.pack_forget()
            self.taraf_kurum_frame.pack(side="left", padx=(8, 0))
        else:
            self.taraf_kurum_frame.pack_forget()
            self.taraf_kisi_frame.pack(side="left", padx=(8, 0))

    def _btn(self, parent, text, cmd, kind):
        return RoundButton(parent, text, command=cmd, kind=kind,
                           font=self.app.f_nav_b, height=36)

    # ─────────────────────────── dikey kaydırma ───────────────────────────
    def _scroll_guncelle(self):
        if not self._canvas.winfo_exists():
            return
        ch = self._canvas.winfo_height()
        req = self._wrap.winfo_reqheight()
        h = max(req, ch)
        if h != self._son_h:
            self._son_h = h
            self._canvas.itemconfigure(self._cwin, height=h)
        self._canvas.configure(scrollregion=(0, 0, self._canvas.winfo_width(), h))

    def _scroll_wheel_bagla(self):
        def _wheel(e):
            w = self.parent.winfo_containing(e.x_root, e.y_root)
            while w is not None:
                if w in (getattr(self, "tree", None), getattr(self, "log", None)):
                    return
                w = getattr(w, "master", None)
            self._canvas.yview_scroll(int(-e.delta / 120), "units")
        self._canvas.bind("<Enter>", lambda e: self._canvas.bind_all("<MouseWheel>", _wheel))
        self._canvas.bind("<Leave>", lambda e: self._canvas.unbind_all("<MouseWheel>"))

    # ─────────────────────────── alan seçimi ───────────────────────────
    def _render_edit(self):
        for w in self.edit_frame.winfo_children():
            w.destroy()
        kart = tk.Frame(self.edit_frame, bg=C.CARD, highlightbackground=C.CARD_EDGE,
                        highlightthickness=1)
        kart.pack(fill="x", pady=(10, 0))
        tk.Label(kart, text="Tablo kolonlarını seç (tercih kaydedilir)", bg=C.CARD,
                 fg=C.INK, font=self.app.f_nav_b).pack(anchor="w", padx=16, pady=(12, 2))
        tk.Label(kart, text="Açık alanlar tablonun başlık+kutu kolonlarıdır. Kişi ile kurum "
                            "birlikte doldurulursa kurum önceliklidir. (Açılış Tarihi üstte ayrıdır.)",
                 bg=C.CARD, fg=C.INK_FAINT, font=self.app.f_small).pack(anchor="w",
                 padx=16, pady=(0, 8))
        self.edit_vars = {}
        for grp in icra_core.GROUPS:
            grp_keys = [f for f in icra_core.FIELDS
                        if f["group"] == grp and f["key"] not in self.DATE_KEYS]
            if not grp_keys:
                continue
            satir = tk.Frame(kart, bg=C.CARD)
            satir.pack(fill="x", padx=16, pady=2)
            tk.Label(satir, text=grp.upper(), bg=C.CARD, fg=C.SAGE_DK,
                     font=self.app.f_nav_b, width=8, anchor="w").pack(side="left", padx=(0, 8))
            for f in grp_keys:
                var = tk.BooleanVar(value=(f["key"] in self.active))
                self.edit_vars[f["key"]] = var
                tk.Checkbutton(satir, text=f["label"], variable=var, bg=C.CARD, fg=C.INK,
                               activebackground=C.CARD, activeforeground=C.INK,
                               selectcolor=C.CARD, font=self.app.f_small, bd=0,
                               highlightthickness=0, cursor="hand2",
                               command=self._edit_degisti).pack(side="left", padx=6)
        tk.Frame(kart, bg=C.CARD, height=8).pack()

    def edit_toggle(self):
        self.edit_acik = not self.edit_acik
        if self.edit_acik:
            self.edit_frame.pack(fill="x", after=self._edit_anchor())
            self.edit_btn.set_text("Alanları Gizle")
        else:
            self.edit_frame.pack_forget()
            self.edit_btn.set_text("Alanları Düzenle")

    def _edit_anchor(self):
        # düğme çubuğunun (edit_frame'den bir önceki kardeş) hemen ardına yerleşir
        kardesler = self.edit_frame.master.winfo_children()
        i = kardesler.index(self.edit_frame)
        return kardesler[i - 1]

    def _edit_degisti(self):
        # tarih anahtarları her zaman korunur (üstte ayrı), kolonlar checkbox'tan
        secili = [f["key"] for f in icra_core.FIELDS
                  if f["key"] not in self.DATE_KEYS
                  and self.edit_vars.get(f["key"]) and self.edit_vars[f["key"]].get()]
        tarih = [k for k in self.active if k in self.DATE_KEYS]
        self.active = secili + tarih
        self._save_active()
        self.sort_key = None
        self._tabloyu_kur()

    # ─────────────────────────── tablo (başlık şeridi + veri) ───────────────────────────
    def _tabloyu_kur(self):
        self.columns = self._kolonlar()
        # başlık şeridi yeniden kur
        for w in self.head_inner.winfo_children():
            w.destroy()
        self.combos = {}

        if not self.columns:
            tk.Label(self.head_inner, text="Kolon yok — “Alanları Düzenle” ile alan ekleyin.",
                     bg=C.SIDEBAR, fg=C.INK_FAINT, font=self.app.f_small).grid(
                     row=0, column=0, padx=10, pady=18, sticky="w")
            self.tree["displaycolumns"] = "#all"
            self.tree["columns"] = ("_ph",)
            self.tree["displaycolumns"] = ("_ph",)
            self.tree.column("_ph", width=400, anchor="w", stretch=True)
            self._tabloyu_doldur()
            self._head_olcule(400)
            return

        total = 0
        for i, k in enumerate(self.columns):
            W = self._kol_genislik(k)
            total += W
            cell = tk.Frame(self.head_inner, bg=C.SIDEBAR, width=W, height=self.HEAD_H)
            cell.grid(row=0, column=i, sticky="nsew")
            cell.grid_propagate(False)
            cell.columnconfigure(0, weight=1)

            ok = ""
            if k == self.sort_key:
                ok = "  ▲" if not self.sort_reverse else "  ▼"
            lbl = tk.Label(cell, text=self._etiket(k) + ok, bg=C.SIDEBAR,
                           fg=C.INK, font=self.app.f_nav_b, anchor="w", cursor="hand2")
            lbl.grid(row=0, column=0, sticky="ew", padx=6, pady=(5, 2))
            lbl.bind("<Button-1>", lambda e, col=k: self._sort_by(col))

            if k in birim_bolme.BOLUNMUS_ANAHTARLAR:
                # Yalnız yerel filtre (Sorgula kriteri değil).
                cb = ttk.Combobox(cell, textvariable=self._bol_vars[k], state="normal",
                                  font=self.app.f_body, width=4)
                cb.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
                for olay in ("<KeyRelease>", "<Return>", "<<ComboboxSelected>>"):
                    cb.bind(olay, lambda ev: self._canli_filtrele())
                self.combos[k] = cb
                continue
            cb = ttk.Combobox(cell, textvariable=self.vars[k], state="normal",
                              font=self.app.f_body, width=4)
            cb.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
            cb.bind("<Return>", lambda ev, col=k: self._combo_return(col))
            cb.bind("<KeyRelease>", lambda ev, col=k: self._oto_tamamla(ev, col))
            cb.bind("<<ComboboxSelected>>", lambda ev: self._canli_filtrele())
            cb.bind("<FocusOut>", lambda ev: self.app.after(150, self._ac_gizle))
            self.combos[k] = cb

        # tree kolonları (sabit genişlik, başlık bandı gizli). displaycolumns
        # önce "#all": eski sütun adlarını tutarsa columns değişimi TclError verir.
        self.tree["displaycolumns"] = "#all"
        self.tree["columns"] = self.columns
        self.tree["displaycolumns"] = self.columns
        for k in self.columns:
            self.tree.column(k, width=self._kol_genislik(k), anchor="w", stretch=False)
        self._tabloyu_doldur()
        self._combolari_doldur()
        self._head_olcule(total)

    def _head_olcule(self, total):
        """Başlık şeridini içeriğe göre boyutlandır + yatay kaydırmayı tree ile eşle."""
        self.head_canvas.itemconfigure(self.head_win, width=total, height=self.HEAD_H)
        self.head_canvas.configure(scrollregion=(0, 0, total, self.HEAD_H))

    @staticmethod
    def _kol_genislik(c):
        bolunmus = {a: w for a, _l, w in birim_bolme.BOLUNMUS_KOLONLAR}
        if c in bolunmus:
            return bolunmus[c]
        c = c.lower()
        if c in ("alacakli", "borclu"):
            return 200
        if "kurum" in c or "birim" in c or c in ("adi", "soyadi"):
            return 200
        if c == "dosyatur":
            return 150
        if "tckimlik" in c or "mersis" in c or "vergi" in c:
            return 150
        if c in ("tebligat_durumu", "kesinlesme_durumu"):
            return 200
        return 120

    def _kriterler(self):
        out = {}
        for k in self.columns:
            cb = self.combos.get(k)
            if cb is not None:
                q = icra_core.tr_lower(cb.get().strip())
                if q:
                    out[k] = q
        return out

    def _gorunen(self):
        # Çok borçlu dosya borçlu başına satır (2026-09-30, bkz. dosya_core.borclu_basina_ac).
        kayitlar = dosya_core.borclu_basina_ac(self.all_records)
        kriter = self._kriterler()
        if kriter:
            def uyar(r):
                for k, q in kriter.items():
                    if q not in icra_core.tr_lower(self._deger(r, k)):
                        return False
                return True
            kayitlar = [r for r in kayitlar if uyar(r)]
        if self.sort_key:
            # dosyalarim_genel.py'deki AYNI disiplin (2026-08-14, kullanıcı
            # bulgusu: burada tarih sütunları METİN olarak sıralanıp GÜN
            # basamağına göre karışıyordu — bkz. icra_core.tarih_siralama_
            # anahtari). Ayrıca geçersiz/boş değerler HER İKİ yönde de SONA
            # sabitlenir (o ekrandaki AYNI düzeltme, kullanıcı bulgusu
            # 2026-07-13: reverse=True'da eskiden başa zıplıyordu).
            tarih_mi = (icra_core.FIELD_BY_KEY.get(self.sort_key) or {}).get("type") == "date"

            def anahtar_bul(r):
                v = self._deger(r, self.sort_key)
                if tarih_mi:
                    k = icra_core.tarih_siralama_anahtari(v)
                    return k
                if not v:
                    return None
                try:
                    return (0, float(v.replace(",", ".")))
                except (ValueError, AttributeError):
                    return (1, v.lower())

            gecerliler, gecersizler = [], []
            for r in kayitlar:
                a = anahtar_bul(r)
                (gecersizler if a is None else gecerliler).append((a, r))
            gecerliler.sort(key=lambda p: p[0], reverse=self.sort_reverse)
            kayitlar = [r for _a, r in gecerliler] + [r for _a, r in gecersizler]
        return kayitlar

    @staticmethod
    def _cell(v):
        t = str(v).replace("\n", " ")
        return (t[:120] + "…") if len(t) > 120 else t

    def _tabloyu_doldur(self):
        self.tree.delete(*self.tree.get_children())
        if not self.columns:
            self.sayac_lbl.config(text="")
            self._gorunen_kayitlar = []
            return
        kayitlar = self._gorunen()
        self._gorunen_kayitlar = kayitlar
        for i, rec in enumerate(kayitlar):
            vals = [self._cell(self._deger(rec, c)) for c in self.columns]
            self.tree.insert("", "end", iid=str(i), values=vals)
        self.sayac_lbl.config(text=_sayac_metni(self.all_records, kayitlar))

    def _combolari_doldur(self):
        for k in self.columns:
            cb = self.combos.get(k)
            if cb is None:
                continue
            if k == "dosyaYil":
                degerler = [str(y) for y in range(2026, 1987, -1)]
            elif k == "birimAdi":
                # İcra daireleri açılır listesi (UYAP birim listesinden); kullanıcı
                # seçince Sorgula sunucu-taraflı birimId ile arar. Liste henüz
                # yüklenmediyse eldeki sonuçların birimlerine düş.
                degerler = icra_core.birim_adlari()
                if degerler:
                    degerler = sorted(set(degerler), key=lambda s: s.lower())
                else:
                    degerler = sorted({self._deger(r, k) for r in self.all_records} - {""},
                                       key=lambda s: s.lower())
            else:
                # Borçlu başına satırlardan — kutuda "A, B" birleşik adı değil tek tek borçlular.
                degerler = sorted({self._deger(r, k) for r in dosya_core.borclu_basina_ac(self.all_records)}
                                  - {""}, key=lambda s: s.lower())
            self._combo_full[k] = degerler
            cb["values"] = degerler

    def _birim_listesi_yukle_bg(self):
        """İcra birim listesini arka planda çekip önbelleğe alır, sonra birim
        kutusunu tazeler (GUI'yi bloklamaz). Ofis (8800) kapalıysa YEREL DB'den
        doldurur — böylece birim filtresi çevrimdışı da çalışır."""
        try:
            icra_core.birim_listesi_getir(self._log)
        except Exception:
            pass
        if not icra_core.birim_adlari():     # ofis kapalı/erişilemez → DB yedeği
            try:
                icra_core.birimleri_db_den_yukle()
            except Exception:
                pass
        self.result_q.put(("birim", None))

    def _acilista_db_yukle_bg(self):
        """UyapMasa açılışında eldeki DB kayıtlarını (Açık) arka planda yükleyip gösterir;
        böylece ofis kapalı olsa bile kullanıcı verisini hemen görür."""
        try:
            kayitlar = icra_core.db_dosyalari_getir({}, icra_core.DURUM_VARSAYILAN)
        except Exception:
            kayitlar = []
        if kayitlar:
            self.result_q.put(("db_acilis", kayitlar))

    def _sort_by(self, col):
        if self.sort_key == col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_key = col
            self.sort_reverse = False
        self._tabloyu_kur()

    def _canli_filtrele(self):
        self._tabloyu_doldur()

    def _oto_tamamla(self, event, k):
        """Filtre kutusuna yazdıkça eşleşmeleri CANLI açılır listede gösterir (TR
        harf DUYARSIZ) ve tabloyu süzer. Ok tuşlarıyla listede gezilir, Enter ile
        seçilir, Esc ile kapanır."""
        ks = event.keysym
        if ks in ("Down", "Up"):
            if self._ac_key == k and self._ac_acik():
                self._ac_gez(ks)
            return
        if ks == "Escape":
            self._ac_gizle()
            return
        if ks in ("Return", "Tab", "Left", "Right", "Shift_L", "Shift_R",
                  "Control_L", "Control_R", "Alt_L", "Alt_R"):
            return
        cb = self.combos.get(k)
        if cb is None:
            return
        tam = self._combo_full.get(k, [])
        nq = icra_core.tr_lower(cb.get().strip())
        esles = [v for v in tam if nq in icra_core.tr_lower(v)] if (nq and tam) else []
        cb["values"] = esles if esles else tam
        if nq and esles:
            self._ac_goster(k, esles)
        else:
            self._ac_gizle()
        self._canli_filtrele()

    def _combo_return(self, k):
        """Enter: açılır listede seçili varsa onu al; yoksa sorguyu çalıştır."""
        if self._ac_key == k and self._ac_acik() and self._ac_list.curselection():
            self._ac_onayla()
        else:
            self._ac_gizle()
            self.sorgula()

    # ── oto-tamamlama açılır listesi (yazdıkça eşleşenler) ───────────────────
    def _ac_acik(self):
        try:
            return self._ac is not None and bool(self._ac.winfo_viewable())
        except Exception:
            return False

    def _ac_kur(self):
        if self._ac is not None:
            return
        top = tk.Toplevel(self.app)
        top.withdraw()
        try:
            top.overrideredirect(True)
            top.attributes("-topmost", True)
        except Exception:
            pass
        lb = tk.Listbox(top, activestyle="dotbox", highlightthickness=1,
                        highlightbackground="#cccccc", bd=0, font=self.app.f_body,
                        bg="#FFFFFF", fg=C.INK, selectbackground="#dbe6f3",
                        selectforeground=C.INK, exportselection=False)
        lb.pack(fill="both", expand=True)
        lb.bind("<ButtonRelease-1>", lambda e: self._ac_onayla())
        lb.bind("<Return>", lambda e: self._ac_onayla())
        self._ac, self._ac_list = top, lb

    def _ac_goster(self, k, esles):
        self._ac_kur()
        cb = self.combos.get(k)
        if cb is None or not esles:
            self._ac_gizle()
            return
        self._ac_key = k
        lb = self._ac_list
        lb.delete(0, "end")
        for v in esles[:300]:
            lb.insert("end", v)
        lb["height"] = min(len(esles), 8)
        try:
            self._ac.update_idletasks()
            x = cb.winfo_rootx()
            y = cb.winfo_rooty() + cb.winfo_height()
            w = max(cb.winfo_width(), 200)
            h = self._ac.winfo_reqheight()
            self._ac.geometry(f"{w}x{h}+{x}+{y}")
            self._ac.deiconify()
            self._ac.lift()
        except Exception:
            self._ac_gizle()

    def _ac_gizle(self):
        if self._ac is not None:
            try:
                self._ac.withdraw()
            except Exception:
                pass
        self._ac_key = None

    def _ac_gez(self, yon):
        lb = self._ac_list
        if lb is None or not lb.size():
            return
        n = lb.size()
        cur = lb.curselection()
        i = cur[0] if cur else (-1 if yon == "Down" else 0)
        i = (i + 1) % n if yon == "Down" else (i - 1) % n
        lb.selection_clear(0, "end")
        lb.selection_set(i)
        lb.activate(i)
        lb.see(i)

    def _ac_onayla(self):
        if self._ac_key is None or self._ac_list is None:
            self._ac_gizle()
            return
        sec = self._ac_list.curselection()
        if not sec:
            self._ac_gizle()
            return
        val = self._ac_list.get(sec[0])
        k = self._ac_key
        self.vars[k].set(val)
        self._ac_gizle()
        cb = self.combos.get(k)
        if cb is not None:
            try:
                cb.icursor("end")
                cb.focus_set()
            except Exception:
                pass
        self._canli_filtrele()

    # ─────────────────────────── sorgu ───────────────────────────
    def sorgula(self):
        if self.running:
            return
        values = {k: v.get() for k, v in self.vars.items()}
        values.update({k: v.get() for k, v in self.taraf_vars.items()})
        values["taraf_tur"] = self.taraf_tur_var.get()
        durum_kod = self._durum_kod.get(self.durum_var.get(), icra_core.DURUM_VARSAYILAN)
        akis = _tid.basvur_ile_cakisma_akisi(self.app, self.kontrol.ad, self.kontrol)
        if akis == "iptal":
            return
        if akis == "sirada":
            self.durum_lbl.config(text="Sırada bekliyor…")
            _tid.sira_bekle_ve_baslat(
                self.app, self.kontrol.ad, self.kontrol,
                lambda: self._gercek_sorgula(values, durum_kod),
                durum_fn=lambda t: self.durum_lbl.config(text=t))
            return
        self._gercek_sorgula(values, durum_kod)

    def _gercek_sorgula(self, values, durum_kod):
        self._son_degerler, self._son_durum_kod = values, durum_kod
        self._aktif_kontrol = self.kontrol
        self.kontrol.sifirla()
        self._set_running(True)
        self.durum_lbl.config(text="Sorgulanıyor…")
        threading.Thread(target=self._ara_bg, args=(values, durum_kod), daemon=True).start()

    def _barkod_hepsini_guncelle(self):
        """Web `icra_barkod_hepsini_guncelle_baslat` ile AYNI: DB'deki tüm açık
        İcra dosyaları `barkod_sorgu.calistir`'ın seçili-dosyalar moduna verilir.
        Kendi toplu-iş kontrolüyle ('Barkod Sorgulama') çalışır, Sorgula'nın
        Duraklat/Durdur düğmelerine bağlı değildir."""
        if getattr(self, "_barkod_calisiyor", False):
            return
        if not hasattr(self, "barkod_kontrol"):
            # Barkod Sorgu ekranının "Barkod Sorgulama" adından FARKLI — kayıt
            # defteri ada göre tutulur, aynı ad birbirinin kaydını silerdi.
            self.barkod_kontrol = toplu_is_kontrol.TopluIsKontrolu(ad="Barkod'u da Güncelle (İcra)")
        # Liste burada (ana akışta) alınır ki yarım kalmış tur varsa "devam /
        # baştan" sorulabilsin (2026-09-24).
        import barkod_sorgu
        try:
            self._barkod_secim = icra_core.acik_icra_dosyalari_secimi()
        except Exception as e:
            self._log(f"[Barkod] ⚠️ Açık dosya listesi alınamadı: {e}")
            return
        if not self._barkod_secim:
            self._log("[Barkod] Veritabanında açık İcra dosyası yok — önce Sorgula çalıştırın.")
            return
        devam = barkod_sorgu.devam_sor(self.app, self._barkod_secim, barkod_sorgu.MOD_IKISI)
        if devam is None:
            return
        self._barkod_devam = devam
        # sifirla() karma bağını da sıfırlar — bu yüzden başvurudan ÖNCE.
        self.barkod_kontrol.sifirla()
        akis = _tid.basvur_ile_cakisma_akisi(self.app, self.barkod_kontrol.ad, self.barkod_kontrol)
        if akis == "iptal":
            return
        if akis == "sirada":
            self.durum_lbl.config(text="Barkod güncellemesi sırada bekliyor…")
            _tid.sira_bekle_ve_baslat(
                self.app, self.barkod_kontrol.ad, self.barkod_kontrol,
                self._barkod_baslat, durum_fn=lambda t: self.durum_lbl.config(text=t))
            return
        self._barkod_baslat()

    def _barkod_baslat(self):
        self._barkod_calisiyor = True
        self.barkod_btn.set_state("disabled")

        def bg():
            try:
                import barkod_sorgu
                secim = getattr(self, "_barkod_secim", None) or icra_core.acik_icra_dosyalari_secimi()
                if not secim:
                    self._log("[Barkod] Veritabanında açık İcra dosyası yok — önce Sorgula çalıştırın.")
                    return
                self._log(f"[Barkod] {len(secim)} açık İcra dosyası için barkod güncellemesi başladı.")
                barkod_sorgu.calistir(secim, lambda m: self._log(f"[Barkod] {m}"),
                                      kontrol=self.barkod_kontrol,
                                      devam=getattr(self, "_barkod_devam", True))
            except Exception as e:
                self._log(f"[Barkod] ⚠️ Hata: {e}")
            finally:
                self._barkod_calisiyor = False
                self.barkod_kontrol.karmadan_cik()
                toplu_is_kontrol.KAYIT_DEFTERI.sil(self.barkod_kontrol.ad)
                self.app.after(0, lambda: self.barkod_btn.set_state("normal"))
        threading.Thread(target=bg, daemon=True).start()

    # ─────────────────────────── UYAP'tan Güncelle ───────────────────────────
    def _uyap_guncelle(self):
        """Tüm İcra dosyaları için künye + taraf + barkod + kesinleşme
        (`dosya_core.dosyalarim_yenile(..., barkod_da=True)`). Sorgula ile aynı
        Duraklat/Durdur düğmelerini kullanır (bkz. `_aktif_kontrol`)."""
        if self.running:
            return
        if not hasattr(self, "guncelle_kontrol"):
            self.guncelle_kontrol = toplu_is_kontrol.TopluIsKontrolu(ad="İcra Dosyalarım UYAP'tan Güncelle")
        # sifirla() karma bağını da sıfırlar — bu yüzden başvurudan ÖNCE.
        self.guncelle_kontrol.sifirla()
        akis = _tid.basvur_ile_cakisma_akisi(self.app, self.guncelle_kontrol.ad, self.guncelle_kontrol)
        if akis == "iptal":
            return
        if akis == "sirada":
            self.durum_lbl.config(text="Sırada bekliyor…")
            _tid.sira_bekle_ve_baslat(
                self.app, self.guncelle_kontrol.ad, self.guncelle_kontrol,
                self._uyap_guncelle_baslat, durum_fn=lambda t: self.durum_lbl.config(text=t))
            return
        self._uyap_guncelle_baslat()

    def _uyap_guncelle_baslat(self):
        kontrol = self._aktif_kontrol = self.guncelle_kontrol
        self._set_running(True)
        self.guncelle_btn.set_state("disabled")
        self.durum_lbl.config(text="UYAP'tan güncelleniyor (künye, taraf, barkod, kesinleşme)…")

        def bg():
            try:
                dosya_core.dosyalarim_yenile(self._log, yargi_turu=dosya_core.YARGI_TURU_ICRA,
                                             kontrol=kontrol, barkod_da=True)
                kayitlar = icra_core.db_dosyalari_getir(self._son_degerler, self._son_durum_kod)
                self.result_q.put(("guncellendi", kayitlar))
            except Exception as e:
                self.result_q.put(("guncelle_hata", str(e)))
            finally:
                kontrol.karmadan_cik()
                toplu_is_kontrol.KAYIT_DEFTERI.sil(kontrol.ad)
        threading.Thread(target=bg, daemon=True).start()

    def _secilenleri_guncelle(self):
        """Seçili dosyalar için künye (dosya durumu) + taraflar + evrak/tebligat
        (UYAP+PTT) + itiraz belgeleri + kesinleşme — `barkod_sorgu.calistir(...,
        taraf_da=True)`, Tüm Dosyalarım / Barkod Sorgulama ile AYNI çağrı."""
        if self.running:
            return
        secim = self.tree.selection()
        if not secim:
            self.durum_lbl.config(text="Önce listeden en az bir dosya seçin (Ctrl/Shift ile çoklu seçim).")
            return
        try:
            kayitlar = [self._gorunen_kayitlar[int(iid)] for iid in secim]
        except (ValueError, IndexError):
            self.durum_lbl.config(text="Seçili satırlar bulunamadı; listeyi yenileyin.")
            return
        gorulen = set()   # satırlar borçlu başına — aynı dosya bir kez işlenir
        kayitlar = [r for r in kayitlar
                    if not ((r.get("birimAdi"), r.get("dosyaNo")) in gorulen
                            or gorulen.add((r.get("birimAdi"), r.get("dosyaNo"))))]
        import barkod_sorgu
        devam = barkod_sorgu.devam_sor(self.app, kayitlar, barkod_sorgu.MOD_IKISI)
        if devam is None:
            return
        if not hasattr(self, "secili_kontrol"):
            self.secili_kontrol = toplu_is_kontrol.TopluIsKontrolu(ad="İcra Dosyalarım Seçilenleri Güncelle")
        self.secili_kontrol.sifirla()
        akis = _tid.basvur_ile_cakisma_akisi(self.app, self.secili_kontrol.ad, self.secili_kontrol)
        if akis == "iptal":
            return
        baslat = lambda: self._secilenleri_guncelle_baslat(kayitlar, devam)  # noqa: E731
        if akis == "sirada":
            self.durum_lbl.config(text="Sırada bekliyor…")
            _tid.sira_bekle_ve_baslat(self.app, self.secili_kontrol.ad, self.secili_kontrol, baslat,
                                      durum_fn=lambda t: self.durum_lbl.config(text=t))
            return
        baslat()

    def _secilenleri_guncelle_baslat(self, kayitlar, devam):
        kontrol = self._aktif_kontrol = self.secili_kontrol
        self._set_running(True)
        self.guncelle_btn.set_state("disabled")
        self.secili_guncelle_btn.set_state("disabled")
        self.durum_lbl.config(text=f"{len(kayitlar)} seçili dosya UYAP'tan güncelleniyor…")
        self._log(f"▶ {len(kayitlar)} seçili dosya UYAP'tan güncelleniyor (künye, taraf, tebligat, itiraz, kesinleşme)…")

        def bg():
            import barkod_sorgu
            try:
                barkod_sorgu.calistir(kayitlar, self._log, kontrol=kontrol, devam=devam, taraf_da=True)
                self.result_q.put(("guncellendi",
                                   icra_core.db_dosyalari_getir(self._son_degerler, self._son_durum_kod)))
            except Exception as e:
                self.result_q.put(("guncelle_hata", str(e)))
            finally:
                kontrol.karmadan_cik()
                toplu_is_kontrol.KAYIT_DEFTERI.sil(kontrol.ad)
        threading.Thread(target=bg, daemon=True).start()

    # ─────────────────────── Seçilenlerin Tüm Evrakını İndir ───────────────────────
    def _secilenlerin_evrakini_indir(self):
        """Seçili dosyaların TÜM evrakını orijinal formatında indirir + her dosya
        için tek 'Birlesik.pdf' üretir — Hukuk Dosyalarım ile AYNI çekirdek
        (`evrak_indirici.calistir`; İcra kayıtları birimId/dosyaNo/birimAdi taşır)."""
        if self.running:
            return
        secim = self.tree.selection()
        if not secim:
            self.durum_lbl.config(text="Önce listeden en az bir dosya seçin (Ctrl/Shift ile çoklu seçim).")
            return
        try:
            kayitlar = [self._gorunen_kayitlar[int(iid)] for iid in secim]
        except (ValueError, IndexError):
            self.durum_lbl.config(text="Seçili satırlar bulunamadı; listeyi yenileyin.")
            return
        gorulen = set()   # satırlar borçlu başına — aynı dosya bir kez indirilir
        kayitlar = [r for r in kayitlar
                    if not ((r.get("birimAdi"), r.get("dosyaNo")) in gorulen
                            or gorulen.add((r.get("birimAdi"), r.get("dosyaNo"))))]
        hedef = filedialog.askdirectory(title="Evrakın indirileceği klasörü seçin")
        if not hedef:
            return
        if not hasattr(self, "evrak_kontrol"):
            self.evrak_kontrol = toplu_is_kontrol.TopluIsKontrolu(ad="İcra Dosyalarım Evrak İndir")
        self.evrak_kontrol.sifirla()
        akis = _tid.basvur_ile_cakisma_akisi(self.app, self.evrak_kontrol.ad, self.evrak_kontrol)
        if akis == "iptal":
            return
        baslat = lambda: self._evrak_indir_baslat(kayitlar, hedef)  # noqa: E731
        if akis == "sirada":
            self.durum_lbl.config(text="Sırada bekliyor…")
            _tid.sira_bekle_ve_baslat(self.app, self.evrak_kontrol.ad, self.evrak_kontrol, baslat,
                                      durum_fn=lambda t: self.durum_lbl.config(text=t))
            return
        baslat()

    def _evrak_indir_baslat(self, kayitlar, hedef):
        kontrol = self._aktif_kontrol = self.evrak_kontrol
        self._set_running(True)
        self.evrak_indir_btn.set_state("disabled")
        self.guncelle_btn.set_state("disabled")
        self.secili_guncelle_btn.set_state("disabled")
        self.durum_lbl.config(text=f"{len(kayitlar)} dosyanın evrakı indiriliyor…")
        self._log(f"▶ {len(kayitlar)} seçili dosya için 'Tüm Evrakı İndir' başlıyor… (klasör: {hedef})")

        def bg():
            try:
                sonuc = evrak_indirici.calistir(kayitlar, hedef, self._log, kontrol=kontrol)
                self.result_q.put(("evrak_indirildi", (sonuc, len(kayitlar))))
            except Exception as e:
                self.result_q.put(("evrak_indir_hata", str(e)))
            finally:
                kontrol.karmadan_cik()
                toplu_is_kontrol.KAYIT_DEFTERI.sil(kontrol.ad)
        threading.Thread(target=bg, daemon=True).start()

    def _evrak_indir_bitti(self, tip, veri):
        self._set_running(False)
        self._aktif_kontrol = None
        self.evrak_indir_btn.set_state("normal")
        self.guncelle_btn.set_state("normal")
        self.secili_guncelle_btn.set_state("normal")
        if tip == "evrak_indirildi":
            sonuc, toplam = veri
            basarili = sum(1 for s in sonuc if str(s.get("Durum", "")).startswith("✅"))
            self.durum_lbl.config(text=f"✔ {basarili}/{toplam} dosyanın evrakı indirildi")
        else:
            self.durum_lbl.config(text="Evrak indirme hatası")
            self._log(f"⚠️ Evrak indirme: {veri}")

    def _uyap_guncelle_bitti(self, tip, veri):
        self._set_running(False)
        self._aktif_kontrol = None
        self.guncelle_btn.set_state("normal")
        self.secili_guncelle_btn.set_state("normal")
        if tip == "guncellendi":
            self._db_yenile_goster(veri)
            self.durum_lbl.config(text=f"✔ UYAP'tan güncellendi · {len(veri)} dosya")
        else:
            self.durum_lbl.config(text="Güncelleme hatası")
            self._log(f"⚠️ UYAP'tan güncelleme: {veri}")

    def duraklat_toggle(self):
        k = getattr(self, "_aktif_kontrol", None)
        if not self.running or k is None:
            return
        paused = k.toggle_pause()
        self.duraklat_btn.set_text("Devam" if paused else "Duraklat")

    def durdur(self):
        k = getattr(self, "_aktif_kontrol", None)
        if self.running and k is not None:
            k.durdur()

    def _set_running(self, running):
        self.running = running
        st = "disabled" if running else "normal"
        self.sorgula_btn.set_state(st)
        self.duraklat_btn.set_state("normal" if running else "disabled")
        self.duraklat_btn.set_text("Duraklat")
        self.durdur_btn.set_state("normal" if running else "disabled")
        if not running:
            toplu_is_kontrol.KAYIT_DEFTERI.sil(self.kontrol.ad)

    def _ara_bg(self, values, durum_kod):
        # 1) ÖNCE DB'den göster (UYAP beklemeden) — ofis kapalı olsa bile veri görünür.
        try:
            db_kayitlar = icra_core.db_dosyalari_getir(values, durum_kod)
        except Exception:
            db_kayitlar = []
        if db_kayitlar:
            self.result_q.put(("db", db_kayitlar))
        # 2) SONRA canlı UYAP: yeni/eksik dosyaları ekle (birim+yıl+sıra ile birleştir).
        try:
            kayitlar, _payload, yeni_dosyalar = self.engine.ara(values, durum_kod, kontrol=self.kontrol)
            # ara() artık DB'yi de günceller (künye, kapanan dosyalar,
            # alacaklı, kesinleşme) — birleştirme TAZE DB ile yapılır ki
            # kapandığı yeni anlaşılan dosyalar ekranda kalmasın.
            try:
                db_kayitlar = icra_core.db_dosyalari_getir(values, durum_kod) or db_kayitlar
            except Exception:
                pass
            birlesik = self._birlestir(db_kayitlar, kayitlar)
            self.result_q.put(("ok", (birlesik, yeni_dosyalar)))
        except icra_core.OturumHatasi as e:
            self.result_q.put(("oturum", str(e)))
        except Exception as e:
            # Canlı sorgu başarısız (ör. ofis 8800 kapalı): DB sonuçları ekranda kalsın.
            if db_kayitlar:
                self.result_q.put(("db_son", str(e)))
            else:
                self.result_q.put(("hata", str(e)))

    def _birlestir(self, db_kayitlar, canli_kayitlar):
        """DB + canlı kayıtları birim+dosyaNo (≈ birim+yıl+sıra) ile ALAN ALAN
        birleştirir (TÜM kaydı DEĞİŞTİRMEZ).

        KRİTİK DÜZELTME (kullanıcı bulgusu, 2026-08-14: "Tüm Dosyalarım'da
        güncellenmiş veri varken İcra Dosyalarım'da Sorgula dedim, var olan
        veri gitti"): canlı UYAP'ın search_phrase_detayli.ajx yanıtı yalnız
        KAPAK KÜNYESİ içerir — alacaklı/borçlu HİÇ GELMEZ (bkz. models/
        icra_models/ingest.py modül başlığı). Eski sürüm canlı kaydı OLDUĞU
        GİBİ DB kaydının ÜSTÜNE yazıyordu; bu yüzden DB'de doğru olan
        alacaklı/borçlu EKRANDA boşa dönüyordu (veritabanının KENDİSİ
        ETKİLENMEDİ — yalnız bu ekrandaki birleşik görünüm bozuluyordu).
        Artık canlı bir alan yalnız DOLUYSA DB'deki karşılığının üstüne
        yazılır; canlı boşsa DB'deki değer KORUNUR."""
        def anahtar(r):
            return (str(r.get("birimAdi", "")), str(r.get("dosyaNo", "")))
        harita = {anahtar(r): dict(r) for r in (db_kayitlar or [])}
        for r in (canli_kayitlar or []):
            k = anahtar(r)
            if k in harita:
                birlesik = harita[k]
                for alan, deger in r.items():
                    if deger not in (None, "", []):
                        birlesik[alan] = deger
            else:
                harita[k] = r
        return list(harita.values())

    def _db_goster(self, kayitlar):
        """Canlı sorgu SÜRERKEN DB sonuçlarını anında göster (running korunur)."""
        self.all_records = kayitlar
        self.sort_key = None
        self._tabloyu_kur()
        self.durum_lbl.config(text=f"DB'den {len(kayitlar)} · canlı sorgu sürüyor…")

    def _db_son(self, msg):
        """Canlı sorgu yapılamadı (ofis kapalı) ama DB sonuçları ekranda kalsın."""
        self._set_running(False)
        self.durum_lbl.config(text=f"DB'den {len(self.all_records)} (ofis kapalı/erişilemedi)")
        self._log("ℹ️ Canlı UYAP yapılamadı; yalnız veritabanı gösteriliyor: " + str(msg))

    def _sorgu_bitti(self, tip, veri):
        self._set_running(False)
        if tip == "ok":
            kayitlar, yeni_dosyalar = veri
            self.all_records = kayitlar
            self.sort_key = None
            self.durum_lbl.config(text=f"{len(kayitlar)} sonuç")
            self._tabloyu_kur()
            if yeni_dosyalar:
                count = len(yeni_dosyalar)
                list_str = "\n".join(f"- {item['dosya_no']} ({item['birim_adi']})" for item in yeni_dosyalar)
                self._log(f"ℹ️ Veritabanına {count} yeni dosya eklendi:\n{list_str}")
        elif tip == "oturum":
            self.durum_lbl.config(text="Oturum/bağlantı hatası")
            self._log("⛔ Oturum/yetki hatası: " + veri)
            self._log("   UYAP Bağlantısı modülünden Paylaş/Al ile bağlantıyı başlatın.")
        else:
            self.durum_lbl.config(text="Hata")
            self._log("⚠️ Sorgu hatası: " + veri)
            self._log("   Yerel ofis (127.0.0.1:8800) açık ve UYAP Bağlantısı aktif mi?")

    # ─────────────────────────── Dosya Görüntüle (dosya kartı) ───────────────
    # 2026-09-30 (kullanıcı bulgusu): burada UYAP'a canlı gidip düz mesaj
    # kutusu açılıyordu; artık Tüm Dosyalarım'daki AYNI kart (dosya_karti.py):
    # önce DB'den, "Canlı Veriyi Getir (UYAP)" düğmesiyle canlı.
    def _dosya_goruntule(self):
        secim = self.tree.selection()
        if not secim:
            self.durum_lbl.config(text="Önce listeden bir dosya seçin.")
            return
        try:
            rec = self._gorunen_kayitlar[int(secim[0])]
        except (ValueError, IndexError):
            self.durum_lbl.config(text="Seçili satır bulunamadı; listeyi yenileyin.")
            return
        self.detay_btn.set_state("disabled")
        self.durum_lbl.config(text="Dosya kartı açılıyor…")

        def bitti():
            self.detay_btn.set_state("normal")
            self.durum_lbl.config(text="Dosya kartı açıldı (veritabanından)")

        def liste_yenile():
            # Canlı veri kaydedildiyse tablo DB'den tazelenir (UYAP'a gitmez).
            threading.Thread(target=self._db_yenile_bg, daemon=True).start()
        dosya_karti.ac(self.app, rec, log_fn=self._log, liste_yenile_fn=liste_yenile, bitti_fn=bitti)

    def ekran_gosterildi(self):
        """Menüden bu ekrana dönülünce (bkz. uyapmasa.select) listeyi son
        kriterlerle DB'den bir kez okur — diğer modüllerin yazdığı veri görünsün.
        Sorgu/güncelleme sürerken dokunmaz."""
        if self.running:
            return

        def bg():
            try:
                kayitlar = icra_core.db_dosyalari_getir(self._son_degerler, self._son_durum_kod)
            except Exception as e:
                self._log(f"⚠️ Liste tazelenemedi: {e}")
                return
            self.result_q.put(("db_acilis", kayitlar))
        threading.Thread(target=bg, daemon=True).start()

    def _db_yenile_bg(self):
        try:
            kayitlar = icra_core.db_dosyalari_getir(self._son_degerler, self._son_durum_kod)
        except Exception as e:
            self._log(f"⚠️ Liste tazelenemedi: {e}")
            return
        self.result_q.put(("db_yenile", kayitlar))

    def _db_yenile_goster(self, kayitlar):
        self.all_records = kayitlar
        self.sort_key = None
        self._tabloyu_kur()
        self.durum_lbl.config(text=f"Kaydedildi — liste güncellendi ({len(kayitlar)} dosya)")

    def temizle(self):
        for v in self.vars.values():
            v.set("")
        for v in self.taraf_vars.values():
            v.set("")
        for v in self._bol_vars.values():
            v.set("")
        self.durum_lbl.config(text="")
        self._tabloyu_doldur()

    # ─────────────────────────── günlük / polling ───────────────────────────
    def _log(self, mesaj):
        with self._loglock:
            self._logbuf.append(str(mesaj))

    def _append_log(self, text):
        if not self.log.winfo_exists():
            return
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _poll(self):
        with self._loglock:
            satirlar, self._logbuf = self._logbuf, []
        for ln in satirlar:
            self._append_log(ln)
        try:
            while True:
                tip, veri = self.result_q.get_nowait()
                if tip == "birim":
                    self._combolari_doldur()
                elif tip == "db":
                    self._db_goster(veri)
                elif tip == "db_acilis":
                    if not self.running:      # kullanıcı sorgu başlattıysa karışma
                        self.all_records = veri
                        self.sort_key = None
                        self._tabloyu_kur()
                        self.durum_lbl.config(
                            text=f"DB'den {len(veri)} dosya · Sorgula ile UYAP'tan güncelle")
                elif tip == "db_son":
                    self._db_son(veri)
                elif tip == "db_yenile":
                    self._db_yenile_goster(veri)
                elif tip in ("guncellendi", "guncelle_hata"):
                    self._uyap_guncelle_bitti(tip, veri)
                elif tip in ("evrak_indirildi", "evrak_indir_hata"):
                    self._evrak_indir_bitti(tip, veri)
                else:
                    self._sorgu_bitti(tip, veri)
        except queue.Empty:
            pass
        self.app.after(200, self._poll)
