# -*- coding: utf-8 -*-
"""
İcra Dosyalarım — headless sorgu motoru (GUI + web ortak)
=========================================================
UYAP "Dosya Sorgulama" ekranının  İcra > Dosyalarım  sorgusunu, suite'te zaten
KANITLI olan tekniği yeniden kullanarak yapar: istek yerel ofis proxy'sine
(127.0.0.1:8800) gider, ofis (uyap_app.py) bunu canlı e-imza oturumuyla UYAP'a
iletir. Tarayıcı / Playwright YOK.

Aktarım katmanı, orijinal "Sorgu/SGK Sorgu/sgk_sorgu_gui.py" içindeki
SorguMotoru._post'tan AYNEN kullanılır (COMMON_HEADERS, retry, oturum-hatası
algılama, 8800 proxy adresi). Buradaki tek yeni iş:
  • 'search_phrase_detayli.ajx' için tam sorgu payload'ını alanlardan kurmak,
  • dönen kayıt listesini ayrıştırmak.

Payload alan adları kullanıcının gerçek UYAP isteklerinden yakalandı:
  dosyaYil, dosyaSira, birimId,
  dosyaAcilisTarihiStart, dosyaAcilisTarihiEnd   (GG.AA.YYYY),
  Kişi  : tarafTuru=0  + adi / soyadi / tcKimlikNo,
  Kurum : tarafTuru=1  + kurumAdi / mersisNo / vergiNo,
  Sabit : dosyaDurumKod=0, pageSize, pageNumber, birimTuru2="1101", birimTuru3="2"
"""

import os
import re as _re
import sys
import time

# Orijinal SGK modülünü import yoluna ekle — aktarımı (transport) ondan alacağız.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SGK_DIR = os.path.normpath(os.path.join(_HERE, "..", "..", "Sorgu", "SGK Sorgu"))
if _SGK_DIR not in sys.path:
    sys.path.insert(0, _SGK_DIR)

import sgk_sorgu_gui as _sgk  # noqa: E402  (orijinal — değiştirilmez)

# Orijinalden AYNEN: 8800 proxy'ye giden POST motoru ve sabitler
SorguMotoru = _sgk.SorguMotoru
OturumHatasi = _sgk.OturumHatasi
BIRIM_TURU2 = _sgk.BIRIM_TURU2   # "1101" -> İcra Dairesi
BIRIM_TURU3 = _sgk.BIRIM_TURU3   # "2"    -> İcra

# ── Adil sıralama: icra sorguları OKUMADIR ───────────────────────────────────
# icra (ve SGK) dosya sorguları durum DEĞİŞTİRMEZ; yalnızca okur. Ama UYAP onları
# POST ile yaptırıyor; ofis de POST'ları varsayılan olarak "yazma" sayıp TEK bir
# yazma kilidine sokuyor. 8999 dosyalık toplu icra sorgusu bu kilidi sürekli
# tutunca DİĞER kullanıcıların bireysel istekleri kuyrukta bekliyordu. Çözüm:
# ofise giden tüm SorguMotoru isteklerine 'X-Uyap-Read' ipucunu ekle → ofis bunları
# yazma kilidine HİÇ sokmaz (jobs tarafındaki write=False'un istemci-tarafı eşi;
# bkz. uyap_core/_runtime.is_read_hint). SorguMotoru salt-okuma bir sorgu motoru
# olduğundan başlığı ortak transport başlıklarına tek seferde eklemek güvenlidir.
# UYARI: İleride SorguMotoru ile bir YAZMA yapılırsa bu başlık o isteğe gitMEMELİ.
_sgk.COMMON_HEADERS.setdefault("X-Uyap-Read", "1")

ENDPOINT = "search_phrase_detayli.ajx"
PAGE_SIZE = 500

# ── UYAP 'eşzamanlı sorgulama' reddi — sayfalama sırasında SESSİZ veri kaybı ──
# Kullanıcı bulgusu (2026-09-22, "İcra Dosyalarım tüm açık dosyaları
# çekmiyor gibi"): 500'lük sayfalar halinde binlerce dosya (kullanıcının
# durumunda 6500+, yani 13+ ardışık sayfa isteği) çekilirken UYAP ARA SIRADA
# {"errorCode":"PRTL_GNL_10001-...","error":"Eş zamanlı olarak birden fazla
# sorgulama yapamazsınız! "} reddini HTTP 200 ile döndürebiliyor (bkz. canlı
# örnek, docs/VERITABANI_BULGULARI_07_ARABULUCULUK_TAZMINAT.md). Bu SAYFA
# DÜZEYİNDEKİ döngü eskiden bu reddi doğrudan `parse_records`'a veriyordu —
# `parse_records` bilinmeyen şekildeki bir dict'i TEK KAYITLI bir liste
# sanıyor (`return [veri]`, bkz. o fonksiyonun 'savunmacı' dalı), yani hata
# zarfının KENDİSİ sahte bir "1 kayıtlık son sayfa" oluyordu — döngü
# `len(kayitlar) < PAGE_SIZE` görüp BAŞARIYLA BİTTİĞİNİ sanıp duruyordu,
# kalan TÜM sayfalar (binlerce dosya) hiç çekilmeden, HİÇBİR HATA/UYARI
# göstermeden sessizce eksik kalıyordu. `dosya_core.py`'nin kendi sayfalama
# döngüsü (`DosyaSorgu._bir_kapsami_ara`) bu redde karşı zaten korumalıydı
# (`_post_eszamanli_korumali` + `_yanit_eszamanli_hata_mi`) — bu modül
# (icra_core) o korumayı hiç PAYLAŞMIYORDU (dosya_core zaten icra_core'u
# İTHAL ETTİĞİ için ters yönde import edilemez, dairesel bağımlılık olurdu) —
# bu yüzden AYNI mantığın küçük, bağımsız bir kopyası burada tutulur.
_ESZAMANLI_IMZA = "birden fazla sorgulama yapamaz"


def _yanit_eszamanli_hata_mi(veri, derinlik=0):
    """`veri` (motor._post'un döndürdüğü ham/parse edilmiş yanıt) içinde
    UYAP'ın eşzamanlı-red imzası var mı? dosya_core._yanit_eszamanli_hata_mi
    ile AYNI mantık (bkz. modül düzeyi not)."""
    if derinlik > 4:
        return False
    if isinstance(veri, str):
        return _ESZAMANLI_IMZA in veri.lower()
    if isinstance(veri, dict):
        return any(_yanit_eszamanli_hata_mi(v, derinlik + 1) for v in veri.values())
    if isinstance(veri, (list, tuple)):
        return any(_yanit_eszamanli_hata_mi(v, derinlik + 1) for v in veri)
    return False


def _post_sayfa_korumali(motor, endpoint, payload, log_fn, tekrar=4, bekleme_saniye=3):
    """`motor._post`'u çağırır; yanıt UYAP'ın 'eşzamanlı' reddiyse bunu
    GERÇEK veri SANMAZ — artan aralıklarla (3sn, 6sn, 9sn, 12sn…) tekrar
    dener (bir YARIŞ DURUMUDUR, genelde birkaç saniye sonra kendiliğinden
    geçer; dosya_core._post_eszamanli_korumali ile AYNI tolerans, 30sn).
    Tüm denemeler tükenirse None döner — çağıran taraf bunu 'sayfa
    alınamadı' ile AYNI ele almalı, ASLA 'son/boş sayfa' ile KARIŞTIRMAMALI
    (bkz. `IcraSorgu.ara` çağrı yeri)."""
    log = log_fn or (lambda *a, **k: None)
    for deneme in range(tekrar + 1):
        _status, veri = motor._post(endpoint, payload)
        if not _yanit_eszamanli_hata_mi(veri):
            return veri
        if deneme < tekrar:
            bekle = bekleme_saniye * (deneme + 1)
            log(f"⚠️ UYAP 'eşzamanlı sorgulama' isteği reddetti ({endpoint}) — "
                f"bu GERÇEK veri değil, {bekle}sn sonra tekrar denenecek "
                f"({deneme + 1}/{tekrar}).")
            time.sleep(bekle)
        else:
            log(f"⚠️ UYAP 'eşzamanlı sorgulama' isteği reddetti ({endpoint}) — "
                f"{tekrar} deneme sonunda hâlâ reddediliyor.")
    return None

