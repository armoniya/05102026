Aşağıda **Debian 13 (trixie)** tabanlı, `live-build` ile üretilen ve `toram` parametresiyle RAM'den çalışan **UYAP (Ulusal Yargı Ağı Bilişim Sistemi) avukat bürosu kiosku** için hazırlanmış **USBGuard** yapılandırma taslağı yer almaktadır. Uygulama planı ayrıca [implementation_plan.md](file:///C:/Users/W%C4%B0N10/.gemini/antigravity-cli/brain/c9e39b38-5fc7-422d-8d64-c716a2dd439b/implementation_plan.md) artefaktına kaydedilmiştir.

> **Canlı İmaj (`live-build`) Entegrasyon Notu:** Aşağıdaki tüm `/etc/...` dosya yolları, çalışan canlı sistemdeki hedef yollardır. `live-build` proje ağacında bu dosyalar `config/includes.chroot/etc/...` altına aynı dizin yapısıyla yerleştirilmeli ve imaj paket listesine (`config/package-lists/kiosk.list.chroot`) `usbguard`, `pcscd`, `pcsc-tools` ve `opensc` eklenmelidir (**Polkit / D-Bus üzerinden yetki yükseltme yüzeyini kapatmak için `usbguard-dbus` paketi kurulmamalıdır**).

---

## 1. `/etc/usbguard/usbguard-daemon.conf` Önemli Ayarları

Kiosk ortamında temel prensip **"varsayılan olarak engelle (default-deny)"**, **"açılışta takılı olanlar dahil tüm aygıtlara kural uygula"** ve **"IPC soketini yalnızca `root` kullanıcısına aç"** yaklaşımıdır.

```dosya:/etc/usbguard/usbguard-daemon.conf
# ==============================================================================
# Debian 13 (trixie) UYAP Canlı Kiosk - USBGuard Daemon Yapılandırması
# İzinler: chmod 0600 /etc/usbguard/usbguard-daemon.conf (root:root)
# ==============================================================================

# Canlı imajdan (SquashFS) gelen sabit kural dosyası
RuleFile=/etc/usbguard/rules.conf

# Kural dosyasında hiçbir kural ile eşleşmeyen aygıtlar için varsayılan hedef:
# "block" -> aygıtı yetkilendirmez (authorized=0), ancak lsusb/usbguard listesinde
# görünür tutar (B seçeneğinde yönetici onayı için gereklidir).
ImplicitPolicyTarget=block

# Sistem açılışında (usbguard-daemon başlarken) halihazırda takılı olan USB aygıtları:
PresentDevicePolicy=apply-policy

# Sistem açılışında mevcut olan dahili USB kök denetleyicileri (xHCI/EHCI):
PresentControllerPolicy=keep

# Daemon çalışırken sonradan takılan (hotplug) USB aygıtları:
InsertedDevicePolicy=apply-policy

# Daemon durdurulduğunda/çöktüğünde denetleyici durumunu eski haline döndürme:
# "false" -> servis dursa bile engellenmiş aygıtlar otomatik yetkilendirilmez.
RestoreControllerDeviceState=false

# Çekirdek aygıt yöneticisi arka ucu (Debian 13 systemd/udev uyumlu standart):
DeviceManagerBackend=uevent

# --- IPC ERİŞİM KONTROLÜ (Kiosk kullanıcısı ve Polkit tamamen devre dışı) ---
# Yalnızca root kullanıcısı usbguard CLI / IPC soketine (/run/usbguard.ipc) erişebilir.
IPCAllowedUsers=root

# Hiçbir gruba (plugdev, sudo, netdev vb.) IPC erişimi verilmez (boş bırakılmalıdır).
IPCAllowedGroups=

# Ek IPC erişim kontrol dizini (içerisi boş tutulmalıdır):
IPCAccessControlFiles=/etc/usbguard/IPCAccessControl.d/

# Kurallarda fiziksel USB port yolunu (via-port) zorunlu tutma:
# Canlı imaj farklı kiosk kasalarında veya farklı portlarda çalışabileceği için "false".
DeviceRulesWithPort=false

# Denetim (Audit) kayıtları:
AuditBackend=FileAudit
AuditFilePath=/var/log/usbguard/usbguard-audit.log

# Adli inceleme ve hata ayıklama için seri no / hash değerlerini loglarda gizleme:
HidePII=false
```

---

## 2. Açılışta Bağlı Aygıtların Durumu ve Canlı Sistemde `generate-policy` Analizi

### 2.1. `PresentDevicePolicy`, `PresentControllerPolicy`, `InsertedDevicePolicy` İçin Gerekçeli Öneri

