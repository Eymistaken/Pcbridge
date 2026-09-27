"""Session selection must never attach to an arbitrary Hyprland instance."""

from __future__ import annotations

import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pcbridge.desktop import session


class HyprlandSessionTests(unittest.TestCase):
    def test_instance_selection_needs_a_unique_pair_of_owned_sockets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            sockets = []
            for signature, display in (("first", "wayland-1"), ("second", "wayland-2")):
                ipc = runtime / "hypr" / signature / ".socket.sock"
                ipc.parent.mkdir(parents=True)
                for path in (ipc, runtime / display):
                    bound = socket.socket(socket.AF_UNIX)
                    bound.bind(str(path))
                    sockets.append(bound)
            instances = [
                {"instance": "first", "wl_socket": "wayland-1"},
                {"instance": "second", "wl_socket": "wayland-2"},
            ]
            env = {"XDG_RUNTIME_DIR": str(runtime), "XDG_CURRENT_DESKTOP": "Hyprland"}
            try:
                with mock.patch.object(session, "_hyprctl_instances", return_value=instances), \
                        mock.patch.object(session, "_xwayland", return_value=None):
                    self.assertIsNone(session.hyprland_instance(env))
                    self.assertNotIn("WAYLAND_DISPLAY", session.ensure_session_env(env))
                    self.assertNotIn("WAYLAND_DISPLAY", env)
                    selected = {**env, "WAYLAND_DISPLAY": "wayland-2"}
                    self.assertEqual(session.hyprland_instance(selected), instances[1])
                    self.assertEqual(session.ensure_session_env(selected),
                                     ["HYPRLAND_INSTANCE_SIGNATURE", "XDG_SESSION_TYPE"])
                    self.assertEqual(selected["HYPRLAND_INSTANCE_SIGNATURE"], "second")
                    stale = {**selected, "HYPRLAND_INSTANCE_SIGNATURE": "first"}
                    self.assertIsNone(session.hyprland_instance(stale))
                    self.assertEqual(session.desktop_kind(stale), session.UNKNOWN)
                    self.assertEqual(session.ensure_session_env(stale), [])
            finally:
                for bound in sockets:
                    bound.close()

    def test_missing_instance_does_not_inherit_host_gnome_bus(self) -> None:
        env = {"HYPRLAND_INSTANCE_SIGNATURE": "stale", "WAYLAND_DISPLAY": "wayland-9"}
        with mock.patch.object(session, "hyprland_instance", return_value=None), \
                mock.patch.object(session, "_bus_names", return_value=["org.gnome.Shell"]):
            self.assertEqual(session.desktop_kind(env), session.UNKNOWN)

    def test_platform_summary_names_hyprland_without_claiming_validation(self) -> None:
        env = {"XDG_CURRENT_DESKTOP": "Hyprland", "XDG_SESSION_TYPE": "wayland"}
        with mock.patch.object(session, "_bus_names", return_value=[]), \
                mock.patch.object(session, "_hyprland_version", return_value="0.56.2"):
            report = session.platform_summary(env)
        self.assertEqual(report["environment"], session.HYPRLAND)
        self.assertEqual(report["hyprland"], "0.56.2")
        self.assertIsNone(report["gnome_shell"])
        self.assertIsNone(report["plasma"])
        self.assertTrue(any("untested" in note for note in report["notes"]))

    def test_unready_hyprland_and_unknown_cannot_open_or_use_a_grant(self) -> None:
        from pcbridge.desktop import safety

        with tempfile.TemporaryDirectory() as temporary:
            cfg = SimpleNamespace(
                state_dir=Path(temporary),
                desktop=SimpleNamespace(enabled=True, unlock_default_minutes=15,
                                        unlock_max_minutes=60),
            )
            gate = safety.SafetyGate(cfg)
            for kind in (session.HYPRLAND, session.UNKNOWN):
                with self.subTest(kind=kind), \
                        mock.patch.object(session, "desktop_kind", return_value=kind):
                    with self.assertRaisesRegex(ValueError, "visible grant frame"):
                        gate.unlock(1)
                    self.assertFalse(gate.is_unlocked())
                    self.assertEqual(gate.check("screen_capture").code,
                                     safety.ErrorCode.BACKEND_UNAVAILABLE)
                    self.assertEqual(gate.verify(None).code,
                                     safety.ErrorCode.BACKEND_UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
