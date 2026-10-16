# -*- coding: utf-8 -*-
"""
Tüm Evrakı İndir — 127.0.0.1:8800'de açık dosyanın TÜM evrakı, PDF'e çevrilmiş olarak
=======================================================================================
UyapMasa/modules/evrak_indirici.py'nin CokluUyap (ofis ajanı) karşılığı. Farkları:
  • Async'tir; UYAP'a ofisin canlı oturumuyla (uyap_proxy.gw.client) gider — Playwright,
    Django, ayrı oturum YOK.
  • Dönüştürme yalnız PyMuPDF (fitz) ile: PDF birleştirme, TIFF/JPG/PNG, UDF (udf_pdf.py),
    HTML (fitz.Story) — light pakete Playwright/Pillow girmez.
  • Çıktı ofis bilgisayarına yazılır (KAYIT_KOKU/<ad>/…) ve ZIP'lenip tarayıcıya indirilir.

UYAP KURALLARINA UYUM (avukat.uyap.gov.tr main.js + kullanım koşulları, 2026-10-16):
  • Eşzamanlı istek YOK: UYAP tek oturumda aynı anda ikinci isteği reddediyor ("birden
    fazla sorgulama yapamazsınız"). Her istek batch_write() geçidinden geçer — kullanıcının
    kendi (anlık) isteklerine önce yol verir, UYAP'a asla iki istek birden gitmez — ve
    uyap_istek_izi() ile işaretlenir (sıra kapısı da bekler). Aynı anda tek toplu indirme.
  • Sabit, sistemi yormayan hız: ardışık iki UYAP isteği arasında en az ISTEK_ARASI_SN.
    Rastgele/insan-taklidi bekleme BİLEREK yok — amaç tespiti atlatmak değil, yükü düşük
    tutmak.
  • Captcha ATLATILMAZ: UYAP bir isteğe PRTL_GNL_100063(-1) ile "güvenlik doğrulaması"
    isterse indirme DURUR, durum "captcha" olur; doğrulamayı kullanıcı 8800'deki panelde
    kendisi çözer (Turnstile), jeton UYAP'ın kendi istemcisiyle AYNI biçimde ("Recaptcha"
    başlığı + alanı) aynı isteğe eklenip bir kez yeniden gönderilir. Otomatik çözüm yok.
"""

import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import time
import uuid
import zipfile

import httpx

from . import uyap_proxy
from ._runtime import batch_write, uyap_istek_izi

ISTEK_ARASI_SN = 1.0
ESZAMANLI_IMZA = "birden fazla sorgulama yapamaz"
ESZAMANLI_TEKRAR = 4                     # 3, 6, 9, 12 sn (UyapMasa dosya_core ile aynı tolerans)
ESZAMANLI_BEKLEME_SN = 3
# Bağlantı kopması (UYAP TLS'i zaman zaman sertçe kapatıyor: "[SSL] record layer failure",
# canlı 2026-10-16): yeni bağlantıyla artan beklemeyle tekrar; her kopmada o indirmenin
# istek aralığı iki katına çıkar (en çok YAVAS_EN_COK_SN) — sunucuya yüklenmek yerine geri çekil.
AG_BEKLEMELERI_SN = (2, 4, 8)
YAVAS_EN_COK_SN = 4.0
ARDISIK_AG_HATASI_SINIRI = 3             # art arda bu kadar evrak alınamazsa indirme durur

# UYAP istemcisindeki CAPTCHA_ERROR_CODES ve Turnstile site anahtarı (REACT_APP_CAPTCHA_TURU=2).
CAPTCHA_KODLARI = ("PRTL_GNL_100063", "PRTL_GNL_100063-1")
TURNSTILE_SITE_KEY = "0x4AAAAAAFDQm5c7ca4D-H_Y"
CAPTCHA_BEKLEME_SN = 15 * 60             # kullanıcı bu sürede çözmezse indirme iptal

ZIP_SAKLAMA_SN = 30 * 60                 # indirilmemiş ZIP'ler bu süre sonra silinir
EVRAK_LISTESI_TOPLAM = "listDosyaEvraklarPageTotal.ajx"
EVRAK_LISTESI = "list_dosya_evraklar.ajx"
DOC_VIEWER_HAZIRLIK = "getDocViewerParameters.ajx"
EVRAK_ICERIK = "view_document_brd.uyap"

