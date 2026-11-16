"""W7, second part: launching, the activity guard and the kill switch on Windows.

    python tests\\live\\windows\\check_guards.py [path\\to\\pcbridge.exe]

Run it in the desktop session with the daemon running. Through a real MCP
client (`pcbridge.exe stdio`): opens the grant, starts Paint with
window_focus (Paint must be closed), then prints a marker and waits while
the host presses a key on QEMU's keyboard (`windows-vm.sh key shift`): the
next key must be refused with USER_ACTIVE. Then `pcbridge lock` (the kill
switch, as its own process) must close the grant and end the frame.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "readiness"))
from check import StdioClient, _tool_text  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    exe = sys.argv[1] if len(sys.argv) > 1 else str(Path(sys.executable).with_name("pcbridge.exe"))
    client = StdioClient([exe, "stdio"], dict(os.environ), 110.0)
    client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "pcbridge-w7-probe2", "version": "1"}})
    client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(name: str, **arguments) -> str:
        started = time.perf_counter()
        result = client.request("tools/call", {"name": name, "arguments": arguments})
        text = _tool_text(result.get("result", result))
        ms = round(1000 * (time.perf_counter() - started))
        print(f"===== {name} {json.dumps(arguments, ensure_ascii=True)[:200]} ({ms} ms)\n"
              f"{ascii(text[:900])}\n", flush=True)
        return text

    call("desktop_unlock", minutes=5, reason="W7 probe 2")
    state = Path(os.environ["LOCALAPPDATA"]) / "pcbridge" / "state" / "glow.json"
    print(f"== frame record present: {state.exists()}", flush=True)
    call("window_focus", window="Paint")
    call("window_list")
    print("== waiting 4 s for a hardware key from the host", flush=True)
    time.sleep(4)
    call("keyboard", action="key", keys="escape")
    time.sleep(3)
    call("keyboard", action="key", keys="escape")
    locked = subprocess.run([exe, "lock"], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=60)
    print(f"== pcbridge lock: rc {locked.returncode}: {ascii(locked.stdout.strip()[-300:])}", flush=True)
    time.sleep(1.5)
    print(f"== frame record present after lock: {state.exists()}", flush=True)
    call("keyboard", action="key", keys="escape")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
