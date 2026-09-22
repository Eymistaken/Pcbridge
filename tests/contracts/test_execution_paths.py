#!/usr/bin/env python3
"""Contracts for desktop-independent execution paths and tool guidance."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastmcp import Client, FastMCP


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import tools as toolslib  # noqa: E402
from pcbridge.config import AgentSpec, Config, DesktopSpec, load_config  # noqa: E402
from pcbridge.desktop.runtime import create_runtime  # noqa: E402
from pcbridge.jobs import JobManager  # noqa: E402
from pcbridge.shots import ShotStore  # noqa: E402


class FakeGate:
    def __init__(self, spec: DesktopSpec) -> None:
        self.spec = spec

    def audit(self, *args, **kwargs) -> None:
        return None

    def is_unlocked(self) -> bool:
        return True

    def remaining_seconds(self) -> int:
        return 30

    def touch(self) -> None:
        return None


def make_config(root: Path, desktop: DesktopSpec) -> Config:
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
        inline_images="false",
        default_agent="contract",
        desktop=desktop,
    )
    config.jobs_dir.mkdir(parents=True, exist_ok=True)
    return config


def build_mcp(root: Path, desktop: DesktopSpec) -> tuple[FastMCP, object]:
    config = make_config(root, desktop)
    runtime = create_runtime(config, gate=FakeGate(desktop))
    mcp = FastMCP("execution-contract")
    toolslib.register(
        mcp,
        config,
        JobManager(config.jobs_dir, default_timeout=60),
        ShotStore(config),
        transport="stdio",
        runtime=runtime,
    )
    return mcp, runtime


class ExecutionPathContractTests(unittest.IsolatedAsyncioTestCase):
    def test_gui_shell_block_is_opt_in_in_defaults_and_example(self) -> None:
        self.assertFalse(DesktopSpec().block_gui_launch_in_shell)
        public = load_config(str(ROOT / "config.example.toml"))
        self.assertFalse(public.desktop.block_gui_launch_in_shell)

    def test_explicit_gui_shell_policy_is_loaded_from_toml(self) -> None:
        text = (ROOT / "config.example.toml").read_text()
        text = text.replace(
            "block_gui_launch_in_shell = false",
            "block_gui_launch_in_shell = true",
        ).replace(
            "gui_launch_blocklist = []",
            'gui_launch_blocklist = ["Google Chrome"]',
        )
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "config.toml"
            path.write_text(text)
            loaded = load_config(str(path))

        self.assertTrue(loaded.desktop.block_gui_launch_in_shell)
        self.assertEqual(loaded.desktop.gui_launch_blocklist, ["Google Chrome"])

    async def test_default_policy_allows_a_chrome_url_command(self) -> None:
        desktop = DesktopSpec(
            enabled=True,
            gui_launch_blocklist=["Google Chrome"],
        )
        completed = SimpleNamespace(returncode=0, stdout="URL accepted\n", stderr="")
        with tempfile.TemporaryDirectory() as raw:
            mcp, runtime = build_mcp(Path(raw), desktop)
            with (
                mock.patch.object(
                    toolslib.appslib,
                    "looks_like_gui_launch",
                ) as detector,
                mock.patch.object(toolslib.subprocess, "run", return_value=completed),
            ):
                async with Client(mcp) as client:
                    result = await client.call_tool(
                        "shell_run",
                        {"command": "google-chrome https://example.invalid"},
                    )
            detector.assert_not_called()
            runtime.close()

        self.assertFalse(result.is_error)
        self.assertIn("URL accepted", result.content[0].text)

    async def test_explicit_gui_shell_blocklist_still_blocks(self) -> None:
        desktop = DesktopSpec(
            enabled=True,
            block_gui_launch_in_shell=True,
            gui_launch_blocklist=["Google Chrome"],
        )
        with tempfile.TemporaryDirectory() as raw:
            mcp, runtime = build_mcp(Path(raw), desktop)
            with (
                mock.patch.object(
                    toolslib.appslib,
                    "looks_like_gui_launch",
                    return_value="Google Chrome",
                ),
                mock.patch.object(toolslib.subprocess, "run") as run,
            ):
                async with Client(mcp) as client:
                    result = await client.call_tool(
                        "shell_run",
                        {"command": "google-chrome https://example.invalid"},
                    )
            runtime.close()

        self.assertFalse(result.is_error)
        self.assertIn('window_focus("Google Chrome")', result.content[0].text)
        run.assert_not_called()

    async def test_desktop_disabled_keeps_shell_filesystem_and_jobs_available(self) -> None:
        desktop = DesktopSpec(
            enabled=False,
            block_gui_launch_in_shell=True,
            gui_launch_blocklist=["Google Chrome"],
        )
        completed = SimpleNamespace(returncode=0, stdout="shell-ok\n", stderr="")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            mcp, runtime = build_mcp(root, desktop)
            with mock.patch.object(toolslib.subprocess, "run", return_value=completed):
                async with Client(mcp) as client:
                    shell = await client.call_tool(
                        "shell_run", {"command": "printf shell-ok"}
                    )
                    written = await client.call_tool(
                        "fs_write", {"path": "contract.txt", "content": "file-ok"}
                    )
                    jobs = await client.call_tool("job_list")
            file_content = (root / "contract.txt").read_text()
            runtime.close()

        self.assertIn("shell-ok", shell.content[0].text)
        self.assertIn("Written", written.content[0].text)
        self.assertEqual(file_content, "file-ok")
        self.assertIn("No jobs recorded", jobs.content[0].text)

    async def test_tool_descriptions_present_multiple_execution_paths(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            mcp, runtime = build_mcp(Path(raw), DesktopSpec())
            tools = {
                tool.name: tool
                for tool in await mcp.list_tools()
            }
            runtime.close()

        shell_text = tools["shell_run"].description
        background_text = tools["shell_run_background"].description
        ui_text = tools["ui_dump"].description
        unlock_text = tools["desktop_unlock"].description
        focus_text = tools["window_focus"].description

        self.assertNotIn("Do NOT use this", shell_text)
        self.assertNotIn("Do NOT use this", background_text)
        self.assertNotIn("cannot read images", ui_text)
        self.assertIn("pcbridge", unlock_text.lower())
        self.assertIn(
            "operating-system permissions are separate",
            unlock_text.lower(),
        )
        self.assertNotIn("ONLY correct way", focus_text)
        self.assertIn("process lifetime", focus_text)


if __name__ == "__main__":
    unittest.main()
