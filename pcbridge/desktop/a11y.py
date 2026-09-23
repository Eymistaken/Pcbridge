"""KDE Plasma: turn Qt accessibility on for the grant, and back off after it.

Qt applications join AT-SPI only while `org.a11y.Status.IsEnabled` is true
(measured on Plasma 6.7.5: with it false the tree held no Kate, Konsole or
plasmashell; set true, the running ones appeared within 2 s). GTK
applications on GNOME are always there, so this is Plasma only.

The switch is the user's setting, and it persists (dconf
`toolkit-accessibility`), so pcbridge only turns it on when it was off,
leaves a marker saying so in its state directory, and turns it back off when
the grant ends: `desktop_lock`, the kill switch, and the expiry cleanup all
call `restore`. A marker that survived a crash is honored at the next end of
a grant. A user who had it on keeps it on: no marker, nothing restored.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from . import compositor as compositorlib

log = logging.getLogger("pcbridge")

MARKER = "a11y-enabled-by-pcbridge"
_STATUS = ("org.a11y.Bus", "/org/a11y/bus", "org.a11y.Status", "IsEnabled")


def _busctl(*args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["busctl", "--user", *args], capture_output=True,
                              text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None


def is_enabled() -> bool | None:
    proc = _busctl("get-property", *_STATUS)
    if proc is None or proc.returncode != 0:
        return None
    words = proc.stdout.split()
    return words == ["b", "true"] if words[:1] == ["b"] else None


def _set(value: bool) -> bool:
    proc = _busctl("set-property", *_STATUS, "b", "true" if value else "false")
    return proc is not None and proc.returncode == 0


def enable_for_grant(state_dir: Path) -> str:
    """Turn Qt accessibility on (Plasma only). Returns a line for the user, or ""."""
    if not compositorlib.is_kde():
        return ""
    current = is_enabled()
    if current is True:
        return ""
    if current is None or not _set(True):
        return ("⚠️ Qt accessibility could not be turned on; ui_dump and ui_click "
                "may not see KDE applications.")
    marker = Path(state_dir) / MARKER
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("false\n", encoding="utf-8")
    except OSError as exc:
        log.warning("a11y marker not written: %s", exc)
    return ("♿ Qt accessibility is on for this grant (KDE applications need it for "
            "ui_dump and ui_click); it goes back off when the grant ends.")


def restore(state_dir: Path) -> bool:
    """Undo `enable_for_grant` if pcbridge turned the switch on. True when it did."""
    marker = Path(state_dir) / MARKER
    if not marker.exists():
        return False
    restored = _set(False)
    if restored:
        try:
            marker.unlink()
        except OSError:
            pass
    return restored
