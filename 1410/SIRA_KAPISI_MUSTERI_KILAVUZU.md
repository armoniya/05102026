# Sıra Kapısı — Kurulum

*Çoklu UYAP · 13.10.2026*

## Ne işe yarar?

Ofisinizde UYAP'a kendiliğinden istek gönderen bir program (bot) çalışıyorsa, bu istekler sizin Çoklu UYAP üzerinden yaptığınız işlemlerle aynı anda UYAP'a ulaşıp uyarıya yol açabilir.

Sıra Kapısı, botun isteklerini Çoklu UYAP bilgisayarı üzerinden geçirir ve sıraya koyar:

- Sizin işlemleriniz **her zaman önce gelir**.
- İki istek UYAP'a **aynı anda gitmez**.
- Bot kendi UYAP oturumuyla çalışmaya devam eder.

## Gerekenler

- İki bilgisayar da **aynı ofis ağında** olmalı.
- İki bilgisayara da **aynı Çoklu UYAP kurulumu** yapılır.
- Bot bilgisayarında **bir kez** Windows yönetici onayı verilir.

## Kurulum (bir kez, yaklaşık 2 dakika)

### 1. Ofis bilgisayarında (Çoklu UYAP paylaşımını yapan)

1. Çoklu UYAP'a giriş yapın.
2. Soldaki menüden **Sıra Kapısı**'nı seçin.
3. **Bot Bilgisayarı Ekle**'ye tıklayın.
   - İlk seferde Windows bir onay penceresi açar. **Evet** deyin.
4. Açılan pencere bot bilgisayarını bekler. Pencereyi kapatmayın.

### 2. Bot bilgisayarında

1. Çoklu UYAP'ı açın. **Giriş yapmanız gerekmez.**
2. Giriş ekranının altındaki **Bot bilgisayarı kurulumu (Sıra Kapısı)** bağlantısına tıklayın.
3. **Ofis Bilgisayarını Bul**'a, ardından **Bağlan**'a tıklayın.

### 3. Kodu iki ekranda karşılaştırın

Her iki bilgisayarın ekranında **6 haneli bir kod** görünür (ör. `385 900`).

- Kodlar **aynıysa**: ofis bilgisayarında **Onayla**'ya, bot bilgisayarında **Evet, aynı**'ya tıklayın.
- Kodlar **farklıysa**: iki tarafta da reddedin ve yöneticinize haber verin.

Bot bilgisayarında Windows bir onay penceresi açar. **Evet** deyin.

**Kurulum tamamlandı.** Bot artık UYAP'a ofis bilgisayarı üzerinden gider.

## Günlük kullanım

Hiçbir şey yapmanız gerekmez. Kapı, ofis bilgisayarında **paylaşım açıkken** kendiliğinden çalışır.

⚠️ Ofis bilgisayarı kapalıyken ya da paylaşım durdurulduğunda bot UYAP'a ulaşamaz.

## Durum ve ayarlar (ofis bilgisayarı → Sıra Kapısı)

| Ne görürsünüz / ne yaparsınız | Anlamı |
|---|---|
| ● Kapı açık | Bot istekleri sıraya alınıyor. |
| ● Kapı hazır — paylaşım başlayınca açılır | Paylaşımı başlatın. |
| ● Kapı kapalı | Botlar UYAP'a ulaşamaz. **Kapıyı Aç**'a tıklayın. |
| **Seçili Botu Kaldır** | Botu listeden çıkarır. Bot bilgisayarında da kurulumu kaldırın. |
| **Günlüğü Aç** | Botun her isteğini ve sırada ne kadar beklediğini gösterir. |

## Kaldırma

Bot bilgisayarında Çoklu UYAP'ı açın. **Bot bilgisayarı kurulumu** → **Kurulumu Kaldır**'a tıklayın ve Windows onayına **Evet** deyin. Bot yeniden doğrudan UYAP'a bağlanır.

Ardından ofis bilgisayarında **Sıra Kapısı** → botu seçin → **Seçili Botu Kaldır**.

## Sorun giderme

| Belirti | Çözüm |
|---|---|
| Bot bilgisayarı "Ofis bilgisayarı bulunamadı" diyor | Ofis bilgisayarında **Bot Bilgisayarı Ekle** penceresi açık olmalı (10 dakika açık kalır). Hâlâ bulunamıyorsa ofis bilgisayarının IP adresini kutuya yazıp **Bağlan**'a tıklayın. |
| Bot UYAP'a ulaşamıyor | Ofis bilgisayarı açık ve paylaşım başlatılmış mı? Bot bilgisayarında **Bot bilgisayarı kurulumu** → **Bağlantıyı Sına** ile kontrol edin. |
| "Ofis bilgisayarının adresi değişmiş" | Ofis bilgisayarında botları kaldırıp yeniden ekleyin. Bunun tekrarlanmaması için modemden iki bilgisayara da **sabit IP** verilmesini öneririz. |
| Bot "sertifika hatası" veriyor | Bot programı Windows'un sertifika deposunu kullanmıyor olabilir. Kurulumu kaldırıp teknik desteğe başvurun. |
| Antivirüs "hosts dosyası değiştirildi" uyarısı | Bu değişiklik kurulumun parçasıdır, bilerek yapılır. |

## Bilinmesi gerekenler

- Kurulum, bot bilgisayarının **tamamını** etkiler. O bilgisayardan tarayıcıyla UYAP'a girenler de ofis bilgisayarı üzerinden geçer. Bot bilgisayarını mümkünse yalnız bot için kullanın.
- Sertifika yaklaşık **2 yıl 3 ay** geçerlidir. Bitiş tarihi Sıra Kapısı ekranında yazar. Bu tarihten önce botları yeniden ekleyin.
