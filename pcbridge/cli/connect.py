"""Which local agents and apps can use pcbridge's tools: `pcbridge clients`,
`pcbridge connect` and `pcbridge disconnect`, and the terminal UI's
Connections tab.

Every client is pointed at `<launcher> stdio`, the relay. Each client's config
file is backed up before it is changed, and only the pcbridge entry is
touched. Where a client has its own command for this, that command is used:

    claude-code     ~/.claude.json, user scope, through `claude mcp` (a
                    project-scoped entry is silently ignored outside that
                    project, which is why user scope is required)
    codex           $CODEX_HOME/config.toml (~/.codex), only `command`,
                    `args` and `enabled` of [mcp_servers.pcbridge]; per-tool
                    sub-tables such as approval_mode are kept
    claude-desktop  ~/.config/Claude/claude_desktop_config.json, the
                    mcpServers.pcbridge object
    antigravity     ~/.gemini/config/mcp_config.json, through `agy mcp`
                    (measured 2026-09-24 with agy 1.2.7: that is the file it
                    writes)
    hermes          the active profile's config.yaml (`hermes config path`),
                    through `hermes mcp add/remove`, which ask for a
                    confirmation that is answered for them
    opencode        ~/.config/opencode/opencode.json, mcp.pcbridge with
                    type "local" (the shape of @opencode-ai/sdk's
                    McpLocalConfig, OpenCode 1.18)
    pi              $PI_CODING_AGENT_DIR/mcp.json (~/.pi/agent), read by the
                    pi-mcp-adapter extension (2.32); Pi itself has no MCP
    oh-my-pi        ~/.omp/agent/mcp.json, the default profile's user file
                    (from oh-my-pi's docs/mcp-config.md; not tried on a real
                    install)

Disconnecting keeps the entry, switched off, where the client can switch an
entry off (codex, antigravity, opencode, pi, oh-my-pi), so its per-client
settings survive; elsewhere it removes the entry. `pcbridge setup` connects
only the first three; the others are connected on request.

A client that already runs the same command is left alone. Running clients
keep their current server process until they restart.
"""

from __future__ import annotations

import json
import os
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..executables import find_executable
from . import install as inst

HOME = Path.home()
CLAUDE_JSON = HOME / ".claude.json"
CODEX_TOML = Path(os.environ.get("CODEX_HOME") or HOME / ".codex") / "config.toml"
DESKTOP_JSON = HOME / ".config" / "Claude" / "claude_desktop_config.json"
AGY_JSON = HOME / ".gemini" / "config" / "mcp_config.json"
OMP_JSON = HOME / ".omp" / "agent" / "mcp.json"

# What `pcbridge setup` and a bare `pcbridge connect` register.
CLIENTS = ("claude-code", "codex", "claude-desktop")
# Every client pcbridge can connect, in the order they are listed.
ALL_CLIENTS = ("claude-code", "codex", "claude-desktop", "antigravity", "hermes",
               "opencode", "pi", "oh-my-pi")
NAMES = {
    "claude-code": "Claude Code",
    "codex": "Codex",
    "claude-desktop": "Claude Desktop",
    "antigravity": "Antigravity CLI",
    "hermes": "Hermes Agent",
    "opencode": "OpenCode",
    "pi": "Pi",
    "oh-my-pi": "oh-my-pi",
}
BINARIES = {
    "claude-code": "claude", "codex": "codex", "antigravity": "agy", "hermes": "hermes",
    "opencode": "opencode", "pi": "pi", "oh-my-pi": "omp",
}
SERVER = "pcbridge"

# Connection states, as `pcbridge clients` and the UI show them.
CONNECTED = "connected"          # enabled and running this pcbridge's command
OUTDATED = "outdated"            # enabled but running another command
SWITCHED_OFF = "switched off"    # the entry is kept but disabled
NOT_CONNECTED = "not connected"
NOT_INSTALLED = "not installed"


@dataclass
class Registration:
    client: str
    present: bool
    command: list[str]
    source: str
    note: str = ""
    enabled: bool = True


def _xdg_config() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")


def opencode_json() -> Path:
    base = _xdg_config() / "opencode"
    json_path = base / "opencode.json"
    if json_path.exists() or not (base / "opencode.jsonc").exists():
        return json_path
    return base / "opencode.jsonc"


def pi_dir() -> Path:
    return Path(os.path.expanduser(os.environ.get("PI_CODING_AGENT_DIR") or HOME / ".pi" / "agent"))


