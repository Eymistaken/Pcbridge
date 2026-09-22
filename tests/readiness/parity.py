#!/usr/bin/env python3
"""Byte-for-byte parity: the relay + daemon path must answer exactly like the
in-process server, apart from ids and values that change on every run.

The same scripted session runs twice:

    in-process   `pcbridge stdio` with PCBRIDGE_NO_DAEMON=1 (the pre-2.0 path)
    relay        `pcbridge stdio` connected to a running daemon

and every raw answer line is normalized (job ids, shot ids, timestamps,
durations, pids) and compared. The script covers tools/list, a plain text
tool, an error result, structuredContent, a cancelled call, a background job
with job_status, and, with --desktop, a screenshot with its image blocks.

Usage: parity.py --command "pcbridge stdio" --socket PATH [--desktop]
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check import PROTOCOL_VERSION, _env_for  # noqa: E402

NORMALIZE = [
    # First: later patterns would otherwise rewrite bits of the base64 data.
    (re.compile(r'"data": ?"[A-Za-z0-9+/=]{200,}"'), '"data":"<image>"'),
    (re.compile(r"\d{8}-\d{6}-[0-9a-f]{6}"), "<job>"),
    (re.compile(r"\bm\d+-[0-9a-f]{6}\b"), "<shot>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}"), "<time>"),
    (re.compile(r"\b\d+(\.\d+)? ?(ms|s|sn|saniye)\b"), "<dur>"),
    (re.compile(r"\"elapsed_seconds\": ?[\d.]+"), '"elapsed_seconds": <n>'),
    (re.compile(r"\bpid \d+"), "pid <n>"),
    (re.compile(r"\"(pid|since|started_at|finished_at|deadline|ts)\": ?[\d.]+"), r'"\1": <n>'),
    (re.compile(r"\"(observed_at|expires_at|until|hard_until)\": ?[\d.]+"), r'"\1": <n>'),
    (re.compile(r"/[\w./-]*/shots/[\w.-]+\.png"), "<shotpath>"),
    (re.compile(r'"grant_id": ?"[0-9a-f]{32}"'), '"grant_id":"<grant>"'),
    (re.compile(r'"revoke_epoch": ?\d+'), '"revoke_epoch":<n>'),
    (re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\b"), "<clock>"),
    (re.compile(r"\\\"data\\\": ?\\\"[A-Za-z0-9+/=]{200,}\\\""), '\\"data\\": \\"<image>\\"'),
]


class Session:
    def __init__(self, argv: list[str], env: dict[str, str]):
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=open(os.environ["PARITY_STDERR"], "ab") if os.environ.get("PARITY_STDERR") else subprocess.DEVNULL, env=env, cwd=str(Path.home()))
        self.raw: dict[object, bytes] = {}
        self.cv = threading.Condition()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "id" in msg:
                with self.cv:
                    self.raw[msg["id"]] = line
                    self.cv.notify_all()

    def send(self, msg: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg).encode() + b"\n")
        self.proc.stdin.flush()

    def request(self, rid, method: str, params: dict | None = None, timeout: float = 60) -> bytes:
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        return self.wait(rid, timeout)

    def wait(self, rid, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        with self.cv:
            while rid not in self.raw:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError(f"no answer for {rid}")
                self.cv.wait(left)
            return self.raw[rid]

    def close(self) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def call(s: Session, rid: int, name: str, args: dict | None = None, timeout: float = 60) -> bytes:
    return s.request(rid, "tools/call", {"name": name, "arguments": args or {}}, timeout)


def script(s: Session, desktop: bool) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    out.append(("initialize", s.request(1, "initialize", {
        "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
        "clientInfo": {"name": "pcbridge-parity", "version": "1"}})))
    s.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    out.append(("tools/list", s.request(2, "tools/list")))
    out.append(("list_agents (text)", call(s, 3, "list_agents")))
    out.append(("fs_read missing (error)", call(s, 4, "fs_read", {"path": "/nonexistent/pcbridge-parity.txt"})))
    out.append(("system_capabilities (structuredContent)", call(s, 5, "system_capabilities")))
    out.append(("unknown tool (protocol error)", call(s, 6, "no_such_tool")))
    # Cancellation: start a slow call, cancel it, then prove the session lives.
    s.send({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "shell_run", "arguments": {"command": "sleep 2", "timeout": 10}}})
    time.sleep(0.3)
    s.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 7, "reason": "parity"}})
    out.append(("ping after cancel", s.request(8, "ping")))
    job = call(s, 9, "shell_run_background", {"command": "echo parity-job"})
    out.append(("shell_run_background", job))
    m = re.search(rb"\d{8}-\d{6}-[0-9a-f]{6}", job)
    if m:
        time.sleep(1.0)
        out.append(("job_status", call(s, 10, "job_status", {"job_id": m.group(0).decode(), "wait_seconds": 5})))
    if desktop:
        out.append(("desktop_unlock", call(s, 11, "desktop_unlock", {"minutes": 2, "reason": "parity test"})))
        time.sleep(0.3)
        out.append(("screen_capture (image)", call(s, 12, "screen_capture", {"monitor": "1"})))
        time.sleep(0.3)
        out.append(("desktop_lock", call(s, 13, "desktop_lock")))
    return out


def normalize(b: bytes) -> str:
    text = b.decode("utf-8", "replace")
    for rx, rep in NORMALIZE:
        text = rx.sub(rep, text)
    return text


def run(argv: list[str], env: dict[str, str], desktop: bool) -> list[tuple[str, str]]:
    s = Session(argv, env)
    try:
        return [(k, normalize(v)) for k, v in script(s, desktop)]
    finally:
        s.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--command", required=True)
    p.add_argument("--socket", required=True)
    p.add_argument("--desktop", action="store_true")
    a = p.parse_args()
    argv = shlex.split(a.command)
    base = _env_for("full", {})
    inproc = run(argv, dict(base, PCBRIDGE_NO_DAEMON="1"), a.desktop)
    relay = run(argv, dict(base, PCBRIDGE_SOCKET=a.socket), a.desktop)
    failures = 0
    for (name, x), (_, y) in zip(inproc, relay):
        same = x == y
        failures += not same
        print(f"{'SAME' if same else 'DIFF'}  {name}  ({len(x)} bytes)")
        if not same:
            for line in difflib.unified_diff(x.split(","), y.split(","), "in-process", "relay", n=1, lineterm=""):
                print("      " + line[:300])
    print("parity: OK" if not failures else f"parity: {failures} difference(s)")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
