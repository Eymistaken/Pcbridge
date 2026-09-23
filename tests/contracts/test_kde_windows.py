"""KDE Plasma: window focus through KWin, Qt accessibility, window capture."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pcbridge.desktop import a11y, apps, capture, compositor, monitors  # noqa: E402
from pcbridge.desktop import kwin_helper  # noqa: E402


def done(stdout: str = "", returncode: int = 0):
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


class KWinFocusTests(unittest.TestCase):
    def setUp(self) -> None:
        # The source every compositor question goes back to.
        patcher = mock.patch("pcbridge.desktop.session.desktop_kind", return_value="kde")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_availability_asks_whether_kwin_owns_its_name(self) -> None:
        with mock.patch.object(apps.subprocess, "run", return_value=done("b true\n")) as run:
            self.assertTrue(apps.extension_focus_available())
        self.assertEqual(run.call_args.args[0][-1], "org.kde.KWin")

    def test_activation_is_true_only_when_kwin_says_so(self) -> None:
        cases = (
            ({"ok": True, "activated": True}, True),
            ({"ok": True, "activated": False, "ambiguous": True}, False),
            ({"ok": False, "error": "x", "activated": True}, False),
        )
        for reply, expected in cases:
            with self.subTest(reply=reply), \
                    mock.patch.object(apps.subprocess, "run",
                                      return_value=done(json.dumps(reply))) as run:
                self.assertIs(apps._extension_activate("kate"), expected)
            self.assertEqual(json.loads(run.call_args.kwargs["input"]),
                             {"cmd": "activate", "target": "kate"})
        with mock.patch.object(apps.subprocess, "run",
                               side_effect=apps.subprocess.TimeoutExpired("python3", 6)):
            self.assertFalse(apps._extension_activate("kate"))

    def test_the_focused_window_names_app_and_title_or_nothing(self) -> None:
        found = {"ok": True, "found": True, "app": "org.kde.kate", "title": "a.txt — Kate",
                 "geometry": [1, 2, 3, 4]}
        with mock.patch.object(apps.subprocess, "run", return_value=done(json.dumps(found))):
            self.assertEqual(apps.extension_focused_window(), ("org.kde.kate", "a.txt — Kate"))
        with mock.patch.object(apps.subprocess, "run",
                               return_value=done('{"ok": true, "found": false}')):
            self.assertIsNone(apps.extension_focused_window())
        with mock.patch.object(apps.subprocess, "run", return_value=done("not json")):
            self.assertIsNone(apps.extension_focused_window())

    def test_the_search_fallback_opens_krunner_not_the_menu(self) -> None:
        keys: list[str] = []
        backend = SimpleNamespace(key=keys.append, type_text=lambda *a, **k: "")
        target = apps.Application(text="Kate", key="kate")
        with mock.patch.object(apps.time, "sleep"), \
                mock.patch.object(apps, "_shows", return_value=True):
            apps._search(target, backend, lambda: ("org.kde.kate", "Kate"), 0)
        self.assertEqual(keys[0], "alt+space")

    def test_the_script_takes_the_request_as_data_not_code(self) -> None:
        hostile = '"); workspace.activeWindow = null; ("'
        text = kwin_helper.SCRIPT % {
            "request": json.dumps({"cmd": "activate", "target": hostile}),
            "name": "n", "max": 200, "max_target": 200}
        first = text.strip().splitlines()[0]
        self.assertTrue(first.startswith("var request = {"))
        self.assertEqual(json.loads(first[len("var request = "):-1])["target"], hostile)


class AccessibilitySwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)

    def _bus(self, value: str):
        calls: list[tuple] = []

        def run(argv, **_):
            calls.append(tuple(argv))
            if "get-property" in argv:
                return done(f"b {value}\n")
            return done()

        return calls, run

    def test_plasma_turns_it_on_and_the_end_of_the_grant_turns_it_back_off(self) -> None:
        calls, run = self._bus("false")
        with mock.patch.object(compositor, "is_kde", return_value=True), \
                mock.patch.object(a11y.subprocess, "run", side_effect=run):
            self.assertIn("Qt accessibility is on", a11y.enable_for_grant(self.state))
            self.assertTrue((self.state / a11y.MARKER).exists())
            self.assertTrue(a11y.restore(self.state))
        self.assertFalse((self.state / a11y.MARKER).exists())
        sets = [c[-1] for c in calls if "set-property" in c]
        self.assertEqual(sets, ["true", "false"])

    def test_a_user_who_had_it_on_keeps_it_on(self) -> None:
        calls, run = self._bus("true")
        with mock.patch.object(compositor, "is_kde", return_value=True), \
                mock.patch.object(a11y.subprocess, "run", side_effect=run):
            self.assertEqual(a11y.enable_for_grant(self.state), "")
            self.assertFalse(a11y.restore(self.state))
        self.assertFalse(any("set-property" in c for c in calls))

    def test_gnome_is_left_alone(self) -> None:
        with mock.patch.object(compositor, "is_kde", return_value=False), \
                mock.patch.object(a11y.subprocess, "run") as run:
            self.assertEqual(a11y.enable_for_grant(self.state), "")
        run.assert_not_called()


class WindowRegionTests(unittest.TestCase):
    MONS = monitors.resolve_state({
        "layout_mode": "logical",
        "physical": [
            {"connector": "A", "modes": [{"width": 1280, "height": 800, "is_current": True}]},
            {"connector": "B", "modes": [{"width": 1280, "height": 800, "is_current": True}]},
        ],
        "logical": [
            {"x": 0, "y": 0, "scale": 1.0, "transform": 0, "primary": True, "connectors": ["A"]},
            {"x": 1280, "y": 0, "scale": 1.0, "transform": 0, "primary": False,
             "connectors": ["B"]},
        ],
    })

    def _region(self, geometry):
        reply = {"ok": True, "found": True, "app": "a", "title": "t", "geometry": geometry}
        with mock.patch("pcbridge.desktop.apps._kwin", return_value=reply), \
                mock.patch.object(monitors, "list_monitors", return_value=self.MONS):
            return capture._kwin_window_region()

    def test_the_focused_window_is_a_box_on_its_monitor(self) -> None:
        box, home = self._region([1600, 146, 640, 508])
        self.assertEqual(box, (1600, 146, 640, 508))
        self.assertEqual(home.connector, "B")

    def test_an_overhanging_window_is_clipped_to_the_monitor_of_its_center(self) -> None:
        # Center at x=1100: monitor A; the right part and the top are cut.
        box, home = self._region([800, -20, 600, 400.5])
        self.assertEqual(home.connector, "A")
        self.assertEqual(box, (800, 0, 480, 381))

    def test_no_focused_window_is_an_error_not_a_guess(self) -> None:
        with mock.patch("pcbridge.desktop.apps._kwin", return_value={"ok": True, "found": False}):
            with self.assertRaises(capture.CaptureError):
                capture._kwin_window_region()


if __name__ == "__main__":
    unittest.main()
