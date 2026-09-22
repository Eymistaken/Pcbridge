#!/usr/bin/env python3
"""Both accessibility helpers, one fixture (Tasks 6.2, 6.3).

The Rust helper runs in `--test-mode`: its tree is a fixture desktop named by
`PCBRIDGE_TEST_A11Y_FIXTURE` and `PCBRIDGE_TEST_A11Y_DESKTOP`, never the
user's applications. Every dump and window case of the shared fixture goes
through `RustAccessibilityProvider` -> NativeClient -> the helper, and the
answer must equal both the fixture's expectation and what the Python helper
produces from the same desktop: node for node, id for id.

Every action case goes through both providers the way `ui_click` and
`ui_set_text` call them. Both must refuse or act alike, with the same words,
and leave the fake desktops in the same state.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop import uitree as uitreelib  # noqa: E402
from pcbridge.desktop.backends.python import PythonAccessibilityProvider  # noqa: E402
from pcbridge.desktop.backends.rust import RustAccessibilityProvider  # noqa: E402
from pcbridge.desktop.errors import DesktopError, ErrorCode  # noqa: E402
from pcbridge.desktop.safety import SafetyGate  # noqa: E402
from pcbridge.native.client import NativeClient  # noqa: E402

FIXTURE_PATH = ROOT / "tests" / "fixtures" / "native" / "accessibility_cases.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _python_reader():
    """The Python helper's code on the same fixture, from the contract test."""
    spec = importlib.util.spec_from_file_location(
        "accessibility_contract_for_parity",
        ROOT / "tests" / "contracts" / "test_accessibility_contract.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTRACT = _python_reader()

# Everything a reader decides; `depth` included, the snapshot excluded (it
# is a fresh random id per dump by design).
NODE_FIELDS = ("node_id", "ref", "path", "role", "name", "states", "actions", "editable", "depth")
DUMP_FIELDS = ("app", "window", "truncated", "app_bus", "app_pid", "scope", "window_ref", "same_name")


def summary(dump: uitreelib.Dump) -> dict:
    return {
        **{field: getattr(dump, field) for field in DUMP_FIELDS},
        "nodes": [{field: getattr(node, field) for field in NODE_FIELDS} for node in dump.nodes],
    }


@unittest.skipUnless(sys.platform.startswith("linux"), "native helper is built for Linux")
class NativeAccessibilityEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not (ROOT / "rust" / "Cargo.toml").is_file():
            raise unittest.SkipTest("builds the native test harness; needs the rust/ workspace of a git checkout")
        completed = subprocess.run(
            ["cargo", "build", "-p", "pcbridge-native", "--features", "test-harness"],
            cwd=ROOT / "rust",
            capture_output=True,
            text=True,
            timeout=600,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr)
        cls.native_binary = ROOT / "rust/target/debug/pcbridge-native"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        wrapper = self.root / "native-test-mode"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{self.native_binary}" --test-mode "$@"\n', encoding="utf-8"
        )
        wrapper.chmod(0o755)
        self.cfg = dataclasses.replace(
            load_config(str(ROOT / "config.example.toml")), state_dir=self.root / "state"
        )
        self.gate = SafetyGate(self.cfg)
        self.gate.unlock(5, reason="accessibility end to end")

        def helper() -> NativeClient:
            return NativeClient(
                wrapper,
                state_dir=self.cfg.state_dir,
                runtime_dir=self.root / "runtime",
                environment={
                    "PCBRIDGE_TEST_A11Y_FIXTURE": str(FIXTURE_PATH),
                    "PCBRIDGE_TEST_A11Y_DESKTOP": "editor",
                },
            )

        self.helper = helper
        self.native = RustAccessibilityProvider(self.cfg, gate=self.gate, client_factory=helper)
        self.addCleanup(self.native.close)
        # The Python reader: the real helper code on a fake Atspi, in process.
        self.atspi = CONTRACT.FakeAtspi()
        for patcher in (
            mock.patch.object(CONTRACT.HELPER, "Atspi", self.atspi),
            mock.patch.object(uitreelib, "_call", self._python_call),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.python = uitreelib.UiTree()
        self.python_provider = PythonAccessibilityProvider()

    @staticmethod
    def _python_call(payload: dict, timeout: int) -> dict:
        wire = json.loads(json.dumps(payload, ensure_ascii=False))
        return json.loads(json.dumps(CONTRACT.HELPER.handle(wire), ensure_ascii=False))

    def use(self, desktop: str) -> None:
        """Point both readers at one fixture desktop."""
        self.atspi.use(desktop)
        client = self.native._helper.for_grant(self.native._dump_grant())
        response = client.request("test.accessibility_desktop", {"desktop": desktop}, timeout=5.0)
        self.assertEqual(response.result, {"desktop": desktop})

    def native_desktop(self) -> dict:
        """What took effect on the helper's fixture desktop, and its texts."""
        client = self.native._helper.for_grant(self.native._dump_grant())
        return client.request("test.accessibility_performed", {}, timeout=5.0).result

    @staticmethod
    def act(provider, node_id: str, act: dict):
        if act["cmd"] == "act":
            return lambda: provider.click(node_id, act.get("action", "click"))
        return lambda: provider.set_text(node_id, act["text"])

    def test_dump_cases_read_the_same_through_both_helpers(self) -> None:
        for case in FIXTURE["dump_cases"]:
            with self.subTest(case=case["name"]):
                self.use(case["desktop"])
                request = case["request"]
                args = {
                    "target": request["target"],
                    "interactive_only": request.get("interactive_only", True),
                    "max_nodes": request.get("max_nodes", 400),
                }
                expect = case["expect"]
                if not expect["ok"]:
                    with self.assertRaises(DesktopError) as native_error:
                        self.native.dump(**args)
                    with self.assertRaises(uitreelib.UiTreeError) as python_error:
                        self.python.dump(**args)
                    if "code" in expect:
                        self.assertEqual(native_error.exception.code.value, expect["code"])
                        self.assertEqual(python_error.exception.code.value, expect["code"])
                    self.assertEqual(native_error.exception.message, str(python_error.exception))
                    continue
                native = self.native.dump(**args)
                python = self.python.dump(**args)
                self.assertEqual(summary(native), summary(python))
                for key, value in expect.items():
                    if key not in ("ok", "nodes"):
                        self.assertEqual(getattr(native, key), value, key)
                if "nodes" in expect:
                    got = [
                        {"ref": n.ref, "path": n.path, "role": n.role, "name": n.name,
                         "actions": n.actions, "editable": n.editable}
                        for n in native.nodes
                    ]
                    want = [{key: node[key] for key in got[0]} for node in expect["nodes"]] if got else []
                    self.assertEqual(got, want)

    def test_window_cases_read_the_same_through_both_helpers(self) -> None:
        for case in FIXTURE["window_cases"]:
            with self.subTest(case=case["name"]):
                self.use(case["desktop"])
                self.assertEqual(self.native.windows(), self.python.windows())
                expect = case["expect_focused"]
                if expect["ok"]:
                    self.assertEqual(self.native.focused_window(), (expect["app"], expect["window"]))
                    self.assertEqual(self.python.focused_window(), (expect["app"], expect["window"]))
                else:
                    with self.assertRaises(DesktopError) as raised:
                        self.native.focused_window()
                    self.assertEqual(raised.exception.code.value, expect["code"])

    def test_action_cases_act_the_same_through_both_helpers(self) -> None:
        ran = 0
        for case in FIXTURE["action_cases"]:
            if "override" in case["act"]:
                continue  # a hand-made request; no provider sends one
            with self.subTest(case=case["name"]):
                request = case["dump"]["request"]
                args = {
                    "target": request["target"],
                    "interactive_only": request.get("interactive_only", True),
                    "max_nodes": request.get("max_nodes", 400),
                }
                self.use(case["dump"]["desktop"])
                native_dump = self.native.dump(**args)
                python_dump = self.python_provider.dump(**args)
                ref = case["act"]["ref"]
                native_node = next(n for n in native_dump.nodes if n.ref == ref)
                python_node = next(n for n in python_dump.nodes if n.ref == ref)
                self.assertEqual(native_node.node_id, python_node.node_id)
                self.use(case["then"])
                native_run = self.act(self.native, native_node.node_id, case["act"])
                python_run = self.act(self.python_provider, python_node.node_id, case["act"])
                expect = case["expect"]
                if expect["ok"]:
                    native = native_run()
                    python = python_run()
                    for key in ("app", "ref", "role", "name", "resolved_by", "action",
                                "replaced_chars", "now_chars", "verified"):
                        self.assertEqual(native.get(key), python.get(key), key)
                        if key in expect:
                            self.assertEqual(native[key], expect[key], key)
                    self.assertEqual(native["snapshot"], native_dump.snapshot)
                else:
                    with self.assertRaises(DesktopError) as native_error:
                        native_run()
                    with self.assertRaises(DesktopError) as python_error:
                        python_run()
                    self.assertEqual(native_error.exception.code.value, expect["code"])
                    self.assertEqual(python_error.exception.code.value, expect["code"])
                    self.assertEqual(native_error.exception.message, python_error.exception.message)
                    self.assertEqual(native_error.exception.category, python_error.exception.category)
                    self.assertEqual(native_error.exception.retryable, python_error.exception.retryable)
                state = self.native_desktop()
                self.assertEqual(state["performed"], expect["performed"])
                self.assertEqual(self.atspi.desktop.performed, expect["performed"])
                for path, text in expect.get("text_after", {}).items():
                    self.assertEqual(state["texts"].get(path), text)
                ran += 1
        self.assertGreaterEqual(ran, 20)

    def test_a_dump_of_another_helper_is_refused(self) -> None:
        # Two processes, two helpers: the id of one names nothing in the other.
        self.use("editor")
        dump = self.native.dump()
        other = RustAccessibilityProvider(self.cfg, gate=self.gate, client_factory=self.helper)
        self.addCleanup(other.close)
        # The other helper has a dump of its own listing the same button;
        # only the snapshot tells the two apart.
        other.dump()
        other._last = dump
        button = next(n for n in dump.nodes if n.name == "Tamam")
        with self.assertRaises(DesktopError) as raised:
            other.click(button.node_id)
        self.assertEqual(raised.exception.code, ErrorCode.ELEMENT_STALE)
        self.assertIn("ui_dump", raised.exception.message)
        self.assertEqual(self.native_desktop()["performed"], [])
        # Its own helper still acts on it.
        self.assertEqual(self.native.click(button.node_id)["name"], "Tamam")

    def test_a_new_grant_forgets_the_dumps_of_the_old_one(self) -> None:
        self.use("editor")
        dump = self.native.dump()
        button = next(n for n in dump.nodes if n.name == "Tamam")
        self.gate.lock()
        self.gate.unlock(5, reason="a second grant")
        with self.assertRaises(DesktopError) as raised:
            self.native.click(button.node_id)
        self.assertEqual(raised.exception.code, ErrorCode.ELEMENT_STALE)
        self.assertIn("ui_dump", raised.exception.message)
        # Refused from the record alone: the new helper never even connected
        # to a desktop, so it cannot have acted on one.
        with self.assertRaises(DesktopError):
            self.native_desktop()
        self.use("editor")
        again = self.native.dump()
        self.native.click(next(n for n in again.nodes if n.name == "Tamam").node_id)
        self.assertEqual(
            self.native_desktop()["performed"], [["/org/pcbridge/Editor/a11y/ok", "click"]]
        )

    def test_a_revoked_grant_gets_no_tree(self) -> None:
        self.use("editor")
        self.native.dump()
        self.gate.lock()
        with self.assertRaises(DesktopError) as raised:
            self.native.dump()
        self.assertEqual(raised.exception.category.value, "safety")


if __name__ == "__main__":
    unittest.main()
