"""The Remote tab: the remote apps that hold OAuth tokens, each with Revoke,
and which tools remote clients get.

A remote app is one name (ChatGPT, Gemini, ...) with every registration it
made; revoking it deletes them all with their tokens, so its next call
fails and it has to connect again with the password (`remoteclients.py`).
Reading and revoking go through the backend in a worker.
"""

from __future__ import annotations

import hashlib
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Static

from .app import Confirm, while_mounted

CSS = """
RemotePane .remote-row {
    height: auto;
    margin-bottom: 1;
}
RemotePane .remote-text {
    width: 1fr;
}
RemotePane #remote-actions {
    height: auto;
    margin-bottom: 1;
}
RemotePane #remote-actions Button {
    margin-right: 2;
}
"""


def row_id(name: str) -> str:
    """A widget id for an app name, which may hold any character."""
    return hashlib.sha1(name.encode()).hexdigest()[:12]


class RemotePane(Vertical):
    DEFAULT_CSS = CSS

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.rows: dict[str, dict] = {}   # row id -> client group
        self.busy = False

    def compose(self) -> ComposeResult:
        yield Static(
            "Remote apps that connected through the tunnel with the password and still hold "
            "tokens. Revoke cuts one off at once: its next call fails and it has to connect "
            "again with the password. A new password alone does not do this.", classes="hint")
        yield Static("Reading the remote clients...", id="remote-status", classes="hint")
        with Horizontal(id="remote-actions"):
            yield Button("Revoke all", id="remote-revoke-all", variant="error", compact=True,
                         disabled=True)
            yield Button("Refresh", id="remote-refresh", compact=True)
        yield VerticalScroll(id="remote-list")

    def on_mount(self) -> None:
        self.reload()

    def reload(self) -> None:
        self._load()

    @work(thread=True, exclusive=True, group="remote")
    def _load(self) -> None:
        try:
            info = self.app.backend.remote_clients(self.app.cfg)
            error = ""
        except Exception as exc:  # noqa: BLE001 - shown in the tab
            info, error = {}, str(exc)
        self.app.post(self._loaded, info, error)

    @while_mounted
    def _loaded(self, info: dict, error: str) -> None:
        status = self.query_one("#remote-status", Static)
        listing = self.query_one("#remote-list", VerticalScroll)
        if error:
            status.update(f"The remote clients could not be read: {error}")
            return
        groups = info.get("clients", [])
        live = sum(g["connected"] for g in groups)
        lines = [f"{len(groups)} app(s) connected ({live} registration(s) with live tokens)"
                 if groups else "No remote app holds a token."]
        lines.append(f"Tools for remote clients: {info.get('remote_profile', '?')}"
                     + ("" if info.get("remote_profile_set") else " (follows tools.profile)")
                     + "; change it in Settings > tools.remote_profile.")
        if info.get("static_token"):
            lines.append("The static token (auth.static_token) is not an app and is not listed: "
                         "anyone who has it can call pcbridge. Replace it in Settings.")
        status.update("\n".join(lines))
        self.query_one("#remote-revoke-all", Button).disabled = self.busy or not info.get("registrations")
        listing.remove_children()
        self.rows = {}
        for g in groups:
            rid = row_id(g["name"])
            self.rows[rid] = g
            listing.mount(Horizontal(
                Static(self._describe(g), classes="remote-text"),
                Button("Revoke", id=f"remote-revoke-{rid}", classes="remote-revoke", variant="error",
                       compact=True, disabled=self.busy),
                classes="remote-row",
            ))

    def _describe(self, g: dict) -> Text:
        from ..remoteclients import describe_expiry

        out = Text()
        out.append(g["name"], style="bold")
        out.append(f"  {g['connected']} connected", style="")
        out.append(f", {g['registrations']} registration(s)", style="dim")
        out.append(f"\nlast connected {g['last']}, tokens expire {describe_expiry(g['expires'])}",
                   style="dim")
        return out

    # -- revoke ------------------------------------------------------------------

    @on(Button.Pressed, "#remote-refresh")
    def _refresh(self) -> None:
        self.reload()

    @on(Button.Pressed, ".remote-revoke")
    def _revoke_one(self, event: Button.Pressed) -> None:
        g = self.rows.get((event.button.id or "").removeprefix("remote-revoke-"))
        if g is None or self.busy:
            return
        self.app.push_screen(Confirm(
            f"Revoke {g['name']}?\n\nIts {g['registrations']} registration(s) and every token are "
            "deleted. Its next call fails; to use pcbridge again it has to connect again and enter "
            "the password.", yes="Revoke"),
            lambda yes: self._start(g["name"]) if yes else None)

    @on(Button.Pressed, "#remote-revoke-all")
    def _revoke_all(self) -> None:
        if not self.busy:
            self.confirm_revoke_all("Revoke every remote app?\n\nEvery registration and every token "
                                    "is deleted; each app has to connect again with the password.")

    def confirm_revoke_all(self, question: str) -> None:
        self.app.push_screen(Confirm(question, yes="Revoke all"),
                             lambda yes: self._start(None) if yes else None)

    def _start(self, name: str | None) -> None:
        self.busy = True
        for button in self.query(Button):
            if button.id != "remote-refresh":
                button.disabled = True
        self._revoke_worker(name)

    @work(thread=True, group="remote-revoke")
    def _revoke_worker(self, name: str | None) -> None:
        try:
            msg, ok = self.app.backend.revoke_remote(self.app.cfg, name), True
        except Exception as exc:  # noqa: BLE001
            msg, ok = str(exc), False
        self.app.post(self._revoked, msg, ok)

    @while_mounted
    def _revoked(self, msg: str, ok: bool) -> None:
        self.busy = False
        if ok:
            self.app.notify(msg, timeout=10)
        else:
            self.app.notify(msg, title="Not revoked", severity="error", timeout=12)
        self.reload()
