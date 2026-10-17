"""Creating the remote password: generated or typed, saved as a hash, shown once.

The terminal dialog (`cli/remotepw.py`) runs with `input` and `getpass`
replaced, so nothing here needs a terminal.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import hashing as H  # noqa: E402
from pcbridge import loginpw  # noqa: E402
from pcbridge import passphrase  # noqa: E402
from pcbridge import remotepassword as rp  # noqa: E402
from pcbridge import settings as S  # noqa: E402
from pcbridge.cli import ops  # noqa: E402
from pcbridge.cli import remotepw  # noqa: E402

CONFIG = """\
config_version = 2
public_url = "https://box.example.ts.net"

[auth]
password = ""
static_token = ""

[paths]
state_dir = "{state}"

[agents.claude]
command = ["true"]
"""
ADDRESS = "https://box.example.ts.net/mcp"
LOGIN = "the-users-login-password"


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pcb-rpw-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = self.tmp / "config.toml"
        self.path.write_text(CONFIG.format(state=self.tmp / "state"))
        os.chmod(self.path, 0o600)
        env = mock.patch.dict(os.environ, {"NO_COLOR": "1"})
        env.start()
        self.addCleanup(env.stop)
        for k in ("PCBRIDGE_PASSWORD", "PCBRIDGE_CONFIG"):
            os.environ.pop(k, None)
        # PAM stands in: LOGIN is the user's login password.
        pam = mock.patch.object(loginpw, "verify", lambda p: p == LOGIN)
        pam.start()
        self.addCleanup(pam.stop)

    def stored(self) -> str:
        return tomllib.loads(self.path.read_text())["auth"]["password"]

    def run_dialog(self, answers: list[str], secrets_: list[str] = (), tty: bool = True,
                   replacing: bool = False):
        out = io.StringIO()
        inputs = iter(answers)
        hidden = iter(secrets_)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                mock.patch("builtins.input", lambda _="": next(inputs)), \
                mock.patch.object(remotepw.getpass, "getpass", lambda _="": next(hidden)), \
                mock.patch.object(remotepw.sys.stdin, "isatty", lambda: tty):
            result = remotepw.create(self.path, ADDRESS, replacing=replacing)
        return result, out.getvalue()


class PassphraseTests(unittest.TestCase):
    def test_six_words_from_the_list_about_77_bits(self) -> None:
        pool = passphrase.words()
        self.assertEqual(len(pool), 7772)  # 7776 less the four hyphenated words
        self.assertFalse(any("-" in w for w in pool))
        self.assertGreater(passphrase.bits(), 77)
        a, b = passphrase.generate(), passphrase.generate()
        self.assertNotEqual(a, b)
        self.assertEqual(len(a.split("-")), 6)
        self.assertTrue(set(a.split("-")) <= set(pool))
        self.assertIsNone(rp.check(a))

    def test_check(self) -> None:
        self.assertIn("at least 12", rp.check("short"))
        self.assertIn("space", rp.check(" a-long-password-here"))
        self.assertIsNone(rp.check("a-long-password-here"))


class DialogTests(Case):
    def test_the_default_generates_saves_and_shows_once(self) -> None:
        result, out = self.run_dialog(["", ""], [LOGIN])
        self.assertIsNotNone(result)
        shown = next(line.split()[-1] for line in out.splitlines() if "Password:" in line)
        self.assertEqual(len(shown.split("-")), 6)
        self.assertTrue(H.verify_password(shown, self.stored()))
        self.assertNotIn(shown, self.path.read_text())
        self.assertIn(ADDRESS, out)
        self.assertIn("only time it is shown", out)

    def test_c_copies_before_it_is_confirmed(self) -> None:
        copied = []
        with mock.patch.object(rp, "copy_to_clipboard", lambda text: copied.append(text) or True):
            result, out = self.run_dialog(["1", "c", ""], [LOGIN])
        self.assertIsNotNone(result)
        self.assertEqual(len(copied), 1)
        self.assertTrue(H.verify_password(copied[0], self.stored()))
        self.assertIn("copied to the clipboard", out)

    def test_a_typed_password_is_checked_twice_and_not_shown(self) -> None:
        result, out = self.run_dialog(
            ["2"], [LOGIN, "short", "a-typed-long-password", "different-long-password",
                    "a-typed-long-password", "a-typed-long-password"])
        self.assertIsNotNone(result)
        self.assertIn("at least 12", out)
        self.assertIn("differ", out)
        self.assertTrue(H.verify_password("a-typed-long-password", self.stored()))
        self.assertNotIn("a-typed-long-password", out)
        self.assertIn(ADDRESS, out)

    def test_cancel_changes_nothing(self) -> None:
        before = self.path.read_text()
        for answers, hidden in ((["q"], [LOGIN]), (["2"], [LOGIN, ""]), ([], [""]),
                                (["1"], ["wrong", "wrong", "wrong"])):
            result, out = self.run_dialog(answers, hidden)
            self.assertIsNone(result)
            self.assertIn("Nothing was changed", out)
        self.assertEqual(self.path.read_text(), before)
        self.assertEqual(list(self.tmp.glob("config.toml.backup-*")), [])

    def test_without_a_terminal_the_password_is_shown_and_not_waited_on(self) -> None:
        result, out = self.run_dialog([""], [LOGIN], tty=False)  # only the menu reads input
        self.assertIsNotNone(result)
        self.assertIn("only time it is shown", out)


class LoginCheckTests(Case):
    def test_save_refuses_without_the_login_password(self) -> None:
        before = self.path.read_text()
        for login in ("", "wrong", LOGIN + "x"):
            with self.assertRaisesRegex(S.SettingsError, "not the login password"):
                rp.save("a-new-long-password", self.path, login_password=login)
        self.assertEqual(self.path.read_text(), before)

    def test_no_libpam_changes_nothing(self) -> None:
        def broken(_):
            raise loginpw.LoginCheckError("libpam is not available")

        with mock.patch.object(loginpw, "verify", broken):
            with self.assertRaisesRegex(S.SettingsError, "libpam"):
                rp.save("a-new-long-password", self.path, login_password=LOGIN)
        self.assertEqual(self.stored(), "")

    def test_wrong_answers_end_the_dialog_after_three(self) -> None:
        asked = []
        with mock.patch.object(remotepw.getpass, "getpass", lambda p="": asked.append(p) or "nope"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                self.assertIsNone(remotepw.ask_login())
        self.assertEqual(len(asked), 3)
        self.assertIn("not the login password", out.getvalue())

    def test_an_empty_login_password_never_reaches_pam(self) -> None:
        with mock.patch.object(loginpw, "_libs", side_effect=AssertionError("PAM called")):
            mock.patch.stopall()
            self.assertFalse(loginpw.verify(""))

    @unittest.skipUnless(os.environ.get("PCBRIDGE_TEST_PAM") == "1",
                         "PCBRIDGE_TEST_PAM=1 sends one wrong password through the real PAM "
                         "(it counts towards pam_faillock where that is on)")
    def test_real_pam_refuses_a_wrong_password(self) -> None:
        mock.patch.stopall()
        self.assertFalse(loginpw.verify("certainly-not-the-login-password-0"))


class SetupOfferTests(Case):
    def test_without_a_terminal_setup_only_says_how(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch.object(ops.sys.stdin, "isatty", lambda: False):
            ops._offer_remote_password(SimpleNamespace(yes=False), self.path)
        self.assertIn("Remote access is off", out.getvalue())
        self.assertEqual(self.stored(), "")

    def test_in_a_terminal_setup_asks_and_no_is_the_default(self) -> None:
        out, asked = io.StringIO(), []
        with contextlib.redirect_stdout(out), \
                mock.patch("builtins.input", lambda prompt="": asked.append(prompt) or ""), \
                mock.patch.object(ops.sys.stdin, "isatty", lambda: True):
            ops._offer_remote_password(SimpleNamespace(yes=False), self.path)
        self.assertIn("Create the remote password now? [y/N]", asked[0])
        self.assertIn("pcbridge remote start", out.getvalue())
        self.assertEqual(self.stored(), "")

    def test_yes_creates_it(self) -> None:
        answers = iter(["y", "", ""])
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                mock.patch("builtins.input", lambda _="": next(answers)), \
                mock.patch.object(remotepw.getpass, "getpass", lambda _="": LOGIN), \
                mock.patch.object(ops.sys.stdin, "isatty", lambda: True):
            ops._offer_remote_password(SimpleNamespace(yes=False), self.path)
        self.assertTrue(self.stored().startswith("scrypt$"))

    def test_an_existing_password_is_left_alone(self) -> None:
        rp.save("an-existing-long-password", self.path, login_password=LOGIN)
        before = self.path.read_text()
        with mock.patch("builtins.input", side_effect=AssertionError("asked")), \
                mock.patch.object(ops.sys.stdin, "isatty", lambda: True):
            ops._offer_remote_password(SimpleNamespace(yes=False), self.path)
        self.assertEqual(self.path.read_text(), before)


class LivePasswordTests(Case):
    def test_the_consent_page_reads_a_new_password_without_a_restart(self) -> None:
        from pcbridge import auth as authlib
        from pcbridge import config as configlib

        cfg = configlib.load_config(str(self.path))
        provider = authlib.SqliteOAuthProvider(cfg)
        self.assertEqual(provider.current_password(), "")
        self.assertFalse(provider.check_password(""))
        rp.save("a-password-set-while-running", self.path, login_password=LOGIN)
        self.assertTrue(provider.check_password("a-password-set-while-running"))
        self.assertFalse(provider.check_password("a-password-set-while-runnin"))
        # The setting is marked live: no restart advice.
        self.assertEqual(S.ConfigEditor(self.path).setting("auth.password").restart, S.RESTART_NONE)


if __name__ == "__main__":
    unittest.main()
