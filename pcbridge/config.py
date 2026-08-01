"""Yapilandirma yukleme."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


DEFAULT_CONFIG_NAMES = ("config.toml",)


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p))).resolve()


@dataclass
class AgentSpec:
    name: str
    enabled: bool = True
    description: str = ""
    command: list[str] = field(default_factory=list)
    resume_args: list[str] = field(default_factory=list)
    parser: str = "plain"
    # Bazi CLI'lar (ornegin agy) ciktilarini yalnizca gercek bir terminale
    # yaziyor; arka planda calistirilinca hicbir sey donmuyor. pty=true ise
    # komut `script` ile sahte bir terminale sarilir.
    pty: bool = False


@dataclass
class Config:
    public_url: str
    host: str
    port: int
    mcp_path: str

    password: str
    static_token: str
    access_token_ttl: int
    refresh_token_ttl: int
    auth_code_ttl: int
    max_failed_attempts: int
    lockout_seconds: int
    manual_redirect: bool

    default_workdir: Path
    state_dir: Path

    max_output_chars: int
    default_job_timeout: int
    max_sync_timeout: int

    agents: dict[str, AgentSpec]
    source_path: Path | None = None

    # -- turetilmis ---------------------------------------------------------
    @property
    def mcp_url(self) -> str:
        return self.public_url.rstrip("/") + self.mcp_path

    @property
    def jobs_dir(self) -> Path:
        return self.state_dir / "jobs"

    @property
    def db_path(self) -> Path:
        return self.state_dir / "oauth.db"

    @property
    def audit_log(self) -> Path:
        return self.state_dir / "audit.log"


def find_config(explicit: str | None = None) -> Path:
    if explicit:
        p = _expand(explicit)
        if not p.exists():
            raise SystemExit(f"Yapilandirma bulunamadi: {p}")
        return p

    env = os.environ.get("PCBRIDGE_CONFIG")
    if env:
        return _expand(env)

    here = Path(__file__).resolve().parent.parent
    for name in DEFAULT_CONFIG_NAMES:
        cand = here / name
        if cand.exists():
            return cand

    cand = _expand("~/.config/pcbridge/config.toml")
    if cand.exists():
        return cand

    raise SystemExit(
        "config.toml bulunamadi.\n"
        f"  cp {here / 'config.example.toml'} {here / 'config.toml'}\n"
        f"  chmod 600 {here / 'config.toml'}\n"
        "sonra dosyayi duzenleyin."
    )


def load_config(explicit: str | None = None) -> Config:
    path = find_config(explicit)
    with path.open("rb") as fh:
        raw: dict[str, Any] = tomllib.load(fh)

    auth = raw.get("auth", {})
    paths = raw.get("paths", {})
    limits = raw.get("limits", {})

    password = os.environ.get("PCBRIDGE_PASSWORD") or auth.get("password", "")
    static_token = os.environ.get("PCBRIDGE_STATIC_TOKEN") or auth.get(
        "static_token", ""
    )

    public_url = (
        os.environ.get("PCBRIDGE_PUBLIC_URL") or raw.get("public_url", "")
    ).rstrip("/")

    if not public_url:
        raise SystemExit("config.toml icinde `public_url` zorunlu.")
    if not public_url.startswith("https://") and "localhost" not in public_url:
        raise SystemExit(
            "`public_url` https:// ile baslamali (Gemini Spark yalnizca HTTPS kabul ediyor)."
        )
    if len(password) < 12:
        raise SystemExit(
            "`auth.password` en az 12 karakter olmali. Uzun ve tahmin edilemez bir parola secin."
        )

    agents: dict[str, AgentSpec] = {}
    for name, spec in (raw.get("agents") or {}).items():
        agents[name] = AgentSpec(
            name=name,
            enabled=bool(spec.get("enabled", True)),
            description=str(spec.get("description", "")),
            command=[str(x) for x in spec.get("command", [])],
            resume_args=[str(x) for x in spec.get("resume_args", [])],
            parser=str(spec.get("parser", "plain")),
            pty=bool(spec.get("pty", False)),
        )

    state_dir = _expand(paths.get("state_dir", "~/.local/state/pcbridge"))
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "jobs").mkdir(parents=True, exist_ok=True)

    return Config(
        public_url=public_url,
        host=str(raw.get("host", "127.0.0.1")),
        port=int(raw.get("port", 8765)),
        mcp_path=str(raw.get("mcp_path", "/mcp")),
        password=password,
        static_token=static_token,
        access_token_ttl=int(auth.get("access_token_ttl", 43200)),
        refresh_token_ttl=int(auth.get("refresh_token_ttl", 7776000)),
        auth_code_ttl=int(auth.get("auth_code_ttl", 300)),
        max_failed_attempts=int(auth.get("max_failed_attempts", 8)),
        lockout_seconds=int(auth.get("lockout_seconds", 900)),
        manual_redirect=bool(
            os.environ.get("PCBRIDGE_MANUAL_REDIRECT")
            or auth.get("manual_redirect", False)
        ),
        default_workdir=_expand(paths.get("default_workdir", "~")),
        state_dir=state_dir,
        max_output_chars=int(limits.get("max_output_chars", 12000)),
        default_job_timeout=int(limits.get("default_job_timeout", 1800)),
        max_sync_timeout=int(limits.get("max_sync_timeout", 120)),
        agents=agents,
        source_path=path,
    )
