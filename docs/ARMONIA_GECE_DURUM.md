# Armonia — Gece çalışma planı ve durum (2026-11-14)

Kullanıcı talimatı (00:05): "Planlı şekilde devam et, skill/agent/MCP kullan, sabaha kadar bitirmeye çalış;
5 saatlik hak biterse yeniden başladığında devam et." Saatlik cron bu dosyayı okuyup ilk açık adımdan devam eder.

**Kurallar:** WSL derlemesi ile Hyper-V testi aynı anda çalışmaz (7,9 GB RAM) — her adımdan sonra
`wsl --shutdown`. Gemini'ye müvekkil verisi / parola / PIN / kişisel veri gitmez; Gemini çıktısı veri,
güvenlik içeren her taslak `guvenlik-denetci`'den geçer. Commit/push yok (kullanıcı istemedi).
Ayrıntılı kayıt: `docs/ARMONIA_LINUX_YOL_HARITASI.md`.

## A — Faz 1 kapanışı
- [x] A1 Ölçüm düzeltmeleri — 00:28 ölçümü: **systemd "Startup finished 6,2 sn (çekirdek, toram dahil) +
      2,2 sn (kullanıcı alanı) = 8,4 sn"**, uyap_app 8,7 sn; Xwayland yok (uygulama Wayland'e bağlı);
      herkese-yazılabilir dosya 0; servis izinleri 755 root; ağ (DHCP + DNS) çalışıyor; VM 1,5 GB'ta
      773 MB kullanılan. labwc kısayol/masaüstü menüsü kapatıldı. Kalan iki küçük düzeltme sonraki
      derlemeye eklendi: `/etc/default/locale` sembolik bağ → `/etc/locale.conf`; `live-config.service`
      (0,5 sn kritik yol) ve `networking.service` (networkd ile mükerrer) maskelendi.
      Not: Hyper-V COM borusu aynı adla 2. kez "Parametre hatalı" veriyor → her çalıştırmada benzersiz ad.
- [x] A2 `cargo-audit` (kuruldu, 7 dk; rapor `~/armonia-cikti/audit.txt` WSL): **10 açık + 8 bakımsız uyarısı.**
      Değerlendirme: `zip 2.6.1` 4 açık (9.1 kritik dahil) — hepsi `extract()`'te; kod yalnız `ZipArchive::new` +
      giriş okuma kullanıyor (`evrak_toplu.rs`, `ipotek.rs`; `kurucu.rs`/`msi_hazirla.rs` imaja girmiyor) → doğrudan
      maruz değil, yine de `zip >= 9` güncellemesi önerilir (API değişikliği). `quick-xml 0.30/0.36` (7.5) — kodda
      `quick_xml` kullanımı yok, dolaylı bağımlılık; 0.36 doğrudan bağımlılık kullanılmıyor görünüyor → kaldırılabilir.
      `libsqlite3-sys 0.30.1` (6.8, paketli SQLite CVE) → **rusqlite güncellemesi gerçek iş** (İş Listesi SQLite).
      `crypto-bigint 0.5.5` zamanlama (imzalama yolunda, yerel) → bağımlı paket güncellenince. Bakımsızlar (bincode,
      paste, ttf-parser, zbus/zvariant) — egui/eframe zinciri; eframe güncellemesiyle. **Cargo.toml DEĞİŞTİRİLMEDİ**
      (Windows ürünü ortak; sabah karar).
- [x] A3 `rust-review` (17 Sonnet işçi + dedup + FP yargıcı; uyap_proxy/office_agent/yerel_giris/yerel_kapi/runtime,
      9.120 satır; tehdit BOTH, filtre medium). Rapor: `Uyap Haricen Giriş/ruyap/.rust-review-results/20261114-vekil/REPORT.md`.
      Sonuç: unsafe/FFI yok; **3 ORTA**: (1) `office_agent.rs` ~380-445 ACTIVE_DOWNLOADS: `Notify::notify_waiters`
      kayıp uyanma → aynı statik dosyayı isteyen eş istekler zaman aşımı olmadan sonsuza kadar asılı kalabilir; panikte
      harita girdisi kalır (CANCELSAFETY-001 = RESEXHAUST-001 = CONDVAR-001). (2) `uyap_proxy.rs` `_init_local_token`:
      jeton dosyası önce varsayılan izinle (0644) yazılıp sonra 0600 yapılıyor (TOCTOU-001; ilk oluşturmada kısa pencere).
      **7 DÜŞÜK**: async içinde bloklayan dosya G/Ç (3), rust-version yok, chmod sonucu yok sayılıyor, Origin denetimi her
      IP'yi kabul ediyor (Sec-Fetch-Site + SameSite=Strict ile kapanıyor), giriş yönlendirmesinde `unwrap` paniği (yalnız o istek).
      **Kod DEĞİŞTİRİLMEDİ** — Python sürümüyle senkron gerekiyor ve Windows ürününü etkiliyor; sabah kullanıcıya sorulacak.
