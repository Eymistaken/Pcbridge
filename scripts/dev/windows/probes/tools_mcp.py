"""Call pcbridge's non-desktop tools through a real MCP client on Windows.

Runs `pcbridge.exe stdio` (the relay, so the running daemon answers) and
calls each tool the port's W3 stage changed, printing what came back. Used
in the Windows test VM: windows-vm.sh session or ssh, from the checkout.

    python scripts\\dev\\windows\\probes\\tools_mcp.py [path\\to\\pcbridge.exe]
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tests" / "readiness"))
from check import StdioClient, _tool_text  # noqa: E402


def call(client: StdioClient, name: str, **arguments) -> str:
    result = client.request("tools/call", {"name": name, "arguments": arguments})
    return _tool_text(result.get("result", result))


def main() -> int:
    exe = sys.argv[1] if len(sys.argv) > 1 else str(Path(sys.executable).with_name("pcbridge.exe"))
    client = StdioClient([exe, "stdio"], dict(os.environ), 60.0)
    client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "pcbridge-w3-probe", "version": "1"}})
    client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    work = Path.home() / "pcb-w3"
    work.mkdir(exist_ok=True)
    (work / "a.txt").write_text("alpha\nneedle one\nbeta\n", encoding="utf-8")
    (work / "b.txt").write_text("needle two ğüş\n", encoding="utf-8")
    steps = [
        ("shell_run", dict(command="Write-Output \"ps $($PSVersionTable.PSVersion.Major) ğüş\"; Get-Location", workdir=str(work))),
        ("fs_list", dict(path=str(Path.home()))),
        ("fs_search", dict(query="needle", path=str(work))),
        ("system_status", {}),
    ]
    for name, arguments in steps:
        print(f"===== {name}\n{call(client, name, **arguments)[:1500]}\n", flush=True)
    started = call(client, "shell_run_background",
                   command="1..3 | ForEach-Object { Write-Output \"tick $_\"; Start-Sleep -Milliseconds 300 }",
                   workdir=str(work))
    print("===== shell_run_background\n" + started)
    job = started.split("`")[1]
    time.sleep(3)
    print("===== job_status\n" + call(client, "job_status", job_id=job)[:800])
    print("===== job_output\n" + call(client, "job_output", job_id=job)[:800])
    long_job = call(client, "shell_run_background", command="Start-Sleep -Seconds 120", workdir=str(work))
    long_id = long_job.split("`")[1]
    time.sleep(1.5)
    print("===== job_cancel\n" + call(client, "job_cancel", job_id=long_id))
    print("===== job_status after cancel\n" + call(client, "job_status", job_id=long_id)[:400])
    print("===== notify\n" + call(client, "notify", message="pcbridge W3 probe", title="pcbridge"))
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
