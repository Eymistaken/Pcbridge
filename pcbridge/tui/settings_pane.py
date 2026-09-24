"""The Settings tab: every setting, edited with the widget its type needs.

Sections on the left, the section's settings in a table on the right, the
selected setting's editor and description under it. Changes are staged in
one `ConfigEditor` and written together by Save: one check with the real
loader, one backup, one atomic write. Secrets are never shown; they can
only be replaced or, for the token, generated.
"""

from __future__ import annotations

import secrets
from typing import Any

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (Button, DataTable, Input, OptionList, Select, Static,
                             Switch)
from textual.widgets.option_list import Option

from .. import settings as S

CSS = """
SettingsPane #settings-sections {
    width: 26;
    height: 1fr;
    margin-right: 1;
}
SettingsPane #settings-right {
    width: 1fr;
}
SettingsPane #settings-filter {
    margin-bottom: 1;
}
SettingsPane #settings-table {
    height: 1fr;
    min-height: 6;
}
SettingsPane #settings-editor {
    height: auto;
    max-height: 60%;
    padding-top: 1;
}
SettingsPane #editor-title {
    text-style: bold;
}
SettingsPane #editor-field {
    height: auto;
    margin: 1 0 0 0;
}
SettingsPane #editor-field Input {
    width: 1fr;
}
SettingsPane #editor-field Button {
    margin-left: 1;
}
SettingsPane #editor-error {
    color: $error;
}
SettingsPane #editor-help {
    height: auto;
    max-height: 12;
    margin-top: 1;
}
SettingsPane #settings-actions {
    height: 1;
    margin-top: 1;
}
SettingsPane #settings-pending {
    width: 1fr;
}
SettingsPane #settings-actions Button {
    margin-left: 1;
}
"""

# Changes that get one more question before they are written.
SENSITIVE = {
    "desktop.enabled": "agents can drive this desktop once they open the grant",
    "host": "the address the daemon listens on",
    "port": "the port the daemon and the tunnel use",
    "public_url": "the address remote clients use",
}