def sayfa_yaniti_gecersiz_mi(veri):
    """Sayfa yanıtı GERÇEK bir kayıt listesi değil mi? None (eşzamanlı-red
    denemeleri tükendi) ya da UYAP'ın genel hata zarfı ({'errorCode': ...}).
    İkisi de eskiden `parse_records` ile "0/1 kayıtlık SON sayfa" sanılıp tarama
    BAŞARILI görünerek duruyordu — kalan sayfalar sessizce eksik kalıyordu."""
    return veri is None or (isinstance(veri, dict) and "errorCode" in veri)


def uyap_toplam_sayisi(veri):
    """search_phrase_detayli.ajx yanıtı `[kayıtlar, toplam]` biçimindeyse
    UYAP'ın bildirdiği TOPLAM kayıt sayısını döner, değilse None. Bu biçim yalnız
    Cbs aramasında canlı doğrulandı (bkz. dosya_core.py Cbs notu) — diğer
    türlerde ikinci eleman yoksa/sayı değilse UYDURULMAZ, None döner ve sayım
    raporu "UYAP toplam bildirmedi" der."""
    if isinstance(veri, (list, tuple)) and len(veri) >= 2 and isinstance(veri[0], list):
        t = veri[1]
        if isinstance(t, bool):
            return None
        if isinstance(t, int):
            return t
        if isinstance(t, str) and t.strip().isdigit():
            return int(t.strip())
    return None


def sayim_satiri(etiket, alinan, uyap_toplam, tam):
    """Tek bir tarama kapsamı için okunur sayım satırı + eksik mi bilgisi.
    Döner: (metin, eksik_mi). `tam=False` → sayfalardan biri alınamadı."""
    if not tam:
        return (f"⚠️ {etiket}: {alinan} dosya alındı — tarama YARIM kaldı "
                "(bir sayfa alınamadı), liste EKSİK olabilir.", True)
    if uyap_toplam is None:
        return (f"… {etiket}: {alinan} dosya alındı (UYAP toplam sayı bildirmedi ya da "
                "taraf filtreli arama — karşılaştırılamadı).", False)
    if alinan == uyap_toplam:
        return f"✔ {etiket}: UYAP {uyap_toplam} dosya bildirdi, {alinan} dosyanın tamamı alındı.", False
    return (f"⚠️ {etiket}: UYAP {uyap_toplam} dosya bildirdi ama {alinan} alındı — "
            f"{abs(uyap_toplam - alinan)} fark var.", True)

# İcra birimleri (mahkeme) listesi — "Dosya Sorgulama" ekranında yargı türü/birimi
# seçilince açılan liste. Birim ADINI birimId'ye çevirip aramayı SUNUCU-TARAFLI
# yapmak için kullanılır (yoksa "Dikili" yazınca tüm ofisler çekilip ekranda
# istemci-taraflı süzülüyordu). Payload: yargiTuru=İcra(2), yargiBirimi=Daire(1101).
BIRIM_LISTE_ENDPOINT = "avukat_mahkemeleri_sorgula.ajx"
_BIRIM_LISTE = None     # [{"birimAdi","birimId"}, ...] (önbellek; oturum boyunca)
_BIRIM_AD2ID = {}       # _birim_norm(ad) -> birimId


def _birim_norm(s):
    """Birim adı karşılaştırması için sade biçim (TR harf duyarsız, boşluk sade)."""
    s = s or ""
    for a, b in (("ı", "i"), ("İ", "i"), ("ş", "s"), ("Ş", "s"), ("ğ", "g"),
                 ("Ğ", "g"), ("ü", "u"), ("Ü", "u"), ("ö", "o"), ("Ö", "o"),
                 ("ç", "c"), ("Ç", "c")):
        s = s.replace(a, b)
    return " ".join(s.lower().split())


def tr_lower(s):
    """Türkçe-duyarlı küçük harf (filtre/karşılaştırma için). İ/I/ı→i, ş→s, ğ→g,
    ü→u, ö→o, ç→c sonra .lower(); böylece 'İZMİR'/'izmir'/'İzmir' hepsi eşleşir
    (Python'ın .lower()'ı 'İ' → 'i̇' birleşik nokta yapıp eşleşmeyi bozuyordu)."""
    s = s or ""
    for a, b in (("İ", "i"), ("I", "i"), ("ı", "i"), ("Ş", "s"), ("ş", "s"),
                 ("Ğ", "g"), ("ğ", "g"), ("Ü", "u"), ("ü", "u"), ("Ö", "o"),
                 ("ö", "o"), ("Ç", "c"), ("ç", "c")):
        s = s.replace(a, b)
    return s.lower()


_TARIH_SIRALAMA_RE = _re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")


def tarih_siralama_anahtari(deger):
    """'GG.AA.YYYY' -> sıralanabilir 'YYYYAAGG'. Ayrıştırılamazsa None döner.
    Kullanıcı bulgusu (2026-08-14): "Açılış Tarihi" gibi tarih sütunları düz
    METİN olarak sıralanınca GÜN basamağı öne geldiği için kronolojik sıra
    YANLIŞ çıkıyordu (ör. "01.03.2026" metin-sırasında "15.01.2026"dan önce
    gelir, oysa Ocak Mart'tan ÖNCEDİR) — bkz. dosyalarim_genel.py
    _ACILIS_TARIHI_RE ile AYNI mantık, o ekranda zaten düzeltilmişti, burada
    (İcra Dosyalarım) EKSİKTİ."""
    m = _TARIH_SIRALAMA_RE.match(str(deger or "").strip())
    if not m:
        return None
    gg, aa, yyyy = m.groups()
    return f"{yyyy}{aa}{gg}"


def birim_listesi_getir(log_fn=None, zorla=False):
    """İcra birimlerini UYAP'tan (avukat_mahkemeleri_sorgula.ajx) çekip önbelleğe alır.
    Döner: list[{"birimAdi","birimId"}]. Ağ hatasında eldeki önbelleği (varsa) döndürür."""
    global _BIRIM_LISTE, _BIRIM_AD2ID
    if _BIRIM_LISTE is not None and not zorla:
        return _BIRIM_LISTE
    motor = SorguMotoru(log_fn or (lambda *a, **k: None))
    payload = {"yargiTuru": BIRIM_TURU3, "yargiBirimi": BIRIM_TURU2, "dosyaKapaliMi": False}
    try:
        _status, veri = motor._post(BIRIM_LISTE_ENDPOINT, payload)
    except Exception:
        return _BIRIM_LISTE or []
    liste = veri if isinstance(veri, list) else []
    temiz = [{"birimAdi": b.get("birimAdi", "") or "", "birimId": str(b.get("birimId", "") or "")}
             for b in liste if isinstance(b, dict) and b.get("birimId")]
    if temiz:
        _BIRIM_LISTE = temiz
        _BIRIM_AD2ID = {_birim_norm(b["birimAdi"]): b["birimId"] for b in temiz}
    return _BIRIM_LISTE or []


def birim_id_bul(ad):
    """Birim ADINI birimId'ye çevirir (önbellekten; AĞ YOK). Önce TAM eşleşme;
    olmazsa TEK birim içeren alt-dize eşleşmesi (ör. 'Dikili' → 'Dikili İcra
    Dairesi'). Eşleşme yok ya da BİRDEN ÇOK ise "" (çağıran net hata verir)."""
    if not ad:
        return ""
    n = _birim_norm(ad)
    if not n:
        return ""
    if n in _BIRIM_AD2ID:
        return _BIRIM_AD2ID[n]
    eslesen = {v for k, v in _BIRIM_AD2ID.items() if n in k}
    return eslesen.pop() if len(eslesen) == 1 else ""


def birim_adlari():
    """Önbellekteki birim adları (AĞ YOK; açılır liste için). Yüklü değilse []."""
    return [b["birimAdi"] for b in (_BIRIM_LISTE or [])]


def birimleri_db_den_yukle():
    """Ofis (8800) KAPALIYKEN birim açılır listesini YEREL DB'den (Birim tablosu)
    doldurur — böylece birim filtresi/oto-tamamlama çevrimdışı da çalışır. Yalnız
    önbellek boşsa iş yapar. Döner: list[{"birimAdi","birimId"}]."""
    global _BIRIM_LISTE, _BIRIM_AD2ID
    if _BIRIM_LISTE:
        return _BIRIM_LISTE
    try:
        import os as _os, sys as _sys
        mdir = _os.path.normpath(_os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)), "..", "..", "models"))
        if mdir not in _sys.path:
            _sys.path.insert(0, mdir)
        _os.environ.setdefault("DJANGO_SETTINGS_MODULE", "uyapdata.settings")
        import django
        django.setup()
        from icra_models.models import Birim
        temiz = [{"birimAdi": b.ad, "birimId": str(b.birim_id)}
                 for b in Birim.objects.exclude(ad="").all() if b.birim_id]
    except Exception:
        return _BIRIM_LISTE or []
    if temiz:
        _BIRIM_LISTE = temiz
        _BIRIM_AD2ID = {_birim_norm(b["birimAdi"]): b["birimId"] for b in temiz}
    return _BIRIM_LISTE or []


