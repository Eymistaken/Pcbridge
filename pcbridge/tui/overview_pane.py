"""The Overview tab: the live logo, the service, health, jobs and activity.

Under the logo two columns (SERVICE beside HEALTH), then two more (JOBS
beside RECENT ACTIVITY); at 80 columns everything stacks in one. Health is
`pcbridge doctor` in a worker; a selected warning shows its explanation and,
where doctor can repair it, Fix this check (that one check only).
"""

from __future__ import annotations

import time
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, OptionList, Static
from textual.widgets.option_list import Option

from .. import __version__
from . import auditlog
from .logo import WIDE, Logo
from .pane import ScrollPane, while_mounted
from .theme import PALETTE, SPINNER
from .widgets import SectionTitle, key_label, set_kind

CSS = """
/* Each part is centered on its own full-width row: Textual's align centers
   a container's children as one block, so a wider reason box or state line
   pushed the logo to the left of that block. */
OverviewPane #logo-area {
    height: auto;
}
OverviewPane #logo-row, OverviewPane #logo-reason-row {
    height: auto;
    width: 100%;
    align-horizontal: center;
}
OverviewPane #logo-side {
    width: auto;
    height: auto;
    padding: 2 0 0 2;
}
OverviewPane #logo-line {
    width: 100%;
    height: 1;
    text-align: center;
}
OverviewPane #logo-note {
    width: auto;
    max-width: 50;
    color: $pb-dim;
}
OverviewPane #logo-reason {
    width: 84;
    max-width: 100%;
    height: auto;
    border: round $pb-rule;
    padding: 0 1;
    display: none;
}
OverviewPane #logo-reason.shown {
    display: block;
}
OverviewPane #logo-reason-text {
    width: 1fr;
}
OverviewPane .two {
    height: auto;
}
OverviewPane #col-service {
    width: 15fr;
    height: auto;
}
OverviewPane #col-health {
    width: 13fr;
    height: auto;
    margin-left: 4;
}
OverviewPane #col-jobs {
    width: 15fr;
    height: auto;
}
OverviewPane #col-activity {
    width: 13fr;
    height: auto;
    margin-left: 4;
}
OverviewPane.narrow .two {
    layout: vertical;
}
OverviewPane.narrow #col-service, OverviewPane.narrow #col-health,
OverviewPane.narrow #col-jobs, OverviewPane.narrow #col-activity {
    width: 1fr;
    margin-left: 0;
}
OverviewPane .svc-row {
    height: 1;
}
OverviewPane .svc-label {
    width: 12;
    color: $pb-muted;
}
OverviewPane .svc-value {
    width: 1fr;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}
OverviewPane .svc-row Button {
    margin-right: 0;
    margin-left: 1;
}
OverviewPane #health-counts-row {
    height: 1;
}
OverviewPane #health-counts {
    width: 1fr;
}
OverviewPane #health-bar {
    height: 1;
    margin-bottom: 1;
}
OverviewPane #health-list {
    text-wrap: nowrap;
    text-overflow: ellipsis;
    height: auto;
    max-height: 8;
    border: none;
    background: ansi_default;
    padding: 0;
}
OverviewPane #health-list > .option-list--option-highlighted {
    background: $pb-selection;
}
OverviewPane #health-detail {
    height: auto;
    border-left: outer $pb-rule;
    padding-left: 1;
    margin-left: 2;
    margin-top: 1;
}
OverviewPane #health-fix-row {
    height: 1;
    margin-top: 1;
}
OverviewPane #health-fix-command {
    width: 1fr;
    color: $pb-dim;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}
OverviewPane #health-more {
    display: none;
}
OverviewPane #overview-jobs, OverviewPane #overview-activity {
    height: auto;
    max-height: 8;
}
"""


def ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} s ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h ago"


def elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"


