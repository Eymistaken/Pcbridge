"""Emergency cleanup for runtime resources, independent of the next action."""

from __future__ import annotations

import logging
import threading
from typing import Callable

from .lease import LeaseToken

log = logging.getLogger("pcbridge.desktop")


class ResourceWatch:
    def __init__(self, gate, cleanup: Callable[[], None]) -> None:
        self.gate = gate
        self.cleanup = cleanup
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._token: LeaseToken | None = None

    def track(self, token: LeaseToken | None) -> None:
        with self._lock:
            if self._stop.is_set():
                raise RuntimeError("The desktop resource watcher is closed")
            self._token = token
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="pcbridge-desktop-resource-guard", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(0.1):
            # Serialize tracking with cleanup: a new admitted call must not
            # attach its resources while an old grant's cleanup is in flight.
            with self._lock:
                token = self._token
                if token is None:
                    continue
                try:
                    decision = self.gate.resource_guard(token)
                    if decision.allowed:
                        continue
                    reason = decision.code.value if decision.code else "unknown"
                except Exception:  # noqa: BLE001 - unreadable safety is closed
                    reason = "unknown"
                self._token = None
                try:
                    self.cleanup()
                except Exception:  # noqa: BLE001 - preserve the guard thread
                    log.warning("Desktop resource emergency cleanup failed")
                self.gate.audit("desktop_resource_guard_closed", grant_id=token.grant_id,
                                revoke_epoch=token.revoke_epoch, reason=reason)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
