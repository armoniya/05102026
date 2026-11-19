#!/bin/bash
# YALNIZ TEST: anahtar yuvasi test parolasiyla aciliyor mu (kullanici girisi olmadan)? Sonuc COM1'e. Claude, 2026-11-14.
exec >/dev/ttyS0 2>&1
P=/dev/disk/by-partlabel/armonia-kasa
echo "=====KASA-DOGRULA basladi====="
free -m | head -2
B=$(date +%s%N)
printf '%s' 'Kasa-test2026' | cryptsetup open --test-passphrase -v --key-file - "$P"; echo "test-passphrase rc=$? sure=$(( ($(date +%s%N) - B) / 1000000 )) ms"
echo "kbd_mode tty1: $(kbd_mode -C /dev/tty1 2>&1)"
cryptsetup luksDump "$P" | grep -E "Memory|Time cost|Threads|PBKDF" | head -4
echo "=====KASA-DOGRULA bitti====="
