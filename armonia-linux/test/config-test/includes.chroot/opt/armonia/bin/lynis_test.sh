#!/bin/sh
# YALNIZ TEST: canli sistemde lynis taramasi; ozet /run/armonia-lynis.txt'e yazilir (olcum.sh seri porta basar).
# Claude, 2026-11-14.
lynis audit system --quick --no-colors --quiet --report-file /run/lynis.dat >/dev/null 2>&1
{
  grep -E '^hardening_index=' /run/lynis.dat
  echo "uyari: $(grep -c '^warning' /run/lynis.dat)"
  grep '^warning' /run/lynis.dat
  echo "oneri: $(grep -c '^suggestion' /run/lynis.dat)"
  grep '^suggestion' /run/lynis.dat | cut -d'|' -f1-2
} > /run/armonia-lynis.txt
