"""The daemon as a per-user Windows service: what systemctl does on Linux.

    enable / disable   the HKCU Run value that starts the supervisor at logon
                       (no administrator needed; Task Manager's Startup apps
                       lists it, and the user can turn it off there)
    start              start the supervisor now, detached, and wait until the
                       daemon answers on its endpoint
    stop               ask the daemon to stop over the local channel; the
                       supervisor ends with it
    status             whether the daemon answers, the supervisor runs, and
                       the Run value is set

Not a Windows service: services run in session 0, which cannot see the
user's desktop, and the desktop tools need it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import localipc, oscompat
from . import paths as pathslib
from .supervisor import LOCK_FILE, PID_FILE

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "pcbridge"
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000


def supervisor_command() -> list[str]:
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return [str(windowless if windowless.exists() else exe), "-P", "-m", "pcbridge.supervisor"]


def is_enabled() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except OSError:
        return False


def enable() -> None:
    import winreg

    command = subprocess.list2cmdline(supervisor_command())
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)


def disable() -> None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_VALUE)
    except FileNotFoundError:
        pass


def supervisor_pid() -> int | None:
    """The running supervisor's pid, or None when none holds the lock."""
    try:
        runtime = pathslib.runtime_dir()
    except pathslib.RuntimeDirError:
        return None
    lock_path = runtime / LOCK_FILE
    if not lock_path.exists():
        return None
    fd = os.open(lock_path, os.O_RDWR)
    try:
        oscompat.lock_fd(fd, exclusive=True, blocking=False)
    except BlockingIOError:
        try:
            return int((runtime / PID_FILE).read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return -1
    else:
        oscompat.unlock_fd(fd)
        return None
    finally:
        os.close(fd)


def daemon_answers(timeout: float = 1.0) -> bool:
    try:
        localipc.connect(pathslib.socket_path(), timeout=timeout).close()
        return True
    except (OSError, pathslib.RuntimeDirError):
        return False


def start(wait: float = 20.0) -> bool:
    """Start the supervisor unless one runs; True once the daemon answers."""
    if supervisor_pid() is None:
        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        # The service's environment, not the caller's: on Linux systemd's
        # user manager starts the daemon, and a client's PCBRIDGE_SOCKET or
        # PCBRIDGE_CONFIG never reaches it (measured: a test relay's socket
        # override made the daemon listen at the test's path).
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PCBRIDGE_")}
        common = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      cwd=str(Path.home()), close_fds=True, env=env)
        try:
            # Out of the caller's job object: an MCP client or an SSH session
            # that ends must not take the supervisor with it.
            subprocess.Popen(supervisor_command(), creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **common)
        except OSError:
            subprocess.Popen(supervisor_command(), creationflags=flags, **common)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if daemon_answers(0.5):
            return True
        time.sleep(0.25)
    return False


def stop(wait: float = 20.0) -> bool:
    """Ask the daemon to stop; True once neither it nor the supervisor runs."""
    try:
        sock = localipc.connect(pathslib.socket_path(), timeout=3.0)
        with sock:
            sock.sendall(json.dumps({"pcbridge_control": "stop"}).encode() + b"\n")
            sock.settimeout(5.0)
            sock.recv(4096)
    except (OSError, pathslib.RuntimeDirError):
        pass
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if supervisor_pid() is None and not daemon_answers(0.3):
            return True
        time.sleep(0.25)
    pid = supervisor_pid()
    if pid and pid > 0:
        try:
            oscompat.terminate_group(pid, force=True)
        except OSError:
            pass
    return supervisor_pid() is None and not daemon_answers(0.3)


def status() -> dict:
    pid = supervisor_pid()
    return {"enabled": is_enabled(), "supervisor_pid": pid, "daemon": daemon_answers()}
