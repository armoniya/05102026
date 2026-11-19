# Sıra Kapısı — Kullanım Kılavuzu

*cokluuyap · 12.10.2026 · güncelleme 13.10.2026: light (CokluUyap) kurulumuna eklendi, güvenlik sıkılaştırması*

> **Bu belge teknik destek içindir.** Müşteri kurulumu artık komut gerektirmez: Çoklu UYAP içinde *Sıra Kapısı* sekmesi (ofis) ve giriş ekranındaki *Bot bilgisayarı kurulumu* (bot) ile yapılır — bkz. `SIRA_KAPISI_MUSTERI_KILAVUZU.md`. Aşağıdaki komut/BAT yolu yalnız elle kurulum ya da sorun giderme için kalmıştır.

## 1. Ne işe yarar?

Ofiste ayrı bir bilgisayarda çalışan otomasyon programı (bundan sonra **bot**) `avukat.uyap.gov.tr`'ye doğrudan istek gönderiyor. Bu istekler cokluuyap.com üzerinden yapılan tıklamalarla aynı anda UYAP'a ulaşınca UYAP eşzamanlılık uyarısı veriyor ve işlem engelleniyor.

Sıra kapısı botun isteklerini cokluuyap üzerinden geçirir ve şu kurallarla UYAP'a iletir:

- Botun istekleri **tek tek** gider. İki istek arasında en az **1 saniye** beklenir.
- **Bizim işlerimiz önce gelir.** cokluuyap.com'dan bir işlem yapılıyorsa bot bekler.
- Bot isteği UYAP'tayken bizim POST işlemlerimiz (sorgu, kaydetme, gönderme) sırada bekler. İki istek UYAP'a aynı anda gitmez.
- Bot **kendi oturumuyla** çalışmaya devam eder. Kapı, botun çerezlerine ve bizim UYAP oturumumuza dokunmaz. Sadece istekleri sıraya koyar.

```
Bot PC  ──►  cokluuyap PC (Sıra Kapısı, port 443)  ──►  avukat.uyap.gov.tr
                         ▲
cokluuyap.com tıklamaları ┘  (aynı sıra, öncelik bizde)
```

## 2. Nasıl çalışır?

| Parça | Ne yapar |
|---|---|
| **hosts dosyası** (bot PC) | Bot PC'si `avukat.uyap.gov.tr`'yi sorduğunda cevap olarak cokluuyap PC'sinin adresini verir. Böylece bot farkında olmadan kapıya bağlanır. |
| **Kök sertifika** (bot PC) | Bot, bağlantının güvenli olduğunu görmek ister. Kurulan sertifika **yalnız `avukat.uyap.gov.tr` için** geçerlidir. Bu sertifikayı üreten özel anahtar üretimden hemen sonra silinmiştir, bu yüzden başka bir site adına sertifika üretmek mümkün değildir. |
| **Sıra kapısı** (cokluuyap PC) | cokluuyap paylaşımı başlayınca otomatik açılır. İstekleri sıraya alıp gerçek UYAP'a iletir. |

## 3. Kurulum (bir kez yapılır)

### Adım 1 — cokluuyap PC'sinde kurulum dosyalarını üretin

Önce bot bilgisayarının IP adresini öğrenin. Bot PC'sinde `cmd` açıp `ipconfig` yazın ve **IPv4 Adresi** satırına bakın (ör. `192.168.1.50`).

Sonra cokluuyap PC'sinde komutu çalıştırın. Komut, programın nasıl kurulduğuna göre değişir:

**a) Çoklu UYAP (light) MSI ile kurulduysa:** `cmd` açıp şunu yazın:

```
"%LOCALAPPDATA%\CokluUyap\CokluUyap.exe" --sira-kapisi kur --bot-ip 192.168.1.50
```

Program penceresi açılmaz. Sonuç Not Defteri'nde açılır (dosya: `%LOCALAPPDATA%\UyapIcra\sira_kapisi\son_komut.txt`). netsh komutunu oradan kopyalayın.

**b) Kaynak koddan çalışıyorsa:** `Masaüstü\cokluuyap\Uyap Haricen Giriş` klasöründe:

```
python -m uyap_core.sira_kapisi kur --bot-ip 192.168.1.50
```

