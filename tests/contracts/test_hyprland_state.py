"""Hyprland lock and idle are independent authoritative observations."""

import unittest
from unittest import mock

from pcbridge.desktop import compositor, hyprland, idlewatch, safety


class HyprlandStateTests(unittest.TestCase):
    def test_lock_parser_accepts_only_a_boolean(self):
        for raw, expected in [({"locked": True}, True), ({"locked": False}, False),
                              ({"locked": "false"}, None), ({"locked": 0}, None),
                              ({}, None), (False, None)]:
            with self.subTest(raw=raw), mock.patch.object(hyprland, "_query", return_value=raw) as query:
                self.assertIs(hyprland.screen_locked(), expected)
                query.assert_called_once_with("locked", json_output=True)

    def test_failed_lock_ipc_is_unknown(self):
        with mock.patch.object(hyprland, "_query", side_effect=hyprland.HyprlandIPCError("offline")):
            self.assertIsNone(hyprland.screen_locked())

    def test_safety_observes_hyprland_without_gnome_or_locker_process_inference(self):
        for locked, expected in [(True, safety.ScreenLockState.KNOWN_LOCKED),
                                 (False, safety.ScreenLockState.KNOWN_UNLOCKED),
                                 (None, safety.ScreenLockState.UNKNOWN)]:
            with mock.patch.object(compositor, "current", return_value=compositor.HYPRLAND), \
                    mock.patch.object(hyprland, "screen_locked", return_value=locked), \
                    mock.patch.object(idlewatch, "read_idle_ms", return_value=4567), \
                    mock.patch.object(safety, "_busctl_json") as bus:
                self.assertEqual(safety.observe_screen_lock().state, expected)
                self.assertEqual(safety.idle_ms(), 4567)
                bus.assert_not_called()


if __name__ == "__main__":
    unittest.main()
