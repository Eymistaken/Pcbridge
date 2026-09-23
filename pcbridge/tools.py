"""MCP arac (tool) tanimlari.

Aciklamalar bilincli olarak Ingilizce: Gemini Spark ozel MCP uygulamalarini
su an yalnizca Ingilizce destekliyor ve arac secimini bu metinlere bakarak
yapiyor. Kullaniciya donen metinler Turkce.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult
from mcp.types import ContentBlock, TextContent
from pydantic import Field

from . import __version__
from . import assets as assetslib
from . import sessionctx
from . import executables as exelib
from . import jobs as jobslib
from . import models as modelslib
from . import shots as shotslib
from . import tmuxctl
from .config import Config
from .desktop import apps as appslib
from .desktop import batch as batchlib
from .desktop import capture as capturelib
from .desktop import execution as executionlib
from .desktop import input as inputlib
from .desktop import monitors as monitorslib
from .desktop import ocr as ocrlib
from .desktop import ops as opslib
from .desktop import policy
from .desktop import presentation as presentationlib
from .desktop import safety as safetylib
from .desktop import screencast as screencastlib
from .desktop import uitree as uitreelib
from .desktop.errors import DesktopError, ErrorCategory, ErrorCode
from .desktop.runtime import DesktopRuntime, create_runtime

logger = logging.getLogger("pcbridge.tools")

MAX_INLINE = 4000

# Arac parametrelerinin aciklamalari yapilandirmaya bagli (hangi modeller var,
# varsayilan ajan ne). Ama `from __future__ import annotations` yuzunden
# Annotated[...] icerigi tanimlanma aninda degil, FastMCP semayi cikarirken
# MODUL GLOBAL UZAYINDA eval ediliyor -- yani register()'in yerel `cfg`'si
# oradan gorunmez (NameError). Bu yuzden metinler burada global olarak duruyor
# ve register() icinde dolduruluyor.
_DESC_AGENT = "Agent name, e.g. 'claude' or 'antigravity'."
_DESC_MODEL = "Model to run with."
_DESC_EFFORT = "Reasoning effort level."


# Gorsel ajan yonergesi. TEK KAYNAK: depodaki dosya. `install.sh` bunu
# `~/.claude/skills/computer-use`'a symlink'liyor (kullanici Claude Code'u elle
# surerken lazim), ama `computer_task` symlink'e GUVENMIYOR ve metni dogrudan
# okuyup prompt'a koyuyor: varsayilan surucu `agy` ve onda Claude-skill
# kavrami yok. Tek dosya, tek kod yolu, ajandan bagimsiz.
def _default_skill_path() -> Path:
    """The computer-use skill, from an installed package or a git checkout."""
    try:
        return assetslib.asset_path("skills/computer-use/SKILL.md")
    except FileNotFoundError:
        return Path(__file__).resolve().parent / "_assets" / "skills" / "computer-use" / "SKILL.md"


_SKILL_PATH = _default_skill_path()


# `find_text` / `wait_for_text` parametreleri (Adim 8.6). Modul duzeyinde,
# cunku `from __future__ import annotations` altinda FastMCP tip ipuclarini
# modulun globallerinden cozuyor; `register()` icindeki bir ad bulunamaz.
_OCR_TEXT = Annotated[
    str,
    Field(
        min_length=1,
        max_length=200,
        description="The text to look for, as it reads on screen. Case, "
        "Turkish letters and punctuation do not matter; several words "
        "match consecutive words on one line.",
    ),
]
_OCR_MONITOR = Annotated[
    str,
    Field(
        description="Which screen to read: 'all' (default), a monitor number "
        "like '1' or '2', a connector name, or 'primary'. One monitor is "
        "faster than two."
    ),
]
_OCR_REGION = Annotated[
    list[int] | None,
    Field(
        min_length=4,
        max_length=4,
        description="Read only this part of one monitor: [x, y, width, "
        "height], in the same spaces as screen_capture's region (with shot, "
        "pixels in that screenshot). Smaller is faster and more accurate.",
    ),
]
_OCR_SHOT = Annotated[
    str | None,
    Field(description="With region: the screenshot the region was read off."),
]


def _task_prompt(instructions: str, goal: str, prepared: str, max_steps: int) -> str:
    """Gorsel ajanin alacagi tam prompt.

    Yonerge ONCE geliyor, hedef SONRA: ajan once nasil calisacagini, sonra ne
    yapacagini okusun. Hedef en sonda kaliyor ki uzun yonergenin icinde
    kaybolmasin.
    """
    parts = [instructions.strip(), "", "---", ""]
    if prepared:
        parts += [f"The application has been prepared for you: {prepared}", ""]
    parts += [
        f"Your step budget is about {max_steps} look-and-act rounds. If you are about to exceed it, stop and "
        "write down where you got to.",
        "",
        "When you are done, say in your FINAL ANSWER: what you did, what the screen "
        "shows now, and whether the goal was reached (yes/no). If not, say why -- "
        "a failed task that looks successful is the worst outcome.",
        "",
        "## Task",
        "",
        goal.strip(),
    ]
    return "\n".join(parts)


def _text(s: str) -> TextContent:
    """Duz metni MCP metin bloguna sar (goruntu donduren araclar icin)."""
    return TextContent(type="text", text=s)


def _want_inline(setting: str, transport: str) -> bool:
    """Ekran goruntusu arac sonucunda GORUNTU BLOGU olarak da gitsin mi?

    Saf fonksiyon: I/O yok, `Config` bile almiyor. `models.py`'deki cozumleyici
    gibi sunucu ayakta olmadan test edilebilsin diye.

    Varsayilan artik "true": hedeflenen butun istemciler (Claude Code, Codex,
    Claude Desktop) goruntuyu okuyabiliyor -- olculdu, bilinen icerikli bir
    PNG'deki gizli deger birebir geri geldi.

    "auto" tasimaya bakar ve GERI DONUS YOLU olarak duruyor. Gemini Spark cagindan
    kalma: oraya giden function-response kanali yalnizca metin tasiyordu ve
    goruntu blogu gelince BOZULUYORDU. Goruntu isleyemeyen bir istemciyle
    karsilasilirsa yine ise yarar. Bos deger de buraya duser.
    """
    s = (setting or "auto").strip().lower()
    if s == "true":
        return True
    if s == "false":
        return False
    return transport == "stdio"


def _resolve_dir(cfg: Config, path: str | None) -> Path:
    if not path:
        return cfg.default_workdir
    p = Path(os.path.expanduser(os.path.expandvars(path)))
    if not p.is_absolute():
        p = cfg.default_workdir / p
    return p.resolve()


def _resolve_file(cfg: Config, path: str) -> Path:
    p = Path(os.path.expanduser(os.path.expandvars(path)))
    if not p.is_absolute():
        p = cfg.default_workdir / p
    return p.resolve()


def _model_matches(requested: str, actual: str) -> bool:
    """Istenen model adi ile CLI'in bildirdigi ad ortusuyor mu?

    Istenen genelde takma ad (`opus`), gerceklesen tam isim (`claude-opus-5`).
    Bu yuzden esitlik degil, parca kapsama araniyor.
    """
    want = set(modelslib.normalize(requested).split())
    got = set(modelslib.normalize(actual).split())
    return bool(want) and (want <= got or got <= want)


def _fmt_job_summary(cfg: Config, jm: jobslib.JobManager, job_id: str) -> str:
    st = jm.status(job_id)
    log = jm.read_log(job_id)
    parsed = jobslib.summarize(log, st.get("parser", "plain"))

    lines: list[str] = []

    # Uyarilar en USTTE: yanlis modelle calisan bir is, basarili gorunen bir is
    # olarak asagida kaybolmasin.
    warnings = list(parsed.get("warnings") or [])
    requested = st.get("model")
    actual = parsed.get("actual_model")
    if requested and actual and not _model_matches(requested, actual):
        warnings.append(
            f"Requested model `{requested}` but `{actual}` ran. "
            "The CLI may have ignored the request."
        )
    for w in warnings:
        lines.append(f"⚠️ **{w}**")
    if warnings:
        lines.append("")

    lines += [
        f"**{job_id}** — status: `{st['status']}`"
        + (f" (exit {st['exit_code']})" if st.get("exit_code") is not None else ""),
    ]
    if st.get("agent"):
        head = f"agent: {st['agent']}"
        if st.get("model"):
            head += f" · model: {st['model']}"
        if st.get("effort"):
            head += f" · effort: {st['effort']}"
        if actual and (not requested or not _model_matches(requested, actual)):
            head += f" · actual: {actual}"
        lines.append(head)
    for note in st.get("model_notes") or []:
        lines.append(f"_note: {note}_")
    lines += [
        f"command: `{jobslib.tail_chars(st['command'], 300)}`",
        f"directory: `{st['cwd']}` · elapsed: {st['elapsed_seconds']}s",
    ]
    if parsed.get("session_id"):
        lines.append(
            f"session id: `{parsed['session_id']}` "
            "(pass it to agent_run as resume_session to continue)"
        )
    if parsed.get("steps"):
        lines.append("\n**Steps:**")
        lines.extend(parsed["steps"][-25:])
    if parsed.get("final_answer"):
        lines.append("\n**Result:**")
        lines.append(jobslib.tail_chars(str(parsed["final_answer"]), MAX_INLINE))
    elif st["status"] == "running":
        lines.append("\n(still running — check again with job_status in a few seconds)")
    if parsed.get("unparsed"):
        lines.append("\n**Raw output (not parsed):**")
        lines.append("\n".join(parsed["unparsed"]))
    if not parsed.get("final_answer") and not parsed.get("steps") and log.strip():
        lines.append("\n**Output:**")
        lines.append(jobslib.tail_chars(jobslib.strip_ansi(log), MAX_INLINE))
    if parsed.get("cost_usd") is not None:
        lines.append(f"\n_maliyet: ${parsed['cost_usd']:.4f} · tur: {parsed.get('num_turns')}_")
    elif parsed.get("total_tokens") is not None:
        lines.append(
            f"\n_jeton: {parsed['total_tokens']:,} · tur: {parsed.get('num_turns')}_"
        )
    return "\n".join(lines)


# Every tool's MCP hints, decided once and pinned by
# tests/contracts/test_tool_surface.py (Step 7 of 2.0). An unset hint falls
# back to the spec default (destructive, open world), so none is left unset.
#   ro   readOnlyHint    changes nothing on the machine
#   de   destructiveHint may change or delete what exists (kept conservative:
#                        anything that drives input or runs commands)
#   id   idempotentHint  the same call twice has no further effect
#   ow   openWorldHint   reaches content outside pcbridge's own state: the
#                        network, other applications, the screen (screen text
#                        is untrusted input, so screen readers say so)
TOOL_HINTS: dict[str, tuple[bool, bool, bool, bool]] = {
    #                        ro     de     id     ow
    "agent_run":            (False, True,  False, True),
    "list_agents":          (True,  False, True,  False),
    "job_status":           (True,  False, True,  False),
    "job_output":           (True,  False, True,  False),
    "job_list":             (True,  False, True,  False),
    "job_cancel":           (False, True,  True,  False),
    "tmux_list":            (True,  False, True,  False),
    "tmux_start":           (False, False, True,  False),
    "tmux_send":            (False, True,  False, True),
    "tmux_keys":            (False, True,  False, True),
    "tmux_capture":         (True,  False, True,  False),
    "tmux_kill":            (False, True,  True,  False),
    "shell_run":            (False, True,  False, True),
    "shell_run_background": (False, True,  False, True),
    "fs_list":              (True,  False, True,  False),
    "fs_read":              (True,  False, True,  False),
    "fs_search":            (True,  False, True,  False),
    "fs_write":             (False, True,  True,  False),
    "notify":               (False, False, False, False),
    "system_status":        (True,  False, True,  False),
    "system_capabilities":  (True,  False, True,  False),
    "desktop_unlock":       (False, True,  False, False),
    "desktop_lock":         (False, False, True,  False),
    "screen_info":          (True,  False, True,  False),
    "screen_capture":       (True,  False, False, True),
    "find_text":            (True,  False, False, True),
    "wait_for_text":        (True,  False, False, True),
    "ui_dump":              (True,  False, False, True),
    "window_list":          (True,  False, True,  True),
    "ui_click":             (False, True,  False, True),
    "ui_set_text":          (False, True,  True,  True),
    "window_focus":         (False, True,  False, True),
    "keyboard":             (False, True,  False, True),
    "mouse":                (False, True,  False, True),
    "computer_batch":       (False, True,  False, True),
    "computer_task":        (False, True,  False, True),
}


# `[tools] profile = "desktop"` keeps these plus the job and status tools;
# "core" drops them.
DESKTOP_TOOLS = frozenset({
    "desktop_unlock", "desktop_lock", "system_capabilities", "screen_info",
    "screen_capture", "find_text", "wait_for_text", "ui_dump", "window_list",
    "ui_click", "ui_set_text", "window_focus", "keyboard", "mouse",
    "computer_batch", "computer_task",
})
_DESKTOP_PROFILE_EXTRA = frozenset({
    "system_status", "notify", "job_status", "job_output", "job_list", "job_cancel",
})


def tools_in_profile(profile: str) -> frozenset[str]:
    """The tool names a `[tools] profile` offers."""
    every = frozenset(TOOL_HINTS)
    if profile == "core":
        return every - DESKTOP_TOOLS
    if profile == "desktop":
        return DESKTOP_TOOLS | _DESKTOP_PROFILE_EXTRA
    return every


def _with_hints(mcp: FastMCP, profile: str = "full") -> None:
    """Make `mcp.tool` take each tool's hints from TOOL_HINTS, and leave out
    tools the profile does not offer.

    A tool missing from the table fails at registration, so a new tool
    cannot ship with unset hints.
    """
    plain = mcp.tool
    offered = tools_in_profile(profile)

    def tool(*args: Any, **kwargs: Any):
        annotations = dict(kwargs.pop("annotations", None) or {})

        def decorate(fn):
            name = kwargs.get("name") or fn.__name__
            ro, de, idem, ow = TOOL_HINTS[name]
            if name not in offered:
                return fn
            annotations.update(readOnlyHint=ro, destructiveHint=de,
                               idempotentHint=idem, openWorldHint=ow)
            return plain(*args, annotations=annotations, **kwargs)(fn)

        return decorate

    mcp.tool = tool  # type: ignore[method-assign]


def register(
    mcp: FastMCP,
    cfg: Config,
    jm: jobslib.JobManager,
    shot_store: "shotslib.ShotStore | None" = None,
    transport: str = "http",
    runtime: DesktopRuntime | None = None,
) -> DesktopRuntime:
    global _DESC_AGENT, _DESC_MODEL, _DESC_EFFORT
    _with_hints(mcp, getattr(cfg, "tools_profile", "full"))
    _DESC_AGENT = (
        "Agent name; configured here: "
        + (", ".join(f"'{n}'" for n, a in cfg.agents.items() if a.enabled) or "none")
        + ". Optional: if omitted it is "
        f"inferred from the model, defaulting to '{cfg.default_agent}'. Give it "
        "explicitly to reach an agent's restricted models."
    )
    _DESC_MODEL = (
        "Model to run with. Aliases and free text are accepted. Valid values — "
        + (modelslib.model_hint(cfg) or "(not configured)")
        + ". Omit to use the agent's default."
    )
    _DESC_EFFORT = (
        "Reasoning effort. Valid values — "
        + (modelslib.effort_hint(cfg) or "(not configured)")
        + ". Higher costs more; omit to use the model's default."
    )

    # SafetyGate iki is yapiyor: masaustu kapisi (asagida) ve DENETIM KAYDI.
    # Ikincisi masaustune ozel degil -- kabuk, ajan ve dosya araclari da buraya
    # yaziyor. Eskiden yalnizca masaustu araclari kayit tutuyordu, yani en sert
    # denetim en zayif araclardaydi; `shell_run` keyfi komut calistirmasina
    # ragmen hicbir iz birakmiyordu.
    if runtime is None:
        runtime = create_runtime(cfg, gate=safetylib.SafetyGate(cfg))
    gate = runtime.gate

    def _inline_images() -> bool:
        # Per call: the daemon serves local (socket) and HTTP sessions at once.
        return _want_inline(cfg.inline_images, sessionctx.transport(transport))

    def _session_env() -> dict[str, str] | None:
        # Allow-listed client environment (PATH, SSH_AUTH_SOCK, LANG, ...) for
        # processes started on behalf of a daemon session; None elsewhere.
        return sessionctx.job_env() or None

    # `shot="m2-a1b2c3"` kimliginin aranacagi dizinler (MCP'nin yazdigi yer +
    # `pcb-shot`unki). Ikisi de arandigi icin ajan hangi yoldan bakmis
    # oldugunu hatirlamak zorunda degil.
    shot_dirs = list(cfg.shot_search_dirs)
    # `shot` unutuldugunda supheli koordinati reddetme penceresi. 0 = kapali.
    guard_age = (float(cfg.desktop.agent_shot_max_age_seconds)
                 if cfg.desktop.ambiguous_coord_guard else 0.0)

    def _to_global(x: int, y: int, monitor: int | None, shot: str | None):
        """Koordinati global uzaya cevir — TEK GECIT (`capture.to_global`).

        Donusum burada YAZILMIYOR, yalnizca dizin listesi baglaniyor. Ikinci
        bir kopya cikarsa gunun birinde biri guncellenmez ve sessizce 1920
        piksel sola tiklanir.
        """
        return capture_provider.to_global(
            x, y, monitor=monitor, shot=shot, dirs=shot_dirs,
            guard_age=guard_age,
        )

    def _stale_note(shot: str | None) -> str:
        """Cekim bayatladiysa uyari. REDDETME degil: `mouse` bugune kadar yas
        kontrolu yapmiyordu, yeni bir kapi eklemek geriye donuk bir kirilma
        olurdu. `pcb-do` tarafinda reddetme aynen duruyor."""
        limit = cfg.desktop.agent_shot_max_age_seconds
        if not shot or limit <= 0:
            return ""
        try:
            age = capture_provider.load_shot(shot, shot_dirs).age
        except (capturelib.CaptureError, DesktopError):
            return ""
        if age <= limit:
            return ""
        return (
            f"\n⚠️ `{shot}` is {int(age)} s old (limit {limit} s). The windows may "
            "have changed since; take a FRESH screenshot before "
            "clicking."
        )

    def _short(text: str, limit: int = 120) -> str:
        s = " ".join(str(text or "").split())
        return s if len(s) <= limit else s[: limit - 1] + "…"

    # ================================================================= AJANLAR
    @mcp.tool(
        annotations={"title": "List available coding agents"},
    )
    def list_agents() -> str:
        """List the coding agents installed on the computer (Claude Code,
        Antigravity CLI, ...), whether their executables are found on PATH, and
        which models and reasoning effort levels each one accepts. Call this
        first if you are unsure which agent, model or effort value to use."""
        out = ["**Configured agents**", ""]
        for name, spec in cfg.agents.items():
            if not spec.enabled:
                out.append(f"- `{name}` — disabled (config.toml)")
                continue
            exe = spec.command[0] if spec.command else ""
            found = exelib.find_executable(exe)
            where = str(found) if found else ""
            mark = "✅" if where else "❌ not installed (PATH and the usual user bin directories)"
            out.append(f"- `{name}` — {spec.description or exe} · {mark} {where}")
            out.append(f"  - command: `{shlex.join(spec.command)}`")
            out.extend(modelslib.describe_agent(spec))
            out.append("")
        out.append(f"agent when none is given: `{cfg.default_agent}`")
        out.append(f"default working directory: `{cfg.default_workdir}`")
        return "\n".join(out)

    @mcp.tool(
        annotations={"title": "Send a prompt to a coding agent"},
    )
    def agent_run(
        prompt: Annotated[
            str, Field(description="The instruction to send to the agent.")
        ],
        agent: Annotated[str | None, Field(description=_DESC_AGENT)] = None,
        model: Annotated[str | None, Field(description=_DESC_MODEL)] = None,
        effort: Annotated[str | None, Field(description=_DESC_EFFORT)] = None,
        workdir: Annotated[
            str | None,
            Field(description="Absolute path of the project directory to run in."),
        ] = None,
        resume_session: Annotated[
            str | None,
            Field(
                description="Session id from a previous run, to continue that "
                "conversation instead of starting a new one."
            ),
        ] = None,
        wait_seconds: Annotated[
            int,
            Field(
                ge=0,
                le=110,
                description="Block up to this many seconds waiting for the agent to "
                "finish. Use 0 to return immediately, 30-60 for short tasks.",
            ),
        ] = 30,
        timeout: Annotated[
            int | None,
            Field(description="Kill the agent after this many seconds. Default 1800."),
        ] = None,
    ) -> str:
        """Start a coding agent (Claude Code / Antigravity CLI) on the user's Linux
        desktop with the given prompt. Runs in the background and survives long
        tasks. Returns a job id; poll it with job_status. Use model/effort to pick
        how much reasoning power the task deserves — the default is a cheap, fast
        model, so ask for a stronger one for hard work. Call list_agents to see
        what is available. This can modify files and run commands on the machine."""
        res = modelslib.resolve(cfg, agent=agent, model=model, effort=effort)
        if res.error:
            return res.error

        spec = cfg.agents[res.agent]
        argv = [
            a.replace("{prompt}", prompt) if "{prompt}" in a else a
            for a in spec.command
        ]
        argv += modelslib.build_args(spec, res)
        env = _session_env()
        exe = exelib.find_executable(argv[0], (env or {}).get("PATH")) if argv else None
        if exe is None:
            return exelib.not_found_message(res.agent, argv[0] if argv else "", cfg.source_path)
        argv[0] = str(exe)
        if resume_session and spec.resume_args:
            argv += [a.replace("{session_id}", resume_session) for a in spec.resume_args]

        cwd = _resolve_dir(cfg, workdir)
        if not cwd.is_dir():
            return f"Directory not found: {cwd}"

        job_id = jm.start(
            kind=f"agent:{res.agent}",
            argv=argv,
            cwd=cwd,
            label=jobslib._short(prompt, 90),
            parser=spec.parser,
            timeout=timeout,
            pty=spec.pty,
            env=env,
            extra={
                "agent": res.agent,
                "model": res.model,
                "effort": res.effort,
                "model_notes": res.notes,
                "prompt": prompt,
                "resume_session": resume_session,
            },
        )

        # Prompt METNI kaydedilmez, yalnizca uzunlugu: parola ya da ozel bilgi
        # icerebilir. Tam metin zaten jobs/<id>/meta.json'da duruyor -- oradan
        # okumak icin dosya sistemine erisim gerekir, denetim kaydini okumak
        # yetmez.
        gate.audit("agent_run", agent=res.agent, model=res.model,
                   effort=res.effort, job=job_id, prompt_chars=len(prompt),
                   resumed=bool(resume_session) or None)
        if wait_seconds > 0:
            jm.wait(job_id, wait_seconds)
        return _fmt_job_summary(cfg, jm, job_id)

    # =================================================================== ISLER
    @mcp.tool(annotations={"title": "Check a background job"})
    def job_status(
        job_id: Annotated[str, Field(description="Job id returned by agent_run.")],
        wait_seconds: Annotated[
            int,
            Field(ge=0, le=110, description="Optionally wait this long for completion."),
        ] = 0,
    ) -> str:
        """Get the status, progress steps and final answer of a background job."""
        try:
            if wait_seconds:
                jm.wait(job_id, wait_seconds)
            return _fmt_job_summary(cfg, jm, job_id)
        except KeyError as exc:
            return str(exc)

    @mcp.tool(annotations={"title": "Read raw job output"})
    def job_output(
        job_id: Annotated[str, Field(description="Job id.")],
        tail_chars: Annotated[
            int, Field(ge=200, le=60000, description="How many trailing characters.")
        ] = 6000,
    ) -> str:
        """Read the raw, unparsed terminal output of a background job. Use when
        job_status does not show enough detail or the agent crashed."""
        try:
            jm.read_meta(job_id)
        except KeyError as exc:
            return str(exc)
        log = jobslib.strip_ansi(jm.read_log(job_id))
        if not log.strip():
            return "No output yet."
        return "```\n" + jobslib.tail_chars(log, tail_chars) + "\n```"

    @mcp.tool(annotations={"title": "List background jobs"})
    def job_list(
        limit: Annotated[int, Field(ge=1, le=100)] = 15,
        only_running: bool = False,
    ) -> str:
        """List recent background jobs on the computer, newest first."""
        rows = jm.list_jobs(limit=limit, only_running=only_running)
        if not rows:
            return "No jobs recorded."
        out = ["| job_id | kind | status | elapsed | label |", "|---|---|---|---|---|"]
        for r in rows:
            out.append(
                f"| `{r['job_id']}` | {r['kind']} | {r['status']} | "
                f"{r['elapsed_seconds']}s | {r['label']} |"
            )
        return "\n".join(out)

    @mcp.tool(annotations={"title": "Cancel a job"})
    def job_cancel(job_id: str) -> str:
        """Stop a running background job (sends SIGTERM, then SIGKILL)."""
        try:
            out = jm.cancel(job_id)
        except KeyError as exc:
            return str(exc)
        gate.audit("job_cancel", job=str(job_id)[:40])
        return out

    # =================================================================== TMUX
    @mcp.tool(annotations={"title": "List live terminal sessions"})
    def tmux_list() -> str:
        """List the live tmux terminal sessions on the computer. These are real
        terminals the user can also attach to physically."""
        if not tmuxctl.available():
            return "tmux is not installed: `sudo apt install tmux`"
        rows = tmuxctl.list_sessions()
        if not rows:
            return "No tmux sessions are open."
        out = ["| session | running | directory | attached on the PC |", "|---|---|---|---|"]
        for r in rows:
            out.append(
                f"| `{r['session']}` | {r['running']} | {r['path']} | "
                f"{'yes' if r['attached_on_pc'] else 'no'} |"
            )
        return "\n".join(out)

    @mcp.tool(annotations={"title": "Open a live terminal session"})
    def tmux_start(
        session: Annotated[str, Field(description="Short name, e.g. 'cc' or 'work'.")],
        command: Annotated[
            str | None,
            Field(
                description="Command to launch inside, e.g. 'claude'. Omit for a plain shell."
            ),
        ] = None,
        workdir: str | None = None,
    ) -> str:
        """Open a new persistent terminal session on the computer, optionally
        launching an interactive CLI (like `claude`) inside it."""
        if not tmuxctl.available():
            return "tmux is not installed: `sudo apt install tmux`"
        cwd = _resolve_dir(cfg, workdir)
        if not cwd.is_dir():
            return f"Directory not found: {cwd}"
        try:
            msg = tmuxctl.start(session, command, str(cwd))
        except tmuxctl.TmuxError as exc:
            return f"Error: {exc}"
        time.sleep(1.2)
        try:
            screen = tmuxctl.capture(session, 25)
        except tmuxctl.TmuxError:
            screen = ""
        return (
            f"{msg}\nTo watch it on the PC: `{tmuxctl.attach_hint(session)}`\n\n"
            f"```\n{screen}\n```"
        )

    @mcp.tool(annotations={"title": "Type into a live terminal"})
    def tmux_send(
        session: Annotated[str, Field(description="Session name from tmux_list.")],
        text: Annotated[str, Field(description="Text to type into the terminal.")],
        press_enter: bool = True,
        capture_after_seconds: Annotated[
            int,
            Field(ge=0, le=90, description="Wait this long, then return the screen."),
        ] = 6,
    ) -> str:
        """Type text into an existing live terminal session and press Enter. Use
        this to talk to an interactive CLI that is already running (for example an
        open Claude Code session), then read what appeared on screen."""
        try:
            tmuxctl.send_text(session, text, press_enter=press_enter)
        except tmuxctl.TmuxError as exc:
            return f"Error: {exc}"
        # Gonderilen METIN kaydedilmez, uzunlugu kaydedilir: terminale parola
        # yazilmis olabilir.
        gate.audit("tmux_send", session=str(session)[:40], chars=len(text or ""))
        if capture_after_seconds:
            time.sleep(capture_after_seconds)
        try:
            return f"Sent.\n\n```\n{tmuxctl.capture(session, 45)}\n```"
        except tmuxctl.TmuxError as exc:
            return f"Sent, but the screen could not be read: {exc}"

    @mcp.tool(annotations={"title": "Press keys in a live terminal"})
    def tmux_keys(
        session: str,
        keys: Annotated[
            list[str],
            Field(
                description="tmux key names, e.g. ['Enter'], ['Escape'], ['C-c'], "
                "['Down','Enter'], ['y']. Use for confirming prompts or menus."
            ),
        ],
        capture_after_seconds: Annotated[int, Field(ge=0, le=60)] = 3,
    ) -> str:
        """Send raw key presses (Enter, Escape, Ctrl-C, arrow keys, y/n) to a live
        terminal session — useful for answering an agent's confirmation prompt."""
        try:
            tmuxctl.send_keys(session, keys)
        except tmuxctl.TmuxError as exc:
            return f"Error: {exc}"
        gate.audit("tmux_keys", session=str(session)[:40],
                   keys=_short(" ".join(keys or []), 60))
        if capture_after_seconds:
            time.sleep(capture_after_seconds)
        try:
            return f"```\n{tmuxctl.capture(session, 45)}\n```"
        except tmuxctl.TmuxError as exc:
            return str(exc)

    @mcp.tool(annotations={"title": "Read a live terminal screen"})
    def tmux_capture(
        session: str,
        lines: Annotated[int, Field(ge=5, le=400)] = 60,
    ) -> str:
        """Read the current contents of a live terminal session's screen."""
        try:
            return f"```\n{tmuxctl.capture(session, lines)}\n```"
        except tmuxctl.TmuxError as exc:
            return f"Error: {exc}"

    @mcp.tool(annotations={"title": "Close a live terminal"})
    def tmux_kill(session: str) -> str:
        """Close a live terminal session and everything running inside it."""
        try:
            out = tmuxctl.kill(session)
        except tmuxctl.TmuxError as exc:
            return f"Error: {exc}"
        gate.audit("tmux_kill", session=str(session)[:40])
        return out

    # ================================================================== SHELL
    def _gui_launch_block(command: str) -> str | None:
        """Kabuktan GUI uygulamasi baslatma denemesi mi? Gerekce ya da None.

        Kapi YALNIZCA masaustu izni acikken isliyor: masaustu kapaliyken
        kabuk normal kabuktur ve pcbridge'in kullaniciya "su uygulamayi
        boyle acma" demesi icin bir sebebi yok.
        """
        spec = cfg.desktop
        if not spec.enabled or not spec.block_gui_launch_in_shell:
            return None
        if not spec.gui_launch_blocklist:
            return None          # bos liste = hicbir sey engellenmez
        if not gate.is_unlocked():
            return None
        try:
            hit = appslib.looks_like_gui_launch(
                command, spec.gui_launch_blocklist
            )
        except Exception as exc:  # noqa: BLE001
            # Tespit CALISMAZSA kabuk calismaya devam etsin: bu kapi bir
            # kolaylik, guvenlik siniri degil. Sessiz kalmiyoruz ama.
            logger.warning("gui_launch detection failed: %s", exc)
            return None
        if not hit:
            return None
        gate.audit("shell_run_denied", reason="gui_launch", app=hit[:60])
        return (
            f"⛔ `{hit}` is a desktop application; the shell does not start it.\n"
            "An application started from the shell becomes a child of this server, and "
            "restarting pcbridge closes it; it also often gets no application id, so "
            "`window_list` and "
            "`window_focus` cannot find its window later.\n"
            f"Instead: window_focus(\"{hit}\")"
        )

    @mcp.tool(annotations={"title": "Run a shell command"})
    def shell_run(
        command: Annotated[str, Field(description="Shell command line to execute.")],
        workdir: str | None = None,
        timeout: Annotated[int, Field(ge=1, le=120)] = 60,
    ) -> str:
        """Run a short shell command on the user's Linux desktop and return its
        output. For anything that may take longer than a minute use
        shell_run_background instead. This path can also send a deterministic
        request, such as a URL, to an application that is already running. Use
        window_focus when a new graphical process must survive pcbridge restarts
        and remain discoverable as a desktop window."""
        denied = _gui_launch_block(command)
        if denied:
            return denied
        cwd = _resolve_dir(cfg, workdir)
        if not cwd.is_dir():
            # Reddi de KAYDEDIYORUZ. Eskiden bu dal sessizce donuyordu ve
            # `audit.log`'da hic iz birakmiyordu: reddedilen bir cagri hic
            # olmamis gibi gorunuyordu. 2026-08-21'de bir test hatasinin
            # gorunmez kalmasinin sebebi tam olarak buydu.
            gate.audit("shell_run_denied", reason="no_workdir",
                       path=str(cwd)[:200])
            return f"Directory not found: {cwd}"
        limit = min(timeout, cfg.max_sync_timeout)
        started = time.monotonic()
        try:
            extra_env = _session_env()
            proc = subprocess.run(
                ["bash", "-lc", command],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=limit,
                stdin=subprocess.DEVNULL,
                env={**os.environ, **extra_env} if extra_env else None,
            )
        except subprocess.TimeoutExpired:
            gate.audit("shell_run", cmd=_short(command), timeout=limit)
            return (
                f"`{command}` did not finish within {limit} s and was stopped. "
                "For longer work use shell_run_background."
            )
        # Komut kaydedilir, CIKTISI kaydedilmez: cikti parola, token ya da
        # ozel yazisma icerebilir. `ui_set_text`teki kural burada da gecerli.
        gate.audit("shell_run", cmd=_short(command), exit=proc.returncode,
                   seconds=round(time.monotonic() - started, 1))
        body = jobslib.strip_ansi((proc.stdout or "") + (proc.stderr or ""))
        head = f"`$ {command}` (directory: {cwd}) → exit {proc.returncode}"
        if not body.strip():
            return head + "\n(no output)"
        return head + "\n```\n" + jobslib.tail_chars(body, MAX_INLINE) + "\n```"

    @mcp.tool(
        annotations={"title": "Run a long shell command in background"}
    )
    def shell_run_background(
        command: str,
        workdir: str | None = None,
        timeout: int | None = None,
    ) -> str:
        """Start a long-running shell command in the background (builds, installs,
        downloads). Returns a job id to poll with job_status. A new graphical
        process started here shares pcbridge's service lifetime; use window_focus
        when the desktop must own that process and keep its window discoverable."""
        # Kapi BURADA DA duruyor. Yalnizca `shell_run` kapatilsaydi ajan
        # digerine duser ve kural hicbir sey yapmamis olurdu.
        denied = _gui_launch_block(command)
        if denied:
            return denied
        cwd = _resolve_dir(cfg, workdir)
        if not cwd.is_dir():
            gate.audit("shell_run_background_denied", reason="no_workdir",
                       path=str(cwd)[:200])
            return f"Directory not found: {cwd}"
        job_id = jm.start(
            kind="shell",
            argv=["bash", "-lc", command],
            cwd=cwd,
            label=jobslib._short(command, 90),
            parser="plain",
            timeout=timeout,
            env=_session_env(),
        )
        gate.audit("shell_run_background", cmd=_short(command), job=job_id)
        return f"Started: `{job_id}`\nStatus: job_status('{job_id}')"

    # =================================================================== DOSYA
    @mcp.tool(annotations={"title": "List a directory"})
    def fs_list(
        path: str | None = None,
        show_hidden: bool = False,
    ) -> str:
        """List the contents of a directory on the computer with sizes and dates."""
        d = _resolve_dir(cfg, path)
        if not d.is_dir():
            return f"Directory not found: {d}"
        entries = []
        for item in sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if not show_hidden and item.name.startswith("."):
                continue
            try:
                stat = item.stat()
                size = "-" if item.is_dir() else f"{stat.st_size:,}"
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))
            except OSError:
                size, when = "?", "?"
            kind = "dir " if item.is_dir() else "file"
            entries.append(f"{kind}  {size:>12}  {when}  {item.name}")
        if not entries:
            return f"`{d}` is empty."
        return f"`{d}` ({len(entries)} entries)\n```\n" + "\n".join(entries[:400]) + "\n```"

    @mcp.tool(annotations={"title": "Read a file"})
    def fs_read(
        path: Annotated[str, Field(description="Absolute path of the file to read.")],
        max_chars: Annotated[int, Field(ge=200, le=60000)] = 8000,
    ) -> str:
        """Read the contents of a text file on the computer."""
        f = _resolve_file(cfg, path)
        if not f.is_file():
            return f"File not found: {f}"
        try:
            data = f.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"Could not read it: {exc}"
        # YOL kaydedilir, ICERIK kaydedilmez. Bu satirin asil amaci: `config.toml`
        # parola ve statik token iceriyor ve okunmasi engellenmis DEGIL (engellemek
        # aldatici olurdu -- `shell_run` zaten keyfi komut calistiriyor, `cat` ile
        # de okunur). Engellemek yerine IZ birakiliyor.
        gate.audit("fs_read", path=str(f)[:200], bytes=f.stat().st_size)
        return f"`{f}` ({f.stat().st_size:,} bayt)\n```\n" + jobslib.tail_chars(
            data, max_chars
        ) + "\n```"

    @mcp.tool(annotations={"title": "Write a file"})
    def fs_write(
        path: Annotated[str, Field(description="Absolute path of the file to write.")],
        content: str,
        append: Annotated[
            bool, Field(description="Append instead of overwriting.")
        ] = False,
    ) -> str:
        """Create or overwrite a text file on the computer. Overwrites existing
        content unless append is true."""
        f = _resolve_file(cfg, path)
        f.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        try:
            with f.open(mode, encoding="utf-8") as fh:
                fh.write(content)
        except OSError as exc:
            return f"Could not write it: {exc}"
        gate.audit("fs_write", path=str(f)[:200], chars=len(content or ""),
                   append=append or None)
        return f"{'Appended' if append else 'Written'}: `{f}` ({f.stat().st_size:,} bytes)"

    @mcp.tool(annotations={"title": "Search inside files"})
    def fs_search(
        query: Annotated[str, Field(description="Text or regex to search for.")],
        path: str | None = None,
        max_results: Annotated[int, Field(ge=1, le=200)] = 40,
    ) -> str:
        """Search for text inside files under a directory (uses ripgrep if
        available, otherwise grep)."""
        d = _resolve_dir(cfg, path)
        if not d.is_dir():
            return f"Directory not found: {d}"
        has_rg = (
            subprocess.run(["bash", "-lc", "command -v rg"], capture_output=True).returncode
            == 0
        )
        if has_rg:
            cmd = f"rg -n --no-heading -m {max_results} -- {shlex.quote(query)} ."
        else:
            cmd = f"grep -rnI -m {max_results} -- {shlex.quote(query)} ."
        proc = subprocess.run(
            ["bash", "-lc", cmd], cwd=str(d), capture_output=True, text=True, timeout=60
        )
        out = (proc.stdout or "").strip()
        if not out:
            return f"No results for `{query}` under `{d}`."
        lines = out.splitlines()[:max_results]
        return f"{len(lines)} result(s) under `{d}`\n```\n" + "\n".join(lines) + "\n```"

    # =============================================================== MASAUSTU
    # Klavye/fare kontrolu. Her cagri once SafetyGate'ten gecer: [desktop]
    # enabled, ekran kilidi, sureli izin, kullanici cakismasi, hiz siniri.
    backend = runtime.input_provider
    tree = runtime.accessibility_provider
    capture_provider = runtime.capture_provider
    capturelib.set_max_pixels(cfg.desktop.screenshot_max_pixels)

    def _unavailable_result(
        capability_name: str,
        *,
        text: str,
        message: str,
        scope: str,
        backend_name: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> ToolResult:
        observed = runtime.capabilities().capabilities.get(capability_name)
        error = presentationlib.capability_error(
            observed,
            message=message,
            scope=scope,
            backend=backend_name,
        )
        return presentationlib.desktop_error_result(error, text=text, extra=extra)

    def _exception_result(
        error: Exception,
        *,
        text: str,
        category: ErrorCategory,
        scope: str,
        backend_name: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> ToolResult:
        typed = presentationlib.execution_error(
            error,
            category=category,
            scope=scope,
            backend=backend_name,
        )
        return presentationlib.desktop_error_result(
            typed,
            text=text,
            permission_scope=scope,
            extra=extra,
        )

    def _open_screencast() -> str:
        """Masaustu izniyle birlikte ekran yayinini ac.

        Yayin `screen_capture` cagrilmasa bile aciliyor: GNOME'un paylasim
        gostergesi kullaniciya "ajan su an masaustune erisebiliyor" diyor ve
        izin acikken bu her zaman dogru. Cekim beklemek gostergeyi izinden
        daha gec baslatirdi.

        Yayin acilamazsa izin YINE DE verilir -- `gnome-screenshot` yedegi
        duruyor, yalnizca flas patlatiyor. Sebebi kullaniciya soyleniyor.
        """
        if cfg.desktop.capture_backend == "gnome-screenshot":
            return ""
        try:
            runtime.start_capture(cursor=cfg.desktop.include_pointer)
        except DesktopError as exc:
            if exc.code == ErrorCode.DISPLAY_MAPPING_UNKNOWN:
                return (
                    "⚠️ Screen sharing could not start "
                    f"(the monitor table could not be read: {exc})."
                )
            if cfg.desktop.capture_backend == "screencast":
                return (
                    f"⚠️ Screen sharing could not start: {exc}\n"
                    "With `capture_backend = \"screencast\"` no screenshot can be "
                    "taken; set it to `auto` to fall back to gnome-screenshot "
                    "(which flashes the screen)."
                )
            return (
                f"⚠️ Screen sharing could not start: {exc}\n"
                "Screenshots will use gnome-screenshot, which flashes the screen and "
                "plays a sound on every capture."
            )
        except monitorslib.MonitorError as exc:
            return f"⚠️ Screen sharing could not start (the monitor table could not be read: {exc})."
        except screencastlib.ScreenCastError as exc:
            if cfg.desktop.capture_backend == "screencast":
                return (
                    f"⚠️ Screen sharing could not start: {exc}\n"
                    "With `capture_backend = \"screencast\"` no screenshot can be "
                    "taken; set it to `auto` to fall back to gnome-screenshot "
                    "(which flashes the screen)."
                )
            return (
                f"⚠️ Screen sharing could not start: {exc}\n"
                "Screenshots will use gnome-screenshot, which flashes the screen and "
                "plays a sound on every capture."
            )
        return (
            "📷 Screen sharing is on: screenshots are silent (no flash). "
            "The sharing indicator in the panel disappears when the grant closes."
        )

    def _held_note() -> str:
        """Basili tutulan varsa yanitin sonuna eklenecek not.

        Ajanin `release`i unutmasi sessiz kalmasin: her cagrida gorunur.
        Zamanlayici bir sey biraktiysa o da BIR KEZ bildirilir.
        """
        parts: list[str] = []
        freed = backend.take_auto_released()
        if freed:
            parts.append(
                f"⚠ released automatically after {cfg.desktop.hold_max_seconds} s: "
                f"{', '.join(freed)}"
            )
        still = backend.held()
        if still:
            parts.append(f"held down: {', '.join(still)}")
        return ("\n· " + "\n· ".join(parts)) if parts else ""

    # ------------------------------------------- kayan kira: computer_task
    # Yerel gorsel ajan ekrani `pcb-shot` ile okuyup `pcb-do` ile suruyor ve
    # IKISI DE `gate.check()`ten geciyor, yani ajanin EYLEMLERI kirayi zaten
    # kaydiriyor. Kapatilmayan tek bosluk ajanin DUSUNME suresi: bir ekran
    # goruntusune bakip karar vermek `unlock_idle_seconds`i asabilir ve izin
    # gorevin ORTASINDA duserdi -- bir sonraki `pcb-do` reddedilir, gorev
    # yarim kalir.
    #
    # Sert tavan (`unlock_default_minutes`) burada da gecerli: `gate.touch()`
    # `hard_until`i asamiyor, yani kalp atisi izni sonsuza uzatamaz. Isin
    # kendisi olunce kayit bosalir ve is parcacigi CIKAR; boste kalan bir
    # zamanlayici birakmiyoruz.
    _hb_jobs: dict[str, object] = {}
    _hb_lock = threading.Lock()
    _hb_thread: dict[str, "threading.Thread | None"] = {"t": None}

    def _heartbeat_loop() -> None:
        # Esigin ucte biri: bir tur kacirilsa bile izin dusmeden once ikinci
        # bir sans var.
        period = max(5, int(cfg.desktop.unlock_idle_seconds or 0) // 3)
        while True:
            time.sleep(period)
            with _hb_lock:
                for jid in list(_hb_jobs):
                    try:
                        if jm.status(jid).get("status") != "running":
                            _hb_jobs.pop(jid, None)
                    except Exception:  # noqa: BLE001 — is kaybolduysa da birak
                        _hb_jobs.pop(jid, None)
                if not _hb_jobs:
                    # Cikis ve slot temizligi AYNI kilit altinda: aksi halde
                    # tam bu arada eklenen bir is, olmek uzere olan bu is
                    # parcacigina guvenip kalp atissiz kalirdi.
                    _hb_thread["t"] = None
                    return
                tokens = set(_hb_jobs.values())
            for token in tokens:
                if not runtime.touch_grant(token):
                    with _hb_lock:
                        stale = [jid for jid, value in _hb_jobs.items() if value == token]
                        for jid in stale:
                            _hb_jobs.pop(jid, None)

    def _heartbeat_add(job_id: str, token: object | None) -> None:
        if int(cfg.desktop.unlock_idle_seconds or 0) <= 0 or token is None:
            return
        with _hb_lock:
            _hb_jobs[job_id] = token
            if _hb_thread["t"] is not None:
                return
            t = threading.Thread(
                target=_heartbeat_loop, daemon=True,
                name="pcbridge-unlock-heartbeat",
            )
            _hb_thread["t"] = t
        t.start()

    def _guard(
        tool: str,
        write: bool = True,
        force: bool = False,
        needs_input: bool = True,
        input_capability: str = "input.pointer",
        input_scope: str = "os.pointer",
    ) -> ToolResult | None:
        """Reddedildiyse typed MCP hata sonucu, izinliyse None.

        `needs_input=False`: sanal klavye/fare cihazi ARANMAZ. Ekran goruntusu
        ve erisilebilirlik araclari uinput kullanmiyor; /dev/uinput yokken
        onlari "girdi cihazi yok" diye reddetmek yanlis gerekce olurdu.
        """
        runtime.close_capture_if_locked()
        decision = gate.check(tool, write=write, force=force)
        if not decision.allowed:
            gate.audit(f"{tool}_denied", reason=decision.reason[:120])
            error = presentationlib.decision_error(decision)
            return presentationlib.desktop_error_result(
                error,
                text=f"⛔ {decision.reason}",
                permission_scope="pcbridge.desktop",
            )
        # `check()` kirayi kaydirdi; gosterge de onunla birlikte kaysin.
        runtime.refresh_capture_deadline()
        if needs_input:
            ok, why = backend.available()
            if not ok:
                gate.audit(f"{tool}_unavailable", reason=why[:120])
                return _unavailable_result(
                    input_capability,
                    text=f"⛔ The virtual input device is unavailable: {why}",
                    message=why,
                    scope=input_scope,
                    backend_name="desktop.input",
                )
        return None

    @contextmanager
    def _sequence(tool: str):
        """One desktop write, alone and re-checked (Task 5.1).

        Takes the cross-process execution lock after `_guard` admitted the
        call, re-checks the grant, then counts this action against the shared
        rate window. A refusal is raised before anything is sent.
        """
        with runtime.write_sequence(tool) as guard:
            guard(tool)
            yield guard

    def _sequence_refused(
        tool: str, exc: DesktopError, extra: dict[str, Any] | None = None
    ) -> ToolResult:
        busy = exc.code == ErrorCode.BUSY
        gate.audit(f"{tool}_{'busy' if busy else 'denied'}", error=exc.code.value)
        return presentationlib.desktop_error_result(
            exc,
            text=f"⛔ {exc}",
            permission_scope="pcbridge.desktop",
            extra=extra,
        )

    def _begin_write(tool: str) -> ExitStack | ToolResult:
        """Start one serialized desktop write, or return why it cannot start.

        The caller closes the returned stack in `finally`. A stack rather than a
        `with` block keeps each tool body as it was, and the refusal is handled
        here, before a tool's own `DesktopError` clause could report it as a
        provider failure.
        """
        stack = ExitStack()
        try:
            stack.enter_context(_sequence(tool))
        except executionlib.SequenceRefused as exc:
            stack.close()
            return _sequence_refused(tool, exc)
        except OSError as exc:
            stack.close()
            gate.audit(f"{tool}_error", error=str(exc)[:160])
            return _exception_result(
                exc,
                text=f"⛔ Could not take the desktop execution lock: {exc}",
                category=ErrorCategory.EXECUTION,
                scope="pcbridge.desktop",
                backend_name="desktop.execution",
            )
        return stack

    @mcp.tool(
        output_schema=None,
        annotations={"title": "Desktop capabilities"},
    )
    def system_capabilities() -> ToolResult:
        """Report desktop backend capabilities and authorization independently.
        This probe is read-only and does not require desktop_unlock. Call it before
        choosing a desktop action or when a desktop tool reports an error."""
        return presentationlib.capabilities_result(runtime.capabilities())

    @mcp.tool(
        output_schema=None,
        annotations={"title": "Allow desktop control for a while"}
    )
    def desktop_unlock(
        minutes: Annotated[
            int,
            Field(
                ge=1,
                le=120,
                description="How long the permission stays open, in minutes.",
            ),
        ] = 15,
        reason: Annotated[
            str | None,
            Field(description="Short note about what this is for; goes to the audit log."),
        ] = None,
    ) -> str | ToolResult:
        """Open pcbridge's time-limited authorization grant for desktop tools.
        Operating-system permissions are separate and system_capabilities reports
        them independently. Call this before using a desktop tool when the user
        has authorized screen reading or control; the grant expires on its own."""
        if not cfg.desktop.enabled:
            text = (
                "⛔ Desktop control is disabled. Set `[desktop] enabled = true` in "
                f"{cfg.source_path} and restart pcbridge (`pcbridge update`). "
                "Keyboard and pointer control also need access to /dev/uinput "
                "(`pcbridge doctor` prints the command); reading the screen does not."
            )
            error = DesktopError(
                code=ErrorCode.DESKTOP_DISABLED,
                message="Desktop control is disabled.",
                category=ErrorCategory.SAFETY,
                retryable=False,
                suggested_action="Enable desktop control in config.toml and restart pcbridge.",
                permission_scope="pcbridge.desktop",
            )
            return presentationlib.desktop_error_result(error, text=text)
        lock_decision = safetylib.screen_lock_decision(
            runtime.desktop_state_provider.screen_lock()
        )
        if not lock_decision.allowed:
            gate.audit(
                "desktop_unlock_denied",
                reason=lock_decision.reason[:120],
            )
            return presentationlib.desktop_error_result(
                presentationlib.decision_error(lock_decision),
                text=f"⛔ {lock_decision.reason}",
                permission_scope="pcbridge.desktop",
            )
        msg = gate.unlock(minutes, reason or "")
        # Izin acildiginin KULLANICIYA gorunmesi onemli, ama tek yolu bu
        # bildirim degil: `gnome-extension/` altindaki kabuk eklentisi ayni
        # durumu ekran kenarlarindaki cerceveyle gosteriyor ve o surekli
        # duruyor -- bildirim birkac saniye sonra kayboluyor. Eklenti
        # kuruluysa bildirim gereksiz tekrar oluyor, o yuzden kapatilabilir.
        # Ikisini birden kapatmak izni GORUNMEZ yapar; bilerek yapilmali.
        if cfg.desktop.unlock_notification:
            subprocess.run(
                [
                    "notify-send",
                    "-a",
                    "pcbridge",
                    "-u",
                    "critical",
                    "Desktop control granted",
                    f"{minutes} min · {reason or 'no reason given'}",
                ],
                capture_output=True,
                timeout=10,
            )
        out = [msg, "", capture_provider.describe_monitors(), ""]
        out.append(_open_screencast())
        out.append(
            "Coordinates are in the **global canvas space**; top left is (0, 0). For "
            "coordinates relative to one monitor, pass `monitor` as well."
        )
        snapshot = runtime.capabilities(refresh=True)
        limitations = {
            name: value.as_dict()
            for name, value in sorted(snapshot.capabilities.items())
            if not value.usable_now or value.limitations
        }
        if limitations:
            out += ["", "**Unavailable or limited capabilities**"]
            for name, value in limitations.items():
                detail = value["reason_code"] or value["state"]
                out.append(f"- `{name}`: {detail}")
        out.append("To close it early: desktop_lock")
        token = getattr(gate, "current_token", lambda: None)()
        grant = {
            "grant_id": getattr(token, "grant_id", ""),
            "revoke_epoch": max(
                0,
                int(getattr(token, "revoke_epoch", snapshot.authorization.revoke_epoch)),
            ),
            "until": float(getattr(gate, "unlocked_until", lambda: 0.0)()),
            "hard_until": float(getattr(gate, "hard_until", lambda: 0.0)()),
        }
        return ToolResult(
            content=[TextContent(type="text", text="\n".join(x for x in out if x))],
            structured_content={
                "type": "pcbridge.desktop.grant",
                "grant": grant,
                "authorization": snapshot.authorization.as_dict(),
                "capability_limitations": limitations,
            },
        )

    @mcp.tool(output_schema=None, annotations={"title": "Stop desktop control"})
    def desktop_lock() -> str:
        """Close the desktop control permission immediately instead of waiting for
        it to expire, and destroy the virtual keyboard/mouse devices. Any key or
        mouse button still held down is released first. Use when the user says they
        are done, or asks you to stop touching their screen."""
        # Revoke is the linearization point: every other Python process and
        # native watchdog observes the new epoch before cleanup begins.
        message = gate.lock()
        freed = backend.release_all()
        yayin = capture_provider.is_open()
        runtime.release_resources()
        # BASKA sureclerin yayinlari da: aynı anda bir `--stdio` istemcisi ya
        # da `pcb-shot` kendi yayinini acmis olabilir ve `close()` yalnizca
        # BIZIM tutamagimizi kapatir. Kullanici "kapat" dediginde ust
        # cubuktaki gostergenin gercekten kaybolmasi gerekiyor.
        others = capture_provider.kill_helpers()
        note = f"\n· released: {', '.join(freed)}" if freed else ""
        if yayin or others:
            note += "\n· screen sharing stopped (the sharing indicator is gone)"
        if others:
            note += f" · {others} helper process(es) stopped"
        return message + note

    @mcp.tool(
        output_schema=None,
        annotations={"title": "Move or click the mouse"},
    )
    def mouse(
        action: Annotated[
            str,
            Field(
                description="One of: move, move_by, click, double_click, "
                "triple_click, right_click, middle_click, drag, scroll, hold, "
                "release. hold presses a button down and leaves it down (for "
                "free-form drag: hold, then move, then release); triple_click "
                "selects a whole line in most text widgets. move_by is the odd "
                "one out: it nudges the pointer BY a delta instead of moving it "
                "TO a point, and it is not a way to reach anything on screen."
            ),
        ],
        x: Annotated[
            int | None,
            Field(
                description="Target X. A global desktop coordinate, unless you "
                "pass shot (then it is the pixel you see in that screenshot) or "
                "monitor (then it is a full-resolution coordinate inside it). "
                "Leave x and y both out of a click or scroll to act where the "
                "pointer already is."
            ),
        ] = None,
        y: Annotated[int | None, Field(description="Target Y, in the same space as x.")] = None,
        to_x: Annotated[
            int | None, Field(description="For drag: X where the drag ends.")
        ] = None,
        to_y: Annotated[
            int | None, Field(description="For drag: Y where the drag ends.")
        ] = None,
        dx: Annotated[
            int,
            Field(
                ge=-4000,
                le=4000,
                description="For move_by: how far to nudge horizontally, in "
                "device units, positive to the right. Not screen pixels and not "
                "a coordinate: on the desktop a unit moves the cursor by a fixed "
                "factor set by the user's mouse-speed setting, and inside an "
                "application that locks the pointer the application applies its "
                "own scale (a game's sensitivity). Either way the effect is "
                "linear, so calibrate instead of guessing: send a known delta, "
                "compare screenshots before and after, and divide.",
            ),
        ] = 0,
        dy: Annotated[
            int,
            Field(
                ge=-4000,
                le=4000,
                description="For move_by: how far to nudge vertically, positive "
                "downward. Same units as dx.",
            ),
        ] = 0,
        scroll_amount: Annotated[
            int,
            Field(
                ge=-50,
                le=50,
                description="For scroll: wheel clicks. Positive scrolls up, negative down.",
            ),
        ] = 3,
        horizontal: Annotated[
            bool,
            Field(
                description="For scroll: use the horizontal wheel instead of the "
                "vertical one. Positive scroll_amount goes right, negative left."
            ),
        ] = False,
        button: Annotated[
            str,
            Field(
                description="For hold/release: which button. One of left, right, "
                "middle. Ignored by the click actions, which imply their own button."
            ),
        ] = "left",
        hold_ms: Annotated[
            int | None,
            Field(
                ge=0,
                le=1000,
                description="For the click actions: how long each press lasts, in "
                "milliseconds. Leave it empty for the configured default (60 ms), "
                "which is long enough for applications that poll input on a fixed "
                "tick, such as games. At most 150 for double_click and "
                "triple_click, so the presses still count as one gesture; for a "
                "longer press use hold, then release.",
            ),
        ] = None,
        smooth: Annotated[
            bool | None,
            Field(
                description="Override how the pointer travels for this call. The "
                "server default glides through intermediate points so the motion "
                "looks natural; pass false to jump straight to the target, which is "
                "faster but skips hover and drag-start events some applications need."
            ),
        ] = None,
        shot: Annotated[
            str | None,
            Field(
                description="The id of the screenshot you read the coordinates off, "
                "as printed next to that image by screen_capture (for example "
                "'m2-a1b2c3'). Pass it and give x/y exactly as you see them in that "
                "picture: the server knows where the image sits on the desktop and "
                "how much it was scaled down, and does the conversion itself. Do not "
                "combine it with monitor."
            ),
        ] = None,
        monitor: Annotated[
            int | None,
            Field(
                description="Treat x/y as full-resolution coordinates inside this "
                "monitor instead of the whole desktop. Monitors are numbered left to "
                "right starting at 1. Use shot instead when the coordinates come off "
                "a screenshot, because a screenshot may be scaled down."
            ),
        ] = None,
        force: Annotated[
            bool,
            Field(
                description="Send even if the user was recently active at the machine. "
                "Only set this when the user explicitly asked you to take over."
            ),
        ] = False,
    ) -> str | ToolResult:
        """Move the mouse pointer, click, drag, scroll or hold a button down on the
        user's Linux desktop. Requires desktop_unlock first. Use when the user asks
        you to press a button, open a menu or otherwise operate a graphical
        application. When you took the coordinates off a screenshot, pass that
        image's shot id and give x/y as you see them — the server converts them for
        you, so you never do the offset and scale arithmetic yourself. Without a
        shot, coordinates are global desktop pixels. The pointer glides to its
        target rather than teleporting, so a move takes a moment. For a drag that
        needs stops along the way — a slider, a selection rectangle, a file onto a
        folder — use hold, then move, then release; the drag action is the
        single-shot version. Take a screenshot or check the result after acting —
        never click blind.

        move and move_by are different jobs. move goes TO a point: it is exact,
        it is what you verify against a screenshot, and it is the only way to
        put the pointer on something you want to click. move_by nudges BY a
        delta and is not a way to reach anything: it exists for applications
        that lock the pointer and read relative motion — games, 3D and CAD
        viewports, WebGL canvases — which never see an absolute "go to this
        point" at all. After a move_by the pointer's position is unknown, so
        read the screen again before you click, or go back to a known point
        with an absolute move.

        move_by units are not pixels or degrees. The scale is fixed and linear
        (pcbridge adds no acceleration, and applications that lock the pointer
        read unaccelerated motion), but it belongs to the setting or the
        application that reads it, and it is not known in advance: calibrate
        it once — send a known delta such as 400, measure on screenshots how
        far the cursor or view turned, divide, and reuse that ratio. An
        application that has just captured the pointer (entering a game world,
        closing a menu) may drop the start of the first nudge, so send a small
        throwaway nudge first and never calibrate on that one.

        A click or scroll without x and y happens where the pointer already
        is. That is how you click inside an application that has locked the
        pointer — aim with move_by, then click with no coordinates — and it
        also works after a move you already verified."""
        err = _guard("mouse", write=True, force=force)
        if err:
            return err

        act = (action or "").strip().lower()
        needs_xy = ("move", "drag")
        clicks = {"click": 1, "double_click": 2, "triple_click": 3,
                  "right_click": 1, "middle_click": 1}
        # Koordinatsiz tiklama/kaydirma imlecin BULUNDUGU yerde (Adim 8.2).
        # Yalnizca birinin verilmesi ya da koordinatsiz `shot`/`monitor`
        # buyuk olasilikla unutulmus bir koordinattir: sessizce yerinde
        # tiklamak yerine reddedilir (batch'teki `_pair` ile ayni kural).
        if act in clicks or act == "scroll":
            if (x is None) != (y is None):
                return ("x and y must be given together. Give neither to act "
                        "where the pointer already is.")
            if x is None and (shot or monitor is not None):
                return ("shot/monitor given without x/y. Add the coordinate; to "
                        "click where the pointer already is, leave out "
                        "shot/monitor.")
        if act in clicks and hold_ms is not None and clicks[act] > 1 and hold_ms > 150:
            return (f"hold_ms for {act} can be at most 150 ({hold_ms} "
                    "given): both presses must stay inside the double-click threshold.")
        in_place = act in clicks and x is None
        write = _begin_write("mouse")
        if isinstance(write, ToolResult):
            return write
        try:
            if act in needs_xy or (act in clicks and not in_place):
                if x is None or y is None:
                    return "x and y are required (and to_x/to_y for drag)."
                gx, gy = _to_global(x, y, monitor, shot)
            if act == "move":
                pos = backend.move(gx, gy, smooth=smooth)
                done = f"pointer moved to {pos}"
            elif act == "move_by":
                if not dx and not dy:
                    return "move_by needs dx or dy."
                sx, sy = backend.move_by(dx, dy)
                done = (
                    f"pointer nudged by ({sx:+d}, {sy:+d}) · its position is "
                    "now UNKNOWN — take a ui_dump or screen_capture before "
                    "clicking, or use an absolute `move` to a known "
                    "point"
                )
            elif act in clicks:
                btn = {"right_click": "right", "middle_click": "middle"}.get(act, "left")
                press = {} if hold_ms is None else {"hold_ms": hold_ms}
                if in_place:
                    backend.click(btn, clicks[act], **press)
                    target = "where the pointer is"
                else:
                    pos = backend.move(gx, gy, smooth=smooth)
                    time.sleep(0.08)
                    backend.click(btn, clicks[act], **press)
                    target = f"at {pos}"
                kind = {2: " (double)", 3: " (triple)"}.get(clicks[act], "")
                held_for = f" · held {hold_ms} ms" if hold_ms is not None else ""
                done = f"{btn} click{kind} {target}{held_for}"
            elif act == "drag":
                if to_x is None or to_y is None:
                    return "drag needs to_x and to_y."
                ex, ey = _to_global(to_x, to_y, monitor, shot)
                backend.drag(gx, gy, ex, ey, button=button)
                done = f"dragged ({gx}, {gy}) -> ({ex}, {ey}) with {button}"
            elif act == "scroll":
                if x is not None and y is not None:
                    backend.move(*_to_global(x, y, monitor, shot), smooth=smooth)
                    time.sleep(0.08)
                backend.scroll(scroll_amount, horizontal=horizontal)
                yon = "horizontally" if horizontal else "vertically"
                done = f"scrolled {scroll_amount} step(s) {yon}"
            elif act == "hold":
                backend.mouse_down(button)
                done = (
                    f"{button} button is HELD DOWN — move the pointer, then "
                    f"let go with `release`"
                )
            elif act == "release":
                backend.mouse_up(button)
                done = f"{button} button released"
            else:
                return (
                    f"Unknown action: '{action}'. Valid: move, move_by, "
                    "click, double_click, triple_click, right_click, "
                    "middle_click, drag, scroll, hold, release"
                )
        except (
            inputlib.InputError,
            monitorslib.MonitorError,
            capturelib.CaptureError,
            DesktopError,
        ) as exc:
            gate.audit("mouse_error", action=act, error=str(exc)[:160])
            return _exception_result(
                exc,
                text=f"Error: {exc}",
                category=ErrorCategory.EXECUTION,
                scope="os.pointer",
                backend_name="desktop.input",
            )
        finally:
            write.close()

        gate.audit(
            "mouse", action=act, x=x, y=y, monitor=monitor, shot=shot,
            dx=dx if act == "move_by" else None,
            dy=dy if act == "move_by" else None,
            button=button if act in ("hold", "release", "drag") else None,
            in_place=in_place or None,
            hold_ms=hold_ms if act in clicks else None,
            forced=force or None,
        )
        where = backend.position
        # Konum bilinmiyorsa bunu SOYLE. Eskiden not bos kalirdi ve ajan
        # "monitor bilgisi yok" ile "konum bilinmiyor"u ayirt edemezdi;
        # `move_by`den sonra bu ayrim tam olarak onemli olan sey.
        note = "" if where else " · pointer position UNKNOWN"
        if where:
            m = capture_provider.find_monitor(*where)
            if m:
                note = f" · monitor {m.index} ({m.connector})"
        return (
            f"{done}{note}.\nDo not move on before you have checked the result."
            + _stale_note(shot)
            + _held_note()
        )

    @mcp.tool(
        output_schema=None,
        annotations={"title": "Type text or press keys"},
    )
    def keyboard(
        action: Annotated[
            str,
            Field(
                description="One of: type, key, hold, release. key presses a "
                "combination and lets go; hold presses and leaves it down until "
                "you call release."
            ),
        ],
        text: Annotated[
            str | None, Field(description="For type: the text to enter.")
        ] = None,
        keys: Annotated[
            str | None,
            Field(
                description="For key/hold/release: a combination like 'ctrl+v', "
                "'super', 'alt+tab', 'Return', 'Escape', 'f5', 'down'. Any number "
                "of keys may be combined with '+' and they all go down together — "
                "the virtual keyboard has none of the ghosting limits of real "
                "hardware."
            ),
        ] = None,
        raw: Annotated[
            bool,
            Field(
                description="For type: send raw key codes instead of pasting via the "
                "clipboard. The clipboard path is the default because it is immune to "
                "the machine's keyboard layout; raw only handles ASCII and will produce "
                "wrong punctuation on a non-US layout. Leave this false unless the "
                "target application ignores paste."
            ),
        ] = False,
        confirm_close: Annotated[
            bool,
            Field(
                description="Required to send a shortcut that closes a window or "
                "quits an application (alt+F4, ctrl+q, ctrl+w). Without it the "
                "call is refused, because unsaved work would be gone without "
                "anyone being asked. Set it only when closing is the actual "
                "intent; it is not a retry flag."
            ),
        ] = False,
        force: Annotated[
            bool,
            Field(description="Send even if the user was recently active at the machine."),
        ] = False,
    ) -> str | ToolResult:
        """Type text or press key combinations on the user's Linux desktop.
        Requires desktop_unlock first. Use when the user asks you to fill in a
        field, confirm a dialog with Enter, or trigger a shortcut. Text is entered
        through the clipboard, so accented and non-English characters come out
        correctly; the previous clipboard contents are restored afterwards.

        hold keeps keys down across later calls, which is how you build gestures
        the shortcut syntax cannot express — hold shift, click twice to extend a
        selection, release. Always release what you hold: a key left down makes
        the machine unusable for the user. As a backstop the server releases
        everything by itself after a timeout and says so in the next reply, but
        that is damage control, not a substitute for releasing.

        Shortcuts that close a window or quit an application are refused unless
        you also pass confirm_close, so unsaved work is never discarded on a
        guess. If you get that refusal, decide whether closing is really what
        the user asked for before repeating the call."""
        err = _guard(
            "keyboard",
            write=True,
            force=force,
            input_capability="input.keyboard",
            input_scope="os.keyboard",
        )
        if err:
            return err

        act = (action or "").strip().lower()
        write = _begin_write("keyboard")
        if isinstance(write, ToolResult):
            return write
        try:
            if act == "type":
                if not text:
                    return "type needs `text`."
                note = backend.type_text(
                    text, raw=raw, restore_clipboard=cfg.desktop.restore_clipboard
                )
                done = note
            elif act in ("key", "hold", "release"):
                if not keys:
                    return f"{act} needs `keys` (for example 'ctrl+v')."
                if act in ("key", "hold"):
                    # Icerik kapisi: `force` bunu ACMAZ, ayri bir niyet beyani
                    # ister (docs/dev/desktop-rules.md §4 item 5).
                    policy.check_key_combo(keys, confirm_close=confirm_close)
                if act == "key":
                    backend.key(keys)
                elif act == "hold":
                    backend.key_down(keys)
                else:
                    backend.key_up(keys)
                done = f"`{keys}` {'pressed' if act == 'key' else act}"
            else:
                return f"Unknown action: '{action}'. Valid: type, key, hold, release"
        except (inputlib.InputError, DesktopError) as exc:
            gate.audit("keyboard_error", action=act, error=str(exc)[:160])
            return _exception_result(
                exc,
                text=f"Error: {exc}",
                category=ErrorCategory.EXECUTION,
                scope="os.keyboard",
                backend_name="desktop.input",
            )
        finally:
            write.close()

        gate.audit(
            "keyboard",
            action=act,
            keys=keys,
            confirmed_close=confirm_close or None,
            chars=len(text) if text else None,
            raw=raw or None,
            forced=force or None,
        )
        return (
            f"{done}.\nDo not move on before you have checked the result."
            + _held_note()
        )

    # ------------------------------------------------------- ekran goruntusu
    @mcp.tool(
        output_schema=None,
        annotations={"title": "Describe the screens"},
    )
    def screen_info() -> str:
        """Describe the monitors: how many there are, their resolution, where each
        one sits in the shared coordinate space, and which one is primary. Use this
        before clicking or capturing anything, so you know which coordinates land on
        which screen. Contains no personal data, only the hardware layout."""
        lines = [capture_provider.describe_monitors(), ""]
        runtime.close_capture_if_locked()
        cap_ok, cap_why = capture_provider.available()
        lines.append(
            f"**Screenshots:** {'ready' if cap_ok else 'UNAVAILABLE'} "
            f"(`{capture_provider.backend_name()}`)"
            + ("" if cap_ok else f" — {cap_why}")
            + ("" if capture_provider.is_open() else
               " · sharing is off, captures flash the screen (`desktop_unlock` turns it on)")
        )
        in_ok, in_why = backend.available()
        lines.append(
            f"**Keyboard/pointer:** {'ready' if in_ok else 'UNAVAILABLE'}"
            + ("" if in_ok else f" — {in_why}")
        )
        ui_ok, ui_why = tree.available()
        ui_line = f"**Accessibility tree:** {'ready' if ui_ok else 'UNAVAILABLE'}"
        if ui_ok:
            # Pencere listesi ve odak yalnizca buradan okunabiliyor: C
            # bolumunde olculdu, Shell.Introspect "Access denied" veriyor.
            # `windows()` agaci gezmedigi icin `dump`tan ucuz (olculdu: 42 ms).
            try:
                wins = tree.windows()
                focused = next((w for w in wins if w.active), None)
                ui_line += f" · {len(wins)} window(s)"
                if focused:
                    ui_line += f", focused: {focused.label}"
            except (uitreelib.UiTreeError, DesktopError) as exc:
                ui_line += f" · the window list could not be read ({exc})"
        else:
            ui_line += f" — {ui_why}"
        lines.append(ui_line)
        lines.append(f"**Grant:** {gate.status_line()}")
        lines.append("")
        lines.append(
            "Coordinates are in the **global canvas space**: top left is (0, 0). If you "
            "give coordinates relative to one monitor, pass `monitor` as well and "
            "pcbridge adds its offset."
        )
        return "\n".join(lines)

    def _capture_target(
        tool: str, monitor: str, region: list[int] | None, shot: str | None
    ) -> tuple[int | str, Any] | str | ToolResult:
        """Monitor secimi + istege bagli bolge -> (spec, area), ya da hata.

        `screen_capture`, `find_text` ve `wait_for_text` ayni sozdizimini
        paylasiyor (Adim 8.5/8.6): bolgenin uzayi `shot` > monitor numarasi/adi
        > global. Hata metni (str) bir ret, ToolResult typed bir hata.
        """
        spec: int | str = monitor.strip() if isinstance(monitor, str) else monitor
        if isinstance(spec, str) and spec.isdigit():
            spec = int(spec)
        if shot and not region:
            return (
                "⛔ `shot` only means something together with `region`: if you read the "
                "region off that screenshot, pass `region=[x, y, width, height]` too."
            )
        if not region:
            return spec, None
        # Bolge: `mouse` koordinatlarinin uc uzayi. Monitor secimi bir SAYI
        # ya da ad ise bolge onun icinde; "all" ise global.
        whole = isinstance(spec, str) and spec.lower() in ("all", "hepsi")
        try:
            area = capture_provider.resolve_region(
                *region,
                monitor=None if whole else spec,
                shot=shot,
                dirs=shot_dirs,
            )
        except DesktopError as exc:
            gate.audit(f"{tool}_error", error=str(exc)[:160])
            return _exception_result(
                exc,
                text=f"Error: {exc}",
                category=ErrorCategory.COORDINATE,
                scope="os.capture",
                backend_name=capture_provider.backend_name(),
            )
        return spec, area

    @mcp.tool(
        output_schema=None,
        annotations={"title": "Take a screenshot"},
    )
    def screen_capture(
        monitor: Annotated[
            str,
            Field(
                description=(
                    "Which screen to capture: 'all' (default, one image per "
                    "monitor), a monitor number like '1' or '2', a connector name "
                    "like 'DP-1', 'primary', or 'window' for just the focused "
                    "window. 'window' images cannot be turned back into "
                    "coordinates."
                )
            ),
        ] = "all",
        scale: Annotated[
            int | None,
            Field(
                description=(
                    "Longest edge of the returned image in pixels, after cropping. "
                    "0 means full resolution. Leave empty for the configured "
                    "default."
                )
            ),
        ] = None,
        include_pointer: Annotated[
            bool | None,
            Field(description="Draw the mouse pointer into the image."),
        ] = None,
        region: Annotated[
            list[int] | None,
            Field(
                min_length=4,
                max_length=4,
                description=(
                    "Capture only this part of one monitor: [x, y, width, "
                    "height]. Same spaces as mouse coordinates: with shot, "
                    "pixels in that earlier screenshot (the usual way to zoom "
                    "into something you saw); with a monitor number, full-"
                    "resolution pixels inside that monitor; otherwise global "
                    "desktop pixels. It must stay inside one monitor. The new "
                    "image gets its own shot id, and clicks on it land where "
                    "they should. A small region costs far fewer tokens and "
                    "comes back at full resolution."
                ),
            ),
        ] = None,
        shot: Annotated[
            str | None,
            Field(
                description="With region: the id of the screenshot the region "
                "was read off, as printed next to it (for example 'm2-a1b2c3')."
            ),
        ] = None,
        enhance: Annotated[
            bool,
            Field(
                description="Brighten and stretch the contrast of the image you "
                "receive, for dark scenes (a game at night, a dark theme). The "
                "picture keeps its size, so coordinates and the shot id work "
                "unchanged; the saved file stays untouched."
            ),
        ] = False,
    ) -> list[ContentBlock] | ToolResult:
        """Take a screenshot of the user's screen. Use when the user asks what is on
        their screen, and before clicking somewhere, to check what is actually
        there. If your client can display images you get the picture itself and can
        look at it; otherwise you get a short-lived link the user can open on their
        phone. Every image comes with a short id, and to act on something you see
        you pass that id as `shot` to `mouse` or `computer_batch` together with the
        pixel coordinates exactly as they appear in the picture — the server knows
        where the image sits and how far it was scaled down, so you never convert
        anything yourself. For GTK applications prefer `ui_dump` — it is cheaper and
        cannot miss, because it does not use coordinates at all. Capture only the
        monitor you need, and use region to look closer at part of it: both cost
        a fraction of a two-monitor capture."""
        # write=False: ekran goruntusu bir YAZMA eylemi degil, o yuzden "yakinda
        # klavye kullanildi" korumasina takilmiyor -- makinenin basinda olmaniz
        # ekraniniza bakmanizi engellememeli. Izin penceresi ve ekran kilidi
        # kontrolu ise aynen gecerli: goruntu en gizlilik-hassas cikti.
        denied = _guard("screen_capture", write=False, needs_input=False)
        if denied:
            return denied
        if shot_store is None:
            text = "⛔ The screenshot service is not set up (an older server?)."
            error = DesktopError(
                code=ErrorCode.BACKEND_UNAVAILABLE,
                message="The screenshot service is not set up.",
                category=ErrorCategory.CAPTURE,
                retryable=False,
                suggested_action="Upgrade or repair the pcbridge server installation.",
                permission_scope="os.capture",
                backend="pcbridge.shots",
            )
            return presentationlib.desktop_error_result(error, text=text)

        cap_ok, cap_why = capture_provider.available()
        if not cap_ok:
            gate.audit("screen_capture_unavailable", reason=cap_why[:120])
            return _unavailable_result(
                "capture.monitor",
                text=f"⛔ Cannot take a screenshot: {cap_why}",
                message=cap_why,
                scope="os.capture",
                backend_name=capture_provider.backend_name(),
            )

        target = _capture_target("screen_capture", monitor, region, shot)
        if isinstance(target, str):
            return _text(target)
        if isinstance(target, ToolResult):
            return target
        spec, area = target
        long_edge = (
            cfg.desktop.screenshot_scale_long_edge if scale is None else max(0, scale)
        )
        pointer = (
            cfg.desktop.include_pointer if include_pointer is None else include_pointer
        )

        # Cekimden ONCE supur, TASIMADAN BAGIMSIZ. Eskiden temizligin tek
        # tetikleyicisi `shot_store.publish()`ti, o da yalnizca HTTP'de
        # cagriliyor: stdio ile baglanildiginda `shots/` hic temizlenmiyor,
        # `shot_keep_hours` yaziyor ama uygulanmiyordu.
        swept = shot_store.sweep()

        try:
            shots = capture_provider.capture(
                spec,
                out_dir=shot_store.dir,
                scale_long_edge=long_edge,
                include_pointer=pointer,
                # `shot=` iki dizinde de ariyor; yeni kimlik ikisinde de bos
                # olmali, yoksa arama baska bir cekimin kaydini bulur.
                reserved_dirs=shot_dirs,
                region=area,
            )
        except (capturelib.CaptureError, monitorslib.MonitorError, DesktopError) as exc:
            gate.audit("screen_capture_error", error=str(exc)[:160])
            return _exception_result(
                exc,
                text=f"Error: {exc}",
                category=ErrorCategory.CAPTURE,
                scope="os.capture",
                backend_name=capture_provider.backend_name(),
            )

        # stdio'da HTTP sunucusu YOK -> /shot/<token>.png rotasi da yok. Orada
        # baglanti uretmek sessizce olu bir URL vermek olurdu; onun yerine
        # diskteki yol soyleniyor (istemci dosyayi kendi okuyabilir).
        links = sessionctx.transport(transport) != "stdio"
        ttl_min = max(1, cfg.desktop.shot_ttl_seconds // 60)
        out: list[str] = []
        for item in shots:
            if links:
                # Token denetim kaydina YAZILMAZ: audit.log'u okuyabilen birinin
                # goruntuyu de acabilmesi anlamsiz bir yetki genislemesi olurdu.
                _token, where = shot_store.publish(item.path)
            else:
                where = str(item.path)
            if item.offset is None:
                out.append(
                    f"**{item.label}** · {item.scaled[0]}x{item.scaled[1]}\n"
                    f"  {where}\n"
                    "  ⚠️ This is the focused window; where it sits on the screen is "
                    "unknown, so do not derive coordinates from it."
                )
            else:
                out.append(
                    f"**{item.label}** · {item.size[0]}x{item.size[1]} "
                    f"@ ({item.offset[0]}, {item.offset[1]}) → "
                    f"{item.scaled[0]}x{item.scaled[1]} (scale {item.scale:.3f})\n"
                    f"  shot: `{item.id}`\n"
                    f"  {where}"
                )

        gate.audit("screen_capture", monitor=str(monitor), shots=len(shots),
                   swept=swept or None, inline=_inline_images() or None,
                   region=list(area[0]) if area else None,
                   enhance=enhance or None,
                   backend=capture_provider.backend_name())

        degraded = getattr(capture_provider, "degraded_reason", "")
        if degraded:
            # GORUNUR geri donus (Task 4.3): `auto` native yardimciyi
            # bulamadi ve kare Python yoluyla alindi. Sessiz kalsaydi "neden
            # yavas" ya da "neden farkli" sorusunun cevabi hicbir yerde olmazdi.
            out.append(
                f"⚠️ Native capture was unavailable ({degraded}); the frame came "
                "through the Python path."
            )

        out.append("")
        if links:
            out.append(f"Links are valid for {ttl_min} min, then they expire.")
        else:
            out.append(
                "The paths point at files on disk (a local session has no HTTP "
                "server, so no link can be made)."
            )
        if not _inline_images():
            # SESSIZ BOSLUK YOK: goruntu blogu gelmiyorsa sebebi soylensin,
            # yoksa istemci "goruntu geldi ama ben goremedim" sanir.
            out.append(
                "Image blocks are OFF (`inline_images`); only the path/link above "
                "is returned."
            )
        example = next((s for s in shots if s.offset is not None), None)
        if example is not None:
            # ARITMETIK YOK. Ofset ve olcegi sunucu uyguluyor; modelin tek isi
            # gordugu pikseli ve o goruntunun kimligini yazmak. Once boyle
            # degildi ve zayif modeller bolmeyi tutturamayip hedefin kenarina
            # tikliyordu.
            out.append(
                "To click a point in this picture, give the coordinate **exactly as "
                "you see it** and add the picture's id: "
                "`mouse(action=\"click\", x=…, y=…, shot=\"" + example.id + "\")` "
                "or in a batch `{\"a\":\"click\",\"x\":…,\"y\":…,"
                "\"shot\":\"" + example.id + "\"}`. pcbridge applies the offset and the "
                "scale itself — do not convert. (The offset/scale values above are "
                "for information only.)"
            )
            # Istemcinin kendi kuculttugu goruntuden koordinat cikarilamaz:
            # gordugunuz piksel ile kayitli olcek ayrisir ve `shot` hesabi
            # sessizce sasar. `scale=0` verildiginde tam da bu oluyor.
            for item in shots:
                note = capture_provider.oversize_note(item)
                if note:
                    out.append(note)
                    break
            out.extend(n for n in map(capturelib.legibility_note, shots) if n)
            if any(s.scale < 1.0 for s in shots):
                # Olculdu: tam cozunurlukte gidis-donus sapmasi 1 px, 1280'e
                # kucultulmusde ~5 px. Bu sapma DONUSUMDEN degil kucultmenin
                # kendisinden geliyor -- donusumu sunucunun yapmasi onu
                # ortadan kaldirmiyor, o yuzden uyari duruyor.
                out.append(
                    "The picture is scaled down, so your target may be off by a few "
                    "pixels (measured: ~5 px) — from the downscaling itself, not the "
                    "arithmetic. Fine for buttons and menus; for more precision "
                    "capture at full resolution with `scale=0`."
                )

        # Goruntu bloklari metinden ONCE hazirlaniyor ama metnin ARKASINA
        # diziliyor: teslim edilemeyen bir goruntu metinde yazmali.
        images: list[ContentBlock] = []
        delivered: list[Any] = []
        undelivered: list[DesktopError] = []
        if _inline_images():
            for item in shots:
                try:
                    images.append(presentationlib.shot_image(item, enhance=enhance))
                    delivered.append(item)
                except DesktopError as exc:
                    undelivered.append(exc)
            if enhance and delivered:
                out.append(
                    "🔆 The picture was sent brightened (only the copy sent to you; "
                    "the capture on disk is untouched). Same size, `shot` "
                    "coordinates unchanged."
                )
        if len(delivered) > 1:
            # Kimlik metinde, goruntu ayri blokta: eslesme SIRAYLA.
            out.append(
                "The pictures follow below in this order: "
                + ", ".join(f"`{item.id or item.label}`" for item in delivered)
                + "."
            )
        if undelivered:
            # Cekim diskte basarili ama istemci goruntuyu ALMADI. Bunu basari
            # gibi dondurmek, modelin gormedigi bir goruntuden koordinat
            # uydurmasina davetiye olurdu. Eskiden tam olarak oyle oluyordu:
            # okunamayan goruntu tek satirlik bir notla basarili sonucun
            # icinde kayboluyordu.
            gate.audit("screen_capture_undelivered", shots=len(undelivered))
            error = presentationlib.undelivered_error(undelivered)
            out.append(
                "⛔ The picture did NOT reach the client: "
                f"{error.message}. The capture was taken, but do not treat this call "
                "as a success: do not derive coordinates from it; take a new "
                "capture."
            )
            return presentationlib.desktop_error_result(
                error,
                content=[_text("\n".join(out)), *images],
                extra={"shots": [item.id for item in shots]},
            )

        # METIN BLOGU HER ZAMAN ILK SIRADA ve her zaman var. Monitor numarasi,
        # global ofset ve donusum kurali goruntuyle BIRLIKTE gitmeli; yoksa
        # istemci ikinci monitore 1920 piksel sasarak tiklar ve hata hicbir
        # yerde gorunmez.
        return [_text("\n".join(out)), *images]

    # ------------------------------------------------ ekrandan metin (OCR)
    # Adim 8.6. Erisilebilirlik agaci olmayan pencerelerde (oyun, bazi
    # Electron/Java) metnin yerini goruntuden okuyup koordinat olarak veriyor;
    # cevap duz metin, goruntu jetonu yok. Motor `ocr.py`de; burada yalnizca
    # kapi, cekim ve bicimleme.
    def _ocr_unavailable(tool: str) -> ToolResult | None:
        ok, why = ocrlib.available(cfg.desktop.ocr_languages)
        if ok:
            return None
        gate.audit(f"{tool}_unavailable", reason=why[:120])
        error = DesktopError(
            code=ErrorCode.DEPENDENCY_MISSING,
            message=why,
            category=ErrorCategory.CAPABILITY,
            retryable=False,
            suggested_action=(
                "Text recognition needs the tesseract OCR engine. Ask the user to "
                f"install it ({ocrlib.INSTALL_HINT}); until then use "
                "screen_capture and read the picture yourself."
            ),
            permission_scope="os.capture",
            backend=ocrlib.ENGINE,
        )
        return presentationlib.desktop_error_result(
            error, text=f"⛔ Cannot read text from the screen: {why}"
        )

    def _ocr_prepare(
        tool: str, monitor: str, region: list[int] | None, shot: str | None
    ) -> tuple[int | str, Any] | ToolResult:
        """Kapi + motor + hedef: ya (spec, area) ya da hazir hata sonucu."""
        denied = _guard(tool, write=False, needs_input=False)
        if denied:
            return denied
        missing = _ocr_unavailable(tool)
        if missing:
            return missing
        if shot_store is None:
            return presentationlib.desktop_error_result(
                DesktopError(
                    code=ErrorCode.BACKEND_UNAVAILABLE,
                    message="The screenshot service is not set up.",
                    category=ErrorCategory.CAPTURE,
                    retryable=False,
                    suggested_action="Upgrade or repair the pcbridge server installation.",
                    permission_scope="os.capture",
                    backend="pcbridge.shots",
                ),
                text="⛔ The screenshot service is not set up.",
            )
        if isinstance(monitor, str) and monitor.strip().lower() == "window":
            return _text_result(
                "⛔ Where a `window` capture sits on the screen is unknown, so the "
                "found text has no usable coordinate. Pick a monitor."
            )
        cap_ok, cap_why = capture_provider.available()
        if not cap_ok:
            return _unavailable_result(
                "capture.monitor",
                text=f"⛔ Cannot take a screenshot: {cap_why}",
                message=cap_why,
                scope="os.capture",
                backend_name=capture_provider.backend_name(),
            )
        target = _capture_target(tool, monitor, region, shot)
        if isinstance(target, str):
            return _text_result(target)
        return target

    def _text_result(text: str) -> ToolResult:
        return ToolResult(content=[_text(text)])

    def _ocr_read(spec: int | str, area: Any, query: str):
        """Tam cozunurlukte cek, oku. -> [(cekim, eslesmeler, kelimeler)]

        Tam cozunurluk: OCR kucultulmus yaziyi kaciriyor, ve bulunan kutu
        dogrudan `shot=` koordinati oluyor. Imlec cizilmiyor, yazinin ustune
        binmesin.
        """
        shot_store.sweep()
        shots = capture_provider.capture(
            spec,
            out_dir=shot_store.dir,
            scale_long_edge=0,
            include_pointer=False,
            reserved_dirs=shot_dirs,
            region=area,
        )
        read = []
        for item in shots:
            words = ocrlib.read_words(item.path, cfg.desktop.ocr_languages)
            read.append((item, ocrlib.find(words, query), words))
        return read

    def _discard(shots) -> None:
        """Kimligi hic gosterilmeyen ara cekimleri sil (bekleme dongusu).

        Kendi urettigimiz gecici dosyalar; kimse onlara bir `shot` ile
        ulasamaz, cunku kimlikleri hicbir cevapta yok.
        """
        for item in shots:
            for path in (item.path, shot_store.dir / f"{item.id}{capturelib.META_SUFFIX}"):
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass

    def _ocr_report(query: str, read, elapsed: float, *, heading: str) -> ToolResult:
        found = [
            (item, match) for item, matches, _words in read for match in matches
        ]
        found.sort(key=lambda pair: (not pair[1].exact, -pair[1].score))
        lines = [heading, ""]
        payload = []
        for number, (item, match) in enumerate(found[: ocrlib.MAX_MATCHES], 1):
            x, y = match.center
            approx = "" if match.exact else f" · approximate ({match.score * 100:.0f}%)"
            lines.append(
                f"{number}. \"{match.text}\" @ ({x}, {y}) · shot `{item.id}` "
                f"({item.label}) · confidence {match.conf:.0f}%{approx}"
            )
            payload.append({**match.as_dict(), "shot": item.id})
        if found:
            item, match = found[0]
            x, y = match.center
            lines += [
                "",
                "Coordinates are in that capture's pixels; to click, pass its id: "
                f"`mouse(action=\"click\", x={x}, y={y}, shot=\"{item.id}\")`.",
            ]
            if not match.exact:
                lines.append(
                    "⚠️ No exact match, only a similar one: look with "
                    "`screen_capture(region=…)` before clicking."
                )
        else:
            near = [
                (item, match)
                for item, _matches, words in read
                for match in ocrlib.nearest_lines(words, query)
            ]
            near.sort(key=lambda pair: -pair[1].score)
            total_words = sum(len(words) for _i, _m, words in read)
            lines.append(f"Words read: {total_words}.")
            if near:
                lines.append("Nearest lines:")
                for item, match in near[:5]:
                    x, y = match.center
                    lines.append(
                        f"  - \"{match.text}\" @ ({x}, {y}) · shot `{item.id}`"
                    )
            lines.append(
                "OCR can miss small or stylized text: zoom in with `region`, "
                "or look yourself with `screen_capture`."
            )
        return ToolResult(
            content=[_text("\n".join(lines))],
            structured_content={
                "type": "pcbridge.desktop.text",
                "found": bool(found),
                "matches": payload,
                "shots": [item.id for item, _m, _w in read],
                "elapsed_seconds": round(elapsed, 2),
                "engine": ocrlib.ENGINE,
            },
        )

    def _ocr_failure(tool: str, exc: Exception) -> ToolResult:
        gate.audit(f"{tool}_error", error=str(exc)[:160])
        if isinstance(exc, ocrlib.OcrError):
            error = DesktopError(
                code=ErrorCode.DEPENDENCY_MISSING if exc.missing else ErrorCode.BACKEND_UNAVAILABLE,
                message=str(exc),
                category=ErrorCategory.CAPABILITY,
                retryable=not exc.missing,
                suggested_action="Check the tesseract installation, or read a screenshot instead.",
                permission_scope="os.capture",
                backend=ocrlib.ENGINE,
            )
            return presentationlib.desktop_error_result(error, text=f"Error: {exc}")
        return _exception_result(
            exc,
            text=f"Error: {exc}",
            category=ErrorCategory.CAPTURE,
            scope="os.capture",
            backend_name=capture_provider.backend_name(),
        )

    @mcp.tool(
        output_schema=None,
        annotations={"title": "Find text on screen"},
    )
    def find_text(
        text: _OCR_TEXT,
        monitor: _OCR_MONITOR = "all",
        region: _OCR_REGION = None,
        shot: _OCR_SHOT = None,
    ) -> ToolResult:
        """Read the screen with OCR and return where a piece of text is, as plain
        text: no image comes back, so it costs a fraction of a screenshot. Use it
        in windows the accessibility tree cannot see — games, many Electron and
        Java applications — to find a button or label and click it: every match
        comes with a shot id, and its x/y go straight into mouse(action="click",
        x=…, y=…, shot=…). Prefer ui_dump where it works; it cannot misread. OCR
        can miss very small or stylized text, and it reports matches it is not
        sure of as approximate."""
        ready = _ocr_prepare("find_text", monitor, region, shot)
        if isinstance(ready, ToolResult):
            return ready
        spec, area = ready
        started = time.monotonic()
        try:
            read = _ocr_read(spec, area, text)
        except (ocrlib.OcrError, capturelib.CaptureError, monitorslib.MonitorError,
                DesktopError) as exc:
            return _ocr_failure("find_text", exc)
        elapsed = time.monotonic() - started
        count = sum(len(matches) for _i, matches, _w in read)
        # Aranan METIN yazilmaz, uzunlugu yazilir: ekranda ne arandigi ozel
        # olabilir (`type` eyleminin kurali).
        gate.audit("find_text", chars=len(text), monitor=str(monitor),
                   region=list(area[0]) if area else None, matches=count,
                   seconds=round(elapsed, 2))
        heading = (
            f"**{count} match(es)** · \"{text}\" ({elapsed:.1f} s, OCR)"
            if count else f"\"{text}\" is not on the screen ({elapsed:.1f} s, OCR)."
        )
        return _ocr_report(text, read, elapsed, heading=heading)

    @mcp.tool(
        output_schema=None,
        annotations={"title": "Wait for text on screen"},
    )
    def wait_for_text(
        text: _OCR_TEXT,
        timeout_seconds: Annotated[
            int,
            Field(
                ge=1,
                le=45,
                description="Give up after this many seconds. Kept under a minute "
                "so the call returns before your client stops waiting for it.",
            ),
        ] = 20,
        gone: Annotated[
            bool,
            Field(
                description="Wait for the text to DISAPPEAR instead, for example a "
                "'Loading' label."
            ),
        ] = False,
        monitor: _OCR_MONITOR = "all",
        region: _OCR_REGION = None,
        shot: _OCR_SHOT = None,
    ) -> ToolResult:
        """Wait until a piece of text appears on screen (or, with gone, until it
        disappears), reading the screen with OCR about once a second, and return
        where it is. Use this instead of a blind wait for something that takes an
        unknown time: a game or installer loading, a dialog opening, a page
        finishing. It returns as soon as the text is seen, with a shot id and
        coordinates ready for mouse(shot=…), or tells you it timed out and what it
        read instead. Nothing is typed or clicked."""
        ready = _ocr_prepare("wait_for_text", monitor, region, shot)
        if isinstance(ready, ToolResult):
            return ready
        spec, area = ready
        started = time.monotonic()
        attempts = 0
        read: list = []
        while True:
            attempts += 1
            began = time.monotonic()
            if attempts > 1:
                # Izin, ekran kilidi ve kira HER turda yeniden okunur: bekleme
                # suresince izin kapanirsa bir kare daha alinmaz.
                runtime.close_capture_if_locked()
                decision = gate.check("wait_for_text", write=False)
                if not decision.allowed:
                    _discard([item for item, _m, _w in read])
                    gate.audit("wait_for_text_denied", reason=decision.reason[:120])
                    return presentationlib.desktop_error_result(
                        presentationlib.decision_error(decision),
                        text=f"⛔ {decision.reason}",
                        permission_scope="pcbridge.desktop",
                    )
                runtime.refresh_capture_deadline()
            previous = read
            try:
                read = _ocr_read(spec, area, text)
            except (ocrlib.OcrError, capturelib.CaptureError,
                    monitorslib.MonitorError, DesktopError) as exc:
                _discard([item for item, _m, _w in previous])
                return _ocr_failure("wait_for_text", exc)
            _discard([item for item, _m, _w in previous])
            visible = ocrlib.seen([m for _i, matches, _w in read for m in matches])
            elapsed = time.monotonic() - started
            if visible != gone:
                break
            if elapsed + 1.0 > timeout_seconds:
                break
            time.sleep(max(0.0, 1.0 - (time.monotonic() - began)))
        done = visible != gone
        gate.audit("wait_for_text", chars=len(text), gone=gone or None,
                   attempts=attempts, seconds=round(elapsed, 1), seen=done)
        if done and not gone:
            heading = (
                f"**Seen** · \"{text}\" after {elapsed:.1f} s (read {attempts}"
                ")."
            )
        elif done:
            heading = (
                f"**Gone** · \"{text}\" left the screen after {elapsed:.1f} s "
                f"(read {attempts})."
            )
        else:
            state = "still on the screen" if gone else "did not appear"
            heading = (
                f"⏱️ Timed out · \"{text}\" {state} within {timeout_seconds} s "
                f"({attempts} reads)."
            )
        result = _ocr_report(text, read, elapsed, heading=heading)
        result.structured_content.update(
            {"done": done, "attempts": attempts, "gone": gone}
        )
        return result

    # ------------------------------------------------- erisilebilirlik agaci
    # Ekranin metinsel ikizi. Model goruntuyu goremedigi icin asil "goz" burasi;
    # tiklama da koordinatla degil dugumun kendi Action'iyla yapiliyor.
    # Hicbiri uinput kullanmaz -> needs_input=False.
    @mcp.tool(
        output_schema=None,
        annotations={"title": "Read the screen as text"},
    )
    def ui_dump(
        target: Annotated[
            str,
            Field(
                description=(
                    "Which window to read: 'focused' (default) for the active "
                    "window, or an application name like 'gnome-text-editor'."
                )
            ),
        ] = "focused",
        interactive_only: Annotated[
            bool,
            Field(
                description=(
                    "Only list things that can be clicked or typed into. Set "
                    "false to also get labels and static text."
                )
            ),
        ] = True,
    ) -> str | ToolResult:
        """List what is on screen as text: every button, menu, text box and label
        the application publishes, each with a short id. Use this when structured
        controls are useful, then act on an item with `ui_click` or `ui_set_text`
        using its id. Prefer it over clicking coordinates when the target appears
        here because accessibility actions address the control directly."""
        denied = _guard("ui_dump", write=False, needs_input=False)
        if denied:
            return denied
        ok, why = tree.available()
        if not ok:
            gate.audit("ui_dump_unavailable", reason=why[:120])
            return _unavailable_result(
                "accessibility.read",
                text=f"⛔ Cannot read the accessibility tree: {why}",
                message=why,
                scope="os.accessibility",
                backend_name="desktop.accessibility",
            )
        try:
            dump = tree.dump(target=target, interactive_only=interactive_only)
        except (uitreelib.UiTreeError, DesktopError) as exc:
            gate.audit("ui_dump_error", error=str(exc)[:160])
            return _exception_result(
                exc,
                text=f"Error: {exc}",
                category=ErrorCategory.ACCESSIBILITY,
                scope="os.accessibility",
                backend_name="desktop.accessibility",
            )
        gate.audit("ui_dump", target=target, nodes=len(dump.nodes),
                   snapshot=getattr(dump, "snapshot", None) or None)
        return jobslib.tail_chars(tree.describe_dump(dump), MAX_INLINE)

    @mcp.tool(
        output_schema=None,
        annotations={"title": "Click something on screen"},
    )
    def ui_click(
        id: Annotated[
            str,
            Field(description="Id of the item from `ui_dump`, e.g. '#90e6'."),
        ],
        force: Annotated[
            bool,
            Field(description="Go ahead even if the user just used the machine."),
        ] = False,
    ) -> str | ToolResult:
        """Click a button, menu item or link by the id `ui_dump` gave it. This asks
        the application to activate that item directly, so it works regardless of
        where the window sits or what has focus. Use it whenever the thing you want
        appears in `ui_dump`; fall back to the `mouse` tool only for things the
        application does not publish, like canvases and games.

        The mouse pointer does NOT move when you use this — the click never goes
        through the virtual mouse. That is intended and it is why this tool cannot
        miss. Do not "correct" it by moving the pointer with the `mouse` tool
        first: the accessibility tree's coordinates are wrong on this system, so
        you would only put the pointer somewhere the click is not happening.

        The id is checked against the item itself, not just its label. If the
        application closed, the item disappeared or was redrawn, or its label
        changed since your `ui_dump`, the click is refused and nothing is
        clicked; call `ui_dump` again and use the new id."""
        denied = _guard("ui_click", force=force, needs_input=False)
        if denied:
            return denied
        write = _begin_write("ui_click")
        if isinstance(write, ToolResult):
            return write
        try:
            res = tree.click(str(id))
        except (uitreelib.UiTreeError, DesktopError) as exc:
            gate.audit("ui_click_error", node=str(id)[:40], error=str(exc)[:160])
            return _exception_result(
                exc,
                text=f"Error: {exc}",
                category=ErrorCategory.ACCESSIBILITY,
                scope="os.accessibility",
                backend_name="desktop.accessibility",
            )
        finally:
            write.close()
        gate.audit("ui_click", node=str(id)[:40], name=res.get("name", "")[:60],
                   snapshot=res.get("snapshot") or None, forced=force or None)
        note = ""
        if res.get("resolved_by") == "moved":
            # Indeks yolu tutmadi ama AYNI dugum (nesne kimligi) yeni yerinde
            # bulundu. Arayuz degismis; model bunu bilsin.
            note = " (the interface changed; the same element was found in its new place)"
        return (
            f"{res.get('role','?')} \"{res.get('name','')}\" clicked{note}.\n"
            "Do not move on before you have checked the result — look again "
            "with ui_dump."
        )

    @mcp.tool(
        output_schema=None,
        annotations={"title": "Type into a text box"},
    )
    def ui_set_text(
        id: Annotated[
            str,
            Field(description="Id of the text box from `ui_dump`, e.g. '#1b72'."),
        ],
        text: Annotated[str, Field(description="The text to put in the box.")],
        force: Annotated[
            bool,
            Field(description="Go ahead even if the user just used the machine."),
        ] = False,
    ) -> str | ToolResult:
        """Replace the contents of a text box directly, by the id `ui_dump` gave it.
        Prefer this over the `keyboard` tool for filling in fields: it writes into
        the widget itself instead of simulating keystrokes, so nothing depends on
        the keyboard layout and no other window can steal the text. Note it
        replaces what is already there rather than appending. Like `ui_click`,
        it is refused when the box is no longer the one `ui_dump` listed."""
        denied = _guard("ui_set_text", force=force, needs_input=False)
        if denied:
            return denied
        write = _begin_write("ui_set_text")
        if isinstance(write, ToolResult):
            return write
        try:
            res = tree.set_text(str(id), text)
        except (uitreelib.UiTreeError, DesktopError) as exc:
            gate.audit("ui_set_text_error", node=str(id)[:40], error=str(exc)[:160])
            return _exception_result(
                exc,
                text=f"Error: {exc}",
                category=ErrorCategory.ACCESSIBILITY,
                scope="os.accessibility",
                backend_name="desktop.accessibility",
            )
        finally:
            write.close()
        # Metnin KENDISI denetim kaydina yazilmaz; parola girilmis olabilir.
        gate.audit("ui_set_text", node=str(id)[:40], chars=len(text),
                   snapshot=res.get("snapshot") or None, forced=force or None)
        return (
            f"{len(text)} characters written into the {res.get('role','?')} "
            f"(replacing {res.get('replaced_chars', 0)} characters).\n"
            "Do not move on before you have checked the result."
        )

    # -------------------------------------------------------- pencere yonetimi
    @mcp.tool(
        output_schema=None,
        annotations={"title": "List open windows"},
    )
    def window_list() -> str | ToolResult:
        """List the windows that are currently open, marking which one has focus.
        Use this to find out what the user is working on, or to pick a window to
        bring forward with `window_focus`. Cheap compared to `ui_dump`: it does
        not read the contents of any window."""
        denied = _guard("window_list", write=False, needs_input=False)
        if denied:
            return denied
        ok, why = tree.available()
        if not ok:
            gate.audit("window_list_unavailable", reason=why[:120])
            return _unavailable_result(
                "window.list",
                text=f"⛔ Cannot read the window list: {why}",
                message=why,
                scope="os.window",
                backend_name="desktop.accessibility",
            )
        try:
            wins = tree.windows()
        except (uitreelib.UiTreeError, DesktopError) as exc:
            gate.audit("window_list_error", error=str(exc)[:160])
            return _exception_result(
                exc,
                text=f"Error: {exc}",
                category=ErrorCategory.ACCESSIBILITY,
                scope="os.window",
                backend_name="desktop.accessibility",
            )
        gate.audit("window_list", windows=len(wins))
        return tree.describe_windows(wins)

    @mcp.tool(
        output_schema=None,
        annotations={"title": "Bring a window to the front"}
    )
    def window_focus(
        window: Annotated[
            str,
            Field(
                description="Application or window name as a human would say it, "
                "e.g. 'Text Editor', 'Google Chrome', 'Vesktop'. The application "
                "does NOT have to be running: a closed application is launched. "
                "A window title only works while the GNOME Shell extension is "
                "available; without it, give the installed application's name."
            ),
        ],
        force: Annotated[
            bool,
            Field(description="Go ahead even if the user just used the machine."),
        ] = False,
    ) -> str | ToolResult:
        """Bring an application's window to the front, launching the application
        first if it is not running. Nothing is typed when the target is already
        in front. An open window is activated through the GNOME Shell extension
        when available; a closed application is started directly. Only when an
        open window cannot be activated that way does desktop search run, as a
        slower fallback. Every result is checked against the window that ends up
        in front, and a wrong window is reported as an error, not as success.

        Use this when the desktop must own the new process lifetime and keep the
        window discoverable across pcbridge restarts. Shell commands remain valid
        for deterministic work and for handing a request, such as a URL, to an
        application process that is already running.

        The extension path takes milliseconds, a cold launch about a second, the
        search fallback several seconds. If the app is already up and you only
        need to press a button or fill a field, prefer `ui_click` /
        `ui_set_text` — those reach the widget directly and do not require the
        window to be in front at all."""
        fast_focus_available = appslib.extension_focus_available()
        denied = _guard(
            "window_focus",
            write=True,
            force=force,
            needs_input=not fast_focus_available,
            input_capability="input.keyboard",
            input_scope="os.keyboard",
        )
        if denied:
            return denied
        write = _begin_write("window_focus")
        if isinstance(write, ToolResult):
            return write
        # `ms` denetim kaydina yaziliyor: 2026-09-02 olcumu yalnizca toplu
        # eylemin `batch_step`inden yapilabilmisti, bu olaylar sure tasimiyordu.
        started = time.monotonic()
        try:
            outcome = appslib.bring_to_front(
                str(window), backend, tree.focused_window, tree.windows
            )
        except (appslib.AppError, DesktopError) as exc:
            gate.audit("window_focus_error", target=str(window)[:60],
                       error=str(exc)[:160],
                       ms=round((time.monotonic() - started) * 1000))
            return _exception_result(
                exc,
                text=f"Error: {exc}",
                category=ErrorCategory.EXECUTION,
                scope="os.window",
                backend_name="desktop.window",
            )
        finally:
            write.close()
        gate.audit("window_focus", target=str(window)[:60], path=outcome.path,
                   ms=round((time.monotonic() - started) * 1000),
                   forced=force or None)
        return outcome.note

    # ------------------------------------------------------------ toplu eylem
    # `DeviceOps` artik `desktop/ops.py`'de: ayni uygulamayi `bin/pcb-do`
    # kabugu da kullaniyor (F bolumu, yerel gorsel ajan). Burada bir kopya
    # dursaydi iki davranis zamanla ayrisirdi.
    batch_ops = opslib.DeviceOps(backend, tree, cfg, capture_provider)

    @mcp.tool(
        output_schema=None,
        annotations={"title": "Run several actions in one go"}
    )
    def computer_batch(
        actions: Annotated[
            str,
            Field(
                description=(
                    'JSON array of actions, run in order. Each item is '
                    '{"a": "<kind>", ...}. Kinds: key {keys, confirm_close?}, '
                    'type {text, raw?}, hold {keys, confirm_close?}, '
                    'release {keys}, wait {ms}, '
                    'move {x, y, shot?, monitor?}, '
                    'click/double_click/triple_click/right_click/middle_click '
                    '{x?, y?, shot?, monitor?, hold_ms?}, '
                    'mouse_down {button?, x?, y?, shot?}, '
                    'mouse_up {button?}, drag {x, y, to_x, to_y, button?, shot?}, '
                    'scroll {amount, horizontal?, shot?}, move_by {dx, dy}, '
                    'ui_click {id}, '
                    'ui_set_text {id, text}, launch {app}, focus {window}. '
                    'shot is the id of the screenshot you read the coordinates off '
                    "(screen_capture prints it, e.g. 'm2-a1b2c3'): pass it and give "
                    'x/y exactly as you see them in that picture, and the server '
                    'converts them for you. A click or scroll with no x/y acts '
                    'where the pointer already is (after move_by, inside an '
                    'application that locked the pointer); hold_ms is how long '
                    'each press lasts, default 60, at most 150 for double and '
                    'triple clicks. hold/mouse_down stay down across later '
                    'actions, so a drag with stops along the way is mouse_down, '
                    'move, move, mouse_up. '
                    'move_by nudges the pointer BY a delta instead of moving '
                    'it TO a point, for applications that lock the pointer and '
                    'read relative motion (games, 3D viewports, WebGL); it is '
                    'not a way to reach anything on screen and it leaves the '
                    'pointer position unknown. Its dx/dy are device units with '
                    'a fixed, linear scale that the application decides: '
                    'calibrate with a known delta and screenshots rather than '
                    'guessing, and expect the first nudge after the application '
                    'captures the pointer to lose its start. '
                    'A key/hold that closes a window or quits an application '
                    '(alt+F4, ctrl+q, ctrl+w) needs confirm_close on that item; '
                    'without it the whole list is rejected and nothing runs. '
                    'Example: [{"a":"click","x":640,"y":360,"shot":"m2-a1b2c3"},'
                    '{"a":"wait","ms":400},{"a":"type","text":"hello"}]'
                )
            ),
        ],
        final: Annotated[
            str,
            Field(
                description="What to return afterwards so you can see the result: "
                "'ui_dump' (default), 'screen_capture', or 'none'."
            ),
        ] = "ui_dump",
        final_monitor: Annotated[
            str,
            Field(
                description="With final='screen_capture': which screen to "
                "capture, in screen_capture's monitor syntax ('all', '1', '2', "
                "'primary'). One monitor costs half of two."
            ),
        ] = "all",
        final_enhance: Annotated[
            bool,
            Field(
                description="With final='screen_capture': brighten a dark "
                "picture, as screen_capture's enhance does."
            ),
        ] = False,
        expect_focus: Annotated[
            str,
            Field(
                description="If a click in this sequence is meant to switch to a "
                "different window, name that window here (a fragment is enough). "
                "Without it, any focus change after a click stops the sequence, "
                "because the following keystrokes would land somewhere unintended."
            ),
        ] = "",
        force: Annotated[
            bool,
            Field(description="Go ahead even if the user just used the machine."),
        ] = False,
    ) -> list[ContentBlock] | ToolResult:
        """Run a whole sequence of desktop actions in a single call, then show you
        the result. Use this instead of calling `mouse`, `keyboard`, `ui_click` one
        at a time: each separate call asks the user for confirmation on their
        phone, so a five-step menu selection becomes five interruptions. Put the
        whole sequence here instead. Actions stop as soon as one fails, the time
        budget runs out, or a click moves focus to a different window — you get
        back what was done and what was left. A list whose estimated duration
        (waits included) exceeds the time budget is refused before anything
        runs, so split long sequences into several calls, and wait for
        something to appear with `wait_for_text` rather than a long blind
        wait."""
        try:
            plan = batchlib.parse(actions, max_actions=cfg.desktop.batch_max_actions)
        except batchlib.BatchError as exc:
            # Kapidan ONCE: hicbir sey calistirilmiyor, yalnizca sozdizimi.
            return _text(f"⛔ {exc}")
        except DesktopError as exc:
            # Icerik kapisi ayristirmada atesLendi (onaylanmamis kapatma):
            # listenin tamami reddedildi, tek eylem bile calismadi.
            gate.audit("computer_batch_refused", error=exc.code.value)
            return _exception_result(
                exc,
                text=f"⛔ {exc}",
                category=exc.category,
                scope="pcbridge.desktop",
                backend_name="desktop.input",
                extra={
                    "batch": {"done": 0, "total": 0, "stopped": "refused"}
                },
            )

        kinds = {a.a for a in plan}
        # Yalnizca erisilebilirlik eylemleri varsa /dev/uinput aranmaz --
        # C bolumunde duzeltilen ayni hata burada tekrarlanmasin.
        # Acik pencere icin eklenti yolu uinput kullanmaz; kapali uygulama da
        # `gtk-launch` ile tussuz acilir. Servis yoksa GNOME aramasinin klavye
        # on kontrolu ve toplu cihaz acilisi aynen korunur. Eklenti bir
        # pencereyi one alamazsa `apps.bring_to_front` yedek arama icin
        # klavyeyi o anda tembel olarak acar. Ayni cevap butce tahmininin
        # hangi `focus` yolunu sayacagini da belirler (Task 6.4).
        fast_focus = appslib.extension_focus_available()
        want_kbd, want_ptr, want_rel = opslib.devices_needed(
            plan,
            focus_uses_keyboard=not fast_focus,
        )
        need_kbd = want_kbd
        needs_input = need_kbd or want_ptr or want_rel
        # Goreli cihaz da fare kapsamindan gecer: ayri cihaz, AYNI izin.
        wants_pointer = want_ptr or want_rel
        input_capability = "input.pointer" if wants_pointer else "input.keyboard"
        input_scope = "os.pointer" if wants_pointer else "os.keyboard"
        denied = _guard(
            "computer_batch",
            write=True,
            force=force,
            needs_input=needs_input,
            input_capability=input_capability,
            input_scope=input_scope,
        )
        if denied:
            return denied

        gap = 0.0
        if cfg.desktop.max_actions_per_second > 0:
            # Hiz sinirina saygi: batch icindeki her eylem gate.check()'ten
            # gecseydi batch kendi kendini bogardi, onun yerine eylemler
            # arasina asgari bosluk konuyor.
            gap = 1.0 / cfg.desktop.max_actions_per_second

        gate.audit("computer_batch_start", count=len(plan),
                   kinds=",".join(sorted(kinds)), forced=force or None)
        # Cihazlari bastan ac: birden fazlasi gerekiyorsa bekleme tek sefere
        # iner (olculdu 2,61 s -> 1,41 s). Gerekmiyorsa hicbir cihaz acilmaz.
        if needs_input:
            try:
                backend.ensure(keyboard=need_kbd, pointer=want_ptr,
                               relative=want_rel)
            except (inputlib.InputError, DesktopError) as exc:
                gate.audit("computer_batch_error", error=str(exc)[:160])
                return _exception_result(
                    exc,
                    text=f"Error: {exc}",
                    category=ErrorCategory.EXECUTION,
                    scope=input_scope,
                    backend_name="desktop.input",
                    extra={
                        "batch": {"done": 0, "total": len(plan), "stopped": "error"}
                    },
                )
        try:
            with runtime.write_sequence("computer_batch") as guard:
                result = batchlib.run(
                    plan,
                    batch_ops,
                    # Kilit icin beklenen sure butceden dusulur: bekleme + butce
                    # yine 110 saniyelik MCP tavaninin altinda kalir.
                    budget=max(
                        0.0, float(cfg.desktop.batch_budget_seconds) - guard.waited
                    ),
                    min_gap=gap,
                    check_focus=cfg.desktop.batch_check_focus,
                    expect_focus=expect_focus or "",
                    repeat_limit=cfg.desktop.repeat_click_limit,
                    before_action=guard,
                    fast_focus=fast_focus,
                )
        except executionlib.SequenceRefused as exc:
            return _sequence_refused(
                "computer_batch",
                exc,
                extra={
                    "batch": {
                        "done": 0,
                        "total": len(plan),
                        "stopped": "busy" if exc.code == ErrorCode.BUSY else "safety",
                    }
                },
            )
        except OSError as exc:
            gate.audit("computer_batch_error", error=str(exc)[:160])
            return _exception_result(
                exc,
                text=f"⛔ Could not take the desktop execution lock: {exc}",
                category=ErrorCategory.EXECUTION,
                scope="pcbridge.desktop",
                backend_name="desktop.execution",
                extra={"batch": {"done": 0, "total": len(plan), "stopped": "error"}},
            )
        for step in result.steps:
            # Metin ICERIGI yazilmaz -- `ui_set_text`teki kural aynen gecerli.
            gate.audit("batch_step", i=step.index, a=step.action,
                       ok=step.ok, ms=round(step.ms))
        gate.audit("computer_batch", done=result.done, total=result.total,
                   seconds=round(result.elapsed, 1), stopped=result.stopped or None)

        out = [batchlib.describe(result)]
        batch_data = {
            "done": result.done,
            "total": result.total,
            "stopped": result.stopped,
        }
        if result.error is not None:
            if result.stopped == "safety":
                error_scope = "pcbridge.desktop"
                error_category = ErrorCategory.SAFETY
            elif want_ptr:
                error_scope = "os.pointer"
                error_category = ErrorCategory.EXECUTION
            elif need_kbd:
                error_scope = "os.keyboard"
                error_category = ErrorCategory.EXECUTION
            elif "focus" in kinds:
                error_scope = "os.window"
                error_category = ErrorCategory.EXECUTION
            else:
                error_scope = "os.accessibility"
                error_category = ErrorCategory.ACCESSIBILITY
            return _exception_result(
                result.error,
                text=jobslib.tail_chars(out[0], MAX_INLINE),
                category=error_category,
                scope=error_scope,
                extra={"batch": batch_data},
            )

        def _report(final_text: list[str]) -> str:
            # KIRPILAN YALNIZCA batch raporu. Son adimin metni (cekim
            # kimlikleri, ofset, olcek) oldugu gibi gidiyor: eskiden tamami
            # birlikte kirpiliyordu ve `tail_chars` BASTAN kestigi icin uzun
            # bir raporda kimlik satirlari eslestikleri goruntulerden
            # ayrilabiliyordu.
            return "\n".join(
                [jobslib.tail_chars("\n".join(out), MAX_INLINE), *final_text]
            )

        def _with_batch_error(final_result: ToolResult) -> ToolResult:
            final_text: list[str] = []
            images: list[ContentBlock] = []
            for block in final_result.content:
                if isinstance(block, TextContent):
                    final_text.append(block.text)
                else:
                    images.append(block)
            structured = dict(final_result.structured_content or {})
            structured["batch"] = batch_data
            return ToolResult(
                content=[_text(_report(final_text)), *images],
                structured_content=structured,
                is_error=True,
            )

        # `screen_capture` artik blok listesi donuyor: metin blogu rapora
        # katiliyor, goruntu bloklari SONA ekleniyor. Metnin ONCE gelmesi onemli
        # -- ofset ve olcek bilgisi goruntuden ayrilirsa koordinat hesabi
        # yapilamaz.
        images: list[ContentBlock] = []
        final_text: list[str] = []
        want = (final or "ui_dump").strip().lower()
        if want == "screen_capture":
            out.append("\n---")
            final_result = screen_capture(
                monitor=final_monitor or "all", enhance=final_enhance
            )
            if isinstance(final_result, ToolResult):
                return _with_batch_error(final_result)
            for block in final_result:
                if isinstance(block, TextContent):
                    final_text.append(block.text)
                else:
                    images.append(block)
        elif want != "none":
            final_result = ui_dump()
            if isinstance(final_result, ToolResult):
                out += ["", "---"]
                return _with_batch_error(final_result)
            out += ["", "---", final_result]
        return [_text(_report(final_text)), *images]

    # ----------------------------------------------------- yerel gorsel ajan
    @mcp.tool(
        output_schema=None,
        annotations={"title": "Let a local agent drive the screen"}
    )
    def computer_task(
        goal: Annotated[
            str,
            Field(
                description="What should end up being true on screen, in plain "
                "language. Be specific about the target: which app, which "
                "conversation, which file. Example: 'In Vesktop, open the DM "
                "with alex and send: hello'."
            ),
        ],
        app: Annotated[
            str | None,
            Field(
                description="Application to open and bring to the front first, "
                "e.g. 'Vesktop' or 'Text Editor'. Leave empty to work with "
                "whatever is already on screen."
            ),
        ] = None,
        agent: Annotated[str | None, Field(description=_DESC_AGENT)] = None,
        model: Annotated[str | None, Field(description=_DESC_MODEL)] = None,
        effort: Annotated[str | None, Field(description=_DESC_EFFORT)] = None,
        max_steps: Annotated[
            int | None,
            Field(
                ge=1, le=200,
                description="Roughly how many look-act rounds the agent may "
                "spend. This is a budget in its instructions, not a hard cap.",
            ),
        ] = None,
        wait_seconds: Annotated[
            int,
            Field(ge=0, le=110,
                  description="Block this long waiting for it to finish. GUI "
                  "work takes minutes, so 0 and polling with job_status is "
                  "usually right."),
        ] = 0,
        timeout: Annotated[
            int | None,
            Field(description="Kill the agent after this many seconds."),
        ] = None,
        force: Annotated[
            bool,
            Field(description="Go ahead even if the user just used the machine."),
        ] = False,
    ) -> str | ToolResult:
        """Hand a long-running graphical task to an agent on the user's own machine
        and get a job id back, so you are not blocked while it works. The local
        agent takes a screenshot, looks at it, clicks, checks the result, and
        repeats until the goal is met; poll it with `job_status`.

        If you can see images yourself, you usually do NOT need this: call
        `screen_capture`, look, then `computer_batch`. Reach for this one when the
        work is long enough that you would rather not sit through it — a
        many-step flow, an install wizard, a slow app. Reach for `ui_dump` +
        `ui_click` first for anything the accessibility tree lists; that path is
        cheaper than both and cannot miss."""
        # Kapi BIR KEZ, burada. `pcb-do` her cagrida izin penceresini ve ekran
        # kilidini yeniden okuyor ama BOSTA kontrolunu okumuyor: uinput idle'i
        # sifirladigi icin ajan ikinci eylemde kendi tusunu "kullanici geldi"
        # sanardi (olculdu 104227 ms -> 151 ms). Kontrol gorev basina.
        denied = _guard("computer_task", write=True, force=force)
        if denied:
            return denied
        last_token = getattr(gate, "last_token", None)
        grant_token = last_token() if callable(last_token) else None

        skill = _SKILL_PATH
        if not skill.is_file():
            text = (
                f"⛔ The computer-use instructions are missing: {skill}. The "
                "installation's `skills/computer-use/SKILL.md` was removed or moved."
            )
            error = DesktopError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message=f"The computer-use instructions are missing: {skill}.",
                category=ErrorCategory.CAPABILITY,
                retryable=False,
                suggested_action="Restore skills/computer-use/SKILL.md and retry.",
                permission_scope="pcbridge.desktop",
                backend="computer_task",
            )
            return presentationlib.desktop_error_result(error, text=text)
        try:
            instructions = skill.read_text(encoding="utf-8")
        except OSError as exc:
            return _exception_result(
                exc,
                text=f"⛔ The computer-use instructions could not be read: {exc}",
                category=ErrorCategory.CAPABILITY,
                scope="pcbridge.desktop",
                backend_name="computer_task",
            )

        spec = cfg.desktop
        # Config'teki uclu (agent, model, effort) BIRBIRINE AIT: model adi o
        # ajanin listesinden geliyor. Cagrida BASKA bir ajan istendiginde
        # config'in modelini ona tasimak sessiz bir hata degil, gurultulu bir
        # hata uretiyordu: `computer_task(agent="claude")` cozumlemede
        # "'gemini-3.6-flash' claude icin gecerli bir model degil" ile patliyordu
        # (fiilen uretildi). Ajan acikca degistirildiyse model/effort da o ajanin
        # kendi varsayilanina birakiliyor.
        agent_changed = bool(agent) and agent.strip() != spec.computer_task_agent
        res = modelslib.resolve(
            cfg,
            agent=agent or spec.computer_task_agent,
            model=model or (None if agent_changed else spec.computer_task_model),
            effort=effort or (None if agent_changed else spec.computer_task_effort),
        )
        if res.error:
            return res.error
        agent_spec = cfg.agents[res.agent]

        opened = ""
        if app:
            # Uygulamayi SUNUCU aciyor, ajan degil: boylece ajan bilinen bir
            # ekranla basliyor ve "hangi pencere" belirsizligi bir tur once
            # cozuluyor. Basarisiz olursa is hic baslatilmiyor.
            write = _begin_write("computer_task")
            if isinstance(write, ToolResult):
                return write
            try:
                opened = appslib.prepare(
                    str(app), backend, tree.focused_window, tree.windows
                )
            except (appslib.AppError, DesktopError) as exc:
                gate.audit("computer_task_app_error", app=str(app)[:60],
                           error=str(exc)[:160])
                return _exception_result(
                    exc,
                    text=f"⛔ `{app}` could not be prepared: {exc}",
                    category=ErrorCategory.EXECUTION,
                    scope="os.window",
                    backend_name="desktop.window",
                )
            finally:
                write.close()

        steps = int(max_steps or spec.computer_task_max_steps)
        prompt = _task_prompt(instructions, str(goal), opened, steps)

        task_argv = [
            a.replace("{prompt}", prompt) if "{prompt}" in a else a
            for a in agent_spec.command
        ] + modelslib.build_args(agent_spec, res)
        task_env = _session_env() or {}
        task_exe = (
            exelib.find_executable(task_argv[0], task_env.get("PATH")) if task_argv else None
        )
        if task_exe is None:
            return exelib.not_found_message(
                res.agent, task_argv[0] if task_argv else "", cfg.source_path
            )
        task_argv[0] = str(task_exe)

        job_id = jm.start(
            kind=f"computer_task:{res.agent}",
            argv=task_argv,
            cwd=cfg.default_workdir,
            label=jobslib._short(goal, 90),
            parser=agent_spec.parser,
            timeout=timeout,
            pty=agent_spec.pty,
            # `pcb-do` bunu gorup BOSTA kontrolunu atlar -- ve YALNIZCA onu.
            # Ekran kilidi, izin penceresi ve hiz siniri aynen isler.
            env={**task_env, "PCBRIDGE_TASK_FORCE": "1"},
            extra={
                "agent": res.agent,
                "model": res.model,
                "effort": res.effort,
                "model_notes": res.notes,
                "goal": str(goal),
                "app": app,
                "max_steps": steps,
            },
        )
        # Kayan kira gorevin ortasinda dusmesin: is kostugu surece izni
        # tazele. Ayrinti `_heartbeat_loop`ta.
        _heartbeat_add(job_id, grant_token)
        # Hedef METNI kaydedilmez, uzunlugu kaydedilir: ekranda ne yapilacagi
        # ozel bilgi icerebilir. Tam metin jobs/<id>/meta.json'da.
        gate.audit("computer_task", agent=res.agent, model=res.model,
                   effort=res.effort, job=job_id, goal_chars=len(str(goal)),
                   app=str(app)[:60] if app else None, steps=steps,
                   forced=force or None)

        if wait_seconds > 0:
            jm.wait(job_id, wait_seconds)

        head = [
            f"**{job_id}** — computer-use agent started ({res.headline()})",
            f"goal: {_short(goal, 160)}",
        ]
        if opened:
            head.append(f"prepared: {opened}")
        head.append(
            f"step budget: {steps} · follow it with `job_status(\"{job_id}\")`"
        )
        head.append(
            "To stop it: `desktop_lock` (the agent's hands stop at its next action) "
            f"or `job_cancel(\"{job_id}\")`."
        )
        if wait_seconds > 0:
            return "\n".join(head) + "\n\n---\n" + _fmt_job_summary(cfg, jm, job_id)
        return "\n".join(head)

    # ================================================================== SISTEM
    @mcp.tool(annotations={"title": "Computer status"})
    def system_status() -> str:
        """Show the desktop computer's current status: uptime, load, memory, disk
        usage, GPU, plus running jobs and open terminal sessions."""
        def sh(c: str) -> str:
            try:
                return subprocess.run(
                    ["bash", "-lc", c], capture_output=True, text=True, timeout=15
                ).stdout.strip()
            except Exception:
                return ""

        host = sh("hostnamectl --static 2>/dev/null || hostname")
        uptime = sh("uptime -p")
        load = sh("cut -d' ' -f1-3 /proc/loadavg")
        mem = sh("free -h | awk '/Mem:/ {print $3 \" / \" $2}'")
        disks = sh(
            "df -h -x tmpfs -x devtmpfs --output=target,size,used,avail,pcent | head -12"
        )

        parts = [
            "**Computer status**",
            "",
            f"- host: {host}",
            f"- uptime: {uptime}",
            f"- load: {load}",
            f"- memory: {mem}",
            "",
            "**Disks**",
            "```",
            disks,
            "```",
        ]
        gpu = sh(
            "nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu "
            "--format=csv,noheader 2>/dev/null"
        )
        if gpu:
            parts += ["**GPU**", "```", gpu, "```"]

        parts.append(f"\n**Desktop:** {gate.status_line()}")
        from . import daemon as daemonlib

        parts.append(f"**pcbridge {__version__}:** {daemonlib.describe()}")

        running = jm.list_jobs(limit=10, only_running=True)
        parts.append(f"\n**Running jobs:** {len(running)}")
        for r in running:
            parts.append(f"- `{r['job_id']}` {r['kind']} · {r['elapsed_seconds']}s · {r['label']}")

        if tmuxctl.available():
            try:
                sessions = tmuxctl.list_sessions()
                parts.append(f"\n**Open terminals:** {len(sessions)}")
                for s in sessions:
                    parts.append(f"- `{s['session']}` → {s['running']} ({s['path']})")
            except tmuxctl.TmuxError as exc:
                parts.append(f"\n**Open terminals:** unreadable ({exc})")
        return "\n".join(parts)

    @mcp.tool(annotations={"title": "Show a desktop notification"})
    def notify(
        message: Annotated[str, Field(description="Notification body text.")],
        title: Annotated[str, Field(description="Notification title.")] = "pcbridge",
    ) -> str:
        """Pop up a desktop notification on the user's computer screen. Useful to
        leave a note for when they get back to the machine."""
        try:
            subprocess.run(
                ["notify-send", "-a", "pcbridge", title, message],
                timeout=10,
                capture_output=True,
            )
            return "Notification sent."
        except FileNotFoundError:
            return "notify-send not found: `sudo apt install libnotify-bin`"
        except Exception as exc:  # pragma: no cover
            return f"Notification failed: {exc}"

    return runtime
