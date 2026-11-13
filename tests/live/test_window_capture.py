#!/usr/bin/env python3
"""GNOME window capture (2.8): the focused window as a region of the native frame.

Skipped unless `PCBRIDGE_TEST_CAPTURE=1`, and meant for a headless or nested
GNOME Shell on its own session bus whose pcbridge extension reads THIS
test's grant: start the shell with `PCBRIDGE_GORUNUR_STATE` set to
`<root>/state/desktop_unlock.json`, and the test uses `<root>` as its scratch
directory. On the real desktop the real extension never sees that grant, so
the test skips instead of capturing anything there. It sends no input: it
opens one painted GTK4 window, which takes the focus on its own, and
captures it.

What it shows, on the native helper and on the Python screencast:

* the extension names the window with its frame (`FocusedWindowFrame`);
* `screen_capture(monitor="window")` gives a region shot of exactly that
  box, made of the window's own color, with a global offset, so its record
  maps the window's pixels back to the right global points;
* no gnome-screenshot runs.
"""

from __future__ import annotations

import os
import shutil
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
from pcbridge.desktop import apps as appslib  # noqa: E402
from pcbridge.desktop import capture as capturelib  # noqa: E402
from pcbridge.desktop import monitors as monitorslib  # noqa: E402
from pcbridge.desktop import screencast as screencastlib  # noqa: E402
from pcbridge.desktop.backends.python import PythonCaptureProvider  # noqa: E402
from pcbridge.desktop.backends.rust import RustCaptureProvider  # noqa: E402
from pcbridge.desktop.safety import SafetyGate  # noqa: E402
from tests.live.test_capture_parity import (  # noqa: E402
    SYSTEM_PYTHON,
    close_to,
    release_binary,
    rgb,
    scratch_config,
    wait_until,
)

LIVE = os.environ.get("PCBRIDGE_TEST_CAPTURE") == "1"
STATE = os.environ.get("PCBRIDGE_GORUNUR_STATE", "")
COLOR = (255, 0, 255)
TITLE = "pcbridge window capture"


def scratch_root() -> Path | None:
    """`<root>` when the shell's extension reads `<root>/state/desktop_unlock.json`."""
    path = Path(STATE) if STATE else None
    if path is None or path.name != "desktop_unlock.json" or path.parent.name != "state":
        return None
    return path.parents[1]


@unittest.skipUnless(LIVE, "set PCBRIDGE_TEST_CAPTURE=1 (inside a headless or nested GNOME Shell)")
@unittest.skipIf(scratch_root() is None,
                 "start the shell with PCBRIDGE_GORUNUR_STATE=<root>/state/desktop_unlock.json")
class WindowCaptureInGnome(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        binary, why = release_binary()
        if binary is None:
            raise unittest.SkipTest(why)
        if appslib.extension_frame_support() is not True:
            raise unittest.SkipTest("the extension in this shell has no FocusedWindowFrame")
        cls.root = scratch_root()
        cls.root.mkdir(parents=True, exist_ok=True)
        cls.cfg = load_config(str(scratch_config(cls.root, binary)))
        cls.gate = SafetyGate(cls.cfg)
        cls.gate.unlock(10, reason="live window capture")
        # Measured 2026-11-13 in a headless GNOME 46 shell: a GTK window
        # started before xdg-desktop-portal is up waits about 25 s for it, so
        # the window skips the portals; and a window started without an
        # activation token never gets the focus on its own, so it is brought
        # forward the way `window_focus` does, through the extension.
        cls.window = subprocess.Popen(
            [SYSTEM_PYTHON, str(HERE / "window_app.py"), "--app-id", "io.pcbridge.WindowCapture",
             "--title", TITLE, "--color", "#ff00ff", "--timeout", "120"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "GDK_DEBUG": "no-portals"},
        )
        cls.frame = None

        def focused() -> bool:
            if not appslib.activate_window(TITLE):
                return False
            frame = appslib.extension_focused_frame()
            if frame is not None and frame.title == TITLE:
                cls.frame = frame
                return True
            return False

        if not wait_until(focused, 45, 0.3):
            cls.tearDownClass()
            raise AssertionError("the test window never had the focus")
        time.sleep(0.5)  # let the compositor present the painted window

    @classmethod
    def tearDownClass(cls) -> None:
        if getattr(cls, "window", None) is not None:
            cls.window.terminate()
            try:
                cls.window.wait(10)
            except subprocess.TimeoutExpired:
                cls.window.kill()
        if getattr(cls, "gate", None) is not None:
            cls.gate.lock()

    def providers(self):
        yield "native", RustCaptureProvider(self.cfg, gate=self.gate)
        ok, _why = screencastlib.available()
        if ok:
            yield "python", PythonCaptureProvider(self.cfg, screencastlib.ScreenCast())

    def test_the_window_is_its_own_box_of_the_monitor_frame(self) -> None:
        mons = monitorslib.list_monitors(use_cache=False)
        left, top = monitorslib.platform_origin(mons)
        fx, fy, fw, fh = self.frame.box
        expected_offset = (fx - left, fy - top)
        for name, provider in self.providers():
            with self.subTest(backend=name):
                out = Path(tempfile.mkdtemp(prefix=f"win-{name}-", dir=self.root))
                provider.start(cursor=False)
                try:
                    with mock.patch.object(capturelib, "_grab_window",
                                           side_effect=AssertionError("gnome-screenshot ran")):
                        shots = provider.capture("window", out_dir=out, scale_long_edge=0,
                                                 include_pointer=False)
                finally:
                    provider.close()
                self.assertEqual(len(shots), 1)
                shot = shots[0]
                print(f"\n  {name}: frame {self.frame.box} -> shot {shot.id} offset "
                      f"{shot.offset} size {shot.desktop_units}")
                self.assertTrue(shot.region)
                self.assertEqual(shot.offset, expected_offset)
                self.assertEqual(shot.desktop_units, (fw, fh))
                image = rgb(Path(shot.path))
                center = image.getpixel((image.width // 2, image.height * 3 // 4))
                self.assertTrue(close_to(center, COLOR), f"{name}: center is {center}")
                gx, gy = capturelib.to_global(
                    image.width // 2, image.height // 2, shot=shot.id, dirs=[out])
                self.assertEqual((gx, gy), (expected_offset[0] + fw // 2,
                                            expected_offset[1] + fh // 2))
                shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
