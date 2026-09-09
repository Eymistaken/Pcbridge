"""Shared ownership and lifecycle for desktop providers."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Hashable

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
from .capabilities import (
    AuthorizationStatus,
    Capability,
    CapabilityEvidence,
    CapabilityRegistry,
    CapabilitySnapshot,
    CapabilityState,
)
from .errors import ErrorCode
from .safety import SafetyGate, idle_ms, screen_locked


logger = logging.getLogger("pcbridge.desktop.runtime")

_REQUIRED_CAPABILITIES = {
    "capture.monitor": ("desktop.capture", "os.capture"),
    "capture.window": ("desktop.capture", "os.capture"),
    "input.pointer": ("desktop.input", "os.pointer"),
    "input.keyboard": ("desktop.input", "os.keyboard"),
    "accessibility.read": ("desktop.accessibility", "os.accessibility"),
    "accessibility.action": ("desktop.accessibility", "os.accessibility"),
    "window.list": ("desktop.accessibility", "os.window"),
    "window.focus": ("desktop.window", "os.window"),
    "window.move_resize": ("desktop.window", "os.window"),
    "clipboard.read": ("desktop.clipboard", "os.clipboard"),
    "clipboard.write": ("desktop.clipboard", "os.clipboard"),
    "user_activity": ("desktop.session", "os.session"),
    "screen_lock": ("desktop.session", "os.session"),
}


class DesktopRuntime:
    """Own providers, their handles, and the capture-expiry timer."""

    def __init__(
        self,
        *,
        capture_provider: CaptureProvider,
        input_provider: InputProvider,
        accessibility_provider: AccessibilityProvider,
        gate: GrantProvider,
        screen_lock_probe: Callable[[], bool | None] | None = None,
        user_activity_probe: Callable[[], int | None] | None = None,
    ) -> None:
        self.capture_provider = capture_provider
        self.input_provider = input_provider
        self.accessibility_provider = accessibility_provider
        self.gate = gate
        self._screen_lock_probe = screen_lock_probe or screen_locked
        self._user_activity_probe = user_activity_probe or idle_ms
        self._capabilities = CapabilityRegistry()
        self._timer: threading.Timer | None = None
        self._lock = threading.RLock()
        self._closed = False

    def _capability_token(self) -> tuple[Hashable, ...]:
        return tuple(
            provider.capability_token()
            for provider in (
                self.capture_provider,
                self.input_provider,
                self.accessibility_provider,
            )
        )

    @staticmethod
    def _observed_capability(
        name: str,
        state: CapabilityState,
        *,
        backend: str,
        scope: str,
        reason_code: ErrorCode | None = None,
        limitations: tuple[str, ...] = (),
    ) -> Capability:
        return Capability(
            name=name,
            state=state,
            backend=backend,
            scope=scope,
            reason_code=reason_code,
            limitations=limitations,
            observed_at=time.time(),
            evidence=CapabilityEvidence.PROBE,
            usable_now=state in {CapabilityState.SUPPORTED, CapabilityState.DEGRADED},
        )

    def _probe_capabilities(self) -> dict[str, Capability]:
        values: dict[str, Capability] = {}
        for provider in (
            self.capture_provider,
            self.input_provider,
            self.accessibility_provider,
        ):
            values.update(provider.probe_capabilities())

        keyboard = values.get("input.keyboard")
        accessibility = values.get("accessibility.read")
        focus_usable = bool(
            keyboard
            and keyboard.usable_now
            and accessibility
            and accessibility.usable_now
        )
        focus_blocker = next(
            (
                capability
                for capability in (keyboard, accessibility)
                if capability and not capability.usable_now
            ),
            None,
        )
        if focus_usable:
            focus_state = CapabilityState.DEGRADED
            focus_reason = None
        elif (
            focus_blocker
            and focus_blocker.state == CapabilityState.PERMISSION_REQUIRED
        ):
            focus_state = CapabilityState.PERMISSION_REQUIRED
            focus_reason = focus_blocker.reason_code
        else:
            focus_state = CapabilityState.UNAVAILABLE
            focus_reason = ErrorCode.BACKEND_UNAVAILABLE
        values["window.focus"] = self._observed_capability(
            "window.focus",
            focus_state,
            backend="linux.gnome-search",
            scope="os.window",
            reason_code=focus_reason,
            limitations=(
                "Focus uses GNOME search and verifies the result through accessibility.",
            )
            if focus_usable
            else (),
        )
        values["window.move_resize"] = self._observed_capability(
            "window.move_resize",
            CapabilityState.UNSUPPORTED,
            backend="none",
            scope="os.window",
            reason_code=ErrorCode.UNSUPPORTED,
        )

        activity = self._user_activity_probe()
        values["user_activity"] = self._observed_capability(
            "user_activity",
            CapabilityState.SUPPORTED
            if activity is not None
            else CapabilityState.UNAVAILABLE,
            backend="linux.mutter-idle-monitor",
            scope="os.session",
            reason_code=None if activity is not None else ErrorCode.ACTIVITY_UNKNOWN,
        )
        locked = self._screen_lock_probe()
        values["screen_lock"] = self._observed_capability(
            "screen_lock",
            CapabilityState.SUPPORTED
            if locked is not None
            else CapabilityState.UNAVAILABLE,
            backend="linux.gnome-screen-saver",
            scope="os.session",
            reason_code=None if locked is not None else ErrorCode.LOCK_STATE_UNKNOWN,
        )
        for name, (backend, scope) in _REQUIRED_CAPABILITIES.items():
            values.setdefault(
                name,
                self._observed_capability(
                    name,
                    CapabilityState.UNAVAILABLE,
                    backend=backend,
                    scope=scope,
                    reason_code=ErrorCode.BACKEND_UNAVAILABLE,
                ),
            )
        return values

    def _authorization(self) -> AuthorizationStatus:
        locked = self._screen_lock_probe()
        screen_lock_state = (
            "unknown" if locked is None else "locked" if locked else "unlocked"
        )
        hard_remaining = getattr(self.gate, "hard_remaining_seconds", None)
        return AuthorizationStatus(
            desktop_enabled=bool(getattr(self.gate.spec, "enabled", False)),
            grant_remaining_seconds=max(0, int(self.gate.remaining_seconds())),
            hard_remaining_seconds=max(
                0,
                int(hard_remaining()) if callable(hard_remaining) else 0,
            ),
            screen_lock_state=screen_lock_state,
            revoke_epoch=max(0, int(getattr(self.gate, "revoke_epoch", 0) or 0)),
        )

    def capabilities(self, *, refresh: bool = False) -> CapabilitySnapshot:
        """Return cached probes plus fresh authorization and operation evidence."""
        if refresh:
            self._capabilities.invalidate()
        return self._capabilities.snapshot(
            token=self._capability_token(),
            probe=self._probe_capabilities,
            authorization=self._authorization(),
        )

    def record_capability_operation(self, capability: Capability) -> None:
        self._capabilities.record_operation(capability)

    def invalidate_capabilities(self) -> None:
        self._capabilities.invalidate()

    def start_capture(self, *, cursor: bool | None = None) -> dict:
        """Start this runtime's capture session and bind it to grant expiry."""
        with self._lock:
            if self._closed:
                raise RuntimeError("desktop runtime is closed")
        result = self.capture_provider.start(cursor=cursor)
        self.invalidate_capabilities()
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
        self.invalidate_capabilities()

    def touch_grant(self, token: object | None = None) -> bool:
        """Refresh the sliding grant and keep its capture timer synchronized."""
        touched = self.gate.touch(token) if token is not None else self.gate.touch()
        if touched is False:
            return False
        self.refresh_capture_deadline()
        return True

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
        self.invalidate_capabilities()

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
