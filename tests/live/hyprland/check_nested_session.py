"""Verify parent and nested Hyprland instance selection in the disposable VM."""
from __future__ import annotations

import os

if not __debug__:
    raise RuntimeError("Nested session verification requires assertions; Python -O is forbidden")
if os.environ.get("PCBRIDGE_TEST_HYPRLAND_NESTED") != "1":
    raise RuntimeError("Set PCBRIDGE_TEST_HYPRLAND_NESTED=1 in the disposable VM")
if os.uname().nodename != "pcbridge-hyprland":
    raise RuntimeError("Nested session verification runs only on the disposable VM")
if "PCBRIDGE_NATIVE_BIN" in os.environ:
    raise RuntimeError("Native helper overrides are forbidden")

import argparse
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from pcbridge.desktop import hyprland, idlewatch, session  # noqa: E402
from tests.live.hyprland.check_glow_owner import layers, wait_for  # noqa: E402


def instances():
    result = subprocess.run(["hyprctl", "-j", "instances"], check=True,
                            capture_output=True, text=True, timeout=4)
    rows = json.loads(result.stdout)
    assert isinstance(rows, list), rows
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    evidence = {"passed": False, "cleanup": {}}
    failure = None
    child = None
    try:
        assert hyprland.screen_locked() is False
        assert not layers() and idlewatch.read_idle_ms() is None
        parent_env = dict(os.environ)
        parent = session.hyprland_instance(parent_env)
        assert parent is not None and parent_env["HYPRLAND_INSTANCE_SIGNATURE"] == parent["instance"]
        assert parent_env["WAYLAND_DISPLAY"] == parent["wl_socket"]
        assert instances() == [parent]
        parent_outputs = hyprland._query("monitors", json_output=True, env=parent_env)
        assert [(row["name"], row["x"], row["y"]) for row in parent_outputs] == [
            ("Virtual-1", 0, 0), ("Virtual-2", 1280, 0)]
        evidence["parent"] = {"instance": parent["instance"],
                              "wayland_display": parent["wl_socket"],
                              "outputs": [row["name"] for row in parent_outputs]}

        with tempfile.TemporaryDirectory(prefix="pcbridge-nested-hyprland-") as temporary:
            config = Path(temporary) / "hyprland.lua"
            config.write_text('hl.monitor({ output = "", mode = "preferred", '
                              'position = "auto", scale = 1 })\n', encoding="utf-8")
            child_env = dict(parent_env)
            child_env.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
            child_env["HYPRLAND_NO_SD_VARS"] = "1"
            child_env["HYPRLAND_NO_SD_NOTIFY"] = "1"
            with (args.out_dir / "nested-compositor.log").open("w") as log:
                child = subprocess.Popen(["Hyprland", "--config", str(config)],
                    env=child_env, stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, start_new_session=True)

                def child_instance():
                    if child.poll() is not None:
                        raise RuntimeError(f"Nested Hyprland exited {child.returncode} during startup")
                    return next((row for row in instances() if row.get("pid") == child.pid), None)

                nested = wait_for(child_instance, timeout=25,
                                  description="nested Hyprland IPC instance")
                assert nested["instance"] != parent["instance"]
                assert nested["wl_socket"] != parent["wl_socket"]
                nested_env = dict(parent_env, HYPRLAND_INSTANCE_SIGNATURE=nested["instance"],
                                  WAYLAND_DISPLAY=nested["wl_socket"])
                mismatched_env = dict(parent_env, WAYLAND_DISPLAY=nested["wl_socket"])
                ambiguous_env = dict(parent_env)
                ambiguous_env.pop("HYPRLAND_INSTANCE_SIGNATURE")
                ambiguous_env.pop("WAYLAND_DISPLAY")
                assert session.hyprland_instance(parent_env) == parent
                assert session.hyprland_instance(nested_env) == nested
                assert session.hyprland_instance(mismatched_env) is None
                assert session.hyprland_instance(ambiguous_env) is None
                assert session.desktop_kind(parent_env) == session.HYPRLAND
                assert session.desktop_kind(nested_env) == session.HYPRLAND
                assert session.desktop_kind(mismatched_env) == session.UNKNOWN
                evidence["nested"] = {"instance": nested["instance"],
                                      "wayland_display": nested["wl_socket"],
                                      "parent_and_child_selected": True,
                                      "mixed_and_ambiguous_refused": True}

                def nested_output():
                    outputs = hyprland._query("monitors", json_output=True, env=nested_env)
                    return outputs if len(outputs) == 1 and outputs[0]["width"] > 0 else None

                try:
                    child_outputs = wait_for(nested_output, timeout=6,
                                             description="nested Wayland output")
                except AssertionError:
                    child_outputs = []
                    evidence["nested"]["output_status"] = "unpresented; checking child log after exit"
                else:
                    evidence["nested"]["output_status"] = "one Wayland output presented"
                parent_again = hyprland._query("monitors", json_output=True, env=parent_env)
                assert [(row["name"], row["x"], row["y"]) for row in parent_again] == [
                    ("Virtual-1", 0, 0), ("Virtual-2", 1280, 0)]
                evidence["nested"]["outputs"] = [row["name"] for row in child_outputs]
                evidence["passed"] = True
    except BaseException as error:
        failure = error
        evidence["failure"] = {"type": type(error).__name__, "message": str(error)[:1000]}
    finally:
        if child is not None:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
            evidence["cleanup"]["nested_exit"] = child.returncode
            nested_result = evidence.get("nested", {})
            if nested_result.get("output_status", "").startswith("unpresented"):
                runtime = Path(parent_env["XDG_RUNTIME_DIR"])
                child_log = runtime / "hypr" / nested_result["instance"] / "hyprland.log"
                try:
                    diagnostic = child_log.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    diagnostic = ""
                if ("GBM: Failed to allocate a GBM buffer: bo null" in diagnostic and
                        "Swapchain: Failed acquiring a buffer" in diagnostic):
                    nested_result["output_status"] = "VM GBM allocation unavailable"
                elif failure is None:
                    failure = RuntimeError("Nested output absent without the known VM GBM error")
            try:
                wait_for(lambda: instances() == [parent], timeout=8,
                         description="only parent Hyprland remains")
                evidence["cleanup"]["parent_only"] = True
            except BaseException as error:
                evidence["cleanup"]["parent_only"] = False
                if failure is None:
                    failure = error
        if hyprland.screen_locked() is not False or layers() or idlewatch.read_idle_ms() is not None:
            evidence["cleanup"]["desktop_clean"] = False
            if failure is None:
                failure = RuntimeError("Nested session left lock, frame, or idle state")
        else:
            evidence["cleanup"]["desktop_clean"] = True
        final_outputs = hyprland.monitors()
        evidence["cleanup"]["parent_outputs"] = [row["name"] for row in final_outputs]
        if [(row["name"], row["x"], row["y"]) for row in final_outputs] != [
                ("Virtual-1", 0, 0), ("Virtual-2", 1280, 0)]:
            if failure is None:
                failure = RuntimeError("Nested session changed parent output geometry")
        evidence["passed"] = evidence["passed"] and failure is None
        (args.out_dir / "nested-session.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print({"passed": evidence["passed"], "failure": evidence.get("failure"),
               "cleanup": evidence["cleanup"]})
    if failure:
        raise failure


if __name__ == "__main__":
    main()
