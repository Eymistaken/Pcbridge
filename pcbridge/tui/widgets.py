"""Small widgets every tab uses: section titles, the segmented picker, the
number input, restart tags and the restart notice.
"""

from __future__ import annotations

from typing import Any, Iterable

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.content import Content
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Button, Input, Static

from .. import settings as S
from .theme import PALETTE

# Part of the app's stylesheet (app.py), so it ranks with the theme's rules
# and a tab's own rules can override both by specificity.
CSS = """
CheckLine {
    width: auto;
    height: 1;
}
Segmented {
    width: auto;
    height: 1;
}
Segmented:focus {
    text-style: bold;
}
Segmented:disabled {
    opacity: 60%;
}

NumberInput {
    width: auto;
    height: 1;
}
NumberInput Input {
    width: 9;
    text-align: right;
    padding: 0 1;
}
NumberInput .num-step {
    margin: 0;
    min-width: 3;
    background: $pb-button;
}
NumberInput .num-unit {
    width: auto;
    color: $pb-muted;
    padding: 0 1;
}

RestartBanner {
    height: 1;
    background: $pb-banner;
    color: $pb-banner-text;
    display: none;
    margin-bottom: 1;
    padding: 0 1;
}
RestartBanner.shown {
    display: block;
}
RestartBanner .banner-text {
    width: 1fr;
    color: $pb-banner-text;
}
RestartBanner .banner-note {
    width: auto;
    color: $pb-dim;
    padding-left: 1;
}
"""


def key_label(label: str, key: str = "") -> Content:
    """A button label with its key after it, in a lighter accent: `Restart r`."""
    out = Content(label)
    if key:
        out = out + Content(" ") + Content.styled(key, PALETTE["accent-light"])
    return out


def set_kind(button: Button, kind: str = "") -> None:
    """Make a button primary, danger, link, quiet or default, keeping its other classes."""
    for k in ("primary", "danger", "link", "quiet"):
        button.set_class(k == kind, k)


def styled(text: str, token: str, bold: bool = False) -> Text:
    return Text(text, style=f"{'bold ' if bold else ''}{PALETTE[token]}")


