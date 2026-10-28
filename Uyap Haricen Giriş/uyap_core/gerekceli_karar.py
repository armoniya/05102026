# -*- coding: utf-8 -*-
"""
Gerekçeli Kararlar (GEÇİCİ modül) — hukuk dosyalarında gerekçeli kararı bulup UDF + PDF indirir
==============================================================================================
İki aşama:
  1. Liste: Hukuk yargı türünün (yargiTuru=1) tüm mahkeme türleri × Açık/Kapalı için dosyalar
     UYAP'tan listelenir; kullanıcı panelde mahkeme türü / dosya işaretler.
  2. Tarama: işaretli her dosyanın evrak listesi ve tarafları alınır, türü "Gerekçeli Karar"
     olan evrak orijinal biçimiyle (.udf) ve PDF olarak ofise yazılır. Bitince taranan dosyaların
     Excel listesi (birim, no, tür, durum, açılış tarihi, taraflar, karar) ve kararlar tek ZIP'te iner.

UYAP'A SAYGI (evrak_toplu.py ile aynı kapıdan, aynı ilkelerle):
  • Her istek evrak_toplu._uyap'tan geçer: eşzamanlı istek yok, kullanıcının kendi işlemlerine
    öncelik (batch_write), bağlantı koparsa geri çekilme, captcha'yı KULLANICI çözer.
  • Sabit, yavaş hız: iki istek arası ISTEK_ARASI_SN; her MOLA_HER_DOSYA dosyada MOLA_SN mola.
    Rastgele/insan-taklidi bekleme BİLEREK yok — amaç yükü düşürmek, tespit atlatmak değil.
  • Tüm Evrakı İndir ile aynı sıra kilidi: iki toplu iş UYAP'a aynı anda gitmez.
  • Kaldığı yer (_durum.json): indirilmiş karar yeniden indirilmez; kararı olmayan dosya
    YENIDEN_KONTROL_SN geçmeden yeniden sorulmaz.
"""

import asyncio
import io
import json
import os
import re
import time
import zipfile
from xml.sax.saxutils import escape

from . import evrak_toplu
from .evrak_toplu import (AgHatasi, _Iptal, _uyap, _guvenli_ad, _tur_bul, _pdfe_cevir, _UZANTI,
                          DOC_VIEWER_HAZIRLIK, EVRAK_ICERIK, CALISIYOR, SIRADA, CAPTCHA, BITTI,
                          HATA, IPTAL)

ISTEK_ARASI_SN = 5.0
YAVAS_EN_COK_SN = 20.0
MOLA_HER_DOSYA = 25
MOLA_SN = 60
YENIDEN_KONTROL_SN = 7 * 86400
ARDISIK_AG_HATASI_SINIRI = 3
ZIP_SAKLAMA_SN = 30 * 60

HUKUK = "1"
DURUMLAR = ((0, "Açık"), (1, "Kapalı"))
BIRIM_TURLERI = "yargiBirimleriSorgula_brd.ajx"
ARAMA = "search_phrase_detayli.ajx"
# [{"adi", "rol": "Davacı"|"Davalı"|…, "vekil": "[AD SOYAD, …]" (yoksa anahtar yok), "kisiKurum"}]
# — bkz. UyapMasa/modules/dosya_core.py TARAF_BILGILERI_ENDPOINT (Hukuk'ta canlı doğrulandı).
TARAF_BILGILERI = "dosya_taraf_bilgileri_brd.ajx"
SAYFA_BOYU = 500
SAYFA_EN_COK = 40

KOK = os.path.join(evrak_toplu.KAYIT_KOKU, "Gerekçeli Kararlar")

SECIM = "secim"            # liste hazır, kullanıcının taranacak dosyaları işaretlemesi bekleniyor
KARAR_SECIM = "karar_secim"   # kontrol bitti, kullanıcının indirilecekleri işaretlemesi bekleniyor
_SON = (BITTI, HATA, IPTAL)
_BEKLEYEN = (SECIM, KARAR_SECIM)

_HARF = str.maketrans("İIÇĞÖŞÜÂÎÛâîû", "iıçğöşüaiuaiu")
_DISLA = ("tebliğ", "teblig", "mazbata", "zarf", "şerh", "serh")


def tr_kucuk(s):
    return str(s or "").translate(_HARF).lower()


