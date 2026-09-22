"""The ``pcbridge`` command.

One entry point for running and operating pcbridge. Subcommands:

    pcbridge serve      run the resident server (the daemon: socket + HTTP)
    pcbridge stdio      connect one MCP client to the daemon (the relay)
    pcbridge --version  print the version

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
}

_HELP = {
    "setup": "Install or update pcbridge for this user: config, service, extension, clients, aliases.",
    "connect": "Register pcbridge with Claude Code, Codex and Claude Desktop (--client, --dry-run).",
    "doctor": "Check everything and say what to fix (--fix, --json).",
    "status": "Daemon, desktop grant, running jobs, remote tunnel (--json).",
    "lock": "Emergency stop for desktop control: close the grant, stop screen sharing.",
    "unlock": "Open desktop control for agents (--minutes N).",
    "stop": "Stop the daemon and every running job (the socket stays ready).",
    "remote": "start | stop | status of remote access through Tailscale Funnel.",
    "logs": "Show the daemon log (-f to follow).",
    "report": "Write a sanitized bug-report bundle.",
    "update": "Load the installed version into the daemon once no job is running.",
    "uninstall": "Remove pcbridge for this user (--purge also trashes config and state).",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pcbridge",
        description="Run and operate pcbridge, an MCP server for the GNOME on Wayland desktop.",
    )
    parser.add_argument("--version", action="version", version=f"pcbridge {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser(
        "serve",
        help="Run the resident server (daemon). Options: -c, --socket, --port, --no-http, --no-socket, --check.",
        add_help=False,
    )
    sub.add_parser(
        "stdio",
        help="Connect one MCP client (stdin/stdout) to the daemon; falls back to an in-process server.",
        add_help=False,
    )
    for name, text in _HELP.items():
        sub.add_parser(name, help=text, add_help=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
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
