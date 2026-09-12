#!/usr/bin/env python3
"""Wire contracts for typed desktop MCP errors and capabilities."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastmcp import Client, FastMCP


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import tools as toolslib  # noqa: E402
from pcbridge.config import AgentSpec, Config, DesktopSpec  # noqa: E402
from pcbridge.desktop.capabilities import (  # noqa: E402
    Capability,
    CapabilityEvidence,
    CapabilityState,
)
from pcbridge.desktop.errors import ErrorCode  # noqa: E402
from pcbridge.desktop.runtime import DesktopRuntime  # noqa: E402
from pcbridge.desktop.safety import Decision  # noqa: E402
from pcbridge.jobs import JobManager  # noqa: E402
from pcbridge.shots import ShotStore  # noqa: E402


REQUIRED_CAPABILITIES = {
    "capture.monitor",
    "capture.window",
    "input.pointer",
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
        inline_images="false",
        default_agent="contract",
        desktop=DesktopSpec(enabled=True, batch_check_focus=False),
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
) -> Capability:
    return Capability(
        name=name,
        state=state,
        backend=backend,
        scope=scope,
        reason_code=reason_code,
        limitations=(),
        observed_at=1.0,
        evidence=CapabilityEvidence.PROBE,
        usable_now=state in {CapabilityState.SUPPORTED, CapabilityState.DEGRADED},
    )


class FakeGate:
    def __init__(self, decision: Decision | None = None) -> None:
        self.spec = SimpleNamespace(enabled=True)
        self.decision = Decision(True) if decision is None else decision
        self.revoke_epoch = 0

    def check(self, *args, **kwargs) -> Decision:
        return self.decision

    def audit(self, *args, **kwargs) -> None:
        return None

    def is_unlocked(self) -> bool:
        return True

    def remaining_seconds(self) -> int:
        return 30

    def hard_remaining_seconds(self) -> int:
        return 120

    def touch(self) -> None:
        return None

    def status_line(self) -> str:
        return "masaustu kontrolu: izinli"

    def lock(self) -> str:
        return "Masaustu kontrolu kapatildi."

    def unlock(self, minutes: int, reason: str = "") -> str:
        return f"Masaustu kontrolu {minutes} dakika acildi."


class FakeCapture:
    def __init__(self, *, available: tuple[bool, str] = (True, "")) -> None:
        self.availability = available

    def capability_token(self):
        return ("capture", self.availability[0])

    def probe_capabilities(self) -> dict[str, Capability]:
        state = (
            CapabilityState.SUPPORTED
            if self.availability[0]
            else CapabilityState.PERMISSION_REQUIRED
        )
        code = None if self.availability[0] else ErrorCode.PERMISSION_REQUIRED
        return {
            name: capability(
                name,
                state,
                backend="fake.capture",
                scope="os.capture",
                reason_code=code,
            )
            for name in ("capture.monitor", "capture.window")
        }

    def available(self) -> tuple[bool, str]:
        return self.availability

    def backend_name(self) -> str:
        return "fake.capture"

    def is_open(self) -> bool:
        return False

    def close(self) -> None:
        return None

    def describe_monitors(self) -> str:
        return "1 monitor"

    def kill_helpers(self) -> int:
        return 0

    def capture(self, *args, **kwargs):
        raise AssertionError("capture must not run when unavailable")

    def find_monitor(self, *args):
        return None

    def to_global(self, x, y, **kwargs):
        return x, y


class FakeInput:
    def __init__(self, *, available: tuple[bool, str] = (True, "")) -> None:
        self.availability = available
        self.keys_sent: list[str] = []
        self.ensure_calls: list[tuple[bool, bool]] = []

    def capability_token(self):
        return ("input", self.availability[0])

    def probe_capabilities(self) -> dict[str, Capability]:
        state = (
            CapabilityState.SUPPORTED
            if self.availability[0]
            else CapabilityState.PERMISSION_REQUIRED
        )
        code = None if self.availability[0] else ErrorCode.DEVICE_NOT_GRANTED
        return {
            "input.pointer": capability(
                "input.pointer", state, backend="fake.input", scope="os.pointer",
                reason_code=code,
            ),
            "input.keyboard": capability(
                "input.keyboard", state, backend="fake.input", scope="os.keyboard",
                reason_code=code,
            ),
        }

    def available(self) -> tuple[bool, str]:
        return self.availability

    def ensure(self, keyboard: bool = False, pointer: bool = False) -> float:
        self.ensure_calls.append((keyboard, pointer))
        return 0.0

    def close(self) -> None:
        return None

    def key(self, combo: str) -> None:
        self.keys_sent.append(combo)

    def key_down(self, combo: str) -> None:
        self.keys_sent.append(combo)

    def key_up(self, combo: str) -> None:
        self.keys_sent.append(combo)

    def held(self) -> list[str]:
        return []

    def release_all(self) -> list[str]:
        return []

    def take_auto_released(self) -> list[str]:
        return []

    @property
    def position(self):
        return None


class FakeAccessibility:
    def __init__(self) -> None:
        self.click_count = 0

    def capability_token(self):
        return "accessibility"

    def probe_capabilities(self) -> dict[str, Capability]:
        return {
            name: capability(
                name,
                CapabilityState.SUPPORTED,
                backend="fake.accessibility",
                scope="os.accessibility" if name.startswith("accessibility") else "os.window",
            )
            for name in ("accessibility.read", "accessibility.action", "window.list")
        }

    def available(self) -> tuple[bool, str]:
        return True, ""

    def click(self, node_id: str, action: str = "click") -> dict:
        self.click_count += 1
        return {"role": "button", "name": "Save"}

    def focused_window(self) -> tuple[str, str]:
        return "fake", "window"

    def close(self) -> None:
        return None


def build_mcp(
    root: Path,
    *,
    gate: FakeGate | None = None,
    capture: FakeCapture | None = None,
    input_provider: FakeInput | None = None,
    accessibility: FakeAccessibility | None = None,
) -> tuple[FastMCP, FakeAccessibility]:
    config = make_config(root)
    gate = gate or FakeGate()
    capture = capture or FakeCapture()
    input_provider = input_provider or FakeInput()
    accessibility = accessibility or FakeAccessibility()
    runtime = DesktopRuntime(
        capture_provider=capture,
        input_provider=input_provider,
        accessibility_provider=accessibility,
        gate=gate,
        screen_lock_probe=lambda: False,
        user_activity_probe=lambda: 60_000,
    )
    mcp = FastMCP("contract")
    toolslib.register(
        mcp,
        config,
        JobManager(config.jobs_dir, default_timeout=60),
        ShotStore(config),
        transport="stdio",
        runtime=runtime,
    )
    return mcp, accessibility


class McpErrorContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_window_focus_extension_does_not_require_uinput(self) -> None:
        input_provider = FakeInput(available=(False, "/dev/uinput izni yok"))
        with tempfile.TemporaryDirectory() as raw:
            mcp, _tree = build_mcp(Path(raw), input_provider=input_provider)
            with (
                mock.patch.object(
                    toolslib.appslib, "extension_focus_available", return_value=True
                ),
                mock.patch.object(
                    toolslib.appslib,
                    "focus",
                    return_value="Target GNOME eklentisiyle one alindi",
                ),
            ):
                async with Client(mcp) as client:
                    result = await client.call_tool(
                        "window_focus", {"window": "Target", "force": True},
                        raise_on_error=False,
                    )

        self.assertFalse(result.is_error)
        self.assertEqual(input_provider.ensure_calls, [])

    async def test_window_focus_without_extension_keeps_keyboard_preflight(self) -> None:
        input_provider = FakeInput(available=(False, "/dev/uinput izni yok"))
        with tempfile.TemporaryDirectory() as raw:
            mcp, _tree = build_mcp(Path(raw), input_provider=input_provider)
            with (
                mock.patch.object(
                    toolslib.appslib, "extension_focus_available", return_value=False
                ),
                mock.patch.object(toolslib.appslib, "focus") as focus,
            ):
                async with Client(mcp) as client:
                    result = await client.call_tool(
                        "window_focus", {"window": "Target", "force": True},
                        raise_on_error=False,
                    )

        self.assertTrue(result.is_error)
        self.assertEqual(
            result.structured_content["error"]["permission_scope"], "os.keyboard"
        )
        focus.assert_not_called()

    async def test_batch_focus_extension_does_not_preopen_keyboard(self) -> None:
        input_provider = FakeInput(available=(False, "/dev/uinput izni yok"))
        with tempfile.TemporaryDirectory() as raw:
            mcp, _tree = build_mcp(Path(raw), input_provider=input_provider)
            with (
                mock.patch.object(
                    toolslib.appslib, "extension_focus_available", return_value=True
                ),
                mock.patch.object(
                    toolslib.appslib,
                    "focus",
                    return_value="Target GNOME eklentisiyle one alindi",
                ),
            ):
                async with Client(mcp) as client:
                    result = await client.call_tool(
                        "computer_batch",
                        {
                            "actions": '[{"a":"focus","window":"Target"}]',
                            "final": "none",
                            "force": True,
                        },
                        raise_on_error=False,
                    )

        self.assertFalse(result.is_error)
        self.assertEqual(input_provider.ensure_calls, [])

    async def test_system_capabilities_is_read_only_and_structured(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            mcp, _tree = build_mcp(Path(raw))
            tool = await mcp.get_tool("system_capabilities")
            async with Client(mcp) as client:
                result = await client.call_tool("system_capabilities")

        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["type"], "pcbridge.desktop.capabilities")
        self.assertEqual(
            set(result.structured_content["capabilities"]), REQUIRED_CAPABILITIES
        )
        self.assertTrue(tool.annotations.readOnlyHint)
        self.assertIsNone(tool.output_schema)

    async def test_gate_error_has_pcbridge_desktop_scope(self) -> None:
        gate = FakeGate(
            Decision(
                False,
                "Masaustu kontrolu su an kilitli.",
                code=ErrorCode.GRANT_REQUIRED,
                permission_scope="pcbridge.desktop",
            )
        )
        with tempfile.TemporaryDirectory() as raw:
            mcp, _tree = build_mcp(Path(raw), gate=gate)
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "screen_capture", raise_on_error=False
                )

        self.assertTrue(result.is_error)
        self.assertEqual(result.content[0].text, "⛔ Masaustu kontrolu su an kilitli.")
        self.assertEqual(result.structured_content["type"], "pcbridge.desktop")
        self.assertEqual(
            result.structured_content["error"]["permission_scope"],
            "pcbridge.desktop",
        )

    async def test_capture_backend_error_has_os_capture_scope(self) -> None:
        capture = FakeCapture(available=(False, "ekran izni verilmedi"))
        with tempfile.TemporaryDirectory() as raw:
            mcp, _tree = build_mcp(Path(raw), capture=capture)
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "screen_capture", raise_on_error=False
                )

        self.assertTrue(result.is_error)
        self.assertEqual(
            result.content[0].text,
            "⛔ Ekran goruntusu alinamiyor: ekran izni verilmedi",
        )
        self.assertEqual(
            result.structured_content["error"]["permission_scope"], "os.capture"
        )

    async def test_pointer_backend_error_has_os_pointer_scope(self) -> None:
        input_provider = FakeInput(available=(False, "/dev/uinput izni yok"))
        with tempfile.TemporaryDirectory() as raw:
            mcp, _tree = build_mcp(Path(raw), input_provider=input_provider)
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "mouse", {"action": "click", "x": 1, "y": 1},
                    raise_on_error=False,
                )

        self.assertTrue(result.is_error)
        self.assertEqual(
            result.content[0].text,
            "⛔ Sanal girdi cihazi kullanilamiyor: /dev/uinput izni yok",
        )
        self.assertEqual(
            result.structured_content["error"]["permission_scope"], "os.pointer"
        )

    async def test_unconfirmed_close_shortcut_is_refused_on_the_wire(self) -> None:
        """KURALLAR.md sec. 4, madde 5 -- MCP telinde gorunur ve tus gitmez."""
        input_provider = FakeInput()
        with tempfile.TemporaryDirectory() as raw:
            mcp, _tree = build_mcp(Path(raw), input_provider=input_provider)
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "keyboard",
                    {"action": "key", "keys": "alt+F4"},
                    raise_on_error=False,
                )

        self.assertTrue(result.is_error)
        self.assertEqual(
            result.structured_content["error"]["code"], "CONFIRMATION_REQUIRED"
        )
        self.assertEqual(input_provider.keys_sent, [], "kapiya ragmen tus gonderildi")

    async def test_confirmed_close_shortcut_goes_through(self) -> None:
        input_provider = FakeInput()
        with tempfile.TemporaryDirectory() as raw:
            mcp, _tree = build_mcp(Path(raw), input_provider=input_provider)
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "keyboard",
                    {"action": "key", "keys": "alt+F4", "confirm_close": True},
                    raise_on_error=False,
                )

        self.assertFalse(result.is_error)
        self.assertEqual(input_provider.keys_sent, ["alt+F4"])

    async def test_batch_refuses_the_whole_plan_on_an_unconfirmed_close(self) -> None:
        input_provider = FakeInput()
        with tempfile.TemporaryDirectory() as raw:
            mcp, _tree = build_mcp(Path(raw), input_provider=input_provider)
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "computer_batch",
                    {"actions": '[{"a":"key","keys":"ctrl+v"},'
                                '{"a":"key","keys":"ctrl+q"}]'},
                    raise_on_error=False,
                )

        self.assertTrue(result.is_error)
        self.assertEqual(
            result.structured_content["error"]["code"], "CONFIRMATION_REQUIRED"
        )
        # Kapi AYRISTIRMADA: listenin ilk, zararsiz eylemi bile calismadi.
        self.assertEqual(input_provider.keys_sent, [])

    async def test_batch_preserves_completed_steps_when_final_capture_fails(self) -> None:
        capture = FakeCapture(available=(False, "ekran izni verilmedi"))
        with tempfile.TemporaryDirectory() as raw:
            mcp, tree = build_mcp(Path(raw), capture=capture)
            async with Client(mcp) as client:
                result = await client.call_tool(
                    "computer_batch",
                    {
                        "actions": '[{"a":"ui_click","id":"save"}]',
                        "final": "screen_capture",
                    },
                    raise_on_error=False,
                )

        self.assertTrue(result.is_error)
        self.assertEqual(tree.click_count, 1)
        self.assertIn("**1 eylemin 1 tanesi yapildi**", result.content[0].text)
        self.assertIn("ekran izni verilmedi", result.content[0].text)
        self.assertEqual(result.structured_content["batch"]["done"], 1)
        self.assertEqual(result.structured_content["batch"]["total"], 1)
        self.assertEqual(
            result.structured_content["error"]["permission_scope"], "os.capture"
        )


if __name__ == "__main__":
    unittest.main()