class SectionTitle(Static):
    """A bold muted title followed by a rule to the right edge: `SERVICE ───`."""

    def __init__(self, title: str, extra: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.title_text = title
        self.extra = extra

    def set(self, title: str | None = None, extra: str | None = None) -> None:
        if title is not None:
            self.title_text = title
        if extra is not None:
            self.extra = extra
        self.refresh()

    def render(self) -> Text:
        out = Text()
        out.append(self.title_text.upper(), style=f"bold {PALETTE['muted']}")
        if self.extra:
            out.append(f"  {self.extra}", style=PALETTE["dim"])
        out.append(" ")
        rest = max(0, self.size.width - out.cell_len)
        out.append("─" * rest, style=PALETTE["rule"])
        return out


class Segmented(Widget, can_focus=True):
    """A segmented picker on one row: `[ claude | antigravity | codex ]`.

    The chosen segment is in accent with dark bold text. `←` `→` move, a
    click picks. Changing the value posts `Segmented.Changed`.
    """

    BINDINGS = [
        Binding("left", "move(-1)", "Previous", show=False),
        Binding("right", "move(1)", "Next", show=False),
    ]

    value: reactive[str] = reactive("", init=False)

    class Changed(Message):
        def __init__(self, segmented: "Segmented", value: str) -> None:
            super().__init__()
            self.segmented = segmented
            self.value = value

        @property
        def control(self) -> "Segmented":
            return self.segmented

    def __init__(self, options: Iterable[tuple[str, str]], value: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.options: list[tuple[str, str]] = list(options)
        values = [v for v, _ in self.options]
        self.set_reactive(Segmented.value, value if value in values else (values[0] if values else ""))
        self._spans: list[tuple[int, int, str]] = []

    def set_options(self, options: Iterable[tuple[str, str]], value: str | None = None) -> None:
        self.options = list(options)
        values = [v for v, _ in self.options]
        target = self.value if value is None else value
        self.set_reactive(Segmented.value, target if target in values else (values[0] if values else ""))
        self.refresh(layout=True)

    def get_content_width(self, container, viewport) -> int:
        return sum(len(label) + 2 for _, label in self.options) + max(0, len(self.options) - 1) + 2

    def render(self) -> Text:
        out = Text()
        self._spans = []
        rule = PALETTE["rule"]
        out.append("[", style=rule)
        for i, (value, label) in enumerate(self.options):
            if i:
                out.append("│", style=rule)
            start = out.cell_len
            if value == self.value:
                out.append(f" {label} ", style=f"bold #0e1216 on {PALETTE['accent']}")
            else:
                out.append(f" {label} ", style=PALETTE["text"] if self.has_focus else PALETTE["muted"])
            self._spans.append((start, out.cell_len, value))
        out.append("]", style=rule)
        return out

    def watch_value(self, value: str) -> None:
        self.refresh()
        self.post_message(self.Changed(self, value))

    def action_move(self, step: int) -> None:
        values = [v for v, _ in self.options]
        if not values:
            return
        i = values.index(self.value) if self.value in values else 0
        self.value = values[(i + step) % len(values)]

    def on_click(self, event: events.Click) -> None:
        for start, end, value in self._spans:
            if start <= event.x < end:
                self.value = value
                return


class CheckLine(Widget, can_focus=True):
    """`[x] label` / `[ ] label`: a checkbox whose state is in the glyph, not
    only in a color (Textual's Checkbox shows the same X either way in an
    ANSI theme). Space, Enter or a click toggles it."""

    BINDINGS = [Binding("space,enter", "toggle", "Toggle", show=False)]

    value: reactive[bool] = reactive(False, init=False)

    class Changed(Message):
        def __init__(self, check: "CheckLine", value: bool) -> None:
            super().__init__()
            self.check = check
            self.value = value

        @property
        def control(self) -> "CheckLine":
            return self.check

    def __init__(self, label: str, value: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.label = label
        self.set_reactive(CheckLine.value, value)

    def get_content_width(self, container, viewport) -> int:
        return len(self.label) + 4

    def get_content_height(self, container, viewport, width) -> int:
        return 1

    def render(self) -> Text:
        out = Text()
        box_style = f"bold {PALETTE['accent']}" if self.has_focus else PALETTE["muted"]
        out.append("[x]" if self.value else "[ ]", style=box_style)
        out.append(f" {self.label}", style=PALETTE["text"] if self.value else PALETTE["muted"])
        return out

    def watch_value(self, value: bool) -> None:
        self.refresh()
        self.post_message(self.Changed(self, value))

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()

    def action_toggle(self) -> None:
        self.value = not self.value

    def toggle(self) -> None:
        self.value = not self.value

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.value = not self.value


class NumberInput(Horizontal):
    """`− 90 s +`: the value right-aligned in an input cell, with its unit.

    `+` `−` step, digits type; the value stays inside [low, high] where the
    schema knows them. Posts `NumberInput.Changed` with the parsed number.
    """

    class Changed(Message):
        def __init__(self, number: "NumberInput", value: int) -> None:
            super().__init__()
            self.number = number
            self.value = value

        @property
        def control(self) -> "NumberInput":
            return self.number

    def __init__(self, value: int, unit: str = "", low: int | None = None, high: int | None = None,
                 step: int = 1, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.value = int(value)
        self.unit = unit
        self.low = low
        self.high = high
        self.step = step

    def compose(self) -> ComposeResult:
        yield Button("−", classes="num-step num-down", compact=True)
        yield Input(str(self.value), restrict=r"-?[0-9]*", classes="num-value")
        if self.unit:
            yield Static(self.unit, classes="num-unit")
        yield Button("+", classes="num-step num-up", compact=True)

    def clamp(self, value: int) -> int:
        if self.low is not None:
            value = max(self.low, value)
        if self.high is not None:
            value = min(self.high, value)
        return value

    def set_value(self, value: int, notify: bool = True) -> None:
        value = self.clamp(int(value))
        changed = value != self.value
        self.value = value
        field = self.query_one(Input)
        if field.value != str(value):
            field.value = str(value)
        if changed and notify:
            self.post_message(self.Changed(self, value))

    @on(Button.Pressed, ".num-down")
    def _down(self, event: Button.Pressed) -> None:
        event.stop()
        self.set_value(self.value - self.step)

    @on(Button.Pressed, ".num-up")
    def _up(self, event: Button.Pressed) -> None:
        event.stop()
        self.set_value(self.value + self.step)

    @on(Input.Submitted, ".num-value")
    def _typed(self, event: Input.Submitted) -> None:
        event.stop()
        self._commit()

    def on_descendant_blur(self) -> None:
        self._commit()

    def _commit(self) -> None:
        text = self.query_one(Input).value.strip()
        try:
            number = int(text)
        except ValueError:
            self.query_one(Input).value = str(self.value)
            return
        self.set_value(number)

    def on_key(self, event: events.Key) -> None:
        if event.character in ("+", "="):
            event.stop()
            event.prevent_default()
            self.set_value(self.value + self.step)
        elif event.character in ("-", "−") and self.query_one(Input).value.strip() not in ("", "-"):
            event.stop()
            event.prevent_default()
            self.set_value(self.value - self.step)


RESTART_TAGS = {
    S.RESTART_NONE: ("applies now", "ok"),
    S.RESTART_DAEMON: ("restart daemon", "muted"),
    S.RESTART_CLIENTS: ("restart daemon + clients", "working"),
}


def restart_tag(level: str | None) -> Text:
    """`applies now` (green tint), `restart daemon` (gray), `restart daemon + clients` (blue)."""
    text, token = RESTART_TAGS.get(level or S.RESTART_NONE, RESTART_TAGS[S.RESTART_DAEMON])
    color = PALETTE[token]
    return Text(f" {text} ", style=f"{color} on {_tint(color)}")


def _tint(color: str, amount: float = 0.18, base: str = "#0e1216") -> str:
    """`color` mixed `amount` over a dark base: a tag's background."""
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(base[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(y + (x - y) * amount):02x}" for x, y in zip(a, b))


def tag(text: str, token: str) -> Text:
    color = PALETTE[token]
    return Text(f" {text} ", style=f"{color} on {_tint(color)}")


class RestartBanner(Horizontal):
    """`▲ 2 saved changes apply after restarting the daemon`, `Restart now r`.

    There is no Later: it stays until the daemon restarted. The app keeps
    the count (`app.restart_due`); every banner shows the same.
    """

    def compose(self) -> ComposeResult:
        yield Static("", classes="banner-text")
        yield Button(key_label("Restart now", "r"), classes="banner-restart primary", compact=True)
        yield Static("stays until the restart", classes="banner-note")

    def show(self, count: int, level: str | None) -> None:
        self.set_class(count > 0, "shown")
        if count:
            who = "the daemon and the clients" if level == S.RESTART_CLIENTS else "the daemon"
            noun = "change applies" if count == 1 else "changes apply"
            self.query_one(".banner-text", Static).update(
                f"▲ {count} saved {noun} after restarting {who}")

    @on(Button.Pressed, ".banner-restart")
    def _restart(self, event: Button.Pressed) -> None:
        event.stop()
        self.app.action_restart()
