"""The computer-use skill comes with pcbridge, two ways.

Every MCP client can list and read it (`skill://computer-use/SKILL.md`), and
`pcbridge setup` links it into Claude Code's skills directory, which the 2.0
setup had stopped doing.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastmcp import Client

from pcbridge import app as applib
from pcbridge.cli import install as inst
from pcbridge.config import load_config
from tests.contracts.test_tool_surface import CONFIG

SKILL_URI = "skill://computer-use/SKILL.md"


class SkillResourceTests(unittest.TestCase):
    def serve(self, transport: str):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cfg_path = root / "config.toml"
            cfg_path.write_text(CONFIG.format(state=root / "state", tools=""))
            cfg_path.chmod(0o600)
            with mock.patch.dict(os.environ, {"PCBRIDGE_CONFIG": str(cfg_path)}):
                mcp, _provider = applib.build_app(load_config(), transport=transport)

            async def go():
                async with Client(mcp) as client:
                    listed = [str(r.uri) for r in await client.list_resources()]
                    body = (await client.read_resource(SKILL_URI))[0].text
                    tools = await client.list_tools()
                    return listed, body, len(tools)

            return asyncio.run(go())

    def test_every_client_can_list_and_read_the_skill(self) -> None:
        for transport in ("stdio", "http"):
            listed, body, tools = self.serve(transport)
            self.assertIn(SKILL_URI, listed, transport)
            self.assertIn("name: computer-use", body, transport)
            self.assertEqual(body, inst.skill_source().joinpath("SKILL.md").read_text())
            self.assertEqual(tools, 40, "the skill adds resources, not tools")


class SkillLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.claude = self.root / ".claude"
        self.link = self.claude / "skills" / "computer-use"
        self.backup = inst.Backup(root=self.root / "backup")

    def test_no_claude_code_means_no_link(self) -> None:
        note = inst.install_skill_link(self.backup, self.claude)
        self.assertIn("does not exist", note)
        self.assertFalse(self.claude.exists())

    def test_a_missing_link_is_made(self) -> None:
        self.claude.mkdir()
        inst.install_skill_link(self.backup, self.claude)
        self.assertEqual(Path(os.readlink(self.link)), inst.skill_source())
        self.assertTrue((self.link / "SKILL.md").is_file())
        inst.install_skill_link(self.backup, self.claude)  # idempotent
        self.assertEqual(self.backup.entries, [])

    def test_a_working_link_of_the_user_stays(self) -> None:
        own = self.root / "checkout" / "skills" / "computer-use"
        own.mkdir(parents=True)
        (own / "SKILL.md").write_text("mine")
        self.link.parent.mkdir(parents=True)
        self.link.symlink_to(own)
        inst.install_skill_link(self.backup, self.claude)
        self.assertEqual(Path(os.readlink(self.link)), own)
        self.assertFalse(inst.remove_skill_link(self.backup, self.claude), "not ours to remove")
        self.assertTrue(self.link.is_symlink())

    def test_a_broken_link_is_saved_and_made_again(self) -> None:
        self.link.parent.mkdir(parents=True)
        self.link.symlink_to(self.root / "old-venv" / "computer-use")
        inst.install_skill_link(self.backup, self.claude)
        self.assertEqual(Path(os.readlink(self.link)), inst.skill_source())
        self.assertEqual(len(self.backup.entries), 1)

    def test_a_real_directory_is_left_alone(self) -> None:
        self.link.mkdir(parents=True)
        (self.link / "SKILL.md").write_text("edited by hand")
        note = inst.install_skill_link(self.backup, self.claude)
        self.assertIn("left as it is", note)
        self.assertEqual((self.link / "SKILL.md").read_text(), "edited by hand")

    def test_uninstall_moves_our_link_to_the_backup(self) -> None:
        self.claude.mkdir()
        inst.install_skill_link(self.backup, self.claude)
        self.assertTrue(inst.remove_skill_link(self.backup, self.claude))
        self.assertFalse(self.link.is_symlink())
        self.assertTrue(inst.skill_source().is_dir(), "only the link goes")


if __name__ == "__main__":
    unittest.main()
