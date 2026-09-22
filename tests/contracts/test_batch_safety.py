#!/usr/bin/env python3
"""Batch safety: every action re-checked, one writer at a time (Task 5.1).

The promises `computer_batch`, `pcb-do` and the single-action tools make once
`SafetyGate.check()` admitted a call:

* grant, revoke, deadline and screen lock are read again before EACH action,
  and a refusal sends nothing more -- not one extra key or click;
* user activity is not asked again (our own uinput events reset IdleMonitor);
* a focus that cannot be read or verified stops the sequence instead of being
  taken as unchanged;
* write sequences are serialized across processes, re-checked after waiting,
  and share one actions-per-second window;
* whatever the sequence still holds is released when it stops early.

No device is opened and no real desktop is read: the gate runs on a temporary
state directory with a fake screen-lock/activity provider, and actions go to a
recording `Ops`. Safe with every live-test flag unset.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.cli import EXIT_DENIED, EXIT_PARTIAL  # noqa: E402
from pcbridge.config import DesktopSpec  # noqa: E402
from pcbridge.desktop import batch as batchlib  # noqa: E402
from pcbridge.desktop import ops as opslib  # noqa: E402
from pcbridge.desktop.errors import ErrorCategory, ErrorCode  # noqa: E402
from pcbridge.desktop.execution import (  # noqa: E402
    ExecutionLock,
    SequenceRefused,
)
from pcbridge.desktop.lease import LeaseStore  # noqa: E402
from pcbridge.desktop.runtime import DesktopRuntime  # noqa: E402
from pcbridge.desktop.safety import (  # noqa: E402
    ActivityObservation,
    ActivityState,
    SafetyGate,
    ScreenLockObservation,
    ScreenLockState,
)

# A second process that holds the execution lock until its stdin closes.
CHILD_HOLDER = """
import sys
sys.path.insert(0, {root!r})
from pcbridge.desktop.execution import ExecutionLock
with ExecutionLock(sys.argv[1]).hold("child"):
    print("held", flush=True)
    sys.stdin.readline()