KAYIT_KOKU = os.environ.get("UYAP_EVRAK_KLASORU") or os.path.join(
    os.path.expanduser("~"), "Documents", "CokluUyap Evrak")

# Durumlar
CALISIYOR = "calisiyor"
SIRADA = "sirada"
CAPTCHA = "captcha"
BITTI = "bitti"
HATA = "hata"
IPTAL = "iptal"

# Seçenekler: çıktı "tek" = yalnız birleşik PDF (doğrudan .pdf iner); "ikisi" = birleşik +
# ayrı ayrı PDF'ler (ZIP). Kapsam "tum" = tüm evrak (ofis listeyi UYAP'tan çeker); "secili" =
# kullanıcının UYAP kartındaki evrak ağacında işaretledikleri (liste tarayıcıdan gelir).
CIKTI_TEK, CIKTI_IKISI = "tek", "ikisi"
KAPSAM_TUM, KAPSAM_SECILI = "tum", "secili"
SECILI_EN_COK = 3000


class _Iptal(Exception):
    pass


class AgHatasi(Exception):
    """UYAP'a bağlantı, tüm yeniden denemelere rağmen kurulamadı/koptu."""


def _guvenli_ad(s, max_len=100):
    """Windows dosya adında yasak karakterleri temizler + uzun adları kırpar
    (evrak_indirici._guvenli_ad ile aynı)."""
    temiz = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "-", str(s or "")).strip().rstrip(". ")
    return temiz[:max_len] or "adsiz"


# ── İndirme kaydı ─────────────────────────────────────────────────────────────────────
class Indirme:
    def __init__(self, dosya_id, ad, cikti=CIKTI_IKISI, kapsam=KAPSAM_TUM, evraklar=None):
        self.id = uuid.uuid4().hex
        self.dosya_id = dosya_id
        self.ad = ad
        self.cikti = cikti
        self.kapsam = kapsam
        self.durum = SIRADA
        self.i = 0
        self.toplam = 0
        self.mesaj = "Sırada…"
        self.uyarilar = []
        self.klasor = None
        self.cikti_yol = None            # indirilecek dosya: birleşik .pdf ya da .zip
        self.hazir_evraklar = evraklar   # kapsam "secili": kartta işaretlenen evraklar
        self.captcha = None              # {"kod", "mesaj", "site_key"}
        self.olusturma = time.time()
        self.bitis = None
        self._iptal = False
        self._captcha_olay = asyncio.Event()
        self._captcha_jeton = None
        self._captcha_sayisi = 0
        self._yavaslatma = 1.0
        self._gorev = None

    def to_dict(self):
        return {
            "id": self.id, "durum": self.durum, "i": self.i, "toplam": self.toplam,
            "mesaj": self.mesaj, "uyarilar": self.uyarilar[-20:], "klasor": self.klasor,
            "cikti": self.cikti, "kapsam": self.kapsam,
            "dosya_hazir": bool(self.cikti_yol and os.path.exists(self.cikti_yol)),
            "captcha": self.captcha,
        }

    def iptal_et(self):
        self._iptal = True
        self._captcha_olay.set()

    def captcha_cevapla(self, jeton):
        if self.durum != CAPTCHA:
            return
        self._captcha_jeton = jeton or None
        # Durum hemen döner ki panel aynı doğrulamayı yeniden çizmesin.
        self.durum, self.captcha = CALISIYOR, None
        self.mesaj = "Doğrulama gönderildi, devam ediliyor…"
        self._captcha_olay.set()

    def aralik(self):
        return min(ISTEK_ARASI_SN * self._yavaslatma, max(YAVAS_EN_COK_SN, ISTEK_ARASI_SN))

    def _yavaslat(self):
        self._yavaslatma *= 2

    def _iptal_kontrol(self):
        if self._iptal:
            raise _Iptal()

    async def _captcha_bekle(self, kod, mesaj):
        """UYAP güvenlik doğrulaması istedi: dur, kullanıcının 8800'de çözmesini bekle."""
        onceki = self.mesaj
        self._captcha_sayisi += 1
        self.durum = CAPTCHA
        self.captcha = {"kod": kod, "site_key": TURNSTILE_SITE_KEY, "n": self._captcha_sayisi,
                        "mesaj": mesaj or "UYAP güvenlik doğrulaması istiyor."}
        self.mesaj = "UYAP güvenlik doğrulaması istiyor — lütfen doğrulamayı tamamlayın."
        self._captcha_olay.clear()
        self._captcha_jeton = None
        try:
            await asyncio.wait_for(self._captcha_olay.wait(), CAPTCHA_BEKLEME_SN)
        except asyncio.TimeoutError:
            self._iptal = True
        self._iptal_kontrol()
        self.durum = CALISIYOR
        self.captcha = None
        self.mesaj = onceki
        return self._captcha_jeton