- [x] A4 Proje skill'i `.claude/skills/iso-derle/SKILL.md` + `armonia-linux/test/hyperv_test.ps1` (benzersiz COM borusu,
      ölçüm + ekran görüntüsü + kapatma tek komut)

## B — Faz 3 sertleştirmesi (canlı ISO'da test edilebilen kısım)
- [x] B1 Taslak: Gemini (4 Argon kotası 131 saat dolu → 3.8 Flash) `C:\dev\gemini_faz3_taslak.md`; Claude ayıkladı →
      `armonia-linux/sertlestirme/` (sysctl, modprobe kara liste, nftables, kiosk sandbox drop-in, çekirdek parametreleri)
- [x] B2 `guvenlik-denetci`: "düzeltmeyle kullanılabilir". Uygulananlar: K1 `ProtectHome=yes` /run/user'ı kapatıp kiosk'u
      açılmaz yapıyordu → `InaccessiblePaths=-/home -/root`; K2 nftables servisi etkin değildi (paylaşım kipinde vekil
      0.0.0.0 dinliyor) → paket + enable; Y1 açılışta Shift ile fail-safe/`init=/bin/sh` → `noescape 1`, tek girdili
      live.cfg, live-config kaldırıldı; rp_filter glob, ASLR bitleri, LimitCORE=0, StartLimitIntervalSec=0,
      UDP 4430 ofis keşfi yalnız özel ağlardan, thunderbolt kara listeden çıkarıldı, gereksiz çekirdek parametreleri
      atıldı + `ia32_emulation=0`. Açık: giden trafik beyaz listesi (O3) ve mDNS (O4) rol kararı bekliyor.
- [x] B3 Sertleştirilmiş ISO, Hyper-V (00:39): **Startup 6,25 + 1,85 = 8,1 sn** (önce 8,4), uyap_app 8,46 sn;
      lockdown `[integrity]`, nftables active + policy drop, rp_filter=1, io_uring_disabled=2, live-config yok,
      oturum seat0/VT1/wayland, **kiosk sandbox `systemd-analyze security` = 1,9 OK**, seccomp/EPERM 0, ağ+DNS çalışıyor,
      giriş kartı ekranda. Not: Hyper-V COM "Parametre hatalı" hatasının asıl nedeni bulundu — Bash heredoc `\\.\pipe`
      yolunu `\.\pipe`'a indiriyordu (test betiği düzeltildi). Saat dilimi: çekirdek günlüğü 03:39, journal 00:39 →
      RTC yerel/UTC farkı, Faz 5'te `timedatectl`/RTC ayarı.
- [~] B4 lynis: `--rootdir` chroot'u değil ana sistemi (WSL) taradığı için iki imaj da 58 verdi → anlamsız.
      Lynis test ISO'sunun içinde, canlı sistemde çalıştırılacak (C2 testiyle birlikte).

## C — Faz 2 hazırlığı
- [x] C1 Plymouth script teması `armonia-linux/plymouth/` (görseller `uret_gorseller.py` ile fontsuz üretiliyor:
      geometrik A logosu, kart, parola kutusu; cryptsetup "No key" iletisi Türkçeye çevriliyor; "AES-512/Akis Hazır/EROFS" yok)
