#!/usr/bin/env python3
"""`fs_read` returns UTF-8 text and refuses binary files plainly.

Before, it decoded with `errors="replace"`: a PDF or an image came back as
text full of U+FFFD with no error. Runs through a real FastMCP client.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from fastmcp import Client

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import tools as toolslib  # noqa: E402
from tests.contracts.test_mcp_errors import build_mcp  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"


class DecodeTextFileTests(unittest.TestCase):
    def test_utf8_text_is_returned_unchanged(self) -> None:
        text = "satır 1 — çğıöşü\nline 2 ✓\n"
        self.assertEqual(toolslib._decode_text_file(text.encode()), (text, None))

    def test_empty_file_is_text(self) -> None:
        self.assertEqual(toolslib._decode_text_file(b""), ("", None))

    def test_known_formats_are_named(self) -> None:
        for raw, label in ((PNG, "PNG image"), (PDF, "PDF document"),
                           (b"\x7fELF\x02\x01\x01", "ELF executable"),
                           (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "WebP image")):
            data, reason = toolslib._decode_text_file(raw)
            self.assertIsNone(data)
            self.assertIn(label, reason)

    def test_unknown_binary_with_nul_is_refused(self) -> None:
        data, reason = toolslib._decode_text_file(b"abc\x00def")
        self.assertIsNone(data)
        self.assertIn("NUL", reason)

    def test_non_utf8_text_is_refused_with_offset(self) -> None:
        data, reason = toolslib._decode_text_file("ok çay".encode("latin-1"))
        self.assertIsNone(data)
        self.assertIn("offset 3", reason)


class FsReadToolTests(unittest.IsolatedAsyncioTestCase):
    async def _read(self, root: Path, path: Path) -> str:
        mcp, _tree = build_mcp(root)
        async with Client(mcp) as client:
            result = await client.call_tool("fs_read", {"path": str(path)})
        return result.content[0].text

    async def test_text_file_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            f = root / "note.txt"
            f.write_text("merhaba dünya\n", encoding="utf-8")
            out = await self._read(root, f)
        self.assertIn("merhaba dünya", out)
        self.assertIn("bytes)", out)
        self.assertNotIn("�", out)

    async def test_long_file_keeps_the_tail_with_an_english_marker(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            f = root / "long.txt"
            f.write_text("BEGIN\n" + "x" * 1000 + "\nEND\n", encoding="utf-8")
            mcp, _tree = build_mcp(root)
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "fs_read", {"path": str(f), "max_chars": 200})
            out = result.content[0].text
        self.assertIn("…(truncated)…", out)
        self.assertIn("END", out)
        self.assertNotIn("BEGIN", out)

    async def test_binary_files_are_refused_without_replacement_chars(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for name, data, label in (("a.png", PNG, "PNG image"),
                                      ("a.pdf", PDF, "PDF document"),
                                      ("a.bin", bytes(range(256)), "NUL bytes"),
                                      ("a.txt", b"caf\xe9\n", "not valid UTF-8")):
                f = root / name
                f.write_bytes(data)
                out = await self._read(root, f)
                self.assertTrue(out.startswith("Not read:"), out)
                self.assertIn(label, out)
                self.assertIn(f"{len(data):,} bytes", out)
                self.assertNotIn("�", out)


if __name__ == "__main__":
    unittest.main()
