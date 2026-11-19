# -*- coding: utf-8 -*-
"""
Çoklu Yargı Türü — headless sorgu motoru (GUI + web ortak)
===========================================================
`icra_core.py`'nin kanıtlanmış İcra akışına PARALEL, genel amaçlı bir modül
(bkz. docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §4 — mimari karar B: paralel
modül). `icra_core.py` KASTEN dokunulmadan kalır; bu dosya ondan yalnızca
zaten yargı-türünden bağımsız olan yardımcıları İTHAL EDER (aktarım motoru,
taraf varyant çözümü, kayıt ayrıştırma, kolon değeri okuma) — aynı mantığı
ikinci kez yazmaz. Yeni olan tek şey: `birimTuru2`/`birimTuru3`'ün sabit
yerine PARAMETRE olması, Yargı Birimi bootstrap çekimi ve `SenkronKapsami`
tablosuna göre çoklu-kapsam sorgu döngüsü.

Canlı doğrulanan veri (§1, plan dosyası) — TAHMİN YOK:
  Yargı Türü (yargiTuru, sabit enum): 0 Ceza, 1 Hukuk, 2 İcra, 3 Cbs,
    6 İdari Yargı, 11 Satış Memurluğu, 25 Arabuluculuk, 26 Tazminat Komisyonu.
  Yargı Birimi ('yargiBirimleriSorgula_brd.ajx' yanıtı): {"tablo": kod, "kod": ad}
    — 'tablo' = search_phrase_detayli.ajx'teki 'birimTuru2'.
  search_phrase_detayli.ajx: aynı uç nokta, yalnız birimTuru2 (yargı birimi
    kodu) ve birimTuru3 (yargı türü kodu, STR) değişir.
"""

import datetime
import os
import re
import sys
import threading
import time
from contextlib import contextmanager

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import icra_core as _ic  # noqa: E402  (paralel modül — icra_core DOKUNULMAZ, yalnız içe aktarılır)

# ── icra_core'dan aynen alınan, zaten yargı-türünden bağımsız yardımcılar ────
SorguMotoru = _ic.SorguMotoru
OturumHatasi = _ic.OturumHatasi
tr_lower = _ic.tr_lower
taraf_variantlari = _ic.taraf_variantlari
build_taraf_variants = _ic.build_taraf_variants
parse_records = _ic.parse_records
kolon_degeri = _ic.kolon_degeri
parse_taraf_from_uyap = _ic.parse_taraf_from_uyap
save_taraf = _ic.save_taraf

ENDPOINT = _ic.ENDPOINT                        # "search_phrase_detayli.ajx"
PAGE_SIZE = _ic.PAGE_SIZE
BIRIM_LISTE_ENDPOINT = _ic.BIRIM_LISTE_ENDPOINT  # "avukat_mahkemeleri_sorgula.ajx"
YARGI_BIRIMI_ENDPOINT = "yargiBirimleriSorgula_brd.ajx"

# Cbs (yargı türü 3) — CANLI DOĞRULANDI (Chrome ağ yakalama, 2026-07-14,
# kullanıcı isteğiyle: giriş yapılmış avukat.uyap.gov.tr'de Detaylı Arama'da
# Cbs seçilip İl/Yargı Birimi doldurularak). Diğer türlerden üç noktada
# FARKLI:
#  1. `YARGI_BIRIMI_ENDPOINT` yargiTuru=3 için HER ZAMAN [] döner (plan
#     dosyası §1.1'de zaten şüphelenilmişti — "Yargı Birimi alt-listesi yok,
#     bunun yerine İl alanı"). Gerçek birim (savcılık) listesi İL BAZINDA
#     ayrı bir uç noktadan gelir.
#  2. `illeri_getirJSON.ajx` (boş gövde `{}`) TÜM illeri döner:
#     [{"il": <plaka kodu>, "ad": "İSTANBUL", ...}, ...] (81 il).
#  3. Her il için `CBS_BIRIM_ENDPOINT` {"ilKodu": <il>} o ildeki Cumhuriyet
#     Başsavcılıklarını döner: [{"birimAdi","birimId"}, ...] — `birimId`
#     DOĞRUDAN arama isteğinin `birimTuru2` alanında kullanılır (diğer
#     türlerdeki gibi ayrı bir 'birim TÜRÜ' kodu yok, savcılık kendi başına
#     hem tür hem birim).
#  4. Dosya arama isteği `search_phrase_detayli.ajx` YERİNE
#     `CBS_ARAMA_ENDPOINT`'e gider — payload/yanıt ŞEKLİ AYNI
#     ({"dosyaDurumKod","pageSize","pageNumber","birimId","birimTuru2",
#     "birimTuru3"} → [kayıtlar, toplam]), `build_payload_genel`/
#     `parse_records` DEĞİŞİKLİK GEREKTİRMEDİ — canlı doğrulanan gerçek bir
#     kayıt: {"dosyaId":"...","dosyaNo":"2026/88535","dosyaDurumKod":0,
#     "dosyaTurKod":16,"dosyaTur":"CBS Sorusturma Dosyası",...} (models.py
#     Dosya.Tur'a CBS_SORUSTURMA=16 eklendi).
YARGI_TURU_CBS = 3
CBS_ILLER_ENDPOINT = "illeri_getirJSON.ajx"
CBS_BIRIM_ENDPOINT = "cbs_birim_sorgula.ajx"
CBS_ARAMA_ENDPOINT = "avukat_dosya_sorgula_cbs_brd.ajx"

# Yargı Türü — sabit enum (§1.1, ön yüz dropdown'da arka-uç çağrısı olmadan
# listeleniyor; büyümez, bu yüzden DB'de referans tablosu YOK, burada sabit).
YARGI_TURLERI = [
    (0, "Ceza"),
    (1, "Hukuk"),
    (2, "İcra"),
    (3, "Cbs"),
    (6, "İdari Yargı"),
    (11, "Satış Memurluğu"),
    (25, "Arabuluculuk"),
    (26, "Tazminat Komisyonu Başkanlığı"),
]
YARGI_TURU_ADI = dict(YARGI_TURLERI)
YARGI_TURU_ICRA = 2  # bkz. YARGI_TURLERI — _taraflar_sutunlari önceliklendirmesi yalnız İcra'da
YARGI_TURU_HUKUK = 1  # bkz. YARGI_TURLERI — hukuk_dosyalarim.py'nin sabit kapsamı


def _kendi_vekil_adlari():
    """Ayarlar ekranında girilen, KULLANICININ KENDİ vekil ad(lar)ı —
    virgülle ayrılmış serbest metin (ör. "Utku Alpaslan, Ayşe Yılmaz"),
    UYAP taraf yanıtındaki 'vekil' adıyla eşleştirmek için `tr_lower` ile
    normalize edilmiş küme olarak döner. Ortak ayar dosyasını (uyap_app_config.
    json) DOĞRUDAN `uyap_gecit.core.config`'ten okur — `uyap_app` modülünü
    İTHAL ETMEZ (bkz. bellek: server._load_auth() canlı-giriş tuzağı; o modül
    yüklendiğinde yan etkili otomatik bağlantı tetikleyebilir, bu saf ayar
    okuyucusu tetiklemez). Ayar hiç girilmemişse boş küme döner — 'bizim
    taraf' tespiti o zaman sessizce devre dışı kalır (uydurma yok)."""
    try:
        uhg_dir = os.path.normpath(os.path.join(_HERE, "..", "..", "Uyap Haricen Giriş"))
        if uhg_dir not in sys.path:
            sys.path.insert(0, uhg_dir)
        from uyap_gecit.core.config import load_config
        ham = (load_config().get("kendi_vekil_adlari", "") or "").strip()
    except Exception:
        return set()
    return {tr_lower(p.strip()) for p in ham.split(",") if p.strip()}


def bu_makine_istemci_mi():
    """Bu makine ofisin UYAP oturumunu TUTAN makine değil, ona bağlanan bir
    İSTEMCİ mi? (`config["auto_connect"] == "receive"`, bkz. `ayarlar.py`
    AUTO_SECENEKLER — "Bağlantıyı Al (İstemci)".) `_kendi_vekil_adlari` ile
    AYNI güvenli desen: ortak config dosyasını `uyap_gecit.core.config`'ten
    DOĞRUDAN okur, `uyap_app` modülünü İTHAL ETMEZ (bkz. bellek:
    server._load_auth() canlı-giriş tuzağı) — böylece uyapmasa.py'nin
    `_load_auth`'unun ~5 sn'lik arka plan yüklemesini BEKLEMEDEN, açılışın
    en başında (DB/senkron başlatılmadan ÖNCE) güvenle çağrılabilir.

    Kullanıcı bulgusu (2026-07-13): aynı ofisin birden fazla çalışanı kendi
    makinesinde bu programı çalıştırırsa, HER makine kendi gömülü
    veritabanını kurup HER biri ayrıca UYAP'ı tarıyordu — hem gereksiz veri
    tekrarı hem de AYNI paylaşılan UYAP oturumuna (bkz. baglanti.py ofis/
    istemci tüneli) gereksiz yük. Yalnız 'sunucu' (auto_connect="share" veya
    "off" — bkz. dosya_core.py modül üstü Açık Sınır notu) DB/senkron
    çalıştırmalı; 'istemci' (auto_connect="receive") çalıştırmamalı, veriyi
    sunucunun web arayüzünden görüntülemeli. Config okunamazsa/ayar hiç
    girilmemişse False (bugünkü varsayılan davranış BOZULMAZ)."""
    try:
        uhg_dir = os.path.normpath(os.path.join(_HERE, "..", "..", "Uyap Haricen Giriş"))
        if uhg_dir not in sys.path:
            sys.path.insert(0, uhg_dir)
        from uyap_gecit.core.config import load_config
        return (load_config().get("auto_connect", "off") or "off") == "receive"
    except Exception:
        return False


def _django_hazirla():
    """models/ dizinini path'e ekler ve Django'yu kurar (bir kez, önbellekli
    olmadan — çağıran zaten kendi önbelleğini tutuyorsa gerek yok)."""
    mdir = os.path.normpath(os.path.join(_HERE, "..", "..", "models"))
    if mdir not in sys.path:
        sys.path.insert(0, mdir)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "uyapdata.settings")
    import django
    django.setup()


def _cbs_birimleri_getir(motor, log_fn=None, istek_sarici=None):
    """Cbs için Yargı Birimi (savcılık) listesini İL BAZINDA toplar (bkz.
    CBS_* sabitleri üstündeki canlı doğrulama notu): önce `illeri_getirJSON.
    ajx` ile TÜM illeri, sonra HER il için `CBS_BIRIM_ENDPOINT` ile o ildeki
    savcılıkları çeker. 81 ayrı istek gerektirdiğinden diğer türlerden
    YAVAŞTIR — yalnız bootstrap/Yenile'de çalışır, sonuç `YargiBirimi`'ye
    önbelleklenir. Bir ilin isteği başarısız olursa yalnız o il atlanır, tur
    devam eder. `istek_sarici`: bkz. `yargi_birimleri_getir` docstring'i —
    bireysel/arka-plan öncelik geçidinden GEÇMEDEN atılan istekler UYAP'ın
    eşzamanlı reddiyle çakışabiliyordu (kullanıcı bulgusu, 2026-07-14).
    Döner: list[{"kod","ad"}] (kod=savcılığın birimId'si)."""
    log = log_fn or (lambda *a, **k: None)
    sarici = istek_sarici or _bireysel_istek
    iller = _post_eszamanli_korumali(sarici, motor, CBS_ILLER_ENDPOINT, {}, log)
    if not isinstance(iller, list):
        if iller is None:
            log("⚠️ Cbs İl listesi alınamadı (eşzamanlı red).")
        return []
    temiz = []
    for il in iller:
        il_kodu = il.get("il") if isinstance(il, dict) else None
        if il_kodu is None:
            continue
        birimler = _post_eszamanli_korumali(sarici, motor, CBS_BIRIM_ENDPOINT, {"ilKodu": il_kodu}, log)
        if not isinstance(birimler, list):
            continue
        for b in birimler:
            if isinstance(b, dict) and b.get("birimId"):
                temiz.append({"kod": str(b["birimId"]), "ad": str(b.get("birimAdi", "") or "")})
    return temiz


def yargi_birimleri_getir(yargi_turu, log_fn=None, istek_sarici=None):
    """Bir yargı türü için Yargı Birimi (mahkeme türü) listesini UYAP'tan
    çeker (`yargiBirimleriSorgula_brd.ajx`, payload {"yargiTuru": kod}) ve
    `YargiBirimi` tablosuna upsert eder. Döner: list[{"kod","ad"}]. Cbs
    (yargı türü 3) İSTİSNA: bu uç nokta o tür için HER ZAMAN [] döndüğünden
    (canlı doğrulandı) `_cbs_birimleri_getir` ile İl-bazlı toplanır.

    `istek_sarici`: hangi öncelik geçidinden (`_bireysel_istek`/
    `_arka_plan_istek`) geçileceği — VARSAYILAN `_bireysel_istek`. Bu modüldeki
    TÜM UYAP çağrıları aynı `_istek_kilidi`'ni paylaşmalı; bu fonksiyon
    ESKİDEN çıplak `motor._post` çağırıyordu (gate'ten TAMAMEN muaf), bu da
    arka plan senkron turunun (`_gorevleri_genislet`) kullanıcının o anki
    'Dosya Görüntüle' tıklamasıyla AYNI ANDA UYAP'a gitmesine, dolayısıyla
    programın YENİ AÇILDIĞI anda bile ilk tıklamada 'eşzamanlı' reddi
    almasına yol açıyordu (kullanıcı bulgusu, 2026-07-14 — log boşken bile
    oluyordu). Ağ hatasında ya da DB erişilemezse [] döner (çağıran eldeki DB
    önbelleğini `yargi_birimleri_db_den_yukle` ile ayrıca okuyabilir)."""
    log = log_fn or (lambda *a, **k: None)
    sarici = istek_sarici or _bireysel_istek
    motor = SorguMotoru(log)
    if yargi_turu == YARGI_TURU_CBS:
        temiz = _cbs_birimleri_getir(motor, log_fn, sarici)
    else:
        veri = _post_eszamanli_korumali(sarici, motor, YARGI_BIRIMI_ENDPOINT,
                                         {"yargiTuru": str(yargi_turu)}, log)
        liste = veri if isinstance(veri, list) else []
        temiz = [{"kod": str(b.get("tablo", "") or ""), "ad": str(b.get("kod", "") or "")}
                 for b in liste if isinstance(b, dict) and b.get("tablo")]
    if temiz:
        try:
            _django_hazirla()
            from icra_models.models import YargiBirimi
            for b in temiz:
                YargiBirimi.objects.update_or_create(
                    yargi_turu=yargi_turu, kod=b["kod"], defaults={"ad": b["ad"]})
        except Exception as e:
            (log_fn or (lambda *a, **k: None))(f"⚠️ YargiBirimi kaydı atlandı: {e}")
    return temiz


def yargi_birimleri_bootstrap(log_fn=None):
    """İlk bağlantıda: TÜM yargı türleri için Yargı Birimi listesini çeker.
    Küçük, seyrek değişen veri — periyodik (ör. günde bir) yeniden çağrılması
    yeterlidir. Döner: {yargi_turu: [{"kod","ad"}, ...]}."""
    log = log_fn or (lambda *a, **k: None)
    sonuc = {}
    for kod, ad in YARGI_TURLERI:
        log(f"… Yargı Birimi listesi çekiliyor: {ad}")
        sonuc[kod] = yargi_birimleri_getir(kod, log_fn)
    return sonuc


def yargi_birimleri_db_den_yukle(yargi_turu=None):
    """`YargiBirimi` tablosundan (önceden `yargi_birimleri_getir`/`_bootstrap`
    ile doldurulmuş) kayıtları AĞA GİTMEDEN okur — ayar ekranının seçenek
    listesini hızlı açması için. yargi_turu None ise tüm türler. DB
    erişilemezse [] döner. Döner: list[{"yargi_turu","kod","ad"}]."""
    try:
        _django_hazirla()
        from icra_models.models import YargiBirimi
        qs = YargiBirimi.objects.all()
        if yargi_turu is not None:
            qs = qs.filter(yargi_turu=yargi_turu)
        return [{"yargi_turu": b.yargi_turu, "kod": b.kod, "ad": b.ad} for b in qs]
    except Exception:
        return []


def yargi_birimleri_getir_veya_db(yargi_turu, log_fn=None):
    """`yargi_birimleri_db_den_yukle`'nin sonucu boşsa (o yargı türü için DB
    önbelleği — Senkron Kapsamı'nda '↻ Listeyi UYAP'tan Getir/Yenile' hiç
    tıklanmamışsa — hiç doldurulmamışsa) canlı UYAP'tan çeker
    (`yargi_birimleri_getir`, DB'ye de yazar). "Dosyalarım (Tümü)" ekranının
    Yargı Birimi filtresi, YALNIZ önceden yenilenmiş türler için değil, HER
    yargı türü seçiminde dolu gelsin diye (bkz. kullanıcı bulgusu: Hukuk
    seçilince Yargı Birimi listesi boş geliyordu). Döner: list[{"kod","ad"}]."""
    birimler = yargi_birimleri_db_den_yukle(yargi_turu)
    if birimler:
        return birimler
    try:
        return yargi_birimleri_getir(yargi_turu, log_fn)
    except Exception:
        return []


def senkron_kapsami_durumu_getir():
    """Ayar ekranının mevcut işaretli durumunu göstermesi için TÜM
    `SenkronKapsami` kayıtlarını (aktif/pasif fark etmeksizin) döner.
    Döner: list[{"yargi_turu","yargi_birimi_kod","aktif"}]."""
    _django_hazirla()
    from icra_models.models import SenkronKapsami
    return [{"yargi_turu": s.yargi_turu, "yargi_birimi_kod": s.yargi_birimi_kod, "aktif": s.aktif}
            for s in SenkronKapsami.objects.all()]


def senkron_kapsami_kaydet(secimler):
    """Ayar ekranından gelen TAM seçim listesini kaydeder (replace-all
    semantik): `secimler` = list[(yargi_turu:int, yargi_birimi_kod:str)],
    burada yargi_birimi_kod="" o yargı türünün TAMAMI demektir (§2.4, kullanıcı
    onayı 2026-07-10 — 'tüm tür' seçeneği de var). Listede OLMAYAN var olan
    kayıtlar SİLİNMEZ, yalnızca aktif=False yapılır (geçmiş/log amaçlı iz
    korunur); listede olanlar upsert edilip aktif=True yapılır."""
    _django_hazirla()
    from icra_models.models import SenkronKapsami
    from django.db import transaction as _tx
    istenen = {(int(t), (k or "")) for t, k in secimler}
    with _tx.atomic():
        for s in SenkronKapsami.objects.all():
            aktif_olmali = (s.yargi_turu, s.yargi_birimi_kod) in istenen
            if s.aktif != aktif_olmali:
                s.aktif = aktif_olmali
                s.save(update_fields=["aktif"])
        for (t, k) in istenen:
            SenkronKapsami.objects.update_or_create(
                yargi_turu=t, yargi_birimi_kod=k, defaults={"aktif": True})


def birim_listesi_getir_genel(yargi_turu, yargi_birimi_kod, log_fn=None, istek_sarici=None):
    """`icra_core.birim_listesi_getir`'in genellenmiş hâli — sabit
    yargiTuru=2/yargiBirimi=1101 yerine parametre alır. Önbelleksiz (her
    çağrıda UYAP'a gider); çağıran isterse kendi önbelleğini tutar.
    `istek_sarici`: bkz. `yargi_birimleri_getir` docstring'i — aynı gerekçeyle
    (2026-07-14) artık öncelik geçidinden (varsayılan `_bireysel_istek`) geçer.
    Döner: list[{"birimAdi","birimId"}]."""
    log = log_fn or (lambda *a, **k: None)
    motor = SorguMotoru(log)
    payload = {"yargiTuru": str(yargi_turu), "yargiBirimi": yargi_birimi_kod, "dosyaKapaliMi": False}
    veri = _post_eszamanli_korumali(istek_sarici or _bireysel_istek, motor, BIRIM_LISTE_ENDPOINT, payload, log)
    liste = veri if isinstance(veri, list) else []
    return [{"birimAdi": b.get("birimAdi", "") or "", "birimId": str(b.get("birimId", "") or "")}
            for b in liste if isinstance(b, dict) and b.get("birimId")]


def build_payload_genel(values, yargi_turu, yargi_birimi_kod, durum_kod=0, taraf_variant=None, birim_id=None):
    """`icra_core.build_payload`'ın genellenmiş hâli: `birimTuru2`/`birimTuru3`
    sabit değil, verilen yargı birimi/türüne göre kurulur. `values` aynı
    `icra_core.FIELDS` sözleşmesini kullanır (dosyaYil, dosyaNo, tarih
    aralığı); `birim_id` doğrudan verilirse birim adı çözümü atlanır."""
    payload = {
        "dosyaDurumKod": durum_kod,
        "pageSize": PAGE_SIZE,
        "pageNumber": 1,
        "birimTuru2": yargi_birimi_kod,
        "birimTuru3": str(yargi_turu),
    }

    yil_val = (values.get("dosyaYil") or "").strip()
    if yil_val:
        try:
            payload["dosyaYil"] = int(yil_val)
        except ValueError:
            pass

    no_val = (values.get("dosyaNo") or "").strip()
    if no_val:
        try:
            if "/" in no_val:
                _, sira = no_val.split("/", 1)
                payload["dosyaSira"] = int(sira)
            else:
                payload["dosyaSira"] = int(no_val)
        except ValueError:
            pass

    if birim_id:
        payload["birimId"] = birim_id
    elif (values.get("birimAdi") or "").strip().isdigit():
        payload["birimId"] = (values.get("birimAdi") or "").strip()

    start_val = (values.get("dosyaAcilisTarihiStart") or "").strip()
    if start_val:
        payload["dosyaAcilisTarihiStart"] = start_val
    end_val = (values.get("dosyaAcilisTarihiEnd") or "").strip()
    if end_val:
        payload["dosyaAcilisTarihiEnd"] = end_val

    if taraf_variant:
        payload.update(taraf_variant)

    return payload


def senkron_kapsamlari_getir():
    """Aktif `SenkronKapsami` satırlarını HAM hâliyle döndürür: list[(yargi_turu,
    yargi_birimi_kod)]. yargi_birimi_kod boş STRING olabilir ("tüm tür" seçimi,
    §2.4) — bu fonksiyon genişletme YAPMAZ, ham kaydı döner. Genişletme
    (`YargiBirimi`'den o türün tüm kodlarını türetme) `DosyaSorgu.calistir`'in
    işidir (bkz. altındaki `_gorevleri_genislet`)."""
    _django_hazirla()
    from icra_models.models import SenkronKapsami
    return [(s.yargi_turu, s.yargi_birimi_kod) for s in SenkronKapsami.objects.filter(aktif=True)]


_DURUM_KODLARI_TUMU = (0, 1)  # Açık, Kapalı — bkz. DosyaSorgu.calistir docstring


# ── Bireysel-öncelikli istek geçidi (kullanıcı bulgusu, 2026-07-13: "programımız
# açıldığında güncelleme yapıyor, ben Dosya Görüntüle dediğimde eşzamanlı hatası
# alıyoruz") ──────────────────────────────────────────────────────────────────
# UYAP tek oturumda aynı anda İKİNCİ bir isteği REDDEDİYOR (bkz. uyap_core/
# _runtime.py'deki AYNI desen — "Eş zamanlı olarak birden fazla sorgulama
# yapamazsınız!"). Ofis tarafındaki proxy (127.0.0.1:8800) kendi süreci için bunu
# zaten sıraya alıyor, AMA UyapMasa'nın KENDİ arka plan zamanlayıcısı
# (`senkron_zamanlayici_baslat`) ile kullanıcının o anki tıklaması (Dosya
# Görüntüle/Yenile) AYNI süreçte iki ayrı thread'den bağımsız HTTP istekleri
# başlatıyordu — proxy'nin sırası doğru olsa bile, kaynakta (UyapMasa) hangisinin
# ÖNCE gideceğini belirleyen bir şey yoktu. Çözüm: `_runtime.py`'deki
# interactive_write()/batch_write() ADİL DAĞITIM desenini (asyncio yerine
# threading ile) burada tekrarlıyoruz — kullanıcının bireysel isteği YÜKSEK
# öncelikli, arka plan senkronu her tek UYAP isteğinden ÖNCE bireysel bekleyen
# var mı diye kontrol edip varsa yol verir.
_istek_kilidi = threading.Lock()
_bekleyen_bireysel = 0
_bireysel_sayac_kilidi = threading.Lock()
_bireysel_yok = threading.Event()
_bireysel_yok.set()  # başlangıçta bekleyen bireysel istek yok