_KAYITLAR = {}
_son_istek = 0.0
_sira = None                             # (loop, asyncio.Lock) — döngü değişirse yenilenir


def _sira_kilidi():
    global _sira
    loop = asyncio.get_running_loop()
    if _sira is None or _sira[0] is not loop:
        _sira = (loop, asyncio.Lock())
    return _sira[1]


# ── UYAP istekleri (kurallara uyumlu tek kapı) ────────────────────────────────────────
def _json_coz(resp):
    ctype = (resp.headers.get("content-type") or "").lower()
    if "json" not in ctype and not resp.content[:1] in (b"{", b"["):
        return None
    try:
        return json.loads(resp.content.decode("utf-8"))
    except Exception:
        return None


def _eszamanli_mi(veri, derinlik=0):
    if derinlik > 4:
        return False
    if isinstance(veri, str):
        return ESZAMANLI_IMZA in veri.lower()
    if isinstance(veri, dict):
        return any(_eszamanli_mi(v, derinlik + 1) for v in veri.values())
    if isinstance(veri, list):
        return any(_eszamanli_mi(v, derinlik + 1) for v in veri)
    return False


def _captcha_kodu(veri):
    if isinstance(veri, dict) and veri.get("errorCode") in CAPTCHA_KODLARI:
        return veri["errorCode"]
    return None


async def _hiz_siniri(ind):
    """Ardışık iki UYAP isteği arasında en az ind.aralik() (sabit, rastgele değil; yalnız
    bağlantı kopmalarından sonra büyür)."""
    bekle = ind.aralik() - (time.monotonic() - _son_istek)
    if bekle > 0:
        await asyncio.sleep(bekle)


async def _uyap(ind, method, path, *, govde=None, params=None):
    """Tek UYAP isteği: hız sınırı → batch_write (eşzamanlılık yok) → login duvarında bir kez
    yeniden giriş → 'eşzamanlı' reddinde artan beklemeyle tekrar → captcha'da kullanıcıyı
    bekleyip jetonla tekrar. (resp, json_veri|None) döner."""
    global _son_istek
    gw = uyap_proxy.gw
    if gw is None:
        raise RuntimeError("UYAP oturumu hazır değil.")
    url = f"{uyap_proxy.UYAP_BASE}/{path}"
    jeton = None
    while True:
        hdrs = {"Referer": f"{uyap_proxy.UYAP_BASE}/dosya-sorgulama",
                "Accept": "application/json, text/plain, */*"}
        g, p = govde, params
        if jeton:
            # UYAP istemcisiyle aynı: jeton hem başlıkta hem gövdede/parametrede.
            hdrs["Recaptcha"] = jeton
            if method == "GET":
                p = dict(p or {}, Recaptcha=jeton)
            else:
                g = dict(g or {}, Recaptcha=jeton)
        for deneme in range(ESZAMANLI_TEKRAR + 1):
            for ag_deneme in range(len(AG_BEKLEMELERI_SN) + 1):
                ind._iptal_kontrol()
                await _hiz_siniri(ind)
                try:
                    async with batch_write():
                        async with uyap_istek_izi():
                            gw.touch()
                            resp = await gw.client.request(method, url, params=p, json=g, headers=hdrs)
                            if uyap_proxy._looks_like_login_wall(resp):
                                await gw.relogin()
                                resp = await gw.client.request(method, url, params=p, json=g, headers=hdrs)
                    break
                except httpx.TransportError as e:
                    # Salt okuma istekleri: yeni bağlantıyla tekrar güvenli.
                    ind._yavaslat()
                    if ag_deneme == len(AG_BEKLEMELERI_SN):
                        raise AgHatasi(f"{type(e).__name__}: {e}") from e
                    await asyncio.sleep(AG_BEKLEMELERI_SN[ag_deneme])
                finally:
                    _son_istek = time.monotonic()
            veri = _json_coz(resp)
            if not _eszamanli_mi(veri):
                break
            if deneme < ESZAMANLI_TEKRAR:
                await asyncio.sleep(ESZAMANLI_BEKLEME_SN * (deneme + 1))
        kod = _captcha_kodu(veri)
        if not kod:
            return resp, veri
        jeton = await ind._captcha_bekle(kod, (veri or {}).get("error") or (veri or {}).get("message"))


