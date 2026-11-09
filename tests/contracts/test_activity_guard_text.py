#!/usr/bin/env python3
"""The activity guard tells an agent what to do next (ROADMAP: review the
skill and every text an agent reads, first case `USER_ACTIVE`).

The guard refuses a write while the keyboard or pointer was used within
`[desktop] idle_guard_seconds`. Right after the user types a request on this
machine, that keystroke trips it, and pcbridge's own input does too (measured:
a uinput event resets the idle time, 104227 ms -> 151 ms). An agent that read
the old text stopped and reported a locked desktop. Every text it reads now
says the same thing: asked for the desktop work, retry with force; not asked,
wait.

No input is sent; the gate runs against a fake state provider.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import app as applib  # noqa: E402
from pcbridge import tools as toolslib  # noqa: E402
from pcbridge.desktop.errors import ErrorCode  # noqa: E402
from pcbridge.desktop.safety import ActivityState, ScreenLockState  # noqa: E402
from tests.contracts.test_desktop_state import (  # noqa: E402
    FakeDesktopStateProvider,
    make_gate,
)
from tests.contracts.test_tool_surface import build  # noqa: E402

SKILL = ROOT / "skills" / "computer-use" / "SKILL.md"

# The tools that send input or act on the desktop and take `force`.
FORCE_TOOLS = {
    "mouse", "keyboard", "ui_click", "ui_set_text", "window_focus",
    "computer_batch", "computer_task",
}


def refuse(idle_ms: int):
    with tempfile.TemporaryDirectory() as raw:
        state = FakeDesktopStateProvider(
            ScreenLockState.KNOWN_UNLOCKED, ActivityState.KNOWN, idle_ms=idle_ms,
        )
        gate = make_gate(Path(raw), state)  # idle_guard_seconds = 60
        gate.unlock(5)
        return gate.check("mouse", write=True), gate.check("mouse", write=True, force=True)


class RefusalTextTests(unittest.TestCase):
    def test_refusal_names_the_request_keystroke_and_both_next_steps(self) -> None:
        denied, forced = refuse(5_000)
        self.assertEqual(denied.code, ErrorCode.USER_ACTIVE)
        self.assertTrue(denied.retryable)
        self.assertTrue(forced.allowed)
        reason = denied.reason
        # What happened, and why that is expected right after a typed request.
        self.assertTrue(reason.startswith("The keyboard or pointer was used 5 seconds ago."), reason)
        self.assertIn("idle_guard_seconds", reason)
        self.assertIn("keystroke was the request", reason)
        self.assertIn("pcbridge's own", reason)
        # Asked: force, on this tool, not on desktop_unlock; pcb-do spells it --force.
        self.assertIn("asked you for this desktop work", reason)
        self.assertIn("force=true", reason)
        self.assertIn("not of desktop_unlock", reason)
        self.assertIn("--force", reason)
        # Not asked: wait, and for how long.
        self.assertIn("do not force", reason)
        self.assertIn("about 55 seconds", reason)
        self.assertIn("force=true", denied.suggested_action)
        self.assertIn("55", denied.suggested_action)

    def test_the_wait_is_never_shown_as_zero(self) -> None:
        denied, _ = refuse(59_900)
        self.assertIn("about 1 second ", denied.reason)
        self.assertTrue(denied.reason.startswith("The keyboard or pointer was used 59 seconds ago."))
        denied, _ = refuse(400)
        self.assertTrue(denied.reason.startswith("The keyboard or pointer was used just now."))


class ForceDescriptionTests(unittest.TestCase):
    def test_every_force_parameter_says_when_to_set_it(self) -> None:
        found = {}
        for tool in build(None):
            prop = tool.parameters.get("properties", {}).get("force")
            if prop is not None:
                found[tool.name] = prop.get("description", "")
        self.assertEqual(set(found), FORCE_TOOLS)
        for name, text in found.items():
            self.assertEqual(text, toolslib.FORCE_DESCRIPTION, name)
        text = toolslib.FORCE_DESCRIPTION
        self.assertIn("USER_ACTIVE", text)
        self.assertIn("asked you for this desktop work", text)
        self.assertIn("wait", text)
        self.assertIn("screen lock", text)


class SkillAndInstructionTests(unittest.TestCase):
    def test_skill_quotes_the_refusal_and_says_when_to_force(self) -> None:
        body = SKILL.read_text(encoding="utf-8")
        denied, _ = refuse(5_000)
        opening = "The keyboard or pointer was used"
        self.assertTrue(denied.reason.startswith(opening))
        self.assertIn(opening, body)
        self.assertIn("USER_ACTIVE", body)
        self.assertIn("--force", body)
        self.assertIn("asked you for this desktop work", body)

    def test_server_instructions_cover_the_guard(self) -> None:
        text = applib.INSTRUCTIONS
        self.assertIn("USER_ACTIVE", text)
        self.assertIn("force=true", text)


if __name__ == "__main__":
    unittest.main()
