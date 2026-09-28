"""Diagnostic VM graphics evidence; this does not test PcBridge capture."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from pcbridge.desktop import hyprland, session  # noqa: E402
from tests.live.test_capture_parity import (  # noqa: E402
    LineReader, MARKERS, close_to, marker_color, read_counter,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    assert os.environ.get("PCBRIDGE_TEST_LIVE_HYPRLAND") == "1"
    assert os.uname().nodename == "pcbridge-hyprland", "Use the dedicated VM"
    assert session.desktop_kind() == session.HYPRLAND
    assert hyprland.screen_locked() is False
    args.out_dir.mkdir(parents=True, exist_ok=True)
    pattern = subprocess.Popen(
        ["/usr/bin/python3", str(ROOT / "tests/live/pattern_window.py"), "--timeout", "60"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        reader = LineReader(pattern.stdout)
        connectors = reader.expect("ready ", 30).split(" ", 1)[1].split(",")
        assert len(connectors) == 2, f"Require two real outputs, observed {connectors}"
        evidence = []
        for counter in (521, 522):
            pattern.stdin.write(f"show {counter}\n")
            pattern.stdin.flush()
            reader.expect(f"shown {counter}", 10)
            for index, connector in enumerate(connectors):
                path = args.out_dir / f"{connector}-{counter}.png"
                # GTK drawing precedes Hyprland's fullscreen fade animation.
                # Wait for the actual displayed pattern, not only the draw ACK.
                deadline = time.monotonic() + 5
                while True:
                    subprocess.run(["grim", "-o", connector, str(path)], check=True, timeout=3)
                    with Image.open(path) as raw:
                        image = raw.convert("RGB")
                    color = marker_color(image)
                    observed = read_counter(image)
                    if close_to(color, MARKERS[index]) and observed == counter:
                        break
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.1)
                assert close_to(color, MARKERS[index]), (connector, color, MARKERS[index])
                assert observed == counter, (connector, observed, counter)
                evidence.append({"connector": connector, "size": image.size,
                                 "marker": color, "counter": observed})
        print(json.dumps({"diagnostic_grim": evidence}, sort_keys=True))
    finally:
        if pattern.poll() is None:
            pattern.stdin.write("quit\n")
            pattern.stdin.flush()
            try:
                pattern.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pattern.kill()
                pattern.wait()


if __name__ == "__main__":
    main()
