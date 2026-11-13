"""Fetching the release's native helper: every check refuses before anything is installed.

No network and no real helper: the release is a dict of URLs, `--build-info`
and the handshake are fakes. `tests/integration/test_native_fetch_live.py`
runs the same path with the real helper over a local HTTP server.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pcbridge import distro, platforminfo  # noqa: E402
from pcbridge.cli import plan as planlib  # noqa: E402
from pcbridge.desktop.session import GNOME, UNKNOWN  # noqa: E402
from pcbridge.native import fetch  # noqa: E402

TARGET = "x86_64-unknown-linux-gnu"
NAME = f"pcbridge-native-{TARGET}"
BASE = "https://example.invalid/releases"
HELPER = b"\x7fELF fake helper GLIBC_2.17 GLIBC_2.35"
GOOD_INFO = {"build_id": "abc123def456", "name": "pcbridge-native", "profile": "release",
             "protocol": {"major": 1, "minor": 0}, "target": TARGET, "test_harness": False,
             "version": "0.1.0"}


def sums(*entries: tuple[bytes, str]) -> bytes:
    return "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n"
                   for data, name in entries).encode()


class FakeRelease:
    """urlopen over a dict of URL -> bytes; anything else is a 404."""

    def __init__(self, files: dict[str, bytes], fail: dict[str, int] | None = None) -> None:
        self.files = files
        self.fail = fail or {}
        self.asked: list[str] = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.asked.append(url)
        if url in self.fail:
            raise urllib.error.HTTPError(url, self.fail[url], "error", None, None)
        if url not in self.files:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return io.BytesIO(self.files[url])


def release(tag: str, helper: bytes = HELPER, listed: bytes | None = None) -> dict[str, bytes]:
    return {
        f"{BASE}/download/{tag}/sha256sums.txt": sums(
            (listed if listed is not None else helper, NAME), (b"wheel", "pcbridge.whl")),
        f"{BASE}/download/{tag}/{NAME}": helper,
    }


def build_info_runner(info: dict | None = None, code: int = 0):
    def run(argv, **_kwargs):
        assert argv[1:] == ["--build-info"], argv
        return subprocess.CompletedProcess(argv, code, json.dumps(info or GOOD_INFO), "")

    return run


class FetchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "pcbridge"
        self.root.mkdir()
        self.dest = fetch.packaged_path(TARGET, self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_fetch(self, opener, **kwargs):
        options = dict(version="9.9.9", target=TARGET, package_root=self.root, opener=opener,
                       run=build_info_runner(), handshake=lambda _path: ("abc123def456", {}),
                       glibc=(2, 39), releases=BASE, latest=lambda: "")
        options.update(kwargs)
        return fetch.fetch(**options)

    def leftovers(self) -> list[str]:
        folder = self.dest.parent
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []

    def test_the_versions_own_release_is_installed_with_its_build_info(self) -> None:
        got = self.run_fetch(FakeRelease(release("v9.9.9")))
        self.assertEqual(got.tag, "v9.9.9")
        self.assertEqual(got.path, self.dest)
        self.assertEqual(self.dest.read_bytes(), HELPER)
        self.assertTrue(self.dest.stat().st_mode & stat.S_IXUSR)
        self.assertEqual(got.sha256, hashlib.sha256(HELPER).hexdigest())
        self.assertEqual(json.loads((self.dest.parent / "build-info.json").read_text()), GOOD_INFO)
        self.assertEqual(self.leftovers(), ["build-info.json", "pcbridge-native"])

    def test_a_checkout_newer_than_every_release_takes_the_latest(self) -> None:
        opener = FakeRelease(release("v9.9.8"))
        got = self.run_fetch(opener, latest=lambda: "v9.9.8")
        self.assertEqual(got.tag, "v9.9.8")
        self.assertIn(f"{BASE}/download/v9.9.9/sha256sums.txt", opener.asked)

    def test_a_release_without_the_asset_is_skipped_for_the_latest(self) -> None:
        files = {f"{BASE}/download/v9.9.9/sha256sums.txt": sums((b"wheel", "pcbridge.whl"))}
        files.update(release("v9.9.8"))
        self.assertEqual(self.run_fetch(FakeRelease(files), latest=lambda: "v9.9.8").tag, "v9.9.8")

    def test_no_release_with_the_asset_says_so_and_installs_nothing(self) -> None:
        with self.assertRaises(fetch.FetchError) as raised:
            self.run_fetch(FakeRelease({}), latest=lambda: "v9.9.8")
        self.assertIn("v9.9.9, v9.9.8", str(raised.exception))
        self.assertIn("scripts/build-native.sh", str(raised.exception))
        self.assertEqual(self.leftovers(), [])

    def test_the_latest_is_not_asked_twice_when_it_is_this_version(self) -> None:
        asked: list[str] = []
        with self.assertRaises(fetch.FetchError) as raised:
            self.run_fetch(FakeRelease({}), latest=lambda: asked.append("x") or "v9.9.9")
        self.assertEqual(asked, ["x"])
        self.assertNotIn(", v9.9.9", str(raised.exception))

    def test_a_checksum_mismatch_installs_nothing(self) -> None:
        with self.assertRaises(fetch.FetchError) as raised:
            self.run_fetch(FakeRelease(release("v9.9.9", listed=b"another file")))
        self.assertIn("does not match", str(raised.exception))
        self.assertEqual(self.leftovers(), [])

    def test_each_failed_check_leaves_an_existing_helper_alone(self) -> None:
        self.dest.parent.mkdir(parents=True)
        self.dest.write_bytes(b"old helper")
        cases = {
            "wrong target": dict(run=build_info_runner({**GOOD_INFO, "target": "aarch64-x"})),
            "not a program": dict(run=build_info_runner(code=126)),
            "test harness": dict(run=build_info_runner({**GOOD_INFO, "test_harness": True})),
            "debug build": dict(run=build_info_runner({**GOOD_INFO, "profile": "debug"})),
            "old glibc": dict(glibc=(2, 31)),
            "handshake": dict(handshake=lambda _path: "PROTOCOL_MISMATCH"),
        }
        for why, kwargs in cases.items():
            with self.subTest(why), self.assertRaises(fetch.FetchError):
                self.run_fetch(FakeRelease(release("v9.9.9")), **kwargs)
            self.assertEqual(self.dest.read_bytes(), b"old helper", why)
            self.assertEqual(self.leftovers(), ["pcbridge-native"], why)

    def test_the_glibc_refusal_names_both_versions(self) -> None:
        with self.assertRaises(fetch.FetchError) as raised:
            self.run_fetch(FakeRelease(release("v9.9.9")), glibc=(2, 31))
        self.assertIn("glibc 2.35", str(raised.exception))
        self.assertIn("2.31", str(raised.exception))

    def test_a_server_error_is_not_mistaken_for_a_missing_release(self) -> None:
        url = f"{BASE}/download/v9.9.9/sha256sums.txt"
        with self.assertRaises(fetch.FetchError) as raised:
            self.run_fetch(FakeRelease({}, fail={url: 403}), latest=lambda: "v9.9.8")
        self.assertIn("HTTP 403", str(raised.exception))

    def test_an_oversized_download_is_refused(self) -> None:
        with mock.patch.object(fetch, "MAX_BYTES", len(HELPER) - 1):
            with self.assertRaises(fetch.FetchError) as raised:
                self.run_fetch(FakeRelease(release("v9.9.9")))
        self.assertIn("larger than", str(raised.exception))
        self.assertEqual(self.leftovers(), [])

    def test_the_request_carries_nothing_about_the_user(self) -> None:
        seen = []

        def opener(request, timeout=None):
            seen.append(dict(request.header_items()))
            return FakeRelease(release("v9.9.9"))(request, timeout)

        self.run_fetch(opener)
        self.assertTrue(seen)
        for headers in seen:
            self.assertEqual(headers, {"User-agent": "pcbridge-setup"})


class ChecksumListTests(unittest.TestCase):
    def test_both_sha256sum_formats_and_only_the_exact_name(self) -> None:
        digest = "a" * 64
        listing = (f"{'b' * 64}  {NAME}.sig\n{digest} *{NAME}\nnot a line\n").encode()
        self.assertEqual(fetch.expected_sha256(listing, NAME), digest)
        self.assertEqual(fetch.expected_sha256(b"zz  " + NAME.encode(), NAME), "")
        self.assertEqual(fetch.expected_sha256(b"", NAME), "")


class RemedyTests(unittest.TestCase):
    def test_each_installation_is_told_its_own_way(self) -> None:
        with mock.patch.object(fetch, "_target_triple", return_value=TARGET):
            self.assertIn("reinstall the pcbridge package", fetch.remedy("deb"))
            self.assertIn("reinstall the pcbridge package", fetch.remedy("pacman"))
            self.assertIn("scripts/build-native.sh", fetch.remedy("git", has_cargo=True))
            self.assertIn("doctor --fix", fetch.remedy("git", has_cargo=True))
            for kind in ("git", "user", "pip"):
                self.assertEqual(fetch.remedy(kind, has_cargo=False),
                                 "`pcbridge doctor --fix` downloads the release's prebuilt helper")

    def test_an_architecture_without_a_build_is_told_plainly(self) -> None:
        with mock.patch.object(fetch, "_target_triple", return_value=None), \
                mock.patch("platform.machine", return_value="aarch64"):
            text = fetch.remedy("git", has_cargo=True)
        self.assertIn("aarch64", text)
        self.assertIn("not available here", text)


def gnome(**kw) -> platforminfo.Platform:
    base = dict(arch="x86_64", distro_name="Ubuntu 24.04", distro_id="ubuntu",
                family=distro.DEBIAN, package_manager="apt-get", session_type="wayland",
                desktop="GNOME", desktop_kind=GNOME, desktop_version="46.0",
                session_source="environment", glibc=(2, 39), language="en")
    base.update(kw)
    return platforminfo.Platform(**base)


def probes(kind: str, *, fetchable: bool = True, cargo: bool = False) -> planlib.Probes:
    return planlib.Probes(
        which=lambda name: (("/usr/bin/cargo" if cargo else None) if name == "cargo"
                            else f"/usr/bin/{name}"),
        atspi=lambda: True, ocr_languages=lambda: {"eng"}, desktop_extras=lambda: True,
        evdev_build=lambda: {}, own_venv=lambda: kind in ("user", "git", "pip"),
        native_fetchable=lambda: fetchable, kind=lambda: kind)


class PlanTests(unittest.TestCase):
    def test_installations_without_a_helper_download_it(self) -> None:
        for kind in ("git", "user", "pip"):
            with self.subTest(kind=kind):
                plan = planlib.build(gnome(), probes=probes(kind))
                self.assertTrue(plan.native_fetch)

    def test_packages_and_present_helpers_download_nothing(self) -> None:
        for kind, fetchable in (("deb", True), ("pacman", True), ("git", False), ("user", False)):
            with self.subTest(kind=kind, fetchable=fetchable):
                plan = planlib.build(gnome(), probes=probes(kind, fetchable=fetchable))
                self.assertFalse(plan.native_fetch)

    def test_an_unsupported_desktop_downloads_nothing(self) -> None:
        plan = planlib.build(gnome(desktop="Cinnamon", desktop_kind=UNKNOWN, session_type="x11"),
                             probes=probes("git"))
        self.assertFalse(plan.native_fetch)

    def test_a_checkout_with_rust_is_told_it_can_build_instead(self) -> None:
        plan = planlib.build(gnome(), probes=probes("git", cargo=True))
        self.assertTrue(plan.native_fetch)
        self.assertIn("scripts/build-native.sh", plan.native_note)
        self.assertEqual(planlib.build(gnome(), probes=probes("git")).native_note, "")

    def test_the_summary_says_when_the_helper_is_still_missing(self) -> None:
        plan = planlib.build(gnome(), probes=probes("git"))
        lines = "\n".join(planlib.summary(plan, probes=probes("git")))
        self.assertIn("Native helper: not installed", lines)
        self.assertIn("doctor --fix", lines)
        done = "\n".join(planlib.summary(plan, probes=probes("git", fetchable=False)))
        self.assertNotIn("Native helper", done)


class FetchableProbeTests(unittest.TestCase):
    def test_a_packaged_or_named_helper_needs_no_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = fetch.packaged_path(TARGET, root)
            config = root / "config.toml"
            config.write_text("[native]\nbinary_path = \"\"\n")
            env = {k: v for k, v in os.environ.items() if k != "PCBRIDGE_NATIVE_BIN"}
            with mock.patch("pcbridge.native.discovery._target_triple", return_value=TARGET), \
                    mock.patch.object(fetch, "PACKAGE_ROOT", root), \
                    mock.patch("pcbridge.paths.config_file", return_value=config), \
                    mock.patch.dict(os.environ, env, clear=True):
                self.assertTrue(planlib._native_fetchable())
                helper.parent.mkdir(parents=True)
                helper.write_bytes(b"x")
                helper.chmod(0o755)
                self.assertFalse(planlib._native_fetchable())
                helper.unlink()
                config.write_text("[native]\nbinary_path = \"/opt/pcbridge-native\"\n")
                self.assertFalse(planlib._native_fetchable())
                config.write_text("[native]\nbinary_path = \"\"\n")
                with mock.patch.dict(os.environ, {"PCBRIDGE_NATIVE_BIN": "/x"}):
                    self.assertFalse(planlib._native_fetchable())
            with mock.patch("pcbridge.native.discovery._target_triple", return_value=None):
                self.assertFalse(planlib._native_fetchable())


if __name__ == "__main__":
    unittest.main()
