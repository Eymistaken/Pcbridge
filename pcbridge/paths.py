"""Where pcbridge keeps its files: one place that knows the XDG layout.

    config   $XDG_CONFIG_HOME/pcbridge/config.toml   (~/.config/pcbridge)
    state    $XDG_STATE_HOME/pcbridge                (~/.local/state/pcbridge)
    logs     <state>/log
    cache    $XDG_CACHE_HOME/pcbridge                (~/.cache/pcbridge)
    data     $XDG_DATA_HOME/pcbridge                 (~/.local/share/pcbridge)
    runtime  $XDG_RUNTIME_DIR/pcbridge               (mode 0700; the socket lives here)

The state directory is shared with processes that may run older code (legacy
stdio servers, the GNOME extension, the PcBridgeDesktop app), so it is never
moved. Nothing here creates a directory unless asked to.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

APP = "pcbridge"

# The config file of a git checkout from before 2.0. Still read, after the
# XDG location, so an old install keeps working until `pcbridge setup`
# migrates it.
LEGACY_REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var, "")
    # The XDG spec says relative values are invalid and must be ignored.
    if value and os.path.isabs(value):
        return Path(value)
    return Path.home() / fallback


def config_home() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / APP


def config_file() -> Path:
    return config_home() / "config.toml"


def state_home() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / APP


def log_dir(state_dir: Path | None = None) -> Path:
    return (state_dir or state_home()) / "log"


def cache_home() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / APP


def data_home() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / APP


def runtime_base() -> Path | None:
    """The user's runtime directory, or None when there is no usable one."""
    value = os.environ.get("XDG_RUNTIME_DIR", "")
    candidates = [Path(value)] if value and os.path.isabs(value) else []
    candidates.append(Path(f"/run/user/{os.getuid()}"))
    for base in candidates:
        try:
            st = base.stat()
        except OSError:
            continue
        if stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and os.access(base, os.W_OK | os.X_OK):
            return base
    return None


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
        raise RuntimeDirError(
            "no usable runtime directory: XDG_RUNTIME_DIR is unset or not writable "
            f"and /run/user/{os.getuid()} does not exist"
        )
    path = base / APP
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    try:
        st = path.lstat()
    except FileNotFoundError:
        if create:
            raise
        return path
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise RuntimeDirError(f"{path} is not a directory owned by this user")
    if create and stat.S_IMODE(st.st_mode) != 0o700:
        os.chmod(path, 0o700)
    return path


def socket_path() -> Path:
    """Where the daemon listens for local MCP sessions."""
    override = os.environ.get("PCBRIDGE_SOCKET", "")
    if override:
        return Path(override)
    return runtime_dir() / "mcp.sock"
