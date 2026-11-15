r"""Where pcbridge keeps its files: one place that knows the XDG layout.

    config   $XDG_CONFIG_HOME/pcbridge/config.toml   (~/.config/pcbridge)
    state    $XDG_STATE_HOME/pcbridge                (~/.local/state/pcbridge)
    logs     <state>/log
    cache    $XDG_CACHE_HOME/pcbridge                (~/.cache/pcbridge)
    data     $XDG_DATA_HOME/pcbridge                 (~/.local/share/pcbridge)
    runtime  $XDG_RUNTIME_DIR/pcbridge               (mode 0700; the socket lives here)

On Windows, unless the XDG variables are set (the tests set them):

    config   %APPDATA%\pcbridge
    state    %LOCALAPPDATA%\pcbridge\state
    cache    %LOCALAPPDATA%\pcbridge\cache
    data     %LOCALAPPDATA%\pcbridge\data
    runtime  %LOCALAPPDATA%\pcbridge\run    (a DACL for the user only)

The state directory is shared with processes that may run older code (legacy
stdio servers, the GNOME extension, the PcBridgeDesktop app), so it is never
moved. Nothing here creates a directory unless asked to.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from . import oscompat

APP = "pcbridge"

# The config file of a git checkout from before 2.0. Still read, after the
# XDG location, so an old install keeps working until `pcbridge setup`
# migrates it.
LEGACY_REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"


def _env_dir(var: str) -> Path | None:
    value = os.environ.get(var, "")
    # The XDG spec says relative values are invalid and must be ignored.
    return Path(value) if value and os.path.isabs(value) else None


def _windows_root(var: str, fallback: str) -> Path:
    """%APPDATA% or %LOCALAPPDATA%, with the profile default when unset."""
    return _env_dir(var) or Path.home() / fallback


def _home(xdg_var: str, posix_fallback: str, windows_root: str, windows_sub: str) -> Path:
    xdg = _env_dir(xdg_var)
    if xdg is not None:
        return xdg / APP
    if oscompat.IS_WINDOWS:
        if windows_root == "APPDATA":
            base = _windows_root("APPDATA", "AppData/Roaming") / APP
        else:
            base = _windows_root("LOCALAPPDATA", "AppData/Local") / APP
        return base / windows_sub if windows_sub else base
    return Path.home() / posix_fallback / APP


def config_home() -> Path:
    return _home("XDG_CONFIG_HOME", ".config", "APPDATA", "")


def config_file() -> Path:
    return config_home() / "config.toml"


def state_home() -> Path:
    return _home("XDG_STATE_HOME", ".local/state", "LOCALAPPDATA", "state")


def log_dir(state_dir: Path | None = None) -> Path:
    return (state_dir or state_home()) / "log"


def cache_home() -> Path:
    return _home("XDG_CACHE_HOME", ".cache", "LOCALAPPDATA", "cache")


def data_home() -> Path:
    return _home("XDG_DATA_HOME", ".local/share", "LOCALAPPDATA", "data")


def _usable_base(base: Path) -> bool:
    try:
        st = base.stat()
    except OSError:
        return False
    if not stat.S_ISDIR(st.st_mode) or not os.access(base, os.W_OK | os.X_OK):
        return False
    # On Windows the profile's AppData\Local need not be owned by the user
    # (SYSTEM may own it); the pcbridge directory inside it is checked in
    # runtime_dir() instead, and that one pcbridge creates and owns.
    return oscompat.IS_WINDOWS or oscompat.owned_by_me(st)


def runtime_base() -> Path | None:
    """The user's runtime directory, or None when there is no usable one."""
    candidates = [base] if (base := _env_dir("XDG_RUNTIME_DIR")) is not None else []
    if oscompat.IS_WINDOWS:
        candidates.append(_windows_root("LOCALAPPDATA", "AppData/Local"))
    else:
        candidates.append(Path(f"/run/user/{os.getuid()}"))
    for base in candidates:
        if _usable_base(base):
            return base
    return None


def _runtime_unavailable() -> str:
    if oscompat.IS_WINDOWS:
        return "no usable runtime directory: XDG_RUNTIME_DIR and %LOCALAPPDATA% are not writable"
    return ("no usable runtime directory: XDG_RUNTIME_DIR is unset or not writable "
            f"and /run/user/{os.getuid()} does not exist")


class RuntimeDirError(OSError):
    """The runtime directory is missing, not ours, or not private."""


def runtime_dir(create: bool = False) -> Path:
    """``$XDG_RUNTIME_DIR/pcbridge``, private to the user.

    With ``create=True`` it is created with mode 0700 and an existing one is
    tightened to 0700. Raises RuntimeDirError, with an English message, when
    no private runtime directory can be had; callers then fall back to
    working without a socket.
    """
    base = runtime_base()
    if base is None:
        raise RuntimeDirError(_runtime_unavailable())
    windows_default = oscompat.IS_WINDOWS and _env_dir("XDG_RUNTIME_DIR") is None
    path = base / APP / "run" if windows_default else base / APP
    if create:
        path.mkdir(mode=0o700, parents=windows_default, exist_ok=True)
        if oscompat.IS_WINDOWS and not path.is_symlink():
            # Owner and DACL first: an elevated process creates directories
            # owned by the Administrators group, not by the user.
            oscompat.restrict_path(path, directory=True)
    try:
        st = path.lstat()
    except FileNotFoundError:
        if create:
            raise
        return path
    owned = oscompat.owned_by_me(path if oscompat.IS_WINDOWS else st)
    if not stat.S_ISDIR(st.st_mode) or not owned:
        raise RuntimeDirError(f"{path} is not a directory owned by this user")
    if create and not oscompat.IS_WINDOWS and stat.S_IMODE(st.st_mode) != 0o700:
        oscompat.restrict_path(path, directory=True)
    return path


def socket_path() -> Path:
    """Where the daemon listens for local MCP sessions (on Windows, the file
    that says where)."""
    override = os.environ.get("PCBRIDGE_SOCKET", "")
    if override:
        return Path(override)
    # Windows has no Unix socket in CPython: the daemon publishes its
    # loopback port and secret in this file instead (localipc).
    return runtime_dir() / ("endpoint.json" if oscompat.IS_WINDOWS else "mcp.sock")
