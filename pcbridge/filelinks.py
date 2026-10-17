"""One-time links that move a whole file between a phone and this machine.

WHY
    Measured through claude.ai (2026-10-17): base64 chunks copied by the
    model stop being reliable after a few tens of kilobytes. It changed one
    character within 75 KB, and the client cut an upload off. So
    `fs_download_link` and `fs_upload_link` return a link instead, and the
    bytes go between the phone's browser and the daemon over the tunnel,
    never through the model's context.

SECURITY (docs/security.md, "File links")
    The link's token is the authorization, as for `/shot/<token>.png`: no
    OAuth, so a phone browser can open it. Hence:

    * 256 random bits, kept in memory only and keyed by their SHA-256. A
      daemon restart kills every link.
    * Bound at creation to one resolved path and one direction. Nothing in
      the request can change the path.
    * Single use: ready -> busy -> spent. Only a COMPLETE transfer spends a
      link. A transfer cut off by the network puts it back to ready until it
      expires; one refused for a limit spends it. One transfer at a time.
    * `[files] link_ttl_seconds` (5 minutes) is the window to start.
    * Unknown, expired, used and busy links get the same 404.
    * A download is always `application/octet-stream`, `attachment` and
      sandboxed: an HTML or SVG file must never run script on this origin,
      which also serves the OAuth consent page.

    The pages before the transfer (`GET /dl/<token>`, `GET /ul/<token>`) do
    not spend the link: `shots.py` records that single-use shot links were
    burned by phone refreshes, back buttons and link previews. A preview
    fetches the URL it was given, not the links inside the page.
"""

from __future__ import annotations

import errno
import glob
import hashlib
import html
import logging
import os
import re
import secrets
import shutil
import stat as statlib
import subprocess
import tempfile
import threading
import time
from base64 import b64encode
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import anyio
from starlette.requests import ClientDisconnect, Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from . import files as fileslib

log = logging.getLogger("pcbridge.filelinks")

TOKEN_BYTES = 32  # secrets.token_urlsafe(32) -> 256 bits, 43 characters
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")
MAX_LIVE = 16
# A used or expired link is remembered this long, only so a second attempt
# can be audited once; after that it is as unknown as a guess.
REMEMBER_SECONDS = 3600
# An upload must leave this much free space behind it.
FREE_MARGIN = 256 << 20
_CHUNK = 1 << 20
TEMP_SUFFIX = ".pcbridge-link"

DOWNLOAD, UPLOAD = "download", "upload"
READY, BUSY, SPENT = "ready", "busy", "spent"

COMMON_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Robots-Tag": "noindex, nofollow",
    "X-Frame-Options": "DENY",
}


class LinkError(Exception):
    """A link that cannot be made; the message is for the caller."""


