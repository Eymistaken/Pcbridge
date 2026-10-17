"""The file links' rules without a server: single use, expiry, limits, the
`[files]` settings and the access-log filter. The HTTP side is in
tests/integration/test_file_links_http.py.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import config as configlib  # noqa: E402
from pcbridge import filelinks as fl  # noqa: E402


def _cfg(**over) -> SimpleNamespace:
    base = dict(public_url="https://host.example.ts.net", link_ttl_seconds=300,
                upload_max_mb=512, download_max_mb=2048)
    return SimpleNamespace(**{**base, **over})


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="pcbr-links-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.events: list[tuple[str, dict]] = []
        self.store = fl.LinkStore(_cfg(), audit=lambda e, **f: self.events.append((e, f)))
        self.file = self.dir / "a.pdf"
        self.file.write_bytes(b"%PDF-1.7 hello")

    def test_a_token_is_256_bits_bound_to_one_path_and_direction(self) -> None:
        link, token = self.store.create_download(self.file)
        self.assertEqual(len(token), 43)
        self.assertEqual(link.path, self.file)
        self.assertEqual(self.store.url(link, token), f"https://host.example.ts.net/dl/{token}")
        self.assertNotIn(token, repr(self.store._links))  # kept by its hash only
        self.assertIsNone(self.store.lookup(token, fl.UPLOAD))
        self.assertIsNone(self.store.lookup(token[:-1] + ("A" if token[-1] != "A" else "B"),
                                            fl.DOWNLOAD))

    def test_one_transfer_at_a_time_and_only_a_complete_one_spends_it(self) -> None:
        link, token = self.store.create_download(self.file)
        self.assertIs(self.store.claim(token, fl.DOWNLOAD), link)
        self.assertIsNone(self.store.claim(token, fl.DOWNLOAD))  # busy
        self.store.release(link)  # broke off
        self.assertIs(self.store.lookup(token, fl.DOWNLOAD), link)
        self.assertIs(self.store.claim(token, fl.DOWNLOAD), link)
        self.store.spend(link)
        self.assertIsNone(self.store.lookup(token, fl.DOWNLOAD))
        self.assertIsNone(self.store.claim(token, fl.DOWNLOAD))
        refused = [f for e, f in self.events if e == "fs_link_refused"]
        self.assertEqual(len(refused), 1, "a replay is audited once, not once per request")
        self.assertEqual(refused[0]["reason"], "used")
        self.assertNotIn(token, str(self.events))

    def test_an_expired_link_cannot_start_a_transfer(self) -> None:
        _link, token = self.store.create_download(self.file)
        later = time.time() + 301
        with mock.patch.object(fl.time, "time", return_value=later):
            self.assertIsNone(self.store.claim(token, fl.DOWNLOAD))
        self.assertEqual(self.events[-1][1]["reason"], "expired")
        with mock.patch.object(fl.time, "time", return_value=later + fl.REMEMBER_SECONDS + 1):
            self.store.create_download(self.file)  # sweeps
        self.assertEqual(len(self.store._links), 1)

    def test_at_most_sixteen_live_links(self) -> None:
        for _ in range(fl.MAX_LIVE):
            self.store.create_download(self.file)
        with self.assertRaises(fl.LinkError):
            self.store.create_download(self.file)

    def test_download_limits_and_refusals(self) -> None:
        small = fl.LinkStore(_cfg(download_max_mb=1))
        big = self.dir / "big.bin"
        big.write_bytes(b"\0" * ((1 << 20) + 1))
        with self.assertRaisesRegex(fl.LinkError, "download_max_mb"):
            small.create_download(big)
        with self.assertRaisesRegex(fl.LinkError, "not found"):
            self.store.create_download(self.dir / "missing")
        with self.assertRaises(fl.LinkError):
            self.store.create_download(self.dir)
        os.symlink(self.file, self.dir / "link.pdf")
        with self.assertRaises(fl.LinkError):  # O_NOFOLLOW: a symlink swapped in is refused
            self.store.create_download(self.dir / "link.pdf")

    def test_upload_refusals(self) -> None:
        with self.assertRaisesRegex(fl.LinkError, "already exists"):
            self.store.create_upload(self.file)
        self.store.create_upload(self.file, overwrite=True)
        with self.assertRaisesRegex(fl.LinkError, "does not exist"):
            self.store.create_upload(self.dir / "no" / "such" / "x.bin")
        with self.assertRaisesRegex(fl.LinkError, "directory"):
            self.store.create_upload(self.dir)
        with self.assertRaisesRegex(fl.LinkError, "64 hex"):
            self.store.create_upload(self.dir / "n.bin", expected="abc")
        link, _ = self.store.create_upload(self.dir / "n.bin", expected="A" * 64)
        self.assertEqual(link.expected, "a" * 64)
        self.assertEqual(link.max_bytes, 512 << 20)

    def test_old_upload_leftovers_of_the_same_target_are_swept(self) -> None:
        old = self.dir / f".n.bin.abc{fl.TEMP_SUFFIX}"
        new = self.dir / f".n.bin.def{fl.TEMP_SUFFIX}"
        other = self.dir / f".other.bin.abc{fl.TEMP_SUFFIX}"
        for f in (old, new, other):
            f.write_bytes(b"x")
        past = time.time() - 2 * 86400
        os.utime(old, (past, past))
        os.utime(other, (past, past))
        self.store.create_upload(self.dir / "n.bin")
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())
        self.assertTrue(other.exists())

    def test_placing_never_clobbers_without_overwrite(self) -> None:
        tmp = self.dir / "t"
        tmp.write_bytes(b"new")
        with self.assertRaises(FileExistsError):
            fl._place(str(tmp), self.file, overwrite=False)
        self.assertEqual(self.file.read_bytes(), b"%PDF-1.7 hello")
        fl._place(str(tmp), self.dir / "fresh", overwrite=False)
        self.assertEqual((self.dir / "fresh").read_bytes(), b"new")
        self.assertFalse(tmp.exists())


class HelperTests(unittest.TestCase):
    def test_the_access_log_never_shows_a_token(self) -> None:
        f = fl.RedactLinkTokens()
        for path, want in (("/dl/abcDEF_-123", "/dl/<link>"),
                           ("/dl/abcDEF_-123/photo%20one.jpg", "/dl/<link>/photo%20one.jpg"),
                           ("/ul/abcDEF_-123", "/ul/<link>"),
                           ("/mcp", "/mcp"), ("/shot/x.png", "/shot/x.png")):
            rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0,
                                    '%s - "%s %s HTTP/%s" %d',
                                    ("1.2.3.4:5", "GET", path, "1.1", 200), None)
            self.assertTrue(f.filter(rec))
            self.assertEqual(rec.args[2], want)
        fl.install_log_filter()
        fl.install_log_filter()
        access = logging.getLogger("uvicorn.access")
        self.assertEqual(sum(isinstance(x, fl.RedactLinkTokens) for x in access.filters), 1)

    def test_the_download_name_is_safe_in_the_header(self) -> None:
        d = fl.disposition('çekim "1";.mp4')
        self.assertTrue(d.startswith('attachment; filename="_ekim _1__.mp4"'))
        self.assertIn("filename*=UTF-8''%C3%A7ekim%20%221%22%3B.mp4", d)
        d.encode("latin-1")

    def test_the_page_policy_allows_only_its_own_script_and_style(self) -> None:
        self.assertIn(fl._csp_hash(fl._UPLOAD_SCRIPT), fl._PAGE_CSP)
        self.assertIn(fl._csp_hash(fl._STYLE), fl._PAGE_CSP)
        self.assertIn("default-src 'none'", fl._PAGE_CSP)
        self.assertIn("frame-ancestors 'none'", fl._PAGE_CSP)


class SettingsTests(unittest.TestCase):
    def test_defaults_and_ranges(self) -> None:
        self.assertEqual(configlib._files({}, "x"),
                         {"link_ttl_seconds": 300, "upload_max_mb": 512, "download_max_mb": 2048})
        self.assertEqual(configlib._files({"files": {"link_ttl_seconds": 10}}, "x")
                         ["link_ttl_seconds"], 10)
        for key, bad in (("link_ttl_seconds", 9), ("link_ttl_seconds", 3601),
                         ("upload_max_mb", 0), ("download_max_mb", 16385),
                         ("upload_max_mb", "5"), ("upload_max_mb", True)):
            with self.assertRaises(SystemExit, msg=f"{key}={bad!r}"):
                configlib._files({"files": {key: bad}}, "x")


if __name__ == "__main__":
    unittest.main()
