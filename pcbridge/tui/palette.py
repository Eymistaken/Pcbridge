"""The command palette (`Ctrl+P`): every pcbridge command and the nine tabs.

Four groups. RUN NOW runs at once and shows the output (or opens the tab
that shows it); ASKS FIRST replaces the list with the question and its
consequence; OPENS A DIALOG opens one; GO TO switches tabs. `serve`,
`stdio`, `ui` and `list` are not offered. `lock` runs at once: locking
never asks, whatever the older Commands list said.

The commands run as `python -m pcbridge ARGS` in a worker, or through the
same backend calls the tabs use, so the palette and the shell do the same
thing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from .theme import PALETTE
from .widgets import key_label

CSS = """
CommandPalette, OutputView {
    align: center top;
    background: black 50%;
}
#palette-box {
    width: 84;
    max-width: 100%;
    height: auto;
    max-height: 90%;
    margin-top: 2;
    border: round $pb-rule;
    background: $pb-panel;
    padding: 0 1;
}
#palette-search-row {
    height: 1;
    margin-bottom: 1;
}
#palette-prompt {
    width: 2;
    color: $pb-accent;
}
#palette-search {
    width: 1fr;
    background: $pb-panel;
}
#palette-count {
    width: auto;
    color: $pb-dim;
    padding-left: 1;
}
#palette-list {
    height: auto;
    max-height: 26;
    border: none;
    background: $pb-panel;
    padding: 0;
}
#palette-list > .option-list--option-highlighted {
    background: $pb-selection;
}
#palette-question {
    height: auto;
    display: none;
    padding: 1 0;
}
#palette-question.shown {
    display: block;
}
#palette-question-buttons {
    height: auto;
    margin-top: 1;
    align-horizontal: right;
}
#output-box {
    width: 100;
    max-width: 100%;
    height: 90%;
    margin-top: 1;
    border: round $pb-rule;
    background: $pb-panel;
    padding: 0 1;
}
#output-head {
    height: 1;
}
#output-command {
    width: 1fr;
    text-style: bold;
}
#output-state {
    width: auto;
}
#output-scroll {
    height: 1fr;
    margin-top: 1;
    background: $pb-box;
    padding: 0 1;
}
#output-buttons {
    height: auto;
    margin-top: 1;
}
#output-fill {
    width: 1fr;
}
"""

GROUPS = [("run", "RUN NOW"), ("ask", "ASKS FIRST"), ("dialog", "OPENS A DIALOG"), ("goto", "GO TO")]


@dataclass
class Command:
    name: str
    group: str                  # run | ask | dialog | goto
    text: str
    key: str = ""
    argv: list[str] = field(default_factory=list)   # `pcbridge ARGV` for run/ask
    tab: str = ""                                   # a tab to open instead of output
    question: str = ""
    consequence: Callable[[Any], str] | None = None
    action: str = ""                                # the button's word on a question
    danger: bool = False
    call: Callable[[Any], Any] | None = None        # app -> result text, instead of argv


def _jobs_line(app: Any) -> str:
    jobs = app.status_info.get("jobs_running") or []
    if not jobs:
        return "No job is running."
    names = ", ".join(f"{j['id'].rsplit('-', 1)[-1][:4]} ({j.get('kind', '').removeprefix('agent:')})"
                      for j in jobs[:4])
    return f"Jobs {names} are running"


def commands() -> list[Command]:
    def restart(app):
        return app.backend.restart(app.cfg)

    def update(app):
        return app.backend.update(app.cfg)

    def tunnel(app):
        ok, msg = app.backend.set_tunnel(app.cfg, True)
        return msg

    def revoke_all(app):
        return app.backend.revoke_remote(app.cfg, None)

    return [
        Command("status", "run", "Daemon, desktop grant, running jobs, remote tunnel.", argv=["status"]),
        Command("doctor", "run", "Check everything and say what to fix.", argv=["doctor"]),
        Command("clients", "run", "Which local agents and apps use pcbridge.", argv=["clients"]),
        Command("keep-awake", "run", "Whether idle suspend is blocked while jobs run.",
                argv=["keep-awake"]),
        Command("lock", "run", "Close desktop control now. Locking never asks.", key="l", argv=["lock"]),
        Command("tools", "run", "The MCP tools and which ones the profile offers.", key="6", tab="tools"),
        Command("logs -f", "run", "The daemon's journal, live.", key="8", tab="logs"),
        Command("remote status", "run", "The tunnel and the remote apps.", key="5", tab="remote"),
        Command("restart", "ask", "Restart the daemon so it reads the config again.", key="r",
                question="Restart the daemon?",
                consequence=lambda app: f"{_jobs_line(app)}; it restarts only when no job runs and "
                "the grant is closed, otherwise it says why and does nothing.",
                action="Restart", call=restart),
        Command("update", "ask", "Load the installed version into the daemon when idle.", key="u",
                question="Load the installed version into the daemon?",
                consequence=lambda app: f"{_jobs_line(app)}. A git install pulls first; the daemon "
                "restarts once no job runs.", action="Update", call=update),
        Command("stop", "ask", "Stop the daemon and every running job.", question="Stop pcbridge?",
                consequence=lambda app: f"{_jobs_line(app)}; they are stopped too, their output so "
                "far is kept. The grant closes. The socket stays, so the next client starts it again.",
                action="Stop", danger=True, argv=["stop"]),
        Command("unlock", "ask", "Open desktop control for agents.", key="l",
                question="Unlock desktop control?",
                consequence=lambda app: "Agents can then use the keyboard, the pointer and the screen "
                "until it locks again.", action="Unlock"),
        Command("remote start", "ask", "Open the tunnel: pcbridge reachable from the internet.", key="t",
                question="Open the tunnel?",
                consequence=lambda app: "Tailscale Funnel exposes this machine at "
                f"{getattr(app.cfg, 'mcp_url', 'the public address')}. Remote apps still need the "
                "password.", action="Open the tunnel", call=tunnel),
        Command("remote revoke --all", "ask", "Sign out every remote app.",
                question="Revoke every remote app?",
                consequence=lambda app: "Every registration and token is deleted; each app has to "
                "connect again with the password.", action="Revoke all", danger=True, call=revoke_all),
        Command("connect", "ask", "Connect Claude Code, Codex and Claude Desktop (backed up).",
                question="Connect the three default clients?",
                consequence=lambda app: "Claude Code, Codex and Claude Desktop get a pcbridge entry; "
                "every file is backed up first. Running clients pick it up when they restart.",
                action="Connect", argv=["connect"]),
        Command("report", "ask", "Write a sanitized bug-report bundle.",
                question="Write a bug report?",
                consequence=lambda app: "A .tar.gz with the logs and the config, secrets removed, is "
                "written to the current directory.", action="Write it", argv=["report"]),
        Command("set auth.password", "dialog", "Replace the remote password (login check first).",
                key="p"),
        Command("setup", "dialog", "Install or update pcbridge for this user."),
        Command("uninstall", "dialog", "Remove pcbridge for this user."),
    ] + [Command(f"Go to {label}", "goto", "", key=str(i + 1), tab=tab)
         for i, (tab, label) in enumerate(_tabs())]


def _tabs() -> list[tuple[str, str]]:
    from .app import TABS

    return [(t, label) for t, label, _short in TABS]


GLYPHS = {"run": "▶", "ask": "?", "dialog": "…", "goto": "›"}


def matches(cmd: Command, query: str) -> bool:
    words = query.lower().split()
    hay = f"{cmd.name} {cmd.text} {cmd.group}".lower()
    return all(w in hay for w in words)


class CommandPalette(ModalScreen["Command | None"]):
    """`❯ Search commands and tabs`, the groups, and a question in place of
    the list for a command that changes state."""

    BINDINGS = [Binding("escape", "back", "Close"), Binding("enter", "choose", "Run", priority=True),
                Binding("down", "move(1)", show=False, priority=True),
                Binding("up", "move(-1)", show=False, priority=True)]

    def __init__(self) -> None:
        super().__init__()
        self.all = commands()
        self.asking: Command | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-box"):
            with Horizontal(id="palette-search-row"):
                yield Static("❯", id="palette-prompt")
                yield Input(placeholder="Search commands and tabs", id="palette-search")
                yield Static("", id="palette-count")
            yield OptionList(id="palette-list")
            with Vertical(id="palette-question"):
                yield Static("", id="palette-question-title", classes="dialog-title")
                yield Static("", id="palette-question-text", classes="dialog-text")
                with Horizontal(id="palette-question-buttons"):
                    yield Button(key_label("Cancel", "esc"), id="palette-no", compact=True)
                    yield Button("", id="palette-yes", classes="primary", compact=True)

    def on_mount(self) -> None:
        self.fill("")
        self.query_one("#palette-search", Input).focus()

    def fill(self, query: str) -> None:
        menu = self.query_one("#palette-list", OptionList)
        menu.clear_options()
        found = [c for c in self.all if matches(c, query)]
        q = query.strip().lower()

        def rank(c: Command) -> int:
            """Name matches first: the start of the name, then anywhere in it."""
            name = c.name.lower().removeprefix("go to ")
            return 0 if q and name.startswith(q) else 1 if q and q in name else 2

        best = min(found, key=rank).name if found else None
        for group, title in GROUPS:
            rows = sorted((c for c in found if c.group == group), key=rank)
            if not rows:
                continue
            menu.add_option(Option(Text(title, style=f"bold {PALETTE['muted']}"), disabled=True))
            for c in rows:
                line = Text()
                glyph = "›" if c.tab and group == "run" else GLYPHS[group]
                line.append(f" {glyph} ", style=PALETTE["accent"])
                line.append(c.name, style="bold")
                if c.text:
                    line.append(f"  {c.text}", style=PALETTE["muted"])
                width = 76
                if c.key:
                    pad = max(2, width - line.cell_len - len(c.key))
                    line.append(" " * pad)
                    line.append(c.key, style=f"bold {PALETTE['accent']}")
                line.truncate(width + 2)
                menu.add_option(Option(line, id=c.name))
        self.query_one("#palette-count", Static).update(f"{len(found)} of {len(self.all)}")
        ids = [menu.get_option_at_index(i).id for i in range(menu.option_count)]
        menu.highlighted = ids.index(best) if best in ids else None

    @on(Input.Changed, "#palette-search")
    def _search(self, event: Input.Changed) -> None:
        if self.asking is None:
            self.fill(event.value)

    def action_move(self, step: int) -> None:
        if self.asking is not None:
            return
        menu = self.query_one("#palette-list", OptionList)
        menu.action_cursor_down() if step > 0 else menu.action_cursor_up()

    def selected(self) -> Command | None:
        option = self.query_one("#palette-list", OptionList).highlighted_option
        if option is None or option.id is None:
            return None
        return next((c for c in self.all if c.name == option.id), None)

    @on(OptionList.OptionSelected, "#palette-list")
    def _picked(self) -> None:
        self.action_choose()

    def action_choose(self) -> None:
        if self.asking is not None:
            self.dismiss(self.asking)
            return
        cmd = self.selected()
        if cmd is None:
            return
        if cmd.group == "ask":
            self.ask(cmd)
        else:
            self.dismiss(cmd)

    def ask(self, cmd: Command) -> None:
        self.asking = cmd
        self.query_one("#palette-list").display = False
        self.query_one("#palette-search-row").display = False
        box = self.query_one("#palette-question")
        box.add_class("shown")
        self.query_one("#palette-question-title", Static).update(cmd.question)
        text = cmd.consequence(self.app) if cmd.consequence else ""
        self.query_one("#palette-question-text", Static).update(text)
        yes = self.query_one("#palette-yes", Button)
        yes.label = key_label(cmd.action or cmd.name, "enter")
        yes.set_class(cmd.danger, "danger")
        yes.set_class(not cmd.danger, "primary")
        self.query_one("#palette-box").set_class(cmd.danger, "destroys")

    @on(Button.Pressed, "#palette-yes")
    def _yes(self) -> None:
        self.action_choose()

    @on(Button.Pressed, "#palette-no")
    def action_back(self) -> None:
        if self.asking is None:
            self.dismiss(None)
            return
        self.asking = None
        self.query_one("#palette-question").remove_class("shown")
        self.query_one("#palette-list").display = True
        self.query_one("#palette-search-row").display = True
        self.query_one("#palette-box").remove_class("destroys")
        self.query_one("#palette-search", Input).focus()


def color_output(text: str) -> Text:
    """The command's own marks, in our colors: ok lines green, warnings amber,
    failures red, fix lines dim."""
    out = Text()
    for i, line in enumerate(text.splitlines()):
        if i:
            out.append("\n")
        stripped = line.strip()
        if stripped.startswith("ok"):
            style = PALETTE["ok"]
        elif stripped.startswith("warn"):
            style = PALETTE["warning"]
        elif stripped.startswith("fail"):
            style = PALETTE["error"]
        elif stripped.startswith("fix:"):
            style = PALETTE["dim"]
        else:
            style = PALETTE["text"]
        out.append(line, style=style)
    return out


class OutputView(ModalScreen["bool | None"]):
    """`$ pcbridge doctor`, `✓ exit 0 · 1.8 s`, the output; Escape goes back
    to the commands, Enter runs it again. The command runs in a worker."""

    BINDINGS = [Binding("escape", "back", "Commands"), Binding("enter", "again", "Run again", priority=True),
                Binding("y", "copy", "Copy")]

    def __init__(self, cmd: Command) -> None:
        super().__init__()
        self.cmd = cmd
        self.output = ""
        self.busy = False

    def compose(self) -> ComposeResult:
        with Vertical(id="output-box"):
            with Horizontal(id="output-head"):
                yield Static(f"$ pcbridge {self.cmd.name}" if self.cmd.argv else f"$ {self.cmd.name}",
                             id="output-command")
                yield Static("", id="output-state")
            with VerticalScroll(id="output-scroll"):
                yield Static("", id="output-text")
            with Horizontal(id="output-buttons"):
                yield Button(key_label("‹ Commands", "esc"), id="output-back", compact=True)
                yield Static("", id="output-fill")
                yield Button(key_label("Copy output", "y"), id="output-copy", compact=True)
                yield Button(key_label("Run again", "enter"), id="output-again", classes="primary",
                             compact=True)

    def on_mount(self) -> None:
        self.run()

    def run(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.query_one("#output-state", Static).update(Text("⠹ running…", style=PALETTE["working"]))
        self.query_one("#output-again", Button).disabled = True
        self._worker()

    @work(thread=True, exclusive=True, group="palette-output")
    def _worker(self) -> None:
        t0 = time.monotonic()
        try:
            if self.cmd.call is not None:
                code, text = 0, str(self.cmd.call(self.app) or "Done.")
            else:
                code, text = self.app.backend.run_cli(self.cmd.argv)
        except Exception as exc:  # noqa: BLE001 - shown in the view
            code, text = 1, str(exc)
        self.app.call_from_thread(self._done, code, text, time.monotonic() - t0)

    def _done(self, code: int, text: str, seconds: float) -> None:
        self.busy = False
        self.output = text
        good = code == 0
        state = Text(f"{'✓' if good else '✕'} exit {code} · {seconds:.1f} s",
                     style=PALETTE["ok" if good else "error"])
        self.query_one("#output-state", Static).update(state)
        self.query_one("#output-text", Static).update(color_output(text) if text else
                                                      Text("No output.", style=PALETTE["dim"]))
        self.query_one("#output-again", Button).disabled = False
        self.app.refresh_grant()
        self.app.load_status()

    @on(Button.Pressed, "#output-again")
    def action_again(self) -> None:
        self.run()

    @on(Button.Pressed, "#output-copy")
    def action_copy(self) -> None:
        if not self.app.backend.copy_text(self.output):
            self.app.copy_to_clipboard(self.output)
        self.app.notify("Copied the output.")

    @on(Button.Pressed, "#output-back")
    def action_back(self) -> None:
        self.dismiss(True)


class TerminalOnly(ModalScreen[None]):
    """setup and uninstall ask questions on a terminal: they stay out of
    this screen, which says how to run them."""

    BINDINGS = [Binding("escape,enter", "close", "Close"), Binding("y", "copy", "Copy")]

    TEXTS = {
        "setup": ("pcbridge setup runs in a terminal",
                  "It installs or updates pcbridge for this user and asks before it changes anything: "
                  "the config, the service, the GNOME extension, the clients and the aliases. Quit this "
                  "screen (q) and run it in a shell.", "pcbridge setup"),
        "uninstall": ("pcbridge uninstall runs in a terminal",
                      "It removes pcbridge for this user and asks first. --purge also moves the config "
                      "and the state to the trash. Quit this screen (q) and run it in a shell.",
                      "pcbridge uninstall"),
    }

    def __init__(self, name: str) -> None:
        super().__init__()
        self.title_text, self.text, self.command = self.TEXTS[name]

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box", classes="dialog"):
            yield Static(self.title_text, classes="dialog-title")
            yield Static(self.text, classes="dialog-text")
            yield Static(Text(f"$ {self.command}", style=f"bold {PALETTE['accent']}"), classes="dialog-row")
            with Horizontal(classes="dialog-buttons"):
                yield Button(key_label("Copy the command", "y"), id="term-copy", compact=True)
                yield Button(key_label("Close", "esc"), id="term-close", classes="primary", compact=True)

    @on(Button.Pressed, "#term-copy")
    def action_copy(self) -> None:
        if not self.app.backend.copy_text(self.command):
            self.app.copy_to_clipboard(self.command)
        self.app.notify("Copied the command.")

    @on(Button.Pressed, "#term-close")
    def action_close(self) -> None:
        self.dismiss(None)


def open_palette(app: Any) -> None:
    """Ctrl+P: the palette, then whatever was chosen."""

    def chosen(cmd: Command | None) -> None:
        if cmd is None:
            return
        if cmd.tab:
            app.action_show_tab(cmd.tab)
        elif cmd.group == "dialog":
            if cmd.name == "set auth.password":
                app.action_show_tab("remote")
                app.panes["remote"].replace_password()
            else:
                app.push_screen(TerminalOnly(cmd.name))
        elif cmd.name == "unlock":
            if app.grant is not None and not app.grant.open:
                app.unlock_with(app.grant_choices()[1])
        else:
            app.push_screen(OutputView(cmd), lambda back: open_palette(app) if back else None)

    app.push_screen(CommandPalette(), chosen)
