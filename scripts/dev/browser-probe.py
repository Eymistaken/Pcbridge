#!/usr/bin/env python3
"""Repeat the browser attach measurements in docs/dev/measured-facts.md
("Browser: attaching to a session") that need no person at the machine.

    scripts/dev/browser-probe.py flags        # CDP flags vs. the default data dir
    scripts/dev/browser-probe.py persistence  # a dedicated profile across a restart
    scripts/dev/browser-probe.py pipe-close   # what closing the pipe does
    scripts/dev/browser-probe.py extension    # --load-extension and Extensions.loadUnpacked

Every run uses a fresh temporary directory, and "default" means the default
path under a temporary HOME: the real profiles are never launched or
connected to. Headed browser windows appear briefly; no input is sent.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import select
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

BROWSERS = {
    "chrome": ("/usr/bin/google-chrome", ".config/google-chrome"),
    "brave": ("/usr/bin/brave-browser", ".config/BraveSoftware/Brave-Browser"),
}
QUIET = ["--no-first-run", "--no-default-browser-check"]


class Browser:
    """A browser started with --remote-debugging-pipe, or with a port."""

    def __init__(self, exe: str, args: list[str], env: dict | None = None,
                 pipe: bool = True, log: Path | None = None) -> None:
        self.buf, self.n, self.pipe = b"", 0, pipe
        pass_fds: tuple[int, ...] = ()
        if pipe:
            r_in, self.w = os.pipe()
            self.r, w_out = os.pipe()

            def pre() -> None:
                os.dup2(r_in, 3)
                os.dup2(w_out, 4)
            pass_fds = (3, 4)
            args = ["--remote-debugging-pipe", *args]
        else:
            pre = None  # type: ignore[assignment]
            args = ["--remote-debugging-port=0", *args]
        self.proc = subprocess.Popen(
            [exe, *args, *QUIET, "about:blank"], env=env, stdout=subprocess.DEVNULL,
            stderr=open(log, "ab") if log else subprocess.DEVNULL,
            start_new_session=True, preexec_fn=pre, pass_fds=pass_fds)
        if pipe:
            os.close(r_in)
            os.close(w_out)

    def call(self, method: str, params: dict | None = None, session: str | None = None,
             timeout: float = 15) -> dict:
        self.n += 1
        msg: dict = {"id": self.n, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        os.write(self.w, json.dumps(msg).encode() + b"\0")
        end = time.time() + timeout
        while time.time() < end:
            while b"\0" in self.buf:
                raw, self.buf = self.buf.split(b"\0", 1)
                reply = json.loads(raw)
                if reply.get("id") == self.n:
                    if "error" in reply:
                        raise RuntimeError(reply["error"])
                    return reply["result"]
            if select.select([self.r], [], [], 0.5)[0]:
                chunk = os.read(self.r, 1 << 20)
                if not chunk:
                    raise EOFError(method)
                self.buf += chunk
        raise TimeoutError(method)

    def page(self) -> str:
        """Attach to the first page target, waiting for it to exist."""
        for _ in range(40):
            pages = [t for t in self.call("Target.getTargets")["targetInfos"]
                     if t["type"] == "page"]
            if pages:
                break
            time.sleep(0.25)
        else:
            pages = [self.call("Target.createTarget", {"url": "about:blank"})]
        return self.call("Target.attachToTarget",
                         {"targetId": pages[0]["targetId"], "flatten": True})["sessionId"]

    def eval(self, session: str, expr: str):
        return self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True},
                         session)["result"].get("value")

    def stop(self) -> None:
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
            self.proc.wait(10)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)


def local_site() -> str:
    """A 127.0.0.1 server whose /login sets a persistent and a session cookie."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            if self.path == "/login":
                self.send_header("Set-Cookie", "sid=kept; Max-Age=86400; Path=/; HttpOnly")
                self.send_header("Set-Cookie", "sess=dropped; Path=/")
            self.end_headers()
            self.wfile.write(b"<title>probe</title><p>ok</p>")

        def log_message(self, *args) -> None:
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}"


