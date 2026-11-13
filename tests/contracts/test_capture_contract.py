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

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.cli import shot as shot_cli  # noqa: E402
from pcbridge.config import DesktopSpec, NativeSpec  # noqa: E402
from pcbridge.desktop import capture as capturelib  # noqa: E402
from pcbridge.desktop import monitors as monitorslib  # noqa: E402
from pcbridge.desktop.backends.rust import RustCaptureProvider  # noqa: E402


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


class FixtureScreenCast:
    """One frame per monitor, as the native share delivers them, from a fixture case."""

    def __init__(self, case: dict, monitors: list[monitorslib.Monitor]) -> None:
        self.colors = {item["connector"]: tuple(item["color"]) for item in case["displays"]}
        self.monitors = {monitor.connector: monitor for monitor in monitors}

    def is_open(self) -> bool:
        return True

    def ensure_cursor(self, _cursor: bool) -> bool:
        return False

    def capture(self, connector: str, path) -> dict:
        monitor = self.monitors[connector]
        Image.new("RGB", monitor.source_pixel_size, self.colors[connector]).save(path, format="PNG")
        return {"ok": True, "path": str(path), "monitor": connector}

    def close(self) -> None:
        return None


class FixtureCapture:
    """Provider-neutral fixtures through `capture.py` and a per-monitor frame source."""

    def __init__(self, case: dict) -> None:
        self.case = case
        self.monitors = make_monitors(case)

    def capture(self, output: Path, long_edge: int) -> list[capturelib.Shot]:
        with mock.patch.object(monitorslib, "list_monitors", return_value=self.monitors):
            return capturelib.capture(
                "all",
                out_dir=output,
                scale_long_edge=long_edge,
                include_pointer=False,
                screencast=FixtureScreenCast(self.case, self.monitors),
            )


CAPTURE_PROVIDER_FACTORIES = (
    ("native frames", FixtureCapture),
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

    def test_the_png_is_written_without_the_slow_optimize_pass(self) -> None:
        """Pillow's `optimize` cost 3.1 s to save 5% (measured 2026-09-20).

        A real 1920x1080 screenshot scaled to 1536: saving with `optimize=True`
        took 3106 ms and wrote 906 KiB, the default compression took 259 ms and
        wrote 957 KiB. That was 84% of a capture's whole time. The pixels are
        the same either way -- PNG is lossless -- so only the file size and the
        wait changed.
        """
        source = Image.new("RGB", (40, 20), (12, 34, 56))
        saved: list[dict] = []
        real_save = Image.Image.save

        def save(self, fp, format=None, **params):  # noqa: A002 - Pillow's name
            saved.append(params)
            return real_save(self, fp, format, **params)

        with tempfile.TemporaryDirectory() as raw:
            dest = Path(raw) / "out.png"
            with mock.patch.object(Image.Image, "save", save):
                size, scaled, scale = capturelib._write_crop(source, None, dest, 0)
            self.assertEqual((size, scaled, scale), ((40, 20), (40, 20), 1.0))
            with Image.open(dest) as written:
                self.assertEqual(list(written.convert("RGB").getdata()),
                                 list(source.getdata()))
        self.assertEqual(len(saved), 1)
        self.assertNotIn("optimize", saved[0])

    def test_cli_out_copies_metadata_to_default_search_directory(self) -> None:
        case = load_display_case("primary_on_right")
        monitors = make_monitors(case)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            custom_output = root / "custom"
            default_output = root / "default"
            cfg = SimpleNamespace(
                desktop=DesktopSpec(
                    screenshot_scale_long_edge=80,
                    shot_keep_hours=24,
                ),
                native=NativeSpec(),
                shot_search_dirs=[default_output],
            )

            class FakeGate:
                def audit(self, *args, **kwargs) -> None:
                    return None

            capture_provider = RustCaptureProvider(
                cfg, screencast=FixtureScreenCast(case, monitors))
            runtime = SimpleNamespace(
                gate=FakeGate(),
                capture_provider=capture_provider,
                start_capture=lambda cursor=None: {},
                stop_capture=lambda: None,
                close=capture_provider.close,
            )

            def default_shot_dir(_cfg) -> Path:
                default_output.mkdir(parents=True, exist_ok=True)
                return default_output

            # Task 3.5 made the copy part of the capture itself (published with
            # it, or not at all), so the real capture runs here; only the
            # helper's frames are replaced by the fixture's.
            stdout = io.StringIO()
            with (
                mock.patch.object(shot_cli, "load", return_value=cfg),
                mock.patch.object(shot_cli, "runtime_of", return_value=runtime),
                mock.patch.object(shot_cli, "check_gate", return_value=None),
                mock.patch.object(shot_cli, "shot_dir", side_effect=default_shot_dir),
                mock.patch.object(shot_cli, "job_id", return_value="contract-job"),
                mock.patch("pcbridge.desktop.backends.rust.native_binary_ready",
                           return_value=(True, "")),
                mock.patch.object(monitorslib, "list_monitors", return_value=monitors),
                contextlib.redirect_stdout(stdout),
            ):
                result = shot_cli.main(
                    ["--monitor", "2", "--out", str(custom_output), "--json"]
                )

            self.assertEqual(result, 0)
            payload = json.loads(stdout.getvalue())
            self.assertTrue(payload["ok"])
            shot = payload["shots"][0]
            self.assertRegex(shot["id"], r"^m2-[0-9a-f]{6}$")
            self.assertEqual(payload["backend"], "linux.mutter.pipewire")
            # No fallback path is left to report, so no "degraded" field.
            self.assertNotIn("degraded", payload)
            self.assertEqual(shot["offset"], [160, 0])
            self.assertTrue((custom_output / f"{shot['id']}.json").is_file())
            copied = json.loads(
                (default_output / f"{shot['id']}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(copied["png"], shot["path"])
            self.assertTrue(Path(copied["png"]).is_absolute())
            self.assertEqual(Path(copied["png"]).parent, custom_output)
            self.assertTrue(Path(copied["png"]).is_file())


if __name__ == "__main__":
    unittest.main()
