"""The ranges the terminal UI's number inputs show are the loader's own:
every bound in settings.BOUNDS is checked against config.py, and
default_agent is a choice of the configured agents."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import settings as S  # noqa: E402
from tests.contracts.test_settings import example_config  # noqa: E402


class BoundsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "config.toml"
        self.path.write_text(example_config(self.dir / "state"))
        self.path.chmod(0o600)
        env = {k: v for k, v in os.environ.items() if not k.startswith("PCBRIDGE_")}
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def saves(self, key: str, value: int) -> bool:
        ed = S.ConfigEditor(self.path)
        ed.set(key, value)
        try:
            ed.save()
        except S.SettingsError:
            return False
        return True

    def test_every_bound_is_the_loaders(self) -> None:
        for key, (low, high, zero, _unit) in S.BOUNDS.items():
            with self.subTest(key=key):
                original = self.path.read_text()
                self.assertTrue(S.in_bounds(key, low))
                self.assertTrue(self.saves(key, low), f"{key} = {low} should load")
                self.path.write_text(original)
                if low - 1 != 0 or not zero:
                    self.assertFalse(S.in_bounds(key, low - 1))
                    self.assertFalse(self.saves(key, low - 1), f"{key} = {low - 1} should not load")
                    self.path.write_text(original)
                if zero:
                    self.assertTrue(self.saves(key, 0), f"{key} = 0 should load")
                    self.path.write_text(original)
                if high is not None:
                    self.assertTrue(self.saves(key, high), f"{key} = {high} should load")
                    self.path.write_text(original)
                    self.assertFalse(S.in_bounds(key, high + 1))
                    self.assertFalse(self.saves(key, high + 1), f"{key} = {high + 1} should not load")
                    self.path.write_text(original)

    def test_bounds_are_described(self) -> None:
        self.assertEqual(S.describe_bounds("files.link_ttl_seconds"), "10 s to 3600 s")
        self.assertEqual(S.describe_bounds("desktop.unlock_idle_seconds"), "0 (off) or 10 s and more")
        self.assertEqual(S.describe_bounds("auth.lockout_seconds"), "")

    def test_default_agent_is_a_choice_of_the_configured_agents(self) -> None:
        ed = S.ConfigEditor(self.path)
        s = ed.setting("default_agent")
        self.assertEqual(s.kind, "choice")
        self.assertEqual(set(s.choices), set(ed.agent_names))
        with self.assertRaises(S.SettingsError):
            ed.set("default_agent", "no-such-agent")


if __name__ == "__main__":
    unittest.main()
