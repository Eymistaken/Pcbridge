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
}

_KNOWN_NAMES = (
    set(inputlib.KEY_NAMES.values())
    | set(inputlib.BUTTON_IDENTS.values())
    | {"ABS_X", "ABS_Y", "REL_WHEEL", "REL_HWHEEL"}
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
    """An `InputBackend` with both devices open and a known pointer start."""
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
        backend.ensure(keyboard=True, pointer=True)
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
    """The capabilities both virtual devices are created with."""
    with recording_backend() as (_backend, recorder):
        keyboard = recorder.capabilities["pcbridge-keyboard"]
        pointer = recorder.capabilities["pcbridge-pointer"]
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
