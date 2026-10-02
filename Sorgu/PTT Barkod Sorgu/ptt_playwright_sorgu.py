# -*- coding: utf-8 -*-
"""
PTT Barkod Sorgu — Playwright ile (bağımsız program)
=====================================================
Excel'deki barkodları ptt.gov.tr'deki "Gönderi Takip" kutusuna TEK TEK yazar,
SORGULA'ya tıklar ve sayfanın kendi takip isteğinin (POST .../api/ShipmentTracking)
yanıtını okuyup sonucu yeni bir Excel dosyasına yazar.

Kurulum:   pip install playwright openpyxl      (Chrome kuruluysa ayrıca tarayıcı
           indirmek gerekmez; yoksa: playwright install chromium)
Kullanım:  python ptt_playwright_sorgu.py [barkodlar.xlsx] [--bekleme 3] [--headless]
           (Excel verilmezse bir dosya seçme penceresi açılır.)

ÖNEMLİ — BOT DOĞRULAMASI: ptt.gov.tr her sorguda Cloudflare Turnstile (görünmez bot
doğrulaması) çalıştırıyor. Bu program onu ATLATMAYA/GİZLENMEYE çalışmaz (gizleme
eklentisi, parmak izi taklidi, çözücü servis YOK); siteyi görünür bir Chrome'da normal
kullanıcı gibi kullanır. Doğrulama otomasyonu reddederse sayfa "Doğrulama
tamamlanamadı" der; bu durumda program 3 ardışık başarısızlıktan sonra durur, o ana
kadarki sonuçları yazar ve ekran görüntüsünü `hata_goruntuleri` klasörüne kaydeder.
Başsız (--headless) mod otomasyon olarak daha kolay tanınır; varsayılan görünür moddur.

Excel girdisi: aktif sayfanın H sütunu (değiştirmek için --sutun); sütunun ilk dolu
hücresi "barkod" içeren bir başlıksa atlanır. UYAP bağlantısı YOKTUR. Çıktı:
girdiyle aynı klasöre `<ad>_PTT_Sonuc_<tarih_saat>.xlsx`.

Tek Excel üzerinden ilerlemek için sonuçlar (PTT durumu + son işlem/teslim tarihi)
ayrıca GİRDİ Excel'inin barkod sütununun yanına (varsayılan I ve J; --sonuc-sutunu)
aynı satıra yazılır. Yazmadan önce girdinin `<ad>_oncesi_<tarih_saat>` yedeği alınır;
kapatmak için --girdiye-yazma. Excel'de açıksa `<ad>_PTT.xlsx` olarak kaydedilir.

Yanıt biçimi (ana projede canlı doğrulanmış, 2026-07-28): tek elemanlı liste;
`errorState` "kayıt VAR mı" demektir (adı yanıltıcı); `sondurum.son_durum_aciklama`,
`sondurum.teslim_tarihi` (teslim edilmemişken literal "0" → `son_islem_tarihi`),
`hareketDongu[]`, `kabul`. "KAYIT YOK" = PTT'de kaydı yok (elektronik tebligat olabilir).
"""

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from copy import copy

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import column_index_from_string, get_column_letter
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

VARSAYILAN_SUTUN = "H"      # barkodların okunduğu Excel sütunu
SONUC_SUTUNU = "I"          # girdi Excel'inde sonucun yazıldığı ilk sütun (durum), sonrakisi tarih
KLASOR = os.path.dirname(os.path.abspath(__file__))
SITE_URL = "https://www.ptt.gov.tr/"
TAKIP_YOLU = "/api/ShipmentTracking"
GIRIS_SECICI = 'input[placeholder="Gönderi takip no giriniz"]:visible'
GIRIS_YEDEK_SECICI = 'input[name="value"]:visible'
DOGRULAMA_HATASI = "Doğrulama tamamlanamadı"
ARDISIK_HATA_SINIRI = 3
BARKOD_RE = re.compile(r"[A-Za-z0-9]{8,30}")

KOLONLAR = ["Sıra", "Excel Satırı", "Barkod", "PTT Durumu", "Son İşlem Tarihi",
            "Kabul Tarihi", "Gönderici", "Durum", "Hareket Geçmişi", "Ham Yanıt"]


