"""The Agents & Jobs tab."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static

from .pane import ScrollPane


class JobsPane(ScrollPane):
    def compose(self) -> ComposeResult:
        yield Static("")
