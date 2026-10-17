"""The pcbridge browser: start it, find it again, and act on its pages.

The browser runs on pcbridge's own profile and is reached only through the
keeper's socket (see keeper.py), never through a TCP port and never by
application name, so the user's own Chrome or Brave is not touched.

Nothing here knows about the desktop grant: the browser tools are not
desktop tools and pass no desktop gate (ROADMAP, "Browser tools for the
user's existing session").
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .. import paths as pathslib
from . import control, snapshot, visibility
from .cdp import CDPError, Connection, Disconnected

KEEPER = Path(__file__).with_name("keeper.py")

# Chrome throttles a window it thinks nobody sees; a hidden pcbridge browser
# must keep loading and acting (ROADMAP, "Show and hide the browser window").
LAUNCH_FLAGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
    "--window-size=1280,900",
    # Its own application id, so its window is not grouped with the user's
    # Chrome in the task list, and compositor rules can name it (measured:
    # Chrome 155 on Wayland uses --class as the app id).
    "--class=pcbridge-browser",
]

CANDIDATES = {
    "chrome": ("google-chrome-stable", "google-chrome"),
    "brave": ("brave-browser-stable", "brave-browser", "brave"),
    "chromium": ("chromium", "chromium-browser"),
}

KEYS = {
    "Enter": (13, "\r"), "Tab": (9, ""), "Escape": (27, ""), "Backspace": (8, ""),
    "Delete": (46, ""), "ArrowUp": (38, ""), "ArrowDown": (40, ""), "ArrowLeft": (37, ""),
    "ArrowRight": (39, ""), "Home": (36, ""), "End": (35, ""), "PageUp": (33, ""),
    "PageDown": (34, ""), "Space": (32, " "),
}
MODIFIERS = {"Alt": 1, "Control": 2, "Ctrl": 2, "Meta": 4, "Shift": 8}

REF = re.compile(r"^\[?e(\d+)\]?$")


class BrowserError(Exception):
    """A failure the agent can act on; the text says what to do."""


@dataclass
class Tab:
    id: str
    title: str
    url: str

    @property
    def short(self) -> str:
        return self.id[:8]


def host_of(url: str) -> str:
    """The host alone, for the audit log: a path or query can hold a token."""
    try:
        return urllib.parse.urlsplit(url).hostname or url.split(":", 1)[0][:20]
    except ValueError:
        return ""


def normalize_url(url: str) -> str:
    """Accept http(s) and about:blank; a bare host gets http://."""
    url = (url or "").strip()
    if not url:
        raise BrowserError("Give a URL to open.")
    if url == "about:blank":
        return url
    if "://" not in url and not url.startswith(("javascript:", "data:", "file:", "chrome:")):
        url = "http://" + url
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise BrowserError(f"Only http and https pages can be opened, not {scheme}:.")
    return url


def png_size(data: bytes) -> tuple[int, int]:
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    return (0, 0)


