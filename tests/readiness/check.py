#!/usr/bin/env python3
"""Readiness check: can a fresh client use pcbridge right now, with no manual step?

This is the safety net for the "always ready" invariants (I1, I2, I7, I9).
It needs only the Python standard library, so it runs under any python3,
including the system one, against any install of pcbridge.

For every command a client has registered (Claude Code, Codex, Claude
Desktop), plus the legacy repository command, it spawns a NEW process the way
that client does, then runs:

    initialize -> notifications/initialized -> tools/list (full tool set)
    -> system_status -> system_capabilities

Each client is spawned with the environment it really provides, because that
is where pcbridge has broken before:

    claude-desktop  only HOME LOGNAME PATH SHELL USER (measured 2026-09-20)
    codex           full session, but DBUS_SESSION_BUS_ADDRESS arrives as the
                    unexpanded literal "$DBUS_SESSION_BUS_ADDRESS"
    claude-code     the full session environment

With --desktop it also calls desktop_unlock(minutes=1) and desktop_lock, which
proves the agent can still open the desktop by itself (I7). With --http it
probes the HTTP listener the way the PcBridgeDesktop app and remote clients
use it (static token, streamable HTTP).

Exit code 0 means every probe passed; 1 means at least one failed.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import statistics
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

PROTOCOL_VERSION = "2025-06-18"

# The full tool set of the default profile. A client that sees fewer tools
# than this has been silently degraded.
EXPECTED_TOOLS = frozenset(
    """
    agent_run computer_batch computer_task desktop_lock desktop_unlock
    find_text fs_list fs_read fs_search fs_write job_cancel job_list
    job_output job_status keyboard list_agents mouse notify screen_capture
    screen_info shell_run shell_run_background system_capabilities
    system_status tmux_capture tmux_keys tmux_kill tmux_list tmux_send
    tmux_start ui_click ui_dump ui_set_text wait_for_text window_focus
    window_list
    """.split()
)

HOME = Path.home()
DEFAULT_REPO = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------
# Registered commands
# --------------------------------------------------------------------------


@dataclass
class Target:
    name: str
    argv: list[str]
    env_profile: str
    source: str
    extra_env: dict[str, str] = field(default_factory=dict)


def _claude_code_target() -> Target | None:
    path = HOME / ".claude.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    entry = (data.get("mcpServers") or {}).get("pcbridge")
    if not entry or entry.get("type", "stdio") != "stdio":
        return None
    return Target(
        "claude-code",
        [entry["command"], *entry.get("args", [])],
        "full",
        f"{path} (user scope)",
        dict(entry.get("env") or {}),
    )


def _codex_target() -> Target | None:
    path = HOME / ".codex" / "config.toml"
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, ValueError):
        return None
    entry = (data.get("mcp_servers") or {}).get("pcbridge")
    if not entry or "command" not in entry:
        return None
    return Target(
        "codex",
        [entry["command"], *entry.get("args", [])],
        "codex",
        str(path),
        dict(entry.get("env") or {}),
    )


def _claude_desktop_target() -> Target | None:
    path = HOME / ".config" / "Claude" / "claude_desktop_config.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    entry = (data.get("mcpServers") or {}).get("pcbridge")
    if not entry or "command" not in entry:
        return None
    return Target(
        "claude-desktop",
        [entry["command"], *entry.get("args", [])],
        "minimal",
        str(path),
        dict(entry.get("env") or {}),
    )


def _legacy_target(repo: Path) -> Target:
    return Target(
        "legacy",
        [str(repo / ".venv" / "bin" / "python"), "-m", "pcbridge.server", "--stdio"],
        "full",
        f"{repo} (legacy command)",
    )


def _env_for(profile: str, extra: dict[str, str]) -> dict[str, str]:
    base = dict(os.environ)
    # Never let the checker's own client identity leak into the probe.
    for key in list(base):
        if key.startswith(("CLAUDE", "MCP_")) or key in {"AI_AGENT"}:
            base.pop(key, None)
    if profile == "minimal":
        base = {k: base[k] for k in ("HOME", "LOGNAME", "PATH", "SHELL", "USER") if k in base}
    elif profile == "codex":
        base["DBUS_SESSION_BUS_ADDRESS"] = "$DBUS_SESSION_BUS_ADDRESS"
    base.update(extra)
    return base


# --------------------------------------------------------------------------
# A minimal stdio MCP client
# --------------------------------------------------------------------------


class ProbeError(Exception):
    pass


class StdioClient:
    def __init__(self, argv: list[str], env: dict[str, str], timeout: float):
        self.timeout = timeout
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(HOME),
        )
        self._next_id = 0
        self._stderr: list[bytes] = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self._stderr.append(line)

    def stderr_tail(self, n: int = 8) -> str:
        return b"".join(self._stderr[-n:]).decode("utf-8", "replace").strip()

    def send(self, message: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        rid = self._next_id
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline = time.monotonic() + self.timeout
        result: dict = {}
        done = threading.Event()

        def read() -> None:
            assert self.proc.stdout is not None
            for raw in self.proc.stdout:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("id") == rid:
                    result.update(msg)
                    break
            done.set()

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        if not done.wait(max(0.0, deadline - time.monotonic())):
            raise ProbeError(f"{method}: no answer within {self.timeout:.0f} s")
        if not result:
            raise ProbeError(
                f"{method}: the server exited (code {self.proc.poll()}). "
                f"stderr: {self.stderr_tail()}"
            )
        if "error" in result:
            raise ProbeError(f"{method}: JSON-RPC error {result['error']}")
        return result["result"]

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
            self.proc.wait()


def _tool_text(result: dict) -> str:
    return "\n".join(
        block.get("text", "") for block in result.get("content", []) if block.get("type") == "text"
    )


def _ms(start: float) -> float:
    return round((time.monotonic() - start) * 1000, 1)


def probe_stdio(target: Target, args: argparse.Namespace) -> dict:
    report: dict = {"client": target.name, "command": shlex.join(target.argv), "source": target.source}
    env = _env_for(target.env_profile, target.extra_env)
    start = time.monotonic()
    client = StdioClient(target.argv, env, args.timeout)
    try:
        client.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": f"pcbridge-readiness/{target.name}", "version": "1"},
            },
        )
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        report["initialize_ms"] = _ms(start)
        tools = {t["name"] for t in client.request("tools/list")["tools"]}
        report["tools_list_ms"] = _ms(start)
        report["tool_count"] = len(tools)
        missing = sorted(args.expected - tools)
        if missing:
            raise ProbeError(f"tools/list is missing {len(missing)} tool(s): {', '.join(missing)}")

        timings = []
        for _ in range(args.warm):
            t0 = time.monotonic()
            res = client.request("tools/call", {"name": "system_status", "arguments": {}})
            timings.append(_ms(t0))
            if res.get("isError"):
                raise ProbeError(f"system_status failed: {_tool_text(res)[:300]}")
        report["system_status_ms"] = statistics.median(timings)

        timings = []
        for _ in range(args.warm):
            t0 = time.monotonic()
            res = client.request("tools/call", {"name": "system_capabilities", "arguments": {}})
            timings.append(_ms(t0))
            if res.get("isError"):
                raise ProbeError(f"system_capabilities failed: {_tool_text(res)[:300]}")
        report["system_capabilities_ms"] = statistics.median(timings)
        caps = res.get("structuredContent") or {}
        report["desktop_backend_ok"] = _desktop_summary(caps)

        if args.desktop:
            t0 = time.monotonic()
            res = client.request(
                "tools/call",
                {
                    "name": "desktop_unlock",
                    "arguments": {"minutes": 1, "reason": "readiness check"},
                },
            )
            report["desktop_unlock_ms"] = _ms(t0)
            if res.get("isError"):
                raise ProbeError(f"desktop_unlock failed: {_tool_text(res)[:300]}")
            t0 = time.monotonic()
            res = client.request("tools/call", {"name": "desktop_lock", "arguments": {}})
            report["desktop_lock_ms"] = _ms(t0)
            if res.get("isError"):
                raise ProbeError(f"desktop_lock failed: {_tool_text(res)[:300]}")
        report["ok"] = True
    except ProbeError as exc:
        report["ok"] = False
        report["error"] = str(exc)
    finally:
        client.close()
    report["total_ms"] = _ms(start)
    return report


def _desktop_summary(caps: dict) -> str:
    """One short word about the desktop side, for the report only."""
    if not caps:
        return "unknown"
    enabled = caps.get("desktop_enabled", caps.get("enabled"))
    return "enabled" if enabled else ("disabled" if enabled is not None else "reported")


# --------------------------------------------------------------------------
# HTTP probe (PcBridgeDesktop and remote clients)
# --------------------------------------------------------------------------


def _http_post(url: str, body: dict, headers: dict[str, str], timeout: float):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **headers,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
        session = resp.headers.get("mcp-session-id")
    if raw.lstrip().startswith("{"):
        return json.loads(raw), session
    for line in raw.splitlines():
        if line.startswith("data:"):
            payload = json.loads(line[5:].strip())
            if "id" in payload:
                return payload, session
    return {}, session


def probe_http(args: argparse.Namespace) -> dict:
    report: dict = {"client": "http", "command": args.http_url}
    base = args.http_url.rstrip("/")
    root = base.rsplit("/", 1)[0] if base.endswith("/mcp") else base
    start = time.monotonic()
    try:
        with urllib.request.urlopen(root + "/healthz", timeout=args.timeout) as resp:
            health = json.loads(resp.read())
        if not health.get("ok"):
            raise ProbeError(f"/healthz did not report ok: {health}")
        report["healthz_ms"] = _ms(start)
        token = args.static_token or os.environ.get("PCBRIDGE_TEST_STATIC", "")
        if not token:
            report["ok"] = True
            report["note"] = "no static token given; only /healthz was probed"
            return report
        headers = {"Authorization": f"Bearer {token}"}
        msg, session = _http_post(
            base,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "pcbridge-readiness/http", "version": "1"},
                },
            },
            headers,
            args.timeout,
        )
        if "result" not in msg:
            raise ProbeError(f"initialize over HTTP failed: {msg}")
        if session:
            headers["mcp-session-id"] = session
        _http_post(base, {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers, args.timeout)
        msg, _ = _http_post(base, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers, args.timeout)
        tools = {t["name"] for t in msg.get("result", {}).get("tools", [])}
        report["tool_count"] = len(tools)
        missing = sorted(args.expected - tools)
        if missing:
            raise ProbeError(f"tools/list over HTTP is missing: {', '.join(missing)}")
        t0 = time.monotonic()
        msg, _ = _http_post(
            base,
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "system_status", "arguments": {}}},
            headers,
            args.timeout,
        )
        report["system_status_ms"] = _ms(t0)
        if msg.get("result", {}).get("isError") or "error" in msg:
            raise ProbeError(f"system_status over HTTP failed: {str(msg)[:300]}")
        report["ok"] = True
    except (ProbeError, urllib.error.URLError, OSError, ValueError) as exc:
        report["ok"] = False
        report["error"] = str(exc)
    report["total_ms"] = _ms(start)
    return report


# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--client",
        action="append",
        choices=["claude-code", "codex", "claude-desktop", "legacy", "http"],
        help="Probe only these (repeatable). Default: every registered client, the legacy command and HTTP.",
    )
    parser.add_argument("--legacy-repo", type=Path, default=DEFAULT_REPO, help="Repository for the legacy command.")
    parser.add_argument(
        "--command",
        action="append",
        default=[],
        help="Extra stdio command to probe, as one shell-quoted string (repeatable).",
    )
    parser.add_argument("--desktop", action="store_true", help="Also call desktop_unlock(1) then desktop_lock.")
    parser.add_argument("--http-url", default="http://127.0.0.1:8765/mcp")
    parser.add_argument("--static-token", default="", help="Default: $PCBRIDGE_TEST_STATIC.")
    parser.add_argument("--warm", type=int, default=3, help="Warm calls per tool; the median is reported.")
    parser.add_argument("--repeat", type=int, default=1, help="Spawn each command this many times.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Seconds per request.")
    parser.add_argument("--expect-tools", default="", help="Comma-separated tool set to require instead of the full set.")
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    args = parser.parse_args(argv)
    args.expected = (
        frozenset(t.strip() for t in args.expect_tools.split(",") if t.strip())
        if args.expect_tools
        else EXPECTED_TOOLS
    )

    # `--command` alone probes only those commands (CI has no registered
    # client, no legacy checkout and no running service).
    default = [] if args.command else ["claude-code", "codex", "claude-desktop", "legacy", "http"]
    wanted = set(args.client or default)
    targets: list[Target] = []
    for name, finder in (
        ("claude-code", _claude_code_target),
        ("codex", _codex_target),
        ("claude-desktop", _claude_desktop_target),
    ):
        if name in wanted:
            t = finder()
            if t is not None:
                targets.append(t)
            elif args.client:
                print(f"{name}: no pcbridge registration found", file=sys.stderr)
                return 1
    if "legacy" in wanted:
        targets.append(_legacy_target(args.legacy_repo))
    for i, cmd in enumerate(args.command):
        targets.append(Target(f"command-{i + 1}", shlex.split(cmd), "full", "--command"))

    reports = []
    for target in targets:
        for _ in range(max(1, args.repeat)):
            reports.append(probe_stdio(target, args))
    if "http" in wanted:
        reports.append(probe_http(args))

    ok = all(r.get("ok") for r in reports)
    if args.json:
        print(json.dumps({"ok": ok, "reports": reports}, indent=2))
    else:
        for r in reports:
            status = "PASS" if r.get("ok") else "FAIL"
            timing = ", ".join(
                f"{k[:-3]} {v} ms" for k, v in r.items() if k.endswith("_ms") and isinstance(v, (int, float))
            )
            print(f"{status}  {r['client']:<15} {timing}")
            if not r.get("ok"):
                print(f"      {r.get('error')}")
                print(f"      command: {r.get('command')}")
        print("ready" if ok else "NOT READY")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
