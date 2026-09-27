"""Hyprland keybind context comes from read-only, current IPC state."""

from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from pcbridge.desktop import hyprland, presentation, session
from pcbridge.desktop.capabilities import AuthorizationStatus, CapabilitySnapshot


class HyprlandBindsTests(unittest.TestCase):
    def test_query_selects_one_instance_and_never_dispatches(self) -> None:
        commands = []

        def run(argv, **kwargs):
            commands.append(argv)
            value = '[{"key":"Q","modmask":64,"submap_universal":"false"}]' \
                if argv[-1] == "binds" else "resize\n"
            return subprocess.CompletedProcess(argv, 0, value, "")

        with mock.patch.object(session, "hyprland_instance",
                               return_value={"instance": "session-a"}), \
                mock.patch.object(hyprland.subprocess, "run", side_effect=run):
            snapshot = hyprland.bindings_snapshot({"XDG_CURRENT_DESKTOP": "Hyprland"})

        self.assertEqual(snapshot["count"], 1)
        self.assertEqual(snapshot["active_submap"], "resize")
        self.assertEqual(snapshot["bindings"][0]["submap_universal"], "false")
        self.assertEqual(commands, [
            ["hyprctl", "-i", "session-a", "-j", "binds"],
            ["hyprctl", "-i", "session-a", "submap"],
        ])

    def test_all_runtime_fields_and_future_fields_survive(self) -> None:
        raw = [{"key": "Q", "modmask": 64, "release": True, "repeat": False,
                "locked": True, "mouse": False, "non_consuming": True,
                "auto_consuming": False, "device": "keyboard-a",
                "allow_input_capture": True, "submap": "resize",
                "submap_universal": "true", "description": "my shortcut",
                "dispatcher": "__lua", "arg": "5", "future_field": {"new": 1}}]
        with mock.patch.object(hyprland, "_query", side_effect=[raw, "resize"]):
            snapshot = hyprland.bindings_snapshot()
        self.assertEqual(snapshot["bindings"], raw)
        self.assertEqual(snapshot["active_submap"], "resize")

    def test_unavailable_or_invalid_ipc_is_explicit(self) -> None:
        with mock.patch.object(session, "hyprland_instance", return_value=None):
            snapshot = hyprland.bindings_snapshot()
        self.assertFalse(snapshot["available"])
        self.assertIn("unambiguous", snapshot["reason"])
        with mock.patch.object(hyprland, "_query", return_value={"not": "a list"}):
            self.assertFalse(hyprland.bindings_snapshot()["available"])
        with self.assertRaises(ValueError):
            hyprland._query("dispatch", json_output=False)

    def test_large_table_remains_complete_in_structured_context(self) -> None:
        raw = [{"key": f"K{index}", "modmask": index, "new_flag": index % 2 == 0}
               for index in range(500)]
        platform = {
            "environment": "hyprland", "hyprland": "0.56.2", "plasma": None,
            "gnome_shell": None, "session_type": "wayland", "desktop": "Hyprland",
            "screencast": False, "remote_desktop": False, "notes": [],
        }
        bindings = {"available": True, "active_submap": "resize", "bindings": raw,
                    "count": len(raw), "source": "hyprctl runtime IPC"}
        capability = CapabilitySnapshot({}, {}, AuthorizationStatus(False, 0, 0, "unknown", 0))
        with mock.patch.object(session, "platform_summary", return_value=platform), \
                mock.patch.object(hyprland, "bindings_snapshot", return_value=bindings):
            result = presentation.capabilities_result(capability)
        self.assertEqual(len(result.structured_content["hyprland_bindings"]["bindings"]), 500)
        self.assertIn("active submap: resize", result.content[0].text)
        self.assertIn("complete binding table", result.content[0].text)
        self.assertNotIn('"key": "K499"', result.content[0].text)


if __name__ == "__main__":
    unittest.main()
