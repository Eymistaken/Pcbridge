"""The Tools tab."""

from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import Static


class ToolsPane(Vertical):
    def compose(self):
        yield Static("Tools: `pcbridge tools` lists them for now.", classes="hint")
