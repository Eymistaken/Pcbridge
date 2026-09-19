#!/usr/bin/env python3
"""The native accessibility reader behind the provider contract (Task 6.2).

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

from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop import runtime as runtimelib  # noqa: E402
from pcbridge.desktop import uitree as uitreelib  # noqa: E402
from pcbridge.desktop.backends import rust as rustlib  # noqa: E402
from pcbridge.desktop.backends.python import PythonAccessibilityProvider  # noqa: E402
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

    def __init__(self, answers: dict | None = None, *, features=("accessibility.read",)) -> None:
        self.answers = answers or {}
        self.sent: list[tuple[str, dict, float | None]] = []
        self.features = frozenset(features)
        self.closed = False

    def request(self, method, params=None, *, binary=b"", timeout=None):
        self.sent.append((method, dict(params or {}), timeout))
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
        # The Python helper is never started here; a call to it is a failure
        # unless a test says otherwise.
        self.python_calls: list[dict] = []

        def python_helper(payload, timeout):
            self.python_calls.append(payload)
            if payload["cmd"] == "windows":
                return {"ok": True, "windows": []}
            if payload["cmd"] == "dump":
                return {"ok": True, "app": "python-side", "window": "p", "nodes": []}
            return {"ok": True, "role": payload.get("role"), "name": payload.get("name"),
                    "resolved_by": "path", "replaced_chars": 0}

        patcher = mock.patch.object(uitreelib, "_call", python_helper)
        patcher.start()
        self.addCleanup(patcher.stop)

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
        self.assertEqual(self.python_calls, [], "a native dump must not start the GI helper")
        self.assertIs(provider._last, dump)
        self.assertEqual(dump.backend, "linux.atspi.native")
        self.assertEqual((dump.app_bus, dump.app_pid, dump.scope), (":1.30", 3100, "window"))
        self.assertRegex(dump.snapshot, r"^[0-9a-f]{12}$")

    def test_ids_are_the_same_whichever_reader_produced_the_dump(self) -> None:
        native = self.provider(FakeClient({"accessibility.dump": DUMP})).dump()
        python = uitreelib.dump_from_response(DUMP)
        self.assertEqual([n.node_id for n in native.nodes], [n.node_id for n in python.nodes])
        self.assertEqual([n.ref for n in native.nodes], [n.ref for n in python.nodes])

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
        self.assertEqual(self.python_calls, [])

    def test_without_a_grant_windows_and_focus_read_what_they_read_before(self) -> None:
        # screen_info lists windows before desktop_unlock; no helper exists then.
        client = FakeClient()
        provider = self.provider(client, gate=Gate(current=None))
        provider.windows()
        provider.focused_window()
        self.assertEqual(client.sent, [])
        self.assertEqual([c["cmd"] for c in self.python_calls], ["windows", "dump"])

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

    def test_actions_still_go_through_the_python_helper_with_the_identity(self) -> None:
        provider = self.provider(FakeClient({"accessibility.dump": DUMP}))
        dump = provider.dump()
        button = dump.nodes[0]
        provider.click(button.node_id)
        sent = self.python_calls[-1]
        self.assertEqual(sent["cmd"], "act")
        self.assertEqual((sent["app_bus"], sent["ref"]), (":1.30", "/org/pcbridge/Editor/a11y/close-b"))
        self.assertEqual((sent["scope"], sent["window_ref"]), ("window", "/org/pcbridge/Editor/a11y/w1"))


class ErrorTests(_Case):
    def test_target_verdicts_get_the_python_readers_advice(self) -> None:
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

    def test_python_is_the_default_until_gate_6(self) -> None:
        self.assertEqual(self.cfg.native.accessibility, "python")
        provider = self.select("python")
        self.assertIs(type(provider), PythonAccessibilityProvider)

    def test_auto_takes_the_native_reader_when_it_is_there(self) -> None:
        self.assertIsInstance(self.select("auto"), RustAccessibilityProvider)

    def test_auto_falls_back_visibly(self) -> None:
        provider = self.select("auto", ready=(False, "yardimci yok"))
        self.assertIs(type(provider), PythonAccessibilityProvider)
        with mock.patch.object(PythonAccessibilityProvider, "_availability", return_value=(True, "", None)):
            values = provider.probe_capabilities()
        read = values["accessibility.read"]
        self.assertIs(read.state, CapabilityState.DEGRADED)
        self.assertTrue(any("yardimci yok" in item for item in read.limitations))

    def test_rust_never_falls_back(self) -> None:
        self.assertIsInstance(self.select("rust", ready=(False, "yok")), RustAccessibilityProvider)

    def test_unknown_value_is_refused_at_load(self) -> None:
        path = Path(self.tmp.name) / "config.toml"
        text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
        swapped = text.replace('accessibility = "python"', 'accessibility = "gtk"')
        self.assertNotEqual(swapped, text, "the example must carry the setting")
        path.write_text(swapped, encoding="utf-8")
        with self.assertRaises(SystemExit) as raised:
            load_config(str(path))
        self.assertIn("accessibility", str(raised.exception))

    def test_native_capabilities_name_the_native_backend(self) -> None:
        provider = RustAccessibilityProvider(self.cfg, gate=Gate(), client_factory=FakeClient)
        with mock.patch.object(rustlib, "native_binary_ready", return_value=(True, "")), \
                mock.patch.object(PythonAccessibilityProvider, "_availability", return_value=(True, "", None)):
            values = provider.probe_capabilities()
        self.assertEqual(values["accessibility.read"].backend, "linux.atspi.native")
        self.assertIs(values["accessibility.read"].state, CapabilityState.SUPPORTED)
        self.assertIs(values["window.list"].state, CapabilityState.DEGRADED)
        # Actions are the Python helper's until Task 6.3.
        self.assertEqual(values["accessibility.action"].backend, "linux.atspi")


if __name__ == "__main__":
    unittest.main()
