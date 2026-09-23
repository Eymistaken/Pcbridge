"""Adapters for the existing pure-Python desktop implementation."""

from __future__ import annotations

import os
import shutil
import time
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Sequence, TypeVar

from ...config import Config
from .. import capture as capturelib
from .. import compositor as compositorlib
from .. import clipboard as clipboardlib
from .. import input as inputlib
from .. import monitors as monitorslib
from .. import policy
from .. import safety as safetylib
from .. import screencast as screencastlib
from .. import uitree as uitreelib
from ..capabilities import Capability, CapabilityEvidence, CapabilityState
from ..errors import DesktopError, ErrorCategory, ErrorCode


_T = TypeVar("_T")


class PythonDesktopStateProvider:
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
_INSTALL_HINTS: tuple[tuple[str, str], ...] = (
    ("clipboard.", "wl-clipboard is not installed: sudo apt install wl-clipboard"),
    ("capture.window", "needs gnome-screenshot: sudo apt install gnome-screenshot"),
    ("capture.", "screen sharing needs GStreamer's PipeWire plugin and PyGObject: "
                 "sudo apt install gstreamer1.0-pipewire python3-gi gir1.2-gst-plugins-base-1.0"),
    ("accessibility.", "needs the AT-SPI bindings: sudo apt install python3-gi gir1.2-atspi-2.0"),
    ("window.", "needs the AT-SPI bindings: sudo apt install python3-gi gir1.2-atspi-2.0"),
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
            hint for prefix, hint in _INSTALL_HINTS if name.startswith(prefix)
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
    exc: uitreelib.UiTreeError,
    default: ErrorCode,
    category: ErrorCategory,
    retryable: bool,
    backend: str = "linux.atspi",
) -> DesktopError:
    code = exc.code or default
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


def _input_boundary(method_name: str, capability_name: str) -> Callable[..., Any]:
    legacy_method = getattr(inputlib.InputBackend, method_name)

    @wraps(legacy_method)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        return self._translate(
            lambda: getattr(inputlib.InputBackend, method_name)(
                self,
                *args,
                **kwargs,
            ),
            capability_name,
        )

    return wrapped


def _accessibility_boundary(
    method_name: str,
    *,
    code: ErrorCode,
    category: ErrorCategory,
    retryable: bool,
) -> Callable[..., Any]:
    legacy_method = getattr(uitreelib.UiTree, method_name)

    @wraps(legacy_method)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        return self._translate_accessibility(
            lambda: getattr(uitreelib.UiTree, method_name)(
                self,
                *args,
                **kwargs,
            ),
            code=code,
            category=category,
            retryable=retryable,
        )

    return wrapped


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
    """`clipboard.read` and `clipboard.write` for the wl-clipboard programs.

    Both backends run the same programs, so both answer the same way: nothing
    is run, the program on `PATH` and the Wayland socket are enough. The one
    limitation belongs to the write, since it is the restore that loses the
    other representations.
    """
    values: dict[str, Capability] = {}
    for name, command in (
        ("clipboard.read", "wl-paste"),
        ("clipboard.write", "wl-copy"),
    ):
        command_path = shutil.which(command)
        available = bool(command_path and _wayland_socket())
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