def hermes_config() -> Path:
    """The active profile's config.yaml, as `hermes config path` reports it."""
    exe = find_executable("hermes")
    if exe is not None:
        res = inst.run([str(exe), "config", "path"], timeout=20)
        line = (res.stdout or "").strip().splitlines()
        if res.returncode == 0 and line and line[-1].endswith((".yaml", ".yml")):
            return Path(line[-1])
    return Path(os.environ.get("HERMES_HOME") or HOME / ".hermes") / "config.yaml"


def config_path(client: str) -> Path:
    return {
        "claude-code": lambda: CLAUDE_JSON,
        "codex": lambda: CODEX_TOML,
        "claude-desktop": lambda: DESKTOP_JSON,
        "antigravity": lambda: AGY_JSON,
        "hermes": hermes_config,
        "opencode": opencode_json,
        "pi": lambda: pi_dir() / "mcp.json",
        "oh-my-pi": lambda: OMP_JSON,
    }[client]()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        if path.suffix == ".jsonc":
            raise ValueError(f"{path} has comments, which pcbridge does not rewrite; "
                             "add the pcbridge entry by hand") from None
        raise ValueError(f"{path} is not valid JSON: {exc}") from None
    return data if isinstance(data, dict) else {}


def _cmd(entry: dict) -> list[str]:
    command = entry.get("command", "")
    if isinstance(command, list):  # OpenCode keeps command and arguments together
        return [str(x) for x in command]
    return [str(command), *[str(x) for x in entry.get("args") or []]]


def _from_servers(client: str, path: Path, servers: Any, enabled_of: Callable[[dict], bool],
                  note: str = "") -> Registration:
    entry = (servers or {}).get(SERVER) if isinstance(servers, dict) else None
    if not isinstance(entry, dict):
        return Registration(client, False, [], str(path), note)
    return Registration(client, True, _cmd(entry), str(path), note, enabled_of(entry))


def current(client: str) -> Registration:
    """What the client has registered for pcbridge today (read only)."""
    if client == "claude-code":
        try:
            data = json.loads(CLAUDE_JSON.read_text())
        except (OSError, ValueError):
            return Registration(client, False, [], str(CLAUDE_JSON), "no ~/.claude.json")
        entry = (data.get("mcpServers") or {}).get(SERVER)
        projects = [p for p, v in (data.get("projects") or {}).items() if SERVER in (v.get("mcpServers") or {})]
        note = f"also registered in {len(projects)} project(s) at project scope" if projects else ""
        if not entry:
            return Registration(client, False, [], f"{CLAUDE_JSON} (user scope)", note)
        return Registration(client, True, _cmd(entry), f"{CLAUDE_JSON} (user scope)", note)
    if client == "codex":
        try:
            data = tomllib.loads(CODEX_TOML.read_text())
        except (OSError, ValueError):
            return Registration(client, False, [], str(CODEX_TOML), f"no readable {CODEX_TOML}")
        entry = (data.get("mcp_servers") or {}).get(SERVER)
        if not entry or "command" not in entry:
            return Registration(client, False, [], str(CODEX_TOML))
        return Registration(client, True, _cmd(entry), str(CODEX_TOML), "", entry.get("enabled", True) is not False)
    path = config_path(client)
    try:
        if client == "hermes":
            import yaml

            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except FileNotFoundError:
                data = {}
            return _from_servers(client, path, data.get("mcp_servers"),
                                 lambda e: e.get("enabled", True) is not False)
        data = _json(path)
    except (OSError, ValueError) as exc:
        return Registration(client, False, [], str(path), f"cannot read {path.name}: {exc}")
    if client == "claude-desktop":
        return _from_servers(client, path, data.get("mcpServers"), lambda e: True)
    if client == "antigravity":
        return _from_servers(client, path, data.get("mcpServers"), lambda e: e.get("disabled") is not True)
    if client == "opencode":
        return _from_servers(client, path, data.get("mcp"), lambda e: e.get("enabled", True) is not False)
    if client == "pi":
        note = "" if pi_adapter_installed() else "needs the pi-mcp-adapter extension: pi install npm:pi-mcp-adapter"
        return _from_servers(client, path, data.get("mcpServers"), lambda e: e.get("disabled") is not True, note)
    if client == "oh-my-pi":
        denied = SERVER in (data.get("disabledServers") or [])
        return _from_servers(client, path, data.get("mcpServers"),
                             lambda e: e.get("enabled", True) is not False and not denied)
    raise ValueError(client)


