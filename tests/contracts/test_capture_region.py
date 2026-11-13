#!/usr/bin/env python3
"""Step 8.5: capture a region of one monitor, and brighten what the agent sees.

A region shot is an ordinary shot record whose `offset` is the region's global
top-left and whose `desktop_size` is the region's size, so `shot=` clicks on
it convert with the same formula as a whole-monitor shot. These tests pin
that on all three capture paths, the three coordinate spaces a region can be
given in, the refusals (two monitors, too small, outside the source image,
`shot` with `monitor`), and that enhancement touches only the copy sent to
the client.

No compositor, no helper, no input: the fake handles from
`test_capture_backend_selection`, whose frames carry a pixel pattern that
tells every position apart.
"""

from __future__ import annotations

import io
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
from pcbridge.desktop import presentation as presentationlib  # noqa: E402
from tests.contracts.test_capture_backend_selection import (  # noqa: E402
    MONITORS,
    PIL_OK,
    FakeLegacyScreenCast,
    FakeNativeClient,
    native_handle,
    png_bytes,
)

if PIL_OK:
    from PIL import Image, ImageStat


def pattern(x: int, y: int) -> tuple[int, int, int]:
    """The fake frames' pixel at monitor-local (x, y)."""
    return (x % 256, y % 256, (x + y) % 256)