class BrowserManager:
    def __init__(self, cfg: Any, use_scopes: Callable[[], bool] = lambda: False) -> None:
        self.cfg = cfg
        self.spec = cfg.browser
        self.use_scopes = use_scopes
        self.dir = Path(cfg.state_dir) / "browser"
        self.profile = Path(os.path.expanduser(self.spec.profile_dir)) if self.spec.profile_dir \
            else self.dir / "profile"
        self.info_path = self.dir / "keeper.json"
        self.tag = hashlib.sha256(str(Path(cfg.state_dir).resolve()).encode()).hexdigest()[:10]
        self.conn: Connection | None = None
        self.sessions: dict[str, str] = {}
        self.current: str | None = None
        self._lock: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None

    # -- where things are ----------------------------------------------------
    def socket_path(self) -> Path:
        # $XDG_RUNTIME_DIR/pcbridge, not the state directory: a Unix socket
        # path is limited to 107 bytes, and a test's state directory is long.
        return pathslib.runtime_dir(create=True) / f"browser-{self.tag}.sock"

    def executable(self) -> str:
        want = self.spec.executable
        if want == "auto":
            names = [n for group in ("chrome", "brave", "chromium") for n in CANDIDATES[group]]
        elif want in CANDIDATES:
            names = list(CANDIDATES[want])
        else:
            path = Path(os.path.expanduser(want))
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
            raise BrowserError(f"[browser] executable = {want!r} is not an executable file.")
        for name in names:
            found = shutil.which(name)
            if found:
                return found
        raise BrowserError(
            "No browser found: install Google Chrome, Brave or Chromium, or set "
            "[browser] executable to its path.")

    def status(self) -> dict | None:
        """The running keeper's record, or None. A record whose keeper is gone
        is removed."""
        try:
            info = json.loads(self.info_path.read_text())
            pid = int(info["keeper_pid"])
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if b"keeper.py" not in cmdline or not Path(info.get("socket", "")).exists():
            self.info_path.unlink(missing_ok=True)
            return None
        return info

    # -- connection ----------------------------------------------------------
    def lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock[0] is not loop:
            self._lock = (loop, asyncio.Lock())
        return self._lock[1]

    async def connect(self, start: bool) -> Connection | None:
        """The connection to the pcbridge browser; with `start` it is
        launched when it is not running."""
        async with self.lock():
            if self.conn is not None and self.conn.usable:
                return self.conn
            self.conn, self.sessions = None, {}
            info = self.status()
            if info is None:
                if not start:
                    return None
                info = await self.launch()
            try:
                self.conn = await Connection.open(info["socket"])
            except (OSError, asyncio.TimeoutError) as exc:
                raise BrowserError(f"Could not reach the pcbridge browser: {exc}") from None
            return self.conn

    async def launch(self) -> dict:
        exe = self.executable()
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.profile.mkdir(mode=0o700, parents=True, exist_ok=True)
        sock = self.socket_path()
        browser = [exe, "--remote-debugging-pipe", f"--user-data-dir={self.profile}",
                   *LAUNCH_FLAGS, *self.spec.extra_args, "about:blank"]
        cmd = [sys.executable, "-I", str(KEEPER), "--socket", str(sock),
               "--info", str(self.info_path), "--", *browser]
        if self.use_scopes() and shutil.which("systemd-run"):
            unit = f"pcbridge-browser-{self.tag}-{secrets.token_hex(3)}.scope"
            cmd = ["systemd-run", "--user", "--scope", "--quiet", "--collect",
                   f"--unit={unit}", "--", *cmd]
        env = dict(os.environ)
        try:
            from ..desktop import session as sessionlib
            sessionlib.ensure_session_env(env)
        except Exception:  # noqa: BLE001 -- a headless machine has no session to repair
            pass
        self.info_path.unlink(missing_ok=True)
        # Hidden by default: the GNOME extension minimizes the new window
        # before it is shown when this says so.
        visibility.write_wanted(self.dir, False)
        control.give_back(self.dir)  # a hand-over ends with the browser it was for
        backend = visibility.for_session()
        backend.prepare_launch()
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env=env, start_new_session=True,
                                close_fds=True)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            info = self.status()
            if info is not None:
                # Hidden by default: on Plasma and Hyprland the first window is
                # hidden as it appears (GNOME's extension does it by itself).
                threading.Thread(target=backend.hide_new,
                                 args=(int(info["browser_pid"]),), daemon=True).start()
                return info
            if proc.poll() is not None and not self.info_path.exists():
                break
            await asyncio.sleep(0.1)
        log = self.dir / "browser.log"
        tail = log.read_text(errors="replace")[-400:] if log.exists() else ""
        raise BrowserError(f"The browser did not start ({exe}). {tail}".strip())

    async def call(self, method: str, params: dict | None = None, session: str | None = None,
                   timeout: float = 30.0) -> dict:
        conn = self.conn
        if conn is None or not conn.usable:
            conn = await self.connect(start=False)
            if conn is None:
                raise BrowserError("The pcbridge browser is not running; browser_navigate starts it.")
        try:
            return await conn.call(method, params, session, timeout)
        except Disconnected:
            self.conn = None
            raise BrowserError("The pcbridge browser closed. browser_navigate starts it again.") from None

    # -- tabs ----------------------------------------------------------------
    async def tabs(self) -> list[Tab]:
        targets = (await self.call("Target.getTargets"))["targetInfos"]
        return [Tab(t["targetId"], t.get("title", ""), t.get("url", "")) for t in targets
                if t.get("type") == "page" and not t.get("url", "").startswith(
                    ("devtools://", "chrome-extension://"))]

    async def resolve(self, tab: str | None) -> Tab:
        tabs = await self.tabs()
        if tab:
            want = tab.strip().lower()
            hits = [t for t in tabs if t.id.lower().startswith(want)]
            if not hits:
                raise BrowserError(f"No tab {tab!r} in the pcbridge browser; browser_tabs lists them.")
            if len(hits) > 1:
                raise BrowserError(f"{tab!r} matches {len(hits)} tabs; give more of the id.")
            self.current = hits[0].id
            return hits[0]
        for t in tabs:
            if t.id == self.current:
                return t
        if not tabs:
            raise BrowserError("The pcbridge browser has no tab open; browser_navigate opens one.")
        self.current = tabs[0].id
        return tabs[0]

    async def session(self, target: str) -> str:
        sid = self.sessions.get(target)
        if sid is None:
            try:
                sid = (await self.call("Target.attachToTarget",
                                       {"targetId": target, "flatten": True}))["sessionId"]
            except CDPError as exc:
                raise BrowserError(f"The tab is gone ({exc.text}); browser_tabs lists the open ones.") from None
            self.sessions[target] = sid
        return sid

    async def evaluate(self, sid: str, expr: str, timeout: float = 10.0) -> Any:
        res = await self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                                   "awaitPromise": True}, sid, timeout)
        if "exceptionDetails" in res:
            raise BrowserError("The page raised an error: "
                               + str(res["exceptionDetails"].get("text", ""))[:200])
        return (res.get("result") or {}).get("value")

    async def wait_ready(self, sid: str, timeout: float) -> str:
        """Wait until the document has loaded; returns the last readyState
        seen ("" when the page never answered)."""
        state = ""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            try:
                state = await self.evaluate(sid, "document.readyState", timeout=2.0) or ""
            except (BrowserError, CDPError, TimeoutError):
                state = ""
            if state == "complete" or time.monotonic() >= deadline:
                return state
            await asyncio.sleep(0.2)

    # -- reading -------------------------------------------------------------
    async def page_outline(self, sid: str) -> snapshot.Page:
        tree = (await self.call("Page.getFrameTree", {}, sid))["frameTree"]
        frames = [snapshot.Frame((await self.call("Accessibility.getFullAXTree", {}, sid))["nodes"],
                                 url=tree["frame"].get("url", ""))]
        skipped: list[str] = []

        async def children(node: dict) -> None:
            for child in node.get("childFrames", []):
                fid = child["frame"]["id"]
                try:
                    nodes = (await self.call("Accessibility.getFullAXTree", {"frameId": fid}, sid))["nodes"]
                    owner = (await self.call("DOM.getFrameOwner", {"frameId": fid}, sid))["backendNodeId"]
                    frames.append(snapshot.Frame(nodes, owner, child["frame"].get("url", "")))
                except CDPError:
                    skipped.append(child["frame"].get("url", ""))
                await children(child)
        await children(tree)
        dom = snapshot.elements(await self.call("DOMSnapshot.captureSnapshot",
                                                {"computedStyles": []}, sid))
        page = snapshot.outline(frames, dom)
        for url in skipped:
            page.lines.append(f"frame (from another site, not read: {host_of(url)})")
        return page

    async def page_text(self, sid: str) -> str:
        # Same-origin frames too; a cross-origin frame throws and is left out.
        return await self.evaluate(sid, """(() => {
            const docs = [document];
            for (const f of Array.from(document.querySelectorAll('iframe,frame'))) {
                try { if (f.contentDocument) docs.push(f.contentDocument); } catch (e) {}
            }
            return docs.map(d => d.body ? d.body.innerText : '').join('\\n');
        })()""") or ""

    async def describe(self, tab: Tab, sid: str, find: str = "", mode: str = "outline",
                       max_chars: int = 12000) -> str:
        state = await self.evaluate(sid, "document.readyState", timeout=5.0)
        live = await self.evaluate(sid, "[document.title, location.href]", timeout=5.0) or ["", ""]
        head = f"Tab {tab.short} · {snapshot._q(live[0])} · {live[1]}"
        if state != "complete":
            head += f" · still loading ({state})"
        if mode == "text":
            text = await self.page_text(sid)
            lines = [ln for ln in text.splitlines() if ln.strip()]
            body, kept, cut = snapshot.trim(lines, find, max_chars)
            foot = ""
        else:
            page = await self.page_outline(sid)
            body, kept, cut = snapshot.trim(page.lines, find, max_chars)
            foot = f"{page.refs} elements to act on: give their ref, e.g. ref=\"e12\", to browser_act."
        if find and not kept:
            body = f"(nothing contains {find!r})"
        notes = [head, body]
        if cut:
            notes.append(f"… cut at {max_chars} characters: narrow it with `find`, or raise `max_chars`.")
        if foot:
            notes.append(foot)
        return "\n".join(n for n in notes if n)

    async def read(self, tab: str | None, mode: str, find: str, max_chars: int,
                   wait_for_text: str, timeout: float) -> tuple[Tab, str]:
        t = await self.resolve(tab)
        sid = await self.session(t.id)
        if wait_for_text:
            deadline = time.monotonic() + timeout
            needle = wait_for_text.lower()
            while needle not in (await self.page_text(sid)).lower():
                if time.monotonic() >= deadline:
                    raise BrowserError(f"{wait_for_text!r} did not appear within {timeout:g} s.")
                await asyncio.sleep(0.5)
        return t, await self.describe(t, sid, find, mode, max_chars)

    async def screenshot(self, tab: str | None, full_page: bool) -> tuple[Tab, bytes]:
        t = await self.resolve(tab)
        sid = await self.session(t.id)
        params: dict[str, Any] = {"format": "png"}
        if full_page:
            size = (await self.call("Page.getLayoutMetrics", {}, sid))["cssContentSize"]
            params.update(captureBeyondViewport=True, clip={
                "x": 0, "y": 0, "width": size["width"], "height": min(size["height"], 10000),
                "scale": 1})
        import base64
        data = (await self.call("Page.captureScreenshot", params, sid, timeout=60))["data"]
        return t, base64.b64decode(data)

    # -- navigating ----------------------------------------------------------
    async def navigate(self, action: str, url: str, tab: str | None, new_tab: bool,
                       timeout: float) -> tuple[Tab, str]:
        """Do it; returns the tab it happened in and a note."""
        if action == "open":
            target = normalize_url(url)
            if new_tab or not await self.tabs():
                tid = (await self.call("Target.createTarget", {"url": target}))["targetId"]
                self.current = tid
                t = await self.resolve(tid)
            else:
                t = await self.resolve(tab)
                sid = await self.session(t.id)
                res = await self.call("Page.navigate", {"url": target}, sid, timeout=timeout)
                if res.get("errorText"):
                    raise BrowserError(f"Could not open {target}: {res['errorText']}")
            await self.wait_ready(await self.session(t.id), timeout)
            return t, f"Opened {target}"
        t = await self.resolve(tab)
        sid = await self.session(t.id)
        if action in ("back", "forward"):
            hist = await self.call("Page.getNavigationHistory", {}, sid)
            i = hist["currentIndex"] + (-1 if action == "back" else 1)
            if not 0 <= i < len(hist["entries"]):
                raise BrowserError(f"There is no page to go {action} to.")
            await self.call("Page.navigateToHistoryEntry", {"entryId": hist["entries"][i]["id"]}, sid)
            await asyncio.sleep(0.2)
            await self.wait_ready(sid, timeout)
            return t, f"Went {action}"
        if action == "reload":
            await self.call("Page.reload", {}, sid)
            await asyncio.sleep(0.2)
            await self.wait_ready(sid, timeout)
            return t, "Reloaded"
        if action == "select":
            await self.call("Target.activateTarget", {"targetId": t.id})
            return t, "Selected the tab"
        if action == "close":
            if len(await self.tabs()) == 1:
                # Closing the last window ends the browser; keep one tab.
                await self.call("Target.createTarget", {"url": "about:blank"})
            await self.call("Target.closeTarget", {"targetId": t.id})
            self.sessions.pop(t.id, None)
            self.current = None
            return t, f"Closed tab {t.short}"
        raise BrowserError(f"Unknown action {action!r}.")

    # -- acting --------------------------------------------------------------
    async def node(self, sid: str, ref: str) -> tuple[int, dict]:
        m = REF.match((ref or "").strip())
        if not m:
            raise BrowserError(f"{ref!r} is not a ref; refs look like e12 and come from browser_read.")
        backend = int(m.group(1))
        gone = BrowserError(f"{ref} is gone: the page changed. Read it again with browser_read.")
        try:
            node = (await self.call("DOM.describeNode", {"backendNodeId": backend}, sid))["node"]
            # Measured: after a navigation the old node can still be
            # described; only isConnected says it left the document.
            obj = (await self.call("DOM.resolveNode", {"backendNodeId": backend}, sid))["object"]
            res = await self.call("Runtime.callFunctionOn", {
                "objectId": obj["objectId"], "returnByValue": True,
                "functionDeclaration": "function() { return this.isConnected; }"}, sid)
        except CDPError:
            raise gone from None
        if (res.get("result") or {}).get("value") is not True:
            raise gone
        return backend, node

    async def label(self, sid: str, backend: int) -> str:
        try:
            nodes = (await self.call("Accessibility.getPartialAXTree",
                                     {"backendNodeId": backend, "fetchRelatives": False}, sid))["nodes"]
        except CDPError:
            return ""
        for n in nodes:
            role = (n.get("role") or {}).get("value") or ""
            name = (n.get("name") or {}).get("value") or ""
            if role:
                return f"{role} {snapshot._q(name)}" if name else role
        return ""

    async def center(self, sid: str, backend: int) -> tuple[float, float]:
        try:
            await self.call("DOM.scrollIntoViewIfNeeded", {"backendNodeId": backend}, sid)
            quads = (await self.call("DOM.getContentQuads", {"backendNodeId": backend}, sid))["quads"]
        except CDPError as exc:
            raise BrowserError(f"The element cannot be reached ({exc.text}); it may be hidden.") from None
        if not quads:
            raise BrowserError("The element has no visible box; it may be hidden or collapsed.")
        q = quads[0]
        return (q[0] + q[2] + q[4] + q[6]) / 4, (q[1] + q[3] + q[5] + q[7]) / 4

    async def key(self, sid: str, combo: str) -> None:
        parts = [p for p in re.split(r"\+", combo.strip()) if p]
        if not parts:
            raise BrowserError("Give a key to press, e.g. Enter or Control+a.")
        *mods, name = parts
        flags = 0
        for m in mods:
            if m.capitalize() not in MODIFIERS:
                raise BrowserError(f"Unknown modifier {m!r}; use Control, Shift, Alt or Meta.")
            flags |= MODIFIERS[m.capitalize()]
        if name in KEYS:
            code, text = KEYS[name]
            key, dom_code = (" " if name == "Space" else name), name
        elif len(name) == 1:
            code, text, key = ord(name.upper()), name, name
            dom_code = f"Key{name.upper()}" if name.isalpha() else (f"Digit{name}" if name.isdigit() else "")
        else:
            raise BrowserError(f"Unknown key {name!r}; known: {', '.join(KEYS)} or a single character.")
        if flags & ~MODIFIERS["Shift"]:
            text = ""  # a shortcut types nothing
        down = {"type": "keyDown" if text else "rawKeyDown", "key": key, "code": dom_code,
                "windowsVirtualKeyCode": code, "modifiers": flags}
        if text:
            down["text"] = text
        await self.call("Input.dispatchKeyEvent", down, sid)
        await self.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": dom_code,
                                                   "windowsVirtualKeyCode": code, "modifiers": flags}, sid)

    async def act(self, action: str, tab: str | None, ref: str, text: str, submit: bool,
                  timeout: float) -> tuple[Tab, str]:
        t = await self.resolve(tab)
        sid = await self.session(t.id)
        what = ""
        if action == "scroll" and not ref:
            down = (text or "down").strip().lower() != "up"
            await self.evaluate(sid, f"window.scrollBy(0, {'' if down else '-'}window.innerHeight * 0.8)")
            done = "Scrolled " + ("down" if down else "up")
        elif action == "press" and not ref:
            await self.key(sid, text)
            done = f"Pressed {text}"
        else:
            backend, node = await self.node(sid, ref)
            what = await self.label(sid, backend)
            ref_s = f"e{backend}" + (f" ({what})" if what else "")
            attrs = dict(zip(node.get("attributes", [])[::2], node.get("attributes", [])[1::2]))
            if action in ("click", "hover"):
                x, y = await self.center(sid, backend)
                await self.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y}, sid)
                if action == "click":
                    for kind in ("mousePressed", "mouseReleased"):
                        await self.call("Input.dispatchMouseEvent", {
                            "type": kind, "x": x, "y": y, "button": "left", "clickCount": 1}, sid)
                done = ("Clicked " if action == "click" else "Hovered over ") + ref_s
            elif action == "type":
                if (node.get("nodeName", "").lower() == "input" and attrs.get("type", "").lower() == "password"
                        and not self.spec.allow_password_fields):
                    raise BrowserError(
                        f"e{backend} is a password field, and typing into one is off "
                        "([browser] allow_password_fields = false). Ask the user to sign in in the "
                        "pcbridge browser themselves; the sign-in stays.")
                try:
                    await self.call("DOM.focus", {"backendNodeId": backend}, sid)
                except CDPError as exc:
                    raise BrowserError(f"e{backend} cannot take text ({exc.text}).") from None
                obj = (await self.call("DOM.resolveNode", {"backendNodeId": backend}, sid))["object"]
                await self.call("Runtime.callFunctionOn", {"objectId": obj["objectId"], "functionDeclaration": (
                    "function() { if (typeof this.select === 'function' && 'value' in this) { this.select(); }"
                    " else { const r = document.createRange(); r.selectNodeContents(this);"
                    " const s = getSelection(); s.removeAllRanges(); s.addRange(r); } }")}, sid)
                await self.key(sid, "Delete")
                if text:
                    await self.call("Input.insertText", {"text": text}, sid)
                done = f"Typed {len(text)} characters into {ref_s}"
                if submit:
                    await self.key(sid, "Enter")
                    done += " and pressed Enter"
            elif action == "select":
                obj = (await self.call("DOM.resolveNode", {"backendNodeId": backend}, sid))["object"]
                res = await self.call("Runtime.callFunctionOn", {
                    "objectId": obj["objectId"], "returnByValue": True, "arguments": [{"value": text}],
                    "functionDeclaration": (
                        "function(want) { if (!this.options) return {error: 'not a select'};"
                        " const w = String(want).trim().toLowerCase();"
                        " const all = Array.from(this.options);"
                        " const o = all.find(o => o.value.toLowerCase() === w) ||"
                        " all.find(o => o.text.trim().toLowerCase() === w);"
                        " if (!o) return {error: 'no option', options: all.map(o => o.text.trim())};"
                        " this.value = o.value; o.selected = true;"
                        " this.dispatchEvent(new Event('input', {bubbles: true}));"
                        " this.dispatchEvent(new Event('change', {bubbles: true}));"
                        " return {chosen: o.text.trim()}; }")}, sid)
                val = (res.get("result") or {}).get("value") or {}
                if val.get("error") == "not a select":
                    raise BrowserError(f"e{backend} is not a select; use click for other menus.")
                if val.get("error"):
                    raise BrowserError(f"No option {text!r}; the options are: "
                                       + ", ".join(repr(o) for o in val.get("options", [])[:30]))
                done = f"Chose {val.get('chosen')!r} in {ref_s}"
            elif action == "press":
                await self.call("DOM.focus", {"backendNodeId": backend}, sid)
                await self.key(sid, text)
                done = f"Pressed {text} in {ref_s}"
            elif action == "scroll":
                await self.center(sid, backend)
                done = f"Scrolled to {ref_s}"
            else:
                raise BrowserError(f"Unknown action {action!r}.")
        await asyncio.sleep(0.3)
        await self.wait_ready(sid, timeout)
        return t, done

    # -- on screen or not ----------------------------------------------------
    def view(self, action: str, focus: bool = False) -> visibility.View:
        """Show, hide or report the browser window through the compositor.
        Sends no input and needs no grant. "show" leaves the focus where it
        is unless `focus`."""
        info = self.status()
        if info is None:
            return visibility.View(False, note="The pcbridge browser is not running; "
                                               "browser_navigate starts it (hidden).")
        backend = visibility.for_session()
        pid = int(info["browser_pid"])
        if action == "status":
            return backend.state(pid)
        if action not in ("show", "hide"):
            raise BrowserError(f"Unknown action {action!r}; use show, hide or status.")
        visibility.write_wanted(self.dir, action == "show")
        return backend.set(pid, action == "show", focus)

    # -- shutting down -------------------------------------------------------
    async def stop(self) -> bool:
        """Close the pcbridge browser cleanly. False when it was not running."""
        info = self.status()
        if info is None:
            return False
        try:
            os.kill(int(info["keeper_pid"]), 15)
        except OSError:
            return False
        for _ in range(100):
            if self.status() is None:
                break
            await asyncio.sleep(0.1)
        self.conn, self.sessions, self.current = None, {}, None
        control.give_back(self.dir)
        return True
