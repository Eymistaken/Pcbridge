"""Shared ownership and lifecycle for desktop providers."""

from __future__ import annotations

import logging
import threading

from ..config import Config
from .backends.python import (
    PythonAccessibilityProvider,
    PythonCaptureProvider,
    PythonInputProvider,
)
from .contracts import (
    AccessibilityProvider,
    CaptureProvider,
    GrantProvider,
    InputProvider,
)
from .safety import SafetyGate


logger = logging.getLogger("pcbridge.desktop.runtime")


class DesktopRuntime:
    """Own providers, their handles, and the capture-expiry timer."""

    def __init__(
        self,
        *,
        capture_provider: CaptureProvider,
        input_provider: InputProvider,
        accessibility_provider: AccessibilityProvider,
        gate: GrantProvider,
    ) -> None:
        self.capture_provider = capture_provider
        self.input_provider = input_provider
        self.accessibility_provider = accessibility_provider
        self.gate = gate
        self._timer: threading.Timer | None = None
        self._lock = threading.RLock()
        self._closed = False

    def start_capture(self, *, cursor: bool | None = None) -> dict:
        """Start this runtime's capture session and bind it to grant expiry."""
        with self._lock:
            if self._closed:
                raise RuntimeError("desktop runtime is closed")
        result = self.capture_provider.start(cursor=cursor)
        self.refresh_capture_deadline()
        return result

    def refresh_capture_deadline(self) -> None:
        """Rearm capture cleanup after the sliding grant deadline changes."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if self._closed or not self.capture_provider.is_open():
                return
            remaining = self.gate.remaining_seconds()
            if remaining <= 0:
                return
            timer = threading.Timer(remaining + 2, self.close_capture_if_locked)
            timer.daemon = True
            timer.start()
            self._timer = timer

    def close_capture_if_locked(self) -> None:
        """Close capture after expiry, or rearm if another process touched it."""
        with self._lock:
            if self._closed or not self.capture_provider.is_open():
                return
        if self.gate.is_unlocked():
            self.refresh_capture_deadline()
            return
        self.stop_capture()

    def stop_capture(self) -> None:
        """Cancel the expiry timer and close only this runtime's capture handle."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self.capture_provider.close()

    def touch_grant(self) -> None:
        """Refresh the sliding grant and keep its capture timer synchronized."""
        self.gate.touch()
        self.refresh_capture_deadline()

    def release_resources(self) -> None:
        """Release reusable desktop resources without retiring the runtime."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        for name, close in (
            ("input", self.input_provider.close),
            ("capture", self.capture_provider.close),
        ):
            try:
                close()
            except Exception as exc:  # noqa: BLE001 - lifecycle cleanup continues
                logger.warning("%s provider cleanup failed: %s", name, exc)

    def close(self) -> None:
        """Idempotently retire every resource owned by this runtime."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.release_resources()
        close_accessibility = getattr(self.accessibility_provider, "close", None)
        if callable(close_accessibility):
            try:
                close_accessibility()
            except Exception as exc:  # noqa: BLE001 - lifecycle cleanup continues
                logger.warning("accessibility provider cleanup failed: %s", exc)


def create_runtime(
    cfg: Config,
    *,
    gate: GrantProvider | None = None,
    capture_provider: CaptureProvider | None = None,
    input_provider: InputProvider | None = None,
    accessibility_provider: AccessibilityProvider | None = None,
) -> DesktopRuntime:
    """Build an isolated, lazy runtime for one MCP or CLI process."""
    return DesktopRuntime(
        capture_provider=(
            capture_provider
            if capture_provider is not None
            else PythonCaptureProvider(cfg)
        ),
        input_provider=(
            input_provider if input_provider is not None else PythonInputProvider(cfg)
        ),
        accessibility_provider=(
            accessibility_provider
            if accessibility_provider is not None
            else PythonAccessibilityProvider()
        ),
        gate=gate if gate is not None else SafetyGate(cfg),
    )


__all__ = ["DesktopRuntime", "create_runtime"]
