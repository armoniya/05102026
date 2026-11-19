# -*- coding: utf-8 -*-
"""
Kesinleşme Süreleri penceresi (2026-11-14 kullanıcı kararı)
===========================================================
İtiraz süresi takip türüne göre girilir (`icra_models.KesinlesmeKurali`).
Eşleşme UYAP'ın takip kodlarıyla yapılır (takibin türü / yolu / şekli — Dosya
Görüntüle'nin "Takibin Türü/Şekli/Yolu" alanları); boş kod "hepsi" demektir ve
en özel kural kazanır. Üç kodu boş kural varsayılandır. Süre boş bırakılırsa o
türde kesinleşme hesaplanmaz. Kaydedince tüm borçluların kesinleşmesi arka
planda yeniden hesaplanır (UYAP'a gitmez).
"""

import threading
import tkinter as tk
from tkinter import ttk, messagebox

from theme import C

from . import dosya_core


class KesinlesmeKurallariPenceresi:
    def __init__(self, app):
        self.app = app
        self.secili_id = None
        self.win = tk.Toplevel(app)   # uygulama nesnesi Tk kök penceresidir (bkz. dosya_karti)
        self.win.title("Kesinleşme Süreleri")
        self.win.configure(bg=C.BG)
        self.win.geometry("860x620")
        self._build()
        self._yenile()

    # ─────────────────────────── arayüz ───────────────────────────
    def _build(self):
        app, w = self.app, self.win
        tk.Label(w, text="Kesinleşme Süreleri", bg=C.BG, fg=C.INK,
                 font=app.f_card_t).pack(anchor="w", padx=20, pady=(16, 2))
        tk.Label(w, text="Takip türüne göre itiraz süresi. Boş kod = hepsi; en özel kural uygulanır. "
                 "Süre boşsa o türde kesinleşme hesaplanmaz.", bg=C.BG, fg=C.INK_SOFT,
                 font=app.f_small, wraplength=800, justify="left").pack(anchor="w", padx=20)

        ust = tk.Frame(w, bg=C.BG)
        ust.pack(fill="both", expand=True, padx=20, pady=(10, 6))
        tk.Label(ust, text="KURALLAR", bg=C.BG, fg=C.SAGE_DK, font=app.f_nav_b).pack(anchor="w")
        self.kural_tree = ttk.Treeview(ust, columns=("turu", "yolu", "sekli", "ad", "gun", "aktif"),
                                       show="headings", height=7)
        for k, b, gen in (("turu", "Türü", 60), ("yolu", "Yolu", 60), ("sekli", "Şekli", 60),
                          ("ad", "Ad", 360), ("gun", "Süre (gün)", 90), ("aktif", "Aktif", 60)):
            self.kural_tree.heading(k, text=b)
            self.kural_tree.column(k, width=gen, anchor="w")
        self.kural_tree.pack(fill="x")
        self.kural_tree.bind("<<TreeviewSelect>>", self._kural_secildi)

        tk.Label(ust, text="VERİTABANINDAKİ TAKİP KODLARI (çift tık: forma al)", bg=C.BG,
                 fg=C.SAGE_DK, font=app.f_nav_b).pack(anchor="w", pady=(12, 0))
        self.dagilim_tree = ttk.Treeview(ust, columns=("turu", "yolu", "sekli", "adet", "ornek"),
                                         show="headings", height=6)
        for k, b, gen in (("turu", "Türü", 60), ("yolu", "Yolu", 60), ("sekli", "Şekli", 60),
                          ("adet", "Dosya", 70), ("ornek", "Örnek dosya", 450)):
            self.dagilim_tree.heading(k, text=b)
            self.dagilim_tree.column(k, width=gen, anchor="w")
        self.dagilim_tree.pack(fill="x")
        self.dagilim_tree.bind("<Double-1>", self._dagilimdan_al)
        self.bilinmeyen_lbl = tk.Label(ust, text="", bg=C.BG, fg=C.INK_FAINT, font=app.f_small,
                                       wraplength=800, justify="left")
        self.bilinmeyen_lbl.pack(anchor="w", pady=(4, 0))

        form = tk.Frame(w, bg=C.CARD, highlightbackground=C.CARD_EDGE, highlightthickness=1)
        form.pack(fill="x", padx=20, pady=(4, 8))
        ic = tk.Frame(form, bg=C.CARD)
        ic.pack(fill="x", padx=14, pady=10)
        self.v = {k: tk.StringVar() for k in ("turu", "yolu", "sekli", "ad", "gun")}
        self.aktif_var = tk.BooleanVar(value=True)
        for i, (k, etiket, gen) in enumerate((("turu", "Türü", 6), ("yolu", "Yolu", 6),
                                              ("sekli", "Şekli", 6), ("ad", "Ad", 34),
                                              ("gun", "Süre (gün)", 6))):
            tk.Label(ic, text=etiket, bg=C.CARD, fg=C.INK_SOFT, font=app.f_small).grid(
                row=0, column=i, sticky="w", padx=(0, 8))
            tk.Entry(ic, textvariable=self.v[k], width=gen, font=app.f_body, bg=C.BG, fg=C.INK,
                     relief="flat", highlightthickness=1, highlightbackground=C.CARD_EDGE,
                     highlightcolor=C.SAGE_DK).grid(row=1, column=i, sticky="w", padx=(0, 8), ipady=3)
        tk.Checkbutton(ic, text="Aktif", variable=self.aktif_var, bg=C.CARD, fg=C.INK,
                       activebackground=C.CARD, selectcolor=C.CARD, font=app.f_small).grid(
            row=1, column=5, sticky="w")

        dug = tk.Frame(w, bg=C.BG)
        dug.pack(fill="x", padx=20, pady=(0, 6))
        for metin, komut, renk in (("Kaydet", self._kaydet, C.SAGE), ("Yeni", self._yeni, C.INK_FAINT),
                                   ("Sil", self._sil, C.CLAY)):
            tk.Button(dug, text=metin, command=komut, font=app.f_nav_b, bg=renk, fg="white",
                      relief="flat", cursor="hand2", padx=14, pady=4,
                      activebackground=C.SAGE_DK, activeforeground="white").pack(side="left", padx=(0, 8))
        self.durum_lbl = tk.Label(w, text="", bg=C.BG, fg=C.SAGE_DK, font=app.f_small,
                                  wraplength=800, justify="left")
        self.durum_lbl.pack(anchor="w", padx=20, pady=(0, 10))

    # ─────────────────────────── veri ───────────────────────────
    def _yenile(self):
        try:
            self.kurallar = dosya_core.kesinlesme_kurallari_listele()
            dagilim, bilinmeyen = dosya_core.takip_turu_dagilimi()
        except Exception as e:
            self._durum(f"Veritabanı okunamadı: {e}", hata=True)
            return
        self.kural_tree.delete(*self.kural_tree.get_children())
        for k in self.kurallar:
            self.kural_tree.insert("", "end", iid=str(k["id"]), values=(
                k["takibin_turu"] or "*", k["takibin_yolu"] or "*", k["takibin_sekli"] or "*",
                k["ad"], "hesaplanmaz" if k["gun"] is None else k["gun"],
                "Evet" if k["aktif"] else "Hayır"))
        self.dagilim_tree.delete(*self.dagilim_tree.get_children())
        for r in dagilim:
            self.dagilim_tree.insert("", "end", values=(r["turu"], r["yolu"], r["sekli"], r["adet"],
                                                        r["ornek"]))
        self.bilinmeyen_lbl.config(
            text=(f"{bilinmeyen} icra dosyasının takip türü henüz okunmadı (Dosya Görüntüle açılınca "
                  "okunur) — bunlara varsayılan kural uygulanır.") if bilinmeyen else "")

    def _kural_secildi(self, _e=None):
        sec = self.kural_tree.selection()
        if not sec:
            return
        k = next((x for x in self.kurallar if str(x["id"]) == sec[0]), None)
        if not k:
            return
        self.secili_id = k["id"]
        for alan, deger in (("turu", k["takibin_turu"]), ("yolu", k["takibin_yolu"]),
                            ("sekli", k["takibin_sekli"]), ("ad", k["ad"]),
                            ("gun", "" if k["gun"] is None else str(k["gun"]))):
            self.v[alan].set(deger)
        self.aktif_var.set(k["aktif"])

    def _dagilimdan_al(self, _e=None):
        sec = self.dagilim_tree.selection()
        if not sec:
            return
        turu, yolu, sekli = self.dagilim_tree.item(sec[0], "values")[:3]
        self._yeni()
        self.v["turu"].set(turu)
        self.v["yolu"].set(yolu)
        self.v["sekli"].set(sekli)

    def _yeni(self):
        self.secili_id = None
        self.kural_tree.selection_remove(*self.kural_tree.selection())
        for v in self.v.values():
            v.set("")
        self.aktif_var.set(True)

    def _kaydet(self):
        try:
            dosya_core.kesinlesme_kurali_kaydet(
                self.secili_id, self.v["turu"].get(), self.v["yolu"].get(), self.v["sekli"].get(),
                self.v["ad"].get(), self.v["gun"].get().strip() or None, self.aktif_var.get())
        except Exception as e:
            self._durum(f"Kaydedilemedi: {e}", hata=True)
            return
        self._yenile()
        self._yeniden_hesapla()

    def _sil(self):
        if not self.secili_id:
            self._durum("Önce listeden bir kural seçin.", hata=True)
            return
        if not messagebox.askyesno("Kuralı sil", "Seçili kural silinsin mi?", parent=self.win):
            return
        try:
            dosya_core.kesinlesme_kurali_sil(self.secili_id)
        except Exception as e:
            self._durum(f"Silinemedi: {e}", hata=True)
            return
        self._yeni()
        self._yenile()
        self._yeniden_hesapla()

    def _yeniden_hesapla(self):
        self._durum("✔ Kaydedildi. Kesinleşmeler yeniden hesaplanıyor…")

        def is_():
            try:
                n = dosya_core.kesinlesme_durumlarini_guncelle()
                metin = f"✔ Kesinleşmeler yeniden hesaplandı ({n} borçlu satırı değişti)."
            except Exception as e:
                metin = f"Yeniden hesaplanamadı: {e}"
            try:
                self.win.after(0, lambda: self._durum(metin))
            except Exception:
                pass
        threading.Thread(target=is_, daemon=True).start()

    def _durum(self, metin, hata=False):
        self.durum_lbl.config(text=metin, fg=C.CLAY if hata else C.SAGE_DK)
