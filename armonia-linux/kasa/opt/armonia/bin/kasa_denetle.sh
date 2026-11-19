#!/bin/sh
# Kiosk baslamadan once: diskte kasa bolumu var ama baglanmamissa durumu isaretler (veri RAM'e gitmesin diye
# uygulama bunu gosterecek). Kiosk'u durdurmaz: kara ekran yerine uyari tercih edildi. Claude, 2026-11-14 (O1).
install -d -m 0755 /run/armonia
if [ -e /dev/disk/by-partlabel/armonia-kasa ]; then
  if mountpoint -q /var/lib/armonia/CokluUyap; then
    [ -s /run/armonia/kasa-durum ] || echo bagli > /run/armonia/kasa-durum
  else
    grep -q '^HATA' /run/armonia/kasa-durum 2>/dev/null || echo "HATA: kasa var ama acilmadi/baglanmadi - veriler KALICI DEGIL" > /run/armonia/kasa-durum
  fi
else
  echo "yok: kalici disk bulunamadi - veriler KALICI DEGIL" > /run/armonia/kasa-durum
fi
# guvenlik-denetci K1 (imaj tarafi): Sunucu rolunde PostgreSQL hazir degilken kiosk ACILMAZ; aksi halde uygulama
# DATABASE_URL olmadan kasadaki SQLite/dosya deposuna sessizce yazar ve veri ikiye bolunur. Kiosk Restart=always ile
# PG gelene kadar yeniden dener (PG Restart=on-failure).
if [ -e /srv/kasa/armonia/rol-sunucu ] && [ ! -s /run/armonia/cokluuyap.env ]; then
  echo "HATA: Sunucu rolu ama veritabani hazir degil; kiosk bekletiliyor" > /run/armonia/pg-durum
  echo "Sunucu rolunde PostgreSQL hazir degil (/run/armonia/cokluuyap.env yok); kiosk acilmiyor" >&2
  exit 1
fi
rm -f /run/armonia/pg-durum
exit 0
