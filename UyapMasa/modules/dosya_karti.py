# -*- coding: utf-8 -*-
"""
Dosya Kartı — "Dosya Görüntüle"nin TEK penceresi (2026-09-30).

Eskiden kart yalnız Tüm Dosyalarım'da (dosyalarim_genel.py) vardı; İcra
Dosyalarım aynı düğmede UYAP'a canlı gidip düz bir mesaj kutusu açıyordu
(kullanıcı bulgusu: "İcra Dosyalarım'da dosya görüntüle dediğimde dosya kartı
açılmıyor"). Kart buraya taşındı; iki ekran da `ac()`'ı çağırır — sekmeler,
DB'den açılış ve "Canlı Veriyi Getir (UYAP)" düğmesi her yerde AYNI.

Akış (kullanıcı kararı, 2026-09-24): kart önce YEREL VERİTABANINDAN açılır
(UYAP'a gitmez, anında); canlı veri karttaki düğmeyle istenir, DB'ye de
kaydedilir ve içerik aynı pencerede yeniden çizilir.
"""

import datetime
import queue
import threading
import tkinter as tk
from tkinter import simpledialog, ttk

from theme import C, RoundButton
from . import dosya_core

# Aynı anda birden çok kart açılabilsin (kullanıcı isteği, 2026-07-13) —
# her yeni pencere hafifçe kaydırılır, üst üste tam binmesinler.
_pencere_sayaci = 0


def _stil_hazirla(app):
    style = ttk.Style(app)
    style.configure("Dosya.TNotebook", background=C.CARD, borderwidth=0)
    style.configure("Dosya.TNotebook.Tab", background=C.SIDEBAR, foreground=C.INK_SOFT,
                    padding=(16, 7), font=app.f_nav_b)
    style.map("Dosya.TNotebook.Tab",
              background=[("selected", C.SAGE_TINT)],
              foreground=[("selected", C.INK)])


def detay_getir(rec, canli, log_fn=None):
    """(ham, aile, kaydedildi, hata, taraflar) — canlı ya da yalnız DB'den."""
    if canli:
        return dosya_core.dosya_detay_goster_ve_kaydet(rec, log_fn=log_fn)
    return dosya_core.dosya_detay_db_den(rec, log_fn=log_fn)


def barkodlar_getir(rec, log_fn=None):
    # DOĞAL ANAHTAR (birim_id+dosya_no) İLE filtrelenir, salt dosyaId İLE
    # DEĞİL: dosyaId oturumluktur, listedeki `rec` eski bir dosyaId taşıyabilir
    # (kullanıcı bulgusu, 2026-08-03 — barkod DB'de varken boş dönüyordu).
    try:
        return dosya_core.tebligat_barkod_gecmisi_listele(
            dosya_id=rec.get("dosyaId"), birim_id=rec.get("birimId"),
            dosya_no=rec.get("dosyaNo"), dosya_tur_kod=rec.get("dosyaTurKod"),
            log_fn=log_fn)
    except Exception as e:
        if log_fn:
            log_fn(f"⚠️ Barkod/tebligat kayıtları okunamadı: {e}")
        return []


def kunye_db_den(rec, log_fn=None):
    """Genel Bilgiler künyesi HER ZAMAN veritabanından — kart hangi ekrandan
    (İcra Dosyalarım / Tüm Dosyalarım / Barkod Sorgulama, geçmiş tablosu dahil)
    açılırsa açılsın aynı alanlar, aynı güncel değerle görünsün; liste
    satırında olmayan alan (ör. geçmiş satırında Durum) boş kalmasın."""
    dosya = dosya_core._dosya_rec_ten_bul(rec, log_fn or (lambda m: None))
    if dosya is None:
        return {}
    return {
        "yargi_turu_adi": dosya_core.YARGI_TURU_ADI.get(dosya.yargi_turu, ""),
        "birimAdi": dosya.birim.ad or "",
        "dosyaNo": dosya.dosya_no or (f"{dosya.yil}/{dosya.sira_no}" if dosya.yil else ""),
        "dosyaTur": dosya.tur or "",
        "dosyaDurum": dosya.durum or "",
        "acilisTarihi": dosya.acilis_tarihi,
        **_islem_durumu_alanlari(dosya),
    }


