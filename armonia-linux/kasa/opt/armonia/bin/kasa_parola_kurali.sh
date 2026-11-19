#!/bin/bash
# Kasa parolasi kurali (kullanici karari, 2026-11-14): en az 10 karakter; Turkce karakter YOK (yalniz ASCII);
# en az bir buyuk harf, bir kucuk harf, bir rakam, bir sembol. Parola argumanla degil stdin'den okunur (ps'te gorunmesin).
# Kullanim: printf '%s' "$parola" | kasa_parola_kurali.sh   -> 0 uygun, 1 uygun degil (nedenler stderr'e)
# Kurulum sihirbazi (Faz 2) ve test bicimlendirmesi kullanir. Claude, 2026-11-14.
export LC_ALL=C
IFS= read -r -d '' p || true
hata=0
[ -n "$p" ] || { echo "Parola bos olamaz." >&2; exit 1; }
# Yalniz yazdirilabilir ASCII (0x20-0x7E): ğ ü ş ı ö ç İ Ğ Ü Ş Ö Ç ve diger ASCII disi karakterler reddedilir
if printf '%s' "$p" | LC_ALL=C grep -q '[^ -~]'; then
  echo "Parolada Türkçe karakter (ğ, ü, ş, ı, ö, ç, İ…) veya ASCII dışı karakter olamaz." >&2; hata=1
fi
[ ${#p} -ge 10 ] || { echo "Parola en az 10 karakter olmalı (şu an ${#p})." >&2; hata=1; }
[[ "$p" =~ [A-Z] ]] || { echo "En az bir büyük harf (A-Z) gerekli." >&2; hata=1; }
[[ "$p" =~ [a-z] ]] || { echo "En az bir küçük harf (a-z) gerekli." >&2; hata=1; }
[[ "$p" =~ [0-9] ]] || { echo "En az bir rakam (0-9) gerekli." >&2; hata=1; }
[[ "$p" =~ [[:punct:]] ]] || { echo "En az bir sembol (ör. - * . , ! ? + %) gerekli." >&2; hata=1; }
exit $hata
