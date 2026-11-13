#!/usr/bin/env python3
"""The typing orchestration around a clipboard paste (Task 5.4).

`InputBackend._type_clipboard` saves the clipboard, puts the text, presses
the paste key, and restores what was there only when the clipboard still
holds the typed text. The clipboard itself is injected: in pcbridge it is
the native helper's (`NativeClipboard`), whose fixture cases --
`tests/fixtures/native/clipboard_cases.json` -- run in
`test_native_clipboard.py` and, against the real programs' behavior, in
Rust (`clipboard_contract.rs`).

No program runs and no key is sent: `key()` only records where the paste
went.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.desktop import clipboard as clipboardlib  # noqa: E402
from pcbridge.desktop import input as inputlib  # noqa: E402


class MemoryClipboard:
    """A clipboard that holds one representation, like a restore leaves it."""

    def __init__(self, current: clipboardlib.Saved | None = None) -> None:
        self.current = current
        self.log: list[str] = []

    def save(self) -> clipboardlib.Saved | None:
        self.log.append("save")
        return self.current

    def put_text(self, text: str) -> None:
        self.log.append(f"put {text}")
        self.current = clipboardlib.Saved(clipboardlib.TEXT_MIME, text.encode("utf-8"))

    def restore(self, saved: clipboardlib.Saved | None) -> None:
        self.log.append("restore" if saved is not None else "clear")
        self.current = saved


def typist(clipboard) -> inputlib.InputBackend:
    backend = inputlib.InputBackend(clipboard=clipboard)
    backend.key = lambda combo: clipboard.log.append(combo)  # type: ignore[method-assign]
    return backend


class TypingOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        sleep = mock.patch.object(inputlib.time, "sleep", lambda _seconds: None)
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_the_input_backend_uses_an_injected_clipboard(self) -> None:
        clipboard = MemoryClipboard(clipboardlib.Saved("text/html", b"<i>x</i>"))
        typist(clipboard).type_text("çğ", restore_clipboard=True)
        self.assertEqual(clipboard.log, ["save", "put çğ", "ctrl+v", "save", "restore"])
        self.assertEqual(clipboard.current, clipboardlib.Saved("text/html", b"<i>x</i>"))

    def test_the_note_says_what_happened_to_the_clipboard(self) -> None:
        for name, start, restore, ending in (
            ("restored", clipboardlib.Saved(clipboardlib.TEXT_MIME, b"old"), True,
             "; clipboard restored"),
            ("cleared", None, True, "; clipboard cleared"),
            ("left", clipboardlib.Saved(clipboardlib.TEXT_MIME, b"old"), False,
             "characters through the clipboard"),
        ):
            with self.subTest(case=name):
                note = typist(MemoryClipboard(start)).type_text("new", restore_clipboard=restore)
                self.assertTrue(note.endswith(ending), note)

    def test_without_restore_the_clipboard_is_never_read(self) -> None:
        clipboard = MemoryClipboard(clipboardlib.Saved(clipboardlib.TEXT_MIME, b"old"))
        typist(clipboard).type_text("new", restore_clipboard=False)
        self.assertEqual(clipboard.log, ["put new", "ctrl+v"])

    def test_a_failed_write_stops_before_the_paste(self) -> None:
        class Refusing(MemoryClipboard):
            def put_text(self, text: str) -> None:
                self.log.append("put refused")
                raise clipboardlib.ClipboardError("wl-copy exited 1")

        clipboard = Refusing(clipboardlib.Saved(clipboardlib.TEXT_MIME, b"old"))
        with self.assertRaises(inputlib.InputError) as raised:
            typist(clipboard).type_text("new", restore_clipboard=True)
        self.assertIn("wl-copy exited 1", str(raised.exception))
        self.assertNotIn("ctrl+v", clipboard.log, "nothing may be pasted after a failed write")

    def test_changed_or_unreadable_clipboard_is_not_replaced_after_pasting(self) -> None:
        for replacement in (
            clipboardlib.Saved(clipboardlib.TEXT_MIME, b"new owner"),
            clipboardlib.Saved("image/png", b"temporary"),
            None,
        ):
            with self.subTest(replacement=replacement):
                clipboard = MemoryClipboard(clipboardlib.Saved(clipboardlib.TEXT_MIME, b"original"))
                backend = inputlib.InputBackend(clipboard=clipboard)
                backend.key = lambda _combo: setattr(  # type: ignore[method-assign]
                    clipboard, "current", replacement)
                note = backend.type_text("temporary", restore_clipboard=True)
                self.assertEqual(clipboard.current, replacement)
                self.assertNotIn("restore", clipboard.log)
                self.assertIn("clipboard changed", note)

    def test_a_clipboard_is_required(self) -> None:
        # There is no Python clipboard to fall back on.
        with self.assertRaises(TypeError):
            inputlib.InputBackend()  # type: ignore[call-arg]


if __name__ == "__main__":
    unittest.main(verbosity=2)