| Ayar | Önerilen Değer | Teknik ve Güvenlik Gerekçesi |
| :--- | :--- | :--- |
| **`PresentDevicePolicy`** | `apply-policy` | **Soğuk Açılış (Boot-time Bypass) Saldırılarını Önler:** Eğer bu değer `keep` veya `allow` olarak bırakılırsa, fiziksel erişimi olan bir kişi kiosku güç düğmesinden kapatıp yasaklı bir USB ağ adaptörü (`02`/`0a`), klavye+depolama birleşik BadUSB aygıtı veya USB bellek (`08`) takarak sistemi yeniden başlatabilir ve `rules.conf` kurallarını tamamen atlatabilir. `apply-policy` ile açılışta takılı olan aygıtlar da tıpkı sonradan takılanlar gibi `rules.conf` süzgecinden geçirilir.<br><br>**`toram` İlişkisi:** Kiosk bir USB bellek üzerinden canlı olarak başlatılıyorsa bile `toram` parametresi sayesinde `initramfs` aşamasında SquashFS imajı RAM'e kopyalanır ve kök dosya sistemi RAM üzerinden bağlanır. Ardından `multi-user.target` aşamasında `usbguard.service` devreye girdiğinde kurulum USB'si (`08:*:*`) engellense bile çalışan sistem etkilenmez; aksine önyükleme medyasının çalışma zamanında okunması/değiştirilmesi engellenmiş olur. |
| **`PresentControllerPolicy`** | `keep` | **Anakart USB Denetleyicilerinin Kilitlenmesini Önler:** PCI/ACPI veriyoluna bağlı dahili USB Host Controller'ların (xHCI/EHCI) çekirdek tarafından başlatıldığı durumu korur. Burada `block` veya hatalı yapılandırılmış bir `apply-policy` kullanılması, anakartın tüm USB kök hub'larını devre dışı bırakarak klavye, fare ve e-imza okuyucusunun tamamen çalışmamasına yol açar. Uç aygıtlar zaten `PresentDevicePolicy=apply-policy` ile denetlenmektedir. |
| **`InsertedDevicePolicy`** | `apply-policy` | **Dinamik E-İmza (AKİS) Kullanımını Sağlar:** Avukatlar UYAP'a giriş yaparken ve `.udf` belgelerini imzalarken kendi e-imza tokenlarını oturum esnasında takıp çıkarırlar. `InsertedDevicePolicy=block` yapılırsa sonradan takılan her şey kurallara bakılmaksızın reddedilir. `apply-policy` sayesinde sonradan takılan e-imza tokenı `rules.conf` içindeki `0b:*:*` kuralıyla eşleşerek yetkilendirilir, kural dışı her şey ise `ImplicitPolicyTarget=block` (veya `reject` kuralları) ile engellenir. |

### 2.2. `usbguard generate-policy` Neden Canlı (`live-build`, `toram`) Kiosk İçin Uygun Değildir?

1. **Uçucu (Ephemeral) ve Salt Okunur Dosya Sistemi:** `live-build` + `toram` mimarisinde kök dosya sistemi RAM'deki sıkıştırılmış ve salt okunur `SquashFS` imajından ve üstündeki geçici `overlayfs` (`tmpfs`) katmanından oluşur. Çalışan sistemde `usbguard generate-policy > /etc/usbguard/rules.conf` çalıştırmak yalnızca RAM'deki geçici katmanı günceller; kiosk yeniden başlatıldığında tüm kurallar imajdaki haline döner.
2. **Donanım Örneklerine Bağımlı Hash ve Port Bağlaması:** `usbguard generate-policy` komutu o an takılı olan çevre birimleri için `id`, `serial`, `hash`, `parent-hash` ve `via-port` içeren birebir kurallar üretir:
   - Aynı canlı imaj bürodaki 5 farklı kiosk bilgisayarına yüklendiğinde veya bozulan bir klavye yenisiyle değiştirildiğinde seri numarası, `hash` veya anakart USB kontrolcüsünün `parent-hash` değeri değişeceğinden klavye/fare engellenir.
3. **Avukatların Bireysel E-İmza Tokenları (TÜBİTAK AKİS):** İmaj derlenirken (`lb build` chroot aşamasında) veya kurulum anında, büroya gelecek avukatların kişisel e-imza tokenları (farklı üretici, seri numarası ve `hash` değerine sahip kart okuyucular) cihaza takılı değildir. Dolayısıyla cihaz bazlı statik envanter yerine **USB arayüz sınıfı (`with-interface`)** bazlı deterministik kural seti kullanmak zorunludur.
4. **Derleme Ortamı Farklılığı:** `live-build` imajı bir derleme sunucusunda veya yönetici iş istasyonunda üretilirken `generate-policy` çalıştırılırsa hedef kioskun değil, derleme makinesinin USB donanımı kural dosyasına yazılır.

---

## 3. BadUSB / Rubber Ducky Tehdit Modeli ve Karşı Önlem Tartışması

USB klavye ve fareler **HID (`03`)** sınıfını kullanır (`03:01:01` Boot Klavye, `03:01:02` Boot Fare, `03:00:*` Genel HID). "Klavye gibi davranan" kötü amaçlı aygıtlar (BadUSB, Hak5 Rubber Ducky, Bash Bunny, O.MG Cable, Flipper Zero) iki kategoride incelenmelidir:

