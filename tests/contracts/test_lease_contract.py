#!/usr/bin/env python3
"""Cross-process contracts for desktop grants and native process records."""

from __future__ import annotations

import json
import multiprocessing
import os
import signal
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.desktop import safety as safetylib  # noqa: E402
from pcbridge.desktop.lease import (  # noqa: E402
    LEASE_LOCK_FILE,
    SCHEMA_VERSION,
    LeaseStore,
)
from pcbridge.native.registry import (  # noqa: E402
    NativeProcessEntry,
    NativeRegistry,
    process_start_identity,
)


def _config(root: Path, *, idle_seconds: int = 90) -> SimpleNamespace:
    return SimpleNamespace(
        state_dir=root,
        audit_log=root / "audit.log",
        audit_max_bytes=0,
        desktop=SimpleNamespace(
            enabled=True,
            unlock_default_minutes=15,
            unlock_max_minutes=120,
            unlock_idle_seconds=idle_seconds,
            max_actions_per_second=0,
            idle_guard_seconds=0,
        ),
    )


def _revoke_many(root: str, count: int) -> None:
    gate = safetylib.SafetyGate(_config(Path(root)))
    for _ in range(count):
        gate.lock()


class LeaseContractTests(unittest.TestCase):
    def test_unlock_writes_versioned_identity_with_private_atomic_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "state"
            gate = safetylib.SafetyGate(_config(root))
            gate.unlock(5, reason="contract", granted_by="desktop_unlock")

            state_path = root / safetylib.STATE_FILE
            lock_path = root / LEASE_LOCK_FILE
            state = json.loads(state_path.read_text(encoding="utf-8"))

            self.assertEqual(state["schema_version"], SCHEMA_VERSION)
            self.assertRegex(state["grant_id"], r"^[0-9a-f]{32}$")
            self.assertEqual(state["revoke_epoch"], 0)
            self.assertEqual(state["reason"], "contract")
            self.assertEqual(state["granted_by"], "desktop_unlock")
            self.assertGreater(state["granted"], 0)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode), 0o600)
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_legacy_grant_remains_python_readable_but_is_not_native_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            until = time.time() + 60
            (root / safetylib.STATE_FILE).write_text(
                json.dumps({"until": until, "reason": "legacy"}),
                encoding="utf-8",
            )
            gate = safetylib.SafetyGate(_config(root))

            with (
                mock.patch.object(safetylib, "screen_locked", return_value=False),
                mock.patch.object(safetylib, "idle_ms", return_value=999_000),
            ):
                self.assertTrue(gate.check("screen_capture", write=False).allowed)

            snapshot = LeaseStore(root).snapshot()
            self.assertTrue(snapshot.is_active())
            self.assertFalse(snapshot.is_native_eligible())
            self.assertNotIn("schema_version", gate._read_state())

    def test_revoke_is_monotonic_across_two_python_processes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            gate = safetylib.SafetyGate(_config(root))
            gate.unlock(5)
            state_path = root / safetylib.STATE_FILE
            lock_inode = (root / LEASE_LOCK_FILE).stat().st_ino
            context = multiprocessing.get_context("spawn")
            workers = [
                context.Process(target=_revoke_many, args=(str(root), 20))
                for _ in range(2)
            ]
            for worker in workers:
                worker.start()
            reads = 0
            while any(worker.is_alive() for worker in workers):
                decoded = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertIsInstance(decoded, dict)
                reads += 1
            for worker in workers:
                worker.join(10)
                self.assertEqual(worker.exitcode, 0)

            state = gate._read_state()
            self.assertEqual(state["revoke_epoch"], 40)
            self.assertEqual(state["until"], 0)
            self.assertEqual(state["hard_until"], 0)
            self.assertEqual(state["reason"], "")
            self.assertEqual(state["granted_by"], "desktop_unlock")
            self.assertGreater(reads, 0)
            self.assertEqual((root / LEASE_LOCK_FILE).stat().st_ino, lock_inode)

    def test_nonfinite_or_wrongly_typed_deadlines_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            gate = safetylib.SafetyGate(_config(Path(raw)))
            gate._write_state({"until": float("inf"), "hard_until": float("inf")})
            self.assertFalse(gate.is_unlocked())
            gate._write_state({"until": {"not": "a timestamp"}})
            self.assertFalse(gate.is_unlocked())

    def test_check_revalidates_the_captured_identity_before_allowing_work(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            gate = safetylib.SafetyGate(_config(Path(raw)))
            gate.unlock(5)
            with (
                mock.patch.object(safetylib, "screen_locked", return_value=False),
                mock.patch.object(safetylib, "idle_ms", return_value=999_000),
                mock.patch.object(gate._lease, "touch", return_value=False),
            ):
                decision = gate.check("screen_capture", write=False)

            self.assertFalse(decision.allowed)
            self.assertEqual(decision.code, safetylib.ErrorCode.GRANT_REQUIRED)

    def test_late_heartbeat_cannot_resurrect_or_extend_a_new_grant(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = safetylib.SafetyGate(_config(root))
            second = safetylib.SafetyGate(_config(root))
            first.unlock(10, reason="first")
            stale = first.current_token()
            self.assertIsNotNone(stale)

            second.lock()
            second.unlock(10, reason="second")
            before = second.unlocked_until()

            self.assertFalse(first.touch(stale))
            self.assertAlmostEqual(second.unlocked_until(), before, delta=0.01)
            current = second.current_token()
            self.assertIsNotNone(current)
            self.assertNotEqual(current, stale)
            self.assertTrue(second.touch(current))

    def test_lock_preserves_audit_fields_and_changes_epoch_before_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            gate = safetylib.SafetyGate(_config(Path(raw)))
            gate.unlock(5, reason="keep-me", granted_by="contract")
            before = gate._read_state()
            gate.lock()
            after = gate._read_state()

            self.assertEqual(after["schema_version"], SCHEMA_VERSION)
            self.assertEqual(after["grant_id"], before["grant_id"])
            self.assertEqual(after["revoke_epoch"], before["revoke_epoch"] + 1)
            self.assertEqual(after["reason"], "keep-me")
            self.assertEqual(after["granted"], before["granted"])
            self.assertEqual(after["granted_by"], "contract")
            self.assertFalse(gate.is_unlocked())


@unittest.skipUnless(sys.platform.startswith("linux"), "registry uses Linux /proc")
class NativeRegistryContractTests(unittest.TestCase):
    def test_registry_records_process_identity_with_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = NativeRegistry(Path(raw))
            entry = registry.register(os.getpid(), "native-contract")

            self.assertEqual(entry.process_start_id, process_start_identity(os.getpid()))
            self.assertTrue(registry.is_same_process(entry))
            self.assertEqual(stat.S_IMODE(registry.directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(entry.path.stat().st_mode), 0o600)
            self.assertEqual(registry.entries(), [entry])

            registry.unregister(entry)
            self.assertEqual(registry.entries(), [])

    def test_pid_reuse_identity_mismatch_is_never_signaled(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = NativeRegistry(Path(raw))
            entry = registry.register(os.getpid(), "native-contract")
            forged = NativeProcessEntry(
                pid=entry.pid,
                process_start_id=entry.process_start_id + "-reused",
                instance_id=entry.instance_id,
                path=entry.path,
            )

            with mock.patch("pcbridge.native.registry.signal.pidfd_send_signal") as send:
                self.assertFalse(registry.terminate(forged))
            send.assert_not_called()

    def test_matching_process_is_signaled_through_a_pinned_pidfd(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = NativeRegistry(Path(raw))
            entry = registry.register(os.getpid(), "native-contract")

            with mock.patch("pcbridge.native.registry.signal.pidfd_send_signal") as send:
                self.assertTrue(registry.terminate(entry))

            send.assert_called_once()
            self.assertIsInstance(send.call_args.args[0], int)
            self.assertEqual(send.call_args.args[1], signal.SIGTERM)

    def test_stale_registry_file_is_pruned_without_signaling(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = NativeRegistry(Path(raw))
            entry = registry.register(os.getpid(), "native-contract")
            payload = json.loads(entry.path.read_text(encoding="utf-8"))
            payload["process_start_id"] = "stale"
            entry.path.write_text(json.dumps(payload), encoding="utf-8")

            with mock.patch("pcbridge.native.registry.signal.pidfd_send_signal") as send:
                self.assertEqual(registry.prune_stale(), 1)
            send.assert_not_called()
            self.assertEqual(registry.entries(), [])


if __name__ == "__main__":
    unittest.main()
