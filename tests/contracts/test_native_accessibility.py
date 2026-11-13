#!/usr/bin/env python3
"""The native accessibility helper behind the provider contract (Tasks 6.2, 6.3).

A fake NativeClient stands in for the helper: these tests pin what the
provider sends, what it makes of the answers, and when it must not ask the
helper at all. The helper itself is exercised against the shared fixture in
`tests/integration/test_native_accessibility.py` and in Rust.
"""

from __future__ import annotations

import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.config import NativeSpec, load_config  # noqa: E402
from pcbridge.desktop import runtime as runtimelib  # noqa: E402
from pcbridge.desktop import uitree as uitreelib  # noqa: E402
from pcbridge.desktop.backends import rust as rustlib  # noqa: E402
from pcbridge.desktop.backends.rust import RustAccessibilityProvider  # noqa: E402
from pcbridge.desktop.capabilities import CapabilityState  # noqa: E402
from pcbridge.desktop.errors import DesktopError, ErrorCategory, ErrorCode  # noqa: E402

DUMP = {
    "ok": True,
    "app": "pcbridge-editor",
    "app_bus": ":1.30",
    "app_pid": 3100,
    "same_name": 1,
    "scope": "window",
    "window": "Belge",
    "window_ref": "/org/pcbridge/Editor/a11y/w1",
    "truncated": False,
    "nodes": [
        {"path": [0, 0, 1], "ref": "/org/pcbridge/Editor/a11y/close-b", "role": "push button",
         "name": "Kapat", "states": ["enabled"], "actions": ["click"], "editable": False,
         "depth": 3},
        {"path": [0, 0, 2], "ref": "/org/pcbridge/Editor/a11y/field", "role": "text",
         "name": "Ad", "states": ["editable"], "actions": [], "editable": True, "depth": 3},
    ],
}
# What the helper returns since Task 6.3: its own snapshot id for the dump.
SNAPSHOT = "5a1e5a1e5a1e"
DUMP_WITH_SNAPSHOT = {
    **DUMP,
    "snapshot": SNAPSHOT,
    "nodes": [
        *DUMP["nodes"],
        {"path": [0, 0, 3], "ref": "/org/pcbridge/Editor/a11y/password", "role": "password text",
         "name": "Parola", "states": ["editable"], "actions": [], "editable": True, "depth": 3},
    ],
}
WINDOWS = {
    "ok": True,
    "windows": [
        {"app": "pcbridge-editor", "app_bus": ":1.30", "app_pid": 3100, "window": "Belge",
         "ref": "/org/pcbridge/Editor/a11y/w1", "index": 0, "role": "frame", "active": True,
         "children": 2},
        {"app": "gsd-color", "app_bus": ":1.5", "app_pid": 10, "window": "", "ref": "/x",
         "index": 0, "role": "window", "active": False, "children": 0},
    ],
}


class FakeClient:
    """Answers like the helper; records what it was asked."""

    def __init__(
        self,
        answers: dict | None = None,
        *,
        features=("accessibility.read", "accessibility.action"),
    ) -> None:
        self.answers = answers or {}
        self.sent: list[tuple[str, dict, float | None]] = []
        self.binaries: list[bytes] = []
        self.features = frozenset(features)
        self.closed = False

    def request(self, method, params=None, *, binary=b"", timeout=None):
        self.sent.append((method, dict(params or {}), timeout))
        self.binaries.append(binary)
        answer = self.answers.get(method)
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(result=answer, error=None, binary=b"")

    def close(self) -> None:
        self.closed = True


class Gate:
    def __init__(self, current=("g1", 3), last=None) -> None:
        self._current = current
        self._last = last

    @staticmethod
    def _token(pair):
        return None if pair is None else SimpleNamespace(grant_id=pair[0], revoke_epoch=pair[1])

    def current_token(self):
        return self._token(self._current)

    def last_token(self):
        return self._token(self._last or self._current)


def remote(code: ErrorCode, category: ErrorCategory, message: str = "yardimcinin sozu") -> DesktopError:
    return DesktopError(
        code=code,
        message=message,
        category=category,
        retryable=True,
        suggested_action="check_native_request",
        backend="pcbridge-native",
    )


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = dataclasses.replace(
            load_config(str(ROOT / "config.example.toml")), state_dir=Path(self.tmp.name)
        )

    def provider(self, client: FakeClient, gate: Gate | None = None) -> RustAccessibilityProvider:
        return RustAccessibilityProvider(
            self.cfg, gate=gate if gate is not None else Gate(), client_factory=lambda: client
        )


