"""The desktop providers: capture, input and accessibility through the native helper.

The native helper (`pcbridge-native`, Rust) is the only desktop backend: it
captures the screen, drives uinput and reads and acts on AT-SPI. The Python
paths that once filled in where it was missing (the GStreamer screen share,
python-evdev, the system-python AT-SPI helper and gnome-screenshot) are gone;
without the helper, desktop control is refused with what fixes it.

WHAT STAYS IN PYTHON
    Only the acquisition of the raw frame happens in the helper. Cropping,
    scaling, the PNG that reaches the client, the shot id, the two lookup
    directories, the metadata record and every coordinate conversion stay in
    `capture.py`. That is deliberate: the shot id is how a later
    `mouse(shot=...)` call finds the offset and scale of an image, and
    moving that bookkeeping into a second language is how two copies drift
    and a click lands on the wrong screen. `NativeScreenCast` is the handle
    `capture.py` takes frames from.

KEYBOARD, POINTER AND CLIPBOARD
    Keyboard and already-resolved global pointer coordinates go to the
    helper; shot and monitor identities never cross that boundary. The
    clipboard programs run in the helper too (`NativeClipboard`), but the
    typing orchestration -- save, put, paste, restore -- stays in
    `InputBackend._type_clipboard`.

ACCESSIBILITY
    The dump, the window list and the focused window come from the helper,
    which reads AT-SPI over D-Bus with no GI and no GTK. Short ids, the
    last-dump record and the text shown to the model stay in `uitree`.

THE SHARING INDICATOR APPEARS WITH THE GRANT
    `start()` asks the helper for `capture.session_open`, which opens the
    Mutter session without reading a frame, at `desktop_unlock`; the session
    stays open until revoke, lock or expiry. The indicator is the user's
    evidence that an agent can see the screen.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ..capabilities import Capability, CapabilityState
from ..errors import DesktopError, ErrorCategory, ErrorCode, error_from_decision
from ..lease import LeaseToken
from .. import capture as capturelib
from .. import compositor as compositorlib
from .. import clipboard as clipboardlib
from .. import input as inputlib
from .. import monitors as monitorslib
from .. import policy
from .. import uitree as uitreelib
from .common import (
    _accessibility_error,
    _capability,
    _clipboard_capabilities,
    _desktop_error,
    _display_mapping_error,
    _gnome_frame_support,
    _gnome_window_capability,
    _wayland_socket,
)
from ...config import Config
from ...native import NativeClient, discover_native_binary

#: Connectors are addressed with an explicit scheme so that a native backend
#: for one compositor cannot be handed an id it would misread: `mutter:` on
#: GNOME, `kwin:` on KDE Plasma (the helper refuses the other one).
DISPLAY_SCHEME = "mutter"
KWIN_DISPLAY_SCHEME = "kwin"
X11_DISPLAY_SCHEME = "x11"

#: Matches the native side's own ceiling. A frame that has not arrived in eight
#: seconds is not coming, and one MCP call may not block for 110.
FRAME_TIMEOUT_MS = 8000

#: A clipboard read runs two programs, each allowed 10 s by the helper.
CLIPBOARD_TIMEOUT_SECONDS = 25.0

BACKEND_NAME = "linux.mutter.pipewire"
KWIN_BACKEND_NAME = "linux.kwin.screenshot2"
HYPRLAND_BACKEND_NAME = "linux.hyprland.image-copy"
X11_BACKEND_NAME = "linux.x11.getimage"


def _display_scheme() -> str:
    kind = compositorlib.current().kind
    return {"kde": KWIN_DISPLAY_SCHEME, "gnome": DISPLAY_SCHEME,
            "cinnamon": X11_DISPLAY_SCHEME, "xfce": X11_DISPLAY_SCHEME}.get(kind, kind)


def _backend_name() -> str:
    kind = compositorlib.current().kind
    return {"kde": KWIN_BACKEND_NAME, "gnome": BACKEND_NAME,
            "hyprland": HYPRLAND_BACKEND_NAME, "cinnamon": X11_BACKEND_NAME,
            "xfce": X11_BACKEND_NAME}.get(kind, "unavailable")

logger = logging.getLogger(__name__)


class NativeCaptureError(RuntimeError):
    """Raised where the legacy handle would raise, carrying the typed cause."""

    def __init__(self, message: str, *, cause: DesktopError | None = None) -> None:
        super().__init__(message)
        self.desktop_error = cause


def runtime_dir() -> Path:
    """Where the helper registry lives.

    `$XDG_RUNTIME_DIR` for the same reason screenshots go there: it is readable
    only by this user and disappears with the session.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    return Path(runtime) if runtime else Path("/tmp/pcb")


def native_binary_ready(cfg: Config) -> tuple[bool, str]:
    """Is a native helper reachable, without starting one?

    Discovery only: no process, no session bus, no screen share. A capability
    query must stay cheap enough to answer on every tool call.
    """
    try:
        discover_native_binary(
            cfg.native,
            package_root=Path(__file__).resolve().parents[2],
        )
    except DesktopError as exc:
        return False, exc.message
    except Exception as exc:  # noqa: BLE001 - discovery reports its own reasons
        return False, str(exc)
    return True, ""


class GrantBoundHelper:
    """One native helper per desktop grant, replaced when the grant changes.

    The helper binds the grant it reads at `initialize` and never rebinds
    (`native_revoke.rs`: `revoke_releases_resource_and_session_never_rebinds`),
    while every `desktop_unlock` writes a new grant id -- in this process or in
    any other. A helper kept across that answers REVOKED to every later
    request. Measured 2026-09-19 with the packaged helper: a second
    `desktop_unlock` while the grant was still open closed the share and left
    capture REVOKED until `desktop_lock`; native input stayed REVOKED after a
    second unlock, after an expiry, and after a second `desktop_lock`.

    The grant a helper serves is the one its first request named. If another
    process changes the grant between that read and the helper's `initialize`,
    the request is refused as REVOKED and the next one gets a fresh helper:
    refused, never sent under the wrong grant.
    """

    def __init__(
        self,
        create: Callable[[], NativeClient],
        client: NativeClient | None = None,
    ) -> None:
        self._create = create
        self._client = client
        self._grant: tuple[str, int] | None = None
        self._lock = threading.RLock()

    @property
    def client(self) -> NativeClient | None:
        """The live helper, if any, without starting or replacing one."""
        return self._client

    def for_grant(
        self,
        grant: tuple[str, int],
        retire: Callable[[NativeClient], None] | None = None,
    ) -> NativeClient:
        """The helper for `grant`, stopping the previous one if it changed.

        `retire` runs while the old helper is still the current one, so the
        owner can release through it. Its failure is logged, not raised: that
        helper's watchdog already failed closed when the grant changed, and a
        cleanup error must not block the request made under the new grant.
        """
        with self._lock:
            stale = self._client
            if stale is not None and self._grant is not None and self._grant != grant:
                try:
                    if retire is not None:
                        retire(stale)
                except Exception as exc:  # noqa: BLE001 - see docstring
                    logger.warning("superseded native helper cleanup failed: %s", exc)
                finally:
                    self._client = None
                    self._grant = None
                    stale.close()
            if self._client is None:
                self._client = self._create()
            self._grant = grant
            return self._client

    def close(self) -> None:
        with self._lock:
            client, self._client = self._client, None
            self._grant = None
        if client is not None:
            client.close()


