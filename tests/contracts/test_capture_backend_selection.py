#!/usr/bin/env python3
"""Which capture backend is chosen, and whether swapping it changes a shot.

Two questions, both answerable without a compositor:

1. Does the selector implement the table in `PLAN.md`? A forced native backend
   that cannot start must stay selected and fail visibly -- quietly becoming a
   Python screencast would hide exactly the thing forcing it was meant to find.
2. Does a shot taken through the native handle come out identical to one taken
   through the legacy handle? The shot id, the offset, the scale and the
   coordinate it maps back to are what a later click depends on, so "the same
   pixels" is not enough.

No D-Bus, no PipeWire, no native helper process: both handles are fakes that
hand `capture.py` the same PNG bytes. Safe with every live flag unset.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.desktop import capture as capturelib  # noqa: E402
from pcbridge.desktop import monitors as monitorslib  # noqa: E402
from pcbridge.desktop.backends.python import PythonCaptureProvider  # noqa: E402
from pcbridge.desktop.backends.rust import (  # noqa: E402
    BACKEND_NAME,
    BackendSelection,
    NativeCaptureError,
    NativeScreenCast,
    RustCaptureProvider,
    select_capture_backend,
)
from pcbridge.desktop.capabilities import CapabilityState  # noqa: E402
from pcbridge.desktop.errors import DesktopError, ErrorCategory, ErrorCode  # noqa: E402
from pcbridge.desktop.lease import LeaseToken  # noqa: E402
from pcbridge.desktop.runtime import select_capture_provider  # noqa: E402
from pcbridge.config import NativeSpec, load_config  # noqa: E402
from pcbridge.native import NativeResponse  # noqa: E402

try:
    from PIL import Image

    PIL_OK = True
except Exception:  # noqa: BLE001 - the suite reports the skip itself
    PIL_OK = False


MONITORS = [
    monitorslib.Monitor(
        index=1, connector="DP-4", x=0, y=0, width=1920, height=1080,
        scale=1.0, primary=False, name="left",
    ),
    monitorslib.Monitor(
        index=2, connector="DP-3", x=1920, y=0, width=1920, height=1080,
        scale=1.0, primary=True, name="right",
    ),
]


def png_bytes(width: int, height: int) -> bytes:
    """A PNG with structure, so a blank result cannot pass for a real one."""
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            pixels[x, y] = (x % 256, y % 256, (x + y) % 256)
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class FakeLegacyScreenCast:
    """The Python handle: writes the frame itself and reports nothing else."""

    def __init__(self, frames: dict[str, bytes]) -> None:
        self.frames = frames
        self.cursor = True

    def is_open(self) -> bool:
        return True

    def ensure_cursor(self, cursor: bool) -> bool:
        changed = bool(cursor) != self.cursor
        self.cursor = bool(cursor)
        return changed

    def capture(self, connector: str, path) -> dict:
        Path(path).write_bytes(self.frames[connector])
        return {"ok": True, "path": str(path), "monitor": connector}


class FakeNativeClient:
    """Answers `capture.frame` the way the helper does, recording the request."""

    def __init__(self, frames: dict[str, bytes], *, wait_ms: float = 40.0) -> None:
        self.frames = frames
        self.wait_ms = wait_ms
        self.requests: list[dict] = []
        self.error: dict | None = None
        self.session_error: dict | None = None
        self.pixel_size_override: list[int] | None = None

    def request(self, method, params=None, *, binary=b"", timeout=None):
        self.requests.append({"method": method, "params": dict(params or {})})
        if method == "capture.session_open":
            if self.session_error is not None:
                return NativeResponse(
                    request_id="fake", result=None, binary=b"", error=self.session_error
                )
            return NativeResponse(
                request_id="fake",
                result={
                    "outcome": "opened",
                    "session_id": params["session_id"],
                    "topology_id": params["topology_id"],
                    "monitors": sorted(self.frames),
                    "include_pointer": params["include_pointer"],
                    "backend": BACKEND_NAME,
                },
                binary=b"",
                error=None,
            )
        if self.error is not None:
            return NativeResponse(
                request_id="fake", result=None, binary=b"", error=self.error
            )
        connector = str(params["display_id"]).split(":", 1)[1]
        blob = self.frames[connector]
        with Image.open(__import__("io").BytesIO(blob)) as image:
            size = list(image.size)
        return NativeResponse(
            request_id="fake",
            result={
                "pixel_size": self.pixel_size_override or size,
                "wait_ms": self.wait_ms,
                "encode_ms": 1.0,
                "frame_identity_source": "source_monotonic_clock",
                "stale_frames": 0,
                "backend": BACKEND_NAME,
                "mime_type": "image/png",
            },
            binary=blob,
            error=None,
        )

    def close(self) -> None:
        pass


def native_handle(cfg, client) -> NativeScreenCast:
    handle = NativeScreenCast(cfg, gate=FakeGate(), client=client)
    with mock.patch.object(monitorslib, "list_monitors", return_value=MONITORS):
        handle.start([monitor.connector for monitor in MONITORS], cursor=True)
    return handle


class FakeGate:
    def current_token(self):
        return LeaseToken(grant_id="fixture-grant", revoke_epoch=4)

    def last_token(self):
        return self.current_token()


# --------------------------------------------------------------- the table


class SelectorTable(unittest.TestCase):
    def test_python_never_reaches_for_the_native_helper(self):
        for ready in (True, False):
            with self.subTest(ready=ready):
                self.assertEqual(
                    select_capture_backend(requested="python", native_ready=ready),
                    BackendSelection(backend="python"),
                )

    def test_forced_rust_stays_selected_when_the_helper_is_missing(self):
        selection = select_capture_backend(
            requested="rust", native_ready=False, native_reason="helper yok"
        )
        self.assertEqual(selection.backend, "rust")
        self.assertFalse(
            selection.degraded,
            "a forced backend that silently degrades defeats the point of forcing it",
        )
        self.assertEqual(selection.reason, "helper yok")

    def test_auto_prefers_rust_and_falls_back_visibly(self):
        self.assertEqual(
            select_capture_backend(requested="auto", native_ready=True),
            BackendSelection(backend="rust"),
        )
        fallback = select_capture_backend(
            requested="auto", native_ready=False, native_reason="helper yok"
        )
        self.assertEqual(fallback.backend, "python")
        self.assertTrue(fallback.degraded)
        self.assertIn("helper yok", fallback.reason)

    def test_an_unknown_setting_degrades_rather_than_guessing_rust(self):
        selection = select_capture_backend(requested="zaphod", native_ready=True)
        self.assertEqual(selection.backend, "python")
        self.assertTrue(selection.degraded)
        self.assertIn("zaphod", selection.reason)

    def test_case_and_padding_do_not_change_the_answer(self):
        self.assertEqual(
            select_capture_backend(requested="  RUST ", native_ready=True).backend,
            "rust",
        )


class ProviderSelection(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config(str(ROOT / "config.example.toml"))

    def _provider(self, requested: str, ready: bool):
        cfg = self.cfg
        object.__setattr__(cfg.native, "capture", requested)
        with mock.patch(
            "pcbridge.desktop.backends.rust.native_binary_ready",
            return_value=(ready, "" if ready else "helper yok"),
        ):
            return select_capture_provider(cfg, gate=None)

    def test_the_shipped_default_is_auto(self):
        """Task 4.3: after the Linux parity gate, `auto` ships as the default."""
        cfg = load_config(str(ROOT / "config.example.toml"))
        self.assertEqual(cfg.native.capture, "auto")
        self.assertEqual(
            NativeSpec().capture, "auto", "a config without [native] gets the same"
        )

    def test_auto_keeps_an_explicit_gnome_screenshot_choice(self):
        object.__setattr__(self.cfg.desktop, "capture_backend", "gnome-screenshot")
        chosen = self._provider("auto", True)
        self.assertIsInstance(chosen, PythonCaptureProvider)
        self.assertNotIsInstance(chosen, RustCaptureProvider)
        self.assertEqual(chosen.degraded_reason, "", "an explicit choice is not a fallback")
        self.assertIsInstance(self._provider("rust", True), RustCaptureProvider)

    def test_each_setting_builds_the_provider_it_names(self):
        self.assertIsInstance(self._provider("python", True), PythonCaptureProvider)
        self.assertIsInstance(self._provider("rust", True), RustCaptureProvider)
        self.assertIsInstance(self._provider("auto", True), RustCaptureProvider)
        fallback = self._provider("auto", False)
        self.assertIsInstance(fallback, PythonCaptureProvider)
        self.assertNotIsInstance(fallback, RustCaptureProvider)

    def test_a_fallback_is_visible_in_the_capability_report(self):
        provider = self._provider("auto", False)
        with mock.patch.object(monitorslib, "list_monitors", return_value=MONITORS):
            capabilities = provider.probe_capabilities()
        monitor = capabilities["capture.monitor"]
        self.assertIn(
            "helper yok",
            " ".join(monitor.limitations),
            "a client cannot tell it was downgraded unless the report says so",
        )
        self.assertIsNot(monitor.state, CapabilityState.SUPPORTED)


# ------------------------------------------------------- the native handle


@unittest.skipUnless(PIL_OK, "Pillow gerekli")
class NativeHandle(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config(str(ROOT / "config.example.toml"))
        self.frames = {
            "DP-4": png_bytes(1920, 1080),
            "DP-3": png_bytes(1920, 1080),
        }
        self.client = FakeNativeClient(self.frames)

    def test_the_request_names_the_grant_topology_and_scheme(self):
        handle = native_handle(self.cfg, self.client)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(monitorslib, "list_monitors", return_value=MONITORS):
                handle.capture("DP-4", Path(tmp) / "frame.png")
        params = next(
            request["params"]
            for request in self.client.requests
            if request["method"] == "capture.frame"
        )
        self.assertEqual(params["display_id"], "mutter:DP-4")
        self.assertEqual(params["grant_id"], "fixture-grant")
        self.assertEqual(params["revoke_epoch"], 4)
        self.assertEqual(params["freshness"], "after_request")
        self.assertEqual(
            params["topology_id"],
            monitorslib.topology_id(MONITORS),
            "the helper must be told the layout this target was chosen from",
        )

    def test_start_opens_the_session_before_any_frame(self):
        """Task 4.3: the share, and so the indicator, starts with the grant."""
        handle = native_handle(self.cfg, self.client)
        self.assertTrue(handle.is_open())
        self.assertEqual(
            [request["method"] for request in self.client.requests],
            ["capture.session_open"],
            "opening must not read a frame",
        )
        params = self.client.requests[0]["params"]
        self.assertEqual(params["grant_id"], "fixture-grant")
        self.assertEqual(params["revoke_epoch"], 4)
        self.assertEqual(params["topology_id"], monitorslib.topology_id(MONITORS))
        self.assertIs(params["include_pointer"], True)

    def test_an_older_helper_without_session_open_still_captures_on_demand(self):
        self.client.session_error = {
            "code": "UNKNOWN_METHOD",
            "message": "unknown method capture.session_open",
            "retryable": False,
            "category": "protocol",
        }
        handle = NativeScreenCast(self.cfg, gate=FakeGate(), client=self.client)
        with mock.patch.object(monitorslib, "list_monitors", return_value=MONITORS):
            started = handle.start([monitor.connector for monitor in MONITORS])
            self.assertTrue(handle.is_open())
            self.assertIs(started["on_demand"], True)
            with tempfile.TemporaryDirectory() as tmp:
                frame = handle.capture("DP-4", Path(tmp) / "frame.png")
        self.assertTrue(frame["ok"])

    def test_a_refused_session_leaves_the_share_closed_and_the_error_typed(self):
        self.client.session_error = {
            "code": "REVOKED",
            "message": "session request does not match the bound desktop grant",
            "retryable": False,
            "category": "safety",
        }
        provider = RustCaptureProvider(
            self.cfg,
            screencast=NativeScreenCast(self.cfg, gate=FakeGate(), client=self.client),
        )
        with mock.patch.object(monitorslib, "list_monitors", return_value=MONITORS):
            with self.assertRaises(DesktopError) as raised:
                provider.start(cursor=True)
        self.assertIs(raised.exception.code, ErrorCode.REVOKED)
        self.assertIs(raised.exception.category, ErrorCategory.SAFETY)
        self.assertFalse(provider.is_open())

    def test_start_without_a_grant_is_refused_before_any_request(self):
        handle = NativeScreenCast(self.cfg, gate=None, client=self.client)
        with mock.patch.object(monitorslib, "list_monitors", return_value=MONITORS):
            with self.assertRaises(NativeCaptureError) as raised:
                handle.start(["DP-4"])
        self.assertEqual(self.client.requests, [])
        self.assertFalse(handle.is_open())
        self.assertIs(raised.exception.desktop_error.code, ErrorCode.GRANT_REQUIRED)
        self.assertIs(raised.exception.desktop_error.category, ErrorCategory.SAFETY)

    def test_capture_after_the_grant_is_gone_is_refused_before_any_frame(self):
        handle = native_handle(self.cfg, self.client)
        handle.gate = None
        with self.assertRaises(NativeCaptureError) as raised:
            handle.capture("DP-4", Path("/tmp/never-written.png"))
        self.assertEqual(
            [request["method"] for request in self.client.requests],
            ["capture.session_open"],
        )
        self.assertIs(raised.exception.desktop_error.code, ErrorCode.GRANT_REQUIRED)
        self.assertIs(raised.exception.desktop_error.category, ErrorCategory.SAFETY)

    def _provider_refusal(self, handle) -> DesktopError:
        provider = RustCaptureProvider(self.cfg, screencast=handle)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(monitorslib, "list_monitors", return_value=MONITORS):
                with self.assertRaises(DesktopError) as raised:
                    provider.capture(
                        1, out_dir=Path(tmp), scale_long_edge=0, include_pointer=False
                    )
            self.assertEqual(list(Path(tmp).iterdir()), [], "a refusal leaves nothing")
        return raised.exception

    def test_a_helper_refusal_keeps_its_code_through_the_provider(self):
        """What a tool sees, not only what the handle raises.

        Measured 2026-09-13 in the Task 4.2 gate: a revoked grant reached the
        caller as BACKEND_UNAVAILABLE/capability. `capture.py` wrapped the
        native exception in a plain CaptureError and the provider reported
        that as a missing backend, so every typed helper refusal lost its code.
        """
        cases = (
            ("REVOKED", ErrorCode.REVOKED, ErrorCategory.SAFETY),
            ("DISPLAY_CHANGED", ErrorCode.DISPLAY_CHANGED, ErrorCategory.CAPTURE),
            ("FRAME_TIMEOUT", ErrorCode.FRAME_TIMEOUT, ErrorCategory.CAPTURE),
        )
        for raw_code, code, category in cases:
            with self.subTest(code=raw_code):
                self.client.error = {
                    "code": raw_code,
                    "message": f"the helper said {raw_code}",
                    "retryable": False,
                    "category": category.value,
                }
                error = self._provider_refusal(native_handle(self.cfg, self.client))
                self.assertIs(error.code, code)
                self.assertIs(error.category, category)

    def test_a_missing_grant_is_a_safety_refusal_through_the_provider(self):
        handle = native_handle(self.cfg, self.client)
        handle.gate = None
        error = self._provider_refusal(handle)
        self.assertIs(error.code, ErrorCode.GRANT_REQUIRED)
        self.assertIs(error.category, ErrorCategory.SAFETY)
        self.assertEqual(
            [request["method"] for request in self.client.requests],
            ["capture.session_open"],
        )

    def test_a_stream_of_the_wrong_size_is_reported_not_rescaled(self):
        self.client.pixel_size_override = [1280, 720]
        handle = native_handle(self.cfg, self.client)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(monitorslib, "list_monitors", return_value=MONITORS):
                with self.assertRaises(NativeCaptureError) as raised:
                    handle.capture("DP-4", Path(tmp) / "frame.png")
        self.assertIn("1280x720", str(raised.exception))

    def test_a_helper_refusal_keeps_its_code(self):
        self.client.error = {
            "code": "DISPLAY_CHANGED",
            "message": "the display layout changed before capture",
            "retryable": True,
            "category": "capture",
        }
        handle = native_handle(self.cfg, self.client)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(monitorslib, "list_monitors", return_value=MONITORS):
                with self.assertRaises(NativeCaptureError) as raised:
                    handle.capture("DP-4", Path(tmp) / "frame.png")
        cause = raised.exception.desktop_error
        self.assertIsInstance(cause, DesktopError)
        self.assertIs(cause.code, ErrorCode.DISPLAY_CHANGED)

    def test_a_revoke_arrives_as_a_safety_error(self):
        self.client.error = {
            "code": "REVOKED",
            "message": "capture request does not match the bound desktop grant",
            "retryable": False,
            "category": "safety",
        }
        handle = native_handle(self.cfg, self.client)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(monitorslib, "list_monitors", return_value=MONITORS):
                with self.assertRaises(NativeCaptureError) as raised:
                    handle.capture("DP-4", Path(tmp) / "frame.png")
        self.assertIs(raised.exception.desktop_error.code, ErrorCode.REVOKED)

    def test_taken_at_comes_from_when_the_frame_arrived(self):
        self.client.wait_ms = 250.0
        handle = native_handle(self.cfg, self.client)
        before = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(monitorslib, "list_monitors", return_value=MONITORS):
                frame = handle.capture("DP-4", Path(tmp) / "frame.png")
        self.assertGreaterEqual(frame["taken_at"], before + 0.24)
        self.assertLessEqual(frame["taken_at"], time.time() + 1.0)


# ----------------------------------------------------------- the same shot


@unittest.skipUnless(PIL_OK, "Pillow gerekli")
class ShotParity(unittest.TestCase):
    """The acceptance: swapping the handle must not change the shot."""

    def setUp(self):
        self.cfg = load_config(str(ROOT / "config.example.toml"))
        self.frames = {
            "DP-4": png_bytes(1920, 1080),
            "DP-3": png_bytes(1920, 1080),
        }

    def _shots(self, screencast):
        with tempfile.TemporaryDirectory() as out:
            with mock.patch.object(monitorslib, "list_monitors", return_value=MONITORS):
                shots = capturelib.capture(
                    "all",
                    out_dir=Path(out),
                    scale_long_edge=1536,
                    include_pointer=False,
                    screencast=screencast,
                )
                return [
                    {
                        "id_prefix": shot.id.split("-")[0],
                        "monitor": shot.monitor.index,
                        "connector": shot.monitor.connector,
                        "offset": shot.offset,
                        "size": shot.size,
                        "scaled": shot.scaled,
                        "scale": shot.scale,
                        "meta_exists": (Path(out) / f"{shot.id}.json").exists(),
                        "global_center": shot.to_global(
                            shot.scaled[0] // 2, shot.scaled[1] // 2
                        ),
                    }
                    for shot in shots
                ]

    def test_both_handles_produce_the_same_shots_and_coordinates(self):
        legacy = self._shots(FakeLegacyScreenCast(self.frames))
        native = self._shots(native_handle(self.cfg, FakeNativeClient(self.frames)))

        self.assertEqual(len(legacy), 2)
        self.assertEqual(legacy, native)
        self.assertEqual([shot["id_prefix"] for shot in native], ["m1", "m2"])
        self.assertEqual(
            [shot["global_center"] for shot in native],
            [(960, 540), (2880, 540)],
            "a shot taken natively must map back to the same desktop point",
        )

    def test_a_shot_records_when_its_own_frame_arrived(self):
        """A slow frame must age from when it was taken, not from the batch.

        `taken_at` drives the staleness guard that stopped a click landing in
        the wrong application once. In an `all` capture the monitors are read
        one after another, so treating the first one's clock as everyone's
        makes the last image look fresher than it is.
        """
        client = FakeNativeClient(self.frames, wait_ms=500.0)
        started = time.time()
        with tempfile.TemporaryDirectory() as out:
            with mock.patch.object(monitorslib, "list_monitors", return_value=MONITORS):
                shots = capturelib.capture(
                    1,
                    out_dir=Path(out),
                    scale_long_edge=0,
                    include_pointer=False,
                    screencast=native_handle(self.cfg, client),
                )
        # The fake reports a 500 ms wait without performing one, so the stamp
        # lands slightly ahead of the wall clock here. `capture.py` accepts up
        # to a second of that and rejects anything further out, because a stamp
        # from the future would switch the staleness guard off silently.
        self.assertGreaterEqual(shots[0].taken_at, started + 0.49)
        self.assertLessEqual(shots[0].taken_at, time.time() + 1.0)

    def test_a_backend_without_a_clock_keeps_the_old_behavior(self):
        with tempfile.TemporaryDirectory() as out:
            with mock.patch.object(monitorslib, "list_monitors", return_value=MONITORS):
                shots = capturelib.capture(
                    1,
                    out_dir=Path(out),
                    scale_long_edge=0,
                    include_pointer=False,
                    screencast=FakeLegacyScreenCast(self.frames),
                )
        self.assertGreater(shots[0].taken_at, 0.0)
        self.assertLessEqual(shots[0].age, 5.0)

    def test_the_shot_id_format_and_metadata_survive_the_swap(self):
        for shot in self._shots(native_handle(self.cfg, FakeNativeClient(self.frames))):
            self.assertTrue(shot["meta_exists"], "the lookup record must be written")
        with tempfile.TemporaryDirectory() as out:
            with mock.patch.object(monitorslib, "list_monitors", return_value=MONITORS):
                shots = capturelib.capture(
                    2,
                    out_dir=Path(out),
                    scale_long_edge=0,
                    include_pointer=False,
                    screencast=native_handle(self.cfg, FakeNativeClient(self.frames)),
                )
            self.assertEqual(len(shots), 1)
            self.assertRegex(shots[0].id, capturelib.SHOT_ID_RE)
            reloaded = capturelib.load_shot(shots[0].id, [Path(out)])
            self.assertEqual(reloaded.offset, (1920, 0))
            self.assertEqual(reloaded.to_global(10, 10), (1930, 10))


if __name__ == "__main__":
    unittest.main(verbosity=2)