class ReadRequestTests(_Case):
    def test_dump_asks_the_helper_under_the_grant(self) -> None:
        client = FakeClient({"accessibility.dump": DUMP})
        provider = self.provider(client)
        dump = provider.dump(target="pcbridge-editor", interactive_only=False, max_nodes=50)
        method, params, timeout = client.sent[0]
        self.assertEqual(method, "accessibility.dump")
        self.assertEqual(
            params,
            {"grant_id": "g1", "revoke_epoch": 3, "target": "pcbridge-editor",
             "interactive_only": False, "max_nodes": 50},
        )
        self.assertEqual(timeout, rustlib.ACCESSIBILITY_TIMEOUT_SECONDS)
        self.assertIs(provider._last, dump)
        self.assertEqual(dump.backend, "linux.atspi.native")
        self.assertEqual((dump.app_bus, dump.app_pid, dump.scope), (":1.30", 3100, "window"))
        self.assertRegex(dump.snapshot, r"^[0-9a-f]{12}$")

    def test_the_short_ids_are_made_in_uitree(self) -> None:
        native = self.provider(FakeClient({"accessibility.dump": DUMP})).dump()
        parsed = uitreelib.dump_from_response(DUMP)
        self.assertEqual([n.node_id for n in native.nodes], [n.node_id for n in parsed.nodes])
        self.assertEqual([n.ref for n in native.nodes], [n.ref for n in parsed.nodes])

    def test_windows_and_focus_go_native_with_a_grant(self) -> None:
        client = FakeClient({
            "accessibility.windows": WINDOWS,
            "accessibility.focused": {"ok": True, "app": "pcbridge-editor", "window": "Belge"},
        })
        provider = self.provider(client)
        windows = provider.windows()
        self.assertEqual([w.app for w in windows], ["pcbridge-editor"], "untitled services drop")
        self.assertEqual(windows[0].ref, "/org/pcbridge/Editor/a11y/w1")
        self.assertEqual(provider.focused_window(), ("pcbridge-editor", "Belge"))
        self.assertEqual([m for m, _p, _t in client.sent], ["accessibility.windows", "accessibility.focused"])

    def test_an_expired_grant_lets_the_helper_say_why(self) -> None:
        # The last grant goes along, so the helper answers REVOKED or
        # EXPIRED itself instead of this side guessing.
        client = FakeClient({"accessibility.windows": WINDOWS})
        provider = self.provider(client, gate=Gate(current=None, last=("g0", 2)))
        provider.windows()
        self.assertEqual(client.sent[0][1], {"grant_id": "g0", "revoke_epoch": 2})

    def test_without_any_grant_windows_and_focus_are_refused_before_a_helper_starts(self) -> None:
        # There is no other reader: screen_info lists no windows before
        # desktop_unlock.
        created: list[FakeClient] = []

        def factory():
            created.append(FakeClient())
            return created[-1]

        provider = RustAccessibilityProvider(
            self.cfg, gate=SimpleNamespace(current_token=lambda: None, last_token=lambda: None),
            client_factory=factory,
        )
        for read in (provider.windows, provider.focused_window):
            with self.subTest(read=read.__name__):
                with self.assertRaises(DesktopError) as raised:
                    read()
                self.assertEqual(raised.exception.code, ErrorCode.GRANT_REQUIRED)
        self.assertEqual(created, [])

    def test_dump_without_any_grant_is_refused_before_a_helper_starts(self) -> None:
        created: list[FakeClient] = []

        def factory():
            created.append(FakeClient())
            return created[-1]

        provider = RustAccessibilityProvider(
            self.cfg, gate=SimpleNamespace(current_token=lambda: None, last_token=lambda: None),
            client_factory=factory,
        )
        with self.assertRaises(DesktopError) as raised:
            provider.dump()
        self.assertEqual(raised.exception.code, ErrorCode.GRANT_REQUIRED)
        self.assertEqual(created, [])

    def test_the_dump_snapshot_is_the_helpers(self) -> None:
        dump = self.provider(FakeClient({"accessibility.dump": DUMP_WITH_SNAPSHOT})).dump()
        self.assertEqual(dump.snapshot, SNAPSHOT)


ACTED = {"ok": True, "app": "pcbridge-editor", "ref": "/org/pcbridge/Editor/a11y/close-b",
         "role": "push button", "name": "Kapat", "action": "click", "resolved_by": "path",
         "returned": True}
