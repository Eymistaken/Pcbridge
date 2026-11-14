"""The Activity tab: what was done through pcbridge, from the audit log.

Newest first, read from the end of `audit.log` (and its rotated `.1`) a
page at a time in a worker, so a full log never slows the screen. Nothing
new is stored: the view only reads what the tools already record, and the
log never held contents (the command but not its output, the path but not
the file).
"""

from __future__ import annotations

import time
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Static

from . import auditlog
from .pane import Pane, while_mounted
from .theme import PALETTE
from .widgets import SectionTitle, Segmented, key_label

PAGE = 22

CSS = """
ActivityPane .filter-row {
    height: 1;
}
ActivityPane .filter-row .label {
    width: 8;
    color: $pb-muted;
}
ActivityPane #activity-totals {
    height: 1;
    color: $pb-dim;
    margin: 1 0;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}
ActivityPane #activity-table {
    height: 1fr;
}
ActivityPane #activity-entry {
    height: 9;
}
ActivityPane #entry-json {
    height: 1fr;
    background: $pb-box;
    padding: 0 1;
}
ActivityPane #entry-nav {
    height: 1;
    margin-top: 1;
}
ActivityPane #entry-page {
    width: auto;
    color: $pb-muted;
    padding: 0 1;
}
ActivityPane #entry-fill {
    width: 1fr;
}
"""

KIND_TOKENS = {"desktop": "kind-desktop", "shell": "kind-shell", "file": "kind-file",
               "agent": "kind-agent", "remote": "kind-remote"}
RESULT_TOKENS = {"refused": "error", "failed": "error", "stopped": "warning"}


class ActivityTable(DataTable, inherit_bindings=False):
    """The table without its own Page Up / Page Down: those page the log."""

    BINDINGS = [b for b in DataTable.BINDINGS
                if getattr(b, "key", "") not in ("pageup", "pagedown")]


def day_label(ts: str, now: float | None = None) -> str:
    day = ts[:10]
    now = time.time() if now is None else now
    if day == time.strftime("%Y-%m-%d", time.localtime(now)):
        return "TODAY"
    if day == time.strftime("%Y-%m-%d", time.localtime(now - 86400)):
        return "YESTERDAY"
    return day


def result_text(result: str) -> Text:
    return Text(result, style=PALETTE[RESULT_TOKENS.get(result, "ok" if result in ("ok", "exit 0")
                                                         else "muted")])


