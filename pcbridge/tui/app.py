"""The terminal UI's frame: the bar, the nine tabs and the footer.

The bar at the top is on every tab: the daemon, the tunnel, the desktop
grant with its countdown, and the grant's button (key `l`) with the
duration menu. Slow work (lock, unlock, restart, status) runs in worker
threads so the screen never freezes. The palette is our own
(`theme.py`); the background stays the terminal's.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.markup import escape
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static, TabbedContent, TabPane, Tabs
from textual.widgets.option_list import Option

from .. import __version__
from .backend import Backend
from .dialogs import CSS as DIALOG_CSS
from .dialogs import Confirm, HelpScreen, ShowOnce, UnlockConfirm, copy_secret, durations, minutes_label
from .pane import while_mounted
from .theme import CSS as THEME_CSS
from .theme import PALETTE, THEME, css_variables
from .widgets import CSS as WIDGET_CSS
from .widgets import key_label, set_kind

__all__ = ["Confirm", "PcbridgeApp", "ShowOnce", "copy_secret", "while_mounted"]

# (id, full label, short label at 80 columns)
TABS = [
    ("overview", "Overview", "Overview"),
    ("desktop", "Desktop", "Desktop"),
    ("jobs", "Agents & Jobs", "Agents"),
    ("connections", "Connections", "Clients"),
    ("remote", "Remote", "Remote"),
    ("tools", "Tools", "Tools"),
    ("activity", "Activity", "Activity"),
    ("logs", "Logs", "Logs"),
    ("advanced", "Advanced", "Advanced"),
]

# Below this width the bar and the tabs shorten (the narrow layout).
NARROW = 100

# Keys a tab may claim; the app hands each to the active tab (`Pane.FOOTER`).
PANE_KEYS = ["u", "f", "c", "o", "d", "t", "y", "p", "x", "k", "g", "a", "v", "slash", "space",
             "ctrl+s", "ctrl+z", "pageup", "pagedown"]

CSS = THEME_CSS + WIDGET_CSS + DIALOG_CSS + """
#bar {
    dock: top;
    height: 1;
    padding: 0 1;
    background: $pb-panel;
}
#bar Static {
    width: auto;
    padding-right: 2;
}
#bar #bar-fill {
    width: 1fr;
    padding: 0;
}
#bar Button {
    width: auto;
    min-width: 0;
}
#bar-grant.open {
    color: $pb-warning;
    text-style: bold;
}
#bar Button.-textual-compact {
    margin-right: 0;
}
#grant-menu {
    padding: 0 1 0 0;
    margin-left: 0;
}
.narrow ContentTabs {
    margin-right: 6;
}
TabbedContent {
    height: 1fr;
}
TabbedContent > ContentTabs, Tabs {
    background: ansi_default;
}
Tab, Tab:ansi {
    color: $pb-muted;
    text-style: none;
}
Tab.-active, Tab:ansi.-active {
    color: $pb-text;
    text-style: bold;
}
Tab:hover {
    color: $pb-text;
}
Tabs:focus .-active, Tabs:ansi:focus .-active {
    background: $pb-selection;
    color: $pb-text;
    text-style: bold;
}
Underline > .underline--bar, Underline:ansi > .underline--bar {
    color: $pb-accent;
    background: $pb-rule;
}
#tabs-more {
    position: absolute;
    width: 6;
    height: 1;
    color: $pb-accent;
    background: $pb-panel;
    text-align: right;
    display: none;
}
#tabs-more.shown {
    display: block;
}
KeyFooter {
    dock: bottom;
    height: 1;
    background: $pb-panel;
    padding: 0 1;
}
DurationMenu {
    align: right top;
    background: transparent;
}
#duration-box {
    width: 30;
    height: auto;
    margin: 1 1 0 0;
    border: round $pb-rule;
    background: $pb-panel;
}
#duration-list {
    background: $pb-panel;
    border: none;
    height: auto;
}
"""


class GrantBar(Horizontal):
    def compose(self) -> ComposeResult:
        yield Static("", id="bar-title")
        yield Static("● daemon …", id="bar-daemon")
        yield Static("", id="bar-remote")
        yield Static("", id="bar-fill")
        yield Static("Desktop: …", id="bar-grant")
        yield Button("…", id="grant-button", compact=True, disabled=True)
        yield Button("▾", id="grant-menu", compact=True, classes="primary")


class KeyFooter(Static):
    """This tab's keys, the key in accent bold and the label muted, then
    right-aligned `? Help` and `q Quit`. Each key can be clicked."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self.entries: list[tuple[str, str, str]] = []
        self._spans: list[tuple[int, int, str]] = []

    def set_entries(self, entries: list[tuple[str, str, str]]) -> None:
        self.entries = entries
        self.refresh()

    def render(self) -> Text:
        right = [("?", "Help", "app.help"), ("q", "Quit", "app.quit")]
        width = self.size.width
        right_len = sum(len(k) + len(lbl) + 1 for k, lbl, _ in right) + 2 * (len(right) - 1)
        out = Text()
        self._spans = []

        def add(entries: list[tuple[str, str, str]]) -> None:
            for i, (key, label, action) in enumerate(entries):
                if i:
                    out.append("  ")
                start = out.cell_len
                out.append(key, style=f"bold {PALETTE['accent']}")
                out.append(f" {label}", style=PALETTE["muted"])
                self._spans.append((start, out.cell_len, action))

        add(self.entries)
        out.append(" " * max(2, width - out.cell_len - right_len))
        add(right)
        return out

    async def on_click(self, event: events.Click) -> None:
        for start, end, action in self._spans:
            if start <= event.x - self.styles.padding.left < end:
                if action.startswith("key:"):
                    self.app.action_pane_key(action[4:])
                else:
                    await self.run_action(action)
                return


