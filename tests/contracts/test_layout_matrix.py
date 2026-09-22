#!/usr/bin/env python3
"""Every monitor layout, scale and orientation (Step 6 of 2.0).

`tests/fixtures/native/layout_matrix.json` holds fourteen layouts with
hand-computed tables. Rust resolves the same file in
`rust/crates/pcbridge-core/tests/layout_matrix.rs`; this side also checks
what only Python does: coordinate conversion through `monitor=` and `shot=`,
crop boxes, the picture-size policy, the refusal of gaps, the ambiguous
coordinate guard and the stability of the topology id.

No D-Bus, no compositor, no screen: the monitor table is patched in.
"""

from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.desktop import capture as capturelib  # noqa: E402
from pcbridge.desktop import monitors as monitorslib  # noqa: E402

FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "native" / "layout_matrix.json").read_text(encoding="utf-8")
)
LAYOUTS = FIXTURE["layouts"]
LONG_EDGE = FIXTURE["long_edge"]
MAX_PIXELS = FIXTURE["max_pixels"]


def _copy(value):
    return json.loads(json.dumps(value))


def _shot(mon: monitorslib.Monitor, scaled: tuple[int, int], shot_id: str = "m1-abc123") -> capturelib.Shot:
    size = mon.source_pixel_size
    return capturelib.Shot(
        path=Path("/nonexistent.png"),
        monitor=mon,
        offset=(mon.x, mon.y),
        size=size,
        scaled=scaled,
        scale=scaled[0] / size[0],
        id=shot_id,
        taken_at=time.time(),
        topology="",
        desktop_size=(mon.width, mon.height),
    )


class LayoutMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        # The default cap, whatever another test left behind.
        capturelib.set_max_pixels(MAX_PIXELS)
        self.addCleanup(capturelib.set_max_pixels, capturelib.DEFAULT_MAX_PIXELS)

    def each(self):
        return [(layout, monitorslib.resolve_state(layout["state"])) for layout in LAYOUTS]

    def patched(self, mons):
        return mock.patch.object(monitorslib, "list_monitors", return_value=mons)

    # ------------------------------------------------------------- the table
    def test_the_matrix_covers_the_layouts_step_6_names(self) -> None:
        self.assertGreaterEqual(len(LAYOUTS), 12)
        modes = {layout["state"]["layout_mode"] for layout in LAYOUTS}
        self.assertEqual(modes, {"logical", "physical"})

    def test_every_layout_resolves_to_the_expected_table(self) -> None:
        for layout, mons in self.each():
            with self.subTest(layout=layout["name"]):
                want = layout["expect"]["monitors"]
                self.assertEqual(len(mons), len(want), layout["note"])
                for got, exp in zip(mons, want):
                    self.assertEqual(
                        (got.index, got.connector, got.x, got.y, got.width, got.height,
                         got.transform, got.primary, got.serial, got.platform),
                        (exp["index"], exp["connector"], exp["x"], exp["y"], exp["width"],
                         exp["height"], exp["transform"], exp["primary"], exp["serial"],
                         (exp["platform_x"], exp["platform_y"])),
                    )
                    self.assertAlmostEqual(got.scale, exp["scale"])
                    self.assertAlmostEqual(got.pixel_ratio, exp["pixel_ratio"])
                    self.assertEqual(list(got.source_pixel_size), exp["source_pixel_size"])

    def test_canvas_origin_and_topology(self) -> None:
        for layout, mons in self.each():
            with self.subTest(layout=layout["name"]):
                exp = layout["expect"]
                self.assertEqual(list(monitorslib.canvas_size(mons)), exp["canvas"])
                self.assertEqual(list(monitorslib.platform_origin(mons)), exp["platform_origin"])
                self.assertEqual(monitorslib.topology_id(mons), exp["topology"])

    def test_the_busctl_adapter_reads_the_layout_mode(self) -> None:
        # (serial, monitors, logical monitors, properties) as busctl prints it.
        mode = [0, 3840, 2160, 60.0, 2.0, [1.0, 2.0],
                {"is-current": {"type": "b", "data": True}}]
        wire = [1, [[["DP-1", "V", "P", "S"], [mode], {}]],
                [[0, 0, 2.0, 0, True, [["DP-1", "V", "P", "S"]], {}]]]
        for value, width in ((2, 3840), (1, 1920), (None, 1920)):
            props = {} if value is None else {"layout-mode": {"type": "u", "data": value}}
            mons = monitorslib.resolve_state(monitorslib._mutter_state([*wire, props]))
            self.assertEqual(mons[0].width, width, value)
        with self.assertRaises(monitorslib.MonitorError):
            monitorslib.resolve_state(monitorslib._mutter_state(
                [*wire, {"layout-mode": {"type": "u", "data": 7}}]))

    # ------------------------------------------------- coordinate conversion
    def test_monitor_coordinates_round_trip(self) -> None:
        for layout, mons in self.each():
            with self.subTest(layout=layout["name"]):
                with self.patched(mons):
                    for mon in mons:
                        for lx, ly in ((0, 0), (mon.width - 1, mon.height - 1),
                                       (mon.width // 2, mon.height // 2)):
                            gx, gy = capturelib.to_global(lx, ly, monitor=mon.index)
                            self.assertEqual((gx, gy), (mon.x + lx, mon.y + ly))
                            self.assertEqual(monitorslib.find_monitor(gx, gy, mons), mon)

    def test_shot_coordinates_round_trip_within_one_image_pixel(self) -> None:
        for layout, mons in self.each():
            with self.subTest(layout=layout["name"]):
                for mon, exp in zip(mons, layout["expect"]["monitors"]):
                    shot = _shot(mon, tuple(exp["scaled"]))
                    step_x = math.ceil(mon.width / shot.scaled[0])
                    step_y = math.ceil(mon.height / shot.scaled[1])
                    for px, py in ((mon.x, mon.y), (mon.x + mon.width - 1, mon.y + mon.height - 1),
                                   mon.center, (mon.x + mon.width // 3, mon.y + 2 * mon.height // 3)):
                        ix = (px - mon.x) * shot.scaled[0] // mon.width
                        iy = (py - mon.y) * shot.scaled[1] // mon.height
                        bx, by = shot.to_global(ix, iy)
                        self.assertLessEqual(abs(bx - px), step_x, (px, py, ix, iy))
                        self.assertLessEqual(abs(by - py), step_y, (px, py, ix, iy))
                        self.assertEqual(monitorslib.find_monitor(bx, by, mons), mon)
                    # The last picture pixel still lands on this monitor.
                    last = shot.to_global(shot.scaled[0] - 1, shot.scaled[1] - 1)
                    self.assertEqual(monitorslib.find_monitor(*last, mons), mon)

    def test_crop_boxes_cover_the_whole_frame(self) -> None:
        for layout, mons in self.each():
            with self.subTest(layout=layout["name"]):
                for mon in mons:
                    box = (mon.x, mon.y, mon.width, mon.height)
                    w, h = mon.source_pixel_size
                    self.assertEqual(capturelib._frame_box(mon, (w, h), box), (0, 0, w, h))
                    capturelib.check_source_size(mon, (w, h))
                    with self.assertRaises(capturelib.CaptureError):
                        capturelib.check_source_size(mon, (w + 7, h))

    def test_points_in_gaps_are_refused(self) -> None:
        for layout, mons in self.each():
            with self.subTest(layout=layout["name"]):
                with self.patched(mons):
                    for gx, gy in layout["expect"]["gaps"]:
                        self.assertIsNone(monitorslib.find_monitor(gx, gy, mons))
                        with self.assertRaises(capturelib.CaptureError):
                            capturelib.to_global(gx, gy)

    def test_the_ambiguous_coordinate_guard_fires_on_every_layout(self) -> None:
        for layout, mons in self.each():
            with self.subTest(layout=layout["name"]):
                mon, exp = mons[0], layout["expect"]["monitors"][0]
                if tuple(exp["scaled"]) == mon.source_pixel_size:
                    continue  # not scaled down: no ambiguity to guard against
                tmp = Path(tempfile.mkdtemp(prefix="pcb-matrix-"))
                self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
                shot = _shot(mon, tuple(exp["scaled"]), shot_id="m1-a1b2c3")
                capturelib.save_meta(shot, tmp)
                with self.patched(mons):
                    inside = (exp["scaled"][0] // 2, exp["scaled"][1] // 2)
                    with self.assertRaises(capturelib.CaptureError) as refused:
                        capturelib.to_global(*inside, dirs=[tmp], guard_age=60)
                    self.assertIn("AMBIGUOUS", str(refused.exception))
                    # Saying which space was meant goes through.
                    back = capturelib.to_global(*inside, shot="m1-a1b2c3", dirs=[tmp])
                    self.assertEqual(monitorslib.find_monitor(*back, mons), mon)

    # -------------------------------------------------------- picture size
    def test_picture_sizes_follow_the_policy(self) -> None:
        for layout, mons in self.each():
            with self.subTest(layout=layout["name"]):
                for mon, exp in zip(mons, layout["expect"]["monitors"]):
                    w, h = mon.source_pixel_size
                    got = capturelib._scaled_size(w, h, LONG_EDGE)
                    self.assertEqual(list(got), exp["scaled"], mon.connector)
                    self.assertLessEqual(max(got), LONG_EDGE)
                    self.assertLessEqual(got[0] * got[1], MAX_PIXELS * 1.002)
                    # Full resolution is never capped: OCR reads it.
                    self.assertEqual(capturelib._scaled_size(w, h, 0), (w, h))

    def test_the_policy_keeps_this_machines_pictures(self) -> None:
        self.assertEqual(capturelib._scaled_size(1920, 1080, 1536), (1536, 864))
        # No cap: a 1.x config reads exactly as before.
        self.assertEqual(capturelib._scaled_size(2880, 1800, 1536, max_pixels=0), (1536, 960))
        self.assertEqual(capturelib._scaled_size(2880, 1800, 1536), (1457, 911))

    def test_small_text_is_flagged_only_where_it_shrinks(self) -> None:
        for layout, mons in self.each():
            with self.subTest(layout=layout["name"]):
                for mon, exp in zip(mons, layout["expect"]["monitors"]):
                    note = capturelib.legibility_note(_shot(mon, tuple(exp["scaled"])))
                    self.assertEqual(note == "", exp["legible"], (mon.connector, note))
                    if note:
                        self.assertIn("region=", note)

    # --------------------------------------------------------- topology id
    def test_a_moved_or_renamed_layout_keeps_its_id(self) -> None:
        for layout, mons in self.each():
            with self.subTest(layout=layout["name"]):
                state = _copy(layout["state"])
                for logical in state["logical"]:
                    logical["x"] += 500
                    logical["y"] -= 300
                renamed = {p["connector"]: p["connector"] + "-X" for p in state["physical"]}
                for physical in state["physical"]:
                    physical["connector"] = renamed[physical["connector"]]
                for logical in state["logical"]:
                    logical["connectors"] = [renamed[c] for c in logical["connectors"]]
                self.assertEqual(
                    monitorslib.topology_id(monitorslib.resolve_state(state)),
                    layout["expect"]["topology"],
                )

    def test_a_scale_or_mode_change_changes_the_id(self) -> None:
        for layout, mons in self.each():
            with self.subTest(layout=layout["name"]):
                state = _copy(layout["state"])
                state["logical"][0]["scale"] = 3.0 if state["logical"][0]["scale"] != 3.0 else 1.0
                self.assertNotEqual(
                    monitorslib.topology_id(monitorslib.resolve_state(state)),
                    layout["expect"]["topology"],
                )
                flipped = _copy(layout["state"])
                flipped["layout_mode"] = (
                    "logical" if flipped["layout_mode"] == "physical" else "physical"
                )
                if all(lm["scale"] == 1.0 for lm in flipped["logical"]):
                    # At scale 1 the two modes map identically: same id on purpose.
                    self.assertEqual(
                        monitorslib.topology_id(monitorslib.resolve_state(flipped)),
                        layout["expect"]["topology"],
                    )
                else:
                    self.assertNotEqual(
                        monitorslib.topology_id(monitorslib.resolve_state(flipped)),
                        layout["expect"]["topology"],
                    )


if __name__ == "__main__":
    unittest.main()