class ActivityPane(Pane):
    FOOTER = [("k", "Kind", "key:k"), ("c", "Client", "key:c"),
              ("pgup/pgdn", "Newer/Older", "key:pagedown"), ("y", "Copy", "key:y")]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.page = 0
        self.kind = "all"
        self.client = "all"
        self.entries: list[auditlog.Entry] = []
        self.more = False
        self.totals: auditlog.Totals | None = None
        self.loaded = False
        self._request = 0

    def compose(self) -> ComposeResult:
        with Horizontal(classes="filter-row"):
            yield Static("Kind", classes="label")
            yield Segmented([("all", "All")] + [(k, auditlog.KIND_LABELS[k]) for k in auditlog.KINDS],
                            id="activity-kind")
        with Horizontal(classes="filter-row"):
            yield Static("Client", classes="label")
            yield Segmented([("all", "All")], id="activity-client")
        yield Static("Reading the audit log…", id="activity-totals")
        yield ActivityTable(id="activity-table", cursor_type="row")
        with Vertical(id="activity-entry"):
            yield SectionTitle("Entry", "as written, one JSON line · contents are never recorded",
                               id="entry-title")
            with VerticalScroll(id="entry-json"):
                yield Static("", id="entry-text")
            with Horizontal(id="entry-nav"):
                yield Button("‹ Newer", id="entry-newer", compact=True)
                yield Static("", id="entry-page")
                yield Button("Older ›", id="entry-older", compact=True)
                yield Static("", id="entry-fill")
                yield Button(key_label("Copy line", "y"), id="entry-copy", compact=True)

    def on_mount(self) -> None:
        self.query_one("#activity-table", DataTable).add_columns(
            "TIME", "CLIENT", "KIND", "EVENT", "WHAT", "RESULT")

    def shown(self) -> None:
        if not self.loaded:
            self.loaded = True
            self.load()
            self._totals_worker()

    def config_changed(self) -> None:
        if self.loaded:
            self.load()

    # -- reading -------------------------------------------------------------------

    def keep(self):
        kind, client = self.kind, self.client

        def keep(e: auditlog.Entry) -> bool:
            if kind != "all" and e.kind != kind:
                return False
            return client == "all" or (e.client or "local") == client

        return keep

    def load(self) -> None:
        cfg = getattr(self.app, "cfg", None)
        if cfg is None:
            self.query_one("#activity-totals", Static).update(
                "The config does not load; the audit log's place is in it.")
            return
        self._request += 1
        self._page_worker(cfg, self.page, self.keep(), self._request)

    @work(thread=True, group="activity")
    def _page_worker(self, cfg: Any, page: int, keep: Any, request: int) -> None:
        try:
            entries, more = self.app.backend.audit_page(cfg, page * PAGE, PAGE, keep)
            error = ""
        except Exception as exc:  # noqa: BLE001 - shown in the tab
            entries, more, error = [], False, str(exc)
        self.app.post(self._page_loaded, entries, more, error, request)

    @work(thread=True, group="activity-totals")
    def _totals_worker(self) -> None:
        cfg = getattr(self.app, "cfg", None)
        if cfg is None:
            return
        try:
            totals = self.app.backend.audit_totals(cfg)
        except Exception:  # noqa: BLE001 - the totals are a detail
            return
        self.app.post(self._totals_loaded, totals)

    @while_mounted
    def _totals_loaded(self, totals: auditlog.Totals) -> None:
        self.totals = totals
        self._render_totals()
        self._render_nav()

    def _render_totals(self) -> None:
        line = "Newest first, read from the end of audit.log"
        t = self.totals
        if t is not None:
            line += f" · {t.entries:,} entries"
            if t.first:
                line += f" since {t.first}"
            limit = int(getattr(self.app.cfg, "audit_max_bytes", 0) or 0) if self.app.cfg else 0
            if limit:
                line += f" · {t.size / 1e6:.1f} of {limit / 1e6:.0f} MB before it rotates"
        self.query_one("#activity-totals", Static).update(line)

    @while_mounted
    def _page_loaded(self, entries: list, more: bool, error: str, request: int) -> None:
        if request != self._request:
            return
        if error:
            self.query_one("#activity-totals", Static).update(
                Text(f"✕ The audit log could not be read: {error}", style=PALETTE["error"]))
            return
        self.entries = entries
        self.more = more
        self._render_totals()
        self._render_clients()
        self.fill()
        self._render_nav()

    def _render_clients(self) -> None:
        seen: dict[str, int] = {}
        for e in self.entries:
            name = e.client or "local"
            seen[name] = seen.get(name, 0) + 1
        top = sorted(seen, key=lambda n: -seen[n])[:5]
        if self.client != "all" and self.client not in top:
            top.insert(0, self.client)
        self.query_one("#activity-client", Segmented).set_options(
            [("all", "All")] + [(n, n[:16]) for n in top], self.client)

    def fill(self) -> None:
        table = self.query_one("#activity-table", DataTable)
        table.clear()
        day = None
        for i, e in enumerate(self.entries):
            label = day_label(e.ts)
            if label != day:
                day = label
                table.add_row(Text(label, style=f"bold {PALETTE['muted']}"), "", "", "", "", "",
                              key=f"day:{i}")
            kind = Text(e.kind, style=PALETTE[KIND_TOKENS.get(e.kind, "muted")])
            table.add_row(e.ts[11:19], Text((e.client or "local")[:18], style=PALETTE["muted"]), kind,
                          e.event, Text(e.what()[:44], style=PALETTE["dim"]), result_text(e.result),
                          key=str(i))
        if not self.entries:
            table.add_row(Text("Nothing recorded here." if self.page == 0 else "No older entry.",
                               style=PALETTE["dim"]), "", "", "", "", "")
        first = next((r for r, k in enumerate(table.rows) if not str(k.value).startswith("day:")), 0)
        table.move_cursor(row=first)
        self.show_entry(0 if self.entries else None)

    def _render_nav(self) -> None:
        shown = len(self.entries)
        text = f"page {self.page + 1} · {shown} shown"
        filtered = self.kind != "all" or self.client != "all"
        if self.totals is not None and not filtered:
            text = f"page {self.page + 1} · {shown} of {self.totals.entries:,}"
        self.query_one("#entry-page", Static).update(text)
        self.query_one("#entry-newer", Button).disabled = self.page == 0
        self.query_one("#entry-older", Button).disabled = not self.more

    def show_entry(self, index: int | None) -> None:
        text = self.query_one("#entry-text", Static)
        if index is None or index >= len(self.entries):
            text.update("")
            return
        text.update(Text(self.entries[index].raw, style=PALETTE["text"]))

    def selected(self) -> auditlog.Entry | None:
        table = self.query_one("#activity-table", DataTable)
        if not table.row_count:
            return None
        key = str(table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value)
        return self.entries[int(key)] if key.isdigit() and int(key) < len(self.entries) else None

    @on(DataTable.RowHighlighted, "#activity-table")
    def _row(self, event: DataTable.RowHighlighted) -> None:
        key = str(event.row_key.value) if event.row_key else ""
        if key.isdigit():
            self.show_entry(int(key))

    # -- filters and pages ----------------------------------------------------------

    @on(Segmented.Changed, "#activity-kind")
    def _kind(self, event: Segmented.Changed) -> None:
        if event.value and event.value != self.kind:
            self.kind, self.page = event.value, 0
            self.load()

    @on(Segmented.Changed, "#activity-client")
    def _client(self, event: Segmented.Changed) -> None:
        if event.value and event.value != self.client:
            self.client, self.page = event.value, 0
            self.load()

    def press_k(self) -> None:
        self.query_one("#activity-kind", Segmented).action_move(1)

    def press_c(self) -> None:
        self.query_one("#activity-client", Segmented).action_move(1)

    @on(Button.Pressed, "#entry-older")
    def press_pagedown(self) -> None:
        if self.more:
            self.page += 1
            self.load()

    @on(Button.Pressed, "#entry-newer")
    def press_pageup(self) -> None:
        if self.page > 0:
            self.page -= 1
            self.load()

    @on(Button.Pressed, "#entry-copy")
    def press_y(self) -> None:
        e = self.selected()
        if e is None:
            return
        if not self.app.backend.copy_text(e.raw):
            self.app.copy_to_clipboard(e.raw)
        self.app.notify("Copied the entry.")