@unittest.skipUnless(PIL_OK, "Pillow is required to write the shot PNGs")
class RegionCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = load_config(str(ROOT / "config.example.toml"))
        self.frames = {
            monitor.connector: png_bytes(monitor.width, monitor.height)
            for monitor in MONITORS
        }
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        patcher = mock.patch.object(monitorslib, "list_monitors", return_value=MONITORS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def canvas(self, tmpdir: Path, include_pointer: bool) -> Path:
        """What `gnome-screenshot -f` writes: both frames side by side."""
        image = Image.new("RGB", (3840, 1080))
        for monitor in MONITORS:
            with Image.open(io.BytesIO(self.frames[monitor.connector])) as frame:
                image.paste(frame, (monitor.x, monitor.y))
        path = Path(tmpdir) / "canvas.png"
        image.save(path)
        return path

    def handles(self):
        return (
            ("python", FakeLegacyScreenCast(self.frames)),
            ("native", native_handle(self.cfg, FakeNativeClient(self.frames))),
            ("gnome-screenshot", None),
        )

    def shoot(self, screencast, region, scale_long_edge: int = 1536):
        with mock.patch.object(capturelib, "_grab_canvas", self.canvas), \
                mock.patch.object(capturelib, "available", return_value=(True, "")):
            return capturelib.capture(
                "all",
                out_dir=self.out,
                scale_long_edge=scale_long_edge,
                include_pointer=False,
                screencast=screencast,
                region=region,
            )

    # ------------------------------------------------------ resolve_region
    def test_the_three_spaces_resolve_to_the_same_global_box(self) -> None:
        box, home = capturelib.resolve_region(2020, 150, 400, 300)
        self.assertEqual((box, home.index), ((2020, 150, 400, 300), 2))
        self.assertEqual(
            capturelib.resolve_region(100, 150, 400, 300, monitor=2)[0], box
        )
        # A downscaled whole-monitor shot: 1920 wide -> 1536, factor 0.8.
        whole = self.shoot(FakeLegacyScreenCast(self.frames), None)[1]
        self.assertAlmostEqual(whole.scale, 0.8)
        from_shot, _ = capturelib.resolve_region(
            80, 120, 320, 240, shot=whole.id, dirs=[self.out]
        )
        self.assertEqual(from_shot, box)

    def test_a_shot_region_is_rounded_outwards(self) -> None:
        whole = self.shoot(FakeLegacyScreenCast(self.frames), None)[1]
        box, _ = capturelib.resolve_region(81, 121, 319, 239, shot=whole.id, dirs=[self.out])
        # 81*1.25 = 101.25 -> 101; (81+319)*1.25 = 500 -> 500 (x); the far
        # edge never shrinks below what was asked for.
        self.assertEqual(box[0], 1920 + 101)
        self.assertGreaterEqual(box[0] + box[2], 1920 + 500)
        self.assertGreaterEqual(box[1] + box[3], 450)

    def test_refusals_name_the_problem(self) -> None:
        whole = self.shoot(FakeLegacyScreenCast(self.frames), None)[1]
        cases = {
            "does not fit inside one monitor": dict(x=1800, y=10, width=300, height=100),
            "at least 16x16": dict(x=10, y=10, width=8, height=100),
            "cannot be negative": dict(x=-5, y=10, width=100, height=100),
            "goes outside": dict(x=1400, y=10, width=300, height=100,
                                   shot=whole.id, dirs=[self.out]),
            "cannot be combined": dict(x=10, y=10, width=100, height=100,
                                       shot=whole.id, monitor=2, dirs=[self.out]),
        }
        for words, kwargs in cases.items():
            with self.subTest(words), self.assertRaises(capturelib.CaptureError) as caught:
                capturelib.resolve_region(**kwargs)
            self.assertIn(words, str(caught.exception))

    # ------------------------------------------------------------- capture
    def test_every_path_crops_exactly_that_region_and_records_it(self) -> None:
        region = capturelib.resolve_region(2020, 150, 400, 300)
        for name, screencast in self.handles():
            with self.subTest(backend=name):
                shots = self.shoot(screencast, region)
                self.assertEqual(len(shots), 1)
                shot = shots[0]
                self.assertTrue(shot.region)
                self.assertEqual(shot.offset, (2020, 150))
                self.assertEqual(shot.desktop_units, (400, 300))
                # Smaller than the long edge: never scaled up, full resolution.
                self.assertEqual((shot.scaled, shot.scale), ((400, 300), 1.0))
                self.assertTrue(shot.id.startswith("m2-"))
                self.assertIn("region", shot.label)
                with Image.open(shot.path) as image:
                    self.assertEqual(image.size, (400, 300))
                    self.assertEqual(image.getpixel((0, 0)), pattern(100, 150))
                    self.assertEqual(image.getpixel((399, 299)), pattern(499, 449))
                record = json.loads((self.out / f"{shot.id}.json").read_text())
                self.assertTrue(record["region"])
                reloaded = capturelib.load_shot(shot.id, [self.out])
                self.assertTrue(reloaded.region)
                self.assertEqual(
                    capturelib.to_global(0, 0, shot=shot.id, dirs=[self.out]),
                    (2020, 150),
                )
                self.assertEqual(
                    capturelib.to_global(399, 299, shot=shot.id, dirs=[self.out]),
                    (2419, 449),
                )
                with self.assertRaises(capturelib.CaptureError):
                    capturelib.to_global(400, 10, shot=shot.id, dirs=[self.out])

    def test_a_large_region_is_scaled_and_still_converts(self) -> None:
        region = capturelib.resolve_region(0, 0, 1920, 1080, monitor=1)
        shot = self.shoot(FakeLegacyScreenCast(self.frames), region, 960)[0]
        self.assertEqual(shot.scaled, (960, 540))
        self.assertEqual(
            capturelib.to_global(480, 270, shot=shot.id, dirs=[self.out]), (960, 540)
        )

    def test_a_window_capture_takes_no_region(self) -> None:
        region = capturelib.resolve_region(10, 10, 100, 100)
        with self.assertRaises(capturelib.CaptureError):
            capturelib.capture(
                "window", out_dir=self.out, scale_long_edge=0,
                include_pointer=False, screencast=None, region=region,
            )

    # ------------------------------------------- GNOME window capture (2.8)
    def window_shot(self, screencast, frames, grab_window=None):
        """`monitor="window"` on GNOME with the extension's answers queued."""
        from pcbridge.desktop import apps as appslib

        answers = list(frames)

        def focused_frame():
            answer = answers.pop(0) if len(answers) > 1 else answers[0]
            if isinstance(answer, Exception):
                raise answer
            return answer

        with mock.patch.object(appslib, "extension_focused_frame", side_effect=focused_frame), \
                mock.patch.object(capturelib, "_grab_window",
                                  grab_window or mock.Mock(side_effect=AssertionError(
                                      "gnome-screenshot ran"))), \
                mock.patch.object(capturelib, "_grab_canvas", self.canvas), \
                mock.patch.object(capturelib, "available", return_value=(True, "")):
            return capturelib.capture(
                "window", out_dir=self.out, scale_long_edge=1536,
                include_pointer=False, screencast=screencast,
            )

    def test_a_gnome_window_is_a_region_of_its_monitor_frame(self) -> None:
        from pcbridge.desktop.apps import ShellWindowFrame

        editor = ShellWindowFrame("org.gnome.TextEditor", "Notes", (2020, 150, 400, 300))
        for name, screencast in self.handles():
            with self.subTest(backend=name):
                shot = self.window_shot(screencast, [editor])[0]
                self.assertTrue(shot.region)
                self.assertEqual(shot.offset, (2020, 150))
                self.assertEqual(shot.desktop_units, (400, 300))
                self.assertTrue(shot.id.startswith("m2-"), shot.id)
                with Image.open(shot.path) as image:
                    self.assertEqual(image.getpixel((0, 0)), pattern(100, 150))
                # Unlike a gnome-screenshot window shot, it converts.
                self.assertEqual(
                    capturelib.to_global(10, 20, shot=shot.id, dirs=[self.out]), (2030, 170))

    def test_the_stage_origin_is_applied_and_the_window_clipped_to_its_monitor(self) -> None:
        from pcbridge.desktop.apps import ShellWindowFrame

        # Mutter's stage starts at x=-1920 here: the left monitor is at -1920.
        shifted = [
            monitorslib.Monitor(index=1, connector="DP-4", x=0, y=0, width=1920, height=1080,
                                scale=1.0, primary=False, name="left",
                                platform_x=-1920, platform_y=0),
            monitorslib.Monitor(index=2, connector="DP-3", x=1920, y=0, width=1920, height=1080,
                                scale=1.0, primary=True, name="right",
                                platform_x=0, platform_y=0),
        ]
        # Centered on the right monitor, hanging 100 px over its left edge.
        hanging = ShellWindowFrame("app", "t", (-100, 50, 800, 400))
        with mock.patch.object(monitorslib, "list_monitors", return_value=shifted):
            shot = self.window_shot(FakeLegacyScreenCast(self.frames), [hanging])[0]
        self.assertEqual(shot.offset, (1920, 50))
        self.assertEqual(shot.desktop_units, (700, 400))

    def test_a_window_that_moved_during_the_capture_is_withdrawn(self) -> None:
        from pcbridge.desktop.apps import ExtensionFrameUnavailable, ShellWindowFrame
        from pcbridge.desktop.errors import DesktopError, ErrorCode

        before = ShellWindowFrame("app", "t", (2020, 150, 400, 300))
        for after in (ShellWindowFrame("app", "t", (2040, 150, 400, 300)),
                      ShellWindowFrame("other", "t", (2020, 150, 400, 300)),
                      None,
                      ExtensionFrameUnavailable("gone", loaded=False)):
            for name, screencast in self.handles():
                with self.subTest(after=after, backend=name):
                    with self.assertRaises(DesktopError) as raised:
                        self.window_shot(screencast, [before, after])
                    self.assertEqual(raised.exception.code, ErrorCode.TARGET_MISMATCH)
                    self.assertEqual(sorted(self.out.iterdir()), [], "a shot was kept")

    def test_no_focused_window_is_said_plainly(self) -> None:
        with self.assertRaises(capturelib.CaptureError) as raised:
            self.window_shot(FakeLegacyScreenCast(self.frames), [None])
        self.assertIn("No focused window", str(raised.exception))

    def test_an_extension_from_before_2_8_keeps_the_screenshot_path(self) -> None:
        from pcbridge.desktop.apps import ExtensionFrameUnavailable

        def grab(tmpdir: Path, include_pointer: bool) -> Path:
            path = Path(tmpdir) / "window.png"
            Image.new("RGB", (640, 480), (1, 2, 3)).save(path)
            return path

        for loaded in (True, False):
            with self.subTest(loaded=loaded):
                old = ExtensionFrameUnavailable("old", loaded=loaded)
                shot = self.window_shot(FakeLegacyScreenCast(self.frames), [old],
                                        grab_window=grab)[0]
                self.assertTrue(shot.id.startswith("win-"), shot.id)
                self.assertEqual(shot.size, (640, 480))

    def test_a_layout_change_between_resolve_and_capture_is_refused(self) -> None:
        region = capturelib.resolve_region(2020, 150, 400, 300)
        moved = [MONITORS[0], monitorslib.Monitor(
            index=2, connector="DP-3", x=1920, y=0, width=2560, height=1440,
            scale=1.0, primary=True, name="right",
        )]
        with mock.patch.object(monitorslib, "list_monitors", return_value=moved):
            with self.assertRaises(capturelib.ShotLayoutChanged):
                self.shoot(FakeLegacyScreenCast(self.frames), region)


@unittest.skipUnless(PIL_OK, "Pillow is required")
class EnhanceTests(unittest.TestCase):
    def dark_scene(self) -> "Image.Image":
        image = Image.new("RGB", (320, 180), (6, 7, 10))
        for i in range(10):
            image.paste((14 + 2 * i, 13, 11), (20 + 28 * i, 120, 40 + 28 * i, 150))
        return image

    def test_a_dark_frame_opens_up_without_a_color_cast(self) -> None:
        dark = self.dark_scene()
        out, stats = capturelib.enhance_image(dark)
        self.assertEqual(out.size, dark.size)
        self.assertLess(stats["gamma"], 1.0)
        self.assertGreater(stats["mean_after"], stats["mean_before"])
        # The blocks were 2 levels apart; now they are visibly apart.
        left = out.getpixel((25, 130))
        right = out.getpixel((20 + 28 * 9 + 5, 130))
        self.assertGreater(sum(right) - sum(left), 60)
        # The near-black background stays near-neutral: no blue channel flare.
        r, g, b = out.getpixel((5, 5))
        self.assertLess(max(r, g, b) - min(r, g, b), 12)

    def test_a_bright_frame_hardly_changes(self) -> None:
        bright = Image.new("RGB", (200, 100), (235, 235, 235))
        bright.paste((15, 15, 15), (0, 0, 100, 50))
        out, stats = capturelib.enhance_image(bright)
        self.assertEqual(stats["gamma"], 1.0)
        before = ImageStat.Stat(bright.convert("L")).mean[0]
        after = ImageStat.Stat(out.convert("L")).mean[0]
        self.assertLess(abs(after - before), 12)

    def test_only_the_copy_sent_to_the_client_changes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "dark.png"
            self.dark_scene().save(path)
            before = path.read_bytes()
            shot = mock.Mock(path=path, scaled=(320, 180), id="m2-abcdef")
            plain = presentationlib.shot_image(shot)
            brighter = presentationlib.shot_image(shot, enhance=True)
            self.assertEqual(path.read_bytes(), before, "the file on disk changed")
        import base64

        self.assertEqual(base64.b64decode(plain.data), before)
        data = base64.b64decode(brighter.data)
        self.assertNotEqual(data, before)
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.size, (320, 180))
            self.assertGreater(
                ImageStat.Stat(image.convert("L")).mean[0],
                ImageStat.Stat(self.dark_scene().convert("L")).mean[0],
            )


if __name__ == "__main__":
    unittest.main()
