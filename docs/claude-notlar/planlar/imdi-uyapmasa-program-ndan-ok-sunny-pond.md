# Kendi logomuz + ana sayfa kartlarının "ajanda" görünümü

## Bağlam
Fikri mülkiyet nedeniyle arayüzden UYAP'a özgü görseller çıkarılıyor. Yükleme ekranındaki logo kaldırıldı ve yazı tipi değiştirildi; bunlar kuruldu. Bu aşamada iki iş var:
1. Sol üstteki UYAP logosunun yerine kendi logomuz gelecek. Kullanıcının seçimi: üst üste binmiş üç pencere ve yanında "Çoklu UYAP / büro paneli" yazısı.
2. Ana sayfadaki **Bildirimler** ve **Yaklaşan Duruşmalarım** kartları "ajanda tarzı"na geçecek.

Dosya penceresi bu aşamanın kapsamında değil (kullanıcı: "şimdilik bakmayalım").

Kural: UYAP'ın kendi kartları ve öğeleri yerinde kalır, paralel bir ekran yazılmaz. Değişiklik CSS ile ve React'in öğelerine küçük ek öğeler koyarak yapılır; React'in kendi metinlerine dokunulmaz.

## UYAP tarafındaki yapı (main.js'ten çıkarıldı)
- **Logo:** `nav.u-navbar > a.navbar-brand.uyap-logo > h1.logo-content > div.logo-title`. Metin `H_CONST_LOGO_TITLE = "UYAP"`; logo görseli UYAP'ın CDN'deki CSS'inden geliyor ve önbellekte yok. Kurumsal logo tanımlıysa yerinde `div.layout-custom-logo` (img + başlık) çıkıyor.
- **Bildirimler:** `.notify-area` içinde `AdaletCard` (`.main-page-notifications.tour-4`). Liste `ul.notify-list > li.notify-item`; her satırda şunlar var:
  - `.icon-list--circle-icon` (zil simgesi)
  - `span.icon-list-baslik` (metni "Başlık -")
  - `span.icon-list-date` (dd.MM.yyyy HH:mm)
  - Tooltip içinde `span.icon-list-text` (mesaj)
  - En fazla 5 kayıt; liste boşken `.empty-state-card`.
- **Duruşmalar:** `.tour-6` içinde `AdaletCard` → `AdaletScrollView` (355px) → `.durusmalarim` → `ul.schedule-list > li`. Her satırda şunlar var:
  - `div.dot-circle.dot-circle--{type}`
  - `span.icon-list-item > strong` (tarih)
  - `span.icon-list-text` (başlık; tıklanınca duruşma sayfasına gider)
  - `span.icon-list-tag` (tür ve durum rozetleri)
  - Liste boşken `.empty-state-card`.

## Yapılacaklar

