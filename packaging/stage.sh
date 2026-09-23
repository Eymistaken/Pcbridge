#!/usr/bin/env bash
# Stage pcbridge's installed tree (the venv and every file) under a root dir.
#
#   packaging/stage.sh STAGE_DIR
#
# Shared by the .deb (build-deb.sh) and the Arch package (arch/PKGBUILD), so
# both install the same layout:
#   /usr/lib/pcbridge/venv                  self-contained venv, pinned deps,
#                                           the native helper inside the package
#   /usr/bin/pcbridge, pcb-shot, pcb-do     symlinks into the venv
#   /usr/lib/systemd/user/pcbridge.{service,socket}
#   /usr/lib/udev/rules.d/60-pcbridge-uinput.rules   (60 < 73-seat-late: uaccess)
#   /usr/lib/modules-load.d/pcbridge-uinput.conf
#   /usr/share/gnome-shell/extensions/<uuid>/        schema compiled
#   /usr/share/applications/pcbridge-{native,lock}.desktop   KDE Plasma (KWin screenshots, kill switch)
#   /usr/share/doc/pcbridge/                         README, example config, changelog
#
# The venv is bound to the build host's python (its minor version), so the
# package that carries it must pin that version. The native helper is built
# first when rust/ and cargo are there (scripts/build-native.sh), unless
# PCBRIDGE_SKIP_NATIVE=1; without it the package still works on the Python
# paths.
#
# Environment: PYTHON (default /usr/bin/python3), PCBRIDGE_SKIP_NATIVE,
# PCBRIDGE_CONSTRAINTS (default packaging/constraints.txt; "none" resolves
# the ranges in pyproject.toml instead, for a python the pins do not fit).
set -euo pipefail
umask 022

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="$(realpath -m "${1:?usage: stage.sh STAGE_DIR}")"
PY="${PYTHON:-/usr/bin/python3}"
UUID="pcbridge-gorunur@eymistaken.local"
PREFIX=/usr/lib/pcbridge/venv
PYVER="$("$PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
CONSTRAINTS="${PCBRIDGE_CONSTRAINTS:-$ROOT/packaging/constraints.txt}"
CONSTRAINT_ARGS=()
[ "$CONSTRAINTS" = none ] || CONSTRAINT_ARGS=(-c "$CONSTRAINTS")

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
VENV="$STAGE$PREFIX"

if [ -d "$ROOT/rust" ] && command -v cargo >/dev/null && [ "${PCBRIDGE_SKIP_NATIVE:-0}" != 1 ]; then
    "$ROOT/scripts/build-native.sh"
fi

echo "==> wheel"
"$PY" -m venv "$WORK/build-venv"
"$WORK/build-venv/bin/pip" install -q --upgrade pip wheel setuptools
(cd "$ROOT" && "$WORK/build-venv/bin/pip" wheel . --no-deps -q -w "$WORK/wheel")
rm -rf "$ROOT/build" "$ROOT/pcbridge.egg-info"
WHEEL="$(ls "$WORK"/wheel/pcbridge-*.whl)"

echo "==> venv at $PREFIX"
"$PY" -m venv --without-pip "$VENV"
"$WORK/build-venv/bin/pip" --python "$VENV/bin/python" install -q --no-compile \
    "${CONSTRAINT_ARGS[@]}" "$WHEEL[desktop]"
# Python 3.14 adds a `𝜋thon` alias next to `python`; tar tools in a C
# locale cannot store the name, and nothing uses it.
find "$VENV/bin" -maxdepth 1 -name '*thon' ! -name 'python*' -delete
# Make the venv live at its final path: scripts, activate files, pyvenv.cfg.
grep -rlI --null "$VENV" "$VENV/bin" "$VENV/pyvenv.cfg" | xargs -0 -r sed -i "s|$VENV|$PREFIX|g"
# pip's record of where the wheel came from (a build path); metadata only.
for f in "$VENV"/lib/python*/site-packages/pcbridge-*.dist-info/direct_url.json; do
    [ -f "$f" ] || continue
    rm -f "$f"
    sed -i '/direct_url.json/d' "$(dirname "$f")/RECORD"
done
"$VENV/bin/python" -m compileall -q -j 0 \
    -d "$PREFIX/lib/python$PYVER/site-packages" "$VENV/lib/python$PYVER/site-packages" || true
if grep -rlI -e "$WORK" -e "$STAGE" "$VENV" >/dev/null 2>&1; then
    echo "the venv still refers to the build directory:" >&2
    grep -rlI -e "$WORK" -e "$STAGE" "$VENV" | head >&2
    exit 1
fi

echo "==> files"
install -d "$STAGE/usr/bin"
for name in pcbridge pcb-shot pcb-do; do
    ln -s "../lib/pcbridge/venv/bin/$name" "$STAGE/usr/bin/$name"
done
install -d "$STAGE/usr/lib/systemd/user"
for unit in pcbridge.service pcbridge.socket; do
    sed "s|__PYTHON__|$PREFIX/bin/python|g" "$ROOT/systemd/$unit" > "$STAGE/usr/lib/systemd/user/$unit"
done
install -D -m 0644 "$ROOT/packaging/udev/60-pcbridge-uinput.rules" \
    "$STAGE/usr/lib/udev/rules.d/60-pcbridge-uinput.rules"
install -D -m 0644 "$ROOT/packaging/modules-load/pcbridge-uinput.conf" \
    "$STAGE/usr/lib/modules-load.d/pcbridge-uinput.conf"
EXT="$STAGE/usr/share/gnome-shell/extensions/$UUID"
install -d "$EXT"
cp -r "$ROOT/gnome-extension/$UUID/." "$EXT/"
rm -f "$EXT/schemas/gschemas.compiled"
if command -v glib-compile-schemas >/dev/null; then
    glib-compile-schemas "$EXT/schemas"
fi
# KDE Plasma: KWin gives screenshots only to programs a .desktop file names
# (pcbridge.desktop.kwin), and the kill switch gets a launcher entry that a
# shortcut can be bound to. Written by pcbridge itself, so the text is one.
HELPER="$(ls "$VENV"/lib/python*/site-packages/pcbridge/_native/*/pcbridge-native 2>/dev/null | head -n 1 || true)"
install -d "$STAGE/usr/share/applications"
if [ -n "$HELPER" ]; then
    "$VENV/bin/python" -c 'import sys; from pathlib import Path; from pcbridge.desktop import kwin; sys.stdout.write(kwin.helper_entry(Path(sys.argv[1])))' \
        "${HELPER#"$STAGE"}" > "$STAGE/usr/share/applications/pcbridge-native.desktop"
fi
"$VENV/bin/python" -c 'import sys; from pathlib import Path; from pcbridge.cli import install; sys.stdout.write(install.lock_entry(Path("/usr/bin/pcbridge")))' \
    > "$STAGE/usr/share/applications/pcbridge-lock.desktop"
DOC="$STAGE/usr/share/doc/pcbridge"
install -d "$DOC"
install -m 0644 "$ROOT/README.md" "$ROOT/config.example.toml" "$DOC/"
[ -f "$ROOT/CHANGELOG.md" ] && install -m 0644 "$ROOT/CHANGELOG.md" "$DOC/"
echo "==> staged under $STAGE"
