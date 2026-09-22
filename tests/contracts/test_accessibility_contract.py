#!/usr/bin/env python3
"""Accessibility target identity (Task 6.1), on a fake AT-SPI desktop.

`tests/fixtures/native/accessibility_cases.json` describes the desktops and the
expected outcomes. The real `atspi_helper.py` code runs against them in this
process: its `Atspi` module is swapped for a fake built from the fixture, so no
accessibility bus, no window and no real application is involved. The same
cases then run once more through `UiTree` and the Python provider, the way the
MCP tools call them.

Nothing here sends input or reads a real tree; it is safe with every live-test
flag unset.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.desktop import uitree as uitreelib  # noqa: E402
from pcbridge.desktop.backends.python import PythonAccessibilityProvider  # noqa: E402
from pcbridge.desktop.errors import DesktopError, ErrorCategory, ErrorCode  # noqa: E402

FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "native" / "accessibility_cases.json").read_text(
        encoding="utf-8"
    )
)


def _load_helper():
    # Load the helper file the imported package ships, so the test also
    # works against an installed pcbridge.
    origin = importlib.util.find_spec("pcbridge.desktop.atspi_helper").origin
    spec = importlib.util.spec_from_file_location("atspi_helper_contract", origin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPER = _load_helper()
# The real transport, kept before any test swaps it for the in-process helper.
REAL_CALL = uitreelib._call


# ------------------------------------------------------------ the fake desktop
class _StateType:
    """`Atspi.StateType.ACTIVE` -> "active", the fixture's spelling."""

    def __getattr__(self, name: str) -> str:
        return name.lower()


class _StateSet:
    def __init__(self, states: list[str]) -> None:
        self._states = frozenset(states)

    def contains(self, state: str) -> bool:
        return state in self._states


class _Application:
    def __init__(self, bus_name: str) -> None:
        self.bus_name = bus_name


class _Action:
    def __init__(self, node: "_Node") -> None:
        self.node = node

    def get_n_actions(self) -> int:
        return len(self.node.spec.get("actions", []))

    def get_action_name(self, index: int) -> str:
        return self.node.spec["actions"][index]

    def do_action(self, index: int) -> bool:
        # GTK4 answers false for an insensitive widget and does nothing.
        if self.node.spec.get("refuses_actions"):
            return False
        self.node.desktop.performed.append([self.node.path, self.get_action_name(index)])
        return True


class _Text:
    """`Atspi.Text`: its methods take the node, as GI's interface methods do."""

    @staticmethod
    def get_character_count(node: "_Node") -> int:
        return len(node.text)

    @staticmethod
    def get_text(node: "_Node", start: int, end: int) -> str:
        # GTK4 answers an end of -1 with "" (measured 2026-09-19); so does
        # this fake, so no reader can lean on it.
        return node.text[start:end] if end >= 0 else ""


class _EditableText:
    def __init__(self, node: "_Node") -> None:
        self.node = node

    def _touch(self) -> None:
        entry = [self.node.path, "settext"]
        if entry not in self.node.desktop.performed:
            self.node.desktop.performed.append(entry)

    def delete_text(self, start: int, end: int) -> bool:
        if self.node.spec.get("read_only"):
            return False
        self._touch()
        self.node.text = self.node.text[:start] + self.node.text[end:]
        return True

    def insert_text(self, position: int, text: str, length: int) -> bool:
        if self.node.spec.get("read_only"):
            return False
        # The GI binding takes the length in BYTES (measured 2026-08-02): a
        # character count cuts Turkish text short, and so does this fake.
        self._touch()
        piece = text.encode("utf-8")[:length].decode("utf-8", "ignore")
        self.node.text = self.node.text[:position] + piece + self.node.text[position:]
        limit = self.node.spec.get("max_chars")
        if limit is not None:
            self.node.text = self.node.text[:limit]
        return True


