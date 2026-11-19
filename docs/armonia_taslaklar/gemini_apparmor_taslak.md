Aşağıda Debian 13 (trixie, AppArmor 4.x, Linux 6.12) canlı imajı üzerinde çalışan `/opt/armonia/cokluuyap/uyap_app` uygulaması için hazırlanan AppArmor profil taslağı yer almaktadır.

### Debian 13 (trixie) `apparmor` (4.x) Paketindeki Soyutlamalar (Abstractions)

Debian 13 `apparmor` paketinde `/etc/apparmor.d/abstractions/` altında aşağıdaki soyutlamalar **yer almaktadır**:
* `abstractions/base`: Temel C/Rust çalışma zamanı kütüphaneleri (`/usr/lib/**`, `/etc/ld.so.cache`), `/dev/null`, `/dev/urandom`, zaman dilimi (`/etc/localtime`, `/usr/share/zoneinfo/**`), yerel ayarlar (`/usr/share/locale/**`) ve temel `/proc`/`/sys` CPU bilgisi okumalarını içerir.
* `abstractions/fonts`: `/usr/share/fonts/**`, `/etc/fonts/**`, `/var/cache/fontconfig/**` ve `@{HOME}/.cache/fontconfig/**` kurallarını içerir.
* `abstractions/nameservice`: DNS ve isim çözümleme dosyaları (`/etc/resolv.conf`, `/etc/hosts`, `/etc/nsswitch.conf`, `/etc/gai.conf`, `/etc/host.conf`, `/run/systemd/resolve/**`) ile temel `inet`/`inet6` (`stream`/`dgram`) ve `netlink raw` soket izinlerini içerir.
* `abstractions/ssl_certs`: `/etc/ssl/certs/**` ve `/usr/share/ca-certificates/**` salt okunur erişimlerini içerir.
* `abstractions/wayland`: `owner /{,var/}run/user/*/wayland-* rw,` soket erişimini içerir.
* `abstractions/dri-enumerate` ve `abstractions/dri-common`: `/sys/devices/pci*/**`, `/sys/class/drm/**`, `/dev/dri/card*` ve `/usr/share/drirc.d/**` erişimlerini içerir.
* `abstractions/mesa`: `/dev/dri/renderD*` ve `@{HOME}/.cache/mesa_shader_cache*/**` kurallarını içerir.

> **Önemli `@{HOME}` ve Canlı İmaj (Live Image) Notu:**
> AppArmor'un varsayılan `tunables/home` tanımı yalnızca `/home/*` ve `/root` dizinlerini kapsar; `kiosk` kullanıcısının ev dizini olan `/var/lib/armonia` varsayılan `@{HOME}` değişkenine **dahil değildir**. Bu nedenle profil içinde `@{HOME}` değişkeni genişletilmiş ve `/var/lib/armonia/**` yolları açıkça tanımlanmıştır. Ayrıca Debian canlı imajlarında (`overlayfs`) ve LUKS bind-mount yapılarında ayrık yol (disconnected path) hatalarını önlemek için `attach_disconnected` bayrağı eklenmiştir.

---

### `/etc/apparmor.d/opt.armonia.cokluuyap.uyap_app`

