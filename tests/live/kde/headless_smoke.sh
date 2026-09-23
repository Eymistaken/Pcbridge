#!/usr/bin/env bash
# A headless KWin with pcbridge's Plasma paths: detection, the monitor table,
# a KWin script round trip, and a ScreenShot2 frame. Sends no input.
#
#   tests/live/kde/headless_smoke.sh        (PY defaults to .venv/bin/python)
#
# Runs under its own dbus-run-session and runtime directory, so it touches
# no running session. Needs kwin_wayland, kscreen-doctor, kbuildsycoca6 and
# the system python with python-gobject. CI runs it in an Arch container;
# it also runs in the test VM (scripts/dev/arch-vm.sh).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PY="${PY:-$ROOT/.venv/bin/python}"
W="$(mktemp -d /tmp/pcbk.XXXX)"
# The document portal mounts itself under the runtime dir; unmount it first.
trap 'fusermount3 -u "$W/xdg/doc" 2>/dev/null || true; rm -rf "$W" 2>/dev/null || true' EXIT
export XDG_RUNTIME_DIR="$W/xdg" XDG_DATA_HOME="$W/data" XDG_CONFIG_HOME="$W/config"
export XDG_CACHE_HOME="$W/cache" XDG_STATE_HOME="$W/state"
unset WAYLAND_DISPLAY DISPLAY DBUS_SESSION_BUS_ADDRESS
mkdir -m 700 -p "$XDG_RUNTIME_DIR"
mkdir -p "$XDG_DATA_HOME/applications"

# The system python takes the screenshots in this smoke, so it is what a
# .desktop file authorizes here (pcbridge itself authorizes only its helper).
SYSPY="$(readlink -f /usr/bin/python3)"
"$PY" -c 'import sys; from pathlib import Path; from pcbridge.desktop import kwin
sys.stdout.write(kwin.helper_entry(Path(sys.argv[1])))' "$SYSPY" \
    > "$XDG_DATA_HOME/applications/pcbridge-smoke.desktop"

dbus-run-session -- bash -ec '
  kbuildsycoca6 >/dev/null 2>&1 || true
  kwin_wayland --virtual --no-lockscreen --socket smoke --width 1280 --height 720 \
      > "'"$W"'/kwin.log" 2>&1 &
  for i in $(seq 60); do busctl --user status org.kde.KWin >/dev/null 2>&1 && break; sleep 1; done
  busctl --user status org.kde.KWin >/dev/null || { echo "KWin did not start"; exit 1; }
  export WAYLAND_DISPLAY=smoke XDG_CURRENT_DESKTOP=KDE XDG_SESSION_TYPE=wayland
  cd "'"$ROOT"'"
  "'"$PY"'" - <<PYEOF
import json, subprocess
from pcbridge.desktop import monitors, session
assert session.desktop_kind() == "kde", session.desktop_kind()
mons = monitors.list_monitors(use_cache=False)
print(monitors.describe())
assert mons and monitors.canvas_size(mons) == (1280, 720), monitors.canvas_size(mons)
reply = json.loads(subprocess.run(["/usr/bin/python3", "pcbridge/desktop/kwin_helper.py"],
                                  input="{\"cmd\": \"focused\"}", capture_output=True,
                                  text=True).stdout)
print("kwin script:", reply)
assert reply.get("ok") is True, reply
PYEOF
  sleep 3   # KWin follows new .desktop files within seconds
  compositing="$(busctl --user call org.kde.KWin /KWin org.kde.KWin supportInformation \
      | grep -o "Compositing Type: [A-Za-z]*" | head -1)"
  echo "${compositing:-Compositing Type: not reported}"
  out="$(/usr/bin/python3 tests/live/kde/probe_screenshot2.py --repeat 1)"
  echo "$out"
  if ! echo "$out" | grep -q "\"ok\": true"; then
    # With no GPU render node (a CI container) KWin composites with QPainter,
    # and ScreenShot2 is not offered there; the frame is checked in the VM.
    case "$compositing" in
      *QPainter*) echo "screenshot skipped: KWin runs QPainter compositing here" ;;
      *) exit 1 ;;
    esac
  fi
  kill %1 2>/dev/null || true
' || { echo "--- kwin log"; tail -30 "$W/kwin.log"; exit 1; }
echo "KWin smoke passed"
