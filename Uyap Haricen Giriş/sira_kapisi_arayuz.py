"""Sıra kapısı arayüzü (uyap_app.py'den kullanılır) — müşteri komut satırı görmez.

  • SiraKapisiSekmesi: ofis bilgisayarı, gösterge panelindeki "Sıra Kapısı" sekmesi.
  • BotKurulumPenceresi: bot bilgisayarı, giriş ekranındaki "Bot bilgisayarı kurulumu".
Ağ ve UAC işleri arka plan iş parçacığında koşar; sonuçlar kuyruk + after() ile arayüze döner
(tkinter iş parçacığı güvenli değildir)."""

import os
import queue
import base64
import threading
import tkinter as tk
from tkinter import messagebox

from uyap_core import sira_kapisi as sk
from uyap_core import sira_eslestirme as se
from uyap_theme import C, RoundButton


def _arka_plan(widget, is_, bitince):
    """is_() arka planda koşar; bitince(sonuc, hata) arayüz iş parçacığında çağrılır."""
    q = queue.Queue()

    def calis():
        try:
            q.put((is_(), None))
        except Exception as e:  # noqa: BLE001 — arayüze iletilir
            q.put((None, e))

    def yokla():
        try:
            sonuc, hata = q.get_nowait()
        except queue.Empty:
            try:
                widget.after(150, yokla)
            except tk.TclError:
                pass
            return
        try:
            bitince(sonuc, hata)
        except tk.TclError:
            pass

    threading.Thread(target=calis, daemon=True).start()
    widget.after(150, yokla)


def _yonetici_hata_metni(hata) -> str:
    if isinstance(hata, se.YetkiReddedildi):
        return "Windows yönetici onayı verilmedi; işlem yapılmadı."
    return f"İşlem yapılamadı: {hata}"


def _pencere(root, baslik, w=460, h=420):
    p = tk.Toplevel(root)
    p.title(baslik)
    p.configure(bg=C.CARD)
    p.resizable(False, False)
    p.geometry(f"{w}x{h}+{root.winfo_rootx() + 60}+{root.winfo_rooty() + 40}")
    p.transient(root)
    return p


def _etiket(parent, metin, font, fg=C.INK, **kw):
    lbl = tk.Label(parent, text=metin, bg=parent.cget("bg"), fg=fg, font=font,
                   justify="left", anchor="w", wraplength=kw.pop("wrap", 400), **kw)
    return lbl


def _kod_etiketi(parent, fonts):
    return tk.Label(parent, text="", bg=C.SAGE_TINT, fg=C.SAGE_DK,
                    font=(fonts["h1"].actual("family"), 28, "bold"), padx=18, pady=8)


# ═══════════════════════════════ OFİS TARAFI ═════════════════════════════════════════════

