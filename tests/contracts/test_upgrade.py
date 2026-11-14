"""`pcbridge upgrade` and the terminal UI's arrow: the release check (with a
fake GitHub), picking and checking the right file for each kind of
installation, the new version's setup, the restart note, the command's exit
codes, and the Overview turning its logo into the arrow and back. Nothing
here reaches the network or installs anything."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge import __version__  # noqa: E402
from pcbridge import upgrade as up  # noqa: E402
from pcbridge.cli import install as inst  # noqa: E402
from pcbridge.cli import upgradecmd  # noqa: E402
from pcbridge.tui.app import PcbridgeApp  # noqa: E402
from pcbridge.tui.logo import EDGE, GREEN_DIM, Logo  # noqa: E402
from tests.contracts.test_tui import NARROW, SIZE, FakeBackend, flat, screen_text, settle  # noqa: E402

NEW = "99.0.0"


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FakeGitHub:
    """Answers the API and the release downloads from a dict; counts calls."""

    def __init__(self, files: dict[str, bytes] | None = None, *, tag: str = f"v{NEW}",
                 fail: Exception | None = None) -> None:
        self.files = files or {}
        self.tag = tag
        self.fail = fail
        self.urls: list[str] = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.urls.append(url)
        if self.fail is not None:
            raise self.fail
        if url == up.LATEST_API:
            assets = [{"name": n, "browser_download_url": f"https://dl.example/{n}"} for n in self.files]
            body = {"tag_name": self.tag, "html_url": f"{up.RELEASES}/tag/{self.tag}",
                    "published_at": "2026-11-15T10:00:00Z", "assets": assets}
            return FakeResponse(json.dumps(body).encode())
        name = url.rsplit("/", 1)[-1]
        if name in self.files:
            return FakeResponse(self.files[name])
        import urllib.error

        raise urllib.error.HTTPError(url, 404, "not found", {}, None)


def sums(files: dict[str, bytes], wrong: str = "") -> bytes:
    lines = []
    for name, data in files.items():
        digest = hashlib.sha256(data + (b"x" if name == wrong else b"")).hexdigest()
        lines.append(f"{digest}  {name}")
    return ("\n".join(lines) + "\n").encode()


def release(names: list[str] | None = None) -> up.Release:
    return up.Release(version=NEW, tag=f"v{NEW}", url=f"{up.RELEASES}/tag/v{NEW}",
                      assets={n: f"https://dl.example/{n}" for n in names or []})


class Home(unittest.TestCase):
    """A throwaway HOME and XDG directories for each test."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pcb-upgrade-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        env = {"HOME": str(self.tmp), "XDG_CACHE_HOME": str(self.tmp / "cache"),
               "XDG_STATE_HOME": str(self.tmp / "state"), "XDG_DATA_HOME": str(self.tmp / "data")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(up.OFF_ENV, None)
        quiet = mock.patch("sys.stdout", io.StringIO())
        quiet.start()
        self.addCleanup(quiet.stop)


class VersionTests(unittest.TestCase):
    def test_versions_compare_as_numbers(self) -> None:
        self.assertEqual(up.parse_version("v2.10.1"), (2, 10, 1))
        self.assertTrue(up.newer("2.10.0", "2.9.4"))
        self.assertTrue(up.newer("v2.10.1", "2.10.0"))
        self.assertFalse(up.newer("2.10.0", "2.10.0"))
        self.assertFalse(up.newer("2.9.9", "2.10.0"))
        self.assertFalse(up.newer("", "2.10.0"))


