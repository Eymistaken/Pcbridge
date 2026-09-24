"""The terminal UI's frame: the grant bar, the tabs and the footer.

The bar at the top is on every tab: the daemon, the desktop grant with its
countdown, and one button that locks or unlocks it (key `l`). Slow work
(lock, unlock, restart, status) runs in worker threads so the screen never
freezes. Colors are the terminal's own (`ansi_color=True`) plus the theme's
accent; nothing else is colored.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Static, TabbedContent, TabPane

from .. import __version__
from .backend import Backend


def while_mounted(method):
    """Run a timer or worker callback only while its widgets exist.

    The 1 s timer and the workers' callbacks can land while the app is
    shutting down and its widgets are already gone (measured on a slow CI
    runner: NoMatches from `refresh_grant`); then there is nothing to update.
    """
    import functools

    from textual.css.query import NoMatches

    @functools.wraps(method)
    def run(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except NoMatches:
            return None

    return run

CSS = """
Screen {
    background: ansi_default;
}
#bar {
    dock: top;
    height: 1;
    padding: 0 1;
}
#bar-title {
    width: auto;
    text-style: bold;
    padding-right: 2;
}
#bar-daemon {
    width: auto;
    padding-right: 2;
}
#bar-grant {
    width: 1fr;
}
#bar-grant.open {
    color: $warning;
    text-style: bold;
}
#grant-button {
    min-width: 18;
}
/* The ANSI theme gives a disabled button a border (!important), which
   breaks a one-line compact button; keep compact buttons one line. */
