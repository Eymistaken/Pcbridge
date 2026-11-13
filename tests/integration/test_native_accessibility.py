#!/usr/bin/env python3
"""The accessibility helper end to end, on the shared fixture (Tasks 6.2, 6.3).

The Rust helper runs in `--test-mode`: its tree is a fixture desktop named by
`PCBRIDGE_TEST_A11Y_FIXTURE` and `PCBRIDGE_TEST_A11Y_DESKTOP`, never the
user's applications. Every dump and window case of the shared fixture goes
through `RustAccessibilityProvider` -> NativeClient -> the helper, and the
answer must equal the fixture's expectation, node for node. The fixture was
written for the Python AT-SPI helper (removed after 2.8), so the native
reader is still held to what that helper read.

Every action case goes through the provider the way `ui_click` and
`ui_set_text` call it, and must refuse or act as the fixture says, leaving
the fake desktop in the state it names.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop import uitree as uitreelib  # noqa: E402
from pcbridge.desktop.backends.rust import RustAccessibilityProvider  # noqa: E402
from pcbridge.desktop.errors import DesktopError, ErrorCode  # noqa: E402
from pcbridge.desktop.safety import SafetyGate  # noqa: E402
from pcbridge.native.client import NativeClient  # noqa: E402

FIXTURE_PATH = ROOT / "tests" / "fixtures" / "native" / "accessibility_cases.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


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

    def use(self, desktop: str) -> None:
        """Point the helper at one fixture desktop."""
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

    def test_dump_cases_read_what_the_fixture_expects(self) -> None:
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
                    if "code" in expect:
                        self.assertEqual(native_error.exception.code.value, expect["code"])
                    continue
                native = self.native.dump(**args)
                for key, value in expect.items():
                    if key not in ("ok", "nodes"):
                        self.assertEqual(getattr(native, key), value, key)
                if "nodes" in expect:
                    self.assertEqual(len(native.nodes), len(expect["nodes"]))
                    for node, want in zip(native.nodes, expect["nodes"]):
                        for key, value in want.items():
                            self.assertEqual(getattr(node, key), value, (want["ref"], key))
                # The ids are made from the read nodes, as for any dump.
                again = uitreelib.dump_from_response({
                    "ok": True, "nodes": [
                        {"path": n.path, "role": n.role, "name": n.name} for n in native.nodes
                    ],
                })
                self.assertEqual([n.node_id for n in native.nodes],
                                 [n.node_id for n in again.nodes])

    def test_window_cases_read_what_the_fixture_expects(self) -> None:
        for case in FIXTURE["window_cases"]:
            with self.subTest(case=case["name"]):
                self.use(case["desktop"])
                self.assertEqual(
                    self.native.windows(),
                    uitreelib.windows_from_response({"windows": case["expect_windows"]}),
                )
                expect = case["expect_focused"]
                if expect["ok"]:
                    self.assertEqual(self.native.focused_window(), (expect["app"], expect["window"]))
                else:
                    with self.assertRaises(DesktopError) as raised:
                        self.native.focused_window()
                    self.assertEqual(raised.exception.code.value, expect["code"])

    def test_action_cases_act_as_the_fixture_expects(self) -> None:
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
                ref = case["act"]["ref"]
                native_node = next(n for n in native_dump.nodes if n.ref == ref)
                self.use(case["then"])
                native_run = self.act(self.native, native_node.node_id, case["act"])
                expect = case["expect"]
                if expect["ok"]:
                    native = native_run()
                    for key in ("resolved_by", "replaced_chars", "now_chars", "verified"):
                        if key in expect:
                            self.assertEqual(native[key], expect[key], key)
                    self.assertEqual(native["ref"], ref)
                    self.assertEqual(native["snapshot"], native_dump.snapshot)
                else:
                    with self.assertRaises(DesktopError) as native_error:
                        native_run()
                    self.assertEqual(native_error.exception.code.value, expect["code"])
                state = self.native_desktop()
                self.assertEqual(state["performed"], expect["performed"])
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
