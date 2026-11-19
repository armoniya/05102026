#!/bin/sh
# root: kasada PostgreSQL veri dizinini hazirlar; bossa initdb (yalniz yerel soket, peer kimlik dogrulama).
# Claude, 2026-11-14.
set -eu
. /opt/armonia/bin/pg_ortak.sh
[ -n "$PG_BIN" ] || { echo "PostgreSQL ikilisi bulunamadi veya kasadaki veri surumu ($(cat "$PG_VERI/PG_VERSION" 2>/dev/null)) imajdakiyle uyusmuyor" >&2; exit 1; }
mountpoint -q /srv/kasa || { echo "Kasa bagli degil" >&2; exit 1; }
install -d -o postgres -g postgres -m 0700 /srv/kasa/postgresql
if [ ! -s "$PG_VERI/PG_VERSION" ]; then
  install -d -o postgres -g postgres -m 0700 "$PG_VERI"
  # TCP tamamen reddedilir; yerel soket yalniz isletim sistemi kullanici adiyla (peer)
  runuser -u postgres -- "$PG_BIN/initdb" -D "$PG_VERI" -E UTF8 --locale=C.UTF-8 --data-checksums \
    --auth-local=peer --auth-host=reject >/dev/null
  echo "PostgreSQL veri dizini olusturuldu: $PG_VERI"
fi
