Rust versiyonu ile ilgili isteklerim:

==================================================================================================
TEMEL İLKE — UYAP TASARIMINDAN TAM KOPUŞ (14.11.2026)
==================================================================================================
UYAP'ın tasarımına ilişkin fikri mülkiyetinden tamamen kopmuş olmamız gerekiyor.

- Hedef, UYAP'ın arayüzünü (sayfalarını, CSS'ini, logosunu, temasını, pencerelerini, hata mesajlarını)
  boyayıp üzerine yama yapmak DEĞİL. UYAP ekranlarını yeniden renklendirmek, CSS'ini ezmek, logosunu
  gizleyip yerine bizimkini koymak geçici bir ara çözümdür; nihai ürün bu değildir.
- Nihai ürün: kendi tasarımımız, kendi ekranlarımız, kendi veritabanımız. UYAP'tan yalnızca VERİ alınır
  (dosya, taraf, evrak, tebligat, safahat vb.); bu veri bizim arayüzümüzde, bizim tasarımımızla gösterilir.
- Kullanıcı hiçbir noktada UYAP'ın görsel tasarımını görmemeli: giriş sayfası, ana sayfa, dosya penceresi,
  menüler, uyarı/hata pencereleri, yükleme animasyonları dahil.
- Yeni geliştirmeler "UYAP sayfasına enjekte et / UYAP CSS'ini değiştir" yaklaşımıyla değil, kendi ekranımız
  olarak tasarlanacak. Mevcut enjekte katmanı yalnızca kendi ekranlarımız hazır olana kadar kullanılacak.
- Proje bittiğinde UYAP'ın HİÇBİR tasarım ve tema öğesi kullanılmamış olacak: renkleri, gri tonlamaları,
  zemin/fon renkleri, yazı tipleri, simgeleri, logosu, düzeni, pencere biçimleri dahil. Bu fikri mülkiyete
  ve o tasarımı yapanların emeğine saygının gereğidir.
- Arada kalan hiçbir alan UYAP'ın gri fonuyla bırakılmayacak (ör. dosya durumu / "Haricen Tahsil" yazan
  kısmın gri arka planı). Her yüzey bizim renk paletimizden gelecek.
- Bu arayüz bizim arayüzümüzdür; "UYAP'ın teması / UYAP'ın CSS'i" diye değil, kendi tasarımımız olarak ele
  alınacak ve öyle anlatılacak.
==================================================================================================

A) UYAP Arayüz tasarımı

Ya en önemlisi; pin kodu girme ve giriş yapma kısmında fare üzerinde olduğu yere tıkladığımda altında ya da üstünde bir yere tıklıyor. Olduğu yere tıklamıyor.
Kapama tuşu çalışmıyor
Round köşeli bir arayüz çıksa da round dışında kalan kısım siyah.

1) Hata mesajları hala uyap tasarımının aynısı. Fikri mülkiyet gereği bunu değiştirmemiz gerekiyor. Belge yükleme esnasında iki tane animasyon çıkıyor. Biri gri tonlu,
öbürü bizim cokluuyap tasarım renginde.

2) Dosya penceresinde belge görüntüleme sığdırma hatası alıyorum zaman zaman. Bu da yine uyap hata mesaj penceresi formatında.

3) Aramaya ilişkin şöyle bir isteğim var; tek bir satır ile taraf gerçek kişi ise de tüzel kişi ise de ad soyad ya da adı ile, tckn mersis vergi numarası ile,
birim adı ile, dosya numarası ile arama yapabilsin. Her birim ve açık kapalı vs fark etmeksizin. Tek arama satırı. (Bunun için postgreSQL modellerinin kurulması gerekecek; 
uyapmasa içinde buna uygun birtakım modeller ve fonksiyonlar vardı; şimdilik bunu en sonlara doğru çözelim.)

4) Arama çubuğunda arama yapılırken birden fazla sonuç çıktığında dosya arama sayfasına gitmesin. Arama bannerının altında bir kısım açılarak listeleme gelsin. 
Kullanıcı bu banner altı arama sonuçlarını isterse kapatabilsin. Tabi arama sonuçları kartı kesinlikle banner ya da alttaki bildirim duruşma kefiş kısmını kapatmasın. 
Kullanıcı mümkün mertebe ana ekrandan kopmadan ilerlesin. Dosya penceresi zaten ana ekranın üstüne açılıyor.

5) Ana ekrandaki bildirimler ve duruşma/keşif günleri ekranları hala uyap tasarımının aynısı (Bir de bildirimlerde +-iş diye text yazıyor) Bizim iş listemiz var.
Ana ekranda tek bir çizelge ile giriş yapan kullanıcının zaman yönetimini, bilgi alması gereken şeyleri, takip ettiği işleri, takvimi kolayi okunur bir şekilde 
karşısına çıksın istiyorum.

