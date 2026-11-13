"""Keyboard and pointer: what stays in Python above the native helper.

The native helper owns the virtual devices on `/dev/uinput`: the keyboard,
the absolute pointer and its relative sibling, the hold timer and the
release on a revoke (`backends/rust.py`, `RustInputProvider`). A device
made through uinput is treated by the compositor as real hardware: no
consent dialog, no portal, the same on GNOME, KDE Plasma and Hyprland. The
python-evdev devices that once did the same here are gone.

WHAT STAYS HERE
    The contract every input backend meets (`InputBackend`) and the typing
    orchestration on top of it. uinput sends raw key codes; what appears on
    the screen is decided by the system's XKB layout (on the reference
    machine `tr+intl`). So the default way to type text is the CLIPBOARD
    and Ctrl+V: independent of the layout and much faster for long texts.
    The raw-key path (`raw=True`) stays, for a US layout and for places
    with no clipboard, but it is not the default.

COORDINATES
    Every pointer call takes **global canvas coordinates**. A
    monitor-relative coordinate is converted once, at the `tools.py`
    boundary (`capture.to_global()`).
"""

from __future__ import annotations

import time
from typing import Any

from . import clipboard as clipboardlib

UINPUT_NODE = "/dev/uinput"

# How long the button stays down in a click (Step 8.3). Applications that
# poll input at a fixed interval (games tick every 50 ms) may miss a click
# whose press and release fall into the same interval; 60 ms always meets
# at least one 50 ms poll. Each press of a double or triple click lasts this
# long; with the 80 ms between them that is 140 ms from press to press,
# under GNOME's 400 ms double-click threshold.
DEFAULT_CLICK_HOLD_MS = 60
# The longest hold one click may ask for. A long press is a "hold" job, with
# `mouse_down`/`mouse_up` and the hold timer.
MAX_CLICK_HOLD_MS = 1000


class InputError(RuntimeError):
    """The input was not sent: no device, no permission, or a bad parameter."""


def click_hold_ms_checked(value: Any) -> int:
    """Check a click's hold time (ms, 0..MAX_CLICK_HOLD_MS)."""
    try:
        ms = int(value)
    except (TypeError, ValueError):
        raise InputError(f"hold_ms must be a number ({value!r} given)") from None
    if not 0 <= ms <= MAX_CLICK_HOLD_MS:
        raise InputError(
            f"hold_ms must be between 0 and {MAX_CLICK_HOLD_MS} ({ms} given); "
            "to hold longer use mouse_down/mouse_up"
        )
    return ms


# The raw typing path (raw=True): ASCII -> (key, needs shift). It assumes a
# US layout; another layout gives other characters, so it is not the default.
_RAW_ASCII: dict[str, tuple[str, bool]] = {}
for _c in "abcdefghijklmnopqrstuvwxyz":
    _RAW_ASCII[_c] = (_c, False)
    _RAW_ASCII[_c.upper()] = (_c, True)
for _c, _shifted in zip("1234567890", "!@#$%^&*()"):
    _RAW_ASCII[_c] = (_c, False)
    _RAW_ASCII[_shifted] = (_c, True)
_RAW_ASCII.update(
    {
        " ": ("space", False),
        "\n": ("return", False),
        "\t": ("tab", False),
        "-": ("minus", False),
        "_": ("minus", True),
        "=": ("equal", False),
        "+": ("equal", True),
        ",": ("comma", False),
        "<": ("comma", True),
        ".": ("dot", False),
        ">": ("dot", True),
        "/": ("slash", False),
        "?": ("slash", True),
        "\\": ("backslash", False),
        "|": ("backslash", True),
        ";": ("semicolon", False),
        ":": ("semicolon", True),
        "'": ("apostrophe", False),
        '"': ("apostrophe", True),
        "`": ("grave", False),
        "~": ("grave", True),
        "[": ("leftbrace", False),
        "{": ("leftbrace", True),
        "]": ("rightbrace", False),
        "}": ("rightbrace", True),
    }
)