def gerekceli_mi(tur):
    """Evrak türü gerekçeli kararın kendisi mi? (tebliğ mazbatası, zarf, şerh vb. değil)"""
    t = tr_kucuk(tur)
    return ("gerekçeli karar" in t or "gerekceli karar" in t) and not any(d in t for d in _DISLA)


def _kayitlar(veri):
    """search_phrase_detayli yanıtı → kayıt listesi (icra_core.parse_records ile aynı biçim:
    [kayıtlar, toplam] ya da düz liste)."""
    if isinstance(veri, dict):
        for k in ("data", "veri", "list", "rows", "dosyalar"):
            if isinstance(veri.get(k), list):
                veri = veri[k]
                break
        else:
            return []
    if not isinstance(veri, list):
        return []
    if veri and isinstance(veri[0], list):
        veri = veri[0]
    return [r for r in veri if isinstance(r, dict)]


def _tarih(val):
    """UYAP tarih alanı ({"date": {"day","month","year"}} ya da metin) → GG.AA.YYYY
    (icra_core.kolon_degeri("acilisTarihi") ile aynı)."""
    if isinstance(val, dict):
        t = val.get("date") or {}
        try:
            return f"{int(t['day']):02d}.{int(t['month']):02d}.{int(t['year']):04d}"
        except (KeyError, TypeError, ValueError):
            return ""
    return str(val or "")


def _anahtar(d):
    """dosyaId oturuma bağlı (her girişte değişir): kalıcı kayıt birim + dosya no ile."""
    return f"{d.get('birim_id') or d.get('birim')}|{d.get('no')}"


async def _uyu(sn):
    await asyncio.sleep(sn)


# ── Kalıcı durum (_durum.json) ─────────────────────────────────────────────────────────
def _durum_yolu():
    return os.path.join(KOK, "_durum.json")


def _durum_oku():
    try:
        with open(_durum_yolu(), encoding="utf-8") as f:
            veri = json.load(f)
        return veri if isinstance(veri, dict) else {}
    except (OSError, ValueError):
        return {}


def _durum_yaz(durum):
    os.makedirs(KOK, exist_ok=True)
    gecici = _durum_yolu() + ".tmp"
    with open(gecici, "w", encoding="utf-8") as f:
        json.dump(durum, f, ensure_ascii=False)
    os.replace(gecici, _durum_yolu())