WRITTEN = {"ok": True, "app": "pcbridge-editor", "ref": "/org/pcbridge/Editor/a11y/field",
           "role": "text", "name": "Ad", "replaced_chars": 0, "now_chars": 5,
           "resolved_by": "path", "verified": True}


class ActionRequestTests(_Case):
    """Task 6.3: clicks and text writes go to the helper that made the dump."""

    def acting(self, answers: dict, **kwargs) -> tuple[RustAccessibilityProvider, FakeClient]:
        client = FakeClient({"accessibility.dump": DUMP_WITH_SNAPSHOT, **answers}, **kwargs)
        provider = self.provider(client)
        provider.dump()
        return provider, client

    def test_a_click_names_the_helpers_dump_and_node_only(self) -> None:
        provider, client = self.acting({"accessibility.act": ACTED})
        button = provider._last.nodes[0]
        result = provider.click(button.node_id)
        method, params, timeout = client.sent[-1]
        self.assertEqual(method, "accessibility.act")
        self.assertEqual(
            params,
            {"grant_id": "g1", "revoke_epoch": 3, "snapshot": SNAPSHOT,
             "ref": "/org/pcbridge/Editor/a11y/close-b", "action": "click"},
        )
        self.assertEqual(timeout, rustlib.ACCESSIBILITY_TIMEOUT_SECONDS)
        self.assertEqual(client.binaries[-1], b"")
        self.assertEqual(result["snapshot"], SNAPSHOT)
        self.assertEqual(result["resolved_by"], "path")

    def test_the_text_travels_as_the_binary_payload_not_in_the_header(self) -> None:
        provider, client = self.acting({"accessibility.set_text": WRITTEN})
        field = provider._last.nodes[1]
        text = "Çağrı ğüşıöç İĞÜŞÖÇ — 1"
        result = provider.set_text(field.node_id, text)
        method, params, _timeout = client.sent[-1]
        self.assertEqual(method, "accessibility.set_text")
        self.assertEqual(
            params,
            {"grant_id": "g1", "revoke_epoch": 3, "snapshot": SNAPSHOT,
             "ref": "/org/pcbridge/Editor/a11y/field"},
        )
        self.assertEqual(client.binaries[-1], text.encode("utf-8"))
        self.assertNotIn(text, repr(params))
        self.assertEqual(result["snapshot"], SNAPSHOT)

    def test_a_password_field_never_reaches_the_helper(self) -> None:
        provider, client = self.acting({})
        password = provider._last.nodes[2]
        with self.assertRaises(DesktopError) as raised:
            provider.set_text(password.node_id, "hunter2")
        self.assertEqual(raised.exception.code, ErrorCode.PASSWORD_FIELD)
        self.assertEqual([m for m, _p, _t in client.sent], ["accessibility.dump"])

    def test_an_element_without_action_never_reaches_the_helper(self) -> None:
        provider, client = self.acting({})
        field = provider._last.nodes[1]
        with self.assertRaises(DesktopError) as raised:
            provider.click(field.node_id)
        self.assertEqual(raised.exception.code, ErrorCode.ACTION_UNSUPPORTED)
        self.assertEqual([m for m, _p, _t in client.sent], ["accessibility.dump"])

    def test_an_unanswered_action_is_unknown_and_never_sent_again(self) -> None:
        lost = DesktopError(
            code=ErrorCode.TIMEOUT,
            message="A native helper request timed out.",
            category=ErrorCategory.EXECUTION,
            retryable=False,
            suggested_action="inspect_native_status_before_retry",
            backend="pcbridge-native",
            execution_state="unknown",
        )
        for method, run in (
            ("accessibility.act", lambda p: p.click(p._last.nodes[0].node_id)),
            ("accessibility.set_text", lambda p: p.set_text(p._last.nodes[1].node_id, "x")),
        ):
            with self.subTest(method=method):
                provider, client = self.acting({method: lost})
                with self.assertRaises(DesktopError) as raised:
                    run(provider)
                error = raised.exception
                self.assertEqual(error.code, ErrorCode.EXECUTION_UNKNOWN)
                self.assertEqual(error.category, ErrorCategory.EXECUTION)
                self.assertEqual(error.execution_state, "unknown")
                self.assertFalse(error.retryable)
                self.assertEqual([m for m, _p, _t in client.sent].count(method), 1)

    def test_a_request_that_never_left_is_not_called_unknown(self) -> None:
        unusable = DesktopError(
            code=ErrorCode.NATIVE_CRASHED,
            message="Native helper kullanilabilir degil.",
            category=ErrorCategory.IPC,
            retryable=False,
            suggested_action="retry_read_only_native_request",
            backend="pcbridge-native",
            execution_state="not_started",
        )
        provider, _client = self.acting({"accessibility.act": unusable})
        with self.assertRaises(DesktopError) as raised:
            provider.click(provider._last.nodes[0].node_id)
        self.assertIs(raised.exception, unusable)

    def test_helper_verdicts_on_actions_keep_their_codes(self) -> None:
        cases = {
            ErrorCode.ELEMENT_STALE: (ErrorCategory.ACCESSIBILITY, True),
            ErrorCode.TARGET_MISMATCH: (ErrorCategory.ACCESSIBILITY, True),
            ErrorCode.ACTION_UNSUPPORTED: (ErrorCategory.ACCESSIBILITY, False),
            ErrorCode.TEXT_MISMATCH: (ErrorCategory.ACCESSIBILITY, False),
            ErrorCode.EXECUTION_UNKNOWN: (ErrorCategory.EXECUTION, False),
        }
        for code, (category, retryable) in cases.items():
            with self.subTest(code=code):
                provider, _client = self.acting(
                    {"accessibility.set_text": remote(code, category, "yardimcinin sozu")}
                )
                with self.assertRaises(DesktopError) as raised:
                    provider.set_text(provider._last.nodes[1].node_id, "x")
                error = raised.exception
                self.assertEqual(error.code, code)
                self.assertEqual(error.message, "yardimcinin sozu")
                self.assertEqual(error.category, category)
                self.assertEqual(error.retryable, retryable)
                self.assertEqual(error.backend, "linux.atspi.native")
                self.assertNotEqual(error.suggested_action, "check_native_request")

    def test_a_helper_without_actions_says_how_to_fix_it(self) -> None:
        provider, _client = self.acting(
            {"accessibility.act": remote(ErrorCode.UNSUPPORTED, ErrorCategory.CAPABILITY)},
            features=("accessibility.read",),
        )
        with self.assertRaises(DesktopError) as raised:
            provider.click(provider._last.nodes[0].node_id)
        self.assertEqual(raised.exception.code, ErrorCode.BACKEND_UNAVAILABLE)
        self.assertIn("build-native.sh", raised.exception.message)


