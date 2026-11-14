"""The live pcbridge logo at the top of the Overview.

Drawn with half blocks: one cell holds two pixels stacked, the upper one in
the cell's foreground color (`▀`) and the lower one in its background, so a
pixel is close to square. Wide terminals get the wordmark, narrow ones the
terminal icon. The state (idle, active, error) tints the gray and adds a
glow; a click plays one short reaction and shows the reason for the state.

The glow is cell backgrounds blended over a base: the terminal's own
background when it answered OSC 11 at start, else a fixed dark base.
"""

from __future__ import annotations

import math
import os
import random
import select
import sys
import time
from typing import Any

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget

RGB = tuple[int, int, int]

# The letters, a bold pixel font with strokes two pixels wide. Rows 0-1 are
# the ascender, 2-6 the x-height, 7-8 the descender.
_LETTERS = {
    "p": ["......", "......", "#####.", "##..##", "##..##", "##..##", "#####.", "##....", "##...."],
    "c": ["......", "......", ".#####", "##....", "##....", "##....", ".#####", "......", "......"],
    "b": ["##....", "##....", "#####.", "##..##", "##..##", "##..##", "#####.", "......", "......"],
    "r": [".....", ".....", "##.##", "####.", "##...", "##...", "##...", ".....", "....."],
    "i": ["##", "..", "##", "##", "##", "##", "##", "..", ".."],
    "d": ["....##", "....##", ".#####", "##..##", "##..##", "##..##", ".#####", "......", "......"],
    "g": ["......", "......", ".#####", "##..##", "##..##", "##..##", ".#####", "....##", "#####."],
    "e": ["......", "......", ".####.", "##..##", "######", "##....", ".#####", "......", "......"],
}

ICON = [
    ".#############.",
    "###############",
    "#.............#",
    "#.##..........#",
    "#..##.........#",
    "#...##........#",
    "#..##.........#",
    "#.##...#####..#",
    "#.............#",
    ".#############.",
]


def _word() -> list[str]:
    rows = []
    for y in range(9):
        rows.append(".".join(_LETTERS[ch][y] for ch in "pcbridge"))
    return rows


WORD = _word()

# (margin left, right, top, bottom) in pixels, for the glow.
MARGINS = {"word": (3, 3, 3, 2), "icon": (3, 3, 3, 3)}

STATES: dict[str, dict[str, Any]] = {
    "idle": {"left": (214, 219, 225), "right": (118, 128, 140), "glow": None,
             "rings": (0.0, 0.0, 0.0)},
    "active": {"left": (221, 231, 245), "right": (124, 146, 178), "glow": (70, 138, 235),
               "rings": (0.46, 0.24, 0.09)},
    "error": {"left": (246, 224, 227), "right": (182, 128, 136), "glow": (232, 72, 90),
              "rings": (0.44, 0.22, 0.08)},
}
BOTTOM = (70, 78, 88)        # the slight darkening toward the bottom row
FIXED_BASE = (14, 18, 22)    # #0e1216, the glow's base when the terminal did not answer
WHITE = (255, 255, 255)

REACTIONS = {"pulse": 1.2, "sweep": 1.0, "flow": 1.6, "wave": 1.3, "sparkle": 1.4}
FPS = 30
WIDE = 96                    # columns at which the wordmark replaces the icon


def mix(a: RGB, b: RGB, t: float) -> RGB:
    t = max(0.0, min(1.0, t))
    return (round(a[0] + (b[0] - a[0]) * t), round(a[1] + (b[1] - a[1]) * t),
            round(a[2] + (b[2] - a[2]) * t))


def tri(u: float) -> float:
    """A triangle wave of period 2: tri(t) = t on [0, 1], back down on [1, 2]."""
    u = u % 2.0
    return u if u <= 1.0 else 2.0 - u