def probe_flags(base: Path) -> None:
    for name, (exe, rel) in BROWSERS.items():
        for pipe in (False, True):
            for kind in ("default", "explicit-default", "custom"):
                home = Path(tempfile.mkdtemp(prefix=f"{name}-", dir=base))
                env = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
                env["HOME"] = str(home)
                udd = home / rel if kind != "custom" else home / "profile"
                args = [] if kind == "default" else [f"--user-data-dir={udd}"]
                b = Browser(exe, ["--password-store=basic", *args], env, pipe, home / "err.txt")
                got = "no CDP"
                try:
                    if pipe:
                        got = b.call("Browser.getVersion", timeout=12)["product"]
                    else:
                        for _ in range(48):
                            f = udd / "DevToolsActivePort"
                            if f.exists() and f.read_text().strip():
                                got = "port " + f.read_text().split()[0]
                                break
                            time.sleep(0.25)
                except (TimeoutError, EOFError):
                    pass
                b.stop()
                print(f"{name:6} {'pipe' if pipe else 'port'} {kind:16} -> {got}")


def probe_persistence(base: Path) -> None:
    site = local_site()
    for name, (exe, _rel) in BROWSERS.items():
        udd = Path(tempfile.mkdtemp(prefix=f"{name}-", dir=base)) / "profile"
        b = Browser(exe, [f"--user-data-dir={udd}"])
        s = b.page()
        b.call("Page.navigate", {"url": site + "/login"}, s)
        time.sleep(1.5)
        b.eval(s, "localStorage.setItem('k', 'v42')")
        try:
            b.call("Browser.close", timeout=5)
        except (TimeoutError, EOFError):
            pass
        b.proc.wait(15)
        b = Browser(exe, [f"--user-data-dir={udd}"])
        s = b.page()
        b.call("Page.navigate", {"url": site + "/"}, s)
        time.sleep(1.5)
        cookies = sorted(c["name"] for c in b.call("Storage.getCookies")["cookies"])
        stored = b.eval(s, "localStorage.getItem('k')")
        b.stop()
        print(f"{name:6} after a restart: cookies={cookies} localStorage={stored!r}")


def probe_pipe_close(base: Path) -> None:
    for name, (exe, _rel) in BROWSERS.items():
        udd = Path(tempfile.mkdtemp(prefix=f"{name}-", dir=base)) / "profile"
        b = Browser(exe, [f"--user-data-dir={udd}"])
        b.page()
        os.close(b.w)
        os.close(b.r)
        t0 = time.time()
        while time.time() - t0 < 10 and b.proc.poll() is None:
            time.sleep(0.05)
        alive = b.proc.poll() is None
        print(f"{name:6} pipe closed -> " +
              ("still running" if alive else f"exited after {time.time() - t0:.1f} s"))
        b.stop()


def probe_extension(base: Path) -> None:
    ext = base / "ext"
    ext.mkdir()
    (ext / "manifest.json").write_text(json.dumps({
        "manifest_version": 3, "name": "pcbridge-probe", "version": "1.0",
        "background": {"service_worker": "probe.js"}}))
    (ext / "probe.js").write_text("self.probe = 1;")
    for name, (exe, _rel) in BROWSERS.items():
        for how in ("--load-extension", "Extensions.loadUnpacked"):
            udd = Path(tempfile.mkdtemp(prefix=f"{name}-", dir=base)) / "profile"
            extra = ([f"--load-extension={ext}"] if how == "--load-extension"
                     else ["--enable-unsafe-extension-debugging"])
            b = Browser(exe, [f"--user-data-dir={udd}", *extra])
            b.page()
            if how == "Extensions.loadUnpacked":
                b.call("Extensions.loadUnpacked", {"path": str(ext)})
            time.sleep(2)
            loaded = any(t["url"].endswith("/probe.js")
                         for t in b.call("Target.getTargets")["targetInfos"])
            b.stop()
            print(f"{name:6} {how:24} -> {'loaded' if loaded else 'not loaded'}")


def main() -> None:
    probes = {"flags": probe_flags, "persistence": probe_persistence,
              "pipe-close": probe_pipe_close, "extension": probe_extension}
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("probe", choices=[*probes, "all"])
    probe = ap.parse_args().probe
    with tempfile.TemporaryDirectory(prefix="pcbridge-browser-probe-") as tmp:
        for name, fn in probes.items():
            if probe in (name, "all"):
                fn(Path(tmp))


if __name__ == "__main__":
    main()