class SiraKapisiSekmesi:
    def __init__(self, parent, fonts, root):
        self.root, self.fonts = root, fonts
        sar = tk.Frame(parent, bg=C.BG)
        sar.pack(fill="both", expand=True, padx=14, pady=14)
        kart = tk.Frame(sar, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        kart.pack(fill="both", expand=True)
        ic = tk.Frame(kart, bg=C.CARD)
        ic.pack(fill="both", expand=True, padx=20, pady=16)

        tk.Label(ic, text="Sıra Kapısı", bg=C.CARD, fg=C.INK, font=fonts["card_t"]).pack(anchor="w")
        _etiket(ic, "Ofisteki başka bir bilgisayarda çalışan UYAP otomasyonunun (bot) isteklerini "
                    "sıraya alır. Sizin işlemleriniz önce gelir, iki istek UYAP'a aynı anda gitmez. "
                    "Kapı yalnız paylaşım açıkken çalışır.",
                fonts["small"], fg=C.INK_SOFT, wrap=620).pack(anchor="w", pady=(4, 10))

        self.durum_lbl = _etiket(ic, "", fonts["body"])
        self.durum_lbl.pack(anchor="w", pady=(0, 8))

        tk.Label(ic, text="Bağlı bot bilgisayarları", bg=C.CARD, fg=C.INK_SOFT,
                 font=fonts["small"]).pack(anchor="w")
        self.liste = tk.Listbox(ic, height=6, bg="#FFFFFF", fg=C.INK, font=fonts["body"],
                                relief="flat", highlightthickness=1, highlightbackground=C.LINE,
                                selectbackground=C.SAGE_TINT, selectforeground=C.INK,
                                activestyle="none")
        self.liste.pack(fill="x", pady=(4, 10))

        dugmeler = tk.Frame(ic, bg=C.CARD)
        dugmeler.pack(fill="x")
        RoundButton(dugmeler, "Bot Bilgisayarı Ekle", command=self.on_ekle, kind="primary",
                    font=fonts["nav_b"], height=36).pack(side="left")
        RoundButton(dugmeler, "Seçili Botu Kaldır", command=self.on_sil, kind="ghost",
                    font=fonts["nav_b"], height=36).pack(side="left", padx=8)
        self.ac_kapat_btn = RoundButton(dugmeler, "Kapıyı Kapat", command=self.on_ac_kapat,
                                        kind="ghost", font=fonts["nav_b"], height=36)
        self.ac_kapat_btn.pack(side="left")
        RoundButton(dugmeler, "Günlüğü Aç", command=self.on_gunluk, kind="ghost",
                    font=fonts["nav_b"], height=36).pack(side="right")

        self.alt_lbl = _etiket(ic, "", fonts["small"], fg=C.INK_SOFT, wrap=620)
        self.alt_lbl.pack(anchor="w", pady=(12, 0))
        self.yenile()

    def yenile(self):
        ayar = sk.ayar_oku()
        self._botlar = sk.botlar(ayar)
        self.liste.delete(0, "end")
        for b in self._botlar:
            ad = f" — {b['ad']}" if b.get("ad") else ""
            tarih = f"  ({b['tarih']})" if b.get("tarih") else ""
            self.liste.insert("end", f"{b['ip']}{ad}{tarih}")
        if not self._botlar:
            self.liste.insert("end", "Henüz bot bilgisayarı eklenmedi.")
        etkin = bool(ayar.get("etkin"))
        if not self._botlar:
            metin, renk = "● Kapı kurulmadı", C.INK_FAINT
        elif not etkin:
            metin, renk = "● Kapı kapalı (botlar UYAP'a ulaşamaz)", C.CLAY
        elif sk.calisiyor_mu():
            metin, renk = f"● Kapı açık — {ayar.get('kapi_ip')}", C.SAGE_DK
        else:
            metin, renk = "● Kapı hazır — paylaşım başlayınca açılır", C.GOLD
        self.durum_lbl.config(text=metin, fg=renk)
        self.ac_kapat_btn.set_text("Kapıyı Aç" if not etkin and self._botlar else "Kapıyı Kapat")
        bitis = sk.sertifika_bitis()
        self.alt_lbl.config(text=(
            (f"Sertifika geçerlilik sonu: {bitis:%d.%m.%Y}. Bu tarihten önce botları yeniden ekleyin.  "
             if bitis and self._botlar else "")
            + "Bot kaldırıldığında bot bilgisayarında da 'Bot bilgisayarı kurulumu > Kurulumu Kaldır' yapın."))

    def on_gunluk(self):
        yol = sk._yol("kapi.log")
        if os.path.exists(yol):
            os.startfile(yol)
        else:
            messagebox.showinfo("Sıra Kapısı", "Henüz günlük yok.", parent=self.root)

    def on_ac_kapat(self):
        ayar = sk.ayar_oku()
        sk.etkin_ayarla(not ayar.get("etkin"))
        self.yenile()
        self.root.after(1500, self.yenile)

    def on_sil(self):
        sec = self.liste.curselection()
        if not sec or sec[0] >= len(self._botlar):
            return
        b = self._botlar[sec[0]]
        if messagebox.askyesno("Sıra Kapısı", f"{b['ip']} bot listesinden kaldırılsın mı?\n\n"
                               "Bot bilgisayarında da kurulumu kaldırmazsanız bot UYAP'a ulaşamaz.",
                               parent=self.root):
            sk.bot_sil(b["ip"])
            self.yenile()

    def on_ekle(self):
        if os.name == "nt" and not se.guvenlik_duvari_var():
            if not messagebox.askokcancel(
                    "Sıra Kapısı",
                    "Bot bilgisayarının bu bilgisayara bağlanabilmesi için Windows güvenlik duvarına "
                    "bir izin eklenecek (yalnız Çoklu UYAP ve yerel ağ için).\n\n"
                    "Tamam'a bastıktan sonra Windows bir onay penceresi gösterecek; 'Evet' deyin.",
                    parent=self.root):
                return

            def bitti(kod, hata):
                if hata is not None:
                    messagebox.showerror("Sıra Kapısı", _yonetici_hata_metni(hata), parent=self.root)
                elif kod != 0:
                    messagebox.showerror("Sıra Kapısı", se.CIKIS_METNI.get(kod, se.CIKIS_METNI[1]),
                                         parent=self.root)
                else:
                    EslestirmePenceresi(self.root, self.fonts, self.yenile)
            _arka_plan(self.root, lambda: se.yonetici_olarak_calistir(["guvenlik-duvari"]), bitti)
            return
        EslestirmePenceresi(self.root, self.fonts, self.yenile)


class EslestirmePenceresi:
    def __init__(self, root, fonts, bitince):
        self.fonts, self.bitince = fonts, bitince
        self.p = p = _pencere(root, "Bot Bilgisayarı Ekle", 470, 400)
        p.protocol("WM_DELETE_WINDOW", self.kapat)
        ic = tk.Frame(p, bg=C.CARD)
        ic.pack(fill="both", expand=True, padx=22, pady=18)
        tk.Label(ic, text="Bot Bilgisayarı Ekle", bg=C.CARD, fg=C.INK, font=fonts["card_t"]).pack(anchor="w")
        self.bilgi = _etiket(ic, "Hazırlanıyor…", fonts["body"])
        self.bilgi.pack(anchor="w", pady=(10, 10))
        self.kod = _kod_etiketi(ic, fonts)
        self.sure = _etiket(ic, "", fonts["small"], fg=C.INK_SOFT)
        self.sure.pack(anchor="w", side="bottom")
        self.dugmeler = tk.Frame(ic, bg=C.CARD)
        self.dugmeler.pack(side="bottom", fill="x", pady=(0, 8))
        self.sunucu = se.EslestirmeSunucusu()
        try:
            self.sunucu.baslat()
        except OSError as e:
            self.bilgi.config(text=f"Eşleştirme başlatılamadı: {e}\n(Program ikinci kez mi açık?)", fg=C.CLAY)
            self._kapat_dugmesi()
            return
        self.bilgi.config(text=(
            "Bot bilgisayarında Çoklu UYAP'ı açın ve giriş ekranındaki\n"
            "“Bot bilgisayarı kurulumu” bağlantısına tıklayın.\n\n"
            "Bot bu bilgisayarı kendiliğinden bulacaktır. Bekleniyor…"))
        self._kapat_dugmesi("Vazgeç")
        self._yokla()

    def _kapat_dugmesi(self, metin="Kapat"):
        for w in self.dugmeler.winfo_children():
            w.destroy()
        RoundButton(self.dugmeler, metin, command=self.kapat, kind="ghost",
                    font=self.fonts["nav_b"], height=34).pack(side="right")

    def _yokla(self):
        try:
            while True:
                tur, veri = self.sunucu.olaylar.get_nowait()
                getattr(self, f"_olay_{tur}")(veri)
        except queue.Empty:
            pass
        except tk.TclError:
            return
        if self.sunucu is not None and not self.sunucu._dur.is_set():
            k = self.sunucu.kalan_sn()
            self.sure.config(text=f"Eşleştirme penceresi {k // 60}:{k % 60:02d} sonra kapanır.")
        try:
            self.p.after(200, self._yokla)
        except tk.TclError:
            pass

    def _olay_uyari(self, veri):
        self.sure.config(text=f"Uyarı: {veri['mesaj']}")

    def _olay_istek(self, veri):
        ad = f"{veri['ad']} " if veri.get("ad") else ""
        self.bilgi.config(text=(f"Bot bilgisayarı bağlanmak istiyor: {ad}({veri['ip']})\n\n"
                                "Bot ekranındaki kod aşağıdakiyle AYNIYSA Onayla'ya basın:"), fg=C.INK)
        self.kod.config(text=veri["kod"])
        self.kod.pack(anchor="center", pady=(4, 8))
        for w in self.dugmeler.winfo_children():
            w.destroy()
        RoundButton(self.dugmeler, "Onayla", command=lambda: self._karar(True), kind="primary",
                    font=self.fonts["nav_b"], height=34).pack(side="right")
        RoundButton(self.dugmeler, "Kodlar farklı — Reddet", command=lambda: self._karar(False),
                    kind="ghost", font=self.fonts["nav_b"], height=34).pack(side="right", padx=8)

    def _karar(self, onay):
        self.sunucu.karar_ver(onay)
        for w in self.dugmeler.winfo_children():
            w.destroy()
        self.bilgi.config(text=("Bot bilgisayarındaki onay bekleniyor…" if onay else
                                "Reddedildi. Başka bir bot bekleniyor…"))
        if not onay:
            self.kod.pack_forget()
        self._kapat_dugmesi("Vazgeç")

    def _olay_bitti(self, veri):
        self.kod.pack_forget()
        self.bilgi.config(text=veri["mesaj"] + ("\n\nPaylaşım açıksa kapı hemen çalışmaya başlar."
                                                if veri["basarili"] else ""),
                          fg=C.SAGE_DK if veri["basarili"] else C.CLAY)
        self.sure.config(text="")
        self._kapat_dugmesi()
        self.bitince()

    def kapat(self):
        if self.sunucu is not None:
            self.sunucu.durdur()
        self.bitince()
        try:
            self.p.destroy()
        except tk.TclError:
            pass


# ═══════════════════════════════ BOT TARAFI ══════════════════════════════════════════════

class BotKurulumPenceresi:
    def __init__(self, root, fonts):
        self.fonts = fonts
        self.istemci = None
        self.p = p = _pencere(root, "Bot bilgisayarı kurulumu", 480, 470)
        p.protocol("WM_DELETE_WINDOW", self.kapat)
        ic = self.ic = tk.Frame(p, bg=C.CARD)
        ic.pack(fill="both", expand=True, padx=22, pady=18)
        tk.Label(ic, text="Bot bilgisayarı kurulumu", bg=C.CARD, fg=C.INK,
                 font=fonts["card_t"]).pack(anchor="w")
        _etiket(ic, "Bu bilgisayarda UYAP'a istek gönderen bir otomasyon programı çalışıyorsa, "
                    "isteklerini ofisteki Çoklu UYAP bilgisayarı üzerinden sıraya alır.",
                fonts["small"], fg=C.INK_SOFT, wrap=430).pack(anchor="w", pady=(4, 8))
        self.durum = _etiket(ic, "", fonts["body"], wrap=430)
        self.durum.pack(anchor="w", pady=(0, 8))
        self.govde = tk.Frame(ic, bg=C.CARD)
        self.govde.pack(fill="both", expand=True)
        self.alt = tk.Frame(ic, bg=C.CARD)
        self.alt.pack(side="bottom", fill="x")
        self._durum_goster()
        self._baslangic()

    # ── ekranlar ──
    def _temizle(self):
        for w in list(self.govde.winfo_children()) + list(self.alt.winfo_children()):
            w.destroy()

    def _dugme(self, parent, metin, komut, tur="primary", yan="right"):
        b = RoundButton(parent, metin, command=komut, kind=tur, font=self.fonts["nav_b"], height=34)
        b.pack(side=yan, padx=(8, 0) if yan == "right" else (0, 8))
        return b

    def _durum_goster(self):
        ip = se.hosts_kapi_ip()
        if ip and se.kok_kurulu_mu():
            self.durum.config(text=f"● Bu bilgisayar sıra kapısına bağlı (ofis: {ip}).", fg=C.SAGE_DK)
        elif ip:
            self.durum.config(text=f"● Kurulum eksik (ofis: {ip}). Yeniden kurun ya da kaldırın.", fg=C.CLAY)
        else:
            self.durum.config(text="● Bu bilgisayar sıra kapısına bağlı değil.", fg=C.INK_SOFT)
        self._bagli = bool(ip)

    def _baslangic(self):
        self._temizle()
        _etiket(self.govde, "1. Ofis bilgisayarında Çoklu UYAP > Sıra Kapısı > “Bot Bilgisayarı Ekle”ye basın.\n"
                            "2. Sonra burada “Ofis Bilgisayarını Bul”a basın.",
                self.fonts["body"], wrap=430).pack(anchor="w")
        self._dugme(self.alt, "Ofis Bilgisayarını Bul", self._bul)
        if self._bagli:
            self._dugme(self.alt, "Kurulumu Kaldır", self._kaldir, tur="ghost", yan="left")
            self._dugme(self.alt, "Bağlantıyı Sına", self._sina, tur="ghost", yan="left")

    def _bul(self):
        self._temizle()
        _etiket(self.govde, "Ofis bilgisayarı aranıyor…", self.fonts["body"]).pack(anchor="w")
        _arka_plan(self.p, se.ofisleri_bul, self._bulundu)

    def _bulundu(self, sonuc, hata):
        self._temizle()
        sonuc = sonuc or []
        if sonuc:
            _etiket(self.govde, "Bulunan ofis bilgisayarı:", self.fonts["body"]).pack(anchor="w")
            self.secim = tk.Listbox(self.govde, height=4, bg="#FFFFFF", fg=C.INK, font=self.fonts["body"],
                                    relief="flat", highlightthickness=1, highlightbackground=C.LINE,
                                    selectbackground=C.SAGE_TINT, selectforeground=C.INK, activestyle="none")
            for ip, ad in sonuc:
                self.secim.insert("end", f"{ad} ({ip})" if ad else ip)
            self.secim.selection_set(0)
            self.secim.pack(fill="x", pady=(4, 8))
            self._bulunan = [ip for ip, _ in sonuc]
        else:
            self._bulunan = []
            _etiket(self.govde, "Ofis bilgisayarı bulunamadı. Orada “Bot Bilgisayarı Ekle” penceresinin "
                                "açık olduğundan emin olup tekrar deneyin.\n\nAğınız aramaya izin "
                                "vermiyorsa ofis bilgisayarının IP adresini yazabilirsiniz:",
                    self.fonts["small"], fg=C.INK_SOFT, wrap=430).pack(anchor="w")
        self.elle_var = tk.StringVar()
        if not sonuc:
            tk.Entry(self.govde, textvariable=self.elle_var, bg="#FFFFFF", fg=C.INK, relief="flat",
                     font=self.fonts["body"], highlightthickness=1, highlightbackground=C.LINE,
                     highlightcolor=C.SAGE).pack(fill="x", ipady=5, pady=(6, 0))
        self._dugme(self.alt, "Bağlan", self._baglan)
        self._dugme(self.alt, "Tekrar Ara", self._bul, tur="ghost")
        self._dugme(self.alt, "Geri", self._baslangic, tur="ghost", yan="left")

    def _baglan(self):
        ip = self.elle_var.get().strip()
        if not ip and self._bulunan:
            sec = self.secim.curselection()
            ip = self._bulunan[sec[0] if sec else 0]
        if not ip:
            return
        try:
            self.istemci = se.EslestirmeIstemcisi(ip)
        except se.EslestirmeHatasi as e:
            messagebox.showerror("Bot kurulumu", str(e), parent=self.p)
            return
        self._temizle()
        _etiket(self.govde, f"{ip} adresine bağlanılıyor…", self.fonts["body"]).pack(anchor="w")
        _arka_plan(self.p, self.istemci.baglan, self._kod_geldi)

    def _kod_geldi(self, kod, hata):
        self._temizle()
        if hata is not None:
            self._hata(str(hata))
            return
        _etiket(self.govde, "Ofis bilgisayarının ekranında da bir kod göründü.\n"
                            "Aşağıdaki kod onunla AYNI mı?", self.fonts["body"]).pack(anchor="w")
        lbl = _kod_etiketi(self.govde, self.fonts)
        lbl.config(text=kod)
        lbl.pack(anchor="center", pady=(12, 8))
        _etiket(self.govde, "Evet dedikten sonra Windows bir onay penceresi gösterecek; 'Evet' deyin.",
                self.fonts["small"], fg=C.INK_SOFT, wrap=430).pack(anchor="w")
        self._dugme(self.alt, "Evet, aynı", lambda: self._onay(True))
        self._dugme(self.alt, "Hayır, farklı", lambda: self._onay(False), tur="ghost")

    def _onay(self, evet):
        self._temizle()
        if not evet:
            try:
                self.istemci.karar_bildir_ve_bekle(False)   # yalnız gönderir, beklemez
            except (OSError, se.EslestirmeHatasi):
                pass
            self.istemci_kapat()
            self._hata("Kodlar farklıysa ağda başka biri araya girmeye çalışıyor olabilir. "
                       "Kurulum yapılmadı; ofis yöneticinize haber verin.")
            return
        _etiket(self.govde, "Ofis bilgisayarındaki onay bekleniyor…", self.fonts["body"]).pack(anchor="w")
        istemci = self.istemci

        def is_():
            if not istemci.karar_bildir_ve_bekle(True):
                raise se.EslestirmeHatasi("Ofis bilgisayarında onaylanmadı.")
            kod = se.yonetici_olarak_calistir(
                ["bot-kur", istemci.kapi_ip, base64.b64encode(istemci.cer).decode("ascii")])
            if kod != 0:
                raise se.EslestirmeHatasi(se.CIKIS_METNI.get(kod, se.CIKIS_METNI[1]))
            return se.kapi_sina(istemci.kapi_ip, istemci.cer)

        _arka_plan(self.p, is_, self._kuruldu)

    def _kuruldu(self, sina, hata):
        self.istemci_kapat()
        self._temizle()
        self._durum_goster()
        if hata is not None:
            self._hata(_yonetici_hata_metni(hata) if isinstance(hata, se.YetkiReddedildi) else str(hata))
            return
        ek = {"tamam": "Kapıya bağlantı denendi: çalışıyor.",
              "kapali": "Ofis bilgisayarında paylaşım şu an kapalı; açıldığında bot oradan geçecek.",
              "sertifika": "Uyarı: kapıyla güvenli bağlantı kurulamadı. Ofis bilgisayarını kontrol edin."}
        _etiket(self.govde, "Kurulum tamamlandı. Bot programı artık UYAP'a ofis bilgisayarı "
                            f"üzerinden gidecek.\n\n{ek.get(sina, '')}", self.fonts["body"],
                fg=C.SAGE_DK, wrap=430).pack(anchor="w")
        _etiket(self.govde, "Not: Ofis bilgisayarı kapalıyken ya da paylaşım durdurulduğunda bot UYAP'a "
                            "ulaşamaz. Bu bilgisayardan tarayıcıyla UYAP'a girenler de ofis bilgisayarı "
                            "üzerinden gider.", self.fonts["small"], fg=C.INK_SOFT, wrap=430
                ).pack(anchor="w", pady=(10, 0))
        self._dugme(self.alt, "Kapat", self.kapat)

    def _hata(self, metin):
        self._temizle()
        _etiket(self.govde, metin, self.fonts["body"], fg=C.CLAY, wrap=430).pack(anchor="w")
        self._dugme(self.alt, "Baştan Başla", self._baslangic)
        self._dugme(self.alt, "Kapat", self.kapat, tur="ghost")

    def _kaldir(self):
        if not messagebox.askyesno("Bot kurulumu", "Sıra kapısı bu bilgisayardan kaldırılsın mı?\n"
                                   "Bot yeniden doğrudan UYAP'a bağlanır.\n\n"
                                   "Windows bir onay penceresi gösterecek; 'Evet' deyin.", parent=self.p):
            return

        def bitti(kod, hata):
            self._durum_goster()
            if hata is not None:
                self._hata(_yonetici_hata_metni(hata))
            elif kod != 0:
                self._hata(se.CIKIS_METNI.get(kod, se.CIKIS_METNI[1]))
            else:
                messagebox.showinfo("Bot kurulumu", "Kaldırıldı. Ofis bilgisayarında da bu botu "
                                    "Sıra Kapısı listesinden kaldırın.", parent=self.p)
                self._baslangic()
        _arka_plan(self.p, lambda: se.yonetici_olarak_calistir(["bot-kaldir"]), bitti)

    def _sina(self):
        ip = se.hosts_kapi_ip()
        if not ip:
            return

        def bitti(sonuc, hata):
            metin = {"tamam": "Kapı çalışıyor.",
                     "kapali": "Kapıya ulaşılamıyor: ofis bilgisayarı kapalı ya da paylaşım durdurulmuş.",
                     "sertifika": "Kapıyla güvenli bağlantı kurulamadı; kurulumu yenileyin."}.get(sonuc, str(hata))
            messagebox.showinfo("Bot kurulumu", metin, parent=self.p)
        _arka_plan(self.p, lambda: se.kapi_sina(ip), bitti)

    def istemci_kapat(self):
        if self.istemci is not None:
            self.istemci.kapat()
            self.istemci = None

    def kapat(self):
        self.istemci_kapat()
        try:
            self.p.destroy()
        except tk.TclError:
            pass
