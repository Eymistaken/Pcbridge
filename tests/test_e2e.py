"""Uctan uca test: OAuth 2.1 akisi + MCP arac cagrisi.

Calistirma:
    PCBRIDGE_CONFIG=/tmp/pcb/config.toml python3 tests/test_e2e.py
Sunucunun ayri bir terminalde calisiyor olmasi gerekir.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import time
from urllib.parse import parse_qs, urlparse

import httpx

BASE = os.environ.get("PCBRIDGE_TEST_BASE", "http://127.0.0.1:8765")
PASSWORD = os.environ.get("PCBRIDGE_TEST_PASSWORD", "test-parola-1234567890")
STATIC = os.environ.get("PCBRIDGE_TEST_STATIC", "STATIC-TEST-TOKEN-abc123")
REDIRECT = "https://oauth-redirect.googleusercontent.com/r/test-project"

ok_count = 0
fail_count = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global ok_count, fail_count
    if cond:
        ok_count += 1
        print(f"  \033[32mPASS\033[0m  {name}")
    else:
        fail_count += 1
        print(f"  \033[31mFAIL\033[0m  {name}  {detail}")


def section(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")


def main() -> int:
    # trust_env=False: ortamdaki HTTP(S)_PROXY ayarlari yerel testi bozmasin
    c = httpx.Client(follow_redirects=False, timeout=30, trust_env=False)

    # ---------------------------------------------------------------- keşif
    section("1. Metadata ve saglik")
    r = c.get(f"{BASE}/healthz")
    check("/healthz 200", r.status_code == 200, str(r.status_code))

    r = c.get(f"{BASE}/.well-known/oauth-protected-resource")
    check("protected-resource metadata", r.status_code == 200, str(r.status_code))
    if r.status_code == 200:
        meta = r.json()
        check("resource alani var", "resource" in meta, json.dumps(meta))
        check(
            "authorization_servers alani var",
            bool(meta.get("authorization_servers")),
            json.dumps(meta),
        )

    r = c.get(f"{BASE}/.well-known/oauth-authorization-server")
    check("AS metadata", r.status_code == 200, str(r.status_code))
    as_meta = r.json() if r.status_code == 200 else {}
    check(
        "S256 PKCE destekleniyor",
        "S256" in (as_meta.get("code_challenge_methods_supported") or []),
        json.dumps(as_meta.get("code_challenge_methods_supported")),
    )
    check(
        "registration_endpoint var (DCR)",
        bool(as_meta.get("registration_endpoint")),
        json.dumps(as_meta),
    )

    section("2. Yetkisiz erisim reddediliyor mu")
    r = c.post(
        f"{BASE}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Accept": "application/json, text/event-stream"},
    )
    check("token'siz /mcp -> 401", r.status_code == 401, str(r.status_code))
    check(
        "WWW-Authenticate basligi var",
        "www-authenticate" in {k.lower() for k in r.headers},
        str(dict(r.headers)),
    )

    r = c.post(
        f"{BASE}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={
            "Authorization": "Bearer kesinlikle-yanlis-token",
            "Accept": "application/json, text/event-stream",
        },
    )
    check("yanlis token -> 401", r.status_code == 401, str(r.status_code))

    # ------------------------------------------------------------------ DCR
    section("3. Dynamic Client Registration")
    r = c.post(
        f"{BASE}/register",
        json={
            "client_name": "Gemini Spark (test)",
            "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        },
    )
    check("/register 200/201", r.status_code in (200, 201), f"{r.status_code} {r.text[:300]}")
    if r.status_code not in (200, 201):
        return 1
    reg = r.json()
    client_id = reg["client_id"]
    client_secret = reg.get("client_secret")
    check("client_id dondu", bool(client_id))

    # ------------------------------------------------------------ authorize
    section("4. Authorize -> parola sayfasi")
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    state = secrets.token_urlsafe(16)
    r = c.get(
        f"{BASE}/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": f"{BASE}/mcp",
        },
    )
    check("/authorize -> 302", r.status_code in (302, 303, 307), f"{r.status_code} {r.text[:200]}")
    loc = r.headers.get("location", "")
    check("/consent sayfasina yonlendi", "/consent?rid=" in loc, loc[:200])
    rid = parse_qs(urlparse(loc).query).get("rid", [""])[0]

    r = c.get(f"{BASE}/consent", params={"rid": rid})
    check("onay sayfasi aciliyor", r.status_code == 200, str(r.status_code))
    check("parola alani var", 'name="password"' in r.text)
    check("istemci adi gosteriliyor", "Gemini Spark (test)" in r.text)

    section("5. Yanlis parola reddediliyor")
    r = c.post(f"{BASE}/consent", data={"rid": rid, "password": "yanlis"})
    check("yanlis parola -> 401", r.status_code == 401, str(r.status_code))
    check("kod sizdirilmadi", "location" not in {k.lower() for k in r.headers})

    section("6. Dogru parola -> yetki kodu")
    r = c.post(f"{BASE}/consent", data={"rid": rid, "password": PASSWORD})
    check("dogru parola -> 303", r.status_code in (302, 303), f"{r.status_code} {r.text[:200]}")
    loc = r.headers.get("location", "")
    q = parse_qs(urlparse(loc).query)
    code = q.get("code", [""])[0]
    check("redirect_uri'ye donuldu", loc.startswith(REDIRECT), loc[:200])
    check("code parametresi var", bool(code))
    check("state korundu", q.get("state", [""])[0] == state)

    section("7. Token degisimi (PKCE)")
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    if client_secret:
        form["client_secret"] = client_secret
    r = c.post(f"{BASE}/token", data=form)
    check("/token 200", r.status_code == 200, f"{r.status_code} {r.text[:300]}")
    if r.status_code != 200:
        return 1
    tok = r.json()
    access = tok["access_token"]
    refresh = tok.get("refresh_token")
    check("access_token dondu", bool(access))
    check("refresh_token dondu", bool(refresh))
    check("token_type Bearer", tok.get("token_type", "").lower() == "bearer")

    section("8. Kod tekrar kullanilamiyor")
    r = c.post(f"{BASE}/token", data=form)
    check("ayni kod ikinci kez -> 4xx", 400 <= r.status_code < 500, str(r.status_code))

    section("9. Yanlis PKCE verifier reddediliyor")
    verifier2 = secrets.token_urlsafe(64)
    challenge2 = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier2.encode()).digest())
        .decode()
        .rstrip("=")
    )
    r = c.get(
        f"{BASE}/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "state": "s2",
            "code_challenge": challenge2,
            "code_challenge_method": "S256",
        },
    )
    rid2 = parse_qs(urlparse(r.headers.get("location", "")).query).get("rid", [""])[0]
    r = c.post(f"{BASE}/consent", data={"rid": rid2, "password": PASSWORD})
    code2 = parse_qs(urlparse(r.headers.get("location", "")).query).get("code", [""])[0]
    bad = {
        "grant_type": "authorization_code",
        "code": code2,
        "redirect_uri": REDIRECT,
        "client_id": client_id,
        "code_verifier": secrets.token_urlsafe(64),
    }
    if client_secret:
        bad["client_secret"] = client_secret
    r = c.post(f"{BASE}/token", data=bad)
    check("yanlis code_verifier -> 4xx", 400 <= r.status_code < 500, str(r.status_code))

    # ------------------------------------------------------------------- MCP
    section("10. MCP protokolu")
    hdr = {
        "Authorization": f"Bearer {access}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }

    def rpc(method: str, params: dict | None = None, sid: str | None = None):
        h = dict(hdr)
        if sid:
            h["mcp-session-id"] = sid
        resp = c.post(
            f"{BASE}/mcp",
            headers=h,
            json={"jsonrpc": "2.0", "id": int(time.time() * 1000) % 100000,
                  "method": method, "params": params or {}},
        )
        body = resp.text
        data = None
        if body.startswith("event:") or "data:" in body:
            for line in body.splitlines():
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    break
        else:
            try:
                data = resp.json()
            except Exception:
                pass
        return resp, data

    resp, data = rpc(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pcbridge-test", "version": "1.0"},
        },
    )
    check("initialize 200", resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}")
    session_id = resp.headers.get("mcp-session-id")
    check("mcp-session-id dondu", bool(session_id))
    if data:
        check(
            "sunucu adi pcbridge",
            (data.get("result", {}).get("serverInfo", {}).get("name")) == "pcbridge",
            json.dumps(data)[:300],
        )

    c.post(
        f"{BASE}/mcp",
        headers={**hdr, "mcp-session-id": session_id or ""},
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )

    resp, data = rpc("tools/list", sid=session_id)
    tools = (data or {}).get("result", {}).get("tools", [])
    names = {t["name"] for t in tools}
    check("tools/list calisti", resp.status_code == 200 and bool(tools), str(resp.status_code))
    for expected in (
        "agent_run",
        "job_status",
        "job_output",
        "job_list",
        "job_cancel",
        "tmux_start",
        "tmux_send",
        "tmux_capture",
        "shell_run",
        "fs_read",
        "fs_write",
        "fs_list",
        "system_status",
        "list_agents",
        "desktop_unlock",
        "desktop_lock",
        "mouse",
        "keyboard",
    ):
        check(f"arac mevcut: {expected}", expected in names, str(sorted(names)))

    # Masaustu araclarinin semasi -- Gemini bunlari dogru doldurabilmeli
    by_name = {t["name"]: t for t in tools}
    for tool_name, must_have in (
        ("mouse", ("action", "x", "y", "monitor", "force")),
        ("keyboard", ("action", "text", "keys", "raw", "force")),
        ("desktop_unlock", ("minutes", "reason")),
    ):
        props = by_name.get(tool_name, {}).get("inputSchema", {}).get("properties", {})
        for field_ in must_have:
            check(
                f"{tool_name}.{field_} parametresi var",
                field_ in props,
                str(sorted(props)),
            )
    for tool_name in ("mouse", "keyboard", "desktop_unlock"):
        ann = by_name.get(tool_name, {}).get("annotations", {}) or {}
        check(
            f"{tool_name} destructiveHint isaretli",
            ann.get("destructiveHint") is True,
            str(ann),
        )
    mouse_req = by_name.get("mouse", {}).get("inputSchema", {}).get("required", [])
    check("mouse.action zorunlu", "action" in mouse_req, str(mouse_req))
    check("mouse.x zorunlu DEGIL (scroll icin)", "x" not in mouse_req, str(mouse_req))
    desc = str(by_name.get("keyboard", {}).get("description", ""))
    check(
        "keyboard aciklamasi 'ne zaman kullanilir' iceriyor",
        "Use when" in desc,
        desc[:160],
    )

    # agent_run'in model/effort semasi -- Gemini bu alanlari gorebilmeli
    schema = next(
        (t.get("inputSchema", {}) for t in tools if t["name"] == "agent_run"), {}
    )
    props = schema.get("properties", {})
    required = schema.get("required", [])
    for field_ in ("model", "effort"):
        check(f"agent_run.{field_} parametresi var", field_ in props, str(sorted(props)))
        desc = str(props.get(field_, {}).get("description", ""))
        check(
            f"agent_run.{field_} aciklamasinda gecerli degerler sayiliyor",
            len(desc) > 40,
            desc,
        )
    check("agent_run.agent artik zorunlu degil", "agent" not in required, str(required))
    check("agent_run.prompt hala zorunlu", "prompt" in required, str(required))

    section("11. Arac cagrilari")

    def call(name: str, args: dict, sid: str | None = session_id) -> str:
        _resp, d = rpc("tools/call", {"name": name, "arguments": args}, sid=sid)
        content = (d or {}).get("result", {}).get("content", [])
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))

    out = call("shell_run", {"command": "echo merhaba-dunya", "workdir": "/tmp/pcb/work"})
    check("shell_run calisti", "merhaba-dunya" in out, out[:300])

    out = call("fs_write", {"path": "/tmp/pcb/work/deneme.txt", "content": "satir1\nsatir2\n"})
    check("fs_write calisti", "Yazildi" in out, out[:200])
    out = call("fs_read", {"path": "/tmp/pcb/work/deneme.txt"})
    check("fs_read calisti", "satir2" in out, out[:200])
    out = call("fs_list", {"path": "/tmp/pcb/work"})
    check("fs_list calisti", "deneme.txt" in out, out[:300])
    out = call("fs_search", {"query": "satir2", "path": "/tmp/pcb/work"})
    check("fs_search calisti", "deneme.txt" in out, out[:300])

    out = call("list_agents", {})
    check("list_agents calisti", "claude" in out, out[:300])
    check("list_agents model tablosu gosteriyor", "modeller:" in out, out[:600])
    check("list_agents varsayilani gosteriyor", "varsayilan:" in out, out[:600])

    out = call("system_status", {})
    check("system_status calisti", "Bilgisayar durumu" in out, out[:300])
    check("system_status masaustu satirini gosteriyor", "asaustu" in out, out[:2000])

    # Masaustu araclari: test yapilandirmasinda [desktop] tanimli degil, yani
    # varsayilan enabled = false gecerli ve HICBIRI girdi gondermemeli.
    # Bu testler gercek klavye/fareye dokunmaz.
    out = call("mouse", {"action": "move", "x": 10, "y": 10})
    check("mouse kapaliyken reddediyor", "⛔" in out, out[:200])
    check("mouse reddi nasil acilacagini soyluyor", "enabled" in out, out[:200])
    out = call("keyboard", {"action": "type", "text": "bu-yazilmamali"})
    check("keyboard kapaliyken reddediyor", "⛔" in out, out[:200])
    out = call("desktop_unlock", {"minutes": 1})
    check("desktop_unlock kapaliyken reddediyor", "⛔" in out, out[:200])
    out = call("desktop_lock", {})
    check("desktop_lock kapaliyken de cevap veriyor", "kontrolu" in out, out[:200])

    section("12. Ajan calistirma ve is takibi")
    out = call(
        "agent_run",
        {
            "agent": "claude",
            "prompt": "merhaba testi",
            "workdir": "/tmp/pcb/work",
            "wait_seconds": 20,
        },
    )
    check("agent_run bitti", "finished" in out, out[:500])
    check("adimlar ayristirildi", "arac: Bash" in out, out[:600])
    check("oturum kimligi cikarildi", "sess-abc-123" in out, out[:600])
    check("sonuc metni var", "Istek tamamlandi: merhaba testi" in out, out[:600])
    check("maliyet gosterildi", "0.0123" in out, out[:800])

    job_id = ""
    for token_ in out.split():
        if token_.startswith("**") and "-" in token_:
            job_id = token_.strip("*")
            break
    check("job_id ayiklandi", bool(job_id), out[:200])

    if job_id:
        out = call("job_status", {"job_id": job_id})
        check("job_status calisti", "finished" in out, out[:300])
        out = call("job_output", {"job_id": job_id})
        check("job_output ham log verdi", "session_id" in out, out[:300])

    out = call("job_list", {"limit": 5})
    check("job_list calisti", "job_id" in out, out[:300])

    section("13. Arka plan shell isi")
    out = call(
        "shell_run_background",
        {"command": "sleep 2; echo arkaplan-bitti", "workdir": "/tmp/pcb/work"},
    )
    bg_id = out.split("`")[1] if "`" in out else ""
    check("arka plan isi basladi", bool(bg_id), out[:200])
    if bg_id:
        out = call("job_status", {"job_id": bg_id, "wait_seconds": 10})
        check("arka plan isi bitti", "arkaplan-bitti" in out, out[:400])

    section("14. tmux canli oturum")
    out = call("tmux_start", {"session": "pcbtest", "workdir": "/tmp/pcb/work"})
    check("tmux oturumu acildi", "olusturuldu" in out or "zaten acik" in out, out[:300])
    out = call("tmux_send", {"session": "pcbtest", "text": "echo tmux-calisiyor",
                             "capture_after_seconds": 3})
    check("tmux_send ekrani okudu", "tmux-calisiyor" in out, out[:400])
    out = call("tmux_list", {})
    check("tmux_list gosteriyor", "pcbtest" in out, out[:300])
    out = call("tmux_kill", {"session": "pcbtest"})
    check("tmux oturumu kapatildi", "kapatildi" in out, out[:200])

    section("15. Statik token ve refresh token")
    if STATIC:
        r = c.post(
            f"{BASE}/mcp",
            headers={
                "Authorization": f"Bearer {STATIC}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "curl", "version": "1"},
                },
            },
        )
        check("statik token kabul edildi", r.status_code == 200, str(r.status_code))

    if refresh:
        form = {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id,
        }
        if client_secret:
            form["client_secret"] = client_secret
        r = c.post(f"{BASE}/token", data=form)
        check("refresh token calisti", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
        if r.status_code == 200:
            new = r.json()
            check("yeni access_token dondu", bool(new.get("access_token")))
            check(
                "refresh token korundu (rotation yok)",
                new.get("refresh_token") == refresh,
                f"{new.get('refresh_token')} != {refresh}",
            )
            r2 = c.post(
                f"{BASE}/mcp",
                headers={
                    "Authorization": f"Bearer {new['access_token']}",
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"},
                    },
                },
            )
            check("yenilenen token ile MCP erisimi", r2.status_code == 200, str(r2.status_code))

    section("16. client_secret_basic (Google'in kullandigi yontem)")
    # Google, RFC 6749 2.3.1'e uyup client_id'yi SADECE Basic basliginda
    # gonderiyor. MCP SDK'si onu form gövdesinde arıyor; BasicAuthFormShim
    # aradaki farki kapatiyor. Bu bolum o koprunun bozulmadigini dogrular.
    def basic_flow(auth_method: str):
        reg_ = c.post(
            f"{BASE}/register",
            json={
                "client_name": "Google",
                "redirect_uris": [REDIRECT],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": auth_method,
            },
        ).json()
        ver = secrets.token_urlsafe(64)
        chal = (
            base64.urlsafe_b64encode(hashlib.sha256(ver.encode()).digest())
            .decode()
            .rstrip("=")
        )
        resp = c.get(
            f"{BASE}/authorize",
            params={
                "response_type": "code",
                "client_id": reg_["client_id"],
                "redirect_uri": REDIRECT,
                "state": "s",
                "code_challenge": chal,
                "code_challenge_method": "S256",
            },
        )
        rid_ = parse_qs(urlparse(resp.headers["location"]).query)["rid"][0]
        resp = c.post(f"{BASE}/consent", data={"rid": rid_, "password": PASSWORD})
        code_ = parse_qs(urlparse(resp.headers["location"]).query)["code"][0]
        return reg_, code_, ver

    reg2, code3, ver3 = basic_flow("client_secret_basic")
    basic = base64.b64encode(
        f"{reg2['client_id']}:{reg2['client_secret']}".encode()
    ).decode()
    r = c.post(
        f"{BASE}/token",
        data={
            "grant_type": "authorization_code",
            "code": code3,
            "redirect_uri": REDIRECT,
            "code_verifier": ver3,
        },
        headers={"Authorization": f"Basic {basic}"},
    )
    check("Basic baslik + gövdede client_id YOK -> 200", r.status_code == 200, r.text[:300])
    if r.status_code == 200:
        t2 = r.json()
        rr = c.post(
            f"{BASE}/token",
            data={"grant_type": "refresh_token", "refresh_token": t2["refresh_token"]},
            headers={"Authorization": f"Basic {basic}"},
        )
        check("Basic ile refresh -> 200", rr.status_code == 200, rr.text[:200])

    reg3, code4, ver4 = basic_flow("client_secret_basic")
    bad_basic = base64.b64encode(f"{reg3['client_id']}:yanlis".encode()).decode()
    r = c.post(
        f"{BASE}/token",
        data={
            "grant_type": "authorization_code",
            "code": code4,
            "redirect_uri": REDIRECT,
            "code_verifier": ver4,
        },
        headers={"Authorization": f"Basic {bad_basic}"},
    )
    check("yanlis client_secret hala reddediliyor", 400 <= r.status_code < 500, str(r.status_code))

    section("17. Metadata issuer bicimi (RFC 8414)")
    meta = c.get(f"{BASE}/.well-known/oauth-authorization-server").json()
    check(
        "issuer sonunda egik cizgi yok",
        not meta.get("issuer", "").endswith("/"),
        meta.get("issuer", ""),
    )
    pr = c.get(f"{BASE}/.well-known/oauth-protected-resource/mcp").json()
    check(
        "authorization_servers sonunda egik cizgi yok",
        all(not s.endswith("/") for s in pr.get("authorization_servers", [])),
        str(pr.get("authorization_servers")),
    )

    print(f"\n\033[1mSonuc: {ok_count} basarili, {fail_count} basarisiz\033[0m")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
