"""`pcbridge connect`: register pcbridge with the local MCP clients.

Every client is pointed at `<launcher> stdio`, the relay. Each client's config
file is backed up before it is changed, and only the pcbridge entry is
touched:

    claude-code     ~/.claude.json, user scope, through `claude mcp` (a
                    project-scoped entry is silently ignored outside that
                    project, which is why user scope is required)
    codex           ~/.codex/config.toml, only `command` and `args` of
                    [mcp_servers.pcbridge]; per-tool sub-tables such as
                    approval_mode are kept
    claude-desktop  ~/.config/Claude/claude_desktop_config.json, the
                    mcpServers.pcbridge object

A client that is already registered with the same command is left alone.
Running clients keep their current server process until they restart.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import install as inst

HOME = Path.home()
CLAUDE_JSON = HOME / ".claude.json"
CODEX_TOML = HOME / ".codex" / "config.toml"
DESKTOP_JSON = HOME / ".config" / "Claude" / "claude_desktop_config.json"
CLIENTS = ("claude-code", "codex", "claude-desktop")


@dataclass
class Registration:
    client: str
    present: bool
    command: list[str]
    source: str
    note: str = ""


def current(client: str) -> Registration:
    """What the client has registered for pcbridge today (read only)."""
    if client == "claude-code":
        try:
            data = json.loads(CLAUDE_JSON.read_text())
        except (OSError, ValueError):
            return Registration(client, False, [], str(CLAUDE_JSON), "no ~/.claude.json")
        entry = (data.get("mcpServers") or {}).get("pcbridge")
        projects = [p for p, v in (data.get("projects") or {}).items() if "pcbridge" in (v.get("mcpServers") or {})]
        note = f"also registered in {len(projects)} project(s) at project scope" if projects else ""
        if not entry:
            return Registration(client, False, [], f"{CLAUDE_JSON} (user scope)", note)
        return Registration(client, True, [entry.get("command", ""), *entry.get("args", [])], f"{CLAUDE_JSON} (user scope)", note)
    if client == "codex":
        try:
            data = tomllib.loads(CODEX_TOML.read_text())
        except (OSError, ValueError):
            return Registration(client, False, [], str(CODEX_TOML), "no readable ~/.codex/config.toml")
        entry = (data.get("mcp_servers") or {}).get("pcbridge")
        if not entry or "command" not in entry:
            return Registration(client, False, [], str(CODEX_TOML))
        return Registration(client, True, [entry["command"], *entry.get("args", [])], str(CODEX_TOML))
    if client == "claude-desktop":
        try:
            data = json.loads(DESKTOP_JSON.read_text())
        except (OSError, ValueError):
            return Registration(client, False, [], str(DESKTOP_JSON), "no Claude Desktop config")
        entry = (data.get("mcpServers") or {}).get("pcbridge")
        if not entry:
            return Registration(client, False, [], str(DESKTOP_JSON))
        return Registration(client, True, [entry.get("command", ""), *entry.get("args", [])], str(DESKTOP_JSON))
    raise ValueError(client)


def client_available(client: str) -> bool:
    if client == "claude-code":
        return CLAUDE_JSON.exists() or bool(inst.run(["claude", "--version"], timeout=15).returncode == 0)
    if client == "codex":
        return CODEX_TOML.parent.exists()
    return DESKTOP_JSON.parent.exists()


# ---------------------------------------------------------------------------


def _connect_claude_code(cmd: list[str], backup: inst.Backup) -> str:
    backup.save(CLAUDE_JSON)
    probe = inst.run(["claude", "--version"], timeout=20)
    if probe.returncode != 0:
        return (
            "the `claude` command is not available; register by hand: "
            f"claude mcp add -s user pcbridge -- {shlex.join(cmd)}"
        )
    inst.run(["claude", "mcp", "remove", "pcbridge", "-s", "user"], timeout=30)
    res = inst.run(["claude", "mcp", "add", "-s", "user", "pcbridge", "--", *cmd], timeout=30)
    if res.returncode != 0:
        return f"`claude mcp add` failed: {(res.stderr or res.stdout).strip()[:200]}"
    return "registered at user scope"


_TABLE = re.compile(r"^\s*\[mcp_servers\.pcbridge\]\s*(#.*)?$", re.M)
_ANY_TABLE = re.compile(r"^\s*\[", re.M)


def _codex_text(text: str, cmd: list[str]) -> str:
    command_line = f"command = {json.dumps(cmd[0])}"
    args_line = f"args = {json.dumps(cmd[1:])}"
    m = _TABLE.search(text)
    if not m:
        sep = "" if text.endswith("\n") or not text else "\n"
        return f"{text}{sep}\n[mcp_servers.pcbridge]\n{command_line}\n{args_line}\n"
    body_start = text.index("\n", m.end()) + 1 if "\n" in text[m.end():] else len(text)
    nxt = _ANY_TABLE.search(text, body_start)
    body_end = nxt.start() if nxt else len(text)
    body = text[body_start:body_end]
    kept = [
        line for line in body.splitlines(keepends=True)
        if not re.match(r"^\s*(command|args)\s*=", line)
    ]
    new_body = f"{command_line}\n{args_line}\n" + "".join(kept)
    return text[:body_start] + new_body + text[body_end:]


def _connect_codex(cmd: list[str], backup: inst.Backup) -> str:
    text = CODEX_TOML.read_text(encoding="utf-8") if CODEX_TOML.exists() else ""
    new = _codex_text(text, cmd)
    data = tomllib.loads(new)  # never write a file Codex could not read
    entry = data["mcp_servers"]["pcbridge"]
    assert [entry["command"], *entry["args"]] == cmd
    if inst.write_if_changed(CODEX_TOML, new, backup):
        return "registered"
    return "already registered"


def _connect_desktop(cmd: list[str], backup: inst.Backup) -> str:
    data = json.loads(DESKTOP_JSON.read_text()) if DESKTOP_JSON.exists() else {}
    servers = data.setdefault("mcpServers", {})
    entry = dict(servers.get("pcbridge") or {})
    entry["command"] = cmd[0]
    entry["args"] = cmd[1:]
    servers["pcbridge"] = entry
    if inst.write_if_changed(DESKTOP_JSON, json.dumps(data, indent=2, ensure_ascii=False) + "\n", backup):
        return "registered (restart Claude Desktop to use it)"
    return "already registered"


CONNECTORS = {
    "claude-code": _connect_claude_code,
    "codex": _connect_codex,
    "claude-desktop": _connect_desktop,
}


def connect(clients: list[str], backup: inst.Backup, dry_run: bool = False) -> list[tuple[str, str, str]]:
    """Returns (client, status, detail) per client; status ok|skip|warn."""
    cmd = inst.client_command()
    out = []
    for client in clients:
        reg = current(client)
        if not client_available(client) and not reg.present:
            out.append((client, "skip", "client not installed"))
            continue
        if reg.present and reg.command == cmd:
            out.append((client, "ok", f"already runs {shlex.join(cmd)}" + (f"; {reg.note}" if reg.note else "")))
            continue
        if dry_run:
            before = shlex.join(reg.command) if reg.present else "(not registered)"
            out.append((client, "plan", f"{before} -> {shlex.join(cmd)}  [{reg.source}]"))
            continue
        try:
            detail = CONNECTORS[client](cmd, backup)
        except Exception as exc:  # noqa: BLE001 - report and carry on with the next client
            out.append((client, "warn", f"not changed: {exc}"))
            continue
        after = current(client)
        status = "ok" if after.present and after.command == cmd else "warn"
        out.append((client, status, detail + (f"; {reg.note}" if reg.note else "")))
    return out


def main(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="pcbridge connect", description="Register pcbridge with local MCP clients.")
    p.add_argument("--client", choices=[*CLIENTS, "all"], default="all")
    p.add_argument("--dry-run", action="store_true", help="Show what would change; change nothing.")
    args = p.parse_args(argv)
    clients = list(CLIENTS) if args.client == "all" else [args.client]
    backup = inst.Backup()
    inst.say(f"Registering `{shlex.join(inst.client_command())}` with MCP clients:")
    results = connect(clients, backup, args.dry_run)
    for client, status, detail in results:
        fn = {"ok": inst.ok, "warn": inst.warn, "skip": inst.ok, "plan": inst.ok}[status]
        fn(f"{client}: {detail}")
    if backup.entries:
        rb = backup.write_rollback()
        inst.say(f"Backups: {backup.root}" + (f" (see {rb.name})" if rb else ""))
    return 0 if all(s != "warn" for _, s, _ in results) else 1
