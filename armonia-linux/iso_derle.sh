#!/bin/bash
# Armonia ISO'sunu live-build ile derler. Once derle_uyap.sh calismis olmali.
# Root ile:  wsl -d Debian -u root -- bash /mnt/c/.../armonia-linux/iso_derle.sh   (ya da: sudo bash .../iso_derle.sh)
#   TEST=1 ... iso_derle.sh  -> test katmani da eklenir (kasa bicimlendirme, lynis), cikti armonia_test_kasa.iso
# Cikti: ISO_DIZIN/armonia.iso  (varsayilan: /mnt/c/dev varsa orasi = C:\dev, yoksa /root/armonia-cikti)
# Yollar betigin kendi yerinden bulunur; ortam degiskeniyle degistirilebilir:
#   KAYNAK=<armonia-linux dizini>  UYGULAMA=<derle_uyap.sh ciktisi .../cokluuyap>  ISO_DIZIN=<ISO hedef dizini>  IS=<lb calisma dizini>
# Imaj amd64'tur: x86_64 olmayan makinede (or. Raspberry Pi) durur; ZORLA=1 ile gecilir.
# Claude, 2026-11-13 (14'te sertlestirme + kasa + Plymouth; 19.11 yollar tasinabilir yapildi).
set -euo pipefail
KAYNAK="${KAYNAK:-$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)}"
# derle_uyap.sh normal kullanicida calisir: sudo ile cagrildiysa onun evi, yoksa WSL'deki 'utku'
DERLEYEN_EVI="$(getent passwd "${SUDO_USER:-utku}" | cut -d: -f6 || true)"
UYGULAMA="${UYGULAMA:-${DERLEYEN_EVI:-/home/utku}/armonia-cikti/cokluuyap}"
IS="${IS:-/root/armonia-iso}"
if [ -z "${ISO_DIZIN:-}" ]; then
  if [ -d /mnt/c/dev ]; then ISO_DIZIN=/mnt/c/dev; else ISO_DIZIN=/root/armonia-cikti; fi
fi
mkdir -p "$ISO_DIZIN"
TEST="${TEST:-0}"
[ "$(id -u)" = "0" ] || { echo "HATA: root ile calistirin (live-build root ister)"; exit 1; }
if [ "$(uname -m)" != "x86_64" ] && [ "${ZORLA:-0}" != "1" ]; then
  echo "HATA: bu makine $(uname -m); Armonia imaji amd64 (e-imza paketi ve uyap_app amd64). ZORLA=1 ile gecilir."; exit 1
fi
[ -d "$KAYNAK/config" ] || { echo "HATA: armonia-linux kaynagi yok: $KAYNAK"; exit 1; }
[ -x "$UYGULAMA/uyap_app" ] || { echo "HATA: once derle_uyap.sh (aranan: $UYGULAMA/uyap_app)"; exit 1; }
ls "$KAYNAK"/config/packages.chroot/adalet-eimza-tray_*_amd64.deb >/dev/null 2>&1 \
  || echo "UYARI: e-imza paketi yok (depoda tutulmuyor): config/packages.chroot/ altina indirin, bkz. docs/GUNCEL_DURUM_2026-11-19.md"
mkdir -p "$IS"; cd "$IS"
# Paket onbellegi Faz 0 test derlemesinden devralinir (indirme suresi kisalir)
[ -d cache ] || { [ -d /root/iso-test/cache ] && cp -a /root/iso-test/cache . || true; }
lb clean >/dev/null 2>&1 || true
rm -rf config
EK_PARAM="$(tr -d '\r\n' < "$KAYNAK/sertlestirme/cekirdek_parametreleri.txt")"
lb config --distribution trixie --archive-areas main --debian-installer none \
  --binary-images iso-hybrid --bootloaders syslinux --apt-recommends false --memtest none \
  --iso-volume ARMONIA --chroot-squashfs-compression-type zstd \
  --bootappend-live "boot=live quiet splash loglevel=3 toram $EK_PARAM$( [ "$TEST" = "1" ] && echo ' armonia.test_kasa=1')$( [ "${OLCUM_TARAYICI:-0}" = "1" ] && echo ' armonia.olcum_tarayici=1')$( { [ "$TEST" = "1" ] || [ "${OLCUM_TARAYICI:-0}" = "1" ] || [ "${OLCUM:-0}" = "1" ]; } && echo ' armonia.olcum=1')" >/dev/null