def _islem_durumu_alanlari(dosya):
    return {
        "islemDurumuKod": dosya.islem_durumu,
        "islemDurumu": dosya.get_islem_durumu_display(),
        "islemDurumuNotu": dosya.islem_durumu_notu,
        "islemDurumuTarihi": dosya.islem_durumu_tarihi,
        "beklemeBitisTarihi": dosya.bekleme_bitis_tarihi,
    }


def _tarih_metni(v):
    if hasattr(v, "strftime"):
        return v.strftime("%d.%m.%Y")
    return v or ""


def _alan_satirlari(app, parent, alanlar, sutun_sayisi=2):
    """[(etiket, değer), ...] — iki grup hâlinde yan yana, kompakt etiketler."""
    for g in range(sutun_sayisi):
        parent.columnconfigure(g * 2 + 1, weight=1)
    for i, (lbl, val) in enumerate(alanlar):
        row, grup = divmod(i, sutun_sayisi)
        col = grup * 2
        tk.Label(parent, text=lbl, bg=C.CARD, fg=C.SAGE_DK, font=app.f_small,
                 anchor="w").grid(row=row, column=col, sticky="w",
                                  padx=(18 if col == 0 else 24, 8), pady=4)
        tk.Label(parent, text=str(val) if val else "—", bg=C.CARD, fg=C.INK,
                 font=app.f_body, anchor="w", justify="left",
                 wraplength=240).grid(row=row, column=col + 1, sticky="w",
                                      padx=(0, 18), pady=4)


def _ozet_sekmesi(app, parent, rec, ham, aile):
    """Künye + yargı-türüne özgü ayrıntı — TEK sekmede. Liste kayıtlarının
    anahtarları ekrana göre biraz farklı (İcra Dosyalarım: dosyaAcilisTarihi,
    yargı türü adı yok) — ikisi de karşılanır."""
    yargi = rec.get("yargi_turu_adi") or {"icra": "İcra", "hukuk": "Hukuk"}.get(aile, "")
    alanlar = [
        ("Yargı Türü", yargi),
        ("Yargı Birimi", rec.get("birimAdi", "")),
        ("Dosya No", rec.get("dosyaNo", "")),
        ("Dosya Türü", rec.get("dosyaTur", "")),
        ("Durum", rec.get("dosyaDurum", "")),
        ("Açılış Tarihi", _tarih_metni(rec.get("acilisTarihi") or rec.get("dosyaAcilisTarihi"))),
        ("İşlem Durumu", rec.get("islemDurumu", "")),
        ("Durum Değişikliği", _tarih_metni(rec.get("islemDurumuTarihi"))),
    ]
    if rec.get("islemDurumuKod") == "beklemede":
        alanlar.append(("Bekleme Bitişi", _tarih_metni(rec.get("beklemeBitisTarihi"))))
    if rec.get("islemDurumuNotu"):
        alanlar.append(("Durum Notu", rec.get("islemDurumuNotu")))
    if aile == "icra":
        alanlar += [
            ("Takibin Türü", ham.get("takibinTuru_metin") or ham.get("takibinTuru", "")),
            ("Takibin Şekli", ham.get("takibinSekli_metin") or ham.get("takibinSekli", "")),
            ("Takibin Yolu", ham.get("takibinYolu_metin") or ham.get("takibinYolu", "")),
            ("Alacak Kalemi Toplam", ham.get("alacakKalemToplamTutar", "")),
            ("Vekalet Ücreti", ham.get("vekaletUcreti", "")),
            ("Tahsil Harcı", ham.get("tahsilHarci", "")),
        ]
    elif aile == "hukuk":
        alanlar += [
            ("Dava Açılış Türü", ham.get("davaAcilisTuru", "")),
            ("Dava Türleri", ham.get("davaTurleriStr", "")),
            ("İlgili Dava Listesi", ham.get("ilgiliDavaListesiStr", "")),
            ("Duruşma Tarihi", ham.get("durusmaTarihi", "")),
        ]
    _alan_satirlari(app, parent, alanlar)
    if aile not in ("icra", "hukuk") and ham:   # ham boşsa sebep "desteklenmiyor" değil, hata
        tk.Label(parent, text="Bu yargı türü için henüz ek ayrıntı görüntüleme desteklenmiyor.",
                 bg=C.CARD, fg=C.INK_SOFT, font=app.f_small, anchor="w",
                 justify="left", wraplength=500).grid(row=99, column=0, columnspan=4,
                                                      sticky="w", padx=18, pady=(10, 4))


