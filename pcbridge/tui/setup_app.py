"""The setup screen: `pcbridge setup` on a terminal.

One screen and no tabs. A bridge across the top gets a plank per step
(hello, parts, clients, review, install, path when it is needed, ready)
from "this pc" to the agents; the current plank fills, the steps already
done can be clicked to go back until the install starts. Choices are taken
with the arrow keys, space, Enter or the mouse; nothing changes before
Install. The install itself is `setupflow.execute`, the same code the
printed setup runs: its lines arrive through `install.set_sink` and become
rows with a spinner, a bar and the last thing each step did. The sudo
password is asked with the terminal handed back for a moment
(`App.suspend`), so pcbridge never sees it.

Empty cells twinkle with a few stars, more as the bridge grows; a
shooting star crosses now and then. TEXTUAL_ANIMATIONS=none keeps them
still (the spinners still turn: they say that work is going on).
"""

from __future__ import annotations

import math
import os
import platform as pyplatform
import random
import shlex
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Button, ContentSwitcher, Static

from .. import __version__
from .. import distro as distrolib
from ..cli import connect as connectlib
from ..cli import install as inst
from ..cli import setupflow
from .logo import FIXED_BASE, Logo, mix
from .theme import CSS as THEME_CSS
from .theme import PALETTE, SPINNER, THEME, css_variables
from .widgets import key_label

RGB = tuple[int, int, int]
FPS = 10
NARROW = 100            # below this many columns the narrow layout is used

LABELS = {"welcome": "hello", "parts": "parts", "clients": "clients", "review": "review",
          "install": "install", "path": "path", "done": "ready"}
TITLES = {"welcome": "Welcome", "parts": "Components", "clients": "Clients", "review": "Review",
          "install": "Installing", "path": "Shell PATH", "done": "Ready"}
BEFORE_INSTALL = ("welcome", "parts", "clients", "review")

STOPPED, DONE, OPEN = "stopped", "done", "open"
# Tesseract's codes for the languages setup adds most often.
LANGUAGES = {"eng": "English", "tur": "Turkish", "deu": "German", "fra": "French", "spa": "Spanish",
             "ita": "Italian", "por": "Portuguese", "rus": "Russian", "nld": "Dutch", "pol": "Polish"}


def rgb(hex_: str) -> RGB:
    hex_ = hex_.lstrip("#")
    return (int(hex_[0:2], 16), int(hex_[2:4], 16), int(hex_[4:6], 16))


def hexc(c: RGB) -> str:
    return f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}"


C = {k: PALETTE[k] for k in PALETTE}
FUTURE = "#3a4552"      # planks not reached yet
TRACK = "#232c35"       # the empty part of a bar


def shorten_homes(text: str) -> str:
    """Every /home/<user>/ in a line, as ~/ (the rows are short)."""
    home = os.path.expanduser("~")
    return text.replace(home + "/", "~/") if home not in ("", "/") else text


def short_home(path: object) -> str:
    text = str(path)
    home = os.path.expanduser("~")
    return "~" + text[len(home):] if text.startswith(home + "/") or text == home else text


# ---------------------------------------------------------------------------
# Stars
# ---------------------------------------------------------------------------

STAR_CHARS = "·······⋅⋅˙˙+✦✧"
STAR_COLORS = [rgb(PALETTE["accent-light"]), rgb(PALETTE["kind-desktop"]), rgb(PALETTE["warning"]),
               rgb(PALETTE["text"]), rgb(PALETTE["working"])]


@dataclass(frozen=True)
class Star:
    x: int
    y: int
    ch: str
    color: RGB
    period: float       # seconds per twinkle
    phase: float
    rank: float         # shown once the sky level is above it


def make_stars(width: int, height: int, seed: str, density: float = 0.03) -> dict[int, list[Star]]:
    """The same stars for the same region and seed, by row."""
    rng = random.Random(f"{seed}:{width}x{height}")
    count = int(width * height * density)
    cells = rng.sample(range(width * height), min(count, width * height)) if width and height else []
    rows: dict[int, list[Star]] = {}
    for cell in cells:
        y, x = divmod(cell, width)
        star = Star(x, y, rng.choice(STAR_CHARS), rng.choice(STAR_COLORS),
                    2.4 + rng.random() * 3.2, rng.random(), rng.random())
        rows.setdefault(y, []).append(star)
    for row in rows.values():
        row.sort(key=lambda s: s.x)
    return rows