class ErrorTests(_Case):
    def test_target_verdicts_get_their_own_advice(self) -> None:
        for code in (ErrorCode.ELEMENT_AMBIGUOUS, ErrorCode.TARGET_MISMATCH, ErrorCode.TIMEOUT):
            with self.subTest(code=code):
                category = (
                    ErrorCategory.EXECUTION if code is ErrorCode.TIMEOUT else ErrorCategory.ACCESSIBILITY
                )
                client = FakeClient({"accessibility.dump": remote(code, category, "neden")})
                with self.assertRaises(DesktopError) as raised:
                    self.provider(client).dump(target="gnome-te")
                error = raised.exception
                self.assertEqual(error.code, code)
                self.assertEqual(error.message, "neden")
                self.assertEqual(error.category, category)
                self.assertNotEqual(error.suggested_action, "check_native_request")

    def test_safety_refusals_pass_through_unchanged(self) -> None:
        refusal = remote(ErrorCode.REVOKED, ErrorCategory.SAFETY)
        client = FakeClient({"accessibility.dump": refusal})
        with self.assertRaises(DesktopError) as raised:
            self.provider(client).dump()
        self.assertIs(raised.exception, refusal)

    def test_a_helper_from_before_this_task_says_how_to_fix_it(self) -> None:
        old = FakeClient(
            {"accessibility.dump": remote(ErrorCode.UNSUPPORTED, ErrorCategory.CAPABILITY)},
            features=("clipboard",),
        )
        with self.assertRaises(DesktopError) as raised:
            self.provider(old).dump()
        self.assertEqual(raised.exception.code, ErrorCode.BACKEND_UNAVAILABLE)
        self.assertIn("build-native.sh", raised.exception.message)

    def test_a_malformed_answer_is_not_turned_into_an_empty_screen(self) -> None:
        client = FakeClient({"accessibility.dump": {"nodes": []}})
        with self.assertRaises(DesktopError) as raised:
            self.provider(client).dump()
        self.assertEqual(raised.exception.code, ErrorCode.INVALID_FRAME)


