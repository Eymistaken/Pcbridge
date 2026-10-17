"""The file tools' "Done when" checks against a real daemon over HTTP, the
path a remote client (claude.ai, ChatGPT, a phone) takes: OAuth, then MCP.

A throwaway config, state directory, socket and HTTP port, as in
`test_remote_access.py`, whose harness this borrows; it never touches the
user's daemon, config or files.
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.contracts.test_fs_binary import Image, noisy_png  # noqa: E402
from tests.contracts.test_fs_edit import MULTILINE_NEW, MULTILINE_OLD  # noqa: E402
from tests.contracts.test_fs_pages import big_text, body_of  # noqa: E402
from tests.integration import test_remote_access as remote  # noqa: E402

_HARNESS = ("setUp", "_write_config", "_start_daemon", "_stop_daemon", "_rpc", "_session",
            "_oauth")


class FileToolsOverHttp(unittest.TestCase):
    locals().update({name: getattr(remote.RemoteAccessTests, name) for name in _HARNESS})

    def _remote(self) -> None:
        self._write_config('profile = "full"')
        self._start_daemon()
        self.token = self._oauth("file-tools-test")["access_token"]
        self.sid = self._session(self.token)

    def call(self, name: str, args: dict) -> dict:
        r, data = self._rpc(self.token, "tools/call", {"name": name, "arguments": args},
                            sid=self.sid)
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertIn("result", data, data)
        return data["result"]

    def text(self, name: str, args: dict) -> str:
        return self.call(name, args)["content"][0]["text"]

    def test_paged_read_reaches_every_part_of_a_large_file(self) -> None:
        self._remote()
        text = big_text()
        f = self.tmp / "work" / "big.txt"
        f.write_text(text, encoding="utf-8")
        got, args, calls = [], {"path": str(f), "max_chars": 60000}, 0
        while True:
            reply = self.text("fs_read", args)
            calls += 1
            got.append(body_of(reply))
            m = re.search(r'cursor="([^"]+)"', reply)
            if not m:
                break
            args = {"path": str(f), "cursor": m.group(1), "max_chars": 60000}
        self.assertEqual("".join(got), text)
        self.assertGreater(len(text), 60000)
        self.assertGreaterEqual(calls, 3)
        middle = self.text("fs_read", {"path": str(f), "start_line": 701, "max_lines": 1})
        self.assertIn("MIDDLE-MARKER", middle)
        end = self.text("fs_read", {"path": str(f), "start_line": -1})
        self.assertIn("END-MARKER", end)
        self.assertIn("End of file.", end)

    def test_targeted_edit_changes_only_its_region_and_detects_conflicts(self) -> None:
        self._remote()
        lines = [f"satır {i:06d} {'x' * 60}\n" for i in range(80_000)]
        lines[40_000] = MULTILINE_OLD
        before = "".join(lines).encode("utf-8")
        f = self.tmp / "work" / "big.py"
        f.write_bytes(before)
        read = self.text("fs_read", {"path": str(f), "start_line": 40_001, "max_lines": 2})
        digest = re.search(r"sha256 ([0-9a-f]{64})", read).group(1)
        self.assertEqual(digest, hashlib.sha256(before).hexdigest())
        # The multiline UTF-8 text travels as a JSON string: no shell, no escaping.
        out = self.text("fs_edit", {"path": str(f), "old_text": MULTILINE_OLD,
                                    "new_text": MULTILINE_NEW, "expected_sha256": digest})
        self.assertTrue(out.startswith("Edited"), out)
        after = f.read_bytes()
        i = before.index(MULTILINE_OLD.encode())
        self.assertEqual(after[:i], before[:i])
        self.assertEqual(after[i:i + len(MULTILINE_NEW.encode())], MULTILINE_NEW.encode())
        self.assertEqual(after[i + len(MULTILINE_NEW.encode()):],
                         before[i + len(MULTILINE_OLD.encode()):])
        self.assertLess(len(out), 6000)
        # Someone else changes the file; the stale hash is a conflict, not an overwrite.
        f.write_bytes(after + b"# another writer\n")
        stale = self.text("fs_edit", {"path": str(f), "old_text": "satır 000001",
                                      "new_text": "LOST", "expected_sha256": digest})
        self.assertIn("Conflict", stale)
        stale_write = self.text("fs_write", {"path": str(f), "content": "LOST",
                                             "expected_sha256": digest})
        self.assertIn("Conflict", stale_write)
        self.assertTrue(f.read_bytes().endswith(b"# another writer\n"))
        self.assertNotIn(b"LOST", f.read_bytes())

    def download(self, f: Path) -> tuple[bytes, str, int]:
        got, args, calls = [], {"path": str(f), "encoding": "base64", "max_chars": 60000}, 0
        while True:
            reply = self.text("fs_read", args)
            calls += 1
            m = re.search(r"```base64\n(.*?)\n```", reply, re.S)
            got.append(base64.b64decode(m.group(1)))
            digest = re.search(r"sha256 ([0-9a-f]{64})", reply).group(1)
            cur = re.search(r'cursor="([^"]+)"', reply)
            if not cur:
                return b"".join(got), digest, calls
            args = {**args, "cursor": cur.group(1)}

    def upload(self, f: Path, data: bytes, chunk: int = 45_000) -> tuple[str, int]:
        reply, calls = "", 0
        for start in range(0, len(data), chunk):
            args = {"path": str(f), "offset": start,
                    "content_base64": base64.b64encode(data[start:start + chunk]).decode()}
            if start + chunk >= len(data):
                args["final_sha256"] = hashlib.sha256(data).hexdigest()
            reply = self.text("fs_write", args)
            calls += 1
        return reply, calls

    @unittest.skipIf(Image is None, "Pillow is not installed")
    def test_multi_megabyte_pdf_and_image_round_trip_byte_for_byte(self) -> None:
        self._remote()
        work = self.tmp / "work"
        # A real PDF (Pillow's writer) around a noisy picture, so it is several MB.
        out = io.BytesIO()
        Image.open(io.BytesIO(noisy_png(1600, 1200))).convert("RGB").save(
            out, "PDF", quality=95)
        samples = {"doc.pdf": out.getvalue()}
        wallpaper = Path("/usr/share/backgrounds/Zorin.jpg")
        samples["photo.jpg"] = wallpaper.read_bytes() if wallpaper.is_file() else \
            noisy_png(1800, 1200)
        for name, data in samples.items():
            self.assertGreater(len(data), 1_000_000, name)
            src = work / name
            src.write_bytes(data)
            got, digest, down = self.download(src)
            self.assertEqual(got, data, name)
            self.assertEqual(digest, hashlib.sha256(data).hexdigest())
            dest = work / "uploaded" / name
            reply, up = self.upload(dest, got)
            self.assertIn("sha256 checked", reply)
            self.assertEqual(hashlib.sha256(dest.read_bytes()).hexdigest(), digest)
            self.assertEqual(down, up)
            self.assertGreaterEqual(down, len(data) // 45_000)
        pic = self.call("fs_read", {"path": str(work / "photo.jpg")})["content"]
        self.assertEqual(pic[1]["type"], "image")
        self.assertLessEqual(len(base64.b64decode(pic[1]["data"])), 3_500_000)
        self.assertIn("shown scaled to", pic[0]["text"])


if __name__ == "__main__":
    unittest.main()
