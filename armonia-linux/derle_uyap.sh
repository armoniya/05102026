#!/bin/bash
# Armonia imajı için ÇokluUYAP'ı (uyap_app) Linux'ta derler ve imaj dizinini hazırlar.
# WSL Debian içinde 'utku' kullanıcısıyla çalışır:  bash /mnt/c/.../armonia-linux/derle_uyap.sh
# Çıktı: ~/armonia-cikti/cokluuyap/  (uyap_app + uyap_core/enjekte + webapp + SURUM)
# Claude, 2026-11-13.
set -euo pipefail

REPO="/mnt/c/Users/WİN10/Desktop/UYAP/cokluuyap"
ANA="$REPO/Uyap Haricen Giriş"          # enjekte/ ve webapp/ için tek doğru kaynak
SRC="$ANA/ruyap"
DST="$HOME/ruyap"
OUT="$HOME/armonia-cikti/cokluuyap"
LOG="$HOME/armonia-cikti/derle.log"

. "$HOME/.cargo/env"
mkdir -p "$DST" "$(dirname "$OUT")"

# 1) Kaynağı birebir eşitle (target/ korunur → artımlı derleme). Çalışma önbelleği ve .exe kopyalanmaz.
find "$DST" -mindepth 1 -maxdepth 1 ! -name target -exec rm -rf {} +
tar -C "$SRC" --exclude=./target --exclude=./uyap_core --exclude=./webapp --exclude='./*.exe' \
    -cf - . | tar -C "$DST" -xf -
KAYNAK_OZET=$(cd "$DST" && find . -path ./target -prune -o -type f -print0 | sort -z \
    | xargs -0 sha256sum | sha256sum | cut -c1-16)
GIT_HEAD=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo bilinmiyor)
GIT_KIRLI=$(git -C "$REPO" status --porcelain 2>/dev/null | wc -l)

# 2) Yalnız imaja girecek program: uyap_app (office_agent ve home_client içine gömülü).
#    İnce LTO + sembol temizliği burada; paylaşılan Cargo.toml'a dokunulmaz.
export CARGO_PROFILE_RELEASE_LTO=thin
export CARGO_PROFILE_RELEASE_STRIP=symbols
export CARGO_PROFILE_RELEASE_DEBUG=0
BAS=$(date +%s)
cd "$DST"
set +e
cargo build --release --locked --bin uyap_app -j 2 >"$LOG" 2>&1
RC=$?
set -e
grep -E '^error|Finished' "$LOG" | tail -5
echo "cargo cikis: $RC  sure: $(( $(date +%s) - BAS )) sn"
[ $RC -eq 0 ] || exit $RC

# 3) Bilinen açık taraması (RustSec). Derlemeyi durdurmaz, raporu kaydeder.
if cargo audit --version >/dev/null 2>&1; then
  cargo audit >"$HOME/armonia-cikti/audit.txt" 2>&1 && echo "cargo audit: temiz" \
    || echo "cargo audit: BULGU VAR -> ~/armonia-cikti/audit.txt"
else
  echo "cargo audit kurulu degil (cargo install cargo-audit --locked)"
fi

# 4) İmaj dizini: ikili + güncel arayüz dosyaları + sürüm kaydı
rm -rf "$OUT"; mkdir -p "$OUT/uyap_core"
install -m 0755 target/release/uyap_app "$OUT/uyap_app"
cp -r "$ANA/uyap_core/enjekte" "$OUT/uyap_core/enjekte"
cp -r "$ANA/webapp" "$OUT/webapp"
cat >"$OUT/SURUM" <<EOT
surum=$(grep -m1 '^version' Cargo.toml | cut -d'"' -f2)
git=$GIT_HEAD (degismis dosya: $GIT_KIRLI)
kaynak_ozet=$KAYNAK_OZET
derleme=$(date -u +%Y-%m-%dT%H:%M:%SZ)
rustc=$(rustc --version)
EOT
cat "$OUT/SURUM"
ls -la "$OUT/uyap_app"; du -sh "$OUT"
