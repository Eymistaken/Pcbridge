"""The Overview tab."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static

from .pane import ScrollPane


class OverviewPane(ScrollPane):
    FOOTER = [("l", "Lock/Unlock", "app.toggle_grant"), ("r", "Restart", "app.restart")]

    def compose(self) -> ComposeResult:
        yield Static("Reading the status…", id="overview-body")
