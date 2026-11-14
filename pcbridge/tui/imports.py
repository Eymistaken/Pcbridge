"""Import FastMCP once, on one thread at a time.

FastMCP resolves `from fastmcp import FastMCP` lazily (a module
`__getattr__` that imports `fastmcp.server.server`). When two threads do
that at once, the second sees the half-imported module and FastMCP turns
the failure into "FastMCP server support is not installed". The terminal
UI's workers start together (the status read imports the tool table, the
doctor imports the OAuth provider), so measured on 2026-11-14 with the
installed 2.9.1 in a fresh process this failed every time and doctor's
config group "crashed". Every worker that may reach FastMCP calls `ready()`
first; the first call imports it, the others wait for it.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_done = False


def ready() -> None:
    """Return once FastMCP and the modules built on it are imported."""
    global _done
    if _done:
        return
    with _lock:
        if _done:
            return
        import fastmcp.server.server  # noqa: F401 - the module the lazy import races on

        from .. import auth, tools  # noqa: F401 - the two the workers import

        _done = True
