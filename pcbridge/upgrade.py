"""Upgrading pcbridge to the latest release: `pcbridge upgrade` and the
terminal UI's arrow.

Three parts, each usable alone:

- `check()` asks GitHub for the latest published release (a few seconds at
  most, answered from a short cache) and says whether it is newer than the
  running code. Nothing is said or changed when the network is not there.
- `install()` puts the new version on disk, by the kind of installation:
  the user install gets the release wheel, checked against the release's
  sha256sums.txt, in its own venv; a .deb or Arch package gets the
  release's package for this system through sudo; a git checkout pulls.
  The running daemon and terminal UI keep the old code.
- `finish()` runs the NEW version's `pcbridge setup --yes --no-packages
  --no-connect --no-aliases`, which refreshes the units, launchers and the
  desktop extension and restarts the daemon once it is idle. Settings and
  client registrations are left as they are.

Every word a user reads says "upgrade": `pcbridge update` already exists
and only loads the installed code into the daemon.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import __version__
from . import paths as pathslib

REPO = "Eymistaken/Pcbridge"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES = f"https://github.com/{REPO}/releases"
# GitHub's API refuses requests without a User-Agent; a generic one, nothing
# about the user or the machine.
USER_AGENT = "pcbridge-upgrade"
CHECK_TIMEOUT = 3.0          # the terminal UI's check at start: never in the way
CACHE_TTL = 600.0            # GitHub allows 60 unauthenticated calls an hour
MAX_DOWNLOAD = 256 * 1024 * 1024
DOWNLOAD_TIMEOUT = 60.0
# Set to anything to turn the check off (tests, offline machines, CI).
OFF_ENV = "PCBRIDGE_NO_UPGRADE_CHECK"

Opener = Callable[..., Any]
Progress = Callable[[int, int], None]   # (bytes so far, total or 0)


class UpgradeError(Exception):
    """An upgrade step failed; the text says what and what to do."""


@dataclass
class Release:
    version: str                 # "2.10.1"
    tag: str                     # "v2.10.1"
    url: str                     # the release page
    published: str = ""          # "2026-11-15"
    assets: dict[str, str] = field(default_factory=dict)   # name -> download URL


@dataclass
class Status:
    current: str
    latest: str = ""
    available: bool = False
    release: Release | None = None
    error: str = ""              # why the check has no answer ("" = it has one)


# ---------------------------------------------------------------------------
# versions
# ---------------------------------------------------------------------------

def parse_version(text: str) -> tuple[int, ...]:
    """"v2.10.1" -> (2, 10, 1); a part's trailing letters are ignored."""
    out = []
    for part in text.strip().lstrip("vV").split("."):
        m = re.match(r"\d+", part)
        if not m:
            break
        out.append(int(m.group()))
    return tuple(out)


def newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return bool(a) and a > b


# ---------------------------------------------------------------------------
# the check
# ---------------------------------------------------------------------------

def cache_path() -> Path:
    return pathslib.cache_home() / "upgrade-check.json"


def _read_cache(now: float) -> Release | None:
    try:
        data = json.loads(cache_path().read_text(encoding="utf-8"))
        if now - float(data["at"]) > CACHE_TTL or now < float(data["at"]):
            return None
        return Release(**data["release"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cache(release: Release, now: float) -> None:
    try:
        path = cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"at": now, "release": asdict(release)}), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _get(url: str, opener: Opener, timeout: float, limit: int = 4 * 1024 * 1024) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/vnd.github+json"})
    try:
        with opener(request, timeout=timeout) as response:
            data = response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        raise UpgradeError(f"GitHub answered HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", None) or exc
        raise UpgradeError(f"could not reach GitHub ({reason})") from exc
    if len(data) > limit:
        raise UpgradeError(f"{url} answered with more than {limit // (1024 * 1024)} MB")
    return bytes(data)


def latest_release(opener: Opener = urllib.request.urlopen, timeout: float = CHECK_TIMEOUT) -> Release:
    """The latest published release (drafts and prereleases are not "latest")."""
    body = _get(LATEST_API, opener, timeout)
    try:
        data = json.loads(body)
        tag = str(data["tag_name"])
    except (ValueError, KeyError, TypeError) as exc:
        raise UpgradeError("GitHub's answer about the latest release could not be read") from exc
    assets = {}
    for a in data.get("assets") or []:
        if isinstance(a, dict) and a.get("name") and a.get("browser_download_url"):
            assets[str(a["name"])] = str(a["browser_download_url"])
    return Release(version=tag.lstrip("vV"), tag=tag, url=str(data.get("html_url") or f"{RELEASES}/tag/{tag}"),
                   published=str(data.get("published_at") or "")[:10], assets=assets)