class NativeScreenCast:
    """The screen-share handle `capture.py` takes frames from, served by the helper.

    `is_open`, `start`, `ensure_cursor`, `capture(connector, path)` and
    `close` are all `capture.py` needs; the frames come from the grant-bound
    native helper.
    """

    def __init__(
        self,
        cfg: Config,
        *,
        gate: Any | None = None,
        client: NativeClient | None = None,
        client_factory: Callable[[], NativeClient] | None = None,
    ) -> None:
        self.cfg = cfg
        self.gate = gate
        self._helper = GrantBoundHelper(client_factory or self._new_client, client)
        self._open = False
        self._cursor = bool(cfg.desktop.include_pointer)
        self._monitors: list[str] = []
        self._session_id = f"pcb-{secrets.token_hex(6)}"

    # ------------------------------------------------------------- helper
    @property
    def _client(self) -> NativeClient | None:
        """The live helper, if any; the live parity test reads its pid."""
        return self._helper.client

    def _new_client(self) -> NativeClient:
        try:
            binary = discover_native_binary(
                self.cfg.native,
                package_root=Path(__file__).resolve().parents[2],
            )
        except DesktopError as exc:
            raise NativeCaptureError(exc.message, cause=exc) from exc
        except Exception as exc:  # noqa: BLE001
            raise NativeCaptureError(f"native helper not found: {exc}") from exc
        return NativeClient(
            binary,
            state_dir=self.cfg.state_dir,
            runtime_dir=runtime_dir(),
        )

    def _ensure_client(self, grant: tuple[str, int]) -> NativeClient:
        return self._helper.for_grant(grant)

    def _grant(self) -> tuple[str, int]:
        """The grant snapshot this capture belongs to.

        The native helper binds one grant at `initialize` and refuses any
        request naming a different one. The admitted call's captured identity
        wins over a parallel replacement. Outside an admitted call, use the
        current lease; native validation still decides whether it is valid.
        """
        token = None
        if self.gate is not None:
            token = self.gate.last_token() or self.gate.current_token()
        if token is None:
            # A missing grant is a safety refusal, not a broken backend. Left
            # untyped it surfaced as BACKEND_UNAVAILABLE/capability (measured
            # 2026-09-13, Task 4.2, for a revoked and for an expired grant),
            # which points an agent at the helper instead of `desktop_unlock`.
            refusal = DesktopError(
                code=ErrorCode.GRANT_REQUIRED,
                message="no desktop grant: native capture cannot be requested without a grant id",
                category=ErrorCategory.SAFETY,
                retryable=False,
                suggested_action="Open the desktop grant with desktop_unlock and try again.",
                backend=_backend_name(),
            )
            raise NativeCaptureError(refusal.message, cause=refusal)
        return str(token.grant_id), int(token.revoke_epoch)

    # -------------------------------------------------------------- shape
    def is_open(self) -> bool:
        return self._open

    def publication_guard(self, spec=None):
        """Capture one exact Hyprland token before rendering any shot."""
        if compositorlib.current().kind != "hyprland":
            return None
        grant_id, epoch = self._grant()
        token = LeaseToken(grant_id, epoch)
        table = monitorslib.list_monitors(use_cache=False)
        topology = monitorslib.topology_id(table)
        outputs = [monitor.connector for monitor in table]
        verify_window = None
        if isinstance(spec, str) and spec.strip().lower() == "window":
            from ..hyprland_windows import capture_region

            _, verify_window = capture_region(table)
        def verify():
            decision = self.gate.verify(token)
            if not decision.allowed:
                raise error_from_decision(decision)
            current = monitorslib.list_monitors()
            if (monitorslib.topology_id(current) != topology
                    or [monitor.connector for monitor in current] != outputs):
                raise DesktopError(code=ErrorCode.DISPLAY_CHANGED, category=ErrorCategory.CAPTURE,
                    message="The output layout changed while the screenshot was being prepared",
                    retryable=True, suggested_action="Capture the current layout again.", backend=HYPRLAND_BACKEND_NAME)
            if verify_window:
                verify_window()
        return verify

    def start(self, monitors: list[str], cursor: bool = True) -> dict:
        """Open the Mutter session now, so the share starts with the grant.

        Until Task 4.3 this only marked the handle usable and the session
        opened at the first capture, which left a stretch after
        `desktop_unlock` with a grant and no sharing indicator. The indicator
        is the user's evidence that an agent can see the screen, so it now
        appears with the grant. No frame is read. A helper from before Task
        4.3 answers UNKNOWN_METHOD and keeps opening on demand.
        """
        self._monitors = [str(name) for name in monitors]
        self._cursor = bool(cursor)
        grant_id, revoke_epoch = self._grant()
        table = monitorslib.list_monitors()
        client = self._ensure_client((grant_id, revoke_epoch))
        response = client.request(
            "capture.session_open",
            {
                "topology_id": monitorslib.topology_id(table),
                "session_id": self._session_id,
                "grant_id": grant_id,
                "revoke_epoch": revoke_epoch,
                "include_pointer": self._cursor,
            },
            # CreateSession, RecordMonitor per monitor and Start, each bounded
            # by the helper's own 10 s D-Bus call timeout.
            timeout=45.0,
        )
        outcome = ""
        on_demand = False
        if response.error:
            if (response.error.get("code") != "UNKNOWN_METHOD"
                    or compositorlib.current().kind == "hyprland"):
                raise NativeCaptureError(
                    str(response.error.get("message") or response.error.get("code")),
                    cause=_error_from_response(response.error),
                )
            on_demand = True
        else:
            result = response.result if isinstance(response.result, dict) else {}
            outcome = str(result.get("outcome") or "")
        self._open = True
        return {
            "already": outcome == "reused",
            "monitors": list(self._monitors),
            "on_demand": on_demand,
            "outcome": outcome,
        }

    def stop(self) -> None:
        self._open = False
        self._monitors = []

    def close(self) -> None:
        self.stop()
        self._helper.close()

    def ensure_cursor(self, cursor: bool) -> bool:
        """Record the pointer mode; the native session recreates itself for it."""
        changed = bool(cursor) != self._cursor
        self._cursor = bool(cursor)
        return changed

    def monitors(self) -> list[str]:
        return list(self._monitors)

    # ------------------------------------------------------------ capture
    def capture(self, connector: str, path: str | Path) -> dict:
        """One frame for one connector, written to `path` as PNG bytes."""
        if not self._open:
            raise NativeCaptureError(
                "native capture is not ready (it opens when the desktop grant is given)"
            )
        grant_id, revoke_epoch = self._grant()
        # The same monitor table `capture.py` selected the target from. The
        # helper refuses a topology that no longer matches, so a layout change
        # between the two reads ends in DISPLAY_CHANGED rather than in a frame
        # from the wrong screen.
        table = monitorslib.list_monitors()
        topology = monitorslib.topology_id(table)
        expected = next((m for m in table if m.connector == connector), None)

        client = self._ensure_client((grant_id, revoke_epoch))
        started = time.time()
        response = client.request(
            "capture.frame",
            {
                "display_id": f"{_display_scheme()}:{connector}",
                "topology_id": topology,
                "session_id": self._session_id,
                "grant_id": grant_id,
                "revoke_epoch": revoke_epoch,
                "timeout_ms": FRAME_TIMEOUT_MS,
                "freshness": "after_request",
                "include_pointer": self._cursor,
            },
            timeout=(FRAME_TIMEOUT_MS / 1000.0) + 5.0,
        )
        if response.error:
            raise NativeCaptureError(
                str(response.error.get("message") or response.error.get("code")),
                cause=_error_from_response(response.error),
            )

        result = response.result if isinstance(response.result, dict) else {}
        pixels = result.get("pixel_size")
        if expected is not None and isinstance(pixels, list) and len(pixels) == 2:
            # Rule 8: an unexpected stream size is reported, never rescaled
            # into place. A silent scale here would put every later click a
            # proportional distance away from where the agent aimed. Since
            # Task 7.1 the monitor's own pixel size (logical times scale) is
            # accepted as well, and the shot record carries both sizes.
            try:
                capturelib.check_source_size(
                    expected, (int(pixels[0]), int(pixels[1]))
                )
            except capturelib.CaptureError as exc:
                raise NativeCaptureError(str(exc)) from exc
        if not response.binary:
            raise NativeCaptureError(f"empty frame returned for {connector}")

        destination = Path(path)
        if compositorlib.current().kind == "hyprland":
            decision = self.gate.verify(LeaseToken(grant_id, revoke_epoch))
            if not decision.allowed:
                raise NativeCaptureError(decision.reason, cause=error_from_decision(decision))
        destination.write_bytes(response.binary)
        wait_ms = float(result.get("wait_ms") or 0.0)
        return {
            "ok": True,
            "path": str(destination),
            "monitor": connector,
            "ms": round((time.time() - started) * 1000),
            # The frame arrived `wait_ms` after we asked, so that -- not the
            # moment the batch began -- is when this image describes the screen.
            "taken_at": started + (wait_ms / 1000.0),
            "frame_identity_source": result.get("frame_identity_source", ""),
            "stale_frames": result.get("stale_frames", 0),
        }


