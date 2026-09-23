#!/usr/bin/env python3
"""The whole desktop path on Plasma, through a fresh MCP client.

Run with pcbridge's venv python inside a KDE Plasma Wayland session in the
TEST VM ONLY (scripts/dev/arch-vm.sh), with the daemon running and
`[desktop] enabled = true`. It opens desktop control, CLICKS into a Kate
window it opened itself and TYPES into it, reads it back, and locks again.

    .venv/bin/python tests/live/kde/check_mcp.py

Each step prints one line: ok or FAIL, the step, and what came back.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

if os.environ.get("XDG_CURRENT_DESKTOP", "").upper() != "KDE":
    print(json.dumps({"skipped": "not a KDE Plasma session"}))
    sys.exit(0)

from fastmcp import Client  # noqa: E402
from fastmcp.client.transports import StdioTransport  # noqa: E402

TEXT = "typed over MCP on Plasma"
failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    failures += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {detail[:300]}", flush=True)


def text_of(result) -> str:
    return "\n".join(getattr(block, "text", "") for block in result.content
                     if getattr(block, "type", "") == "text")


def a11y_state() -> str:
    return subprocess.run(["busctl", "--user", "get-property", "org.a11y.Bus", "/org/a11y/bus",
                           "org.a11y.Status", "IsEnabled"],
                          capture_output=True, text=True).stdout.strip()


def images_of(result) -> int:
    return sum(1 for block in result.content if getattr(block, "type", "") == "image")


async def main() -> int:
    launcher = str(Path.home() / ".local/bin/pcbridge")
    doc = Path(f"/tmp/pcbridge-mcp-{os.getpid()}.txt")
    doc.write_text("", encoding="utf-8")
    # Kate with an unsaved document ignores SIGTERM; earlier runs leave one.
    subprocess.run(["pkill", "-KILL", "-x", "kate"], check=False)
    a11y_before = a11y_state()
    time.sleep(1)
    subprocess.Popen(["kate", "--new", str(doc)], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    time.sleep(4)

    async with Client(StdioTransport(command=launcher, args=["stdio"])) as client:
        async def call(name: str, **args):
            return await client.call_tool(name, args, raise_on_error=False)

        caps = text_of(await call("system_capabilities"))
        check("platform is Plasma", "KDE Plasma 6" in caps, caps[caps.find("**Platform"):][:200])
        for name, backend in (("capture.monitor", "linux.kwin.screenshot2"),
                              ("window.focus", "linux.kwin-script"),
                              ("screen_lock", "linux.freedesktop-screen-saver"),
                              ("user_activity", "linux.freedesktop-screen-saver")):
            line = next((ln for ln in caps.splitlines() if f"`{name}`" in ln), "")
            check(f"{name} via {backend}", backend in line and "unavailable" not in line, line)

        unlock = text_of(await call("desktop_unlock", minutes=5, reason="VM e2e"))
        check("desktop_unlock", "KWin" in unlock, unlock)
        # A session that had it on already keeps it, and nothing is said.
        check("Qt accessibility turned on", a11y_before == "b true"
              or "Qt accessibility is on" in unlock, unlock)

        focus = text_of(await call("window_focus", window=doc.name))
        check("window_focus", doc.name in focus, focus)

        shot = await call("screen_capture", monitor="window")
        check("screen_capture window", images_of(shot) == 1, text_of(shot))
        full = await call("screen_capture")
        check("screen_capture all monitors", images_of(full) >= 1, text_of(full))

        dump = text_of(await call("ui_dump", target="kate"))
        check("ui_dump sees Kate", doc.name in dump, dump[:200])

        # Click into the document through the window's own geometry, via KWin.
        reply = json.loads(subprocess.run(
            ["/usr/bin/python3", str(Path(__file__).resolve().parents[3]
                                      / "pcbridge/desktop/kwin_helper.py")],
            input='{"cmd": "focused"}', capture_output=True, text=True).stdout)
        x, y, w, h = reply["geometry"]
        target = {"x": int(x + w / 2), "y": int(y + h / 2)}
        click = text_of(await call("mouse", action="click", force=True, **target))
        check("mouse click", "rror" not in click, click)
        typed = text_of(await call("keyboard", action="type", text=TEXT, force=True))
        check("keyboard type", "rror" not in typed, typed)
        time.sleep(1)
        dump = text_of(await call("ui_dump", target="kate"))
        check("Kate marks the document changed", f"{doc.name} *" in dump, dump[:120])
        found = text_of(await call("find_text", text="typed over MCP"))
        check("the text arrived (OCR on the screen)", "not found" not in found.lower()
              and "rror" not in found, found)

        lock = text_of(await call("desktop_lock"))
        check("desktop_lock", "closed" in lock, lock)
        after = text_of(await call("screen_capture"))
        check("capture refused after lock", "desktop_unlock" in after or "grant" in after, after)

    state = a11y_state()
    check("Qt accessibility restored", state == a11y_before, f"{state} (was {a11y_before})")
    notices = subprocess.run(["pgrep", "-x", "notify-send"], capture_output=True, text=True).stdout
    check("grant notification closed", not notices.strip(), notices)
    print(f"{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