class CheckTests(Home):
    def test_a_newer_release_is_reported_and_cached(self) -> None:
        gh = FakeGitHub({"sha256sums.txt": b""})
        st = up.check(opener=gh, current="2.10.0")
        self.assertTrue(st.available)
        self.assertEqual((st.latest, st.release.tag, st.release.published), (NEW, f"v{NEW}", "2026-11-15"))
        self.assertIn("sha256sums.txt", st.release.assets)
        again = up.check(opener=gh, current="2.10.0")
        self.assertTrue(again.available)
        self.assertEqual(len(gh.urls), 1, "the second check comes from the cache")
        up.check(opener=gh, current="2.10.0", use_cache=False)
        self.assertEqual(len(gh.urls), 2)

    def test_the_same_version_is_not_an_upgrade(self) -> None:
        st = up.check(opener=FakeGitHub(tag=f"v{__version__}"))
        self.assertFalse(st.available)
        self.assertEqual(st.error, "")

    def test_no_network_is_an_error_not_an_exception(self) -> None:
        st = up.check(opener=FakeGitHub(fail=OSError("network is unreachable")))
        self.assertFalse(st.available)
        self.assertIn("could not reach GitHub", st.error)

    def test_the_environment_turns_the_check_off(self) -> None:
        gh = FakeGitHub()
        with mock.patch.dict(os.environ, {up.OFF_ENV: "1"}):
            st = up.check(opener=gh)
        self.assertFalse(st.available)
        self.assertEqual(gh.urls, [])

    def test_an_old_cache_is_asked_again(self) -> None:
        gh = FakeGitHub()
        up.check(opener=gh, now=lambda: 1000.0)
        up.check(opener=gh, now=lambda: 1000.0 + up.CACHE_TTL + 1)
        self.assertEqual(len(gh.urls), 2)


class PickTests(unittest.TestCase):
    NAMES = ["pcbridge-2.10.1-py3-none-linux_x86_64.whl", "pcbridge-2.10.1.tar.gz",
             "pcbridge_2.10.1_ubuntu24.04_amd64.deb", "pcbridge_2.10.1_debian13_amd64.deb",
             "pcbridge_2.10.1_ubuntu26.04_amd64.deb", "pcbridge-2.10.1-1-x86_64.pkg.tar.zst",
             "sha256sums.txt", "constraints.txt"]

    def test_the_wheel_and_the_arch_package(self) -> None:
        self.assertEqual(up.pick_wheel(self.NAMES, "x86_64"), "pcbridge-2.10.1-py3-none-linux_x86_64.whl")
        self.assertEqual(up.pick_wheel(self.NAMES, "aarch64"), "")
        self.assertEqual(up.pick_pkg(self.NAMES), "pcbridge-2.10.1-1-x86_64.pkg.tar.zst")

    def test_a_deb_for_this_system_or_its_ubuntu_base(self) -> None:
        zorin = {"ID": "zorin", "VERSION_ID": "18", "UBUNTU_CODENAME": "noble"}
        self.assertEqual(up.deb_flavors(zorin), ["zorin18", "ubuntu24.04"])
        self.assertEqual(up.pick_deb(self.NAMES, up.deb_flavors(zorin)), "pcbridge_2.10.1_ubuntu24.04_amd64.deb")
        debian = {"ID": "debian", "VERSION_ID": "13"}
        self.assertEqual(up.pick_deb(self.NAMES, up.deb_flavors(debian)), "pcbridge_2.10.1_debian13_amd64.deb")
        self.assertEqual(up.pick_deb(self.NAMES, up.deb_flavors({"ID": "fedora", "VERSION_ID": "41"})), "")


