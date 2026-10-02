# Veritabanı Bulguları — Genel / Ortak (tüm yargı türleri)

Tarih: 2026-09-22
Durum: Bu oturumda (kullanıcı: "veritabanının tamamen oluşması için gereken
aksiyonları uyapta al... tam hakimiyet olsun istiyorum") Chrome ile canlı
UYAP oturumunda (avukat.uyap.gov.tr, kullanıcı ZEYNEL SAVAŞ) yapılan geniş
taramanın bulguları. **Metodoloji:** hiçbir alan/model UYDURULMADI; her
model/alan, hangi somut dosyada (birim+dosya no) hangi tarihte canlı
görüldüğü belirtilerek eklendi. Bir bağlamda (yargı türü/dosya türü) görülen
bir yapının BAŞKA bir bağlamda da geçerli olduğu ASLA varsayılmadı — her biri
ayrı ayrı doğrulanmadan "ortak" sayılmaz (kullanıcı talimatı, 2026-09-22).

Kardeş dosyalar (her biri kendi yargı türünün özelini anlatır):
- [VERITABANI_BULGULARI_01_ICRA.md](VERITABANI_BULGULARI_01_ICRA.md) — en
  detaylı, otomasyon için en kritik yargı türü (kullanıcı vurgusu).
- [VERITABANI_BULGULARI_02_CEZA.md](VERITABANI_BULGULARI_02_CEZA.md)
- [VERITABANI_BULGULARI_03_HUKUK.md](VERITABANI_BULGULARI_03_HUKUK.md)
- [VERITABANI_BULGULARI_04_IDARI_YARGI.md](VERITABANI_BULGULARI_04_IDARI_YARGI.md)
- [VERITABANI_BULGULARI_05_SATIS_MEMURLUGU.md](VERITABANI_BULGULARI_05_SATIS_MEMURLUGU.md)
- [VERITABANI_BULGULARI_06_CBS.md](VERITABANI_BULGULARI_06_CBS.md)
- [VERITABANI_BULGULARI_07_ARABULUCULUK_TAZMINAT.md](VERITABANI_BULGULARI_07_ARABULUCULUK_TAZMINAT.md)

Model kodu: `models/icra_models/models.py`. İlgili migration'lar:
`0020`–`0024` (bkz. her migration dosyasının başlığı). Önceki/temel plan:
[CIOK_YARGI_TURU_SENKRON_PLANI.md](CIOK_YARGI_TURU_SENKRON_PLANI.md)
(2026-07-10, bu oturumdan ÖNCEki mimari kararlar — `Birim.yargi_turu`,
`IcraTakipDetay`, `HukukDavaDetay`'in kökeni orada).

## 1. `YargiTuru` — sabit enum (8 tür)

`yargiBirimleriSorgula_brd.ajx`'i besleyen ön yüz dropdown'ında sabit 8
seçenek var (büyümez):

| Kod | Yargı Türü | Bu oturumda dosya bulundu mu |
|---|---|---|
| 0 | Ceza | Evet — 11 birimin 6'sında |
| 1 | Hukuk | Evet — 12 birimin 6'sında |
| 2 | İcra | Evet — tek birim (İCRA DAİRESİ), binlerce dosya |
| 3 | Cbs | Evet (önceki tur) — yalnız İzmir Cumhuriyet Başsavcılığı test edildi |
| 6 | İdari Yargı | Evet — yalnız İdare Mahkemesi (Bölge İdare/Vergi'de dosya yok) |
| 11 | Satış Memurluğu | Evet (önceki tur) |
| 25 | Arabuluculuk | HAYIR — kullanıcının hiç dosyası yok (test edildi) |
| 26 | Tazminat Komisyonu Başkanlığı | HAYIR — kullanıcının hiç dosyası yok (test edildi) |

`Dosya.yargi_turu` bu kodu DOĞRUDAN dosya satırında tutar (önceden yalnız
`Birim.yargi_turu` üzerinden dolaylıydı — çarpışma riskini azaltmak için bu
oturumda eklendi, migration 0020).

## 2. Dosyanın "kimliği" hakkında genel kurallar

- `dosyaId`: OTURUMLUK, her sorguda değişebilir. Kimlik DEĞİLDİR.
- KALICI doğal anahtar: `(birim, yıl, sıra_no, tür_kod)` — `uq_dosya_kunye`.
- `dosyaTurKod`/`dosyaDurumKod`: UYAP'ın ham kodu, OLDUĞU GİBİ saklanır.
  Görüntüleme için `Dosya.Tur`/`Dosya.Durum` `IntegerChoices`'a yalnız CANLI
  görülen kodlar eklenir. **Önemli keşif (2026-09-22):** aynı `dosyaDurumKod`
  (ör. 0) FARKLI serbest metin (`dosyaDurum`) taşıyabilir — örn. İcra'da
  "Açık" ile "Açık (Durdurulmuş : Takibe İtiraz)" AYNI kod (0), farklı metin.
  Bu yüzden `Dosya.durum` (serbest metin CharField) alanı KESİNLİKLE gerekli
  — yalnız `durum_kod`'a güvenmek bu nüansı kaybeder.
- `dosyaTurKod` DEĞERLERİ (bu oturumda genişletildi, `Dosya.Tur`):
  0=Esas, 1=Talimat, 3=Ceza Dava Dosyası, 14=Hukuk Değişik İş Dosyası,
  15=Hukuk Dava Dosyası, 16=Cbs Soruşturma Dosyası, **35=İcra Dosyası**
  (şaşırtıcı biçimde şimdiye dek hiç eklenmemişti, İcra'nın ANA kodu).
- `dosyaDurumKod` DEĞERLERİ (bu oturumda genişletildi, `Dosya.Durum`):
  0=Açık, 1=Kapalı, 3=Yargıtayda, 7=Karara Çıkmış, 16=Yargıtaydan Döndü,
  29=İstinafta, 31=İstinaftan Kısmi Döndü.

## 3. Her (yargı türü, dosya türü) çifti için kontrol edilecek soru listesi

Yeni bir dosya türüyle karşılaşıldığında şu sıra izlenir (bu oturumda
kurulan yöntem):

1. `dosya_islem_turleri_sorgula_brd.ajx` çağrısı `{dosyaTurKod: "anahtar,
   virgüllü,liste"}` döner — hangi sekmelerin (`ayrinti_bilgileri`,
   `taraf_bilgileri`, `evrak_bilgileri`, `safahat_bilgileri`,
   `tahsilat_reddiyat_bilgileri`, `hesap_bilgileri` [yalnız İcra'da
   görüldü], `borclu_bilgileri` [yalnız İcra'da görüldü], `evrak_gonderme`,
   `odeme`, `kisisel_not_alani`) o dosyada MEVCUT olduğunun TEK güvenilir
   kaynağıdır — sekme listesi TAHMİN EDİLMEZ, bu uç noktadan okunur.
2. Mevcutsa `dosyaAyrintiBilgileri_brd.ajx` (aile-özel ayrıntı alanları).
3. `dosya_taraf_bilgileri_brd.ajx` (taraf rolleri — yeni rol görülürse
   `DosyaTaraf.Rol`'e eklenir, `tr_lower()` ile üretilen kod kullanılır).
4. `dosya_safahat_bilgileri_brd.ajx` (işlem kronolojisi — bkz. §4).
5. `dosya_tahsilat_reddiyat_bilgileri_brd.ajx` (mali defter — bkz. §5).
6. Yalnız İcra'da: `dosya_hesap_bilgileri.ajx` (canlı bakiye, bkz. İcra
   dosyası §3) ve "Borçlu Bilgileri" malvarlığı sorgu modülü.

## 4. `DosyaSafahat` — dosyanın işlem geçmişi (genel model, ayrı ayrı doğrulandı)

`dosya_safahat_bilgileri_brd.ajx` yanıtı: `{"safahatlar":[{"islemYapanBirim",
"safahatTarihi","safahatTarihiSTR","safahatTuruKod","safahatTuruAciklama",
"aciklama","safahatStatuKod","safahatStatuKodAciklama","personel"}, ...]}`.

CANLI DOĞRULANDI (AYRI AYRI, bkz. `models.py` `DosyaSafahat` docstring'i):
- **Ceza** (İcra Ceza Hakimliği, dosya 2026/78)
- **Hukuk** (Asliye Hukuk Mahkemesi, dosya 2026/117)
- **İcra** (İcra Dairesi, dosya 2026/21475) — **ÜCRETLİ/KOTALI!** Bu sekme
  her açıldığında UYAP'ın "Sorgu Bakiyeniz"/"Kalan Ücretsiz Sorgu Limiti"
  sayacı azalıyor (canlı örnekte 5/5 → 4/5). Otomatik/toplu senkron
  yazılırken bu sekme SINIRSIZ çağrılmamalı.

YOK olduğu doğrulanan bağlamlar: Ceza/Bölge Adliye Mah. Ceza Dairesi
(istinaf), Ceza Talimat Dosyası, Satış Memurluğu, Cbs Soruşturma Dosyası,
Hukuk Değişik İş Dosyası (bkz. ilgili dosyalar).

## 5. `DosyaTahsilatHarc` + `DosyaReddiyat` — mali defter (genel model)

`dosya_tahsilat_reddiyat_bilgileri_brd.ajx` yanıtı: `{"tahsilatList":[...],
"harcList":[... AYNI alan kümesi ...], "reddiyatList":[...], "toplamTahsilat",
"toplamreddiyat","haricen","toplamTeminat","toplamTahsilHarci","toplamKalan",
"isIcraMi"}`. `tahsilatList`/`harcList` birebir aynı alan kümesi → TEK model
(`DosyaTahsilatHarc`, `tur` ayrımıyla), `kayitId` kalıcı anahtar. `reddiyatList`
farklı alan kümesi (vergi alanları) → ayrı model (`DosyaReddiyat`).

CANLI DOĞRULANDI: **Ceza** (İcra Ceza Hakimliği, 2026/78), **Hukuk** (Asliye
Hukuk, 2026/117). **İcra'da bu sekme AYRICA açılıp doğrulanmadı** (yalnız
Safahat logundaki "Harç Masraf Tahsilatı" satırlarından dolaylı iz var) —
AÇIK bir doğrulama boşluğu.

## 6. Evrak / belge indirme

`list_dosya_evraklar.ajx` (liste) + `view_document_brd.uyap` (GET,
`evrakId`+`dosyaId` — dosyaId ARAMA ADIMINDAN çözülen, evrak kaydının kendi
`dosyaId`'si DEĞİL) — bu mekanizma zaten `UyapMasa/modules/dosya_core.py`
`evrak_html_indir()`'de birebir uygulanmış durumda. Bu oturumda İcra'da
gerçek bir belge (PTT MTS Tebligat Belgesi) indirilerek doğrulandı — YENİ
bir mekanizma çıkmadı, mevcut kod doğru.

## 7. Taraf rolleri — bu oturumda eklenen yeniler

`DosyaTaraf.Rol`'e eklendi (her biri hangi bağlamda görüldüğü ile):
- `KATILAN` ("Katılan") — Ceza (mağdur/şikayetçi, "Ceza Dava Dosyası").
- `VASI` ("Vasi") — Satış Memurluğu (kısıtlının yasal temsilcisi).
- `HACIZ_ALACAKLISI` ("Haciz Alacaklısı") — Satış Memurluğu (Alacaklı'dan
  AYRI bir rol).

Ayrıca: "Sanık" rolünün bir Kurum (tüzel kişi) olarak da görülebildiği
doğrulandı (İcra Ceza Hakimliği örneği) — zaten var olan `Taraf.Tur` alanı
bunu karşılıyor, şema değişikliği gerekmedi.

## 8. Kapsam Haritası — hangi model UÇTAN UCA çalışıyor, hangisi yalnız şema

Bu bölüm 2026-09-22'de `models.py` + `ingest.py` + `UyapMasa/modules/*.py`'nin
TAMAMI (grep ile: `from icra_models`, `update_or_create`, `_django_hazirla`)
taranarak çıkarıldı. Amaç: bir modelin var OLMASI ile o modelin CANLI UYAP
verisiyle DOLUYOR olması AYNI ŞEY DEĞİL — bu fark burada netleştirilir.

### 8.1 Veritabanına dokunan modüller (TAM liste, grep ile doğrulandı)

Yalnız **5 modül** `icra_models` içe aktarıyor / DB'ye yazıyor:
`dosya_core.py` (ana senkron motoru), `icra_core.py` (İcra'nın ayrı/eski
akışı), `barkod_sorgu.py` (`TebligatBarkod`), `teblig_21_2_core.py` (yalnız
`TebligatBarkod` OKUYOR, yazmıyor), `evrak_indirici.py` (yalnız `Birim`
okuyor). Diğer tüm modüller (`kesinlesme_core.py`, `mts_takip.py`,
`sgk_core.py`, `hukuk_dosyalarim.py`, `potek_takip.py`, `xml_takip.py`,
`senkron_kapsami.py`, vb.) DB modellerini KENDİLERİ içe aktarmıyor — Django
sınıflarını/nesnelerini `dosya_core.py`'den parametre olarak alıyorlar
(bkz. ör. `_borclu_kesinlesme_hesapla(dt, tatiller, bugun, TebligatBarkod,
DosyaTarafCls, kc)`) — bu, projenin bilinçli bağımlılık-enjeksiyonu deseni,
eksiklik değil.

`dosya_core.py`'nin genel tarama motoru (`DosyaSorgu`/`SenkronKapsami`
görev listesi) `yargi_turu`'yu görev listesinden DİNAMİK alıp
`dosya_kunyesi_kaydet(rec, yargi_turu=...)`'a geçiyor — yani Hukuk, Ceza,
İdari Yargı, Satış Memurluğu, Cbs, Arabuluculuk, Tazminat Komisyonu bu genel
motordan geçen HER modülde (`hukuk_dosyalarim.py` dahil) otomatik doğru
`Dosya.yargi_turu` alıyor. `icra_core.py`'deki tek satırlık ek yalnız
İcra'nın KENDİ AYRI/eski akışı için gerekliydi (bu genel motoru
kullanmıyor).

### 8.2 Model → ingest durumu tablosu

| Model | Ingest kodu | Durum |
|---|---|---|
| `Birim`, `Dosya` (kapak künyesi) | `ingest.dosya_kunyesi_kaydet()` | ✅ UÇTAN UCA — tüm yargı türlerinde çalışıyor |
| `Taraf`, `Vekil`, `DosyaTaraf` | `dosya_core.py` (~satır 1122+) | ✅ UÇTAN UCA |
| `TebligatBarkod` | `barkod_sorgu.py` | ✅ UÇTAN UCA |
| `Evrak` | `dosya_core.evrak_html_indir()` | ✅ UÇTAN UCA |
| `IcraTakipDetay` | `dosya_core.dosya_ayrinti_kaydet()` | 🟡 KISMİ — yalnız `takibin_turu/sekli/yolu` (kod), `alacak_kalemi_toplam`, `vekalet_ucreti`, `tahsil_harci` yazılıyor. Bu oturumda eklenen `bsmv_ucreti`/`kdv`/`kkdf_ucreti`/`ozel_iletisim_vergisi` VE `alacak_kalemi_faiz`/`takip_sonrasi_masraf`/`yapilmis_tahsilat` HENÜZ yazılmıyor (migration var, ingest satırı yok) |
| `HukukDavaDetay` | `dosya_core.dosya_ayrinti_kaydet()` | 🟡 KISMİ — `dava_acilis_turu`, `dava_turleri`, `ilgili_dava_listesi`, `durusma_tarihi` yazılıyor; `ilgili_dosya_listesi`/`ilgili_seri_dava_listesi`/`birlesen_dosya_listesi`/`basvuruya_birakilma_tarihi` BİLEREK yazılmıyor (UYAP alan biçimi canlı doğrulanmadan DB'ye tahmini yazılmaz — proje disiplini, eksiklik değil) |
| `IdariYargiDetay` | — | ⚪ YOK — yalnız şema/migration (0021). Model docstring'i bunu zaten itiraf ediyor |
| `DosyaSafahat` | — | ⚪ YOK — yalnız şema/migration (0022). Ayrıca İcra'da ÜCRETLİ/KOTALI olduğu unutulmamalı — ileride ingest yazılırsa rate-limit/önbellek GEREKİR |
| `DosyaTahsilatHarc` | — | ⚪ YOK — yalnız şema/migration (0022) |
| `DosyaReddiyat` | — | ⚪ YOK — yalnız şema/migration (0022) |
| `IcraDosyaHesabi` | — | ⚪ YOK — yalnız şema/migration (0024) |
| `BorcluSorgu` | — | ⚪ YOK — yalnız şema/migration (0024) |

### 8.3 Sonuç

Migration zinciri (`0001`→`0024`) DB şemasıyla `models.py` birebir tutarlı
(bu oturumda `makemigrations --check --dry-run` ile doğrulandı). Ama kod
tarafında net bir sınır var: **künye + taraf + (kısmi) aile-detay senkronize
oluyor; bu oturumda eklenen 6 modelin (İdari Yargı Detayı, Safahat,
Tahsilat/Harç, Reddiyat, Dosya Hesabı, Borçlu Sorgu) hiçbiri henüz canlı
UYAP'tan otomatik doldurulmuyor** — hepsi "hazır kap", ingest kodu YAZILMADI
(kullanıcıdan henüz bu yönde bir talimat da gelmedi). Bu, ileride ingest
kodu yazılacaksa doğal bir öncelik sıralaması sunuyor: önce
`IcraTakipDetay`'ın eksik 7 alanı (küçük, tek fonksiyona ek), sonra
`DosyaTahsilatHarc`/`DosyaReddiyat` (aynı uç nokta, iki liste), en son
`DosyaSafahat`/`IcraDosyaHesabi`/`BorcluSorgu` (her biri ayrı bir ağ
çağrısı gerektirir — Safahat özellikle kotalı olduğu için toplu/otomatik
senkrona SOKMADAN önce bir kullanım stratejisi netleşmeli).
