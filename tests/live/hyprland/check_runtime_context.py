"""Measure read-only runtime binding context using VM-only Lua fixtures.

Run through scripts/dev/hyprland-vm.sh session with
PCBRIDGE_TEST_HYPRLAND_CONTEXT=1. No callback is executed and no input,
grant, capture, or config-file discovery is used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from unittest import mock


def containment():
    if not __debug__:
        raise RuntimeError("Live verification requires assertions; do not use -O")
    if (os.environ.get("PCBRIDGE_TEST_HYPRLAND_CONTEXT") != "1"
            or os.uname().nodename != "pcbridge-hyprland"):
        raise RuntimeError("Use only the explicitly enabled disposable Hyprland VM")


def main():
    containment()  # Before imports, IPC discovery, or any fixture mutation.
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))
    from pcbridge.desktop import hyprland, session
    from pcbridge.desktop.capabilities import AuthorizationStatus, CapabilitySnapshot
    from pcbridge.desktop.presentation import capabilities_result
    from tests.live.hyprland.check_glow_owner import layers

    assert hyprland.screen_locked() is False
    assert not layers(), "Do not overlap a PcBridge visible grant"
    selected = session.hyprland_instance(dict(os.environ))
    assert selected is not None
    instance = selected["instance"]
    run = subprocess.run

    def ipc(command, argument=None, *, json_output=False):
        args = ["hyprctl", "-i", instance]
        if json_output:
            args.append("-j")
        args.append(command)
        if argument is not None:
            args.append(argument)
        result = run(args, check=True, text=True, capture_output=True, timeout=3)
        if command in {"eval", "dispatch"}:
            assert result.stdout.strip() == "ok", result.stdout
        return json.loads(result.stdout) if json_output else result.stdout.strip()

    baseline = ipc("binds", json_output=True)
    assert ipc("submap") == "default", "Require the authoritative default submap"
    token = "pcbridge_context_" + uuid.uuid4().hex
    submap = token + "_submap"
    # Separate fixtures avoid flags Hyprland explicitly declares incompatible.
    fixtures = [
        ("F20", {"repeating": True, "locked": True,
                 "non_consuming": True, "allow_input_capture": True}, ""),
        ("F21", {"release": True, "submap_universal": True}, ""),
        ("mouse:275", {}, ""),
        ("F22", {"device": {"inclusive": True, "list": [token + "_absent_device"]}}, submap),
    ]
    keys = {key.casefold() for key, _, _ in fixtures}
    # Conservatively refuse any baseline occurrence, even with other modifiers.
    assert not any(str(row.get("key", "")).casefold() in keys for row in baseline)
    assert not any(row.get("submap") == submap for row in baseline)
    platform = session.platform_summary()
    assert platform["environment"] == "hyprland"
    snapshot = CapabilitySnapshot({}, {}, AuthorizationStatus(False, 0, 0, "unlocked", 0))
    read_requests = []

    def readonly_run(args, **kwargs):
        assert args in (["hyprctl", "-i", instance, "-j", "binds"],
                        ["hyprctl", "-i", instance, "submap"]), args
        read_requests.append(args[-1])
        return run(args, **kwargs)

    def observe(expected_submap, expected_count):
        raw = ipc("binds", json_output=True)
        active = ipc("submap")
        assert active == expected_submap
        # Pin already-measured selection/platform metadata so the spy covers
        # the actual production context requests without unrelated probes.
        with mock.patch.object(session, "hyprland_instance", return_value=selected), \
                mock.patch.object(session, "platform_summary", return_value=platform), \
                mock.patch.object(hyprland.subprocess, "run", side_effect=readonly_run):
            direct = hyprland.bindings_snapshot()
            presented = capabilities_result(snapshot).structured_content["hyprland_bindings"]
        assert direct == presented
        assert direct["available"] and direct["active_submap"] == active
        assert direct["count"] == expected_count == len(raw)
        # JSON serialization checks types as well as values (bool != integer).
        assert json.dumps(direct["bindings"], sort_keys=True) == json.dumps(raw, sort_keys=True)
        return raw

    # Establish ownership before cleanup; never unbind a preexisting table.
    ipc("eval", f"assert(_G[{json.dumps(token)}] == nil)")
    evidence = {}
    try:
        observe("default", len(baseline))
        ipc("eval", f"assert(_G[{json.dumps(token)}] == nil); _G[{json.dumps(token)}] = {{}}")
        for index, (key, flags, owner) in enumerate(fixtures):
            options = dict(flags, description=token + "_" + str(index))
            def lua(value):
                if isinstance(value, bool):
                    return "true" if value else "false"
                if isinstance(value, dict):
                    return "{" + ",".join(k + "=" + lua(v) for k, v in value.items()) + "}"
                if isinstance(value, list):
                    return "{" + ",".join(lua(v) for v in value) + "}"
                return json.dumps(value)
            bind = (f'table.insert(_G[{json.dumps(token)}], hl.bind('
                    f'{json.dumps("CTRL+ALT+SHIFT+" + key)}, function() end, {lua(options)}))')
            if owner:
                bind = f"hl.define_submap({json.dumps(owner)}, function() {bind} end)"
            ipc("eval", bind)
        rows = observe("default", len(baseline) + len(fixtures))
        added = [row for row in rows if str(row.get("description", "")).startswith(token)]
        assert len(added) == len(fixtures)
        by_description = {row["description"]: row for row in added}
        for index, (key, _, owner) in enumerate(fixtures):
            row = by_description[token + "_" + str(index)]
            assert row["key"] == key and row["modmask"] == 13
            assert row["submap"] == owner
            assert row["dispatcher"] == "__lua" and isinstance(row["arg"], str)
        repeat, release, mouse, device = [by_description[token + "_" + str(i)] for i in range(4)]
        assert repeat["repeat"] is True and repeat["locked"] is True
        assert repeat["non_consuming"] is True and repeat["allow_input_capture"] is True
        assert release["release"] is True
        assert release["submap_universal"] in (True, "true")
        # v0.56.2 LuaBindingsToplevel.cpp parseKeyString/hlBind never set
        # kb.mouse, including for dispatcher callbacks; this is a mouse-key
        # fixture, not evidence of legacy mouse=true dispatcher semantics.
        assert mouse["key"] == "mouse:275" and mouse["mouse"] is False
        ipc("dispatch", f"hl.dsp.submap({json.dumps(submap)})")
        assert observe(submap, len(rows)) == rows
        ipc("dispatch", 'hl.dsp.submap("reset")')
        assert observe("default", len(rows)) == rows
        evidence = {"baseline_count": len(baseline), "fixture_count": len(added),
                    "fixtures": added, "transitions": ["default", submap, "default"],
                    "device_fields_reported": sorted(k for k in device if "device" in k.lower()),
                    "opaque_callbacks": "not interpreted or executed",
                    "mouse_flag_coverage": "Lua mouse-key fixture reports false; true not covered"}
    finally:
        # Handles are recorded immediately after each successful registration.
        # Their v0.56.2 unbind method removes matching key/modmask entries;
        # baseline collision refusal and distinct fixture keys protect originals.
        ipc("dispatch", 'hl.dsp.submap("reset")')
        ipc("eval", f'local t = _G[{json.dumps(token)}]; if t then '
            f'for _, b in ipairs(t) do b:unbind() end; _G[{json.dumps(token)}] = nil end')
        final = observe("default", len(baseline))
        assert json.dumps(final, sort_keys=True) == json.dumps(baseline, sort_keys=True)
        assert hyprland.screen_locked() is False and not layers()
    evidence.update(cleanup="exact baseline restored", context_requests=read_requests)
    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    main()
