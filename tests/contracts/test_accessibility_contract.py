#!/usr/bin/env python3
"""Accessibility ids and the text the model reads (Task 6.1).

The reader is the native helper. Its walk and its actions run against the
shared fixture, `tests/fixtures/native/accessibility_cases.json`, in Rust
(`accessibility_read.rs`, `accessibility_actions.rs`) and through the
provider in `tests/integration/test_native_accessibility.py`; what the
provider sends and refuses is pinned in `test_native_accessibility.py`.

What stays in Python is everything after the read, and that is what this
file holds: the short public ids (`id_cases`), resolving an id back to a
node of the last dump, and the note `ui_dump` prints when several
applications share a name. Nothing here reads a real tree or sends input.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.desktop import uitree as uitreelib  # noqa: E402

FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "native" / "accessibility_cases.json").read_text(
        encoding="utf-8"
    )
)


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
        raw = [{"role": "list item", "name": f"Row {i}", "path": [0, i]} for i in range(400)]
        nodes = uitreelib._to_nodes(raw)
        ids = [n.node_id for n in nodes]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(any(len(i) > uitreelib.MIN_ID for i in ids))
        self.assertTrue(all(len(i) >= uitreelib.MIN_ID for i in ids))


class DumpTextTests(unittest.TestCase):
    def test_same_name_note_is_shown(self) -> None:
        # The fixture's "two_pythons" desktop: two applications called
        # python3, and the dump names which one it read.
        dump = uitreelib.dump_from_response({
            "ok": True,
            "app": "python3",
            "app_pid": 8001,
            "same_name": 2,
            "window": "One",
            "nodes": [{"role": "push button", "name": "OK", "actions": ["click"],
                       "path": [0, 0]}],
        })
        text = uitreelib.describe(dump)
        self.assertIn("2 applications with this name", text)
        self.assertIn("pid 8001", text)

    def test_one_application_gets_no_note(self) -> None:
        dump = uitreelib.dump_from_response({"ok": True, "app": "python3", "nodes": []})
        self.assertNotIn("applications with this name", uitreelib.describe(dump))

    def test_a_dump_without_a_helper_snapshot_still_gets_one(self) -> None:
        dump = uitreelib.dump_from_response({"ok": True, "app": "x", "nodes": []})
        self.assertRegex(dump.snapshot, r"^[0-9a-f]{12}$")


if __name__ == "__main__":
    unittest.main()
