# Veritabanı Bulguları — İcra

Tarih: 2026-09-22 · Genel/ortak modeller için bkz.
[VERITABANI_BULGULARI_00_GENEL.md](VERITABANI_BULGULARI_00_GENEL.md).

**Kullanıcı vurgusu:** "Otomasyon için en önemli kısım İcra aslında." Bu
yüzden bu dosya diğerlerinden daha detaylı. **Ayrıca kullanıcı uyardı:**
İcra'da (Kambiyo Senedi, Rehin, Tahliye, İflas gibi) çok daha fazla takip
türü var ve bunları KENDİSİ ayrı ayrı anlatacak — bu dosyadaki bulgular
YALNIZ şimdiye dek CANLI görülenleri kapsar, TAM LİSTE DEĞİLDİR.

## 1. Yargı Birimi

İcra yargı türünün tek Yargı Birimi seçeneği var: **İCRA DAİRESİ**
(`yargiBirimleriSorgula_brd.ajx` başka hiçbir seçenek döndürmüyor). Ama bu
TEK "Yargı Birimi" kategorisinin ALTINDA, gerçek UYAP birimleri (mahkemeler/
daireler) İSİM olarak çok çeşitleniyor — bkz. §2.

## 2. Aynı "Yargı Birimi" altında niteliksel olarak FARKLI icra daireleri

**Kullanıcı sorusu/uyarısı (2026-09-22):** "İlamlı/İlamsız takip dışında,
ilamsız takiplerde merkezi takip sistemi, banka alacakları, abonelik
sözleşmeleri icra dairesi gibi nitelik itibariyle farklı takipler var."

Bu oturumda arama sırasında görülen farklı birim ADLARI (hepsi aynı
`birimTuru2="1101"` kodunu taşıyor — yani UYAP'ın kendi `birimTuru`
sınıflandırmasında AYNI "İcra Dairesi" tipi, ayrım yalnız birim ADINDA):