# ── Excel okuma / yazma ──────────────────────────────────────────────────────
def barkod_temizle(deger):
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
    [(excel_satiri, barkod), ...]. Boş hücreler atlanır; sütunun ilk dolu hücresi
    'barkod' içeren bir başlıksa (örn. 'Barkod No') o satır atlanır."""
    n = sutun_indeksi(sutun)
    wb = openpyxl.load_workbook(yol, data_only=True, read_only=True)
    try:
        kayitlar, ilk = [], True
        for r, satir in enumerate(wb.active.iter_rows(values_only=True), start=1):
            barkod = barkod_temizle(satir[n - 1] if len(satir) >= n else None)
            if not barkod:
                continue
            if ilk:
                ilk = False
                if "barkod" in barkod.lower():
                    continue
            kayitlar.append((r, barkod))
        return kayitlar
    finally:
        wb.close()


def cikti_yolu_uret(girdi_yolu, istenen=None):
    if istenen:
        return istenen
    klasor, ad = os.path.split(os.path.abspath(girdi_yolu))
    dosya = f"{os.path.splitext(ad)[0]}_PTT_Sonuc_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"
    return os.path.join(klasor if os.access(klasor, os.W_OK) else os.path.expanduser("~"), dosya)


def excel_yaz(satirlar, yol):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "PTT Sonuc"
    ws.append(KOLONLAR)
    barkod_sutunu = KOLONLAR.index("Barkod") + 1
    for s in satirlar:
        ws.append(s)
        ws.cell(row=ws.max_row, column=barkod_sutunu).number_format = "@"
    dolgu = PatternFill("solid", fgColor="1F4E78")
    for hucre in ws[1]:
        hucre.font = Font(bold=True, color="FFFFFF")
        hucre.fill = dolgu
    genislik = {"Sıra": 6, "Excel Satırı": 11, "Barkod": 20, "PTT Durumu": 28,
                "Son İşlem Tarihi": 17, "Kabul Tarihi": 13, "Gönderici": 30,
                "Durum": 40, "Hareket Geçmişi": 55, "Ham Yanıt": 30}
    for i, k in enumerate(KOLONLAR, start=1):
        ws.column_dimensions[get_column_letter(i)].width = genislik[k]
    for satir in ws.iter_rows(min_row=2):
        satir[KOLONLAR.index("Hareket Geçmişi")].alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(yol)


def girdiye_yaz(yol, satirlar, sutun=VARSAYILAN_SUTUN, sonuc_sutunu=SONUC_SUTUNU):
    """Sonucu (PTT durumu, tarih) girdi Excel'inde barkodun bulunduğu satıra, `sonuc_sutunu`
    ve bir sağındaki sütuna yazar; diğer hücrelere dokunmaz. Yazmadan önce yedek alır.
    Excel'de açıksa (kilit) `<ad>_PTT` adıyla yanına kaydeder. Yazılan dosyanın yolunu döner."""
    n, hedef = sutun_indeksi(sutun), sutun_indeksi(sonuc_sutunu)
    if n in (hedef, hedef + 1):
        raise ValueError(f"Sonuç sütunları ({get_column_letter(hedef)}, {get_column_letter(hedef + 1)}) "
                         f"barkod sütunuyla ({get_column_letter(n)}) çakışıyor.")
    klasor, ad = os.path.split(os.path.abspath(yol))
    kok, uzanti = os.path.splitext(ad)
    yedek = os.path.join(klasor, f"{kok}_oncesi_{time.strftime('%Y%m%d_%H%M%S')}{uzanti}")
    shutil.copy2(yol, yedek)

    wb = openpyxl.load_workbook(yol, keep_vba=uzanti.lower() == ".xlsm")   # formüller korunur
    try:
        ws = wb.active
        # barkod sütununun ilk dolu hücresi "barkod" içeren bir başlıksa, başlıkları da yaz
        for r in range(1, ws.max_row + 1):
            deger = barkod_temizle(ws.cell(row=r, column=n).value)
            if deger:
                if "barkod" in deger.lower():
                    for k, metin in enumerate(("PTT Durumu", "Son İşlem Tarihi")):
                        hucre = ws.cell(row=r, column=hedef + k, value=metin)
                        hucre._style = copy(ws.cell(row=r, column=n)._style)
                break
        for s in satirlar:
            ws.cell(row=s[1], column=hedef, value=s[3] or s[7] or None)   # durum (yoksa hata/neden)
            ws.cell(row=s[1], column=hedef + 1, value=s[4] or None)        # tarih
        for k, genislik in enumerate((28, 17)):
            boyut = ws.column_dimensions[get_column_letter(hedef + k)]
            boyut.width = max(boyut.width or 0, genislik)
        try:
            wb.save(yol)
            return yol
        except PermissionError:
            alternatif = os.path.join(klasor, f"{kok}_PTT{uzanti}")
            wb.save(alternatif)
            print(f"UYARI: {ad} Excel'de açık, kaydedilemedi; sonuçlar şuraya yazıldı: {alternatif}")
            return alternatif
    finally:
        wb.close()


