"""Threaded supervisor for the private pcbridge-native child process."""

from __future__ import annotations

import itertools
import os
import queue
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from pcbridge import __version__
from pcbridge.desktop.errors import DesktopError, ErrorCategory, ErrorCode

from .protocol import (
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    NativeFrameError,
    NativeProtocolMismatch,
    NativeResponse,
    decode_response,
    encode_frame,
    read_frame,
    write_encoded_frame,
)
from .registry import NativeProcessEntry, NativeRegistry


_STDERR_LIMIT = 64 * 1024
_ALLOWED_ENVIRONMENT = {
    "DBUS_SESSION_BUS_ADDRESS",
    "DISPLAY",
    "HOME",
    "LANG",
    "LOGNAME",
    "PATH",
    "PIPEWIRE_REMOTE",
    "USER",
    "WAYLAND_DISPLAY",
    "XDG_CONFIG_DIRS",
    "XDG_CURRENT_DESKTOP",
    "XDG_DATA_DIRS",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_DESKTOP",
    "XDG_SESSION_TYPE",
}


def _is_sensitive_environment(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in ("API_KEY", "PASSWORD", "SECRET", "TOKEN"))


@dataclass
class _PendingRequest:
    event: threading.Event = field(default_factory=threading.Event)
    response: NativeResponse | None = None
    error: DesktopError | None = None


@dataclass(frozen=True)
class _OutboundFrame:
    encoded: bytes
    request_id: str | None


@dataclass(frozen=True)
class NativeHandshake:
    """Validated capabilities negotiated during helper initialization."""

    instance_id: str
    native_version: str
    platform: str
    features: frozenset[str]
    # Commit the helper was built from ("dev" for ad-hoc builds). Helpers from
    # before Task 4.1 do not send it, so it stays optional.
    build_id: str = ""


def _desktop_error(
    code: ErrorCode,
    message: str,
    *,
    retryable: bool = False,
    suggested_action: str,
    execution_state: str | None = None,
) -> DesktopError:
    category = (
        ErrorCategory.EXECUTION
        if code in (ErrorCode.TIMEOUT, ErrorCode.CANCELLED, ErrorCode.BUSY)
        else ErrorCategory.IPC
    )
    return DesktopError(
        code=code,
        message=message,
        category=category,
        retryable=retryable,
        suggested_action=suggested_action,
        backend="pcbridge-native",
        execution_state=execution_state,
    )


