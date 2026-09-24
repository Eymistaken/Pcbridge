"""The tools pcbridge offers, for people: name, title, description, hints,
parameters and whether the current config offers each one.

Built from the same registration the server runs (`tools.register`), on a
throwaway state directory with desktop control off, so the catalog cannot
drift from what a client really gets. Measured 2026-09-24: 0.65 s including
the imports, 0.07 s for the registration itself.
"""

from __future__ import annotations

import asyncio
import dataclasses
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GROUPS = ("Agents and jobs", "Shell, files and terminals", "Desktop", "System")

_AGENT_TOOLS = {"agent_run", "list_agents", "job_status", "job_output", "job_list", "job_cancel"}
_SYSTEM_TOOLS = {"system_status", "notify", "panel_icon"}


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    required: bool
    description: str


@dataclass(frozen=True)
class ToolInfo:
    name: str
    title: str
    description: str
    group: str
    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool
    params: tuple[Param, ...]
    active: bool          # offered by the configured [tools] profile
    note: str             # why it is not usable now, "" when it is

    @property
    def state(self) -> str:
        if not self.active:
            return "not in profile"
        return "needs desktop" if self.note else "active"

    def hints(self) -> list[str]:
        out = ["read-only" if self.read_only else "changes things"]
        if self.destructive:
            out.append("destructive")
        if self.idempotent:
            out.append("idempotent")
        if self.open_world:
            out.append("open world")
        return out


def group_of(name: str) -> str:
    from .tools import DESKTOP_TOOLS

    if name in _AGENT_TOOLS:
        return GROUPS[0]
    if name in DESKTOP_TOOLS:
        return GROUPS[2]
    if name in _SYSTEM_TOOLS:
        return GROUPS[3]
    return GROUPS[1]


def _type_of(schema: dict[str, Any]) -> str:
    if "type" in schema:
        t = schema["type"]
        return "|".join(t) if isinstance(t, list) else str(t)
    kinds = [s.get("type", "any") for s in schema.get("anyOf", []) if isinstance(s, dict)]
    kinds = [k for k in kinds if k != "null"] or ["any"]
    return "|".join(dict.fromkeys(kinds))


def _params(schema: dict[str, Any] | None) -> tuple[Param, ...]:
    schema = schema or {}
    required = set(schema.get("required", []))
    out = []
    for name, spec in (schema.get("properties") or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        out.append(Param(name, _type_of(spec), name in required,
                         " ".join(str(spec.get("description", "")).split())))
    return tuple(out)


def _registered(cfg: Any) -> list[Any]:
    """The MCP tool objects of the full profile, built in a throwaway state directory."""
    from fastmcp import FastMCP

    from . import tools as toolsmod
    from .jobs import JobManager
    from .shots import ShotStore

    with tempfile.TemporaryDirectory(prefix="pcbridge-catalog-") as tmp:
        c = dataclasses.replace(
            cfg,
            state_dir=Path(tmp),
            tools_profile="full",
            desktop=dataclasses.replace(cfg.desktop, enabled=False),
        )
        mcp = FastMCP(name="pcbridge")
        runtime = toolsmod.register(mcp, c, JobManager(c.jobs_dir), ShotStore(c), transport="stdio")
        try:
            return asyncio.run(mcp.list_tools())
        finally:
            runtime.close()


def build(cfg: Any) -> list[ToolInfo]:
    """Every tool, marked with what the given config offers."""
    from .tools import DESKTOP_TOOLS, tools_in_profile

    offered = tools_in_profile(getattr(cfg, "tools_profile", "full"))
    desktop_on = bool(cfg.desktop.enabled)
    out = []
    for t in _registered(cfg):
        a = t.annotations
        active = t.name in offered
        note = ""
        if active and t.name in DESKTOP_TOOLS and not desktop_on and t.name != "system_capabilities":
            note = "[desktop] enabled is false"
        out.append(ToolInfo(
            name=t.name,
            title=(a.title if a and a.title else t.name),
            description=(t.description or "").strip(),
            group=group_of(t.name),
            read_only=bool(a and a.readOnlyHint),
            destructive=bool(a and a.destructiveHint),
            idempotent=bool(a and a.idempotentHint),
            open_world=bool(a and a.openWorldHint),
            params=_params(t.parameters),
            active=active,
            note=note,
        ))
    order = {g: i for i, g in enumerate(GROUPS)}
    return sorted(out, key=lambda i: (order[i.group], i.name))


def search(tools: list[ToolInfo], query: str) -> list[ToolInfo]:
    """Tools whose name, title or description holds every word of `query`.

    Name matches come first, then title matches, then description matches.
    """
    words = [w for w in query.lower().split() if w]
    if not words:
        return list(tools)
    scored = []
    for i, t in enumerate(tools):
        name, title, desc = t.name.lower(), t.title.lower(), t.description.lower()
        hay = " ".join((name, name.replace("_", " "), title, desc))
        if not all(w in hay for w in words):
            continue
        if all(w in name or w in name.replace("_", " ") for w in words):
            rank = 0
        elif all(w in title for w in words):
            rank = 1
        else:
            rank = 2
        scored.append((rank, i, t))
    return [t for _, _, t in sorted(scored, key=lambda x: (x[0], x[1]))]