"""


class FakeDesktopState:
    """Screen lock and activity answers the test controls, with counters."""

    def __init__(self) -> None:
        self.locked = False
        self.lock_queries = 0
        self.activity_queries = 0

    def screen_lock(self) -> ScreenLockObservation:
        self.lock_queries += 1
        state = (
            ScreenLockState.KNOWN_LOCKED if self.locked
            else ScreenLockState.KNOWN_UNLOCKED
        )
        return ScreenLockObservation(state=state, observed_at=time.time())

    def user_activity(self) -> ActivityObservation:
        self.activity_queries += 1
        return ActivityObservation(
            state=ActivityState.KNOWN, idle_ms=600_000, observed_at=time.time()
        )


class ClosedCapture:
    def is_open(self) -> bool:
        return False

    def close(self) -> None:
        return None


class HeldInput:
    """Input provider seen by the runtime: only what refusal cleanup uses."""

    def __init__(self) -> None:
        self.held_now: list[str] = []
        self.release_calls = 0

    def release_all(self) -> list[str]:
        self.release_calls += 1
        freed, self.held_now = self.held_now, []
        return freed

    def close(self) -> None:
        return None


class RecordingOps:
    """`batch.Ops` that writes down what reached the 'device' and nothing else."""

    def __init__(self, *, focus: str = "editor | Belge", on_action=None) -> None:
        self.log: list[str] = []
        self.focus = focus
        self.focus_error: Exception | None = None
        # action kind -> new focus title, or an exception focused() raises from then on
        self.focus_after: dict[str, object] = {}
        self.fail_on: str | None = None
        self.released: list[list[str]] = []
        self._held: list[str] = []
        self._on_action = on_action

    def _do(self, kind: str, detail: str = "") -> str:
        if self.fail_on == kind:
            raise RuntimeError(f"{kind} failed on purpose")
        self.log.append(f"{kind} {detail}".strip())
        after = self.focus_after.get(kind)
        if isinstance(after, Exception):
            self.focus_error = after
        elif isinstance(after, str):
            self.focus = after
        if self._on_action is not None:
            self._on_action(kind, len(self.log))
        return kind

    def key(self, keys):
        return self._do("key", keys)

    def type(self, text, raw):
        return self._do("type", str(len(text)))

    def hold(self, keys):
        self._held.append(keys)
        return self._do("hold", keys)

    def release(self, keys):
        if keys in self._held:
            self._held.remove(keys)
        return self._do("release", keys)

    def move(self, x, y, monitor, shot=None):
        return self._do("move", f"{x},{y}")

    def move_by(self, dx, dy):
        return self._do("move_by", f"{dx},{dy}")

    def click(self, button, count, x, y, monitor, shot=None):
        return self._do("click", f"{x},{y}")

    def mouse_down(self, button, x, y, monitor, shot=None):
        self._held.append(button)
        return self._do("mouse_down", button)

    def mouse_up(self, button):
        if button in self._held:
            self._held.remove(button)
        return self._do("mouse_up", button)

    def drag(self, x, y, to_x, to_y, button, monitor, shot=None):
        return self._do("drag", f"{x},{y}->{to_x},{to_y}")

    def scroll(self, amount, x, y, monitor, horizontal=False, shot=None):
        return self._do("scroll", str(amount))

    def held(self):
        return list(self._held)

    def release_all(self):
        freed, self._held = list(self._held), []
        self.released.append(freed)
        return freed

    def ui_click(self, node_id):
        return self._do("ui_click", node_id)

    def ui_set_text(self, node_id, text):
        return self._do("ui_set_text", node_id)

    def launch(self, app, budget_left=None):
        return self._do("launch", app)

    def focus(self, window, budget_left=None):
        return self._do("focus", window)

    def focused(self):
        if self.focus_error is not None:
            raise self.focus_error
        return self.focus


class Desk:
    """A real `SafetyGate` and runtime on a temporary state directory."""

    def __init__(self, root: Path, *, lock: bool = True, wait: float = 0.3,
                 rate_limit: int = 0, **desktop) -> None:
        self.root = root
        self.state = FakeDesktopState()
        self.cfg = SimpleNamespace(
            state_dir=root,
            audit_log=root / "audit.log",
            shot_search_dirs=[root / "shots"],
            desktop=DesktopSpec(enabled=True, **desktop),
        )
        self.gate = SafetyGate(self.cfg, state_provider=self.state)
        self.input = HeldInput()
        self.runtime = DesktopRuntime(
            capture_provider=ClosedCapture(),
            input_provider=self.input,
            accessibility_provider=object(),
            gate=self.gate,
            desktop_state_provider=self.state,
            execution_lock=ExecutionLock(root) if lock else None,
            rate_limit=rate_limit,
            execution_wait_seconds=wait,
        )

    def grant(self, minutes: int = 5) -> None:
        self.gate.unlock(minutes)

    def admit(self, tool: str = "computer_batch", *, force: bool = True) -> None:
        decision = self.gate.check(tool, write=True, force=force)
        assert decision.allowed, decision.reason

    def run(self, plan, ops, **kwargs) -> batchlib.Result:
        with self.runtime.write_sequence("computer_batch") as guard:
            return batchlib.run(
                plan, ops, budget=1e6, before_action=guard,
                sleep=lambda seconds: None, **kwargs,
            )


def plan(*items: dict) -> list[batchlib.Action]:
    return batchlib.parse(json.dumps(list(items)))


class TempDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class PerActionRecheckTests(TempDirTest):
    def test_revoke_mid_sequence_sends_nothing_more(self) -> None:
        desk = Desk(self.root)
        desk.grant()
        desk.admit()
        # `desktop_lock` from another process lands while the second key is sent.
        ops = RecordingOps(
            on_action=lambda kind, n: n == 2 and LeaseStore(self.root).revoke()
        )
        result = desk.run(plan(
            {"a": "key", "keys": "a"},
            {"a": "key", "keys": "b"},
            {"a": "click", "x": 5, "y": 5},
            {"a": "key", "keys": "c"},
            {"a": "type", "text": "x"},
        ), ops, check_focus=False)

        self.assertEqual(ops.log, ["key a", "key b"])
        self.assertEqual(result.stopped, "safety")
        self.assertEqual(result.error.code, ErrorCode.REVOKED)
        self.assertEqual(result.error.category, ErrorCategory.SAFETY)
        self.assertFalse(result.error.retryable)
        self.assertEqual([a.a for a in result.remaining], ["click", "key", "type"])

    def test_a_new_grant_mid_sequence_ends_the_old_one(self) -> None:
        desk = Desk(self.root)
        desk.grant()
        desk.admit()
        ops = RecordingOps(on_action=lambda kind, n: n == 1 and desk.gate.unlock(5))
        result = desk.run(plan(
            {"a": "key", "keys": "a"}, {"a": "key", "keys": "b"},
        ), ops, check_focus=False)

        self.assertEqual(ops.log, ["key a"])
        self.assertEqual(result.error.code, ErrorCode.REVOKED)
        # The user did not stop anything; a fresh call may carry on.
        self.assertTrue(result.error.retryable)

    def test_deadline_mid_sequence_stops_with_grant_expired(self) -> None:
        desk = Desk(self.root)
        desk.grant()
        desk.admit()

        def expire(kind: str, n: int) -> None:
            if n == 1:
                store = LeaseStore(self.root)
                data = store.read()
                data["until"] = time.time() - 1
                store.replace(data)

        ops = RecordingOps(on_action=expire)
        result = desk.run(plan(
            {"a": "key", "keys": "a"},
            {"a": "key", "keys": "b"},
            {"a": "key", "keys": "c"},
        ), ops, check_focus=False)

        self.assertEqual(ops.log, ["key a"])
        self.assertEqual(result.error.code, ErrorCode.GRANT_EXPIRED)

    def test_screen_lock_mid_sequence_stops(self) -> None:
        desk = Desk(self.root)
        desk.grant()
        desk.admit()
        ops = RecordingOps(
            on_action=lambda kind, n: n == 1 and setattr(desk.state, "locked", True)
        )
        result = desk.run(plan(
            {"a": "ui_click", "id": "a"}, {"a": "ui_click", "id": "b"},
        ), ops, check_focus=False)

        self.assertEqual(ops.log, ["ui_click a"])
        self.assertEqual(result.error.code, ErrorCode.SCREEN_LOCKED)

    def test_activity_is_asked_once_at_admission_and_never_again(self) -> None:
        desk = Desk(self.root)
        desk.grant()
        desk.admit(force=False)
        self.assertEqual(desk.state.activity_queries, 1)

        result = desk.run(plan(*[{"a": "key", "keys": k} for k in "abcdef"]),
                          RecordingOps(), check_focus=False)

        self.assertEqual(result.done, 6)
        self.assertEqual(desk.state.activity_queries, 1)
        # check() + the re-check after the lock + one per action
        self.assertEqual(desk.state.lock_queries, 1 + 1 + 6)

    def test_a_wait_is_not_checked(self) -> None:
        desk = Desk(self.root)
        desk.grant()
        desk.admit()
        desk.run(plan(
            {"a": "key", "keys": "a"}, {"a": "wait", "ms": 0}, {"a": "key", "keys": "b"},
        ), RecordingOps(), check_focus=False)
        self.assertEqual(desk.state.lock_queries, 1 + 1 + 2)

    def test_a_refusal_releases_what_the_sequence_holds(self) -> None:
        desk = Desk(self.root)
        desk.grant()
        desk.admit()
        ops = RecordingOps(
            on_action=lambda kind, n: n == 2 and LeaseStore(self.root).revoke()
        )
        result = desk.run(plan(
            {"a": "hold", "keys": "shift"},
            {"a": "mouse_down"},
            {"a": "key", "keys": "a"},
        ), ops, check_focus=False)

        self.assertEqual(result.stopped, "safety")
        self.assertEqual(ops.released, [["shift", "left"]])
        self.assertEqual(result.held, [])

    def test_a_refused_admission_sends_nothing_and_lets_held_input_go(self) -> None:
        desk = Desk(self.root)
        desk.grant()
        desk.admit()
        desk.input.held_now = ["ctrl"]
        LeaseStore(self.root).revoke()
        ran = []
        with self.assertRaises(SequenceRefused) as caught:
            with desk.runtime.write_sequence("computer_batch"):
                ran.append(True)

        self.assertEqual(ran, [])
        self.assertEqual(caught.exception.code, ErrorCode.REVOKED)
        self.assertEqual(desk.input.release_calls, 1)
        self.assertEqual(desk.input.held_now, [])

    def test_an_unlocked_runtime_still_rechecks_every_action(self) -> None:
        desk = Desk(self.root, lock=False)
        desk.grant()
        desk.admit()
        ops = RecordingOps(
            on_action=lambda kind, n: n == 1 and LeaseStore(self.root).revoke()
        )
        result = desk.run(plan(
            {"a": "key", "keys": "a"}, {"a": "key", "keys": "b"},
        ), ops, check_focus=False)
        self.assertEqual(ops.log, ["key a"])
        self.assertEqual(result.stopped, "safety")


class FocusVerificationTests(unittest.TestCase):
    def run_plan(self, actions, ops, **kwargs) -> batchlib.Result:
        return batchlib.run(actions, ops, budget=1e6, sleep=lambda s: None, **kwargs)

    def test_unreadable_focus_refuses_a_plan_with_clicks_before_anything_runs(self) -> None:
        ops = RecordingOps()
        ops.focus_error = RuntimeError("AT-SPI yok")
        result = self.run_plan(plan(
            {"a": "key", "keys": "a"},
            {"a": "click", "x": 5, "y": 5},
            {"a": "key", "keys": "b"},
        ), ops)

        self.assertEqual(ops.log, [])
        self.assertEqual(result.stopped, "focus")
        self.assertEqual(len(result.remaining), 3)
        self.assertIn("NO action", result.detail)

    def test_a_plan_without_clicks_still_runs_when_focus_is_unreadable(self) -> None:
        ops = RecordingOps()
        ops.focus_error = RuntimeError("AT-SPI yok")
        result = self.run_plan(plan(
            {"a": "key", "keys": "a"}, {"a": "ui_click", "id": "x"},
        ), ops)
        self.assertEqual(result.done, 2)
        self.assertEqual(result.stopped, "")

    def test_unreadable_focus_after_a_click_stops_before_the_next_action(self) -> None:
        ops = RecordingOps()
        ops.focus_after["click"] = RuntimeError("AT-SPI dustu")
        result = self.run_plan(plan(
            {"a": "click", "x": 920, "y": 520},
            {"a": "key", "keys": "ctrl+a"},
            {"a": "key", "keys": "Delete"},
        ), ops)

        self.assertEqual(ops.log, ["click 920,520"])
        self.assertEqual(result.stopped, "focus")
        self.assertEqual([a.a for a in result.remaining], ["key", "key"])

    def test_unreadable_focus_after_the_last_click_is_reported_not_stopped(self) -> None:
        ops = RecordingOps()
        ops.focus_after["click"] = RuntimeError("AT-SPI dustu")
        result = self.run_plan(plan(
            {"a": "key", "keys": "a"}, {"a": "click", "x": 5, "y": 5},
        ), ops)
        self.assertEqual(result.done, 2)
        self.assertEqual(result.stopped, "")
        self.assertIn("could not be read", result.detail)

    def test_focus_lost_after_launch_blocks_the_next_click(self) -> None:
        ops = RecordingOps()
        ops.focus_after["launch"] = RuntimeError("okunamadi")
        result = self.run_plan(plan(
            {"a": "launch", "app": "editor"},
            {"a": "click", "x": 5, "y": 5},
            {"a": "key", "keys": "a"},
        ), ops)

        self.assertEqual(ops.log, ["launch editor"])
        self.assertEqual(result.stopped, "focus")
        self.assertEqual([a.a for a in result.remaining], ["click", "key"])

    def test_focus_check_off_runs_everything(self) -> None:
        ops = RecordingOps()
        ops.focus_error = RuntimeError("AT-SPI yok")
        result = self.run_plan(plan(
            {"a": "click", "x": 5, "y": 5}, {"a": "key", "keys": "a"},
        ), ops, check_focus=False)
        self.assertEqual(result.done, 2)


class ClickInPlaceAndHoldTests(unittest.TestCase):
    """Step 8.2/8.3: clicks without coordinates, and how long a press lasts."""

    def test_a_click_without_coordinates_is_valid_and_goes_in_place(self) -> None:
        backend = mock.Mock()
        device = opslib.DeviceOps(backend, mock.Mock(), SimpleNamespace(
            shot_search_dirs=[], desktop=SimpleNamespace(
                agent_shot_max_age_seconds=60, ambiguous_coord_guard=True,
            ),
        ), mock.Mock())
        note = device.click("left", 1, None, None, None, None)
        backend.move.assert_not_called()
        backend.click.assert_called_once_with("left", 1)
        self.assertIn("where the pointer is", note)

        backend.reset_mock()
        device.click("right", 1, None, None, None, None, hold_ms=120)
        backend.click.assert_called_once_with("right", 1, hold_ms=120)

    def test_hold_ms_reaches_ops_only_when_given(self) -> None:
        seen: list[dict] = []

        class Ops(RecordingOps):
            def click(self, button, count, x, y, monitor, shot=None, **extra):
                seen.append(extra)
                return super().click(button, count, x, y, monitor, shot)

        batchlib.run(plan(
            {"a": "click"}, {"a": "wait", "ms": 1},
            {"a": "right_click", "hold_ms": 90},
        ), Ops(), budget=1e6, check_focus=False, sleep=lambda s: None)
        self.assertEqual(seen, [{}, {"hold_ms": 90}])

    def test_bad_click_arguments_refuse_the_whole_list(self) -> None:
        for bad in (
            {"a": "click", "x": 5},
            {"a": "click", "y": 5},
            {"a": "click", "shot": "m2-a1b2c3"},
            {"a": "right_click", "monitor": 2},
            {"a": "scroll", "x": 3},
            {"a": "double_click", "hold_ms": 151},
            {"a": "click", "hold_ms": 1001},
            {"a": "click", "hold_ms": -1},
        ):
            with self.subTest(action=bad), self.assertRaises(batchlib.BatchError):
                plan({"a": "key", "keys": "a"}, bad)

    def test_a_custom_hold_is_in_the_estimate(self) -> None:
        base = batchlib.estimate(plan({"a": "click"}))
        longer = batchlib.estimate(plan({"a": "click", "hold_ms": 560}))
        self.assertAlmostEqual(longer - base, 0.5)
        self.assertIn(
            "held 560 ms",
            plan({"a": "click", "hold_ms": 560})[0].describe(),
        )


class HeldInputCleanupTests(unittest.TestCase):
    """Every early stop lets go; a finished sequence keeps a deliberate hold."""

    def test_budget_stop_releases(self) -> None:
        # The plan fits its estimate, so it starts; the wait then takes far
        # longer than planned and the budget runs out mid-sequence.
        now = [0.0]

        def slow_sleep(seconds: float) -> None:
            now[0] += seconds * 10

        ops = RecordingOps()
        result = batchlib.run(plan(
            {"a": "hold", "keys": "shift"}, {"a": "wait", "ms": 500},
            {"a": "key", "keys": "a"},
        ), ops, budget=3.0, check_focus=False, clock=lambda: now[0],
            sleep=slow_sleep)
        self.assertIsNone(result.plan_seconds)
        self.assertEqual(result.stopped, "budget")
        self.assertEqual(ops.released, [["shift"]])
        self.assertEqual(result.held, [])

    def test_plan_over_budget_sends_nothing_and_keeps_holds(self) -> None:
        ops = RecordingOps()
        result = batchlib.run(plan(
            {"a": "hold", "keys": "shift"}, {"a": "wait", "ms": 5000},
        ), ops, budget=3.0, check_focus=False, sleep=lambda s: None)
        self.assertEqual(result.stopped, "budget")
        self.assertGreater(result.plan_seconds, 3.0)
        self.assertEqual(ops.log, [])
        self.assertEqual(ops.released, [])
        self.assertEqual(len(result.remaining), 2)

    def test_focus_stop_releases(self) -> None:
        ops = RecordingOps()
        ops.focus_after["click"] = "masaustu | Desktop Icons 2"
        result = batchlib.run(plan(
            {"a": "hold", "keys": "shift"},
            {"a": "click", "x": 5, "y": 5},
            {"a": "key", "keys": "a"},
        ), ops, budget=1e6, sleep=lambda s: None)
        self.assertEqual(result.stopped, "focus")
        self.assertEqual(ops.released, [["shift"]])

    def test_repeat_stop_releases(self) -> None:
        ops = RecordingOps()
        result = batchlib.run(plan(
            {"a": "hold", "keys": "ctrl"},
            {"a": "ui_click", "id": "x"},
            {"a": "ui_click", "id": "x"},
            {"a": "ui_click", "id": "x"},
        ), ops, budget=1e6, check_focus=False, sleep=lambda s: None)
        self.assertEqual(result.stopped, "repeat")
        self.assertEqual(ops.released, [["ctrl"]])

    def test_error_stop_releases(self) -> None:
        ops = RecordingOps()
        ops.fail_on = "ui_click"
        result = batchlib.run(plan(
            {"a": "mouse_down"}, {"a": "ui_click", "id": "x"},
        ), ops, budget=1e6, check_focus=False, sleep=lambda s: None)
        self.assertEqual(result.stopped, "error")
        self.assertEqual(ops.released, [["left"]])

    def test_a_finished_sequence_keeps_a_deliberate_hold(self) -> None:
        ops = RecordingOps()
        result = batchlib.run(plan(
            {"a": "hold", "keys": "shift"}, {"a": "key", "keys": "a"},
        ), ops, budget=1e6, check_focus=False, sleep=lambda s: None)
        self.assertEqual(result.stopped, "")
        self.assertEqual(result.held, ["shift"])
        self.assertEqual(ops.released, [])


class ExecutionLockTests(TempDirTest):
    def test_a_second_sequence_is_busy_and_names_the_holder(self) -> None:
        with ExecutionLock(self.root).hold("computer_batch"):
            with self.assertRaises(SequenceRefused) as caught:
                with ExecutionLock(self.root).hold("pcb_do", timeout=0.2):
                    self.fail("two sequences held the lock at once")
        error = caught.exception
        self.assertEqual(error.code, ErrorCode.BUSY)
        self.assertTrue(error.retryable)
        self.assertIn("computer_batch", error.message)
        self.assertIn(f"pid {os.getpid()}", error.message)

    def test_the_lock_is_free_as_soon_as_the_holder_leaves(self) -> None:
        with ExecutionLock(self.root).hold("first"):
            pass
        with ExecutionLock(self.root).hold("second", timeout=0.0) as slot:
            self.assertLess(slot.waited, 0.05)

    def test_another_process_holding_the_lock_makes_this_one_busy(self) -> None:
        child = subprocess.Popen(
            [sys.executable, "-c", CHILD_HOLDER.format(root=str(ROOT)), str(self.root)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(child.stdout.readline().strip(), "held")
            with self.assertRaises(SequenceRefused) as caught:
                with ExecutionLock(self.root).hold("computer_batch", timeout=0.3):
                    self.fail("the lock is not shared across processes")
            self.assertIn("child", caught.exception.message)
        finally:
            child.stdin.write("\n")
            child.stdin.close()
            child.wait(timeout=10)
            child.stdout.close()
        with ExecutionLock(self.root).hold("computer_batch", timeout=2.0):
            pass

    def test_a_killed_holder_leaves_no_stale_lock(self) -> None:
        child = subprocess.Popen(
            [sys.executable, "-c", CHILD_HOLDER.format(root=str(ROOT)), str(self.root)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(child.stdout.readline().strip(), "held")
            child.kill()
            child.wait(timeout=10)
        finally:
            child.stdin.close()
            child.stdout.close()
        with ExecutionLock(self.root).hold("computer_batch", timeout=0.0):
            pass

    def test_a_process_started_while_locked_does_not_keep_the_lock(self) -> None:
        with ExecutionLock(self.root).hold("computer_task"):
            job = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"], close_fds=False
            )
        try:
            with ExecutionLock(self.root).hold("pcb_do", timeout=0.0):
                pass
        finally:
            job.kill()
            job.wait(timeout=10)

    def test_a_waiting_sequence_rechecks_the_grant_once_it_has_the_lock(self) -> None:
        desk = Desk(self.root, wait=5.0)
        desk.grant()
        desk.admit()
        held = threading.Event()
        release = threading.Event()

        def holder() -> None:
            with ExecutionLock(self.root).hold("pcb_do"):
                held.set()
                release.wait(5)

        def revoke_then_release() -> None:
            time.sleep(0.2)
            LeaseStore(self.root).revoke()
            release.set()

        holding = threading.Thread(target=holder)
        holding.start()
        self.assertTrue(held.wait(5))
        threading.Thread(target=revoke_then_release).start()

        ops = RecordingOps()
        try:
            with self.assertRaises(SequenceRefused) as caught:
                desk.run(plan({"a": "key", "keys": "a"}), ops, check_focus=False)
        finally:
            release.set()
            holding.join(5)

        self.assertEqual(caught.exception.code, ErrorCode.REVOKED)
        self.assertEqual(ops.log, [])


class SharedRateWindowTests(TempDirTest):
    def fake_time(self):
        now = [100.0]
        slept: list[float] = []

        def clock() -> float:
            return now[0]

        def sleep(seconds: float) -> None:
            slept.append(seconds)
            now[0] += seconds

        return now, slept, clock, sleep

    def test_a_sequence_right_after_another_waits_for_the_window(self) -> None:
        now, slept, clock, sleep = self.fake_time()
        with ExecutionLock(self.root, clock=clock, sleep=sleep).hold("computer_batch") as slot:
            slot.pace(2)
            now[0] += 0.1
            slot.pace(2)
        self.assertEqual(slept, [])

        now[0] += 0.1
        # A different lock object stands in for the next process.
        with ExecutionLock(self.root, clock=clock, sleep=sleep).hold("pcb_do") as slot:
            waited = slot.pace(2)
        self.assertAlmostEqual(waited, 0.8, places=6)
        self.assertEqual(len(slept), 1)

    def test_actions_inside_the_limit_do_not_wait(self) -> None:
        now, slept, clock, sleep = self.fake_time()
        with ExecutionLock(self.root, clock=clock, sleep=sleep).hold("computer_batch") as slot:
            for _ in range(12):
                slot.pace(10)
                now[0] += 0.11
        self.assertEqual(slept, [])

    def test_a_wall_clock_step_back_does_not_freeze_the_window(self) -> None:
        now, slept, clock, sleep = self.fake_time()
        with ExecutionLock(self.root, clock=clock, sleep=sleep).hold("a") as slot:
            slot.pace(1)
        now[0] = 50.0
        with ExecutionLock(self.root, clock=clock, sleep=sleep).hold("b") as slot:
            self.assertEqual(slot.pace(1), 0.0)
        self.assertEqual(slept, [])

    def test_the_batch_gap_between_actions_is_kept(self) -> None:
        slept: list[float] = []
        batchlib.run(plan(
            {"a": "key", "keys": "a"}, {"a": "key", "keys": "b"}, {"a": "key", "keys": "c"},
        ), RecordingOps(), budget=1e6, min_gap=0.1, check_focus=False, sleep=slept.append)
        self.assertEqual(slept, [0.1, 0.1, 0.1])


class ImportBoundaryTests(unittest.TestCase):
    def test_batch_and_execution_import_no_mcp_and_no_device_library(self) -> None:
        code = (
            f"import sys; sys.path.insert(0, {str(ROOT)!r})\n"
            "import pcbridge.desktop.batch, pcbridge.desktop.execution\n"
            "roots = {name.split('.')[0] for name in sys.modules}\n"
            "print(','.join(sorted(roots & {'fastmcp', 'mcp', 'evdev', 'gi', "
            "'pydantic', 'starlette'})))\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True,
            timeout=60, check=True,
        ).stdout.strip()
        self.assertEqual(out, "")


class RevokingTree:
    """Accessibility provider whose first click is followed by a `desktop_lock`."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.clicks: list[str] = []

    def click(self, node_id: str, action: str = "click") -> dict:
        self.clicks.append(node_id)
        if len(self.clicks) == 1:
            LeaseStore(self.root).revoke()
        return {"role": "button", "name": node_id}


