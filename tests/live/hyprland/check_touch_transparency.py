"""Measure whether native glow strips pass real touchscreen input to a client."""
from __future__ import annotations

import os

# Refuse before importing GUI, device, or PcBridge code.
if not __debug__:
    raise RuntimeError("Touch acceptance requires assertions; Python -O is forbidden")
if os.environ.get("PCBRIDGE_TEST_HYPRLAND_TOUCH") != "1":
    raise RuntimeError("Set PCBRIDGE_TEST_HYPRLAND_TOUCH=1 in the disposable VM")
if os.uname().nodename != "pcbridge-hyprland":
    raise RuntimeError("Touch acceptance runs only on the disposable pcbridge-hyprland VM")
if "PCBRIDGE_NATIVE_BIN" in os.environ:
    raise RuntimeError("Native helper overrides are forbidden")

import argparse
import asyncio
import base64
import dataclasses
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from evdev import AbsInfo, UInput, ecodes as e  # noqa: E402
from fastmcp import Client  # noqa: E402
from pcbridge.app import build_app  # noqa: E402
from pcbridge.config import load_config  # noqa: E402
from pcbridge.native import discover_native_binary  # noqa: E402
from pcbridge.desktop import hyprland, idlewatch  # noqa: E402
from tests.live.hyprland.check_glow_owner import layers, wait_for  # noqa: E402
from tests.live.hyprland.wayland_test_support import Observer, compile_observer  # noqa: E402


def make_touchscreen(width, height):
    caps = {
        e.EV_KEY: [e.BTN_TOUCH],
        e.EV_ABS: [
            (e.ABS_X, AbsInfo(0, 0, width - 1, 0, 0, 0)),
            (e.ABS_Y, AbsInfo(0, 0, height - 1, 0, 0, 0)),
            (e.ABS_MT_SLOT, AbsInfo(0, 0, 0, 0, 0, 0)),
            (e.ABS_MT_TRACKING_ID, AbsInfo(0, 0, 65535, 0, 0, 0)),
            (e.ABS_MT_POSITION_X, AbsInfo(0, 0, width - 1, 0, 0, 0)),
            (e.ABS_MT_POSITION_Y, AbsInfo(0, 0, height - 1, 0, 0, 0)),
        ],
    }
    return UInput(caps, name="pcbridge-vm-touch-" + uuid.uuid4().hex[:8],
                  input_props=[e.INPUT_PROP_DIRECT])


def tap(device, x, y, tracking_id):
    device.write(e.EV_ABS, e.ABS_MT_SLOT, 0)
    device.write(e.EV_ABS, e.ABS_MT_TRACKING_ID, tracking_id)
    device.write(e.EV_ABS, e.ABS_MT_POSITION_X, x)
    device.write(e.EV_ABS, e.ABS_MT_POSITION_Y, y)
    device.write(e.EV_ABS, e.ABS_X, x)
    device.write(e.EV_ABS, e.ABS_Y, y)
    device.write(e.EV_KEY, e.BTN_TOUCH, 1)
    device.syn()
    time.sleep(0.08)
    device.write(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1)
    device.write(e.EV_KEY, e.BTN_TOUCH, 0)
    device.syn()


def eval_hyprland(expression):
    result = subprocess.run(["hyprctl", "eval", expression], capture_output=True,
                            text=True, timeout=5)
    assert result.returncode == 0 and result.stdout.strip() == "ok", {
        "expression": expression, "returncode": result.returncode,
        "stdout": result.stdout, "stderr": result.stderr,
    }


def map_touchscreen(name, output):
    eval_hyprland(f"hl.device({{name={json.dumps(name)},output={json.dumps(output)}}})")