def token_key(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", "replace")).hexdigest()


def _stamp(st: os.stat_result) -> tuple[int, int, int]:
    return st.st_size, st.st_mtime_ns, st.st_ino


@dataclass
class Link:
    key: str
    direction: str
    path: Path
    created: float
    expires_at: float
    state: str = READY
    # download: what the file was when the link was made
    size: int = 0
    sha256: str = ""
    stamp: tuple[int, int, int] = (0, 0, 0)
    # upload
    overwrite: bool = False
    expected: str | None = None
    max_bytes: int = 0
    refused: set[str] = field(default_factory=set)

    @property
    def id(self) -> str:
        """Joins the audit lines of one link; useless as a link."""
        return self.key[:8]


def _mib(n: int) -> str:
    return f"{n / (1 << 20):,.1f} MB"


class LinkStore:
    """The live links, shared by the two tools and the HTTP routes."""

    def __init__(self, cfg: Any, audit: Callable[..., None] | None = None) -> None:
        self.cfg = cfg
        self.audit: Callable[..., None] = audit or (lambda event, **fields: None)
        self._links: dict[str, Link] = {}
        self._lock = threading.Lock()

    @property
    def ttl(self) -> int:
        return int(self.cfg.link_ttl_seconds)

    def url(self, link: Link, token: str) -> str:
        prefix = "dl" if link.direction == DOWNLOAD else "ul"
        return f"{self.cfg.public_url.rstrip('/')}/{prefix}/{token}"

    # ------------------------------------------------------------ creating
    def _sweep(self, now: float) -> None:
        for key in [k for k, l in self._links.items() if l.expires_at + REMEMBER_SECONDS < now]:
            self._links.pop(key, None)

    def live(self) -> int:
        now = time.time()
        with self._lock:
            return sum(1 for l in self._links.values()
                       if l.state == BUSY or (l.state == READY and l.expires_at > now))

    def _add(self, **fields: Any) -> tuple[Link, str]:
        now = time.time()
        with self._lock:
            self._sweep(now)
            live = sum(1 for l in self._links.values()
                       if l.state == BUSY or (l.state == READY and l.expires_at > now))
            if live >= MAX_LIVE:
                raise LinkError(f"{live} links are open already (the limit is {MAX_LIVE}); "
                                "use or let some expire first.")
            token = secrets.token_urlsafe(TOKEN_BYTES)
            link = Link(key=token_key(token), created=now, expires_at=now + self.ttl, **fields)
            self._links[link.key] = link
        return link, token

    def create_download(self, path: Path) -> tuple[Link, str]:
        limit = int(self.cfg.download_max_mb) << 20
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            raise LinkError(f"File not found: {path}") from None
        except OSError as exc:
            raise LinkError(f"Could not open `{path}`: {exc.strerror or exc}") from None
        st = os.fstat(fd)
        if not _is_regular(st):
            os.close(fd)
            raise LinkError(f"`{path}` is not a regular file.")
        with os.fdopen(fd, "rb") as fh:
            if st.st_size > limit:
                raise LinkError(
                    f"`{path}` is {_mib(st.st_size)}, above the download limit of "
                    f"{_mib(limit)} ([files] download_max_mb).")
            h = hashlib.sha256()
            while block := fh.read(_CHUNK):
                h.update(block)
            if _stamp(os.fstat(fh.fileno())) != _stamp(st):
                raise LinkError(f"`{path}` changed while it was being read; try again.")
        return self._add(direction=DOWNLOAD, path=path, size=st.st_size,
                         sha256=h.hexdigest(), stamp=_stamp(st))

    def create_upload(self, path: Path, *, overwrite: bool = False,
                      expected: str | None = None) -> tuple[Link, str]:
        want = None
        if expected is not None:
            want = expected.strip().lower()
            if len(want) != 64 or not set(want) <= set("0123456789abcdef"):
                raise LinkError("expected_sha256 must be a SHA-256 of 64 hex digits.")
        parent = path.parent
        if not parent.is_dir():
            raise LinkError(f"The directory `{parent}` does not exist; nothing is created.")
        if not os.access(parent, os.W_OK | os.X_OK):
            raise LinkError(f"The directory `{parent}` is not writable.")
        if path.is_dir():
            raise LinkError(f"`{path}` is a directory; give the path of the file to create.")
        if os.path.lexists(path):
            if not path.is_file():
                raise LinkError(f"`{path}` exists and is not a regular file.")
            if not overwrite:
                raise LinkError(f"`{path}` already exists; pass overwrite=true to replace it.")
        _sweep_temp(path)
        return self._add(direction=UPLOAD, path=path, overwrite=overwrite, expected=want,
                         max_bytes=int(self.cfg.upload_max_mb) << 20)

    # ------------------------------------------------------------- serving
    def _find(self, token: str, direction: str, claim: bool) -> Link | None:
        if not _TOKEN_RE.fullmatch(token or ""):
            return None
        now = time.time()
        refused = None
        with self._lock:
            link = self._links.get(token_key(token))
            if link is None or link.direction != direction:
                return None
            if link.state == SPENT:
                refused = "used"
            elif link.state == READY and link.expires_at <= now:
                refused = "expired"
            elif link.state == BUSY:
                return None
            else:
                if claim:
                    link.state = BUSY
                return link
            first = refused not in link.refused
            link.refused.add(refused)
        if first:
            self.audit("fs_link_refused", direction=direction, path=str(link.path)[:200],
                       link=link.id, reason=refused)
        return None

    def lookup(self, token: str, direction: str) -> Link | None:
        """The link if it can still be used; the state does not change."""
        return self._find(token, direction, claim=False)

    def claim(self, token: str, direction: str) -> Link | None:
        """The link, now busy, if it can be used; None otherwise."""
        return self._find(token, direction, claim=True)

    def release(self, link: Link) -> None:
        """A transfer broke off: usable again until it expires."""
        with self._lock:
            if link.state == BUSY:
                link.state = READY

    def spend(self, link: Link) -> None:
        with self._lock:
            link.state = SPENT


def _is_regular(st: os.stat_result) -> bool:
    return statlib.S_ISREG(st.st_mode)


def _sweep_temp(path: Path, older_than: float = 86400) -> None:
    """Remove this target's upload leftovers from a daemon that died mid-upload."""
    cutoff = time.time() - older_than
    try:
        for f in path.parent.glob(f".{glob.escape(path.name)}.*{TEMP_SUFFIX}"):
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                continue
    except OSError:
        pass


# ------------------------------------------------------------------ helpers


def tunnel_open(port: int) -> bool | None:
    """Whether Tailscale Funnel serves this port; None when it cannot be told."""
    if not shutil.which("tailscale"):
        return None
    try:
        out = subprocess.run(["tailscale", "funnel", "status"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return f":{port}" in out


class RedactLinkTokens(logging.Filter):
    """Hide link tokens from uvicorn's access log.

    The access log goes to the journal with every request path (checked:
    each `POST /mcp` is there with the client address), so it would hold
    live tokens for anyone who can read the journal.
    """

    _PATH = re.compile(r"^/(dl|ul|browser)/[^/?\s]+")

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path = self._PATH.sub(r"/\1/<link>", args[2])
            if path != args[2]:
                record.args = (*args[:2], path, *args[3:])
        return True


def install_log_filter() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactLinkTokens) for f in access.filters):
        access.addFilter(RedactLinkTokens())


def not_found() -> Response:
    """The one answer for unknown, expired, used and busy links."""
    return PlainTextResponse("not found", status_code=404, headers=COMMON_HEADERS)


def _remote(request: Request) -> str | None:
    return request.client.host if request.client else None


def disposition(name: str) -> str:
    plain = "".join(c if 32 <= ord(c) < 127 and c not in '"\\;' else "_" for c in name) or "file"
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(name, safe='')}"


