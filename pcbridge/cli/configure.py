"""Direct commands for settings and tools: `pcbridge list`, `settings`, `get`,
`set`, `reset`, `tools` and `restart`. They do what the terminal UI does,
without opening it.

Secrets (the password and the static token) are never printed, and a new
secret is never taken from the command line, where it would land in the
shell history: `set` prompts for it, reads it from stdin or generates it.
"""

from __future__ import annotations

import argparse
import getpass
import json
import secrets
import shutil
import sys
import textwrap
from typing import Any

from .. import __version__
from .. import settings as S
from . import install as inst


def _editor() -> S.ConfigEditor:
    try:
        return S.ConfigEditor()
    except (S.SettingsError, SystemExit) as exc:
        raise SystemExit(f"pcbridge: {exc}") from None


def _width() -> int:
    return max(60, min(shutil.get_terminal_size((100, 24)).columns, 120))


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m" if inst._COLOR else text


def _dim(text: str) -> str:
    return f"\033[2m{text}\033[0m" if inst._COLOR else text


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def list_commands(argv: list[str]) -> int:
    argparse.ArgumentParser(prog="pcbridge list",
                            description="Show every pcbridge command.").parse_args(argv)
    from .main import COMMANDS

    width = _width()
    usage_w = max(len(c[2]) for c in COMMANDS) + 2
    print(f"pcbridge {__version__}. Run `pcbridge` alone in a terminal for the terminal UI.")
    group = None
    for grp, _name, usage, text in COMMANDS:
        if grp != group:
            print()
            print(_bold(grp))
            group = grp
        lines = textwrap.wrap(text, max(30, width - usage_w - 4)) or [""]
        print(f"  {usage.ljust(usage_w)}{lines[0]}")
        for line in lines[1:]:
            print(" " * (usage_w + 2) + line)
    print()
    print("Every command takes --help.")
    return 0


# ---------------------------------------------------------------------------
# settings / get / set / reset
# ---------------------------------------------------------------------------


def _entry(ed: S.ConfigEditor, s: S.Setting) -> dict[str, Any]:
    value = ed.value(s.key)
    out: dict[str, Any] = {
        "key": s.key,
        "section": s.section_title,
        "kind": s.kind,
        "changed": ed.is_set(s.key) and value != ed.default(s.key),
        "restart": s.restart,
        "description": s.help,
    }
    if s.secret:
        out["set"] = bool(value)
    else:
        out["value"] = value
        out["default"] = ed.default(s.key)
    if s.choices:
        out["choices"] = list(s.choices)
    env = ed.env_override(s.key)
    if env:
        out["overridden_by"] = env
    return out


def settings_cmd(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="pcbridge settings",
                                description="List every setting with its current value.")
    p.add_argument("filter", nargs="*",
                   help="Show only settings whose key or description contains every word.")
    p.add_argument("--changed", action="store_true", help="Only settings that differ from the default.")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    ed = _editor()
    words = " ".join(args.filter).lower().split()
    rows = []
    for s in ed.settings:
        hay = f"{s.key} {s.help}".lower()
        if words and not all(w in hay for w in words):
            continue
        e = _entry(ed, s)
        if args.changed and not e["changed"]:
            continue
        rows.append((s, e))
    if args.json:
        print(json.dumps([e for _, e in rows], indent=2, default=str))
        return 0
    if not rows:
        print("No setting matches." if words or args.changed else "No settings.")
        return 1 if words else 0
    key_w = min(max(len(s.key) for s, _ in rows) + 2, 44)
    val_w = max(20, _width() - key_w - 4)
    section = None
    print(f"{ed.path}")
    for s, e in rows:
        if s.section_title != section:
            print()
            print(_bold(s.section_title))
            section = s.section_title
        shown = S.format_value(s, ed.value(s.key))
        if len(shown) > val_w:
            shown = shown[: val_w - 3] + "..."
        mark = "*" if e["changed"] else " "
        extra = f"  (from ${e['overridden_by']})" if "overridden_by" in e else ""
        print(f" {mark}{s.key.ljust(key_w)}{shown}{extra}")
    print()
    print(_dim("* differs from the default. `pcbridge get KEY` explains a setting; "
               "`pcbridge set KEY VALUE` changes it."))
    return 0


