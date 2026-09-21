#!/usr/bin/env python3
"""Step 8.5: a region capture on the real desktop, against a whole-monitor one.

Skipped unless `PCBRIDGE_TEST_CAPTURE=1`. Like `test_capture_parity.py` it
covers every monitor with a static pattern, opens screen shares under a
scratch grant, and sends no input.

What it shows, on the Python screencast and on the native helper:

* a region cut from the live frame is pixel for pixel the same part of a
  whole-monitor capture taken right before it;
* a region read off a downscaled screenshot (`shot=`) lands on the same
  pattern, and its record maps its own pixels back to the right global
  points;
* how much smaller the region's PNG is, and how long each capture takes.

`PCBRIDGE_REGION_REPORT=<path>` writes the numbers as JSON.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop import monitors as monitorslib  # noqa: E402
from pcbridge.desktop import screencast as screencastlib  # noqa: E402
from pcbridge.desktop.backends.python import PythonCaptureProvider  # noqa: E402
from pcbridge.desktop.backends.rust import RustCaptureProvider  # noqa: E402
from pcbridge.desktop.safety import SafetyGate  # noqa: E402
from tests.live.test_capture_parity import (  # noqa: E402
    MARKER_BOX,
    MARKERS,
    PatternWindow,
    agreement,
    close_to,
    legacy_helpers,
    release_binary,
    rgb,
    scratch_config,
    screen_locked,
)

LIVE = os.environ.get("PCBRIDGE_TEST_CAPTURE") == "1"
REPORT: dict = {}


@unittest.skipUnless(
    LIVE, "set PCBRIDGE_TEST_CAPTURE=1: opens screen shares and covers the monitors"
)
class RegionOnTheDesktop(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        binary, why = release_binary()
        if binary is None:
            raise unittest.SkipTest(why)
        if screen_locked():
            raise unittest.SkipTest("the screen is locked")
        if legacy_helpers():
            raise unittest.SkipTest("a Python screencast helper is running (a real grant)")
        cls.root = Path(tempfile.mkdtemp(prefix="pcb-region-"))
        cls.pattern = None
        cls.providers = []
        try:
            cls.cfg = load_config(str(scratch_config(cls.root, binary)))
            cls.gate = SafetyGate(cls.cfg)
            cls.gate.unlock(10, reason="live region capture")
            cls.pattern = PatternWindow()
            cls.monitors = monitorslib.list_monitors(use_cache=False)
            legacy = PythonCaptureProvider(cls.cfg, screencastlib.ScreenCast())
            legacy.start(cursor=False)
            native = RustCaptureProvider(cls.cfg, gate=cls.gate)
            native.start(cursor=False)
            cls.providers = [("python", legacy), ("native", native)]
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        for _name, provider in cls.providers:
            try:
                provider.close()
            except Exception:  # noqa: BLE001
                pass
        if cls.pattern is not None:
            cls.pattern.close()
        try:
            cls.gate.lock()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(cls.root, ignore_errors=True)
        rendered = json.dumps(REPORT, indent=2)
        print("\nregion report:\n" + rendered, file=sys.stderr)
        target = os.environ.get("PCBRIDGE_REGION_REPORT")
        if target:
            Path(target).write_text(rendered + "\n", encoding="utf-8")

    def shoot(self, provider, label: str, *, scale: int, region=None):
        out = self.root / label
        started = time.perf_counter()
        shots = provider.capture(
            "all" if region else self.monitors[-1].index,
            out_dir=out, scale_long_edge=scale, include_pointer=False,
            reserved_dirs=[out], region=region,
        )
        return shots, (time.perf_counter() - started) * 1000, out

    def test_1_a_region_is_exactly_that_part_of_the_monitor(self) -> None:
        right = self.monitors[-1]
        # The marker box plus a margin of pattern around it.
        x, y, width, height = MARKER_BOX
        local = (x - 40, y - 40, width + 80, height + 80)
        for name, provider in self.providers:
            with self.subTest(backend=name):
                region = provider.resolve_region(*local, monitor=right.index)
                self.assertEqual(region[0], (right.x + local[0], right.y + local[1],
                                             local[2], local[3]))
                whole, whole_ms, _ = self.shoot(provider, f"{name}-whole", scale=0)
                part, part_ms, _ = self.shoot(provider, f"{name}-part", scale=0,
                                              region=region)
                self.assertEqual(len(part), 1)
                shot = part[0]
                self.assertTrue(shot.region)
                self.assertEqual(shot.offset, region[0][:2])
                self.assertEqual(shot.scaled, tuple(local[2:]))
                crop = rgb(whole[0].path).crop(
                    (local[0], local[1], local[0] + local[2], local[1] + local[3])
                )
                match = agreement(crop, rgb(shot.path))
                marker = rgb(shot.path).getpixel((40 + width // 2, 40 + height // 2))
                expected = MARKERS[(right.index - 1) % len(MARKERS)]
                self.assertTrue(close_to(marker, expected), f"{marker} != {expected}")
                self.assertGreaterEqual(match, 99.0, f"{name}: {match}%")
                self.assertEqual(shot.to_global(0, 0), region[0][:2])
                REPORT.setdefault("exact", {})[name] = {
                    "pixel_match_percent": round(match, 3),
                    "whole_ms": round(whole_ms),
                    "region_ms": round(part_ms),
                    "whole_png_kib": whole[0].path.stat().st_size // 1024,
                    "region_png_kib": shot.path.stat().st_size // 1024,
                    "region_px": list(shot.scaled),
                }

    def test_2_a_region_read_off_a_downscaled_shot_lands_on_the_pattern(self) -> None:
        right = self.monitors[-1]
        x, y, width, height = MARKER_BOX
        for name, provider in self.providers:
            with self.subTest(backend=name):
                small, _ms, out = self.shoot(provider, f"{name}-small", scale=1536)
                seen = small[0]
                factor = seen.scaled[0] / right.width
                self.assertLess(factor, 1.0)
                # The marker as the agent sees it in the 1536-wide picture.
                picked = (round(x * factor), round(y * factor),
                          round(width * factor), round(height * factor))
                region = provider.resolve_region(*picked, shot=seen.id, dirs=[out])
                part, _ms, _ = self.shoot(provider, f"{name}-zoom", scale=0, region=region)
                shot = part[0]
                image = rgb(shot.path)
                center = image.getpixel((image.width // 2, image.height // 2))
                expected = MARKERS[(right.index - 1) % len(MARKERS)]
                self.assertTrue(close_to(center, expected), f"{center} != {expected}")
                # The region's own pixels map back inside the marker box.
                gx, gy = shot.to_global(image.width // 2, image.height // 2)
                self.assertTrue(right.x + x <= gx < right.x + x + width)
                self.assertTrue(right.y + y <= gy < right.y + y + height)
                REPORT.setdefault("from_shot", {})[name] = {
                    "picked_in_shot": list(picked),
                    "global_box": list(region[0]),
                    "region_px": list(shot.scaled),
                }


if __name__ == "__main__":
    unittest.main(verbosity=2)