```apparmor
# =============================================================================
# AppArmor Profili: /opt/armonia/cokluuyap/uyap_app
# Hedef Sistem   : Debian 13 (trixie), AppArmor 4.x, Linux çekirdeği 6.12
# Çalışma Bağlamı: systemd (User=kiosk, uid=989, HOME=/var/lib/armonia)
#                  labwc Wayland bileşimcisi içinde /opt/armonia/bin/oturum.sh
#                  tarafından başlatılır (cwd=/var/lib/armonia/cokluuyap).
# =============================================================================

abi <abi/4.0>,

#include <tunables/global>

# kiosk kullanıcısının ev dizini (/var/lib/armonia) varsayılan @{HOME} içinde
# yer almadığından soyutlamaların (fonts, mesa vb.) doğru çalışması için eklenir.
@{HOME} += /var/lib/armonia

# Canlı imaj (overlayfs) ve LUKS bind-mount kaynaklı ayrık yol (disconnected path)
# çözümlemelerinde erişim reddi yaşanmaması için attach_disconnected etkinleştirilir.
# DOĞRULANACAK: Canlı imajda overlayfs/bind-mount davranışı aa-complain ile test edilmeli.
profile uyap_app /opt/armonia/cokluuyap/uyap_app flags=(attach_disconnected) {

  # ---------------------------------------------------------------------------
  # 1. TEMEL SOYUTLAMALAR (Debian 13 / AppArmor 4.x içinde mevcuttur)
  # Gerekçe: Paylaşımlı kütüphaneler, temel sistem dosyaları, DNS/ağ, yazı
  # tipleri, TLS kök sertifikaları, Wayland ve Mesa/DRI altyapısı.
  # ---------------------------------------------------------------------------
  #include <abstractions/base>
  #include <abstractions/nameservice>
  #include <abstractions/fonts>
  #include <abstractions/ssl_certs>
  #include <abstractions/wayland>
  #include <abstractions/dri-common>
  #include <abstractions/dri-enumerate>
  #include <abstractions/mesa>

  # ---------------------------------------------------------------------------
  # 2. UYGULAMA İKİLİSİ VE SALT OKUNUR UYGULAMA DOSYALARI
  # Gerekçe: İkilinin belleğe eşlenmesi (mr) ve webapp/, enjekte/, models/,
  # ice_servers.json dahil statik uygulama kaynaklarının salt okunur erişimi.
  # ---------------------------------------------------------------------------
  /opt/armonia/cokluuyap/uyap_app mr,
  /opt/armonia/cokluuyap/ r,
  /opt/armonia/cokluuyap/** r,

  # ---------------------------------------------------------------------------
  # 3. ÇALIŞMA DİZİNİ VE VERİ DEPOSU (LUKS Bind-Mount)
  # Gerekçe: cwd (/var/lib/armonia/cokluuyap), static_cache (~190 MB),
  # accounts.json, zs.sqlite3 (-wal/-shm/-journal kilitleri için 'k' izni şarttır),
  # zs_files/ ve CokluUyap/ ayar dizini.
  # ---------------------------------------------------------------------------
  owner /var/lib/armonia/ r,
  owner /var/lib/armonia/** rwk,

  # Mesa ve fontconfig önbellek dosyaları mmap (m) gerektirebilir:
  owner /var/lib/armonia/.cache/fontconfig/** mrwk,
  owner /var/lib/armonia/.cache/mesa_shader_cache/** mrwk,
  owner /var/lib/armonia/.cache/mesa_shader_cache_db/** mrwk,
  owner /var/lib/armonia/.cache/mesa_shader_cache_sf/** mrwk,

  # DOĞRULANACAK: /var/lib/armonia bir LUKS kasasından bind-mount edildiği için,
  # çekirdek yolu kaynak bağlama noktasına (örn. /mnt/luks/... veya /run/...)
  # çözümlüyorsa gerçek kaynak yol da buraya eklenmelidir.

  # ---------------------------------------------------------------------------
  # 4. WAYLAND VE XDG_RUNTIME_DIR (/run/user/989)
  # Gerekçe: labwc Wayland soketi (wayland-0) ve winit/egui/Mesa paylaşımlı
  # bellek (wl_shm / memfd / imleç tamponları) dosyaları.
  # ---------------------------------------------------------------------------
  owner /run/user/989/ r,
  owner /run/user/989/wayland-0 rw,
  owner /run/user/989/** mrwk,

  # Paylaşımlı bellek ve memfd tamponları (winit / Mesa / llvmpipe):
  owner /dev/shm/** mrwk,
  # DOĞRULANACAK: Kernel 6.12 + AppArmor 4.x üzerinde anonim memfd_create()
  # tamponları için "/memfd:* mrw," kuralının gerekip gerekmediği loglardan izlenmeli.

  # ---------------------------------------------------------------------------
  # 5. GPU, OPENGL (glow) VE MESA / LLVMPIPE ÇİZİM ALTYAPISI
  # Gerekçe: Donanım DRI düğümleri veya Mesa yazılım çizici (llvmpipe/swrast),
  # PCI/DRM cihaz numaralandırması, libdrm kimlikleri ve glvnd yapılandırmaları.
  # ---------------------------------------------------------------------------
  /dev/dri/ r,
  /dev/dri/card[0-9]* rw,
  /dev/dri/renderD[0-9]* rw,

  /sys/devices/pci[0-9]*/** r,
  /sys/devices/platform/** r,          # DOĞRULANACAK: SoC/sanal GPU platform cihazları
  /sys/class/drm/ r,
  /sys/class/drm/** r,
  /sys/bus/pci/devices/ r,

  /usr/lib/x86_64-linux-gnu/dri/ r,
  /usr/lib/x86_64-linux-gnu/dri/** mr,
  /usr/lib/x86_64-linux-gnu/gbm/** mr, # DOĞRULANACAK: Mesa GBM arka ucu yüklenirse
  /usr/lib/x86_64-linux-gnu/egl/** mr, # DOĞRULANACAK: EGL kütüphaneleri ayrı dizindeyse

  /usr/share/glvnd/ r,
  /usr/share/glvnd/** r,
  /usr/share/drirc.d/ r,
  /usr/share/drirc.d/** r,
  /usr/share/libdrm/ r,
  /usr/share/libdrm/** r,

  # ---------------------------------------------------------------------------
  # 6. KLAVYE (XKB / libxkbcommon)
  # Gerekçe: Wayland istemcisinin (winit) klavye düzeni haritalarını okuması.
  # ---------------------------------------------------------------------------
  /usr/share/X11/xkb/ r,
  /usr/share/X11/xkb/** r,

  # ---------------------------------------------------------------------------
  # 7. YAZI TİPLERİ VE FONTCONFIG
  # Gerekçe: egui/eframe metin çizimi için sistem yazı tipleri ve önbelleği.
  # (abstractions/fonts içinde de bulunur; netlik ve tam kapsam için açıktır).
  # ---------------------------------------------------------------------------
  /usr/share/fonts/ r,
  /usr/share/fonts/** r,
  /usr/local/share/fonts/ r,
  /usr/local/share/fonts/** r,
  /etc/fonts/ r,
  /etc/fonts/** r,
  /var/cache/fontconfig/ r,
  /var/cache/fontconfig/** mr,

  # ---------------------------------------------------------------------------
  # 8. AĞ, DNS VE TLS SERTİFİKALARI (tokio, rustls + native-certs)
  # Gerekçe:
  # - TCP istemci (HTTPS UYAP sunucuları) ve TCP dinleme (127.0.0.1:8800,
  #   paylaşım kipinde 0.0.0.0:8800 ve 8843)
  # - UDP 4430 yerel ağ yayın/keşif
  # - Netlink route (ağ arayüzlerinin listelenmesi)
  # - DNS çözümleme ve sistem kök sertifika deposu
  # Not: AppArmor'da TCP/UDP izinleri soket ailesi/tipi düzeyindedir; port bazlı
  # kısıtlama (8800, 8843, 4430) gerekirse systemd servis biriminde
  # SocketBindAllow= / nftables ile tamamlanmalıdır.
  # ---------------------------------------------------------------------------
  network inet stream,
  network inet6 stream,
  network inet dgram,
  network inet6 dgram,
  network netlink raw,

  /etc/resolv.conf r,
  /run/systemd/resolve/ r,
  /run/systemd/resolve/** r,
  /etc/nsswitch.conf r,
  /etc/hosts r,
  /etc/host.conf r,
  /etc/gai.conf r,

  /etc/ssl/certs/ r,
  /etc/ssl/certs/** r,
  /usr/share/ca-certificates/ r,
  /usr/share/ca-certificates/** r,
  /etc/ca-certificates.conf r,         # DOĞRULANACAK: rustls-native-certs ihtiyaç duyarsa

  # ---------------------------------------------------------------------------
  # 9. POSTGRESQL UNIX SOKETİ (rust-postgres)
  # Gerekçe: Yerel PostgreSQL sunucusuna (/run/postgresql/.s.PGSQL.5432) bağlantı.
  # ---------------------------------------------------------------------------
  network unix stream,
  /run/postgresql/.s.PGSQL.5432 rw,

  # ---------------------------------------------------------------------------
  # 10. AKILLI KART / E-İMZA (İLERİDE ETKİNLEŞTİRİLECEK - ŞİMDİLİK YORUMLU)
  # Gerekçe: pcscd iletişim soketi ve TÜBİTAK Akis PKCS#11 kütüphanesi.
  # ---------------------------------------------------------------------------
  # /run/pcscd/pcscd.comm rw,
  # /usr/lib/x86_64-linux-gnu/libakisp11.so mr,
  # /etc/akis/** r,                    # DOĞRULANACAK: Akis sistem yapılandırma yolu
  # owner /var/lib/armonia/.akis/** rwk, # DOĞRULANACAK: Akis kullanıcı ayar/önbellek yolu

  # ---------------------------------------------------------------------------
  # 11. SÜREÇ (/proc), CGROUP (/sys/fs/cgroup), ZAMAN VE YEREL AYARLAR
  # Gerekçe: tokio çalışma zamanının çekirdek/CPU kotası tespiti, Rust bellek/iş
  # parçacığı yönetimi, saat dilimi ve yerel dil dosyaları.
  # ---------------------------------------------------------------------------
  owner @{PROC}/@{pid}/stat r,
  owner @{PROC}/@{pid}/status r,
  owner @{PROC}/@{pid}/maps r,
  owner @{PROC}/@{pid}/cgroup r,
  owner @{PROC}/@{pid}/mountinfo r,    # DOĞRULANACAK: cgroup v2 bağlama noktası tespiti için
  owner @{PROC}/@{pid}/fd/ r,          # DOĞRULANACAK: rusqlite / tokio fd numaralandırması
  owner @{PROC}/@{pid}/task/ r,
  owner @{PROC}/@{pid}/task/** r,
  owner @{PROC}/@{pid}/comm rw,        # DOĞRULANACAK: tokio iş parçacığı isimlendirme (prctl)

  @{PROC}/sys/kernel/random/uuid r,    # DOĞRULANACAK: UUID üretimi için okunup okunmadığı

  /sys/fs/cgroup/ r,
  /sys/fs/cgroup/** r,
  /sys/devices/system/cpu/ r,
  /sys/devices/system/cpu/** r,

  /etc/localtime r,
  /usr/share/zoneinfo/ r,
  /usr/share/zoneinfo/** r,
  /usr/share/locale/ r,
  /usr/share/locale/** r,
  /usr/lib/locale/ r,
  /usr/lib/locale/** mr,

  # ---------------------------------------------------------------------------
  # 12. AÇIK YASAKLAR (EXEC, PTRACE, MOUNT, RAW SOCKET, CAPABILITY)
  # Gerekçe: Kiosk dışına çıkışı (xdg-open, kabuk veya başka ikili çalıştırma),
  # süreç izlemeyi, dosya sistemi bağlamayı, ham ağ paketlerini ve tüm Linux
  # yeteneklerini (capabilities) açıkça engeller ve denetim logu gürültüsünü keser.
  # ---------------------------------------------------------------------------
  deny /usr/bin/xdg-open x,
  deny /bin/** x,
  deny /usr/bin/** x,
  deny /sbin/** x,
  deny /usr/sbin/** x,
  deny /usr/local/** x,
  deny /opt/armonia/bin/** x,
  deny /var/** x,
  deny /tmp/** x,
  deny /run/** x,
  deny /dev/shm/** x,

  deny ptrace,
  deny mount,
  deny umount,
  deny pivot_root,

  deny network inet raw,
  deny network inet6 raw,
  deny network packet,

  deny capability,
}
```

