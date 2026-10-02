# -*- coding: utf-8 -*-
"""
Barkod Sorgu — Kapalı Tebligat barkodunu PTT'de sorgula
=========================================================
Excel'deki (Birim, Dosya No) her satır için:
  1. birim adı        -> birimId          (icra_core.birim_id_bul, önbellekli)
  2. birimId+yıl+sıra  -> dosyaId          (search_phrase_detayli.ajx)
  3. dosyaId           -> evrak listesi    (dosya_core.evrak_listesi_getir, sayfalı)
  4. "tur" alanı == "Kapalı Tebligat" olan evrak(lar) filtrelenir (bu değer
     docs/BELGE_ONBELLEK_PLANI.md §3'te CANLI DOĞRULANMIŞ örnek olarak geçiyor)
     — bir dosyada birden fazla olabilir, HEPSİ işlenir.
  5. her eşleşen evrak PDF olarak indirilir (dosya_core.evrak_html_indir HTML
     BEKLEYİP utf-8 ile decode ettiğinden bu içerik için UYGUN DEĞİL — bkz.
     `_evrak_pdf_indir` — bu modül kendi ham-bayt indiricisini kullanır) ve
     içindeki PTT barkodu çıkarılır (bkz. `barkod_bul`).
  6. barkod PTT'nin gönderi takip servisine sorgulanır (`ptt_sorgula`),
     sonuç ekrana (UyapMasa tablosu) yazdırılır.

CANLI DOĞRULAMA (2026-07-28, gerçek ofis oturumu + Chrome ağ yakalama):

  * `view_document_brd.uyap`'ın "Kapalı Tebligat" evrakı için döndürdüğü içerik
    HTML DEĞİL, `Content-Type: application/pdf` ham bir PDF'tir (hem
    "Elektronik Tebligat" hem "Normal Tebligat" alt türlerinde aynı — 3 gerçek
    dosya üzerinde doğrulandı: 2026/94270 elektronik, 2026/94269 ve
    2026/93940 normal/fiziki). `dosya_core.evrak_html_indir` içeriği
    `.decode("utf-8","replace")` ile okuduğundan bu ikili PDF'i GERİ
    DÖNÜLEMEZ biçimde bozar — bu yüzden burada AYRI bir ham-bayt indirici
    (`_evrak_pdf_indir`) kullanılır (dosya_core.py'ye DOKUNULMADI, olası
    başka çağıranları etkilememek için).
  * PDF'in içindeki tebliğ mazbatasında PTT barkodu, Code-39 barkod fontuyla
    (`IDAutomationHC39M`, PDF kaynağında `/F4`) çizilen düz metin olarak
    gömülü — Code-39 kuralı gereği değer `*...*` arasında geçiyor, örn.
    PDF content stream'inde `/F4 10 Tf … (*4007663523293*)Tj`. Bu yüzden
    barkod, PDF'i görüntülemeye/OCR'a gerek KALMADAN metin regex'iyle
    doğrudan ve KESİN biçimde çıkarılabiliyor (bkz. `barkod_bul`).
  * PTT servisi `gonderitakip.ptt.gov.tr` DEĞİL (o alt alan adı artık
    `ptt.gov.tr` anasayfasına yönleniyor) — anasayfadaki "Gönderi Takip"
    kutusunun gittiği gerçek uç nokta `POST https://api.ptt.gov.tr/api/
    ShipmentTracking`, gövde bir NESNE değil HAM JSON STRING (ör. sadece
    `"4007663523293"` — `{"barkod":...}` göndermek 400 döner). Kimlik/çerez
    GEREKMEZ (düz curl ile de çalışıyor).
  * Yanıt biçimi (gerçek barkodla doğrulandı, ör. 2026/94269 dosyasının
    "Normal Tebligat" evrakı — barkod 4007663523293, PTT durumu "TESLİM
    EDİLDİ"): tek elemanlı liste, örn.
    `[{"errorMessage":"BAŞARILI","errorState":true,
       "hareketDongu":[{"aciklama":"KABUL EDİLDİ","il":...,"tarih":...,"saat":...}, …],
       "kabul":{"barkod_no":...,"gonderici":...,"kabul_tarihi":20260723,...},
       "sondurum":{"son_durum_aciklama":"TESLİM EDİLDİ","teslim_tarihi":"20260727",...}}]`
    Kayıtsız/elektronik-tebligat durumunda (barkodun PTT'de karşılığı yoksa):
    `[{"errorMessage":"KAYIT YOK","errorState":false,"hareketDongu":null,
       "kabul":null,"sondurum":null}]` — yani `errorState` GERÇEK/kayıt VAR mı
    anlamına geliyor (isim yanıltıcı, ama iki canlı örnekle doğrulandı).
  * "Elektronik Tebligat" (UETS/KEP ile teslim, `aciklama` alanında bu ibare
    geçer) evrakının PDF'inde de aynı barkod deseni var, ama bu bir UYAP
    belge takip numarasıdır — fiziki postaya hiç çıkmadığından PTT'de HİÇBİR
    ZAMAN kaydı olmaz (canlı doğrulandı: 2026/94270 örneği "KAYIT YOK"
    döndürdü). Bu yüzden bu modül elektronik tebligatlar için PTT'ye hiç
    sorgu ATMAZ, doğrudan "Elektronik Tebligat (PTT'de takip yok)" yazar —
    hem gereksiz dış istek hem de yanıltıcı "KAYIT YOK" satırından kaçınmak
    için.

KULLANICI BULGUSU (2026-08-03, üçüncü tur) — üç kural eklendi:
  1. [ARTIK GEÇERSİZ — bkz. altıncı tur notu aşağıda: bu kural CANLI olarak
     YANLIŞ çıktığı için TAMAMEN KALDIRILDI, tarihçe için burada bırakıldı.]
     "Kapalı Tebligat" türündeki evrak SADECE ödeme emri DEĞİL, borçlunun
     bankasına/işverenine giden haciz ihbarnamesi gibi ÜÇÜNCÜ ŞAHISLARA
     giden tebligatları da kapsıyor — bunları ayrım yapmadan işlemek
     veritabanına "kirli veri" yazıyordu. Artık PDF'in gövde metninde o
     dosyanın borçlusunun adı GEÇMİYORSA sonuç DB'ye YAZILMAZ (bkz.
     `_borclu_adi_govdede_var_mi`). UYARI: bu ayrım kullanıcının önerdiği bir
     SEZGİDİR — CANLI bir haciz ihbarnamesi örneğiyle doğrulanmadı (böyle bir
     ihbarnamenin gövdesinde de borçlunun adı geçiyor olabilir, bu durumda
     ayrım hiçbir şeyi elemez). PDF gövde metni çıkarımı da yalnız barkod
     fontu (Code-39, ASCII rakam) için CANLI doğrulanan yöntemin genel metne
     UYARLANMASIDIR — asıl gövde metni özel/altküme bir yazı tipi
     kodlamasıyla gömülüyse okunaksız çıkabilir; böyle durumda İHTİYATLI
     TARAF seçilir (ad bulunamadı sayılır, DB'ye YAZILMAZ).
  2. Bir evrağın barkodu daha önce DB'ye yazılmışsa (barkod bir kez atanır,
     değişmez) bir sonraki turda PDF TEKRAR İNDİRİLMEZ/barkod TEKRAR
     ÇIKARILMAZ — yalnız PTT durumu tazelenir (bkz. `_bilinen_barkod_kaydini_al`).
  3. Mazbata (Tebliğ Mazbatası / Kapalı Tebliğ Mazbatası) VARLIĞI zaten her
     turda `evrak_listesi_getir` üzerinden kontrol ediliyordu (bkz.
     `tm_evrak`/`ktm_var`) — bu artık barkod ister önbellekten gelsin ister
     yeni sorgulansın HER turda DB'ye yazılıp güncellenir.

KULLANICI BULGUSU (2026-08-03, beşinci tur) — tebligat türü sınıflandırması:
  "Kapalı Tebligat" evrakının PDF'indeki tebligat zarf pusulasında "...
  ihtiva eder" biçiminde bir cümle bulunuyor (kullanıcı bildirdi — CANLI bir
  örnekle DOĞRULANMADI); ayrıca elektronik tebligatlarda evrak açıklaması
  "Elektronik tebligat [tebligat yapılacak üçüncü taraf: <ad>]" biçiminde
  geliyor (bu ikinci biçim CANLI doğrulandı — bankaya giden bir örnekte
  görüldü). Bu iki kaynaktan `_tebligat_turunu_belirle` sabit kategorilere
  (Ödeme Emri / Banka (Haciz İhbarnamesi) / Maaş (Haciz İhbarnamesi) / 103
  Davetiyesi / Kıymet Takdiri Raporu / Bilirkişi Raporu) sınıflandırma
  dener; hiçbiri eşleşmezse "ihtiva eder" sonrası yakalanan HAM metni döner
  (kullanıcı testte eksik/yanlış anahtar kelimeyi görüp bildirebilsin diye —
  sessizce boş bırakmak yerine). UYARI: anahtar kelimeler kullanıcının
  verdiği TEK TEK örneklerden çıkarıldı, toplu canlı veriyle doğrulanmadı.

KULLANICI BULGUSU (2026-08-03, altıncı tur — CANLI doğrulandı, gerçek dosya
2026/84410) — üçüncü turdaki "borçlu adı gövdede yoksa DB'ye YAZILMAZ" filtresi
(`_borclu_adi_govdede_var_mi`) TAMAMEN KALDIRILDI:
  * O filtrenin dayandığı varsayım YANLIŞ çıktı: "Kapalı Tebligat" evrakı için
    indirilen PDF, ihbarnamenin/mektubun KENDİSİ DEĞİL — genel bir "Tebliğ
    Mazbatası" zarf/kapak şablonudur ("BU ZARFTA İcra Daireleri Haciz Tebliğ
    Yazısı VARDIR" biçiminde boş alanlı bir form). Bankaya/işverene giden bir
    haciz ihbarnamesinde bu şablonun "Adı ve Soyadı" alanı kurumun kendisine
    ait olduğundan (ya da elektronik/KEP teslimde hiç doldurulmadığından)
    borçlunun adı YAPISAL OLARAK bu metinde hiç geçmiyor — canlı örnekte
    (dosya 2026/84410, DENİZBANK'a giden Kapalı Tebligat, birimEvrakNo
    5761471) doğrulandı: gövde metninde "Adı ve Soyadı:" alanı boş, borçlu adı
    (KURTULUŞ YARDIMCI) hiç geçmiyor. Yani filtre "kirli veri"yi değil,
    3. şahsa giden HER haciz ihbarnamesini (banka + muhtemelen işveren/maaş)
    %100 oranda eliyordu — kullanıcı bunu "tebligat listesine hiç yok" olarak
    fark etti.
  * Ayrıca `_UCUNCU_TARAF_RE`'nin aradığı "üçüncü taraf:" (iki nokta üst üste
    ile) biçimi de YANLIŞ çıktı — gerçek UYAP metni "3. Taraf" (rakamla, İKİ
    NOKTA ÜST ÜSTE YOK) diyor: `"Elektronik Tebligat[Tebligat Yapılacak 3.
    Taraf DENİZBANK ANONİM ŞİRKETİ]"` (bu, beşinci turdaki "CANLI doğrulandı"
    notuna rağmen yanlıştı — bu sefer gerçekten canlı indirilip kontrol
    edildi). Düzeltilen desen artık bunu doğru yakalıyor.
  * Karar (kullanıcı, 2026-08-03): borçlu adı kontrolü olmadan HER "Kapalı
    Tebligat" evrakı (borçlunun kendisine giden VE bankaya/işverene giden
    dahil) DB'ye yazılır; kimin tebligatı olduğu ayrımı artık dışlama için
    değil, yalnız "Tebligat Türü" sütununu (Ödeme Emri / Banka (Haciz
    İhbarnamesi) / Maaş (Haciz İhbarnamesi) / vs.) doldurmak için kullanılır.

Doğrudan çalıştır:  python barkod_sorgu.py ["Dosya Sorgulama.xlsx"]
UyapMasa içinden (Üretilen Modüller): EXCEL_GIRDI sayesinde "Dosya Seç…" düğmesi
çıkar; Çalıştır'a basınca excel_isle(excel_yolu, log_fn=...) çağrılır.
"""

import base64
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

import openpyxl

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import icra_core  # noqa: E402  (birim_id_bul, birim_listesi_getir, SorguMotoru, BIRIM_TURU2/3)
import dosya_core  # noqa: E402  (evrak_listesi_getir, öncelik geçidi, DOC_VIEWER_HAZIRLIK_ENDPOINT)
import ptt_tarayici  # noqa: E402  (PTT sorgusu — Playwright ile, bkz. ptt_sorgula)
import tur_eslestirme  # noqa: E402  (şablon dışı tebligat türlerinin kullanıcı eşleştirmesi)

SorguMotoru = icra_core.SorguMotoru
BIRIM_TURU2 = icra_core.BIRIM_TURU2
BIRIM_TURU3 = icra_core.BIRIM_TURU3


_YEREL_JETON_YOLU = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "UyapIcra", "gw_local_token")


