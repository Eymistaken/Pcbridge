"""Taking the pcbridge browser over from a phone: a page behind the tunnel.

When an agent hands the browser to the user (browser_handoff), the user may
not be at the machine. The daemon then serves a small page, `/browser/<token>`,
that shows the browser's tab as a stream of frames (CDP
`Page.startScreencast`, sent as server-sent events) and sends taps, typed
text and keys back to it (`Input.dispatch*`). It is a web page, not an
application (ROADMAP, "Taking over the agent's browser").

SECURITY (docs/security.md, "The browser")
    The token is the authorization, as for file links and `/shot/`: a phone
    browser cannot do OAuth.

    * 256 random bits, kept in memory only, by their SHA-256; a daemon
      restart ends the link.
    * One live link, bound to the hand-over it was made for (its `since`):
      giving the browser back, a new hand-over, or `HANDOFF_TTL` (30
      minutes) ends it, and it then answers 404 like an unknown one.
    * It reaches only the pcbridge browser, never the desktop: input goes
      through CDP into the page.
    * The page carries a strict Content-Security-Policy with a per-response
      nonce, because this origin also serves the OAuth consent page.
    * Uvicorn's access log does not show the token (filelinks.RedactLinkTokens).
    * The audit log records the page opening, the stream, and giving back;
      never typed text, and not every tap.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import secrets
import time
from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, Response, StreamingResponse

from . import control
from .manager import BrowserError, BrowserManager

HANDOFF_TTL = 30 * 60
MAX_INPUT_BYTES = 4096
MAX_TEXT = 1000
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
           "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"}


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RemoteStore:
    """The one live take-over link."""

    def __init__(self, manager: BrowserManager, base_url: str,
                 audit: Callable[..., None] = lambda *a, **k: None) -> None:
        self.manager = manager
        self.base_url = base_url.rstrip("/")
        self.audit = audit
        self._digest = ""
        self._since = 0.0
        self._expires = 0.0

    def create(self) -> str:
        record = control.read(self.manager.dir)
        if record is None:
            raise BrowserError("The user does not have the browser; nothing to take over.")
        token = secrets.token_urlsafe(32)
        self._digest, self._since = _digest(token), float(record.get("since", 0))
        self._expires = time.time() + HANDOFF_TTL
        return f"{self.base_url}/browser/{token}"

    def valid(self, token: str) -> bool:
        if not self._digest or not secrets.compare_digest(_digest(token), self._digest):
            return False
        if time.time() > self._expires:
            return False
        record = control.read(self.manager.dir)
        return record is not None and float(record.get("since", 0)) == self._since

    def revoke(self) -> None:
        self._digest = ""


def _not_found() -> Response:
    return PlainTextResponse("not found", status_code=404, headers=HEADERS)


def _csp(nonce: str) -> str:
    return ("default-src 'none'; img-src data:; connect-src 'self'; "
            f"script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pcbridge browser</title>
<style nonce="{nonce}">
body {{ margin: 0; font: 15px system-ui, sans-serif; background: #111; color: #eee; }}
header {{ padding: 8px 10px; background: #222; display: flex; gap: 8px; align-items: center;
          flex-wrap: wrap; }}
header p {{ margin: 0; flex: 1 1 200px; }}
button {{ font: inherit; padding: 6px 10px; border-radius: 6px; border: 0; }}
#back {{ background: #2e7d32; color: #fff; }}
#screen {{ display: block; width: 100%; height: auto; touch-action: manipulation; background: #000; }}
#keys {{ display: flex; gap: 6px; padding: 8px 10px; background: #222; flex-wrap: wrap; }}
#text {{ flex: 1 1 160px; font: inherit; padding: 6px; }}
#state {{ padding: 4px 10px; color: #aaa; }}
</style></head>
<body>
<header><p><b>The agent handed you the browser.</b> {reason}</p>
<button id="back">Give it back to the agent</button></header>
<div id="state">Connecting…</div>
<img id="screen" alt="The pcbridge browser's page">
<div id="keys"><input id="text" autocomplete="off" autocapitalize="off" placeholder="Type here, then Send">
<button data-send="1">Send</button><button data-key="Enter">Enter</button>
<button data-key="Backspace">⌫</button><button data-key="Tab">Tab</button>
<button data-scroll="-600">▲</button><button data-scroll="600">▼</button></div>
<script nonce="{nonce}">
const base = location.pathname.replace(/\\/$/, "");
const img = document.getElementById("screen"), state = document.getElementById("state");
let size = {{w: 1, h: 1}};
function send(body) {{
  return fetch(base + "/input", {{method: "POST", headers: {{"Content-Type": "application/json"}},
    body: JSON.stringify(body)}});
}}
const events = new EventSource(base + "/frames");
events.onmessage = e => {{ const f = JSON.parse(e.data); size = f;
  img.src = "data:image/jpeg;base64," + f.img; state.textContent = "Live: tap the page to click."; }};
events.addEventListener("end", () => {{ events.close();
  state.textContent = "The browser is back with the agent. You can close this page."; }});
events.onerror = () => {{ state.textContent = "Reconnecting…"; }};
img.addEventListener("click", e => {{ const r = img.getBoundingClientRect();
  send({{kind: "click", x: (e.clientX - r.left) / r.width * size.w,
         y: (e.clientY - r.top) / r.height * size.h}}); }});
document.querySelector("[data-send]").onclick = () => {{ const t = document.getElementById("text");
  if (t.value) send({{kind: "text", text: t.value}}); t.value = ""; }};
for (const b of document.querySelectorAll("[data-key]")) b.onclick = () => send({{kind: "key", key: b.dataset.key}});
for (const b of document.querySelectorAll("[data-scroll]")) b.onclick = () => send({{kind: "scroll", dy: +b.dataset.scroll}});
document.getElementById("back").onclick = async () => {{ await send({{kind: "give_back"}});
  state.textContent = "Given back. You can close this page."; events.close(); }};
</script></body></html>"""


