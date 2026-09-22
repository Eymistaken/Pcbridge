#!/usr/bin/env python3
"""Contracts for the GNOME extension window-focus fast path and fallback.

The order around them (already in front, cold launch, which names may be
searched) is in `test_window_operations.py` (Task 6.4).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.desktop import apps  # noqa: E402

# An installed application: only those are ever typed into GNOME search.
TARGET = apps.Entry("org.example.Target", "Target", False, ("Target",), (), "target-app")


class RecordingBackend:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def key(self, keys: str) -> None:
        self.events.append(("key", keys))

    def type_text(self, text: str, *, raw: bool) -> None:
        self.events.append(("type", text, raw))


class FocusFastPathTests(unittest.TestCase):
    def test_extension_success_skips_gnome_search(self) -> None:
        backend = RecordingBackend()
        focused = mock.Mock(return_value=("Other", "Other"))
        with (
            mock.patch.object(apps, "_extension_activate", return_value=True),
            mock.patch.object(apps.time, "sleep") as sleep,
        ):
            result = apps.focus("Pcbridge Nested A", backend, focused)

        self.assertEqual(backend.events, [])
        self.assertEqual(sleep.call_args_list, [])
        self.assertIn("GNOME extension", result)

    def test_extension_not_installed_preserves_existing_search_path(self) -> None:
        backend = RecordingBackend()
        focused = mock.Mock(side_effect=[
            ("Other", "Other"), ("target-app", "Target Window"),
        ])
        with (
            mock.patch.object(apps.subprocess, "run", side_effect=FileNotFoundError),
            mock.patch.object(apps, "entries", return_value=[TARGET]),
            mock.patch.object(apps.time, "sleep") as sleep,
        ):
            result = apps.focus("Target", backend, focused, settle=0)

        self.assertEqual(
            backend.events,
            [("key", "super"), ("type", "Target", True), ("key", "Return")],
        )
        self.assertEqual(
            sleep.call_args_list,
            [mock.call(0), mock.call(apps.SEARCH_RESULTS), mock.call(apps.SEARCH_ACTIVATE)],
        )
        self.assertEqual(
            result, "target-app | Target Window raised (GNOME search, fallback path)"
        )
        # Odak IKI kez okunuyor ve ikisi de kullaniliyor: once "zaten odakta
        # mi" (oyleyse hic tus gitmez, Task 6.4), sonra aramanin sonucu. Eski
        # koddaki bastaki okuma kullanilmiyordu ve silinmisti; bu o degil.
        self.assertEqual(focused.call_count, 2)

    def test_extension_target_miss_falls_back_to_search(self) -> None:
        backend = RecordingBackend()
        focused = mock.Mock(side_effect=[
            ("Other", "Other"), ("target-app", "Target Window"),
        ])
        reply = SimpleNamespace(returncode=0, stdout="b false\n", stderr="")
        with (
            mock.patch.object(apps.subprocess, "run", return_value=reply),
            mock.patch.object(apps, "entries", return_value=[TARGET]),
            mock.patch.object(apps.time, "sleep"),
        ):
            apps.focus("Target", backend, focused, settle=0)

        self.assertEqual(
            backend.events,
            [("key", "super"), ("type", "Target", True), ("key", "Return")],
        )

    def test_extension_false_falls_back_to_search(self) -> None:
        backend = RecordingBackend()
        focused = mock.Mock(side_effect=[
            ("Other", "Other"), ("target-app", "Target Window"),
        ])
        with (
            mock.patch.object(apps, "_extension_activate", return_value=False),
            mock.patch.object(apps, "entries", return_value=[TARGET]),
            mock.patch.object(apps.time, "sleep"),
        ):
            apps.focus("Target", backend, focused, settle=0)

        self.assertEqual(
            backend.events,
            [("key", "super"), ("type", "Target", True), ("key", "Return")],
        )

    def test_dbus_call_uses_bounded_argument_vector(self) -> None:
        reply = SimpleNamespace(returncode=0, stdout="b true\n", stderr="")
        with mock.patch.object(apps.subprocess, "run", return_value=reply) as run:
            self.assertTrue(apps._extension_activate("A target; $(not shell)"))

        argv = run.call_args.args[0]
        self.assertEqual(argv[-2:], ["s", "A target; $(not shell)"])
        self.assertIn("--timeout=500ms", argv)
        self.assertFalse(run.call_args.kwargs.get("shell", False))
        self.assertEqual(run.call_args.kwargs["timeout"], 1.0)

    def test_extension_availability_is_a_read_only_name_owner_probe(self) -> None:
        reply = SimpleNamespace(returncode=0, stdout="b true\n", stderr="")
        with mock.patch.object(apps.subprocess, "run", return_value=reply) as run:
            self.assertTrue(apps.extension_focus_available())

        argv = run.call_args.args[0]
        self.assertEqual(argv[-3:-1], ["NameHasOwner", "s"])
        self.assertEqual(argv[-1], apps._FOCUS_BUS_NAME)
        self.assertEqual(run.call_args.kwargs["timeout"], 1.0)


class ExtensionFocusSourceTests(unittest.TestCase):
    """Step 8.1: the shell extension as a second source for the focused window."""

    def reply(self, stdout: str = "", returncode: int = 0):
        return SimpleNamespace(stdout=stdout, returncode=returncode, stderr="")

    def test_a_found_window_is_named_by_its_app_id_then_wm_class(self) -> None:
        cases = [
            ('{"type":"bsss","data":[true,"Minecraft* 26.3","","Minecraft* 26.3"]}',
             ("Minecraft* 26.3", "Minecraft* 26.3")),
            ('{"type":"bsss","data":[true,"org.gnome.TextEditor",'
             '"org.gnome.TextEditor","Belge"]}',
             ("org.gnome.TextEditor", "Belge")),
            ('{"type":"bsss","data":[true,"","",""]}', ("?", "")),
        ]
        for stdout, expected in cases:
            with self.subTest(stdout=stdout), mock.patch.object(
                apps.subprocess, "run", return_value=self.reply(stdout)
            ) as run:
                self.assertEqual(apps.extension_focused_window(), expected)
            argv = run.call_args.args[0]
            self.assertIn("FocusedWindow", argv)
            self.assertIn("--json=short", argv)

    def test_anything_else_is_no_answer_not_a_guess(self) -> None:
        for reply in (
            self.reply('{"type":"bsss","data":[false,"","",""]}'),
            # The extension that runs until the next login has no such method.
            self.reply("", returncode=1),
            self.reply('{"type":"b","data":[true]}'),
            self.reply("not json"),
            self.reply('{"type":"bsss","data":[true,"a"]}'),
        ):
            with self.subTest(reply=reply), mock.patch.object(
                apps.subprocess, "run", return_value=reply
            ):
                self.assertIsNone(apps.extension_focused_window())
        with mock.patch.object(
            apps.subprocess, "run", side_effect=apps.subprocess.TimeoutExpired("busctl", 1)
        ):
            self.assertIsNone(apps.extension_focused_window())

    def test_device_ops_falls_back_only_when_atspi_cannot_say(self) -> None:
        from pcbridge.desktop import ops as opslib

        tree = mock.Mock()
        device = opslib.DeviceOps(mock.Mock(), tree, SimpleNamespace(
            shot_search_dirs=[], desktop=SimpleNamespace(
                agent_shot_max_age_seconds=60, ambiguous_coord_guard=True,
            ),
        ), mock.Mock())

        tree.focused_window.return_value = ("gnome-text-editor", "Belge")
        with mock.patch.object(apps, "extension_focused_window") as shell:
            self.assertEqual(device.focused(), "gnome-text-editor | Belge")
            shell.assert_not_called()

        tree.focused_window.side_effect = RuntimeError("No window has the focus")
        with mock.patch.object(
            apps, "extension_focused_window", return_value=("Minecraft", "Minecraft 26.3")
        ):
            self.assertEqual(device.focused(), "Minecraft | Minecraft 26.3")

        with mock.patch.object(apps, "extension_focused_window", return_value=None):
            with self.assertRaises(opslib.FocusUnreadable) as caught:
                device.focused()
        self.assertIn("No window has the focus", str(caught.exception))
        self.assertIn("the shell extension could not tell", str(caught.exception))

    def test_a_batch_in_a_window_atspi_cannot_see_now_runs(self) -> None:
        """The Minecraft refusal: clicks go through when the shell names focus."""
        from pcbridge.desktop import batch as batchlib
        from pcbridge.desktop import ops as opslib

        backend = mock.Mock()
        backend.move_by.return_value = (40, 0)
        tree = mock.Mock()
        tree.focused_window.side_effect = RuntimeError("No window has the focus")
        device = opslib.DeviceOps(backend, tree, SimpleNamespace(
            shot_search_dirs=[], desktop=SimpleNamespace(
                agent_shot_max_age_seconds=60, ambiguous_coord_guard=True,
            ),
        ), mock.Mock())
        plan = batchlib.parse('[{"a":"move_by","dx":40,"dy":0},{"a":"click"},'
                              '{"a":"right_click","hold_ms":80}]')
        with mock.patch.object(
            apps, "extension_focused_window", return_value=("Minecraft", "Minecraft 26.3")
        ):
            result = batchlib.run(plan, device, budget=60, sleep=lambda s: None)
        self.assertEqual((result.done, result.stopped), (3, ""), result.detail)
        self.assertEqual(result.focus_start, "Minecraft | Minecraft 26.3")

        with mock.patch.object(apps, "extension_focused_window", return_value=None):
            refused = batchlib.run(plan, device, budget=60, sleep=lambda s: None)
        self.assertEqual((refused.done, refused.stopped), (0, "focus"))
        self.assertIn("the shell extension could not tell", refused.detail)


if __name__ == "__main__":
    unittest.main()
