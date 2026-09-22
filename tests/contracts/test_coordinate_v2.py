#!/usr/bin/env python3
"""Coordinate contract v2: mixed scale, negative origin, gaps (Task 7.1).

Both languages read `tests/fixtures/native/mixed_scale_cases.json`: this file
checks the Python side (`monitors.resolve_state`, `capture.Shot`,
`capture.to_global`), `rust/crates/pcbridge-core/tests/geometry.rs` checks the
same layouts in Rust. The expected values in the fixture were computed by
hand, not taken from either implementation.

This machine runs both monitors at scale 1.0, so the scaled layouts here are
NOT measured hardware; the rules are written down and pinned instead. No
D-Bus call, no screenshot, no input. Safe with every live flag unset.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.desktop import capture as capturelib  # noqa: E402
from pcbridge.desktop import monitors as monitorslib  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "native" / "mixed_scale_cases.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))
LAYOUTS = {case["name"]: case for case in CASES["layouts"]}


def table(name: str) -> list[monitorslib.Monitor]:
    return monitorslib.resolve_state(LAYOUTS[name]["state"])


def shot(record: dict, monitor: monitorslib.Monitor | None = None) -> capturelib.Shot:
    """A `Shot` as `load_shot` would rebuild it from that record."""
    size = tuple(record.get("source_pixel_size") or record["size"])
    desktop = record.get("desktop_size")
    return capturelib.Shot(
        path=Path("/tmp/none.png"),
        monitor=monitor,
        offset=tuple(record["offset"]),
        size=size,
        scaled=tuple(record["scaled"]),
        scale=float(record["scale"]),
        id="m1-abc123",
        desktop_size=tuple(desktop) if desktop else None,
    )


class MonitorTableTests(unittest.TestCase):
    def test_every_layout_resolves_to_the_expected_table(self) -> None:
        for name, case in LAYOUTS.items():
            with self.subTest(layout=name):
                got = table(name)
                want = case["expect"]["monitors"]
                self.assertEqual(len(got), len(want), case["note"])
                for monitor, expected in zip(got, want):
                    self.assertEqual(monitor.index, expected["index"])
                    self.assertEqual(monitor.connector, expected["connector"])
                    self.assertEqual((monitor.x, monitor.y),
                                     (expected["x"], expected["y"]))
                    self.assertEqual((monitor.width, monitor.height),
                                     (expected["width"], expected["height"]))
                    self.assertAlmostEqual(monitor.scale, expected["scale"])
                    self.assertEqual(monitor.transform, expected["transform"])
                    self.assertEqual(monitor.primary, expected["primary"])
                    self.assertEqual(monitor.platform,
                                     (expected["platform_x"], expected["platform_y"]))
                    self.assertEqual(list(monitor.source_pixel_size),
                                     expected["source_pixel_size"])

    def test_canvas_and_platform_origin_are_separate_spaces(self) -> None:
        for name, case in LAYOUTS.items():
            with self.subTest(layout=name):
                got = table(name)
                self.assertEqual(list(monitorslib.canvas_size(got)),
                                 case["expect"]["canvas"])
                self.assertEqual(list(monitorslib.platform_origin(got)),
                                 case["expect"]["platform_origin"])
                # Whatever the compositor reported, the canvas starts at (0, 0):
                # a negative canvas coordinate is unreachable for the absolute
                # pointer axis and crops outside the captured image.
                self.assertEqual(min(m.x for m in got), 0)
                self.assertEqual(min(m.y for m in got), 0)

    def test_canvas_size_is_the_bounding_box_of_whatever_it_is_given(self) -> None:
        # `list_monitors` always hands over a normalized table, but this is a
        # public helper and a hand-built table may still carry the
        # compositor's own origin.
        raw = [
            monitorslib.Monitor(1, "A", -1920, -100, 1920, 1080, 1.0, False),
            monitorslib.Monitor(2, "B", 0, 0, 1920, 1080, 1.0, True),
        ]
        self.assertEqual(monitorslib.canvas_size(raw), (3840, 1180))

    def test_topology_matches_the_shared_string(self) -> None:
        for name, case in LAYOUTS.items():
            with self.subTest(layout=name):
                self.assertEqual(monitorslib.topology_id(table(name)),
                                 case["expect"]["topology"])

    def test_a_shifted_layout_is_the_same_layout(self) -> None:
        # The same two monitors, the compositor's origin 1920 units to the
        # left: nothing moved on screen, so no shot may be invalidated.
        self.assertEqual(
            monitorslib.topology_id(table("this_machine_two_equal_monitors")),
            monitorslib.topology_id(table("negative_origin_is_normalized")),
        )

    def test_a_scaled_monitor_keeps_its_pixel_size(self) -> None:
        eDP, DP = table("hidpi_two_times_and_rotated_portrait")
        self.assertEqual((eDP.width, eDP.height), (1440, 900))
        self.assertEqual(eDP.source_pixel_size, (2880, 1800))
        # A rotation swaps the mode axes; the scale is still 1 here.
        self.assertEqual((DP.width, DP.height), (1080, 1920))
        self.assertEqual(DP.source_pixel_size, (1080, 1920))


class ShotTransformTests(unittest.TestCase):
    def test_every_image_point_maps_to_the_expected_desktop_point(self) -> None:
        for case in CASES["coordinates"]:
            with self.subTest(case=case["name"]):
                point = shot(case["record"]).to_global(*case["image"])
                self.assertEqual(list(point), case["desktop"])

    def test_a_record_without_the_new_fields_keeps_the_old_answer(self) -> None:
        legacy = next(c for c in CASES["coordinates"]
                      if c["name"] == "legacy_v1_record_without_the_new_fields")
        record = shot(legacy["record"])
        self.assertIsNone(record.desktop_size)
        self.assertEqual(record.desktop_units, record.size)
        self.assertEqual(list(record.to_global(*legacy["image"])), legacy["desktop"])

    def test_the_round_trip_stays_within_one_desktop_unit(self) -> None:
        tolerance = CASES["round_trip"]["tolerance_desktop_units"]
        for case in CASES["round_trip"]["cases"]:
            record = shot(case["record"])
            ox, oy = record.offset
            dw, dh = record.desktop_units
            sw, sh = record.scaled
            for want in case["points"]:
                with self.subTest(case=case["name"], point=want):
                    # Desktop unit -> image pixel, the way an agent reads a
                    # coordinate off the picture it was shown.
                    image = (
                        monitorslib.round_half_away((want[0] - ox) * sw / dw),
                        monitorslib.round_half_away((want[1] - oy) * sh / dh),
                    )
                    got = record.to_global(*image)
                    self.assertLessEqual(abs(got[0] - want[0]), tolerance)
                    self.assertLessEqual(abs(got[1] - want[1]), tolerance)

    def test_both_axes_are_converted_on_their_own(self) -> None:
        # One ratio for both axes put the long edge a pixel off when the crop
        # did not divide evenly.
        record = shot({"offset": [0, 0], "source_pixel_size": [1000, 625],
                       "desktop_size": [1000, 625], "scaled": [800, 501],
                       "scale": 0.8})
        self.assertNotAlmostEqual(record.scale_xy[0], record.scale_xy[1])
        self.assertEqual(record.to_global(800, 501), (1000, 625))
        # The compatibility field stays the x ratio.
        self.assertAlmostEqual(record.scale, record.scale_xy[0])


class LoadedRecordTests(unittest.TestCase):
    def record(self, tmp: Path, name: str, data: dict) -> capturelib.Shot:
        (tmp / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
        return capturelib.load_shot(name, [tmp])

    def test_the_loaded_monitor_box_is_in_desktop_units(self) -> None:
        # `offset` and the monitor box are used together, so both have to be
        # in the same space. On a scaled monitor `size` is raw pixels.
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            shot = self.record(tmp, "m1-abc123", {
                "id": "m1-abc123", "png": str(tmp / "x.png"), "monitor": 1,
                "connector": "eDP-1", "primary": True, "offset": [0, 0],
                "size": [2880, 1800], "scaled": [1536, 960], "scale": 0.5333333333333333,
                "taken_at": 0, "topology_id": "",
                "source_pixel_size": [2880, 1800], "desktop_size": [1440, 900],
            })
            self.assertEqual((shot.monitor.width, shot.monitor.height), (1440, 900))
            self.assertAlmostEqual(shot.monitor.scale, 2.0)
            self.assertEqual(shot.to_global(768, 480), (720, 450))

            legacy = self.record(tmp, "m2-abc123", {
                "id": "m2-abc123", "png": str(tmp / "y.png"), "monitor": 2,
                "connector": "DP-1", "primary": False, "offset": [1920, 0],
                "size": [1920, 1080], "scaled": [1280, 720],
                "scale": 0.6666666666666666, "taken_at": 0,
            })
            self.assertEqual((legacy.monitor.width, legacy.monitor.height), (1920, 1080))
            self.assertAlmostEqual(legacy.monitor.scale, 1.0)
            self.assertEqual(legacy.to_global(640, 360), (2880, 540))


class RefusalTests(unittest.TestCase):
    def convert(self, layout: str, x: int, y: int) -> tuple[int, int]:
        with mock.patch.object(monitorslib, "list_monitors",
                               return_value=table(layout)):
            return capturelib.to_global(x, y)

    def test_a_point_on_no_monitor_is_refused(self) -> None:
        for case in CASES["rejected_points"]:
            with self.subTest(case=case["name"]):
                with self.assertRaises(capturelib.CaptureError) as caught:
                    self.convert(case["layout"], *case["global"])
                self.assertIn("is not on any monitor", str(caught.exception))

    def test_a_point_on_a_monitor_passes(self) -> None:
        self.assertEqual(self.convert("gap_and_step_between_monitors", 10, 10), (10, 10))
        self.assertEqual(
            self.convert("gap_and_step_between_monitors", 2100, 200), (2100, 200)
        )

    def test_an_unreadable_monitor_table_does_not_refuse(self) -> None:
        # Refusing because something could not be checked would break a call
        # that works.
        with mock.patch.object(monitorslib, "list_monitors",
                               side_effect=monitorslib.MonitorError("yok")):
            self.assertEqual(capturelib.to_global(9999, 9999), (9999, 9999))

    def test_a_point_outside_the_image_is_refused(self) -> None:
        record = shot(CASES["coordinates"][0]["record"])
        with (
            mock.patch.object(capturelib, "load_shot", return_value=record),
            mock.patch.object(monitorslib, "list_monitors",
                              return_value=table("this_machine_two_equal_monitors")),
            self.assertRaises(capturelib.CaptureError) as caught,
        ):
            capturelib.to_global(1600, 100, shot="m2-a1b2c3", dirs=[Path("/tmp")])
        self.assertIn("is outside the", str(caught.exception))

    def test_a_point_inside_the_image_still_converts(self) -> None:
        case = CASES["coordinates"][0]
        record = shot(case["record"])
        with (
            mock.patch.object(capturelib, "load_shot", return_value=record),
            mock.patch.object(monitorslib, "list_monitors",
                              return_value=table(case["layout"])),
        ):
            point = capturelib.to_global(*case["image"], shot="m2-a1b2c3",
                                         dirs=[Path("/tmp")])
        self.assertEqual(list(point), case["desktop"])


class SourceSizeTests(unittest.TestCase):
    def test_a_frame_may_be_logical_or_raw_pixels(self) -> None:
        monitor = table("hidpi_two_times_and_rotated_portrait")[0]
        capturelib.check_source_size(monitor, (1440, 900))   # logical units
        capturelib.check_source_size(monitor, (2880, 1800))  # raw pixels
        with self.assertRaises(capturelib.CaptureError) as caught:
            capturelib.check_source_size(monitor, (1920, 1080))
        self.assertIn("1440x900", str(caught.exception))
        self.assertIn("2880x1800", str(caught.exception))

    def test_the_canvas_ratio_is_resolved_or_refused(self) -> None:
        for case in CASES["canvas_ratio"]:
            with self.subTest(case=case["name"]):
                if "state" in case:
                    mons = monitorslib.resolve_state(case["state"])
                else:
                    mons = table(case["layout"])
                if case["ratio"] is None:
                    with self.assertRaises(capturelib.CaptureError) as caught:
                        capturelib.canvas_pixel_ratio(mons, tuple(case["canvas"]))
                    self.assertIn("different scales", str(caught.exception))
                else:
                    self.assertAlmostEqual(
                        capturelib.canvas_pixel_ratio(mons, tuple(case["canvas"])),
                        case["ratio"],
                    )

    def test_the_record_carries_both_sizes_and_the_space(self) -> None:
        monitor = table("hidpi_two_times_and_rotated_portrait")[0]
        record = capturelib.Shot(
            path=Path("/tmp/x.png"), monitor=monitor, offset=(monitor.x, monitor.y),
            size=monitor.source_pixel_size, scaled=(1536, 960), scale=0.5333333333333333,
            id="m1-abc123", desktop_size=(monitor.width, monitor.height),
        )
        meta = record.meta()
        self.assertEqual(meta["source_pixel_size"], [2880, 1800])
        self.assertEqual(meta["desktop_size"], [1440, 900])
        self.assertEqual(meta["size"], [2880, 1800])
        self.assertEqual(meta["coordinate_space"], capturelib.COORDINATE_SPACE)
        self.assertAlmostEqual(meta["scale_xy"][0], 1536 / 2880)
        self.assertAlmostEqual(meta["scale_xy"][1], 960 / 1800)


if __name__ == "__main__":
    unittest.main(verbosity=2)