def _yerel_jetonu_oku():
    """Jetonu HER ÇAĞRIDA dosyadan taze okur — ÖNBELLEKLEMEZ. Kullanıcı bulgusu
    (2026-08-14): ofis (uyap_app.py) her açılışta YENİ rastgele bir jeton
    üretip dosyayı ÜZERİNE yazıyor (bkz. uyap_proxy.py configure()); eskiden
    bu fonksiyon jetonu yalnız import anında BİR KEZ okuyup bir closure'a
    kilitliyordu — ofis UyapMasa açıkken yeniden başlatılırsa (ör. çöküp
    kendiliğinden ya da elle yeniden başlatılırsa) UyapMasa'nın elindeki jeton
    ANINDA geçersiz kalıyor, "ofis yetki hatası (HTTP 403)" ile sonuçlanan
    tarama hatalarına yol açıyordu — UyapMasa'yı yeniden başlatmadan düzelmiyordu."""
    try:
        with open(_YEREL_JETON_YOLU, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


class _JetonEnjektor(urllib.request.BaseHandler):
    def http_request(self, req):
        try:
            if req.host and req.host.split(":")[0] in ("127.0.0.1", "localhost") \
                    and not req.has_header("X-uyap-local-token"):
                token = _yerel_jetonu_oku()
                if token:
                    req.add_unredirected_header("X-Uyap-Local-Token", token)
        except Exception:
            pass
        return req

    https_request = http_request


def _yerel_yetki_jetonunu_kur():
    """Bu betik UyapMasa'nın İÇİNDE (uyap_app ile aynı süreçte) çalışıyorsa ofis
    zaten süreç-global bir urllib opener kurup jetonu otomatik ekliyordur —
    bu durumda burada yapılan da onunla aynı jetonu taşıyacağından ZARARSIZDIR.
    Bağımsız `python ...py` süreci olarak çalıştırılırsa (bu dosyanın normal
    kullanım şekli) ofisin kullanıcıya özel jeton dosyasını okuyup aynı
    başlığı (X-Uyap-Local-Token) ekleyen bir opener kurar (bkz.
    baro_pulu_makbuzu_indiren.py — aynı desen). Jeton HER İSTEKTE taze
    okunur (bkz. _yerel_jetonu_oku) — ofis yeniden başlayıp jetonu
    değiştirse bile bu opener otomatik uyum sağlar."""
    urllib.request.install_opener(urllib.request.build_opener(_JetonEnjektor()))


_yerel_yetki_jetonunu_kur()

# UyapMasa runner'ı (modules/uretilmis_runner.py) bunu görünce "Dosya Seç…"
# düğmesini kurar; Çalıştır'a basınca excel_isle(excel_yolu, log_fn=...) çağrılır.
EXCEL_GIRDI = {
    "etiket": "Dosya Sorgulama Excel'i (A: birim, B: dosya no)",
    "fonksiyon": "excel_isle",
    "uzanti": [("Excel dosyası", "*.xlsx"), ("Tüm dosyalar", "*.*")],
}

# Excel'e ALTERNATİF girdi: dosya seçilmezse (ne masaüstünde ne webde) UyapMasa bu
# alanları formda gösterir ve calistir(girdi_sozlugu, log_fn) çağrılır — bkz.
# UYAP'ın "Dosya Sorgulama" ekranındaki tarih aralığı filtresiyle AYNI
# search_phrase_detayli.ajx sorgusu (icra_core.build_payload/ENDPOINT).
PARAMETRELER = [
    ("birim_adi", "Birim (İcra Dairesi) — tam ad", ""),
    ("baslangic", "Açılış Başlangıç Tarihi (GG.AA.YYYY)", ""),
    ("bitis", "Açılış Bitiş Tarihi (GG.AA.YYYY)", ""),
]

HEDEF_TUR = "Kapalı Tebligat"
PTT_ENDPOINT = "https://api.ptt.gov.tr/api/ShipmentTracking"
ELEKTRONIK_ANAHTAR = "Elektronik Tebligat"

# Avukat portalından atılan HER talep (21/2 yeniden tebliğ dahil) dosyanın evrak
# listesine bu türde düşüyor — dosya açılışındaki otomatik "Kapalı Tebligat"/
# "Ödeme İcra Emri" bu türde DEĞİL (kullanıcı isteği, 2026-08-13: canlı
# doğrulandı — dosya 2026/98353'te tam olarak bu türde 1 evrak vardı ve
# kullanıcı orada gerçekten yeniden tebliğ talebi göndermiş olduğunu
# doğruladı). Yalnız VARLIĞI kontrol edilir, PDF içeriği okunmaz.
TALEP_EVRAK_TUR = "Avukat Portal Tebligat Talebi"

# "Kapalı E-Tebliğ Mazbatası" — kullanıcı bulgusu (2026-08-03, yedinci tur —
# CANLI doğrulandı, dosya 2026/84410): elektronik tebligat gönderildiğinde
# UYAP, "Kapalı Tebligat" evrakından AYRI bir evrak türü daha üretiyor; bu
# mazbatanın PDF'i GERÇEK UETS teslim geçmişini (Tarih/Açıklama tablosu) ve
# barkodu ("Taahhütlü No") taşıyor — bu barkod, karşılık gelen "Kapalı
# Tebligat" evrakının barkoduyla AYNI, yani ikisi eşleştirilebiliyor (bkz.
# `_e_teblig_haritasini_olustur`).
KAPALI_E_TEBLIG_MAZBATASI_TUR = "Kapalı E-Tebliğ Mazbatası"

# "Tebliğ Mazbatası" — 2026/94270, 2026/94269, 2026/94274 gibi gerçek dosyalarda
# CANLI DOĞRULANDI (2026-07-28): "tur" alanı birebir bu string, "aciklama"
# alanı zaten "... - Barkod Numarası: ... - Tebliğ Edildi/Edilmedi" biçiminde
# dolu geliyor — ayrıca barkod/PDF okumaya gerek yok, sadece VARLIĞI (ve
# aciklama'daki hazır özeti) kontrol edilir.
TEBLIG_MAZBATASI_TUR = "Tebliğ Mazbatası"

# "Kapalı Tebliğ Mazbatası" — kullanıcı tarafından bildirildi (fiziki tebligatın
# taranmış/fotoğraf mazbatası, içeriği okunamaz — bkz. modül başlığı), ANCAK
# canlı örneklemde (27+ gerçek dosya) HİÇ rastlanmadı; bu yüzden tam string
# eşleşmesi yerine Türkçe karakter/boşluk farkına toleranslı `_tur_anahtar`
# ile eşleştirilir. Yalnızca VARLIĞI işaretlenir, PDF'i İNDİRİLMEZ/OKUNMAZ.
KAPALI_TEBLIG_MAZBATASI_TUR = "Kapalı Tebliğ Mazbatası"

# Merkezi Takip Sistemi (MTS) dosyaları — 2026-09-24 CANLI bakıldı (TEK dosya:
# MTS 2026/196039; genelleme için başka örneklerle de doğrulanmalı): tebligat
# "Kapalı Tebligat" DEĞİL bu türle geliyor, bu yüzden 317 açık MTS dosyasında
# hiç barkod yoktu. İçerik yine PDF (application/pdf), barkod aynı Code-39
# deseniyle gömülü (`barkod_bul` çalıştı). O dosyada bu türde İKİ evrak vardı
# ve İKİSİ de AYNI barkodu taşıyordu: biri ödeme emrinin kendisi (tür "Ödeme
# Emri" çıktı), diğeri boş mazbata formu (açıklama "Normal Tebligat[BORÇLU …]",
# tür boş çıktı). Bu yüzden MTS evrakları dosya içinde BARKODA GÖRE
# birleştirilir (bkz. `_dosya_isle`): aynı barkod ikinci satır açmaz, boş türü
# dolu olanla tamamlanır; mükerrer evrak `mts_mukerrer.json`'a yazılıp sonraki
# turlarda PDF'i bir daha indirilmez.
MTS_TEBLIGAT_TUR = "PTT MTS Tebligat Belgesi"
# Aynı dosyada: açıklaması "4100131175175 Barkod Numaralı Tebligat Mazbatası."
# — normal dosyalardaki "Tebliğ Mazbatası"nın MTS karşılığı (dönen mazbata).
MTS_TEBLIG_MAZBATASI_TUR = "PTT MTS Tebliğ Mazbatası"

# Mazbata açıklamasındaki barkod — DB'deki iki biçim (2026-09-24):
#   "Satış İlanı - [VELİ …] - Barkod Numarası: 3000120018142 - Tebliğ Edildi"
#   "4100140832731 Barkod Numaralı Tebligat Mazbatası."   (MTS)
_MAZBATA_BARKOD_RE = re.compile(r"Barkod\s+Numaras[ıi]\s*:\s*(\d{8,20})|(\d{8,20})\s*Barkod\s+Numaral",
                                re.IGNORECASE)


def kendi_mazbatasi_dondu_mu(barkod, mazbata_var, mazbata_aciklama):
    """Bu barkodun KENDİ mazbatası dosyaya dönmüş mü? (açıklamadaki barkod = bu
    barkod). 2026-09-25 kullanıcı kararı: mazbatası dönen tebligat sonuçlanmıştır,
    PTT'de sorgulanmaz. Barkodsuz 'Var' kayıtlar sayılmaz (hangi barkoda ait belli değil)."""
    if not (mazbata_var and barkod):
        return False
    m = _MAZBATA_BARKOD_RE.search(mazbata_aciklama or "")
    return bool(m) and (m.group(1) or m.group(2)) == str(barkod)


_TEBLIG_SONUCU_RE = re.compile(r"-\s*tebli[gğ]\s+edil(di|emedi)\s*$", re.IGNORECASE)


def teblig_sonucu_aciklamada_mi(evrak_aciklama):
    """UYAP, mazbata dönünce tebligat evrakının açıklamasına sonucu ekliyor:
    'Normal Tebligat[BORÇLU …] - Tebliğ Edildi' / '- Tebliğ Edilemedi' (barkod başına)."""
    return bool(_TEBLIG_SONUCU_RE.search(dosya_core.tr_lower(evrak_aciklama or "").replace("ğ", "g")))


def mazbatasi_dondu_mu(barkod, evrak_aciklama, mazbata_var, mazbata_aciklama):
    """2026-09-25 kullanıcı (iki kez): "mazbatası icra dairesine dönmüş olanları
    sorgulamasın." Dönmüş sayılır: evrak açıklamasında tebliğ sonucu varsa, ya da
    kayıtta mazbata işareti varsa (barkodsuz 'Var' DAHİL), ya da mazbata açıklaması
    bu barkodu taşıyorsa. Bu barkodlar PTT'ye SORULMAZ."""
    return (teblig_sonucu_aciklamada_mi(evrak_aciklama) or bool(mazbata_var)
            or kendi_mazbatasi_dondu_mu(barkod, mazbata_var, mazbata_aciklama))


def _mazbata_haritasi(evraklar):
    """({barkod: mazbata evrakı}, [açıklamasında barkod olmayan mazbatalar])."""
    harita, barkodsuz = {}, []
    for e in evraklar:
        if e.get("tur") not in (TEBLIG_MAZBATASI_TUR, MTS_TEBLIG_MAZBATASI_TUR):
            continue
        m = _MAZBATA_BARKOD_RE.search(e.get("aciklama") or "")
        if m:
            harita.setdefault(m.group(1) or m.group(2), e)
        else:
            barkodsuz.append(e)
    return harita, barkodsuz

_TR_CEVIRI = str.maketrans({
    "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "İ": "i",
    "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u",
})


def _tur_anahtar(tur):
    """Türkçe karakter/boşluk farkına toleranslı karşılaştırma anahtarı —
    canlı doğrulanamamış evrak türü adları için (bkz. KAPALI_TEBLIG_MAZBATASI_TUR)."""
    return re.sub(r"\s+", " ", (tur or "").translate(_TR_CEVIRI).lower()).strip()


# Dosyalar arası bekleme. 2026-09-24: sabit 2 sn kaldırıldı — 6800 dosyada
# ~3,8 saat salt bekleme ediyordu, oysa bir dosyanın KENDİ istekleri (evrak
# listesi, PDF'ler) zaten arada beklemeden art arda gidiyor ve UYAP'ın
# "eşzamanlı sorgulama" reddine karşı `dosya_core._post_eszamanli_korumali`'nin
# tekrar-deneme ağı var. UYARLAMALI: bir dosyada o ret görülürse sonraki
# YAVAS_MOD_DOSYA dosya boyunca YAVAS_MOD_SN beklenir (eski davranış).
SATIR_ARASI_SN = 0.0
YAVAS_MOD_SN = 2.0
YAVAS_MOD_DOSYA = 20

# Sonucu artık DEĞİŞMEYECEK PTT durumları — bu barkodlar PTT'de tekrar
# sorgulanmaz (2026-09-24). Karşılaştırma `dosya_core.tr_lower` ile yapılır.
# İADE: kullanıcı kararı (2026-09-24) — iade sonrası hareketler takip edilmez.
SONUCLANMIS_PTT_DURUMLARI = ("TESLİM EDİLDİ", "İADE")

# Üst üste bu kadar dosya YALNIZ hatayla biterse (UYAP bağlantısı yok/oturum
# düştü belirtisi — 2026-09-23 akşamı saatlerce böyle döndü) tur durur;
# kaldığı yer kaydedildiği için sonra devam edilebilir.
ARDISIK_HATALI_DOSYA_SINIRI = 10


def _dosya_no_ayir(dosya_no):
    """'2026/94270' -> (2026, 94270). Format uymazsa (None, None)."""
    m = re.match(r"^\s*(\d{4})\s*/\s*(\d+)\s*$", str(dosya_no or ""))
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def excel_oku(xlsx_yolu):
    """A: Birim/İcra Dairesi, B: Dosya No sütunlarını okur (başlık satırına göre
    kolon bulunur — 'Birim'/'Dosya No' içeren başlıklar aranır; bulunamazsa ilk
    iki sütun kullanılır). Döner: list[(birim_adi, dosya_no)]."""
    wb = openpyxl.load_workbook(xlsx_yolu, read_only=True, data_only=True)
    ws = wb.active
    satirlar = [r for r in ws.iter_rows(values_only=True) if any(c is not None for c in r)]
    if not satirlar:
        return []
    basliklar = [str(h or "").strip().lower() for h in satirlar[0]]
    try:
        bi = next(i for i, h in enumerate(basliklar) if "birim" in h or "daire" in h)
    except StopIteration:
        bi = 0
    try:
        di = next(i for i, h in enumerate(basliklar) if "dosya no" in h)
    except StopIteration:
        di = 1

    sonuc = []
    for row in satirlar[1:]:
        if bi >= len(row) or di >= len(row):
            continue
        birim, dosya_no = row[bi], row[di]
        if not birim or not dosya_no:
            continue
        sonuc.append((str(birim).strip(), str(dosya_no).strip()))
    return sonuc


def _dosya_id_coz(motor, birim_id, yil, sira, log):
    """birimId+yıl+sıra -> ham UYAP kaydı (search_phrase_detayli.ajx). birim_id
    VERİLEREK aranır — aynı yıl/sıra farklı birimlerde de olabileceğinden
    birimsiz arama YANLIŞ dosyayı döndürebilir (bkz.
    baro_pulu_makbuzu_indiren.py — aynı fonksiyon). TAM kaydı döner (yalnız
    dosyaId değil) — DB'ye kapak künyesi yazarken (bkz. `_dosya_isle`,
    `icra_models.ingest.dosya_kunyesi_kaydet`) gereken diğer alanlar
    (birimAdi/birimTuru*/dosyaDurumKod/... ) da bu kayıtta gelir."""
    payload = {
        "dosyaDurumKod": 0, "pageSize": 500, "pageNumber": 1,
        "dosyaYil": yil, "dosyaSira": sira,
        "birimId": birim_id, "birimTuru2": BIRIM_TURU2, "birimTuru3": BIRIM_TURU3,
    }
    veri = dosya_core._post_eszamanli_korumali(
        dosya_core._arka_plan_istek, motor, icra_core.ENDPOINT, payload, log)
    try:
        kayitlar = veri[0] if isinstance(veri, list) else []
        if kayitlar:
            return kayitlar[0]
    except Exception as e:
        # Önceden sessizce yutuluyordu (bare except: pass) — gerçek bir
        # ayrıştırma hatası da "dosya bulunamadı" gibi görünüyor, kullanıcı
        # sebebi hiç göremiyordu (kullanıcı bulgusu, 2026-08-13). Akış aynı
        # kalır (yine None döner), yalnız sebep artık loglanır.
        log(f"  ⚠️ Dosya kaydı ayrıştırılamadı: {e}")
    return None


def _tarih_araligi_dosyalari_getir(motor, birim_adi, baslangic, bitis, log):
    """PARAMETRELER'den gelen tarih aralığı girdisini search_phrase_detayli.ajx
    ile sorgular — UYAP'ın 'Dosya Sorgulama' ekranındaki tarih aralığı filtresiyle
    AYNI endpoint (bkz. icra_core.py başlığı). icra_core.IcraSorgu.ara() da bunu
    yapıyor ama HER dosya için AYRICA borçlu-detay isteği + Django DB senkronu
    çalıştırıyor — bu modül zaten kendi evrak/PDF/PTT işini yapacağından o ekstra
    ağır adımlar gereksiz; bu yüzden `build_payload`/`parse_records` doğrudan
    kullanılır (Django bağımlılığı da eklenmemiş olur). Döner: ham UYAP kayıt
    listesi — dosyaId zaten yanıtta geldiğinden AYRICA `_dosya_id_coz` ile
    çözülmesine gerek YOK."""
    birim_adi = (birim_adi or "").strip()
    baslangic = (baslangic or "").strip()
    bitis = (bitis or "").strip()
    if not birim_adi:
        raise RuntimeError("Birim adı girilmedi.")
    if not baslangic and not bitis:
        raise RuntimeError("Açılış başlangıç/bitiş tarihinden en az biri girilmeli "
                            "(yoksa birimdeki TÜM dosyalar taranır — çok uzun sürer).")
    if not icra_core.birim_id_bul(birim_adi):
        raise RuntimeError(f"'{birim_adi}' birimi bulunamadı — tam adını yazın.")

    values = {"birimAdi": birim_adi, "dosyaAcilisTarihiStart": baslangic, "dosyaAcilisTarihiEnd": bitis}
    tum, gorulen, sayfa = [], set(), 1
    while True:
        payload = icra_core.build_payload(values, icra_core.DURUM_VARSAYILAN, None)
        payload["pageNumber"] = sayfa
        _status, veri = motor._post(icra_core.ENDPOINT, payload)
        kayitlar = icra_core.parse_records(veri)
        for rec in kayitlar:
            did = rec.get("dosyaId")
            if did and did in gorulen:
                continue
            if did:
                gorulen.add(did)
            tum.append(rec)
        if len(kayitlar) < icra_core.PAGE_SIZE:
            break
        sayfa += 1
        if sayfa > 200:
            log("⚠️ Sayfa sınırına (200) ulaşıldı; bazı dosyalar alınmamış olabilir.")
            break
        log(f"… {len(tum)} dosya alındı, sayfa {sayfa} çekiliyor")
    log(f"📅 Tarih aralığında {len(tum)} dosya bulundu.")
    return tum


def _evrak_pdf_indir(motor, dosya_id, evrak_id, log_fn=None, istek_sarici=None, timeout=90):
    """`dosya_core.evrak_html_indir`'in ham-bayt eşdeğeri — o fonksiyon içeriği
    utf-8 ile decode ettiğinden ("Kapalı Tebligat" GERÇEKTE bir PDF, HTML
    değil — bkz. modül başlığındaki canlı doğrulama notu) burada AYNI istek
    sırası (önce `getDocViewerParameters.ajx`, sonra GET) tekrarlanır ama
    yanıt bayt olarak (decode edilmeden) döner. Döner: (content_type, ham_bayt)."""
    log = log_fn or (lambda *a, **k: None)
    sarici = istek_sarici or dosya_core._bireysel_istek

    dosya_core._post_eszamanli_korumali(sarici, motor, dosya_core.DOC_VIEWER_HAZIRLIK_ENDPOINT, {}, log)

    qs = urllib.parse.urlencode({"evrakId": evrak_id, "dosyaId": dosya_id})
    url = f"{motor.base}/{dosya_core.EVRAK_ICERIK_YOLU}?{qs}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("accept", "*/*")
    req.add_header("referer", f"{motor.base}/dosya-sorgulama")

    with sarici():
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = r.status
            content_type = r.headers.get("Content-Type", "")
            data = r.read()
    if status >= 400:
        raise RuntimeError(f"Evrak indirilemedi (HTTP {status})")
    return content_type, data


# ── PDF içi barkod çıkarımı (bkz. modül başlığı — CANLI DOĞRULANDI) ─────────
_PDF_STREAM_RE = re.compile(rb"\d+ 0 obj\s*<<(.*?)>>\s*stream\r?\n", re.DOTALL)
_BARKOD_PDF_RE = re.compile(rb"\(\*([0-9A-Za-z]{6,20})\*\)\s*Tj")


def _pdf_metin_coz(pdf_bytes):
    """PDF'teki tüm FlateDecode content stream'lerini çözüp birleştirir
    (font/resource gibi Flate OLMAYAN stream'ler sessizce atlanır)."""
    parcalar = []
    for m in _PDF_STREAM_RE.finditer(pdf_bytes):
        start = m.end()
        end = pdf_bytes.find(b"endstream", start)
        if end == -1:
            continue
        try:
            parcalar.append(zlib.decompress(pdf_bytes[start:end]))
        except zlib.error:
            continue
    return b"\n".join(parcalar)


def barkod_bul(pdf_bytes, log_fn=None):
    """PDF içindeki Code-39 barkod fontuyla (`/F4`, IDAutomationHC39M) çizilen
    metni bulur — Code-39 kuralınca değer `*...*` arasında geçer (bkz. modül
    başlığı). Birden fazla eşleşme varsa (mazbatanın iki nüshası aynı barkodu
    taşıyor) ilkini döner. Bulunamazsa None döner."""
    log = log_fn or (lambda *a, **k: None)
    if not pdf_bytes.startswith(b"%PDF"):
        log(f"    ⚠️ Beklenmeyen içerik türü (PDF değil, {len(pdf_bytes)} bayt) — barkod aranmadı.")
        return None
    metin = _pdf_metin_coz(pdf_bytes)
    m = _BARKOD_PDF_RE.search(metin)
    if not m:
        log("    ⚠️ PDF içinde Code-39 barkod örüntüsü bulunamadı.")
        return None
    return m.group(1).decode("ascii")


# ── PDF içi gövde metni çıkarımı (borçlu adı eşleşmesi için — bkz. modül
# başlığındaki KULLANICI BULGUSU notu, CANLI DOĞRULANMADI) ─────────────────
_PDF_TEXT_STR_RE = re.compile(rb"\((?:[^()\\]|\\.)*\)")
_PDF_ESCAPE_RE = re.compile(rb"\\([0-7]{1,3}|.)", re.DOTALL)


def _pdf_dize_coz(ham_parantezli):
    """PDF metin operatörünün ('(...)Tj'/TJ) dizge operandındaki kaçış
    dizilerini çözer: '\\(' '\\)' '\\\\' ve sekizli kaçışlar (`\\ddd`)."""
    ic = ham_parantezli[1:-1]

    def _cevir(m):
        g = m.group(1)
        if g in (b"(", b")", b"\\"):
            return g
        if re.match(rb"^[0-7]{1,3}$", g):
            return bytes([int(g, 8) & 0xFF])
        return b""  # satır-sonu kaçışı (\\<newline>) gibi görmezden gelinenler
    return _PDF_ESCAPE_RE.sub(_cevir, ic)


def _pdf_govde_metni(pdf_bytes, log_fn=None):
    """PDF içeriğindeki TÜM metin-gösterme operatörlerinin dizge
    operandlarını sırayla çıkarıp birleştirir — tam bir PDF ayrıştırıcı
    DEĞİL, yalnız düz metin arama (borçlu adı eşleşmesi) için yeterli kaba
    bir çıkarım (bkz. modül başlığındaki UYARI — CANLI DOĞRULANMADI)."""
    metin = _pdf_metin_coz(pdf_bytes)
    parcalar = []
    for m in _PDF_TEXT_STR_RE.finditer(metin):
        try:
            parcalar.append(_pdf_dize_coz(m.group(0)))
        except Exception:
            continue
    ham = b"".join(parcalar)
    try:
        return ham.decode("cp1254")
    except UnicodeDecodeError:
        return ham.decode("latin-1", "replace")


# ── Kapalı E-Tebliğ Mazbatası çözümü (kullanıcı bulgusu, 2026-08-03, yedinci
# tur — CANLI doğrulandı, dosya 2026/84410, örnek dosya
# "2026_84410_Kapalı_E-Tebliğ_Mazbatası_07_07_2026.pdf") ─────────────────────
# Gövde metni (tr_lower ile) şu düz metne çözülüyor (satır sonları YOK, tablo
# hücreleri art arda eklenmiş): "...Örnek No: 25TarihAçıklama30/06/2026
# 15:36Muhatap hesabına teslim edilmek üzere UETStarafından teslim
# alındı.30/06/2026 15:36Tebligat alıcı için ayrılmış tebligat alanına
# (hesabına)başarılı bir şekilde konuldu.05/07/2026Tebligat, alıcının
# hesabına iletilmesine müteakip mevzuat gereği belirlenen süre sonunda
# otomatik olarak okundu sayıldı.E-TEBLİĞ MAZBATASIKarar No:*5002864776768*
# Yukarıda Belirtilen Tebliğ Bilgileri...". "TarihAçıklama" başlığı ile
# "E-TEBLİĞ MAZBATASI" alt başlığı ARASINDAKİ bölüm, art arda
# "GG/AA/YYYY[ SS:DD]<açıklama>" satırlarından oluşan bir tabloya karşılık
# geliyor; SON satır UETS'in vardığı NİHAİ sonucu (gerçekten okundu ya da
# süre sonunda otomatik okundu sayıldı) taşıyor — bu, e-tebligatın GERÇEK
# tebliğ tarihi/durumudur (kullanıcı: "borçluya e-tebligat gönderildiyse
# kapalı e-tebliğ mazbatasında okunmuş sayıldığı tarih yer alır, tebliğ
# tarihi o tarih olur").
_E_TEBLIG_TABLO_RE = re.compile(r"tarihaciklama(.*?)(?:e-teblig mazbatasi|$)", re.DOTALL)
_E_TEBLIG_SATIR_RE = re.compile(r"(\d{2}/\d{2}/\d{4})(?:\s+(\d{2}:\d{2}))?(.*?)(?=\d{2}/\d{2}/\d{4}|\Z)", re.DOTALL)


def _e_teblig_mazbatasi_coz(govde_metni, log_fn=None):
    """Kapalı E-Tebliğ Mazbatası PDF gövdesindeki Tarih/Açıklama tablosunun
    SON satırını (tarih, açıklama) ikilisi olarak döner — bkz. yukarıdaki
    canlı doğrulanmış örnek. Tablo bulunamazsa ("", "") döner."""
    log = log_fn or (lambda *a, **k: None)
    govde_l = dosya_core.tr_lower(govde_metni or "")
    m = _E_TEBLIG_TABLO_RE.search(govde_l)
    if not m:
        log("    ⚠️ E-Tebliğ Mazbatası tablosu (Tarih/Açıklama) bulunamadı.")
        return "", ""
    # tr_lower karakter-karşılığı 1:1 (uzunluk değişmez — bkz. icra_core.tr_lower),
    # bu yüzden govde_l üzerindeki konumlar orijinal (küçültülmemiş) govde_metni
    # üzerinde de aynı aralığa karşılık gelir — orijinal metin ekranda daha
    # okunaklı olduğundan (Türkçe karakterler korunur) o dilimlenir.
    tablo = govde_metni[m.start(1):m.end(1)]
    satirlar = list(_E_TEBLIG_SATIR_RE.finditer(tablo))
    if not satirlar:
        return "", ""
    son = satirlar[-1]
    tarih = son.group(1)
    if son.group(2):
        tarih = f"{tarih} {son.group(2)}"
    aciklama = re.sub(r"\s+", " ", son.group(3)).strip()
    return tarih, aciklama


def _e_teblig_haritasini_olustur(motor, dosya_id, evraklar, log):
    """[2026-09-24'ten beri ÇAĞRILMIYOR — kullanıcı kararı: "okundu" bilgisine
    girilmez, tebliğ tarihi = evrak tarihi (bkz. e_teblig_teblig_tarihi).
    Mazbata çözümü gerekirse diye bırakıldı.] Dosyadaki TÜM 'Kapalı E-Tebliğ Mazbatası' evraklarını indirir, her
    birinin barkodunu (aynı Code-39 font yöntemiyle, bkz. `barkod_bul` —
    bu mazbatalarda da 'Taahhütlü No' alanı aynı fontla basılıyor) ve
    Tarih/Açıklama tablosunun son satırını çözüp barkod -> (tarih, açıklama)
    sözlüğüne ekler. Bu barkod, karşılık gelen 'Kapalı Tebligat' evrakının
    barkoduyla AYNI olduğundan (canlı doğrulandı) elektronik tebligatın
    GERÇEK sonucunu bulmak için kullanılır (bkz. `_dosya_isle`). Yalnız
    GERÇEKTEN bir elektronik evrakla karşılaşılınca ÇAĞRILIR — dosyada
    elektronik tebligat yoksa hiç mazbata indirilmez."""
    harita = {}
    mazbatalar = [e for e in evraklar if e.get("tur") == KAPALI_E_TEBLIG_MAZBATASI_TUR]
    for mz in mazbatalar:
        try:
            _, mz_pdf = _evrak_pdf_indir(
                motor, dosya_id, mz.get("evrakId"), log_fn=log, istek_sarici=dosya_core._arka_plan_istek)
            mz_barkod = barkod_bul(mz_pdf, log)
            if not mz_barkod:
                continue
            mz_govde = _pdf_govde_metni(mz_pdf, log)
            tarih, aciklama = _e_teblig_mazbatasi_coz(mz_govde, log)
            if tarih:
                harita[mz_barkod] = (tarih, aciklama)
        except Exception as e:
            log(f"    ⚠️ E-Tebliğ Mazbatası okunamadı (evrak {mz.get('birimEvrakNo')}): {e}")
    if mazbatalar:
        log(f"  📬 {len(harita)}/{len(mazbatalar)} E-Tebliğ Mazbatası çözüldü.")
    return harita


# Kullanıcı bulgusu (2026-08-03, altıncı tur): burada eskiden bir
# `_borclu_adi_govdede_var_mi` fonksiyonu vardı ("Kapalı Tebligat" PDF'inin
# gövde metninde borçlunun adı geçmiyorsa 3. şahıs tebligatı sayılıp DB'ye
# YAZILMAZDI). KALDIRILDI: indirilen PDF ihbarnamenin kendisi değil genel bir
# "Tebliğ Mazbatası" zarf şablonu olduğundan, bankaya/işverene giden bir haciz
# ihbarnamesinde borçlunun adı bu metinde YAPISAL OLARAK hiç geçmiyor (canlı
# doğrulandı, dosya 2026/84410) — filtre "kirli veri"yi değil 3. şahsa giden
# HER haciz ihbarnamesini eliyordu. Artık ayrım yapılmadan HER 'Kapalı
# Tebligat' evrakı DB'ye yazılır; kime gittiği bilgisi (borçlu/banka/işveren)
# yalnız `_tebligat_turunu_belirle`nin ürettiği "Tebligat Türü" sütununda
# gösterilir (bkz. modül başlığı).


# ── Tebligat türü sınıflandırması (bkz. modül başlığındaki KULLANICI
# BULGUSU, 2026-08-03/beşinci tur — CANLI toplu veriyle DOĞRULANMADI) ──────
_IHTIVA_EDER_RE = re.compile(r"(.{0,140}?)\s*ihtiva\s+ed(?:er|ilmi|iyor)", re.IGNORECASE)
# Kullanıcı bulgusu (2026-08-03, altıncı tur — CANLI doğrulandı, dosya
# 2026/84410): gerçek UYAP metni "üçüncü taraf:" (iki nokta üst üste ile)
# DEĞİL, "3. Taraf" (rakamla, iki nokta üst üste YOK) diyor — ör.
# "Elektronik Tebligat[Tebligat Yapılacak 3. Taraf DENİZBANK ANONİM
# ŞİRKETİ]". Her iki biçim de (eski varsayım + gerçek biçim) kabul edilir.
_UCUNCU_TARAF_RE = re.compile(r"(?:ucuncu taraf|3\.\s*taraf)\s*:?\s*([^\]]*)\]")

# Kullanıcı bulgusu (2026-08-03, yedinci tur — CANLI doğrulandı, dosya
# 2026/84410'un "Kapalı E-Tebliğ Mazbatası" PDF'i): zarfın içeriği "BU
# ZARFTA <belge adı> VARDIR" cümlesiyle beyan ediliyor — ör. "BU ZARFTA
# Ödeme İcra Emri VARDIR", "BU ZARFTA İcra Daireleri Haciz Tebliğ Yazısı
# VARDIR". Beşinci turda varsayılan "... ihtiva eder" biçimi hâlâ olası bir
# şablon farkı için `_IHTIVA_EDER_RE` ile ayrıca denenir (o CANLI
# doğrulanmadı).
_ZARF_ICERIGI_RE = re.compile(r"bu zarfta\s+(.*?)\s+vardir")
# Mazbata başlığındaki "Maaş haciz müzekkeresi vardır." (tr_lower sonrası ASCII;
# PDF metninde kelimeler bitişik de çıkabildiği için \s*).
_MAAS_MUZEKKERE_RE = re.compile(r"maas\s*haciz\s*muzekkere")


# `_tebligat_turunu_belirle`'nin sabit kategorileri (dışındakiler: zarf içeriği
# metni ya da "Belirsiz"). Kullanıcının "Tür Eşleştir"de eklediği türler için
# bkz. tur_eslestirme.kategoriler().
BILINEN_TURLER = frozenset(tur_eslestirme.TEMEL_TURLER)


def _tebligat_turunu_belirle(aciklama, pdf_govde_metni=None):
    """Evrak açıklaması + (varsa) PDF gövde metninden tebligat türünü
    sınıflandırır. Döner: 'Ödeme Emri' | '103 Davetiyesi' |
    'Kıymet Takdiri Raporu' | 'Bilirkişi Raporu' | 'Banka (Haciz
    İhbarnamesi)' | 'Maaş (Haciz İhbarnamesi)' | 'Haciz İhbarnamesi' (taraf
    belirsiz) | 'ihtiva eder' sonrası yakalanan ham metin (bilinen bir
    kategoriyle eşleşmediyse — kullanıcı testte anahtar kelime eksikse
    görüp bildirebilsin diye SESSİZCE boş bırakılmaz) | "" (hiçbir ipucu
    bulunamadıysa). Karşılaştırma `tr_lower` (Türkçe karakterleri ASCII'ye
    çevirir) ile yapılır, bu yüzden anahtar kelimeler ASCII yazılmıştır."""
    aciklama_l = dosya_core.tr_lower(aciklama or "")
    govde_l = dosya_core.tr_lower(pdf_govde_metni or "")
    birlesik = f"{aciklama_l} {govde_l}"

    # 2026-09-24 kullanıcı: "Ödeme İcra Emri benim için ödeme emri ile aynı şey" —
    # UYAP ilamsız ödeme emri ile ilamlı icra emri için bu ortak çatı adı kullanıyor.
    if "odeme emri" in birlesik or "odeme icra emri" in birlesik:
        return "Ödeme Emri"
    if "kiymet takdir" in birlesik:
        return "Kıymet Takdiri Raporu"
    if "bilirkisi" in birlesik:
        return "Bilirkişi Raporu"
    if "103" in birlesik and ("davet" in birlesik or "mal beyan" in birlesik or "madde" in birlesik):
        return "103 Davetiyesi"

    ucuncu_taraf_m = _UCUNCU_TARAF_RE.search(aciklama_l)
    ucuncu_taraf = ucuncu_taraf_m.group(1) if ucuncu_taraf_m else ""

    # Kullanıcı bulgusu (2026-08-03, yedinci tur — CANLI doğrulandı, dosya
    # 2026/84410'un "Kapalı E-Tebliğ Mazbatası" PDF'i): zarfın NE İÇERDİĞİ
    # ("Ödeme İcra Emri", "İcra Daireleri Haciz Tebliğ Yazısı" vb.) "BU
    # ZARFTA ... VARDIR" cümlesinde geçiyor — "3. Taraf" parantezi ise SADECE
    # KİME gönderildiğini (kurum/kişi adı) söylüyor. "Maaş"/"ücret"/"işveren"
    # anahtar kelimesi bu yüzden ZARF İÇERİĞİNDE aranmalı, alıcı adında DEĞİL
    # (aksi halde işverenin şirket adı hiçbir zaman bu kelimeleri
    # içermeyeceğinden maaş haczi hiç yakalanamaz).
    zarf_icerigi_m = _ZARF_ICERIGI_RE.search(govde_l)
    zarf_icerigi = zarf_icerigi_m.group(1) if zarf_icerigi_m else ""

    # Kullanıcı bulgusu (2026-09-23 — CANLI doğrulandı, İzmir 7. İcra
    # 2024/11435'teki 3 maaş haczi tebligatı: 1 normal + 2 elektronik, alıcı
    # açıklamada "ARACIKISIKURUM <işveren>"): mazbata başlığında "Maaş haciz
    # müzekkeresi vardır." yazıyor, "BU ZARFTA ... VARDIR" cümlesinde ise yalnız
    # "Haciz Karar Yazısı"/"Tensip zaptı" geçiyor — zarf içeriğine bakan eski
    # kural bunları hiç yakalayamıyor, tür BOŞ kalıyordu.
    if _MAAS_MUZEKKERE_RE.search(govde_l):
        return "Maaş (Haciz Müzekkeresi)"

    if "haciz ihbarname" in birlesik or "haciz ihbarname" in zarf_icerigi:
        # Kullanıcı bulgusu (2026-08-03, altıncı tur — CANLI doğrulandı):
        # "banka" YERİNE "bank" aranır — DENİZBANK/AKBANK/ŞEKERBANK/ING
        # BANK/ENPARA BANK gibi çoğu banka adı "...banka" değil "...bank"
        # (sonunda 'a' YOK) ile bitiyor, "banka" substring'i bunları kaçırıyordu.
        if "bank" in (ucuncu_taraf or birlesik):
            return "Banka (Haciz İhbarnamesi)"
        if any(k in zarf_icerigi for k in ("maas", "ucret", "isveren", "is yeri", "hizmet")):
            return "Maaş (Haciz İhbarnamesi)"
        return "Haciz İhbarnamesi"

    # "haciz ihbarname" ibaresi geçmese bile elektronik tebligatın üçüncü
    # taraf parantezi (bkz. modül başlığı, CANLI doğrulandı) banka ayrımı
    # için tek başına yeterli olabilir (kurum ADI zaten "bank" içeriyor).
    if ucuncu_taraf and "bank" in ucuncu_taraf:
        return "Banka"
    if any(k in zarf_icerigi for k in ("maas", "ucret", "isveren", "is yeri", "hizmet")):
        return "Maaş"

    # 2026-09-24: eskiden burada "… ihtiva eder" öncesindeki 140 karakter HAM
    # olarak tür diye yazılıyordu — DB'de ~290 kayıtta mazbata formunun şablon
    # metni çıktı ("İflas DairesiDosya No:2026/7 İflasNo:TaahhütlüAdı ve
    # Soyadı:" gibi; çoğu vekil/alacaklı/diğer alacaklıya giden tebligatlar).
    # Artık: zarfın beyan ettiği içerik ("BU ZARFTA <X> VARDIR" — canlı
    # doğrulanmış desen) varsa o; yoksa "Belirsiz". PDF okunduktan sonra tür
    # HİÇ boş kalmaz → bu evrakın PDF'i bir daha indirilmez (bkz. _dosya_isle).
    if pdf_govde_metni:
        if zarf_icerigi_m:
            # tr_lower uzunluğu korur → aynı aralık özgün metinde okunaklı hâli verir.
            ham = pdf_govde_metni[zarf_icerigi_m.start(1):zarf_icerigi_m.end(1)]
            ham = re.sub(r"\s+", " ", ham).strip(" .,:;\"'()[]")
            if 2 < len(ham) <= 80:
                # 2026-09-25: kullanıcı bu yazılışı "Tür Eşleştir"de bir türe
                # bağladıysa o tür; yoksa ham metin kalır ve pencerede sorulur.
                return tur_eslestirme.eslestir(ham) or ham
        return "Belirsiz"
    return ""


def ptt_sorgula(barkod, log_fn=None, timeout=20):
    """PTT gönderi takip sorgusu. 2026-09-23: PTT doğrudan HTTP isteğini
    kapattı (X-Tracking-Token + Turnstile, 404) — sorgu artık ptt.gov.tr
    sayfası üzerinden Playwright ile, insan hızında yapılır (bkz.
    `ptt_tarayici`). Dönüş sözleşmesi aynı: PTT listesinin ilk elemanı (dict)
    ya da {"_hata": ...}. `calistir` turu boyunca tek tarayıcı paylaşılır."""
    return ptt_tarayici.ptt_sorgula(barkod, log_fn)


# İşlem modları (kullanıcı isteği, 2026-09-23 — üç ayrı düğme):
MOD_UYAP = "uyap"     # yalnız UYAP tebligat verisi (tür, tarih, barkod, mazbata) — PTT YOK
MOD_PTT = "ptt"       # yalnız DB'deki barkodların PTT sorgusu — UYAP YOK
MOD_IKISI = "ikisi"   # ikisi birden (eski/varsayılan davranış)
MODLAR = (MOD_UYAP, MOD_PTT, MOD_IKISI)

# Yalnız-UYAP modunda PTT yerine konan işaret: `_hata` taşıdığı için
# `_barkodu_veritabanina_kaydet` PTT alanlarını YAZMAZ (önceki PTT durumu korunur).
_PTT_ATLANDI = {"_hata": "PTT sorgulanmadı (yalnız UYAP tebligat verisi)", "_atlandi": True}
# Sonuçlanmış barkod için PTT'ye gidilmediğinde konan işaret — aynı şekilde
# `_hata` taşıdığı için DB'deki PTT alanlarına (ham yanıt dahil) dokunulmaz.
_PTT_SONUCLANMIS = {"_hata": "PTT sorgulanmadı (sonuç kesin)", "_atlandi": True, "_sonuclanmis": True}

_SONUCLANMIS_PTT_ANAHTARLARI = None


def _ptt_sonuclandi(durum):
    """DB'deki PTT durumu artık değişmeyecek bir sonuç mu (bkz. SONUCLANMIS_PTT_DURUMLARI)?"""
    global _SONUCLANMIS_PTT_ANAHTARLARI
    if _SONUCLANMIS_PTT_ANAHTARLARI is None:
        _SONUCLANMIS_PTT_ANAHTARLARI = {dosya_core.tr_lower(d).strip() for d in SONUCLANMIS_PTT_DURUMLARI}
    return dosya_core.tr_lower(durum or "").strip() in _SONUCLANMIS_PTT_ANAHTARLARI


def _e_teblig_sonuclandi(durum, tarih):
    """Elektronik tebligatın tebliğ tarihi DB'de var mı? (varsa sonuç KESİN —
    bir daha hiçbir şey indirilmez). 2026-09-24 kullanıcı kararı: "okundu"
    bilgisine hiç girilmez, tebliğ tarihi = evrak tarihi (bkz.
    `e_teblig_teblig_tarihi`)."""
    d = dosya_core.tr_lower(durum or "")
    return bool(tarih) and d.startswith("e-tebligat:")


# 2026-09-24 kullanıcı kararı: "e-tebligatta okundu bilgisine girme hiç; tebliğ
# tarihi yeterli, tebliğden itibaren 5 gün okuma süresi var." Kapalı E-Tebliğ
# Mazbatası İNDİRİLMEZ. 2026-10-02 kullanıcı düzeltmesi: tebliğ tarihi EVRAK
# TARİHİDİR (28.09 tebliğ → 03.10 yazmak yanlış); 5 gün yalnız sürenin başlangıcını
# öteler — bkz. dosya_core.sure_baslangici.
E_TEBLIG_OKUMA_SURESI_GUN = dosya_core.E_TEBLIG_OKUMA_SURESI_GUN
E_TEBLIG_DURUM_METNI = "E-Tebligat: tebliğ edildi (süre 5 gün sonra başlar)"


def e_teblig_teblig_tarihi(evrak_tarihi):
    """E-tebligatın tebliğ tarihi = evrak tarihi ('GG/AA/YYYY'); çözülemezse ""."""
    t = dosya_core._teblig_tarihini_coz(evrak_tarihi)
    return t.strftime("%d/%m/%Y") if t else ""


def _ptt_adimi(barkod, log, ptt_sorgusu):
    """(ptt_veri, ozet, tarih) — ptt_sorgusu False ise PTT'ye gidilmez."""
    if not ptt_sorgusu:
        return dict(_PTT_ATLANDI), "", ""
    ptt_veri = ptt_sorgula(barkod, log)
    ozet, tarih = _ptt_durum_ve_tarih(ptt_veri)
    log(f"  ✅ PTT: {ozet}" + (f" ({tarih})" if tarih else ""))
    return ptt_veri, ozet, tarih


def _durum_metni(ptt_veri, basarili_metin):
    if isinstance(ptt_veri, dict) and ptt_veri.get("_sonuclanmis"):
        return "✅ Sonuç kesin (PTT tekrar sorgulanmadı)"
    if isinstance(ptt_veri, dict) and ptt_veri.get("_atlandi"):
        return "📋 UYAP tebligat verisi alındı"
    if isinstance(ptt_veri, dict) and ptt_veri.get("_hata"):
        return "⚠️ PTT sorgulanamadı"
    return basarili_metin


def _ham_yanit_metni(ptt_veri):
    if not isinstance(ptt_veri, dict) or ptt_veri.get("_atlandi"):
        return ""
    return json.dumps(ptt_veri, ensure_ascii=False)


def _ptt_sorgula_http(barkod, log_fn=None, timeout=20):
    """ESKİ doğrudan HTTP yöntemi (2026-09-23'ten beri PTT 404 döndürüyor) —
    PTT ileride kapıyı yeniden açarsa diye referans olarak bırakıldı; hiçbir
    yerden çağrılmaz. Gövde ham JSON STRING olmalı (`{"barkod": ...}` 400 döner)."""
    log = log_fn or (lambda *a, **k: None)
    govde = json.dumps(barkod, ensure_ascii=False).encode("utf-8")
    istek = urllib.request.Request(PTT_ENDPOINT, data=govde, method="POST")
    istek.add_header("Content-Type", "application/json")
    istek.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(istek, timeout=timeout) as r:
            metin = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return {"_hata": f"PTT HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"_hata": f"PTT'ye ulaşılamadı ({getattr(e, 'reason', e)})"}
    try:
        veri = json.loads(metin)
    except Exception:
        return {"_hata": f"PTT yanıtı JSON değil: {metin[:200]!r}"}
    if isinstance(veri, list):
        return veri[0] if veri else {"_hata": "PTT boş liste döndü"}
    return veri


def _ptt_tarih_bicimle(ham):
    """PTT'nin `sondurum` alanındaki tarihler 'YYYYMMDD' (ör. 20260727, int ya
    da string) biçiminde geliyor — ekranda 'GG/AA/YYYY' olarak gösterilir.
    `hareketDongu` içindeki tarihler zaten 'GG/AA/YYYY' geldiğinden bu
    fonksiyondan GEÇİRİLMEZ (bkz. `_ptt_durum_ve_tarih`)."""
    s = str(ham or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[6:8]}/{s[4:6]}/{s[0:4]}"
    return s


def _ptt_durum_ve_tarih(veri):
    """PTT yanıtını (durum_metni, tarih_metni) çiftine çevirir (alan adları
    CANLI DOĞRULANDI — bkz. modül başlığı). `errorState` GERÇEK/kayıt var mı
    demek (isim yanıltıcı ama iki canlı örnekle doğrulandı: teslim edilmiş
    gerçek bir gönderide True, kayıtsızda False).

    `sondurum` — CANLI DOĞRULANDI (2026-07-28, 28 dosyalık toplu koşu) — yalnız
    TESLİM EDİLDİ'de değil, "DAĞITIMDA"/"İADE" gibi HENÜZ sonuçlanmamış
    durumlarda da dolu geliyor (`son_durum_aciklama` her zaman güncel durumu
    yansıtıyor). Ancak `teslim_tarihi` alanı yalnız GERÇEKTEN teslim
    edildiğinde gerçek bir tarih taşıyor — teslim edilmemişken PTT bu alana
    yer-tutucu olarak LİTERAL "0" STRING'i koyuyor (canlı örnek: DAĞITIMDA
    durumunda `teslim_tarihi: "0"`, gerçek güncel tarih ise
    `son_islem_tarihi`'nde). "0" niteliksiz bir değer olduğundan (boş değil,
    `or` ile atlanmaz) AYRICA kontrol edilip `son_islem_tarihi`'ne düşülür.
    Bu aracın asıl kullanım amacı çoğunlukla HENÜZ sonuçlanmamış
    (yolda/dağıtımda) tebligatları görmek olduğundan bu düzeltme önemli."""
    if not isinstance(veri, dict):
        return str(veri), ""
    if veri.get("_hata"):
        return veri["_hata"], ""
    if not veri.get("errorState"):
        return veri.get("errorMessage") or "Kayıt yok", ""
    son = veri.get("sondurum") or {}
    if son.get("son_durum_aciklama"):
        teslim_tarihi = son.get("teslim_tarihi")
        if not teslim_tarihi or str(teslim_tarihi).strip() == "0":
            teslim_tarihi = son.get("son_islem_tarihi")
        tarih = _ptt_tarih_bicimle(teslim_tarihi)
        return son["son_durum_aciklama"], tarih
    hareket = veri.get("hareketDongu")
    if isinstance(hareket, list) and hareket:
        son_hareket = hareket[-1]
        tarih = son_hareket.get("tarih", "")
        if son_hareket.get("saat"):
            tarih = f"{tarih} {son_hareket['saat']}".strip()
        return son_hareket.get("aciklama") or veri.get("errorMessage") or "Bilinmeyen durum", tarih
    return veri.get("errorMessage") or "Bilinmeyen durum", ""


# Ekrandaki/excel çıktısındaki tablo hep bu sırayla, hep bu kolonlarla
# gösterilir (satırlar arasında kolon farkı olursa UyapMasa tablosu satırları
# hizasız gösterir — bu yüzden her satır bu şablondan üretilir).
_KOLONLAR = ["Birim", "Dosya No", "Evrak Açıklaması", "Tebligat Türü", "Evrak Tarihi", "Barkod",
             "PTT Durumu", "Son İşlem Tarihi", "Tebliğ Mazbatası",
             "Kapalı Tebliğ Mazbatası", "Yeniden Tebliğ Talebi", "Durum", "Ham Yanıt"]


def _satir_olustur(temel, **degerler):
    satir = {k: "" for k in _KOLONLAR}
    satir.update(temel)
    satir.update(degerler)
    return satir


class _SonucListesi(list):
    """UyapMasa'ya dönen satır listesiyle AYNI zamanda üretilen Excel çıktısının
    baytlarını da taşıyan liste alt sınıfı. `_uretilmis_sonuc` (web) ve
    `uretilmis_runner` (masaüstü) sıradan bir `list` bekler ve tablo olarak
    çizer — `dosya_b64`/`dosya_ad` öznitelikleri onlar için görünmez/zararsız,
    ama web tarafı bunları görüp bir 'İndir' düğmesi ekleyebilir (bkz.
    server.py `uretilmis_run`/`_uretilmis_sonuc`)."""
    dosya_b64 = None
    dosya_ad = None


def _excel_bayt_uret(sonuclar):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Barkod Sorgu Sonuc"
    ws.append(_KOLONLAR)
    for satir in sonuclar:
        ws.append([satir.get(k, "") for k in _KOLONLAR])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _sonucu_excele_yaz(sonuclar, taban_ad, kaynak_klasoru, log):
    """İşlem bitince sonuç tablosunu bir .xlsx'e yazar. Web'de yüklenen dosya
    geçici (ve iş bitince silinen) bir klasörde yaşadığından "girdiyle aynı
    klasöre yaz" YETERSİZ — bu yüzden baytlar HER ZAMAN üretilip döndürülür
    (UyapMasa web bunu base64 olarak yanıta ekleyip tarayıcıda indirme düğmesi
    gösterir); `kaynak_klasoru` GERÇEKTEN yazılabilir bir yerse (masaüstü/CLI
    kullanımı) ayrıca oraya da best-effort bir kopya bırakılır — tarih aralığı
    modunda (girdi bir Excel dosyası DEĞİL) `kaynak_klasoru` None geçilir ve
    bu adım atlanır."""
    veri = _excel_bayt_uret(sonuclar)
    damga = time.strftime("%Y%m%d_%H%M%S")
    dosya_ad = f"{taban_ad}_Barkod_Sonuc_{damga}.xlsx"

    if kaynak_klasoru:
        try:
            cikti_yolu = os.path.join(kaynak_klasoru, dosya_ad)
            with open(cikti_yolu, "wb") as f:
                f.write(veri)
            log(f"📊 Excel çıktısı diske de yazıldı: {cikti_yolu}")
        except OSError:
            pass  # web'de girdi geçici klasördeyse beklenen durum — b64 zaten yeterli

    return dosya_ad, veri


# ── Veritabanı entegrasyonu ("Dosyalarım (Tümü)" ile AYNI kalıcı PostgreSQL) ─
# icra_core.py'nin IcraSorgu.ara() içinde kullandığı desenin AYNISI (django
# setup + gevşek hata yutma) — o sorgu Birim/Dosya kapak künyesini zaten bu
# veritabanına yazıyor; burada AYNI Dosya satırına (doğal anahtarla upsert
# edilerek) barkod sonucu bağlanır.
_DB_MODULLERI = None  # None=henüz denenmedi, False=kullanılamıyor, dict=hazır


def _db_baglantisini_kur(log):
    """icra_models Django uygulamasına bağlanır. PostgreSQL kapalı/erişilemezse
    sessizce None döner — barkod sorgusu yine de canlı UYAP+PTT üzerinden
    çalışmaya devam eder, yalnız DB kaydı atlanır."""
    global _DB_MODULLERI
    if _DB_MODULLERI is not None:
        return _DB_MODULLERI or None
    _DB_MODULLERI = False
    try:
        # Bağlantı bootstrap'i artık dosya_core._django_hazirla()'dan gelir —
        # aynı sys.path/DJANGO_SETTINGS_MODULE/django.setup() bloğu 5 ayrı
        # dosyada tekrarlanıyordu (kullanıcı isteği, 2026-08-13: bağlantı
        # kodu sadeleştirme). icra_core.py KASTEN dokunulmadan bırakıldı
        # (bkz. dosya_core.py başlığı) — yalnız burası, dosya_core'un zaten
        # modül seviyesinde import edilmiş olan hazır fonksiyonunu kullanır.
        dosya_core._django_hazirla()
        from icra_models.models import Dosya, DosyaTaraf, TebligatBarkod
        from icra_models.ingest import dosya_kunyesi_kaydet
        Dosya.objects.filter(pk=1).exists()  # bağlantıyı doğrula
        _DB_MODULLERI = {
            "Dosya": Dosya,
            "TebligatBarkod": TebligatBarkod,
            "DosyaTaraf": DosyaTaraf,
            "dosya_kunyesi_kaydet": dosya_kunyesi_kaydet,
        }
    except Exception as e:
        log(f"⚠️ Yerel veritabanı aktif değil (barkod sonuçları DB'ye yazılmayacak): {e}")
    return _DB_MODULLERI or None


# (2026-09-24) Eski `_borclu_coz_tekil` — "dosyada tek borçlu varsa HER
# tebligatı ona bağla" — kaldırıldı: banka/işveren/vekile giden tebligatlar da
# borçluya bağlanıyor, çok borçlulu dosyada hiçbiri bağlanmıyordu. Yerine
# açıklamadaki muhataptan evrak başına çözüm: dosya_core.tebligat_borclusunu_bul.


def _barkodu_veritabanina_kaydet(db, dosya_obj, evrak, log, **degerler):
    """Tek bir evrak/barkod sonucunu TebligatBarkod'a upsert eder — KALICI
    anahtar (dosya, birim_evrak_no) [uq_dosya_tebligat_barkod]. `dosya_obj`
    ÖNCEDEN çözülmüş bir Django `Dosya` kaydıdır (bkz. `_dosya_isle` — dosya
    başına BİR KEZ `dosya_kunyesi_kaydet` ile çözülür, evrak döngüsünde
    tekrar tekrar çözülmez). `db=None`/`dosya_obj=None` (bağlantı ya da künye
    yok) veya `birimEvrakNo` eksikse sessizce atlanır."""
    if not db or not dosya_obj:
        return
    birim_evrak_no = evrak.get("birimEvrakNo")
    if not birim_evrak_no:
        return
    ham = degerler.get("ham_yanit")
    if isinstance(ham, dict) and ham.get("_hata"):
        # 2026-09-23: PTT'ye ulaşılamadıysa (ör. "PTT HTTP 404", doğrulama
        # reddi) hata metni PTT DURUMU DEĞİLDİR — önceki doğru durumun
        # ("TESLİM EDİLDİ" vb.) üzerine yazılmasın; barkod/tür/mazbata bilgisi
        # yine kaydedilir.
        degerler = {k: v for k, v in degerler.items()
                    if k not in ("ptt_durumu", "son_islem_tarihi", "ham_yanit")}
    try:
        db["TebligatBarkod"].objects.update_or_create(
            dosya=dosya_obj, birim_evrak_no=int(birim_evrak_no),
            defaults=degerler,
        )
    except Exception as e:
        log(f"  ⚠️ DB'ye kaydedilemedi: {e}")


def _bilinen_barkod_kaydini_al(db, dosya_obj, birim_evrak_no):
    """Bu evrak için DAHA ÖNCE kaydedilmiş bir TebligatBarkod satırı varsa
    döner (kullanıcı bulgusu, 2026-08-03: barkod bir kez atanır, değişmez —
    bkz. modül başlığı) — yoksa ya da DB/dosya_obj/birim_evrak_no eksikse
    None döner (çağıran bu durumda PDF'i normal şekilde indirip işler)."""
    if not db or not dosya_obj or not birim_evrak_no:
        return None
    try:
        return db["TebligatBarkod"].objects.filter(
            dosya=dosya_obj, birim_evrak_no=int(birim_evrak_no)).first()
    except Exception:
        return None


def _taraflari_tazele(dosya_obj, dosya_id, log):
    """Seçili dosya güncellemesi (2026-09-30, taraf_da=True): dosyanın
    taraflarını UYAP'tan (dosya_taraf_bilgileri_brd.ajx, 1 istek) okuyup
    DosyaTaraf'a yazar — borçlu listesi kesinleşme/tebligat eşleşmesinden ÖNCE
    tazelenir. Hata turu durdurmaz."""
    try:
        ham = dosya_core.dosya_taraf_getir(dosya_id, log_fn=log, timeout=30, denemeler=2,
                                           istek_sarici=dosya_core._arka_plan_istek)
        if ham:
            n = dosya_core.dosya_taraf_kaydet(dosya_obj, ham, log)
            log(f"  👥 Taraflar güncellendi ({n}).")
    except Exception as e:
        log(f"  ⚠️ Taraflar güncellenemedi: {e}")


def _dosya_isle(motor, birim_adi, dosya_no, log, _ekle, db, rec_bilinen=None, ptt_sorgusu=True, taraf_da=False):
    """Tek bir (birim, dosya no) için 'Kapalı Tebligat' evrak(lar)ının barkodunu
    bulup PTT'de sorgular; sonuç satır(lar)ını `_ekle` ile biriktirir (ve
    DB bağlantısı varsa `db` üzerinden TebligatBarkod'a da yazar).
    `rec_bilinen` verilmişse (tarih aralığı modu — arama sonucunda zaten
    geliyor, dosyaId DAHİL) search_phrase_detayli.ajx ile YENİDEN çözülmez;
    Excel modunda (yalnız birim+dosya no varsa) None geçilip aşağıda çözülür."""
    temel = {"Birim": birim_adi, "Dosya No": dosya_no}
    try:
        rec = rec_bilinen
        dosya_id = rec.get("dosyaId") if rec else None
        if not dosya_id:
            yil, sira = _dosya_no_ayir(dosya_no)
            if yil is None:
                log(f"  ⚠️ Dosya no formatı tanınmadı: {dosya_no!r}")
                _ekle(_satir_olustur(temel, Durum="⚠️ Atlandı (dosya no formatı)"), "atlanan")
                return

            birim_id = icra_core.birim_id_bul(birim_adi)
            if not birim_id:
                log(f"  ⚠️ Birim bulunamadı: {birim_adi!r}")
                _ekle(_satir_olustur(temel, Durum="⚠️ Atlandı (birim bulunamadı)"), "atlanan")
                return

            rec = _dosya_id_coz(motor, birim_id, yil, sira, log)
            dosya_id = rec.get("dosyaId") if rec else None
            if not dosya_id:
                log("  ⚠️ Dosya bulunamadı.")
                _ekle(_satir_olustur(temel, Durum="⚠️ Atlandı (dosya bulunamadı)"), "atlanan")
                return

        evraklar = dosya_core.evrak_listesi_getir(
            dosya_id, log_fn=log, istek_sarici=dosya_core._arka_plan_istek)

        # 2026-09-24 kullanıcı kararı: mazbata BARKOD BAŞINA eşleştirilir. Eskiden
        # dosyadaki İLK mazbata dosyanın TÜM barkodlarına yazılıyordu — DB'de
        # "mazbata döndü" işaretli 6.078 kaydın 4.508'inde yazan mazbata aynı
        # dosyadaki BAŞKA bir barkoda aitti. Mazbata açıklaması barkodu taşıyor
        # (bkz. _MAZBATA_BARKOD_RE); her tebligat yalnız kendi barkodunun
        # mazbatasını alır (bkz. `_mazbata_bul`, aşağıda evrak döngüsünde).
        mazbata_haritasi, barkodsuz_mazbatalar = _mazbata_haritasi(evraklar)
        tebligat_sayisi = len({e.get("birimEvrakNo") for e in evraklar
                               if e.get("tur") in (HEDEF_TUR, MTS_TEBLIGAT_TUR)})

        def _mazbata_bul(barkod):
            """(mazbata evrakı | None, açıklama). Açıklamasında barkod olmayan TEK
            mazbata yalnız dosyada TEK tebligat varsa ona bağlanır (tahmin yok)."""
            mz = mazbata_haritasi.get(barkod)
            if mz is None and tebligat_sayisi == 1 and len(barkodsuz_mazbatalar) == 1:
                mz = barkodsuz_mazbatalar[0]
            return mz, ((mz.get("aciklama") or "Var") if mz else "")

        mazbata_say = len(mazbata_haritasi) + len(barkodsuz_mazbatalar)
        temel["Tebliğ Mazbatası"] = f"{mazbata_say} mazbata (barkoda göre)" if mazbata_say else "Yok"
        log(f"  📎 Tebliğ Mazbatası: {temel['Tebliğ Mazbatası']}")

        ktm_anahtar = _tur_anahtar(KAPALI_TEBLIG_MAZBATASI_TUR)
        ktm_var = any(_tur_anahtar(e.get("tur")) == ktm_anahtar for e in evraklar)
        temel["Kapalı Tebliğ Mazbatası"] = "Var" if ktm_var else "Yok"

        # Yeniden tebliğ (21/2 vb.) talebi zaten gönderilmiş mi — evrak listesi
        # zaten burada elimizde, ekstra istek GEREKMEZ (kullanıcı isteği,
        # 2026-08-13).
        yeniden_teblig_talebi_var = any(e.get("tur") == TALEP_EVRAK_TUR for e in evraklar)
        temel["Yeniden Tebliğ Talebi"] = "Var" if yeniden_teblig_talebi_var else "Yok"

        eslesen = [e for e in evraklar if e.get("tur") in (HEDEF_TUR, MTS_TEBLIGAT_TUR)]
        if not eslesen:
            log(f"  ℹ️ '{HEDEF_TUR}' / '{MTS_TEBLIGAT_TUR}' evrakı yok ({len(evraklar)} evrak tarandı).")
            _ekle(_satir_olustur(temel, Durum=f"ℹ️ '{HEDEF_TUR}' yok"), "atlanan")
            # 2026-09-30: tebligatı olmayan dosyada da künye (dosya durumu —
            # ör. "Durdurulmuş : Takibe İtiraz") yazılır ve kesinleşme
            # hesaplanır.
            if db:
                try:
                    dosya_obj, _ = db["dosya_kunyesi_kaydet"](rec)
                    if taraf_da:
                        _taraflari_tazele(dosya_obj, dosya_id, log)
                    dosya_core.kesinlesme_durumlarini_guncelle(dosya_obj=dosya_obj, log_fn=log)
                except Exception as e:
                    log(f"  ⚠️ Künye/kesinleşme kaydedilemedi: {e}")
            return

        # Dosya başına BİR KEZ çözülür (evrak döngüsünde tekrar tekrar
        # çözülmez — bkz. `_barkodu_veritabanina_kaydet` docstring'i).
        dosya_obj = None
        if db:
            try:
                dosya_obj, _ = db["dosya_kunyesi_kaydet"](rec)
            except Exception as e:
                log(f"  ⚠️ Dosya künyesi kaydedilemedi (DB kaydı bu dosya için atlanacak): {e}")
        if taraf_da and dosya_obj:
            _taraflari_tazele(dosya_obj, dosya_id, log)
        # Borçlu artık EVRAK BAŞINA, açıklamadaki muhataptan çözülür (2026-09-24,
        # bkz. dosya_core.tebligat_borclusunu_bul) — çok borçlulu dosyada da
        # her borçlunun tebligatı kendisine bağlanır; banka/işveren/vekile
        # giden tebligat hiçbir borçluya bağlanmaz.
        dosya_borclulari = []
        if dosya_obj:
            try:
                dosya_borclulari = list(dosya_obj.taraf_baglari.filter(rol="borclu").select_related("taraf"))
            except Exception:
                dosya_borclulari = []

        # ASLA GERİYE DÜŞMEZ (kullanıcı bulgusu, 2026-08-17: "gönderdim ama
        # Yeniden Tebliğ Talebi durumu Var'a dönmedi" — az önce GERÇEKTEN
        # gönderilmiş (bkz. teblig_21_2_core.dosyalari_gonderildi_isaretle,
        # finalize()'ın KESİN başarı yanıtına dayanır) bir dosyada, BU tarama
        # yeniden_teblig_talebi_var'ı BURADA (satır ~944) UYAP'ın evrak
        # listesinden yeniden hesaplarken, o evrak UYAP'ın listesine henüz
        # YANSIMAMIŞ olabilir — "şu an evrak listesinde YOK" (False) "hiç
        # gönderilmedi" ANLAMINA GELMEZ. Aşağıdaki _barkodu_veritabanina_
        # kaydet çağrıları bu alanı defaults= ile HER taramada YENİDEN YAZAR
        # (update_or_create), yani bir SONRAKİ taramanın yanlış-negatif
        # vermesi, az önceki DOĞRU "gönderildi" kaydını SESSİZCE siliyordu.
        # Burada: veritabanında BU dosya için ZATEN True olan bir kayıt varsa
        # canlı sonuç False çıksa bile geriye düşürülmez.
        if db and dosya_obj and not yeniden_teblig_talebi_var:
            try:
                if db["TebligatBarkod"].objects.filter(
                        dosya=dosya_obj, yeniden_teblig_talep_edildi=True).exists():
                    yeniden_teblig_talebi_var = True
                    temel["Yeniden Tebliğ Talebi"] = "Var"
            except Exception:
                pass

        # MTS: aynı barkodu taşıyan evrakları birleştirmek için dosyadaki
        # bilinen barkodlar (bkz. MTS_TEBLIGAT_TUR notu).
        mts_var = any(e.get("tur") == MTS_TEBLIGAT_TUR for e in eslesen)
        mts_mukerrer = _mts_mukerrer_oku() if mts_var else set()
        dosya_barkodlari = {}   # barkod -> (birim_evrak_no, tebligat_turu)
        if mts_var and db and dosya_obj:
            try:
                for t in db["TebligatBarkod"].objects.filter(dosya=dosya_obj).exclude(barkod=""):
                    dosya_barkodlari.setdefault(t.barkod, (t.birim_evrak_no, t.tebligat_turu))
            except Exception:
                pass

        for evrak in eslesen:
            evrak_id = evrak.get("evrakId")
            birim_evrak_no = evrak.get("birimEvrakNo")
            aciklama = evrak.get("aciklama", "") or ""
            borclu_obj = dosya_core.tebligat_borclusunu_bul(aciklama, dosya_borclulari)
            mts_evrak = evrak.get("tur") == MTS_TEBLIGAT_TUR
            mukerrer_anahtar = (f"{dosya_obj.pk}:{birim_evrak_no}"
                                if mts_evrak and dosya_obj and birim_evrak_no else None)
            if mukerrer_anahtar and mukerrer_anahtar in mts_mukerrer:
                log(f"  ♻️ MTS evrak {birim_evrak_no}: barkodu bu dosyada başka evrakta kayıtlı — atlandı.")
                continue
            temel_evrak = {**temel, "Evrak Açıklaması": aciklama,
                            "Evrak Tarihi": evrak.get("onaylandigiTarih", "")}
            try:
                bilinen = _bilinen_barkod_kaydini_al(db, dosya_obj, birim_evrak_no)
                # Kullanıcı bulgusu (2026-08-04, YEDİNCİ tur — ÖNEMLİ bug):
                # "barkod SONUCU elinde varsa tekrar sorgulama" kullanıcının
                # kendi isteğiydi, ama önceki kod bunu YALNIZ `bilinen.barkod`
                # dolu mu diye kontrol ederek uyguluyordu — `tebligat_turu`
                # BOŞ kalmış olsa BİLE "sonuç elimde" sayılıp PDF bir daha HİÇ
                # indirilmiyordu. aciklama-only sınıflandırma (`pdf_govde_metni`
                # olmadan çağrılan `_tebligat_turunu_belirle`) çoğu evrakta
                # (Maaş/Banka'nın zarf-içeriği tespiti, "ihtiva eder" yakalama
                # dahil TÜM gövde-metni-gerektiren kurallar) HİÇBİR ZAMAN
                # sonuç üretemez — bu yüzden ilk turda türü boş kalan bir evrak
                # kaç kez "güncelleme" çalıştırılırsa çalıştırılsın SONSUZA DEK
                # boş kalıyordu (dosya "atlanıyormuş" gibi görünüyordu, aslında
                # her turda işleniyordu ama körlemesine — PDF'e hiç bakmadan).
                # Şart artık `bilinen.tebligat_turu` DOLU olmasını da istiyor:
                # tür hâlâ boşsa PDF'i (aşağıdaki "yavaş yol") yeniden indirip
                # gövde metniyle YENİDEN sınıflandırmayı dener; barkod'un
                # kendisi değişmez kuralına aykırı değildir (aynı PDF'ten aynı
                # barkod yeniden çıkar, yalnız fazladan bir istek maliyeti var).
                # 2026-09-24 (kullanıcı: "aynı işi tekrar tekrar yapmasın"): PDF
                # okunduktan sonra tür artık HİÇ boş kalmaz (bulunamazsa
                # "Belirsiz" — bkz. _tebligat_turunu_belirle), yani bir evrakın
                # PDF'i EN FAZLA BİR KEZ indirilir. Türü boş eski kayıtlar
                # (sonucu kesin olsalar da) bir kereliğine okunur.
                if bilinen and bilinen.barkod and bilinen.tebligat_turu:
                    barkod = bilinen.barkod
                    log(f"  ♻️ Barkod+tür zaten biliniyor ({barkod}) — PDF tekrar indirilmedi, PTT durumu tazeleniyor.")
                    tebligat_turu = bilinen.tebligat_turu
                    if tebligat_turu not in BILINEN_TURLER:
                        # Şablon dışı kayıtlı tür sonradan eşleştirildiyse onu kullan (PDF indirmeden).
                        tebligat_turu = tur_eslestirme.eslestir(tebligat_turu) or tebligat_turu
                    if bilinen.elektronik_tebligat and _e_teblig_sonuclandi(
                            bilinen.ptt_durumu, bilinen.son_islem_tarihi):
                        # Tebliğ tarihi zaten kayıtlı — hiçbir şey indirilmez.
                        ozet, tarih, ptt_veri = bilinen.ptt_durumu, bilinen.son_islem_tarihi, None
                        log("  ♻️ E-tebligat tebliğ tarihi kayıtlı — hiçbir şey indirilmedi.")
                    elif bilinen.elektronik_tebligat:
                        # 2026-09-24: mazbata İNDİRİLMEZ — tebliğ = evrak tarihi (süre +5 günden).
                        tarih = e_teblig_teblig_tarihi(evrak.get("onaylandigiTarih") or bilinen.evrak_tarihi)
                        ozet = E_TEBLIG_DURUM_METNI if tarih else "Elektronik Tebligat (evrak tarihi yok)"
                        ptt_veri = None
                    elif ptt_sorgusu and (_ptt_sonuclandi(bilinen.ptt_durumu)
                                          or teblig_sonucu_aciklamada_mi(aciklama)
                                          or _mazbata_bul(barkod)[0] is not None):
                        # 2026-09-24: TESLİM EDİLDİ gibi kesin sonuç; 2026-09-25: ya da
                        # bu barkodun KENDİ mazbatası dönmüş — PTT'ye gidilmez.
                        ptt_veri = dict(_PTT_SONUCLANMIS)
                        ozet, tarih = bilinen.ptt_durumu, bilinen.son_islem_tarihi or ""
                        log(f"  ♻️ PTT sonucu kesin ya da mazbatası dönmüş — tekrar sorgulanmadı.")
                    else:
                        ptt_veri, ozet, tarih = _ptt_adimi(barkod, log, ptt_sorgusu)
                        if not ptt_sorgusu:
                            # yalnız ekranda göstermek için DB'deki son PTT durumu
                            ozet, tarih = bilinen.ptt_durumu or "", bilinen.son_islem_tarihi or ""
                    _ekle(_satir_olustur(
                        temel_evrak, Barkod=barkod, **{"Tebliğ Mazbatası": _mazbata_bul(barkod)[1] or "Yok"},
                        **{"PTT Durumu": ozet, "Son İşlem Tarihi": tarih, "Tebligat Türü": tebligat_turu},
                        Durum=_durum_metni(ptt_veri, "✅ Sorgulandı") + " (barkod önbellekten)",
                        **{"Ham Yanıt": _ham_yanit_metni(ptt_veri)}),
                        "basarili")
                    _barkodu_veritabanina_kaydet(
                        db, dosya_obj, evrak, log,
                        evrak_aciklama=aciklama, evrak_tarihi=str(evrak.get("onaylandigiTarih", "") or ""),
                        barkod=barkod, elektronik_tebligat=bilinen.elektronik_tebligat,
                        ptt_durumu=ozet, son_islem_tarihi=tarih, tebligat_turu=tebligat_turu,
                        ham_yanit=ptt_veri if isinstance(ptt_veri, dict) else None,
                        tebligat_mazbatasi_var=_mazbata_bul(barkod)[0] is not None,
                        tebligat_mazbatasi_aciklama=_mazbata_bul(barkod)[1],
                        kapali_tebligat_mazbatasi_var=ktm_var, borclu=borclu_obj,
                        yeniden_teblig_talep_edildi=yeniden_teblig_talebi_var)
                    continue

                _, pdf_bytes = _evrak_pdf_indir(
                    motor, dosya_id, evrak_id, log_fn=log, istek_sarici=dosya_core._arka_plan_istek)
                if bilinen and bilinen.barkod:
                    # Barkod zaten biliniyor (yalnız tür eksikti, bkz. yukarı)
                    # — "barkod bir kez atanır, değişmez" kuralı gereği YENİDEN
                    # ÇIKARILMAZ, PDF yalnız gövde metni için indirildi.
                    barkod = bilinen.barkod
                    log(f"  ♻️ Barkod zaten biliniyor ({barkod}) — tür için PDF yeniden indirildi.")
                else:
                    barkod = barkod_bul(pdf_bytes, log)
                    if not barkod:
                        _ekle(_satir_olustur(temel_evrak, Durum="⚠️ Barkod bulunamadı"), "atlanan")
                        continue

                # Gövde metni BİR KEZ çıkarılır — tebligat türü sınıflandırması
                # bunu kullanır (bkz. `_tebligat_turunu_belirle`). Kullanıcı
                # bulgusu (2026-08-03, altıncı tur): burada eskiden bir
                # "belge gövdesinde borçlu adı yoksa DB'ye YAZILMAZ" filtresi
                # vardı — KALDIRILDI (bkz. modül başlığı): indirilen PDF genel
                # bir zarf/mazbata şablonu olduğundan bankaya/işverene giden
                # haciz ihbarnamelerinde borçlu adı bu metinde YAPISAL OLARAK
                # hiç geçmiyor, filtre bunları %100 dışlıyordu.
                govde_metni = _pdf_govde_metni(pdf_bytes, log)
                tebligat_turu = _tebligat_turunu_belirle(aciklama, govde_metni)
                if tebligat_turu == "Belirsiz":
                    # Zarf cümlesi yoksa: kendi mazbatasındaki belge adı eşleştirilmişse o tür.
                    tebligat_turu = tur_eslestirme.eslestir(
                        tur_eslestirme._mazbata_belge_adi(_mazbata_bul(barkod)[1])) or tebligat_turu

                if mts_evrak and dosya_obj:
                    diger = dosya_barkodlari.get(barkod)
                    if diger and diger[0] != int(birim_evrak_no):
                        # Aynı barkod bu dosyada başka bir evrakta zaten var —
                        # ikinci satır açılmaz; boş türü varsa bununla tamamlanır.
                        _kat = set(tur_eslestirme.kategoriler())
                        if tebligat_turu in _kat and diger[1] not in _kat:
                            try:
                                db["TebligatBarkod"].objects.filter(
                                    dosya=dosya_obj, birim_evrak_no=diger[0]).update(tebligat_turu=tebligat_turu)
                                dosya_barkodlari[barkod] = (diger[0], tebligat_turu)
                                log(f"  🔗 MTS: {barkod} türü '{tebligat_turu}' olarak tamamlandı (evrak {diger[0]}).")
                            except Exception as e:
                                log(f"  ⚠️ MTS tür tamamlanamadı: {e}")
                        mts_mukerrer.add(mukerrer_anahtar)
                        _mts_mukerrer_yaz(mts_mukerrer)
                        log(f"  ♻️ MTS: {barkod} bu dosyada evrak {diger[0]}'de zaten var — ikinci satır açılmadı.")
                        continue
                    dosya_barkodlari.setdefault(barkod, (int(birim_evrak_no), tebligat_turu))

                log(f"  📮 Barkod: {barkod}" + (f" — Tür: {tebligat_turu}" if tebligat_turu else ""))
                if ELEKTRONIK_ANAHTAR in aciklama:
                    # e-Tebligat PTT'ye çıkmaz. 2026-09-24 kullanıcı kararı:
                    # Kapalı E-Tebliğ Mazbatası ("okundu") İNDİRİLMEZ — tebliğ
                    # tarihi = evrak tarihi (bkz. e_teblig_teblig_tarihi).
                    tarih = e_teblig_teblig_tarihi(evrak.get("onaylandigiTarih"))
                    ozet = E_TEBLIG_DURUM_METNI if tarih else "Elektronik Tebligat (evrak tarihi yok)"
                    log(f"  ✅ {ozet}" + (f" ({tarih})" if tarih else ""))
                    _ekle(_satir_olustur(
                        temel_evrak, Barkod=barkod, **{"Tebliğ Mazbatası": _mazbata_bul(barkod)[1] or "Yok"},
                        **{"PTT Durumu": ozet, "Son İşlem Tarihi": tarih, "Tebligat Türü": tebligat_turu},
                        Durum="ℹ️ Elektronik"), "basarili")
                    _barkodu_veritabanina_kaydet(
                        db, dosya_obj, evrak, log,
                        evrak_aciklama=aciklama, evrak_tarihi=str(evrak.get("onaylandigiTarih", "") or ""),
                        barkod=barkod, elektronik_tebligat=True,
                        ptt_durumu=ozet, son_islem_tarihi=tarih,
                        tebligat_turu=tebligat_turu, ham_yanit=None,
                        tebligat_mazbatasi_var=_mazbata_bul(barkod)[0] is not None,
                        tebligat_mazbatasi_aciklama=_mazbata_bul(barkod)[1],
                        kapali_tebligat_mazbatasi_var=ktm_var, borclu=borclu_obj,
                        yeniden_teblig_talep_edildi=yeniden_teblig_talebi_var)
                    continue

                if (bilinen and _ptt_sonuclandi(bilinen.ptt_durumu)) or \
                        teblig_sonucu_aciklamada_mi(aciklama) or _mazbata_bul(barkod)[0] is not None:
                    # PTT sonucu zaten kesin ya da bu barkodun kendi mazbatası dönmüş — PTT'ye gidilmez.
                    ptt_veri = dict(_PTT_SONUCLANMIS)
                    ozet = (bilinen.ptt_durumu if bilinen else "") or "Mazbata döndü"
                    tarih = (bilinen.son_islem_tarihi if bilinen else "") or ""
                else:
                    ptt_veri, ozet, tarih = _ptt_adimi(barkod, log, ptt_sorgusu)
                _ekle(_satir_olustur(
                    temel_evrak, Barkod=barkod, **{"Tebliğ Mazbatası": _mazbata_bul(barkod)[1] or "Yok"},
                    **{"PTT Durumu": ozet, "Son İşlem Tarihi": tarih, "Tebligat Türü": tebligat_turu},
                    Durum=_durum_metni(ptt_veri, "✅ Sorgulandı"),
                    **{"Ham Yanıt": _ham_yanit_metni(ptt_veri)}), "basarili")
                _barkodu_veritabanina_kaydet(
                    db, dosya_obj, evrak, log,
                    evrak_aciklama=aciklama, evrak_tarihi=str(evrak.get("onaylandigiTarih", "") or ""),
                    barkod=barkod, elektronik_tebligat=False,
                    ptt_durumu=ozet, son_islem_tarihi=tarih, tebligat_turu=tebligat_turu,
                    ham_yanit=ptt_veri if isinstance(ptt_veri, dict) else None,
                    tebligat_mazbatasi_var=_mazbata_bul(barkod)[0] is not None,
                    tebligat_mazbatasi_aciklama=_mazbata_bul(barkod)[1],
                    kapali_tebligat_mazbatasi_var=ktm_var, borclu=borclu_obj,
                    yeniden_teblig_talep_edildi=yeniden_teblig_talebi_var)

            except Exception as e:
                log(f"  ❌ Hata: {e}")
                _ekle(_satir_olustur(temel_evrak, Durum=f"❌ Hata: {e}"), "hatali")

        # Bu dosyanın Ödeme Emri tebligatı yeni işlendiğine göre kesinleşme
        # durumu da tazelenir (bkz. dosya_core.kesinlesme_durumlarini_guncelle
        # docstring'i) — kullanıcı bulgusu, 2026-08-04: kesinleşme sütunu
        # hiçbir akışta hesaplanmıyordu.
        if db and dosya_obj:
            try:
                dosya_core.kesinlesme_durumlarini_guncelle(dosya_obj=dosya_obj, log_fn=log)
            except Exception as e:
                log(f"  ⚠️ Kesinleşme durumu güncellenemedi: {e}")

    except Exception as e:
        log(f"  ❌ Hata: {e}")
        _ekle(_satir_olustur(temel, Durum=f"❌ Hata: {e}"), "hatali")


# Yalnız-PTT turunda sorgulanacak tebligat türleri (None = hepsi). Kalıcı kural
# değil — çağıran (ör. gece_bekci\ptt_turu2.py) o tur için ayarlar. 2026-09-25
# kullanıcı: "Maaş, 103, ödeme barkodlarının PTT sorgusu yapılsın şimdilik."
PTT_TUR_FILTRESI = None


def _yalniz_ptt_dosya_isle(birim_adi, dosya_no, log, _ekle, db, bekle_fn=None):
    """MOD_PTT: UYAP'a HİÇ gitmeden, bu dosya için DB'de kayıtlı (elektronik
    OLMAYAN) barkodları PTT'de sorgular ve yalnız PTT alanlarını günceller.
    Barkodlar ancak bir UYAP turundan (MOD_UYAP/MOD_IKISI) sonra DB'de olur.
    Sonucu kesin barkodlar (bkz. SONUCLANMIS_PTT_DURUMLARI) sorgulanmaz.
    PTT geçici bekleme dönemindeyse (bkz. ptt_tarayici — ardışık hata sonrası
    soğuma) `bekle_fn(saniye)` ile beklenip AYNI barkod yeniden denenir —
    bu modda başka iş olmadığından dosyalar boş yere hatayla geçilmez.
    `bekle_fn` False dönerse (tur durduruldu) dosya yarıda bırakılır."""
    temel = {"Birim": birim_adi, "Dosya No": dosya_no}
    if not db:
        _ekle(_satir_olustur(temel, Durum="⚠️ Veritabanı yok — kayıtlı barkod okunamadı"), "atlanan")
        return
    yil, sira = _dosya_no_ayir(dosya_no)
    if yil is None:
        _ekle(_satir_olustur(temel, Durum="⚠️ Atlandı (dosya no formatı)"), "atlanan")
        return
    kayitlar = list(db["TebligatBarkod"].objects.filter(
        dosya__birim__ad=birim_adi, dosya__yil=yil, dosya__sira_no=sira)
        .exclude(barkod="").order_by("birim_evrak_no"))
    fiziki = [t for t in kayitlar if not t.elektronik_tebligat]
    if not fiziki:
        neden = ("yalnız elektronik tebligat var (PTT'de takip yok)" if kayitlar
                 else "kayıtlı barkod yok — önce 'UYAP'tan Tebligat Bilgisi Çek'")
        log(f"  ℹ️ {neden}")
        _ekle(_satir_olustur(temel, Durum=f"ℹ️ {neden[0].upper()}{neden[1:]}"), "atlanan")
        return
    sorgulandi = False
    for tb in fiziki:
        temel_evrak = {**temel, "Evrak Açıklaması": tb.evrak_aciklama,
                       "Evrak Tarihi": tb.evrak_tarihi, "Tebligat Türü": tb.tebligat_turu}
        mazbata_dondu = mazbatasi_dondu_mu(tb.barkod, tb.evrak_aciklama, tb.tebligat_mazbatasi_var,
                                           tb.tebligat_mazbatasi_aciklama)
        if _ptt_sonuclandi(tb.ptt_durumu) or mazbata_dondu:
            log(f"  ♻️ {tb.barkod} — " + ("mazbatası dönmüş" if mazbata_dondu else f"sonuç kesin ({tb.ptt_durumu})")
                + ", sorgulanmadı.")
            _ekle(_satir_olustur(temel_evrak, Barkod=tb.barkod,
                                 **{"PTT Durumu": tb.ptt_durumu,
                                    "Son İşlem Tarihi": tb.son_islem_tarihi or ""},
                                 Durum=_durum_metni(_PTT_SONUCLANMIS, "")), "atlanan")
            continue
        if PTT_TUR_FILTRESI and tb.tebligat_turu not in PTT_TUR_FILTRESI:
            log(f"  ⏭ {tb.barkod} — tür '{tb.tebligat_turu}' bu turun kapsamı dışında, sorgulanmadı.")
            continue
        log(f"  📮 {tb.barkod}" + (f" — {tb.tebligat_turu}" if tb.tebligat_turu else ""))
        while True:
            ptt_veri, ozet, tarih = _ptt_adimi(tb.barkod, log, True)
            kalan = ptt_veri.get("_beklemede") if isinstance(ptt_veri, dict) else None
            if not kalan or bekle_fn is None:
                break
            log(f"  ⏸ PTT bekleme döneminde — {int(kalan // 60) + 1} dk sonra aynı barkod yeniden denenecek.")
            if not bekle_fn(kalan):
                return
        _ekle(_satir_olustur(temel_evrak, Barkod=tb.barkod,
                             **{"PTT Durumu": ozet, "Son İşlem Tarihi": tarih},
                             Durum=_durum_metni(ptt_veri, "✅ PTT sorgulandı"),
                             **{"Ham Yanıt": _ham_yanit_metni(ptt_veri)}), "basarili")
        _barkodu_veritabanina_kaydet(
            db, tb.dosya, {"birimEvrakNo": tb.birim_evrak_no}, log,
            ptt_durumu=ozet, son_islem_tarihi=tarih,
            ham_yanit=ptt_veri if isinstance(ptt_veri, dict) else None)
        sorgulandi = True
    # 2026-09-25: yalnız-PTT turu kesinleşmeyi HİÇ yeniden hesaplamıyordu —
    # PTT sonucu yazılıyor ama İcra Dosyalarım'da Kesinleşme boş kalıyordu
    # (UYAP turundaki `_dosya_isle` bunu dosya sonunda yapıyor).
    if sorgulandi:
        try:
            dosya_core.kesinlesme_durumlarini_guncelle(dosya_obj=fiziki[0].dosya, log_fn=log)
        except Exception as e:
            log(f"  ⚠️ Kesinleşme durumu güncellenemedi: {e}")


# ── Tur izleme: ilerleme dosyası, dışarıdan durdurma, kaldığı yerden devam ──
# (2026-09-24). Klasör: %LOCALAPPDATA%\UyapMasa\barkod_turu\
#   tur_<pid>_<n>.json  — çalışan her turun canlı durumu (i/N, sayaçlar, son
#                         nabız). Masaüstü, web ve bağımsız betikler aynı yere
#                         yazar; `aktif_turlar()` bunları okur (gece turu da).
#   DURDUR              — bu dosya bir turun BAŞLANGICINDAN sonra oluşturulursa
#                         tur bir sonraki dosya sınırında durur (bkz. `durdurma_iste`).
#   devam_<mod>.json/.txt — yarıda kalan turun listesi + işlenen dosyalar.
#                         Aynı modda büyük ölçüde aynı listeyle yeniden
#                         başlatılırsa işlenenler atlanır; tur bitince silinir.
DEVAM_MIN_DOSYA = 20          # daha küçük seçimler her zaman baştan işlenir
DEVAM_MIN_ORTAKLIK = 0.9      # yeni listenin en az %90'ı eski listede olmalı
DEVAM_MAX_YAS_SN = 7 * 24 * 3600
AKTIF_NABIZ_SN = 600          # bundan eski nabızlı tur "ölü" sayılır
_TUR_SAYACI = [0]


def tur_klasoru():
    kok = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    yol = os.path.join(kok, "UyapMasa", "barkod_turu")
    os.makedirs(yol, exist_ok=True)
    return yol


def _json_atomik_yaz(yol, veri):
    gecici = yol + ".tmp"
    with open(gecici, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=1)
    os.replace(gecici, yol)


def _mts_mukerrer_oku():
    """Barkodu aynı dosyadaki başka bir evrakta zaten kayıtlı MTS evrakları
    ({"<Dosya.pk>:<birimEvrakNo>"}) — PDF'leri bir daha indirilmez."""
    try:
        with open(os.path.join(tur_klasoru(), "mts_mukerrer.json"), encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def _mts_mukerrer_yaz(kume):
    try:
        _json_atomik_yaz(os.path.join(tur_klasoru(), "mts_mukerrer.json"), sorted(kume))
    except OSError:
        pass


def aktif_turlar(max_nabiz_yasi=AKTIF_NABIZ_SN):
    """Şu an süren ya da duraklatılmış barkod turları (tüm süreçler): list[dict]."""
    out = []
    simdi = time.time()
    try:
        adlar = os.listdir(tur_klasoru())
    except OSError:
        return out
    for ad in adlar:
        if not (ad.startswith("tur_") and ad.endswith(".json")):
            continue
        try:
            with open(os.path.join(tur_klasoru(), ad), encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        if d.get("durum") in ("sürüyor", "duraklatıldı") and simdi - d.get("nabiz", 0) <= max_nabiz_yasi:
            out.append(d)
    return out


def durdurma_iste():
    """Çalışan tüm barkod turlarına 'bir sonraki dosya sınırında dur' der."""
    yol = os.path.join(tur_klasoru(), "DURDUR")
    with open(yol, "w", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    return yol


def _yarim_tur_incele(mod, yeni_anahtarlar):
    """Bu mod ve (büyük ölçüde) bu listeyle yarım kalmış tur var mı?
    Döner: (eski kayıt dict | None, işlenmiş anahtar kümesi)."""
    klasor = tur_klasoru()
    devam_json = os.path.join(klasor, f"devam_{mod}.json")
    devam_txt = os.path.join(klasor, f"devam_{mod}.txt")
    if len(yeni_anahtarlar) < DEVAM_MIN_DOSYA or not os.path.exists(devam_json):
        return None, set()
    with open(devam_json, encoding="utf-8") as f:
        eski = json.load(f)
    yas = time.time() - eski.get("zaman", 0)
    eski_liste = set(eski.get("liste") or [])
    ortak = sum(1 for k in yeni_anahtarlar if k in eski_liste) / len(yeni_anahtarlar)
    if yas > DEVAM_MAX_YAS_SN or ortak < DEVAM_MIN_ORTAKLIK or not os.path.exists(devam_txt):
        return None, set()
    with open(devam_txt, encoding="utf-8") as f:
        islenmis = {s.rstrip("\n") for s in f if s.strip()} & set(yeni_anahtarlar)
    return (eski if islenmis else None), islenmis


def _liste_anahtarlari(girdi):
    """Seçili-dosyalar girdisini (dict listesi) calistir'daki anahtarlara çevirir."""
    out = []
    for rec in girdi or []:
        b = str(rec.get("birimAdi") or rec.get("Birim") or "").strip()
        no = str(rec.get("dosyaNo") or rec.get("Dosya No") or "").strip()
        if b and no:
            out.append(f"{b}\t{no}")
    return out


def devam_sor(parent, girdi, mod=MOD_IKISI):
    """Tur başlatılmadan önce (2026-09-24 kullanıcı isteği): bu listeyle yarım
    kalmış bir tur varsa "Kaldığı yerden devam / Baştan başlat" sorar.
    Döner: True (devam — ya da yarım tur yok), False (baştan), None (vazgeçildi)."""
    try:
        eski, islenmis = _yarim_tur_incele(mod, _liste_anahtarlari(girdi))
    except Exception:
        return True
    if not islenmis:
        return True
    from tkinter import messagebox
    toplam = len(_liste_anahtarlari(girdi))
    cevap = messagebox.askyesnocancel(
        "Yarım kalmış tur var",
        f"Bu listeyle {eski.get('baslangic', '?')} tarihinde başlayıp yarım kalmış bir tur var: "
        f"{toplam} dosyadan {len(islenmis)} tanesi işlenmiş, {toplam - len(islenmis)} dosya kaldı.\n\n"
        "EVET — Kaldığı yerden devam et (yalnız kalan dosyalar; işlenmiş dosyalara bir sonraki "
        "tam turda yeniden bakılır)\n"
        "HAYIR — Baştan başlat (bütün dosyalar; yeni mazbata/tebligat hepsinde aranır)\n"
        "İPTAL — Vazgeç", parent=parent)
    return cevap


class _TurIzleyici:
    def __init__(self, mod, satirlar, log, devam=True):
        _TUR_SAYACI[0] += 1
        self.log = log
        self.mod = mod
        self.klasor = tur_klasoru()
        self.yol = os.path.join(self.klasor, f"tur_{os.getpid()}_{_TUR_SAYACI[0]}.json")
        self.baslangic = time.time()
        self._son_yazim = 0.0
        self.durum = {"pid": os.getpid(), "mod": mod, "toplam": len(satirlar),
                      "baslangic": time.strftime("%Y-%m-%d %H:%M:%S"), "i": 0, "dosya": "",
                      "sayac": {}, "durum": "sürüyor", "atlanan_onceki": 0}
        self._devam_json = os.path.join(self.klasor, f"devam_{mod}.json")
        self._devam_txt = os.path.join(self.klasor, f"devam_{mod}.txt")
        self.islenmis = self._devam_hazirla(satirlar, devam)
        self.durum["atlanan_onceki"] = len(self.islenmis)
        self.yaz(zorla=True)

    @staticmethod
    def _anahtar(birim_adi, dosya_no):
        return f"{birim_adi}\t{dosya_no}"

    def _devam_hazirla(self, satirlar, devam):
        """Önceki yarım turun işlenmiş dosyalarını döner (atlanacak küme) ve
        bu turun devam kaydını hazırlar."""
        yeni = [self._anahtar(b, n) for b, n in satirlar]
        islenmis = set()
        try:
            if devam:
                eski, islenmis = _yarim_tur_incele(self.mod, yeni)
                if islenmis:
                    self.log(f"⏩ Kaldığı yerden devam: önceki yarım turda ({eski.get('baslangic', '?')}) "
                             f"işlenmiş {len(islenmis)} dosya atlanacak, {len(yeni) - len(islenmis)} "
                             f"dosya işlenecek.")
                    return islenmis
        except Exception as e:
            self.log(f"⚠️ Önceki tur kaydı okunamadı, baştan başlanıyor: {e}")
            islenmis = set()
        try:
            _json_atomik_yaz(self._devam_json, {"zaman": time.time(), "baslangic": self.durum["baslangic"],
                                                "mod": self.mod, "liste": yeni})
            open(self._devam_txt, "w", encoding="utf-8").close()
        except OSError as e:
            self.log(f"⚠️ Devam kaydı oluşturulamadı (tur yine çalışır): {e}")
        return islenmis

    def islendi(self, birim_adi, dosya_no):
        try:
            with open(self._devam_txt, "a", encoding="utf-8") as f:
                f.write(self._anahtar(birim_adi, dosya_no) + "\n")
        except OSError:
            pass

    def yaz(self, zorla=False, **alanlar):
        self.durum.update(alanlar)
        simdi = time.time()
        if not zorla and simdi - self._son_yazim < 5:
            return
        self._son_yazim = simdi
        self.durum["nabiz"] = simdi
        self.durum["nabiz_saat"] = time.strftime("%H:%M:%S")
        try:
            _json_atomik_yaz(self.yol, self.durum)
        except OSError:
            pass

    def durdurma_istendi(self):
        try:
            return os.path.getmtime(os.path.join(self.klasor, "DURDUR")) >= self.baslangic
        except OSError:
            return False

    def bitir(self, durum):
        """Turu kapatır. 'tamamlandı' ise devam kaydı silinir; aksi halde
        (durduruldu/hata) kalır ki sonraki başlatma kaldığı yerden sürsün."""
        self.yaz(zorla=True, durum=durum, bitis=time.strftime("%Y-%m-%d %H:%M:%S"))
        try:
            _json_atomik_yaz(os.path.join(self.klasor, "son_tur.json"), self.durum)
            os.remove(self.yol)
        except OSError:
            pass
        if durum == "tamamlandı":
            for yol in (self._devam_json, self._devam_txt):
                try:
                    os.remove(yol)
                except OSError:
                    pass


def calistir(girdi, log_fn=None, kontrol=None, excel=True, mod=MOD_IKISI, devam=True, taraf_da=False):
    """Ana giriş noktası — üç modu destekler:
      * Excel modu: `girdi` bir dosya yolu (str) — Excel'deki (Birim, Dosya No)
        satırları okunur (bkz. `excel_oku`).
      * Tarih aralığı modu: `girdi` bir sözlük ({"birim_adi","baslangic","bitis"}
        — bkz. PARAMETRELER) — UYAP'ın "Dosya Sorgulama" ekranındaki tarih
        aralığı filtresiyle AYNI şekilde o aralıkta açılmış dosyalar listelenir
        (bkz. `_tarih_araligi_dosyalari_getir`); dosyaId zaten yanıtta geldiğinden
        AYRICA search_phrase_detayli.ajx ile çözülmez.
      * Seçili dosyalar modu: `girdi` bir liste — UyapMasa'nın "Dosyalarım (Tümü)"
        tarzı yerel DB listesinden (bkz. dosya_core.dosyalarim_db_listele)
        kullanıcının seçtiği satırlar, her biri en az {"birimAdi","dosyaNo"}
        içeren bir sözlük. BİLEREK `rec_bilinen` OLUŞTURULMAZ: bu sözlükler
        UYAP'ın ham search_phrase_detayli.ajx kaydıyla AYNI BİÇİMDE DEĞİL
        (ör. birimTuru1/2/3 yok, tarih alanı başka ad taşıyor) — `_dosya_isle`'ye
        böyle geçirilirse `dosya_kunyesi_kaydet` var olan doğru
        Birim.turu1/2/3 ve Dosya.acilis_tarihi alanlarını SESSİZCE boşaltırdı;
        bu yüzden her satır Excel modundaki gibi `_dosya_id_coz` ile YENİDEN
        çözülür (aşağıda `onceden_cozulmus` bu modda hiç doldurulmaz).
    Her modda da her dosya için 'Kapalı Tebligat' evrak(lar)ının barkodu
    bulunup PTT'de sorgulanır. Döner: satır başına sözlük listesi (UyapMasa'da
    tablo olarak gösterilir); işlem bitince bir .xlsx çıktısı da üretilir
    (bkz. `_sonucu_excele_yaz`). `excel=False` ise .xlsx ÜRETİLMEZ — gece tam
    güncelleme turu (dosya_core.guncelleme_hatti) sonuçları yalnız DB'ye
    yazar, her gece binlerce satırlık bir Excel biriktirmez.

    `mod` (2026-09-23): MOD_UYAP = yalnız UYAP tebligat verisi (PTT'ye
    gidilmez, DB'deki PTT durumu korunur); MOD_PTT = yalnız DB'deki fiziki
    barkodların PTT sorgusu (UYAP'a gidilmez; tarih aralığı girdisi yine
    UYAP'tan dosya listesi ister); MOD_IKISI = ikisi birden (varsayılan).

    `devam` (2026-09-24): True ise yarıda kalmış (Durdur/çökme/hata) aynı
    moddaki büyük bir turun işlenmiş dosyaları atlanır (bkz. `_TurIzleyici`).
    İlerleme `tur_klasoru()` altındaki JSON'a yazılır; `durdurma_iste()` ile
    (ya da `python barkod_sorgu.py --durdur`) dışarıdan durdurulabilir.

    `taraf_da` (2026-09-30, "Seçilenleri UYAP'tan Güncelle"): her dosyada
    taraflar da UYAP'tan tazelenir (dosya başına +1 istek). Toplu turlar
    (Barkod'u da Güncelle, gece) VERMEZ — yalnız kullanıcının seçtiği dosyalar."""
    log = log_fn or print
    if mod not in MODLAR:
        raise ValueError(f"Bilinmeyen mod: {mod!r}")
    log({MOD_UYAP: "📋 Mod: yalnız UYAP tebligat verisi (PTT sorgulanmayacak)",
         MOD_PTT: "📮 Mod: yalnız PTT barkod sorgusu (UYAP'a gidilmeyecek)",
         MOD_IKISI: "🔄 Mod: UYAP tebligat verisi + PTT barkod sorgusu"}[mod])
    if mod != MOD_PTT or isinstance(girdi, dict):
        icra_core.birim_listesi_getir(log)  # birim adı -> id önbelleğini doldur
    motor = SorguMotoru(log)
    db = _db_baglantisini_kur(log)  # None ise DB kaydı sessizce atlanır

    onceden_cozulmus = {}  # (birim, dosya_no) -> ham UYAP kaydı — yalnız tarih aralığı modunda dolu
    if isinstance(girdi, list):
        satirlar = []
        for rec in girdi:
            b = str(rec.get("birimAdi") or rec.get("Birim") or "").strip()
            no = str(rec.get("dosyaNo") or rec.get("Dosya No") or "").strip()
            if b and no:
                satirlar.append((b, no))
        log(f"🗂️ {len(satirlar)} seçili dosya işlenecek")
        taban_ad = "Barkod_Sorgu_Secili_Dosyalar"
        kaynak_klasoru = None
    elif isinstance(girdi, dict):
        birim_adi_g = (girdi.get("birim_adi") or "").strip()
        baslangic = (girdi.get("baslangic") or "").strip()
        bitis = (girdi.get("bitis") or "").strip()
        kayitlar = _tarih_araligi_dosyalari_getir(motor, birim_adi_g, baslangic, bitis, log)
        satirlar = []
        for rec in kayitlar:
            dosya_id = rec.get("dosyaId")
            dosya_no = rec.get("dosyaNo")
            if not dosya_id or not dosya_no:
                continue
            b = rec.get("birimAdi") or rec.get("birim_adi") or birim_adi_g
            anahtar = (b, str(dosya_no))
            satirlar.append(anahtar)
            onceden_cozulmus[anahtar] = rec
        taban_ad = f"Barkod_Sorgu_{birim_adi_g or 'TarihAraligi'}".replace(" ", "_")
        kaynak_klasoru = None
    else:
        satirlar = excel_oku(girdi)
        log(f"📄 {len(satirlar)} satır okundu")
        taban_ad = os.path.splitext(os.path.basename(girdi))[0]
        kaynak_klasoru = os.path.dirname(os.path.abspath(girdi))

    sonuclar = []
    sayac = {"basarili": 0, "atlanan": 0, "hatali": 0}

    def _ekle(satir, anahtar):
        sayac[anahtar] += 1
        sonuclar.append(satir)

    # Dışarıdan durdurma (DURDUR dosyası) kontrol nesnesi üzerinden işler —
    # çağıran vermediyse (bağımsız betik/CLI) yerel bir tane kurulur.
    if kontrol is None:
        import toplu_is_kontrol
        kontrol = toplu_is_kontrol.TopluIsKontrolu(ad="Barkod Sorgu (bağımsız)")
    izleyici = _TurIzleyici(mod, satirlar, log, devam=devam)

    def _nabiz_ve_dis_durdurma():
        # nokta() duraklatılmışken de bunu çağırır → duraklatılmış tur da
        # "aktif" görünür (gece turu çakışmasın) ve dışarıdan durdurulabilir.
        izleyici.yaz(durum="duraklatıldı" if kontrol.paused else "sürüyor", sayac=dict(sayac))
        if izleyici.durdurma_istendi() and not kontrol.durduruldu:
            log("⏹ Dışarıdan durdurma isteği (DURDUR dosyası) alındı.")
            kontrol.durdur()

    def _bekle(saniye):
        """Durdurulabilir bekleme; tur durdurulduysa False."""
        bitis = time.time() + saniye
        while time.time() < bitis:
            _nabiz_ve_dis_durdurma()
            if not kontrol.nokta(_nabiz_ve_dis_durdurma):
                return False
            time.sleep(min(2.0, max(0.0, bitis - time.time())))
        return True

    son_durum = "tamamlandı"
    yavas_kalan = 0
    ardisik_hatali = 0
    # Tur boyunca TEK PTT tarayıcısı (ilk PTT sorgusunda açılır, tur bitince
    # kapanır — bkz. ptt_tarayici.ptt_oturumu). Yalnız-PTT modunda bekleme
    # dönemi burada (`_bekle`) karşılanır; diğer modlarda UYAP işi sürer.
    try:
        with ptt_tarayici.ptt_oturumu(log):
            for i, (birim_adi, dosya_no) in enumerate(satirlar, 1):
                # Önceki yarım turda işlenmiş dosya: karma sırasına/duraklatmaya
                # girmeden atlanır (binlerce dosyada sıra beklemesin).
                if izleyici._anahtar(birim_adi, dosya_no) in izleyici.islenmis:
                    continue
                kontrol.tur_bitti()
                _nabiz_ve_dis_durdurma()
                if not kontrol.nokta(_nabiz_ve_dis_durdurma):
                    log("⏹ Durduruldu. Kaldığı yer kaydedildi — aynı listeyle yeniden "
                        "başlatınca işlenmiş dosyalar atlanır.")
                    son_durum = "durduruldu"
                    break
                log(f"\n▶ [{i}/{len(satirlar)}] {birim_adi} {dosya_no}")
                izleyici.yaz(zorla=True, i=i, dosya=f"{birim_adi} {dosya_no}", sayac=dict(sayac))
                hatali_once, basarili_once = sayac["hatali"], sayac["basarili"]
                if mod == MOD_PTT:
                    # UYAP'a gidilmediği için dosya arası bekleme yok; PTT'nin
                    # kendi insan-hızı beklemesi ptt_tarayici'da.
                    _yalniz_ptt_dosya_isle(birim_adi, dosya_no, log, _ekle, db, bekle_fn=_bekle)
                    if kontrol.durduruldu:
                        continue   # dosya yarıda kaldı — işlenmiş SAYILMAZ; döngü başı durdurur
                else:
                    red_once = dosya_core.ESZAMANLI_RED_SAYISI
                    _dosya_isle(motor, birim_adi, dosya_no, log, _ekle, db,
                                onceden_cozulmus.get((birim_adi, dosya_no)),
                                ptt_sorgusu=(mod == MOD_IKISI), taraf_da=taraf_da)
                    if dosya_core.ESZAMANLI_RED_SAYISI > red_once:
                        if not yavas_kalan:
                            log(f"  🐢 UYAP 'eşzamanlı sorgulama' reddi görüldü — sonraki "
                                f"{YAVAS_MOD_DOSYA} dosyada dosya arası {YAVAS_MOD_SN:g} sn beklenecek.")
                        yavas_kalan = YAVAS_MOD_DOSYA
                # Yalnız hatayla biten dosya "işlenmiş" sayılmaz — devam
                # edilince yeniden denenir.
                yalniz_hata = sayac["hatali"] > hatali_once and sayac["basarili"] == basarili_once
                if yalniz_hata:
                    ardisik_hatali += 1
                else:
                    ardisik_hatali = 0
                    izleyici.islendi(birim_adi, dosya_no)
                if mod != MOD_PTT and ardisik_hatali >= ARDISIK_HATALI_DOSYA_SINIRI:
                    log(f"\n⛔ Üst üste {ardisik_hatali} dosya hatayla bitti — UYAP bağlantısı/oturumu "
                        "yok gibi görünüyor (UYAP Bağlantısı ekranını kontrol edin). Tur durduruldu; "
                        "kaldığı yer kaydedildi, aynı listeyle yeniden başlatınca devam eder.")
                    son_durum = "hata: ardışık UYAP hatası"
                    break
                if mod != MOD_PTT:
                    bekle = YAVAS_MOD_SN if yavas_kalan else SATIR_ARASI_SN
                    yavas_kalan = max(0, yavas_kalan - 1)
                    if bekle:
                        time.sleep(bekle)
    except BaseException:
        son_durum = "hata"
        raise
    finally:
        izleyici.bitir(son_durum if son_durum != "tamamlandı" or not kontrol.durduruldu else "durduruldu")

    log(f"\n{'=' * 60}\n"
        f"✅ İşlenen: {sayac['basarili']}   ⚠️ Atlanan: {sayac['atlanan']}   ❌ Hatalı: {sayac['hatali']}"
        + (f"   ⏩ Önceki turda işlenmiş (atlandı): {len(izleyici.islenmis)}" if izleyici.islenmis else ""))

    cikti = _SonucListesi(sonuclar)
    if sonuclar and excel:
        try:
            dosya_ad, veri = _sonucu_excele_yaz(sonuclar, taban_ad, kaynak_klasoru, log)
            cikti.dosya_ad = dosya_ad
            cikti.dosya_b64 = base64.b64encode(veri).decode("ascii")
        except Exception as e:
            log(f"  ⚠️ Excel çıktısı üretilemedi: {e}")

    return cikti


def excel_isle(excel_yolu, log_fn=print, kontrol=None):
    """UyapMasa runner'ının (EXCEL_GIRDI) çağırdığı giriş noktası — calistir()
    ile birebir aynı, yalnızca imza runner'ın çağrı biçimiyle uyumlu."""
    return calistir(excel_yolu, log_fn, kontrol=kontrol)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    # Dışarıdan izleme/durdurma (2026-09-24):
    #   python barkod_sorgu.py --durum    → çalışan turlar + son biten tur
    #   python barkod_sorgu.py --durdur   → çalışan turları bir sonraki dosyada durdur
    if len(sys.argv) > 1 and sys.argv[1] in ("--durum", "--durdur"):
        if sys.argv[1] == "--durdur":
            print(f"Durdurma isteği yazıldı: {durdurma_iste()}")
        turlar = aktif_turlar()
        if not turlar:
            print("Çalışan barkod turu yok.")
        for t in turlar:
            s = t.get("sayac") or {}
            print(f"[{t.get('durum')}] mod={t.get('mod')} pid={t.get('pid')} "
                  f"{t.get('i')}/{t.get('toplam')} — {t.get('dosya')} | "
                  f"✅{s.get('basarili', 0)} ⚠️{s.get('atlanan', 0)} ❌{s.get('hatali', 0)} | "
                  f"başlangıç {t.get('baslangic')}, son nabız {t.get('nabiz_saat')}")
        try:
            with open(os.path.join(tur_klasoru(), "son_tur.json"), encoding="utf-8") as f:
                son = json.load(f)
            print(f"Son biten tur: {son.get('durum')} — mod={son.get('mod')} "
                  f"{son.get('i')}/{son.get('toplam')}, {son.get('baslangic')} → {son.get('bitis')}")
        except (OSError, ValueError):
            pass
        sys.exit(0)
    _varsayilan_xlsx = os.path.normpath(os.path.join(_HERE, "..", "..", "Dosya Sorgulama.xlsx"))
    _xlsx = sys.argv[1] if len(sys.argv) > 1 else _varsayilan_xlsx
    for _satir in calistir(_xlsx, log_fn=print):
        print(_satir)
