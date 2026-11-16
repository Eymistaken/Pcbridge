"""Every release body opens with the install commands.

From v2.10.0 to v2.10.3 the written notes replaced the whole draft body and
the install block that release.yml had put there was gone. The block now has
one source (`.github/release-install.md`) and one way into a body
(`scripts/release_notes.py`), which the release workflow and the
release-notes workflow both use. These tests hold that together.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release_notes.py"
LINUX = "curl -fsSL https://github.com/Eymistaken/Pcbridge/releases/latest/download/install.sh | sh"
WINDOWS = "irm https://github.com/Eymistaken/Pcbridge/releases/latest/download/install.ps1 | iex"

NOTES = "This release fixes four problems.\n\n## One\n\nText.\n\n---\n\nA rule inside the notes.\n"
# The block as v2.9.4 had it: a heading, the commands, then a rule.
OLD_BODY = ("## Install\n\n```bash\n" + LINUX + "\n```\n\nOlder wording.\n\n---\n\n\n"
            "## What changed\n\nText.\n")


def load():
    spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["release_notes"] = mod
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(SCRIPT.is_file(), "scripts/ is not next to the tests (a package's test copy)")
class ReleaseNotesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rn = load()

    def test_a_composed_body_starts_with_the_block(self) -> None:
        for windows in (True, False):
            with self.subTest(windows=windows):
                body = self.rn.compose("v2.11.0", NOTES, windows)
                block = self.rn.install_block("v2.11.0", windows)
                self.assertTrue(body.startswith("## Install\n"))
                self.assertTrue(body.startswith(block + "\n---\n\n"))
                self.assertTrue(body.endswith(NOTES))
                self.assertTrue(self.rn.starts_with_block(body, "v2.11.0", windows))
                self.assertFalse(self.rn.starts_with_block(NOTES, "v2.11.0", windows))

    def test_the_block_has_both_commands_and_the_version(self) -> None:
        block = self.rn.install_block("v2.11.0", windows=True)
        self.assertIn(LINUX, block)
        self.assertIn(WINDOWS, block)
        self.assertIn("--version v2.11.0", block)
        self.assertIn('$env:PCBRIDGE_VERSION = "v2.11.0"', block)
        self.assertIn("/blob/v2.11.0/docs/install.md", block)
        self.assertNotIn("{{", block)
        self.assertNotIn("<!--", block)

    def test_a_release_without_a_windows_wheel_gets_no_windows_lines(self) -> None:
        block = self.rn.install_block("v2.10.3", windows=False)
        self.assertIn(LINUX, block)
        self.assertNotIn("install.ps1", block)
        self.assertNotIn("PowerShell", block)
        self.assertNotIn("<!--", block)
        self.assertNotIn("\n\n\n", block)

    def test_composing_again_changes_nothing(self) -> None:
        once = self.rn.compose("v2.11.0", NOTES)
        self.assertEqual(self.rn.compose("v2.11.0", once), once)

    def test_an_older_block_is_replaced_not_doubled(self) -> None:
        body = self.rn.compose("v2.9.4", OLD_BODY, windows=False)
        self.assertEqual(body.count("## Install"), 1)
        self.assertNotIn("Older wording", body)
        self.assertTrue(body.endswith("## What changed\n\nText.\n"))

    def test_the_rule_inside_the_notes_is_kept(self) -> None:
        body = self.rn.compose("v2.11.0", NOTES)
        self.assertIn("A rule inside the notes.", body)
        self.assertEqual(self.rn.strip_install_block(body), NOTES)

    def test_a_tag_is_required(self) -> None:
        for bad in ("2.11.0", "main", "v2.11", "v2.11.0; rm -rf ~"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.rn.install_block(bad)

    def _ensure(self, body: str, windows: bool = False, **kw) -> list[str]:
        from unittest import mock

        edits: list[str] = []
        with mock.patch.object(self.rn, "_release", return_value=(body, windows)), \
                mock.patch.object(self.rn, "_edit", side_effect=lambda v, b: edits.append(b)), \
                mock.patch("builtins.print"):
            self.rn._ensure("v2.11.0", **kw)
        return edits

    def test_ensure_adds_a_missing_block_and_leaves_a_good_body_alone(self) -> None:
        edits = self._ensure(NOTES)
        self.assertEqual(edits, [self.rn.compose("v2.11.0", NOTES, windows=False)])
        self.assertEqual(self._ensure(edits[0]), [])

    def test_the_daily_pass_leaves_an_older_block_as_it_is(self) -> None:
        self.assertEqual(self._ensure(OLD_BODY, keep_older=True), [])
        self.assertEqual(len(self._ensure(NOTES, keep_older=True)), 1)
        # Asked for one tag, an older block is brought up to date.
        self.assertEqual(len(self._ensure(OLD_BODY)), 1)

    def test_the_workflows_write_bodies_through_the_script(self) -> None:
        release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("scripts/release_notes.py block", release)
        self.assertNotIn('sed "s/{{VERSION}}', release)
        # The draft carries the assets the block's Windows lines point to.
        self.assertIn("cp install.ps1 dist/install.ps1", release)
        self.assertIn("name: wheel-windows", release)
        notes = (ROOT / ".github/workflows/release-notes.yml").read_text(encoding="utf-8")
        self.assertRegex(notes, r"types: \[published, edited\]")
        self.assertIn("scripts/release_notes.py ensure", notes)
        # The "edited" event started no run on 2026-11-16: a daily pass backs it up.
        self.assertIn("schedule:", notes)
        self.assertIn("scripts/release_notes.py ensure-recent", notes)
        # The tag goes through the environment, never into the script's text.
        self.assertNotRegex(notes, re.compile(r"run:.*\$\{\{\s*github\.event", re.S))

    def test_the_procedure_is_documented(self) -> None:
        doc = (ROOT / "docs/dev/contributing.md").read_text(encoding="utf-8")
        self.assertIn("scripts/release_notes.py publish", doc)


if __name__ == "__main__":
    unittest.main()
