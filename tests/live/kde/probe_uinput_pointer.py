#!/usr/bin/env python3
"""Measure how KWin maps pcbridge's absolute uinput pointer.

Run with pcbridge's venv python (it needs python-evdev) inside a KDE Plasma
Wayland session, as a user who can write /dev/uinput. It MOVES THE POINTER
(no clicks, no keys): only run it in the test VM (scripts/dev/arch-vm.sh).

It creates an absolute pointer the same way pcbridge does (ABS range
0..canvas-1, taken from `kscreen-doctor -j`), moves it to a few targets,
and reads the cursor back from KWin through a one-shot KWin script. Each
target prints one JSON line: where it was sent, where KWin put it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

if os.environ.get("XDG_CURRENT_DESKTOP", "").upper() != "KDE":
    print(json.dumps({"skipped": "not a KDE Plasma session"}))
    sys.exit(0)

from evdev import AbsInfo, UInput, ecodes as e  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def canvas() -> tuple[int, int]:
    data = json.loads(subprocess.run(["kscreen-doctor", "-j"], capture_output=True,
                                     text=True, check=True).stdout)
    right = bottom = 0
    for o in data["outputs"]:
        if not o.get("enabled"):
            continue
        scale = float(o.get("scale") or 1)
        w, h = o["size"]["width"] / scale, o["size"]["height"] / scale
        if o.get("rotation") in (2, 8):
            w, h = h, w
        right = max(right, o["pos"]["x"] + w)
        bottom = max(bottom, o["pos"]["y"] + h)
    return int(round(right)), int(round(bottom))


def cursor() -> list[int]:
    out = subprocess.run(["/usr/bin/python3", os.path.join(HERE, "probe_kwin_script.py"),
                          "--repeat", "1"], capture_output=True, text=True).stdout
    return json.loads(out)["reply"]["cursor"]


def main() -> int:
    width, height = canvas()
    caps = {
        e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
        e.EV_ABS: [
            (e.ABS_X, AbsInfo(0, 0, width - 1, 0, 0, 0)),
            (e.ABS_Y, AbsInfo(0, 0, height - 1, 0, 0, 0)),
        ],
    }
    with UInput(caps, name="pcbridge-probe-pointer", input_props=[e.INPUT_PROP_DIRECT]
                if "--direct" in sys.argv else None) as dev:
        time.sleep(1.0)  # let libinput and KWin pick the device up
        print(json.dumps({"canvas": [width, height]}), flush=True)
        for x, y in ((100, 100), (width // 2, height // 2), (width - 50, height - 50),
                     (1500, 300), (0, 0)):
            dev.write(e.EV_ABS, e.ABS_X, x)
            dev.write(e.EV_ABS, e.ABS_Y, y)
            dev.syn()
            time.sleep(0.3)
            print(json.dumps({"sent": [x, y], "kwin": cursor()}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
