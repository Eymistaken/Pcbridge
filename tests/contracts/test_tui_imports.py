"""FastMCP imported from two threads at once fails ("FastMCP server support
is not installed"); the terminal UI's workers call imports.ready() first.
Measured with the installed 2.9.1: every fresh start broke doctor's config
group. Each case runs in a fresh interpreter, where nothing is imported yet."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = """
import sys, threading
sys.path.insert(0, {root!r})
from pcbridge.tui import imports
errors = []
def go(name):
    try:
        if {guard}:
            imports.ready()
        __import__(name)
    except Exception as exc:
        errors.append(repr(exc))
threads = [threading.Thread(target=go, args=(n,)) for n in ("pcbridge.tools", "pcbridge.auth")]
[t.start() for t in threads]
[t.join() for t in threads]
print(errors)
"""


def run(guard: bool) -> str:
    code = SCRIPT.format(root=str(ROOT), guard=guard)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=100).stdout.strip()


class ImportRaceTests(unittest.TestCase):
    def test_two_workers_import_fastmcp_safely(self) -> None:
        for _ in range(3):
            self.assertEqual(run(guard=True), "[]")


if __name__ == "__main__":
    unittest.main()
