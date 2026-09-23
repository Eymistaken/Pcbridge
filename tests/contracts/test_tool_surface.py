#!/usr/bin/env python3
"""The tool surface (Step 7 of 2.0): every tool carries all four MCP hints
from one table, and `[tools] profile` offers the documented sets.

The exact hint values are pinned in test_mcp_contract.py.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import app as applib  # noqa: E402
from pcbridge import tools as toolslib  # noqa: E402
from pcbridge.config import ConfigError, load_config  # noqa: E402

CONFIG = """\
config_version = 2
public_url = "http://localhost:8765"
default_agent = "claude"
[auth]
password = "tool-surface-test-password"
[paths]
state_dir = "{state}"
[desktop]
enabled = false
[agents.claude]
command = ["true"]
{tools}
"""


def build(profile: str | None) -> list:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        cfg_path = root / "config.toml"
        tools = f'[tools]\nprofile = "{profile}"' if profile else ""
        cfg_path.write_text(CONFIG.format(state=root / "state", tools=tools))
        cfg_path.chmod(0o600)
        with mock.patch.dict(os.environ, {"PCBRIDGE_CONFIG": str(cfg_path)}):
            cfg = load_config()
            mcp, _provider = applib.build_app(cfg, transport="stdio")
            return asyncio.run(mcp.list_tools())


class ToolSurfaceTests(unittest.TestCase):
    def test_full_is_the_default_and_every_tool_has_all_four_hints(self) -> None:
        tools = build(None)
        self.assertEqual({t.name for t in tools}, set(toolslib.TOOL_HINTS))
        self.assertEqual(len(tools), 37)
        for tool in tools:
            a = tool.annotations
            for hint in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
                self.assertIsNotNone(getattr(a, hint), f"{tool.name}: {hint} unset")
            self.assertTrue(a.title, tool.name)
            # A read-only tool that also claims to be destructive is a mistake.
            if a.readOnlyHint:
                self.assertFalse(a.destructiveHint, tool.name)

    def test_tools_that_drive_the_machine_are_never_read_only(self) -> None:
        drivers = {"shell_run", "shell_run_background", "agent_run", "tmux_send", "tmux_keys",
                   "fs_write", "keyboard", "mouse", "computer_batch", "computer_task",
                   "ui_click", "ui_set_text", "window_focus", "desktop_unlock", "job_cancel"}
        for name in drivers:
            ro, de, _idem, _ow = toolslib.TOOL_HINTS[name]
            self.assertFalse(ro, name)
            self.assertTrue(de, name)

    def test_profiles_offer_the_documented_sets(self) -> None:
        core = {t.name for t in build("core")}
        desktop = {t.name for t in build("desktop")}
        self.assertTrue(core.isdisjoint(toolslib.DESKTOP_TOOLS))
        self.assertIn("shell_run", core)
        self.assertLessEqual(toolslib.DESKTOP_TOOLS, desktop)
        self.assertNotIn("shell_run", desktop)
        self.assertIn("job_status", desktop)  # computer_task answers with a job id
        self.assertEqual(core | toolslib.DESKTOP_TOOLS, set(toolslib.TOOL_HINTS))

    def test_an_unknown_profile_is_refused_in_english(self) -> None:
        with self.assertRaises(ConfigError) as refused:
            build("tiny")
        self.assertIn("full, core, desktop", refused.exception.message)


if __name__ == "__main__":
    unittest.main()
