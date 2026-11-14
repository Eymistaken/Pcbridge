"""The Advanced tab: every setting of config.toml, each with the control its
kind calls for.

The sections on the left (a `●` marks one with unsaved changes), the
section's keys on the right, one block per key: the mark (`●` unsaved,
`◦` differs from the default), the key, when it applies, the control, the
description, the default and the allowed range. Changes are staged in one
`ConfigEditor` and written together by Save: one check with the real
loader, one backup, one atomic write. Secrets are stored as hashes and
never shown; they can only be replaced.
"""

from __future__ import annotations

import secrets
from typing import Any

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, OptionList, Static, Switch
from textual.widgets.option_list import Option

from .. import settings as S
from .pane import Pane
from .theme import PALETTE
from .widgets import CheckLine, NumberInput, RestartBanner, Segmented, key_label, restart_tag

CSS = """
SettingsPane #settings-top {
    height: 1;
    margin-bottom: 1;
}
SettingsPane #settings-filter {
    width: 40;
}
SettingsPane #settings-changed-only {
    margin-left: 2;
}
SettingsPane #settings-message {
    width: 1fr;
    text-align: right;
    color: $pb-dim;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}
SettingsPane #settings-body {
    height: 1fr;
}
SettingsPane #settings-sections {
    width: 24;
    height: 1fr;
    border: none;
    background: ansi_default;
    padding: 0;
    margin-right: 2;
}
SettingsPane.narrow #settings-sections {
    width: 18;
}
SettingsPane #settings-sections > .option-list--option-highlighted {
    background: $pb-selection;
}
SettingsPane #settings-keys {
    width: 1fr;
    height: 1fr;
    scrollbar-size-vertical: 1;
}
SettingsPane .key-block {
    height: auto;
    margin-bottom: 1;
}
SettingsPane .key-block.current {
    background: $pb-selection-hover;
}
SettingsPane .key-head {
    height: auto;
}
SettingsPane .key-title {
    width: 1fr;
}
SettingsPane .key-control {
    width: auto;
    height: auto;
}
SettingsPane .key-secret {
    width: auto;
}
SettingsPane .key-control Input {
    width: 36;
}
SettingsPane.narrow .key-control Input {
    width: 24;
}
SettingsPane .key-help {
    color: $pb-muted;
    padding-left: 2;
}
SettingsPane .key-meta {
    color: $pb-dim;
    padding-left: 2;
}
SettingsPane .key-error {
    color: $pb-error;
    padding-left: 2;
}
SettingsPane #settings-actions {
    height: 2;
    border-top: solid $pb-rule;
    padding-top: 0;
}
SettingsPane #settings-pending {
    width: 1fr;
}
"""

# Changes that get one more question before they are written.
SENSITIVE = {
    "desktop.enabled": "agents can drive this desktop once they open the grant",
    "host": "the address the daemon listens on",
    "port": "the port the daemon and the tunnel use",
    "public_url": "the address remote clients use",
}


def block_id(key: str) -> str:
    return "key-" + key.replace(".", "--")


def key_of(block_id_: str) -> str:
    return block_id_.removeprefix("key-").replace("--", ".")


