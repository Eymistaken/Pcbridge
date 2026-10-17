"""The browser tools: the pcbridge browser over MCP.

Registered from `tools.register`; their hints are in `tools.TOOL_HINTS`
with every other tool's. They pass no desktop gate: like the file and shell
tools they work with the desktop grant closed and with `[desktop] enabled =
false`, and only leave an audit record of what was done (action, tab, ref,
host, character counts), never typed text or page content.
"""

from __future__ import annotations

import asyncio
import base64
import secrets
import time
from typing import Annotated, Any, Callable, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ContentBlock, ImageContent, TextContent
from pydantic import Field

from .. import sessionctx
from .cdp import CDPError
from .manager import BrowserError, BrowserManager, host_of, png_size

# Under the 110 s every call must stay below (CLAUDE.md).
CALL_LIMIT = 100.0

TabArg = Annotated[str | None, Field(
    description="The tab id from browser_tabs (its first characters are enough). "
                "Default: the tab last used.")]


def register(mcp: FastMCP, manager: BrowserManager, gate: Any, shot_store: Any,
             transport: str, inline_images: Callable[[], bool]) -> None:

    async def run(what: str, coro):
        try:
            return await asyncio.wait_for(coro, CALL_LIMIT)
        except BrowserError as exc:
            gate.audit(f"{what}_error", error=str(exc)[:160])
            raise ToolError(str(exc)) from None
        except CDPError as exc:
            gate.audit(f"{what}_error", error=exc.text[:160])
            raise ToolError(f"The browser refused it: {exc.text}") from None
        except (asyncio.TimeoutError, TimeoutError):
            gate.audit(f"{what}_error", error="timeout")
            raise ToolError(f"The browser did not finish within {CALL_LIMIT:g} s.") from None

    @mcp.tool(annotations={"title": "List the browser's tabs"})
    async def browser_tabs() -> str:
        """List the tabs of the pcbridge browser: id, title and address.

        Use it first when working with web pages, to find the tab to read or
        act on. The pcbridge browser is a Chrome/Brave pcbridge runs on its own
        profile, where sign-ins persist; it is not the user's everyday
        browser. Needs no desktop control. Does not start the browser."""
        async def go() -> str:
            if await manager.connect(start=False) is None:
                return "The pcbridge browser is not running. browser_navigate starts it."
            tabs = await manager.tabs()
            gate.audit("browser_tabs", tabs=len(tabs))
            if not tabs:
                return "The pcbridge browser has no tab open."
            mark = manager.current
            rows = [f"{'*' if t.id == mark else ' '} {t.short}  {t.title[:80]!r}  {t.url[:200]}"
                    for t in tabs]
            return f"{len(tabs)} tab(s) (* = the one used by default)\n" + "\n".join(rows)
        return await run("browser_tabs", go())

    @mcp.tool(annotations={"title": "Read a web page"})
    async def browser_read(
        tab: TabArg = None,
        mode: Annotated[Literal["outline", "text"], Field(
            description="'outline' (default): headings, text, links, form fields with their "
                        "values, tables, each actionable element with a ref for browser_act. "
                        "'text': the visible text only, shorter.")] = "outline",
        find: Annotated[str, Field(
            description="Keep only the lines that contain this text (any case), to find "
                        "one field or row on a long page.")] = "",
        wait_for_text: Annotated[str, Field(
            description="First wait until this text is on the page (up to `timeout`), "
                        "for pages that fill in after loading.")] = "",
        timeout: Annotated[float, Field(ge=0, le=60, description="Seconds to wait for wait_for_text.")] = 15,
        max_chars: Annotated[int, Field(ge=500, le=60000, description="Cut the answer at this size.")] = 12000,
    ) -> str:
        """Read a page in the pcbridge browser: its structure, form fields
        (password values are never shown), tables and links.

        Use it to inspect a web interface, a router admin page or a signed-in
        site, and to get the refs browser_act needs. Read again after the page
        changes: refs from an old page are refused. Needs no desktop control;
        does not start the browser."""
        async def go() -> str:
            if await manager.connect(start=False) is None:
                raise BrowserError("The pcbridge browser is not running; browser_navigate starts it.")
            t, text = await manager.read(tab, mode, find, max_chars, wait_for_text, timeout)
            gate.audit("browser_read", tab=t.short, host=host_of(t.url), mode=mode,
                       find=bool(find) or None, chars=len(text))
            return text
        return await run("browser_read", go())

    @mcp.tool(output_schema=None, annotations={"title": "Screenshot a web page"})
    async def browser_screenshot(
        tab: TabArg = None,
        full_page: Annotated[bool, Field(
            description="The whole page, not only what fits in the window (up to 10000 "
                        "pixels tall).")] = False,
    ) -> list[ContentBlock]:
        """Take a picture of a page in the pcbridge browser, through the browser
        itself: it works while the window is hidden or covered and sees no
        other window.

        Use it when the page's look matters (a chart, a captcha, a layout
        problem); browser_read is cheaper for text and forms. Needs no
        desktop control; does not start the browser."""
        async def go() -> list[ContentBlock]:
            if await manager.connect(start=False) is None:
                raise BrowserError("The pcbridge browser is not running; browser_navigate starts it.")
            t, png = await manager.screenshot(tab, full_page)
            shot_store.sweep()
            path = shot_store.dir / f"browser-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.png"
            path.write_bytes(png)
            path.chmod(0o600)
            if sessionctx.transport(transport) != "stdio":
                _token, where = shot_store.publish(path)
            else:
                where = str(path)
            w, h = png_size(png)
            inline = inline_images()
            gate.audit("browser_screenshot", tab=t.short, host=host_of(t.url), size=f"{w}x{h}",
                       full_page=full_page or None, inline=inline or None)
            out: list[ContentBlock] = [TextContent(
                type="text", text=f"Tab {t.short} · {t.title[:80]!r} · {w}x{h}\n{where}")]
            if inline:
                out.append(ImageContent(type="image", mimeType="image/png",
                                        data=base64.b64encode(png).decode("ascii")))
            return out
        return await run("browser_screenshot", go())

    @mcp.tool(annotations={"title": "Open or move between web pages"})
    async def browser_navigate(
        url: Annotated[str, Field(
            description="Address to open (http or https; a bare host like 192.168.1.1 "
                        "gets http://). Needed for action 'open'.")] = "",
        action: Annotated[Literal["open", "back", "forward", "reload", "select", "close"], Field(
            description="'open' (default) a URL; 'back' / 'forward' in the tab's history; "
                        "'reload'; 'select' brings the tab to the front of the browser "
                        "window; 'close' the tab.")] = "open",
        tab: TabArg = None,
        new_tab: Annotated[bool, Field(description="Open the URL in a new tab.")] = False,
        timeout: Annotated[float, Field(ge=1, le=90, description="Seconds to wait for the page to load.")] = 30,
        max_chars: Annotated[int, Field(ge=0, le=60000,
                                        description="Size of the page outline returned; 0 = none.")] = 6000,
    ) -> str:
        """Open a page in the pcbridge browser, or go back, forward, reload,
        select or close a tab; then return the resulting page's outline.

        Use it to reach a web interface (a router at 192.168.1.1, a signed-in
        site). Starts the pcbridge browser when it is not running; it uses its
        own profile, so the user signs in to a site once there and stays signed
        in. Needs no desktop control."""
        async def go() -> str:
            await manager.connect(start=action == "open")
            if manager.conn is None:
                raise BrowserError("The pcbridge browser is not running; open a URL to start it.")
            t, done = await manager.navigate(action, url, tab, new_tab, timeout)
            gate.audit("browser_navigate", action=action, tab=t.short,
                       host=host_of(url) if url else host_of(t.url), new_tab=new_tab or None)
            if action == "close" or max_chars == 0:
                return done
            t = await manager.resolve(t.id)
            page = await manager.describe(t, await manager.session(t.id), max_chars=max_chars)
            return f"{done}.\n{page}"
        return await run("browser_navigate", go())

    @mcp.tool(annotations={"title": "Show or hide the browser window"})
    async def browser_visibility(
        action: Annotated[Literal["show", "hide", "status"], Field(
            description="'show' puts the pcbridge browser's window on screen, 'hide' takes it "
                        "off, 'status' says where it is.")] = "status",
        focus: Annotated[bool, Field(
            description="With 'show': also give the window keyboard focus. Off by default, "
                        "so showing never takes focus from what the user is doing.")] = False,
    ) -> str:
        """Show or hide the pcbridge browser's window on the user's screen.

        The browser works hidden (reads, actions and screenshots do not need
        the window); show it when the user should see or take over a page,
        for example a captcha or a sign-in. Goes through the compositor,
        sends no input and needs no desktop control. Does not start the
        browser."""
        async def go() -> str:
            view = await asyncio.to_thread(manager.view, action, focus)
            gate.audit("browser_visibility", action=action, focus=focus or None,
                       found=view.found, visible=view.visible if view.found else None)
            if action != "status" and not view.found:
                raise BrowserError(view.describe())
            return view.describe()
        return await run("browser_visibility", go())

    @mcp.tool(annotations={"title": "Click, type or choose on a web page"})
    async def browser_act(
        action: Annotated[Literal["click", "type", "select", "press", "hover", "scroll"], Field(
            description="'click' an element; 'type' text into a field (replacing what is "
                        "there); 'select' an option of a dropdown by its label or value; "
                        "'press' a key (Enter, Tab, Escape, ArrowDown, Control+a, ...); "
                        "'hover'; 'scroll' to an element, or the page up/down without a ref.")],
        ref: Annotated[str, Field(
            description="The element's ref from browser_read, e.g. 'e12'. Optional for "
                        "'press' and 'scroll'.")] = "",
        text: Annotated[str, Field(
            description="For 'type': the text. For 'select': the option. For 'press': the "
                        "key. For 'scroll' without a ref: 'down' (default) or 'up'.")] = "",
        submit: Annotated[bool, Field(description="For 'type': press Enter afterwards.")] = False,
        tab: TabArg = None,
        timeout: Annotated[float, Field(ge=0, le=60,
                                        description="Seconds to wait for a page load the action starts.")] = 10,
        max_chars: Annotated[int, Field(ge=0, le=60000,
                                        description="Size of the page outline returned; 0 = none.")] = 6000,
    ) -> str:
        """Act on an element of a page in the pcbridge browser, by the ref
        browser_read gave it; then return the page as it is now, so the result
        can be checked at once.

        Use it to fill in and submit forms, follow links and press buttons on a
        web interface. It goes through the browser, not the keyboard or mouse,
        so it needs no desktop control and cannot land in another window.
        Typing into a password field is refused unless the user turned it on."""
        async def go() -> str:
            if await manager.connect(start=False) is None:
                raise BrowserError("The pcbridge browser is not running; browser_navigate starts it.")
            t, done = await manager.act(action, tab, ref, text, submit, timeout)
            gate.audit("browser_act", action=action, tab=t.short, ref=ref[:16] or None,
                       host=host_of(t.url), chars=len(text) if action == "type" else None,
                       submit=submit or None)
            if max_chars == 0:
                return done
            t = await manager.resolve(t.id)
            page = await manager.describe(t, await manager.session(t.id), max_chars=max_chars)
            return f"{done}.\n{page}"
        return await run("browser_act", go())
