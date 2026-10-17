"""Download and upload links against a real daemon over HTTP: the path a
phone takes. The link is made through OAuth + MCP, then opened with no
credentials at all, as a phone browser would.

A throwaway config, state directory, socket and HTTP port, as in
`test_remote_access.py`, whose harness this borrows; it never touches the
user's daemon, config or files.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import socket
import sys
import time
import unittest
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.integration import test_remote_access as remote  # noqa: E402

_HARNESS = ("setUp", "_write_config", "_start_daemon", "_stop_daemon", "_rpc", "_session",
            "_oauth", "_http_tools", "_socket_tools")
MB = 1 << 20


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FileLinksOverHttp(unittest.TestCase):
    locals().update({name: getattr(remote.RemoteAccessTests, name) for name in _HARNESS})

    def _remote(self, tools: str = 'profile = "full"') -> None:
        self._write_config(tools)
        self._start_daemon()
        self.token = self._oauth("file-links-test")["access_token"]
        self.sid = self._session(self.token)
        self.work = self.tmp / "work"

    def call(self, name: str, args: dict) -> dict:
        r, data = self._rpc(self.token, "tools/call", {"name": name, "arguments": args},
                            sid=self.sid)
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertIn("result", data, data)
        return data["result"]

    def text(self, name: str, args: dict) -> str:
        return self.call(name, args)["content"][0]["text"]

    def link(self, name: str, args: dict) -> tuple[str, str]:
        """(path of the link on this daemon, the token) from a link tool's reply."""
        reply = self.text(name, args)
        m = re.search(r"Link: (\S+)", reply)
        self.assertIsNotNone(m, reply)
        path = urlparse(m.group(1)).path
        return path, path.split("/")[2]

    def get(self, path: str, **kw):
        return self.http.get(f"{self.base}{path}", **kw)

    def put(self, path: str, data, **kw):
        return self.http.put(f"{self.base}{path}", content=data, **kw)

    def file_path(self, page_path: str) -> str:
        page = self.get(page_path)
        self.assertEqual(page.status_code, 200, page.text[:300])
        href = re.search(r'href="(/dl/[^"]+)"', page.text).group(1)
        return href.replace("&amp;", "&")

    def assertNothing(self, r) -> None:
        """A used, expired or unknown link: the same 404, nothing else."""
        unknown = self.get(f"/dl/{secrets.token_urlsafe(32)}")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.content, b"not found")
        self.assertEqual(r.content, unknown.content)
        strip = {"date", "content-length"}
        self.assertEqual({k: v for k, v in r.headers.items() if k not in strip},
                         {k: v for k, v in unknown.headers.items() if k not in strip})

    def audit(self) -> list[dict]:
        text = (self.tmp / "state" / "audit.log").read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if '"fs_link' in line]

    def leftovers(self, directory: Path) -> list[str]:
        return [p.name for p in directory.iterdir() if p.name.endswith(".pcbridge-link")]

    # ------------------------------------------------------------- downloads

    def test_a_50_mb_download_arrives_whole_once_and_never_again(self) -> None:
        self._remote()
        data = os.urandom(50 * MB)
        src = self.work / "video çekim.mp4"
        src.write_bytes(data)
        page, token = self.link("fs_download_link", {"path": str(src)})
        # The page and a HEAD request do not spend the link (previews, refreshes).
        first = self.get(page)
        self.assertEqual(first.status_code, 200)
        self.assertIn(_sha(data), first.text)
        self.assertIn("video çekim.mp4", first.text)
        self.assertIn("default-src 'none'", first.headers["content-security-policy"])
        href = self.file_path(page)
        head = self.http.head(f"{self.base}{href}")
        self.assertEqual(head.status_code, 200)
        self.assertEqual(int(head.headers["content-length"]), len(data))

        r = self.get(href)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(_sha(r.content), _sha(data))
        self.assertEqual(r.headers["content-type"], "application/octet-stream")
        self.assertTrue(r.headers["content-disposition"].startswith("attachment;"))
        self.assertIn("UTF-8''video%20%C3%A7ekim.mp4", r.headers["content-disposition"])
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertIn("sandbox", r.headers["content-security-policy"])
        self.assertEqual(r.headers["cache-control"], "no-store, max-age=0")

        self.assertNothing(self.get(href))
        self.assertNothing(self.get(page))
        self.assertEqual(self.http.head(f"{self.base}{href}").status_code, 404)

        lines = self.audit()
        events = [(e["event"], e.get("direction")) for e in lines]
        self.assertIn(("fs_link_created", "download"), events)
        used = next(e for e in lines if e["event"] == "fs_link_used")
        self.assertEqual((used["bytes"], used["sha256"]), (len(data), _sha(data)))
        self.assertEqual(sum(e["event"] == "fs_link_refused" for e in lines), 1)
        self.assertNotIn(token, (self.tmp / "state" / "audit.log").read_text())
        log = (self.tmp / "daemon.log").read_text(errors="replace")
        self.assertNotIn(token, log)
        self.assertIn('"GET /dl/<link>/video%20%C3%A7ekim.mp4 HTTP/1.1" 200', log)

    def test_a_cut_off_download_leaves_the_link_usable(self) -> None:
        self._remote()
        data = os.urandom(20 * MB)
        src = self.work / "big.bin"
        src.write_bytes(data)
        page, _ = self.link("fs_download_link", {"path": str(src)})
        href = self.file_path(page)
        with socket.create_connection(("127.0.0.1", self.port)) as s:
            s.sendall(f"GET {href} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
            got = b""
            while len(got) < 2 * MB:
                got += s.recv(65536)
        r = None
        for _ in range(50):  # the daemon notices the closed socket shortly after
            r = self.get(href)
            if r.status_code == 200:
                break
            time.sleep(0.1)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(_sha(r.content), _sha(data))
        self.assertNothing(self.get(href))
        reasons = [e.get("reason") for e in self.audit() if e["event"] == "fs_link_failed"]
        self.assertIn("disconnected", reasons)

    def test_a_file_changed_after_the_link_was_made_is_not_sent(self) -> None:
        self._remote()
        src = self.work / "notes.txt"
        src.write_text("first")
        page, _ = self.link("fs_download_link", {"path": str(src)})
        href = self.file_path(page)
        src.write_text("second, longer")
        r = self.get(href)
        self.assertEqual(r.status_code, 409)
        self.assertNotIn(b"second", r.content)
        self.assertNothing(self.get(href))

    # --------------------------------------------------------------- uploads

    def test_a_50_mb_upload_lands_whole_once_and_never_again(self) -> None:
        self._remote()
        data = os.urandom(50 * MB)
        dest = self.work / "from phone.jpg"
        page, token = self.link("fs_upload_link", {"path": str(dest)})
        shown = self.get(page)
        self.assertEqual(shown.status_code, 200)
        self.assertIn("from phone.jpg", shown.text)
        self.assertIn("script-src 'sha256-", shown.headers["content-security-policy"])
        self.assertFalse(dest.exists())

        r = self.put(page, data)
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertEqual(r.json()["sha256"], _sha(data))
        self.assertEqual(r.json()["bytes"], len(data))
        self.assertEqual(_sha(dest.read_bytes()), _sha(data))
        self.assertEqual(self.leftovers(self.work), [])

        self.assertNothing(self.put(page, b"again"))
        self.assertNothing(self.get(page))
        self.assertEqual(_sha(dest.read_bytes()), _sha(data))
        used = next(e for e in self.audit() if e["event"] == "fs_link_used")
        self.assertEqual((used["direction"], used["sha256"]), ("upload", _sha(data)))
        self.assertNotIn(token, (self.tmp / "state" / "audit.log").read_text())
        self.assertNotIn(token, (self.tmp / "daemon.log").read_text(errors="replace"))

    def test_upload_limits_spend_the_link_and_leave_nothing(self) -> None:
        self._remote('profile = "full"\n\n[files]\nupload_max_mb = 1\n')
        dest = self.work / "too-big.bin"
        page, _ = self.link("fs_upload_link", {"path": str(dest)})
        r = self.put(page, os.urandom(2 * MB))
        self.assertEqual(r.status_code, 413)
        self.assertFalse(r.json()["retry"])
        self.assertNothing(self.put(page, b"small"))

        # No Content-Length (chunked): cut off while streaming.
        page, _ = self.link("fs_upload_link", {"path": str(dest)})

        def chunks():
            for _ in range(4):
                yield os.urandom(512 * 1024)

        r = self.put(page, chunks())
        self.assertEqual(r.status_code, 413)
        self.assertFalse(dest.exists())
        self.assertEqual(self.leftovers(self.work), [])
        self.assertNothing(self.put(page, b"small"))

    def test_a_wrong_file_is_refused_and_the_right_one_still_accepted(self) -> None:
        self._remote()
        right = os.urandom(300_000)
        dest = self.work / "doc.pdf"
        page, _ = self.link("fs_upload_link", {"path": str(dest),
                                               "expected_sha256": _sha(right)})
        r = self.put(page, b"some other file")
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.json()["retry"])
        self.assertFalse(dest.exists())
        r = self.put(page, right)
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertEqual(dest.read_bytes(), right)

    def test_an_existing_file_is_replaced_only_with_overwrite(self) -> None:
        self._remote()
        dest = self.work / "keep.txt"
        dest.write_text("original")
        os.chmod(dest, 0o640)
        refused = self.text("fs_upload_link", {"path": str(dest)})
        self.assertIn("already exists", refused)
        page, _ = self.link("fs_upload_link", {"path": str(dest), "overwrite": True})
        self.assertEqual(self.put(page, b"replacement").status_code, 200)
        self.assertEqual(dest.read_bytes(), b"replacement")
        self.assertEqual(dest.stat().st_mode & 0o777, 0o640)

        # A file that appears after the link was made is not overwritten.
        late = self.work / "late.txt"
        page, _ = self.link("fs_upload_link", {"path": str(late)})
        late.write_text("someone else's")
        r = self.put(page, b"upload")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(late.read_text(), "someone else's")
        self.assertEqual(self.leftovers(self.work), [])

    def test_a_cut_off_upload_leaves_no_file_and_the_link_usable(self) -> None:
        self._remote()
        dest = self.work / "photo.heic"
        page, _ = self.link("fs_upload_link", {"path": str(dest)})
        with socket.create_connection(("127.0.0.1", self.port)) as s:
            s.sendall((f"PUT {page} HTTP/1.1\r\nHost: localhost\r\n"
                       f"Content-Length: {4 * MB}\r\n\r\n").encode())
            s.sendall(os.urandom(MB))
        data = os.urandom(3 * MB)
        r = None
        for _ in range(50):
            r = self.put(page, data)
            if r.status_code == 200:
                break
            time.sleep(0.1)
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertEqual(_sha(dest.read_bytes()), _sha(data))
        self.assertEqual(self.leftovers(self.work), [])
        reasons = [e.get("reason") for e in self.audit() if e["event"] == "fs_link_failed"]
        self.assertIn("disconnected", reasons)

    # ------------------------------------------------- expiry and profiles

    def test_an_expired_link_returns_nothing(self) -> None:
        self._remote('profile = "full"\n\n[files]\nlink_ttl_seconds = 10\n')
        src = self.work / "a.txt"
        src.write_text("x")
        down, _ = self.link("fs_download_link", {"path": str(src)})
        up, _ = self.link("fs_upload_link", {"path": str(self.work / "b.txt")})
        self.assertIn("10 seconds", self.text("fs_download_link", {"path": str(src)}))
        time.sleep(10.5)
        self.assertNothing(self.get(down))
        self.assertNothing(self.get(up))
        self.assertNothing(self.put(up, b"late"))
        self.assertFalse((self.work / "b.txt").exists())
        reasons = {e.get("reason") for e in self.audit() if e["event"] == "fs_link_refused"}
        self.assertEqual(reasons, {"expired"})

    def test_readonly_remote_profile_gets_downloads_but_never_uploads(self) -> None:
        self._remote('profile = "full"\nremote_profile = "readonly"')
        tools = self._http_tools(self.token)
        self.assertIn("fs_download_link", tools)
        self.assertNotIn("fs_upload_link", tools)
        r, data = self._rpc(self.token, "tools/call", {
            "name": "fs_upload_link", "arguments": {"path": str(self.work / "x")}}, sid=self.sid)
        result = data.get("result") or {}
        self.assertTrue(result.get("isError") or "error" in data, data)
        # A local session with profile "full" cannot make one either: the link
        # would be used over HTTP, where remote_profile applies.
        client, local = self._socket_tools()
        self.assertIn("fs_upload_link", local)
        reply = client.call("tools/call", {"name": "fs_upload_link",
                                           "arguments": {"path": str(self.work / "x")}})
        self.assertIn('remote_profile = "readonly"', reply["result"]["content"][0]["text"])
        self.assertFalse((self.work / "x").exists())
        src = self.work / "r.txt"
        src.write_text("readable")
        page, _ = self.link("fs_download_link", {"path": str(src)})
        self.assertEqual(self.get(self.file_path(page)).content, b"readable")


if __name__ == "__main__":
    unittest.main()
