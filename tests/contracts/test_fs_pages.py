#!/usr/bin/env python3
"""`fs_read` pages: every part of a file larger than one page is reachable.

The ROADMAP's "Done when" for paginated reads: an agent reads the beginning,
middle and end of a file larger than the response cap through file tools
alone, without dropped or duplicated content. The page-level tests check
the bytes; the tool-level tests follow cursors through a real FastMCP
client, the way an agent would, and rebuild the file from the replies.
"""

from __future__ import annotations

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


def big_text() -> str:
    """Over 160,000 characters: markers, Turkish letters, emoji, a very long
    line, blank lines and backticks, so page edges land on all of them."""
    lines = ["BEGIN-MARKER"]
    for i in range(1, 1400):
        if i == 700:
            lines.append("MIDDLE-MARKER")
        elif i % 97 == 0:
            lines.append("")
        elif i % 211 == 0:
            lines.append("uzun satır " + "çğıöşü✓🙂" * 2000)
        elif i % 50 == 0:
            lines.append("```python fence ```` inside")
        else:
            lines.append(f"line {i:04d} şçğ — {'x' * (i % 120)}")
    lines.append("END-MARKER")
    return "\n".join(lines) + "\n"


def collect(path: Path, budget: int, **first) -> list[fileslib.Page]:
    pages = [fileslib.read_page(path, max_chars=budget, **first)]
    while pages[-1].next_cursor:
        pages.append(fileslib.read_page(path, cursor=pages[-1].next_cursor, max_chars=budget))
        assert len(pages) < 10_000
    return pages


class PageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, name: str, data: str | bytes) -> Path:
        p = self.dir / name
        if isinstance(data, str):
            p.write_bytes(data.encode("utf-8"))
        else:
            p.write_bytes(data)
        return p

    def test_following_cursors_rebuilds_the_file_exactly(self) -> None:
        text = big_text()
        self.assertGreater(len(text), fileslib.PAGE_MAX)
        f = self.write("big.txt", text)
        for budget in (200, 997, 8000, 60000):
            pages = collect(f, budget)
            self.assertEqual("".join(p.text for p in pages), text, budget)
            for a, b in zip(pages, pages[1:]):
                self.assertEqual(a.end, b.start)
                self.assertEqual(b.first_line, a.last_line + (0 if a.ends_mid_line else 1))
            self.assertTrue(pages[-1].eof)
            self.assertLessEqual(max(len(p.text) for p in pages), budget)

    def test_pages_end_on_line_boundaries_unless_a_line_is_longer(self) -> None:
        f = self.write("big.txt", big_text())
        for p in collect(f, 8000):
            if not p.eof and not p.ends_mid_line:
                self.assertTrue(p.text.endswith("\n"))
            if p.ends_mid_line:
                self.assertNotIn("\n", p.text[:-1] if p.starts_mid_line else p.text)

    def test_line_numbers_match_the_file(self) -> None:
        text = big_text()
        f = self.write("big.txt", text)
        lines = text.split("\n")
        snap = fileslib.snapshot(f)
        self.assertEqual(snap.lines, len(lines) - 1)
        mid = fileslib.read_page(f, start_line=701, max_lines=1)
        self.assertEqual(mid.text, "MIDDLE-MARKER\n")
        self.assertEqual((mid.first_line, mid.last_line), (701, 701))
        end = fileslib.read_page(f, start_line=-1)
        self.assertEqual(end.text, "END-MARKER\n")
        self.assertEqual(end.first_line, snap.lines)
        self.assertTrue(end.eof)

    def test_negative_start_reads_the_tail(self) -> None:
        f = self.write("log.txt", "".join(f"{i}\n" for i in range(1, 501)))
        p = fileslib.read_page(f, start_line=-3)
        self.assertEqual(p.text, "498\n499\n500\n")
        self.assertEqual((p.first_line, p.last_line), (498, 500))
        whole = fileslib.read_page(f, start_line=-10_000)
        self.assertEqual(whole.first_line, 1)

    def test_start_past_the_end_is_refused(self) -> None:
        f = self.write("a.txt", "one\ntwo\n")
        with self.assertRaisesRegex(fileslib.FileToolError, "has 2 lines"):
            fileslib.read_page(f, start_line=3)

    def test_a_changed_file_refuses_the_old_cursor(self) -> None:
        f = self.write("a.txt", "a\n" * 500)
        first = fileslib.read_page(f, max_chars=200)
        f.write_text("b\n" + "a\n" * 500, encoding="utf-8")
        with self.assertRaisesRegex(fileslib.FileToolError, "changed since that cursor"):
            fileslib.read_page(f, cursor=first.next_cursor)

    def test_a_cursor_for_another_file_is_refused(self) -> None:
        a = self.write("a.txt", "a\n" * 500)
        b = self.write("b.txt", "a\n" * 500)
        cur = fileslib.read_page(a, max_chars=200).next_cursor
        with self.assertRaisesRegex(fileslib.FileToolError, "another file"):
            fileslib.read_page(b, cursor=cur)
        with self.assertRaisesRegex(fileslib.FileToolError, "not valid"):
            fileslib.read_page(a, cursor="c1.garbage")

    def test_crlf_and_no_final_newline_survive(self) -> None:
        text = "bir\r\niki\r\nüç"
        f = self.write("crlf.txt", text)
        pages = collect(f, 200)
        self.assertEqual("".join(p.text for p in pages), text)
        self.assertEqual(fileslib.snapshot(f).lines, 3)

    def test_empty_file(self) -> None:
        f = self.write("empty.txt", "")
        p = fileslib.read_page(f)
        self.assertEqual((p.text, p.next_cursor), ("", None))
        self.assertIn("empty", fileslib.format_page(p))

    def test_invalid_utf8_later_in_the_file_names_the_offset(self) -> None:
        f = self.write("bad.txt", b"ok\n" * 5000 + b"caf\xe9\n")
        with self.assertRaisesRegex(fileslib.FileToolError, "offset 15,003"):
            collect(f, 60000)

    def test_fence_is_longer_than_any_backtick_run(self) -> None:
        self.assertEqual(fileslib.fence_for("plain"), "```")
        self.assertEqual(fileslib.fence_for("a ```` b"), "`````")

    def test_numbered_lines(self) -> None:
        out = fileslib.number_lines("a\nb\n", 9, False)
        self.assertEqual(out, "     9\ta\n    10\tb\n")
        self.assertTrue(fileslib.number_lines("rest\n", 4, True).startswith("     +\t"))