async def run_case(cfg, observer, device, app_id, evidence, out_dir, expected_at, output):
    mcp = None
    grant_open = False
    try:
        mcp, _ = build_app(cfg, transport="stdio")
        async with Client(mcp, timeout=15) as client:
            async def call(name, arguments):
                result = await asyncio.wait_for(client.call_tool(name, arguments,
                    raise_on_error=False), timeout=8)
                evidence["calls"].append({"name": name, "is_error": result.is_error})
                assert not result.is_error, {"tool": name, "content": result.content}
                return result

            def focus():
                assert hyprland.screen_locked() is False
                current = hyprland._query("activewindow", json_output=True)
                clients = [c for c in hyprland._query("clients", json_output=True)
                           if c.get("pid") == observer.process.pid and c.get("class") == app_id]
                assert len(clients) == 1, clients
                target = clients[0]
                assert target.get("mapped") and target.get("visible") and target.get("fullscreen") == 2
                assert current.get("pid") == observer.process.pid and current.get("address") == target.get("address")
                assert target["at"] == expected_at and target["size"] == [1280, 800], target
                return target

            async def receive(kind, mark, predicate=lambda event: True):
                return await asyncio.to_thread(observer.wait, kind, mark, 4, predicate)

            async def check_tap(name, x, y, tracking_id):
                focus()
                mark = observer.mark()
                tap(device, x, y, tracking_id)
                down = await receive("touch_down", mark, lambda event:
                    event["on_surface"] and abs(event["x"] - x) <= 1 and abs(event["y"] - y) <= 1)
                up = await receive("touch_up", mark, lambda event: event["id"] == down["id"])
                events = observer.since(mark)
                assert len([event for event in events if event["event"] == "touch_down"]) == 1, events
                assert len([event for event in events if event["event"] == "touch_up"]) == 1, events
                assert not any(event["event"] == "touch_cancel" for event in events), events
                focus()
                evidence["taps"].append({"name": name, "sent": [x, y], "down": down, "up": up})

            try:
                observer.wait("ready", timeout=8)
                target = focus()
                evidence["wayland_output"] = await receive("output_selected", 0,
                    lambda event: event["name"] == output)
                await receive("ready", 0, lambda event:
                    [event["width"], event["height"]] == target["size"])
                evidence["touch_capability"] = await receive("touch_capability", 0)
                evidence["geometry"] = {"at": target["at"], "size": target["size"]}
                await check_tap("before_grant", 640, 400, 1)
                wait_for(lambda: (idlewatch.read_idle_ms() or 0) >= 2000,
                         description="known idle after baseline touch")
                await call("desktop_unlock", {"minutes": 1,
                    "reason": "Disposable VM touch transparency acceptance"})
                grant_open = True
                evidence["glow_layers"] = len(layers())
                assert evidence["glow_layers"] == 8
                focus()
                capture = await call("screen_capture", {"monitor": "window", "scale": 0})
                images = [item for item in capture.content if getattr(item, "type", None) == "image"]
                assert len(images) == 1
                path = out_dir / "touch-glow.png"
                path.write_bytes(base64.b64decode(images[0].data, validate=True))
                evidence["screenshot"] = str(path)
                focus()
                for tracking_id, (name, x, y) in enumerate((
                    ("top", 640, 5), ("bottom", 640, 794),
                    ("left", 5, 400), ("right", 1274, 400)), start=2):
                    await check_tap(name, x, y, tracking_id)
                await call("desktop_lock", {})
                grant_open = False
                wait_for(lambda: not layers(), description="touch test frame teardown")
                await check_tap("after_grant", 640, 400, 6)
                evidence["passed"] = True
            finally:
                if grant_open:
                    await call("desktop_lock", {})
    finally:
        if mcp:
            await asyncio.wait_for(asyncio.to_thread(mcp._pcbridge_desktop_runtime.close), timeout=8)
            evidence["cleanup"]["runtime"] = "closed"


