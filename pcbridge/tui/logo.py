"""The live pcbridge logo at the top of the Overview.

Drawn with half blocks: one cell holds two pixels stacked, the upper one in
the cell's foreground color (`▀`) and the lower one in its background, so a
pixel is close to square. Wide terminals get the wordmark, narrow ones the
terminal icon. The state (idle, active, error) tints the gray and adds a
glow; a click plays one short reaction and shows the reason for the state.

The glow never switches: three levels (blue, red, breathing) ease toward
the state's targets, so it opens in about 0.8 s, inner ring first, and
fades in about 1.2 s, outer ring first. While a job runs it breathes on a
3.2 s cycle; a single tool call gives a lighter glow. Without motion
(`TEXTUAL_ANIMATIONS=none`) the levels jump, as before.

When a newer release is out the logo becomes the upgrade: its pixels flow
into a green arrow with a black edge (`show_arrow`), which fills from the
bottom while the upgrade installs (`set_fill`) and then shoots up and away
(`launch`). A click on the arrow posts `UpgradeClicked`.

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

# The upgrade arrow: K the black edge (so it shows on a green terminal too),
# L the lit left side, G the green. One for the wordmark, one for the icon.
ARROW = [
    "........K........",
    ".......KLK.......",
    "......KLGGK......",
    ".....KLGGGGK.....",
    "....KLGGGGGGK....",
    "...KLGGGGGGGGK...",
    "..KLGGGGGGGGGGK..",
    ".KLGGGGGGGGGGGGK.",
    "KKKKKKLGGGGKKKKKK",
    ".....KLGGGGK.....",
    ".....KLGGGGK.....",
    ".....KLGGGGK.....",
    ".....KKKKKKK.....",
]
ARROW_SMALL = [
    ".....K.....",
    "....KLK....",
    "...KLGGK...",
    "..KLGGGGK..",
    ".KLGGGGGGK.",
    "KKKKLGGKKKK",
    "...KLGGK...",
    "...KLGGK...",
    "...KLGGK...",
    "...KKKKK...",
]
GREEN_TOP = (126, 226, 160)
GREEN_BOTTOM = (52, 170, 98)
GREEN_LIGHT = (196, 247, 212)
GREEN_DIM = (31, 74, 48)           # not filled yet, while the upgrade installs
GREEN_DIM_LIGHT = (44, 94, 61)
GREEN_GLOW = (98, 208, 138)
EDGE = (0, 0, 0)
MORPH_S = 1.3                      # letters to arrow
LAUNCH_S = 0.8                     # the arrow shoots up and away
BOB_S = 0.7                        # the arrow moves half a cell up and down

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
# The glow's easing: the time constant (s) toward a higher and a lower
# level; about three of them reach the target. Red comes faster and goes
# slower, so an error is not missed.
TAU = {"blue": (0.26, 0.42), "red": (0.17, 0.7), "breathe": (0.3, 0.3)}
BLIP = 0.62                  # a tool call without a job: a lighter glow
BREATH_S = 3.2               # one breath while a job runs
BREATH_LOW = 0.58            # the glow at the bottom of a breath
GLOW_FPS = 15
RING_STEP = 0.18             # ring d lights once the level passes (d - 1) * this
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

    class UpgradeClicked(Message):
        """The upgrade arrow was clicked (or Enter/space while it had focus)."""

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
        # The upgrade: None, "morph", "arrow", "fill", "launch" or "gone".
        self.upgrade: str | None = None
        self.fill = 0.0
        self.bob = 0
        self._u_started = 0.0
        self._u_timer: Any = None
        self._bob_timer: Any = None
        # The glow's levels and where they are heading (set_state).
        self.levels = dict(self._targets(state, working=True), breathe=0.0)
        self.targets = dict(self.levels)
        self._glow_timer: Any = None
        self._glow_last = 0.0
        self._t0 = time.monotonic()
        self._pix: dict[tuple[int, int], RGB] | None = None
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
        self._arrow_layout()

    def _arrow_layout(self) -> None:
        """Where the arrow's pixels go on this glyph's canvas, and which
        letter pixel flows to which arrow pixel (left to right, top down)."""
        bitmap = ARROW if self.glyph == "word" else ARROW_SMALL
        aw, ah = len(bitmap[0]), len(bitmap)
        x0 = (self.pw - aw) // 2
        y0 = 0 if self.glyph == "word" else (self.ph - ah) // 2
        self.arrow_h = ah
        self.arrow: dict[tuple[int, int], tuple[str, int]] = {
            (x + x0, y + y0): (ch, y) for y, row in enumerate(bitmap) for x, ch in enumerate(row) if ch != "."}
        src = sorted(self.on)
        tgt = sorted(self.arrow)
        self.pairs = [(s, tgt[i * len(tgt) // len(src)]) for i, s in enumerate(src)]
        self.arrow_ring: dict[tuple[int, int], int] = {}
        for (x, y) in self.arrow:
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    p = (x + dx, y + dy)
                    if p in self.arrow or not (0 <= p[0] < self.pw and 0 <= p[1] < self.ph + 1):
                        continue
                    d = max(abs(dx), abs(dy))
                    if d and (p not in self.arrow_ring or d < self.arrow_ring[p]):
                        self.arrow_ring[p] = d

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

    @staticmethod
    def _targets(state: str, working: bool) -> dict[str, float]:
        if state == "error":
            return {"blue": 0.0, "red": 1.0, "breathe": 0.0}
        if state == "active":
            return {"blue": 1.0 if working else BLIP, "red": 0.0, "breathe": 1.0 if working else 0.0}
        return {"blue": 0.0, "red": 0.0, "breathe": 0.0}

    def set_state(self, state: str, working: bool = True) -> None:
        """Head for `state`'s glow; `working`: a job runs, so it breathes."""
        if state not in STATES:
            return
        targets = self._targets(state, working)
        if state == self.state and targets == self.targets:
            return
        self.state = state
        self.targets = targets
        if not self._motion():
            self.levels = dict(targets)
            self.refresh()
            return
        if self._glow_timer is None:
            self._glow_last = time.monotonic()
            self._glow_timer = self.set_interval(1 / GLOW_FPS, self._glow_tick)

    def _glow_tick(self) -> None:
        now = time.monotonic()
        dt = min(0.25, now - self._glow_last)
        self._glow_last = now
        settled = True
        for key, goal in self.targets.items():
            value = self.levels[key]
            up, down = TAU[key]
            value += (goal - value) * (1 - math.exp(-dt / (up if goal > value else down)))
            if abs(goal - value) < 0.004:
                value = goal
            else:
                settled = False
            self.levels[key] = value
        if settled and not self.levels["breathe"] and self._glow_timer is not None:
            self._glow_timer.stop()
            self._glow_timer = None
        self.refresh()

    def refresh(self, *args: Any, **kwargs: Any) -> Any:
        self._pix = None
        return super().refresh(*args, **kwargs)

    # -- pixel colors ------------------------------------------------------------

    def _letter_ends(self) -> tuple[RGB, RGB]:
        """The letters' left and right colors at the current levels."""
        idle, act, err = STATES["idle"], STATES["active"], STATES["error"]
        b, r = self.levels["blue"], self.levels["red"]
        left = mix(mix(idle["left"], act["left"], b), err["left"], r)
        right = mix(mix(idle["right"], act["right"], b), err["right"], r)
        return left, right

    def letter_color(self, x: int, y: int) -> RGB:
        left, right = self._letter_ends()
        color = mix(left, right, (x - self.ml) / max(1, self.bw - 1))
        return mix(color, BOTTOM, 0.16 * (y - self.mt) / max(1, self.bh - 1))

    def breath(self) -> tuple[float, float]:
        """(the glow's factor, the wave 0..1) of the breathing now."""
        amount = self.levels["breathe"]
        if not amount:
            return 1.0, 0.0
        wave = 0.5 - 0.5 * math.cos(2 * math.pi * (time.monotonic() - self._t0) / BREATH_S)
        return 1 - amount * (1 - (BREATH_LOW + (1 - BREATH_LOW) * wave)), wave

    def glow_now(self) -> tuple[RGB | None, list[float]]:
        """The glow color and the three rings' strength at the current levels."""
        b, r = self.levels["blue"], self.levels["red"]
        level = max(b, r)
        if level <= 0.004:
            return None, [0.0, 0.0, 0.0]
        act, err = STATES["active"], STATES["error"]
        share = r / (b + r)
        glow = mix(act["glow"], err["glow"], share)
        factor, _wave = self.breath()
        rings = []
        for d in (1, 2, 3):
            alpha = act["rings"][d - 1] + (err["rings"][d - 1] - act["rings"][d - 1]) * share
            start = (d - 1) * RING_STEP
            stage = max(0.0, min(1.0, (level - start) / (1 - start)))
            rings.append(alpha * stage * factor)
        return glow, rings

    def arrow_color(self, kind: str, row: int, lit: bool = True) -> RGB:
        if kind == "K":
            return EDGE
        if not lit:
            return GREEN_DIM_LIGHT if kind == "L" else GREEN_DIM
        color = GREEN_LIGHT if kind == "L" else mix(GREEN_TOP, GREEN_BOTTOM, row / max(1, self.arrow_h - 1))
        return mix(color, WHITE, 0.18) if self.hover else color

    def _upgrade_pixels(self) -> dict[tuple[int, int], RGB]:
        mode = self.upgrade
        out: dict[tuple[int, int], RGB] = {}
        if mode == "gone":
            return out
        if mode == "morph":
            k = self.progress
            span = max(1, self.bw - 1)
            for (sx, sy), (tx, ty) in self.pairs:
                m = max(0.0, min(1.0, (k - 0.45 * (sx - self.ml) / span) / 0.55))
                m = m * m * (3 - 2 * m)   # ease in and out
                kind, row = self.arrow[(tx, ty)]
                pos = (round(sx + (tx - sx) * m), round(sy + (ty - sy) * m))
                out[pos] = mix(self.letter_color(sx, sy), self.arrow_color(kind, row), m)
            return out
        dy = self.bob if mode == "arrow" else 0
        if mode == "launch":
            dy = -round(self.progress ** 2 * (self.ph + 4))
        threshold = (1 - self.fill) * (self.arrow_h - 1)
        for (x, y), (kind, row) in self.arrow.items():
            lit = mode != "fill" or row >= threshold
            if y + dy >= 0:
                out[(x, y + dy)] = self.arrow_color(kind, row, lit)
        if mode == "arrow":
            rings = (0.42, 0.18) if self.hover else (0.32, 0.12)
            for (x, y), d in self.arrow_ring.items():
                target = (x, y + dy)
                if target not in out and target[1] >= 0:
                    out[target] = mix(self.base, GREEN_GLOW, rings[d - 1])
        return out

    def pixels(self) -> dict[tuple[int, int], RGB]:
        """The color of every lit pixel at the current frame; unlit ones are absent."""
        return self._upgrade_pixels() if self.upgrade is not None else self._state_pixels()

    def _state_pixels(self) -> dict[tuple[int, int], RGB]:
        left, right = self._letter_ends()
        _factor, wave = self.breath()
        lift = 0.06 * self.levels["breathe"] * wave
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
            color = mix(left, right, t)
            color = mix(color, BOTTOM, 0.16 * (y - self.mt) / max(1, self.bh - 1))
            if lift:
                color = mix(color, WHITE, lift)
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
        glow, rings = self.glow_now()
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
        # One frame's pixels serve all its rows; refresh() starts a new frame.
        if self._pix is None:
            self._pix = self.pixels()
        pix = self._pix
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
        if self.upgrade == "arrow":
            self.post_message(self.UpgradeClicked())
            return
        if self.upgrade is not None:
            return
        self.click()

    # -- the upgrade arrow ----------------------------------------------------------

    def _motion(self) -> bool:
        try:
            return self.app.animation_level != "none"
        except Exception:  # noqa: BLE001 - not mounted (tests)
            return False

    def _stop_upgrade_timers(self) -> None:
        for name in ("_u_timer", "_bob_timer"):
            timer = getattr(self, name)
            if timer is not None:
                timer.stop()
                setattr(self, name, None)

    def show_arrow(self) -> None:
        """Turn the letters into the arrow (at once without motion)."""
        if self.upgrade in ("morph", "arrow"):
            return
        self.finish()
        self._stop_upgrade_timers()
        self.fill = 0.0
        if self._motion():
            self.upgrade = "morph"
            self.progress = 0.0
            self._u_started = time.monotonic()
            self._u_timer = self.set_interval(1 / FPS, self._upgrade_tick)
        else:
            self.upgrade = "arrow"
        self.refresh()

    def _arrow_rests(self) -> None:
        self.upgrade = "arrow"
        self.progress = 0.0
        self.bob = 0
        if self._motion():
            self._bob_timer = self.set_interval(BOB_S, self._bob_tick)

    def _bob_tick(self) -> None:
        self.bob = 1 - self.bob
        self.refresh()

    def set_fill(self, fraction: float) -> None:
        """The arrow fills from the bottom while the upgrade installs."""
        self._stop_upgrade_timers()
        self.upgrade = "fill"
        self.bob = 0
        self.fill = max(0.0, min(1.0, fraction))
        self.refresh()

    def launch(self) -> None:
        """The arrow shoots up and is gone."""
        self._stop_upgrade_timers()
        self.fill = 1.0
        if self._motion():
            self.upgrade = "launch"
            self.progress = 0.0
            self._u_started = time.monotonic()
            self._u_timer = self.set_interval(1 / FPS, self._upgrade_tick)
        else:
            self.upgrade = "gone"
        self.refresh()

    def clear_upgrade(self) -> None:
        """Back to the letters."""
        self._stop_upgrade_timers()
        self.upgrade = None
        self.progress = 0.0
        self.bob = 0
        self.refresh()

    def _upgrade_tick(self) -> None:
        length = MORPH_S if self.upgrade == "morph" else LAUNCH_S
        self.progress = min(1.0, (time.monotonic() - self._u_started) / length)
        if self.progress >= 1.0:
            self._stop_upgrade_timers()
            if self.upgrade == "morph":
                self._arrow_rests()
            else:
                self.upgrade = "gone"
                self.progress = 0.0
        self.refresh()

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