class _Node:
    def __init__(self, spec: dict, desktop: "_Desktop", bus: str, parent: "_Node | None") -> None:
        self.spec = spec
        self.desktop = desktop
        self.path = spec["ref"]
        self.app = _Application(bus)
        self.parent = parent
        self.text = spec.get("text", "")
        self.children = [_Node(child, desktop, bus, self) for child in spec.get("children", [])]

    def get_role_name(self) -> str:
        return self.spec["role"]

    def get_name(self) -> str:
        return self.spec.get("name", "")

    def get_child_count(self) -> int:
        return len(self.children)

    def get_child_at_index(self, index: int) -> "_Node | None":
        return self.children[index] if 0 <= index < len(self.children) else None

    def get_parent(self) -> "_Node | None":
        return self.parent

    def get_state_set(self) -> _StateSet:
        return _StateSet(self.spec.get("states", []))

    def get_action_iface(self) -> _Action | None:
        return _Action(self) if self.spec.get("actions") else None

    def get_text_iface(self) -> "_Node | None":
        # GI returns the node itself, not a separate object (measured
        # 2026-09-19), so the Text methods are the node's.
        return self if "text" in self.spec else None

    def get_character_count(self) -> int:
        return _Text.get_character_count(self)

    def get_text(self) -> "_Node | None":
        # `Atspi.Accessible.get_text()`: no arguments, returns the Text
        # interface. Called as `ti.get_text(0, n)` it raises TypeError, as
        # the real one does.
        return self.get_text_iface()

    def get_editable_text_iface(self) -> _EditableText | None:
        return _EditableText(self) if self.spec.get("editable_text") else None

    def get_process_id(self) -> int:
        return int(self.spec.get("pid", 0))


class _Desktop:
    def __init__(self, key: str) -> None:
        self.performed: list[list[str]] = []
        self.apps = []
        for app_key in FIXTURE["desktops"][key]:
            app = FIXTURE["apps"][app_key]
            root = {
                # libatspi gives every application root this same path.
                "ref": "/org/a11y/atspi/accessible/root",
                "role": "application",
                "name": app["name"],
                "pid": app["pid"],
                "children": app["windows"],
            }
            self.apps.append(_Node(root, self, app["bus"], None))

    def get_child_count(self) -> int:
        return len(self.apps)

    def get_child_at_index(self, index: int) -> _Node | None:
        return self.apps[index] if 0 <= index < len(self.apps) else None


class FakeAtspi:
    StateType = _StateType()
    Text = _Text

    def __init__(self) -> None:
        self.desktop: _Desktop | None = None

    def use(self, key: str) -> _Desktop:
        self.desktop = _Desktop(key)
        return self.desktop

    def init(self) -> None:
        pass

    def get_desktop(self, _index: int) -> _Desktop:
        assert self.desktop is not None
        return self.desktop


class _FakeDesktopCase(unittest.TestCase):
    def setUp(self) -> None:
        self.atspi = FakeAtspi()
        patcher = mock.patch.object(HELPER, "Atspi", self.atspi)
        patcher.start()
        self.addCleanup(patcher.stop)

    def dump(self, desktop: str, request: dict) -> dict:
        self.atspi.use(desktop)
        return HELPER.handle({"cmd": "dump", **request})

    @staticmethod
    def request_for(dumped: dict, act: dict) -> dict:
        """What `UiTree._target_payload` sends for the dumped node `act.ref`."""
        node = next(n for n in dumped["nodes"] if n["ref"] == act["ref"])
        req = {
            "cmd": act["cmd"],
            "app": dumped["app"],
            "app_bus": dumped["app_bus"],
            "app_pid": dumped["app_pid"],
            "scope": dumped["scope"],
            "window_ref": dumped["window_ref"],
            "path": node["path"],
            "ref": node["ref"],
            "role": node["role"],
            "name": node["name"],
        }
        for key in ("action", "text"):
            if key in act:
                req[key] = act[key]
        req.update(act.get("override", {}))
        return req

    def text_of(self, ref: str) -> str:
        stack = list(self.atspi.desktop.apps)
        while stack:
            node = stack.pop()
            if node.path == ref:
                return node.text
            stack.extend(node.children)
        raise AssertionError(f"no node {ref}")