class DurationMenu(ModalScreen["int | None"]):
    """`Unlock desktop control for` 5 min … 2 h (max); a pick unlocks at once."""

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, choices: list[int], current: int, maximum: int) -> None:
        super().__init__()
        self.choices = choices
        self.current = current
        self.maximum = maximum

    def compose(self) -> ComposeResult:
        options: list[Option] = [Option(Text("Unlock desktop control for", style=PALETTE["muted"]),
                                        disabled=True)]
        for m in self.choices:
            label = Text()
            label.append("● " if m == self.current else "  ", style=PALETTE["accent"])
            label.append(minutes_label(m))
            if m == self.maximum:
                label.append(" (max)", style=PALETTE["dim"])
            options.append(Option(label, id=str(m)))
        with Vertical(id="duration-box"):
            yield OptionList(*options, id="duration-list")

    def on_mount(self) -> None:
        menu = self.query_one(OptionList)
        menu.focus()
        menu.highlighted = 1 + self.choices.index(self.current) if self.current in self.choices else 1

    @on(OptionList.OptionSelected)
    def _picked(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(int(event.option.id or 0) or None)

    def on_click(self, event: events.Click) -> None:
        if self.get_widget_at(event.screen_x, event.screen_y)[0] is self:
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


def _stylesheet() -> str:
    """The frame's rules and every tab's: one stylesheet, ranked by specificity."""
    from . import (activity_pane, connections_pane, desktop_pane, jobs_pane, logs_pane, overview_pane,
                   palette, remote_pane, settings_pane, tools_pane)

    return CSS + "".join(m.CSS for m in (overview_pane, desktop_pane, jobs_pane, connections_pane,
                                          remote_pane, tools_pane, activity_pane, logs_pane,
                                          settings_pane, palette))


class PcbridgeApp(App):
    TITLE = "pcbridge"
    # Our own palette (palette.py): grouped, with questions and an output view.
    ENABLE_COMMAND_PALETTE = False
    CSS = _stylesheet()
    BINDINGS = [
        Binding("l", "toggle_grant", "Lock/Unlock", show=False),
        Binding("r", "restart", "Restart", show=False),
        *[Binding(str(i + 1), f"show_tab('{tab}')", label, show=False)
          for i, (tab, label, _short) in enumerate(TABS)],
        *[Binding(key, f"pane_key('{key}')", key, show=False) for key in PANE_KEYS],
        Binding("question_mark", "help", "Help", show=False),
        Binding("ctrl+p", "commands", "Commands", show=False),
        Binding("q", "quit", "Quit", show=False),
    ]

    def __init__(self, backend: Backend | None = None,
                 terminal_background: tuple[int, int, int] | None = None) -> None:
        super().__init__(ansi_color=True)
        # The terminal's background (OSC 11), when it answered; the logo's
        # glow blends over it, else over a fixed dark base.
        self.terminal_background = terminal_background
        self.register_theme(THEME)
        self.theme = "pcbridge"
        self.backend = backend or Backend()
        self.cfg: Any = None
        self.cfg_error = ""
        self.grant: Any = None
        self.status_info: dict = {}
        self.grant_busy = False
        self.unlock_minutes: int | None = None   # the duration picked this session
        self.restart_due = 0                     # saved changes waiting for a restart
        self.restart_level: str | None = None
        self.restart_pid: Any = None             # the daemon's pid when they were saved
        self.panes: dict[str, Any] = {}
        self.narrow = False

    def get_css_variables(self) -> dict[str, str]:
        return {**super().get_css_variables(), **css_variables()}

    # -- layout -----------------------------------------------------------------

    def compose(self) -> ComposeResult:
        from .activity_pane import ActivityPane
        from .connections_pane import ConnectionsPane
        from .desktop_pane import DesktopPane
        from .jobs_pane import JobsPane
        from .logs_pane import LogsPane
        from .overview_pane import OverviewPane
        from .remote_pane import RemotePane
        from .settings_pane import SettingsPane
        from .tools_pane import ToolsPane

        classes = {"overview": OverviewPane, "desktop": DesktopPane, "jobs": JobsPane,
                   "connections": ConnectionsPane, "remote": RemotePane, "tools": ToolsPane,
                   "activity": ActivityPane, "logs": LogsPane, "advanced": SettingsPane}
        yield GrantBar(id="bar")
        with TabbedContent(initial="overview"):
            for i, (tab, label, _short) in enumerate(TABS):
                with TabPane(self._tab_label(i, label), id=tab):
                    pane = classes[tab](id=f"pane-{tab}")
                    self.panes[tab] = pane
                    yield pane
        yield Static("", id="tabs-more")
        yield KeyFooter(id="footer")

    @staticmethod
    def _tab_label(i: int, label: str) -> str:
        return f"[{PALETTE['dim']}]{i + 1}[/] {escape(label)}"

    def on_mount(self) -> None:
        self.reload_config()
        self.refresh_grant()
        self.set_interval(1.0, self.refresh_grant)
        self.load_status()
        self.set_interval(10.0, self.load_status)
        self._update_footer()

    def on_resize(self, event: events.Resize) -> None:
        narrow = event.size.width < NARROW
        if narrow != self.narrow:
            self.narrow = narrow
            self.screen.set_class(narrow, "narrow")
            tabbed = self.query_one(TabbedContent)
            for i, (tab, label, short) in enumerate(TABS):
                tabbed.get_tab(tab).label = self._tab_label(i, short if narrow else label)
            self.refresh_grant()
            self._render_bar_daemon()
            self._update_footer()
        self.call_after_refresh(self._update_more)

    @while_mounted
    def _update_more(self) -> None:
        """`+4 ›` at the right edge for the tabs that do not fit."""
        marker = self.query_one("#tabs-more", Static)
        tabs = self.query_one(Tabs)
        scroll = tabs.query_one("#tabs-scroll")
        right = scroll.scroll_x + scroll.size.width
        hidden = sum(1 for t in tabs.query("Tab") if t.virtual_region.right - 1 > right)
        marker.set_class(hidden > 0, "shown")
        marker.update(f"+{hidden} ›" if hidden else "")
        marker.styles.offset = (max(0, self.size.width - 6), 1)

    @on(TabbedContent.TabActivated)
    def _tab_activated(self, event: TabbedContent.TabActivated) -> None:
        self._update_footer()
        pane = self.active_pane()
        if pane is not None:
            pane.shown()
        self.call_after_refresh(self._update_more)
        self.set_timer(0.5, self._update_more)  # after the tabs' scroll animation

    def active_pane(self) -> Any:
        try:
            return self.panes.get(self.query_one(TabbedContent).active)
        except Exception:  # noqa: BLE001 - during shutdown
            return None

    @while_mounted
    def _update_footer(self) -> None:
        pane = self.active_pane()
        entries = list(getattr(pane, "FOOTER", []))
        if not any(key == "^p" for key, _l, _a in entries):
            entries.append(("^p", "Commands", "app.commands"))
        if self.active_tab() == "overview" and not self.narrow:
            entries.append(("1-9", "Tabs", "app.help"))
        self.query_one(KeyFooter).set_entries(entries)

    def active_tab(self) -> str:
        try:
            return str(self.query_one(TabbedContent).active)
        except Exception:  # noqa: BLE001
            return ""

    def action_show_tab(self, tab: str) -> None:
        # Focus left inside the old tab pulls TabbedContent back to it.
        self.set_focus(None)
        self.query_one(TabbedContent).active = tab

    def action_pane_key(self, key: str) -> None:
        pane = self.active_pane()
        if pane is not None:
            pane.dispatch_key(key.replace("+", "_"))

    def action_commands(self) -> None:
        from .palette import open_palette

        open_palette(self)

    def action_help(self) -> None:
        pane = self.active_pane()
        title = dict((t, label) for t, label, _s in TABS).get(self.active_tab(), "pcbridge")
        keys = [(k, label) for k, label, _a in getattr(pane, "FOOTER", [])]
        keys += [("l", "Lock or unlock desktop control (unlocking asks)"), ("r", "Restart the daemon"),
                 ("1-9", "Switch tabs"), ("^p", "Commands and tabs"), ("q", "Quit")]
        self.push_screen(HelpScreen(f"Keys · {title}", keys))

    async def action_quit(self) -> None:
        pane = self.panes.get("advanced")
        pending = pane.pending() if pane is not None else 0
        if not pending:
            self.exit()
            return

        def answer(yes: bool | None) -> None:
            if yes:
                self.exit()

        self.push_screen(Confirm(f"Quit and discard {pending} unsaved setting change(s)?",
                                 yes="Discard and quit", kind="destroys"), answer)

    def post(self, callback, *args, **kwargs) -> None:
        """call_from_thread that does nothing once the app has stopped."""
        try:
            self.call_from_thread(callback, *args, **kwargs)
        except RuntimeError:  # the app is no longer running
            pass

    # -- config -----------------------------------------------------------------

    def reload_config(self) -> None:
        try:
            self.cfg = self.backend.load_config()
            self.cfg_error = ""
        except SystemExit as exc:  # ConfigError is a SystemExit with a message
            self.cfg = None
            self.cfg_error = str(getattr(exc, "message", exc))
            self.notify(self.cfg_error, title="The config does not load", severity="error", timeout=15)
        for pane in self.panes.values():
            pane.config_changed()

    # -- saving from a task tab ----------------------------------------------------------

    def apply_settings(self, changes: dict[str, Any]) -> bool:
        """Save these settings at once (the task tabs: Desktop, Agents, Tools,
        Remote): checked with the real loader and backed up, as Advanced saves.
        A change that needs a restart raises the restart notice."""
        from .. import settings as S

        try:
            ed = self.backend.editor()
            for key, value in changes.items():
                ed.set(key, value)
            result = ed.save()
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - shown; nothing was written
            self.notify(str(getattr(exc, "message", exc)), title="Nothing was saved",
                        severity="error", timeout=12)
            return False
        if result.restart in (S.RESTART_DAEMON, S.RESTART_CLIENTS):
            due = [k for k in result.changed if ed.setting(k).restart != S.RESTART_NONE]
            self.note_restart_due(len(due), result.restart)
        self.notify(f"Saved {', '.join(result.changed)}.", timeout=5)
        self.reload_config()
        self.refresh_grant()
        self.load_status()
        return True

    # -- the restart notice -------------------------------------------------------------

    def note_restart_due(self, count: int, level: str | None) -> None:
        """Saved changes that apply after a restart: every tab's banner shows them."""
        from .. import settings as S

        if not count or not level:
            return
        self.restart_due += count
        if level == S.RESTART_CLIENTS or self.restart_level != S.RESTART_CLIENTS:
            self.restart_level = level
        daemon = self.status_info.get("daemon") or {}
        self.restart_pid = daemon.get("pid")
        self.refresh_banners()

    def clear_restart_due(self) -> None:
        self.restart_due, self.restart_level, self.restart_pid = 0, None, None
        self.refresh_banners()

    @while_mounted
    def refresh_banners(self) -> None:
        from .widgets import RestartBanner

        for banner in self.query(RestartBanner):
            banner.show(self.restart_due, self.restart_level)

    # -- the grant bar --------------------------------------------------------------

    def grant_choices(self) -> tuple[list[int], int, int]:
        """(durations, the current one, the maximum) for the unlock pickers."""
        d = self.cfg.desktop
        maximum = int(getattr(d, "unlock_max_minutes", 120) or 120)
        default = int(d.unlock_default_minutes)
        choices = durations(default, maximum)
        current = self.unlock_minutes or default
        return choices, current if current in choices else default, maximum

    @while_mounted
    def refresh_grant(self) -> None:
        grant_text = self.query_one("#bar-grant", Static)
        button = self.query_one("#grant-button", Button)
        menu = self.query_one("#grant-menu", Button)
        if self.cfg is None:
            self.grant = None
            grant_text.update("Desktop: config error")
            grant_text.remove_class("open")
            button.label, button.disabled = "Config error", True
            set_kind(button, "")
            menu.display = False
            return
        self.grant = self.backend.grant_state(self.cfg)
        for pane in self.panes.values():
            grant_changed = getattr(pane, "grant_changed", None)
            if grant_changed is not None:
                grant_changed(self.grant)
        if self.grant_busy:
            return
        g = self.grant
        narrow = self.narrow
        if not g.enabled_in_config:
            grant_text.update(Text("◆ Desktop control off", style=PALETTE["muted"]))
            grant_text.remove_class("open")
            button.label, button.disabled = "Unlock", True
            set_kind(button, "primary")
            menu.display = False
            button.tooltip = "Turn it on in Desktop > Desktop control."
        elif g.open:
            from ..cli.grant import format_duration

            left = format_duration(g.seconds_left)
            grant_text.update(f"◇ OPEN {left}" if narrow else f"◇ Desktop OPEN · {left} left")
            grant_text.add_class("open")
            button.label = key_label("Lock" if narrow else "Lock now", "l")
            button.disabled = False
            set_kind(button, "danger")
            menu.display = False
            button.tooltip = "Close desktop control and stop screen sharing (l)."
        else:
            _choices, current, _max = self.grant_choices()
            grant_text.update(Text("◆ locked" if narrow else "◆ Desktop locked", style=PALETTE["muted"]))
            grant_text.remove_class("open")
            button.label = "Unlock" if narrow else f"Unlock {minutes_label(current)}"
            button.disabled = False
            set_kind(button, "primary")
            menu.display = True
            menu.disabled = False
            button.tooltip = "Let agents use the desktop for a while (l asks first)."

    @on(Button.Pressed, "#grant-button")
    def _grant_button(self) -> None:
        # A click is deliberate: it locks or unlocks at once.
        self.toggle_grant(ask_to_unlock=False)

    @on(Button.Pressed, "#grant-menu")
    def _grant_menu(self) -> None:
        if self.cfg is None or self.grant is None or self.grant.open or self.grant_busy:
            return
        choices, current, maximum = self.grant_choices()

        def picked(minutes: int | None) -> None:
            if minutes:
                self.unlock_with(minutes)

        self.push_screen(DurationMenu(choices, current, maximum), picked)

    def action_toggle_grant(self) -> None:
        # A key can be hit by accident (typing while the focus is elsewhere
        # did exactly that in the live check), so the key locks at once, the
        # safe direction, but asks before it unlocks.
        self.toggle_grant(ask_to_unlock=True)

    def toggle_grant(self, ask_to_unlock: bool) -> None:
        if self.grant_busy or self.cfg is None or self.grant is None:
            return
        if self.grant.open:
            self.lock_now()
        elif self.grant.enabled_in_config:
            if not ask_to_unlock:
                self._unlock_now()
                return
            choices, current, maximum = self.grant_choices()

            def answer(minutes: int | None) -> None:
                if minutes:
                    self.unlock_with(minutes)

            self.push_screen(UnlockConfirm(current, choices,
                                           int(getattr(self.cfg.desktop, "unlock_idle_seconds", 0) or 0),
                                           maximum), answer)
        else:
            self.notify("Desktop control is off in the config. Turn it on in the Desktop tab first.",
                        severity="warning")

    def lock_now(self) -> None:
        """Locking never asks."""
        if self.grant_busy or self.grant is None or not self.grant.open:
            return
        self._set_busy("Locking…")
        self._grant_worker(lock=True, minutes=None)

    def unlock_with(self, minutes: int) -> None:
        """Unlock for `minutes` at once (a click or a confirmed question)."""
        default = int(self.cfg.desktop.unlock_default_minutes) if self.cfg is not None else None
        self.unlock_minutes = None if minutes == default else minutes
        self._unlock_now()

    def reclock(self, minutes: int) -> None:
        """Open the grant again for `minutes`: the clock starts over (a click)."""
        if self.grant_busy or self.grant is None or not self.grant.enabled_in_config:
            return
        if not self.grant.open:
            self.unlock_with(minutes)
            return
        self._set_busy("Unlocking…")
        self._grant_worker(lock=False, minutes=minutes)

    def _unlock_now(self) -> None:
        if self.grant_busy or self.grant is None or self.grant.open or not self.grant.enabled_in_config:
            return
        self._set_busy("Unlocking…")
        self._grant_worker(lock=False, minutes=self.unlock_minutes)

    def _set_busy(self, label: str) -> None:
        self.grant_busy = True
        button = self.query_one("#grant-button", Button)
        button.label, button.disabled = label, True
        self.query_one("#grant-menu", Button).disabled = True

    @work(thread=True, exclusive=True, group="grant")
    def _grant_worker(self, lock: bool, minutes: int | None) -> None:
        try:
            msg = self.backend.lock(self.cfg) if lock else self.backend.unlock(self.cfg, minutes)
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
                # it happens here in the worker, never on the screen's thread,
                # and one worker at a time (imports.py says why).
                from .imports import ready

                ready()
                from ..tools import TOOL_HINTS, tools_in_profile

                remote = getattr(cfg, "remote_tools_profile", "same")
                remote = cfg.tools_profile if remote in ("same", "", None) else remote
                info["tools_offered"] = (cfg.tools_profile, len(tools_in_profile(cfg.tools_profile)),
                                         len(TOOL_HINTS), remote, len(tools_in_profile(remote)))
            except Exception:  # noqa: BLE001 - the overview must not fail on this
                pass
        self.post(self._show_status, info)

    @while_mounted
    def _show_status(self, info: dict) -> None:
        self.status_info = info
        daemon = info.get("daemon") or {}
        if self.restart_due and self.restart_pid and daemon.get("pid") not in (None, self.restart_pid):
            self.clear_restart_due()  # restarted from somewhere else
        self._render_bar_daemon()
        for pane in self.panes.values():
            pane.status_changed(info)

    def daemon_running(self) -> bool:
        daemon = self.status_info.get("daemon") or {}
        return bool(daemon) and daemon.get("reachable") is not False

    @while_mounted
    def _render_bar_daemon(self) -> None:
        title = Text("pcbridge", style="bold")
        if not self.narrow:
            title.append(f" {__version__}", style=PALETTE["dim"])
        self.query_one("#bar-title", Static).update(title)
        if not self.status_info:
            return
        running = self.daemon_running()
        word = "running" if running else "not running"
        text = Text("● ", style=PALETTE["ok" if running else "error"])
        text.append(word if self.narrow else f"daemon {word}")
        self.query_one("#bar-daemon", Static).update(text)
        tunnel = self.status_info.get("remote_tunnel") == "open"
        remote = self.query_one("#bar-remote", Static)
        remote.update(Text("◉ remote" if self.narrow else "◉ remote open", style=PALETTE["warning"])
                      if tunnel else "")
        remote.display = tunnel

    # -- restart and update -----------------------------------------------------------------

    def _jobs_note(self) -> str:
        jobs = self.status_info.get("jobs_running") or []
        if not jobs:
            return "No job is running."
        names = ", ".join(f"{j['id'][:4]} ({j.get('agent') or j['kind']})" for j in jobs[:4])
        return f"Jobs {names} are running, so it waits until they end."

    def action_restart(self) -> None:
        if self.cfg is None:
            self.notify("The config does not load, so the daemon would not start. Fix it first.",
                        severity="error")
            return

        def answer(yes: bool | None) -> None:
            if yes:
                self.notify("Restarting the daemon…")
                self._restart_worker(update=False)

        self.push_screen(Confirm(
            "Restart the pcbridge daemon?\n\nIt reads the config again. It restarts only when no "
            "job is running and the desktop grant is closed; otherwise nothing happens and you are "
            f"told why. {self._jobs_note()}",
            yes="Restart",
        ), answer)

    def action_update(self) -> None:
        if self.cfg is None:
            self.notify("The config does not load, so the daemon would not start. Fix it first.",
                        severity="error")
            return

        def answer(yes: bool | None) -> None:
            if yes:
                self.notify("Loading the installed version…")
                self._restart_worker(update=True)

        self.push_screen(Confirm(
            f"Load pcbridge {__version__} into the daemon?\n\nThe daemon restarts into the "
            "installed code when no job runs and the desktop grant is closed. "
            f"{self._jobs_note()}", yes="Update"), answer)

    @work(thread=True, exclusive=True, group="restart")
    def _restart_worker(self, update: bool) -> None:
        try:
            msg = self.backend.update(self.cfg) if update else self.backend.restart(self.cfg)
            ok = msg.startswith("daemon")
            self.post(self.notify, msg, severity="information" if ok else "warning", timeout=10)
            if ok:
                self.post(self.clear_restart_due)
        except Exception as exc:  # noqa: BLE001
            self.post(self.notify, str(exc), severity="error", timeout=10)
        self.post(self.load_status)
