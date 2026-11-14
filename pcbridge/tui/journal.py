"""The daemon's journal for the Logs tab: read, parse, follow.

`journalctl --user -u pcbridge.service -o json` gives one JSON object per
line. The daemon logs through Python's logging, which journald stores at
priority 6 whatever the level, so the level is read from the message
(`2026-11-14 11:17:50,346 WARNING name: text` or `ERROR: text`) and the
priority only fills in when the message has none.
"""

from __future__ import annotations

import json
import os
import re
import select
import subprocess
from dataclasses import dataclass
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


def read(lines: int = 300, before: str = "") -> list[Line]:
    """The last `lines` of this boot (or the ones before the cursor `before`)."""
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