def _left(link: Link) -> str:
    secs = max(0, int(link.expires_at - time.time()))
    return f"{secs // 60} min {secs % 60:02d} s" if secs >= 60 else f"{secs} s"


# -------------------------------------------------------------------- pages

_STYLE = """\
body{font:16px/1.5 system-ui,sans-serif;margin:0;padding:24px 16px;background:#f6f6f4;color:#1d1d1b}
main{max-width:34rem;margin:0 auto}
h1{font-size:1.25rem;margin:0 0 1rem}
.name{font-size:1.1rem;font-weight:600;word-break:break-all}
code{font-size:.8rem;word-break:break-all}
.button,button{display:inline-block;margin:1rem 0;padding:.8rem 1.4rem;border:0;border-radius:8px;
background:#1d4ed8;color:#fff;font:inherit;font-weight:600;text-decoration:none}
button:disabled{background:#9ca3af}
progress{width:100%}
.note{color:#555;font-size:.9rem}
.ok{color:#15803d}.bad{color:#b91c1c}
#out{word-break:break-all}
@media (prefers-color-scheme:dark){body{background:#161615;color:#ececea}.note{color:#aaa}
.ok{color:#4ade80}.bad{color:#f87171}}
"""

_UPLOAD_SCRIPT = """\
"use strict";
const form = document.getElementById("f");
const input = document.getElementById("file");
const out = document.getElementById("out");
const bar = document.getElementById("bar");
const button = form.querySelector("button");
const max = Number(document.body.dataset.max);
function show(text, cls) { out.textContent = text; out.className = cls || ""; }
async function localHash(file) {
  if (!(window.crypto && crypto.subtle) || file.size > 200 * 1048576) return null;
  const d = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(d), b => b.toString(16).padStart(2, "0")).join("");
}
function send(file) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("PUT", location.pathname);
    x.upload.onprogress = e => { if (e.lengthComputable) bar.value = e.loaded / e.total; };
    x.onload = () => resolve(x);
    x.onerror = () => reject(new Error("The connection broke off."));
    x.send(file);
  });
}
form.addEventListener("submit", async ev => {
  ev.preventDefault();
  const file = input.files[0];
  if (!file) { show("Choose a file first.", "bad"); return; }
  if (file.size > max) { show("The file is " + file.size + " bytes; the limit is " + max + ".", "bad"); return; }
  button.disabled = true;
  show("Uploading " + file.size + " bytes...");
  try {
    const [x, mine] = await Promise.all([send(file), localHash(file)]);
    let r = {};
    try { r = JSON.parse(x.responseText); } catch (e) { r = {}; }
    if (x.status !== 200) {
      show(r.error || ("Refused (HTTP " + x.status + ")."), "bad");
      button.disabled = !r.retry;
      return;
    }
    const same = mine === null ? "Compare it with the file's SHA-256 on this device."
      : (mine === r.sha256 ? "It matches the file on this device." : "It does NOT match the file on this device.");
    show("Stored " + r.bytes + " bytes. SHA-256 " + r.sha256 + ". " + same,
      mine === null || mine === r.sha256 ? "ok" : "bad");
  } catch (e) {
    show(e.message + " Try again while the link is still valid.", "bad");
    button.disabled = false;
  }
});
"""


