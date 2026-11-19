#!/bin/sh
# postgres kullanicisi: sunucuyu yalniz Unix soketiyle baslatir (listen_addresses bos = TCP yok). Claude, 2026-11-14.
set -eu
. /opt/armonia/bin/pg_ortak.sh
exec "$PG_BIN/postgres" -D "$PG_VERI" \
  -c listen_addresses='' \
  -c unix_socket_directories="$PG_SOKET" \
  -c unix_socket_permissions=0770 \
  -c huge_pages=off \
  -c shared_buffers=64MB \
  -c max_connections=30 \
  -c logging_collector=off \
  -c log_destination=stderr \
  -c password_encryption=scram-sha-256 \
  -c jit=off
