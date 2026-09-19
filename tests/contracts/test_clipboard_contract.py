#!/usr/bin/env python3
"""Clipboard save, put and restore around a paste (Task 5.4).

The cases live in `tests/fixtures/native/clipboard_cases.json`, which the Rust
wl-clipboard adapter reads too: same programs, same arguments, same clipboard
afterwards. Here they drive the Python `WlClipboard` through the real
`InputBackend.type_text` orchestration.

No program runs and no key is sent. `subprocess.run` is replaced by a model of
the Wayland clipboard that answers `wl-paste` and `wl-copy` the way the real
programs do, and `key()` only records where the paste went.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.desktop import clipboard as clipboardlib  # noqa: E402
from pcbridge.desktop import input as inputlib  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "native" / "clipboard_cases.json"


def is_text(mime: str) -> bool:
    """What wl-paste counts as text, and so ends with a newline unless told not to."""
    return mime.startswith("text/") or mime in {"UTF8_STRING", "STRING", "TEXT"}


def entry_bytes(entry: dict) -> bytes:
    if "base64" in entry:
        return base64.b64decode(entry["base64"])
    return entry["text"].encode("utf-8")


class ModelClipboard:
    """The compositor's clipboard as `wl-paste` and `wl-copy` see it."""

    def __init__(self, initial: list[dict]) -> None:
        self.offers = [(entry["mime"], entry_bytes(entry), bool(entry.get("read_fails")))
                       for entry in initial]
        self.calls: list[list[str] | str] = []
        self.captured: list[tuple[list[str], bool]] = []

    def state(self) -> list[tuple[str, bytes]]:
        return [(mime, data) for mime, data, _fails in self.offers]

    def run(self, args, input=None, capture_output=False, stdout=None, stderr=None,
            timeout=None, check=False):
        argv = list(args)
        self.calls.append(argv)
        captured = bool(capture_output) or stdout == subprocess.PIPE or stderr == subprocess.PIPE
        self.captured.append((argv, captured))
        if argv[0] == "wl-paste" and argv[1:] == ["--list-types"]:
            if not self.offers:
                return subprocess.CompletedProcess(argv, 1, b"", b"Nothing is copied\n")
            listing = "".join(f"{mime}\n" for mime, _data, _fails in self.offers)
            return subprocess.CompletedProcess(argv, 0, listing.encode(), b"")
        if argv[0] == "wl-paste" and argv[1] == "--type":
            mime = argv[2]
            for offered, data, fails in self.offers:
                if offered == mime and not fails:
                    if is_text(mime) and "--no-newline" not in argv:
                        data += b"\n"  # what wl-paste appends by default
                    return subprocess.CompletedProcess(argv, 0, data, b"")
            return subprocess.CompletedProcess(argv, 1, b"", b"cannot read\n")
        if argv == ["wl-copy", "--clear"]:
            self.offers = []
            return subprocess.CompletedProcess(argv, 0, None, None)
        if argv[:2] == ["wl-copy", "--type"] and len(argv) == 3:
            self.offers = [(argv[2], bytes(input or b""), False)]
            return subprocess.CompletedProcess(argv, 0, None, None)
        raise AssertionError(f"unexpected program call: {argv}")


class Typist(inputlib.InputBackend):
    """The real orchestration; only the paste key is recorded instead of sent."""

    def __init__(self, model: ModelClipboard) -> None:
        super().__init__()
        self.model = model
        self.pasted: list[list[tuple[str, bytes]]] = []

    def key(self, combo: str) -> None:
        assert combo == "ctrl+v", combo
        self.model.calls.append("PASTE")
        self.pasted.append(self.model.state())


class ClipboardFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def run_case(self, case: dict) -> tuple[ModelClipboard, Typist, str]:
        model = ModelClipboard(case["initial"])
        typist = Typist(model)
        with mock.patch.object(clipboardlib.subprocess, "run", model.run), \
                mock.patch.object(inputlib.time, "sleep", lambda _seconds: None):
            note = typist.type_text(case["text"], restore_clipboard=case["restore"])
        return model, typist, note

    def test_the_fixture_is_the_one_both_sides_read(self) -> None:
        self.assertEqual(self.fixture["schema_version"], 1)
        self.assertEqual(self.fixture["text_mime"], clipboardlib.TEXT_MIME)
        self.assertGreaterEqual(len(self.fixture["cases"]), 7)

    def test_every_case_runs_the_same_programs_and_restores_the_same_clipboard(self) -> None:
        for case in self.fixture["cases"]:
            with self.subTest(case=case["name"]):
                model, typist, _note = self.run_case(case)
                self.assertEqual(model.calls, case["calls"])
                self.assertEqual(
                    typist.pasted,
                    [[(clipboardlib.TEXT_MIME, case["text"].encode("utf-8"))]],
                    "the paste must see exactly the typed text",
                )
                self.assertEqual(
                    model.state(),
                    [(entry["mime"], entry_bytes(entry)) for entry in case["final"]],
                )

    def test_wl_copy_never_captures_its_output(self) -> None:
        """The pipe trap: a captured wl-copy hangs until its timeout."""
        for case in self.fixture["cases"]:
            model, _typist, _note = self.run_case(case)
            for argv, captured in model.captured:
                with self.subTest(case=case["name"], argv=argv):
                    self.assertIs(captured, argv[0] == "wl-paste")

    def test_the_note_still_says_what_happened_to_the_clipboard(self) -> None:
        cases = {case["name"]: case for case in self.fixture["cases"]}
        for name, ending in (
            ("text_is_restored_byte_for_byte", "; pano eski icerigine donduruldu"),
            ("an_empty_clipboard_is_cleared_again", "; pano temizlendi"),
            ("without_restore_the_typed_text_stays", "karakter yapistirildi"),
        ):
            with self.subTest(case=name):
                _model, _typist, note = self.run_case(cases[name])
                self.assertTrue(note.endswith(ending), note)

    def test_a_failed_write_stops_before_the_paste(self) -> None:
        model = ModelClipboard([{"mime": clipboardlib.TEXT_MIME, "text": "eski"}])
        typist = Typist(model)

        def refuse(args, **kwargs):
            if list(args)[0] == "wl-copy":
                return subprocess.CompletedProcess(list(args), 1, None, None)
            return model.run(args, **kwargs)

        with mock.patch.object(clipboardlib.subprocess, "run", refuse), \
                mock.patch.object(inputlib.time, "sleep", lambda _seconds: None):
            with self.assertRaises(inputlib.InputError) as raised:
                typist.type_text("yeni", restore_clipboard=True)
        self.assertIn("wl-copy exit 1", str(raised.exception))
        self.assertEqual(typist.pasted, [], "nothing may be pasted after a failed write")

    def test_a_missing_wl_copy_names_the_package(self) -> None:
        typist = Typist(ModelClipboard([]))

        def missing(args, **kwargs):
            raise FileNotFoundError(args[0])

        with mock.patch.object(clipboardlib.subprocess, "run", missing), \
                mock.patch.object(inputlib.time, "sleep", lambda _seconds: None):
            with self.assertRaises(inputlib.InputError) as raised:
                typist.type_text("metin", restore_clipboard=False)
        self.assertIn("sudo apt install wl-clipboard", str(raised.exception))

    def test_the_input_backend_uses_an_injected_clipboard(self) -> None:
        class Recording:
            def __init__(self) -> None:
                self.log: list[str] = []

            def save(self):
                self.log.append("save")
                return clipboardlib.Saved("text/html", b"<i>x</i>")

            def put_text(self, text):
                self.log.append(f"put {text}")

            def restore(self, saved):
                self.log.append(f"restore {saved.mime}")

        recording = Recording()
        backend = inputlib.InputBackend(clipboard=recording)
        backend.key = lambda combo: recording.log.append(combo)  # type: ignore[method-assign]
        with mock.patch.object(inputlib.time, "sleep", lambda _seconds: None):
            backend.type_text("çğ", restore_clipboard=True)
        self.assertEqual(recording.log, ["save", "put çğ", "ctrl+v", "restore text/html"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