class InputBackend:
    """The keyboard and pointer contract, and the typing built on it.

    A backend implements the device methods; `type_text` turns text into
    clipboard pastes or raw keys through them. `close()` releases what is
    held and destroys the devices: it is also the emergency stop
    (`desktop_lock` calls it, and so does the service dying).
    """

    def __init__(self, clipboard: clipboardlib.Clipboard) -> None:
        # The native backend passes the helper's clipboard; there is no
        # Python one to fall back on.
        self.clipboard: clipboardlib.Clipboard = clipboard

    # ----------------------------------------------- implemented by a backend
    def available(self) -> tuple[bool, str]:
        raise NotImplementedError

    def ensure(
        self, keyboard: bool = False, pointer: bool = False, relative: bool = False,
    ) -> float:
        """Create the devices asked for and wait once; returns the seconds waited."""
        raise NotImplementedError

    @property
    def position(self) -> tuple[int, int] | None:
        raise NotImplementedError

    def move(self, x: int, y: int, smooth: bool | None = None) -> tuple[int, int]:
        raise NotImplementedError

    def move_by(self, dx: int, dy: int) -> tuple[int, int]:
        raise NotImplementedError

    def click(self, button: str = "left", count: int = 1, hold_ms: int | None = None) -> None:
        raise NotImplementedError

    def drag(self, x: int, y: int, to_x: int, to_y: int, button: str = "left") -> None:
        raise NotImplementedError

    def scroll(self, amount: int, horizontal: bool = False) -> None:
        raise NotImplementedError

    def mouse_down(self, button: str = "left") -> None:
        raise NotImplementedError

    def mouse_up(self, button: str = "left") -> None:
        raise NotImplementedError

    def key(self, combo: str) -> None:
        raise NotImplementedError

    def key_down(self, combo: str) -> None:
        raise NotImplementedError

    def key_up(self, combo: str) -> None:
        raise NotImplementedError

    def held(self) -> list[str]:
        raise NotImplementedError

    def release_all(self) -> list[str]:
        raise NotImplementedError

    def take_auto_released(self) -> list[str]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    # ----------------------------------------------------------- typing text
    def type_text(
        self, text: str, raw: bool = False, restore_clipboard: bool = True
    ) -> str:
        """Type text. The default path is the CLIPBOARD (layout-independent);
        `raw=True` sends raw keys.

        Returns a short note saying what was done.
        """
        if not text:
            return "empty text, nothing done"
        if raw:
            return self._type_raw(text)
        return self._type_clipboard(text, restore_clipboard)

    def _type_clipboard(self, text: str, restore: bool) -> str:
        saved = self.clipboard.save() if restore else None
        try:
            self.clipboard.put_text(text)
        except clipboardlib.ClipboardError as exc:
            raise InputError(str(exc)) from exc
        time.sleep(0.15)
        self.key("ctrl+v")
        time.sleep(0.25)
        note = f"pasted {len(text)} characters through the clipboard"
        if restore:
            # wl-copy exposes no owner token. Re-read the temporary text before
            # restoring; an identical replacement or a later change remains
            # indistinguishable from our own clipboard value.
            current = self.clipboard.save()
            if (
                current is not None
                and current.mime in (
                    clipboardlib.TEXT_MIME, "text/plain", "UTF8_STRING", "STRING", "TEXT"
                )
                and current.data == text.encode("utf-8")
            ):
                self.clipboard.restore(saved)
                note += "; clipboard restored" if saved else "; clipboard cleared"
            else:
                note += "; clipboard changed or unreadable; left as is"
        return note

    def _type_raw(self, text: str) -> str:
        """The raw key code path -- LAYOUT-DEPENDENT.

        With another layout than US (this machine runs `tr+intl`) the codes
        give other punctuation (`"` for `@`); letters and digits are right.
        It stays, on purpose, as the second choice.
        """
        unknown: list[str] = []
        for ch in text:
            entry = _RAW_ASCII.get(ch)
            if entry is None:
                unknown.append(ch)
                continue
            name, shift = entry
            self.key(f"shift+{name}" if shift else name)
            time.sleep(0.012)
        note = f"typed {len(text) - len(unknown)} characters as raw keys"
        if unknown:
            note += (
                f"; {len(unknown)} characters skipped ({''.join(sorted(set(unknown)))[:20]}) "
                "— the raw path only handles ASCII; use the clipboard path for other text"
            )
        return note


__all__ = [
    "DEFAULT_CLICK_HOLD_MS",
    "InputBackend",
    "InputError",
    "MAX_CLICK_HOLD_MS",
    "UINPUT_NODE",
    "click_hold_ms_checked",
]
