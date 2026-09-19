"""Native capture and keyboard providers with narrow Python adapters.

WHAT MOVES AND WHAT DOES NOT
    Only the acquisition of the raw frame changes. Cropping, scaling, the PNG
    that reaches the client, the shot id, the two lookup directories, the
    metadata record and every coordinate conversion stay exactly where they
    were, in `capture.py`. That is deliberate: the shot id is how a later
    `mouse(shot=...)` call finds the offset and scale of an image, and moving
    that bookkeeping into a second language is how the two copies drift and a
    click lands on the wrong screen.

    So this module is small on purpose. `NativeScreenCast` has the same shape
    the legacy `ScreenCast` handle has -- `is_open`, `start`, `ensure_cursor`,
    `capture(connector, path)`, `close` -- and `capture.py` cannot tell them
    apart. `RustCaptureProvider` is the Python provider with that handle
    injected, overriding only what genuinely differs: availability, the
    capability report and the backend name.

KEYBOARD AND POINTER MOVE WITHOUT CLIPBOARD
    Tasks 5.2 and 5.3 add `RustInputProvider`, selected only by the explicit
    `[native] input = "rust"` setting. Keyboard and already-resolved global
    pointer coordinates go to the helper; shot and monitor identities never
    cross that boundary. Clipboard orchestration remains in Python. The shipped
    default remains Python until the final input gate is complete.

THE SHARING INDICATOR APPEARS WITH THE GRANT
    On the Python path the screen share opens at `desktop_unlock`, so GNOME's
    top-bar indicator appears the moment the grant does. Until Task 4.3 the
    native session opened on the first `capture.frame` instead, which left a
    stretch with a grant and no indicator. Before `auto` became the default
    that difference was closed rather than only written down: `start()` asks
    the helper for `capture.session_open`, which opens the Mutter session
    without reading a frame, and the session stays open until revoke, lock or
    expiry. The indicator is the user's evidence that an agent can see the
    screen, so both paths now show it at the same moment. A helper from before
    Task 4.3 answers UNKNOWN_METHOD and keeps opening on demand.
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
from ..errors import DesktopError, ErrorCategory, ErrorCode
from .. import capture as capturelib
from .. import input as inputlib
from .. import monitors as monitorslib
from .python import PythonCaptureProvider, PythonInputProvider, _capability, _desktop_error
from ...config import Config
from ...native import NativeClient, discover_native_binary

#: Mutter connectors are addressed with an explicit scheme so that a native
#: backend for another compositor cannot be handed an id it would misread.
DISPLAY_SCHEME = "mutter"

#: Matches the native side's own ceiling. A frame that has not arrived in eight
#: seconds is not coming, and one MCP call may not block for 110.
FRAME_TIMEOUT_MS = 8000

BACKEND_NAME = "linux.mutter.pipewire"

logger = logging.getLogger(__name__)


class NativeCaptureError(RuntimeError):
    """Raised where the legacy handle would raise, carrying the typed cause."""

    def __init__(self, message: str, *, cause: DesktopError | None = None) -> None:
        super().__init__(message)
        self.desktop_error = cause


class BackendSelection:
    """Which acquisition path was chosen, and whether the user should be told."""

    __slots__ = ("backend", "degraded", "reason")

    def __init__(self, *, backend: str, degraded: bool = False, reason: str = "") -> None:
        self.backend = backend
        self.degraded = degraded
        self.reason = reason

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BackendSelection):
            return NotImplemented
        return (self.backend, self.degraded, self.reason) == (
            other.backend,
            other.degraded,
            other.reason,
        )

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"BackendSelection(backend={self.backend!r}, "
            f"degraded={self.degraded!r}, reason={self.reason!r})"
        )


def select_capture_backend(
    *,
    requested: str,
    native_ready: bool,
    native_reason: str = "",
) -> "BackendSelection":
    """Decide once, at runtime construction, which acquisition path is used.

    Pure on purpose: the table in `PLAN.md` is the contract, and a table that
    lives in one function can be read and tested without a compositor.

    The rule that matters is the one for `rust`: a forced native backend that
    cannot start does **not** quietly become a Python screencast. It stays
    selected and fails visibly, because the whole point of forcing it is to
    find out whether it works.
    """
    choice = (requested or "python").strip().lower()
    if choice == "python":
        return BackendSelection(backend="python")
    if choice == "rust":
        return BackendSelection(backend="rust", reason="" if native_ready else native_reason)
    if choice == "auto":
        if native_ready:
            return BackendSelection(backend="rust")
        return BackendSelection(backend="python", degraded=True, reason=native_reason)
    # config.py validates this field, so an unknown value means the two drifted.
    return BackendSelection(
        backend="python",
        degraded=True,
        reason=f"bilinmeyen `[native] capture` degeri: {requested!r}",
    )


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
    """The legacy `ScreenCast` surface, answered by the native helper.

    Deliberately not a `ScreenCast` subclass: nothing here drives GStreamer or
    the system Python, and inheriting would invite a method that silently falls
    through to the helper process we are trying to retire.
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
            raise NativeCaptureError(f"native helper bulunamadi: {exc}") from exc
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
        request naming a different one. Passing the gate's current token is what
        makes a revoke that happened between two calls come back as `REVOKED`
        instead of a picture.
        """
        token = None
        if self.gate is not None:
            token = self.gate.current_token() or self.gate.last_token()
        if token is None:
            # A missing grant is a safety refusal, not a broken backend. Left
            # untyped it surfaced as BACKEND_UNAVAILABLE/capability (measured
            # 2026-09-13, Task 4.2, for a revoked and for an expired grant),
            # which points an agent at the helper instead of `desktop_unlock`.
            refusal = DesktopError(
                code=ErrorCode.GRANT_REQUIRED,
                message="masaustu izni yok: native capture grant kimligi olmadan istenemez",
                category=ErrorCategory.SAFETY,
                retryable=False,
                suggested_action="Masaustu iznini desktop_unlock ile acip tekrar deneyin.",
                backend=BACKEND_NAME,
            )
            raise NativeCaptureError(refusal.message, cause=refusal)
        return str(token.grant_id), int(token.revoke_epoch)

    # -------------------------------------------------------------- shape
    def is_open(self) -> bool:
        return self._open

    def start(self, monitors: list[str], cursor: bool = True) -> dict:
        """Open the Mutter session now, so the share starts with the grant.

        Until Task 4.3 this only marked the handle usable and the session
        opened at the first capture, which left a stretch after
        `desktop_unlock` with a grant and no sharing indicator. The indicator
        is the user's evidence that an agent can see the screen, so the native
        path now shows it exactly when the Python path does. No frame is read.
        A helper from before Task 4.3 answers UNKNOWN_METHOD and keeps opening
        on demand.
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
            if response.error.get("code") != "UNKNOWN_METHOD":
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
                "native capture hazir degil (masaustu izni verilince aciliyor)"
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
                "display_id": f"{DISPLAY_SCHEME}:{connector}",
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
            if (int(pixels[0]), int(pixels[1])) != (expected.width, expected.height):
                # Rule 8: an unexpected stream size is reported, never rescaled
                # into place. A silent scale here would put every later click a
                # proportional distance away from where the agent aimed.
                raise NativeCaptureError(
                    f"{connector} akisi {pixels[0]}x{pixels[1]} verdi, monitor "
                    f"tablosu {expected.width}x{expected.height} diyor. Monitor "
                    "duzeni degismis olabilir; tekrar deneyin."
                )
        if not response.binary:
            raise NativeCaptureError(f"{connector} icin bos kare dondu")

        destination = Path(path)
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
        message=str(error.get("message") or code or "native capture basarisiz"),
        category=mapped[1],
        retryable=bool(error.get("retryable", False)),
        suggested_action="check_desktop_grant_and_display_layout",
        backend=BACKEND_NAME,
    )


