"""MCP arac (tool) tanimlari.

Aciklamalar bilincli olarak Ingilizce: Gemini Spark ozel MCP uygulamalarini
su an yalnizca Ingilizce destekliyor ve arac secimini bu metinlere bakarak
yapiyor. Kullaniciya donen metinler Turkce.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.utilities.types import Image
from mcp.types import ContentBlock, TextContent
from pydantic import Field

from . import jobs as jobslib
from . import models as modelslib
from . import shots as shotslib
from . import tmuxctl
from .config import Config
from .desktop import apps as appslib
from .desktop import batch as batchlib
from .desktop import capture as capturelib
from .desktop import input as inputlib
from .desktop import monitors as monitorslib
from .desktop import ops as opslib
from .desktop import safety as safetylib
from .desktop import uitree as uitreelib

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
_SKILL_PATH = Path(__file__).resolve().parent.parent / "skills" / "computer-use" / "SKILL.md"


def _task_prompt(instructions: str, goal: str, prepared: str, max_steps: int) -> str:
    """Gorsel ajanin alacagi tam prompt.

    Yonerge ONCE geliyor, hedef SONRA: ajan once nasil calisacagini, sonra ne
    yapacagini okusun. Hedef en sonda kaliyor ki uzun yonergenin icinde
    kaybolmasin.
    """
    parts = [instructions.strip(), "", "---", ""]
    if prepared:
        parts += [f"Uygulama senin icin hazirlandi: {prepared}", ""]
    parts += [
        f"Adim butcen yaklasik {max_steps} bak-eyle turu. Asacak gibiyse dur ve "
        "nerede kaldigini yaz.",
        "",
        "Isin bittiginde SON CEVABINDA sunu yaz: ne yaptin, ekranda ne "
        "gorunuyor, ve hedefe ulasildi mi (evet/hayir). Ulasilmadiysa sebebini "
        "yaz -- basarili gibi gorunen basarisiz bir is en kotu sonuc.",
        "",
        "## Görev",
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
            f"Istenen model `{requested}` ama calisan `{actual}`. "
            "CLI istegi yok saymis olabilir."
        )
    for w in warnings:
        lines.append(f"⚠️ **{w}**")
    if warnings:
        lines.append("")

    lines += [
        f"**{job_id}** — durum: `{st['status']}`"
        + (f" (exit {st['exit_code']})" if st.get("exit_code") is not None else ""),
    ]
    if st.get("agent"):
        head = f"ajan: {st['agent']}"
        if st.get("model"):
            head += f" · model: {st['model']}"
        if st.get("effort"):
            head += f" · effort: {st['effort']}"
        if actual and (not requested or not _model_matches(requested, actual)):
            head += f" · calisan: {actual}"
        lines.append(head)
    for note in st.get("model_notes") or []:
        lines.append(f"_not: {note}_")
    lines += [
        f"komut: `{jobslib.tail_chars(st['command'], 300)}`",
        f"dizin: `{st['cwd']}` · sure: {st['elapsed_seconds']}s",
    ]
    if parsed.get("session_id"):
        lines.append(
            f"oturum kimligi: `{parsed['session_id']}` "
            "(devam etmek icin agent_run'a resume_session olarak ver)"
        )
    if parsed.get("steps"):
        lines.append("\n**Adimlar:**")
        lines.extend(parsed["steps"][-25:])
    if parsed.get("final_answer"):
        lines.append("\n**Sonuc:**")
        lines.append(jobslib.tail_chars(str(parsed["final_answer"]), MAX_INLINE))
    elif st["status"] == "running":
        lines.append("\n(hala calisiyor — birkac saniye sonra job_status ile tekrar bak)")
    if parsed.get("unparsed"):
        lines.append("\n**Ham cikti (ayristirilamayan):**")
        lines.append("\n".join(parsed["unparsed"]))
    if not parsed.get("final_answer") and not parsed.get("steps") and log.strip():
        lines.append("\n**Cikti:**")
        lines.append(jobslib.tail_chars(jobslib.strip_ansi(log), MAX_INLINE))
    if parsed.get("cost_usd") is not None:
        lines.append(f"\n_maliyet: ${parsed['cost_usd']:.4f} · tur: {parsed.get('num_turns')}_")
    elif parsed.get("total_tokens") is not None:
        lines.append(
            f"\n_jeton: {parsed['total_tokens']:,} · tur: {parsed.get('num_turns')}_"
        )
    return "\n".join(lines)


def register(
    mcp: FastMCP,
    cfg: Config,
    jm: jobslib.JobManager,
    shot_store: "shotslib.ShotStore | None" = None,
    transport: str = "http",
) -> None:
    global _DESC_AGENT, _DESC_MODEL, _DESC_EFFORT
    _DESC_AGENT = (
        "Agent name, e.g. 'claude' or 'antigravity'. Optional: if omitted it is "
        f"inferred from the model, defaulting to '{cfg.default_agent}'. Give it "
        "explicitly to reach an agent's restricted models."
    )
    _DESC_MODEL = (
        "Model to run with. Aliases and free text are accepted ('opus', "
        "'Gemini 3.6 Flash', '3.1 pro'). Valid values — "
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
    gate = safetylib.SafetyGate(cfg)

    # Bir kez hesaplanir: arac calisma anina kadar ne ayar ne tasima degisir.
    inline_images = _want_inline(cfg.inline_images, transport)

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
        out = ["**Tanimli ajanlar**", ""]
        for name, spec in cfg.agents.items():
            if not spec.enabled:
                out.append(f"- `{name}` — devre disi (config.toml)")
                continue
            exe = spec.command[0] if spec.command else ""
            found = subprocess.run(
                ["bash", "-lc", f"command -v {shlex.quote(exe)}"],
                capture_output=True,
                text=True,
            )
            where = found.stdout.strip()
            mark = "✅" if where else "❌ PATH'te bulunamadi"
            out.append(f"- `{name}` — {spec.description or exe} · {mark} {where}")
            out.append(f"  - komut: `{shlex.join(spec.command)}`")
            out.extend(modelslib.describe_agent(spec))
            out.append("")
        out.append(f"ajan belirtilmezse: `{cfg.default_agent}`")
        out.append(f"varsayilan calisma dizini: `{cfg.default_workdir}`")
        return "\n".join(out)

    @mcp.tool(
        annotations={"title": "Send a prompt to a coding agent", "destructiveHint": True},
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
        if resume_session and spec.resume_args:
            argv += [a.replace("{session_id}", resume_session) for a in spec.resume_args]

        cwd = _resolve_dir(cfg, workdir)
        if not cwd.is_dir():
            return f"Dizin yok: {cwd}"

        job_id = jm.start(
            kind=f"agent:{res.agent}",
            argv=argv,
            cwd=cwd,
            label=jobslib._short(prompt, 90),
            parser=spec.parser,
            timeout=timeout,
            pty=spec.pty,
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
    @mcp.tool(annotations={"title": "Check a background job", "readOnlyHint": True})
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

    @mcp.tool(annotations={"title": "Read raw job output", "readOnlyHint": True})
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
            return "Henuz cikti yok."
        return "```\n" + jobslib.tail_chars(log, tail_chars) + "\n```"

    @mcp.tool(annotations={"title": "List background jobs", "readOnlyHint": True})
    def job_list(
        limit: Annotated[int, Field(ge=1, le=100)] = 15,
        only_running: bool = False,
    ) -> str:
        """List recent background jobs on the computer, newest first."""
        rows = jm.list_jobs(limit=limit, only_running=only_running)
        if not rows:
            return "Kayitli is yok."
        out = ["| job_id | tur | durum | sure | aciklama |", "|---|---|---|---|---|"]
        for r in rows:
            out.append(
                f"| `{r['job_id']}` | {r['kind']} | {r['status']} | "
                f"{r['elapsed_seconds']}s | {r['label']} |"
            )
        return "\n".join(out)

    @mcp.tool(annotations={"title": "Cancel a job", "destructiveHint": True})
    def job_cancel(job_id: str) -> str:
        """Stop a running background job (sends SIGTERM, then SIGKILL)."""
        try:
            out = jm.cancel(job_id)
        except KeyError as exc:
            return str(exc)
        gate.audit("job_cancel", job=str(job_id)[:40])
        return out

    # =================================================================== TMUX
    @mcp.tool(annotations={"title": "List live terminal sessions", "readOnlyHint": True})
    def tmux_list() -> str:
        """List the live tmux terminal sessions on the computer. These are real
        terminals the user can also attach to physically."""
        if not tmuxctl.available():
            return "tmux kurulu degil: `sudo apt install tmux`"
        rows = tmuxctl.list_sessions()
        if not rows:
            return "Acik tmux oturumu yok."
        out = ["| oturum | calisan | dizin | PC'de acik mi |", "|---|---|---|---|"]
        for r in rows:
            out.append(
                f"| `{r['session']}` | {r['running']} | {r['path']} | "
                f"{'evet' if r['attached_on_pc'] else 'hayir'} |"
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
            return "tmux kurulu degil: `sudo apt install tmux`"
        cwd = _resolve_dir(cfg, workdir)
        if not cwd.is_dir():
            return f"Dizin yok: {cwd}"
        try:
            msg = tmuxctl.start(session, command, str(cwd))
        except tmuxctl.TmuxError as exc:
            return f"Hata: {exc}"
        time.sleep(1.2)
        try:
            screen = tmuxctl.capture(session, 25)
        except tmuxctl.TmuxError:
            screen = ""
        return (
            f"{msg}\nPC'de izlemek icin: `{tmuxctl.attach_hint(session)}`\n\n"
            f"```\n{screen}\n```"
        )

    @mcp.tool(annotations={"title": "Type into a live terminal", "destructiveHint": True})
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
            return f"Hata: {exc}"
        # Gonderilen METIN kaydedilmez, uzunlugu kaydedilir: terminale parola
        # yazilmis olabilir.
        gate.audit("tmux_send", session=str(session)[:40], chars=len(text or ""))
        if capture_after_seconds:
            time.sleep(capture_after_seconds)
        try:
            return f"Gonderildi.\n\n```\n{tmuxctl.capture(session, 45)}\n```"
        except tmuxctl.TmuxError as exc:
            return f"Gonderildi ama ekran okunamadi: {exc}"

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
            return f"Hata: {exc}"
        gate.audit("tmux_keys", session=str(session)[:40],
                   keys=_short(" ".join(keys or []), 60))
        if capture_after_seconds:
            time.sleep(capture_after_seconds)
        try:
            return f"```\n{tmuxctl.capture(session, 45)}\n```"
        except tmuxctl.TmuxError as exc:
            return str(exc)

    @mcp.tool(annotations={"title": "Read a live terminal screen", "readOnlyHint": True})
    def tmux_capture(
        session: str,
        lines: Annotated[int, Field(ge=5, le=400)] = 60,
    ) -> str:
        """Read the current contents of a live terminal session's screen."""
        try:
            return f"```\n{tmuxctl.capture(session, lines)}\n```"
        except tmuxctl.TmuxError as exc:
            return f"Hata: {exc}"

    @mcp.tool(annotations={"title": "Close a live terminal", "destructiveHint": True})
    def tmux_kill(session: str) -> str:
        """Close a live terminal session and everything running inside it."""
        try:
            out = tmuxctl.kill(session)
        except tmuxctl.TmuxError as exc:
            return f"Hata: {exc}"
        gate.audit("tmux_kill", session=str(session)[:40])
        return out

    # ================================================================== SHELL
    @mcp.tool(annotations={"title": "Run a shell command", "destructiveHint": True})
    def shell_run(
        command: Annotated[str, Field(description="Shell command line to execute.")],
        workdir: str | None = None,
        timeout: Annotated[int, Field(ge=1, le=120)] = 60,
    ) -> str:
        """Run a short shell command on the user's Linux desktop and return its
        output. For anything that may take longer than a minute use
        shell_run_background instead."""
        cwd = _resolve_dir(cfg, workdir)
        if not cwd.is_dir():
            return f"Dizin yok: {cwd}"
        limit = min(timeout, cfg.max_sync_timeout)
        started = time.monotonic()
        try:
            proc = subprocess.run(
                ["bash", "-lc", command],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=limit,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            gate.audit("shell_run", cmd=_short(command), timeout=limit)
            return (
                f"`{command}` {limit} saniyede bitmedi ve iptal edildi. "
                "Uzun surecekse shell_run_background kullan."
            )
        # Komut kaydedilir, CIKTISI kaydedilmez: cikti parola, token ya da
        # ozel yazisma icerebilir. `ui_set_text`teki kural burada da gecerli.
        gate.audit("shell_run", cmd=_short(command), exit=proc.returncode,
                   seconds=round(time.monotonic() - started, 1))
        body = jobslib.strip_ansi((proc.stdout or "") + (proc.stderr or ""))
        head = f"`$ {command}` (dizin: {cwd}) → exit {proc.returncode}"
        if not body.strip():
            return head + "\n(cikti yok)"
        return head + "\n```\n" + jobslib.tail_chars(body, MAX_INLINE) + "\n```"

    @mcp.tool(
        annotations={"title": "Run a long shell command in background", "destructiveHint": True}
    )
    def shell_run_background(
        command: str,
        workdir: str | None = None,
        timeout: int | None = None,
    ) -> str:
        """Start a long-running shell command in the background (builds, installs,
        downloads). Returns a job id to poll with job_status."""
        cwd = _resolve_dir(cfg, workdir)
        if not cwd.is_dir():
            return f"Dizin yok: {cwd}"
        job_id = jm.start(
            kind="shell",
            argv=["bash", "-lc", command],
            cwd=cwd,
            label=jobslib._short(command, 90),
            parser="plain",
            timeout=timeout,
        )
        gate.audit("shell_run_background", cmd=_short(command), job=job_id)
        return f"Baslatildi: `{job_id}`\nDurum icin: job_status('{job_id}')"

    # =================================================================== DOSYA
    @mcp.tool(annotations={"title": "List a directory", "readOnlyHint": True})
    def fs_list(
        path: str | None = None,
        show_hidden: bool = False,
    ) -> str:
        """List the contents of a directory on the computer with sizes and dates."""
        d = _resolve_dir(cfg, path)
        if not d.is_dir():
            return f"Dizin yok: {d}"
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
            return f"`{d}` bos."
        return f"`{d}` ({len(entries)} oge)\n```\n" + "\n".join(entries[:400]) + "\n```"

    @mcp.tool(annotations={"title": "Read a file", "readOnlyHint": True})
    def fs_read(
        path: Annotated[str, Field(description="Absolute path of the file to read.")],
        max_chars: Annotated[int, Field(ge=200, le=60000)] = 8000,
    ) -> str:
        """Read the contents of a text file on the computer."""
        f = _resolve_file(cfg, path)
        if not f.is_file():
            return f"Dosya yok: {f}"
        try:
            data = f.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"Okunamadi: {exc}"
        # YOL kaydedilir, ICERIK kaydedilmez. Bu satirin asil amaci: `config.toml`
        # parola ve statik token iceriyor ve okunmasi engellenmis DEGIL (engellemek
        # aldatici olurdu -- `shell_run` zaten keyfi komut calistiriyor, `cat` ile
        # de okunur). Engellemek yerine IZ birakiliyor.
        gate.audit("fs_read", path=str(f)[:200], bytes=f.stat().st_size)
        return f"`{f}` ({f.stat().st_size:,} bayt)\n```\n" + jobslib.tail_chars(
            data, max_chars
        ) + "\n```"

    @mcp.tool(annotations={"title": "Write a file", "destructiveHint": True})
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
            return f"Yazilamadi: {exc}"
        gate.audit("fs_write", path=str(f)[:200], chars=len(content or ""),
                   append=append or None)
        return f"{'Eklendi' if append else 'Yazildi'}: `{f}` ({f.stat().st_size:,} bayt)"

    @mcp.tool(annotations={"title": "Search inside files", "readOnlyHint": True})
    def fs_search(
        query: Annotated[str, Field(description="Text or regex to search for.")],
        path: str | None = None,
        max_results: Annotated[int, Field(ge=1, le=200)] = 40,
    ) -> str:
        """Search for text inside files under a directory (uses ripgrep if
        available, otherwise grep)."""
        d = _resolve_dir(cfg, path)
        if not d.is_dir():
            return f"Dizin yok: {d}"
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
            return f"`{query}` icin `{d}` altinda sonuc yok."
        lines = out.splitlines()[:max_results]
        return f"`{d}` altinda {len(lines)} sonuc\n```\n" + "\n".join(lines) + "\n```"

    # =============================================================== MASAUSTU
    # Klavye/fare kontrolu. Her cagri once SafetyGate'ten gecer: [desktop]
    # enabled, ekran kilidi, sureli izin, kullanici cakismasi, hiz siniri.
    backend = inputlib.InputBackend(
        pointer_speed=cfg.desktop.pointer_speed,
        pointer_max_ms=cfg.desktop.pointer_move_max_ms,
        hold_max_seconds=cfg.desktop.hold_max_seconds,
    )
    tree = uitreelib.UiTree()

    def _held_note() -> str:
        """Basili tutulan varsa yanitin sonuna eklenecek not.

        Ajanin `release`i unutmasi sessiz kalmasin: her cagrida gorunur.
        Zamanlayici bir sey biraktiysa o da BIR KEZ bildirilir.
        """
        parts: list[str] = []
        freed = backend.take_auto_released()
        if freed:
            parts.append(
                f"⚠ {cfg.desktop.hold_max_seconds} sn dolduğu için kendiliğinden "
                f"bırakıldı: {', '.join(freed)}"
            )
        still = backend.held()
        if still:
            parts.append(f"basılı tutulan: {', '.join(still)}")
        return ("\n· " + "\n· ".join(parts)) if parts else ""

    def _guard(
        tool: str,
        write: bool = True,
        force: bool = False,
        needs_input: bool = True,
    ) -> str | None:
        """Reddedildiyse kullaniciya donecek Turkce gerekce, izinliyse None.

        `needs_input=False`: sanal klavye/fare cihazi ARANMAZ. Ekran goruntusu
        ve erisilebilirlik araclari uinput kullanmiyor; /dev/uinput yokken
        onlari "girdi cihazi yok" diye reddetmek yanlis gerekce olurdu.
        """
        decision = gate.check(tool, write=write, force=force)
        if not decision.allowed:
            gate.audit(f"{tool}_denied", reason=decision.reason[:120])
            return f"⛔ {decision.reason}"
        if needs_input:
            ok, why = backend.available()
            if not ok:
                gate.audit(f"{tool}_unavailable", reason=why[:120])
                return f"⛔ Sanal girdi cihazi kullanilamiyor: {why}"
        return None

    @mcp.tool(
        annotations={"title": "Allow desktop control for a while", "destructiveHint": True}
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
    ) -> str:
        """Open a time-limited permission window for controlling the computer's
        keyboard and mouse. The mouse and keyboard tools refuse to do anything
        until this is called, and the permission expires on its own. Call this
        first whenever the user asks you to click, type or drive an application
        on their screen."""
        if not cfg.desktop.enabled:
            return (
                "⛔ Masaustu kontrolu kapali. config.toml'da `[desktop] enabled = true` "
                "yapip `systemctl --user restart pcbridge` calistirin. Once "
                "`sudo ./setup_uinput.sh` gerekiyor (bir kez)."
            )
        ok, why = backend.available()
        if not ok:
            return f"⛔ Sanal girdi cihazi kullanilamiyor: {why}"

        msg = gate.unlock(minutes, reason or "")
        subprocess.run(
            [
                "notify-send",
                "-a",
                "pcbridge",
                "-u",
                "critical",
                "Masaüstü kontrolü açıldı",
                f"{minutes} dakika · {reason or 'gerekçe belirtilmedi'}",
            ],
            capture_output=True,
            timeout=10,
        )
        out = [msg, "", monitorslib.describe(), ""]
        out.append(
            "Koordinatlar **global tuval uzayinda**; sol ust (0, 0). Monitore ozel "
            "koordinat verecekseniz `monitor` parametresini de verin."
        )
        out.append("Erken kapatmak icin: desktop_lock")
        return "\n".join(out)

    @mcp.tool(annotations={"title": "Stop desktop control"})
    def desktop_lock() -> str:
        """Close the desktop control permission immediately instead of waiting for
        it to expire, and destroy the virtual keyboard/mouse devices. Any key or
        mouse button still held down is released first. Use when the user says they
        are done, or asks you to stop touching their screen."""
        freed = backend.release_all()
        backend.close()
        note = f"\n· bırakılan: {', '.join(freed)}" if freed else ""
        return gate.lock() + note

    @mcp.tool(annotations={"title": "Move or click the mouse", "destructiveHint": True})
    def mouse(
        action: Annotated[
            str,
            Field(
                description="One of: move, click, double_click, triple_click, "
                "right_click, middle_click, drag, scroll, hold, release. "
                "hold presses a button down and leaves it down (for free-form "
                "drag: hold, then move, then release); triple_click selects a "
                "whole line in most text widgets."
            ),
        ],
        x: Annotated[
            int | None,
            Field(description="Target X. Global desktop coordinate unless monitor is given."),
        ] = None,
        y: Annotated[int | None, Field(description="Target Y.")] = None,
        to_x: Annotated[
            int | None, Field(description="For drag: X where the drag ends.")
        ] = None,
        to_y: Annotated[
            int | None, Field(description="For drag: Y where the drag ends.")
        ] = None,
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
        smooth: Annotated[
            bool | None,
            Field(
                description="Override how the pointer travels for this call. The "
                "server default glides through intermediate points so the motion "
                "looks natural; pass false to jump straight to the target, which is "
                "faster but skips hover and drag-start events some applications need."
            ),
        ] = None,
        monitor: Annotated[
            int | None,
            Field(
                description="Treat x/y as coordinates inside this monitor instead of "
                "the whole desktop. Monitors are numbered left to right starting at 1."
            ),
        ] = None,
        force: Annotated[
            bool,
            Field(
                description="Send even if the user was recently active at the machine. "
                "Only set this when the user explicitly asked you to take over."
            ),
        ] = False,
    ) -> str:
        """Move the mouse pointer, click, drag, scroll or hold a button down on the
        user's Linux desktop. Requires desktop_unlock first. Use when the user asks
        you to press a button, open a menu or otherwise operate a graphical
        application. Coordinates are global desktop pixels unless you pass monitor.
        The pointer glides to its target rather than teleporting, so a move takes a
        moment. For a drag that needs stops along the way — a slider, a selection
        rectangle, a file onto a folder — use hold, then move, then release; the
        drag action is the single-shot version. Take a screenshot or check the
        result after acting — never click blind."""
        err = _guard("mouse", write=True, force=force)
        if err:
            return err

        act = (action or "").strip().lower()
        needs_xy = ("move", "click", "double_click", "triple_click", "right_click",
                    "middle_click", "drag")
        clicks = {"click": 1, "double_click": 2, "triple_click": 3,
                  "right_click": 1, "middle_click": 1}
        try:
            if act in needs_xy:
                if x is None or y is None:
                    return "x ve y zorunlu (drag icin ayrica to_x/to_y)."
                gx, gy = monitorslib.to_global(x, y, monitor)
            if act == "move":
                pos = backend.move(gx, gy, smooth=smooth)
                done = f"imlec {pos} konumuna tasindi"
            elif act in clicks:
                pos = backend.move(gx, gy, smooth=smooth)
                time.sleep(0.08)
                btn = {"right_click": "right", "middle_click": "middle"}.get(act, "left")
                backend.click(btn, clicks[act])
                kind = {2: " (cift)", 3: " (uclu)"}.get(clicks[act], "")
                done = f"{pos} konumuna {btn} tiklama{kind}"
            elif act == "drag":
                if to_x is None or to_y is None:
                    return "drag icin to_x ve to_y zorunlu."
                ex, ey = monitorslib.to_global(to_x, to_y, monitor)
                backend.drag(gx, gy, ex, ey, button=button)
                done = f"({gx}, {gy}) -> ({ex}, {ey}) {button} ile suruklendi"
            elif act == "scroll":
                if x is not None and y is not None:
                    backend.move(*monitorslib.to_global(x, y, monitor), smooth=smooth)
                    time.sleep(0.08)
                backend.scroll(scroll_amount, horizontal=horizontal)
                yon = "yatay" if horizontal else "dikey"
                done = f"{scroll_amount} tik {yon} kaydirildi"
            elif act == "hold":
                backend.mouse_down(button)
                done = (
                    f"{button} dugmesi BASILI TUTULUYOR — imleci tasiyip "
                    f"`release` ile birakin"
                )
            elif act == "release":
                backend.mouse_up(button)
                done = f"{button} dugmesi birakildi"
            else:
                return (
                    f"Bilinmeyen eylem: '{action}'. Gecerli: move, click, "
                    "double_click, triple_click, right_click, middle_click, drag, "
                    "scroll, hold, release"
                )
        except (inputlib.InputError, monitorslib.MonitorError) as exc:
            gate.audit("mouse_error", action=act, error=str(exc)[:160])
            return f"Hata: {exc}"

        gate.audit(
            "mouse", action=act, x=x, y=y, monitor=monitor,
            button=button if act in ("hold", "release", "drag") else None,
            forced=force or None,
        )
        where = backend.position
        note = ""
        if where:
            m = monitorslib.find_monitor(*where)
            if m:
                note = f" · monitor {m.index} ({m.connector})"
        return (
            f"{done}{note}.\nSonucu dogrulamadan bir sonraki adima gecmeyin."
            + _held_note()
        )

    @mcp.tool(annotations={"title": "Type text or press keys", "destructiveHint": True})
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
        force: Annotated[
            bool,
            Field(description="Send even if the user was recently active at the machine."),
        ] = False,
    ) -> str:
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
        that is damage control, not a substitute for releasing."""
        err = _guard("keyboard", write=True, force=force)
        if err:
            return err

        act = (action or "").strip().lower()
        try:
            if act == "type":
                if not text:
                    return "type icin `text` zorunlu."
                note = backend.type_text(
                    text, raw=raw, restore_clipboard=cfg.desktop.restore_clipboard
                )
                done = note
            elif act in ("key", "hold", "release"):
                if not keys:
                    return f"{act} icin `keys` zorunlu (ornek: 'ctrl+v')."
                if act == "key":
                    backend.key(keys)
                elif act == "hold":
                    backend.key_down(keys)
                else:
                    backend.key_up(keys)
                done = f"`{keys}` {'basildi' if act == 'key' else act}"
            else:
                return f"Bilinmeyen eylem: '{action}'. Gecerli: type, key, hold, release"
        except inputlib.InputError as exc:
            gate.audit("keyboard_error", action=act, error=str(exc)[:160])
            return f"Hata: {exc}"

        gate.audit(
            "keyboard",
            action=act,
            keys=keys,
            chars=len(text) if text else None,
            raw=raw or None,
            forced=force or None,
        )
        return (
            f"{done}.\nSonucu dogrulamadan bir sonraki adima gecmeyin."
            + _held_note()
        )

    # ------------------------------------------------------- ekran goruntusu
    @mcp.tool(annotations={"title": "Describe the screens", "readOnlyHint": True})
    def screen_info() -> str:
        """Describe the monitors: how many there are, their resolution, where each
        one sits in the shared coordinate space, and which one is primary. Use this
        before clicking or capturing anything, so you know which coordinates land on
        which screen. Contains no personal data, only the hardware layout."""
        lines = [monitorslib.describe(), ""]
        cap_ok, cap_why = capturelib.available()
        lines.append(
            f"**Ekran goruntusu:** {'hazir' if cap_ok else 'KULLANILAMIYOR'} "
            f"(`{capturelib.backend_name()}`)" + ("" if cap_ok else f" — {cap_why}")
        )
        in_ok, in_why = backend.available()
        lines.append(
            f"**Klavye/fare:** {'hazir' if in_ok else 'KULLANILAMIYOR'}"
            + ("" if in_ok else f" — {in_why}")
        )
        ui_ok, ui_why = uitreelib.available()
        ui_line = f"**Erisilebilirlik agaci:** {'hazir' if ui_ok else 'KULLANILAMIYOR'}"
        if ui_ok:
            # Pencere listesi ve odak yalnizca buradan okunabiliyor: C
            # bolumunde olculdu, Shell.Introspect "Access denied" veriyor.
            # `windows()` agaci gezmedigi icin `dump`tan ucuz (olculdu: 42 ms).
            try:
                wins = tree.windows()
                focused = next((w for w in wins if w.active), None)
                ui_line += f" · {len(wins)} pencere"
                if focused:
                    ui_line += f", odakta: {focused.label}"
            except uitreelib.UiTreeError as exc:
                ui_line += f" · pencere listesi okunamadi ({exc})"
        else:
            ui_line += f" — {ui_why}"
        lines.append(ui_line)
        lines.append(f"**Izin:** {gate.status_line()}")
        lines.append("")
        lines.append(
            "Koordinatlar **global tuval uzayinda**: sol ust (0, 0). Bir monitore "
            "ozel koordinat veriyorsaniz `monitor` parametresini de verin, ofseti "
            "pcbridge ekler."
        )
        return "\n".join(lines)

    @mcp.tool(annotations={"title": "Take a screenshot", "readOnlyHint": True})
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
    ) -> list[ContentBlock]:
        """Take a screenshot of the user's screen. Use when the user asks what is on
        their screen, and before clicking somewhere, to check what is actually
        there. If your client can display images you get the picture itself and can
        look at it; otherwise you get a short-lived link the user can open on their
        phone. Either way the reply tells you each image's position in the global
        coordinate space, so you can convert a spot in the picture into coordinates
        for the `mouse` tool. For GTK applications prefer `ui_dump` — it is cheaper
        and cannot miss, because it does not use coordinates at all."""
        # write=False: ekran goruntusu bir YAZMA eylemi degil, o yuzden "yakinda
        # klavye kullanildi" korumasina takilmiyor -- makinenin basinda olmaniz
        # ekraniniza bakmanizi engellememeli. Izin penceresi ve ekran kilidi
        # kontrolu ise aynen gecerli: goruntu en gizlilik-hassas cikti.
        denied = _guard("screen_capture", write=False, needs_input=False)
        if denied:
            return _text(denied)
        if shot_store is None:
            return _text(
                "⛔ Ekran goruntusu servisi kurulu degil (sunucu eski surumde?)."
            )

        cap_ok, cap_why = capturelib.available()
        if not cap_ok:
            gate.audit("screen_capture_unavailable", reason=cap_why[:120])
            return _text(f"⛔ Ekran goruntusu alinamiyor: {cap_why}")

        spec: int | str = monitor.strip() if isinstance(monitor, str) else monitor
        if isinstance(spec, str) and spec.isdigit():
            spec = int(spec)
        long_edge = (
            cfg.desktop.screenshot_scale_long_edge if scale is None else max(0, scale)
        )
        pointer = (
            cfg.desktop.include_pointer if include_pointer is None else include_pointer
        )

        try:
            shots = capturelib.capture(
                spec,
                out_dir=shot_store.dir,
                scale_long_edge=long_edge,
                include_pointer=pointer,
            )
        except (capturelib.CaptureError, monitorslib.MonitorError) as exc:
            gate.audit("screen_capture_error", error=str(exc)[:160])
            return _text(f"Hata: {exc}")

        # stdio'da HTTP sunucusu YOK -> /shot/<token>.png rotasi da yok. Orada
        # baglanti uretmek sessizce olu bir URL vermek olurdu; onun yerine
        # diskteki yol soyleniyor (istemci dosyayi kendi okuyabilir).
        links = transport != "stdio"
        ttl_min = max(1, cfg.desktop.shot_ttl_seconds // 60)
        out: list[str] = []
        for shot in shots:
            if links:
                # Token denetim kaydina YAZILMAZ: audit.log'u okuyabilen birinin
                # goruntuyu de acabilmesi anlamsiz bir yetki genislemesi olurdu.
                _token, where = shot_store.publish(shot.path)
            else:
                where = str(shot.path)
            if shot.offset is None:
                out.append(
                    f"**{shot.label}** · {shot.scaled[0]}x{shot.scaled[1]}\n"
                    f"  {where}\n"
                    "  ⚠️ Bu goruntu odaktaki pencere; ekranin neresinde oldugu "
                    "bilinmiyor, buradan koordinat turetmeyin."
                )
            else:
                out.append(
                    f"**{shot.label}** · {shot.size[0]}x{shot.size[1]} "
                    f"@ ({shot.offset[0]}, {shot.offset[1]}) → "
                    f"{shot.scaled[0]}x{shot.scaled[1]} (olcek {shot.scale:.3f})\n"
                    f"  {where}"
                )

        gate.audit("screen_capture", monitor=str(monitor), shots=len(shots),
                   inline=inline_images or None)

        out.append("")
        if links:
            out.append(f"Baglantilar {ttl_min} dakika gecerli, sonra kapaniyor.")
        else:
            out.append(
                "Yollar diskteki dosyalari gosteriyor (stdio'da HTTP sunucusu "
                "yok, bu yuzden baglanti uretilemiyor)."
            )
        if not inline_images:
            # SESSIZ BOSLUK YOK: goruntu blogu gelmiyorsa sebebi soylensin,
            # yoksa istemci "goruntu geldi ama ben goremedim" sanir.
            out.append(
                "Goruntu blogu KAPALI (`inline_images`); yalnizca yukaridaki "
                "yol/baglanti donuyor."
            )
        if any(s.offset is not None for s in shots):
            out.append(
                "Goruntudeki bir noktayi tiklamak icin once global koordinata "
                "cevirin:  `global_x = ofset_x + goruntu_x / olcek`  "
                "(y icin de ayni). Sonra `mouse` aracina **global** koordinati "
                "verin, `monitor` parametresi olmadan."
            )
            if any(s.scale < 1.0 for s in shots):
                # Olculdu: tam cozunurlukte gidis-donus sapmasi 1 px, 1280'e
                # kucultulmusde ~5 px. Buton icin sorun degil, ama modelin
                # koordinati birebir sanmamasi lazim.
                out.append(
                    "Goruntu kucultuldugu icin geri cevrilen koordinat birkac "
                    "piksel sapabilir (olculdu: ~5 px). Buton/menu icin yeterli; "
                    "daha keskin gerekiyorsa `scale=0` ile tam cozunurlukte alin."
                )

        # METIN BLOGU HER ZAMAN ILK SIRADA ve her zaman var. Monitor numarasi,
        # global ofset ve donusum kurali goruntuyle BIRLIKTE gitmeli; yoksa
        # istemci ikinci monitore 1920 piksel sasarak tiklar ve hata hicbir
        # yerde gorunmez.
        blocks: list[ContentBlock] = [_text("\n".join(out))]
        if inline_images:
            for shot in shots:
                try:
                    blocks.append(Image(path=shot.path).to_image_content())
                except OSError as exc:
                    # Dosya okunamadi: metin zaten yolu soyluyor, sessizce
                    # atlamak yerine sebebi de soyle.
                    blocks.append(
                        _text(f"({shot.label}: goruntu okunamadi — {exc})")
                    )
        return blocks

    # ------------------------------------------------- erisilebilirlik agaci
    # Ekranin metinsel ikizi. Model goruntuyu goremedigi icin asil "goz" burasi;
    # tiklama da koordinatla degil dugumun kendi Action'iyla yapiliyor.
    # Hicbiri uinput kullanmaz -> needs_input=False.
    @mcp.tool(annotations={"title": "Read the screen as text", "readOnlyHint": True})
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
    ) -> str:
        """List what is on screen as text: every button, menu, text box and label
        the application publishes, each with a short id. Use this instead of a
        screenshot when you need to know what is there — you can read this, and you
        cannot read images. Then act on an item with `ui_click` or `ui_set_text`
        using its id. Prefer this over clicking coordinates: it is exact, while
        coordinates are guesswork."""
        denied = _guard("ui_dump", write=False, needs_input=False)
        if denied:
            return denied
        ok, why = uitreelib.available()
        if not ok:
            gate.audit("ui_dump_unavailable", reason=why[:120])
            return f"⛔ Erisilebilirlik agaci okunamiyor: {why}"
        try:
            dump = tree.dump(target=target, interactive_only=interactive_only)
        except uitreelib.UiTreeError as exc:
            gate.audit("ui_dump_error", error=str(exc)[:160])
            return f"Hata: {exc}"
        gate.audit("ui_dump", target=target, nodes=len(dump.nodes))
        return jobslib.tail_chars(uitreelib.describe(dump), MAX_INLINE)

    @mcp.tool(annotations={"title": "Click something on screen", "destructiveHint": True})
    def ui_click(
        id: Annotated[
            str,
            Field(description="Id of the item from `ui_dump`, e.g. '#90e6'."),
        ],
        force: Annotated[
            bool,
            Field(description="Go ahead even if the user just used the machine."),
        ] = False,
    ) -> str:
        """Click a button, menu item or link by the id `ui_dump` gave it. This asks
        the application to activate that item directly, so it works regardless of
        where the window sits or what has focus. Use it whenever the thing you want
        appears in `ui_dump`; fall back to the `mouse` tool only for things the
        application does not publish, like canvases and games."""
        denied = _guard("ui_click", force=force, needs_input=False)
        if denied:
            return denied
        try:
            res = tree.click(str(id))
        except uitreelib.UiTreeError as exc:
            gate.audit("ui_click_error", node=str(id)[:40], error=str(exc)[:160])
            return f"Hata: {exc}"
        gate.audit("ui_click", node=str(id)[:40], name=res.get("name", "")[:60],
                   forced=force or None)
        note = ""
        if res.get("resolved_by") == "search":
            # Yol tutmadi, dugum rol+etiketle bulundu. Kullanici bunu bilsin.
            note = " (arayuz degismis, dugum adiyla bulundu)"
        return (
            f"{res.get('role','?')} \"{res.get('name','')}\" tiklandi{note}.\n"
            "Sonucu dogrulamadan bir sonraki adima gecmeyin — ui_dump ile "
            "yeniden bakin."
        )

    @mcp.tool(annotations={"title": "Type into a text box", "destructiveHint": True})
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
    ) -> str:
        """Replace the contents of a text box directly, by the id `ui_dump` gave it.
        Prefer this over the `keyboard` tool for filling in fields: it writes into
        the widget itself instead of simulating keystrokes, so nothing depends on
        the keyboard layout and no other window can steal the text. Note it
        replaces what is already there rather than appending."""
        denied = _guard("ui_set_text", force=force, needs_input=False)
        if denied:
            return denied
        try:
            res = tree.set_text(str(id), text)
        except uitreelib.UiTreeError as exc:
            gate.audit("ui_set_text_error", node=str(id)[:40], error=str(exc)[:160])
            return f"Hata: {exc}"
        # Metnin KENDISI denetim kaydina yazilmaz; parola girilmis olabilir.
        gate.audit("ui_set_text", node=str(id)[:40], chars=len(text),
                   forced=force or None)
        return (
            f"{res.get('role','?')} icine {len(text)} karakter yazildi "
            f"(oncekiler silindi: {res.get('replaced_chars', 0)} karakter).\n"
            "Sonucu dogrulamadan bir sonraki adima gecmeyin."
        )

    # -------------------------------------------------------- pencere yonetimi
    @mcp.tool(annotations={"title": "List open windows", "readOnlyHint": True})
    def window_list() -> str:
        """List the windows that are currently open, marking which one has focus.
        Use this to find out what the user is working on, or to pick a window to
        bring forward with `window_focus`. Cheap compared to `ui_dump`: it does
        not read the contents of any window."""
        denied = _guard("window_list", write=False, needs_input=False)
        if denied:
            return denied
        ok, why = uitreelib.available()
        if not ok:
            gate.audit("window_list_unavailable", reason=why[:120])
            return f"⛔ Pencere listesi okunamiyor: {why}"
        try:
            wins = tree.windows()
        except uitreelib.UiTreeError as exc:
            gate.audit("window_list_error", error=str(exc)[:160])
            return f"Hata: {exc}"
        gate.audit("window_list", windows=len(wins))
        return uitreelib.describe_windows(wins)

    @mcp.tool(
        annotations={"title": "Bring a window to the front", "destructiveHint": True}
    )
    def window_focus(
        window: Annotated[
            str,
            Field(
                description="Application or window name, e.g. 'Text Editor' or "
                "'Firefox'. Names from `window_list` work best."
            ),
        ],
        force: Annotated[
            bool,
            Field(description="Go ahead even if the user just used the machine."),
        ] = False,
    ) -> str:
        """Bring an application's window to the front so the next keystrokes go
        there. Takes a few seconds because it goes through the desktop's own
        search. If you only need to press a button or fill a field, prefer
        `ui_click` / `ui_set_text` — those reach the widget directly and do not
        require the window to be in front at all."""
        denied = _guard("window_focus", write=True, force=force)
        if denied:
            return denied
        try:
            note = appslib.focus(str(window), backend, tree.focused_window)
        except appslib.AppError as exc:
            gate.audit("window_focus_error", target=str(window)[:60],
                       error=str(exc)[:160])
            return f"Hata: {exc}"
        gate.audit("window_focus", target=str(window)[:60], forced=force or None)
        return note

    # ------------------------------------------------------------ toplu eylem
    # `DeviceOps` artik `desktop/ops.py`'de: ayni uygulamayi `bin/pcb-do`
    # kabugu da kullaniyor (F bolumu, yerel gorsel ajan). Burada bir kopya
    # dursaydi iki davranis zamanla ayrisirdi.
    batch_ops = opslib.DeviceOps(backend, tree, cfg)

    @mcp.tool(
        annotations={"title": "Run several actions in one go", "destructiveHint": True}
    )
    def computer_batch(
        actions: Annotated[
            str,
            Field(
                description=(
                    'JSON array of actions, run in order. Each item is '
                    '{"a": "<kind>", ...}. Kinds: key {keys}, type {text, raw?}, '
                    'wait {ms}, move/click/double_click/right_click/middle_click '
                    '{x?, y?, monitor?}, drag {x, y, to_x, to_y}, scroll {amount}, '
                    'ui_click {id}, ui_set_text {id, text}, launch {app}, '
                    'focus {window}. Example: '
                    '[{"a":"ui_click","id":"90e6"},{"a":"wait","ms":400},'
                    '{"a":"type","text":"hello"}]'
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
    ) -> list[ContentBlock]:
        """Run a whole sequence of desktop actions in a single call, then show you
        the result. Use this instead of calling `mouse`, `keyboard`, `ui_click` one
        at a time: each separate call asks the user for confirmation on their
        phone, so a five-step menu selection becomes five interruptions. Put the
        whole sequence here instead. Actions stop as soon as one fails, the time
        budget runs out, or a click moves focus to a different window — you get
        back what was done and what was left."""
        try:
            plan = batchlib.parse(actions, max_actions=cfg.desktop.batch_max_actions)
        except batchlib.BatchError as exc:
            # Kapidan ONCE: hicbir sey calistirilmiyor, yalnizca sozdizimi.
            return _text(f"⛔ {exc}")

        kinds = {a.a for a in plan}
        # Yalnizca erisilebilirlik eylemleri varsa /dev/uinput aranmaz --
        # C bolumunde duzeltilen ayni hata burada tekrarlanmasin.
        needs_input = bool(kinds & batchlib.INPUT_ACTIONS) or "focus" in kinds
        denied = _guard("computer_batch", write=True, force=force,
                        needs_input=needs_input)
        if denied:
            return _text(denied)

        gap = 0.0
        if cfg.desktop.max_actions_per_second > 0:
            # Hiz sinirina saygi: batch icindeki her eylem gate.check()'ten
            # gecseydi batch kendi kendini bogardi, onun yerine eylemler
            # arasina asgari bosluk konuyor.
            gap = 1.0 / cfg.desktop.max_actions_per_second

        gate.audit("computer_batch_start", count=len(plan),
                   kinds=",".join(sorted(kinds)), forced=force or None)
        # Cihazlari bastan ac: iki cihaz gerekiyorsa bekleme tek sefere iner
        # (olculdu 2,61 s -> 1,41 s). Gerekmiyorsa hicbir cihaz acilmaz.
        want_kbd, want_ptr = opslib.devices_needed(plan)
        if want_kbd or want_ptr:
            backend.ensure(keyboard=want_kbd, pointer=want_ptr)
        result = batchlib.run(
            plan,
            batch_ops,
            budget=float(cfg.desktop.batch_budget_seconds),
            min_gap=gap,
            check_focus=cfg.desktop.batch_check_focus,
            expect_focus=expect_focus or "",
        )
        for step in result.steps:
            # Metin ICERIGI yazilmaz -- `ui_set_text`teki kural aynen gecerli.
            gate.audit("batch_step", i=step.index, a=step.action,
                       ok=step.ok, ms=round(step.ms))
        gate.audit("computer_batch", done=result.done, total=result.total,
                   seconds=round(result.elapsed, 1), stopped=result.stopped or None)

        out = [batchlib.describe(result)]
        # `screen_capture` artik blok listesi donuyor: metin blogu rapora
        # katiliyor, goruntu bloklari SONA ekleniyor. Metnin ONCE gelmesi onemli
        # -- ofset ve olcek bilgisi goruntuden ayrilirsa koordinat hesabi
        # yapilamaz.
        images: list[ContentBlock] = []
        want = (final or "ui_dump").strip().lower()
        if want == "screen_capture":
            out.append("\n---")
            for block in screen_capture():
                if isinstance(block, TextContent):
                    out.append(block.text)
                else:
                    images.append(block)
        elif want != "none":
            out += ["", "---", ui_dump()]
        return [_text(jobslib.tail_chars("\n".join(out), MAX_INLINE)), *images]

    # ----------------------------------------------------- yerel gorsel ajan
    @mcp.tool(
        annotations={"title": "Let a local agent drive the screen",
                     "destructiveHint": True}
    )
    def computer_task(
        goal: Annotated[
            str,
            Field(
                description="What should end up being true on screen, in plain "
                "language. Be specific about the target: which app, which "
                "conversation, which file. Example: 'In Vesktop, open the DM "
                "with oneaura and send: hello'."
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
    ) -> str:
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

        skill = _SKILL_PATH
        if not skill.is_file():
            return (
                f"⛔ Gorsel ajan yonergesi yok: {skill}. Depodaki "
                "`skills/computer-use/SKILL.md` silinmis ya da tasinmis."
            )
        instructions = skill.read_text(encoding="utf-8")

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
            try:
                opened = appslib.launch(str(app))
                time.sleep(1.5)
                opened += " · " + appslib.focus(
                    str(app), backend, tree.focused_window
                )
            except appslib.AppError as exc:
                gate.audit("computer_task_app_error", app=str(app)[:60],
                           error=str(exc)[:160])
                return f"⛔ `{app}` hazirlanamadi: {exc}"

        steps = int(max_steps or spec.computer_task_max_steps)
        prompt = _task_prompt(instructions, str(goal), opened, steps)

        job_id = jm.start(
            kind=f"computer_task:{res.agent}",
            argv=[
                a.replace("{prompt}", prompt) if "{prompt}" in a else a
                for a in agent_spec.command
            ] + modelslib.build_args(agent_spec, res),
            cwd=cfg.default_workdir,
            label=jobslib._short(goal, 90),
            parser=agent_spec.parser,
            timeout=timeout,
            pty=agent_spec.pty,
            # `pcb-do` bunu gorup BOSTA kontrolunu atlar -- ve YALNIZCA onu.
            # Ekran kilidi, izin penceresi ve hiz siniri aynen isler.
            env={"PCBRIDGE_TASK_FORCE": "1"},
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
        # Hedef METNI kaydedilmez, uzunlugu kaydedilir: ekranda ne yapilacagi
        # ozel bilgi icerebilir. Tam metin jobs/<id>/meta.json'da.
        gate.audit("computer_task", agent=res.agent, model=res.model,
                   effort=res.effort, job=job_id, goal_chars=len(str(goal)),
                   app=str(app)[:60] if app else None, steps=steps,
                   forced=force or None)

        if wait_seconds > 0:
            jm.wait(job_id, wait_seconds)

        head = [
            f"**{job_id}** — gorsel ajan basladi ({res.headline()})",
            f"hedef: {_short(goal, 160)}",
        ]
        if opened:
            head.append(f"hazirlik: {opened}")
        head.append(
            f"adim butcesi: {steps} · `job_status(\"{job_id}\")` ile izleyin"
        )
        head.append(
            "Durdurmak icin: `desktop_lock` (ajanin elleri bir sonraki eylemde "
            f"durur) ya da `job_cancel(\"{job_id}\")`."
        )
        if wait_seconds > 0:
            return "\n".join(head) + "\n\n---\n" + _fmt_job_summary(cfg, jm, job_id)
        return "\n".join(head)

    # ================================================================== SISTEM
    @mcp.tool(annotations={"title": "Computer status", "readOnlyHint": True})
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
            "**Bilgisayar durumu**",
            "",
            f"- makine: {host}",
            f"- calisma suresi: {uptime}",
            f"- yuk: {load}",
            f"- bellek: {mem}",
            "",
            "**Diskler**",
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

        parts.append(f"\n**Masaustu:** {gate.status_line()}")

        running = jm.list_jobs(limit=10, only_running=True)
        parts.append(f"\n**Calisan isler:** {len(running)}")
        for r in running:
            parts.append(f"- `{r['job_id']}` {r['kind']} · {r['elapsed_seconds']}s · {r['label']}")

        if tmuxctl.available():
            try:
                sessions = tmuxctl.list_sessions()
                parts.append(f"\n**Acik terminaller:** {len(sessions)}")
                for s in sessions:
                    parts.append(f"- `{s['session']}` → {s['running']} ({s['path']})")
            except tmuxctl.TmuxError as exc:
                parts.append(f"\n**Acik terminaller:** okunamadi ({exc})")
        return "\n".join(parts)

    @mcp.tool(annotations={"title": "Show a desktop notification"})
    def notify(
        message: Annotated[str, Field(description="Notification body text.")],
        title: str = "Gemini",
    ) -> str:
        """Pop up a desktop notification on the user's computer screen. Useful to
        leave a note for when they get back to the machine."""
        try:
            subprocess.run(
                ["notify-send", "-a", "pcbridge", title, message],
                timeout=10,
                capture_output=True,
            )
            return "Bildirim gonderildi."
        except FileNotFoundError:
            return "notify-send bulunamadi: `sudo apt install libnotify-bin`"
        except Exception as exc:  # pragma: no cover
            return f"Bildirim gonderilemedi: {exc}"
