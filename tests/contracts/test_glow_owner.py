"""Frame ownership cannot silently revoke a newer process's grant."""

from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from pcbridge.desktop.errors import DesktopError, ErrorCode
from pcbridge.desktop.glowowner import FrameOwner
from pcbridge.desktop.lease import LeaseStore, LeaseToken


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("native frame", timeout)
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.returncode = -9


class FrameOwnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.store = LeaseStore(self.directory)
        self.owner = FrameOwner(self.directory, Path("/fixture/native"))

    def tearDown(self):
        self.owner.close()
        self.temporary.cleanup()

    def grant(self):
        now = time.time()
        return self.store.grant(until=now + 60, reason="frame contract", granted=now, granted_by="test").token()

    def test_conditional_revoke_matches_exact_identity_and_never_repeats(self):
        first = self.grant()
        second = self.grant()
        self.assertFalse(self.store.revoke_if(first))
        self.assertEqual(self.store.snapshot().token(), second)
        self.assertFalse(self.store.revoke_if(LeaseToken(second.grant_id, second.revoke_epoch + 1)))
        self.assertTrue(self.store.revoke_if(second))
        self.assertFalse(self.store.revoke_if(second))
        self.assertIsNone(self.store.snapshot().token())
        self.assertEqual(self.store.snapshot().revoke_epoch, second.revoke_epoch + 1)

    def test_conditional_cleanup_can_retire_expiry_but_never_a_legacy_identity(self):
        token = self.grant()
        self.store.replace({**self.store.read(), "until": 0, "hard_until": 0})
        self.assertTrue(self.store.revoke_if(token))
        self.store.replace({"grant_id": "legacy", "until": time.time() + 60})
        self.assertFalse(self.store.revoke_if(LeaseToken("legacy", 0)))

    def test_start_failure_reaps_child_and_closes_only_its_own_lease(self):
        token, process = self.grant(), FakeProcess()
        process.returncode = 1
        with mock.patch("pcbridge.desktop.glowowner.subprocess.Popen", return_value=process):
            with self.assertRaises(DesktopError) as raised:
                self.owner.open(token, timeout=0.1)
        self.assertEqual(raised.exception.code, ErrorCode.BACKEND_UNAVAILABLE)
        self.assertIsNone(self.store.snapshot().token())

    def test_failed_old_start_preserves_replacement_identity(self):
        token, process = self.grant(), FakeProcess()
        replacement = []
        def replace(_token):
            replacement.append(self.grant())
            return None
        with mock.patch("pcbridge.desktop.glowowner.subprocess.Popen", return_value=process), \
                mock.patch.object(self.owner, "health", side_effect=replace):
            with self.assertRaises(DesktopError) as raised:
                self.owner.open(token, timeout=0.1)
        self.assertEqual(raised.exception.code, ErrorCode.REVOKED)
        self.assertEqual(self.store.snapshot().token(), replacement[0])
        self.assertTrue(process.terminated)

    def test_frame_proof_precedes_success_and_death_retires_its_captured_lease(self):
        token, process = self.grant(), FakeProcess()
        with mock.patch("pcbridge.desktop.glowowner.subprocess.Popen", return_value=process) as spawn, \
                mock.patch.object(self.owner, "health", return_value={"ready": True}) as health:
            self.assertEqual(self.owner.open(token), {"ready": True})
            health.assert_called_with(token)
            arguments = spawn.call_args.args[0]
            self.assertEqual(arguments[1:], ["glow-watch", str(self.directory), token.grant_id, str(token.revoke_epoch)])
            self.assertEqual(self.owner.open(token), {"ready": True})
            self.assertEqual(spawn.call_count, 1)
            process.returncode = 1
            until = time.monotonic() + 1
            while self.store.snapshot().token() is not None and time.monotonic() < until:
                time.sleep(0.01)
            self.assertIsNone(self.store.snapshot().token())

    def test_delayed_open_and_close_cannot_disturb_a_newer_owner(self):
        token, process = self.grant(), FakeProcess()
        with mock.patch("pcbridge.desktop.glowowner.subprocess.Popen", return_value=process) as spawn, \
                mock.patch.object(self.owner, "health", return_value={"ready": True}):
            self.owner.open(token)
            replacement = self.grant()
            with self.assertRaises(DesktopError):
                self.owner.open(token)
            self.assertEqual(spawn.call_count, 1)
            self.assertFalse(process.terminated)
            self.owner.close()
            self.assertTrue(process.terminated)
            self.assertEqual(self.store.snapshot().token(), replacement)

    def test_timeout_never_reports_an_invisible_grant(self):
        token, process = self.grant(), FakeProcess()
        with mock.patch("pcbridge.desktop.glowowner.subprocess.Popen", return_value=process), \
                mock.patch.object(self.owner, "health", return_value=None):
            with self.assertRaises(DesktopError):
                self.owner.open(token, timeout=0.05)
        self.assertTrue(process.terminated)
        self.assertIsNone(self.store.snapshot().token())

    def test_interrupted_start_retires_lease_and_reaps_child(self):
        token, process = self.grant(), FakeProcess()
        with mock.patch("pcbridge.desktop.glowowner.subprocess.Popen", return_value=process), \
                mock.patch.object(self.owner, "health", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.owner.open(token)
        self.assertTrue(process.terminated)
        self.assertIsNone(self.store.snapshot().token())

    def test_repeated_open_with_lost_proof_does_not_restart_the_old_grant(self):
        token, process = self.grant(), FakeProcess()
        with mock.patch("pcbridge.desktop.glowowner.subprocess.Popen", return_value=process) as spawn, \
                mock.patch.object(self.owner, "health", return_value={"ready": True}) as health:
            self.owner.open(token)
            health.return_value = None
            with self.assertRaises(DesktopError):
                self.owner.open(token)
            self.assertEqual(spawn.call_count, 1)
        self.assertTrue(process.terminated)
        self.assertIsNone(self.store.snapshot().token())


if __name__ == "__main__":
    unittest.main()
