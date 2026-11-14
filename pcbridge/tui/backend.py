"""What the terminal UI does, in one replaceable object.

The UI calls only these methods, so the tests drive the real widgets with a
fake backend and never touch the machine's config, grant or daemon.
"""

from __future__ import annotations

import os
from typing import Any


class Backend:
    def load_config(self) -> Any:
        from ..config import load_config

        return load_config()

    def grant_state(self, cfg: Any) -> Any:
        from ..cli.grant import read_state

        return read_state(cfg)

    def lock(self, cfg: Any) -> str:
        from ..cli.grant import lock

        return lock(cfg)

    def unlock(self, cfg: Any, minutes: int | None) -> str:
        from ..cli.grant import unlock

        return unlock(cfg, minutes, "opened in the pcbridge terminal UI", granted_by="pcbridge ui")

    def status(self) -> dict:
        """`pcbridge status` as data, plus what the Overview shows beside it."""
        from ..cli.ops import runs_installed_code, status_data

        info = status_data()
        daemon = info.get("daemon") or {}
        if daemon.get("pid"):
            info["daemon_started"] = process_started(int(daemon["pid"]))
            info["daemon_current"] = runs_installed_code(daemon)
        return info

    def remote_apps(self, cfg: Any) -> int:
        """How many remote apps hold an unexpired token."""
        from .. import remoteclients as rc

        return len(rc.clients(cfg.db_path)) if cfg is not None else 0

    def doctor(self, only: tuple[str, str] | None = None) -> list[dict]:
        """Every doctor check; with `only`, that one check is repaired first."""
        from dataclasses import asdict

        from ..cli.doctor import Doctor

        doc = Doctor(fix=only is not None, only={only} if only else None)
        return [asdict(c) for c in doc.run_all()]

    def jobs(self, cfg: Any, limit: int = 40, only_running: bool = False) -> list[dict]:
        """Running and recent jobs, newest first, with the agent fields of meta.json."""
        from ..jobs import JobManager

        jm = JobManager(cfg.jobs_dir)
        out = []
        for j in jm.list_jobs(limit=limit, only_running=only_running):
            try:
                meta = jm.status(j["job_id"])
            except Exception:  # noqa: BLE001 - a record being written
                continue
            out.append({"id": j["job_id"], "kind": j["kind"], "label": j["label"],
                        "status": meta.get("status"), "exit_code": meta.get("exit_code"),
                        "agent": meta.get("agent") or "", "model": meta.get("model") or "",
                        "effort": meta.get("effort") or "", "cwd": meta.get("cwd") or "",
                        "started": meta.get("started_at") or 0,
                        "elapsed": meta.get("elapsed_seconds") or 0})
        return out

    def job_detail(self, cfg: Any, job_id: str, tail: int = 4000) -> dict:
        """One job: its prompt, the tail of its output and its session id."""
        from .. import jobs as jobslib

        jm = jobslib.JobManager(cfg.jobs_dir)
        st = jm.status(job_id)
        log = jm.read_log(job_id)
        parsed = jobslib.summarize(log, st.get("parser", "plain"))
        clean = jobslib.strip_ansi(log)
        return {"id": job_id, "status": st.get("status"), "exit_code": st.get("exit_code"),
                "prompt": st.get("prompt") or st.get("label") or st.get("command") or "",
                "cwd": st.get("cwd") or "", "started": st.get("started_at") or 0,
                "elapsed": st.get("elapsed_seconds") or 0, "agent": st.get("agent") or "",
                "model": st.get("model") or "", "session_id": parsed.get("session_id") or "",
                "output": clean, "tail": clean[-tail:]}

    def cancel_job(self, cfg: Any, job_id: str) -> str:
        from ..jobs import JobManager

        msg = JobManager(cfg.jobs_dir).cancel(job_id)
        from ..auditing import append

        append(cfg, "job_cancel", job=job_id, client="pcbridge ui")
        return msg

    def audit_page(self, cfg: Any, skip: int, count: int, keep: Any = None) -> tuple[list, bool]:
        from . import auditlog

        return auditlog.page(cfg.audit_log, skip, count, keep)

    def native(self, cfg: Any) -> dict:
        return native_info(cfg)

    def audit_totals(self, cfg: Any) -> Any:
        from . import auditlog

        return auditlog.totals(cfg.audit_log)

    def restart(self, cfg: Any) -> str:
        from ..cli.ops import _restart_when_idle

        return _restart_when_idle(cfg, 0).replace("`pcbridge update`", "`pcbridge restart`")

    def editor(self) -> Any:
        from ..settings import ConfigEditor

        return ConfigEditor()

    def panel_icon_status(self) -> tuple[str | None, str]:
        """The GNOME icon mode and any reason it cannot take effect yet."""
        from ..desktop import panelicon, session

        unsupported = session.support_note()
        if unsupported:
            return None, unsupported
        kind = session.desktop_kind()
        if kind == session.KDE:
            return None, "KDE Plasma has no pcbridge panel icon."
        if kind == session.HYPRLAND:
            return None, ("Hyprland has no pcbridge GNOME panel icon. No panel or tray is required; "
                          "the native glow signals granted desktop control.")
        if kind != session.GNOME:
            return None, "No GNOME session was detected."
        try:
            mode = panelicon.get_mode()
        except panelicon.PanelIconError as exc:
            return None, str(exc)
        version = panelicon.running_version()
        if version is None:
            note = "The extension is not running in this session; the setting applies when it runs."
        elif not panelicon.running_supports_mode(version):
            note = f"The running extension is version {version}; the setting applies after the next login."
        else:
            note = "Changes apply immediately and persist across logins."
        return mode, note

    def set_panel_icon_mode(self, mode: str) -> tuple[str | None, str]:
        from ..desktop import panelicon, session

        unsupported = session.support_note()
        if unsupported:
            raise panelicon.PanelIconError(unsupported)
        if session.desktop_kind() != session.GNOME:
            raise panelicon.PanelIconError("This session has no pcbridge GNOME panel icon.")
        panelicon.set_mode(mode)
        return self.panel_icon_status()

    def tools(self, cfg: Any) -> list:
        from ..toolcatalog import build

        return build(cfg)

    def clients(self) -> list[dict]:
        """Every MCP client pcbridge can connect, with its state."""
        from ..cli import connect as c
        from ..cli import install as inst

        cmd = inst.client_command()
        out = []
        for client in c.ALL_CLIENTS:
            st, reg = c.state(client, cmd)
            out.append({"client": client, "name": c.NAMES[client], "state": st,
                        "command": reg.command if reg.present else None, "config": reg.source,
                        "note": reg.note, "setup_default": client in c.CLIENTS})
        return out

    def set_connection(self, client: str, connect: bool) -> tuple[str, str]:
        """Connect or disconnect one client; (ok|warn|skip, what happened)."""
        from ..cli import connect as c
        from ..cli import install as inst

        backup = inst.Backup()
        results = c.connect([client], backup) if connect else c.disconnect([client], backup)
        _, status, detail = results[0]
        if backup.entries:
            backup.write_rollback()
            detail += f" (backup in {backup.root})"
        return status, detail

    def connection_preview(self, client: str, connect: bool) -> tuple[str, str]:
        """What connecting or disconnecting would write: (ok|plan|skip|warn, detail),
        from `connect --dry-run`; nothing is changed."""
        from ..cli import connect as c
        from ..cli import install as inst

        backup = inst.Backup()
        results = (c.connect if connect else c.disconnect)([client], backup, dry_run=True)
        _, status, detail = results[0]
        return status, detail

    def open_file(self, path: str) -> None:
        """Open a client's config file in the desktop's default app."""
        import subprocess

        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)

    def remote_clients(self, cfg: Any) -> dict:
        """The remote apps that hold tokens, the stale registrations, the tunnel
        and what the HTTP path offers."""
        import time

        from .. import remoteclients as rc
        from ..cli.ops import funnel_open

        if cfg is None:
            raise RuntimeError("the config does not load")
        groups = rc.clients(cfg.db_path)
        stale = {r.client_id for r in rc.stale(cfg.db_path)}
        remote = getattr(cfg, "remote_tools_profile", "same")
        counts = {}
        try:
            from ..tools import tools_in_profile

            for profile in ("full", "core", "desktop", "readonly"):
                counts[profile] = len(tools_in_profile(profile))
        except Exception:  # noqa: BLE001 - the counts are a detail
            pass
        return {
            "clients": [{"name": g.name, "connected": len(g.connected),
                         "registrations": len(g.registrations),
                         "stale": sum(1 for r in g.registrations if r.client_id in stale),
                         "expires": g.expires,
                         "last": time.strftime("%Y-%m-%d %H:%M", time.localtime(g.last_registered))}
                        for g in groups],
            "registrations": len(rc.registrations(cfg.db_path)),
            "stale": len(stale),
            "static_token": bool(getattr(cfg, "static_token", "")),
            "has_password": bool(getattr(cfg, "password", "")),
            "remote_profile": cfg.tools_profile if remote == "same" else remote,
            "remote_profile_setting": remote,
            "remote_profile_set": remote != "same",
            "profile_counts": counts,
            "url": getattr(cfg, "mcp_url", ""),
            "tunnel": funnel_open(cfg),
        }

    def set_tunnel(self, cfg: Any, on: bool) -> tuple[bool, str]:
        """Open or close Tailscale Funnel; (worked, what happened)."""
        from ..cli.ops import tunnel_start, tunnel_stop

        ok, lines = tunnel_start(cfg) if on else tunnel_stop(cfg)
        return ok, " ".join(lines)

    def cleanup_stale(self, cfg: Any) -> str:
        """Remove the registrations that hold no token and are 30 days old or more."""
        from .. import remoteclients as rc

        ids = [r.client_id for r in rc.stale(cfg.db_path)]
        if not ids:
            return "No stale registration."
        res = rc.revoke(cfg.db_path, ids, cfg.audit_log, cfg.audit_max_bytes)
        return f"Removed {res.registrations} stale registration(s); live sessions stay."

    def copy_text(self, text: str) -> bool:
        """Put text on the desktop clipboard with wl-copy; False if it is missing."""
        from ..remotepassword import copy_to_clipboard

        return copy_to_clipboard(text)

    def login_ok(self, password: str) -> bool:
        """Is this the user's login password (PAM)?"""
        from .. import remotepassword

        return remotepassword.login_ok(password)

    def set_remote_password(self, cfg: Any, password: str, login_password: str) -> Any:
        """Store the hash of a new remote password, after the login check; the SaveResult."""
        from .. import remotepassword

        return remotepassword.save(password, getattr(cfg, "source_path", None),
                                   login_password=login_password)

    def connected_remote(self, cfg: Any) -> int:
        from .. import remoteclients as rc

        return rc.connected_count(cfg.db_path) if cfg is not None else 0

    def revoke_remote(self, cfg: Any, name: str | None) -> str:
        """Revoke one app by name, or every one with None; what happened."""
        from .. import remoteclients as rc

        if cfg is None:
            raise RuntimeError("the config does not load")
        if name is None:
            res = rc.revoke_all(cfg.db_path, cfg.audit_log, cfg.audit_max_bytes)
        else:
            res = rc.revoke(cfg.db_path, [r.client_id for r in rc.select(cfg.db_path, name)],
                            cfg.audit_log, cfg.audit_max_bytes)
        return (f"Revoked {', '.join(res.names) or 'nothing'}: {res.registrations} registration(s), "
                f"{res.refresh_tokens} refresh token(s).")

    def update(self, cfg: Any) -> str:
        """Load the installed (for a git install: pulled) code into the daemon when idle."""
        from ..cli import install as inst
        from ..cli.ops import _restart_when_idle

        note = ""
        if inst.install_kind() == "git":
            res = inst.run(["git", "-C", str(inst.repo_root()), "pull", "--ff-only"], timeout=100)
            if res.returncode != 0:
                return "git pull failed: " + (res.stderr or res.stdout).strip()[:200]
            note = " (git pull: " + ((res.stdout.strip().splitlines() or ["done"])[-1]) + ")"
        inst.write_version_stamp()
        return _restart_when_idle(cfg, 0) + note


