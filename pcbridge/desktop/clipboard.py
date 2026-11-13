"""The clipboard seam under text input (Task 5.4).

WHY A MODULE OF ITS OWN
    Text is typed through the clipboard: `wl-copy` plus Ctrl+V does not
    depend on the `tr+intl` layout the way raw key codes do. The
    ORCHESTRATION stays in Python (`InputBackend._type_clipboard`: save,
    put, the native Ctrl+V, restore); the three clipboard operations sit
    behind `Clipboard`, and `NativeClipboard` (`backends/rust.py`) sends
    them to the grant-bound native helper.

THE PROGRAMS RUN IN THE HELPER
    The helper runs `wl-paste` and `wl-copy` (`clipboard.rs`) and carries
    the rules measured on this machine: `wl-copy`'s output is never
    captured (it lives on as the clipboard's owner and would hold a pipe
    open until a timeout), every type is read with `--no-newline`, and a
    restore keeps only the first offered type. The cases are pinned in
    `tests/fixtures/native/clipboard_cases.json`. The Python class that
    once ran the programs itself is gone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

TEXT_MIME = "text/plain;charset=utf-8"

#: The capability report's wording for the one-type restore.
SINGLE_MIME_LIMITATION = (
    "Clipboard restore keeps only the first offered type; other representations are lost."
)


class ClipboardError(RuntimeError):
    """Pano yazilamadi. Mesaj kullaniciya aynen doner."""


@dataclass(frozen=True)
class Saved:
    """Panonun yedeklenen tek temsili."""

    mime: str
    data: bytes


class Clipboard(Protocol):
    """Metin girisinin panodan istedigi uc islem."""

    def save(self) -> Saved | None: ...

    def put_text(self, text: str) -> None: ...

    def restore(self, saved: Saved | None) -> None: ...


__all__ = [
    "Clipboard",
    "ClipboardError",
    "SINGLE_MIME_LIMITATION",
    "Saved",
    "TEXT_MIME",
]