Button.-textual-compact:disabled {
    border: none !important;
    height: 1;
}
TabbedContent {
    height: 1fr;
}
.pane {
    padding: 1 1 0 1;
}
.hint {
    color: $text-muted;
}
Confirm {
    align: center middle;
}
#confirm-box {
    width: 64;
    height: auto;
    padding: 1 2;
    border: round $primary;
    background: ansi_default;
}
#confirm-buttons {
    height: auto;
    margin-top: 1;
    align-horizontal: right;
}
#confirm-buttons Button {
    margin-left: 2;
}
"""


class Confirm(ModalScreen[bool]):
    """A yes/no question. Escape and the second button say no."""

    BINDINGS = [Binding("escape", "no", "Cancel")]

    def __init__(self, question: str, yes: str = "Yes", no: str = "Cancel") -> None:
        super().__init__()
        self.question = question
        self.yes = yes
        self.no = no

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(self.question, id="confirm-question")
            with Horizontal(id="confirm-buttons"):
                yield Button(self.no, id="confirm-no", compact=True)
                yield Button(self.yes, id="confirm-yes", variant="primary", compact=True)

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")

    def action_no(self) -> None:
        self.dismiss(False)


class GrantBar(Horizontal):
    def compose(self) -> ComposeResult:
        yield Static(f"pcbridge {__version__}", id="bar-title")
        yield Static("daemon: ...", id="bar-daemon")
        yield Static("Desktop: ...", id="bar-grant")
        yield Button("...", id="grant-button", compact=True, disabled=True)


class OverviewPane(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Static("Reading the status...", id="overview-body")
        yield Static(
            "\nPress l or click the button at the top right to lock or unlock desktop control. "
            "r restarts the daemon, q quits. Every tab works with the mouse.",
            classes="hint",
        )


class CommandsPane(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("The same work without this screen: run `pcbridge COMMAND` in a shell. "
                     "`pcbridge list` prints this table.", classes="hint")
        yield DataTable(id="commands-table", cursor_type="row", zebra_stripes=False)

    def on_mount(self) -> None:
        from ..cli.main import COMMANDS

        table = self.query_one(DataTable)
        table.add_columns("Group", "Command", "What it does")
        for group, _name, usage, text in COMMANDS:
            table.add_row(group, f"pcbridge {usage}", text)


def _row(label: str, value: str) -> Text:
    out = Text()
    out.append(f"{label:<18}", style="bold")
    out.append(value)
    return out


class PcbridgeApp(App):
    TITLE = "pcbridge"
    ENABLE_COMMAND_PALETTE = False
    CSS = CSS
    BINDINGS = [
        Binding("l", "toggle_grant", "Lock/Unlock"),
        Binding("r", "restart", "Restart daemon"),
        Binding("1", "show_tab('overview')", "Overview", show=False),
        Binding("2", "show_tab('settings')", "Settings", show=False),
        Binding("3", "show_tab('tools')", "Tools", show=False),
        Binding("4", "show_tab('connections')", "Connections", show=False),
        Binding("5", "show_tab('commands')", "Commands", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, backend: Backend | None = None) -> None:
        super().__init__(ansi_color=True)
        self.backend = backend or Backend()
        self.cfg: Any = None
        self.cfg_error = ""
        self.grant: Any = None
        self.status_info: dict = {}
        self.grant_busy = False

    # -- layout -----------------------------------------------------------------

    def compose(self) -> ComposeResult:
        from .connections_pane import ConnectionsPane
        from .settings_pane import SettingsPane
        from .tools_pane import ToolsPane

        yield GrantBar(id="bar")
        with TabbedContent(initial="overview"):
            with TabPane("Overview", id="overview"):
                yield OverviewPane(classes="pane")
            with TabPane("Settings", id="settings"):
                yield SettingsPane(classes="pane")
            with TabPane("Tools", id="tools"):
                yield ToolsPane(classes="pane")
            with TabPane("Connections", id="connections"):
                yield ConnectionsPane(classes="pane")
            with TabPane("Commands", id="commands"):
                yield CommandsPane(classes="pane")
        yield Footer()

    def on_mount(self) -> None:
        self.reload_config()
        self.refresh_grant()
        self.set_interval(1.0, self.refresh_grant)
        self.load_status()
        self.set_interval(10.0, self.load_status)

    def action_show_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab

    async def action_quit(self) -> None:
        from .settings_pane import SettingsPane

        pending = self.query_one(SettingsPane).pending()
        if not pending:
            self.exit()
            return

        def answer(yes: bool | None) -> None:
            if yes:
                self.exit()

        self.push_screen(Confirm(f"Quit and discard {pending} unsaved setting change(s)?",
                                 yes="Discard and quit"), answer)

    # -- config -----------------------------------------------------------------

    def reload_config(self) -> None:
        try:
            self.cfg = self.backend.load_config()
            self.cfg_error = ""
        except SystemExit as exc:  # ConfigError is a SystemExit with a message
            self.cfg = None
            self.cfg_error = str(getattr(exc, "message", exc))
            self.notify(self.cfg_error, title="The config does not load", severity="error", timeout=15)
        from .tools_pane import ToolsPane

        for pane in self.query(ToolsPane):
            pane.reload()

    # -- the grant bar --------------------------------------------------------------

    @while_mounted
    def refresh_grant(self) -> None:
        grant_text = self.query_one("#bar-grant", Static)
        button = self.query_one("#grant-button", Button)
        if self.cfg is None:
            self.grant = None
            grant_text.update("Desktop: config error")
            grant_text.remove_class("open")
            button.label, button.variant, button.disabled = "Config error", "default", True
            return
        self.grant = self.backend.grant_state(self.cfg)
        self._render_overview()
        if self.grant_busy:
            return
        g = self.grant
        if not g.enabled_in_config:
            grant_text.update("Desktop control: off in config")
            grant_text.remove_class("open")
            button.label, button.variant, button.disabled = "Unlock", "default", True
            button.tooltip = "Turn it on in Settings > Desktop control > desktop.enabled."
        elif g.open:
            from ..cli.grant import format_duration

            text = f"Desktop: OPEN, {format_duration(g.seconds_left)} left"
            if g.sliding:
                text += f" (at most {format_duration(g.hard_seconds_left)})"
            grant_text.update(text)
            grant_text.add_class("open")
            button.label, button.variant, button.disabled = "Lock now", "error", False
            button.tooltip = "Close desktop control and stop screen sharing (l)."
        else:
            minutes = self.cfg.desktop.unlock_default_minutes
            grant_text.update("Desktop: locked")
            grant_text.remove_class("open")
            button.label, button.variant, button.disabled = f"Unlock {minutes} min", "primary", False
            button.tooltip = "Let agents use the desktop for a while (l)."

    @on(Button.Pressed, "#grant-button")
    def _grant_button(self) -> None:
        # A click is deliberate: it locks or unlocks at once.
        self.toggle_grant(ask_to_unlock=False)

    def action_toggle_grant(self) -> None:
        # A key can be hit by accident (typing while the focus is elsewhere
        # did exactly that in the live check), so the key locks at once, the
        # safe direction, but asks before it unlocks.
        self.toggle_grant(ask_to_unlock=True)

    def toggle_grant(self, ask_to_unlock: bool) -> None:
        if self.grant_busy or self.cfg is None or self.grant is None:
            return
        if self.grant.open:
            self._set_busy("Locking...")
            self._grant_worker(lock=True)
        elif self.grant.enabled_in_config:
            if not ask_to_unlock:
                self._unlock_now()
                return
            minutes = self.cfg.desktop.unlock_default_minutes

            def answer(yes: bool | None) -> None:
                if yes:
                    self._unlock_now()

            self.push_screen(Confirm(
                f"Unlock desktop control for {minutes} minutes?\n\nAgents can then use the "
                "keyboard, the pointer and the screen. l or Lock now closes it again.",
                yes="Unlock"), answer)
        else:
            self.notify("Desktop control is off in the config. Turn on desktop.enabled in Settings first.",
                        severity="warning")

    def post(self, callback, *args, **kwargs) -> None:
        """call_from_thread that does nothing once the app has stopped."""
        try:
            self.call_from_thread(callback, *args, **kwargs)
        except RuntimeError:  # the app is no longer running
            pass

    def _unlock_now(self) -> None:
        if self.grant_busy or self.grant is None or self.grant.open:
            return
        self._set_busy("Unlocking...")
        self._grant_worker(lock=False)

    def _set_busy(self, label: str) -> None:
        self.grant_busy = True
        button = self.query_one("#grant-button", Button)
        button.label, button.disabled = label, True

    @work(thread=True, exclusive=True, group="grant")
    def _grant_worker(self, lock: bool) -> None:
        try:
            msg = self.backend.lock(self.cfg) if lock else self.backend.unlock(self.cfg, None)
            self.post(self.notify, msg.splitlines()[0] if msg else "Done.")
        except Exception as exc:  # noqa: BLE001 - shown to the user, never raised in the UI
            self.post(self.notify, str(exc), severity="error", timeout=10)
        finally:
            self.post(self._grant_done)

    @while_mounted
    def _grant_done(self) -> None:
        self.grant_busy = False
        self.refresh_grant()
        self.load_status()

    # -- status -------------------------------------------------------------------------

    @work(thread=True, exclusive=True, group="status")
    def load_status(self) -> None:
        try:
            info = self.backend.status()
        except Exception as exc:  # noqa: BLE001
            info = {"error": str(exc)}
        cfg = self.cfg
        if cfg is not None:
            try:
                # Importing the tool table pulls in FastMCP (about 0.5 s), so
                # it happens here in the worker, never on the screen's thread.
                from ..tools import TOOL_HINTS, tools_in_profile

                info["tools_offered"] = (cfg.tools_profile, len(tools_in_profile(cfg.tools_profile)),
                                         len(TOOL_HINTS))
            except Exception:  # noqa: BLE001 - the overview must not fail on this
                pass
        self.post(self._show_status, info)

    @while_mounted
    def _show_status(self, info: dict) -> None:
        self.status_info = info
        self._render_overview()

    @while_mounted
    def _render_overview(self) -> None:
        info = self.status_info
        if not info:
            return
        daemon = info.get("daemon") or {}
        running = bool(daemon) and daemon.get("reachable") is not False
        self.query_one("#bar-daemon", Static).update("daemon: running" if running else "daemon: not running")
        lines = []
        if "error" in info:
            lines.append(_row("Status", f"could not be read: {info['error']}"))
        if running:
            lines.append(_row("Daemon", f"running, pcbridge {daemon.get('version')}, pid {daemon.get('pid')} "
                                        f"(service {info.get('service')}, socket {info.get('socket_unit')})"))
        else:
            lines.append(_row("Daemon", f"not running ({daemon.get('why', 'no answer')})"))
        if self.cfg is not None:
            g = self.grant or self.backend.grant_state(self.cfg)
            lines.append(_row("Desktop control", "enabled in config" if g.enabled_in_config
                              else "disabled in config (desktop.enabled = false)"))
            lines.append(_row("Desktop grant", g.describe()))
            jobs = info.get("jobs_running") or []
            lines.append(_row("Jobs running", str(len(jobs))))
            for j in jobs[:10]:
                lines.append(_row("", f"{j['id']}  {j['kind']}  {j['label']}"))
            lines.append(_row("Remote tunnel", str(info.get("remote_tunnel", "unknown"))))
            if "tools_offered" in info:
                profile, offered, total = info["tools_offered"]
                lines.append(_row("Tools profile", f"{profile}: {offered} of {total} tools offered"))
            lines.append(_row("Config file", str(self.cfg.source_path)))
            for w in getattr(self.cfg, "warnings", []) or []:
                lines.append(_row("Warning", w))
        else:
            lines.append(_row("Config", self.cfg_error or "not loaded"))
        lines.append(_row("Install", str(info.get("install", "unknown"))))
        self.query_one("#overview-body", Static).update(Text("\n").join(lines))

    # -- restart --------------------------------------------------------------------------

    def action_restart(self) -> None:
        if self.cfg is None:
            self.notify("The config does not load, so the daemon would not start. Fix it first.",
                        severity="error")
            return

        def answer(yes: bool | None) -> None:
            if yes:
                self.notify("Restarting the daemon...")
                self._restart_worker()

        self.push_screen(Confirm(
            "Restart the pcbridge daemon so it reads the config again?\n\n"
            "It restarts only when no job is running and the desktop grant is closed; "
            "otherwise nothing happens and you are told why.",
            yes="Restart",
        ), answer)

    @work(thread=True, exclusive=True, group="restart")
    def _restart_worker(self) -> None:
        try:
            msg = self.backend.restart(self.cfg)
            ok = msg.startswith("daemon")
            self.post(self.notify, msg, severity="information" if ok else "warning", timeout=10)
        except Exception as exc:  # noqa: BLE001
            self.post(self.notify, str(exc), severity="error", timeout=10)
        self.post(self.load_status)
