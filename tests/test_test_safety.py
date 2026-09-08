#!/usr/bin/env python3
"""Regression tests for live desktop-test opt-in boundaries."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LIVE_FLAGS = (
    "PCBRIDGE_TEST_CAPTURE",
    "PCBRIDGE_TEST_INPUT",
    "PCBRIDGE_TEST_ATSPI",
    "PCBRIDGE_TEST_BATCH",
)


class LiveTestSelectionSafetyTests(unittest.TestCase):
    def test_capture_only_never_selects_uinput_or_batch(self) -> None:
        probe = textwrap.dedent(
            """
            import importlib.util
            import os
            import sys
            from pathlib import Path

            root = Path(sys.argv[1])
            spec = importlib.util.spec_from_file_location(
                "pcbridge_test_desktop_probe", root / "tests" / "test_desktop.py"
            )
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)

            def forbidden_device(*args, **kwargs):
                raise AssertionError("capture-only selection constructed a uinput device")

            module.I.InputBackend._make_keyboard = forbidden_device
            module.I.InputBackend._make_pointer = forbidden_device

            class Selected(RuntimeError):
                pass

            def mark_selected(title):
                raise Selected(title)

            module.section = mark_selected

            def selected(function, **flags):
                for name in (
                    "PCBRIDGE_TEST_CAPTURE",
                    "PCBRIDGE_TEST_INPUT",
                    "PCBRIDGE_TEST_ATSPI",
                    "PCBRIDGE_TEST_BATCH",
                ):
                    os.environ.pop(name, None)
                os.environ.update({name: "1" for name, enabled in flags.items() if enabled})
                try:
                    function()
                except Selected:
                    return True
                return False

            capture_only = {"PCBRIDGE_TEST_CAPTURE": True}
            assert selected(module.test_real_capture, **capture_only)
            assert selected(module.test_real_screencast, **capture_only)
            assert not selected(module.test_real_hold, **capture_only)
            assert not selected(module.test_real_batch, **capture_only)

            assert selected(module.test_real_hold, PCBRIDGE_TEST_INPUT=True)
            assert not selected(module.test_real_batch, PCBRIDGE_TEST_BATCH=True)
            assert not selected(module.test_real_batch, PCBRIDGE_TEST_INPUT=True)
            assert selected(
                module.test_real_batch,
                PCBRIDGE_TEST_INPUT=True,
                PCBRIDGE_TEST_BATCH=True,
            )
            print("capture-only-safe")
            """
        )
        env = os.environ.copy()
        for name in LIVE_FLAGS:
            env.pop(name, None)
        env["PCBRIDGE_TEST_CAPTURE"] = "1"

        result = subprocess.run(
            [sys.executable, "-c", probe, str(ROOT)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(
            result.returncode,
            0,
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("capture-only-safe", result.stdout)


if __name__ == "__main__":
    unittest.main()
