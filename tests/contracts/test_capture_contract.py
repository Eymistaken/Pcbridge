#!/usr/bin/env python3
"""Offline capture and CLI artifact contracts shared by future providers."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.cli import shot as shot_cli  # noqa: E402
from pcbridge.config import DesktopSpec  # noqa: E402
from pcbridge.desktop import capture as capturelib  # noqa: E402
from pcbridge.desktop import monitors as monitorslib  # noqa: E402


FIXTURES = ROOT / "tests" / "fixtures" / "native"


def load_display_case(name: str) -> dict:
    data = json.loads((FIXTURES / "display_cases.json").read_text(encoding="utf-8"))
    return next(case for case in data["cases"] if case["name"] == name)


def make_monitors(case: dict) -> list[monitorslib.Monitor]:
    monitors = [
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
        for item in case["displays"]
    ]
    return monitorslib._ordered(monitors)


class PythonCaptureProvider:
    """Adapter that lets the legacy backend consume provider-neutral fixtures."""

    def __init__(self, case: dict) -> None:
        self.case = case
        self.monitors = make_monitors(case)

    def _canvas(self, directory: Path, _include_pointer: bool) -> Path:
        path = directory / "fixture-canvas.png"
        canvas = Image.new("RGB", tuple(self.case["expected_canvas"]), (0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        colors = {item["connector"]: tuple(item["color"]) for item in self.case["displays"]}
        for monitor in self.monitors:
            draw.rectangle(monitor.bbox, fill=colors[monitor.connector])
        canvas.save(path, format="PNG")
        return path

    def capture(self, output: Path, long_edge: int) -> list[capturelib.Shot]:
        with (
            mock.patch.object(monitorslib, "list_monitors", return_value=self.monitors),
            mock.patch.object(capturelib, "available", return_value=(True, "")),
            mock.patch.object(capturelib, "_grab_canvas", side_effect=self._canvas),
        ):
            return capturelib.capture(
                "all",
                out_dir=output,
                scale_long_edge=long_edge,
                include_pointer=False,
            )


CAPTURE_PROVIDER_FACTORIES = (
    ("python", PythonCaptureProvider),
)


class CaptureContractTests(unittest.TestCase):
    def test_crop_precedes_resize_and_preserves_monitor_identity(self) -> None:
        case = load_display_case("primary_on_right")
        expected_colors = {
            item["connector"]: tuple(item["color"]) for item in case["displays"]
        }

        for provider_name, factory in CAPTURE_PROVIDER_FACTORIES:
            with self.subTest(provider=provider_name), tempfile.TemporaryDirectory() as raw:
                output = Path(raw)
                shots = factory(case).capture(output, long_edge=80)

                self.assertEqual(
                    [shot.monitor.connector for shot in shots],
                    case["expected_order"],
                )
                self.assertEqual([shot.size for shot in shots], [(160, 100), (160, 100)])
                self.assertEqual([shot.scaled for shot in shots], [(80, 50), (80, 50)])
                self.assertEqual([shot.offset for shot in shots], [(0, 0), (160, 0)])
                self.assertEqual(len({shot.id for shot in shots}), 2)

                for shot in shots:
                    self.assertTrue(shot.path.is_file())
                    self.assertTrue((output / f"{shot.id}.json").is_file())
                    restored = capturelib.load_shot(shot.id, [output])
                    self.assertEqual(restored.offset, shot.offset)
                    self.assertEqual(restored.scale, shot.scale)
                    with Image.open(shot.path) as image:
                        self.assertEqual(image.size, shot.scaled)
                        center = image.getpixel((image.width // 2, image.height // 2))
                    self.assertEqual(center, expected_colors[shot.monitor.connector])

    def test_cli_out_copies_metadata_to_default_search_directory(self) -> None:
        case = load_display_case("primary_on_right")
        monitors = make_monitors(case)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            custom_output = root / "custom"
            default_output = root / "default"
            cfg = SimpleNamespace(
                desktop=DesktopSpec(
                    capture_backend="gnome-screenshot",
                    screenshot_scale_long_edge=80,
                    shot_keep_hours=24,
                )
            )

            class FakeGate:
                def audit(self, *args, **kwargs) -> None:
                    return None

            def default_shot_dir(_cfg) -> Path:
                default_output.mkdir(parents=True, exist_ok=True)
                return default_output

            def fake_capture(spec, out_dir, scale_long_edge, **kwargs):
                self.assertEqual(spec, 2)
                self.assertEqual(scale_long_edge, 80)
                destination = Path(out_dir) / "fixture.png"
                Image.new("RGB", (80, 50), (30, 80, 220)).save(destination)
                shot = capturelib.Shot(
                    path=destination,
                    monitor=monitors[1],
                    offset=(160, 0),
                    size=(160, 100),
                    scaled=(80, 50),
                    scale=0.5,
                    id="m2-a1b2c3",
                    taken_at=1.0,
                )
                capturelib.save_meta(shot)
                return [shot]

            stdout = io.StringIO()
            with (
                mock.patch.object(shot_cli, "load", return_value=cfg),
                mock.patch.object(shot_cli, "gate_of", return_value=FakeGate()),
                mock.patch.object(shot_cli, "check_gate", return_value=None),
                mock.patch.object(shot_cli, "shot_dir", side_effect=default_shot_dir),
                mock.patch.object(shot_cli, "job_id", return_value="contract-job"),
                mock.patch.object(capturelib, "available", return_value=(True, "")),
                mock.patch.object(capturelib, "capture", side_effect=fake_capture),
                mock.patch.object(monitorslib, "list_monitors", return_value=monitors),
                contextlib.redirect_stdout(stdout),
            ):
                result = shot_cli.main(
                    ["--monitor", "2", "--out", str(custom_output), "--json"]
                )

            self.assertEqual(result, 0)
            payload = json.loads(stdout.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["shots"][0]["id"], "m2-a1b2c3")
            self.assertTrue((custom_output / "m2-a1b2c3.json").is_file())
            copied = json.loads(
                (default_output / "m2-a1b2c3.json").read_text(encoding="utf-8")
            )
            self.assertEqual(copied["png"], str((custom_output / "fixture.png").resolve()))


if __name__ == "__main__":
    unittest.main()
