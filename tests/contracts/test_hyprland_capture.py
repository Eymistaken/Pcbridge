"""Hyprland shots preserve coordinates and never publish under a changed grant."""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from pcbridge.config import load_config
from pcbridge.desktop import capture, compositor, hyprland, monitors
from pcbridge.desktop.backends.rust import NativeScreenCast, RustCaptureProvider
from pcbridge.desktop.capabilities import CapabilityState
from pcbridge.desktop.errors import DesktopError, ErrorCode
from pcbridge.desktop.lease import LeaseToken
from pcbridge.desktop.safety import Decision
from tests.contracts.test_capture_backend_selection import FakeNativeClient, png_bytes
from tests.contracts.test_hyprland_monitors import output

ROOT = Path(__file__).resolve().parents[2]


class Gate:
    token = LeaseToken("hyprland-capture-contract", 2)
    allowed = True
    def last_token(self): return self.token
    def current_token(self): return self.token
    def verify(self, token):
        return Decision(self.allowed and token == self.token, "Grant revoked", code=ErrorCode.REVOKED)


class HyprlandCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        cfg = load_config(ROOT / "config.example.toml")
        self.table = monitors.resolve_state(monitors._hyprland_state([
            {**output("A", 0, 0), "width": 64, "height": 40},
            {**output("B", 64, 0), "width": 64, "height": 40}]))
        self.gate = Gate()
        self.client = FakeNativeClient({row.connector: png_bytes(64, 40) for row in self.table})
        self.handle = NativeScreenCast(cfg, gate=self.gate, client=self.client)
        self.provider = RustCaptureProvider(cfg, screencast=self.handle)
        self.window = {"address": "0x123", "stableId": "7", "pid": 100,
            "mapped": True, "class": "fixture", "title": "Window",
            "at": [67, 4], "size": [20, 24]}
        for patch in (mock.patch.object(compositor, "current", return_value=compositor.HYPRLAND),
                      mock.patch.object(monitors, "list_monitors", side_effect=lambda **kwargs: self.table),
                      mock.patch.object(hyprland, "_query", side_effect=lambda *args, **kwargs: self.window.copy()),
                      mock.patch.object(capture, "_run_grab", side_effect=AssertionError("External capture is forbidden"))):
            patch.start()
            self.addCleanup(patch.stop)

    def shot(self, spec):
        return self.provider.capture(spec, out_dir=self.root, scale_long_edge=32, include_pointer=False)

    def test_monitor_shot_uses_native_scheme_and_shared_scaled_coordinate_mapping(self):
        shot = self.shot(2)[0]
        self.assertEqual(shot.offset, (64, 0))
        self.assertEqual(shot.size, (64, 40))
        self.assertEqual(shot.scaled, (32, 20))
        self.assertEqual(capture.to_global(16, 10, shot=shot.id, dirs=[self.root]), (96, 20))
        frame = next(request for request in self.client.requests if request["method"] == "capture.frame")
        self.assertEqual(frame["params"]["display_id"], "hyprland:B")

    def test_window_is_a_native_monitor_region_with_a_global_offset(self):
        shot = self.shot("window")[0]
        self.assertTrue(shot.region)
        self.assertEqual(shot.offset, (67, 4))
        self.assertEqual(shot.size, (20, 24))
        self.assertEqual(shot.desktop_units, (20, 24))

    def test_revoke_during_publication_withdraws_all_new_pngs_and_metadata(self):
        publish = capture._publish_file
        def revoked(source, destination):
            publish(source, destination)
            self.gate.allowed = False
        with mock.patch.object(capture, "_publish_file", side_effect=revoked):
            with self.assertRaises(DesktopError) as caught:
                self.shot("all")
        self.assertEqual(caught.exception.code, ErrorCode.REVOKED)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_window_focus_change_after_frame_refuses_publication(self):
        request = self.client.request
        def changed(method, *args, **kwargs):
            response = request(method, *args, **kwargs)
            if method == "capture.frame": self.window["address"] = "0x456"
            return response
        with mock.patch.object(self.client, "request", side_effect=changed):
            with self.assertRaises(DesktopError) as caught:
                self.shot("window")
        self.assertEqual(caught.exception.code, ErrorCode.TARGET_MISMATCH)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_window_or_unreadable_focus_has_a_typed_refusal(self):
        self.window = {}
        with self.assertRaises(DesktopError) as caught:
            self.shot("window")
        self.assertEqual(caught.exception.code, ErrorCode.BACKEND_UNAVAILABLE)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_window_geometry_has_a_typed_refusal(self):
        for coordinate in (float("nan"), float("inf"), 1 << 4096, True):
            with self.subTest(coordinate_type=type(coordinate).__name__):
                self.window["at"] = [coordinate, 4]
                with self.assertRaises(DesktopError) as caught:
                    self.shot("window")
                self.assertEqual(caught.exception.code, ErrorCode.BACKEND_UNAVAILABLE)
                self.assertEqual(list(self.root.iterdir()), [])

    def test_same_geometry_output_replacement_during_render_refuses_old_shot(self):
        from dataclasses import replace

        request = self.client.request
        def changed(method, *args, **kwargs):
            response = request(method, *args, **kwargs)
            if method == "capture.frame":
                self.table = [replace(row, connector="replacement" if row.connector == "B" else row.connector)
                              for row in self.table]
            return response
        with mock.patch.object(self.client, "request", side_effect=changed):
            with self.assertRaises(DesktopError) as caught:
                self.shot(2)
        self.assertEqual(caught.exception.code, ErrorCode.DISPLAY_CHANGED)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_protocol_absence_is_reported_as_unavailable_without_legacy_fallback(self):
        with mock.patch("pcbridge.desktop.backends.rust.native_binary_ready", return_value=(True, "")), \
                mock.patch.object(self.provider, "_hyprland_protocols_ready", return_value=(False, "Missing image-copy")):
            result = self.provider.probe_capabilities()
        self.assertEqual(result["capture.monitor"].state, CapabilityState.UNAVAILABLE)
        self.assertEqual(result["capture.monitor"].reason_code, ErrorCode.BACKEND_UNAVAILABLE)
        self.assertEqual(result["capture.window"].state, CapabilityState.UNAVAILABLE)

    def test_an_old_helper_cannot_claim_hyprland_on_demand_capture(self):
        self.client.session_error = {"code": "UNKNOWN_METHOD", "message": "Old helper", "category": "protocol"}
        with self.assertRaises(DesktopError): self.shot("all")
        self.assertFalse(self.handle.is_open())
        self.assertFalse(any(item["method"] == "capture.frame" for item in self.client.requests))


if __name__ == "__main__":
    unittest.main()