# ── İstekler arası taban aralık (kullanıcı bulgusu, 2026-07-13: `_istek_kilidi`
# TÜM UYAP isteklerini bu süreç içinde katı biçimde sıraya alıyor — iki istek
# ASLA aynı anda uçmuyor — ama "eşzamanlı" reddi YİNE de çıkabiliyordu, ör.
# `dosya_detay_goster_ve_kaydet`'in ardışık iki çağrısı: dosya_ayrinti_getir
# bitip yanıt döner dönmez (aradaki tek şey bir DB kaydı, milisaniyeler) hemen
# dosya_taraf_getir başlıyordu. Demek ki UYAP'ın REDDİ yalnız "bizim tarafımızda
# çakışma" değil — UYAP'ın kendi oturum kilidini HTTP yanıtını gönderdikten
# SONRA bırakması da biraz zaman alıyor olmalı (bkz. _post_eszamanli_korumali
# üzerindeki 'birkaç saniye sonra kendiliğinden geçer' notu — bu zaten bilinen
# bir gecikmeydi, ama önceden yalnız BAŞARISIZLIK SONRASI bekleniyordu, önceden
# değil). Çözüm: kilidi tutarken bir önceki isteğin BİTİŞİNDEN bu yana en az
# `_MIN_ISTEK_ARALIGI` saniye geçmediyse yenisini başlatmadan önce bekle —
# böylece ilk denemede reddedilme ihtimali baştan azalır; `_post_eszamanli_
# korumali`'nin tekrar-deneme mekanizması yine de bir GÜVENLİK AĞI olarak kalır
# (tamamen dışarıdan — başka ofis/sekme — gelen çakışmalar için).
# NOT: bu bekleme yalnız `_bireysel_istek` (kullanıcının tıklaması) tarafında
# UYGULANIR — `_arka_plan_istek` (toplu tur) BEKLEMEZ, yalnız bitiş zamanını
# KAYDEDER. Toplu tur binlerce ardışık istek atabiliyor (bkz. taraf_da_cek'in
# 2026-07-12 hız düzeltmesi, hemen yukarıda) — her adıma sabit 1sn eklemek o
# düzeltmeyi geri alırdı. Tespit edilen somut çakışma (dosya_ayrinti_getir →
# dosya_taraf_getir) HER ZAMAN _bireysel_istek üzerinden gider, bu yüzden
# bekleme yalnız orada yeterli.
_MIN_ISTEK_ARALIGI = 1.0
_son_istek_bitis = 0.0
_son_istek_kilidi = threading.Lock()


def _aralik_bekle():
    with _son_istek_kilidi:
        bekle = _son_istek_bitis + _MIN_ISTEK_ARALIGI - time.monotonic()
    if bekle > 0:
        time.sleep(bekle)


def _aralik_kaydet():
    global _son_istek_bitis
    with _son_istek_kilidi:
        _son_istek_bitis = time.monotonic()


@contextmanager
def _bireysel_istek():
    """Kullanıcının o anki tıklamasının (Dosya Görüntüle, Yenile) UYAP isteği
    için yüksek öncelikli geçit — arka plan senkronu buna yol verir."""
    global _bekleyen_bireysel
    with _bireysel_sayac_kilidi:
        _bekleyen_bireysel += 1
        _bireysel_yok.clear()
    try:
        with _istek_kilidi:
            _aralik_bekle()
            try:
                yield
            finally:
                _aralik_kaydet()
    finally:
        with _bireysel_sayac_kilidi:
            _bekleyen_bireysel -= 1
            if _bekleyen_bireysel <= 0:
                _bekleyen_bireysel = 0
                _bireysel_yok.set()


@contextmanager
def _arka_plan_istek():
    """Otomatik senkron turunun HER TEK UYAP isteği için düşük öncelikli geçit —
    kilidi almadan önce bekleyen bireysel istek kalmayana kadar bekler; böylece
    kullanıcının tıklaması sayfa/birim sınırında (saniyeler içinde) araya
    girebilir, saatlerce süren tam turun bitmesini beklemez. `_aralik_bekle()`
    BİLEREK çağrılmaz (bkz. `_MIN_ISTEK_ARALIGI` yorumu) — yalnız bitiş zamanı
    kaydedilir ki ardından gelecek bir _bireysel_istek doğru referans alsın."""
    while _bekleyen_bireysel > 0:
        _bireysel_yok.wait()
    with _istek_kilidi:
        try:
            yield
        finally:
            _aralik_kaydet()


# ── UYAP'ın "eşzamanlı" reddinin GERÇEK bir hata olarak tanınması ────────────
# (kullanıcı bulgusu, 2026-07-13: yukarıdaki öncelik geçidi YARIŞI ortadan
# kaldırır AMA UYAP'a bizim dışımızdan — ör. ayrı bir tarayıcı sekmesi/başka bir
# ofis bilgisayarı — aynı anda başka bir istek giderse UYAP yine de "Eş zamanlı
# olarak birden fazla sorgulama yapamazsınız!" ile reddedebilir; bu durumda
# `SorguMotoru._post` (bkz. Sorgu/SGK Sorgu/sgk_sorgu_gui.py — orijinal,
# değiştirilmez) BİR İSTİSNA FIRLATMAZ, red metnini normal 200 yanıt gibi
# döner. Bu metnin ham .ajx gövdesinde TAM OLARAK hangi şekilde geleceği (düz
# metin mi, HTML mi, yoksa UYAP'ın kendi JSON hata zarfı içinde mi) CANLI
# DOĞRULANMADI — yalnız bir TARAYICI SAYFASI görünümü yakalanabildi (bkz. UyapMasa/
# Eşzamanlı hata metni.txt). Bu YÜZDEN biçimden bağımsız, imza metnine dayalı
# bir tarama yapılır: veri dict/list/str hangi şekilde gelirse gelsin, İÇİNDE
# bu imza varsa bu bir HATADIR, gerçek dosya verisi DEĞİLDİR — asla DB'ye
# yazılmaz, aksi halde gerçek/eski veri boş/yanlış değerle EZİLEBİLİR.
_ESZAMANLI_IMZA = "birden fazla sorgulama yapamaz"
# Süreç boyunca görülen "eşzamanlı" ret sayısı — barkod turu bununla dosya
# arası beklemeyi uyarlar (bkz. barkod_sorgu.SATIR_ARASI_SN, 2026-09-24).
ESZAMANLI_RED_SAYISI = 0


def _yanit_hata_zarfi_mi(veri):
    """`veri` UYAP'ın GENEL hata zarfı mı (ör. {'errorCode': 'PRTL_GNL_10001-4',
    'error': 'Doğrulama hatası...'})? — kullanıcı bulgusu, 2026-07-14: bu zarf
    dict OLDUĞU için `dosya_ayrinti_getir` onu 'veri geldi' sanıp doğrudan
    `dosya_ayrinti_kaydet`'e veriyordu; zarftaki alanlar (errorCode/error) gerçek
    dosya alanlarıyla (takibinTuru vb.) eşleşmediğinden `update_or_create`
    HEPSİNİ boş/sıfır değerle YAZIYORDU — DAHA ÖNCE DOĞRU KAYDEDİLMİŞ takibin
    türü/şekli/yolu bu şekilde SESSİZCE SİLİNMİŞ oluyordu. Gerçek başarılı
    yanıtlarda (canlı doğrulanan örnekler) 'errorCode' anahtarı HİÇ görülmedi —
    bu yüzden onun varlığı güvenilir bir hata imzası sayılır."""
    return isinstance(veri, dict) and "errorCode" in veri


def _yanit_eszamanli_hata_mi(veri, derinlik=0):
    """`veri` (motor._post'un döndürdüğü ham/parse edilmiş yanıt) içinde UYAP'ın
    eşzamanlı-red imzası var mı? bkz. modül düzeyi açıklama (_ESZAMANLI_IMZA)."""
    if derinlik > 4:
        return False
    if isinstance(veri, str):
        return _ESZAMANLI_IMZA in veri.lower()
    if isinstance(veri, dict):
        return any(_yanit_eszamanli_hata_mi(v, derinlik + 1) for v in veri.values())
    if isinstance(veri, (list, tuple)):
        return any(_yanit_eszamanli_hata_mi(v, derinlik + 1) for v in veri)
    return False


def _post_eszamanli_korumali(istek_sarici, motor, endpoint, payload, log_fn,
                              tekrar=4, bekleme_saniye=3, **post_kwargs):
    """`istek_sarici()` geçidi ALTINDA `motor._post`'u çağırır; yanıt UYAP'ın
    "eşzamanlı" reddiyse (bkz. _yanit_eszamanli_hata_mi) bunu GERÇEK veri
    SANMAZ — artan aralıklarla (3sn, 6sn, 9sn, 12sn…) tekrar dener (bu bir
    YARIŞ DURUMUDUR, genelde birkaç saniye sonra kendiliğinden geçer), tüm
    denemeler tükenirse None döner. Çağıran None'ı 'veri yok' ile AYNI ele
    almalı (boş/eski kayıt DB'ye asla bu redle EZİLMEMELİ).
    `tekrar=4` (kullanıcı bulgusu, 2026-07-13: 'Yenile' sırasında UYAP önce
    502/'ulaşılamadı' verip ardından ~13sn boyunca 'eşzamanlı' reddetmeye
    devam etti — eski tekrar=2/sabit 3sn toplam bekleme bu tür uzun süren
    UYAP-taraflı tıkanmayı aşamıyordu; artan bekleme ile toplam tolerans
    3+6+9+12=30sn'ye çıkarıldı)."""
    global ESZAMANLI_RED_SAYISI
    log = log_fn or (lambda *a, **k: None)
    for deneme in range(tekrar + 1):
        with istek_sarici():
            _status, veri = motor._post(endpoint, payload, **post_kwargs)
        if not _yanit_eszamanli_hata_mi(veri):
            return veri
        ESZAMANLI_RED_SAYISI += 1
        if deneme < tekrar:
            bekle = bekleme_saniye * (deneme + 1)
            log(f"⚠️ UYAP 'eşzamanlı sorgulama' isteği reddetti ({endpoint}) — "
                f"bu GERÇEK veri değil, {bekle}sn sonra tekrar denenecek "
                f"({deneme + 1}/{tekrar}).")
            time.sleep(bekle)
        else:
            log(f"⚠️ UYAP 'eşzamanlı sorgulama' isteği reddetti ({endpoint}) — "
                f"{tekrar} deneme sonunda hâlâ reddediliyor, bu tur atlanıyor "
                "(DB'ye YAZILMAYACAK, eski/doğru veri korunacak).")
    return None


# ── Evrak (belge) listesi + içerik indirme ───────────────────────────────────
# bkz. docs/BELGE_ONBELLEK_PLANI.md §0.2 — canlı yakalamada `evrakId`/`ggEvrakId`
# VE bir evrak kaydının KENDİ İÇİNDEKİ 'dosyaId' alanı OTURUMLUK/rotating çıktı
# (aynı belge için ardışık iki istekte üçü de farklıydı). Kalıcı anahtar
# `birimEvrakNo`. `view_document_brd.uyap`'a giden dosyaId İSE arama adımından
# (search_phrase_detayli.ajx) ÇÖZÜLEN dosyaId olmalı — evrak kaydının kendi
# 'dosyaId' alanı DEĞİL; 2026-07-23 canlı yakalamada AYNI yanıt içinde bile
# ikisi FARKLI çıktı (ör. "...RmReb5gihS4K9jNV6m19j" vs "...8a6DCpZ7Yi7x16v5v+FOlT").
# Bu ayrım gözden kaçırılırsa indirme oturum/yetki hatasıyla başarısız olur.
EVRAK_LISTESI_ENDPOINT = "list_dosya_evraklar.ajx"
EVRAK_LISTESI_TOPLAM_ENDPOINT = "listDosyaEvraklarPageTotal.ajx"
DOC_VIEWER_HAZIRLIK_ENDPOINT = "getDocViewerParameters.ajx"
EVRAK_ICERIK_YOLU = "view_document_brd.uyap"


def evrak_listesi_getir(dosya_id, log_fn=None, istek_sarici=None):
    """`dosya_id` (arama adımından ÇÖZÜLEN dosyaId) için TÜM sayfalardaki evrak
    kayıtlarını (dict: evrakId, birimEvrakNo, tur, aciklama, onaylandigiTarih,
    ...) düz bir listede döner. Bir sayfa UYAP hata verirse o sayfa atlanır."""
    log = log_fn or (lambda *a, **k: None)
    sarici = istek_sarici or _bireysel_istek
    motor = SorguMotoru(log)

    toplam = _post_eszamanli_korumali(sarici, motor, EVRAK_LISTESI_TOPLAM_ENDPOINT,
                                       {"dosyaId": dosya_id, "pageNumber": 1}, log)
    try:
        toplam_sayfa = max(int(toplam), 1)
    except (TypeError, ValueError):
        toplam_sayfa = 1

    tum = []
    for sayfa in range(1, toplam_sayfa + 1):
        veri = _post_eszamanli_korumali(sarici, motor, EVRAK_LISTESI_ENDPOINT,
                                         {"dosyaId": dosya_id, "pageNumber": sayfa}, log)
        gruplar = veri.get("tumEvraklar") if isinstance(veri, dict) else None
        if not isinstance(gruplar, dict):
            continue
        for grup in gruplar.values():
            if isinstance(grup, list):
                tum.extend(e for e in grup if isinstance(e, dict))
    return tum