# --------------------------------------------------------------- helper level
class HelperDumpTests(_FakeDesktopCase):
    def test_dump_cases(self) -> None:
        for case in FIXTURE["dump_cases"]:
            with self.subTest(case=case["name"]):
                got = self.dump(case["desktop"], case["request"])
                expect = case["expect"]
                self.assertEqual(got["ok"], expect["ok"], got.get("error"))
                if "code" in expect:
                    self.assertEqual(got.get("code"), expect["code"])
                for key, value in expect.items():
                    if key in ("ok", "code", "nodes"):
                        continue
                    self.assertEqual(got[key], value, key)
                if "nodes" not in expect:
                    continue
                self.assertEqual(len(got["nodes"]), len(expect["nodes"]))
                for node, want in zip(got["nodes"], expect["nodes"]):
                    for key, value in want.items():
                        self.assertEqual(node[key], value, (want["ref"], key))

    def test_every_listed_node_carries_its_object_path(self) -> None:
        got = self.dump("editor", {"target": "focused"})
        refs = [n["ref"] for n in got["nodes"]]
        self.assertTrue(all(refs), refs)
        self.assertEqual(len(refs), len(set(refs)))


class HelperActionTests(_FakeDesktopCase):
    def test_action_cases(self) -> None:
        for case in FIXTURE["action_cases"]:
            with self.subTest(case=case["name"]):
                dumped = self.dump(case["dump"]["desktop"], case["dump"]["request"])
                self.assertTrue(dumped["ok"], dumped.get("error"))
                request = self.request_for(dumped, case["act"])
                desktop = self.atspi.use(case["then"])
                got = HELPER.handle(request)
                expect = case["expect"]
                self.assertEqual(got["ok"], expect["ok"], got.get("error"))
                self.assertEqual(desktop.performed, expect["performed"])
                for ref, text in expect.get("text_after", {}).items():
                    self.assertEqual(self.text_of(ref), text)
                if not expect["ok"]:
                    self.assertEqual(got.get("code"), expect["code"], got.get("error"))
                    continue
                for key in ("resolved_by", "replaced_chars", "now_chars", "verified"):
                    if key in expect:
                        self.assertEqual(got[key], expect[key], key)
                self.assertEqual(got["ref"], case["act"]["ref"])

    def test_duplicate_object_paths_are_never_guessed(self) -> None:
        # A toolkit that hands two nodes the same path gives no identity: the
        # search must refuse instead of taking the first one it meets.
        dumped = self.dump("editor", {"target": "focused"})
        request = self.request_for(dumped, {"cmd": "act", "ref": "/org/pcbridge/Editor/a11y/close-b"})
        desktop = self.atspi.use("editor_prepend")
        twin = desktop.apps[1].children[0].children[0].children[0]  # "Ek 1"
        twin.path = "/org/pcbridge/Editor/a11y/close-b"
        got = HELPER.handle(request)
        self.assertFalse(got["ok"])
        self.assertEqual(got["code"], "ELEMENT_AMBIGUOUS")
        self.assertEqual(desktop.performed, [])


# -------------------------------------------- UiTree and the Python provider
class _ThroughUiTree(_FakeDesktopCase):
    """Route `uitree._call` into the helper, over a JSON round trip."""

    def setUp(self) -> None:
        super().setUp()
        self.calls: list[dict] = []

        def call(payload: dict, timeout: int) -> dict:
            self.calls.append(payload)
            wire = json.loads(json.dumps(payload, ensure_ascii=False))
            return json.loads(json.dumps(HELPER.handle(wire), ensure_ascii=False))

        patcher = mock.patch.object(uitreelib, "_call", call)
        patcher.start()
        self.addCleanup(patcher.stop)

    def dump_tree(self, tree: uitreelib.UiTree, desktop: str, request: dict):
        self.atspi.use(desktop)
        return tree.dump(
            target=request["target"],
            interactive_only=request.get("interactive_only", True),
            max_nodes=request.get("max_nodes", 400),
        )


