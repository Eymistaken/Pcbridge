"""Shared ownership and lifecycle for desktop providers."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Callable, Hashable, Iterator

from ..config import Config
from . import apps as appslib
from . import execution as executionlib
from .backends.python import (
    PythonAccessibilityProvider,
    PythonCaptureProvider,
    PythonDesktopStateProvider,
    PythonInputProvider,
)
from .contracts import (
    AccessibilityProvider,
    CaptureProvider,
    DesktopStateProvider,
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
from .safety import ActivityState, SafetyGate, ScreenLockState


logger = logging.getLogger("pcbridge.desktop.runtime")

_REQUIRED_CAPABILITIES = {
    "capture.monitor": ("desktop.capture", "os.capture"),
    "capture.window": ("desktop.capture", "os.capture"),
    "input.pointer": ("desktop.input", "os.pointer"),
    "input.pointer_relative": ("desktop.input", "os.pointer"),
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
        desktop_state_provider: DesktopStateProvider | None = None,
        screen_lock_probe: Callable[[], bool | None] | None = None,
        user_activity_probe: Callable[[], int | None] | None = None,
        extension_focus_probe: Callable[[], bool] | None = None,
        execution_lock: executionlib.ExecutionLock | None = None,
        rate_limit: int = 0,
        execution_wait_seconds: float = executionlib.EXECUTION_WAIT_SECONDS,
    ) -> None:
        self.capture_provider = capture_provider
        self.input_provider = input_provider
        self.accessibility_provider = accessibility_provider
        self.gate = gate
        self.desktop_state_provider = desktop_state_provider or PythonDesktopStateProvider(
            screen_lock_probe=screen_lock_probe,
            user_activity_probe=user_activity_probe,
        )
        self._extension_focus_probe = (
            extension_focus_probe or appslib.extension_focus_available
        )
        self._capabilities = CapabilityRegistry()
        self._timer: threading.Timer | None = None
        self._lock = threading.RLock()
        self._closed = False
        # One desktop write at a time across processes (Task 5.1). A runtime
        # built by hand without a lock still re-checks every action; it only
        # does not serialize against other processes.
        self._execution_lock = execution_lock
        self._rate_limit = int(rate_limit or 0)
        self._execution_wait_seconds = float(execution_wait_seconds)

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
        try:
            extension_focus = bool(self._extension_focus_probe())
        except Exception:
            extension_focus = False
        focus_usable = bool(
            extension_focus
            or (
                keyboard
                and keyboard.usable_now
                and accessibility
                and accessibility.usable_now
            )
        )
        focus_dependencies = (
            ()
            if extension_focus
            else (keyboard, accessibility)
        )
        focus_blocker = next(
            (
                capability
                for capability in focus_dependencies
                if capability and not capability.usable_now
            ),
            None,
        )
        if focus_usable:
            focus_state = (
                CapabilityState.SUPPORTED
                if extension_focus
                else CapabilityState.DEGRADED
            )
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
            backend=(
                "linux.gnome-shell-extension"
                if extension_focus
                else "linux.gnome-search"
            ),
            scope="os.window",
            reason_code=focus_reason,
            limitations=(
                (
                    "Already-open windows use the GNOME Shell extension; closed "
                    "applications are launched directly; GNOME search is the "
                    "fallback for a window the extension cannot activate. Results "
                    "are verified through accessibility."
                    if extension_focus
                    else "Closed applications are launched directly; open windows "
                    "come forward through GNOME search, for installed applications "
                    "only. Results are verified through accessibility."
                ),
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

        activity = self.desktop_state_provider.user_activity()
        values["user_activity"] = self._observed_capability(
            "user_activity",
            CapabilityState.SUPPORTED
            if activity.state == ActivityState.KNOWN
            else CapabilityState.UNAVAILABLE,
            backend="linux.mutter-idle-monitor",
            scope="os.session",
            reason_code=(
                None
                if activity.state == ActivityState.KNOWN
                else ErrorCode.ACTIVITY_UNKNOWN
            ),
        )
        lock = self.desktop_state_provider.screen_lock()
        values["screen_lock"] = self._observed_capability(
            "screen_lock",
            CapabilityState.SUPPORTED
            if lock.state != ScreenLockState.UNKNOWN
            else CapabilityState.UNAVAILABLE,
            backend="linux.gnome-screen-saver",
            scope="os.session",
            reason_code=(
                None
                if lock.state != ScreenLockState.UNKNOWN
                else ErrorCode.LOCK_STATE_UNKNOWN
            ),
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
        screen_lock_state = self.desktop_state_provider.screen_lock().state.value
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
        """Close capture after expiry, or rearm if another process touched it.

        The close does not ask whether the handle still believes it is open.
        That flag is known to drift: a screen lock makes Mutter close the
        session while the Python side still says open (measured 2026-09-13),
        and any other drift would silently skip the cleanup. `stop_capture`
        is idempotent, so closing an already closed handle costs nothing;
        skipping a close that was due costs a share that outlives its grant.
        """
        with self._lock:
            if self._closed:
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

    @contextmanager
    def write_sequence(self, tool: str) -> Iterator[executionlib.SequenceGuard]:
        """Run one desktop write alone and keep re-checking its admission.

        Call only after `gate.check()` admitted the call: the grant identity
        captured there is what every later action is held to. Raises
        `SequenceRefused` -- BUSY, or the refusal -- before yielding, so a
        refused call has sent nothing.
        """
        last_token = getattr(self.gate, "last_token", None)
        token = last_token() if callable(last_token) else None

        def verify() -> object:
            decision = self.gate.verify(token)
            if getattr(decision, "allowed", False):
                # The check slid the lease; the capture deadline follows it.
                self.refresh_capture_deadline()
            return decision

        if self._execution_lock is None:
            guard = executionlib.SequenceGuard(verify, None)
            self._admit(guard)
            yield guard
            return
        with self._execution_lock.hold(
            tool, timeout=self._execution_wait_seconds
        ) as slot:
            guard = executionlib.SequenceGuard(
                verify, slot, rate_limit=self._rate_limit
            )
            self._admit(guard)
            yield guard

    def _admit(self, guard: executionlib.SequenceGuard) -> None:
        try:
            guard.admit()
        except executionlib.SequenceRefused:
            # Keys or buttons this process still holds from an earlier call
            # belong to a grant that no longer admits anything: let them go now
            # instead of when the hold timer fires.
            try:
                self.input_provider.release_all()
            except Exception as exc:  # noqa: BLE001 - the refusal is still raised
                logger.warning("held input release after refusal failed: %s", exc)
            raise

    def release_resources(self) -> None:
        """Release reusable desktop resources without retiring the runtime."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        # The native accessibility reader is a helper bound to the grant that
        # just ended; the Python reader has nothing to close.
        close_accessibility = getattr(self.accessibility_provider, "close", None)
        for name, close in (
            ("input", self.input_provider.close),
            ("capture", self.capture_provider.close),
            ("accessibility", close_accessibility),
        ):
            if not callable(close):
                continue
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


