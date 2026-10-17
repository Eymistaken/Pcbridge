"""Remote access safety against a real daemon: `[tools] remote_profile` per
path, and `pcbridge remote clients` / `revoke` against real OAuth tokens.

A throwaway config, state directory, socket and HTTP port; it never touches
the user's daemon, config or OAuth database, and needs no display.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import signal
import stat
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pcbridge.tools import tools_in_profile  # noqa: E402
from tests.integration.test_daemon import Client, _text  # noqa: E402

PASSWORD = "remote-access-test-password"
STATIC = "remote-access-test-static-token"
REDIRECT = "https://client.example/callback"

# pcbridge.app configures INFO logging on import; httpx would log every request.
logging.getLogger("httpx").setLevel(logging.WARNING)

CONFIG = """\
config_version = 2
public_url = "http://localhost:{port}"
port = {port}
default_agent = "claude"

[auth]
password = "{password}"
static_token = "{static}"

[paths]
state_dir = "{state}"
default_workdir = "{work}"

[desktop]
enabled = false

[agents.claude]
command = ["true"]

[tools]
{tools}
"""


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class RemoteAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pcbr-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "state").mkdir()
        (self.tmp / "work").mkdir()
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.cfg = self.tmp / "config.toml"
        self.sock = self.tmp / "mcp.sock"
        self.env = dict(os.environ)
        for k in list(self.env):
            if k.startswith(("CLAUDE", "MCP_", "PCBRIDGE_")):
                self.env.pop(k)
        self.env.update({
            "PCBRIDGE_CONFIG": str(self.cfg),
            "PCBRIDGE_SOCKET": str(self.sock),
            "XDG_DATA_HOME": str(self.tmp / "data"),
            "NO_COLOR": "1",
        })
        self.daemon: subprocess.Popen | None = None
        self.addCleanup(self._stop_daemon)
        self.http = httpx.Client(timeout=30, follow_redirects=False)
        self.addCleanup(self.http.close)

    def _write_config(self, tools: str, password: str = PASSWORD) -> None:
        self.cfg.write_text(CONFIG.format(
            port=self.port, password=password, static=STATIC, state=self.tmp / "state",
            work=self.tmp / "work", tools=tools))
        os.chmod(self.cfg, 0o600)

    def _start_daemon(self) -> None:
        log = open(self.tmp / "daemon.log", "ab")
        self.addCleanup(log.close)
        self.daemon = subprocess.Popen(
            [sys.executable, "-m", "pcbridge", "serve", "--socket", str(self.sock)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=self.env, cwd=str(ROOT),
        )
        deadline = time.monotonic() + 30
        while True:
            try:
                if self.http.get(f"{self.base}/healthz").status_code == 200 and self.sock.exists():
                    return
            except httpx.HTTPError:
                pass
            if self.daemon.poll() is not None or time.monotonic() > deadline:
                raise AssertionError((self.tmp / "daemon.log").read_text()[-2000:])
            time.sleep(0.1)

    def _stop_daemon(self) -> None:
        if self.daemon and self.daemon.poll() is None:
            self.daemon.send_signal(signal.SIGTERM)
            try:
                self.daemon.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.daemon.kill()
        self.daemon = None

    def _cli(self, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "pcbridge", *args], input=stdin, text=True,
                              capture_output=True, env=self.env, cwd=str(ROOT), timeout=60)

    # -- MCP over HTTP -----------------------------------------------------------

    def _rpc(self, token: str, method: str, params: dict | None = None,
             sid: str | None = None) -> tuple[httpx.Response, dict | None]:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if sid:
            headers["mcp-session-id"] = sid
        r = self.http.post(f"{self.base}/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": secrets.randbelow(10**6), "method": method, "params": params or {}})
        data = None
        for line in r.text.splitlines():
            if line.startswith("data:"):
                data = json.loads(line[5:].strip())
                break
        else:
            try:
                data = r.json()
            except ValueError:
                pass
        return r, data

    def _session(self, token: str) -> str:
        r, _ = self._rpc(token, "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "remote-test", "version": "1"}})
        self.assertEqual(r.status_code, 200, r.text[:300])
        sid = r.headers["mcp-session-id"]
        self.http.post(f"{self.base}/mcp", headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream", "mcp-session-id": sid},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        return sid

    def _http_tools(self, token: str = STATIC) -> set[str]:
        sid = self._session(token)
        _, data = self._rpc(token, "tools/list", sid=sid)
        return {t["name"] for t in data["result"]["tools"]}

    def _socket_tools(self) -> tuple[Client, set[str]]:
        c = Client(self.env)
        self.addCleanup(c.close)
        c.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "local-test", "version": "1"}})
        c.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return c, {t["name"] for t in c.call("tools/list")["result"]["tools"]}

    # -- OAuth -----------------------------------------------------------------

    def _oauth(self, name: str) -> dict:
        """Register `name`, approve it with the password and return its tokens."""
        reg = self.http.post(f"{self.base}/register", json={
            "client_name": name, "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post"}).json()
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        r = self.http.get(f"{self.base}/authorize", params={
            "response_type": "code", "client_id": reg["client_id"], "redirect_uri": REDIRECT,
            "state": "s", "code_challenge": challenge, "code_challenge_method": "S256"})
        rid = parse_qs(urlparse(r.headers["location"]).query)["rid"][0]
        r = self.http.post(f"{self.base}/consent", data={"rid": rid, "password": PASSWORD})
        code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
        r = self.http.post(f"{self.base}/token", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
            "code_verifier": verifier, "client_id": reg["client_id"],
            "client_secret": reg["client_secret"]})
        self.assertEqual(r.status_code, 200, r.text[:300])
        return {**reg, **r.json()}

    def _refresh(self, client: dict) -> httpx.Response:
        return self.http.post(f"{self.base}/token", data={
            "grant_type": "refresh_token", "refresh_token": client["refresh_token"],
            "client_id": client["client_id"], "client_secret": client["client_secret"]})

    # -- remote_profile ------------------------------------------------------------

    def test_remote_profile_gives_each_path_its_own_tools(self) -> None:
        self._write_config('profile = "core"\nremote_profile = "readonly"')
        self._start_daemon()
        c, local = self._socket_tools()
        self.assertEqual(local, set(tools_in_profile("core")))
        self.assertIn("shell_run", local)

        remote = self._http_tools()
        self.assertEqual(remote, set(tools_in_profile("readonly")))
        self.assertNotIn("shell_run", remote)

        # A tool outside remote_profile is refused by name, not only hidden.
        sid = self._session(STATIC)
        _, data = self._rpc(STATIC, "tools/call", {"name": "shell_run",
                                                   "arguments": {"command": "echo leaked"}}, sid=sid)
        self.assertTrue(data["result"]["isError"], data)
        text = json.dumps(data)
        self.assertNotIn("leaked\\n", text)
        self.assertIn("not offered to remote clients", text)
        self.assertIn('remote_profile = \\"readonly\\"', text)
        # ...while one inside it runs.
        _, data = self._rpc(STATIC, "tools/call", {"name": "fs_list",
                                                   "arguments": {"path": str(self.tmp)}}, sid=sid)
        self.assertFalse(data["result"].get("isError"), data)
        # The local client keeps the shell.
        self.assertIn("local-ok", _text(c.tool("shell_run", {"command": "echo local-ok"})))
        # The refusal is audited, with the tool's name and no arguments.
        audit = (self.tmp / "state" / "audit.log").read_text()
        self.assertIn('"event": "tool_refused"', audit)
        self.assertIn('"tool": "shell_run"', audit)
        self.assertNotIn("echo leaked", audit)

    def test_remote_profile_same_follows_profile(self) -> None:
        self._write_config('profile = "core"')
        self._start_daemon()
        _, local = self._socket_tools()
        self.assertEqual(self._http_tools(), local)
        self.assertEqual(local, set(tools_in_profile("core")))

    def test_a_wider_remote_profile_does_not_widen_the_local_one(self) -> None:
        self._write_config('profile = "readonly"\nremote_profile = "core"')
        self._start_daemon()
        c, local = self._socket_tools()
        self.assertEqual(local, set(tools_in_profile("readonly")))
        self.assertEqual(self._http_tools(), set(tools_in_profile("core")))
        res = c.tool("shell_run", {"command": "echo nope"})
        self.assertIn("not offered to local clients", json.dumps(res))

    # -- revoke ----------------------------------------------------------------------

    def test_revoke_by_name_cuts_the_client_off(self) -> None:
        self._write_config('profile = "core"')
        self._start_daemon()
        phone = self._oauth("Phone app")
        other = self._oauth("Laptop app")
        self._session(phone["access_token"])

        out = self._cli("remote", "clients", "--json")
        self.assertEqual(out.returncode, 0, out.stderr)
        listed = {g["name"]: g for g in json.loads(out.stdout)["clients"]}
        self.assertEqual(listed["Phone app"]["connected"], 1)
        self.assertNotIn(phone["access_token"], out.stdout)
        self.assertNotIn(phone["refresh_token"], out.stdout)

        # Without a terminal and without --yes nothing is revoked.
        out = self._cli("remote", "revoke", "phone APP")
        self.assertEqual(out.returncode, 1)
        self.assertEqual(self._rpc(phone["access_token"], "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "x", "version": "1"}})[0].status_code, 200)

        out = self._cli("remote", "revoke", "phone APP", "--yes")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("revoked Phone app", out.stdout)

        r, _ = self._rpc(phone["access_token"], "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "x", "version": "1"}})
        self.assertEqual(r.status_code, 401)
        self.assertGreaterEqual(self._refresh(phone).status_code, 400)
        # The other client is untouched.
        self.assertEqual(self._refresh(other).status_code, 200)
        names = {g["name"] for g in json.loads(self._cli("remote", "clients", "--json").stdout)["clients"]}
        self.assertEqual(names, {"Laptop app"})
        audit = (self.tmp / "state" / "audit.log").read_text()
        self.assertIn('"event": "remote_client_revoked"', audit)
        self.assertNotIn(phone["refresh_token"], audit)

        out = self._cli("remote", "revoke", "nobody", "--yes")
        self.assertEqual(out.returncode, 1)
        self.assertIn("Laptop app", out.stdout)

    def test_password_change_keeps_refresh_tokens_and_offers_revoke_all(self) -> None:
        self._write_config('profile = "core"')
        self._start_daemon()
        a = self._oauth("App A")
        b = self._oauth("App B")

        out = self._cli("set", "auth.password", "--stdin", stdin="a-brand-new-password\n")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("still hold tokens", out.stdout)
        self.assertIn("pcbridge remote revoke --all", out.stdout)
        self.assertNotIn("a-brand-new-password", out.stdout)
        self._stop_daemon()
        self._start_daemon()

        # The measured fact: a new password does not end existing sessions.
        r = self._refresh(a)
        self.assertEqual(r.status_code, 200, r.text[:200])
        self.assertEqual(self._refresh(b).status_code, 200)

        out = self._cli("remote", "revoke", "--all", "--yes")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        for client in (a, b):
            self.assertGreaterEqual(self._refresh(client).status_code, 400)
        r, _ = self._rpc(r.json()["access_token"], "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "x", "version": "1"}})
        self.assertEqual(r.status_code, 401)
        out = self._cli("remote", "clients")
        self.assertIn("no remote client holds a token", out.stdout)

    def test_set_password_with_revoke_remote(self) -> None:
        self._write_config('profile = "core"')
        self._start_daemon()
        a = self._oauth("App A")
        out = self._cli("set", "auth.password", "--stdin", "--revoke-remote",
                        stdin="another-new-password\n")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("revoked every remote client", out.stdout)
        self.assertGreaterEqual(self._refresh(a).status_code, 400)

    # -- stored secrets ----------------------------------------------------------

    def _legacy_db(self) -> dict[str, str]:
        """An oauth.db as pcbridge wrote it before tokens were hashed."""
        from mcp.server.auth.provider import AccessToken, RefreshToken
        from mcp.shared.auth import OAuthClientInformationFull

        from pcbridge.auth import SCHEMA

        old = {"access": "pcb_at_" + secrets.token_urlsafe(40),
               "refresh": "pcb_rt_" + secrets.token_urlsafe(40),
               "client_id": "legacy-client", "client_secret": "legacy-client-secret"}
        client = OAuthClientInformationFull(
            client_id=old["client_id"], client_secret=old["client_secret"], client_name="Legacy App",
            redirect_uris=[REDIRECT], grant_types=["authorization_code", "refresh_token"],
            response_types=["code"], token_endpoint_auth_method="client_secret_post")
        exp = int(time.time()) + 3600
        at = AccessToken(token=old["access"], client_id=old["client_id"], scopes=[], expires_at=exp)
        rt = RefreshToken(token=old["refresh"], client_id=old["client_id"], scopes=[], expires_at=None)
        import sqlite3
        conn = sqlite3.connect(self.tmp / "state" / "oauth.db")
        with conn:
            conn.executescript(SCHEMA)
            conn.execute("INSERT INTO clients VALUES (?,?,?)",
                         (old["client_id"], client.model_dump_json(), time.time()))
            conn.execute("INSERT INTO access_tokens VALUES (?,?,?,?)",
                         (old["access"], old["client_id"], at.model_dump_json(), exp))
            conn.execute("INSERT INTO refresh_tokens VALUES (?,?,?,?,?)",
                         (old["refresh"], old["client_id"], rt.model_dump_json(), None, old["access"]))
        conn.close()
        return old

    def _db_bytes(self) -> bytes:
        state = self.tmp / "state"
        return b"".join(p.read_bytes() for p in state.glob("oauth.db*") if p.is_file())

    def test_secrets_and_tokens_are_stored_as_hashes(self) -> None:
        self._write_config('profile = "core"')
        old = self._legacy_db()
        self._start_daemon()

        # The daemon replaced the plaintext secrets of the file with hashes.
        text = self.cfg.read_text()
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn(STATIC, text)
        self.assertIn('password = "scrypt$', text)
        self.assertIn('static_token = "sha256$', text)
        self.assertEqual(stat.S_IMODE(self.cfg.stat().st_mode), 0o600)
        self.assertIn("as hashes", (self.tmp / "daemon.log").read_text())

        # ...and both still work: the static token, and the password on the consent page.
        self.assertIn("shell_run", self._http_tools(STATIC))
        r, _ = self._rpc("not-" + STATIC, "initialize", {})
        self.assertEqual(r.status_code, 401)
        app = self._oauth("App A")
        self.assertIn("shell_run", self._http_tools(app["access_token"]))

        # Tokens issued before the change still work: they were hashed in place.
        self.assertIn("shell_run", self._http_tools(old["access"]))
        r = self._refresh({**old, "refresh_token": old["refresh"]})
        self.assertEqual(r.status_code, 200, r.text[:300])
        renewed = r.json()
        self.assertEqual(renewed["refresh_token"], old["refresh"])
        self.assertIn("shell_run", self._http_tools(renewed["access_token"]))
        # The access token the refresh replaced is gone.
        r, _ = self._rpc(old["access"], "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "x", "version": "1"}})
        self.assertEqual(r.status_code, 401)

        # Nothing in the database files is a usable token.
        raw = self._db_bytes()
        for token in (old["access"], old["refresh"], app["access_token"], app["refresh_token"],
                      renewed["access_token"]):
            self.assertNotIn(token.encode(), raw)

        # A wrong password is still refused.
        reg = self.http.post(f"{self.base}/register", json={
            "client_name": "Wrong", "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post"}).json()
        r = self.http.get(f"{self.base}/authorize", params={
            "response_type": "code", "client_id": reg["client_id"], "redirect_uri": REDIRECT,
            "state": "s", "code_challenge": "x" * 43, "code_challenge_method": "S256"})
        rid = parse_qs(urlparse(r.headers["location"]).query)["rid"][0]
        r = self.http.post(f"{self.base}/consent", data={"rid": rid, "password": PASSWORD + "x"})
        self.assertEqual(r.status_code, 401)

        # Revoking still finds the hashed tokens.
        out = self._cli("remote", "revoke", "App A", "--yes")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertGreaterEqual(self._refresh(app).status_code, 400)

        # A restart finds nothing left to hash, and the tokens keep working.
        self._stop_daemon()
        before = self.cfg.read_text()
        self._start_daemon()
        self.assertEqual(self.cfg.read_text(), before)
        self.assertIn("shell_run", self._http_tools(STATIC))
        self.assertIn("shell_run", self._http_tools(renewed["access_token"]))

    def test_no_password_means_the_consent_page_refuses(self) -> None:
        self._write_config('profile = "core"', password="")
        self._start_daemon()
        reg = self.http.post(f"{self.base}/register", json={
            "client_name": "Phone", "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post"}).json()
        r = self.http.get(f"{self.base}/authorize", params={
            "response_type": "code", "client_id": reg["client_id"], "redirect_uri": REDIRECT,
            "state": "s", "code_challenge": "x" * 43, "code_challenge_method": "S256"})
        rid = parse_qs(urlparse(r.headers["location"]).query)["rid"][0]
        r = self.http.get(f"{self.base}/consent", params={"rid": rid})
        self.assertEqual(r.status_code, 403)
        self.assertIn("Remote access is off", r.text)
        self.assertIn("pcbridge remote start", r.text)
        # An empty password approves nothing either.
        r = self.http.post(f"{self.base}/consent", data={"rid": rid, "password": ""})
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("location", r.headers)
        # Local clients and the static token do not need it.
        _, local = self._socket_tools()
        self.assertIn("shell_run", local)
        self.assertIn("shell_run", self._http_tools(STATIC))

    def test_a_new_password_works_without_a_restart(self) -> None:
        self._write_config('profile = "core"', password="")
        self._start_daemon()
        pid = self.daemon.pid
        out = self._cli("set", "auth.password", "--generate")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("only time it is shown", out.stdout + out.stderr)
        self.assertIn(f"http://localhost:{self.port}/mcp", out.stdout)
        shown = re.search(r"Password:\s+(\S+)", out.stdout).group(1)
        self.assertEqual(len(shown.split("-")), 6)
        self.assertNotIn(shown, self.cfg.read_text())
        global PASSWORD
        old, PASSWORD = PASSWORD, shown
        try:
            app = self._oauth("Phone")  # the daemon was not restarted
        finally:
            PASSWORD = old
        self.assertEqual(self.daemon.pid, pid)
        self.assertIn("fs_list", self._http_tools(app["access_token"]))


if __name__ == "__main__":
    unittest.main()
