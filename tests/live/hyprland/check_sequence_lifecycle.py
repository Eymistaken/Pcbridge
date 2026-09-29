"""Observe real MCP sequence cancellation in the disposable Hyprland VM."""
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
        async with Client(mcp, timeout=8) as client:
            async def call(name, arguments):
                return await asyncio.wait_for(client.call_tool(
                    name, arguments, raise_on_error=False), timeout=8)

            try:
                unlock = await call("desktop_unlock", {"minutes": 1,
                    "reason": "Disposable VM MCP cancellation acceptance"})
                evidence["unlock"] = result_data(unlock)
                assert not unlock.is_error, evidence["unlock"]
                assert hyprland.screen_locked() is False
                assert hyprland._query("activewindow", json_output=True)["pid"] == window.process.pid
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
                await client.cancel(requests.batch_request_id,
                    reason="Disposable VM sequence cancellation acceptance")
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
                    task.cancel()
                    try:
                        await asyncio.wait_for(task, timeout=2)
                    except asyncio.CancelledError:
                        pass
    finally:
        if mcp:
            try:
                await asyncio.wait_for(asyncio.to_thread(mcp._pcbridge_desktop_runtime.close), timeout=8)
            except BaseException as error:
                evidence.setdefault("cleanup_errors", []).append(f"runtime.close: {type(error).__name__}: {error}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=["cancellation"], required=True)
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
            window = InputWindow(directory / "observer.stderr", timeout=90, details=True)
            ready = window.wait(lambda e: e.get("event") == "ready", 20)
            table = monitors.list_monitors(use_cache=False)
            assert ready and len(table) == 2, "Two observers must be ready before input"
            assert ready[1]["monitors"] == [[m.x, m.y, m.width, m.height] for m in table]
            window.settle()
            assert hyprland._query("activewindow", json_output=True)["pid"] == window.process.pid
            clients = [c for c in hyprland._query("clients", json_output=True)
                       if c.get("pid") == window.process.pid and c.get("mapped")]
            assert len(clients) == 2 and all(c.get("visible") for c in clients)
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
