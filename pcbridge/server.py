"""pcbridge girisi: FastMCP sunucusunu kurar ve calistirir."""

from __future__ import annotations

import argparse
import logging
import sys

import base64
import json
from urllib.parse import parse_qsl, unquote, urlencode

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse

from . import tools as toolsmod
from .auth import SqliteOAuthProvider, make_consent_routes
from .desktop import session as sessionlib
from .config import Config, load_config
from .jobs import JobManager
from .shots import ShotStore

# stream=sys.stderr ACIKCA veriliyor. Varsayilan zaten stderr ama stdio
# tasimasinda stdout JSON-RPC kanalinin KENDISI: oraya dusen tek bir log satiri
# istemcinin cozumleyicisini bozar ve hata "sunucu bozuk" gibi gorunur.
# Olculdu (H0.3): bu haliyle stdout'ta JSON disi satir yok.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("pcbridge")

INSTRUCTIONS = """\
This MCP server controls the user's personal Linux desktop computer (ZorinOS,
GNOME on Wayland, two 1920x1080 monitors side by side).

You can:
  * send prompts to terminal coding agents (Claude Code, Antigravity CLI) with
    `agent_run`, then follow them with `job_status`;
  * drive an already-open interactive terminal with `tmux_start` / `tmux_send`
    / `tmux_keys` / `tmux_capture`;
  * run shell commands, read and write files, and check machine status;
  * read and drive the graphical desktop: `ui_dump` to read the screen as text,
    `ui_click` / `ui_set_text` to act on what it lists, `screen_capture` to see
    it, and `computer_batch` to run a whole sequence at once.

Guidelines:
  * Coding agents take minutes, not seconds. `agent_run` returns a job id;
    if the job is still running, wait and call `job_status` again rather than
    starting the task over.
  * Reuse `session_id` via `resume_session` to continue an earlier conversation
    with the same agent instead of starting from scratch.
  * Prefer `agent_run` for anything that requires reasoning about code, and
    plain `shell_run` only for simple, deterministic commands.
  * For the desktop, reach for `ui_dump` before `screen_capture`: it is far
    cheaper, and because it acts on widgets rather than coordinates it cannot
    miss. Fall back to the screenshot where the tree comes back empty — Electron
    apps, canvases, games.
  * Desktop tools need `desktop_unlock` first, and stay off entirely unless the
    user enabled them in the configuration.
  * Screenshots go stale. If you read coordinates off one, act on them right
    away — do not do other work in between.
  * Coordinates you read off a screenshot MUST be sent with that image's
    `shot` id (printed next to it, e.g. `m2-a1b2c3`). Give x/y exactly as you
    see them in the picture; the server applies the offset and the scale. Do
    not do that arithmetic yourself, and do not send such a coordinate without
    the id — screenshots are scaled down, so a bare coordinate is read as a
    global desktop position and the click lands somewhere else entirely. If a
    coordinate really is a global or per-monitor one, pass `monitor` instead.
  * Always tell the user which directory you are working in.

Desktop rules — these are not preferences:
  * To open a graphical application, use `window_focus`. It goes through the
    desktop's own search and launches the app if it is closed. NEVER launch a
    graphical application with `shell_run`: it becomes a child of this server,
    dies when the server restarts, and often has no app id, which means you
    will not find the window again.
  * `shell_run` stays for non-graphical work: builds, file operations, queries.
  * `ui_click` does not move the mouse pointer — it asks the application
    directly. That is intended; do not "fix" it with the `mouse` tool.
  * After `desktop_unlock`, call `desktop_lock` when the graphical work is
    done. The permission also expires on its own, but leaving it open keeps
    the on-screen indicator running and worries the user.
"""


