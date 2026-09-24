"""The desktop grant for people: read its state, lock it, open it.

`pcbridge status`, `lock`, `unlock` and the terminal UI all go through
here, so the grant has one definition outside the MCP tools. The state comes
from the same lease file the gate uses (`desktop/lease.py`), under its lock.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


class GrantError(Exception):
    """The grant could not be changed; the message says why, in English."""


@dataclass(frozen=True)
class GrantState:
    open: bool
    seconds_left: int            # until the grant closes if nothing else happens
    enabled_in_config: bool      # [desktop] enabled
    granted_by: str = ""

    def describe(self) -> str:
        if not self.enabled_in_config:
            return "disabled in config"
        if not self.open:
            return "locked"
        return f"open, {format_duration(self.seconds_left)} left"


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def read_state(cfg: Any, now: float | None = None) -> GrantState:
    """The grant as the gate sees it. Never raises; an unreadable file is closed."""
    from ..desktop.lease import LeaseStore

    moment = time.time() if now is None else now
    try:
        snap = LeaseStore(cfg.state_dir).snapshot()
    except OSError:
        return GrantState(False, 0, bool(cfg.desktop.enabled))
    active = snap.is_active(moment)
    left = int(snap.until - moment) if active else 0
    by = snap.raw.get("granted_by", "") if active else ""
    return GrantState(active, max(0, left), bool(cfg.desktop.enabled), str(by or ""))


def lock(cfg: Any) -> str:
    """Close the grant and stop every screen share, whichever process opened it."""
    from . import runtime_of

    runtime = runtime_of(cfg)
    try:
        out = runtime.gate.lock()
        # The screen share is a separate resource: its handle lives in the
        # process that opened it, so the provider finds and stops its helpers.
        killed = runtime.capture_provider.kill_helpers()
        if killed:
            out += (f"\n· {killed} screen share(s) stopped "
                    "(the sharing indicator is gone)")
        return out
    finally:
        runtime.close()


def unlock(cfg: Any, minutes: int | None = None, reason: str = "",
           granted_by: str = "pcbridge unlock") -> str:
    """Open the grant for `minutes` (the config's default when None)."""
    if not cfg.desktop.enabled:
        raise GrantError(
            f"desktop control is disabled in {cfg.source_path} ([desktop] enabled = false)"
        )
    from . import runtime_of

    runtime = runtime_of(cfg)
    try:
        return runtime.gate.unlock(minutes, reason, granted_by=granted_by)
    finally:
        runtime.close()