def get_cmd(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="pcbridge get", description="Show one setting and what it does.")
    p.add_argument("key")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    ed = _editor()
    try:
        s = ed.setting(args.key)
    except S.SettingsError as exc:
        inst.fail(str(exc))
        return 2
    e = _entry(ed, s)
    if args.json:
        print(json.dumps(e, indent=2, default=str))
        return 0
    print(f"{_bold(s.key)} = {S.format_value(s, ed.value(s.key))}")
    if not s.secret:
        print(_dim(f"default: {S.format_value(s, ed.default(s.key))}"
                   + (f"   allowed: {', '.join(s.choices)}" if s.choices else "")))
    if "overridden_by" in e:
        inst.warn(f"${e['overridden_by']} is set and wins over the file.")
    print()
    for para in s.help.splitlines():
        for line in textwrap.wrap(para, _width() - 2, subsequent_indent="  " if para.startswith("  ") else ""):
            print(line)
    return 0


def _read_secret(s: S.Setting, args: argparse.Namespace) -> str:
    if args.generate:
        return secrets.token_urlsafe(32 if s.name == "static_token" else 24)
    if args.stdin:
        return sys.stdin.readline().rstrip("\n")
    if not sys.stdin.isatty():
        raise S.SettingsError(
            f"{s.key} is secret: give it on stdin with --stdin, or use --generate."
        )
    first = getpass.getpass(f"New value for {s.key}: ")
    if getpass.getpass("Again: ") != first:
        raise S.SettingsError("The two entries differ; nothing was changed.")
    return first


def set_cmd(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="pcbridge set",
        description="Change one setting. The old file is backed up and the new one is "
                    "checked before it is written.",
        epilog="Lists: a TOML list or comma-separated words. Tables: TOML, "
               'for example \'{ opus = "high" }\'. Secrets (auth.password, '
               "auth.static_token) are never taken from the command line: they are "
               "prompted for, read with --stdin, or made with --generate.",
    )
    p.add_argument("key")
    p.add_argument("value", nargs="?")
    p.add_argument("--stdin", action="store_true", help="Read a secret value from stdin.")
    p.add_argument("--generate", action="store_true", help="Generate a new random secret.")
    args = p.parse_args(argv)
    ed = _editor()
    try:
        s = ed.setting(args.key)
        if s.secret:
            if args.value is not None:
                raise S.SettingsError(
                    f"{s.key} is secret and is not taken from the command line (it would stay "
                    f"in the shell history). Run `pcbridge set {s.key}` to be asked for it, "
                    "or use --stdin or --generate."
                )
            value: Any = _read_secret(s, args)
        else:
            if args.stdin or args.generate:
                raise S.SettingsError("--stdin and --generate are only for secrets.")
            if args.value is None:
                raise S.SettingsError(f"Give a value: pcbridge set {s.key} VALUE")
            value = S.parse_value(s, args.value)
        ed.set(s.key, value)
        result = ed.save()
    except S.SettingsError as exc:
        inst.fail(str(exc))
        return 1
    if not result.changed:
        inst.ok(f"{s.key} unchanged")
        return 0
    inst.ok(f"{s.key} = {S.format_value(s, S.ConfigEditor(result.path).value(s.key))}")
    if s.secret and args.generate:
        inst.say(f"          The new value is in {result.path}; it is not printed.")
    if s.key == "desktop.enabled" and value is True:
        inst.warn("desktop control is enabled: agents can drive this desktop once they open "
                  "the grant. `pcbridge lock` stops them at any time.")
    if result.backup:
        inst.say(_dim(f"          backup: {result.backup}"))
    advice = S.restart_advice(result.restart)
    if advice:
        inst.say(f"          {advice}")
    return 0