_DB_MODELLER = None


def _db_modeller():
    """Django'yu (bir kez) kurar ve icra modellerini döndürür. DB erişilemezse
    istisna yükseltir (çağıran yakalar). Önbellekli."""
    global _DB_MODELLER
    if _DB_MODELLER is not None:
        return _DB_MODELLER
    import os as _os, sys as _sys
    mdir = _os.path.normpath(_os.path.join(
        _os.path.dirname(_os.path.abspath(__file__)), "..", "..", "models"))
    if mdir not in _sys.path:
        _sys.path.insert(0, mdir)
    _os.environ.setdefault("DJANGO_SETTINGS_MODULE", "uyapdata.settings")
    import django
    django.setup()
    from icra_models.models import Dosya, Birim, Taraf, DosyaTaraf
    Dosya.objects.exists()   # bağlantı testi (DB kapalıysa burada patlar)
    _DB_MODELLER = {"Dosya": Dosya, "Birim": Birim, "Taraf": Taraf, "DosyaTaraf": DosyaTaraf}
    return _DB_MODELLER


def db_dosyalari_getir(values, durum_kod=0):   # 0 = Açık (DURUM_VARSAYILAN)
    """Girilen kriterlere uyan dosyaları YEREL DB'den döndürür (UYAP'a GİTMEDEN).
    Döner: list[dict] — arama kaydı biçiminde (kolon_degeri ile uyumlu: dosyaId,
    dosyaNo, dosyaTur, birimAdi, alacakli, borclu, dosyaAcilisTarihi). DB
    erişilemezse []. UyapMasa böylece ofis kapalı olsa bile eldeki veriyi gösterir.
    Taraf (alacaklı/borçlu) metni filtresi ekranda (istemci-taraflı) uygulanır."""
    try:
        M = _db_modeller()
    except Exception:
        return []
    Dosya = M["Dosya"]
    # Yalnız İcra dosyaları (2026-09-23): eskiden yargı türüne göre SÜZÜLMÜYORDU —
    # 'Tüm Dosyalarım'ın indirdiği Hukuk/Ceza/... dosyaları da İcra Dosyalarım'da
    # görünüyor, 'Barkod'u da Güncelle' kuyruğuna da giriyordu.
    qs = Dosya.objects.filter(durum_kod=durum_kod).filter(_icra_dosyasi_q())
    birim_val = (values.get("birimAdi") or "").strip()
    if birim_val:
        bid = birim_id_bul(birim_val)
        if bid:
            qs = qs.filter(birim__birim_id=bid)
        else:
            qs = qs.filter(birim__ad__icontains=birim_val)
    yil = (values.get("dosyaYil") or "").strip()
    if yil.isdigit():
        qs = qs.filter(yil=int(yil))
    no = (values.get("dosyaNo") or "").strip()
    if "/" in no:
        no = no.split("/", 1)[1].strip()
    if no.isdigit():
        qs = qs.filter(sira_no=int(no))
    tur = (values.get("dosyaTur") or "").strip()
    if tur:
        qs = qs.filter(tur__icontains=tur)
    # ALACAKLI/BORÇLU sütun filtresi (kullanıcı bulgusu, 2026-08-14: "sütunlarda
    # filtreleme yapsam bile Sorgula'ya basınca o daireki TÜM dosyaları
    # sorguluyor") — bu fonksiyon eskiden yalnız birim/yıl/dosyaNo/tür'e bakıp
    # taraf adı metnini TAMAMEN YOK SAYIYORDU (dosyalarim_db_listele'nin AYNI
    # işi doğru yaptığı halde). Aşağıda dosya_core._taraf_isim_eslesir İLE AYNI
    # (Türkçe-duyarlı, C-locale ILIKE sorununu atlayan) Python-taraflı eşleşme
    # kullanılır — DB seviyesinde icontains YAZILMAZ (aynı gerekçe).
    alacakli_l = tr_lower((values.get("alacakli") or "").strip())
    borclu_l = tr_lower((values.get("borclu") or "").strip())
    # DÜZ import (relative DEĞİL): icra_core çoğu zaman sys.path üzerinden düz
    # modül olarak yüklenir (barkod_sorgu, teblig_21_2_core "import icra_core").
    import dosya_core as _dc2
    _eslesir = _dc2._taraf_isim_eslesir
    out = []
    # ÖNEMLİ (kullanıcı bulgusu, 2026-08-14): eskiden burada [:8000] sabit bir
    # kesme vardı — portföy bu sayıyı geçince (bkz. dosyalarim_db_listele'nin
    # AYNI SINIRLAMAYA sahip OLMADIĞI, kullanıcının "Tüm Dosyalarım"da görüp
    # "İcra Dosyalarım"da GÖREMEDİĞİ tam da bu yüzdendi) en düşük dosyaNo'lu
    # (ör. yeni açılan banka dosyaları, sıra no'su küçük ofislerde) kayıtlar
    # SESSİZCE listeden düşüyordu — kesme, "en yeni açılan" değil "en yüksek
    # dosyaNo'lu" kayıtları koruyordu. dosyalarim_db_listele ile TUTARLI olsun
    # diye kesme kaldırıldı.
    # 2026-09-25 hız çalışması: model nesnesi kurulmadan okunur (dosyalar tek
    # sorgu + taraf bağları tek sorgu, bkz. dosya_core._yalin_taraf_baglari) —
    # çıktı eskisiyle birebir aynı (karşılaştırılarak doğrulandı).
    qs = qs.order_by("-yil", "-sira_no", "-id")   # -id: eşitlerde sıra her açılışta aynı
    baglar_haritasi = _dc2._yalin_taraf_baglari(qs)
    for d in qs.values_list("id", "dosya_id", "dosya_no", "yil", "sira_no", "tur", "tur_kod",
                            "birim__ad", "birim__birim_id", "acilis_tarihi", "durum", named=True):
        baglar = baglar_haritasi.get(d.id, [])
        if _eslesir is not None:
            if alacakli_l and not any(b.rol == "alacakli" and _eslesir(b.taraf, alacakli_l) for b in baglar):
                continue
            if borclu_l and not any(b.rol == "borclu" and _eslesir(b.taraf, borclu_l) for b in baglar):
                continue
        alac = [str(b.taraf) for b in baglar if b.rol == "alacakli"]
        borc = [str(b.taraf) for b in baglar if b.rol == "borclu"]
        # Kesinleşme + Tebliğ Durumu borçlu başına, Tüm Dosyalarım ile AYNI
        # fonksiyondan (2026-09-25 — tebliğ = borçlunun ödeme emri durumu).
        kesinlesme, tebligat = _dc2.borclu_durum_metinleri(baglar)
        out.append({
            "dosyaId": d.dosya_id,
            "dosyaNo": d.dosya_no or (f"{d.yil}/{d.sira_no}" if d.yil else ""),
            "dosyaTur": d.tur or "",
            "birimAdi": d.birim__ad or "",
            # Doğal anahtarın geri kalanı (2026-09-23): Dosya Görüntüle
            # (dosya_core.dosya_detay_goster_ve_kaydet) ve barkod listesi
            # (tebligat_barkod_gecmisi_listele) Dosya'yı birimId+dosyaTurKod ile
            # arıyor — DB'den gelen satırda bunlar YOKTU, Sorgula'ya basılmadan
            # açılan detay "Dosya yerel veritabanında bulunamadı" verip barkodları
            # hiç göstermiyordu.
            "birimId": str(d.birim__birim_id),
            "dosyaTurKod": d.tur_kod,
            "alacakli": ", ".join(alac),
            "borclu": ", ".join(borc),
            "dosyaAcilisTarihi": d.acilis_tarihi,
            "dosyaDurum": d.durum or "",   # dosya kartı "Durum" alanı (Takibe İtiraz vb.)
            "dbId": d.id,
            "kesinlesme_durumu": kesinlesme,
            "tebligat_durumu": tebligat,
            "borclu_satirlari": _dc2.borclu_satirlari(baglar),   # ekranda borçlu başına satır
        })
    return out

