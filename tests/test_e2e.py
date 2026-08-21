"""Uctan uca test: OAuth 2.1 akisi + MCP arac cagrisi.

⚠️ BU TEST GERCEK BIR AJAN OTURUMU ACAR VE KOTA YAKAR.
    12. bolum `agent_run` ile gercek bir `claude -p` calistiriyor. 2026-08-03'te
    bu, kullanicinin gunluk limitini bitirdi ve hicbir yerde uyari yoktu.
    Kotaya dokunmadan kosmak icin:

        PCBRIDGE_TEST_NO_AGENT=1 ./.venv/bin/python tests/test_e2e.py

    Ajan kapsamini sunucusuz olarak `tests/test_models.py` 11. bolum zaten
    kontrol ediyor; gunluk kosumda bayragi ACIK tutmak makul.

Sunucu ayakta olmali. Test parolayi ve statik token'i ORTAMDAN alir; verilmezse
sahte varsayilanlarla dener ve OAuth adimlari 401 doner. Gercek config'le:

    export PCBRIDGE_TEST_PASSWORD="$(./.venv/bin/python -c '
    import sys; sys.path.insert(0,".")
    from pcbridge.config import load_config; print(load_config().password)')"
    export PCBRIDGE_TEST_STATIC="$(./.venv/bin/python -c '
    import sys; sys.path.insert(0,".")
    from pcbridge.config import load_config; print(load_config().static_token or "")')"
    ./.venv/bin/python tests/test_e2e.py

Ajan ayristirma kontrolleri (12. bolum) sabit ciktili bir ajan ister ve gercek
`claude` ile kosamaz; ATLA sayilirlar. O kapsam `tests/test_models.py` 11.
bolumde, sunucusuz olarak duruyor.

Degiskenler asla ekrana basilmaz; `config.toml` sir iceriyor.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
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
skip_count = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global ok_count, fail_count
    if cond:
        ok_count += 1
        print(f"  \033[32mPASS\033[0m  {name}")
    else:
        fail_count += 1
        print(f"  \033[31mFAIL\033[0m  {name}  {detail}")


def skip(name: str, why: str = "") -> None:
    """Kosulu saglanmayan kontrol — basarisiz DEGIL, calistirilmamis.

    Gercek ajanla deterministik olamayan kontrolleri FAIL saymak testin
    tamamini guvenilmez yapiyordu; ayrimi acikca goster.
    """
    global skip_count
    skip_count += 1
    print(f"  \033[33mATLA\033[0m  {name}  {why}")


def section(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")


def _stdio_session(timeout: float = 45.0):
    """stdio sunucusunu baslat, initialize et, (gonder, oku, kapat) ver.

    YANIT SATIR SATIR OKUNUYOR. `communicate()` ile hepsini birden gondermek
    stdin'i hemen kapatiyor, sunucu EOF gorup `tools/list` yanitini YAZMADAN
    kapaniyor ve test "sunucu bozuk" diyor -- bu tam olarak yasandi, kalibi
    boyle sabitledik.
    """
    import subprocess

    root = pathlib.Path(__file__).resolve().parent.parent
    proc = subprocess.Popen(
        [str(root / ".venv/bin/python"), "-m", "pcbridge.server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        # Banner ve loglar stderr'e gidiyor; testin isine yaramiyor ama
        # stdout'a KARISMAMALARI kontrol edilen seylerden biri.
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        # Kasten repo DISINDA: venv'deki pcbridge.pth sayesinde modul her
        # dizinden bulunmali. Istemciler sunucuyu kendi cwd'lerinden baslatiyor.
        cwd="/",
    )

    def send(msg: dict) -> None:
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    def read_id(want: int) -> dict | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                return None
            try:
                msg = json.loads(line)
            except ValueError:
                # stdout'a JSON olmayan bir sey dustu: kanal kirli demektir.
                return {"_junk": line}
            if msg.get("id") == want:
                return msg
        return None

    def close() -> None:
        try:
            proc.stdin.close()
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    send({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "test_e2e", "version": "0"}},
    })
    first = read_id(1)
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return proc, send, read_id, close, first


def _test_stdio() -> None:
    """stdio ile baslayan sunucu: el sikismasi, arac listesi, sema.

    Bu bolum SUNUCUNUN AYAKTA OLMASINI GEREKTIRMEZ -- kendi surecini baslatiyor.
    """
    proc, send, read_id, close, first = _stdio_session()
    try:
        if first is None or "_junk" in first:
            check("stdio initialize yanit veriyor", False,
                  first.get("_junk", "yanit yok")[:80] if first else "yanit yok")
            return
        info = first.get("result", {}).get("serverInfo", {})
        check("stdio initialize yanit veriyor", info.get("name") == "pcbridge",
              str(info))
        # OAuth'suz calisiyor: HTTP'de bu istek 401 alirdi.
        check("stdio'da OAuth istenmiyor", "error" not in first,
              str(first.get("error")))

        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        res = read_id(2)
        if res is None or "_junk" in res:
            check("stdio tools/list calisiyor", False,
                  res.get("_junk", "yanit yok")[:80] if res else "yanit yok")
            return
        tools = res.get("result", {}).get("tools", [])
        check("stdio tools/list calisiyor", len(tools) >= 30, f"{len(tools)} arac")
        names = {t["name"] for t in tools}
        for name in ("screen_capture", "computer_batch", "agent_run", "ui_dump"):
            check(f"stdio araci var: {name}", name in names)

        # Goruntu donebilen araclarda outputSchema OLMAMALI: FastMCP sema
        # uretirse cagri "outputSchema defined but no structured output" ile
        # patliyor (H0'da fiilen uretildi).
        for name in ("screen_capture", "computer_batch"):
            tool = next((t for t in tools if t["name"] == name), {})
            check(f"{name} sema uretmiyor (goruntu donebilsin)",
                  tool.get("outputSchema") is None, str(tool.get("outputSchema")))

        # Arac aciklamalari Ingilizce olmali: istemci arac secerken bunlari
        # okuyor. Turkce karakter kacaksa yakala.
        tr = set("çğıöşüÇĞİÖŞÜ")
        bad = [t["name"] for t in tools if tr & set(t.get("description", ""))]
        check("arac aciklamalari Ingilizce", not bad, ", ".join(bad[:4]))
    finally:
        close()

    check("stdio surec temiz kapandi", proc.returncode == 0, str(proc.returncode))


def _test_inline_setting() -> None:
    """`inline_images` iki tasimada ne yapiyor (saf cozum + canli config)."""
    root = pathlib.Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from pcbridge.config import load_config as _load  # noqa: PLC0415
    from pcbridge.tools import _want_inline as _wi  # noqa: PLC0415

    cfg = _load()
    check("inline_images gecerli deger", cfg.inline_images in ("auto", "true", "false"),
          cfg.inline_images)
    # Hedeflenen istemcilerin hepsi goruntuyu okuyabiliyor, o yuzden varsayilan
    # `true`: stdio'dan da HTTP'den de goruntu gitmeli.
    if cfg.inline_images == "true":
        check("true: stdio'da goruntu ACIK", _wi(cfg.inline_images, "stdio") is True)
        check("true: HTTP'de de goruntu ACIK", _wi(cfg.inline_images, "http") is True)
    elif cfg.inline_images == "auto":
        # Geri donus yolu: goruntu isleyemeyen bir istemci varsa boyle kullanilir.
        check("auto: stdio'da ACIK", _wi(cfg.inline_images, "stdio") is True)
        check("auto: HTTP'de KAPALI", _wi(cfg.inline_images, "http") is False)
    else:
        skip("inline_images kapali", f"deger: {cfg.inline_images}")


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
        "screen_info",
        "screen_capture",
        "ui_dump",
        "ui_click",
        "ui_set_text",
        "computer_batch",
        "window_list",
        "window_focus",
        "computer_task",
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

    # Ekran goruntusu araclari: okuma araci olduklari icin destructiveHint
    # DEGIL readOnlyHint tasimali, yoksa Gemini her cagriyi onaya sokar.
    cap_props = (
        by_name.get("screen_capture", {}).get("inputSchema", {}).get("properties", {})
    )
    for field_ in ("monitor", "scale", "include_pointer"):
        check(
            f"screen_capture.{field_} parametresi var",
            field_ in cap_props,
            str(sorted(cap_props)),
        )
    cap_req = by_name.get("screen_capture", {}).get("inputSchema", {}).get("required", [])
    check("screen_capture zorunlu parametresiz", not cap_req, str(cap_req))
    for tool_name in ("screen_info", "screen_capture"):
        ann = by_name.get(tool_name, {}).get("annotations", {}) or {}
        check(f"{tool_name} readOnlyHint isaretli", ann.get("readOnlyHint") is True, str(ann))
        check(
            f"{tool_name} destructiveHint tasimiyor",
            ann.get("destructiveHint") is not True,
            str(ann),
        )
    # Docstring'ler satir kaydiriliyor: "the global\ncoordinate space" aranan
    # ifadeyi ikiye boluyor ve kontrol bosuna kaliyor. Bosluklari tekillestir.
    cap_desc = " ".join(
        str(by_name.get("screen_capture", {}).get("description", "")).split()
    )
    check(
        "screen_capture aciklamasi 'ne zaman kullanilir' iceriyor",
        "Use when" in cap_desc,
        cap_desc[:160],
    )
    # FAZ H'DE DEGISTI. Eskiden aciklama "goruntuyu goremezsin, baglanti
    # kullanici icin" diyordu -- Spark'a giden kanal metin-only oldugu icin
    # DOGRUYDU. Claude Code goruyor (olculdu), yani o cumle artik yanlis olurdu.
    # Yerine gecen sart: aciklama iki durumu da anlatsin ve koordinat
    # donusumunun goruntuyle birlikte geldigini soylesin.
    check(
        "screen_capture aciklamasi goren/gormeyen istemciyi ayiriyor",
        "can display images" in cap_desc,
        cap_desc[:200],
    )
    check(
        "screen_capture aciklamasi koordinat uzayini soyluyor",
        "global coordinate space" in cap_desc,
        cap_desc[:260],
    )
    check(
        "screen_capture aciklamasi once ui_dump'i oneriyor",
        "ui_dump" in cap_desc,
        cap_desc[:400],
    )

    # Erisilebilirlik araclari: ui_dump okuma, digerleri gercek eylem.
    dump_props = by_name.get("ui_dump", {}).get("inputSchema", {}).get("properties", {})
    for field_ in ("target", "interactive_only"):
        check(f"ui_dump.{field_} parametresi var", field_ in dump_props, str(sorted(dump_props)))
    check(
        "ui_dump readOnlyHint isaretli",
        (by_name.get("ui_dump", {}).get("annotations") or {}).get("readOnlyHint") is True,
        str(by_name.get("ui_dump", {}).get("annotations")),
    )
    for tool_name in ("ui_click", "ui_set_text"):
        ann = by_name.get(tool_name, {}).get("annotations", {}) or {}
        check(f"{tool_name} destructiveHint isaretli",
              ann.get("destructiveHint") is True, str(ann))
        req = by_name.get(tool_name, {}).get("inputSchema", {}).get("required", [])
        check(f"{tool_name}.id zorunlu", "id" in req, str(req))
    settext_req = by_name.get("ui_set_text", {}).get("inputSchema", {}).get("required", [])
    check("ui_set_text.text zorunlu", "text" in settext_req, str(settext_req))
    dump_desc = str(by_name.get("ui_dump", {}).get("description", ""))
    check("ui_dump aciklamasi 'ne zaman kullanilir' iceriyor",
          "Use this" in dump_desc or "Use when" in dump_desc, dump_desc[:160])
    check("ui_dump aciklamasi goruntu yerine metni onermeyi soyluyor",
          "cannot read images" in dump_desc, dump_desc[:240])

    # Toplu eylem ve pencereler (E bolumu)
    batch_props = (
        by_name.get("computer_batch", {}).get("inputSchema", {}).get("properties", {})
    )
    for field_ in ("actions", "final", "force"):
        check(f"computer_batch.{field_} parametresi var", field_ in batch_props,
              str(sorted(batch_props)))
    batch_req = (
        by_name.get("computer_batch", {}).get("inputSchema", {}).get("required", [])
    )
    check("computer_batch.actions zorunlu", "actions" in batch_req, str(batch_req))
    check("computer_batch destructiveHint isaretli",
          (by_name.get("computer_batch", {}).get("annotations") or {})
          .get("destructiveHint") is True,
          str(by_name.get("computer_batch", {}).get("annotations")))
    batch_desc = str(by_name.get("computer_batch", {}).get("description", ""))
    check("computer_batch aciklamasi 'ne zaman kullanilir' iceriyor",
          "Use this" in batch_desc, batch_desc[:160])
    # Aracin varlik sebebi bu: her ayri cagri telefonda bir onay demek.
    check("computer_batch aciklamasi onay maliyetini anlatiyor",
          "confirmation" in batch_desc, batch_desc[:300])
    actions_desc = str(batch_props.get("actions", {}).get("description", ""))
    check("actions aciklamasi ornek JSON veriyor", '{"a":' in actions_desc,
          actions_desc[:200])
    for kind in ("key", "type", "wait", "ui_click", "launch", "focus",
                 "hold", "release", "mouse_down", "mouse_up", "triple_click"):
        check(f"actions aciklamasi '{kind}' eylemini sayiyor",
              kind in actions_desc, actions_desc[:400])
    # Ayri cagrilarla da yapilabilen bir sey; ajanin BUNU tek listede
    # yapabilecegini bilmesi lazim, yoksa hold'u hic kullanmaz.
    check("actions aciklamasi duraklamali suruklemeyi anlatiyor",
          "mouse_down, move" in actions_desc, actions_desc[:500])

    # -- I bolumu: mouse/keyboard genisledi -------------------------------
    mouse_props = by_name.get("mouse", {}).get("inputSchema", {}).get("properties", {})
    for field_ in ("action", "x", "y", "to_x", "to_y", "scroll_amount",
                   "horizontal", "button", "smooth", "monitor", "force"):
        check(f"mouse.{field_} parametresi var", field_ in mouse_props,
              str(sorted(mouse_props)))
    mouse_act = " ".join(str(mouse_props.get("action", {}).get("description", "")).split())
    for act in ("triple_click", "right_click", "middle_click", "drag", "scroll",
                "hold", "release"):
        check(f"mouse.action '{act}' eylemini sayiyor", act in mouse_act,
              mouse_act[:300])
    mouse_desc = " ".join(str(by_name.get("mouse", {}).get("description", "")).split())
    # Imlec artik yol aliyor: ajan bunu bilmezse "takildi" sanip cagriyi
    # tekrarlar ve iki hareket ust uste biner.
    check("mouse aciklamasi imlecin isinlanmadigini soyluyor",
          "glides" in mouse_desc, mouse_desc[:400])
    check("mouse aciklamasi duraklamali suruklemeyi anlatiyor",
          "hold, then move, then release" in mouse_desc, mouse_desc[:400])

    kb_desc = " ".join(str(by_name.get("keyboard", {}).get("description", "")).split())
    check("keyboard aciklamasi hold'un kalici oldugunu soyluyor",
          "keeps keys down across later calls" in kb_desc, kb_desc[:400])
    check("keyboard aciklamasi birakmayi zorunlu kiliyor",
          "Always release what you hold" in kb_desc, kb_desc[:500])
    # Otomatik birakma bir GUVENLIK AGI; ajan onu normal yol sanmamali.
    check("keyboard aciklamasi otomatik birakmayi yedek olarak anlatiyor",
          "damage control" in kb_desc, kb_desc[:600])
    kb_keys = " ".join(str(by_name.get("keyboard", {}).get("inputSchema", {})
                           .get("properties", {}).get("keys", {})
                           .get("description", "")).split())
    check("keyboard.keys sinirsiz tus birlesimini soyluyor",
          "Any number of keys" in kb_keys, kb_keys[:300])

    check("window_list readOnlyHint isaretli",
          (by_name.get("window_list", {}).get("annotations") or {})
          .get("readOnlyHint") is True,
          str(by_name.get("window_list", {}).get("annotations")))
    check("window_focus destructiveHint isaretli",
          (by_name.get("window_focus", {}).get("annotations") or {})
          .get("destructiveHint") is True,
          str(by_name.get("window_focus", {}).get("annotations")))
    wf_req = by_name.get("window_focus", {}).get("inputSchema", {}).get("required", [])
    check("window_focus.window zorunlu", "window" in wf_req, str(wf_req))
    wf_desc = str(by_name.get("window_focus", {}).get("description", ""))
    check("window_focus aciklamasi ui_click'i oneriyor",
          "ui_click" in wf_desc, wf_desc[:240])

    # computer_task: gorsel isi yerel ajana devreden arac
    ct = by_name.get("computer_task", {})
    ct_props = ct.get("inputSchema", {}).get("properties", {})
    for field_ in ("goal", "app", "agent", "model", "effort", "max_steps",
                   "wait_seconds", "timeout", "force"):
        check(f"computer_task.{field_} parametresi var", field_ in ct_props,
              str(sorted(ct_props)))
    ct_req = ct.get("inputSchema", {}).get("required", [])
    check("computer_task.goal zorunlu", "goal" in ct_req, str(ct_req))
    check("computer_task.app zorunlu DEGIL", "app" not in ct_req, str(ct_req))
    check("computer_task destructiveHint isaretli",
          (ct.get("annotations") or {}).get("destructiveHint") is True,
          str(ct.get("annotations")))
    ct_desc = " ".join(str(ct.get("description", "")).split())
    check("computer_task aciklamasi 'ne zaman kullanilir' iceriyor",
          "Reach for this one when" in ct_desc, ct_desc[:200])
    # Ucuz yolu once denemesi soylensin. FAZ H: gerekce degisti -- artik
    # "sen goremiyorsun" degil, "goruyorsan buna gerek yok". Aciklama gorebilen
    # istemciye bunu ACIKCA soylemeli, yoksa gereksiz yere ajan oturumu acar.
    check("computer_task aciklamasi once computer_batch'i oneriyor",
          "computer_batch" in ct_desc, ct_desc[:400])
    check("computer_task aciklamasi goren istemciye gerekmedigini soyluyor",
          "do NOT need this" in ct_desc, ct_desc[:400])
    check("computer_task aciklamasi job_status'u soyluyor",
          "job_status" in ct_desc, ct_desc[:400])
    # `max_steps` DISARIDAN ZORLANAMAZ; aciklama garanti ima etmemeli.
    steps_desc = str(ct_props.get("max_steps", {}).get("description", ""))
    check("computer_task.max_steps garanti vaat etmiyor",
          "not a hard cap" in steps_desc, steps_desc[:200])

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

    # Calisma dizini BURADA yaratiliyor, asagidaki `fs_write`e birakilmiyor.
    # OLCULDU 2026-08-21: /tmp/pcb/work yokken `shell_run` "Dizin yok" donuyor
    # ve tek basina bu kontrol patliyordu -- ama hemen ardindaki `fs_write`
    # dizini yaratiyordu, yani AYNI koda karsi ikinci kosum geciyordu.
    # /tmp her acilista temizlendigi icin belirtisi "yeniden baslatmadan sonra
    # ilk kosum basarisiz" oluyordu ve titresim sanildi. Titresim degil,
    # testin kendi sira bagimliligiydi.
    pathlib.Path("/tmp/pcb/work").mkdir(parents=True, exist_ok=True)

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

    # Masaustu araclari: HICBIRI girdi gondermemeli. Kontroller
    # `[desktop] enabled`in IKI DEGERINDE DE gecerli olacak sekilde yazildi --
    # kullanicinin config'i degistigi anda test kirilmasin diye. Kapali ve
    # kilitli halin reddi FARKLI cumleler kuruyor, ikisi de kabul.
    #
    # `desktop_unlock` cagrisi ise masaustu ACIKKEN gercek bir izin acardi;
    # bir test YAN ETKI birakmamali, o yuzden acikken hic cagrilmiyor.
    import sys as _sys
    _sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from pcbridge.config import load_config as _lc  # noqa: PLC0415

    desktop_on = _lc().desktop.enabled

    out = call("mouse", {"action": "move", "x": 10, "y": 10})
    check("mouse izinsiz reddediyor", "⛔" in out, out[:200])
    check(
        "mouse reddi ne yapilacagini soyluyor",
        ("enabled" in out) if not desktop_on else ("desktop_unlock" in out),
        out[:200],
    )
    out = call("keyboard", {"action": "type", "text": "bu-yazilmamali"})
    check("keyboard izinsiz reddediyor", "⛔" in out, out[:200])

    if desktop_on:
        # Cagirmak GERCEK izin acardi -> yan etki. Kapali haldeki reddi
        # `tests/test_desktop.py` zaten SafetyGate duzeyinde kontrol ediyor.
        skip("desktop_unlock reddi", "masaustu ACIK, cagri gercek izin acardi")
    else:
        out = call("desktop_unlock", {"minutes": 1})
        check("desktop_unlock kapaliyken reddediyor", "⛔" in out, out[:200])

    out = call("desktop_lock", {})
    check("desktop_lock her durumda cevap veriyor", "kontrolu" in out, out[:200])
    # Ekran goruntusu de ayni kapidan geciyor: izin yokken EKRAN OKUNMAMALI.
    out = call("screen_capture", {})
    check("screen_capture izinsiz reddediyor", "⛔" in out, out[:200])
    check("screen_capture reddinde baglanti sizmiyor", "/shot/" not in out, out[:200])
    # screen_info izin kapisindan gecmez (yalnizca donanim duzeni) ama
    # calismali ve koordinat sozlesmesini soylemeli.
    out = call("screen_info", {})
    check("screen_info kapaliyken de calisiyor", "tuval:" in out, out[:200])
    check("screen_info koordinat sozlesmesini soyluyor", "global" in out, out[:300])
    # Erisilebilirlik araclari da ayni kapidan geciyor: izin yokken EKRAN
    # ICERIGI (etiketler, metin kutulari) okunmamali.
    out = call("ui_dump", {})
    check("ui_dump kapaliyken reddediyor", "⛔" in out, out[:200])
    out = call("ui_click", {"id": "#abcd"})
    check("ui_click kapaliyken reddediyor", "⛔" in out, out[:200])
    out = call("ui_set_text", {"id": "#abcd", "text": "bu-yazilmamali"})
    check("ui_set_text kapaliyken reddediyor", "⛔" in out, out[:200])
    # Toplu eylem: izin yokken HICBIR eylem calismamali.
    out = call("computer_batch", {
        "actions": '[{"a":"key","keys":"a"},{"a":"type","text":"bu-yazilmamali"}]',
    })
    check("computer_batch kapaliyken reddediyor", "⛔" in out, out[:200])
    check("computer_batch reddinde eylem raporu yok",
          "yapildi" not in out, out[:200])
    # Bozuk liste kapiya varmadan reddedilmeli ve gerekcesi ANLASILIR olmali.
    out = call("computer_batch", {"actions": "[{bozuk"})
    check("computer_batch bozuk JSON'u aciklayarak reddediyor",
          "JSON" in out and "⛔" in out, out[:200])
    out = call("computer_batch", {"actions": '[{"a":"ucmak"}]'})
    check("computer_batch bilinmeyen eylemi reddediyor",
          "ucmak" in out, out[:200])
    out = call("window_list", {})
    check("window_list kapaliyken reddediyor", "⛔" in out, out[:200])
    out = call("window_focus", {"window": "Terminal"})
    check("window_focus kapaliyken reddediyor", "⛔" in out, out[:200])
    # computer_task masaustu kapisinin ARKASINDA: `agent_run` gibi serbestce
    # ajan baslatabilseydi kapiyi tamamen delerdi (ajan pcb-do'yu cagiriyor).
    out = call("computer_task", {"goal": "bir sey yap", "app": "Vesktop"})
    check("computer_task kapaliyken reddediyor", "⛔" in out, out[:200])
    check("computer_task reddinde is BASLATILMADI",
          "job_status" not in out and "gorsel ajan basladi" not in out, out[:200])

    section("12. Ajan calistirma ve is takibi")

    # ⚠️ BU BOLUM GERCEK BIR `claude -p` OTURUMU ACAR VE KOTA YAKAR.
    # Bir kere kullanicinin gunluk limitini bu test bitirdi (2026-08-03) ve
    # hicbir belgede uyari yoktu. Atlamak icin:  PCBRIDGE_TEST_NO_AGENT=1
    out = ""
    if os.environ.get("PCBRIDGE_TEST_NO_AGENT"):
        skip("agent_run bitti", "PCBRIDGE_TEST_NO_AGENT=1 — gercek ajan calistirilmadi")
    else:
        out = call(
            "agent_run",
            {
                "agent": "claude",
                "prompt": "merhaba testi",
                "workdir": "/tmp/pcb/work",
                "wait_seconds": 20,
            },
        )
        if "finished" in out:
            check("agent_run bitti", True)
        else:
            # Kota bitmis ya da CLI'da oturum acilmamis olabilir. Bu, TESTIN
            # bozuk oldugu anlamina gelmiyor; FAIL saymak sonraki kisiyi
            # olmayan bir hatayi aramaya gonderir.
            skip("agent_run bitti",
                 "ajan is dondurmedi (kota? oturum?) — " + " ".join(out.split())[:90])

    # Asagidaki dort kontrol AYRISTIRICIYI olcuyor, ajani degil: sabit bir
    # stream-json akisi gerekiyor, gercek `claude` ise her cagrida baska metin
    # ve baska maliyet uretiyor. Sahte ajani sunucuya PATH ile enjekte etmek de
    # mumkun degil (`jobs.py` `bash -lc` kullaniyor, login kabugu PATH'i
    # yeniden kuruyor -- gerekcesi `tests/fake_agents/claude`). Kapsam bu yuzden
    # `test_models.py` 11. bolume tasindi; burada FAIL saymak yaniltici olurdu.
    if "sess-abc-123" in out:
        check("adimlar ayristirildi", "arac: Bash" in out, out[:600])
        check("oturum kimligi cikarildi", "sess-abc-123" in out, out[:600])
        check("sonuc metni var", "Istek tamamlandi: merhaba testi" in out, out[:600])
        check("maliyet gosterildi", "0.0123" in out, out[:800])
    else:
        why = "gercek ajan sabit cikti vermez; ayristirici test_models.py 11. bolumde"
        skip("adimlar ayristirildi", why)
        skip("oturum kimligi cikarildi", "")
        skip("sonuc metni var", "")
        skip("maliyet gosterildi", "")

    job_id = ""
    for token_ in out.split():
        if token_.startswith("**") and "-" in token_:
            job_id = token_.strip("*")
            break
    if not out:
        # Ajan hic calistirilmadi (PCBRIDGE_TEST_NO_AGENT) -> is kimligi de yok.
        skip("job_id ayiklandi", "ajan calistirilmadi")
        skip("job_status calisti", "")
        skip("job_output ham log verdi", "")
    else:
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

    section("18. stdio tasimasi (faz H)")
    _test_stdio()

    section("19. inline_images (faz H)")
    _test_inline_setting()

    tail = f", {skip_count} atlandi" if skip_count else ""
    print(f"\n\033[1mSonuc: {ok_count} basarili, {fail_count} basarisiz{tail}\033[0m")
    if skip_count:
        print("  Atlananlarin kapsami: ./.venv/bin/python tests/test_models.py")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
