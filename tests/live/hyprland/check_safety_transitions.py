"""Measure lock and activity safety transitions only in the disposable VM."""
from __future__ import annotations

import os

# These checks must precede all PcBridge, GUI, and device imports.
if not __debug__:
    raise RuntimeError("Safety acceptance requires assertions; Python -O is forbidden")
if os.environ.get("PCBRIDGE_TEST_HYPRLAND_SAFETY") != "1":
    raise RuntimeError("Set PCBRIDGE_TEST_HYPRLAND_SAFETY=1 in the disposable VM")
if os.uname().nodename != "pcbridge-hyprland":
    raise RuntimeError("Safety acceptance requires the disposable pcbridge-hyprland VM")
if os.environ.get("PCBRIDGE_NATIVE_BIN"):
    raise RuntimeError("Native helper overrides are forbidden")

import argparse
import asyncio
import dataclasses
import errno
import json
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from fastmcp import Client  # noqa: E402
from fastmcp.server.middleware import Middleware  # noqa: E402
from pcbridge.app import build_app  # noqa: E402
from pcbridge.config import load_config  # noqa: E402
from pcbridge.native import discover_native_binary  # noqa: E402
from pcbridge.desktop import glowstate, hyprland, idlewatch, monitors  # noqa: E402
from pcbridge.desktop.lease import LeaseStore  # noqa: E402
from tests.live.hyprland.check_glow_owner import layers, wait_for  # noqa: E402
from tests.live.test_input_parity import InputWindow  # noqa: E402

class RequestObserver(Middleware):
    """Observe the actual request ID without changing the tool pipeline."""
    def __init__(self):
        self.batch_request_id = None

    async def on_call_tool(self, context, call_next):
        if context.message.name == "computer_batch":
            self.batch_request_id = context.fastmcp_context.request_context.request_id
        return await call_next(context)

def result_data(result):
    return {"is_error": result.is_error, "structured_content": result.structured_content,
            "content": [item.model_dump(mode="json") for item in result.content]}

def error_code(result):
    assert result.is_error, result_data(result)
    return result.structured_content["error"]["code"]

def keyboard_devices():
    from evdev import InputDevice, list_devices
    found = []
    for path in list_devices():
        # evdev may open RW for metadata; the event reader uses only this RO replacement.
        device = InputDevice(path)
        try:
            readonly = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except BaseException:
            device.close()
            raise
        os.close(device.fd)
        device.fd = readonly
        if device.name == "pcbridge-keyboard":
            found.append(device)
        else:
            device.close()
    return found

class KernelObserver:
    """Read, never grab or write, the held key's unique kernel device."""
    def __init__(self):
        from evdev import ecodes
        devices = keyboard_devices()
        try:
            assert len(devices) == 1, [device.path for device in devices]
            self.device = devices[0]
            self.active = self.device.active_keys()
            assert ecodes.KEY_LEFTSHIFT in self.active, self.active
        except BaseException:
            for device in devices:
                device.close()
            raise
        self.events = []
        self.closed_at = None
        self.errors = []
        self.stop = threading.Event()
        self.armed = threading.Event()
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()
        assert self.armed.wait(2), "Kernel reader failed to arm"

    def read(self):
        from evdev import ecodes
        self.armed.set()
        while not self.stop.is_set():
            try:
                if select.select([self.device.fd], [], [], 0.05)[0]:
                    for event in self.device.read():
                        if event.type == ecodes.EV_KEY:
                            self.events.append({"at": time.monotonic(), "code": event.code,
                                                "value": event.value, "kernel_time": event.timestamp()})
                if not Path(self.device.path).exists():
                    self.closed_at = time.monotonic()
                    return
            except OSError as error:
                if error.errno == errno.ENODEV:
                    self.closed_at = time.monotonic()
                elif error.errno != errno.EAGAIN:
                    self.errors.append(str(error))
                if error.errno != errno.EAGAIN:
                    return

    def evidence(self):
        return {"path": self.device.path, "initial_active_keys": self.active,
                "events": list(self.events), "closed_at": self.closed_at, "errors": self.errors}

    def close(self):
        self.stop.set()
        self.thread.join(2)
        self.device.close()
        assert not self.thread.is_alive(), "Kernel reader did not stop"

