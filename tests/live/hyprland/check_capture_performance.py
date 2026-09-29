"""Measure normal MCP capture latency on both disposable Hyprland VM outputs."""
from __future__ import annotations

import os

if not __debug__:
    raise RuntimeError("Capture performance requires assertions; Python -O is forbidden")
if os.environ.get("PCBRIDGE_TEST_HYPRLAND_PERFORMANCE") != "1":
    raise RuntimeError("Set PCBRIDGE_TEST_HYPRLAND_PERFORMANCE=1 in the disposable VM")
if os.uname().nodename != "pcbridge-hyprland":
    raise RuntimeError("Capture performance runs only on the disposable pcbridge-hyprland VM")
if "PCBRIDGE_NATIVE_BIN" in os.environ:
    raise RuntimeError("Native helper overrides are forbidden")

import argparse
import asyncio
import base64
import dataclasses
import io
import json
import math
from pathlib import Path
import re
import statistics
import subprocess
import sys
import tempfile
import time

from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from fastmcp import Client  # noqa: E402
from pcbridge.app import build_app  # noqa: E402
from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop import hyprland, idlewatch, monitors  # noqa: E402
from pcbridge.native import discover_native_binary  # noqa: E402
from tests.live.hyprland.check_glow_owner import layers, wait_for  # noqa: E402
from tests.live.test_capture_parity import (  # noqa: E402
    LineReader, MARKERS, close_to, marker_color, read_counter,
)

SHOT_LINE = re.compile(
    r'\*\*([^\n*]+)\*\* · (\d+)x(\d+) @ \((-?\d+), (-?\d+)\) → '
    r'(\d+)x(\d+) \(scale ([0-9.]+)\)\n  shot: `([^`]+)`'
)


def check_capture(result, connectors, counter, seen_shots):
    assert not result.is_error, [item.text for item in result.content
                                 if getattr(item, "type", None) == "text"]
    body = "\n".join(item.text for item in result.content
                     if getattr(item, "type", None) == "text")
    rows = SHOT_LINE.findall(body)
    images = [item for item in result.content if getattr(item, "type", None) == "image"]
    assert len(rows) == len(images) == len(connectors) == 2, (rows, len(images))
    for index, (row, block) in enumerate(zip(rows, images)):
        caption, width, height, x, y, scaled_width, scaled_height, scale, shot_id = row
        assert [name for name in connectors if name in caption] == [connectors[index]], caption
        assert (int(width), int(height), int(scaled_width), int(scaled_height)) == (
            1280, 800, 1280, 800), row
        assert (int(x), int(y)) == (1280 * index, 0), row
        assert float(scale) == 1.0, row
        assert shot_id not in seen_shots, shot_id
        seen_shots.add(shot_id)
        with Image.open(io.BytesIO(base64.b64decode(block.data, validate=True))) as raw:
            image = raw.convert("RGB")
        assert image.size == (1280, 800), image.size
        assert close_to(marker_color(image), MARKERS[index]), connectors[index]
        assert read_counter(image) == counter, (connectors[index], counter)


