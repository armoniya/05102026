# Debian 13 (Trixie) Kiosk Sertleştirme Yapılandırma Planı (Armonia / UYAP)

## Hedef Tanımı
Bu plan; Debian 13 (trixie) üzerinde `live-build` ile üretilen, `systemd-networkd` ve `systemd-resolved` kullanan, SSH ve uzaktan yönetim barındırmayan, Wayland (`labwc`) altında tek bir Rust (`egui`) kiosk uygulamasını (`/opt/armonia/cokluuyap/uyap_app`) çalıştıran sistemin sertleştirilmesini kapsar.

Sistem gereksinimleri:
- **Uygulama:** HTTPS dış bağlantı, `127.0.0.1:8800` yerel HTTP vekili (gelecekte LAN erişimi opsiyonlu), WebRTC / UDP trafiği.
- **Donanım:** PKCS#11 USB akıllı kart okuyucu (Akis, `pcscd` uyumlu).
- **Grafik:** Wayland (`labwc`), DRM/KMS, Mesa GPU hızlandırma.
- **Kısıt:** Kicksecure `security-misc` referans alınacak; fakat açılışı geciktirmeyen, akıllı kart, GPU/Wayland ve WebRTC'yi bozmayan ayarlar seçilecek. Abartılı güvenlik iddiaları yerine somut fayda-risk analizleri yapılacak.

---

## Kullanıcı İncelemesi Gereken Kritik Noktalar

> [!WARNING]
> **Kiosk ve Grafik/Donanım Kırılma Riskleri:**
> 1. **`MemoryDenyWriteExecute=yes` KULLANILMAMALIDIR:** Mesa (GPU shader derleyicisi, LLVMpipe) ve WebRTC motorları dinamik JIT/W^X bellek sayfaları oluşturur. Bu ayar açılırsa labwc veya Rust/egui uygulaması segfault ile çöker.
> 2. **`PrivateDevices=yes` KULLANILMAMALIDIR:** `/dev/dri/card*`, `/dev/dri/renderD*`, `/dev/input/*` ve `/dev/bus/usb/*` aygıtlarını gizler. Logind ve PAM üzerinden devredilse dahi doğrudan USB akıllı kart (CCID) erişimini bozar. Yerine `DevicePolicy=closed` + açık aygıt kuralları veya `PrivateDevices=no` tercih edilir.
> 3. **Squashfs / Overlay / ISO9660 Modülleri:** Kicksecure dosya sistemi kara listelerinde yer alan `squashfs` ve `overlayfs`, Debian `live-boot` mekanizmasının omurgasıdır. Bunlar kesinlikle kara listeye eklenemez.
> 4. **IPv6 Router Advertisements (`accept_ra`):** `net.ipv6.conf.all.accept_ra=0` yapılırsa, SLAAC veya DHCPv6 kullanan modern kurumsal ağlarda IPv6 yapılandırması tamamen çöker.

---

## 1. /etc/sysctl.d/90-armonia.conf

Aşağıdaki ayarlar Kicksecure `security-misc` tabanından seçilmiş olup, kiosk çalışma ortamı (USB, GPU, WebRTC, hızlı açılış) gözetilerek optimize edilmiştir.