### 3.1. USBGuard İle Doğrudan Engellenebilen Saldırılar: Bileşik (Composite) BadUSB Aygıtları
Saldırganların sık kullandığı yöntemlerden biri, görünürde USB bellek (`08`), USB Ethernet (`02`/`0a`), ses kartı (`01`) veya satıcıya özgü aygıt (`ff`) gibi davranırken ikinci bir USB arayüzü olarak gizli bir klavye (`03:01:01`) tanımlamaktır.
* **Yanlış Kural (`with-interface one-of` veya gevşek eşleşme):** Eğer kural `allow with-interface one-of { 03:*:* }` şeklinde yazılırsa, içinde hem yığın depolama hem klavye (`{ 08:06:50 03:01:01 }`) barındıran bir BadUSB aygıtı klavye arayüzü sayesinde onay alır!
* **Doğru Kural (`with-interface equals` + Öncelikli `reject`):**
  1. Kural dosyasının **en başında** (USBGuard kuralları yukarıdan aşağıya ilk eşleşme — *first-match* — mantığıyla çalıştırır) HID (`03:*:*`) ile başka herhangi bir tehlikeli sınıfı (`08`, `02`, `0a`, `01`, `0e`, `e0`, `ff`, `ef`) aynı aygıtta birleştiren bileşik aygıtlar kesin olarak **`reject`** edilir.
  2. HID izin kurallarında **`with-interface equals { ... }`** operatörü kullanılır. Örneğin `allow with-interface equals { 03:01:01 }` kuralı, **yalnızca ve yalnızca tek bir arayüzü olan ve o arayüzü de `03:01:01` olan** aygıtlara izin verir. İçinde ek olarak `08:06:50` veya `ff:00:00` barındıran hiçbir aygıt bu kuraldan geçemez.