> Bot IP'si ve Kapı IP'si **yerel ağ (LAN) IPv4 adresi** olmalıdır (192.168.x.x, 10.x.x.x, 172.16-31.x.x). İnternet adresi, 127.0.0.1 ya da bu bilgisayarın kendi adresi verilirse komut hiçbir dosya üretmeden durur.

Komut çalışınca şunları yazar:
- **Kapı IP'si:** Bu bilgisayarın yerel adresi. Yanlışsa komutu `--kapi-ip 192.168.1.10` gibi bir ekle tekrar çalıştırın.
- **Bot PC'sine kopyalanacak klasör:** `%LOCALAPPDATA%\UyapIcra\sira_kapisi\bot_pc_icin`
- Bir **netsh** komutu. Bu komut bir sonraki adımda kullanılacak.

> Birden fazla bilgisayar kapıyı kullanacaksa `--bot-ip` her IP için ayrı ayrı yazılır.

### Adım 2 — cokluuyap PC'sinde güvenlik duvarı izni

Başlat menüsünde **cmd**'ye sağ tıklayıp **Yönetici olarak çalıştır**'ı seçin. Adım 1'in ekrana yazdığı komutu yapıştırın. Örnek:

```
netsh advfirewall firewall add rule name="cokluuyap sira kapisi" dir=in action=allow protocol=TCP localport=443 remoteip=192.168.1.50
```

Bu kural 443 portunu **yalnız bot PC'sine** açar.

### Adım 3 — Bot PC'sinde kurulum

1. `bot_pc_icin` klasörünü **USB bellekle** (ya da yalnız sizin erişebildiğiniz bir yerle) bot PC'sine kopyalayın. Herkesin yazabildiği ortak bir ağ paylaşımı kullanmayın: klasörü değiştiren biri yönetici olarak çalışacak kurulumu değiştirebilir. Klasörde beş dosya vardır: `KUR.bat`, `KUR.ps1`, `KALDIR.bat`, `KALDIR.ps1`, `cokluuyap_sira_kapisi_kok.cer`.
2. `KUR.bat`'a **sağ tıklayıp Yönetici olarak çalıştır**'ı seçin.
3. Pencerede **kök sertifika parmak izi (SHA256)** görünür. Bunu cokluuyap PC'sindeki `son_komut.txt` dosyasında yazan değerle karşılaştırın. Aynıysa `E` yazıp Enter'a basın. Farklıysa kurulumu durdurun.
4. "Tamam" mesajını görünce pencereyi kapatın.
5. Kurulum bitince klasörü USB'den ya da paylaşımdan **silin**. `KALDIR.bat` için bot PC'sinde bir kopya bırakabilirsiniz.

`KUR.bat` şunları yapar: sertifika dosyasının değiştirilmediğini denetler, onayınızı alır, eski cokluuyap köklerini silip yenisini kurar, hosts dosyasına tek bir satır ekler, DNS önbelleğini temizler. Sertifika dosyası değiştirilmişse hiçbir şey yapmadan durur.

> ⚠️ Kök sertifika ve hosts satırı **bilgisayarın tamamını** etkiler. Bot PC'sinde tarayıcıyla UYAP'a giren biri de cokluuyap üzerinden geçer. Kapı çerezleri ve gövdeleri kaydetmez, ama o kişinin trafiği cokluuyap PC'sinden geçer. Mümkünse bot PC'sinde insanlar UYAP'a girmesin.

> Antivirüs programı hosts dosyasının değiştirilmesi konusunda uyarı verebilir. Bu değişiklik bilerek yapılıyor. Eklenen satırın sonunda `# cokluuyap-sira-kapisi` notu bulunur.

### Adım 4 — cokluuyap paylaşımını yeniden başlatın

cokluuyap uygulamasında **Paylaşımı Durdur**'a, ardından **Paylaş**'a basın. Uygulamanın günlüğünde şu satır görünmelidir:

```
[i] Sıra kapısı açık: 192.168.1.10:443 mod=sirala aralık=1.0sn izinli=['192.168.1.50'] ...
```

Kapı yalnız Kapı IP'sinde dinler, `0.0.0.0` (tüm ağ kartları) üzerinde dinlemez.

