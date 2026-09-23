#!/usr/bin/env python3
"""End-to-end revoke lifecycle across Python and two native helpers."""

from __future__ import annotations

import dataclasses
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

from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop.backends.rust import RustInputProvider  # noqa: E402
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
        if not (ROOT / "rust" / "Cargo.toml").is_file():
            raise unittest.SkipTest("builds the native test harness; needs the rust/ workspace of a git checkout")
        # The helper validates every grant against the REAL screen lock.
        # Without a readable, unlocked session (CI) it rightly refuses.
        from pcbridge.desktop.safety import ScreenLockState, observe_screen_lock

        if observe_screen_lock().state != ScreenLockState.KNOWN_UNLOCKED:
            raise unittest.SkipTest("needs an unlocked desktop session: the helper checks the real screen lock")
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

    def test_every_new_grant_gets_an_input_helper_bound_to_it(self) -> None:
        """A second unlock, then a lock and a third unlock, all still type.

        The helper runs in `--test-mode`: its keyboard is a null device, so
        nothing reaches `/dev/uinput`. Before the fix (measured 2026-09-19) the
        second and third keys came back REVOKED, because the helper bound to
        the first grant was kept and never rebinds.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            wrapper = root / "native-test-mode"
            wrapper.write_text(
                f'#!/bin/sh\nexec "{self.native_binary}" --test-mode "$@"\n',
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
            cfg = dataclasses.replace(
                load_config(str(ROOT / "config.example.toml")),
                state_dir=root / "state",
            )
            gate = SafetyGate(cfg)
            started: list[NativeClient] = []

            def helper() -> NativeClient:
                client = NativeClient(
                    wrapper, state_dir=cfg.state_dir, runtime_dir=root / "runtime"
                )
                started.append(client)
                return client

            provider = RustInputProvider(cfg, gate=gate, client_factory=helper)
            try:
                gate.unlock(5, reason="first")
                provider.key("a")
                gate.unlock(5, reason="second, while the first is open")
                provider.key("b")
                gate.lock()
                provider.close()
                self.assertFalse(started[-1].is_running, "desktop_lock stops the helper")
                gate.unlock(5, reason="third")
                provider.key("c")
            finally:
                gate.lock()
                provider.close()

            self.assertEqual(len(started), 3)
            self.assertEqual(NativeRegistry(root / "runtime").entries(), [])


if __name__ == "__main__":
    unittest.main()
