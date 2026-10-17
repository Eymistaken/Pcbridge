#!/usr/bin/env python3
"""Showing and hiding the pcbridge browser (pcbridge/browser/visibility.py)
without a compositor: each backend's request and how its answer is read,
the wanted state on disk, and the manager's refusal without a browser.

The real compositors are covered by scripts/dev/browser-nested.py (GNOME)
and the VM checks recorded in docs/dev/measured-facts.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.browser import manager as M  # noqa: E402
from pcbridge.browser import visibility as V  # noqa: E402
from pcbridge.config import BrowserSpec  # noqa: E402


def done(stdout: str, code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, stdout, "")


class GnomeTests(unittest.TestCase):
    def test_show_without_focus_is_one_busctl_call_and_its_answer_is_read(self) -> None:
        with mock.patch.object(V.subprocess, "run", return_value=done("bbb true true false\n")) as run:
            view = V.Gnome().set(101, True, False)
        args = run.call_args.args[0]
        self.assertEqual(args[-4:], ["SetBrowserVisible", "bb", "true", "false"])
        self.assertIn(V.BUS, args)
        self.assertEqual((view.found, view.visible, view.focused), (True, True, False))
        self.assertIn("on screen", view.describe())
        self.assertNotIn("focused", view.describe())

    def test_an_old_extension_says_what_to_do(self) -> None:
        with mock.patch.object(V.subprocess, "run", return_value=done("", 1)):
            view = V.Gnome().state(101)
        self.assertFalse(view.found)
        self.assertIn("log out and\nback in".replace("\n", " "), view.describe().replace("\n", " "))

    def test_hidden_is_never_reported_as_focused(self) -> None:
        self.assertEqual(V.View(True, False, True, "x").describe(),
                         "The pcbridge browser is hidden (through x).")


class HyprlandTests(unittest.TestCase):
    CLIENTS = [
        {"pid": 101, "address": "0xabc", "workspace": {"name": "special:pcbridge"}, "focusHistoryID": 3},
        {"pid": 555, "address": "0xdef", "workspace": {"name": "1"}, "focusHistoryID": 0},
    ]

    def test_state_reads_only_the_browser_pid(self) -> None:
        h = V.Hyprland()
        with mock.patch.object(h, "_hyprctl", return_value=done(json.dumps(self.CLIENTS))):
            view = h.state(101)
        self.assertEqual((view.found, view.visible, view.focused), (True, False, False))

    def test_dispatch_takes_only_a_window_address(self) -> None:
        h = V.Hyprland()
        with mock.patch.object(h, "_hyprctl", return_value=done("ok")) as call:
            self.assertFalse(h._dispatch("hyprlang", "move", "class:chrome", "special:pcbridge"))
            call.assert_not_called()
            self.assertTrue(h._dispatch("hyprlang", "move", "0xabc", "special:pcbridge"))
            self.assertEqual(call.call_args.args,
                             ("dispatch", "movetoworkspacesilent", "special:pcbridge,address:0xabc"))


class WantedStateTests(unittest.TestCase):
    def test_hidden_unless_written_visible(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            d = Path(raw) / "browser"
            self.assertFalse(V.wanted(d))
            V.write_wanted(d, True)
            self.assertTrue(V.wanted(d))
            self.assertEqual((d / "view.json").stat().st_mode & 0o777, 0o600)
            V.write_wanted(d, False)
            self.assertFalse(V.wanted(d))

    def test_the_manager_needs_a_running_browser_and_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            m = M.BrowserManager(SimpleNamespace(state_dir=Path(raw), browser=BrowserSpec()))
            view = m.view("show")
            self.assertFalse(view.found)
            self.assertIn("not running", view.describe())
            self.assertFalse((m.dir / "view.json").exists(), "nothing written without a browser")

    def test_launch_flags_give_the_window_its_own_app_id(self) -> None:
        self.assertIn("--class=pcbridge-browser", M.LAUNCH_FLAGS)


if __name__ == "__main__":
    unittest.main()