def pi_adapter_installed() -> bool:
    packages = _json(pi_dir() / "settings.json").get("packages") or []
    return any("pi-mcp-adapter" in str(p) for p in packages)


def client_available(client: str) -> bool:
    if client == "claude-code":
        return CLAUDE_JSON.exists() or bool(inst.run(["claude", "--version"], timeout=15).returncode == 0)
    if client == "codex":
        return CODEX_TOML.parent.exists()
    if client == "claude-desktop":
        return DESKTOP_JSON.parent.exists()
    if find_executable(BINARIES[client]) is not None:
        return True
    return config_path(client).parent.exists() and client not in ("antigravity", "hermes")


def state(client: str, cmd: list[str] | None = None) -> tuple[str, Registration]:
    """(one of the states above, the registration)."""
    cmd = cmd or inst.client_command()
    reg = current(client)
    if not reg.present:
        return (NOT_CONNECTED if client_available(client) else NOT_INSTALLED), reg
    if not reg.enabled:
        return SWITCHED_OFF, reg
    return (CONNECTED if reg.command == cmd else OUTDATED), reg


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: dict, backup: inst.Backup) -> bool:
    return inst.write_if_changed(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n", backup)


def _run(client: str, args: list[str], stdin: str | None = None, timeout: float = 90) -> str:
    """Run a client's own CLI; return "" on success or the reason it failed."""
    exe = find_executable(BINARIES[client])
    if exe is None:
        return f"the `{BINARIES[client]}` command is not available"
    res = inst.run([str(exe), *args], timeout=timeout, input=stdin)
    if res.returncode != 0:
        return f"`{BINARIES[client]} {' '.join(args[:3])}` failed: {(res.stderr or res.stdout).strip()[-200:]}"
    return ""


def _last_plain_key(table: Any) -> str | None:
    from tomlkit.items import AoT, Table

    last = None
    for key, item in table.value.body:
        if key is not None and not isinstance(item, (Table, AoT)):
            last = key.key
    return last


def _toml_set(table: Any, key: str, value: Any) -> None:
    """Set a plain key without landing it inside one of the table's sub-tables."""
    import tomlkit

    if key in table:
        table[key] = value
        return
    after = _last_plain_key(table)
    if after is None:
        if table.value.body:
            table.value._insert_at(0, key, tomlkit.item(value))
        else:
            table.append(key, value)
    else:
        table.value._insert_after(after, key, tomlkit.item(value))


def _codex_text(text: str, cmd: list[str] | None = None, enabled: bool | None = None) -> str:
    """Codex's config with pcbridge's entry changed; everything else kept."""
    import tomlkit

    doc = tomlkit.parse(text)
    servers = doc.get("mcp_servers")
    if servers is None:
        if cmd is None:
            return text
        doc["mcp_servers"] = tomlkit.table(is_super_table=True)
        servers = doc["mcp_servers"]
    if SERVER not in servers:
        if cmd is None:
            return text
        servers[SERVER] = tomlkit.table()
    entry = servers[SERVER]
    if cmd is not None:
        _toml_set(entry, "command", cmd[0])
        _toml_set(entry, "args", cmd[1:])
    if enabled is True and "enabled" in entry:
        del entry["enabled"]
    elif enabled is False:
        _toml_set(entry, "enabled", False)
    out = doc.as_string()
    tomllib.loads(out)  # never write a file Codex could not read
    return out


def _connect_claude_code(cmd: list[str], backup: inst.Backup) -> str:
    backup.save(CLAUDE_JSON)
    probe = inst.run(["claude", "--version"], timeout=20)
    if probe.returncode != 0:
        return (
            "the `claude` command is not available; register by hand: "
            f"claude mcp add -s user pcbridge -- {shlex.join(cmd)}"
        )
    inst.run(["claude", "mcp", "remove", SERVER, "-s", "user"], timeout=30)
    res = inst.run(["claude", "mcp", "add", "-s", "user", SERVER, "--", *cmd], timeout=30)
    if res.returncode != 0:
        return f"`claude mcp add` failed: {(res.stderr or res.stdout).strip()[:200]}"
    return "registered at user scope"


def _disconnect_claude_code(backup: inst.Backup) -> str:
    backup.save(CLAUDE_JSON)
    res = inst.run(["claude", "mcp", "remove", SERVER, "-s", "user"], timeout=30)
    if res.returncode != 0:
        return f"`claude mcp remove` failed: {(res.stderr or res.stdout).strip()[:200]}"
    return "removed from user scope"


def _connect_codex(cmd: list[str], backup: inst.Backup) -> str:
    text = CODEX_TOML.read_text(encoding="utf-8") if CODEX_TOML.exists() else ""
    new = _codex_text(text, cmd, enabled=True)
    entry = tomllib.loads(new)["mcp_servers"][SERVER]
    assert [entry["command"], *entry["args"]] == cmd
    return "registered" if inst.write_if_changed(CODEX_TOML, new, backup) else "already registered"


def _disconnect_codex(backup: inst.Backup) -> str:
    text = CODEX_TOML.read_text(encoding="utf-8")
    inst.write_if_changed(CODEX_TOML, _codex_text(text, enabled=False), backup)
    return "switched off (enabled = false); its per-tool settings are kept"


def _connect_desktop(cmd: list[str], backup: inst.Backup) -> str:
    data = _json(DESKTOP_JSON)
    servers = data.setdefault("mcpServers", {})
    entry = dict(servers.get(SERVER) or {})
    entry["command"] = cmd[0]
    entry["args"] = cmd[1:]
    servers[SERVER] = entry
    if _write_json(DESKTOP_JSON, data, backup):
        return "registered (restart Claude Desktop to use it)"
    return "already registered"


def _disconnect_desktop(backup: inst.Backup) -> str:
    data = _json(DESKTOP_JSON)
    (data.get("mcpServers") or {}).pop(SERVER, None)
    _write_json(DESKTOP_JSON, data, backup)
    return "removed (restart Claude Desktop)"


def _connect_agy(cmd: list[str], backup: inst.Backup) -> str:
    backup.save(AGY_JSON)
    why = _run("antigravity", ["mcp", "add", SERVER, "--", *cmd])
    if not why and current("antigravity").enabled is False:
        why = _run("antigravity", ["mcp", "enable", SERVER])
    return why or "registered"


def _disconnect_agy(backup: inst.Backup) -> str:
    backup.save(AGY_JSON)
    return _run("antigravity", ["mcp", "disable", SERVER]) or "switched off (agy mcp disable)"


def _connect_hermes(cmd: list[str], backup: inst.Backup) -> str:
    backup.save(hermes_config())
    # `hermes mcp add` connects to the server, lists its tools and asks
    # whether to enable them all; the answer is given here.
    why = _run("hermes", ["mcp", "add", SERVER, "--command", cmd[0], "--args", *cmd[1:]], stdin="y\n")
    return why or "registered with all tools enabled"


def _disconnect_hermes(backup: inst.Backup) -> str:
    backup.save(hermes_config())
    return _run("hermes", ["mcp", "remove", SERVER], stdin="y\n") or "removed"


def _connect_opencode(cmd: list[str], backup: inst.Backup) -> str:
    path = opencode_json()
    data = _json(path) if path.exists() else {"$schema": "https://opencode.ai/config.json"}
    servers = data.setdefault("mcp", {})
    entry = dict(servers.get(SERVER) or {})
    entry.update(type="local", command=list(cmd), enabled=True)
    servers[SERVER] = entry
    return "registered" if _write_json(path, data, backup) else "already registered"


def _disconnect_opencode(backup: inst.Backup) -> str:
    path = opencode_json()
    data = _json(path)
    entry = (data.get("mcp") or {}).get(SERVER)
    if isinstance(entry, dict):
        entry["enabled"] = False
        _write_json(path, data, backup)
    return "switched off (enabled: false)"


def _connect_pi(cmd: list[str], backup: inst.Backup) -> str:
    path = pi_dir() / "mcp.json"
    data = _json(path)
    servers = data.setdefault("mcpServers", {})
    entry = dict(servers.get(SERVER) or {})
    entry.update(command=cmd[0], args=cmd[1:])
    entry.pop("disabled", None)
    servers[SERVER] = entry
    detail = "registered" if _write_json(path, data, backup) else "already registered"
    if not pi_adapter_installed():
        detail += "; Pi reads it only with the pi-mcp-adapter extension: pi install npm:pi-mcp-adapter"
    return detail


def _disconnect_pi(backup: inst.Backup) -> str:
    path = pi_dir() / "mcp.json"
    data = _json(path)
    entry = (data.get("mcpServers") or {}).get(SERVER)
    if isinstance(entry, dict):
        entry["disabled"] = True
        _write_json(path, data, backup)
    return "switched off (disabled: true); run /reload in a running Pi"


def _connect_omp(cmd: list[str], backup: inst.Backup) -> str:
    data = _json(OMP_JSON)
    servers = data.setdefault("mcpServers", {})
    entry = dict(servers.get(SERVER) or {})
    entry.update(type="stdio", command=cmd[0], args=cmd[1:])
    entry.pop("enabled", None)
    servers[SERVER] = entry
    if SERVER in (data.get("disabledServers") or []):
        data["disabledServers"] = [s for s in data["disabledServers"] if s != SERVER]
    return "registered" if _write_json(OMP_JSON, data, backup) else "already registered"


def _disconnect_omp(backup: inst.Backup) -> str:
    data = _json(OMP_JSON)
    entry = (data.get("mcpServers") or {}).get(SERVER)
    if isinstance(entry, dict):
        entry["enabled"] = False
        _write_json(OMP_JSON, data, backup)
    return "switched off (enabled: false)"


def _codex_remove(backup: inst.Backup) -> str:
    import tomlkit

    doc = tomlkit.parse(CODEX_TOML.read_text(encoding="utf-8"))
    servers = doc.get("mcp_servers")
    if servers is not None and SERVER in servers:
        del servers[SERVER]
        out = doc.as_string()
        tomllib.loads(out)
        inst.write_if_changed(CODEX_TOML, out, backup)
    return "removed"


def _json_remove(path: Path, section: str, backup: inst.Backup) -> str:
    data = _json(path)
    if isinstance(data.get(section), dict) and SERVER in data[section]:
        del data[section][SERVER]
        _write_json(path, data, backup)
    return "removed"


def remove_entry(client: str, backup: inst.Backup) -> str:
    """Remove pcbridge's entry completely (for `pcbridge uninstall`)."""
    if client == "codex":
        return _codex_remove(backup)
    if client == "antigravity":
        backup.save(AGY_JSON)
        return _run("antigravity", ["mcp", "remove", SERVER]) or "removed"
    if client == "opencode":
        return _json_remove(opencode_json(), "mcp", backup)
    if client == "pi":
        return _json_remove(pi_dir() / "mcp.json", "mcpServers", backup)
    if client == "oh-my-pi":
        return _json_remove(OMP_JSON, "mcpServers", backup)
    return DISCONNECTORS[client](backup)  # claude-code, claude-desktop, hermes remove anyway


CONNECTORS: dict[str, Callable[[list[str], inst.Backup], str]] = {
    "claude-code": _connect_claude_code,
    "codex": _connect_codex,
    "claude-desktop": _connect_desktop,
    "antigravity": _connect_agy,
    "hermes": _connect_hermes,
    "opencode": _connect_opencode,
    "pi": _connect_pi,
    "oh-my-pi": _connect_omp,
}
DISCONNECTORS: dict[str, Callable[[inst.Backup], str]] = {
    "claude-code": _disconnect_claude_code,
    "codex": _disconnect_codex,
    "claude-desktop": _disconnect_desktop,
    "antigravity": _disconnect_agy,
    "hermes": _disconnect_hermes,
    "opencode": _disconnect_opencode,
    "pi": _disconnect_pi,
    "oh-my-pi": _disconnect_omp,
}


def connect(clients: list[str], backup: inst.Backup, dry_run: bool = False) -> list[tuple[str, str, str]]:
    """Returns (client, status, detail) per client; status ok|skip|warn|plan."""
    cmd = inst.client_command()
    out = []
    for client in clients:
        reg = current(client)
        if not client_available(client) and not reg.present:
            out.append((client, "skip", "client not installed"))
            continue
        if reg.present and reg.enabled and reg.command == cmd:
            out.append((client, "ok", f"already runs {shlex.join(cmd)}" + (f"; {reg.note}" if reg.note else "")))
            continue
        if dry_run:
            before = shlex.join(reg.command) if reg.present else "(not registered)"
            if reg.present and not reg.enabled:
                before += " (switched off)"
            out.append((client, "plan", f"{before} -> {shlex.join(cmd)}  [{reg.source}]"))
            continue
        try:
            detail = CONNECTORS[client](cmd, backup)
        except Exception as exc:  # noqa: BLE001 - report and carry on with the next client
            out.append((client, "warn", f"not changed: {exc}"))
            continue
        after = current(client)
        status = "ok" if after.present and after.enabled and after.command == cmd else "warn"
        out.append((client, status, detail + (f"; {reg.note}" if reg.note and reg.note not in detail else "")))
    return out


def disconnect(clients: list[str], backup: inst.Backup, dry_run: bool = False) -> list[tuple[str, str, str]]:
    """Stop each client from using pcbridge. Returns (client, status, detail)."""
    out = []
    for client in clients:
        reg = current(client)
        if not reg.present or not reg.enabled:
            out.append((client, "ok", "not connected" if not reg.present else "already switched off"))
            continue
        if dry_run:
            out.append((client, "plan", f"{shlex.join(reg.command)} -> off  [{reg.source}]"))
            continue
        try:
            detail = DISCONNECTORS[client](backup)
        except Exception as exc:  # noqa: BLE001
            out.append((client, "warn", f"not changed: {exc}"))
            continue
        after = current(client)
        status = "ok" if not after.present or not after.enabled else "warn"
        out.append((client, status, detail))
    return out


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _pick(names: list[str], every: bool, default: tuple[str, ...]) -> list[str]:
    if every:
        return list(ALL_CLIENTS)
    return list(dict.fromkeys(names)) or list(default)


def _report(results: list[tuple[str, str, str]], backup: inst.Backup) -> int:
    for client, status, detail in results:
        fn = {"ok": inst.ok, "warn": inst.warn, "skip": inst.ok, "plan": inst.ok}[status]
        fn(f"{client}: {detail}")
    if backup.entries:
        rb = backup.write_rollback()
        inst.say(f"Backups: {backup.root}" + (f" (see {rb.name})" if rb else ""))
    if any(s == "ok" for _, s, _ in results):
        inst.say("A client that is running picks the change up when it restarts.")
    return 0 if all(s != "warn" for _, s, _ in results) else 1


def _parser(prog: str, description: str):
    import argparse

    p = argparse.ArgumentParser(prog=prog, description=description)
    p.add_argument("clients", nargs="*", metavar="CLIENT",
                   help=f"One or more of: {', '.join(ALL_CLIENTS)}.")
    p.add_argument("--client", action="append", default=[], choices=[*ALL_CLIENTS, "all"],
                   help="The same as naming it; `all` means every supported client.")
    p.add_argument("--dry-run", action="store_true", help="Show what would change; change nothing.")
    return p


def _chosen(p, args) -> tuple[list[str], bool]:
    names = [c for c in [*args.clients, *args.client] if c != "all"]
    bad = [n for n in names if n not in ALL_CLIENTS]
    if bad:
        p.error(f"unknown client {bad[0]!r}; choose from {', '.join(ALL_CLIENTS)}")
    return names, "all" in args.client or "all" in args.clients


def main(argv: list[str]) -> int:
    p = _parser("pcbridge connect",
                "Let local agents and apps use pcbridge's tools. Without a name: "
                + ", ".join(CLIENTS) + ", as `pcbridge setup` does.")
    args = p.parse_args(argv)
    names, every = _chosen(p, args)
    clients = _pick(names, every, CLIENTS)
    backup = inst.Backup()
    inst.say(f"Registering `{shlex.join(inst.client_command())}` with MCP clients:")
    return _report(connect(clients, backup, args.dry_run), backup)


def disconnect_main(argv: list[str]) -> int:
    p = _parser("pcbridge disconnect",
                "Stop local agents and apps from using pcbridge. Where a client can switch an "
                "entry off, the entry is kept, switched off; otherwise it is removed. Every "
                "changed file is backed up first.")
    args = p.parse_args(argv)
    names, every = _chosen(p, args)
    if not names and not every:
        p.error("name the clients to disconnect, or use --client all")
    backup = inst.Backup()
    return _report(disconnect(_pick(names, every, ()), backup, args.dry_run), backup)


def clients_main(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="pcbridge clients",
                                description="Which local agents and apps can use pcbridge's tools.")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    cmd = inst.client_command()
    rows = []
    for client in ALL_CLIENTS:
        st, reg = state(client, cmd)
        rows.append({"client": client, "name": NAMES[client], "state": st,
                     "command": reg.command if reg.present else None,
                     "config": reg.source, "note": reg.note, "setup_default": client in CLIENTS})
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    inst.say(f"pcbridge runs as `{shlex.join(cmd)}` for its clients.")
    width = max(len(c) for c in ALL_CLIENTS) + 2
    for r in rows:
        line = f"  {r['client'].ljust(width)}{r['state'].ljust(15)}{r['config']}"
        inst.say(line)
        if r["state"] == OUTDATED:
            inst.say(" " * (width + 2) + f"runs {shlex.join(r['command'])}; `pcbridge connect {r['client']}` updates it")
        if r["note"]:
            inst.say(" " * (width + 2) + r["note"])
    inst.say("`pcbridge connect NAME` and `pcbridge disconnect NAME` switch one on or off.")
    return 0
