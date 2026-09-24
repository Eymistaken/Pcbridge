"""The ``pcbridge`` command.

One entry point for running and operating pcbridge. `COMMANDS` below lists
every subcommand; `pcbridge list` prints it.

Heavy modules (FastMCP, the desktop layer) are imported only inside the
subcommand that needs them, so ``pcbridge --version`` and the stdio relay stay
fast.
"""

from __future__ import annotations

import argparse
import sys

from .. import __version__


def _serve(argv: list[str]) -> int:
    if "--check" in argv:
        from ..app import main as app_main

        return app_main(argv)
    from ..daemon import main as daemon_main

    return daemon_main(argv)


def _stdio(argv: list[str]) -> int:
    from ..relay import main as relay_main

    return relay_main(argv)


def _lazy(module: str, func: str):
    def run(argv: list[str]) -> int:
        import importlib

        return getattr(importlib.import_module(module, __package__), func)(argv)

    return run


# Subcommands whose arguments belong to another module's parser.
_PASSTHROUGH = {
    "serve": _serve,
    "stdio": _stdio,
    "setup": _lazy(".ops", "setup"),
    "connect": _lazy(".connect", "main"),
    "disconnect": _lazy(".connect", "disconnect_main"),
    "clients": _lazy(".connect", "clients_main"),
    "doctor": _lazy(".doctor", "main"),
    "status": _lazy(".ops", "status"),
    "lock": _lazy(".ops", "lock"),
    "unlock": _lazy(".ops", "unlock"),
    "stop": _lazy(".ops", "stop"),
    "remote": _lazy(".ops", "remote"),
    "logs": _lazy(".ops", "logs"),
    "report": _lazy(".ops", "report"),
    "update": _lazy(".ops", "update"),
    "uninstall": _lazy(".ops", "uninstall"),
    "list": _lazy(".configure", "list_commands"),
    "settings": _lazy(".configure", "settings_cmd"),
    "get": _lazy(".configure", "get_cmd"),
    "set": _lazy(".configure", "set_cmd"),
    "reset": _lazy(".configure", "reset_cmd"),
    "tools": _lazy(".configure", "tools_cmd"),
    "restart": _lazy(".configure", "restart_cmd"),
    "ui": _lazy("..tui", "run"),
}

# Every command: (group, name, usage, what it does). `pcbridge list` and
# `--help` are both generated from this table, so a command cannot be missing
# from either.
COMMANDS: list[tuple[str, str, str, str]] = [
    ("Desktop control", "lock", "lock",
     "Emergency stop for desktop control: close the grant, stop screen sharing."),
    ("Desktop control", "unlock", "unlock [--minutes N]", "Open desktop control for agents."),
    ("Settings", "settings", "settings [FILTER] [--changed]",
     "List every setting with its value; FILTER searches keys and descriptions."),
    ("Settings", "get", "get KEY", "Show one setting, its default and what it does."),
    ("Settings", "set", "set KEY VALUE",
     "Change a setting (checked, backed up). Secrets are prompted for, never given on the command line."),
    ("Settings", "reset", "reset KEY", "Put a setting back to its default."),
    ("Tools", "tools", "tools [QUERY] [--active]",
     "List the MCP tools and which ones the profile offers; search by name or description; "
     "a tool name shows its details."),
    ("Connections", "clients", "clients",
     "Which local agents and apps can use pcbridge: Claude Code, Codex, Claude Desktop, Antigravity, "
     "Hermes, OpenCode, Pi, oh-my-pi."),
    ("Connections", "connect", "connect [CLIENT...]",
     "Let a client use pcbridge's tools (backed up; --dry-run). Alone: the three `setup` connects."),
    ("Connections", "disconnect", "disconnect CLIENT...",
     "Stop a client from using pcbridge; the entry is switched off where the client allows it, removed otherwise."),
    ("Service", "status", "status", "Daemon, desktop grant, running jobs, remote tunnel (--json)."),
    ("Service", "restart", "restart [--wait S]",
     "Restart the daemon to apply settings, only when no job is running."),
    ("Service", "stop", "stop [--keep-jobs]", "Stop the daemon and every running job (the socket stays ready)."),
    ("Service", "update", "update", "Load the installed version into the daemon once no job is running."),
    ("Service", "logs", "logs [-f]", "Show the daemon log (-f to follow)."),
    ("Service", "doctor", "doctor [--fix]", "Check everything and say what to fix (--json)."),
    ("Service", "report", "report", "Write a sanitized bug-report bundle."),
    ("Remote access", "remote", "remote start|stop|status", "Remote access through Tailscale Funnel."),
    ("Setup", "setup", "setup", "Install or update pcbridge for this user: config, service, extension, clients, aliases."),

    ("Setup", "uninstall", "uninstall [--purge]", "Remove pcbridge for this user (--purge also trashes config and state)."),
    ("Interface", "ui", "ui", "Open the terminal UI; `pcbridge` alone in a terminal does the same."),
    ("Interface", "list", "list", "Show every command."),
    ("MCP clients", "serve", "serve", "Run the resident server (the daemon: socket + HTTP)."),
    ("MCP clients", "stdio", "stdio", "Connect one MCP client (stdin/stdout) to the daemon."),
]

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pcbridge",
        description="Run and operate pcbridge, an MCP server for a GNOME or KDE Plasma desktop on Wayland.",
        epilog="`pcbridge list` groups the commands; every command takes --help.",
    )
    parser.add_argument("--version", action="version", version=f"pcbridge {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    for _group, name, _usage, text in COMMANDS:
        sub.add_parser(name, help=text, add_help=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Alone in a terminal: the terminal UI. Piped or scripted: the help, as
    # before, so nothing that runs `pcbridge` without arguments changes.
    if not argv and sys.stdin.isatty() and sys.stdout.isatty():
        from ..tui import run

        return run([])
    if argv and argv[0] in _PASSTHROUGH:
        return _PASSTHROUGH[argv[0]](argv[1:])
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help(sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