def evrak_html_indir(dosya_id, evrak_id, log_fn=None, istek_sarici=None, timeout=90):
    """Bir evrakın ham HTML içeriğini `view_document_brd.uyap`'tan GET ile indirir.
    `dosya_id` ARAMA ADIMINDAN ÇÖZÜLEN dosyaId olmalı (bkz. modül başlığındaki
    not — evrak kaydının kendi 'dosyaId' alanı DEĞİL). UYAP'ın gerçek akışında
    bu isteğin hemen öncesinde `getDocViewerParameters.ajx` boş gövdeyle
    çağrılıyor (canlı yakalamada görüldü); aynı sıra burada da izlenir."""
    import urllib.parse
    import urllib.request

    log = log_fn or (lambda *a, **k: None)
    sarici = istek_sarici or _bireysel_istek
    motor = SorguMotoru(log)

    _post_eszamanli_korumali(sarici, motor, DOC_VIEWER_HAZIRLIK_ENDPOINT, {}, log)

    qs = urllib.parse.urlencode({"evrakId": evrak_id, "dosyaId": dosya_id})
    url = f"{motor.base}/{EVRAK_ICERIK_YOLU}?{qs}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("accept", "*/*")
    req.add_header("referer", f"{motor.base}/dosya-sorgulama")

    with sarici():
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = r.status
            data = r.read()
    if status >= 400:
        raise RuntimeError(f"Evrak indirilemedi (HTTP {status})")
    return data.decode("utf-8", "replace")


class DosyaSorgu:
    """Çoklu yargı türü/birimi dosya sorgusu (headless). `SenkronKapsami`'de
    işaretli her (yargı türü, yargı birimi) kombinasyonu için
    `search_phrase_detayli.ajx`'i kendi birimTuru2/3 değerleriyle çağırır.
    Aktarım ve taraf/kayıt işleme mantığı `icra_core`'dan İTHAL EDİLİR."""

    def __init__(self, log_fn=None, istek_sarici=None):
        self.log_fn = log_fn or (lambda m: None)
        # Varsayılan YÜKSEK öncelik (_bireysel_istek): mevcut tüm çağıranlar
        # (Yenile düğmesi, dosyalarim_yenile) davranışını DEĞİŞTİRMEDEN otomatik
        # olarak arka plan senkronuna karşı öncelik kazanır. Yalnız arka plan
        # zamanlayıcısı (`senkron_zamanlayici_baslat`) düşük öncelikli
        # `_arka_plan_istek`'i AÇIKÇA verir — bkz. _bireysel_istek/_arka_plan_istek
        # docstring'i.
        self._istek_sarici = istek_sarici or _bireysel_istek

    def _bir_kapsami_ara(self, motor, yargi_turu, yargi_birimi_kod, values, durum_kod):
        """Tek bir (yargi_turu, yargi_birimi_kod) için sayfalanmış arama.
        Döner: list[dict] (ham UYAP kayıtları, dosyaId ile bu kapsam içinde
        tekilleştirilmiş). Sayım için `self._son_kapsam_tam` (bütün sayfalar
        alındı mı) ve `self._son_kapsam_uyap_toplam` (UYAP'ın bildirdiği toplam,
        yoksa None) da doldurulur."""
        tum = []
        gorulen = set()
        self._son_kapsam_tam = True
        self._son_kapsam_uyap_toplam = None
        endpoint = CBS_ARAMA_ENDPOINT if yargi_turu == YARGI_TURU_CBS else ENDPOINT
        variantlar = taraf_variantlari(values) or [None]
        for variant in variantlar:
            sayfa = 1
            while True:
                payload = build_payload_genel(values, yargi_turu, yargi_birimi_kod, durum_kod, variant)
                payload["pageNumber"] = sayfa
                veri = _post_eszamanli_korumali(self._istek_sarici, motor, endpoint, payload, self.log_fn)
                if _ic.sayfa_yaniti_gecersiz_mi(veri):
                    # Eskiden None/hata zarfı parse_records ile "0/1 kayıtlık son
                    # sayfa" sanılıp kapsam BAŞARILI bitmiş gibi duruyordu —
                    # kalan sayfalar SESSİZCE eksik kalıyordu (2026-09-23).
                    neden = ("'eşzamanlı sorgulama' reddi" if veri is None
                             else f"UYAP hatası ({veri.get('error') or veri.get('errorCode')})")
                    self.log_fn(f"⚠️ ({YARGI_TURU_ADI.get(yargi_turu, yargi_turu)}/{yargi_birimi_kod}) "
                                f"sayfa {sayfa} {neden} yüzünden alınamadı — bu kapsamın listesi EKSİK.")
                    self._son_kapsam_tam = False
                    break
                if sayfa == 1 and variant is None:
                    self._son_kapsam_uyap_toplam = _ic.uyap_toplam_sayisi(veri)
                kayitlar = parse_records(veri)
                for rec in kayitlar:
                    did = rec.get("dosyaId")
                    if did and did in gorulen:
                        continue
                    if did:
                        gorulen.add(did)
                    tum.append(rec)
                if len(kayitlar) < PAGE_SIZE:
                    break
                sayfa += 1
                if sayfa > 200:
                    self.log_fn(f"⚠️ ({YARGI_TURU_ADI.get(yargi_turu, yargi_turu)}/{yargi_birimi_kod}) "
                                "sayfa sınırına (200) ulaşıldı; bazı kayıtlar alınmamış olabilir.")
                    self._son_kapsam_tam = False
                    break
        return tum

    def _gorevleri_genislet(self, kapsamlar):
        """Ham `SenkronKapsami` satırlarını (yargi_turu, yargi_birimi_kod) iş
        listesine çevirir. yargi_birimi_kod="" ("tüm tür" seçimi, §2.4) ise
        `YargiBirimi`'den o türün TÜM kayıtlı kodları genişletilir (önce DB
        önbelleği, boşsa canlı `yargi_birimleri_getir` denenir). Genişletilemezse
        (Yargı Birimi listesi de boşsa) o kapsam log ile bildirilip atlanır.
        Döner: list[(yargi_turu, yargi_birimi_kod, yargi_turu_adi)]."""
        gorevler = []
        for yargi_turu, yargi_birimi_kod in kapsamlar:
            ad = YARGI_TURU_ADI.get(yargi_turu, str(yargi_turu))
            if yargi_birimi_kod:
                gorevler.append((yargi_turu, yargi_birimi_kod, ad))
                continue
            birimler = yargi_birimleri_db_den_yukle(yargi_turu)
            if not birimler:
                self.log_fn(f"… '{ad}' (tüm tür) için Yargı Birimi listesi DB'de yok, canlı çekiliyor…")
                try:
                    birimler = yargi_birimleri_getir(yargi_turu, self.log_fn, self._istek_sarici)
                except Exception as e:
                    self.log_fn(f"⚠️ '{ad}' (tüm tür) genişletilemedi: {e}")
                    birimler = []
            if not birimler:
                self.log_fn(f"⚠️ '{ad}' (tüm tür) genişletilemedi — Yargı Birimi listesi boş/erişilemez, bu kapsam atlandı.")
                continue
            for b in birimler:
                if b.get("kod"):
                    gorevler.append((yargi_turu, b["kod"], f"{ad}/{b.get('ad', b['kod'])}"))
        return gorevler

    def calistir(self, values=None, durum_kod=None, tum_turler=False, tek_kapsam=None, taraf_da_cek=False,
                 kontrol=None):
        """`SenkronKapsami`'deki her aktif kapsamı sırayla arar, kaydeder.
        Döner: (kayit_sayisi, kapsam_sonuclari:list[dict]). `values` boşsa
        (varsayılan) kapsam-genişleticiler dışında ekstra filtre uygulanmaz.
        `tek_kapsam=(yargi_turu, yargi_birimi_kod)` verilirse SADECE o (tür,
        birim) taranır (`yargi_birimi_kod=""` ise o türün TÜM birimleri —
        bkz. `_gorevleri_genislet`) — 'Dosyalarım (Tümü)' ekranının EKONOMİK
        Yenile'si için (kullanıcı bulgusu: eskiden Yenile seçili filtreden
        bağımsız HER ZAMAN tüm türleri/birimleri tarıyordu, pahalıydı).
        `tum_turler=True` ise `SenkronKapsami`'ye HİÇ BAKMADAN her yargı
        türünü ('Tümü' birim genişletmesiyle) tarar — 'Tüm Dosyaları
        Güncelle' ayrı düğmesi için. `tek_kapsam`, `tum_turler`'den ÖNCELİKLİDİR.
        `taraf_da_cek=True` ise her kayıt için AYRICA taraf bilgisi de çekilip
        kaydedilir (kapsamdaki dosya sayısı kadar EK canlı UYAP isteği —
        kullanıcı bulgusu, 2026-07-12: 'Dosyalarım (Tümü)' ekranının Taraf Adı
        filtresi anlamlı olsun diye eklendi). VARSAYILAN False: arka plan
        zamanlayıcısı (`senkron_zamanlayici_baslat`) bu parametreyi VERMEZ —
        30 dakikada bir sessizce çalışan otomatik tur eskisi gibi yalnız ucuz
        kapak künyesini çeker; taraf çekimi yalnız kullanıcının doğrudan
        tetiklediği 'Yenile'/'Tüm Dosyaları Güncelle'de (bkz. `dosyalarim_yenile`)
        açılır — sessiz arka plan turunun maliyetini artırmamak için.

        `durum_kod`: VARSAYILAN `None` — her kapsam HEM Açık (0) HEM Kapalı
        (1) `dosyaDurumKod`'uyla ayrı ayrı taranır (bkz. `_DURUM_KODLARI_TUMU`).
        Açık/Kapalı ikili kodu `icra_core.DURUMLAR`'dan CANLI DOĞRULANMIŞTIR
        (yalnız İcra için); `search_phrase_detayli.ajx` TÜM yargı türlerinde
        AYNI uç nokta/payload şeklini paylaştığından (§1.4 plan dosyası) aynı
        ikili kod diğer türlere de uygulanır — bu genelleme türe özel CANLI
        doğrulanmadı (TAHMİN olarak işaretlenir). Eskiden BU PARAMETRE HİÇBİR
        çağıran tarafından verilmiyordu ve varsayılan `0` (yalnız Açık) idi:
        yani 'Dosyalarım (Tümü)' ekranı hiçbir yargı türünde KAPALI dosyayı
        HİÇ çekmiyordu (kullanıcı bulgusu, 2026-07-14: özellikle Arabuluculuk'ta
        fark edildi, ama kapsam TÜM türleri etkiliyordu). `durum_kod` açıkça
        bir tamsayı verilirse (ör. eski/özel bir çağrı) eskisi gibi TEK durum
        taranır."""
        values = values or {}
        durum_kodlari = list(_DURUM_KODLARI_TUMU) if durum_kod is None else [durum_kod]
        motor = SorguMotoru(self.log_fn)
        if tek_kapsam is not None:
            kapsamlar = [tek_kapsam]
        elif tum_turler:
            kapsamlar = [(kod, "") for kod, _ad in YARGI_TURLERI]
        else:
            try:
                kapsamlar = senkron_kapsamlari_getir()
            except Exception as e:
                raise RuntimeError(f"SenkronKapsami okunamadı (DB erişilemez olabilir): {e}")
            if not kapsamlar:
                self.log_fn("SenkronKapsami boş — hiçbir yargı türü/birimi seçilmemiş, senkron yapılacak bir şey yok.")
                return 0, []

        gorevler = self._gorevleri_genislet(kapsamlar)
        if not gorevler:
            self.log_fn("Genişletme sonrası sorgulanacak hiçbir (yargı türü, yargı birimi) kalmadı.")
            return 0, []

        try:
            _django_hazirla()
            from icra_models.ingest import dosya_kunyesi_kaydet
            db_available = True
        except Exception as e:
            self.log_fn(f"⚠️ Yerel veritabanı aktif değil: {e}")
            db_available = False

        toplam = 0
        taraf_cekilen = 0
        sonuclar = []
        for yargi_turu, yargi_birimi_kod, ad in gorevler:
            if kontrol:
                kontrol.tur_bitti()
                if not kontrol.nokta():
                    self.log_fn("⏹ Durduruldu.")
                    break
            kayitlar = []
            gorulen_did = set()
            basarisiz = False
            kapsam_eksik = False
            kapsam_uyap_toplam = 0   # durum kodları toplamı; biri bilinmiyorsa None
            for dk in durum_kodlari:
                durum_adi = {0: "Açık", 1: "Kapalı"}.get(dk, str(dk))
                self.log_fn(f"… sorgulanıyor: {ad} / {yargi_birimi_kod} ({durum_adi})")
                try:
                    parca = self._bir_kapsami_ara(motor, yargi_turu, yargi_birimi_kod, values, dk)
                except OturumHatasi:
                    raise
                except Exception as e:
                    self.log_fn(f"⚠️ {ad}/{yargi_birimi_kod} ({durum_adi}) sorgusu başarısız: {e}")
                    basarisiz = True
                    kapsam_eksik = True
                    kapsam_uyap_toplam = None
                    continue
                metin, eksik = _ic.sayim_satiri(
                    f"{ad}/{yargi_birimi_kod or 'tümü'} ({durum_adi})", len(parca),
                    self._son_kapsam_uyap_toplam, self._son_kapsam_tam)
                self.log_fn(metin)
                kapsam_eksik = kapsam_eksik or eksik
                if kapsam_uyap_toplam is not None and self._son_kapsam_uyap_toplam is not None:
                    kapsam_uyap_toplam += self._son_kapsam_uyap_toplam
                else:
                    kapsam_uyap_toplam = None
                for rec in parca:
                    did = rec.get("dosyaId")
                    if did and did in gorulen_did:
                        continue
                    if did:
                        gorulen_did.add(did)
                    kayitlar.append(rec)
            if basarisiz and not kayitlar:
                sonuclar.append({
                    "yargi_turu": yargi_turu, "yargi_turu_adi": ad,
                    "yargi_birimi_kod": yargi_birimi_kod,
                    "bulunan": 0, "kaydedilen": 0,
                    "uyap_toplam": None, "eksik": True,
                })
                continue

            kaydedilen = 0
            if db_available:
                for rec in kayitlar:
                    if kontrol:
                        kontrol.tur_bitti()
                        if not kontrol.nokta():
                            self.log_fn("⏹ Durduruldu.")
                            break
                    dosya_id = rec.get("dosyaId")
                    if not dosya_id:
                        continue
                    try:
                        dosya, _created = dosya_kunyesi_kaydet(rec, yargi_turu=yargi_turu)
                        kaydedilen += 1
                    except Exception as e:
                        self.log_fn(f"⚠️ Kayıt atlandı ({rec.get('dosyaNo','')}): {e}")
                        continue
                    if not taraf_da_cek:
                        continue
                    # ZATEN taraf verisi olan kayıt atlanır (kullanıcı bulgusu,
                    # 2026-07-13: taraf_da_cek ilk eklendiğinde HER kayıt için
                    # koşulsuz ek istek atılıyordu — büyük kapsamlarda (binlerce
                    # dosya) bu, tek çalıştırmayı pratikte bitmeyecek kadar
                    # yavaşlatıp 'Yenile' düğmesini uzun süre devre dışı bırakıyor,
                    # kullanıcıya "bir kere çalıştı, sonra tıklanmıyor" gibi
                    # görünüyordu. Bu atlama sayesinde TEKRAR eden turlar yalnız
                    # YENİ/taraf'ı henüz çekilmemiş dosyalar için istek atar.
                    # Kimlikli (isim/unvan dolu) taraf kaydı VARSA atla —
                    # yalnız BOŞ/hayalet kayıt (bkz. dosya_taraf_kaydet'teki
                    # "Kimliksiz kayıt KORUMASI") "taraf var" SAYILMAZ (canlı
                    # DB denetimi, 2026-08-13: 953 dosyada boş hayalet taraf
                    # kaydı bulunmuştu; eski `exists()` kontrolü bu dosyalar
                    # için toplu sorguda taraf bilgisini SONSUZA DEK bir daha
                    # hiç çekmiyordu — kullanıcı bulgusu: "Tüm Dosyalarım'dan
                    # sorgula dedim, veritabanını güncelledim ama taraf
                    # bilgileri hâlâ çıkmıyor").
                    if dosya.taraf_baglari.exclude(
                            taraf__ad="", taraf__soyad="", taraf__unvan="").exists():
                        continue
                    # Yalnız `taraf_da_cek=True` iken (bkz. docstring): kapsamdaki
                    # YENİ dosya sayısı kadar EK canlı UYAP isteği. Bir kaydın
                    # taraf çekimi başarısız olursa yalnız o kayıt atlanır, tur
                    # devam eder.
                    try:
                        # KISA timeout/tek deneme (bkz. dosya_taraf_getir
                        # docstring): taraf sayısı çok olan dosyalarda UYAP
                        # yanıtı 90s×3 varsayılanla ~4.5dk sürebiliyor —
                        # kullanıcı bulgusu (2026-07-12): 'Yenile' tam da
                        # böyle 'kalabalık' bir dosyada takılıp kalıyordu.
                        # Burada başarısız/yavaş bir dosya HIZLI atlanır,
                        # tur devam eder; tek dosya ayrıntısını açan kullanıcı
                        # (dosya_detay_goster_ve_kaydet) hâlâ tam sabırla bekler.
                        taraflari_tazele(dosya, dosya_id, self.log_fn, self._istek_sarici,
                                         timeout=20, denemeler=1)
                    except Exception as e:
                        self.log_fn(f"⚠️ Taraf bilgisi atlandı ({rec.get('dosyaNo','')}): {e}")
                    taraf_cekilen += 1
                    if taraf_cekilen % 50 == 0:
                        # İlerleme günlüğü — bu satır olmadan uzun bir tur
                        # sessizce çalışır ve kullanıcıya "takıldı" gibi görünür
                        # (kullanıcı bulgusu, 2026-07-13).
                        self.log_fn(f"… taraf bilgisi: {taraf_cekilen} yeni dosya işlendi ({ad})")

            toplam += len(kayitlar)
            if db_available and kaydedilen < len(kayitlar):
                self.log_fn(f"⚠️ {ad}/{yargi_birimi_kod or 'tümü'}: {len(kayitlar)} dosyadan "
                            f"{kaydedilen} tanesi veritabanına yazıldı.")
                kapsam_eksik = True
            sonuclar.append({
                "yargi_turu": yargi_turu, "yargi_turu_adi": ad,
                "yargi_birimi_kod": yargi_birimi_kod,
                "bulunan": len(kayitlar), "kaydedilen": kaydedilen,
                "uyap_toplam": kapsam_uyap_toplam, "eksik": kapsam_eksik,
            })
        # Tur sonu özeti (kullanıcı isteği, 2026-09-23: "UYAP'taki dosya sayısı
        # kadar dosya indirildi mi emin olmalıyım").
        eksikler = [s for s in sonuclar if s.get("eksik")]
        self.log_fn("── Sayım raporu ──")
        self.log_fn(f"Toplam {toplam} dosya alındı, "
                    f"{sum(s['kaydedilen'] for s in sonuclar)} tanesi veritabanına yazıldı "
                    f"({len(sonuclar)} kapsam).")
        if eksikler:
            self.log_fn(f"⚠️ {len(eksikler)} kapsam EKSİK/doğrulanamadı: " + ", ".join(
                f"{s['yargi_turu_adi']}/{s['yargi_birimi_kod'] or 'tümü'}" for s in eksikler[:10])
                + (" …" if len(eksikler) > 10 else "") + " — bu kapsamlar için Yenile'yi tekrar çalıştırın.")
        elif sonuclar:
            self.log_fn("✔ Tüm kapsamlar eksiksiz alındı ve yazıldı.")
        return toplam, sonuclar


# ── "Dosya Görüntüle" — Dosya Bilgileri sekmesi ayrıntısı ────────────────────
# (docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §2.5/§7 "Kalan")
# İstek şekli TAHMİN DEĞİL: UyapMasa/modules/Vekalet_Sunma.py'deki CANLI YAKALANMIŞ
# akışın 7. adımından alındı — {"dosyaId": "..."} tek alanlı payload.
# Yanıt şeklinin YALNIZ bir kısmı canlı doğrulandı (plan §1.5/§2.5, önceki
# oturumdan — prose özet, ham JSON dökümü değil). Bu yüzden ingest fonksiyonu
# YALNIZ doğrulanan anahtarları yazar; geri kalanı (ör. faiz/masraf ayrıntısı,
# ilgili/seri/birleşen dosya listeleri, başvuruya bırakılma tarihi) UYDURULMAZ,
# model varsayılanında bırakılır. Ham yanıt log_fn'e yazılır ki canlı doğrulama
# yapıldığında gerçek anahtar adları buradan (loglardan) okunabilsin.
DOSYA_AYRINTI_ENDPOINT = "dosyaAyrintiBilgileri_brd.ajx"


def dosya_ayrinti_getir(dosya_id, log_fn=None, istek_sarici=None):
    """`dosyaAyrintiBilgileri_brd.ajx` çağrısı. `dosya_id`, çağrıldığı ANKİ
    arama oturumunun TAZE 'dosyaId' değeri olmalı (bkz. models.Dosya
    docstring'i — dosyaId oturumluktur, DB'den eski bir değer okunup buraya
    verilmemeli). `istek_sarici`: VARSAYILAN `_bireysel_istek` (kullanıcının
    doğrudan tıklaması) — SWR arka plan tazelemesi gibi düşük öncelikli
    çağıranlar `_arka_plan_istek` vermeli (bkz. `dosya_detay_goster_ve_kaydet`)
    ki kullanıcının bir SONRAKİ gerçek tıklamasının önüne geçmesin. Ağ
    hatasında ya da beklenmeyen yanıt şeklinde {} döner."""
    log = log_fn or (lambda *a, **k: None)
    motor = SorguMotoru(log)
    try:
        # Öncelik geçidi (bkz. istek_sarici) + UYAP'ın "eşzamanlı" reddini
        # GERÇEK veri sanmayan koruma (bkz. _post_eszamanli_korumali) —
        # kullanıcı bulgusu, 2026-07-13.
        veri = _post_eszamanli_korumali(istek_sarici or _bireysel_istek, motor,
                                         DOSYA_AYRINTI_ENDPOINT, {"dosyaId": str(dosya_id)}, log)
    except Exception as e:
        log(f"⚠️ Dosya ayrıntısı alınamadı: {e}")
        return {}
    if _yanit_hata_zarfi_mi(veri):
        log(f"⚠️ UYAP hata zarfı döndürdü (gerçek veri değil): {veri} — "
            "bu tur DB'ye YAZILMAYACAK, eski/doğru veri korunacak.")
        return {}
    return veri if isinstance(veri, dict) else {}


def _decimal_veya_sifir(deger):
    from decimal import Decimal, InvalidOperation
    try:
        if deger in (None, ""):
            return Decimal("0")
        return Decimal(str(deger))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _acik_metin_bul(ham, kod_alan):
    """`ham` (dosyaAyrintiBilgileri_brd.ajx yanıtı) içinde `kod_alan` (ör.
    'takibinTuru') ile İLGİLİ, kod DEĞİL okunabilir METİN taşıyan bir kardeş
    alan arar. UYARI: bu uç nokta için açıklama alanının GERÇEK adı henüz
    canlı doğrulanmadı (bkz. plan §2.5) — dönen eşleşme bir TAHMİNDİR (ad
    benzerliğine dayanır, MTS modülündeki '<alan>Aciklama'/'<alan>Adi'
    deseninden esinlenir — bkz. uyap_core/mts/parse.py — ama bu, FARKLI bir
    UYAP alt sisteminin XML şeması, bu .ajx uç noktasının kanıtı DEĞİLDİR).
    Bu YÜZDEN çağıran kod bu değeri asla DB'ye yazmamalı, yalnız ekranda
    '(tahmini)' etiketiyle göstermelidir — kesin/doğrulanmış veriymiş gibi
    sunulursa kullanıcı yanlış bilgiye güvenebilir. Bulunamazsa ("", "")
    döner. İkinci eleman hangi anahtarın eşleştiğidir — log_fn'e yazılır ki
    bir canlı test sırasında gerçek anahtar adı buradan teyit/düzeltilebilsin."""
    kod_deger = str(ham.get(kod_alan, "") or "").strip()
    kok = kod_alan.lower()
    en_iyi_anahtar, en_iyi_deger = "", ""
    for anahtar, deger in ham.items():
        if anahtar == kod_alan or not isinstance(deger, str):
            continue
        deger = deger.strip()
        if not deger or deger == kod_deger or deger.isdigit() or kok not in anahtar.lower():
            continue
        if len(deger) > len(en_iyi_deger):
            en_iyi_anahtar, en_iyi_deger = anahtar, deger
    return en_iyi_deger, en_iyi_anahtar


def dosya_ayrinti_kaydet(dosya, ham, log_fn=None):
    """Bir `Dosya` (Django nesnesi) için `dosya_ayrinti_getir`'in ham yanıtını
    ailesine (İcra/Hukuk) göre `IcraTakipDetay`/`HukukDavaDetay`'a yazar.
    Aile, kaydın kendi alanından DEĞİL `dosya.birim.yargi_turu`'ndan belirlenir
    (Dosya'da yargi_turu alanı YOK, §1.6). Diğer yargı türleri için henüz
    *Detay modeli yok (plan §5) — o durumda hiçbir şey yazılmaz, yalnız
    loglanır. Döner: ("icra"|"hukuk"|None, kaydedildi:bool)."""
    log = log_fn or (lambda m: None)
    if not isinstance(ham, dict) or not ham:
        log("⚠️ Dosya ayrıntısı boş/geçersiz yanıt — kaydedilmedi.")
        return None, False
    log(f"… ham dosya ayrıntısı (teşhis/canlı doğrulama için): {ham}")

    _django_hazirla()
    from icra_models.models import IcraTakipDetay, HukukDavaDetay
    from icra_models.ingest import _tarih

    yargi_turu = dosya.birim.yargi_turu
    if yargi_turu == 2:  # İcra
        turu_metin, turu_k = _acik_metin_bul(ham, "takibinTuru")
        sekli_metin, sekli_k = _acik_metin_bul(ham, "takibinSekli")
        yolu_metin, yolu_k = _acik_metin_bul(ham, "takibinYolu")
        for etiket, anahtar, metin in (("takibinTuru", turu_k, turu_metin),
                                        ("takibinSekli", sekli_k, sekli_metin),
                                        ("takibinYolu", yolu_k, yolu_metin)):
            if metin:
                log(f"… '{etiket}' için TAHMİNİ açıklama '{anahtar}' alanından "
                    f"bulundu: {metin!r} (canlı doğrulanmadı — DB'ye yazılmadı, "
                    "yalnız ekranda '(tahmini)' etiketiyle gösterilecek).")
        # takibin_turu/sekli/yolu_aciklama alanları BİLEREK 'defaults'a
        # eklenmiyor: gerçek UYAP anahtar adı canlı doğrulanana kadar DB'ye
        # tahmini metin yazılmaz (bkz. _acik_metin_bul docstring'i) — yalnız
        # kod alanları güncellenir, açıklama alanları dokunulmadan kalır.
        IcraTakipDetay.objects.update_or_create(
            dosya=dosya,
            defaults={
                "takibin_turu": str(ham.get("takibinTuru", "") or ""),
                "takibin_sekli": str(ham.get("takibinSekli", "") or ""),
                "takibin_yolu": str(ham.get("takibinYolu", "") or ""),
                "alacak_kalemi_toplam": _decimal_veya_sifir(ham.get("alacakKalemToplamTutar")),
                "vekalet_ucreti": _decimal_veya_sifir(ham.get("vekaletUcreti")),
                "tahsil_harci": _decimal_veya_sifir(ham.get("tahsilHarci")),
            },
        )
        # UI (masaüstü+web) 'ham'ı doğrudan kullanır; kod yerine TAHMİNİ metni
        # gösterebilsin diye senkron anahtarlar eklenir, '(tahmini)' etiketiyle
        # açıkça işaretlenir — bulunamadıysa koda düşer (etiketsiz).
        ham["takibinTuru_metin"] = f"{turu_metin} (tahmini)" if turu_metin else str(ham.get("takibinTuru", "") or "")
        ham["takibinSekli_metin"] = f"{sekli_metin} (tahmini)" if sekli_metin else str(ham.get("takibinSekli", "") or "")
        ham["takibinYolu_metin"] = f"{yolu_metin} (tahmini)" if yolu_metin else str(ham.get("takibinYolu", "") or "")
        return "icra", True
    if yargi_turu == 1:  # Hukuk
        HukukDavaDetay.objects.update_or_create(
            dosya=dosya,
            defaults={
                "dava_acilis_turu": str(ham.get("davaAcilisTuru", "") or ""),
                "dava_turleri": str(ham.get("davaTurleriStr", "") or ""),
                "ilgili_dava_listesi": str(ham.get("ilgiliDavaListesiStr", "") or ""),
                "durusma_tarihi": _tarih(ham.get("durusmaTarihi")),
            },
        )
        if ham.get("basvuruyaBirakilmaTarihiStr"):
            log("… 'basvuruyaBirakilmaTarihiStr' biçimi henüz canlı doğrulanmadı, "
                f"kaydedilmedi (ham değer: {ham.get('basvuruyaBirakilmaTarihiStr')!r}).")
        return "hukuk", True
    log(f"⚠️ Yargı türü {yargi_turu} için henüz ayrı bir *Detay modeli yok "
        "(bkz. plan §5) — bu dosya için ayrıntı kaydedilmedi.")
    return None, False


# ── Taraf Bilgileri (tüm yargı türleri) ──────────────────────────────────
# Canlı doğrulandı (2026-07-10, Hukuk dava dosyası, klasik avukat.uyap.gov.tr
# üzerinden Chrome ağ trafiği yakalanarak — bkz.
# docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §8). Yanıt şekli:
#   [{"adi": str, "rol": "Davacı"|"Davalı", "vekil": "[AD SOYAD]" (opsiyonel —
#     vekil yoksa anahtar HİÇ yok), "kisiKurum": "Kişi"|"Kurum"}, ...]
# TCKN/MERSİS/vergi no VERMEZ — save_taraf bu yüzden ad/unvan eşleşmesine
# düşer (icra_core.save_taraf zaten bunu destekliyor, tckn'i defaults'ta
# EZMEZ). Yalnız Hukuk canlı test edildi; sekmenin diğer yargı türlerinde de
# aynı uç noktayı kullandığı `dosya_islem_turleri_sorgula_brd.ajx` yanıtındaki
# ortak modül listesinden (taraf_bilgileri) çıkarılıyor — ayrı ayrı
# doğrulanmadı, bu yüzden hata durumunda sessizce [] döner (varsayım UI'ı
# bozmaz, yalnız o dosya için taraf boş kalır).
TARAF_BILGILERI_ENDPOINT = "dosya_taraf_bilgileri_brd.ajx"


def dosya_taraf_getir(dosya_id, log_fn=None, timeout=90, denemeler=3, istek_sarici=None):
    """`dosya_taraf_bilgileri_brd.ajx` çağrısı. `dosya_id` TAZE olmalı (bkz.
    `dosya_ayrinti_getir` docstring'i — aynı oturumluk kısıt). Ağ hatasında
    ya da beklenmeyen yanıt şeklinde [] döner. `timeout`/`denemeler`
    VARSAYILANI (90s×3 ≈ 4.5dk) kullanıcının TEK dosya için beklediği
    'Dosya Görüntüle' akışına uygundur; taraf sayısı ÇOK olan dosyalarda
    UYAP'ın yanıt süresi de uzuyor — toplu 'Yenile' turunda (bkz.
    `DosyaSorgu.calistir`) bu yüzden DAHA KISA değerler verilir, aksi halde
    tek 'kalabalık' dosya turun tamamını dakikalarca kilitleyip kullanıcıya
    'takıldı' izlenimi veriyordu (kullanıcı bulgusu, 2026-07-12). `istek_sarici`:
    bkz. `dosya_ayrinti_getir` docstring'i — VARSAYILAN `_bireysel_istek`."""
    log = log_fn or (lambda *a, **k: None)
    motor = SorguMotoru(log)
    try:
        # bkz. dosya_ayrinti_getir'deki AYNI gerekçe — bu da öncelik geçidi
        # (istek_sarici) + eşzamanlı-red koruması.
        veri = _post_eszamanli_korumali(istek_sarici or _bireysel_istek, motor,
                                         TARAF_BILGILERI_ENDPOINT, {"dosyaId": str(dosya_id)}, log,
                                         timeout=timeout, denemeler=denemeler)
    except Exception as e:
        log(f"⚠️ Taraf bilgileri alınamadı: {e}")
        return []
    return veri if isinstance(veri, list) else []


def _taraf_bilgisi_ayristir(item):
    """`dosya_taraf_getir` satırını `save_taraf`'ın beklediği sözlüğe çevirir.
    Ad/soyad ayrımı, kod tabanında zaten kullanılan sezgiyle yapılır (son
    kelime soyad — bkz. icra_core.parse_taraf_from_uyap'taki unvan bölme)."""
    adi = str(item.get("adi", "") or "").strip()
    if tr_lower(item.get("kisiKurum", "")) == "kurum":
        return {"tur": "tuzel", "unvan": adi, "ad": "", "soyad": "",
                "tckn": None, "vergi_no": "", "mersis_no": None}
    parcalar = adi.rsplit(None, 1)
    ad, soyad = (parcalar[0], parcalar[1]) if len(parcalar) == 2 else (adi, "")
    return {"tur": "gercek", "ad": ad, "soyad": soyad, "unvan": "",
            "tckn": None, "vergi_no": "", "mersis_no": None}


def _vekiller_kaydet(vekil_ham, Vekil, log_fn=None):
    """`item['vekil']` (ör. "[HAKAN TOLUNAY BURHAN]" ya da birden fazla vekil
    olduğunda "[AD1 SOYAD1, AD2 SOYAD2]") ayrıştırıp dedup ederek `Vekil`
    nesnelerinin LİSTESİNİ döner (kullanıcı bulgusu, 2026-07-11: eskiden yalnız
    ilki kaydedilip diğerleri atılıyordu — `DosyaTaraf.vekiller` artık M2M).
    TCKN/baro yok — yalnız ad+soyad eşleşmesiyle dedup edilir (bu uç nokta
    başka kimlik vermiyor)."""
    log = log_fn or (lambda m: None)
    metin = str(vekil_ham or "").strip()
    if metin.startswith("[") and metin.endswith("]"):
        metin = metin[1:-1].strip()
    if not metin:
        return []
    isimler = [p.strip() for p in metin.split(",") if p.strip()]
    sonuc = []
    for isim in isimler:
        parcalar = isim.rsplit(None, 1)
        ad, soyad = (parcalar[0], parcalar[1]) if len(parcalar) == 2 else (isim, "")
        if not ad:
            continue
        vekil = Vekil.objects.filter(ad=ad, soyad=soyad).first()
        sonuc.append(vekil or Vekil.objects.create(ad=ad, soyad=soyad))
    return sonuc


def _dosya_tarafi_yaz(dosya, info, rol, sira, baglar, log):
    """Bir UYAP taraf satırını bu dosyanın bağına yazar ve bağı döner
    (2026-11-14). Önce dosyanın KENDİ bağları arasında aynı rol + aynı ad
    aranır (dosya içinde ad tekildir); bulunursa:
      • gelen kimlik (TCKN/MERSİS/vergi) bağın tarafında yoksa ve o taraf
        YALNIZ bu dosyada kullanılıyorsa kimlik o tarafa yazılır;
      • taraf başka dosyalarla paylaşılıyorsa ya da kimliği ÇELİŞİYORSA bağ,
        kimliğin gerçek sahibine (yoksa yeni tarafa) TAŞINIR — bağ satırı aynı
        kaldığından kesinleşme/tebliğ alanları ve tebligat→borçlu bağlantısı
        korunur, sahte "UYAP'ta yok" uyarısı doğmaz.
    Bulunmazsa genel eşleştirme (icra_core.save_taraf) + yeni bağ.
    `baglar`: dosyanın DosyaTaraf listesi (yerinde güncellenir)."""
    from icra_models.models import Taraf, DosyaTaraf
    tur = info.get("tur") or "gercek"
    tckn = _ic._kimlik_temizle(info.get("tckn"), (11,)) if tur == "gercek" else None
    mersis = _ic._kimlik_temizle(info.get("mersis_no"), (16,)) if tur == "tuzel" else None
    vergi = _ic._kimlik_temizle(info.get("vergi_no"), (10, 11)) if tur == "tuzel" else None
    kimlikli = bool(tckn or mersis or vergi)
    anahtar = _ic._ad_anahtari_taraf(tur, info.get("ad"), info.get("soyad"), info.get("unvan"))
    es = [b for b in baglar if b.rol == rol and b.taraf.tur == tur and anahtar
          and _ic._ad_anahtari_taraf(b.taraf.tur, b.taraf.ad, b.taraf.soyad, b.taraf.unvan) == anahtar]
    if len(es) > 1:
        es = [b for b in es if b.uyapta_gorulmedi is None] or es
    if len(es) == 1:
        bag, mevcut = es[0], es[0].taraf
        kimliksiz = not (mevcut.tckn or mevcut.mersis_no or mevcut.vergi_no)
        hedef = mevcut
        if kimlikli and (kimliksiz or _ic._kimlik_celisir(mevcut, tckn, mersis, vergi)):
            sahibi = None
            for alan, deger in (("tckn", tckn), ("mersis_no", mersis), ("vergi_no", vergi)):
                if deger and sahibi is None:
                    sahibi = Taraf.objects.filter(**{alan: deger}).first()
            if sahibi is not None:
                hedef = sahibi
            elif kimliksiz and not DosyaTaraf.objects.filter(taraf=mevcut).exclude(dosya=dosya).exists():
                hedef = mevcut                       # yalnız bu dosyanın kaydı: kimlik ona yazılır
            else:
                hedef = None                         # paylaşılan ya da çelişen: yeni taraf
        if hedef is None:
            hedef = Taraf.objects.create(tur=tur, ad=info.get("ad") or "", soyad=info.get("soyad") or "",
                                         unvan=info.get("unvan") or "", tckn=tckn, mersis_no=mersis,
                                         vergi_no=vergi or "")
        _ic._taraf_guncelle(hedef, {"tur": tur, "ad": info.get("ad") or "", "soyad": info.get("soyad") or "",
                                    "unvan": info.get("unvan") or "", "tckn": tckn, "mersis_no": mersis,
                                    "vergi_no": vergi})
        if hedef.pk != mevcut.pk:
            cakisan = DosyaTaraf.objects.filter(dosya=dosya, taraf=hedef, rol=rol).exclude(pk=bag.pk).first()
            if cakisan is None:
                log(f"  … {dosya}: {rol} '{mevcut}' kaydı kimliğiyle ayrıldı "
                    f"({'TCKN' if tckn else 'vergi/MERSİS'} eşleşmesi).")
                bag.taraf = hedef
                bag.save(update_fields=["taraf"])
                from icra_models.models import DurumOlayi, durum_olayi_yaz
                durum_olayi_yaz(dosya, DurumOlayi.Tur.TARAF_KIMLIK, str(mevcut),
                                f"{hedef} ({'TCKN ' + tckn if tckn else 'vergi/MERSİS'})",
                                dosya_taraf=bag, kaynak=DurumOlayi.Kaynak.UYAP)
            else:
                bag = cakisan                        # doğru bağ zaten var
        if bag.sira != sira:
            bag.sira = sira
            bag.save(update_fields=["sira"])
        return bag
    taraf = save_taraf(info, Taraf)
    bag, _ = DosyaTaraf.objects.update_or_create(dosya=dosya, taraf=taraf, rol=rol, defaults={"sira": sira})
    if bag not in baglar:
        baglar.append(bag)
    return bag


def kaybolan_taraflari_isaretle(dosya, gorulen_bag_idleri, log_fn=None, roller=None, haric_roller=()):
    """2026-11-14 kullanıcı kararı: UYAP'ın taraf listesinde artık görünmeyen
    bağ SİLİNMEZ, `uyapta_gorulmedi` ile işaretlenir ve kullanıcı uyarılır;
    yeniden görünen bağın işareti kalkar. Yalnız TAM listesi alınan roller
    karşılaştırılır (`roller` None = hepsi; `haric_roller` listede olmayan roller).
    Döner: bu çağrıda YENİ işaretlenen taraflar ("Rol: Ad")."""
    log = log_fn or (lambda m: None)
    from django.utils import timezone
    from icra_models.models import DosyaTaraf
    qs = DosyaTaraf.objects.filter(dosya=dosya)
    if roller is not None:
        qs = qs.filter(rol__in=list(roller))
    if haric_roller:
        qs = qs.exclude(rol__in=list(haric_roller))
    from icra_models.models import DurumOlayi, durum_olayi_yaz
    geri = list(qs.filter(pk__in=gorulen_bag_idleri, uyapta_gorulmedi__isnull=False).select_related("taraf"))
    if geri:
        DosyaTaraf.objects.filter(pk__in=[d.pk for d in geri]).update(uyapta_gorulmedi=None)
        for d in geri:
            durum_olayi_yaz(dosya, DurumOlayi.Tur.TARAF_GERI_GELDI, "UYAP'ta görülmedi", "UYAP'ta var",
                            dosya_taraf=d, kaynak=DurumOlayi.Kaynak.UYAP)
    yeni = list(qs.exclude(pk__in=gorulen_bag_idleri).filter(uyapta_gorulmedi__isnull=True)
                .select_related("taraf"))
    if yeni:
        DosyaTaraf.objects.filter(pk__in=[d.pk for d in yeni]).update(uyapta_gorulmedi=timezone.now())
        for d in yeni:
            durum_olayi_yaz(dosya, DurumOlayi.Tur.TARAF_GORULMEDI, "UYAP'ta var", "UYAP'ta görülmedi",
                            dosya_taraf=d, kaynak=DurumOlayi.Kaynak.UYAP,
                            aciklama=f"{d.get_rol_display()} UYAP'ın taraf listesinde yok.")
        log("⚠️ UYAP'ta artık görünmeyen taraf (kayıt silinmedi, işaretlendi): "
            + "; ".join(f"{d.get_rol_display()}: {d.taraf}" for d in yeni)
            + f" — {dosya}. UYAP'ta kontrol edin.")
    return [f"{d.get_rol_display()}: {d.taraf}" for d in yeni]


def dosya_taraf_kaydet(dosya, ham_liste, log_fn=None, roller=None, haric_roller=()):
    """`dosya_taraf_getir`'in ham yanıtını `DosyaTaraf`'a yazar. Rol, Türkçe
    metinden `tr_lower` ile koda çevrilir (Davacı→davaci, Davalı→davali,
    Alacaklı→alacakli, Borçlu→borclu — bkz. models.DosyaTaraf.Rol). Taraf önce
    dosyanın KENDİ tarafları arasında eşleştirilir (bkz. icra_core.save_taraf):
    bu uç nokta TCKN vermediğinden, `dosya_borclu_list.ajx`'ten TCKN'siyle
    yazılmış borçluya düşer. Liste `roller`/`haric_roller` kapsamında TAM
    sayılır; kapsamda olup listede olmayan bağlar işaretlenir (bkz.
    `kaybolan_taraflari_isaretle`). Döner: kaydedilen_sayisi:int."""
    log = log_fn or (lambda m: None)
    if not isinstance(ham_liste, list) or not ham_liste:
        return 0
    _django_hazirla()
    import django
    from icra_models.models import Taraf, Vekil, DosyaTaraf
    sayac = 0
    gorulen = set()
    with django.db.transaction.atomic():
        baglar = list(dosya.taraf_baglari.select_related("taraf"))
        for idx, item in enumerate(ham_liste):
            if not isinstance(item, dict):
                continue
            info = _taraf_bilgisi_ayristir(item)
            # Kimliksiz kayıt KORUMASI (canlı DB denetimi, 2026-08-13): UYAP'ın
            # taraf_bilgileri yanıtı bazı istisnai dosya türlerinde (Merkezi
            # Takip Sistemi / Abonelik Sözleşmeleri İcra Daireleri gibi —
            # görülen dosya.tur_kod: 35, 294) ad/soyad/tckn/unvan/mersis'i
            # TAMAMEN boş bir satır döndürüyor; her çağrıda yeni hayalet Taraf
            # oluşuyordu (953 dosyada 1799 kayıt). Kimliksiz satır kaydedilmez.
            if not (info.get("tckn") or info.get("mersis_no")
                    or (info.get("unvan") or "").strip()
                    or (info.get("ad") or "").strip() or (info.get("soyad") or "").strip()):
                log(f"  ⚠️ Kimliksiz taraf kaydı atlandı (rol={item.get('rol', '')!r}).")
                continue
            vekil_objs = _vekiller_kaydet(item.get("vekil"), Vekil, log)
            rol_kod = tr_lower(item.get("rol", "")) or "diger"
            dt = _dosya_tarafi_yaz(dosya, info, rol_kod, idx, baglar, log)
            dt.vekiller.set(vekil_objs)
            gorulen.add(dt.pk)
            sayac += 1
        if sayac:
            kaybolan_taraflari_isaretle(dosya, gorulen, log, roller, haric_roller)
    return sayac


BORCLU_LISTESI_ENDPOINT = "dosya_borclu_list.ajx"


def dosya_borclu_kimlik_getir(dosya_id, log_fn=None, istek_sarici=None, timeout=60, denemeler=2):
    """`dosya_borclu_list.ajx` — icra dosyasının borçluları KİMLİKLERİYLE
    (kisiTumDVO.tcKimlikNo / kurumTumDVO vergiNo-mersisNo; bkz. icra_core.
    parse_taraf_from_uyap, teblig_21_2_core._kisi_kurum_id_bul). Hata: []."""
    log = log_fn or (lambda *a, **k: None)
    motor = SorguMotoru(log)
    try:
        veri = _post_eszamanli_korumali(istek_sarici or _bireysel_istek, motor,
                                         BORCLU_LISTESI_ENDPOINT, {"dosyaId": str(dosya_id)}, log,
                                         timeout=timeout, denemeler=denemeler)
    except Exception as e:
        log(f"⚠️ Borçlu kimlikleri alınamadı: {e}")
        return []
    return veri if isinstance(veri, list) else []


def borclu_kimliklerini_kaydet(dosya, borclu_ham, log_fn=None):
    """`dosya_borclu_kimlik_getir` yanıtını rol=borclu bağlarına yazar: aynı
    dosyadaki adı tutan kimliksiz borçluya TCKN/vergi no/MERSİS eklenir (yeni
    Taraf açılmaz). Borçlu listesi TAM sayılır — listede olmayan borçlu
    işaretlenir. Döner: kaydedilen sayı."""
    log = log_fn or (lambda m: None)
    if not isinstance(borclu_ham, list) or not borclu_ham:
        return 0
    _django_hazirla()
    import django
    from icra_models.models import Taraf, DosyaTaraf
    sayac, gorulen = 0, set()
    with django.db.transaction.atomic():
        baglar = list(dosya.taraf_baglari.select_related("taraf"))
        for idx, item in enumerate(borclu_ham):
            if not isinstance(item, dict):
                continue
            info = _ic.parse_taraf_from_uyap(item)
            if not (info.get("tckn") or info.get("mersis_no") or info.get("vergi_no")
                    or (info.get("unvan") or "").strip() or (info.get("ad") or "").strip()):
                continue
            dt = _dosya_tarafi_yaz(dosya, info, "borclu", idx, baglar, log)
            gorulen.add(dt.pk)
            sayac += 1
        if sayac:
            kaybolan_taraflari_isaretle(dosya, gorulen, log, roller=["borclu"])
    return sayac


def borclu_kimligi_eksik_mi(dosya):
    """Dosyada kimliği (TCKN / MERSİS / vergi no) olmayan borçlu var mı?"""
    return dosya.taraf_baglari.filter(rol="borclu", taraf__tckn__isnull=True,
                                      taraf__mersis_no__isnull=True, taraf__vergi_no="").exists()


def taraflari_tazele(dosya, dosya_id, log_fn=None, istek_sarici=None, timeout=90, denemeler=3):
    """Tarafları UYAP'tan okuyup yazar; icra dosyasında kimliği eksik borçlu
    kalırsa `dosya_borclu_list.ajx` ile TCKN/vergi no tamamlanır (yalnız
    gerektiğinde, +1 istek). Döner: taraf_bilgileri ham listesi (ekran için)."""
    log = log_fn or (lambda m: None)
    taraflar = dosya_taraf_getir(dosya_id, log, timeout=timeout, denemeler=denemeler,
                                 istek_sarici=istek_sarici) if dosya_id else []
    if taraflar:
        dosya_taraf_kaydet(dosya, taraflar, log)
    if dosya_id and dosya.yargi_turu == YARGI_TURU_ICRA and borclu_kimligi_eksik_mi(dosya):
        borclular = dosya_borclu_kimlik_getir(dosya_id, log, istek_sarici,
                                              timeout=min(timeout, 60), denemeler=min(denemeler, 2))
        if borclular:
            borclu_kimliklerini_kaydet(dosya, borclular, log)
    return taraflar


# ── Canlı sorgu başarısız olursa DB'ye düş (kullanıcı bulgusu, 2026-07-14:
# "eşzamanlı" reddi/ağ hatası yüzünden dosya_ayrinti_getir ya da
# dosya_taraf_getir boş dönünce ekran daha önce BAŞARIYLA kaydedilmiş veriyi
# de yok sayıp kullanıcıya çıplak hata gösteriyordu — zaten işlenmiş veri
# ASLA sessizce kaybolmamalı). Bu iki fonksiyon `IcraTakipDetay`/
# `HukukDavaDetay`/`DosyaTaraf`'tan `ham`/`taraflar` ile AYNI şekle sahip bir
# sözlük/liste kurar; `_ozet_sekmesi`/`_taraflar_sekmesi` (UI) hangi kaynaktan
# geldiğini bilmeden aynen render eder.
def _ham_db_den_olustur(dosya):
    """Kayıtlı `IcraTakipDetay`/`HukukDavaDetay`'dan `dosya_ayrinti_getir`
    şeklinde bir 'ham' sözlüğü kurar. '_metin' (tahmini açıklama) alanları
    BİLEREK YOK — o yalnız CANLI yanıtta üretilir, DB'de tutulmaz (bkz.
    `_acik_metin_bul` docstring'i); bu yüzden düşme (fallback) yolunda
    kullanıcı ham kodu görür, tahmini açıklamayı değil — yine de HİÇBİR ŞEY
    görmemekten iyidir."""
    yargi_turu = dosya.birim.yargi_turu
    if yargi_turu == 2:
        try:
            d = dosya.icra_detay
        except Exception:
            return {}
        return {
            "takibinTuru": d.takibin_turu,
            "takibinSekli": d.takibin_sekli,
            "takibinYolu": d.takibin_yolu,
            "alacakKalemToplamTutar": str(d.alacak_kalemi_toplam),
            "vekaletUcreti": str(d.vekalet_ucreti),
            "tahsilHarci": str(d.tahsil_harci),
        }
    if yargi_turu == 1:
        try:
            d = dosya.hukuk_detay
        except Exception:
            return {}
        return {
            "davaAcilisTuru": d.dava_acilis_turu,
            "davaTurleriStr": d.dava_turleri,
            "ilgiliDavaListesiStr": d.ilgili_dava_listesi,
            "durusmaTarihi": d.durusma_tarihi.isoformat() if d.durusma_tarihi else "",
        }
    return {}


def _taraflar_db_den_olustur(dosya):
    """Kayıtlı `DosyaTaraf`lardan `dosya_taraf_getir` şeklinde (adi/rol/vekil)
    bir liste kurar."""
    sonuc = []
    for dt in dosya.taraf_baglari.select_related("taraf").prefetch_related("vekiller").all():
        vekiller = ", ".join(f"{v.ad} {v.soyad}".strip() for v in dt.vekiller.all())
        sonuc.append({
            "rol": dt.get_rol_display(),
            "adi": str(dt.taraf),
            "vekil": f"[{vekiller}]" if vekiller else "",
            "uyaptaYok": dt.uyapta_gorulmedi is not None,
        })
    return sonuc


def _taraflar_kesinlesme_bilgisi_ekle(dosya, taraflar):
    """`taraflar` (dosya_taraf_getir/_taraflar_db_den_olustur şekli: rol/adi/
    vekil) listesine DB'deki `DosyaTaraf.kesinlesme_durumu`/`tebligat_durumu`
    (yalnız borçlularda anlamlı) bilgisini ekler — kullanıcı isteği
    (2026-08-04): "Dosyaya tıkladığımızda açılan pencerede tebligat ve
    kesinleşme durumunu da görmek istiyorum". Bu iki alan UYAP'tan ASLA
    gelmez (yalnız yerelde hesaplanır, bkz. kesinlesme_durumlarini_guncelle) —
    bu yüzden canlı taraf yanıtında hiç yok, DB'ye AYRICA bakılır. Rol+ad ile
    eşleştirilir (tr_lower, TCKN'siz — save_taraf'ın kendi eşleştirmesiyle AYNI
    disiplin). `taraflar` YERİNDE değiştirilir, ayrıca döner."""
    try:
        borclular = {
            tr_lower(str(dt.taraf)): dt
            for dt in dosya.taraf_baglari.select_related("taraf").filter(rol="borclu")
        }
    except Exception:
        return taraflar
    for t in taraflar:
        dt = borclular.get(tr_lower(t.get("adi", "")))
        t["kesinlesmeDurumu"] = kesinlesme_metni(dt) if dt else ""
        t["tebligatDurumu"] = tebligat_metni(dt) if dt else ""
    # 2026-11-14: UYAP'ın listesinde artık olmayan (işaretli) bağlar canlı
    # yanıtta hiç yok — kullanıcı görsün diye uyarı işaretiyle listeye eklenir.
    try:
        mevcut = {(tr_lower(t.get("rol", "")), tr_lower(t.get("adi", ""))) for t in taraflar}
        for dt in (dosya.taraf_baglari.select_related("taraf")
                   .filter(uyapta_gorulmedi__isnull=False)):
            anahtar = (tr_lower(dt.get_rol_display()), tr_lower(str(dt.taraf)))
            if anahtar in mevcut:
                for t in taraflar:
                    if (tr_lower(t.get("rol", "")), tr_lower(t.get("adi", ""))) == anahtar:
                        t["uyaptaYok"] = True
                continue
            taraflar.append({
                "rol": dt.get_rol_display(), "adi": str(dt.taraf), "vekil": "",
                "kesinlesmeDurumu": kesinlesme_metni(dt) if dt.rol == "borclu" else "",
                "tebligatDurumu": tebligat_metni(dt) if dt.rol == "borclu" else "",
                "uyaptaYok": True,
            })
    except Exception:
        pass
    return taraflar


def dosya_id_taze_coz(birim_id, dosya_no, log_fn=None, istek_sarici=None):
    """(birim_id, "YYYY/sıra") -> UYAP'tan TAZE dosyaId (Açık, sonra Kapalı
    denenir); bulunamazsa None. evrak_indirici._dosya_id_taze_coz ve
    barkod_sorgu._dosya_id_coz ile AYNI yöntem — DB'deki dosyaId oturumluktur,
    eskimiş değerle UYAP 'PRTL_GNL_10001-4 Doğrulama hatası' döndürür."""
    kayit = dosya_kaydi_taze_coz(birim_id, dosya_no, log_fn, istek_sarici)
    return (kayit or {}).get("dosyaId") or None


def dosya_kaydi_taze_coz(birim_id, dosya_no, log_fn=None, istek_sarici=None, tur_kod=None):
    """`dosya_id_taze_coz` ile aynı arama, ama UYAP'ın TÜM arama kaydını döner
    (dosyaId + dosyaDurum/dosyaDurumKod + künye); bulunamazsa None. 2026-09-30
    kullanıcı bulgusu (2026/8220): dosya UYAP'ta kapanmıştı, "Canlı Veriyi
    Getir" sonrası hâlâ Açık görünüyordu — bu arama durumu zaten getiriyordu
    ama yalnız dosyaId alınıp gerisi atılıyordu. `tur_kod` verilirse (aynı
    numaralı Esas/Talimat) o türdeki kayıt tercih edilir."""
    log = log_fn or (lambda m: None)
    _django_hazirla()
    from icra_models.models import Birim
    try:
        birim = Birim.objects.get(birim_id=str(birim_id))
    except Birim.DoesNotExist:
        return None
    if not re.match(r"^\s*\d{4}\s*/\s*\d+\s*$", str(dosya_no or "")):
        return None
    values = {"dosyaYil": str(dosya_no).split("/", 1)[0].strip(), "dosyaNo": str(dosya_no)}
    motor = SorguMotoru(log)
    for durum_kod in (0, 1):
        payload = build_payload_genel(values, birim.yargi_turu, birim.turu2, durum_kod, birim_id=str(birim_id))
        try:
            veri = _post_eszamanli_korumali(istek_sarici or _bireysel_istek, motor, ENDPOINT, payload, log)
            kayitlar = veri[0] if isinstance(veri, list) and veri else []
        except Exception as e:
            log(f"… taze dosyaId çözülemedi: {e}")
            return None
        kayitlar = [k for k in kayitlar if isinstance(k, dict)] if isinstance(kayitlar, list) else []
        if tur_kod is not None:
            kayitlar = ([k for k in kayitlar if str(k.get("dosyaTurKod", "")) == str(tur_kod)]
                        or kayitlar)
        if kayitlar and kayitlar[0].get("dosyaId"):
            return kayitlar[0]
    return None


