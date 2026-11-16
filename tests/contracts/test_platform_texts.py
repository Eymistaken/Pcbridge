#!/usr/bin/env python3
"""What a model reads must be true on the system it runs on.

The tool names are the same on Linux and Windows (renaming one breaks every
connected client), so the descriptions and the server instructions are what
tell a model where it is. On Windows they once said "the user's Linux
desktop", named GNOME's Shell extension and tmux's attach command. This
builds the whole tool surface for each system, with the system forced, and
checks every tool description, every parameter description and the
instructions for the other system's words.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import app as applib  # noqa: E402
from pcbridge import oscompat  # noqa: E402
from pcbridge import tools as toolslib  # noqa: E402
from pcbridge.config import load_config  # noqa: E402

CONFIG = """\
config_version = 2
public_url = "http://localhost:8765"
default_agent = "claude"
[auth]
password = "platform-texts-test-password"
[paths]
state_dir = '{state}'
[desktop]
enabled = false
[agents.claude]
command = ["true"]
"""

# Words that are only true on Linux, and only true on Windows. "tmux" is not
# here: on Windows the tools are still called tmux_*, and their texts say the
# sessions there are ConPTY, not tmux.
LINUX_ONLY = re.compile(
    r"\b(Linux|GNOME|Wayland|uinput|systemd|systemctl|journalctl|Hyprland|KDE|KWin|Plasma|"
    r"X11|AT-SPI|GTK|bash|gtk-launch|tmux attach)\b", re.I)
WINDOWS_ONLY = re.compile(
    r"\b(Windows|ConPTY|PowerShell|UI Automation|SendInput|Start menu|tray|Notepad|"
    r"pcbridge attach|DXGI)\b")


def surface(windows: bool) -> tuple[list[tuple[str, str]], str]:
    """(where, text) for every tool and parameter description, and the instructions."""
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        cfg_path = root / "config.toml"
        cfg_path.write_text(CONFIG.format(state=root / "state"))
        cfg_path.chmod(0o600)
        with mock.patch.dict(os.environ, {"PCBRIDGE_CONFIG": str(cfg_path)}), \
                mock.patch.object(oscompat, "IS_WINDOWS", windows):
            cfg = load_config()
            mcp, _provider = applib.build_app(cfg, transport="stdio")
            tools = asyncio.run(mcp.list_tools())
            instructions = mcp.instructions or ""
    texts = []
    for tool in tools:
        texts.append((tool.name, tool.description or ""))
        for name, schema in (tool.parameters or {}).get("properties", {}).items():
            texts.append((f"{tool.name}.{name}", schema.get("description") or ""))
    return texts, instructions


def hits(texts: list[tuple[str, str]], rx: re.Pattern[str]) -> list[str]:
    out = []
    for where, text in texts:
        for m in rx.finditer(text):
            start = max(0, m.start() - 40)
            out.append(f"{where}: ...{text[start:m.end() + 40]}...")
    return out


class PlatformTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.windows, cls.windows_instructions = surface(windows=True)
        # On Windows (CI's portable job) the Windows surface is the real one;
        # the Linux one is built only where Linux is the system.
        if sys.platform == "win32":
            cls.linux, cls.linux_instructions = [], ""
        else:
            cls.linux, cls.linux_instructions = surface(windows=False)

    def setUp(self) -> None:
        if not self.linux and self._testMethodName != "test_windows_texts_name_nothing_linux_only":
            self.skipTest("the Linux surface is checked on Linux")

    def test_the_same_tools_on_both_systems(self) -> None:
        self.assertEqual({w for w, _ in self.windows}, {w for w, _ in self.linux})
        self.assertEqual(len([w for w, _ in self.windows if "." not in w]), 40)

    def test_windows_texts_name_nothing_linux_only(self) -> None:
        found = hits(self.windows + [("instructions", self.windows_instructions)], LINUX_ONLY)
        self.assertEqual(found, [], "Linux-only words on Windows:\n" + "\n".join(found))

    def test_linux_texts_name_nothing_windows_only(self) -> None:
        found = hits(self.linux + [("instructions", self.linux_instructions)], WINDOWS_ONLY)
        self.assertEqual(found, [], "Windows-only words on Linux:\n" + "\n".join(found))

    def test_each_system_is_named_where_it_matters(self) -> None:
        win = dict(self.windows)
        lin = dict(self.linux)
        self.assertIn("user's Windows desktop computer", self.windows_instructions)
        self.assertIn("user's Linux desktop computer", self.linux_instructions)
        self.assertIn("pcbridge attach NAME", self.windows_instructions)
        self.assertIn("On Hyprland", self.linux_instructions)
        self.assertIn("PowerShell", win["shell_run"])
        self.assertIn("bash", lin["shell_run"])
        self.assertIn("ConPTY", win["tmux_list"])
        self.assertIn("tray icon", win["panel_icon"])
        self.assertIn("GNOME top bar", lin["panel_icon"])
        self.assertIn("Start menu", win["window_focus.window"])
        self.assertIn("GNOME Shell extension", lin["window_focus"])

    def test_the_instructions_follow_this_system_by_default(self) -> None:
        self.assertEqual(applib.INSTRUCTIONS, applib.instructions(oscompat.IS_WINDOWS))


class WindowsDocTests(unittest.TestCase):
    def _tool(self) -> object:
        def tool() -> None:
            """Runs on the user's Linux
            desktop with bash."""
        return tool

    def test_a_phrase_may_cross_a_line_break(self) -> None:
        fn = toolslib._windows_doc(True, {"user's Linux desktop": "user's desktop"})(self._tool())
        self.assertNotIn("Linux", fn.__doc__)
        self.assertIn("user's desktop with bash", fn.__doc__)

    def test_linux_keeps_the_docstring(self) -> None:
        fn = toolslib._windows_doc(False, {"user's Linux desktop": "user's desktop"})(self._tool())
        self.assertIn("Linux", fn.__doc__)

    def test_a_missing_phrase_fails_loudly(self) -> None:
        with self.assertRaises(AssertionError):
            toolslib._windows_doc(True, {"GNOME": "Windows"})(self._tool())


if __name__ == "__main__":
    unittest.main()
