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
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from .. import remotepassword as rp
from .app import Confirm, ShowOnce, while_mounted

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


class LoginScreen(ModalScreen["str | None"]):
    """The user's login password, checked through PAM before the remote password changes."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        from ..loginpw import user

        with Vertical(id="confirm-box"):
            yield Static(f"First, the login password of {user()}. Whoever sets the remote "
                         "password gets remote access, so it must be you.", id="confirm-question")
            yield Input(placeholder="login password", password=True, id="login-pw")
            yield Static("", id="pw-error")
            with Horizontal(id="confirm-buttons"):
                yield Button("Cancel", id="login-cancel", compact=True)
                yield Button("Continue", id="login-ok", variant="primary", compact=True)

    def on_mount(self) -> None:
        self.query_one("#login-pw", Input).focus()

    @on(Button.Pressed, "#login-ok")
    @on(Input.Submitted, "#login-pw")
    def _submit(self) -> None:
        given = self.query_one("#login-pw", Input).value
        if not given:
            self.query_one("#pw-error", Static).update("Type your login password.")
            return
        self.query_one("#login-ok", Button).disabled = True
        self.query_one("#pw-error", Static).update("Checking...")
        self._check(given)

    @work(thread=True, exclusive=True, group="login-check")
    def _check(self, given: str) -> None:
        try:
            ok, error = self.app.backend.login_ok(given), ""
        except Exception as exc:  # noqa: BLE001 - no libpam: shown, nothing changes
            ok, error = False, str(getattr(exc, "message", exc))
        self.app.call_from_thread(self._checked, given, ok, error)

    def _checked(self, given: str, ok: bool, error: str) -> None:
        if ok:
            self.dismiss(given)
            return
        from ..remotepassword import wrong_login

        self.query_one("#pw-error", Static).update(error or wrong_login())
        self.query_one("#login-pw", Input).value = ""
        self.query_one("#login-ok", Button).disabled = False

    @on(Button.Pressed, "#login-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class PasswordScreen(ModalScreen["tuple[str, bool] | None"]):
    """Create or replace the remote password: generate a passphrase, or type one twice."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, replacing: bool) -> None:
        super().__init__()
        self.replacing = replacing

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(
                ("Replace the remote password. Remote apps that already connected keep their "
                 "sessions until they are revoked." if self.replacing else
                 "Turn on remote access. Remote clients type this password on the approval "
                 "page; local clients never use it."), id="confirm-question")
            yield Button("Generate a passphrase (six words)", id="pw-generate", variant="primary",
                         compact=True)
            yield Static(f"\nOr type your own, at least {rp.MIN_LENGTH} characters:", classes="hint")
            yield Input(placeholder="new password", password=True, id="pw-first")
            yield Input(placeholder="again", password=True, id="pw-second")
            yield Static("", id="pw-error")
            with Horizontal(id="confirm-buttons"):
                yield Button("Cancel", id="pw-cancel", compact=True)
                yield Button("Use this password", id="pw-typed", compact=True)

    @on(Button.Pressed, "#pw-generate")
    def _generate(self) -> None:
        self.dismiss((rp.generate(), True))

    @on(Button.Pressed, "#pw-typed")
    @on(Input.Submitted, "#pw-second")
    def _typed(self) -> None:
        first = self.query_one("#pw-first", Input).value
        second = self.query_one("#pw-second", Input).value
        why = rp.check(first) or (None if first == second else "The two entries differ.")
        if why:
            self.query_one("#pw-error", Static).update(why)
            return
        self.dismiss((first, False))

    @on(Input.Submitted, "#pw-first")
    def _next(self) -> None:
        self.query_one("#pw-second", Input).focus()

    @on(Button.Pressed, "#pw-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


def row_id(name: str) -> str:
    """A widget id for an app name, which may hold any character."""
    return hashlib.sha1(name.encode()).hexdigest()[:12]


class RemotePane(Vertical):
    DEFAULT_CSS = CSS

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.rows: dict[str, dict] = {}   # row id -> client group
        self.busy = False
        self.has_password = True

    def compose(self) -> ComposeResult:
        yield Static(
            "Remote apps that connected through the tunnel with the password and still hold "
            "tokens. Revoke cuts one off at once: its next call fails and it has to connect "
            "again with the password. A new password alone does not do this.", classes="hint")
        yield Static("Reading the remote clients...", id="remote-status", classes="hint")
        with Horizontal(id="remote-actions"):
            yield Button("Remote password...", id="remote-password", compact=True)
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
        self.has_password = info.get("has_password", True)
        self.query_one("#remote-password", Button).label = (
            "Replace password" if self.has_password else "Turn on remote access")
        if not self.has_password:
            lines.insert(0, "Remote access is off: no password is set, so the approval page "
                            "refuses every app and the tunnel does not open. Local clients do not "
                            "need one. \"Turn on remote access\" creates it.")
        lines.append(f"Tools for remote clients: {info.get('remote_profile', '?')}"
                     + ("" if info.get("remote_profile_set") else " (follows tools.profile)")
                     + "; change it in Settings > tools.remote_profile.")
        if info.get("static_token"):
            lines.append("The static token (auth.static_token) is not an app and is not listed: "
                         "anyone who has it can call pcbridge. Replace it in Settings.")
        status.update("\n".join(lines))
        self.query_one("#remote-revoke-all", Button).disabled = self.busy or not info.get("registrations")
        self.query_one("#remote-password", Button).disabled = self.busy
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

    # -- password ----------------------------------------------------------------

    @on(Button.Pressed, "#remote-password")
    def _password(self) -> None:
        if self.busy:
            return
        replacing = self.has_password

        def choose(login: str | None) -> None:
            if login:
                self.app.push_screen(PasswordScreen(replacing), lambda picked: self._save_password(
                    picked, login, replacing) if picked else None)

        self.app.push_screen(LoginScreen(), choose)

    def _save_password(self, picked: tuple[str, bool], login: str, replacing: bool) -> None:
        self.busy = True
        self._password_worker(picked[0], picked[1], login, replacing)

    @work(thread=True, group="remote-password")
    def _password_worker(self, password: str, generated: bool, login: str, replacing: bool) -> None:
        try:
            self.app.backend.set_remote_password(self.app.cfg, password, login)
            error = ""
        except Exception as exc:  # noqa: BLE001 - shown to the user
            error = str(getattr(exc, "message", exc))
        self.app.post(self._password_saved, password, generated, replacing, error)

    @while_mounted
    def _password_saved(self, password: str, generated: bool, replacing: bool, error: str) -> None:
        self.busy = False
        if error:
            self.app.notify(error, title="The password was not saved", severity="error", timeout=15)
            return
        url = getattr(self.app.cfg, "mcp_url", "")
        address = f"Remote address: {url}" if url else ""
        done = "replaced" if replacing else "created; remote access is on once the tunnel is open"
        self.app.notify(f"Remote password {done}. Only its hash is stored."
                        + ("" if generated else f"\n{address}"), timeout=12)
        self.app.reload_config()  # reads this tab again too
        # The offer to revoke waits until the new password is saved and gone.
        after = self.app.offer_revoke_after_password if replacing else (lambda: None)
        if generated:
            self.app.push_screen(ShowOnce("The remote password", password, address),
                                 lambda _: after())
        else:
            after()

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