def reset_cmd(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="pcbridge reset",
                                description="Remove a setting from the file so its default applies.")
    p.add_argument("key")
    args = p.parse_args(argv)
    ed = _editor()
    try:
        s = ed.setting(args.key)
        if s.secret:
            raise S.SettingsError(f"{s.key} has no default to go back to; use `pcbridge set {s.key}`.")
        ed.reset(s.key)
        result = ed.save()
    except S.SettingsError as exc:
        inst.fail(str(exc))
        return 1
    if not result.changed:
        inst.ok(f"{s.key} was not set in the file; it already has its default")
        return 0
    inst.ok(f"{s.key} reset to its default: {S.format_value(s, ed.default(s.key))}")
    if result.backup:
        inst.say(_dim(f"          backup: {result.backup}"))
    advice = S.restart_advice(result.restart)
    if advice:
        inst.say(f"          {advice}")
    return 0


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------


def _tool_detail(t: Any) -> None:
    print(f"{_bold(t.name)}  {t.title}")
    print(_dim(f"{t.group} · {t.state}{' (' + t.note + ')' if t.note else ''} · {', '.join(t.hints())}"))
    print()
    for para in t.description.split("\n\n"):
        text = " ".join(para.split())
        for line in textwrap.wrap(text, _width() - 2):
            print(line)
        print()
    if t.params:
        print(_bold("Parameters"))
        name_w = max(len(p.name) for p in t.params) + 2
        for prm in t.params:
            req = "required" if prm.required else "optional"
            print(f"  {prm.name.ljust(name_w)}{prm.type}, {req}")
            for line in textwrap.wrap(prm.description, _width() - name_w - 4):
                print(" " * (name_w + 2) + _dim(line))


def tools_cmd(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="pcbridge tools",
        description="List the MCP tools, search them by name or description, or show one.",
    )
    p.add_argument("query", nargs="*", help="Words to search for, or one tool name for its details.")
    p.add_argument("--active", action="store_true", help="Only the tools the profile offers.")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    from .. import toolcatalog
    from ..config import ConfigError, load_config

    try:
        cfg = load_config()
    except ConfigError as exc:
        inst.fail(exc.message)
        return 1
    tools = toolcatalog.build(cfg)
    query = " ".join(args.query).strip()
    exact = next((t for t in tools if t.name == query), None)
    if exact is not None and not args.json:
        _tool_detail(exact)
        return 0
    found = toolcatalog.search(tools, query)
    if args.active:
        found = [t for t in found if t.active]
    if args.json:
        print(json.dumps([
            {"name": t.name, "title": t.title, "group": t.group, "state": t.state,
             "note": t.note, "hints": t.hints(), "description": t.description,
             "parameters": [p.__dict__ for p in t.params]}
            for t in found], indent=2))
        return 0
    active = sum(1 for t in tools if t.active)
    print(f"profile {cfg.tools_profile}: {active} of {len(tools)} tools offered")
    if not found:
        print("No tool matches.")
        return 1
    name_w = max(len(t.name) for t in found) + 2
    group = None
    for t in found:
        if not query and t.group != group:
            print()
            print(_bold(t.group))
            group = t.group
        state = "" if t.state == "active" else f"  [{t.state}]"
        print(f"  {t.name.ljust(name_w)}{t.title}{_dim(state)}")
    print()
    print(_dim("`pcbridge tools NAME` shows a tool's description and parameters."))
    return 0


# ---------------------------------------------------------------------------
# restart
# ---------------------------------------------------------------------------


def restart_cmd(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="pcbridge restart",
        description="Restart the daemon so it reads the config again. Waits for running "
                    "jobs and an open desktop grant; never interrupts them.",
    )
    p.add_argument("--wait", type=float, default=0, metavar="SECONDS",
                   help="How long to wait for jobs to finish (default: do not wait).")
    args = p.parse_args(argv)
    from ..config import ConfigError, load_config
    from .ops import _restart_when_idle

    try:
        cfg = load_config()
    except ConfigError as exc:
        inst.fail(f"the config does not load, so the daemon would not start: {exc.message}")
        return 1
    msg = _restart_when_idle(cfg, args.wait)
    (inst.ok if msg.startswith("daemon") else inst.warn)(msg.replace("`pcbridge update`", "`pcbridge restart`"))
    return 0 if msg.startswith("daemon") else 1