def _csp_hash(text: str) -> str:
    return "'sha256-" + b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


_PAGE_CSP = (
    f"default-src 'none'; style-src {_csp_hash(_STYLE)}; script-src {_csp_hash(_UPLOAD_SCRIPT)}; "
    "connect-src 'self'; img-src 'none'; form-action 'none'; frame-ancestors 'none'; "
    "base-uri 'none'"
)


def _page(title: str, body: str, data_max: int | None = None) -> Response:
    attr = f' data-max="{data_max}"' if data_max is not None else ""
    script = f"<script>{_UPLOAD_SCRIPT}</script>" if data_max is not None else ""
    doc = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex">'
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body{attr}><main>{body}</main>{script}</body></html>"
    )
    return HTMLResponse(doc, headers={**COMMON_HEADERS, "Content-Security-Policy": _PAGE_CSP})


# ------------------------------------------------------------------- routes


async def download_page(store: LinkStore, request: Request) -> Response:
    token = request.path_params.get("token", "")
    link = store.lookup(token, DOWNLOAD)
    if link is None:
        return not_found()
    name = link.path.name
    href = f"/dl/{token}/{quote(name, safe='')}"
    body = (
        "<h1>Download from pcbridge</h1>"
        f'<p class="name">{html.escape(name)}</p>'
        f"<p>{link.size:,} bytes · valid for {_left(link)} more · one download</p>"
        f"<p>SHA-256<br><code>{link.sha256}</code></p>"
        f'<a class="button" href="{html.escape(href)}" download>Download</a>'
        '<p class="note">The link works for one complete download. If the download '
        "breaks off, open this page again and tap Download while the link is still valid.</p>"
    )
    return _page("pcbridge download", body)


def _file_headers(link: Link) -> dict[str, str]:
    return {
        **COMMON_HEADERS,
        "Content-Type": "application/octet-stream",
        "Content-Length": str(link.size),
        "Content-Disposition": disposition(link.path.name),
        "Content-Security-Policy": "sandbox; default-src 'none'",
    }


