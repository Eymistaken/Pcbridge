"""The `pcbridge` CLI, run as a subprocess against a throwaway HOME.

Nothing here touches the real user's clients, units or shell: HOME and every
XDG directory point into a temp directory, and `claude`/`systemctl` are
shadowed on PATH by stubs that only record their arguments.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

CODEX = """\
model = "gpt-x"

[mcp_servers.pcbridge]
command = "/old/.venv/bin/python"
args = ["-m", "pcbridge.server", "--stdio"]

[mcp_servers.pcbridge.tools.desktop_unlock]
approval_mode = "approve"

[mcp_servers.other]
command = "other"
"""

CONFIG = """\
config_version = 2
public_url = "http://localhost:8765"
default_agent = "claude"

[auth]
password = "a-very-secret-password-123"
static_token = "a-very-secret-static-token"

[paths]
state_dir = "{state}"

[agents.claude]
command = ["true"]
"""


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="pcb-cli-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        stub = self.home / "stubs"
        stub.mkdir()
        self.log = self.home / "stub.log"
        for name in ("systemctl", "gnome-extensions", "tailscale"):
            p = stub / name
            p.write_text(f'#!/bin/sh\necho "{name} $*" >> "{self.log}"\nexit 0\n')
            p.chmod(0o755)
        # `claude mcp add -s user NAME -- CMD...` writes ~/.claude.json like the real one.
        claude = stub / "claude"
        claude.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            f"open({str(self.log)!r}, 'a').write('claude ' + ' '.join(sys.argv[1:]) + '\\n')\n"
            "a = sys.argv[1:]\n"
            "p = os.path.expanduser('~/.claude.json')\n"
            "if a[:2] == ['mcp', 'add']:\n"
            "    i = a.index('--')\n"
            "    d = json.load(open(p)) if os.path.exists(p) else {}\n"
            "    d.setdefault('mcpServers', {})[a[i - 1]] = {'type': 'stdio', 'command': a[i + 1], 'args': a[i + 2:]}\n"
            "    json.dump(d, open(p, 'w'))\n"
        )
        claude.chmod(0o755)
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "config.toml").write_text(CODEX)
        (self.home / ".config" / "Claude").mkdir(parents=True)
        (self.home / ".config" / "Claude" / "claude_desktop_config.json").write_text(
            json.dumps({"mcpServers": {"x": {"command": "x"}}, "keep": True})
        )
        state = self.home / "state"
        cfg = self.home / "config.toml"
        cfg.write_text(CONFIG.format(state=state))
        cfg.chmod(0o600)
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{stub}:/usr/bin:/bin",
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_STATE_HOME": str(self.home / ".local" / "state"),
            "XDG_DATA_HOME": str(self.home / ".local" / "share"),
            "PCBRIDGE_CONFIG": str(cfg),
            "LANG": "C.UTF-8",
        }

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "pcbridge", *args],
            capture_output=True, text=True, env=self.env, cwd=str(self.home), timeout=120,
        )

    def test_version(self) -> None:
        from pcbridge import __version__

        self.assertIn(__version__, self.cli("--version").stdout)

    def test_connect_edits_only_the_pcbridge_entries_with_backups(self) -> None:
        dry = self.cli("connect", "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertEqual((self.home / ".codex" / "config.toml").read_text(), CODEX)
        res = self.cli("connect")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        codex = tomllib.loads((self.home / ".codex" / "config.toml").read_text())
        entry = codex["mcp_servers"]["pcbridge"]
        self.assertEqual(entry["args"], ["stdio"])
        self.assertTrue(entry["command"].endswith("pcbridge"))
        self.assertEqual(entry["tools"]["desktop_unlock"]["approval_mode"], "approve")
        self.assertEqual(codex["mcp_servers"]["other"]["command"], "other")
        self.assertEqual(codex["model"], "gpt-x")
        desk = json.loads((self.home / ".config" / "Claude" / "claude_desktop_config.json").read_text())
        self.assertEqual(desk["mcpServers"]["pcbridge"]["args"], ["stdio"])
        self.assertIn("x", desk["mcpServers"])
        self.assertTrue(desk["keep"])
        self.assertIn("claude mcp add -s user pcbridge --", self.log.read_text())
        backups = list((self.home / ".local" / "state" / "pcbridge").glob("backup-*"))
        self.assertEqual(len(backups), 1)
        saved = [p.name for p in backups[0].iterdir()]
        self.assertTrue(any(n.endswith("config.toml") for n in saved))
        self.assertIn("ROLLBACK.md", saved)
        # Second run: nothing to change, nothing backed up again.
        again = self.cli("connect")
        self.assertIn("already", again.stdout)

    def test_aliases_block_is_rewritten_in_place(self) -> None:
        rc = self.home / ".bashrc"
        rc.write_text("export A=1\n# >>> pcbridge >>>\nalias bridgeac='/old/remote.sh start'\n# <<< pcbridge <<<\nexport B=2\n")
        code = (
            "from pathlib import Path; from pcbridge.cli import install as i; "
            f"b=i.Backup(); print(i.write_aliases(b, Path({str(rc)!r})))"
        )
        subprocess.run([sys.executable, "-c", code], env=self.env, check=True, cwd=str(ROOT), capture_output=True)
        text = rc.read_text()
        self.assertTrue(text.startswith("export A=1\n"))
        self.assertTrue(text.endswith("export B=2\n"))
        self.assertIn(" remote start'", text)
        self.assertIn("alias bridgekilit=", text)
        self.assertNotIn("/old/remote.sh", text)

    def test_report_redacts_secrets(self) -> None:
        res = self.cli("report", "-o", str(self.home / "r.tar.gz"))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(stat.S_IMODE((self.home / "r.tar.gz").stat().st_mode), 0o600)
        with tarfile.open(self.home / "r.tar.gz") as tar:
            blob = b"".join(tar.extractfile(m).read() for m in tar.getmembers() if m.isfile())
        self.assertNotIn(b"a-very-secret-password-123", blob)
        self.assertNotIn(b"a-very-secret-static-token", blob)
        self.assertNotIn(str(self.home).encode(), blob)

    def test_doctor_json_is_structured_and_english(self) -> None:
        res = self.cli("doctor", "--json")
        data = json.loads(res.stdout)
        self.assertIn("summary", data)
        groups = {c["group"] for c in data["checks"]}
        for g in ("install", "config", "daemon", "clients", "readiness", "agents", "desktop"):
            self.assertIn(g, groups)

    def test_a_deb_install_moves_leftover_user_units_aside(self) -> None:
        code = (
            "from pathlib import Path; from unittest import mock; "
            "from pcbridge.cli import install as i; "
            "i.USER_UNIT_DIR.mkdir(parents=True, exist_ok=True); "
            "(i.USER_UNIT_DIR / 'pcbridge.service').write_text('old'); "
            "b = i.Backup(); "
            "m = mock.patch.object(i, 'install_kind', return_value='deb'); m.start(); "
            "print(i.install_units(b), (i.USER_UNIT_DIR / 'pcbridge.service').exists(), "
            "[p.read_text() for p in b.root.iterdir() if p.name.endswith('__pcbridge.service')])"
        )
        res = subprocess.run([sys.executable, "-c", code], env=self.env, cwd=str(ROOT),
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "True False ['old']")

    def test_units_render_the_running_python(self) -> None:
        from pcbridge.cli import install as inst

        text = inst.render_unit("pcbridge.service", "/opt/x/bin/python")
        self.assertIn("ExecStart=/opt/x/bin/python -m pcbridge serve", text)
        self.assertNotIn("__PYTHON__", text)
        self.assertIn("RestartForceExitStatus=75", text)
        self.assertIn("ListenStream=%t/pcbridge/mcp.sock", inst.render_unit("pcbridge.socket"))


if __name__ == "__main__":
    unittest.main()
