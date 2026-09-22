#!/usr/bin/env python3
"""Performance baseline for invariant I9: the numbers every release is held to.

Runs a scripted MCP session against one stdio command and prints medians:

    cold start until tools/list answers (fresh process each time)
    warm system_status and system_capabilities round trips
    screen_capture of one monitor and of every monitor
    window_focus on a window that is already open
    ui_dump of the focused window
    resident memory of the server process after the session

It opens the desktop grant for a few minutes (desktop_unlock) and closes it
again at the end. It sends no keyboard or pointer input; the only visible
effect is the screencast indicator and, with --focus, a window being raised.

Standard library only, like check.py next to it.
"""

from __future__ import annotations

import argparse
import json
import shlex
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check import PROTOCOL_VERSION, ProbeError, StdioClient, _env_for, _tool_text  # noqa: E402


def _rss_kib(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except OSError:
        return None
    return None


def _tree_rss_kib(pid: int) -> int:
    """RSS of a process and all its descendants (helpers included)."""
    total = 0
    stack = [pid]
    while stack:
        p = stack.pop()
        total += _rss_kib(p) or 0
        try:
            children = Path(f"/proc/{p}/task/{p}/children").read_text().split()
        except OSError:
            children = []
        stack.extend(int(c) for c in children)
    return total


def _open(argv: list[str], timeout: float) -> tuple[StdioClient, float]:
    t0 = time.monotonic()
    client = StdioClient(argv, _env_for("full", {}), timeout)
    client.request(
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "pcbridge-bench", "version": "1"},
        },
    )
    client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    client.request("tools/list")
    return client, (time.monotonic() - t0) * 1000


PACE_S = 0.25


def _call(client: StdioClient, name: str, arguments: dict) -> tuple[float, dict]:
    # The desktop gate allows at most 10 actions per second; pace the calls so
    # the benchmark measures the tool, not the rate limiter.
    time.sleep(PACE_S)
    t0 = time.monotonic()
    res = client.request("tools/call", {"name": name, "arguments": arguments})
    ms = (time.monotonic() - t0) * 1000
    if res.get("isError"):
        raise ProbeError(f"{name}({arguments}) failed: {_tool_text(res)[:400]}")
    return ms, res


def _median(xs: list[float]) -> float:
    return round(statistics.median(xs), 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", help="stdio command, one shell-quoted string")
    parser.add_argument("-n", type=int, default=5, help="Runs per measurement (median).")
    parser.add_argument("--focus", default="", help="Window to focus (an app that is already open).")
    parser.add_argument("--no-desktop", action="store_true", help="Skip every desktop measurement.")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)
    argv_cmd = shlex.split(args.command)
    out: dict = {"command": args.command, "n": args.n}

    cold = []
    for _ in range(args.n):
        client, ms = _open(argv_cmd, args.timeout)
        cold.append(ms)
        client.close()
    out["cold_start_to_tools_list_ms"] = _median(cold)

    client, _ = _open(argv_cmd, args.timeout)
    try:
        out["system_status_ms"] = _median([_call(client, "system_status", {})[0] for _ in range(args.n)])
        out["system_capabilities_ms"] = _median(
            [_call(client, "system_capabilities", {})[0] for _ in range(args.n)]
        )
        if not args.no_desktop:
            _call(client, "desktop_unlock", {"minutes": 5, "reason": "performance baseline"})
            try:
                # The first capture opens the screencast session; measure it apart.
                out["screen_capture_first_ms"] = round(
                    _call(client, "screen_capture", {"monitor": "1"})[0], 1
                )
                out["screen_capture_one_ms"] = _median(
                    [_call(client, "screen_capture", {"monitor": "1"})[0] for _ in range(args.n)]
                )
                out["screen_capture_all_ms"] = _median(
                    [_call(client, "screen_capture", {"monitor": "all"})[0] for _ in range(args.n)]
                )
                out["ui_dump_focused_ms"] = _median(
                    [_call(client, "ui_dump", {"target": "focused"})[0] for _ in range(args.n)]
                )
                out["window_list_ms"] = _median([_call(client, "window_list", {})[0] for _ in range(args.n)])
                if args.focus:
                    out["window_focus_ms"] = _median(
                        [_call(client, "window_focus", {"window": args.focus})[0] for _ in range(args.n)]
                    )
            finally:
                _call(client, "desktop_lock", {})
        out["server_rss_kib"] = _rss_kib(client.proc.pid)
        out["server_tree_rss_kib"] = _tree_rss_kib(client.proc.pid)
    finally:
        client.close()
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
