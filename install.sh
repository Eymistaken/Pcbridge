#!/bin/sh
# pcbridge from a git checkout: create .venv, install this checkout into it
# (editable, with the desktop extras, at the tested dependency versions),
# optionally build the native helper, then run `pcbridge setup`.
#
#   ./install.sh            interactive
#   ./install.sh --yes      no questions
#
# Everything after the venv is `pcbridge setup`; run it again any time.
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY=${PYTHON:-python3}

if ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
  echo "pcbridge needs Python 3.12 or newer ($PY is older)." >&2
  exit 1
fi
if ! "$PY" -c 'import venv, ensurepip' 2>/dev/null; then
  # Arch ships venv inside the python package; this is Debian's split.
  echo "Python's venv module is missing: sudo apt install python3-venv" >&2
  exit 1
fi

[ -d "$DIR/.venv" ] || "$PY" -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q --upgrade pip
"$DIR/.venv/bin/pip" install -q -c "$DIR/packaging/constraints.txt" -e "$DIR[desktop]"

if [ ! -x "$DIR/pcbridge/_native/x86_64-unknown-linux-gnu/pcbridge-native" ]; then
  if command -v cargo >/dev/null 2>&1; then
    echo "Building the native helper (faster capture, input and accessibility)..."
    "$DIR/scripts/build-native.sh" || echo "Native helper build failed; pcbridge uses the Python paths instead." >&2
  else
    echo "note: no Rust toolchain; pcbridge uses the Python paths (install Rust and run scripts/build-native.sh for the native helper)."
  fi
fi

exec "$DIR/.venv/bin/pcbridge" setup "$@"
