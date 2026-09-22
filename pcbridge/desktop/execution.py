"""One desktop write sequence at a time, re-checked before every action.

WHY
    `SafetyGate.check()` answered once, at the start of a call. A forty-action
    `computer_batch` then ran on that single answer: `desktop_lock` from the
    phone stopped the NEXT call but not the rest of the running sequence, and a
    `pcb-do` started by the local agent could interleave its keystrokes with an
    MCP batch served by another process. Both processes passed the gate;
    nothing ordered their input.

WHAT THIS MODULE ADDS
    * `ExecutionLock` serializes write sequences across processes with an
      advisory `flock` beside the grant file. The kernel drops the lock with
      the holder's descriptor, so a crashed holder leaves nothing to clean up.
    * `ExecutionSlot.pace()` shares the actions-per-second window between
      those processes under the same lock. Without it a sequence starting
      right after another one could spend a whole second's allowance again.
    * `SequenceGuard` is the device-agnostic `before_action` hook of
      `batch.run`: grant identity, revoke, deadline and screen lock again
      before every action. User activity is NOT asked again -- our own uinput
      events reset `IdleMonitor` (measured 104227 ms -> 151 ms), so the second
      action would take the first one's keystroke for the user.

It knows no device and no MCP type; `pcb-do` imports it as well.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .errors import DesktopError, ErrorCategory, ErrorCode, error_from_decision

logger = logging.getLogger("pcbridge.desktop.execution")

EXECUTION_LOCK_FILE = "desktop_execution.lock"
EXECUTION_STATE_FILE = "desktop_execution.json"

# How long a write call waits for another sequence before answering BUSY.
# `computer_batch` deducts the wait from its budget, so waiting never pushes a
# call past the 110-second MCP ceiling.
EXECUTION_WAIT_SECONDS = 10.0

_POLL_SECONDS = 0.05
_RATE_WINDOW_SECONDS = 1.0
_MAX_STATE_BYTES = 16 * 1024


class SequenceRefused(DesktopError):
    """The execution layer refused before anything was sent.

    A distinct type so a tool can tell "this call never touched the desktop"
    apart from a provider failure in the middle of an action.
    """

    @classmethod
    def from_error(cls, error: DesktopError) -> SequenceRefused:
        return cls(
            code=error.code,
            message=error.message,
            category=error.category,
            retryable=error.retryable,
            suggested_action=error.suggested_action,
            permission_scope=error.permission_scope,
            backend=error.backend,
            execution_state=error.execution_state,
        )


def _busy(holder: Any, waited: float, now: float) -> SequenceRefused:
    who = ""
    if isinstance(holder, dict):
        parts = [str(holder.get("tool") or "?")[:40]]
        pid = holder.get("pid")
        if isinstance(pid, int) and not isinstance(pid, bool):
            parts.append(f"pid {pid}")
        since = holder.get("since")
        if isinstance(since, (int, float)) and not isinstance(since, bool):
            parts.append(f"for {max(0, int(now - since))} s")
        who = f" ({', '.join(parts)})"
    return SequenceRefused(
        code=ErrorCode.BUSY,
        message=(
            f"Another desktop action sequence is running{who}. Only one runs at a "
            "time so that the input of two sequences never mixes; waited "
            f"{waited:.0f} seconds. Try again when it has finished."
        ),
        category=ErrorCategory.EXECUTION,
        retryable=True,
        suggested_action="Wait for the running desktop sequence to finish, then retry.",
        permission_scope="pcbridge.desktop",
        backend="desktop.execution",
    )


def _window(values: Any, now: float) -> list[float]:
    """Timestamps inside the last second, oldest first.

    A timestamp in the future means the wall clock stepped back. It is dropped
    rather than blocking every later action until the clock catches up.
    """
    if not isinstance(values, list):
        return []
    kept = [
        float(value)
        for value in values
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and now - _RATE_WINDOW_SECONDS < float(value) <= now
    ]
    return sorted(kept)


class ExecutionLock:
    """Cross-process lock for desktop write sequences, kept in `state_dir`."""

    def __init__(
        self,
        state_dir: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.lock_path = self.state_dir / EXECUTION_LOCK_FILE
        self.state_path = self.state_dir / EXECUTION_STATE_FILE
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep

    @contextmanager
    def hold(
        self, tool: str, *, timeout: float = EXECUTION_WAIT_SECONDS
    ) -> Iterator[ExecutionSlot]:
        """Hold the lock for one sequence, or raise `SequenceRefused` (BUSY)."""
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        # O_CLOEXEC matters: a job started while the lock is held must not
        # inherit the descriptor and keep the desktop locked after we exit.
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.lock_path, flags, 0o600)
        try:
            started = self._monotonic()
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    waited = self._monotonic() - started
                    if waited >= timeout:
                        holder = self._read_state().get("holder")
                        raise _busy(holder, waited, self._clock()) from None
                    self._sleep(min(_POLL_SECONDS, max(0.0, timeout - waited)))
            slot = ExecutionSlot(self, waited=self._monotonic() - started)
            slot._set_holder({"tool": tool, "pid": os.getpid(), "since": self._clock()})
            try:
                yield slot
            finally:
                slot._set_holder(None)
        finally:
            os.close(descriptor)

    def _read_state(self) -> dict[str, Any]:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.state_path, flags)
        except OSError:
            return {}
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            raw = stream.read(_MAX_STATE_BYTES + 1)
        if len(raw) > _MAX_STATE_BYTES:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write_state(self, data: dict[str, Any]) -> None:
        payload = json.dumps(data, separators=(",", ":")).encode("utf-8")
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f".{EXECUTION_STATE_FILE}.", suffix=".tmp", dir=self.state_dir
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor = -1
                stream.write(payload)
            os.replace(raw_path, self.state_path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(raw_path)
            except FileNotFoundError:
                pass


class ExecutionSlot:
    """Proof that the execution lock is held; valid only inside `hold()`."""

    def __init__(self, lock: ExecutionLock, *, waited: float) -> None:
        self._lock = lock
        self.waited = waited

    def pace(self, limit: int) -> float:
        """Count one action against the shared per-second window.

        Waits first when `limit` actions already started within the last
        second, in this process or in the one that held the lock before it.
        Returns the seconds waited.
        """
        if limit <= 0:
            return 0.0
        state = self._lock._read_state()
        now = self._lock._clock()
        recent = _window(state.get("recent"), now)
        waited = 0.0
        if len(recent) >= limit:
            waited = max(0.0, recent[-limit] + _RATE_WINDOW_SECONDS - now)
            if waited > 0:
                self._lock._sleep(waited)
                now = self._lock._clock()
                recent = _window(recent, now)
        recent.append(now)
        state["recent"] = recent[-limit:]
        self._write(state)
        return waited

    def _set_holder(self, holder: dict[str, Any] | None) -> None:
        state = self._lock._read_state()
        state["holder"] = holder
        self._write(state)

    def _write(self, state: dict[str, Any]) -> None:
        # The holder and the rate window are bookkeeping. Failing to write them
        # must not strand a sequence that already holds the lock; the lock
        # itself lives in the kernel, not in this file.
        try:
            self._lock._write_state(state)
        except OSError as exc:
            logger.warning("desktop execution state not written: %s", exc)


class SequenceGuard:
    """Re-check one admitted write sequence before each action it sends.

    `verify` returns a `SafetyGate` decision for the grant captured when the
    call was admitted. The guard is `batch.run`'s `before_action` hook, and a
    single-action tool calls it once.
    """

    def __init__(
        self,
        verify: Callable[[], Any],
        slot: ExecutionSlot | None,
        *,
        rate_limit: int = 0,
    ) -> None:
        self._verify = verify
        self._slot = slot
        self._rate_limit = int(rate_limit or 0)
        self.checks = 0

    @property
    def waited(self) -> float:
        """Seconds spent waiting for the lock; callers deduct it from budgets."""
        return self._slot.waited if self._slot is not None else 0.0

    def admit(self) -> None:
        """Re-check once the lock is held: the wait may have outlived the grant."""
        self._check()

    def __call__(self, action: Any) -> None:
        # A wait sends nothing, so it neither needs the check nor counts
        # against the rate window.
        if getattr(action, "a", action) == "wait":
            return
        self._check()
        if self._slot is not None:
            self._slot.pace(self._rate_limit)

    def _check(self) -> None:
        self.checks += 1
        decision = self._verify()
        if not getattr(decision, "allowed", False):
            raise SequenceRefused.from_error(error_from_decision(decision))


__all__ = [
    "EXECUTION_LOCK_FILE",
    "EXECUTION_STATE_FILE",
    "EXECUTION_WAIT_SECONDS",
    "ExecutionLock",
    "ExecutionSlot",
    "SequenceGuard",
    "SequenceRefused",
]
