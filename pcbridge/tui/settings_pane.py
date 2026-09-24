"""The Settings tab."""

from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import Static


class SettingsPane(Vertical):
    def compose(self):
        yield Static("Settings: `pcbridge settings` lists them for now.", classes="hint")
