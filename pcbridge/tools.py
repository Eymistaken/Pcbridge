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
from . import tmuxctl
from .config import Config

MAX_INLINE = 4000


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


def _fmt_job_summary(cfg: Config, jm: jobslib.JobManager, job_id: str) -> str:
    st = jm.status(job_id)
    log = jm.read_log(job_id)
    parsed = jobslib.summarize(log, st.get("parser", "plain"))

    lines = [
        f"**{job_id}** — durum: `{st['status']}`"
        + (f" (exit {st['exit_code']})" if st.get("exit_code") is not None else ""),
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
    return "\n".join(lines)


def register(mcp: FastMCP, cfg: Config, jm: jobslib.JobManager) -> None:
    # ================================================================= AJANLAR
    @mcp.tool(
        annotations={"title": "List available coding agents"},
    )
    def list_agents() -> str:
        """List the coding agents installed on the computer (Claude Code,
        Antigravity CLI, ...) and whether their executables are found on PATH.
        Call this first if you are unsure which agent name to use."""
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
        out.append("")
        out.append(f"varsayilan calisma dizini: `{cfg.default_workdir}`")
        return "\n".join(out)

    @mcp.tool(
        annotations={"title": "Send a prompt to a coding agent", "destructiveHint": True},
    )
    def agent_run(
        agent: Annotated[
            str, Field(description="Agent name, e.g. 'claude' or 'antigravity'.")
        ],
        prompt: Annotated[
            str, Field(description="The instruction to send to the agent.")
        ],
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
        tasks. Returns a job id; poll it with job_status. This can modify files
        and run commands on the machine."""
        spec = cfg.agents.get(agent)
        if not spec or not spec.enabled:
            names = ", ".join(k for k, v in cfg.agents.items() if v.enabled) or "-"
            return f"'{agent}' tanimli degil. Kullanilabilir ajanlar: {names}"

        argv = [
            a.replace("{prompt}", prompt) if "{prompt}" in a else a
            for a in spec.command
        ]
        if resume_session and spec.resume_args:
            argv += [a.replace("{session_id}", resume_session) for a in spec.resume_args]

        cwd = _resolve_dir(cfg, workdir)
        if not cwd.is_dir():
            return f"Dizin yok: {cwd}"

        job_id = jm.start(
            kind=f"agent:{agent}",
            argv=argv,
            cwd=cwd,
            label=jobslib._short(prompt, 90),
            parser=spec.parser,
            timeout=timeout,
            pty=spec.pty,
            extra={"agent": agent, "prompt": prompt, "resume_session": resume_session},
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
