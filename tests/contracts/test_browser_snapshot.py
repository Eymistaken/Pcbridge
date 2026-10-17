#!/usr/bin/env python3
"""The page outline (pcbridge/browser/snapshot.py), from accessibility nodes
shaped like Chrome 155's: refs, password masking, labels said once, tables,
frames under their owner, options of a select, `find` and the size cut.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.browser import snapshot as S  # noqa: E402


class Tree:
    """Build `Accessibility.getFullAXTree` nodes; ids double as backend ids."""

    def __init__(self) -> None:
        self.nodes: list[dict] = []
        self.next = 1

    def add(self, role: str, name: str = "", parent: dict | None = None, value: str | None = None,
            ignored: bool = False, **props) -> dict:
        nid = str(self.next)
        self.next += 1
        node = {"nodeId": nid, "role": {"value": role}, "ignored": ignored, "childIds": [],
                "backendDOMNodeId": int(nid),
                "properties": [{"name": k, "value": {"value": v}} for k, v in props.items()]}
        if name:
            node["name"] = {"value": name}
        if value is not None:
            node["value"] = {"value": value}
        if parent is not None:
            node["parentId"] = parent["nodeId"]
            parent["childIds"].append(nid)
        self.nodes.append(node)
        return node

    def text(self, text: str, parent: dict) -> dict:
        st = self.add("StaticText", text, parent)
        self.add("InlineTextBox", text, st)
        return st


def render(*trees: S.Frame, dom: dict | None = None) -> list[str]:
    return S.outline(list(trees), dom or {}).lines


class OutlineTests(unittest.TestCase):
    def test_controls_get_refs_values_and_states(self) -> None:
        t = Tree()
        root = t.add("RootWebArea", "Wireless")
        form = t.add("form", parent=root)
        box = t.add("textbox", "Network name", form, value="home-net", focusable=True)
        t.text("home-net", t.add("generic", parent=box))  # the field's own internals
        t.add("checkbox", "Radio on", form, checked="true", focusable=True)
        t.add("button", "Save", form, disabled=True, focusable=True)
        lines = render(S.Frame(t.nodes))
        self.assertEqual(lines, [
            'textbox "Network name" [e3] = "home-net"',
            'checkbox "Radio on" [e7] checked',
            'button "Save" [e8] disabled',
        ])

    def test_a_password_value_is_never_shown(self) -> None:
        t = Tree()
        root = t.add("RootWebArea")
        pw = t.add("textbox", "Password", root, value="hunter2-secret", focusable=True)
        empty = t.add("textbox", "Again", root, value="", focusable=True)
        dom = {pw["backendDOMNodeId"]: S.Element("input", "password"),
               empty["backendDOMNodeId"]: S.Element("input", "password")}
        text = "\n".join(render(S.Frame(t.nodes), dom=dom))
        self.assertNotIn("hunter2", text)
        self.assertIn('password "Password" [e2] = ' + S.MASK, text)
        self.assertIn('password "Again" [e3] (empty)', text)

    def test_a_label_and_a_link_say_their_text_once(self) -> None:
        t = Tree()
        root = t.add("RootWebArea")
        label = t.add("LabelText", parent=root)
        t.text("Network name ", label)
        t.add("textbox", "Network name", label, focusable=True)
        link = t.add("link", "Wireless", root, focusable=True)
        t.text("Wireless", link)
        heading = t.add("heading", "Status", root, level=2)
        t.text("Status", heading)
        lines = render(S.Frame(t.nodes), dom={link["backendDOMNodeId"]: S.Element("a", href="/wifi")})
        self.assertEqual(lines, ['textbox "Network name" [e5]', 'link "Wireless" [e6] → /wifi',
                                 'heading "Status" level=2'])

    def test_text_runs_join_and_ignored_wrappers_vanish(self) -> None:
        t = Tree()
        root = t.add("RootWebArea")
        wrap = t.add("none", parent=root, ignored=True)
        para = t.add("paragraph", parent=wrap)
        t.text("Firmware ", para)
        t.text("1.4", para)
        self.assertEqual(render(S.Frame(t.nodes)), ['"Firmware 1.4"'])

    def test_a_table_is_rows_of_cells(self) -> None:
        t = Tree()
        root = t.add("RootWebArea")
        table = t.add("table", parent=root)
        group = t.add("none", parent=table, ignored=True)
        for cells, role in ((("Name", "Address"), "columnheader"), (("laptop", "192.168.1.20"), "cell")):
            row = t.add("row", parent=group)
            for c in cells:
                t.text(c, t.add(role, c, row))
        edit_row = t.add("row", parent=group)
        cell = t.add("cell", parent=edit_row)
        t.add("link", "Edit", cell, focusable=True)
        self.assertEqual(render(S.Frame(t.nodes)), [
            "table", "  | Name | Address |", "  | laptop | 192.168.1.20 |",
            f'  | link "Edit" [e{cell["backendDOMNodeId"] + 1}] |'])

    def test_a_frame_goes_under_its_owner(self) -> None:
        main = Tree()
        root = main.add("RootWebArea", "Status")
        owner = main.add("Iframe", "Device status", root)
        main.add("button", "After", root, focusable=True)
        sub = Tree()
        sub.next = 100
        sroot = sub.add("RootWebArea", "status")
        sub.text("WAN connected", sub.add("paragraph", parent=sroot))
        lines = render(S.Frame(main.nodes), S.Frame(sub.nodes, owner=owner["backendDOMNodeId"]))
        self.assertEqual(lines, ['frame "Device status"', '  "WAN connected"', 'button "After" [e3]'])

    def test_a_select_lists_its_options_and_marks_the_chosen_one(self) -> None:
        t = Tree()
        root = t.add("RootWebArea")
        combo = t.add("combobox", "Channel", root, value="Channel 6", expanded=False, focusable=True)
        popup = t.add("MenuListPopup", parent=combo)
        for c in ("1", "6", "11"):
            t.add("option", f"Channel {c}", popup, selected=c == "6")
        self.assertEqual(render(S.Frame(t.nodes)), [
            'combobox "Channel" [e2] = "Channel 6" options: "Channel 1", "Channel 6"*, "Channel 11"'])

    def test_a_fieldset_legend_is_only_the_group_name(self) -> None:
        t = Tree()
        root = t.add("RootWebArea")
        group = t.add("group", "Band", root)
        t.text("Band", t.add("Legend", parent=group))
        t.add("radio", "5 GHz", group, checked="false", focusable=True)
        self.assertEqual(render(S.Frame(t.nodes)), ['group "Band"', '  radio "5 GHz" [e6] unchecked'])

    def test_find_and_the_size_cut(self) -> None:
        lines = [f"row {i}" for i in range(100)] + ["textbox \"SSID\" [e9]"]
        text, kept, cut = S.trim(lines, find="ssid")
        self.assertEqual((text, kept, cut), ('textbox "SSID" [e9]', 1, False))
        text, kept, cut = S.trim(lines, max_chars=30)
        self.assertTrue(cut)
        self.assertLessEqual(len(text), 30)
        self.assertEqual(text.splitlines()[-1], f"row {kept - 1}")

    def test_dom_snapshot_attributes(self) -> None:
        snap = {"strings": ["INPUT", "type", "password", "A", "href", "/wifi", "DIV"],
                "documents": [{"nodes": {"backendNodeId": [7, 8, 9], "nodeName": [0, 3, 6],
                                         "attributes": [[1, 2], [4, 5], []]}}]}
        els = S.elements(snap)
        self.assertTrue(S.is_password(els[7]))
        self.assertEqual(els[8].href, "/wifi")
        self.assertNotIn(9, els)


if __name__ == "__main__":
    unittest.main()
