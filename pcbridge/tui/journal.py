"""The daemon's journal for the Logs tab: read, parse, follow.

`journalctl --user -u pcbridge.service -o json` gives one JSON object per
line. The daemon logs through Python's logging, which journald stores at
priority 6 whatever the level, so the level is read from the message
(`2026-11-14 11:17:50,346 WARNING name: text` or `ERROR: text`) and the
priority only fills in when the message has none.

On Windows there is no journal: the supervisor writes the daemon's output to
<state>/log/daemon.log, and the same three functions read that file. Its
cursor is a line's byte offset ("file:<offset>"); a line without the
timestamp prefix (a traceback's continuation) takes the time and level of
the line before it.
"""

from __future__ import annotations

import json
import os
import re
import select
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

UNITS = ["-u", "pcbridge.service", "-u", "pcbridge.socket"]
LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")

_PREFIX = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d[,.]\d+\s+(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\s+")
_SHORT = re.compile(r"^(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL):\s+")


@dataclass(frozen=True)
class Line:
    ts: float          # epoch seconds
    level: str         # DEBUG | INFO | WARN | ERROR
    message: str
    cursor: str = ""


def _level(name: str) -> str:
    name = name.upper()
    if name in ("WARNING", "WARN"):
        return "WARN"
    if name in ("ERROR", "CRITICAL"):
        return "ERROR"
    return name if name in LEVELS else "INFO"


def _from_priority(priority: int) -> str:
    if priority <= 3:
        return "ERROR"
    if priority == 4:
        return "WARN"
    if priority == 7:
        return "DEBUG"
    return "INFO"


def parse(raw: str) -> Line | None:
    """One `journalctl -o json` line -> Line; None for what is not one."""
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    message = d.get("MESSAGE")
    if isinstance(message, list):  # journald stores non-UTF-8 text as bytes
        message = bytes(message).decode("utf-8", "replace")
    message = str(message or "")
    try:
        ts = int(d.get("__REALTIME_TIMESTAMP", 0)) / 1_000_000
    except (TypeError, ValueError):
        ts = 0.0
    m = _PREFIX.match(message) or _SHORT.match(message)
    if m:
        level = _level(m.group(1))
        message = message[m.end():]
    else:
        try:
            level = _from_priority(int(d.get("PRIORITY", 6)))
        except (TypeError, ValueError):
            level = "INFO"
    return Line(ts, level, message.rstrip(), str(d.get("__CURSOR") or ""))


_STAMP = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)[,.](\d+)")
_FILE_WINDOW = 4 * 1024 * 1024


def _log_file() -> Path:
    from ..supervisor import log_path

    return log_path()


def _offset(cursor: str) -> int | None:
    if cursor.startswith("file:"):
        try:
            return int(cursor[5:])
        except ValueError:
            return None
    return None


def _plain_lines(data: bytes, base: int, ts: float = 0.0, level: str = "INFO") -> list[Line]:
    """Complete lines of daemon.log starting at byte `base`."""
    out: list[Line] = []
    offset = base
    for raw in data.split(b"\n")[:-1]:
        text = raw.decode("utf-8", "replace").rstrip("\r")
        stamp = _STAMP.match(text)
        if stamp:
            try:
                ts = time.mktime(time.strptime(stamp.group(1), "%Y-%m-%d %H:%M:%S")) + int(stamp.group(2)[:3]) / 1000
            except ValueError:
                pass
        m = _PREFIX.match(text) or _SHORT.match(text)
        if m:
            level = _level(m.group(1))
            text = text[m.end():]
        out.append(Line(ts, level, text.rstrip(), f"file:{offset}"))
        offset += len(raw) + 1
    return out


def _read_file(lines: int, before: str) -> list[Line]:
    path = _log_file()
    try:
        size = path.stat().st_size
        end = _offset(before) if before else size
        end = size if end is None else min(end, size)
        start = max(0, end - _FILE_WINDOW)
        with open(path, "rb") as stream:
            stream.seek(start)
            data = stream.read(end - start)
    except OSError:
        return []
    if start:
        cut = data.find(b"\n") + 1  # drop the partial first line
        data, start = data[cut:], start + cut
    if data and not data.endswith(b"\n"):
        data = data[: data.rfind(b"\n") + 1]
    return _plain_lines(data, start)[-lines:]


def _follow_file(after: str) -> Iterator[Line | None]:
    path = _log_file()
    try:
        position = path.stat().st_size
    except OSError:
        position = 0
    start = _offset(after)
    skip_first = start is not None
    if start is not None:
        position = start
    pending = b""
    while True:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size < position:  # rotated: the new file starts from the top
            position, pending, skip_first = 0, b"", False
        if size == position:
            yield None
            time.sleep(0.5)
            continue
        with open(path, "rb") as stream:
            stream.seek(position)
            chunk = stream.read(size - position)
        base = position - len(pending)
        position = size
        data = pending + chunk
        cut = data.rfind(b"\n") + 1
        pending = data[cut:]
        for line in _plain_lines(data[:cut], base):
            if skip_first:
                skip_first = False
                continue
            yield line


def read(lines: int = 300, before: str = "") -> list[Line]:
    """The last `lines` of this boot (or the ones before the cursor `before`)."""
    if sys.platform == "win32":
        return _read_file(lines, before)
    cmd = ["journalctl", "--user", *UNITS, "-b", "-o", "json", "--no-pager", "-n", str(lines)]
    if before:
        cmd += ["--cursor", before, "--reverse"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    parsed = [p for p in (parse(raw) for raw in out.splitlines()) if p is not None]
    if before:
        parsed = [p for p in parsed if p.cursor != before][:lines]
        parsed.reverse()
    return parsed


def follow(after: str = "") -> Iterator[Line | None]:
    """New lines as they come (`journalctl -f`); yields None every half second
    without one, so the caller can stop. Closing the generator ends journalctl."""
    if sys.platform == "win32":
        yield from _follow_file(after)
        return
    cmd = ["journalctl", "--user", *UNITS, "-o", "json", "--no-pager", "-f", "-n", "0"]
    if after:
        cmd = ["journalctl", "--user", *UNITS, "-o", "json", "--no-pager", "-f", "--after-cursor", after]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    buffer = b""
    try:
        while True:
            ready, _, _ = select.select([proc.stdout], [], [], 0.5)
            if not ready:
                if proc.poll() is not None:
                    return
                yield None
                continue
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                return
            buffer += chunk
            *complete, buffer = buffer.split(b"\n")
            for raw in complete:
                line = parse(raw.decode("utf-8", "replace"))
                if line is not None:
                    yield line
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