class PcbDoWiringTests(TempDirTest):
    def setUp(self) -> None:
        super().setUp()
        from pcbridge.cli import do as dolib

        self.dolib = dolib
        self.desk = Desk(self.root, wait=0.2, batch_check_focus=False)
        self.desk.grant()
        self.tree = RevokingTree(self.root)
        self.desk.runtime.accessibility_provider = self.tree
        self.args = SimpleNamespace(
            max_shot_age=0, force=True, expect_focus="", no_check_focus=True, json=True
        )
        self.plan = plan({"a": "ui_click", "id": "a"}, {"a": "ui_click", "id": "b"})

    def test_pcb_do_stops_at_a_revoke_and_reports_it(self) -> None:
        out = io.StringIO()
        with (
            mock.patch("pcbridge.desktop.apps.extension_focus_available", return_value=False),
            redirect_stdout(out),
        ):
            code = self.dolib._run_plan(self.desk.cfg, self.args, self.plan, self.desk.runtime)

        data = json.loads(out.getvalue())
        self.assertEqual(code, EXIT_PARTIAL)
        self.assertEqual(self.tree.clicks, ["a"])
        self.assertEqual(data["stopped"], "safety")
        self.assertEqual(data["error_code"], "REVOKED")
        self.assertEqual(data["done"], 1)

    def test_pcb_do_answers_busy_and_sends_nothing(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch("pcbridge.desktop.apps.extension_focus_available", return_value=False),
            ExecutionLock(self.root).hold("computer_batch"),
            redirect_stdout(out),
            redirect_stderr(err),
            self.assertRaises(SystemExit) as caught,
        ):
            self.dolib._run_plan(self.desk.cfg, self.args, self.plan, self.desk.runtime)

        self.assertEqual(caught.exception.code, EXIT_DENIED)
        self.assertEqual(self.tree.clicks, [])
        self.assertIn("computer_batch", out.getvalue() + err.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