# ─────────────────────────────────────────────────────────────────────────────
# Sorgu alanları. Her biri OPSİYONEL; kullanıcı tek tek açıp kapatabilir ve bu
# tercih kaydedilir (bkz. GUI/web). 'pkey' = UYAP payload alan adı.
# ─────────────────────────────────────────────────────────────────────────────
FIELDS = [
    {"key": "alacakli",               "label": "Alacaklı",            "group": "Taraf", "type": "text", "pkey": "alacakli", "default": True,  "ph": "Alacaklı adı"},
    {"key": "borclu",                 "label": "Borçlu",              "group": "Taraf", "type": "text", "pkey": "borclu", "default": True,  "ph": "Borçlu adı"},
    {"key": "birimAdi",               "label": "Birim Adı",           "group": "Dosya", "type": "text", "pkey": "birimAdi", "default": True,  "ph": "Birim adı/No"},
    {"key": "dosyaYil",               "label": "Dosya Yılı",          "group": "Dosya", "type": "int",  "pkey": "dosyaYil", "default": True,  "ph": "YYYY"},
    {"key": "dosyaNo",                "label": "Dosya No",            "group": "Dosya", "type": "text", "pkey": "dosyaNo", "default": True,  "ph": "Sıra No"},
    {"key": "dosyaTur",               "label": "Dosya Türü",          "group": "Dosya", "type": "text", "pkey": "dosyaTur", "default": True,  "ph": "Esas/Talimat"},
    {"key": "acilisTarihi",           "label": "Açılış Tarihi",       "group": "Tarih", "type": "date", "pkey": "acilisTarihi", "default": True,  "ph": "GG.AA.YYYY"},
    {"key": "dosyaAcilisTarihiStart", "label": "Açılış Başlangıç",     "group": "Tarih", "type": "date", "pkey": "dosyaAcilisTarihiStart", "default": False, "ph": "GG.AA.YYYY"},
    {"key": "dosyaAcilisTarihiEnd",   "label": "Açılış Bitiş",         "group": "Tarih", "type": "date", "pkey": "dosyaAcilisTarihiEnd",   "default": False, "ph": "GG.AA.YYYY"},
    # Kullanıcı isteği (2026-08-14): bu iki sütun "Tüm Dosyalarım"da DEĞİL
    # "İcra Dosyalarım"da görünsün — YALNIZCA yerel DB'den (db_dosyalari_getir)
    # doldurulur, UYAP'a canlı arama parametresi olarak GÖNDERİLMEZ (build_payload
    # bu key'leri bilmiyor, bilerek — UYAP'ta böyle bir arama alanı YOK).
    {"key": "kesinlesme_durumu",      "label": "Kesinleşme Durumu",   "group": "Durum", "type": "text", "pkey": None, "default": True,  "ph": ""},
    {"key": "tebligat_durumu",        "label": "Tebliğ Durumu",       "group": "Durum", "type": "text", "pkey": None, "default": True,  "ph": ""},
]
FIELD_BY_KEY = {f["key"]: f for f in FIELDS}
DEFAULT_ACTIVE = [f["key"] for f in FIELDS if f["default"]]
GROUPS = ["Taraf", "Dosya", "Tarih", "Durum"]

# Dosya durumu -> payload "dosyaDurumKod" (kullanıcının yakaladığı isteklerden):
#   Açık = 0, Kapalı = 1.
DURUMLAR = [("Açık", 0), ("Kapalı", 1)]
DURUM_VARSAYILAN = 0


def gecerli_alanlar(active):
    """Verilen anahtar listesinden yalnızca tanınanları, FIELDS sırasıyla döndürür."""
    var = set(active or [])
    return [f["key"] for f in FIELDS if f["key"] in var]


def build_payload(values, durum_kod=DURUM_VARSAYILAN, taraf_variant=None):
    """values: {field_key: ham_str}. Yalnızca DOLU alanlar payload'a girer.
    durum_kod: Dosya Durumu seçimi -> dosyaDurumKod (Açık/Kapalı/Tümü).
    taraf_variant: build_taraf_variants'ten gelen taraf anahtarları (kişi VEYA
    kurum); None ise taraf filtresi uygulanmaz."""
    payload = {
        "dosyaDurumKod": durum_kod,
        "pageSize": PAGE_SIZE,
        "pageNumber": 1,
        "birimTuru2": BIRIM_TURU2,
        "birimTuru3": BIRIM_TURU3,
    }

    # 1. Dosya Yılı
    yil_val = (values.get("dosyaYil") or "").strip()
    if yil_val:
        try:
            payload["dosyaYil"] = int(yil_val)
        except ValueError:
            pass

    # 2. Dosya No
    no_val = (values.get("dosyaNo") or "").strip()
    if no_val:
        if "/" in no_val:
            try:
                _, sira = no_val.split("/", 1)
                payload["dosyaSira"] = int(sira)
            except ValueError:
                pass
        else:
            try:
                payload["dosyaSira"] = int(no_val)
            except ValueError:
                pass

    # 3. Birim — sayısal birimId doğrudan; isim ise birim listesinden birimId'ye çevir
    #    (sunucu-taraflı filtre). Bulunamazsa birimId konmaz → eski davranış (tüm
    #    ofisler + ekranda istemci-taraflı süzme) korunur.
    birim_val = (values.get("birimAdi") or "").strip()
    if birim_val:
        if birim_val.isdigit():
            payload["birimId"] = birim_val
        else:
            bid = birim_id_bul(birim_val)
            if bid:
                payload["birimId"] = bid

    # 4. Dates
    start_val = (values.get("dosyaAcilisTarihiStart") or "").strip()
    if start_val:
        payload["dosyaAcilisTarihiStart"] = start_val
    end_val = (values.get("dosyaAcilisTarihiEnd") or "").strip()
    if end_val:
        payload["dosyaAcilisTarihiEnd"] = end_val

    # 5. Taraf search (Alacaklı or Borçlu) — varyant verildiyse onun taraf
    #    anahtarlarını ekle. Varyant 'ara' tarafından build_taraf_variants ile
    #    üretilir; böylece tek tahmin yerine kişi+kurum birden denenebilir.
    if taraf_variant:
        payload.update(taraf_variant)

    return payload


def taraf_degeri(values):
    """Alacaklı (öncelikli) ya da borçlu kutusuna yazılan taraf metnini döndürür."""
    return ((values.get("alacakli") or "").strip()
            or (values.get("borclu") or "").strip())


def taraf_variantlari(values):
    """Sorgu için taraf arama varyantlarını üretir.

    Öncelik sırası:
      1) AÇIK 'Taraf ile Ara' alanları (gerçek kişi / kurum) — kullanıcı türü
         kendi seçtiği için TAHMİN YOK, tek kesin varyant döner.
      2) Açık alan boşsa tablo kolonundaki serbest metin (alacaklı/borçlu);
         kişi mi kurum mu belli olmadığından build_taraf_variants ile birden
         çok varyanta açılır.

    Döner: list[dict] — payload'a eklenecek taraf anahtarları. Hiç taraf
           girilmediyse []."""
    tur = (values.get("taraf_tur") or "").strip()
    ad = (values.get("taraf_ad") or "").strip()
    soyad = (values.get("taraf_soyad") or "").strip()
    tckn = (values.get("taraf_tckn") or "").strip()
    kurum = (values.get("taraf_kurum") or "").strip()
    vergi = (values.get("taraf_vergi") or "").strip()
    mersis = (values.get("taraf_mersis") or "").strip()

    if tur == "gercek" and (ad or soyad or tckn):
        v = {"tarafTuru": 0}
        if tckn:
            v["tcKimlikNo"] = tckn
        if ad:
            v["adi"] = ad
        if soyad:
            v["soyadi"] = soyad
        return [v]

    if tur == "kurum" and (kurum or vergi or mersis):
        v = {"tarafTuru": 1}
        if kurum:
            v["kurumAdi"] = kurum
        if vergi:
            v["vergiNo"] = vergi
        if mersis:
            v["mersisNo"] = mersis
        return [v]

    # Açık alan yok → serbest metni tahminle çöz.
    return build_taraf_variants(taraf_degeri(values))


