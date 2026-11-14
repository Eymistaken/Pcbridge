"""The Connections tab: which local agents and apps use pcbridge's tools.

A table in two groups, the clients on this computer and the ones not found,
each with a small switch: on when the client has an enabled pcbridge entry.
The selected client shows what a change would write, from `connect
--dry-run`, before anything is written. Turning one on registers
`<launcher> stdio`; turning it off switches the entry off where the client
allows it and removes it otherwise (`cli/connect.py`). Every file is backed
up first. Reading the states runs the clients' own commands, so it happens
in a worker; so does every change.
"""

from __future__ import annotations

import shlex
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Static

from .pane import Pane, while_mounted
from .theme import PALETTE
from .widgets import SectionTitle, key_label, set_kind

CSS = """
ConnectionsPane #conn-status {
    height: auto;
    margin-bottom: 1;
}
ConnectionsPane #conn-body {
    height: 1fr;
}
ConnectionsPane #conn-left {
    width: 3fr;
    height: 1fr;
}
ConnectionsPane #conn-table {
    height: 1fr;
}
ConnectionsPane #conn-right {
    width: 2fr;
    height: 1fr;
    margin-left: 4;
}
ConnectionsPane.narrow #conn-body {
    layout: vertical;
}
ConnectionsPane.narrow #conn-right {
    margin-left: 0;
    height: auto;
}
ConnectionsPane .kv {
    height: auto;
}
ConnectionsPane .kv .label {
    width: 8;
    color: $pb-muted;
}
ConnectionsPane .kv .value {
    width: 1fr;
}
ConnectionsPane #conn-badge {
    height: auto;
    color: $pb-warning;
    margin-top: 1;
}
ConnectionsPane #conn-diff {
    height: auto;
    margin-top: 1;
}
ConnectionsPane #conn-actions {
    height: auto;
    margin-top: 1;
}
ConnectionsPane .conn-more {
    height: auto;
    margin-top: 1;
}
"""

ON_STATES = ("connected", "outdated")


def mini_switch(on: bool, disabled: bool = False) -> Text:
    """A one-row switch: the knob right in green when on, left in red when off."""
    if disabled:
        return Text("[ ·· ]", style=PALETTE["dim"])
    if on:
        return Text.assemble(("[ ", PALETTE["rule"]), ("━●", PALETTE["ok"]), (" ]", PALETTE["rule"]))
    return Text.assemble(("[ ", PALETTE["rule"]), ("●━", "#c8434b"), (" ]", PALETTE["rule"]))


def state_text(state: str, busy: bool = False) -> Text:
    if busy:
        return Text("⠹ working…", style=PALETTE["working"])
    return {
        "connected": Text("● connected", style=PALETTE["ok"]),
        "outdated": Text("▲ old command", style=PALETTE["warning"]),
        "switched off": Text("○ off", style=PALETTE["dim"]),
        "not connected": Text("○ off", style=PALETTE["dim"]),
        "not installed": Text("not installed", style=PALETTE["dim"]),
    }.get(state, Text(state, style=PALETTE["dim"]))


def short_path(path: str) -> str:
    import os

    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path and path.startswith(home) else (path or "")