class PythonCaptureProvider:
    """Own the legacy ScreenCast handle and expose capture as one provider."""

    def __init__(
        self,
        cfg: Config,
        screencast: Any | None = None,
        *,
        degraded_reason: str = "",
    ) -> None:
        self.cfg = cfg
        self.screencast = (
            screencast if screencast is not None else screencastlib.ScreenCast()
        )
        # Set when the selector wanted the native backend and could not have it.
        # Carried into the capability report so a fallback is something the
        # client can read, not something it has to infer from timings.
        self.degraded_reason = degraded_reason

    def capability_token(self) -> tuple[Any, ...]:
        """Dependency, session, and topology state without starting capture."""
        # Duzen kimligi TEK yerde hesaplaniyor (`monitors.topology_id`), cunku
        # buradaki ikinci bir kopya `transform` gibi bir alan eklendiginde
        # sessizce eskir ve onbellek bayat kalirdi.
        topology: tuple[Any, ...]
        try:
            topology = (
                monitorslib.topology_id(monitorslib.list_monitors(use_cache=False)),
            )
        except monitorslib.MonitorError as exc:
            topology = ("error", type(exc).__name__)
        return (
            self.cfg.desktop.capture_backend,
            capturelib.PIL_AVAILABLE,
            shutil.which(capturelib.GNOME_SCREENSHOT),
            screencastlib.available()[0],
            self.screencast.is_open(),
            topology,
        )

    def probe_capabilities(self) -> dict[str, Capability]:
        """Report monitor and window capture separately without opening a session."""
        pillow_ok = capturelib.PIL_AVAILABLE
        screenshot_ok = bool(shutil.which(capturelib.GNOME_SCREENSHOT))
        screencast_ok, _screencast_reason = screencastlib.available()
        requested = self.cfg.desktop.capture_backend

        if not pillow_ok:
            monitor = _capability(
                "capture.monitor",
                CapabilityState.UNAVAILABLE,
                backend="linux.python.capture",
                scope="os.capture",
                reason_code=ErrorCode.DEPENDENCY_MISSING,
                limitations=(
                    "Pillow is required to crop and scale frames; reinstall pcbridge "
                    "(`pcbridge update`) so its environment has it.",
                ),
            )
        elif requested == "gnome-screenshot":
            monitor = self._screenshot_capability("capture.monitor", screenshot_ok)
        elif self.screencast.is_open() or screencast_ok:
            monitor = _capability(
                "capture.monitor",
                CapabilityState.SUPPORTED,
                backend="linux.gnome-screencast",
                scope="os.capture",
            )
        elif requested == "auto" and screenshot_ok:
            monitor = _capability(
                "capture.monitor",
                CapabilityState.DEGRADED,
                backend="linux.gnome-screenshot",
                scope="os.capture",
                reason_code=ErrorCode.BACKEND_UNAVAILABLE,
                limitations=(
                    "The ScreenCast backend is unavailable; capture uses a visible flash.",
                ),
            )
        else:
            monitor = _capability(
                "capture.monitor",
                CapabilityState.UNAVAILABLE,
                backend="linux.gnome-screencast",
                scope="os.capture",
                reason_code=ErrorCode.DEPENDENCY_MISSING,
            )

        try:
            monitorslib.list_monitors()
        except monitorslib.MonitorError:
            monitor = _capability(
                "capture.monitor",
                CapabilityState.UNAVAILABLE,
                backend=compositorlib.current().display_backend,
                scope="os.capture",
                reason_code=ErrorCode.DISPLAY_MAPPING_UNKNOWN,
            )

        if self.degraded_reason:
            monitor = _capability(
                monitor.name,
                CapabilityState.DEGRADED
                if monitor.state is CapabilityState.SUPPORTED
                else monitor.state,
                backend=monitor.backend,
                scope=monitor.scope,
                reason_code=monitor.reason_code or ErrorCode.BACKEND_UNAVAILABLE,
                limitations=tuple(monitor.limitations) + (self.degraded_reason,),
            )

        window = self._screenshot_capability(
            "capture.window",
            pillow_ok and screenshot_ok,
        )
        return {"capture.monitor": monitor, "capture.window": window}

    @staticmethod
    def _screenshot_capability(name: str, available: bool) -> Capability:
        return _capability(
            name,
            CapabilityState.SUPPORTED if available else CapabilityState.UNAVAILABLE,
            backend="linux.gnome-screenshot",
            scope="os.capture",
            reason_code=None if available else ErrorCode.DEPENDENCY_MISSING,
            limitations=("Window captures do not include a global position.",)
            if name == "capture.window" and available
            else (),
        )

    def start(self, *, cursor: bool | None = None) -> dict:
        try:
            monitors = [monitor.connector for monitor in self.list_monitors()]
            return self.screencast.start(
                monitors,
                cursor=self.cfg.desktop.include_pointer if cursor is None else cursor,
            )
        except DesktopError:
            raise
        except screencastlib.ScreenCastError as exc:
            raise _desktop_error(
                exc,
                code=ErrorCode.BACKEND_UNAVAILABLE,
                category=ErrorCategory.CAPABILITY,
                backend="linux.gnome-screencast",
                retryable=True,
                suggested_action="Close the screen share and open it again.",
            ) from exc

    def close(self) -> None:
        self.screencast.close()

    def is_open(self) -> bool:
        return self.screencast.is_open()

    def available(self) -> tuple[bool, str]:
        return capturelib.available(self.screencast)

    def backend_name(self) -> str:
        return capturelib.backend_name(self.screencast)

    def capture(
        self,
        spec: int | str,
        *,
        out_dir: Path,
        scale_long_edge: int,
        include_pointer: bool,
        copy_meta_to: Sequence[Path] = (),
        reserved_dirs: Sequence[Path] = (),
        region: Any = None,
    ) -> list[capturelib.Shot]:
        try:
            return capturelib.capture(
                spec,
                out_dir=out_dir,
                scale_long_edge=scale_long_edge,
                include_pointer=include_pointer,
                screencast=self.screencast,
                copy_meta_to=copy_meta_to,
                reserved_dirs=reserved_dirs,
                region=region,
            )
        except capturelib.ShotLayoutChanged as exc:
            raise _desktop_error(
                exc,
                code=ErrorCode.DISPLAY_CHANGED,
                category=ErrorCategory.COORDINATE,
                backend="linux.python.capture",
                retryable=True,
                suggested_action="Choose the region again and retry.",
            ) from exc
        except capturelib.CaptureError as exc:
            raise _desktop_error(
                exc,
                code=ErrorCode.BACKEND_UNAVAILABLE,
                category=ErrorCategory.CAPABILITY,
                backend="linux.python.capture",
                retryable=True,
                suggested_action="Check the screen capture dependencies and the session.",
            ) from exc
        except monitorslib.MonitorError as exc:
            raise _display_mapping_error(exc) from exc

    def list_monitors(self) -> list[monitorslib.Monitor]:
        try:
            return monitorslib.list_monitors()
        except monitorslib.MonitorError as exc:
            raise _display_mapping_error(exc) from exc

    def topology_id(self) -> str:
        """Ekran duzeninin kararli kimligi — Rust tarafiyla BIREBIR ayni dize.

        Acik bir capture oturumunun ya da onbellege alinmis bir goruntunun
        hala gecerli olup olmadigini soran katman bunu karsilastiriyor. Kural
        `monitors.topology_id`te; burasi yalnizca saglayici sinirindan gecirip
        hatayi typed hale getiriyor.
        """
        try:
            return monitorslib.topology_id(monitorslib.list_monitors())
        except monitorslib.MonitorError as exc:
            raise _display_mapping_error(exc) from exc

    def describe_monitors(self) -> str:
        try:
            return monitorslib.describe()
        except monitorslib.MonitorError as exc:
            raise _display_mapping_error(exc) from exc

    def find_monitor(self, x: int, y: int) -> monitorslib.Monitor | None:
        try:
            return monitorslib.find_monitor(x, y)
        except monitorslib.MonitorError as exc:
            raise _display_mapping_error(exc) from exc

    def to_global(
        self,
        x: int,
        y: int,
        *,
        monitor: int | str | None = None,
        shot: str | None = None,
        dirs: Sequence[Path] | None = None,
        guard_age: float = 0.0,
    ) -> tuple[int, int]:
        if shot and monitor is not None:
            code = ErrorCode.AMBIGUOUS_COORDINATE
        elif shot and not capturelib.SHOT_ID_RE.match(shot):
            code = ErrorCode.SHOT_INVALID
        elif shot:
            code = ErrorCode.SHOT_NOT_FOUND
        elif guard_age > 0 and monitor is None:
            code = ErrorCode.AMBIGUOUS_COORDINATE
        else:
            code = ErrorCode.DISPLAY_MAPPING_UNKNOWN
        try:
            return capturelib.to_global(
                x,
                y,
                monitor=monitor,
                shot=shot,
                dirs=dirs,
                guard_age=guard_age,
            )
        except capturelib.ShotLayoutChanged as exc:
            raise _desktop_error(
                exc,
                code=ErrorCode.DISPLAY_CHANGED,
                category=ErrorCategory.COORDINATE,
                backend="python.shot-coordinate",
                retryable=True,
                suggested_action="Take a new screenshot and use its id.",
            ) from exc
        except capturelib.CaptureError as exc:
            raise _desktop_error(
                exc,
                code=code,
                category=ErrorCategory.COORDINATE,
                backend="python.shot-coordinate",
                retryable=code
                in {ErrorCode.SHOT_NOT_FOUND, ErrorCode.AMBIGUOUS_COORDINATE},
                suggested_action=(
                    "Use a fresh shot id or an explicit monitor."
                ),
            ) from exc
        except monitorslib.MonitorError as exc:
            raise _display_mapping_error(exc) from exc

    def resolve_region(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        *,
        monitor: int | str | None = None,
        shot: str | None = None,
        dirs: Sequence[Path] | None = None,
    ) -> Any:
        """Bolgeyi global tuval kutusuna cevir (Adim 8.5), typed hatayla.

        Hata kodlari `to_global`inkilerle ayni secimle: ayni uc uzay, ayni
        hatalar.
        """
        if shot and monitor is not None:
            code = ErrorCode.AMBIGUOUS_COORDINATE
        elif shot and not capturelib.SHOT_ID_RE.match(shot):
            code = ErrorCode.SHOT_INVALID
        elif shot:
            code = ErrorCode.SHOT_NOT_FOUND
        else:
            code = ErrorCode.DISPLAY_MAPPING_UNKNOWN
        try:
            return capturelib.resolve_region(
                x, y, width, height, monitor=monitor, shot=shot, dirs=dirs
            )
        except capturelib.ShotLayoutChanged as exc:
            raise _desktop_error(
                exc,
                code=ErrorCode.DISPLAY_CHANGED,
                category=ErrorCategory.COORDINATE,
                backend="python.shot-coordinate",
                retryable=True,
                suggested_action="Take a new screenshot and use its id.",
            ) from exc
        except capturelib.CaptureError as exc:
            raise _desktop_error(
                exc,
                code=code,
                category=ErrorCategory.COORDINATE,
                backend="python.shot-coordinate",
                retryable=code == ErrorCode.SHOT_NOT_FOUND,
                suggested_action="Give the region inside one monitor, in the right coordinate space.",
            ) from exc
        except monitorslib.MonitorError as exc:
            raise _display_mapping_error(exc) from exc

    def load_shot(
        self, shot_id: str, dirs: Sequence[Path]
    ) -> capturelib.Shot:
        code = (
            ErrorCode.SHOT_INVALID
            if not capturelib.SHOT_ID_RE.match(shot_id or "")
            else ErrorCode.SHOT_NOT_FOUND
        )
        try:
            return capturelib.load_shot(shot_id, dirs)
        except capturelib.CaptureError as exc:
            raise _desktop_error(
                exc,
                code=code,
                category=ErrorCategory.COORDINATE,
                backend="python.shot-coordinate",
                retryable=code == ErrorCode.SHOT_NOT_FOUND,
                suggested_action="Take a fresh screenshot and use its id exactly.",
            ) from exc

    def save_meta(
        self, shot: capturelib.Shot, out_dir: Path | None = None
    ) -> Path | None:
        return capturelib.save_meta(shot, out_dir)

    def oversize_note(self, shot: capturelib.Shot) -> str:
        return capturelib.oversize_note(shot)

    def kill_helpers(self) -> int:
        return screencastlib.kill_helpers()


