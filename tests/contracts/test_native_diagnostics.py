#!/usr/bin/env python3
"""What `doctor.sh` says about the native helper, decided without a helper.

The levels matter more than the words: a forced `rust` backend with no binary
is a failure, `auto` falls back and warns, the default only notes it. A test
harness build must be called out loudly -- it answers the protocol with a fake
backend and looks healthy. Processes, `readelf`, `ldd` and the handshake are
all replaced here; nothing is started except one tiny shell script.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.config import NativeSpec, load_config  # noqa: E402
from pcbridge.native import diagnostics  # noqa: E402


RELEASE_INFO = {
    "name": "pcbridge-native",
    "version": "0.1.0",
    "build_id": "2294156a1b2c",
    "protocol": {"major": 1, "minor": 0},
    "target": "x86_64-unknown-linux-gnu",
    "profile": "release",
    "test_harness": False,
}
READELF = "\n".join(
    f" 0x0000000000000001 (NEEDED)             Shared library: [{name}]"
    for name in ("libpipewire-0.3.so.0", "libgcc_s.so.1", "libc.so.6", "ld-linux-x86-64.so.2")
)
LDD = (
    "\tlibpipewire-0.3.so.0 => /lib/x86_64-linux-gnu/libpipewire-0.3.so.0 (0x1)\n"
    "\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x2)\n"
)
SUPPORTED = (
    "2294156a1b2c",
    {
        "backend": "linux.mutter.pipewire",
        "capabilities": [
            {"name": "capture.monitor", "status": "supported"},
            {"name": "accessibility.read", "status": "supported"},
            {"name": "accessibility.action", "status": "supported"},
        ],
    },
)
# A helper built before Task 6.2: no accessibility methods at all.
BEFORE_ACCESSIBILITY = (
    "2294156a1b2c",
    {
        "backend": "linux.mutter.pipewire",
        "capabilities": [{"name": "capture.monitor", "status": "supported"}],
    },
)


def completed(stdout: str = "", stderr: str = "", code: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=stderr)


class FakeRunner:
    def __init__(self, *, info=RELEASE_INFO, info_result=None, readelf=READELF, ldd=LDD):
        self.info_result = info_result or completed(json.dumps(info))
        self.readelf = readelf
        self.ldd = ldd

    def __call__(self, args, **kwargs):
        if args[0] == "readelf":
            return completed(self.readelf)
        if args[0] == "ldd":
            return completed(self.ldd)
        assert args[1] == "--build-info", args
        return self.info_result


class NativeDiagnostics(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.binary = self.root / "pcbridge-native"
        self.binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.binary.chmod(0o755)
        self.cfg = load_config(str(ROOT / "config.example.toml"))

    def config(
        self,
        capture: str,
        binary: Path | None,
        typing: str | None = None,
        reading: str | None = None,
    ):
        # One selection for all three unless a test sets one on its own.
        return replace(
            self.cfg,
            native=NativeSpec(
                capture=capture,
                input=typing or capture,
                accessibility=reading or capture,
                binary_path=binary,
            ),
        )

    def run_diagnose(
        self, capture="auto", binary="present", typing=None, reading=None, **kwargs
    ):
        runner = kwargs.pop("runner", FakeRunner())
        probe = kwargs.pop("probe", lambda _path: SUPPORTED)
        return diagnostics.diagnose(
            self.config(capture, self.binary if binary == "present" else None, typing, reading),
            environ={},
            run=runner,
            probe=probe,
            package_root=self.root / "empty-package",
        )

    @staticmethod
    def levels(findings, level):
        return [finding.message for finding in findings if finding.level == level]

    def test_a_missing_helper_is_as_serious_as_the_selection_makes_it(self) -> None:
        for capture, level in (("python", "info"), ("auto", "warn"), ("rust", "fail")):
            with self.subTest(capture=capture):
                findings = self.run_diagnose(capture, binary=None)
                missing = [f for f in findings if "no native helper" in f.message]
                self.assertEqual([f.level for f in missing], [level])
                self.assertIn("scripts/build-native.sh", missing[0].message)
                self.assertIn("python3-gi", findings[-1].message, "legacy GI note last")

    def test_the_input_setting_needs_the_helper_as_much_as_capture(self) -> None:
        """Since Gate 5 input is `auto` too: a missing helper is not just info."""
        for typing, level in (("python", "info"), ("auto", "warn"), ("rust", "fail")):
            with self.subTest(input=typing):
                findings = self.run_diagnose("python", binary=None, typing=typing)
                missing = [f for f in findings if "no native helper" in f.message]
                self.assertEqual([f.level for f in missing], [level])
                infos = " | ".join(self.levels(findings, "info"))
                self.assertIn(f"[native] input = {typing}", infos)

    def test_the_accessibility_setting_needs_the_helper_too(self) -> None:
        """Since Task 6.3 accessibility is `auto` as well."""
        for reading, level in (("python", "info"), ("auto", "warn"), ("rust", "fail")):
            with self.subTest(accessibility=reading):
                findings = self.run_diagnose("python", binary=None, reading=reading)
                missing = [f for f in findings if "no native helper" in f.message]
                self.assertEqual([f.level for f in missing], [level])
                infos = " | ".join(self.levels(findings, "info"))
                self.assertIn(f"[native] accessibility = {reading}", infos)

    def test_a_helper_older_than_the_accessibility_methods_is_called_out(self) -> None:
        # `auto` takes any helper it finds, so an old one breaks ui_dump.
        for reading, level in (("auto", "warn"), ("rust", "fail")):
            with self.subTest(accessibility=reading):
                findings = self.run_diagnose(
                    "auto", reading=reading, probe=lambda _path: BEFORE_ACCESSIBILITY
                )
                stale = [f for f in findings if "older build" in f.message]
                self.assertEqual([f.level for f in stale], [level])
                self.assertIn("accessibility.action", stale[0].message)
                self.assertIn("scripts/build-native.sh", stale[0].message)
        quiet = self.run_diagnose(
            "auto", reading="python", probe=lambda _path: BEFORE_ACCESSIBILITY
        )
        self.assertFalse([f for f in quiet if "older build" in f.message])

    def test_a_healthy_release_build_raises_nothing(self) -> None:
        findings = self.run_diagnose("auto")
        self.assertEqual(self.levels(findings, "fail"), [])
        self.assertEqual(self.levels(findings, "warn"), [])
        passes = " | ".join(self.levels(findings, "pass"))
        self.assertIn("build 2294156a1b2c", passes)
        self.assertIn("protocol 1.0", passes)
        self.assertIn("capture.monitor: supported", passes)
        self.assertIn("libpipewire-0.3.so.0", passes)

    def test_debug_and_test_harness_builds_are_called_out(self) -> None:
        debug = self.run_diagnose(runner=FakeRunner(info={**RELEASE_INFO, "profile": "debug"}))
        self.assertTrue(any("not a release build" in m for m in self.levels(debug, "warn")))

        harness = self.run_diagnose(runner=FakeRunner(info={**RELEASE_INFO, "test_harness": True}))
        self.assertTrue(any("test-harness" in m for m in self.levels(harness, "fail")))

    def test_a_protocol_or_target_mismatch_fails(self) -> None:
        other = {**RELEASE_INFO, "protocol": {"major": 2, "minor": 0}, "target": "aarch64-apple-darwin"}
        failures = self.levels(self.run_diagnose(runner=FakeRunner(info=other)), "fail")
        self.assertTrue(any("protocol 2" in m for m in failures), failures)
        self.assertTrue(any("aarch64-apple-darwin" in m for m in failures), failures)

    def test_an_old_helper_without_build_info_is_a_warning_not_a_crash(self) -> None:
        old = completed(stderr="pcbridge-native: unsupported command-line arguments\n", code=2)
        findings = self.run_diagnose(runner=FakeRunner(info_result=old))
        self.assertTrue(any("older than Task 4.1" in m for m in self.levels(findings, "warn")))

    def test_libraries_that_do_not_resolve_or_are_not_documented(self) -> None:
        unresolved = self.run_diagnose(
            runner=FakeRunner(ldd="\tlibpipewire-0.3.so.0 => not found\n")
        )
        self.assertTrue(any("libpipewire-0.3.so.0" in m for m in self.levels(unresolved, "fail")))

        extra = READELF + "\n 0x1 (NEEDED) Shared library: [libgstreamer-1.0.so.0]"
        surprising = self.run_diagnose(runner=FakeRunner(readelf=extra))
        self.assertTrue(any("libgstreamer" in m for m in self.levels(surprising, "warn")))

    def test_unavailable_capture_carries_the_helpers_reason(self) -> None:
        unavailable = (
            "2294156a1b2c",
            {
                "backend": "linux.mutter.pipewire",
                "capabilities": [{
                    "name": "capture.monitor",
                    "status": "unavailable",
                    "reason": "org.gnome.Mutter.ScreenCast has no owner; this is not a Mutter session",
                }],
            },
        )
        warnings = self.levels(self.run_diagnose(probe=lambda _path: unavailable), "warn")
        self.assertTrue(any("not a Mutter session" in m for m in warnings), warnings)

    def test_a_degraded_entry_shows_its_limitation(self) -> None:
        degraded = (
            "2294156a1b2c",
            {
                "backend": "linux.mutter.pipewire",
                "capabilities": [
                    *SUPPORTED[1]["capabilities"],
                    {"name": "window.list", "status": "degraded",
                     "limitations": ["Only accessibility-visible applications are listed."]},
                ],
            },
        )
        warnings = self.levels(self.run_diagnose(probe=lambda _path: degraded), "warn")
        self.assertTrue(
            any("window.list" in m and "accessibility-visible" in m for m in warnings), warnings
        )

    def test_a_failed_handshake_is_a_failure(self) -> None:
        failures = self.levels(
            self.run_diagnose(probe=lambda _path: "handshake failed: EOF"), "fail"
        )
        self.assertIn("handshake failed: EOF", failures)

    def test_build_info_runs_the_binary_itself(self) -> None:
        script = self.root / "describes-itself"
        script.write_text(
            "#!/bin/sh\n[ \"$1\" = --build-info ] || exit 2\n"
            f"echo '{json.dumps(RELEASE_INFO)}'\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        self.assertEqual(diagnostics.read_build_info(script), RELEASE_INFO)


if __name__ == "__main__":
    unittest.main(verbosity=2)