## 4. Kontrol

1. Bot PC'sinde tarayıcıyla `https://avukat.uyap.gov.tr` adresini açın. Sayfa açılmalı. Adres çubuğundaki kilide tıklayınca sertifika **"cokluuyap Sira Kapisi"** olarak görünmeli.
2. Botu çalıştırın.
3. cokluuyap PC'sinde `%LOCALAPPDATA%\UyapIcra\sira_kapisi\kapi.log` dosyasını açın. Botun her isteği için bir satır olmalı:

```
2026-10-12 14:03:11 SIRA 192.168.1.50 POST /avukat_dosya_sorgula.ajx 200 bekleme=850ms sure=310ms
```

| Alan | Anlamı |
|---|---|
| `bekleme` | İsteğin sırada ne kadar beklediği |
| `sure` | UYAP'ın yanıt verme süresi |
| `CAKISMA` | Yalnız izleme modunda görünür. O anda bizim de bir isteğimizin UYAP'ta olduğunu gösterir. |

## 5. Günlük kullanım

- Kapı, **cokluuyap paylaşımı açıkken** kendiliğinden çalışır. Ayrıca bir şey yapmaya gerek yoktur.
- ⚠️ **cokluuyap kapalıysa ya da paylaşım durdurulmuşsa bot UYAP'a ulaşamaz.** Bot "bağlantı kurulamadı" türünden hatalar verir. cokluuyap PC'si uzun süre kapalı kalacaksa bot PC'sinde `KALDIR.bat` çalıştırın.

## 6. Ayarlar

Aşağıdaki komutlar cokluuyap PC'sinde çalıştırılır. Light kurulumda `python -m uyap_core.sira_kapisi` yerine `"%LOCALAPPDATA%\CokluUyap\CokluUyap.exe" --sira-kapisi` yazın (ör. `... --sira-kapisi durum`); sonuç Not Defteri'nde açılır. Değişiklikler **paylaşım yeniden başlatılınca** geçerli olur.

