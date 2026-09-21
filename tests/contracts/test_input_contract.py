#!/usr/bin/env python3
"""Golden uinput events of the Python input backend (Task 5.1).

The Rust keyboard and pointer (Tasks 5.2 and 5.3) must write the same events
for the same calls. This file pins them down from the working Python backend
before anything moves: key combos, a combo sharing a held modifier,
hold/release, `release_all` order, pointer paths (smooth, teleport, clamped
first move), clicks, scroll direction, drag, raw typing and the hold timer's
auto-release -- plus the capabilities both virtual devices advertise.

Nothing opens /dev/uinput. `UInput` is replaced by a recorder, availability is
forced, `time.sleep` is recorded instead of slept, and the hold timer is fired
by hand. Safe with every live-test flag unset.

The fixture is data for another implementation, so nothing here regenerates
it from the code under test. When the Python behavior changes on purpose,
update the fixture in the same commit and say why.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.desktop import input as inputlib  # noqa: E402

try:
    from evdev import ecodes
except ImportError:  # pragma: no cover - requirements.txt installs evdev
    ecodes = None

FIXTURE = ROOT / "tests" / "fixtures" / "native" / "input_events.json"
CANVAS = (3840, 1080)

REQUIRED_CASES = {
    "key_combo",
    "key_with_held_modifier",
    "hold_release",
    "release_all_order",
    "pointer_path",
    "pointer_teleport",
    "pointer_first_move_clamped",
    "double_click",
    "scroll_up",
    "scroll_left",
    "drag",
    "hold_auto_release",
    "type_raw",
    "move_by_small",
    "move_by_chunked",
    "move_by_then_absolute",
    "click_hold_custom",
    "click_in_place_after_move_by",
}

_KNOWN_NAMES = (
    set(inputlib.KEY_NAMES.values())
    | set(inputlib.BUTTON_IDENTS.values())
    | {"ABS_X", "ABS_Y", "REL_WHEEL", "REL_HWHEEL", "REL_X", "REL_Y"}
)


def code_name(event_type: int, code: int) -> str:
    """Stable name for an event code; evdev lists aliases (BTN_LEFT/BTN_MOUSE)."""
    names = ecodes.bytype[event_type][code]
    if isinstance(names, str):
        return names
    preferred = sorted(name for name in names if name in _KNOWN_NAMES)
    return (preferred or sorted(names))[0]


class FakeTimer:
    def __init__(self, interval: float, function) -> None:
        self.interval = float(interval)
        self.function = function
        self.daemon = False
        self.started = False
        self.canceled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.canceled = True


class RecordingDevice:
    def __init__(self, recorder: Recorder, name: str) -> None:
        self._recorder = recorder
        self.name = name

    def write(self, event_type: int, code: int, value: int) -> None:
        self._recorder.events.append(
            [ecodes.EV[event_type], code_name(event_type, code), int(value)]
        )

    def syn(self) -> None:
        self._recorder.events.append(["SYN"])

    def close(self) -> None:
        self._recorder.events.append(["close", self.name])


class Recorder:
    """Stands in for `UInput`, `time.sleep` and `threading.Timer` at once."""

    def __init__(self) -> None:
        self.events: list[list[Any]] = []
        self.capabilities: dict[str, dict] = {}
        self.timers: list[FakeTimer] = []

    def uinput(self, capabilities, name: str = "", version: int = 1) -> RecordingDevice:
        self.capabilities[name] = capabilities
        return RecordingDevice(self, name)

    def timer(self, interval: float, function) -> FakeTimer:
        timer = FakeTimer(interval, function)
        self.timers.append(timer)
        return timer

    def sleep(self, seconds: float) -> None:
        self.events.append(["sleep", round(float(seconds), 6)])


@contextmanager
def recording_backend(
    *, start: list[int] | None = None, hold_max_seconds: float = 120.0
) -> Iterator[tuple[inputlib.InputBackend, Recorder]]:
    """An `InputBackend` with all three devices open and a known pointer start."""
    recorder = Recorder()
    with (
        mock.patch.object(inputlib, "UInput", recorder.uinput),
        mock.patch.object(inputlib.InputBackend, "available", return_value=(True, "")),
        mock.patch.object(inputlib.monitorslib, "canvas_size", return_value=CANVAS),
        mock.patch.object(inputlib.time, "sleep", recorder.sleep),
        mock.patch.object(inputlib.threading, "Timer", recorder.timer),
    ):
        backend = inputlib.InputBackend(
            pointer_speed=5000, pointer_max_ms=500, hold_max_seconds=hold_max_seconds
        )
        backend.ensure(keyboard=True, pointer=True, relative=True)
        backend._pos = tuple(start) if start is not None else None
        recorder.events.clear()
        yield backend, recorder


def replay(case: dict[str, Any]) -> dict[str, Any]:
    """Run a fixture case's calls and return what the devices saw."""
    with recording_backend(
        start=case.get("start"), hold_max_seconds=case.get("hold_max_seconds", 120.0)
    ) as (backend, recorder):
        outputs: list[list[Any]] = []
        for call in case["calls"]:
            op = call["op"]
            if op == "fire_hold_timer":
                live = [t for t in recorder.timers if t.started and not t.canceled]
                live[-1].function()
                continue
            value = getattr(backend, op)(*call.get("args", []), **call.get("kwargs", {}))
            if op in ("held", "release_all", "take_auto_released"):
                outputs.append([op, value])
        return {
            "events": recorder.events,
            "hold_timers": [t.interval for t in recorder.timers if t.started],
            "outputs": outputs,
            "position": list(backend.position) if backend.position else None,
        }


