# -*- coding: utf-8 -*-
"""
Tebligat türü eşleştirme (kullanıcı kararı, 2026-09-25)
=======================================================
"Belirli şablon dışında olanları kullanıcıya manuel sorsun, hatalı veri ile iş
olmaz." Barkod turu, sabit kategorilere uymayan tebligatta zarftaki "BU ZARFTA
<X> VARDIR" metnini HAM olarak yazıyor (ör. "Tensip zaptı", "13/01/2025 tarihli
sıra cetveli ve derece kararı", "4-5 ÖRNEK İCRA EMRİ VE İLAM SURETİ"). Bu modül:
  • ham yazılışları tarih/yazım farkından bağımsız GRUPLAR (`anahtar`),
  • kullanıcının "Tür Eşleştir" penceresinde (barkod_sorgu_ekrani) her grup için
    seçtiği türü `%LOCALAPPDATA%\\UyapMasa\\tur_eslestirme.json`'a kaydeder,
  • mevcut kayıtlara uygular (yedekli) ve barkod_sorgu sonraki turlarda aynı
    yazılışı otomatik olarak o türe çevirir (`eslestir`).
"Belirsiz"/boş kayıtlarda ham ad, kaydın KENDİ mazbata açıklamasındaki belge
adından alınır ("Satış İlanı - [VELİ …] - Barkod Numarası: …" → "Satış İlanı").
Tür bir kez eşleştirilince kullanıcıya bir daha SORULMAZ.
"""

import datetime
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import dosya_core  # noqa: E402

# barkod_sorgu._tebligat_turunu_belirle'nin sabit kategorileri (orada da kullanılır).
TEMEL_TURLER = (
    "Ödeme Emri", "103 Davetiyesi", "Kıymet Takdiri Raporu", "Bilirkişi Raporu",
    "Banka", "Banka (Haciz İhbarnamesi)", "Maaş", "Maaş (Haciz İhbarnamesi)",
    "Maaş (Haciz Müzekkeresi)", "Haciz İhbarnamesi",
)

_TARIH_RE = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b")
_GEREKSIZ = {"tarihli", "gunlu", "yazisi", "yazi", "ve"}


def _yol():
    kok = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    os.makedirs(os.path.join(kok, "UyapMasa"), exist_ok=True)
    return os.path.join(kok, "UyapMasa", "tur_eslestirme.json")


def anahtar(ham):
    """Yazılış farkından bağımsız grup anahtarı: tarih ve 'tarihli/ve/yazısı'
    gibi dolgu kelimeleri atılır, kelimeler tekilleştirilip sıralanır
    ("Satış İlanı Satış İlanı" = "SATIŞ İLANI"; "Ödeme İcra Emri Takibin
    Dayanagi Belge" = "Takibin Dayanagi Belge Ödeme İcra Emri")."""
    s = _TARIH_RE.sub(" ", dosya_core.tr_lower(ham or ""))
    kelimeler = {k for k in re.sub(r"[^0-9a-z]+", " ", s).split() if k not in _GEREKSIZ}
    return " ".join(sorted(kelimeler))


_ONBELLEK = {"mtime": None, "veri": None}


def oku():
    """Eşleştirme dosyası — barkod turu her tebligatta çağırdığı için dosya
    değişmedikçe bellekten döner."""
    try:
        mtime = os.path.getmtime(_yol())
    except OSError:
        return {"eslesmeler": {}, "kategoriler": []}
    if _ONBELLEK["mtime"] != mtime:
        try:
            with open(_yol(), encoding="utf-8") as f:
                d = json.load(f)
            _ONBELLEK["veri"] = {"eslesmeler": dict(d.get("eslesmeler") or {}),
                                 "kategoriler": list(d.get("kategoriler") or [])}
            _ONBELLEK["mtime"] = mtime
        except (OSError, ValueError):
            return {"eslesmeler": {}, "kategoriler": []}
    v = _ONBELLEK["veri"]
    return {"eslesmeler": dict(v["eslesmeler"]), "kategoriler": list(v["kategoriler"])}


