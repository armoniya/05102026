# Yapılacaklar — 28.09.2026

Durum işaretleri: ✅ yapıldı · 🔎 canlı doğrulama bekliyor · ⏳ yapılacak

---

## 0. Kullanıcının 01.10.2026'da bakacakları ⏳

1. **9 borçlu — ilk mi en yeni tebliğ mi:** UYAP'ta kontrol → karar. Liste ve açıklama:
   `docs/KONTROL_COKLU_ODEME_EMRI_2026-09-30.md` (bkz. §4).
2. **İki bağlantılı dosya çifti (aynı barkod / aynı evrak iki dosyada):** UYAP'ta iki dosya arasındaki ilişki
   (talimat / yetkisizlik / birleştirme — "İlgili Dosyalar"):
   - İzmir Gayrimenkul Satış İcra D. 2025/256 ↔ İzmir 27. İcra D. 2023/19210 (54 barkod)
   - Milas İcra D. 2024/3957 ↔ İzmir 23. İcra D. 2023/22965 (11 barkod)
   Ayrıntı: `docs/KONTROL_MUKERRER_TEBLIGAT_2026-09-30.md`.
3. **Süre tablosu onayı:** `docs/KESINLESME_SURELERI_TAKIP_TURU.md` (sondaki 3 soru dahil) — bkz. §8.3.
4. **cokluuyap.com'a yükleme (deploy) yolu:** tatil admin modülü (§8.1) sunucuda çalışacak; canlı site ESKİ
   sürümü çalıştırıyor ve güncellemenin nasıl yüklendiği bilinmiyor (Render hesabı / GitHub bağlantısı /
   elle yükleme?). Gereken: Render panelinde servisin kaynağı (repo + dal) ya da yükleme yöntemi; erişim
   bilgisi paylaşılmadan önce hangi yolun kullanıldığını söylemen yeterli.
5. **Geçici yerel tatil girişi kararı:** sunucu hazır olana dek tatiller yerel `ResmiTatil` tablosuna girilsin
   mi? Öneri: evet — veritabanında borçluya bağlı ödeme emirleri 2020'den başladığı için (2020: 15, 2021: 242, … 2026: 1.072) **2020–2027**
   resmi/dini tatiller (arifeler "yarım gün" notuyla) tek seferlik yerel olarak girilir; sunucu modülü
   gelince aynı liste sunucuya aktarılır, yerel giriş eşitlemeyle üzerine yazılır. Liste, girilmeden önce
   onayına sunulur (Diyanet ilanına göre).

---

## 1. Bugün yapılanlar ✅

- **PTT tarayıcısı** (`UyapMasa/modules/ptt_tarayici.py`): headless canlı denendi → Turnstile reddediyor
  ("HeadlessChrome"), kullanılmıyor. Pencere artık varsayılan olarak EKRAN DIŞINDA açılıyor
  (`UYAP_PTT_GORUNUR=1` → ekranda). Chrome tur bitince kapanmıyor, süreç boyunca tek pencere; 15 dk boşta
  kalınca ve program kapanınca kapanıyor. Dar pencerede takip kutusu gizlendiği için `--window-size` eklendi.
- **Kesinleşme kuralı** (kullanıcı): ödeme emri gönderilmiş borçlu Kesinleşti olana dek **Kesinleşme Bekliyor**
  (dağıtımda, teslim ama mazbata dönmedi, iade/tebliğ edilemedi, süre işliyor). Kesinleşti = tebliğ + KENDİ
  mazbatası dosyada + süre doldu (`dosya_core._borclu_kesinlesme_hesapla`). Konkordato/Vefat/Vasi ezilmez.
- **Hata düzeltmesi:** `dosya_core._mazbata_sonucu` açıklamadaki "- Tebliğ Edildi/Edilemedi"yi hiç okuyamıyordu
  (`tr_lower` ğ→g). Tebliğ Durumu da düzeldi.