```ini
# /etc/sysctl.d/90-armonia.conf
# Armonia Kiosk - Debian 13 (Trixie) Çekirdek Sertleştirme Parametreleri

# --- Dosya Sistemi Bağlantı ve Bilgi Sızıntısı Korumaları ---
# Neden: /tmp ve /var/tmp gibi paylaşımlı dizinlerde sembolik/sert bağ (symlink/hardlink) yarış saldırılarını (TOCTOU) önler.
# Kırma Riski: Yok (kiosk tek kullanıcı ve standart dosya dizilimindedir).
fs.protected_symlinks = 1
fs.protected_hardlinks = 1

# Neden: Kötü niyetli kullanıcıların başka kullanıcılara ait FIFO ve normal dosyalara yazmasını/okumasını engeller.
# Kırma Riski: Yok.
fs.protected_fifos = 2
fs.protected_regular = 2

# Neden: Çökme durumunda hassas bellek içeriklerinin disk dökümüne (core dump) yazılmasını engeller (bellekteki PKCS#11 pin/anahtar koruması).
# Kırma Riski: Hata ayıklama (coredump) devre dışı kalır; üretim ortamında risk oluşturmaz.
fs.suid_dumpable = 0

# --- Çekirdek ve Bellek Görünürlüğü ---
# Neden: /proc/kallsyms ve sysfs üzerinden çekirdek bellek adreslerinin okunmasını engeller; ROP/ASLR atlatma saldırılarını zorlaştırır.
# Kırma Riski: Root olmayan kullanıcılar çekirdek adreslerini göremez; Wayland, Akis ve kiosk uygulamasını etkilemez.
kernel.kptr_restrict = 2

# Neden: dmesg çıktısını yalnızca CAP_SYSLOG yetkisine sahip süreçlerle sınırlandırır, donanım/bellek sızıntısını önler.
# Kırma Riski: Normal kullanıcı dmesg okuyamaz; kiosk kullanıcısı için sorunsuzdur (logind/udev arka planda çalışır).
kernel.dmesg_restrict = 1

# Neden: Ayrıcalıksız kullanıcıların perf olaylarını ve donanım sayaçlarını izleyip yan kanal (side-channel) saldırısı yapmasını engeller.
# Kırma Riski: Yok; kiosk perf/profiling aracı kullanmaz.
kernel.perf_event_paranoid = 3

# Neden: Kiosk kullanıcısının çekirdeğe eBPF bytecode yüklemesini engeller.
# Kırma Riski: systemd/socket filtreleri root olarak çalışır; kiosk uygulamasının BPF gereksinimi yoktur.
kernel.unprivileged_bpf_disabled = 1

# Neden: eBPF Just-In-Time (JIT) derleyicisine karşı JIT püskürtme (spraying) saldırılarını zorlaştırır.
# Kırma Riski: Yok; performans kaybı ihmal edilebilir düzeydedir.
net.core.bpf_jit_harden = 2

# Neden: Bir sürecin yalnızca alt süreçlerini trace etmesine (ptrace_scope=1) veya yalnızca root'un trace etmesine izin verir.
# Kırma Riski: ptrace_scope=2 durumunda debugger çalışmaz; kiosk için uygundur.
kernel.yama.ptrace_scope = 2

# Neden: Çalışan sistem üzerinden kexec ile yetkisiz yeni çekirdek yüklenmesini engeller.
# Kırma Riski: Yok; sistem normal systemd/reboot kullanır.
kernel.kexec_load_disabled = 1

# Neden: Kiosk klavyesinden Magic SysRq tuş kombinasyonlarıyla (Alt+SysRq+X) sistemin düşürülmesini veya kabuk açılmasını önler.
# Kırma Riski: Kiosk fiziksel güvenliği için gereklidir; acil yerinde bakım tuş kombinasyonları kapanır.
kernel.sysrq = 0

# Neden: Ayrıcalıksız süreçlerin userfaultfd kullanarak çekirdek yarış durumlarını (race condition) tetiklemesini önler.
# Kırma Riski: Yok (yalnızca bazı sanallaştırma/canlı göç araçları kullanır).
vm.unprivileged_userfaultfd = 0

# Neden: Eski TTY satır disiplinlerinin otomatik modül tetiklemesini önler.
# Kırma Riski: Yok; kiosk yalnızca standart TTY/KMS konsolunu kullanır.
dev.tty.ldisc_autoload = 0

# --- Ağ Katmanı (IPv4/IPv6) Sertleştirmesi ---
# Neden: TCP SYN flood saldırılarına karşı SYN çerezlerini zorunlu kılar.
# Kırma Riski: Yok; bağlantı doyumunda iletişimin kopmasını engeller.
net.ipv4.tcp_syncookies = 1

# Neden: TCP zaman aşımı (TIME-WAIT) suikast saldırılarına (RFC 1337) karşı koruma sağlar.
# Kırma Riski: Yok; TCP oturum kararlılığını artırır.
net.ipv4.tcp_rfc1337 = 1

# Neden: IP spoofing ve kaynak adresi sahteciliğini ters yol filtrelemesiyle (strict reverse path) engeller.
# Kırma Riski: Asimetrik çoklu ağ kartı senaryolarında paket düşebilir; tek arayüzlü DHCP kiosk için sorunsuzdur.
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1

# Neden: Ağ yönlendirme yönlendiricisi (ICMP redirect) paketlerini reddeder; Man-in-the-Middle (MitM) yönlendirmesini engeller.
# Kırma Riski: Yok; kiosk bir uç noktadır, harici yönlendirme tablolarını kabul etmemelidir.
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.secure_redirects = 0
net.ipv4.conf.default.secure_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0

# Neden: Kioskun kendisinin ICMP yönlendirme paketi üretmesini engeller.
# Kırma Riski: Yok; kiosk router değildir.
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0

# Neden: Kaynak yönlendirmeli (Source Routed) IP paketlerini düşürür; paket yolunun sahte belirlenmesini önler.
# Kırma Riski: Yok; modern ağlarda kullanılmaz.
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv6.conf.default.accept_source_route = 0

# Neden: Sahte ve broadcast ICMP eko isteklerine yanıt vererek Smurf DoS saldırısına alet olmayı engeller.
# Kırma Riski: Yok.
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.icmp_ignore_bogus_error_responses = 1

# DİKKAT: net.ipv6.conf.*.accept_ra bilerek 0 YAPILMAMIŞTIR.
# Kırma Riski Uyarısı: accept_ra=0 yapılırsa IPv6 ağlarında Router Advertisement alınamaz ve IPv6 bağlantısı çöker.
```