def _canli_durum_kaydet(dosya, kayit, log):
    """"Canlı Veriyi Getir"de dosyanın durumunu (Açık/Kapalı/"Takibe İtiraz")
    `dosya_kaydi_taze_coz`'un arama kaydından yazar (ek istek yok). Hata kartı
    bozmaz — loglanır. Döner: tazelenmiş `Dosya`."""
    if int(kayit.get("dosyaTurKod", 0) or 0) != dosya.tur_kod:
        log(f"… arama kaydının dosya türü ({kayit.get('dosyaTur')}) bu dosyayla eşleşmiyor — durum yazılmadı.")
        return dosya
    try:
        from icra_models.ingest import dosya_kunyesi_kaydet
        eski_durum = dosya.durum or ""
        dosya, _ = dosya_kunyesi_kaydet(kayit, yargi_turu=dosya.yargi_turu)
        if (dosya.durum or "") != eski_durum:
            log(f"… dosya durumu UYAP'ta değişmiş: '{eski_durum}' → '{dosya.durum}' (kaydedildi).")
    except Exception as e:
        log(f"⚠️ Dosya durumu kaydedilemedi: {e}")
    return dosya


def _canli_kesinlesme_tazele(dosya, log):
    """İcra'da borçluların kesinleşmesini yeniden hesaplar (dosya durumu
    "Takibe İtiraz"a dönmüş/çıkmış olabilir). UYAP'a istek atmaz. Hata loglanır."""
    if dosya.yargi_turu != 2:
        return
    try:
        kesinlesme_durumlarini_guncelle(dosya_obj=dosya, log_fn=log)
    except Exception as e:
        log(f"⚠️ Kesinleşme yeniden hesaplanamadı: {e}")


def dosya_detay_db_den(rec, log_fn=None):
    """"Dosya Görüntüle"nin VARSAYILAN, UYAP'a HİÇ GİTMEYEN yolu (kullanıcı
    kararı, 2026-09-24: "Önce veritabanını göster. Canlı veriyi ise bir tuş ile
    getir. İleride bu dosya kartında çok fazla veri olacak"). Canlı yol
    (`dosya_detay_goster_ve_kaydet`) en az 3 ardışık UYAP isteği + istekler
    arası 1 sn bekleme + olası 'eşzamanlı' tekrar denemeleri demek; kart her
    açılışta saniyelerce bekletiyordu. Dönüş şekli canlı yolla AYNI:
    (ham, aile, kaydedildi=False, hata, taraflar). `ham` kayıtlı
    IcraTakipDetay/HukukDavaDetay'dan kurulur ('_metin' tahmini açıklamaları
    YOK — bkz. `_ham_db_den_olustur`); `ham["_db_den"]` True olur. Dosya DB'de
    yoksa `hata` doludur — UI yine de pencereyi açıp canlı getirmeyi sunmalı."""
    log = log_fn or (lambda m: None)
    dosya = _dosya_rec_ten_bul(rec, log)
    if dosya is None:
        return {}, None, False, ("Dosya yerel veritabanında bulunamadı — "
                                  "'Canlı Veriyi Getir' ile UYAP'tan alınabilir."), []
    aile = {2: "icra", 1: "hukuk"}.get(dosya.birim.yargi_turu)
    ham = _ham_db_den_olustur(dosya)
    ham["_db_den"] = True
    taraflar = _taraflar_db_den_olustur(dosya)
    if taraflar:
        taraflar = _taraflar_kesinlesme_bilgisi_ekle(dosya, taraflar)
    return ham, aile, False, None, taraflar


def _dosya_rec_ten_bul(rec, log):
    """Liste kaydından (`rec`) yerel `Dosya`yı bulur; yoksa None. Önce kalıcı
    `dbId` (Dosya.pk — dosyalarim_db_listele 2026-09-24'ten beri verir), yoksa
    doğal anahtar (birim+yıl+sıra+tür). UYAP'a GİTMEZ."""
    try:
        _django_hazirla()
        from icra_models.models import Dosya
        from icra_models.ingest import _yil_sira
        db_id = (rec or {}).get("dbId")
        if db_id:
            return Dosya.objects.select_related("birim").get(pk=db_id)
        birim_id = str((rec or {}).get("birimId", "") or "")
        yil, sira = _yil_sira((rec or {}).get("dosyaNo"))
        tur_kod = int((rec or {}).get("dosyaTurKod", 0) or 0)
        return Dosya.objects.select_related("birim").get(
            birim__birim_id=birim_id, yil=yil, sira_no=sira, tur_kod=tur_kod)
    except Exception as e:
        log(f"… dosya yerel veritabanında bulunamadı: {e}")
        return None


def islem_durumu_degistir(rec, durum, notu="", bekleme_bitis=None, log_fn=None):
    """Dosya kartındaki elle değişiklik (Aktif/Pasif/Beklemede) — yalnız
    yerel DB, UYAP'a gitmez. Başarılıysa güncel `Dosya`, değilse None."""
    log = log_fn or (lambda m: None)
    dosya = _dosya_rec_ten_bul(rec, log)
    if dosya is None:
        return None
    dosya.islem_durumu_ata(durum, notu, bekleme_bitis)
    log(f"✓ {dosya.dosya_no}: işlem durumu → {dosya.get_islem_durumu_display()}")
    return dosya


