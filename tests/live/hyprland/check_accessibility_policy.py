"""Verify native AT-SPI policy through ordinary MCP tools in the disposable VM."""
from __future__ import annotations

import os

if not __debug__:
    raise RuntimeError("Accessibility policy acceptance requires assertions; Python -O is forbidden")
if os.environ.get("PCBRIDGE_TEST_HYPRLAND_A11Y_POLICY") != "1":
    raise RuntimeError("Set PCBRIDGE_TEST_HYPRLAND_A11Y_POLICY=1 in the disposable VM")
if os.uname().nodename != "pcbridge-hyprland":
    raise RuntimeError("Accessibility policy acceptance runs only on the disposable pcbridge-hyprland VM")
if "PCBRIDGE_NATIVE_BIN" in os.environ:
    raise RuntimeError("Native helper overrides are forbidden")

import argparse
import asyncio
import dataclasses
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from fastmcp import Client  # noqa: E402
from pcbridge.app import build_app  # noqa: E402
from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop import hyprland, idlewatch  # noqa: E402
from pcbridge.native import discover_native_binary  # noqa: E402
from tests.live.hyprland.check_glow_owner import layers, wait_for  # noqa: E402
from tests.live.test_accessibility_parity import A11yWindow, APP  # noqa: E402


def code(result):
    assert result.is_error, result.content
    return result.structured_content["error"]["code"]


def text(result):
    return "\n".join(item.text for item in result.content if getattr(item, "type", None) == "text")


def node_id(dump, role, name):
    matches = re.findall(rf'^\s+#([0-9a-f]+) {re.escape(role)} "{re.escape(name)}"',
                         dump, re.MULTILINE)
    assert len(matches) == 1, {"role": role, "name": name, "dump": dump}
    return matches[0]