---

## 2. Çekirdek Komut Satırı Ek Parametreleri (Live-Boot Uyumlu)

Aşağıdaki satır `live-build` ortamında önyükleyici yapılandırmasına (`LB_BOOTLOADER_BIOS` / `syslinux` / `grub` live menü girdisine) doğrudan eklenebilir. `init_on_free=1` açılışı ve bellek serbest bırakmayı yavaşlattığı için elenmiş; yalnızca açılışa etki etmeyen ve canlı imajı bozmayan parametreler seçilmiştir.

```text
boot=live components quiet splash slab_nomerge init_on_alloc=1 page_alloc.shuffle=1 vsyscall=none debugfs=off lockdown=integrity intel_iommu=on amd_iommu=on iommu=force
```

*Parametre Açıklamaları ve Analiz:*
- `boot=live components quiet splash`: Canlı sistem için zorunlu Debian live-boot parametreleri.
- `slab_nomerge`: Çekirdek SLAB bellek havuzlarının birleştirilmesini engeller; heap taşması izolasyonu sağlar (Açılış gecikmesi: Sıfır).
- `init_on_alloc=1`: Yeni tahsis edilen sayfaları sıfırlar; use-after-free ve bellek sızıntılarını engeller (Açılış gecikmesi: < %1, hissedilmez).
- `page_alloc.shuffle=1`: Sayfa tahsisçisi sırasını rastgeleleştirir; sayfa yerleşimi tahminini zorlaştırır (Açılış gecikmesi: Yok).
- `vsyscall=none`: Eski ve sabit adresli vsyscall tablosunu tamamen kapatır; ASLR'ı korur (Kırma riski: Çok eski glibc 2.13 öncesi ikililer hariç yok).
- `debugfs=off`: DebugFS dosya sistemini kapatır; çekirdek iç yapılarına yetkisiz erişimi engeller (Kırma riski: Geliştirici hata ayıklama araçları hariç yok).
- `lockdown=integrity`: Canlı çekirdeğin kullanıcı alanından değiştirilmesini önler. (Not: `confidentiality` yerine `integrity` seçildi; live-boot overlayfs/squashfs mount işlemlerini ve KMS GPU sürücülerini kırmaz).
- `intel_iommu=on amd_iommu=on iommu=force`: DMA saldırılarına karşı donanım tabanlı I/O bellek yönetimini zorunlu kılar (Kırma riski: Eski anakartlar hariç yok, USB kart okuyucu güvenliğini artırır).

---

## 3. /etc/modprobe.d/90-armonia-kara-liste.conf

