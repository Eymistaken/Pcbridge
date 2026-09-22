#!/usr/bin/env python3
"""Window operations on the real desktop (Task 6.4).

Skipped unless `PCBRIDGE_TEST_ATSPI=1`. The tests open small GTK4 windows of
their own (`window_app.py`) through temporary desktop entries in
~/.local/share/applications, read the accessibility tree, and close the
windows and remove the entries afterwards. No key is sent, except by the
search fallback test, which also needs `PCBRIDGE_TEST_INPUT=1`: it presses
Super, types the test application's name and presses Return, exactly what
the fallback does, while one of the test's own windows has focus.

    PCBRIDGE_TEST_ATSPI=1 PCBRIDGE_TEST_INPUT=1 \\
      ./.venv/bin/python -m unittest tests/live/test_window_operations.py -v

`PCBRIDGE_WINDOW_REPORT=<path>` writes the measured timings there as JSON.

The GNOME Shell extension is left out: `activate_window` answers False, so
the paths behind it run. The extension's own path was measured on the real
session on 2026-09-12 (3-6 ms). Every test runs with the Python accessibility
reader and once more with the native helper when one is found.
"""

from __future__ import annotations

import dataclasses
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop import apps  # noqa: E402
from pcbridge.desktop import uitree as uitreelib  # noqa: E402
from pcbridge.desktop.backends.python import (  # noqa: E402
    PythonAccessibilityProvider,
    PythonInputProvider,
)
from pcbridge.desktop.errors import DesktopError, ErrorCode  # noqa: E402
from tests.live.test_accessibility_parity import native_provider  # noqa: E402

SYSTEM_PYTHON = "/usr/bin/python3"
APPLICATIONS = Path.home() / ".local/share/applications"
READ = os.environ.get("PCBRIDGE_TEST_ATSPI") == "1"
INPUT = READ and os.environ.get("PCBRIDGE_TEST_INPUT") == "1"
REPORT: dict[str, list[float]] = {}


def _measure(name: str, started: float) -> float:
    ms = round((time.monotonic() - started) * 1000, 1)
    REPORT.setdefault(name, []).append(ms)
    return ms


def screen_locked() -> bool:
    try:
        out = subprocess.run(
            ["busctl", "--user", "call", "org.gnome.ScreenSaver",
             "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver", "GetActive"],
            capture_output=True, text=True, timeout=2,
        ).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out == ["b", "true"]


class TestApp:
    """A temporary desktop entry for `window_app.py` and what it starts.

    The application id is the entry's file name and the program's name, so
    GNOME ties the window to the entry and the accessibility tree names the
    application after it: the same links a real application has.
    """

    def __init__(self, label: str) -> None:
        tag = secrets.token_hex(3)
        self.app_id = f"org.pcbridge.WindowTest.{label}{tag}"
        self.name = f"Pcbridge {label} Test {tag}"
        self.entry = APPLICATIONS / f"{self.app_id}.desktop"
        APPLICATIONS.mkdir(parents=True, exist_ok=True)
        self.entry.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={self.name}\n"
            f"Exec={SYSTEM_PYTHON} {HERE / 'window_app.py'} --app-id {self.app_id}"
            f" --title '{self.name}' --timeout 120\n"
            "Terminal=false\n",
            encoding="utf-8",
        )

    def pids(self) -> list[int]:
        found = []
        marker = self.app_id.encode()
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            try:
                cmdline = Path(f"/proc/{name}/cmdline").read_bytes()
            except OSError:
                continue
            if marker in cmdline and b"window_app.py" in cmdline:
                found.append(int(name))
        return found

    def close(self) -> None:
        for pid in self.pids():
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 3
        while self.pids() and time.monotonic() < deadline:
            time.sleep(0.1)
        # Our own test file, not a user file.
        self.entry.unlink(missing_ok=True)


class NoKeys:
    """A keyboard that sends nothing: any use of it is a test failure."""

    def __init__(self) -> None:
        self.events: list[tuple] = []

    def key(self, keys: str) -> None:
        self.events.append(("key", keys))

    def type_text(self, text: str, *, raw: bool) -> None:
        self.events.append(("type", len(text), raw))


def cgroup(pid: int) -> str:
    return Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8").strip().rsplit(":", 1)[-1]