- DB'ye uygulandı (açık dosyalar): Kesinleşti 1.242 → 419, Kesinleşme Bekliyor 29 → 2.782.
  Yedek: `%LOCALAPPDATA%\UyapMasa\yedek\kesinlesme_yedek_20260928_172558.json`.

🔎 O sırada uygulamada çalışan barkod turu eski kodla yazıyordu → uygulama yeniden başlatılınca
`dosya_core.kesinlesme_durumlarini_guncelle()` bir kez daha çalıştırılacak.

## 2. MTS dosyalarının kesinleşmesi ⏳ (kullanıcı: "MTS sistemi çok farklı")

Şu an kesinleşmesi boş kalan açık dosya borçlularının büyük kısmı MTS:
**2.975 borçlu** (2.939'u tek borçlulu dosyada). MTS ödeme emrinin açıklaması yalnız
"PTT MTS Tebligat Belgesi" — muhatap yok; borçluya yalnız PTT yanıtındaki maskeli alıcıyla
(`_ptt_alici_borclusunu_bul`) bağlanabiliyor, PTT'de sorgulanmamış olanlar bağlanmıyor.

MTS sürecinin kuralları kullanıcıyla ayrıca konuşulacak — normal takibin kuralı VARSAYILMAYACAK:
- MTS'de ödeme emri, tebliğ, mazbata (PTT MTS Tebliğ Mazbatası) ve kesinleşme nasıl işliyor;
  MTS dosyası icra dairesine geçince ne değişiyor.
- Tek borçlulu MTS dosyasında "Ödeme Emri" türündeki tebligatın doğrudan o borçluya bağlanması (öneri,
  onay bekliyor).
- Aynı dosyada iki MTS ödeme emri kaydı (ör. 2026/2383: biri İADE, biri boş) nasıl yorumlanacak.

## 3. Kesinleşmesi boş kalan diğer borçlular ⏳

Kullanıcı notu: eski dosyaların kesinleşmesi pek takip edilmeyecek — öncelik yeni dosyalarda.
- **811** borçlu: dosya hiç taranmamış (229 Merkezi Takip Sistemi, 102 İzmir Abonelik …) → UYAP barkod turu.
- **~120**: ödeme emri borçlunun VEKİLİNE gitmiş ("Elektronik Tebligat[Vekil …]"). Öneri: vekil adı dosyada
  TAM BİR borçlunun kayıtlı vekiliyle eşleşirse o borçluya bağla (onay bekliyor).
- **389**: dosyada "Ödeme Emri" türünde tebligat yok (Belirsiz vb.) · **186**: aynı dosyada başka borçlunun
  ödeme emri var, bununki yok → canlı UYAP'ta örnek dosyalara bakılacak.

## 4. Karar bekleyenler ⏳

- Birden çok ödeme emrinde **en yeni** tebliğ mi (kod böyle) yoksa **ilk geçerli** tebliğ mi esas alınsın
  (ör. 2026/120272 ATİLLA YOLÇİ: 28.02.2026 tebliğ + mazbata, 26.09.2026 ikinci e-tebligat).
  NOT (30.09 ölçüm): borçlu başına ayrım ZATEN çalışıyor — bu soru AYNI borçluya giden birden çok ödeme
  emriyle ilgili. 814 borçlunun 805'inde yalnız biri başarılı (tipik: önce İADE, sonra yeniden tebliğ →
  kod doğru olanı alıyor). Karar gereken yalnız **9 borçlu** (iki tebliğ de başarılı), 7'sinde gün değişiyor.
  → Liste + açıklama: `docs/KONTROL_COKLU_ODEME_EMRI_2026-09-30.md` (kullanıcı kontrol edecek).
- Barkodsuz "Var" mazbata işareti: PTT turunda iki mod farklı davranıyor (Yalnız-PTT atlıyor, UYAP+PTT
  sorguluyor); kesinleşmede mazbata sayılmıyor.
- Takip türüne göre kesinleşme süresi (şu an hep Örnek 7 / 7 gün).

## 5. İtiraz

