"""A fake router admin panel on 127.0.0.1, for the browser tools' tests.

It has what real panels have: a sign-in form that sets a persistent session
cookie, a status table inside a same-origin iframe, and a settings form with
a text field, a select, a checkbox and radio buttons that saves and reports
back. The password is chosen by the test; nothing here is a real secret.
"""

from __future__ import annotations

import html
import http.server
import secrets
import threading
import urllib.parse

LOGIN = """<!doctype html><title>Router - Sign in</title>
<h1>Router sign in</h1>
<form method="post" action="/login">
  <label>Username <input name="user" autocomplete="username"></label>
  <label>Password <input name="pass" type="password" autocomplete="current-password"></label>
  <button type="submit">Sign in</button>
</form>{error}"""

HOME = """<!doctype html><title>Router - Status</title>
<h1>Status</h1>
<nav><a href="/">Status</a> <a href="/wifi">Wireless</a> <a href="/logout">Sign out</a></nav>
<iframe src="/status-frame" title="Device status" width="600" height="220"></iframe>
<h2>Connected devices</h2>
<table>
  <tr><th>Name</th><th>Address</th><th>Signal</th></tr>
  <tr><td>laptop</td><td>192.168.1.20</td><td>-48 dBm</td></tr>
  <tr><td>phone</td><td>192.168.1.31</td><td>-61 dBm</td></tr>
</table>"""

FRAME = """<!doctype html><title>status</title>
<table>
  <tr><th>Firmware</th><td>{firmware}</td></tr>
  <tr><th>Uptime</th><td>3 days</td></tr>
  <tr><th>WAN</th><td>connected</td></tr>
</table>"""

WIFI = """<!doctype html><title>Router - Wireless</title>
<h1>Wireless</h1>{saved}
<form method="post" action="/wifi">
  <label>Network name <input name="ssid" value="{ssid}"></label>
  <label>Channel <select name="channel">{channels}</select></label>
  <label><input type="checkbox" name="enabled" {checked}> Radio on</label>
  <fieldset><legend>Band</legend>
    <label><input type="radio" name="band" value="2.4" {b24}> 2.4 GHz</label>
    <label><input type="radio" name="band" value="5" {b5}> 5 GHz</label>
  </fieldset>
  <button type="submit">Save</button>
</form>"""


class RouterSite:
    def __init__(self, password: str | None = None) -> None:
        self.password = password or secrets.token_urlsafe(12)
        self.session = secrets.token_hex(16)
        self.firmware = "RT-1.4." + secrets.token_hex(3)
        self.state = {"ssid": "home-net", "channel": "6", "enabled": True, "band": "2.4"}
        self.posts: list[str] = []
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def _signed_in(self) -> bool:
                cookie = self.headers.get("Cookie", "")
                return f"rsid={site.session}" in cookie

            def _page(self, body: str, status: int = 200, headers: dict | None = None) -> None:
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def _redirect(self, where: str, cookie: str | None = None) -> None:
                headers = {"Location": where}
                if cookie:
                    headers["Set-Cookie"] = cookie
                self._page("", 303, headers)

            def _form(self) -> dict[str, str]:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n).decode()
                return {k: v[-1] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

            def do_GET(self) -> None:
                path = urllib.parse.urlsplit(self.path).path
                if path == "/login.html":
                    return self._page(LOGIN.format(error=""))
                if path == "/logout":
                    return self._redirect("/login.html", "rsid=; Max-Age=0; Path=/")
                if not self._signed_in():
                    return self._redirect("/login.html")
                if path == "/":
                    return self._page(HOME)
                if path == "/status-frame":
                    return self._page(FRAME.format(firmware=site.firmware))
                if path == "/wifi":
                    return self._page(site.wifi_page(saved=False))
                self._page("<title>Not found</title><p>Not found", 404)

            def do_POST(self) -> None:
                path = urllib.parse.urlsplit(self.path).path
                form = self._form()
                site.posts.append(path)
                if path == "/login":
                    if form.get("user") == "admin" and form.get("pass") == site.password:
                        return self._redirect("/", f"rsid={site.session}; Max-Age=86400; Path=/; HttpOnly")
                    return self._page(LOGIN.format(error="<p role=alert>Wrong username or password</p>"))
                if not self._signed_in():
                    return self._redirect("/login.html")
                if path == "/wifi":
                    site.state.update(ssid=form.get("ssid", ""), channel=form.get("channel", ""),
                                      enabled="enabled" in form, band=form.get("band", ""))
                    return self._page(site.wifi_page(saved=True))
                self._page("<title>Not found</title>", 404)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def wifi_page(self, saved: bool) -> str:
        s = self.state
        channels = "".join(
            f'<option value="{c}"{" selected" if c == s["channel"] else ""}>Channel {c}</option>'
            for c in ("1", "6", "11"))
        return WIFI.format(
            saved="<p role=status>Settings saved</p>" if saved else "",
            ssid=html.escape(s["ssid"]), channels=channels,
            checked="checked" if s["enabled"] else "",
            b24="checked" if s["band"] == "2.4" else "", b5="checked" if s["band"] == "5" else "")

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
