"""`pcbridge list`, `settings`, `get`, `set`, `reset`, `tools` and `restart`,
run as subprocesses against a throwaway HOME and config. `systemctl` is a
stub that only records its arguments.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.cli import main as mainlib  # noqa: E402

PASSWORD = "cli-settings-secret-password-42"
TOKEN = "cli-settings-secret-static-token-42"


class CliSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="pcb-cli-settings-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        stub = self.home / "stubs"
        stub.mkdir()
        self.log = self.home / "stub.log"
        for name in ("systemctl", "tailscale"):
            p = stub / name
            p.write_text(f'#!/bin/sh\necho "{name} $*" >> "{self.log}"\nexit 0\n')
            p.chmod(0o755)
        text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
        text = re.sub(r'(?m)^public_url = .*$', 'public_url = "http://localhost:8765"', text, count=1)
        text = re.sub(r'(?m)^password = .*$', f'password = "{PASSWORD}"', text, count=1)
        text = re.sub(r'(?m)^static_token = .*$', f'static_token = "{TOKEN}"', text, count=1)
        text = re.sub(r'(?m)^state_dir = .*$', f'state_dir = "{self.home / "state"}"', text, count=1)
        self.cfg = self.home / "config.toml"
        self.cfg.write_text(text)
        self.cfg.chmod(0o600)
        self.original = text
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{stub}:/usr/bin:/bin",
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_STATE_HOME": str(self.home / ".local" / "state"),
            "XDG_DATA_HOME": str(self.home / ".local" / "share"),
            "XDG_RUNTIME_DIR": str(self.home / "run"),
            # Never reach the real daemon's socket.
            "PCBRIDGE_SOCKET": str(self.home / "run" / "mcp.sock"),
            "PCBRIDGE_CONFIG": str(self.cfg),
            "LANG": "C.UTF-8",
        }

    def cli(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "pcbridge", *args], input=stdin,
            capture_output=True, text=True, env=self.env, cwd=str(self.home), timeout=120,
        )

    def assertNoSecrets(self, res: subprocess.CompletedProcess) -> None:
        for secret in (PASSWORD, TOKEN):
            self.assertNotIn(secret, res.stdout + res.stderr)

    # -- list -------------------------------------------------------------------

    def test_list_names_every_command(self) -> None:
        res = self.cli("list")
        self.assertEqual(res.returncode, 0, res.stderr)
        names = {c[1] for c in mainlib.COMMANDS}
        for name in names:
            self.assertRegex(res.stdout, rf"(?m)^  {re.escape(name)}\b", name)

    def test_every_dispatched_command_is_listed_and_vice_versa(self) -> None:
        listed = {c[1] for c in mainlib.COMMANDS}
        dispatched = set(mainlib._PASSTHROUGH) | set(getattr(mainlib, "_BUILTIN", {}))
        self.assertEqual(listed, dispatched)

    def test_help_lists_the_commands_too(self) -> None:
        res = self.cli("--help")
        for name in ("settings", "tools", "restart", "list"):
            self.assertIn(name, res.stdout)

    # -- settings / get ----------------------------------------------------------

    def test_settings_hides_secrets_and_marks_changes(self) -> None:
        res = self.cli("settings")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNoSecrets(res)
        self.assertIn("auth.password", res.stdout)
        self.assertIn("set (hidden)", res.stdout)
        self.assertRegex(res.stdout, r"(?m)^ \*public_url")
        res = self.cli("settings", "--json")
        self.assertNoSecrets(res)
        rows = {r["key"]: r for r in json.loads(res.stdout)}
        self.assertEqual(rows["auth.password"]["set"], True)
        self.assertNotIn("value", rows["auth.password"])
        self.assertEqual(rows["desktop.enabled"]["value"], False)

    def test_settings_filters(self) -> None:
        res = self.cli("settings", "pointer")
        self.assertIn("desktop.pointer_speed", res.stdout)
        self.assertNotIn("auth.password", res.stdout)
        self.assertEqual(self.cli("settings", "no-such-thing-zz").returncode, 1)

    def test_get_explains_a_setting(self) -> None:
        res = self.cli("get", "desktop.enabled")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("desktop.enabled = false", res.stdout)
        self.assertIn("deliberate", res.stdout)
        res = self.cli("get", "auth.static_token")
        self.assertNoSecrets(res)
        res = self.cli("get", "nope.nope")
        self.assertEqual(res.returncode, 2)
        self.assertIn("Unknown setting", res.stdout)

    # -- set / reset --------------------------------------------------------------

    def test_set_changes_one_line_with_a_backup(self) -> None:
        res = self.cli("set", "desktop.pointer_speed", "3000")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("desktop.pointer_speed = 3000", res.stdout)
        self.assertIn("pcbridge restart", res.stdout)
        self.assertEqual(tomllib.loads(self.cfg.read_text())["desktop"]["pointer_speed"], 3000)
        backups = list(self.home.glob("config.toml.backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), self.original)

    def test_set_refuses_a_bad_value_and_writes_nothing(self) -> None:
        for args in (("desktop.pointer_speed", "fast"), ("desktop.batch_budget_seconds", "500"),
                     ("tools.profile", "tiny")):
            res = self.cli("set", *args)
            self.assertEqual(res.returncode, 1, args)
            self.assertEqual(self.cfg.read_text(), self.original, args)
        self.assertEqual(list(self.home.glob("config.toml.backup-*")), [])

    def test_a_secret_is_never_taken_from_the_command_line(self) -> None:
        res = self.cli("set", "auth.password", "a-new-password-on-argv")
        self.assertEqual(res.returncode, 1)
        self.assertIn("shell history", res.stdout)
        self.assertEqual(self.cfg.read_text(), self.original)

    def test_a_secret_from_stdin_or_generated(self) -> None:
        res = self.cli("set", "auth.password", "--stdin", stdin="another-long-password-99\n")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertNotIn("another-long-password-99", res.stdout + res.stderr)
        self.assertEqual(tomllib.loads(self.cfg.read_text())["auth"]["password"], "another-long-password-99")
        res = self.cli("set", "auth.static_token", "--generate")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        token = tomllib.loads(self.cfg.read_text())["auth"]["static_token"]
        self.assertGreaterEqual(len(token), 40)
        self.assertNotIn(token, res.stdout + res.stderr)
        # A password shorter than the loader allows is refused.
        res = self.cli("set", "auth.password", "--stdin", stdin="short\n")
        self.assertEqual(res.returncode, 1)
        self.assertIn("at least 12", res.stdout)

    def test_reset_goes_back_to_the_default(self) -> None:
        self.cli("set", "desktop.pointer_speed", "3000")
        res = self.cli("reset", "desktop.pointer_speed")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("5000", res.stdout)
        self.assertNotIn("pointer_speed", tomllib.loads(self.cfg.read_text())["desktop"])

    def test_enabling_desktop_control_warns(self) -> None:
        res = self.cli("set", "desktop.enabled", "true")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("pcbridge lock", res.stdout)

    # -- tools / restart ------------------------------------------------------------

    def test_tools_lists_searches_and_details(self) -> None:
        res = self.cli("tools")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("37 of 37 tools offered", res.stdout)
        self.assertIn("[needs desktop]", res.stdout)  # desktop control is off in this config
        res = self.cli("tools", "screenshot")
        self.assertIn("screen_capture", res.stdout)
        self.assertNotIn("job_cancel", res.stdout)
        res = self.cli("tools", "agent_run")
        self.assertIn("Parameters", res.stdout)
        self.assertIn("prompt", res.stdout)
        res = self.cli("tools", "--json", "--active")
        names = {t["name"] for t in json.loads(res.stdout)}
        self.assertIn("shell_run", names)

    def test_restart_waits_for_jobs_and_uses_systemctl(self) -> None:
        res = self.cli("restart")
        self.assertIn("systemctl --user restart pcbridge.service", self.log.read_text())
        # The stub systemd has no daemon, so the socket never answers.
        self.assertIn("does not answer", res.stdout)


if __name__ == "__main__":
    unittest.main()
