#!/usr/bin/env python3
"""Screenshot artifacts are published whole, never half, and never over another.

`capture.py` used to write each monitor's PNG and record the moment that one
monitor was ready. Three things could go wrong with that, all silently:

1. In an `all` capture a failure on the second monitor left the first one's
   image and record behind -- an artifact nobody got, with an id that `shot=`
   would still resolve.
2. A shot id collision replaced another shot's record. A later `shot=` click
   would then go through the wrong offset and scale.
3. A capture killed half-way left a full-resolution screenshot in a place no
   sweep ever looked at.

And one about what leaves the server: the image block handed to a client must
be the published PNG, with the size its record claims, or the call says so.

Offline: the frames come from a fake handle, the monitor table is a fixture,
nothing touches the compositor. Safe with every live flag unset.
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.cli import shot as shot_cli  # noqa: E402
from pcbridge.desktop import capture as capturelib  # noqa: E402
from pcbridge.desktop import monitors as monitorslib  # noqa: E402
from pcbridge.desktop import presentation as presentationlib  # noqa: E402
from pcbridge.desktop.errors import DesktopError, ErrorCategory, ErrorCode  # noqa: E402
from pcbridge.shots import ShotStore  # noqa: E402


MONITORS = monitorslib._ordered(
    [
        monitorslib.Monitor(
            index=0, connector="DP-3", x=160, y=0, width=160, height=100,
            scale=1.0, primary=True,
        ),
        monitorslib.Monitor(
            index=0, connector="DP-4", x=0, y=0, width=160, height=100,
            scale=1.0, primary=False,
        ),
    ]
)
COLORS = {"DP-4": (220, 40, 30), "DP-3": (30, 80, 220)}
FIXED_STAMP = "20260913-010203"


class FakeScreenCast:
    """The handle shape `capture.py` consumes, answering with solid colors."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.calls: list[str] = []

    def is_open(self) -> bool:
        return True

    def ensure_cursor(self, cursor: bool) -> bool:
        return False

    def capture(self, connector: str, path: Path) -> dict:
        self.calls.append(connector)
        if connector == self.fail_on:
            raise capturelib.CaptureError(f"{connector}: frame timeout")
        monitor = next(m for m in MONITORS if m.connector == connector)
        Image.new("RGB", (monitor.width, monitor.height), COLORS[connector]).save(
            path, format="PNG"
        )
        return {"ok": True, "taken_at": time.time()}


def run_capture(out_dir: Path, *, screencast=None, spec="all", **kwargs):
    with mock.patch.object(monitorslib, "list_monitors", return_value=MONITORS):
        return capturelib.capture(
            spec,
            out_dir=out_dir,
            scale_long_edge=80,
            include_pointer=False,
            screencast=screencast if screencast is not None else FakeScreenCast(),
            **kwargs,
        )


def entries(directory: Path) -> list[str]:
    """Everything in the directory, hidden names included."""
    if not directory.exists():
        return []
    return sorted(path.name for path in directory.iterdir())


def canvas(directory: Path, _include_pointer: bool) -> Path:
    """A gnome-screenshot style full canvas for the fixture layout."""
    path = Path(directory) / "canvas.png"
    image = Image.new("RGB", (320, 100))
    for monitor in MONITORS:
        image.paste(COLORS[monitor.connector], monitor.bbox)
    image.save(path, format="PNG")
    return path


