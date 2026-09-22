"""Per-session state in the resident daemon (2.0 step 3)."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import sessionctx  # noqa: E402
from pcbridge.desktop import uitree  # noqa: E402


def _in_session(key: str, fn, *, env=None, transport="stdio"):
    token = sessionctx.bind(sessionctx.SessionInfo(key=key, transport=transport, env=env or {}))
    try:
        return fn()
    finally:
        sessionctx.reset(token)


class SessionContextTests(unittest.TestCase):
    def test_ui_dump_ids_do_not_leak_between_sessions(self) -> None:
        tree = uitree.UiTree()
        dump_a = uitree.Dump(app="a", window="A", nodes=[], snapshot="sa")
        _in_session("sock:1", lambda: setattr(tree, "_last", dump_a))
        self.assertIs(_in_session("sock:1", lambda: tree._last), dump_a)
        self.assertIsNone(_in_session("sock:2", lambda: tree._last))
        with self.assertRaises(uitree.UiTreeError):
            _in_session("sock:2", lambda: tree.resolve("#abcd"))
        sessionctx.session_ended("sock:1")
        self.assertIsNone(_in_session("sock:1", lambda: tree._last))

    def test_outside_any_session_the_key_is_the_process(self) -> None:
        self.assertEqual(sessionctx.key(), "process")
        self.assertEqual(sessionctx.transport("http"), "http")
        self.assertEqual(_in_session("s", lambda: sessionctx.transport("http")), "stdio")

    def test_env_allowlist_and_path_merge(self) -> None:
        env = sessionctx.filter_env(
            {"PATH": "/client/bin:/usr/bin", "SSH_AUTH_SOCK": "/a", "LC_ALL": "C",
             "CLAUDECODE": "1", "CLAUDE_CODE_EFFORT_LEVEL": "max", "MCP_X": "y", "https_proxy": "p"}
        )
        self.assertEqual(set(env), {"PATH", "SSH_AUTH_SOCK", "LC_ALL", "https_proxy"})
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/daemon/bin"}):
            merged = _in_session("s", sessionctx.job_env, env=env)
        self.assertEqual(merged["PATH"], "/client/bin:/usr/bin:/daemon/bin")
        self.assertNotIn("CLAUDECODE", merged)
        self.assertEqual(sessionctx.job_env(), {})

    def test_per_session_map_is_bounded(self) -> None:
        m: sessionctx.PerSession[int] = sessionctx.PerSession(limit=3)
        for i in range(5):
            m.set(i, session=f"k{i}")
        self.assertEqual(len(m), 3)
        self.assertIsNone(m.get(session="k0"))
        self.assertEqual(m.get(session="k4"), 4)


if __name__ == "__main__":
    unittest.main()
