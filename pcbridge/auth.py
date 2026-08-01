"""Gemini Spark'in bekledigi OAuth 2.1 authorization server.

Spark ozel MCP sunucularina yalnizca OAuth 2.1 ile baglanir; duz bearer token
kabul etmez. Burada tam bir mini authorization server var:

  * Dynamic Client Registration (RFC 7591)  -> POST /register
  * Authorization Code + PKCE (S256)        -> GET/POST /authorize, POST /token
  * Refresh token                            -> POST /token
  * Token revocation                         -> POST /revoke
  * Metadata                                 -> /.well-known/...

Kullaniciyi dogrulayan tek sey: /consent sayfasinda girilen PAROLA.
Bu parola sizin "kisisel token"iniz gibi davranir; onu bilmeyen kimse
Spark uzerinden bile olsa sunucuya yetki alamaz.
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from fastmcp.server.auth.auth import (
    ClientRegistrationOptions,
    OAuthProvider,
    RevocationOptions,
)

from .config import Config

logger = logging.getLogger("pcbridge.auth")

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    client_id  TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_codes (
    code       TEXT PRIMARY KEY,
    client_id  TEXT NOT NULL,
    data       TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS access_tokens (
    token      TEXT PRIMARY KEY,
    client_id  TEXT NOT NULL,
    data       TEXT NOT NULL,
    expires_at REAL
);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token         TEXT PRIMARY KEY,
    client_id     TEXT NOT NULL,
    data          TEXT NOT NULL,
    expires_at    REAL,
    access_token  TEXT
);
CREATE TABLE IF NOT EXISTS pending (
    rid        TEXT PRIMARY KEY,
    client_id  TEXT NOT NULL,
    data       TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    ip         TEXT PRIMARY KEY,
    count      INTEGER NOT NULL,
    locked_until REAL NOT NULL
);
"""