def build_taraf_variants(taraf_val):
    """Bir taraf metnini UYAP'ın kabul ettiği AYRI sorgu varyantlarına çevirir.

    UYAP 'search_phrase_detayli.ajx' taraf aramasında ya kişi (tarafTuru=0 +
    adi/soyadi) YA da kurum (tarafTuru=1 + kurumAdi) seçtirir; ikisini tek
    istekte aratamazsın. Tek bir tahmin yapmak (kurum mu kişi mi?) yanlış
    olduğunda sonucu sıfırlar ('duvara çarpma'). Bu yüzden metni hem kişi hem
    kurum olarak deneyecek BİRDEN ÇOK varyant üretiriz; 'ara' her birini çalıştırıp
    sonuçları dosyaId ile tekilleştirir.

    Döner: list[dict]  — her dict, payload'a eklenecek taraf anahtarlarıdır.
            Taraf metni boşsa [] (taraf filtresi yok)."""
    if not taraf_val:
        return []

    # Sayısal kimlik: tek varyant, kesin alan.
    if taraf_val.isdigit():
        if len(taraf_val) == 11:
            return [{"tcKimlikNo": taraf_val, "tarafTuru": 0}]
        if len(taraf_val) == 10:
            return [{"vergiNo": taraf_val, "tarafTuru": 1}]
        if len(taraf_val) == 16:
            return [{"mersisNo": taraf_val, "tarafTuru": 1}]
        # Tanınmayan uzunlukta sayı: kurum adı gibi de aranabilir.
        return [{"kurumAdi": taraf_val, "tarafTuru": 1}]

    variants = []

    # Kurum varyantı: tüm metni tek alana koyar → ad/soyad ayrımı derdi yok.
    variants.append({"kurumAdi": taraf_val, "tarafTuru": 1})

    # Kişi varyantı: son boşluktan ad/soyad olarak böl.
    parts = taraf_val.rsplit(None, 1)
    if len(parts) == 2:
        variants.append({"adi": parts[0], "soyadi": parts[1], "tarafTuru": 0})
    else:
        variants.append({"adi": taraf_val, "tarafTuru": 0})

    return variants


def parse_records(veri):
    """search_phrase_detayli.ajx yanıtını kayıt (dict) listesine indirger.
    SGK motorunda yanıt veri[0] = kayıt listesidir; yine de savunmacı davranır."""
    if veri is None:
        return []
    if isinstance(veri, dict):
        for k in ("data", "veri", "list", "rows", "dosyalar"):
            if isinstance(veri.get(k), list):
                veri = veri[k]
                break
        else:
            return [veri]
    if isinstance(veri, list):
        if veri and isinstance(veri[0], list):
            return [r for r in veri[0] if isinstance(r, dict)]
        return [r for r in veri if isinstance(r, dict)]
    return []


def kolonlar_belirle(records):
    """Kayıtların düz (skaler) anahtarlarından sıralı kolon listesi üretir."""
    return ["alacakli", "borclu", "birimAdi", "dosyaYil", "dosyaNo", "dosyaTur", "acilisTarihi"]


# Arama alanı (FIELDS key) -> yanıt kaydındaki olası anahtar adları.
YANIT_ALIAS = {
    "dosyaYil":  ["dosyaYil", "dosyaYili", "yil"],
    "dosyaSira": ["dosyaSira", "dosyaNo", "dosyaSiraNo", "sira"],
    "birimId":   ["birimAdi", "birim", "birimId"],
}


import datetime as dt

def kolon_degeri(rec, field_key):
    """Bir yanıt kaydından, verilen arama alanına karşılık gelen metin değeri.
    Bulunamazsa "" döner (kolon o kayıt için boş görünür)."""
    if not isinstance(rec, dict):
        return ""
    
    if field_key == "alacakli":
        return str(rec.get("alacakli") or "")
    elif field_key == "borclu":
        return str(rec.get("borclu") or "")
    elif field_key == "birimAdi":
        return str(rec.get("birimAdi") or rec.get("birim_adi") or "")
    elif field_key == "dosyaYil":
        # UYAP yanıtında ayrı 'dosyaYil' yok; yıl dosyaNo="YYYY/NNN" içinde gelir.
        # Yıl yıl filtreleyebilmek için dosyaNo'dan ayır (yoksa eski alanlara düş).
        dno = str(rec.get("dosyaNo") or "")
        if "/" in dno:
            return dno.split("/", 1)[0].strip()
        return str(rec.get("dosyaYil") or rec.get("yil") or "")
    elif field_key == "dosyaNo":
        # Yıl ayrı kolonda; burada yalnız SIRA no göster ("2013/294" → "294").
        dno = str(rec.get("dosyaNo") or "")
        if "/" in dno:
            return dno.split("/", 1)[1].strip()
        return dno
    elif field_key == "dosyaTur":
        # Esas (İcra Dosyası) / Talimat Dosyası ayrımı — UYAP yanıtında 'dosyaTur'.
        return str(rec.get("dosyaTur") or rec.get("tur") or "")
    elif field_key == "acilisTarihi":
        val = rec.get("acilisTarihi") or rec.get("dosyaAcilisTarihi") or rec.get("acilis_tarihi")
        if not val:
            return ""
        if isinstance(val, dict):
            tarih = val.get("date") or {}
            try:
                day = int(tarih["day"])
                month = int(tarih["month"])
                year = int(tarih["year"])
                return f"{day:02d}.{month:02d}.{year:04d}"
            except (KeyError, TypeError, ValueError):
                return ""
        if isinstance(val, (dt.datetime, dt.date)):
            return val.strftime("%d.%m.%Y")
        return str(val)
        
    for k in YANIT_ALIAS.get(field_key, [field_key]):
        if k in rec and not isinstance(rec[k], (dict, list)):
            v = rec[k]
            return "" if v is None else str(v)
    return ""


def parse_taraf_from_uyap(b):
    kb = b.get("kisiTumDVO") or b.get("kurumTumDVO") or b.get("tarafTumDVO") or b
    if not isinstance(kb, dict):
        kb = {}
    
    tckn = str(kb.get("tcKimlikNo") or kb.get("tckn") or kb.get("tc") or "").strip()
    vergi_no = str(kb.get("vergiNo") or kb.get("vergiNumarasi") or "").strip()
    mersis_no = str(kb.get("mersisNo") or kb.get("mersis") or "").strip()
    
    tckn = "".join(c for c in tckn if c.isdigit())
    vergi_no = "".join(c for c in vergi_no if c.isdigit())
    mersis_no = "".join(c for c in mersis_no if c.isdigit())
    
    adi = str(kb.get("adi") or kb.get("ad") or "").strip()
    soyadi = str(kb.get("soyadi") or kb.get("soyad") or "").strip()
    unvan = str(kb.get("unvan") or kb.get("kurumAdi") or kb.get("unvani") or "").strip()
    
    full_name = f"{adi} {soyadi}".strip()
    corp_keywords = ["ltd", "sti", "şti", "anonim", "a.s", "a.ş", "sirket", "şirket",
                     "kooperatif", "mudurlu", "müdürlü", "banka", "aş.", "aş", "san.", "tic."]
    is_corp = (bool(vergi_no) or bool(mersis_no) or 
               any(k in full_name.lower() for k in corp_keywords) or
               any(k in unvan.lower() for k in corp_keywords))
               
    if is_corp:
        tur = "tuzel"
        final_unvan = unvan or full_name
        return {
            "tur": tur,
            "unvan": final_unvan,
            "vergi_no": vergi_no,
            "mersis_no": mersis_no or None,
            "tckn": None,
            "ad": "",
            "soyad": "",
        }
    else:
        tur = "gercek"
        if not adi and unvan:
            parts = unvan.rsplit(None, 1)
            if len(parts) == 2:
                adi, soyadi = parts[0], parts[1]
            else:
                adi = unvan
        return {
            "tur": tur,
            "ad": adi,
            "soyad": soyadi,
            "tckn": tckn or None,
            "unvan": "",
            "vergi_no": "",
            "mersis_no": None,
        }


def save_taraf(info, Taraf):
    tckn = info.get("tckn")
    mersis_no = info.get("mersis_no")
    vergi_no = info.get("vergi_no")
    unvan = info.get("unvan")
    ad = info.get("ad")
    soyad = info.get("soyad")
    tur = info.get("tur")
    
    defaults = {
        "tur": tur,
        "ad": ad or "",
        "soyad": soyad or "",
        "unvan": unvan or "",
        "vergi_no": vergi_no or "",
        "mersis_no": mersis_no,
    }
    
    taraf = None
    if tckn:
        taraf = Taraf.objects.filter(tckn=tckn).first()
    elif mersis_no:
        taraf = Taraf.objects.filter(mersis_no=mersis_no).first()
    
    if taraf:
        for k, v in defaults.items():
            setattr(taraf, k, v)
        taraf.save()
    else:
        if tur == "tuzel" and unvan:
            taraf = Taraf.objects.filter(tur="tuzel", unvan=unvan).first()
        elif tur == "gercek" and ad and soyad:
            taraf = Taraf.objects.filter(tur="gercek", ad=ad, soyad=soyad).first()
            
        if taraf:
            for k, v in defaults.items():
                setattr(taraf, k, v)
            taraf.save()
        else:
            taraf = Taraf.objects.create(**defaults)
            
    return taraf


