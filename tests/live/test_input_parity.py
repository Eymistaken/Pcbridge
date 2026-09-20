#!/usr/bin/env python3
"""Task 5.4 / Gate 5: native keyboard, pointer and clipboard on the real desktop.

Skipped unless `PCBRIDGE_TEST_INPUT=1`. THIS SENDS REAL KEYS AND CLICKS, so it
runs only with the user at the machine, hands off the keyboard and mouse.

What keeps it contained:

* `input_window.py` covers every monitor with a window that reports the input
  it receives. A pointer event that lands in the wrong place lands there, not
  on one of the user's windows, and the report shows where.
* Every key waits for the report that the test's text field has focus; a
  field that lost focus fails the test before anything is typed. No combination
  that edits or deletes (ctrl+a with Delete, BackSpace) is ever sent.
* The grant lives in a scratch state directory; the user's own grant, pointer
  state and audit log are not touched.
* The user's clipboard is read before typing and compared afterwards, which is
  the restore under test. Only its first representation survives, as always.

Evidence for `PLAN.md` Task 5.4 and Gate 5: Turkish text arrives byte for byte,
the clipboard is restored, modifiers reach the application, the pointer lands
within one pixel on both monitors, a click follows a verified move, drag and
scroll arrive, and held input is released by revoke, by expiry and by the hold
timer without another request. `PCBRIDGE_INPUT_REPORT=<path>` writes the
measurements as JSON.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop import clipboard as clipboardlib  # noqa: E402
from pcbridge.desktop.backends.rust import RustInputProvider  # noqa: E402
from pcbridge.desktop.safety import SafetyGate  # noqa: E402
from tests.live.test_capture_parity import (  # noqa: E402
    PACKAGED,
    SYSTEM_PYTHON,
    release_binary,
    screen_locked,
)

LIVE = os.environ.get("PCBRIDGE_TEST_INPUT") == "1"
TURKISH = "Merhaba dünya — ğüşıöç İĞÜŞÖÇ «pcbridge» 1+2=3"
REPORT: dict = {}


class InputWindow:
    """`input_window.py` and the events it reports."""

    def __init__(self, errors: Path, timeout: int = 300) -> None:
        # GTK swallows an exception raised in a signal handler: the event is
        # simply not reported. Keep what it prints, or a broken window looks
        # exactly like input that never arrived (it did, 2026-09-19).
        self.errors = errors
        self._stderr = errors.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [SYSTEM_PYTHON, str(HERE / "input_window.py"), "--timeout", str(timeout)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            bufsize=1,
        )
        self.events: list[tuple[float, dict]] = []
        self.changed = threading.Condition()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            with self.changed:
                self.events.append((time.monotonic(), event))
                self.changed.notify_all()

    def mark(self) -> int:
        with self.changed:
            return len(self.events)

    def since(self, mark: int) -> list[tuple[float, dict]]:
        with self.changed:
            return list(self.events[mark:])

    def wait(self, predicate, timeout: float, since: int = 0) -> tuple[float, dict] | None:
        deadline = time.monotonic() + timeout
        with self.changed:
            while True:
                for stamp, event in self.events[since:]:
                    if predicate(event):
                        return stamp, event
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.changed.wait(remaining)

    def last(self, kind: str) -> dict | None:
        with self.changed:
            for _stamp, event in reversed(self.events):
                if event.get("event") == kind:
                    return event
        return None

    def settle(self, quiet: float = 0.25, timeout: float = 3.0) -> None:
        """Wait until no event has arrived for `quiet` seconds."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.changed:
                newest = self.events[-1][0] if self.events else 0.0
            if time.monotonic() - newest >= quiet:
                return
            time.sleep(0.05)

    def send(self, command: str) -> None:
        if self.process.poll() is None and self.process.stdin is not None:
            self.process.stdin.write(command + "\n")
            self.process.stdin.flush()

    def close(self) -> None:
        self.send("quit")
        try:
            self.process.wait(5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(5)
        for stream in (self.process.stdin, self.process.stdout, self._stderr):
            if stream is not None:
                stream.close()


def described(saved: clipboardlib.Saved | None) -> str:
    """A clipboard in a message: its type and size, never its content.

    Not even a hash: the first run printed the user's clipboard in an
    assertion message (2026-09-19), and it looked like a password.
    """
    return "empty" if saved is None else f"{saved.mime}, {len(saved.data)} bytes"


def restored(before: clipboardlib.Saved | None) -> tuple[bool, str]:
    """Is the saved representation offered again, byte for byte?

    Not "is the first type the same": after a restore wl-copy lists its text
    aliases (UTF8_STRING, STRING, TEXT) before the type it was given.
    """
    if before is None:
        now = clipboardlib.WlClipboard().save()
        return now is None, described(now)
    listing = subprocess.run(["wl-paste", "--list-types"], capture_output=True, timeout=10)
    offered = [line.strip() for line in listing.stdout.decode("utf-8", "replace").splitlines()]
    if before.mime not in offered:
        return False, f"{before.mime} is no longer offered"
    got = subprocess.run(
        ["wl-paste", "--type", before.mime, "--no-newline"], capture_output=True, timeout=10
    ).stdout
    return got == before.data, described(clipboardlib.Saved(before.mime, got))


def near(event: dict, point: tuple[int, int], tolerance: int = 1) -> bool:
    return abs(event["x"] - point[0]) <= tolerance and abs(event["y"] - point[1]) <= tolerance


@unittest.skipUnless(LIVE, "set PCBRIDGE_TEST_INPUT=1 with the user at the machine: sends real input")
class NativeInputOnTheDesktop(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        binary, why = release_binary()
        if binary is None or binary != PACKAGED:
            raise unittest.SkipTest(why or "the test uses the packaged release helper")
        if screen_locked():
            raise unittest.SkipTest("the screen is locked")
        cls.root = Path(tempfile.mkdtemp(prefix="pcb-input-"))
        base = load_config(str(ROOT / "config.example.toml"))
        cls.cfg = dataclasses.replace(
            base,
            state_dir=cls.root / "state",
            native=dataclasses.replace(base.native, input="rust", binary_path=None),
        )
        os.environ.pop("PCBRIDGE_NATIVE_BIN", None)
        cls.window = InputWindow(cls.root / "window.stderr")
        ready = cls.window.wait(lambda e: e.get("event") == "ready", 20)
        if ready is None:
            cls.window.close()
            raise unittest.SkipTest("the input window did not appear")
        cls.monitors = ready[1]["monitors"]
        x, y, width, height = ready[1]["field"]
        cls.field = (x, y, width, height)
        cls.field_center = (x + width // 2, y + height // 2)
        right = cls.monitors[-1]
        cls.blank = (right[0] + right[2] // 2, right[1] + 700)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        errors = cls.window.errors.read_text(encoding="utf-8", errors="replace").strip()
        if errors:
            print(f"\ninput window stderr:\n{errors[-2000:]}", file=sys.stderr)
        REPORT["window_errors"] = bool(errors)
        shutil.rmtree(cls.root, ignore_errors=True)
        target = os.environ.get("PCBRIDGE_INPUT_REPORT")
        if target:
            Path(target).write_text(json.dumps(REPORT, indent=2), encoding="utf-8")

    # ------------------------------------------------------------ helpers
    def provider(self, hold_max_seconds: int | None = None) -> tuple[SafetyGate, RustInputProvider]:
        cfg = self.cfg
        if hold_max_seconds is not None:
            cfg = dataclasses.replace(
                cfg, desktop=dataclasses.replace(cfg.desktop, hold_max_seconds=hold_max_seconds)
            )
        gate = SafetyGate(cfg)
        gate.unlock(5, reason="live input parity")
        provider = RustInputProvider(cfg, gate=gate)
        self.addCleanup(provider.close)
        self.addCleanup(gate.lock)
        return gate, provider

    def require_focus(self) -> None:
        """The last word from the window must be: the field has focus."""
        focus = self.window.last("focus")
        active = self.window.last("active")
        if not (focus and focus["field"] and active and active["active"]
                and active["monitor"] == len(self.monitors) - 1):
            self.fail("the test field does not have focus; no key was sent")

    def focus_field(self, provider: RustInputProvider) -> None:
        """Move, see the pointer arrive in the field, click it, see it focused."""
        mark = self.window.mark()
        before = self.move_and_verify(provider, self.field_center)
        x, y, width, height = self.field
        self.assertTrue(x <= before["x"] < x + width and y <= before["y"] < y + height, before)
        provider.click("left")
        self.window.wait(lambda e: e.get("event") == "release", 3.0, mark)
        self.window.settle()
        self.require_focus()

    def move_and_verify(self, provider: RustInputProvider, point: tuple[int, int]) -> dict:
        already = self.window.last("motion")
        mark = self.window.mark()
        provider.move(*point)
        arrived = self.window.wait(
            lambda e: e.get("event") == "motion" and near(e, point), 3.0, mark
        )
        self.window.settle()
        last = self.window.last("motion")
        # A pointer already resting on the target does not move, so no motion
        # is reported: the last report, with nothing since, is the evidence.
        stayed = (
            arrived is None
            and already is not None
            and near(already, point, 0)
            and not any(e.get("event") == "motion" for _s, e in self.window.since(mark))
        )
        self.assertTrue(arrived is not None or stayed, f"no motion reached {point}; last {last}")
        return last

    # -------------------------------------------------------------- tests
    def test_1_the_pointer_lands_within_a_pixel_on_both_monitors(self) -> None:
        _gate, provider = self.provider()
        left, right = self.monitors[0], self.monitors[-1]
        targets = [
            (left[0] + 200, left[1] + 200),
            (left[0] + left[2] // 2, left[1] + left[3] // 2),
            (left[0] + left[2] - 1, left[1] + left[3] // 2),
            (right[0], right[1] + right[3] // 2),
            (right[0] + right[2] // 2, right[1] + right[3] // 2),
            (right[0] + right[2] - 200, right[1] + right[3] - 200),
            # Near, not on, the corners: the top-left one opens the overview.
            (left[0] + 5, left[1] + 5),
            (right[0] + right[2] - 6, right[1] + right[3] - 6),
        ]
        deviations = []
        for target in targets:
            last = self.move_and_verify(provider, target)
            deviations.append(max(abs(last["x"] - target[0]), abs(last["y"] - target[1])))
            self.assertTrue(near(last, target), f"{target}: the pointer rests at {last}")
        # A long move must travel, not teleport.
        mark = self.window.mark()
        provider.move(left[0] + 200, left[1] + 200)
        self.window.settle()
        motions = [e for _s, e in self.window.since(mark) if e.get("event") == "motion"]
        self.assertGreaterEqual(len(motions), 5, "a long move arrived in too few steps")
        REPORT["pointer"] = {
            "targets": len(targets),
            "max_deviation_px": max(deviations),
            "long_move_motion_events": len(motions),
        }

    def test_2_a_click_follows_a_verified_move_and_focuses_the_field(self) -> None:
        _gate, provider = self.provider()
        before = self.move_and_verify(provider, self.field_center)
        x, y, width, height = self.field
        self.assertTrue(x <= before["x"] < x + width and y <= before["y"] < y + height)
        mark = self.window.mark()
        provider.click("left")
        press = self.window.wait(lambda e: e.get("event") == "press", 3.0, mark)
        release = self.window.wait(lambda e: e.get("event") == "release", 3.0, mark)
        self.assertIsNotNone(press)
        self.assertIsNotNone(release)
        self.assertEqual(press[1]["button"], 1)
        self.assertTrue(near(press[1], self.field_center), press[1])
        self.assertTrue(near(release[1], self.field_center), release[1])
        focused = self.window.wait(lambda e: e.get("event") == "focus" and e["field"], 3.0)
        self.assertIsNotNone(focused, "the click did not focus the field")
        self.window.settle()
        self.require_focus()
        REPORT["click"] = {"press": press[1], "release": release[1]}

    def test_3_turkish_text_arrives_exactly_and_the_clipboard_comes_back(self) -> None:
        _gate, provider = self.provider()
        self.focus_field(provider)
        clipboard = clipboardlib.WlClipboard()
        before = clipboard.save()
        self.window.send("clear")
        self.window.wait(lambda e: e.get("event") == "text" and e["value"] == "", 2.0)
        self.require_focus()
        mark = self.window.mark()
        started = time.monotonic()
        provider.type_text(TURKISH, restore_clipboard=True)
        typed = self.window.wait(
            lambda e: e.get("event") == "text" and e["value"] == TURKISH, 5.0, mark
        )
        elapsed = time.monotonic() - started
        same, now = restored(before)
        self.assertIsNotNone(typed, "the field does not hold the typed text")
        self.assertTrue(same, f"clipboard not restored: before {described(before)}, now {now}")

        self.window.send("clear")
        self.window.wait(lambda e: e.get("event") == "text" and e["value"] == "", 2.0)
        self.require_focus()
        mark = self.window.mark()
        provider.type_text("abc 123", raw=True)
        raw = self.window.wait(
            lambda e: e.get("event") == "text" and e["value"] == "abc 123", 5.0, mark
        )
        self.assertIsNotNone(raw, "raw typing did not produce the expected text")
        REPORT["text"] = {
            "turkish_exact": True,
            "clipboard_restored": True,
            "clipboard_was_empty": before is None,
            "clipboard_type": before.mime if before else None,
            "seconds": round(elapsed, 3),
        }

    def test_4_modifiers_reach_the_application(self) -> None:
        _gate, provider = self.provider()
        self.focus_field(provider)
        self.window.send("clear")
        self.window.wait(lambda e: e.get("event") == "text" and e["value"] == "", 2.0)
        self.require_focus()
        mark = self.window.mark()
        provider.key("shift+a")
        upper = self.window.wait(lambda e: e.get("event") == "text" and e["value"] == "A", 3.0, mark)
        shifted = self.window.wait(
            lambda e: e.get("event") == "key_press" and e["keyval"].lower() == "a" and e["shift"],
            3.0, mark,
        )
        self.assertIsNotNone(upper, f"shift+a left {self.window.last('text')}")
        self.assertIsNotNone(shifted)
        self.require_focus()
        mark = self.window.mark()
        provider.key("ctrl+a")  # select all in the test field: edits nothing
        controlled = self.window.wait(
            lambda e: e.get("event") == "key_press" and e["keyval"].lower() == "a" and e["control"],
            3.0, mark,
        )
        self.assertIsNotNone(controlled)
        self.window.send("clear")
        REPORT["modifiers"] = {"shift": True, "control": True}

    def test_5_drag_and_scroll_arrive_where_they_were_sent(self) -> None:
        _gate, provider = self.provider()
        right = self.monitors[-1]
        start = (right[0] + 300, right[1] + 650)
        end = (right[0] + 1100, right[1] + 850)
        self.move_and_verify(provider, start)
        mark = self.window.mark()
        provider.drag(*start, *end)
        released = self.window.wait(lambda e: e.get("event") == "release", 5.0, mark)
        self.window.settle()
        events = [e for _s, e in self.window.since(mark)]
        press = next(e for e in events if e.get("event") == "press")
        motions = [e for e in events if e.get("event") == "motion"]
        self.assertIsNotNone(released)
        self.assertTrue(near(press, start), press)
        self.assertTrue(near(released[1], end), released[1])
        self.assertGreaterEqual(len(motions), 3)

        self.move_and_verify(provider, self.blank)
        mark = self.window.mark()
        provider.scroll(2)
        self.window.settle()
        up = [e for _s, e in self.window.since(mark) if e.get("event") == "scroll"]
        mark = self.window.mark()
        provider.scroll(-2)
        self.window.settle()
        down = [e for _s, e in self.window.since(mark) if e.get("event") == "scroll"]
        self.assertTrue(up and all(e["dy"] < 0 for e in up), up)
        self.assertTrue(down and all(e["dy"] > 0 for e in down), down)
        REPORT["drag_scroll"] = {"drag_motions": len(motions), "scroll_up": len(up), "scroll_down": len(down)}

    def test_6_revoke_releases_a_held_key_and_button_without_another_request(self) -> None:
        gate, provider = self.provider()
        self.focus_field(provider)
        mark = self.window.mark()
        provider.key_down("shift")
        self.assertIsNotNone(self.window.wait(
            lambda e: e.get("event") == "key_press" and e["keyval"].startswith("Shift"), 3.0, mark))
        mark = self.window.mark()
        revoked = time.monotonic()
        gate.lock()
        key_up = self.window.wait(
            lambda e: e.get("event") == "key_release" and e["keyval"].startswith("Shift"), 3.0, mark)
        self.assertIsNotNone(key_up, "shift stayed down after the revoke")
        key_latency = key_up[0] - revoked

        gate.unlock(5, reason="live input parity, button")
        self.move_and_verify(provider, self.blank)
        mark = self.window.mark()
        provider.mouse_down("left")
        self.assertIsNotNone(self.window.wait(lambda e: e.get("event") == "press", 3.0, mark))
        mark = self.window.mark()
        revoked = time.monotonic()
        gate.lock()
        button_up = self.window.wait(lambda e: e.get("event") == "release", 3.0, mark)
        self.assertIsNotNone(button_up, "the button stayed down after the revoke")
        button_latency = button_up[0] - revoked
        self.assertLess(key_latency, 1.0)
        self.assertLess(button_latency, 1.0)
        REPORT["revoke"] = {
            "key_release_ms": round(key_latency * 1000),
            "button_release_ms": round(button_latency * 1000),
        }

    def test_7_expiry_releases_a_held_key(self) -> None:
        gate, provider = self.provider()
        self.focus_field(provider)
        # A short grant of its own: the next request moves to a new helper
        # bound to it, which has to open its keyboard (1.2 s) inside 4 s.
        lifetime = 4.0
        moment = time.time()
        gate._lease.grant(until=moment + lifetime, reason="live expiry", granted=moment,
                          granted_by="desktop_unlock")
        expires = time.monotonic() + (moment + lifetime - time.time())
        self.require_focus()
        mark = self.window.mark()
        provider.key_down("shift")
        self.assertIsNotNone(self.window.wait(
            lambda e: e.get("event") == "key_press" and e["keyval"].startswith("Shift"), 3.0, mark))
        released = self.window.wait(
            lambda e: e.get("event") == "key_release" and e["keyval"].startswith("Shift"),
            lifetime + 3.0, mark)
        self.assertIsNotNone(released, "shift stayed down after the grant expired")
        after_expiry = released[0] - expires
        self.assertGreater(after_expiry, -0.1, "released before the grant ended")
        self.assertLess(after_expiry, 1.5)
        REPORT["expiry"] = {"release_after_expiry_ms": round(after_expiry * 1000)}

    def test_8_the_hold_timer_releases_without_another_request(self) -> None:
        _gate, provider = self.provider(hold_max_seconds=5)
        self.focus_field(provider)
        mark = self.window.mark()
        provider.key_down("shift")
        pressed = self.window.wait(
            lambda e: e.get("event") == "key_press" and e["keyval"].startswith("Shift"), 3.0, mark)
        self.assertIsNotNone(pressed)
        released = self.window.wait(
            lambda e: e.get("event") == "key_release" and e["keyval"].startswith("Shift"), 8.0, mark)
        self.assertIsNotNone(released, "the hold timer did not release shift")
        held = released[0] - pressed[0]
        self.assertGreater(held, 4.5)
        self.assertLess(held, 6.5)
        self.assertEqual(provider.take_auto_released(), ["shift"])
        self.assertEqual(provider.take_auto_released(), [], "reported once")
        REPORT["hold_timer"] = {"held_seconds": round(held, 2)}

    def test_9_a_relative_nudge_moves_the_cursor_and_the_next_move_heals(self) -> None:
        """Adim 7: the SECOND, relative device, on the real desktop.

        Two things are proved here and nowhere else. First that a delta
        actually moves the cursor, in the direction asked and by a measurable
        amount -- unit tests can only show the events were written. Second
        that an absolute move afterwards lands where it says: after a relative
        nudge this device's ABS state no longer matches the cursor, and the
        kernel swallows a repeated absolute value, so without the resync step
        the pointer would silently stay put while the tool reported success.
        """
        _gate, provider = self.provider()
        park = (self.monitors[0][0] + self.monitors[0][2] // 2,
                self.monitors[0][1] + self.monitors[0][3] // 2)
        measured = {}
        for axis, (dx, dy) in (("x", (200, 0)), ("y", (0, -200))):
            before = self.move_and_verify(provider, park)
            mark = self.window.mark()
            sent = provider.move_by(dx, dy)
            self.assertEqual(tuple(sent), (dx, dy), "the whole delta was sent")
            self.window.wait(lambda e: e.get("event") == "motion", 3.0, mark)
            self.window.settle()
            after = self.window.last("motion")
            moved = (after["x"] - before["x"], after["y"] - before["y"])
            # The scale is the user's mouse-speed setting (k = 1 + speed), so
            # the exact pixel count is not pinned here -- the direction and
            # the fact that it moved at all are.
            self.assertNotEqual(moved, (0, 0), f"{axis}: the nudge moved nothing")
            if dx:
                self.assertGreater(moved[0] * dx, 0, f"{axis}: wrong direction")
                self.assertEqual(moved[1], 0, f"{axis}: the other axis moved")
            if dy:
                self.assertGreater(moved[1] * dy, 0, f"{axis}: wrong direction")
                self.assertEqual(moved[0], 0, f"{axis}: the other axis moved")
            measured[axis] = {"sent": [dx, dy], "moved": list(moved)}

            # The position is unknown now, and says so.
            self.assertIsNone(provider.position, "a nudge must forget the position")

            # Back to the SAME point the nudge started from: the case that
            # silently did nothing before the resync step existed.
            healed = self.move_and_verify(provider, park)
            deviation = max(abs(healed["x"] - park[0]), abs(healed["y"] - park[1]))
            self.assertLessEqual(deviation, 1, f"{axis}: the absolute move did not heal")
            measured[axis]["heal_deviation_px"] = deviation
        REPORT["move_by"] = measured

    def test_9b_absolute_accuracy_survives_the_second_device(self) -> None:
        """The BTN_TOUCH lesson, re-measured with the relative device OPEN.

        A second uinput device changes how the compositor enumerates and
        merges pointers, and the absolute device's <=1 px accuracy is the
        measurement the whole coordinate layer rests on. It is nearly free to
        prove it again, so it is proved again.
        """
        _gate, provider = self.provider()
        provider.ensure(pointer=True, relative=True)
        provider.move_by(50, 0)          # the relative device is now in use
        left, right = self.monitors[0], self.monitors[-1]
        targets = [
            (left[0] + 200, left[1] + 200),
            (left[0] + left[2] // 2, left[1] + left[3] // 2),
            (right[0] + right[2] // 2, right[1] + right[3] // 2),
            (right[0] + right[2] - 200, right[1] + right[3] - 200),
        ]
        deviations = []
        for target in targets:
            last = self.move_and_verify(provider, target)
            deviations.append(max(abs(last["x"] - target[0]), abs(last["y"] - target[1])))
            self.assertTrue(near(last, target), f"{target}: the pointer rests at {last}")
        REPORT["absolute_with_relative_open"] = {
            "targets": len(targets),
            "max_deviation_px": max(deviations),
        }


if __name__ == "__main__":
    unittest.main(verbosity=2)