class MetadataNormalizer:
    """OAuth metadata belgelerindeki `issuer` degerini duzeltir.

    Pydantic'in AnyHttpUrl tipi "https://host" adresini "https://host/" haline
    getiriyor. RFC 8414'e gore issuer, metadata'nin bulundugu adresten
    /.well-known/... kismi cikarilinca kalan deger olmali - yani SONDA EGIK
    CIZGI OLMAMALI. Katı dogrulayicilar (Google dahil) bunu "issuer uyusmuyor"
    diye reddedip akisi token adimina hic getirmiyor.

    Saf ASGI middleware olarak yazildi: yalnizca /.well-known/ yollarina
    dokunur, /mcp'deki SSE akisina hic karismaz.
    """

    def __init__(self, app):
        self.app = app

    @staticmethod
    def _fix(raw: bytes) -> bytes:
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return raw
        if not isinstance(data, dict):
            return raw

        changed = False
        issuer = data.get("issuer")
        if isinstance(issuer, str) and issuer.endswith("/"):
            data["issuer"] = issuer.rstrip("/")
            changed = True

        servers = data.get("authorization_servers")
        if isinstance(servers, list):
            fixed = [
                s.rstrip("/") if isinstance(s, str) and s.endswith("/") else s
                for s in servers
            ]
            if fixed != servers:
                data["authorization_servers"] = fixed
                changed = True

        return json.dumps(data).encode() if changed else raw

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not scope.get("path", "").startswith(
            "/.well-known/"
        ):
            await self.app(scope, receive, send)
            return

        start: dict = {}
        chunks: list[bytes] = []

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                start.update(message)
                return
            if message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if message.get("more_body"):
                    return
                body = self._fix(b"".join(chunks))
                headers = [
                    (k, v)
                    for k, v in start.get("headers", [])
                    if k.lower() != b"content-length"
                ]
                headers.append((b"content-length", str(len(body)).encode()))
                await send(
                    {
                        "type": "http.response.start",
                        "status": start.get("status", 200),
                        "headers": headers,
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
            await send(message)

        await self.app(scope, receive, send_wrapper)


class BasicAuthFormShim:
    """`Authorization: Basic` ile gelen kimlik bilgilerini form gövdesine kopyalar.

    NEDEN GEREKLI: MCP SDK'sinin /token isleyicisi, istemci `client_secret_basic`
    yontemiyle kayitli olsa bile `client_id` alanini FORM GOVDESINDE ariyor
    (`form_data.get("client_id")`); bulamayinca "Missing client_id" deyip 401
    donuyor. Oysa RFC 6749 Bolum 2.3.1'e gore Basic kullanan istemci kimligini
    yalnizca baslikta gonderebilir - Google tam olarak boyle yapiyor.

    Bu middleware araya girip, gövdede eksikse, Basic basligindaki client_id ve
    client_secret degerlerini forma ekler. Zaten gövdede varsa hicbir sey
    degistirmez.
    """

    def __init__(self, app, token_path: str = "/token"):
        self.app = app
        self.token_path = token_path

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != self.token_path
        ):
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        auth = headers.get("authorization", "")
        ctype = headers.get("content-type", "")

        # Basarisiz token yanitlarinin GEREKCESINI loga yaz. 401'de govde
        # {"error": "...", "error_description": "..."} iceriyor; hangi adimda
        # takildigini ancak boyle gorebiliyoruz.
        status_holder: dict = {}
        out_chunks: list[bytes] = []

        async def logging_send(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            elif message["type"] == "http.response.body":
                out_chunks.append(message.get("body", b""))
                if not message.get("more_body"):
                    st = status_holder.get("status", 0)
                    if st != 200:
                        log.warning(
                            "/token %s -> %s",
                            st,
                            b"".join(out_chunks)[:400].decode("utf-8", "replace"),
                        )
            await send(message)

        log.info(
            "/token istegi: auth=%s content-type=%s",
            "Basic" if auth.startswith("Basic ") else (auth.split(" ")[0] or "yok"),
            ctype or "yok",
        )

        if not auth.startswith("Basic ") or "x-www-form-urlencoded" not in ctype:
            await self.app(scope, receive, logging_send)
            return

        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.request":
                body += message.get("body", b"")
                if not message.get("more_body"):
                    break
            elif message["type"] == "http.disconnect":
                break

        fields = parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True)
        present = {k for k, _ in fields}
        log.info("/token govde alanlari: %s", sorted(present) or "(bos)")
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            cid, sep, csec = decoded.partition(":")
            if sep:
                cid, csec = unquote(cid), unquote(csec)
                added = []
                if cid and "client_id" not in present:
                    fields.append(("client_id", cid))
                    added.append("client_id")
                if csec and "client_secret" not in present:
                    fields.append(("client_secret", csec))
                    added.append("client_secret")
                if added:
                    log.info(
                        "Basic baslıgindan forma kopyalandi: %s", ", ".join(added)
                    )
        except (ValueError, UnicodeDecodeError):
            pass

        new_body = urlencode(fields).encode()
        new_headers = [
            (k, v) for k, v in scope["headers"] if k.lower() != b"content-length"
        ]
        new_headers.append((b"content-length", str(len(new_body)).encode()))
        new_scope = dict(scope)
        new_scope["headers"] = new_headers

        delivered = False

        async def new_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": new_body, "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(new_scope, new_receive, logging_send)


def build_app(cfg: Config, transport: str = "http"):
    """FastMCP orneğini kur.

    `transport` araclara kadar iniyor cunku iki tasima arasinda GERCEK bir fark
    var: stdio'da HTTP sunucusu yok, yani `@mcp.custom_route` ile eklenen
    `/shot/<token>.png`, `/healthz`, `/consent` ve `/.well-known/*` rotalari
    servis EDILMEZ. `screen_capture`'in urettigi baglanti orada olu olur; bunu
    bilen tek yer araclarin kendisi.

    Ayrica stdio'da OAuth uygulanmaz -- fastmcp bunu kendi yapiyor (STDIO
    tasimasinda auth kontrolleri atlanir). Yetki surec sinirinin kendisi.
    """
    provider = SqliteOAuthProvider(cfg)
    mcp = FastMCP(
        name="pcbridge",
        instructions=INSTRUCTIONS,
        auth=provider,
    )

    jm = JobManager(cfg.jobs_dir, default_timeout=cfg.default_job_timeout)
    shot_store = ShotStore(cfg)
    toolsmod.register(mcp, cfg, jm, shot_store, transport=transport)

    consent_get, consent_post = make_consent_routes(provider)

    @mcp.custom_route("/consent", methods=["GET"], include_in_schema=False)
    async def _consent_get(request: Request):
        return await consent_get(request)

    @mcp.custom_route("/consent", methods=["POST"], include_in_schema=False)
    async def _consent_post(request: Request):
        return await consent_post(request)

    # ---------------------------------------------------------------------
    # Kesif (discovery) takma adlari.
    # MCP spesifikasyonu protected-resource belgesini
    # /.well-known/oauth-protected-resource/mcp altinda tutar; bazi istemciler
    # ise kok yolu yoklar. Ayni belgeyi her iki adreste de servis ediyoruz ki
    # Spark hangi yolu denerse denesin bulsun.
    # ---------------------------------------------------------------------
    from mcp.server.auth.routes import build_metadata
    from mcp.server.auth.settings import (
        ClientRegistrationOptions as _CRO,
        RevocationOptions as _RO,
    )
    from pydantic import AnyHttpUrl

    _as_meta = build_metadata(
        issuer_url=AnyHttpUrl(cfg.public_url + "/"),
        service_documentation_url=None,
        client_registration_options=_CRO(enabled=True),
        revocation_options=_RO(enabled=True),
    ).model_dump(exclude_none=True, mode="json")

    # issuer / authorization_servers: sonda egik cizgi YOK (RFC 8414)
    _as_meta["issuer"] = cfg.public_url
    _pr_meta = {
        "resource": cfg.mcp_url,
        "authorization_servers": [cfg.public_url],
        "scopes_supported": [],
        "bearer_methods_supported": ["header"],
    }

    @mcp.custom_route(
        "/.well-known/oauth-protected-resource", methods=["GET"], include_in_schema=False
    )
    async def _pr_root(request: Request):
        return JSONResponse(_pr_meta)

    @mcp.custom_route(
        "/.well-known/oauth-authorization-server" + cfg.mcp_path,
        methods=["GET"],
        include_in_schema=False,
    )
    async def _as_suffixed(request: Request):
        return JSONResponse(_as_meta)

    @mcp.custom_route(
        "/.well-known/openid-configuration", methods=["GET"], include_in_schema=False
    )
    async def _oidc_root(request: Request):
        return JSONResponse(_as_meta)

    @mcp.custom_route(
        "/.well-known/openid-configuration" + cfg.mcp_path,
        methods=["GET"],
        include_in_schema=False,
    )
    async def _oidc_suffixed(request: Request):
        return JSONResponse(_as_meta)

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def _health(request: Request):
        return JSONResponse(
            {
                "ok": True,
                "service": "pcbridge",
                "mcp_url": cfg.mcp_url,
                "agents": sorted(k for k, v in cfg.agents.items() if v.enabled),
            }
        )

    @mcp.custom_route("/shot/{name}", methods=["GET"], include_in_schema=False)
    async def _shot(request: Request):
        """Kisa omurlu ekran goruntusu. Yetki token'in KENDISI -- OAuth yok.

        Kullanici telefondan baglantiya dokunup ekranina baksin diye var:
        Spark'a giden MCP function-response kanali yalnizca metin tasidigi icin
        goruntu arac sonucuna konulamiyor (sinir kanalin, modelin degil).
        Gecersiz ve suresi dolmus token ayni cevabi alir: disaridan token
        tahmini icin bilgi sizmasin.
        """
        name = request.path_params.get("name", "")
        token = name[:-4] if name.endswith(".png") else name
        path = shot_store.resolve(token)
        if path is None:
            return PlainTextResponse("bulunamadi", status_code=404)
        return FileResponse(
            path,
            media_type="image/png",
            headers={
                # Telefon tarayicisi ya da araya giren bir vekil sunucu
                # goruntuyu saklamasin: baglanti kisa omurlu olmali.
                "Cache-Control": "no-store, max-age=0",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

    @mcp.custom_route("/", methods=["GET"], include_in_schema=False)
    async def _root(request: Request):
        return PlainTextResponse(
            "pcbridge calisiyor.\n"
            f"Gemini Spark'a eklenecek adres: {cfg.mcp_url}\n"
        )

    return mcp, provider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pcbridge")
    parser.add_argument("-c", "--config", help="config.toml yolu")
    parser.add_argument("--check", action="store_true", help="sadece dogrula ve cik")
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="HTTP yerine stdio tasimasi (yerel istemciler: Claude Code, Codex, "
        "Claude Desktop). DIKKAT: stdio'da OAuth YOK, yetki surec sinirinin "
        "kendisi.",
    )
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    transport = "stdio" if args.stdio else "http"

    # Oturum ortamini ONAR. stdio'da sunucuyu istemci baslatiyor ve onun
    # ortamini devraliyoruz; o ortamin dogru olacaginin garantisi yok.
    # Olculdu: Codex'in baslattigi surecte DBUS_SESSION_BUS_ADDRESS
    # genisletilmemis bir literal olarak geliyor ve masaustu araclarinin
    # TAMAMI sessizce cokuyor. Ayrintili gerekce `desktop/session.py`'de.
    fixed = sessionlib.ensure_session_env()
    if fixed:
        log.warning(
            "Oturum ortami eksikti, standart yollardan onarildi: %s",
            ", ".join(fixed),
        )

    mcp, _provider = build_app(cfg, transport=transport)

    if args.stdio:
        banner = [
            "",
            "  pcbridge (stdio)",
            f"  yapilandirma : {cfg.source_path}",
            f"  ajanlar      : {', '.join(k for k, v in cfg.agents.items() if v.enabled)}",
            f"  is kayitlari : {cfg.jobs_dir}",
            f"  masaustu     : {'acik' if cfg.desktop.enabled else 'KAPALI ([desktop] enabled = false)'}",
            "  UYARI: stdio'da OAuth yok. Bu sureci baslatabilen her yerel",
            "         program masaustune erisir; onunde yalnizca desktop_unlock var.",
            "",
        ]
    else:
        banner = [
            "",
            "  pcbridge hazir",
            f"  yapilandirma : {cfg.source_path}",
            f"  yerel adres  : http://{cfg.host}:{cfg.port}{cfg.mcp_path}",
            f"  dis adres    : {cfg.mcp_url}   <-- uzak istemciye BUNU gir",
            f"  onay sayfasi : {cfg.public_url}/consent",
            f"  durum        : {cfg.public_url}/healthz",
            f"  ajanlar      : {', '.join(k for k, v in cfg.agents.items() if v.enabled)}",
            f"  is kayitlari : {cfg.jobs_dir}",
            "",
        ]
    # stderr SART: stdio'da stdout JSON-RPC kanalinin kendisi.
    print("\n".join(banner), file=sys.stderr, flush=True)

    if args.check:
        print("Yapilandirma gecerli.", file=sys.stderr)
        return 0

    if args.stdio:
        # HTTP sunucusu yok -> /shot, /healthz, /consent, /.well-known/* de yok.
        # `screen_capture` bunu biliyor ve baglanti yerine dosya yolu doner.
        mcp.run(transport="stdio", show_banner=False)
        return 0

    mcp.run(
        transport="http",
        host=cfg.host,
        port=cfg.port,
        path=cfg.mcp_path,
        # Kendi banner'imizi basiyoruz; FastMCP'ninki acilista PyPI'a
        # baglanmaya calistigi icin kapali (offline/proxy ortamlarinda patliyor).
        show_banner=False,
        middleware=[
            # issuer'daki sondaki egik cizgiyi temizler
            Middleware(MetadataNormalizer),
            # Basic auth kimligini form gövdesine tasir (Google icin sart)
            Middleware(BasicAuthFormShim),
        ],
        # Tunel arkasindayiz: Host basligi ts.net alan adi olarak gelir.
        allowed_hosts=["*"],
        allowed_origins=["*"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