class SkyFill(Widget):
    """Empty space with the twinkling stars the sky level allows."""

    DEFAULT_CSS = """
    SkyFill {
        width: 1fr;
        height: 1fr;
    }
    """

    def __init__(self, seed: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.seed = seed
        self._stars: dict[int, list[Star]] = {}
        self._sky_size: tuple[int, int] = (0, 0)

    def render_line(self, y: int) -> Strip:
        width, height = self.size.width, self.size.height
        if (width, height) != self._sky_size:
            self._sky_size = (width, height)
            self._stars = make_stars(width, height, self.seed)
        app = self.app
        segments: list[Segment] = []
        x = 0
        for star in self._stars.get(y, []):
            if not getattr(app, "star_visible", lambda s: False)(star):
                continue
            segments.append(Segment(" " * (star.x - x)))
            segments.append(Segment(star.ch, Style(color=app.star_color(star))))
            x = star.x + 1
        segments.append(Segment(" " * max(0, width - x)))
        return Strip(segments, width)


# ---------------------------------------------------------------------------
# The bridge
# ---------------------------------------------------------------------------


class Bridge(Widget):
    """"this pc" and the agents, a plank per step between them, stars around.

    Five rows: the sky (a shooting star passes), the two boxes with the
    planks on their middle row, the pylons, the labels.
    """

    DEFAULT_CSS = """
    Bridge {
        width: 1fr;
        height: 5;
    }
    """

    class Picked(Message):
        """A plank's label was clicked."""

        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.keys: list[str] = []
        self.index = 0
        self.fill = 0.5
        self.done_since: int | None = None    # frame the last step began
        self._stars: dict[int, list[Star]] = {}
        self._stars_w = -1
        self._spans: list[tuple[int, int]] = []

    def set_state(self, keys: list[str], index: int, fill: float, done_since: int | None) -> None:
        self.keys, self.index, self.fill, self.done_since = keys, index, fill, done_since
        self.refresh()

    # -- geometry ----------------------------------------------------------------

    def plank_len(self) -> int:
        return 6 if self.size.width < NARROW else 11

    def travel(self) -> tuple[int, bool]:
        """Where the dot is on the finished bridge, and whether it arrived."""
        total = len(self.keys) * self.plank_len()
        if self.done_since is None:
            return -1, False
        app = self.app
        if not getattr(app, "motion", True):
            return total, True
        pos = (app.frame - self.done_since) * 3
        return min(pos, total), pos >= total

    def arrived_since(self) -> int | None:
        if self.done_since is None:
            return None
        if not getattr(self.app, "motion", True):
            return self.done_since
        return self.done_since + math.ceil(len(self.keys) * self.plank_len() / 3)

    def canvas(self) -> list[list[tuple[str, Style | None]]]:
        width = self.size.width
        grid: list[list[tuple[str, Style | None]]] = [[(" ", None)] * width for _ in range(5)]

        def put(x: int, y: int, text: str, style: Style | None) -> None:
            for i, ch in enumerate(text):
                if 0 <= x + i < width:
                    grid[y][x + i] = (ch, style)

        app = self.app
        frame = getattr(app, "frame", 0)
        motion = getattr(app, "motion", True)
        n = len(self.keys)
        length = self.plank_len()
        total = 6 + 1 + n * (length + 1) + 7
        x0 = max(0, (width - total) // 2)
        accent = Style(color=C["accent"], bold=True)
        # this pc
        cursor = "_" if not motion or (frame // 5) % 2 == 0 else " "
        put(x0, 1, "┏━━━━┓", accent)
        put(x0, 2, "┃ >" + cursor + " ┃", accent)
        put(x0, 3, "┗━━━━┛", accent)
        put(x0, 4, "this pc", Style(color=C["muted"]))
        done_step = self.done_since is not None
        pos, arrived = self.travel()
        self._spans = []
        px = x0 + 7
        for i, key in enumerate(self.keys):
            x = px + i * (length + 1)
            self._spans.append((x, x + length))
            reached = i < self.index or done_step
            if done_step:
                off = pos - i * length
                for j in range(length):
                    if not arrived and off == j:
                        put(x + j, 2, "●", Style(color=C["working"], bold=True))
                    else:
                        put(x + j, 2, "━", accent)
            elif i < self.index:
                put(x, 2, "━" * length, accent)
            elif i == self.index:
                k = max(1, min(length - 1, round(self.fill * (length - 1))))
                put(x, 2, "━" * k, Style(color=C["accent-light"], bold=True))
                bright = motion and (frame // 4) % 2 == 0
                put(x + k, 2, "●", Style(color="#9cc2ff" if bright else C["working"], bold=True))
                put(x + k + 1, 2, "╌" * (length - 1 - k), Style(color=FUTURE))
            else:
                put(x, 2, "╌" * length, Style(color=FUTURE))
            put(x + length // 2, 3, "┃", Style(color=C["accent"] if i <= self.index or done_step else FUTURE))
            label = LABELS.get(key, key) if length >= 8 else str(i + 1)
            label = label.center(length)[:length]
            if reached:
                style = Style(color=C["accent"])
            elif i == self.index:
                style = Style(color=C["text"], bold=True)
            else:
                style = Style(color=C["dim"])
            put(x, 4, label, style)
        rx = px + n * (length + 1)
        since = self.arrived_since()
        if arrived:
            color = C["ok"]
        elif done_step:
            color = C["accent"]
        else:
            color = C["muted"]
        eyes = "█ █"
        if arrived and since is not None and frame - since < 18 and motion:
            eyes = "▀ ▀"
        elif motion and frame % 46 < 2:
            eyes = "▁ ▁"
        box = Style(color=color, bold=True)
        put(rx, 1, "┏━━━━━┓", box)
        put(rx, 2, "┃ " + eyes + " ┃", box)
        put(rx, 3, "┗━━━━━┛", box)
        put(rx, 4, "agents", Style(color=C["muted"]))
        # Stars in the empty cells, and now and then a shooting star on top.
        if self._stars_w != width:
            self._stars_w = width
            self._stars = make_stars(width, 5, "bridge", density=0.05)
        span = (x0 - 2, rx + 9)      # rows 1-4 keep the bridge itself clear
        for y, row in self._stars.items():
            for star in row:
                if y and span[0] <= star.x < span[1]:
                    continue
                if grid[y][star.x][0] == " " and app.star_visible(star):
                    grid[y][star.x] = (star.ch, Style(color=app.star_color(star)))
        if motion and width > 20:
            phase = frame % 110
            if phase < 36:
                head = int(phase * (width + 8) / 36) - 4
                for j, ch in enumerate("·∙•✦"):
                    x = head - 3 + j
                    if 0 <= x < width and grid[0][x][0] == " ":
                        tint = mix(FIXED_BASE, rgb(C["accent-light"]), 0.3 + 0.23 * j)
                        grid[0][x] = (ch, Style(color=hexc(tint)))
        return grid

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        app = self.app
        key = (getattr(app, "frame", 0), width, self.index, round(self.fill, 3), self.done_since,
               tuple(self.keys), getattr(app, "_sky", 0))
        if getattr(self, "_canvas_key", None) != key:
            self._canvas_key = key
            self._canvas_rows = self.canvas()
        row = self._canvas_rows[y] if y < 5 else [(" ", None)] * width
        return Strip([Segment(ch, st) for ch, st in row], width)

    def on_click(self, event: events.Click) -> None:
        for i, (start, end) in enumerate(self._spans):
            if start <= event.x < end and event.y in (2, 3, 4):
                self.post_message(self.Picked(i))
                return


class HRule(Widget):
    DEFAULT_CSS = """
    HRule {
        width: 1fr;
        height: 1;
    }
    """

    def render(self) -> Text:
        return Text("─" * self.size.width, style=PALETTE["rule"])


class RevealLogo(Logo):
    """The pcbridge wordmark, drawn in from the left once, a cursor at its edge.

    It glows only over a background the terminal reported (OSC 11): blended
    over a guessed one, the glow is a box of the wrong color (seen in tmux,
    which does not answer).
    """

    def __init__(self, glow: bool = True, **kwargs: Any) -> None:
        super().__init__(glyph="word", state="active" if glow else "idle", **kwargs)
        self.reveal = 10 ** 6

    def pixels(self) -> dict[tuple[int, int], tuple[int, int, int]]:
        pix = super().pixels()
        if self.reveal >= self.pw:
            return pix
        out = {p: c for p, c in pix.items() if p[0] < self.reveal}
        for y in range(self.mt, self.mt + self.bh):
            out[(self.reveal, y)] = rgb(C["accent"])
        return out


# ---------------------------------------------------------------------------
# The checklist and the task rows
# ---------------------------------------------------------------------------


@dataclass
class Item:
    key: str
    title: str
    desc: str = ""
    sub: str = ""
    tag: str = ""
    on: bool = True
    locked: bool = False
    toggled_at: int = -99


class Checklist(Widget, can_focus=True):
    """Rows to tick: ↑↓ move, space ticks, a click ticks the row clicked."""

    DEFAULT_CSS = """
    Checklist {
        width: 1fr;
        height: auto;
    }
    """
    BINDINGS = [
        Binding("up", "move(-1)", show=False),
        Binding("down", "move(1)", show=False),
        Binding("k", "move(-1)", show=False),
        Binding("j", "move(1)", show=False),
        Binding("space", "toggle", show=False),
    ]

    class Changed(Message):
        def __init__(self, checklist: "Checklist") -> None:
            super().__init__()
            self.checklist = checklist

    def __init__(self, items: list[Item], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.items = items
        self.cursor = 0

    def _lines(self, item: Item) -> int:
        return 2 if item.sub else 1

    def get_content_height(self, container: Any, viewport: Any, width: int) -> int:
        return max(1, sum(self._lines(i) for i in self.items))

    def render(self) -> Text:
        out = Text()
        frame = getattr(self.app, "frame", 0)
        width = max(10, self.size.width)
        for i, item in enumerate(self.items):
            line = Text()
            here = i == self.cursor
            line.append("❯ " if here and self.has_focus else "  ", style=C["accent"])
            if item.locked:
                line.append("[◆]", style=C["dim"])
            elif item.on:
                fresh = frame - item.toggled_at < 3
                line.append("[", style=C["ok"])
                line.append("✓", style=("bold #ffffff" if fresh else f"bold {C['ok']}"))
                line.append("]", style=C["ok"])
            else:
                line.append("[ ]", style=C["muted"])
            line.append(" ")
            line.append(item.title, style="bold" if not item.locked else C["muted"])
            tag_len = len(item.tag) + 4 if item.tag else 0
            if item.desc and line.cell_len + 2 + len(item.desc) + tag_len <= width:
                line.append("  " + item.desc, style=C["muted"] if item.sub else C["dim"])
            if item.tag:
                tag = f" {item.tag} "
                pad = max(2, width - line.cell_len - len(tag))
                line.append(" " * pad)
                line.append(tag, style=f"bold {PALETTE['box']} on {C['ok']}" if item.tag == "Recommended"
                            else C["muted"])
            rows = [line]
            if item.sub:
                rows.append(Text("      " + item.sub, style=C["dim"]))
            for row in rows:
                row.truncate(width)
                if here:
                    row.pad_right(width - row.cell_len)
                    row.stylize(f"on {C['selection']}")
                out.append_text(row)
                out.append("\n")
        out.rstrip()
        return out

    def action_move(self, step: int) -> None:
        if self.items:
            self.cursor = (self.cursor + step) % len(self.items)
            self.refresh()

    def action_toggle(self) -> None:
        self.toggle(self.cursor)

    def toggle(self, index: int) -> None:
        if not (0 <= index < len(self.items)):
            return
        item = self.items[index]
        self.cursor = index
        if not item.locked:
            item.on = not item.on
            item.toggled_at = getattr(self.app, "frame", 0)
            self.post_message(self.Changed(self))
        self.refresh()

    def on_click(self, event: events.Click) -> None:
        row = 0
        for i, item in enumerate(self.items):
            row += self._lines(item)
            if event.y < row:
                self.focus()
                self.toggle(i)
                return

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()


@dataclass
class Task:
    key: str
    title: str
    state: str = "wait"        # wait, run, sudo, ok, warn, fail
    detail: str = ""
    live: str = ""
    started: float = 0.0
    ended: float = 0.0
    messages: list[tuple[str, str]] = field(default_factory=list)


class TaskList(Widget):
    """One row per step: state, name, a bar, the time and what it did last."""

    DEFAULT_CSS = """
    TaskList {
        width: 1fr;
        height: auto;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tasks: list[Task] = []

    def get_content_height(self, container: Any, viewport: Any, width: int) -> int:
        return max(1, len(self.tasks))

    def render(self) -> Text:
        app = self.app
        frame = getattr(app, "frame", 0)
        width = max(40, self.size.width)
        bar = 24 if width >= 90 else 10
        name_w = 23 if width >= 90 else 22
        out = Text()
        now = time.monotonic()
        for t in self.tasks:
            glyph, gcolor = {
                "wait": ("○", C["dim"]), "run": (SPINNER[frame % len(SPINNER)], C["working"]),
                "sudo": ("◇", C["warning"]), "ok": ("✓", C["ok"]), "warn": ("▲", C["warning"]),
                "fail": ("✕", C["error"]),
            }[t.state]
            line = Text()
            line.append(f" {glyph} ", style=gcolor)
            line.append(t.title[: name_w - 1].ljust(name_w), style=C["muted"] if t.state == "wait" else C["text"])
            if t.state in ("run", "sudo"):
                track = [TRACK] * bar
                if t.state == "run":
                    span = 6
                    cycle = 2 * (bar - span)
                    pos = frame % cycle if cycle else 0
                    pos = pos if pos <= bar - span else cycle - pos
                    for j in range(pos, pos + span):
                        track[j] = C["accent"]
                for color in track:
                    line.append("━", style=color)
            else:
                color = {"wait": TRACK, "ok": C["ok"], "warn": C["warning"], "fail": C["error"]}[t.state]
                line.append("━" * bar, style=color)
            line.append("  ")
            if t.state in ("run", "sudo"):
                secs = f"{int(now - t.started)} s"
            elif t.state in ("ok", "warn", "fail"):
                secs = f"{t.ended - t.started:.1f} s"
            else:
                secs = ""
            line.append(secs.rjust(6), style=C["dim"])
            line.append("  ")
            if t.state == "sudo":
                detail, dcolor = "asking for your password", C["warning"]
            elif t.state == "run":
                detail, dcolor = t.live or "…", C["muted"]
            elif t.state == "warn":
                detail, dcolor = t.detail, C["warning"]
            elif t.state == "fail":
                detail, dcolor = t.detail, C["error"]
            else:
                detail, dcolor = t.detail, C["dim"]
            room = width - line.cell_len - 1
            if room > 3:
                detail = detail if len(detail) <= room else detail[: room - 1] + "…"
                line.append(detail, style=dcolor)
            line.truncate(width)
            if t.state in ("run", "sudo"):
                line.pad_right(width - line.cell_len)
                line.stylize(f"on {C['selection-hover']}")
            out.append_text(line)
            out.append("\n")
        out.rstrip()
        return out


class KeyHints(Static):
    """The keys of this step, the key in accent bold, the label muted."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self.entries: list[tuple[str, str]] = []

    def set_entries(self, entries: list[tuple[str, str]]) -> None:
        self.entries = entries
        self.refresh()

    def render(self) -> Text:
        out = Text()
        for i, (key, label) in enumerate(self.entries):
            if i:
                out.append("   ")
            out.append(key, style=f"bold {C['accent']}")
            out.append(f" {label}", style=C["muted"])
        right = "the mouse works too"
        if self.size.width >= NARROW:
            out.append(" " * max(2, self.size.width - out.cell_len - len(right) - 2))
            out.append(right, style=C["dim"])
        return out


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------

SETUP_CSS = """
Screen {
    layout: vertical;
}
#top {
    height: 1;
    padding: 0 2;
}
#title {
    width: 1fr;
}
#where {
    width: auto;
    color: $pb-muted;
}
#body {
    height: 1fr;
}
SkyFill.gutter {
    width: 3;
}
Screen.narrow SkyFill.gutter {
    width: 1;
}
#steps {
    width: 1fr;
    height: 1fr;
}
#steps > Vertical {
    height: 1fr;
    padding: 1 1 0 1;
    overflow-y: auto;
    scrollbar-size-vertical: 1;
}
Screen.short #steps > Vertical {
    padding-top: 0;
}
Screen.short .section {
    margin-top: 0;
}
.title {
    text-style: bold;
}
.section {
    color: $pb-muted;
    text-style: bold;
    margin-top: 1;
}
.buttons {
    dock: bottom;
    height: 2;
    padding-bottom: 1;
}
Screen.short .buttons {
    height: 1;
    padding-bottom: 0;
}
.cmdbox {
    background: $pb-box;
    color: $pb-banner-text;
    padding: 0 1;
    width: 1fr;
}
#welcome-cols {
    height: auto;
}
#welcome-left {
    width: 60;
    height: auto;
}
#welcome-right {
    width: 1fr;
    height: auto;
    padding-left: 4;
}
#lead {
    margin-top: 1;
}
Screen.narrow #welcome-cols {
    layout: vertical;
}
Screen.narrow #welcome-left {
    width: 1fr;
}
Screen.narrow #welcome-right {
    padding-left: 0;
    margin-top: 1;
}
Screen.narrow RevealLogo {
    display: none;
}
#cmd-row {
    height: auto;
    margin-top: 1;
}
#review-notes {
    margin-top: 1;
}
#cmd-row Button {
    margin-left: 1;
}
#logbox {
    display: none;
    height: auto;
    max-height: 10;
    margin-top: 1;
    background: $pb-box;
    color: $pb-dim;
    padding: 0 1;
}
#logbox.shown {
    display: block;
}
#install-head {
    height: 1;
}
#install-title {
    width: 1fr;
    text-style: bold;
}
#elapsed {
    width: auto;
    color: $pb-muted;
}
#tasks {
    margin-top: 1;
}
#install-end {
    margin-top: 1;
}
#go-continue, #path-continue {
    display: none;
}
#go-continue.shown, #path-continue.shown {
    display: block;
}
#path-line {
    margin-top: 1;
}
#done-cols {
    height: auto;
    margin-top: 1;
}
#done-left {
    width: 46;
    height: auto;
}
#done-right {
    width: 1fr;
    height: auto;
}
Screen.narrow #done-cols {
    layout: vertical;
}
Screen.narrow #done-left {
    width: 1fr;
}
#keys {
    dock: bottom;
    height: 1;
    background: $pb-panel;
    padding: 0 1;
}
"""

LEAD = ("Lets your coding agents use this computer: terminals, files, background jobs and, "
        "when you allow it, the desktop.")


class SetupApp(App[str]):
    """`pcbridge setup` as a screen. `executor` and `sudo_runner` exist for the tests."""

    CSS = THEME_CSS + SETUP_CSS
    BINDINGS = [
        Binding("enter", "primary", show=False),
        Binding("escape", "back", show=False),
        Binding("q", "quit", show=False),
        Binding("ctrl+q", "quit", show=False, priority=True),
        Binding("ctrl+c", "quit", show=False, priority=True),
        Binding("l", "log", show=False),
        Binding("c", "copy", show=False),
        Binding("y", "path_answer('yes')", show=False),
        Binding("n", "path_answer('no')", show=False),
    ]

    def __init__(self, ctx: setupflow.Context, *, terminal_background: RGB | None = None,
                 executor: Callable[..., setupflow.Outcome] | None = None,
                 sudo_runner: Callable[[list[str]], bool] | None = None,
                 motion: bool | None = None) -> None:
        super().__init__(ansi_color=True)
        self.register_theme(THEME)
        self.theme = "pcbridge"
        self.ctx = ctx
        self.base = terminal_background or FIXED_BASE
        self.background_known = terminal_background is not None
        self.executor = executor or setupflow.execute
        self.sudo_runner = sudo_runner
        self._motion = motion
        self.frame = 0
        self.entered = 0
        self.started_at = 0.0
        self.finished_at = 0.0
        self.log_lines: deque[str] = deque(maxlen=400)
        self.outcome: setupflow.Outcome | None = None
        self.error: str = ""
        self.installing = False
        self.finished = False
        self.path_choice: str | None = None
        self.path_result = ""
        self.done_since: int | None = None
        args = ctx.args
        self.keys = ["welcome", "parts"] + ([] if args.no_connect else ["clients"]) + ["review", "install"]
        if inst.path_fix() is not None:
            self.keys.append("path")
        self.keys.append("done")
        self.step = "welcome"
        self.optional = [self._component_item(c) for c in ctx.plan.components]
        self.client_items, self.clients_missing = self._client_items()

    # -- motion and the sky -------------------------------------------------------

    @property
    def motion(self) -> bool:
        if self._motion is not None:
            return self._motion
        return self.animation_level != "none"

    def sky_level(self) -> float:
        if self.step == "done":
            return 1.01
        idx = self.keys.index(self.step)
        extra = self.install_fraction() if self.step == "install" else 0.0
        return 0.28 + 0.72 * (idx + extra) / max(1, len(self.keys) - 1)

    def star_visible(self, star: Star) -> bool:
        return star.rank < self._sky

    def star_color(self, star: Star) -> str:
        if self.motion:
            t = self.frame / FPS
            b = 0.5 - 0.5 * math.cos(2 * math.pi * (t / star.period + star.phase))
        else:
            b = 0.5
        bright = 1.0 if self.step == "done" else 0.62
        return hexc(mix(self.base, star.color, 0.08 + bright * 0.75 * b))

    # -- choices ---------------------------------------------------------------------

    def _component_item(self, c: Any) -> Item:
        if c.key == "ocr":
            title, desc = "OCR", "lets agents find text on the screen (find_text, wait_for_text)"
            langs = " + ".join(LANGUAGES.get(code, code) for code in c.languages)
        else:
            title, desc, langs = c.title, "", ""
        if c.installed:
            return Item(c.key, title, desc, "installed" + (f" · {langs}" if langs else ""), "", True, True)
        sub = ", ".join(c.packages) + (f" · {langs}" if langs else "") + " · needs sudo"
        return Item(c.key, title, desc, sub, "Recommended", c.ticked, False)

    def _client_items(self) -> tuple[list[Item], list[str]]:
        items, missing = [], []
        for client in connectlib.ALL_CLIENTS:
            name = connectlib.NAMES[client]
            reg = connectlib.current(client)
            if not connectlib.client_available(client) and not reg.present:
                missing.append(name)
                continue
            connected = reg.present and reg.enabled
            default = client in connectlib.CLIENTS
            tag = "connected" if connected else ("" if default else "on request")
            items.append(Item(client, name.ljust(18), short_home(reg.source) if reg.source else "", "",
                              tag, default or connected))
        return items, missing

    def chosen_clients(self) -> list[str]:
        return [i.key for i in self.client_items if i.on]

    def apply_choices(self) -> None:
        on = {i.key: i.on for i in self.optional}
        for c in self.ctx.plan.components:
            if not c.installed:
                c.ticked = on.get(c.key, c.ticked)

    @property
    def install_now(self) -> bool:
        return bool(self.ctx.plan.chosen_packages()) and not self.ctx.args.no_packages

    def package_command(self) -> str:
        plan = self.ctx.plan
        argv = plan.install_argv()
        if argv:
            return shlex.join(argv)
        names = plan.chosen_packages()
        return distrolib.install_command(raw=tuple(names), fam=plan.family) if names else ""

    # -- layout -----------------------------------------------------------------------

    def get_css_variables(self) -> dict[str, str]:
        return {**super().get_css_variables(), **css_variables()}

    def compose(self) -> ComposeResult:
        self._sky = 0.28
        with Horizontal(id="top"):
            yield Static(self._title_text(), id="title")
            yield Static("", id="where")
        yield Bridge(id="bridge")
        yield HRule()
        with Horizontal(id="body"):
            yield SkyFill("left", classes="gutter")
            with ContentSwitcher(initial="welcome", id="steps"):
                yield from self._compose_welcome()
                yield from self._compose_parts()
                yield from self._compose_clients()
                yield from self._compose_review()
                yield from self._compose_install()
                yield from self._compose_path()
                yield from self._compose_done()
            yield SkyFill("right", classes="gutter")
        yield KeyHints(id="keys")

    def _title_text(self) -> Text:
        t = Text()
        t.append("pcbridge", style=f"bold {C['accent']}")
        t.append(" setup", style=f"bold {C['text']}")
        t.append(f"  {__version__}", style=C["dim"])
        return t

    def _buttons(self, *buttons: Button) -> Horizontal:
        return Horizontal(*buttons, classes="buttons")

    def _compose_welcome(self) -> ComposeResult:
        with Vertical(id="welcome"):
            with Horizontal(id="welcome-cols"):
                with Vertical(id="welcome-left"):
                    yield RevealLogo(glow=self.background_known, id="logo", base=self.base)
                    yield Static(LEAD, id="lead")
                    lead2 = Text("About a minute. Nothing on this computer changes before you press ",
                                 style=C["muted"])
                    lead2.append("Install", style=C["accent-light"])
                    lead2.append(".", style=C["muted"])
                    yield Static(lead2, id="lead2")
                with Vertical(id="welcome-right"):
                    yield Static("Looking at this computer", classes="section")
                    yield Static("", id="probes")
            yield SkyFill("welcome")
            yield self._buttons(Button(key_label("Start", "⏎"), id="go-start", compact=True, classes="primary"),
                                Button(key_label("Quit", "q"), id="go-quit", compact=True))

    def _compose_parts(self) -> ComposeResult:
        with Vertical(id="parts"):
            yield Static("Choose what to install", classes="title")
            hint = Text("Click a row, or move with ↑↓ and tick with space. You can run ", style=C["muted"])
            hint.append("pcbridge setup", style=C["accent-light"])
            hint.append(" again later.", style=C["muted"])
            yield Static(hint)
            yield Static("Needed · always installed", classes="section")
            yield Static(self._needed_text(), id="needed")
            yield Static("Optional", classes="section")
            if self.optional:
                yield Checklist(self.optional, id="optional")
            else:
                yield Static(Text("  ○  Nothing optional on this computer: OCR reads the screen, and desktop "
                                  "control is not available here.", style=C["muted"]), id="optional-none")
            yield Static("", id="parts-note")
            yield SkyFill("parts")
            yield self._buttons(Button(key_label("Next", "⏎"), id="go-next-parts", compact=True, classes="primary"),
                                Button(key_label("Back", "esc"), id="go-back-parts", compact=True))

    def _compose_clients(self) -> ComposeResult:
        with Vertical(id="clients"):
            yield Static("Connect your coding agents", classes="title")
            yield Static(Text("pcbridge adds itself to each ticked client's MCP settings; the file is backed "
                              "up first. Unticked clients are left as they are.", style=C["muted"]))
            yield Static("Found on this computer", classes="section")
            if self.client_items:
                yield Checklist(self.client_items, id="client-list")
            else:
                yield Static(Text("  ○  No MCP client found. Install one, then run pcbridge connect NAME.",
                                  style=C["muted"]))
            yield Static("Not found", classes="section")
            yield Static(Text("  " + ("   ".join("· " + n for n in self.clients_missing) or "· none"),
                              style=C["dim"]))
            yield Static("", id="clients-note")
            remote = Text("claude.ai and your phone connect later, through remote access: ", style=C["muted"])
            remote.append("pcbridge", style=C["accent-light"])
            remote.append(" → Remote.", style=C["muted"])
            yield Static(remote)
            yield SkyFill("clients")
            yield self._buttons(Button(key_label("Next", "⏎"), id="go-next-clients", compact=True,
                                       classes="primary"),
                                Button(key_label("Back", "esc"), id="go-back-clients", compact=True))

    def _compose_review(self) -> ComposeResult:
        with Vertical(id="review"):
            yield Static("Ready to install", classes="title")
            yield Static(Text("This is everything setup will do. Nothing has changed yet.", style=C["muted"]))
            yield Static("", id="review-list")
            with Horizontal(id="cmd-row"):
                yield Static("", id="cmd", classes="cmdbox")
                yield Button(key_label("Copy", "c"), id="go-copy", compact=True)
            yield Static("", id="review-notes")
            yield SkyFill("review")
            yield self._buttons(Button(key_label("Install", "⏎"), id="go-install", compact=True, classes="primary"),
                                Button(key_label("Back", "esc"), id="go-back-review", compact=True))

    def _compose_install(self) -> ComposeResult:
        with Vertical(id="install"):
            with Horizontal(id="install-head"):
                yield Static("Installing", id="install-title")
                yield Static("", id="elapsed")
            hint = Text("Every step shows here as it runs. Press ", style=C["muted"])
            hint.append("l", style=C["accent-light"])
            hint.append(" for the full output.", style=C["muted"])
            yield Static(hint)
            yield TaskList(id="tasks")
            yield Static("", id="logbox")
            yield Static("", id="install-end")
            yield SkyFill("install")
            yield self._buttons(Button(key_label("Show the output", "l"), id="go-log", compact=True),
                                Button(key_label("Continue", "⏎"), id="go-continue", compact=True,
                                       classes="primary"))

    def _compose_path(self) -> ComposeResult:
        with Vertical(id="path"):
            title = Text("One more thing: make ", style="bold")
            title.append("pcbridge", style=f"bold {C['accent-light']}")
            title.append(" a command you can type", style="bold")
            yield Static(title)
            yield Static("", id="path-why")
            yield Static("", id="path-line", classes="cmdbox")
            yield Static(Text("The file is backed up first and listed in ROLLBACK.md, so it can be undone.",
                              style=C["dim"]))
            yield Static("", id="path-result")
            yield SkyFill("path")
            yield self._buttons(Button(key_label("Add the line", "y"), id="go-path-yes", compact=True,
                                       classes="primary"),
                                Button(key_label("Leave my files alone", "n"), id="go-path-no", compact=True),
                                Button(key_label("Continue", "⏎"), id="path-continue", compact=True,
                                       classes="primary"))

    def _compose_done(self) -> ComposeResult:
        with Vertical(id="done"):
            yield Static("", id="done-title")
            yield Static(Text("It runs in the background and starts at login. This window can close.",
                              style=C["muted"]), id="done-sub")
            with Horizontal(id="done-cols"):
                with Vertical(id="done-left"):
                    yield Static("Connected", classes="section")
                    yield Static("", id="connected")
                    yield Static(Text("A client that was already open sees pcbridge after you restart it.",
                                      style=C["dim"]), id="restart-note")
                with Vertical(id="done-right"):
                    yield Static("Next", classes="section")
                    yield Static("", id="next-steps")
                    yield Static("", id="attention")
            yield SkyFill("done")
            yield self._buttons(Button(key_label("Open pcbridge", "⏎"), id="go-open", compact=True,
                                       classes="primary"),
                                Button(key_label("Exit", "q"), id="go-exit", compact=True))

    def on_mount(self) -> None:
        self.started_at = time.monotonic()
        self._apply_size()
        self.goto("welcome")
        self.set_interval(1 / FPS, self._tick)

    def on_resize(self, event: events.Resize) -> None:
        self._apply_size()

    def _apply_size(self) -> None:
        self.screen.set_class(self.size.width < NARROW, "narrow")
        self.screen.set_class(self.size.height < 30, "short")
        try:
            self.query_one("#needed", Static).update(self._needed_text())
            if self.step == "review":
                self._fill_review()
        except NoMatches:
            pass

    # -- steps ---------------------------------------------------------------------------

    def goto(self, key: str) -> None:
        if key not in self.keys:
            return
        self.step = key
        self.entered = self.frame
        self.query_one("#steps", ContentSwitcher).current = key
        idx = self.keys.index(key)
        self.query_one("#where", Static).update(
            Text(f"{TITLES[key]} · step {idx + 1} of {len(self.keys)}", style=C["muted"]))
        if key == "welcome":
            self.query_one("#probes", Static).update(self._probes_text(0))
        elif key == "parts":
            self._refresh_parts_note()
        elif key == "clients":
            self._refresh_clients_note()
        elif key == "review":
            self._fill_review()
        elif key == "path":
            self._fill_path()
        elif key == "done":
            self.done_since = self.frame
            self._fill_done()
        focus: Widget | None = None
        if key == "parts" and self.optional:
            focus = self.query_one("#optional", Checklist)
        elif key == "clients" and self.client_items:
            focus = self.query_one("#client-list", Checklist)
        self.set_focus(focus)
        self._update_keys()
        self._sync()

    def _next_key(self) -> str:
        return self.keys[min(len(self.keys) - 1, self.keys.index(self.step) + 1)]

    def _update_keys(self) -> None:
        key = self.step
        entries = {
            "welcome": [("⏎", "start"), ("q", "quit")],
            "parts": [("↑↓", "move"), ("space", "tick"), ("⏎", "next"), ("esc", "back")],
            "clients": [("↑↓", "move"), ("space", "tick"), ("⏎", "next"), ("esc", "back")],
            "review": [("⏎", "install")] + ([("c", "copy the command")] if self.package_command()
                                             and (self.install_now or self.ctx.args.no_packages) else [])
            + [("esc", "back")],
            "install": [("l", "hide the output" if self._log_shown() else "show the output")]
            + ([("⏎", "continue")] if self.finished else []),
            "path": [("y", "add the line"), ("n", "leave it")] if self.path_choice is None
            else [("⏎", "continue")],
            "done": [("⏎", "open pcbridge"), ("q", "exit")],
        }[key]
        if not self.optional and key == "parts":
            entries = [("⏎", "next"), ("esc", "back")]
        self.query_one(KeyHints).set_entries(entries)

    def install_fraction(self) -> float:
        tasks = self.query_one(TaskList).tasks
        if not tasks:
            return 0.0
        done = sum(1 for t in tasks if t.state in ("ok", "warn", "fail"))
        running = sum(1 for t in tasks if t.state in ("run", "sudo"))
        return min(1.0, (done + 0.5 * running) / len(tasks))

    def _sync(self) -> None:
        self._sky = self.sky_level()
        idx = self.keys.index(self.step)
        fill = self.install_fraction() if self.step == "install" else 0.5
        self.query_one(Bridge).set_state(self.keys, idx, fill, self.done_since if self.step == "done" else None)

    def _tick(self) -> None:
        try:
            self._frame()
        except NoMatches:  # the screen is going away
            pass

    def _frame(self) -> None:
        self.frame += 1
        self._sync()
        for sky in self.query(SkyFill):
            sky.refresh()
        local = self.frame - self.entered
        if self.step == "welcome":
            logo = self.query_one(RevealLogo)
            logo.reveal = local * 3 if self.motion else 10 ** 6
            logo.refresh()
            self.query_one("#probes", Static).update(self._probes_text(local))
        elif self.step == "install":
            self.query_one(TaskList).refresh()
            end = self.finished_at if self.finished else time.monotonic()
            secs = int(end - self.started_at)
            self.query_one("#elapsed", Static).update(f"{secs // 60}:{secs % 60:02d}")
        elif self.step == "done":
            self._fill_done()
        elif self.step in ("parts", "clients"):
            for cl in self.query(Checklist):
                cl.refresh()

    # -- welcome ----------------------------------------------------------------------

    def probes(self) -> list[tuple[str, str, bool]]:
        found = self.ctx.found
        names = [i.title.strip() for i in self.client_items]
        if found.desktop_supported:
            desktop = "supported: keyboard, pointer and screen reading" + (" (X11)" if found.x11 else "")
        else:
            desktop = found.unsupported_reason()
        return [
            ("System", found.found_line().removeprefix("Found: "), True),
            ("Python", f"{pyplatform.python_version()} · {short_home(sys.prefix)}", True),
            ("Desktop", desktop, found.desktop_supported),
            ("Clients", f"{len(names)} found" + (": " + ", ".join(names) if names else ""), True),
            ("Setup", "an update: your settings are kept" if self.ctx.xdg.exists() else "a first install",
             True),
        ]

    def _probes_text(self, local: int) -> Text:
        out = Text()
        spin = SPINNER[self.frame % len(SPINNER)]
        for i, (key, value, good) in enumerate(self.probes()):
            start = 4 + i * 5
            if self.motion and local < start:
                break
            done = not self.motion or local >= start + 4
            if done:
                out.append("✓ " if good else "▲ ", style=C["ok"] if good else C["warning"])
                out.append(key.ljust(9), style=C["muted"])
                out.append(value, style=C["text"])
            else:
                out.append(spin + " ", style=C["working"])
                out.append(key.ljust(9), style=C["muted"])
                out.append("checking…", style=C["muted"])
            out.append("\n")
        out.rstrip()
        return out

    # -- parts and clients -------------------------------------------------------------

    def _needed_text(self) -> Text:
        plan, found, args = self.ctx.plan, self.ctx.found, self.ctx.args
        rows: list[tuple[str, str, str]] = []
        if plan.packages:
            rows.append(("System packages", ", ".join(plan.packages), "needs sudo"))
        else:
            rows.append(("System packages", "all present", ""))
        if plan.extras:
            rows.append(("Pillow", "crops and scales screenshots", "no sudo"))
        if plan.native_fetch:
            rows.append(("Native helper", f"from the {__version__} release, sha256-checked", "no sudo"))
        if found.session_type and not args.no_extension:
            title = setupflow.desktop_title(found.desktop_kind)
            what = "panel icon and window frames" if title.startswith("GNOME") else "desktop integration"
            rows.append((title, what, "no sudo"))
        rows.append(("Service", "pcbridge.socket and pcbridge.service, for you only", "no sudo"))
        wide = self.size.width >= NARROW
        out = Text()
        for name, what, how in rows:
            line = Text("  ◆  ", style=C["dim"])
            line.append(name.ljust(24))
            line.append(what, style=C["muted"])
            if how and wide:
                line.append(f"  · {how}", style=C["dim"])
            line.truncate(max(20, self.size.width - 8), overflow="ellipsis")
            out.append_text(line)
            out.append("\n")
        out.rstrip()
        return out

    def _refresh_parts_note(self) -> None:
        self.apply_choices()
        n = len(self.ctx.plan.chosen_packages())
        if self.ctx.args.no_packages and n:
            text = f"--no-packages: the {n} system packages are left for you; setup prints the command."
        elif n:
            text = (f"{n} system package{'s need' if n != 1 else ' needs'} sudo. You see the exact command "
                    "before anything runs.")
        else:
            text = "No system package is missing: nothing here needs sudo."
        self.query_one("#parts-note", Static).update(Text(text, style=C["muted"]))

    def _refresh_clients_note(self) -> None:
        chosen = [i.title.strip() for i in self.client_items if i.on]
        if chosen:
            text = Text(f"{len(chosen)} ticked: {', '.join(chosen)}.", style=C["muted"])
        else:
            text = Text("▲ Nothing ticked: pcbridge runs, but no agent sees it until you connect one "
                        "(pcbridge connect NAME).", style=C["warning"])
        self.query_one("#clients-note", Static).update(text)

    def on_checklist_changed(self, message: Checklist.Changed) -> None:
        if self.step == "parts":
            self._refresh_parts_note()
        elif self.step == "clients":
            self._refresh_clients_note()

    # -- review -------------------------------------------------------------------------

    def review_items(self) -> list[tuple[str, str]]:
        self.apply_choices()
        plan, args, found = self.ctx.plan, self.ctx.args, self.ctx.found
        items: list[tuple[str, str]] = []
        n = len(plan.chosen_packages())
        if n and args.no_packages:
            items.append((f"Leave {n} system package{'s' if n != 1 else ''} for you", "--no-packages"))
        elif n:
            items.append((f"Install {n} system package{'s' if n != 1 else ''}", "asks for your password"))
        else:
            items.append(("No system package to install", "nothing needs sudo"))
        extra = [name for name, on in (("Pillow", plan.extras), ("the native helper", plan.native_fetch)) if on]
        if extra:
            items.append(("Add " + " and ".join(extra),
                          "no sudo" + (" · checked against sha256sums.txt" if plan.native_fetch else "")))
        items.append(("Keep the configuration" if self.ctx.xdg.exists() else "Write the configuration",
                      short_home(self.ctx.xdg)))
        items.append(("Start pcbridge at login", "pcbridge.socket and pcbridge.service, for you only"))
        if found.session_type and not args.no_extension:
            items.append((f"Set up the {setupflow.desktop_title(found.desktop_kind)}", "no sudo"))
        if not args.no_connect:
            chosen = [i.title.strip() for i in self.client_items if i.on]
            items.append(("Connect " + ", ".join(chosen) if chosen else "Connect no client",
                          "each file is backed up first" if chosen else "pcbridge connect NAME later"))
        if not args.no_aliases:
            items.append(("Refresh the pcbridge aliases", "in ~/.bashrc, only if they are there"))
        items.append(("Check that a fresh client gets the tools", ""))
        return items

    def _fill_review(self) -> None:
        wide = self.size.width >= NARROW
        out = Text()
        for i, (text, meta) in enumerate(self.review_items(), 1):
            out.append(f"{i}".rjust(3) + "  ", style=C["accent-light"])
            if wide and meta and len(text) < 46:
                out.append(text.ljust(46))
                out.append(meta, style=C["muted"])
            elif wide and meta:
                out.append(text + "\n" + " " * 5)
                out.append(meta, style=C["muted"])
            else:
                out.append(text)
            out.append("\n")
        out.rstrip()
        self.query_one("#review-list", Static).update(out)
        cmd = self.package_command() if self.install_now or self.ctx.args.no_packages else ""
        row = self.query_one("#cmd-row")
        row.display = bool(cmd)
        if cmd:
            box = Text("$ ", style=C["dim"])
            box.append(cmd)
            self.query_one("#cmd", Static).update(box)
        notes = Text()
        if self.ctx.found.desktop_supported:
            notes.append("◇  ", style=C["accent"])
            if not self.ctx.xdg.exists():
                say = "Desktop control will be on; agents open a grant each time. Type pcbridge to turn it off.\n"
            elif self.desktop_enabled():
                say = "Desktop control stays on; agents open a grant each time.\n"
            else:
                say = "Desktop control stays off, as your config has it.\n"
            notes.append(say, style=C["muted"])
        notes.append("◇  ", style=C["accent"])
        notes.append("Every file setup changes is backed up first; ROLLBACK.md in the backup says how "
                     "to undo it." if wide else "Changed files are backed up first (ROLLBACK.md).",
                     style=C["muted"])
        self.query_one("#review-notes", Static).update(notes)

    # -- install ------------------------------------------------------------------------

    def start_install(self) -> None:
        if self.installing or self.finished:
            return
        self.apply_choices()
        self.installing = True
        self.started_at = time.monotonic()
        tasks = self.query_one(TaskList)
        tasks.tasks = [Task(k, t) for k, t in setupflow.steps(self.ctx)]
        self.goto("install")
        tasks.refresh(layout=True)
        self._install_worker()

    @work(thread=True, exclusive=True, group="install")
    def _install_worker(self) -> None:
        inst.set_sink(lambda kind, msg: self.call_from_thread(self.on_setup_line, kind, msg))
        inst.sudo_prompt = self._sudo_from_thread
        outcome, error = None, ""
        try:
            outcome = self.executor(self.ctx, self.install_now, self.chosen_clients()
                                    if not self.ctx.args.no_connect else None)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - shown on the screen, not lost
            error = str(exc) or exc.__class__.__name__
        finally:
            inst.set_sink(None)
            inst.sudo_prompt = None
        self.call_from_thread(self.install_finished, outcome, error)

    def _current_task(self) -> Task | None:
        for t in self.query_one(TaskList).tasks:
            if t.state in ("run", "sudo"):
                return t
        return None

    def _finish_task(self, task: Task) -> None:
        kinds = [k for k, _ in task.messages]
        task.state = "fail" if "fail" in kinds else "warn" if "warn" in kinds else "ok"
        if task.key == "clients" and task.state == "ok" and task.messages:
            task.detail = f"{len(task.messages)} connected"
        else:
            picks = [m for k, m in task.messages if k == task.state]
            task.detail = picks[-1] if picks else (task.messages[-1][1] if task.messages else "done")
        task.detail = shorten_homes(task.detail)
        task.ended = time.monotonic()

    def on_setup_line(self, kind: str, msg: str) -> None:
        tasks = self.query_one(TaskList)
        current = self._current_task()
        if kind == "step":
            key, _, title = msg.partition("\t")
            if current is not None:
                self._finish_task(current)
            task = next((t for t in tasks.tasks if t.key == key), None)
            if task is None:
                task = Task(key, title)
                tasks.tasks.append(task)
                tasks.refresh(layout=True)
            task.state, task.started = "run", time.monotonic()
            self.log_lines.append(f"── {title}")
        elif kind in ("ok", "warn", "fail"):
            mark = {"ok": "✓", "warn": "▲", "fail": "✕"}[kind]
            self.log_lines.append(f"{mark} {msg}")
            if current is not None:
                current.messages.append((kind, msg))
                current.live = shorten_homes(msg)
        else:
            text = msg.strip()
            if text:
                self.log_lines.append(("    " if kind == "out" else "") + text)
                if current is not None:
                    current.live = shorten_homes(text)
        tasks.refresh()
        self._refresh_log()

    def _sudo_from_thread(self, argv: list[str]) -> bool:
        return bool(self.call_from_thread(self.ask_sudo, argv))

    def ask_sudo(self, argv: list[str]) -> bool:
        task = self._current_task()
        if task is not None:
            task.state = "sudo"
            self.query_one(TaskList).refresh()
        try:
            if self.sudo_runner is not None:
                return self.sudo_runner(argv)
            out = sys.__stdout__
            with self.suspend():
                out.write("\npcbridge setup: sudo asks for your password to run\n\n    "
                          + shlex.join(argv) + "\n\npcbridge never sees the password; the setup "
                          "screen comes back right after.\n\n")
                out.flush()
                return subprocess.run(["sudo", "-v"], check=False).returncode == 0
        finally:
            if task is not None:
                task.state = "run"

    def install_finished(self, outcome: setupflow.Outcome | None, error: str) -> None:
        current = self._current_task()
        if current is not None:
            if error:
                current.messages.append(("fail", error))
            self._finish_task(current)
        self.outcome, self.error = outcome, error
        self.installing, self.finished = False, True
        self.finished_at = time.monotonic()
        tasks = self.query_one(TaskList).tasks
        trouble = [t for t in tasks if t.state in ("warn", "fail")]
        end = Text()
        if error:
            end.append("✕  Setup stopped: " + error, style=C["error"])
            self.query_one("#install-title", Static).update("Setup stopped")
        elif outcome is not None and not outcome.ready:
            end.append("▲  Installed, but the readiness check failed; the last screen says what to do.",
                       style=C["warning"])
            self.query_one("#install-title", Static).update("Installed, with a problem")
        elif trouble:
            end.append(f"▲  Installed; {len(trouble)} step{'s' if len(trouble) != 1 else ''} need"
                       f"{'' if len(trouble) != 1 else 's'} attention (listed on the last screen).",
                       style=C["warning"])
            self.query_one("#install-title", Static).update("Installed")
        else:
            end.append("✓  Everything is installed.", style=C["ok"])
            self.query_one("#install-title", Static).update("Installed")
        self.query_one("#install-end", Static).update(end)
        self.query_one("#go-continue").add_class("shown")
        self.query_one("#go-continue").focus()
        self._update_keys()
        self._sync()

    def _log_shown(self) -> bool:
        try:
            return self.query_one("#logbox").has_class("shown")
        except Exception:  # noqa: BLE001 - before the screen is built
            return False

    def _refresh_log(self) -> None:
        if self._log_shown():
            lines = list(self.log_lines)[-9:]
            self.query_one("#logbox", Static).update(Text("\n".join(lines) or "nothing yet"))

    def after_install(self) -> None:
        if self.outcome is not None and self.outcome.path_fix is not None and "path" in self.keys:
            self.goto("path")
        else:
            self.goto("done")

    # -- path ---------------------------------------------------------------------------

    def _fill_path(self) -> None:
        fix = self.outcome.path_fix if self.outcome is not None else None
        if fix is None:
            return
        name = self.ctx.found.distro_name or "this system"
        why = Text(f"On {name} ", style=C["muted"])
        why.append(short_home(inst.LOCAL_BIN), style=C["text"])
        why.append(" is not on PATH, so typing ", style=C["muted"])
        why.append("pcbridge", style=C["text"])
        why.append(" in a new terminal finds nothing.\nSetup can add one line to ", style=C["muted"])
        why.append(short_home(fix.file), style=C["text"])
        why.append(f", the file of your login shell ({fix.shell}):", style=C["muted"])
        self.query_one("#path-why", Static).update(why)
        line = Text(fix.line)
        line.append(f"   {inst.PATH_MARK}", style=C["dim"])
        self.query_one("#path-line", Static).update(line)

    def action_path_answer(self, answer: str) -> None:
        if self.step != "path" or self.path_choice is not None or self.outcome is None:
            return
        fix = self.outcome.path_fix
        self.path_choice = answer
        result = Text()
        if answer == "yes" and fix is not None:
            try:
                where = setupflow.add_path_line(self.ctx, self.outcome)
            except OSError as exc:
                result.append(f"✕  Not changed: {exc}", style=C["error"])
            else:
                self.path_result = where
                result.append("✓  ", style=C["ok"])
                result.append(where[0].upper() + where[1:] + ".\n")
                again = "open a new one" if fix.shell == "fish" else f"open a new one, or run source {short_home(fix.file)}"
                result.append(f"This terminal still has the old PATH: {again}.", style=C["muted"])
        else:
            result.append("○  ", style=C["dim"])
            result.append("Nothing was changed.\n")
            target = short_home(fix.file) if fix is not None else "your shell's file"
            result.append(f"To do it yourself later, add the line above to {target}. Until then, run "
                          f"{short_home(inst.LOCAL_BIN / 'pcbridge')}.", style=C["muted"])
        self.query_one("#path-result", Static).update(result)
        self.query_one("#go-path-yes").display = False
        self.query_one("#go-path-no").display = False
        cont = self.query_one("#path-continue")
        cont.add_class("shown")
        cont.focus()
        self._update_keys()

    # -- done ---------------------------------------------------------------------------

    def _fill_done(self) -> None:
        bridge = self.query_one(Bridge)
        since = bridge.arrived_since()
        local = self.frame - since if since is not None and self.frame >= since else -1
        outcome = self.outcome
        title = Text()
        if self.error:
            title.append("✕  Setup stopped before the end", style=f"bold {C['error']}")
        elif outcome is not None and not outcome.ready:
            title.append("▲  pcbridge is installed, but the readiness check failed",
                         style=f"bold {C['warning']}")
        else:
            sparkle = [C["accent-light"], C["warning"], C["kind-desktop"], C["accent"]]
            shift = (self.frame // 3) if self.motion else 0
            title.append("✦ ", style=sparkle[shift % 4])
            title.append("pcbridge is ready", style=f"bold {C['ok']}")
            title.append(" ✧", style=sparkle[(shift + 1) % 4])
            title.append(" ·", style=sparkle[(shift + 2) % 4])
            title.append(" ✦", style=sparkle[(shift + 3) % 4])
        self.query_one("#done-title", Static).update(title)
        connected = Text()
        rows = outcome.clients if outcome is not None else []
        spin = SPINNER[self.frame % len(SPINNER)]
        for i, (client, status, _detail) in enumerate(rows):
            name = connectlib.NAMES.get(client, client)
            shown = local >= 3 + i * 5 or not self.motion
            if not shown:
                connected.append(f"{spin}  ", style=C["working"])
                connected.append(name.ljust(22))
                connected.append("connecting…", style=C["muted"])
            elif status == "ok":
                connected.append("✓  ", style=C["ok"])
                connected.append(name.ljust(22))
                connected.append("connected", style=C["ok"])
            elif status == "skip":
                connected.append("○  ", style=C["dim"])
                connected.append(name.ljust(22), style=C["muted"])
                connected.append("not installed", style=C["dim"])
            else:
                connected.append("▲  ", style=C["warning"])
                connected.append(name.ljust(22))
                connected.append("not changed", style=C["warning"])
            connected.append("\n")
        if not rows:
            connected.append("○  ", style=C["dim"])
            connected.append("none yet: ", style=C["muted"])
            connected.append("pcbridge connect NAME", style=C["accent-light"])
        connected.rstrip()
        self.query_one("#connected", Static).update(connected)
        self.query_one("#restart-note").display = bool(rows)
        nxt = Text()

        def step(pre: str, code: str, post: str) -> None:
            nxt.append("›  ", style=C["accent"])
            nxt.append(pre)
            nxt.append(f" {code} ", style=f"{C['accent-light']} on {PALETTE['input']}")
            nxt.append(post + "\n")

        step("Type", "pcbridge", "to check and change anything.")
        if self.ctx.found.desktop_supported:
            if self.desktop_enabled():
                step("Desktop control is on. To turn it off, type", "pcbridge", "→ Desktop.")
            else:
                step("Desktop control is off. To turn it on, type", "pcbridge", "→ Desktop.")
        step("Remote access for claude.ai and your phone:", "pcbridge", "→ Remote.")
        if outcome is not None and not outcome.ready:
            step("See what is missing:", "pcbridge doctor", "")
        if self.path_choice == "yes":
            step("Open a new terminal first, so the PATH change applies.", "", "")
        nxt.rstrip()
        self.query_one("#next-steps", Static).update(nxt)
        att = Text()
        notes = list(outcome.notes) if outcome is not None else []
        if self.error:
            notes.insert(0, "Run pcbridge setup again; pcbridge doctor shows what is missing.")
        for n in notes:
            att.append("\n▲  ", style=C["warning"])
            att.append(n, style=C["text"])
        if outcome is not None and outcome.rollback is not None:
            att.append("\n◇  Undo: ", style=C["dim"])
            att.append(short_home(outcome.rollback), style=C["dim"])
        self.query_one("#attention", Static).update(att)

    def desktop_enabled(self) -> bool:
        """`[desktop] enabled` after setup (a new config: on; an update keeps the user's value)."""
        return setupflow.desktop_enabled(self.ctx.xdg)

    # -- actions --------------------------------------------------------------------------

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "go-start" or bid.startswith("go-next"):
            self.goto(self._next_key())
        elif bid.startswith("go-back"):
            self.action_back()
        elif bid in ("go-quit", "go-exit"):
            await self.action_quit()
        elif bid == "go-install":
            self.start_install()
        elif bid == "go-copy":
            self.action_copy()
        elif bid == "go-log":
            self.action_log()
        elif bid == "go-continue":
            self.after_install()
        elif bid == "go-path-yes":
            self.action_path_answer("yes")
        elif bid == "go-path-no":
            self.action_path_answer("no")
        elif bid == "path-continue":
            self.goto("done")
        elif bid == "go-open":
            self.exit(OPEN)

    def on_bridge_picked(self, message: Bridge.Picked) -> None:
        idx = self.keys.index(self.step)
        if self.step in BEFORE_INSTALL and message.index < idx:
            self.goto(self.keys[message.index])

    def action_primary(self) -> None:
        key = self.step
        if key in ("welcome", "parts", "clients"):
            self.goto(self._next_key())
        elif key == "review":
            self.start_install()
        elif key == "install" and self.finished:
            self.after_install()
        elif key == "path" and self.path_choice is not None:
            self.goto("done")
        elif key == "done":
            self.exit(OPEN)

    def action_back(self) -> None:
        if self.step in ("parts", "clients", "review"):
            self.goto(self.keys[self.keys.index(self.step) - 1])

    async def action_quit(self) -> None:
        if self.installing:
            self.notify("Setup is still running; it stops by itself when the steps are done.",
                        severity="warning")
            return
        self.exit(STOPPED if self.step in BEFORE_INSTALL else DONE)

    def action_log(self) -> None:
        if self.step != "install":
            return
        box = self.query_one("#logbox")
        box.toggle_class("shown")
        self.query_one("#go-log", Button).label = key_label(
            "Hide the output" if self._log_shown() else "Show the output", "l")
        self._refresh_log()
        self._update_keys()

    def action_copy(self) -> None:
        if self.step != "review":
            return
        cmd = self.package_command()
        if cmd:
            self.copy_to_clipboard(cmd)
            self.query_one("#go-copy", Button).label = key_label("✓ Copied", "c")

    # -- after the screen -----------------------------------------------------------------

    def report(self, result: str | None) -> int:
        """What the terminal keeps once the screen is gone; the exit status."""
        if result in (None, STOPPED) and self.outcome is None and not self.error:
            print("Stopped; nothing was changed.")
            return 1
        outcome = self.outcome
        if self.error or outcome is None:
            print(f"pcbridge setup stopped: {self.error or 'unknown error'}")
            print("Run `pcbridge setup` again; `pcbridge doctor` shows what is missing.")
            return 1
        names = [connectlib.NAMES.get(c, c) for c, st, _ in outcome.clients if st == "ok"]
        state = "ready" if outcome.ready else "installed, but the readiness check failed"
        print(f"pcbridge {__version__} setup: {state}"
              + (f" ({outcome.tools} tools)" if outcome.ready else f" ({outcome.tools})"))
        if names:
            print("Connected: " + ", ".join(names))
        if self.path_result:
            print(f"PATH: {self.path_result}; open a new terminal for it to apply")
        if outcome.rollback:
            print(f"Backups and undo instructions: {outcome.rollback}")
        if outcome.notes:
            print("Still to do by hand:")
            for n in outcome.notes:
                print(f"  {n}")
        print("Type `pcbridge` to check and change things.")
        return 0 if outcome.ready else 1


def run_screen(args: Any) -> int:
    """`pcbridge setup` on a terminal: the screen, then a short summary."""
    from .logo import query_terminal_background

    inst.say(f"pcbridge {__version__} setup ({inst.install_kind()} install): detecting this computer…")
    ctx = setupflow.prepare(args)
    background = query_terminal_background()
    app = SetupApp(ctx, terminal_background=background)
    result = app.run()
    code = app.report(result)
    if result == OPEN and app.outcome is not None:
        from . import run as run_ui

        run_ui([])
    return code


__all__ = ["Bridge", "Checklist", "Item", "SetupApp", "SkyFill", "Task", "TaskList", "run_screen"]
