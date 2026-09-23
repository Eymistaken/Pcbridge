#!/usr/bin/env python3
"""Drive pcbridge's own input layer on Plasma and check what KWin saw.

Run with pcbridge's venv python inside a KDE Plasma Wayland session in the
TEST VM ONLY (scripts/dev/arch-vm.sh): it moves the pointer, CLICKS into a
Kate window and TYPES a line into it. It opens that Kate window itself
(`kate --new` on an empty document) and checks, through a KWin script and
AT-SPI, that the pointer landed where it was sent and that the text arrived.

    .venv/bin/python tests/live/kde/check_input.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

if os.environ.get("XDG_CURRENT_DESKTOP", "").upper() != "KDE":
    print(json.dumps({"skipped": "not a KDE Plasma session"}))
    sys.exit(0)

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from pcbridge.desktop.input import InputBackend  # noqa: E402

HERE = Path(__file__).resolve().parent
TEXT = "pcbridge on KWin 42"


def kwin(*args: str) -> dict:
    out = subprocess.run(["/usr/bin/python3", str(HERE / "probe_kwin_script.py"),
                          "--repeat", "1", *args], capture_output=True, text=True).stdout
    return json.loads(out)["reply"]


def kate_text(doc_name: str) -> str:
    """The text of Kate's document view named `doc_name`, through AT-SPI."""
    code = r"""
import gi, sys; gi.require_version("Atspi", "2.0"); from gi.repository import Atspi
want = sys.argv[1]
def walk(a, depth=0):
    if depth > 25: return
    try:
        if a.get_name() == want and "Text" in (a.get_interfaces() or []):
            print(Atspi.Text.get_text(a, 0, Atspi.Text.get_character_count(a))); sys.exit(0)
        for i in range(a.get_child_count()):
            walk(a.get_child_at_index(i), depth + 1)
    except SystemExit:
        raise
    except Exception:
        pass
d = Atspi.get_desktop(0)
for i in range(d.get_child_count()):
    app = d.get_child_at_index(i)
    if app.get_name() == "kate":
        walk(app)
"""
    return subprocess.run(["/usr/bin/python3", "-c", code, doc_name], capture_output=True,
                          text=True, timeout=30).stdout.strip()


def main() -> int:
    subprocess.run(["busctl", "--user", "set-property", "org.a11y.Bus", "/org/a11y/bus",
                    "org.a11y.Status", "IsEnabled", "b", "true"], check=False)
    doc = Path(f"/tmp/pcbridge-input-{os.getpid()}.txt")
    doc.write_text("", encoding="utf-8")
    subprocess.Popen(["kate", "--new", str(doc)], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    time.sleep(4)
    window = next(w for w in kwin("--activate", doc.name)["windows"]
                  if doc.name in w["caption"])
    x, y, w, h = window["geometry"]
    target = (int(x + w / 2), int(y + h / 2))
    backend = InputBackend()
    try:
        backend.move(*target)
        time.sleep(0.2)
        seen = kwin()["cursor"]
        print(json.dumps({"sent": target, "kwin": seen, "ok": list(target) == seen}), flush=True)
        backend.click()
        time.sleep(0.3)
        print(json.dumps({"typed": backend.type_text(TEXT)}), flush=True)
        time.sleep(1.0)
    finally:
        backend.close()
    text = kate_text(doc.name)
    print(json.dumps({"kate_text": text, "ok": TEXT in text}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