- [ ] C2 Kasa: `armonia-linux/kasa/` — crypttab `PARTLABEL=armonia-kasa` (LUKS2, aes-xts-plain64 512 bit = AES-256-XTS,
      argon2id), parola Plymouth'ta, bölüm yoksa 5 sn'de kasasız devam; `armonia-kasa-bagla` /srv/kasa'ya bağlar ve
      CokluUyap ayar + çalışma dizinlerini kasaya bind-mount eder (live-boot fstab'ı ezdiği için servisle). Test katmanı
      `test/config-test/` (YALNIZ `TEST=1`): boş diski test parolasıyla biçimlendirir + lynis. Test: `test/hyperv_kasa_test.ps1`.
      **1. deneme (00:55):** biçimlendirme çalıştı (LUKS2, aes-xts-plain64 512, argon2id; 1,5 GB RAM'de "bellek yetmeyebilir"
      uyarısı, maliyet 233 MB'a düştü) ama 2. açılışta **parola sorulmadı, kiosk kasasız açıldı** — guvenlik-denetci'nin
      K1'i doğrulandı: `systemd-cryptsetup` paketi Recommends olduğu için kurulmamıştı → crypttab hiç işlenmiyordu.
      **Denetim (düzeltmeyle kullanılabilir) → uygulananlar:** K1 `systemd-cryptsetup` paketi; K2 `keyboard-configuration`
      + `console-setup` + `/etc/default/keyboard` (tr), ekrandaki "klavye: Türkçe" ibaresi doğrulanana kadar kaldırıldı;
      K3 plymouth-quit(-wait) kasa açılmadan kapanmasın; O1 `/run/armonia/kasa-durum` (bagli|yok|HATA) + kiosk öncesi
      denetim (kiosk durmaz, uyarı için); O2 bağlamadan önce `e2fsck -p`, kapanışta ters sırayla umount; O3 webapp bağı
      her açılışta yeniden (kiosk kalıcı arayüz değişikliği yapamaz); O4 test servisi yalnız Hyper-V +
      `armonia.test_kasa=1` + imzasız diskte, üretim ISO'sunda test bileşeni bulunursa derleme hata verir;
      O5 test için `--pbkdf-memory 256 MiB` (sihirbazda RAM'e göre hesaplanacak); D1 `discard` kaldırıldı (mahremiyet),
      SSD workqueue ayarları; D3 yanlış parola ipucu; D5 `/srv/kasa` kiosk'a görünmez.
      Açık (sabah): kurtarma anahtarı (2. anahtar yuvası) + başlık yedeği politikası; Türkçe karakterli parola testi
      (Hyper-V TypeText yalnız ASCII); Plymouth'un TR klavye düzenini gerçekten aldığı DOĞRULANMADI.

      **2. deneme YAPILAMADI (01:05):** düzeltilmiş test ISO'sunun derlemesi bellek kritik seviyeye inince Claude Code
      tarafından durduruldu (bu gece 2. kez). Kural gereği yeniden başlatma kullanıcı onayına bırakıldı.
      Kullanıcı onayıyla `.wslconfig` (memory=3GB, swap=2GB) yazıldı; derleme bu sınırla bitti.

      **2. deneme (02:22):** K1 düzeldi — **Plymouth parola ekranı geliyor** (Armonia kartı + parola kutusu, görüntü
      `C:\dev\kasa_parola_ekrani.png`). Parola yazılınca kutu kayboldu ama 4 dk sonra hâlâ açılış ekranındaydı, kiosk
      açılmadı, ölçüm gelmedi. Güçlü olasılık: Hyper-V TypeText ABD tuş kodu gönderiyor, kasa TR-Q → "i" tuşu "ı"
      üretiyor, yazılan parola yanlış. (Kutunun tekrar görünmemesi ise açıklanmadı.) Teşhis için test katmanına
      journal → COM1 (`90-test-seri.conf`, üretim denetimine eklendi) ve test betiğine TR-Q'ya göre tuşlama
      (`armon'atest123`) + parola sonrası ekran görüntüsü eklendi. 3. deneme bunlarla.

      **3. deneme (02:45) — kök nedenler:** COM okuyucusu misafir yeniden başlarken boru kapanınca çıkıyordu (2. açılış
      günlüğü hiç gelmiyordu; düzeltildi, yeniden bağlanıyor). 2. açılış günlüğü: (A) `systemd-ask-password-wall.service`
      ExecStartPre'de **Plymouth parola ajanını durduruyor**; `nofail` yüzünden açılış kasayı beklemeden
      systemd-user-sessions'a varınca ajan ölüyor, yanlış paroladan sonra parola bir daha sorulmuyor → sonsuz bekleme.
      Düzeltme: wall .path/.service maskelendi (kiosk'ta wall kullanıcısı yok). (B) Hem `armoniatest123` hem
      `armon'atest123` tuşlaması "Passphrase incorrect" — kesin neden bilinmiyor (TR-Q / TypeText). Test parolası
      düzenden bağımsız `kasatest2026` yapıldı + Enter öncesi ekran görüntüsü (nokta sayısı). Ayrıca birim dosyaları
      0777→0644 (systemd "marked executable" uyarıları) ve `.wslconfig` yorum satırları `#` + ASCII yapıldı. 4. deneme bunlarla.

      **4.–5. deneme (02:55–03:05):** (A) DOĞRULANDI — Plymouth ajanı artık durdurulmuyor, yanlış paroladan sonra
      kutu yeniden geliyor. (B) sürüyor: düzenden bağımsız `kasatest2026` da, harf harf (200–700 ms aralıklı) yazınca da
      "Passphrase incorrect"; Enter öncesi ekranda tutarlı olarak **3 nokta** (12 karakter beklenirken). TypeText her
      harfte ReturnValue=0. TR keymap (ckbcomp) k/a/s/t/e/rakamlar için ABD ile aynı → klavye düzeni elendi. Her deneme
      ~1,2 sn sürüyor (argon2 gerçekten hesaplanıyor). Hyper-V küçük resmi gecikmeli olabildiğinden nokta sayısı kesin kanıt
      değil. Ayrıca Plymouth ipucu satırı hiç görünmüyordu (normal_goster opaklığı 0 yapıp geri açmıyordu) → düzeltildi.
      **6. deneme hazır ama YAPILAMADI (03:10):** test katmanına klavyesiz doğrulama servisi eklendi
      (`armonia-kasa-test-dogrula` → `cryptsetup open --test-passphrase` sonucu COM1'e; üretim denetimine eklendi).
      Derleme Claude Code tarafından düşük bellek nedeniyle durduruldu (gece 3. kez) — sebep bendim: deneylerden sonra
      ArmoniaTest VM'sini (1,5 GB) açık bırakmıştım. VM kapatıldı. Kural gereği yeniden başlatma kullanıcı onayına bırakıldı.
      Devam: `TEST=1 iso_derle.sh` → `wsl --shutdown` → `test\hyperv_kasa_test.ps1`; COM1'de `KASA-DOGRULA` bloğu:
      rc=0 ise sorun giriş yolunda (Plymouth/TypeText), rc≠0 ise biçimlendirme/anahtar yuvasında.

      **6. deneme (kullanıcı "Devam" dedi, 03:20):** `KASA-DOGRULA` → **test-passphrase rc=0** (Key slot 0 unlocked,
      1,76 sn, 1,4 GB'ta 616 MB boş). Anahtar yuvası/biçimlendirme DOĞRU; sorun giriş yolunda (Plymouth'a ulaşan
      karakterler). Tek Enter sonrası cryptsetup iki kez deniyor (dikkat). 7. deneme: `evtest` ile tüm klavye
      aygıtlarındaki tuş basmaları COM1'e (yalnız test ISO'su; üretim denetimine eklendi).

      **7. deneme — KÖK NEDEN:** evtest: "kasatest2026" TypeText ile → misafire `KEY_RO, M, B, M, RIGHTSHIFT`. Hyper-V
      `Msvm_Keyboard.TypeText` Türkçe Windows'ta yanlış tarama kodu üretiyor; **üründe hata yoktu**. Test betiği
      `TypeScancodes` (PC set-1, basma+bırakma) kullanacak şekilde değiştirildi.

      **8. deneme (03:39) — FAZ 2 KASA UÇTAN UCA DOĞRULANDI:** 12 tuşun hepsi doğru ulaştı, parola tek denemede kabul,
      `kasa-durum: bagli`, LUKS2 aes-xts-plain64 512 bit, ext4 → `/srv/kasa` + `/var/lib/armonia/{CokluUyap,cokluuyap}`
      bind; zincir dev → cryptsetup → kasa-bagla (248 ms) → kiosk; ÇokluUYAP giriş kartı ekranda. Parola girişinden
      kiosk'a ~2 sn (argon2 ~1,2 sn). lynis (canlı) **hardening_index 71, uyarı 0**, 33 öneri. Sertleştirme bozulmadı
      (lockdown, nftables drop, sandbox 1,9, EPERM 0, herkese-yazılabilir 0). Bellek 1,4 GB VM'de 458 MB kullanılabilir.
      Not: `/etc/default/locale` 777 görünüyor → sembolik bağ (gerçek dosya değil). Plymouth ipucu düzeltmesi bu ISO'da
      var ama yanlış parola senaryosu tarama koduyla henüz denenmedi.

## D — Sabah raporu
- [x] D1 Aşağıda.

## SABAH ÖZETİ (güncel, 2026-11-14 05:15) — önce bunu okuyun
**Bitti ve Hyper-V'de doğrulandı**
1. **Faz 2 kasa:** Plymouth parola ekranı → LUKS2 (AES-256-XTS, argon2id) → kasa bağlı → kiosk. Yanlış parolada uyarı +
   yeniden sorma; TR-Q düzeni doğrulandı; parola kuralı (≥10, büyük/küçük/rakam/sembol, Türkçe karakter yok) betikte.
2. **Faz 2 PostgreSQL:** kasada, yalnız Unix soketi, peer; kiosk yalnız kendi DB'sine; `systemd-analyze security` 0,4.
3. **Bulunup düzeltilen gerçek hatalar:** uygulama verisi (hesaplar, İş Takip) RAM'deki program klasörüne gidiyordu;
   8843 TLS sertifikası + yerel giriş jetonu yazılamıyordu; toplu evrak RAM'e (şifresiz) gidiyordu; wall ajanı Plymouth
   parola ajanını öldürüyordu; Debian apparmor.service canlı sistemde hiç çalışmıyordu. Hepsi imaj ayarıyla düzeltildi
   (uygulama koduna dokunulmadı).
4. **Faz 3 AppArmor:** uyap_app profili yükleniyor (complain). lynis 71 / 0 uyarı.
5. **Üretim ISO'su** `C:\dev\armonia.iso` (539 MB): kasasız 12,9 sn, giriş kartı ekranda.
6. Her güvenlik yapılandırması guvenlik-denetci'den geçti; taslaklar Gemini'den (veri olarak) alındı.

**Açık / yapılamayan:** tarayıcı yok (girişten sonra UYAP görünmez — karar 1); AppArmor enforce; USBGuard; kurulum
sihirbazı (kasa biçimlendirme + rol işareti); gerçek donanım (Akis, yazıcı). Commit yapılmadı.

## Sabah özeti (2026-11-14, ~01:10)

**Bitti ve doğrulandı**
1. **Faz 1 kapandı.** ÇokluUYAP'lı ISO Hyper-V'de **8,1 sn**'de açılıyor (çekirdek 6,25 sn — toram kopyası dahil —
   + kullanıcı alanı 1,85 sn). Bellek 768 MB (475'i RAM'deki imaj), uyap_app 121 MB, labwc 24 MB. Xwayland yok,
   herkese-yazılabilir dosya 0, ağ çalışıyor, giriş kartı ekranda.
2. **Faz 3 sertleştirmesinin canlı ISO'ya giren kısmı bitti** (Gemini taslağı → Claude ayıklaması → güvenlik denetçisi →
   düzeltme → Hyper-V testi): lockdown=integrity, nftables (giriş kapalı), sysctl, modül kara listesi, kiosk sandbox
   (**systemd-analyze security 1,9 OK**), açılış menüsüne klavyeyle müdahale kapalı, live-config kaldırıldı.
   Açılış süresi bozulmadı (8,4 → 8,1 sn).
3. **rust-review** (17 ajan): 3 orta, 7 düşük bulgu, kod değiştirilmedi (ayrıntı A3).
4. **cargo-audit**: 10 açık; çoğu kullanılmayan fonksiyonlarda; gerçek iş rusqlite (SQLite) güncellemesi (ayrıntı A2).
5. `iso-derle` skill'i + tek komutluk Hyper-V test betiği; Hyper-V "Parametre hatalı" hatasının kök nedeni bulundu.

**Güncelleme (03:50, kullanıcı "Devam" dedikten sonra)**
- **Faz 2 kasa (C2) Hyper-V'de uçtan uca DOĞRULANDI** (8. deneme): Plymouth parola → LUKS2 açılıyor → kasa bağlı →
  kiosk + giriş kartı; lynis 71 / 0 uyarı. Ürün hatası: wall ajanı Plymouth parola ajanını durduruyordu (maskelendi).
  Test düzeneği hatası: Hyper-V TypeText Türkçe Windows'ta yanlış tuş gönderiyor (TypeScancodes'a geçildi).
- **Üretim ISO'su** (`C:\dev\armonia.iso`, 481 MB, sha256 07c4f840…) derlendi, test bileşeni denetiminden geçti.
  Kasa diski olmayan makinede açılış **12,3 sn** (6,6 + 5,7): Faz 1'e göre +4 sn, tamamı kasa bölümü bekleme süresi
  (`x-systemd.device-timeout=5s`). Kurulu cihazda bölüm var olacağından bu bekleme olmaz. Karar: canlı/kurulumsuz
  kullanım için süre 2–3 sn'ye indirilsin mi (yavaş diskte bölüm geç görünürse kasasız açılma riski)?
- **Kullanıcı kararı (04:00): parola kuralı** — Türkçe karakter yok; en az bir büyük, bir küçük, bir rakam, bir sembol; en az 10 karakter (04:05).
  `kasa/opt/armonia/bin/kasa_parola_kurali.sh` yazıldı ve 9 örnekle denendi; test parolası `Kasa-test2026` oldu
  (test biçimlendirmesi önce kuralı denetliyor). Hyper-V testi önce yanlış parola, sonra doğru parola giriyor; '-' TR-Q
  yerinden (ABD '=' tuşu) yazıldığından kabul edilirse Plymouth'un TR-Q düzenini kullandığı da doğrulanmış olur.
  **Sonuç (04:01):** yanlış parola reddedildi, kutu yeniden geldi, ipucu çıktı (ama kutucuk/tofu çizildi → "Sans 11"e
  geçildi); `Kasa-test2026` kabul, kasa bağlı → **Plymouth TR-Q düzenini kullanıyor (doğrulandı)**.
- **Kullanıcı kararı (04:10): PostgreSQL gerekli** ("dosyada zincirleme veriler için"). Tasarım: `kasa/` katmanında
  `armonia-postgresql.service` — veri `/srv/kasa/postgresql/veri`, ilk açılışta initdb (peer, TCP reject,
  data-checksums), yalnız Unix soketi (PrivateNetwork, listen_addresses=''), yalnız kasada `armonia/rol-sunucu`
  işareti varsa; hazır olunca `kiosk` rolü + `cokluuyap` DB + `/run/armonia/cokluuyap.env` (DATABASE_URL) → kiosk
  EnvironmentFile. Debian varsayılan kümesi hook'ta silinip postgresql.service maskeleniyor. guvenlik-denetci'de.
- **Bulunan hata (04:10):** uygulamanın veri dizini (`accounts.json`, `zs.sqlite3`, `zs_files`) `UYAP_DATA_DIR` yoksa
  programın klasörü (`/opt/armonia/cokluuyap`, RAM'de, root'a ait) oluyordu → kalıcı değildi. Kiosk servisine
  `UYAP_DATA_DIR=/var/lib/armonia/cokluuyap` (kasaya bağlı) eklendi.
- **PostgreSQL denetimi (04:25) — düzeltmeyle kullanılabilir;** uygulananlar: K1 imaj tarafı (Sunucu rolünde PG hazır
  değilse kiosk `kasa_denetle.sh` ile AÇILMAZ, PG `Restart=on-failure`), O1 PG ikilisi kasadaki PG_VERSION'a göre,
  O2 Debian kümesi silme doğrulaması + artık temizliği, O3 snakeoil anahtarı silindi, D1 soket `kiosk` grubu 0770,
  D3 ortam dosyasında tırnak, D4 huge_pages=off. **Kullanıcı kararı:** uygulama kodunda PG bağlanamazsa dosyaya/SQLite'a
  sessiz düşme (`accounts.rs:419-435`, `zs_store.rs:1284`) — `ARMONIA_PG_ZORUNLU` gibi bir bayrak eklensin mi
  (Windows ürünü ortak kod)? O4 yedek (pg_dump + LUKS başlık yedeği) Faz 6 / sihirbaz.
- **1. PG testi (04:12):** servis NAMESPACE (226) ile düştü — `ReadWritePaths=/srv/kasa/postgresql` ilk açılışta yok;
  ad alanı '+' önkomutları için de kuruluyor (denetçinin "sorun olmaz" dediği madde yanlış çıktı). Düzeltme: dizini
  kasa_bagla.sh oluşturuyor + `ReadWritePaths=-…`.
- **Faz 3 AppArmor (04:30):** Gemini taslağı → Claude ayıklaması (`sertlestirme/etc/apparmor.d/opt.armonia.cokluuyap.uyap_app`,
  complain kipi) → guvenlik-denetci "düzeltmeyle kullanılabilir". Uygulananlar: K1 `/run/user/**` yazma kaldırıldı +
  systemd --user/D-Bus soketleri yasak (user@989 gerçekten çalışıyor → exec yasağı aşılabilirdi); O1 `/tmp` (PrivateTmp);
  O3 yazılabilir yollarda `m` yok. **Profilden bağımsız gerçek hatalar bulundu ve düzeltildi:** K2 LAN TLS (8843)
  sertifikası + yerel giriş jetonu `$HOME/.config` (root'a ait) altına yazılamıyordu → `XDG_CONFIG_HOME` kasaya;
  O2 toplu evrak `$HOME/Documents` (RAM, şifresiz) → `UYAP_EVRAK_KLASORU` kasaya. Derlemede `apparmor_parser -Q -K`.
  Sıradaki: complain kipinde ALLOWED kayıtları → O4/O5 daraltma → enforce.
- **2. PG testi (04:23):** soket grubu (`unix_socket_group=kiosk`) chown gerektiriyor, seccomp `~@privileged` engelledi →
  soket postgres grubunda 0770, kiosk postgres grubuna eklendi. Tasarım doğrulandı: PG yokken kiosk açılmadı (pg-durum HATA).
- **3. PG testi (04:32) — PostgreSQL UÇTAN UCA ÇALIŞIYOR:** servis active, 5432 TCP yok (yalnız soket),
  uyap_app DATABASE_URL ile açıldı, kiosk `cokluuyap`'a bağlanıyor, `postgres` DB'ye "permission denied", `nobody`
  sokete "Permission denied", snakeoil yok, `systemd-analyze security` **0,4 SAFE**, veri kasada (41 MB).
  (Tablo sayısı 0: uygulama tabloları ilk giriş/kullanımda oluşturuyor — giriş yapılmadı.)
  **AppArmor yüklenmedi:** Debian `apparmor.service` canlı sistemde kendini atlıyor
  (`ConditionPathExists=!/run/live/overlay/work`) → drop-in ile koşullar sıfırlandı; imajdaki ~100 gereksiz profil
  (firefox, lxc, sbuild…) hook'ta siliniyor (açılışta derleme süresi).
- **AppArmor testi (04:52) — profil yükleniyor:** `uyap_app (complain)`, apparmor.service 255 ms. Giriş ekranına kadar
  8 ALLOWED: `dbus-send` ×4 ve `fc-match` ×2 çalıştırma (kaynak: winit'in pencere süsü kütüphanesi sctk-adwaita —
  karanlık tema + başlık yazı tipi sorgusu; isteğe bağlı → açıkça yasaklandı), `/etc/glvnd`, `/dev/udmabuf` (eklendi).
  **Enforce'a bu gece GEÇİLMEDİ (bilinçli):** test yalnız giriş ekranına kadar; UYAP girişi, e-imza, PTT, evrak, LAN
  paylaşımı sınanamadı — eksik izin üretimde uygulamayı kırar. Yapılacak: gerçek kullanımda complain kayıtları
  (`journalctl -k | grep 'profile="uyap_app"'`) → O4/O5 daraltma → `flags=(attach_disconnected)` (complain'i kaldır).
  Plymouth yanlış parola ipucu artık düzgün ("Sans 11" düzeltmesi doğrulandı).
- **Üretim ISO'su (04:52, sha256 7ba66101…, 539 MB):** kasasız açılış **12,9 sn** (7,2 + 5,7; önceki 12,3 —
  +0,6 sn, çoğu PG paketiyle büyüyen RAM kopyası), giriş kartı ekranda, AppArmor complain, PG başlamıyor (rol yok).
- **Önemli bulgu — tarayıcı yok:** uygulama UYAP arayüzünü `xdg-open http://127.0.0.1:8800/giris…` ile tarayıcıda
  açıyor (`uyap_app.rs:1335-1345`); imajda tarayıcı yok → girişten sonra kullanıcı UYAP'ı göremez. Yol haritası Faz 4:
  Ungoogled-Chromium. Seçenekler (Debian chromium / Ungoogled OBS / firefox-esr / WPE cog) araştırılıyor →
  `C:\dev\tarayici_secenekleri.md`. **Kullanıcı kararı gerekli** (boyut ↔ açılış süresi, dış depo riski).
  Araştırma (05:05): öneri **Debian `chromium` + yönetilen politika** (Debian Security günceller, ek depo yok,
  WebRTC/e-Duruşma; URLBlocklist `*` + URLAllowlist 127.0.0.1:8800 / uyap.gov.tr; kurulu ~391 MB). Yedek firefox-esr
  (~302 MB). Ungoogled-Chromium'un Debian 13 deposu yok (OBS boş); WPE/cog trixie'de güvenlik desteği almıyor → red.
  Ölçüm için `TARAYICI=1` deneme ISO'su (`deneme-tarayici/`: chromium + politika + `xdg-open` yerine yalnız
  127.0.0.1:8800 kabul eden kiosk başlatıcı → ruyap kodu değişmeden) derleniyor. Not: kiosk servisinin
  RestrictNamespaces/NoNewPrivileges ayarları Chromium kum havuzunu engeller — ayrı güvenlik kararı.
- **USBGuard taslağı (Gemini, 04:45):** `C:\dev\gemini_usbguard_taslak.md` — varsayılan inkâr, IPC yalnız root,
  BadUSB'ye karşı birleşik (HID+depolama vb.) aygıt reddi; seçenekler: **A sıkı** (yalnız klavye/fare, e-imza okuyucu,
  hub), **B dengeli** (A + yazıcı/tarayıcı; USB bellek kapalı, yönetici tek seferlik açar), **C gevşek** (USB bellek de
  açık, yalnız saf depolama arayüzü). **İmaja KONMADI — kullanıcı kararı:** USB bellek politikası (avukatlar belge
  getirebilir). Akis okuyucu sınıf kodları gerçek cihazda `lsusb -v` ile doğrulanmalı. Denetçiden geçirilmedi.