def body_of(reply: str) -> str:
    """The page text between the fences, with its final newline restored
    unless the header says the last line continues."""
    lines = reply.split("\n")
    fence = next(ln for ln in lines if re.fullmatch(r"`{3,}", ln))
    i = lines.index(fence)
    j = lines.index(fence, i + 1)
    body = "\n".join(lines[i + 1:j])
    return body if "continues on the next one" in reply else body + "\n"


class ToolPageTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_agent_reaches_beginning_middle_and_end(self) -> None:
        text = big_text()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            f = root / "big.txt"
            f.write_text(text, encoding="utf-8")
            mcp, _tree = build_mcp(root)
            got = []
            async with Client(mcp) as client:
                args = {"path": str(f), "max_chars": 60000}
                for _ in range(100):
                    reply = (await client.call_tool("fs_read", args)).content[0].text
                    got.append(body_of(reply))
                    m = re.search(r'cursor="([^"]+)"', reply)
                    if not m:
                        self.assertIn("End of file.", reply)
                        break
                    args = {"path": str(f), "cursor": m.group(1), "max_chars": 60000}
                tail = (await client.call_tool(
                    "fs_read", {"path": str(f), "start_line": -1})).content[0].text
                numbered = (await client.call_tool(
                    "fs_read", {"path": str(f), "start_line": 701, "max_lines": 1,
                                "line_numbers": True})).content[0].text
        self.assertEqual("".join(got), text)
        self.assertGreater(len(got), 2)
        self.assertIn("END-MARKER", tail)
        self.assertIn("Lines 1,401–1,401 of 1,401", tail)
        self.assertIn("   701\tMIDDLE-MARKER", numbered)

    async def test_several_paths_share_the_budget(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            a, b = root / "a.txt", root / "b.txt"
            a.write_text("A\n" * 2000, encoding="utf-8")
            b.write_text("B\n", encoding="utf-8")
            mcp, _tree = build_mcp(root)
            async with Client(mcp) as client:
                out = (await client.call_tool(
                    "fs_read", {"paths": [str(a), str(b)], "max_chars": 1000})).content[0].text
                both_cursor = (await client.call_tool(
                    "fs_read", {"paths": [str(a), str(b)], "cursor": "c1.x"})).content[0].text
        self.assertIn("2 files, up to 500 characters each", out)
        self.assertIn("`" + str(a) + "`", out)
        self.assertIn("`" + str(b) + "`", out)
        self.assertEqual(out.count("End of file."), 1)
        self.assertIn("belongs to one file", both_cursor)

    async def test_missing_path_is_explained(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            mcp, _tree = build_mcp(Path(raw))
            async with Client(mcp) as client:
                out = (await client.call_tool("fs_read", {})).content[0].text
        self.assertIn("Give path", out)


if __name__ == "__main__":
    unittest.main()
