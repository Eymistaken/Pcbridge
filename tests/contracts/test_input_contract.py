#!/usr/bin/env python3
"""Golden uinput events of the virtual input devices (Task 5.1).

`tests/fixtures/native/input_events.json` was recorded from the Python
evdev backend before input moved to the native helper. The helper replays
every case in Rust (`keyboard_contract.rs`, `pointer_contract.rs`,
`relative_contract.rs`, `pointer_path.rs`); the Python backend itself was
removed after 2.8.

What stays here: the invariants the fixture must keep whoever edits it --
an absolute mouse that is not a touchscreen, a relative device that only
nudges, gestures that leave nothing pressed -- and the copies of the
helper's limits that `batch.py` carries, since it knows no device. Nothing
opens /dev/uinput.

The fixture is data for another implementation, so nothing here regenerates
it. When the behavior changes on purpose, update the fixture in the same
commit and say why.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.desktop import batch as batchlib  # noqa: E402
from pcbridge.desktop import input as inputlib  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "native" / "input_events.json"
CANVAS = (3840, 1080)
CORE_INPUT = ROOT / "rust" / "crates" / "pcbridge-core" / "src" / "input.rs"
POINTER = (
    ROOT / "rust" / "crates" / "pcbridge-native" / "src" / "platform" / "linux" / "input"
    / "pointer.rs"
)

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


def rust_constant(path: Path, pattern: str) -> int:
    """An integer constant read from the helper's source, `_` separators allowed."""
    match = re.search(pattern, path.read_text(encoding="utf-8"))
    if match is None:
        raise AssertionError(f"{pattern!r} not found in {path}")
    return int(match.group(1).replace("_", ""))


def _still_pressed(events: list[list[Any]]) -> set[str]:
    pressed: set[str] = set()
    for event in events:
        if len(event) == 3 and event[0] == "EV_KEY":
            (pressed.add if event[2] else pressed.discard)(event[1])
    return pressed


class InputEventFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.cases = {case["name"]: case for case in cls.fixture["cases"]}

    def test_fixture_describes_the_canvas_it_was_recorded_on(self) -> None:
        self.assertEqual(self.fixture["schema_version"], 1)
        self.assertEqual(self.fixture["canvas"], list(CANVAS))

    def test_the_fixture_covers_the_task_contract(self) -> None:
        self.assertLessEqual(REQUIRED_CASES, set(self.cases))

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


@unittest.skipUnless(CORE_INPUT.exists(), "the Rust sources are not in this tree")
class HelperLimitCopyTests(unittest.TestCase):
    """`batch.py` refuses early with the helper's own limits, never other ones."""

    def test_the_batch_delta_cap_matches_the_helpers(self) -> None:
        self.assertEqual(
            batchlib.MOVE_BY_MAX,
            rust_constant(CORE_INPUT, r"pub const MOVE_BY_MAX: i32 = ([0-9_]+);"),
        )

    def test_the_click_hold_copies_match_the_helpers(self) -> None:
        default = rust_constant(
            POINTER, r"pub const DEFAULT_CLICK_HOLD: Duration = Duration::from_millis\(([0-9_]+)\);"
        )
        ceiling = rust_constant(POINTER, r"pub const MAX_CLICK_HOLD_MS: u64 = ([0-9_]+);")
        for copy in (batchlib, inputlib):
            with self.subTest(module=copy.__name__):
                self.assertEqual(copy.DEFAULT_CLICK_HOLD_MS, default)
                self.assertEqual(copy.MAX_CLICK_HOLD_MS, ceiling)

    def test_a_hold_beyond_the_ceiling_is_refused_before_the_helper(self) -> None:
        self.assertEqual(inputlib.click_hold_ms_checked(inputlib.MAX_CLICK_HOLD_MS),
                         inputlib.MAX_CLICK_HOLD_MS)
        with self.assertRaises(inputlib.InputError):
            inputlib.click_hold_ms_checked(inputlib.MAX_CLICK_HOLD_MS + 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