class SettingsPane(Pane):
    FOOTER = [("/", "Filter", "key:slash"), ("↑↓", "Section", "key:none"),
              ("space", "Toggle", "key:space"), ("+-", "Number", "key:none"),
              ("^s", "Save", "key:ctrl_s"), ("^z", "Reset key", "key:ctrl_z")]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.ed: S.ConfigEditor | None = None
        self.section = ""
        self.current: str | None = None      # the key whose block has the focus
        self.generated_token: str | None = None
        self.quiet = False

    def compose(self) -> ComposeResult:
        yield RestartBanner()
        with Horizontal(id="settings-top"):
            yield Input(placeholder="Filter: key or description", id="settings-filter")
            yield CheckLine("Changed from default only", id="settings-changed-only")
            yield Static("", id="settings-message")
        with Horizontal(id="settings-body"):
            yield OptionList(id="settings-sections")
            yield VerticalScroll(id="settings-keys")
        with Horizontal(id="settings-actions"):
            yield Static("", id="settings-pending")
            yield Button("Discard", id="settings-discard", compact=True)
            yield Button(key_label("Save", "^s"), id="settings-save", classes="primary", compact=True)

    def on_mount(self) -> None:
        self.set_class(self.app.size.width < 100, "narrow")
        self.load()

    def on_resize(self) -> None:
        self.set_class(self.app.size.width < 100, "narrow")

    def config_changed(self) -> None:
        # Another tab saved: read the file again unless changes wait here
        # (their Save then refuses, since the file changed on disk).
        if self.ed is None or not self.ed.changed:
            self.load()

    # -- loading ------------------------------------------------------------------

    def load(self) -> None:
        try:
            self.ed = self.app.backend.editor()
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - config errors are shown here
            self.ed = None
            self.query_one("#settings-message", Static).update(
                Text(f"The settings cannot be read: {getattr(exc, 'message', exc)}",
                     style=f"bold {PALETTE['error']}"))
        else:
            self.query_one("#settings-message", Static).update(str(self.ed.path))
        self.fill_sections()
        self.fill_keys()
        self.update_pending()

    def sections(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for s in self.ed.settings if self.ed else []:
            if s.section not in [k for k, _t in out]:
                out.append((s.section, s.section_title))
        return out

    def fill_sections(self) -> None:
        menu = self.query_one("#settings-sections", OptionList)
        menu.clear_options()
        sections = self.sections()
        if not sections:
            return
        if self.section not in [k for k, _t in sections]:
            self.section = sections[0][0]
        for key, title in sections:
            menu.add_option(Option(self._section_label(key, title), id=key or "general"))
        menu.highlighted = [k for k, _t in sections].index(self.section)

    def _section_label(self, key: str, title: str) -> Text:
        dirty = bool(self.ed and any(self.ed.setting(k).section == key for k in self.ed.changed))
        out = Text()
        out.append("● " if dirty else "  ", style=PALETTE["warning"])
        out.append(title)
        return out

    def refresh_sections(self) -> None:
        menu = self.query_one("#settings-sections", OptionList)
        for i, (key, title) in enumerate(self.sections()):
            if i < menu.option_count:
                menu.replace_option_prompt_at_index(i, self._section_label(key, title))

    def visible(self) -> list[S.Setting]:
        if self.ed is None:
            return []
        words = self.query_one("#settings-filter", Input).value.lower().split()
        changed_only = self.query_one("#settings-changed-only", CheckLine).value
        if words:
            found = [s for s in self.ed.settings if all(w in f"{s.key} {s.help}".lower() for w in words)]
        else:
            found = [s for s in self.ed.settings if s.section == self.section]
        if changed_only:
            found = [s for s in found if s.key in self.ed.changed or self._differs(s)]
        return found

    def _differs(self, s: S.Setting) -> bool:
        assert self.ed is not None
        return self.ed.is_set(s.key) and self.ed.value(s.key) != self.ed.default(s.key)

    # -- the key blocks --------------------------------------------------------------

    def fill_keys(self) -> None:
        """Show the visible keys' blocks (the old ones are removed first)."""
        self.run_worker(self._fill_keys(), group="settings-fill", exclusive=True)

    async def _fill_keys(self) -> None:
        box = self.query_one("#settings-keys", VerticalScroll)
        rows = self.visible()
        filtered = bool(self.query_one("#settings-filter", Input).value.strip())
        self.quiet = True
        try:
            await box.remove_children()
            if not rows:
                await box.mount(Static("No setting matches." if self.ed else "", classes="key-help"))
                return
            await box.mount_all([self._block(s, filtered) for s in rows])
        finally:
            self.call_after_refresh(self._unquiet)

    def _unquiet(self) -> None:
        self.quiet = False

    def _title(self, s: S.Setting, filtered: bool) -> Text:
        assert self.ed is not None
        out = Text()
        if s.key in self.ed.changed:
            out.append("● ", style=PALETTE["warning"])
        elif self._differs(s):
            out.append("◦ ", style=PALETTE["warning"])
        else:
            out.append("  ")
        out.append(s.key if filtered or not s.section else s.name, style="bold")
        out.append("  ")
        out.append_text(restart_tag(s.restart))
        return out

    def _meta(self, s: S.Setting) -> Text:
        assert self.ed is not None
        bits = []
        unit = S.unit_of(s.key)
        if not s.secret:
            default = S.format_value(s, self.ed.default(s.key))
            bits.append(f"default {default}{' ' + unit if unit and s.kind == 'int' else ''}")
        span = S.describe_bounds(s.key)
        if span:
            bits.append(span)
        env = self.ed.env_override(s.key)
        if env:
            bits.append(f"${env} is set and wins over the file")
        return Text(" · ".join(bits))

    def _control(self, s: S.Setting) -> Any:
        assert self.ed is not None
        value = self.ed.value(s.key)
        cid = f"ctl-{block_id(s.key)}"
        if s.secret:
            return Horizontal(Static(Text("✓ set · hashed  " if value else "not set  ",
                                          style=PALETTE["ok" if value else "dim"]), classes="key-secret"),
                              Button("Replace…", id=cid, classes="edit-replace", compact=True),
                              classes="key-control")
        if s.kind == "bool":
            return Switch(value=bool(value), id=cid, classes="edit-switch")
        if s.kind == "choice" and s.choices:
            return Segmented([(c, c) for c in s.choices], value=str(value), id=cid, classes="edit-choice")
        if s.kind == "int":
            low, high, zero, _unit = S.BOUNDS.get(s.key, (None, None, False, ""))
            return NumberInput(int(value or 0), S.unit_of(s.key), low=0 if zero else low, high=high,
                               id=cid, classes="edit-number")
        text = S.format_value(s, value) if s.kind in ("list", "table") else str(value)
        return Input(value=text.strip(), id=cid, classes=f"edit-input edit-{s.kind}",
                     placeholder={"list": '["a", "b"] or a, b', "table": '{ key = "value" }'}.get(s.kind, ""))

    def _block(self, s: S.Setting, filtered: bool) -> Vertical:
        head = Horizontal(Static(self._title(s, filtered), classes="key-title"), self._control(s),
                          classes="key-head")
        parts: list[Any] = [head]
        if s.help:
            parts.append(Static(Text(" ".join(s.help.split()) if "\n" not in s.help else s.help),
                                classes="key-help"))
        parts.append(Static(self._meta(s), classes="key-meta"))
        parts.append(Static("", classes="key-error"))
        return Vertical(*parts, id=block_id(s.key), classes="key-block")

    def refresh_block(self, key: str) -> None:
        assert self.ed is not None
        s = self.ed.setting(key)
        try:
            block = self.query_one(f"#{block_id(key)}")
        except Exception:  # noqa: BLE001 - filtered out
            return
        filtered = bool(self.query_one("#settings-filter", Input).value.strip())
        block.query_one(".key-title", Static).update(self._title(s, filtered))
        self.update_pending()
        self.refresh_sections()

    def _error(self, key: str, text: str) -> None:
        try:
            self.query_one(f"#{block_id(key)} .key-error", Static).update(text)
        except Exception:  # noqa: BLE001
            pass

    def update_pending(self) -> None:
        n = len(self.ed.changed) if self.ed else 0
        pending = self.query_one("#settings-pending", Static)
        if n:
            out = Text()
            out.append(f"● {n} unsaved change{'s' if n != 1 else ''}", style=f"bold {PALETTE['warning']}")
            out.append(" · checked with the real loader, backed up before writing", style=PALETTE["muted"])
            pending.update(out)
        else:
            pending.update(Text("No unsaved changes. ◦ marks a value that differs from the default.",
                                style=PALETTE["muted"]))
        self.query_one("#settings-save", Button).disabled = n == 0
        self.query_one("#settings-discard", Button).disabled = n == 0

    # -- staging --------------------------------------------------------------------

    def _stage(self, key: str, value: Any) -> bool:
        if self.ed is None:
            return False
        try:
            self.ed.set(key, value)
        except S.SettingsError as exc:
            self._error(key, str(exc))
            return False
        self._error(key, "")
        self.refresh_block(key)
        return True

    @staticmethod
    def _key_from(widget: Any) -> str | None:
        wid = widget.id or ""
        return key_of(wid.removeprefix("ctl-")) if wid.startswith("ctl-") else None

    @on(Switch.Changed, ".edit-switch")
    def _switch(self, event: Switch.Changed) -> None:
        key = self._key_from(event.switch)
        if key and not self.quiet and self.ed and event.value != self.ed.value(key):
            self._stage(key, event.value)

    @on(Segmented.Changed, ".edit-choice")
    def _choice(self, event: Segmented.Changed) -> None:
        key = self._key_from(event.segmented)
        if key and not self.quiet and self.ed and event.value != str(self.ed.value(key)):
            self._stage(key, event.value)

    @on(NumberInput.Changed, ".edit-number")
    def _number(self, event: NumberInput.Changed) -> None:
        key = self._key_from(event.number)
        if not key or self.quiet or self.ed is None or event.value == self.ed.value(key):
            return
        if not S.in_bounds(key, event.value):
            self._error(key, f"{key} takes {S.describe_bounds(key)}.")
            return
        self._stage(key, event.value)

    @on(Input.Changed, ".edit-str")
    def _text(self, event: Input.Changed) -> None:
        key = self._key_from(event.input)
        if key and not self.quiet and self.ed and event.value != str(self.ed.value(key)):
            self._stage(key, event.value)

    @on(Input.Submitted, ".edit-list, .edit-table")
    def _structured(self, event: Input.Submitted) -> None:
        key = self._key_from(event.input)
        if key is None or self.ed is None:
            return
        try:
            value = S.parse_value(self.ed.setting(key), event.value)
        except S.SettingsError as exc:
            self._error(key, str(exc))
            return
        self._stage(key, value)

    @on(Button.Pressed, ".edit-replace")
    def _replace(self, event: Button.Pressed) -> None:
        key = self._key_from(event.button)
        if key == "auth.password":
            # One path, with the login-password check: the Remote tab's dialog.
            remote = self.app.panes.get("remote")
            if remote is not None:
                remote.replace_password()
            return
        if key == "auth.static_token":
            token = secrets.token_urlsafe(32)
            if self._stage(key, token):
                self.generated_token = token
                self.app.notify("A new static token is staged; it is shown once, after Save.")

    def on_descendant_focus(self, event: Any) -> None:
        node = event.widget if hasattr(event, "widget") else None
        while node is not None and not (getattr(node, "id", "") or "").startswith("key-"):
            node = node.parent
        if node is not None:
            for block in self.query(".key-block.current"):
                block.remove_class("current")
            node.add_class("current")
            self.current = key_of(node.id)

    # -- navigation -----------------------------------------------------------------

    @on(OptionList.OptionHighlighted, "#settings-sections")
    def _section(self, event: OptionList.OptionHighlighted) -> None:
        section = "" if event.option.id == "general" else str(event.option.id)
        if section != self.section:
            self.section = section
            self.current = None
            self.query_one("#settings-filter", Input).value = ""
            self.fill_keys()

    def show_section(self, section: str) -> None:
        """Open one section (the Agents tab's "Every setting of NAME")."""
        keys = [k for k, _t in self.sections()]
        if section in keys:
            self.query_one("#settings-filter", Input).value = ""
            self.query_one("#settings-sections", OptionList).highlighted = keys.index(section)
            self.section = section
            self.fill_keys()

    @on(Input.Changed, "#settings-filter")
    def _filter(self) -> None:
        self.current = None
        self.fill_keys()

    @on(CheckLine.Changed, "#settings-changed-only")
    def _changed_only(self) -> None:
        self.fill_keys()

    def press_slash(self) -> None:
        self.query_one("#settings-filter", Input).focus()

    def press_space(self) -> None:
        focused = self.app.focused
        if isinstance(focused, (Switch, CheckLine)):
            focused.toggle()

    def press_ctrl_s(self) -> None:
        self.save()

    def press_ctrl_z(self) -> None:
        key, ed = self.current, self.ed
        if key is None or ed is None:
            return
        ed.reset(key)
        self.fill_keys()
        self.update_pending()
        self.refresh_sections()
        self.app.notify(f"{key} goes back to its default when you save.")

    def press_none(self) -> None:
        pass

    # -- saving -------------------------------------------------------------------------

    @on(Button.Pressed, "#settings-discard")
    def discard(self) -> None:
        self.generated_token = None
        self.ed = None
        self.load()
        self.app.notify("Unsaved changes discarded.")

    @on(Button.Pressed, "#settings-save")
    def save(self) -> None:
        if self.ed is None or not self.ed.changed:
            return
        risky = [k for k in self.ed.changed if k in SENSITIVE or k.startswith("auth.")]
        if risky:
            from .dialogs import Confirm

            lines = [f"  {k}: {SENSITIVE.get(k, 'authentication for remote clients')}" for k in risky]
            self.app.push_screen(Confirm("Save these changes?\n\n" + "\n".join(lines), yes="Save"),
                                 lambda yes: self._write() if yes else None)
            return
        self._write()

    def _write(self) -> None:
        assert self.ed is not None
        try:
            restart_keys = [k for k in self.ed.changed if self.ed.setting(k).restart != S.RESTART_NONE]
            result = self.ed.save()
        except S.SettingsError as exc:
            self.app.notify(str(exc), title="Nothing was written", severity="error", timeout=15)
            return
        self.app.notify(f"Saved {len(result.changed)} change(s)."
                        + (f"\nBackup: {result.backup}" if result.backup else ""), timeout=8)
        if result.restart in (S.RESTART_DAEMON, S.RESTART_CLIENTS):
            self.app.note_restart_due(len(restart_keys), result.restart)
        self.load()
        self.app.reload_config()
        self.app.refresh_grant()
        self.app.load_status()
        token, self.generated_token = self.generated_token, None
        if token and "auth.static_token" in result.changed:
            from .dialogs import ShowOnce

            self.app.push_screen(ShowOnce("The new static token", token,
                                          "HTTP clients send it as: Authorization: Bearer <token>"))

    def pending(self) -> int:
        return len(self.ed.changed) if self.ed else 0
