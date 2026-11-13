#!/usr/bin/env python3
"""Native input selection and keyboard IPC adapter contracts (Tasks 5.2-5.3).

No test opens `/dev/uinput` or starts the helper. The native client and grant
are deterministic fakes. The helper is the only input backend: the
python-evdev devices were removed after 2.8.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.config import NativeSpec, load_config  # noqa: E402
from pcbridge.desktop.backends.rust import RustInputProvider  # noqa: E402
from pcbridge.desktop.capabilities import CapabilityState  # noqa: E402
from pcbridge.desktop.lease import LeaseToken  # noqa: E402
from pcbridge.desktop.runtime import select_input_provider  # noqa: E402
from pcbridge.native import NativeResponse  # noqa: E402


class FakeGate:
    def current_token(self):
        return LeaseToken(grant_id="keyboard-fixture", revoke_epoch=7)

    def last_token(self):
        return self.current_token()


class FakeNativeClient:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []
        self.closed = False
        self.is_running = True
        self.auto_released = ["shift"]

    def request(self, method, params=None, *, binary=b"", timeout=None):
        self.requests.append((method, dict(params or {})))
        results = {
            "input.keyboard.ensure": {"waited_seconds": 0.0},
            "input.keyboard.key": {"held": []},
            "input.keyboard.key_down": {"held": ["shift"]},
            "input.keyboard.key_up": {"held": []},
            "input.keyboard.held": {"held": ["shift"]},
            "input.keyboard.release_all": {"released": ["shift"]},
            "input.keyboard.take_auto_released": {
                "released": list(self.auto_released)
            },
            "input.pointer.move": {"position": [12, 34]},
            "input.pointer.release_all": {"released": []},
        }
        if method == "input.keyboard.take_auto_released":
            self.auto_released.clear()
        return NativeResponse(
            request_id="fixture",
            result=results[method],
            binary=b"",
            error=None,
        )

    def close(self) -> None:
        self.closed = True
        self.is_running = False


class NativeInputSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = load_config(str(ROOT / "config.example.toml"))

    def test_the_shipped_and_implicit_default_is_auto_since_gate_5(self) -> None:
        self.assertEqual(self.cfg.native.input, "auto")
        self.assertEqual(NativeSpec().input, "auto")

    def test_every_setting_builds_the_native_provider(self) -> None:
        # With or without a helper: a missing one is reported by the
        # provider, never answered by another backend.
        for choice in ("auto", "rust"):
            for ready in ((True, ""), (False, "pcbridge-native not found")):
                with self.subTest(choice=choice, ready=ready[0]):
                    object.__setattr__(self.cfg.native, "input", choice)
                    with mock.patch(
                        "pcbridge.desktop.backends.rust.native_binary_ready",
                        return_value=ready,
                    ):
                        provider = select_input_provider(self.cfg, FakeGate())
                    self.assertIs(type(provider), RustInputProvider)

    def test_without_the_helper_input_says_why(self) -> None:
        provider = select_input_provider(self.cfg, FakeGate())
        with mock.patch(
            "pcbridge.desktop.backends.rust.native_binary_ready",
            return_value=(False, "pcbridge-native not found"),
        ):
            capabilities = provider.probe_capabilities()
            available = provider.available()
        for name in ("input.keyboard", "input.pointer", "clipboard.write"):
            with self.subTest(capability=name):
                self.assertIs(capabilities[name].state, CapabilityState.UNAVAILABLE)
                self.assertIn("pcbridge-native not found", capabilities[name].limitations)
        self.assertEqual(available, (False, "pcbridge-native not found"))

    def test_an_unknown_input_setting_is_refused_at_load(self) -> None:
        text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
        self.assertIn('input = "auto"', text)
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "config.toml"
            path.write_text(text.replace('input = "auto"', 'input = "native"'), encoding="utf-8")
            with self.assertRaises(SystemExit) as raised:
                load_config(str(path))
        self.assertIn("must be auto or rust", str(raised.exception))

    def test_the_old_python_setting_is_read_as_auto_with_a_warning(self) -> None:
        text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "config.toml"
            path.write_text(text.replace('input = "auto"', 'input = "python"'), encoding="utf-8")
            cfg = load_config(str(path))
        self.assertEqual(cfg.native.input, "auto")
        self.assertTrue(any('input = "python" is no longer supported' in w for w in cfg.warnings))
        self.assertIs(type(select_input_provider(cfg, FakeGate())), RustInputProvider)


class RustKeyboardAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = load_config(str(ROOT / "config.example.toml"))
        self.client = FakeNativeClient()
        self.provider = RustInputProvider(
            self.cfg,
            gate=FakeGate(),
            client=self.client,
        )

    def tearDown(self) -> None:
        self.provider.close()

    def test_keyboard_calls_are_single_non_replayed_grant_bound_requests(self) -> None:
        self.provider.key("ctrl+shift+t")
        self.assertEqual(len(self.client.requests), 1)
        method, params = self.client.requests[0]
        self.assertEqual(method, "input.keyboard.key")
        self.assertEqual(
            params,
            {
                "combo": "ctrl+shift+t",
                "grant_id": "keyboard-fixture",
                "revoke_epoch": 7,
                "hold_max_seconds": 120,
            },
        )

    def test_held_keys_and_auto_release_are_the_helpers(self) -> None:
        self.provider.key_down("shift")
        self.assertEqual(self.provider.held(), ["shift"])
        self.assertEqual(self.provider.take_auto_released(), ["shift"])
        self.assertEqual(self.provider.take_auto_released(), [])
        methods = [method for method, _params in self.client.requests]
        self.assertEqual(
            methods,
            [
                "input.keyboard.key_down",
                "input.keyboard.held",
                "input.keyboard.take_auto_released",
                "input.keyboard.take_auto_released",
            ],
        )

    def test_pointer_methods_move_to_the_native_side_in_task_5_3(self) -> None:
        with (
            mock.patch(
                "pcbridge.desktop.backends.rust.monitorslib.list_monitors",
                return_value=[object()],
            ),
            mock.patch(
                "pcbridge.desktop.backends.rust.monitorslib.topology_id",
                return_value="layout",
            ),
        ):
            self.assertEqual(self.provider.move(12, 34), (12, 34))
        self.assertEqual(self.client.requests[0][0], "input.pointer.move")

    def test_missing_native_helper_marks_both_native_input_capabilities_unavailable(
        self,
    ) -> None:
        with mock.patch(
            "pcbridge.desktop.backends.rust.native_binary_ready",
            return_value=(False, "native helper missing"),
        ):
            capabilities = self.provider.probe_capabilities()
            available = self.provider.available()

        self.assertEqual(
            capabilities["input.pointer"].state,
            CapabilityState.UNAVAILABLE,
        )
        self.assertEqual(
            capabilities["input.keyboard"].state,
            CapabilityState.UNAVAILABLE,
        )
        self.assertEqual(available, (False, "native helper missing"))

    def test_a_writable_uinput_and_a_helper_are_all_input_needs(self) -> None:
        with (
            mock.patch(
                "pcbridge.desktop.backends.rust.native_binary_ready",
                return_value=(True, ""),
            ),
            mock.patch("pcbridge.desktop.backends.rust.os.path.exists", return_value=True),
            mock.patch("pcbridge.desktop.backends.rust.os.access", return_value=True),
        ):
            capabilities = self.provider.probe_capabilities()

        self.assertEqual(
            capabilities["input.pointer"].state,
            CapabilityState.SUPPORTED,
        )
        self.assertEqual(
            capabilities["input.keyboard"].state,
            CapabilityState.SUPPORTED,
        )

    def test_close_releases_before_stopping_the_native_client(self) -> None:
        self.provider.key_down("shift")
        self.provider.close()
        methods = [method for method, _params in self.client.requests]
        self.assertEqual(methods[-1], "input.keyboard.release_all")
        self.assertTrue(self.client.closed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
