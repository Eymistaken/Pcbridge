#!/usr/bin/env python3
"""Task 4.3: what a freshly started process does with the shipped configuration.

Skipped unless `PCBRIDGE_TEST_CAPTURE=1`. It opens screen shares; it sends no
input. Grants live in scratch state directories, and `gnome-screenshot` is
shadowed by a program that refuses to run, so no flash or sound can happen.

Three processes, each started the way a user's would be after the default
became `auto`, from the example configuration with `[native]` left untouched:

* a stdio server: picks the native helper, and the Mutter session -- the share
  the indicator shows -- opens at `desktop_unlock`, before any capture, which
  is when the Python path has always opened it;
* a stdio server that cannot find the helper: falls back to Python and says so
  in `system_capabilities` and in the `screen_capture` result;
* an HTTP server, the way the systemd service runs: native by default too.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from fastmcp import Client  # noqa: E402
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport  # noqa: E402

from tests.live.test_capture_parity import (  # noqa: E402
    PACKAGED,
    legacy_helpers,
    release_binary,
    screen_locked,
    screencast_sessions,
    wait_until,
)


LIVE = os.environ.get("PCBRIDGE_TEST_CAPTURE") == "1"
VENV_PYTHON = str(ROOT / ".venv" / "bin" / "python")
SHARE_OPEN = "Screen sharing is on"
FALLBACK_NOTE = "Native capture was unavailable"


def shipped_config(root: Path, *, port: int | None = None, token: str | None = None) -> Path:
    """The example config as shipped, except scratch paths and enabled desktop.

    `[native]` is not touched: the point is what the default does.
    """
    text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    replacements = [
        (r"^state_dir = .*$", f'state_dir = "{root / "state"}"'),
        (r'^agent_shot_dir = ""$', f'agent_shot_dir = "{root / "agent-shots"}"'),
        (r"^include_pointer = true$", "include_pointer = false"),
    ]
    if port is not None:
        replacements.append((r"^port = 8765$", f"port = {port}"))
    if token is not None:
        replacements.append((r'^static_token = ""$', f'static_token = "{token}"'))
    for pattern, value in replacements:
        text, count = re.subn(pattern, lambda _match, v=value: v, text, flags=re.M)
        if count != 1:
            raise AssertionError(f"config.example.toml changed shape: {pattern}")
    header = text.index("\n[desktop]\n")
    enabled = re.compile(r"^enabled = false$", re.M).search(text, header)
    if enabled is None or "\n[" in text[header + 1 : enabled.start()]:
        raise AssertionError("config.example.toml: [desktop] enabled not found")
    text = text[: enabled.start()] + "enabled = true" + text[enabled.end():]
    root.mkdir(parents=True, exist_ok=True)
    path = root / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def shimmed_env(root: Path, **extra: str) -> dict[str, str]:
    shim = root / "no-screenshot"
    shim.mkdir(parents=True, exist_ok=True)
    program = shim / "gnome-screenshot"
    program.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
    program.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{shim}{os.pathsep}{env.get('PATH', '')}"
    env.pop("PCBRIDGE_NATIVE_BIN", None)
    env.update(extra)
    return env


def text_of(result) -> str:
    return "\n".join(block.text for block in result.content if block.type == "text")


def image_count(result) -> int:
    return sum(1 for block in result.content if block.type == "image")


def capture_monitor(result) -> dict:
    return result.structured_content["capabilities"]["capture.monitor"]


@unittest.skipUnless(LIVE, "set PCBRIDGE_TEST_CAPTURE=1: opens screen shares")
class ShippedDefault(unittest.TestCase):
    def setUp(self) -> None:
        binary, why = release_binary()
        if binary is None or binary != PACKAGED:
            self.skipTest(why or "the shipped default looks for the packaged helper")
        if screen_locked():
            self.skipTest("the screen is locked")
        if legacy_helpers():
            self.skipTest("a Python screencast helper is running (a real grant)")
        self.root = Path(tempfile.mkdtemp(prefix="pcb-default-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.sessions_before = screencast_sessions()

    def tearDown(self) -> None:
        wait_until(lambda: screencast_sessions() <= self.sessions_before, 10)

    def test_a_new_stdio_process_uses_rust_and_opens_the_share_at_unlock(self) -> None:
        config = shipped_config(self.root / "stdio")
        env = shimmed_env(self.root / "stdio")
        transport = StdioTransport(
            VENV_PYTHON, ["-m", "pcbridge.server", "--stdio", "-c", str(config)],
            env=env, cwd=str(ROOT), keep_alive=False,
        )

        async def scenario():
            async with Client(transport) as client:
                caps = await client.call_tool("system_capabilities")
                unlock = await client.call_tool(
                    "desktop_unlock", {"minutes": 2, "reason": "default rollout"},
                    raise_on_error=False)
                opened = wait_until(
                    lambda: screencast_sessions() > self.sessions_before, 10)
                after_unlock = screencast_sessions()
                shot = await client.call_tool(
                    "screen_capture", {"monitor": "all"}, raise_on_error=False)
                after_capture = screencast_sessions()
                lock = await client.call_tool("desktop_lock", raise_on_error=False)
                closed = wait_until(
                    lambda: screencast_sessions() <= self.sessions_before, 10)
                return caps, unlock, opened, after_unlock, shot, after_capture, lock, closed

        caps, unlock, opened, after_unlock, shot, after_capture, lock, closed = asyncio.run(
            scenario())

        self.assertEqual(capture_monitor(caps)["backend"], "linux.mutter.pipewire")
        self.assertEqual(capture_monitor(caps)["state"], "supported")
        self.assertIn(SHARE_OPEN, text_of(unlock), text_of(unlock))
        self.assertTrue(opened, "the share must open at desktop_unlock, before any capture")
        self.assertEqual(after_unlock, self.sessions_before + 1)
        self.assertFalse(shot.is_error, text_of(shot))
        self.assertEqual(image_count(shot), 2)
        self.assertNotIn(FALLBACK_NOTE, text_of(shot))
        self.assertEqual(after_capture, after_unlock, "the capture reuses the unlock session")
        self.assertFalse(lock.is_error, text_of(lock))
        self.assertTrue(closed, "desktop_lock must close the share")

    def test_without_the_helper_a_new_process_falls_back_to_python_visibly(self) -> None:
        config = shipped_config(self.root / "fallback")
        env = shimmed_env(
            self.root / "fallback",
            PCBRIDGE_NATIVE_BIN=str(self.root / "no-such-helper"),
        )
        transport = StdioTransport(
            VENV_PYTHON, ["-m", "pcbridge.server", "--stdio", "-c", str(config)],
            env=env, cwd=str(ROOT), keep_alive=False,
        )

        async def scenario():
            async with Client(transport) as client:
                caps = await client.call_tool("system_capabilities")
                unlock = await client.call_tool(
                    "desktop_unlock", {"minutes": 2, "reason": "fallback rollout"},
                    raise_on_error=False)
                shot = await client.call_tool(
                    "screen_capture", {"monitor": "1"}, raise_on_error=False)
                lock = await client.call_tool("desktop_lock", raise_on_error=False)
                return caps, unlock, shot, lock

        caps, unlock, shot, lock = asyncio.run(scenario())

        monitor = capture_monitor(caps)
        self.assertEqual(monitor["state"], "degraded", monitor)
        self.assertIn("no-such-helper", " ".join(monitor["limitations"]), monitor)
        self.assertIn(SHARE_OPEN, text_of(unlock), text_of(unlock))
        self.assertFalse(shot.is_error, text_of(shot))
        self.assertEqual(image_count(shot), 1)
        self.assertIn(FALLBACK_NOTE, text_of(shot))
        self.assertFalse(lock.is_error, text_of(lock))

    def test_a_service_style_http_process_uses_rust_by_default(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        token = secrets.token_urlsafe(24)
        root = self.root / "http"
        config = shipped_config(root, port=port, token=token)
        env = shimmed_env(root)
        log = (root / "server.log").open("w")
        server = subprocess.Popen(
            [VENV_PYTHON, "-m", "pcbridge.server", "-c", str(config)],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        )

        def healthy() -> bool:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as r:
                    return r.status == 200
            except OSError:
                return False

        async def scenario():
            transport = StreamableHttpTransport(
                f"http://127.0.0.1:{port}/mcp",
                headers={"Authorization": f"Bearer {token}"},
            )
            async with Client(transport) as client:
                caps = await client.call_tool("system_capabilities")
                unlock = await client.call_tool(
                    "desktop_unlock", {"minutes": 2, "reason": "service rollout"},
                    raise_on_error=False)
                opened = wait_until(
                    lambda: screencast_sessions() > self.sessions_before, 10)
                shot = await client.call_tool(
                    "screen_capture", {"monitor": "all"}, raise_on_error=False)
                lock = await client.call_tool("desktop_lock", raise_on_error=False)
                return caps, unlock, opened, shot, lock

        try:
            self.assertTrue(wait_until(healthy, 30), (root / "server.log").read_text())
            caps, unlock, opened, shot, lock = asyncio.run(scenario())
        finally:
            server.terminate()
            try:
                server.wait(15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(5)
            log.close()

        self.assertEqual(capture_monitor(caps)["backend"], "linux.mutter.pipewire")
        self.assertIn(SHARE_OPEN, text_of(unlock), text_of(unlock))
        self.assertTrue(opened, "the service must open the share at desktop_unlock")
        self.assertFalse(shot.is_error, text_of(shot))
        self.assertEqual(image_count(shot), 2)
        self.assertFalse(lock.is_error, text_of(lock))


if __name__ == "__main__":
    unittest.main(verbosity=2)