async def download_file(store: LinkStore, request: Request) -> Response:
    token = request.path_params.get("token", "")
    if request.method == "HEAD":
        link = store.lookup(token, DOWNLOAD)
        return not_found() if link is None else Response(b"", headers=_file_headers(link))
    link = store.claim(token, DOWNLOAD)
    if link is None:
        return not_found()
    try:
        fd = os.open(link.path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        store.spend(link)
        store.audit("fs_link_failed", direction=DOWNLOAD, path=str(link.path)[:200],
                    link=link.id, reason="gone", error=exc.strerror)
        return PlainTextResponse("The file is gone; ask for a new link.", status_code=410,
                                 headers=COMMON_HEADERS)
    st = os.fstat(fd)
    if not _is_regular(st) or _stamp(st) != link.stamp:
        os.close(fd)
        store.spend(link)
        store.audit("fs_link_failed", direction=DOWNLOAD, path=str(link.path)[:200],
                    link=link.id, reason="changed")
        return PlainTextResponse("The file changed after the link was made; ask for a new "
                                 "link.", status_code=409, headers=COMMON_HEADERS)
    return _FileStream(store, link, os.fdopen(fd, "rb"), _remote(request))


class _FileStream(Response):
    """Streams the file; spends the link only once the last byte was sent."""

    def __init__(self, store: LinkStore, link: Link, fh: Any, remote: str | None) -> None:
        self.store, self.link, self.fh, self.remote = store, link, fh, remote
        self.status_code = 200
        self.background = None
        self.init_headers(_file_headers(link))

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        link, store = self.link, self.store
        started = time.monotonic()
        h = hashlib.sha256()
        sent = 0
        done = False
        try:
            async with anyio.create_task_group() as tg:
                async def watch() -> None:
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            tg.cancel_scope.cancel()
                            return

                tg.start_soon(watch)
                await send({"type": "http.response.start", "status": 200,
                            "headers": self.raw_headers})
                while sent < link.size:
                    block = await anyio.to_thread.run_sync(
                        self.fh.read, min(_CHUNK, link.size - sent))
                    if not block:
                        raise OSError("the file got shorter while it was being sent")
                    h.update(block)
                    sent += len(block)
                    await send({"type": "http.response.body", "body": block, "more_body": True})
                await send({"type": "http.response.body", "body": b"", "more_body": False})
                done = True
                tg.cancel_scope.cancel()
        except Exception as exc:  # noqa: BLE001 - a broken connection, in any of its forms
            log.info("download %s broke off after %d bytes: %s", link.id, sent, exc)
        finally:
            self.fh.close()
            if done:
                store.spend(link)
                got = h.hexdigest()
                store.audit("fs_link_used", direction=DOWNLOAD, path=str(link.path)[:200],
                            link=link.id, bytes=sent, sha256=got,
                            changed="yes" if got != link.sha256 else None,
                            seconds=round(time.monotonic() - started, 1), remote=self.remote)
            else:
                store.release(link)
                store.audit("fs_link_failed", direction=DOWNLOAD, path=str(link.path)[:200],
                            link=link.id, reason="disconnected", bytes=sent)


async def upload_page(store: LinkStore, request: Request) -> Response:
    token = request.path_params.get("token", "")
    link = store.lookup(token, UPLOAD)
    if link is None:
        return not_found()
    what = "replaces the file there" if link.overwrite and link.path.exists() else "creates it"
    check = (f"<p>Only a file with SHA-256 <code>{link.expected}</code> is accepted.</p>"
             if link.expected else "")
    body = (
        "<h1>Upload to pcbridge</h1>"
        f'<p class="name">{html.escape(link.path.name)}</p>'
        f"<p>Goes to <code>{html.escape(str(link.path))}</code> and {what}. Up to "
        f"{link.max_bytes:,} bytes · valid for {_left(link)} more · one upload</p>{check}"
        '<form id="f"><input type="file" id="file"><br><button type="submit">Upload</button></form>'
        '<progress id="bar" value="0" max="1"></progress><p id="out"></p>'
        '<p class="note">The file name on this device does not matter. The SHA-256 the '
        "computer reports is compared with the file here when the browser can compute it "
        "(up to 200 MB). From a computer: <code>curl -T FILE</code> followed by this "
        "page's address.</p>"
    )
    return _page("pcbridge upload", body, data_max=link.max_bytes)


class _TooLarge(Exception):
    pass


def _refuse(store: LinkStore, link: Link, status: int, reason: str, message: str, *,
            retry: bool, got: int = 0) -> Response:
    if retry:
        store.release(link)
    else:
        store.spend(link)
    store.audit("fs_link_failed", direction=UPLOAD, path=str(link.path)[:200], link=link.id,
                reason=reason, bytes=got or None)
    if not retry:
        message += " This link is used up; ask for a new one."
    return JSONResponse({"error": message, "retry": retry}, status_code=status,
                        headers=COMMON_HEADERS)


def _finish(fh: Any) -> None:
    fh.flush()
    os.fsync(fh.fileno())
    fh.close()


def _place(tmp: str, path: Path, overwrite: bool) -> None:
    """Put the finished upload in place in one step; never clobber without overwrite."""
    os.chmod(tmp, fileslib._file_mode(path))
    if overwrite:
        os.replace(tmp, path)
        return
    try:
        os.link(tmp, path)  # fails if `path` exists, unlike rename
    except OSError as exc:
        if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EMLINK):
            raise
        # No hard links here (FAT, some FUSE): check, then rename.
        if os.path.lexists(path):
            raise FileExistsError(errno.EEXIST, "exists", str(path)) from None
        os.replace(tmp, path)
        return
    os.unlink(tmp)


