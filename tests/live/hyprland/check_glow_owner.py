"""Measure native frame ownership in the disposable VM, without desktop input.

Scratch LeaseStore identities authorize only this drawing observer. They do
not open the production SafetyGate or exercise capture/input authorization.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from pcbridge.desktop import glowstate, hyprland, monitors as monitorlib  # noqa: E402
from pcbridge.desktop.lease import LeaseStore, LeaseToken  # noqa: E402


def wait_for(predicate, *, timeout=8, description="observation"):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {description}")


def layers():
    return [layer for output in hyprland._query("layers", json_output=True).values()
            for level in output["levels"].values() for layer in level
            if layer["namespace"] == "pcbridge-glow"]


def monitor_rule(row, *, scale=None, transform=None):
    # Diagnostic configuration changes are confined to the dedicated VM.
    mode = f'{row["width"]}x{row["height"]}@{row["refreshRate"]:.2f}'
    source = ('hl.monitor({ output = ' + json.dumps(row["name"])
              + ', mode = ' + json.dumps(mode)
              + ', position = ' + json.dumps(f'{row["x"]}x{row["y"]}')
              + f', scale = {row["scale"] if scale is None else scale}'
              + f', transform = {row["transform"] if transform is None else transform} }} )')
    subprocess.run(["hyprctl", "eval", source], check=True, capture_output=True, timeout=3)


def main():
    assert os.environ.get("PCBRIDGE_TEST_LIVE_HYPRLAND") == "1"
    assert os.uname().nodename == "pcbridge-hyprland", "Use the dedicated VM"
    assert hyprland.screen_locked() is False
    assert not layers(), "Refuse to overlap a resident frame"
    monitors = hyprland.monitors()
    assert len(monitors) == 2
    binary = (ROOT / "rust/target/debug/pcbridge-native").resolve()
    processes = []
    evidence = {}
    with tempfile.TemporaryDirectory(prefix="pcbridge-frame-observer-") as temporary, \
            tempfile.TemporaryFile() as errors:
        directory = Path(temporary).resolve()
        store = LeaseStore(directory)

        def grant(seconds=90):
            now = time.time()
            return store.grant(until=now + seconds, reason="Drawing-only native observer probe",
                               granted=now, granted_by="vm-test").token()

        def spawn(token):
            process = subprocess.Popen([str(binary), "glow-watch", str(directory),
                                        token.grant_id, str(token.revoke_epoch)],
                                       stdout=subprocess.DEVNULL, stderr=errors)
            processes.append(process)
            return process

        def health(token):
            return glowstate.read(directory, token, binary=binary)

        def ready(process, token):
            def observation():
                assert process.poll() is None, "Frame owner exited before presentation"
                return health(token)
            record = wait_for(observation, description="fresh grant-bound presentation")
            assert record["pid"] == process.pid and record["owner_pid"] == os.getpid()
            assert record["strip_count"] == 8 and len(layers()) == 8
            return record

        try:
            token = grant()
            process = spawn(token)
            first = ready(process, token)
            first_age_ms = int(time.time() * 1000) - first["presented_unix_ms"]
            assert health(LeaseToken("different-grant", token.revoke_epoch)) is None
            assert health(LeaseToken(token.grant_id, token.revoke_epoch + 1)) is None
            with mock.patch.dict(os.environ, HYPRLAND_INSTANCE_SIGNATURE="foreign-session"):
                assert health(token) is None
            evidence["identity_and_native_self_validation"] = "passed"

            # An alive but stopped writer cannot keep refreshing its proof.
            process.send_signal(signal.SIGSTOP)
            time.sleep(1.3)
            assert health(token) is None
            process.send_signal(signal.SIGCONT)
            ready(process, token)
            evidence["stopped_writer_freshness_ms"] = 1300

            # std::fs::File::lock must contend with Python's LeaseStore flock.
            # A publication blocked past freshness must fail closed on resume.
            with store.lock_path.open("r+b") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                time.sleep(1.3)
                assert health(token) is None, "Native writer ignored Python's lease lock"
                fcntl.flock(lock, fcntl.LOCK_UN)
            assert process.wait(timeout=5) != 0
            wait_for(lambda: not layers(), description="stale writer teardown")
            process = spawn(token)
            ready(process, token)
            evidence["python_native_flock_interoperability"] = "passed"

            stable_topology = health(token)["topology_id"]
            for row in monitors:
                command = "hl.dsp.focus({ monitor = " + json.dumps(row["name"]) + " })"
                subprocess.run(["hyprctl", "dispatch", command], check=True, capture_output=True, timeout=3)
                focused = monitorlib.list_monitors(use_cache=False)
                assert monitorlib.resolve(None, focused).connector == row["name"]
                assert monitorlib.topology_id(focused) == stable_topology
                until = time.monotonic() + 1.2
                while time.monotonic() < until:
                    record = health(token)
                    assert record and record["topology_id"] == stable_topology, "Focus rebuilt visible geometry"
                    time.sleep(0.05)
            evidence["focus_preserves_physical_topology_and_visibility"] = "passed"

            original_topology = health(token)["topology_id"]
            monitor_rule(monitors[1], scale=1.25, transform=1)
            def changed_topology():
                record = health(token)
                return record if record and record["topology_id"] != original_topology else None
            rebuilt = wait_for(changed_topology,
                               description="fractional rotated topology rebuild")
            assert rebuilt["pid"] == process.pid and rebuilt["strip_count"] == 8
            for row in monitors:
                monitor_rule(row)
            geometry_keys = ("name", "x", "y", "width", "height", "scale", "transform")
            def original_geometry():
                current = hyprland.monitors()
                return [{key: row[key] for key in geometry_keys} for row in current] == [
                    {key: row[key] for key in geometry_keys} for row in monitors]
            wait_for(original_geometry, description="restored compositor geometry")
            expected_topology = monitorlib.topology_id(monitorlib.list_monitors(use_cache=False))
            def restored_topology():
                record = health(token)
                return record and record["topology_id"] == expected_topology
            try:
                wait_for(restored_topology, description="restored topology presentation")
            except AssertionError:
                raw = json.loads((directory / glowstate.STATE_FILE).read_text())
                print(json.dumps({"original_topology": original_topology,
                                  "rebuilt_topology": rebuilt["topology_id"],
                                  "restored_topology": raw["topology_id"],
                                  "restored_ready": raw["ready"], "owner_exit": process.poll(),
                                  "runtime_monitors": [{key: row[key] for key in
                                      ("name", "x", "y", "width", "height", "scale", "transform", "focused")}
                                      for row in hyprland.monitors()]}, sort_keys=True), file=sys.stderr)
                raise
            evidence["topology_rebuild_same_identity"] = "passed"

            replacement = grant()
            assert health(replacement) is None
            next_process = spawn(replacement)
            ready(next_process, replacement)
            assert process.wait(timeout=5) == 0
            time.sleep(0.6)
            assert health(replacement), "Old shutdown clobbered replacement health"
            assert health(token) is None
            store.revoke()
            assert next_process.wait(timeout=5) == 0
            assert health(replacement) is None
            wait_for(lambda: not layers(), description="revoked frame teardown")
            evidence["replacement_and_revoke"] = "passed"

            expiring = grant(3)
            expiring_process = spawn(expiring)
            ready(expiring_process, expiring)
            assert expiring_process.wait(timeout=5) == 0
            assert health(expiring) is None
            wait_for(lambda: not layers(), description="expired frame teardown")
            evidence["expiry"] = "passed"

            killed = grant()
            killed_process = spawn(killed)
            ready(killed_process, killed)
            killed_process.kill()
            killed_process.wait(timeout=5)
            assert health(killed) is None
            wait_for(lambda: not layers(), description="killed helper teardown")
            evidence["helper_death"] = "passed"

            # The owner is an independent test process, never the compositor.
            orphan_token = grant()
            child_source = ("import subprocess,sys,time; p=subprocess.Popen(sys.argv[1:]); "
                            "print(p.pid,flush=True); time.sleep(30)")
            parent = subprocess.Popen([sys.executable, "-c", child_source, str(binary), "glow-watch",
                                       str(directory), orphan_token.grant_id, str(orphan_token.revoke_epoch)],
                                      stdout=subprocess.PIPE, stderr=errors, text=True)
            processes.append(parent)
            int(parent.stdout.readline())
            wait_for(lambda: health(orphan_token), description="child-owned frame presentation")
            parent.kill()
            parent.wait(timeout=5)
            assert health(orphan_token) is None
            wait_for(lambda: not layers(), description="owner death teardown")
            evidence["owner_death"] = "passed"
            evidence["first_presentation"] = {"strip_count": first["strip_count"],
                                               "age_ms": first_age_ms}
            print(json.dumps({"native_frame_owner": evidence}, sort_keys=True))
        finally:
            store.revoke()
            for process in processes:
                if process.poll() is None:
                    process.send_signal(signal.SIGCONT)
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            for row in monitors:
                monitor_rule(row)
            errors.seek(0)
            diagnostic = errors.read(8192).decode(errors="replace")
            if diagnostic:
                print(diagnostic, file=sys.stderr)


if __name__ == "__main__":
    main()
