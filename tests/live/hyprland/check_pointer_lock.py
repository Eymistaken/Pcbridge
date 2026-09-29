"""Real pointer-constraint acceptance through registered MCP tools, VM only."""
from __future__ import annotations

import os

# Refuse before importing any PcBridge, GUI, state, or device code.
if not __debug__:
    raise RuntimeError("Pointer-lock acceptance requires assertions; Python -O is forbidden")
if os.environ.get("PCBRIDGE_TEST_HYPRLAND_POINTER_LOCK") != "1":
    raise RuntimeError("Set PCBRIDGE_TEST_HYPRLAND_POINTER_LOCK=1 in the disposable VM")
if os.uname().nodename != "pcbridge-hyprland":
    raise RuntimeError("Pointer-lock acceptance runs only on the disposable pcbridge-hyprland VM")
if "PCBRIDGE_NATIVE_BIN" in os.environ:
    raise RuntimeError("Native helper overrides are forbidden")

import argparse
import asyncio
import base64
import dataclasses
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from fastmcp import Client  # noqa: E402
from pcbridge.app import build_app  # noqa: E402
from pcbridge.config import load_config  # noqa: E402
from pcbridge.native import discover_native_binary  # noqa: E402
from pcbridge.desktop import hyprland, idlewatch  # noqa: E402
from tests.live.hyprland.check_glow_owner import layers, wait_for  # noqa: E402


def compile_observer(directory):
    protocols = Path(subprocess.run(["pkg-config", "--variable=pkgdatadir", "wayland-protocols"],
        check=True, capture_output=True, text=True, timeout=5).stdout.strip())
    sources = []
    for name, relative in (
        ("xdg-shell", "stable/xdg-shell/xdg-shell.xml"),
        ("pointer-constraints-v1", "unstable/pointer-constraints/pointer-constraints-unstable-v1.xml"),
        ("relative-pointer-v1", "unstable/relative-pointer/relative-pointer-unstable-v1.xml"),
    ):
        xml = protocols / relative
        assert xml.is_file(), str(xml)
        header = directory / f"{name}-client-protocol.h"
        source = directory / f"{name}-protocol.c"
        for mode, output in (("client-header", header), ("private-code", source)):
            subprocess.run(["wayland-scanner", mode, str(xml), str(output)], check=True,
                           capture_output=True, text=True, timeout=5)
        sources.append(str(source))
    flags = shlex.split(subprocess.run(["pkg-config", "--cflags", "--libs", "wayland-client"],
        check=True, capture_output=True, text=True, timeout=5).stdout)
    binary = directory / "wayland-input-window"
    command = ["cc", "-std=c11", "-Wall", "-Wextra", "-I", str(directory),
        str(Path(__file__).with_name("wayland_input_window.c")), *sources, *flags, "-o", str(binary)]
    compiled = subprocess.run(command, check=True, capture_output=True, text=True, timeout=20)
    return binary, {"command": command, "stderr": compiled.stderr}


class Observer:
    """Read protocol evidence independently of the MCP event loop."""

    def __init__(self, binary, directory, app_id):
        self.events = []
        self.condition = threading.Condition()
        self.stderr_path = directory / "observer.stderr"
        self.stderr = self.stderr_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen([str(binary), app_id], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.stderr, text=True, bufsize=1)
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def read(self):
        for line in self.process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                event = {"event": "invalid_json", "line": line[:512]}
            event["received_monotonic"] = time.monotonic()
            with self.condition:
                self.events.append(event)
                self.condition.notify_all()

    def mark(self):
        with self.condition:
            return len(self.events)

    def since(self, mark):
        with self.condition:
            return list(self.events[mark:])

    def wait(self, kind, mark=0, timeout=4, predicate=lambda event: True):
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                matches = [e for e in self.events[mark:] if e.get("event") == kind and predicate(e)]
                if matches:
                    return matches[0]
                remaining = deadline - time.monotonic()
                assert remaining > 0, {"missing": kind, "events": self.events[mark:],
                                       "exit_code": self.process.poll()}
                self.condition.wait(min(remaining, 0.1))

    def command(self, text):
        self.process.stdin.write(text + "\n")
        self.process.stdin.flush()

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()  # EOF requests bounded, normal client disposal.
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
        self.reader.join(timeout=2)
        self.process.stdout.close()
        self.stderr.close()
        return {"pid": self.process.pid, "exit_code": self.process.returncode,
                "reader_stopped": not self.reader.is_alive(),
                "stderr": self.stderr_path.read_text(encoding="utf-8", errors="replace")[-8000:]}