✅ (30.09) **"Borca İtiraz" belgesi kontrolü KALDIRILDI** (kullanıcı kararı): `ItirazBelgesi` modeli,
"İtiraz dilekçesini kontrol edin" durumu, `itiraz_kontrol.py`, masaüstü/web "İtiraz Kontrolü" ekranları
silindi (migration 0029; tabloda elle işaret yoktu). İtiraz artık yalnız dosya durumu
"Açık (Durdurulmuş : Takibe İtiraz)" ya da borçluya elle `itiraz_edildi_mi` ile belirlenir — bkz. §7.

## 6. Kapalı dosyada kesinleşme (30.09) ⏳

✅ (30.09) Dosya kartı "Canlı Veriyi Getir" artık dosya durumunu da yazıyor (arama kaydındaki
`dosyaDurum`, ek istek yok) ve kesinleşmeyi yeniden hesaplıyor (`dosya_core._canli_durum_kaydet`,
`_canli_kesinlesme_tazele`). Canlı: İzmir 10.
İcra 2026/8220 → "Kapalı (Vazgeçme/Feragat)(28/09/2026)".

⏳ **Kullanıcı kuralı (30.09, son hâli):** kesinleşme **kapanış tarihine göre BİR KEZ** hesaplanır, sonra
**donar** — dosya kapandıktan sonra hiçbir işlem devam etmez ("hem ekstra iş olmasın hem veritabanı
şişmesin").
- Kapanış tarihi `Dosya.durum` metninden: "Kapalı (Vazgeçme/Feragat)(28/09/2026)" ya da
  "Kapalı (2021-05-28 11:17:00.0)" — iki biçim de DB'de görüldü; ayrıştırıcı ikisini de okumalı, okunamazsa
  hesap yapılmaz (son açık değer kalır).
- **Tek seferlik hesap:** dosyanın durumu Açık → Kapalı'ya döndüğü an (künye kaydı / "Canlı Veriyi Getir")
  `_borclu_kesinlesme_hesapla` `bugun` yerine kapanış tarihiyle bir kez çalışır. Mevcut kapalı dosyalar için
  bir kerelik toplu geçiş.
- **Sonra dokunulmaz:** toplu kesinleşme hesabı, barkod/PTT turu ve güncelleme hattı kapalı dosyaları
  (`durum_kod=1`) işlemez. Dosya yeniden açılırsa normal hesaba döner.
- Kesinleşme tarihi ≤ kapanış tarihi → Kesinleşti; değilse **"Kesinleşmeden kapandı"** (ad kullanıcı
  kararı, 30.09; yeni `KesinlesmeDurumu` değeri → migration). İtiraz / Durdurma / Konkordato / Vefat / Vasi
  korunur.

## 7. Durdurma nedenleri → kesinleşme (30.09)

✅ (30.09) İcra dosya durumu "Açık (Durdurulmuş : X)" → dosyanın TÜM borçlularının kesinleşmesi X
(`dosya_core.DURDURMA_NEDENLERI` / `durdurma_kesinlesme_kodu`, migration 0030). Takibe İtiraz → "İtiraz";
diğerleri "Durdurulmuş: İcra Mahkemesine İtiraz / Genel Mahkemeye Dava Açılması / Tehir-i İcra Kararı /
İhtiyati Tedbir / İflas / Borçlunun Ölümü / Diğer"; listede olmayan → "Durdurulmuş: Tanımsız Neden" + log.
Öncelik: elle `itiraz_edildi_mi` > durdurma > tebliğ hesabı; elle Konkordato/Vefat/Vasi ezilmez. Durdurma
kalkınca borçlu normal hesaba döner. Amaç: iş tespiti + filtre.

⏳ **Yapılacak İşler modülüne iş: "Durdurulmuş: Diğer" dosyalarında elle giriş** (kullanıcı, 30.09).
Yapılacak İşler modülü henüz yok (planlı, bkz. `YAPILACAKLAR_2026-09-25.md`); modül kurulunca ilk iş
türlerinden biri bu olacak.
- **Neden:** UYAP "Diğer" durdurmanın gerçek nedenini ve kapsadığı borçluyu söylemiyor. Kullanıcının
  30.09'da UYAP'ta baktığı örnekler:
  - Adıyaman İcra 2025/14958 → aslında **itiraz**.
  - Çankırı İcra 2025/2489 → borçlu **itiraz** etmiş, durdurma "Diğer" girilmiş.
  - Burhaniye İcra 2022/1062 → **yalnız Ayşe Pesent Çiftçi** yönünden durdurma; diğer borçlu(lar)da takip
    devam ediyor. 2022/1063 → aynı şekilde **bir borçlu** yönünden durdurma.
  - Yani "Diğer" durdurma borçlu bazlı olabiliyor — şimdiki "tüm borçlular" kuralı bu dosyalarda yanlış
    sonuç veriyor (başka nedenler için aynısı VARSAYILMAZ, ayrıca doğrulanır).
- **İş:** dosya durumu "Durdurulmuş : Diğer" olan her dosya için Yapılacak İşler'e bir iş düşer
  (DB'de 30.09 itibarıyla 20 dosya / 28 borçlu).
- **Elle giriş — BORÇLU BAŞINA** (kullanıcı seçimi): her borçlu için
  - **İtiraz**,
  - **Durdurulmuş** (neden serbest metin/listeden),
  - **Takip devam ediyor** → normal tebliğ/kesinleşme hesabı uygulanır.
- Elle seçim, UYAP'taki dosya durumu değişene dek korunur (hesap ezmez); durum değişirse (ör. durdurma
  kalkar ya da neden değişir) iş yeniden listeye düşer.
- Açık sorular (modül kurulurken sorulacak): elle girilen neden için ayrı alan mı, yoksa mevcut
  `kesinlesme_durumu` değerleri mi; bu elle giriş diğer durdurma nedenlerine (ör. İhtiyati Tedbir,
  İflas) de açılsın mı.

## 8. Kesinleşme — kalan işler planı (30.09)

### 8.1 Resmi/dini tatiller — üretici admin modülü ⏳
Tespit (30.09): yerel `ResmiTatil` tablosu **BOŞ** → hesap yalnız hafta sonunu atlıyor; bayram/29 Ekim'e
denk gelen süre sonu erken "kesinleşti" çıkabilir. Kullanıcı kararı: tatilleri **üretici** her yıl
**genel (merkezi) veritabanına** girer; programlar oradan alır.
- **Sunucu (`Uyap Haricen Giriş/vendor_server.py`, cokluuyap.com):** mevcut owner (utku, TOTP'li) `/admin`
  altına "Resmi Tatiller" sayfası: yıl seç, gün ekle/sil (tarih + açıklama [+ yarım gün?]). Veri mevcut
  hesap deposunda (`accounts.py`: JSON ya da Render Postgres JSONB k/v) ayrı anahtarla tutulur.
- **Okuma ucu:** `GET /api/resmi-tatiller` (herkese açık veri; sürüm/ETag ile).
- **İstemci (UyapMasa):** açılışta + günde bir kez çeker, yerel `ResmiTatil`'e yazar (gelen yılların
  kümesini birebir eşitler); değişiklik varsa kesinleşme yeniden hesaplanır. İnternet yoksa yerel tablo
  kullanılır. Günde tek küçük istek — performansa yük yok.
- **Risk:** cokluuyap.com canlıda ESKİ sürüm çalışıyor, deploy yolu bilinmiyor (bkz. bellek
  render-canli-kod-eski) → önce deploy yolu netleşmeli (§0 madde 4, kullanıcı 01.10'da bakacak).
- **Geçici yerel giriş** (karar bekliyor, §0 madde 5): sunucu hazır olana dek 2020–2027 tatilleri yerel
  tabloya; sunucu gelince eşitleme üzerine yazar.
- **Veri kapsamı:** yalnız bu yıl değil, **2020'den itibaren** (DB'deki en eski borçluya bağlı ödeme emri 2020).
- **Arife (yarım gün) — araştırma (30.09, dış kaynak, kullanıcı onayı bekliyor):** 2429 sayılı Kanun'a
  göre Ramazan/Kurban arifesi ve 28 Ekim **13.00'ten itibaren** yarım gün tatil. Kaynakların ortak görüşü:
  bu günler süre yönünden **İŞ GÜNÜ** sayılır — sürenin son günü arifeye denk gelirse süre **uzamaz**, o gün
  mesai bitiminde (13.00) dolar; itiraz arife günü yapılmalıdır (icraguncesi.com'daki Yargıtay kararı
  başlığı: "Son gün bayram öncesi yarım gün resmi tatile rastlarsa bu tarihte icra müdürlüğüne itiraz
  edilmesi gerekir" — kararın künyesi okunamadı, site sertifikası geçersiz). İİK m.19: son gün TAM tatile
  rastlarsa süre tatili izleyen ilk iş gününe uzar. Adli tatil icra sürelerini etkilemez.
  → Programa etkisi: arife tatil tablosuna **"yarım gün"** işaretiyle girilir ama süre hesabında **iş günü**
  sayılır (itiraz son günü olabilir); kesinleşebilir gün ise ertesi TAM iş günüdür (bayram atlanır).
  Admin ekranında "yarım gün" alanı bu yüzden gerekli. Açık nokta: UYAP üzerinden elektronik itirazın arife
  günü 13.00'ten sonra (23.59'a kadar) yapılıp yapılamayacağı — kesinleşme gününü değiştirmez, not olarak kalsın.

### 8.2 Takip türü verisi — neden yalnız 22 dosyada var ⏳
Kök neden (30.09, kod okundu): `IcraTakipDetay` YALNIZ "Dosya Görüntüle → canlı" yolunda yazılıyor
(`dosya_core.dosya_ayrinti_kaydet`). "UYAP'tan Güncelle" hattının katmanları (künye/taraf/barkod/kesinleşme)
ayrıntı isteği HİÇ atmıyor — kullanıcının önceki güncellemeleri bu yüzden doldurmadı. Ayrıca:
- Açıklama metinleri (ör. "ÖRNEK: 7 İlamsız Takiplerde Ödeme Emri") UYAP yanıtında VAR (bkz.
  VERITABANI_BULGULARI_01_ICRA §3) ama kod "tahmini" diye DB'ye yazmıyor; 22 kaydın hepsinde boş.
- Şekil KODU tek başına yetmiyor: Örnek 7 de Örnek 4-5 de `takibinSekli="0"` → tür açıklama metninden okunmalı.
**Kullanıcı kuralı (30.09): "takip türü bilinmeyen dosya" KABUL EDİLMEZ** — her İcra dosyasının takip türü
veritabanında olmalı:
- **Yeni dosya:** program UYAP'tan veri çekerken dosya veritabanına İLK KEZ ekleniyorsa, aynı anda takip türü
  (ayrıntı) da çekilir ve yazılır.
- **Eski dosya:** veritabanında olup takip türü bilgisi olmayan dosya sorgulanıp çekilir (güncelleme hattında
  eksikleri tamamlama; bir kez).
- **Dosya kartı:** her durumda ayrı bir düğme — "Takip Bilgilerini Güncelle" — o dosyanın takip türü/
  ayrıntısını UYAP'tan yeniden çekip yazar.
Plan:
1. ✅ (01.10) Açıklama alanlarının gerçek anahtar adları canlı ham yanıtta görüldü (`%LOCALAPPDATA%\UyapIcra\
   dosyalarim_genel.log`, 30.09 16:17): `takibinTuruAciklama` ("İlamsız Takip"), `takibinSekliAciklama`
   (" ÖRNEK: 7 İlamsız Takiplerde Ödeme Emri - Eski No: 49 " — baş/son boşluklu), `takibinYoluAciklama`
   ("Genel Haciz Yoluyla Takip"); ayrıca `dosyaDurumu` ("Vazgeçme/Feragat") aynı yanıtta.
2. `dosya_ayrinti_kaydet` açıklamaları da yazar; metinden "Örnek No" ayrıştırılır (ör. `ornek_no` alanı).
3. Künye kaydı (`ingest.dosya_kunyesi_kaydet`) dosyayı YENİ oluşturduğunda ayrıntı isteği tetiklenir (tüm
   künye yazan akışlar: İcra Sorgula, UYAP'tan Güncelle, barkod turu).
4. `guncelleme_hatti`'na "ayrıntı" katmanı: YALNIZ `IcraTakipDetay`'ı (ya da Örnek No'su) olmayan dosyalar
   (takip türü değişmez → dosya başına BİR KEZ). İlk tur ~6.786 açık dosya = ~6.786 ücretsiz istek (Safahat
   DEĞİL); sonra yalnız eksikler.
5. Dosya kartına (masaüstü + web) "Takip Bilgilerini Güncelle" düğmesi.
6. Kesinleşme hesabı süreyi Örnek No'dan alır (8.3); tablo kullanıcı onayından sonra koda girer.

### 8.3 Takip türüne göre kesinleşme süreleri ⏳ (kullanıcı onayı bekliyor)
Tablo, genel kurallar (tatil/arife/adli tatil/e-tebligat), onayda bakılacak 3 soru ve kaynaklar:
**`docs/KESINLESME_SURELERI_TAKIP_TURU.md`** (30.09). Özet: Örnek 7/8/9/11/13/14 ve MTS → 7 gün;
Örnek 10 (kambiyo) ve 12 (kambiyo iflas) → **5 gün**; Örnek 4-5 (ilamlı) → itirazla kesinleşme yok, 7 gün
sonra cebri icra. Onaydan sonra koda girer; takip türü verisi (8.2) önce toplanmalı.
(Takip türü bilinmeyen dosya olmayacak — bkz. 8.2 kullanıcı kuralı.)

### 8.4 Aklımda tutulacak diğer öneriler ⏳
- "Kesinleşme Bekliyor" (2.569 borçlu) içinde farklı işler: 836'sında süre dolmuş ama mazbata dönmemiş,
  1.129'u İADE, 272'sinde tebliğ bilgisi yok → Yapılacak İşler modülünde ayrı iş türleri. Kullanıcı notu
  (30.09): Barkod Sorgu modülündeki iş eylem düğmeleri (21/2 Uygunluk Taraması → "Seçilenler İçin Gönder",
  yeniden tebliğ talebi takibi) bunun bir kısmını zaten karşılıyor.
- ⏳ **"Kesinleştirme Talebi Gönder" düğmesi** (kullanıcı, 30.09): kesinleşme günü gelmiş borçlular için
  icra dairesine takibin kesinleştirilmesi talebini UYAP avukat portalından gönderen eylem düğmesi —
  Barkod Sorgu'daki 21/2 gönderme akışıyla aynı desen (seçili borçlular → önizleme/onay → gönder → sonuç
  DB'ye yazılır, aynı borçluya ikinci kez gönderilmez). Yapılacak İşler modülüne de "Kesinleştirme talebi
  gönderilecek" iş türü olarak bağlanır. Kurulmadan önce canlı UYAP'ta doğrulanacaklar:
  - Talep türünün portaldaki adı ve gönderim uç noktası (21/2 talebi `akis_21_2_Talep.py` örnek alınır).
  - Ücretli mi (21/2 gönderimi ücretli işaretli).
  - Talep gönderildikten sonra dosyada nasıl göründüğü (evrak türü) — "gönderildi mi" tespiti için
    (Safahat'taki "Dosya Kesinleşme Bilgi Girişi" satırı ücretli/kotalı, ona dayanılmamalı).
  - Kimlere gönderilir: yalnız "Kesinleşti" (hesap) olan, itiraz/durdurma olmayan borçlular; çok borçlu
    dosyada borçlu başına mı dosya başına mı talep edildiği.
- Kesinleşme kuralları için otomatik test dosyası (senaryo testleri; yol haritası Aşama 1).
- MTS kesinleşmesi (§2) ve boş kalan borçlular (§3: vekile giden ödeme emri ~120 vb.).
- "Durdurulmuş: Diğer" elle giriş işi (§7).