def _error_from_response(error: dict[str, Any]) -> DesktopError:
    code = str(error.get("code") or "")
    mapped = {
        "REVOKED": (ErrorCode.REVOKED, ErrorCategory.SAFETY),
        "GRANT_REQUIRED": (ErrorCode.GRANT_REQUIRED, ErrorCategory.SAFETY),
        "SCREEN_LOCKED": (ErrorCode.SCREEN_LOCKED, ErrorCategory.SAFETY),
        "LOCK_STATE_UNKNOWN": (ErrorCode.LOCK_STATE_UNKNOWN, ErrorCategory.SAFETY),
        "DISPLAY_CHANGED": (ErrorCode.DISPLAY_CHANGED, ErrorCategory.CAPTURE),
        "DISPLAY_MAPPING_UNKNOWN": (
            ErrorCode.DISPLAY_MAPPING_UNKNOWN,
            ErrorCategory.CAPTURE,
        ),
        "FRAME_TIMEOUT": (ErrorCode.FRAME_TIMEOUT, ErrorCategory.CAPTURE),
        "FRAME_FORMAT_UNSUPPORTED": (
            ErrorCode.FRAME_FORMAT_UNSUPPORTED,
            ErrorCategory.CAPTURE,
        ),
        "FRAME_TOO_LARGE": (ErrorCode.FRAME_TOO_LARGE, ErrorCategory.CAPTURE),
        "INVALID_FRAME": (ErrorCode.FRAME_FORMAT_UNSUPPORTED, ErrorCategory.CAPTURE),
        "CANCELLED": (ErrorCode.CANCELLED, ErrorCategory.EXECUTION),
        "BUSY": (ErrorCode.BUSY, ErrorCategory.EXECUTION),
    }.get(code, (ErrorCode.BACKEND_UNAVAILABLE, ErrorCategory.CAPABILITY))
    return DesktopError(
        code=mapped[0],
        message=str(error.get("message") or code or "native capture failed"),
        category=mapped[1],
        retryable=bool(error.get("retryable", False)),
        suggested_action="check_desktop_grant_and_display_layout",
        backend=_backend_name(),
    )


