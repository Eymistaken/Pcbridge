#!/usr/bin/env python3
"""Typing through the native clipboard, end to end (Task 5.4, step 3).

Python orchestration -> NativeClient -> the Rust helper -> its wl-clipboard
adapter -> programs, for every case of the shared fixture. The helper runs in
`--test-mode`: its keyboard is a null device, so the paste key reaches nothing,
and its clipboard runs only the programs named in `PCBRIDGE_TEST_WL_PASTE` and
`PCBRIDGE_TEST_WL_COPY`. Those are small scripts keeping a modeled clipboard in
a scratch file, so the user's clipboard is never read or written.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop import input as inputlib  # noqa: E402
from pcbridge.desktop.backends.rust import RustInputProvider  # noqa: E402
from pcbridge.desktop.safety import SafetyGate  # noqa: E402
from pcbridge.native.client import NativeClient  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "native" / "clipboard_cases.json"

# One program for both names: argv[0] says which it is. The state is a JSON
# list of [mime, base64, read_fails]; every call is appended to the log.
PROGRAM = r'''#!{python}
import base64, json, os, sys
STATE, LOG = {state!r}, {log!r}
name, args = os.path.basename(sys.argv[0]), sys.argv[1:]
with open(LOG, "a", encoding="utf-8") as log:
    log.write(json.dumps([name, *args]) + "\n")
with open(STATE, encoding="utf-8") as fh:
    offers = json.load(fh)
if name == "wl-paste":
    if args == ["--list-types"]:
        if not offers:
            sys.exit(1)
        sys.stdout.write("".join(mime + "\n" for mime, _data, _fails in offers))
        sys.exit(0)
    if args[:1] == ["--type"]:
        for mime, data, fails in offers:
            if mime == args[1] and not fails:
                raw = base64.b64decode(data)
                text = mime.startswith("text/") or mime in ("UTF8_STRING", "STRING", "TEXT")
                if text and "--no-newline" not in args:
                    raw += b"\n"
                sys.stdout.buffer.write(raw)
                sys.exit(0)
        sys.exit(1)
    sys.exit(2)
if args == ["--clear"]:
    offers = []
elif len(args) == 2 and args[0] == "--type":
    offers = [[args[1], base64.b64encode(sys.stdin.buffer.read()).decode(), False]]
else:
    sys.exit(2)
with open(STATE, "w", encoding="utf-8") as fh:
    json.dump(offers, fh)
'''


def entry_bytes(entry: dict) -> bytes:
    if "base64" in entry:
        return base64.b64decode(entry["base64"])
    return entry["text"].encode("utf-8")


class RecordingClient(NativeClient):
    """The real client, remembering the order of what it sent."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.sent: list[str] = []

    def request(self, method, params=None, *, binary=b"", timeout=None):
        self.sent.append(method)
        return super().request(method, params, binary=binary, timeout=timeout)


@unittest.skipUnless(sys.platform.startswith("linux"), "native helper is built for Linux")
class NativeClipboardEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not (ROOT / "rust" / "Cargo.toml").is_file():
            raise unittest.SkipTest("builds the native test harness; needs the rust/ workspace of a git checkout")
        completed = subprocess.run(
            ["cargo", "build", "-p", "pcbridge-native", "--features", "test-harness"],
            cwd=ROOT / "rust",
            capture_output=True,
            text=True,
            timeout=300,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr)
        cls.native_binary = ROOT / "rust/target/debug/pcbridge-native"
        cls.cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "clipboard.json"
        self.log = self.root / "programs.log"
        for name in ("wl-paste", "wl-copy"):
            path = self.root / name
            path.write_text(
                PROGRAM.format(python=sys.executable, state=str(self.state), log=str(self.log)),
                encoding="utf-8",
            )
            path.chmod(0o755)
        wrapper = self.root / "native-test-mode"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{self.native_binary}" --test-mode "$@"\n', encoding="utf-8"
        )
        wrapper.chmod(0o755)
        self.cfg = dataclasses.replace(
            load_config(str(ROOT / "config.example.toml")), state_dir=self.root / "state"
        )
        self.gate = SafetyGate(self.cfg)
        self.gate.unlock(5, reason="clipboard end to end")
        self.clients: list[RecordingClient] = []

        def helper() -> RecordingClient:
            client = RecordingClient(
                wrapper,
                state_dir=self.cfg.state_dir,
                runtime_dir=self.root / "runtime",
                environment={
                    "PCBRIDGE_TEST_WL_PASTE": str(self.root / "wl-paste"),
                    "PCBRIDGE_TEST_WL_COPY": str(self.root / "wl-copy"),
                },
            )
            self.clients.append(client)
            return client

        self.provider = RustInputProvider(self.cfg, gate=self.gate, client_factory=helper)
        self.addCleanup(self.provider.close)
        sleep = mock.patch.object(inputlib.time, "sleep", lambda _seconds: None)
        sleep.start()
        self.addCleanup(sleep.stop)

    def model(self, entries: list[dict]) -> None:
        self.state.write_text(
            json.dumps([
                [e["mime"], base64.b64encode(entry_bytes(e)).decode(), bool(e.get("read_fails"))]
                for e in entries
            ]),
            encoding="utf-8",
        )
        self.log.write_text("", encoding="utf-8")

    def clipboard(self) -> list[tuple[str, bytes]]:
        offers = json.loads(self.state.read_text(encoding="utf-8"))
        return [(mime, base64.b64decode(data)) for mime, data, _fails in offers]

    def program_calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def test_every_fixture_case_through_the_real_helper(self) -> None:
        for case in self.cases:
            with self.subTest(case=case["name"]):
                self.model(case["initial"])
                started = time.monotonic()
                self.provider.type_text(case["text"], restore_clipboard=case["restore"])
                elapsed = time.monotonic() - started

                self.assertEqual(
                    self.program_calls(),
                    [call for call in case["calls"] if call != "PASTE"],
                )
                self.assertEqual(
                    self.clipboard(),
                    [(e["mime"], entry_bytes(e)) for e in case["final"]],
                )
                sent = [m for m in self.clients[-1].sent if m != "input.keyboard.release_all"]
                paste = sent.index("input.keyboard.key")
                self.assertEqual(sent[paste - 1], "clipboard.write", "put, then paste")
                self.assertLess(elapsed, 10.0)
        self.assertEqual(len(self.clients), 1, "one grant, one helper")

    def test_a_new_grant_moves_the_clipboard_to_a_new_helper(self) -> None:
        self.model([{"mime": "text/plain;charset=utf-8", "text": "önce"}])
        self.provider.type_text("bir", restore_clipboard=True)
        self.gate.unlock(5, reason="second grant while open")
        self.provider.type_text("iki", restore_clipboard=True)
        self.assertEqual(len(self.clients), 2)
        self.assertFalse(self.clients[0].is_running)
        self.assertEqual(self.clipboard(), [("text/plain;charset=utf-8", "önce".encode())])


if __name__ == "__main__":
    unittest.main(verbosity=2)
