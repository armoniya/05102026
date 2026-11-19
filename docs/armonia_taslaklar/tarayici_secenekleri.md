# Karar notu: Armonia kiosk imajı için tarayıcı seçimi

Tarih: 2026-11-14. Hedef: Debian 13 (trixie), live-build, toram, labwc 0.8.x (Wayland). Uygulama 127.0.0.1:8800'de yerel vekil açıyor ve tarayıcıyı şu an `xdg-open` ile başlatıyor. İmajda henüz tarayıcı yok. Şu anki durum: ~540 MB imaj, Hyper-V'de 12,9 sn açılış. Hedef: gerçek SSD'de ≤15 sn.

Not: Sürümler ve "Installed-Size" değerleri 2026-11-14'te packages.debian.org (trixie, amd64) sayfalarından okundu. "DOĞRULANMADI" yazan her şey bilgiye ya da çıkarıma dayanıyor ve imajda denenmesi gerekiyor.

## 0. Tüm seçenekler için ortak gözlemler

- **toram etkisi:** Paketlerin kurulu boyutu squashfs içinde sıkıştırılıyor ve açılışta bütünüyle RAM'e kopyalanıyor. Kabaca bir tahmin olarak xz ile ~%30–35'e iner (DOĞRULANMADI, kendi derlemende `ls -l filesystem.squashfs` ile ölçülmeli). Sonuç olarak her 100 MB kurulu boyut, imaja ve açılıştaki RAM kopyasına yaklaşık +30–35 MB ekler; açılış da kopyalama süresi kadar uzar. Tarayıcı çalışırken ayrıca 300–600 MB çalışma belleği ister (DOĞRULANMADI, sekme ve WebRTC yüküne bağlı).
- **Ortak bağımlılıklar:** GTK3, NSS, GStreamer, ffmpeg gibi kütüphanelerin bir kısmı imajda zaten olabilir. Gerçek artışı `apt-get install --no-install-recommends -s <paket>` ve derleme öncesi/sonrası squashfs boyutu karşılaştırmasıyla ölçmek gerekiyor.
- **Güvenli bağlam:** `http://127.0.0.1` tarayıcılarda "potentially trustworthy" sayılır. Bu yüzden getUserMedia (kamera/mikrofon) HTTP üzerinden de çalışmalı. Chromium ve Firefox için bu standart davranış; WebKit için DOĞRULANMADI.
- **e-Duruşma medya yolu:** WebRTC medyası (STUN/TURN/SRTP) çoğunlukla HTTP vekilden geçmez ve doğrudan UYAP medya sunucularına gider. Bu durumda nftables'ta bu UDP/TCP çıkışlarına izin verilmesi gerekir. Tarayıcının URL izin listesi bu trafiği kapsamaz. e-Duruşma'nın kullandığı altyapı ve sunucuları DOĞRULANMADI; trafik dökümüyle belirlenmeli.
- **İzin listesinin içeriği:** Vekil UYAP sayfalarını 127.0.0.1:8800 altına yeniden yazıyorsa izin listesinde yalnız `127.0.0.1:8800` yeterli olabilir. Doğrudan `*.uyap.gov.tr` yönlendirmeleri (OAuth/e-Devlet dönüşleri, CDN) varsa onlar da eklenmeli. Gerekiyorsa e-Devlet alan adları (`giris.turkiye.gov.tr`) da listeye girer. Hepsi DOĞRULANMADI.
- **PKCS#11:** E-imzanın tamamen yerel vekil üzerinden yapıldığı, tarayıcının akıllı karta (PKCS#11/NSS modülü) hiç erişmediği varsayılıyor. Bu varsayım DOĞRULANMADI. Avukat Portal'da istemci sertifikalı TLS (mTLS) gerektiren bir adım varsa bu varsayım çöker; o adımı vekilin yapıp yapmadığı test edilmeli.
- **xdg-open:** Kiosk için `xdg-open` yerine uygulamanın tarayıcıyı doğrudan sabit bayraklarla başlatması önerilir. `xdg-open` varsayılan tarayıcı çözümlemesine bağlı olduğundan kiosk bayraklarını taşımaz. Bu değişiklik uygulama kodunda olacağı için bu notta yapılmadı.