def describe_devices() -> dict[str, Any]:
    """The capabilities all three virtual devices are created with."""
    with recording_backend() as (_backend, recorder):
        keyboard = recorder.capabilities["pcbridge-keyboard"]
        pointer = recorder.capabilities["pcbridge-pointer"]
        relative = recorder.capabilities["pcbridge-pointer-rel"]
    return {
        "keyboard": {
            "name": "pcbridge-keyboard",
            "event_types": sorted(ecodes.EV[t] for t in keyboard),
            "keys": sorted(code_name(ecodes.EV_KEY, c) for c in keyboard[ecodes.EV_KEY]),
        },
        "pointer": {
            "name": "pcbridge-pointer",
            "event_types": sorted(ecodes.EV[t] for t in pointer),
            "keys": [code_name(ecodes.EV_KEY, c) for c in pointer[ecodes.EV_KEY]],
            "absolute": {
                code_name(ecodes.EV_ABS, code): {"min": info.min, "max": info.max}
                for code, info in pointer[ecodes.EV_ABS]
            },
            "relative": [code_name(ecodes.EV_REL, c) for c in pointer[ecodes.EV_REL]],
        },
        "pointer_relative": {
            "name": "pcbridge-pointer-rel",
            "event_types": sorted(ecodes.EV[t] for t in relative),
            "keys": [code_name(ecodes.EV_KEY, c) for c in relative[ecodes.EV_KEY]],
            "relative": [code_name(ecodes.EV_REL, c) for c in relative[ecodes.EV_REL]],
        },
    }


def _still_pressed(events: list[list[Any]]) -> set[str]:
    pressed: set[str] = set()
    for event in events:
        if len(event) == 3 and event[0] == "EV_KEY":
            (pressed.add if event[2] else pressed.discard)(event[1])
    return pressed


class InputEventFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if ecodes is None:
            raise unittest.SkipTest("evdev is not installed")
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.cases = {case["name"]: case for case in cls.fixture["cases"]}

    def test_fixture_describes_the_canvas_it_was_recorded_on(self) -> None:
        self.assertEqual(self.fixture["schema_version"], 1)
        self.assertEqual(self.fixture["canvas"], list(CANVAS))

    def test_the_fixture_covers_the_task_contract(self) -> None:
        self.assertLessEqual(REQUIRED_CASES, set(self.cases))

    def test_devices_match_the_golden_capabilities(self) -> None:
        self.assertEqual(describe_devices(), self.fixture["devices"])

    def test_pointer_is_an_absolute_mouse_not_a_touchscreen(self) -> None:
        # BTN_TOUCH / BTN_TOOL_PEN make the compositor bind the device to one
        # output, and the second monitor becomes unreachable (measured).
        pointer = self.fixture["devices"]["pointer"]
        self.assertNotIn("BTN_TOUCH", pointer["keys"])
        self.assertNotIn("BTN_TOOL_PEN", pointer["keys"])
        self.assertIn("BTN_LEFT", pointer["keys"])
        self.assertEqual(
            pointer["absolute"],
            {"ABS_X": {"min": 0, "max": CANVAS[0] - 1},
             "ABS_Y": {"min": 0, "max": CANVAS[1] - 1}},
        )

    def test_relative_axes_did_not_leak_onto_the_absolute_device(self) -> None:
        # The BTN_TOUCH lesson in machine-checkable form: the relative pointer
        # is a SEPARATE device (Adim 7), and the absolute one must keep exactly
        # the wheel axes it was measured with.
        self.assertEqual(
            self.fixture["devices"]["pointer"]["relative"],
            ["REL_WHEEL", "REL_HWHEEL"],
        )

    def test_the_relative_device_declares_buttons_it_never_emits(self) -> None:
        # MEASURED 2026-09-20: a REL_X/REL_Y device with no EV_KEY gets no
        # ID_INPUT_MOUSE from udev and moves the cursor zero pixels, while its
        # button-carrying twin moved 23. The buttons are declared for that
        # classification only -- every press still goes out of the absolute
        # device, so a relative case may never contain an EV_KEY event.
        relative = self.fixture["devices"]["pointer_relative"]
        self.assertEqual(relative["relative"], ["REL_X", "REL_Y"])
        self.assertIn("BTN_LEFT", relative["keys"])
        self.assertNotIn("EV_ABS", relative["event_types"])
        for name in ("move_by_small", "move_by_chunked"):
            events = self.cases[name]["expect"]["events"]
            self.assertEqual([e for e in events if e[0] == "EV_KEY"], [])

    def test_a_relative_move_forgets_the_pointer_position(self) -> None:
        # An absolute position cached across a relative nudge is a lie.
        for name in ("move_by_small", "move_by_chunked"):
            self.assertIsNone(self.cases[name]["expect"]["position"])

    def test_an_absolute_move_after_a_nudge_steps_aside_first(self) -> None:
        # MEASURED 2026-09-20: after a relative nudge carried the cursor from
        # 960 to 1052, sending ABS_X=960 again did nothing at all -- the
        # kernel treats a repeated absolute value as no change, so the pointer
        # stayed where the nudge left it. Going to 961 worked, and 960 worked
        # after that. Without this the tool would report a move that never
        # happened, which is exactly the silent-wrong-place class of bug the
        # coordinate work closed.
        events = self.cases["move_by_then_absolute"]["expect"]["events"]
        absolute = [event for event in events if event[0] == "EV_ABS"]
        self.assertEqual(
            absolute,
            [["EV_ABS", "ABS_X", 99], ["EV_ABS", "ABS_Y", 100],
             ["EV_ABS", "ABS_X", 100], ["EV_ABS", "ABS_Y", 100]],
        )

    def test_a_plain_absolute_move_does_not_step_aside(self) -> None:
        # The resync is only paid after a relative nudge; the ordinary path
        # must not gain an extra event.
        events = self.cases["pointer_teleport"]["expect"]["events"]
        self.assertEqual(len([e for e in events if e[0] == "SYN"]), 1)

    def test_move_by_is_capped_in_delta_and_in_chunks(self) -> None:
        with recording_backend() as (backend, recorder):
            backend.move_by(99_999, 0)
        moved = [e for e in recorder.events if e[:2] == ["EV_REL", "REL_X"]]
        self.assertEqual(sum(e[2] for e in moved), inputlib.MOVE_BY_MAX)
        self.assertLessEqual(len(moved), inputlib.MOVE_BY_MAX_CHUNKS)

    def test_the_batch_delta_cap_matches_the_device_one(self) -> None:
        # `batch` deliberately does not import the device module, so it carries
        # its own copy of the limit. This is what keeps the two from drifting.
        from pcbridge.desktop import batch as batchlib

        self.assertEqual(batchlib.MOVE_BY_MAX, inputlib.MOVE_BY_MAX)

    def test_the_batch_click_hold_copies_match_the_device_ones(self) -> None:
        from pcbridge.desktop import batch as batchlib

        self.assertEqual(batchlib.DEFAULT_CLICK_HOLD_MS, inputlib.DEFAULT_CLICK_HOLD_MS)
        self.assertEqual(batchlib.MAX_CLICK_HOLD_MS, inputlib.MAX_CLICK_HOLD_MS)

    def test_every_case_replays_to_its_golden_events(self) -> None:
        for name, case in self.cases.items():
            with self.subTest(case=name):
                self.assertEqual(replay(case), case["expect"])

    def test_completed_gestures_leave_nothing_pressed(self) -> None:
        for name in ("key_combo", "double_click", "drag", "type_raw", "release_all_order"):
            with self.subTest(case=name):
                self.assertEqual(_still_pressed(self.cases[name]["expect"]["events"]), set())

    def test_the_first_move_without_a_known_start_is_one_clamped_jump(self) -> None:
        events = self.cases["pointer_first_move_clamped"]["expect"]["events"]
        self.assertEqual(
            events, [["EV_ABS", "ABS_X", CANVAS[0] - 1], ["EV_ABS", "ABS_Y", 0], ["SYN"]]
        )

    def test_a_click_in_place_after_move_by_sends_no_absolute_event(self) -> None:
        # Step 8.2: the click goes where the pointer is, and the stale-ABS
        # resync is still owed to the next absolute move.
        events = self.cases["click_in_place_after_move_by"]["expect"]["events"]
        first_key = next(i for i, e in enumerate(events) if e[0] == "EV_KEY")
        last_key = max(i for i, e in enumerate(events) if e[0] == "EV_KEY")
        between = events[first_key:last_key + 1]
        self.assertFalse([e for e in between if e[0] == "EV_ABS"])
        after = [e for e in events[last_key:] if e[0] == "EV_ABS"]
        self.assertEqual(after[0], ["EV_ABS", "ABS_X", 99])

    def test_click_hold_defaults_to_60_ms_and_is_bounded(self) -> None:
        self.assertEqual(inputlib.DEFAULT_CLICK_HOLD_MS, 60)
        with recording_backend() as (backend, recorder):
            backend.click("left")
            with self.assertRaises(inputlib.InputError):
                backend.click("left", 1, inputlib.MAX_CLICK_HOLD_MS + 1)
        self.assertIn(["sleep", 0.06], recorder.events)
        self.assertEqual(
            [e for e in recorder.events if e[0] == "EV_KEY"],
            [["EV_KEY", "BTN_LEFT", 1], ["EV_KEY", "BTN_LEFT", 0]],
        )

    def test_scroll_is_capped_at_100_ticks(self) -> None:
        with recording_backend() as (backend, recorder):
            backend.scroll(250)
        wheel = [e for e in recorder.events if e[:2] == ["EV_REL", "REL_WHEEL"]]
        self.assertEqual(len(wheel), 100)

    def test_replay_never_opens_the_device_node(self) -> None:
        real_open = os.open

        def guarded(path, *args, **kwargs):
            if str(path) == inputlib.UINPUT_NODE:
                raise AssertionError("a contract test opened /dev/uinput")
            return real_open(path, *args, **kwargs)

        with mock.patch("os.open", guarded):
            for case in self.cases.values():
                replay(case)


if __name__ == "__main__":
    unittest.main(verbosity=2)