# ── Tarama kaydı ───────────────────────────────────────────────────────────────────────
class Tarama(evrak_toplu.Indirme):
    """evrak_toplu.Indirme'nin captcha/iptal/yavaşlatma davranışını aynen kullanır; yalnız
    hız (5 sn) ve akış farklı: liste → (dosya seçimi) → kontrol → (karar seçimi) → indirme."""

    def __init__(self, durumlar=(0, 1)):
        super().__init__("", "Gerekçeli Kararlar")
        self.durumlar = [(kod, ad) for kod, ad in DURUMLAR if kod in durumlar]
        self.asama = "liste"
        self.mesaj = "Hukuk dosyaları listeleniyor…"
        self.turler = []                 # [{"kod", "ad"}]
        self.dosyalar = {}               # dosyaId → {"id","no","birim","birim_id","tur","durum",…}
        self.sonuclar = {}               # kontrol edilen dosyaId → {"sonuc","kararlar","taraflar"}
        self.bulunan = 0                 # kontrolde bulunan karar
        self.yeni = 0                    # bu taramada indirilen karar
        self.atlanan = 0                 # yakın zamanda kontrol edilip kararı olmayan
        self.mola_bitis = None
        self.cikti = []                  # ZIP'e girecek dosya yolları
        self.excel_yol = None

    def aralik(self):
        return min(ISTEK_ARASI_SN * self._yavaslatma, YAVAS_EN_COK_SN)

    def kalan_sn(self):
        if self.asama not in ("kontrol", "indirme") or self.durum in _SON:
            return None
        kalan = max(self.toplam - self.i, 0)
        # kontrol: dosya başına evrak listesi (2) + taraflar (1); indirme: karar başına 2 istek.
        istek = 3 if self.asama == "kontrol" else 2
        return int(kalan * istek * self.aralik() + (kalan // MOLA_HER_DOSYA) * MOLA_SN)

    def to_dict(self):
        return {
            "id": self.id, "durum": self.durum, "asama": self.asama, "mesaj": self.mesaj,
            "i": self.i, "toplam": self.toplam, "bulunan": self.bulunan, "yeni": self.yeni,
            "atlanan": self.atlanan, "dosya_sayisi": len(self.dosyalar),
            "kontrol_edilen": len(self.sonuclar),
            "karar_dosyasi": sum(1 for s in self.sonuclar.values() if s["kararlar"]),
            "durumlar": [ad for _kod, ad in self.durumlar],
            "kalan_sn": self.kalan_sn(), "aralik_sn": self.aralik(),
            "mola_kalan_sn": max(0, int(self.mola_bitis - time.time())) if self.mola_bitis else None,
            "uyarilar": self.uyarilar[-20:], "klasor": KOK,
            "dosya_hazir": bool(self.cikti_yol and os.path.exists(self.cikti_yol)),
            "excel_hazir": bool(self.excel_yol and os.path.exists(self.excel_yol)),
            "captcha": self.captcha,
        }

    def liste_dict(self):
        return {"turler": self.turler, "dosyalar": list(self.dosyalar.values())}

    def sonuclar_dict(self):
        """Kontrol sonucu: kararı bulunan dosyalar (indirme seçimi için) + diğerlerinin sayısı."""
        var = []
        for did, s in self.sonuclar.items():
            if s["kararlar"]:
                var.append(dict(self.dosyalar[did], sonuc=s["sonuc"],
                                kararlar=[{"ad": k["ad"], "inmis": bool(k["mevcut"])} for k in s["kararlar"]]))
        return {"dosyalar": var,
                "karar_yok": sum(1 for s in self.sonuclar.values()
                                 if not s["kararlar"] and s["sonuc"].startswith("Yok")),
                "kontrol_edilemedi": sum(1 for s in self.sonuclar.values()
                                         if s["sonuc"].startswith("Kontrol edilemedi"))}


_KAYITLAR = {}


# ── 1. aşama: liste ────────────────────────────────────────────────────────────────────
async def _mahkeme_turleri(t):
    _r, veri = await _uyap(t, "POST", BIRIM_TURLERI, govde={"yargiTuru": HUKUK})
    return [{"kod": str(b["tablo"]), "ad": str(b.get("kod") or b["tablo"])}
            for b in (veri if isinstance(veri, list) else [])
            if isinstance(b, dict) and b.get("tablo")]


async def _ara(t, tur, durum_kod):
    tum = []
    for sayfa in range(1, SAYFA_EN_COK + 1):
        _r, veri = await _uyap(t, "POST", ARAMA, govde={
            "dosyaDurumKod": durum_kod, "pageSize": SAYFA_BOYU, "pageNumber": sayfa,
            "birimTuru2": tur["kod"], "birimTuru3": HUKUK})
        if isinstance(veri, dict) and (veri.get("errorCode") or veri.get("error")):
            t.uyarilar.append(f"{tur['ad']}: {veri.get('error') or veri.get('errorCode')}")
            break
        parca = _kayitlar(veri)
        tum.extend(parca)
        if len(parca) < SAYFA_BOYU:
            break
    return tum


async def _listele(t):
    t.turler = await _mahkeme_turleri(t)
    if not t.turler:
        raise RuntimeError("UYAP hukuk mahkemesi türlerini döndürmedi.")
    for n, tur in enumerate(t.turler, 1):
        for kod, durum_ad in t.durumlar:
            t.mesaj = f"Listeleniyor: {tur['ad']} ({durum_ad}) — {n}/{len(t.turler)} tür, {len(t.dosyalar)} dosya"
            for r in await _ara(t, tur, kod):
                did = str(r.get("dosyaId") or "")
                if not did or did in t.dosyalar:
                    continue
                t.dosyalar[did] = {
                    "id": did, "no": str(r.get("dosyaNo") or ""),
                    "birim": str(r.get("birimAdi") or ""), "birim_id": str(r.get("birimId") or ""),
                    # "Açık" aramasında Karara Çıkmış / İstinafta dosyalar da gelir: UYAP'ın
                    # kayıttaki kendi durum metni gösterilir (arama: hangi aramadan geldi).
                    "tur": tur["ad"], "tur_kod": tur["kod"], "arama": durum_ad,
                    "durum": str(r.get("dosyaDurum") or durum_ad),
                    "dosya_tur": str(r.get("dosyaTur") or ""),
                    "acilis": _tarih(r.get("dosyaAcilisTarihi") or r.get("acilisTarihi")),
                }
    t.durum, t.asama = SECIM, "secim"
    t.mesaj = f"{len(t.dosyalar)} hukuk dosyası bulundu. İşaretleyip taramayı başlatın."


# ── 2. aşama: tarama ───────────────────────────────────────────────────────────────────
async def _mola(t):
    t.mola_bitis = time.time() + MOLA_SN
    onceki = t.mesaj
    t.mesaj = f"Mola: UYAP'ı yormamak için {MOLA_SN} sn bekleniyor…"
    try:
        for _ in range(MOLA_SN):
            t._iptal_kontrol()
            await _uyu(1)
    finally:
        t.mola_bitis = None
        t.mesaj = onceki


def _yaz(yol, veri):
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    with open(yol, "wb") as f:
        f.write(veri)


async def _karar_indir(t, d, evrak, klasor, ad):
    """Tek kararı indirir; yazılan dosya yollarını döndürür (alınamazsa [])."""
    await _uyap(t, "POST", DOC_VIEWER_HAZIRLIK, govde={})
    resp, veri = await _uyap(t, "GET", EVRAK_ICERIK,
                             params={"evrakId": evrak["evrakId"], "dosyaId": d["id"]})
    if resp.status_code >= 400 or (isinstance(veri, dict) and (veri.get("errorCode") or veri.get("error"))):
        neden = veri.get("error") if isinstance(veri, dict) else None
        t.uyarilar.append(f"{d['birim']} {d['no']} — {ad}: alınamadı ({neden or 'HTTP ' + str(resp.status_code)})")
        return []
    ham = resp.content
    tur = _tur_bul((resp.headers.get("content-type") or "").lower(), ham)
    pdf, not_ = await asyncio.to_thread(_pdfe_cevir, tur, ham)
    yollar = []
    if tur != "pdf":
        yol = os.path.join(klasor, ad + _UZANTI.get(tur, ".bin"))
        await asyncio.to_thread(_yaz, yol, ham)
        yollar.append(yol)
    if pdf:
        yol = os.path.join(klasor, ad + ".pdf")
        await asyncio.to_thread(_yaz, yol, pdf)
        yollar.append(yol)
    else:
        t.uyarilar.append(f"{d['birim']} {d['no']} — {ad}: PDF'e çevrilemedi ({not_}), orijinali kaydedildi")
    return yollar


async def _taraflar(t, d, kayit):
    """Dosyanın tarafları; önceki taramadan _durum.json'da varsa UYAP'a yeniden sorulmaz.
    Alınamazsa None (Excel'de "alınamadı" yazar, önbelleğe girmez)."""
    if isinstance(kayit.get("taraflar"), list):
        return kayit["taraflar"]
    try:
        _r, veri = await _uyap(t, "POST", TARAF_BILGILERI, govde={"dosyaId": d["id"]})
    except AgHatasi as e:
        veri = None
        t.uyarilar.append(f"{d['birim']} {d['no']}: taraflar alınamadı ({e})")
    if not isinstance(veri, list):
        return None
    return [{"rol": str(x.get("rol") or "").strip(), "ad": str(x.get("adi") or "").strip(),
             "vekil": str(x.get("vekil") or "").strip("[] ")}
            for x in veri if isinstance(x, dict)]


def _kaydet(durum, anahtar, karar_yok, taraflar):
    kayit = {"zaman": time.time(), "karar_yok": karar_yok}
    if taraflar is not None:
        kayit["taraflar"] = taraflar
    durum[anahtar] = kayit


def _klasor(d):
    return os.path.join(KOK, _guvenli_ad(f"{d['birim']} {d['no'].replace('/', '-')}", 120))


def _karar_bilgileri(d, kararlar):
    """Bulunan kararların kayıt adı + ofiste zaten inmiş dosyaları (yeniden indirilmez)."""
    klasor = _klasor(d)
    sonuc = []
    for n, e in enumerate(kararlar, 1):
        no = e.get("birimEvrakNo") or ""
        ad = _guvenli_ad(e.get("tur") or "Gerekçeli Karar", 80) + (
            f" ({_guvenli_ad(no, 30)})" if no else (f" ({n})" if len(kararlar) > 1 else ""))
        sonuc.append({"evrakId": e["evrakId"], "tur": str(e.get("tur") or ""), "ad": ad,
                      "mevcut": [os.path.join(klasor, ad + u) for u in (".udf", ".pdf")
                                 if os.path.exists(os.path.join(klasor, ad + u))]})
    return sonuc


async def _kontrol(t, secilen):
    """İşaretli dosyalarda yalnız KONTROL: evrak listesi + taraflar. Hiçbir şey indirmez;
    sonuç t.sonuclar'a yazılır, indirilecekleri kullanıcı sonra seçer."""
    t.asama, t.toplam, t.i = "kontrol", len(secilen), 0
    durum = await asyncio.to_thread(_durum_oku)
    ardisik_ag = 0
    for sira, d in enumerate(secilen, 1):
        t.i = sira
        if sira > 1 and (sira - 1) % MOLA_HER_DOSYA == 0:
            await _mola(t)
        anahtar = _anahtar(d)
        kayit = durum.get(anahtar) or {}
        t.dosya_id = d["id"]
        if kayit.get("karar_yok") and time.time() - kayit.get("zaman", 0) < YENIDEN_KONTROL_SN:
            t.atlanan += 1
            taraflar = await _taraflar(t, d, kayit)
            if taraflar is not None and "taraflar" not in kayit:
                kayit["taraflar"] = taraflar
                await asyncio.to_thread(_durum_yaz, durum)
            t.sonuclar[d["id"]] = {"sonuc": "Yok (yakın zamanda kontrol edildi)", "kararlar": [],
                                   "taraflar": taraflar}
            continue
        t.mesaj = f"{sira}/{t.toplam}: {d['birim']} {d['no']} kontrol ediliyor…"
        try:
            evraklar = await evrak_toplu._evrak_listesi(t)
        except AgHatasi as e:
            t.uyarilar.append(f"{d['birim']} {d['no']}: bağlantı koptu ({e})")
            t.sonuclar[d["id"]] = {"sonuc": "Kontrol edilemedi (bağlantı)", "kararlar": [],
                                   "taraflar": kayit.get("taraflar")}
            ardisik_ag += 1
            if ardisik_ag >= ARDISIK_AG_HATASI_SINIRI:
                raise AgHatasi(f"art arda {ardisik_ag} dosyada bağlantı koptu")
            continue
        ardisik_ag = 0
        taraflar = await _taraflar(t, d, kayit)
        kararlar = _karar_bilgileri(d, [e for e in evraklar if gerekceli_mi(e.get("tur"))])
        _kaydet(durum, anahtar, not kararlar, taraflar)
        await asyncio.to_thread(_durum_yaz, durum)
        t.bulunan += len(kararlar)
        t.sonuclar[d["id"]] = {"sonuc": f"Var ({len(kararlar)})" if kararlar else "Yok",
                               "kararlar": kararlar, "taraflar": taraflar}


async def _indir(t, dosya_idleri):
    """Kullanıcının seçtiği dosyalardaki kararları indirir (ofiste olanlar atlanır)."""
    isler = [(did, k) for did in dosya_idleri for k in t.sonuclar[did]["kararlar"]]
    t.asama, t.toplam, t.i = "indirme", len(isler), 0
    for did in dosya_idleri:
        t.sonuclar[did]["indirilecek"] = True
    for sira, (did, k) in enumerate(isler, 1):
        t.i = sira
        if sira > 1 and (sira - 1) % MOLA_HER_DOSYA == 0:
            await _mola(t)
        d = t.dosyalar[did]
        yollar = [y for y in k["mevcut"] if os.path.exists(y)]
        if not yollar:
            t.mesaj = f"{sira}/{t.toplam}: {d['birim']} {d['no']} — gerekçeli karar indiriliyor…"
            t.dosya_id = did
            try:
                yollar = await _karar_indir(t, d, k, _klasor(d), k["ad"])
            except AgHatasi as e:
                t.uyarilar.append(f"{d['birim']} {d['no']} — {k['ad']}: bağlantı koptu ({e})")
                yollar = []
            if yollar:
                t.yeni += 1
        k["yollar"] = yollar
        t.cikti.extend(yollar)


# ── Excel (.xlsx): openpyxl light pakete girmediği için en küçük geçerli biçim elle ─────
_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
_RNS = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
_PKG = "http://schemas.openxmlformats.org/package/2006"
_OD = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_SML = "application/vnd.openxmlformats-officedocument.spreadsheetml"
_XML_BAS = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_YASAK_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
_XLSX_SABIT = {
    "[Content_Types].xml": (
        f'{_XML_BAS}<Types xmlns="{_PKG}/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="{_SML}.sheet.main+xml"/>'
        f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{_SML}.worksheet+xml"/>'
        f'<Override PartName="/xl/styles.xml" ContentType="{_SML}.styles+xml"/></Types>'),
    "_rels/.rels": (
        f'{_XML_BAS}<Relationships xmlns="{_PKG}/relationships">'
        f'<Relationship Id="rId1" Type="{_OD}/officeDocument" Target="xl/workbook.xml"/></Relationships>'),
    "xl/workbook.xml": (
        f'{_XML_BAS}<workbook {_NS} {_RNS}>'
        '<sheets><sheet name="Hukuk Dosyaları" sheetId="1" r:id="rId1"/></sheets></workbook>'),
    "xl/_rels/workbook.xml.rels": (
        f'{_XML_BAS}<Relationships xmlns="{_PKG}/relationships">'
        f'<Relationship Id="rId1" Type="{_OD}/worksheet" Target="worksheets/sheet1.xml"/>'
        f'<Relationship Id="rId2" Type="{_OD}/styles" Target="styles.xml"/></Relationships>'),
    "xl/styles.xml": (            # stil 0: düz, stil 1: kalın (başlık satırı)
        f'{_XML_BAS}<styleSheet {_NS}>'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'),
}


def _sutun(i):
    """0 → A, 25 → Z, 26 → AA."""
    ad = ""
    i += 1
    while i:
        i, k = divmod(i - 1, 26)
        ad = chr(65 + k) + ad
    return ad


def _xlsx(basliklar, satirlar, genislikler):
    """Tek sayfalık .xlsx (metin hücreleri, kalın + dondurulmuş başlık, süzgeç) baytları."""
    def hucre(r, c, v):
        metin = escape(_YASAK_XML.sub("", "" if v is None else str(v)))
        stil = ' s="1"' if r == 1 else ""
        return f'<c r="{_sutun(c)}{r}" t="inlineStr"{stil}><is><t xml:space="preserve">{metin}</t></is></c>'
    satir_xml = "".join(
        f'<row r="{r}">' + "".join(hucre(r, c, v) for c, v in enumerate(satir)) + "</row>"
        for r, satir in enumerate([basliklar] + satirlar, 1))
    sutunlar = "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>'
                       for i, w in enumerate(genislikler, 1))
    son = f"{_sutun(len(basliklar) - 1)}{len(satirlar) + 1}"
    sayfa = (f'{_XML_BAS}<worksheet {_NS} {_RNS}><sheetViews><sheetView workbookViewId="0">'
             '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView>'
             f'</sheetViews><cols>{sutunlar}</cols><sheetData>{satir_xml}</sheetData>'
             f'<autoFilter ref="A1:{son}"/></worksheet>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for ad, icerik in list(_XLSX_SABIT.items()) + [("xl/worksheets/sheet1.xml", sayfa)]:
            z.writestr(ad, icerik.encode("utf-8"))
    return buf.getvalue()


def _taraf_sutunlari(taraflar):
    """(Davacı, Davalı, Diğer taraflar) hücreleri; vekil varsa adın yanında."""
    if taraflar is None:
        return ("(alınamadı)", "", "")
    davaci, davali, diger = [], [], []
    for x in taraflar:
        ad = x["ad"] + (f" (Vekil: {x['vekil']})" if x.get("vekil") else "")
        rol = tr_kucuk(x.get("rol"))
        if rol.startswith(("davacı", "davaci")):
            davaci.append(ad)
        elif rol.startswith(("davalı", "davali")):
            davali.append(ad)
        else:
            diger.append(f"{x['rol']}: {ad}" if x.get("rol") else ad)
    return ("; ".join(davaci), "; ".join(davali), "; ".join(diger))


EXCEL_BASLIKLARI = ["Mahkeme Türü", "Birim", "Dosya No", "Dosya Türü", "Dosya Durumu", "Açılış Tarihi",
                    "Davacı", "Davalı", "Diğer Taraflar", "Gerekçeli Karar", "Karar Dosyaları"]
EXCEL_GENISLIK = [22, 34, 12, 18, 12, 13, 40, 40, 40, 16, 50]


def _excel_satiri(d, s):
    yollar = [y for k in s["kararlar"] for y in k.get("yollar", [])]
    sonuc = s["sonuc"]
    if s["kararlar"] and not s.get("indirilecek"):
        sonuc += " — indirilmedi"
    elif s["kararlar"] and not yollar:
        sonuc += " — indirilemedi"
    return [d["tur"], d["birim"], d["no"], d.get("dosya_tur", ""), d["durum"], d.get("acilis", ""),
            *_taraf_sutunlari(s["taraflar"]), sonuc, " | ".join(os.path.relpath(y, KOK) for y in yollar)]


def _excel_yap(t, damga):
    """Kontrol edilen TÜM dosyalar (karar var/yok, indirilsin indirilmesin) bir satır."""
    satirlar = [_excel_satiri(t.dosyalar[did], s) for did, s in t.sonuclar.items()]
    yol = os.path.join(KOK, f"Hukuk Dosyalari {damga}.xlsx")
    _yaz(yol, _xlsx(EXCEL_BASLIKLARI, satirlar, EXCEL_GENISLIK))
    return yol


def _ciktilari_hazirla(t):
    """Excel (kontrol edilen tüm dosyalar) her zaman; ZIP yalnız karar indirildiyse
    (Excel + kararlar; KOK/_zip/, ZIP_SAKLAMA_SN sonra silinir). Excel ofiste KOK'ta kalır."""
    damga = time.strftime("%Y-%m-%d %H.%M")
    t.excel_yol = _excel_yap(t, damga)
    yollar = [y for y in dict.fromkeys(t.cikti) if os.path.exists(y)]
    if not yollar:
        return
    zip_yol = os.path.join(KOK, "_zip", f"Gerekceli Kararlar {damga}.zip")
    os.makedirs(os.path.dirname(zip_yol), exist_ok=True)
    with zipfile.ZipFile(zip_yol, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.write(t.excel_yol, os.path.basename(t.excel_yol))
        for yol in yollar:
            z.write(yol, os.path.relpath(yol, KOK))
    t.cikti_yol = zip_yol


async def _calistir(t, is_):
    try:
        async with evrak_toplu._sira_kilidi():
            t.durum = CALISIYOR
            await is_()
    except _Iptal:
        t.durum, t.mesaj = IPTAL, "Durduruldu."
    except AgHatasi as e:
        t.durum, t.mesaj = HATA, f"UYAP bağlantısı tekrar tekrar koptu ({e}). Bir süre sonra yeniden deneyin."
    except Exception as e:
        t.durum, t.mesaj = HATA, f"Hata: {type(e).__name__}: {e}"
    finally:
        t.captcha = None
        t.mola_bitis = None
        if t.asama == "kontrol" and (t.durum == CALISIYOR or t.sonuclar):
            # Durdurulsa/kopsa da kontrol edilenler boşa gitmesin: indirme seçimine geçilir.
            yarim = "" if t.durum == CALISIYOR else f"{t.mesaj} {len(t.sonuclar)}/{t.toplam} dosya kontrol edildi. "
            dosya = sum(1 for s in t.sonuclar.values() if s["kararlar"])
            t.durum, t.asama = KARAR_SECIM, "karar_secim"
            t.mesaj = (f"{yarim}Kontrol bitti: {len(t.sonuclar)} dosyanın {dosya} tanesinde "
                       f"{t.bulunan} gerekçeli karar var. İndirmek istediklerinizi seçin.")
        elif t.asama == "indirme":
            try:
                await asyncio.to_thread(_ciktilari_hazirla, t)
            except Exception as e:
                t.uyarilar.append(f"Excel/ZIP hazırlanamadı: {type(e).__name__}: {e}")
            if t.durum == CALISIYOR:
                t.durum = BITTI
                t.mesaj = (f"Tamamlandı: {t.toplam} gerekçeli karar ({t.yeni} yeni indirildi). Kayıt: {KOK}"
                           if t.toplam else f"Excel listesi hazır (karar indirilmedi). Kayıt: {KOK}")
            else:
                t.mesaj += (f" {t.i}/{t.toplam} kararda kalındı; yeniden taranınca inenler atlanır.")
        if t.durum not in _BEKLEYEN:
            t.bitis = time.time()


def _temizle():
    simdi = time.time()
    for k, t in list(_KAYITLAR.items()):
        if t.bitis and simdi - t.bitis > ZIP_SAKLAMA_SN:
            if t.cikti_yol:
                try:
                    os.remove(t.cikti_yol)
                except OSError:
                    pass
            _KAYITLAR.pop(k, None)


def aktif():
    """Süren ya da en son tarama (panel sayfa yenilenince kaldığı yerden göstersin)."""
    _temizle()
    return max(_KAYITLAR.values(), key=lambda t: t.olusturma, default=None)


def liste_baslat(durumlar=None):
    """durumlar: aranacak dosyaDurumKod'lar (0 Açık, 1 Kapalı); yoksa ikisi. "Açık" aramasında
    Karara Çıkmış / İstinafta dosyalar da gelir — gerekçeli karar çoğu zaman bunlardadır."""
    _temizle()
    if durumlar is None:
        durumlar = [kod for kod, _ad in DURUMLAR]
    if (not isinstance(durumlar, list) or not durumlar
            or any(d not in dict(DURUMLAR) or isinstance(d, bool) for d in durumlar)):
        raise ValueError("Geçersiz dosya durumu seçimi.")
    for t in _KAYITLAR.values():
        if t.durum in (SIRADA, CALISIYOR, CAPTCHA) + _BEKLEYEN:
            return t
    t = Tarama(sorted(set(durumlar)))
    _KAYITLAR[t.id] = t
    t._gorev = asyncio.get_running_loop().create_task(_calistir(t, lambda: _listele(t)))
    return t


def _secimi_dogrula(dosya_idleri, gecerli, bos_olabilir=False):
    if not isinstance(dosya_idleri, list) or (not dosya_idleri and not bos_olabilir):
        raise ValueError("İşaretli dosya yok.")
    secilen = []
    for did in dosya_idleri:
        did = str(did)
        if did not in gecerli:
            raise ValueError("Listede olmayan dosya seçildi.")
        if did not in secilen:
            secilen.append(did)
    return secilen


def tarama_baslat(t, dosya_idleri):
    """İşaretli dosyalarda gerekçeli karar KONTROLÜ (indirme yok)."""
    if t.durum != SECIM:
        raise ValueError("Tarama zaten başladı ya da liste hazır değil.")
    secilen = [t.dosyalar[did] for did in _secimi_dogrula(dosya_idleri, t.dosyalar)]
    t.durum, t.asama = SIRADA, "kontrol"
    t.toplam, t.i = len(secilen), 0
    t.mesaj = "Kontrol sırada…"
    t._gorev = asyncio.get_running_loop().create_task(_calistir(t, lambda: _kontrol(t, secilen)))
    return t


def indir_baslat(t, dosya_idleri):
    """Kontrolde kararı bulunan dosyalardan işaretlenenlerin kararlarını indirir. Boş liste:
    hiçbir şey indirmeden yalnız Excel listesi hazırlanır."""
    if t.durum != KARAR_SECIM:
        raise ValueError("Kontrol bitmedi ya da indirme zaten başladı.")
    karar_var = {did for did, s in t.sonuclar.items() if s["kararlar"]}
    secilen = _secimi_dogrula(dosya_idleri, karar_var, bos_olabilir=True)
    t.durum, t.asama = SIRADA, "indirme"
    t.i, t.toplam = 0, sum(len(t.sonuclar[did]["kararlar"]) for did in secilen)
    t.mesaj = "İndirme sırada…" if secilen else "Excel listesi hazırlanıyor…"
    t._gorev = asyncio.get_running_loop().create_task(_calistir(t, lambda: _indir(t, secilen)))
    return t


def iptal(t):
    if t.durum in _BEKLEYEN:             # çalışan görev yok: seçim ekranından vazgeçildi
        t.durum, t.mesaj, t.bitis = IPTAL, "Vazgeçildi.", time.time()
    else:
        t.iptal_et()


def getir(t_id):
    return _KAYITLAR.get(t_id)


def dosya_oku(t, excel=False):
    yol = t.excel_yol if excel else t.cikti_yol
    with open(yol, "rb") as f:
        veri = f.read()
    return veri, os.path.basename(yol), (_SML + ".sheet" if excel else "application/zip")
