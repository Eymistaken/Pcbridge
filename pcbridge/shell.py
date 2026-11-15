"""Which shell runs shell_run and shell_run_background, and how.

Linux keeps what pcbridge always ran: `bash -lc COMMAND`. Windows has no
bash by default, so the default there is PowerShell: PowerShell 7 (`pwsh`)
when it is installed, else Windows PowerShell 5.1, without a profile and
non-interactive. `[shell] program` picks another one. PowerShell writes the
console code page unless told otherwise (cp1252 in the test VM, measured),
so its output encoding is set to UTF-8 first; the caller reads UTF-8.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

_PS_UTF8 = "$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "


def _powershell(exe: str, command: str) -> list[str]:
    return [exe, "-NoProfile", "-NonInteractive", "-Command", _PS_UTF8 + command]


def _which(name: str) -> str | None:
    return shutil.which(name)


def _git_bash() -> str | None:
    found = _which("bash")
    # C:\Windows\System32\bash.exe is WSL's launcher, not Git Bash.
    if found and "system32" not in found.lower():
        return found
    for root in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if Path(root).is_file():
            return root
    return None


def describe(program: str = "") -> str:
    """A short name for the shell `program` selects, for tool texts."""
    choice = (program or "").strip().lower()
    if not choice:
        if not IS_WINDOWS:
            return "bash"
        return "PowerShell 7" if _which("pwsh") else "Windows PowerShell"
    return program


def argv(command: str, program: str = "") -> list[str]:
    """The argv that runs `command` in the configured shell."""
    choice = (program or "").strip()
    lowered = choice.lower()
    if not choice:
        if not IS_WINDOWS:
            return ["bash", "-lc", command]
        pwsh = _which("pwsh")
        return _powershell(pwsh or "powershell.exe", command)
    if lowered == "bash":
        exe = _git_bash() if IS_WINDOWS else "bash"
        if exe is None:
            raise FileNotFoundError("[shell] program = \"bash\", but Git Bash is not installed")
        return [exe, "-lc", command]
    if lowered in ("pwsh", "powershell"):
        exe = _which(lowered) or ("powershell.exe" if lowered == "powershell" else None)
        if exe is None:
            raise FileNotFoundError(f"[shell] program = \"{choice}\", but it is not installed")
        return _powershell(exe, command)
    if lowered == "cmd":
        return ["cmd.exe", "/d", "/s", "/c", command]
    return [choice, "-c", command]