def _taraflar_sekmesi(app, parent, taraflar):
    # Kesinleşme/Tebliğ Durumu yalnız borçlu satırlarında dolu olur (bkz.
    # dosya_core._taraflar_kesinlesme_bilgisi_ekle); diğer rollerde "—".
    parent.columnconfigure(1, weight=1)
    for c, baslik in enumerate(("Rol", "Ad / Unvan", "Vekil", "Kesinleşme Durumu", "Tebliğ Durumu")):
        tk.Label(parent, text=baslik, bg=C.CARD, fg=C.INK_SOFT, font=app.f_nav_b,
                 anchor="w").grid(row=0, column=c, sticky="w",
                                  padx=(18 if c == 0 else 0, 18 if c == 4 else 10),
                                  pady=(12, 4))
    for i, t in enumerate(taraflar, start=1):
        tk.Label(parent, text=t.get("rol", "") or "—", bg=C.CARD, fg=C.INK,
                 font=app.f_body, anchor="w").grid(row=i, column=0, sticky="w",
                                                   padx=(18, 10), pady=3)
        for c, val in ((1, t.get("adi", "")), (2, (t.get("vekil") or "").strip("[]")),
                       (3, t.get("kesinlesmeDurumu", "")), (4, t.get("tebligatDurumu", ""))):
            tk.Label(parent, text=val or "—", bg=C.CARD, fg=C.INK, font=app.f_body,
                     anchor="w", justify="left", wraplength=200).grid(
                row=i, column=c, sticky="w", padx=(0, 18 if c == 4 else 10), pady=3)


def _barkod_sekmesi(parent, barkodlar):
    """Barkod Sorgu modülünün DB'ye yazdığı sonuçlar — en yeniden eskiye."""
    parent.columnconfigure(0, weight=1)
    cols = [("evrakTarihi", "Evrak Tarihi", 90), ("tebligatTuru", "Tebligat Türü", 170),
            ("barkod", "Barkod", 110), ("evrakAciklama", "Evrak Açıklaması", 220),
            ("pttDurumu", "PTT Durumu", 160), ("sonIslemTarihi", "Son İşlem Tarihi", 100),
            ("teslimSekli", "Teslim Şekli / İade Nedeni", 200),
            ("tebligMazbatasiVar", "Tebliğ Mazbatası", 100),
            ("kapaliTebligMazbatasiVar", "Kapalı Mazbata", 100),
            ("sorguZamani", "Sorgu Zamanı", 120)]
    tree = ttk.Treeview(parent, columns=[c[0] for c in cols], show="headings", height=8)
    for k, lbl, w in cols:
        tree.heading(k, text=lbl)
        tree.column(k, width=w, anchor="w", stretch=False)
    for rec in barkodlar:
        tree.insert("", "end", values=[rec.get(k, "") for k, _l, _w in cols])
    tree.grid(row=0, column=0, sticky="nsew", padx=18, pady=12)
    ysb = tk.Scrollbar(parent, orient="vertical", command=tree.yview)
    ysb.grid(row=0, column=1, sticky="ns", pady=12)
    tree.configure(yscrollcommand=ysb.set)
    parent.rowconfigure(0, weight=1)


