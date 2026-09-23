#!/usr/bin/env bash
# Build pcbridge_<version>_amd64.deb with plain dpkg-deb (no nfpm, no uv).
#
#   packaging/build-deb.sh [OUT_DIR]        default OUT_DIR: dist/
#
# The installed layout comes from packaging/stage.sh (shared with the Arch
# package); this adds the Debian control files and doc extras.
#
# The venv is bound to the build host's python3 (3.12 on Ubuntu 24.04), so a
# package is built per distribution release; `Depends` pins that minor version.
set -euo pipefail
umask 022

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$(realpath -m "${1:-$ROOT/dist}")"
PY="${PYTHON:-/usr/bin/python3}"

command -v dpkg-deb >/dev/null || { echo "dpkg-deb is required" >&2; exit 3; }
PYVER="$("$PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
PYNEXT="$("$PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1] + 1}")')"
VERSION="$("$PY" -c "import re; t=open('$ROOT/pcbridge/__init__.py').read(); print(re.search(r'__version__ = \"([^\"]+)\"', t)[1])")"
# PEP 440 -> Debian: 2.0.0.dev0 -> 2.0.0~dev0, 2.0.0rc1 -> 2.0.0~rc1 (sorts before 2.0.0).
DEBVER="$(echo "$VERSION" | sed -E 's/\.?(dev|a|b|rc)([0-9]+)$/~\1\2/')"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
STAGE="$WORK/root"

echo "==> pcbridge $VERSION (deb $DEBVER), python $PYVER"
PYTHON="$PY" "$ROOT/packaging/stage.sh" "$STAGE"
DOC="$STAGE/usr/share/doc/pcbridge"
{
    echo "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/"
    echo "Upstream-Name: pcbridge"
    echo "Source: https://github.com/Eymistaken/Pcbridge"
    echo
    echo "Files: *"
    echo "Copyright: Eymistaken"
    echo "License: see /usr/share/doc/pcbridge/LICENSE"
} > "$DOC/copyright"
[ -f "$ROOT/LICENSE" ] && install -m 0644 "$ROOT/LICENSE" "$DOC/"
printf 'pcbridge (%s) unstable; urgency=medium\n\n  * See CHANGELOG.md.\n\n -- Eymistaken <110712235+Eymistaken@users.noreply.github.com>  %s\n' \
    "$DEBVER" "$(date -R)" | gzip -9n > "$DOC/changelog.Debian.gz"

echo "==> control"
install -d "$STAGE/DEBIAN"
SIZE="$(du -sk --exclude=DEBIAN "$STAGE" | cut -f1)"
# Quoted heredoc + placeholders: nothing in this text can run as a command.
# (An unquoted one once ran the backquoted `pcbridge setup` in the description.)
cat > "$STAGE/DEBIAN/control" <<'CONTROL'
Package: pcbridge
Version: @DEBVER@
Architecture: amd64
Maintainer: Eymistaken <110712235+Eymistaken@users.noreply.github.com>
Installed-Size: @SIZE@
Section: utils
Priority: optional
Homepage: https://github.com/Eymistaken/Pcbridge
Depends: python3 (>= @PYVER@), python3 (<< @PYNEXT@), tmux, wl-clipboard, libnotify-bin, python3-gi, gir1.2-atspi-2.0, gstreamer1.0-pipewire, gir1.2-gst-plugins-base-1.0, libpipewire-0.3-0t64 | libpipewire-0.3-0, libc6 (>= 2.39), libgcc-s1
Recommends: tesseract-ocr, gnome-screenshot
Suggests: tailscale
Description: MCP server that lets coding agents drive a GNOME desktop
 pcbridge gives Claude Code, Codex, Claude Desktop and remote MCP clients
 background jobs, tmux, shell and file tools, and (off by default)
 time-limited control of the GNOME on Wayland desktop through a virtual
 keyboard and pointer, screen capture and the accessibility tree.
 .
 After installing, run "pcbridge setup" as your own user.
CONTROL
sed -i -e "s|@DEBVER@|$DEBVER|" -e "s|@SIZE@|$SIZE|" -e "s|@PYVER@|$PYVER|" -e "s|@PYNEXT@|$PYNEXT|" "$STAGE/DEBIAN/control"
cat > "$STAGE/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
    # The rule tags /dev/uinput with uaccess; nothing here touches a user session.
    udevadm control --reload-rules >/dev/null 2>&1 || true
    modprobe uinput >/dev/null 2>&1 || true
    udevadm trigger --subsystem-match=misc --sysname-match=uinput >/dev/null 2>&1 || true
    echo "pcbridge is installed. As your own user (not root), run:  pcbridge setup"
fi
exit 0
POSTINST
cat > "$STAGE/DEBIAN/postrm" <<'POSTRM'
#!/bin/sh
set -e
if [ "$1" = remove ] || [ "$1" = purge ]; then
    udevadm control --reload-rules >/dev/null 2>&1 || true
fi
exit 0
POSTRM
chmod 0755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

mkdir -p "$OUT"
# One package per release (the venv is bound to its python3), so the file
# name says which: pcbridge_2.0.0_ubuntu24.04_amd64.deb.
DISTRO="$(. /etc/os-release && echo "${ID}${VERSION_ID:-}")"
DEB="$OUT/pcbridge_${DEBVER}_${DISTRO}_amd64.deb"
dpkg-deb --root-owner-group -Zxz --build "$STAGE" "$DEB" >/dev/null
echo "==> $DEB ($(du -h "$DEB" | cut -f1))"
