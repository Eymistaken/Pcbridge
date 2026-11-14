"""The Desktop tab: the grant, desktop control, the panel icon, power and the
native helper with the operating system's permissions.

Two columns. Every switch and number here saves at once (the same checked,
backed-up write as Advanced); a change that needs a restart raises the
restart notice. Turning desktop control on asks first.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static, Switch

from .. import settings as S
from .dialogs import Confirm, minutes_label
from .pane import ScrollPane, while_mounted
from .theme import PALETTE
from .widgets import NumberInput, RestartBanner, SectionTitle, Segmented, key_label, set_kind

CSS = """
DesktopPane .two {
    height: auto;
}
DesktopPane #desk-left {
    width: 1fr;
    height: auto;
}
DesktopPane #desk-right {
    width: 1fr;
    height: auto;
    margin-left: 4;
}
DesktopPane.narrow .two {
    layout: vertical;
}
DesktopPane.narrow #desk-right {
    margin-left: 0;
}
DesktopPane .line {
    height: auto;
}
DesktopPane .switch-row {
    height: auto;
    min-height: 3;
}
DesktopPane .switch-row Switch {
    margin-right: 2;
}
DesktopPane .switch-text {
    width: 1fr;
    height: auto;
}
DesktopPane .num-row {
    height: auto;
    margin: 1 0 0 0;
}
DesktopPane .num-text {
    width: 1fr;
}
DesktopPane #grant-meter {
    height: 1;
}
DesktopPane #grant-for-row {
    height: 1;
    margin-top: 1;
}
DesktopPane #grant-for-row .label {
    width: 5;
}
DesktopPane #grant-actions {
    height: 1;
    margin-top: 1;
}
DesktopPane #perm-grid {
    height: auto;
}
DesktopPane #perm-right {
    margin-left: 2;
}
DesktopPane .perm-col {
    width: 1fr;
    height: auto;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}
DesktopPane .kv {
    height: 1;
}
DesktopPane .kv .label {
    width: 10;
}
DesktopPane .kv .value {
    width: 1fr;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}