def dosya_detay_goster_ve_kaydet(rec, log_fn=None, istek_sarici=None):
    """Tek giriş noktası (UI'ların çağıracağı): ham arama kaydından (`rec` —
    `search_phrase_detayli.ajx` satırı, TAZE 'dosyaId' içermeli) ayrıntı VE
    taraf bilgilerini çeker, ilgili `Dosya`'yı doğal anahtarıyla
    (birim+yıl+sıra+tür) bulur ve kaydeder. Döner: (ham:dict, aile:str|None,
    kaydedildi:bool, hata:str|None, taraflar:list) — `hata` doluysa
    `ham`/`aile` boş/None olabilir, UI kullanıcıya `hata`yı göstermeli.
    `taraflar`, `dosya_taraf_getir`'in ham (UYAP şekilli) listesidir; ayrıntı
    başarısız olsa bile boş liste olarak döner (asla None). `istek_sarici`:
    bkz. `dosya_ayrinti_getir` docstring'i — VARSAYILAN `_bireysel_istek`
    (kullanıcının doğrudan tıklaması); düşük öncelikli arka plan çağıranları
    `_arka_plan_istek` vermeli (bkz. `DosyaSorgu`).

    CANLI SORGU BAŞARISIZ OLURSA (bkz. modül düzeyi not, _ham_db_den_olustur):
    daha önce kaydedilmiş DB verisi varsa ONA düşülür — kullanıcı asla
    'zaten işlediğimiz veri' yüzünden boş ekran görmez. Bu durumda dönen
    `ham` sözlüğünde `_onbellekten` anahtarı True olur (UI bunu bir uyarı
    şeridi göstermek için kullanabilir); `kaydedildi` False kalır (yeni bir
    şey YAZILMADI, yalnız gösterildi)."""
    log = log_fn or (lambda m: None)
    dosya_id = str((rec or {}).get("dosyaId", "") or "")

    # Dosya kaydı YALNIZ `rec`'ten (birim/yıl/sıra/tür) bulunur — `ham`'a
    # bağımlı DEĞİL, bu yüzden canlı sorgu tamamen başarısız olsa bile önce
    # bu arama yapılabilir (fallback'in ön koşulu).
    dosya = _dosya_rec_ten_bul(rec, log)
    if dosya is None:
        log("… (fallback devre dışı kalacak)")

    # Kullanıcı bulgusu (2026-09-23, 2024/11435): liste ekranları satırı DB'den
    # okuyor, `dosyaId` de DB'deki ESKİ oturumun değeri — UYAP bununla
    # "PRTL_GNL_10001-4 Doğrulama hatası" veriyor, kart UYAP bağlıyken bile
    # "canlı veri alınamadı" diyordu. dosyaId DB'dekiyle aynıysa (ya da yoksa)
    # önce birim+dosya no ile TAZE kimlik çözülür (tek arama isteği).
    # 2026-09-30: arama HER ZAMAN yapılır (eskiden yalnız dosyaId eskiyse) —
    # kaydı dosyanın güncel durumunu (Açık/Kapalı/Takibe İtiraz) taşır.
    arama_kaydi = None
    if dosya is not None:
        arama_kaydi = dosya_kaydi_taze_coz(dosya.birim.birim_id, (rec or {}).get("dosyaNo"), log,
                                           istek_sarici, tur_kod=dosya.tur_kod)
        if arama_kaydi:
            dosya_id = str(arama_kaydi["dosyaId"])
            rec["dosyaId"] = dosya_id   # Evraklar sekmesi vb. aynı taze kimliği kullansın
            dosya = _canli_durum_kaydet(dosya, arama_kaydi, log)
        else:
            log("… taze dosyaId çözülemedi — kayıtlı kimlikle deneniyor.")

    if not dosya_id and dosya is None:
        return {}, None, False, ("Bu kayıtta 'dosyaId' yok (DB önbelleğinden "
                                  "geliyor olabilir) — listeyi yenileyip tekrar deneyin."), []

    ham = dosya_ayrinti_getir(dosya_id, log, istek_sarici) if dosya_id else {}
    canli_basarisiz = not ham
    if canli_basarisiz and dosya is not None:
        onbellek = _ham_db_den_olustur(dosya)
        if onbellek:
            log("⚠️ Canlı dosya ayrıntısı alınamadı — yerel veritabanındaki "
                "önceki kayıt gösteriliyor (bu tur DB'ye yeni bir şey yazılmadı).")
            ham = onbellek
            ham["_onbellekten"] = True

    if not ham:
        if dosya is None:
            return {}, None, False, "Dosya ayrıntısı alınamadı (bağlantı/oturum sorunu olabilir).", []
        # Kullanıcı bulgusu (2026-09-23, İzmir 7. İcra 2024/11435): toplu
        # taramanın indirdiği borçlu/alacaklı DosyaTaraf'ta kayıtlıyken
        # (IcraTakipDetay ise hiç yazılmamışken — canlı DB'de 6798 borçlulu
        # İcra dosyasının 6780'i böyle) canlı ayrıntı gelmeyince taraflar da
        # [] dönüyor, "Taraflar" sekmesi hiç açılmıyordu. Taraflar UYAP'sız
        # DB'den gösterilir; canlı taraf sorgusu DENENMEZ (ayrıntı zaten
        # başarısız — aynı dosyaId ile dakikalarca bekletmenin anlamı yok).
        aile = {2: "icra", 1: "hukuk"}.get(dosya.birim.yargi_turu)
        taraflar = _taraflar_db_den_olustur(dosya)
        if taraflar:
            taraflar = _taraflar_kesinlesme_bilgisi_ekle(dosya, taraflar)
            log("⚠️ Canlı dosya ayrıntısı alınamadı — yerel veritabanındaki taraflar gösteriliyor.")
            return {}, aile, False, ("Canlı dosya ayrıntısı UYAP'tan alınamadı (bağlantı/oturum). "
                                      "Taraflar yerel veritabanından gösteriliyor."), taraflar
        return {}, aile, False, ("Dosya ayrıntısı alınamadı ve yerel veritabanında da "
                                  "önbelleğe alınmış bir kayıt yok (henüz hiç görüntülenmemiş olabilir)."), []
    if dosya is None:
        return ham, None, False, ("Dosya yerel veritabanında bulunamadı (önce "
                                   "Sorgula/senkron ile kaydedilmeli olabilir)."), []

    if canli_basarisiz:
        aile = {2: "icra", 1: "hukuk"}.get(dosya.birim.yargi_turu)
        kaydedildi = False
    else:
        aile, kaydedildi = dosya_ayrinti_kaydet(dosya, ham, log)
    # İcra'da bu uç nokta CANLI DOĞRULANDI (2026-07-11, İzmir Banka Alacakları
    # İcra Dairesi 2026/89122 dosyası, avukat.uyap.gov.tr Taraf Bilgileri
    # sekmesi): rol string'leri "Alacaklı"/"Borçlu" — tr_lower() ile icra_core.
    # py'nin DosyaTaraf.Rol seçenekleriyle ("alacakli"/"borclu") BİREBİR
    # eşleşiyor. Ayrıca 'dosya_borclu_list.ajx' (icra_core.py'nin kendi
    # akışı) ile karşılaştırıldı: aynı kişi için adi/soyadi alanları harf
    # harf aynı ("FİKRİ"/"BOZKUŞ") ve her iki update_or_create çağrısı da
    # aynı (dosya, taraf, rol) anahtarını kullanıyor — bu yüzden save_taraf'ın
    # TCKN'siz (ad,soyad) eşleşmesi icra_core'un TCKN'li kaydettiği AYNI Taraf
    # satırına düşüyor (mevcut tckn'ye dokunulmuyor, sira/vekil güncelleniyor).
    # icra_core.py yalnız rol="borclu" yazıyor; bu akış ayrıca "alacakli"
    # satırlarını da (ilk kez) ekliyor — DosyaTaraf.objects.update_or_create
    # hiçbir zaman silme yapmadığından en kötü ihtimalde kozmetik bir
    # yinelenen satır oluşur, mevcut kanıtlı veri kaybolmaz.
    try:
        taraflar = taraflari_tazele(dosya, dosya_id, log, istek_sarici)
    except Exception as e:
        taraflar = []
        log(f"⚠️ Taraf bilgileri kaydedilemedi: {e}")
    if taraflar:
        log(f"… {len(taraflar)} taraf canlı olarak alındı: "
            + ", ".join(f"{t.get('rol', '')}:{t.get('adi', '')}" for t in taraflar))
    elif dosya.taraf_baglari.exists():
        log("⚠️ Canlı taraf bilgisi alınamadı (boş yanıt) — yerel veritabanındaki önceki taraflar gösteriliyor.")
        taraflar = _taraflar_db_den_olustur(dosya)
    else:
        log("… canlı taraf sorgusu boş döndü ve yerel veritabanında da önceki taraf kaydı yok "
            "(Taraflar sekmesi bu yüzden hiç görünmeyecek).")
    # Taraflar kaydedildikten SONRA — aşağıdaki kesinleşme metni yeni hesabı göstersin.
    _canli_kesinlesme_tazele(dosya, log)
    if taraflar:
        taraflar = _taraflar_kesinlesme_bilgisi_ekle(dosya, taraflar)
    return ham, aile, kaydedildi, None, taraflar


# Stale-while-revalidate ("Dosya Görüntüle" için önbellek+arka plan tazeleme)
# GERİ ALINDI (kullanıcı bulgusu, 2026-07-13): önbellekli 'ham' takibin türü/
# şekli/yolu için yalnız ÇIPLAK UYAP kodunu ("1"/"0" vb.) içerebiliyordu —
# okunabilir '(tahmini)' açıklama metni BİLEREK DB'ye yazılmıyor (bkz.
# dosya_ayrinti_kaydet, UYDURULMAZ disiplini), yalnız CANLI yanıtta
# üretiliyor. Sessiz arka plan tazelemesi bu metni kullanıcıya bir daha HİÇ
# göstermiyordu — gerçek bir regresyondu. "Dosya Görüntüle" bu yüzden HER
# ZAMAN canlı sorguya düşer (bkz. dosya_detay_goster_ve_kaydet) — yalnız
# `dosyalarim_db_listele` (liste ekranı, zaten UYAP'a hiç gitmiyordu) hızlı
# kalır.


# ── Genel "Dosyalarım" (çoklu yargı türü) — DB tarama + filtre ──────────────
# (docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §7 "Kalan" — filtre UI)
# İcra Dosyalarım'ın aksine bu ekran CANLI UYAP sorgusu yapmaz (yalnız DB
# okur) — SenkronKapsami'nin (Faz 3/4) zaten doldurduğu veriyi yargı
# türü/birimi/dosya türü/durum/tarih aralığına göre tarar. Tazelemek isteyen
# kullanıcı "Yenile" ile `dosyalarim_yenile` (= DosyaSorgu.calistir, arka plan
# zamanlayıcısıyla AYNI mantık, yalnız HEMEN) çağırır, sonra DB yeniden okunur.
def dosya_tur_secenekleri(yargi_turu=None):
    """Dosya Türü seçeneklerini döner: list[(kod, ad)].
    `Dosya.Tur` (models.py) sabit enum'u yalnız canlı doğrulanan birkaç değeri
    kapsar (0/1=Esas/Talimat → İcra'ya özgü, 14/15=Hukuk Değişik İş/Hukuk Dava
    → Hukuk'a özgü) ve yargı türüne göre AYRIM YAPMAZ — bu yüzden hangi Yargı
    Türü seçilirse seçilsin aynı 4 seçenek listeleniyordu (kullanıcı bulgusu,
    2026-07-11). Burada onun yerine yerel DB'deki GERÇEK kayıtlardan, verilen
    `yargi_turu`ya ait DISTINCT (tur_kod, tur) çiftleri okunur — `tur` UYAP'ın
    kendi serbest-metin etiketidir, `durum`/`durum_kod` ile aynı desen (bkz.
    models.py Dosya.durum yorumu). `yargi_turu=None` ise tüm DB taranır.
    O tür için DB'de hiç kayıt yoksa (henüz senkron edilmemiş) statik
    `Dosya.Tur` listesine düşülür — uydurma değil, en azından bilinen
    kodları göstermek için."""
    _django_hazirla()
    from icra_models.models import Dosya
    qs = Dosya.objects.all()
    if yargi_turu not in (None, ""):
        qs = qs.yargi(yargi_turu)
    satirlar = (qs.exclude(tur="")
                  .values_list("tur_kod", "tur").distinct().order_by("tur_kod"))
    secenekler = {}
    for kod, ad in satirlar:
        secenekler.setdefault(kod, ad)
    if secenekler:
        return sorted(secenekler.items())
    return [(v, l) for v, l in Dosya.Tur.choices]


def dosya_durum_secenekleri():
    """`Dosya.Durum` (models.py) seçeneklerini döner: list[(kod, ad)]."""
    _django_hazirla()
    from icra_models.models import Dosya
    return [(v, l) for v, l in Dosya.Durum.choices]


TARAF_SUTUN_SAYISI = 4
# Sabit görüntü sütunu sayısı — kullanıcı bulgusu (2026-07-12): taraflar TEK
# bir metin sütununda BİRLEŞTİRİLMEMELİ, her taraf kendi sütununda ayrı ayrı
# görünmeli ki taraf adına göre filtreleme anlamlı olsun. Canlı DB'de şimdiye
# dek gözlenen azami taraf sayısı 3'tür; pay bırakmak için 4 sütun ayrıldı.
# Bundan fazlası olursa (ör. çok taraflı CBS/arabuluculuk dosyası) taşan
# taraflar SON sütuna ("; " ile) eklenir — hiçbiri sessizce atılmaz. Alacaklı/
# Borçlu bu 4 sütuna hiç girmez (kendi ayrı "alacakli"/"borclu" sütunlarında
# gösteriliyorlar, bkz. `_taraflar_sutunlari` içindeki filtre) — bütçe yalnız
# kalan (üçüncü şahıs/vekil dışı taraf) sayısına göre işler.


class _YalinTaraf:
    """Liste okuması için `Taraf`ın hafif eşi (2026-09-25 hız çalışması: 44 bin
    satırda Django model nesnesi kurmak sürenin %80'iydi). `str()` ve alanlar
    `Taraf` ile birebir — `Taraf.__str__` aynen kullanılır."""
    __slots__ = ("pk", "tur", "ad", "soyad", "unvan", "tckn", "mersis_no", "vergi_no")
    Tur = None      # ilk kullanımda Taraf.Tur atanır (bkz. _yalin_taraf_baglari)

    def __init__(self, *degerler):
        for ad, deger in zip(self.__slots__, degerler):
            setattr(self, ad, deger)

    def __str__(self):
        return type(self)._taraf_str(self)


class _YalinVekiller:
    """`dt.vekiller.all()` eşi — yalnız gerektiğinde (kalabalık icra dosyasında
    kendi vekil önceliği) tek sorguyla okunur."""
    __slots__ = ("_bag_id",)

    def __init__(self, bag_id):
        self._bag_id = bag_id

    def all(self):
        from icra_models.models import Vekil
        return list(Vekil.objects.filter(temsil_baglari__id=self._bag_id))


class _YalinBag:
    """Liste okuması için `DosyaTaraf`ın hafif eşi — listelerin kullandığı alanlar
    ve `get_*_display()` metotları aynı sonucu verir."""
    __slots__ = ("pk", "rol", "sira", "taraf", "kesinlesme_durumu", "kesinlesme_tarihi", "tebligat_durumu",
                 "tebligat_durum_tarihi", "tebligat_sekli", "vekiller")
    _KES = _TEB = None

    def __init__(self, pk, rol, sira, taraf, kes, kes_tarih, teb, teb_tarih, teb_sekli=""):
        self.pk, self.rol, self.sira, self.taraf = pk, rol, sira, taraf
        self.kesinlesme_durumu, self.kesinlesme_tarihi = kes, kes_tarih
        self.tebligat_durumu, self.tebligat_durum_tarihi, self.tebligat_sekli = teb, teb_tarih, teb_sekli
        self.vekiller = _YalinVekiller(pk)

    def get_kesinlesme_durumu_display(self):
        return str(self._KES.get(self.kesinlesme_durumu, self.kesinlesme_durumu))

    def get_tebligat_durumu_display(self):
        return str(self._TEB.get(self.tebligat_durumu, self.tebligat_durumu))


def _yalin_taraf_baglari(dosya_qs):
    """`dosya_qs`'teki dosyaların taraf bağlarını TEK sorguda, model nesnesi
    kurmadan okur: {Dosya.pk: [_YalinBag, ...]} (dosya içinde sira sırasıyla).
    Eskiden `prefetch_related("taraf_baglari__taraf")` 25 bin bağ için tam
    Django nesnesi kuruyordu."""
    from icra_models.models import DosyaTaraf, Taraf
    if _YalinTaraf.Tur is None:
        _YalinTaraf.Tur = Taraf.Tur
        _YalinTaraf._taraf_str = Taraf.__str__
        _YalinBag._KES = dict(DosyaTaraf.KesinlesmeDurumu.choices)
        _YalinBag._TEB = dict(DosyaTaraf.TebligatDurumu.choices)
    harita = {}
    satirlar = (DosyaTaraf.objects.filter(dosya_id__in=dosya_qs.order_by().values("id"))
                .order_by("dosya_id", "sira", "id")
                .values_list("dosya_id", "id", "rol", "sira", "kesinlesme_durumu", "kesinlesme_tarihi",
                             "tebligat_durumu", "tebligat_durum_tarihi", "tebligat_sekli", "taraf_id",
                             "taraf__tur", "taraf__ad", "taraf__soyad", "taraf__unvan", "taraf__tckn",
                             "taraf__mersis_no", "taraf__vergi_no"))
    for (did, bid, rol, sira, kes, kes_t, teb, teb_t, teb_s, *taraf) in satirlar:
        harita.setdefault(did, []).append(
            _YalinBag(bid, rol, sira, _YalinTaraf(*taraf), kes, kes_t, teb, teb_t, teb_s))
    return harita


def _taraflar_sutunlari(baglar, yargi_turu):
    """Bir dosyanın taraf bağlarından (`baglar`, sira sırasıyla)
    `TARAF_SUTUN_SAYISI` uzunluğunda bir liste üretir; her eleman "Rol: Ad
    Soyad" biçiminde TEK bir tarafı temsil eder (boş kalan slotlar ""). Rol
    etiketi `DosyaTaraf.Rol` seçeneklerinden (bilinenler); listede olmayan
    roller (ör. arabuluculuk/CBS'e özgü "talep eden", "katilan" — kullanıcı
    bulgusu, 2026-07-12: yargı türüne göre rol kümesi değişiyor) ham rol
    metninin ilk harfi büyütülerek gösterilir — uydurma değil, DB'deki gerçek
    metnin biçimi.

    İcra dosyalarında (`YARGI_TURU_ICRA`) taraf sayısı sütun bütçesini
    aşıyorsa (ör. onlarca borçlulu konsolide dosya) ham `sira` yerine
    ÖNCELİK sırası kullanılır: (Ayarlar'da kendi vekil adı tanımlıysa) vekili
    olduğumuz taraf önce gelir — kullanıcı bulgusu, 2026-07-12: kalabalık
    İcra dosyasında önemli taraflar rastgele 'taşan' sütuna gömülüp fiilen
    görünmez oluyordu. Alacaklı/Borçlu bu önceliklendirmeye artık dahil
    DEĞİL çünkü `baglar`'dan zaten çıkarılıyorlar (kendi ayrı sütunlarında
    gösteriliyorlar, bkz. aşağıdaki filtre). Diğer yargı türlerinde ve az
    tarafli dosyalarda davranış DEĞİŞMEZ (ham sıra)."""
    from icra_models.models import DosyaTaraf
    rol_etiket = dict(DosyaTaraf.Rol.choices)
    # Alacaklı/Borçlu artık `dosyalarim_db_listele`'de kendi ayrı sütunlarında
    # ("alacakli"/"borclu") gösteriliyor — burada TEKRAR göstermemek için bu
    # iki rol çıkarılır (kullanıcı bulgusu, 2026-07-13: "Alacaklı: X" hem
    # kendi sütununda hem taraf1'de aynı anda görünüyordu, "bütünlük yok"
    # şikayetinin asıl nedeni buydu). İcra dışı yargı türlerinde bu roller
    # hiç oluşmadığından bu satır etkisizdir (no-op).
    baglar = [dt for dt in baglar if dt.rol not in ("alacakli", "borclu", "davaci", "davali")]
    parcalar = [f"{rol_etiket.get(dt.rol, dt.rol.title())}: {dt.taraf}" for dt in baglar]

    sirali = parcalar
    if yargi_turu == YARGI_TURU_ICRA and len(baglar) > TARAF_SUTUN_SAYISI:
        kendi = _kendi_vekil_adlari()
        bizim_idx = None
        if kendi:
            for i, dt in enumerate(baglar):
                if any(tr_lower(f"{v.ad} {v.soyad}".strip()) in kendi for v in dt.vekiller.all()):
                    bizim_idx = i
                    break

        if bizim_idx is not None:
            kalanlar = [i for i in range(len(parcalar)) if i != bizim_idx]
            sirali = [parcalar[bizim_idx]] + [parcalar[i] for i in kalanlar]

    sutunlar = sirali[:TARAF_SUTUN_SAYISI] + [""] * max(0, TARAF_SUTUN_SAYISI - len(sirali))
    if len(sirali) > TARAF_SUTUN_SAYISI:
        sutunlar[TARAF_SUTUN_SAYISI - 1] = "; ".join(sirali[TARAF_SUTUN_SAYISI - 1:])
    return sutunlar


def mahkeme_secenekleri(yargi_turu=None, yargi_birimi_kod=None):
    """BELİRLİ mahkeme/icra dairesi (`Birim`) seçeneklerini yerel DB'den döner:
    list[{"birimId","ad"}]. 'Yargı Birimi' filtresi mahkeme TÜRÜNÜ süzer (ör.
    'Asliye Hukuk Mahkemesi' — bkz. `yargi_birimleri_getir_veya_db`); bu ise
    aynı türdeki BELİRLİ mahkemeyi süzer (ör. 'ANKARA 4. ASLİYE HUKUK
    MAHKEMESİ') — kullanıcı bulgusu, 2026-07-12: 'yargı türü ve yargı birimi
    var fakat mahkeme ile filtreleme yok'. `yargi_turu`/`yargi_birimi_kod`
    verilirse Birim.yargi_turu/turu2'ye göre daraltılır (Yargı Türü/Yargı
    Birimi seçimine göre kademeli doldurulması için — ekranın diğer
    dropdown'larıyla AYNI desen). DB erişilemezse [] döner."""
    try:
        _django_hazirla()
        from icra_models.models import Birim
        qs = Birim.objects.all()
        if yargi_turu not in (None, ""):
            qs = qs.filter(yargi_turu=int(yargi_turu))
        if yargi_birimi_kod:
            qs = qs.filter(turu2=yargi_birimi_kod)
        return [{"birimId": b.birim_id, "ad": b.ad} for b in qs.order_by("ad")]
    except Exception:
        return []


def taraf_adi_secenekleri(rol, yargi_turu=None):
    """Belirli bir rol (`DosyaTaraf.Rol` kodu, ör. "alacakli"/"borclu") için
    DB'deki BENZERSİZ taraf görünen adlarını (`str(Taraf)`) döner — Alacaklı/
    Borçlu arama kutusunun açılır listesini doldurmak için (kullanıcı isteği:
    serbest metnin yanında var olan isimler de seçilebilsin). `yargi_turu`
    verilirse yalnız o türdeki dosyaların tarafları (bu liste şu an yalnız
    İcra ekranında kullanılıyor). Türkçe harf sırasına göre sıralanır
    (`tr_lower` — bkz. dosyalarim_db_listele'deki aynı gerekçe). DB
    erişilemezse [] döner."""
    try:
        _django_hazirla()
        from icra_models.models import DosyaTaraf
        qs = DosyaTaraf.objects.filter(rol=rol).select_related("taraf")
        if yargi_turu is not None:
            qs = qs.filter(dosya__yargi_turu=int(yargi_turu))
        isimler = {str(dt.taraf) for dt in qs if dt.taraf_id}
        return sorted((i for i in isimler if i), key=tr_lower)
    except Exception:
        return []


def _taraf_isim_eslesir(taraf, aranan_lower):
    """Bir `Taraf`ın ad/soyad/unvanının `aranan_lower` (zaten `tr_lower`
    ile küçültülmüş) metni içerip içermediğini denetler. `dosyalarim_db_
    listele`'nin taraf_adi/alacakli_adi/borclu_adi filtreleri için Python'da
    çalışır — DB (gömülü PostgreSQL, `initdb --locale=C`, bkz. db_baslat.py)
    `icontains`'i Postgres'in yerel `ILIKE`'ına çevirir; `C` yerelinde ILIKE
    yalnız ASCII harfleri büyük/küçük eşler ('İ/I/ı/Ş/ş/Ğ/ğ/Ü/ü/Ö/ö/Ç/ç'
    içeren adlarda DB'de birebir var olan bir isim girilse bile eşleşme
    BULAMIYORDU — kullanıcı bulgusu, 2026-07-12; canlı veriyle DOĞRULANDI:
    gerçek bir borçlunun kendi soyadını küçük harfle arattığında eski
    icontains 0 sonuç, bu fonksiyon 3/3 doğru sonuç verdi). `tr_lower` bu
    harfleri katlayarak karşılaştırdığından büyük/küçük harf hiçbir zaman
    zorunlu değildir."""
    if not aranan_lower:
        return True
    adaylar = (taraf.ad, taraf.soyad, taraf.unvan, f"{taraf.ad} {taraf.soyad}".strip())
    return any(aranan_lower in tr_lower(a) for a in adaylar if a)


# 2026-09-25: liste ekranlarının 20 sn'lik otomatik tazelemesi ve onun
# değişiklik imzası (`db_degisiklik_imzasi` / `OtoYenileBekcisi`) kullanıcı
# kararıyla KALDIRILDI — tüm tabloları 20 sn'de bir tarıyordu.


