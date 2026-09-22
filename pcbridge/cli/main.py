"""The ``pcbridge`` command.

One entry point for running and operating pcbridge. Subcommands:

    pcbridge serve      run the resident server (HTTP listener; the daemon)
    pcbridge stdio      serve one MCP client over stdin/stdout
    pcbridge --version  print the version

Heavy modules (FastMCP, the desktop layer) are imported only inside the
subcommand that needs them, so ``pcbridge --version`` and the stdio relay stay
fast.
"""

from __future__ import annotations

import argparse
import sys

from .. import __version__


def _serve(args: argparse.Namespace) -> int:
    from ..server import main as server_main

    argv = []
    if args.config:
        argv += ["--config", args.config]
    if args.check:
        argv.append("--check")
    return server_main(argv)


def _stdio(args: argparse.Namespace) -> int:
    from ..server import main as server_main

    argv = ["--stdio"]
    if args.config:
        argv += ["--config", args.config]
    return server_main(argv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pcbridge",
        description="Run and operate pcbridge, an MCP server for the GNOME on Wayland desktop.",
    )
    parser.add_argument("--version", action="version", version=f"pcbridge {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("serve", help="Run the resident server.")
    p.add_argument("-c", "--config", help="Path to config.toml.")
    p.add_argument("--check", action="store_true", help="Validate the configuration and exit.")
    p.set_defaults(func=_serve)

    p = sub.add_parser("stdio", help="Serve one MCP client over stdin and stdout.")
    p.add_argument("-c", "--config", help="Path to config.toml.")
    p.set_defaults(func=_stdio)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help(sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
