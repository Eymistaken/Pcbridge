#!/usr/bin/env python3
"""Lifecycle and isolation contracts for the shared desktop runtime factory."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastmcp import FastMCP


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import app as serverlib  # noqa: E402
from pcbridge import tools as toolslib  # noqa: E402
from pcbridge.cli import do as do_cli  # noqa: E402
from pcbridge.config import AgentSpec, Config, DesktopSpec  # noqa: E402
from pcbridge.desktop import input as inputlib  # noqa: E402
from pcbridge.desktop import screencast as screencastlib  # noqa: E402
from pcbridge.desktop import uitree as uitreelib  # noqa: E402
from pcbridge.desktop.runtime import DesktopRuntime, create_runtime  # noqa: E402
from pcbridge.jobs import JobManager  # noqa: E402


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


class FakeGate:
    def __init__(self) -> None:
        self.unlocked = True
        self.touches = 0

    def remaining_seconds(self) -> float:
        return 30.0

    def is_unlocked(self) -> bool:
        return self.unlocked

    def touch(self, token=None) -> None:
        self.touches += 1
        self.last_touch_token = token


class FakeTimer:
    created: list["FakeTimer"] = []

    def __init__(self, seconds, callback) -> None:
        self.seconds = seconds
        self.callback = callback
        self.daemon = False
        self.started = False
        self.cancelled = False
        self.created.append(self)

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


class FakeCaptureProvider:
    def __init__(self, response: str = "capture") -> None:
        self.response = response
        self.open = False
        self.start_count = 0
        self.close_count = 0

    def start(self, *, cursor: bool | None = None):
        self.open = True
        self.start_count += 1
        return {"response": self.response, "cursor": cursor}

    def is_open(self) -> bool:
        return self.open

    def close(self) -> None:
        self.open = False
        self.close_count += 1


class FakeInputProvider:
    def __init__(self) -> None:
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


class FakeAccessibilityProvider:
    pass


class DesktopRuntimeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeTimer.created.clear()

    def test_factory_is_lazy_and_creates_isolated_provider_instances(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cfg = make_config(Path(raw))
            with (
                mock.patch.object(
                    screencastlib.ScreenCast,
                    "_spawn",
                    side_effect=AssertionError("capture started during construction"),
                ),
                mock.patch.object(
                    inputlib.InputBackend,
                    "ensure",
                    side_effect=AssertionError("input started during construction"),
                ),
                mock.patch.object(
                    uitreelib,
                    "_call",
                    side_effect=AssertionError("accessibility started during construction"),
                ),
            ):
                first = create_runtime(cfg, gate=FakeGate())
                second = create_runtime(cfg, gate=FakeGate())

            self.assertIsNot(first.capture_provider, second.capture_provider)
            self.assertIsNot(first.input_provider, second.input_provider)
            self.assertIsNot(first.accessibility_provider, second.accessibility_provider)
            first.close()
            second.close()

    def test_capture_timers_and_responses_stay_with_their_runtime(self) -> None:
        first_capture = FakeCaptureProvider("first")
        second_capture = FakeCaptureProvider("second")
        first_input = FakeInputProvider()
        second_input = FakeInputProvider()

        with mock.patch("pcbridge.desktop.runtime.threading.Timer", FakeTimer):
            first = DesktopRuntime(
                capture_provider=first_capture,
                input_provider=first_input,
                accessibility_provider=FakeAccessibilityProvider(),
                gate=FakeGate(),
            )
            second = DesktopRuntime(
                capture_provider=second_capture,
                input_provider=second_input,
                accessibility_provider=FakeAccessibilityProvider(),
                gate=FakeGate(),
            )

            self.assertEqual(first.start_capture()["response"], "first")
            self.assertEqual(second.start_capture()["response"], "second")
            self.assertEqual(len(FakeTimer.created), 2)
            self.assertIsNot(first.capture_provider, second.capture_provider)

            first.close()
            first.close()
            self.assertTrue(FakeTimer.created[0].cancelled)
            self.assertFalse(FakeTimer.created[1].cancelled)
            self.assertEqual(first_capture.close_count, 1)
            self.assertEqual(first_input.close_count, 1)
            self.assertTrue(second_capture.is_open())
            second.close()

    def test_expiry_closes_capture_even_if_the_open_flag_drifted(self) -> None:
        """The close must not depend on `is_open()`.

        That flag is known to drift: a screen lock makes Mutter close the
        session while the Python side still reports open (measured
        2026-09-13). A close that asks first would skip exactly the case it
        exists for, and closing twice costs nothing.
        """
        capture = FakeCaptureProvider()
        gate = FakeGate()
        with mock.patch("pcbridge.desktop.runtime.threading.Timer", FakeTimer):
            runtime = DesktopRuntime(
                capture_provider=capture,
                input_provider=FakeInputProvider(),
                accessibility_provider=FakeAccessibilityProvider(),
                gate=gate,
            )
            runtime.start_capture()
            capture.open = False          # the handle now lies about itself
            gate.unlocked = False         # and the grant is over
            runtime.close_capture_if_locked()
            self.assertEqual(capture.close_count, 1)

            # Still unlocked: nothing is closed, the deadline is rearmed.
            gate.unlocked = True
            runtime.close_capture_if_locked()
            self.assertEqual(capture.close_count, 1)
            runtime.close()

    def test_touch_grant_rearms_only_the_owning_runtime(self) -> None:
        capture = FakeCaptureProvider()
        gate = FakeGate()
        with mock.patch("pcbridge.desktop.runtime.threading.Timer", FakeTimer):
            runtime = DesktopRuntime(
                capture_provider=capture,
                input_provider=FakeInputProvider(),
                accessibility_provider=FakeAccessibilityProvider(),
                gate=gate,
            )
            runtime.start_capture()
            first_timer = FakeTimer.created[-1]
            token = object()
            runtime.touch_grant(token)

            self.assertEqual(gate.touches, 1)
            self.assertIs(gate.last_touch_token, token)
            self.assertTrue(first_timer.cancelled)
            self.assertEqual(len(FakeTimer.created), 2)
            runtime.close()

    def test_mcp_registration_uses_the_injected_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cfg = make_config(root)
            runtime = DesktopRuntime(
                capture_provider=FakeCaptureProvider(),
                input_provider=FakeInputProvider(),
                accessibility_provider=FakeAccessibilityProvider(),
                gate=FakeGate(),
            )
            mcp = FastMCP("runtime-contract")

            returned = toolslib.register(
                mcp,
                cfg,
                JobManager(cfg.jobs_dir),
                transport="stdio",
                runtime=runtime,
            )

            self.assertIs(returned, runtime)
            self.assertIs(returned.input_provider, runtime.input_provider)
            runtime.close()

    def test_mcp_lock_revokes_before_releasing_any_resource(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cfg = make_config(root)
            events: list[str] = []

            class OrderedGate(FakeGate):
                spec = SimpleNamespace(enabled=True)

                def lock(self) -> str:
                    events.append("revoke")
                    return "locked"

                def audit(self, *args, **kwargs) -> None:
                    return None

                def check(self, *args, **kwargs):
                    return SimpleNamespace(allowed=True, reason="")

                def status_line(self) -> str:
                    return "open"

            class OrderedInput(FakeInputProvider):
                def release_all(self) -> list[str]:
                    events.append("release-input")
                    return []

                def take_auto_released(self) -> list[str]:
                    return []

                def held(self) -> list[str]:
                    return []

                def available(self) -> tuple[bool, str]:
                    return True, ""

            class OrderedCapture(FakeCaptureProvider):
                def close(self) -> None:
                    events.append("close-capture")
                    super().close()

                def kill_helpers(self) -> int:
                    events.append("legacy-kill")
                    return 0

                def describe_monitors(self) -> str:
                    return ""

            runtime = DesktopRuntime(
                capture_provider=OrderedCapture(),
                input_provider=OrderedInput(),
                accessibility_provider=FakeAccessibilityProvider(),
                gate=OrderedGate(),
            )
            mcp = FastMCP("lock-order-contract")
            toolslib.register(
                mcp,
                cfg,
                JobManager(cfg.jobs_dir),
                transport="stdio",
                runtime=runtime,
            )

            tool = asyncio.run(mcp.get_tool("desktop_lock"))
            self.assertEqual(tool.fn(), "locked")
            self.assertEqual(events[0], "revoke")
            self.assertEqual(
                events[1:],
                ["release-input", "close-capture", "legacy-kill"],
            )
            runtime.close()

    def test_cli_and_server_close_runtime_in_finally_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cfg = make_config(Path(raw))

            cli_runtime = SimpleNamespace(close=mock.Mock())
            with (
                mock.patch.object(do_cli, "load", return_value=cfg),
                mock.patch.object(do_cli, "runtime_of", return_value=cli_runtime),
                mock.patch.object(
                    do_cli, "_run_plan", side_effect=RuntimeError("cli stopped")
                ),
                self.assertRaisesRegex(RuntimeError, "cli stopped"),
            ):
                do_cli.main(['[{"a":"wait","ms":1}]'])
            cli_runtime.close.assert_called_once_with()

            server_runtime = SimpleNamespace(close=mock.Mock())
            fake_mcp = SimpleNamespace(
                _pcbridge_desktop_runtime=server_runtime,
            )
            with (
                mock.patch.object(serverlib, "load_config", return_value=cfg),
                mock.patch.object(
                    serverlib.sessionlib, "ensure_session_env", return_value=[]
                ),
                mock.patch.object(
                    serverlib, "build_app", return_value=(fake_mcp, object())
                ),
                mock.patch.object(
                    serverlib, "_run_app", side_effect=RuntimeError("server stopped")
                ),
                self.assertRaisesRegex(RuntimeError, "server stopped"),
            ):
                serverlib.main(["--check", "-c", "unused.toml"])
            server_runtime.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