def dosyalarim_db_listele(filtreler=None):
    """Yerel DB'deki `Dosya` kayıtlarını (SenkronKapsami'nin doldurduğu)
    isteğe bağlı filtrelerle döner. `filtreler`: {"yargi_turu": int|None,
    "yargi_birimi_kod": str|None, "mahkeme_id": str|None (bkz.
    `mahkeme_secenekleri` — BELİRLİ mahkeme/icra dairesi, `Birim.birim_id`),
    "tur_kod": int|None, "durum_kod": int|None,
    "tarih_baslangic": "GG.AA.YYYY"|None, "tarih_bitis": "GG.AA.YYYY"|None,
    "taraf_adi": str|None (rol'den BAĞIMSIZ, Taraf.ad/soyad/unvan'da Türkçe-
    duyarlı alt-metin araması — Hukuk/Ceza gibi davacı/davalı/sanık rolleri
    için), "alacakli_adi": str|None, "borclu_adi": str|None (rol="alacakli"/
    "borclu" ile SINIRLI aynı arama — İcra dosyalarında belirli tarafa göre
    arama; kullanıcı bulgusu, 2026-07-12: yalnız genel 'Taraf Adı' vardı,
    İcra'da alacaklı/borçlu AYRI ARANAMIYORDU. İkisi birden verilirse AYRI
    taraf_baglari eşleşmeleri aranır (aynı satırda olmaları ŞART DEĞİL) —
    "bu alacaklı VE bu borçlunun birlikte geçtiği dosya" sorgusunu doğal
    olarak destekler. Arama büyük/küçük harf VE Türkçe harf (İ/I/ı/Ş/Ğ/Ü/Ö/Ç)
    duyarsızdır — bkz. `_taraf_isim_eslesir`, kullanıcı bulgusu 2026-07-12:
    DB'de birebir var olan bir ad girilse bile eski SQL icontains eşleşme
    bulamıyordu).}.
    DB erişilemezse [] döner. Her kayıt "dosyaId" içerir ama bu DB'den okunan
    OLABİLİR eski bir değer (bkz. plan §7 Faz 5 "kabul edilmiş bilinmeyen") —
    'Dosya Görüntüle' güncel olmayabileceğini varsayıp hata durumunda
    kullanıcıyı 'Yenile'ye yönlendirmelidir. Her kayıtta ayrıca "taraf1".."taraf{N}"
    (N=`TARAF_SUTUN_SAYISI`, bkz. `_taraflar_sutunlari`) alanları bulunur — her
    biri TEK bir tarafı temsil eder (birleştirilmiş özet metin DEĞİL, kullanıcı
    bulgusu 2026-07-12). Taraf bilgisi yalnız UYAP'tan taraf ayrıntısı çekilmiş
    dosyalarda dolu olur (bkz. `DosyaSorgu.calistir` — artık bulk 'Yenile'
    sırasında da çekilir), henüz çekilmemişse boş kalır (uydurma yok). Ayrıca
    "alacakli"/"borclu" alanları bulunur: rol="alacakli"/"borclu" olan TÜM
    tarafların virgülle birleştirilmiş adları (İcra Dosyalarım'daki
    `icra_core.db_dosyalari_getir` ile AYNI türetme, kullanıcı bulgusu
    2026-07-13: taraf1..4 genel sütunlarında gömülüydü ama tek bakışta
    ayırt edilemiyordu); İcra dışındaki yargı türlerinde bu rol'ler hiç
    oluşmadığından boş kalır. Ayrıca "kesinlesme_durumu" ve "tebligat_durumu":
    borçlu başına `DosyaTaraf` alanlarından (bkz. `borclu_durum_metinleri`;
    2026-09-25'ten beri tebliğ = borçlunun ÖDEME EMRİ durumu, dosyanın en yeni
    barkodu değil — `kesinlesme_durumlarini_guncelle` yazar)."""
    filtreler = filtreler or {}
    try:
        _django_hazirla()
        from icra_models.models import Dosya
        qs = Dosya.objects.all()
        if filtreler.get("yargi_turu") not in (None, ""):
            qs = qs.yargi(filtreler["yargi_turu"])
        if filtreler.get("yargi_birimi_kod"):
            qs = qs.filter(birim__turu2=filtreler["yargi_birimi_kod"])
        if filtreler.get("mahkeme_id"):
            qs = qs.filter(birim__birim_id=filtreler["mahkeme_id"])
        if filtreler.get("tur_kod") not in (None, ""):
            qs = qs.filter(tur_kod=int(filtreler["tur_kod"]))
        if filtreler.get("durum_kod") not in (None, ""):
            qs = qs.filter(durum_kod=int(filtreler["durum_kod"]))
        from datetime import datetime, timedelta
        if filtreler.get("tarih_baslangic"):
            try:
                qs = qs.filter(acilis_tarihi__gte=datetime.strptime(
                    filtreler["tarih_baslangic"], "%d.%m.%Y"))
            except ValueError:
                pass
        if filtreler.get("tarih_bitis"):
            # `acilis_tarihi` saat bilgisi de taşıyan bir DateTimeField —
            # `__lte <bitiş günü> 00:00:00` yazılırsa bitiş gününün kendisinde
            # (saat 00:00 dışında) açılmış HER dosya filtreden dışarıda
            # kalıyordu (kullanıcı bulgusu, 2026-08-04: "açılış tarihi
            # yazıyor ama aralığı filtrelediğimde çıkmıyor"). Bunun yerine
            # bitiş gününün TAMAMINI kapsamak için bir SONRAKİ günün
            # başlangıcından KESİN ÖNCESİ (`__lt`) kullanılır.
            try:
                bitis = datetime.strptime(filtreler["tarih_bitis"], "%d.%m.%Y") + timedelta(days=1)
                qs = qs.filter(acilis_tarihi__lt=bitis)
            except ValueError:
                pass
        # taraf_adi/alacakli_adi/borclu_adi: DB icontains YAZILMAZ — gömülü
        # PostgreSQL `C` yereliyle kurulur (initdb --locale=C, db_baslat.py),
        # bu yerelde Django'nun icontains'i çevirdiği Postgres `ILIKE` yalnız
        # ASCII harfleri büyük/küçük eşler; Türkçe harfli adlarda (İ/I/ı/Ş/ş/
        # Ğ/ğ/Ü/ü/Ö/ö/Ç/ç) DB'de birebir var olan bir isim girilse bile hiç
        # eşleşme BULAMIYORDU (kullanıcı bulgusu, 2026-07-12).
        # Bunun yerine aşağıda, prefetch edilmiş taraf_baglari üzerinde Python'da
        # `_taraf_isim_eslesir` (tr_lower tabanlı) ile filtrelenir — rol ve isim
        # koşulu doğal olarak AYNI DosyaTaraf satırına uygulanır (çok-değerli
        # ilişki join-bölünmesi tuzağı da böylece hiç oluşmaz).
        # 2026-09-25 hız çalışması: model nesnesi kurulmadan, dosyalar tek
        # sorguda + taraf bağları tek sorguda (bkz. `_yalin_taraf_baglari`) —
        # çıktı sözlükleri eskisiyle birebir aynı (karşılaştırılarak doğrulandı).
        qs = qs.order_by("-acilis_tarihi", "-id")   # -id: eşitlerde sıra her açılışta aynı
        baglar_haritasi = _yalin_taraf_baglari(qs)
        taraf_adi_l = tr_lower(filtreler.get("taraf_adi") or "")
        alacakli_l = tr_lower(filtreler.get("alacakli_adi") or "")
        borclu_l = tr_lower(filtreler.get("borclu_adi") or "")
        out = []
        for d in qs.values_list("id", "dosya_id", "birim__birim_id", "birim__yargi_turu", "yargi_turu",
                                "birim__ad", "dosya_no", "tur_kod", "tur", "durum_kod", "durum",
                                "acilis_tarihi", named=True):
            baglar = baglar_haritasi.get(d.id, [])
            if taraf_adi_l and not any(_taraf_isim_eslesir(dt.taraf, taraf_adi_l) for dt in baglar):
                continue
            if alacakli_l and not any(dt.rol == "alacakli" and _taraf_isim_eslesir(dt.taraf, alacakli_l) for dt in baglar):
                continue
            if borclu_l and not any(dt.rol == "borclu" and _taraf_isim_eslesir(dt.taraf, borclu_l) for dt in baglar):
                continue
            rec = {
                # dbId = Dosya satırının KALICI birincil anahtarı (dosyaId
                # oturumluk, bkz. docstring) — barkod satırlarını dosyaya
                # güvenle bağlamak için (barkod_sorgu_ekrani, 2026-09-24).
                "dbId": d.id,
                "dosyaId": d.dosya_id, "birimId": d.birim__birim_id,
                "yargi_turu": d.yargi_turu,
                "yargi_turu_adi": YARGI_TURU_ADI.get(d.yargi_turu, ""),
                "birimAdi": d.birim__ad, "dosyaNo": d.dosya_no,
                "dosyaTurKod": d.tur_kod, "dosyaTur": d.tur,
                "dosyaDurumKod": d.durum_kod, "dosyaDurum": d.durum,
                "acilisTarihi": d.acilis_tarihi.strftime("%d.%m.%Y") if d.acilis_tarihi else "",
                # İcra dosyalarında rol="alacakli"/"borclu" olan tarafların
                # adları — icra_core.db_dosyalari_getir'deki AYNI türetme
                # (bkz. o dosyanın 213-221. satırları); diğer yargı türlerinde
                # bu rol'ler hiç oluşmadığından boş kalır (uydurma yok).
                # İcra Dosyalarım'daki gibi kendi başına sütun olsun diye
                # eklendi — kullanıcı bulgusu, 2026-07-13: taraf1..4 genel
                # sütunlarında Alacaklı/Borçlu gömülü ama tek bakışta
                # ayırt edilemiyordu ("bütünlük yok").
                "alacakli": ", ".join(str(dt.taraf) for dt in baglar if dt.rol == "alacakli"),
                "borclu": ", ".join(str(dt.taraf) for dt in baglar if dt.rol == "borclu"),
                # Hukuk Dosyalarım (hukuk_dosyalarim.py) için aynı türetme —
                # rol="davaci"/"davali" olmayan yargı türlerinde boş kalır.
                "davaci": ", ".join(str(dt.taraf) for dt in baglar if dt.rol == "davaci"),
                "davali": ", ".join(str(dt.taraf) for dt in baglar if dt.rol == "davali"),
            }
            # Kesinleşme + Tebliğ Durumu borçlu başına DosyaTaraf'tan (2026-09-25:
            # tebliğ artık dosyanın "en yeni barkodu" DEĞİL, borçlunun ödeme
            # emri durumu — bkz. `_borclu_tebligat_hesapla`).
            rec["kesinlesme_durumu"], rec["tebligat_durumu"] = borclu_durum_metinleri(baglar)
            rec["borclu_satirlari"] = borclu_satirlari(baglar)
            for i, deger in enumerate(_taraflar_sutunlari(baglar, d.birim__yargi_turu), start=1):
                rec[f"taraf{i}"] = deger
            out.append(rec)
        return out
    except Exception:
        return []


_EVRAK_TARIHI_RE_LISTE = re.compile(r"^(\d{2})[./](\d{2})[./](\d{4})")


def _evrak_tarihi_siralama_anahtari(tarih):
    """barkod_sorgu_ekrani._evrak_tarihi_anahtari İLE AYNI mantık (o dosyaya
    bağımlılık YOK — dosya_core Tkinter'sız, web sunucusundan da çağrılır):
    'GG/AA/YYYY' ya da 'GG.AA.YYYY' -> sıralanabilir 'YYYYAAGG'. Ayrıştırılamazsa
    boş dize (en eski sayılır)."""
    m = _EVRAK_TARIHI_RE_LISTE.match(str(tarih or "").strip())
    if not m:
        return ""
    gg, aa, yyyy = m.groups()
    return f"{yyyy}{aa}{gg}"


# ── Tebligat Barkod Sorgu geçmişi (barkod_sorgu.py'nin yazdığı TebligatBarkod) ──
# "Dosyalarım (Tümü)" ile AYNI kalıcı DB'den okur, AYNI desen (server-side
# kaba filtre yok — tüm kayıtlar döner, ekran kendi içinde sütun-başlığı
# canlı filtre/sıralama uygular, bkz. dosyalarim_genel.py `_yerel_uygula`).
# 2026-09-24 kullanıcı kararı: listelerde satır sınırı YOK — ne veri varsa yüklenir.
# (Eski 3000 sınırı web /api barkod listesinde 14 bin+ kaydın çoğunu gizliyordu.)
TEBLIGAT_BARKOD_LIMIT = None


def tebligat_barkod_gecmisi_listele(limit=TEBLIGAT_BARKOD_LIMIT, dosya_id=None,
                                     birim_id=None, dosya_no=None, dosya_tur_kod=None,
                                     log_fn=None):
    """`barkod_sorgu.py`'nin (Kapalı Tebligat Barkod Sorgu) yazdığı
    `TebligatBarkod` kayıtlarını, bağlı `Dosya`/`Birim` bilgisiyle birlikte
    en yeniden eskiye döner: list[dict]. DB erişilemezse [] döner. Her kayıt
    dosyalarim_genel'deki KOLONLAR ile aynı ruhta düz sözlük alanları taşır
    (ekran kendi filtre/sıralamasını yerelde uygular).

    `birim_id`+`dosya_no` verilirse DOĞAL ANAHTARLA (birim, yıl, sıra —
    gerekirse `dosya_tur_kod` ile de) filtrelenir — TERCİH EDİLEN yol, çünkü
    `dosya_id` (UYAP'ın 'dosyaId'si) OTURUMA GÖRE DEĞİŞİR (bkz.
    icra_models.ingest.dosya_kunyesi_kaydet yorumu): "Dosyalarım (Tümü)"
    ekranı DB'den okuduğu ESKİ bir dosyaId'yi bellekte tutarken, ARADAN
    başka bir akış (ör. Barkod Sorgu) aynı dosyayı YENİ bir oturumla
    çözüp `Dosya.dosya_id`'yi güncelleyebilir — bu durumda eski dosyaId'yle
    filtrelemek YANLIŞLIKLA BOŞ SONUÇ döner (kullanıcı bulgusu, 2026-08-03:
    barkod sorgulanıp DB'ye kaydedildiği hâlde "Dosya Görüntüle" barkodu
    hiç göstermiyordu). `dosya_id` yalnız `birim_id`/`dosya_no` verilmediğinde
    (veya doğal anahtar çözülemediğinde) GERİYE DÖNÜK uyumluluk için kullanılır.
    "Dosyalarım (Tümü)" ekranındaki "Dosya Görüntüle" penceresinin
    Barkod/Tebligat sekmesi bunu kullanır (bkz. dosyalarim_genel.py)."""
    try:
        _django_hazirla()
        from icra_models.models import TebligatBarkod
        from icra_models.ingest import _yil_sira
        qs = TebligatBarkod.objects.select_related("dosya", "dosya__birim")
        dogal_anahtar_uygulandi = False
        if birim_id and dosya_no:
            yil, sira = _yil_sira(dosya_no)
            if yil:
                qs = qs.filter(dosya__birim__birim_id=str(birim_id),
                                dosya__yil=yil, dosya__sira_no=sira)
                try:
                    tur_kod_int = int(dosya_tur_kod) if dosya_tur_kod not in (None, "") else None
                except (TypeError, ValueError):
                    tur_kod_int = None   # tanınmayan tür kodu → tür filtresi atlanır
                if tur_kod_int is not None:
                    qs = qs.filter(dosya__tur_kod=tur_kod_int)
                dogal_anahtar_uygulandi = True
        if not dogal_anahtar_uygulandi and dosya_id is not None:
            qs = qs.filter(dosya__dosya_id=dosya_id)
        qs = qs.order_by("-sorgu_zamani")[:limit]
        out = []
        for t in qs:
            d = t.dosya
            out.append({
                "birimAdi": d.birim.ad, "dosyaNo": d.dosya_no,
                "evrakAciklama": t.evrak_aciklama, "evrakTarihi": t.evrak_tarihi,
                "barkod": t.barkod, "tebligatTuru": t.tebligat_turu,
                "elektronikTebligat": "Evet" if t.elektronik_tebligat else "Hayır",
                "pttDurumu": t.ptt_durumu, "sonIslemTarihi": t.son_islem_tarihi,
                "teslimSekli": t.teslim_sekli,
                "tebligMazbatasiVar": "Var" if t.tebligat_mazbatasi_var else "Yok",
                "tebligMazbatasiAciklama": t.tebligat_mazbatasi_aciklama,
                "kapaliTebligMazbatasiVar": "Var" if t.kapali_tebligat_mazbatasi_var else "Yok",
                "yenidenTebligTalebi": "Var" if t.yeniden_teblig_talep_edildi else "Yok",
                "sorguZamani": t.sorgu_zamani.strftime("%d.%m.%Y %H:%M") if t.sorgu_zamani else "",
                "dosyaId": d.dosya_id, "birimId": d.birim.birim_id,
                # Kalıcı dosya anahtarı + dosya içi sıralama için evrak no
                # (barkod_sorgu_ekrani: dosya başına TÜM barkodlar, 2026-09-24).
                "dbId": d.pk, "birimEvrakNo": t.birim_evrak_no,
                # Borçlu başına listeleme (2026-09-24): tebligatın bağlı olduğu
                # borçlu (DosyaTaraf.pk) + açıklamadaki muhatap metni.
                "borcluId": t.borclu_id, "muhatap": tebligat_muhatabi(t.evrak_aciklama)[0],
                "tbId": t.pk,
            })
        return out
    except Exception as e:
        # 2026-09-23: eskiden SESSİZCE [] dönüyordu — barkod neden görünmüyor
        # sorusunun cevabı hiç görünmüyordu.
        if log_fn:
            log_fn(f"⚠️ Barkod/tebligat kayıtları okunamadı: {e}")
        return []


def ptt_hareketleri_listele(tebligat_id):
    """Bir tebligatın PTT hareketleri, eskiden yeniye: [{zaman, aciklama, yer, islemDetay}]."""
    _django_hazirla()
    from icra_models.models import PttHareket
    return [{"zaman": h.zaman.strftime("%d.%m.%Y %H:%M") if h.zaman else "",
             "aciklama": h.aciklama, "yer": h.yer, "islemDetay": h.islem_detay}
            for h in PttHareket.objects.filter(tebligat_id=tebligat_id).order_by("zaman", "sira")]


def durum_olaylari_listele(rec, log_fn=None):
    """Dosyanın durum geçmişi, yeniden eskiye (dosya kartı "Geçmiş" sekmesi)."""
    try:
        dosya = _dosya_rec_ten_bul(rec, log_fn or (lambda m: None))
        if dosya is None:
            return []
        return [{"zaman": o.zaman.strftime("%d.%m.%Y %H:%M") if o.zaman else "",
                 "tur": o.get_tur_display(), "taraf": o.taraf_adi, "eski": o.eski, "yeni": o.yeni,
                 "olayTarihi": o.olay_tarihi.strftime("%d.%m.%Y") if o.olay_tarihi else "",
                 "kaynak": o.get_kaynak_display(), "aciklama": o.aciklama}
                for o in dosya.durum_olaylari.all()[:500]]
    except Exception as e:
        if log_fn:
            log_fn(f"⚠️ Durum geçmişi okunamadı: {e}")
        return []


def dosyalarim_yenile(log_fn=None, yargi_turu=None, yargi_birimi_kod=None, tum_turler=False, kontrol=None,
                      barkod_da=False):
    """'Yenile' düğmesi: CANLI UYAP taraması yapar (`DosyaSorgu.calistir`,
    kullanıcı isteğiyle HEMEN). `yargi_turu` verilirse SADECE o tür taranır
    (`yargi_birimi_kod` de verilirse SADECE o birim) — EKONOMİK varsayılan
    (kullanıcı bulgusu: eski davranış seçili filtreden bağımsız HER ZAMAN tüm
    türleri/birimleri tarıyordu, pahalıydı). `tum_turler=True` ('Tüm
    Dosyaları Güncelle' ayrı düğmesi) ESKİ davranış: SenkronKapsami'ye
    bakmadan her yargı türünü/birimini tarar (bkz. calistir docstring).
    İkisi de verilmezse `SenkronKapsami`'ye döner (arka plan senkronuyla aynı
    kapsam). Arka plan zamanlayıcısı bu fonksiyonu ÇAĞIRMAZ,
    `DosyaSorgu(...).calistir()`'i doğrudan (tum_turler=False, taraf_da_cek=False
    ile) çağırır — bkz. `senkron_zamanlayici_baslat`. Bu fonksiyon (kullanıcının
    doğrudan tetiklediği 'Yenile'/'Tüm Dosyaları Güncelle') `taraf_da_cek=True`
    verir — sessiz arka plan turunun aksine burada ek istek maliyeti kullanıcının
    o anki bilinçli eylemine bağlı. Döner: (toplam, sonuclar).

    Tarama bittikten sonra `kesinlesme_durumlarini_guncelle` de çağrılır
    (kullanıcı bulgusu, 2026-08-04: "UYAP'tan güncelle dediğimde kesinleşme
    sütunu hala boş") — bu, YENİ bir UYAP isteği atmaz, yalnız daha önce
    Barkod Sorgu ile toplanmış yerel TebligatBarkod verisinden (varsa) o anki
    tarihe göre kesinleşme durumunu yeniden hesaplar (bkz. o fonksiyonun
    docstring'i — özellikle "kesinleşme bekliyor" -> "kesinleşti" geçişi
    yalnız GÜNÜN geçmesiyle tetiklenebildiği için Yenile'nin her çağrısında
    tazelenmesi gerekir). DİKKAT: 'Yenile' BİLEREK barkod sorgusu ÇALIŞTIRMAZ
    (kullanıcı kararı, 2026-08-04: "toplu yenile yaptığımda barkod sorgusu
    yapmasın, yenile SADECE dosya listesini çeksin" — daha önce burada
    otomatik bir Barkod Sorgu tetikleyicisi denenmiş, kullanıcı isteğiyle
    KALDIRILMIŞTIR) — barkod/tebliğ verisi yalnız kullanıcı Barkod Sorgu
    ekranından TOPLU olarak açıkça çalıştırdığında toplanır; burada yalnız
    o ÖNCEDEN toplanmış yerel veri okunur.

    2026-09-23'ten beri `guncelleme_hatti`'nın GÜNDÜZ katmanlarıyla (künye +
    taraf + kesinleşme, barkod YOK) çağrılmasının ince bir sarmalayıcısıdır —
    davranış aynıdır, yalnız mantık tek yerde toplanmıştır.

    `barkod_da=True` (2026-09-30, İcra Dosyalarım > "UYAP'tan Güncelle"):
    kullanıcı bu düğmeden künye + taraf + barkod + kesinleşmenin BİRLİKTE
    güncellenmesini istedi — barkod katmanı (K3) da çalışır, uzun sürebilir."""
    katmanlar = GECE_KATMANLARI if barkod_da else GUNDUZ_KATMANLARI
    sonuc = guncelleme_hatti(log_fn, katmanlar=katmanlar, yargi_turu=yargi_turu,
                             yargi_birimi_kod=yargi_birimi_kod, tum_turler=tum_turler,
                             kontrol=kontrol)
    return sonuc["toplam"], sonuc["sonuclar"]


# ── Güncelleme Hattı (kullanıcı kararı, 2026-09-23) ──────────────────────────
# "Tüm Dosyalarım / İcra Dosyalarım / Barkod Sorgu'dan hangisinden güncellersem
# güncelleyeyim veritabanının tamamı tutarlı güncellensin" isteği üzerine DB'ye
# yazan akışlar KATMAN olarak tek fonksiyonda toplandı:
#   K1 künye      — Açık+Kapalı kapak künyesi upsert (ucuz)
#   K2 taraf      — yalnız tarafı eksik dosyalar (orta)
#   K3 barkod     — açık İcra dosyalarının evrakları taranır, 'Kapalı Tebligat'
#                   barkodları DB'ye eklenip PTT/e-tebliğ durumu sorgulanır.
#                   Teslim edilmiş olanlar DAHİL her seferinde tazelenir
#                   (mazbata dönüşü UYAP'tan kontrol ediliyor, dosyalara sonradan
#                   barkodlu belge eklenebiliyor — kullanıcı kararı).
#   K4 kesinleşme — yerel hesap, UYAP'a gitmez (bedava)
# Kullanıcı kararı: gündüz düğmeleri K3'ü ÇALIŞTIRMAZ (2026-08-04 performans
# kararı korunur, barkod için ayrı "Barkod'u da Güncelle" düğmesi var); K3 GECE
# otomatik turunda (bkz. `gece_tam_guncelleme`) tüm açık icra dosyaları için
# çalışır. İleride evraklardan barkod dışında veri çekilecek (kullanıcı notu) —
# yeni bir evrak çıkarıcısı buraya YENİ bir katman olarak eklenir.
KATMAN_KUNYE = "kunye"
KATMAN_TARAF = "taraf"
KATMAN_BARKOD = "barkod"
KATMAN_KESINLESME = "kesinlesme"
GUNDUZ_KATMANLARI = (KATMAN_KUNYE, KATMAN_TARAF, KATMAN_KESINLESME)
GECE_KATMANLARI = (KATMAN_KUNYE, KATMAN_TARAF, KATMAN_BARKOD, KATMAN_KESINLESME)


def guncelleme_hatti(log_fn=None, katmanlar=GUNDUZ_KATMANLARI, yargi_turu=None, yargi_birimi_kod=None,
                     tum_turler=False, kontrol=None, istek_sarici=None):
    """Seçilen katmanları sırayla çalıştırır (bkz. yukarıdaki blok notu).
    K1/K2 kapsamı `DosyaSorgu.calistir`'daki gibidir: `yargi_turu` verilirse
    yalnız o tür (+ `yargi_birimi_kod`), yoksa `tum_turler`/SenkronKapsami.
    K3 kapsamı HER ZAMAN DB'deki tüm açık İcra dosyalarıdır. Döner:
    {"toplam", "sonuclar", "barkod_satir"}."""
    log = log_fn or (lambda m: None)
    sonuc = {"toplam": 0, "sonuclar": [], "barkod_satir": None}

    if KATMAN_KUNYE in katmanlar:
        sorgu = DosyaSorgu(log, istek_sarici=istek_sarici)
        taraf_da_cek = KATMAN_TARAF in katmanlar
        # DİKKAT: `if yargi_turu:` YAZILMAZ — Ceza'nın kodu 0'dır ve Python'da 0
        # falsy'dir; Ceza seçiliyken yanlışlıkla tüm türleri tarayan dala
        # düşerdi (kullanıcı bulgusu, 2026-07-13).
        if yargi_turu is not None:
            toplam, sonuclar = sorgu.calistir(tek_kapsam=(yargi_turu, yargi_birimi_kod or ""),
                                              taraf_da_cek=taraf_da_cek, kontrol=kontrol)
        else:
            toplam, sonuclar = sorgu.calistir(tum_turler=tum_turler, taraf_da_cek=taraf_da_cek,
                                              kontrol=kontrol)
        sonuc["toplam"], sonuc["sonuclar"] = toplam, sonuclar

    if KATMAN_BARKOD in katmanlar and not (kontrol and kontrol.durduruldu):
        # Geç import: barkod_sorgu bu modülü (dosya_core) ithal eder.
        import barkod_sorgu as _bs
        secim = _ic.acik_icra_dosyalari_secimi()
        log(f"… barkod katmanı: {len(secim)} açık İcra dosyasının evrakları taranacak.")
        if secim:
            satirlar = _bs.calistir(secim, log, kontrol=kontrol, excel=False)
            sonuc["barkod_satir"] = len(satirlar or [])

    if KATMAN_KESINLESME in katmanlar:
        try:
            kesinlesme_durumlarini_guncelle(log_fn=log)
        except Exception as e:
            log(f"⚠️ Kesinleşme durumu güncellenemedi: {e}")
    return sonuc


# ── Kesinleşme durumu hesabı (bkz. kesinlesme_core.py) ───────────────────────
# Kullanıcı kararı (AskUserQuestion, 2026-08-04): tek bilinen kural (Örnek-7,
# ilamsız, genel haciz yoluyla takip — 7 günlük itiraz süresi) TÜM "Ödeme
# Emri" tebligatı olan borçlulara uygulanır. UYAP'ta takip türünü (ilamsız/
# kambiyo/ilamlı) güvenilir şekilde ayırt eden canlı doğrulanmış bir alan YOK
# (bkz. IcraTakipDetay.takibin_turu yorumu — kod saklanır ama metin eşlemesi
# "(tahmini)"dir, DB'ye yazılmaz) — kambiyo senedi ile takipte de aynı "Ödeme
# Emri" evrak adı geçebilir ama itiraz süresi 7 gün DEĞİLDİR (5 gündür);
# kullanıcı bu riski bilerek kabul etti. itiraz_edildi_mi (elle işaretlenir)
# hesaplamaya HER ZAMAN önceliklidir.
_TEBLIG_TARIHI_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})")


