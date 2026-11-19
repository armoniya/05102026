#!/bin/sh
# root: sunucu hazir olunca kiosk rolunu + cokluuyap veritabanini (yoksa) olusturur, CokluUYAP icin
# DATABASE_URL ortam dosyasini yazar. Rol yalniz LOGIN, ust yetki yok; peer ile yalniz OS kullanicisi kiosk baglanir.
# Claude, 2026-11-14.
set -eu
. /opt/armonia/bin/pg_ortak.sh
i=0
until "$PG_BIN/pg_isready" -q -h "$PG_SOKET"; do
  i=$((i + 1)); [ "$i" -lt 60 ] || { echo "PostgreSQL hazir olmadi" >&2; exit 1; }; sleep 0.5
done
sql() { runuser -u postgres -- "$PG_BIN/psql" -h "$PG_SOKET" -d postgres -v ON_ERROR_STOP=1 -tAq -c "$1"; }
[ "$(sql "SELECT 1 FROM pg_roles WHERE rolname='kiosk'")" = 1 ] || \
  sql "CREATE ROLE kiosk LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
[ "$(sql "SELECT 1 FROM pg_database WHERE datname='cokluuyap'")" = 1 ] || \
  sql "CREATE DATABASE cokluuyap OWNER kiosk ENCODING 'UTF8' TEMPLATE template0"
# Veritabanina BAGLANMA yetkisi: cokluuyap'a yalniz sahibi (kiosk) baglansin (sema yetkileri PG15+ varsayilaniyla dogru)
sql "REVOKE CONNECT ON DATABASE cokluuyap FROM PUBLIC"
sql "ALTER ROLE kiosk CONNECTION LIMIT 20"
# kiosk yonetim veritabanlarina baglanamasin (postgres sistem kullanicisi superuser oldugu icin etkilenmez)
sql "REVOKE CONNECT ON DATABASE postgres FROM PUBLIC"
sql "REVOKE CONNECT ON DATABASE template1 FROM PUBLIC"
install -d -m 0755 /run/armonia
umask 022
echo "DATABASE_URL=\"host=$PG_SOKET user=kiosk dbname=cokluuyap\"" > /run/armonia/cokluuyap.env