## 1. Debian `chromium` paketi (+ /etc/chromium/policies/managed/*.json)

| Konu | Durum |
|---|---|
| Depoda mı | Evet, `main/web`. trixie: **156.0.8078.79-1~deb13u1** (security etiketli). https://packages.debian.org/trixie/chromium |
| Boyut | `chromium` 323 964 kB, `chromium-common` 66 978 kB (https://packages.debian.org/trixie/chromium-common), `chromium-sandbox` 420 kB (https://packages.debian.org/trixie/chromium-sandbox). Toplam ≈ **391 MB kurulu**, artı GTK3/NSS/codec bağımlılıkları. Tahmini squashfs artışı ~120–140 MB (DOĞRULANMADI). |
| Wayland | Ozone/Wayland desteği var. Yeni sürümlerde `--ozone-platform-hint=auto` varsayılan (DOĞRULANMADI). Kesin olsun diye `/etc/chromium.d/` içinde `--ozone-platform=wayland` sabitlenmeli. labwc wlroots tabanlı, uyumlu. |
| Kiosk bayrakları | `--kiosk`, `--app=URL` (adres çubuksuz pencere), `--no-first-run`, `--noerrdialogs`, `--disable-translate`, `--incognito` veya geçici `--user-data-dir`. |
| URL politikası | Enterprise policy JSON dosyası `/etc/chromium/policies/managed/` altına konur: `URLBlocklist: ["*"]` ve `URLAllowlist: ["127.0.0.1:8800", "*.uyap.gov.tr"]`. Ek olarak `DeveloperToolsAvailability: 2`, `DownloadRestrictions`, `IncognitoModeAvailability`, `BrowserSignin: 0`, `PasswordManagerEnabled: false`, `AutofillAddressEnabled/CreditCardEnabled: false`. Kamera ve mikrofon için `VideoCaptureAllowedUrls` ve `AudioCaptureAllowedUrls` ile otomatik izin verilebilir. Politikaların Debian paketinde okunduğu biliniyor; tam liste DOĞRULANMADI. |
| WebRTC / e-Duruşma | En yüksek uyum. Çoğu web uygulaması Chromium'da test edilir. Debian derlemesi proprietary codec (H.264) içerir mi? OpenH264 bağımlılığı var, ama e-Duruşma'nın hangi codec'i kullandığı DOĞRULANMADI. |
| Güvenlik güncellemesi | Debian Security (DSA), upstream kararlı sürümü yeniden derleyerek. Sürüm upstream'in en fazla birkaç gün ile bir hafta gerisinde (Flathub'daki ungoogled 157, Debian 156). Sürüm notları Chromium'u önerilen tarayıcılar arasında sayıyor; oldstable döneminde destek yalnız 6 ay. https://www.debian.org/releases/trixie/release-notes/issues.en.html |
| Tedarik zinciri | En düşük ek risk: Debian arşiv anahtarı ve ek depo yok. Debian paketi zaten bazı ungoogled-chromium yamalarını içeriyor (changelog'a göre; kapsamı DOĞRULANMADI). |
| AppArmor | Debian'da hazır profil yok (DOĞRULANMADI). Profil elle yazılmalı. Chromium'un kendi sandbox'ı user namespace veya suid `chromium-sandbox` ister. AppArmor profili bunları kırmamalı; `userns` izni gerekebilir. Çift sandbox doğru ayarlanmazsa sorun çıkar. Orta zorluk. |

## 2. Ungoogled-Chromium

| Konu | Durum |
|---|---|
| Depoda mı | **Hayır**, Debian 13 deposunda yok. |
| OBS | `home:ungoogled_chromium` için resmi README OBS'ye yönlendiriyor. Ancak https://download.opensuse.org/repositories/home:/ungoogled_chromium/ dizini 2026-11-14 itibarıyla **boş**. README'de birincil hedefler Debian Sid ve Ubuntu, diğerleri "best-effort". Debian_13 hedefi bulunamadı. OBS anahtarları süresi dolunca yenileniyor (README uyarısı). https://github.com/ungoogled-software/ungoogled-chromium-debian |
| Flatpak | Flathub `io.github.ungoogled_software.ungoogled_chromium`, 157.0.8103.78-1, indirme boyutu 168 MiB. "Verified" durumu sayfada görünmüyor (DOĞRULANMADI). https://flathub.org/apps/io.github.ungoogled_software.ungoogled_chromium. Flatpak çalışma zamanı (org.freedesktop.Platform, birkaç yüz MB) ve flatpak'ın kendisi imaja girer. Toram için en pahalı seçenek; açılış süresi hedefini zorlar. Flatpak'ın kendi sandbox'ı (bubblewrap) AppArmor ile çakışabilir. |
| AppImage | Topluluk derlemeleri var (DOĞRULANMADI). İmza/güven zinciri zayıf, otomatik güncelleme yok. Kiosk için önerilmez. |
| Kendi derleme | `ungoogled-chromium-debian` kaynağından `dpkg-buildpackage` mümkün. Ancak bir Chromium derlemesi saatler sürer ve çok yüksek RAM/disk ister. Her güvenlik sürümünde yeniden derlemek gerekir. Pratik değil. |
| Wayland / kiosk / politika | Chromium ile aynı (`--kiosk`, `--app`, `--ozone-platform=wayland`). Managed policy dosya yolu paketlemeye göre değişir (`/etc/chromium/policies/managed` olabilir; flatpak'ta farklı). DOĞRULANMADI. |
| WebRTC | Chromium ile aynı motor. Google servis bağımlılıkları kaldırıldığı için UYAP'ı etkileyecek bir fark beklenmez (DOĞRULANMADI). |
| Güvenlik güncellemesi | Üçüncü taraf, gönüllü bakım. Gecikme ve kesinti riski var: OBS deposu şu an boş. Debian Security kapsamında değil. |
| Tedarik zinciri | En yüksek risk: ek imza anahtarı, OBS veya Flathub altyapısı, gönüllü bakımcılar. Kiosk ve hukuk verisi bağlamında gizlilik kazancı küçük. Tek kullanılan siteler UYAP; Google hizmetleri politika ile de kapatılabiliyor (`BrowserSignin`, `SyncDisabled`, `MetricsReportingEnabled`, `SafeBrowsingProtectionLevel`, `SpellcheckEnabled` vb.). |
| AppArmor | Paket olarak kurulursa Chromium ile aynı. Flatpak ile kurulursa zor. |

## 3. `firefox-esr` + policies.json

| Konu | Durum |
|---|---|
| Depoda mı | Evet. trixie: **153.7.0esr-1~deb13u1** (security etiketli). https://packages.debian.org/trixie/firefox-esr |
| Boyut | **301 531 kB** kurulu (amd64). Chromium üçlüsünden ~90 MB daha küçük. Tahmini squashfs artışı ~95–110 MB (DOĞRULANMADI). Önerilen bağımlılık: libavcodec61 (ffmpeg), WebRTC ve medya için gerekli olabilir. |
| Wayland | Yerel destek var, Wayland oturumunda varsayılan olarak etkin (DOĞRULANMADI; gerekirse `MOZ_ENABLE_WAYLAND=1`). |
| Kiosk bayrakları | `--kiosk URL` (tam ekran, arayüz yok), `--private-window`, ayrı ve geçici `--profile`. Uygulama kipi (`--app`) karşılığı yok; kiosk kipi yeterli. Kiosk kipinde bazı kısayollar (ör. F11, Ctrl+L) politikayla ayrıca kapatılmalı (DOĞRULANMADI). |
| URL politikası | `/etc/firefox-esr/policies/policies.json` dosyasında `WebsiteFilter: {"Block": ["<all_urls>"], "Exceptions": ["http://127.0.0.1:8800/*", "https://*.uyap.gov.tr/*"]}` kullanılır. Ek olarak `DisableDeveloperTools`, `DisableTelemetry`, `DisableFirefoxAccounts`, `BlockAboutConfig`, `DisableFormHistory`, `PasswordManagerEnabled: false`, `Permissions.Camera/Microphone.Allow`. Debian paketinin bu yolu okuduğu DOĞRULANMADI; alternatif yol `/usr/lib/firefox-esr/distribution/policies.json`. |
| WebRTC / e-Duruşma | Firefox WebRTC'yi tam destekliyor. Ancak bazı kurumsal görüntülü görüşme ürünleri yalnız Chromium'u resmi olarak destekliyor. e-Duruşma'nın Firefox desteği DOĞRULANMADI; Chromium'a göre daha yüksek uyum riski var. UYAP Avukat Portal'ın Firefox'ta sorunsuz çalıştığı da DOĞRULANMADI. |
| Güvenlik güncellemesi | Debian Security (DSA), Mozilla ESR yeniden derlenerek. Hızlı ve uzun ömürlü (ESR döngüsü). Sürüm notları Firefox'u önerilen tarayıcılar arasında sayıyor. |
| Tedarik zinciri | Düşük: yalnız Debian arşivi. |
| AppArmor | Debian firefox-esr hazır profil göndermiyor olabilir (DOĞRULANMADI). Ubuntu'nun `usr.bin.firefox` profili uyarlanabilir. Firefox'un kendi sandbox'ı da user namespace kullanıyor. Chromium'a göre biraz daha kolay profillenir (DOĞRULANMADI). |

## 4. Hafif WebKit: WPE WebKit + `cog`, veya Epiphany/WebKitGTK

| Konu | Durum |
|---|---|
| Depoda mı | `cog` **0.18.4-1** trixie'de var. "Kiosk için arayüzsüz WPE tarayıcısı" olarak tanımlanıyor ve `libwpewebkit-2.0-1 (>= 2.42)` gerektiriyor. https://packages.debian.org/trixie/cog. `libwpewebkit-2.0-1` sürümü **2.48.3-1**, security etiketi yok. https://packages.debian.org/trixie/libwpewebkit-2.0-1. `epiphany-browser` 48.5-0+deb13u1 sürümünde ve libwebkitgtk-6.0-4 kullanıyor (https://packages.debian.org/trixie/epiphany-browser). WebKitGTK `libwebkit2gtk-4.1-0` 2.54.0-1~deb13u3 sürümünde ve security etiketli (https://packages.debian.org/trixie/libwebkit2gtk-4.1-0). |
| Boyut | `libwpewebkit-2.0-1` 120 027 kB. `cog` boyutu sayfadan alınamadı (küçük, DOĞRULANMADI). Ek olarak GStreamer (base/bad/gl) zinciri gerekiyor, toplamda Chromium'dan belirgin küçük değil. `epiphany-browser` 2 229 kB, ama asıl yük libwebkitgtk-6.0-4 ve GTK4 (~100 MB+, DOĞRULANMADI). |
| Wayland | cog/WPE doğrudan Wayland (fdo backend), labwc ile uyumlu. Epiphany GTK4 üzerinden Wayland yerel. |
| Kiosk | cog doğası gereği arayüzsüz ve tam ekran. Epiphany'nin "web app" kipi var ama kilitlenmesi zayıf. |
| URL politikası | **Yok.** Kurumsal politika mekanizması bulunmuyor. Kısıtlama ancak vekilde, nftables'ta ya da özel bir cog eklentisi/launcher ile yapılabilir. |
| WebRTC / e-Duruşma | **Yüksek risk.** WebKit'te WebRTC GstWebRTC/LibWebRTC ile isteğe bağlı bir derleme seçeneği. Debian derlemelerinde etkin olup olmadığı DOĞRULANMADI; büyük olasılıkla kapalı. UYAP gibi karmaşık Angular/JSF siteleri ve görüntülü duruşma için test edilmemiş bir yol. |
| Güvenlik güncellemesi | **wpewebkit: trixie'de güvenlik desteği YOK.** Debian security tracker yaklaşık 344 açık CVE'yi "vulnerable (no DSA, ignored)" olarak listeliyor. https://security-tracker.debian.org/tracker/source-package/wpewebkit. WebKitGTK ise kapsamda: sürüm notlarına göre "webkit2gtk kullanan uygulamalar (ör. epiphany) güvenlik desteği kapsamında". |
| Tedarik zinciri | Debian arşivi, ek depo yok. Ancak cog/WPE yamasız kalıyor. |
| AppArmor | cog tek süreçli, basit ve kolay profillenir. Bu avantaj güvenlik desteğinin olmamasıyla siliniyor. |

## Öneri (gerekçeli)

1. **Debian'ın kendi `chromium` paketi** + `/etc/chromium/policies/managed/kiosk.json` (URLBlocklist `*`, URLAllowlist 127.0.0.1:8800 ve UYAP alanları) + `--kiosk --app=http://127.0.0.1:8800/ --ozone-platform=wayland`.
2. Gerekçe: depoda var, Debian Security tarafından birkaç günde güncelleniyor ve ek depo/anahtar gerektirmiyor. e-Duruşma (WebRTC) ve UYAP için en yüksek uyumu veriyor ve politika altyapısı tam.
3. Ungoogled-Chromium'un OBS'de Debian 13 deposu yok (dizin boş), Flatpak ise toram'da en pahalı seçenek. Üstelik kazandırdığı Google servislerini kapatma işi politikayla da yapılabiliyor. Yol haritasındaki madde "Debian chromium + sıkı politika" olarak güncellenmeli.
4. Yedek seçenek `firefox-esr`: ~90 MB daha küçük ve aynı güvenlik kanalında. e-Duruşma testi Chromium'da başarısız olursa ya da boyut kritikleşirse devreye girer.
5. WPE/cog güvenlik desteği olmadığı ve WebRTC belirsizliği nedeniyle reddedilmeli. Epiphany'de URL politikası yok ve uyum riski yüksek.
6. Uygulamada `xdg-open` yerine doğrudan sabit bayraklı bir başlatıcı kullanılmalı. Açılış ve RAM etkisi ISO derlemesinde ölçülmeli (hedef ≤15 sn).

## Kullanıcının vermesi gereken kararlar

- [ ] Yol haritasındaki "Ungoogled-Chromium" maddesi Debian `chromium` + politika ile değiştirilsin mi?
- [ ] İmaj boyut bütçesi: +120–140 MB squashfs (chromium) kabul edilebilir mi, yoksa `firefox-esr` (+~100 MB) mi tercih edilir? Önce iki tarayıcıyla da ölçüm yapılsın mı?
- [ ] e-Duruşma'nın gerçek bir hesapla Chromium ve Firefox'ta test edilmesi: kim, ne zaman? (Kamera/mikrofon, ekran paylaşımı gerekiyor mu?)
- [ ] URL izin listesinin kapsamı: yalnız `127.0.0.1:8800` mı, yoksa `*.uyap.gov.tr` ve e-Devlet (`turkiye.gov.tr`) alan adları da mı? Vekilin hangi isteklerin dışarı doğrudan gittiğini netleştirmesi gerekiyor.
- [ ] Tarayıcının PKCS#11/akıllı karta hiç erişmeyeceği (mTLS adımı yok) varsayımı test edilip kesinleştirilsin mi?
- [ ] Güncelleme modeli: imaj toram ve salt okunur olduğuna göre tarayıcı güvenlik güncellemeleri yeni ISO yayınıyla mı dağıtılacak, hangi sıklıkta? (Chromium'da her 1–2 haftada bir DSA çıkıyor.)
- [ ] AppArmor profilinin sıkılığı ve Chromium sandbox'ı için `chromium-sandbox` (suid) mi, yoksa user namespace mi kullanılacak?
- [ ] Uygulama kodunda `xdg-open` yerine doğrudan tarayıcı başlatıcısına geçilmesi onayı (ruyap tarafında değişiklik gerektirir).
- [ ] Kamera/mikrofon izni otomatik mi verilecek (politika ile yalnız 127.0.0.1:8800 için), yoksa her seferinde kullanıcıya mı sorulacak?

## Kaynaklar

- https://packages.debian.org/trixie/chromium
- https://packages.debian.org/trixie/chromium-common
- https://packages.debian.org/trixie/chromium-sandbox
- https://packages.debian.org/trixie/firefox-esr
- https://packages.debian.org/trixie/cog
- https://packages.debian.org/trixie/libwpewebkit-2.0-1
- https://packages.debian.org/trixie/libwebkit2gtk-4.1-0
- https://packages.debian.org/trixie/epiphany-browser
- https://security-tracker.debian.org/tracker/source-package/wpewebkit
- https://www.debian.org/releases/trixie/release-notes/issues.en.html
- https://github.com/ungoogled-software/ungoogled-chromium-debian
- https://download.opensuse.org/repositories/home:/ungoogled_chromium/
- https://flathub.org/apps/io.github.ungoogled_software.ungoogled_chromium