class RustCaptureProvider:
    """Screen capture through the native helper, with the shot bookkeeping in Python.

    The helper delivers raw frames (`NativeScreenCast`); everything else is
    `capture.py`: shot ids, metadata, `to_global`, the two lookup
    directories. There is no other capture path: a share that cannot open,
    or a helper that is missing, fails the call with what fixes it.
    """

    def __init__(
        self,
        cfg: Config,
        *,
        gate: Any | None = None,
        screencast: Any | None = None,
    ) -> None:
        self.cfg = cfg
        self.screencast = (
            screencast if screencast is not None else NativeScreenCast(cfg, gate=gate)
        )

    # ------------------------------------------------------------ session
    def start(self, *, cursor: bool | None = None) -> dict:
        """Open the share; a refusal comes back typed, never as a crash.

        `desktop_unlock` catches DesktopError and reports it. A bare
        NativeCaptureError would escape the tool instead.
        """
        try:
            monitors = [monitor.connector for monitor in self.list_monitors()]
            return self.screencast.start(
                monitors,
                cursor=self.cfg.desktop.include_pointer if cursor is None else cursor,
            )
        except DesktopError:
            raise
        except NativeCaptureError as exc:
            if exc.desktop_error is not None:
                raise exc.desktop_error from exc
            raise _desktop_error(
                exc,
                code=ErrorCode.BACKEND_UNAVAILABLE,
                category=ErrorCategory.CAPABILITY,
                backend=_backend_name(),
                retryable=True,
                suggested_action="Check the native capture helper and the desktop grant.",
            ) from exc

    def close(self) -> None:
        self.screencast.close()

    def is_open(self) -> bool:
        return self.screencast.is_open()

    def capability_token(self) -> tuple[Any, ...]:
        topology: tuple[Any, ...]
        try:
            topology = (
                monitorslib.topology_id(monitorslib.list_monitors(use_cache=False)),
            )
        except monitorslib.MonitorError as exc:
            topology = ("error", type(exc).__name__)
        ready, _reason = native_binary_ready(self.cfg)
        return (
            "rust",
            capturelib.PIL_AVAILABLE,
            ready,
            self.screencast.is_open(),
            topology,
            _gnome_frame_support(),
        )

    def available(self) -> tuple[bool, str]:
        if not capturelib.PIL_AVAILABLE:
            return False, (
                f"the python package `Pillow` is missing ({capturelib.PIL_IMPORT_ERROR}). "
                "Reinstall pcbridge with its [desktop] extra (`pcbridge setup`)."
            )
        return native_binary_ready(self.cfg)

    def backend_name(self) -> str:
        """What the next capture will actually use -- state, not a guess."""
        return _backend_name() if self.screencast.is_open() else "pcbridge-native"

    def _kwin_authorized(self) -> bool:
        from .. import kwin as kwinlib

        try:
            binary = discover_native_binary(
                self.cfg.native, package_root=Path(__file__).resolve().parents[2])
        except Exception:  # noqa: BLE001 — `native_binary_ready` reports it
            return False
        return kwinlib.helper_authorized(binary) is not None

    def probe_capabilities(self) -> dict[str, Capability]:
        """Report monitor capture from the native path without opening a session."""
        kind = compositorlib.current().kind
        if kind not in ("gnome", "kde", "hyprland", "cinnamon", "xfce"):
            return {
                name: _capability(
                    name, CapabilityState.UNAVAILABLE, backend="unavailable",
                    scope="os.capture", reason_code=ErrorCode.BACKEND_UNAVAILABLE,
                    limitations=("No native capture backend is ready for this compositor.",),
                )
                for name in ("capture.monitor", "capture.window")
            }
        pillow_ok = capturelib.PIL_AVAILABLE
        ready, reason = native_binary_ready(self.cfg)
        helper_ready = ready
        if ready and kind == "hyprland":
            ready, reason = self._hyprland_protocols_ready()
        if not pillow_ok:
            monitor = _capability(
                "capture.monitor",
                CapabilityState.UNAVAILABLE,
                backend=_backend_name(),
                scope="os.capture",
                reason_code=ErrorCode.DEPENDENCY_MISSING,
                limitations=(
                    "Pillow is required to crop and scale frames; reinstall pcbridge "
                    "(`pcbridge update`) so its environment has it.",
                ),
            )
        elif ready and compositorlib.is_kde() and not self._kwin_authorized():
            monitor = _capability(
                "capture.monitor",
                CapabilityState.PERMISSION_REQUIRED,
                backend=_backend_name(),
                scope="os.capture",
                reason_code=ErrorCode.PERMISSION_REQUIRED,
                limitations=(
                    "KWin allows screenshots only to programs its .desktop files "
                    "authorize; run `pcbridge setup` to install the native helper's.",
                ),
            )
        elif ready:
            monitor = _capability(
                "capture.monitor",
                CapabilityState.SUPPORTED,
                backend=_backend_name(),
                scope="os.capture",
            )
        else:
            monitor = _capability(
                "capture.monitor",
                CapabilityState.UNAVAILABLE,
                backend=_backend_name(),
                scope="os.capture",
                limitations=(reason,) if reason else (),
                reason_code=ErrorCode.DEPENDENCY_MISSING if not helper_ready else ErrorCode.BACKEND_UNAVAILABLE,
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

        # A window capture is a region of the monitor frame: KWin, Hyprland
        # and, since 2.8, the GNOME extension name the focused window and its
        # frame, so it stands or falls with monitor capture. On X11 the
        # helper reads the focused window's frame through EWMH.
        if kind in ("kde", "hyprland", "cinnamon", "xfce"):
            window = _capability(
                "capture.window",
                monitor.state,
                backend=compositorlib.current().focus_backend,
                scope="os.capture",
                reason_code=monitor.reason_code,
                limitations=monitor.limitations,
            )
        else:
            window = _gnome_window_capability(monitor)
        return {"capture.monitor": monitor, "capture.window": window}

    def _hyprland_protocols_ready(self) -> tuple[bool, str]:
        client = None
        try:
            binary = discover_native_binary(self.cfg.native)
            client = NativeClient(binary, state_dir=self.cfg.state_dir, runtime_dir=runtime_dir())
            response = client.request("capabilities")
            result = response.result if isinstance(response.result, dict) else {}
            entries = result.get("capabilities", [])
            if not isinstance(entries, list) or result.get("backend") != HYPRLAND_BACKEND_NAME:
                return False, "The native helper did not confirm the Hyprland image-copy backend"
            entry = next((item for item in entries
                          if isinstance(item, dict) and item.get("name") == "capture.monitor"), {})
            return entry.get("status") == "supported", str(entry.get("reason") or "Native image-copy protocols are unavailable")
        except DesktopError as error:
            return False, error.message
        finally:
            if client is not None:
                client.close()

    # ------------------------------------------------------------ capture
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
        # The share opens with `desktop_unlock`; one that did not (or a
        # short-lived process such as `pcb-shot`) opens here. There is no
        # other path to fall back to.
        if not self.screencast.is_open() or compositorlib.current().kind in ("hyprland", "unknown"):
            self.start(cursor=include_pointer)
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
        except NativeCaptureError as exc:
            if exc.desktop_error is not None:
                raise exc.desktop_error from exc
            raise _desktop_error(
                exc,
                code=ErrorCode.BACKEND_UNAVAILABLE,
                category=ErrorCategory.CAPABILITY,
                backend=_backend_name(),
                retryable=True,
                suggested_action="Check the native capture helper and the desktop grant.",
            ) from exc
        except capturelib.ShotLayoutChanged as exc:
            raise _desktop_error(
                exc,
                code=ErrorCode.DISPLAY_CHANGED,
                category=ErrorCategory.COORDINATE,
                backend=_backend_name(),
                retryable=True,
                suggested_action="Choose the region again and retry.",
            ) from exc
        except capturelib.CaptureError as exc:
            raise _desktop_error(
                exc,
                code=ErrorCode.BACKEND_UNAVAILABLE,
                category=ErrorCategory.CAPABILITY,
                backend=_backend_name(),
                retryable=True,
                suggested_action="Check the screen capture dependencies and the session.",
            ) from exc
        except monitorslib.MonitorError as exc:
            raise _display_mapping_error(exc) from exc

    # -------------------------------------------- monitors and coordinates
    def list_monitors(self) -> list[monitorslib.Monitor]:
        try:
            return monitorslib.list_monitors()
        except monitorslib.MonitorError as exc:
            raise _display_mapping_error(exc) from exc

    def topology_id(self) -> str:
        """The layout's stable identity, the same string the helper computes.

        Whoever asks whether an open capture session or a cached picture is
        still valid compares this. The rule is in `monitors.topology_id`;
        here it only crosses the provider boundary with a typed error.
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
        """The region as a global canvas box (Step 8.5), with a typed error.

        The error codes are chosen as `to_global` chooses them: the same
        three spaces, the same mistakes.
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


class NativeClipboard:
    """The `Clipboard` operations over the helper's grant-bound IPC (Task 5.4).

    `InputBackend._type_clipboard` keeps the orchestration and the paste key
    already goes to the native keyboard. This moves only where `wl-paste` and
    `wl-copy` run: into the helper that holds the grant, with the content as a
    binary payload that never enters a JSON header or a log line.
    """

    def __init__(self, provider: "RustInputProvider") -> None:
        self._provider = provider

    def save(self) -> clipboardlib.Saved | None:
        result, data = self._provider._clipboard_request("clipboard.read")
        if result.get("empty", True):
            return None
        return clipboardlib.Saved(str(result.get("mime") or ""), data)

    def put_text(self, text: str) -> None:
        self._provider._clipboard_request(
            "clipboard.write",
            mime=clipboardlib.TEXT_MIME,
            data=text.encode("utf-8"),
        )

    def restore(self, saved: clipboardlib.Saved | None) -> None:
        # A failing `wl-copy` on the restore is only logged: the paste has
        # already happened, and failing the call now would invite typing the
        # text twice. Anything else -- a refused grant, a missing program, a
        # timeout -- still propagates.
        try:
            if saved is None:
                self._provider._clipboard_request("clipboard.clear")
            else:
                self._provider._clipboard_request(
                    "clipboard.write", mime=saved.mime, data=saved.data
                )
        except DesktopError as exc:
            if exc.code is not ErrorCode.EXECUTION_UNKNOWN:
                raise
            logger.warning("native clipboard restore failed: %s", exc.message)


class RustInputProvider(inputlib.InputBackend):
    """Native keyboard, pointer and clipboard; typing orchestration stays in Python."""

    def __init__(
        self,
        cfg: Config,
        *,
        gate: Any | None = None,
        client: NativeClient | None = None,
        client_factory: Callable[[], NativeClient] | None = None,
    ) -> None:
        super().__init__(clipboard=NativeClipboard(self))
        self.cfg = cfg
        self.gate = gate
        self._helper = GrantBoundHelper(client_factory or self._new_input_client, client)
        self._keyboard_used = False
        self._pointer_used = False

    def _new_input_client(self) -> NativeClient:
        binary = discover_native_binary(
            self.cfg.native,
            package_root=Path(__file__).resolve().parents[2],
        )
        return NativeClient(
            binary,
            state_dir=self.cfg.state_dir,
            runtime_dir=runtime_dir(),
        )

    def _input_client_for(self, params: dict[str, Any]) -> NativeClient:
        # A superseded helper is asked to release before it stops. Its
        # watchdog already did when the grant changed, and its shutdown does
        # again; the explicit request is the same belt `close()` wears.
        return self._helper.for_grant(
            (params["grant_id"], params["revoke_epoch"]),
            retire=lambda _stale: self.release_all(),
        )

    def _grant_params(self) -> dict[str, Any]:
        token = None
        if self.gate is not None:
            token = self.gate.last_token() or self.gate.current_token()
        if token is None:
            raise DesktopError(
                code=ErrorCode.GRANT_REQUIRED,
                message="no desktop grant: native input cannot be used without a grant id",
                category=ErrorCategory.SAFETY,
                retryable=False,
                suggested_action="Open the desktop grant with desktop_unlock and try again.",
                backend="linux.uinput.native",
            )
        return {
            "grant_id": str(token.grant_id),
            "revoke_epoch": int(token.revoke_epoch),
            "hold_max_seconds": int(self.cfg.desktop.hold_max_seconds),
        }

    @staticmethod
    def _result_dict(response: Any) -> dict[str, Any]:
        if getattr(response, "error", None):
            error = response.error
            raw_code = str(error.get("code") or "")
            raw_category = str(error.get("category") or "")
            try:
                code = ErrorCode(raw_code)
            except ValueError:
                code = ErrorCode.BACKEND_UNAVAILABLE
            try:
                category = ErrorCategory(raw_category)
            except ValueError:
                category = ErrorCategory.IPC
            raise DesktopError(
                code=code,
                message=str(error.get("message") or raw_code),
                category=category,
                retryable=bool(error.get("retryable", False)),
                suggested_action="Check the native input grant and the display topology.",
                backend="pcbridge-native",
            )
        result = getattr(response, "result", None)
        if not isinstance(result, dict):
            raise DesktopError(
                code=ErrorCode.INVALID_FRAME,
                message="Native input returned an invalid answer.",
                category=ErrorCategory.IPC,
                retryable=False,
                suggested_action="Check the native input protocol.",
                backend="pcbridge-native",
            )
        return result

    def _write_request(self, method: str, combo: str | None = None) -> dict[str, Any]:
        params = self._grant_params()
        if combo is not None:
            params["combo"] = str(combo)
        # One call, once. NativeClient never replays a failed request across a
        # helper restart; input operations must not add a retry above it.
        client = self._input_client_for(params)
        self._keyboard_used = True
        response = client.request(method, params, timeout=5.0)
        return self._result_dict(response)

    def _clipboard_request(
        self,
        method: str,
        *,
        mime: str | None = None,
        data: bytes = b"",
    ) -> tuple[dict[str, Any], bytes]:
        """One grant-bound clipboard request; the content only as binary."""
        token = self._grant_params()
        params: dict[str, Any] = {
            "grant_id": token["grant_id"],
            "revoke_epoch": token["revoke_epoch"],
        }
        if mime is not None:
            params["mime"] = mime
        client = self._input_client_for(params)
        try:
            response = client.request(
                method, params, binary=data, timeout=CLIPBOARD_TIMEOUT_SECONDS
            )
        except DesktopError as exc:
            if exc.code is ErrorCode.UNSUPPORTED and "clipboard" not in client.features:
                raise DesktopError(
                    code=ErrorCode.BACKEND_UNAVAILABLE,
                    message=(
                        "The native helper does not know the clipboard methods (a build "
                        "from before Task 5.4). Rebuild it with `scripts/build-native.sh`."
                    ),
                    category=ErrorCategory.CAPABILITY,
                    retryable=False,
                    suggested_action="scripts/build-native.sh",
                    backend="pcbridge-native",
                ) from exc
            raise
        return self._result_dict(response), bytes(response.binary or b"")

    def _read_request(self, method: str) -> dict[str, Any]:
        client = self._helper.client
        pointer = method.startswith("input.pointer.")
        used = self._pointer_used if pointer else self._keyboard_used
        if not used or client is None:
            if method.endswith("position"):
                return {"position": None}
            key = "held" if method.endswith("held") else "released"
            return {key: []}
        if not bool(getattr(client, "is_running", True)):
            self._keyboard_used = False
            self._pointer_used = False
            if method.endswith("position"):
                return {"position": None}
            key = "held" if method.endswith("held") else "released"
            return {key: []}
        response = client.request(method, {}, timeout=2.0)
        return self._result_dict(response)

    def _pointer_params(self, **action: Any) -> dict[str, Any]:
        try:
            table = monitorslib.list_monitors(use_cache=False)
            topology = monitorslib.topology_id(table)
        except monitorslib.MonitorError as exc:
            raise DesktopError(
                code=ErrorCode.DISPLAY_MAPPING_UNKNOWN,
                message=str(exc),
                category=ErrorCategory.COORDINATE,
                retryable=True,
                suggested_action="Refresh the display topology and try again.",
                backend="linux.uinput.native",
            ) from exc
        params = self._grant_params()
        params.update(
            {
                "topology_id": topology,
                "pointer_speed": int(self.cfg.desktop.pointer_speed),
                "pointer_max_ms": int(self.cfg.desktop.pointer_move_max_ms),
            }
        )
        params.update(action)
        return params

    def _relative_params(self, **action: Any) -> dict[str, Any]:
        """Goreli istekler icin DAR zarf.

        `topology_id` ve `pointer_speed`/`pointer_max_ms` bilerek YOK. Goreli
        cihazin ABS araligi olmadigi icin ekran duzeni onu ilgilendirmiyor;
        gonderip yok saymak sonraki okura "topoloji dogrulandi" dedirtirdi.
        `hold_max_seconds` de yok: bu cihaz hic dugme basmiyor. Ayni kalip
        `_clipboard_request`te de var.
        """
        params = self._grant_params()
        params.pop("hold_max_seconds", None)
        params.update(action)
        return params

    def _write_relative_request(self, method: str, **action: Any) -> dict[str, Any]:
        params = self._relative_params(**action)
        # Tek istek, tekrarlanmaz: ilk yazma cihaza ulasmis olabilir.
        client = self._input_client_for(params)
        self._pointer_used = True
        response = client.request(method, params, timeout=5.0)
        return self._result_dict(response)

    def _write_pointer_request(self, method: str, **action: Any) -> dict[str, Any]:
        params = self._pointer_params(**action)
        # Exactly one request. A failed pointer write is never replayed across
        # a helper restart because the first write may already have landed.
        client = self._input_client_for(params)
        self._pointer_used = True
        response = client.request(method, params, timeout=5.0)
        return self._result_dict(response)

    @staticmethod
    def _position_tuple(result: dict[str, Any]) -> tuple[int, int] | None:
        position = result.get("position")
        if position is None:
            return None
        if not isinstance(position, (list, tuple)) or len(position) != 2:
            raise DesktopError(
                code=ErrorCode.INVALID_FRAME,
                message="The native pointer returned an invalid position.",
                category=ErrorCategory.IPC,
                retryable=False,
                suggested_action="Check the native pointer protocol.",
                backend="pcbridge-native",
            )
        return int(position[0]), int(position[1])

    def ensure(
        self, keyboard: bool = False, pointer: bool = False,
        relative: bool = False,
    ) -> float:
        waited = 0.0
        if keyboard:
            result = self._write_request("input.keyboard.ensure")
            waited += float(result.get("waited_seconds") or 0.0)
        if pointer or relative:
            # TEK istek: yardimci istenen cihazlari acip beklemeyi bir kez
            # oduyor. Ayri ayri sorulsaydi native yol, Python yolunun
            # kacindigi ikinci 1,2 saniyeyi geri getirirdi.
            result = self._write_pointer_request(
                "input.pointer.ensure",
                pointer=bool(pointer),
                relative=bool(relative),
            )
            waited += float(result.get("waited_seconds") or 0.0)
        return waited

    def key(self, combo: str) -> None:
        self._write_request("input.keyboard.key", combo)

    def key_down(self, combo: str) -> None:
        self._write_request("input.keyboard.key_down", combo)

    def key_up(self, combo: str) -> None:
        self._write_request("input.keyboard.key_up", combo)

    @property
    def position(self) -> tuple[int, int] | None:
        return self._position_tuple(self._read_request("input.pointer.position"))

    def move(
        self,
        x: int,
        y: int,
        smooth: bool | None = None,
    ) -> tuple[int, int]:
        result = self._write_pointer_request(
            "input.pointer.move",
            x=int(x),
            y=int(y),
            smooth=smooth,
        )
        position = self._position_tuple(result)
        if position is None:
            raise DesktopError(
                code=ErrorCode.INVALID_FRAME,
                message="The native pointer move returned no position.",
                category=ErrorCategory.IPC,
                retryable=False,
                suggested_action="Check the native pointer protocol.",
                backend="pcbridge-native",
            )
        return position

    def move_by(self, dx: int, dy: int) -> tuple[int, int]:
        """Goreli kaydirma, ikinci cihazdan. Konumu yardimci da unutuyor."""
        result = self._write_relative_request(
            "input.pointer.move_by",
            dx=int(dx),
            dy=int(dy),
        )
        sent = result.get("sent")
        if not isinstance(sent, (list, tuple)) or len(sent) != 2:
            raise DesktopError(
                code=ErrorCode.INVALID_FRAME,
                message="The native relative move did not return the delta it sent.",
                category=ErrorCategory.IPC,
                retryable=False,
                suggested_action="Check the native pointer protocol.",
                backend="pcbridge-native",
            )
        return int(sent[0]), int(sent[1])

    def click(
        self, button: str = "left", count: int = 1, hold_ms: int | None = None
    ) -> None:
        # Sure HER ZAMAN acikca gidiyor: yardimcinin kendi varsayilani yalnizca
        # protokolu dogrudan konusan testler icin. Dogrulama Python yoluyla
        # ayni fonksiyondan, yardimci de ayni sinirla ikinci kez bakiyor.
        hold = (
            int(self.cfg.desktop.click_hold_ms)
            if hold_ms is None
            else inputlib.click_hold_ms_checked(hold_ms)
        )
        self._write_pointer_request(
            "input.pointer.click",
            button=str(button),
            count=int(count),
            hold_ms=hold,
        )

    def drag(
        self,
        x: int,
        y: int,
        to_x: int,
        to_y: int,
        button: str = "left",
    ) -> None:
        self._write_pointer_request(
            "input.pointer.drag",
            x1=int(x),
            y1=int(y),
            x2=int(to_x),
            y2=int(to_y),
            button=str(button),
        )

    def scroll(self, amount: int, horizontal: bool = False) -> None:
        self._write_pointer_request(
            "input.pointer.scroll",
            amount=int(amount),
            horizontal=bool(horizontal),
        )

    def mouse_down(self, button: str = "left") -> None:
        self._write_pointer_request("input.pointer.mouse_down", button=str(button))

    def mouse_up(self, button: str = "left") -> None:
        self._write_pointer_request("input.pointer.mouse_up", button=str(button))

    def held(self) -> list[str]:
        keyboard = list(self._read_request("input.keyboard.held").get("held") or [])
        pointer = list(self._read_request("input.pointer.held").get("held") or [])
        return keyboard + pointer

    def release_all(self) -> list[str]:
        released: list[str] = []
        native_error: Exception | None = None
        if self._keyboard_used:
            try:
                released.extend(
                    self._read_request("input.keyboard.release_all").get("released") or []
                )
            except Exception as exc:  # cleanup must still release the pointer
                native_error = exc
            finally:
                self._keyboard_used = False
        if self._pointer_used:
            try:
                released.extend(
                    self._read_request("input.pointer.release_all").get("released") or []
                )
            except Exception as exc:
                if native_error is None:
                    native_error = exc
            finally:
                self._pointer_used = False
        if native_error is not None:
            raise native_error
        return released

    def take_auto_released(self) -> list[str]:
        keyboard = list(
            self._read_request("input.keyboard.take_auto_released").get("released") or []
        )
        pointer = list(
            self._read_request("input.pointer.take_auto_released").get("released") or []
        )
        return keyboard + pointer

    def close(self) -> None:
        # Not one-shot: `desktop_lock` closes this provider every time, and the
        # next grant starts a new helper. A close that only worked once left
        # the second lock's helper running under a dead grant.
        cleanup_error: Exception | None = None
        try:
            self.release_all()
        except Exception as exc:
            cleanup_error = exc
        finally:
            self._helper.close()
        if cleanup_error is not None:
            raise cleanup_error

    def capability_token(self) -> tuple[Any, ...]:
        ready, reason = native_binary_ready(self.cfg)
        try:
            stat = os.stat(inputlib.UINPUT_NODE)
            device = (stat.st_dev, stat.st_ino, stat.st_mode)
        except OSError as exc:
            device = (type(exc).__name__, getattr(exc, "errno", None))
        return (
            "rust-input",
            ready,
            reason,
            device,
            os.access(inputlib.UINPUT_NODE, os.W_OK),
            shutil.which("wl-copy"),
            shutil.which("wl-paste"),
            shutil.which("xclip"),
            _wayland_socket(),
            os.environ.get("DISPLAY", ""),
        )

    def available(self) -> tuple[bool, str]:
        ready, reason = native_binary_ready(self.cfg)
        if not ready:
            return False, reason or "native input helper not found"
        if not os.path.exists(inputlib.UINPUT_NODE):
            return False, f"{inputlib.UINPUT_NODE} does not exist"
        if not os.access(inputlib.UINPUT_NODE, os.W_OK):
            return False, f"no write access to {inputlib.UINPUT_NODE}"
        return True, ""

    def probe_capabilities(self) -> dict[str, Capability]:
        values: dict[str, Capability] = {}
        ready, reason = native_binary_ready(self.cfg)
        for name, scope in (
            ("input.keyboard", "os.keyboard"),
            ("input.pointer", "os.pointer"),
            ("input.pointer_relative", "os.pointer"),
        ):
            if not ready:
                values[name] = _capability(
                    name,
                    CapabilityState.UNAVAILABLE,
                    backend="linux.uinput.native",
                    scope=scope,
                    reason_code=ErrorCode.DEPENDENCY_MISSING,
                    limitations=(reason,) if reason else (),
                )
            elif not os.path.exists(inputlib.UINPUT_NODE):
                values[name] = _capability(
                    name,
                    CapabilityState.UNAVAILABLE,
                    backend="linux.uinput.native",
                    scope=scope,
                    reason_code=ErrorCode.DEPENDENCY_MISSING,
                )
            elif not os.access(inputlib.UINPUT_NODE, os.W_OK):
                values[name] = _capability(
                    name,
                    CapabilityState.PERMISSION_REQUIRED,
                    backend="linux.uinput.native",
                    scope=scope,
                    reason_code=ErrorCode.DEVICE_NOT_GRANTED,
                )
            else:
                values[name] = _capability(
                    name,
                    CapabilityState.SUPPORTED,
                    backend="linux.uinput.native",
                    scope=scope,
                )
        clipboard_backend = ("linux.xclip.native" if compositorlib.is_x11()
                             else "linux.wl-clipboard.native")
        if ready:
            values.update(_clipboard_capabilities(clipboard_backend))
        else:
            for name in ("clipboard.read", "clipboard.write"):
                values[name] = _capability(
                    name,
                    CapabilityState.UNAVAILABLE,
                    backend=clipboard_backend,
                    scope="os.clipboard",
                    reason_code=ErrorCode.DEPENDENCY_MISSING,
                    limitations=(reason,) if reason else (),
                )
        return values


# Task 5.2 exposed this internal class name. Keep it as an alias while runtime
# construction moves to the accurate Task 5.3 name.
RustKeyboardInputProvider = RustInputProvider


#: The helper gives up on a dump after 15 s, and on finding an action's
#: target after 8 s plus 5 s for the call itself; the request waits longer,
#: so the helper's TIMEOUT arrives with its reason instead of a bare IPC
#: timeout. An action whose request runs out may have happened
#: (`EXECUTION_UNKNOWN`) and is never sent again.
ACCESSIBILITY_TIMEOUT_SECONDS = 20.0
ACCESSIBILITY_BACKEND = "linux.atspi.native"

# The helper's own verdicts, passed on with their wording and the shared
# advice (`common._accessibility_error`).
_ACCESSIBILITY_CODES = frozenset({
    ErrorCode.ELEMENT_STALE,
    ErrorCode.ELEMENT_AMBIGUOUS,
    ErrorCode.TARGET_MISMATCH,
    ErrorCode.TIMEOUT,
    ErrorCode.ACTION_UNSUPPORTED,
    ErrorCode.TEXT_MISMATCH,
    ErrorCode.EXECUTION_UNKNOWN,
})


class RustAccessibilityProvider(uitreelib.UiTree):
    """Accessibility through the grant-bound native helper (Tasks 6.2, 6.3).

    The dump, the window list and the focused window come from the helper,
    which reads AT-SPI over D-Bus with no GI and no GTK. Everything after the
    read stays in `uitree`: short ids, the last-dump registry and the text
    shown to the model.

    Actions (`click`, `set_text`) go to the same helper. `uitree` still turns
    the short id into a node of the last dump and the password rule still
    runs first (`set_text` below); the helper is then told only which of its
    own dumps (the snapshot it returned) and which node, and acts on the
    identity it recorded itself. A dump from another helper -- another
    process, or this one before a new grant -- is unknown there and refused
    with ELEMENT_STALE: a new `ui_dump` fixes it.

    Without a grant there is no helper to ask, so nothing is read: the window
    list and the focused window are refused like a dump (`screen_info` lists
    no windows before `desktop_unlock`). The Python AT-SPI helper that once
    answered then is gone.
    """

    def __init__(
        self,
        cfg: Config,
        *,
        gate: Any | None = None,
        client: NativeClient | None = None,
        client_factory: Callable[[], NativeClient] | None = None,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self.gate = gate
        self._helper = GrantBoundHelper(client_factory or self._new_client, client)

    def _new_client(self) -> NativeClient:
        binary = discover_native_binary(
            self.cfg.native,
            package_root=Path(__file__).resolve().parents[2],
        )
        return NativeClient(binary, state_dir=self.cfg.state_dir, runtime_dir=runtime_dir())

    def _current_grant(self) -> tuple[str, int] | None:
        token = self.gate.current_token() if self.gate is not None else None
        if token is None:
            return None
        return str(token.grant_id), int(token.revoke_epoch)

    def _dump_grant(self) -> tuple[str, int]:
        # The last grant too: the helper then answers why it is refused
        # (expired, revoked) instead of this side guessing.
        token = None
        if self.gate is not None:
            token = self.gate.last_token() or self.gate.current_token()
        if token is None:
            raise DesktopError(
                code=ErrorCode.GRANT_REQUIRED,
                message="no desktop grant: the accessibility tree is not read without one",
                category=ErrorCategory.SAFETY,
                retryable=False,
                suggested_action="Open the desktop grant with desktop_unlock and try again.",
                backend=ACCESSIBILITY_BACKEND,
            )
        return str(token.grant_id), int(token.revoke_epoch)

    def _request(
        self,
        method: str,
        grant: tuple[str, int],
        params: dict[str, Any] | None = None,
        *,
        binary: bytes = b"",
        acting: bool = False,
    ) -> dict[str, Any]:
        grant_id, revoke_epoch = grant
        client = self._helper.for_grant(grant)
        feature = "accessibility.action" if acting else "accessibility.read"
        try:
            response = client.request(
                method,
                {"grant_id": grant_id, "revoke_epoch": revoke_epoch, **(params or {})},
                binary=binary,
                timeout=ACCESSIBILITY_TIMEOUT_SECONDS,
            )
        except DesktopError as exc:
            if exc.code is ErrorCode.UNSUPPORTED and feature not in client.features:
                raise DesktopError(
                    code=ErrorCode.BACKEND_UNAVAILABLE,
                    message=(
                        "The native helper does not know this accessibility method "
                        f"({method}; an older build). Rebuild it with `scripts/build-native.sh`."
                        ""
                    ),
                    category=ErrorCategory.CAPABILITY,
                    retryable=False,
                    suggested_action="scripts/build-native.sh",
                    backend=ACCESSIBILITY_BACKEND,
                ) from exc
            if acting and exc.execution_state == "unknown":
                # Sent, and the helper never answered (timeout, crash, closed
                # pipe): the action may have happened. Never sent again.
                raise _accessibility_error(
                    uitreelib.UiTreeError(
                        "The native helper did not answer the action; it may or may not "
                        "have happened. Not repeated: look at the result with ui_dump "
                        "or screen_capture first.",
                        ErrorCode.EXECUTION_UNKNOWN,
                    ),
                    ErrorCode.EXECUTION_UNKNOWN,
                    ErrorCategory.EXECUTION,
                    False,
                    backend=ACCESSIBILITY_BACKEND,
                ) from exc
            if exc.code in _ACCESSIBILITY_CODES:
                raise _accessibility_error(
                    uitreelib.UiTreeError(exc.message, exc.code),
                    exc.code,
                    ErrorCategory.ACCESSIBILITY,
                    exc.retryable,
                    backend=ACCESSIBILITY_BACKEND,
                ) from exc
            raise
        result = getattr(response, "result", None)
        if not isinstance(result, dict) or not result.get("ok"):
            raise DesktopError(
                code=ErrorCode.INVALID_FRAME,
                message="The native accessibility helper returned an invalid answer.",
                category=ErrorCategory.IPC,
                retryable=False,
                suggested_action="check_native_protocol",
                backend=ACCESSIBILITY_BACKEND,
            )
        return result

    def dump(
        self,
        target: str = "focused",
        interactive_only: bool = True,
        max_nodes: int = 400,
    ) -> uitreelib.Dump:
        result = self._request(
            "accessibility.dump",
            self._dump_grant(),
            {
                "target": str(target),
                "interactive_only": bool(interactive_only),
                "max_nodes": int(max_nodes),
            },
        )
        dump = uitreelib.dump_from_response(result, backend=ACCESSIBILITY_BACKEND)
        self._last = dump
        return dump

    def _act(self, payload: dict, fallback: str) -> dict:
        """A click or a text write, sent to the helper that made the dump.

        `UiTree.click` and `UiTree.set_text` resolved the short id and built
        `payload`. Only the snapshot and the node's object path go on: the
        helper checks them against its own record. The text rides in the
        binary payload, never in the JSON header.
        """
        dump = self._last
        assert dump is not None  # resolve() refuses before any dump
        params: dict[str, Any] = {"snapshot": dump.snapshot, "ref": str(payload["ref"])}
        if payload["cmd"] == "act":
            method, binary = "accessibility.act", b""
            params["action"] = str(payload.get("action") or "click")
        else:
            method = "accessibility.set_text"
            binary = str(payload["text"]).encode("utf-8")
        result = self._request(method, self._dump_grant(), params, binary=binary, acting=True)
        result["snapshot"] = dump.snapshot
        return result

    def focused_window(self) -> tuple[str, str]:
        # The current grant, else the last one, so the helper says why it is
        # refused (expired, revoked); no grant at all is GRANT_REQUIRED.
        grant = self._current_grant() or self._dump_grant()
        result = self._request("accessibility.focused", grant)
        return result.get("app") or "?", result.get("window") or ""

    def windows(self) -> list[uitreelib.Window]:
        grant = self._current_grant() or self._dump_grant()
        return uitreelib.windows_from_response(self._request("accessibility.windows", grant))

    def _translate_accessibility(
        self,
        operation: Callable[[], Any],
        *,
        code: ErrorCode,
        category: ErrorCategory,
        retryable: bool,
    ) -> Any:
        """Run a `uitree` step and turn its failure into the shared taxonomy.

        A stable code the step states itself wins over the caller's default;
        the message is never parsed.
        """
        try:
            return operation()
        except uitreelib.UiTreeError as exc:
            raise _accessibility_error(
                exc, code, category, retryable, backend=ACCESSIBILITY_BACKEND) from exc

    def click(self, node_id: str, action: str = "click") -> dict:
        return self._translate_accessibility(
            lambda: super(RustAccessibilityProvider, self).click(node_id, action),
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
        # A content gate, not a permission gate: it refuses even when
        # `SafetyGate` allows, and `force` does not pass it
        # (docs/dev/desktop-rules.md, section 4 item 7).
        policy.check_text_target(role=node.role, name=node.name)
        return self._translate_accessibility(
            lambda: super(RustAccessibilityProvider, self).set_text(node_id, text),
            code=ErrorCode.TARGET_MISMATCH,
            category=ErrorCategory.ACCESSIBILITY,
            retryable=False,
        )

    def describe_dump(self, dump: uitreelib.Dump) -> str:
        return uitreelib.describe(dump)

    def describe_windows(self, windows: list[uitreelib.Window]) -> str:
        return uitreelib.describe_windows(windows)

    def close(self) -> None:
        self._helper.close()

    def available(self) -> tuple[bool, str]:
        ready, reason = native_binary_ready(self.cfg)
        if not ready:
            return False, reason or "native accessibility helper not found"
        return True, ""

    def capability_token(self) -> tuple[Any, ...]:
        ready, reason = native_binary_ready(self.cfg)
        return ("rust-accessibility", ready, reason)

    def probe_capabilities(self) -> dict[str, Capability]:
        values: dict[str, Capability] = {}
        ready, reason = native_binary_ready(self.cfg)
        if not ready:
            for name in ("accessibility.read", "window.list", "accessibility.action"):
                values[name] = _capability(
                    name,
                    CapabilityState.UNAVAILABLE,
                    backend=ACCESSIBILITY_BACKEND,
                    scope="os.accessibility",
                    reason_code=ErrorCode.DEPENDENCY_MISSING,
                    limitations=(reason,) if reason else (),
                )
            return values
        for name in ("accessibility.read", "accessibility.action"):
            values[name] = _capability(
                name,
                CapabilityState.SUPPORTED,
                backend=ACCESSIBILITY_BACKEND,
                scope="os.accessibility",
            )
        values["window.list"] = _capability(
            "window.list",
            CapabilityState.DEGRADED,
            backend=ACCESSIBILITY_BACKEND,
            scope="os.accessibility",
            limitations=("Only accessibility-visible applications are listed.",),
        )
        return values


__all__ = [
    "ACCESSIBILITY_BACKEND",
    "BACKEND_NAME",
    "KWIN_BACKEND_NAME",
    "NativeCaptureError",
    "NativeScreenCast",
    "RustAccessibilityProvider",
    "RustCaptureProvider",
    "RustInputProvider",
    "RustKeyboardInputProvider",
    "native_binary_ready",
    "runtime_dir",
]
