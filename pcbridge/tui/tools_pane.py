"""The Tools tab: every MCP tool, what the profile offers, search as you type.

The catalog comes from `toolcatalog.build`, the server's own registration,
built in a worker thread (about 0.65 s). The search box matches every word
against the name, title and description, name matches first. The profile
picker saves `tools.profile` at once and raises the restart notice (the
daemon and the clients, which cache the tool list).
"""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Input, Static

from .pane import Pane, while_mounted
from .theme import PALETTE
from .widgets import RestartBanner, SectionTitle, Segmented, tag

CSS = """
ToolsPane #tools-top {
    height: 1;
    margin-bottom: 1;
}
ToolsPane #tools-search {
    width: 12;
    margin-right: 2;
}
ToolsPane.narrow #tools-search {
    width: 10;
}
ToolsPane.narrow #tools-profile-note, ToolsPane.narrow #tools-profile-label {
    display: none;
}
ToolsPane #tools-profile-label {
    width: auto;
    color: $pb-muted;
    padding-right: 1;
}
ToolsPane #tools-profile-note {
    width: 1fr;
    color: $pb-dim;
    padding: 0 0 0 1;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}
ToolsPane #tools-show {
    margin-left: 1;
}
ToolsPane #tools-body {
    height: 1fr;
}
ToolsPane #tools-left {
    width: 1fr;
    height: 1fr;
}
ToolsPane #tools-table {
    height: 1fr;
}
ToolsPane #tools-count {
    height: 1;
    color: $pb-dim;
}
ToolsPane #tools-detail {
    width: 1fr;
    height: 1fr;
    margin-left: 4;
    scrollbar-size-vertical: 1;
}
ToolsPane.narrow #tools-body {
    layout: vertical;
}
ToolsPane.narrow #tools-detail {
    margin-left: 0;
}
"""

SHORT_GROUP = {"Agents and jobs": "agents", "Shell, files and terminals": "shell",
               "Desktop": "desktop", "System": "system"}


def hint_tags(tool: Any) -> Text:
    out = Text()
    parts = [tag("read-only", "ok") if tool.read_only else tag("changes things", "muted")]
    if tool.destructive:
        parts.append(tag("destructive", "error"))
    if tool.idempotent:
        parts.append(tag("idempotent", "muted"))
    if tool.open_world:
        parts.append(tag("open world", "working"))
    for i, p in enumerate(parts):
        if i:
            out.append(" ")
        out.append_text(p)
    return out


def describe(tool: Any, profile: str) -> Group:
    head = Text()
    head.append(tool.title, style="bold")
    head.append(f"  {SHORT_GROUP.get(tool.group, tool.group)}\n", style=PALETTE["dim"])
    head.append_text(hint_tags(tool))
    head.append("\n")
    if not tool.active:
        head.append(f"Hidden by the {profile} profile; a call by name is refused and audited.\n",
                    style=PALETTE["warning"])
    elif tool.note:
        head.append(f"Offered, but {tool.note}.\n", style=PALETTE["warning"])
    else:
        head.append(f"Offered by the {profile} profile.\n", style=PALETTE["ok"])
    body = Text()
    for para in tool.description.split("\n\n"):
        body.append(" ".join(para.split()) + "\n\n", style=PALETTE["text"])
    if not tool.params:
        body.append("No parameters.", style=PALETTE["dim"])
        return Group(head, body)
    title = Text("PARAMETERS", style=f"bold {PALETTE['muted']}")
    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True, style="bold")
    grid.add_column(no_wrap=True, style=PALETTE["muted"])
    grid.add_column(ratio=1)
    for p in tool.params:
        grid.add_row(p.name, f"{p.type}, {'required' if p.required else 'optional'}",
                     Text(p.description, style=PALETTE["dim"]))
    return Group(head, body, title, grid)