def query_terminal_background(timeout: float = 0.15) -> RGB | None:
    """Ask the terminal for its background color (OSC 11); None when it does not answer.

    Runs before Textual takes the terminal, on the controlling tty only.
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    try:
        import termios
        import tty
    except ImportError:  # pragma: no cover - not POSIX
        return None
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return None
    old = None
    try:
        old = termios.tcgetattr(fd)
        tty.setraw(fd)
        os.write(fd, b"\x1b]11;?\x07")
        data = b""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            ready, _, _ = select.select([fd], [], [], max(0.0, end - time.monotonic()))
            if not ready:
                break
            data += os.read(fd, 64)
            if data.endswith(b"\x07") or data.endswith(b"\x1b\\"):
                break
        return parse_osc11(data.decode("ascii", "replace"))
    except (OSError, termios.error):
        return None
    finally:
        if old is not None:
            try:
                termios.tcsetattr(fd, termios.TCSANOW, old)
            except termios.error:
                pass
        os.close(fd)


def parse_osc11(reply: str) -> RGB | None:
    """`ESC ] 11 ; rgb:RRRR/GGGG/BBBB BEL` -> (r, g, b)."""
    marker = "rgb:"
    i = reply.find(marker)
    if i < 0:
        return None
    body = reply[i + len(marker):].rstrip("\x07").split("\x1b")[0]
    parts = body.split("/")
    if len(parts) != 3:
        return None
    try:
        out = []
        for p in parts:
            value = int(p, 16)
            out.append(round(value * 255 / (16 ** len(p) - 1)))
        return (out[0], out[1], out[2])
    except ValueError:
        return None


class Logo(Widget):
    """The wordmark or the icon, with a state glow and the click reactions.

    `glyph` is "word" or "icon"; `state` is "idle", "active" or "error". A
    timer runs only while a reaction plays; an idle logo uses none.
    """

    DEFAULT_CSS = """
    Logo {
        width: auto;
        height: auto;
    }
    """

    class Clicked(Message):
        """The logo was clicked while no reaction played: toggle the reason."""

    def __init__(self, glyph: str = "word", state: str = "idle", base: RGB | None = None,
                 seed: int | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.glyph = glyph
        self.state = state
        self.base = base or FIXED_BASE
        self.hover = False
        self.reaction: str | None = None
        self.last_reaction: str | None = None
        self.progress = 0.0
        self._started = 0.0
        self._timer: Any = None
        self._rng = random.Random(seed)
        self._sparkles: set[tuple[int, int]] = set()
        self._sparkle_at = -1.0
        self._layout()

    # -- geometry ----------------------------------------------------------------

    def _layout(self) -> None:
        bitmap = WORD if self.glyph == "word" else ICON
        left, right, top, bottom = MARGINS[self.glyph]
        self.ml, self.mt = left, top
        self.bw, self.bh = len(bitmap[0]), len(bitmap)
        self.pw = self.bw + left + right
        self.ph = self.bh + top + bottom
        self.on: set[tuple[int, int]] = {(x + left, y + top) for y, row in enumerate(bitmap)
                                         for x, ch in enumerate(row) if ch == "#"}
        # Chebyshev distance of every empty pixel within 3 of a letter pixel.
        self.ring: dict[tuple[int, int], int] = {}
        for (x, y) in self.on:
            for dy in range(-3, 4):
                for dx in range(-3, 4):
                    p = (x + dx, y + dy)
                    if p in self.on or not (0 <= p[0] < self.pw and 0 <= p[1] < self.ph):
                        continue
                    d = max(abs(dx), abs(dy))
                    if d and (p not in self.ring or d < self.ring[p]):
                        self.ring[p] = d

    @property
    def cells(self) -> tuple[int, int]:
        """(columns, rows) the logo takes."""
        return self.pw, (self.ph + 1) // 2

    def get_content_width(self, container, viewport) -> int:
        return self.cells[0]

    def get_content_height(self, container, viewport, width) -> int:
        return self.cells[1]

    def set_glyph(self, glyph: str) -> None:
        if glyph != self.glyph:
            self.glyph = glyph
            self._layout()
            self.refresh(layout=True)

    def set_state(self, state: str) -> None:
        if state in STATES and state != self.state:
            self.state = state
            self.refresh()

    # -- pixel colors ------------------------------------------------------------

    def pixels(self) -> dict[tuple[int, int], RGB]:
        """The color of every lit pixel at the current frame; unlit ones are absent."""
        st = STATES[self.state]
        p = self.progress if self.reaction else 0.0
        env = math.sin(math.pi * p) if self.reaction else 0.0
        reaction = self.reaction
        out: dict[tuple[int, int], RGB] = {}
        span = max(1, self.bw - 1)
        shift: dict[int, int] = {}
        if reaction == "wave":
            for x in range(self.pw):
                shift[x] = round(math.sin(x * 0.45 - p * 3 * math.pi) * 1.4 * env)
        for (x, y) in self.on:
            t = (x - self.ml) / span
            if reaction == "flow":
                t = tri(t + 2 * p)
            color = mix(st["left"], st["right"], t)
            color = mix(color, BOTTOM, 0.16 * (y - self.mt) / max(1, self.bh - 1))
            if reaction == "pulse":
                color = mix(color, WHITE, 0.15 * env)
            elif reaction == "sweep":
                center = -6 + p * (self.bw + 12)
                dist = abs((x - self.ml) - center)
                if dist < 4:
                    color = mix(color, WHITE, 0.75 * (1 - dist / 4))
            if self.hover:
                color = mix(color, WHITE, 0.08)
            out[(x, y + shift.get(x, 0))] = color
        rings = list(st["rings"])
        glow = st["glow"]
        if reaction == "pulse":
            rings = [a * (1 + 1.3 * env) + b * env for a, b in zip(rings, (0.30, 0.16, 0.08))]
            glow = glow or (150, 160, 172)  # even idle glows briefly
        if glow and any(rings):
            for (x, y), d in self.ring.items():
                alpha = rings[d - 1]
                if self.hover:
                    alpha *= 1.15
                if alpha <= 0.01:
                    continue
                target = (x, y + shift.get(x, 0))
                if target not in out:
                    out[target] = mix(self.base, glow, alpha)
        if reaction == "sparkle":
            light = mix(glow or (190, 198, 208), WHITE, 0.55)
            for pos in self._sparkles:
                if pos not in out:
                    out[pos] = light
        return out

    def _pick_sparkles(self, env: float) -> None:
        empty = [pos for pos in self.ring]
        count = int(len(empty) * 0.12 * env)
        self._sparkles = set(self._rng.sample(empty, min(count, len(empty))))

    # -- drawing -----------------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        pix = self.pixels()
        top, low = 2 * y, 2 * y + 1
        segments = []
        for x in range(self.pw):
            a, b = pix.get((x, top)), pix.get((x, low))
            if a is None and b is None:
                segments.append(Segment(" "))
            elif a is not None and b is not None and a == b:
                segments.append(Segment(" ", Style(bgcolor=_hex(a))))
            elif a is not None and b is not None:
                segments.append(Segment("▀", Style(color=_hex(a), bgcolor=_hex(b))))
            elif a is not None:
                segments.append(Segment("▀", Style(color=_hex(a))))
            else:
                segments.append(Segment("▄", Style(color=_hex(b))))
        return Strip(segments, self.pw)

    def text_rows(self) -> list[str]:
        """The logo as characters only (for tests)."""
        return ["".join(seg.text for seg in self.render_line(y)) for y in range(self.cells[1])]

    # -- interaction ---------------------------------------------------------------

    def on_enter(self, event: events.Enter) -> None:
        self.hover = True
        self.refresh()

    def on_leave(self, event: events.Leave) -> None:
        self.hover = False
        self.refresh()

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.click()

    def click(self) -> bool:
        """Play one reaction and toggle the reason; ignored while a reaction runs."""
        if self.reaction is not None:
            return False
        choices = [r for r in REACTIONS if r != self.last_reaction]
        self.play(self._rng.choice(choices))
        self.post_message(self.Clicked())
        return True

    def play(self, reaction: str) -> None:
        self.reaction = reaction
        self.last_reaction = reaction
        self.progress = 0.0
        self._started = time.monotonic()
        self._sparkle_at = -1.0
        if reaction == "sparkle":
            self._pick_sparkles(0.0)
        self._timer = self.set_interval(1 / FPS, self._tick)
        self.refresh()

    def _tick(self) -> None:
        if self.reaction is None:
            return
        elapsed = time.monotonic() - self._started
        length = REACTIONS[self.reaction]
        self.progress = min(1.0, elapsed / length)
        if self.reaction == "sparkle" and elapsed - self._sparkle_at >= 0.09:
            self._sparkle_at = elapsed
            self._pick_sparkles(math.sin(math.pi * self.progress))
        if self.progress >= 1.0:
            self.finish()
        self.refresh()

    def finish(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self.reaction = None
        self.progress = 0.0
        self._sparkles = set()
        self.refresh()


def _hex(c: RGB) -> str:
    return f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}"
