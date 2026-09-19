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
        self.assertIn("GNOME eklentisi", result)

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
            result, "target-app | Target Window one alindi (GNOME aramasi, yedek yol)"
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


if __name__ == "__main__":
    unittest.main()
