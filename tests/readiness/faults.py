#!/usr/bin/env python3
"""Fault injection against a running pcbridge daemon (invariant I3).

Each scenario must recover within 3 s with no client hang:

    kill9       kill -9 the daemon while a call is in flight: that call gets a
                retryable error, the next call succeeds (socket activation)
    stopped     the daemon is stopped: the next client starts it
    stale       the socket file is replaced by a dead one: the relay falls back
                (in-process) and still serves the client
    noruntime   XDG_RUNTIME_DIR unusable: in-process fallback
    three       three clients at once, each running a desktop-free call
    jobscope    a background job survives kill -9 of the daemon

Usage: faults.py --unit pcbridge.service --socket PATH --command "pcbridge stdio" [scenario ...]
It stops/kills only the unit it is given. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check import PROTOCOL_VERSION, ProbeError, StdioClient, _env_for, _tool_text  # noqa: E402


def session(cmd: list[str], env: dict[str, str], timeout: float = 30.0) -> tuple[StdioClient, float]:
    t0 = time.monotonic()
    c = StdioClient(cmd, env, timeout)
    c.request(
        "initialize",
        {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "pcbridge-faults", "version": "1"}},
    )
    c.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    c.request("tools/list")
    return c, (time.monotonic() - t0) * 1000


def call(c: StdioClient, name: str, args: dict | None = None) -> dict:
    return c.request("tools/call", {"name": name, "arguments": args or {}})


def main_pid(unit: str) -> int:
    out = subprocess.run(
        ["systemctl", "--user", "show", "-p", "MainPID", "--value", unit], capture_output=True, text=True
    ).stdout.strip()
    return int(out or 0)


def wait_active(unit: str, timeout: float = 10.0) -> float:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if main_pid(unit):
            return time.monotonic() - t0
        time.sleep(0.05)
    return -1.0


def scenario_kill9(a, env) -> dict:
    c, _ = session(a.cmd, env)
    try:
        pid = main_pid(a.unit)
        if not pid:
            raise ProbeError("daemon not running before kill9")
        # A call that takes a few seconds, then kill the daemon under it.
        c.send({"jsonrpc": "2.0", "id": 900, "method": "tools/call",
                "params": {"name": "shell_run", "arguments": {"command": "sleep 4; echo done", "timeout": 20}}})
        time.sleep(0.5)
        t0 = time.monotonic()
        os.kill(pid, 9)
        # The in-flight answer must come back as an error, fast.
        got = c._read_id(900, 5.0)
        err_ms = (time.monotonic() - t0) * 1000
        if "error" not in got or not got["error"].get("data", {}).get("retryable"):
            raise ProbeError(f"in-flight call did not get a retryable error: {got}")
        t1 = time.monotonic()
        res = call(c, "system_capabilities")
        next_ms = (time.monotonic() - t1) * 1000
        if res.get("isError"):
            raise ProbeError("call after kill9 failed")
        return {"ok": True, "in_flight_error_ms": round(err_ms, 1), "next_call_ms": round(next_ms, 1),
                "new_pid": main_pid(a.unit), "old_pid": pid}
    finally:
        c.close()


def scenario_stopped(a, env) -> dict:
    subprocess.run(["systemctl", "--user", "stop", a.unit], check=False)
    time.sleep(0.3)
    c, ms = session(a.cmd, env)
    try:
        res = call(c, "system_status")
        via = "daemon" if "daemon pid" in _tool_text(res) else "in-process"
        return {"ok": via == "daemon", "cold_start_ms": round(ms, 1), "served_by": via}
    finally:
        c.close()


def scenario_stale(a, env) -> dict:
    # Point the relay at a socket file nobody listens on.
    stale = Path(a.socket).with_name("stale.sock")
    s = socket.socket(socket.AF_UNIX)
    stale.unlink(missing_ok=True)
    s.bind(str(stale))
    s.close()  # bound, never listening: connect() is refused
    e = dict(env, PCBRIDGE_SOCKET=str(stale))
    try:
        c, ms = session(a.cmd, e)
        try:
            res = call(c, "system_status")
            text = _tool_text(res)
            return {"ok": "degraded: in-process" in text, "cold_start_ms": round(ms, 1)}
        finally:
            c.close()
    finally:
        stale.unlink(missing_ok=True)


def scenario_noruntime(a, env) -> dict:
    e = dict(env, XDG_RUNTIME_DIR="/nonexistent-runtime")
    e.pop("PCBRIDGE_SOCKET", None)
    c, ms = session(a.cmd, e)
    try:
        text = _tool_text(call(c, "system_status"))
        return {"ok": "degraded: in-process" in text, "cold_start_ms": round(ms, 1)}
    finally:
        c.close()


def scenario_three(a, env) -> dict:
    results: list = [None, None, None]

    def one(i: int) -> None:
        try:
            c, ms = session(a.cmd, env)
            try:
                r = call(c, "shell_run", {"command": f"echo client-{i}", "timeout": 10})
                results[i] = (ms, f"client-{i}" in _tool_text(r))
            finally:
                c.close()
        except Exception as exc:  # noqa: BLE001
            results[i] = (None, str(exc))

    threads = [threading.Thread(target=one, args=(i,)) for i in range(3)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    ok = all(r and r[1] is True for r in results)
    return {"ok": ok, "wall_ms": round((time.monotonic() - t0) * 1000, 1), "results": results}


def scenario_jobscope(a, env) -> dict:
    c, _ = session(a.cmd, env)
    try:
        res = call(c, "shell_run_background", {"command": "sleep 20; echo survived"})
        text = _tool_text(res)
        import re

        m = re.search(r"\d{8}-\d{6}-[0-9a-f]{6}", text)
        if not m:
            raise ProbeError(f"no job id in: {text[:200]}")
        job = m.group(0)
        scope = f"pcbridge-job-{job}.scope"
        active_before = ""
        for _ in range(40):  # systemd registers the scope asynchronously
            active_before = subprocess.run(["systemctl", "--user", "is-active", scope], capture_output=True, text=True).stdout.strip()
            if active_before == "active":
                break
            time.sleep(0.05)
        os.kill(main_pid(a.unit), 9)
        time.sleep(1.5)
        active_after = subprocess.run(["systemctl", "--user", "is-active", scope], capture_output=True, text=True).stdout.strip()
        st = _tool_text(call(c, "job_status", {"job_id": job}))
        call(c, "job_cancel", {"job_id": job})
        return {"ok": active_before == "active" and active_after == "active",
                "scope_before": active_before, "scope_after_kill9": active_after, "job_status_after": st[:80]}
    finally:
        c.close()


SCENARIOS = {
    "kill9": scenario_kill9,
    "stopped": scenario_stopped,
    "stale": scenario_stale,
    "noruntime": scenario_noruntime,
    "three": scenario_three,
    "jobscope": scenario_jobscope,
}


def _read_id(self: StdioClient, rid, timeout: float) -> dict:
    """Wait for the answer to a request sent with send()."""
    deadline = time.monotonic() + timeout
    assert self.proc.stdout is not None
    result: dict = {}
    done = threading.Event()

    def read() -> None:
        for raw in self.proc.stdout:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("id") == rid:
                result.update(msg)
                break
        done.set()

    threading.Thread(target=read, daemon=True).start()
    if not done.wait(max(0.0, deadline - time.monotonic())) or not result:
        raise ProbeError(f"no answer for id {rid} within {timeout} s (client hang)")
    return result


StdioClient._read_id = _read_id  # type: ignore[attr-defined]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--unit", required=True)
    p.add_argument("--socket", required=True)
    p.add_argument("--command", required=True)
    p.add_argument("scenarios", nargs="*", default=list(SCENARIOS))
    a = p.parse_args()
    a.cmd = shlex.split(a.command)
    env = _env_for("full", {"PCBRIDGE_SOCKET": a.socket})
    out = {}
    for name in a.scenarios:
        try:
            out[name] = SCENARIOS[name](a, env)
        except Exception as exc:  # noqa: BLE001
            out[name] = {"ok": False, "error": str(exc)}
        print(f"{'PASS' if out[name].get('ok') else 'FAIL'}  {name}: {json.dumps(out[name])}", flush=True)
        wait_active(a.unit, 5)
    return 0 if all(v.get("ok") for v in out.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
