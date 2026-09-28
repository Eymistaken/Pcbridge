"""Measure native glow pixels and lifecycle without granting desktop control."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from pcbridge.desktop import hyprland  # noqa: E402
from tests.live.test_capture_parity import LineReader  # noqa: E402

FALLOFF = [(0.0, 1.0), (0.06, 0.7), (0.18, 0.38), (0.35, 0.16),
           (0.55, 0.055), (0.78, 0.012), (1.0, 0.0)]


def falloff(position):
    for (x0, y0), (x1, y1) in zip(FALLOFF, FALLOFF[1:]):
        if position <= x1:
            return y0 + (y1 - y0) * (position - x0) / (x1 - x0)
    return 0.0


def capture(connector, path):
    subprocess.run(["grim", "-o", connector, str(path)], check=True, timeout=3)
    with Image.open(path) as raw:
        return raw.convert("RGB")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    assert os.environ.get("PCBRIDGE_TEST_LIVE_HYPRLAND") == "1"
    assert os.uname().nodename == "pcbridge-hyprland", "Use the dedicated VM"
    assert hyprland.screen_locked() is False
    args.out_dir.mkdir(parents=True, exist_ok=True)
    window = subprocess.Popen(["/usr/bin/python3", str(ROOT / "tests/live/input_window.py"), "--timeout", "60"],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    native = None
    try:
        reader = LineReader(window.stdout)
        reader.expect('{"event": "ready"', 30)
        monitors = hyprland.monitors()
        assert len(monitors) == 2
        # The independent test application must own focus before the renderer.
        until = time.monotonic() + 5
        while time.monotonic() < until:
            focus = hyprland._query("activewindow", json_output=True)
            if focus.get("pid") == window.pid:
                break
            time.sleep(0.1)
        assert focus.get("pid") == window.pid, focus
        def geometry():
            return {client["address"]: [client["at"], client["size"]]
                    for client in hyprland._query("clients", json_output=True) if client.get("pid") == window.pid}
        def layers():
            return [layer for output in hyprland._query("layers", json_output=True).values()
                    for level in output["levels"].values() for layer in level if layer["namespace"] == "pcbridge-glow"]
        original_geometry = geometry()
        assert len(original_geometry) == 2
        baseline = {}
        for monitor in monitors:
            name = monitor["name"]
            until = time.monotonic() + 5
            while True:
                image = capture(name, args.out_dir / f"{name}-before.png")
                w, h = image.size
                points = [(w // 2, 0), (w // 2, h - 1), (0, int(h * 0.7)), (w - 1, int(h * 0.7))]
                if all(all(abs(value - background) <= 1 for value, background in zip(image.getpixel(point), (31, 31, 41)))
                       for point in points):
                    baseline[name] = image
                    break
                assert time.monotonic() < until, (name, "Fixture has not reached its displayed fullscreen background")
                time.sleep(0.1)
        native = subprocess.Popen([
            "cargo", "test", "-p", "pcbridge-native", "--locked", "--test", "hyprland_glow_live", "--", "--nocapture",
        ], cwd=ROOT / "rust", env={**os.environ, "PCBRIDGE_TEST_HYPRLAND_GLOW": "1"},
            stdout=subprocess.PIPE, text=True, start_new_session=True)
        LineReader(native.stdout).expect("GLOW_PRESENTED ", 45)
        assert hyprland._query("activewindow", json_output=True).get("address") == focus["address"]
        assert len(layers()) == 8
        assert geometry() == original_geometry, "Overlay changed fullscreen window geometry"
        evidence = []
        for monitor in monitors:
            name = monitor["name"]
            before = baseline[name]
            after = capture(name, args.out_dir / f"{name}-glow.png")
            w, h = after.size
            assert before.size == after.size == (monitor["width"], monitor["height"])
            d = round(min(150, max(48, min(w, h) * 0.085)))
            # Keep samples away from the fixture's field, banner, and notice.
            points = [(w // 2, 0), (w // 2, h - 1), (0, int(h * 0.7)), (w - 1, int(h * 0.7))]
            measured = []
            for point in points:
                original, shown = before.getpixel(point), after.getpixel(point)
                for background, channel in zip(original, shown):
                    expected = round(background + (255 - background) * 0.42)
                    assert abs(channel - expected) <= 3, (name, point, original, shown, expected)
                measured.append({"point": point, "before": original, "glow": shown})
            # Compare the rapid inward falloff at the left edge. Breathing has
            # just begun, so allow its small width change and 8-bit rounding.
            for distance in (0, 4, 12, 24, 37, 53, d - 1):
                point = (distance, int(h * 0.7))
                original, shown = before.getpixel(point), after.getpixel(point)
                alpha = 0.42 * falloff(distance / d)
                assert all(abs(channel - round(background + (255 - background) * alpha)) <= 5
                           for background, channel in zip(original, shown)), (name, distance, original, shown)
            evidence.append({"connector": name, "depth": d, "edges": measured})
        assert native.wait(timeout=20) == 0
        until = time.monotonic() + 2
        while layers() and time.monotonic() < until:
            time.sleep(0.1)
        assert not layers(), "Renderer shutdown left glow surfaces"
        print(json.dumps({"native_glow_pixels": evidence, "focus_unchanged": True,
                          "breathing_cycle_and_fade_out": "passed"}, sort_keys=True))
    finally:
        if native and native.poll() is None:
            os.killpg(native.pid, signal.SIGTERM)
            try:
                native.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(native.pid, signal.SIGKILL)
                native.wait()
        if window.poll() is None:
            window.stdin.write("quit\n")
            window.stdin.flush()
            try:
                window.wait(timeout=5)
            except subprocess.TimeoutExpired:
                window.kill()
                window.wait()


if __name__ == "__main__":
    main()
