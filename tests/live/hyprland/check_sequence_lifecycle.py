"""Observe real MCP sequence lifecycle in the disposable Hyprland VM."""
from __future__ import annotations

import os

# Refuse before importing PcBridge, GUI observers, or touching state/devices.
if not __debug__:
    raise RuntimeError("Sequence acceptance requires assertions; Python -O is forbidden")
if os.environ.get("PCBRIDGE_TEST_HYPRLAND_SEQUENCE") != "1":
    raise RuntimeError("Set PCBRIDGE_TEST_HYPRLAND_SEQUENCE=1 in the disposable VM")
if os.uname().nodename != "pcbridge-hyprland":
    raise RuntimeError("Sequence acceptance runs only on the disposable pcbridge-hyprland VM")
if os.environ.get("PCBRIDGE_NATIVE_BIN"):
    raise RuntimeError("Native helper overrides are forbidden")

import argparse
import asyncio
import dataclasses
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from fastmcp import Client  # noqa: E402
from fastmcp.server.middleware import Middleware  # noqa: E402
from pcbridge.app import build_app  # noqa: E402
from pcbridge.config import load_config  # noqa: E402
from pcbridge.native import discover_native_binary  # noqa: E402
from pcbridge.desktop.lease import LeaseStore  # noqa: E402
from pcbridge.desktop import hyprland, idlewatch, monitors  # noqa: E402
from tests.live.hyprland.check_glow_owner import layers, wait_for  # noqa: E402
from tests.live.test_input_parity import InputWindow  # noqa: E402


class RequestObserver(Middleware):
    """Read the real wire request ID without altering the normal tool pipeline."""

    def __init__(self):
        self.batch_request_id = None

    async def on_call_tool(self, context, call_next):
        if context.message.name == "computer_batch":
            self.batch_request_id = context.fastmcp_context.request_context.request_id
        return await call_next(context)


def result_data(result):
    return {"is_error": result.is_error, "data": result.data,
            "structured_content": result.structured_content,
            "content": [item.model_dump(mode="json") for item in result.content]}


