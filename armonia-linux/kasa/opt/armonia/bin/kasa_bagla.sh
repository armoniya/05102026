#!/bin/sh
# Kasayi denetleyip baglar; kiosk'un CokluUyap ayar ve calisma dizinlerini kasaya bind-mount eder.
# Durum /run/armonia/kasa-durum dosyasina yazilir (bagli | HATA:...); uygulama ileride bunu gosterecek.
# Claude, 2026-11-14 (guvenlik-denetci O1/O2/O3 duzeltmeleri).
set -eu
install -d -m 0755 /run/armonia
durum() { echo "$1" > /run/armonia/kasa-durum; }
trap 'durum "HATA: baglama tamamlanamadi"' EXIT
# O2: baglamadan once dosya sistemi denetimi (fstab yok -> systemd-fsck calismiyor). >=4 duzeltilemeyen hata.
rc=0; e2fsck -p /dev/mapper/kasa >/dev/null 2>&1 || rc=$?
[ "$rc" -lt 4 ] || { durum "HATA: dosya sistemi denetimi basarisiz (e2fsck $rc)"; trap - EXIT; exit 1; }
install -d -m 0755 /srv/kasa
mountpoint -q /srv/kasa || mount -o noatime,nodev,nosuid,noexec,errors=remount-ro /dev/mapper/kasa /srv/kasa
install -d -o kiosk -g kiosk -m 0700 /srv/kasa/cokluuyap /srv/kasa/cokluuyap/CokluUyap /srv/kasa/cokluuyap/calisma
install -d -o kiosk -g kiosk -m 0700 /var/lib/armonia/CokluUyap
mountpoint -q /var/lib/armonia/CokluUyap || mount --bind /srv/kasa/cokluuyap/CokluUyap /var/lib/armonia/CokluUyap
mountpoint -q /var/lib/armonia/cokluuyap || mount --bind /srv/kasa/cokluuyap/calisma /var/lib/armonia/cokluuyap
# XDG_CONFIG_HOME (LAN TLS sertifikasi, yerel giris jetonu) ve toplu evrak klasoru kasada
install -d -o kiosk -g kiosk -m 0700 /var/lib/armonia/CokluUyap/config /var/lib/armonia/CokluUyap/Evrak /var/lib/armonia/CokluUyap/chromium
# O3: webapp her acilista salt-okunur imajdaki kopyaya baglanir; kiosk'un yerine koydugu dizin silinir.
rm -rf --one-file-system /var/lib/armonia/cokluuyap/webapp
ln -s /opt/armonia/cokluuyap/webapp /var/lib/armonia/cokluuyap/webapp
# Sunucu rolunde PostgreSQL veri dizini (armonia-postgresql.service ad alani kurulmadan once var olmali)
if [ -e /srv/kasa/armonia/rol-sunucu ] && id postgres >/dev/null 2>&1; then
  install -d -o postgres -g postgres -m 0700 /srv/kasa/postgresql
fi
trap - EXIT
durum "bagli"
