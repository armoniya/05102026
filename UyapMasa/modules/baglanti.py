# -*- coding: utf-8 -*-
"""
Modül: UYAP Bağlantısı (Paylaş / Al)
====================================
SAKİN tarzda yeni arayüz; ÇALIŞMA MANTIĞI orijinal koddan AYNEN kullanılır.

Orijinal fonksiyonlar (uyap_app.py / uyap_core üzerinden, yeniden yazılmadan):
  • office_agent.run_office(args)   → e-imzayla UYAP oturumu + 8800 proxy + dış ağ paylaşımı
  • home_client.amain(args)         → istemci olarak ofise bağlanma
  • free_port_if_busy, detect_ips   → port temizleme / yerel IP tespiti
  • load_config/save_config/encrypt_secret/decrypt_secret → ayar + DPAPI

Sürücü kalıbı (asyncio loop + thread + iptal) uyap_app'teki start_sharing/
start_receiving ile BİREBİR aynıdır; sadece görünüm değişti.
"""

import argparse
import collections
import os
import threading
import asyncio
import time
import webbrowser
import tkinter as tk

from theme import C, RoundButton


class BaglantiEkrani:
    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.auth = app._auth          # uyap_app modülü (login sonrası yüklü)
        self.port = getattr(self.auth, "LOCAL_PORT", 8800) if self.auth else 8800

        self.is_sharing = False
        self.is_receiving = False

        # Görsel durum makinesi için iç bayraklar (günlük ekranda gösterilmez,
        # yalnızca anlamlı işaretler okunur): Bağlanılıyor → Kuruldu → Hata.
        self._pending_share = False
        self._pending_receive = False
        self._share_ready = False   # cokluuyap.com (signaling) bağlantısı — DIŞ AĞ paylaşımı
        self._recv_ready = False
        self._err_msg = ""
        self._pulse_step = 0
        # 2026-09-24: UYAP e-imza girişi AYRI izlenir. Eskiden "Bağlantı kuruldu"
        # yalnız cokluuyap.com'a bağlanınca çıkıyordu; 23.09 akşamı UYAP girişi
        # takıldığı halde ekran "kuruldu" dedi, barkod turu 4 saat boşa döndü.
        self._uyap_ready = False
        self._uyap_err = ""
        self._uyap_giris_suruyor = False
        # Görünür günlük (son satırlar) + kalıcı günlük dosyası — sorun
        # olduğunda sebebi sonradan görebilmek için.
        self._log_satirlari = collections.deque(maxlen=400)
        self._log_gorunur = False
        kok = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        self._log_yolu = os.path.join(kok, "UyapMasa", "baglanti.log")

        self._build()
        self._refresh_state()
        self._poll_log()

    # ─────────────────────────────── arayüz ───────────────────────────────
    def _build(self):
        wrap = tk.Frame(self.parent, bg=C.BG)
        wrap.pack(fill="both", expand=True, padx=40, pady=34)

        tk.Label(wrap, text="UYAP Bağlantısı", bg=C.BG, fg=C.INK,
                 font=self.app.f_h1).pack(anchor="w")
        tk.Label(wrap, text="E-imza ile UYAP oturumunu paylaşın ya da bir ofise bağlanın. "
                            "Oturum 127.0.0.1:%d üzerinde sunulur." % self.port,
                 bg=C.BG, fg=C.INK_SOFT, font=self.app.f_sub).pack(anchor="w", pady=(6, 22))

        cards = tk.Frame(wrap, bg=C.BG)
        cards.pack(fill="x")
        cards.grid_columnconfigure(0, weight=1, uniform="c")
        cards.grid_columnconfigure(1, weight=1, uniform="c")

        self._build_share_card(cards)
        self._build_receive_card(cards)

        # ── Görsel bağlantı durumu (eski "İşlem Günlüğü" yerine) ──
        # Kullanıcı teknik günlükle ilgilenmez; tek, belirgin bir durum gösterilir:
        # Bağlanılıyor → Bağlantı kuruldu → Bağlantı hatası.
        self.state_box = tk.Frame(wrap, bg=C.CARD, highlightbackground=C.CARD_EDGE,
                                  highlightthickness=1)
        self.state_box.pack(fill="x", pady=(24, 0))
        sb_in = tk.Frame(self.state_box, bg=C.CARD)
        sb_in.pack(fill="x", padx=22, pady=16)

        self.state_dot = tk.Label(sb_in, text="●", bg=C.CARD, fg=C.INK_FAINT,
                                  font=self.app.f_card_t)
        self.state_dot.pack(side="left", padx=(0, 14))
        txt = tk.Frame(sb_in, bg=C.CARD)
        txt.pack(side="left", fill="x", expand=True)
        self.state_title = tk.Label(txt, text="Bağlantı bekleniyor", bg=C.CARD,
                                    fg=C.INK, font=self.app.f_card_t, anchor="w")
        self.state_title.pack(anchor="w")
        self.state_sub = tk.Label(
            txt, text="Paylaşmak ya da bir ofise bağlanmak için yukarıdan bir işlem başlatın.",
            bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_body, anchor="w", justify="left")
        self.state_sub.pack(anchor="w", pady=(2, 0))

        # Zemin/kenar rengini topluca değiştirmek için bandın tüm yüzeyleri.
        self._state_faces = [self.state_box, sb_in, self.state_dot, txt,
                             self.state_title, self.state_sub]

        # ── Ayrıntılı günlük (varsayılan kapalı; 2026-09-24) ──
        self.log_toggle = tk.Label(wrap, text="▸ Ayrıntılı günlüğü göster", bg=C.BG,
                                   fg=C.SAGE_DK, font=self.app.f_small, cursor="hand2")
        self.log_toggle.pack(anchor="w", pady=(10, 0))
        self.log_toggle.bind("<Button-1>", lambda e: self._log_ac_kapa())
        self.log_frame = tk.Frame(wrap, bg=C.BG)
        self.log_text = tk.Text(self.log_frame, height=12, wrap="word", bg="#FFFFFF", fg=C.INK,
                                relief="flat", highlightthickness=1, highlightbackground=C.LINE,
                                font=self.app.f_small, state="disabled")
        sb = tk.Scrollbar(self.log_frame, command=self.log_text.yview)
        self.log_text.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)
        tk.Label(wrap, text=f"Günlük dosyası: {self._log_yolu}", bg=C.BG, fg=C.INK_FAINT,
                 font=self.app.f_small).pack(anchor="w", side="bottom")

    def _log_ac_kapa(self):
        self._log_gorunur = not self._log_gorunur
        if self._log_gorunur:
            self.log_frame.pack(fill="both", expand=True, pady=(6, 0))
            self.log_toggle.config(text="▾ Ayrıntılı günlüğü gizle")
            self._log_text_yenile()
        else:
            self.log_frame.pack_forget()
            self.log_toggle.config(text="▸ Ayrıntılı günlüğü göster")

    def _log_text_yenile(self):
        if not self._log_gorunur:
            return
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("end", "\n".join(self._log_satirlari))
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _card(self, parent, col, legend, title):
        holder = tk.Frame(parent, bg=C.BG)
        holder.grid(row=0, column=col, sticky="nsew",
                    padx=(0, 9) if col == 0 else (9, 0))
        card = tk.Frame(holder, bg=C.CARD, highlightbackground=C.CARD_EDGE,
                        highlightthickness=1)
        card.pack(fill="both", expand=True)
        inner = tk.Frame(card, bg=C.CARD)
        inner.pack(fill="both", expand=True, padx=20, pady=18)
        tk.Label(inner, text=legend, bg=C.SAGE_TINT, fg=C.SAGE_DK,
                 font=self.app.f_small, padx=9, pady=2).pack(anchor="w")
        tk.Label(inner, text=title, bg=C.CARD, fg=C.INK,
                 font=self.app.f_card_t).pack(anchor="w", pady=(10, 14))
        # Aksiyonları (butonlar + durum) dibe sabitle; gövde aradaki boşluğu doldurur.
        # Böylece iki karttaki butonlar, üst içerik farklı yükseklikte olsa da hizalı kalır.
        actions = tk.Frame(inner, bg=C.CARD)
        actions.pack(side="bottom", fill="x")
        body = tk.Frame(inner, bg=C.CARD)
        body.pack(side="top", fill="both", expand=True)
        return body, actions

    def _entry(self, parent, label, show=None):
        tk.Label(parent, text=label, bg=C.CARD, fg=C.INK_SOFT,
                 font=self.app.f_small).pack(anchor="w", pady=(0, 4))
        e = tk.Entry(parent, show=(show or ""), bg="#FFFFFF", fg=C.INK,
                     relief="flat", insertbackground=C.INK, font=self.app.f_body,
                     highlightthickness=1, highlightbackground=C.LINE,
                     highlightcolor=C.SAGE)
        e.pack(fill="x", ipady=6, pady=(0, 13))
        return e

    def _btn(self, parent, text, cmd, kind="primary"):
        b = RoundButton(parent, text, command=cmd, kind=kind,
                        font=self.app.f_nav_b, height=38)
        b.pack(fill="x", pady=(2, 6))
        return b

    def _build_share_card(self, parent):
        body, actions = self._card(parent, 0, "OFİS", "Bağlantı Paylaş")
        cfg = {}
        try:
            cfg = self.auth.load_config() if self.auth else {}
        except Exception:
            cfg = {}
        # Sertifika ID artık arayüzde gösterilmez; varsa ayardan sessizce kullanılır
        # (boşsa office_agent uygun sertifikayı otomatik seçer).
        self._cert_id = cfg.get("cert_id") or ""
        self.pin_entry = self._entry(body, "E-imza PIN kodu", show="•")
        try:
            if cfg.get("pin_enc"):
                self.pin_entry.insert(0, self.auth.decrypt_secret(cfg["pin_enc"]))
        except Exception:
            pass
        self.share_btn = self._btn(actions, "Bağlantıyı Paylaş", self.on_share)
        self.share_browser_btn = self._btn(actions, "Tarayıcıyı Aç (Kendim Gir)",
                                           self.open_browser, kind="ghost")
        self.share_browser_btn.set_state("disabled")
        self.share_status = tk.Label(actions, text="● Paylaşım durduruldu", bg=C.CARD,
                                     fg=C.INK_FAINT, font=self.app.f_small)
        self.share_status.pack(anchor="w", pady=(6, 0))

    def _build_receive_card(self, parent):
        body, actions = self._card(parent, 1, "İSTEMCİ", "Bağlantı Al")
        tk.Label(body, text="Ofise bağlanmak için kullanın; yerel ya da dış ağ "
                            "otomatik seçilir.",
                 bg=C.CARD, fg=C.INK_SOFT, font=self.app.f_body,
                 wraplength=300, justify="left").pack(anchor="w", pady=(0, 16))
        self.receive_btn = self._btn(actions, "Bağlantıyı Al", self.on_receive)
        self.receive_browser_btn = self._btn(actions, "Tarayıcıyı Aç",
                                             self.open_browser, kind="ghost")
        self.receive_browser_btn.set_state("disabled")
        self.receive_status = tk.Label(actions, text="● Bağlantı yok", bg=C.CARD,
                                       fg=C.INK_FAINT, font=self.app.f_small)
        self.receive_status.pack(anchor="w", pady=(6, 0))

    # ─────────────────────────── PAYLAŞ (orijinal mantık) ───────────────────────────
    def on_share(self):
        if self.is_sharing:
            # 2026-09-24: 17:42'de paylaşım, e-imza girişi bitmeden (hatasız) iptal
            # edildi; sebebi bilinmiyor. Giriş sürerken yanlışlıkla durdurmayı önle.
            if not self._uyap_ready:
                from tkinter import messagebox
                if not messagebox.askyesno(
                        "Paylaşım durdurulsun mu?",
                        "UYAP girişi henüz tamamlanmadı (e-imza girişi sürüyor).\n\n"
                        "Paylaşımı şimdi durdurursanız UYAP bağlantısı kurulmaz ve barkod/21-2 "
                        "sorguları çalışmaz. Yine de durdurulsun mu?"):
                    return
            self.stop_sharing(sebep="Paylaşımı Durdur düğmesi")
        else:
            self.start_sharing()

    def start_sharing(self):
        pin = self.pin_entry.get().strip()
        cert_id = getattr(self, "_cert_id", "") or ""
        if not pin:
            self._err_msg = "E-imza PIN kodu gerekli."
            self.share_status.config(text="● PIN kodu gerekli", fg=C.CLAY)
            self._refresh_state()
            return
        if not self.auth:
            self._err_msg = "Giriş modülü yüklenmedi."
            self._refresh_state()
            return

        self.is_sharing = True
        self._err_msg = ""
        self._share_ready = False
        self._uyap_ready = False
        self._uyap_err = ""
        self._uyap_giris_suruyor = True
        self._giris_bas = time.time()
        self._pending_share = True
        self._refresh_state()
        self.pin_entry.config(state="disabled")
        self._save_cfg()

        # Orijinal fonksiyon: 8800'ü tutan eski süreci temizle
        self.auth.free_port_if_busy(self.port, self.app.log_queue.put)
        self._log("[SİSTEM] UYAP oturumu kuruluyor; LAN + dış ağ paylaşımı başlatılıyor...\n")

        self.share_loop = asyncio.new_event_loop()
        office_args = argparse.Namespace(
            signaling=self.auth.DEFAULT_SERVER_URL, room=self.app.username,
            password=self.app._password, pin=pin, cert_id=cert_id or None,
            no_verify=False, interval=5, proxy_port=self.port, host="0.0.0.0",
            user=self.app.username,
        )

        def run_office_thread():
            asyncio.set_event_loop(self.share_loop)
            # ORİJİNAL: office_agent.run_office
            self.share_task = self.share_loop.create_task(
                self.auth.office_agent.run_office(office_args))
            try:
                self.share_loop.run_until_complete(self.share_task)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.app.log_queue.put(f"[HATA] Paylaşım durdu: {e}\n")
            finally:
                self.app.log_queue.put("[SİSTEM] Paylaşım sonlandı.\n")
                self.app.after(0, lambda: self._on_exit("office"))
                try:
                    self.share_loop.close()
                except Exception:
                    pass

        self.share_thread = threading.Thread(target=run_office_thread, daemon=True)
        self.share_thread.start()

        self.share_btn.set_text("Paylaşımı Durdur")
        self.share_btn.set_kind("stop")
        self.share_browser_btn.set_state("normal")
        # "Aktif" yazısı artık UYAP girişi GERÇEKTEN tamamlanınca konur (_refresh_state).
        self.share_status.config(text="● UYAP girişi yapılıyor…", fg=C.GOLD)

        # Bu bilgisayardan giriş yalnız "Tarayıcıda Aç" düğmesiyle (tek kullanımlık güvenli
        # bağlantı, güvenlik taraması bulgu 4.1); düz 127.0.0.1 adresi doğrudan açılamaz.
        ips = self.auth.detect_ips()
        addrs = [f"http://{ip}:{self.port}/giris" for ip in ips]
        self._log("[SİSTEM] Bu bilgisayardan giriş: 'Tarayıcıda Aç' düğmesi. Yerel ağdaki diğer "
                  "bilgisayarlar (parolayla): " + ("  |  ".join(addrs) or "—") + "\n")
        self._log(f"[SİSTEM] Dış ağ: '{self.app.username}' kullanıcısıyla "
                  f"{self.auth.DEFAULT_SERVER_URL} üzerinden paylaşılıyor.\n")

    def stop_sharing(self, unexpected=False, sebep=""):
        # Neden durdurulduğu günlüğe (baglanti.log) yazılır — 2026-09-24'te
        # paylaşım sessizce iptal edilmiş, sebebi ayırt edilememişti.
        if self.is_sharing:
            # Kuyruk yerine DOĞRUDAN dosyaya: program kapanırken (os._exit) kuyruk işlenmez.
            self._gunluge_ekle(f"[SİSTEM] Paylaşım durduruluyor — sebep: "
                               f"{sebep or ('beklenmedik kapanış' if unexpected else 'belirtilmedi')}")
        self.is_sharing = False
        if getattr(self, "share_loop", None) and not self.share_loop.is_closed():
            if getattr(self, "share_task", None):
                try:
                    self.share_loop.call_soon_threadsafe(self.share_task.cancel)
                except Exception as e:
                    self._log(f"[SİSTEM] Paylaşım iptalinde hata: {e}\n")
        self.share_btn.set_text("Bağlantıyı Paylaş")
        self.share_btn.set_kind("primary")
        self.share_browser_btn.set_state("disabled")
        self.share_status.config(text="● Paylaşım durduruldu", fg=C.INK_FAINT)
        self.pin_entry.config(state="normal")
        self._pending_share = False
        self._share_ready = False
        self._uyap_ready = False
        self._uyap_giris_suruyor = False
        if unexpected:
            self._err_msg = "Paylaşım beklenmedik bir hata nedeniyle durdu."
        self._refresh_state()

    # ─────────────────────────── AL (orijinal mantık) ───────────────────────────
    def on_receive(self):
        if self.is_receiving:
            self.stop_receiving()
        else:
            self.start_receiving()

    def start_receiving(self):
        if not self.auth:
            self._err_msg = "Giriş modülü yüklenmedi."
            self._refresh_state()
            return
        self.is_receiving = True
        self._err_msg = ""
        self._recv_ready = False
        self._pending_receive = True
        self._refresh_state()
        self.auth.free_port_if_busy(self.port, self.app.log_queue.put)
        self._log("[SİSTEM] İstemci başlatılıyor (önce yerel ağ, olmazsa dış ağ)...\n")

        self.receiver_loop = asyncio.new_event_loop()
        receiver_args = argparse.Namespace(
            signaling=self.auth.DEFAULT_SERVER_URL, room=self.app.username,
            password=self.app._password, host="127.0.0.1", port=self.port,
        )

        def run_receiver():
            asyncio.set_event_loop(self.receiver_loop)
            # ORİJİNAL: home_client.amain
            self.receiver_task = self.receiver_loop.create_task(
                self.auth.home_client.amain(receiver_args))
            try:
                self.receiver_loop.run_until_complete(self.receiver_task)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.app.log_queue.put(f"[HATA] İstemci durdu: {e}\n")
            finally:
                self.app.log_queue.put("[SİSTEM] İstemci sonlandı.\n")
                self.app.after(0, lambda: self._on_exit("home_client"))
                try:
                    self.receiver_loop.close()
                except Exception:
                    pass

        self.receiver_thread = threading.Thread(target=run_receiver, daemon=True)
        self.receiver_thread.start()

        self.receive_btn.set_text("Bağlantıyı Kes")
        self.receive_btn.set_kind("stop")
        self.receive_browser_btn.set_state("normal")
        self.receive_status.config(text="● Bağlantı aktif", fg=C.SAGE_DK)

    def stop_receiving(self, unexpected=False):
        self.is_receiving = False
        if getattr(self, "receiver_loop", None) and not self.receiver_loop.is_closed():
            if getattr(self, "receiver_task", None):
                try:
                    self.receiver_loop.call_soon_threadsafe(self.receiver_task.cancel)
                except Exception as e:
                    self._log(f"[SİSTEM] Alıcı iptalinde hata: {e}\n")
        self.receive_btn.set_text("Bağlantıyı Al")
        self.receive_btn.set_kind("primary")
        self.receive_browser_btn.set_state("disabled")
        self.receive_status.config(text="● Bağlantı yok", fg=C.INK_FAINT)
        self._pending_receive = False
        self._recv_ready = False
        if unexpected:
            self._err_msg = "İstemci bağlantısı beklenmedik şekilde kesildi."
        self._refresh_state()

    def _on_exit(self, name):
        if name == "office" and self.is_sharing:
            self.stop_sharing(unexpected=True)
        elif name == "home_client" and self.is_receiving:
            self.stop_receiving(unexpected=True)

    def open_browser(self):
        # Paylaşımda proxy tarayıcıyı yalnız tek kullanımlık giriş bağlantısıyla kabul eder
        # (güvenlik taraması bulgu 4.1, bkz. uyap_core/yerel_giris.py); alıcı modunda düz adres döner.
        def _ac():
            try:
                from uyap_core.yerel_giris import tarayici_giris_url
                url = tarayici_giris_url(self.port)
            except Exception:
                url = f"http://127.0.0.1:{self.port}/giris"
            try:
                webbrowser.open(url)
            except Exception as e:
                self._log(f"[HATA] Tarayıcı açılamadı: {e}\n")
        threading.Thread(target=_ac, daemon=True).start()

    def _save_cfg(self):
        try:
            cfg = self.auth.load_config()
            cfg["pin_enc"] = self.auth.encrypt_secret(self.pin_entry.get().strip())
            cfg["local_port"] = self.port
            self.auth.save_config(cfg)
        except Exception:
            pass

    # ─────────────────────── günlük → görsel durum ───────────────────────
    def _log(self, text):
        self.app.log_queue.put(text)

    def _gunluge_ekle(self, ln):
        """Etiketli ([*] [+] [!] [HATA] [SİSTEM] …) satırları görünür günlüğe ve
        dosyaya yazar. Ortak stdout kuyruğuna başka modüllerin düz çıktıları da
        düşebildiği için yalnız '[' ile başlayan satırlar alınır."""
        eklendi = False
        for satir in str(ln).splitlines():
            satir = satir.strip()
            if not satir.startswith("["):
                continue
            damgali = f"{time.strftime('%d.%m %H:%M:%S')}  {satir}"
            self._log_satirlari.append(damgali)
            eklendi = True
            try:
                if os.path.exists(self._log_yolu) and os.path.getsize(self._log_yolu) > 2_000_000:
                    os.replace(self._log_yolu, self._log_yolu + ".1")
                with open(self._log_yolu, "a", encoding="utf-8") as f:
                    f.write(damgali + "\n")
            except OSError:
                pass
        return eklendi

    def _scan_markers(self, text):
        """Sunucu/ajan günlüğünden anlamlı işaretleri süzer (ayrıca etiketli
        satırları görünür günlüğe/dosyaya yazar). Kurtarılabilir uyarılar ([!])
        genel hata sayılmaz; yalnızca [HATA] hata sayılır — ama UYAP girişine
        dair [!] satırları UYAP durumunu belirler (bkz. uyap_proxy.lifespan/relogin)."""
        ln = str(text)
        self._gunluge_ekle(ln)
        # UYAP e-imza girişi (uyap_proxy.py'nin bastığı satırlar):
        if "İlk giriş tamam" in ln or "[+] Oturum hazır" in ln:
            self._uyap_ready, self._uyap_err, self._uyap_giris_suruyor = True, "", False
        elif "İlk giriş başarısız" in ln:
            # Biçim: "[!] İlk giriş başarısız (<neden>). Otomatik yeniden deneme yapılmayacak."
            self._uyap_ready, self._uyap_giris_suruyor = False, False
            kuyruk = ln.split("İlk giriş başarısız", 1)[1]
            neden = kuyruk.split("(", 1)[1].rsplit(")", 1)[0] if "(" in kuyruk and ")" in kuyruk else kuyruk
            self._uyap_err = neden.strip(" .\n") or "bilinmeyen hata"
        elif "Oturum yok/koptu" in ln:
            self._uyap_ready, self._uyap_giris_suruyor = False, True
            self._giris_bas = time.time()
        elif "Keepalive hatası" in ln:
            self._uyap_ready, self._uyap_giris_suruyor = False, False
            self._uyap_err = ln.split("Keepalive hatası", 1)[1].strip(" :\n") or "bilinmeyen hata"
        if "[HATA]" in ln:
            msg = ln.split("[HATA]", 1)[1].strip()
            if msg:
                self._err_msg = msg
            return
        # Paylaşım tarafı hazır sinyalleri (office_agent)
        if ("oda hazır" in ln or "Signaling'e bağlandı" in ln
                or "DataChannel açıldı" in ln):
            self._share_ready = True
        # Alıcı tarafı hazır sinyalleri (home_client)
        if "P2P kanal açık" in ln or "Doğrudan LAN bağlantısı" in ln:
            self._recv_ready = True

    def _set_face_bg(self, bg):
        for w in self._state_faces:
            try:
                w.config(bg=bg)
            except Exception:
                pass

    def _render_state(self, state, title, sub):
        colors = {
            "idle":       (C.INK_FAINT, C.CARD,      C.CARD_EDGE),
            "connecting": (C.GOLD,      C.CARD,      C.GOLD),
            "connected":  (C.SAGE_DK,   C.SAGE_TINT, C.SAGE),
            "error":      (C.CLAY,      C.CARD,      C.CLAY),
        }
        dot, bg, edge = colors.get(state, colors["idle"])
        self._set_face_bg(bg)
        self.state_box.config(highlightbackground=edge, highlightcolor=edge)
        self.state_dot.config(fg=dot)
        self.state_title.config(text=title)
        self.state_sub.config(text=sub)
        self._cur_state = state

    def _paylasim_etiketini_guncelle(self):
        if not self.is_sharing:
            return
        if self._uyap_ready:
            metin, renk = "● Paylaşım aktif (UYAP oturumu açık)", C.SAGE_DK
        elif self._uyap_err:
            metin, renk = "● UYAP girişi başarısız — denemeler durduruldu", C.CLAY
        else:
            metin, renk = "● UYAP girişi yapılıyor…", C.GOLD
        self.share_status.config(text=metin, fg=renk)

    def _refresh_state(self):
        self._paylasim_etiketini_guncelle()
        # Paylaşımda "kuruldu" = UYAP e-imza girişi GERÇEKTEN tamam (2026-09-24).
        # cokluuyap.com bağlantısı (dış ağ) yalnız alt metinde bilgi olarak geçer.
        if self.is_sharing and not self._uyap_ready:
            if self._uyap_err:
                self._render_state(
                    "error", "UYAP girişi başarısız",
                    f"{self._uyap_err}\nProgram kendiliğinden yeniden denemez. "
                    "E-imza kartını ve PIN'i kontrol edip paylaşımı durdurup yeniden başlatın.")
            else:
                dis = ("cokluuyap.com bağlantısı kuruldu; " if self._share_ready else "")
                sub = f"{dis}e-imza ile UYAP girişi henüz tamamlanmadı. Bu sırada UYAP'a sorgu gitmez."
                gecen = time.time() - getattr(self, "_giris_bas", time.time())
                if gecen > 120:
                    sub += (f"\n{int(gecen // 60)} dakikadır sürüyor — giriş takılmış olabilir. "
                            "Paylaşımı durdurup yeniden başlatın; düzelmezse UyapMasa'yı kapatıp açın. "
                            "Ayrıntılı günlükte son satırlara bakın.")
                self._render_state("connecting", "UYAP girişi bekleniyor…", sub)
            return

        connected = ((self.is_sharing and self._uyap_ready)
                     or (self.is_receiving and self._recv_ready))
        connecting = ((self.is_sharing or self._pending_share
                       or self.is_receiving or self._pending_receive)
                      and not connected)

        if connected:
            if self.is_sharing:
                sub = ("UYAP oturumu açık. " + ("Dış ağ paylaşımı da hazır; istemciler bağlanabilir."
                                               if self._share_ready else
                                               "Dış ağ (cokluuyap.com) paylaşımı henüz hazır değil."))
            else:
                sub = "Ofis bilgisayarına bağlandınız."
            self._render_state("connected", "Bağlantı kuruldu", sub)
        elif connecting:
            self._render_state("connecting", "Bağlanılıyor…",
                               "UYAP oturumu hazırlanıyor, lütfen bekleyin.")
        elif self._err_msg:
            self._render_state("error", "Bağlantı hatası", self._err_msg)
        else:
            self._render_state(
                "idle", "Bağlantı bekleniyor",
                "Paylaşmak ya da bir ofise bağlanmak için yukarıdan bir işlem başlatın.")

    def _poll_log(self):
        changed = False
        n = 0
        while n < 200:
            try:
                self._scan_markers(self.app.log_queue.get_nowait())
                changed = True
                n += 1
            except Exception:
                break
        if changed:
            self._refresh_state()
            self._log_text_yenile()
        elif self.is_sharing and not self._uyap_ready \
                and time.time() - getattr(self, "_son_periyodik", 0) > 5:
            self._son_periyodik = time.time()
            self._refresh_state()   # 5 sn'de bir: "N dakikadır sürüyor" uyarısı güncellensin
        # "Bağlanılıyor" sırasında noktaya yumuşak bir nabız ver.
        if getattr(self, "_cur_state", "idle") == "connecting":
            self._pulse_step += 1
            bright = (self._pulse_step // 4) % 2   # ~560ms'de bir, yumuşak geçiş
            self.state_dot.config(fg=C.GOLD if bright else "#E2D0AC")
        self.app.after(140, self._poll_log)
