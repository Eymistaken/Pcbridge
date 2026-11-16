"""The applications a Windows user can start, and starting one.

Linux reads the installed `.desktop` entries and starts one with
`gtk-launch`. Windows' counterpart is the Start menu: `Get-StartApps` lists
every application it shows, shortcuts and packaged (Store) apps alike, each
with its AppUserModelID or path (`AppID`), and
`explorer.exe shell:AppsFolder\\<AppID>` starts any of them the way the
Start menu does. Nothing else is run: `launch` opens an application, never
an arbitrary command.

The list takes about a second to read, so it is kept for a minute.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import PureWindowsPath

_CACHE_SECONDS = 60.0
_lock = threading.Lock()
_cache: tuple[float, list] | None = None

_LIST_COMMAND = (
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
    "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"
)
CREATE_NO_WINDOW = 0x08000000


def _exec_name(app_id: str) -> str:
    """The executable's name when the AppID is a path ("notepad"), else ""."""
    if app_id.lower().endswith(".exe"):
        return PureWindowsPath(app_id).stem.lower()
    return ""


def start_apps() -> list[tuple[str, str]]:
    """(name, AppID) of every Start menu application; [] when unreadable."""
    global _cache
    with _lock:
        if _cache is not None and time.monotonic() - _cache[0] < _CACHE_SECONDS:
            return list(_cache[1])
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _LIST_COMMAND],
            capture_output=True, timeout=20, creationflags=CREATE_NO_WINDOW,
        )
        data = json.loads(proc.stdout.decode("utf-8", "replace") or "[]")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []
    rows = data if isinstance(data, list) else [data]
    found = [(str(row.get("Name") or ""), str(row.get("AppID") or ""))
             for row in rows if isinstance(row, dict)]
    found = [(name, app_id) for name, app_id in found if name and app_id]
    with _lock:
        _cache = (time.monotonic(), found)
    return list(found)


def entries(entry_type):
    """The Start menu applications as `apps.Entry` values (`entry_type`)."""
    out = []
    seen: set[str] = set()
    for name, app_id in start_apps():
        if app_id in seen:
            continue
        seen.add(app_id)
        out.append(entry_type(app_id, name, False, (name,), (), _exec_name(app_id)))
    return out


def launch(app_id: str) -> None:
    """Start the application the Start menu knows as `app_id`.

    explorer.exe answers at once and its exit code says nothing about the
    application (it is 1 even on success), so success is decided the way it
    is on Linux: by its window showing up.
    """
    subprocess.Popen(
        ["explorer.exe", f"shell:AppsFolder\\{app_id}"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )
