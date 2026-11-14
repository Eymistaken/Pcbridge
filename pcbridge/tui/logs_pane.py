"""The Logs tab: the daemon's journal (`pcbridge logs -f`), live.

The last lines of this boot are read when the tab first opens, then
`journalctl -f` is followed in a worker thread for as long as the app runs.
Paused, new lines collect behind a button instead of moving the view.
The level is read from each message (`journal.py`); Find highlights.
"""

from __future__ import annotations

import time
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Static
from textual.worker import get_current_worker

from .pane import Pane, while_mounted
from .theme import PALETTE
from .widgets import Segmented, key_label

KEEP = 5000          # lines kept in memory
FIRST = 300          # lines read when the tab opens, and per Older

CSS = """
LogsPane #logs-top {
    height: 1;
    margin-bottom: 1;
}
LogsPane #logs-follow {
    margin-right: 2;
}
LogsPane #logs-follow.following {
    background: #1d3b2a;
    color: $pb-ok;
}
LogsPane #logs-follow.paused {
    background: $pb-banner;
    color: $pb-warning;
}
LogsPane .label {
    width: auto;
    color: $pb-muted;
    padding-right: 1;
}
LogsPane #logs-find {
    width: 1fr;
    max-width: 24;
    min-width: 8;
    margin-left: 2;
}
LogsPane #logs-count {
    width: 1fr;
    text-align: right;
    color: $pb-dim;
}
LogsPane #logs-box {
    height: 1fr;
}
LogsPane #logs-scroll {
    height: 1fr;
    background: $pb-box;
    padding: 0 1;
}
LogsPane #logs-new {
    dock: bottom;
    height: 1;
    align-horizontal: right;
    display: none;
}
LogsPane #logs-new.shown {
    display: block;
}
LogsPane #logs-new-button {
    width: 32;
}
LogsPane #logs-bottom {
    height: 1;
    margin-top: 1;
}
LogsPane #logs-source {
    width: 1fr;
    color: $pb-dim;
}
"""

LEVEL_STYLE = {"DEBUG": "dim", "INFO": "muted", "WARN": "warning", "ERROR": "error"}
LEVEL_FILTER = {"all": ("DEBUG", "INFO", "WARN", "ERROR"), "info": ("INFO", "WARN", "ERROR"),
                "warn": ("WARN", "ERROR"), "errors": ("ERROR",)}


