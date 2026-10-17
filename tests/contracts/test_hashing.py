"""The remote password and the tokens are stored only as hashes.

`hashing.py` makes and checks them; the config loader, the settings editor,
backups and migrations write nothing else; `oauth.db` keys every token by its
SHA-256 digest and older databases are converted in place.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import auth as authlib  # noqa: E402
from pcbridge import config as configlib  # noqa: E402
from pcbridge import hashing as H  # noqa: E402
from pcbridge import settings as S  # noqa: E402

PASSWORD = "a-long-test-password-for-hashing"
TOKEN = "a-test-static-token-for-hashing-0123456789"

CONFIG = """\
config_version = 2
public_url = "http://localhost:8765"

[auth]
# the consent page asks for this
password = "{password}"  # keep me
static_token = "{token}"

[paths]
state_dir = "{state}"

[agents.claude]
command = ["true"]
"""


class HashTests(unittest.TestCase):
    def test_password_hash_verifies_only_the_password(self) -> None:
        stored = H.hash_password(PASSWORD)
        self.assertTrue(stored.startswith("scrypt$ln=15,r=8,p=1$"), stored)
        self.assertNotIn(PASSWORD, stored)
        self.assertTrue(H.verify_password(PASSWORD, stored))
        self.assertFalse(H.verify_password(PASSWORD + "x", stored))
        self.assertFalse(H.verify_password("", stored))
        # A new salt every time.
        self.assertNotEqual(stored, H.hash_password(PASSWORD))

    def test_no_password_matches_nothing(self) -> None:
        self.assertFalse(H.verify_password("", ""))
        self.assertFalse(H.verify_password(PASSWORD, ""))

    def test_a_damaged_hash_matches_nothing(self) -> None:
        for bad in ("scrypt$", "scrypt$ln=15$x$y", "scrypt$ln=x,r=8,p=1$AA$AA"):
            self.assertFalse(H.verify_password(PASSWORD, bad), bad)

    def test_plaintext_still_verifies(self) -> None:
        # An unmigrated file or PCBRIDGE_PASSWORD in the environment.
        self.assertTrue(H.verify_password(PASSWORD, PASSWORD))
        self.assertFalse(H.verify_password("other", PASSWORD))
        self.assertTrue(H.verify_token(TOKEN, TOKEN))

    def test_token_hash(self) -> None:
        stored = H.hash_token(TOKEN)
        self.assertTrue(H.is_token_hash(stored))
        self.assertTrue(H.verify_token(TOKEN, stored))
        self.assertFalse(H.verify_token(TOKEN + "x", stored))
        self.assertFalse(H.verify_token(TOKEN, ""))
        self.assertFalse(H.verify_token("", stored))

    def test_harden_text_keeps_comments_and_is_idempotent(self) -> None:
        text = CONFIG.format(password=PASSWORD, token=TOKEN, state="/tmp/x")
        new, keys = H.harden_text(text)
        self.assertEqual(keys, ["password", "static_token"])
        self.assertNotIn(PASSWORD, new)
        self.assertNotIn(TOKEN, new)
        self.assertIn("# the consent page asks for this", new)
        self.assertIn("# keep me", new)
        self.assertEqual(H.harden_text(new), (new, []))

    def test_harden_text_reuses_a_matching_hash(self) -> None:
        text = CONFIG.format(password=PASSWORD, token=TOKEN, state="/tmp/x")
        first, _ = H.harden_text(text)
        self.assertEqual(H.harden_text(text, reuse=first)[0], first)
        other = CONFIG.format(password="a-different-long-password", token=TOKEN, state="/tmp/x")
        self.assertNotEqual(H.harden_text(other, reuse=first)[0], first)

    def test_an_empty_secret_stays_empty(self) -> None:
        text = CONFIG.format(password=PASSWORD, token="", state="/tmp/x")
        new, keys = H.harden_text(text)
        self.assertEqual(keys, ["password"])
        self.assertIn('static_token = ""', new)


class ConsentPageTests(unittest.TestCase):
    def test_every_page_template_formats(self) -> None:
        # _DONE carried unescaped CSS braces, so every refusal page (an
        # expired request, the lockout) answered 500 instead of its message.
        done = authlib._DONE.format(title="T", msg="M")
        self.assertIn("<h2>T</h2><p>M</p>", done)
        self.assertIn("body{background", done)
        authlib._PAGE.format(rid="r", client_name="c", redirect="u", error="")
        authlib._MANUAL.format(target="t")


class ConfigFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pcb-hash-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = self.tmp / "config.toml"
        self.path.write_text(CONFIG.format(password=PASSWORD, token=TOKEN, state=self.tmp / "state"))
        os.chmod(self.path, 0o600)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for k in ("PCBRIDGE_PASSWORD", "PCBRIDGE_STATIC_TOKEN", "PCBRIDGE_CONFIG"):
            os.environ.pop(k, None)

    def test_load_warns_about_plaintext_and_still_works(self) -> None:
        cfg = configlib.load_config(str(self.path), check_state=False)
        self.assertTrue(any("in plaintext" in w and "auth.password" in w for w in cfg.warnings),
                        cfg.warnings)
        self.assertTrue(H.verify_password(PASSWORD, cfg.password))
        for w in cfg.warnings:
            self.assertNotIn(PASSWORD, w)

    def test_hardened_load_rewrites_the_file(self) -> None:
        cfg, keys = configlib.load_config_hardened(str(self.path))
        self.assertEqual(keys, ["password", "static_token"])
        text = self.path.read_text()
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn(TOKEN, text)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertFalse(any("in plaintext" in w for w in cfg.warnings), cfg.warnings)
        self.assertTrue(H.verify_password(PASSWORD, cfg.password))
        self.assertTrue(H.verify_token(TOKEN, cfg.static_token))
        # A stored hash has no length to check.
        self.assertEqual(configlib.load_config_hardened(str(self.path))[1], [])

    def test_no_password_loads_and_means_no_remote_access(self) -> None:
        self.path.write_text(CONFIG.format(password="", token="", state=self.tmp / "state"))
        cfg = configlib.load_config(str(self.path), check_state=False)
        self.assertFalse(cfg.has_password)
        self.assertFalse(any("in plaintext" in w for w in cfg.warnings), cfg.warnings)
        self.assertFalse(H.verify_password("", cfg.password))

    def test_a_short_plaintext_password_is_still_refused(self) -> None:
        self.path.write_text(CONFIG.format(password="short", token="", state=self.tmp / "state"))
        with self.assertRaisesRegex(configlib.ConfigError, "at least 12"):
            configlib.load_config(str(self.path), check_state=False)

    def test_the_environment_wins_and_is_never_written(self) -> None:
        with mock.patch.dict(os.environ, {"PCBRIDGE_PASSWORD": "from-the-environment-123"}):
            cfg, _ = configlib.load_config_hardened(str(self.path))
            self.assertEqual(cfg.password, "from-the-environment-123")
        self.assertNotIn("from-the-environment-123", self.path.read_text())

    def test_the_editor_hashes_what_it_writes_and_backs_up_without_plaintext(self) -> None:
        ed = S.ConfigEditor(self.path)
        ed.set("auth.access_token_ttl", 3600)
        result = ed.save()
        for f in (self.path, result.backup):
            text = f.read_text()
            self.assertNotIn(PASSWORD, text, f)
            self.assertNotIn(TOKEN, text, f)
            self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600, f)
        ed.set("auth.password", "the-next-long-password")
        ed.save()
        stored = configlib.load_config(str(self.path), check_state=False).password
        self.assertTrue(H.verify_password("the-next-long-password", stored))
        self.assertNotIn("the-next-long-password", self.path.read_text())
        for backup in self.tmp.glob("config.toml.backup-*"):
            self.assertNotIn("the-next-long-password", backup.read_text())

    def test_the_editor_refuses_a_short_password(self) -> None:
        with self.assertRaisesRegex(S.SettingsError, "at least 12"):
            S.ConfigEditor(self.path).set("auth.password", "short")


class SetupTests(unittest.TestCase):
    def test_a_fresh_config_has_no_password_and_no_token(self) -> None:
        from pcbridge.cli import ops

        tmp = Path(tempfile.mkdtemp(prefix="pcb-fresh-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        dest = tmp / "pcbridge" / "config.toml"
        ops._fresh_config(dest)
        self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o600)
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in ("PCBRIDGE_PASSWORD", "PCBRIDGE_STATIC_TOKEN"):
                os.environ.pop(k, None)
            cfg = configlib.load_config(str(dest), check_state=False)
        self.assertEqual((cfg.password, cfg.static_token), ("", ""))
        self.assertFalse(cfg.has_password)


class TokenDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pcb-hash-db-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db = self.tmp / "oauth.db"

    def _legacy(self) -> dict[str, str]:
        tokens = {"code": "pcb_ac_" + "c" * 40, "access": "pcb_at_" + "a" * 50,
                  "refresh": "pcb_rt_" + "r" * 50}
        conn = sqlite3.connect(self.db)
        with conn:
            conn.executescript(authlib.SCHEMA)
            conn.execute("INSERT INTO auth_codes VALUES (?,?,?,?)",
                         (tokens["code"], "c1", json.dumps({"code": tokens["code"]}), 9e12))
            conn.execute("INSERT INTO access_tokens VALUES (?,?,?,?)",
                         (tokens["access"], "c1", json.dumps({"token": tokens["access"]}), None))
            conn.execute("INSERT INTO refresh_tokens VALUES (?,?,?,?,?)",
                         (tokens["refresh"], "c1", json.dumps({"token": tokens["refresh"]}), None,
                          tokens["access"]))
        conn.close()
        return tokens

    def test_plaintext_tokens_are_replaced_by_digests(self) -> None:
        tokens = self._legacy()
        self.assertEqual(authlib.hash_stored_tokens(self.db), 3)
        raw = b"".join(p.read_bytes() for p in self.tmp.glob("oauth.db*"))
        for token in tokens.values():
            self.assertNotIn(token.encode(), raw)
        conn = sqlite3.connect(self.db)
        row = conn.execute("SELECT token, data, access_token FROM refresh_tokens").fetchone()
        conn.close()
        self.assertEqual(row[0], H.token_digest(tokens["refresh"]))
        self.assertEqual(json.loads(row[1])["token"], H.token_digest(tokens["refresh"]))
        self.assertEqual(row[2], H.token_digest(tokens["access"]))
        # Safe to run on every start.
        self.assertEqual(authlib.hash_stored_tokens(self.db), 0)

    def test_plaintext_rows_are_counted_read_only(self) -> None:
        self._legacy()
        before = self.db.read_bytes()
        self.assertEqual(authlib.plaintext_token_rows(self.db), 3)
        self.assertEqual(self.db.read_bytes(), before)
        authlib.hash_stored_tokens(self.db)
        self.assertEqual(authlib.plaintext_token_rows(self.db), 0)
        self.assertEqual(authlib.plaintext_token_rows(self.tmp / "missing.db"), 0)

    def test_doctor_names_the_copies_that_still_hold_a_secret(self) -> None:
        from types import SimpleNamespace

        from pcbridge.cli import doctor

        cfg_path = self.tmp / "config.toml"
        cfg_path.write_text(CONFIG.format(password=H.hash_password(PASSWORD), token="", state=self.tmp))
        old = self.tmp / "config.toml.backup-20260101-000000"
        old.write_text(CONFIG.format(password=PASSWORD, token="", state=self.tmp))
        clean = self.tmp / "config.toml.backup-20260102-000000"
        clean.write_text(cfg_path.read_text())
        yedek = self.tmp / "config.toml.yedek-20260803-131545"  # 1.x named them so
        yedek.write_text(old.read_text())
        self._legacy()
        shutil.copy(self.db, self.tmp / "oauth.db.bak-2026-10-14")
        authlib.hash_stored_tokens(self.db)
        cfg = SimpleNamespace(source_path=cfg_path, db_path=self.db)
        with mock.patch.object(doctor.pathslib, "LEGACY_REPO_CONFIG", self.tmp / "none.toml"):
            found = doctor._plaintext_leftovers(cfg)
        self.assertEqual(found, [old, yedek, self.tmp / "oauth.db.bak-2026-10-14"])

    def test_a_missing_database_is_left_alone(self) -> None:
        self.assertEqual(authlib.hash_stored_tokens(self.db), 0)
        self.assertFalse(self.db.exists())


if __name__ == "__main__":
    unittest.main()