async def _evrak_listesi(ind):
    _r, toplam = await _uyap(ind, "POST", EVRAK_LISTESI_TOPLAM,
                             govde={"dosyaId": ind.dosya_id, "pageNumber": 1})
    try:
        toplam_sayfa = max(int(toplam), 1)
    except (TypeError, ValueError):
        toplam_sayfa = 1
    tum = []
    for sayfa in range(1, toplam_sayfa + 1):
        _r, veri = await _uyap(ind, "POST", EVRAK_LISTESI,
                               govde={"dosyaId": ind.dosya_id, "pageNumber": sayfa})
        gruplar = veri.get("tumEvraklar") if isinstance(veri, dict) else None
        if not isinstance(gruplar, dict):
            continue
        for grup in gruplar.values():
            if isinstance(grup, list):
                tum.extend(e for e in grup if isinstance(e, dict))
    return _duzlestir(tum)


def _duzlestir(evraklar):
    """ekEvrakListesi alt evraklarını ayrı kayıt olarak katar (evrak_indirici ile aynı);
    aynı evrakId iki grupta görünürse bir kez alınır."""
    duz, gorulen = [], set()

    def ekle(e):
        if e.get("evrakId") and e["evrakId"] not in gorulen:
            gorulen.add(e["evrakId"])
            duz.append(e)

    for e in evraklar:
        ekle(e)
        for ek in (e.get("ekEvrakListesi") or []):
            if isinstance(ek, dict) and ek.get("evrakId"):
                ekle({"evrakId": ek["evrakId"],
                      "tur": f"{e.get('tur', '')} (ek)".strip(),
                      "birimEvrakNo": f"{e.get('birimEvrakNo', '')}-ek{ek.get('sira', '')}",
                      "onaylandigiTarih": e.get("onaylandigiTarih")})
    for sira, e in enumerate(duz, 1):
        e["_sira"] = sira                 # dosya adındaki numara: tam listedeki yeri
    return duz


# ── Dönüştürme (PyMuPDF) ──────────────────────────────────────────────────────────────
_GORSEL_IMZA = ((b"II*\x00", "tiff"), (b"MM\x00*", "tiff"), (b"\xff\xd8", "jpeg"),
                (b"\x89PNG", "png"))
_UZANTI = {"tiff": ".tiff", "jpeg": ".jpg", "png": ".png", "udf": ".udf", "zip": ".zip",
           "html": ".html"}


def _tur_bul(ctype, ham):
    if ham[:4] == b"%PDF":
        return "pdf"
    for imza, tur in _GORSEL_IMZA:
        if ham.startswith(imza):
            return tur
    if ham[:2] == b"PK":
        from . import udf_pdf
        return "udf" if udf_pdf.udf_zip_mi(ham) else "zip"
    bas = ham[:512].lstrip().lower()
    if "html" in ctype or bas.startswith((b"<!doctype", b"<html", b"<")):
        return "html"
    return "bilinmiyor"


def _html_metni(ham):
    for kod in ("utf-8", "cp1254"):
        try:
            metin = ham.decode(kod)
            break
        except UnicodeDecodeError:
            continue
    else:
        metin = ham.decode("utf-8", "replace")
    return re.sub(r"(?is)<script.*?</script>", "", metin)


def _html_pdf(ham):
    import fitz
    story = fitz.Story(html=_html_metni(ham))
    buf = io.BytesIO()
    yazici = fitz.DocumentWriter(buf)
    sayfa = fitz.paper_rect("a4")
    alan = sayfa + (40, 40, -40, -40)
    devam = 1
    while devam:
        aygit = yazici.begin_page(sayfa)
        devam, _ = story.place(alan)
        story.draw(aygit)
        yazici.end_page()
    yazici.close()
    return buf.getvalue()


