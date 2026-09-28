"""Own one exact native frame lease; never authorize input or capture here."""

from __future__ import annotations

import logging
from pathlib import Path
import subprocess
import threading
import time

from ..native.client import helper_environment
from . import glowstate
from .errors import DesktopError, ErrorCategory, ErrorCode
from .lease import LeaseStore, LeaseToken

log = logging.getLogger("pcbridge.desktop")


def _unavailable(code: ErrorCode = ErrorCode.BACKEND_UNAVAILABLE) -> DesktopError:
    return DesktopError(code=code,
        message="The desktop grant could not obtain a visible native frame; control remains closed.",
        category=ErrorCategory.SAFETY, retryable=True,
        suggested_action="Run pcbridge doctor and restore the visible grant frame before granting access again.",
        permission_scope="pcbridge.desktop", backend="linux.hyprland.glow")


class FrameOwner:
    """The resident process owns the child and retires only its captured grant.

    Consumers in other processes read the same trusted presentation record.
    They never take ownership of, or restart, an existing grant's frame.
    """

    def __init__(self, state_dir: Path, binary: Path) -> None:
        self.directory = state_dir.resolve()
        self.binary = binary.resolve()
        self._lease = LeaseStore(self.directory)
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._token: LeaseToken | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False

    def _valid(self, token: LeaseToken) -> bool:
        snapshot = self._lease.snapshot()
        return snapshot.is_native_eligible() and snapshot.token() == token

    def health(self, token: LeaseToken) -> dict | None:
        return glowstate.read(self.directory, token, binary=self.binary)

    def _retire(self, token: LeaseToken) -> None:
        try:
            self._lease.revoke_if(token)
        except OSError:
            log.warning("The frame owner's lease state could not be retired")

    def _supervise(self, process: subprocess.Popen, token: LeaseToken, stopped: threading.Event) -> None:
        while not stopped.wait(0.1):
            if process.poll() is not None:
                self._retire(token)
                return

    def _stop_process(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
            self._thread = None
        process, self._process = self._process, None
        self._token = None
        if process is None:
            return
        # Lease retirement happens first. Give the native owner its fade-out,
        # then bound shutdown even for an unresponsive configured executable.
        try:
            process.wait(timeout=0.8)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.5)

    def open(self, token: LeaseToken, *, timeout: float = 5.0) -> dict:
        if timeout <= 0:
            raise ValueError("frame startup timeout must be positive")
        with self._lock:
            if self._closed:
                raise _unavailable()
            # Check before stopping any previous owner: a delayed request for
            # an obsolete token must not kill a newer owner's visible frame.
            try:
                valid = self._valid(token)
            except OSError:
                raise _unavailable() from None
            if not valid:
                raise _unavailable(ErrorCode.REVOKED)
            if self._token == token and self._process is not None and self._process.poll() is None:
                record = self.health(token)
                if record is not None:
                    return record
                try:
                    self._retire(token)
                finally:
                    self._stop_process()
                raise _unavailable()
            self._stop_process()
            self._stop = threading.Event()
            try:
                self._process = subprocess.Popen(
                    [str(self.binary), "glow-watch", str(self.directory), token.grant_id, str(token.revoke_epoch)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    close_fds=True, env=helper_environment(),
                )
                self._token = token
                until = time.monotonic() + timeout
                while time.monotonic() < until:
                    if not self._valid(token):
                        raise _unavailable(ErrorCode.REVOKED)
                    if self._process.poll() is not None:
                        raise _unavailable()
                    record = self.health(token)
                    if record is not None:
                        if not self._valid(token):
                            raise _unavailable(ErrorCode.REVOKED)
                        self._thread = threading.Thread(target=self._supervise,
                            args=(self._process, token, self._stop), name="pcbridge-frame-owner", daemon=True)
                        self._thread.start()
                        return record
                    time.sleep(0.025)
                raise _unavailable()
            except BaseException as error:
                try:
                    self._retire(token)
                finally:
                    self._stop_process()
                if isinstance(error, (OSError, RuntimeError)) and not isinstance(error, DesktopError):
                    raise _unavailable() from None
                raise

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                if self._token is not None:
                    self._retire(self._token)
            finally:
                self._stop_process()
