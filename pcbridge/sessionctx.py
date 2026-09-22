"""Which MCP session is this call serving?

Before 2.0 every stdio client had its own server process, so "per process"
and "per client" were the same thing. The resident daemon serves many
sessions from one process, and a few pieces of state must stay per session:

    * the short ids of the last `ui_dump` (`#1b72` must resolve against the
      dump THIS client made, never another client's window);
    * the per-second rate window of the desktop gate;
    * how screenshots are delivered (file path for local sessions, link for
      HTTP ones) and which environment agents started from it inherit.

A session is identified by `key()`. Sessions that arrive over the daemon's
unix socket carry a `SessionInfo` (set by the daemon from the relay's
preamble). Other MCP sessions (HTTP) are keyed by their `ServerSession`
object. Outside any MCP request (CLI tools, tests) the key is "process".

Context variables follow the call into FastMCP's worker threads, because
anyio copies the context for every `to_thread.run_sync` call.
"""

from __future__ import annotations

import os
import threading
import weakref
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")

# Environment variables a local client may hand to the daemon for the agents,
# shells and jobs it starts. Everything else comes from the daemon's own
# (systemd) environment. CLAUDE*/MCP_* are deliberately NOT here: inherited,
# they change how a spawned `claude` behaves (CLAUDE_CODE_EFFORT_LEVEL silently
# overrides --effort).
ENV_ALLOWLIST = ("PATH", "SSH_AUTH_SOCK", "LANG", "LANGUAGE", "PWD", "TERM")
ENV_ALLOW_PREFIXES = ("LC_",)
ENV_PROXY = ("http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "all_proxy")


def filter_env(env: dict[str, str]) -> dict[str, str]:
    """The allow-listed subset of a client's environment."""
    out = {}
    for k, v in env.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        if k in ENV_ALLOWLIST or k in ENV_PROXY or k.startswith(ENV_ALLOW_PREFIXES):
            out[k] = v
    return out


@dataclass
class SessionInfo:
    key: str
    transport: str = "stdio"  # how tools should behave: "stdio" (local) or "http"
    client: str = ""          # e.g. "claude-code", from the relay preamble
    pid: int | None = None
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    relay_version: str = ""


_current: ContextVar[SessionInfo | None] = ContextVar("pcbridge_session", default=None)


def current() -> SessionInfo | None:
    return _current.get()


def bind(info: SessionInfo | None):
    """Set the session for the current context; returns the reset token."""
    return _current.set(info)


def reset(token) -> None:
    _current.reset(token)


def key() -> str:
    info = _current.get()
    if info is not None:
        return info.key
    try:
        from mcp.server.lowlevel.server import request_ctx

        ctx = request_ctx.get()
    except (LookupError, ImportError):
        return "process"
    return f"mcp:{id(ctx.session):x}"


def transport(default: str) -> str:
    info = _current.get()
    return info.transport if info is not None else default


def client_name() -> str | None:
    info = _current.get()
    return info.client or None if info is not None else None


def job_env() -> dict[str, str]:
    """Environment overrides for a process started on behalf of this session.

    PATH is merged: the client's entries first (they found the CLI the user
    expects, e.g. an nvm version), then the daemon's entries not already there.
    """
    info = _current.get()
    if info is None or not info.env:
        return {}
    out = {k: v for k, v in info.env.items() if k != "PATH"}
    client_path = info.env.get("PATH")
    if client_path:
        parts = [p for p in client_path.split(os.pathsep) if p]
        for p in os.environ.get("PATH", "").split(os.pathsep):
            if p and p not in parts:
                parts.append(p)
        out["PATH"] = os.pathsep.join(parts)
    return out


class PerSession(Generic[T]):
    """A small map from session key to a value, bounded and thread-safe.

    HTTP sessions never tell us they ended, so the map forgets the least
    recently used sessions beyond `limit`.
    """

    def __init__(self, limit: int = 64) -> None:
        self._data: OrderedDict[str, T] = OrderedDict()
        self._lock = threading.Lock()
        self._limit = limit
        # Forget a session's value as soon as the daemon reports it ended.
        ref = weakref.WeakMethod(self.pop)

        def _forget(session: str) -> None:
            pop = ref()
            if pop is not None:
                pop(session)

        on_session_end(_forget)

    def get(self, default: T | None = None, *, session: str | None = None) -> T | None:
        k = session or key()
        with self._lock:
            if k in self._data:
                self._data.move_to_end(k)
                return self._data[k]
            return default

    def set(self, value: T, *, session: str | None = None) -> None:
        k = session or key()
        with self._lock:
            self._data[k] = value
            self._data.move_to_end(k)
            while len(self._data) > self._limit:
                self._data.popitem(last=False)

    def setdefault(self, factory: Callable[[], T], *, session: str | None = None) -> T:
        k = session or key()
        with self._lock:
            if k not in self._data:
                self._data[k] = factory()
                while len(self._data) > self._limit:
                    self._data.popitem(last=False)
            self._data.move_to_end(k)
            return self._data[k]

    def pop(self, session: str) -> T | None:
        with self._lock:
            return self._data.pop(session, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


# Called with the session key when a daemon session ends (relay disconnected).
_end_hooks: list[Callable[[str], Any]] = []


def on_session_end(hook: Callable[[str], Any]) -> None:
    _end_hooks.append(hook)


def session_ended(session: str) -> None:
    for hook in list(_end_hooks):
        try:
            hook(session)
        except Exception:  # noqa: BLE001 - one cleanup must not stop the others
            import logging

            logging.getLogger("pcbridge").exception("session cleanup failed")