class InstallTests(Home):
    WHEEL = f"pcbridge-{NEW}-py3-none-linux_x86_64.whl"

    def files(self, wrong: str = "") -> dict[str, bytes]:
        files = {self.WHEEL: b"wheel bytes", "constraints.txt": b"textual==8.2.8\n"}
        files["sha256sums.txt"] = sums(files, wrong)
        return files

    def test_a_user_install_gets_the_checked_wheel_in_its_venv(self) -> None:
        files = self.files()
        gh = FakeGitHub(files)
        runs: list[list[str]] = []
        progress: list[tuple[int, int]] = []
        venv = self.tmp / "venv"
        with mock.patch.object(sys, "prefix", str(venv)), \
                mock.patch.object(inst, "run_streamed", side_effect=lambda argv, **kw: runs.append(argv) or 0), \
                mock.patch.object(up, "_arch", return_value="x86_64"):
            line = up.install(release(list(files)), kind="user", opener=gh,
                              progress=lambda d, t: progress.append((d, t)))
        self.assertIn(f"installed {NEW}", line)
        self.assertEqual(runs[0][:3], [str(venv / "bin" / "python"), "-m", "pip"])
        self.assertIn("--force-reinstall", runs[0])
        self.assertIn("--no-deps", runs[0])
        self.assertTrue(runs[1][-1].endswith(f"{self.WHEEL}[desktop]"))
        self.assertIn("-c", runs[0])
        self.assertEqual((self.tmp / "data" / "pcbridge" / "constraints.txt").read_bytes(), b"textual==8.2.8\n")
        self.assertEqual(progress[-1], (len(files[self.WHEEL]), len(files[self.WHEEL])))

    def test_a_wheel_that_does_not_match_its_checksum_is_refused(self) -> None:
        files = self.files(wrong=self.WHEEL)
        runs: list[list[str]] = []
        with mock.patch.object(inst, "run_streamed", side_effect=lambda argv, **kw: runs.append(argv) or 0), \
                mock.patch.object(up, "_arch", return_value="x86_64"), \
                self.assertRaisesRegex(up.UpgradeError, "does not match its sha256"):
            up.install(release(list(files)), kind="user", opener=FakeGitHub(files))
        self.assertEqual(runs, [], "nothing is installed")

    def test_a_deb_goes_through_sudo_with_the_checked_file(self) -> None:
        deb = f"pcbridge_{NEW}_ubuntu24.04_amd64.deb"
        files = {deb: b"deb bytes"}
        files["sha256sums.txt"] = sums(files)
        seen: list[list[str]] = []
        with mock.patch("pcbridge.cli.plan.run_sudo", side_effect=lambda argv: seen.append(argv) or True), \
                mock.patch.object(up, "deb_flavors", return_value=["zorin18", "ubuntu24.04"]):
            line = up.install(release(list(files)), kind="deb", opener=FakeGitHub(files))
        self.assertEqual(line, f"installed {deb}")
        self.assertEqual(seen[0][:4], ["sudo", "apt-get", "install", "-y"])
        self.assertTrue(seen[0][-1].endswith(deb))

    def test_no_package_for_this_system_says_where_to_get_one(self) -> None:
        files = {f"pcbridge_{NEW}_debian13_amd64.deb": b"x"}
        files["sha256sums.txt"] = sums(files)
        with mock.patch.object(up, "deb_flavors", return_value=["fedora41"]), \
                self.assertRaisesRegex(up.UpgradeError, "no .deb for this system"):
            up.install(release(list(files)), kind="deb", opener=FakeGitHub(files))

    def test_a_git_checkout_pulls(self) -> None:
        calls = []

        class Res:
            returncode, stdout, stderr = 0, "Fast-forward\n", ""

        with mock.patch.object(inst, "run", side_effect=lambda argv, **kw: calls.append(argv) or Res()), \
                mock.patch.object(inst, "repo_root", return_value=Path("/src/pcbridge")):
            up.install(release(), kind="git", opener=FakeGitHub())
        self.assertEqual(calls[0], ["git", "-C", "/src/pcbridge", "pull", "--ff-only"])

    def test_a_pip_install_is_told_what_to_do(self) -> None:
        with self.assertRaisesRegex(up.UpgradeError, "installed with pip"):
            up.install(release(), kind="pip", opener=FakeGitHub())


class FinishTests(Home):
    def test_the_new_setup_runs_without_touching_settings_or_clients(self) -> None:
        seen = {}

        class Proc:
            def __init__(self, argv, **kw):
                seen["argv"], seen["env"] = argv, kw["env"]
                self.stdout = io.StringIO("Summary\n  ok    units up to date\n"
                                          "  ok    daemon 99.0.0 running, pid 7; socket and service enabled\n")

            def wait(self):
                return 0

        lines: list[str] = []
        with mock.patch.object(up, "launcher", return_value="/home/u/.local/bin/pcbridge"):
            ok, daemon = up.finish(0, line=lines.append, run=Proc)
        self.assertTrue(ok)
        self.assertTrue(daemon.startswith("daemon 99.0.0 running"))
        self.assertEqual(seen["argv"][:2], ["/home/u/.local/bin/pcbridge", "setup"])
        for flag in ("--yes", "--no-packages", "--no-connect", "--no-aliases"):
            self.assertIn(flag, seen["argv"])
        self.assertEqual(seen["env"]["PCBRIDGE_SETUP_PLAIN"], "1")
        self.assertIn("units up to date", lines)
        self.assertIn("daemon 99.0.0 running", up.log_path().read_text())

    def test_a_failing_setup_is_reported(self) -> None:
        class Proc:
            def __init__(self, argv, **kw):
                self.stdout = io.StringIO("Traceback\n")

            def wait(self):
                return 1

        with mock.patch.object(up, "launcher", return_value="/x/pcbridge"):
            ok, why = up.finish(0, run=Proc)
        self.assertFalse(ok)
        self.assertIn("status 1", why)

    def test_the_restart_note_is_read_once_by_the_new_version(self) -> None:
        up.write_marker("2.10.0", NEW, "https://x")
        self.assertIsNone(up.take_marker(current="2.10.0"))   # not the version it was for: dropped
        up.write_marker("2.10.0", NEW, "https://x")
        note = up.take_marker(current=NEW)
        self.assertEqual(note["from"], "2.10.0")
        self.assertIsNone(up.take_marker(current=NEW))
        up.write_marker("2.10.0", NEW, "https://x")
        self.assertIsNone(up.take_marker(current=NEW, now=lambda: time.time() + 3600))


