"""Compile and observe the VM-only Wayland input receiver."""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import subprocess
import threading
import time


def compile_observer(directory):
    protocols = Path(subprocess.run(["pkg-config", "--variable=pkgdatadir", "wayland-protocols"],
        check=True, capture_output=True, text=True, timeout=5).stdout.strip())
    sources = []
    for name, relative in (
        ("xdg-shell", "stable/xdg-shell/xdg-shell.xml"),
        ("pointer-constraints-v1", "unstable/pointer-constraints/pointer-constraints-unstable-v1.xml"),
        ("relative-pointer-v1", "unstable/relative-pointer/relative-pointer-unstable-v1.xml"),
    ):
        xml = protocols / relative
        assert xml.is_file(), str(xml)
        header = directory / f"{name}-client-protocol.h"
        source = directory / f"{name}-protocol.c"
        for mode, output in (("client-header", header), ("private-code", source)):
            subprocess.run(["wayland-scanner", mode, str(xml), str(output)], check=True,
                           capture_output=True, text=True, timeout=5)
        sources.append(str(source))
    flags = shlex.split(subprocess.run(["pkg-config", "--cflags", "--libs", "wayland-client"],
        check=True, capture_output=True, text=True, timeout=5).stdout)
    binary = directory / "wayland-input-window"
    command = ["cc", "-std=c11", "-Wall", "-Wextra", "-I", str(directory),
        str(Path(__file__).with_name("wayland_input_window.c")), *sources, *flags, "-o", str(binary)]
    compiled = subprocess.run(command, check=True, capture_output=True, text=True, timeout=20)
    return binary, {"command": command, "stderr": compiled.stderr}


class Observer:
    """Read protocol evidence independently of the MCP event loop."""

    def __init__(self, binary, directory, app_id):
        self.events = []
        self.condition = threading.Condition()
        self.stderr_path = directory / "observer.stderr"
        self.stderr = self.stderr_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen([str(binary), app_id], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.stderr, text=True, bufsize=1)
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def read(self):
        for line in self.process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                event = {"event": "invalid_json", "line": line[:512]}
            event["received_monotonic"] = time.monotonic()
            with self.condition:
                self.events.append(event)
                self.condition.notify_all()

    def mark(self):
        with self.condition:
            return len(self.events)

    def since(self, mark):
        with self.condition:
            return list(self.events[mark:])

    def wait(self, kind, mark=0, timeout=4, predicate=lambda event: True):
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                matches = [e for e in self.events[mark:] if e.get("event") == kind and predicate(e)]
                if matches:
                    return matches[0]
                remaining = deadline - time.monotonic()
                assert remaining > 0, {"missing": kind, "events": self.events[mark:],
                                       "exit_code": self.process.poll()}
                self.condition.wait(min(remaining, 0.1))

    def command(self, text):
        self.process.stdin.write(text + "\n")
        self.process.stdin.flush()

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()  # EOF requests bounded, normal client disposal.
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
        self.reader.join(timeout=2)
        self.process.stdout.close()
        self.stderr.close()
        return {"pid": self.process.pid, "exit_code": self.process.returncode,
                "reader_stopped": not self.reader.is_alive(),
                "stderr": self.stderr_path.read_text(encoding="utf-8", errors="replace")[-8000:]}
