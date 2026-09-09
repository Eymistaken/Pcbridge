#!/usr/bin/env python3
"""End-to-end revoke lifecycle across Python and two native helpers."""

from __future__ import annotations

import multiprocessing
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.desktop.errors import DesktopError, ErrorCode  # noqa: E402
from pcbridge.desktop.lease import LeaseToken  # noqa: E402
from pcbridge.desktop.safety import SafetyGate  # noqa: E402
from pcbridge.native.client import NativeClient  # noqa: E402
from pcbridge.native.registry import NativeRegistry  # noqa: E402


def _config(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        state_dir=root / "state",
        audit_log=root / "audit.log",
        audit_max_bytes=0,
        desktop=SimpleNamespace(
            enabled=True,
            unlock_default_minutes=15,
            unlock_max_minutes=120,
            unlock_idle_seconds=90,
            max_actions_per_second=0,
            idle_guard_seconds=0,
        ),
    )


def _late_heartbeat(
    root: str,
    grant_id: str,
    revoke_epoch: int,
    ready: multiprocessing.synchronize.Event,
    proceed: multiprocessing.synchronize.Event,
    result: multiprocessing.queues.Queue,
) -> None:
    gate = SafetyGate(_config(Path(root)))
    ready.set()
    if not proceed.wait(10):
        result.put(False)
        return
    result.put(gate.touch(LeaseToken(grant_id, revoke_epoch)))


@unittest.skipUnless(sys.platform.startswith("linux"), "native lifecycle uses Linux")
class NativeRevokeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        completed = subprocess.run(
            [
                "cargo",
                "build",
                "-p",
                "pcbridge-native",
                "--features",
                "test-harness",
            ],
            cwd=ROOT / "rust",
            capture_output=True,
            text=True,
            timeout=120,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr)
        cls.native_binary = ROOT / "rust/target/debug/pcbridge-native"

    def test_global_revoke_stops_two_helpers_and_rejects_late_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cfg = _config(root)
            gate = SafetyGate(cfg)
            gate.unlock(5, reason="integration")
            token = gate.current_token()
            self.assertIsNotNone(token)
            assert token is not None

            context = multiprocessing.get_context("spawn")
            ready = context.Event()
            proceed = context.Event()
            result = context.Queue()
            heartbeat = context.Process(
                target=_late_heartbeat,
                args=(
                    str(root),
                    token.grant_id,
                    token.revoke_epoch,
                    ready,
                    proceed,
                    result,
                ),
            )
            heartbeat.start()
            self.assertTrue(ready.wait(10))

            clients = [
                NativeClient(
                    self.native_binary,
                    state_dir=cfg.state_dir,
                    runtime_dir=root / "runtime",
                )
                for _ in range(2)
            ]
            try:
                for client in clients:
                    self.assertTrue(client.request("test.hold_resource").result["open"])
                self.assertEqual(len(NativeRegistry(root / "runtime").entries()), 2)

                gate.lock()
                proceed.set()
                heartbeat.join(10)
                self.assertEqual(heartbeat.exitcode, 0)
                self.assertFalse(result.get(timeout=2))
                self.assertFalse(gate.is_unlocked())

                started = time.monotonic()
                while True:
                    states = [
                        client.request("test.resource_status").result["open"]
                        for client in clients
                    ]
                    if states == [False, False]:
                        break
                    self.assertLess(time.monotonic() - started, 1.0)
                    time.sleep(0.02)
                self.assertLessEqual(time.monotonic() - started, 1.0)

                for client in clients:
                    with self.assertRaises(DesktopError) as raised:
                        client.request("test.hold_resource")
                    self.assertEqual(raised.exception.code, ErrorCode.REVOKED)

                gate.unlock(5, reason="replacement")
                for client in clients:
                    with self.assertRaises(DesktopError) as raised:
                        client.request("test.hold_resource")
                    self.assertEqual(raised.exception.code, ErrorCode.REVOKED)
            finally:
                proceed.set()
                if heartbeat.is_alive():
                    heartbeat.terminate()
                    heartbeat.join(5)
                for client in clients:
                    client.close()

            self.assertEqual(NativeRegistry(root / "runtime").entries(), [])


if __name__ == "__main__":
    unittest.main()
