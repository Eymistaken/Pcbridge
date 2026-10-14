"""`remoteclients`: grouping, selecting and revoking OAuth registrations, on
a database built by the real provider's schema; and the `[tools]` profiles
that decide what remote clients get.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import config as configlib  # noqa: E402
from pcbridge import remoteclients as rc  # noqa: E402
from pcbridge.auth import SCHEMA  # noqa: E402
from pcbridge.tools import DESKTOP_TOOLS, TOOL_HINTS, remote_profile, tools_in_profile  # noqa: E402

NOW = 1_800_000_000.0


class Db:
    def __init__(self, path: Path) -> None:
        self.path = path
        with sqlite3.connect(path) as conn:
            conn.executescript(SCHEMA)

    def client(self, cid: str, name: str | None, created: float = NOW - 100) -> None:
        data = {"client_id": cid, "redirect_uris": ["https://x/cb"]}
        if name is not None:
            data["client_name"] = name
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT INTO clients VALUES (?,?,?)", (cid, json.dumps(data), created))

    def token(self, table: str, cid: str, expires: float | None) -> None:
        with sqlite3.connect(self.path) as conn:
            tok = f"{table}-{cid}-{expires}"
            if table == "refresh_tokens":
                conn.execute("INSERT INTO refresh_tokens VALUES (?,?,?,?,?)", (tok, cid, "{}", expires, None))
            else:
                conn.execute(f"INSERT INTO {table} VALUES (?,?,?,?)", (tok, cid, "{}", expires))

    def count(self, table: str) -> int:
        with sqlite3.connect(self.path) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class RemoteClientsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="pcbrc-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.db = Db(self.dir / "oauth.db")
        d = self.db
        d.client("aaaaaaaa-1", "ChatGPT", NOW - 50)
        d.token("refresh_tokens", "aaaaaaaa-1", NOW + 86400 * 10)
        d.token("access_tokens", "aaaaaaaa-1", NOW + 3600)
        d.client("bbbbbbbb-1", "Google", NOW - 300)
        d.token("refresh_tokens", "bbbbbbbb-1", NOW - 1)          # expired
        d.client("bbbbbbbb-2", "google", NOW - 200)                 # same app, other case
        d.token("refresh_tokens", "bbbbbbbb-2", None)               # never expires
        d.client("cccccccc-1", None, NOW - 400)                     # no name, no tokens

    def test_groups_by_name_and_hides_stale_ones(self) -> None:
        groups = rc.clients(self.db.path, now=NOW)
        self.assertEqual([g.name for g in groups], ["ChatGPT", "Google"])  # the first spelling seen
        google = groups[1]
        self.assertEqual(len(google.registrations), 2)
        self.assertEqual(len(google.connected), 1)
        self.assertIsNone(google.expires)  # one token never expires
        self.assertEqual(groups[0].expires, NOW + 86400 * 10)
        names = [g.name for g in rc.clients(self.db.path, include_stale=True, now=NOW)]
        self.assertIn(rc.UNNAMED, names)

    def test_select_by_name_any_case_or_by_id_prefix(self) -> None:
        self.assertEqual({r.client_id for r in rc.select(self.db.path, "GOOGLE")},
                         {"bbbbbbbb-1", "bbbbbbbb-2"})
        self.assertEqual([r.client_id for r in rc.select(self.db.path, "aaaaaaaa")], ["aaaaaaaa-1"])
        with self.assertRaisesRegex(rc.RevokeError, "give more of the id"):
            rc.select(self.db.path, "bbbbbbbb")
        with self.assertRaisesRegex(rc.RevokeError, "No remote client is named 'aaa'"):
            rc.select(self.db.path, "aaa")  # too short for an id prefix
        with self.assertRaisesRegex(rc.RevokeError, "Give a client name"):
            rc.select(self.db.path, "  ")

    def test_revoke_deletes_the_registration_and_everything_issued_to_it(self) -> None:
        for table in ("auth_codes", "pending"):
            self.db.token(table, "aaaaaaaa-1", NOW + 60)
        audit = self.dir / "audit.log"
        res = rc.revoke(self.db.path, ["aaaaaaaa-1"], audit)
        self.assertEqual((res.names, res.registrations, res.access_tokens, res.refresh_tokens),
                         (["ChatGPT"], 1, 1, 1))
        for table in ("auth_codes", "pending", "access_tokens"):
            self.assertEqual(self.db.count(table), 0, table)
        self.assertEqual(self.db.count("clients"), 3)
        rec = json.loads(audit.read_text())
        self.assertEqual(rec["event"], "remote_client_revoked")
        self.assertNotIn("refresh_tokens-", audit.read_text())

    def test_revoke_all_and_a_missing_database(self) -> None:
        res = rc.revoke_all(self.db.path)
        self.assertEqual(res.registrations, 4)
        self.assertEqual(rc.clients(self.db.path, include_stale=True), [])
        missing = self.dir / "none.db"
        self.assertEqual(rc.clients(missing), [])
        self.assertEqual(rc.revoke_all(missing).registrations, 0)
        self.assertFalse(missing.exists())  # reading never creates it

    def test_describe_expiry(self) -> None:
        self.assertEqual(rc.describe_expiry(None), "never")
        self.assertEqual(rc.describe_expiry(NOW + 89 * 86400 + 5, NOW), "in 89 days")
        self.assertEqual(rc.describe_expiry(NOW + 5 * 3600, NOW), "in 5 hours")
        self.assertEqual(rc.describe_expiry(NOW + 10, NOW), "in 1 minutes")


class ProfileTests(unittest.TestCase):
    def test_readonly_offers_only_tools_that_change_nothing(self) -> None:
        ro = tools_in_profile("readonly")
        self.assertTrue(ro)
        for name in ro:
            self.assertTrue(TOOL_HINTS[name][0], f"{name} is not read-only")
        self.assertNotIn("shell_run", ro)
        self.assertNotIn("desktop_unlock", ro)
        self.assertFalse(ro & (DESKTOP_TOOLS - {"system_capabilities", "screen_info"}))

    def test_remote_profile_same_follows_profile(self) -> None:
        class Cfg:
            tools_profile = "core"
            remote_tools_profile = "same"

        self.assertEqual(remote_profile(Cfg), "core")
        Cfg.remote_tools_profile = "readonly"
        self.assertEqual(remote_profile(Cfg), "readonly")

    def test_the_loader_reads_and_checks_remote_profile(self) -> None:
        path = Path("/x/config.toml")
        self.assertEqual(configlib._tools_profile({}, path, "remote_profile"), "same")
        self.assertEqual(configlib._tools_profile({"tools": {"remote_profile": " ReadOnly "}}, path,
                                                  "remote_profile"), "readonly")
        with self.assertRaises(SystemExit) as cm:
            configlib._tools_profile({"tools": {"remote_profile": "phone"}}, path, "remote_profile")
        self.assertIn('"same" to follow `profile`', str(cm.exception))
        with self.assertRaises(SystemExit):
            configlib._tools_profile({"tools": {"profile": "same"}}, path)


if __name__ == "__main__":
    unittest.main()
