"""Idle time from the session-bound record `pcbridge-native idle-watch` keeps.

KWin does not answer `GetSessionIdleTime` on Wayland (measured on Plasma
6.7.5, docs/dev/measured-facts.md); only a Wayland client holding an
`ext_idle_notifier_v1` notification learns when input stops and resumes.
The native helper has a mode for exactly that. The daemon runs it on Plasma
and Hyprland (`supervise`), keeping `$XDG_RUNTIME_DIR/pcbridge/idle.json` current.

The version-2 record includes the Wayland display, Hyprland instance when
applicable, process start ticks, and a compositor-confirmed heartbeat.

While idle, `since_unix_ms` conservatively approximates the last input.
While active the last input is known to be under a second ago, and the idle
time reads as 0: the safe side for a gate that refuses writes while someone
is at the machine. Missing, stale, dead-writer, or foreign-session records
are UNKNOWN. The shared safety layer decides whether a call may act.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from .. import paths as pathslib

log = logging.getLogger("pcbridge")

STATE_FILE = "idle.json"
RECORD_VERSION = 2
MAX_RECORD_AGE_MS = 3000
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


def _writer_start_ticks(pid: int) -> int | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return int(stat.rsplit(")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def read_idle_ms(path: Path | None = None, now_ms: int | None = None) -> int | None:
    """Milliseconds since the last input, or None when there is no trusted record."""
    path = path or state_path()
    if path is None:
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        version, pid, idle, since = (record["version"], record["pid"],
                                     record["idle"], record["since_unix_ms"])
        heartbeat = record["confirmed_unix_ms"]
        start = record["writer_start_ticks"]
        display = record["wayland_display"]
        signature = record["hyprland_instance"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if (type(version) is not int or version != RECORD_VERSION
            or any(type(value) is not int or value < 0 for value in (pid, since, heartbeat, start))
            or pid == 0 or start == 0 or type(idle) is not bool
            or type(record.get("timeout_ms")) is not int or record["timeout_ms"] != 1000
            or not isinstance(display, str) or not display
            or display != os.environ.get("WAYLAND_DISPLAY", "")
            or not isinstance(signature, str)
            or signature != os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
            or not 0 <= now - heartbeat <= MAX_RECORD_AGE_MS
            or since > heartbeat):
        return None
    if not _writer_alive(pid) or _writer_start_ticks(pid) != start:
        return None
    if idle is not True:
        return 0
    return max(0, now - since)


async def supervise(binary: Path) -> None:
    """Keep `binary idle-watch` running while the compositor needs it."""
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