- **"İzmir Abonelik Sözleşmeleri İcra Dairesi"** — telekom/abonelik
  alacakları için özelleşmiş, kullanıcının dosyalarının BÜYÜK ÇOĞUNLUĞU
  burada (6500+ dosyanın çoğu bu birimde, hepsi "İlamsız Takip / Genel
  Haciz Yoluyla Takip" — bkz. §3).
- **"İzmir Banka Alacakları İcra Dairesi"** — banka alacakları için
  özelleşmiş. Test edilen örnekte (2026/120272) YİNE "İlamsız Takip / Genel
  Haciz Yoluyla Takip" çıktı — yani birim adı farklı olsa da UYAP'ın kendi
  Tür/Yolu/Şekli alanları AYNI kalabiliyor.
- **Numaralı genel daireler** ("İzmir 1. İcra Dairesi", "İzmir 4. İcra
  Dairesi", "Karşıyaka 2. İcra Dairesi", "Bandırma 3. İcra Dairesi" vb.) —
  şehir/ilçe bazlı genel icra daireleri, İlamlı VE İlamsız takip ikisi de
  burada görülebiliyor.
- **"Merkezi Takip Sistemi" (MTS)** — bir dosyanın kendisi DEĞİL, dosyanın
  AÇILDIĞI kaynak sistem/aşama. Safahat kaydında `islemYapanBirim` alanı
  "Merkezi Takip Sistemi" olarak görülüyor (bkz. §5) — MTS dosyayı
  oluşturuyor, sonra gerçek bir icra dairesine "tevzi" (dağıtım/atama)
  ediyor. `UyapMasa` tarafında zaten `modules/mts_takip.py` ve
  `Dosya Açılış/MTS Takip Açılış/*.py` bu AÇILIŞ sürecini yönetiyor (bkz.
  §6) — bu doküman o mevcut kodu TEKRARLAMAZ, yalnız DB modeliyle ilişkisini
  not eder.

**Açık soru — KISMEN ÇÖZÜLDÜ (2026-09-22, internet araştırmasıyla, bkz. §9):**
Bu ayrım UYAP'ın kendi verisinde ayrı bir ALAN/KOD olarak mı geliyor, yoksa
yalnızca **icra dairesinin adından mı (`Birim.ad` serbest metni)**
anlaşılıyor? Test edilen 2 örnekte (Abonelik Sözleşmeleri, Banka Alacakları)
`birimTuru2` AYNI ("1101") çıkmıştı. İnternet araştırması bunun NEDEN böyle
olduğunu (mevzuat düzeyinde) doğruladı: **her ikisi de yapısal olarak
sıradan bir icra dairesidir, ayrım UYAP kodunda değil, hangi TAKİP TÜRLERİNE
bakacaklarını belirleyen İKİ AYRI/BAĞIMSIZ kanuni rejimdedir** (7155→MTS
zorunlu abonelik takibi vs. 7343→ihtisas/banka daireleri) — ayrıntı için
§9. Bu, `Birim.ad` metin eşleştirmesinin (§ aşağıdaki not) aslında
TAHMİNİ değil, MEVZUATA DAYALI güvenilir bir sınıflandırma temeli
olabileceği anlamına gelir — ama şema/choices'a yine de kullanıcının kendi
takip-türü anlatımları tamamlanmadan YAZILMADI (ihtiyat kararı korunuyor).

**Şema notu:** `Birim.ad` zaten bu bilgiyi (birimin tam adını) OLDUĞU GİBİ
saklıyor — yeni bir alan eklemeden, `Birim.ad` üzerinde metin eşleştirmesiyle
(ör. "Abonelik Sözleşmeleri", "Banka Alacakları", "Merkezi Takip" gibi
anahtar kelimeler) bir "icra dairesi niteliği" sınıflandırması YAPILABİLİR
— ama bu TAHMİNİ bir sınıflandırma kuralı olur, kullanıcının kendi
anlatımı beklenmeden ŞEMAYA/CHOICES'A yazılmadı.

## 3. `IcraTakipDetay` — dosya ayrıntısı (`dosyaAyrintiBilgileri_brd.ajx`)

CANLI DOĞRULANAN 2 takip TÜRÜ (ikisi de AYNI JSON alan kümesini paylaşıyor,
model TEK kalıyor):

| Alan | İlamsız Takip örneği (2026/21475) | İlamlı Takip örneği (2018/15855) |
|---|---|---|
| `takibinTuru` / açıklama | "1" / İlamsız Takip | "0" / İlamlı Takip |
| `takibinYolu` / açıklama | "0" / Genel Haciz Yoluyla Takip | "1" / İlamların İcrası, Para ve Teminat Verilmesi Hakkındaki İlamların İcrası |
| `takibinSekli` / açıklama | "0" / ÖRNEK: 7 İlamsız Takiplerde Ödeme Emri - Eski No: 49 | "0" / ÖRNEK: 4-5 Para Borcuna Veya Teminat Verilmesine Veya Bir İşin Yapılması Veya Yapılmamasına, İrtifak Hakkının Kaldırılmasına Veya Yükletilmesine Dair İcra Emri - Eski No: 53 |
| `alacakKalemToplamTutar`, `alacakKalemFaizTutar`, `takipSonrasiMasraf`, `vekaletUcreti`, `tahsilHarci`, `yapilmisBorcTahsilati` | değerli | değerli |
| `bsmvUcreti`, `kdv`, `kkdfUcreti`, `ozelIletisimVergisi` | **anahtarlar YOK** | **anahtarlar VAR (0 değerinde)** |

Son satır önemli: bu 4 vergi/ücret alanı İlamsız örnekte hiç yoktu, İlamlı
örnekte vardı (sıfır da olsa). Model bu 4 alanı EKLEDİ (`default=0`),
ingest bunları `rec.get(...)` ile opsiyonel okumalı.

`Dosya.durum` serbest metninde İcra'ya özgü yeni bir değer görüldü: **"Açık
(Durdurulmuş : Takibe İtiraz)"** — `dosyaDurumKod` YİNE 0 (düz "Açık" ile
aynı kod), yalnız açıklama metni farklı.

## 4. YENİ SEKME — "Dosya Hesabı" (`IcraDosyaHesabi` modeli)

`dosya_hesap_bilgileri.ajx` — UYAP'ın GÜNCEL/CANLI bakiye hesabı. Canlı
örnek (dosya 2026/21475):

```json
[{"textAlan":"Takipte Kesinlesen Miktar","degerAlan":3762.9,"grupId":1},
 {"textAlan":"Basvurma Harci","grupId":1},
 {"textAlan":"Toplam Faiz Miktari","degerAlan":73.76,"grupId":1},
 {"textAlan":"Vekalet Ücreti","degerAlan":3762.9,"grupId":1},
 {"textAlan":"Masraf Miktari","degerAlan":1153.2,"grupId":1},
 {"textAlan":"BSMV Miktari","grupId":1},
 {"textAlan":"KKDF Miktari","grupId":1},
 {"textAlan":"KDV Miktari","grupId":1},
 {"textAlan":"Tahsil Harcı","degerAlan":171.21,"grupId":1},
 {"textAlan":"Özel Iletisim Vergisi","grupId":1},
 {"textAlan":"Toplam Alacak","degerAlan":8923.97,"grupId":1},
 {"textAlan":"Yatan Para","degerAlan":0,"grupId":2},
 {"textAlan":"Bakiye Borç Miktari","degerAlan":8923.97,"grupId":3}]
```

Dikkat: bazı kalemlerin (`Basvurma Harci`, `BSMV`, `KKDF`, `KDV`, `Özel
İletişim Vergisi`) `degerAlan`'ı SIFIR bile değil, HİÇ YOK — bu yüzden model
sabit `DecimalField` kolonları DEĞİL, esnek (etiket, değer, grup) satır
listesi olarak tasarlandı (`IcraDosyaHesabi`, `uq=(dosya,metin_alan,
grup_id)`). Bu değerler ZAMANLA DEĞİŞİR (İcraTakipDetay'daki statik
ayrıntının aksine) — her yeniden sorguda upsert edilir.

Yalnız Ceza/Hukuk'ta bu sekme HİÇ görülmedi — İcra'ya özgü olabilir ama bu
da kanıtlanmadı, varsayılmadı.

## 5. YENİ SEKME — "Borçlu Bilgileri" = Malvarlığı Sorgu Modülü (`BorcluSorgu`)

Otomasyon için EN DEĞERLİ bulgu. "Borçlu Bilgileri" sekmesi bir borçlu
seçtirir, sonra iki alt sekme sunar: **"Sorgular"** ve **"Talep Gönder"**
(bu ikincisi HİÇ incelenmedi — muhtemelen haciz/tebligat talebi gönderme,
bir EYLEM/action formu olabilir, veri listesi olmayabilir — doğrulanmadı).

"Sorgular" alt sekmesinde 9 kutu var: **MERSİS, GİB, SGK** (+ alt sorgu
"Kamu Çalışanı" gibi), **EGM-TNB, Posta Çeki, TAKBİS, SGK Haciz, İcra
Dosyası, Banka**. Her biri `borclu_bilgileri_goruntule_<tur>.ajx` uç
noktasına gidiyor.

CANLI DOĞRULANAN 3 tür (dosya 2026/21475, borçlu bir TÜZEL KİŞİ — Limited
Şirket):

- **MERSİS** (`borclu_bilgileri_goruntule_mersis.ajx`): `sorguSonucDVO` DÜZ
  METİN bir adres — `"30 AĞUSTOS MAH. 7122 SK. EGEM DENİZCİLER SİTESİ NO: 7
  CB- MENEMEN / İZMİR"`.
- **GİB** (`borclu_bilgileri_goruntule_gib_adres.ajx`): `sorguSonucDVO`
  YAPILANDIRILMIŞ bir nesne — `{"caddeSokak","daireNo","ilKodu","ilceAdi",
  "kapiNo","mahalleSemt"}`.
- **SGK "Kamu Çalışanı"** (`borclu_bilgileri_goruntule_sgk_
  kamuCalisaniBilgileri.ajx`): tüzel kişide ANLAMSIZ — `sorguSonucDVO` bir
  HATA AÇIKLAMA metni ("...gerekli değerlerden bir veya birkaçı mevcut
  değil..."), `sorguSonucu:false`.

**Kalan 6 tür (EGM-TNB, Posta Çeki, TAKBİS, SGK Haciz, İcra Dosyası, Banka)
GERÇEK KİŞİ bir borçlu üzerinde HENÜZ TEST EDİLMEDİ** — kullanıcı bunları
ayrı ayrı anlatacak. Yanıt ZARFI (`borcluBilgileri`, `sorgulayanBilgileri`,
`dosyaOzetDVO`, `sorguTarihi`, `sorgulamaTuru`, `sorguSonucu`, `hasError`,
`hasData`) tüm görülen türlerde AYNI ama `sorguSonucDVO` HER TÜRDE FARKLI
şekil alıyor — bu yüzden `BorcluSorgu.sonuc` bir `JSONField`, sabit
kolonlara ZORLANMADI.

## 6. Safahat loğunda MTS'in kendi izi (açılış → tevzi → kapanış sırası)

Aynı dosyanın (2026/21475) Safahat kaydı, kronolojik olarak (eskiden
yeniye), MTS'ten gerçek icra dairesine geçiş sürecini GÖSTERİYOR:

1. `safahatTuruKod:5127` "Dosyanın Açılması" — İzmir Abonelik Sözleşmeleri
   İcra Dairesi.
2. `safahatTuruKod:7` "Dosya Açıldı ve Alt Dosyalarda Gerekli İşlemler
   Gerçekleştirildi" — **işlemYapanBirim: "Merkezi Takip Sistemi"** —
   açıklama: *"MTS Birimindeki 2026/981616 Nolu Dosya Tevzi Edilerek Bu
   Dosya Açılmıştır."* (Not: MTS'nin KENDİ dosya numarası — 2026/981616 —
   nihai icra dairesindeki 2026/21475'ten FARKLI; MTS bir ÜST/kaynak dosya
   numarası taşıyor.)
3. `safahatTuruKod:5113` "Alacak Kalemleri Girişi" / `5279` "Diğer Alacak
   Kalemleri Girişi" — işlemYapanBirim: "Merkezi Takip Sistemi".
4. `safahatTuruKod:103` "Harç Masraf Tahsilatı" (x2 — Başvurma Harcı +
   Tahsil Harcı, ayrı satırlar) — işlemYapanBirim: "Merkezi Takip Sistemi".
5. `safahatTuruKod:5427` "Dosya Kesinleşme Bilgi Girişi" — işlemYapanBirim:
   gerçek icra dairesi (Abonelik Sözleşmeleri).
6. `safahatTuruKod:58` "Dosya Alt Dosya Olarak Kaydedildi" — *"[ Merkezi
   Takip Sistemi ]'nin, [ 2026/981616 ], [ MTS alt dosya olarak
   kaydedildi ]"* — işlemYapanBirim: "Merkezi Takip Sistemi".