class ToolsPane(Pane):
    FOOTER = [("/", "Search", "key:slash"), ("↑↓", "Tool", "key:none"), ("p", "Profile", "key:p"),
              ("a", "All/Offered/Hidden", "key:a")]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.catalog: list = []
        self.profile = ""
        self.remote_profile = ""
        self.showing = "all"
        self.quiet = False

    def compose(self) -> ComposeResult:
        yield RestartBanner()
        with Horizontal(id="tools-top"):
            yield Input(placeholder="Search tools", id="tools-search")
            yield Static("Profile", id="tools-profile-label")
            yield Segmented([(p, p) for p in ("full", "core", "desktop", "readonly")], id="tools-profile")
            yield Static("", id="tools-profile-note")
            yield Segmented([("all", "All"), ("offered", "Offered"), ("hidden", "Hidden")],
                            id="tools-show")
        with Horizontal(id="tools-body"):
            with Vertical(id="tools-left"):
                yield DataTable(id="tools-table", cursor_type="row")
                yield Static("Loading the tools…", id="tools-count")
            with VerticalScroll(id="tools-detail"):
                yield SectionTitle("", upper=False, id="tool-title", classes="first")
                yield Static("", id="tool-body")

    def on_mount(self) -> None:
        self.set_class(self.app.size.width < 100, "narrow")
        self.query_one("#tools-table", DataTable).add_columns(" ", "TOOL", "GROUP", "TITLE")
        if getattr(self.app, "cfg", None) is not None:
            self.reload()

    def on_resize(self) -> None:
        self.set_class(self.app.size.width < 100, "narrow")
        self._show_labels()

    def config_changed(self) -> None:
        self.reload()

    def reload(self) -> None:
        cfg = getattr(self.app, "cfg", None)
        if cfg is None:
            self.query_one("#tools-count", Static).update(
                "The config does not load; the tool list needs it.")
            return
        self.query_one("#tools-count", Static).update("Loading the tools…")
        self._load(cfg)

    @work(thread=True, exclusive=True, group="tools")
    def _load(self, cfg: Any) -> None:
        try:
            tools = self.app.backend.tools(cfg)
            error = ""
        except Exception as exc:  # noqa: BLE001 - shown in the tab
            tools, error = [], str(exc)
        remote = getattr(cfg, "remote_tools_profile", "same")
        self.app.post(self._loaded, tools, error, getattr(cfg, "tools_profile", ""), remote)

    @while_mounted
    def _loaded(self, tools: list, error: str, profile: str, remote: str = "same") -> None:
        self.catalog = tools
        self.profile = profile
        self.remote_profile = profile if remote in ("same", "", None) else remote
        self.quiet = True
        if profile:
            self.query_one("#tools-profile", Segmented).value = profile
        self.call_after_refresh(self._unquiet)
        self._show_labels()
        self.query_one("#tools-profile-note", Static).update(f"local · remote: {self.remote_profile}")
        if error:
            self.query_one("#tools-count", Static).update(f"The tool list could not be built: {error}")
        self.fill()

    def _unquiet(self) -> None:
        self.quiet = False

    def _show_labels(self) -> None:
        """`All 40 | Offered 24 | Hidden 16`; without the counts at 80 columns."""
        offered = sum(1 for t in self.catalog if t.active)
        counts = not self.has_class("narrow")
        self.query_one("#tools-show", Segmented).set_options(
            [("all", f"All {len(self.catalog)}" if counts else "All"),
             ("offered", f"Offered {offered}" if counts else "Offered"),
             ("hidden", f"Hidden {len(self.catalog) - offered}" if counts else "Hidden")])

    def matching(self) -> list:
        from ..toolcatalog import search

        found = search(self.catalog, self.query_one("#tools-search", Input).value)
        if self.showing == "offered":
            found = [t for t in found if t.active]
        elif self.showing == "hidden":
            found = [t for t in found if not t.active]
        return found

    def fill(self) -> None:
        table = self.query_one("#tools-table", DataTable)
        keep = None
        if table.row_count and table.cursor_row < table.row_count:
            keep = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        table.clear()
        found = self.matching()
        for t in found:
            mark = Text("✓", style=PALETTE["ok"]) if t.active else Text("·", style=PALETTE["dim"])
            name = Text(t.name, style="bold" if t.active else PALETTE["dim"])
            table.add_row(mark, name, Text(SHORT_GROUP.get(t.group, t.group), style=PALETTE["dim"]),
                          Text(t.title, style=PALETTE["muted"]), key=t.name)
        active = sum(1 for t in self.catalog if t.active)
        query = self.query_one("#tools-search", Input).value.strip()
        count = f"{len(found)} shown · {active} of {len(self.catalog)} offered locally"
        if query:
            count += " · name matches first"
        self.query_one("#tools-count", Static).update(count)
        names = [t.name for t in found]
        table.move_cursor(row=names.index(keep) if keep in names else 0)
        self.show_tool(found[table.cursor_row] if found and table.cursor_row < len(found) else None)

    def show_tool(self, tool: Any) -> None:
        title = self.query_one("#tool-title", SectionTitle)
        body = self.query_one("#tool-body", Static)
        if tool is None:
            title.set("No tool matches." if self.catalog else "")
            body.update("")
            return
        title.set(tool.name)
        body.update(describe(tool, self.profile))

    @on(Input.Changed, "#tools-search")
    def _filter(self) -> None:
        self.fill()

    @on(Segmented.Changed, "#tools-show")
    def _show(self, event: Segmented.Changed) -> None:
        self.showing = event.value or "all"
        self.fill()

    @on(Segmented.Changed, "#tools-profile")
    def _profile(self, event: Segmented.Changed) -> None:
        if self.quiet or not self.profile or event.value == self.profile:
            return
        if not self.app.apply_settings({"tools.profile": event.value}):
            self.quiet = True
            self.query_one("#tools-profile", Segmented).value = self.profile
            self.call_after_refresh(self._unquiet)

    @on(DataTable.RowHighlighted, "#tools-table")
    def _row(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        tool = next((t for t in self.catalog if t.name == event.row_key.value), None)
        self.show_tool(tool)

    def key_slash(self) -> None:
        self.query_one("#tools-search", Input).focus()

    def key_p(self) -> None:
        self.query_one("#tools-profile", Segmented).focus()

    def key_a(self) -> None:
        self.query_one("#tools-show", Segmented).action_move(1)

    def key_none(self) -> None:
        pass
