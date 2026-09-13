#!/usr/bin/env bash
# pcbridge-native'i RELEASE olarak derleyip Python paketinin buldugu yere koyar.
#
#   scripts/build-native.sh           derle, dogrula, pcbridge/_native/<hedef>/ altina kur
#   scripts/build-native.sh --check   yalnizca derleme gereksinimlerine bak
#
# Neden release: debug derlemede PNG kodlama release'ten ~9 kat yavas
# (olculdu 2026-09-13: monitor basina ~1755 ms'ye karsi ~200 ms).
#
# Calisan servisi YENIDEN BASLATMAZ. Calisan surecler eski binary'yi kullanmaya
# devam eder (Linux'ta calisan dosyanin yerine yenisini koymak guvenli); yeni
# binary bir sonraki baslatmada secilir.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Ilk artifact hedefi bu; baska hedef PLAN.md Faz W/M'nin isi.
TARGET="x86_64-unknown-linux-gnu"
DEST="$DIR/pcbridge/_native/$TARGET"

missing=()
command -v cargo >/dev/null || missing+=("Rust arac zinciri: https://rustup.rs (rust/rust-toolchain.toml 1.95.0'i kendisi secer)")
command -v pkg-config >/dev/null || missing+=("pkg-config")
pkg-config --exists libpipewire-0.3 2>/dev/null || missing+=("libpipewire-0.3-dev (libspa-0.2-dev ile)")
if ! ldconfig -p 2>/dev/null | grep -q 'libclang' \
   && ! ls /usr/lib/llvm-*/lib/libclang*.so* >/dev/null 2>&1; then
  missing+=("libclang-dev (pipewire sandiginin bindgen'i icin)")
fi
if [ ${#missing[@]} -gt 0 ]; then
  echo "Derleme gereksinimleri eksik:" >&2
  for item in "${missing[@]}"; do echo "  - $item" >&2; done
  echo "Sistem paketleri (sudo, bir kez):" >&2
  echo "  sudo apt install libpipewire-0.3-dev libspa-0.2-dev libclang-dev pkg-config" >&2
  exit 3
fi
if [ "${1:-}" = "--check" ]; then
  echo "Derleme gereksinimleri tamam."
  exit 0
fi
if [ $# -gt 0 ]; then
  echo "kullanim: scripts/build-native.sh [--check]" >&2
  exit 2
fi

# Build kimligi: commit + Rust tarafinda commit'lenmemis degisiklik varsa -dirty.
# Zaman damgasi YOK: ayni kaynaktan iki derleme ayni kimligi tasir.
BUILD_ID="$(git -C "$DIR" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
if ! git -C "$DIR" diff --quiet HEAD -- rust 2>/dev/null; then
  BUILD_ID="$BUILD_ID-dirty"
fi
export PCBRIDGE_BUILD_ID="$BUILD_ID"

# `rust/` icinden: rust-toolchain.toml yalnizca calisma dizinine gore secilir,
# `--manifest-path` ile depo kokunden cagrilan cargo onu GORMEZ.
(cd "$DIR/rust" && cargo build --release --locked -p pcbridge-native --target "$TARGET")
BUILT="$DIR/rust/target/$TARGET/release/pcbridge-native"

# Kurmadan once kendini dogrulasin: debug, test-harness ya da yanlis hedefe
# derlenmis bir binary buraya girmesin.
INFO="$("$BUILT" --build-info)"
python3 - "$INFO" "$TARGET" "$BUILD_ID" <<'PY'
import json, sys
info, target, build_id = json.loads(sys.argv[1]), sys.argv[2], sys.argv[3]
problems = []
if info.get("profile") != "release":
    problems.append(f"profil {info.get('profile')!r}, release bekleniyordu")
if info.get("test_harness") is not False:
    problems.append("test-harness derlemesi: sahte backend")
if info.get("target") != target:
    problems.append(f"hedef {info.get('target')!r}, {target!r} bekleniyordu")
if info.get("build_id") != build_id:
    problems.append(f"build kimligi {info.get('build_id')!r}, {build_id!r} bekleniyordu")
if problems:
    sys.exit("Derlenen binary reddedildi: " + "; ".join(problems))
PY

mkdir -p "$DEST"
TMP="$(mktemp "$DEST/.pcbridge-native.XXXXXX")"
install -m 0755 "$BUILT" "$TMP"
mv -f "$TMP" "$DEST/pcbridge-native"
printf '%s\n' "$INFO" > "$DEST/build-info.json"

echo "Kuruldu: $DEST/pcbridge-native"
echo "  $("$DEST/pcbridge-native" --version)"
echo "Calisan servis ve stdio surecleri yeni binary'yi yeniden baslatilana kadar"
echo "KULLANMAZ; bu betik hicbir seyi yeniden baslatmaz."
