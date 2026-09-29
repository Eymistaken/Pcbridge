"""Measure normal MCP capture across changed Hyprland output layouts."""
from __future__ import annotations

import os

if not __debug__:
    raise RuntimeError("Topology acceptance requires assertions; Python -O is forbidden")
if os.environ.get("PCBRIDGE_TEST_HYPRLAND_TOPOLOGY") != "1":
    raise RuntimeError("Set PCBRIDGE_TEST_HYPRLAND_TOPOLOGY=1 in the disposable VM")
if os.uname().nodename != "pcbridge-hyprland":
    raise RuntimeError("Topology acceptance runs only on the disposable pcbridge-hyprland VM")
if "PCBRIDGE_NATIVE_BIN" in os.environ:
    raise RuntimeError("Native helper overrides are forbidden")

import argparse
import asyncio
import base64
import dataclasses
import io
import json
from pathlib import Path
import re
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
from pcbridge.desktop import glowstate, hyprland, idlewatch, monitors  # noqa: E402
from pcbridge.native import discover_native_binary  # noqa: E402
from tests.live.hyprland.check_glow_owner import layers, monitor_rule, wait_for  # noqa: E402
from tests.live.test_capture_parity import (  # noqa: E402
    LineReader, MARKERS, close_to, marker_color, read_counter,
)


SHOT_LINE = re.compile(
    r'\*\*([^\n*]+)\*\* · (\d+)x(\d+) @ \((-?\d+), (-?\d+)\) → '
    r'(\d+)x(\d+) \(scale ([0-9.]+)\)\n  shot: `([^`]+)`'
)


def capture_evidence(result, table, connectors, counter, label, out_dir):
    assert not result.is_error, [item.text for item in result.content
                                 if getattr(item, "type", None) == "text"]
    body = "\n".join(item.text for item in result.content
                     if getattr(item, "type", None) == "text")
    rows = SHOT_LINE.findall(body)
    images = [item for item in result.content if getattr(item, "type", None) == "image"]
    assert len(rows) == len(images) == len(table) == 2, {"rows": rows, "images": len(images), "body": body}
    evidence = []
    for row, block in zip(rows, images):
        caption, width, height, x, y, scaled_width, scaled_height, scale, shot_id = row
        matching = [connector for connector in connectors if connector in caption]
        assert len(matching) == 1, caption
        connector = matching[0]
        monitor = next(item for item in table if item.connector == connector)
        expected = monitor.source_pixel_size
        assert (int(width), int(height)) == (int(scaled_width), int(scaled_height)) == expected
        assert (int(x), int(y)) == (monitor.x, monitor.y)
        assert float(scale) == 1.0
        with Image.open(io.BytesIO(base64.b64decode(block.data, validate=True))) as raw:
            image = raw.convert("RGB")
        assert image.size == expected, (connector, image.size, expected)
        marker = marker_color(image)
        observed = read_counter(image)
        assert close_to(marker, MARKERS[connectors.index(connector)]), (connector, marker)
        assert observed == counter, (connector, observed, counter)
        path = out_dir / f"{label}-{connector}.png"
        image.save(path)
        evidence.append({"connector": connector, "at": [monitor.x, monitor.y],
                         "desktop_size": [monitor.width, monitor.height],
                         "pixels": list(image.size), "marker": marker,
                         "counter": observed, "shot": shot_id, "path": str(path)})
    return evidence