async def run(cfg, pattern, reader, connectors, evidence):
    mcp, _ = build_app(cfg, transport="stdio")
    grant_open = False
    try:
        async with Client(mcp, timeout=20) as client:
            async def call(name, arguments):
                return await asyncio.wait_for(client.call_tool(name, arguments,
                    raise_on_error=False), timeout=15)

            try:
                opened = await call("desktop_unlock", {"minutes": 2,
                    "reason": "Disposable VM capture performance measurement"})
                assert not opened.is_error, opened.content
                grant_open = True
                wait_for(lambda: len(layers()) == 8, description="capture performance glow")
                seen_shots = set()
                durations = []
                for index in range(22):
                    counter = 741 if index < 12 else 742
                    if index in (0, 12):
                        pattern.stdin.write(f"show {counter}\n")
                        pattern.stdin.flush()
                        reader.expect(f"shown {counter}", 10)
                        time.sleep(0.3)
                    start = time.perf_counter()
                    result = await call("screen_capture", {"monitor": "all", "scale": 0,
                        "include_pointer": False})
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    check_capture(result, connectors, counter, seen_shots)
                    if index >= 2:
                        durations.append(round(elapsed_ms, 3))
                ordered = sorted(durations)
                evidence["samples_ms"] = durations
                evidence["median_ms"] = round(statistics.median(durations), 3)
                evidence["p95_ms"] = ordered[math.ceil(0.95 * len(ordered)) - 1]
                evidence["min_ms"] = ordered[0]
                evidence["max_ms"] = ordered[-1]
                evidence["unique_shots"] = len(seen_shots)
                evidence["passed"] = True
            finally:
                if grant_open:
                    closed = await call("desktop_lock", {})
                    assert not closed.is_error, closed.content
                    wait_for(lambda: not layers(), description="capture performance frame teardown")
                    evidence["cleanup"]["grant"] = "closed"
    finally:
        await asyncio.wait_for(asyncio.to_thread(mcp._pcbridge_desktop_runtime.close), timeout=8)
        evidence["cleanup"]["runtime"] = "closed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    evidence = {"passed": False, "cleanup": {}, "warmups": 2,
                "samples": 20, "capture": "all", "scale": 0,
                "include_pointer": False}
    failure = None
    idle = pattern = None
    try:
        assert hyprland.screen_locked() is False
        assert not layers() and idlewatch.read_idle_ms() is None
        assert [(row["name"], row["x"], row["y"], row["width"], row["height"],
                 row["scale"], row["transform"], row["mirrorOf"], row["disabled"])
                for row in hyprland.monitors()] == [
                ("Virtual-1", 0, 0, 1280, 800, 1, 0, "none", False),
                ("Virtual-2", 1280, 0, 1280, 800, 1, 0, "none", False)]
        base = load_config(ROOT / "config.example.toml", check_state=False)
        assert base.native.binary_path is None
        binary = discover_native_binary(base.native)
        assert binary.is_file() and binary.is_relative_to(ROOT / "pcbridge/_native")
        build = json.loads(subprocess.run([str(binary), "--build-info"], check=True,
            capture_output=True, text=True, timeout=5).stdout)
        assert build["profile"] == "release" and build["test_harness"] is False
        evidence["build"] = build
        with tempfile.TemporaryDirectory(prefix="pcbridge-capture-performance-") as temporary:
            directory = Path(temporary)
            cfg = dataclasses.replace(base, state_dir=directory / "state",
                desktop=dataclasses.replace(base.desktop, enabled=True,
                    unlock_idle_seconds=60, unlock_notification=False,
                    agent_shot_dir=str(directory / "shots")),
                native=dataclasses.replace(base.native, capture="rust", input="rust"))
            cfg.jobs_dir.mkdir(parents=True)
            (cfg.state_dir / "shots").mkdir(parents=True)
            Path(cfg.desktop.agent_shot_dir).mkdir(parents=True)
            try:
                idle = subprocess.Popen([str(binary), "idle-watch"], stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
                wait_for(lambda: idlewatch.read_idle_ms() is not None,
                         description="fresh idle for capture performance")
                pattern = subprocess.Popen(["/usr/bin/python3",
                    str(ROOT / "tests/live/pattern_window.py"), "--timeout", "120"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
                reader = LineReader(pattern.stdout)
                connectors = reader.expect("ready ", 30).split(" ", 1)[1].split(",")
                assert connectors == ["Virtual-1", "Virtual-2"], connectors
                assert [item.connector for item in monitors.list_monitors(use_cache=False)] == connectors
                asyncio.run(asyncio.wait_for(run(cfg, pattern, reader, connectors, evidence),
                                             timeout=105))
            finally:
                if pattern:
                    if pattern.poll() is None:
                        try:
                            pattern.stdin.write("quit\n")
                            pattern.stdin.flush()
                        except BrokenPipeError:
                            pass
                    try:
                        pattern.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pattern.kill()
                        pattern.wait(timeout=3)
                    evidence["cleanup"]["pattern_exit"] = pattern.returncode
                if idle:
                    if idle.poll() is None:
                        idle.terminate()
                    idle.wait(timeout=3)
                    evidence["cleanup"]["idle_exit"] = idle.returncode
                wait_for(lambda: not layers() and idlewatch.read_idle_ms() is None,
                         description="capture performance cleanup")
                evidence["cleanup"]["screen_locked"] = hyprland.screen_locked()
                assert evidence["cleanup"]["screen_locked"] is False
    except BaseException as error:
        failure = error
        evidence["failure"] = {"type": type(error).__name__, "message": str(error)[:1600]}
        evidence["passed"] = False
    finally:
        (args.out_dir / "capture-performance.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print({"passed": evidence["passed"], "median_ms": evidence.get("median_ms"),
               "p95_ms": evidence.get("p95_ms"), "failure": evidence.get("failure"),
               "cleanup": evidence["cleanup"]})
    if failure:
        raise failure


if __name__ == "__main__":
    main()
