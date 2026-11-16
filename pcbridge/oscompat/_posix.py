"""POSIX (Linux) implementations: the code pcbridge always ran, in one place."""

from __future__ import annotations

import fcntl
import os
import signal
import stat
from pathlib import Path
from typing import NoReturn


def lock_fd(fd: int, *, exclusive: bool = True, blocking: bool = True) -> None:
    """Lock an open file. Raises BlockingIOError when not blocking and held."""
    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    if not blocking:
        operation |= fcntl.LOCK_NB
    fcntl.flock(fd, operation)


def unlock_fd(fd: int) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)


def restrict_fd(fd: int) -> None:
    """Make an open file readable and writable by its owner only."""
    os.fchmod(fd, 0o600)


def restrict_path(path: str | os.PathLike[str], *, directory: bool = False) -> None:
    """Mode 0700 for a directory, 0600 for a file."""
    os.chmod(path, 0o700 if directory else 0o600)


def private_problem(path: str | os.PathLike[str]) -> str | None:
    """None when only the owner can read `path`, else what is wrong ("mode 644")."""
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return None
    return f"mode {mode:o}" if mode & 0o077 else None


def fsync_directory(path: str | os.PathLike[str]) -> None:
    """Flush a directory entry change (an atomic rename) to disk."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def owned_by_me(path: str | os.PathLike[str] | os.stat_result) -> bool:
    info = path if isinstance(path, os.stat_result) else os.lstat(path)
    return info.st_uid == os.getuid()


def is_link_or_reparse(path: str | os.PathLike[str]) -> bool:
    try:
        return stat.S_ISLNK(os.lstat(path).st_mode)
    except FileNotFoundError:
        return False


def current_user() -> str:
    import pwd

    return pwd.getpwuid(os.getuid()).pw_name


def pid_alive(pid: int) -> bool:
    """Whether the process runs. A zombie does not: it has exited and only
    waits for its parent to reap it, which may never happen when it was
    reparented to an init that does not reap (a container's PID 1)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return not _is_zombie(pid)


def _is_zombie(pid: int) -> bool:
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    state = raw[raw.rfind(")") + 2 : raw.rfind(")") + 3]
    return state in ("Z", "X")


def process_start_identity(pid: int) -> str:
    """Return Linux process start ticks, which do not repeat when a PID is reused."""
    raw = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
    closing = raw.rfind(")")
    if closing < 0:
        raise ValueError("invalid /proc process stat")
    fields = raw[closing + 2 :].split()
    if len(fields) <= 19 or not fields[19].isdigit():
        raise ValueError("process start identity is unavailable")
    return fields[19]


def process_started_at(pid: int) -> float | None:
    """When a process started (epoch seconds), from /proc; None if unknown."""
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as stream:
            ticks = int(stream.read().rsplit(")", 1)[1].split()[19])
        with open("/proc/stat", encoding="ascii") as stream:
            boot = next(float(line.split()[1]) for line in stream if line.startswith("btime"))
    except (OSError, ValueError, IndexError, StopIteration):
        return None
    return boot + ticks / os.sysconf("SC_CLK_TCK")


def process_owned_by_me(pid: int) -> bool:
    try:
        return Path(f"/proc/{int(pid)}").stat().st_uid == os.getuid()
    except OSError:
        return False


def terminate_group(pid: int, *, force: bool = False, own_group: bool = False) -> None:
    """Signal the process group of `pid`: SIGTERM, or SIGKILL with force.

    `own_group` says `pid` leads its group (it was started with a new
    session), so its id is the group id even after it has exited.
    """
    group = pid if own_group else os.getpgid(pid)
    os.killpg(group, signal.SIGKILL if force else signal.SIGTERM)


def exec_replace(argv: list[str]) -> NoReturn:
    os.execv(argv[0], argv)
