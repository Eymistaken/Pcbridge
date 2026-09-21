#!/usr/bin/env python3
"""Step 8.2 / 8.3: clicks in place, and how long a press has to last.

Skipped unless `PCBRIDGE_TEST_INPUT=1`. THIS SENDS REAL CLICKS, so it runs
only with nobody touching the keyboard and mouse.

What keeps it contained is the same as `test_input_parity.py`: the report
window covers every monitor, so every click lands on it and is reported
instead of reaching one of the user's windows, and the grant and pointer
state live in a scratch directory.

What it measures:

* 8.3 -- the window samples the button state every 50 ms, the way a game's
  tick polls its input. A click whose press and release both fall between two
  ticks is never seen there, although both events arrived. Clicks go out with
  a 30 ms press (the old fixed value) and a 60 ms press (the new default),
  spaced irregularly so the phase against the tick varies, and the report
  counts how many each tick saw.
* 8.2 -- after a relative nudge the absolute position is unknown. A click
  with no coordinate must still land where the pointer now is, not at the
  stale absolute point, and the next absolute move must still arrive.

Both on the Python backend and the packaged native helper.
`PCBRIDGE_CLICK_REPORT=<path>` writes the numbers as JSON.
"""

from __future__ import annotations

import dataclasses
import json
import os
import random
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop.backends.python import PythonInputProvider  # noqa: E402
from pcbridge.desktop.backends.rust import RustInputProvider  # noqa: E402
from pcbridge.desktop.safety import SafetyGate  # noqa: E402
from tests.live.test_capture_parity import (  # noqa: E402
    PACKAGED,
    release_binary,
    screen_locked,
)
from tests.live.test_input_parity import InputWindow, near  # noqa: E402

LIVE = os.environ.get("PCBRIDGE_TEST_INPUT") == "1"
TICK_MS = 50
CLICKS = 40
REPORT: dict = {}


@unittest.skipUnless(LIVE, "set PCBRIDGE_TEST_INPUT=1 with nobody at the machine: sends real clicks")
class ClickHoldOnTheDesktop(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        binary, why = release_binary()
        if binary is None or binary != PACKAGED:
            raise unittest.SkipTest(why or "the test uses the packaged release helper")
        if screen_locked():
            raise unittest.SkipTest("the screen is locked")
        cls.root = Path(tempfile.mkdtemp(prefix="pcb-click-"))
        base = load_config(str(ROOT / "config.example.toml"))
        cls.cfg = dataclasses.replace(
            base,
            state_dir=cls.root / "state",
            native=dataclasses.replace(base.native, input="rust", binary_path=None),
        )
        os.environ.pop("PCBRIDGE_NATIVE_BIN", None)
        cls.window = InputWindow(cls.root / "window.stderr", tick_ms=TICK_MS)
        ready = cls.window.wait(lambda e: e.get("event") == "ready", 20)
        if ready is None:
            cls.window.close()
            raise unittest.SkipTest("the input window did not appear")
        right = ready[1]["monitors"][-1]
        # The blank lower half of the right-hand window, clear of its field.
        cls.spot = (right[0] + right[2] // 2, right[1] + 750)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        errors = cls.window.errors.read_text(encoding="utf-8", errors="replace").strip()
        if errors:
            print(f"\ninput window stderr:\n{errors[-2000:]}", file=sys.stderr)
        shutil.rmtree(cls.root, ignore_errors=True)
        target = os.environ.get("PCBRIDGE_CLICK_REPORT")
        if target:
            Path(target).write_text(json.dumps(REPORT, indent=2), encoding="utf-8")

    def providers(self):
        gate = SafetyGate(self.cfg)
        gate.unlock(5, reason="live click hold")
        self.addCleanup(gate.lock)
        python = PythonInputProvider(self.cfg)
        native = RustInputProvider(self.cfg, gate=gate)
        self.addCleanup(python.close)
        self.addCleanup(native.close)
        return {"python": python, "native": native}

    def arrive(self, provider, point) -> None:
        mark = self.window.mark()
        provider.move(*point)
        arrived = self.window.wait(
            lambda e: e.get("event") == "motion" and near(e, point), 3.0, mark
        )
        self.window.settle()
        last = self.window.last("motion")
        self.assertTrue(
            arrived is not None or (last is not None and near(last, point)),
            f"the pointer did not reach {point}; last {last}",
        )

    def test_1_a_60_ms_press_is_seen_by_a_50_ms_tick(self) -> None:
        rng = random.Random(8_3)
        for name, provider in self.providers().items():
            self.arrive(provider, self.spot)
            REPORT.setdefault("tick", {})[name] = {}
            for hold in (30, 60):
                mark = self.window.mark()
                for _ in range(CLICKS):
                    provider.click("left", 1, hold_ms=hold)
                    # 150-250 ms apart: past any double-click pairing that
                    # matters here, and a phase against the tick that varies.
                    time.sleep(0.15 + rng.random() * 0.1)
                self.window.settle(quiet=0.3)
                events = [e for _s, e in self.window.since(mark)]
                presses = sum(1 for e in events if e.get("event") == "press")
                releases = sum(1 for e in events if e.get("event") == "release")
                seen = sum(1 for e in events if e.get("event") == "tick_press")
                REPORT["tick"][name][str(hold)] = {
                    "clicks": CLICKS,
                    "press_events": presses,
                    "release_events": releases,
                    "seen_by_tick": seen,
                }
                with self.subTest(backend=name, hold_ms=hold):
                    self.assertEqual(presses, CLICKS, "a press event never arrived")
                    self.assertEqual(releases, CLICKS)
                    if hold >= TICK_MS:
                        self.assertEqual(seen, CLICKS, "the tick missed a long press")

    def test_2_a_click_in_place_after_a_nudge_lands_where_the_pointer_is(self) -> None:
        for name, provider in self.providers().items():
            with self.subTest(backend=name):
                self.arrive(provider, self.spot)
                mark = self.window.mark()
                provider.move_by(300, 0)
                self.window.settle()
                nudged = self.window.last("motion")
                self.assertIsNotNone(nudged)
                self.assertGreater(nudged["x"], self.spot[0] + 20, "the nudge did not move")
                self.assertIsNone(provider.position, "a nudge must forget the position")

                mark = self.window.mark()
                provider.click("left")
                press = self.window.wait(lambda e: e.get("event") == "press", 3.0, mark)
                self.assertIsNotNone(press, "the click in place never arrived")
                self.assertTrue(
                    near(press[1], (nudged["x"], nudged["y"])),
                    f"clicked at {press[1]}, the pointer was at {nudged}",
                )
                self.window.settle()

                # The stale ABS state is still owed: going back to the very
                # point the absolute device last sent must still arrive.
                self.arrive(provider, self.spot)
                REPORT.setdefault("in_place", {})[name] = {
                    "start": list(self.spot),
                    "after_nudge": [nudged["x"], nudged["y"]],
                    "press": [press[1]["x"], press[1]["y"]],
                }


if __name__ == "__main__":
    unittest.main(verbosity=2)