---

### Test ve Devreye Alma Notu (`aa-complain` → `aa-enforce`)

Profili doğrudan `enforce` kipinde devreye almak yerine, canlı imaj (`overlayfs`), LUKS bind-mount ve `llvmpipe`/OpenGL davranışlarını doğrulamak için önce **complain (şikâyet/gözlem)** kipinde çalıştırın:

1. **Paketleri ve profil sözdizimini doğrulayın:**
   ```bash
   sudo apt update && sudo apt install -y apparmor-utils auditd
   sudo apparmor_parser -Q -d /etc/apparmor.d/opt.armonia.cokluuyap.uyap_app
   ```

2. **Profili `complain` kipine alıp uygulamayı yeniden başlatın:**
   ```bash
   sudo aa-complain /etc/apparmor.d/opt.armonia.cokluuyap.uyap_app
   sudo systemctl restart <kiosk-servis-adi>.service
   ```

3. **Tüm uygulama iş akışlarını test edin ve `ALLOWED` kayıtlarını inceleyin:**
   Uygulamada oturum açma, SQLite (`zs.sqlite3`) okuma/yazma, PostgreSQL bağlantısı, TLS/HTTPS UYAP istekleri, paylaşım kipi (`8800`/`8843`) ve UDP `4430` keşif senaryolarını çalıştırın. Ardından `DOĞRULANACAK` olarak işaretli kuralları ve eksik yolları kontrol edin:
   ```bash
   sudo journalctl -k | grep -E 'apparmor="ALLOWED".*profile="uyap_app"'
   # veya etkileşimli kural önerisi için:
   sudo aa-logprof
   ```

4. **Hiçbir beklenmedik `ALLOWED` kaydı kalmadığında `enforce` kipine geçin:**
   ```bash
   sudo aa-enforce /etc/apparmor.d/opt.armonia.cokluuyap.uyap_app
   sudo systemctl restart <kiosk-servis-adi>.service
   sudo aa-status
   ```