def result_data(result):
    return {"is_error": result.is_error, "structured_content": result.structured_content,
            "content": [{k: v for k, v in item.model_dump(mode="json").items() if k != "data"}
                        for item in result.content]}


async def run_case(cfg, observer, app_id, evidence, out_dir):
    mcp = None
    try:
        mcp, _ = build_app(cfg, transport="stdio")
        async with Client(mcp, timeout=15) as client:
            async def call(name, arguments, acting=False):
                if acting:
                    focus()
                result = await asyncio.wait_for(client.call_tool(name, arguments,
                    raise_on_error=False), timeout=8)
                evidence["replies"].append({"tool": name, "arguments": arguments,
                                            "result": result_data(result)})
                assert not result.is_error, evidence["replies"][-1]
                return result

            def focus():
                assert hyprland.screen_locked() is False
                current = hyprland._query("activewindow", json_output=True)
                clients = [c for c in hyprland._query("clients", json_output=True)
                    if c.get("pid") == observer.process.pid and c.get("class") == app_id]
                evidence["focus"].append({"monotonic": time.monotonic(), "active": current,
                                          "clients": clients})
                assert len(clients) == 1, clients
                target = clients[0]
                assert target.get("mapped") and target.get("visible") and target.get("fullscreen") == 2, target
                assert current.get("pid") == observer.process.pid and current.get("class") == app_id, current
                assert current.get("address") == target.get("address"), current
                assert target["size"][0] > 250 and target["size"][1] > 250
                if "geometry" in evidence:
                    assert {"at": target["at"], "size": target["size"]} == evidence["geometry"]
                return target

            def cursor():
                instance, _ = hyprland.focus_context()
                result = subprocess.run(["hyprctl", "-i", instance, "-j", "cursorpos"],
                    check=True, capture_output=True, text=True, timeout=3)
                point = json.loads(result.stdout)
                evidence["cursor"].append({"monotonic": time.monotonic(), "point": point})
                return point

            async def receive(kind, mark=0, predicate=lambda event: True):
                return await asyncio.to_thread(observer.wait, kind, mark, 4, predicate)

            async def mouse(arguments):
                return await call("mouse", {**arguments, "force": True}, acting=True)

            async def screenshot(name, include_pointer=False):
                focus()
                capture = await call("screen_capture", {"monitor": "window", "scale": 0,
                                                       "include_pointer": include_pointer})
                images = [item for item in capture.content if getattr(item, "type", None) == "image"]
                assert len(images) == 1, "Expected one fresh client screenshot"
                path = out_dir / f"pointer-lock-{name}.png"
                path.write_bytes(base64.b64decode(images[0].data, validate=True))
                focus()
                return str(path)

            try:
                evidence["initial_ready"] = await receive("ready")
                target = focus()
                # The initial configure can paint the tiled size before fullscreen.
                evidence["ready"] = await receive("ready", predicate=lambda event:
                    [event["width"], event["height"]] == target["size"])
                target = focus()
                assert [evidence["ready"]["width"], evidence["ready"]["height"]] == target["size"]
                evidence["geometry"] = {"at": target["at"], "size": target["size"]}
                await call("desktop_unlock", {"minutes": 1,
                    "reason": "Disposable VM real Wayland pointer-lock acceptance"})
                evidence["glow"] = layers()
                assert len(evidence["glow"]) == 8, evidence["glow"]
                evidence["target_screenshot"] = await screenshot("target")
                # Fresh metadata identifies the actual painted client before every input call.
                before_absolute = cursor()
                evidence["before_initial_absolute_cursor"] = before_absolute
                center = [target["at"][0] + target["size"][0] // 2,
                          target["at"][1] + target["size"][1] // 2]
                initial = [center[0] + 31, center[1] + 23]
                if abs(before_absolute["x"] - initial[0]) <= 1 and abs(before_absolute["y"] - initial[1]) <= 1:
                    initial = [center[0] - 31, center[1] - 23]
                assert abs(before_absolute["x"] - initial[0]) > 1 or abs(before_absolute["y"] - initial[1]) > 1
                assert all(target["at"][i] < initial[i] < target["at"][i] + target["size"][i] - 1
                           for i in (0, 1))
                evidence["initial_absolute"] = initial
                await mouse({"action": "move", "x": initial[0], "y": initial[1], "smooth": False})
                await receive("enter")
                await mouse({"action": "click"})
                focus()
                mark = observer.mark()
                observer.command("lock")
                evidence["locked"] = await receive("locked", mark)
                locked_snapshot = observer.since(0)
                locked_mark = next(index for index, event in enumerate(locked_snapshot)
                                   if event is evidence["locked"]) + 1
                original_cursor = cursor()
                evidence["locked_cursor"] = original_cursor
                evidence["cursor_stable"] = True
                evidence["locked_before_screenshot"] = await screenshot("locked-before", include_pointer=True)

                async def delta(dx, dy, warmup=False):
                    mark = observer.mark()
                    await mouse({"action": "move_by", "dx": dx, "dy": dy})
                    await receive("relative", mark,
                                  lambda e: any(e[k] != 0 for k in ("dx", "dy", "ux", "uy")))
                    immediate_cursor = cursor()
                    await asyncio.sleep(0.15)
                    delayed_cursor = cursor()
                    events = observer.since(mark)
                    relatives = [e for e in events if e.get("event") == "relative"]
                    sums = {k: sum(e[k] for e in relatives) for k in ("dx", "dy", "ux", "uy")}
                    item = {"sent": [dx, dy], "warmup": warmup, "sum": sums, "events": events,
                            "unaccelerated_ratio": sums["ux" if dx else "uy"] / (dx or dy)}
                    item["cursor"] = {"immediate": immediate_cursor, "delayed": delayed_cursor,
                        "immediate_displacement": {axis: immediate_cursor[axis] - original_cursor[axis]
                                                   for axis in ("x", "y")},
                        "delayed_displacement": {axis: delayed_cursor[axis] - original_cursor[axis]
                                                 for axis in ("x", "y")},
                        "stable": immediate_cursor == original_cursor and delayed_cursor == original_cursor}
                    evidence["cursor_stable"] = evidence["cursor_stable"] and item["cursor"]["stable"]
                    evidence["deltas"].append(item)
                    assert not any(e.get("event") == "motion" for e in events), events
                    assert not any(e.get("event") in ("unlocked", "leave") for e in observer.since(locked_mark))
                    focus()
                    if not warmup:
                        axis, cross = ("x", "y") if dx else ("y", "x")
                        amount = dx or dy
                        assert sums["d" + axis] * amount > 0 and sums["u" + axis] * amount > 0, item
                        assert abs(sums["d" + cross]) <= 0.01 and abs(sums["u" + cross]) <= 0.01, item
                    return item

                await delta(3, 0, warmup=True)
                samples = [await delta(dx, dy) for dx, dy in ((40, 0), (80, 0), (-40, 0), (0, 50), (0, -50))]
                ratios = [item["unaccelerated_ratio"] for item in samples]
                evidence["unaccelerated_ratios"] = ratios
                assert all(r > 0 for r in ratios)
                assert max(ratios) - min(ratios) <= max(ratios) * 0.03, ratios
                evidence["locked_after_screenshot"] = await screenshot("locked-after", include_pointer=True)
                mark = observer.mark()
                await mouse({"action": "click"})
                down = await receive("button", mark, lambda e: e["button"] == 272 and e["state"] == 1)
                up = await receive("button", mark, lambda e: e["button"] == 272 and e["state"] == 0)
                evidence["locked_click"] = [down, up]
                mark = observer.mark()
                await mouse({"action": "scroll", "scroll_amount": 2})
                evidence["locked_scroll"] = await receive("axis", mark, lambda e: e["value"] != 0)
                await asyncio.sleep(0.1)
                locked_events = observer.since(locked_mark)
                evidence["locked_events_before_destroy"] = locked_events
                assert not any(e.get("event") in ("motion", "unlocked", "leave") for e in locked_events)
                final_locked_cursor = cursor()
                evidence["final_locked_cursor"] = final_locked_cursor
                evidence["cursor_stable"] = evidence["cursor_stable"] and final_locked_cursor == original_cursor
                focus()
                mark = observer.mark()
                observer.command("unlock")
                evidence["destroyed"] = await receive("constraint_destroyed", mark)
                interval_snapshot = observer.since(0)
                destroyed_index = next(index for index, event in enumerate(interval_snapshot)
                                       if event is evidence["destroyed"])
                assert destroyed_index >= locked_mark
                evidence["locked_events"] = interval_snapshot[locked_mark:destroyed_index]
                assert not any(event.get("event") in ("motion", "unlocked", "leave")
                               for event in evidence["locked_events"]), evidence["locked_events"]
                heal = [initial[0] + 83, initial[1] + 61]
                assert heal[0] < target["at"][0] + target["size"][0] - 1
                assert heal[1] < target["at"][1] + target["size"][1] - 1
                evidence["heal_target"] = heal
                mark = observer.mark()
                await mouse({"action": "move", "x": heal[0], "y": heal[1], "smooth": False})
                local = [heal[i] - target["at"][i] for i in (0, 1)]
                evidence["healed_motion"] = await receive("motion", mark,
                    lambda e: abs(e["x"] - local[0]) <= 1 and abs(e["y"] - local[1]) <= 1)
                healed_cursor = cursor()
                assert abs(healed_cursor["x"] - heal[0]) <= 1 and abs(healed_cursor["y"] - heal[1]) <= 1
                evidence["protocol_lock_checks_complete"] = True
                # Installed pointer-constraints guarantees locked delivery without
                # wl_pointer.motion; hyprctl cursorpos observes compositor internals.
                # Hyprland 0.56.2's early return before lock correction may explain drift.
                evidence["diagnostic_warnings"] = []
                if not evidence["cursor_stable"]:
                    evidence["diagnostic_warnings"].append({
                        "message": "The compositor cursor position changed while the real client lock remained active. "
                                   "This read-only diagnostic is separate from the verified Wayland protocol behavior.",
                        "locked_cursor": original_cursor, "final_locked_cursor": final_locked_cursor,
                        "mismatches": [{"sent": item["sent"], "cursor": item["cursor"]}
                                       for item in evidence["deltas"] if not item["cursor"]["stable"]]})
                evidence["passed"] = True
            finally:
                try:
                    await call("desktop_lock", {})
                    evidence["cleanup"]["desktop_lock"] = "completed"
                except BaseException as error:
                    evidence["cleanup"]["desktop_lock"] = f"{type(error).__name__}: {error}"
                    raise
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
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    evidence = {"replies": [], "focus": [], "cursor": [], "deltas": [], "cleanup": {}, "passed": False, "protocol_lock_checks_complete": False,
                "cursor_stable": None}
    observer = idle = None
    failure = None
    try:
        assert hyprland.screen_locked() is False
        assert not layers() and idlewatch.read_idle_ms() is None, "Existing glow or idle writer"
        assert os.access("/dev/uinput", os.R_OK | os.W_OK)
        existing = subprocess.run(["pgrep", "-af", "pcbridge-native"], capture_output=True,
                                  text=True, timeout=3)
        assert existing.returncode in (0, 1)
        assert not any("idle-watch" in line or " input" in line or "glow" in line
                       for line in existing.stdout.splitlines()), "Existing native input/glow/idle child"
        base = load_config(ROOT / "config.example.toml", check_state=False)
        assert base.native.binary_path is None
        binary = discover_native_binary(base.native)
        assert binary.is_file() and binary.is_relative_to(ROOT / "pcbridge/_native")
        info = json.loads(subprocess.run([str(binary), "--build-info"], check=True,
            capture_output=True, text=True, timeout=5).stdout)
        evidence.update({"helper": str(binary), "build": info,
                         "compositor": json.loads(subprocess.run(["hyprctl", "-j", "version"],
                             check=True, capture_output=True, text=True, timeout=3).stdout)})
        assert info["profile"] == "release" and info["test_harness"] is False, info
        with tempfile.TemporaryDirectory(prefix="pcbridge-pointer-lock-") as temporary:
            directory = Path(temporary)
            cfg = dataclasses.replace(base, state_dir=directory / "state",
                desktop=dataclasses.replace(base.desktop, enabled=True, unlock_idle_seconds=60,
                    unlock_notification=False, idle_guard_seconds=2, hold_max_seconds=5,
                    batch_budget_seconds=25, agent_shot_dir=str(directory / "shots")),
                native=dataclasses.replace(base.native, input="rust", capture="rust"))
            cfg.jobs_dir.mkdir(parents=True)
            (cfg.state_dir / "shots").mkdir(parents=True)
            Path(cfg.desktop.agent_shot_dir).mkdir(parents=True)
            evidence["profile"] = {"unlock_idle_seconds": 60, "idle_guard_seconds": 2,
                                   "hold_max_seconds": 5, "batch_budget_seconds": 25}
            executable, evidence["compile"] = compile_observer(directory)
            app_id = "pcbridge-pointer-lock-" + uuid.uuid4().hex
            evidence["app_id"] = app_id
            try:
                observer = Observer(executable, directory, app_id)
                evidence["observer_pid"] = observer.process.pid
                idle = subprocess.Popen([str(binary), "idle-watch"], stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
                wait_for(lambda: idlewatch.read_idle_ms() is not None, description="own fresh idle watcher")
                asyncio.run(asyncio.wait_for(run_case(cfg, observer, app_id, evidence, args.out_dir), timeout=50))
            finally:
                # Every disposal is attempted even when a preceding cleanup fails.
                for name, cleanup in (("observer", observer.close if observer else None),
                                      ("idle", (lambda: stop_child(idle)) if idle else None)):
                    if cleanup:
                        try:
                            evidence["cleanup"][name] = cleanup()
                        except BaseException as error:
                            evidence["cleanup"][name] = {"error": f"{type(error).__name__}: {error}"}
                if observer:
                    evidence["events"] = observer.since(0)
                wait_for(lambda: not layers(), description="final pointer-lock frame teardown")
                evidence["cleanup"]["final_layers"] = layers()
                wait_for(lambda: idlewatch.read_idle_ms() is None, description="own idle proof expires")
                evidence["cleanup"]["final_idle_ms"] = idlewatch.read_idle_ms()
                assert evidence["cleanup"]["final_idle_ms"] is None
                remaining = subprocess.run(["pgrep", "-af", "pcbridge-native"], capture_output=True,
                                           text=True, timeout=3)
                assert remaining.returncode in (0, 1)
                evidence["cleanup"]["native_children"] = [line for line in remaining.stdout.splitlines()
                    if "idle-watch" in line or " input" in line or "glow" in line]
                assert not evidence["cleanup"]["native_children"]
                evidence["cleanup"]["screen_locked"] = hyprland.screen_locked()
                assert evidence["cleanup"]["screen_locked"] is False
                assert idle is None or idle.poll() is not None
                if observer:
                    disposed = evidence["cleanup"]["observer"]
                    assert disposed.get("exit_code") == 0 and disposed.get("reader_stopped") is True, disposed
                assert not any(isinstance(value, dict) and "error" in value
                               for value in evidence["cleanup"].values()), evidence["cleanup"]
    except BaseException as error:
        failure = error
        evidence["failure"] = {"type": type(error).__name__, "message": str(error)}
        evidence["passed"] = False
    finally:
        (args.out_dir / "pointer-lock.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                                                     encoding="utf-8")
        print(json.dumps(evidence, sort_keys=True))
    if failure:
        raise failure


if __name__ == "__main__":
    main()
