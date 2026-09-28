"""Exact compositor activation retains admission and never guesses keys."""

import unittest
from contextvars import copy_context
from types import SimpleNamespace
from unittest import mock

from pcbridge.desktop import apps, compositor, hyprland
from pcbridge.desktop.errors import DesktopError, ErrorCode
from pcbridge.desktop.hyprland_windows import HyprlandWindowProvider, Window
from pcbridge.desktop.runtime import DesktopRuntime
from pcbridge.desktop.safety import Decision


A = Window("0x123", "1800001e", "foot", "Same title", 42)
B = Window("0x124", "1800001f", "foot", "Same title", 43)


class HyprlandFocusTests(unittest.TestCase):
    def test_exact_reference_uses_the_selected_identity_and_checks_fresh_focus(self):
        provider = HyprlandWindowProvider()
        checkpoint = mock.Mock()
        with mock.patch.object(provider, "windows", return_value=[A, B]), \
                mock.patch.object(provider, "_focused", side_effect=[B, A]), \
                mock.patch.object(hyprland, "focus_exact") as dispatch:
            result = provider.activate(A.ref, checkpoint=checkpoint)
        self.assertEqual(result.identity, A.identity)
        self.assertEqual(dispatch.call_args.args, (A.address, A.stable_id))
        dispatch.call_args.kwargs["checkpoint"]()
        self.assertGreaterEqual(checkpoint.call_count, 2)

    def test_ambiguous_name_never_activates_even_if_one_match_is_focused(self):
        provider = HyprlandWindowProvider()
        with mock.patch.object(provider, "windows", return_value=[A, B]), \
                mock.patch.object(hyprland, "focus_exact") as dispatch:
            with self.assertRaises(DesktopError) as error:
                provider.activate("Same title", checkpoint=mock.Mock())
        self.assertEqual(error.exception.code, ErrorCode.ELEMENT_AMBIGUOUS)
        dispatch.assert_not_called()

    def test_localized_application_name_resolves_an_open_window_without_duplicate_launch(self):
        target = apps.resolve_application("Localized Terminal", [apps.Entry("foot", "Localized Terminal",
            False, ("Localized Terminal",), (), "foot")])
        provider = HyprlandWindowProvider()
        with mock.patch.object(provider, "windows", return_value=[A]), \
                mock.patch.object(provider, "_focused", return_value=A), \
                mock.patch.object(hyprland, "focus_exact"):
            result = provider.activate(target.text, checkpoint=mock.Mock(), application=target)
        self.assertEqual(result.identity, A.identity)

    def test_reused_address_after_resolution_does_not_activate(self):
        provider = HyprlandWindowProvider()
        replacement = Window(A.address, "18000020", A.app, A.title, A.app_pid)
        with mock.patch.object(provider, "windows", side_effect=[[A], [replacement]]), \
                mock.patch.object(hyprland, "focus_exact") as dispatch:
            with self.assertRaises(DesktopError) as error:
                provider.activate(A.ref, checkpoint=mock.Mock())
        self.assertEqual(error.exception.code, ErrorCode.ELEMENT_STALE)
        dispatch.assert_not_called()

    def test_dispatch_acknowledgment_is_not_focus_success(self):
        provider = HyprlandWindowProvider()
        with mock.patch.object(provider, "windows", return_value=[A, B]), \
                mock.patch.object(provider, "_focused", return_value=B), \
                mock.patch.object(hyprland, "focus_exact"), \
                mock.patch("pcbridge.desktop.hyprland_windows.time.monotonic", side_effect=[0, 2]):
            with self.assertRaises(DesktopError) as error:
                provider.activate(A.ref, checkpoint=mock.Mock())
        self.assertEqual(error.exception.code, ErrorCode.EXECUTION_UNKNOWN)

    def test_bring_to_front_does_not_require_input_or_accessibility(self):
        keys, focus, listed, checkpoint = [mock.Mock() for _ in range(4)]
        with mock.patch.object(compositor, "current", return_value=compositor.HYPRLAND), \
                mock.patch.object(HyprlandWindowProvider, "activate", return_value=A), \
                mock.patch.object(apps, "entries", return_value=[]):
            result = apps.bring_to_front(A.ref, keys, focus, listed, checkpoint=checkpoint)
        self.assertEqual(result.app, A.app)
        self.assertEqual(result.window, A.title)
        keys.key.assert_not_called()
        keys.type_text.assert_not_called()
        focus.assert_not_called()

    def test_dispatch_is_fixed_and_rechecks_after_read_only_context(self):
        checkpoint = mock.Mock()
        for mode, command in [("lua", 'hl.dsp.focus({window="stableid:1800001e"})'),
                              ("hyprlang", "focuswindow")]:
            with self.subTest(mode=mode), mock.patch.object(hyprland, "focus_context", return_value=("instance", mode)), \
                    mock.patch.object(hyprland.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="ok")) as run:
                hyprland.focus_exact(A.address, A.stable_id, checkpoint=checkpoint)
                self.assertEqual(run.call_args.args[0][:4], ["hyprctl", "-i", "instance", "dispatch"])
                self.assertEqual(run.call_args.args[0][4], command)
                if mode == "hyprlang":
                    self.assertEqual(run.call_args.args[0][5], "address:0x123")
                self.assertFalse(run.call_args.kwargs.get("shell", False))
                self.assertEqual(run.call_args.kwargs["timeout"], 3)
        self.assertEqual(checkpoint.call_count, 2)

    def test_revoke_before_dispatch_sends_nothing(self):
        refusal = DesktopError(code=ErrorCode.REVOKED, category="safety", message="Revoked",
            retryable=False, suggested_action="Open a new grant.")
        with mock.patch.object(hyprland, "focus_context", return_value=("instance", "lua")), \
                mock.patch.object(hyprland.subprocess, "run") as run:
            with self.assertRaises(DesktopError):
                hyprland.focus_exact(A.address, A.stable_id, checkpoint=mock.Mock(side_effect=refusal))
        run.assert_not_called()

    def test_unknown_runtime_provider_and_untrusted_selectors_fail_before_dispatch(self):
        with mock.patch.object(hyprland.session, "hyprland_instance", return_value={"instance": "selected", "wl_socket": "wayland-4"}), \
                mock.patch.object(hyprland, "_query", return_value={"configProvider": "unknown"}):
            with self.assertRaises(hyprland.HyprlandIPCError):
                hyprland.focus_context()
        for address, stable in (("0x123;bad", "1"), (A.address, '1") bad'), ("0x0", "1")):
            with self.subTest(address=address), mock.patch.object(hyprland, "focus_context") as context:
                with self.assertRaises(hyprland.HyprlandIPCError):
                    hyprland.focus_exact(address, stable, checkpoint=mock.Mock())
                context.assert_not_called()

    def test_expired_budget_after_context_lookup_sends_nothing(self):
        provider = HyprlandWindowProvider()
        with mock.patch.object(provider, "windows", return_value=[A]), \
                mock.patch.object(hyprland, "focus_context", return_value=("selected", "lua")), \
                mock.patch("pcbridge.desktop.hyprland_windows.time.monotonic", return_value=100), \
                mock.patch.object(hyprland.subprocess, "run") as run:
            with self.assertRaises(apps.NoTimeLeft):
                provider.activate(A.ref, checkpoint=mock.Mock(), deadline=99)
        run.assert_not_called()

    def test_missing_sequence_and_replacement_cannot_pass_the_runtime_checkpoint(self):
        with mock.patch.object(DesktopRuntime, "refresh_capture_deadline"):
            gate = mock.Mock(last_token=mock.Mock(return_value="original"),
                             verify=mock.Mock(return_value=Decision(True)))
            gate.requires_frame = False
            runtime = DesktopRuntime(capture_provider=mock.Mock(), input_provider=mock.Mock(),
                accessibility_provider=mock.Mock(), gate=gate)
            with self.assertRaises(DesktopError):
                runtime.compositor_checkpoint()
            with runtime.write_sequence("focus"):
                copied = copy_context()
                gate.last_token.return_value = "replacement"
                runtime.compositor_checkpoint()
                self.assertEqual(gate.verify.call_args.args, ("original",))
            with self.assertRaises(DesktopError):
                runtime.compositor_checkpoint()
            with self.assertRaises(DesktopError):
                copied.run(runtime.compositor_checkpoint)
            runtime.close()


if __name__ == "__main__":
    unittest.main()