class ConnectionsPane(Pane):
    FOOTER = [("↑↓", "Client", "key:none"), ("space", "Connect/Disconnect", "key:space"),
              ("u", "Update", "key:u"), ("g", "Look again", "key:g")]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.rows: dict[str, dict] = {}
        self.busy: set[str] = set()
        self.changed: dict[str, str] = {}     # client -> the backup of its last change
        self.selected: str | None = None
        self._preview_request = 0

    def compose(self) -> ComposeResult:
        yield Static("Reading the clients…", id="conn-status")
        with Horizontal(id="conn-body"):
            with Vertical(id="conn-left"):
                yield DataTable(id="conn-table", cursor_type="row")
                yield Static("Every file is backed up before it changes. A running client picks the "
                             "change up when it restarts.", classes="hint")
            with Vertical(id="conn-right"):
                yield SectionTitle("", upper=False, id="conn-title", classes="first")
                for key, label in (("state", "State"), ("runs", "Runs"), ("file", "File"),
                                   ("backup", "Backup")):
                    with Horizontal(classes="kv"):
                        yield Static(label, classes="label")
                        yield Static("", id=f"conn-{key}", classes="value")
                yield Static("", id="conn-badge")
                yield Static("", id="conn-diff", classes="box")
                with Horizontal(id="conn-actions"):
                    yield Button(key_label("Update the entry", "u"), id="conn-update",
                                 classes="primary", compact=True)
                    yield Button("Connect", id="conn-toggle", compact=True)
                with Horizontal(classes="conn-more"):
                    yield Button("Open the file", id="conn-open", compact=True)
                    yield Button(key_label("Look again", "g"), id="conn-again", compact=True)

    def on_mount(self) -> None:
        self.set_class(self.app.size.width < 100, "narrow")
        table = self.query_one("#conn-table", DataTable)
        table.add_columns(" ", "CLIENT", "STATE", "CONFIG FILE")
        self.reload()

    def on_resize(self) -> None:
        self.set_class(self.app.size.width < 100, "narrow")

    def reload(self) -> None:
        self._load()

    @work(thread=True, exclusive=True, group="connections")
    def _load(self) -> None:
        try:
            rows = self.app.backend.clients()
            error = ""
        except Exception as exc:  # noqa: BLE001 - shown in the tab
            rows, error = [], str(exc)
        self.app.post(self._loaded, rows, error)

    @while_mounted
    def _loaded(self, rows: list[dict], error: str) -> None:
        status = self.query_one("#conn-status", Static)
        if error:
            status.update(Text(f"✕ The clients could not be read: {error}", style=PALETTE["error"]))
            return
        self.rows = {r["client"]: r for r in rows}
        on = sum(1 for r in rows if r["state"] in ON_STATES)
        line = Text()
        line.append(f"{on} of {len(rows)} local clients", style="bold")
        line.append(" use pcbridge through ", style=PALETTE["muted"])
        line.append("pcbridge stdio", style=PALETTE["accent"])
        line.append(", a relay to the daemon. No network, no password.", style=PALETTE["muted"])
        status.update(line)
        overview = self.app.panes.get("overview")
        if overview is not None:
            overview.clients_loaded(rows)
        self.fill()

    def order(self) -> list[str]:
        here = [c for c, r in self.rows.items() if r["state"] != "not installed"]
        missing = [c for c, r in self.rows.items() if r["state"] == "not installed"]
        return here + missing

    def fill(self) -> None:
        table = self.query_one("#conn-table", DataTable)
        keep = self.selected
        table.clear()
        here = [c for c in self.order() if self.rows[c]["state"] != "not installed"]
        missing = [c for c in self.order() if self.rows[c]["state"] == "not installed"]
        for title, group in (("on this computer", here), ("not found", missing)):
            if not group:
                continue
            table.add_row(Text(""), Text(f"── {title} ", style=PALETTE["dim"]), Text(""), Text(""),
                          key=f"group:{title}")
            for cid in group:
                r = self.rows[cid]
                busy = cid in self.busy
                installed = r["state"] != "not installed"
                table.add_row(mini_switch(r["state"] in ON_STATES, disabled=not installed or busy),
                              Text(r["name"], style="bold" if installed else PALETTE["dim"]),
                              state_text(r["state"], busy),
                              Text(short_path(str(r.get("config") or "")), style=PALETTE["dim"]),
                              key=cid)
        keys = [str(k.value) for k in table.rows]
        target = keep if keep in keys else next((k for k in keys if not k.startswith("group:")), None)
        if target is not None:
            table.move_cursor(row=keys.index(target))
            self.show_client(target)

    @on(DataTable.RowHighlighted, "#conn-table")
    def _row(self, event: DataTable.RowHighlighted) -> None:
        key = str(event.row_key.value) if event.row_key else ""
        if key.startswith("group:"):
            return
        if key and key != self.selected:
            self.show_client(key)

    @on(DataTable.CellSelected, "#conn-table")
    def _cell(self, event: DataTable.CellSelected) -> None:
        key = str(event.cell_key.row_key.value)
        if event.coordinate.column == 0 and key in self.rows:
            self.toggle(key)

    def show_client(self, cid: str) -> None:
        r = self.rows.get(cid)
        if r is None:
            return
        self.selected = cid
        installed = r["state"] != "not installed"
        self.query_one("#conn-title", SectionTitle).set(r["name"])
        self.query_one("#conn-state", Static).update(state_text(r["state"], cid in self.busy))
        runs = shlex.join(r["command"]) if r.get("command") else "nothing yet"
        self.query_one("#conn-runs", Static).update(Text(runs, style=PALETTE["dim"]))
        self.query_one("#conn-file", Static).update(Text(short_path(str(r.get("config") or "")),
                                                         style=PALETTE["dim"]))
        self.query_one("#conn-backup", Static).update(
            Text(self.changed.get(cid) or "made before every change", style=PALETTE["dim"]))
        badge = self.query_one("#conn-badge", Static)
        badge.update(f"▲ restart {r['name']} to pick this up" if cid in self.changed else "")
        badge.display = cid in self.changed
        update = self.query_one("#conn-update", Button)
        toggle = self.query_one("#conn-toggle", Button)
        update.display = r["state"] == "outdated"
        update.disabled = cid in self.busy
        on = r["state"] in ON_STATES
        toggle.label = key_label("Disconnect" if on else "Connect", "space")
        set_kind(toggle, "" if on else ("" if r["state"] == "outdated" else "primary"))
        toggle.disabled = cid in self.busy or not installed
        toggle.display = installed
        self.query_one("#conn-open", Button).display = installed and bool(r.get("config"))
        diff = self.query_one("#conn-diff", Static)
        if not installed:
            diff.update(Text(f"Install {r['name']}, then press Look again g.", style=PALETTE["muted"]))
            return
        diff.update(Text("Reading what a change would write…", style=PALETTE["dim"]))
        self._preview_request += 1
        self._preview_worker(self._preview_request, cid, not on or r["state"] == "outdated")

    @work(thread=True, exclusive=True, group="connections-preview")
    def _preview_worker(self, request: int, cid: str, connect: bool) -> None:
        try:
            status, detail = self.app.backend.connection_preview(cid, connect)
        except Exception as exc:  # noqa: BLE001 - shown in the tab
            status, detail = "warn", str(exc)
        self.app.post(self._preview_loaded, request, cid, connect, status, detail)

    @while_mounted
    def _preview_loaded(self, request: int, cid: str, connect: bool, status: str, detail: str) -> None:
        if request != self._preview_request or cid != self.selected:
            return
        out = Text()
        verb = "Connect" if connect else "Disconnect"
        out.append(f"what {verb.lower()} would write\n", style=f"bold {PALETTE['muted']}")
        if status == "plan" and " -> " in detail:
            change, _, where = detail.rpartition("  [")
            before, _, after = change.partition(" -> ")
            out.append(f"  {where.rstrip(']')}\n", style=PALETTE["dim"])
            if before and before != "(not registered)":
                out.append(f"- {before}\n", style=PALETTE["error"])
            out.append(f"+ {after}" if after != "off" else "+ (switched off or removed)",
                       style=PALETTE["ok"])
        elif status == "ok":
            out.append(f"  nothing: {detail}", style=PALETTE["dim"])
        else:
            out.append(f"  {detail}", style=PALETTE["warning"])
        self.query_one("#conn-diff", Static).update(out)

    # -- changes ---------------------------------------------------------------

    def toggle(self, cid: str) -> None:
        r = self.rows.get(cid)
        if r is None or cid in self.busy or r["state"] == "not installed":
            return
        self._change(cid, connect=r["state"] not in ON_STATES)

    @on(Button.Pressed, "#conn-toggle")
    def _toggle_button(self) -> None:
        if self.selected:
            self.toggle(self.selected)

    @on(Button.Pressed, "#conn-update")
    def key_u(self) -> None:
        cid = self.selected
        if cid and self.rows.get(cid, {}).get("state") == "outdated" and cid not in self.busy:
            self._change(cid, connect=True)

    @on(Button.Pressed, "#conn-open")
    def _open(self) -> None:
        r = self.rows.get(self.selected or "")
        if r and r.get("config"):
            try:
                self.app.backend.open_file(str(r["config"]))
            except Exception as exc:  # noqa: BLE001
                self.app.notify(f"The file could not be opened: {exc}", severity="error")

    @on(Button.Pressed, "#conn-again")
    def key_g(self) -> None:
        self.query_one("#conn-status", Static).update(Text("Reading the clients…", style=PALETTE["dim"]))
        self.reload()

    def key_space(self) -> None:
        if self.selected:
            self.toggle(self.selected)

    def key_none(self) -> None:
        pass

    def _change(self, cid: str, connect: bool) -> None:
        self.busy.add(cid)
        self.fill()
        self._change_worker(cid, connect)

    @work(thread=True, group="connections-change")
    def _change_worker(self, cid: str, connect: bool) -> None:
        try:
            status, detail = self.app.backend.set_connection(cid, connect)
        except Exception as exc:  # noqa: BLE001
            status, detail = "warn", str(exc)
        self.app.post(self._changed, cid, connect, status, detail)

    @while_mounted
    def _changed(self, cid: str, connect: bool, status: str, detail: str) -> None:
        self.busy.discard(cid)
        name = self.rows.get(cid, {}).get("name", cid)
        verb = "connected" if connect else "disconnected"
        if status == "warn":
            self.app.notify(f"{name}: {detail}", title="Not changed", severity="error", timeout=12)
        else:
            backup = detail.rsplit("(backup in ", 1)[1].rstrip(")") if "(backup in " in detail else ""
            self.changed[cid] = short_path(backup) if backup else "none needed"
            self.app.notify(f"{name} {verb}. Restart {name} to pick this up.", timeout=8)
        self.reload()