class PythonInputProvider(inputlib.InputBackend):
    """Configure the existing lazy input backend from one Config object."""

    def __init__(self, cfg: Config, *, degraded_reason: str = "") -> None:
        super().__init__(
            pointer_speed=cfg.desktop.pointer_speed,
            pointer_max_ms=cfg.desktop.pointer_move_max_ms,
            hold_max_seconds=cfg.desktop.hold_max_seconds,
            pos_file=cfg.pointer_pos_file,
            click_hold_ms=cfg.desktop.click_hold_ms,
        )
        # Set when `[native] input = "auto"` wanted the helper and fell back.
        self.degraded_reason = degraded_reason

    def capability_token(self) -> tuple[Any, ...]:
        try:
            stat = os.stat(inputlib.UINPUT_NODE)
            device = (stat.st_dev, stat.st_ino, stat.st_mode)
        except OSError as exc:
            device = (type(exc).__name__, getattr(exc, "errno", None))
        return (
            inputlib.EVDEV_AVAILABLE,
            device,
            os.access(inputlib.UINPUT_NODE, os.W_OK),
            shutil.which("wl-copy"),
            shutil.which("wl-paste"),
            _wayland_socket(),
        )

    def _availability(self) -> tuple[bool, str, ErrorCode | None]:
        # This method describes the Python uinput path specifically. Calling
        # `self.available()` would let a hybrid provider's native keyboard
        # readiness incorrectly hide its still-Python pointer capability.
        ok, reason = inputlib.InputBackend.available(self)
        if ok:
            return True, reason, None
        if not inputlib.EVDEV_AVAILABLE or not os.path.exists(inputlib.UINPUT_NODE):
            return False, reason, ErrorCode.DEPENDENCY_MISSING
        if not os.access(inputlib.UINPUT_NODE, os.W_OK):
            return False, reason, ErrorCode.DEVICE_NOT_GRANTED
        return False, reason, ErrorCode.BACKEND_UNAVAILABLE

    def probe_capabilities(self) -> dict[str, Capability]:
        ok, _reason, code = self._availability()
        state = CapabilityState.SUPPORTED if ok else _state_for(code)
        values = {
            name: _capability(
                name,
                state,
                backend="linux.uinput",
                scope=scope,
                reason_code=None if ok else code,
            )
            for name, scope in (
                ("input.pointer", "os.pointer"),
                # Ayri bir cihaz ama AYNI kapi ve ayni grant: cogalan yalnizca
                # yetenek adi, izin kapsami degil (Adim 7).
                ("input.pointer_relative", "os.pointer"),
                ("input.keyboard", "os.keyboard"),
            )
        }
        if self.degraded_reason:
            # A fallback is shown, never hidden, as capture shows its own.
            for name in ("input.pointer", "input.pointer_relative",
                         "input.keyboard"):
                value = values[name]
                values[name] = _capability(
                    name,
                    CapabilityState.DEGRADED
                    if value.state is CapabilityState.SUPPORTED
                    else value.state,
                    backend=value.backend,
                    scope=value.scope,
                    reason_code=value.reason_code or ErrorCode.BACKEND_UNAVAILABLE,
                    limitations=tuple(value.limitations) + (self.degraded_reason,),
                )
        values.update(_clipboard_capabilities("linux.wl-clipboard"))
        return values

    def _translate(self, operation: Callable[[], _T], capability_name: str) -> _T:
        try:
            return operation()
        except DesktopError:
            raise
        except inputlib.InputError as exc:
            ok, _reason, reason_code = self._availability()
            code = reason_code if not ok and reason_code else ErrorCode.EXECUTION_UNKNOWN
            category = (
                ErrorCategory.PERMISSION
                if code
                in {
                    ErrorCode.PERMISSION_REQUIRED,
                    ErrorCode.PERMISSION_DENIED,
                    ErrorCode.DEVICE_NOT_GRANTED,
                }
                else ErrorCategory.CAPABILITY
                if code
                in {
                    ErrorCode.BACKEND_UNAVAILABLE,
                    ErrorCode.DEPENDENCY_MISSING,
                    ErrorCode.UNSUPPORTED,
                }
                else ErrorCategory.EXECUTION
            )
            raise _desktop_error(
                exc,
                code=code,
                category=category,
                backend="linux.uinput",
                retryable=code
                not in {ErrorCode.DEPENDENCY_MISSING, ErrorCode.UNSUPPORTED},
                suggested_action=(
                    f"Check the state of {capability_name} and try again."
                ),
                permission_scope=(
                    "os.input" if category == ErrorCategory.PERMISSION else None
                ),
            ) from exc
        except OSError as exc:
            raise _desktop_error(
                exc,
                code=ErrorCode.EXECUTION_UNKNOWN,
                category=ErrorCategory.EXECUTION,
                backend="linux.uinput",
                retryable=False,
                suggested_action=(
                    f"Check the state of {capability_name} and verify the result."
                ),
            ) from exc

    ensure = _input_boundary("ensure", "input")
    move = _input_boundary("move", "input.pointer")
    move_by = _input_boundary("move_by", "input.pointer_relative")
    click = _input_boundary("click", "input.pointer")
    drag = _input_boundary("drag", "input.pointer")
    scroll = _input_boundary("scroll", "input.pointer")
    mouse_down = _input_boundary("mouse_down", "input.pointer")
    mouse_up = _input_boundary("mouse_up", "input.pointer")
    key = _input_boundary("key", "input.keyboard")
    key_down = _input_boundary("key_down", "input.keyboard")
    key_up = _input_boundary("key_up", "input.keyboard")
    type_text = _input_boundary("type_text", "clipboard.write")