class NativeClient:
    """Own one native child and correlate concurrent framed requests by ID."""

    def __init__(
        self,
        binary: str | Path,
        *,
        state_dir: str | Path,
        runtime_dir: str | Path,
        client_version: str = __version__,
        environment: Mapping[str, str] | None = None,
        startup_timeout: float = 3.0,
        default_timeout: float = 2.0,
        shutdown_timeout: float = 2.0,
        pending_limit: int = 16,
    ) -> None:
        self.binary = Path(binary).resolve()
        self.state_dir = Path(state_dir).resolve()
        self.runtime_dir = Path(runtime_dir).resolve()
        self.client_version = client_version
        self.startup_timeout = float(startup_timeout)
        self.default_timeout = float(default_timeout)
        self.shutdown_timeout = float(shutdown_timeout)
        self.pending_limit = int(pending_limit)
        self._environment = dict(environment or {})

        if self.startup_timeout <= 0 or self.default_timeout <= 0:
            raise ValueError("native request timeouts must be positive")
        if self.shutdown_timeout <= 0:
            raise ValueError("native shutdown timeout must be positive")
        if self.pending_limit < 1:
            raise ValueError("native pending limit must be at least one")

        self._client_id = f"python-{uuid.uuid4().hex}"
        self._request_numbers = itertools.count(1)
        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._pending: dict[str, _PendingRequest] = {}
        self._process: subprocess.Popen[bytes] | None = None
        self._writer_queue: queue.Queue[_OutboundFrame | None] | None = None
        self._reader_thread: threading.Thread | None = None
        self._writer_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._generation = 0
        self._ready = False
        self._closing = False
        self._closed = False
        self._handshake: NativeHandshake | None = None
        self._registry = NativeRegistry(self.runtime_dir)
        self._registry_entry: NativeProcessEntry | None = None
        self._stderr = bytearray()

    @property
    def pid(self) -> int | None:
        with self._state_lock:
            return self._process.pid if self._process is not None else None

    @property
    def instance_id(self) -> str | None:
        with self._state_lock:
            handshake = self._handshake
            return handshake.instance_id if handshake is not None else None

    @property
    def handshake(self) -> NativeHandshake | None:
        with self._state_lock:
            return self._handshake

    @property
    def features(self) -> frozenset[str]:
        with self._state_lock:
            handshake = self._handshake
            return handshake.features if handshake is not None else frozenset()

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            process = self._process
            return bool(self._ready and process is not None and process.poll() is None)

    @property
    def stderr_tail(self) -> str:
        with self._state_lock:
            return bytes(self._stderr).decode("utf-8", errors="replace")

    def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        binary: bytes = b"",
        timeout: float | None = None,
    ) -> NativeResponse:
        """Send one request without replaying it across process restarts."""

        if not isinstance(method, str) or not method:
            raise ValueError("native method must be a nonempty string")
        self._ensure_started()
        return self._request_started(
            method,
            dict(params or {}),
            binary=binary,
            timeout=self.default_timeout if timeout is None else float(timeout),
        )

    def close(self) -> None:
        """Shut down, escalate if needed, and reap the child exactly once."""

        with self._lifecycle_lock:
            if self._closed:
                return
            self._closing = True
            self._fail_pending(
                _desktop_error(
                    ErrorCode.CANCELLED,
                    "Native helper kapatiliyor.",
                    suggested_action="start_a_new_native_request",
                    execution_state="canceled",
                )
            )
            with self._state_lock:
                process = self._process
                can_shutdown = bool(
                    self._ready and process is not None and process.poll() is None
                )
            shutdown_failed = False
            if can_shutdown:
                try:
                    self._request_started(
                        "shutdown",
                        {},
                        binary=b"",
                        timeout=self.shutdown_timeout,
                        allow_closing=True,
                    )
                except DesktopError:
                    shutdown_failed = True
            if process is not None:
                self._stop_process_locked(
                    process,
                    wait_before_terminate=not shutdown_failed,
                )
            self._closed = True
            self._closing = False

    def __enter__(self) -> NativeClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _ensure_started(self) -> None:
        with self._lifecycle_lock:
            if self._closed or self._closing:
                raise _desktop_error(
                    ErrorCode.CANCELLED,
                    "Native client kapatildi.",
                    suggested_action="create_a_new_native_client",
                    execution_state="not_started",
                )
            with self._state_lock:
                process = self._process
                ready = bool(
                    self._ready and process is not None and process.poll() is None
                )
            if ready:
                return
            if process is not None:
                self._stop_process_locked(process)
            self._start_locked()

    def _start_locked(self) -> None:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in _ALLOWED_ENVIRONMENT or key.startswith("LC_")
        }
        environment.update(self._environment)
        environment = {
            key: value
            for key, value in environment.items()
            if not _is_sensitive_environment(key)
        }
        try:
            process = subprocess.Popen(
                [str(self.binary)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                close_fds=True,
                env=environment,
            )
        except (FileNotFoundError, PermissionError) as error:
            raise _desktop_error(
                ErrorCode.NATIVE_NOT_FOUND,
                f"Native helper baslatilamadi: {self.binary}",
                suggested_action="configure_native_binary",
            ) from error
        except OSError as error:
            raise _desktop_error(
                ErrorCode.BACKEND_UNAVAILABLE,
                "Native helper process'i baslatilamadi.",
                retryable=True,
                suggested_action="inspect_native_runtime",
            ) from error

        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                os.set_inheritable(stream.fileno(), False)
            except OSError as error:
                process.kill()
                process.wait()
                for candidate in (process.stdin, process.stdout, process.stderr):
                    if candidate is not None:
                        candidate.close()
                raise _desktop_error(
                    ErrorCode.NATIVE_CRASHED,
                    "Native helper IPC descriptor'lari guvenli hale getirilemedi.",
                    suggested_action="inspect_native_runtime",
                ) from error

        with self._state_lock:
            self._generation += 1
            generation = self._generation
            self._process = process
            writer_queue: queue.Queue[_OutboundFrame | None] = queue.Queue(
                maxsize=self.pending_limit + 1
            )
            self._writer_queue = writer_queue
            self._ready = False
            self._handshake = None
            self._stderr.clear()

        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            args=(process, generation),
            name=f"pcbridge-native-reader-{process.pid}",
            daemon=True,
        )
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            args=(process, generation, writer_queue),
            name=f"pcbridge-native-writer-{process.pid}",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._stderr_loop,
            args=(process, generation),
            name=f"pcbridge-native-stderr-{process.pid}",
            daemon=True,
        )
        self._reader_thread.start()
        self._writer_thread.start()
        self._stderr_thread.start()

        try:
            response = self._request_started(
                "initialize",
                {
                    "client_version": self.client_version,
                    "supported_minor": [PROTOCOL_MINOR],
                    "state_dir": str(self.state_dir),
                    "runtime_dir": str(self.runtime_dir),
                },
                binary=b"",
                timeout=self.startup_timeout,
                allow_unready=True,
            )
            handshake = self._decode_handshake(response.result)
            try:
                registry_entry = self._registry.register(process.pid, handshake.instance_id)
            except (OSError, ValueError) as error:
                raise _desktop_error(
                    ErrorCode.BACKEND_UNAVAILABLE,
                    "Native helper process kaydi olusturulamadi.",
                    suggested_action="inspect_native_runtime",
                ) from error
            with self._state_lock:
                registered = bool(
                    self._process is process
                    and self._generation == generation
                    and process.poll() is None
                )
                if registered:
                    self._handshake = handshake
                    self._registry_entry = registry_entry
                    self._ready = True
            if not registered:
                self._registry.unregister(registry_entry)
                raise _desktop_error(
                    ErrorCode.NATIVE_CRASHED,
                    "Native helper initialize sonrasinda kapandi.",
                    suggested_action="retry_read_only_native_request",
                    execution_state="not_started",
                )
        except BaseException:
            self._stop_process_locked(process)
            raise

    def _request_started(
        self,
        method: str,
        params: dict[str, Any],
        *,
        binary: bytes,
        timeout: float,
        allow_unready: bool = False,
        allow_closing: bool = False,
    ) -> NativeResponse:
        if timeout <= 0:
            raise ValueError("native request timeout must be positive")
        if not isinstance(binary, bytes):
            raise ValueError("native binary payload must be bytes")
        request_id = f"{self._client_id}:{next(self._request_numbers)}"
        pending = _PendingRequest()

        header = {
            "protocol": {"major": PROTOCOL_MAJOR, "minor": PROTOCOL_MINOR},
            "id": request_id,
            "method": method,
            "params": params,
            "binary_len": len(binary),
        }
        try:
            encoded = encode_frame(header, binary)
        except NativeFrameError as error:
            raise _desktop_error(
                ErrorCode.INVALID_FRAME,
                "Native helper request'i frame'e donusturulemedi.",
                suggested_action="check_native_request",
                execution_state="not_started",
            ) from error

        with self._state_lock:
            process = self._process
            if (
                process is None
                or process.poll() is not None
                or (not allow_unready and not self._ready)
                or (self._closing and not allow_closing)
            ):
                raise _desktop_error(
                    ErrorCode.NATIVE_CRASHED,
                    "Native helper kullanilabilir degil.",
                    suggested_action="retry_read_only_native_request",
                    execution_state="not_started",
                )
            if len(self._pending) >= self.pending_limit:
                raise _desktop_error(
                    ErrorCode.BUSY,
                    "Native helper pending request sinirina ulasti.",
                    retryable=True,
                    suggested_action="retry_after_pending_requests_finish",
                    execution_state="not_started",
                )
            generation = self._generation
            self._pending[request_id] = pending
            writer_queue = self._writer_queue
            assert writer_queue is not None
            try:
                writer_queue.put_nowait(_OutboundFrame(encoded, request_id))
            except queue.Full:
                self._pending.pop(request_id, None)
                raise _desktop_error(
                    ErrorCode.BUSY,
                    "Native helper outgoing frame sinirina ulasti.",
                    retryable=True,
                    suggested_action="retry_after_pending_requests_finish",
                    execution_state="not_started",
                )

        if not pending.event.wait(timeout):
            with self._state_lock:
                timed_out = self._pending.pop(request_id, None) is pending
            if timed_out:
                self._send_cancel(process, generation, request_id)
                raise _desktop_error(
                    ErrorCode.TIMEOUT,
                    "Native helper request zaman asimina ugradi.",
                    suggested_action="inspect_native_status_before_retry",
                    execution_state="unknown",
                )
            pending.event.wait()

        if pending.error is not None:
            raise pending.error
        if pending.response is None:
            raise _desktop_error(
                ErrorCode.INVALID_FRAME,
                "Native helper bos response tamamladi.",
                suggested_action="check_native_protocol",
            )
        if pending.response.error is not None:
            raise self._remote_error(pending.response.error)
        return pending.response

    def _send_cancel(
        self,
        process: subprocess.Popen[bytes],
        generation: int,
        target_id: str,
    ) -> None:
        cancel_id = f"{self._client_id}:{next(self._request_numbers)}"
        header = {
            "protocol": {"major": PROTOCOL_MAJOR, "minor": PROTOCOL_MINOR},
            "id": cancel_id,
            "method": "cancel",
            "params": {"target_id": target_id},
            "binary_len": 0,
        }
        try:
            encoded = encode_frame(header)
        except NativeFrameError:
            return
        with self._state_lock:
            if self._process is not process or self._generation != generation:
                return
            writer_queue = self._writer_queue
            if writer_queue is not None:
                try:
                    writer_queue.put_nowait(_OutboundFrame(encoded, None))
                except queue.Full:
                    pass

    def _writer_loop(
        self,
        process: subprocess.Popen[bytes],
        generation: int,
        writer_queue: queue.Queue[_OutboundFrame | None],
    ) -> None:
        assert process.stdin is not None
        try:
            while True:
                outbound = writer_queue.get()
                if outbound is None:
                    return
                with self._state_lock:
                    if self._process is not process or self._generation != generation:
                        return
                    if (
                        outbound.request_id is not None
                        and outbound.request_id not in self._pending
                    ):
                        continue
                write_encoded_frame(process.stdin, outbound.encoded)
        except NativeFrameError:
            self._fail_generation(
                process,
                generation,
                _desktop_error(
                    ErrorCode.NATIVE_CRASHED,
                    "Native helper request pipe'i kapandi.",
                    suggested_action="retry_read_only_native_request",
                    execution_state="unknown",
                ),
            )

    def _reader_loop(
        self,
        process: subprocess.Popen[bytes],
        generation: int,
    ) -> None:
        assert process.stdout is not None
        try:
            while True:
                frame = read_frame(process.stdout)
                if frame is None:
                    raise EOFError
                response = decode_response(frame)
                with self._state_lock:
                    if self._process is not process or self._generation != generation:
                        return
                    pending = self._pending.pop(response.request_id, None)
                    if pending is not None:
                        pending.response = response
                        pending.event.set()
        except NativeProtocolMismatch:
            failure = _desktop_error(
                ErrorCode.PROTOCOL_MISMATCH,
                "Native helper uyumsuz protocol surumu dondurdu.",
                suggested_action="install_matching_native_binary",
            )
        except NativeFrameError:
            failure = _desktop_error(
                ErrorCode.INVALID_FRAME,
                "Native helper gecersiz frame dondurdu.",
                suggested_action="check_native_protocol",
            )
        except (EOFError, OSError):
            failure = _desktop_error(
                ErrorCode.NATIVE_CRASHED,
                "Native helper baglantisi kapandi.",
                suggested_action="retry_read_only_native_request",
                execution_state="unknown",
            )
        except Exception:
            failure = _desktop_error(
                ErrorCode.INVALID_FRAME,
                "Native helper response'u islenemedi.",
                suggested_action="check_native_protocol",
            )
        self._fail_generation(process, generation, failure)

    def _stderr_loop(
        self,
        process: subprocess.Popen[bytes],
        generation: int,
    ) -> None:
        assert process.stderr is not None
        while True:
            try:
                chunk = process.stderr.read(4096)
            except OSError:
                return
            if not chunk:
                return
            with self._state_lock:
                if self._process is not process or self._generation != generation:
                    return
                self._stderr.extend(chunk)
                if len(self._stderr) > _STDERR_LIMIT:
                    del self._stderr[:-_STDERR_LIMIT]

    def _remote_error(self, error: dict[str, Any]) -> DesktopError:
        raw_code = error["code"]
        aliases = {
            "UNSUPPORTED_PROTOCOL": ErrorCode.PROTOCOL_MISMATCH,
            "UNSUPPORTED_PROTOCOL_MINOR": ErrorCode.PROTOCOL_MISMATCH,
            "NOT_INITIALIZED": ErrorCode.PROTOCOL_MISMATCH,
            "ALREADY_INITIALIZED": ErrorCode.PROTOCOL_MISMATCH,
            "UNKNOWN_METHOD": ErrorCode.UNSUPPORTED,
            "INVALID_PARAMS": ErrorCode.INVALID_FRAME,
            "UNEXPECTED_BINARY": ErrorCode.INVALID_FRAME,
        }
        try:
            code = ErrorCode(raw_code)
        except ValueError:
            code = aliases.get(raw_code, ErrorCode.INVALID_FRAME)
        try:
            category = ErrorCategory(error["category"])
        except ValueError:
            category = ErrorCategory.IPC
        return DesktopError(
            code=code,
            message=error["message"],
            category=category,
            retryable=error["retryable"],
            suggested_action=(
                "install_matching_native_binary"
                if code == ErrorCode.PROTOCOL_MISMATCH
                else "check_native_request"
            ),
            backend="pcbridge-native",
        )

    def _decode_handshake(self, result: Any) -> NativeHandshake:
        if not isinstance(result, dict):
            raise self._invalid_initialize_response()
        names = ("instance_id", "native_version", "platform")
        values = {name: result.get(name) for name in names}
        if any(not isinstance(value, str) or not value for value in values.values()):
            raise self._invalid_initialize_response()
        raw_features = result.get("features")
        if not isinstance(raw_features, list) or any(
            not isinstance(feature, str) or not feature for feature in raw_features
        ):
            raise self._invalid_initialize_response()
        raw_build_id = result.get("build_id", "")
        return NativeHandshake(
            instance_id=values["instance_id"],
            native_version=values["native_version"],
            platform=values["platform"],
            features=frozenset(raw_features),
            build_id=raw_build_id if isinstance(raw_build_id, str) else "",
        )

    @staticmethod
    def _invalid_initialize_response() -> DesktopError:
        return _desktop_error(
            ErrorCode.INVALID_FRAME,
            "Native initialize response gecersiz.",
            suggested_action="check_native_protocol",
        )

    def _fail_pending(self, error: DesktopError) -> None:
        with self._state_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for item in pending:
            item.error = error
            item.event.set()

    def _fail_generation(
        self,
        process: subprocess.Popen[bytes],
        generation: int,
        error: DesktopError,
    ) -> None:
        with self._state_lock:
            if self._process is not process or self._generation != generation:
                return
            self._ready = False
            self._handshake = None
            writer_queue = self._writer_queue
            pending = list(self._pending.values())
            self._pending.clear()
        if writer_queue is not None:
            try:
                writer_queue.put_nowait(None)
            except queue.Full:
                pass
        for item in pending:
            item.error = error
            item.event.set()

    def _stop_process_locked(
        self,
        process: subprocess.Popen[bytes],
        *,
        wait_before_terminate: bool = True,
    ) -> None:
        with self._state_lock:
            writer_queue = self._writer_queue if self._process is process else None
        if writer_queue is not None:
            try:
                writer_queue.put_nowait(None)
            except queue.Full:
                pass

        if wait_before_terminate:
            try:
                process.wait(timeout=self.shutdown_timeout)
            except subprocess.TimeoutExpired:
                process.terminate()
        elif process.poll() is None:
            process.terminate()

        if process.poll() is None:
            try:
                process.wait(timeout=self.shutdown_timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

        current = threading.current_thread()
        for thread in (
            self._reader_thread,
            self._writer_thread,
            self._stderr_thread,
        ):
            if thread is not None and thread is not current:
                thread.join(timeout=0.2)

        with self._state_lock:
            if self._process is process:
                registry_entry = self._registry_entry
                self._process = None
                self._writer_queue = None
                self._ready = False
                self._handshake = None
                self._registry_entry = None
            else:
                registry_entry = None
        if registry_entry is not None:
            self._registry.unregister(registry_entry)


__all__ = ["NativeClient", "NativeHandshake", "NativeResponse"]
