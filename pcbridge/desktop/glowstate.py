"""Read only fresh native presentation evidence for the exact desktop grant."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import time

from .idlewatch import _writer_start_ticks
from .lease import LeaseToken

STATE_FILE = "glow.json"
MAX_AGE_MS = 1000
MAX_RECORD_BYTES = 8192


def matches(record: dict, token: LeaseToken, *, display: str, signature: str, now_ms: int) -> bool:
    numbers = ("version", "pid", "writer_start_ticks", "owner_pid", "owner_start_ticks",
               "revoke_epoch", "strip_count", "presented_unix_ms")
    if any(type(record.get(name)) is not int or record[name] < 0 for name in numbers):
        return False
    outputs = record.get("outputs")
    if (not isinstance(outputs, list) or not 1 <= len(outputs) <= 16
            or any(not isinstance(name, str) or not name or len(name) > 256 for name in outputs)
            or len(set(outputs)) != len(outputs)):
        return False
    return (record["version"] == 2 and record.get("ready") is True
            and all(record[name] > 0 for name in ("pid", "writer_start_ticks", "owner_pid", "owner_start_ticks"))
            and record.get("grant_id") == token.grant_id and record["revoke_epoch"] == token.revoke_epoch
            and bool(display) and record.get("wayland_display") == display
            and bool(signature) and record.get("hyprland_instance") == signature
            and isinstance(record.get("topology_id"), str) and record["topology_id"].startswith("v1|")
            and len(record["topology_id"]) <= 2048
            and 4 <= record["strip_count"] <= 64 and record["strip_count"] % 4 == 0
            and record["strip_count"] == 4 * len(outputs)
            and 0 <= now_ms - record["presented_unix_ms"] <= MAX_AGE_MS)


def covers_outputs(record: dict, table) -> bool:
    from .monitors import topology_id

    return (record.get("topology_id") == topology_id(table)
            and record.get("outputs") == [m.connector for m in table]
            and record.get("strip_count") == 4 * len(table))


def read_on_current_outputs(directory: Path, token: LeaseToken, *, binary: Path) -> dict | None:
    from . import monitors

    try:
        table = monitors.list_monitors(use_cache=False)
    except monitors.MonitorError:
        return None
    # Read proof after the bounded query: a slow compositor cannot make an
    # earlier timestamp count as fresh at protected-operation admission.
    record = read(directory, token, binary=binary)
    return record if record is not None and covers_outputs(record, table) else None


def _writer_matches_binary(pid: int, directory: Path, binary: Path, token: LeaseToken) -> bool:
    try:
        arguments = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        actual = Path(f"/proc/{pid}/exe").stat()
        expected = binary.stat()
        with Path(f"/proc/{pid}/environ").open("rb") as stream:
            environment = stream.read(65537)
        if len(environment) > 65536:
            return False
        entries = environment.split(b"\0")
        return (len(arguments) >= 5 and arguments[1] == b"glow-watch"
                and arguments[2] == os.fsencode(directory)
                and arguments[3] == token.grant_id.encode()
                and arguments[4] == str(token.revoke_epoch).encode()
                and os.fsencode("WAYLAND_DISPLAY=" + os.environ.get("WAYLAND_DISPLAY", "")) in entries
                and os.fsencode("HYPRLAND_INSTANCE_SIGNATURE=" + os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")) in entries
                and (actual.st_dev, actual.st_ino, actual.st_uid) == (expected.st_dev, expected.st_ino, expected.st_uid))
    except OSError:
        return False


def _parent_pid(pid: int) -> int | None:
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def read(directory: Path, token: LeaseToken, *, binary: Path, now_ms: int | None = None) -> dict | None:
    """None means frame health is unavailable, never permission to act."""
    directory = directory.resolve()
    try:
        fd = os.open(directory / STATE_FILE, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                return None
            raw = stream.read(MAX_RECORD_BYTES + 1)
        if len(raw) > MAX_RECORD_BYTES:
            return None
        record = json.loads(raw)
        if not isinstance(record, dict) or not matches(record, token,
                display=os.environ.get("WAYLAND_DISPLAY", ""),
                signature=os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""),
                now_ms=int(time.time() * 1000) if now_ms is None else now_ms):
            return None
        if (_writer_start_ticks(record["pid"]) != record["writer_start_ticks"]
                or _writer_start_ticks(record["owner_pid"]) != record["owner_start_ticks"]
                or _parent_pid(record["pid"]) != record["owner_pid"]
                or Path(f'/proc/{record["pid"]}').stat().st_uid != os.getuid()
                or Path(f'/proc/{record["owner_pid"]}').stat().st_uid != os.getuid()
                or not _writer_matches_binary(record["pid"], directory, binary, token)):
            return None
        return record
    except (OSError, ValueError, TypeError):
        return None