async def run_case(cfg, window, evidence):
    mcp = None
    task = None
    try:
        mcp, _ = build_app(cfg, transport="stdio")
        requests = RequestObserver()
        mcp.add_middleware(requests)
        async with Client(mcp, timeout=35) as client:
            async def call(name, arguments):
                return await asyncio.wait_for(client.call_tool(
                    name, arguments, raise_on_error=False), timeout=8)

            try:
                unlock = await call("desktop_unlock", {"minutes": 1,
                    "reason": f"Disposable VM MCP {evidence['case']} acceptance"})
                evidence["unlock"] = result_data(unlock)
                assert not unlock.is_error, evidence["unlock"]
                initial_layers = layers()
                evidence["initial_glow_layers"] = initial_layers
                assert len(initial_layers) == 8, "Two outputs must have eight visible grant strips"
                assert hyprland.screen_locked() is False
                assert hyprland._query("activewindow", json_output=True)["pid"] == window.process.pid
                capabilities = await call("system_capabilities", {})
                evidence["capabilities"] = result_data(capabilities)
                assert not capabilities.is_error
                keyboard = capabilities.structured_content["capabilities"]["input.keyboard"]
                assert keyboard["backend"] == "linux.uinput.native", keyboard
                if evidence["case"] != "cancellation":
                    await lifecycle_case(client, call, cfg, window, evidence, requests)
                else:
                    mark = window.mark()
                    started = time.monotonic()
                    task = asyncio.create_task(client.call_tool("computer_batch", {
                        "actions": json.dumps([{"a": "hold", "keys": "shift"},
                            {"a": "wait", "ms": 3000}, {"a": "key", "keys": "a"}]),
                        "final": "none", "force": True}, raise_on_error=False))
                    down = await asyncio.to_thread(window.wait, lambda e:
                        e.get("event") == "key_press" and e.get("keyval", "").startswith("Shift"),
                        5, mark)
                    evidence["shift_down"] = down
                    if task.done() and not task.cancelled():
                        evidence["batch_result"] = result_data(task.result())
                    assert down, {"events": window.since(mark), "evidence": evidence}
                    canceled = time.monotonic()
                    evidence["cancel_after_start_seconds"] = canceled - started
                    assert requests.batch_request_id is not None
                    evidence["mcp_request_id"] = requests.batch_request_id
                    await asyncio.wait_for(client.cancel(requests.batch_request_id,
                        reason="Disposable VM sequence cancellation acceptance"), timeout=2)
                    evidence["mcp_cancel_notification_sent"] = True
                    task.cancel()  # Dispose the local waiter after the real protocol notification.
                    try:
                        await asyncio.wait_for(task, timeout=2)
                    except asyncio.CancelledError:
                        evidence["client_canceled_error"] = True
                    else:
                        raise AssertionError("Client call did not raise CancelledError")
                    # The MCP client/server stay alive through the original batch deadline.
                    # Observation runs in a thread so MCP cancellation can be processed.
                    up = await asyncio.to_thread(window.wait, lambda e:
                        e.get("event") == "key_release" and e.get("keyval", "").startswith("Shift"),
                        4.5, mark)
                    remaining = canceled + 4.5 - time.monotonic()
                    if remaining > 0:
                        await asyncio.sleep(remaining)
                    evidence["observed_after_cancel_seconds"] = time.monotonic() - canceled
                    evidence["shift_release"] = up
                    evidence["release_after_cancel_seconds"] = up[0] - canceled if up else None
                    assert hyprland._query("activewindow", json_output=True)["pid"] == window.process.pid
                    evidence["events"] = window.since(mark)
                    evidence["sentinel_events"] = [(stamp, event) for stamp, event in window.since(mark)
                        if event.get("event") in ("key_press", "key_release")
                        and event.get("keyval") in ("a", "A")]
                    capabilities = await call("system_capabilities", {})
                    evidence["capabilities_after_cancel"] = result_data(capabilities)
                    assert not capabilities.is_error, evidence["capabilities_after_cancel"]
                    assert up and 0 <= up[0] - canceled <= 1, "Canceled MCP batch did not release Shift within 1 second"
                    assert not evidence["sentinel_events"], "Canceled MCP batch dispatched its later sentinel key"
            finally:
                # Lock while the client is alive, including failures before cancellation.
                try:
                    evidence["lock"] = result_data(await call("desktop_lock", {}))
                except BaseException as error:
                    evidence.setdefault("cleanup_errors", []).append(f"desktop_lock: {type(error).__name__}: {error}")
                if task and not task.done():
                    if requests.batch_request_id is not None:
                        try:
                            await asyncio.wait_for(client.cancel(requests.batch_request_id,
                                reason="Sequence cleanup"), timeout=2)
                        except BaseException as error:
                            evidence.setdefault("cleanup_errors", []).append(
                                f"cancel notification: {type(error).__name__}: {error}")
                    else:
                        evidence.setdefault("cleanup_errors", []).append("Missing MCP request ID for pending batch cancellation")
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=2)
                    except asyncio.CancelledError:
                        pass
                    except BaseException as error:
                        evidence.setdefault("cleanup_errors", []).append(
                            f"batch waiter cleanup: {type(error).__name__}: {error}")
    finally:
        if mcp:
            try:
                await asyncio.wait_for(asyncio.to_thread(mcp._pcbridge_desktop_runtime.close), timeout=8)
            except BaseException as error:
                evidence.setdefault("cleanup_errors", []).append(f"runtime.close: {type(error).__name__}: {error}")


