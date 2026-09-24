"""Every pcbridge setting in one registry, and an editor that changes
config.toml without losing anything.

The registry is built from what the loader reads (`config.py`): the top-level
keys, the fixed sections, every `DesktopSpec` field and every field of each
`[agents.NAME]` block in the file. A setting's description is the comment
above it in `config.example.toml`, so the documentation has one source.

`ConfigEditor` edits with tomlkit, which keeps every comment and every byte it
was not asked to change. A save:

1. refuses if the file changed on disk since it was read;
2. renders the new text and checks it with the real loader, on a temporary
   copy, so a file that would not load is never written;
3. copies the old file to a timestamped backup next to it;
4. writes the new text atomically, mode 0600 (it holds secrets).

The password and the static token are secret: nothing here returns their
value for display, only whether they are set.
"""

from __future__ import annotations

import dataclasses
import os
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit.items import AoT, Array, Comment, Table, Whitespace

from . import assets as assetslib
from . import config as configlib
from .config import AgentSpec, DesktopSpec

# What a change needs before it takes effect.
RESTART_DAEMON = "daemon"      # the daemon reads it at start
RESTART_CLIENTS = "clients"    # the daemon, then the clients (the tool list changes)

KINDS = ("bool", "int", "str", "choice", "list", "table")

# Environment variables that win over the file (see `_load_config`).
ENV_OVERRIDES = {
    "public_url": "PCBRIDGE_PUBLIC_URL",
    "auth.password": "PCBRIDGE_PASSWORD",
    "auth.static_token": "PCBRIDGE_STATIC_TOKEN",
    "auth.manual_redirect": "PCBRIDGE_MANUAL_REDIRECT",
}

# Keys the loader reads that are not offered for editing, with the reason.
NOT_EDITABLE = {
    "config_version": "written by the migration; changing it by hand breaks upgrades",
    "desktop.keyboard_layout": "not used any more; kept only so older files still load",
}

SECTION_TITLES = {
    "": "General",
    "server": "Server",
    "auth": "Authentication",
    "paths": "Paths",
    "limits": "Limits",
    "native": "Native helper",
    "desktop": "Desktop control",
    "tools": "Tools",
}


class SettingsError(Exception):
    """A setting could not be read, changed or saved; the message says why."""


@dataclass(frozen=True)
class Setting:
    key: str                     # dotted: "port", "desktop.enabled", "agents.claude.models"
    section: str                 # "" for top-level keys, "desktop", "agents.claude"
    name: str                    # the key inside its section
    kind: str                    # one of KINDS
    default: Any
    choices: tuple[str, ...] = ()
    secret: bool = False
    restart: str = RESTART_DAEMON
    help: str = ""

    @property
    def section_title(self) -> str:
        if self.section.startswith("agents."):
            return f"Agent: {self.section.split('.', 1)[1]}"
        return SECTION_TITLES.get(self.section, self.section)


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

# (kind, default, choices) of the keys outside [desktop] and [agents.*].
# The defaults are the loader's; a contract test compares them.
_FIXED: dict[str, tuple[str, Any, tuple[str, ...]]] = {
    "default_agent": ("str", "claude", ()),
    "public_url": ("str", "", ()),
    "host": ("str", "127.0.0.1", ()),
    "port": ("int", 8765, ()),
    "mcp_path": ("str", "/mcp", ()),
    "server.inline_images": ("choice", "true", ("true", "false", "auto")),
    "auth.password": ("str", "", ()),
    "auth.static_token": ("str", "", ()),
    "auth.access_token_ttl": ("int", 43200, ()),
    "auth.refresh_token_ttl": ("int", 7776000, ()),
    "auth.auth_code_ttl": ("int", 300, ()),
    "auth.max_failed_attempts": ("int", 8, ()),
    "auth.lockout_seconds": ("int", 900, ()),
    "auth.manual_redirect": ("bool", False, ()),
    "paths.default_workdir": ("str", "~", ()),
    "paths.state_dir": ("str", "", ()),
    "limits.max_output_chars": ("int", 12000, ()),
    "limits.default_job_timeout": ("int", 1800, ()),
    "limits.max_sync_timeout": ("int", 120, ()),
    "limits.audit_max_bytes": ("int", 5_000_000, ()),
    "native.capture": ("choice", "auto", ("auto", "python", "rust")),
    "native.input": ("choice", "auto", ("auto", "python", "rust")),
    "native.accessibility": ("choice", "auto", ("auto", "python", "rust")),
    "native.binary_path": ("str", "", ()),
    "tools.profile": ("choice", "full", configlib.TOOL_PROFILES),
}

