"""The command palette (`Ctrl+P`): every pcbridge command and the nine tabs."""

from __future__ import annotations

from textual.command import Hit, Hits, Provider


class PcbridgeCommands(Provider):
    async def search(self, query: str) -> Hits:
        from .app import TABS

        matcher = self.matcher(query)
        for i, (tab, label, _short) in enumerate(TABS):
            text = f"Go to {label}"
            score = matcher.match(text)
            if score > 0:
                yield Hit(score, matcher.highlight(text),
                          lambda tab=tab: self.app.action_show_tab(tab), help=f"key {i + 1}")
