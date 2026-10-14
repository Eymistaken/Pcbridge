"""Which remote clients hold OAuth tokens, and cutting them off.

Remote clients (ChatGPT, Gemini, claude.ai, a phone) register themselves
through Dynamic Client Registration and get tokens after the password on the
consent page. They live in `oauth.db` (`auth.py`). This module reads and
changes that file directly, so `pcbridge remote clients` and `revoke` work
with the daemon stopped and without importing FastMCP.

The daemon reads the database on every request (`load_access_token`), so a
revocation takes effect on the client's next call, with no restart.

Revoking a client deletes its registration together with its access tokens,
refresh tokens, authorization codes and pending consents: the app has to
register and go through the password page again. Changing the password does
NOT do this: refresh tokens do not depend on it (measured 2026-10-14, see
docs/dev/measured-facts.md).

Nothing here returns or logs a token.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

UNNAMED = "(unnamed)"
# A client id given on the command line must be at least this long, so a
# typo cannot match half the table.
MIN_ID_PREFIX = 6


class RevokeError(ValueError):
    """The selector matched nothing, or more than one client by id."""


@dataclass
class Registration:
    client_id: str
    name: str
    registered: float
    access_tokens: int = 0      # unexpired
    refresh_tokens: int = 0     # unexpired
    expires: float | None = None  # when its last token runs out
    never_expires: bool = False   # it holds a token without an expiry

    @property
    def connected(self) -> bool:
        return self.access_tokens > 0 or self.refresh_tokens > 0


@dataclass
class RemoteClient:
    """Every registration that shares one client name."""

    name: str
    registrations: list[Registration] = field(default_factory=list)

    @property
    def connected(self) -> list[Registration]:
        return [r for r in self.registrations if r.connected]

    @property
    def last_registered(self) -> float:
        return max(r.registered for r in self.registrations)

    @property
    def expires(self) -> float | None:
        """When the last token of this name runs out; None if one never does."""
        live = self.connected
        if any(r.never_expires for r in live):
            return None
        times = [r.expires for r in live if r.expires is not None]
        return max(times) if times else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "registrations": len(self.registrations),
            "connected": len(self.connected),
            "last_registered": self.last_registered,
            "expires": self.expires,
            "clients": [
                {"client_id": r.client_id, "registered": r.registered, "connected": r.connected,
                 "access_tokens": r.access_tokens, "refresh_tokens": r.refresh_tokens,
                 "expires": None if r.never_expires else r.expires}
                for r in self.registrations
            ],
        }


@dataclass
class RevokeResult:
    names: list[str]
    registrations: int
    access_tokens: int
    refresh_tokens: int


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def registrations(db: Path, now: float | None = None) -> list[Registration]:
    """Every registered client with its unexpired token counts, oldest first."""
    if not Path(db).exists():
        return []
    now = time.time() if now is None else now
    with _connect(db) as conn:
        if "clients" not in _tables(conn):
            return []
        regs: dict[str, Registration] = {}
        for row in conn.execute("SELECT client_id, data, created_at FROM clients ORDER BY created_at"):
            try:
                name = json.loads(row["data"]).get("client_name") or ""
            except (ValueError, AttributeError):
                name = ""
            name = " ".join(str(name).split())[:80] or UNNAMED
            regs[row["client_id"]] = Registration(row["client_id"], name, float(row["created_at"]))
        for table in ("access_tokens", "refresh_tokens"):
            for row in conn.execute(
                f"SELECT client_id, expires_at FROM {table}"
                " WHERE expires_at IS NULL OR expires_at >= ?", (now,)
            ):
                reg = regs.get(row["client_id"])
                if reg is None:
                    continue
                if table == "access_tokens":
                    reg.access_tokens += 1
                else:
                    reg.refresh_tokens += 1
                if row["expires_at"] is None:
                    reg.never_expires = True
                else:
                    reg.expires = max(reg.expires or 0.0, float(row["expires_at"]))
    return list(regs.values())


def clients(db: Path, include_stale: bool = False, now: float | None = None) -> list[RemoteClient]:
    """Registrations grouped by name, most recently registered first.

    Without `include_stale` only names with at least one unexpired token are
    returned: a name whose every token has expired cannot reach the server.
    """
    groups: dict[str, RemoteClient] = {}
    for reg in registrations(db, now):
        groups.setdefault(reg.name.casefold(), RemoteClient(reg.name)).registrations.append(reg)
    out = [g for g in groups.values() if include_stale or g.connected]
    return sorted(out, key=lambda g: g.last_registered, reverse=True)


def connected_count(db: Path) -> int:
    """How many registrations still hold an unexpired token."""
    return sum(1 for r in registrations(db) if r.connected)


def select(db: Path, selector: str) -> list[Registration]:
    """The registrations a NAME (every registration of it) or a client id prefix names."""
    selector = " ".join(selector.split())
    if not selector:
        raise RevokeError("Give a client name or id; `pcbridge remote clients` lists them.")
    regs = registrations(db)
    by_name = [r for r in regs if r.name.casefold() == selector.casefold()]
    if by_name:
        return by_name
    if len(selector) >= MIN_ID_PREFIX:
        by_id = [r for r in regs if r.client_id.startswith(selector)]
        if len(by_id) == 1:
            return by_id
        if len(by_id) > 1:
            raise RevokeError(f"{len(by_id)} client ids start with {selector!r}; give more of the id.")
    names = sorted({r.name for r in regs if r.connected}, key=str.casefold)
    hint = f" Connected now: {', '.join(names)}." if names else " No remote client is connected."
    raise RevokeError(f"No remote client is named {selector!r}"
                      + (f" and no client id starts with it." if len(selector) >= MIN_ID_PREFIX else ".")
                      + hint)


def revoke(db: Path, client_ids: list[str], audit_log: Path | None = None) -> RevokeResult:
    """Delete these registrations and everything issued to them."""
    ids = list(dict.fromkeys(client_ids))
    if not ids or not Path(db).exists():
        return RevokeResult([], 0, 0, 0)
    names: dict[str, str] = {r.client_id: r.name for r in registrations(db)}
    marks = ",".join("?" * len(ids))
    with _connect(db) as conn:
        tables = _tables(conn)
        counts = {}
        for table in ("access_tokens", "refresh_tokens", "auth_codes", "pending", "clients"):
            if table in tables:
                counts[table] = conn.execute(
                    f"DELETE FROM {table} WHERE client_id IN ({marks})", ids).rowcount
    result = RevokeResult(
        names=sorted({names.get(i, UNNAMED) for i in ids}, key=str.casefold),
        registrations=counts.get("clients", 0),
        access_tokens=counts.get("access_tokens", 0),
        refresh_tokens=counts.get("refresh_tokens", 0),
    )
    if audit_log is not None:
        _audit(audit_log, "remote_client_revoked", names=result.names,
               registrations=result.registrations, access_tokens=result.access_tokens,
               refresh_tokens=result.refresh_tokens)
    return result


def revoke_all(db: Path, audit_log: Path | None = None) -> RevokeResult:
    """Revoke every registered client, connected or not."""
    return revoke(db, [r.client_id for r in registrations(db)], audit_log)


def _audit(path: Path, event: str, **fields: Any) -> None:
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **fields}
    try:
        with Path(path).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def describe_expiry(expires: float | None, now: float | None = None) -> str:
    """'never', 'in 89 days', 'in 5 hours' for a token expiry."""
    if expires is None:
        return "never"
    left = expires - (time.time() if now is None else now)
    if left >= 2 * 86400:
        return f"in {int(left // 86400)} days"
    if left >= 7200:
        return f"in {int(left // 3600)} hours"
    return f"in {max(1, int(left // 60))} minutes"
