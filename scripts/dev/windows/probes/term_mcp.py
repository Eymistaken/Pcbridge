"""The tmux_* tools on Windows through a real MCP client, plus `pcbridge attach`.

Opens a session, types into it, reads it back, restarts the daemon (the
session must survive), attaches from a ConPTY the way a person would in a
terminal, leaves with Ctrl+], and closes the session. Run from the checkout
in the Windows test VM with the daemon running (windows-vm.sh ssh).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tests" / "readiness"))
from check import StdioClient, _tool_text  # noqa: E402

EXE = str(Path(sys.executable).with_name("pcbridge.exe"))


def client() -> StdioClient:
    c = StdioClient([EXE, "stdio"], dict(os.environ), 60.0)
    c.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                             "clientInfo": {"name": "pcbridge-w4-probe", "version": "1"}})
    c.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return c


def call(c: StdioClient, name: str, **arguments) -> str:
    reply = c.request("tools/call", {"name": name, "arguments": arguments})
    return _tool_text(reply.get("result", reply)) or str(reply)[:600]


def main() -> int:
    from pcbridge import winservice

    c = client()
    print("== tmux_start\n" + call(c, "tmux_start", session="w4", workdir=str(Path.home()))[-600:])
    print("== tmux_send\n" + call(c, "tmux_send", session="w4", text="Write-Output \"hello-$((6*7))-ğüş\"")[-500:])
    time.sleep(1.0)
    print("== tmux_capture\n" + call(c, "tmux_capture", session="w4", lines=8))
    print("== tmux_keys C-c\n" + call(c, "tmux_keys", session="w4", keys=["C-c"])[-200:])
    print("== tmux_list\n" + call(c, "tmux_list"))
    c.close()
    t = time.monotonic()
    print("== daemon restart:", winservice.stop(), winservice.start(), f"{time.monotonic() - t:.1f}s")
    c = client()
    print("== tmux_list after restart\n" + call(c, "tmux_list"))

    from winpty import PtyProcess

    attach = PtyProcess.spawn(f"{EXE} attach w4", dimensions=(50, 200))
    seen: list[str] = []

    def drain() -> None:
        while True:
            try:
                data = attach.read(65536)
            except EOFError:
                return
            if data:
                seen.append(data)

    import threading

    threading.Thread(target=drain, daemon=True).start()
    time.sleep(2.0)
    attach.write("Write-Output attached-$((2+3))\r")
    time.sleep(2.0)
    text = "".join(seen)
    # The stream carries PowerShell's syntax colors between the typed words,
    # so the command's output, not its echo, is what is looked for.
    print("== attach saw the command's output:", "attached-5" in text,
          f"({len(text)} chars, 'attached-5' x{text.count('attached-5')})")
    attach.write("\x1d")
    time.sleep(2.0)
    print("== attach left with Ctrl+]:", not attach.isalive())
    if attach.isalive():
        attach.terminate(force=True)
    print("== capture after attach\n" + call(c, "tmux_capture", session="w4", lines=5))
    print("== tmux_kill\n" + call(c, "tmux_kill", session="w4"))
    time.sleep(1.0)
    print("== tmux_list at the end\n" + call(c, "tmux_list"))
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
