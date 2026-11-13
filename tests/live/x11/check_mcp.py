#!/usr/bin/env python3
"""The desktop checks on Cinnamon or Xfce (X11), through a fresh MCP client.

Run with pcbridge's venv python inside a Cinnamon or Xfce X11 session in a
TEST VM ONLY (scripts/dev/debian-vm.sh, or a Linux Mint live session), with
the daemon running and `[desktop] enabled = true`. It opens desktop control,
CLICKS into an editor window it opened itself, TYPES into it, saves it,
LOCKS THE SCREEN and unlocks it through logind (`sudo loginctl` for
light-locker, which needs a passwordless sudo as the test VMs have), and
ends with the kill switch.

    ~/.local/share/pcbridge/venv/bin/python tests/live/x11/check_mcp.py

Each step prints one line: ok or FAIL, the step, and what came back. The
checks are the stage 7 list: capture, input, window focus, lock, idle,
grant indicator and kill switch.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

DESKTOP = os.environ.get("XDG_CURRENT_DESKTOP", "")
if os.environ.get("XDG_SESSION_TYPE") != "x11" or not any(
        token.lower() in ("x-cinnamon", "cinnamon", "xfce") for token in DESKTOP.split(":")):
    print(json.dumps({"skipped": "not a Cinnamon or Xfce X11 session"}))
    sys.exit(0)

from fastmcp import Client  # noqa: E402
from fastmcp.client.transports import StdioTransport  # noqa: E402

from pcbridge.config import load_config  # noqa: E402
from pcbridge.desktop import idlewatch, safety, x11native  # noqa: E402

CINNAMON = "cinnamon" in DESKTOP.lower()
TEXT = "typed over MCP on X11 ğüşİ"
failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    failures += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {detail[:300]}", flush=True)


def text_of(result) -> str:
    return "\n".join(getattr(block, "text", "") for block in result.content
                     if getattr(block, "type", "") == "text")


def images_of(result) -> int:
    return sum(1 for block in result.content if getattr(block, "type", "") == "image")


def frame_windows() -> int | None:
    if not shutil.which("xwininfo"):
        return None
    tree = subprocess.run(["xwininfo", "-root", "-tree"], capture_output=True, text=True).stdout
    return tree.count('"pcbridge desktop grant"')


def wait_for(predicate, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return predicate()


def lock_screen() -> str:
    if CINNAMON:
        command = ["gdbus", "call", "--session", "-d", "org.cinnamon.ScreenSaver", "-o",
                   "/org/cinnamon/ScreenSaver", "-m", "org.cinnamon.ScreenSaver.Lock", ""]
    elif shutil.which("xfce4-screensaver-command"):
        command = ["xfce4-screensaver-command", "--lock"]
    else:
        command = ["light-locker-command", "-l"]
    subprocess.run(command, capture_output=True, timeout=10)
    return command[0]


def unlock_screen() -> None:
    """The VM's stand-in for typing the password: logind unlocks the session.

    light-locker locks by switching to the greeter; the session comes back
    with `loginctl activate`. Activated right after the unlock it was locked
    again (measured on Debian 12); a second later it stays.
    """
    session = subprocess.run(["loginctl", "show-user", str(os.getuid()), "-p", "Display",
                              "--value"], capture_output=True, text=True).stdout.strip()
    for _ in range(3):
        subprocess.run(["loginctl", "unlock-session", session], capture_output=True)
        time.sleep(1)
        subprocess.run(["sudo", "-n", "loginctl", "activate", session], capture_output=True)
        time.sleep(2)
        if safety.screen_locked() is False:
            return


async def main() -> int:
    launcher = os.environ.get("PCBRIDGE_LAUNCHER") or str(Path.home() / ".local/bin/pcbridge")
    cfg = load_config(check_state=False)
    editor = next((name for name in ("mousepad", "xed", "gedit") if shutil.which(name)), None)
    if editor is None:
        print("FAIL no editor (mousepad, xed or gedit) to type into")
        return 1
    doc = Path(f"/tmp/pcbridge-x11-{os.getpid()}.txt")
    doc.write_text("", encoding="utf-8")
    # A Mousepad killed by a reboot asks to restore its session in a dialog
    # instead of opening the file: ask it never to, and end earlier runs.
    subprocess.run(["gsettings", "set", "org.xfce.mousepad.preferences.file",
                    "session-restore", "never"], capture_output=True)
    subprocess.run(["pkill", "-x", editor], capture_output=True)
    time.sleep(1)
    subprocess.Popen([editor, str(doc)], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    time.sleep(4)

    async with Client(StdioTransport(command=launcher, args=["stdio"])) as client:
        async def call(name: str, **args):
            return await client.call_tool(name, args, raise_on_error=False)

        caps = text_of(await call("system_capabilities"))
        name = "Cinnamon" if CINNAMON else "Xfce"
        check(f"platform is {name} on X11", f"{name} " in caps and "on X11" in caps,
              caps[caps.find("**Platform"):][:200])
        check("the X11 security note is shown", "security: X11" in caps, "")
        for capability, backend in (("capture.monitor", "linux.x11.getimage"),
                                    ("capture.window", "linux.x11.ewmh"),
                                    ("window.focus", "linux.x11.ewmh"),
                                    ("clipboard.write", "linux.xclip.native"),
                                    ("screen_lock", "logind"),
                                    ("user_activity", "linux.x11-screensaver")):
            line = next((ln for ln in caps.splitlines() if f"`{capability}`" in ln), "")
            check(f"{capability} via {backend}", backend in line and "supported" in line, line)

        unlock = text_of(await call("desktop_unlock", minutes=5, reason="X11 VM check"))
        check("desktop_unlock", "granted" in unlock, unlock)
        mons = len([ln for ln in unlock.splitlines() if ln.strip()[:2].rstrip(":").isdigit()])
        count = frame_windows()
        check("grant frame on every monitor", count is None or count == 4 * max(1, mons),
              f"{count} strip window(s) for {mons} monitor(s)")
        if CINNAMON:
            applets = subprocess.run(["gsettings", "get", "org.cinnamon", "enabled-applets"],
                                     capture_output=True, text=True).stdout
            check("Cinnamon applet in the panel", "pcbridge@eymistaken" in applets, "")
        notices = subprocess.run(["pgrep", "-af", "notify-send|busctl --user monitor"],
                                 capture_output=True, text=True).stdout
        check("lasting grant notification with Lock now",
              "lock=Lock now" in notices or "org.freedesktop.Notifications" in notices, notices)

        focus = text_of(await call("window_focus", window=doc.stem, force=True))
        check("window_focus", "raised by" in focus, focus)
        shot = await call("screen_capture", monitor="window")
        check("screen_capture window", images_of(shot) == 1, text_of(shot)[:120])
        full = await call("screen_capture")
        check("screen_capture all monitors", images_of(full) >= 1, text_of(full)[:120])
        dump = text_of(await call("ui_dump", target="focused"))
        check("ui_dump sees the editor", doc.name in dump, dump[:200])

        frame = x11native.focused() or {}
        x, y, w, h = frame.get("geometry") or (0, 0, 0, 0)
        click = text_of(await call("mouse", action="click", x=int(x + w / 2),
                                   y=int(y + h / 2), force=True))
        check("mouse click into the editor", "click at" in click, click)
        typed = text_of(await call("keyboard", action="type", text=TEXT, force=True))
        check("keyboard type", "pasted" in typed or "typed" in typed, typed)
        save = text_of(await call("keyboard", action="key", keys="ctrl+s", force=True))
        check("keyboard ctrl+s", "rror" not in save, save)
        check("the text arrived in the file",
              wait_for(lambda: doc.read_text(encoding="utf-8").strip() == TEXT, 5),
              repr(doc.read_text(encoding="utf-8")))

        first = idlewatch.read_idle_ms()
        time.sleep(2.5)
        second = idlewatch.read_idle_ms()
        check("idle time grows without input", first is not None and second is not None
              and second >= first + 2000, f"{first} -> {second} ms")

        locker = lock_screen()
        check(f"screen locked ({locker})", wait_for(lambda: safety.screen_locked() is True, 8), "")
        refused = text_of(await call("screen_capture"))
        check("capture refused while locked", "locked" in refused, refused)
        unlock_screen()
        check("screen unlocked", wait_for(lambda: safety.screen_locked() is False, 8), "")
        # The frame proves itself again within a few hundred milliseconds
        # (measured 0.27 s on Xfce 4.20); until then a capture is refused as
        # retryable, as on Hyprland. Retry like an agent would.
        for _ in range(10):
            again = await call("screen_capture")
            if images_of(again) >= 1:
                break
            time.sleep(0.3)
        check("capture works after unlocking", images_of(again) >= 1, text_of(again)[:120])

        killed = subprocess.run([launcher, "lock"], capture_output=True, text=True)
        check("kill switch (pcbridge lock)", killed.returncode == 0, killed.stdout.strip())
        check("frame gone", wait_for(lambda: frame_windows() in (0, None), 5),
              f"{frame_windows()} strip window(s)")
        check("grant notification closed", wait_for(lambda: not subprocess.run(
            ["pgrep", "-f", "notify-send|busctl --user monitor"], capture_output=True
        ).stdout.strip(), 5), "")
        after = text_of(await call("screen_capture"))
        check("capture refused after the kill switch", "desktop_unlock" in after, after)

    print(f"{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