def _arka_planda(top, is_fn, bitince):
    """is_fn'i thread'de çalıştırır, sonucu Tk thread'inde bitince(sonuc)'a
    verir (Tk nesnelerine yalnız ana thread dokunur)."""
    q = queue.Queue()
    threading.Thread(target=lambda: q.put(is_fn()), daemon=True).start()

    def bekle():
        try:
            sonuc = q.get_nowait()
        except queue.Empty:
            if top.winfo_exists():
                top.after(150, bekle)
            return
        bitince(sonuc)
    top.after(150, bekle)


def ac(app, rec, log_fn=None, liste_yenile_fn=None, bitti_fn=None):
    """Seçili liste kaydının dosya kartını açar: önce DB'den (arka planda
    okunur), pencere hazır olunca `bitti_fn()` çağrılır (düğmeyi yeniden
    etkinleştirmek için). Canlı veri kaydedilirse `liste_yenile_fn()` —
    ekran listesi yeni veriyi göstersin."""
    kart_rec = dict(rec)   # listenin kaydı değiştirilmez

    def oku(canli):
        sonuc = detay_getir(kart_rec, canli, log_fn)
        try:
            kart_rec.update({k: v for k, v in kunye_db_den(kart_rec, log_fn).items() if v})
        except Exception as e:
            if log_fn:
                log_fn(f"⚠️ Künye veritabanından okunamadı: {e}")
        return sonuc, barkodlar_getir(kart_rec, log_fn)

    def hazir(veri):
        try:
            _pencere(app, kart_rec, veri[0], veri[1], oku, liste_yenile_fn, log_fn)
        finally:
            if bitti_fn:
                bitti_fn()
    _arka_planda(app, lambda: oku(False), hazir)


def _tarih_coz(metin):
    """'GG.AA.YYYY' → date; boşsa None; geçersizse ValueError."""
    metin = (metin or "").strip()
    if not metin:
        return None
    return datetime.datetime.strptime(metin, "%d.%m.%Y").date()


def _bekleme_penceresi(app, top, rec, durum_kaydet):
    """Beklemeye Al / Beklemeyi Düzenle: bitiş tarihi + not. Dosya zaten
    Beklemede ise alanlar dolu gelir ve "Beklemeyi Bitir" düğmesi çıkar
    (Aktif'e döner)."""
    beklemede = rec.get("islemDurumuKod") == "beklemede"
    dlg = tk.Toplevel(top)
    dlg.title("Beklemeyi Düzenle" if beklemede else "Beklemeye Al")
    dlg.configure(bg=C.CARD)
    dlg.resizable(False, False)
    dlg.transient(top)

    tk.Label(dlg, text="Bekleme bitiş tarihi (GG.AA.YYYY — boş bırakılabilir)", bg=C.CARD,
             fg=C.SAGE_DK, font=app.f_small, anchor="w").pack(anchor="w", padx=18, pady=(16, 4))
    tarih_e = tk.Entry(dlg, font=app.f_body, width=14)
    tarih_e.pack(anchor="w", padx=18)
    tk.Label(dlg, text="Not / gerekçe", bg=C.CARD, fg=C.SAGE_DK, font=app.f_small,
             anchor="w").pack(anchor="w", padx=18, pady=(12, 4))
    not_t = tk.Text(dlg, font=app.f_body, width=48, height=4, wrap="word")
    not_t.pack(fill="x", padx=18)
    if beklemede:
        tarih_e.insert(0, _tarih_metni(rec.get("beklemeBitisTarihi")))
        not_t.insert("1.0", rec.get("islemDurumuNotu") or "")
    hata = tk.Label(dlg, text="", bg=C.CARD, fg=C.CLAY, font=app.f_small, anchor="w")
    hata.pack(anchor="w", padx=18, pady=(6, 0))

    def kaydet():
        try:
            bitis = _tarih_coz(tarih_e.get())
        except ValueError:
            hata.config(text="Tarih GG.AA.YYYY biçiminde olmalı (ör. 15.11.2026).")
            return
        notu = not_t.get("1.0", "end").strip()   # destroy'dan ÖNCE oku
        dlg.destroy()
        durum_kaydet("beklemede", notu, bitis)

    def bitir():
        dlg.destroy()
        durum_kaydet("aktif", "Bekleme bitirildi")

    alt = tk.Frame(dlg, bg=C.CARD)
    alt.pack(fill="x", padx=18, pady=(12, 16))
    RoundButton(alt, "Kaydet", command=kaydet, kind="primary",
                font=app.f_nav_b, height=34).pack(side="right")
    RoundButton(alt, "İptal", command=dlg.destroy, kind="ghost",
                font=app.f_nav_b, height=34).pack(side="right", padx=(0, 8))
    if beklemede:
        RoundButton(alt, "Beklemeyi Bitir (Aktife Al)", command=bitir, kind="ghost",
                    font=app.f_nav_b, height=34).pack(side="left")

    dlg.update_idletasks()
    dlg.geometry(f"+{top.winfo_rootx() + 60}+{top.winfo_rooty() + 80}")
    dlg.grab_set()
    tarih_e.focus_set()