### 1) Logo: `uyap_core/enjekte/tema.js` + `tema.css`
- `tema.js` içine `logoKur()` eklenecek ve mevcut `tur()` döngüsüne (açılışta, DOMContentLoaded'da, 500 ms'de bir) katılacak. React üst çubuğu yeniden çizerse logo tekrar eklenir.
  - Döngü `.u-navbar .navbar-brand.uyap-logo .logo-content` öğesini bulur. İçinde `.cu-logo` yoksa bir kez ekler. Logo, inline SVG ile iki satır yazıdan oluşur.
  - Sol menü daraltıldığında yazı gizlenir, yalnızca işaret kalır (UYAP'ın `d-none d-sm-block` mantığıyla aynı, CSS medya kuralıyla).
- SVG (yaklaşık 34×30, viewBox 0 0 34 30): köşeleri yuvarlatılmış üç dikdörtgen, çapraz kaydırılmış.
  - Arka ikisi yalnızca çizgi; renk `var(--cu-ust-yazi)` yarı saydam.
  - Öndeki dolu; renk `var(--cu-vurgu-parlak)`, üstünde ince bir başlık çubuğu çizgisi var.
  - Renkler CSS değişkeninden geldiği için 9 temada da kendiliğinden uyar. Güneş temasında yazı koyu olur (`--cu-ust-yazi`).
- Yazı:
  - "Çoklu UYAP": 17px, kalınlık 600, `var(--cu-ust-yazi)`.
  - "büro paneli": 10.5px, büyük harf, harf aralığı .08em, `var(--cu-ust-soluk)`.
- `tema.css` kuralları:
  - `.uyap-logo .logo-content > :not(.cu-logo){display:none!important}`
  - `.uyap-logo, .uyap-logo .logo-content, .uyap-logo .logo-title {background-image:none!important}`, böylece CSS'ten gelen UYAP görseli de gizlenir.
  - `.cu-logo` için flex, 10px aralık, 60px yükseklik ve dikey ortalama.

### 2) Kartlar: `uyap_core/enjekte/anasayfa.js` (stil, mevcut `stilKur()` dizisine)
Ortak görünüm:
- Kart yüzeyi `var(--cu-t-yuzey)`, kenar `var(--cu-t-kenar)`, köşeler 10px, gölge hafif.
- Başlık 14px, kalınlık 600; solunda ince bir vurgu çizgisi (`::before`).
- "Tümünü Listele" bağlantısı vurgu renginde ve küçük.

**Yaklaşan Duruşmalar (ajanda)**
- Yeni `durusmaSusle()` işlevi, `altTemizle()` ile aynı döngüde çalışacak ve her `ul.schedule-list > li` için şunları yapacak:
  - `strong` içindeki tarih metnini okur (format canlıda doğrulanacak; dd.MM.yyyy ve dd.MM.yyyy HH:mm ikisi de desteklenecek).
  - Satırın başına `<span class="cu-ajanda-gun"><b>14</b><i>KAS</i></span>` ekler.
  - `.icon-list-tag` içine göreli zaman etiketi ekler: `<span class="cu-ajanda-goreli">Bugün | Yarın | N gün | Geçti</span>`.
  - Saat `strong`'dan alınıp başlık satırında "· 10:30" olarak gösterilir. Bu, eklenen bir `span` ile yapılır; React'in metnine dokunulmaz.
  - Satır bugüne aitse `li.cu-bugun` sınıfı eklenir: zemin `--cu-vurgu-zemin`, solda vurgu kenarı.
  - Satırı işlenmiş olarak işaretlemek için `li.dataset.cuTarih = <tarih metni>` yazılır. Metin değişirse (React yeniden çizerse) eklenen öğeler silinip yeniden oluşturulur, böylece işlem tekrar tekrar güvenle çalışır.
- CSS:
  - `li` iki sütunlu grid: 48px gün kutusu ve içerik.
  - `.dot-circle` ve `.icon-list-item` gizlenir; tarih artık gün kutusunda.
  - Gün kutusu: gün 20px kalın, ay 10px büyük harf; zemin `--cu-vurgu-zemin`, yazı `--cu-vurgu-yazi`.
  - Başlık 13px, kalınlık 600, en fazla 2 satır.
  - Rozetler küçük ve hap biçiminde.
  - "Bugün" etiketi vurgu rengiyle dolu, "Yarın" çizgili, diğerleri soluk.

**Bildirimler (sık liste)**
- Yalnızca CSS ile yapılacak, iki küçük ek dışında:
  - `li.notify-item` grid düzeninde: üstte başlık (sol) ve tarih (sağ), altta mesaj (iki satır, `-webkit-line-clamp:2`, soluk renk).
  - `.icon-list--circle-icon` gizlenir; yerine 3px vurgu renkli sol kenar gelir. Satırlar ince çizgilerle ayrılır, üstüne gelince zemin `--cu-vurgu-zemin` olur.
  - Tarih 11.5px, soluk, eşit genişlikte rakamlar (`tabular-nums`).
  - Başlığın sonundaki " -" React metninde duruyor. JS ile `li.dataset.cuBaslik` değerine temiz başlık yazılır, CSS `.icon-list-baslik` öğesini gizleyip başlığı `::before{content:attr(data-cu-baslik)}` ile gösterir. Bu iş de `durusmaSusle()` ile aynı döngüde yapılır.
- Boş durum (`.empty-state-card`): simge küçültülür, yazı soluk ve 13px olur. İki kartta da aynı.

**Koyu tema:** Bütün renkler `--cu-*` değişkenlerinden geliyor. Gün kutusu ve "Bugün" vurgusunun okunurluğu `html[data-cu-tema="koyu"]` kuralıyla ayrıca denetlenir.

## Değişecek dosyalar
- `Uyap Haricen Giriş/uyap_core/enjekte/tema.js`: `logoKur()`, `tur()` içine ekleme.
- `Uyap Haricen Giriş/uyap_core/enjekte/tema.css`: logo kuralları.
- `Uyap Haricen Giriş/uyap_core/enjekte/anasayfa.js`: `durusmaSusle()`, `stilKur()` içine kart CSS'i, döngüye ekleme.

`uyap_proxy.py` değişmiyor; ek dosyalar `INJECT_VERSION`'a zaten katılıyor.

## Doğrulama
- `node --check` ile tema.js ve anasayfa.js denetlenecek. Tarih ayrıştırma ve göreli etiket mantığı Node'da birkaç örnekle (bugün, yarın, 5 gün sonra, geçmiş, saatli ve saatsiz) denenecek.
- Bilinen kalıpla derlenip kurulacak: PyInstaller → robocopy → yeniden başlatma. Kullanıcı bağlantıyı programdan yeniden kuracak.
- Canlı kontrol:
  - Yeni sekme ana sayfada sorgu tetikleyip "eş zamanlı sorgu" uyarısına yol açabileceği için kullanıcıya sorulmadan sekme açılmayacak.
  - Kullanıcı açık sekmesinde bakacak: logo üç temada (Koyu, UYAP, Güneş); sol menü daraltılmışken; duruşma ve bildirim kartlarının dolu ve boş halleri.
  - Duruşma tarihi beklenen formatta değilse (gün kutusu çıkmazsa) yalnızca okuma yapan tek bir JS komutuyla `strong` metni görülüp ayrıştırıcı düzeltilecek.