async def run(cfg, window, evidence):
    mcp, _ = build_app(cfg, transport="stdio")
    grant_open = False
    try:
        async with Client(mcp, timeout=20) as client:
            async def call(name, arguments):
                return await asyncio.wait_for(client.call_tool(name, arguments,
                    raise_on_error=False), timeout=12)

            try:
                before = await call("ui_dump", {"target": APP})
                evidence["before_grant"] = code(before)
                assert evidence["before_grant"] == "GRANT_REQUIRED"
                opened = await call("desktop_unlock", {"minutes": 1,
                    "reason": "Disposable VM accessibility policy acceptance"})
                assert not opened.is_error, text(opened)
                grant_open = True
                wait_for(lambda: len(layers()) == 8, description="native frame for accessibility policy")
                capabilities = await call("system_capabilities", {})
                assert not capabilities.is_error, text(capabilities)
                evidence["accessibility_backend"] = capabilities.structured_content[
                    "capabilities"]["accessibility.read"]["backend"]
                assert evidence["accessibility_backend"] == "linux.atspi.native"

                dumped = await call("ui_dump", {"target": APP})
                assert not dumped.is_error, text(dumped)
                body = text(dumped)
                evidence["dump"] = body
                assert window.ready["pid"] in [row.get("pid") for row in
                    hyprland._query("clients", json_output=True)]
                name = node_id(body, "text", "Ad")
                password = node_id(body, "password text", "Parola")
                button = node_id(body, "push button", "Tamam")

                password_mark = window.mark()
                denied = await call("ui_set_text", {"id": password,
                    "text": "refused-fixture-value", "force": True})
                evidence["password_refusal"] = code(denied)
                assert evidence["password_refusal"] == "PASSWORD_FIELD"
                assert not any(item.get("field") == "password" for item in window.since(password_mark))

                mark = window.mark()
                written = await call("ui_set_text", {"id": name,
                    "text": "Hyprland policy fixture", "force": True})
                assert not written.is_error, text(written)
                received = await asyncio.to_thread(window.wait, lambda item:
                    item.get("field") == "name" and
                    item.get("value") == "Hyprland policy fixture", 4, mark)
                assert received is not None, window.since(mark)
                evidence["ordinary_text"] = "received"

                mark = window.mark()
                close_batch = await call("computer_batch", {"actions": json.dumps([
                    {"a": "ui_click", "id": button},
                    {"a": "key", "keys": "alt+F4"}]),
                    "final": "none", "force": True})
                evidence["batch_close_refusal"] = code(close_batch)
                assert evidence["batch_close_refusal"] == "CONFIRMATION_REQUIRED"
                assert not any(item.get("event") == "clicked" for item in window.since(mark))

                mark = window.mark()
                repeated = await call("computer_batch", {"actions": json.dumps([
                    {"a": "ui_click", "id": button},
                    {"a": "wait", "ms": 500},
                    {"a": "ui_click", "id": button},
                    {"a": "wait", "ms": 500},
                    {"a": "ui_click", "id": button}]),
                    "final": "none", "force": True})
                assert not repeated.is_error, text(repeated)
                evidence["repeat_result"] = text(repeated)
                assert evidence["repeat_result"].startswith("**4 of 5 actions done**")
                assert "Repeated click" in evidence["repeat_result"], evidence["repeat_result"]
                await asyncio.to_thread(wait_for, lambda: len([
                    item for item in window.since(mark) if item.get("event") == "clicked"]) >= 2,
                    timeout=3, description="two allowed accessibility clicks")
                clicked = [item for item in window.since(mark)
                           if item.get("event") == "clicked"]
                assert [item.get("button") for item in clicked] == ["ok", "ok"], clicked
                evidence["repeat_click"] = "third_refused_after_two_clicks"

                denied = await call("keyboard", {"action": "key", "keys": "alt+F4",
                    "force": True})
                evidence["close_refusal"] = code(denied)
                assert evidence["close_refusal"] == "CONFIRMATION_REQUIRED"
                await asyncio.sleep(0.2)
                assert window.process.poll() is None
                assert not any(item.get("field") == "password" for item in window.since(password_mark))
                evidence["window_alive_after_refusals"] = True
                closed = await call("desktop_lock", {})
                assert not closed.is_error, text(closed)
                grant_open = False
                wait_for(lambda: not layers(), description="accessibility policy frame teardown")
                evidence["passed"] = True
            finally:
                if grant_open:
                    await call("desktop_lock", {})
    finally:
        await asyncio.wait_for(asyncio.to_thread(mcp._pcbridge_desktop_runtime.close), timeout=8)
        evidence["cleanup"]["runtime"] = "closed"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    evidence = {"passed": False, "cleanup": {}}
    failure = None
    idle = window = None
    try:
        assert hyprland.screen_locked() is False
        assert not layers() and idlewatch.read_idle_ms() is None
        base = load_config(ROOT / "config.example.toml", check_state=False)
        assert base.native.binary_path is None
        binary = discover_native_binary(base.native)
        assert binary.is_file() and binary.is_relative_to(ROOT / "pcbridge/_native")
        build = json.loads(subprocess.run([str(binary), "--build-info"], check=True,
            capture_output=True, text=True, timeout=5).stdout)
        assert build["profile"] == "release" and build["test_harness"] is False
        evidence["build"] = build
        with tempfile.TemporaryDirectory(prefix="pcbridge-a11y-policy-") as temporary:
            directory = Path(temporary)
            cfg = dataclasses.replace(base, state_dir=directory / "state",
                desktop=dataclasses.replace(base.desktop, enabled=True,
                    unlock_idle_seconds=60, unlock_notification=False, idle_guard_seconds=2,
                    agent_shot_dir=str(directory / "shots")),
                native=dataclasses.replace(base.native, accessibility="rust",
                    input="rust", capture="rust"))
            cfg.jobs_dir.mkdir(parents=True)
            (cfg.state_dir / "shots").mkdir(parents=True)
            Path(cfg.desktop.agent_shot_dir).mkdir(parents=True)
            try:
                idle = subprocess.Popen([str(binary), "idle-watch"], stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
                wait_for(lambda: idlewatch.read_idle_ms() is not None,
                         description="fresh idle for accessibility policy")
                window = A11yWindow(directory / "window.stderr")
                asyncio.run(asyncio.wait_for(run(cfg, window, evidence), timeout=55))
            finally:
                if window:
                    window.close()
                    evidence["cleanup"]["window_exit"] = window.process.returncode
                if idle:
                    if idle.poll() is None:
                        idle.terminate()
                    idle.wait(timeout=3)
                    evidence["cleanup"]["idle_exit"] = idle.returncode
                wait_for(lambda: not layers() and idlewatch.read_idle_ms() is None,
                         description="accessibility policy cleanup")
                evidence["cleanup"]["screen_locked"] = hyprland.screen_locked()
                assert evidence["cleanup"]["screen_locked"] is False
    except BaseException as error:
        failure = error
        evidence["failure"] = {"type": type(error).__name__, "message": str(error)[:1600]}
        evidence["passed"] = False
    finally:
        (args.out_dir / "accessibility-policy.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print({"passed": evidence["passed"], "failure": evidence.get("failure"),
               "cleanup": evidence["cleanup"]})
    if failure:
        raise failure


if __name__ == "__main__":
    main()
