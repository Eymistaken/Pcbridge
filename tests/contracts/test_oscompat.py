"""The operating-system layer behaves the same on Linux and on Windows.

Runs on both. Every check uses real processes and real files, because the
point of `pcbridge.oscompat` is what the operating system actually does.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from pcbridge import oscompat

_CHILD_LOCK = textwrap.dedent("""
    import os, sys
    from pcbridge import oscompat
    fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
    try:
        oscompat.lock_fd(fd, exclusive=True, blocking=False)
    except BlockingIOError:
        print("busy")
    else:
        print("got")
        oscompat.unlock_fd(fd)
""")


def _child(code: str, *args: str) -> str:
    result = subprocess.run([sys.executable, "-c", code, *args], capture_output=True, text=True,
                            timeout=30, check=True)
    return result.stdout.strip()


class LockTest(unittest.TestCase):
    def test_an_exclusive_lock_excludes_another_process_until_released(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.lock")
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                oscompat.lock_fd(fd, exclusive=True)
                self.assertEqual(_child(_CHILD_LOCK, path), "busy")
                oscompat.unlock_fd(fd)
                self.assertEqual(_child(_CHILD_LOCK, path), "got")
            finally:
                os.close(fd)

    def test_shared_locks_coexist_and_block_an_exclusive_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.lock")
            first = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            second = os.open(path, os.O_RDWR)
            try:
                oscompat.lock_fd(first, exclusive=False)
                oscompat.lock_fd(second, exclusive=False, blocking=False)
                self.assertEqual(_child(_CHILD_LOCK, path), "busy")
            finally:
                oscompat.unlock_fd(second)
                oscompat.unlock_fd(first)
                os.close(second)
                os.close(first)


class PrivateFileTest(unittest.TestCase):
    def _private_to_user(self, path: Path) -> None:
        if oscompat.IS_WINDOWS:
            listing = subprocess.run(["icacls", str(path)], capture_output=True, text=True,
                                     check=True).stdout
            # "C:\\dir DOMAIN\\user:(OI)(CI)(F)" on the first line, one entry per
            # line after it; the summary lines carry no "(".
            entries = re.findall(r"(\S+):\(", listing)
            self.assertEqual(len(entries), 1, listing)
            self.assertTrue(entries[0].lower().endswith("\\" + oscompat.current_user().lower()), listing)
            self.assertTrue(oscompat.owned_by_me(path))
        else:
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode & 0o077, 0, oct(mode))
            self.assertTrue(oscompat.owned_by_me(path))

    def test_restrict_path_leaves_only_the_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "private"
            directory.mkdir()
            oscompat.restrict_path(directory, directory=True)
            self._private_to_user(directory)
            secret = directory / "secret.txt"
            secret.write_text("x", encoding="utf-8")
            oscompat.restrict_path(secret)
            self._private_to_user(secret)
            self.assertEqual(secret.read_text(encoding="utf-8"), "x")

    def test_a_link_is_recognized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.mkdir()
            link = Path(tmp) / "link"
            if oscompat.IS_WINDOWS:
                # A junction needs no privilege, unlike a symlink.
                subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                               capture_output=True, check=True)
            else:
                link.symlink_to(target)
            self.assertTrue(oscompat.is_link_or_reparse(link))
            self.assertFalse(oscompat.is_link_or_reparse(target))
            self.assertFalse(oscompat.is_link_or_reparse(Path(tmp) / "missing"))


class ProcessTest(unittest.TestCase):
    def test_liveness_identity_and_start_time(self) -> None:
        before = time.time()
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            time.sleep(0.3)
            self.assertTrue(oscompat.pid_alive(child.pid))
            identity = oscompat.process_start_identity(child.pid)
            self.assertEqual(identity, oscompat.process_start_identity(child.pid))
            self.assertNotEqual(identity, oscompat.process_start_identity(os.getpid()))
            started = oscompat.process_started_at(child.pid)
            self.assertIsNotNone(started)
            self.assertLess(abs(started - before), 5.0)
            self.assertTrue(oscompat.process_owned_by_me(child.pid))
        finally:
            child.kill()
            child.wait(10)
        self.assertFalse(oscompat.pid_alive(child.pid))

    def test_terminate_group_ends_the_child_and_its_children(self) -> None:
        code = ("import subprocess, sys, time; "
                "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                "print(c.pid, flush=True); time.sleep(60)")
        kwargs = {"start_new_session": True} if not oscompat.IS_WINDOWS else {}
        parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, **kwargs)
        assert parent.stdout is not None
        with parent.stdout:
            grandchild = int(parent.stdout.readline())
        self.assertTrue(oscompat.pid_alive(grandchild))
        oscompat.terminate_group(parent.pid, force=True, own_group=True)
        parent.wait(15)
        self.assertFalse(oscompat.pid_alive(parent.pid))
        deadline = time.monotonic() + 10
        while oscompat.pid_alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(oscompat.pid_alive(grandchild))

    def test_a_missing_process_is_not_alive(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(10)
        self.assertFalse(oscompat.pid_alive(child.pid))
        if not oscompat.IS_WINDOWS:
            # Windows keeps an exited process's object, and its creation
            # time, while any handle to it is open (here Popen's).
            with self.assertRaises((ProcessLookupError, OSError, ValueError)):
                oscompat.process_start_identity(child.pid)


class UserTest(unittest.TestCase):
    def test_current_user_is_a_name(self) -> None:
        name = oscompat.current_user()
        self.assertTrue(name)
        self.assertNotIn("\\", name)


if __name__ == "__main__":
    unittest.main()
