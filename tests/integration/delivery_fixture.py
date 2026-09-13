"""An offline pcbridge MCP server whose screenshots come from fixture frames.

Shared by `test_mcp_capture_delivery.py` and `delivery_server.py`, so the
in-memory client and the real stdio client talk to exactly the same server.
Only the frames are fake. Everything after them is production code:
`capture.py` crops, scales, stages and publishes; `tools.py` builds the
result; FastMCP serializes it.

Not a test module (no `test_` prefix): discovery must not collect it.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from fastmcp import FastMCP
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pcbridge import tools as toolslib  # noqa: E402
from pcbridge.config import AgentSpec, Config, DesktopSpec  # noqa: E402
from pcbridge.desktop import monitors as monitorslib  # noqa: E402
from pcbridge.desktop.backends.python import PythonCaptureProvider  # noqa: E402
from pcbridge.desktop.capabilities import (  # noqa: E402
    Capability,
    CapabilityEvidence,
    CapabilityState,
)
from pcbridge.desktop.runtime import DesktopRuntime  # noqa: E402
from pcbridge.desktop.safety import Decision  # noqa: E402
from pcbridge.jobs import JobManager  # noqa: E402
from pcbridge.shots import ShotStore  # noqa: E402


MONITORS = monitorslib._ordered(
    [
        monitorslib.Monitor(
            index=0, connector="DP-3", x=320, y=0, width=320, height=200,
            scale=1.0, primary=True,
        ),
        monitorslib.Monitor(
            index=0, connector="DP-4", x=0, y=0, width=320, height=200,
            scale=1.0, primary=False,
        ),
    ]
)
# The long edge the fixture server scales to: 320x200 frames become 160x100
# images, so a quadrant center sits 40 px away from any color edge.
LONG_EDGE = 160
QUADRANTS = {
    "DP-4": ((200, 30, 30), (30, 200, 30), (30, 30, 200), (200, 200, 30)),
    "DP-3": ((30, 200, 200), (200, 30, 200), (240, 240, 240), (20, 20, 20)),
}
QUADRANT_CENTERS = ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75))


def frame(connector: str) -> Image.Image:
    """A monitor frame with four solid quadrants, distinct per monitor."""
    monitor = next(m for m in MONITORS if m.connector == connector)
    image = Image.new("RGB", (monitor.width, monitor.height))
    half_w, half_h = monitor.width // 2, monitor.height // 2
    corners = ((0, 0), (half_w, 0), (0, half_h), (half_w, half_h))
    for color, (x, y) in zip(QUADRANTS[connector], corners):
        image.paste(color, (x, y, x + half_w, y + half_h))
    return image


def observed_quadrants(image: Image.Image) -> list[tuple[int, int, int]]:
    """The color at each quadrant center of a decoded image."""
    rgb = image.convert("RGB")
    return [
        rgb.getpixel((int(rgb.width * fx), int(rgb.height * fy)))
        for fx, fy in QUADRANT_CENTERS
    ]


def capability(name: str, scope: str) -> Capability:
    return Capability(
        name=name,
        state=CapabilityState.SUPPORTED,
        backend="fixture",
        scope=scope,
        reason_code=None,
        limitations=(),
        observed_at=1.0,
        evidence=CapabilityEvidence.PROBE,
        usable_now=True,
    )


class FixtureScreenCast:
    """The screencast handle shape, answering with the fixture frames."""

    def __init__(self) -> None:
        self.frames = 0

    def is_open(self) -> bool:
        return True

    def ensure_cursor(self, cursor: bool) -> bool:
        return False

    def start(self, monitors: list[str], cursor: bool = True) -> dict:
        return {"already": True, "monitors": list(monitors)}

    def stop(self) -> None:
        return None

    def close(self) -> None:
        return None

    def monitors(self) -> list[str]:
        return [monitor.connector for monitor in MONITORS]

    def capture(self, connector: str, path: str | Path) -> dict:
        frame(connector).save(path, format="PNG")
        self.frames += 1
        return {"ok": True, "path": str(path), "taken_at": time.time()}


class FixtureCaptureProvider(PythonCaptureProvider):
    """The real capture provider and pipeline over fixture frames.

    Probing is replaced: the real probe starts the system Python to look for
    GStreamer, which is neither offline nor what these tests are about.
    """

    def __init__(
        self,
        cfg: Config,
        *,
        after_capture: Callable[[list[Any]], None] | None = None,
        degraded_reason: str = "",
    ) -> None:
        super().__init__(cfg, FixtureScreenCast(), degraded_reason=degraded_reason)
        self.after_capture = after_capture

    def capability_token(self) -> tuple[Any, ...]:
        return ("fixture-capture",)

    def probe_capabilities(self) -> dict[str, Capability]:
        return {
            "capture.monitor": capability("capture.monitor", "os.capture"),
            "capture.window": capability("capture.window", "os.capture"),
        }

    def list_monitors(self) -> list[monitorslib.Monitor]:
        return list(MONITORS)

    def topology_id(self) -> str:
        return monitorslib.topology_id(MONITORS)

    def describe_monitors(self) -> str:
        return "fixture: two 320x200 monitors"

    def find_monitor(self, x: int, y: int) -> monitorslib.Monitor | None:
        return next(
            (
                m for m in MONITORS
                if m.x <= x < m.x + m.width and m.y <= y < m.y + m.height
            ),
            None,
        )

    def kill_helpers(self) -> int:
        return 0

    def capture(self, spec: int | str, **kwargs: Any) -> list[Any]:
        with mock.patch.object(monitorslib, "list_monitors", return_value=list(MONITORS)):
            shots = super().capture(spec, **kwargs)
        if self.after_capture is not None:
            self.after_capture(shots)
        return shots


class OpenGate:
    """A grant that is always open; audit events are kept for assertions."""

    def __init__(self) -> None:
        self.spec = SimpleNamespace(enabled=True)
        self.revoke_epoch = 0
        self.events: list[tuple[str, dict]] = []

    def check(self, *args: Any, **kwargs: Any) -> Decision:
        return Decision(True)

    def audit(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))

    def is_unlocked(self) -> bool:
        return True

    def remaining_seconds(self) -> int:
        return 30

    def hard_remaining_seconds(self) -> int:
        return 120

    def touch(self, *args: Any, **kwargs: Any) -> None:
        return None

    def current_token(self) -> None:
        return None

    def last_token(self) -> None:
        return None

    def status_line(self) -> str:
        return "masaustu kontrolu: izinli"

    def lock(self) -> str:
        return "Masaustu kontrolu kapatildi."

    def unlock(self, minutes: int, reason: str = "") -> str:
        return f"Masaustu kontrolu {minutes} dakika acildi."


class QuietInput:
    """An input provider that never opens a device."""

    def capability_token(self) -> str:
        return "quiet-input"

    def probe_capabilities(self) -> dict[str, Capability]:
        return {
            "input.pointer": capability("input.pointer", "os.pointer"),
            "input.keyboard": capability("input.keyboard", "os.keyboard"),
        }

    def available(self) -> tuple[bool, str]:
        return True, ""

    def ensure(self, keyboard: bool = False, pointer: bool = False) -> float:
        return 0.0

    def close(self) -> None:
        return None

    def held(self) -> list[str]:
        return []

    def release_all(self) -> list[str]:
        return []

    def take_auto_released(self) -> list[str]:
        return []

    @property
    def position(self) -> None:
        return None


class QuietAccessibility:
    def capability_token(self) -> str:
        return "quiet-accessibility"

    def probe_capabilities(self) -> dict[str, Capability]:
        return {
            "accessibility.read": capability("accessibility.read", "os.accessibility"),
            "accessibility.action": capability(
                "accessibility.action", "os.accessibility"
            ),
            "window.list": capability("window.list", "os.window"),
        }

    def available(self) -> tuple[bool, str]:
        return True, ""

    def focused_window(self) -> tuple[str, str]:
        return "fixture", "window"

    def close(self) -> None:
        return None


def make_config(root: Path) -> Config:
    config = Config(
        public_url="https://example.invalid",
        host="127.0.0.1",
        port=8765,
        mcp_path="/mcp",
        password="delivery-password",
        static_token="",
        access_token_ttl=60,
        refresh_token_ttl=120,
        auth_code_ttl=30,
        max_failed_attempts=3,
        lockout_seconds=60,
        manual_redirect=False,
        default_workdir=root,
        state_dir=root / "state",
        max_output_chars=4000,
        default_job_timeout=60,
        max_sync_timeout=60,
        agents={"fixture": AgentSpec(name="fixture", command=["true"])},
        inline_images="true",
        default_agent="fixture",
        desktop=DesktopSpec(
            enabled=True,
            batch_check_focus=False,
            screenshot_scale_long_edge=LONG_EDGE,
            include_pointer=False,
            # Hermetic: never look at the user's real `pcb-shot` directory.
            agent_shot_dir=str(root / "agent-shots"),
        ),
    )
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.jobs_dir.mkdir(parents=True, exist_ok=True)
    return config


def fixture_runtime(
    config: Config,
    *,
    after_capture: Callable[[list[Any]], None] | None = None,
    degraded_reason: str = "",
) -> DesktopRuntime:
    return DesktopRuntime(
        capture_provider=FixtureCaptureProvider(
            config, after_capture=after_capture, degraded_reason=degraded_reason
        ),
        input_provider=QuietInput(),
        accessibility_provider=QuietAccessibility(),
        gate=OpenGate(),
        screen_lock_probe=lambda: False,
        user_activity_probe=lambda: 60_000,
        extension_focus_probe=lambda: False,
    )


def build(
    root: Path,
    *,
    transport: str = "stdio",
    after_capture: Callable[[list[Any]], None] | None = None,
    degraded_reason: str = "",
) -> SimpleNamespace:
    """The fixture server: `mcp`, its `store`, `gate`, `config` and `runtime`."""
    config = make_config(root)
    store = ShotStore(config)
    runtime = fixture_runtime(
        config, after_capture=after_capture, degraded_reason=degraded_reason
    )
    mcp = FastMCP("capture-delivery")
    toolslib.register(
        mcp,
        config,
        JobManager(config.jobs_dir, default_timeout=60),
        store,
        transport=transport,
        runtime=runtime,
    )
    return SimpleNamespace(
        mcp=mcp,
        store=store,
        gate=runtime.gate,
        config=config,
        runtime=runtime,
    )