class LifecycleTests(_Case):
    def test_a_new_grant_gets_a_new_helper(self) -> None:
        created: list[FakeClient] = []

        def factory():
            created.append(FakeClient({"accessibility.windows": WINDOWS}))
            return created[-1]

        gate = Gate(current=("g1", 1))
        provider = RustAccessibilityProvider(self.cfg, gate=gate, client_factory=factory)
        provider.windows()
        gate._current = ("g2", 1)
        provider.windows()
        self.assertEqual(len(created), 2)
        self.assertTrue(created[0].closed)
        self.assertEqual(created[1].sent[0][1]["grant_id"], "g2")

    def test_releasing_desktop_resources_closes_the_reader(self) -> None:
        client = FakeClient({"accessibility.windows": WINDOWS})
        provider = self.provider(client)
        provider.windows()
        runtime = runtimelib.create_runtime(
            self.cfg,
            gate=Gate(),
            capture_provider=mock.Mock(),
            input_provider=mock.Mock(),
            accessibility_provider=provider,
        )
        runtime.release_resources()
        self.assertTrue(client.closed)


class SelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = dataclasses.replace(
            load_config(str(ROOT / "config.example.toml")), state_dir=Path(self.tmp.name)
        )

    def select(self, choice: str, ready: tuple[bool, str] = (True, "")):
        cfg = dataclasses.replace(self.cfg, native=dataclasses.replace(self.cfg.native, accessibility=choice))
        with mock.patch.object(rustlib, "native_binary_ready", return_value=ready):
            return runtimelib.select_accessibility_provider(cfg, Gate())

    def test_auto_is_the_default(self) -> None:
        self.assertEqual(self.cfg.native.accessibility, "auto")
        self.assertEqual(NativeSpec().accessibility, "auto")

    def test_a_config_without_the_setting_gets_auto(self) -> None:
        path = Path(self.tmp.name) / "config.toml"
        text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
        without = text.replace('accessibility = "auto"\n', "")
        self.assertNotEqual(without, text, "the example must carry the setting")
        path.write_text(without, encoding="utf-8")
        self.assertEqual(load_config(str(path)).native.accessibility, "auto")

    def test_the_native_reader_is_the_only_one(self) -> None:
        # With or without a helper: there is no other reader to fall back on.
        for choice in ("auto", "rust"):
            for ready in ((True, ""), (False, "no helper")):
                with self.subTest(choice=choice, ready=ready[0]):
                    self.assertIs(type(self.select(choice, ready)), RustAccessibilityProvider)

    def test_without_a_helper_the_capabilities_say_why(self) -> None:
        provider = RustAccessibilityProvider(self.cfg, gate=Gate(), client_factory=FakeClient)
        with mock.patch.object(rustlib, "native_binary_ready", return_value=(False, "no helper here")):
            values = provider.probe_capabilities()
        for name in ("accessibility.read", "accessibility.action", "window.list"):
            with self.subTest(name=name):
                self.assertIs(values[name].state, CapabilityState.UNAVAILABLE)
                self.assertEqual(values[name].reason_code, ErrorCode.DEPENDENCY_MISSING)
                self.assertIn("no helper here", values[name].limitations)

    def test_unknown_value_is_refused_at_load(self) -> None:
        path = Path(self.tmp.name) / "config.toml"
        text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
        swapped = text.replace('accessibility = "auto"', 'accessibility = "gtk"')
        self.assertNotEqual(swapped, text, "the example must carry the setting")
        path.write_text(swapped, encoding="utf-8")
        with self.assertRaises(SystemExit) as raised:
            load_config(str(path))
        self.assertIn("accessibility", str(raised.exception))

    def test_native_capabilities_name_the_native_backend(self) -> None:
        provider = RustAccessibilityProvider(self.cfg, gate=Gate(), client_factory=FakeClient)
        with mock.patch.object(rustlib, "native_binary_ready", return_value=(True, "")):
            values = provider.probe_capabilities()
        self.assertEqual(values["accessibility.read"].backend, "linux.atspi.native")
        self.assertIs(values["accessibility.read"].state, CapabilityState.SUPPORTED)
        self.assertIs(values["window.list"].state, CapabilityState.DEGRADED)
        # Task 6.3: actions go to the same helper.
        self.assertEqual(values["accessibility.action"].backend, "linux.atspi.native")
        self.assertIs(values["accessibility.action"].state, CapabilityState.SUPPORTED)


if __name__ == "__main__":
    unittest.main()