@unittest.skipUnless(READ, "set PCBRIDGE_TEST_ATSPI=1: opens small test windows")
class WindowOperationsLive(unittest.TestCase):
    #: Timings are kept per accessibility reader.
    label = "python"

    def setUp(self) -> None:
        ok, why = uitreelib.available()
        if not ok:
            self.skipTest(why)
        if screen_locked():
            self.skipTest("the screen is locked")
        self.tree = self.provider()
        self.activations: list[str] = []
        patcher = mock.patch.object(apps, "activate_window", side_effect=self._no_extension)
        patcher.start()
        self.addCleanup(patcher.stop)

    def provider(self):
        return PythonAccessibilityProvider()

    def _no_extension(self, window: str) -> bool:
        self.activations.append(window)
        return False

    def app(self, label: str = "Target") -> TestApp:
        app = TestApp(label)
        self.addCleanup(app.close)
        # GNOME and GIO notice a new entry through a file monitor.
        time.sleep(1.0)
        return app

    def front(self, name: str, keys=None) -> tuple[apps.Outcome, object]:
        keys = keys if keys is not None else NoKeys()
        outcome = apps.bring_to_front(name, keys, self.tree.focused_window, self.tree.windows)
        return outcome, keys

    def test_1_a_closed_application_opens_without_a_key(self) -> None:
        app = self.app()
        started = time.monotonic()
        outcome, keys = self.front(app.name)
        ms = _measure(f"{self.label}_cold_launch_ms", started)

        self.assertEqual(outcome.path, "launch", outcome.note)
        self.assertIn("focused", outcome.note)
        self.assertEqual(keys.events, [])
        pids = app.pids()
        self.assertEqual(len(pids), 1, pids)
        # Its own systemd scope: restarting pcbridge (or the caller) leaves it.
        self.assertIn("/app-pcbridge-", cgroup(pids[0]))
        self.assertNotEqual(cgroup(pids[0]), cgroup(os.getpid()))
        self.assertLess(ms, apps.LAUNCH_OBSERVE * 1000)

    def test_2_a_window_in_front_gets_no_key(self) -> None:
        app = self.app()
        self.front(app.name)
        started = time.monotonic()
        outcome, keys = self.front(app.name)
        _measure(f"{self.label}_already_ms", started)

        self.assertEqual(outcome.path, "already", outcome.note)
        self.assertEqual(keys.events, [])
        self.assertEqual(len(app.pids()), 1)

    def test_3_a_name_that_is_no_application_sends_nothing(self) -> None:
        name = f"pcbridge no such window {secrets.token_hex(3)}"
        keys = NoKeys()
        with self.assertRaises(DesktopError) as caught:
            self.front(name, keys)
        self.assertEqual(caught.exception.code, ErrorCode.TARGET_MISMATCH)
        self.assertEqual(caught.exception.execution_state, "not_started")
        self.assertEqual(keys.events, [])

    def test_4_a_batch_launch_is_verified_by_its_window(self) -> None:
        app = self.app()
        started = time.monotonic()
        outcome = apps.launch_application(
            apps.resolve_application(app.name), self.tree.focused_window, self.tree.windows
        )
        _measure(f"{self.label}_launch_ms", started)
        self.assertEqual(outcome.path, "launch")
        self.assertEqual(len(app.pids()), 1)


class NativeWindowOperationsLive(WindowOperationsLive):
    label = "native"

    def provider(self):
        return native_provider(self, "accessibility.read")


@unittest.skipUnless(
    INPUT,
    "set PCBRIDGE_TEST_ATSPI=1 and PCBRIDGE_TEST_INPUT=1: presses Super, types "
    "a test application's name and Return",
)
class SearchFallbackLive(WindowOperationsLive):
    """The degraded fallback: GNOME search brings an open window forward."""

    def test_1_a_closed_application_opens_without_a_key(self) -> None:
        self.skipTest("covered by WindowOperationsLive")

    test_2_a_window_in_front_gets_no_key = test_1_a_closed_application_opens_without_a_key
    test_3_a_name_that_is_no_application_sends_nothing = test_1_a_closed_application_opens_without_a_key
    test_4_a_batch_launch_is_verified_by_its_window = test_1_a_closed_application_opens_without_a_key

    def test_5_search_brings_an_open_window_forward(self) -> None:
        target = self.app("Target")
        other = self.app("Other")
        self.assertEqual(self.front(target.name)[0].path, "launch")
        self.assertEqual(self.front(other.name)[0].path, "launch")
        # Keys go only while one of the test's own windows has focus: if the
        # overview did not open, the name would land in that window.
        app, _title = self.tree.focused_window()
        if apps._norm(app) != apps._norm(other.app_id):
            self.skipTest("the test window lost focus before the search; nothing was sent")

        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        cfg = dataclasses.replace(
            load_config(str(ROOT / "config.example.toml")), state_dir=Path(scratch.name)
        )
        keyboard = PythonInputProvider(cfg)
        self.addCleanup(keyboard.close)
        keyboard.ensure(keyboard=True)

        started = time.monotonic()
        outcome, _keys = self.front(target.name, keyboard)
        _measure(f"{self.label}_search_ms", started)

        self.assertEqual(outcome.path, "search", outcome.note)
        app, _title = self.tree.focused_window()
        self.assertEqual(apps._norm(app), apps._norm(target.app_id))
        self.assertEqual(len(target.pids()), 1, "the search started a second instance")


def tearDownModule() -> None:
    target = os.environ.get("PCBRIDGE_WINDOW_REPORT")
    if target and REPORT:
        Path(target).write_text(json.dumps(REPORT, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main(verbosity=2)