def _pencere(app, rec, sonuc, barkodlar, oku, liste_yenile_fn, log_fn=None):
    global _pencere_sayaci
    _stil_hazirla(app)
    baslik = f"Dosya Bilgileri — {rec.get('dosyaNo', '') or ''}".rstrip(" —")
    top = tk.Toplevel(app)
    top.title(baslik)
    top.configure(bg=C.CARD)
    top.geometry("880x560")   # alt çubukta 4 düğme
    top.minsize(620, 420)
    top.resizable(True, True)
    # BİLEREK: transient/grab_set YOK — pencere bağımsız, ana pencereyi ya da
    # başka bir kartı KİLİTLEMEZ.

    tk.Label(top, text=baslik, bg=C.CARD, fg=C.SAGE_DK, font=app.f_card_t,
             anchor="w", justify="left").pack(anchor="w", padx=18, pady=(16, 10))

    # Alt çubuk içerikten ÖNCE (side=bottom) — içerik yeniden çizilince
    # düğmeler yerinde kalsın.
    alt = tk.Frame(top, bg=C.CARD)
    alt.pack(side="bottom", fill="x", padx=18, pady=(10, 16))
    durum = tk.Label(alt, text="", bg=C.CARD, fg=C.INK_FAINT, font=app.f_small,
                     anchor="w", justify="left", wraplength=420)
    RoundButton(alt, "Kapat", command=top.destroy, kind="primary",
                font=app.f_nav_b, height=36).pack(side="right")
    canli_btn = RoundButton(alt, "Canlı Veriyi Getir (UYAP)", command=lambda: canli_getir(),
                            kind="ghost", font=app.f_nav_b, height=36)
    canli_btn.pack(side="right", padx=(0, 8))
    # Pasife Çevir ↔ Aktife Al (2026-10-02) — yalnız yerel DB, UYAP'a gitmez.
    pasif_btn = RoundButton(alt, "Pasife Çevir", command=lambda: pasif_degistir(),
                            kind="ghost", font=app.f_nav_b, height=36)
    pasif_btn.pack(side="right", padx=(0, 8))
    bekleme_btn = RoundButton(alt, "Beklemeye Al", command=lambda: _bekleme_penceresi(
                                  app, top, rec, durum_kaydet),
                              kind="ghost", font=app.f_nav_b, height=36)
    bekleme_btn.pack(side="right", padx=(0, 8))
    durum.pack(side="left", fill="x", expand=True)

    icerik = tk.Frame(top, bg=C.CARD)
    icerik.pack(fill="both", expand=True)
    son_cizim = {}

    def pasif_btn_tazele():
        kod = rec.get("islemDurumuKod")
        pasif_btn.set_text("Aktife Al" if kod == "pasif" else "Pasife Çevir")
        bekleme_btn.set_text("Beklemeyi Düzenle" if kod == "beklemede" else "Beklemeye Al")

    def pasif_degistir():
        yeni = "aktif" if rec.get("islemDurumuKod") == "pasif" else "pasif"
        notu = simpledialog.askstring(
            "Pasife Çevir" if yeni == "pasif" else "Aktife Al",
            "Gerekçe / not (boş bırakılabilir):", parent=top)
        if notu is None:   # İptal
            return
        durum_kaydet(yeni, notu.strip())

    def durum_kaydet(yeni, notu="", bekleme_bitis=None):
        pasif_btn.set_state("disabled")
        bekleme_btn.set_state("disabled")
        _arka_planda(top, lambda: dosya_core.islem_durumu_degistir(
            rec, yeni, notu, bekleme_bitis, log_fn=log_fn), pasif_degisti)

    def pasif_degisti(dosya):
        if not top.winfo_exists():
            return
        pasif_btn.set_state("normal")
        bekleme_btn.set_state("normal")
        if dosya is None:
            durum.config(text="⚠️ İşlem durumu kaydedilemedi — dosya yerel veritabanında bulunamadı.")
            return
        rec.update(_islem_durumu_alanlari(dosya))
        pasif_btn_tazele()
        ciz(**son_cizim)
        durum.config(text=f"İşlem durumu: {rec['islemDurumu']}")

    def ciz(sonuc, barkodlar, canli):
        son_cizim.update(sonuc=sonuc, barkodlar=barkodlar, canli=canli)
        for w in icerik.winfo_children():
            w.destroy()
        ham, aile, kaydedildi, hata, taraflar = sonuc
        ham = ham or {}
        if canli:
            durum.config(text=("Canlı veri UYAP'tan alındı ve yerel veritabanına kaydedildi."
                               if kaydedildi else "Canlı sorgu tamamlandı."))
        else:
            durum.config(text="Veritabanındaki kayıt gösteriliyor — güncel veri için "
                              "'Canlı Veriyi Getir'e basın.")
        if hata:
            ek = "\nAşağıda yalnız veritabanındaki kayıtlar gösteriliyor." if canli else ""
            tk.Label(icerik, text=f"⚠️ {hata}{ek}", bg=C.CARD, fg=C.CLAY,
                     font=app.f_small, anchor="w", justify="left",
                     wraplength=700).pack(anchor="w", padx=18, pady=(0, 6))
        if ham.get("_onbellekten"):
            tk.Label(icerik, text="⚠️ Canlı sorgu şu an başarısız — daha önce kaydedilmiş veri "
                                  "gösteriliyor (güncel olmayabilir).",
                     bg=C.CARD, fg=C.CLAY, font=app.f_small, anchor="w",
                     justify="left", wraplength=700).pack(anchor="w", padx=18, pady=(0, 6))

        nb = ttk.Notebook(icerik, style="Dosya.TNotebook")
        nb.pack(fill="both", expand=True, padx=18)

        tab_ozet = tk.Frame(nb, bg=C.CARD)
        nb.add(tab_ozet, text="Genel Bilgiler")
        _ozet_sekmesi(app, tab_ozet, rec, ham, aile)

        if taraflar:
            tab_taraf = tk.Frame(nb, bg=C.CARD)
            nb.add(tab_taraf, text="Taraflar")
            _taraflar_sekmesi(app, tab_taraf, taraflar)

        if barkodlar:
            tab_barkod = tk.Frame(nb, bg=C.CARD)
            nb.add(tab_barkod, text="Barkod / Tebligat")
            _barkod_sekmesi(tab_barkod, barkodlar)

    def canli_getir():
        canli_btn.set_state("disabled")
        durum.config(text="UYAP'tan canlı veri alınıyor…")
        _arka_planda(top, lambda: oku(True), canli_geldi)

    def canli_geldi(veri):
        if not top.winfo_exists():
            return
        sonuc_c, barkodlar_c = veri
        canli_btn.set_state("normal")
        pasif_btn_tazele()
        ciz(sonuc_c, barkodlar_c, canli=True)
        if sonuc_c[2] and liste_yenile_fn:   # kaydedildi → liste yeni veriyi göstersin
            liste_yenile_fn()

    pasif_btn_tazele()
    ciz(sonuc, barkodlar or [], canli=False)

    top.update_idletasks()
    kaydirma = (_pencere_sayaci % 8) * 28
    _pencere_sayaci += 1
    x = app.winfo_rootx() + 40 + kaydirma
    y = app.winfo_rooty() + 40 + kaydirma
    top.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    top.focus_set()