async def upload_receive(store: LinkStore, request: Request) -> Response:
    token = request.path_params.get("token", "")
    link = store.claim(token, UPLOAD)
    if link is None:
        return not_found()
    started = time.monotonic()
    length = request.headers.get("content-length")
    try:
        declared = int(length) if length is not None else None
    except ValueError:
        return _refuse(store, link, 400, "bad_request", "Content-Length is not a number.",
                       retry=True)
    if declared is not None and declared > link.max_bytes:
        return _refuse(store, link, 413, "too_large",
                       f"The file is {declared:,} bytes; the limit is {link.max_bytes:,}.",
                       retry=False)
    try:
        free = shutil.disk_usage(link.path.parent).free
    except OSError:
        free = None
    if free is not None and free < (declared or 0) + FREE_MARGIN:
        return _refuse(store, link, 507, "no_space",
                       f"Not enough free space on the computer ({free:,} bytes free).",
                       retry=False)
    try:
        fd, tmp = tempfile.mkstemp(prefix=f".{link.path.name}.", suffix=TEMP_SUFFIX,
                                   dir=link.path.parent)
    except OSError as exc:
        return _refuse(store, link, 500, "write_error",
                       f"Could not write in the directory: {exc.strerror}.", retry=False)
    h = hashlib.sha256()
    got = 0
    try:
        fh = os.fdopen(fd, "wb")
        try:
            buf = bytearray()
            async for chunk in request.stream():
                got += len(chunk)
                if got > link.max_bytes:
                    raise _TooLarge
                h.update(chunk)
                buf += chunk
                if len(buf) >= _CHUNK:
                    await anyio.to_thread.run_sync(fh.write, bytes(buf))
                    buf.clear()
            if buf:
                await anyio.to_thread.run_sync(fh.write, bytes(buf))
            await anyio.to_thread.run_sync(_finish, fh)
        finally:
            if not fh.closed:
                fh.close()
        if declared is not None and got != declared:
            raise ClientDisconnect
        digest = h.hexdigest()
        if link.expected and digest != link.expected:
            return _refuse(store, link, 409, "sha256_mismatch",
                           f"The {got:,} bytes received have SHA-256 {digest}, not "
                           f"{link.expected}; nothing was stored. Pick the right file and "
                           "try again.", retry=True, got=got)
        try:
            _place(tmp, link.path, link.overwrite)
        except FileExistsError:
            return _refuse(store, link, 409, "exists",
                           f"`{link.path.name}` appeared on the computer in the meantime; "
                           "nothing was overwritten.", retry=False, got=got)
        store.spend(link)
        store.audit("fs_link_used", direction=UPLOAD, path=str(link.path)[:200], link=link.id,
                    bytes=got, sha256=digest, overwrite=link.overwrite or None,
                    seconds=round(time.monotonic() - started, 1), remote=_remote(request))
        return JSONResponse({"ok": True, "bytes": got, "sha256": digest,
                             "name": link.path.name}, headers=COMMON_HEADERS)
    except _TooLarge:
        return _refuse(store, link, 413, "too_large",
                       f"The upload went past the limit of {link.max_bytes:,} bytes.",
                       retry=False, got=got)
    except ClientDisconnect:
        store.release(link)
        store.audit("fs_link_failed", direction=UPLOAD, path=str(link.path)[:200],
                    link=link.id, reason="disconnected", bytes=got)
        return PlainTextResponse("incomplete", status_code=400, headers=COMMON_HEADERS)
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            return _refuse(store, link, 507, "no_space", "The disk filled up.", retry=False,
                           got=got)
        return _refuse(store, link, 500, "write_error", f"Could not write it: {exc.strerror}.",
                       retry=False, got=got)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
