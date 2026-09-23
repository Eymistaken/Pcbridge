#!/usr/bin/env bash
# Build pcbridge-native as RELEASE and put it where the Python package finds it.
#
#   scripts/build-native.sh           build, verify, install under pcbridge/_native/<target>/
#   scripts/build-native.sh --check   only check the build requirements
#
# Why release: in a debug build PNG encoding is ~9 times slower
# (measured 2026-09-13: ~1755 ms against ~200 ms per monitor).
#
# Does NOT restart the running service. Running processes keep using the old
# binary (replacing a running file is safe on Linux); the new binary is picked
# up on the next start.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The only target: Windows and macOS builds were dropped (ROADMAP.md).
TARGET="x86_64-unknown-linux-gnu"
DEST="$DIR/pcbridge/_native/$TARGET"

missing=()
command -v cargo >/dev/null || missing+=("the Rust toolchain: https://rustup.rs (rust/rust-toolchain.toml selects 1.95.0 by itself)")
command -v pkg-config >/dev/null || missing+=("pkg-config")
pkg-config --exists libpipewire-0.3 2>/dev/null || missing+=("libpipewire-0.3-dev (with libspa-0.2-dev)")
if ! ldconfig -p 2>/dev/null | grep -q 'libclang' \
   && ! ls /usr/lib/llvm-*/lib/libclang*.so* >/dev/null 2>&1; then
  missing+=("libclang-dev (for the bindgen of the pipewire crate)")
fi
if [ ${#missing[@]} -gt 0 ]; then
  echo "Build requirements are missing:" >&2
  for item in "${missing[@]}"; do echo "  - $item" >&2; done
  echo "System packages (sudo, once):" >&2
  echo "  sudo apt install libpipewire-0.3-dev libspa-0.2-dev libclang-dev pkg-config" >&2
  exit 3
fi
if [ "${1:-}" = "--check" ]; then
  echo "Build requirements are present."
  exit 0
fi
if [ $# -gt 0 ]; then
  echo "usage: scripts/build-native.sh [--check]" >&2
  exit 2
fi

# Build id: the commit, plus -dirty when rust/ has uncommitted changes.
# NO timestamp: two builds from the same source carry the same id.
BUILD_ID="$(git -C "$DIR" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
if ! git -C "$DIR" diff --quiet HEAD -- rust 2>/dev/null; then
  BUILD_ID="$BUILD_ID-dirty"
fi
export PCBRIDGE_BUILD_ID="$BUILD_ID"

# From inside `rust/`: rust-toolchain.toml is picked by the working directory,
# so cargo called from the repo root with `--manifest-path` does NOT see it.
(cd "$DIR/rust" && cargo build --release --locked -p pcbridge-native --target "$TARGET")
BUILT="$DIR/rust/target/$TARGET/release/pcbridge-native"

# Check the binary before installing it: no debug, test-harness or
# wrong-target build gets in here.
INFO="$("$BUILT" --build-info)"
python3 - "$INFO" "$TARGET" "$BUILD_ID" <<'PY'
import json, sys
info, target, build_id = json.loads(sys.argv[1]), sys.argv[2], sys.argv[3]
problems = []
if info.get("profile") != "release":
    problems.append(f"profile {info.get('profile')!r}, expected release")
if info.get("test_harness") is not False:
    problems.append("test-harness build: fake backend")
if info.get("target") != target:
    problems.append(f"target {info.get('target')!r}, expected {target!r}")
if info.get("build_id") != build_id:
    problems.append(f"build id {info.get('build_id')!r}, expected {build_id!r}")
if problems:
    sys.exit("built binary rejected: " + "; ".join(problems))
PY

mkdir -p "$DEST"
TMP="$(mktemp "$DEST/.pcbridge-native.XXXXXX")"
install -m 0755 "$BUILT" "$TMP"
mv -f "$TMP" "$DEST/pcbridge-native"
printf '%s\n' "$INFO" > "$DEST/build-info.json"

echo "Installed: $DEST/pcbridge-native"
echo "  $("$DEST/pcbridge-native" --version)"
echo "A running pcbridge keeps the old binary until it restarts; this script"
echo "restarts nothing (\`pcbridge update\` does, once no job is running)."
