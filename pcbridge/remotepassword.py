"""Creating or replacing the remote password, for every place that does it.

The password exists only for remote access; local clients never use it.
It is stored as a hash (`hashing.py`), so it cannot be shown again later:
whoever creates it is shown it once, next to the remote address, and either
types their own or takes a generated word passphrase (`passphrase.py`).

The terminal UI's Remote tab, `pcbridge remote start`, the end of `pcbridge
setup` and `pcbridge set auth.password` all save it through `save()`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import passphrase
from . import settings as S

MIN_LENGTH = 12

ONCE_NOTE = ("This is the only time it is shown: only its hash is stored, so it cannot be "
             "read back, only replaced. Save it now (a screenshot or a password manager).")


def generate() -> str:
    return passphrase.generate()


def check(password: str) -> str | None:
    """Why this password cannot be used, or None."""
    if len(password) < MIN_LENGTH:
        return f"The password must be at least {MIN_LENGTH} characters."
    if password != password.strip():
        return "The password must not start or end with a space."
    return None


def save(password: str, path: Path | str | None = None) -> S.SaveResult:
    """Store the hash of `password` as `[auth] password`, with the usual backup."""
    why = check(password)
    if why:
        raise S.SettingsError(why)
    ed = S.ConfigEditor(path) if path else S.ConfigEditor()
    ed.set("auth.password", password)
    return ed.save()


def copy_to_clipboard(text: str) -> bool:
    """Put `text` on the desktop clipboard with wl-copy; False when that is not possible."""
    if not shutil.which("wl-copy"):
        return False
    try:
        subprocess.run(["wl-copy"], input=text, text=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return True
