"""The Remote tab: the tunnel, the public address, access, and the remote apps
that hold OAuth tokens.

Four numbered sections. The tunnel's switch opens Tailscale Funnel (it asks
first: that exposes this machine) and closes it at once. A remote app is one
name (ChatGPT, Gemini, ...) with every registration it made; revoking it
deletes them all with their tokens, so its next call fails and it has to
connect again with the password (`remoteclients.py`). Registrations that
hold no token and are 30 days old or more can be cleaned up; live sessions
stay. Reading and every change go through the backend in a worker.
"""

from __future__ import annotations

import hashlib
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Static, Switch

from .. import remotepassword as rp
from .dialogs import Confirm, ShowOnce
from .pane import ScrollPane, while_mounted
from .theme import PALETTE
from .widgets import CheckLine, RestartBanner, SectionTitle, Segmented, key_label

CSS = """
RemotePane .two {
    height: auto;
}
RemotePane #remote-left {
    width: 1fr;
    height: auto;
}
RemotePane #remote-right {
    width: 1fr;
    height: auto;
    margin-left: 4;
}
RemotePane.narrow .two {
    layout: vertical;
}
RemotePane.narrow #remote-right {
    margin-left: 0;
}
RemotePane #tunnel-row {
    height: auto;
}
RemotePane #tunnel-row Switch {
    margin-right: 2;
}
RemotePane #tunnel-text {
    width: 1fr;
}
RemotePane #address-row {
    height: 1;
    margin-top: 1;
}
RemotePane #address {
    width: 1fr;
    background: $pb-input;
    padding: 0 1;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}
RemotePane #address-row Button {
    margin-left: 1;
}
RemotePane #copied {
    height: 1;
}
RemotePane .kv {
    height: auto;
    margin-top: 1;
}
RemotePane .kv .label {
    width: 14;
    color: $pb-muted;
}
RemotePane .kv .value {
    width: 1fr;
}
RemotePane #password-replace {
    margin-left: 1;
}
RemotePane #profile-notes {
    padding-left: 14;
    height: auto;
}
RemotePane #remote-apps {
    height: auto;
}
RemotePane #apps-table {
    height: auto;
    max-height: 12;
}
RemotePane #apps-confirm {
    height: 1;
    display: none;
}
RemotePane #apps-confirm.shown {
    display: block;
}
RemotePane #apps-confirm-text {
    width: auto;
    max-width: 30;
    padding-right: 1;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}
RemotePane #apps-actions {
    height: 1;
    margin-top: 1;
}
RemotePane #apps-actions-fill {
    width: 1fr;
}
RemotePane #stale-rule {
    color: $pb-dim;
}
"""

PROFILE_NOTES = {
    "same": "follow the local profile ({local})",
    "full": "every tool",
    "core": "no desktop tools: agents, jobs, shell, files, tmux, status",
    "desktop": "the desktop tools plus job and status tools",
    "readonly": "only tools that change nothing",
}

CLAUDE_AI = ("In claude.ai: Settings › Connectors › Add custom connector, paste the address, then "
             "type the remote password on the approval page. ChatGPT and Gemini work the same way.")


