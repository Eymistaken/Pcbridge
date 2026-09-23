"""KDE Plasma's idle time, from the record `pcbridge-native idle-watch` keeps.

KWin does not answer `GetSessionIdleTime` on Wayland (measured on Plasma
6.7.5, docs/dev/measured-facts.md); only a Wayland client holding an
`ext_idle_notifier_v1` notification learns when input stops and resumes.
The native helper has a mode for exactly that. The daemon runs it on Plasma
(`supervise`), and it keeps `$XDG_RUNTIME_DIR/pcbridge/idle.json` current:

    {"version": 1, "pid": 1234, "timeout_ms": 1000, "idle": true,
     "since_unix_ms": 1790174152180}

While idle, `since_unix_ms` is the last input, so the idle time is exact.
While active the last input is known to be under a second ago, and the idle
time reads as 0: the safe side for a gate that refuses writes while someone
is at the machine. A record whose writer is not a running watcher is not
trusted, and the idle time is then unknown (writes need force), which is
what every other unreadable idle source gives.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .. import paths as pathslib

log = logging.getLogger("pcbridge")

STATE_FILE = "idle.json"
RECORD_VERSION = 1
# The helper's argument; also how a record's writer is recognized.
WATCH_ARGUMENT = "idle-watch"
# Restart delays after the watcher exits: a compositor restart or a logout
# ends it, and a helper that cannot run should not be retried in a loop.
_BACKOFF_S = (2.0, 5.0, 15.0, 60.0)


def state_path() -> Path | None:
    base = pathslib.runtime_base()
    return base / "pcbridge" / STATE_FILE if base else None


def _writer_alive(pid: int) -> bool:
    try:
        raw = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
    except (OSError, ValueError):
        return False
    return WATCH_ARGUMENT.encode() in raw.split(b"\0")


def read_idle_ms(path: Path | None = None, now_ms: int | None = None) -> int | None:
    """Milliseconds since the last input, or None when there is no trusted record."""
    path = path or state_path()
    if path is None:
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        version, pid, idle, since = (record["version"], record["pid"],
                                     record["idle"], record["since_unix_ms"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if version != RECORD_VERSION or not isinstance(pid, int) or not isinstance(since, int):
        return None
    if not _writer_alive(pid):
        return None
    if idle is not True:
        return 0
    now = int(time.time() * 1000) if now_ms is None else now_ms
    return max(0, now - since)


async def supervise(binary: Path) -> None:
    """Keep `binary idle-watch` running for the daemon's lifetime (Plasma only)."""
    import anyio

    failures = 0
    while True:
        started = time.monotonic()
        try:
            proc = await anyio.open_process([str(binary), WATCH_ARGUMENT])
        except OSError as exc:
            log.warning("idle watcher did not start: %s", exc)
            code: int | None = None
        else:
            try:
                code = await proc.wait()
                stderr = (await proc.stderr.receive()).decode(errors="replace").strip() \
                    if proc.stderr else ""
            except anyio.EndOfStream:
                stderr = ""
            except BaseException:
                proc.kill()
                with anyio.CancelScope(shield=True):
                    await proc.wait()
                raise
            log.warning("idle watcher exited (%s): %s", code, stderr[:300])
        failures = 0 if time.monotonic() - started > 300 else failures + 1
        await anyio.sleep(_BACKOFF_S[min(failures, len(_BACKOFF_S) - 1)])
