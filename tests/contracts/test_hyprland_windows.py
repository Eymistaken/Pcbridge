"""Compositor window observations do not depend on AT-SPI participation."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from pcbridge.desktop import apps, compositor, hyprland, ops
from pcbridge.desktop.errors import DesktopError, ErrorCode
from pcbridge.desktop.hyprland_windows import HyprlandWindowProvider
from pcbridge.desktop.runtime import DesktopRuntime, create_runtime
from pcbridge.desktop.capabilities import CapabilityState
from pcbridge.desktop.safety import ActivityState, ScreenLockState
from tests.contracts.test_runtime_contract import make_config


def client(address="0x123", stable_id="1", pid=42, **changes):
    return {"address": address, "stableId": stable_id, "pid": pid, "mapped": True,
            "class": "foot", "title": "Identical title", **changes}


class HyprlandWindowTests(unittest.TestCase):
    def test_lists_mapped_windows_with_exact_identity_and_active_marker(self):
        raw = [client(), client("0x124", "2"), client("0x125", "3", mapped=False)]
        provider = HyprlandWindowProvider()
        with mock.patch.object(hyprland, "_query", side_effect=[raw, raw[1]]) as query:
            windows = provider.windows()
        self.assertEqual([w.ref for w in windows], ["hyprland:0x123", "hyprland:0x124"])
        self.assertEqual([w.active for w in windows], [False, True])
        self.assertIn("hyprland:0x124", provider.describe_windows(windows))
        self.assertEqual(query.call_args_list, [mock.call("clients", json_output=True),
                                               mock.call("activewindow", json_output=True)])

    def test_same_title_address_reuse_and_pid_changes_are_different_focus(self):
        provider = HyprlandWindowProvider()
        with mock.patch.object(hyprland, "_query", side_effect=[
            client(), client("0x124", "2"), client(stable_id="3"), client(pid=43),
        ]):
            identities = [provider.focused_identity() for _ in range(4)]
        self.assertEqual(len(set(identities)), 4)

    def test_runtime_hexadecimal_stable_ids_are_preserved(self):
        # Actual 0.56.2 IDs include letters once the window counter reaches 10.
        raw = [client(stable_id="1800001e"), client("0x124", "1800001f")]
        provider = HyprlandWindowProvider()
        with mock.patch.object(hyprland, "_query", side_effect=[raw, raw[0]]):
            self.assertEqual([w.stable_id for w in provider.windows()], ["1800001e", "1800001f"])

    def test_empty_focus_can_be_observed_but_cannot_pass_a_batch_focus_guard(self):
        provider = HyprlandWindowProvider()
        with mock.patch.object(hyprland, "_query", return_value={}):
            self.assertEqual(provider.focused_window(), ("", ""))
            device = ops.DeviceOps(None, mock.Mock(), SimpleNamespace(
                shot_search_dirs=[], desktop=SimpleNamespace(ambiguous_coord_guard=False)),
                None, provider)
            with self.assertRaises(ops.FocusUnreadable):
                device.focused()

    def test_malformed_duplicate_or_inconsistent_state_fails_closed(self):
        provider = HyprlandWindowProvider()
        for table, active in [
            ({}, {}), ([client(), client()], {}),
            ([client(), client("0x124")], {}),
            ([client(mapped=None)], {}), ([client(pid=True)], {}),
            ([client(address="0x123;exec bad")], {}),
            ([client(stableId="1\"bad")], {}),
            ([client(stableId="1" * 17)], {}),
            ([client()], client("0x999", "9")),
        ]:
            with self.subTest(table=table), mock.patch.object(
                    hyprland, "_query", side_effect=[table, active]):
                with self.assertRaises(DesktopError) as raised:
                    provider.windows()
                self.assertEqual(raised.exception.code, ErrorCode.BACKEND_UNAVAILABLE)

    def test_unavailable_ipc_is_reported_and_does_not_use_atspi(self):
        provider = HyprlandWindowProvider()
        with mock.patch.object(hyprland, "_query", side_effect=hyprland.HyprlandIPCError("offline")):
            self.assertEqual(provider.available(), (False, "offline"))

    def test_window_labels_escape_control_characters(self):
        provider = HyprlandWindowProvider()
        raw = client(title="title\n\x1b[2J")
        with mock.patch.object(hyprland, "_query", side_effect=[[raw], raw]):
            text = provider.describe_windows(provider.windows())
        self.assertNotIn("\x1b", text)
        self.assertIn("\\n", text)

    def test_search_never_guesses_gnome_keys_on_hyprland_or_unknown(self):
        backend = mock.Mock()
        for comp in (compositor.HYPRLAND, compositor.UNKNOWN):
            with self.subTest(kind=comp.kind), mock.patch.object(compositor, "current", return_value=comp):
                with self.assertRaises(DesktopError) as raised:
                    apps._search(mock.Mock(), backend, mock.Mock(), 0)
                self.assertEqual(raised.exception.code, ErrorCode.UNSUPPORTED)
        backend.key.assert_not_called()
        backend.type_text.assert_not_called()

    def test_factory_selects_hyprland_windows_independently_of_accessibility(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as raw, mock.patch.object(
                compositor, "current", return_value=compositor.HYPRLAND):
            accessibility = mock.Mock()
            runtime = create_runtime(make_config(Path(raw)), capture_provider=mock.Mock(),
                                     input_provider=mock.Mock(), accessibility_provider=accessibility)
            self.assertIs(runtime.accessibility_provider, accessibility)
            self.assertIsInstance(runtime.window_provider, HyprlandWindowProvider)
            runtime.close()

    def test_window_capability_remains_independent_of_missing_accessibility(self):
        providers = [mock.Mock(probe_capabilities=mock.Mock(return_value={})) for _ in range(3)]
        state = mock.Mock(
            user_activity=mock.Mock(return_value=SimpleNamespace(state=ActivityState.UNKNOWN, backend="idle")),
            screen_lock=mock.Mock(return_value=SimpleNamespace(state=ScreenLockState.UNKNOWN, backend="lock")),
        )
        runtime = DesktopRuntime(capture_provider=providers[0], input_provider=providers[1],
                                 accessibility_provider=providers[2], gate=mock.Mock(),
                                 window_provider=mock.Mock(available=mock.Mock(return_value=(True, ""))),
                                 desktop_state_provider=state, extension_focus_probe=lambda: False)
        with mock.patch.object(compositor, "current", return_value=compositor.HYPRLAND):
            caps = runtime._probe_capabilities()
        self.assertEqual(caps["window.list"].state, CapabilityState.SUPPORTED)
        self.assertEqual(caps["window.list"].backend, "linux.hyprland-ipc")
        self.assertEqual(caps["accessibility.read"].state, CapabilityState.UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