def _yaz(d):
    gecici = _yol() + ".tmp"
    with open(gecici, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(gecici, _yol())


def kategoriler():
    """Kullanılabilir türler: temel türler + kullanıcının eklediği yeni türler."""
    d = oku()
    return list(TEMEL_TURLER) + [k for k in d["kategoriler"] if k not in TEMEL_TURLER]


def eslestir(ham):
    """Ham yazılış daha önce eşleştirilmişse türü, değilse None."""
    if not ham:
        return None
    return oku()["eslesmeler"].get(anahtar(ham))


def _mazbata_belge_adi(aciklama):
    """'Satış İlanı - [VELİ …] - Barkod Numarası: …' → 'Satış İlanı' (yoksa '')."""
    ac = (aciklama or "").strip()
    if " - [" in ac:
        return ac.split(" - [", 1)[0].strip()
    return ""


def _ham_ad(tur, mazbata_aciklama):
    """Kaydın eşleştirilecek ham adı: şablon dışı tür metni, ya da (tür
    Belirsiz/boşsa) kendi mazbata açıklamasındaki belge adı."""
    if tur and tur != "Belirsiz" and tur not in kategoriler():
        return tur, "zarf"
    if tur in ("", "Belirsiz"):
        ad = _mazbata_belge_adi(mazbata_aciklama)
        if ad:
            return ad, "mazbata"
    return "", ""


def bekleyen_gruplar():
    """Kullanıcının karar vermesi gereken gruplar (eşleştirilmemiş olanlar),
    en çok kayıt olandan aza: [{anahtar, ornek, yazilislar, adet, kaynak, ornek_dosya}]."""
    dosya_core._django_hazirla()
    from icra_models.models import TebligatBarkod
    eslesmeler = oku()["eslesmeler"]
    kat = set(kategoriler())
    gruplar = {}
    for tur, mz, birim, dosya_no in TebligatBarkod.objects.values_list(
            "tebligat_turu", "tebligat_mazbatasi_aciklama", "dosya__birim__ad", "dosya__dosya_no"):
        if tur in kat:
            continue
        ham, kaynak = _ham_ad(tur, mz)
        if not ham:
            continue
        a = anahtar(ham)
        if not a or a in eslesmeler:
            continue
        g = gruplar.setdefault(a, {"anahtar": a, "yazilislar": {}, "adet": 0, "kaynak": set(),
                                   "ornek_dosya": f"{birim} {dosya_no}"})
        g["adet"] += 1
        g["yazilislar"][ham] = g["yazilislar"].get(ham, 0) + 1
        g["kaynak"].add(kaynak)
    out = []
    for g in gruplar.values():
        g["ornek"] = max(g["yazilislar"].items(), key=lambda p: p[1])[0]
        g["kaynak"] = "/".join(sorted(g["kaynak"]))
        out.append(g)
    out.sort(key=lambda g: -g["adet"])
    return out


def uygula(yeni_eslesmeler=None, log_fn=None):
    """`yeni_eslesmeler` ({anahtar: tür}) kaydedilir, sonra KAYITLI tüm
    eşleştirmeler DB'ye uygulanır (değişen kayıtlar yedeklenir); "Ödeme Emri"ne
    çevrilenlerin dosyalarında kesinleşme yeniden hesaplanır. UYAP'a gitmez.
    Döner: değişen kayıt sayısı."""
    log = log_fn or (lambda *a, **k: None)
    d = oku()
    for a, tur in (yeni_eslesmeler or {}).items():
        tur = (tur or "").strip()
        if not a or not tur:
            continue
        d["eslesmeler"][a] = tur
        if tur not in TEMEL_TURLER and tur not in d["kategoriler"]:
            d["kategoriler"].append(tur)
    if yeni_eslesmeler:
        _yaz(d)
    if not d["eslesmeler"]:
        return 0
    dosya_core._django_hazirla()
    from icra_models.models import TebligatBarkod, Dosya
    kat = set(kategoriler())
    degisecek = []
    for tb_id, dosya_id, tur, mz in TebligatBarkod.objects.values_list(
            "id", "dosya_id", "tebligat_turu", "tebligat_mazbatasi_aciklama"):
        if tur in kat:
            continue
        ham, _k = _ham_ad(tur, mz)
        yeni = d["eslesmeler"].get(anahtar(ham)) if ham else None
        if yeni and yeni != tur:
            degisecek.append((tb_id, dosya_id, tur, yeni))
    if not degisecek:
        return 0
    yedek = os.path.join(os.path.dirname(_yol()), "yedek",
                         f"tur_eslestirme_yedek_{datetime.datetime.now():%Y%m%d_%H%M%S}.json")
    os.makedirs(os.path.dirname(yedek), exist_ok=True)
    with open(yedek, "w", encoding="utf-8") as f:
        json.dump([(i, eski) for i, _d, eski, _y in degisecek], f, ensure_ascii=False)
    for tb_id, _d, _eski, yeni in degisecek:
        TebligatBarkod.objects.filter(pk=tb_id).update(tebligat_turu=yeni)
    odeme = {dosya_id for _i, dosya_id, _e, yeni in degisecek if yeni == "Ödeme Emri"}
    for dosya in Dosya.objects.filter(id__in=odeme):
        dosya_core.kesinlesme_durumlarini_guncelle(dosya_obj=dosya, log_fn=log)
    log(f"… tür eşleştirmesi uygulandı: {len(degisecek)} kayıt ({len(odeme)} dosyada kesinleşme yeniden hesaplandı).")
    return len(degisecek)
