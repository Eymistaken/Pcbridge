#!/usr/bin/env python3
"""Provider-neutral monitor, shot, stale, and ambiguity contracts."""

from __future__ import annotations

import json
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


FIXTURE = ROOT / "tests" / "fixtures" / "native" / "coordinate_cases.json"


def make_monitors(data: dict) -> list[monitorslib.Monitor]:
    return monitorslib._ordered(
        [
            monitorslib.Monitor(
                index=0,
                connector=item["connector"],
                x=item["x"],
                y=item["y"],
                width=item["width"],
                height=item["height"],
                scale=item["scale"],
                primary=item["primary"],
            )
            for item in data["displays"]
        ]
    )


class CoordinateContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.monitors = make_monitors(cls.data)

    def test_display_order_geometry_and_monitor_coordinates(self) -> None:
        display_cases = json.loads(
            (FIXTURE.parent / "display_cases.json").read_text(encoding="utf-8")
        )
        for case in display_cases["cases"]:
            monitors = make_monitors(case)
            with self.subTest(case=case["name"]):
                self.assertEqual(
                    [monitor.connector for monitor in monitors], case["expected_order"]
                )
                self.assertEqual(
                    monitorslib.canvas_size(monitors), tuple(case["expected_canvas"])
                )

        with mock.patch.object(
            monitorslib, "list_monitors", return_value=self.monitors
        ):
            for case in self.data["monitor_points"]:
                with self.subTest(point=case):
                    actual = capturelib.to_global(
                        case["x"], case["y"], monitor=case["monitor"]
                    )
                    self.assertEqual(actual, tuple(case["expected"]))

    def _save_shot(self, directory: Path, age: float, scale: float | None = None) -> None:
        data = self.data["shot"]
        monitor = self.monitors[1]
        shot = capturelib.Shot(
            path=directory / "fixture.png",
            monitor=monitor,
            offset=tuple(data["offset"]),
            size=tuple(data["size"]),
            scaled=tuple(data["scaled"]),
            scale=data["scale"] if scale is None else scale,
            id=data["id"],
            taken_at=time.time() - age,
        )
        capturelib.save_meta(shot, directory)

    def test_shot_lookup_and_server_side_coordinate_conversion(self) -> None:
        shot = self.data["shot"]
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            self._save_shot(directory, age=0)
            actual = capturelib.to_global(
                *shot["point"], shot=shot["id"], dirs=[directory]
            )
            self.assertEqual(actual, tuple(shot["expected"]))
            restored = capturelib.load_shot(shot["id"], [directory])
            self.assertEqual(restored.monitor.connector, shot["connector"])

            for invalid in self.data["invalid_shot_ids"]:
                with self.subTest(invalid=invalid), self.assertRaises(capturelib.CaptureError):
                    capturelib.load_shot(invalid, [directory])

    def test_recent_scaled_shot_is_ambiguous_but_stale_or_unscaled_is_not(self) -> None:
        guard = self.data["guard"]
        point = guard["ambiguous_point"]
        with tempfile.TemporaryDirectory() as raw, mock.patch.object(
            monitorslib, "list_monitors", return_value=self.monitors
        ):
            directory = Path(raw)
            self._save_shot(directory, age=guard["recent_age_seconds"])
            with self.assertRaisesRegex(capturelib.CaptureError, "AMBIGUOUS"):
                capturelib.to_global(
                    *point,
                    dirs=[directory],
                    guard_age=guard["max_age_seconds"],
                )

            (directory / f"{self.data['shot']['id']}.json").unlink()
            self._save_shot(directory, age=guard["stale_age_seconds"])
            self.assertEqual(
                capturelib.to_global(
                    *point,
                    dirs=[directory],
                    guard_age=guard["max_age_seconds"],
                ),
                tuple(guard["passthrough"]),
            )

            (directory / f"{self.data['shot']['id']}.json").unlink()
            self._save_shot(directory, age=0, scale=1.0)
            self.assertEqual(
                capturelib.to_global(
                    *point,
                    dirs=[directory],
                    guard_age=guard["max_age_seconds"],
                ),
                tuple(guard["passthrough"]),
            )


if __name__ == "__main__":
    unittest.main()
