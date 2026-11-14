"""Reading the audit log for the terminal UI, newest first.

The log is one JSON line per action (`audit.log`, rotated once to
`audit.log.1` at `[limits] audit_max_bytes`). The view reads from the end
of the file backward, a block at a time, so a full log never slows the
screen. Nothing here writes; contents were never recorded, so nothing here
can show them.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

BLOCK = 64 * 1024

KINDS = ("desktop", "shell", "file", "agent", "remote")
KIND_LABELS = {"desktop": "Desktop", "shell": "Shell", "file": "File", "agent": "Agent",
               "remote": "Remote access"}

_REMOTE = {"token_issued", "authorize_started", "code_issued", "consent_granted",
           "client_registered", "password_failed", "remote_client_revoked", "tokens_hashed",
           "login_failed", "token_revoked"}
_AGENT = {"agent_run", "job_cancel", "computer_task"}


@dataclass
class Entry:
    raw: str
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def event(self) -> str:
        return str(self.data.get("event") or "")

    @property
    def ts(self) -> str:
        return str(self.data.get("ts") or "")

    @property
    def when(self) -> float:
        try:
            return time.mktime(time.strptime(self.ts, "%Y-%m-%dT%H:%M:%S"))
        except (ValueError, OverflowError):
            return 0.0

    @property
    def client(self) -> str:
        return str(self.data.get("client") or self.data.get("client_name") or "")

    @property
    def kind(self) -> str:
        return kind_of(self.event)

    @property
    def tool(self) -> str:
        """The tool the entry belongs to: `ui_click` for `ui_click_denied`."""
        ev = self.event
        for suffix in ("_denied", "_refused", "_error", "_failed", "_unavailable", "_start", "_step"):
            if ev.endswith(suffix):
                return ev[: -len(suffix)]
        return ev

    @property
    def result(self) -> str:
        return result_of(self.event, self.data)

    @property
    def failed(self) -> bool:
        return self.result in ("refused", "failed")

    def what(self) -> str:
        return summary_of(self.event, self.data)


def kind_of(event: str) -> str:
    if event in _REMOTE:
        return "remote"
    if event in _AGENT or event.startswith("agent_") or event.startswith("computer_task"):
        return "agent"
    if event.startswith(("shell_", "tmux_", "job_")):
        return "shell"
    if event.startswith("fs_"):
        return "file"
    return "desktop"


def result_of(event: str, d: dict[str, Any]) -> str:
    if event.endswith(("_denied", "_refused")) or event in ("password_failed",):
        return "refused"
    if event.endswith(("_error", "_failed", "_unavailable")):
        return "failed"
    if "exit" in d and d.get("exit") is not None:
        return f"exit {d['exit']}"
    if d.get("stopped"):
        return "stopped"
    if d.get("ok") is False:
        return "failed"
    return "ok"


def _short(value: Any, width: int = 48) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= width else text[: width - 1] + "…"


def summary_of(event: str, d: dict[str, Any]) -> str:
    """A short, content-free summary from the entry's own fields."""
    if "reason" in d and result_of(event, d) == "refused":
        return _short(d["reason"])
    if "error" in d:
        return _short(d["error"])
    if event.startswith("shell_run") and "cmd" in d:
        return _short(d["cmd"])
    if "path" in d:
        return _short(d["path"])
    if event in ("agent_run", "computer_task"):
        bits = [str(d.get("agent") or ""), str(d.get("model") or ""), str(d.get("effort") or "")]
        return _short(" · ".join(b for b in bits if b) + (f" · job {d['job']}" if d.get("job") else ""))
    if event == "desktop_unlock":
        return f"{d.get('minutes', '?')} min" + (f" · {_short(d['granted_by'], 24)}" if d.get("granted_by") else "")
    if event == "mouse":
        where = f"{d.get('x')},{d.get('y')}" if d.get("x") is not None else ""
        return " ".join(str(b) for b in (d.get("action"), d.get("button"), where) if b)
    if event == "keyboard":
        if d.get("keys"):
            return _short(f"{d.get('action', '')} {d['keys']}")
        return f"{d.get('action', '')} · {d.get('chars', 0)} chars"
    if event == "screen_capture":
        return " · ".join(str(b) for b in (f"monitor {d['monitor']}" if d.get("monitor") is not None else "",
                                            d.get("backend")) if b)
    if event in ("computer_batch", "pcb_do"):
        return f"{d.get('done', '?')} of {d.get('total', '?')} steps"
    if event == "computer_batch_start":
        return f"{d.get('count', '?')} steps"
    if event == "batch_step":
        return _short(f"step {d.get('i')} · {d.get('a')}")
    if event.startswith("tmux_") and "session" in d:
        return _short(d["session"])
    if event == "client_registered":
        return _short(d.get("client_name") or d.get("client_id") or "")
    if event == "remote_client_revoked":
        return _short(", ".join(d.get("names") or []))
    if "target" in d:
        return _short(d["target"])
    if "client_id" in d:
        return _short(d["client_id"], 20)
    return ""


def _lines_backward(path: Path) -> Iterator[str]:
    """The file's lines from the last to the first, read a block at a time."""
    try:
        fh = path.open("rb")
    except OSError:
        return
    with fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        rest = b""
        while pos > 0:
            step = min(BLOCK, pos)
            pos -= step
            fh.seek(pos)
            chunk = fh.read(step) + rest
            lines = chunk.split(b"\n")
            rest = lines.pop(0)
            for line in reversed(lines):
                if line.strip():
                    yield line.decode("utf-8", "replace")
        if rest.strip():
            yield rest.decode("utf-8", "replace")


def newest(path: Path) -> Iterator[Entry]:
    """Every entry, newest first: the log, then its rotated `.1` copy."""
    for p in (Path(path), Path(f"{path}.1")):
        for line in _lines_backward(p):
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict):
                yield Entry(line, data)


def page(path: Path, skip: int, count: int, keep=None) -> tuple[list[Entry], bool]:
    """`count` entries after skipping `skip` matching ones, newest first; and
    whether older ones remain. `keep(entry)` filters."""
    out: list[Entry] = []
    seen = 0
    for e in newest(path):
        if keep is not None and not keep(e):
            continue
        if seen < skip:
            seen += 1
            continue
        if len(out) == count:
            return out, True
        out.append(e)
    return out, False


@dataclass
class Totals:
    entries: int
    first: str
    size: int


def totals(path: Path) -> Totals:
    """How many entries there are and since when; counted in a worker."""
    count, first, size = 0, "", 0
    for p in (Path(f"{path}.1"), Path(path)):
        try:
            size += p.stat().st_size if p == Path(path) else 0
            with p.open("rb") as fh:
                head = fh.readline()
                fh.seek(0)
                count += sum(chunk.count(b"\n") for chunk in iter(lambda: fh.read(BLOCK), b""))
        except OSError:
            continue
        if not first:
            try:
                first = str(json.loads(head).get("ts") or "")[:10]
            except ValueError:
                pass
    return Totals(count, first, size)


def logo_state(entries: list[Entry], jobs_running: int, now: float | None = None) -> tuple[str, Entry | None]:
    """idle, active or error from the newest entries (newest first) and the jobs.

    Active: a job runs, or a tool call happened in the last 3 s. Error: the
    last tool call failed or was refused; it clears 60 s after that error.
    """
    now = time.time() if now is None else now
    calls = [e for e in entries if e.kind != "remote" and e.client]
    last = calls[0] if calls else None
    if last is not None and last.failed and now - last.when <= 60:
        return "error", last
    if jobs_running or (last is not None and now - last.when <= 3):
        return "active", last
    return "idle", last
