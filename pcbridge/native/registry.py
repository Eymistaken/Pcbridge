"""Private native-helper registry protected against PID reuse."""

from __future__ import annotations

import json
import os
import signal
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_MAX_ENTRY_BYTES = 16 * 1024


def process_start_identity(pid: int) -> str:
    """Return Linux process start ticks, which do not repeat when a PID is reused."""

    raw = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
    closing = raw.rfind(")")
    if closing < 0:
        raise ValueError("invalid /proc process stat")
    fields = raw[closing + 2 :].split()
    if len(fields) <= 19 or not fields[19].isdigit():
        raise ValueError("process start identity is unavailable")
    return fields[19]


@dataclass(frozen=True)
class NativeProcessEntry:
    pid: int
    process_start_id: str
    instance_id: str
    path: Path


class NativeRegistry:
    """Record only exact native children owned by this desktop session."""

    def __init__(self, runtime_dir: str | Path) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.directory = self.runtime_dir / "pcbridge" / "native"

    @staticmethod
    def _private_directory(path: Path) -> None:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path, 0o700)

    def _ensure_directory(self) -> None:
        self.runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._private_directory(self.runtime_dir / "pcbridge")
        self._private_directory(self.directory)

    def register(self, pid: int, instance_id: str) -> NativeProcessEntry:
        if not instance_id:
            raise ValueError("native instance_id must be nonempty")
        self._ensure_directory()
        entry = NativeProcessEntry(
            pid=int(pid),
            process_start_id=process_start_identity(pid),
            instance_id=instance_id,
            path=self.directory / f"native-{int(pid)}-{uuid.uuid4().hex}.json",
        )
        payload = {
            "pid": entry.pid,
            "process_start_id": entry.process_start_id,
            "instance_id": entry.instance_id,
            "registered_at": time.time(),
        }
        descriptor, raw_path = tempfile.mkstemp(
            prefix=".native-", suffix=".tmp", dir=self.directory
        )
        temporary = Path(raw_path)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", closefd=True) as stream:
                descriptor = -1
                json.dump(payload, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, entry.path)
            directory_fd = os.open(
                self.directory,
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
        return entry

    @staticmethod
    def _read(path: Path) -> NativeProcessEntry | None:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            return None
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            raw = stream.read(_MAX_ENTRY_BYTES + 1)
        if len(raw) > _MAX_ENTRY_BYTES:
            return None
        try:
            value: Any = json.loads(raw.decode("utf-8"))
            pid = value["pid"]
            start_id = value["process_start_id"]
            instance_id = value["instance_id"]
        except (KeyError, TypeError, ValueError, UnicodeDecodeError):
            return None
        if (
            isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid <= 0
            or not isinstance(start_id, str)
            or not start_id
            or not isinstance(instance_id, str)
            or not instance_id
        ):
            return None
        return NativeProcessEntry(pid, start_id, instance_id, path)

    def entries(self) -> list[NativeProcessEntry]:
        try:
            paths = sorted(self.directory.glob("native-*.json"))
        except OSError:
            return []
        return [entry for path in paths if (entry := self._read(path)) is not None]

    def is_same_process(self, entry: NativeProcessEntry) -> bool:
        try:
            if Path(f"/proc/{entry.pid}").stat().st_uid != os.getuid():
                return False
            return process_start_identity(entry.pid) == entry.process_start_id
        except (OSError, ValueError):
            return False

    def unregister(self, entry: NativeProcessEntry) -> None:
        current = self._read(entry.path)
        if current != entry:
            return
        try:
            entry.path.unlink()
        except FileNotFoundError:
            pass

    def terminate(
        self,
        entry: NativeProcessEntry,
        process_signal: signal.Signals = signal.SIGTERM,
    ) -> bool:
        pidfd_open = getattr(os, "pidfd_open", None)
        pidfd_send_signal = getattr(signal, "pidfd_send_signal", None)
        if not callable(pidfd_open) or not callable(pidfd_send_signal):
            return False
        try:
            descriptor = pidfd_open(entry.pid)
        except OSError:
            return False
        try:
            if not self.is_same_process(entry):
                return False
            pidfd_send_signal(descriptor, process_signal)
        except (OSError, ValueError):
            return False
        finally:
            os.close(descriptor)
        return True

    def prune_stale(self) -> int:
        removed = 0
        try:
            paths = list(self.directory.glob("native-*.json"))
        except OSError:
            return 0
        for path in paths:
            entry = self._read(path)
            if entry is not None and self.is_same_process(entry):
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            removed += 1
        return removed


__all__ = [
    "NativeProcessEntry",
    "NativeRegistry",
    "process_start_identity",
]
