#!/usr/bin/env python3
"""Typed desktop errors and side-effect-free capability snapshot contracts."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

REQUIRED_CAPABILITIES = {
    "capture.monitor",
    "capture.window",
    "input.pointer",
    "input.pointer_relative",
    "input.keyboard",
    "accessibility.read",
    "accessibility.action",
    "window.list",
    "window.focus",
    "window.move_resize",
    "clipboard.read",
    "clipboard.write",
    "user_activity",
    "screen_lock",
}

from pcbridge.config import AgentSpec, Config, DesktopSpec  # noqa: E402
from pcbridge.desktop import capture as capturelib  # noqa: E402
from pcbridge.desktop import input as inputlib  # noqa: E402
from pcbridge.desktop import monitors as monitorslib  # noqa: E402
from pcbridge.desktop import screencast as screencastlib  # noqa: E402
from pcbridge.desktop import uitree as uitreelib  # noqa: E402
from pcbridge.desktop.backends.python import (  # noqa: E402
    PythonAccessibilityProvider,
    PythonCaptureProvider,
    PythonInputProvider,
)
from pcbridge.desktop.capabilities import (  # noqa: E402
    AuthorizationStatus,
    Capability,
    CapabilityEvidence,
    CapabilitySnapshot,
    CapabilityState,
)
from pcbridge.desktop.errors import (  # noqa: E402
    DesktopError,
    ErrorCategory,
    ErrorCode,
)
from pcbridge.desktop.runtime import DesktopRuntime  # noqa: E402


def make_config(root: Path) -> Config:
    config = Config(
        public_url="https://example.invalid",
        host="127.0.0.1",
        port=8765,
        mcp_path="/mcp",
        password="contract-password",
        static_token="",
        access_token_ttl=60,
        refresh_token_ttl=120,
        auth_code_ttl=30,
        max_failed_attempts=3,
        lockout_seconds=60,
        manual_redirect=False,
        default_workdir=root,
        state_dir=root,
        max_output_chars=4000,
        default_job_timeout=60,
        max_sync_timeout=60,
        agents={"contract": AgentSpec(name="contract", command=["true"])},
        inline_images="true",
        default_agent="contract",
        desktop=DesktopSpec(enabled=True),
    )
    config.jobs_dir.mkdir(parents=True, exist_ok=True)
    return config


def capability(
    name: str,
    state: CapabilityState,
    *,
    backend: str,
    scope: str,
    reason_code: ErrorCode | None = None,
    limitations: tuple[str, ...] = (),
    evidence: CapabilityEvidence = CapabilityEvidence.PROBE,
    usable_now: bool | None = None,
) -> Capability:
    return Capability(
        name=name,
        state=state,
        backend=backend,
        scope=scope,
        reason_code=reason_code,
        limitations=limitations,
        observed_at=1.0,
        evidence=evidence,
        usable_now=(state in (CapabilityState.SUPPORTED, CapabilityState.DEGRADED)
                    if usable_now is None else usable_now),
    )


class FakeGate:
    spec = SimpleNamespace(enabled=True)

    def remaining_seconds(self) -> int:
        return 30

    def hard_remaining_seconds(self) -> int:
        return 120

    def is_unlocked(self) -> bool:
        return True

    def touch(self) -> None:
        return None


class FakeProvider:
    def __init__(self, values: dict[str, Capability]) -> None:
        self.values = values
        self.token = 1
        self.probe_count = 0

    def capability_token(self):
        return self.token

    def probe_capabilities(self) -> dict[str, Capability]:
        self.probe_count += 1
        return dict(self.values)

    def close(self) -> None:
        return None


class CapabilityContractTests(unittest.TestCase):
    def test_error_taxonomy_contains_the_version_one_contract(self) -> None:
        self.assertEqual(
            {code.value for code in ErrorCode},
            {
                "DESKTOP_DISABLED",
                "GRANT_REQUIRED",
                "GRANT_EXPIRED",
                "REVOKED",
                "SCREEN_LOCKED",
                "LOCK_STATE_UNKNOWN",
                "USER_ACTIVE",
                "ACTIVITY_UNKNOWN",
                "RATE_LIMITED",
                # Icerik kapilari (docs/dev/desktop-rules.md §4 items 7 and 5):
                # izinden bagimsiz, hedefin kendisine bakan retler.
                "PASSWORD_FIELD",
                "CONFIRMATION_REQUIRED",
                "PERMISSION_REQUIRED",
                "PERMISSION_DENIED",
                "DEVICE_NOT_GRANTED",
                "UNSUPPORTED",
                "BACKEND_UNAVAILABLE",
                "DEPENDENCY_MISSING",
                "FRAME_TIMEOUT",
                "STALE_FRAME",
                "FRAME_FORMAT_UNSUPPORTED",
                "FRAME_TOO_LARGE",
                "DISPLAY_CHANGED",
                "DISPLAY_MAPPING_UNKNOWN",
                # Task 3.5: a capture can succeed while its image fails to
                # reach the client; that is not a frame error.
                "IMAGE_DELIVERY_FAILED",
                "SHOT_NOT_FOUND",
                "SHOT_INVALID",
                "SHOT_STALE",
                "AMBIGUOUS_COORDINATE",
                "TARGET_MISMATCH",
                "ELEMENT_STALE",
                "ELEMENT_AMBIGUOUS",
                "ACTION_UNSUPPORTED",
                # Task 6.3: a field written with other text than was sent.
                "TEXT_MISMATCH",
                "TIMEOUT",
                "CANCELLED",
                "EXECUTION_UNKNOWN",
                "BUSY",
                "NATIVE_NOT_FOUND",
                "PROTOCOL_MISMATCH",
                "NATIVE_CRASHED",
                "INVALID_FRAME",
            },
        )

    def test_snapshot_represents_independent_capture_and_pointer_permission(self) -> None:
        snapshot = CapabilitySnapshot(
            capabilities={
                "capture.monitor": capability(
                    "capture.monitor",
                    CapabilityState.SUPPORTED,
                    backend="linux.gnome-screenshot",
                    scope="os.capture",
                ),
                "input.pointer": capability(
                    "input.pointer",
                    CapabilityState.PERMISSION_REQUIRED,
                    backend="linux.uinput",
                    scope="os.pointer",
                    reason_code=ErrorCode.DEVICE_NOT_GRANTED,
                    usable_now=False,
                ),
            },
            last_operations={},
            authorization=AuthorizationStatus(
                desktop_enabled=True,
                grant_remaining_seconds=30,
                hard_remaining_seconds=120,
                screen_lock_state="unlocked",
                revoke_epoch=0,
            ),
        )

        data = snapshot.as_dict()
        self.assertEqual(data["capabilities"]["capture.monitor"]["state"], "supported")
        self.assertEqual(
            data["capabilities"]["input.pointer"]["state"],
            "permission_required",
        )
        self.assertEqual(
            data["capabilities"]["input.pointer"]["reason_code"],
            "DEVICE_NOT_GRANTED",
        )

    def test_runtime_keeps_probe_and_operation_evidence_separate(self) -> None:
        capture = FakeProvider({
            "capture.monitor": capability(
                "capture.monitor",
                CapabilityState.SUPPORTED,
                backend="synthetic.capture",
                scope="os.capture",
            )
        })
        input_provider = FakeProvider({
            "input.pointer": capability(
                "input.pointer",
                CapabilityState.PERMISSION_REQUIRED,
                backend="synthetic.pointer",
                scope="os.pointer",
                reason_code=ErrorCode.PERMISSION_REQUIRED,
                usable_now=False,
            ),
            "input.keyboard": capability(
                "input.keyboard",
                CapabilityState.PERMISSION_REQUIRED,
                backend="synthetic.keyboard",
                scope="os.keyboard",
                reason_code=ErrorCode.PERMISSION_REQUIRED,
                usable_now=False,
            ),
        })
        accessibility = FakeProvider({
            "accessibility.read": capability(
                "accessibility.read",
                CapabilityState.SUPPORTED,
                backend="linux.atspi",
                scope="os.accessibility",
            ),
            "accessibility.action": capability(
                "accessibility.action",
                CapabilityState.SUPPORTED,
                backend="linux.atspi",
                scope="os.accessibility",
            ),
            "window.list": capability(
                "window.list",
                CapabilityState.DEGRADED,
                backend="linux.atspi",
                scope="os.accessibility",
                limitations=("Only accessibility-visible applications are listed.",),
            ),
        })
        runtime = DesktopRuntime(
            capture_provider=capture,
            input_provider=input_provider,
            accessibility_provider=accessibility,
            gate=FakeGate(),
            screen_lock_probe=lambda: False,
            user_activity_probe=lambda: 2000,
            extension_focus_probe=lambda: False,
        )

        first = runtime.capabilities()
        self.assertEqual(set(first.capabilities), REQUIRED_CAPABILITIES)
        self.assertEqual(
            first.capabilities["window.list"].state,
            CapabilityState.DEGRADED,
        )
        self.assertEqual(
            first.capabilities["window.move_resize"].state,
            CapabilityState.UNSUPPORTED,
        )
        self.assertEqual(
            first.capabilities["capture.monitor"].state,
            CapabilityState.SUPPORTED,
        )
        self.assertEqual(
            first.capabilities["input.pointer"].state,
            CapabilityState.PERMISSION_REQUIRED,
        )
        self.assertEqual(
            first.capabilities["window.focus"].state,
            CapabilityState.PERMISSION_REQUIRED,
        )

        runtime.record_capability_operation(
            capability(
                "capture.monitor",
                CapabilityState.UNAVAILABLE,
                backend="synthetic.capture",
                scope="os.capture",
                reason_code=ErrorCode.BACKEND_UNAVAILABLE,
                evidence=CapabilityEvidence.OPERATION,
                usable_now=False,
            )
        )
        second = runtime.capabilities()
        self.assertEqual(
            second.capabilities["capture.monitor"].evidence,
            CapabilityEvidence.PROBE,
        )
        self.assertEqual(
            second.last_operations["capture.monitor"].evidence,
            CapabilityEvidence.OPERATION,
        )

        capture.token += 1
        third = runtime.capabilities()
        self.assertEqual(third.last_operations, {})
        self.assertEqual(capture.probe_count, 2)
        runtime.close()

    def test_extension_reports_supported_focus_without_uinput(self) -> None:
        capture = FakeProvider({})
        input_provider = FakeProvider({
            "input.keyboard": capability(
                "input.keyboard",
                CapabilityState.PERMISSION_REQUIRED,
                backend="synthetic.keyboard",
                scope="os.keyboard",
                reason_code=ErrorCode.PERMISSION_REQUIRED,
                usable_now=False,
            ),
        })
        accessibility = FakeProvider({
            "accessibility.read": capability(
                "accessibility.read",
                CapabilityState.SUPPORTED,
                backend="linux.atspi",
                scope="os.accessibility",
            ),
        })
        runtime = DesktopRuntime(
            capture_provider=capture,
            input_provider=input_provider,
            accessibility_provider=accessibility,
            gate=FakeGate(),
            screen_lock_probe=lambda: False,
            user_activity_probe=lambda: 2000,
            extension_focus_probe=lambda: True,
        )

        focus = runtime.capabilities().capabilities["window.focus"]

        self.assertEqual(focus.state, CapabilityState.SUPPORTED)
        self.assertEqual(focus.backend, "linux.gnome-shell-extension")
        self.assertIn("closed applications", focus.limitations[0].lower())
        runtime.close()

    def test_input_probe_never_constructs_or_writes_to_devices(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            provider = PythonInputProvider(make_config(Path(raw)))
            with (
                mock.patch.object(
                    inputlib.InputBackend,
                    "_make_keyboard",
                    side_effect=AssertionError("keyboard device constructed"),
                ),
                mock.patch.object(
                    inputlib.InputBackend,
                    "_make_pointer",
                    side_effect=AssertionError("pointer device constructed"),
                ),
            ):
                values = provider.probe_capabilities()

        self.assertIn("input.keyboard", values)
        self.assertIn("input.pointer", values)

    def test_provider_maps_legacy_capture_errors_without_message_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cfg = make_config(Path(raw))
            provider = PythonCaptureProvider(cfg)
            arbitrary = "a localized message with no classification words"
            with mock.patch.object(
                capturelib, "capture", side_effect=capturelib.CaptureError(arbitrary)
            ):
                with self.assertRaises(DesktopError) as raised:
                    provider.capture(
                        "all",
                        out_dir=Path(raw),
                        scale_long_edge=80,
                        include_pointer=False,
                    )

        error = raised.exception
        self.assertEqual(error.code, ErrorCode.BACKEND_UNAVAILABLE)
        self.assertEqual(error.category, ErrorCategory.CAPABILITY)
        self.assertEqual(error.message, arbitrary)
        self.assertEqual(error.backend, "linux.python.capture")
        self.assertEqual(error.to_dict()["code"], "BACKEND_UNAVAILABLE")

    def test_capture_probe_keeps_window_capture_when_topology_is_unavailable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            provider = PythonCaptureProvider(make_config(Path(raw)))
            with (
                mock.patch(
                    "pcbridge.desktop.backends.python.shutil.which",
                    return_value="/usr/bin/probe-command",
                ),
                mock.patch.object(
                    screencastlib,
                    "available",
                    return_value=(True, ""),
                ),
                mock.patch.object(
                    monitorslib,
                    "list_monitors",
                    side_effect=monitorslib.MonitorError("synthetic topology error"),
                ),
            ):
                values = provider.probe_capabilities()

        self.assertEqual(
            values["capture.monitor"].reason_code,
            ErrorCode.DISPLAY_MAPPING_UNKNOWN,
        )
        self.assertEqual(
            values["capture.window"].state,
            CapabilityState.SUPPORTED,
        )

    def test_provider_maps_legacy_input_errors_without_message_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            provider = PythonInputProvider(make_config(Path(raw)))
            arbitrary = "a localized input message with no classification words"
            with (
                mock.patch.object(
                    provider,
                    "_availability",
                    return_value=(True, "", None),
                ),
                mock.patch.object(
                    inputlib.InputBackend,
                    "key",
                    side_effect=inputlib.InputError(arbitrary),
                ),
            ):
                with self.assertRaises(DesktopError) as raised:
                    provider.key("ctrl+a")

        error = raised.exception
        self.assertEqual(error.code, ErrorCode.EXECUTION_UNKNOWN)
        self.assertEqual(error.category, ErrorCategory.EXECUTION)
        self.assertEqual(error.message, arbitrary)

    def test_provider_maps_legacy_accessibility_errors_without_message_parsing(
        self,
    ) -> None:
        provider = PythonAccessibilityProvider()
        arbitrary = "a localized accessibility message with no classification words"
        with mock.patch.object(
            uitreelib.UiTree,
            "dump",
            side_effect=uitreelib.UiTreeError(arbitrary),
        ):
            with self.assertRaises(DesktopError) as raised:
                provider.dump()

        error = raised.exception
        self.assertEqual(error.code, ErrorCode.BACKEND_UNAVAILABLE)
        self.assertEqual(error.category, ErrorCategory.CAPABILITY)
        self.assertEqual(error.message, arbitrary)


if __name__ == "__main__":
    unittest.main()