def stop_child(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
    return {"pid": process.pid, "exit_code": process.returncode}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--output", choices=("Virtual-1", "Virtual-2"), default="Virtual-1")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    evidence = {"calls": [], "taps": [], "cleanup": {}, "output": args.output, "passed": False}
    failure = None
    observer = idle = device = None
    mapping_requested = False
    try:
        assert hyprland.screen_locked() is False
        assert not layers() and idlewatch.read_idle_ms() is None
        assert os.access("/dev/uinput", os.R_OK | os.W_OK)
        monitors = {item["name"]: item for item in hyprland._query("monitors", json_output=True)}
        target_monitor = monitors[args.output]
        assert target_monitor["width"] == 1280 and target_monitor["height"] == 800
        assert target_monitor["scale"] == 1 and target_monitor["transform"] == 0
        expected_at = [target_monitor["x"], target_monitor["y"]]
        assert expected_at == ([0, 0] if args.output == "Virtual-1" else [1280, 0])
        evidence["monitor"] = {"at": expected_at, "size": [1280, 800]}
        base = load_config(ROOT / "config.example.toml", check_state=False)
        assert base.native.binary_path is None
        binary = discover_native_binary(base.native)
        assert binary.is_file() and binary.is_relative_to(ROOT / "pcbridge/_native")
        build = json.loads(subprocess.run([str(binary), "--build-info"], check=True,
            capture_output=True, text=True, timeout=5).stdout)
        assert build["profile"] == "release" and build["test_harness"] is False
        evidence["build"] = build
        with tempfile.TemporaryDirectory(prefix="pcbridge-touch-") as temporary:
            directory = Path(temporary)
            cfg = dataclasses.replace(base, state_dir=directory / "state",
                desktop=dataclasses.replace(base.desktop, enabled=True, unlock_idle_seconds=60,
                    unlock_notification=False, idle_guard_seconds=2,
                    agent_shot_dir=str(directory / "shots")),
                native=dataclasses.replace(base.native, input="rust", capture="rust"))
            cfg.jobs_dir.mkdir(parents=True)
            (cfg.state_dir / "shots").mkdir(parents=True)
            Path(cfg.desktop.agent_shot_dir).mkdir(parents=True)
            executable, evidence["compile"] = compile_observer(directory)
            app_id = "pcbridge-touch-" + uuid.uuid4().hex
            try:
                device = make_touchscreen(1280, 800)
                evidence["device"] = {"name": device.name, "path": device.device.path}
                time.sleep(1.0)  # Let the compositor publish the temporary seat capability.
                if args.output == "Virtual-2":
                    mapping_requested = True
                    map_touchscreen(device.name, args.output)
                observer = Observer(executable, directory, app_id, args.output)
                idle = subprocess.Popen([str(binary), "idle-watch"], stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
                wait_for(lambda: idlewatch.read_idle_ms() is not None,
                         description="own fresh idle watcher")
                asyncio.run(asyncio.wait_for(run_case(cfg, observer, device, app_id,
                    evidence, args.out_dir, expected_at, args.output), timeout=50))
            finally:
                for name, cleanup in (
                    ("observer", observer.close if observer else None),
                    ("touch_mapping", (lambda: map_touchscreen(device.name, "[[Auto]]"))
                     if mapping_requested and device else None),
                    ("touch_device", device.close if device else None),
                    ("idle", (lambda: stop_child(idle)) if idle else None),
                ):
                    if cleanup:
                        try:
                            evidence["cleanup"][name] = cleanup() or "closed"
                        except BaseException as error:
                            evidence["cleanup"][name] = {"error": f"{type(error).__name__}: {error}"}
                if observer:
                    evidence["events"] = observer.since(0)
                wait_for(lambda: not layers(), description="final touch frame teardown")
                wait_for(lambda: idlewatch.read_idle_ms() is None,
                         description="own idle proof expires")
                evidence["cleanup"]["final_layers"] = layers()
                evidence["cleanup"]["final_idle_ms"] = idlewatch.read_idle_ms()
                evidence["cleanup"]["screen_locked"] = hyprland.screen_locked()
                assert evidence["cleanup"]["screen_locked"] is False
                if observer:
                    assert evidence["cleanup"]["observer"]["exit_code"] == 0
                    assert evidence["cleanup"]["observer"]["reader_stopped"] is True
                assert not any(isinstance(value, dict) and "error" in value
                               for value in evidence["cleanup"].values()), evidence["cleanup"]
    except BaseException as error:
        failure = error
        evidence["failure"] = {"type": type(error).__name__, "message": str(error)}
        evidence["passed"] = False
    finally:
        (args.out_dir / "touch.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                                                 encoding="utf-8")
        print(json.dumps({"passed": evidence["passed"], "failure": evidence.get("failure"),
                          "taps": len(evidence["taps"]), "cleanup": evidence["cleanup"]}))
    if failure:
        raise failure


if __name__ == "__main__":
    main()
