"""The one writer of the audit log (`<state_dir>/audit.log`).

Every part of pcbridge that records an action (the desktop gate, the OAuth
provider, remote revocation, the terminal UI) appends through `write_line`,
so the rotation at `[limits] audit_max_bytes` holds for all of them: when
the file reaches the limit it moves to `audit.log.1` (one copy, replaced)
and a new file starts. The log is an audit trail, not an archive.

What is recorded is what was done, never the content: the command but not
its output, the path but not the file, a length but not the text.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def record(event: str, fields: dict[str, Any], drop_none: bool = False) -> str:
    """One audit line: the time, the event, then the fields."""
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event,
           **{k: v for k, v in fields.items() if not (drop_none and v is None)}}
    return json.dumps(rec, ensure_ascii=False)


def rotate(path: Path, max_bytes: int) -> None:
    """Move the log to `.1` once it reached `max_bytes`; 0 turns rotation off."""
    if max_bytes <= 0:
        return
    try:
        if path.exists() and path.stat().st_size >= max_bytes:
            path.replace(path.with_suffix(path.suffix + ".1"))
    except OSError:  # pragma: no cover - a full or read-only disk
        pass


def write_line(path: Path | str, line: str, max_bytes: int = 0) -> None:
    """Append one line, rotating first. A new file is readable by the user only."""
    path = Path(path)
    rotate(path, int(max_bytes or 0))
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:  # pragma: no cover - auditing never breaks the action
        pass


def append(cfg: Any, event: str, **fields: Any) -> str:
    """Record `event` in the config's audit log; returns the line written."""
    line = record(event, fields, drop_none=True)
    write_line(cfg.audit_log, line, int(getattr(cfg, "audit_max_bytes", 0) or 0))
    return line
