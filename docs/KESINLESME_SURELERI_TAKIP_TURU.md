# Takip Türüne Göre Kesinleşme Süreleri

Hazırlanma: **30.09.2026** · Durum: ⏳ **kullanıcı (avukat) onayı bekliyor** — onaylanmadan koda girmez.
Kaynak: internet araştırması (aşağıda), UYAP'tan canlı gözlem DEĞİL.
İlgili: `docs/YAPILACAKLAR_2026-09-28.md` §8.2 (takip türü verisi), §8.3; `docs/VERITABANI_BULGULARI_01_ICRA.md` §9.7 (taksonomi).

## Genel kurallar

- **Kesinleşme** = ödeme emrine **itiraz süresi** dolup süresinde itiraz edilmemesi. Ödeme süresi kesinleşmeyi
  belirlemez (ayrı bilgi olarak tutulur).
- Süre, **tebliği izleyen günden** başlar. Tatil günleri süreye dahildir; **son gün tam tatile** (hafta sonu,
  resmi/dini bayram) rastlarsa süre tatili izleyen **ilk iş günü** mesai bitiminde dolar (İİK m.19).
- **Arife / 28 Ekim (13.00'ten sonra yarım gün tatil):** süre yönünden **iş günü** — son gün arifeye denk
  gelirse süre uzamaz, 13.00'te dolar (bkz. YAPILACAKLAR §8.1).
- **Adli tatil** (20 Temmuz – 31 Ağustos) icra sürelerini **durdurmaz, uzatmaz**.
- **E-tebligat:** tebliğ = evrak tarihi + 5 gün (programdaki mevcut kural).
- **Kesinleşebilir gün** (programın yazdığı tarih) = itiraz süresinin son gününden sonraki ilk iş günü.

## Süre tablosu

| Örnek No | Takip türü | İtiraz süresi (→ kesinleşme) | İtirazın yapıldığı yer | Ödeme süresi | Not |
|---|---|---|---|---|---|
| **7** | İlamsız — genel haciz yoluyla | **7 gün** | İcra dairesi | 7 gün | Programın bugün HER dosyaya uyguladığı kural |
| **10** | Kambiyo senetlerine özgü haciz (çek/bono/poliçe) | **5 gün** | İcra mahkemesi | 10 gün | İtiraz takibi kendiliğinden DURDURMAZ (mahkeme kararı gerekir) |
| **13** | Kira alacağı — tahliye ihtarlı ödeme emri | **7 gün** | İcra dairesi | 30 gün (konut/çatılı işyeri); ürün kirası 60, diğer 10 | Ödeme süresi bitiminden itibaren 6 ay içinde tahliye talebi |
| **14** | Süre bitimine dayalı tahliye | **7 gün** | İcra dairesi | — (15 gün içinde tahliye) | |
| **8** | Taşınır rehninin paraya çevrilmesi | **7 gün** | İcra dairesi | 15 gün | Rehin hakkına ayrıca, açıkça itiraz edilmeli |
| **9** | İpoteğin paraya çevrilmesi | **7 gün** | İcra dairesi | 30 gün | İpotek hakkına itiraz edilemez (yalnız borca/imzaya) |
| **11** | İflas yoluyla adi takip | **7 gün** (dilekçeyle) | İcra dairesi | 7 gün | Kesinleşince alacaklı iflas davası açabilir |
| **12** | Kambiyo senedine dayalı iflas | **5 gün** | İcra dairesi | 5 gün | |
| **4-5** | İlamlı (para/teminat) — icra emri | *İtirazla kesinleşme yok* | İcra mahkemesi (icranın geri bırakılması) | 7 gün | 7 gün sonra cebri icraya geçilir; programda "kesinleşme" yerine ayrı gösterim düşünülmeli |
| **MTS (7155)** | Abonelik sözleşmesi alacakları | **7 gün** | Herhangi bir icra dairesi / MTS (e-imza) | — | Sonra yetkili icra dairesinde "cebri icraya devam" kararı; MTS kuralları ayrıca konuşulacak (YAPILACAKLAR §2) |

Kapsam dışı (ödeme emriyle kesinleşme kavramı yok ya da canlı görülmedi): Örnek 2 (taşınır teslimi/taşınmaz
tahliyesi icra emri), Örnek 3 (çocuk teslimi), doğrudan doğruya iflas (İİK m.177), ihtiyati haciz (tedbir).

## Onay sırasında bakılacaklar

- Örnek 10 ve 12'de "5 gün" hem itiraz hem şikâyet için mi esas alınsın?
- Örnek 4-5 (ilamlı) dosyalarda kesinleşme sütununda ne görünsün (ör. "İcra emri süresi doldu")?
- Örnek 13'te kesinleşme yalnız 7 günlük itiraz süresine mi bağlı; 30 günlük ödeme süresi ayrı bir iş
  (tahliye talebi) olarak mı izlenecek?

## Programa uygulanması (onaydan sonra)

Takip türü verisi toplandıktan sonra (YAPILACAKLAR §8.2) `kesinlesme_core`'a Örnek No → itiraz süresi
eşlemesi eklenir; `dosya_core._borclu_kesinlesme_hesapla` süreyi dosyanın Örnek No'sundan alır.
Takip türü bilinmeyen dosya olmayacak (kullanıcı kuralı, §8.2).

## Kaynaklar

- [Ödeme Emrine İtiraz Süresi 2026: 7 Gün, 5 Gün – Balgat Hukuk](https://balgathukuk.com/hukuk-rehberi/icra-hukuku/odeme-emrine-itiraz-suresi-rehberi-2026)
- [Kambiyo Senetlerine Mahsus Haciz Yolu İle Takiplerde İtiraz ve Şikayet – Sert Hukuk](https://sert.av.tr/kambiyo-senetlerine-mahsus-haciz-yolu-ile-takiplerde-itiraz-ve-sikayet/)
- [Kambiyo Senetlerine Özgü İcra Takibi – Kadim Hukuk](https://kadimhukuk.com.tr/makale/kambiyo-senetlerine-ozgu-icra-takibi/)
- [İlamlı İcra Takibi Nedir – Kadim Hukuk](https://kadimhukuk.com.tr/makale/ilamli-icra-takibi-nedir-nasil-yapilir-itiraz-edilir/)
- [İcra Emri ve İcranın Geri Bırakılması – Av. Alptürk Kaya](https://alpturkkaya.av.tr/hukuk/ozel-hukuk/icra-iflas-hukuku/icra-emri-icerigi-ve-icranin-geri-birakilmasi/)
- [Örnek 13 Tahliye Takibi – Tureli Ceylan](https://tureliceylan.com/ornek-no-13-tahliye-ihtarli-odeme-emri/)
- [Örnek 13 ve Örnek 14 Farkı – Saim Doğru](https://www.saimdogru.com/yayin/kiracinin-icra-yoluyla-tahliyesinde-ornek-no-13-ve-ornek-no-14-farki-hangi-durumda-hangisi-uygulanir)
- [Menkul Rehninin Paraya Çevrilmesi (Örnek 8) – Kübra Yıldız](https://kubrayildiz.av.tr/menkul-rehninin-paraya-cevrilmesi-yoluyla-icra-takibi-ornek-no-8/)
- [Rehnin Paraya Çevrilmesinde Ödeme Emri – Kılınç Hukuk](https://www.kilinc.av.tr/rehnin-paraya-cevrilmesi-yolula-icra-takibinde-odeme-emri/)
- [İflas Yoluyla Adi Takip (İİK 155-156) – Hukukçular Evi](https://hukukcularevi.com/iflas-yoluyla-adi-takip-odeme-emri-itiraz-iik-155/)
- [Kambiyo Senetlerine Dayalı İflas (Örnek 12) – Kübra Yıldız](https://kubrayildiz.av.tr/kambiyo-senetlerine-dayali-iflas-takibi-ornek-no-12/)
- [7155 sayılı Kanun – mevzuat.gov.tr](https://mevzuat.gov.tr/MevzuatMetin/1.5.7155.pdf)
- [Abonelik/Fatura Borcu MTS Takibi ve İtiraz – Hukukçular Evi](https://hukukcularevi.com/abonelik-fatura-borcu-icra-takibi-mts-itiraz/)
- [Adli Tatil 2026: İcra Ne Olur – Sinanoğlu Hukuk](https://sinanogluhukuk.com/tr/makaleler/adli-tatil-2026)
- [Adli Tatilde İcra Takipleri Durur mu – Bolak Hukuk](https://www.aycabolak.av.tr/adli-tatilde-icra-takipleri-durur-mu/)
- [Son gün bayram öncesi yarım gün resmi tatile rastlarsa… – icraguncesi.com](http://icraguncesi.com/son-gun-bayram-oncesi-yarim-gun-resmi-tatile-rastlarsa-bu-tarihte-icra-mudurlugune-itiraz-edilmesi-gerekir/)
- [Süre Hesaplama – İmer Hukuk](https://imer.av.tr/avukat-araclari/sure-hesaplama)