async def run(cfg, pattern, reader, connectors, original, binary, out_dir, evidence, layout):
    mcp, _ = build_app(cfg, transport="stdio")
    grant_open = False
    changed = False
    try:
        async with Client(mcp, timeout=20) as client:
            async def call(name, arguments):
                return await asyncio.wait_for(client.call_tool(name, arguments,
                    raise_on_error=False), timeout=15)

            def show(counter):
                pattern.stdin.write(f"show {counter}\n")
                pattern.stdin.flush()
                reader.expect(f"shown {counter}", 10)
                time.sleep(0.7)  # Let fullscreen reconfiguration finish after draw.

            try:
                opened = await call("desktop_unlock", {"minutes": 1,
                    "reason": "Disposable VM topology capture acceptance"})
                assert not opened.is_error, opened.content
                grant_open = True
                wait_for(lambda: len(layers()) == 8, description="initial topology glow")
                runtime = mcp._pcbridge_desktop_runtime
                token = runtime.gate.current_token()
                assert token is not None

                show(741)
                first = await call("screen_capture", {"monitor": "all", "scale": 0,
                    "include_pointer": False})
                initial_table = monitors.list_monitors(use_cache=False)
                evidence["initial"] = capture_evidence(first, initial_table, connectors,
                    741, "initial", out_dir)
                old_shot = next(row["shot"] for row in evidence["initial"]
                                if row["connector"] == original[1]["name"])

                changed = True
                if layout == "rotated":
                    monitor_rule(original[1], scale=1.25, transform=1)
                    wait_for(lambda: any(item.connector == original[1]["name"] and
                        item.scale == 1.25 and item.transform == 1
                        for item in monitors.list_monitors(use_cache=False)),
                        description="fractional rotated monitor")
                else:
                    monitor_rule(original[0], position=(-1280, 0))
                    monitor_rule(original[1], position=(0, 0))
                    wait_for(lambda: [(row["name"], row["x"], row["y"])
                        for row in hyprland.monitors()] == [
                        (original[0]["name"], -1280, 0),
                        (original[1]["name"], 0, 0)],
                        description="negative platform origin")
                wait_for(lambda: glowstate.read_on_current_outputs(cfg.state_dir,
                    token, binary=binary), description="new topology glow")
                time.sleep(2.1)  # Let the product's normal monitor cache expire.

                if layout == "rotated":
                    cursor_before = json.loads(subprocess.run(["hyprctl", "-j", "cursorpos"],
                        check=True, capture_output=True, text=True, timeout=5).stdout)
                    stale = await call("mouse", {"action": "move", "x": 300, "y": 300,
                        "shot": old_shot, "force": True, "smooth": False})
                    assert stale.is_error and "screen layout changed" in str(stale.content).lower(), stale.content
                    cursor_after = json.loads(subprocess.run(["hyprctl", "-j", "cursorpos"],
                        check=True, capture_output=True, text=True, timeout=5).stdout)
                    assert cursor_after == cursor_before, (cursor_before, cursor_after)
                    evidence["stale_shot"] = "refused_without_motion"

                show(742)
                second = await call("screen_capture", {"monitor": "all", "scale": 0,
                    "include_pointer": False})
                changed_table = monitors.list_monitors(use_cache=False)
                evidence["changed"] = capture_evidence(second, changed_table, connectors,
                    742, "changed", out_dir)
                if layout == "rotated":
                    rotated = next(row for row in evidence["changed"]
                                   if row["connector"] == original[1]["name"])
                    assert rotated["pixels"] == [800, 1280]
                    assert rotated["desktop_size"] == [640, 1024]
                else:
                    assert [(item.connector, item.x, item.y, item.platform) for item in changed_table] == [
                        (original[0]["name"], 0, 0, (-1280, 0)),
                        (original[1]["name"], 1280, 0, (0, 0))]
                    assert all(row["pixels"] == [1280, 800] for row in evidence["changed"])
                    shot = next(row["shot"] for row in evidence["changed"]
                                if row["connector"] == original[0]["name"])
                    moved = await call("mouse", {"action": "move", "x": 300, "y": 300,
                        "shot": shot, "force": True, "smooth": False})
                    assert not moved.is_error, moved.content

                    def cursor_at_negative_target():
                        position = json.loads(subprocess.run(["hyprctl", "-j", "cursorpos"],
                            check=True, capture_output=True, text=True, timeout=5).stdout)
                        if abs(position["x"] + 980) <= 2 and abs(position["y"] - 300) <= 2:
                            return position
                        return None

                    evidence["pointer"] = wait_for(cursor_at_negative_target,
                        description="shot coordinate mapped to negative platform position")
                evidence["passed"] = True
            finally:
                try:
                    if changed:
                        for row in original:
                            monitor_rule(row)
                        wait_for(lambda: [(row["name"], row["x"], row["y"], row["scale"], row["transform"])
                            for row in hyprland.monitors()] ==
                            [(row["name"], row["x"], row["y"], row["scale"], row["transform"])
                             for row in original], description="original monitor rules restored")
                        evidence["cleanup"]["monitors"] = "restored"
                finally:
                    if grant_open:
                        closed = await call("desktop_lock", {})
                        assert not closed.is_error, closed.content
                        wait_for(lambda: not layers(), description="topology frame teardown")
                        evidence["cleanup"]["grant"] = "closed"
    finally:
        await asyncio.wait_for(asyncio.to_thread(mcp._pcbridge_desktop_runtime.close), timeout=8)
        evidence["cleanup"]["runtime"] = "closed"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--layout", choices=("rotated", "negative"), default="rotated")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    evidence = {"passed": False, "layout": args.layout, "cleanup": {}}
    failure = None
    idle = pattern = None
    try:
        assert hyprland.screen_locked() is False
        assert not layers() and idlewatch.read_idle_ms() is None
        original = hyprland.monitors()
        assert len(original) == 2
        assert [(item["name"], item["x"], item["y"], item["width"], item["height"],
                 item["scale"], item["transform"]) for item in original] == [
            ("Virtual-1", 0, 0, 1280, 800, 1, 0),
            ("Virtual-2", 1280, 0, 1280, 800, 1, 0)]
        base = load_config(ROOT / "config.example.toml", check_state=False)
        assert base.native.binary_path is None
        binary = discover_native_binary(base.native)
        assert binary.is_file() and binary.is_relative_to(ROOT / "pcbridge/_native")
        build = json.loads(subprocess.run([str(binary), "--build-info"], check=True,
            capture_output=True, text=True, timeout=5).stdout)
        assert build["profile"] == "release" and build["test_harness"] is False
        evidence["build"] = build
        with tempfile.TemporaryDirectory(prefix="pcbridge-topology-") as temporary:
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
                         description="fresh idle for topology acceptance")
                pattern = subprocess.Popen(["/usr/bin/python3",
                    str(ROOT / "tests/live/pattern_window.py"), "--timeout", "90"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
                reader = LineReader(pattern.stdout)
                connectors = reader.expect("ready ", 30).split(" ", 1)[1].split(",")
                assert connectors == ["Virtual-1", "Virtual-2"], connectors
                asyncio.run(asyncio.wait_for(run(cfg, pattern, reader, connectors,
                    original, binary, args.out_dir, evidence, args.layout), timeout=75))
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
                         description="topology acceptance cleanup")
                evidence["cleanup"]["screen_locked"] = hyprland.screen_locked()
                assert evidence["cleanup"]["screen_locked"] is False
    except BaseException as error:
        failure = error
        evidence["failure"] = {"type": type(error).__name__, "message": str(error)[:1600]}
        evidence["passed"] = False
    finally:
        (args.out_dir / "topology-capture.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print({"passed": evidence["passed"], "failure": evidence.get("failure"),
               "cleanup": evidence["cleanup"]})
    if failure:
        raise failure


if __name__ == "__main__":
    main()