def _teblig_tarihini_coz(deger):
    """barkod_sorgu.py'nin ürettiği 'GG/AA/YYYY[ SS:DD]' biçimindeki Son
    İşlem Tarihi'ni datetime.date'e çevirir; ayrıştırılamazsa None."""
    m = _TEBLIG_TARIHI_RE.match(str(deger or "").strip())
    if not m:
        return None
    gg, aa, yyyy = m.groups()
    try:
        return datetime.date(int(yyyy), int(aa), int(gg))
    except ValueError:
        return None


def _son_islem_gunu(k):
    """TebligatBarkod'un son işlem (tebliğ/iade) günü — 2026-11-14'ten beri
    tarih alanından (`son_islem_zamani`); eski/türetilmemiş kayıtta ham metinden."""
    z = getattr(k, "son_islem_zamani", None)
    if z:
        return z.date() if isinstance(z, datetime.datetime) else z
    return _teblig_tarihini_coz(k.son_islem_tarihi)


def _evrak_gunu(k):
    g = getattr(k, "evrak_gunu", None)
    return g or _teblig_tarihini_coz(k.evrak_tarihi)


# E-tebligatta tebliğ tarihi = evrak tarihi (`son_islem_tarihi`'nde o yazılır);
# 5 günlük okuma süresi yalnız SÜRENİN BAŞLANGICINI öteler (kullanıcı, 2026-10-02:
# "süre beş gün sonra başlıyor ama tebliğ tarihini 5 gün sonrasına yazman doğru değil").
E_TEBLIG_OKUMA_SURESI_GUN = 5


def sure_baslangici(barkod_kaydi):
    """Kesinleşme süresinin hesaplandığı tebliğ günü: fizikide tebliğ tarihi,
    e-tebligatta tebliğ (evrak) tarihi + 5 gün. Tarih yoksa None."""
    t = _son_islem_gunu(barkod_kaydi)
    if t and barkod_kaydi.elektronik_tebligat:
        t += datetime.timedelta(days=E_TEBLIG_OKUMA_SURESI_GUN)
    return t


def _tebligat_gerceklesti_mi(barkod_kaydi):
    """Bu TebligatBarkod satırı GERÇEKTEN teslim/okundu-sayılmış mı —
    "Dağıtımda"/"İade" gibi ara durumlarda kesinleşme süresi BAŞLAMAZ.
    Fiziki PTT'de `ptt_durumu` metninde "teslim edildi" aranır (canlı
    doğrulanan tek kesin terim, bkz. barkod_sorgu._ptt_durum_ve_tarih)."""
    if barkod_kaydi.elektronik_tebligat:
        # 5 günlük okuma süresi dolmadan tebliğ gerçekleşmiş sayılmaz.
        t = sure_baslangici(barkod_kaydi)
        return bool(t) and t <= datetime.date.today()
    return "teslim edildi" in tr_lower(barkod_kaydi.ptt_durumu or "")


def _mazbatasi_dosyaya_dondu_mu(k):
    """Bu ödeme emri tebligatının KENDİ mazbatası icra dosyasına dönmüş mü?
    Elektronik tebligatta ayrı bir dönüş beklenmez (tebliğ = evrak tarihi,
    mazbata indirilmiyor — bkz. barkod_sorgu.e_teblig_teblig_tarihi). Fizikide
    yalnız bu barkoda ait kanıt sayılır: açıklamasında bu barkod geçen mazbata
    ya da UYAP'ın evrak açıklamasına eklediği "- Tebliğ Edildi". Barkodsuz
    "Var" işareti hangi tebligata ait olduğu belli olmadığından SAYILMAZ."""
    if k.elektronik_tebligat:
        return True
    import barkod_sorgu as _bs
    return (_mazbata_sonucu(k.evrak_aciklama) == "edildi"
            or _bs.kendi_mazbatasi_dondu_mu(k.barkod, k.tebligat_mazbatasi_var,
                                            k.tebligat_mazbatasi_aciklama))


def _borclu_kesinlesme_hesapla(dt, odeme_emirleri, tatiller, bugun, DosyaTarafCls, kc, durdurma=None,
                               sure_gun=7):
    """Tek bir borçlu `DosyaTaraf` satırı için kesinlesme_durumu hesaplar.
    2026-09-28 kullanıcı kuralı: ödeme emri gönderilmiş borçlu, Kesinleşti
    olana dek KESİNLEŞME BEKLİYOR'dur — dağıtımda, teslim edildi ama mazbata
    dönmedi, iade/tebliğ edilemedi, süre işliyor hepsi dahil. Kesinleşti için
    bir ödeme emrinde ÜÇÜ BİRDEN gerekir: tebliğ gerçekleşmiş (tarihiyle),
    mazbatası dosyaya dönmüş (bkz. `_mazbatasi_dosyaya_dondu_mu`) ve süre dolmuş.
    Önce gelenler: elle `itiraz_edildi_mi` → İtiraz; sonra `durdurma` (dosyanın
    UYAP durumu "Açık (Durdurulmuş : X)" — `durdurma_kesinlesme_kodu` çıktısı;
    Takibe İtiraz → İtiraz, diğerleri kendi "Durdurulmuş: X" değeri).
    Döner: (durum kodu, kesinleşme günü|None); borçluya bağlı ödeme emri hiç
    yoksa ("", None). Kesinleşme günü (2026-09-30 kullanıcı isteği) itiraz
    süresinden sonraki ilk iş günüdür: Kesinleşti'de mazbatası dönmüş
    tebliğden, Kesinleşme Bekliyor'da (mazbata henüz dönmemiş olsa da) bilinen
    en yeni tebliğ tarihinden hesaplanır; tebliğ tarihi yoksa ya da itirazda None."""
    K = DosyaTarafCls.KesinlesmeDurumu
    if dt.itiraz_edildi_mi:
        return K.ITIRAZ, None
    if durdurma:
        return durdurma, None
    if not odeme_emirleri:
        return "", None
    # 2026-11-14: itiraz süresi takip türünün kuralından (KesinlesmeKurali);
    # kural "hesaplanmaz" diyorsa (gün boş) kesinleşme yazılmaz.
    if sure_gun is None:
        return "", None
    mazbatali_tarih = teblig_tarihi = None
    for k in odeme_emirleri:
        if not _tebligat_gerceklesti_mi(k) or _mazbata_sonucu(k.evrak_aciklama) == "edilemedi":
            continue
        t = sure_baslangici(k)
        if not t:
            continue
        if teblig_tarihi is None or t > teblig_tarihi:
            teblig_tarihi = t
        if _mazbatasi_dosyaya_dondu_mu(k) and (mazbatali_tarih is None or t > mazbatali_tarih):
            mazbatali_tarih = t
    if mazbatali_tarih is not None:
        gun = kc.kesinlesme_hesapla(mazbatali_tarih, sure_gun, tatiller)["kesinlesebilir_tarihi"]
        if bugun >= gun:
            return K.KESINLESTI, gun
    if teblig_tarihi is not None:
        return (K.KESINLESME_BEKLIYOR,
                kc.kesinlesme_hesapla(teblig_tarihi, sure_gun, tatiller)["kesinlesebilir_tarihi"])
    return K.KESINLESME_BEKLIYOR, None


def _mazbata_sonucu(aciklama):
    """UYAP mazbata dönünce evrak açıklamasının sonuna "- Tebliğ Edildi" /
    "- Tebliğ Edilemedi" ekliyor (kesik yazılışlar yok sayılır). `tr_lower`
    Türkçe harfleri ASCII'ye çevirir ("ğ" → "g") — 2026-09-28'e dek burada
    "tebliğ" aranıyordu, hiçbir açıklama eşleşmiyordu."""
    son = tr_lower((aciklama or "").rsplit("]", 1)[-1])
    if "teblig edilemedi" in son:
        return "edilemedi"
    if "teblig edildi" in son:
        return "edildi"
    return ""


def _borclu_tebligat_hesapla(odeme_emirleri, TebligatDurumu):
    """Borçlunun ödeme emri tebligatlarından (TebligatBarkod listesi) (durum,
    tarih). 2026-09-25 kullanıcı kararı: Tebliğ Durumu borçlu başına ve
    ödeme emrine göre. Kurallar:
      • herhangi biri tebliğ edilmiş (kesinleşmeyle AYNI ölçüt,
        `_tebligat_gerceklesti_mi`) → Tebliğ Edildi, en yeni tebliğ tarihi;
      • yoksa mazbatası "Tebliğ Edildi" dönmüş olan → Tebliğ Edildi (tarih yok);
      • yoksa evrak tarihi en yeni olan ve bilgi taşıyan: PTT İADE ya da mazbata
        "Tebliğ Edilemedi" → İade; başka bir PTT hareketi ya da günü gelmemiş
        e-tebligat → Dağıtımda;
      • hiç bilgi yok → ("", None).
    Üçüncü değer (2026-10-02): durumu veren tebligatın teslim şekli / iade nedeni
    (`TebligatBarkod.teslim_sekli`; e-tebligatta "E-tebligat"), yoksa ""."""
    en_yeni = en_yeni_k = None
    for k in odeme_emirleri:
        if _tebligat_gerceklesti_mi(k):
            t = _son_islem_gunu(k)
            if t and (en_yeni is None or t > en_yeni):
                en_yeni, en_yeni_k = t, k
    if en_yeni:
        return TebligatDurumu.TEBLIG_EDILDI, en_yeni, _teslim_sekli(en_yeni_k)
    if any(_mazbata_sonucu(k.evrak_aciklama) == "edildi" for k in odeme_emirleri):
        return TebligatDurumu.TEBLIG_EDILDI, None, ""
    for k in sorted(odeme_emirleri, key=lambda k: _evrak_gunu(k) or datetime.date.min, reverse=True):
        ptt = tr_lower(k.ptt_durumu or "")
        if "iade" in ptt or _mazbata_sonucu(k.evrak_aciklama) == "edilemedi":
            return TebligatDurumu.IADE, _son_islem_gunu(k), _teslim_sekli(k)
        if k.elektronik_tebligat or (ptt and "kayit yok" not in ptt and "kayıt yok" not in ptt):
            return TebligatDurumu.DAGITIMDA, None, ""
    return "", None, ""


def _teslim_sekli(k):
    if k.elektronik_tebligat:
        return "E-tebligat"
    return getattr(k, "teslim_sekli", "") or ""


def tebligat_metni(b):
    """"Tebliğ Edildi (12.09.2026) — Muhtara teslim (21. md.)" — borçlu başına
    (DosyaTaraf ya da _YalinBag); tarih ve teslim şekli/iade nedeni biliniyorsa."""
    if not b.tebligat_durumu:
        return ""
    m = b.get_tebligat_durumu_display()
    if b.tebligat_durum_tarihi:
        m += f" ({b.tebligat_durum_tarihi:%d.%m.%Y})"
    if getattr(b, "tebligat_sekli", ""):
        m += f" — {b.tebligat_sekli}"
    return m


def kesinlesme_metni(dt):
    """"Kesinleşme Bekliyor (14.10.2026)" — kesinleşme günü biliniyorsa
    parantez içinde (DosyaTaraf ya da _YalinBag)."""
    if not dt.kesinlesme_durumu:
        return ""
    m = dt.get_kesinlesme_durumu_display()
    if dt.kesinlesme_tarihi:
        m += f" ({dt.kesinlesme_tarihi:%d.%m.%Y})"
    return m


def borclu_satirlari(baglar):
    """[{"borclu", "kesinlesme_durumu", "tebligat_durumu"}] — borçlu başına
    (sira sırasıyla). Liste ekranları çok borçlu dosyayı borçlu başına satıra
    açarken kullanır (bkz. `borclu_basina_ac`)."""
    out = []
    for b in baglar:
        if b.rol != "borclu":
            continue
        out.append({"borclu": str(b.taraf), "kesinlesme_durumu": kesinlesme_metni(b),
                    "tebligat_durumu": tebligat_metni(b)})
    return out


def borclu_basina_ac(kayitlar):
    """2026-09-30 kullanıcı kararı: İcra Dosyalarım / Tüm Dosyalarım'da çok
    borçlu dosya Barkod Sorgulama gibi BORÇLU BAŞINA satır olarak gösterilir
    (Borçlu / Kesinleşme / Tebliğ o borçlunun kendi değeri; dosya bilgisi
    tekrar eder). Yalnız ekran içindir — kayıtların kendisi dosya başınadır
    (birleştirme, Dosya Görüntüle, barkod kuyruğu dosya bazlı çalışır).
    Tek/borçlusuz dosya olduğu gibi kalır. Döner: yeni liste."""
    out = []
    for r in kayitlar:
        satirlar = r.get("borclu_satirlari") or []
        if len(satirlar) <= 1:
            out.append(r)
            continue
        for b in satirlar:
            s = dict(r)
            s.update(b)
            out.append(s)
    return out


def borclu_durum_metinleri(baglar):
    """Liste sütunları için (kesinleşme_metni, tebliğ_metni) — borçlu başına
    DosyaTaraf alanlarından. Tek borçluda yalnız değer ("Tebliğ Edildi
    (25.08.2025)"), çok borçluda "AD: değer; AD2: değer" (hangi borçlunun
    hangi durumda olduğu görünsün)."""
    borclular = [b for b in baglar if b.rol == "borclu"]
    cok = len(borclular) > 1

    def _birlestir(parcalar):
        return "; ".join(f"{ad}: {m}" if cok else m for ad, m in parcalar if m)

    kes = [(str(b.taraf), kesinlesme_metni(b)) for b in borclular]
    teb = [(str(b.taraf), tebligat_metni(b)) for b in borclular]
    return _birlestir(kes), _birlestir(teb)


# ── Tebligat muhatabı → borçlu (2026-09-24, kullanıcı kararı: tebliğ/iade/
# kesinleşme HER BORÇLU için ayrı) ──────────────────────────────────────────
# UYAP her tebligat evrakının açıklamasına muhatabı köşeli parantez içinde
# yazıyor (DB'deki 13 bin kayıtta doğrulandı, parantezsiz açıklama YOK):
#   "Normal Tebligat[BORÇLU VE MÜFLİS SELCAN KINA] - Tebliğ Edilemedi"
#   "Elektronik Tebligat[BORÇLU VE MÜFLİS EMİNE SERAP AKTAŞ]"
#   "Elektronik Tebligat[Tebligat Yapılacak 3. Taraf DENİZBANK A.Ş.]"
#   "…[ARACIKISIKURUM <işveren>]", "…[ÜÇÜNCÜ ŞAHIS …]", "…[Vekil …]", "…[ALACAKLI …]"
# Eskiden tek borçlulu dosyada TÜM tebligatlar (banka/işveren/vekil dahil) o
# borçluya bağlanıyor, çok borçlulu dosyada HİÇBİRİ bağlanmıyordu. Artık yalnız
# etiketi BORÇLU olan ve adı dosyadaki borçlulardan TAM BİRİYLE eşleşen tebligat
# bağlanır — eşleşme yoksa/birden çoksa bağlanmaz (tahmin yürütülmez).
_MUHATAP_PARANTEZ_RE = re.compile(r"\[(.*?)\]")
_BORCLU_ETIKETLERI = ("borclu ve muflis ", "borclu ")   # tr_lower sonrası


def tebligat_muhatabi(aciklama):
    """Evrak açıklamasından (muhatap_metni, borçlu_adı|None). muhatap_metni
    ekranda gösterilir ("BORÇLU VE MÜFLİS X", "3. Taraf DENİZBANK" …);
    borçlu_adı yalnız etiket BORÇLU ise dolu."""
    m = _MUHATAP_PARANTEZ_RE.search(aciklama or "")
    if not m:
        return "", None
    ic = m.group(1).strip()
    gorunen = re.sub(r"^Tebligat Yapılacak\s+", "", ic)
    kucuk = tr_lower(ic)   # uzunluk 1:1 korunur (bkz. icra_core.tr_lower)
    for etiket in _BORCLU_ETIKETLERI:
        if kucuk.startswith(etiket):
            ad = ic[len(etiket):].strip()
            return gorunen, (ad or None)
    return gorunen, None


def _ad_anahtari(ad):
    return tuple(re.sub(r"[^0-9a-z]+", " ", tr_lower(ad or "")).split())


def _bas_harfler(ad):
    # `_ad_anahtari` Türkçe harfleri atıyor (ÇAYIR → "ay r") — baş harf için uygun değil.
    return tuple(k[0] for k in re.sub(r"[^0-9a-zçğıöşü]+", " ", tr_lower(ad or "")).split())


def _ptt_alici_borclusunu_bul(ham_yanit, borclular):
    """MTS ödeme emirlerinin açıklaması yalnız "PTT MTS Tebligat Belgesi" —
    muhatap parantezi YOK (2026-09-25: 8.908 fiziki ödeme emrinin 7.303'ü
    bu yüzden hiçbir borçluya bağlanmıyor, kesinleşmesi hiç hesaplanmıyordu).
    PTT yanıtındaki alıcı ise baş harfleriyle maskeli geliyor:
    "B* V* M* F* B*" = BORÇLU VE MÜFLİS FATMA BİROLOĞLU. Yalnız "B* V* M*"
    etiketli alıcı, baş harfleri TEK bir borçluyla aynıysa bağlanır; PTT uzun
    unvanları kesiyor ("S* E* E* K*" / SARKA ENDÜSTRİYEL ELEKTRİK KABLO A.Ş.)
    → en az 3 harflik baş kısım TEK bir borçlunun başıyla aynıysa kabul."""
    kabul = (ham_yanit or {}).get("kabul") if isinstance(ham_yanit, dict) else None
    alici = (kabul or {}).get("alici") or ""
    harfler = tuple(tr_lower(p[0]) for p in alici.split() if p[0].isalnum() and p[0] != "*")
    if harfler[:3] != ("b", "v", "m") or len(harfler) < 5:
        return None
    hedef = harfler[3:]
    adaylar = [b for b in borclular if _bas_harfler(str(b.taraf)) == hedef]
    if not adaylar and len(hedef) >= 3:
        adaylar = [b for b in borclular if _bas_harfler(str(b.taraf))[:len(hedef)] == hedef]
    return adaylar[0] if len(adaylar) == 1 else None


def tebligat_borclusunu_bul(aciklama, borclular, ham_yanit=None):
    """`borclular`: dosyanın rol=borclu DosyaTaraf listesi (taraf yüklü).
    Açıklamadaki BORÇLU muhatabının adı TAM BİR borçluyla eşleşirse onu,
    aksi halde None döner. Önce kelime sırasıyla birebir, sonra sırasız eşleşme.
    Açıklamada muhatap parantezi hiç yoksa (MTS) PTT yanıtındaki alıcıya
    bakılır (bkz. `_ptt_alici_borclusunu_bul`)."""
    if ham_yanit and not _MUHATAP_PARANTEZ_RE.search(aciklama or ""):
        return _ptt_alici_borclusunu_bul(ham_yanit, borclular)
    _, ad = tebligat_muhatabi(aciklama)
    if not ad:
        return None
    hedef = _ad_anahtari(ad)
    if not hedef:
        return None
    adaylar = [b for b in borclular if _ad_anahtari(str(b.taraf)) == hedef]
    if not adaylar:
        adaylar = [b for b in borclular if sorted(_ad_anahtari(str(b.taraf))) == sorted(hedef)]
    if not adaylar and len(hedef) >= 5:
        # UYAP uzun tüzel unvanları açıklamada KESİYOR (DB'de görüldü: "…SANAYİ VE
        # TİCARET" / borçlu "…SANAYİ VE TİCARET LİMİTED ŞİRKETİ") — en az 5
        # kelimelik bir baş kısım TEK bir borçlunun başıyla aynıysa o kabul edilir.
        adaylar = [b for b in borclular if _ad_anahtari(str(b.taraf))[:len(hedef)] == hedef]
    return adaylar[0] if len(adaylar) == 1 else None


def mazbata_barkod_eslesmesini_duzelt(log_fn=None):
    """Eski "dosyanın ilk mazbatası tüm barkodlara" hatasıyla yazılmış kayıtları
    (2026-09-24: 6.078'in 4.508'i) DB'deki açıklamalardan düzeltir — UYAP'a gitmez.
    Aynı dosyada hangi barkodun mazbata açıklaması biliniyorsa o barkoda yazılır;
    açıklaması BAŞKA barkoda ait kayıttan mazbata kaldırılır. Açıklamasında
    barkod olmayan ("Var") kayıtlara dokunulmaz. Eksikler bir sonraki UYAP
    turunda (evrak listesi her turda okunur) tamamlanır. Döner: değişen kayıt."""
    import barkod_sorgu as _bs
    log = log_fn or (lambda *a, **k: None)
    _django_hazirla()
    from icra_models.models import TebligatBarkod
    satirlar = list(TebligatBarkod.objects.exclude(barkod="").values_list(
        "id", "dosya_id", "barkod", "tebligat_mazbatasi_var", "tebligat_mazbatasi_aciklama"))
    bilinen = {}   # (dosya_id, barkod) -> açıklama
    for _i, d, _b, _v, ac in satirlar:
        m = _bs._MAZBATA_BARKOD_RE.search(ac or "")
        if m:
            bilinen.setdefault((d, m.group(1) or m.group(2)), ac)
    degisen = 0
    for i, d, b, var, ac in satirlar:
        dogru = bilinen.get((d, b))
        if dogru is not None:
            yeni = (True, dogru)
        elif _bs._MAZBATA_BARKOD_RE.search(ac or ""):
            yeni = (False, "")          # başka barkodun mazbatası yazılmış
        else:
            continue                    # "Var"/boş — ayırt edilemez, dokunulmaz
        if (bool(var), ac or "") != yeni:
            TebligatBarkod.objects.filter(pk=i).update(
                tebligat_mazbatasi_var=yeni[0], tebligat_mazbatasi_aciklama=yeni[1])
            degisen += 1
    log(f"… mazbata barkod eşleşmesi düzeltildi: {degisen} kayıt.")
    return degisen


def odeme_icra_emri_turlerini_duzelt(log_fn=None):
    """Türü "Ödeme İcra Emri …" (zarf içeriğinden gelen ham ad) olan tebligatları
    "Ödeme Emri"ne çevirir ve o dosyaların kesinleşmesini yeniden hesaplar
    (kullanıcı, 2026-09-24: "Ödeme İcra Emri benim için ödeme emri ile aynı şey").
    UYAP'a gitmez, PDF indirmez. Döner: düzeltilen kayıt sayısı."""
    log = log_fn or (lambda *a, **k: None)
    _django_hazirla()
    from icra_models.models import TebligatBarkod, Dosya
    idler, dosyalar = [], set()
    for tb_id, dosya_id, tur in TebligatBarkod.objects.exclude(tebligat_turu="Ödeme Emri").values_list(
            "id", "dosya_id", "tebligat_turu"):
        if "odeme icra emri" in tr_lower(tur or ""):
            idler.append(tb_id)
            dosyalar.add(dosya_id)
    if not idler:
        return 0
    TebligatBarkod.objects.filter(id__in=idler).update(tebligat_turu="Ödeme Emri")
    for d in Dosya.objects.filter(id__in=dosyalar):
        kesinlesme_durumlarini_guncelle(dosya_obj=d, log_fn=log)
    log(f"… 'Ödeme İcra Emri' → 'Ödeme Emri': {len(idler)} tebligat, {len(dosyalar)} dosya düzeltildi.")
    return len(idler)


def dosya_borclulari_haritasi(db_idleri=None):
    """{Dosya.pk: [{"id": DosyaTaraf.pk, "ad", "kesinlesme"}, ...]} — borçlu
    başına listeleme için (yalnız okur). `db_idleri` None ise tüm dosyalar."""
    try:
        _django_hazirla()
        from icra_models.models import DosyaTaraf
        qs = DosyaTaraf.objects.filter(rol="borclu").select_related("taraf").order_by("dosya_id", "pk")
        if db_idleri is not None:
            qs = qs.filter(dosya_id__in=list(db_idleri))
        out = {}
        for dt in qs:
            out.setdefault(dt.dosya_id, []).append({
                "id": dt.pk, "ad": str(dt.taraf),
                "kesinlesme": kesinlesme_metni(dt),
            })
        return out
    except Exception:
        return {}


def _tebligat_barkod_borclu_geriye_donuk_doldur(DosyaTaraf, TebligatBarkod, dosya_obj, log):
    """`TebligatBarkod.borclu`'yu muhatap kuralına (bkz. `tebligat_borclusunu_bul`)
    göre yeniden hesaplar ve FARKLIYSA düzeltir — yanlış bağlanmış (ör. tek
    borçlulu dosyada bankaya giden) kayıtlar da ÇÖZÜLÜR (borclu=NULL).
    Yalnız `borclu` alanına yazar. Döner: değişen kayıt sayısı."""
    qs = TebligatBarkod.objects.all()
    if dosya_obj is not None:
        qs = qs.filter(dosya=dosya_obj)
    dosya_idleri = set(qs.values_list("dosya_id", flat=True))
    borclu_haritasi = {}
    for dt in DosyaTaraf.objects.filter(rol="borclu", dosya_id__in=dosya_idleri).select_related("taraf"):
        borclu_haritasi.setdefault(dt.dosya_id, []).append(dt)
    degisen = 0
    for tb_id, dosya_id, aciklama, ham, eski in qs.values_list(
            "id", "dosya_id", "evrak_aciklama", "ham_yanit", "borclu_id"):
        yeni = tebligat_borclusunu_bul(aciklama, borclu_haritasi.get(dosya_id, []), ham)
        yeni_id = yeni.pk if yeni else None
        if yeni_id != eski:
            TebligatBarkod.objects.filter(pk=tb_id).update(borclu_id=yeni_id)
            degisen += 1
    if degisen:
        log(f"… tebligat→borçlu eşlemesi güncellendi: {degisen} barkod kaydı.")
    return degisen


