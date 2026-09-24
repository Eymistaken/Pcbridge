"""The Connections tab: which local agents and apps can use pcbridge's
tools, each with a switch.

A switch is on when the client has an enabled pcbridge entry. Turning it on
registers `<launcher> stdio`; turning it off switches the entry off where the
client allows it and removes it otherwise (`cli/connect.py`). An entry that
runs an older command stays on and offers Update. Reading the states runs
the clients' own commands, so it happens in a worker; so does every change.
"""

from __future__ import annotations

import shlex
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Static, Switch

from .app import while_mounted

CSS = """
ConnectionsPane .conn-row {
    height: auto;
    margin-bottom: 1;
}
ConnectionsPane .conn-row Switch {
    margin-right: 1;
}
ConnectionsPane .conn-text {
    width: 1fr;
}
ConnectionsPane .conn-update {
    margin-left: 1;
}
"""


class ConnectionsPane(Vertical):
    DEFAULT_CSS = CSS

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.rows: dict[str, dict] = {}
        self.busy: set[str] = set()
        self.quiet = False  # set while switches are moved by code, not by a person

    def compose(self) -> ComposeResult:
        yield Static(
            "Agents and apps on this computer that can use pcbridge's tools. A switch connects or "
            "disconnects one; every file it changes is backed up first. A client that is running "
            "picks the change up when it restarts.", classes="hint")
        yield Static("Reading the clients...", id="conn-status", classes="hint")
        yield VerticalScroll(id="conn-list")

    def on_mount(self) -> None:
        self.reload()

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
            status.update(f"The clients could not be read: {error}")
            return
        on = sum(1 for r in rows if r["state"] in ("connected", "outdated"))
        status.update(f"{on} of {len(rows)} connected")
        listing = self.query_one("#conn-list", VerticalScroll)
        if set(self.rows) != {r["client"] for r in rows}:
            listing.remove_children()
            for r in rows:
                cid = r["client"]
                listing.mount(Horizontal(
                    Switch(value=False, id=f"conn-switch-{cid}"),
                    Static("", id=f"conn-text-{cid}", classes="conn-text"),
                    Button("Update", id=f"conn-update-{cid}", classes="conn-update", compact=True),
                    classes="conn-row",
                ))
        self.rows = {r["client"]: r for r in rows}
        self.call_after_refresh(self._paint)

    @while_mounted
    def _paint(self) -> None:
        self.quiet = True
        try:
            for cid, r in self.rows.items():
                switch = self.query_one(f"#conn-switch-{cid}", Switch)
                switch.value = r["state"] in ("connected", "outdated")
                switch.disabled = r["state"] == "not installed" or cid in self.busy
                self.query_one(f"#conn-update-{cid}", Button).display = (
                    r["state"] == "outdated" and cid not in self.busy)
                self.query_one(f"#conn-text-{cid}", Static).update(self._describe(r))
        finally:
            self.call_after_refresh(self._unquiet)

    def _unquiet(self) -> None:
        self.quiet = False

    def _describe(self, r: dict) -> Text:
        out = Text()
        out.append(r["name"], style="bold")
        state = "working..." if r["client"] in self.busy else r["state"]
        out.append(f"  {state}", style="" if state in ("connected", "working...") else "dim")
        if r.get("setup_default"):
            out.append("  (set up by pcbridge setup)", style="dim")
        out.append(f"\n{r['config']}", style="dim")
        if r["state"] == "outdated" and r.get("command"):
            out.append(f"\nruns {shlex.join(r['command'])}; Update points it at this pcbridge", style="dim")
        if r.get("note"):
            out.append(f"\n{r['note']}", style="dim")
        return out

    # -- changes ---------------------------------------------------------------

    @on(Switch.Changed)
    def _switched(self, event: Switch.Changed) -> None:
        sid = event.switch.id or ""
        if self.quiet or not sid.startswith("conn-switch-"):
            return
        cid = sid.removeprefix("conn-switch-")
        r = self.rows.get(cid)
        if r is None or cid in self.busy:
            return
        now_on = r["state"] in ("connected", "outdated")
        if event.value == now_on:
            return
        self._change(cid, connect=event.value)

    @on(Button.Pressed, ".conn-update")
    def _update(self, event: Button.Pressed) -> None:
        cid = (event.button.id or "").removeprefix("conn-update-")
        if cid in self.rows and cid not in self.busy:
            self._change(cid, connect=True)

    def _change(self, cid: str, connect: bool) -> None:
        self.busy.add(cid)
        self._paint()
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
            self.app.notify(f"{name} {verb}: {detail}", timeout=8)
        self.reload()
