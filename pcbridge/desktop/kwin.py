"""KDE Plasma (KWin) pieces that have no GNOME counterpart.

Screenshots
    KWin's `org.kde.KWin.ScreenShot2` answers only programs that a `.desktop`
    file authorizes: `X-KDE-DBUS-Restricted-Interfaces` names the interface
    and `Exec` names the program (measured on Plasma 6.7.5; KWin follows the
    file within seconds). pcbridge authorizes its native helper and nothing
    else: authorizing the system python would let every python script take
    screenshots. `pcbridge setup` writes the file (`install_helper_entry`);
    the capability report asks `helper_authorized` before claiming capture.
"""

from __future__ import annotations

import os
from pathlib import Path

SCREENSHOT_SERVICE = "org.kde.KWin.ScreenShot2"
RESTRICTED_KEY = "X-KDE-DBUS-Restricted-Interfaces"
HELPER_ENTRY = "pcbridge-native.desktop"


def helper_entry(binary: Path) -> str:
    """The `.desktop` text that authorizes `binary` for KWin screenshots."""
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=pcbridge native helper\n"
        "Comment=Lets pcbridge take screenshots on KDE Plasma while desktop control is granted\n"
        f"Exec={Path(binary).resolve()}\n"
        "NoDisplay=true\n"
        f"{RESTRICTED_KEY}={SCREENSHOT_SERVICE}\n"
    )


def data_dirs() -> list[Path]:
    """Where KWin looks for applications: XDG data home, then the data dirs."""
    home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [Path(home), *(Path(d) for d in dirs.split(":") if d)]


def _authorizes(path: Path, binary: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    exec_ok = listed = False
    for line in text.splitlines():
        if line.startswith("Exec="):
            program = (line[len("Exec="):].split() or [""])[0]
            try:
                exec_ok = bool(program) and Path(program).resolve() == binary
            except OSError:
                exec_ok = False
        elif line.startswith(RESTRICTED_KEY):
            names = line[len(RESTRICTED_KEY):].lstrip("=").replace(";", ",").split(",")
            listed = SCREENSHOT_SERVICE in (n.strip() for n in names)
    return exec_ok and listed


def helper_authorized(binary: Path, dirs: list[Path] | None = None) -> Path | None:
    """The `pcbridge*.desktop` file that authorizes `binary`, or None.

    The same search the native helper runs for its own capability report.
    """
    try:
        binary = Path(binary).resolve()
    except OSError:
        return None
    for base in dirs if dirs is not None else data_dirs():
        apps = base / "applications"
        try:
            entries = sorted(apps.glob("pcbridge*.desktop"))
        except OSError:
            continue
        for entry in entries:
            if _authorizes(entry, binary):
                return entry
    return None


def install_helper_entry(binary: Path, dest_dir: Path | None = None) -> tuple[Path, bool]:
    """Write the helper's entry into the user's applications dir.

    Returns the path and whether it changed. Idempotent: an identical file is
    left alone, so KWin sees no churn.
    """
    base = dest_dir or Path(data_dirs()[0]) / "applications"
    base.mkdir(parents=True, exist_ok=True)
    path = base / HELPER_ENTRY
    text = helper_entry(binary)
    try:
        if path.read_text(encoding="utf-8") == text:
            return path, False
    except OSError:
        pass
    tmp = path.with_suffix(".desktop.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path, True
