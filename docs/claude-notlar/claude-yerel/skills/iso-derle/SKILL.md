---
name: iso-derle
description: Armonia Linux ISO'sunu derler ve Hyper-V'de otomatik test eder (uyap_app derlemesi → live-build ISO → WSL kapat → Hyper-V açılış ölçümü + ekran görüntüsü). "ISO derle", "imajı test et", "açılış süresini ölç", Faz 1+ imaj değişikliklerinden sonra kullan.
---

# iso-derle — Armonia ISO derle + test

Tek doğru kaynak: `docs/ARMONIA_LINUX_YOL_HARITASI.md`. Dosyalar `armonia-linux/` altında.

## Adımlar (sırayla; bellek 7,9 GB → WSL ile Hyper-V aynı anda çalışmaz)

1. **Rust değiştiyse** (yalnız `ruyap/src` veya Cargo dosyaları değiştiyse; `enjekte/`, `webapp/` için de
   gerekir çünkü imaj dizinini bu betik hazırlar):
   `MSYS_NO_PATHCONV=1 wsl.exe -d Debian -- bash "/mnt/c/Users/WİN10/Desktop/UYAP/cokluuyap/armonia-linux/derle_uyap.sh"`
   (arka planda; tam derleme ~9 dk, artımlı ~1 dk). Çıktı `~/armonia-cikti/cokluuyap/`.
2. **ISO:** `MSYS_NO_PATHCONV=1 wsl.exe -d Debian -u root -- bash "/mnt/c/Users/WİN10/Desktop/UYAP/cokluuyap/armonia-linux/iso_derle.sh"`
   (~6 dk) → `C:\dev\armonia.iso`. **Ölçüm servisi yalnız `armonia.olcum=1` ile çalışır (2026-11-14):** Hyper-V
   ölçümü için `OLCUM=1` ver (→ `C:\dev\armonia_olcum.iso`, `hyperv_test.ps1 -Iso` ile) ya da `TEST=1`/`OLCUM_TARAYICI=1`;
   üretim `armonia.iso` seri porta blok YAZMAZ. Ortam değişkenini WSL'e geçirmek için `WSLENV=OLCUM` (veya `OLCUM_TARAYICI`).
3. **Belleği boşalt:** `wsl.exe --shutdown` (başka bir ajan WSL kullanıyorsa önce bitmesini bekle).
4. **Test:** `powershell -NoProfile -ExecutionPolicy Bypass -File armonia-linux\test\hyperv_test.ps1`
   (~1 dk). Seri porttan `=====ARMONIA-OLCUM-BASI=====` bloğu gelir; `C:\dev\vm_ekran.png`'yi Read ile bak.
5. **Değerlendir ve yol haritasına yaz:** `Startup finished` (hedef ≤ 20 sn Hyper-V), uyap_app başlangıcı,
   `systemctl is-system-running` = running, başarısız birim yok, herkese-yazılabilir dosya 0,
   Xwayland yok, ağ routable, bellek. Ekranda ÇokluUYAP giriş kartı görünmeli.

## Tuzaklar
- `wsl.exe -- bash -c '...'` içinde değişkenler boşalır → komutları `C:\dev\*.sh` (LF) dosyasına yaz.
- `/mnt/c`'den kopyalanan dosyalar 0777 gelir; `iso_derle.sh` chmod ile düzeltir, hook denetler.
- **Bash heredoc `\\` dizisini tek `\`'ye indirir** → `\\.\pipe\x` yolu `\.\pipe\x` olup Start-VM
  `Parametre hatalı (0x80070057)` verdi. Ters eğik çizgili PowerShell/systemd içeriğini Write aracıyla yaz.
- Çalışan bir bash betiğini (ör. derleme sürerken `iso_derle.sh`) düzenleme: bash dosyayı parça parça okur.
- `lb build` yapılandırmayı başta kopyalar; derleme başladıktan sonraki `config/` değişiklikleri o ISO'ya girmez.
- Kasa testi: `TEST=1 ... iso_derle.sh` → `C:\dev\armonia_test_kasa.iso`, sonra `test\hyperv_kasa_test.ps1`
  (Gen1 IDE disk konukta `/dev/sda`).
- **Hyper-V `Msvm_Keyboard.TypeText` Türkçe Windows'ta yanlış tarama kodu gönderir** ("kasatest2026" → KEY_RO, M, B…).
  Konuğa metin yazarken `TypeScancodes` (PC set-1, basma + `kod|0x80` bırakma) kullan; `hyperv_kasa_test.ps1`'de tablo var.
- COM borusu konuk yeniden başlarken kapanır; `com_oku.ps1` yeniden bağlanır (eski sürüm 2. açılışı kaçırıyordu).
- Test ISO'sunda journal COM1'e akar (`90-test-seri.conf`); takılan açılışı oradan teşhis et.
- Elle deney yaptıysan **ArmoniaTest VM'sini kapat** (1,5 GB); açık unutulan VM derlemeyi bellekten öldürttü.
- systemd `ReadWritePaths=` dizini yoksa servis NAMESPACE (226) ile düşer — ad alanı `ExecStartPre=+` için de kurulur.
  Dizini önceden oluştur ya da `-` öneki kullan.
- `SystemCallFilter=~@privileged` `chown` ailesini de engeller (ör. PostgreSQL `unix_socket_group` EPERM).
- Debian `apparmor.service` canlı sistemde kendini atlar (`!/run/live/overlay/work`) → `sertlestirme/.../apparmor.service.d/10-canli.conf`.
- Plymouth script'te "Sans 10" kutucuk (tofu) çizdi, "Sans 11" düzgün.
- Ekran görüntüsü yalnız 640x480'de çalışıyor (800x600+ → 32775); küçük resim birkaç saniye gecikmeli olabilir.
- Python betiklerinde Türkçe yol: `PYTHONUTF8=1`.
- Güvenlik içeren yeni yapılandırma (sysctl, nftables, sandbox, AppArmor) imaja girmeden önce
  `guvenlik-denetci` ajanından geçer.
