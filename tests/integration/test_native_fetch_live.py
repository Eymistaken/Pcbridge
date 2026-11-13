#!/usr/bin/env python3
"""The release-helper download, end to end, with the real helper.

A local HTTP server plays the GitHub release: `download/<tag>/sha256sums.txt`
and `download/<tag>/pcbridge-native-<target>`, the layout
`.github/workflows/release.yml` publishes. `fetch.fetch()` then runs exactly
what `pcbridge setup` runs, against the real binary: the checksum, the real
`--build-info`, the glibc check and a real handshake (in a throwaway state
directory: no grant, no screen share, nothing requested). The helper comes
from `scripts/build-native.sh` (`pcbridge/_native/<target>/`) or
`PCBRIDGE_NATIVE_BIN`; with neither, the tests skip.
"""

from __future__ import annotations

import functools
import hashlib
import http.server
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.native import fetch  # noqa: E402
from pcbridge.native.discovery import _target_triple, discover_native_binary  # noqa: E402
from pcbridge.config import NativeSpec  # noqa: E402

TARGET = "x86_64-unknown-linux-gnu"


def real_helper() -> Path | None:
    explicit = os.environ.get("PCBRIDGE_NATIVE_BIN", "").strip()
    if explicit:
        return Path(explicit)
    packaged = ROOT / "pcbridge" / "_native" / TARGET / "pcbridge-native"
    return packaged if packaged.is_file() else None


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args) -> None:  # keep the test output clean
        pass


@unittest.skipUnless(_target_triple() == TARGET, "release builds are x86_64 only")
@unittest.skipIf(real_helper() is None, "no built native helper (scripts/build-native.sh)")
class LiveFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.site = base / "site"
        self.root = base / "pcbridge"
        self.root.mkdir()
        handler = functools.partial(_Quiet, directory=str(self.site))
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.releases = f"http://127.0.0.1:{self.server.server_port}"
        # No proxy for the local server, whatever the environment says.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({})).open

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def publish(self, tag: str, data: bytes, listed: bytes | None = None) -> None:
        folder = self.site / "download" / tag
        folder.mkdir(parents=True)
        (folder / fetch.asset_name(TARGET)).write_bytes(data)
        digest = hashlib.sha256(listed if listed is not None else data).hexdigest()
        (folder / "sha256sums.txt").write_text(f"{digest}  {fetch.asset_name(TARGET)}\n")

    def test_the_real_helper_is_fetched_checked_and_found_by_discovery(self) -> None:
        helper = real_helper()
        self.publish("v0.0.0-test", helper.read_bytes())
        got = fetch.fetch(version="0.0.0-test", target=TARGET, package_root=self.root,
                          opener=self.opener, releases=self.releases, latest=lambda: "")
        self.assertEqual(got.tag, "v0.0.0-test")
        self.assertTrue(got.build_id)
        self.assertEqual(got.path.read_bytes(), helper.read_bytes())
        self.assertEqual(
            discover_native_binary(NativeSpec(), environ={}, package_root=self.root),
            got.path.resolve())
        self.assertTrue((got.path.parent / "build-info.json").is_file())

    def test_the_latest_release_serves_a_newer_checkout(self) -> None:
        self.publish("v0.0.1-test", real_helper().read_bytes())
        got = fetch.fetch(version="0.0.2-test", target=TARGET, package_root=self.root,
                          opener=self.opener, releases=self.releases,
                          latest=lambda: "v0.0.1-test")
        self.assertEqual(got.tag, "v0.0.1-test")

    def test_a_tampered_download_never_reaches_the_package(self) -> None:
        data = real_helper().read_bytes()
        self.publish("v0.0.0-test", data[:-1] + bytes([data[-1] ^ 1]), listed=data)
        with self.assertRaises(fetch.FetchError):
            fetch.fetch(version="0.0.0-test", target=TARGET, package_root=self.root,
                        opener=self.opener, releases=self.releases, latest=lambda: "")
        self.assertFalse(fetch.packaged_path(TARGET, self.root).exists())

    def test_something_that_is_not_the_helper_fails_its_checks(self) -> None:
        fake = Path(self.tmp.name) / "fake"
        fake.write_text("#!/bin/sh\necho '{\"name\": \"pcbridge-native\"}'\n")
        self.publish("v0.0.0-test", fake.read_bytes())
        with self.assertRaises(fetch.FetchError) as raised:
            fetch.fetch(version="0.0.0-test", target=TARGET, package_root=self.root,
                        opener=self.opener, releases=self.releases, latest=lambda: "")
        self.assertIn("not this machine", str(raised.exception))
        self.assertFalse(fetch.packaged_path(TARGET, self.root).exists())
        self.assertEqual(list(fetch.packaged_path(TARGET, self.root).parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