def observer_geometry(window):
    assert window.process.poll() is None
    table = monitors.list_monitors(use_cache=False)
    clients = [c for c in hyprland._query("clients", json_output=True)
               if c.get("pid") == window.process.pid and c.get("mapped")]
    assert len(table) == len(clients) == 2
    assert all(c.get("visible") and c.get("fullscreen") == 2 for c in clients)
    for monitor in table:
        assert any(c["at"] == [monitor.x, monitor.y] and
                   c["size"] == [monitor.width, monitor.height] for c in clients)
    return {"pid": window.process.pid, "clients": clients,
            "monitors": [dataclasses.asdict(m) for m in table]}

def focus_verified(window):
    geometry = observer_geometry(window)
    assert hyprland._query("activewindow", json_output=True)["pid"] == window.process.pid
    return geometry

async def poll(predicate, description, timeout=8):
    return await asyncio.to_thread(wait_for, predicate, timeout=timeout, description=description)

async def key_events(window, key, mark):
    for kind in ("key_press", "key_release"):
        event = await asyncio.to_thread(window.wait, lambda e:
            e.get("event") == kind and e.get("keyval", "").lower() == key, 3, mark)
        assert event and event[1].get("shift") is False, window.since(mark)
    return window.since(mark)

def stop_process(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)