_DESKTOP_CHOICES = {
    "capture_backend": ("auto", "screencast", "gnome-screenshot"),
}

_SECRETS = {"auth.password", "auth.static_token"}

# Descriptions for keys config.example.toml documents in a block rather than
# above the key itself.
_HELP = {
    "paths.state_dir": (
        "Job records, logs, the grant state and the OAuth database live here. "
        "Empty = $XDG_STATE_HOME/pcbridge (~/.local/state/pcbridge)."
    ),
    "tools.profile": (
        "Which tools this server offers.\n"
        "  full     every tool (default)\n"
        "  core     no desktop tools: agents, jobs, shell, files, tmux, status\n"
        "  desktop  the desktop tools plus job and status tools\n"
        "A smaller set saves the client's context. Restart pcbridge and the "
        "clients after a change: clients cache the tool list."
    ),
}

_AGENT_HELP = {
    "enabled": "Whether agent_run and computer_task may use this agent.",
    "description": "One line shown to clients in the agent list.",
    "command": (
        "The command, as a list; the first element is the executable. {prompt} "
        "is replaced by the prompt."
    ),
    "resume_args": "Arguments added to the command to resume a session; {session_id} is replaced.",
    "parser": 'How the output is read: "claude_stream_json", "agy_json" or "plain".',
    "pty": "Run the command in a pseudo-terminal, for CLIs that print nothing without a TTY.",
    "model_args": 'Syntax of the model flag, for example ["--model", "{model}"].',
    "effort_args": 'Syntax of the effort flag, for example ["--effort", "{effort}"].',
    "models": "Models that can be chosen freely.",
    "restricted_models": "Models chosen only when the user names them explicitly; never a default.",
    "blocked_models": "Models that can never be chosen.",
    "efforts": "Effort levels the agent accepts.",
    "model_efforts": (
        "Allowed efforts per model; overrides `efforts`. An empty list means the "
        "model takes no effort flag."
    ),
    "default_model": "Model used when a call gives none. Must be in `models`.",
    "default_effort": "Effort used when a call gives none (after `model_effort`).",
    "model_effort": "Default effort per model.",
    "effort_required_with_model": "Require an effort whenever a model is given.",
    "aliases": "Free text to canonical name, for models and efforts alike.",
}

_AGENT_CHOICES = {
    "parser": ("claude_stream_json", "agy_json", "plain"),
}


def _kind_of(annotation: str, value: Any) -> str:
    text = str(annotation)
    if text.startswith("bool"):
        return "bool"
    if text.startswith("int"):
        return "int"
    if text.startswith("list"):
        return "list"
    if text.startswith("dict"):
        return "table"
    if isinstance(value, bool):
        return "bool"
    return "str"


def _field_default(f: dataclasses.Field) -> Any:
    if f.default is not dataclasses.MISSING:
        return f.default
    if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
        return f.default_factory()  # type: ignore[misc]
    return None


def _clean_comment(lines: list[str]) -> str:
    out: list[str] = []
    for line in lines:
        text = line.strip()
        if text.startswith("#"):
            text = text[1:]
            if text.startswith(" "):
                text = text[1:]
        if set(text.strip()) <= {"-"} and text.strip():
            continue  # a banner rule
        out.append(text.rstrip())
    # Collapse to paragraphs: lines joined by spaces, "#" alone breaks them.
    paras: list[str] = []
    cur: list[str] = []
    for text in out:
        if not text.strip():
            if cur:
                paras.append(" ".join(cur))
                cur = []
            continue
        # Keep indented lines (lists of values) on their own line.
        if text.startswith(" ") and cur:
            paras.append(" ".join(cur))
            cur = []
        cur.append(text.strip() if not text.startswith(" ") else "  " + text.strip())
    if cur:
        paras.append(" ".join(cur))
    return "\n".join(paras).strip()


def _walk_help(container: Any, prefix: str, out: dict[str, str]) -> None:
    block: list[str] = []
    for key, item in container.body:
        if isinstance(item, Comment):
            block.append(item.as_string())
            continue
        if isinstance(item, Whitespace):
            if "\n" in item.as_string():
                block = []
            continue
        if key is None:
            continue
        name = key.key
        dotted = f"{prefix}{name}"
        if isinstance(item, Table):
            _walk_help(item.value, f"{dotted}.", out)
            block = []
            continue
        if isinstance(item, AoT):
            block = []
            continue
        text = _clean_comment(block)
        inline = item.trivia.comment.strip().lstrip("#").strip()
        if inline:
            text = f"{text} ({inline})" if text else inline
        if text:
            out[dotted] = text