async def lifecycle_case(client, call, cfg, window, evidence, requests):
    case = evidence["case"]
    store = LeaseStore(cfg.state_dir)
    mark = window.mark()
    wait_ms = (11500 if case == "expiry" else 5000 if case == "replacement"
               else 100 if case == "failure" else 3000)
    actions = [{"a": "hold", "keys": "shift"}, {"a": "wait", "ms": wait_ms}]
    if case == "failure":
        actions.append({"a": "key", "keys": "pcbridge_no_such_key"})
    actions.append({"a": "key", "keys": "a"})
    assert hyprland._query("activewindow", json_output=True)["pid"] == window.process.pid
    started = time.monotonic()
    task = asyncio.create_task(client.call_tool("computer_batch", {
        "actions": json.dumps(actions), "final": "none", "force": True}, raise_on_error=False))
    try:
        down = await asyncio.to_thread(window.wait, lambda e:
            e.get("event") == "key_press" and e.get("keyval", "").startswith("Shift"), 5, mark)
        evidence["shift_down"] = down
        assert down, {"events": window.since(mark), "batch_done": task.done()}
        assert requests.batch_request_id is not None, "Real MCP batch request ID was not observed"
        evidence["mcp_request_id"] = requests.batch_request_id
        snapshot = store.snapshot()  # Read only: do not refresh the sliding deadline.
        evidence["sequence_lease"] = dataclasses.asdict(snapshot)
        deadline = time.monotonic() + snapshot.until - time.time()
        trigger = time.monotonic()
        if case == "revoke":
            locked = await call("desktop_lock", {})
            evidence["revoke"] = result_data(locked)
            assert not locked.is_error
        elif case == "replacement":
            replaced = await call("desktop_unlock", {"minutes": 1,
                "reason": "Disposable VM replacement sequence acceptance"})
            evidence["replacement_unlock"] = result_data(replaced)
            assert not replaced.is_error
            replacement = store.snapshot()
            evidence["replacement_lease"] = dataclasses.asdict(replacement)
            assert replacement.grant_id != snapshot.grant_id
            assert replacement.token() != snapshot.token()
            assert hyprland._query("activewindow", json_output=True)["pid"] == window.process.pid
            keymark = window.mark()
            evidence["old_batch_done_before_new_hold"] = task.done()
            assert not task.done(), "Replacement hold must be queued while the old sequence is active"
            response = await call("keyboard", {"action": "hold", "keys": "ctrl", "force": True})
            evidence["replacement_hold"] = result_data(response)
            assert not response.is_error
            pressed = await asyncio.to_thread(window.wait, lambda e:
                e.get("event") == "key_press" and e.get("keyval", "").startswith("Control"), 3, keymark)
            assert pressed, window.since(keymark)
        up = await asyncio.to_thread(window.wait, lambda e:
            e.get("event") == "key_release" and e.get("keyval", "").startswith("Shift"),
            14 if case == "expiry" else 5, mark)
        evidence["shift_release"] = up
        evidence["release_after_trigger_seconds"] = up[0] - trigger if up else None
        evidence["release_after_shift_down_seconds"] = up[0] - down[0] if up else None
        evidence["release_from_deadline_seconds"] = up[0] - deadline if up else None
        assert up
        assert hyprland._query("activewindow", json_output=True)["pid"] == window.process.pid
        if case == "expiry":
            assert -0.5 <= up[0] - deadline <= 1.5, "Release must follow the actual sliding deadline"
            assert up[0] - down[0] < 15, "Hold watchdog is not expiry evidence"
        elif case in ("revoke", "replacement"):
            assert 0 <= up[0] - trigger <= 1, "Revoked/replaced input was not released promptly"
        else:
            assert 0 <= up[0] - down[0] <= 1, "Failed sequence did not release promptly"
        result = await asyncio.wait_for(asyncio.shield(task), timeout=20)
        evidence["batch_result"] = result_data(result)
        evidence["batch_result_received_after_shift_down_seconds"] = time.monotonic() - down[0]
        structured = result.structured_content
        assert result.is_error and structured and "error" in structured
        batch = structured["batch"]
        assert batch["total"] == len(actions) and 1 <= batch["done"] < len(actions)
        assert batch["stopped"] == ("error" if case == "failure" else "safety"), batch
        code = structured["error"]["code"]
        if case == "failure":
            assert batch["done"] == 2, "Runtime key failure must follow the completed hold and wait"
            assert "pcbridge_no_such_key" in structured["error"]["message"], structured
            assert "unknown key" in structured["error"]["message"], structured
            assert structured["error"]["backend"] == "pcbridge-native", structured
        elif case == "replacement":
            assert code == "REVOKED", structured
        elif case in ("expiry", "revoke"):
            assert code in ("GRANT_REQUIRED", "GRANT_EXPIRED", "REVOKED"), structured
        # Keep MCP alive past the original wait to detect any late sentinel.
        await asyncio.sleep(max(0, started + wait_ms / 1000 + 1 - time.monotonic()))
        assert hyprland._query("activewindow", json_output=True)["pid"] == window.process.pid
        evidence["events"] = window.since(mark)
        evidence["sentinel_events"] = [(stamp, event) for stamp, event in window.since(mark)
            if event.get("event") in ("key_press", "key_release") and event.get("keyval") in ("a", "A")]
        assert not evidence["sentinel_events"], "Stopped sequence dispatched its later sentinel"
        after = store.snapshot()
        evidence["lease_after_batch"] = dataclasses.asdict(after)
        if case in ("expiry", "revoke"):
            assert not after.is_active()
            await asyncio.to_thread(wait_for, lambda: not layers(), description="closed grant frame")
        else:
            assert after.is_active() and layers(), "Usable grant must remain visible"
            if case == "replacement":
                assert after.token() == replacement.token(), "Old cleanup retired the replacement"
            assert hyprland._query("activewindow", json_output=True)["pid"] == window.process.pid
            key = "ctrl" if case == "replacement" else "b"
            prefix = "Control" if case == "replacement" else "b"
            if case == "failure":
                keymark = window.mark()
                response = await call("keyboard", {"action": "key", "keys": key, "force": True})
                evidence["grant_reuse"] = result_data(response)
                assert not response.is_error
                pressed = await asyncio.to_thread(window.wait, lambda e:
                    e.get("event") == "key_press" and e.get("keyval", "").startswith(prefix), 3, keymark)
                assert pressed, window.since(keymark)
                assert pressed[1].get("shift") is False, pressed
            if case == "replacement":
                await asyncio.sleep(0.7)
                assert not any(e.get("event") == "key_release" and e.get("keyval", "").startswith("Control")
                    for _, e in window.since(keymark)), "Old cleanup released the replacement hold"
                response = await call("keyboard", {"action": "release", "keys": key, "force": True})
                evidence["replacement_release"] = result_data(response)
                assert not response.is_error
            released = await asyncio.to_thread(window.wait, lambda e:
                e.get("event") == "key_release" and e.get("keyval", "").startswith(prefix), 3, keymark)
            assert released, window.since(keymark)
            if case == "failure":
                assert released[1].get("shift") is False, released
            evidence["reuse_events"] = window.since(keymark)
    finally:
        if not task.done():
            if requests.batch_request_id is not None:
                try:
                    await asyncio.wait_for(client.cancel(requests.batch_request_id,
                        reason="Sequence lifecycle cleanup"), timeout=2)
                except BaseException as error:
                    evidence.setdefault("cleanup_errors", []).append(
                        f"cancel notification: {type(error).__name__}: {error}")
            else:
                evidence.setdefault("cleanup_errors", []).append("Missing MCP request ID for pending batch cancellation")
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=3)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=2)
                except asyncio.CancelledError:
                    pass
                except BaseException as error:
                    evidence.setdefault("cleanup_errors", []).append(
                        f"batch waiter cleanup: {type(error).__name__}: {error}")
            except BaseException as error:
                evidence.setdefault("cleanup_errors", []).append(
                    f"batch waiter cleanup: {type(error).__name__}: {error}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=["cancellation", "expiry", "revoke", "replacement", "failure"], required=True)
    args = parser.parse_args()
    idle = window = None
    evidence = {"case": args.case}
    with tempfile.TemporaryDirectory(prefix="pcbridge-sequence-") as temporary:
        directory = Path(temporary)
        try:
            assert hyprland.screen_locked() is False
            assert not layers(), "Refuse to overlap an existing glow"
            assert idlewatch.read_idle_ms() is None, "Refuse to overlap an idle writer"
            assert os.access("/dev/uinput", os.R_OK | os.W_OK)
            base = load_config(ROOT / "config.example.toml", check_state=False)
            assert base.native.binary_path is None
            binary = discover_native_binary(base.native)
            assert binary.is_file() and binary.is_relative_to(ROOT / "pcbridge/_native")
            info = json.loads(subprocess.run([str(binary), "--build-info"], check=True,
                capture_output=True, text=True, timeout=5).stdout)
            assert info["profile"] == "release" and info["test_harness"] is False, info
            evidence.update(helper=str(binary), build=info)
            cfg = dataclasses.replace(base, state_dir=directory,
                desktop=dataclasses.replace(base.desktop, enabled=True,
                    agent_shot_dir=str(directory / "shots"),
                    unlock_notification=False, unlock_idle_seconds=10,
                    hold_max_seconds=20, batch_budget_seconds=25))
            cfg.jobs_dir.mkdir(parents=True)
            (directory / "shots").mkdir()
            window = InputWindow(directory / "observer.stderr", timeout=120, details=True)
            ready = window.wait(lambda e: e.get("event") == "ready", 20)
            table = monitors.list_monitors(use_cache=False)
            assert ready and len(table) == 2, "Two observers must be ready before input"
            assert ready[1]["monitors"] == [[m.x, m.y, m.width, m.height] for m in table]
            window.settle()
            assert hyprland._query("activewindow", json_output=True)["pid"] == window.process.pid
            clients = [c for c in hyprland._query("clients", json_output=True)
                       if c.get("pid") == window.process.pid and c.get("mapped")]
            assert len(clients) == 2 and all(c.get("visible") for c in clients)
            assert all(c.get("fullscreen") == 2 for c in clients), "Observers must be fully fullscreen"
            for monitor in table:
                assert any(c["at"] == [monitor.x, monitor.y]
                    and c["size"] == [monitor.width, monitor.height] for c in clients)
            evidence["observer"] = {"pid": window.process.pid, "ready": ready, "clients": clients}
            idle = subprocess.Popen([str(binary), "idle-watch"], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            wait_for(lambda: idlewatch.read_idle_ms() is not None, description="fresh packaged idle watcher")
            asyncio.run(run_case(cfg, window, evidence))
        except BaseException as error:
            evidence["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            errors = []
            cleanups = []
            if idle:
                def stop_idle():
                    if idle.poll() is None:
                        idle.terminate()
                    idle.wait(timeout=5)
                cleanups.append(stop_idle)
            if window:
                cleanups.append(window.close)
            cleanups.append(lambda: wait_for(lambda: not layers(), description="final sequence frame teardown"))
            for cleanup in cleanups:
                try:
                    cleanup()
                except BaseException as error:
                    errors.append(f"{type(error).__name__}: {error}")
            if errors:
                evidence.setdefault("cleanup_errors", []).extend(errors)
            if window:
                evidence["observer_stderr_tail"] = window.errors.read_text(
                    encoding="utf-8", errors="replace")[-8000:]
            print(json.dumps(evidence, sort_keys=True, default=str), flush=True)
            if evidence.get("cleanup_errors") and "error" not in evidence:
                raise RuntimeError(f"Sequence cleanup failed: {evidence['cleanup_errors']}")


if __name__ == "__main__":
    main()