> [!IMPORTANT]
> **Kritik İstisnalar:**
> - `squashfs`, `overlay`, `iso9660` ve `loop` modülleri `live-boot` çalışması için **kesinlikle engellenmemiştir**.
> - `usb-storage` ve `uas` modülleri talep gereği akıllı kart ve USB altyapısı için **kara listeye alınmamıştır**.
> - DRM/KMS sürücüleri (`i915`, `amdgpu`, `nouveau`, `virtio-gpu`) ve HID modülleri korunmuştur.

```ini
# /etc/modprobe.d/90-armonia-kara-liste.conf
# Armonia Kiosk - Kullanılmayan Protokol ve Sürücü Kara Listesi

# --- Kullanılmayan Eski / Güvensiz Dosya Sistemleri ---
# Neden: Bozuk dosya sistemi imajları üzerinden yerel ayrıcalık yükseltme açıklarını engeller.
# Kırma Riski: squashfs, overlayfs ve iso9660 HARİÇ tutulduğu için live-boot sistemine zararı yoktur.
install cramfs /bin/true
install freevxfs /bin/true
install jffs2 /bin/true
install hfs /bin/true
install hfsplus /bin/true
install udf /bin/true
install gfs2 /bin/true
install reiserfs /bin/true
install nilfs2 /bin/true
install befs /bin/true
install qnx4 /bin/true
install adfs /bin/true
install affs /bin/true
install bfs /bin/true
install sysv /bin/true

# --- Kullanılmayan Eski / Egzotik Ağ Protokolleri ---
# Neden: Çekirdeğin kullanılmayan ağ yığınlarındaki zafiyetlerin (ör. DCCP/SCTP privilege escalation) istismar edilmesini önler.
# Kırma Riski: Kiosk yalnızca TCP/UDP/ICMP kullanır; WebRTC veya yerel vekil etkilenmez.
install dccp /bin/true
install sctp /bin/true
install rds /bin/true
install tipc /bin/true
install atm /bin/true
install ax25 /bin/true
install netrom /bin/true
install x25 /bin/true
install rose /bin/true
install decnet /bin/true
install econet /bin/true
install af_802154 /bin/true
install ipx /bin/true
install appletalk /bin/true
install psnap /bin/true
install p8023 /bin/true
install p8022 /bin/true
install can /bin/true

# --- Doğrudan Bellek Erişimi (DMA) Riski Taşıyan Eski Veri Yolları ---
# Neden: Fiziksel portlar üzerinden donanımsal DMA saldırılarını (donanım takarak RAM okuma) engeller.
# Kırma Riski: Kiosk bu veri yollarını kullanmaz. USB veri yolu açık bırakılmıştır (Akis kart okuyucu için).
install firewire-core /bin/true
install firewire_ohci /bin/true
install thunderbolt /bin/true

# --- Kullanılmayan Eski Giriş/Çıkış Aygıtları ---
# Neden: Donanımsal saldırı yüzeyini ve gereksiz sürücü yüklemelerini azaltır.
# Kırma Riski: Yok.
install floppy /bin/true
install parport /bin/true
install parport_pc /bin/true

# --- Bluetooth (Kiosk Güvenliği) ---
# Neden: Uzaktan kimlik doğrulamasız saldırı yüzeyini ve kablosuz dinlemeyi engeller.
# Kırma Riski: Bluetooth klavye/fare kullanılmıyorsa risk yoktur (kiosk kablolu kabul edilmiştir).
install bluetooth /bin/true
install btusb /bin/true
```

---

## 4. /etc/nftables.conf

Gereksinimler doğrultusunda:
- Varsayılan Giriş: `DROP`
- `lo` arayüzü: Tam serbest
- Durum takibi: `established, related` kabul
- DHCP İstemcisi: Gerekli UDP portları açık
- ICMPv6: RFC 4890 standart gerekli tipler açık
- 8800 Portu: Dışarıya kapalı, LAN paylaşım satırı yorumda
- Varsayılan Çıkış: `ACCEPT` (HTTPS, WebRTC UDP, DNS serbest)

