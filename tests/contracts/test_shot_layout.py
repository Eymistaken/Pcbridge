#!/usr/bin/env python3
"""A shot coordinate is refused once the display layout it was taken under changed.

`PLAN.md` Task 5.3, item 7. A shot record keeps the offset and scale of its
monitor at capture time, and `capture.to_global()` turns an image pixel into a
global point with them. If a monitor is added, removed, moved or resized
between the capture and the click, the same offset lands on another screen and
the click goes somewhere the agent never saw -- the class of mistake this
repository has paid for twice. The record now carries `monitors.topology_id()`
and the conversion refuses with DISPLAY_CHANGED when it no longer matches.

No compositor, no helper, no input: the capture pipeline runs with the fake
handles from `test_capture_backend_selection` and a patched monitor table.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop import capture as capturelib  # noqa: E402
from pcbridge.desktop import monitors as monitorslib  # noqa: E402
from pcbridge.desktop.backends.python import PythonCaptureProvider  # noqa: E402
from pcbridge.desktop.errors import DesktopError, ErrorCategory, ErrorCode  # noqa: E402
from tests.contracts.test_capture_backend_selection import (  # noqa: E402
    MONITORS,
    PIL_OK,
    FakeLegacyScreenCast,
    FakeNativeClient,
    native_handle,
    png_bytes,
)

# The same two panels, swapped: the right one is now on the left. Same sizes,
# same count -- only the offsets moved, which is the case nothing else notices.
SWAPPED = [
    dataclasses.replace(MONITORS[1], index=1, x=0),
    dataclasses.replace(MONITORS[0], index=2, x=1920),
]
# A resolution change on the right panel.
RESIZED = [
    MONITORS[0],
    dataclasses.replace(MONITORS[1], width=2560, height=1440),
]


@unittest.skipUnless(PIL_OK, "Pillow is required to write the shot PNGs")
class ShotLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = load_config(str(ROOT / "config.example.toml"))
        self.frames = {
            monitor.connector: png_bytes(monitor.width, monitor.height)
            for monitor in MONITORS
        }
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)

    def shoot(self, screencast) -> list[capturelib.Shot]:
        # Whether gnome-screenshot is installed here is not what is measured.
        with mock.patch.object(monitorslib, "list_monitors", return_value=MONITORS), \
                mock.patch.object(capturelib, "available", return_value=(True, "")):
            return capturelib.capture(
                "all",
                out_dir=self.out,
                scale_long_edge=1536,
                include_pointer=False,
                screencast=screencast,
            )

    def to_global(self, shot_id: str, layout, x: int = 100, y: int = 100):
        with mock.patch.object(monitorslib, "list_monitors", return_value=layout):
            return capturelib.to_global(x, y, shot=shot_id, dirs=[self.out])

    def canvas(self, tmpdir: Path, include_pointer: bool) -> Path:
        """What `gnome-screenshot -f` would write: the whole 3840x1080 canvas."""
        path = Path(tmpdir) / "canvas.png"
        path.write_bytes(png_bytes(3840, 1080))
        return path

    def test_every_capture_path_records_the_layout_it_was_taken_under(self) -> None:
        expected = monitorslib.topology_id(MONITORS)
        for name, screencast in (
            ("python", FakeLegacyScreenCast(self.frames)),
            ("native", native_handle(self.cfg, FakeNativeClient(self.frames))),
            ("gnome-screenshot", None),
        ):
            with self.subTest(backend=name), mock.patch.object(
                capturelib, "_grab_canvas", self.canvas
            ):
                for shot in self.shoot(screencast):
                    self.assertEqual(shot.topology, expected)
                    record = json.loads(
                        (self.out / f"{shot.id}.json").read_text(encoding="utf-8")
                    )
                    self.assertEqual(record["topology_id"], expected)
                    self.assertEqual(
                        capturelib.load_shot(shot.id, [self.out]).topology, expected
                    )

    def test_an_unchanged_layout_converts_exactly_as_before(self) -> None:
        right = self.shoot(FakeLegacyScreenCast(self.frames))[1]
        self.assertEqual(
            self.to_global(right.id, MONITORS, 768, 432),
            right.to_global(768, 432),
        )
        self.assertEqual(right.to_global(768, 432), (2880, 540))

    def test_a_moved_or_resized_layout_refuses_the_shot(self) -> None:
        right = self.shoot(FakeLegacyScreenCast(self.frames))[1]
        for name, layout in (("swapped", SWAPPED), ("resized", RESIZED)):
            with self.subTest(layout=name):
                with self.assertRaises(capturelib.ShotLayoutChanged) as raised:
                    self.to_global(right.id, layout)
                self.assertIn(right.id, str(raised.exception))

    def test_the_provider_reports_it_as_a_retryable_display_change(self) -> None:
        right = self.shoot(FakeLegacyScreenCast(self.frames))[1]
        provider = PythonCaptureProvider(self.cfg, screencast=FakeLegacyScreenCast(self.frames))
        with mock.patch.object(monitorslib, "list_monitors", return_value=SWAPPED):
            with self.assertRaises(DesktopError) as raised:
                provider.to_global(100, 100, shot=right.id, dirs=[self.out])
        self.assertIs(raised.exception.code, ErrorCode.DISPLAY_CHANGED)
        self.assertIs(raised.exception.category, ErrorCategory.COORDINATE)
        self.assertTrue(raised.exception.retryable)

    def test_a_record_from_before_the_field_still_converts(self) -> None:
        right = self.shoot(FakeLegacyScreenCast(self.frames))[1]
        path = self.out / f"{right.id}.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        del record["topology_id"]
        path.write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual(self.to_global(right.id, SWAPPED), right.to_global(100, 100))

    def test_monitor_and_global_coordinates_never_consult_a_shot(self) -> None:
        self.shoot(FakeLegacyScreenCast(self.frames))
        with mock.patch.object(monitorslib, "list_monitors", return_value=SWAPPED):
            self.assertEqual(capturelib.to_global(10, 10, monitor=2, dirs=[self.out]), (1930, 10))
            self.assertEqual(capturelib.to_global(3000, 10, dirs=[self.out]), (3000, 10))


if __name__ == "__main__":
    unittest.main(verbosity=2)