**Yorum (kullanıcının "açılış/açılış sonrası/eylemler/kapanış farklı"
tespitiyle örtüşüyor):** MTS dosyaları için `islemYapanBirim` alanı bazı
satırlarda "Merkezi Takip Sistemi" (MTS'in kendi otomatik işlemleri: toplu
açılış, alacak kalemi girişi, harç tahsilatı), bazı satırlarda gerçek icra
dairesi (kesinleşme girişi gibi el ile/dairede yapılan işlemler) olarak
DEĞİŞİYOR — yani AYNI dosyanın Safahat kaydı içinde bile "kim/hangi sistem
yaptı" bilgisi iki farklı kaynak arasında geçiş yapıyor. `DosyaSafahat.
islem_yapan_birim` alanı bunu zaten OLDUĞU GİBİ saklıyor (serbest metin) —
şema değişikliği gerekmedi, ama bu ayrımın İCRA dosyalarını (özellikle MTS
kökenli olanları) ANLAMAK için önemli bir okuma ipucu olduğu not edilir.

**Kapanış süreci HENÜZ canlı gözlemlenmedi** (test edilen dosya hâlâ Açık) —
kullanıcının "dosya kapanışı farklı" dediği kısım açık bir doğrulama.

## 7. Mevcut MTS kodu (bu oturumda YENİDEN YAZILMADI, yalnız referans)

`Dosya Açılış/MTS Takip Açılış/` altında zaten çalışan bir Playwright
otomasyonu var (dosya AÇMA yönünde, bu dokümanın konusu olan OKUMA/veritabanı
yönünün TERSİ):

- `mts_veri.py` — saf veri modelleri: `Takip` (dosya_no, alacakli, iban,
  abone_no, ilamsiz_tutar, aciklama, fatura_tarihi, odeme_tarihi,
  hizmet_abone_no, borclular, alacak_kalemleri), `Borclu` (ad, soyad,
  kimlik), `AlacakKalemi` (ad, tutar, faiz_oran, faiz_tur).
- `mts_donusum.py` — XML/Excel ↔ bu veri modelleri dönüşümü.
- `mts_takip_acan.py` / `mts_takip_acan_api.py` — Playwright ile UYAP web
  arayüzünü sürüp gerçek MTS takibi AÇAN akış (`KontrolDurumu`,
  `TakipDurduruldu`, `DosyaAtla` gibi kontrol sınıfları).
- `mts_pencere.py`, `mts_indirme.py`, `mts_evrak_yukle.py` — pencere
  yönetimi, indirme yakalama, evrak yükleme yardımcıları.

Bu modüller "MTS'e nasıl toplu dosya açılır" sorusunu ZATEN çözüyor; bu
dokümanın/oturumun konusu ise "açılmış bir MTS/İcra dosyası UYAP'tan nasıl
OKUNUP veritabanına yazılır" — iki yön birbirini TAMAMLIYOR, biri diğerinin
yerine geçmiyor.

## 8. Kalan iş / açık uçlar (İcra)

- Kambiyo Senedi, Rehin, Tahliye, İflas gibi diğer takip türleri — kullanıcı
  ayrı ayrı anlatacak.
- "Talep Gönder" alt sekmesi (Borçlu Bilgileri içinde) hiç incelenmedi.
- Kalan 6 malvarlığı sorgu türü (EGM-TNB, Posta Çeki, TAKBİS, SGK Haciz,
  İcra Dosyası, Banka) gerçek kişi borçluda test edilmedi.
- Tah./Red. Bilgileri sekmesi İcra için ayrıca (Ceza/Hukuk'tan bağımsız)
  doğrulanmadı.
- Dosya KAPANIŞ sürecinin Safahat/veri izi henüz gözlemlenmedi (§9.2'deki
  mevzuata göre haciz talebi 5 yıl içinde yapılmazsa dosya "düşer" — bu
  UYAP'ta CANLI gözlemlenmedi, yalnız mevzuat metninden biliniyor).
- "İcra dairesi niteliği" ayrımı artık mevzuat düzeyinde AÇIKLANDI (§9.3,
  §9.4) — ama UYAP'ın kendi verisinde bunu doğrulayan AYRI bir alan/kod
  hâlâ görülmedi (yalnız `Birim.ad` metninden çıkarım). Kullanıcının takip
  türlerini ayrı ayrı anlatması hâlâ bekleniyor.
- **Talimat dosyası mekanizması** (§9.5) İcra bağlamında HENÜZ canlı
  görülmedi — `Dosya.Tur.TALIMAT=1` yalnız Ceza'da doğrulanmıştı. İcra'da
  gerçek bir talimat dosyası (ör. Gayrimenkul Satış İcra Müdürlüğü'ne
  yazılmış) bulunursa yapısının Ceza'daki gibi sığ mı yoksa farklı mı
  olduğu AYRI doğrulanmalı — varsayılmaz.
- **Gayrimenkul Satış İcra Dairesi** hiç canlı test edilmedi — kullanıcının
  dosyalarında böyle bir birim var mı henüz bilinmiyor.
- **İtirazla durdurulan dosyalar** (§9.6) — "Açık (Durdurulmuş: Takibe
  İtiraz)" durumundaki bir dosyanın Safahat kaydında itiraz olayının nasıl
  göründüğü (hangi `safahatTuruKod`), itirazın iptali/kaldırılması davası
  SONUCUNUN (devam/düşme) dosyaya nasıl yansıdığı HENÜZ canlı doğrulanmadı.
- **Örnek No:13/14 (Tahliye)** ve **Örnek No:8/10 (Rehin/Kambiyo Senedi)**
  takip türleri İcra bağlamında HENÜZ canlı görülmedi — kullanıcı bunları
  ayrı ayrı anlatacak, §9.4/§9.6'daki mevzuat bilgisi yalnızca NE
  ARANACAĞINI biliyor olmamızı sağlıyor, canlı doğrulamanın YERİNE geçmez.
- **§9.7'deki tüm taksonomi** (Örnek 2/3 ilamlı teslim-tahliye-çocuk,
  Örnek 9 ipotek, Örnek 11/12 iflas, İhtiyati Haciz) HENÜZ hiçbiri İcra
  bağlamında canlı görülmedi — yalnız NE ARANACAĞINI gösteren bir harita,
  kullanıcının kendi anlatımıyla/canlı taramayla tek tek doğrulanmalı.

## 9. Yasal çerçeve — MTS/Abonelik ve Banka Alacakları rejimlerinin kaynağı
(internet araştırması, 2026-09-22 — kullanıcı isteği: "telekomünikasyon
abonelik alacakları merkezi takip sisteminde takip açılıyor, kapsamlı
araştırma yap")

Bu bölüm UYAP'tan CANLI GÖZLEMLENMİŞ bir bulgu DEĞİL — dış kaynaklı (resmi
mevzuat + hukuk pratiği siteleri) araştırmadır, §1-8'deki "yalnız canlı
görülen yazılır" kuralından İSTİSNAdır ve öyle işaretlenir. Amaç: yukarıdaki
Safahat izinin (§6) HANGİ KANUNİ SÜRECE karşılık geldiğini ve "icra dairesi
niteliği" sorusunun (§2) kökenini açıklamak.

### 9.1 MTS'nin yasal temeli

- **7155 sayılı Kanun** (RG 19.12.2018/30630) — "Abonelik Sözleşmesinden
  Kaynaklanan Para Alacaklarına İlişkin Takibin Başlatılması Usulü Hakkında
  Kanun". 6502 sayılı Tüketicinin Korunması Hakkında Kanun kapsamındaki
  abonelik sözleşmelerinden (elektrik, su, doğalgaz, **internet, telefon**)
  doğan para alacaklarının **YALNIZCA** UYAP içindeki Merkezi Takip Sistemi
  (MTS) üzerinden, **yalnızca alacaklının avukatı eliyle**, ilamsız haciz
  yoluyla takip edilebilmesini ZORUNLU kılar — genel icra yoluyla açılmaya
  çalışılırsa MTS reddeder.
- **Uygulama Yönetmeliği** (RG 29.05.2019/30788) — süreç detaylarını
  düzenler. MTS fiilen 01.06.2019'da devreye girdi; yürürlükten ÖNCEki
  derdest takiplere uygulanmaz (onlarda 2004 sayılı İİK hükümleri geçerli
  kalır).

### 9.2 Süreç akışı (Safahat izindeki §6 ile birebir örtüşüyor)

1. **Takip açılışı** — avukat MTS'de talebi doldurur (alacaklı/borçlu/avukat,
   IBAN, alacak+faiz, dayanak belgeler, **yetkili icra dairesi bilgisi**).
2. **Ödeme emri** — sistem otomatik düzenler, PTT/elektronik tebliğ; toplam
   borç + MTS harcı, **7 gün** ödeme/itiraz süresi.
3. **İtiraz (7 gün)** — borca/kısmına/**yetkiye** itiraz edilebilir; herhangi
   bir icra dairesinden VEYA e-imzayla doğrudan MTS'den yapılabilir. İtiraz,
   sisteme yüklendiği günü izleyen **5. günün sonunda** alacaklı avukatına
   tebliğ edilmiş SAYILIR. Yalnız yetkiye itiraz takibi DURDURMAZ.
4. **Kesinleşme** — süresinde itiraz yoksa (veya yetki itirazı icra
   mahkemesince kaldırılırsa) kesinleşir; **yeniden ödeme emri tebliğine
   gerek kalmadan** mahkemenin yetkili gördüğü icra dairesinde haciz talep
   edilebilir — Safahat'taki §6.5 "Dosya Kesinleşme Bilgi Girişi" satırı
   tam olarak bu adıma karşılık geliyor.
5. **Haciz talebi süresi** — ödeme emri tebliğinden itibaren **5 yıl**
   içinde haciz talep edilmezse dosya DÜŞER (itiraz davası süresi bu
   hesaba dahil değil) — muhtemel "dosya kapanışı" yollarından biri,
   HENÜZ CANLI GÖZLEMLENMEDİ.
6. **Harç** — icraya başvurma harcı VE tahsil harcı ALINMAZ, yalnızca
   (daha düşük) **MTS harcı** peşin alınır; ödeme icra dairesine değil
   MTS'ye bağlı özel hesaba yapılır. Safahat'taki §6.4 "Harç Masraf
   Tahsilatı" (Başvurma Harcı + Tahsil Harcı, işlemYapanBirim="Merkezi
   Takip Sistemi") bu MTS harcının kaydı olabilir — DOĞRULANMADI, yalnız
   olası bir eşleme.

### 9.3 "İcra dairesi niteliği" ayrımının kökeni — İKİ FARKLI KANUNİ REJİM

- **Abonelik Sözleşmeleri İcra Dairesi ↔ 7155/MTS:** birden fazla icra
  dairesi olan yerlerde adalet komisyonu ya dairelerden BİRİNİ MTS/abonelik
  takiplerine ÖZGÜ yetkilendiriyor (→ birim bu ADI buradan alıyor), ya da
  tevzi sistemiyle tüm dairelere dağıtıyor. Yani bu isim UYAP'ın ayrı bir
  KODU değil, **adalet komisyonunun idari/coğrafi yetkilendirme kararının**
  birim adına yansımasıdır.
- **Banka Alacakları İcra Dairesi ↔ 7343 sayılı Kanun (RG 30.11.2021),
  MTS/7155 İLE İLGİSİ YOK:** İİK'ya "ihtisas icra dairesi" modelini getirdi;
  yoğun iş yükü olan illerde bankaların kredi/kredi kartı alacaklarına özgü
  icra daireleri kurulabiliyor (Adalet Bakanlığı/HSK kararıyla). Bu, canlı
  bulgumuzla (§2 — test edilen Banka Alacakları örneği "İlamsız Takip /
  Genel Haciz Yoluyla Takip" çıkmıştı, yani MTS'in özel akışı DEĞİL,
  sıradan ilamsız takip yolu) TUTARLI: banka alacakları MTS kapsamına hiç
  GİRMİYOR, yalnızca KONU BAZLI ihtisaslaşmadan (7343) geçiyor.
- **Sonuç:** her iki özel birim de yapısal olarak sıradan bir icra dairesi
  (`birimTuru2="1101"` bu yüzden AYNI çıkıyor) — ayrım UYAP veri modelinde
  değil, hangi takip türlerine bakacaklarını belirleyen kanuni/idari
  yetkilendirmede. `Birim.ad` üzerinde ("Abonelik Sözleşmeleri"/"Banka
  Alacakları"/"Merkezi Takip" anahtar kelimeleriyle) bir sınıflandırma
  artık TAHMİNİ değil, MEVZUATA DAYALI bir temele sahip — yine de
  şemaya/choices'a kullanıcının kendi takip-türü anlatımı tamamlanmadan
  YAZILMADI (ihtiyat kararı, §2 sonu).

**Kaynaklar (2026-09-22 tarihli arama):**
[İcra İşleri Dairesi Başkanlığı — MTS](https://iidb.adalet.gov.tr/Home/SayfaDetay/merkezi-takip-sistemi-mts24022026030238) ·
[7155 sayılı Kanun](https://mevzuat.gov.tr/MevzuatMetin/1.5.7155.pdf) ·
[Yönetmelik yayımlanması](https://moral.av.tr/tr/hukuki-haberler/abonelik-sozlesmesinden-kaynaklanan-para-alacaklarina-iliskin-takibin-baslatilmasi-usulu-hakkinda-yonetmelik-yayimlandi-436) ·
[Abonelik/Fatura Borcu İcra Takibi ve İtiraz](https://hukukcularevi.com/abonelik-fatura-borcu-icra-takibi-mts-itiraz/) ·
[Abonelik Sözleşmeleri ve MTS](https://havvakulug.av.tr/abonelik-sozlesmeleri-ve-merkezi-takip-sistemi/) ·
[MTS Nedir — Rehber](https://www.saimdogru.com/yayin/merkezi-takip-sistemi-mts-nedir-abonelik-alacaklarinda-hizli-takip-rehberi) ·
[İstanbul Banka Alacakları İcra Dairesi](https://icra.net.tr/istanbul-banka-alacaklari-icra-dairesi/) ·
[7343 sayılı Kanun değerlendirmesi](https://tbbdergisi.barobirlik.org.tr/m2022-160-2057)

### 9.4 Banka Alacakları İcra Dairesi — ayrıntı (kullanıcı isteği: "taraflardan
biri banka olan dosyalar")

- **Kesin yasal dayanak:** 7343 sayılı Kanun'un 2. maddesiyle 2004 sayılı
  İİK'ya eklenen **Madde 3/a** — iş yoğunluğunun/icra dairesi sayısının fazla
  olduğu illerde Adalet Bakanlığınca, yetki çevresi belirlenerek bir veya
  birden fazla "icra daireleri başkanlığı" kurulabiliyor. Banka Alacakları
  İcra Dairesi bu ihtisas modelinin bir görünümü.
- **Kapsam:** "Alacaklısı banka olan" dosyaların TAMAMI — yalnız kredi/kredi
  kartı değil, taraflardan biri banka olan icra dosyaları genel olarak.
  Pratik kaynaklar bu dairenin işlediği takip TÜRLERİNİ veriyor: **Örnek 7**
  (İlamsız Takip — bizim canlı verimizdeki "Genel Haciz Yoluyla Takip" ile
  aynı), **Örnek 4-5** (İlamlı Takip), **Örnek 8** (Rehinli Alacak Takibi),
  **Örnek 10** (Kambiyo Senetlerine Özgü Haciz Yoluyla Takip). Son ikisi
  kullanıcının "kendim anlatacağım" dediği takip türlerinden **Rehin** ve
  **Kambiyo Senedi** ile örtüşüyor — yani bu türler muhtemelen (henüz canlı
  doğrulanmadı) Banka Alacakları İcra Dairesi'nde de görülebilir.
- **Kuruluş yeri:** İstanbul (Çağlayan + Anadolu; Bakırköy'e de "Abonelik ve
  Banka İcra Dairesi" kurulduğuna dair bir duyuru bulundu), Ankara, İzmir
  gibi iş yükü yüksek illerde — tam il listesi mevzuatta sabit değil, idari
  kararla (Adalet Bakanlığı) belirleniyor, zamanla değişebilir.
- **Küçük şehirlerde (kullanıcı notu, 2026-09-22):** Banka Alacakları İcra
  Dairesi yoksa banka takipleri sıradan/numaralı genel icra dairesinde de
  açılabiliyor — ihtisas dairesi kurulması ZORUNLU değil, il bazında iş
  yüküne göre isteğe bağlı bir idari düzenleme.

**Ek kaynaklar:** [İstanbul Banka Alacakları İcra Dairesi — icra.net.tr](https://icra.net.tr/istanbul-banka-alacaklari-icra-dairesi/) ·
[İİK Madde 3/a — 7343 değişikliği](https://www.lexpera.com.tr/resmi-gazete/metin/icra-ve-iflas-kanunu-ile-bazi-kanunlarda-degisiklik-yapilmasi-hakkinda-kanun-31675-7343) ·
[İcra Daireleri Başkanlıkları Yönetmeliği haberi](https://ankalex.com/icra-daireleri-baskanliklarinin-kurulus-ve-gorevleri-ile-calisma-usul-ve-esaslari-hakkinda-yonetmelik-resmi-gazetede-yayinlandi/) ·
[Bakırköy'e Abonelik ve Banka İcra Dairesi duyurusu](https://istanbul2nolubarosu.org.tr/duyuruDetay.aspx?s=36&ss=BAKIRKoY-E-ABONELIK-VE-BANKA-ICRA-DAIRESI) ·
[Kambiyo Senetlerine Özgü Takip — Örnek 10](https://www.ekinhukuk.com.tr/kambiyo-senetlerine-ozgu-haciz-yoluyla-takip-ornek-no-10/)

### 9.5 Gayrimenkul Satış İcra Müdürlüğü ve Talimat Dosyası mekanizması
(kullanıcı isteği: "gayrimenkul satış icra müdürlüğü" + "talimat kısmını
araştır")

**Gayrimenkul Satış İcra Dairesi/Müdürlüğü nedir:** İcra takibi (haciz veya
ipoteğin paraya çevrilmesi) sonucu satışa çıkacak taşınmazların açık
artırma/ihale sürecini yürüten, aynı ihtisas dairesi ailesinden (7343 ile
bağlantılı — elektronik satış portalı zorunluluğu m.111/b, borçluya da
satış talep hakkı m.106) bir birim. **Devreye giriş noktası** (kısmi
doğrulanmış bulgu): dosya takip AÇILIŞINDAN itibaren değil, **satış
aşamasına** gelince bu daireye geçiyor — hazırlık süreçleri (kıymet
takdiri dahil, alttaki Yargıtay örneğinde görüldüğü gibi) ve kesinleşme
orijinal/asıl icra dairesinde (Banka Alacakları veya genel numaralı)
kalıyor, yalnız satış/ihale (ve kıymet takdiri) işlemleri bu daireye
devrediliyor. Kullanıcının tarifiyle BİREBİR örtüşüyor: "**ilamsız takipte
satış yapılacağı zaman, eğer o gayrimenkul satış icra dairesinin yetki
alanına giriyorsa satış için talimat yazılır**" — takibin başında
ipotek/rehin OLMASI ŞART DEĞİL, herhangi bir haciz sonucu satılacak
taşınmaz o yetki alanındaysa aynı mekanizma işliyor; şehirde böyle bir
daire yoksa satış zaten esas/asıl dairede kalır.

**Talimat dosyası — genel mekanizma (İİK):**
- **Madde 79** — haczolunacak mallar (taşınır) başka bir icra dairesinin
  yetki alanındaysa, asıl icra dairesi o yerin icra dairesine **talimat
  yazarak** haczi yaptırır.
- **Madde 360** — taşınmaz, takibi yürüten dairenin yetki çevresi
  DIŞINDAYSA, satış **istinabe** (talimat) yoluyla, taşınmazın bulunduğu
  yerdeki icra dairesince yapılır.
- **Esas dosya ↔ Talimat dosyası ayrımı:** icra müdürlüklerinde "Esas"
  olarak açılan dosyalar ve başka dairelerden GELEN talimatlar için açılan
  "Talimat" dosyaları AYRI numara/bölümde tutulur. Talimat dosyası esas
  dosyanın kendisi DEĞİL — yalnız BELİRLİ bir işlemin (haciz, kıymet
  takdiri, satış, tebligat) o yerde yapılması için açılan, esas dosyaya
  bağlı/referanslı bir alt kayıt; işlem bitince sonuç esas dosyaya rapor
  edilir.
- **Şikayet mercii kuralı (Yargıtay içtihadıyla somut örnekle doğrulandı):**
  talimatla yapılan işleme (kıymet takdiri, satış vb.) itiraz/şikayet, esas
  dosyanın bulunduğu yerin icra mahkemesine DEĞİL, **işlemi fiilen yapan
  (talimatı alan) dairenin bağlı olduğu icra mahkemesine** yapılır. Somut
  örnek: "Adana Gayrimenkul Satış İcra Müdürlüğünce" yapılan kıymet
  takdirine şikayet Adana 4. İcra Hukuk Mahkemesi'nde inceleniyor — esas
  takip başka şehirde olsa bile.

**Şemamızla bağlantı — ÖNEMLİ:** `Dosya.Tur.TALIMAT=1` zaten modelde var ve
Ceza'da canlı görülmüştü (Asliye Ceza Mahkemesi, `dosya_islem_turleri_
sorgula_brd.ajx` yanıtı neredeyse boş çıkmıştı — bkz. `docs/
VERITABANI_BULGULARI_02_CEZA.md §2`). Bu araştırma NEDENİNİ açıklıyor:
talimat dosyası kendi başına bir "takip" değil, yalnızca tek bir işlem
talebi — UYAP'ın normal Takip Türü/Yolu/Şekli alanlarının orada boş çıkması
TUTARLI ve BEKLENEN, eksik veri değil. İcra'da `tur_kod=1` (Talimat) ile
karşılaşılırsa muhtemelen aynı sığ yapı beklenebilir — ama bu bir VARSAYIM,
İcra bağlamında HENÜZ canlı doğrulanmadı (kalan iş, §8).

**Kaynaklar:** [İstanbul Gayrimenkul Satış İcra Dairesi](https://icra.net.tr/istanbul-gayrimenkul-satis-icra-dairesi/) ·
[İpotekli Taşınmazın İcra Dosyası Üzerinden Satış Süreci](https://moral.av.tr/tr/makaleler/ipotekli-tasinmazin-icra-dosyasi-uzerinden-satis-sureci-607) ·
[İcra Yoluyla Gayrimenkul Satışlarında Güncel Gelişmeler](https://andishukuk.com/icra-yoluyla-gayrimenkul-satislarinda-guncel-gelismeler/) ·
[Talimat Dosyası ile İcra Dosyası Arasındaki Fark](https://enmeshur.com.tr/talimat-dosyasi-ile-icra-dosyasi-arasindaki-fark) ·
[Kıymet Takdiri Talimat Yoluyla — Yargıtay değerlendirmesi](https://karamercanhukuk.com/yargitay-karari/kiymet-takdiri-talimat-yolula-yapildigindan-raporu-duzenleten-icra-dairesinin-bulundugu-yerdeki-icra-mahkemesi-sikayeti-incelemelidir) ·
[Taşınmazların Satışı — İİK m.360](https://www.ahmetekin.av.tr/tasinmazlarin-satisi/)

### 9.6 İlamsız takipte itiraz — takibi durdurma etkisi (kullanıcı isteği:
"itiraz ile durdurulan dosyalar var, bunu araştır")

Bu bölüm, geçen oturumda canlı görülen `Dosya.durum` = **"Açık (Durdurulmuş:
Takibe İtiraz)"** metninin (aynı `dosyaDurumKod=0`, bkz. §3) hangi kanuni
sürece karşılık geldiğini açıklıyor.

- **İİK Madde 62** — borçlu, ödeme emrinin tebliğinden itibaren **7 gün**
  içinde ilamsız icra takibine itiraz edebilir.
- **İİK Madde 66** — **süresinde ve usulüne uygun yapılan itiraz, takibi
  KENDİLİĞİNDEN durdurur** (icra müdürünün ayrı bir kararına gerek yok).
  Süresi geçtikten sonra yapılan itiraz bu etkiyi göstermez — alacaklının
  talebi üzerine icra müdürü takibe devam eder.
- **Takibin yeniden ilerlemesi için alacaklının 2 seçeneği var:**
  - **İtirazın İptali Davası (İİK m.67):** genel mahkemede (asliye hukuk/
    ticaret) açılan, genel hükümlere tabi bir eda davası; alacaklı alacağını
    HER TÜRLÜ delille (tanık, bilirkişi, fatura, ticari defter vb.)
    ispatlayabilir. Hak düşürücü süre **1 yıl** (itirazın tebliğinden
    itibaren).
  - **İtirazın Kaldırılması (İİK m.68/68-a):** yalnız İCRA MAHKEMESİNDE
    istenebilen, takip hukukuna özgü bir yol — yalnız alacaklının elinde
    YAZILI BELGE/imzası ikrar edilmiş senet varsa kullanılabilir, inceleme
    sınırlı sayıda belgeyle yapılır. Hak düşürücü süre **6 ay**. Belge
    şartı sağlanıyorsa alacaklı iki yoldan dilediğini seçebilir, ama dava
    açarak seçim hakkını kullanmışsa (dava derdestken) icra mahkemesine
    başvuramaz.
- **Örnek No'ların (ödeme emri formları) takip türüyle ilişkisi** —
  kullanıcının bahsettiği 7/13/14 örnekleri:
  - **Örnek No:7** — "İlamsız Takiplerde Ödeme Emri" (genel haciz yoluyla
    ilamsız takip) — bizim canlı `IcraTakipDetay` örneğimizdeki
    `takibinSekli` ile BİREBİR aynı (bkz. §3).
  - **Örnek No:13** — "Kiralanan Taşınmazın İlamsız Tahliyesi" sürecindeki
    İLK belge (kira alacağı + tahliye talepli ödeme emri) — borçlu 7 gün
    içinde itiraz etmezse VE 30 gün içinde ödemezse tahliye süreci ilerler.
  - **Örnek No:14** — aynı tahliye sürecinin devamı/tahliye emri aşaması.
  - Bu ikisi (13/14), kullanıcının önceden "kendim anlatacağım" dediği
    **Tahliye** takip türüyle DOĞRUDAN örtüşüyor — İcra bağlamında HENÜZ
    canlı görülmedi, ama artık hangi Örnek No'ları arayacağımızı biliyoruz.
- **Şemamızla bağlantı:** `Dosya.durum` serbest metin alanı zaten bu nüansı
  ("Açık (Durdurulmuş: Takibe İtiraz)" vs düz "Açık") taşıyor — modelde ek
  bir alan GEREKMİYOR, yalnızca bu durumun İİK m.66'daki "itirazın kendiliğinden
  durdurma etkisi"nin UYAP'taki görünümü olduğu artık AÇIKLANMIŞ oluyor.
  İtirazın iptali/kaldırılması davası SONUCUNUN (devam/düşme) Safahat'ta
  nasıl bir `safahatTuruKod` ile göründüğü HENÜZ canlı doğrulanmadı (kalan
  iş, §8).

**Kaynaklar:** [İlamsız İcra Takibine İtiraz ve Hukuki Sonuçları](https://kulacoglu.av.tr/ilamsiz-icra-takibine-itiraz/) ·
[İİK Madde 66 — e-uyar.com](https://app.e-uyar.com/madde/index/2d72ae18-2f90-4d05-ba51-8da1d829c3e6) ·
[İlamsız Takiplerde Ödeme Emri (Örnek No:7)](https://www.ekinhukuk.com.tr/ilamsiz-takiplerde-odeme-emri-ornek-no7-nedir/) ·
[Kiralanan Taşınmazın İlamsız Tahliyesi — Örnek No:13](https://www.hubox.com.tr/icerik/kiralanan-tasinmazin-ilamsiz-tahliyesi-ornek-no13) ·
[Kiralanan Taşınmazın İlamsız Tahliyesi — Örnek No:14](https://www.hubox.com.tr/icerik/kiralanan-tasinmazin-ilamsiz-tahliyesi-ornek-no14) ·
[İtirazın İptali Davası (İİK m.67)](https://kadimhukuk.com.tr/makale/itirazin-iptali-davasi-iik-67-madde/) ·
[İtirazın Kaldırılması Davası (İİK 68-68a)](https://barandogan.av.tr/blog/borclar-hukuku/itirazin-kaldirilmasi-davasi.html)

### 9.7 İcra takip türleri — kapsamlı taksonomi (kullanıcı isteği: "takip
türlerini kapsamlı araştır, benim atladıklarım olabilir")

Önceki bölümlerde (§9.4, §9.6) geçen Örnek No'ları TAMAMLAYAN, İİK
Yönetmeliği ekindeki resmi form numaralarına dayalı genel taksonomi:

**A. İLAMLI İCRA** (mahkeme kararı/ilam niteliğinde belgeye dayalı, her
türlü edim için):
- **Örnek 2** — Taşınır teslimi VEYA taşınmaz tahliye/teslim icra emri
- **Örnek 3** — Çocuk teslimine ilişkin icra emri
- **Örnek 4-5** — Para/teminat verilmesi + bir işin yapılması/yapılmaması +
  irtifak hakkı icra emri (**bizim canlı bulduğumuz tür**, bkz. §3)

**B. İLAMSIZ İCRA** (yalnız para/teminat alacakları, mahkeme kararı
gerekmez, itirazla kolayca durur — bkz. §9.6):
- **Örnek 7** — Genel haciz yoluyla ilamsız takip ödeme emri (**bizim canlı
  bulduğumuz tür — çoğunluk**, bkz. §3)
- **Örnek 10** — Kambiyo senetlerine özgü haciz yoluyla takip ödeme emri
  (çek/bono/poliçe)
- **Örnek 13/14** — Kiralanan taşınmazların ilamsız tahliyesi (bkz. §9.6)
- Abonelik sözleşmesi/MTS (§9.1-9.3) — teknik olarak Örnek 7'nin 7155 ile
  ayrıca özel düzenlenmiş bir uygulama biçimi

**C. REHNİN PARAYA ÇEVRİLMESİ YOLUYLA TAKİP** (hem ilamlı hem ilamsız
olabilir):
- **Örnek 8** — Taşınır rehninin paraya çevrilmesi (Eski No:50)
- **Örnek 9** — İpoteğin (taşınmaz rehni) paraya çevrilmesi ödeme emri
  (Eski No:152) — 30 gün ödeme + 7 gün itiraz süresi, **ipotek hakkına
  itiraz edilemez** (yalnız borca/imzaya itiraz edilebilir)

**D. İFLAS YOLUYLA TAKİP** (yalnız tacirlere/iflasa tabi borçlulara karşı,
en ağır takip yolu — külli tasfiye sonucu doğurur):
- **Örnek 11** — İflas yoluyla ADİ takip ödeme emri (fatura/cari hesap
  alacağı, kambiyo senedi YOK, 10 gün itiraz/ödeme süresi)
- **Örnek 12** — Kambiyo senetlerine DAYALI iflas takibi ödeme emri (bono/
  poliçe/çek, 5 gün itiraz/ödeme süresi)
- **Doğrudan Doğruya İflas** (İİK m.177) — İCRA TAKİBİ BİLE GEREKMEZ,
  doğrudan Asliye Ticaret Mahkemesi'nde dava açılır ("takipsiz iflas") —
  muhtemelen UYAP'ta İCRA yargı türünde HİÇ görünmez, Hukuk/Ticaret
  tarafında kalır (DOĞRULANMADI).

**İhtiyati Haciz — takip YOLU değil, bir TEDBİR (İİK m.257 vd):** dava/
takip öncesi veya sırasında mahkeme kararıyla alınır, sonradan icraya
konularak infaz edilir. `UyapMasa/modules/htiyati_Haciz.py` zaten var —
kod tarafında bilinen bir konu ama DB modelimizde henüz karşılığı yok;
"takip türü" değil, İcra dosyalarıyla iç içe geçen AYRI bir kavram.

**Şemamıza etkisi — rahatlatıcı bulgu:** `Dosya.Tur` (dosyaTurKod)
tarafında İcra için TEK kod var (**35 = İcra Dosyası**, bkz. `docs/
VERITABANI_BULGULARI_00_GENEL.md §2`) — UYAP takip YOLUNU dosya türü
seviyesinde AYIRMIYOR, hepsi aynı `tur_kod`. Ayrım tamamen `IcraTakipDetay.
takibin_turu/yolu/sekli` alanlarında yaşıyor — bu alanlar zaten modelde
var (§3), yeni bir `Dosya.Tur` kodu eklemeye GEREK YOK; yalnız
`takibinSekli`'nin Örnek No'lara karşılık gelen ham UYAP kodlarının (şu an
yalnız "0" ve "1" görüldü) her biri canlı doğrulanmalı.

**Kaynaklar:** [İlamlı ve İlamsız İcra Takibi](https://ayboga.av.tr/ilamli-ve-ilamsiz-icra-takibi/) ·
[Taşınır Rehninin Paraya Çevrilmesi — Örnek 8 / İpoteğin Paraya Çevrilmesi — Örnek 9](https://avevrak.com/ipotegin-paraya-cevrilmesinde-odeme-emri-ornek-9/) ·
[İflas Yoluyla Adi Takip — Örnek 11](https://www.dilekce.baltaci.av.tr/iflas-yoluyla-adi-takipte-odeme-emri-ornek-no11/) ·
[Kambiyo Senetlerine Dayalı İflas — Örnek 12](https://kubrayildiz.av.tr/kambiyo-senetlerine-dayali-iflas-takibi-ornek-no-12/) ·
[İflas Yoluyla Takip Nedir](https://www.ekinlaw.com/iflas-yoluyla-takip/) ·
[Doğrudan Doğruya İflas — İİK m.177](https://blog.lexpera.com.tr/alacakli-tarafindan-acilan-dogrudan-dogruya-iflas-davalari-iik-m-177/)