```nftables
#!/usr/sbin/nft -f
# /etc/nftables.conf
# Armonia Kiosk - Güvenlik Duvarı Yapılandırması

flush ruleset

table inet filter {
    chain input {
        type filter hook input priority filter; policy drop;

        # 1. Loopback trafiği (127.0.0.1:8800 vekili ve yerel IPC için tam serbest)
        iif "lo" accept

        # 2. Giden isteklerin devamı olan bağlantılar (Stateful firewall)
        ct state established,related accept
        ct state invalid drop

        # 3. DHCP İstemcisi (IPv4 için bootpc/bootps)
        ip protocol udp udp sport 67 udp dport 68 accept comment "DHCPv4 istemci trafigi"

        # 4. ICMPv4 Temel Ağ İletişimi (Ping ve MTU Keşfi)
        icmp type { destination-unreachable, time-exceeded, echo-request } accept comment "Gerekli ICMPv4"

        # 5. ICMPv6 RFC 4890 Uyumluluğu (IPv6 Ağ Bağlantısı, Komşu Keşfi / SLAAC için zorunlu)
        icmpv6 type {
            destination-unreachable,
            packet-too-big,
            time-exceeded,
            parameter-problem,
            echo-request,
            nd-router-solicit,
            nd-router-advert,
            nd-neighbor-solicit,
            nd-neighbor-advert,
            mld-listener-query,
            mld-listener-report,
            mld-listener-reduction
        } accept comment "RFC 4890 ICMPv6 ve NDP trafigi"

        # 6. Yerel Ağ Paylaşımı (8800 Portu)
        # Varsayılan olarak kapalıdır. İleride LAN paylaşımı gerektiğinde alt ağa göre aşağıdaki satırı açınız:
        # ip saddr 192.168.1.0/24 tcp dport 8800 accept comment "Armonia LAN Paylasim Portu"
    }

    chain forward {
        type filter hook forward priority filter; policy drop;
    }

    chain output {
        type filter hook output priority filter; policy accept;
        # Cikis serbest: HTTPS (443), DNS (53), WebRTC (RTP/RTCP dinamik UDP portlari) serbestce cikar.
    }
}
```

---

## 5. armonia-kiosk.service Sandbox Drop-in

Dosya konumu: `/etc/systemd/system/armonia-kiosk.service.d/10-sandbox.conf`  
Bu servis `User=kiosk`, `PAMName=login` ve TTY üzerinde doğrudan `labwc` Wayland kompozitörünü çalıştıran oturumdur.

> [!CAUTION]
> **Wayland/DRM ve Akıllı Kartı Bozabilecek Kritik Parametre İşaretlemeleri:**
> - `MemoryDenyWriteExecute=no` tutulmalıdır. Aksi halde Mesa / LLVMpipe shader derleyicisi çöker.
> - `PrivateDevices=no` veya dikkatle filtrelenmiş `DeviceAllow` kullanılmalıdır. Aksi halde `/dev/dri/card*` ve `/dev/bus/usb/*` kaybolur.
> - `PrivateUsers=no` tutulmalıdır. TTY login ve logind seat tahsisi için UID eşleşmesi gereklidir.
> - `ProtectHome=read-only` veya `tmpfs` dikkatli seçilmelidir; `labwc` ayarları `/home/kiosk/.config/labwc` altındaysa `ProtectHome=yes` oturumu açamaz.

