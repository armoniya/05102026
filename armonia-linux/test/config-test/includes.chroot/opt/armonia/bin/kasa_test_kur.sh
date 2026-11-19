#!/bin/bash
# YALNIZ TEST ISO'su: bolumsuz ilk diski sabit test parolasiyla LUKS2 kasa yapar, sonra yeniden baslatir.
# Gercek kurulum sihirbazi Faz 2'de ayri yazilacak. Claude, 2026-11-14.
set -euo pipefail
exec >/dev/ttyS0 2>&1
echo "=====KASA-TEST-KUR basladi====="
# Bolum icermeyen, CD olmayan, 1 GB'tan buyuk ilk disk
D=""
for d in $(lsblk -dnpo NAME,TYPE | awk '$2=="disk"{print $1}'); do
  [ "$(lsblk -nro NAME "$d" | wc -l)" -eq 1 ] || continue
  [ -z "$(blkid -p "$d" 2>/dev/null)" ] || continue   # imzasi (fs/LUKS/tablo) olan diske dokunma
  [ "$(blockdev --getsize64 "$d")" -gt 1000000000 ] || continue
  D="$d"; break
done
[ -n "$D" ] || { echo "KASA-TEST-KUR: uygun bos disk yok"; exit 0; }
echo "disk: $D"
sgdisk --zap-all "$D" >/dev/null
sgdisk -n1:0:0 -t1:8309 -c1:armonia-kasa "$D"
partprobe "$D"; udevadm settle
P=/dev/disk/by-partlabel/armonia-kasa
printf '%s' 'Kasa-test2026' | /opt/armonia/bin/kasa_parola_kurali.sh || { echo "KASA-TEST-KUR: test parolasi kurala uymuyor"; exit 1; }
B=$(date +%s%N)
printf '%s' 'Kasa-test2026' | cryptsetup luksFormat --batch-mode --type luks2 \
  --cipher aes-xts-plain64 --key-size 512 --pbkdf argon2id --pbkdf-memory 262144 --label ARMONIA_KASA --key-file - "$P"
echo "luksFormat sure: $(( ($(date +%s%N) - B) / 1000000 )) ms"
printf '%s' 'Kasa-test2026' | cryptsetup open --key-file - "$P" kasa
mkfs.ext4 -q -L kasa /dev/mapper/kasa
# Rol isareti (gercekte kurulum sihirbazi yazar): bu test makinesi Sunucu -> PostgreSQL kasada calisir
M=$(mktemp -d); mount /dev/mapper/kasa "$M"; install -d -m 0755 "$M/armonia"; echo sunucu > "$M/armonia/rol-sunucu"
umount "$M"; rmdir "$M"
udevadm settle; sleep 1; cryptsetup close kasa || { sleep 2; cryptsetup close kasa; }
cryptsetup luksDump "$P" | grep -E "Version|Cipher|Keyslot|PBKDF|Memory|Time cost|Threads" | head -12
echo "=====KASA-TEST-KUR bitti, yeniden baslatiliyor====="
sync; systemctl reboot