# ── PTT yanıtını çözme ───────────────────────────────────────────────────────
def _tarih_bicimle(ham):
    s = str(ham or "").strip()
    return f"{s[6:8]}/{s[4:6]}/{s[0:4]}" if len(s) == 8 and s.isdigit() else s


def _durum_ve_tarih(veri):
    if not veri.get("errorState"):
        return veri.get("errorMessage") or "Kayıt yok", ""
    son = veri.get("sondurum") or {}
    if son.get("son_durum_aciklama"):
        tarih = son.get("teslim_tarihi")
        if not tarih or str(tarih).strip() == "0":
            tarih = son.get("son_islem_tarihi")
        return son["son_durum_aciklama"], _tarih_bicimle(tarih)
    hareket = veri.get("hareketDongu")
    if isinstance(hareket, list) and hareket:
        h = hareket[-1]
        return (h.get("aciklama") or veri.get("errorMessage") or "Bilinmeyen durum",
                f"{h.get('tarih') or ''} {h.get('saat') or ''}".strip())
    return veri.get("errorMessage") or "Bilinmeyen durum", ""


def _hareket_metni(veri):
    satirlar = []
    for h in (veri.get("hareketDongu") or []):
        if isinstance(h, dict):
            parca = [str(h.get(k)) for k in ("tarih", "saat", "il", "aciklama") if h.get(k)]
            if parca:
                satirlar.append(" ".join(parca))
    return "\n".join(satirlar)


def sonuc_satiri(sira, excel_satiri, barkod, yanit=None, durum_metni="", hata=""):
    """`yanit` PTT'nin döndürdüğü listedir; hata dolu ise (yanıt alınamadı/geçersiz) o yazılır."""
    if hata:
        return [sira, excel_satiri, barkod, "", "", "", "", hata, "", ""]
    veri = yanit[0] if isinstance(yanit, list) and yanit and isinstance(yanit[0], dict) else {}
    if not veri:
        return [sira, excel_satiri, barkod, "", "", "", "", "Beklenmeyen yanıt biçimi", "", json.dumps(yanit, ensure_ascii=False)[:30000]]
    durum, tarih = _durum_ve_tarih(veri)
    kabul = veri.get("kabul") or {}
    sonuc = "Sorgulandı" if veri.get("errorState") else "PTT'de kayıt yok (elektronik tebligat olabilir)"
    return [sira, excel_satiri, barkod, durum, tarih, _tarih_bicimle(kabul.get("kabul_tarihi")),
            kabul.get("gonderici") or "", sonuc, _hareket_metni(veri),
            json.dumps(yanit, ensure_ascii=False)[:30000]]


# ── Playwright ───────────────────────────────────────────────────────────────
# Gerçek Chrome sürecini takip etmek için global referans (CDP modunda kullanılır)
_chrome_proc = None


def _chrome_exe_bul():
    """Sistemde kurulu Chrome yürütülebilir dosyasını arar."""
    adaylar = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ]
    return next((c for c in adaylar if os.path.exists(c)), None)


