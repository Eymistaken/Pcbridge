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
from pydantic import Field

from . import jobs as jobslib
from . import models as modelslib
from . import tmuxctl
from .config import Config
from .desktop import input as inputlib
from .desktop import monitors as monitorslib
from .desktop import safety as safetylib

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


def register(mcp: FastMCP, cfg: Config, jm: jobslib.JobManager) -> None:
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
            return jm.cancel(job_id)
        except KeyError as exc:
            return str(exc)

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
            return tmuxctl.kill(session)
        except tmuxctl.TmuxError as exc:
            return f"Hata: {exc}"

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
            return (
                f"`{command}` {limit} saniyede bitmedi ve iptal edildi. "
                "Uzun surecekse shell_run_background kullan."
            )
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
    gate = safetylib.SafetyGate(cfg)
    backend = inputlib.InputBackend()

    def _guard(tool: str, write: bool = True, force: bool = False) -> str | None:
        """Reddedildiyse kullaniciya donecek Turkce gerekce, izinliyse None."""
        decision = gate.check(tool, write=write, force=force)
        if not decision.allowed:
            gate.audit(f"{tool}_denied", reason=decision.reason[:120])
            return f"⛔ {decision.reason}"
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
        it to expire, and destroy the virtual keyboard/mouse devices. Use when the
        user says they are done, or asks you to stop touching their screen."""
        backend.close()
        return gate.lock()

    @mcp.tool(annotations={"title": "Move or click the mouse", "destructiveHint": True})
    def mouse(
        action: Annotated[
            str,
            Field(
                description="One of: move, click, double_click, right_click, "
                "middle_click, drag, scroll."
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
        """Move the mouse pointer, click, drag or scroll on the user's Linux
        desktop. Requires desktop_unlock first. Use when the user asks you to
        press a button, open a menu or otherwise operate a graphical application.
        Coordinates are global desktop pixels unless you pass monitor. Take a
        screenshot or check the result after acting — never click blind."""
        err = _guard("mouse", write=True, force=force)
        if err:
            return err

        act = (action or "").strip().lower()
        try:
            if act in ("move", "click", "double_click", "right_click", "middle_click", "drag"):
                if x is None or y is None:
                    return "x ve y zorunlu (drag icin ayrica to_x/to_y)."
                gx, gy = monitorslib.to_global(x, y, monitor)
            if act == "move":
                pos = backend.move(gx, gy)
                done = f"imlec {pos} konumuna tasindi"
            elif act in ("click", "double_click", "right_click", "middle_click"):
                pos = backend.move(gx, gy)
                time.sleep(0.08)
                button = {"right_click": "right", "middle_click": "middle"}.get(act, "left")
                backend.click(button, 2 if act == "double_click" else 1)
                done = f"{pos} konumuna {button} tiklama" + (
                    " (cift)" if act == "double_click" else ""
                )
            elif act == "drag":
                if to_x is None or to_y is None:
                    return "drag icin to_x ve to_y zorunlu."
                ex, ey = monitorslib.to_global(to_x, to_y, monitor)
                backend.drag(gx, gy, ex, ey)
                done = f"({gx}, {gy}) -> ({ex}, {ey}) suruklendi"
            elif act == "scroll":
                if x is not None and y is not None:
                    backend.move(*monitorslib.to_global(x, y, monitor))
                    time.sleep(0.08)
                backend.scroll(scroll_amount)
                done = f"{scroll_amount} tik kaydirildi"
            else:
                return (
                    f"Bilinmeyen eylem: '{action}'. Gecerli: move, click, "
                    "double_click, right_click, middle_click, drag, scroll"
                )
        except (inputlib.InputError, monitorslib.MonitorError) as exc:
            gate.audit("mouse_error", action=act, error=str(exc)[:160])
            return f"Hata: {exc}"

        gate.audit("mouse", action=act, x=x, y=y, monitor=monitor, forced=force or None)
        where = backend.position
        note = ""
        if where:
            m = monitorslib.find_monitor(*where)
            if m:
                note = f" · monitor {m.index} ({m.connector})"
        return f"{done}{note}.\nSonucu dogrulamadan bir sonraki adima gecmeyin."

    @mcp.tool(annotations={"title": "Type text or press keys", "destructiveHint": True})
    def keyboard(
        action: Annotated[
            str, Field(description="One of: type, key, hold, release.")
        ],
        text: Annotated[
            str | None, Field(description="For type: the text to enter.")
        ] = None,
        keys: Annotated[
            str | None,
            Field(
                description="For key/hold/release: a combination like 'ctrl+v', "
                "'super', 'alt+tab', 'Return', 'Escape', 'f5', 'down'."
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
        correctly; the previous clipboard contents are restored afterwards."""
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
        return f"{done}.\nSonucu dogrulamadan bir sonraki adima gecmeyin."

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