class PublishWhole(unittest.TestCase):
    def test_a_complete_capture_publishes_every_image_and_record(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            out = Path(raw) / "shots"
            shots = run_capture(out)

            self.assertEqual([shot.monitor.connector for shot in shots], ["DP-4", "DP-3"])
            expected = sorted(
                [shot.path.name for shot in shots] + [f"{shot.id}.json" for shot in shots]
            )
            self.assertEqual(entries(out), expected, "nothing else may be left behind")
            for shot in shots:
                record = json.loads((out / f"{shot.id}.json").read_text(encoding="utf-8"))
                self.assertEqual(record["png"], str(shot.path))
                self.assertTrue(Path(record["png"]).is_absolute())
                with Image.open(shot.path) as image:
                    self.assertEqual(image.size, shot.scaled)

    def test_a_failure_on_a_later_monitor_leaves_nothing_behind(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            out = Path(raw) / "shots"
            handle = FakeScreenCast(fail_on="DP-3")
            with self.assertRaises(capturelib.CaptureError):
                run_capture(out, screencast=handle)

            # Without this the test would pass even if nothing had been rendered.
            self.assertEqual(handle.calls, ["DP-4", "DP-3"])
            self.assertEqual(entries(out), [])

    def test_a_write_failure_in_the_screenshot_path_leaves_nothing_behind(self) -> None:
        real_write = capturelib._write_crop
        calls = {"count": 0}

        def failing_write(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError(28, "No space left on device")
            return real_write(*args, **kwargs)

        with tempfile.TemporaryDirectory() as raw:
            out = Path(raw) / "shots"

            class Closed:
                def is_open(self) -> bool:
                    return False

            with (
                mock.patch.object(capturelib, "available", return_value=(True, "")),
                mock.patch.object(capturelib, "_grab_canvas", side_effect=canvas),
                mock.patch.object(capturelib, "_write_crop", side_effect=failing_write),
                self.assertRaises(capturelib.CaptureError),
            ):
                run_capture(out, screencast=Closed())

            self.assertEqual(calls["count"], 2)
            self.assertEqual(entries(out), [])

    def test_a_window_capture_is_published_the_same_way(self) -> None:
        def window(directory: Path, _include_pointer: bool) -> Path:
            path = Path(directory) / "window.png"
            Image.new("RGB", (120, 90), (10, 200, 10)).save(path, format="PNG")
            return path

        with tempfile.TemporaryDirectory() as raw:
            out = Path(raw) / "shots"
            with (
                mock.patch.object(capturelib, "available", return_value=(True, "")),
                mock.patch.object(capturelib, "_grab_window", side_effect=window),
            ):
                shots = run_capture(out, spec="window")

            self.assertEqual(len(shots), 1)
            self.assertRegex(shots[0].id, r"^win-[0-9a-f]{6}$")
            self.assertEqual(
                entries(out), sorted([shots[0].path.name, f"{shots[0].id}.json"])
            )


class NeverOverwrite(unittest.TestCase):
    def test_an_id_used_in_any_lookup_directory_is_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            out = root / "mcp"
            other = root / "agent"
            other.mkdir()
            sentinel = other / "m1-aaaaaa.json"
            sentinel.write_text('{"id": "m1-aaaaaa", "sentinel": true}', encoding="utf-8")

            with mock.patch.object(
                capturelib.secrets, "token_hex", side_effect=["aaaaaa", "bbbbbb"]
            ):
                shots = run_capture(out, reserved_dirs=[out, other])

            self.assertEqual([shot.id for shot in shots], ["m1-bbbbbb", "m2-bbbbbb"])
            self.assertEqual(
                sentinel.read_text(encoding="utf-8"),
                '{"id": "m1-aaaaaa", "sentinel": true}',
            )
            self.assertFalse((out / "m1-aaaaaa.json").exists())

    def test_an_existing_image_file_is_never_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            out = Path(raw) / "shots"
            out.mkdir()
            occupied = out / f"{FIXED_STAMP}-aaaaaa-m1-DP-4.png"
            occupied.write_bytes(b"someone else's picture")

            with (
                mock.patch.object(
                    capturelib.secrets, "token_hex", side_effect=["aaaaaa", "cccccc"]
                ),
                mock.patch.object(capturelib.time, "strftime", return_value=FIXED_STAMP),
            ):
                shots = run_capture(out)

            self.assertEqual([shot.id for shot in shots], ["m1-cccccc", "m2-cccccc"])
            self.assertEqual(occupied.read_bytes(), b"someone else's picture")

    def test_a_file_that_appears_during_publishing_is_not_replaced(self) -> None:
        """Another process wins the race between the check and the publish."""
        real_link = os.link
        calls = {"count": 0}

        def racing_link(source, destination, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                Path(destination).write_bytes(b"the other process")
                raise FileExistsError(17, "File exists", str(destination))
            return real_link(source, destination, *args, **kwargs)

        with tempfile.TemporaryDirectory() as raw:
            out = Path(raw) / "shots"
            with (
                mock.patch.object(
                    capturelib.secrets, "token_hex", side_effect=["aaaaaa", "dddddd"]
                ),
                mock.patch.object(capturelib.time, "strftime", return_value=FIXED_STAMP),
                mock.patch.object(capturelib.os, "link", side_effect=racing_link),
            ):
                shots = run_capture(out)

            self.assertEqual([shot.id for shot in shots], ["m1-dddddd", "m2-dddddd"])
            winner = out / f"{FIXED_STAMP}-aaaaaa-m2-DP-3.png"
            self.assertEqual(winner.read_bytes(), b"the other process")
            expected = sorted(
                [shot.path.name for shot in shots]
                + [f"{shot.id}.json" for shot in shots]
                + [winner.name]
            )
            # The first attempt's own monitor-1 image was withdrawn, not kept.
            self.assertEqual(entries(out), expected)

    def test_a_record_copy_goes_out_with_the_capture_and_avoids_its_ids(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            custom = root / "custom"
            default = root / "default"
            default.mkdir()
            (default / "m2-aaaaaa.json").write_text("{}", encoding="utf-8")

            with mock.patch.object(
                capturelib.secrets, "token_hex", side_effect=["aaaaaa", "eeeeee"]
            ):
                shots = run_capture(custom, copy_meta_to=[default])

            for shot in shots:
                self.assertTrue(shot.id.endswith("eeeeee"))
                copy = json.loads((default / f"{shot.id}.json").read_text(encoding="utf-8"))
                self.assertEqual(copy["png"], str(shot.path))
                self.assertEqual(copy["png"], str((custom / shot.path.name).resolve()))
            self.assertEqual((default / "m2-aaaaaa.json").read_text(encoding="utf-8"), "{}")

    @unittest.skipIf(os.geteuid() == 0, "root ignores directory permissions")
    def test_a_record_copy_that_cannot_be_written_withdraws_the_capture(self) -> None:
        """Images and records were already out when the copy failed."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            out = root / "custom"
            read_only = root / "default"
            read_only.mkdir()
            read_only.chmod(0o500)
            try:
                with self.assertRaises(capturelib.CaptureError):
                    run_capture(out, copy_meta_to=[read_only])
            finally:
                read_only.chmod(0o700)

            self.assertEqual(entries(out), [])
            self.assertEqual(entries(read_only), [])

    def test_a_copy_directory_that_is_a_file_is_refused_before_capturing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            out = root / "custom"
            blocker = root / "not-a-directory"
            blocker.write_text("x", encoding="utf-8")
            handle = FakeScreenCast()

            with self.assertRaises(capturelib.CaptureError):
                run_capture(out, screencast=handle, copy_meta_to=[blocker / "shots"])

            self.assertEqual(handle.calls, [], "no frame is read for an unusable target")
            self.assertEqual(entries(out), [])


class SweepAbandonedStaging(unittest.TestCase):
    @staticmethod
    def _staging(directory: Path, name: str, age_seconds: float) -> Path:
        path = directory / name
        path.mkdir(parents=True)
        (path / "frame.png").write_bytes(b"\x89PNG full resolution")
        moment = time.time() - age_seconds
        os.utime(path / "frame.png", (moment, moment))
        os.utime(path, (moment, moment))
        return path

    def test_the_mcp_store_sweeps_what_a_killed_capture_left(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cfg = SimpleNamespace(
                state_dir=root,
                public_url="https://example.invalid",
                # 0 keeps finished shots forever; an abandoned staging
                # directory is not a finished shot and still goes.
                desktop=SimpleNamespace(shot_ttl_seconds=300, shot_keep_hours=0),
            )
            shots_dir = root / "shots"
            old = self._staging(shots_dir, f"{capturelib.STAGING_PREFIX}old", 3600)
            fresh = self._staging(shots_dir, f"{capturelib.STAGING_PREFIX}fresh", 5)

            ShotStore(cfg).sweep()

            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists(), "a capture still running must keep its files")

    def test_the_cli_sweeps_what_a_killed_capture_left(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            old = self._staging(directory, f"{capturelib.STAGING_PREFIX}old", 3600)
            fresh = self._staging(directory, f"{capturelib.STAGING_PREFIX}fresh", 5)
            unrelated = directory / ".not-ours"
            unrelated.mkdir()
            os.utime(unrelated, (time.time() - 7200, time.time() - 7200))

            shot_cli.sweep(directory, 0)

            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(unrelated.exists(), "only our own staging names are swept")


class DeliveredImage(unittest.TestCase):
    def test_the_delivered_block_is_the_published_png_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            shots = run_capture(Path(raw) / "shots")
            for shot in shots:
                block = presentationlib.shot_image(shot)
                self.assertEqual(block.type, "image")
                self.assertEqual(block.mimeType, "image/png")
                data = base64.b64decode(block.data)
                self.assertEqual(data, shot.path.read_bytes())
                with Image.open(io.BytesIO(data)) as image:
                    self.assertEqual(image.size, shot.scaled)
                    center = image.convert("RGB").getpixel(
                        (image.width // 2, image.height // 2)
                    )
                self.assertEqual(center, COLORS[shot.monitor.connector])

    def test_an_image_that_disagrees_with_its_record_is_a_delivery_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            shot = run_capture(Path(raw) / "shots", spec=1)[0]
            Image.new("RGB", (40, 25), (0, 0, 0)).save(shot.path, format="PNG")

            with self.assertRaises(DesktopError) as raised:
                presentationlib.shot_image(shot)

            self.assertEqual(raised.exception.code, ErrorCode.IMAGE_DELIVERY_FAILED)
            self.assertEqual(raised.exception.category, ErrorCategory.CAPTURE)
            self.assertIn(shot.id, raised.exception.message)

    def test_a_corrupt_or_missing_image_is_a_delivery_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            corrupt, missing = run_capture(Path(raw) / "shots")
            corrupt.path.write_bytes(b"not a png at all, just bytes")
            missing.path.unlink()

            for shot in (corrupt, missing):
                with self.subTest(shot=shot.id), self.assertRaises(DesktopError) as raised:
                    presentationlib.shot_image(shot)
                self.assertEqual(raised.exception.code, ErrorCode.IMAGE_DELIVERY_FAILED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