def tarayici_ac(p, headless):
    """Chrome'u gerçek bir kullanıcı tarayıcısı gibi başlatır ve Playwright'ı
    CDP (Chrome DevTools Protocol) üzerinden bağlar.

    NEDEN CDP?
    ----------
    Playwright normal başlatmada tarayıcıya kendi CDP kancalarını enjekte eder.
    Cloudflare Turnstile bu kancaları tespit eder ve isteği reddeder.
    CDP yaklaşımında Chrome tamamen bağımsız bir süreç olarak başlar
    (tıpkı kullanıcının çift tıklaması gibi); Playwright sadece var olan
    Chrome'a bağlanır. Böylece otomasyon izi minimumdur.

    Tarayıcı kimliği (UA, navigator.*, sec-ch-ua) HİÇ değiştirilmez: elle verilen
    eski bir UA, Chrome'un gönderdiği sec-ch-ua ile çelişip doğrulamanın
    reddetmesine yol açıyordu.

    Yedek: Chrome bulunamazsa veya CDP bağlantısı başarısız olursa,
    launch_persistent_context yöntemine düşer.
    """
    global _chrome_proc

    profil = os.path.join(KLASOR, "tarayici_profili")
    os.makedirs(profil, exist_ok=True)

    chrome_exe = _chrome_exe_bul()
    if chrome_exe:
        komut = [
            chrome_exe,
            "--remote-debugging-port=9222",
            f"--user-data-dir={profil}",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-service-autorun",
            "--password-store=basic",
            "--lang=tr-TR",
        ]
        if headless:
            komut.append("--headless=new")

        print(f"Chrome başlatılıyor: {chrome_exe}")
        try:
            _chrome_proc = subprocess.Popen(komut)
            # Chrome'un CDP portunu açmasını bekle (max 8 saniye)
            for _ in range(16):
                time.sleep(0.5)
                try:
                    browser = p.chromium.connect_over_cdp("http://localhost:9222")
                    # Bağlantı başarılı — mevcut context'i al ya da yeni aç
                    ctx = browser.contexts[0] if browser.contexts else browser.new_context(
                        locale="tr-TR", timezone_id="Europe/Istanbul")
                    print("✓ Gerçek Chrome'a CDP ile bağlandı (tarayıcı kimliği değiştirilmedi).")
                    return ctx
                except Exception:
                    continue
            # 8 saniye içinde bağlanamadı
            raise RuntimeError("CDP portu açılmadı (9222).")
        except Exception as e:
            print(f"(CDP başarısız: {e} — launch_persistent_context yöntemine düşülüyor)")
            if _chrome_proc:
                _chrome_proc.kill()
                _chrome_proc = None

    # ── Yedek: Playwright'ın kendi başlatması ────────────────────────────────
    print("(Chrome bulunamadı veya CDP başarısız; Playwright başlatması kullanılıyor)")
    ayarlar = dict(
        headless=headless,
        viewport={"width": 1366, "height": 800},
        locale="tr-TR",
        timezone_id="Europe/Istanbul",
        args=["--lang=tr-TR,tr"],
    )
    try:
        ctx = p.chromium.launch_persistent_context(profil, channel="chrome", **ayarlar)
    except PlaywrightError as e:
        print(f"(Chrome açılamadı, Chromium deneniyor: {str(e).splitlines()[0]})")
        ctx = p.chromium.launch_persistent_context(profil, **ayarlar)
    return ctx


def sayfayi_hazirla(page):
    """Ana sayfayı yükler, takip kutusunu ve SORGULA düğmesini döner."""
    page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60000)
    giris = page.locator(GIRIS_SECICI).first
    try:
        giris.wait_for(state="visible", timeout=30000)
    except PlaywrightTimeout:
        giris = page.locator(GIRIS_YEDEK_SECICI).first
        giris.wait_for(state="visible", timeout=15000)
    # Düğme, girişle aynı "inputWrap" kutusunun içinde (sayfa kaynağından).
    buton = giris.locator("xpath=..").get_by_role("button", name=re.compile("sorgula", re.I))
    return giris, buton


def barkod_sorgula(page, giris, buton, barkod, zaman_asimi_sn):
    """Rakamları tek tek yazıp SORGULA'ya basar; sayfanın kendi takip isteğinin JSON
    yanıtını döner. Yanıt gelmezse None (çağıran sayfadaki mesajı okur)."""
    giris.click()
    giris.fill("")
    giris.press_sequentially(barkod, delay=random.randint(60, 140))
    try:
        with page.expect_response(
                lambda r: TAKIP_YOLU in r.url and r.request.method == "POST",
                timeout=zaman_asimi_sn * 1000) as bilgi:
            buton.click()
    except PlaywrightTimeout:
        return None
    yanit = bilgi.value
    if yanit.status != 200:      # 403/429 gibi: gerçek "kayıt yok" değil, yanıt alınamadı say
        return None
    try:
        return yanit.json()
    except (ValueError, PlaywrightError):   # JSON olmayan (HTML) gövde
        return None


