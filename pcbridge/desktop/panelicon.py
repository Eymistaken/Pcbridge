"""When the GNOME extension shows pcbridge's panel icon.

The extension reads its `indicator-mode` setting: "always" (the default), or
"when-granted", which hides the icon while desktop control is closed. An
open grant shows the icon in either mode; that rule lives in the extension
(`status.js: indicatorVisible`), so nothing on this side can hide the icon
while an agent has the desktop.

The setting is a GSettings key of the extension's own schema. `gsettings`
is pointed at the installed extension's schema directory, so a missing
extension or an extension older than the key is reported, not guessed.
KDE Plasma has no pcbridge panel icon.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .. import assets as assetslib

SCHEMA_ID = "org.gnome.shell.extensions.pcbridge-gorunur"
KEY = "indicator-mode"
MODES = ("always", "when-granted")
# The first extension version that reads `indicator-mode`.
SUPPORTED_FROM = (2, 2)
_FOCUS_NAME = "io.github.eymistaken.Pcbridge.WindowFocus"
_FOCUS_PATH = "/io/github/eymistaken/Pcbridge/WindowFocus"


class PanelIconError(Exception):
    """The setting cannot be read or written; the message says why."""


def schema_dirs() -> list[Path]:
    """Where an installed extension keeps its compiled schema, user copy first
    (GNOME Shell prefers a user extension over a system one)."""
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return [base / "gnome-shell" / "extensions" / assetslib.EXTENSION_UUID / "schemas"
            for base in (data, Path("/usr/share"))]


def schema_dir() -> Path | None:
    return next((d for d in schema_dirs() if (d / "gschemas.compiled").is_file()), None)


def _gsettings(*args: str) -> str:
    where = schema_dir()
    if where is None:
        raise PanelIconError(
            "pcbridge's GNOME extension is not installed, or its settings schema is not "
            "compiled. Run `pcbridge setup`.")
    try:
        # C locale: the "No such key" check reads gsettings' message.
        proc = subprocess.run(["gsettings", "--schemadir", str(where), *args],
                              capture_output=True, text=True, timeout=5,
                              env={**os.environ, "LC_ALL": "C"})
    except FileNotFoundError as exc:
        raise PanelIconError("`gsettings` was not found (it comes with GLib).") from exc
    except subprocess.TimeoutExpired as exc:
        raise PanelIconError("`gsettings` did not answer within 5 s.") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip()
        if "No such key" in err:
            raise PanelIconError(
                "The installed extension predates this setting. Update pcbridge "
                "(`pcbridge setup` installs the current extension), then log in again.")
        raise PanelIconError(f"gsettings failed: {err[:200]}")
    return proc.stdout.strip()


def get_mode() -> str:
    """The stored mode: "always" or "when-granted"."""
    return _gsettings("get", SCHEMA_ID, KEY).strip("'\"")


def set_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    _gsettings("set", SCHEMA_ID, KEY, mode)


def running_version() -> str | None:
    """The extension version this GNOME session loaded, or None when none is
    running (it loads new files only at the next login)."""
    try:
        out = subprocess.run(
            ["busctl", "--user", "get-property", _FOCUS_NAME, _FOCUS_PATH, _FOCUS_NAME, "Version"],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r's "([^"]*)"', out.stdout.strip()) if out.returncode == 0 else None
    return m.group(1) if m else None


def running_supports_mode(version: str | None) -> bool:
    m = re.match(r"(\d+)\.(\d+)", version or "")
    return bool(m) and (int(m.group(1)), int(m.group(2))) >= SUPPORTED_FROM