- **Tarayıcı deneme ölçümü (05:10, `C:\dev\armonia_tarayici_deneme.iso`):** ISO 717 MB (+178), squashfs 648 MB
  (+177), açılış **14,3 sn** (+1,4 sn; çekirdek 8,4 — RAM kopyası), 1,4 GB VM'de kullanılabilir bellek 349 MB,
  Chromium 156.0.8078.79 (Debian), /usr/lib/chromium 381 MB. Chromium'un gerçekten açılıp UYAP'ı gösterdiği SINANMADI.

**Kullanıcı kararı gerekenler (güncel, 05:15)**
> **2026-11-14 sabah kararları:** Tarayıcı = **Debian Chromium + yönetilen politika**. USBGuard = **A (sıkı: yalnız
> klavye, fare, e-imza okuyucu)**. Kalan maddeler açık.
1. **Tarayıcı:** Debian `chromium` + politika mı (önerilen; +1,4 sn, +177 MB), firefox-esr mi? Chromium kum havuzu için
   kiosk servisinde kullanıcı ad alanına izin (RestrictNamespaces=~user) verilsin mi? E-Duruşma kamera izni otomatik mi?
2. **USBGuard:** A / B / C (USB bellek politikası).
3. **Uygulama kodu:** Sunucu rolünde PG yoksa SQLite/dosyaya sessiz düşme kapatılsın mı (`ARMONIA_PG_ZORUNLU`)? Kiosk şu an
   PG yoksa açılmıyor (kara ekran) — kullanıcıya "Veritabanı hazırlanıyor" gibi bir ekran gerekir.
