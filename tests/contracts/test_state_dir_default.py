"""`[paths] state_dir` on Windows: the template's Linux path is not a choice.

Measured in W10: a config that `pcbridge setup` wrote on Windows carried the
template's `state_dir = "~/.local/state/pcbridge"`, so the daemon kept its
state (the grant, jobs, the audit log, oauth.db) in `~\\.local\\state` while
setup, upgrade and uninstall worked in %LOCALAPPDATA%\\pcbridge\\state.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from pcbridge import config as configlib
from pcbridge import oscompat
from pcbridge import paths as pathslib
from pcbridge.cli import ops


def _config(tmp: Path, state_line: str) -> Path:
    """A config from the template, as setup writes it on Linux, with this line."""
    path = tmp / "config.toml"
    with mock.patch.object(oscompat, "IS_WINDOWS", False), \
            mock.patch.object(oscompat, "restrict_path"):
        ops._fresh_config(path)
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^state_dir = .*$", lambda _m: state_line, text, count=1)
    path.write_text(text, encoding="utf-8")
    return path


class StateDirTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_windows_reads_the_template_path_as_unset(self) -> None:
        path = _config(self.tmp, 'state_dir = "~/.local/state/pcbridge"')
        with mock.patch.object(oscompat, "IS_WINDOWS", True):
            cfg = configlib.load_config(str(path), check_state=False)
            default = pathslib.state_home()
        self.assertEqual(cfg.state_dir, default)
        self.assertNotIn(".local", cfg.state_dir.parts)

    def test_linux_keeps_the_template_path(self) -> None:
        path = _config(self.tmp, 'state_dir = "~/.local/state/pcbridge"')
        with mock.patch.object(oscompat, "IS_WINDOWS", False):
            cfg = configlib.load_config(str(path), check_state=False)
        self.assertEqual(cfg.state_dir, (Path.home() / ".local" / "state" / "pcbridge").resolve())

    def test_windows_keeps_a_directory_the_user_chose(self) -> None:
        own = self.tmp / "my-state"
        path = _config(self.tmp, f"state_dir = '{own}'")
        with mock.patch.object(oscompat, "IS_WINDOWS", True):
            cfg = configlib.load_config(str(path), check_state=False)
        self.assertEqual(cfg.state_dir, own.resolve())

    def test_setup_on_windows_leaves_the_key_out_of_a_new_config(self) -> None:
        for windows in (True, False):
            dest = self.tmp / ("win" if windows else "linux") / "config.toml"
            with mock.patch.object(oscompat, "IS_WINDOWS", windows), \
                    mock.patch.object(oscompat, "restrict_path"):
                ops._fresh_config(dest)
            paths = tomllib.loads(dest.read_text(encoding="utf-8"))["paths"]
            self.assertEqual("state_dir" in paths, not windows, f"windows={windows}")


if __name__ == "__main__":
    unittest.main()
