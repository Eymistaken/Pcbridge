"""`pcbridge clients`, `connect` and `disconnect` for every supported client,
run as subprocesses against a throwaway HOME.

`claude`, `agy` and `hermes` are stubs that record their arguments and write
the same files the real commands write (measured 2026-09-24 with agy 1.2.7
and Hermes Agent 0.20.6 in a throwaway HOME). Codex, Claude Desktop,
OpenCode, Pi and oh-my-pi are edited as files, so their real edits are
checked here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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
[auth]
password = "connections-test-password"
[paths]
state_dir = "{state}"
[agents.claude]
command = ["true"]
"""

# agy: `mcp add NAME -- CMD ARGS`, `mcp enable|disable|remove NAME`, on
# ~/.gemini/config/mcp_config.json with a `disabled` flag.
AGY = r'''#!{py}
import json, os, sys
log = open({log!r}, "a"); log.write("agy " + " ".join(sys.argv[1:]) + "\n")
p = os.path.expanduser("~/.gemini/config/mcp_config.json")
d = json.load(open(p)) if os.path.exists(p) else {{"mcpServers": {{}}}}
a = sys.argv[1:]
if a[:2] == ["mcp", "add"]:
    i = a.index("--")
    old = d["mcpServers"].get(a[2], {{}})
    d["mcpServers"][a[2]] = {{"command": a[i + 1], "args": a[i + 2:], "disabled": old.get("disabled", False)}}
elif a[:2] in (["mcp", "enable"], ["mcp", "disable"]):
    d["mcpServers"][a[2]]["disabled"] = a[1] == "disable"
elif a[:2] == ["mcp", "remove"]:
    d["mcpServers"].pop(a[2], None)
os.makedirs(os.path.dirname(p), exist_ok=True)
json.dump(d, open(p, "w"), indent=2)
'''

# claude: `mcp add -s user NAME -- CMD...` and `mcp remove NAME -s user` on
# ~/.claude.json, like the real one.
CLAUDE = r'''#!{py}
import json, os, sys
open({log!r}, "a").write("claude " + " ".join(sys.argv[1:]) + "\n")
a = sys.argv[1:]
p = os.path.expanduser("~/.claude.json")
d = json.load(open(p)) if os.path.exists(p) else {{}}
if a[:2] == ["mcp", "add"]:
    i = a.index("--")
    d.setdefault("mcpServers", {{}})[a[i - 1]] = {{"type": "stdio", "command": a[i + 1], "args": a[i + 2:]}}
elif a[:2] == ["mcp", "remove"]:
    d.get("mcpServers", {{}}).pop(a[2], None)
json.dump(d, open(p, "w"))
'''

# hermes: `config path`, `mcp add NAME --command C --args A...` and
# `mcp remove NAME`, both asking for a y on stdin, on ~/.hermes/config.yaml.
HERMES = r'''#!{py}
import os, sys, yaml
log = open({log!r}, "a"); log.write("hermes " + " ".join(sys.argv[1:]) + " stdin=" + repr(sys.stdin.read()) + "\n")
p = os.path.expanduser("~/.hermes/config.yaml")
a = sys.argv[1:]
if a[:2] == ["config", "path"]:
    print(p); sys.exit(0)
d = yaml.safe_load(open(p)) if os.path.exists(p) else {{}}
d = d or {{}}
servers = d.setdefault("mcp_servers", {{}})
if a[:2] == ["mcp", "add"]:
    c = a.index("--command"); r = a.index("--args")
    servers[a[2]] = {{"command": a[c + 1], "args": a[r + 1:], "enabled": True}}
elif a[:2] == ["mcp", "remove"]:
    servers.pop(a[2], None)
os.makedirs(os.path.dirname(p), exist_ok=True)
yaml.safe_dump(d, open(p, "w"))
'''


class ConnectionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="pcb-connections-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        stub = self.home / "stubs"
        stub.mkdir()
        self.log = self.home / "stub.log"
        py = sys.executable
        for name, body in (("agy", AGY), ("hermes", HERMES)):
            p = stub / name
            p.write_text(body.format(py=py, log=str(self.log)))
            p.chmod(0o755)
        p = stub / "systemctl"
        p.write_text(f'#!/bin/sh\necho "systemctl $*" >> "{self.log}"\nexit 0\n')
        p.chmod(0o755)
        p = stub / "claude"
        p.write_text(CLAUDE.format(py=py, log=str(self.log)))
        p.chmod(0o755)
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "config.toml").write_text(CODEX)
        (self.home / ".config" / "Claude").mkdir(parents=True)
        (self.home / ".config" / "Claude" / "claude_desktop_config.json").write_text(
            json.dumps({"mcpServers": {"x": {"command": "x"}}, "keep": True}))
        (self.home / ".config" / "opencode").mkdir(parents=True)
        (self.home / ".config" / "opencode" / "opencode.json").write_text(
            json.dumps({"$schema": "https://opencode.ai/config.json", "model": "m"}))
        (self.home / ".pi" / "agent").mkdir(parents=True)
        (self.home / ".pi" / "agent" / "settings.json").write_text(json.dumps({"packages": ["npm:pi-mcp-adapter"]}))
        (self.home / ".pi" / "agent" / "mcp.json").write_text(json.dumps(
            {"mcpServers": {"pcbridge": {"command": "/old/python", "args": ["-m", "pcbridge.server", "--stdio"],
                                         "lifecycle": "lazy"}}}))
        (self.home / ".omp" / "agent").mkdir(parents=True)
        cfg = self.home / "config.toml"
        cfg.write_text(CONFIG.format(state=self.home / "state"))
        cfg.chmod(0o600)
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{stub}:/usr/bin:/bin",
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_STATE_HOME": str(self.home / ".local" / "state"),
            "XDG_DATA_HOME": str(self.home / ".local" / "share"),
            "PCBRIDGE_CONFIG": str(cfg),
            "PCBRIDGE_SOCKET": str(self.home / "none.sock"),
            "PYTHONPATH": os.pathsep.join(p for p in sys.path if "site-packages" in p),
            "LANG": "C.UTF-8",
        }

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "pcbridge", *args], capture_output=True, text=True,
                              env=self.env, cwd=str(self.home), timeout=120)

    def states(self) -> dict[str, dict]:
        res = self.cli("clients", "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        return {r["client"]: r for r in json.loads(res.stdout)}

    def command(self) -> list[str]:
        return self.states()["codex"]["command"] or []

    # -----------------------------------------------------------------------

    def test_the_list_names_every_client_with_its_state(self) -> None:
        rows = self.states()
        self.assertEqual(list(rows), ["claude-code", "codex", "claude-desktop", "antigravity", "hermes",
                                      "opencode", "pi", "oh-my-pi"])
        self.assertEqual(rows["codex"]["state"], "outdated")  # the pre-2.0 command
        self.assertEqual(rows["pi"]["state"], "outdated")
        self.assertEqual(rows["opencode"]["state"], "not connected")
        self.assertEqual(rows["claude-desktop"]["state"], "not connected")
        self.assertEqual(rows["oh-my-pi"]["state"], "not connected")  # ~/.omp exists
        self.assertTrue(rows["codex"]["setup_default"])
        self.assertFalse(rows["hermes"]["setup_default"])
        text = self.cli("clients").stdout
        self.assertIn("pcbridge connect codex", text)

    def test_a_bare_connect_still_only_touches_the_setup_three(self) -> None:
        res = self.cli("connect")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        rows = self.states()
        self.assertEqual(rows["codex"]["state"], "connected")
        self.assertEqual(rows["claude-desktop"]["state"], "connected")
        self.assertEqual(rows["pi"]["state"], "outdated")
        self.assertEqual(rows["opencode"]["state"], "not connected")
        self.assertNotIn("agy", self.log.read_text())

    def test_file_clients_connect_and_switch_off_keeping_their_settings(self) -> None:
        res = self.cli("connect", "codex", "opencode", "pi", "oh-my-pi")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        cmd = self.states()["codex"]["command"]
        self.assertTrue(cmd[0].endswith("pcbridge") and cmd[1:] == ["stdio"], cmd)

        codex = tomllib.loads((self.home / ".codex" / "config.toml").read_text())
        self.assertEqual(codex["mcp_servers"]["pcbridge"]["tools"]["desktop_unlock"]["approval_mode"], "approve")
        self.assertEqual(codex["model"], "gpt-x")
        oc = json.loads((self.home / ".config" / "opencode" / "opencode.json").read_text())
        self.assertEqual(oc["mcp"]["pcbridge"], {"type": "local", "command": cmd, "enabled": True})
        self.assertEqual(oc["model"], "m")
        pi = json.loads((self.home / ".pi" / "agent" / "mcp.json").read_text())["mcpServers"]["pcbridge"]
        self.assertEqual((pi["command"], pi["args"], pi["lifecycle"]), (cmd[0], cmd[1:], "lazy"))
        omp = json.loads((self.home / ".omp" / "agent" / "mcp.json").read_text())["mcpServers"]["pcbridge"]
        self.assertEqual(omp, {"type": "stdio", "command": cmd[0], "args": cmd[1:]})

        res = self.cli("disconnect", "codex", "opencode", "pi", "oh-my-pi")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        rows = self.states()
        for c in ("codex", "opencode", "pi", "oh-my-pi"):
            self.assertEqual(rows[c]["state"], "switched off", c)
        codex_text = (self.home / ".codex" / "config.toml").read_text()
        codex = tomllib.loads(codex_text)
        self.assertIs(codex["mcp_servers"]["pcbridge"]["enabled"], False)
        self.assertEqual(codex["mcp_servers"]["pcbridge"]["tools"]["desktop_unlock"]["approval_mode"], "approve")
        # `enabled` belongs to [mcp_servers.pcbridge], not to its tools sub-table.
        self.assertLess(codex_text.index("enabled = false"), codex_text.index("[mcp_servers.pcbridge.tools"))
        self.assertIs(json.loads((self.home / ".pi" / "agent" / "mcp.json").read_text())
                      ["mcpServers"]["pcbridge"]["disabled"], True)

        # On again: the switched-off entries come back on with their settings.
        self.cli("connect", "codex", "pi")
        rows = self.states()
        self.assertEqual((rows["codex"]["state"], rows["pi"]["state"]), ("connected", "connected"))
        self.assertNotIn("enabled", tomllib.loads((self.home / ".codex" / "config.toml").read_text())
                         ["mcp_servers"]["pcbridge"])

    def test_claude_code_through_its_own_cli(self) -> None:
        self.cli("connect", "claude-code")
        self.assertEqual(self.states()["claude-code"]["state"], "connected")
        self.cli("disconnect", "claude-code")
        self.assertIn("claude mcp remove pcbridge -s user", self.log.read_text())
        self.assertEqual(self.states()["claude-code"]["state"], "not connected")

    def test_claude_desktop_is_removed_on_disconnect(self) -> None:
        self.cli("connect", "claude-desktop")
        self.cli("disconnect", "claude-desktop")
        desk = json.loads((self.home / ".config" / "Claude" / "claude_desktop_config.json").read_text())
        self.assertNotIn("pcbridge", desk["mcpServers"])
        self.assertIn("x", desk["mcpServers"])
        self.assertTrue(desk["keep"])

    def test_antigravity_through_its_own_cli(self) -> None:
        res = self.cli("connect", "antigravity")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.states()["antigravity"]["state"], "connected")
        self.assertRegex(self.log.read_text(), r"agy mcp add pcbridge -- \S+pcbridge stdio")
        self.cli("disconnect", "antigravity")
        self.assertIn("agy mcp disable pcbridge", self.log.read_text())
        self.assertEqual(self.states()["antigravity"]["state"], "switched off")
        self.cli("connect", "antigravity")
        self.assertIn("agy mcp enable pcbridge", self.log.read_text())
        self.assertEqual(self.states()["antigravity"]["state"], "connected")

    def test_hermes_through_its_own_cli_answering_its_question(self) -> None:
        res = self.cli("connect", "hermes")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.states()["hermes"]["state"], "connected")
        log = self.log.read_text()
        self.assertRegex(log, r"hermes mcp add pcbridge --command \S+pcbridge --args stdio stdin='y\\n'")
        self.cli("disconnect", "hermes")
        self.assertIn("hermes mcp remove pcbridge stdin='y\\n'", self.log.read_text())
        self.assertEqual(self.states()["hermes"]["state"], "not connected")

    def test_every_change_is_backed_up_and_a_dry_run_changes_nothing(self) -> None:
        before = (self.home / ".codex" / "config.toml").read_text()
        res = self.cli("connect", "--dry-run", "--client", "all")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual((self.home / ".codex" / "config.toml").read_text(), before)
        self.cli("connect", "codex", "pi")
        backups = list((self.home / ".local" / "state" / "pcbridge").glob("backup-*"))
        saved = [p.name for b in backups for p in b.iterdir()]
        self.assertTrue(any(n.endswith(".codex__config.toml") for n in saved), saved)
        self.assertTrue(any(n.endswith("agent__mcp.json") for n in saved), saved)

    def test_mistakes_are_refused(self) -> None:
        self.assertNotEqual(self.cli("connect", "nosuch").returncode, 0)
        self.assertNotEqual(self.cli("disconnect").returncode, 0)  # a name is required

    def test_an_opencode_jsonc_with_comments_is_not_rewritten(self) -> None:
        base = self.home / ".config" / "opencode"
        (base / "opencode.json").unlink()
        (base / "opencode.jsonc").write_text('// mine\n{ "model": "m" }\n')
        res = self.cli("connect", "opencode")
        self.assertIn("has comments", res.stdout)
        self.assertEqual((base / "opencode.jsonc").read_text(), '// mine\n{ "model": "m" }\n')

    def test_uninstall_removes_every_entry(self) -> None:
        self.cli("connect", "codex", "opencode", "pi", "oh-my-pi", "antigravity", "hermes", "claude-desktop")
        res = self.cli("uninstall", "--yes")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        rows = self.states()
        for c in ("codex", "opencode", "pi", "oh-my-pi", "antigravity", "hermes", "claude-desktop"):
            self.assertIn(rows[c]["state"], ("not connected", "not installed"), c)
        codex = tomllib.loads((self.home / ".codex" / "config.toml").read_text())
        self.assertNotIn("pcbridge", codex["mcp_servers"])
        self.assertEqual(codex["mcp_servers"]["other"]["command"], "other")


if __name__ == "__main__":
    unittest.main()