async def page(store: RemoteStore, request: Request) -> Response:
    token = request.path_params["token"]
    if not store.valid(token):
        return _not_found()
    record = control.read(store.manager.dir) or {}
    nonce = secrets.token_urlsafe(16)
    store.audit("browser_takeover_page", remote=request.client.host if request.client else None)
    body = PAGE.format(nonce=nonce, reason=html.escape(str(record.get("reason", ""))))
    return HTMLResponse(body, headers={**HEADERS, "Content-Security-Policy": _csp(nonce)})


async def frames(store: RemoteStore, request: Request) -> Response:
    token = request.path_params["token"]
    if not store.valid(token):
        return _not_found()
    m = store.manager
    try:
        conn = await m.connect(start=False)
        if conn is None:
            return _not_found()
        tab = await m.resolve(None)
        sid = (await m.call("Target.attachToTarget", {"targetId": tab.id, "flatten": True}))["sessionId"]
    except BrowserError:
        return _not_found()
    queue = conn.subscribe("Page.screencastFrame", sid)
    await m.call("Page.startScreencast", {"format": "jpeg", "quality": 60, "maxWidth": 1280,
                                          "maxHeight": 1600, "everyNthFrame": 1}, sid)
    store.audit("browser_takeover_stream", tab=tab.short)

    async def stream():
        try:
            while not await request.is_disconnected():
                if not store.valid(token):
                    yield "event: end\ndata: back\n\n"
                    return
                try:
                    frame = await asyncio.wait_for(queue.get(), 2.0)
                except asyncio.TimeoutError:
                    yield ": still here\n\n"
                    continue
                if frame is None:
                    yield "event: end\ndata: closed\n\n"
                    return
                await m.call("Page.screencastFrameAck", {"sessionId": frame["sessionId"]}, sid)
                meta = frame.get("metadata") or {}
                yield "data: " + json.dumps({"img": frame["data"], "w": meta.get("deviceWidth", 1),
                                             "h": meta.get("deviceHeight", 1)}) + "\n\n"
        finally:
            conn.unsubscribe(queue)
            for method, params in (("Page.stopScreencast", {}), ("Target.detachFromTarget",
                                                                  {"sessionId": sid})):
                try:
                    await m.call(method, params, None if method.startswith("Target") else sid, 5)
                except Exception:  # noqa: BLE001 -- the browser may be gone already
                    pass

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={**HEADERS, "X-Accel-Buffering": "no"})


async def user_input(store: RemoteStore, request: Request) -> Response:
    token = request.path_params["token"]
    if not store.valid(token):
        return _not_found()
    raw = await request.body()
    if len(raw) > MAX_INPUT_BYTES:
        return PlainTextResponse("too large", status_code=413, headers=HEADERS)
    try:
        event: dict[str, Any] = json.loads(raw)
    except ValueError:
        return PlainTextResponse("bad request", status_code=400, headers=HEADERS)
    m = store.manager
    kind = event.get("kind")
    try:
        if kind == "give_back":
            control.give_back(m.dir)
            store.revoke()
            store.audit("browser_handoff_back", by="remote page")
            return Response(status_code=204, headers=HEADERS)
        tab = await m.resolve(None)
        sid = await m.session(tab.id)
        if kind == "click":
            x, y = float(event["x"]), float(event["y"])
            await m.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y}, sid)
            for t in ("mousePressed", "mouseReleased"):
                await m.call("Input.dispatchMouseEvent",
                             {"type": t, "x": x, "y": y, "button": "left", "clickCount": 1}, sid)
        elif kind == "text":
            await m.call("Input.insertText", {"text": str(event.get("text", ""))[:MAX_TEXT]}, sid)
        elif kind == "key":
            await m.key(sid, str(event.get("key", "")))
        elif kind == "scroll":
            await m.evaluate(sid, f"window.scrollBy(0, {max(-3000, min(3000, int(event.get('dy', 0))))})")
        else:
            return PlainTextResponse("bad request", status_code=400, headers=HEADERS)
    except (BrowserError, KeyError, TypeError, ValueError):
        return PlainTextResponse("bad request", status_code=400, headers=HEADERS)
    return Response(status_code=204, headers=HEADERS)
