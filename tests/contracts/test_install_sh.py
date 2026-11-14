"""install.sh's one question before pcbridge has a Python: the arrow-key menu
on a terminal and the numbered prompt with TERM=dumb. A stub python3 that
is too old makes the script ask; "Quit" ends it before anything changes."""

from __future__ import annotations

import os
import pty
import select
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "install.sh"


def stub_bin(tmp: Path) -> Path:
    """python3 reports 3.10 and fails the 3.12 check; the 3.12+ names fail too."""
    bin_ = tmp / "bin"
    bin_.mkdir()
    old = bin_ / "python3"
    old.write_text("#!/bin/sh\ncase \"$*\" in *python_version*) echo 3.10.12 ;; esac\nexit 1\n")
    old.chmod(0o755)
    for name in ("python3.12", "python3.13", "python3.14"):
        p = bin_ / name
        p.write_text("#!/bin/sh\nexit 1\n")
        p.chmod(0o755)
    return bin_


def run_in_pty(keys: list[bytes], term: str = "xterm-256color", timeout: float = 20.0) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        env = {"PATH": f"{stub_bin(tmp)}:/usr/bin:/bin", "HOME": str(tmp), "TERM": term,
               "XDG_DATA_HOME": str(tmp / "data"), "LANG": "C.UTF-8"}
        pid, fd = pty.fork()
        if pid == 0:  # the child: the pty is its controlling terminal, so /dev/tty works
            os.chdir(tmp)
            os.execve("/bin/sh", ["sh", str(SCRIPT)], env)
        out = b""
        sent = 0
        end = time.monotonic() + timeout
        status = None
        while time.monotonic() < end:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    chunk = b""
                if not chunk:
                    break
                out += chunk
            prompt = b"enter confirm" if term != "dumb" else b"Choice [1]:"
            if sent < len(keys) and prompt in out:
                time.sleep(0.15)
                os.write(fd, keys[sent])
                sent += 1
            done, st = os.waitpid(pid, os.WNOHANG)
            if done:
                status = st
                break
        if status is None:
            try:
                _, status = os.waitpid(pid, 0)
            except ChildProcessError:
                status = 0
        os.close(fd)
        left = list(tmp.iterdir())
    code = os.waitstatus_to_exitcode(status) if status is not None else -1
    text = out.decode("utf-8", "replace")
    assert {p.name for p in left} <= {"bin", "data"}, left
    return code, text


@unittest.skipUnless(sys.platform.startswith("linux") and Path("/bin/sh").exists(), "needs a POSIX shell")
@unittest.skipUnless(SCRIPT.is_file(), "install.sh is not next to the tests (a package's test copy)")
class InstallMenuTests(unittest.TestCase):
    def test_the_arrow_keys_move_and_enter_picks(self) -> None:
        code, text = run_in_pty([b"\x1b[B", b"\x1b[B", b"\r"])
        self.assertIn("Where should pcbridge's Python come from?", text)
        self.assertIn("Download a Python for pcbridge only, with uv (Recommended)", text)
        self.assertIn("> 3) Quit", text)
        self.assertIn("Nothing was changed.", text)
        self.assertEqual(code, 1)

    def test_a_number_key_picks_at_once(self) -> None:
        code, text = run_in_pty([b"3"])
        self.assertIn("Nothing was changed.", text)
        self.assertEqual(code, 1)

    def test_a_dumb_terminal_gets_the_numbered_prompt(self) -> None:
        code, text = run_in_pty([b"3\n"], term="dumb")
        self.assertIn("  1) Download a Python for pcbridge only, with uv (Recommended)", text)
        self.assertIn("  3) Quit", text)
        self.assertIn("Choice [1]:", text)
        self.assertNotIn("enter confirm", text)
        self.assertIn("Nothing was changed.", text)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