cp -a "$KAYNAK/config/." config/
# Faz 3 sertlestirme katmani (guvenlik-denetci denetiminden gecti, 2026-11-14)
cp -a "$KAYNAK/sertlestirme/etc/." config/includes.chroot/etc/
# Faz 2 kasa katmani (LUKS2, PARTLABEL=armonia-kasa) + Plymouth temasi
cp -a "$KAYNAK/kasa/." config/includes.chroot/
mkdir -p config/includes.chroot/usr/share/plymouth/themes/armonia
cp "$KAYNAK"/plymouth/armonia/* config/includes.chroot/usr/share/plymouth/themes/armonia/
if [ "$TEST" = "1" ]; then
  echo "TEST katmani ekleniyor (YALNIZ test ISO'su)"
  cp -a "$KAYNAK/test/config-test/includes.chroot/." config/includes.chroot/
  cp "$KAYNAK"/test/config-test/hooks/normal/* config/hooks/normal/
  cp "$KAYNAK/test/config-test/test.list.chroot" config/package-lists/
fi
# SERI=1 -> uretim imaji + yalniz gunlugun COM1'e akmasi (uygulama hatasi teshisi; armonia_seri.iso). 2026-11-14.
if [ "${SERI:-0}" = "1" ]; then
  mkdir -p config/includes.chroot/etc/systemd/journald.conf.d
  cp "$KAYNAK/test/config-test/includes.chroot/etc/systemd/journald.conf.d/90-test-seri.conf" \
     config/includes.chroot/etc/systemd/journald.conf.d/
fi
# Tarayici (chromium) artik uretim katmaninda: config/ (armonia-tarayici.socket/@.service, xdg-open, politika).
# OLCUM_TARAYICI=1 -> olcum.sh acilistan sonra tarayiciyi bir kez acip kum havuzunu raporlar (armonia_tarayici_olcum.iso)
find config -type f \( -name '*.sh' -o -name '*.hook.*' -o -name '*.service' -o -name '*.conf' -o -name '*.cfg' \
  -o -name '*.in' -o -name '*.list.*' -o -name '*.script' -o -name '*.plymouth' -o -name 'crypttab' \
  -o -name '*.socket' -o -name '*.json' -o -name 'xdg-open' -o -name '*.xml' -o -name '*.rules' -o -name '*.nft' -o -name '*.slice' \) \
  -exec sed -i 's/\r$//' {} +
mkdir -p config/includes.chroot/opt/armonia
cp -a "$UYGULAMA" config/includes.chroot/opt/armonia/cokluuyap
# /mnt/c'den gelen dosyalar 0777 olur: imaja herkese-yazilabilir dosya girmesin
# (u=rwX 0777 dosyada x'i korur → systemd "marked executable" uyarisi; once hepsi 0644, sonra yalniz betikler 0755)
find config/includes.chroot config/hooks config/bootloaders -type d -exec chmod 0755 {} +
find config/includes.chroot config/hooks config/bootloaders -type f -exec chmod 0644 {} +
chmod 0755 config/hooks/normal/*.hook.chroot config/includes.chroot/opt/armonia/bin/*.sh \
  config/includes.chroot/opt/armonia/cokluuyap/uyap_app
[ -f config/includes.chroot/usr/local/bin/xdg-open ] && chmod 0755 config/includes.chroot/usr/local/bin/xdg-open
BAS=$(date +%s)
set +e; lb build >"$IS/build.log" 2>&1; RC=$?; set -e
echo "lb build cikis: $RC  sure: $(( $(date +%s) - BAS )) sn"
grep -nE "^E:|^P: Begin|error|UYARI" "$IS/build.log" | grep -viE "liberror|error-perl|errors=" | tail -15
[ $RC -eq 0 ] || exit $RC
# Uretim ISO'sunda test bileseni olmamali (guvenlik-denetci O4)
if [ "$TEST" != "1" ] && unsquashfs -l binary/live/filesystem.squashfs 2>/dev/null \
     | grep -qE 'kasa_test_kur|armonia-kasa-test|lynis_test|/usr/sbin/lynis|90-test-seri|kasa_test_dogrula|tus_kaydi|/usr/bin/evtest'; then
  echo "HATA: uretim ISO'sunda test bileseni bulundu"; exit 3
fi
# DNS: live-build resolv.conf'u bos birakir; acilista tmpfiles ile resolved stub'ina baglanmali.
# Icerik denetlenir (guvenlik-denetci D2). Boru + grep -q KULLANMA: pipefail altinda unsquashfs SIGPIPE alir
# ve dosya varken de hata verir (2026-11-14 ilk deneme).
RESOLV_SATIR="$(unsquashfs -cat binary/live/filesystem.squashfs etc/tmpfiles.d/armonia-resolv.conf 2>/dev/null || true)"
grep -q '^L+ /etc/resolv.conf .*stub-resolv.conf' <<<"$RESOLV_SATIR" \
  || { echo "HATA: armonia-resolv.conf (DNS) imajda yok veya bozuk"; exit 4; }
if [ "$TEST" = "1" ]; then HEDEF="$ISO_DIZIN/armonia_test_kasa.iso"; else HEDEF="$ISO_DIZIN/armonia.iso"; fi
[ "${OLCUM:-0}" = "1" ] && HEDEF="$ISO_DIZIN/armonia_olcum.iso"
[ "${OLCUM_TARAYICI:-0}" = "1" ] && HEDEF="$ISO_DIZIN/armonia_tarayici_olcum.iso"
[ "${SERI:-0}" = "1" ] && HEDEF="$ISO_DIZIN/armonia_seri.iso"
# WSL bellegi ~2,9 GB: /mnt/c'ye (9p) cp sayfa onbellegini sisirip "Cannot allocate memory" verdi (14 Kasim 12:08)
sync; echo 3 > /proc/sys/vm/drop_caches || true
rm -f "$HEDEF"
dd if=live-image-amd64.hybrid.iso of="$HEDEF" bs=4M oflag=direct status=none
ls -la "$HEDEF"; sha256sum "$HEDEF"
echo "--- squashfs:"; ls -la binary/live/filesystem.squashfs