| Komut | Ne yapar |
|---|---|
| `python -m uyap_core.sira_kapisi durum` | Geçerli ayarları gösterir |
| `python -m uyap_core.sira_kapisi kapat` | Kapıyı kapatır (bot PC'sinde de `KALDIR.bat` çalıştırılmalı) |
| `python -m uyap_core.sira_kapisi ac` | Kapıyı yeniden açar |
| `python -m uyap_core.sira_kapisi mod izle` | **İzleme modu:** hiçbir isteği bekletmez, yalnız çakışmaları günlüğe yazar (ölçüm için) |
| `python -m uyap_core.sira_kapisi mod sirala` | Normal mod: istekleri sıraya alır |

İki istek arasındaki bekleme süresini değiştirmek için kurulumu `--aralik` ile tekrarlayın (ör. `--aralik 2`). Kurulum tekrarlandığında yeni bir sertifika üretilir, bu yüzden Adım 3'ü de yeniden yapın. Yeni `KUR.bat` eski kökü kendiliğinden siler.

**Sertifika süresi:** Kök ve sunucu sertifikası kurulumdan **825 gün (yaklaşık 2 yıl 3 ay)** sonra geçersiz olur. Bu tarihten önce kurulumu (Adım 1–3) tekrarlayın, yoksa bot "sertifika geçersiz" hatası verir.

Ayar dosyası: `%LOCALAPPDATA%\UyapIcra\sira_kapisi\ayar.json`

## 7. Eski hâle dönme

1. Bot PC'sinde `KALDIR.bat`'ı **yönetici olarak** çalıştırın. Bu dosya hosts satırını ve önceki kurulumlardan kalanlar dahil **tüm** cokluuyap kök sertifikalarını kaldırır. Bot yeniden doğrudan UYAP'a bağlanır.
2. cokluuyap PC'sinde `python -m uyap_core.sira_kapisi kapat` komutunu çalıştırın ve paylaşımı yeniden başlatın.
3. İsterseniz güvenlik duvarı kuralını da silin (yönetici cmd):
   `netsh advfirewall firewall delete rule name="cokluuyap sira kapisi"`

## 8. Sorun giderme

| Belirti | Olası neden / çözüm |
|---|---|
| Bot hiç bağlanamıyor, `kapi.log` boş | Paylaşım kapalı, güvenlik duvarı kuralı eksik (Adım 2) ya da Kapı IP'si yanlış. Bot PC'sinde `ping avukat.uyap.gov.tr` yazın; cokluuyap PC'sinin IP'si görünmeli. |
| `KUR.bat` "sertifika dosyasi degistirilmis" diyor | Klasördeki `.cer` dosyası kurulumdan sonra değişmiş. Klasörü kullanmayın. cokluuyap PC'sinde kurulumu (Adım 1) tekrarlayıp yeni klasörü USB ile taşıyın. |
| Günlükte `sunucu anahtarı açılamadı` | `sunucu.key` ya da `sunucu.parola` başka bir Windows hesabında veya bilgisayarda oluşturulmuş ya da bozulmuş. Kurulumu bu hesapla tekrarlayın (Adım 1 ve 3). |
| `kapi.log`'da `... sn içinde bitmedi` (bot 504 alır) | UYAP 3 dakika içinde yanıt vermedi. Kapı, bizim işlerimiz beklemesin diye isteği keser. Sık görülüyorsa UYAP yavaştır. |
| Bot "sertifika güvenilir değil / SSL hatası" veriyor | Program Windows sertifika deposunu kullanmıyor (Java tabanlı ya da kendi deposu var) veya sertifika sabitleme yapıyor. Bu durumda `KALDIR.bat` ile geri dönün ve programın türünü bildirin. |
| `kapi.log`'da `RET istemci=... (izinli değil)` | İstek, kurulumda verilmemiş bir IP'den geliyor. Bot PC'sinin IP'si değişmiş olabilir; kurulumu yeni IP ile tekrarlayın. Bot PC'sinin modemden **sabit IP** alması önerilir. |
| Günlükte `Sıra kapısı başlatılamadı` / `durdu` | 443 portunu başka bir program kullanıyor, sertifika yok ya da bu PC'nin IP'si değişti (kapı yalnız kurulumdaki Kapı IP'sinde dinler). Kurulumu (Adım 1) tekrarlayın. cokluuyap PC'sinin de **sabit IP** alması önerilir. |
| Günlükte `Sıra kapısı açılmadı: ...` | Ayar dosyasında izinli bot IP'si ya da geçerli bir LAN Kapı IP'si yok. Kapı bu durumda güvenlik gereği hiç açılmaz. Kurulumu `--bot-ip` ile tekrarlayın. |
| `kapi.log`'da `geçersiz hedef` ya da `gövde sınırı aşıldı` | Bot, UYAP dışında bir adrese ya da 100 MB'tan büyük bir istek göndermeye çalıştı. Kapı bunları reddeder. Sürekli görünüyorsa botun ayarını kontrol edin. |
| Günlükte `... bu bilgisayarda ... adresine çözülüyor (hosts?)` | hosts satırı yanlışlıkla **cokluuyap PC'sine** eklenmiş. Kapı kendine bağlanacağı için açılmaz. `KUR.bat` yalnız bot PC'sinde çalıştırılmalı; bu PC'de `KALDIR.bat` ile satırı silin. |
| Bot eskisinden yavaş | Beklenen bir durum: bot artık sıra bekliyor ve bizim işlerimize yol veriyor. Süreleri `kapi.log`'daki `bekleme` alanında görebilirsiniz. |
| Uyarılar hâlâ geliyor | Bir süre `mod izle` ile çalıştırıp `CAKISMA` satırlarına bakın. Aşağıdaki "Bilinen sınırlama"ya da göz atın. |

## 9. Bilinen sınırlama

- Bizim tarafımızdaki **sayfa yükleme (GET) istekleri sıraya girmez.** Kapı, bot isteğini göndermeden önce uçuştaki GET'lerimizin bitmesini en fazla 5 saniye bekler. Ama bot isteği UYAP'tayken bizim yeni bir sayfa yüklememiz başlarsa yine çakışma olabilir. POST işlemlerinde bu risk yoktur.
- Sıra kapısı **light (Çoklu UYAP) MSI kurulumunda vardır** (13.10.2026 sürümü). Derlenmiş (Nuitka) **UyapMasa** kurulumunda henüz yoktur.

## 9a. Güvenlik önlemleri (13.10.2026)

- Kapı **yalnız kurulumda verilen bot IP'lerinden** istek kabul eder. Liste boşsa kapı hiç açılmaz. İstemci adresi `X-Forwarded-For` gibi başlıklarla taklit edilemez.
- İstekler **yalnız `https://avukat.uyap.gov.tr`** adresine iletilir. Başka bir sunucuya yönlendirmeye çalışan istekler 400 ile reddedilir. UYAP sertifikası doğrulanır.
- Botun LAN adresini taşıyan `X-Forwarded-*` başlıkları UYAP'a iletilmez. Gövde uzunluğu yeniden hesaplanır.
- Tek istek en fazla 100 MB olabilir. Aynı anda en fazla 16 bağlantı kabul edilir.
- Kurulumdaki IP'ler doğrulanır. `KUR.bat` içine yalnız geçerli bir LAN IPv4 adresi yazılabilir.
- Sunucu anahtarı (`sunucu.key`) **parolayla şifreli** saklanır. Parola (`sunucu.parola`) Windows DPAPI ile yalnız bu Windows kullanıcısına ve bu bilgisayara bağlıdır. İki dosya birlikte kopyalansa bile başka bir hesapta ya da bilgisayarda açılamaz. Her iki dosya da yalnız bu kullanıcının okuyabileceği şekilde kısıtlanır. Kök sertifikanın özel anahtarı hiç diske yazılmaz. Önceki sürümle yapılmış kurulumlarda anahtar, paylaşım ilk açıldığında kendiliğinden şifrelenir.
- Kök sertifika artık 10 yıl değil, sunucu sertifikasıyla aynı gün (825 gün) geçersizleşir.
- Bot isteği bizim UYAP işlemlerimizi **en fazla 3 dakika** bekletebilir. UYAP yanıtı kilit içinde alınır, bota kilit bırakıldıktan sonra gönderilir. Yavaş okuyan bir bot cokluuyap'ı bekletemez. UYAP yanıtı 100 MB'ı aşarsa istek reddedilir.
- `KUR.bat`/`KALDIR.bat` sistem araçlarını tam yoluyla çağırır. Klasöre bırakılmış sahte bir `certutil.exe` ya da `powershell.exe` çalışmaz.
- Kurulum yapılmadıkça kapı **kapalıdır**. MSI'yi kurmak tek başına 443 portunu açmaz.

**Kodla tam çözülemeyen iki risk:**
- *Bot PC'sindeki insanlar:* hosts dosyası ve kök sertifika bilgisayarın tamamını etkiler. Kapı, botun isteğini aynı bilgisayardaki tarayıcının isteğinden ayıramaz. Önlem: bot PC'sini yalnız bot için kullanın.
- *IP taklidi:* bot kapalıyken ağdaki biri onun IP'sini alırsa kapıyı kullanabilir. Kendi UYAP oturumu olmadan bir şey elde edemez, ama istekleri ofis IP'sinden gider. Kapı bu istekleri de saniyede en fazla bir tane olacak şekilde sıraya alır. Önlem: modemde bot PC'sine **sabit IP (DHCP rezervasyonu)** verin. Güvenlik duvarı kuralını yalnız o IP'ye açık tutun. Düzenli olarak `kapi.log`'a bakın.

## 10. Dosyaların yeri

| Ne | Nerede |
|---|---|
| Kapının kodu | `cokluuyap\Uyap Haricen Giriş\uyap_core\sira_kapisi.py` |
| Testler | `uyap_core\test_sira_kapisi.py` (`python -m unittest uyap_core.test_sira_kapisi`) |
| Light kurulumda program | `%LOCALAPPDATA%\CokluUyap\CokluUyap.exe` (`--sira-kapisi ...`) |
| Son komutun çıktısı (light) | `%LOCALAPPDATA%\UyapIcra\sira_kapisi\son_komut.txt` |
| Ayar, sunucu sertifikası, günlük | `%LOCALAPPDATA%\UyapIcra\sira_kapisi\` |
| Bot PC'si kurulum paketi | `%LOCALAPPDATA%\UyapIcra\sira_kapisi\bot_pc_icin\` |