class ProviderActionTests(_ThroughUiTree):
    """The fixture cases once more, the way `ui_click`/`ui_set_text` call them."""

    def test_action_cases_through_the_provider(self) -> None:
        for case in FIXTURE["action_cases"]:
            if "override" in case["act"]:
                continue  # a hand-made request; the provider never sends one
            with self.subTest(case=case["name"]):
                provider = PythonAccessibilityProvider()
                dump = self.dump_tree(provider, case["dump"]["desktop"], case["dump"]["request"])
                node = next(n for n in dump.nodes if n.ref == case["act"]["ref"])
                desktop = self.atspi.use(case["then"])
                act = case["act"]
                expect = case["expect"]
                run = (
                    (lambda: provider.click(node.node_id, act.get("action", "click")))
                    if act["cmd"] == "act"
                    else (lambda: provider.set_text(node.node_id, act["text"]))
                )
                if expect["ok"]:
                    result = run()
                    self.assertEqual(result["resolved_by"], expect["resolved_by"])
                    self.assertEqual(result["snapshot"], dump.snapshot)
                else:
                    with self.assertRaises(DesktopError) as raised:
                        run()
                    self.assertEqual(raised.exception.code.value, expect["code"])
                    self.assertEqual(raised.exception.category, ErrorCategory.ACCESSIBILITY)
                self.assertEqual(desktop.performed, expect["performed"])

    def test_refusals_that_a_new_dump_fixes_are_retryable(self) -> None:
        provider = PythonAccessibilityProvider()
        dump = self.dump_tree(provider, "editor", {"target": "focused"})
        close_a = next(n for n in dump.nodes if n.ref.endswith("/close-a"))
        self.atspi.use("editor_remove_a")
        with self.assertRaises(DesktopError) as raised:
            provider.click(close_a.node_id)
        self.assertEqual(raised.exception.code, ErrorCode.ELEMENT_STALE)
        self.assertTrue(raised.exception.retryable)
        self.assertIn("ui_dump", raised.exception.suggested_action)

    def test_password_field_is_refused_before_the_helper_is_asked(self) -> None:
        provider = PythonAccessibilityProvider()
        dump = self.dump_tree(provider, "editor", {"target": "focused"})
        field = next(n for n in dump.nodes if n.role == "password text")
        calls = len(self.calls)
        with self.assertRaises(DesktopError) as raised:
            provider.set_text(field.node_id, "hunter2")
        self.assertEqual(raised.exception.code, ErrorCode.PASSWORD_FIELD)
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(self.atspi.desktop.performed, [])

    def test_element_without_action_never_reaches_the_helper(self) -> None:
        tree = uitreelib.UiTree()
        dump = self.dump_tree(tree, "editor", {"target": "focused"})
        field = next(n for n in dump.nodes if n.ref.endswith("/field-name"))
        calls = len(self.calls)
        with self.assertRaises(uitreelib.UiTreeError) as raised:
            tree.click(field.node_id)
        self.assertEqual(raised.exception.code, ErrorCode.ACTION_UNSUPPORTED)
        self.assertIn("screen_capture", str(raised.exception))
        self.assertEqual(len(self.calls), calls)

    def test_ambiguous_dump_target_keeps_its_code(self) -> None:
        provider = PythonAccessibilityProvider()
        with self.assertRaises(DesktopError) as raised:
            self.dump_tree(provider, "similar_names", {"target": "gnome-te"})
        self.assertEqual(raised.exception.code, ErrorCode.ELEMENT_AMBIGUOUS)

    def test_a_helper_timeout_is_not_replayed(self) -> None:
        provider = PythonAccessibilityProvider()
        dump = self.dump_tree(provider, "editor", {"target": "focused"})
        ok = next(n for n in dump.nodes if n.name == "Tamam")

        def hang(*_args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="atspi_helper", timeout=kwargs.get("timeout"))

        with mock.patch.object(uitreelib, "_call", REAL_CALL), mock.patch.object(
            uitreelib.subprocess, "run", side_effect=hang
        ) as run:
            with self.assertRaises(DesktopError) as raised:
                provider.click(ok.node_id)
        self.assertEqual(run.call_count, 1)
        error = raised.exception
        self.assertEqual(error.code, ErrorCode.EXECUTION_UNKNOWN)
        self.assertEqual(error.category, ErrorCategory.EXECUTION)
        self.assertEqual(error.execution_state, "unknown")
        self.assertFalse(error.retryable)


