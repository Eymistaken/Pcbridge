#!/usr/bin/env python3
"""The native clipboard behind `[native] input = "rust"` (Task 5.4, step 3).

Typing keeps its Python orchestration -- save, put, paste, restore -- and the
clipboard programs run in the helper. What this pins, without a helper, a
program or a key: the order of the IPC requests, that the paste key goes to the
native keyboard, that clipboard content travels only as a binary payload, and
how failures come back.
"""

from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop import clipboard as clipboardlib  # noqa: E402
from pcbridge.desktop import input as inputlib  # noqa: E402
from pcbridge.desktop.backends.python import PythonInputProvider  # noqa: E402
from pcbridge.desktop.backends.rust import NativeClipboard, RustInputProvider  # noqa: E402
from pcbridge.desktop.capabilities import CapabilityState  # noqa: E402
from pcbridge.desktop.errors import DesktopError, ErrorCategory, ErrorCode  # noqa: E402
from pcbridge.desktop.lease import LeaseToken  # noqa: E402
from pcbridge.native import NativeResponse  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "native" / "clipboard_cases.json"
TEXT = clipboardlib.TEXT_MIME


def entry_bytes(entry: dict) -> bytes:
    if "base64" in entry:
        return base64.b64decode(entry["base64"])
    return entry["text"].encode("utf-8")


class Grants:
    def __init__(self) -> None:
        self.token = LeaseToken(grant_id="clip-grant", revoke_epoch=2)

    def current_token(self):
        return self.token

    def last_token(self):
        return self.token


class ClipboardHelper:
    """The helper's clipboard methods over a modeled clipboard.

    `clipboard.read` answers what the Rust adapter's `save` answers: the first
    offered type, or empty when there is none or it cannot be read.
    """

    def __init__(self, initial: list[dict], *, features=("input.keyboard", "clipboard")) -> None:
        self.offers = [(e["mime"], entry_bytes(e), bool(e.get("read_fails"))) for e in initial]
        self.requests: list[tuple[str, dict, bytes]] = []
        self.failures: dict[str, DesktopError] = {}
        self.features = frozenset(features)
        self.is_running = True
        self.closed = False
        self.pasted: list[list[tuple[str, bytes]]] = []

    def state(self) -> list[tuple[str, bytes]]:
        return [(mime, data) for mime, data, _fails in self.offers]

    def request(self, method, params=None, *, binary=b"", timeout=None):
        params = dict(params or {})
        self.requests.append((method, params, bytes(binary)))
        if method in self.failures:
            raise self.failures.pop(method)
        result: dict = {}
        payload = b""
        if method == "clipboard.read":
            first = self.offers[0] if self.offers else None
            if first is None or first[2]:
                result = {"empty": True, "mime": None}
            else:
                result = {"empty": False, "mime": first[0]}
                payload = first[1]
        elif method == "clipboard.write":
            self.offers = [(params["mime"], bytes(binary), False)]
            result = {"written": len(binary)}
        elif method == "clipboard.clear":
            self.offers = []
            result = {"cleared": True}
        elif method == "input.keyboard.key":
            assert params["combo"] == "ctrl+v", params
            self.pasted.append(self.state())
            result = {"held": []}
        elif method.endswith("release_all"):
            result = {"released": []}
        else:
            raise AssertionError(f"unexpected request {method}")
        return NativeResponse(request_id="fixture", result=result, binary=payload, error=None)

    def methods(self) -> list[str]:
        return [method for method, _params, _binary in self.requests]

    def close(self) -> None:
        self.closed = True
        self.is_running = False


def refusal(code: ErrorCode, category: ErrorCategory, message: str = "refused") -> DesktopError:
    return DesktopError(
        code=code,
        message=message,
        category=category,
        retryable=False,
        suggested_action="fixture",
        backend="pcbridge-native",
    )


class NativeClipboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]

    def setUp(self) -> None:
        self.cfg = load_config(str(ROOT / "config.example.toml"))
        sleep = mock.patch.object(inputlib.time, "sleep", lambda _seconds: None)
        sleep.start()
        self.addCleanup(sleep.stop)

    def provider(self, helper: ClipboardHelper) -> RustInputProvider:
        return RustInputProvider(self.cfg, gate=Grants(), client=helper)

    def test_the_rust_provider_uses_the_native_clipboard(self) -> None:
        self.assertIsInstance(self.provider(ClipboardHelper([])).clipboard, NativeClipboard)
        self.assertIsInstance(PythonInputProvider(self.cfg).clipboard, clipboardlib.WlClipboard)

    def test_every_fixture_case_types_and_restores_through_the_helper(self) -> None:
        for case in self.cases:
            with self.subTest(case=case["name"]):
                helper = ClipboardHelper(case["initial"])
                self.provider(helper).type_text(case["text"], restore_clipboard=case["restore"])

                restored = [
                    ("clipboard.write" if entry else "clipboard.clear")
                    for entry in ([case["final"][0]] if case["final"] else [None])
                ]
                expected = (
                    ["clipboard.read", "clipboard.write", "input.keyboard.key", *restored]
                    if case["restore"]
                    else ["clipboard.write", "input.keyboard.key"]
                )
                self.assertEqual(helper.methods(), expected)
                self.assertEqual(helper.pasted, [[(TEXT, case["text"].encode("utf-8"))]])
                self.assertEqual(
                    helper.state(),
                    [(e["mime"], entry_bytes(e)) for e in case["final"]],
                )

    def test_content_travels_only_as_binary_and_every_request_names_the_grant(self) -> None:
        case = next(c for c in self.cases if c["name"] == "text_is_restored_byte_for_byte")
        helper = ClipboardHelper(case["initial"])
        self.provider(helper).type_text(case["text"], restore_clipboard=True)
        secrets = {case["text"], case["initial"][0]["text"]}
        for method, params, binary in helper.requests:
            with self.subTest(method=method):
                self.assertEqual(params["grant_id"], "clip-grant")
                self.assertEqual(params["revoke_epoch"], 2)
                if method.startswith("clipboard."):
                    self.assertLessEqual(set(params), {"grant_id", "revoke_epoch", "mime"})
                header = json.dumps(params, ensure_ascii=False)
                self.assertFalse(any(secret in header for secret in secrets), header)
                if method != "clipboard.write":
                    self.assertEqual(binary, b"")
        writes = [binary for method, _params, binary in helper.requests if method == "clipboard.write"]
        self.assertEqual(
            writes,
            [case["text"].encode("utf-8"), case["initial"][0]["text"].encode("utf-8")],
        )

    def test_a_failed_put_is_typed_and_nothing_is_pasted(self) -> None:
        helper = ClipboardHelper([{"mime": TEXT, "text": "eski"}])
        helper.failures["clipboard.write"] = refusal(
            ErrorCode.DEPENDENCY_MISSING, ErrorCategory.CAPABILITY, "wl-copy was not found"
        )
        with self.assertRaises(DesktopError) as raised:
            self.provider(helper).type_text("yeni", restore_clipboard=True)
        self.assertIs(raised.exception.code, ErrorCode.DEPENDENCY_MISSING)
        self.assertNotIn("input.keyboard.key", helper.methods())

    def test_a_failing_restore_program_does_not_fail_a_typed_text(self) -> None:
        """The paste happened; failing now would invite typing it twice."""
        helper = ClipboardHelper([{"mime": TEXT, "text": "eski"}])
        provider = self.provider(helper)
        original_request = helper.request

        def fail_second_write(method, params=None, **kwargs):
            if method == "clipboard.write" and "input.keyboard.key" in helper.methods():
                helper.requests.append((method, dict(params or {}), kwargs.get("binary", b"")))
                raise refusal(ErrorCode.EXECUTION_UNKNOWN, ErrorCategory.EXECUTION, "wl-copy failed")
            return original_request(method, params, **kwargs)

        helper.request = fail_second_write  # type: ignore[method-assign]
        with self.assertLogs("pcbridge.desktop.backends.rust", level="WARNING"):
            note = provider.type_text("yeni", restore_clipboard=True)
        self.assertIn("characters through the clipboard", note)

    def test_a_revoke_before_the_restore_is_not_hidden(self) -> None:
        helper = ClipboardHelper([{"mime": TEXT, "text": "eski"}])
        provider = self.provider(helper)
        original_request = helper.request

        def revoke_at_restore(method, params=None, **kwargs):
            if method == "clipboard.write" and "input.keyboard.key" in helper.methods():
                raise refusal(ErrorCode.REVOKED, ErrorCategory.SAFETY)
            return original_request(method, params, **kwargs)

        helper.request = revoke_at_restore  # type: ignore[method-assign]
        with self.assertRaises(DesktopError) as raised:
            provider.type_text("yeni", restore_clipboard=True)
        self.assertIs(raised.exception.code, ErrorCode.REVOKED)

    def test_a_helper_without_the_clipboard_asks_to_be_rebuilt(self) -> None:
        helper = ClipboardHelper([], features=("input.keyboard",))
        helper.failures["clipboard.read"] = refusal(
            ErrorCode.UNSUPPORTED, ErrorCategory.IPC, "method 'clipboard.read' is not available"
        )
        with self.assertRaises(DesktopError) as raised:
            self.provider(helper).type_text("metin", restore_clipboard=True)
        self.assertIs(raised.exception.code, ErrorCode.BACKEND_UNAVAILABLE)
        self.assertIn("build-native.sh", raised.exception.message)
        self.assertEqual(helper.methods(), ["clipboard.read"], "nothing written, nothing pasted")

    def test_no_grant_means_no_clipboard_request(self) -> None:
        helper = ClipboardHelper([{"mime": TEXT, "text": "eski"}])
        provider = self.provider(helper)
        provider.gate.token = None  # type: ignore[attr-defined]
        with self.assertRaises(DesktopError) as raised:
            provider.type_text("metin", restore_clipboard=True)
        self.assertIs(raised.exception.code, ErrorCode.GRANT_REQUIRED)
        self.assertEqual(helper.requests, [])

    def test_both_providers_report_the_single_type_restore_on_the_write(self) -> None:
        with mock.patch("pcbridge.desktop.backends.python.shutil.which", return_value="/usr/bin/wl-copy"), \
                mock.patch("pcbridge.desktop.backends.python._wayland_socket", return_value="/run/wayland-0"), \
                mock.patch("pcbridge.desktop.backends.rust.native_binary_ready", return_value=(True, "")):
            native = self.provider(ClipboardHelper([])).probe_capabilities()
            python = PythonInputProvider(self.cfg).probe_capabilities()
        for values, backend in (
            (native, "linux.wl-clipboard.native"),
            (python, "linux.wl-clipboard"),
        ):
            with self.subTest(backend=backend):
                self.assertEqual(values["clipboard.write"].backend, backend)
                self.assertIs(values["clipboard.write"].state, CapabilityState.SUPPORTED)
                self.assertEqual(
                    values["clipboard.write"].limitations,
                    (clipboardlib.SINGLE_MIME_LIMITATION,),
                )
                self.assertEqual(values["clipboard.read"].limitations, ())

    def test_without_the_helper_the_native_clipboard_is_unavailable_not_python(self) -> None:
        with mock.patch(
            "pcbridge.desktop.backends.rust.native_binary_ready",
            return_value=(False, "pcbridge-native bulunamadi"),
        ):
            values = self.provider(ClipboardHelper([])).probe_capabilities()
        for name in ("clipboard.read", "clipboard.write"):
            self.assertIs(values[name].state, CapabilityState.UNAVAILABLE)
            self.assertEqual(values[name].backend, "linux.wl-clipboard.native")
            self.assertIs(values[name].reason_code, ErrorCode.DEPENDENCY_MISSING)


if __name__ == "__main__":
    unittest.main(verbosity=2)
