"""What every desktop provider shares: the capability report and typed errors.

The providers themselves are in `rust.py`: the native helper is the only
desktop backend. This module holds what does not talk to the operating
system: how a capability is written down, how a failure becomes a typed
`DesktopError`, the clipboard programs' capability, and the desktop state
observations the safety gate reads.
"""

from __future__ import annotations

import os
import shutil
import time
from typing import Callable

from ... import distro as distrolib
from .. import clipboard as clipboardlib
from .. import compositor as compositorlib
from .. import safety as safetylib
from ..capabilities import Capability, CapabilityEvidence, CapabilityState
from ..errors import DesktopError, ErrorCategory, ErrorCode
from pathlib import Path


class SessionStateProvider:
    """Expose desktop session observations without collapsing unknown states."""

    def __init__(
        self,
        *,
        screen_lock_probe: Callable[[], bool | None] | None = None,
        user_activity_probe: Callable[[], int | None] | None = None,
    ) -> None:
        self._screen_lock_probe = screen_lock_probe
        self._user_activity_probe = user_activity_probe

    def screen_lock(self) -> safetylib.ScreenLockObservation:
        if self._screen_lock_probe is None:
            return safetylib.observe_screen_lock()
        value = self._screen_lock_probe()
        state = (
            safetylib.ScreenLockState.KNOWN_LOCKED
            if value is True
            else safetylib.ScreenLockState.KNOWN_UNLOCKED
            if value is False
            else safetylib.ScreenLockState.UNKNOWN
        )
        return safetylib.ScreenLockObservation(state, observed_at=time.time())

    def user_activity(self) -> safetylib.ActivityObservation:
        if self._user_activity_probe is None:
            return safetylib.observe_user_activity()
        value = self._user_activity_probe()
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return safetylib.ActivityObservation(
                safetylib.ActivityState.KNOWN,
                idle_ms=value,
                observed_at=time.time(),
            )
        return safetylib.ActivityObservation(
            safetylib.ActivityState.UNKNOWN,
            idle_ms=None,
            observed_at=time.time(),
        )


def _state_for(code: ErrorCode | None) -> CapabilityState:
    if code in {
        ErrorCode.PERMISSION_REQUIRED,
        ErrorCode.PERMISSION_DENIED,
        ErrorCode.DEVICE_NOT_GRANTED,
    }:
        return CapabilityState.PERMISSION_REQUIRED
    if code == ErrorCode.UNSUPPORTED:
        return CapabilityState.UNSUPPORTED
    return CapabilityState.UNAVAILABLE


# What to install when a capability reports DEPENDENCY_MISSING without a
# reason of its own (Step 8 of 2.0): `system_capabilities` prints it, so the
# reader gets the command instead of a bare code. First matching prefix wins.
def _install_hints() -> tuple[tuple[str, str], ...]:
    install = distrolib.install_command
    return (
        ("clipboard.", f"wl-clipboard is not installed: {install('wl-clipboard')}"),
        ("input.", "the uinput device is missing: run `pcbridge setup` (installs the udev "
                   "rule and loads the module; asks for sudo)"),
    )


def _capability(
    name: str,
    state: CapabilityState,
    *,
    backend: str,
    scope: str,
    reason_code: ErrorCode | None = None,
    limitations: tuple[str, ...] = (),
) -> Capability:
    if reason_code is ErrorCode.DEPENDENCY_MISSING and not limitations:
        limitations = tuple(
            hint for prefix, hint in _install_hints() if name.startswith(prefix)
        )[:1]
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


def _desktop_error(
    exc: Exception,
    *,
    code: ErrorCode,
    category: ErrorCategory,
    backend: str,
    retryable: bool,
    suggested_action: str,
    permission_scope: str | None = None,
) -> DesktopError:
    return DesktopError(
        code=code,
        message=str(exc),
        category=category,
        retryable=retryable,
        suggested_action=suggested_action,
        permission_scope=permission_scope,
        backend=backend,
    )


# A new `ui_dump` fixes these: the element is gone, changed, or the id no
# longer picks one element. Retrying the same id does not.
_REFRESH_CODES = frozenset({
    ErrorCode.ELEMENT_STALE,
    ErrorCode.ELEMENT_AMBIGUOUS,
    ErrorCode.TARGET_MISMATCH,
})