### 3.2. Saf HID Taklidi (Pure HID Spoofing) Sınırları ve Katmanlı Önlemler
Klasik bir USB Rubber Ducky kendisini yalnızca tek arayüzlü bir klavye (`03:01:01`) olarak tanıtır ve bilindik bir üreticinin `VID:PID` değerini taklit edebilir. Sadece sınıf bazlı (`03:01:01`) izin verildiğinde USB katmanı bu aygıtı gerçek bir klavyeden ayırt edemez. Buna karşı kioskta uygulanması gereken ek önlemler:
1. **Büro Klavye/Fare `VID:PID` Sabitlemesi (Önerilen):** Bürodaki tüm kiosklarda aynı marka/model klavye ve fare kullanılıyorsa, genel `03:*:*` kuralı yerine yalnızca o donanımların kimliklerine (`id <KLAVYE_VID:PID_DOĞRULANACAK>`) izin verilmelidir (kural dosyalarında ilgili satırlar hazır bırakılmıştır).
2. **Fiziksel Port Ayrımı / Kilitli Kasa:** Klavye ve fare kasanın kilitli arka portlarına takılmalı; avukatların erişimine açık ön paneldeki USB uzatma portunda (varsa ayrı USB kök denetleyicisi üzerinden) yalnızca `0b:*:*` (AKİS e-imza) ve (Seçenek B/C'de) `08:*:*` izinli olmalıdır.
3. **Kiosk Oturum ve Pencere Yöneticisi Kısıtlaması:** Saldırgan klavye enjeksiyonuyla `Alt+F2`, `Ctrl+Alt+T`, `Super` veya `Ctrl+Alt+F1..F12` tuş kombinasyonlarını göndererek kabuk (shell) açmaya çalışır. Xorg/Wayland kiosk yapılandırmasında sanal terminal geçişleri (`Option "DontVTSwitch" "true"`, `Option "DontZap" "true"`) kapatılmalı ve pencere yöneticisinde kısayol tuşları devre dışı bırakılmalıdır.

---

## 4. Üç İlke Seçeneği (A: Sıkı, B: Dengeli, C: Gevşek)

### 4.1. Aygıt Sınıfı İzin Matrisi

| USB Aygıt Sınıfı | Hex Kodu (`cc:ss:pp`) | Seçenek A (Sıkı) | Seçenek B (Dengeli — Önerilen) | Seçenek C (Gevşek) |
| :--- | :--- | :--- | :--- | :--- |
| **USB Hub** | `09:00:*` | **İzinli (`allow`)** | **İzinli (`allow`)** | **İzinli (`allow`)** |
| **Klavye / Fare (Saf HID)** | `03:01:01`, `03:01:02`, `03:*:*` | **İzinli (`allow`)** *(Yalnızca saf HID; bileşik arayüzler `reject`)* | **İzinli (`allow`)** *(Yalnızca saf HID; bileşik arayüzler `reject`)* | **İzinli (`allow`)** *(Yalnızca saf HID; bileşik arayüzler `reject`)* |
| **E-İmza / Akıllı Kart (TÜBİTAK AKİS CCID)** | `0b:00:00` / `0b:*:*` | **İzinli (`allow`)** | **İzinli (`allow`)** | **İzinli (`allow`)** |
| **Yazıcı (Printer)** | `07:*:*` | **Yasak (`reject`)** | **İzinli (`allow`)** | **İzinli (`allow`)** |
| **Tarayıcı (Still Image / SANE)** | `06:01:01` / `06:*:*` ve `ff:*:*` | **Yasak (`reject`)** | **İzinli (`allow`)** *(Sınıf `06` doğrudan, `ff` ise `VID:PID` ile)* | **İzinli (`allow`)** |
| **USB Bellek (Mass Storage)** | `08:06:50` / `08:*:*` | **Tamamen Yasak (`reject`)** *(Sysfs'ten kaldırılır, onaylanamaz)* | **Varsayılan Yasak (`block`)** *(Yönetici `usbguard allow-device <id>` ile tek seferlik açabilir)* | **İzinli (`allow`)** *(Yalnızca saf `08:*:*`; `08+03` BadUSB `reject`)* |
| **Ağ Adaptörleri (CDC / Ethernet / RNDIS)** | `02:*:*`, `0a:*:*` | **Kesin Yasak (`reject`)** | **Kesin Yasak (`reject`)** | **Kesin Yasak (`reject`)** |
| **Kablosuz (Wi-Fi / Bluetooth)** | `e0:*:*` | **Kesin Yasak (`reject`)** | **Kesin Yasak (`reject`)** | **Kesin Yasak (`reject`)** |
| **Ses (Mikrofon / Ses Kartı)** | `01:*:*` | **Kesin Yasak (`reject`)** | **Kesin Yasak (`reject`)** | **Kesin Yasak (`reject`)** |
| **Görüntü (Webcam / Video Yakalama)** | `0e:*:*` | **Kesin Yasak (`reject`)** | **Kesin Yasak (`reject`)** | **Kesin Yasak (`reject`)** |

---

### 4.2. Seçenek A (Sıkı İlke) — `/etc/usbguard/rules.conf`
Belge aktarımının tamamen UYAP Avukat Portal üzerinden yapıldığı, fiziksel USB bellek, yazıcı ve tarayıcı kullanımının tamamen kapatıldığı en yüksek güvenlikli profildir. `reject` hedefi kullanıldığı için takılan bir USB bellek çekirdek aygıt ağacından (`/sys/bus/usb/devices`) anında silinir.

```dosya:/etc/usbguard/rules.conf
# ==============================================================================
# SEÇENEK A (SIKI İLKE) - /etc/usbguard/rules.conf
# İzinliler : Hub (09), Saf HID Klavye/Fare (03), CCID E-İmza / AKİS (0b)
# Yasaklılar: USB Bellek (08), Yazıcı (07), Tarayıcı (06/ff), Ağ (02/0a/e0), Ses/Video (01/0e)
# İzinler   : chmod 0600 /etc/usbguard/rules.conf (root:root)
# ==============================================================================

# 1. KESİN YASAKLI SINIFLAR (Ağ, Kablosuz, Ses, Video, Yığın Depolama, Yazıcı, Tarayıcı)
# Bu sınıflardan herhangi birini içeren tekil veya bileşik (composite) aygıtları anında reddet:
reject with-interface one-of { 01:*:* 02:*:* 06:*:* 07:*:* 08:*:* 0a:*:* 0e:*:* e0:*:* ff:*:* }

# 2. USB HUB'LAR (Kök Hub'lar, Tekil-TT ve Çoklu-TT USB 2.0/3.0 Hub'lar)
allow with-interface equals { 09:00:* }
allow with-interface equals { 09:00:* 09:00:* }

# 3. AKILLI KART OKUYUCU / E-İMZA TOKENI (TÜBİTAK AKİS - CCID Sınıf 0b)
# Yalnızca saf CCID arayüzü sunan kart okuyuculara izin ver:
allow with-interface equals { 0b:00:00 }
allow with-interface equals { 0b:*:* }
# Not: Bazı e-imza tokenları CCID + HID (0b + 03) çift arayüz sunabilir.
# Büro envanterinde böyle bir token varsa aşağıdaki satırda VID:PID doğrulanarak açılmalıdır:
# allow id <AKIS_TOKEN_VID:PID_DOĞRULANACAK> with-interface equals { 0b:*:* 03:*:* }

# 4. KLAVYE VE FARE (HID - Sınıf 03) - BadUSB Korumalı
# Opsiyonel Donanım Sabitleme: Büroda standart klavye/fare kullanılıyorsa aşağıdaki
# iki satırı etkinleştirip genel 03:*:* kurallarını yorum satırı yapın:
# allow id <KLAVYE_VID:PID_DOĞRULANACAK> with-interface equals { 03:01:01 }
# allow id <FARE_VID:PID_DOĞRULANACAK> with-interface equals { 03:01:02 }

# Yalnızca saf HID arayüzüne sahip (başka hiçbir sınıf barındırmayan) klavye ve fareler:
# Tek arayüzlü Boot Klavye (03:01:01), Boot Fare (03:01:02) veya Genel HID:
allow with-interface equals { 03:01:01 }
allow with-interface equals { 03:01:02 }
allow with-interface equals { 03:*:* }
# Çift/Üç HID arayüzlü klavye+multimedya tuşları veya kablosuz klavye+fare alıcıları:
allow with-interface equals { 03:*:* 03:*:* }
allow with-interface equals { 03:*:* 03:*:* 03:*:* }

# 5. GERİ KALAN TÜM AYGITLARI REDDET
reject
```

---

### 4.3. Seçenek B (Dengeli İlke — Önerilen) — `/etc/usbguard/rules.conf`
Avukat bürosunda dilekçe/evrak tarama ve yazdırma işlemleri için **yazıcı (`07`)** ve **tarayıcı (`06` / doğrulanan `ff`)** izinlidir. **USB bellek (`08`)** ise varsayılan olarak **`block`** (yetkisiz ama listede görünür) durumundadır.

* **Bileşik BadUSB Koruması:** Hem `08:*:*` (USB bellek) hem `03:*:*` (HID klavye) barındıran bir aygıt takılırsa `block` kuralına ulaşmadan önce üstteki kural tarafından **`reject`** edilir; böylece sistem yöneticisi dahi yanlışlıkla bir BadUSB belleğe onay veremez.
* **Tek Seferlik Yönetici Onayı Mekanizması:** Avukat USB bellekle dilekçe (`udf`/`pdf`) getirdiğinde, yönetici SSH veya yetkili konsoldan `usbguard list-devices -b` ile aygıtın yalnızca `08:06:50` arayüzüne sahip olduğunu görür ve `usbguard allow-device <ID>` komutuyla (`-p` bayrağı **kullanmadan**) yalnızca o takılma süresi için geçici izin verir. Bellek çıkarıldığı anda izin düşer.

```dosya:/etc/usbguard/rules.conf
# ==============================================================================
# SEÇENEK B (DENGELİ İLKE - ÖNERİLEN) - /etc/usbguard/rules.conf
# İzinliler : Hub (09), Saf HID Klavye/Fare (03), CCID E-İmza / AKİS (0b),
#             Yazıcı (07), Tarayıcı (06 ve VID:PID doğrulanmış ff)
# Onaya Tabi: USB Bellek (08) -> Varsayılan "block", yönetici "usbguard allow-device" ile açar
# Yasaklılar: Ağ (02/0a/e0), Ses/Video (01/0e), Bileşik BadUSB (03 + diğer sınıflar)
# İzinler   : chmod 0600 /etc/usbguard/rules.conf (root:root)
# ==============================================================================

# 1. KESİN YASAKLI SINIFLAR (Ağ, Kablosuz, Ses, Video)
# Bu sınıfları içeren hiçbir aygıt yönetici onayıyla dahi kullanılamaz (reject):
reject with-interface one-of { 01:*:* 02:*:* 0a:*:* 0e:*:* e0:*:* }

# 2. BİLEŞİK (COMPOSITE) BadUSB KORUMASI
# HID (03) arayüzünü Yığın Depolama (08), Yazıcı (07), Tarayıcı (06) veya
# Satıcıya Özgü (ff) arayüzlerle birleştiren tüm aygıtları kesin reddet (reject):
reject with-interface all-of { 03:*:* 08:*:* }
reject with-interface all-of { 03:*:* 07:*:* }
reject with-interface all-of { 03:*:* 06:*:* }
reject with-interface all-of { 03:*:* ff:*:* }

# 3. USB HUB'LAR (Sınıf 09)
allow with-interface equals { 09:00:* }
allow with-interface equals { 09:00:* 09:00:* }

# 4. AKILLI KART OKUYUCU / E-İMZA TOKENI (TÜBİTAK AKİS - CCID Sınıf 0b)
allow with-interface equals { 0b:00:00 }
allow with-interface equals { 0b:*:* }
# Çift arayüzlü (CCID + HID) e-imza tokenları için (büroda varsa VID:PID doğrulanmalıdır):
# allow id <AKIS_TOKEN_VID:PID_DOĞRULANACAK> with-interface equals { 0b:*:* 03:*:* }

# 5. KLAVYE VE FARE (Saf HID - Sınıf 03)
# Opsiyonel Donanım Sabitleme:
# allow id <KLAVYE_VID:PID_DOĞRULANACAK> with-interface equals { 03:01:01 }
# allow id <FARE_VID:PID_DOĞRULANACAK> with-interface equals { 03:01:02 }
allow with-interface equals { 03:01:01 }
allow with-interface equals { 03:01:02 }
allow with-interface equals { 03:*:* }
allow with-interface equals { 03:*:* 03:*:* }
allow with-interface equals { 03:*:* 03:*:* 03:*:* }

# 6. YAZICI (Sınıf 07) VE TARAYICI (Sınıf 06 Still Image / Sınıf ff Vendor-Specific)
# Tekil USB Yazıcılar:
allow with-interface equals { 07:*:* }
allow with-interface equals { 07:*:* 07:*:* }
# Standart Still Image (PTP/SANE) Tarayıcılar:
allow with-interface equals { 06:01:01 }
allow with-interface equals { 06:*:* }
# Çok Fonksiyonlu Yazıcı + Tarayıcı (07 + 06):
allow with-interface equals { 07:*:* 06:*:* }
allow with-interface equals { 06:*:* 07:*:* }

# UYARI: Birçok masaüstü tarayıcı (Canon, Epson, Fujitsu vb.) ve çok fonksiyonlu yazıcı
# satıcıya özgü "ff:*:* " sınıfını kullanır. Ancak USB-Ethernet ve USB-Seri dönüştürücüler de
# "ff:*:*" kullanabildiğinden, "ff:*:*" sınıfına yalnızca bürodaki cihazın VID:PID değeri
# doğrulanarak izin verilmelidir:
allow id <TARAYICI_VEYA_MFP_VID:PID_DOĞRULANACAK> with-interface one-of { 06:*:* 07:*:* ff:*:* }

# 7. USB BELLEK (Mass Storage - Sınıf 08) -> VARSAYILAN ENGEL (BLOCK), YÖNETİCİ ONAYINA TABİ
# "reject" yerine "block" kullanılır; böylece aygıt "usbguard list-devices -b" çıktısında
# görünür kalır ve yönetici "usbguard allow-device <ID>" ile tek seferlik izin verebilir:
block with-interface equals { 08:06:50 }
block with-interface equals { 08:*:* }

# 8. DİĞER TÜM BİLİNMEYEN AYGITLARI ENGELLE
block
```

---

### 4.4. Seçenek C (Gevşek İlke) — `/etc/usbguard/rules.conf`
Avukatların yoğun belge trafiği nedeniyle yönetici onayı beklemeksizin USB bellek (`08`), yazıcı (`07`) ve tarayıcı (`06`/`ff`) kullanabildiği, ancak **ağ (`02`/`0a`/`e0`), ses/görüntü (`01`/`0e`) ve bileşik BadUSB (`08+03`, `ff+03`)** saldırılarının yine kesin olarak engellendiği profildir.

> **Ek İşletim Sistemi Önlemi (Seçenek C ve B için):** Avukatların taktığı USB belleklerin otomatik bağlanma (automount) ayarlarında (`udisks2` / `/etc/udisks2/mount_options.conf`) mutlaka `nosuid,nodev,noexec` seçenekleri zorunlu tutulmalıdır.

```dosya:/etc/usbguard/rules.conf
# ==============================================================================
# SEÇENEK C (GEVŞEK İLKE) - /etc/usbguard/rules.conf
# İzinliler : Hub (09), Saf HID Klavye/Fare (03), CCID E-İmza / AKİS (0b),
#             Yazıcı (07), Tarayıcı (06/ff), Saf USB Bellek (08)
# Yasaklılar: Ağ (02/0a/e0), Ses/Video (01/0e), Bileşik HID+Depolama BadUSB (03+08)
# İzinler   : chmod 0600 /etc/usbguard/rules.conf (root:root)
# ==============================================================================

# 1. KESİN YASAKLI SINIFLAR (Ağ, Kablosuz, Ses, Video)
reject with-interface one-of { 01:*:* 02:*:* 0a:*:* 0e:*:* e0:*:* }

# 2. BİLEŞİK (COMPOSITE) BadUSB KORUMASI
# Kendisini hem USB Bellek (08) hem Klavye (03) olarak tanıtan BadUSB aygıtlarını reddet:
reject with-interface all-of { 03:*:* 08:*:* }
reject with-interface all-of { 03:*:* ff:*:* }

# 3. USB HUB'LAR (Sınıf 09)
allow with-interface equals { 09:00:* }
allow with-interface equals { 09:00:* 09:00:* }

# 4. AKILLI KART OKUYUCU / E-İMZA TOKENI (TÜBİTAK AKİS - CCID Sınıf 0b)
allow with-interface equals { 0b:00:00 }
allow with-interface equals { 0b:*:* }
# Bazı eski e-imza tokenları CCID (0b) yanında sürücü içeren sanal CD-ROM (08) veya HID (03) sunar:
allow with-interface equals { 0b:*:* 08:*:* }
allow with-interface equals { 0b:*:* 03:*:* }

# 5. KLAVYE VE FARE (Saf HID - Sınıf 03)
allow with-interface equals { 03:01:01 }
allow with-interface equals { 03:01:02 }
allow with-interface equals { 03:*:* }
allow with-interface equals { 03:*:* 03:*:* }
allow with-interface equals { 03:*:* 03:*:* 03:*:* }

# 6. YAZICI (Sınıf 07) VE TARAYICI (Sınıf 06 ve ff)
allow with-interface equals { 07:*:* }
allow with-interface equals { 07:*:* 07:*:* }
allow with-interface equals { 06:*:* }
allow with-interface equals { ff:*:* }
allow with-interface one-of { 06:*:* 07:*:* } with-interface one-of { 06:*:* 07:*:* 08:*:* ff:*:* }

# 7. USB BELLEK (Mass Storage - Sınıf 08)
# Yalnızca saf yığın depolama arayüzüne sahip USB bellekler izinlidir:
allow with-interface equals { 08:06:50 }
allow with-interface equals { 08:*:* }

# 8. DİĞER BİLİNMEYEN AYGITLARI ENGELLE
block
```

---

## 5. `systemd` `usbguard.service` İçin Sertleştirme (Drop-in)

Debian 13 (`trixie`) içindeki `/usr/lib/systemd/system/usbguard.service` paket dosyasını doğrudan değiştirmek paket güncellemelerinde çakışma yaratır. Bunun yerine `/etc/systemd/system/usbguard.service.d/hardening.conf` drop-in dosyası kullanılmalıdır.

### Kritik Teknik Detaylar (Varsayılan Birimi Bozmamak İçin):
1. **`PrivateNetwork=no` Zorunluluğu:** `usbguard-daemon`, `DeviceManagerBackend=uevent` ile çekirdekten gelen USB hotplug olaylarını `AF_NETLINK` (`NETLINK_KOBJECT_UEVENT`) soketi üzerinden dinler. Linux çekirdeği USB uevent mesajlarını yalnızca **başlangıç ağ ad alanına (`init_net`)** yayınlar. Eğer `PrivateNetwork=yes` yapılırsa yeni bir ağ ad alanı oluşturulur ve `usbguard-daemon` sonradan takılan hiçbir USB aygıtını göremez! Bunun yerine `PrivateNetwork=no` tutulup **`RestrictAddressFamilies=AF_UNIX AF_NETLINK`** kullanılarak TCP/UDP (`AF_INET`, `AF_INET6`) ağ erişimi tamamen kapatılır.
2. **`/sys` Yazma İzni ve `/etc/usbguard` Salt Okunur Koruması:** `usbguard-daemon` aygıtları yetkilendirmek/engellemek için `/sys/bus/usb/devices/*/authorized` ve `remove` dosyalarına yazmak zorundadır (`ReadWritePaths=/sys/bus/usb/devices`). Buna karşılık canlı kioskta kuralların çalışma zamanında (`usbguard append-rule` vb. ile) değiştirilmesini engellemek için **`ReadOnlyPaths=/etc/usbguard`** uygulanır.

```dosya:/etc/systemd/system/usbguard.service.d/hardening.conf
# ==============================================================================
# Debian 13 (trixie) usbguard.service Systemd Drop-in Sertleştirmesi
# Paket birim dosyasını (/usr/lib/systemd/system/usbguard.service) bozmaz.
# ==============================================================================
[Unit]
# Grafiksel kiosk oturumu (display-manager) başlamadan önce USB politikasının
# devreye girdiğinden emin ol:
Before=display-manager.service getty@tty1.service

[Service]
# Kural dizinini çalışma zamanında (root dahil) salt okunur kilitle:
ProtectSystem=strict
ReadOnlyPaths=/etc/usbguard
ReadWritePaths=-/var/log/usbguard -/run /dev /sys

# Kullanıcı ev dizinlerine (/home, /root) ve geçici dizinlere erişimi izole et:
ProtectHome=yes
PrivateTmp=yes

# ÖNEMLİ: Linux çekirdeği NETLINK_KOBJECT_UEVENT (USB hotplug) olaylarını yalnızca
# ana ağ ad alanına (init_net) gönderir. Bu nedenle PrivateNetwork=no kalmalı,
# ancak IP (AF_INET/AF_INET6) erişimi RestrictAddressFamilies ile kapatılmalıdır:
PrivateNetwork=no
RestrictAddressFamilies=AF_UNIX AF_NETLINK

# Yetenek (Capability) sınırlandırması:
# - CAP_NET_ADMIN: uevent netlink soketini dinlemek için
# - CAP_CHOWN, CAP_FOWNER, CAP_DAC_OVERRIDE: IPC soketi ve sysfs yazımı için
# - CAP_AUDIT_WRITE: denetim kayıtları için
CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER CAP_NET_ADMIN CAP_AUDIT_WRITE
AmbientCapabilities=

# Çekirdek ve bellek koruma bayrakları:
NoNewPrivileges=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
MemoryDenyWriteExecute=yes
LockPersonality=yes
SystemCallArchitectures=native
UMask=0077

# Servis beklenmedik şekilde çökerse derhal yeniden başlat:
Restart=always
RestartSec=1s
```

---

## 6. Test ve Doğrulama Adımları

Canlı imaj derlendikten veya test ortamında yapılandırma uygulandıktan sonra aşağıdaki adımlar sırasıyla çalıştırılmalıdır:

### Adım 1: Dosya İzinleri, Systemd Birimi ve Daemon Sözdizimi Kontrolü
```bash
# 1. Dosya izinlerini sıkılaştır (kiosk kullanıcısı kuralları okuyamasın)
sudo chown -R root:root /etc/usbguard
sudo chmod 0700 /etc/usbguard
sudo chmod 0600 /etc/usbguard/usbguard-daemon.conf /etc/usbguard/rules.conf

# 2. Systemd drop-in doğrulaması ve servisin yeniden yüklenmesi
sudo systemd-analyze verify usbguard.service
sudo systemctl daemon-reload
sudo systemctl restart usbguard.service
sudo systemctl status usbguard.service

# 3. Yüklü kuralların sırasını ve sözdizimini doğrula
sudo usbguard list-rules
```
* **Beklenen Sonuç:** `usbguard.service` hatasız (`active (running)`) başlamalı ve `usbguard list-rules` çıktısında `rules.conf` içindeki kurallar numaralandırılmış olarak görünmelidir.

### Adım 2: Kiosk Kullanıcısı IPC Erişim Reddi Testi (`IPCAllowedUsers=root`)
```bash
# Kiosk kullanıcısı (ör. "kiosk" veya "uyap" - KULLANICI_ADI_DOĞRULANACAK) ile test et:
sudo -u <KIOSK_KULLANICI_ADI_DOĞRULANACAK> usbguard list-devices
sudo -u <KIOSK_KULLANICI_ADI_DOĞRULANACAK> usbguard list-rules
```
* **Beklenen Sonuç:** `ERROR: IPC connection denied` veya `Permission denied` hatası dönmeli; kiosk kullanıcısı hiçbir aygıtı listeleyememeli ve kural değiştirememelidir.

### Adım 3: Salt Okunur Kural Dosyası (`ReadOnlyPaths=/etc/usbguard`) Testi
```bash
# Root olarak bile çalışma zamanında kalıcı kural eklenemediğini (-p) doğrula:
sudo usbguard append-rule -p 'allow'
```
* **Beklenen Sonuç:** `ReadOnlyPaths=/etc/usbguard` sayesinde kalıcı dosya yazma işlemi reddedilmeli (`Read-only file system`), canlı sistemdeki `/etc/usbguard/rules.conf` değişmeden kalmalıdır (`sudo usbguard remove-rule <eklenen_gecici_id>` ile RAM'deki kural silinir).

### Adım 4: TÜBİTAK AKİS E-İmza Tokenı (`0b:*:*`) Testi
```bash
# 1. E-imza tokenını (AKİS akıllı kart okuyucu) USB portuna takın.
# 2. USBGuard durumunu kontrol edin:
sudo usbguard list-devices

# 3. PC/SC katmanında AKİS kartın okunduğunu doğrulayın:
pcsc_scan -n
opensc-tool --list-readers
```
* **Beklenen Sonuç:** `usbguard list-devices` çıktısında e-imza okuyucusu `allow` durumunda (`with-interface { 0b:00:00 }`) görünmeli ve `pcsc_scan` çıktısında TÜBİTAK AKİS kart ATR değeri okunmalıdır.
* **Doğrulama Notu:** Eğer bürodaki spesifik bir e-imza tokenı `block` veya `reject` durumuna düşerse, `sudo usbguard list-devices` çıktısındaki `with-interface { ... }` ve `id <VID:PID>` değerleri incelenerek çift arayüzlü (`0b:*:* 03:*:*` vb.) olup olmadığı kontrol edilmelidir.

### Adım 5: USB Bellek (`08:*:*`) ve Tek Seferlik Yönetici Onayı Testi (Seçenek B)
```bash
# 1. Standart bir USB belleği kioska takın.
# 2. Engellenen aygıtları listeleyin:
sudo usbguard list-devices -b
# Örnek çıktı:
# 14: block id 0951:1666 serial "..." name "DataTraveler" ... with-interface 08:06:50 ...

# 3. Aygıtın yalnızca "08:06:50" arayüzüne sahip olduğunu (03:*:* içermediğini) doğrulayın.
# 4. Yönetici olarak TEK SEFERLİK (kalıcı olmayan) izin verin (ör. ID=14):
sudo usbguard allow-device 14

# 5. USB belleğin erişilebilir olduğunu doğrulayın, ardından belleği çıkarıp tekrar takın:
sudo usbguard list-devices -b
```
* **Beklenen Sonuç:** İlk takıldığında `block` durumunda olmalı ve dosya yöneticisinde görünmemelidir. `sudo usbguard allow-device <ID>` sonrasında erişime açılmalı; bellek çıkarılıp tekrar takıldığında ise otomatik olarak tekrar `block` durumuna düşmelidir. (Seçenek A'da ise aygıt `reject` edildiğinden listeden tamamen silinir).

### Adım 6: Yasaklı Aygıtlar ve Bileşik BadUSB Simülasyonu Testi
```bash
# 1. Yasaklı bir USB Wi-Fi/Ethernet adaptörü (02/0a/e0) veya USB Webcam (0e) takın.
# 2. Denetim günlüklerini gerçek zamanlı izleyin:
sudo tail -f /var/log/usbguard/usbguard-audit.log
sudo journalctl -u usbguard.service -n 30 --no-pager
```
* **Bileşik BadUSB Simülasyonu (Linux USB Gadget / ConfigFS destekli test cihazı veya Raspberry Pi Zero / Bash Bunny ile):**
  - Test cihazını hem `03:01:01` (HID Klavye) hem `08:06:50` (Mass Storage) veya `02:02:01` (CDC ACM) sunacak şekilde yapılandırıp kioska takın.
  - **Beklenen Sonuç:** `usbguard-audit.log` içerisinde `target=reject` kaydı oluşmalı; aygıtın klavye arayüzü dahi etkinleşmeden çekirdekten düşürülmelidir.

### Adım 7: Soğuk Açılış (`PresentDevicePolicy=apply-policy` + `toram`) Testi
1. Kiosk kapalıyken (güç kesikken) cihaza bir USB bellek (`08`) ve bir USB Ethernet/Wi-Fi adaptörü (`02`/`e0`) takın.
2. Kiosku açın ve `toram` ile canlı sistemin açılmasını bekleyin.
3. Sistem açıldığında `sudo usbguard list-devices` ve `ip link` / `lsblk` komutlarını çalıştırın.
* **Beklenen Sonuç:** Klavye, fare ve takılıysa AKİS e-imza okuyucusu çalışır durumda (`allow`) olmalı; USB Ethernet adaptörü `reject` edilmiş (`ip link` çıktısında yok), USB bellek ise Seçenek A'da `reject`, Seçenek B'de `block` durumunda (`lsblk` çıktısında yok) olmalıdır.