def process_started(pid: int) -> float | None:
    """When a process started (epoch seconds), from /proc; None if unknown."""
    try:
        stat = open(f"/proc/{pid}/stat", encoding="ascii").read()
        ticks = int(stat.rsplit(")", 1)[1].split()[19])
        boot = next(float(line.split()[1]) for line in open("/proc/stat", encoding="ascii")
                    if line.startswith("btime"))
    except (OSError, ValueError, IndexError, StopIteration):
        return None
    import os

    return boot + ticks / os.sysconf("SC_CLK_TCK")


def native_info(cfg: Any) -> dict:
    """The native helper's diagnostics, the session and the monitors (read-only)."""
    from ..desktop import monitors, session
    from ..native.diagnostics import diagnose

    findings = []
    for f in diagnose(cfg):
        line = f.line() if hasattr(f, "line") else str(f)
        level, _, text = line.partition("\t")
        findings.append((level, text or line))
    try:
        kind = session.desktop_kind()
        note = session.support_note()
        stype = os.environ.get("XDG_SESSION_TYPE", "") or ("wayland" if os.environ.get("WAYLAND_DISPLAY")
                                                            else "x11")
        session_text = f"{kind} · {stype}" + (f" · {note}" if note else "")
        session_ok = not note
    except Exception as exc:  # noqa: BLE001
        session_text, session_ok = str(exc), False
    try:
        mons = monitors.list_monitors()
        names = ", ".join(f"{m.connector} {m.width}×{m.height}" for m in mons)
        monitor_text = f"{len(mons)} · {names}"
    except Exception as exc:  # noqa: BLE001
        monitor_text = f"unknown ({exc})"[:80]
    return {"findings": findings, "session": session_text, "session_ok": session_ok,
            "monitors": monitor_text}
