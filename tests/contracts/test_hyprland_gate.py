"""Exact visibility and idle evidence at the shared safety boundary."""

from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from pcbridge.config import DesktopSpec, NativeSpec
from pcbridge.desktop import session
from pcbridge.desktop.errors import DesktopError, ErrorCategory, ErrorCode
from pcbridge.desktop.lease import LeaseStore
from pcbridge.desktop.resourcewatch import ResourceWatch
from pcbridge.desktop.safety import (ActivityObservation, ActivityState, SafetyGate,
    ScreenLockObservation, ScreenLockState)


class MutableState:
    lock = ScreenLockState.KNOWN_UNLOCKED
    activity = ActivityState.KNOWN
    idle = 120_000

    def screen_lock(self):
        return ScreenLockObservation(self.lock, time.time())

    def user_activity(self):
        return ActivityObservation(self.activity, self.idle if self.activity == ActivityState.KNOWN else None, time.time())


class HyprlandGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cfg = SimpleNamespace(state_dir=self.root, audit_log=self.root / "audit.log",
            desktop=DesktopSpec(enabled=True), native=NativeSpec())
        self.state = MutableState()
        self.presented = set()
        self.owner = mock.Mock()
        self.owner.open.side_effect = lambda token: self.presented.add(token) or {"ready": True}
        self.gate = SafetyGate(self.cfg, state_provider=self.state)
        for patch in (
            mock.patch.object(session, "desktop_kind", return_value=session.HYPRLAND),
            mock.patch("pcbridge.native.discovery.discover_native_binary", return_value=Path("/fixture/native")),
            mock.patch("pcbridge.desktop.glowowner.FrameOwner", return_value=self.owner),
            mock.patch("pcbridge.desktop.glowstate.read", side_effect=lambda directory, token, **kwargs:
                {"ready": True} if token in self.presented else None),
            mock.patch("pcbridge.desktop.glowstate.read_on_current_outputs", side_effect=lambda directory, token, **kwargs:
                {"ready": True} if token in self.presented else None),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.gate.close)

    def unlock(self):
        self.gate.unlock(5, "contract")
        return self.gate.last_token()

    def test_grant_success_waits_for_frame_and_keeps_the_initial_hard_deadline(self):
        observations = []
        def open_frame(token):
            observations.append(self.gate.check("mouse", force=True).code)
            self.presented.add(token)
            return {"ready": True}
        self.owner.open.side_effect = open_frame
        token = self.unlock()
        self.assertEqual(observations, [ErrorCode.BACKEND_UNAVAILABLE])
        snapshot = LeaseStore(self.root).snapshot()
        self.assertEqual(snapshot.token(), token)
        self.assertAlmostEqual(snapshot.until - snapshot.raw["granted"], 300, places=2)
        self.assertTrue(self.gate.is_unlocked())

    def test_disabled_locked_unknown_lock_and_unknown_idle_never_spawn_a_frame(self):
        cases = [(False, ScreenLockState.KNOWN_UNLOCKED, ActivityState.KNOWN, ErrorCode.DESKTOP_DISABLED),
                 (True, ScreenLockState.KNOWN_LOCKED, ActivityState.KNOWN, ErrorCode.SCREEN_LOCKED),
                 (True, ScreenLockState.UNKNOWN, ActivityState.KNOWN, ErrorCode.LOCK_STATE_UNKNOWN),
                 (True, ScreenLockState.KNOWN_UNLOCKED, ActivityState.UNKNOWN, ErrorCode.ACTIVITY_UNKNOWN)]
        for enabled, lock, activity, code in cases:
            with self.subTest(code=code):
                self.cfg.desktop.enabled = enabled
                self.state.lock, self.state.activity = lock, activity
                with self.assertRaises(DesktopError) as caught:
                    self.unlock()
                self.assertEqual(caught.exception.code, code)
                self.owner.open.assert_not_called()
                self.assertIsNone(self.gate.current_token())

    def test_frame_startup_failure_conditionally_retires_only_its_token(self):
        self.owner.open.side_effect = DesktopError(code=ErrorCode.BACKEND_UNAVAILABLE,
            message="Frame unavailable", category=ErrorCategory.SAFETY, retryable=True, suggested_action="doctor")
        with self.assertRaises(DesktopError):
            self.unlock()
        self.assertIsNone(self.gate.current_token())
        self.assertIsNone(self.gate.last_token())

    def test_replacement_during_frame_startup_is_preserved_and_old_unlock_is_refused(self):
        replacement = []
        def open_frame(token):
            now = time.time()
            replacement.append(LeaseStore(self.root).grant(until=now + 60, reason="replacement",
                granted=now, granted_by="contract").token())
            self.presented.add(token)
            return {"ready": True}
        self.owner.open.side_effect = open_frame
        with self.assertRaises(DesktopError) as caught:
            self.unlock()
        self.assertEqual(caught.exception.code, ErrorCode.REVOKED)
        self.assertEqual(self.gate.current_token(), replacement[0])

    def test_frame_loss_refuses_read_write_force_and_each_action_without_touching_the_lease(self):
        token = self.unlock()
        self.presented.clear()
        before = LeaseStore(self.root).read()
        for write, force in ((False, False), (True, False), (True, True)):
            self.assertEqual(self.gate.check("desktop", write=write, force=force).code, ErrorCode.BACKEND_UNAVAILABLE)
        self.assertEqual(self.gate.verify(token).code, ErrorCode.BACKEND_UNAVAILABLE)
        self.assertEqual(LeaseStore(self.root).read(), before)
        self.assertFalse(self.gate.is_unlocked())
        self.assertIn("paused", self.gate.status_line())

    def test_unlock_and_result_metadata_require_presentation_on_current_outputs(self):
        with mock.patch("pcbridge.desktop.glowstate.read_on_current_outputs", return_value=None):
            with self.assertRaises(DesktopError):
                self.unlock()
        self.assertIsNone(self.gate.current_token())
        token = self.unlock()
        with mock.patch("pcbridge.desktop.glowstate.read_on_current_outputs", return_value=None):
            with self.assertRaises(DesktopError):
                self.gate.grant_info(token)
        self.assertIsNone(self.gate.current_token())
        self.assertFalse(self.gate.is_unlocked())

    def test_changed_outputs_refuse_admission_and_each_action_before_presentation_expires(self):
        token = self.unlock()
        before = LeaseStore(self.root).read()
        with mock.patch("pcbridge.desktop.glowstate.read_on_current_outputs", return_value=None):
            self.assertTrue(self.gate.visible_frame(token))
            self.assertTrue(self.gate.resource_guard(token))
            self.assertEqual(self.gate.check("mouse", force=True).code, ErrorCode.BACKEND_UNAVAILABLE)
            self.assertEqual(self.gate.verify(token).code, ErrorCode.BACKEND_UNAVAILABLE)
        self.assertEqual(LeaseStore(self.root).read(), before)

    def test_force_requires_known_idle_and_per_action_does_not_repeat_the_conflict_threshold(self):
        token = self.unlock()
        self.state.idle = 0
        self.assertEqual(self.gate.check("mouse").code, ErrorCode.USER_ACTIVE)
        self.assertTrue(self.gate.check("mouse", force=True))
        self.assertTrue(self.gate.verify(token))
        self.state.activity = ActivityState.UNKNOWN
        self.assertEqual(self.gate.check("mouse", force=True).code, ErrorCode.ACTIVITY_UNKNOWN)
        self.assertEqual(self.gate.verify(token).code, ErrorCode.ACTIVITY_UNKNOWN)

    def test_old_admitted_action_cannot_adopt_the_replacement_frame(self):
        first = self.unlock()
        self.assertTrue(self.gate.check("mouse", force=True))
        replacement = self.unlock()
        self.assertNotEqual(first, replacement)
        self.assertEqual(self.gate.verify(first).code, ErrorCode.REVOKED)
        self.assertTrue(self.gate.verify(replacement))

    def test_nonowner_gate_close_does_not_retire_the_resident_grant(self):
        token = self.unlock()
        consumer = SafetyGate(self.cfg, state_provider=self.state)
        consumer.close()
        self.assertEqual(self.gate.current_token(), token)

    def test_python_capture_and_native_legacy_fallback_cannot_cross_the_hyprland_boundary(self):
        from pcbridge.desktop.backends.python import PythonCaptureProvider
        from pcbridge.desktop.backends.rust import RustCaptureProvider

        cfg = SimpleNamespace(desktop=DesktopSpec(enabled=True))
        python = PythonCaptureProvider(cfg, screencast=mock.Mock())
        self.assertFalse(python.available()[0])
        with mock.patch("pcbridge.desktop.capture.capture") as capture:
            with self.assertRaises(DesktopError):
                python.start()
            with self.assertRaises(DesktopError):
                python.capture("all", out_dir=self.root, scale_long_edge=1280, include_pointer=True)
            native = RustCaptureProvider(cfg)
            with mock.patch.object(native, "start", side_effect=DesktopError(code=ErrorCode.BACKEND_UNAVAILABLE,
                    message="Native capture pending", category=ErrorCategory.CAPABILITY,
                    retryable=False, suggested_action="doctor")):
                with self.assertRaises(DesktopError):
                    native.capture("all", out_dir=self.root, scale_long_edge=1280, include_pointer=True)
            capture.assert_not_called()

    def test_emergency_resource_guard_closes_once_and_never_slides_the_grant(self):
        token = self.unlock()
        before = LeaseStore(self.root).read()
        closed = threading.Event()
        cleanup = mock.Mock(side_effect=closed.set)
        watch = ResourceWatch(self.gate, cleanup)
        self.addCleanup(watch.stop)
        watch.track(token)
        time.sleep(0.15)
        self.assertEqual(LeaseStore(self.root).read(), before)
        self.state.activity = ActivityState.UNKNOWN
        self.assertTrue(closed.wait(0.3))
        self.assertEqual(cleanup.call_count, 1)
        self.assertEqual(LeaseStore(self.root).read(), before)
        self.state.activity = ActivityState.KNOWN
        closed.clear()
        watch.track(token)
        self.presented.clear()
        self.assertTrue(closed.wait(0.3))
        self.assertEqual(cleanup.call_count, 2)


if __name__ == "__main__":
    unittest.main()
