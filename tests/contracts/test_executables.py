"""Agent CLI discovery for a daemon that does not see the login shell's PATH."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import executables as exelib  # noqa: E402


def _make_exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


class ExecutableDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="pcb-exe-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        env = mock.patch.dict(os.environ, {"HOME": str(self.home), "PATH": "/nonexistent"})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("NVM_DIR", None)

    def test_path_hit_wins(self) -> None:
        bindir = self.home / "onpath"
        exe = _make_exe(bindir / "agentx")
        _make_exe(self.home / ".local" / "bin" / "agentx")
        with mock.patch.dict(os.environ, {"PATH": str(bindir)}):
            self.assertEqual(exelib.find_executable("agentx"), exe)

    def test_user_bin_dirs_are_searched_when_path_misses(self) -> None:
        exe = _make_exe(self.home / ".npm-global" / "bin" / "agentx")
        self.assertEqual(exelib.find_executable("agentx"), exe)

    def test_newest_nvm_node_first(self) -> None:
        _make_exe(self.home / ".nvm" / "versions" / "node" / "v18.2.0" / "bin" / "agentx")
        newest = _make_exe(self.home / ".nvm" / "versions" / "node" / "v20.20.2" / "bin" / "agentx")
        self.assertEqual(exelib.find_executable("agentx"), newest)

    def test_configured_path_is_used_as_is(self) -> None:
        exe = _make_exe(self.home / "tools" / "agentx")
        self.assertEqual(exelib.find_executable(str(exe)), exe.resolve())
        self.assertIsNone(exelib.find_executable(str(self.home / "missing" / "agentx")))

    def test_not_found_names_the_setting(self) -> None:
        self.assertIsNone(exelib.find_executable("agentx"))
        msg = exelib.not_found_message("claude", "agentx", "/cfg/config.toml")
        self.assertIn("[agents.claude]", msg)
        self.assertIn("/cfg/config.toml", msg)
        self.assertIn("'agentx'", msg)


if __name__ == "__main__":
    unittest.main()