def check(*, timeout: float = CHECK_TIMEOUT, use_cache: bool = True,
          opener: Opener = urllib.request.urlopen, now: Callable[[], float] = time.time,
          current: str = __version__) -> Status:
    """Is a newer release out? Never raises: a failure is `Status.error`."""
    if os.environ.get(OFF_ENV):
        return Status(current=current, error="the check is turned off")
    t = now()
    release = _read_cache(t) if use_cache else None
    if release is None:
        try:
            release = latest_release(opener, timeout)
        except UpgradeError as exc:
            return Status(current=current, error=str(exc))
        _write_cache(release, t)
    return Status(current=current, latest=release.version,
                  available=newer(release.version, current), release=release)


# ---------------------------------------------------------------------------
# installing
# ---------------------------------------------------------------------------

def _out() -> Any:
    from .cli import install as inst

    return inst


def install_kind() -> str:
    return _out().install_kind()


def _download(url: str, dest: Path, opener: Opener, progress: Progress | None) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=DOWNLOAD_TIMEOUT) as response, open(dest, "wb") as fh:
            total = int(response.headers.get("Content-Length") or 0) if getattr(response, "headers", None) else 0
            done = 0
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                done += len(chunk)
                if done > MAX_DOWNLOAD:
                    raise UpgradeError(f"{url} is larger than {MAX_DOWNLOAD // (1024 * 1024)} MB; refused")
                fh.write(chunk)
                if progress is not None:
                    progress(done, total)
    except urllib.error.HTTPError as exc:
        raise UpgradeError(f"{url} answered HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", None) or exc
        raise UpgradeError(f"{url} could not be downloaded ({reason})") from exc