```ini
# /etc/systemd/system/armonia-kiosk.service.d/10-sandbox.conf
# Armonia Kiosk - Wayland / labwc Uyumlu systemd İzolasyon Drop-in

[Service]
# --- Kullanıcı ve Temel Güvenlik ---
# Neden: Sürecin veya alt süreçlerinin setuid/setgid ile yetki yükseltmesini engeller.
# Kırma Riski: Yok (kiosk kullanıcısı ayrıcalıksızdır).
NoNewPrivileges=yes

# --- Dosya Sistemi İzolasyonu ---
# Neden: Kök dosya sistemini (/usr, /boot, /etc) salt okunur bağlar.
# Kırma Riski: Uygulamanın yazması gereken dizin açıkça belirtilmelidir.
ProtectSystem=strict

# Neden: /var/lib/armonia uygulamaya özel kalıcı veri yazma yoludur.
ReadWritePaths=/var/lib/armonia

# Neden: Wayland kompozitörünün çalışma soketi (/run/user/<uid>) ve logind haberleşmesi için gereklidir.
ReadWritePaths=/run/user

# Neden: Servise izole geçici dosya alanı verir; diğer süreçlerin geçici dosyalarına erişimi engeller.
# Kırma Riski: Wayland soketi /run/user altında olduğu sürece X11/Wayland'ı bozmaz.
PrivateTmp=yes

# Neden: /home dizinini kiosk oturumu için salt okunur yapar (kullanıcı konfigürasyonunu korur).
# Kırma Riski Uyarısı: ProtectHome=yes yapılırsa /home tamamen gizlenir ve labwc konfigürasyonunu okuyamaz!
ProtectHome=read-only

# --- Çekirdek ve Sistem Ayarları Koruma ---
# Neden: Servisin sysctl veya /proc/sys değerlerini değiştirmesini engeller.
# Kırma Riski: Yok.
ProtectKernelTunables=yes

# Neden: Servisin çekirdek modülü yüklemesini/kaldırmasını engeller.
# Kırma Riski: Modüller sistem açılışında yüklenir; kiosk içinde modül yüklenmesi gerekmez.
ProtectKernelModules=yes

# Neden: Servisin cgroup parametrelerini değiştirmesini engeller.
# Kırma Riski: Yok.
ProtectControlGroups=yes

# Neden: /proc/sysrq-trigger gibi acil durum çekirdek kancalarını maskeler.
# Kırma Riski: Yok.
ProtectKernelLogs=yes

# --- Mimari ve Ayrıcalık Kısıtlamaları ---
# Neden: Yalnızca x86_64 yerel sistem çağrılarına izin verir, 32-bit x86 emülasyon açıklarını kapatır.
# Kırma Riski: Uygulama ve kütüphaneler 64-bit olduğu sürece sıfır risk.
SystemCallArchitectures=native

# Neden: Sürecin yürütme alanını (personality) değiştirmesini engeller.
# Kırma Riski: Yok.
LockPersonality=yes

# Neden: Kiosk sürecinin gereksiz Linux kabiliyetlerini (capabilities) sıfırlar.
# Kırma Riski: Wayland/DRM ve logind oturumu normal kullanıcı yetkisiyle çalıştığından sorunsuzdur.
CapabilityBoundingSet=

# Neden: Kullanıcı ve ağ namespace'lerinin süreç tarafından oluşturulmasını engeller.
# Kırma Riski: Kiosk uygulaması veya labwc kendi sandbox namespace'ini açmıyorsa güvenlidir.
# (Not: Eğer ileride Chromium tabanlı webview eklenirse 'user' namespace gerekecektir).
RestrictNamespaces=cgroup ipc net pid uts

# --- Donanım ve Bellek İpuçları (KIRILMA RİSKİ OLAN ALANLAR) ---

# DİKKAT: PrivateDevices=no OLMAK ZORUNDADIR.
# Kırma Riski Açıklaması: PrivateDevices=yes yapılırsa /dev/dri (GPU) ve /dev/bus/usb (Akıllı Kart) yok olur!
PrivateDevices=no

# DİKKAT: MemoryDenyWriteExecute=no OLMAK ZORUNDADIR.
# Kırma Riski Açıklaması: MemoryDenyWriteExecute=yes yapılırsa Mesa GPU shader derleyicisi ve WebRTC JIT çöker!
MemoryDenyWriteExecute=no

# DİKKAT: RestrictAddressFamilies AF_UNIX, AF_INET, AF_INET6 ve AF_NETLINK içermelidir.
# Kırma Riski: AF_NETLINK eksik olursa systemd-resolved ve udev ağ sorguları bozulur.
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK

# --- Temel Güvenli Sistem Çağrı Filtresi ---
SystemCallFilter=@system-service
SystemCallFilter=~@privileged @resources @obsolete
```

---

## 6. Doğrulama ve Test Listesi (Debian 13 Komutları)

Kiosk imajı başlatıldıktan sonra her bir katmanın çalıştığını ve kısıtların devrede olduğunu doğrulamak için aşağıdaki test komutları kullanılır:

