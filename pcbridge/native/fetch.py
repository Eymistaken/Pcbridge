"""Fetch the release's prebuilt native helper into an installation without one.

WHY
    Desktop control needs `pcbridge-native`. The wheel, the `.deb` and the
    Arch package carry it, and a git checkout builds it with Rust
    (`scripts/build-native.sh`), but a checkout on a machine without a Rust
    toolchain had no way to get it. Every release therefore publishes the
    helper as an asset of its own, `pcbridge-native-<target>`, listed in the
    release's `sha256sums.txt`, and `pcbridge setup` and `pcbridge doctor
    --fix` put it where discovery looks (`pcbridge/_native/<target>/`).

WHAT IS CHECKED, IN THIS ORDER
    1. The release: this version's tag first, then the latest release, so a
       checkout newer than every release takes the newest helper.
    2. The SHA-256, against the same release's `sha256sums.txt`.
    3. `--build-info`: the file is a pcbridge-native release build for this
       machine's target.
    4. The glibc it needs is not newer than this system's.
    5. A real handshake in a throwaway state directory, so a helper that
       speaks another protocol version than this checkout is refused here,
       not at the first desktop call.
    Only then is it moved into place, atomically. A failed step leaves
    nothing behind and says what to do instead.

INSTALL TIME ONLY
    The daemon never downloads, builds or searches for the helper
    (docs/native/protocol-v1.md). This module runs from setup and doctor,
    when the user is there to see what it does.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .discovery import _target_triple, glibc_needed

REPO = "Eymistaken/Pcbridge"
RELEASES = f"https://github.com/{REPO}/releases"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
# The helper is about 7 MB; a much larger download is not the helper.
MAX_BYTES = 64 * 1024 * 1024
DOWNLOAD_TIMEOUT = 60.0
SMALL_TIMEOUT = 15.0
# GitHub's API refuses requests without a User-Agent. A generic one: nothing
# about the user or the machine goes into the request.
USER_AGENT = "pcbridge-setup"

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

Opener = Callable[..., Any]
Runner = Callable[..., subprocess.CompletedProcess]


class FetchError(RuntimeError):
    """The helper was not installed; the message says why and what to do."""


@dataclass(frozen=True)
class Fetched:
    path: Path
    tag: str
    sha256: str
    build_id: str
    size: int


def asset_name(target: str) -> str:
    """The release asset that carries the helper for `target`."""
    return f"pcbridge-native-{target}"


def packaged_path(target: str, package_root: Path | None = None) -> Path:
    """Where discovery looks for the packaged helper (`discovery.py`)."""
    return (package_root or PACKAGE_ROOT) / "_native" / target / "pcbridge-native"


def remedy(kind: str | None = None, *, has_cargo: bool | None = None) -> str:
    """How this installation gets a native helper, as one instruction.

    `kind` is `install.install_kind()`; `has_cargo` whether a Rust toolchain
    is on PATH. Both are looked up when not given.
    """
    if _target_triple() is None:
        import platform

        return (f"no native helper is built for {platform.machine() or 'this architecture'} "
                "(release builds are x86_64 only), so desktop control is not available here")
    if kind is None:
        from ..cli.install import install_kind

        kind = install_kind()
    if kind in ("deb", "pacman"):
        return "reinstall the pcbridge package: it carries the native helper"
    if has_cargo is None:
        has_cargo = shutil.which("cargo") is not None
    if kind == "git" and has_cargo:
        return ("build it with scripts/build-native.sh, or let `pcbridge doctor --fix` "
                "download the release's prebuilt helper")
    return "`pcbridge doctor --fix` downloads the release's prebuilt helper"


def _get(url: str, opener: Opener, *, limit: int, timeout: float) -> bytes | None:
    """The body at `url`, or None for a 404. Any other failure raises."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=timeout) as response:
            data = response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise FetchError(f"{url} answered HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", None) or exc
        raise FetchError(f"{url} could not be read: {reason}") from exc
    if len(data) > limit:
        raise FetchError(f"{url} is larger than {limit // (1024 * 1024)} MB; refused")
    return bytes(data)


def latest_tag(opener: Opener = urllib.request.urlopen, *, api: str = LATEST_API) -> str:
    """The latest published release's tag, "" when it cannot be read."""
    try:
        body = _get(api, opener, limit=1024 * 1024, timeout=SMALL_TIMEOUT)
    except FetchError:
        return ""
    if body is None:
        return ""
    try:
        tag = json.loads(body).get("tag_name", "")
    except (ValueError, AttributeError):
        return ""
    return tag if isinstance(tag, str) else ""


def expected_sha256(sums: bytes, name: str) -> str:
    """The checksum `sha256sum` wrote for `name`, "" when it is not listed."""
    for line in sums.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if (len(parts) == 2 and parts[1].lstrip("*") == name
                and len(parts[0]) == 64 and all(c in "0123456789abcdefABCDEF" for c in parts[0])):
            return parts[0].lower()
    return ""