class LoginScreen(ModalScreen["str | None"]):
    """The user's login password, checked through PAM before the remote password changes."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        from ..loginpw import user

        with Vertical(id="confirm-box", classes="dialog"):
            yield Static("First, your login password", id="confirm-question", classes="dialog-title")
            yield Static("Whoever sets the remote password gets remote access, so it must be you: "
                         f"the login password of {user()}.", classes="dialog-text")
            with Horizontal(classes="dialog-row"):
                yield Static("Password", classes="label")
                yield Input(placeholder="login password", password=True, id="login-pw")
            yield Static("", id="pw-error")
            with Horizontal(classes="dialog-buttons"):
                yield Button(key_label("Cancel", "esc"), id="login-cancel", compact=True)
                yield Button(key_label("Continue", "enter"), id="login-ok", classes="primary", compact=True)

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
        self.query_one("#pw-error", Static).update(Text("Checking…", style=PALETTE["muted"]))
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
        self.query_one("#pw-error", Static).update(error or rp.wrong_login())
        self.query_one("#login-pw", Input).value = ""
        self.query_one("#login-ok", Button).disabled = False

    @on(Button.Pressed, "#login-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class PasswordScreen(ModalScreen["tuple[str, bool, bool] | None"]):
    """Create or replace the remote password: generate a passphrase, or type
    one twice with live checks. Returns (password, generated, sign out the apps)."""

    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("g", "generate", "Generate", show=False)]

    def __init__(self, replacing: bool, live_apps: int = 0) -> None:
        super().__init__()
        self.replacing = replacing
        self.live_apps = live_apps

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box", classes="dialog"):
            yield Static("Replace the remote password" if self.replacing else "Turn on remote access",
                         id="confirm-question", classes="dialog-title")
            if not self.replacing:
                yield Static("Remote clients type this password on the approval page; local clients "
                             "never use it.", classes="dialog-text")
            yield Button(key_label("Generate a passphrase, six words", "g"), id="pw-generate",
                         classes="primary", compact=True)
            yield Static(Text("─── or type your own " + "─" * 38, style=PALETTE["rule"]), classes="dialog-row")
            with Horizontal(classes="dialog-row"):
                yield Static("New", classes="label")
                yield Input(placeholder=f"at least {rp.MIN_LENGTH} characters", password=True,
                            id="pw-first")
                yield Static("", id="pw-first-check", classes="pw-check")
            with Horizontal(classes="dialog-row"):
                yield Static("Again", classes="label")
                yield Input(placeholder="the same again", password=True, id="pw-second")
                yield Static("", id="pw-second-check", classes="pw-check")
            if self.replacing and self.live_apps:
                yield CheckLine(f"Also sign out the {self.live_apps} app"
                                f"{'s' if self.live_apps != 1 else ''} that hold tokens; a new "
                                "password does not end their sessions", value=True, id="pw-signout")
            yield Static("", id="pw-error")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Show", id="pw-show", compact=True)
                yield Button(key_label("Cancel", "esc"), id="pw-cancel", compact=True)
                yield Button(key_label("Set the password", "enter"), id="pw-typed", classes="primary",
                             compact=True, disabled=True)

    def on_mount(self) -> None:
        self._checks()

    def _sign_out(self) -> bool:
        found = self.query("#pw-signout")
        return bool(found) and found.first(CheckLine).value

    @on(Input.Changed)
    def _checks(self) -> None:
        first = self.query_one("#pw-first", Input).value
        second = self.query_one("#pw-second", Input).value
        a = self.query_one("#pw-first-check", Static)
        b = self.query_one("#pw-second-check", Static)
        long_enough = len(first) >= rp.MIN_LENGTH and rp.check(first) is None
        if not first:
            a.update("")
        elif long_enough:
            a.update(Text(f"✓ {len(first)} chars", style=PALETTE["ok"]))
        else:
            a.update(Text(f"{len(first)} of {rp.MIN_LENGTH}", style=PALETTE["warning"]))
        same = bool(first) and first == second
        if not second:
            b.update("")
        else:
            b.update(Text("✓ same", style=PALETTE["ok"]) if same else Text("✕ differs", style=PALETTE["error"]))
        self.query_one("#pw-typed", Button).disabled = not (long_enough and same)

    @on(Button.Pressed, "#pw-generate")
    def action_generate(self) -> None:
        self.dismiss((rp.generate(), True, self._sign_out()))

    @on(Button.Pressed, "#pw-show")
    def _show(self, event: Button.Pressed) -> None:
        first, second = self.query_one("#pw-first", Input), self.query_one("#pw-second", Input)
        hidden = not first.password
        first.password = second.password = hidden
        event.button.label = "Show" if hidden else "Hide"

    @on(Button.Pressed, "#pw-typed")
    @on(Input.Submitted, "#pw-second")
    def _typed(self) -> None:
        first = self.query_one("#pw-first", Input).value
        second = self.query_one("#pw-second", Input).value
        why = rp.check(first) or (None if first == second else "The two entries differ.")
        if why:
            self.query_one("#pw-error", Static).update(why)
            return
        self.dismiss((first, False, self._sign_out()))

    @on(Input.Submitted, "#pw-first")
    def _next(self) -> None:
        self.query_one("#pw-second", Input).focus()

    @on(Button.Pressed, "#pw-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


def row_id(name: str) -> str:
    """A row key for an app name, which may hold any character."""
    return hashlib.sha1(name.encode()).hexdigest()[:12]


class RemotePane(ScrollPane):
    FOOTER = [("t", "Tunnel on/off", "key:t"), ("y", "Copy address", "key:y"),
              ("p", "Password", "key:p"), ("x", "Revoke", "key:x")]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.rows: dict[str, dict] = {}   # row key -> client group
        self.info: dict = {}
        self.busy = False
        self.has_password = True
        self.tunnel_open = False
        self.revoking: str | None = None  # the row asking "sign it out?"
        self.quiet = False

    def compose(self) -> ComposeResult:
        yield RestartBanner()
        with Horizontal(classes="two"):
            with Vertical(id="remote-left"):
                yield SectionTitle("1  Tunnel", classes="first")
                with Horizontal(id="tunnel-row"):
                    yield Switch(id="tunnel-switch")
                    yield Static("Reading the tunnel…", id="tunnel-text")
                yield Static("The tunnel never starts at login: the service listens on 127.0.0.1, the "
                             "tunnel is what exposes this machine.", classes="hint")
                yield SectionTitle("2  Address")
                with Horizontal(id="address-row"):
                    yield Static("", id="address")
                    yield Button(key_label("Copy", "y"), id="address-copy", compact=True)
                yield Static("", id="copied")
                yield Static(CLAUDE_AI, classes="muted")
            with Vertical(id="remote-right"):
                yield SectionTitle("3  Access", classes="first")
                with Horizontal(classes="kv"):
                    yield Static("Password", classes="label")
                    yield Static("", id="password-state", classes="value")
                    yield Button(key_label("Replace…", "p"), id="password-replace", compact=True)
                with Horizontal(classes="kv"):
                    yield Static("Remote tools", classes="label")
                    yield Segmented([(p, p) for p in ("same", "full", "core", "desktop", "readonly")],
                                    id="remote-profile")
                yield Static("", id="profile-notes", classes="muted")
        with Vertical(id="remote-apps"):
            yield SectionTitle("4  Connected apps", id="apps-title")
            yield Static("", id="remote-status", classes="muted")
            yield DataTable(id="apps-table", cursor_type="row")
            with Horizontal(id="apps-confirm"):
                yield Static("", id="apps-confirm-text")
                yield Button("Revoke", id="apps-confirm-yes", classes="danger", compact=True)
                yield Button("Keep", id="apps-confirm-no", compact=True)
            with Horizontal(id="apps-actions"):
                yield Button("Clean up", id="stale-cleanup", compact=True)
                yield Static("", id="apps-actions-fill")
                yield Button("Revoke every app…", id="remote-revoke-all", classes="quiet",
                             compact=True)
            yield Static("", id="stale-rule")

    def on_mount(self) -> None:
        self.set_class(self.app.size.width < 100, "narrow")
        self.query_one("#apps-table", DataTable).add_columns(
            "NAME", "LIVE", "REGISTERED", "LAST CONNECTED", "TOKEN ENDS", " ")
        self.reload()

    def on_resize(self) -> None:
        self.set_class(self.app.size.width < 100, "narrow")

    def config_changed(self) -> None:
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
        if error:
            status.update(Text(f"✕ The remote clients could not be read: {error}", style=PALETTE["error"]))
            return
        self.info = info
        self.has_password = info.get("has_password", True)
        self._render_tunnel(bool(info.get("tunnel")))
        self.query_one("#address", Static).update(info.get("url") or "not set (public_url)")
        password = Text()
        if self.has_password:
            password.append("✓ set", style=PALETTE["ok"])
            password.append(" · stored as a hash", style=PALETTE["muted"])
        else:
            password.append("not set: remote access is off", style=PALETTE["warning"])
        self.query_one("#password-state", Static).update(password)
        replace = self.query_one("#password-replace", Button)
        replace.label = key_label("Replace…" if self.has_password else "Create…", "p")
        replace.disabled = self.busy
        self.quiet = True
        setting = info.get("remote_profile_setting", "same")
        self.query_one("#remote-profile", Segmented).value = setting
        self.call_after_refresh(self._unquiet)
        self._render_profile_notes(setting)
        self._render_apps(info)

    def _unquiet(self) -> None:
        self.quiet = False

    def _render_tunnel(self, is_open: bool) -> None:
        self.tunnel_open = is_open
        self.quiet = True
        switch = self.query_one("#tunnel-switch", Switch)
        switch.value = is_open
        switch.disabled = self.busy
        self.call_after_refresh(self._unquiet)
        out = Text()
        if is_open:
            out.append("◉ Open", style=f"bold {PALETTE['warning']}")
            out.append("\nTailscale Funnel · reachable from the internet", style=PALETTE["muted"])
        else:
            out.append("○ Closed", style=f"bold {PALETTE['muted']}")
            out.append("\nRemote apps cannot reach pcbridge. Local clients are not affected.",
                       style=PALETTE["muted"])
        self.query_one("#tunnel-text", Static).update(out)

    def _render_profile_notes(self, setting: str) -> None:
        counts = self.info.get("profile_counts") or {}
        local = getattr(self.app.cfg, "tools_profile", "full") if self.app.cfg is not None else "full"
        out = Text()
        effective = local if setting == "same" else setting
        note = PROFILE_NOTES.get(setting, "").format(local=local)
        out.append(f"{setting}: {note}")
        if effective in counts:
            out.append(f", {counts[effective]} tools")
        out.append(". Applies to the static token too.")
        self.query_one("#profile-notes", Static).update(out)

    def _render_apps(self, info: dict) -> None:
        from ..remoteclients import describe_expiry

        groups = info.get("clients", [])
        table = self.query_one("#apps-table", DataTable)
        table.clear()
        self.rows = {}
        n = len(groups)
        self.query_one("#apps-title", SectionTitle).set(
            extra=f"{n} app{'s' if n != 1 else ''} hold{'s' if n == 1 else ''} tokens" if n else "")
        lines = []
        if not self.has_password:
            lines.append("No password is set, so the approval page refuses every app and the tunnel "
                         "does not open. Local clients do not need one.")
        if info.get("static_token"):
            lines.append("The static token (auth.static_token) is not an app and is not listed: anyone "
                         "who has it can call pcbridge. Replace it in Advanced.")
        if not groups:
            lines.insert(0, "No remote app holds a token.")
        self.query_one("#remote-status", Static).update("\n".join(lines))
        for g in groups:
            rid = row_id(g["name"])
            self.rows[rid] = g
            registered = Text(str(g["registrations"]))
            if g.get("stale"):
                registered.append(f" ({g['stale']} stale)", style=PALETTE["warning"])
            table.add_row(Text(g["name"], style="bold"), str(g["connected"]), registered, g["last"],
                          describe_expiry(g["expires"]), Text("Revoke", style=PALETTE["error"]), key=rid)
        table.display = bool(groups)
        stale = int(info.get("stale") or 0)
        cleanup = self.query_one("#stale-cleanup", Button)
        cleanup.label = f"Clean up {stale} stale registration{'s' if stale != 1 else ''}"
        cleanup.disabled = self.busy or not stale
        self.query_one("#stale-rule", Static).update(
            "no token, registered 30 days ago or more; live sessions stay")
        self.query_one("#remote-revoke-all", Button).disabled = self.busy or not info.get("registrations")

    # -- tunnel --------------------------------------------------------------------

    @on(Switch.Changed, "#tunnel-switch")
    def _tunnel_switched(self, event: Switch.Changed) -> None:
        if self.quiet or self.busy or event.value == self.tunnel_open:
            return
        self.set_tunnel(event.value)

    def press_t(self) -> None:
        if not self.busy:
            self.set_tunnel(not self.tunnel_open)

    def set_tunnel(self, on: bool) -> None:
        """Opening asks first (it exposes this machine); closing acts at once."""
        if not on:
            self._tunnel_start(False)
            return
        if not self.has_password:
            self.app.notify("Remote access needs a password first: create it, then open the tunnel.",
                            severity="warning")
            self._render_tunnel(self.tunnel_open)
            self.replace_password()
            return

        def answer(yes: bool | None) -> None:
            if yes:
                self._tunnel_start(True)
            else:
                self._render_tunnel(self.tunnel_open)

        url = self.info.get("url") or "the public address"
        self.app.push_screen(Confirm(
            "Open the tunnel?\n\nTailscale Funnel makes pcbridge reachable from the internet at "
            f"{url}. Remote apps still need the password; local clients are not affected.",
            yes="Open the tunnel", kind="unlocks"), answer)

    def _tunnel_start(self, on: bool) -> None:
        self.busy = True
        self.query_one("#tunnel-switch", Switch).disabled = True
        self.query_one("#tunnel-text", Static).update(
            Text("Opening the tunnel…" if on else "Closing the tunnel…", style=PALETTE["working"]))
        self._tunnel_worker(on)

    @work(thread=True, exclusive=True, group="remote-tunnel")
    def _tunnel_worker(self, on: bool) -> None:
        try:
            ok, msg = self.app.backend.set_tunnel(self.app.cfg, on)
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, str(exc)
        self.app.post(self._tunnel_done, ok, msg)

    @while_mounted
    def _tunnel_done(self, ok: bool, msg: str) -> None:
        self.busy = False
        self.app.notify(msg, severity="information" if ok else "error", timeout=10)
        self.reload()
        self.app.load_status()

    # -- address -------------------------------------------------------------------

    @on(Button.Pressed, "#address-copy")
    def press_y(self) -> None:
        url = self.info.get("url")
        if not url:
            return
        copied = self.query_one("#copied", Static)
        if self.app.backend.copy_text(url):
            copied.update(Text("✓ copied with wl-copy", style=PALETTE["ok"]))
        else:
            self.app.copy_to_clipboard(url)
            copied.update(Text("Asked the terminal to copy it (OSC 52); check that it arrived.",
                               style=PALETTE["muted"]))

    # -- access --------------------------------------------------------------------

    @on(Segmented.Changed, "#remote-profile")
    def _profile(self, event: Segmented.Changed) -> None:
        current = self.info.get("remote_profile_setting", "same")
        if self.quiet or not self.info or event.value == current:
            return
        if self.app.apply_settings({"tools.remote_profile": event.value}):
            self.info["remote_profile_setting"] = event.value
            self._render_profile_notes(event.value)

    @on(Button.Pressed, "#password-replace")
    def press_p(self) -> None:
        self.replace_password()

    def replace_password(self) -> None:
        """Replace (or create) the remote password: the login check, then the dialog."""
        if self.busy:
            return
        replacing = self.has_password
        live = sum(1 for _ in self.rows) if replacing else 0

        def choose(login: str | None) -> None:
            if login:
                self.app.push_screen(PasswordScreen(replacing, live), lambda picked: self._save_password(
                    picked, login, replacing) if picked else None)

        self.app.push_screen(LoginScreen(), choose)

    def _save_password(self, picked: tuple[str, bool, bool], login: str, replacing: bool) -> None:
        self.busy = True
        self._password_worker(picked[0], picked[1], picked[2], login, replacing)

    @work(thread=True, group="remote-password")
    def _password_worker(self, password: str, generated: bool, sign_out: bool, login: str,
                         replacing: bool) -> None:
        revoked = ""
        try:
            self.app.backend.set_remote_password(self.app.cfg, password, login)
            error = ""
        except Exception as exc:  # noqa: BLE001 - shown to the user
            error = str(getattr(exc, "message", exc))
        if not error and sign_out:
            try:
                revoked = self.app.backend.revoke_remote(self.app.cfg, None)
            except Exception as exc:  # noqa: BLE001
                revoked = f"The apps were not signed out: {exc}"
        self.app.post(self._password_saved, password, generated, replacing, error, revoked)

    @while_mounted
    def _password_saved(self, password: str, generated: bool, replacing: bool, error: str,
                        revoked: str) -> None:
        self.busy = False
        if error:
            self.app.notify(error, title="The password was not saved", severity="error", timeout=15)
            return
        url = getattr(self.app.cfg, "mcp_url", "")
        address = f"Remote address: {url}" if url else ""
        done = "replaced" if replacing else "created; remote access is on once the tunnel is open"
        self.app.notify(f"Remote password {done}. Only its hash is stored."
                        + (f"\n{revoked}" if revoked else ""), timeout=12)
        self.app.reload_config()  # reads this tab again too
        if generated:
            self.app.push_screen(ShowOnce("The new remote password", password,
                                          "Type it on the approval page when a remote app connects."))

    # -- revoke ------------------------------------------------------------------

    def selected_row(self) -> str | None:
        table = self.query_one("#apps-table", DataTable)
        if not table.row_count:
            return None
        return str(table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value)

    @on(DataTable.CellSelected, "#apps-table")
    def _cell(self, event: DataTable.CellSelected) -> None:
        if event.coordinate.column == 5:
            self.ask_revoke(str(event.cell_key.row_key.value))

    def press_x(self) -> None:
        rid = self.selected_row()
        if rid:
            self.ask_revoke(rid)

    def ask_revoke(self, rid: str) -> None:
        g = self.rows.get(rid)
        if g is None or self.busy:
            return
        self.revoking = rid
        self.query_one("#apps-confirm-text", Static).update(
            Text(f"{g['name']}: sign it out?", style=PALETTE["warning"]))
        self.query_one("#apps-confirm").add_class("shown")
        self.query_one("#apps-confirm-no", Button).focus()

    @on(Button.Pressed, "#apps-confirm-no")
    def _keep(self) -> None:
        self.revoking = None
        self.query_one("#apps-confirm").remove_class("shown")

    @on(Button.Pressed, "#apps-confirm-yes")
    def _revoke_confirmed(self) -> None:
        g = self.rows.get(self.revoking or "")
        self.query_one("#apps-confirm").remove_class("shown")
        self.revoking = None
        if g is not None:
            self._start(g["name"])

    @on(Button.Pressed, "#remote-revoke-all")
    def _revoke_all(self) -> None:
        if not self.busy:
            self.confirm_revoke_all("Revoke every remote app?\n\nEvery registration and every token "
                                    "is deleted; each app has to connect again with the password.")

    def confirm_revoke_all(self, question: str) -> None:
        self.app.push_screen(Confirm(question, yes="Revoke all", kind="destroys"),
                             lambda yes: self._start(None) if yes else None)

    @on(Button.Pressed, "#stale-cleanup")
    def _cleanup(self) -> None:
        if self.busy:
            return
        self.busy = True
        self._cleanup_worker()

    @work(thread=True, group="remote-cleanup")
    def _cleanup_worker(self) -> None:
        try:
            msg, ok = self.app.backend.cleanup_stale(self.app.cfg), True
        except Exception as exc:  # noqa: BLE001
            msg, ok = str(exc), False
        self.app.post(self._revoked, msg, ok)

    def _start(self, name: str | None) -> None:
        self.busy = True
        for button in self.query(Button):
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
        for button in self.query(Button):
            button.disabled = False
        if ok:
            self.app.notify(msg, timeout=10)
        else:
            self.app.notify(msg, title="Not revoked", severity="error", timeout=12)
        self.reload()