class CommandTests(Home):
    def status(self, available: bool, error: str = "") -> up.Status:
        return up.Status(current=__version__, latest=NEW if available else __version__,
                         available=available, release=release() if available else None, error=error)

    def run_main(self, argv: list[str], status: up.Status) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch.object(up, "check", return_value=status), mock.patch("sys.stdout", out):
            code = upgradecmd.main(argv)
        return code, out.getvalue()

    def test_check_exit_codes(self) -> None:
        code, text = self.run_main(["--check"], self.status(True))
        self.assertEqual(code, upgradecmd.EXIT_AVAILABLE)
        self.assertIn(f"pcbridge {__version__} → {NEW}", text)
        code, text = self.run_main(["--check"], self.status(False))
        self.assertEqual(code, 0)
        self.assertIn("is the latest release", text)
        code, text = self.run_main(["--check"], self.status(False, error="could not reach GitHub (x)"))
        self.assertEqual(code, 1)
        self.assertIn("Nothing was changed", text)

    def test_upgrade_installs_then_finishes(self) -> None:
        with mock.patch.object(up, "install", return_value="installed") as installed, \
                mock.patch.object(up, "finish", return_value=(True, "daemon 99.0.0 running, pid 7")) as finished:
            code, text = self.run_main(["--yes"], self.status(True))
        self.assertEqual(code, 0)
        installed.assert_called_once()
        finished.assert_called_once()
        self.assertIn(f"Upgraded to {NEW}", text)
        self.assertNotIn("update", text.lower().replace("pcbridge update", ""))

    def test_a_failed_install_changes_nothing_else(self) -> None:
        with mock.patch.object(up, "install", side_effect=up.UpgradeError("boom")), \
                mock.patch.object(up, "finish") as finished:
            code, text = self.run_main(["--yes"], self.status(True))
        self.assertEqual(code, 1)
        self.assertIn("boom", text)
        finished.assert_not_called()

    def test_the_command_is_listed(self) -> None:
        from pcbridge.cli.main import _PASSTHROUGH, COMMANDS

        self.assertIn("upgrade", _PASSTHROUGH)
        self.assertIn("upgrade", [c[1] for c in COMMANDS])


class LogoArrowTests(unittest.TestCase):
    def test_the_arrow_has_a_black_edge_and_fills_from_the_bottom(self) -> None:
        for glyph in ("word", "icon"):
            logo = Logo(glyph)
            logo.upgrade = "arrow"
            colors = set(logo.pixels().values())
            self.assertIn(EDGE, colors)
            logo.upgrade, logo.fill = "fill", 0.3
            pix = logo.pixels()
            top = min(y for (_x, y) in pix)
            bottom = max(y for (_x, y) in pix)
            self.assertIn(GREEN_DIM, [c for (x, y), c in pix.items() if y < top + 4])
            self.assertNotIn(GREEN_DIM, [c for (x, y), c in pix.items() if y > bottom - 2])
            logo.upgrade = "gone"
            self.assertEqual(logo.pixels(), {})

    def test_the_morph_starts_as_letters_and_ends_as_the_arrow(self) -> None:
        logo = Logo("word")
        letters = set(logo.pixels())
        logo.upgrade, logo.progress = "morph", 0.0
        self.assertEqual(set(logo.pixels()), letters)
        logo.progress = 1.0
        self.assertEqual(set(logo.pixels()), set(logo.arrow))