def _pdfe_cevir(tur, ham):
    """(pdf_baytlari | None, hata_notu | None)."""
    import fitz
    try:
        if tur == "pdf":
            return ham, None
        if tur in ("tiff", "jpeg", "png"):
            with fitz.open(stream=ham, filetype=tur) as gorsel:
                return gorsel.convert_to_pdf(), None
        if tur == "udf":
            from . import udf_pdf
            return udf_pdf.udf_pdf_uret(ham), None
        if tur == "zip":
            # KEP eki (.eyp/.eml) — proxy görüntüleyicisiyle aynı çözücü.
            ctype, pdf = uyap_proxy._ek_zip_pdf_ac(EVRAK_ICERIK, "application/zip", ham)
            if ctype == "application/pdf":
                return pdf, None
            with zipfile.ZipFile(io.BytesIO(ham)) as z:
                html = next((i for i in z.infolist() if i.filename.lower().endswith((".html", ".htm"))), None)
                if html and html.file_size < 10 * 1024 * 1024:
                    return _html_pdf(z.read(html)), None
            return None, "zip içinde PDF/HTML yok"
        if tur == "html":
            return _html_pdf(ham), None
        return None, "tanınmayan biçim"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _isaret_sayfasi(ad, not_):
    """Dönüştürülemeyen evrak için birleşik PDF'e tek sayfalık işaret (uydurma render yok)."""
    import fitz
    doc = fitz.open()
    sayfa = doc.new_page()
    metin = (f"Bu evrak PDF'e otomatik dönüştürülemedi.\n\nEvrak: {ad}\nNeden: {not_}\n\n"
             "Orijinal dosya, kayıt klasöründeki 'Orijinal' alt klasöründe duruyor.")
    font = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf")
    kw = {"fontname": "arial", "fontfile": font} if os.path.exists(font) else {}
    sayfa.insert_textbox(fitz.Rect(50, 60, 545, 800), metin, fontsize=11, **kw)
    veri = doc.tobytes()
    doc.close()
    return veri


def _birlestir(klasor, ad, pdf_yollari):
    """Birleşik PDF'i yazar; sayfa yoksa None."""
    import fitz
    if not pdf_yollari:
        return None
    birlesik = fitz.open()
    try:
        for yol in pdf_yollari:
            try:
                with fitz.open(yol) as src:
                    birlesik.insert_pdf(src)
            except Exception:
                pass
        if not birlesik.page_count:
            return None
        yol = os.path.join(klasor, f"{ad} - Birlesik.pdf")
        birlesik.save(yol, garbage=3, deflate=True)
        return yol
    finally:
        birlesik.close()


