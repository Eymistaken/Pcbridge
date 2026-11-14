"""The dialogs: one frame for all of them.

66 columns wide, a rounded border (warning tint for a question that
unlocks, danger tint for one that destroys), a bold title, muted text and
the buttons right-aligned with their keys.
"""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from .widgets import CheckLine, Segmented, key_label

CSS = """
Confirm, UnlockConfirm, ShowOnce, PasswordScreen, LoginScreen, HelpScreen {
    align: center middle;
    background: black 50%;
}
.dialog {
    width: 66;
    max-width: 100%;
    height: auto;
    padding: 1 2;
    border: round $pb-rule;
    background: $pb-panel;
}
.dialog.unlocks {
    border: round $pb-warning;
}
.dialog.destroys {
    border: round $pb-error;
}
.dialog-title {
    text-style: bold;
    color: white;
    margin-bottom: 1;
}
.dialog-text {
    color: $pb-muted;
}
.dialog-buttons {
    height: auto;
    margin-top: 1;
    align-horizontal: right;
}
.dialog-buttons Button {
    margin-left: 1;
    margin-right: 0;
}
.dialog-row {
    height: auto;
    margin-top: 1;
}
.dialog-row .label {
    width: 8;
}
#pw-error {
    color: $pb-error;
}
#once-secret {
    margin: 1 0;
    padding: 0 1;
    border: tall $pb-accent;
    text-style: bold;
    content-align: center middle;
}
"""


def minutes_label(minutes: int) -> str:
    if minutes >= 60 and minutes % 60 == 0:
        return f"{minutes // 60} h"
    return f"{minutes} min"


def durations(default: int, maximum: int) -> list[int]:
    """The unlock durations offered: 5, 15, 30, 60, 120 minutes, the default
    always among them, the list stopping at the maximum."""
    out = {m for m in (5, 15, 30, 60, 120) if m <= maximum}
    out.add(min(default, maximum))
    if maximum < 120:
        out.add(maximum)
    return sorted(m for m in out if m > 0)


class Confirm(ModalScreen[bool]):
    """A yes/no question. Escape and the first button say no, Enter says yes.

    `kind` tints the border: "unlocks" (warning) or "destroys" (danger).
    """

    BINDINGS = [Binding("escape", "no", "Cancel"), Binding("enter", "yes", "Confirm", priority=True)]

    def __init__(self, question: str, yes: str = "Yes", no: str = "Cancel", detail: str = "",
                 kind: str = "") -> None:
        super().__init__()
        self.question = question
        self.detail = detail
        self.yes = yes
        self.no = no
        self.kind = kind

    def compose(self) -> ComposeResult:
        title, _, rest = self.question.partition("\n\n")
        with Vertical(id="confirm-box", classes=f"dialog {self.kind}"):
            yield Static(title, id="confirm-question", classes="dialog-title")
            text = "\n\n".join(t for t in (rest, self.detail) if t)
            if text:
                yield Static(text, classes="dialog-text")
            with Horizontal(classes="dialog-buttons"):
                yield Button(key_label(self.no, "esc"), id="confirm-no", compact=True)
                yield Button(key_label(self.yes, "enter"), id="confirm-yes", compact=True,
                             classes="danger" if self.kind == "destroys" else "primary")

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")

    def action_no(self) -> None:
        self.dismiss(False)

    def action_yes(self) -> None:
        self.dismiss(True)