class IcraSorgu:
    """İcra > Dosyalarım sorgusu (headless). Aktarım orijinal SorguMotoru._post."""

    def __init__(self, log_fn=None):
        self.log_fn = log_fn or (lambda m: None)

    def ara(self, values, durum_kod=DURUM_VARSAYILAN, kontrol=None):
        """Döner: (kayitlar:list[dict], payload:dict, yeni_dosyalar:list[dict]). Hata olursa istisna yükselir
        (OturumHatasi veya genel).

        UYAP sayfa başına en çok PAGE_SIZE (500) kayıt döndürür. Dosya sayısı
        500'ü aşarsa kalan sayfalar pageNumber artırılarak çekilir ve hepsi tek
        listede birleştirilir; böylece sonuç 500'de kapanmaz.

        `kontrol` verilirse (toplu_is_kontrol.TopluIsKontrolu), her sayfa ve her
        dosya-detayı çekiminden önce duraklat/durdur/karma kontrol noktasından
        geçilir; durdurulduysa o ana kadar toplanan sonuçla erken döner."""
        motor = SorguMotoru(self.log_fn)
        # Birim adını sunucu-taraflı filtreye çevirebilmek için birim listesini
        # hazırla (önbellekli; yalnız ilk sorguda UYAP'a gider).
        try:
            birim_listesi_getir(self.log_fn)
        except Exception:
            pass
        # Kullanıcı BİR BİRİM ADI girdiyse ama birimId'ye çözemiyorsak: TÜM ofisleri
        # taramak (binlerce dosya!) YERİNE net hata ver. Birim kutusu boşsa serbest
        # bırakılır (tüm ofisler bilinçli aranır).
        birim_secimi = (values.get("birimAdi") or "").strip()
        if birim_secimi and not birim_secimi.isdigit():
            if not birim_adlari():                 # liste yüklenememiş (ofis kapalı?)
                birim_listesi_getir(self.log_fn, zorla=True)
            if not birim_adlari():
                raise RuntimeError(
                    "Birim listesi yüklenemedi (yerel ofis 127.0.0.1:8800 kapalı "
                    "olabilir). Bağlantıyı kontrol edip tekrar Sorgula'ya basın.")
            if not birim_id_bul(birim_secimi):
                raise RuntimeError(
                    f"'{birim_secimi}' birimi listede bulunamadı veya birden fazla "
                    "birime uyuyor. 'Birim Adı' kutusundan tam adı seçin.")
        tum, son_payload = [], None
        gorulen = set()   # dosyaId — varyantlar arası tekilleştirme
        # Tarama UYAP'ın açık listesini SONUNA kadar aldı mı? Yalnız True iken
        # "listede olmayan = kapanmış olabilir" uzlaştırması yapılır (bkz.
        # `_kapananlari_uzlastir`) — yarım bir listeyle çalışırsa açık dosyaları
        # yanlışlıkla aday sayardı.
        tarama_tam = True
        # UYAP'ın ilk sayfada bildirdiği toplam (yalnız taraf varyantı YOKKEN
        # anlamlı — varyantlı aramada toplamlar örtüşür). Sayım raporu için.
        uyap_toplam = None
        self.son_sayim = None

        # Taraf metni kişi mi kurum mu belli değil; her varyantı ayrı sorgula,
        # sonuçları dosyaId ile birleştir. Taraf yoksa tek (None) sorgu çalışır.
        variantlar = taraf_variantlari(values) or [None]
        if len(variantlar) > 1:
            self.log_fn(f"… taraf adı için {len(variantlar)} varyant (kişi+kurum) deneniyor")

        for variant in variantlar:
            sayfa = 1
            while True:
                if kontrol and not kontrol.nokta():
                    self.log_fn("⏹ Durduruldu.")
                    return tum, son_payload, []
                payload = build_payload(values, durum_kod, variant)
                payload["pageNumber"] = sayfa
                son_payload = payload
                veri = _post_sayfa_korumali(motor, ENDPOINT, payload, self.log_fn)
                if sayfa_yaniti_gecersiz_mi(veri):
                    # None = 'eşzamanlı sorgulama' reddi denemelerde aşılamadı;
                    # dict+errorCode = UYAP'ın genel hata zarfı. Eskiden ikisi
                    # de SESSİZCE "son sayfa" sanılıp tarama BAŞARILI görünerek
                    # dururdu (bkz. modül düzeyi not) — artık kullanıcıya AÇIKÇA
                    # bildirilir ve tarama burada durur.
                    neden = ("'eşzamanlı sorgulama' reddi" if veri is None
                             else f"UYAP hatası ({veri.get('error') or veri.get('errorCode')})")
                    self.log_fn(f"⚠️ Sayfa {sayfa} {neden} yüzünden alınamadı — "
                                "tarama burada durduruldu, bazı dosyalar eksik "
                                "kalmış olabilir. Birkaç saniye sonra Sorgula'yı "
                                "tekrar çalıştırın.")
                    tarama_tam = False
                    break
                if sayfa == 1 and variant is None:
                    uyap_toplam = uyap_toplam_sayisi(veri)
                kayitlar = parse_records(veri)
                for rec in kayitlar:
                    did = rec.get("dosyaId")
                    if did and did in gorulen:
                        continue
                    if did:
                        gorulen.add(did)
                    tum.append(rec)
                if kontrol:
                    kontrol.tur_bitti()
                if len(kayitlar) < PAGE_SIZE:      # son (eksik) sayfa -> bitti
                    break
                sayfa += 1
                if sayfa > 200:                    # güvenlik freni (~100.000 dosya)
                    self.log_fn("⚠️ Sayfa sınırına (200) ulaşıldı; bazı kayıtlar alınmamış olabilir.")
                    tarama_tam = False
                    break
                self.log_fn(f"… {len(tum)} dosya alındı, sayfa {sayfa} çekiliyor")

        # Load/Save Creditor and Debtor info from Database/UYAP
        import sys
        import os
        _HERE = os.path.dirname(os.path.abspath(__file__))
        _MODELS_DIR = os.path.normpath(os.path.join(_HERE, "..", "..", "models"))
        if _MODELS_DIR not in sys.path:
            sys.path.insert(0, _MODELS_DIR)
        
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "uyapdata.settings")
        import django
        
        db_available = False
        try:
            django.setup()
            from icra_models.models import Dosya, Taraf, DosyaTaraf
            from icra_models.ingest import dosya_kunyesi_kaydet
            # Test connection with a quick query
            Dosya.objects.filter(pk=1).exists()
            db_available = True
        except Exception as e:
            self.log_fn(f"⚠️ Yerel veritabanı aktif değil (PostgreSQL kapalı veya erişilemez durumda): {e}")
            self.log_fn("   Sorgular canlı UYAP üzerinden devam edecek (veritabanı eşitlemesi atlanıyor).")

        yeni_dosyalar = []
        gorulen_pk = set()   # bu taramada UYAP'ın açık listesinde görülen Dosya.pk'ları
        durduruldu = False

        for rec in tum:
            if kontrol:
                kontrol.tur_bitti()
                if not kontrol.nokta():
                    self.log_fn("⏹ Durduruldu.")
                    durduruldu = True
                    break
            dosya_id = rec.get("dosyaId")
            if not dosya_id:
                continue

            # Kapak künyesi HER kayıt için upsert edilir (güncelleme hattı K1,
            # 2026-09-23). Eskiden yalnız DB'de alacaklısı olmayan dosyalar
            # yazılıyordu; zaten kayıtlı bir dosyanın durum/tür değişikliği
            # DB'ye hiç yansımıyordu. Dosya ayrıca OTURUMLUK `dosyaId` ile
            # aranıyordu — UYAP oturumu değişince tüm dosyalar "DB'de yok"
            # sayılıp borçlu bilgisi baştan çekiliyordu. Artık KALICI doğal
            # anahtarla (birim, yıl, sıra, tür — bkz. ingest.dosya_kunyesi_kaydet)
            # bulunur.
            dosya_obj = None
            if db_available:
                try:
                    dosya_obj, created = dosya_kunyesi_kaydet(rec, yargi_turu=int(BIRIM_TURU3))
                    gorulen_pk.add(dosya_obj.pk)
                    if created:   # "yeni dosya" bildirimi yalnız gerçekten yeni kayıtlar için
                        yeni_dosyalar.append({
                            "dosya_no": rec.get("dosyaNo") or f"{dosya_obj.yil}/{dosya_obj.sira_no}",
                            "birim_adi": rec.get("birimAdi") or (dosya_obj.birim.ad if dosya_obj.birim else "")
                        })
                except Exception as db_save_err:
                    self.log_fn(f"⚠️ Künye kaydedilemedi ({rec.get('dosyaNo', '')}): {db_save_err}")

            # Check if we should use DB or fetch live
            used_db = False
            if dosya_obj is not None:
                try:
                    # Her icra dosyasında alacaklı vardır; alacaklı bağı yoksa kayıt
                    # EKSİKTİR (ör. yalnız borçlu çekilmiş) → canlı yeniden çek.
                    if dosya_obj.taraf_baglari.filter(rol="alacakli").exists():
                        # Retrieve from database
                        creditors = [str(bag.taraf) for bag in dosya_obj.taraf_baglari.filter(rol='alacakli').select_related('taraf')]
                        debtors = [str(bag.taraf) for bag in dosya_obj.taraf_baglari.filter(rol='borclu').select_related('taraf')]
                        rec['alacakli'] = ", ".join(creditors)
                        rec['borclu'] = ", ".join(debtors)
                        used_db = True
                except Exception as db_err:
                    self.log_fn(f"⚠️ Veritabanı sorgu hatası: {db_err}")

            if not used_db:
                # Detayı canlı UYAP'tan çek. TEK bir dosyadaki hata (ör. UYAP 500)
                # TÜM sorguyu çökertmesin: o dosyayı atla, devam et (kullanıcı tercihi:
                # "çek ama hata öldürmesin"). Oturum hatası gerçek auth sorunudur,
                # devam etmek anlamsız + oturumu döver → yukarı bildirilir.
                try:
                    self.log_fn(f"Detay çekiliyor: {rec.get('dosyaNo', '')}...")

                    # NOT: 'dosya_alacakli_list.ajx' diye bir UYAP endpoint'i YOK; eski
                    # kod onu uydurduğu için her dosyada HTTP 500 + tüm sorgu çökmesi
                    # oluyordu. Borçlu için KANITLI 'dosya_borclu_list.ajx' kullanılır
                    # (TCKN/vergi no da verir). Alacaklı AŞAĞIDA 'Tüm Dosyalarım'ın
                    # kullandığı taraf uç noktasından alınır.
                    _d_status, debt_veri = motor._post("dosya_borclu_list.ajx", {"dosyaId": dosya_id})

                    borclu_names = []
                    if isinstance(debt_veri, list):
                        for item in debt_veri:
                            info = parse_taraf_from_uyap(item)
                            name = info["unvan"] if info["tur"] == "tuzel" else f"{info['ad']} {info['soyad']}".strip()
                            if name:
                                borclu_names.append(name)
                    rec['borclu'] = ", ".join(borclu_names)

                    # DB'ye borçlu bağlarını kaydet (künye yukarıda zaten yazıldı).
                    if dosya_obj is not None:
                        try:
                            with django.db.transaction.atomic():
                                for idx, item in enumerate(debt_veri if isinstance(debt_veri, list) else []):
                                    info = parse_taraf_from_uyap(item)
                                    taraf_obj = save_taraf(info, Taraf)
                                    DosyaTaraf.objects.update_or_create(
                                        dosya=dosya_obj, taraf=taraf_obj, rol="borclu",
                                        defaults={"sira": idx})
                        except Exception as db_save_err:
                            self.log_fn(f"⚠️ Veritabanına kaydetme hatası: {db_save_err}")
                        alacaklilar = self._alacaklilari_kaydet(dosya_obj, dosya_id)
                        if alacaklilar:
                            rec['alacakli'] = ", ".join(alacaklilar)

                except OturumHatasi:
                    raise
                except Exception as detay_err:
                    self.log_fn(f"⚠️ {rec.get('dosyaNo','')} detayı atlandı: {detay_err}")
                    continue

        if db_available:
            # Kapanan dosyalar: yalnız UYAP'ın açık listesi EKSİKSİZ alındıysa
            # ve sorgu tüm portföyü (ya da tek bir birimi) kapsıyorsa.
            if tarama_tam and not durduruldu and durum_kod == DURUM_VARSAYILAN:
                birim_id, uygun = _uzlastirma_kapsami(values)
                if uygun:
                    try:
                        self._kapananlari_uzlastir(motor, gorulen_pk, birim_id, kontrol, Dosya)
                    except OturumHatasi:
                        raise
                    except Exception as e:
                        self.log_fn(f"⚠️ Kapanan dosya kontrolü yapılamadı: {e}")
        self._sayim_raporu(tum, uyap_toplam, tarama_tam, durduruldu, gorulen_pk,
                           values, durum_kod, Dosya if db_available else None)
        if db_available:
            # Güncelleme hattı K4: kesinleşme durumunu yerel veriden yeniden
            # hesapla (UYAP'a gitmez) — 'Tüm Dosyalarım > Yenile' ile AYNI.
            try:
                import dosya_core as _dc
                _dc.kesinlesme_durumlarini_guncelle(log_fn=self.log_fn)
            except Exception as e:
                self.log_fn(f"⚠️ Kesinleşme durumu güncellenemedi: {e}")

        return tum, son_payload, yeni_dosyalar

    def _sayim_raporu(self, tum, uyap_toplam, tarama_tam, durduruldu, gorulen_pk,
                      values, durum_kod, Dosya):
        """Tarama sonunda "UYAP'taki dosya sayısı kadar dosya indirildi mi?"
        sorusunu üç sayıyla yanıtlar (kullanıcı isteği, 2026-09-23):
          1) UYAP'ın bildirdiği toplam ↔ alınan kayıt sayısı,
          2) alınan ↔ DB'ye yazılan,
          3) (tüm portföy/tek birim taramasında) DB'deki açık İcra dosyası ↔ UYAP.
        Sonuç `self.son_sayim`'e de yazılır."""
        alinan = len(tum)
        metin, eksik = sayim_satiri("İcra (UYAP listesi)", alinan, uyap_toplam, tarama_tam)
        self.log_fn("── Sayım raporu ──")
        self.log_fn(metin)
        sayim = {"uyap_toplam": uyap_toplam, "alinan": alinan, "tam": tarama_tam,
                 "db_yazilan": None, "db_acik": None, "eksik": eksik}
        if Dosya is not None:
            sayim["db_yazilan"] = len(gorulen_pk)
            if durduruldu:
                self.log_fn(f"⚠️ Kayıt aşaması durduruldu: {len(gorulen_pk)}/{alinan} dosya DB'ye yazıldı.")
                sayim["eksik"] = True
            elif len(gorulen_pk) < alinan:
                self.log_fn(f"⚠️ {alinan - len(gorulen_pk)} dosya DB'ye yazılamadı (yukarıdaki hatalara bakın).")
                sayim["eksik"] = True
            else:
                self.log_fn(f"✔ Alınan {alinan} dosyanın tamamı veritabanına yazıldı.")
            birim_id, uygun = _uzlastirma_kapsami(values)
            if uygun and durum_kod == DURUM_VARSAYILAN and tarama_tam and not durduruldu:
                try:
                    qs = Dosya.objects.filter(durum_kod=DURUM_VARSAYILAN).filter(_icra_dosyasi_q())
                    if birim_id:
                        qs = qs.filter(birim__birim_id=str(birim_id))
                    db_acik = qs.count()
                    sayim["db_acik"] = db_acik
                    if db_acik == alinan:
                        self.log_fn(f"✔ Veritabanındaki açık İcra dosyası sayısı ({db_acik}) UYAP ile birebir aynı.")
                    else:
                        self.log_fn(f"⚠️ Veritabanında {db_acik} açık İcra dosyası var, UYAP'ta {alinan} — "
                                    f"{abs(db_acik - alinan)} fark (UYAP'ta hiçbir listede bulunamayan "
                                    "ya da yazılamayan dosyalar).")
                        sayim["eksik"] = True
                except Exception as e:
                    self.log_fn(f"⚠️ Veritabanı sayımı yapılamadı: {e}")
        self.son_sayim = sayim

    def _alacaklilari_kaydet(self, dosya_obj, dosya_id):
        """Alacaklı (ve borçlu DIŞINDAKİ diğer roller) bağlarını 'Tüm
        Dosyalarım'ın kullandığı taraf uç noktasından (`dosya_core.
        dosya_taraf_getir`) çekip `dosya_taraf_kaydet` ile yazar. Borçlu
        satırları BİLEREK atlanır: borçlular yukarıda TCKN'li
        `dosya_borclu_list.ajx`'ten zaten yazıldı; bu uç nokta TCKN vermediği
        için aynı kişi ikinci (kimliksiz) bir Taraf olarak eklenip dosya "çok
        borçlulu" görünebilirdi (bu da barkod_sorgu._borclu_coz_tekil'i bozar).
        Eskiden İcra Sorgula alacaklıyı HİÇ yazmıyordu — alacaklısı eksik
        dosyalar her Sorgula'da yeniden canlı çekiliyordu, 'Tüm Dosyalarım'
        da (taraf VAR sayıp) onları hiç tamamlamıyordu. Döner: alacaklı adları."""
        try:
            import dosya_core as _dc
            ham = _dc.dosya_taraf_getir(dosya_id, self.log_fn, timeout=20, denemeler=1)
            borclu_disi = [t for t in (ham or []) if isinstance(t, dict)
                           and tr_lower(t.get("rol", "")) != "borclu"]
            if borclu_disi:
                _dc.dosya_taraf_kaydet(dosya_obj, borclu_disi, self.log_fn)
            return [str(t.get("adi", "") or "").strip() for t in borclu_disi
                    if tr_lower(t.get("rol", "")) == "alacakli" and str(t.get("adi", "") or "").strip()]
        except OturumHatasi:
            raise
        except Exception as e:
            self.log_fn(f"⚠️ Alacaklı bilgisi alınamadı: {e}")
            return []

    def _kapananlari_uzlastir(self, motor, gorulen_pk, birim_id, kontrol, Dosya):
        """UYAP'ın açık listesinde ARTIK OLMAYAN ama DB'de hâlâ 'Açık' duran
        İcra dosyalarını bulur ve her birini UYAP'ta Kapalı listede TEK TEK
        doğrular (kullanıcı isteği, 2026-09-23: "kapanan dosya listeden düşüyor
        ama DB'de açık kalıyor"). Yalnız Kapalı listede GERÇEKTEN bulunan dosya
        güncellenir (künye upsert → durum_kod/durum UYAP'tan gelir); hiçbir
        listede bulunamayan dosyaya (ör. başka birime gönderilmiş) DOKUNULMAZ,
        yalnız sayısı loglanır — tahminle 'kapalı' işaretlenmez."""
        from icra_models.ingest import dosya_kunyesi_kaydet
        qs = (Dosya.objects.select_related("birim")
              .filter(durum_kod=DURUM_VARSAYILAN).filter(_icra_dosyasi_q())
              .exclude(pk__in=gorulen_pk))
        if birim_id:
            qs = qs.filter(birim__birim_id=str(birim_id))
        adaylar = list(qs)
        if not adaylar:
            return
        # Emniyet freni: adaylar görülen açık dosyalardan bile fazlaysa listenin
        # kendisi şüphelidir (ör. UYAP boş/eksik yanıt verdi) — hiçbir şey yapma.
        if len(adaylar) > max(50, len(gorulen_pk)):
            self.log_fn(f"⚠️ {len(adaylar)} açık dosya UYAP listesinde görünmedi — sayı "
                        "beklenmeyecek kadar yüksek, kapanan dosya kontrolü bu tur ATLANDI.")
            return
        self.log_fn(f"… {len(adaylar)} dosya UYAP'ın açık listesinde yok — kapanıp "
                    "kapanmadığı tek tek doğrulanıyor")
        kapandi = bulunamadi = 0
        for d in adaylar:
            if kontrol:
                kontrol.tur_bitti()
                if not kontrol.nokta():
                    self.log_fn("⏹ Durduruldu.")
                    break
            payload = {
                "dosyaDurumKod": 1, "pageSize": PAGE_SIZE, "pageNumber": 1,
                "dosyaYil": d.yil, "dosyaSira": d.sira_no, "birimId": d.birim.birim_id,
                "birimTuru2": BIRIM_TURU2, "birimTuru3": BIRIM_TURU3,
            }
            veri = _post_sayfa_korumali(motor, ENDPOINT, payload, self.log_fn)
            if veri is None:
                continue
            eslesen = None
            for r in parse_records(veri):
                if not r.get("dosyaId"):
                    continue
                if (str(r.get("dosyaNo", "")).strip() == f"{d.yil}/{d.sira_no}"
                        and int(r.get("dosyaTurKod", 0) or 0) == d.tur_kod):
                    eslesen = r
                    break
            if eslesen is None:
                bulunamadi += 1
                continue
            dosya_kunyesi_kaydet(eslesen, yargi_turu=int(BIRIM_TURU3))
            kapandi += 1
            self.log_fn(f"  ✔ {d.birim.ad} {d.yil}/{d.sira_no} kapalı olarak güncellendi"
                        f" ({eslesen.get('dosyaDurum', '') or 'Kapalı'}).")
        if kapandi or bulunamadi:
            self.log_fn(f"… kapanan dosya kontrolü: {kapandi} dosya kapalı olarak işaretlendi"
                        + (f", {bulunamadi} dosya UYAP'ta açık/kapalı listede bulunamadı "
                           "(dokunulmadı)" if bulunamadi else "") + ".")


