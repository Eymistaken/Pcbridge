"""Atomic, cross-process storage for the desktop authorization lease."""

from __future__ import annotations

import fcntl
import json
import math
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping


SCHEMA_VERSION = 1
LEASE_STATE_FILE = "desktop_unlock.json"
LEASE_LOCK_FILE = "desktop_unlock.lock"
_MAX_STATE_BYTES = 64 * 1024


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _nonnegative_integer(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


@dataclass(frozen=True)
class LeaseToken:
    """Identity captured when one operation passes the safety gate."""

    grant_id: str
    revoke_epoch: int


@dataclass(frozen=True)
class LeaseSnapshot:
    """Normalized read-only view while retaining the on-disk mapping."""

    raw: Mapping[str, Any]
    schema_version: int
    grant_id: str
    revoke_epoch: int
    until: float
    hard_until: float

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> LeaseSnapshot:
        return cls(
            raw=dict(raw),
            schema_version=_nonnegative_integer(raw.get("schema_version")),
            grant_id=(
                raw.get("grant_id") if isinstance(raw.get("grant_id"), str) else ""
            ),
            revoke_epoch=_nonnegative_integer(raw.get("revoke_epoch")),
            until=_number(raw.get("until")),
            hard_until=_number(raw.get("hard_until")),
        )

    def is_active(self, now: float | None = None) -> bool:
        moment = time.time() if now is None else now
        if self.until <= moment:
            return False
        return self.schema_version != SCHEMA_VERSION or self.hard_until > moment

    def is_native_eligible(self, now: float | None = None) -> bool:
        return (
            self.schema_version == SCHEMA_VERSION
            and bool(self.grant_id)
            and self.hard_until > (time.time() if now is None else now)
            and self.is_active(now)
        )

    def token(self, now: float | None = None) -> LeaseToken | None:
        if not self.is_active(now):
            return None
        return LeaseToken(self.grant_id, self.revoke_epoch)


class LeaseStore:
    """Serialize grant read-modify-write operations through a stable lockfile."""

    def __init__(self, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir)
        self.state_path = self.state_dir / LEASE_STATE_FILE
        self.lock_path = self.state_dir / LEASE_LOCK_FILE

    def _ensure_directory(self) -> None:
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)

    @contextmanager
    def _locked(self, *, exclusive: bool) -> Iterator[None]:
        self._ensure_directory()
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.lock_path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read_unlocked(self) -> dict[str, Any]:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
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

    def _write_unlocked(self, data: Mapping[str, Any]) -> None:
        payload = json.dumps(
            dict(data), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        if len(payload) > _MAX_STATE_BYTES:
            raise ValueError("desktop lease state exceeds 64 KiB")
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f".{LEASE_STATE_FILE}.",
            suffix=".tmp",
            dir=self.state_dir,
        )
        temporary = Path(raw_path)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
            directory_fd = os.open(
                self.state_dir,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def read(self) -> dict[str, Any]:
        with self._locked(exclusive=False):
            return self._read_unlocked()

    def snapshot(self) -> LeaseSnapshot:
        return LeaseSnapshot.from_mapping(self.read())

    def replace(self, data: Mapping[str, Any]) -> None:
        """Atomically replace state without upgrading legacy test fixtures."""

        with self._locked(exclusive=True):
            self._write_unlocked(data)

    def grant(
        self,
        *,
        until: float,
        reason: str,
        granted: float,
        granted_by: str,
    ) -> LeaseSnapshot:
        with self._locked(exclusive=True):
            current = LeaseSnapshot.from_mapping(self._read_unlocked())
            data = {
                "schema_version": SCHEMA_VERSION,
                "grant_id": uuid.uuid4().hex,
                "revoke_epoch": current.revoke_epoch,
                "until": float(until),
                "hard_until": float(until),
                "reason": reason,
                "granted": float(granted),
                "granted_by": granted_by,
            }
            self._write_unlocked(data)
            return LeaseSnapshot.from_mapping(data)

    def revoke(self, *, now: float | None = None) -> tuple[LeaseSnapshot, int]:
        moment = time.time() if now is None else now
        with self._locked(exclusive=True):
            current = LeaseSnapshot.from_mapping(self._read_unlocked())
            data = dict(current.raw)
            data.setdefault("grant_id", current.grant_id)
            data.setdefault("reason", "")
            data.setdefault("granted", 0.0)
            data.setdefault("granted_by", "")
            data.update(
                schema_version=SCHEMA_VERSION,
                revoke_epoch=current.revoke_epoch + 1,
                until=0.0,
                hard_until=0.0,
            )
            self._write_unlocked(data)
            remaining = max(0, int(current.until - moment)) if current.is_active(moment) else 0
            return LeaseSnapshot.from_mapping(data), remaining

    def touch(
        self,
        token: LeaseToken,
        *,
        idle_seconds: int,
        now: float | None = None,
    ) -> bool:
        """Validate one captured identity and optionally slide its deadline."""

        moment = time.time() if now is None else now
        with self._locked(exclusive=True):
            current = LeaseSnapshot.from_mapping(self._read_unlocked())
            if not current.is_active(moment):
                return False
            if (
                current.grant_id != token.grant_id
                or current.revoke_epoch != token.revoke_epoch
            ):
                return False
            if idle_seconds <= 0 or current.hard_until <= 0:
                return True
            if current.hard_until <= moment:
                return False
            new_until = min(current.hard_until, moment + idle_seconds)
            if abs(new_until - current.until) < 1.0:
                return True
            data = dict(current.raw)
            data["until"] = new_until
            self._write_unlocked(data)
            return True


__all__ = [
    "LEASE_LOCK_FILE",
    "LEASE_STATE_FILE",
    "SCHEMA_VERSION",
    "LeaseSnapshot",
    "LeaseStore",
    "LeaseToken",
]
