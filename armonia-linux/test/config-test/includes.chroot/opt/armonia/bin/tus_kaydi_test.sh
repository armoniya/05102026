#!/bin/bash
# YALNIZ TEST: tum klavye event aygitlarindaki tus basmalarini 120 sn boyunca COM1'e yazar. Claude, 2026-11-14.
exec >/dev/ttyS0 2>&1
sleep 2
for d in /dev/input/event*; do
  ad=$(cat /sys/class/input/$(basename "$d")/device/name 2>/dev/null)
  echo "TUS-KAYDI aygit $d: $ad"
  ( timeout 120 evtest "$d" 2>/dev/null | grep --line-buffered -E "EV_KEY.*value 1" | sed -u "s#^#TUS-KAYDI $(basename "$d") #" ) &
done
wait
