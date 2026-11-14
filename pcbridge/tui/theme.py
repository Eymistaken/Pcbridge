"""The terminal UI's own palette, glyphs and shared styles.

The accents and states are our own colors, the same in every terminal; the
background stays the terminal's (`ansi_default`). The tokens are the ones in
TUI_REDESIGN.md, *Palette*. Every widget refers to them as `$pb-NAME` CSS
variables; the few places that build Rich text use `PALETTE` directly.
"""

from __future__ import annotations

import dataclasses

from textual.theme import BUILTIN_THEMES, Theme

PALETTE: dict[str, str] = {
    "accent": "#45c4d6",
    "accent-light": "#8fdde8",   # the key after a button's label
    "primary": "#1d6aa8",
    "danger": "#8a2c2c",
    "working": "#6aa5ff",
    "ok": "#62d08a",
    "warning": "#e8bb4f",
    "error": "#ff7a7a",
    "text": "#d5dbe1",
    "muted": "#8d99a6",
    "dim": "#75818e",
    "panel": "#151a20",
    "button": "#27313b",
    "button-hover": "#34414e",
    "selection": "#1e2a38",
    "selection-hover": "#161c23",
    "rule": "#2a333d",
    "banner": "#3a3115",
    "banner-text": "#f3d27c",
    "input": "#1b2027",
    "box": "#0e1216",            # the dark box behind output, diffs and the journal
    "switch-off": "#c8434b",
    # Activity kinds
    "kind-desktop": "#c79bff",
    "kind-shell": "#6aa5ff",
    "kind-file": "#e8bb4f",
    "kind-agent": "#62d08a",
    "kind-remote": "#ff9a9a",
}

# States never rely on hue alone; each has a glyph too.
GLYPH = {
    "ok": "●", "done": "✓", "open": "◇", "warn": "▲", "remote": "◉", "error": "✕",
    "locked": "◆", "off": "○", "cancelled": "⊘", "none": "·",
}
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _theme() -> Theme:
    base = BUILTIN_THEMES["ansi-dark"]
    return dataclasses.replace(
        base,
        name="pcbridge",
        primary=PALETTE["primary"],
        secondary=PALETTE["accent"],
        accent=PALETTE["accent"],
        success=PALETTE["ok"],
        warning=PALETTE["warning"],
        error=PALETTE["error"],
        foreground=PALETTE["text"],
        variables={**base.variables, **{f"pb-{k}": v for k, v in PALETTE.items()},
                   "block-cursor-background": PALETTE["selection"],
                   "block-cursor-foreground": PALETTE["text"],
                   "border-blurred": PALETTE["rule"],
                   "footer-key-foreground": PALETTE["accent"]},
    )


THEME = _theme()


def css_variables() -> dict[str, str]:
    """The `$pb-*` variables, for App.get_css_variables (a theme's variables
    reach widgets' DEFAULT_CSS only after the theme is active)."""
    return {f"pb-{k}": v for k, v in PALETTE.items()}


# Styles shared by every tab: sections, buttons, tags, switches, tables.
CSS = """
Screen {
    background: ansi_default;
    color: $pb-text;
}
.hint, .dim {
    color: $pb-dim;
}
.muted {
    color: $pb-muted;
}
.ok { color: $pb-ok; }
.warn { color: $pb-warning; }
.error { color: $pb-error; }
.working { color: $pb-working; }

SectionTitle {
    height: 1;
    margin-top: 1;
    color: $pb-muted;
    text-style: bold;
}
SectionTitle.first {
    margin-top: 0;
}

/* Buttons: one line, compact, the key after the label in a lighter accent. */
Button.-textual-compact {
    background: $pb-button;
    color: $pb-text;
    border: none;
    height: 1;
    min-width: 0;
    padding: 0 1;
    margin-right: 1;
    text-style: none;
}
Button.-textual-compact:hover {
    background: $pb-button-hover;
}
Button.-textual-compact:focus {
    text-style: bold;
    background-tint: $pb-accent 15%;
}
Button.-textual-compact.primary {
    background: $pb-primary;
    color: white;
}
Button.-textual-compact.danger {
    background: $pb-danger;
    color: white;
}
Button.-textual-compact.link {
    background: transparent;
    color: $pb-accent;
    padding: 0;
}
Button.-textual-compact.link:hover {
    text-style: underline;
}
Button.-textual-compact.quiet {
    background: transparent;
    color: $pb-error;
    padding: 0;
}
Button.-textual-compact.quiet:hover {
    text-style: underline;
}
/* The ANSI theme gives a disabled button a border (!important), which
   breaks a one-line compact button; keep compact buttons one line. */
Button.-textual-compact:disabled {
    border: none !important;
    height: 1;
    color: $pb-dim;
    text-style: none;
}

/* Textual's own Switch with our colors: on green right, off red left. */
Switch, Switch:ansi {
    background: $pb-input;
    border: tall $pb-rule;
}
Switch:focus, Switch:ansi:focus {
    border: tall $pb-accent;
}
Switch .switch--slider, Switch:ansi .switch--slider {
    color: $pb-switch-off;
    background: $pb-input;
}
Switch.-on .switch--slider, Switch:ansi.-on .switch--slider {
    color: $pb-ok;
}
Switch:hover > .switch--slider, Switch:ansi:hover > .switch--slider {
    color: #e0606a;
}
Switch.-on:hover > .switch--slider, Switch:ansi.-on:hover > .switch--slider {
    color: #82e0a6;
}

Input, Input:ansi {
    background: $pb-input;
    border: none;
    height: 1;
    padding: 0 1;
    color: $pb-text;
}
Input:focus, Input:ansi:focus {
    border: none;
    text-style: underline;
    background-tint: $pb-accent 8%;
}

DataTable, DataTable:ansi {
    background: ansi_default;
    scrollbar-size-vertical: 1;
    scrollbar-size-horizontal: 0;
}
DataTable > .datatable--header, DataTable:ansi > .datatable--header {
    color: $pb-muted;
    text-style: bold;
    background: ansi_default;
}
DataTable > .datatable--cursor, DataTable:ansi > .datatable--cursor {
    background: $pb-selection;
    color: $pb-text;
    text-style: none;
}
DataTable > .datatable--hover, DataTable:ansi > .datatable--hover {
    background: $pb-selection-hover;
}

.box {
    background: $pb-box;
    padding: 0 1;
}
.row {
    height: auto;
}
.cols {
    height: auto;
}
.col {
    width: 1fr;
    height: auto;
}
.gap {
    margin-left: 4;
}
.label {
    width: 12;
    color: $pb-muted;
}
"""