class PythonAccessibilityProvider(uitreelib.UiTree):
    """Expose the legacy AT-SPI client through the provider contract."""

    def __init__(self, *, degraded_reason: str = "") -> None:
        super().__init__()
        # Set when `auto` wanted the native reader and could not have it.
        self.degraded_reason = degraded_reason

    def available(self) -> tuple[bool, str]:
        return uitreelib.available()

    def capability_token(self) -> tuple[Any, ...]:
        available, _reason, _code = self._availability()
        return (
            uitreelib.HELPER.exists(),
            shutil.which(uitreelib.SYSTEM_PYTHON),
            available,
        )

    @staticmethod
    def _availability() -> tuple[bool, str, ErrorCode | None]:
        try:
            ok, reason = uitreelib.available()
        except OSError as exc:
            return False, str(exc), ErrorCode.BACKEND_UNAVAILABLE
        if ok:
            return True, reason, None
        if not uitreelib.HELPER.exists() or not shutil.which(
            uitreelib.SYSTEM_PYTHON
        ):
            return False, reason, ErrorCode.DEPENDENCY_MISSING
        return False, reason, ErrorCode.BACKEND_UNAVAILABLE

    def probe_capabilities(self) -> dict[str, Capability]:
        ok, _reason, code = self._availability()
        state = CapabilityState.SUPPORTED if ok else _state_for(code)
        values = {
            name: _capability(
                name,
                state,
                backend="linux.atspi",
                scope="os.accessibility",
                reason_code=None if ok else code,
            )
            for name in ("accessibility.read", "accessibility.action")
        }
        values["window.list"] = _capability(
            "window.list",
            CapabilityState.DEGRADED if ok else state,
            backend="linux.atspi",
            scope="os.accessibility",
            reason_code=None if ok else code,
            limitations=("Only accessibility-visible applications are listed.",)
            if ok
            else (),
        )
        if self.degraded_reason:
            # A fallback is shown, never hidden, as input and capture show theirs.
            for name in ("accessibility.read", "window.list"):
                value = values[name]
                values[name] = _capability(
                    name,
                    CapabilityState.DEGRADED
                    if value.state is CapabilityState.SUPPORTED
                    else value.state,
                    backend=value.backend,
                    scope=value.scope,
                    reason_code=value.reason_code or ErrorCode.BACKEND_UNAVAILABLE,
                    limitations=tuple(value.limitations) + (self.degraded_reason,),
                )
        return values

    def _translate_accessibility(
        self,
        operation: Callable[[], _T],
        *,
        code: ErrorCode,
        category: ErrorCategory,
        retryable: bool,
    ) -> _T:
        """Run a legacy call and turn its failure into the shared taxonomy.

        The helper states its own reason as a stable code; a failure without
        one gets the caller's default. The message is never parsed.
        """
        try:
            return operation()
        except uitreelib.UiTreeError as exc:
            raise _accessibility_error(exc, code, category, retryable) from exc

    dump = _accessibility_boundary(
        "dump",
        code=ErrorCode.BACKEND_UNAVAILABLE,
        category=ErrorCategory.CAPABILITY,
        retryable=True,
    )
    focused_window = _accessibility_boundary(
        "focused_window",
        code=ErrorCode.BACKEND_UNAVAILABLE,
        category=ErrorCategory.CAPABILITY,
        retryable=True,
    )
    windows = _accessibility_boundary(
        "windows",
        code=ErrorCode.BACKEND_UNAVAILABLE,
        category=ErrorCategory.CAPABILITY,
        retryable=True,
    )

    def click(self, node_id: str, action: str = "click") -> dict:
        return self._translate_accessibility(
            lambda: super(PythonAccessibilityProvider, self).click(node_id, action),
            code=ErrorCode.ACTION_UNSUPPORTED,
            category=ErrorCategory.ACCESSIBILITY,
            retryable=False,
        )

    def set_text(self, node_id: str, text: str) -> dict:
        node = self._translate_accessibility(
            lambda: self.resolve(node_id),
            code=ErrorCode.ELEMENT_STALE,
            category=ErrorCategory.ACCESSIBILITY,
            retryable=True,
        )
        # Icerik kapisi, izin kapisi degil: `SafetyGate` gecse de burasi
        # reddeder ve `force` ile asilamaz (docs/dev/desktop-rules.md §4 item 7).
        policy.check_text_target(role=node.role, name=node.name)
        return self._translate_accessibility(
            lambda: super(PythonAccessibilityProvider, self).set_text(node_id, text),
            code=ErrorCode.TARGET_MISMATCH,
            category=ErrorCategory.ACCESSIBILITY,
            retryable=False,
        )

    def describe_dump(self, dump: uitreelib.Dump) -> str:
        return uitreelib.describe(dump)

    def describe_windows(self, windows: list[uitreelib.Window]) -> str:
        return uitreelib.describe_windows(windows)


__all__ = [
    "PythonAccessibilityProvider",
    "PythonCaptureProvider",
    "PythonInputProvider",
]