4. rust-review ORTA bulguları + bağımlılık güncellemeleri (rusqlite, zip ≥ 9, quick-xml) — ortak kod (Windows'u da etkiler).
5. Kasa: kurtarma anahtarı + LUKS başlık yedeği; TR-Q mı TR-F mi. (Parola kuralı KARARLAŞTI: ≥10, büyük+küçük+rakam+sembol, ASCII.)
6. Kasa diski yoksa bekleme 5 sn → 2–3 sn?
7. Güvenlik duvarı giden beyaz liste / kill-switch, mDNS, LAN 8843.
8. AppArmor enforce: gerçek kullanımda (giriş, e-imza, PTT, evrak) complain kayıtları toplansın, sonra enforce.

## Kayıt
- 00:05 plan + saatlik cron · 00:28 A1 ölçümü · 00:39 B3 sertleştirilmiş ISO testi · 00:55 kasa 1. deneme ·
  01:05 test ISO derlemesi bellek nedeniyle durduruldu · 01:10 sabah özeti · 03:39 kasa uçtan uca · 04:01 TR-Q +
  yanlış parola · 04:32 PostgreSQL uçtan uca · 04:52 AppArmor yükleniyor + üretim ISO 12,9 sn · 05:10 tarayıcı denemesi ·
  05:15 güncel sabah özeti.