def select_capture_provider(cfg: Config, gate: GrantProvider) -> CaptureProvider:
    """Apply the backend table once, here, and nowhere else.

    The choice is made when a runtime is built and never revisited inside it:
    swapping acquisition backends mid-session would let one `all` capture be
    assembled from two different sources, and `PLAN.md` forbids exactly that.
    """
    from .backends.rust import (  # yerel import: native yol istege bagli
        RustCaptureProvider,
        native_binary_ready,
        select_capture_backend,
    )

    requested = cfg.native.capture
    if requested == "auto" and cfg.desktop.capture_backend == "gnome-screenshot":
        # An explicitly chosen screenshot program is kept: `auto` must not
        # quietly open a screen share the user configured away (Task 4.3).
        requested = "python"
    ready, reason = native_binary_ready(cfg)
    selection = select_capture_backend(
        requested=requested,
        native_ready=ready,
        native_reason=reason,
    )
    if selection.backend == "rust":
        return RustCaptureProvider(cfg, gate=gate)
    return PythonCaptureProvider(cfg, degraded_reason=selection.reason if selection.degraded else "")


def select_input_provider(cfg: Config, gate: GrantProvider) -> InputProvider:
    """Choose input injection once, the way capture is chosen.

    `auto`, the default since Gate 5, takes the native helper when it is
    packaged and otherwise the Python provider -- visibly: the keyboard and
    pointer capabilities come back degraded with the reason. `rust` never
    falls back; `python` never reaches for the helper.
    """
    choice = cfg.native.input
    if choice == "python":
        return PythonInputProvider(cfg)
    from .backends.rust import RustInputProvider, native_binary_ready

    if choice == "rust":
        return RustInputProvider(cfg, gate=gate)
    ready, reason = native_binary_ready(cfg)
    if ready:
        return RustInputProvider(cfg, gate=gate)
    return PythonInputProvider(
        cfg,
        degraded_reason=(
            "The native input helper is unavailable; using the Python path: "
            + (reason or "helper not found")
        ),
    )


def select_accessibility_provider(cfg: Config, gate: GrantProvider) -> AccessibilityProvider:
    """Choose who reads the accessibility tree, once, like capture and input.

    `auto` is the default since Task 6.3: the native helper when it is
    packaged, the Python helper otherwise, and it says so. `rust` never falls
    back; `python` keeps the old path.
    The provider chosen here also acts: a click or a text write goes to the
    helper that made the dump it names (Task 6.3).
    """
    choice = cfg.native.accessibility
    if choice == "python":
        return PythonAccessibilityProvider()
    from .backends.rust import RustAccessibilityProvider, native_binary_ready

    if choice == "rust":
        return RustAccessibilityProvider(cfg, gate=gate)
    ready, reason = native_binary_ready(cfg)
    if ready:
        return RustAccessibilityProvider(cfg, gate=gate)
    return PythonAccessibilityProvider(
        degraded_reason=(
            "The native accessibility helper is unavailable; using the Python "
            "path: " + (reason or "helper not found")
        ),
    )


def create_runtime(
    cfg: Config,
    *,
    gate: GrantProvider | None = None,
    capture_provider: CaptureProvider | None = None,
    input_provider: InputProvider | None = None,
    accessibility_provider: AccessibilityProvider | None = None,
    desktop_state_provider: DesktopStateProvider | None = None,
) -> DesktopRuntime:
    """Build an isolated, lazy runtime for one MCP or CLI process."""
    state_provider = desktop_state_provider or PythonDesktopStateProvider()
    resolved_gate = (
        gate if gate is not None else SafetyGate(cfg, state_provider=state_provider)
    )
    return DesktopRuntime(
        capture_provider=(
            capture_provider
            if capture_provider is not None
            else select_capture_provider(cfg, resolved_gate)
        ),
        input_provider=(
            input_provider
            if input_provider is not None
            else select_input_provider(cfg, resolved_gate)
        ),
        accessibility_provider=(
            accessibility_provider
            if accessibility_provider is not None
            else select_accessibility_provider(cfg, resolved_gate)
        ),
        gate=resolved_gate,
        desktop_state_provider=state_provider,
        execution_lock=executionlib.ExecutionLock(cfg.state_dir),
        rate_limit=int(cfg.desktop.max_actions_per_second),
    )


__all__ = [
    "DesktopRuntime",
    "create_runtime",
    "select_accessibility_provider",
    "select_capture_provider",
    "select_input_provider",
]
