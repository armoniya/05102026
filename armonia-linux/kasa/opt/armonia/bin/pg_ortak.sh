# Armonia PostgreSQL ortak degiskenleri (source edilir). Claude, 2026-11-14.
PG_VERI=/srv/kasa/postgresql/veri
PG_SOKET=/run/postgresql
# guvenlik-denetci O1: veri dizini varsa ONUN surumu kullanilir (imaja ikinci ana surum girse bile yanlis ikili
# secilmez); yoksa imajdaki tek surum. Uyusmazlikta PG_BIN bos kalir ve servis acik hata verir (pg_upgrade ayri is).
if [ -s "$PG_VERI/PG_VERSION" ]; then
  PG_BIN="/usr/lib/postgresql/$(cat "$PG_VERI/PG_VERSION")/bin"
  [ -x "$PG_BIN/postgres" ] || PG_BIN=""
else
  PG_BIN=$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)
fi