class UnlockConfirm(ModalScreen["int | None"]):
    """Unlocking from the `l` key asks, with the duration picker."""

    BINDINGS = [Binding("escape", "no", "Cancel"), Binding("enter", "yes", "Unlock", priority=True)]

    def __init__(self, minutes: int, choices: list[int], idle_seconds: int, maximum: int) -> None:
        super().__init__()
        self.minutes = minutes
        self.choices = choices
        self.idle_seconds = idle_seconds
        self.maximum = maximum

    def compose(self) -> ComposeResult:
        from ..cli.grant import format_duration

        with Vertical(id="confirm-box", classes="dialog unlocks"):
            yield Static("Unlock desktop control?", id="confirm-question", classes="dialog-title")
            with Horizontal(classes="dialog-row"):
                yield Static("For", classes="label")
                yield Segmented([(str(m), minutes_label(m)) for m in self.choices],
                                value=str(self.minutes), id="confirm-minutes")
            idle = (f"It locks after {self.idle_seconds} s without agent input, at the latest in "
                    f"{minutes_label(self.maximum)}." if self.idle_seconds else
                    f"It locks after {format_duration(self.minutes * 60)}.")
            yield Static(f"\nAgents can then use the keyboard, the pointer and the screen. {idle} "
                         "l or Lock now closes it again.", classes="dialog-text")
            with Horizontal(classes="dialog-buttons"):
                yield Button(key_label("Cancel", "esc"), id="confirm-no", compact=True)
                yield Button(key_label(f"Unlock for {minutes_label(self.minutes)}", "enter"),
                             id="confirm-yes", compact=True, classes="primary")

    def on_mount(self) -> None:
        self.query_one(Segmented).focus()

    @on(Segmented.Changed, "#confirm-minutes")
    def _minutes(self, event: Segmented.Changed) -> None:
        self.minutes = int(event.value)
        self.query_one("#confirm-yes", Button).label = key_label(
            f"Unlock for {minutes_label(self.minutes)}", "enter")

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self.dismiss(self.minutes if event.button.id == "confirm-yes" else None)

    def action_no(self) -> None:
        self.dismiss(None)

    def action_yes(self) -> None:
        self.dismiss(self.minutes)


def copy_secret(app: App, text: str) -> tuple[bool, str]:
    """Put a secret on the clipboard; (reached the desktop clipboard, what happened).

    wl-copy reaches the desktop clipboard from any terminal; without it the
    terminal is asked through OSC 52, which some terminals (GNOME Terminal
    among them) ignore.
    """
    from ..remotepassword import copy_to_clipboard

    if copy_to_clipboard(text):
        return True, "✓ Copied with wl-copy"
    app.copy_to_clipboard(text)
    return False, "Asked the terminal to copy it (OSC 52); check that it arrived"


class ShowOnce(ModalScreen[None]):
    """A new secret, shown the only time it can be: until the user says it is saved.

    No timer and no Escape: Done stays disabled until the box "I saved it" is
    ticked, so there is time for a screenshot or a password manager.
    """

    BINDINGS = [Binding("enter", "done", "Done", priority=True), Binding("y", "copy", "Copy")]

    def __init__(self, title: str, secret: str, where: str = "") -> None:
        super().__init__()
        self.title_text = title
        self.secret = secret
        self.where = where

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box", classes="dialog"):
            yield Static(self.title_text, id="confirm-question", classes="dialog-title")
            if self.where:
                yield Static(self.where, classes="dialog-text")
            yield Static(Text(self.secret), id="once-secret")
            with Horizontal(classes="row"):
                yield Button(key_label("Copy", "y"), id="once-copy", compact=True)
                yield Static("", id="once-copied", classes="muted")
            yield Static("\nThis is the only time it is shown: only its hash is stored, so it "
                         "cannot be read back, only replaced.", classes="hint")
            yield CheckLine("I saved it in a password manager", id="once-saved")
            with Horizontal(classes="dialog-buttons"):
                yield Button(key_label("Done", "enter"), id="once-done", classes="primary",
                             compact=True, disabled=True)

    @on(Button.Pressed, "#once-copy")
    def action_copy(self) -> None:
        reached, text = copy_secret(self.app, self.secret)
        label = self.query_one("#once-copied", Static)
        label.update(Text(text))
        label.set_class(reached, "ok")

    @on(CheckLine.Changed, "#once-saved")
    def _saved(self, event: CheckLine.Changed) -> None:
        self.query_one("#once-done", Button).disabled = not event.value

    @on(Button.Pressed, "#once-done")
    def action_done(self) -> None:
        if not self.query_one("#once-done", Button).disabled:
            self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    """`?`: this tab's keys and the keys that work everywhere."""

    BINDINGS = [Binding("escape,question_mark,q", "close", "Close")]

    def __init__(self, title: str, keys: list[tuple[str, str]]) -> None:
        super().__init__()
        self.title_text = title
        self.keys = keys

    def compose(self) -> ComposeResult:
        from .theme import PALETTE

        out = Text()
        for key, label in self.keys:
            out.append(f"{key:>10}  ", style=f"bold {PALETTE['accent']}")
            out.append(f"{label}\n", style=PALETTE["muted"])
        with Vertical(classes="dialog"):
            yield Static(self.title_text, classes="dialog-title")
            yield Static(out)
            with Horizontal(classes="dialog-buttons"):
                yield Button(key_label("Close", "esc"), id="help-close", compact=True)

    @on(Button.Pressed, "#help-close")
    def action_close(self) -> None:
        self.dismiss(None)