class LogsPane(Pane):
    FOOTER = [("f", "Follow/Pause", "key:f"), ("v", "Level", "key:v"), ("/", "Find", "key:slash"),
              ("o", "Older", "key:o"), ("y", "Copy", "key:y")]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.lines: list[Any] = []
        self.pending: list[Any] = []      # new lines that came while paused
        self.following = True
        self.level = "all"
        self.started = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="logs-top"):
            yield Button("● Following f", id="logs-follow", classes="following", compact=True)
            yield Static("Level", classes="label")
            yield Segmented([("all", "all"), ("info", "info+"), ("warn", "warn+"), ("errors", "errors")],
                            id="logs-level")
            yield Input(placeholder="Find", id="logs-find")
            yield Static("", id="logs-count")
        with Vertical(id="logs-box"):
            with VerticalScroll(id="logs-scroll"):
                yield Static("Reading the journal…", id="logs-text")
            with Horizontal(id="logs-new"):
                yield Button("", id="logs-new-button", classes="primary", compact=True)
        with Horizontal(id="logs-bottom"):
            yield Static("journalctl --user -u pcbridge.service · this boot", id="logs-source")
            yield Button("Copy what is shown", id="logs-copy", compact=True)
            yield Button("Older ›", id="logs-older", compact=True)

    def shown(self) -> None:
        if not self.started:
            self.started = True
            self._read_worker("")

    # -- reading ---------------------------------------------------------------------

    @work(thread=True, group="logs-read")
    def _read_worker(self, before: str) -> None:
        try:
            lines = self.app.backend.journal(FIRST, before)
            error = ""
        except Exception as exc:  # noqa: BLE001 - shown in the tab
            lines, error = [], str(exc)
        self.app.post(self._read_done, lines, before, error)

    @while_mounted
    def _read_done(self, lines: list, before: str, error: str) -> None:
        if error:
            self.query_one("#logs-text", Static).update(
                Text(f"✕ The journal could not be read: {error}", style=PALETTE["error"]))
            return
        if before:
            self.lines = lines + self.lines
            self.draw_lines(keep_position=True)
            if not lines:
                self.app.notify("No older line in this boot.")
            return
        self.lines = lines
        self.draw_lines()
        after = lines[-1].cursor if lines else ""
        self._follow_worker(after)

    @work(thread=True, group="logs-follow")
    def _follow_worker(self, after: str) -> None:
        worker = get_current_worker()
        batch: list[Any] = []
        last = time.monotonic()
        try:
            stream = self.app.backend.journal_follow(after)
        except Exception as exc:  # noqa: BLE001
            self.app.post(self.app.notify, f"The journal cannot be followed: {exc}", severity="error")
            return
        try:
            for line in stream:
                if worker.is_cancelled:
                    break
                if line is not None:
                    batch.append(line)
                if batch and (line is None or time.monotonic() - last > 0.25):
                    self.app.post(self._arrived, batch)
                    batch, last = [], time.monotonic()
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                close()

    @while_mounted
    def _arrived(self, batch: list) -> None:
        if self.following:
            self.lines.extend(batch)
            self.lines = self.lines[-KEEP:]
            self.draw_lines()
        else:
            self.pending.extend(batch)
            self._render_new()

    # -- drawing -----------------------------------------------------------------------

    def shown_lines(self) -> list[Any]:
        allowed = LEVEL_FILTER[self.level]
        return [line for line in self.lines if line.level in allowed]

    def draw_lines(self, keep_position: bool = False) -> None:
        find = self.query_one("#logs-find", Input).value.strip()
        lines = self.shown_lines()
        out = Text()
        for i, line in enumerate(lines):
            if i:
                out.append("\n")
            out.append(time.strftime("%H:%M:%S", time.localtime(line.ts)), style=PALETTE["dim"])
            out.append(f" {line.level:<5} ", style=PALETTE[LEVEL_STYLE.get(line.level, "muted")])
            start = out.cell_len
            out.append(line.message, style=PALETTE["error" if line.level == "ERROR" else "text"])
            if find:
                lower, needle = line.message.lower(), find.lower()
                at = lower.find(needle)
                while at >= 0:
                    out.stylize(f"bold #0e1216 on {PALETTE['accent']}", start + at, start + at + len(needle))
                    at = lower.find(needle, at + len(needle))
        if not lines:
            out = Text("No line at this level in this boot." if self.lines else "The journal is empty.",
                       style=PALETTE["dim"])
        self.query_one("#logs-text", Static).update(out)
        self.query_one("#logs-count", Static).update(f"{len(lines)} lines shown")
        scroll = self.query_one("#logs-scroll", VerticalScroll)
        if self.following and not keep_position:
            scroll.call_after_refresh(scroll.scroll_end, animate=False)

    def _render_new(self) -> None:
        box = self.query_one("#logs-new")
        n = len(self.pending)
        box.set_class(n > 0 and not self.following, "shown")
        self.query_one("#logs-new-button", Button).label = f"↓ {n} new line{'s' if n != 1 else ''} · follow again"

    def _render_follow(self) -> None:
        button = self.query_one("#logs-follow", Button)
        button.label = key_label("● Following", "f") if self.following else key_label("❚❚ Paused", "f")
        button.set_class(self.following, "following")
        button.set_class(not self.following, "paused")

    # -- controls ----------------------------------------------------------------------

    @on(Button.Pressed, "#logs-follow")
    def press_f(self) -> None:
        self.following = not self.following
        if self.following:
            self.lines.extend(self.pending)
            self.lines = self.lines[-KEEP:]
            self.pending = []
            self.draw_lines()
        self._render_follow()
        self._render_new()

    @on(Button.Pressed, "#logs-new-button")
    def _follow_again(self) -> None:
        if not self.following:
            self.press_f()

    @on(Segmented.Changed, "#logs-level")
    def _level(self, event: Segmented.Changed) -> None:
        if event.value and event.value != self.level:
            self.level = event.value
            self.draw_lines()

    def press_v(self) -> None:
        self.query_one("#logs-level", Segmented).action_move(1)

    @on(Input.Changed, "#logs-find")
    def _find(self) -> None:
        self.draw_lines(keep_position=True)

    def press_slash(self) -> None:
        self.query_one("#logs-find", Input).focus()

    @on(Button.Pressed, "#logs-older")
    def press_o(self) -> None:
        first = self.lines[0].cursor if self.lines else ""
        if first:
            self._read_worker(first)

    @on(Button.Pressed, "#logs-copy")
    def press_y(self) -> None:
        text = "\n".join(f"{time.strftime('%H:%M:%S', time.localtime(x.ts))} {x.level} {x.message}"
                         for x in self.shown_lines())
        if not self.app.backend.copy_text(text):
            self.app.copy_to_clipboard(text)
        self.app.notify(f"Copied {len(self.shown_lines())} lines.")