def _accessibility_error(
    exc: Exception,
    default: ErrorCode,
    category: ErrorCategory,
    retryable: bool,
    backend: str,
) -> DesktopError:
    code = getattr(exc, "code", None) or default
    if code in _REFRESH_CODES:
        return _desktop_error(
            exc,
            code=code,
            category=ErrorCategory.ACCESSIBILITY,
            backend=backend,
            retryable=True,
            suggested_action=(
                "Find the target again: refresh the list with ui_dump or look at "
                "the open windows with window_list."
            ),
        )
    if code is ErrorCode.TIMEOUT:
        return _desktop_error(
            exc,
            code=code,
            category=ErrorCategory.EXECUTION,
            backend=backend,
            retryable=True,
            suggested_action="The application may be hung; look with screen_capture.",
        )
    if code is ErrorCode.EXECUTION_UNKNOWN:
        # The helper timed out: the action may or may not have happened, so
        # it is not replayed. Look first, then decide.
        return DesktopError(
            code=code,
            message=str(exc),
            category=ErrorCategory.EXECUTION,
            retryable=False,
            suggested_action="Before repeating, look at the result with ui_dump or screen_capture.",
            backend=backend,
            execution_state="unknown",
        )
    if code is ErrorCode.ACTION_UNSUPPORTED:
        return _desktop_error(
            exc,
            code=code,
            category=ErrorCategory.ACCESSIBILITY,
            backend=backend,
            retryable=False,
            suggested_action="This element does not offer that action; look at a screenshot and pick another way.",
        )
    if code is ErrorCode.TEXT_MISMATCH:
        # The field was written: repeating the same text changes nothing.
        return _desktop_error(
            exc,
            code=code,
            category=ErrorCategory.ACCESSIBILITY,
            backend=backend,
            retryable=False,
            suggested_action=(
                "The field did not take the text as given; look with ui_dump and "
                "try a text the field accepts."
            ),
        )
    return _desktop_error(
        exc,
        code=code,
        category=category,
        backend=backend,
        retryable=retryable,
        suggested_action="Refresh the accessibility tree and try again.",
    )


def _display_mapping_error(exc: Exception) -> DesktopError:
    return _desktop_error(
        exc,
        code=ErrorCode.DISPLAY_MAPPING_UNKNOWN,
        category=ErrorCategory.CAPTURE,
        backend=compositorlib.current().display_backend,
        retryable=True,
        suggested_action="Refresh the screen layout and try again.",
    )


def _wayland_socket() -> str | None:
    display = os.environ.get("WAYLAND_DISPLAY", "").strip()
    if not display:
        return None
    path = Path(display)
    if not path.is_absolute():
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "").strip()
        if not runtime_dir:
            return None
        path = Path(runtime_dir) / path
    return str(path) if path.exists() else None


def _clipboard_capabilities(backend: str) -> dict[str, Capability]:
    """`clipboard.read` and `clipboard.write` for the clipboard programs.

    The helper runs the programs; nothing is run here: the program on `PATH`
    and the display are enough (wl-clipboard and the Wayland socket, or on
    X11 `xclip` and `DISPLAY`). The one limitation belongs to the write,
    since it is the restore that loses the other representations.
    """
    import os

    x11 = backend == "linux.xclip.native"
    values: dict[str, Capability] = {}
    for name, command in (
        ("clipboard.read", "xclip" if x11 else "wl-paste"),
        ("clipboard.write", "xclip" if x11 else "wl-copy"),
    ):
        command_path = shutil.which(command)
        display = bool(os.environ.get("DISPLAY")) if x11 else _wayland_socket()
        available = bool(command_path and display)
        values[name] = _capability(
            name,
            CapabilityState.SUPPORTED if available else CapabilityState.UNAVAILABLE,
            backend=backend,
            scope="os.clipboard",
            reason_code=(
                None
                if available
                else ErrorCode.DEPENDENCY_MISSING
                if not command_path
                else ErrorCode.BACKEND_UNAVAILABLE
            ),
            limitations=(
                (clipboardlib.SINGLE_MIME_LIMITATION,)
                if available and name == "clipboard.write"
                else ()
            ),
        )
    return values


def _gnome_frame_support() -> bool | None:
    """Whether GNOME's extension reports window frames; None elsewhere or without one."""
    if compositorlib.current().kind != "gnome":
        return None
    from .. import apps as appslib

    return appslib.extension_frame_support()


def _gnome_window_capability(monitor: Capability) -> Capability:
    """`capture.window` on GNOME: a region of the monitor frame (2.8).

    The extension names the focused window's frame, so a window capture
    stands or falls with monitor capture, as on KDE Plasma and Hyprland.
    Without an extension that reports frames there is no window capture:
    the gnome-screenshot path is gone, so this says what fixes it.
    """
    support = _gnome_frame_support()
    if support:
        return _capability(
            "capture.window",
            monitor.state,
            backend=compositorlib.current().focus_backend,
            scope="os.capture",
            reason_code=monitor.reason_code,
            limitations=monitor.limitations,
        )
    why = (
        "The GNOME extension loaded in this session cannot report window frames; log "
        "out and back in to load the new one."
        if support is False
        else "The pcbridge GNOME extension is not running; `pcbridge setup` installs "
        "and enables it, and the next login loads it."
    )
    return _capability(
        "capture.window",
        CapabilityState.UNAVAILABLE,
        backend=compositorlib.current().focus_backend,
        scope="os.capture",
        reason_code=ErrorCode.BACKEND_UNAVAILABLE,
        limitations=(why,),
    )


__all__ = [
    "SessionStateProvider",
]