def _icra_dosyasi_q():
    """Bir `Dosya` satırının İcra'ya ait olduğunu söyleyen Django Q filtresi —
    `Dosya.objects.icra()` ile AYNI tanım. 2026-09-25: `Dosya.yargi_turu`
    zorunlu; eski "NULL ise birimden / birimTuru2'den tanı" yedekleri kaldırıldı."""
    from django.db.models import Q
    return Q(yargi_turu=int(BIRIM_TURU3))


def acik_icra_dosyalari_secimi():
    """DB'deki AÇIK İcra dosyaları, `barkod_sorgu.calistir`'ın 'seçili dosyalar'
    modunun beklediği biçimde: [{"birimAdi", "dosyaNo"}, ...]. 'Barkod'u da
    Güncelle' düğmesi ve gece tam güncelleme turu bunu kullanır. Eskiden
    `db_dosyalari_getir` kullanılıyordu — o fonksiyon yargı türüne göre
    SÜZMEDİĞİ için Hukuk/Ceza vb. açık dosyalar da barkod kuyruğuna girip
    (İcra aramasıyla hiç bulunamayacakları halde) boşuna UYAP isteği
    harcatıyordu. DB erişilemezse []."""
    try:
        M = _db_modeller()
    except Exception:
        return []
    qs = (M["Dosya"].objects.select_related("birim")
          .filter(durum_kod=DURUM_VARSAYILAN).filter(_icra_dosyasi_q())
          .order_by("-yil", "-sira_no"))
    return [{"birimAdi": d.birim.ad, "dosyaNo": d.dosya_no or f"{d.yil}/{d.sira_no}"}
            for d in qs if d.birim and d.birim.ad]


def _uzlastirma_kapsami(values):
    """Kapanan-dosya uzlaştırması bu sorgu için güvenli mi? Yalnız TÜM
    portföyü ya da tek bir birimi kapsayan sorgularda (taraf/yıl/no/tarih/tür
    filtresi YOKKEN) — aksi halde filtre dışında kalan açık dosyalar yanlışlıkla
    'listede yok' sayılırdı. Döner: (birim_id veya None, uygun_mu)."""
    # UYAP'a filtre olarak GİTMEYEN anahtarlar tarama kapsamını daraltmaz:
    # birimAdi ayrıca ele alınır, taraf_tur yalnız kişi/kurum radyo seçimidir,
    # pkey=None alanlar (Kesinleşme/Tebliğ Durumu) yalnız ekran filtresidir.
    yok_say = {"birimAdi", "taraf_tur"} | {f["key"] for f in FIELDS if f.get("pkey") is None}
    for k, v in (values or {}).items():
        if k in yok_say:
            continue
        if str(v or "").strip():
            return None, False
    birim_val = (values or {}).get("birimAdi", "").strip()
    if not birim_val:
        return None, True
    bid = birim_val if birim_val.isdigit() else birim_id_bul(birim_val)
    if not bid:
        return None, False
    return bid, True