### 6.1. Sysctl ve Çekirdek Parametreleri Testi
```bash
# 1. 90-armonia.conf parametrelerinin devrede olduğunu denetle
sudo sysctl fs.protected_symlinks fs.protected_hardlinks kernel.kptr_restrict kernel.dmesg_restrict kernel.yama.ptrace_scope net.ipv4.tcp_syncookies

# 2. Canlı çekirdek komut satırını doğrula
cat /proc/cmdline | grep -E "init_on_alloc=1|slab_nomerge|page_alloc.shuffle=1|lockdown=integrity"

# 3. Dmesg kısıtını normal kullanıcı ile test et (Erişim engellenmeli: Operation not permitted)
su - kiosk -s /bin/bash -c "dmesg"
```

### 6.2. Modprobe Kara Liste Testi
```bash
# 1. Kara listeye alınan bir protokolü yüklemeyi dene (Modül bulunamadı veya /bin/true dönmeli)
sudo modprobe dccp
lsmod | grep dccp

# 2. Firewire / Thunderbolt durumunu denetle
sudo modprobe firewire-core
lsmod | grep firewire

# 3. Canlı sistem ve USB modüllerinin başarıyla yüklendiğini teyit et
lsmod | grep -E "squashfs|overlay|usbcore|usb_storage"
```

### 6.3. Güvenlik Duvarı (nftables) Testi
```bash
# 1. nftables kurallarının yüklendiğini kontrol et
sudo nft list ruleset

# 2. Loopback 8800 portu dinlemesini ve yerel erişimi test et
ss -tlpn | grep 8800
curl -I http://127.0.0.1:8800/

# 3. Dış ağdan 8800 portuna erişilmediğini (DROP) başka bir makineden doğrula:
# nmap -p 8800 <kiosk-ip>  (Sonuç: filtered olmalı)

# 4. WebRTC UDP ve DNS/HTTPS giden bağlantılarını doğrula
ss -ulpn
getent hosts debian.org
```

### 6.4. USB Akıllı Kart ve PC/SC Altyapısı Testi
```bash
# 1. USB kart okuyucunun sistem tarafından görüldüğünü doğrula
lsusb

# 2. Akis akıllı kart okuyucu sürücüsü ve kart algılamasını doğrula
opensc-tool -l
pkcs11-tool --module /usr/lib/libakisp11.so --list-slots
# veya pcscd devrede ise:
pcsc_scan -n
```

### 6.5. Wayland / labwc ve DRM/GPU Testi
```bash
# 1. labwc kompozitörünün ve kiosk uygulamasının çalıştığını doğrula
pgrep -l labwc
pgrep -l uyap_app

# 2. DRM aygıt düğümlerini ve kullanıcı erişim izinlerini kontrol et
ls -la /dev/dri/

# 3. Wayland ortam değişkenlerini kiosk kullanıcısı oturumunda doğrula
su - kiosk -c "env | grep -E 'WAYLAND_DISPLAY|XDG_RUNTIME_DIR'"
```

### 6.6. systemd Sandbox Güvenlik Analizi
```bash
# 1. Servisin sandbox puanlamasını ve açıklarını raporla
systemd-analyze security armonia-kiosk.service

# 2. Servis izolasyon bayraklarının canlı süreçte uygulandığını kontrol et
systemctl show armonia-kiosk.service | grep -E "NoNewPrivileges|ProtectSystem|ProtectHome|PrivateDevices|MemoryDenyWriteExecute"
```

---

## Doğrulama Planı

### Otomatize Edilebilir Komutlar
- live-build chroot aşamasında yapılandırma dosyalarının sözdizimi denetimi:
  - `nft -c -f /etc/nftables.conf` (Sözdizimi doğrulaması)
  - `systemd-analyze verify /etc/systemd/system/armonia-kiosk.service`
  - `sysctl -p /etc/sysctl.d/90-armonia.conf --system` (Hatalı anahtar testi)

### Manuel Doğrulama
- Kiosk canlı USB üzerinden boot edilerek açılış süresinde anormallik olmadığı doğrulanır.
- Akis akıllı kart takılarak PKCS#11 imzalamasının `uyap_app` içinden çalıştığı doğrulanır.
- WebRTC video/ses görüşmesi başlatılarak UDP paket geçişleri teyit edilir.
