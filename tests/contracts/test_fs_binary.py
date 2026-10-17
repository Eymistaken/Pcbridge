#!/usr/bin/env python3
"""Binary transfer: base64 chunks both ways, checked by SHA-256, and images
returned as pictures.

The ROADMAP's "Done when": a client downloads and uploads a multi-megabyte
PDF and image byte for byte, with matching hashes. Runs through a real
FastMCP client, the way an agent would: follow the cursors, join the
decoded chunks; send chunks with offsets and the hash at the end.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import random
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

try:
    from PIL import Image
except ImportError:  # the desktop extra is not installed
    Image = None


def fake_pdf(size: int, seed: int = 7) -> bytes:
    rnd = random.Random(seed)
    body = bytes(rnd.getrandbits(8) for _ in range(size - 20))
    return b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + body[: size - 26] + b"\n%%EOF\n"


def noisy_png(w: int, h: int) -> bytes:
    rnd = random.Random(3)
    im = Image.frombytes("RGB", (w, h), bytes(rnd.getrandbits(8) for _ in range(w * h * 3)))
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def b64_body(reply: str) -> bytes:
    m = re.search(r"```base64\n(.*?)\n```", reply, re.S)
    return base64.b64decode(m.group(1)) if m else b""


class BinaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        mcp, _tree = build_mcp(self.root)
        self.client = Client(mcp)
        await self.client.__aenter__()

    async def asyncTearDown(self) -> None:
        await self.client.__aexit__(None, None, None)
        self.tmp.cleanup()

    async def call(self, name: str, **args):
        return (await self.client.call_tool(name, args)).content

    async def text(self, name: str, **args) -> str:
        return (await self.call(name, **args))[0].text

    async def download(self, f: Path, max_chars: int = 20000) -> tuple[bytes, str, int]:
        got, args, calls = [], {"path": str(f), "encoding": "base64", "max_chars": max_chars}, 0
        while True:
            reply = await self.text("fs_read", **args)
            calls += 1
            got.append(b64_body(reply))
            self.assertIn(f"chunk sha256 {sha(got[-1])}", reply)
            digest = re.search(r"sha256 ([0-9a-f]{64})", reply).group(1)
            m = re.search(r'cursor="([^"]+)"', reply)
            if not m:
                self.assertIn("End of file.", reply)
                return b"".join(got), digest, calls
            args = {"path": str(f), "encoding": "base64", "cursor": m.group(1),
                    "max_chars": max_chars}

    async def upload(self, f: Path, data: bytes, chunk: int = 45_000) -> str:
        reply = ""
        before = f.read_bytes() if f.exists() else None
        for start in range(0, len(data), chunk):
            last = start + chunk >= len(data)
            part = data[start:start + chunk]
            args = {"path": str(f), "content_base64": base64.b64encode(part).decode(),
                    "offset": start, "chunk_sha256": sha(part)}
            if last:
                args["final_sha256"] = sha(data)
            reply = await self.text("fs_write", **args)
            if not last:
                self.assertIn(f"offset={start + len(data[start:start + chunk])}", reply)
                now = f.read_bytes() if f.exists() else None
                self.assertEqual(now, before, "the target changed before the last chunk")
        return reply

    async def test_multi_megabyte_pdf_round_trips_byte_for_byte(self) -> None:
        pdf = fake_pdf(3_000_000)
        src = self.root / "manual.pdf"
        src.write_bytes(pdf)
        auto = await self.text("fs_read", path=str(src))
        self.assertIn("PDF document", auto)
        self.assertIn('encoding="base64"', auto)
        data, digest, calls = await self.download(src)
        self.assertEqual(data, pdf)
        self.assertEqual(digest, sha(pdf))
        self.assertEqual(calls, -(-len(pdf) // fileslib.chunk_bytes(fileslib.B64_PAGE_MAX)))
        dest = self.root / "copy" / "manual.pdf"
        reply = await self.upload(dest, data)
        self.assertIn("sha256 checked", reply)
        self.assertEqual(dest.read_bytes(), pdf)
        self.assertEqual([p.name for p in dest.parent.iterdir()], ["manual.pdf"])

    @unittest.skipIf(Image is None, "Pillow is not installed")
    async def test_multi_megabyte_image_round_trips_and_shows_scaled(self) -> None:
        png = noisy_png(2400, 1600)
        self.assertGreater(len(png), 2_000_000)
        src = self.root / "photo.png"
        src.write_bytes(png)
        blocks = await self.call("fs_read", path=str(src))
        self.assertEqual(blocks[1].type, "image")
        self.assertIn("2400x1600, shown scaled to 1568x1045", blocks[0].text)
        shown = Image.open(io.BytesIO(base64.b64decode(blocks[1].data)))
        self.assertLessEqual(max(shown.size), fileslib.IMAGE_LONG_EDGE)
        self.assertEqual(src.read_bytes(), png)
        data, digest, _calls = await self.download(src)
        self.assertEqual((data, digest), (png, sha(png)))
        dest = self.root / "photo-copy.png"
        await self.upload(dest, data, chunk=300_000)
        self.assertEqual(sha(dest.read_bytes()), sha(png))

    @unittest.skipIf(Image is None, "Pillow is not installed")
    async def test_small_image_is_sent_as_it_is(self) -> None:
        im = Image.new("RGBA", (40, 30), (255, 0, 0, 128))
        out = io.BytesIO()
        im.save(out, "PNG")
        f = self.root / "icon.png"
        f.write_bytes(out.getvalue())
        blocks = await self.call("fs_read", path=str(f))
        self.assertEqual(blocks[1].mimeType, "image/png")
        self.assertEqual(base64.b64decode(blocks[1].data), out.getvalue())
        self.assertIn("40x30, shown as it is", blocks[0].text)

    async def test_damaged_image_is_explained(self) -> None:
        f = self.root / "broken.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"garbage" * 10)
        out = await self.text("fs_read", path=str(f))
        self.assertTrue(out.startswith("Not read:"), out)
        self.assertIn('encoding="base64"', out)

    async def test_non_utf8_text_is_readable_as_bytes(self) -> None:
        raw = "çay ve şeker\n".encode("iso-8859-9")
        f = self.root / "latin5.txt"
        f.write_bytes(raw)
        self.assertIn("not valid UTF-8", await self.text("fs_read", path=str(f)))
        data, digest, calls = await self.download(f)
        self.assertEqual((data, digest, calls), (raw, sha(raw), 1))

    async def test_a_file_that_changes_mid_download_refuses_the_cursor(self) -> None:
        f = self.root / "grow.bin"
        f.write_bytes(fake_pdf(10_000))
        first = await self.text("fs_read", path=str(f), encoding="base64", max_chars=400)
        self.assertIn("Bytes 0–299 of", first)
        cur = re.search(r'cursor="([^"]+)"', first).group(1)
        f.write_bytes(fake_pdf(10_000, seed=8))
        again = await self.text("fs_read", path=str(f), encoding="base64", cursor=cur)
        self.assertIn("changed since that cursor", again)

    async def test_out_of_order_chunks_and_a_wrong_hash_change_nothing(self) -> None:
        f = self.root / "target.bin"
        f.write_bytes(b"original")
        b64 = base64.b64encode(b"abc").decode()
        abc = sha(b"abc")
        skip = await self.text("fs_write", path=str(f), content_base64=b64, offset=3,
                               chunk_sha256=abc)
        self.assertIn("must have offset=0", skip)
        await self.text("fs_write", path=str(f), content_base64=b64, offset=0, chunk_sha256=abc)
        wrong = await self.text("fs_write", path=str(f), content_base64=b64, offset=3,
                                chunk_sha256=abc, final_sha256="0" * 64)
        self.assertIn("upload was discarded", wrong)
        self.assertEqual(f.read_bytes(), b"original")
        self.assertFalse(fileslib.staging_path(f).exists())
        short = await self.text("fs_write", path=str(f), content_base64=b64, offset=0,
                                chunk_sha256=abc, final_sha256="abc")
        self.assertIn("64 hex digits", short)

    async def test_a_miscopied_or_cut_chunk_is_refused_before_staging(self) -> None:
        # What claude.ai did on 2026-10-17: one base64 character changed in the
        # fifth chunk, then a call cut off mid-chunk that still decoded.
        data = fake_pdf(45_000)
        f = self.root / "up.pdf"
        first, second = data[:15_000], data[15_000:30_000]
        ok = await self.text("fs_write", path=str(f), offset=0, chunk_sha256=sha(first),
                             content_base64=base64.b64encode(first).decode())
        self.assertIn("chunk sha256 checked", ok)
        text = base64.b64encode(second).decode()
        slip = text[:12_422] + ("7" if text[12_422] != "7" else "3") + text[12_423:]
        bad = await self.text("fs_write", path=str(f), offset=15_000, content_base64=slip,
                              chunk_sha256=sha(second))
        self.assertIn("changed or cut short", bad)
        self.assertIn("offset=15000", bad)
        cut = await self.text("fs_write", path=str(f), offset=15_000,
                              content_base64=text[:5_264], chunk_sha256=sha(second))
        self.assertIn("arrived as 3,948 bytes", cut)
        self.assertEqual(fileslib.staging_path(f).stat().st_size, 15_000)
        none = await self.text("fs_write", path=str(f), offset=15_000, content_base64=text)
        self.assertIn("needs chunk_sha256", none)
        self.assertEqual(fileslib.staging_path(f).stat().st_size, 15_000)
        self.assertFalse(f.exists())

    async def test_base64_chunks_are_capped_at_20000_characters(self) -> None:
        f = self.root / "big.bin"
        f.write_bytes(fake_pdf(100_000))
        out = await self.text("fs_read", path=str(f), encoding="base64", max_chars=60000)
        self.assertIn(f"Bytes 0–14,999 of {f.stat().st_size:,} as base64 (20,000 characters)", out)
        default = await self.text("fs_read", path=str(f), encoding="base64")
        self.assertIn("(20,000 characters)", default)
        text = await self.text("fs_read", path=str(f))
        self.assertIn("call(s) of 20,000 characters", text)

    async def test_single_base64_write_checks_hash_and_conflicts(self) -> None:
        f = self.root / "one.bin"
        data = bytes(range(256)) * 10
        bad = await self.text("fs_write", path=str(f), content_base64=base64.b64encode(
            data).decode(), final_sha256="1" * 64)
        self.assertIn("was not changed", bad)
        self.assertFalse(f.exists())
        ok = await self.text("fs_write", path=str(f), content_base64=base64.b64encode(
            data).decode(), final_sha256=sha(data))
        self.assertIn(sha(data), ok)
        self.assertEqual(f.read_bytes(), data)
        stale = await self.text("fs_write", path=str(f), content_base64="AAAA",
                                expected_sha256="2" * 64)
        self.assertIn("Conflict", stale)
        self.assertEqual(f.read_bytes(), data)
        exists = await self.text("fs_write", path=str(f), content_base64="AAAA",
                                 create_only=True)
        self.assertIn("already exists", exists)
        both = await self.text("fs_write", path=str(f), content="x", content_base64="AAAA")
        self.assertIn("not both", both)
        bad64 = await self.text("fs_write", path=str(f), content_base64="!!notbase64")
        self.assertIn("not valid base64", bad64)

    async def test_chunked_upload_keeps_the_mode_of_the_file_it_replaces(self) -> None:
        f = self.root / "tool.bin"
        f.write_bytes(b"old")
        os.chmod(f, 0o755)
        await self.upload(f, b"\x7fELF" + b"\x00" * 100_000, chunk=40_000)
        self.assertEqual(f.stat().st_mode & 0o777, 0o755)


if __name__ == "__main__":
    unittest.main()