def sayfa_mesaji(page):
    """Sayfada 'Doğrulama tamamlanamadı' gibi bir mesaj var mı (yanıt gelmediğinde tanı için)."""
    try:
        return DOGRULAMA_HATASI if DOGRULAMA_HATASI in page.locator("body").inner_text(timeout=3000) else ""
    except PlaywrightError:
        return ""


def ekran_goruntusu(page, barkod):
    try:
        klasor = os.path.join(KLASOR, "hata_goruntuleri")
        os.makedirs(klasor, exist_ok=True)
        yol = os.path.join(klasor, f"{time.strftime('%Y%m%d_%H%M%S')}_{barkod}.png")
        page.screenshot(path=yol)
        return yol
    except PlaywrightError:
        return ""


def isle(excel_yolu, cikti_yolu=None, bekleme=3.0, headless=False, zaman_asimi=25,
         sutun=VARSAYILAN_SUTUN, girdiye_yaz_mi=True, yaz_sutunu=SONUC_SUTUNU):
    kayitlar = excel_oku(excel_yolu, sutun)
    if not kayitlar:
        raise ValueError(f"Excel'in aktif sayfasında {str(sutun).upper()} sütununda barkod bulunamadı.")
    print(f"{len(kayitlar)} barkod okundu ({str(sutun).upper()} sütunu).")

    cikti = cikti_yolu_uret(excel_yolu, cikti_yolu)
    satirlar, onbellek = [], {}
    ardisik_hata = 0
    durduruldu = ""

    def ara_kaydet():
        """Yarıda kesilirse sonuçlar kaybolmasın; Excel'de açıksa (kilit) sessizce geçer."""
        try:
            excel_yaz(satirlar, cikti)
        except OSError:
            pass

    with sync_playwright() as p:
        ctx = tarayici_ac(p, headless)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            giris, buton = sayfayi_hazirla(page)
            print("Tarayıcı hazır. (İlk seferde çerez bildirimini elle kapatabilirsiniz.)")
            for sira, (excel_satiri, barkod) in enumerate(kayitlar, start=1):
                etiket = f"[{sira}/{len(kayitlar)}] {barkod}"
                if not BARKOD_RE.fullmatch(barkod):
                    print(f"{etiket}: geçersiz barkod biçimi, atlandı.")
                    satirlar.append(sonuc_satiri(sira, excel_satiri, barkod, hata="Geçersiz barkod (atlandı)"))
                    continue
                if barkod in onbellek:
                    satirlar.append(sonuc_satiri(sira, excel_satiri, barkod, yanit=onbellek[barkod]))
                    print(f"{etiket}: tekrar eden barkod (yeniden sorulmadı).")
                    continue
                try:
                    yanit = barkod_sorgula(page, giris, buton, barkod, zaman_asimi)
                except PlaywrightError as e:
                    yanit, hata_metni = None, str(e).splitlines()[0]
                else:
                    hata_metni = ""

                if yanit is None:
                    ardisik_hata += 1
                    neden = sayfa_mesaji(page) or hata_metni or "yanıt gelmedi"
                    goruntu = ekran_goruntusu(page, barkod)
                    print(f"{etiket}: YANIT ALINAMADI ({neden})" + (f" — görüntü: {goruntu}" if goruntu else ""))
                    satirlar.append(sonuc_satiri(sira, excel_satiri, barkod, hata=f"Yanıt alınamadı: {neden}"))
                    if ardisik_hata >= ARDISIK_HATA_SINIRI:
                        durduruldu = (f"{ARDISIK_HATA_SINIRI} ardışık sorgu yanıtsız kaldı"
                                      + (" — PTT bot doğrulaması otomasyonu reddediyor olabilir" if neden == DOGRULAMA_HATASI else "")
                                      + ". Program durduruldu; o ana kadarki sonuçlar yazılıyor.")
                        break
                    yenile = True   # temiz başlangıç
                else:
                    ardisik_hata = 0
                    onbellek[barkod] = yanit
                    satir = sonuc_satiri(sira, excel_satiri, barkod, yanit=yanit)
                    satirlar.append(satir)
                    print(f"{etiket}: {satir[3] or satir[7]}" + (f" ({satir[4]})" if satir[4] else ""))
                    # sonuç penceresi açıldıysa → sayfayı tazele
                    yenile = (isinstance(yanit, list) and bool(yanit) and isinstance(yanit[0], dict)
                              and bool(yanit[0].get("errorState")))
                if sira % 10 == 0:
                    ara_kaydet()
                if yenile:
                    try:
                        giris, buton = sayfayi_hazirla(page)
                    except PlaywrightError as e:
                        durduruldu = f"Sayfa yeniden yüklenemedi ({str(e).splitlines()[0]}); o ana kadarki sonuçlar yazılıyor."
                        break
                if sira < len(kayitlar):
                    time.sleep(bekleme * random.uniform(0.7, 1.3))
        except KeyboardInterrupt:
            durduruldu = "Kullanıcı durdurdu; o ana kadarki sonuçlar yazılıyor."
        except Exception as e:  # noqa: BLE001 — beklenmedik hatada da toplananlar kaybolmasın
            durduruldu = f"Beklenmeyen hata ({type(e).__name__}: {e}); o ana kadarki sonuçlar yazılıyor."
        finally:
            try:
                ctx.close()
            except PlaywrightError:
                pass
            # CDP modunda başlatılan Chrome sürecini kapat
            if _chrome_proc is not None:
                try:
                    _chrome_proc.terminate()
                    _chrome_proc.wait(timeout=5)
                except Exception:
                    try:
                        _chrome_proc.kill()
                    except Exception:
                        pass

    if not satirlar:
        raise RuntimeError(durduruldu or "Hiç sonuç alınamadı.")
    excel_yaz(satirlar, cikti)
    if durduruldu:
        print(f"UYARI: {durduruldu}")
    print(f"Bitti: {len(satirlar)} satır. Çıktı: {cikti}")
    if girdiye_yaz_mi:
        try:
            yazilan = girdiye_yaz(excel_yolu, satirlar, sutun, yaz_sutunu)
            k = str(yaz_sutunu).upper()
            print(f"Girdi Excel'ine de yazıldı ({k} ve {get_column_letter(sutun_indeksi(yaz_sutunu) + 1)} sütunları): {yazilan}")
        except Exception as e:  # noqa: BLE001 — sonuç dosyası zaten kaydedildi
            print(f"UYARI: Sonuçlar girdi Excel'ine yazılamadı ({e}); sonuç dosyası kaydedildi.")
    return cikti


