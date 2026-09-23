#!/usr/bin/env python3
"""Offline snapshots for MCP names, schemas, annotations, and image ordering."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastmcp import FastMCP
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import tools as toolslib  # noqa: E402
from pcbridge.config import AgentSpec, Config, DesktopSpec  # noqa: E402
from pcbridge.desktop import capture as capturelib  # noqa: E402
from pcbridge.desktop import monitors as monitorslib  # noqa: E402
from pcbridge.jobs import JobManager  # noqa: E402
from pcbridge.shots import ShotStore  # noqa: E402


EXPECTED_TOOL_NAMES = [
    "agent_run",
    "computer_batch",
    "computer_task",
    "desktop_lock",
    "desktop_unlock",
    "find_text",
    "fs_list",
    "fs_read",
    "fs_search",
    "fs_write",
    "job_cancel",
    "job_list",
    "job_output",
    "job_status",
    "keyboard",
    "list_agents",
    "mouse",
    "notify",
    "panel_icon",
    "screen_capture",
    "screen_info",
    "shell_run",
    "shell_run_background",
    "system_capabilities",
    "system_status",
    "tmux_capture",
    "tmux_keys",
    "tmux_kill",
    "tmux_list",
    "tmux_send",
    "tmux_start",
    "ui_click",
    "ui_dump",
    "ui_set_text",
    "wait_for_text",
    "window_focus",
    "window_list",
]

EXPECTED_ANNOTATIONS = {
    "agent_run": {"title": "Send a prompt to a coding agent", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    "computer_batch": {"title": "Run several actions in one go", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    "computer_task": {"title": "Let a local agent drive the screen", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    "desktop_lock": {"title": "Stop desktop control", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "desktop_unlock": {"title": "Allow desktop control for a while", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
    "find_text": {"title": "Find text on screen", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    "fs_list": {"title": "List a directory", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "fs_read": {"title": "Read a file", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "fs_search": {"title": "Search inside files", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "fs_write": {"title": "Write a file", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False},
    "job_cancel": {"title": "Cancel a job", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False},
    "job_list": {"title": "List background jobs", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "job_output": {"title": "Read raw job output", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "job_status": {"title": "Check a background job", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "keyboard": {"title": "Type text or press keys", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    "list_agents": {"title": "List available coding agents", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "mouse": {"title": "Move or click the mouse", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    "notify": {"title": "Show a desktop notification", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    "panel_icon": {"title": "Show or hide pcbridge's panel icon", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "screen_capture": {"title": "Take a screenshot", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    "screen_info": {"title": "Describe the screens", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "shell_run": {"title": "Run a shell command", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    "shell_run_background": {"title": "Run a long shell command in background", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    "system_capabilities": {"title": "Desktop capabilities", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "system_status": {"title": "Computer status", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "tmux_capture": {"title": "Read a live terminal screen", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "tmux_keys": {"title": "Press keys in a live terminal", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    "tmux_kill": {"title": "Close a live terminal", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False},
    "tmux_list": {"title": "List live terminal sessions", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "tmux_send": {"title": "Type into a live terminal", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    "tmux_start": {"title": "Open a live terminal session", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    "ui_click": {"title": "Click something on screen", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    "ui_dump": {"title": "Read the screen as text", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    "ui_set_text": {"title": "Type into a text box", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True},
    "wait_for_text": {"title": "Wait for text on screen", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    "window_focus": {"title": "Bring a window to the front", "readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
    "window_list": {"title": "List open windows", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
}


def make_config(root: Path) -> Config:
    config = Config(
        public_url="https://example.invalid",
        host="127.0.0.1",
        port=8765,
        mcp_path="/mcp",
        password="contract-password",
        static_token="",
        access_token_ttl=60,
        refresh_token_ttl=120,
        auth_code_ttl=30,
        max_failed_attempts=3,
        lockout_seconds=60,
        manual_redirect=False,
        default_workdir=root,
        state_dir=root,
        max_output_chars=4000,
        default_job_timeout=60,
        max_sync_timeout=60,
        agents={"contract": AgentSpec(name="contract", command=["true"])},
        inline_images="true",
        default_agent="contract",
        desktop=DesktopSpec(enabled=True, screenshot_scale_long_edge=80),
    )
    config.jobs_dir.mkdir(parents=True, exist_ok=True)
    return config


class OpenGate:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def check(self, *args, **kwargs):
        return SimpleNamespace(allowed=True, reason="")

    def audit(self, *args, **kwargs) -> None:
        return None

    def is_unlocked(self) -> bool:
        return True

    def remaining_seconds(self) -> float:
        return 0.0


def build_mcp(root: Path) -> tuple[FastMCP, ShotStore]:
    config = make_config(root)
    store = ShotStore(config)
    mcp = FastMCP("contract")
    with mock.patch.object(toolslib.safetylib, "SafetyGate", OpenGate):
        toolslib.register(
            mcp,
            config,
            JobManager(config.jobs_dir, default_timeout=60),
            store,
            transport="stdio",
        )
    return mcp, store


def compact_schema(tool) -> dict:
    schema = tool.parameters
    return {
        "properties": list(schema["properties"]),
        "required": schema.get("required", []),
        "defaults": {
            name: value["default"]
            for name, value in schema["properties"].items()
            if "default" in value
        },
        "additionalProperties": schema.get("additionalProperties"),
    }


class McpContractTests(unittest.TestCase):
    def test_tool_names_annotations_and_desktop_input_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            mcp, _store = build_mcp(Path(raw))
            tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

        self.assertEqual(sorted(tools), EXPECTED_TOOL_NAMES)
        self.assertEqual(
            {
                name: tool.annotations.model_dump(exclude_none=True)
                for name, tool in tools.items()
            },
            EXPECTED_ANNOTATIONS,
        )
        self.assertEqual(
            compact_schema(tools["screen_capture"]),
            {
                "properties": [
                    "monitor", "scale", "include_pointer", "region", "shot", "enhance",
                ],
                "required": [],
                "defaults": {
                    "monitor": "all",
                    "scale": None,
                    "include_pointer": None,
                    "region": None,
                    "shot": None,
                    "enhance": False,
                },
                "additionalProperties": False,
            },
        )
        self.assertEqual(
            compact_schema(tools["mouse"]),
            {
                "properties": [
                    "action",
                    "x",
                    "y",
                    "to_x",
                    "to_y",
                    "dx",
                    "dy",
                    "scroll_amount",
                    "horizontal",
                    "button",
                    "hold_ms",
                    "smooth",
                    "shot",
                    "monitor",
                    "force",
                ],
                "required": ["action"],
                "defaults": {
                    "x": None,
                    "y": None,
                    "to_x": None,
                    "to_y": None,
                    # `move_by` deltasi: koordinat degil, o yuzden None degil 0.
                    "dx": 0,
                    "dy": 0,
                    "scroll_amount": 3,
                    "horizontal": False,
                    "button": "left",
                    "hold_ms": None,
                    "smooth": None,
                    "shot": None,
                    "monitor": None,
                    "force": False,
                },
                "additionalProperties": False,
            },
        )
        self.assertEqual(
            compact_schema(tools["computer_batch"]),
            {
                "properties": [
                    "actions", "final", "final_monitor", "final_enhance",
                    "expect_focus", "force",
                ],
                "required": ["actions"],
                "defaults": {
                    "final": "ui_dump",
                    "final_monitor": "all",
                    "final_enhance": False,
                    "expect_focus": "",
                    "force": False,
                },
                "additionalProperties": False,
            },
        )
        for name in (
            "computer_batch",
            "computer_task",
            "desktop_lock",
            "desktop_unlock",
            "keyboard",
            "mouse",
            "screen_capture",
            "screen_info",
            "system_capabilities",
            "ui_click",
            "ui_dump",
            "ui_set_text",
            "window_focus",
            "window_list",
            "find_text",
            "wait_for_text",
        ):
            self.assertIsNone(tools[name].output_schema, name)

    def test_screen_capture_returns_text_before_image_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            mcp, store = build_mcp(root)
            monitors = monitorslib._ordered(
                [
                    monitorslib.Monitor(0, "DP-1", 2, 0, 2, 2, 1.0, True),
                    monitorslib.Monitor(0, "DP-2", 0, 0, 2, 2, 1.0, False),
                ]
            )

            def fake_capture(spec, out_dir, scale_long_edge, **kwargs):
                self.assertEqual(spec, "all")
                self.assertEqual(scale_long_edge, 80)
                shots = []
                for monitor, color in zip(monitors, ((220, 40, 30), (30, 80, 220))):
                    path = Path(out_dir) / f"m{monitor.index}.png"
                    Image.new("RGB", (2, 2), color).save(path)
                    shots.append(
                        capturelib.Shot(
                            path=path,
                            monitor=monitor,
                            offset=(monitor.x, monitor.y),
                            size=(2, 2),
                            scaled=(2, 2),
                            scale=1.0,
                            id=f"m{monitor.index}-a1b2c3",
                            taken_at=time.time(),
                        )
                    )
                return shots

            with (
                mock.patch.object(toolslib.capturelib, "available", return_value=(True, "")),
                mock.patch.object(toolslib.capturelib, "capture", side_effect=fake_capture),
            ):
                function = asyncio.run(mcp.get_tool("screen_capture")).fn
                blocks = function()

            self.assertEqual([block.type for block in blocks], ["text", "image", "image"])
            self.assertIn("shot: `m1-a1b2c3`", blocks[0].text)
            self.assertIn("shot: `m2-a1b2c3`", blocks[0].text)
            self.assertTrue(store.dir.is_dir())

    def test_screen_capture_passes_a_resolved_region_and_enhances_the_copy(self) -> None:
        """Step 8.5 on the wire: region resolved once, enhancement per copy."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            mcp, _store = build_mcp(root)
            monitors = monitorslib._ordered(
                [
                    monitorslib.Monitor(0, "DP-1", 1920, 0, 1920, 1080, 1.0, True),
                    monitorslib.Monitor(0, "DP-2", 0, 0, 1920, 1080, 1.0, False),
                ]
            )
            seen: list = []

            def fake_capture(spec, out_dir, scale_long_edge, **kwargs):
                seen.append((spec, kwargs.get("region")))
                (x, y, width, height), monitor = kwargs["region"]
                path = Path(out_dir) / "region.png"
                Image.new("RGB", (width, height), (8, 9, 12)).save(path)
                return [
                    capturelib.Shot(
                        path=path, monitor=monitor, offset=(x, y),
                        size=(width, height), scaled=(width, height), scale=1.0,
                        id="m2-a1b2c3", taken_at=time.time(),
                        desktop_size=(width, height), region=True,
                    )
                ]

            with (
                mock.patch.object(toolslib.capturelib, "available", return_value=(True, "")),
                mock.patch.object(toolslib.capturelib, "capture", side_effect=fake_capture),
                mock.patch.object(monitorslib, "list_monitors", return_value=monitors),
            ):
                function = asyncio.run(mcp.get_tool("screen_capture")).fn
                blocks = function(monitor="2", region=[100, 50, 400, 200], enhance=True)
                orphan = function(shot="m2-a1b2c3")

            self.assertEqual(seen[0][1][0], (2020, 50, 400, 200))
            self.assertEqual(seen[0][1][1].index, 2)
            self.assertEqual([block.type for block in blocks], ["text", "image"])
            self.assertIn("region", blocks[0].text)
            self.assertIn("🔆", blocks[0].text)
            self.assertIn("region", orphan.text)
            self.assertEqual(len(seen), 1, "a shot without a region captured something")


if __name__ == "__main__":
    unittest.main()
