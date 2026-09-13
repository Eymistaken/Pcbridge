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
from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult
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
    runtime: DesktopRuntime | None = None,
) -> DesktopRuntime:
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
    if runtime is None:
        runtime = create_runtime(cfg, gate=safetylib.SafetyGate(cfg))
    gate = runtime.gate

    # Bir kez hesaplanir: arac calisma anina kadar ne ayar ne tasima degisir.
    inline_images = _want_inline(cfg.inline_images, transport)

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
            f"\n⚠️ `{shot}` {int(age)} saniyelik (sinir {limit} sn). Aradan "
            "gecen surede pencereler degismis olabilir; tiklamadan once TAZE "
            "bir goruntu alin."
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
            logger.warning("gui_launch tespiti basarisiz: %s", exc)
            return None
        if not hit:
            return None
        gate.audit("shell_run_denied", reason="gui_launch", app=hit[:60])
        return (
            f"⛔ `{hit}` bir masaüstü uygulaması; kabuktan başlatılmıyor.\n"
            "Kabuktan açılan uygulama bu sunucunun çocuğu olur ve "
            "`systemctl --user restart pcbridge` onu kapatır; ayrıca çoğu "
            "zaman uygulama kimliği oluşmadığı için `window_list` ve "
            "`window_focus` pencereyi sonradan bulamaz.\n"
            f"Bunun yerine: window_focus(\"{hit}\")"
        )

    @mcp.tool(annotations={"title": "Run a shell command", "destructiveHint": True})
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
    backend = runtime.input_provider
    tree = runtime.accessibility_provider
    capture_provider = runtime.capture_provider

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
                    "⚠️ Ekran yayını açılamadı "
                    f"(monitör tablosu okunamadı: {exc})."
                )
            if cfg.desktop.capture_backend == "screencast":
                return (
                    f"⚠️ Ekran yayını açılamadı: {exc}\n"
                    "`capture_backend = \"screencast\"` olduğu için ekran "
                    "görüntüsü alınamayacak; `auto` yapılırsa gnome-screenshot'a "
                    "düşer (o flaş patlatır)."
                )
            return (
                f"⚠️ Ekran yayını açılamadı: {exc}\n"
                "Ekran görüntüsü gnome-screenshot ile alınacak — her çekimde "
                "beyaz flaş ve ses olur."
            )
        except monitorslib.MonitorError as exc:
            return f"⚠️ Ekran yayını açılamadı (monitör tablosu okunamadı: {exc})."
        except screencastlib.ScreenCastError as exc:
            if cfg.desktop.capture_backend == "screencast":
                return (
                    f"⚠️ Ekran yayını açılamadı: {exc}\n"
                    "`capture_backend = \"screencast\"` olduğu için ekran "
                    "görüntüsü alınamayacak; `auto` yapılırsa gnome-screenshot'a "
                    "düşer (o flaş patlatır)."
                )
            return (
                f"⚠️ Ekran yayını açılamadı: {exc}\n"
                "Ekran görüntüsü gnome-screenshot ile alınacak — her çekimde "
                "beyaz flaş ve ses olur."
            )
        return (
            "📷 Ekran yayını açık: görüntüler sessizce alınacak (flaş yok). "
            "Üst çubuktaki paylaşım göstergesi izin kapanınca kaybolur."
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
                f"⚠ {cfg.desktop.hold_max_seconds} sn dolduğu için kendiliğinden "
                f"bırakıldı: {', '.join(freed)}"
            )
        still = backend.held()
        if still:
            parts.append(f"basılı tutulan: {', '.join(still)}")
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
                    text=f"⛔ Sanal girdi cihazi kullanilamiyor: {why}",
                    message=why,
                    scope=input_scope,
                    backend_name="desktop.input",
                )
        return None

    @mcp.tool(
        output_schema=None,
        annotations={"title": "Desktop capabilities", "readOnlyHint": True},
    )
    def system_capabilities() -> ToolResult:
        """Report desktop backend capabilities and authorization independently.
        This probe is read-only and does not require desktop_unlock. Call it before
        choosing a desktop action or when a desktop tool reports an error."""
        return presentationlib.capabilities_result(runtime.capabilities())

    @mcp.tool(
        output_schema=None,
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
    ) -> str | ToolResult:
        """Open pcbridge's time-limited authorization grant for desktop tools.
        Operating-system permissions are separate and system_capabilities reports
        them independently. Call this before using a desktop tool when the user
        has authorized screen reading or control; the grant expires on its own."""
        if not cfg.desktop.enabled:
            text = (
                "⛔ Masaustu kontrolu kapali. config.toml'da `[desktop] enabled = true` "
                "yapip `systemctl --user restart pcbridge` calistirin. Once "
                "Pointer/keyboard kontrolu icin `sudo ./setup_uinput.sh` gerekiyor; "
                "yalnizca ekran okumak icin gerekmiyor."
            )
            error = DesktopError(
                code=ErrorCode.DESKTOP_DISABLED,
                message="Masaustu kontrolu kapali.",
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
                    "Masaüstü kontrolü açıldı",
                    f"{minutes} dakika · {reason or 'gerekçe belirtilmedi'}",
                ],
                capture_output=True,
                timeout=10,
            )
        out = [msg, "", capture_provider.describe_monitors(), ""]
        out.append(_open_screencast())
        out.append(
            "Koordinatlar **global tuval uzayinda**; sol ust (0, 0). Monitore ozel "
            "koordinat verecekseniz `monitor` parametresini de verin."
        )
        snapshot = runtime.capabilities(refresh=True)
        limitations = {
            name: value.as_dict()
            for name, value in sorted(snapshot.capabilities.items())
            if not value.usable_now or value.limitations
        }
        if limitations:
            out += ["", "**Kullanilamayan veya sinirli yetenekler**"]
            for name, value in limitations.items():
                detail = value["reason_code"] or value["state"]
                out.append(f"- `{name}`: {detail}")
        out.append("Erken kapatmak icin: desktop_lock")
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
        note = f"\n· bırakılan: {', '.join(freed)}" if freed else ""
        if yayin or others:
            note += "\n· ekran yayını kapatıldı (paylaşım göstergesi kayboldu)"
        if others:
            note += f" · {others} yardımcı süreç durduruldu"
        return message + note

    @mcp.tool(
        output_schema=None,
        annotations={"title": "Move or click the mouse", "destructiveHint": True},
    )
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
            Field(
                description="Target X. A global desktop coordinate, unless you "
                "pass shot (then it is the pixel you see in that screenshot) or "
                "monitor (then it is a full-resolution coordinate inside it)."
            ),
        ] = None,
        y: Annotated[int | None, Field(description="Target Y, in the same space as x.")] = None,
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
        never click blind."""
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
                gx, gy = _to_global(x, y, monitor, shot)
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
                ex, ey = _to_global(to_x, to_y, monitor, shot)
                backend.drag(gx, gy, ex, ey, button=button)
                done = f"({gx}, {gy}) -> ({ex}, {ey}) {button} ile suruklendi"
            elif act == "scroll":
                if x is not None and y is not None:
                    backend.move(*_to_global(x, y, monitor, shot), smooth=smooth)
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
        except (
            inputlib.InputError,
            monitorslib.MonitorError,
            capturelib.CaptureError,
            DesktopError,
        ) as exc:
            gate.audit("mouse_error", action=act, error=str(exc)[:160])
            return _exception_result(
                exc,
                text=f"Hata: {exc}",
                category=ErrorCategory.EXECUTION,
                scope="os.pointer",
                backend_name="desktop.input",
            )

        gate.audit(
            "mouse", action=act, x=x, y=y, monitor=monitor, shot=shot,
            button=button if act in ("hold", "release", "drag") else None,
            forced=force or None,
        )
        where = backend.position
        note = ""
        if where:
            m = capture_provider.find_monitor(*where)
            if m:
                note = f" · monitor {m.index} ({m.connector})"
        return (
            f"{done}{note}.\nSonucu dogrulamadan bir sonraki adima gecmeyin."
            + _stale_note(shot)
            + _held_note()
        )

    @mcp.tool(
        output_schema=None,
        annotations={"title": "Type text or press keys", "destructiveHint": True},
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
                if act in ("key", "hold"):
                    # Icerik kapisi: `force` bunu ACMAZ, ayri bir niyet beyani
                    # ister (KURALLAR.md sec. 4, madde 5).
                    policy.check_key_combo(keys, confirm_close=confirm_close)
                if act == "key":
                    backend.key(keys)
                elif act == "hold":
                    backend.key_down(keys)
                else:
                    backend.key_up(keys)
                done = f"`{keys}` {'basildi' if act == 'key' else act}"
            else:
                return f"Bilinmeyen eylem: '{action}'. Gecerli: type, key, hold, release"
        except (inputlib.InputError, DesktopError) as exc:
            gate.audit("keyboard_error", action=act, error=str(exc)[:160])
            return _exception_result(
                exc,
                text=f"Hata: {exc}",
                category=ErrorCategory.EXECUTION,
                scope="os.keyboard",
                backend_name="desktop.input",
            )

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
            f"{done}.\nSonucu dogrulamadan bir sonraki adima gecmeyin."
            + _held_note()
        )

    # ------------------------------------------------------- ekran goruntusu
    @mcp.tool(
        output_schema=None,
        annotations={"title": "Describe the screens", "readOnlyHint": True},
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
            f"**Ekran goruntusu:** {'hazir' if cap_ok else 'KULLANILAMIYOR'} "
            f"(`{capture_provider.backend_name()}`)"
            + ("" if cap_ok else f" — {cap_why}")
            + ("" if capture_provider.is_open() else
               " · yayın kapalı, çekimde flaş olur (`desktop_unlock` açar)")
        )
        in_ok, in_why = backend.available()
        lines.append(
            f"**Klavye/fare:** {'hazir' if in_ok else 'KULLANILAMIYOR'}"
            + ("" if in_ok else f" — {in_why}")
        )
        ui_ok, ui_why = tree.available()
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
            except (uitreelib.UiTreeError, DesktopError) as exc:
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

    @mcp.tool(
        output_schema=None,
        annotations={"title": "Take a screenshot", "readOnlyHint": True},
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
        cannot miss, because it does not use coordinates at all."""
        # write=False: ekran goruntusu bir YAZMA eylemi degil, o yuzden "yakinda
        # klavye kullanildi" korumasina takilmiyor -- makinenin basinda olmaniz
        # ekraniniza bakmanizi engellememeli. Izin penceresi ve ekran kilidi
        # kontrolu ise aynen gecerli: goruntu en gizlilik-hassas cikti.
        denied = _guard("screen_capture", write=False, needs_input=False)
        if denied:
            return denied
        if shot_store is None:
            text = "⛔ Ekran goruntusu servisi kurulu degil (sunucu eski surumde?)."
            error = DesktopError(
                code=ErrorCode.BACKEND_UNAVAILABLE,
                message="Ekran goruntusu servisi kurulu degil.",
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
                text=f"⛔ Ekran goruntusu alinamiyor: {cap_why}",
                message=cap_why,
                scope="os.capture",
                backend_name=capture_provider.backend_name(),
            )

        spec: int | str = monitor.strip() if isinstance(monitor, str) else monitor
        if isinstance(spec, str) and spec.isdigit():
            spec = int(spec)
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
            )
        except (capturelib.CaptureError, monitorslib.MonitorError, DesktopError) as exc:
            gate.audit("screen_capture_error", error=str(exc)[:160])
            return _exception_result(
                exc,
                text=f"Hata: {exc}",
                category=ErrorCategory.CAPTURE,
                scope="os.capture",
                backend_name=capture_provider.backend_name(),
            )

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
                    f"  shot: `{shot.id}`\n"
                    f"  {where}"
                )

        gate.audit("screen_capture", monitor=str(monitor), shots=len(shots),
                   swept=swept or None, inline=inline_images or None,
                   backend=capture_provider.backend_name())

        degraded = getattr(capture_provider, "degraded_reason", "")
        if degraded:
            # GORUNUR geri donus (Task 4.3): `auto` native yardimciyi
            # bulamadi ve kare Python yoluyla alindi. Sessiz kalsaydi "neden
            # yavas" ya da "neden farkli" sorusunun cevabi hicbir yerde olmazdi.
            out.append(
                f"⚠️ Native yakalama kullanılamadı ({degraded}); kare Python "
                "yoluyla alındı."
            )

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
        example = next((s for s in shots if s.offset is not None), None)
        if example is not None:
            # ARITMETIK YOK. Ofset ve olcegi sunucu uyguluyor; modelin tek isi
            # gordugu pikseli ve o goruntunun kimligini yazmak. Once boyle
            # degildi ve zayif modeller bolmeyi tutturamayip hedefin kenarina
            # tikliyordu.
            out.append(
                "Bu goruntudeki bir noktaya tiklamak icin koordinati **gordugunuz "
                "gibi** verin ve yanina o goruntunun kimligini ekleyin: "
                "`mouse(action=\"click\", x=…, y=…, shot=\"" + example.id + "\")` "
                "ya da toplu eylemde `{\"a\":\"click\",\"x\":…,\"y\":…,"
                "\"shot\":\"" + example.id + "\"}`. Ofseti ve olcegi pcbridge "
                "kendisi uyguluyor — siz cevirmeyin. (Yukaridaki ofset/olcek "
                "degerleri yalnizca bilgi icindir.)"
            )
            # Istemcinin kendi kuculttugu goruntuden koordinat cikarilamaz:
            # gordugunuz piksel ile kayitli olcek ayrisir ve `shot` hesabi
            # sessizce sasar. `scale=0` verildiginde tam da bu oluyor.
            for shot in shots:
                note = capture_provider.oversize_note(shot)
                if note:
                    out.append(note)
                    break
            if any(s.scale < 1.0 for s in shots):
                # Olculdu: tam cozunurlukte gidis-donus sapmasi 1 px, 1280'e
                # kucultulmusde ~5 px. Bu sapma DONUSUMDEN degil kucultmenin
                # kendisinden geliyor -- donusumu sunucunun yapmasi onu
                # ortadan kaldirmiyor, o yuzden uyari duruyor.
                out.append(
                    "Goruntu kucultuldugu icin hedefiniz birkac piksel sapabilir "
                    "(olculdu: ~5 px) — bu kucultmenin kendisinden, hesaptan "
                    "degil. Buton/menu icin yeterli; daha keskin gerekiyorsa "
                    "`scale=0` ile tam cozunurlukte alin."
                )

        # Goruntu bloklari metinden ONCE hazirlaniyor ama metnin ARKASINA
        # diziliyor: teslim edilemeyen bir goruntu metinde yazmali.
        images: list[ContentBlock] = []
        delivered: list[Any] = []
        undelivered: list[DesktopError] = []
        if inline_images:
            for shot in shots:
                try:
                    images.append(presentationlib.shot_image(shot))
                    delivered.append(shot)
                except DesktopError as exc:
                    undelivered.append(exc)
        if len(delivered) > 1:
            # Kimlik metinde, goruntu ayri blokta: eslesme SIRAYLA.
            out.append(
                "Goruntuler asagida bu sirayla: "
                + ", ".join(f"`{shot.id or shot.label}`" for shot in delivered)
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
                "⛔ Goruntu istemciye ULASTIRILAMADI: "
                f"{error.message}. Cekim alindi ama bu cagriyi basarili "
                "saymayin: bu goruntuden koordinat cikarmayin, yeni bir cekim "
                "alin."
            )
            return presentationlib.desktop_error_result(
                error,
                content=[_text("\n".join(out)), *images],
                extra={"shots": [shot.id for shot in shots]},
            )

        # METIN BLOGU HER ZAMAN ILK SIRADA ve her zaman var. Monitor numarasi,
        # global ofset ve donusum kurali goruntuyle BIRLIKTE gitmeli; yoksa
        # istemci ikinci monitore 1920 piksel sasarak tiklar ve hata hicbir
        # yerde gorunmez.
        return [_text("\n".join(out)), *images]

    # ------------------------------------------------- erisilebilirlik agaci
    # Ekranin metinsel ikizi. Model goruntuyu goremedigi icin asil "goz" burasi;
    # tiklama da koordinatla degil dugumun kendi Action'iyla yapiliyor.
    # Hicbiri uinput kullanmaz -> needs_input=False.
    @mcp.tool(
        output_schema=None,
        annotations={"title": "Read the screen as text", "readOnlyHint": True},
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
                text=f"⛔ Erisilebilirlik agaci okunamiyor: {why}",
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
                text=f"Hata: {exc}",
                category=ErrorCategory.ACCESSIBILITY,
                scope="os.accessibility",
                backend_name="desktop.accessibility",
            )
        gate.audit("ui_dump", target=target, nodes=len(dump.nodes))
        return jobslib.tail_chars(tree.describe_dump(dump), MAX_INLINE)

    @mcp.tool(
        output_schema=None,
        annotations={"title": "Click something on screen", "destructiveHint": True},
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
        you would only put the pointer somewhere the click is not happening."""
        denied = _guard("ui_click", force=force, needs_input=False)
        if denied:
            return denied
        try:
            res = tree.click(str(id))
        except (uitreelib.UiTreeError, DesktopError) as exc:
            gate.audit("ui_click_error", node=str(id)[:40], error=str(exc)[:160])
            return _exception_result(
                exc,
                text=f"Hata: {exc}",
                category=ErrorCategory.ACCESSIBILITY,
                scope="os.accessibility",
                backend_name="desktop.accessibility",
            )
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

    @mcp.tool(
        output_schema=None,
        annotations={"title": "Type into a text box", "destructiveHint": True},
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
        replaces what is already there rather than appending."""
        denied = _guard("ui_set_text", force=force, needs_input=False)
        if denied:
            return denied
        try:
            res = tree.set_text(str(id), text)
        except (uitreelib.UiTreeError, DesktopError) as exc:
            gate.audit("ui_set_text_error", node=str(id)[:40], error=str(exc)[:160])
            return _exception_result(
                exc,
                text=f"Hata: {exc}",
                category=ErrorCategory.ACCESSIBILITY,
                scope="os.accessibility",
                backend_name="desktop.accessibility",
            )
        # Metnin KENDISI denetim kaydina yazilmaz; parola girilmis olabilir.
        gate.audit("ui_set_text", node=str(id)[:40], chars=len(text),
                   forced=force or None)
        return (
            f"{res.get('role','?')} icine {len(text)} karakter yazildi "
            f"(oncekiler silindi: {res.get('replaced_chars', 0)} karakter).\n"
            "Sonucu dogrulamadan bir sonraki adima gecmeyin."
        )

    # -------------------------------------------------------- pencere yonetimi
    @mcp.tool(
        output_schema=None,
        annotations={"title": "List open windows", "readOnlyHint": True},
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
                text=f"⛔ Pencere listesi okunamiyor: {why}",
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
                text=f"Hata: {exc}",
                category=ErrorCategory.ACCESSIBILITY,
                scope="os.window",
                backend_name="desktop.accessibility",
            )
        gate.audit("window_list", windows=len(wins))
        return tree.describe_windows(wins)

    @mcp.tool(
        output_schema=None,
        annotations={"title": "Bring a window to the front", "destructiveHint": True}
    )
    def window_focus(
        window: Annotated[
            str,
            Field(
                description="Application or window name as a human would say it, "
                "e.g. 'Text Editor', 'Firefox', 'Vesktop'. The application does "
                "NOT have to be running. Already-open windows use the GNOME "
                "Shell extension when available; closed applications and "
                "systems without the extension use desktop search."
            ),
        ],
        force: Annotated[
            bool,
            Field(description="Go ahead even if the user just used the machine."),
        ] = False,
    ) -> str | ToolResult:
        """Open a graphical application and bring it to the front, launching it
        first if it is not already running. An available GNOME Shell extension
        activates an already-open window directly. If the extension is absent or
        the target is closed, the existing desktop-search path remains the
        fallback and verifies the result through the accessibility tree.

        Use this when the desktop must own the new process lifetime and keep the
        window discoverable across pcbridge restarts. Shell commands remain valid
        for deterministic work and for handing a request, such as a URL, to an
        application process that is already running.

        The direct path takes milliseconds; the search fallback takes a few
        seconds. If the app is already up and you only need to press a button or
        fill a field, prefer `ui_click` / `ui_set_text` — those reach the widget
        directly and do not require the window to be in front at all."""
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
        try:
            note = appslib.focus(str(window), backend, tree.focused_window)
        except (appslib.AppError, DesktopError) as exc:
            gate.audit("window_focus_error", target=str(window)[:60],
                       error=str(exc)[:160])
            return _exception_result(
                exc,
                text=f"Hata: {exc}",
                category=ErrorCategory.EXECUTION,
                scope="os.window",
                backend_name="desktop.window",
            )
        gate.audit("window_focus", target=str(window)[:60], forced=force or None)
        return note

    # ------------------------------------------------------------ toplu eylem
    # `DeviceOps` artik `desktop/ops.py`'de: ayni uygulamayi `bin/pcb-do`
    # kabugu da kullaniyor (F bolumu, yerel gorsel ajan). Burada bir kopya
    # dursaydi iki davranis zamanla ayrisirdi.
    batch_ops = opslib.DeviceOps(backend, tree, cfg, capture_provider)

    @mcp.tool(
        output_schema=None,
        annotations={"title": "Run several actions in one go", "destructiveHint": True}
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
                    'move/click/double_click/triple_click/right_click/middle_click '
                    '{x?, y?, shot?, monitor?}, mouse_down {button?, x?, y?, shot?}, '
                    'mouse_up {button?}, drag {x, y, to_x, to_y, button?, shot?}, '
                    'scroll {amount, horizontal?, shot?}, ui_click {id}, '
                    'ui_set_text {id, text}, launch {app}, focus {window}. '
                    'shot is the id of the screenshot you read the coordinates off '
                    "(screen_capture prints it, e.g. 'm2-a1b2c3'): pass it and give "
                    'x/y exactly as you see them in that picture, and the server '
                    'converts them for you. hold/mouse_down stay down across later '
                    'actions, so a drag with stops along the way is mouse_down, '
                    'move, move, mouse_up. '
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
        back what was done and what was left."""
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
        # Acik pencere icin eklenti yolu uinput kullanmaz. Servis yoksa eski
        # GNOME aramasinin klavye on kontrolu ve toplu cihaz acilisi aynen
        # korunur. Hedef bulunamazsa eklenti False dondurur; apps.focus yedek
        # arama icin klavyeyi o anda tembel olarak acar.
        want_kbd, want_ptr = opslib.devices_needed(
            plan,
            focus_uses_keyboard=not appslib.extension_focus_available(),
        )
        need_kbd = want_kbd
        needs_input = need_kbd or want_ptr
        input_capability = "input.pointer" if want_ptr else "input.keyboard"
        input_scope = "os.pointer" if want_ptr else "os.keyboard"
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
        # Cihazlari bastan ac: iki cihaz gerekiyorsa bekleme tek sefere iner
        # (olculdu 2,61 s -> 1,41 s). Gerekmiyorsa hicbir cihaz acilmaz.
        if need_kbd or want_ptr:
            try:
                backend.ensure(keyboard=need_kbd, pointer=want_ptr)
            except (inputlib.InputError, DesktopError) as exc:
                gate.audit("computer_batch_error", error=str(exc)[:160])
                return _exception_result(
                    exc,
                    text=f"Hata: {exc}",
                    category=ErrorCategory.EXECUTION,
                    scope=input_scope,
                    backend_name="desktop.input",
                    extra={
                        "batch": {"done": 0, "total": len(plan), "stopped": "error"}
                    },
                )
        result = batchlib.run(
            plan,
            batch_ops,
            budget=float(cfg.desktop.batch_budget_seconds),
            min_gap=gap,
            check_focus=cfg.desktop.batch_check_focus,
            expect_focus=expect_focus or "",
            repeat_limit=cfg.desktop.repeat_click_limit,
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
            if want_ptr:
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
            final_result = screen_capture()
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
                f"⛔ Gorsel ajan yonergesi yok: {skill}. Depodaki "
                "`skills/computer-use/SKILL.md` silinmis ya da tasinmis."
            )
            error = DesktopError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message=f"Gorsel ajan yonergesi yok: {skill}.",
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
                text=f"⛔ Gorsel ajan yonergesi okunamadi: {exc}",
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
            try:
                opened = appslib.prepare(
                    str(app), backend, tree.focused_window
                )
            except (appslib.AppError, DesktopError) as exc:
                gate.audit("computer_task_app_error", app=str(app)[:60],
                           error=str(exc)[:160])
                return _exception_result(
                    exc,
                    text=f"⛔ `{app}` hazirlanamadi: {exc}",
                    category=ErrorCategory.EXECUTION,
                    scope="os.window",
                    backend_name="desktop.window",
                )

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

    return runtime