# ── Eski kodla çalışan süreç koruması (2026-09-30) ───────────────────────────
# Kullanıcı bulgusu: masaüstü uygulaması kurallar değiştirilmeden ÖNCE açılmıştı;
# eski kodla kesinleşmeyi yeniden hesaplayıp yeni kuralın yazdığını ezdi
# (2026/8217 "İtiraz" → "Kesinleşme Bekliyor"). Süreç açıldığında hesap
# kurallarını taşıyan dosyaların değiştirilme zamanı saklanır; diskteki dosya
# daha yeniyse bu süreç ESKİ kodla çalışıyordur ve kesinleşme YAZMAZ.
_KURAL_DOSYALARI = [os.path.join(_HERE, ad) for ad in
                    ("dosya_core.py", "kesinlesme_core.py", "barkod_sorgu.py", "icra_core.py")]
_KURAL_DOSYALARI.append(os.path.normpath(os.path.join(_HERE, "..", "..", "models", "icra_models", "models.py")))


def _kural_damgasi():
    damga = {}
    for yol in _KURAL_DOSYALARI:
        try:
            damga[yol] = os.path.getmtime(yol)
        except OSError:
            damga[yol] = None
    return damga


_ACILIS_DAMGASI = _kural_damgasi()
_ESKI_KOD_UYARILDI = False


def kod_eski_mi():
    """Bu süreç açıldıktan sonra hesap kurallarını taşıyan bir dosya
    değiştirildiyse değişen dosya adları listesi; değilse []."""
    simdi = _kural_damgasi()
    return [os.path.basename(y) for y, t in simdi.items()
            if t is not None and _ACILIS_DAMGASI.get(y) is not None and t > _ACILIS_DAMGASI[y] + 1]


# İcra dosya durumu "Açık (Durdurulmuş : X)" (2026-09-30 kullanıcı) → dosyanın
# TÜM borçlularının kesinleşmesi X. "Takibe İtiraz" = itiraz verilmiş ve
# müdürlükçe kabul edilmiş → İtiraz. Anahtarlar `tr_lower` + boşluk sadeleştirme
# sonrası; metinler canlı DB'de görülen UYAP değerleri (UYAP "Borçlunun Ölümü "
# sonunda boşlukla gönderiyor). Listede olmayan neden → Tanımsız (loglanır).
DURDURMA_ANAHTARI = "Durdurulmuş"   # Dosya.durum içinde, UYAP yazımıyla aynen
_DURDURMA_RE = re.compile(r"durdurulmus\s*:\s*(.*?)\s*\)?\s*$")
DURDURMA_NEDENLERI = {
    "takibe itiraz": "itiraz",
    "icra mahkemesine itiraz": "durd_icra_mah_itiraz",
    "genel mahkemeye dava acilmasi": "durd_genel_mah_dava",
    "tehir-i icra karari": "durd_tehiri_icra",
    "ihtiyati tedbir": "durd_ihtiyati_tedbir",
    "iflas": "durd_iflas",
    "borclunun olumu": "durd_borclu_olumu",
    "diger": "durd_diger",
}


def durdurma_kesinlesme_kodu(durum):
    """Dosya durum metninden kesinleşme kodu: "Açık (Durdurulmuş : X)" → X'in
    kodu (bkz. `DURDURMA_NEDENLERI`), bilinmeyen X → "durd_tanimsiz";
    durdurulmamışsa None."""
    m = _DURDURMA_RE.search(" ".join(tr_lower(durum or "").split()))
    if not m:
        return None
    return DURDURMA_NEDENLERI.get(m.group(1).strip(), "durd_tanimsiz")


def kesinlesme_durumlarini_guncelle(dosya_obj=None, log_fn=None):
    """rol=borclu `DosyaTaraf` satırları için kesinlesme_durumu'nu YEREL
    veritabanındaki `TebligatBarkod` kayıtlarından hesaplar ve yazar — UYAP'a
    YENİ istek ATMAZ (bkz. `_borclu_kesinlesme_hesapla`). Önce
    `_tebligat_barkod_borclu_geriye_donuk_doldur` ile ESKİ (borclu FK'sinden
    önce yazılmış) barkod kayıtlarını da bağlar, bu yüzden daha önce çekilmiş
    barkod verisi de hesaba dahil olur. `dosya_obj` verilirse yalnız o
    dosyanın borçluları güncellenir (barkod_sorgu.py bir dosyayı işleyip
    bitirdiğinde çağırır); verilmezse (Yenile akışı) TebligatBarkod'u olan
    TÜM borçlular taranır. Döner: güncellenen satır sayısı; DB/modül
    hazırlanamazsa (ör. PostgreSQL kapalı) sessizce 0."""
    log = log_fn or (lambda *a, **k: None)
    global _ESKI_KOD_UYARILDI
    degisen = kod_eski_mi()
    if degisen:
        if _ESKI_KOD_UYARILDI:        # barkod turunda dosya başına çağrılır — bir kez yaz
            return 0
        _ESKI_KOD_UYARILDI = True
        log("⛔ Program kodu bu pencere açıldıktan sonra güncellendi (" + ", ".join(degisen) +
            ") — kesinleşme ESKİ kuralla yazılmasın diye hesaplanmadı. Uygulamayı kapatıp yeniden açın.")
        return 0
    try:
        _django_hazirla()
        from django.db.models import Q
        from icra_models.models import DosyaTaraf, TebligatBarkod, ResmiTatil, KesinlesmeKurali
        from icra_models.models import kesinlesme_kurali_sec, DurumOlayi, durum_olayi_yaz
        import kesinlesme_core as kc
    except Exception as e:
        log(f"⚠️ Kesinleşme hesabı için DB/modül hazırlanamadı: {e}")
        return 0

    _tebligat_barkod_borclu_geriye_donuk_doldur(DosyaTaraf, TebligatBarkod, dosya_obj, log)

    tatiller = set(ResmiTatil.objects.values_list("tarih", flat=True))
    bugun = datetime.date.today()
    kurallar = list(KesinlesmeKurali.objects.filter(aktif=True))
    kuralsiz = set()
    olaylar = []

    qs = DosyaTaraf.objects.filter(rol="borclu").select_related("dosya", "dosya__icra_detay")
    if dosya_obj is not None:
        qs = qs.filter(dosya=dosya_obj)
    else:
        # Son satır: durdurması kalkmış (UYAP'ta yine düz "Açık") ama barkodu
        # olmayan dosyanın eski durdurma değeri de yeniden hesaplansın.
        qs = qs.filter(Q(dosya__tebligat_barkodlari__isnull=False)
                       | Q(dosya__durum__contains=DURDURMA_ANAHTARI)
                       | Q(kesinlesme_durumu="itiraz") | Q(kesinlesme_durumu__startswith="durd_")).distinct()

    # Borçlu başına ödeme emirleri TEK sorguda (eskiden borçlu başına ayrı sorgu).
    odeme_emirleri = {}
    oe_qs = TebligatBarkod.objects.filter(tebligat_turu="Ödeme Emri", borclu__isnull=False)
    if dosya_obj is not None:
        oe_qs = oe_qs.filter(dosya=dosya_obj)
    for k in oe_qs:
        odeme_emirleri.setdefault(k.borclu_id, []).append(k)

    guncellenen = 0
    # Hesabın ÜRETMEDİĞİ, elle girilecek durumlar (Konkordato/Vefat/Vasi) ezilmez.
    _K = DosyaTaraf.KesinlesmeDurumu
    elle = {_K.KONKORDATO, _K.VEFAT, _K.VASI}
    tanimsiz = set()
    for dt in qs:
        alanlar = []
        durdurma = (durdurma_kesinlesme_kodu(dt.dosya.durum)
                    if dt.dosya.yargi_turu == 2 else None)
        if durdurma == "durd_tanimsiz":
            tanimsiz.add(dt.dosya.durum)
        detay = getattr(dt.dosya, "icra_detay", None) if hasattr(dt.dosya, "icra_detay") else None
        kod = ((detay.takibin_turu, detay.takibin_yolu, detay.takibin_sekli) if detay else ("", "", ""))
        kural = kesinlesme_kurali_sec(kurallar, *kod)
        if kural is None:
            kuralsiz.add("/".join(x or "?" for x in kod))
        sure_gun = kural.itiraz_suresi_gun if kural else None
        yeni, yeni_tarih = _borclu_kesinlesme_hesapla(dt, odeme_emirleri.get(dt.pk, []), tatiller, bugun,
                                                      DosyaTaraf, kc, durdurma, sure_gun)
        if dt.kesinlesme_durumu in elle and not dt.itiraz_edildi_mi:
            yeni, yeni_tarih = dt.kesinlesme_durumu, dt.kesinlesme_tarihi
        if (yeni, yeni_tarih) != (dt.kesinlesme_durumu, dt.kesinlesme_tarihi):
            eski_metin = kesinlesme_metni(dt)
            dt.kesinlesme_durumu, dt.kesinlesme_tarihi = yeni, yeni_tarih
            alanlar += ["kesinlesme_durumu", "kesinlesme_tarihi"]
            olaylar.append((dt, DurumOlayi.Tur.KESINLESME, eski_metin, kesinlesme_metni(dt), yeni_tarih))
        teb, teb_tarih, teb_sekli = _borclu_tebligat_hesapla(odeme_emirleri.get(dt.pk, []),
                                                             DosyaTaraf.TebligatDurumu)
        if (teb, teb_tarih, teb_sekli) != (dt.tebligat_durumu, dt.tebligat_durum_tarihi, dt.tebligat_sekli):
            eski_metin = tebligat_metni(dt)
            dt.tebligat_durumu, dt.tebligat_durum_tarihi, dt.tebligat_sekli = teb, teb_tarih, teb_sekli
            alanlar += ["tebligat_durumu", "tebligat_durum_tarihi", "tebligat_sekli"]
            olaylar.append((dt, DurumOlayi.Tur.TEBLIGAT, eski_metin, tebligat_metni(dt), teb_tarih))
        if alanlar:
            dt.save(update_fields=alanlar)
            guncellenen += 1
    for dt_o, tur_o, eski_o, yeni_o, tarih_o in olaylar:
        durum_olayi_yaz(dt_o.dosya, tur_o, eski_o, yeni_o, dosya_taraf=dt_o, olay_tarihi=tarih_o)
    if kuralsiz:
        log("⚠️ Kesinleşme kuralı tanımlı olmayan takip türü (kesinleşme hesaplanmadı; "
            "Ayarlar > Kesinleşme Süreleri): " + ", ".join(sorted(kuralsiz)))
    if tanimsiz:
        log("⚠️ Tanımsız durdurma nedeni (kesinleşme 'Durdurulmuş: Tanımsız Neden' yazıldı, "
            "DURDURMA_NEDENLERI'ne eklenmeli): " + "; ".join(sorted(tanimsiz)))
    if guncellenen:
        log(f"… kesinleşme/tebliğ durumu güncellendi: {guncellenen} borçlu satırı.")
    return guncellenen


# ── Kesinleşme kuralları (2026-11-14 kullanıcı kararı: süre takip türüne göre) ──
def kesinlesme_kurallari_listele():
    """[{id, takibin_turu, takibin_yolu, takibin_sekli, ad, gun, aktif, aciklama}]"""
    _django_hazirla()
    from icra_models.models import KesinlesmeKurali
    return [{"id": k.pk, "takibin_turu": k.takibin_turu, "takibin_yolu": k.takibin_yolu,
             "takibin_sekli": k.takibin_sekli, "ad": k.ad, "gun": k.itiraz_suresi_gun,
             "aktif": k.aktif, "aciklama": k.aciklama}
            for k in KesinlesmeKurali.objects.all()]


def takip_turu_dagilimi():
    """Veritabanındaki icra dosyalarının takip kodu kümeleri — kural girerken
    hangi kodun kaç dosyada olduğunu görmek için. Döner: [{turu, yolu, sekli,
    adet, ornek}] ve takip türü hiç okunmamış (IcraTakipDetay yok) dosya sayısı."""
    _django_hazirla()
    from django.db.models import Count, Min
    from icra_models.models import Dosya, IcraTakipDetay
    satirlar = []
    for r in (IcraTakipDetay.objects.filter(dosya__yargi_turu=YARGI_TURU_ICRA)
              .values("takibin_turu", "takibin_yolu", "takibin_sekli")
              .annotate(adet=Count("id"), ornek=Min("dosya_id")).order_by("-adet")):
        ornek = Dosya.objects.filter(pk=r["ornek"]).select_related("birim").first()
        satirlar.append({"turu": r["takibin_turu"], "yolu": r["takibin_yolu"],
                         "sekli": r["takibin_sekli"], "adet": r["adet"],
                         "ornek": f"{ornek.birim.ad} {ornek.dosya_no}" if ornek else ""})
    bilinmeyen = Dosya.objects.icra().filter(icra_detay__isnull=True).count()
    return satirlar, bilinmeyen


def kesinlesme_kurali_kaydet(kural_id, turu, yolu, sekli, ad, gun, aktif=True, aciklama=""):
    """Ekler ya da günceller; `gun` None = o türde kesinleşme hesaplanmaz.
    Aynı kod kümesi için ikinci kural açılmaz (ValueError)."""
    _django_hazirla()
    from icra_models.models import KesinlesmeKurali
    turu, yolu, sekli = (str(x or "").strip() for x in (turu, yolu, sekli))
    if gun not in (None, ""):
        gun = int(gun)
        if not 0 < gun <= 365:
            raise ValueError("Süre 1–365 gün arasında olmalı.")
    else:
        gun = None
    cakisan = KesinlesmeKurali.objects.filter(takibin_turu=turu, takibin_yolu=yolu, takibin_sekli=sekli)
    if kural_id:
        cakisan = cakisan.exclude(pk=kural_id)
    if cakisan.exists():
        raise ValueError("Bu takip kodu için zaten bir kural var; onu düzenleyin.")
    k = KesinlesmeKurali.objects.get(pk=kural_id) if kural_id else KesinlesmeKurali()
    k.takibin_turu, k.takibin_yolu, k.takibin_sekli = turu, yolu, sekli
    k.ad, k.itiraz_suresi_gun, k.aktif, k.aciklama = (ad or "").strip(), gun, bool(aktif), aciklama or ""
    k.save()
    return k.pk


def kesinlesme_kurali_sil(kural_id):
    _django_hazirla()
    from icra_models.models import KesinlesmeKurali
    KesinlesmeKurali.objects.filter(pk=kural_id).delete()


# ── Arka plan zamanlayıcı — hem Tkinter hem web SÜREÇ BAŞINA bir kez çağırır ──
# (docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §7 "Kalan" — otomatik senkron döngüsü)
# Tek ortak uygulama burada: iki arayüz de kendi sürecinde bu fonksiyonu bir kez
# çağırır, döngü mantığı İKİ KEZ YAZILMAZ. UYAP oturumu/proxy (127.0.0.1:8800)
# veya DB o an erişilemezse bir tur sessizce loglanıp bir sonraki aralıkta
# yeniden denenir — _load_auth()/boot_autoconnect() gibi YENİ bir oturum AÇMAZ,
# yalnızca zaten var olan bağlantıyı kullanır (bkz. bellek:
# server-load-auth-canli-giris-tuzagi — bu fonksiyon o riski TAŞIMAZ).
VARSAYILAN_ARALIK_SANIYE = 1800  # 30 dakika

# ── Gece tam güncelleme turu (kullanıcı kararı, 2026-09-23) ──────────────────
# Zamanlayıcı her uyanışında (30 dk) gece penceresinde miyiz ve bugün tur
# BAŞARIYLA tamamlandı mı diye bakar; tamamlanmadıysa `guncelleme_hatti`'nı
# GECE_KATMANLARI ile (barkod dahil) çalıştırır. Masaüstü ve web aynı makinede
# ayrı süreçlerde kendi zamanlayıcısını çalıştırdığından tur, PostgreSQL
# advisory kilidiyle TEK sürece sınırlanır. Durum/log dosyaları
# %LOCALAPPDATA%\UyapMasa\ altındadır.
# 2026-09-24 kullanıcı kararı: gece turu RAFA KALDIRILDI (verimli bir düzen
# kurulana kadar). True yapılırsa aşağıdaki pencere/kilit mantığı aynen döner.
GECE_TURU_ETKIN = False
GECE_TURU_BASLANGIC_SAAT = 1   # 01:00
GECE_TURU_BITIS_SAAT = 6       # 06:00'dan sonra YENİ tur başlatılmaz (sürmekte olan devam eder)
_GECE_TURU_KILIT = 7310023     # pg advisory lock anahtarı (uygulamaya özgü sabit)


def _gece_turu_klasoru():
    kok = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or _HERE
    yol = os.path.join(kok, "UyapMasa")
    os.makedirs(yol, exist_ok=True)
    return yol


def gece_turu_durumu_oku():
    """Son gece turunun özeti ({"son_tarih", "baslangic", "bitis", "sonuc", ...}).
    Dosya yoksa/okunamazsa {}."""
    import json
    try:
        with open(os.path.join(_gece_turu_klasoru(), "gece_turu.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _gece_turu_durumu_yaz(durum):
    import json
    try:
        with open(os.path.join(_gece_turu_klasoru(), "gece_turu.json"), "w", encoding="utf-8") as f:
            json.dump(durum, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _gece_turu_zamani_mi(simdi=None):
    if not GECE_TURU_ETKIN:
        return False
    simdi = simdi or datetime.datetime.now()
    if not (GECE_TURU_BASLANGIC_SAAT <= simdi.hour < GECE_TURU_BITIS_SAAT):
        return False
    return gece_turu_durumu_oku().get("son_tarih") != simdi.date().isoformat()


def gece_tam_guncelleme(log_fn=None):
    """Tüm yargı türlerinde künye (Açık+Kapalı) + eksik taraf, ardından TÜM
    açık İcra dosyalarının evrak taraması/barkod sorgusu, en sonda kesinleşme
    (bkz. `guncelleme_hatti`). Toplu iş kayıt defterine "Gece Tam Güncelleme"
    adıyla girer — kullanıcı sabah başka bir toplu iş başlatırsa mevcut
    çakışma penceresi bunu gösterir (durdur/karma). `son_tarih` YALNIZ tur
    hatasız bittiğinde (ya da kullanıcı durdurduğunda) yazılır; hata olursa
    pencere içinde bir sonraki uyanışta yeniden denenir. Başka bir süreç turu
    zaten çalıştırıyorsa hiçbir şey yapmaz ve None döner."""
    import toplu_is_kontrol
    ust_log = log_fn or (lambda m: None)
    bugun = datetime.date.today().isoformat()
    log_yolu = os.path.join(_gece_turu_klasoru(), f"gece_turu_{bugun}.log")

    def log(m):
        ust_log(m)
        try:
            with open(log_yolu, "a", encoding="utf-8") as f:
                f.write(f"{datetime.datetime.now():%H:%M:%S} {m}\n")
        except Exception:
            pass

    # 2026-09-24: elle başlatılmış bir iş sürerken (duraklatılmış olsa bile)
    # gece turu BAŞLAMAZ — eskiden `zorla_ekle` ile paralel giriyordu. Bu süreçteki
    # toplu işler KAYIT_DEFTERI'nden, diğer süreçlerdeki (masaüstü/web/bağımsız
    # betik) barkod turları nabız dosyalarından görülür. `son_tarih` yazılmadığı
    # için pencere içinde bir sonraki uyanışta yeniden denenir.
    import barkod_sorgu as _bs
    suren = list(toplu_is_kontrol.KAYIT_DEFTERI.anlik()) + [
        f"Barkod turu ({t.get('mod')}, pid {t.get('pid')}, {t.get('durum')})" for t in _bs.aktif_turlar()]
    if suren:
        ust_log("… gece tam güncelleme turu ertelendi — süren iş var: " + ", ".join(suren))
        return None

    _django_hazirla()
    from django.db import connection
    with connection.cursor() as c:
        c.execute("SELECT pg_try_advisory_lock(%s)", [_GECE_TURU_KILIT])
        kilit = c.fetchone()[0]
    if not kilit:
        ust_log("… gece tam güncelleme turu başka bir süreçte (masaüstü/web) sürüyor — atlandı.")
        return None

    kontrol = toplu_is_kontrol.TopluIsKontrolu(ad="Gece Tam Güncelleme")
    if toplu_is_kontrol.KAYIT_DEFTERI.basvur(kontrol.ad, kontrol)[0] != "ok":
        # Kontrolle kilit arasında elle bir iş başlatılmış — ona yol ver.
        ust_log("… gece tam güncelleme turu ertelendi — az önce başka bir iş başladı.")
        with connection.cursor() as c:
            c.execute("SELECT pg_advisory_unlock(%s)", [_GECE_TURU_KILIT])
        return None
    durum = dict(gece_turu_durumu_oku())
    durum.update({"baslangic": datetime.datetime.now().isoformat(timespec="seconds"),
                  "bitis": None, "sonuc": "sürüyor", "log": log_yolu})
    _gece_turu_durumu_yaz(durum)
    try:
        log("🌙 Gece tam güncelleme turu başladı (künye + taraf + barkod + kesinleşme).")
        sonuc = guncelleme_hatti(log, katmanlar=GECE_KATMANLARI, tum_turler=True,
                                 kontrol=kontrol, istek_sarici=_arka_plan_istek)
        durum.update({"son_tarih": bugun,
                      "sonuc": "durduruldu" if kontrol.durduruldu else "tamamlandı",
                      "kunye_toplam": sonuc["toplam"], "barkod_satir": sonuc["barkod_satir"]})
        log(f"🌙 Gece turu {durum['sonuc']}: {sonuc['toplam']} künye tarandı, "
            f"{sonuc['barkod_satir'] or 0} barkod satırı işlendi.")
        return sonuc
    except Exception as e:
        durum.update({"sonuc": f"hata: {e}"})
        log(f"⚠️ Gece turu hata ile bitti: {e}")
        raise
    finally:
        durum["bitis"] = datetime.datetime.now().isoformat(timespec="seconds")
        _gece_turu_durumu_yaz(durum)
        toplu_is_kontrol.KAYIT_DEFTERI.sil(kontrol.ad)
        try:
            with connection.cursor() as c:
                c.execute("SELECT pg_advisory_unlock(%s)", [_GECE_TURU_KILIT])
        except Exception:
            pass

_zamanlayici_thread = None
_zamanlayici_dur = None


def senkron_zamanlayici_baslat(interval_saniye=None, log_fn=None):
    """Arka planda periyodik `DosyaSorgu.calistir()` döngüsü başlatır (daemon
    thread). Süreç başına yalnız bir kez gerçekten başlar; art arda çağrılar
    (ör. iki UyapMasa aynı süreçte dosya_core'u kullanıyorsa) no-op'tur — çalışan
    thread'i döner. `SenkronKapsami` boşsa `calistir()` zaten hızlıca 0,[]
    dönüp bir sonraki aralığı bekler; bu normaldir, hata değildir.

    İSTEMCİ modunda (bkz. `bu_makine_istemci_mi`) HİÇ başlamaz — bu makine
    ofisin UYAP oturumunu tutan sunucu değil, o yüzden kendi başına UYAP'ı
    taramamalı (kullanıcı bulgusu, 2026-07-13). Hem `uyapmasa.py`'nin hem
    `web/server.py`'nin çağrı noktası bu TEK fonksiyonu kullandığından bu
    kontrol ikisini birden kapsar."""
    if bu_makine_istemci_mi():
        (log_fn or (lambda m: None))(
            "… bu makine 'İstemci' modunda (Ayarlar > Bağlantı) — yerel "
            "veritabanı/arka plan senkronu başlatılmadı, dosyalar ofis "
            "sunucusunun web arayüzünden görüntülenmeli.")
        return None
    global _zamanlayici_thread, _zamanlayici_dur
    if _zamanlayici_thread is not None and _zamanlayici_thread.is_alive():
        return _zamanlayici_thread
    aralik = interval_saniye or VARSAYILAN_ARALIK_SANIYE
    log = log_fn or (lambda m: None)
    durdur = threading.Event()
    _zamanlayici_dur = durdur

    def _dongu():
        log(f"… otomatik senkron zamanlayıcı başladı ({aralik} sn aralıkla).")
        while not durdur.is_set():
            try:
                # Düşük öncelikli geçit: kullanıcının o anki tıklaması (Dosya
                # Görüntüle/Yenile) HER TEK UYAP isteğinden önce araya girebilsin
                # diye — bkz. _arka_plan_istek/_bireysel_istek docstring'i.
                toplam, sonuclar = DosyaSorgu(log, istek_sarici=_arka_plan_istek).calistir()
                if sonuclar:
                    log(f"✔ Otomatik senkron turu: {toplam} kayıt tarandı "
                        f"({len(sonuclar)} kapsam).")
            except Exception as e:
                log(f"⚠️ Otomatik senkron turu başarısız (bir sonraki aralıkta "
                    f"yeniden denenecek): {e}")
            try:
                if _gece_turu_zamani_mi():
                    gece_tam_guncelleme(log)
            except Exception as e:
                log(f"⚠️ Gece tam güncelleme turu başarısız (pencere içinde bir "
                    f"sonraki aralıkta yeniden denenecek): {e}")
            durdur.wait(aralik)
        log("… otomatik senkron zamanlayıcı durduruldu.")

    _zamanlayici_thread = threading.Thread(target=_dongu, daemon=True)
    _zamanlayici_thread.start()
    return _zamanlayici_thread


def senkron_zamanlayici_durdur():
    """Çalışan zamanlayıcıyı (varsa) bir sonraki uyanışında nazikçe durdurur."""
    if _zamanlayici_dur is not None:
        _zamanlayici_dur.set()