def example_help() -> dict[str, str]:
    """The comment above each key in config.example.toml, by dotted key."""
    try:
        text = assetslib.asset_path("config.example.toml").read_text(encoding="utf-8")
    except (OSError, FileNotFoundError):
        return {}
    out: dict[str, str] = {}
    _walk_help(tomlkit.parse(text), "", out)
    return out


def registry(agent_names: list[str] | tuple[str, ...] = ()) -> list[Setting]:
    """Every editable setting, in the order the example file documents them.

    `agent_names` adds the fields of each `[agents.NAME]` block.
    """
    helps = example_help()
    out: list[Setting] = []
    for key, (kind, default, choices) in _FIXED.items():
        section, _, name = key.rpartition(".")
        restart = RESTART_CLIENTS if key in ("tools.profile", "default_agent") else RESTART_DAEMON
        out.append(Setting(key, section, name, kind, default, choices,
                           secret=key in _SECRETS, restart=restart,
                           help=_HELP.get(key) or helps.get(key, "")))
    for f in dataclasses.fields(DesktopSpec):
        key = f"desktop.{f.name}"
        if key in NOT_EDITABLE:
            continue
        default = _field_default(f)
        kind = _kind_of(f.type, default)
        choices = _DESKTOP_CHOICES.get(f.name, ())
        if choices:
            kind = "choice"
        out.append(Setting(key, "desktop", f.name, kind, default, choices,
                           help=_HELP.get(key) or helps.get(key, "")))
    for agent in agent_names:
        for f in dataclasses.fields(AgentSpec):
            if f.name == "name":
                continue
            default = _field_default(f)
            kind = _kind_of(f.type, default)
            choices = _AGENT_CHOICES.get(f.name, ())
            if choices:
                kind = "choice"
            out.append(Setting(f"agents.{agent}.{f.name}", f"agents.{agent}", f.name, kind,
                               default, choices, restart=RESTART_CLIENTS,
                               help=_AGENT_HELP.get(f.name, "")))
    # Keep the document order of the fixed sections: top, server, auth, ...
    order = ["", "server", "auth", "paths", "limits", "native", "desktop", "tools"]
    rank = {s: i for i, s in enumerate(order)}
    return sorted(out, key=lambda s: rank.get(s.section, len(order)))


# ---------------------------------------------------------------------------
# Values: text <-> Python <-> TOML
# ---------------------------------------------------------------------------

_TRUE = {"true", "yes", "on", "1"}
_FALSE = {"false", "no", "off", "0"}


def parse_value(setting: Setting, text: str) -> Any:
    """Turn what a person typed into the value for `setting`, or raise SettingsError."""
    raw = text.strip()
    if setting.kind == "bool":
        low = raw.lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise SettingsError(f"{setting.key} takes true or false, not {raw!r}.")
    if setting.kind == "int":
        try:
            return int(raw.replace("_", ""))
        except ValueError:
            raise SettingsError(f"{setting.key} takes a whole number, not {raw!r}.") from None
    if setting.kind == "choice":
        low = raw.strip('"').lower()
        if low not in setting.choices:
            raise SettingsError(f"{setting.key} takes one of: {', '.join(setting.choices)}.")
        return low
    if setting.kind == "str":
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            try:
                return str(tomllib.loads(f"v = {raw}")["v"])
            except tomllib.TOMLDecodeError:
                return raw[1:-1]
        return text if setting.secret else raw
    if setting.kind == "list":
        if raw.startswith("["):
            try:
                value = tomllib.loads(f"v = {raw}")["v"]
            except tomllib.TOMLDecodeError as exc:
                raise SettingsError(f"{setting.key}: not a TOML list ({exc}).") from None
            return [str(x) for x in value]
        return [part.strip() for part in raw.split(",") if part.strip()]
    if setting.kind == "table":
        body = raw if raw.startswith("{") else "{" + raw + "}"
        try:
            value = tomllib.loads(f"v = {body}")["v"]
        except tomllib.TOMLDecodeError as exc:
            raise SettingsError(
                f"{setting.key}: not a TOML table, for example {{ opus = \"high\" }} ({exc})."
            ) from None
        return value
    raise SettingsError(f"{setting.key}: unknown kind {setting.kind}")


def _to_toml(setting: Setting, value: Any) -> Any:
    """The value as it is written to the file."""
    if setting.key == "server.inline_images" and value in ("true", "false"):
        return value == "true"
    return value