class RustCaptureProvider(PythonCaptureProvider):
    """The Python capture provider with the native handle injected.

    Everything inherited is the part that must not change: shot ids, metadata,
    `to_global`, the two lookup directories. What is overridden is only what
    would otherwise answer for the wrong backend.
    """

    def __init__(
        self,
        cfg: Config,
        *,
        gate: Any | None = None,
        screencast: Any | None = None,
    ) -> None:
        super().__init__(
            cfg,
            screencast=screencast
            if screencast is not None
            else NativeScreenCast(cfg, gate=gate),
        )

    def start(self, *, cursor: bool | None = None) -> dict:
        """Open the share; a refusal comes back typed, never as a crash.

        `desktop_unlock` catches DesktopError and reports it. A bare
        NativeCaptureError would escape the tool instead.
        """
        try:
            return super().start(cursor=cursor)
        except NativeCaptureError as exc:
            if exc.desktop_error is not None:
                raise exc.desktop_error from exc
            raise _desktop_error(
                exc,
                code=ErrorCode.BACKEND_UNAVAILABLE,
                category=ErrorCategory.CAPABILITY,
                backend=BACKEND_NAME,
                retryable=True,
                suggested_action="Native capture yardimcisini ve masaustu iznini denetleyin.",
            ) from exc

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
            self.cfg.desktop.capture_backend,
            capturelib.PIL_AVAILABLE,
            ready,
            self.screencast.is_open(),
            topology,
        )

    def available(self) -> tuple[bool, str]:
        if not capturelib.PIL_AVAILABLE:
            return False, (
                f"python paketi `Pillow` yok ({capturelib.PIL_IMPORT_ERROR}). "
                "Kurulum: ./.venv/bin/pip install -r requirements.txt"
            )
        return native_binary_ready(self.cfg)

    def backend_name(self) -> str:
        """What the next capture will actually use -- state, not a guess."""
        return BACKEND_NAME if self.screencast.is_open() else "pcbridge-native"

    def probe_capabilities(self) -> dict[str, Capability]:
        """Report monitor capture from the native path without opening a session."""
        pillow_ok = capturelib.PIL_AVAILABLE
        ready, reason = native_binary_ready(self.cfg)
        if not pillow_ok:
            monitor = _capability(
                "capture.monitor",
                CapabilityState.UNAVAILABLE,
                backend=BACKEND_NAME,
                scope="os.capture",
                reason_code=ErrorCode.DEPENDENCY_MISSING,
                limitations=("Pillow is required to crop and scale frames.",),
            )
        elif ready:
            monitor = _capability(
                "capture.monitor",
                CapabilityState.SUPPORTED,
                backend=BACKEND_NAME,
                scope="os.capture",
            )
        else:
            monitor = _capability(
                "capture.monitor",
                CapabilityState.UNAVAILABLE,
                backend=BACKEND_NAME,
                scope="os.capture",
                reason_code=ErrorCode.DEPENDENCY_MISSING,
                limitations=(reason,) if reason else (),
            )

        try:
            monitorslib.list_monitors()
        except monitorslib.MonitorError:
            monitor = _capability(
                "capture.monitor",
                CapabilityState.UNAVAILABLE,
                backend="linux.mutter-display-config",
                scope="os.capture",
                reason_code=ErrorCode.DISPLAY_MAPPING_UNKNOWN,
            )

        # Window capture is not a native capability and is not claimed as one:
        # the legacy screenshot path still owns it.
        window = PythonCaptureProvider._screenshot_capability(
            "capture.window",
            pillow_ok and bool(shutil.which(capturelib.GNOME_SCREENSHOT)),
        )
        return {"capture.monitor": monitor, "capture.window": window}

    def capture(
        self,
        spec: int | str,
        *,
        out_dir: Path,
        scale_long_edge: int,
        include_pointer: bool,
        copy_meta_to: Sequence[Path] = (),
        reserved_dirs: Sequence[Path] = (),
    ) -> list[capturelib.Shot]:
        try:
            return super().capture(
                spec,
                out_dir=out_dir,
                scale_long_edge=scale_long_edge,
                include_pointer=include_pointer,
                copy_meta_to=copy_meta_to,
                reserved_dirs=reserved_dirs,
            )
        except NativeCaptureError as exc:
            if exc.desktop_error is not None:
                raise exc.desktop_error from exc
            raise _desktop_error(
                exc,
                code=ErrorCode.BACKEND_UNAVAILABLE,
                category=ErrorCategory.CAPABILITY,
                backend=BACKEND_NAME,
                retryable=True,
                suggested_action="Native capture yardimcisini ve masaustu iznini denetleyin.",
            ) from exc


