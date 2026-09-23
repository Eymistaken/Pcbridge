#!/usr/bin/env python3
"""Fault injection for pcbridge 2.0 (Step 8): each scenario states what must
happen and checks it against a fake or a throwaway environment.

Nothing here touches the real desktop, the real grant or the real service.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.desktop.errors import DesktopError, ErrorCode  # noqa: E402
from pcbridge.native.client import NativeClient  # noqa: E402
from pcbridge.native.registry import NativeRegistry  # noqa: E402

FAKE_HELPER = ROOT / "tests/fixtures/native/fake_native_helper.py"


def run_pcbridge(*args: str, env: dict[str, str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pcbridge", *args],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=timeout,
    )


class NativeHelperCrashTests(unittest.TestCase):
    """The native helper dies mid-capture: the next call gets a new process
    and nothing of the dead one is left behind."""

    def test_a_crash_leaves_no_registry_entry_and_the_next_call_respawns(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            client = NativeClient(
                FAKE_HELPER,
                state_dir=root / "state",
                runtime_dir=root / "runtime",
                environment={"PCBRIDGE_FAKE_NATIVE_MODE": "crash"},
                default_timeout=1.0,
            )
            self.addCleanup(client.close)
            registry = NativeRegistry(root / "runtime")

            client.request("ping")
            first = client.pid
            self.assertEqual([e.pid for e in registry.entries()], [first])

            with self.assertRaises(DesktopError) as crashed:
                client.request("crash")
            self.assertEqual(crashed.exception.code, ErrorCode.NATIVE_CRASHED)
            deadline = time.monotonic() + 2.0
            while registry.entries() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(registry.entries(), [], "the dead helper is still registered")

            client.request("ping")
            self.assertNotEqual(client.pid, first)
            self.assertEqual([e.pid for e in registry.entries()], [client.pid])


if __name__ == "__main__":
    unittest.main()
