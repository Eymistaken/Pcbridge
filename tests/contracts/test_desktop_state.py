#!/usr/bin/env python3
"""Contracts for typed desktop-state observations and safety policy."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import tools as toolslib  # noqa: E402
from pcbridge.config import AgentSpec, Config, DesktopSpec  # noqa: E402
from pcbridge.desktop import safety as safetylib  # noqa: E402
from pcbridge.desktop.backends.python import PythonDesktopStateProvider  # noqa: E402
from pcbridge.desktop.capabilities import (  # noqa: E402
    Capability,
    CapabilityEvidence,
    CapabilityState,
)
from pcbridge.desktop.errors import ErrorCode  # noqa: E402
from pcbridge.desktop.runtime import DesktopRuntime  # noqa: E402
from pcbridge.desktop.safety import (  # noqa: E402
    ActivityObservation,
    ActivityState,
    SafetyGate,
    ScreenLockObservation,
    ScreenLockState,
)
from pcbridge.jobs import JobManager  # noqa: E402
from pcbridge.shots import ShotStore  # noqa: E402


class FakeDesktopStateProvider:
    def __init__(
        self,
        lock: ScreenLockState,
        activity: ActivityState,
        *,
        idle_ms: int | None = None,
    ) -> None:
        self.lock = lock
        self.activity = activity
        self.idle_ms = idle_ms
        self.activity_calls = 0

    def screen_lock(self) -> ScreenLockObservation:
        return ScreenLockObservation(self.lock, observed_at=1.0)

    def user_activity(self) -> ActivityObservation:
        self.activity_calls += 1
        return ActivityObservation(
            self.activity,
            idle_ms=self.idle_ms,
            observed_at=1.0,
        )


def make_gate(root: Path, state: FakeDesktopStateProvider) -> SafetyGate:
    cfg = SimpleNamespace(
        desktop=DesktopSpec(
            enabled=True,
            idle_guard_seconds=60,
            max_actions_per_second=0,
        ),
        state_dir=root,
        audit_log=root / "audit.log",
        audit_max_bytes=0,
    )
    return SafetyGate(cfg, state_provider=state)


def capability(
    name: str,
    state: CapabilityState,
    *,
    scope: str,
    reason_code: ErrorCode | None = None,
) -> Capability:
    return Capability(
        name=name,
        state=state,
        backend="fake",
        scope=scope,
        reason_code=reason_code,
        limitations=(),
        observed_at=1.0,
        evidence=CapabilityEvidence.PROBE,
        usable_now=state in {CapabilityState.SUPPORTED, CapabilityState.DEGRADED},
    )


class CaptureOnlyProvider:
    def __init__(self) -> None:
        self.open = False
        self.start_count = 0

    def capability_token(self):
        return "capture-only"

    def probe_capabilities(self) -> dict[str, Capability]:
        return {
            name: capability(name, CapabilityState.SUPPORTED, scope="os.capture")
            for name in ("capture.monitor", "capture.window")
        }

    def start(self, *, cursor: bool | None = None) -> dict:
        self.open = True
        self.start_count += 1
        return {"backend": "fake.capture", "cursor": cursor}

    def is_open(self) -> bool:
        return self.open

    def close(self) -> None:
        self.open = False

    def describe_monitors(self) -> str:
        return "1 monitor"

    def kill_helpers(self) -> int:
        return 0


class UnavailableInputProvider:
    def capability_token(self):
        return "input-unavailable"

    def probe_capabilities(self) -> dict[str, Capability]:
        return {
            "input.pointer": capability(
                "input.pointer",
                CapabilityState.PERMISSION_REQUIRED,
                scope="os.pointer",
                reason_code=ErrorCode.DEVICE_NOT_GRANTED,
            ),
            "input.keyboard": capability(
                "input.keyboard",
                CapabilityState.PERMISSION_REQUIRED,
                scope="os.keyboard",
                reason_code=ErrorCode.DEVICE_NOT_GRANTED,
            ),
        }

    def available(self) -> tuple[bool, str]:
        raise AssertionError("desktop_unlock must not probe uinput availability")

    def close(self) -> None:
        return None

    def held(self) -> list[str]:
        return []

    def release_all(self) -> list[str]:
        return []


class AvailableInputProvider(UnavailableInputProvider):
    def probe_capabilities(self) -> dict[str, Capability]:
        return {
            name: capability(name, CapabilityState.SUPPORTED, scope=scope)
            for name, scope in (
                ("input.pointer", "os.pointer"),
                ("input.keyboard", "os.keyboard"),
            )
        }

    def available(self) -> tuple[bool, str]:
        return True, ""


class AccessibilityProvider:
    def capability_token(self):
        return "accessibility"

    def probe_capabilities(self) -> dict[str, Capability]:
        return {
            name: capability(name, CapabilityState.SUPPORTED, scope="os.accessibility")
            for name in ("accessibility.read", "accessibility.action", "window.list")
        }

    def close(self) -> None:
        return None

    def click(self, node_id: str, action: str = "click") -> dict:
        return {"role": "button", "name": node_id}


class RecordingJobManager:
    def __init__(self) -> None:
        self.starts = 0

    def start(self, **_kwargs) -> str:
        self.starts += 1
        return "job-desktop-state"


def make_config(root: Path) -> Config:
    cfg = Config(
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
        inline_images="false",
        default_agent="contract",
        desktop=DesktopSpec(
            enabled=True,
            unlock_notification=False,
            max_actions_per_second=0,
        ),
    )
    cfg.jobs_dir.mkdir(parents=True, exist_ok=True)
    return cfg


class DesktopStateContractTests(unittest.TestCase):
    def test_raw_activity_parser_rejects_boolean_values(self) -> None:
        with mock.patch.object(safetylib, "_busctl_json", return_value=True):
            self.assertIsNone(safetylib.idle_ms())

    def test_python_provider_preserves_known_and_unknown_observations(self) -> None:
        provider = PythonDesktopStateProvider()
        cases = (
            (
                True,
                1_000,
                ScreenLockState.KNOWN_LOCKED,
                ActivityState.KNOWN,
                1_000,
            ),
            (
                False,
                90_000,
                ScreenLockState.KNOWN_UNLOCKED,
                ActivityState.KNOWN,
                90_000,
            ),
            (None, None, ScreenLockState.UNKNOWN, ActivityState.UNKNOWN, None),
            (False, True, ScreenLockState.KNOWN_UNLOCKED, ActivityState.UNKNOWN, None),
        )

        for (
            raw_lock,
            raw_idle,
            expected_lock,
            expected_activity,
            expected_idle,
        ) in cases:
            with self.subTest(raw_lock=raw_lock, raw_idle=raw_idle):
                with (
                    mock.patch.object(
                        safetylib, "screen_locked", return_value=raw_lock
                    ),
                    mock.patch.object(safetylib, "idle_ms", return_value=raw_idle),
                ):
                    lock = provider.screen_lock()
                    activity = provider.user_activity()

                self.assertEqual(lock.state, expected_lock)
                self.assertEqual(activity.state, expected_activity)
                self.assertEqual(activity.idle_ms, expected_idle)

    def test_unknown_lock_rejects_reads_and_force_writes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state = FakeDesktopStateProvider(
                ScreenLockState.UNKNOWN,
                ActivityState.KNOWN,
                idle_ms=90_000,
            )
            gate = make_gate(Path(raw), state)
            gate.unlock(5)

            read = gate.check("screen_capture", write=False)
            forced_write = gate.check("mouse", write=True, force=True)

        self.assertEqual(read.code, ErrorCode.LOCK_STATE_UNKNOWN)
        self.assertEqual(forced_write.code, ErrorCode.LOCK_STATE_UNKNOWN)
        self.assertEqual(state.activity_calls, 0)

    def test_unknown_activity_rejects_write_but_force_only_bypasses_activity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state = FakeDesktopStateProvider(
                ScreenLockState.KNOWN_UNLOCKED,
                ActivityState.UNKNOWN,
            )
            gate = make_gate(Path(raw), state)
            gate.unlock(5)

            denied = gate.check("mouse", write=True)
            read = gate.check("screen_capture", write=False)
            forced = gate.check("mouse", write=True, force=True)
            gate.lock()
            no_grant = gate.check("mouse", write=True, force=True)

        self.assertEqual(denied.code, ErrorCode.ACTIVITY_UNKNOWN)
        self.assertTrue(read.allowed)
        self.assertTrue(forced.allowed)
        self.assertEqual(no_grant.code, ErrorCode.GRANT_REQUIRED)
        self.assertEqual(state.activity_calls, 1)

    def test_capture_only_machine_can_unlock_and_reports_input_limitations(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cfg = make_config(root)
            state = FakeDesktopStateProvider(
                ScreenLockState.KNOWN_UNLOCKED,
                ActivityState.KNOWN,
                idle_ms=90_000,
            )
            gate = SafetyGate(cfg, state_provider=state)
            capture = CaptureOnlyProvider()
            runtime = DesktopRuntime(
                capture_provider=capture,
                input_provider=UnavailableInputProvider(),
                accessibility_provider=AccessibilityProvider(),
                desktop_state_provider=state,
                gate=gate,
            )
            mcp = FastMCP("desktop-state-contract")
            toolslib.register(
                mcp,
                cfg,
                JobManager(cfg.jobs_dir),
                ShotStore(cfg),
                transport="stdio",
                runtime=runtime,
            )
            tool = asyncio.run(mcp.get_tool("desktop_unlock"))

            result = tool.fn(minutes=5, reason="capture-only contract")
            runtime.close()

        self.assertIsInstance(result, ToolResult)
        self.assertFalse(result.is_error)
        self.assertEqual(capture.start_count, 1)
        self.assertEqual(result.structured_content["type"], "pcbridge.desktop.grant")
        self.assertTrue(result.structured_content["grant"]["grant_id"])
        self.assertEqual(
            result.structured_content["authorization"]["screen_lock_state"],
            "known_unlocked",
        )
        self.assertEqual(
            result.structured_content["capability_limitations"]["input.pointer"][
                "reason_code"
            ],
            "DEVICE_NOT_GRANTED",
        )

    def test_batch_reads_activity_once_before_actions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cfg = make_config(root)
            state = FakeDesktopStateProvider(
                ScreenLockState.KNOWN_UNLOCKED,
                ActivityState.KNOWN,
                idle_ms=90_000,
            )
            gate = SafetyGate(cfg, state_provider=state)
            gate.unlock(5)
            runtime = DesktopRuntime(
                capture_provider=CaptureOnlyProvider(),
                input_provider=UnavailableInputProvider(),
                accessibility_provider=AccessibilityProvider(),
                desktop_state_provider=state,
                gate=gate,
            )
            mcp = FastMCP("batch-activity-contract")
            toolslib.register(
                mcp,
                cfg,
                JobManager(cfg.jobs_dir),
                ShotStore(cfg),
                transport="stdio",
                runtime=runtime,
            )
            tool = asyncio.run(mcp.get_tool("computer_batch"))

            result = tool.fn(
                actions='[{"a":"ui_click","id":"save"}]',
                final="none",
                expect_focus="",
                force=False,
            )
            runtime.close()

        self.assertIsInstance(result, list)
        self.assertEqual(state.activity_calls, 1)

    def test_task_reads_activity_once_before_start(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cfg = make_config(root)
            cfg.desktop.unlock_idle_seconds = 0
            state = FakeDesktopStateProvider(
                ScreenLockState.KNOWN_UNLOCKED,
                ActivityState.KNOWN,
                idle_ms=90_000,
            )
            gate = SafetyGate(cfg, state_provider=state)
            gate.unlock(5)
            runtime = DesktopRuntime(
                capture_provider=CaptureOnlyProvider(),
                input_provider=AvailableInputProvider(),
                accessibility_provider=AccessibilityProvider(),
                desktop_state_provider=state,
                gate=gate,
            )
            jobs = RecordingJobManager()
            mcp = FastMCP("task-activity-contract")
            toolslib.register(
                mcp,
                cfg,
                jobs,
                ShotStore(cfg),
                transport="stdio",
                runtime=runtime,
            )
            tool = asyncio.run(mcp.get_tool("computer_task"))

            result = tool.fn(
                goal="Verify activity is checked once",
                agent="contract",
                wait_seconds=0,
                force=False,
            )
            runtime.close()

        self.assertIsInstance(result, str)
        self.assertEqual(jobs.starts, 1)
        self.assertEqual(state.activity_calls, 1)

    def test_unlock_rejects_locked_or_unknown_before_grant_or_capture(self) -> None:
        cases = (
            (ScreenLockState.KNOWN_LOCKED, "SCREEN_LOCKED"),
            (ScreenLockState.UNKNOWN, "LOCK_STATE_UNKNOWN"),
        )
        for lock_state, expected_code in cases:
            with self.subTest(lock_state=lock_state):
                with tempfile.TemporaryDirectory() as raw:
                    root = Path(raw)
                    cfg = make_config(root)
                    state = FakeDesktopStateProvider(
                        lock_state,
                        ActivityState.KNOWN,
                        idle_ms=90_000,
                    )
                    gate = SafetyGate(cfg, state_provider=state)
                    capture = CaptureOnlyProvider()
                    runtime = DesktopRuntime(
                        capture_provider=capture,
                        input_provider=UnavailableInputProvider(),
                        accessibility_provider=AccessibilityProvider(),
                        desktop_state_provider=state,
                        gate=gate,
                    )
                    mcp = FastMCP("unlock-lock-state-contract")
                    toolslib.register(
                        mcp,
                        cfg,
                        JobManager(cfg.jobs_dir),
                        ShotStore(cfg),
                        transport="stdio",
                        runtime=runtime,
                    )
                    tool = asyncio.run(mcp.get_tool("desktop_unlock"))

                    result = tool.fn(minutes=5, reason="must stay closed")
                    grant_created = gate.current_token()
                    runtime.close()

                self.assertIsInstance(result, ToolResult)
                self.assertTrue(result.is_error)
                self.assertEqual(
                    result.structured_content["error"]["code"],
                    expected_code,
                )
                self.assertEqual(capture.start_count, 0)
                self.assertIsNone(grant_created)


if __name__ == "__main__":
    unittest.main()
