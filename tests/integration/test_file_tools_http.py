"""The file tools' "Done when" checks against a real daemon over HTTP, the
path a remote client (claude.ai, ChatGPT, a phone) takes: OAuth, then MCP.

A throwaway config, state directory, socket and HTTP port, as in
`test_remote_access.py`, whose harness this borrows; it never touches the
user's daemon, config or files.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.contracts.test_fs_pages import big_text, body_of  # noqa: E402
from tests.integration import test_remote_access as remote  # noqa: E402

_HARNESS = ("setUp", "_write_config", "_start_daemon", "_stop_daemon", "_rpc", "_session",
            "_oauth")


class FileToolsOverHttp(unittest.TestCase):
    locals().update({name: getattr(remote.RemoteAccessTests, name) for name in _HARNESS})

    def _remote(self) -> None:
        self._write_config('profile = "full"')
        self._start_daemon()
        self.token = self._oauth("file-tools-test")["access_token"]
        self.sid = self._session(self.token)

    def call(self, name: str, args: dict) -> dict:
        r, data = self._rpc(self.token, "tools/call", {"name": name, "arguments": args},
                            sid=self.sid)
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertIn("result", data, data)
        return data["result"]

    def text(self, name: str, args: dict) -> str:
        return self.call(name, args)["content"][0]["text"]

    def test_paged_read_reaches_every_part_of_a_large_file(self) -> None:
        self._remote()
        text = big_text()
        f = self.tmp / "work" / "big.txt"
        f.write_text(text, encoding="utf-8")
        got, args, calls = [], {"path": str(f), "max_chars": 60000}, 0
        while True:
            reply = self.text("fs_read", args)
            calls += 1
            got.append(body_of(reply))
            m = re.search(r'cursor="([^"]+)"', reply)
            if not m:
                break
            args = {"path": str(f), "cursor": m.group(1), "max_chars": 60000}
        self.assertEqual("".join(got), text)
        self.assertGreater(len(text), 60000)
        self.assertGreaterEqual(calls, 3)
        middle = self.text("fs_read", {"path": str(f), "start_line": 701, "max_lines": 1})
        self.assertIn("MIDDLE-MARKER", middle)
        end = self.text("fs_read", {"path": str(f), "start_line": -1})
        self.assertIn("END-MARKER", end)
        self.assertIn("End of file.", end)


if __name__ == "__main__":
    unittest.main()
