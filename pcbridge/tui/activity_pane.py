"""The Activity tab."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static

from .pane import ScrollPane


class ActivityPane(ScrollPane):
    def compose(self) -> ComposeResult:
        yield Static("")