def _from_toml(setting: Setting, value: Any) -> Any:
    """A value read from the file, normalized to the setting's kind."""
    if isinstance(value, tomlkit.items.Item):
        value = value.unwrap()
    if setting.kind == "choice":
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value).strip().lower()
    return value


def format_value(setting: Setting, value: Any) -> str:
    """How a value is shown to a person. Secrets are never shown."""
    if setting.secret:
        return "set (hidden)" if value else "not set"
    if setting.kind == "bool":
        return "true" if value else "false"
    if setting.kind in ("list", "table"):
        item = tomlkit.item(value)
        return item.as_string()
    if setting.kind == "str":
        return f'"{value}"' if value == "" else str(value)
    return str(value)


# ---------------------------------------------------------------------------
# The editor
# ---------------------------------------------------------------------------


@dataclass
class SaveResult:
    path: Path
    backup: Path | None
    changed: list[str]
    restart: str | None          # RESTART_DAEMON, RESTART_CLIENTS or None


def _last_plain_key(container: Any) -> str | None:
    last = None
    for key, item in container.body:
        if key is not None and not isinstance(item, (Table, AoT)):
            last = key.key
    return last


class ConfigEditor:
    """Read, change and save one config file."""

    def __init__(self, path: Path | str | None = None) -> None:
        if path is None:
            path, _kind = configlib.locate_config()
        self.path = Path(path)
        try:
            self._text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SettingsError(f"Cannot read {self.path}: {exc.strerror}.") from None
        try:
            self.doc = tomlkit.parse(self._text)
        except Exception as exc:  # noqa: BLE001 - tomlkit raises several types
            raise SettingsError(f"{self.path} is not valid TOML: {exc}") from None
        self.changed: list[str] = []
        agents = self.doc.get("agents") or {}
        self.agent_names = [str(n) for n in agents.keys()] if isinstance(agents, dict) else []
        self.settings = registry(self.agent_names)
        self._by_key = {s.key: s for s in self.settings}

    # -- lookup ---------------------------------------------------------------

    def setting(self, key: str) -> Setting:
        key = key.strip()
        if key in self._by_key:
            return self._by_key[key]
        if key in NOT_EDITABLE:
            raise SettingsError(f"{key} is not editable: {NOT_EDITABLE[key]}.")
        if key.startswith("agents."):
            agent = key.split(".")[1] if key.count(".") >= 2 else ""
            if agent and agent not in self.agent_names:
                raise SettingsError(
                    f"There is no [agents.{agent}] block. Defined: "
                    f"{', '.join(self.agent_names) or '-'}. Add a new agent in {self.path}."
                )
        close = [s.key for s in self.settings if key.split(".")[-1] == s.name]
        hint = f" Did you mean: {', '.join(close[:4])}?" if close else ""
        raise SettingsError(f"Unknown setting {key!r}.{hint} `pcbridge settings` lists them all.")

    def _table(self, section: str, create: bool = False) -> Any:
        node: Any = self.doc
        if not section:
            return node
        for part in section.split("."):
            nxt = node.get(part) if hasattr(node, "get") else None
            if nxt is None:
                if not create:
                    return None
                nxt = tomlkit.table(is_super_table=False)
                node[part] = nxt
                nxt = node[part]
            node = nxt
        return node

    def is_set(self, key: str) -> bool:
        s = self.setting(key)
        table = self._table(s.section)
        if table is not None and s.name in table:
            return True
        return s.key == "server.inline_images" and "inline_images" in self.doc

    def value(self, key: str) -> Any:
        """The value in effect from the file: the file's, or the default."""
        s = self.setting(key)
        table = self._table(s.section)
        if table is not None and s.name in table:
            return _from_toml(s, table[s.name])
        if s.key == "server.inline_images" and "inline_images" in self.doc:
            return _from_toml(s, self.doc["inline_images"])
        return self.default(key)

    def default(self, key: str) -> Any:
        """The default for this file: an older file keeps its version's defaults."""
        s = self.setting(key)
        try:
            version = int(self.doc.get("config_version", 1))
        except (TypeError, ValueError):
            version = 1
        for newer in range(version + 1, configlib.CONFIG_VERSION + 1):
            for section, name, value in configlib._PINNED_DEFAULTS.get(newer, []):
                if f"{section}.{name}" == s.key:
                    return tomllib.loads(f"v = {value}")["v"]
        return s.default

    def env_override(self, key: str) -> str | None:
        name = ENV_OVERRIDES.get(key)
        return name if name and os.environ.get(name) else None

    # -- changes --------------------------------------------------------------

    def set(self, key: str, value: Any) -> None:
        s = self.setting(key)
        if s.kind == "choice":
            value = _from_toml(s, value)
            if value not in s.choices:
                raise SettingsError(f"{s.key} takes one of: {', '.join(s.choices)}.")
        new = _to_toml(s, value)
        table = self._table(s.section, create=True)
        if s.name in table:
            old = table[s.name]
            if s.kind == "table" and isinstance(old, (Table, tomlkit.items.InlineTable)):
                self._update_table(old, dict(new))
            else:
                item = tomlkit.item(new)
                if isinstance(old, Array) and isinstance(item, Array) and "\n" in old.as_string():
                    item.multiline(True)
                table[s.name] = item
        else:
            container = table.value if isinstance(table, Table) else table
            after = _last_plain_key(container)
            if after is None or container is self.doc:
                if container is self.doc:
                    self._insert_top(s.name, new)
                elif not container.body:
                    container.append(s.name, tomlkit.item(new))
                else:
                    container._insert_at(0, s.name, tomlkit.item(new))
            else:
                container._insert_after(after, s.name, tomlkit.item(new))
        if key not in self.changed:
            self.changed.append(s.key)

    def _insert_top(self, name: str, value: Any) -> None:
        """A top-level key goes after the last top-level key, before any table."""
        body = self.doc.body
        after = None
        for k, item in body:
            if isinstance(item, (Table, AoT)):
                break
            if k is not None:
                after = k.key
        if after is None:
            self.doc._insert_at(0, name, tomlkit.item(value))
        else:
            self.doc._insert_after(after, name, tomlkit.item(value))

    @staticmethod
    def _update_table(table: Any, new: dict[str, Any]) -> None:
        for k in [k for k in table.keys() if k not in new]:
            del table[k]
        for k, v in new.items():
            if k not in table or table[k].unwrap() != v:
                table[k] = v

    def reset(self, key: str) -> None:
        """Remove the key from the file, so the default applies."""
        s = self.setting(key)
        table = self._table(s.section)
        removed = False
        if table is not None and s.name in table:
            del table[s.name]
            removed = True
        if s.key == "server.inline_images" and "inline_images" in self.doc:
            del self.doc["inline_images"]
            removed = True
        if removed and s.key not in self.changed:
            self.changed.append(s.key)

    # -- saving ---------------------------------------------------------------

    def render(self) -> str:
        return self.doc.as_string()

    def validate(self, text: str | None = None) -> Any:
        """Load `text` with the real loader; return the Config or raise SettingsError."""
        text = self.render() if text is None else text
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise SettingsError(f"The new file would not be valid TOML: {exc}") from None
        fd, tmp = tempfile.mkstemp(prefix=".config-check-", suffix=".toml", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.chmod(tmp, 0o600)
            try:
                return configlib.load_config(tmp, check_state=False)
            except configlib.ConfigError as exc:
                raise SettingsError(exc.message.replace(tmp, str(self.path))) from None
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def restart_needed(self) -> str | None:
        levels = {self.setting(k).restart for k in self.changed}
        if RESTART_CLIENTS in levels:
            return RESTART_CLIENTS
        return RESTART_DAEMON if levels else None

    def save(self) -> SaveResult:
        if not self.changed:
            return SaveResult(self.path, None, [], None)
        text = self.render()
        try:
            on_disk = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SettingsError(f"Cannot read {self.path}: {exc.strerror}.") from None
        if on_disk != self._text:
            raise SettingsError(
                f"{self.path} changed on disk since it was read; nothing was written. "
                "Reload and make the change again."
            )
        self.validate(text)
        backup = configlib._backup(self.path)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)
        result = SaveResult(self.path, backup, list(self.changed), self.restart_needed())
        self._text = text
        self.changed = []
        return result


def restart_advice(level: str | None) -> str:
    if level == RESTART_CLIENTS:
        return ("Takes effect after `pcbridge restart`; restart the MCP clients too, "
                "they cache the tool list.")
    if level == RESTART_DAEMON:
        return "Takes effect after `pcbridge restart`."
    return ""


__all__ = [
    "ConfigEditor", "NOT_EDITABLE", "RESTART_CLIENTS", "RESTART_DAEMON", "SaveResult",
    "Setting", "SettingsError", "example_help", "format_value", "parse_value",
    "registry", "restart_advice",
]
