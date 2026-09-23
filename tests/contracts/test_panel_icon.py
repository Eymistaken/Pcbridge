"""`panel_icon`: an agent hides pcbridge's icon only while desktop control is closed."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pcbridge import app as applib  # noqa: E402
from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop import compositor, panelicon  # noqa: E402

CONFIG = """config_version = 2
public_url = "http://localhost:8765"
default_agent = "claude"
[auth]
password = "panel-icon-test-password"
[paths]
state_dir = "{state}"
[desktop]
enabled = false
[agents.claude]
command = ["true"]
"""


def done(stdout: str = "", stderr: str = "", returncode: int = 0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


class SettingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.schemas = Path(self.tmp.name) / "schemas"
        self.schemas.mkdir()
        (self.schemas / "gschemas.compiled").write_bytes(b"")
        patcher = mock.patch.object(panelicon, "schema_dirs", return_value=[self.schemas])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_hide_writes_the_extension_key_through_its_own_schema(self) -> None:
        with mock.patch.object(panelicon.subprocess, "run", return_value=done()) as run:
            panelicon.set_mode("when-granted")
        argv = run.call_args.args[0]
        self.assertEqual(argv, ["gsettings", "--schemadir", str(self.schemas), "set",
                                panelicon.SCHEMA_ID, panelicon.KEY, "when-granted"])
        # gsettings' messages are read, so they must not be translated.
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")

    def test_only_the_two_modes_are_written(self) -> None:
        with mock.patch.object(panelicon.subprocess, "run") as run, \
                self.assertRaises(ValueError):
            panelicon.set_mode("never")
        run.assert_not_called()

    def test_the_stored_mode_is_read_without_quotes(self) -> None:
        with mock.patch.object(panelicon.subprocess, "run", return_value=done("'when-granted'\n")):
            self.assertEqual(panelicon.get_mode(), "when-granted")

    def test_an_extension_older_than_the_key_is_named(self) -> None:
        with mock.patch.object(panelicon.subprocess, "run",
                               return_value=done(stderr="No such key “indicator-mode”\n",
                                                 returncode=1)), \
                self.assertRaises(panelicon.PanelIconError) as err:
            panelicon.get_mode()
        self.assertIn("predates this setting", str(err.exception))

    def test_no_installed_extension_is_an_error_not_a_guess(self) -> None:
        (self.schemas / "gschemas.compiled").unlink()
        with mock.patch.object(panelicon.subprocess, "run") as run, \
                self.assertRaises(panelicon.PanelIconError) as err:
            panelicon.get_mode()
        run.assert_not_called()
        self.assertIn("pcbridge setup", str(err.exception))

    def test_the_running_extension_version(self) -> None:
        with mock.patch.object(panelicon.subprocess, "run", return_value=done('s "2.2.0"\n')):
            self.assertEqual(panelicon.running_version(), "2.2.0")
        with mock.patch.object(panelicon.subprocess, "run", return_value=done(returncode=1)):
            self.assertIsNone(panelicon.running_version())
        for version, ok in (("2.2.0", True), ("2.10.1", True), ("3.0.0", True),
                            ("2.1.0", False), ("2.0.0", False), ("", False), (None, False)):
            self.assertIs(panelicon.running_supports_mode(version), ok, version)


class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        cfg_path = root / "config.toml"
        cfg_path.write_text(CONFIG.format(state=root / "state"))
        cfg_path.chmod(0o600)
        with mock.patch.dict(os.environ, {"PCBRIDGE_CONFIG": str(cfg_path)}):
            self.mcp, _provider = applib.build_app(load_config(), transport="stdio")

    def call(self, **args) -> str:
        result = asyncio.run(self.mcp.call_tool("panel_icon", args))
        return "\n".join(getattr(block, "text", "") for block in result.content)

    def test_hide_sets_when_granted_and_says_the_grant_still_shows_it(self) -> None:
        with mock.patch.object(compositor, "is_kde", return_value=False), \
                mock.patch.object(panelicon, "set_mode") as set_mode, \
                mock.patch.object(panelicon, "get_mode", return_value="when-granted"), \
                mock.patch.object(panelicon, "running_version", return_value="2.2.0"):
            text = self.call(action="hide")
        set_mode.assert_called_once_with("when-granted")
        self.assertIn("appears whenever desktop control is granted", text)
        self.assertNotIn("next login", text)

    def test_show_and_status(self) -> None:
        with mock.patch.object(compositor, "is_kde", return_value=False), \
                mock.patch.object(panelicon, "set_mode") as set_mode, \
                mock.patch.object(panelicon, "get_mode", return_value="always"), \
                mock.patch.object(panelicon, "running_version", return_value="2.0.0"):
            shown = self.call(action="show")
            set_mode.assert_called_once_with("always")
            status = self.call()
        self.assertEqual(set_mode.call_count, 1, "status changes nothing")
        self.assertIn("shown all the time", status)
        self.assertIn("after the next login", shown)

    def test_plasma_has_nothing_to_hide(self) -> None:
        with mock.patch.object(compositor, "is_kde", return_value=True), \
                mock.patch.object(panelicon, "set_mode") as set_mode:
            text = self.call(action="hide")
        set_mode.assert_not_called()
        self.assertIn("KDE Plasma has no pcbridge panel icon", text)

    def test_a_failure_is_said_in_the_answer(self) -> None:
        with mock.patch.object(compositor, "is_kde", return_value=False), \
                mock.patch.object(panelicon, "set_mode",
                                  side_effect=panelicon.PanelIconError("no extension")):
            text = self.call(action="hide")
        self.assertIn("could not be changed: no extension", text)


if __name__ == "__main__":
    unittest.main()