class WindowReadTests(_ThroughUiTree):
    def test_window_cases(self) -> None:
        for case in FIXTURE["window_cases"]:
            with self.subTest(case=case["name"]):
                self.atspi.use(case["desktop"])
                got = HELPER.handle({"cmd": "windows"})
                self.assertEqual(got["windows"], case["expect_windows"])
                expect = case["expect_focused"]
                tree = uitreelib.UiTree()
                if expect["ok"]:
                    self.assertEqual(tree.focused_window(), (expect["app"], expect["window"]))
                else:
                    with self.assertRaises(uitreelib.UiTreeError) as raised:
                        tree.focused_window()
                    self.assertEqual(raised.exception.code.value, expect["code"])


class DumpRegistryTests(_ThroughUiTree):
    def test_dump_records_backend_app_window_and_snapshot(self) -> None:
        tree = uitreelib.UiTree()
        dump = self.dump_tree(tree, "editor", {"target": "focused"})
        self.assertEqual(dump.backend, "linux.atspi")
        self.assertEqual((dump.app_bus, dump.app_pid), (":1.30", 3100))
        self.assertEqual(dump.scope, "window")
        self.assertEqual(dump.window_ref, "/org/pcbridge/Editor/a11y/w1")
        self.assertRegex(dump.snapshot, r"^[0-9a-f]{12}$")
        again = self.dump_tree(tree, "editor", {"target": "focused"})
        self.assertNotEqual(again.snapshot, dump.snapshot)
        self.assertEqual([n.node_id for n in again.nodes], [n.node_id for n in dump.nodes])

    def test_focus_and_window_reads_leave_the_last_dump_alone(self) -> None:
        tree = uitreelib.UiTree()
        dump = self.dump_tree(tree, "editor", {"target": "focused"})
        ids = dict(dump.by_id)
        self.atspi.use("chrome")
        self.assertEqual(tree.focused_window(), ("chromium", "Sayfa - Chromium"))
        windows = tree.windows()
        self.assertIs(tree._last, dump)
        self.assertEqual(tree._last.by_id, ids)
        chromium = next(w for w in windows if w.app == "chromium")
        self.assertEqual((chromium.app_bus, chromium.app_pid), (":1.60", 7000))
        self.assertEqual(chromium.ref, "/org/a11y/atspi/accessible/1")

    def test_same_name_note_is_shown(self) -> None:
        tree = uitreelib.UiTree()
        dump = self.dump_tree(tree, "two_pythons", {"target": "python3"})
        text = uitreelib.describe(dump)
        self.assertIn("2 applications with this name", text)
        self.assertIn("pid 8001", text)


class ShortIdTests(unittest.TestCase):
    def test_id_cases(self) -> None:
        for case in FIXTURE["id_cases"]:
            with self.subTest(case=case["name"]):
                raw = [dict(item, path=[0, i]) for i, item in enumerate(case["nodes"])]
                nodes = uitreelib._to_nodes(raw)
                self.assertEqual([n.node_id for n in nodes], case["expect_ids"])
                tree = uitreelib.UiTree()
                tree._last = uitreelib.Dump(
                    app="x", window="", nodes=nodes, by_id={n.node_id: n for n in nodes}
                )
                for probe in case["resolve"]:
                    if "code" in probe:
                        with self.assertRaises(uitreelib.UiTreeError) as raised:
                            tree.resolve(probe["id"])
                        self.assertEqual(raised.exception.code.value, probe["code"], probe)
                    else:
                        self.assertIs(tree.resolve(probe["id"]), nodes[probe["index"]], probe)

    def test_ids_are_unique_in_a_large_dump(self) -> None:
        # 400 nodes of 4 hex digits collide with ~70 % probability; the last
        # overwrite made the first node's id click the second one.
        raw = [{"role": "list item", "name": f"Satır {i}", "path": [0, i]} for i in range(400)]
        nodes = uitreelib._to_nodes(raw)
        ids = [n.node_id for n in nodes]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(any(len(i) > uitreelib.MIN_ID for i in ids))
        self.assertTrue(all(len(i) >= uitreelib.MIN_ID for i in ids))


if __name__ == "__main__":
    unittest.main()