async def run(cfg, window, resources, evidence):
    mcp = task = kernel = None
    requests = RequestObserver()
    store = LeaseStore(cfg.state_dir)
    try:
        mcp, _ = build_app(cfg, transport="stdio")
        mcp.add_middleware(requests)
        async with Client(mcp, timeout=35) as client:
            async def call(name, args):
                return await asyncio.wait_for(client.call_tool(name, args, raise_on_error=False), 8)

            async def unlock(label):
                response = await call("desktop_unlock", {"minutes": 1,
                    "reason": f"Disposable VM safety {label}"})
                evidence[label] = result_data(response)
                assert not response.is_error, evidence[label]
                return store.snapshot()

            async def key(key, force=True):
                focus_verified(window)
                return await call("keyboard", {"action": "key", "keys": key, "force": force})

            async def reuse(old, sentinel):
                fresh = await unlock("fresh_unlock")
                assert fresh.token() != old.token() and fresh.grant_id != old.grant_id
                evidence["fresh_lease"] = dataclasses.asdict(fresh)
                clients = observer_geometry(window)["clients"]
                if hyprland._query("activewindow", json_output=True).get("pid") != window.process.pid:
                    response = await call("window_focus", {"window": "hyprland:" + clients[0]["address"], "force": True})
                    evidence["restore_focus"] = result_data(response)
                    assert not response.is_error
                evidence["fresh_target"] = focus_verified(window)
                mark = window.mark()
                response = await key(sentinel)
                evidence["fresh_key"] = result_data(response)
                assert not response.is_error
                evidence["fresh_events"] = await key_events(window, sentinel, mark)

            try:
                old = await unlock("initial_unlock")
                evidence["initial_lease"] = dataclasses.asdict(old)
                evidence["initial_layers"] = layers()
                assert len(evidence["initial_layers"]) == 8
                evidence["initial_target"] = focus_verified(window)
                caps = await call("system_capabilities", {})
                evidence["initial_capabilities"] = result_data(caps)
                assert not caps.is_error
                assert caps.structured_content["capabilities"]["input.keyboard"]["backend"] == "linux.uinput.native"
                if evidence["case"] == "lock":
                    from evdev import ecodes
                    mark = window.mark()
                    started = time.monotonic()
                    evidence["batch_started_at"] = started
                    focus_verified(window)
                    task = asyncio.create_task(client.call_tool("computer_batch", {
                        "actions": json.dumps([{"a": "hold", "keys": "shift"},
                            {"a": "wait", "ms": 3000}, {"a": "key", "keys": "a"}]),
                        "final": "none", "force": True}, raise_on_error=False))
                    down = await asyncio.to_thread(window.wait, lambda e:
                        e.get("event") == "key_press" and e.get("keyval", "").startswith("Shift"), 5, mark)
                    evidence["shift_down"] = down
                    assert down and requests.batch_request_id is not None
                    evidence["mcp_request_id"] = requests.batch_request_id
                    kernel = KernelObserver()
                    runtime = mcp._pcbridge_desktop_runtime
                    old_input_pid = runtime.input_provider._helper.client.pid
                    evidence["before_lock_resources"] = {"input_pid": old_input_pid,
                        "capture_open": runtime.capture_provider.is_open(), "at": time.monotonic()}
                    assert evidence["before_lock_resources"]["capture_open"] is True
                    initial_health = glowstate.read_on_current_outputs(cfg.state_dir, old.token(), binary=resources["binary"])
                    evidence["before_lock_frame_health"] = initial_health
                    assert initial_health is not None and old_input_pid is not None
                    owner_fields = ("pid", "writer_start_ticks", "owner_pid", "owner_start_ticks")
                    initial_owner = tuple(initial_health[field] for field in owner_fields)
                    config = cfg.state_dir / "hyprlock.conf"
                    config.write_text("animations {\n enabled = false\n}\nbackground {\n monitor =\n color = rgba(202020ff)\n}\n", encoding="utf-8")
                    trigger = time.monotonic()
                    evidence["lock_trigger_at"] = trigger
                    with (cfg.state_dir / "locker.stderr").open("w") as log:
                        resources["locker"] = subprocess.Popen(["hyprlock", "--config", str(config),
                            "--grace", "0", "--verbose"], stdout=log, stderr=log)
                    def presented():
                        assert resources["locker"].poll() is None, "Locker exited before presentation"
                        return "onLockLocked called" in (cfg.state_dir / "locker.stderr").read_text()
                    await poll(lambda: hyprland.screen_locked() is True, "authoritative locked session")
                    await poll(presented, "actual hyprlock locked callback")
                    resources["presented"] = True
                    evidence["locked_at"] = time.monotonic()
                    await poll(lambda: kernel.closed_at or any(e["code"] == ecodes.KEY_LEFTSHIFT and
                        e["value"] == 0 for e in kernel.events), "emergency kernel release or device removal", 4)
                    evidence["kernel"] = kernel.evidence()
                    releases = [e for e in kernel.events if e["code"] == ecodes.KEY_LEFTSHIFT and e["value"] == 0]
                    evidence["kernel_release_count"] = len(releases)
                    evidence["release_after_trigger_seconds"] = releases[0]["at"] - trigger if releases else None
                    evidence["device_removal_after_trigger_seconds"] = kernel.closed_at - trigger if kernel.closed_at else None
                    assert 0 <= (releases[0]["at"] if releases else kernel.closed_at) - trigger <= 1.5
                    assert not any(e["code"] == ecodes.KEY_A for e in kernel.events)
                    assert hyprland.screen_locked() is True
                    response = await call("keyboard", {"action": "key", "keys": "c", "force": True})
                    evidence["locked_keyboard"] = result_data(response)
                    assert error_code(response) == "SCREEN_LOCKED"
                    assert hyprland.screen_locked() is True
                    response = await call("desktop_unlock", {"minutes": 1, "reason": "Locked refusal"})
                    evidence["locked_unlock"] = result_data(response)
                    assert error_code(response) == "SCREEN_LOCKED"
                    caps = await call("system_capabilities", {})
                    evidence["locked_capabilities"] = result_data(caps)
                    assert caps.structured_content["authorization"]["screen_lock_state"] == "known_locked"
                    response = await asyncio.wait_for(asyncio.shield(task), 6)
                    evidence["batch_result"] = result_data(response)
                    assert error_code(response) == "SCREEN_LOCKED"
                    batch = response.structured_content["batch"]
                    assert batch["done"] == 2 and batch["total"] == 3 and batch["stopped"] == "safety"
                    await asyncio.sleep(max(0, started + 4 - time.monotonic()))
                    evidence["old_events"] = window.since(mark)
                    assert not any(e.get("keyval", "").lower() == "a" for _, e in window.since(mark))
                    # OS lock closes resources and withdraws presentation, without revoking the grant.
                    locked_lease = store.snapshot()
                    evidence["locked_lease"] = dataclasses.asdict(locked_lease)
                    assert locked_lease.is_active() and locked_lease.token() == old.token()
                    assert glowstate.read_on_current_outputs(cfg.state_dir, old.token(), binary=resources["binary"]) is None
                    raw_frame = json.loads((cfg.state_dir / glowstate.STATE_FILE).read_text())
                    evidence["locked_frame"] = {"at": time.monotonic(), "health": None,
                        "raw": raw_frame, "layers": layers()}
                    assert raw_frame["grant_id"] == old.grant_id and raw_frame["revoke_epoch"] == old.revoke_epoch
                    assert raw_frame["ready"] is False and raw_frame["presented_unix_ms"] == 0
                    assert tuple(raw_frame[field] for field in owner_fields) == initial_owner
                    assert len(evidence["locked_frame"]["layers"]) == 8
                    assert not runtime.capture_provider.is_open()
                    evidence["locked_capture_open"] = runtime.capture_provider.is_open()
                    locked_client = runtime.input_provider._helper.client
                    evidence["locked_input_pid"] = locked_client.pid if locked_client else None
                    assert evidence["locked_input_pid"] is None

                    devices = keyboard_devices()
                    try:
                        evidence["keyboard_devices_after_lock"] = [d.path for d in devices]
                        assert not devices
                    finally:
                        for device in devices:
                            device.close()
                    resources["locker"].send_signal(signal.SIGUSR1)
                    await asyncio.to_thread(resources["locker"].wait, 5)
                    await poll(lambda: hyprland.screen_locked() is False, "KnownUnlocked")
                    unlocked_unix_ms = int(time.time() * 1000)
                    def fresh_frame():
                        health = glowstate.read_on_current_outputs(cfg.state_dir, old.token(), binary=resources["binary"])
                        return health if health and health["presented_unix_ms"] >= unlocked_unix_ms else None
                    resumed = await poll(fresh_frame, "same grant fresh presentation")
                    evidence["resumed_frame"] = {"at": time.monotonic(), "unlocked_unix_ms": unlocked_unix_ms,
                        "health": resumed}
                    assert resumed["presented_unix_ms"] >= unlocked_unix_ms
                    assert tuple(resumed[field] for field in owner_fields) == initial_owner
                    resumed_lease = store.snapshot()
                    evidence["resumed_lease"] = dataclasses.asdict(resumed_lease)
                    assert resumed_lease.is_active() and resumed_lease.token() == old.token()
                    clients = observer_geometry(window)["clients"]
                    if hyprland._query("activewindow", json_output=True).get("pid") != window.process.pid:
                        response = await call("window_focus", {"window": "hyprland:" + clients[0]["address"], "force": True})
                        evidence["same_grant_focus"] = result_data(response)
                        assert not response.is_error
                    keymark = window.mark()
                    response = await key("b")
                    evidence["same_grant_key"] = result_data(response)
                    assert not response.is_error
                    evidence["same_grant_b_events"] = await key_events(window, "b", keymark)
                    new_client = runtime.input_provider._helper.client
                    evidence["resumed_input_pid"] = new_client.pid if new_client else None
                    assert new_client and new_client.pid is not None and new_client.pid != old_input_pid
                    assert not any(e.get("keyval", "").lower() == "a" for _, e in window.since(mark))
                    await reuse(old, "c")
                    evidence["kernel_final"] = kernel.evidence()
                    assert not any(e["code"] == ecodes.KEY_A for e in kernel.events)
                else:
                    mark = window.mark()
                    response = await key("b")
                    assert not response.is_error, result_data(response)
                    evidence["b_events"] = await key_events(window, "b", mark)
                    await poll(lambda: (v := idlewatch.read_idle_ms()) is not None and v <= 1000, "own input idle reset", 3)
                    evidence["idle_after_b"] = idlewatch.read_idle_ms()
                    mark = window.mark()
                    response = await key("c", False)
                    evidence["active_refusal"] = result_data(response)
                    assert error_code(response) == "USER_ACTIVE"
                    await asyncio.sleep(0.2)
                    assert not any(e.get("keyval", "").lower() == "c" for _, e in window.since(mark))
                    response = await key("c")
                    assert not response.is_error
                    evidence["forced_c_events"] = await key_events(window, "c", mark)
                    await poll(lambda: (v := idlewatch.read_idle_ms()) is not None and v >= 2000, "real idle admission")
                    evidence["idle_before_sequence"] = idlewatch.read_idle_ms()
                    mark = window.mark()
                    focus_verified(window)
                    task = asyncio.create_task(client.call_tool("computer_batch", {"actions": json.dumps([
                        {"a": "key", "keys": "d"}, {"a": "wait", "ms": 1000}, {"a": "key", "keys": "e"}]),
                        "final": "none", "force": False}, raise_on_error=False))
                    down = await asyncio.to_thread(window.wait, lambda e: e.get("event") == "key_press" and
                        e.get("keyval", "").lower() == "d", 3, mark)
                    assert down
                    # Allow one 500ms native heartbeat to publish, while the 1s wait is still pending.
                    await poll(lambda: (v := idlewatch.read_idle_ms()) is not None and v <= 1000,
                               "recent idle during batch wait", 0.8)
                    evidence["idle_during_sequence"] = {"at": time.monotonic(), "idle_ms": idlewatch.read_idle_ms(), "batch_done": task.done()}
                    assert not task.done(), "Recent activity must be observed during the wait"
                    response = await asyncio.wait_for(asyncio.shield(task), 5)
                    evidence["batch_result"] = result_data(response)
                    completed_text = "\n".join(getattr(block, "text", "") for block in response.content)
                    evidence["batch_completion_text"] = completed_text
                    assert not response.is_error and "**3 of 3 actions done**" in completed_text
                    evidence["d_events"] = await key_events(window, "d", mark)
                    evidence["e_events"] = await key_events(window, "e", mark)
                    await asyncio.to_thread(stop_process, resources["idle"])
                    await poll(lambda: idlewatch.read_idle_ms() is None, "unknown idle after own watcher exit")
                    mark = window.mark()
                    response = await key("f")
                    evidence["unknown_keyboard"] = result_data(response)
                    code = error_code(response)
                    snapshot = store.snapshot()
                    evidence["unknown_lease"] = dataclasses.asdict(snapshot)
                    assert code == "ACTIVITY_UNKNOWN" or (code in ("GRANT_REQUIRED", "REVOKED", "BACKEND_UNAVAILABLE") and not snapshot.is_active())
                    evidence["unknown_refusal_reason"] = "activity unknown" if code == "ACTIVITY_UNKNOWN" else "normal cleanup already retired the grant"
                    response = await call("desktop_unlock", {"minutes": 1, "reason": "Unknown activity refusal"})
                    evidence["unknown_unlock"] = result_data(response)
                    assert error_code(response) == "ACTIVITY_UNKNOWN"
                    await asyncio.sleep(0.5)
                    evidence["unknown_phase_events"] = window.since(mark)
                    assert not any(e.get("keyval", "").lower() == "f" for _, e in window.since(mark))
                    resources["idle"] = start_idle(resources["binary"], cfg.state_dir)
                    await poll(lambda: idlewatch.read_idle_ms() is not None, "fresh restarted idle record")
                    evidence["restarted_idle_ms"] = idlewatch.read_idle_ms()
                    await reuse(old, "f")
            finally:
                try:
                    response = await call("desktop_lock", {})
                    evidence["final_lock"] = result_data(response)
                    assert not response.is_error, evidence["final_lock"]
                except BaseException as error:
                    evidence.setdefault("cleanup_errors", []).append(f"desktop_lock: {error}")
                if task and not task.done():
                    try:
                        assert requests.batch_request_id is not None, "No actual pending batch request ID"
                        await asyncio.wait_for(client.cancel(requests.batch_request_id, reason="Safety probe cleanup"), 2)
                        evidence["cleanup_cancel_request_id"] = requests.batch_request_id
                    except BaseException as error:
                        evidence.setdefault("cleanup_errors", []).append(f"MCP cancel: {error}")
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, 2)
                    except asyncio.CancelledError:
                        pass
                    except BaseException as error:
                        evidence.setdefault("cleanup_errors", []).append(f"batch waiter cleanup: {error}")
    finally:
        if kernel:
            evidence["kernel_cleanup"] = kernel.evidence()
            try:
                kernel.close()
            except BaseException as error:
                evidence.setdefault("cleanup_errors", []).append(f"kernel.close: {error}")
        if mcp:
            try:
                await asyncio.wait_for(asyncio.to_thread(mcp._pcbridge_desktop_runtime.close), 8)
            except BaseException as error:
                evidence.setdefault("cleanup_errors", []).append(f"runtime.close: {error}")

