"""Actual resident/CLI grant lifecycle in the disposable Arch Hyprland VM.

No input or screenshot is requested. Native capture startup may truthfully
report unavailable while its separate implementation stage is pending.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from pcbridge.config import load_config  # noqa: E402
from pcbridge.cli.grant import read_state  # noqa: E402
from pcbridge.desktop import glowstate, hyprland, idlewatch  # noqa: E402
from pcbridge.desktop.errors import ErrorCode  # noqa: E402
from pcbridge.desktop.lease import LeaseStore  # noqa: E402
from pcbridge.desktop.safety import SafetyGate  # noqa: E402
from pcbridge.relay import SocketBackend  # noqa: E402
from tests.live.hyprland.check_glow_owner import layers, wait_for  # noqa: E402

CONFIG = '''config_version = 2
public_url = "http://localhost:8765"
default_agent = "claude"
[auth]
password = "hyprland-local-lifecycle-test"
[paths]
state_dir = "{state}"
default_workdir = "{work}"
[native]
capture = "rust"
input = "rust"
accessibility = "rust"
binary_path = "{binary}"
[desktop]
enabled = true
unlock_idle_seconds = 10
unlock_notification = false
[agents.claude]
command = ["true"]
'''


def main():
    assert os.environ.get("PCBRIDGE_TEST_HYPRLAND_GRANT_LIFECYCLE") == "1"
    assert os.uname().nodename == "pcbridge-hyprland"
    assert hyprland.screen_locked() is False
    assert not layers(), "Refuse to overlap a resident frame"
    assert idlewatch.read_idle_ms() is None, "Refuse to replace a resident idle watcher"
    binary = (ROOT / "rust/target/debug/pcbridge-native").resolve()
    with tempfile.TemporaryDirectory(prefix="pcbridge-resident-grant-") as temporary:
        directory = Path(temporary).resolve()
        state = directory / "state"
        state.mkdir(mode=0o700)
        path = directory / "config.toml"
        path.write_text(CONFIG.format(state=state, work=directory, binary=binary))
        path.chmod(0o600)
        cfg = load_config(path)
        store = LeaseStore(state)
        sock_path = directory / "mcp.sock"
        environment = {**os.environ, "PCBRIDGE_CONFIG": str(path), "PCBRIDGE_SOCKET": str(sock_path),
                       "PCBRIDGE_NATIVE_BIN": str(binary)}
        daemon = None
        client = None
        counter = 0
        idle_identity = None
        with (directory / "daemon.log").open("wb") as log:
            try:
                daemon = subprocess.Popen([sys.executable, "-m", "pcbridge", "serve", "--no-http",
                    "--config", str(path), "--socket", str(sock_path)], env=environment,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log)
                wait_for(lambda: sock_path.exists() and idlewatch.read_idle_ms() is not None,
                         timeout=15, description="resident socket and fresh compositor-confirmed idle")
                idle_record = json.loads(idlewatch.state_path().read_text())
                idle_identity = (idle_record["pid"], idle_record["writer_start_ticks"])
                assert glowstate._parent_pid(idle_identity[0]) == daemon.pid
                sock = socket.socket(socket.AF_UNIX)
                sock.connect(str(sock_path))
                client = SocketBackend(sock)
                client.handshake(5, desktop_context=True)

                def rpc(method, params=None):
                    nonlocal counter
                    counter += 1
                    client.sock.settimeout(15)
                    client.send(json.dumps({"jsonrpc": "2.0", "id": counter, "method": method,
                                           "params": params or {}}).encode() + b"\n")
                    while True:
                        line = client.rfile.readline(512 * 1024 + 1)
                        assert line.endswith(b"\n") and len(line) <= 512 * 1024
                        response = json.loads(line)
                        if response.get("id") == counter:
                            assert "error" not in response, response.get("error")
                            return response["result"]

                rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "Hyprland grant lifecycle probe", "version": "1"}})
                client.send(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')

                def tool(name):
                    return rpc("tools/call", {"name": name, "arguments": {}})

                def cli(command):
                    result = subprocess.run([str(ROOT / ".venv/bin/pcbridge"), *command],
                        env=environment, capture_output=True, text=True, timeout=30)
                    assert result.returncode == 0, (result.returncode, result.stderr[-1000:], result.stdout[-1000:])
                    return result

                def health(token):
                    return glowstate.read(state, token, binary=binary)

                first_cli = cli(["unlock", "--minutes", "1", "--reason", "Resident frame lifecycle probe"])
                assert "Desktop control granted" in first_cli.stdout
                first = store.snapshot().token()
                record = health(first)
                assert record and record["owner_pid"] == daemon.pid and record["strip_count"] == 8
                assert read_state(cfg).open
                assert not tool("system_capabilities").get("isError")
                # The CLI has already exited; the daemon still owns the same frame.
                assert health(first)["pid"] == record["pid"]

                cli(["unlock", "--minutes", "1", "--reason", "Replacement resident lifecycle probe"])
                replacement = store.snapshot().token()
                assert replacement != first and health(replacement)["pid"] != record["pid"]
                consumer = SafetyGate(cfg)
                assert consumer.verify(first).code == ErrorCode.REVOKED
                consumer.close()
                assert health(replacement), "Closing a read-only caller killed daemon ownership"

                frame_pid = health(replacement)["pid"]
                os.kill(frame_pid, signal.SIGSTOP)
                wait_for(lambda: not read_state(cfg).open, timeout=2, description="stale presentation CLI pause")
                denied = tool("window_list")
                assert denied.get("isError") and denied["structuredContent"]["error"]["code"] == "BACKEND_UNAVAILABLE"
                os.kill(frame_pid, signal.SIGCONT)
                wait_for(lambda: health(replacement), description="resumed real presentation")
                os.kill(frame_pid, signal.SIGKILL)
                started = time.monotonic()
                wait_for(lambda: store.snapshot().token() is None, timeout=1, description="dead frame lease retirement")
                dead_ms = round((time.monotonic() - started) * 1000)
                assert dead_ms <= 250
                wait_for(lambda: not layers(), description="dead frame layers removed")

                cli(["unlock", "--minutes", "1"])
                assert health(store.snapshot().token())
                assert not tool("window_list").get("isError")
                wait_for(lambda: store.snapshot().token() is None, timeout=13, description="actual sliding grant expiry")
                wait_for(lambda: not layers(), description="expired grant layers removed")

                cli(["unlock", "--minutes", "1"])
                cli(["lock"])
                assert store.snapshot().token() is None
                wait_for(lambda: not layers(), description="CLI desktop lock layers removed")
                cli(["unlock", "--minutes", "1"])
                assert not tool("desktop_lock").get("isError")
                assert store.snapshot().token() is None
                wait_for(lambda: not layers(), description="MCP desktop lock layers removed")

                cli(["unlock", "--minutes", "1"])
                old = store.snapshot().token()
                daemon.kill()
                daemon.wait(timeout=3)
                wait_for(lambda: not read_state(cfg).open, timeout=1, description="dead resident parent refuses grant visibility")
                wait_for(lambda: not layers(), description="dead parent frame teardown")
                assert health(old) is None
                print(json.dumps({"resident_hyprland_grant": {"cli_exit_preserves_frame": "passed",
                    "strip_count": 8, "replacement": "passed", "nonowner_close": "passed",
                    "stale_presentation": "paused", "dead_frame_retirement_ms": dead_ms,
                    "sliding_expiry": "passed", "cli_and_mcp_lock": "passed",
                    "parent_death": "closed", "input_and_screenshot_requested": False}}, sort_keys=True))
            finally:
                store.revoke()
                if client:
                    client.close()
                if daemon and daemon.poll() is None:
                    daemon.terminate()
                    daemon.wait(timeout=10)
                # A SIGKILLed daemon cannot reap its idle child. Stop only the
                # exact VM test child PID named by the current session record.
                if idle_identity:
                    pid, start = idle_identity
                    if idlewatch._writer_start_ticks(pid) == start and idlewatch._writer_alive(pid):
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                wait_for(lambda: not layers(), description="final frame cleanup")


if __name__ == "__main__":
    main()
