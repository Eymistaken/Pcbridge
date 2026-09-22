#!/usr/bin/env python3
"""The packaged helper runs from anywhere and needs only what the notes list.

Task 4.1. The binary under test is the one `scripts/build-native.sh` installs
into `pcbridge/_native/<target>/`, or `PCBRIDGE_NATIVE_BIN`; with neither, the
binary checks skip. Each check copies the binary into a fresh directory and runs
it there with a working directory and an environment that know nothing about
this repository: a helper that only works from `rust/target` is not packaged.

The handshake check needs a session bus. It asks for `capabilities`, which
opens no screen share and requests nothing, in a throwaway state directory.
`PCBRIDGE_TEST_REQUIRE_RELEASE=1` (CI) makes a debug build a failure even when
the binary came from `PCBRIDGE_NATIVE_BIN`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.config import NativeSpec, load_config  # noqa: E402
from pcbridge.desktop.backends.python import PythonCaptureProvider  # noqa: E402
from pcbridge.desktop.backends.rust import RustCaptureProvider  # noqa: E402
from pcbridge.desktop.errors import DesktopError, ErrorCategory, ErrorCode  # noqa: E402
from pcbridge.desktop.runtime import select_capture_provider  # noqa: E402
from pcbridge.native.client import NativeClient  # noqa: E402
from pcbridge.native.diagnostics import EXPECTED_LIBRARIES  # noqa: E402
from pcbridge.native.discovery import _target_triple, discover_native_binary  # noqa: E402
from pcbridge.native.protocol import PROTOCOL_MAJOR  # noqa: E402


TARGET = "x86_64-unknown-linux-gnu"
PACKAGED = ROOT / "pcbridge" / "_native" / TARGET / "pcbridge-native"


def helper_under_test() -> tuple[Path, bool] | None:
    """(binary, came from the package directory) or None."""
    explicit = os.environ.get("PCBRIDGE_NATIVE_BIN", "").strip()
    if explicit:
        return Path(explicit), False
    if PACKAGED.is_file():
        return PACKAGED, True
    return None


def workspace_version() -> str:
    text = (ROOT / "rust" / "Cargo.toml").read_text(encoding="utf-8")
    match = re.search(r'^\[workspace\.package\]\s*\nversion = "([^"]+)"', text, re.M)
    if match is None:
        raise AssertionError("rust/Cargo.toml has no [workspace.package] version")
    return match.group(1)


@unittest.skipUnless(
    sys.platform.startswith("linux") and _target_triple() == TARGET,
    "the first packaged artifact is x86_64 Linux",
)
class PackagedHelper(unittest.TestCase):
    def setUp(self) -> None:
        found = helper_under_test()
        if found is None:
            self.skipTest(
                "no packaged helper: run scripts/build-native.sh or set PCBRIDGE_NATIVE_BIN"
            )
        source, self.from_package = found
        self.scratch = Path(tempfile.mkdtemp(prefix="pcb-package-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        (self.scratch / "bin").mkdir()
        self.binary = self.scratch / "bin" / "pcbridge-native"
        shutil.copy2(source, self.binary)

    def build_info(self) -> dict:
        proc = subprocess.run(
            [str(self.binary), "--build-info"],
            cwd=self.scratch,
            env={"PATH": "/usr/bin:/bin", "HOME": str(self.scratch), "LANG": "C"},
            capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        return json.loads(proc.stdout)

    def test_the_copied_binary_describes_itself_from_an_unrelated_directory(self) -> None:
        info = self.build_info()
        self.assertEqual(info["name"], "pcbridge-native")
        self.assertEqual(info["version"], workspace_version())
        self.assertEqual(info["protocol"]["major"], PROTOCOL_MAJOR)
        self.assertEqual(info["target"], TARGET)
        self.assertIs(info["test_harness"], False, "a test-harness build is never packaged")
        self.assertTrue(info["build_id"])
        if self.from_package or os.environ.get("PCBRIDGE_TEST_REQUIRE_RELEASE") == "1":
            self.assertEqual(info["profile"], "release")
            self.assertNotEqual(info["build_id"], "dev", "a packaged build names its commit")
        sidecar = PACKAGED.with_name("build-info.json")
        if self.from_package and sidecar.is_file():
            self.assertEqual(json.loads(sidecar.read_text(encoding="utf-8")), info)

    def test_it_links_only_against_pipewire_and_the_c_runtime(self) -> None:
        proc = subprocess.run(
            ["readelf", "-d", str(self.binary)],
            capture_output=True, text=True, timeout=10, check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
        needed = {
            line.split("[", 1)[1].split("]", 1)[0]
            for line in proc.stdout.splitlines()
            if "(NEEDED)" in line
        }
        self.assertTrue(needed, proc.stdout)
        self.assertLessEqual(needed, EXPECTED_LIBRARIES)
        for legacy in ("gstreamer", "gobject", "glib", "girepository", "python"):
            self.assertFalse(
                any(legacy in library for library in needed),
                f"native capture must not need {legacy}: {sorted(needed)}",
            )
        ldd = subprocess.run(
            ["ldd", str(self.binary)], capture_output=True, text=True, timeout=10, check=False
        )
        self.assertNotIn("not found", ldd.stdout)

    @unittest.skipUnless(
        os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "needs a desktop session bus"
    )
    def test_a_handshake_from_an_unrelated_directory_matches_the_build(self) -> None:
        info = self.build_info()
        state = self.scratch / "state"
        runtime = self.scratch / "runtime"
        state.mkdir()
        runtime.mkdir()
        # The helper inherits the working directory; nothing in the repository
        # is reachable relative to this one.
        previous = os.getcwd()
        os.chdir(self.scratch)
        self.addCleanup(os.chdir, previous)
        client = NativeClient(self.binary, state_dir=state, runtime_dir=runtime)
        self.addCleanup(client.close)

        response = client.request("capabilities", {}, timeout=10)

        self.assertIsNone(response.error, response.error)
        self.assertEqual(client.handshake.build_id, info["build_id"])
        self.assertEqual(client.handshake.native_version, info["version"])
        self.assertEqual(response.result["backend"], "linux.mutter.pipewire")
        monitor = response.result["capabilities"][0]
        self.assertEqual(monitor["name"], "capture.monitor")
        if monitor["status"] != "supported":
            self.assertTrue(monitor.get("reason"), monitor)


class WithoutAHelper(unittest.TestCase):
    def test_discovery_finds_the_packaged_layout_and_nothing_else(self) -> None:
        target = _target_triple()
        if target is None:
            self.skipTest("no packaged target for this platform")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with self.assertRaises(DesktopError) as raised:
                discover_native_binary(NativeSpec(), environ={}, package_root=root)
            self.assertEqual(raised.exception.code, ErrorCode.NATIVE_NOT_FOUND)

            placed = root / "_native" / target / "pcbridge-native"
            placed.parent.mkdir(parents=True)
            placed.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            placed.chmod(0o755)
            self.assertEqual(
                discover_native_binary(NativeSpec(), environ={}, package_root=root),
                placed.resolve(),
            )

    def test_the_python_install_keeps_capturing_when_no_helper_exists(self) -> None:
        cfg = load_config(str(ROOT / "config.example.toml"))
        not_found = DesktopError(
            code=ErrorCode.NATIVE_NOT_FOUND,
            message="The pcbridge native helper was not found.",
            category=ErrorCategory.IPC,
            retryable=False,
            suggested_action="configure_native_binary",
            backend="pcbridge-native",
        )
        with mock.patch(
            "pcbridge.desktop.backends.rust.discover_native_binary", side_effect=not_found
        ):
            automatic = select_capture_provider(
                replace(cfg, native=NativeSpec(capture="auto")), gate=None
            )
            forced = select_capture_provider(
                replace(cfg, native=NativeSpec(capture="rust")), gate=None
            )

            self.assertIsInstance(automatic, PythonCaptureProvider)
            self.assertNotIsInstance(automatic, RustCaptureProvider)
            self.assertIn("not found", automatic.degraded_reason)
            # Forced stays forced, and says why it cannot capture.
            self.assertIsInstance(forced, RustCaptureProvider)
            ready, why = forced.available()
            self.assertFalse(ready)
            self.assertIn("not found", why)


if __name__ == "__main__":
    unittest.main(verbosity=2)