def uptime(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {rest // 60} min"
    return f"{rest // 60} min"


def short_id(job_id: str) -> str:
    """`20261114-021133-7f3a9c` -> `7f3a`."""
    return job_id.rsplit("-", 1)[-1][:4]


def job_agent(job: dict) -> str:
    return job.get("agent") or job.get("kind", "").removeprefix("agent:")


class OverviewPane(ScrollPane):
    FOOTER = [("l", "Lock/Unlock", "app.toggle_grant"), ("r", "Restart", "app.restart"),
              ("u", "Update", "app.update"), ("f", "Fix", "key:f")]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.checks: list[dict] = []
        self.listed: list[dict] = []      # the warning rows, in the order shown
        self.checked_at = 0.0
        self.doctor_busy = False
        self.fix_busy = False
        self.show_all_warnings = False
        self.jobs: list[dict] = []
        self.recent: list[auditlog.Entry] = []
        self.state = "idle"
        self.state_entry: auditlog.Entry | None = None
        self.clients_summary = ""
        self.remote_apps: int | None = None
        self._spin = 0

    # -- layout ---------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="logo-area"):
            with Horizontal(id="logo-row"):
                yield Logo("word", id="logo")
                with Vertical(id="logo-side"):
                    yield Static("", id="logo-line-side")
                    yield Static("", id="logo-note")
            yield Static("", id="logo-line")
            with Horizontal(id="logo-reason-row"):
                with Horizontal(id="logo-reason"):
                    yield Static("", id="logo-reason-text")
                    yield Button("Activity ›", id="logo-activity", classes="link", compact=True)
        with Horizontal(classes="two"):
            with Vertical(id="col-service"):
                yield SectionTitle("Service")
                with Horizontal(classes="svc-row"):
                    yield Static("Daemon", classes="svc-label")
                    yield Static("…", id="svc-daemon", classes="svc-value")
                    yield Button(key_label("Restart", "r"), id="svc-restart", compact=True)
                with Horizontal(classes="svc-row", id="svc-update-row"):
                    yield Static("", classes="svc-label")
                    yield Static("", id="svc-update", classes="svc-value warn")
                    yield Button(key_label("Update", "u"), id="svc-update-button", compact=True)
                with Horizontal(classes="svc-row"):
                    yield Static("Desktop", classes="svc-label")
                    yield Static("…", id="svc-desktop", classes="svc-value")
                    yield Button("Unlock ▾", id="svc-grant", compact=True)
                with Horizontal(classes="svc-row"):
                    yield Static("Remote", classes="svc-label")
                    yield Static("…", id="svc-remote", classes="svc-value")
                    yield Button("Remote ›", id="svc-remote-link", classes="link", compact=True)
                for key, label in (("keep", "Keep awake"), ("tools", "Tools"), ("clients", "Clients"),
                                   ("config", "Config")):
                    with Horizontal(classes="svc-row"):
                        yield Static(label, classes="svc-label")
                        yield Static("…", id=f"svc-{key}", classes="svc-value")
            with Vertical(id="col-health"):
                yield SectionTitle("Health", "pcbridge doctor", id="health-title")
                with Horizontal(id="health-counts-row"):
                    yield Static("Running pcbridge doctor…", id="health-counts")
                    yield Button("Run again", id="health-run", compact=True)
                yield Static("", id="health-bar")
                yield OptionList(id="health-list")
                yield Button("", id="health-more", classes="link", compact=True)
                with Vertical(id="health-detail"):
                    yield Static("", id="health-explain")
                    with Horizontal(id="health-fix-row"):
                        yield Button(key_label("Fix this check", "f"), id="health-fix",
                                     classes="primary", compact=True)
                        yield Static("", id="health-fix-command")
        with Horizontal(classes="two"):
            with Vertical(id="col-jobs"):
                yield SectionTitle("Jobs", id="jobs-title")
                yield DataTable(id="overview-jobs", cursor_type="row", show_cursor=True)
                yield Button("Agents & Jobs ›", id="jobs-link", classes="link", compact=True)
            with Vertical(id="col-activity"):
                yield SectionTitle("Recent activity")
                yield DataTable(id="overview-activity", cursor_type="row")

    def on_mount(self) -> None:
        jobs = self.query_one("#overview-jobs", DataTable)
        jobs.add_columns("ID", "AGENT", "PROMPT", "TIME")
        activity = self.query_one("#overview-activity", DataTable)
        activity.add_columns("TIME", "CLIENT", "ACTION")
        self.query_one("#health-detail").display = False
        self.query_one("#svc-update-row").display = False
        self.query_one("#logo", Logo).base = getattr(self.app, "terminal_background", None) or \
            self.query_one("#logo", Logo).base
        self._layout_for(self.app.size.width)
        self.run_doctor()
        self.load_clients()
        self.set_interval(2.0, self.poll_activity)
        self.set_interval(1.0, self._tick)
        self.poll_activity()

    def on_resize(self) -> None:
        self._layout_for(self.app.size.width)

    def _layout_for(self, width: int) -> None:
        wide = width >= WIDE
        logo = self.query_one("#logo", Logo)
        logo.set_glyph("word" if wide else "icon")
        self.query_one("#logo-side").display = not wide
        self.query_one("#logo-line").display = wide
        self.set_class(width < self.app_narrow(), "narrow")
        self._render_logo_line()
        self._render_health()

    def app_narrow(self) -> int:
        from .app import NARROW

        return NARROW

    # -- the logo -------------------------------------------------------------------

    def _logo_line(self) -> Text:
        out = Text()
        if self.state == "active":
            running = [j for j in self.jobs if j.get("status") == "running"]
            if running:
                j = running[0]
                out.append(f"● agent_run · {job_agent(j)} · {elapsed(j.get('elapsed', 0))}",
                           style=PALETTE["working"])
            elif self.state_entry is not None:
                e = self.state_entry
                out.append(f"● {e.tool} · {e.client or 'local'} · now", style=PALETTE["working"])
            else:
                out.append("● working", style=PALETTE["working"])
        elif self.state == "error" and self.state_entry is not None:
            e = self.state_entry
            out.append(f"✕ {e.tool} {e.result} · {ago(time.time() - e.when)}", style=PALETTE["error"])
        else:
            out.append("idle · listening, nothing running", style=PALETTE["muted"])
        return out

    @while_mounted
    def _render_logo_line(self) -> None:
        wide = self.app.size.width >= WIDE
        line = self._logo_line()
        if wide:
            line.append(" · click the logo for the reason", style=PALETTE["dim"])
            self.query_one("#logo-line", Static).update(line)
        else:
            self.query_one("#logo-line-side", Static).update(line)
            self.query_one("#logo-note", Static).update(
                f"{__version__} · below {WIDE} columns the wordmark gives way to the icon\n"
                "click the icon for the reason")

    def _reason(self) -> Text:
        heads = {"idle": ("IDLE", "muted"), "active": ("ACTIVE", "working"),
                 "error": ("LAST ERROR", "error")}
        head, token = heads[self.state]
        out = Text()
        out.append(f"{head}  ", style=f"bold {PALETTE[token]}")
        if self.state == "error" and self.state_entry is not None:
            e = self.state_entry
            verb = "was refused" if e.result == "refused" else "failed"
            why = e.what()
            out.append(f"{e.tool} from {e.client or 'a local client'} {verb} at {e.ts[11:19]}"
                       + (f": {why}." if why else "."))
        elif self.state == "active":
            running = [j for j in self.jobs if j.get("status") == "running"]
            parts = [f"agent_run {job_agent(j)} ({short_id(j['id'])}, {elapsed(j.get('elapsed', 0))})"
                     for j in running]
            calls = 0
            if self.state_entry is not None and time.time() - self.state_entry.when <= 3:
                calls = 1
                parts.append(f"{self.state_entry.tool} from {self.state_entry.client or 'local'} now")
            counts = []
            if running:
                counts.append(f"{len(running)} job{'s' if len(running) != 1 else ''}")
            if calls:
                counts.append("1 tool call")
            out.append(f"{' and '.join(counts)}: {', '.join(parts)}." if parts else "Working.")
        else:
            e = self.state_entry
            if e is not None:
                out.append(f"Nothing is running. The last call was {e.tool} from "
                           f"{e.client or 'a local client'}, {ago(time.time() - e.when)}.")
            else:
                out.append("Nothing is running, and no call is recorded yet.")
        return out

    @on(Logo.Clicked)
    def _logo_clicked(self) -> None:
        box = self.query_one("#logo-reason")
        box.toggle_class("shown")
        self.query_one("#logo-reason-text", Static).update(self._reason())

    @on(Button.Pressed, "#logo-activity")
    def _to_activity(self) -> None:
        self.app.action_show_tab("activity")

    def set_state(self, state: str, entry: auditlog.Entry | None) -> None:
        self.state, self.state_entry = state, entry
        self.query_one("#logo", Logo).set_state(state)
        self._render_logo_line()
        if self.query_one("#logo-reason").has_class("shown"):
            self.query_one("#logo-reason-text", Static).update(self._reason())

    # -- polling --------------------------------------------------------------------

    def poll_activity(self) -> None:
        cfg = getattr(self.app, "cfg", None)
        if cfg is not None:
            self._activity_worker(cfg)

    @work(thread=True, exclusive=True, group="overview-activity")
    def _activity_worker(self, cfg: Any) -> None:
        try:
            entries, _more = self.app.backend.audit_page(cfg, 0, 40)
        except Exception:  # noqa: BLE001 - the overview must not fail on this
            entries = []
        try:
            jobs = self.app.backend.jobs(cfg, limit=8)
        except Exception:  # noqa: BLE001
            jobs = []
        self.app.post(self._activity_loaded, entries, jobs)

    @while_mounted
    def _activity_loaded(self, entries: list, jobs: list[dict]) -> None:
        self.recent = entries
        self.jobs = jobs
        running = [j for j in jobs if j.get("status") == "running"]
        state, entry = auditlog.logo_state(entries, len(running))
        self.set_state(state, entry)
        self._render_jobs()
        self._render_activity()

    def _tick(self) -> None:
        self._spin = (self._spin + 1) % len(SPINNER)
        if any(j.get("status") == "running" for j in self.jobs):
            for j in self.jobs:
                if j.get("status") == "running":
                    j["elapsed"] = j.get("elapsed", 0) + 1
            self._render_jobs()
            self._render_logo_line()
        if self.checked_at:
            self.query_one("#health-title", SectionTitle).set(
                extra=f"pcbridge doctor · {ago(time.time() - self.checked_at)}")

    @while_mounted
    def _render_jobs(self) -> None:
        table = self.query_one("#overview-jobs", DataTable)
        table.clear()
        running = [j for j in self.jobs if j.get("status") == "running"]
        self.query_one("#jobs-title", SectionTitle).set(
            extra=f"{len(running)} running" if running else "none running")
        width = 24 if self.has_class("narrow") else 30
        for j in running[:5]:
            prompt = " ".join((j.get("label") or "").split())
            table.add_row(Text(f"{SPINNER[self._spin]} {short_id(j['id'])}", style=PALETTE["working"]),
                          job_agent(j), prompt[:width], elapsed(j.get("elapsed", 0)), key=j["id"])
        if not running:
            table.add_row(Text("No job is running.", style=PALETTE["dim"]), "", "", "")

    @while_mounted
    def _render_activity(self) -> None:
        table = self.query_one("#overview-activity", DataTable)
        table.clear()
        shown = [e for e in self.recent if e.kind != "remote" or e.client][:3] or self.recent[:3]
        for i, e in enumerate(shown):
            color = PALETTE["error"] if e.failed else PALETTE["text"]
            table.add_row(e.ts[11:19], (e.client or "local")[:16],
                          Text(f"{e.event} {e.what()}"[:40], style=color), key=str(i))
        if not shown:
            table.add_row(Text("Nothing recorded yet.", style=PALETTE["dim"]), "", "")

    @on(DataTable.RowSelected, "#overview-jobs")
    def _job_selected(self, event: DataTable.RowSelected) -> None:
        job = event.row_key.value if event.row_key else None
        self.app.action_show_tab("jobs")
        pane = self.app.panes.get("jobs")
        if job and pane is not None and hasattr(pane, "select_job"):
            pane.select_job(str(job))

    @on(DataTable.RowSelected, "#overview-activity")
    def _activity_selected(self) -> None:
        self.app.action_show_tab("activity")

    @on(Button.Pressed, "#jobs-link")
    def _jobs_link(self) -> None:
        self.app.action_show_tab("jobs")

    # -- service ----------------------------------------------------------------------

    def status_changed(self, info: dict) -> None:
        self._render_service(info)
        if self.remote_apps is None and getattr(self.app, "cfg", None) is not None:
            self._remote_worker(self.app.cfg)

    def config_changed(self) -> None:
        self._render_service(self.app.status_info)

    def grant_changed(self, grant: Any) -> None:
        self._render_desktop(grant)

    @work(thread=True, exclusive=True, group="overview-remote")
    def _remote_worker(self, cfg: Any) -> None:
        try:
            n = self.app.backend.remote_apps(cfg)
        except Exception:  # noqa: BLE001
            n = 0
        self.app.post(self._remote_loaded, n)

    @while_mounted
    def _remote_loaded(self, n: int) -> None:
        self.remote_apps = n
        self._render_service(self.app.status_info)

    @while_mounted
    def _render_service(self, info: dict) -> None:
        if not info:
            return
        cfg = getattr(self.app, "cfg", None)
        daemon = info.get("daemon") or {}
        running = bool(daemon) and daemon.get("reachable") is not False
        d = Text()
        if "error" in info:
            d.append(f"✕ status could not be read: {info['error']}", style=PALETTE["error"])
        elif running:
            d.append("● running", style=PALETTE["ok"])
            bits = [str(daemon.get("version") or "?"), f"pid {daemon.get('pid')}"]
            if info.get("daemon_started"):
                bits.append(f"up {uptime(time.time() - info['daemon_started'])}")
            d.append(" · " + " · ".join(bits), style=PALETTE["muted"])
        else:
            d.append("● not running", style=PALETTE["error"])
            d.append(f" · {daemon.get('why', 'no answer')}", style=PALETTE["muted"])
        self.query_one("#svc-daemon", Static).update(d)
        stale = running and info.get("daemon_current") is False
        self.query_one("#svc-update-row").display = stale
        if stale:
            self.query_one("#svc-update", Static).update(
                f"▲ {__version__} installed · loads when no job runs")
        remote = Text()
        if info.get("remote_tunnel") == "open":
            remote.append("◉ tunnel open", style=PALETTE["warning"])
        else:
            remote.append("○ tunnel closed", style=PALETTE["muted"])
        if self.remote_apps is not None:
            remote.append(f" · {self.remote_apps} app{'s' if self.remote_apps != 1 else ''} hold tokens"
                          if self.remote_apps != 1 else " · 1 app holds tokens", style=PALETTE["muted"])
        self.query_one("#svc-remote", Static).update(remote)
        if "keep_awake" in info:
            from ..cli.ops import describe_keep_awake

            fine, text = describe_keep_awake(info["keep_awake"])
            self.query_one("#svc-keep", Static).update(
                Text(text.split(" (`")[0], style=PALETTE["text" if fine else "warning"]))
        else:
            self.query_one("#svc-keep", Static).update(Text("unknown", style=PALETTE["dim"]))
        if "tools_offered" in info:
            profile, offered, total, *remote_bits = info["tools_offered"]
            text = f"{profile} · {offered} of {total} offered"
            if len(remote_bits) == 2:
                text += f" · remote: {remote_bits[0]}, {remote_bits[1]}"
            self.query_one("#svc-tools", Static).update(text)
        self.query_one("#svc-clients", Static).update(self.clients_summary or "…")
        self.query_one("#svc-config", Static).update(
            Text(str(cfg.source_path), style=PALETTE["dim"]) if cfg is not None
            else Text(self.app.cfg_error or "not loaded", style=PALETTE["error"]))
        self._render_desktop(getattr(self.app, "grant", None))

    @while_mounted
    def _render_desktop(self, grant: Any) -> None:
        value = self.query_one("#svc-desktop", Static)
        button = self.query_one("#svc-grant", Button)
        if grant is None:
            value.update(Text("config error", style=PALETTE["error"]))
            button.display = False
            return
        button.display = True
        if not grant.enabled_in_config:
            value.update(Text("◆ desktop control off in the config", style=PALETTE["muted"]))
            button.label, button.disabled = "Unlock ▾", True
            set_kind(button, "primary")
        elif grant.open:
            value.update(Text("◇ OPEN · agents may use keyboard, pointer, screen",
                              style=f"bold {PALETTE['warning']}"))
            button.label, button.disabled = key_label("Lock now", "l"), False
            set_kind(button, "danger")
        else:
            value.update(Text("◆ locked · desktop control enabled", style=PALETTE["muted"]))
            button.label, button.disabled = "Unlock ▾", False
            set_kind(button, "primary")

    @on(Button.Pressed, "#svc-grant")
    def _grant(self) -> None:
        grant = getattr(self.app, "grant", None)
        if grant is not None and grant.open:
            self.app.lock_now()
        else:
            self.app._grant_menu()

    @on(Button.Pressed, "#svc-restart")
    def _restart(self) -> None:
        self.app.action_restart()

    @on(Button.Pressed, "#svc-update-button")
    def _update(self) -> None:
        self.app.action_update()

    @on(Button.Pressed, "#svc-remote-link")
    def _remote(self) -> None:
        self.app.action_show_tab("remote")

    def press_u(self) -> None:
        self.app.action_update()

    def load_clients(self) -> None:
        self._clients_worker()

    @work(thread=True, exclusive=True, group="overview-clients")
    def _clients_worker(self) -> None:
        try:
            rows = self.app.backend.clients()
        except Exception:  # noqa: BLE001
            rows = []
        self.app.post(self.clients_loaded, rows)

    @while_mounted
    def clients_loaded(self, rows: list[dict]) -> None:
        on = [r["name"] for r in rows if r["state"] in ("connected", "outdated")]
        text = f"{len(on)} of {len(rows)} connected"
        if on:
            text += " · " + ", ".join(on)
        self.clients_summary = text if rows else "none found"
        self.query_one("#svc-clients", Static).update(self.clients_summary)

    # -- health -------------------------------------------------------------------------

    def run_doctor(self) -> None:
        if self.doctor_busy:
            return
        self.doctor_busy = True
        self.query_one("#health-run", Button).disabled = True
        if not self.checks:
            self.query_one("#health-counts", Static).update(Text("Running pcbridge doctor…",
                                                                 style=PALETTE["muted"]))
        self._doctor_worker(None)

    @on(Button.Pressed, "#health-run")
    def _run_again(self) -> None:
        self.run_doctor()

    @work(thread=True, exclusive=True, group="doctor")
    def _doctor_worker(self, only: tuple[str, str] | None) -> None:
        try:
            checks, error = self.app.backend.doctor(only), ""
        except Exception as exc:  # noqa: BLE001 - shown in the section
            checks, error = [], str(exc)
        self.app.post(self._doctor_done, checks, error, only)

    @while_mounted
    def _doctor_done(self, checks: list[dict], error: str, only: tuple[str, str] | None) -> None:
        self.doctor_busy = False
        self.fix_busy = False
        self.query_one("#health-run", Button).disabled = False
        if error:
            self.query_one("#health-counts", Static).update(
                Text(f"✕ doctor could not run: {error}", style=PALETTE["error"]))
            return
        self.checks = checks
        self.checked_at = time.time()
        self.query_one("#health-title", SectionTitle).set(extra="pcbridge doctor · 0 s ago")
        if only is not None:
            fixed = next((c for c in checks if (c["group"], c["name"]) == only), None)
            if fixed is not None and (fixed.get("fixed") or fixed["status"] in ("ok", "info")):
                self.app.notify(f"Fixed: {only[0]} · {only[1]}.")
            else:
                self.app.notify(f"{only[0]} · {only[1]} is not fixed yet; see the explanation.",
                                severity="warning")
        self._render_health()

    def problems(self) -> list[dict]:
        out = [c for c in self.checks if c["status"] in ("warn", "fail") and not c.get("fixed")]
        return sorted(out, key=lambda c: 0 if c["status"] == "fail" else 1)

    @while_mounted
    def _render_health(self) -> None:
        if not self.checks:
            return
        live = [c for c in self.checks if not c.get("fixed")]
        ok = sum(1 for c in live if c["status"] == "ok") + sum(1 for c in self.checks if c.get("fixed"))
        warn = sum(1 for c in live if c["status"] == "warn")
        fail = sum(1 for c in live if c["status"] == "fail")
        counts = Text()
        counts.append(f"✓ {ok} ok", style=PALETTE["ok"])
        counts.append("  ")
        counts.append(f"▲ {warn} warning{'s' if warn != 1 else ''}",
                      style=PALETTE["warning"] if warn else PALETTE["dim"])
        counts.append("  ")
        counts.append(f"✕ {fail} failed", style=PALETTE["error"] if fail else PALETTE["dim"])
        self.query_one("#health-counts", Static).update(counts)
        bar = Text()
        width = max(10, self.query_one("#col-health").size.width or 40)
        total = ok + warn + fail or 1
        for token, n in (("ok", ok), ("warning", warn), ("error", fail)):
            cells = round(n * min(1.0, width / total)) if total > width else n
            bar.append("█" * cells, style=PALETTE[token])
        self.query_one("#health-bar", Static).update(bar)
        problems = self.problems()
        menu = self.query_one("#health-list", OptionList)
        kept = self.selected_check()
        menu.clear_options()
        self.listed = []
        limit = 2 if self.has_class("narrow") and not self.show_all_warnings else len(problems)
        for c in problems[:limit]:
            line = Text()
            if c["status"] == "fail":
                line.append("✕ ", style=PALETTE["error"])
            else:
                line.append("▲ ", style=PALETTE["warning"])
            line.append(f"{c['group']:<9}", style=PALETTE["muted"])
            line.append(" " + c["detail"].splitlines()[0][:70])
            # By position: doctor gives several checks the same group and
            # name (each native helper finding is "native helper · native").
            menu.add_option(Option(line, id=f"check-{len(self.listed)}"))
            self.listed.append(c)
        more = self.query_one("#health-more", Button)
        hidden = len(problems) - limit
        more.display = hidden > 0
        more.label = f"+{hidden} more · Show all warnings ›"
        ids = [f"check-{i}" for i in range(len(self.listed))]
        if ids:
            same = [i for i, c in enumerate(self.listed) if kept is not None
                    and (c["group"], c["name"], c["detail"]) == (kept["group"], kept["name"], kept["detail"])]
            menu.highlighted = same[0] if same else 0
        menu.display = bool(ids)
        self._show_check()

    @on(Button.Pressed, "#health-more")
    def _more(self) -> None:
        self.show_all_warnings = True
        self._render_health()

    @on(OptionList.OptionHighlighted, "#health-list")
    def _highlighted(self) -> None:
        self._show_check()

    def selected_check(self) -> dict | None:
        menu = self.query_one("#health-list", OptionList)
        option = menu.highlighted_option
        if option is None or not option.id:
            return None
        index = int(option.id.removeprefix("check-"))
        listed = getattr(self, "listed", [])
        return listed[index] if index < len(listed) else None

    def _show_check(self) -> None:
        check = self.selected_check()
        detail = self.query_one("#health-detail")
        detail.display = check is not None
        if check is None:
            return
        explain = Text()
        explain.append(f"{check['group']} · {check['name']}\n", style="bold")
        explain.append(check["detail"], style=PALETTE["muted"])
        self.query_one("#health-explain", Static).update(explain)
        fix = self.query_one("#health-fix", Button)
        fix.display = bool(check.get("fixable"))
        fix.disabled = self.fix_busy
        fix.label = "Fixing…" if self.fix_busy else key_label("Fix this check", "f")
        command = self.query_one("#health-fix-command", Static)
        if check.get("fixable"):
            command.update("doctor --fix, this check only")
        elif check.get("fix"):
            command.update(Text(f"fix: {check['fix']}", style=PALETTE["dim"]))
        else:
            command.update("")

    @on(Button.Pressed, "#health-fix")
    def press_f(self) -> None:
        check = self.selected_check()
        if check is None or not check.get("fixable") or self.fix_busy or self.doctor_busy:
            return
        self.fix_busy = self.doctor_busy = True
        self.query_one("#health-fix", Button).disabled = True
        self.query_one("#health-fix", Button).label = "Fixing…"
        self._doctor_worker((check["group"], check["name"]))

    def shown(self) -> None:
        self.poll_activity()
