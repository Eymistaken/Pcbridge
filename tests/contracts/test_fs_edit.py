#!/usr/bin/env python3
"""`fs_edit` and `fs_write`: targeted, atomic, conflict-checked changes.

The ROADMAP's "Done when" for targeted edits: a small edit to a large file
changes only the requested region, concurrent changes produce a conflict
instead of silent replacement, and multiline UTF-8 edits can be submitted
without shell escaping. Runs through a real FastMCP client.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

from fastmcp import Client

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import files as fileslib  # noqa: E402
from tests.contracts.test_mcp_errors import build_mcp  # noqa: E402

MULTILINE_OLD = 'def selam():\n    return "merhaba"\n'
MULTILINE_NEW = ('def selam(ad: str) -> str:\n    # çğıöşü İ — "tırnak" \'tek\' $HOME `ls` \\n 🙂\n'
                 '    return f"merhaba {ad}"\n')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class EditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        mcp, _tree = build_mcp(self.root)
        self.client = Client(mcp)
        await self.client.__aenter__()

    async def asyncTearDown(self) -> None:
        await self.client.__aexit__(None, None, None)
        self.tmp.cleanup()

    async def tool(self, name: str, **args) -> str:
        return (await self.client.call_tool(name, args)).content[0].text

    def file(self, name: str, data: bytes | str) -> Path:
        p = self.root / name
        p.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        return p

    def leftovers(self) -> list[str]:
        return [p.name for p in self.root.iterdir() if p.name.endswith(".pcbridge-tmp")]

    async def test_small_edit_to_a_large_file_changes_only_that_region(self) -> None:
        lines = [f"satır {i:06d} {'x' * 60}\n" for i in range(80_000)]
        lines[40_000] = "TARGET = 1\n"
        before = "".join(lines).encode("utf-8")
        self.assertGreater(len(before), 5_000_000)
        f = self.file("big.py", before)
        out = await self.tool("fs_edit", path=str(f), old_text="TARGET = 1\n",
                              new_text="TARGET = 2  # değişti\n")
        self.assertTrue(out.startswith("Edited"), out)
        self.assertIn("at line(s) 40,001", out)
        self.assertIn("-TARGET = 1", out)
        self.assertIn("+TARGET = 2  # değişti", out)
        after = f.read_bytes()
        i = before.index(b"TARGET = 1\n")
        self.assertEqual(after[:i], before[:i])
        self.assertEqual(after[i:].split(b"\n", 1)[1], before[i:].split(b"\n", 1)[1])
        self.assertIn(sha(after), out)
        self.assertLess(len(out), 6000)
        self.assertEqual(self.leftovers(), [])

    async def test_multiline_utf8_edit_needs_no_escaping(self) -> None:
        f = self.file("m.py", "import os\n\n" + MULTILINE_OLD + "\nprint(selam())\n")
        out = await self.tool("fs_edit", path=str(f), old_text=MULTILINE_OLD,
                              new_text=MULTILINE_NEW)
        self.assertTrue(out.startswith("Edited"), out)
        self.assertEqual(f.read_text(encoding="utf-8"),
                         "import os\n\n" + MULTILINE_NEW + "\nprint(selam())\n")

    async def test_conflict_when_the_file_changed_since_it_was_read(self) -> None:
        f = self.file("c.txt", "a = 1\nb = 2\n")
        read = await self.tool("fs_read", path=str(f))
        digest = re.search(r"sha256 ([0-9a-f]{64})", read).group(1)
        f.write_text("a = 1\nb = 2\nc = 3  # someone else\n", encoding="utf-8")
        out = await self.tool("fs_edit", path=str(f), old_text="b = 2", new_text="b = 20",
                              expected_sha256=digest)
        self.assertTrue(out.startswith("Not edited: Conflict"), out)
        self.assertIn("someone else", f.read_text())
        self.assertIn("b = 2\n", f.read_text())
        ok = await self.tool("fs_edit", path=str(f), old_text="b = 2", new_text="b = 20",
                             expected_sha256=sha(f.read_bytes())[:12])
        self.assertTrue(ok.startswith("Edited"), ok)

    async def test_conflict_when_the_file_changes_during_the_write(self) -> None:
        f = self.file("r.txt", "one\n")
        before = f.read_bytes()
        f.write_text("two\n")
        with self.assertRaisesRegex(fileslib.FileToolError, "changed while"):
            fileslib.atomic_write(f, b"three\n", before=before)
        self.assertEqual(f.read_text(), "two\n")
        self.assertEqual(self.leftovers(), [])

    async def test_ambiguous_match_changes_nothing_and_names_lines(self) -> None:
        f = self.file("a.txt", "x = 1\ny = 2\nx = 1\n")
        out = await self.tool("fs_edit", path=str(f), old_text="x = 1", new_text="x = 9")
        self.assertIn("matches 2 places (lines 1, 3)", out)
        self.assertEqual(f.read_text(), "x = 1\ny = 2\nx = 1\n")
        out = await self.tool("fs_edit", path=str(f), old_text="x = 1", new_text="x = 9",
                              replace_all=True)
        self.assertIn("2 replacement(s)", out)
        self.assertEqual(f.read_text(), "x = 9\ny = 2\nx = 9\n")

    async def test_missing_match_with_an_indentation_hint(self) -> None:
        f = self.file("i.py", "def f():\n    if x:\n        return 1\n")
        out = await self.tool("fs_edit", path=str(f), old_text="if x:\n    return 1",
                              new_text="if y:\n    return 2")
        self.assertIn("was not found", out)
        self.assertIn("near line 2", out)
        self.assertEqual(f.read_text(), "def f():\n    if x:\n        return 1\n")

    async def test_several_edits_are_all_or_nothing(self) -> None:
        f = self.file("s.txt", "alpha\nbeta\ngamma\n")
        out = await self.tool("fs_edit", path=str(f), edits=[
            {"old_text": "alpha", "new_text": "ALPHA"},
            {"old_text": "missing", "new_text": "x"}])
        self.assertIn("Edit 2: old_text was not found", out)
        self.assertEqual(f.read_text(), "alpha\nbeta\ngamma\n")
        out = await self.tool("fs_edit", path=str(f), edits=[
            {"old_text": "alpha", "new_text": "ALPHA"},
            {"old_text": "ALPHA\nbeta", "new_text": "AB"}])
        self.assertTrue(out.startswith("Edited"), out)
        self.assertEqual(f.read_text(), "AB\ngamma\n")

    async def test_crlf_and_bom_are_kept(self) -> None:
        f = self.file("w.txt", "\ufeffbir\r\niki\r\nüç\r\n".encode("utf-8"))
        out = await self.tool("fs_edit", path=str(f), old_text="iki\nüç\n",
                              new_text="İKİ\nÜÇ\ndört\n")
        self.assertTrue(out.startswith("Edited"), out)
        self.assertEqual(f.read_bytes(), "\ufeffbir\r\nİKİ\r\nÜÇ\r\ndört\r\n".encode("utf-8"))

    async def test_dry_run_writes_nothing(self) -> None:
        f = self.file("d.txt", "keep\n")
        out = await self.tool("fs_edit", path=str(f), old_text="keep", new_text="gone",
                              dry_run=True)
        self.assertTrue(out.startswith("Would change"), out)
        self.assertIn("(nothing written)", out)
        self.assertIn("+gone", out)
        self.assertEqual(f.read_text(), "keep\n")

    async def test_mode_is_kept(self) -> None:
        f = self.file("run.sh", "#!/bin/sh\necho a\n")
        os.chmod(f, 0o750)
        await self.tool("fs_edit", path=str(f), old_text="echo a", new_text="echo b")
        self.assertEqual(f.stat().st_mode & 0o777, 0o750)

    async def test_binary_and_missing_files_are_refused(self) -> None:
        png = self.file("a.png", b"\x89PNG\r\n\x1a\n\x00\x00")
        out = await self.tool("fs_edit", path=str(png), old_text="PNG", new_text="x")
        self.assertIn("PNG image", out)
        out = await self.tool("fs_edit", path=str(self.root / "nope.txt"), old_text="a",
                              new_text="b")
        self.assertIn("File not found", out)
        out = await self.tool("fs_edit", path=str(png))
        self.assertIn("Give old_text and new_text", out)

    async def test_write_is_atomic_and_conditional(self) -> None:
        f = self.root / "new" / "note.txt"
        out = await self.tool("fs_write", path=str(f), content="ilk\n", create_only=True)
        self.assertIn(sha("ilk\n".encode()), out)
        again = await self.tool("fs_write", path=str(f), content="x", create_only=True)
        self.assertIn("already exists", again)
        stale = await self.tool("fs_write", path=str(f), content="x", expected_sha256="0" * 64)
        self.assertIn("Conflict", stale)
        self.assertEqual(f.read_text(), "ilk\n")
        ok = await self.tool("fs_write", path=str(f), content="ikinci\n",
                             expected_sha256=sha(b"ilk\n"))
        self.assertTrue(ok.startswith("Written"), ok)
        app = await self.tool("fs_write", path=str(f), content="üçüncü\n", append=True)
        self.assertTrue(app.startswith("Appended"), app)
        self.assertEqual(f.read_text(encoding="utf-8"), "ikinci\nüçüncü\n")
        self.assertEqual([p.name for p in f.parent.iterdir()], ["note.txt"])
        umask = os.umask(0)
        os.umask(umask)
        self.assertEqual(f.stat().st_mode & 0o777, 0o666 & ~umask)

    async def test_bad_expected_hash_is_explained(self) -> None:
        f = self.file("h.txt", "a\n")
        out = await self.tool("fs_edit", path=str(f), old_text="a", new_text="b",
                              expected_sha256="abc")
        self.assertIn("at least the first 12 digits", out)
        self.assertEqual(f.read_text(), "a\n")


if __name__ == "__main__":
    unittest.main()
