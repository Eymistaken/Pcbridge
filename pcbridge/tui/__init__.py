"""The terminal UI: `pcbridge` with no arguments, or `pcbridge ui`.

Textual draws it with the terminal's own colors and takes mouse clicks
where the terminal reports them. Everything it does goes through
`backend.Backend`, the same modules the direct commands use.
"""

from __future__ import annotations

import argparse
import sys


def run(argv: list[str]) -> int:
    argparse.ArgumentParser(
        prog="pcbridge ui",
        description="Open the terminal UI: settings, the desktop grant and the tool list.",
    ).parse_args(argv)
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("pcbridge ui needs a terminal. `pcbridge list` shows the commands that work without one.",
              file=sys.stderr)
        return 2
    try:
        from .app import PcbridgeApp
    except ImportError as exc:  # pragma: no cover - an install without Textual
        print(f"The terminal UI needs Textual, which is not installed ({exc}). Reinstall pcbridge, "
              "or in a git checkout run: .venv/bin/pip install -e .", file=sys.stderr)
        return 1
    PcbridgeApp().run()
    return 0