def expected_sha256(sums: str, name: str) -> str:
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    return ""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class _Fetcher:
    """Downloads release assets into one temporary directory, each checked."""

    def __init__(self, release: Release, opener: Opener, progress: Progress | None) -> None:
        self.release, self.opener, self.progress = release, opener, progress
        self.dir = Path(tempfile.mkdtemp(prefix="pcbridge-upgrade-"))
        self._sums: str | None = None

    def url(self, name: str) -> str:
        return self.release.assets.get(name) or f"{RELEASES}/download/{self.release.tag}/{name}"

    def sums(self) -> str:
        if self._sums is None:
            path = self.dir / "sha256sums.txt"
            _download(self.url("sha256sums.txt"), path, self.opener, None)
            self._sums = path.read_text(encoding="utf-8", errors="replace")
        return self._sums

    def names(self) -> list[str]:
        return [line.split()[1].lstrip("*") for line in self.sums().splitlines() if len(line.split()) == 2]

    def get(self, name: str, *, show: bool = False) -> Path:
        want = expected_sha256(self.sums(), name)
        if not want:
            raise UpgradeError(f"{name} is not listed in the release's sha256sums.txt; refused")
        path = self.dir / name
        _download(self.url(name), path, self.opener, self.progress if show else None)
        if _sha256(path) != want:
            raise UpgradeError(f"{name} does not match its sha256 in sha256sums.txt; nothing was installed")
        return path

    def close(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


def _arch() -> str:
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "x86_64"
    raise UpgradeError(f"there is no release build for {platform.machine()} yet; "
                       "upgrade a git checkout with `git pull` instead")


def os_release(path: str = "/etc/os-release") -> dict[str, str]:
    out = {}
    try:
        for line in open(path, encoding="utf-8"):
            if "=" in line:
                k, v = line.rstrip("\n").split("=", 1)
                out[k] = v.strip().strip('"')
    except OSError:
        pass
    return out


# Ubuntu releases by code name, for derivatives (Zorin, Mint, Pop!_OS) whose
# ID is their own; packages are built per Ubuntu or Debian release.
UBUNTU_CODENAMES = {"noble": "ubuntu24.04", "resolute": "ubuntu26.04"}


def deb_flavors(info: dict[str, str] | None = None) -> list[str]:
    """The package flavors that fit this system, best first ("ubuntu24.04")."""
    info = os_release() if info is None else info
    out = []
    if info.get("ID") and info.get("VERSION_ID"):
        out.append(f"{info['ID']}{info['VERSION_ID']}")
    code = info.get("UBUNTU_CODENAME", "")
    if code in UBUNTU_CODENAMES:
        out.append(UBUNTU_CODENAMES[code])
    return out


def pick_deb(names: list[str], flavors: list[str]) -> str:
    for flavor in flavors:
        for name in names:
            if re.fullmatch(rf"pcbridge_[^_]+_{re.escape(flavor)}_amd64\.deb", name):
                return name
    return ""


def pick_pkg(names: list[str]) -> str:
    for name in names:
        if re.fullmatch(r"pcbridge-[^-]+-\d+-x86_64\.pkg\.tar\.zst", name):
            return name
    return ""


def pick_wheel(names: list[str], arch: str) -> str:
    for name in names:
        if re.fullmatch(rf"pcbridge-[0-9][^-]*-py3-none-linux_{re.escape(arch)}\.whl", name):
            return name
    return ""


def short_home(path: str | Path) -> str:
    text, home = str(path), os.path.expanduser("~")
    return "~" + text[len(home):] if home and text.startswith(home + "/") else text


def install(release: Release, *, kind: str | None = None, opener: Opener = urllib.request.urlopen,
            progress: Progress | None = None) -> str:
    """Put `release` on disk for this kind of installation; one line saying what was done.

    Lines go through `cli.install` (printed, or to the terminal UI's sink);
    sudo, for a package, through `plan.run_sudo` (the terminal UI hands the
    terminal back for the password). Raises `UpgradeError`.
    """
    inst = _out()
    kind = kind or inst.install_kind()
    if kind == "git":
        root = inst.repo_root()
        res = inst.run(["git", "-C", str(root), "pull", "--ff-only"], timeout=120)
        if res.returncode != 0:
            raise UpgradeError("git pull --ff-only failed: " + (res.stderr or res.stdout).strip()[:300])
        last = (res.stdout.strip().splitlines() or ["done"])[-1]
        inst.ok(f"git pull --ff-only: {last}")
        return f"pulled {short_home(root)}"
    if kind == "pip":
        raise UpgradeError("this pcbridge was installed with pip outside setup's venv; install the "
                           f"{release.version} wheel the same way (from {release.url})")
    fetch = _Fetcher(release, opener, progress)
    try:
        if kind == "user":
            return _install_user(release, fetch, inst)
        if kind == "deb":
            name = pick_deb(fetch.names(), deb_flavors())
            if not name:
                raise UpgradeError(f"release {release.tag} has no .deb for this system "
                                   f"({', '.join(deb_flavors()) or 'unknown'}); download one from {release.url}")
            return _install_package(fetch, name, ["sudo", "apt-get", "install", "-y", "--allow-downgrades"], inst)
        if kind == "pacman":
            name = pick_pkg(fetch.names())
            if not name:
                raise UpgradeError(f"release {release.tag} has no Arch package; download it from {release.url}")
            return _install_package(fetch, name, ["sudo", "pacman", "-U", "--noconfirm"], inst)
        raise UpgradeError(f"unknown kind of installation: {kind}")
    finally:
        fetch.close()


def _install_user(release: Release, fetch: _Fetcher, inst: Any) -> str:
    names = fetch.names()
    wheel_name = pick_wheel(names, _arch())
    if not wheel_name:
        raise UpgradeError(f"release {release.tag} has no wheel for {_arch()}")
    wheel = fetch.get(wheel_name, show=True)
    inst.ok(f"downloaded {wheel_name} and checked it against sha256sums.txt")
    venv = Path(sys.prefix)
    python = venv / "bin" / "python"
    data = pathslib.data_home()
    constraints: Path | None = None
    if "constraints.txt" in names:
        got = fetch.get("constraints.txt")
        data.mkdir(parents=True, exist_ok=True)
        constraints = data / "constraints.txt"
        shutil.copyfile(got, constraints)
    c = ["-c", str(constraints)] if constraints else []
    pip = [str(python), "-m", "pip", "install", "-q", "--disable-pip-version-check", *c]
    if inst.run_streamed([*pip, "--force-reinstall", "--no-deps", str(wheel)], timeout=600) != 0:
        raise UpgradeError("pip could not install the new wheel (its output is above); "
                           "`pcbridge doctor` shows what is installed now")
    # Its dependencies, with the desktop extra (Pillow) where it installs.
    if inst.run_streamed([*pip, f"{wheel}[desktop]"], timeout=600) != 0:
        if inst.run_streamed([*pip, str(wheel)], timeout=600) != 0:
            raise UpgradeError("pip could not install the new version's dependencies; run `pcbridge doctor`")
        inst.warn("the desktop extra (Pillow) was not installed; only desktop control uses it")
    inst.ok(f"installed into {short_home(venv)}")
    return f"installed {release.version} into {short_home(venv)}"


def _install_package(fetch: _Fetcher, name: str, argv: list[str], inst: Any) -> str:
    from .cli import plan as planlib

    path = fetch.get(name, show=True)
    inst.ok(f"downloaded {name} and checked it against sha256sums.txt")
    # apt reads a local file as the unprivileged _apt user: a readable copy.
    os.chmod(fetch.dir, 0o755)
    os.chmod(path, 0o644)
    if not planlib.run_sudo([*argv, str(path)]):
        raise UpgradeError(f"installing {name} failed; nothing else was changed")
    inst.ok(f"installed {name}")
    return f"installed {name}"


# ---------------------------------------------------------------------------
# finishing: the new version's setup, then the daemon
# ---------------------------------------------------------------------------

def launcher() -> str:
    """The `pcbridge` that runs the code installed now."""
    inst = _out()
    path = inst.launcher_path()
    if Path(path).exists():
        return str(path)
    return shutil.which("pcbridge") or str(Path(sys.prefix) / "bin" / "pcbridge")


def finish_argv(idle_wait: float) -> list[str]:
    return [launcher(), "setup", "--yes", "--no-packages", "--no-connect", "--no-aliases",
            "--idle-wait", str(int(idle_wait))]


def finish(idle_wait: float = 0, line: Callable[[str], None] | None = None,
           run: Callable[..., Any] = subprocess.Popen) -> tuple[bool, str]:
    """Run the new version's setup; (it worked, the line about the daemon).

    `idle_wait` 0: when a job runs or the grant is open the daemon is not
    restarted now; it notices the new install and restarts itself once idle.
    Every line of setup's output goes to `line` and to the upgrade log.
    """
    env = dict(os.environ, PCBRIDGE_SETUP_PLAIN="1", NO_COLOR="1")
    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    daemon = ""
    try:
        proc = run(finish_argv(idle_wait), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                   stderr=subprocess.STDOUT, text=True, errors="replace", env=env, cwd=str(Path.home()))
    except OSError as exc:
        return False, f"the new version's setup did not start: {exc}"
    with open(log, "w", encoding="utf-8") as fh, proc.stdout:
        for raw in proc.stdout:
            text = raw.rstrip()
            fh.write(text + "\n")
            if not text:
                continue
            body = text.strip()
            for mark in ("ok ", "warn ", "fail "):
                if body.startswith(mark):
                    body = body[len(mark):].strip()
            if body.startswith("daemon ") or "deferred:" in body or body.startswith("restart"):
                daemon = body
            if line is not None:
                line(body)
    code = proc.wait()
    if code != 0 and not daemon:
        return False, f"the new version's setup ended with status {code}; see {short_home(log)}"
    return True, daemon or "setup finished"


def log_path() -> Path:
    return pathslib.state_home() / "upgrade.log"


# The terminal UI that restarts itself leaves this note for the new one,
# which then says "upgraded to X" once.
def marker_path() -> Path:
    return pathslib.state_home() / "upgraded.json"


def write_marker(old: str, new: str, url: str) -> None:
    try:
        path = marker_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"from": old, "to": new, "url": url, "at": time.time()}), encoding="utf-8")
    except OSError:
        pass


def take_marker(current: str = __version__, now: Callable[[], float] = time.time) -> dict | None:
    """The note from an upgrade into `current` in the last ten minutes, removed once read."""
    path = marker_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        path.unlink()
    except OSError:
        pass
    if not isinstance(data, dict) or data.get("to") != current or now() - float(data.get("at", 0)) > 600:
        return None
    return data


__all__ = ["CHECK_TIMEOUT", "Release", "Status", "UpgradeError", "check", "deb_flavors", "finish",
           "install", "latest_release", "newer", "parse_version", "pick_deb", "pick_pkg", "pick_wheel",
           "take_marker", "write_marker"]