def _build_info(path: Path, run: Runner) -> dict[str, Any]:
    try:
        proc = run([str(path), "--build-info"], capture_output=True, text=True,
                   timeout=10, check=False, cwd="/")
    except (OSError, subprocess.SubprocessError) as exc:
        raise FetchError(f"the downloaded helper does not run on this machine: {exc}") from exc
    if proc.returncode != 0:
        raise FetchError("the downloaded helper does not run on this machine: "
                         f"--build-info exited {proc.returncode} {(proc.stderr or '').strip()[:160]}")
    try:
        info = json.loads(proc.stdout)
    except ValueError as exc:
        raise FetchError("the downloaded helper gave no build information") from exc
    if not isinstance(info, dict):
        raise FetchError("the downloaded helper gave no build information")
    return info


def _default_handshake(path: Path) -> Any:
    from .diagnostics import handshake_capabilities

    return handshake_capabilities(path)


def fetch(
    *,
    version: str,
    target: str | None = None,
    package_root: Path | None = None,
    opener: Opener = urllib.request.urlopen,
    run: Runner = subprocess.run,
    handshake: Callable[[Path], Any] | None = None,
    glibc: tuple[int, ...] | None = None,
    releases: str = RELEASES,
    latest: Callable[[], str] | None = None,
) -> Fetched:
    """Download, check and install the helper. Raises FetchError with the reason.

    `version` is this pcbridge's version; its release is tried first. The
    keyword arguments other than `version` exist for the tests.
    """
    target = target or _target_triple()
    if target is None:
        raise FetchError(remedy())
    name = asset_name(target)
    own = f"v{version}"

    def checksum_in(tag: str) -> str:
        sums = _get(f"{releases}/download/{tag}/sha256sums.txt", opener,
                    limit=1024 * 1024, timeout=SMALL_TIMEOUT)
        return expected_sha256(sums, name) if sums is not None else ""

    tag, want = own, checksum_in(own)
    tried = [own]
    if not want:
        newest = latest() if latest is not None else latest_tag(opener)
        if newest and newest != own:
            tried.append(newest)
            tag, want = newest, checksum_in(newest)
    if not want:
        raise FetchError(
            f"no pcbridge release ({', '.join(tried)}) publishes {name} yet; "
            "install Rust and run scripts/build-native.sh instead")

    data = _get(f"{releases}/download/{tag}/{name}", opener,
                limit=MAX_BYTES, timeout=DOWNLOAD_TIMEOUT)
    if data is None:
        raise FetchError(f"release {tag} lists {name} in sha256sums.txt but does not serve it")
    digest = hashlib.sha256(data).hexdigest()
    if digest != want:
        raise FetchError(f"{name} from release {tag} does not match its sha256sums.txt "
                         f"(got {digest[:12]}, expected {want[:12]}); nothing was installed")

    dest = packaged_path(target, package_root)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".pcbridge-native.", dir=dest.parent)
    except OSError as exc:
        raise FetchError(f"cannot write next to {dest}: {exc}") from exc
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        tmp.chmod(0o755)
        info = _build_info(tmp, run)
        if info.get("name") != "pcbridge-native" or info.get("target") != target:
            raise FetchError(f"{name} from release {tag} describes itself as "
                             f"{info.get('name')!r} for {info.get('target')!r}, not this machine's "
                             f"{target}; nothing was installed")
        if info.get("test_harness") is not False or info.get("profile") != "release":
            raise FetchError(f"{name} from release {tag} is not a release build; nothing was installed")
        if glibc is None:
            from ..platforminfo import glibc_version

            glibc = glibc_version()
        need = glibc_needed(tmp)
        if glibc and need != (0, 0) and tuple(glibc[:2]) < need:
            raise FetchError(f"the release helper needs glibc {need[0]}.{need[1]} and this system "
                             f"has {glibc[0]}.{glibc[1]}; nothing was installed")
        checked = (handshake or _default_handshake)(tmp)
        if isinstance(checked, str):
            raise FetchError(f"the release {tag} helper failed its handshake with this pcbridge "
                             f"({checked}); build it with scripts/build-native.sh instead")
        os.replace(tmp, dest)
    except FetchError:
        tmp.unlink(missing_ok=True)
        raise
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise FetchError(f"cannot install {dest}: {exc}") from exc
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    # The same record scripts/build-native.sh leaves next to its build.
    try:
        (dest.parent / "build-info.json").write_text(
            json.dumps(info, separators=(",", ":")) + "\n", encoding="utf-8")
    except OSError:
        pass  # discovery and doctor read the binary itself, not this file
    return Fetched(dest, tag, digest, str(info.get("build_id") or ""), len(data))


__all__ = [
    "FetchError",
    "Fetched",
    "asset_name",
    "expected_sha256",
    "fetch",
    "latest_tag",
    "packaged_path",
    "remedy",
]
