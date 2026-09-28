"""Hyprland output JSON must enter the one shared canvas coordinate model."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from pcbridge.desktop import hyprland, monitors, session


def output(name: str, x: int, y: int, *, scale: float = 1,
           transform: int = 0, focused: bool = False) -> dict:
    return {
        "name": name, "width": 1280, "height": 800, "x": x, "y": y,
        "scale": scale, "transform": transform, "focused": focused,
        "disabled": False, "mirrorOf": "none", "description": name,
        "make": "QEMU", "model": "Monitor", "serial": name,
    }


class HyprlandMonitorTests(unittest.TestCase):
    def tearDown(self) -> None:
        monitors.invalidate_cache()

    def test_shared_transport_cases_resolve_to_the_same_canvas(self) -> None:
        path = Path(__file__).resolve().parents[1] / "fixtures/native/hyprland_monitor_cases.json"
        cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
        for case in cases:
            with self.subTest(case=case["name"]):
                state = monitors._hyprland_state(case["hyprland"])
                self.assertEqual(state, case["state"])
                self.assertEqual(monitors.canvas_size(monitors.resolve_state(state)),
                                 tuple(case["canvas"]))

    def test_raw_pixels_scale_rotation_and_negative_origin_resolve_once(self) -> None:
        raw = [output("right", 0, 0, focused=True),
               output("left", -640, -1024, scale=1.25, transform=1)]
        table = monitors.resolve_state(monitors._hyprland_state(raw))
        self.assertEqual([m.connector for m in table], ["left", "right"])
        left, right = table
        self.assertEqual((left.platform_x, left.platform_y), (-640, -1024))
        self.assertEqual((left.x, left.y, left.width, left.height), (0, 0, 640, 1024))
        self.assertEqual(left.source_pixel_size, (800, 1280))
        self.assertEqual((right.x, right.y, right.width, right.height),
                         (640, 1024, 1280, 800))
        self.assertTrue(right.primary)
        self.assertEqual(monitors.platform_origin(table), (-640, -1024))
        self.assertEqual(monitors.canvas_size(table), (1920, 1824))

    def test_focused_monitor_is_primary_and_cache_reloads_topology(self) -> None:
        one = [output("Virtual-1", 0, 0, focused=True)]
        two = [output("Virtual-1", 0, 0), output("Virtual-2", 1280, 0, focused=True)]
        with mock.patch.object(session, "desktop_kind", return_value=session.HYPRLAND), \
                mock.patch.object(hyprland, "monitors", side_effect=[one, two]):
            first = monitors.list_monitors(use_cache=False)
            second = monitors.list_monitors(use_cache=False)
        self.assertNotEqual(monitors.topology_id(first), monitors.topology_id(second))
        self.assertEqual([m.primary for m in second], [False, True])

    def test_unusable_geometry_and_mirror_fail_closed(self) -> None:
        invalid = [
            {**output("A", 0, 0), "scale": 0},
            {**output("A", 0, 0), "scale": float("nan")},
            {**output("A", 0, 0), "transform": 8},
            {**output("A", 0, 0), "width": False},
            {**output("A", 0, 0), "mirrorOf": "B"},
        ]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(monitors.MonitorError):
                monitors._hyprland_state([raw])
        with self.assertRaises(monitors.MonitorError):
            monitors._hyprland_state([output("A", 0, 0), output("A", 1280, 0)])
        with self.assertRaises(monitors.MonitorError):
            monitors._hyprland_state([output("A", 0, 0, focused=True),
                                     output("B", 1280, 0, focused=True)])

    def test_focus_changes_default_selection_without_invalidating_geometry(self) -> None:
        before = monitors.resolve_state(monitors._hyprland_state([
            output("A", 0, 0, focused=True), output("B", 1280, 0)]))
        after = monitors.resolve_state(monitors._hyprland_state([
            output("A", 0, 0), output("B", 1280, 0, focused=True)]))
        self.assertEqual(monitors.resolve(None, before).connector, "A")
        self.assertEqual(monitors.resolve(None, after).connector, "B")
        self.assertEqual(monitors.topology_id(before), monitors.topology_id(after))
        # A configured primary output still changes topology on GNOME/KDE.
        from dataclasses import replace
        self.assertNotEqual(monitors.topology_id([replace(m, primary_is_focus=False) for m in before]),
                            monitors.topology_id([replace(m, primary_is_focus=False) for m in after]))

    def test_ipc_failure_does_not_fall_back_to_xrandr(self) -> None:
        with mock.patch.object(session, "desktop_kind", return_value=session.HYPRLAND), \
                mock.patch.object(hyprland, "monitors",
                                  side_effect=hyprland.HyprlandIPCError("offline")), \
                mock.patch.object(monitors, "_from_xrandr") as xrandr:
            with self.assertRaisesRegex(monitors.MonitorError, "offline"):
                monitors.list_monitors(use_cache=False)
        xrandr.assert_not_called()


if __name__ == "__main__":
    unittest.main()
