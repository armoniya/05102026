# Geçici modül: Hukuk dosyalarından gerekçeli kararları yavaş ve düzenli indirme

## Context
Kullanıcı, UYAP'taki açık + kapalı tüm hukuk dosyalarında (tüm hukuk mahkemesi türleri)
gerekçeli karar olup olmadığını kontrol edip varsa **UDF + PDF** olarak indiren, UYAP arayüzü
içinden çalışan **geçici** bir yardımcı istiyor. Amaç: bir insanın tek tek tıklaması yerine
programın kararları makul bir sürede, UYAP sunucularını yormadan indirmesi.

Kararlar (kullanıcı):
- Önce liste çıkar, **mahkeme türleri ve dosyalar işaretlenebilir** (varsayılan hepsi işaretli).
- Hız: **iki UYAP isteği arası sabit 5 sn + her 25 dosyada 60 sn mola**. Rastgele bekleme /
  insan taklidi YOK (mevcut `evrak_toplu.py` ilkesiyle aynı: amaç yükü düşürmek, tespit atlatmak değil).
  Kullanıcının kendi UYAP işlemlerine `batch_write()` zaten öncelik verir.
- Çıktı: ofiste `CokluUyap Evrak\Gerekçeli Kararlar\<Mahkeme> <Esas No>\` altına `.udf` + `.pdf`;
  bitince tümü tek ZIP olarak tarayıcıya iner. Yeniden çalıştırmada önceden indirilen atlanır.
- Captcha atlatılmaz: UYAP doğrulama isterse iş durur, kullanıcı panelde çözer (mevcut akış).

## Yeni modül — `uyap_core/gerekceli_karar.py`
`evrak_toplu.py`'nin altyapısını **yeniden kullanır** (kopyalamaz):
- İstek kapısı: `evrak_toplu._uyap(ind, …)` (hız sınırı, `batch_write`, `uyap_istek_izi`,
  eşzamanlı reddi tekrarı, ağ hatasında geri çekilme, captcha bekleme, relogin). `_uyap` bir
  `Indirme` benzeri nesne bekler → yeni `Tarama` sınıfı `evrak_toplu.Indirme`'den türetilir,
  `ISTEK_ARASI_SN` yerine `aralik()` 5 sn döndürür (`YAVAS_EN_COK_SN` üstü 20 sn).
- Aynı `_sira_kilidi()` kullanılır → Tüm Evrakı İndir ile aynı anda iki toplu iş UYAP'a gitmez.
- `evrak_toplu._evrak_listesi`, `_tur_bul`, `_pdfe_cevir`, `_guvenli_ad`, `_zip_yap`, `KAYIT_KOKU`.

Akış (iki aşama):
1. **Liste** (`liste_baslat`): `yargiBirimleriSorgula_brd.ajx {"yargiTuru":"1"}` → hukuk
   mahkeme türleri (`{"tablo": kod, "kod": ad}`; bkz. `UyapMasa/modules/dosya_core.py:191`).
   Her tür × durum (0 Açık, 1 Kapalı) için `search_phrase_detayli.ajx`
   `{"dosyaDurumKod", "pageSize":500, "pageNumber", "birimTuru2": kod, "birimTuru3":"1"}`
   (bkz. `dosya_core.build_payload_genel`), sayfa < 500 kayıt dönene kadar. Kayıt ayrıştırma
   `icra_core.parse_records` mantığıyla (veri[0] liste). Sonuç: türlere göre gruplu dosya
   listesi (dosyaId, birimAdi, dosyaNo, durum) → panele.
2. **Tarama** (`tarama_baslat(secilen_dosyalar)`): her dosya için `_evrak_listesi` →
   `tur` alanı `tr_lower` ile "gerekçeli karar" / "gerekceli karar" içeren, "tebliğ",
   "mazbata", "zarf", "şerh" içermeyen evraklar. Her biri için `getDocViewerParameters.ajx` +
   `view_document_brd.uyap` → ham bayt orijinal uzantısıyla (`.udf`) ve `_pdfe_cevir` ile `.pdf`.
   Her 25 dosyada 60 sn mola (iptal/captcha kontrolü sürer). Ağ hatası art arda 3 dosyada → durur
   (`ARDISIK_AG_HATASI_SINIRI`).
- **Kaldığı yer**: `KAYIT_KOKU\Gerekçeli Kararlar\_durum.json` → kontrol edilen dosyaId +
   indirilen evrakId. Yeniden çalıştırmada indirilen evrak atlanır; kontrol edilip kararı
   olmayan dosyalar 7 gün sonra yeniden kontrol edilir (karar sonradan gelmiş olabilir).
- Durum sözlüğü: aşama (liste/tarama/mola/captcha/bitti), i/toplam dosya, bulunan karar,
  uyarılar, tahmini kalan süre, `dosya_hazir` (ZIP). Rapor: `_rapor.csv` (dosya, karar var/yok,
  dosya adları).

## Uç noktalar — `uyap_core/jobs.py`
`TUM_EVRAK_PREFIX` deseniyle `GEREKCELI_PREFIX = AGENT_PREFIX + "/gerekceli"`:
- `POST /gerekceli/liste` → `{tarama}`; `GET /gerekceli/<id>` durum; `POST /<id>/baslat
  {"dosyalar":[dosyaId…]}`; `/<id>/captcha`, `/<id>/iptal`; `GET /<id>/dosya` → ZIP.
- `is_control_path` ve `handle_control`'a eklenir; seçilen dosyaId'ler sunucuda listeyle
  doğrulanır (yalnız liste aşamasında dönenler kabul edilir).

## UYAP arayüzü — `uyap_core/uyap_proxy.py` (enjekte script)
"Tüm Evrakı İndir" panelinin yanına yeni IIFE: sayfada küçük **"Gerekçeli Kararlar"** düğmesi
(İş Listesi düğmesinin yanında) → panel:
- "Dosyaları listele" → ilerleme → mahkeme türü başlıkları altında işaret kutulu dosyalar
  (tür/hepsi seç-kaldır, Açık/Kapalı süzgeci, sayaç, tahmini süre).
- "Taramayı başlat" → ilerleme, bulunan kararlar, mola sayacı, Durdur; captcha görünümü ve
  indirme `dosyaIndir` ile aynı **fetch+blob** yöntemiyle (telefonda da çalışsın).
- Geçici modül: tek blokta, kolay kaldırılır.

## Testler — `tests/test_gerekceli_karar.py` (unittest, ağ yok)
- `tur` süzgeci (Gerekçeli Karar ✓, "Gerekçeli Karar Tebliğ Mazbatası" ✗, büyük/küçük harf, i/İ).
- Arama yanıtı ayrıştırma + sayfalama durması (sahte `_uyap`).
- `_durum.json` ile atlama ve 7 gün kuralı; seçilmeyen dosyaId reddi.
- Mola: 25 dosyada bir `asyncio.sleep(60)` çağrısı (sleep yamalanır).

## Doğrulama
- `PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python -m unittest tests.test_gerekceli_karar tests.test_zs`
- Enjekte script için `node --check`.
- Canlı (kullanıcıyla, ofis oturumunda): önce yalnız **liste** aşaması — dönen tür/dosya
  sayılarının UYAP Dosya Sorgulama ile tutması (UYAP yılsız aramada kayıt sınırlıyorsa yıl
  döngüsüne geçilir); sonra 2–3 dosya işaretleyip tarama: `.udf` açılıyor mu, `.pdf` doğru mu,
  aralıkların 5 sn olduğu ofis konsolundaki istek zamanlarından görülür.