class SqliteOAuthProvider(OAuthProvider):
    """Diske yazan, parola ile onaylanan tek kullanicilik OAuth 2.1 sunucusu."""

    def __init__(self, cfg: Config):
        super().__init__(
            base_url=cfg.public_url,
            resource_base_url=cfg.public_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
            required_scopes=None,
        )
        self.cfg = cfg
        self.db_path = cfg.db_path
        self._init_db()

    # ---------------------------------------------------------------- sqlite
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)
        try:
            self.db_path.chmod(0o600)
        except OSError:
            pass

    def _gc(self) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute("DELETE FROM auth_codes WHERE expires_at < ?", (now,))
            conn.execute("DELETE FROM pending WHERE expires_at < ?", (now,))
            conn.execute(
                "DELETE FROM access_tokens WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )

    def audit(self, event: str, **fields: Any) -> None:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **fields}
        line = json.dumps(rec, ensure_ascii=False)
        logger.info(line)
        try:
            with Path(self.cfg.audit_log).open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    # ------------------------------------------------------- client kayitlari
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM clients WHERE client_id = ?", (client_id,)
            ).fetchone()
        if not row:
            return None
        return OAuthClientInformationFull.model_validate_json(row["data"])

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise ValueError("client_id gerekli")
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO clients(client_id, data, created_at) VALUES (?,?,?)",
                (
                    client_info.client_id,
                    client_info.model_dump_json(),
                    time.time(),
                ),
            )
        self.audit(
            "client_registered",
            client_id=client_info.client_id,
            client_name=client_info.client_name,
            redirect_uris=[str(u) for u in (client_info.redirect_uris or [])],
        )

    # --------------------------------------------------------------- authorize
    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Kod uretmeden once kullaniciyi parola sayfasina yollar."""
        if not client.client_id:
            raise AuthorizeError(
                error="invalid_client", error_description="client_id yok"
            )

        rid = secrets.token_urlsafe(24)
        payload = {
            "client_id": client.client_id,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "state": params.state,
            "scopes": params.scopes or [],
            "code_challenge": params.code_challenge,
            "resource": params.resource,
            "client_name": client.client_name or client.client_id,
        }
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO pending(rid, client_id, data, expires_at) VALUES (?,?,?,?)",
                (
                    rid,
                    client.client_id,
                    json.dumps(payload),
                    time.time() + self.cfg.auth_code_ttl,
                ),
            )
        self.audit("authorize_started", client_id=client.client_id, rid=rid[:8])
        return f"{self.cfg.public_url}/consent?rid={rid}"

    def _issue_code(self, rid: str) -> str:
        """Parola dogrulandiktan sonra cagrilir; client'a donulecek URL'i verir."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data, expires_at FROM pending WHERE rid = ?", (rid,)
            ).fetchone()
            if not row:
                raise KeyError("istek bulunamadi veya suresi doldu")
            if row["expires_at"] < time.time():
                conn.execute("DELETE FROM pending WHERE rid = ?", (rid,))
                raise KeyError("istegin suresi doldu, Spark'tan tekrar baglan")
            payload = json.loads(row["data"])
            conn.execute("DELETE FROM pending WHERE rid = ?", (rid,))

            code = "pcb_ac_" + secrets.token_urlsafe(32)
            auth_code = AuthorizationCode(
                code=code,
                client_id=payload["client_id"],
                redirect_uri=payload["redirect_uri"],  # type: ignore[arg-type]
                redirect_uri_provided_explicitly=payload[
                    "redirect_uri_provided_explicitly"
                ],
                scopes=payload["scopes"],
                expires_at=time.time() + self.cfg.auth_code_ttl,
                code_challenge=payload["code_challenge"],
                resource=payload.get("resource"),
            )
            conn.execute(
                "INSERT INTO auth_codes(code, client_id, data, expires_at) VALUES (?,?,?,?)",
                (
                    code,
                    payload["client_id"],
                    auth_code.model_dump_json(),
                    auth_code.expires_at,
                ),
            )

        target = construct_redirect_uri(
            payload["redirect_uri"], code=code, state=payload["state"]
        )
        # Tam hedef URL'i kaydediyoruz: istemci (ornegin Google) kodu geri
        # getirmezse, bu adresi tarayiciya elle yapistirip istemcinin ne
        # dedigini gorebiliyoruz. Kod tek kullanimlik ve 5 dakikada oluyor.
        self.audit(
            "code_issued",
            client_id=payload["client_id"],
            redirect_to=target,
            state_len=len(payload["state"] or ""),
        )
        return target

    def peek_pending(self, rid: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data, expires_at FROM pending WHERE rid = ?", (rid,)
            ).fetchone()
        if not row or row["expires_at"] < time.time():
            return None
        return json.loads(row["data"])

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data, client_id, expires_at FROM auth_codes WHERE code = ?",
                (authorization_code,),
            ).fetchone()
        if not row:
            return None
        if row["client_id"] != client.client_id:
            return None
        if row["expires_at"] < time.time():
            with self._conn() as conn:
                conn.execute(
                    "DELETE FROM auth_codes WHERE code = ?", (authorization_code,)
                )
            return None
        return AuthorizationCode.model_validate_json(row["data"])

    # ------------------------------------------------------------- token uret
    def _new_tokens(
        self,
        client_id: str,
        scopes: list[str],
        resource: str | None,
    ) -> OAuthToken:
        access = "pcb_at_" + secrets.token_urlsafe(40)
        refresh = "pcb_rt_" + secrets.token_urlsafe(40)
        now = time.time()
        access_exp = int(now + self.cfg.access_token_ttl)
        refresh_exp = (
            int(now + self.cfg.refresh_token_ttl) if self.cfg.refresh_token_ttl else None
        )

        at = AccessToken(
            token=access,
            client_id=client_id,
            scopes=scopes,
            expires_at=access_exp,
            resource=resource,
        )
        rt = RefreshToken(
            token=refresh,
            client_id=client_id,
            scopes=scopes,
            expires_at=refresh_exp,
        )
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO access_tokens(token, client_id, data, expires_at) VALUES (?,?,?,?)",
                (access, client_id, at.model_dump_json(), access_exp),
            )
            conn.execute(
                "INSERT INTO refresh_tokens(token, client_id, data, expires_at, access_token)"
                " VALUES (?,?,?,?,?)",
                (refresh, client_id, rt.model_dump_json(), refresh_exp, access),
            )
        self._gc()
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=self.cfg.access_token_ttl,
            refresh_token=refresh,
            scope=" ".join(scopes) if scopes else None,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM auth_codes WHERE code = ?", (authorization_code.code,)
            )
            if cur.rowcount == 0:
                raise TokenError("invalid_grant", "Kod bulunamadi veya kullanildi.")
        assert client.client_id
        self.audit("token_issued", client_id=client.client_id, grant="authorization_code")
        return self._new_tokens(
            client.client_id, authorization_code.scopes, authorization_code.resource
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data, client_id, expires_at FROM refresh_tokens WHERE token = ?",
                (refresh_token,),
            ).fetchone()
        if not row or row["client_id"] != client.client_id:
            return None
        if row["expires_at"] is not None and row["expires_at"] < time.time():
            return None
        return RefreshToken.model_validate_json(row["data"])

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if scopes and not set(scopes).issubset(set(refresh_token.scopes)):
            raise TokenError("invalid_scope", "Istenen kapsam yetkiyi asiyor.")
        assert client.client_id

        # Eski access token'i dusur; refresh token'i DONDURMUYORUZ (rotation yok)
        # -> Spark tarafinda yenileme yarisi yasanirsa baglanti kopmaz.
        with self._conn() as conn:
            row = conn.execute(
                "SELECT access_token FROM refresh_tokens WHERE token = ?",
                (refresh_token.token,),
            ).fetchone()
            if row and row["access_token"]:
                conn.execute(
                    "DELETE FROM access_tokens WHERE token = ?", (row["access_token"],)
                )

        new = self._new_tokens(
            client.client_id, scopes or refresh_token.scopes, None
        )
        # yeni access token'i eski refresh kaydina bagla, eski refresh'i koru
        with self._conn() as conn:
            conn.execute(
                "UPDATE refresh_tokens SET access_token = ? WHERE token = ?",
                (new.access_token, refresh_token.token),
            )
            conn.execute(
                "DELETE FROM refresh_tokens WHERE token = ?", (new.refresh_token,)
            )
        new.refresh_token = refresh_token.token
        self.audit("token_issued", client_id=client.client_id, grant="refresh_token")
        return new

    # ---------------------------------------------------------------- dogrula
    async def load_access_token(self, token: str) -> AccessToken | None:
        if self.cfg.static_token and secrets.compare_digest(
            token, self.cfg.static_token
        ):
            return AccessToken(
                token=token,
                client_id="static",
                scopes=[],
                expires_at=None,
            )
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data, expires_at FROM access_tokens WHERE token = ?", (token,)
            ).fetchone()
        if not row:
            return None
        if row["expires_at"] is not None and row["expires_at"] < time.time():
            with self._conn() as conn:
                conn.execute("DELETE FROM access_tokens WHERE token = ?", (token,))
            return None
        return AccessToken.model_validate_json(row["data"])

    async def verify_token(self, token: str) -> AccessToken | None:
        return await self.load_access_token(token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM access_tokens WHERE token = ?", (token.token,))
            conn.execute("DELETE FROM refresh_tokens WHERE token = ?", (token.token,))
        self.audit("token_revoked", client_id=token.client_id)

    # ------------------------------------------------------ brute-force koruma
    def _client_ip(self, request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def is_locked(self, ip: str) -> float:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT locked_until FROM attempts WHERE ip = ?", (ip,)
            ).fetchone()
        if not row:
            return 0.0
        remaining = row["locked_until"] - time.time()
        return max(0.0, remaining)

    def record_failure(self, ip: str) -> None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT count FROM attempts WHERE ip = ?", (ip,)
            ).fetchone()
            count = (row["count"] if row else 0) + 1
            locked_until = (
                time.time() + self.cfg.lockout_seconds
                if count >= self.cfg.max_failed_attempts
                else 0.0
            )
            conn.execute(
                "INSERT OR REPLACE INTO attempts(ip, count, locked_until) VALUES (?,?,?)",
                (ip, count, locked_until),
            )
        self.audit("password_failed", ip=ip, count=count)

    def clear_failures(self, ip: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM attempts WHERE ip = ?", (ip,))

    def check_password(self, given: str) -> bool:
        return secrets.compare_digest(given or "", self.cfg.password)


# ---------------------------------------------------------------------------
# /consent sayfasi
# ---------------------------------------------------------------------------

_PAGE = """<!doctype html>
<html lang="tr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>pcbridge - Erisim onayi</title>
<style>
 :root {{ color-scheme: dark; }}
 * {{ box-sizing: border-box; }}
 body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
   background:#0d1117; color:#e6edf3;
   font-family:ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif; padding:24px; }}
 .card {{ width:100%; max-width:420px; background:#161b22; border:1px solid #30363d;
   border-radius:14px; padding:28px; }}
 h1 {{ font-size:19px; margin:0 0 6px; }}
 p  {{ font-size:14px; color:#8b949e; line-height:1.55; margin:0 0 18px; }}
 .who {{ background:#0d1117; border:1px solid #30363d; border-radius:8px;
   padding:10px 12px; font-size:13px; margin-bottom:18px; word-break:break-all; }}
 .who b {{ color:#e6edf3; }}
 label {{ display:block; font-size:13px; margin-bottom:6px; color:#8b949e; }}
 input {{ width:100%; padding:11px 12px; font-size:15px; border-radius:8px;
   border:1px solid #30363d; background:#0d1117; color:#e6edf3; }}
 input:focus {{ outline:2px solid #1f6feb; border-color:#1f6feb; }}
 button {{ width:100%; margin-top:16px; padding:11px; font-size:15px; font-weight:600;
   border:0; border-radius:8px; background:#238636; color:#fff; cursor:pointer; }}
 button:hover {{ background:#2ea043; }}
 .err {{ background:#3d1417; border:1px solid #6e2528; color:#ff9b9b;
   padding:10px 12px; border-radius:8px; font-size:13px; margin-bottom:16px; }}
 .warn {{ font-size:12px; color:#8b949e; margin-top:16px; border-top:1px solid #30363d;
   padding-top:14px; line-height:1.5; }}
</style></head>
<body><div class="card">
  <h1>Bilgisayarina erisim izni</h1>
  <p>Asagidaki uygulama <b>pcbridge</b> araciligiyla bilgisayarinda komut calistirma
     yetkisi istiyor.</p>
  <div class="who"><b>{client_name}</b><br>{redirect}</div>
  {error}
  <form method="post" action="/consent">
    <input type="hidden" name="rid" value="{rid}">
    <label for="pw">pcbridge parolasi</label>
    <input id="pw" name="password" type="password" autocomplete="current-password"
           autofocus required>
    <button type="submit">Onayla ve baglan</button>
  </form>
  <div class="warn">Bu istegi sen baslatmadiysan sayfayi kapat ve parolani degistir.
    Onay verirsen bu uygulama terminalinde komut calistirabilir.</div>
</div></body></html>"""

_DONE = """<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>pcbridge</title>
<style>body{background:#0d1117;color:#e6edf3;font-family:system-ui;display:flex;
min-height:100vh;align-items:center;justify-content:center;text-align:center;padding:24px}
a{color:#58a6ff}</style></head><body><div>
<h2>{title}</h2><p>{msg}</p></div></body></html>"""


_MANUAL = """<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>pcbridge - hata ayiklama</title>
<style>
 :root{{color-scheme:dark}}
 body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
   background:#0d1117;color:#e6edf3;font-family:ui-sans-serif,system-ui,sans-serif;padding:24px}}
 .card{{max-width:640px;background:#161b22;border:1px solid #30363d;border-radius:14px;padding:28px}}
 h1{{font-size:18px;margin:0 0 10px}}
 p{{font-size:14px;color:#8b949e;line-height:1.6;margin:0 0 16px}}
 a.btn{{display:block;text-align:center;padding:13px;border-radius:8px;background:#238636;
   color:#fff;text-decoration:none;font-weight:600;font-size:15px}}
 a.btn:hover{{background:#2ea043}}
 textarea{{width:100%;height:110px;margin-top:16px;background:#0d1117;color:#8b949e;
   border:1px solid #30363d;border-radius:8px;padding:10px;font-family:ui-monospace,monospace;
   font-size:11px;resize:vertical}}
 .warn{{font-size:12px;color:#d29922;margin-top:14px;line-height:1.5}}
</style></head><body><div class="card">
<h1>Parola dogrulandi — hata ayiklama modu acik</h1>
<p>Normalde bu noktada otomatik olarak Google'a donulurdu. Bu modda donmuyoruz ki
   karsi tarafin ne dedigini gorebilesin.</p>
<a class="btn" href="{target}">Google'a don ve devam et</a>
<p class="warn">Butona bastiktan sonra ekranda ne yazdigini <b>oku ve not al</b>.
   Hata cikarsa sayfa kapanmadan mesaji kopyala. Kodun omru 5 dakika.</p>
<textarea readonly onclick="this.select()">{target}</textarea>
<p class="warn">Isin bitince config.toml icinde manual_redirect = false yap
   ve servisi yeniden baslat.</p>
</div></body></html>"""


def _err_box(msg: str) -> str:
    return f'<div class="err">{msg}</div>' if msg else ""


def make_consent_routes(provider: SqliteOAuthProvider):
    """FastMCP'ye eklenecek /consent GET ve POST isleyicileri."""

    async def consent_get(request: Request) -> Response:
        rid = request.query_params.get("rid", "")
        pending = provider.peek_pending(rid)
        if not pending:
            return HTMLResponse(
                _DONE.format(
                    title="Istek gecersiz",
                    msg="Baglanti istegi bulunamadi veya suresi doldu. "
                    "Gemini tarafindan tekrar baglanmayi dene.",
                ),
                status_code=400,
            )
        return HTMLResponse(
            _PAGE.format(
                rid=rid,
                client_name=pending.get("client_name", "Bilinmeyen uygulama"),
                redirect=pending.get("redirect_uri", ""),
                error="",
            )
        )

    async def consent_post(request: Request) -> Response:
        form = await request.form()
        rid = str(form.get("rid", ""))
        password = str(form.get("password", ""))
        ip = provider._client_ip(request)

        locked = provider.is_locked(ip)
        if locked > 0:
            return HTMLResponse(
                _DONE.format(
                    title="Cok fazla hatali deneme",
                    msg=f"{int(locked / 60) + 1} dakika sonra tekrar dene.",
                ),
                status_code=429,
            )

        pending = provider.peek_pending(rid)
        if not pending:
            return HTMLResponse(
                _DONE.format(
                    title="Istek gecersiz",
                    msg="Baglanti istegi bulunamadi veya suresi doldu.",
                ),
                status_code=400,
            )

        if not provider.check_password(password):
            provider.record_failure(ip)
            return HTMLResponse(
                _PAGE.format(
                    rid=rid,
                    client_name=pending.get("client_name", ""),
                    redirect=pending.get("redirect_uri", ""),
                    error=_err_box("Parola hatali."),
                ),
                status_code=401,
            )

        provider.clear_failures(ip)
        try:
            target = provider._issue_code(rid)
        except KeyError as exc:
            return HTMLResponse(
                _DONE.format(title="Istek gecersiz", msg=str(exc)), status_code=400
            )
        provider.audit("consent_granted", ip=ip, client=pending.get("client_name"))

        if provider.cfg.manual_redirect:
            # HATA AYIKLAMA MODU: otomatik yonlendirme yerine tiklanabilir bir
            # baglanti gosterilir. Boylece karsi tarafin (Google'in) hata
            # sayfasi ekranda kalir; normalde popup aninda kapandigi icin
            # gorulemiyor.
            return HTMLResponse(_MANUAL.format(target=target))

        # 303 See Other: POST sonrasi yonlendirmede tarayicinin GET'e gecmesini
        # GARANTI eder. 302'de bu davranis teknik olarak tanimsizdir ve bazi
        # istemciler POST'u koruyup karsi tarafta reddedilir.
        return RedirectResponse(target, status_code=303)

    return consent_get, consent_post
