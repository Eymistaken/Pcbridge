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


# Subcommands whose arguments belong to another module's parser.
_PASSTHROUGH = {"serve": _serve, "stdio": _stdio}


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