def excel_sec():
    import tkinter as tk
    from tkinter import filedialog
    kok = tk.Tk()
    kok.withdraw()
    kok.attributes("-topmost", True)
    yol = filedialog.askopenfilename(title="Barkod Excel'i seç",
                                     filetypes=[("Excel", "*.xlsx *.xlsm"), ("Tümü", "*.*")])
    kok.destroy()
    return yol


def main():
    ap = argparse.ArgumentParser(description="Excel'deki PTT barkodlarını Playwright ile sorgular.")
    ap.add_argument("excel", nargs="?", help="Barkod içeren .xlsx (verilmezse dosya seçme penceresi açılır)")
    ap.add_argument("--cikti", help="Çıktı .xlsx yolu (varsayılan: girdinin yanına)")
    ap.add_argument("--bekleme", type=float, default=3.0, help="Sorgular arası ortalama bekleme, sn (varsayılan 3)")
    ap.add_argument("--zaman-asimi", type=int, default=25, help="Bir sorgunun yanıt bekleme süresi, sn (varsayılan 25)")
    ap.add_argument("--sutun", default=VARSAYILAN_SUTUN,
                    help=f"Barkodların okunduğu Excel sütunu (varsayılan {VARSAYILAN_SUTUN})")
    ap.add_argument("--sonuc-sutunu", default=SONUC_SUTUNU,
                    help=f"Girdi Excel'inde durumun yazıldığı sütun; tarih bir sağına yazılır (varsayılan {SONUC_SUTUNU})")
    ap.add_argument("--girdiye-yazma", action="store_true", help="Sonuçları girdi Excel'ine yazma (yalnız ayrı sonuç dosyası)")
    ap.add_argument("--headless", action="store_true", help="Görünmez tarayıcı (bot doğrulamasına takılma ihtimali daha yüksek)")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    excel = args.excel or excel_sec()
    if not excel:
        print("Dosya seçilmedi.")
        return 1
    try:
        isle(excel, args.cikti, args.bekleme, args.headless, args.zaman_asimi, args.sutun,
             not args.girdiye_yazma, args.sonuc_sutunu)
    except Exception as e:  # noqa: BLE001
        print(f"HATA: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