def _zip_yap(klasor):
    zip_yol = klasor + ".zip"
    with zipfile.ZipFile(zip_yol, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for kok, _dizinler, dosyalar in os.walk(klasor):
            for f in dosyalar:
                tam = os.path.join(kok, f)
                z.write(tam, os.path.relpath(tam, os.path.dirname(klasor)))
    return zip_yol


def _parcalari_sil(yollar):
    for yol in yollar:
        try:
            os.remove(yol)
        except OSError:
            pass


def _benzersiz_klasor(ad):
    os.makedirs(KAYIT_KOKU, exist_ok=True)
    yol = os.path.join(KAYIT_KOKU, ad)
    n = 2
    while os.path.exists(yol) or os.path.exists(yol + ".zip"):
        yol = os.path.join(KAYIT_KOKU, f"{ad} ({n})")
        n += 1
    os.makedirs(yol)
    return yol


# ── Ana akış ──────────────────────────────────────────────────────────────────────────
async def _indir_ve_yaz(ind, evraklar):
    """Seçilen evrakı tek tek indirir/çevirir, kayıt klasörüne yazar, çıktıyı hazırlar."""
    ind.toplam = len(evraklar)
    ind.klasor = await asyncio.to_thread(_benzersiz_klasor, ind.ad)
    orijinal = os.path.join(ind.klasor, "Orijinal")
    yazilan, pdf_yollari, gorulen_pdf = [], [], set()
    ardisik_ag, yarida = 0, None

    for sira, evrak in enumerate(evraklar, 1):
        ind.i = sira
        ind.mesaj = f"{sira}/{ind.toplam} evrak indiriliyor…"
        no = evrak.get("birimEvrakNo") or ""
        govde_ad = _guvenli_ad(evrak.get("tur") or "evrak", 80)
        ad = f"{evrak.get('_sira', sira):03d} - {govde_ad}" + (f" ({_guvenli_ad(no, 30)})" if no else "")

        try:
            await _uyap(ind, "POST", DOC_VIEWER_HAZIRLIK, govde={})
            resp, veri = await _uyap(ind, "GET", EVRAK_ICERIK,
                                     params={"evrakId": evrak["evrakId"], "dosyaId": ind.dosya_id})
        except AgHatasi as e:
            ind.uyarilar.append(f"{ad}: bağlantı koptu, alınamadı ({e})")
            ardisik_ag += 1
            if ardisik_ag >= ARDISIK_AG_HATASI_SINIRI:
                yarida = (f"UYAP bağlantısı art arda {ardisik_ag} evrakta koptu; sunucuyu "
                          f"yormamak için durduruldu. {sira - ardisik_ag}/{ind.toplam} evrak "
                          "kaydedildi — kalanlar için bir süre sonra yeniden deneyin.")
                break
            continue
        ardisik_ag = 0
        if resp.status_code >= 400 or (isinstance(veri, dict) and (veri.get("errorCode") or veri.get("error"))):
            neden = (veri or {}).get("error") if isinstance(veri, dict) else None
            ind.uyarilar.append(f"{ad}: indirilemedi ({neden or 'HTTP ' + str(resp.status_code)})")
            continue

        ham = resp.content
        tur = _tur_bul((resp.headers.get("content-type") or "").lower(), ham)
        pdf, not_ = await asyncio.to_thread(_pdfe_cevir, tur, ham)

        def yaz(pdf=pdf, not_=not_, tur=tur, ham=ham, ad=ad):
            if tur != "pdf":
                os.makedirs(orijinal, exist_ok=True)
                with open(os.path.join(orijinal, ad + _UZANTI.get(tur, ".bin")), "wb") as f:
                    f.write(ham)
            yol = os.path.join(ind.klasor, ad + ".pdf")
            with open(yol, "wb") as f:
                f.write(pdf if pdf else _isaret_sayfasi(ad, not_))
            return yol

        yol = await asyncio.to_thread(yaz)
        yazilan.append(yol)
        if pdf:
            ozet = hashlib.sha256(pdf).digest()
            if ozet in gorulen_pdf:
                continue                  # aynı içerik ikinci kez birleşiğe girmesin
            gorulen_pdf.add(ozet)
        else:
            ind.uyarilar.append(f"{ad}: PDF'e çevrilemedi ({not_}), orijinali kaydedildi")
        pdf_yollari.append(yol)

    ind.mesaj = "Birleşik PDF hazırlanıyor…"
    birlesik = await asyncio.to_thread(_birlestir, ind.klasor, ind.ad, pdf_yollari)
    if ind.cikti == CIKTI_TEK:
        await asyncio.to_thread(_parcalari_sil, yazilan)   # kayıtta yalnız birleşik kalır
        ind.cikti_yol = birlesik
    else:
        ind.mesaj = "ZIP hazırlanıyor…"
        ind.cikti_yol = await asyncio.to_thread(_zip_yap, ind.klasor)
    if not birlesik:
        ind.durum, ind.mesaj = HATA, (yarida or "Hiçbir evrak alınamadı.") + f" Kayıt: {ind.klasor}"
    elif yarida:
        ind.durum, ind.mesaj = HATA, yarida + f" Kayıt: {ind.klasor}"
    else:
        ind.durum = BITTI
        ind.mesaj = f"Tamamlandı: {ind.toplam} evrak. Kayıt: {ind.klasor}"


async def _calistir(ind):
    try:
        async with _sira_kilidi():
            ind.durum = CALISIYOR
            evraklar = ind.hazir_evraklar
            if evraklar is None:
                ind.mesaj = "Evrak listesi alınıyor…"
                evraklar = await _evrak_listesi(ind)
            if not evraklar:
                ind.durum, ind.mesaj = HATA, "Bu dosyada evrak bulunamadı."
                return
            await _indir_ve_yaz(ind, evraklar)
    except _Iptal as e:
        ind.durum, ind.mesaj = IPTAL, (str(e) or "İndirme durduruldu.")
    except AgHatasi as e:
        ind.durum, ind.mesaj = HATA, (f"UYAP bağlantısı tekrar tekrar koptu ({e}). "
                                      "Bir süre sonra yeniden deneyin.")
    except Exception as e:
        ind.durum, ind.mesaj = HATA, f"Hata: {type(e).__name__}: {e}"
    finally:
        ind.captcha = None
        ind.liste = None
        ind.bitis = time.time()


def _temizle():
    """Süresi geçen kayıtları unutur ve ZIP'lerini siler (klasör/PDF ofiste kalır)."""
    simdi = time.time()
    for k, ind in list(_KAYITLAR.items()):
        if ind.bitis and simdi - ind.bitis > ZIP_SAKLAMA_SN:
            if ind.cikti_yol and ind.cikti_yol.endswith(".zip"):
                try:
                    os.remove(ind.cikti_yol)
                except OSError:
                    pass
            _KAYITLAR.pop(k, None)


_AKTIF = (SIRADA, CALISIYOR, CAPTCHA)


def _secili_evraklar(evraklar):
    """Tarayıcıdan (UYAP kartının evrak ağacından) gelen seçimi doğrular; sıra korunur,
    aynı evrakId bir kez alınır. Yalnız indirme için gereken alanlar tutulur."""
    if not isinstance(evraklar, list) or not evraklar:
        raise ValueError("İşaretli belge yok.")
    if len(evraklar) > SECILI_EN_COK:
        raise ValueError("Çok fazla belge işaretlendi.")
    temiz, gorulen = [], set()
    for e in evraklar:
        if not isinstance(e, dict):
            raise ValueError("Geçersiz belge kaydı.")
        eid = e.get("evrakId")
        if not isinstance(eid, str) or not eid or len(eid) > 400 or any(c in eid for c in "\r\n"):
            raise ValueError("Geçersiz evrakId.")
        if eid in gorulen:
            continue
        gorulen.add(eid)
        temiz.append({"evrakId": eid,
                      "tur": str(e.get("tur") or "evrak")[:150],
                      "birimEvrakNo": str(e.get("birimEvrakNo") or "")[:60],
                      "onaylandigiTarih": str(e.get("onaylandigiTarih") or "")[:30],
                      "_sira": len(temiz) + 1})
    return temiz


def baslat(dosya_id, ad, cikti=CIKTI_IKISI, kapsam=KAPSAM_TUM, evraklar=None):
    _temizle()
    dosya_id = str(dosya_id or "").strip()
    if not dosya_id or len(dosya_id) > 400 or any(c in dosya_id for c in "\r\n"):
        raise ValueError("Geçersiz dosyaId.")
    if cikti not in (CIKTI_TEK, CIKTI_IKISI) or kapsam not in (KAPSAM_TUM, KAPSAM_SECILI):
        raise ValueError("Geçersiz seçenek.")
    secili = _secili_evraklar(evraklar) if kapsam == KAPSAM_SECILI else None
    for ind in _KAYITLAR.values():                # aynı dosya zaten indiriliyorsa onu ver
        if ind.dosya_id == dosya_id and ind.durum in _AKTIF:
            return ind
    ad = _guvenli_ad(ad or "", 80)
    if ad == "adsiz":
        ad = "UYAP Evrak " + time.strftime("%Y-%m-%d %H.%M")
    if kapsam == KAPSAM_SECILI:
        ad = _guvenli_ad(ad + " - Secili", 90)
    ind = Indirme(dosya_id, ad, cikti, kapsam, secili)
    _KAYITLAR[ind.id] = ind
    ind._gorev = asyncio.get_running_loop().create_task(_calistir(ind))
    return ind


def getir(ind_id):
    return _KAYITLAR.get(ind_id)


def dosya_oku(ind):
    """(bayt, dosya adı, içerik türü). Dosya hemen silinmez (indirme yarıda kalırsa yeniden
    istenebilsin); ZIP'i _temizle ZIP_SAKLAMA_SN sonra siler, PDF ofisteki kayıttır."""
    with open(ind.cikti_yol, "rb") as f:
        veri = f.read()
    tur = "application/pdf" if ind.cikti_yol.endswith(".pdf") else "application/zip"
    return veri, os.path.basename(ind.cikti_yol), tur