6) Sol menü hala uyapın aynısı. O kısımda uyapın birebir verdiği hizmetlerin avukat için en acil olanını tutacağım ve sonra kendi eklediğimiz modülleri oraya ekleyeceğim.

7) Yeni logomuz var, bu logoyu kullanalım.

8) Giriş yapılmamış ise 127.0.0.1:8800 sayfası uyap giriş olarak açılıyor. Yine fikri mülkiyet konusunda sorun yaşatır.

9) Dosya görüntüleme penceresi hala UYAP'ın tasarımını kullanıyor. Veritabanı geliştirildikten sonra ya da önce dosya penceresi tasarımı oluşturulacak.

10) Belge indirme, iş atama, iş listesi, imzalama tasarımı temayla uyumsuz (yeşil ve mor butonlar var)

11) Ana menüden bağlantılı olan bir sayfa açıldığında (ana menüye tıklanarak) ana menü daha önce açıksa üst üste binme oluyor. Ana menüye katı sabit bir şekilde alan ayırmamız gerekiyor. 
Bu kaymalar çarpışmalar çok sorunlu. Açılmamış takiplerde evrak gönder butonuna tıkladığımda bir pencere açılıyor ve ana menünün arkasında yer alıyor. Görev çubuğu 
kaybolmuş durumda.

12) Görev çubuğu da yine üst üste binme konusunda çok başarılı değil. 

13) Dosya penceresi dışında kalan alanlarda hala fon ve tasarım rengi uyap rengi. Temayı buralara uygulamayı unutmuşuz.

12) Mobil ile ilgili arayüz sorunları:
a) Sarı temada dosya penceresi açıldığında sekmeler butonuna tıkladığımda hala kahverengi fon, siyah yazı var.
b) Anasayfa dışında ana menü açıldığında üst üste binme oluyor.
c) Dosya penceresi açıkken ana menü açıldığında dosya penceresinin altında açılıyor. Genel olarak ana menünün çakışması söz konusu.


B) İŞ LİSTESİ

1) İş listesi veritabanı ana bilgisayar sunucusunda değil render sunucudaymış. Bu nedenle tıklama sonrası sonuç alma uzun sürüyor. Çok katı ve donuk. 
Veritabanı kısmını yeniden değerlendirmemiz gerekiyor zaten. 

2) İş listesi farklı bir sayfada açılıyor. Bu belki kalabilir ama arayüzünün değişmesi gerekecek. Hatta veritabanı modelinin de değişmesi gerekecek.

3) İş listesinde her işi onaya sunma kısmı sıkıntılı buna bir çözüm bulmamız gerekiyor.

4) İş listesinin telegram ve chrome bildirimi kısmı güvenlik açısından bir sorun teşkilş ediyor mu bilmiyorum.

5) İş listesinde de arama çubuğu olmalı. Yine detaylar PostreSQL ile iş listesi ve genel veritabanı kısmında bir çalışma yaptıktan sonra belli olacak.

6) Dosyalar ve belgeler ile bağlantılı olarak iş atama şablonları eklenecek. Yani örnek olarak; "avukat portal tebligat talebi" 
başlıklı belgede tebligat oluşturuldu mu kontrol et işi iş eklerken şablonlar arasında çıksın gibi mesela. Bu da veritabanı oluştuğunda birbirine bağlı işler olarak
oluşturulacak. Şu an icra takip kesinleştirme kısmında var ama yeterli değil gibi. Sonraki işi oluştur da string olarak geliyor.

7) Üye onaya sunduğunda yeni iş önerisi gelmiyor. Bu noktada iş silsilesi yeniden oluşturulduğunda bir çözüm üretilecek.

8) Sonraki iş için sadece gün int olarak alınıyor. Bu yanlış. Günü belirlemek lazım.

9) İş listesine iş eklerken saat eklemek şart mı? Sürekli gecikme oluyor.

10) Bazı işler tekrar eden işlerden. Yani her gün kontrol edilmesi gereken, her ay başında kontrol edilmesi gereken işler var. Bunun için her gün ertesi güne aktar
biraz yorucu oluyor.

11) Kullanıcının rutinine ilişkin de takvimde yer olsun.

12) Duruşma, keşif, haciz de iş listesinde yer edinecek. Bu da veritabanıyla çözülecek. Duruşmalarım/keşif günlerim ile bildirimler ekranı nihayetinde iş listesi
ve takvim ile birleşecek.

13) Veritabanının farklı serverda olması api ile veri aktarılması ciddi yavaşlamaya neden oluyor. 

