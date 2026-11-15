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


@unittest.skipIf(sys.platform == "win32", "the Linux directories and execute bits")
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


NPM_SHIM = r"""@ECHO off
GOTO start
:find_dp0
SET dp0=%~dp0
EXIT /b
:start
SETLOCAL
CALL :find_dp0

IF EXIST "%dp0%\node.exe" (
  SET "_prog=%dp0%\node.exe"
) ELSE (
  SET "_prog=node"
  SET PATHEXT=%PATHEXT:;.JS;=;%
)

endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\node_modules\@anthropic-ai\claude-code\cli.js" %*
"""


class ShimTests(unittest.TestCase):
    """A Windows .cmd shim is never handed a prompt (cmd.exe would parse it)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_an_npm_shim_becomes_node_and_its_script(self) -> None:
        shim = self.dir / "claude.cmd"
        shim.write_text(NPM_SHIM, encoding="utf-8")
        node = self.dir / "node.exe"
        node.write_bytes(b"")
        script = self.dir / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
        script.parent.mkdir(parents=True)
        script.write_text("", encoding="utf-8")
        argv = exelib.direct_argv(shim)
        self.assertEqual(argv, [str(node), str(script)])

    def test_a_batch_file_that_is_not_an_npm_shim_is_refused(self) -> None:
        bat = self.dir / "agent.bat"
        bat.write_text("@echo off\r\nreal-agent.exe %*\r\n", encoding="utf-8")
        self.assertIsNone(exelib.direct_argv(bat))
        self.assertIn("cmd.exe", exelib.refused_shim_message(bat))

    def test_a_shim_whose_script_is_missing_is_refused(self) -> None:
        shim = self.dir / "claude.cmd"
        shim.write_text(NPM_SHIM, encoding="utf-8")
        (self.dir / "node.exe").write_bytes(b"")
        self.assertIsNone(exelib.direct_argv(shim))

    def test_a_program_passes_through(self) -> None:
        exe = self.dir / "claude.exe"
        self.assertEqual(exelib.direct_argv(exe), [str(exe)])


@unittest.skipUnless(sys.platform == "win32", "Windows lookup rules")
class WindowsDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"APPDATA": str(self.home / "Roaming"),
                                                 "LOCALAPPDATA": str(self.home / "Local"),
                                                 "PATH": str(self.home / "empty"),
                                                 "USERPROFILE": str(self.home)})
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def test_npm_prefix_and_pathext(self) -> None:
        npm = self.home / "Roaming" / "npm"
        npm.mkdir(parents=True)
        (npm / "agentx").write_bytes(b"")          # npm's shell shim: not a program
        (npm / "agentx.cmd").write_text("x", encoding="utf-8")
        found = exelib.find_executable("agentx")
        self.assertEqual(found, npm / "agentx.cmd")

    def test_a_backslash_path_is_a_path(self) -> None:
        exe = self.home / "tools" / "agentx.exe"
        exe.parent.mkdir()
        exe.write_bytes(b"")
        self.assertEqual(exelib.find_executable(str(exe)), exe.resolve())


if __name__ == "__main__":
    unittest.main()