def start_idle(binary, directory):
    with (directory / "idle.stderr").open("a") as log:
        return subprocess.Popen([str(binary), "idle-watch"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=log)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=["lock", "activity"], required=True)
    args = parser.parse_args()
    evidence = {"case": args.case}
    resources = {}
    window = None
    with tempfile.TemporaryDirectory(prefix="pcbridge-safety-") as temporary:
        directory = Path(temporary)
        try:
            assert hyprland.screen_locked() is False
            assert not layers(), "Refuse overlapping grant frame"
            assert idlewatch.read_idle_ms() is None, "Refuse overlapping idle writer"
            devices = keyboard_devices()
            try:
                assert not devices, "Refuse overlapping native keyboard"
            finally:
                for device in devices:
                    device.close()
            base = load_config(ROOT / "config.example.toml", check_state=False)
            assert base.native.binary_path is None
            binary = discover_native_binary(base.native)
            assert binary.is_file() and binary.is_relative_to(ROOT / "pcbridge/_native")
            info = json.loads(subprocess.run([str(binary), "--build-info"], check=True,
                capture_output=True, text=True, timeout=5).stdout)
            assert info["profile"] == "release" and info["test_harness"] is False
            resources["binary"] = binary
            evidence.update(helper=str(binary), build=info)
            cfg = dataclasses.replace(base, state_dir=directory, desktop=dataclasses.replace(
                base.desktop, enabled=True, agent_shot_dir=str(directory / "shots"),
                unlock_notification=False, unlock_idle_seconds=30, hold_max_seconds=20,
                batch_budget_seconds=25, idle_guard_seconds=2))
            cfg.jobs_dir.mkdir(parents=True)
            (directory / "shots").mkdir()
            window = InputWindow(directory / "observer.stderr", timeout=120, details=True)
            ready = window.wait(lambda e: e.get("event") == "ready", 20)
            assert ready
            window.settle()
            evidence["observer_ready"] = ready
            evidence["observer_geometry"] = focus_verified(window)
            assert ready[1]["monitors"] == [[m.x, m.y, m.width, m.height]
                for m in monitors.list_monitors(use_cache=False)]
            resources["idle"] = start_idle(binary, directory)
            wait_for(lambda: idlewatch.read_idle_ms() is not None, description="fresh packaged idle watcher")
            asyncio.run(run(cfg, window, resources, evidence))
        except BaseException as error:
            evidence["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            cleanups = []
            if resources.get("idle"):
                cleanups.append(lambda: stop_process(resources["idle"]))
            if window:
                cleanups.append(window.close)
            if resources.get("locker") and resources["locker"].poll() is None:
                def unlock_locker():
                    wait_for(lambda: "onLockLocked called" in (directory / "locker.stderr").read_text(),
                             description="cleanup locker presented", timeout=5)
                    resources["locker"].send_signal(signal.SIGUSR1)
                    resources["locker"].wait(timeout=5)
                    wait_for(lambda: hyprland.screen_locked() is False, description="cleanup KnownUnlocked")
                cleanups.append(unlock_locker)
            cleanups.append(lambda: wait_for(lambda: not layers(), description="final frame teardown"))
            for cleanup in cleanups:
                try:
                    cleanup()
                except BaseException as error:
                    evidence.setdefault("cleanup_errors", []).append(f"{type(error).__name__}: {error}")
            try:
                evidence["final_state"] = {"screen_locked": hyprland.screen_locked(),
                    "idle_ms": idlewatch.read_idle_ms(), "layers": layers()}
                assert evidence["final_state"] == {"screen_locked": False, "idle_ms": None, "layers": []}
            except BaseException as error:
                evidence.setdefault("cleanup_errors", []).append(f"final state: {error}")
            for name in ("observer", "idle", "locker"):
                path = directory / f"{name}.stderr"
                if path.exists():
                    evidence[f"{name}_stderr_tail"] = path.read_text(errors="replace")[-8000:]
            print(json.dumps(evidence, sort_keys=True, default=str), flush=True)
            if evidence.get("cleanup_errors") and "error" not in evidence:
                raise RuntimeError(f"Safety cleanup failed: {evidence['cleanup_errors']}")

if __name__ == "__main__":
    main()
