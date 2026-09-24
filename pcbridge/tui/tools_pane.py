"""The Tools tab: every MCP tool, what the profile offers, search as you type.

The catalog comes from `toolcatalog.build`, the server's own registration,
built in a worker thread (about 0.65 s). The search box matches every word
against the name, title and description, name matches first.
"""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Input, Static

from .app import while_mounted

CSS = """
ToolsPane #tools-top {
    height: auto;
}
ToolsPane #tools-search {
    width: 1fr;
}
ToolsPane #tools-active-only {
    width: auto;
    min-width: 18;
    margin: 1 0 0 1;
}
ToolsPane #tools-count {
    margin: 0 0 1 1;
}
ToolsPane #tools-table {
    height: 1fr;
    min-height: 6;
}
ToolsPane #tools-detail {
    height: 45%;
    padding-top: 1;
}
ToolsPane #tool-title {
    text-style: bold;
}
"""


def describe(tool: Any) -> Group:
    text = Text()
    for para in tool.description.split("\n\n"):
        text.append(" ".join(para.split()) + "\n\n")
    if not tool.params:
        text.append("No parameters.", style="dim")
        return Group(text)
    text.append("Parameters", style="bold")
    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True, style="bold")
    grid.add_column(no_wrap=True)
    grid.add_column(ratio=1)
    for p in tool.params:
        grid.add_row(p.name, f"{p.type}, {'required' if p.required else 'optional'}",
                     Text(p.description, style="dim"))
    return Group(text, grid)


class ToolsPane(Vertical):
    DEFAULT_CSS = CSS

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.catalog: list = []
        self.profile = ""
        self.only_active = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="tools-top"):
            yield Input(placeholder="Search tools by name or description", id="tools-search")
            # A button that names its state: a checkbox shows the same glyph
            # either way in the ANSI theme and differs only by color.
            yield Button("Showing: all", id="tools-active-only", compact=True)
        yield Static("Loading the tools...", id="tools-count", classes="hint")
        yield DataTable(id="tools-table", cursor_type="row")
        with VerticalScroll(id="tools-detail"):
            yield Static("", id="tool-title")
            yield Static("", id="tool-meta", classes="hint")
            yield Static("", id="tool-body")

    def on_mount(self) -> None:
        self.query_one("#tools-table", DataTable).add_columns("Tool", "State", "What it does")
        if getattr(self.app, "cfg", None) is not None:
            self.reload()

    def reload(self) -> None:
        cfg = getattr(self.app, "cfg", None)
        if cfg is None:
            self.query_one("#tools-count", Static).update(
                "The config does not load; the tool list needs it.")
            return
        self.query_one("#tools-count", Static).update("Loading the tools...")
        self._load(cfg)

    @work(thread=True, exclusive=True, group="tools")
    def _load(self, cfg: Any) -> None:
        try:
            tools = self.app.backend.tools(cfg)
            error = ""
        except Exception as exc:  # noqa: BLE001 - shown in the tab
            tools, error = [], str(exc)
        self.app.post(self._loaded, tools, error, getattr(cfg, "tools_profile", ""))

    @while_mounted
    def _loaded(self, tools: list, error: str, profile: str) -> None:
        self.catalog = tools
        self.profile = profile
        if error:
            self.query_one("#tools-count", Static).update(f"The tool list could not be built: {error}")
        self.fill()

    def matching(self) -> list:
        from ..toolcatalog import search

        found = search(self.catalog, self.query_one("#tools-search", Input).value)
        if self.only_active:
            found = [t for t in found if t.active]
        return found

    def fill(self) -> None:
        table = self.query_one("#tools-table", DataTable)
        keep = None
        if table.row_count and table.cursor_row < table.row_count:
            keep = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        table.clear()
        found = self.matching()
        for t in found:
            state = Text(t.state, style="" if t.state == "active" else "dim")
            name = Text(t.name, style="bold" if t.active else "dim")
            table.add_row(name, state, t.title, key=t.name)
        active = sum(1 for t in self.catalog if t.active)
        query = self.query_one("#tools-search", Input).value.strip()
        count = f"profile {self.profile}: {active} of {len(self.catalog)} tools offered"
        if query or self.only_active:
            count += f" · {len(found)} shown"
        self.query_one("#tools-count", Static).update(count)
        names = [t.name for t in found]
        table.move_cursor(row=names.index(keep) if keep in names else 0)
        self.show(found[table.cursor_row] if found and table.cursor_row < len(found) else None)

    def show(self, tool: Any) -> None:
        title = self.query_one("#tool-title", Static)
        meta = self.query_one("#tool-meta", Static)
        body = self.query_one("#tool-body", Static)
        if tool is None:
            title.update("No tool matches." if self.catalog else "")
            meta.update("")
            body.update("")
            return
        title.update(f"{tool.name}  {tool.title}")
        state = tool.state + (f" ({tool.note})" if tool.note else "")
        if not tool.active:
            state += f"; the {self.profile!r} profile leaves it out (Settings > Tools)"
        meta.update(f"{tool.group} · {state} · {', '.join(tool.hints())}")
        body.update(describe(tool))

    @on(Input.Changed, "#tools-search")
    def _filter(self) -> None:
        self.fill()

    @on(Button.Pressed, "#tools-active-only")
    def _toggle_active(self, event: Button.Pressed) -> None:
        self.only_active = not self.only_active
        event.button.label = "Showing: offered" if self.only_active else "Showing: all"
        self.fill()

    @on(DataTable.RowHighlighted, "#tools-table")
    def _row(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        tool = next((t for t in self.catalog if t.name == event.row_key.value), None)
        self.show(tool)