class RustInputProvider(PythonInputProvider):
    """Native keyboard and pointer with Python clipboard orchestration."""

    def __init__(
        self,
        cfg: Config,
        *,
        gate: Any | None = None,
        client: NativeClient | None = None,
        client_factory: Callable[[], NativeClient] | None = None,
    ) -> None:
        super().__init__(cfg)
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
            token = self.gate.current_token() or self.gate.last_token()
        if token is None:
            raise DesktopError(
                code=ErrorCode.GRANT_REQUIRED,
                message="masaustu izni yok: native input grant kimligi olmadan kullanilamaz",
                category=ErrorCategory.SAFETY,
                retryable=False,
                suggested_action="Masaustu iznini desktop_unlock ile acip tekrar deneyin.",
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
                suggested_action="Native input grant ve display topology durumunu denetleyin.",
                backend="pcbridge-native",
            )
        result = getattr(response, "result", None)
        if not isinstance(result, dict):
            raise DesktopError(
                code=ErrorCode.INVALID_FRAME,
                message="Native input gecersiz bir yanit dondurdu.",
                category=ErrorCategory.IPC,
                retryable=False,
                suggested_action="Native input protokolunu denetleyin.",
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
                suggested_action="Display topology durumunu yenileyip tekrar deneyin.",
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
                message="Native pointer gecersiz bir konum dondurdu.",
                category=ErrorCategory.IPC,
                retryable=False,
                suggested_action="Native pointer protokolunu denetleyin.",
                backend="pcbridge-native",
            )
        return int(position[0]), int(position[1])

    def ensure(self, keyboard: bool = False, pointer: bool = False) -> float:
        waited = 0.0
        if keyboard:
            result = self._write_request("input.keyboard.ensure")
            waited += float(result.get("waited_seconds") or 0.0)
        if pointer:
            result = self._write_pointer_request("input.pointer.ensure")
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
                message="Native pointer hareketi konum dondurmedi.",
                category=ErrorCategory.IPC,
                retryable=False,
                suggested_action="Native pointer protokolunu denetleyin.",
                backend="pcbridge-native",
            )
        return position

    def click(self, button: str = "left", count: int = 1) -> None:
        self._write_pointer_request(
            "input.pointer.click",
            button=str(button),
            count=int(count),
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
        # No event path remains in Python, but close any legacy object created
        # before a runtime switched providers in the same process.
        released.extend(super().release_all())
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
        return keyboard + pointer + super().take_auto_released()

    def close(self) -> None:
        # Not one-shot: `desktop_lock` closes this provider every time, and the
        # next grant starts a new helper. A close that only worked once left
        # the second lock's helper running under a dead grant.
        cleanup_error: Exception | None = None
        try:
            self.release_all()
        except Exception as exc:
            cleanup_error = exc
        try:
            super().close()
        finally:
            self._helper.close()
        if cleanup_error is not None:
            raise cleanup_error

    def capability_token(self) -> tuple[Any, ...]:
        ready, reason = native_binary_ready(self.cfg)
        return ("rust-input", ready, reason, super().capability_token())

    def available(self) -> tuple[bool, str]:
        ready, reason = native_binary_ready(self.cfg)
        if not ready:
            return False, reason or "native input yardimcisi bulunamadi"
        if not os.path.exists(inputlib.UINPUT_NODE):
            return False, f"{inputlib.UINPUT_NODE} yok"
        if not os.access(inputlib.UINPUT_NODE, os.W_OK):
            return False, f"{inputlib.UINPUT_NODE} icin yazma izni yok"
        return True, ""

    def probe_capabilities(self) -> dict[str, Capability]:
        values = super().probe_capabilities()
        ready, reason = native_binary_ready(self.cfg)
        for name, scope in (
            ("input.keyboard", "os.keyboard"),
            ("input.pointer", "os.pointer"),
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
        return values


# Task 5.2 exposed this internal class name. Keep it as an alias while runtime
# construction moves to the accurate Task 5.3 name.
RustKeyboardInputProvider = RustInputProvider


__all__ = [
    "BackendSelection",
    "BACKEND_NAME",
    "NativeCaptureError",
    "NativeScreenCast",
    "RustCaptureProvider",
    "RustInputProvider",
    "RustKeyboardInputProvider",
    "native_binary_ready",
    "runtime_dir",
    "select_capture_backend",
]
