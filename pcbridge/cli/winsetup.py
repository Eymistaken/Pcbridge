"""The parts of `pcbridge setup` and uninstall that are Windows' own.

Linux puts launchers in ~/.local/bin as symlinks and asks the shell's file to
put that on PATH. Windows has neither: creating a symlink needs a privilege
an ordinary user lacks (measured in the port's W8: WinError 1314), and the
user's PATH lives in the registry (`HKCU\\Environment`). So on Windows the
venv's own Scripts folder, where pip puts `pcbridge.exe`, `pcb-shot.exe` and
`pcb-do.exe`, goes on the user's PATH, and the computer-use skill is linked
with a directory junction, which needs no privilege.

The service needs nothing here: `winservice` writes the Run key when
`systemctl enable` is mapped to it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ENVIRONMENT_KEY = "Environment"
CREATE_NO_WINDOW = 0x08000000


def scripts_dir() -> Path:
    """Where pip put this installation's commands."""
    return Path(sys.prefix) / "Scripts"


def user_path() -> tuple[str, int]:
    """The user's own PATH from the registry and its value type ("", REG_EXPAND_SZ when unset)."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _ENVIRONMENT_KEY) as key:
            value, kind = winreg.QueryValueEx(key, "Path")
            return str(value), int(kind)
    except FileNotFoundError:
        return "", winreg.REG_EXPAND_SZ


def _entries(value: str) -> list[str]:
    return [part for part in value.split(";") if part.strip()]


def _same(left: str, right: Path) -> bool:
    try:
        return os.path.normcase(os.path.abspath(os.path.expandvars(left))) == \
            os.path.normcase(os.path.abspath(right))
    except (OSError, ValueError):
        return False


def on_user_path(folder: Path | None = None) -> bool:
    folder = folder or scripts_dir()
    return any(_same(entry, folder) for entry in _entries(user_path()[0]))


def path_note() -> str:
    """What to do when the commands are not on the user's PATH, "" when they are."""
    folder = scripts_dir()
    if on_user_path(folder):
        if any(_same(entry, folder) for entry in _entries(os.environ.get("PATH", ""))):
            return ""
        return (f"{folder} is on your PATH for new terminals; this one still needs the full "
                f"path ({folder / 'pcbridge.exe'})")
    return (f"{folder} is not on your PATH, so `pcbridge` is not found by name; setup can "
            "add it to your user PATH (HKCU\\Environment)")


def _broadcast() -> None:
    """Tell running programs (Explorer, new terminals) that the environment changed."""
    import ctypes
    from ctypes import wintypes as W

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendMessageTimeoutW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPCWSTR, W.UINT, W.UINT,
                                           ctypes.POINTER(ctypes.c_size_t)]
    result = ctypes.c_size_t()
    # HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG, 5 s
    user32.SendMessageTimeoutW(W.HWND(0xFFFF), 0x001A, 0, "Environment", 0x0002, 5000,
                               ctypes.byref(result))


def add_to_user_path(folder: Path | None = None) -> str:
    """Append `folder` to the user's PATH; one line saying what happened."""
    import winreg

    folder = folder or scripts_dir()
    value, kind = user_path()
    if any(_same(entry, folder) for entry in _entries(value)):
        return f"{folder} is already on your PATH"
    new = ";".join([*_entries(value), str(folder)])
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _ENVIRONMENT_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "Path", 0, kind if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ)
                          else winreg.REG_EXPAND_SZ, new)
    _broadcast()
    return f"added {folder} to your user PATH (new terminals find `pcbridge`)"


def remove_from_user_path(folder: Path | None = None) -> bool:
    """Take `folder` off the user's PATH again (uninstall). True when it was there."""
    import winreg

    folder = folder or scripts_dir()
    value, kind = user_path()
    entries = _entries(value)
    kept = [entry for entry in entries if not _same(entry, folder)]
    if kept == entries:
        return False
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _ENVIRONMENT_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "Path", 0, kind, ";".join(kept))
    _broadcast()
    return True


def is_junction(path: Path) -> bool:
    try:
        return path.is_junction()
    except (AttributeError, OSError):
        return False


def link_directory(link: Path, target: Path) -> None:
    """A directory junction `link` -> `target` (no privilege needed, unlike a symlink)."""
    link.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True,
                   capture_output=True, creationflags=CREATE_NO_WINDOW)


def recycle(path: Path) -> bool:
    """Move `path` to the Recycle Bin (never a permanent delete). True when it went."""
    import ctypes
    from ctypes import wintypes as W

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", W.HWND), ("wFunc", W.UINT), ("pFrom", W.LPCWSTR), ("pTo", W.LPCWSTR),
                    ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", W.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", W.LPCWSTR)]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    # FO_DELETE with FOF_ALLOWUNDO (to the Recycle Bin), no dialogs.
    operation = SHFILEOPSTRUCTW(None, 0x0003, str(path.resolve()) + "\0", None,
                                0x0040 | 0x0010 | 0x0004 | 0x0400, False, None, None)
    return shell32.SHFileOperationW(ctypes.byref(operation)) == 0 and not path.exists()


__all__ = ["add_to_user_path", "is_junction", "recycle", "link_directory", "on_user_path", "path_note",
           "remove_from_user_path", "scripts_dir", "user_path"]