14) İş ataması yaparken ve işi onaya/ertesi güne sunarken kaydet tuşundan sonra pencere eski veriler sıfırlanmış bir şekilde kalmaya devam ediyor.
Bir uyarı penceresi gösterip onay ile kapanmalı.

15) İş listesinin kendisine özgü olan sayfasında işe tıkladığımızda ertesi güne aktar kısmı yok. İş kartı açıldığında sağdan üst üste binme oluyor. Bu da tasarım 
açısından sorun.

16) Canımı en çok sıkan konu: iş listesi ayrı sayfasında açılan bir işte uyapta aç tıklandığında cokluuyap.com'a gidiyor. Belki o oturum 127.0.0.1:8800 ile bağlandı?
İşte bu yüzden tek veritabanı önemli.

17) İş listesi ayrı sayfada önceki günden kalan iş sadece bir önceki günü gösteriyor. Buna gerek yok.

18) En önemlisi yeni ve doğru düzgün veritabanı modeli oluşturup buna göre yeni bir sistem kurmak.

19) İş silsilesi manuel olsa da default değerler olmalı.

20) Süreçler kısmı yeterli değil. Veritabanı oluştuktan sonra yeniden düzenlenecek.

------------------------------------------------------------------------------------------------

Dosya Penceresi ve Dosya Veritabanı Tasarımı:

1) Tasarım yenilenecek. Uyap tasarımının aynısı olduğu için fikri mülkiyet ve yapan kişinin emeğine saygısızlık.

Önerim daha az sekme ile hem sade hem de verinin derli toplu durduğu; başka bir otomasyona gerek kalmadan tek bir ekrandan tüm işlerin halledildiği bir sistem.

Dosya penceresi dosya bilgileri, taraf bilgileri gibi birer ikişer satırlık veriler içeren iki sekme ile başlıyor. Mesela basit bir künye ile dosya veritabanının
içerdiği verilerin de yer aldığı; tek veritabanı ile çalışacak ve fakat uyapla o veriler çeliştiğinde kontrol sağlatacak bir künye ekranı.

Dosyanın açılış bilgileri, türü, tipi, tarafları, tarafların rolü, dosyanın mevcut durumu gibi kilit tüm bilgiler ilk sekmede yer alsın.

İCRA DOSYASI DOSYA PENCERESİ:

1) İcra dosyalarında dosya bilgileri dosya görüntüleme ekranında bir kısım, dosya penceresinin ilk iki sekmesinde bir kısım olarak yer alıyor. Dosya bilgileri, dosya hesabı, taraflar üç sekme. 
Bir dosya kartında bu üç sekmenin verileri çok rahat sığar. İyi bir tasarım olması yeter.

2) İcra takibi takip talebi ile başlar, icra dairesi ödeme icra emrini hazırlar, tebligat hazırlanır ve borçluya gönderilmek üzere tebligat pttye teslim edilir.

3) Tebligat yolda olduğunda ptt online sisteminde veri takip edilir. Tebligat yapıldığında ya da iade olduğunda mazbata dönüşü beklenir.

4) Tebligat iade ise ve mazbata dönmüşse yeniden tebligat prosedüründen devam edilir.

5) Tebligat yapılmışsa ve mazbata dönmüşse itiraz süresi ve kesinleşme koşullarına göre kesinleştirme talebi gönderilir; kesinleştirme kabul tensip kararının oluşması
beklenir, karar oluştuktan sonra sorgular yapılır.

6) Şayet ilgili dosyada borca itiraz dilekçesi verilmişse ve/veya dosya durumu borca itiraz içeriyorsa (Uyaptaki doğru tanıma bakarız) veritabanı modelinde durumu itiraz
olarak kaydedilir.

7) Bu durumda bir icra takibinin durum verisi "Takip Talebi" "Ödeme Emri" "Ödeme Emri Tebligat Oluşturuluyor" "Ödeme Emri PTT'ye Veriliyor" "Ödeme Emri Tebliğe Çıktı"
"Tebliğ" , "İade" "İtiraz gibi değerler alıyor takip kesinleşene kadar. İtiraz halinde dava açılması prosedürü ayrı. Onu sonra detyalyı anlatacağım.
Özetle veritabanında dosyanın kesinleşene kadar olan kısmı yapılacak iş listesini, iş listesi silsilesini etkiliyor. Bu nedenle postgreSQL ile hem icra veritabanı
hem de iş listesi veritabanı belirli noktalarda ortak hareket etmek zorunda. Takip kesinleştikten sonra ve hatta her dosya türünde iş listesi veritabanı ilgili dosya türü ile 
ortak hareket etmeli.