def run_app(body, size=SIZE, available: bool = True):
    status = up.Status(current=__version__, latest=NEW, available=available,
                       release=release() if available else None)

    async def go():
        with mock.patch.object(up, "check", return_value=status), \
                mock.patch.object(up, "take_marker", return_value=None):
            app = PcbridgeApp(FakeBackend())
            async with app.run_test(size=size) as pilot:
                await settle(app, pilot)
                await pilot.pause(1.0)            # the check runs 0.8 s after start
                await settle(app, pilot)
                await body(app, pilot)
            return app

    return asyncio.run(go())


class OverviewArrowTests(unittest.TestCase):
    def test_a_newer_release_turns_the_logo_into_the_arrow(self) -> None:
        async def body(app, pilot):
            self.assertEqual(app.upgrade_phase, "available")
            self.assertIn(app.query_one(Logo).upgrade, ("morph", "arrow"))
            text = flat(app)
            self.assertIn(f"↑ {NEW} available", text)
            self.assertIn(f"upgrade available · {__version__} → {NEW} · click the arrow or press u", text)
            footer = screen_text(app)[-1]
            self.assertIn(f"u Upgrade to {NEW}", footer)
            self.assertNotIn("u Update", footer)

        run_app(body)

    def test_nothing_changes_when_this_is_the_latest(self) -> None:
        async def body(app, pilot):
            self.assertEqual(app.upgrade_phase, "none")
            self.assertIsNone(app.query_one(Logo).upgrade)
            self.assertNotIn("↑", flat(app))
            self.assertIn("u Upgrade", screen_text(app)[-1])

        run_app(body, available=False)

    def test_u_installs_then_restart_to_upgrade_restarts_into_it(self) -> None:
        def fake_install(rel, progress=None, **kw):
            progress(5, 10)
            inst.ok(f"downloaded pcbridge-{NEW}.whl and checked it against sha256sums.txt")
            progress(10, 10)
            inst.ok("installed into ~/.local/share/pcbridge/venv")
            return "installed"

        notes = []

        async def body(app, pilot):
            with mock.patch.object(up, "install", side_effect=fake_install), \
                    mock.patch.object(up, "finish", return_value=(True, "daemon running")), \
                    mock.patch.object(up, "write_marker", side_effect=lambda *a: notes.append(a)):
                await pilot.press("u")
                await settle(app, pilot)
                self.assertEqual(app.upgrade_phase, "installed")
                await pilot.pause(1.2)             # the arrow shoots away
                text = flat(app)
                self.assertIn("Restart to upgrade", text)
                self.assertIn(f"✓ {NEW} is installed", text)
                self.assertIn("↑ restart to upgrade", text)
                self.assertFalse(app.query_one(Logo).display)
                await pilot.press("u")
                await settle(app, pilot)
                await pilot.pause(0.2)

        app = run_app(body)
        self.assertEqual(app.return_value, "reexec")
        self.assertEqual(notes, [(__version__, NEW, f"{up.RELEASES}/tag/v{NEW}")])

    def test_a_failed_install_brings_the_arrow_back(self) -> None:
        async def body(app, pilot):
            with mock.patch.object(up, "install", side_effect=up.UpgradeError("no space left")):
                await pilot.press("u")
                await settle(app, pilot)
            self.assertEqual(app.upgrade_phase, "failed")
            self.assertIn("✕ upgrade failed: no space left · press u to try again", flat(app))
            self.assertIn(app.query_one(Logo).upgrade, ("morph", "arrow"))

        run_app(body)

    def test_the_narrow_overview_shows_the_small_arrow(self) -> None:
        async def body(app, pilot):
            logo = app.query_one(Logo)
            self.assertEqual(logo.glyph, "icon")
            self.assertIn(logo.upgrade, ("morph", "arrow"))
            text = flat(app)
            self.assertIn(f"↑ {NEW}", text)
            self.assertIn("click the arrow or press u", text)

        run_app(body, size=NARROW)

    def test_the_new_version_says_it_was_upgraded(self) -> None:
        note = {"from": "2.10.0", "to": __version__, "url": "https://example/rel", "at": time.time()}

        async def go():
            with mock.patch.object(up, "take_marker", return_value=note), \
                    mock.patch.object(up, "check") as check:
                app = PcbridgeApp(FakeBackend())
                async with app.run_test(size=SIZE) as pilot:
                    await settle(app, pilot)
                    await pilot.pause(1.0)
                    await settle(app, pilot)
                    self.assertIn(f"✓ upgraded to {__version__} · what's new: https://example/rel", flat(app))
                    check.assert_not_called()

        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
