"""The tool catalog shown by `pcbridge tools` and the terminal UI is built
from the server's own registration, so it lists exactly what a client gets.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import toolcatalog  # noqa: E402
from pcbridge import tools as toolslib  # noqa: E402
from pcbridge.config import load_config  # noqa: E402

CONFIG = """\
config_version = 2
public_url = "http://localhost:8765"
[auth]
password = "tool-catalog-test-password"
[paths]
state_dir = "{state}"
[desktop]
enabled = {enabled}
[agents.claude]
command = ["true"]
[tools]
profile = "{profile}"
"""


def catalog(profile: str = "full", enabled: bool = False) -> list[toolcatalog.ToolInfo]:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        path = root / "config.toml"
        path.write_text(CONFIG.format(state=root / "state", profile=profile,
                                      enabled=str(enabled).lower()))
        path.chmod(0o600)
        with mock.patch.dict(os.environ, {"PCBRIDGE_CONFIG": str(path)}):
            return toolcatalog.build(load_config())


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.full = catalog("full", enabled=True)

    def test_every_registered_tool_is_listed_once_with_its_hints(self) -> None:
        names = [t.name for t in self.full]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), set(toolslib.TOOL_HINTS))
        for t in self.full:
            ro, de, idem, ow = toolslib.TOOL_HINTS[t.name]
            self.assertEqual((t.read_only, t.destructive, t.idempotent, t.open_world),
                             (ro, de, idem, ow), t.name)
            self.assertTrue(t.title and t.description, t.name)
            self.assertIn(t.group, toolcatalog.GROUPS)
            self.assertTrue(all(t.active for t in self.full))
            self.assertEqual(t.state, "active", t.name)

    def test_the_active_set_follows_the_profile(self) -> None:
        for profile in ("core", "desktop"):
            with self.subTest(profile=profile):
                tools = catalog(profile, enabled=True)
                self.assertEqual({t.name for t in tools}, set(toolslib.TOOL_HINTS))
                self.assertEqual({t.name for t in tools if t.active},
                                 set(toolslib.tools_in_profile(profile)))
                self.assertTrue(all(t.state == "not in profile" for t in tools if not t.active))

    def test_desktop_tools_say_when_desktop_control_is_off(self) -> None:
        tools = {t.name: t for t in catalog("full", enabled=False)}
        self.assertEqual(tools["mouse"].state, "needs desktop")
        self.assertEqual(tools["system_capabilities"].state, "active")
        self.assertEqual(tools["shell_run"].state, "active")

    def test_parameters_are_listed(self) -> None:
        agent_run = next(t for t in self.full if t.name == "agent_run")
        params = {p.name: p for p in agent_run.params}
        self.assertTrue(params["prompt"].required)
        self.assertFalse(params["model"].required)
        self.assertTrue(params["prompt"].description)

    def test_search_by_name_title_and_description(self) -> None:
        names = [t.name for t in toolcatalog.search(self.full, "screen")]
        self.assertIn("screen_capture", names)
        self.assertLess(names.index("screen_capture"), len(names))
        # Name matches rank before description matches.
        first_desc_only = next(i for i, t in enumerate(toolcatalog.search(self.full, "screen"))
                               if "screen" not in t.name)
        self.assertTrue(all("screen" in n for n in names[:first_desc_only]))
        # Every word must match; "job cancel" finds the one tool.
        self.assertEqual([t.name for t in toolcatalog.search(self.full, "job cancel")][0], "job_cancel")
        self.assertEqual(toolcatalog.search(self.full, "zzzz-no-such-tool"), [])
        self.assertEqual(len(toolcatalog.search(self.full, "  ")), len(self.full))


if __name__ == "__main__":
    unittest.main()
