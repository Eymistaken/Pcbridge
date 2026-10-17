"""A page as text: the accessibility tree turned into an outline an agent can
read and act on.

Pure functions over the CDP answers (`Accessibility.getFullAXTree` per
frame, `DOMSnapshot.captureSnapshot` for the attributes the tree lacks), so
they are tested without a browser.

Every element an agent can act on gets a ref, `e<backendDOMNodeId>`. The
backend id is the browser's own and stays the same between reads of one
document, so a ref from an earlier read still names the same element; after
a navigation it no longer exists and an action on it is refused.

Measured (Chrome 155): a same-origin iframe's content is not in the main
frame's tree, only an `Iframe` node with no children, so each frame is read
on its own and put under its owner. A password field is a plain `textbox`
in the tree; the DOM's `type` attribute is what tells it apart, and its
value is never shown.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

MASK = "••••"
MAX_NAME = 160
MAX_OPTIONS = 25

INTERACTIVE = {
    "button", "link", "textbox", "searchbox", "checkbox", "radio", "combobox", "listbox",
    "option", "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "switch", "slider",
    "spinbutton", "treeitem", "ToggleButton", "PopUpButton", "DisclosureTriangle",
}
# Controls whose own line says everything; their children are internals.
LEAF = {"textbox", "searchbox", "combobox", "spinbutton", "slider", "checkbox", "radio",
        "switch", "PopUpButton", "ToggleButton"}
# Roles whose name comes from their text: the text is not repeated below.
NAMED_BY_TEXT = {"link", "button", "heading", "option", "tab", "menuitem", "menuitemcheckbox",
                 "menuitemradio", "treeitem", "cell", "gridcell", "columnheader", "rowheader",
                 "listitem", "Legend", "caption", "label"}
SKIP = {"InlineTextBox", "LineBreak"}
TRANSPARENT = {"none", "generic", "presentation", "paragraph", "LabelText", "Section", "div",
               "RootWebArea", "WebArea", "Pre", "Abbr", "Mark", "emphasis", "strong", "time",
               "subscript", "superscript", "code", "rowgroup", "list", "listitem",
               "DescriptionList", "term", "definition", "Details", "blockquote", "Ruby"}
TABLES = {"table", "grid", "treegrid", "LayoutTable"}
CELLS = {"cell", "gridcell", "columnheader", "rowheader", "LayoutTableCell"}
ROWS = {"row", "LayoutTableRow"}
STATES = ("disabled", "required", "readonly", "invalid", "focused")


@dataclass
class Element:
    """The DOM facts the accessibility tree does not carry."""
    tag: str = ""
    type: str = ""
    href: str = ""


@dataclass
class Frame:
    """One frame's accessibility nodes, and the backend id of the element
    that holds it (None for the main frame)."""
    nodes: list[dict]
    owner: int | None = None
    url: str = ""


@dataclass
class Page:
    lines: list[str] = field(default_factory=list)
    refs: int = 0


def elements(dom_snapshot: dict) -> dict[int, Element]:
    """Backend id -> tag, input type, href, from `DOMSnapshot.captureSnapshot`."""
    strings = dom_snapshot.get("strings", [])
    out: dict[int, Element] = {}

    def s(i: int) -> str:
        return strings[i] if 0 <= i < len(strings) else ""

    for doc in dom_snapshot.get("documents", []):
        nodes = doc.get("nodes", {})
        ids = nodes.get("backendNodeId", [])
        names = nodes.get("nodeName", [])
        attrs = nodes.get("attributes", [])
        for i, backend in enumerate(ids):
            tag = s(names[i]).lower() if i < len(names) else ""
            if tag not in ("input", "a", "area", "select", "textarea", "iframe", "frame"):
                continue
            el = Element(tag=tag)
            pairs = attrs[i] if i < len(attrs) else []
            for k, v in zip(pairs[::2], pairs[1::2]):
                key = s(k).lower()
                if key == "type":
                    el.type = s(v).lower()
                elif key == "href":
                    el.href = s(v)
            out[backend] = el
    return out


def is_password(el: Element | None) -> bool:
    return bool(el and el.tag == "input" and el.type == "password")


def visible(text: str) -> str:
    """Whitespace collapsed; invisible format characters (Cf) and
    private-use characters (Co) dropped. Measured on a real router's
    sign-in page: icon fonts draw the globe and the lock as private-use
    glyphs (U+E60F, U+E610), which carry no meaning and made empty lines."""
    return " ".join("".join(ch for ch in text
                            if unicodedata.category(ch) not in ("Cf", "Co")).split())


def _clip(text: str, limit: int = MAX_NAME) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _q(text: str) -> str:
    return '"' + _clip(text).replace('"', "'") + '"'


class _Tree:
    def __init__(self, frame: Frame, dom: dict[int, Element], frames: dict[int, "_Tree"]) -> None:
        self.by_id = {n["nodeId"]: n for n in frame.nodes}
        self.dom, self.frames = dom, frames
        self.root = next((n for n in frame.nodes if not n.get("parentId")), None)
        self.refs = 0

    # -- node facts ---------------------------------------------------------
    @staticmethod
    def role(n: dict) -> str:
        return (n.get("role") or {}).get("value") or ""

    @staticmethod
    def name(n: dict) -> str:
        return visible(str((n.get("name") or {}).get("value") or ""))

    @staticmethod
    def props(n: dict) -> dict:
        return {p["name"]: (p.get("value") or {}).get("value") for p in n.get("properties", [])}

    def kids(self, n: dict) -> list[dict]:
        return [self.by_id[c] for c in n.get("childIds", []) if c in self.by_id]

    def ref(self, n: dict) -> str:
        backend = n.get("backendDOMNodeId")
        if backend is None:
            return ""
        self.refs += 1
        return f"[e{backend}]"

    def has_named_control(self, n: dict) -> bool:
        return any((self.role(k) in INTERACTIVE and self.name(k)) or self.has_named_control(k)
                   for k in self.kids(n))

    def text_of(self, n: dict) -> str:
        """All text below a node, for a table cell."""
        if self.role(n) == "StaticText":
            return self.name(n)
        if self.role(n) in INTERACTIVE:
            return self.element_line(n)
        parts = [self.text_of(k) for k in self.kids(n) if self.role(k) not in SKIP]
        return " ".join(p for p in parts if p)

    # -- lines --------------------------------------------------------------
    def element_line(self, n: dict) -> str:
        role, name, props = self.role(n), self.name(n), self.props(n)
        el = self.dom.get(n.get("backendDOMNodeId") or -1)
        parts = [role]
        if is_password(el):
            parts = ["password"]
        if name:
            parts.append(_q(name))
        interactive = role in INTERACTIVE or props.get("focusable") and role not in TRANSPARENT
        if interactive:
            r = self.ref(n)
            if r:
                parts.append(r)
        if role == "heading" and props.get("level"):
            parts.append(f"level={props['level']}")
        checked = props.get("checked")
        if checked is not None and role in ("checkbox", "radio", "switch", "menuitemcheckbox",
                                            "menuitemradio", "ToggleButton"):
            parts.append({"true": "checked", "mixed": "mixed"}.get(str(checked), "unchecked"))
        if role in ("option", "tab", "treeitem") and props.get("selected"):
            parts.append("selected")
        if "expanded" in props and role not in ("combobox",):
            parts.append("expanded" if props["expanded"] else "collapsed")
        for state in STATES:
            v = props.get(state)
            if v is True or v == "true":
                parts.append(state)
        value = (n.get("value") or {}).get("value")
        if is_password(el):
            parts.append(f"= {MASK}" if value else "(empty)")
        elif value not in (None, "") and role not in ("link", "heading"):
            parts.append(f"= {_q(value)}")
        if role == "combobox":
            opts = self.options(n)
            if opts:
                parts.append("options: " + opts)
        if el is not None and el.href and role == "link":
            parts.append(f"→ {_clip(el.href, 120)}")
        return " ".join(parts)

    def options(self, n: dict) -> str:
        found: list[str] = []

        def walk(x: dict) -> None:
            for k in self.kids(x):
                if self.role(k) == "option":
                    found.append(_q(self.name(k)) + ("*" if self.props(k).get("selected") else ""))
                else:
                    walk(k)
        walk(n)
        more = len(found) - MAX_OPTIONS
        return ", ".join(found[:MAX_OPTIONS]) + (f", … {more} more" if more > 0 else "")

    def table(self, n: dict, depth: int, out: list[str]) -> None:
        pad = "  " * depth
        name = self.name(n)
        out.append(f"{pad}table" + (f" {_q(name)}" if name else ""))
        rows: list[dict] = []

        def find_rows(x: dict) -> None:
            for k in self.kids(x):
                if self.role(k) in ROWS:
                    rows.append(k)
                elif self.role(k) not in TABLES:
                    find_rows(k)
        find_rows(n)
        for row in rows:
            cells = [k for k in self.kids(row) if self.role(k) in CELLS]
            if not cells:
                continue
            out.append(f"{pad}  | " + " | ".join(_clip(self.text_of(c), 80) for c in cells) + " |")

    def render(self, n: dict, depth: int, out: list[str], text_named: bool = False) -> None:
        """Append the lines of `n`'s children (and `n` itself unless it is
        transparent). `text_named`: an ancestor took its name from this text."""
        role = self.role(n)
        if role in SKIP or role == "Legend":
            return  # a fieldset's legend is its group's name, shown on the group
        if role == "StaticText":
            if not text_named:
                text = self.name(n)
                if text:
                    out.append("  " * depth + _q(text))
            return
        if role in TABLES:
            self.table(n, depth, out)
            return
        child_depth = depth
        named = text_named
        if not n.get("ignored") and role not in TRANSPARENT:
            name = self.name(n)
            if role in INTERACTIVE or name or role in ("img", "Iframe", "separator"):
                if role == "img" and not name:
                    return
                out.append("  " * depth + (self.element_line(n) if role != "Iframe"
                                           else f"frame {_q(name)}" if name else "frame"))
                child_depth = depth + 1
                named = named or (role in NAMED_BY_TEXT and bool(name))
            if role in LEAF:
                return
            if role == "Iframe":
                sub = self.frames.get(n.get("backendDOMNodeId") or -1)
                if sub is not None and sub.root is not None:
                    sub.render(sub.root, child_depth, out)
                    self.refs += sub.refs
                return
        elif role == "LabelText" and self.has_named_control(n):
            named = True  # the label's text is the control's name, already shown
        run: list[str] = []
        for k in self.kids(n):
            if self.role(k) == "StaticText":
                if not named and self.name(k):
                    run.append(self.name(k))
                continue
            if self.role(k) in SKIP:
                continue
            if run:
                out.append("  " * child_depth + _q(" ".join(run)))
                run = []
            self.render(k, child_depth, out, named)
        if run:
            out.append("  " * child_depth + _q(" ".join(run)))


def outline(frames: list[Frame], dom: dict[int, Element]) -> Page:
    """The main frame (first) with every child frame under its owner."""
    if not frames:
        return Page()
    subs: dict[int, _Tree] = {}
    for f in frames[1:]:
        if f.owner is not None:
            subs[f.owner] = _Tree(f, dom, subs)
    main = _Tree(frames[0], dom, subs)
    lines: list[str] = []
    if main.root is not None:
        main.render(main.root, 0, lines)
    return Page(lines=lines, refs=main.refs)


def trim(lines: list[str], find: str = "", max_chars: int = 12000) -> tuple[str, int, bool]:
    """Join the lines, keeping only those that contain `find` (any case);
    cut at `max_chars` on a line boundary. Returns (text, lines kept, cut)."""
    if find:
        needle = find.lower()
        lines = [ln.strip() for ln in lines if needle in ln.lower()]
    out: list[str] = []
    size = 0
    for ln in lines:
        if size + len(ln) + 1 > max_chars:
            return "\n".join(out), len(out), True
        out.append(ln)
        size += len(ln) + 1
    return "\n".join(out), len(out), False