class SettingsPane(Vertical):
    DEFAULT_CSS = CSS

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.ed: S.ConfigEditor | None = None
        self.section = ""
        self.current: S.Setting | None = None
        self.restart_level: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="settings-message", classes="hint")
        with Horizontal():
            yield OptionList(id="settings-sections")
            with Vertical(id="settings-right"):
                yield Input(placeholder="Filter: type part of a key or its description",
                            id="settings-filter")
                yield DataTable(id="settings-table", cursor_type="row")
                with VerticalScroll(id="settings-editor"):
                    yield Static("", id="editor-title")
                    yield Static("", id="editor-meta", classes="hint")
                    yield Horizontal(id="editor-field")
                    yield Static("", id="editor-error")
                    yield Static("", id="editor-help")
        with Horizontal(id="settings-actions"):
            yield Static("", id="settings-pending")
            yield Button("Restart daemon", id="settings-restart", compact=True)
            yield Button("Discard", id="settings-discard", compact=True)
            yield Button("Save", id="settings-save", variant="primary", compact=True)

    def on_mount(self) -> None:
        table = self.query_one("#settings-table", DataTable)
        table.add_columns(" ", "Setting", "Value")
        self.query_one("#settings-restart").display = False
        self.load()

    # -- loading ------------------------------------------------------------------

    def load(self) -> None:
        try:
            self.ed = self.app.backend.editor()
        except Exception as exc:  # noqa: BLE001 - SettingsError, SystemExit, a missing file
            self.ed = None
            self.query_one("#settings-message", Static).update(
                Text(f"The settings cannot be read: {getattr(exc, 'message', exc)}", style="bold"))
            return
        self.query_one("#settings-message", Static).update(f"{self.ed.path}")
        sections = self.query_one("#settings-sections", OptionList)
        sections.clear_options()
        seen: list[str] = []
        for s in self.ed.settings:
            if s.section not in seen:
                seen.append(s.section)
                sections.add_option(Option(s.section_title, id=s.section or "general"))
        if self.section not in seen:
            self.section = seen[0] if seen else ""
        sections.highlighted = seen.index(self.section) if seen else None
        self.fill_table()
        self.update_pending()

    def visible(self) -> list[S.Setting]:
        if self.ed is None:
            return []
        words = self.query_one("#settings-filter", Input).value.lower().split()
        if words:
            return [s for s in self.ed.settings
                    if all(w in f"{s.key} {s.help}".lower() for w in words)]
        return [s for s in self.ed.settings if s.section == self.section]

    def _mark(self, s: S.Setting) -> Text:
        assert self.ed is not None
        if s.key in self.ed.changed:
            return Text("+", style="bold")
        if self.ed.is_set(s.key) and self.ed.value(s.key) != self.ed.default(s.key):
            return Text("*")
        return Text(" ")

    def _shown(self, s: S.Setting) -> str:
        assert self.ed is not None
        text = S.format_value(s, self.ed.value(s.key))
        env = self.ed.env_override(s.key)
        if env:
            text += f"  (from ${env})"
        return text if len(text) <= 70 else text[:67] + "..."

    def fill_table(self, keep: str | None = None) -> None:
        table = self.query_one("#settings-table", DataTable)
        table.clear()
        rows = self.visible()
        for s in rows:
            label = s.key if self.query_one("#settings-filter", Input).value else s.name
            table.add_row(self._mark(s), label, self._shown(s), key=s.key)
        target = keep or (self.current.key if self.current else None)
        keys = [s.key for s in rows]
        if target in keys:
            table.move_cursor(row=keys.index(target))
        if rows:
            self.show(self.ed.setting(keys[table.cursor_row]) if table.cursor_row < len(keys) else rows[0])
        else:
            self.show(None)

    def refresh_row(self, s: S.Setting) -> None:
        table = self.query_one("#settings-table", DataTable)
        try:
            table.update_cell(s.key, table.ordered_columns[0].key, self._mark(s))
            table.update_cell(s.key, table.ordered_columns[2].key, self._shown(s))
        except Exception:  # noqa: BLE001 - the row may be filtered out
            pass
        self.update_pending()

    def update_pending(self) -> None:
        n = len(self.ed.changed) if self.ed else 0
        pending = self.query_one("#settings-pending", Static)
        if n:
            pending.update(Text(f"{n} unsaved change{'s' if n != 1 else ''}: {', '.join(self.ed.changed)}",
                                style="bold"))
        else:
            pending.update("+ unsaved   * differs from the default")
        self.query_one("#settings-save", Button).disabled = n == 0
        self.query_one("#settings-discard", Button).disabled = n == 0

    # -- navigation -----------------------------------------------------------------

    @on(OptionList.OptionHighlighted, "#settings-sections")
    def _section(self, event: OptionList.OptionHighlighted) -> None:
        section = "" if event.option.id == "general" else str(event.option.id)
        if section != self.section:
            self.section = section
            self.current = None
            self.query_one("#settings-filter", Input).value = ""
            self.fill_table()

    @on(Input.Changed, "#settings-filter")
    def _filter(self) -> None:
        self.current = None
        self.fill_table()

    @on(DataTable.RowHighlighted, "#settings-table")
    def _row(self, event: DataTable.RowHighlighted) -> None:
        if self.ed is None or event.row_key is None or event.row_key.value is None:
            return
        s = self.ed.setting(str(event.row_key.value))
        if self.current is None or s.key != self.current.key:
            self.show(s)

    # -- the editor --------------------------------------------------------------------

    def show(self, s: S.Setting | None) -> None:
        self.current = s
        title = self.query_one("#editor-title", Static)
        meta = self.query_one("#editor-meta", Static)
        field = self.query_one("#editor-field", Horizontal)
        self.query_one("#editor-error", Static).update("")
        field.remove_children()
        if s is None or self.ed is None:
            title.update("No setting matches." if self.ed else "")
            meta.update("")
            self.query_one("#editor-help", Static).update("")
            return
        title.update(s.key)
        bits = []
        if not s.secret:
            bits.append(f"default {S.format_value(s, self.ed.default(s.key))}")
        if s.choices:
            bits.append(f"allowed {', '.join(s.choices)}")
        bits.append("applies after a daemon restart" if s.restart == S.RESTART_DAEMON
                    else "applies after restarting the daemon and the clients")
        env = self.ed.env_override(s.key)
        if env:
            bits.append(f"${env} is set and wins over the file")
        meta.update(" · ".join(bits))
        value = self.ed.value(s.key)
        widgets: list[Any] = []
        if s.secret:
            widgets.append(Input(placeholder="type a new value (hidden)", password=True, classes="edit-input"))
            widgets.append(Button("Set", classes="edit-apply", compact=True))
            if s.name == "static_token":
                widgets.append(Button("Generate", classes="edit-generate", compact=True))
        elif s.kind == "bool":
            widgets.append(Switch(value=bool(value), classes="edit-switch"))
            widgets.append(Static(" true" if value else " false", classes="edit-switch-label"))
        elif s.kind == "choice":
            options = [(c, c) for c in s.choices]
            current = value if value in s.choices else Select.NULL
            widgets.append(Select(options, value=current, allow_blank=False, classes="edit-select"))
        else:
            text = S.format_value(s, value) if s.kind in ("list", "table") else str(value)
            if s.kind == "table":
                text = text.strip()
            widgets.append(Input(value=text, classes="edit-input",
                                 placeholder={"int": "a whole number", "list": '["a", "b"] or a, b',
                                              "table": '{ key = "value" }'}.get(s.kind, "")))
            widgets.append(Button("Apply", classes="edit-apply", compact=True))
        if not s.secret:
            widgets.append(Button("Default", classes="edit-reset", compact=True,
                                  disabled=not self.ed.is_set(s.key)))
        field.mount(*widgets)
        self.query_one("#editor-help", Static).update(s.help or "")

    def _stage(self, value: Any) -> None:
        s, ed = self.current, self.ed
        if s is None or ed is None:
            return
        try:
            ed.set(s.key, value)
        except S.SettingsError as exc:
            self.query_one("#editor-error", Static).update(str(exc))
            return
        self.query_one("#editor-error", Static).update("")
        self.refresh_row(s)

    @on(Switch.Changed, ".edit-switch")
    def _switch(self, event: Switch.Changed) -> None:
        if self.current and self.ed and event.value != self.ed.value(self.current.key):
            self._stage(event.value)
        for label in self.query(".edit-switch-label"):
            label.update(" true" if event.value else " false")

    @on(Select.Changed, ".edit-select")
    def _select(self, event: Select.Changed) -> None:
        if (self.current and self.ed and event.value is not Select.NULL
                and event.value != self.ed.value(self.current.key)):
            self._stage(event.value)

    @on(Button.Pressed, ".edit-apply")
    @on(Input.Submitted, ".edit-input")
    def _apply(self) -> None:
        s = self.current
        if s is None:
            return
        text = self.query_one(".edit-input", Input).value
        if s.secret:
            if not text:
                self.query_one("#editor-error", Static).update("Type the new value first.")
                return
            self._stage(text)
            self.query_one(".edit-input", Input).value = ""
            return
        try:
            value = S.parse_value(s, text)
        except S.SettingsError as exc:
            self.query_one("#editor-error", Static).update(str(exc))
            return
        self._stage(value)

    @on(Button.Pressed, ".edit-generate")
    def _generate(self) -> None:
        self._stage(secrets.token_urlsafe(32))
        self.app.notify("A new static token is staged; Save writes it. It is never shown here.")

    @on(Button.Pressed, ".edit-reset")
    def _reset(self) -> None:
        s, ed = self.current, self.ed
        if s is None or ed is None:
            return
        ed.reset(s.key)
        self.refresh_row(s)
        self.show(s)

    # -- saving -------------------------------------------------------------------------

    @on(Button.Pressed, "#settings-discard")
    def discard(self) -> None:
        keep = self.current.key if self.current else None
        self.load()
        if keep:
            self.fill_table(keep)
        self.app.notify("Unsaved changes discarded.")

    @on(Button.Pressed, "#settings-save")
    def save(self) -> None:
        if self.ed is None or not self.ed.changed:
            return
        risky = [k for k in self.ed.changed if k in SENSITIVE or k.startswith("auth.")]
        if risky:
            from .app import Confirm

            lines = [f"  {k}: {SENSITIVE.get(k, 'authentication for remote clients')}" for k in risky]
            self.app.push_screen(Confirm("Save these changes?\n\n" + "\n".join(lines), yes="Save"),
                                 lambda yes: self._write() if yes else None)
            return
        self._write()

    def _write(self) -> None:
        assert self.ed is not None
        keep = self.current.key if self.current else None
        try:
            result = self.ed.save()
        except S.SettingsError as exc:
            self.app.notify(str(exc), title="Nothing was written", severity="error", timeout=15)
            return
        advice = S.restart_advice(result.restart)
        self.app.notify(f"Saved {len(result.changed)} change(s). {advice}".strip()
                        + (f"\nBackup: {result.backup}" if result.backup else ""), timeout=10)
        if result.restart:
            self.restart_level = result.restart
            self.query_one("#settings-restart").display = True
        self.load()
        if keep:
            self.fill_table(keep)
        self.app.reload_config()
        self.app.refresh_grant()
        self.app.load_status()

    @on(Button.Pressed, "#settings-restart")
    def _restart(self) -> None:
        self.app.action_restart()

    def pending(self) -> int:
        return len(self.ed.changed) if self.ed else 0