"""

# The four colors of the GNOME panel icon (the extension's own states).
ICON_LEGEND = [("locked", "#8d99a6"), ("open", "#e8bb4f"), ("working", "#6aa5ff"), ("error", "#ff7a7a")]

# OS permission scopes and the capability that shows each.
PERMISSIONS = [("capture", "capture.monitor"), ("pointer", "input.pointer"),
               ("keyboard", "input.keyboard"), ("accessibility", "accessibility.read"),
               ("window", "window.list"), ("session", "")]

SWITCHES = [
    ("desktop.enabled", "Desktop control",
     "Off by default. When off, no desktop tool works, grant or not."),
    ("desktop.unlock_notification", "Notify me when it unlocks",
     "A desktop notification each time an agent or you open the grant."),
    ("desktop.block_gui_launch_in_shell", "Block GUI launches from shell_run",
     "shell_run refuses to start graphical apps; agents use window_focus instead."),
]

NUMBERS = [
    ("desktop.unlock_idle_seconds", "Lock after {} s without agent input"),
    ("desktop.idle_guard_seconds", "Refuse input {} s after you touch keys or mouse"),
]


def switch_label(title: str, key: str, help_text: str) -> Text:
    out = Text()
    out.append(title, style="bold")
    if key:
        out.append(f"  {key}", style=PALETTE["dim"])
    if help_text:
        out.append(f"\n{help_text}", style=PALETTE["muted"])
    return out


class DesktopPane(ScrollPane):
    FOOTER = [("l", "Lock/Unlock", "app.toggle_grant"), ("space", "Toggle", "key:space"),
              ("+-", "Number", "key:plus")]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.quiet = False
        self.minutes: int | None = None
        self.native: dict | None = None
        self.panel_mode: str | None = None
        self._panel_request = 0

    def compose(self) -> ComposeResult:
        yield RestartBanner()
        with Horizontal(classes="two"):
            with Vertical(id="desk-left"):
                yield SectionTitle("Grant", classes="first")
                yield Static("", id="grant-state", classes="line")
                yield Static("", id="grant-meter")
                with Horizontal(id="grant-for-row"):
                    yield Static("For", classes="label muted")
                    yield Segmented([], id="grant-minutes")
                with Horizontal(id="grant-actions"):
                    yield Button("Unlock", id="desk-grant", classes="primary", compact=True)
                    yield Button("Restart the clock", id="desk-reclock", compact=True)
                yield Static("The l key asks before it unlocks; a click acts at once. "
                             "Locking never asks.", classes="hint")
                yield SectionTitle("Desktop control")
                for key, title, help_text in SWITCHES:
                    with Horizontal(classes="switch-row"):
                        yield Switch(id=f"sw-{key.replace('.', '-')}")
                        yield Static(switch_label(title, key, help_text), classes="switch-text")
                for key, _title in NUMBERS:
                    with Horizontal(classes="num-row"):
                        yield Static("", id=f"num-label-{key.split('.')[1]}", classes="num-text")
                        low, high, zero, unit = S.BOUNDS.get(key, (0, None, False, "s"))
                        yield NumberInput(0, unit or "s", low=0 if zero else low, high=high,
                                          id=f"num-{key.split('.')[1]}")
            with Vertical(id="desk-right"):
                yield SectionTitle("Panel icon", classes="first")
                with Horizontal(classes="switch-row"):
                    yield Switch(id="sw-panel-icon")
                    yield Static(switch_label("Show pcbridge in the top bar", "",
                                              "Off: the icon shows only while desktop control "
                                              "is granted."), classes="switch-text")
                yield Static("", id="panel-note", classes="line muted")
                legend = Text()
                for i, (name, color) in enumerate(ICON_LEGEND):
                    if i:
                        legend.append("   ")
                    legend.append("● ", style=color)
                    legend.append(name, style=PALETTE["muted"])
                yield Static(legend, id="panel-legend")
                yield SectionTitle("Power")
                with Horizontal(classes="switch-row"):
                    yield Switch(id="sw-power-keep_awake")
                    yield Static(switch_label("Keep awake while jobs run", "power.keep_awake", ""),
                                 classes="switch-text")
                yield Static("", id="power-now", classes="line")
                yield SectionTitle("Native helper", id="native-title")
                for key, label in (("binary", "Binary"), ("session", "Session"), ("monitors", "Monitors")):
                    with Horizontal(classes="kv"):
                        yield Static(label, classes="label muted")
                        yield Static("…", id=f"native-{key}", classes="value")
                yield SectionTitle("OS permissions")
                with Horizontal(id="perm-grid"):
                    yield Static("", id="perm-left", classes="perm-col")
                    yield Static("", id="perm-right", classes="perm-col")

    def on_mount(self) -> None:
        self.set_class(self.app.size.width < 100, "narrow")
        self.config_changed()

    def on_resize(self) -> None:
        self.set_class(self.app.size.width < 100, "narrow")

    def shown(self) -> None:
        if self.native is None:
            self._native_worker()
        self.reload_panel_icon()

    # -- config -----------------------------------------------------------------------

    def config_changed(self) -> None:
        cfg = getattr(self.app, "cfg", None)
        self.quiet = True
        try:
            if cfg is None:
                for sw in self.query(Switch):
                    if sw.id != "sw-panel-icon":
                        sw.disabled = True
                return
            d = cfg.desktop
            values = {"desktop.enabled": d.enabled,
                      "desktop.unlock_notification": getattr(d, "unlock_notification", True),
                      "desktop.block_gui_launch_in_shell": getattr(d, "block_gui_launch_in_shell", False)}
            for key, value in values.items():
                sw = self.query_one(f"#sw-{key.replace('.', '-')}", Switch)
                sw.disabled = False
                sw.value = bool(value)
            power = self.query_one("#sw-power-keep_awake", Switch)
            power.disabled = False
            power.value = bool(getattr(cfg, "keep_awake", False))
            idle = int(getattr(d, "unlock_idle_seconds", 90) or 0)
            guard = int(getattr(d, "idle_guard_seconds", 60) or 0)
            self.query_one("#num-unlock_idle_seconds", NumberInput).set_value(idle, notify=False)
            self.query_one("#num-idle_guard_seconds", NumberInput).set_value(guard, notify=False)
            self.query_one("#num-label-unlock_idle_seconds", Static).update(
                self._num_label("desktop.unlock_idle_seconds", f"Lock after {idle} s without agent input"))
            self.query_one("#num-label-idle_guard_seconds", Static).update(
                self._num_label("desktop.idle_guard_seconds",
                                f"Refuse input {guard} s after you touch keys or mouse"))
            choices, current, _max = self.app.grant_choices()
            self.query_one("#grant-minutes", Segmented).set_options(
                [(str(m), minutes_label(m)) for m in choices], str(self.minutes or current))
        finally:
            self.call_after_refresh(self._unquiet)
        self.grant_changed(getattr(self.app, "grant", None))

    def current(self, key: str) -> Any:
        """The value the loaded config has for one of this tab's keys."""
        cfg = getattr(self.app, "cfg", None)
        if cfg is None:
            return None
        section, _, name = key.partition(".")
        if section == "desktop":
            return getattr(cfg.desktop, name, None)
        if key == "power.keep_awake":
            return getattr(cfg, "keep_awake", None)
        return None

    def _num_label(self, key: str, title: str) -> Text:
        out = Text()
        out.append(title, style="bold")
        out.append(f"  {key}", style=PALETTE["dim"])
        span = S.describe_bounds(key)
        if span:
            out.append(f"\n{span}", style=PALETTE["dim"])
        return out

    def _unquiet(self) -> None:
        self.quiet = False

    @on(Switch.Changed)
    def _switched(self, event: Switch.Changed) -> None:
        sid = event.switch.id or ""
        if self.quiet or not sid.startswith("sw-"):
            return
        if sid == "sw-panel-icon":
            self._change_panel_icon("always" if event.value else "when-granted")
            return
        key = sid.removeprefix("sw-").replace("-", ".", 1)
        if bool(event.value) == bool(self.current(key)):
            return  # moved by code to the value already saved
        if key == "desktop.enabled" and event.value:
            def answer(yes: bool | None) -> None:
                if yes:
                    self.app.apply_settings({key: True})
                self.config_changed()

            self.app.push_screen(Confirm(
                "Turn desktop control on?\n\nAgents can then drive this desktop whenever the grant "
                "is open. It stays locked until you or an agent unlocks it.", yes="Turn on",
                kind="unlocks"), answer)
            return
        if not self.app.apply_settings({key: bool(event.value)}):
            self.config_changed()

    @on(NumberInput.Changed)
    def _number(self, event: NumberInput.Changed) -> None:
        if self.quiet:
            return
        name = (event.number.id or "").removeprefix("num-")
        key = f"desktop.{name}"
        if event.value == self.current(key):
            return
        if not S.in_bounds(key, event.value):
            self.app.notify(f"{key} takes {S.describe_bounds(key)}.", severity="warning")
            self.config_changed()
            return
        if not self.app.apply_settings({key: event.value}):
            self.config_changed()

    def press_space(self) -> None:
        focused = self.app.focused
        if isinstance(focused, Switch):
            focused.toggle()

    def press_plus(self) -> None:
        focused = self.app.focused
        if focused is not None and isinstance(focused.parent, NumberInput):
            focused.parent.set_value(focused.parent.value + 1)

    # -- the grant ----------------------------------------------------------------------

    @while_mounted
    def grant_changed(self, grant: Any) -> None:
        state = self.query_one("#grant-state", Static)
        meter = self.query_one("#grant-meter", Static)
        button = self.query_one("#desk-grant", Button)
        reclock = self.query_one("#desk-reclock", Button)
        cfg = getattr(self.app, "cfg", None)
        if grant is None or cfg is None:
            state.update(Text("The config does not load; the grant cannot be read.", style=PALETTE["error"]))
            meter.update("")
            button.disabled, reclock.display = True, False
            return
        from ..cli.grant import format_duration

        minutes = int(self.query_one("#grant-minutes", Segmented).value or cfg.desktop.unlock_default_minutes)
        if grant.open:
            text = Text()
            text.append("◇ Open", style=f"bold {PALETTE['warning']}")
            text.append(f" · {format_duration(grant.seconds_left)} left", style=PALETTE["warning"])
            if grant.sliding:
                text.append(f" · at most {format_duration(grant.hard_seconds_left)} (sliding)",
                            style=PALETTE["muted"])
            state.update(text)
            total = max(grant.seconds_left, grant.hard_seconds_left, minutes * 60, 1)
            width = max(10, min(56, (self.query_one("#desk-left").size.width or 56)))
            filled = round(width * grant.seconds_left / total)
            meter.update(Text("━" * filled, style=PALETTE["warning"]) +
                         Text("╌" * (width - filled), style=PALETTE["rule"]))
            button.label, button.disabled = key_label("Lock now", "l"), False
            set_kind(button, "danger")
            reclock.display = True
            reclock.label = f"Restart the clock at {minutes_label(minutes)}"
        else:
            if not grant.enabled_in_config:
                state.update(Text("◆ Locked. Desktop control is off in the config: turn it on below "
                                  "first.", style=PALETTE["muted"]))
            elif grant.blocked_reason:
                state.update(Text(f"◆ Paused: {grant.blocked_reason}", style=PALETTE["muted"]))
            else:
                state.update(Text("◆ Locked. Agents cannot use the keyboard, the pointer or the screen.",
                                  style=PALETTE["muted"]))
            meter.update("")
            button.label = key_label(f"Unlock for {minutes_label(minutes)}", "l")
            button.disabled = not grant.enabled_in_config or self.app.grant_busy
            set_kind(button, "primary")
            reclock.display = False

    @on(Segmented.Changed, "#grant-minutes")
    def _minutes(self, event: Segmented.Changed) -> None:
        if event.value:
            self.minutes = int(event.value)
            self.grant_changed(getattr(self.app, "grant", None))

    @on(Button.Pressed, "#desk-grant")
    def _grant(self) -> None:
        grant = getattr(self.app, "grant", None)
        if grant is None:
            return
        if grant.open:
            self.app.lock_now()
        else:
            self.app.unlock_with(self.minutes or self.app.grant_choices()[1])

    @on(Button.Pressed, "#desk-reclock")
    def _reclock(self) -> None:
        """Open the grant again for the chosen time (the clock starts over)."""
        self.app.reclock(self.minutes or self.app.grant_choices()[1])

    # -- status (power) -----------------------------------------------------------------

    def status_changed(self, info: dict) -> None:
        line = self.query_one("#power-now", Static)
        ka = info.get("keep_awake")
        if not ka:
            line.update("")
            return
        if not ka.get("enabled"):
            line.update(Text("Idle suspend is not blocked: the switch is off.", style=PALETTE["dim"]))
        elif ka.get("held"):
            line.update(Text(f"✓ Blocking idle suspend now ({ka.get('reason') or 'a job runs'}).",
                             style=PALETTE["ok"]))
        elif ka.get("error"):
            line.update(Text(f"▲ {ka['error']}", style=PALETTE["warning"]))
        else:
            line.update(Text("Not blocking now: no job runs.", style=PALETTE["dim"]))

    # -- the panel icon ------------------------------------------------------------------

    def reload_panel_icon(self) -> None:
        self._panel_request += 1
        self.query_one("#sw-panel-icon", Switch).disabled = True
        self.query_one("#panel-note", Static).update("Reading the panel icon setting…")
        self._read_panel_icon(self._panel_request)

    @work(thread=True, exclusive=True, group="panel-icon")
    def _read_panel_icon(self, request: int) -> None:
        try:
            mode, note = self.app.backend.panel_icon_status()
        except Exception as exc:  # noqa: BLE001 - shown in the tab
            mode, note = None, str(exc)
        self.app.post(self._panel_loaded, request, mode, note)

    @while_mounted
    def _panel_loaded(self, request: int, mode: str | None, note: str) -> None:
        if request != self._panel_request:
            return
        self.panel_mode = mode
        switch = self.query_one("#sw-panel-icon", Switch)
        self.quiet = True
        switch.value = mode == "always"
        switch.disabled = mode is None
        self.call_after_refresh(self._unquiet)
        head = "GNOME extension pcbridge@eymistaken · " + ("enabled" if mode is not None else "unavailable")
        self.query_one("#panel-note", Static).update(Text(f"{head}\n{note}", style=PALETTE["muted"]))

    def _change_panel_icon(self, mode: str) -> None:
        if self.panel_mode is None or self.panel_mode == mode:
            return
        self._panel_request += 1
        self.query_one("#sw-panel-icon", Switch).disabled = True
        self._write_panel_icon(self._panel_request, mode, self.panel_mode)

    @work(thread=True, exclusive=True, group="panel-icon")
    def _write_panel_icon(self, request: int, mode: str, previous: str) -> None:
        try:
            actual, note = self.app.backend.set_panel_icon_mode(mode)
        except Exception as exc:  # noqa: BLE001 - shown in the tab
            actual, note = previous, f"The panel icon could not be changed: {exc}"
        self.app.post(self._panel_loaded, request, actual, note)

    # -- native helper ------------------------------------------------------------------

    @work(thread=True, exclusive=True, group="native")
    def _native_worker(self) -> None:
        cfg = getattr(self.app, "cfg", None)
        if cfg is None:
            return
        try:
            info = self.app.backend.native(cfg)
        except Exception as exc:  # noqa: BLE001 - shown in the tab
            info = {"findings": [("fail", f"diagnostics failed: {exc}")], "session": "unknown",
                    "session_ok": False, "monitors": "unknown"}
        self.app.post(self._native_loaded, info)

    @while_mounted
    def _native_loaded(self, info: dict) -> None:
        self.native = info
        findings = info.get("findings") or []
        bad = [t for level, t in findings if level == "fail"]
        title = self.query_one("#native-title", SectionTitle)
        title.set(extra="✕ not usable" if bad else "✓ healthy")
        binary = next((t for level, t in findings if t.startswith("found:")), "")
        if not binary and bad:
            binary = bad[0]
        version = next((t for level, t in findings if t.startswith("version")), "")
        self.query_one("#native-binary", Static).update(
            Text((binary.removeprefix("found: ").split(" (")[0] + (f" · {version.split(' · ')[0]}"
                                                                     if version else "")) or "not found",
                 style=PALETTE["dim"]))
        self.query_one("#native-session", Static).update(info.get("session", ""))
        self.query_one("#native-monitors", Static).update(info.get("monitors", ""))
        caps = {}
        for level, text in findings:
            name, _, rest = text.partition(":")
            if name.strip() in {c for _p, c in PERMISSIONS if c}:
                backend = rest.split("(", 1)[1].split(")", 1)[0] if "(" in rest else ""
                caps[name.strip()] = (level, rest.strip().split(" ")[0], backend)
        lines = []
        for label, cap in PERMISSIONS:
            line = Text()
            if cap:
                level, state, backend = caps.get(cap, ("warn", "unknown", ""))
            else:
                level = "pass" if info.get("session_ok") else "warn"
                state, backend = "", info.get("session", "").split(" · ")[0]
            good = level == "pass"
            line.append("✓ " if good else "▲ ", style=PALETTE["ok" if good else "warning"])
            line.append(f"{label:<14}")
            line.append((backend or state).removeprefix("linux."), style=PALETTE["dim"])
            if not good and state:
                line.append(f" · {state}", style=PALETTE["warning"])
            lines.append(line)
        half = (len(lines) + 1) // 2
        self.query_one("#perm-left", Static).update(Text("\n").join(lines[:half]))
        self.query_one("#perm-right", Static).update(Text("\n").join(lines[half:]))
