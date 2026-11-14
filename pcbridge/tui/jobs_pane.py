"""The Agents & Jobs tab: the running and recent jobs, one job's output, the
default agent and each agent's settings.

The jobs come from the job records (`<state_dir>/jobs`), read in a worker
every two seconds while the tab is shown. Cancel asks inline and keeps the
output so far; it is recorded in the audit log. The default agent and an
agent's "Offered to agent_run" switch save at once, like the Desktop tab.
"""

from __future__ import annotations

import time
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, OptionList, Static, Switch
from textual.widgets.option_list import Option

from .overview_pane import elapsed, job_agent, short_id
from .pane import Pane, while_mounted
from .theme import PALETTE, SPINNER
from .widgets import RestartBanner, SectionTitle, Segmented, key_label, tag

CSS = """
JobsPane #jobs-top {
    height: 1;
    margin-bottom: 1;
}
JobsPane #jobs-table {
    height: auto;
    max-height: 10;
}
JobsPane .two {
    height: 1fr;
    margin-top: 1;
}
JobsPane #job-detail {
    width: 3fr;
    height: 1fr;
}
JobsPane #agents-col {
    width: 2fr;
    height: 1fr;
    margin-left: 4;
}
JobsPane.narrow .two {
    layout: vertical;
    height: auto;
}
JobsPane.narrow #job-detail, JobsPane.narrow #agents-col {
    height: auto;
    margin-left: 0;
}
JobsPane .kv {
    height: auto;
}
JobsPane .kv .label {
    width: 9;
    color: $pb-muted;
}
JobsPane .kv .value {
    width: 1fr;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}
JobsPane #job-output {
    height: 1fr;
    min-height: 4;
    margin-top: 1;
    background: $pb-box;
    padding: 0 1;
}
JobsPane.narrow #job-output {
    height: 8;
}
JobsPane #job-actions, JobsPane #job-confirm {
    height: 1;
    margin-top: 1;
}
JobsPane #job-confirm {
    display: none;
}
JobsPane #job-confirm.shown {
    display: block;
}
JobsPane #job-confirm-text {
    width: auto;
    padding-right: 1;
    color: $pb-warning;
}
JobsPane #agents-list {
    height: auto;
    max-height: 6;
    border: none;
    background: ansi_default;
    padding: 0;
}
JobsPane #agents-list > .option-list--option-highlighted {
    background: $pb-selection;
}
JobsPane #agent-offered {
    height: auto;
    margin-top: 1;
}
JobsPane #agent-offered Switch {
    margin-right: 2;
}
"""

STATES = {
    "running": ("⠹ running", "working"),
    "finished": ("✓ done", "ok"),
    "failed": ("✕ failed", "error"),
    "timeout": ("✕ timed out", "error"),
    "vanished": ("⊘ vanished", "warning"),
    "cancelled": ("⊘ cancelled", "warning"),
}


def state_of(job: dict) -> str:
    if job.get("status") == "failed" and job.get("exit_code") == 130:
        return "cancelled"
    return str(job.get("status") or "")


def started(ts: float) -> str:
    if not ts:
        return ""
    day = time.strftime("%Y-%m-%d", time.localtime(ts))
    today = time.strftime("%Y-%m-%d")
    clock = time.strftime("%H:%M", time.localtime(ts))
    return f"today {clock}" if day == today else f"{day} {clock}"


class JobsPane(Pane):
    FOOTER = [("↑↓", "Job", "key:none"), ("c", "Cancel", "key:c"), ("o", "Output", "key:o"),
              ("d", "Default agent", "key:d")]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.jobs: list[dict] = []
        self.filter = "running"
        self.selected: str | None = None
        self.detail: dict | None = None
        self.agent: str | None = None
        self.quiet = False
        self._spin = 0
        self._timer: Any = None

    def compose(self) -> ComposeResult:
        yield RestartBanner()
        with Horizontal(id="jobs-top"):
            yield SectionTitle("Jobs", id="jobs-title", classes="first")
        yield Segmented([("running", "Running"), ("recent", "Recent"), ("all", "All")], id="jobs-filter")
        yield DataTable(id="jobs-table", cursor_type="row")
        with Horizontal(classes="two"):
            with Vertical(id="job-detail"):
                yield SectionTitle("Job", id="job-title", upper=True)
                for key, label in (("prompt", "Prompt"), ("folder", "Folder"), ("started", "Started"),
                                   ("session", "Session")):
                    with Horizontal(classes="kv"):
                        yield Static(label, classes="label")
                        yield Static("", id=f"job-{key}", classes="value")
                with VerticalScroll(id="job-output"):
                    yield Static("", id="job-tail")
                with Horizontal(id="job-actions"):
                    yield Button(key_label("Cancel", "c"), id="job-cancel", classes="danger", compact=True)
                    yield Button(key_label("Full output", "o"), id="job-full", compact=True)
                    yield Button("Copy session id", id="job-copy", compact=True)
                with Horizontal(id="job-confirm"):
                    yield Static("", id="job-confirm-text")
                    yield Button("Stop the job", id="job-stop", classes="danger", compact=True)
                    yield Button("Keep running", id="job-keep", compact=True)
            with Vertical(id="agents-col"):
                yield SectionTitle("Default agent")
                yield Segmented([], id="default-agent")
                yield Static("Used by agent_run when a call names neither an agent nor a model of a "
                             "known agent.", classes="muted")
                yield SectionTitle("Agents")
                yield OptionList(id="agents-list")
                with Horizontal(id="agent-offered"):
                    yield Switch(id="agent-enabled")
                    yield Static(Text("Offered to agent_run", style="bold"))
                yield Static("", id="agent-detail")
                yield Button("", id="agent-advanced", classes="link", compact=True)

    def on_mount(self) -> None:
        self.set_class(self.app.size.width < 100, "narrow")
        self.query_one("#jobs-table", DataTable).add_columns("ID", "AGENT", "MODEL", "PROMPT", "STATE",
                                                             "TIME")
        self.query_one("#job-confirm").display = False
        self.config_changed()
        self.poll()

    def on_resize(self) -> None:
        self.set_class(self.app.size.width < 100, "narrow")

    def shown(self) -> None:
        self.poll()
        if self._timer is None:
            self._timer = self.set_interval(2.0, self._tick)

    def _tick(self) -> None:
        if self.app.active_tab() != "jobs":
            return
        self._spin = (self._spin + 1) % len(SPINNER)
        self.poll()

    # -- jobs ---------------------------------------------------------------------

    def poll(self) -> None:
        cfg = getattr(self.app, "cfg", None)
        if cfg is not None:
            self._jobs_worker(cfg, self.selected)

    @work(thread=True, exclusive=True, group="jobs")
    def _jobs_worker(self, cfg: Any, selected: str | None) -> None:
        try:
            jobs = self.app.backend.jobs(cfg, limit=40)
        except Exception:  # noqa: BLE001 - the tab shows the last list
            jobs = []
        if selected is None and jobs:
            running = [j for j in jobs if j.get("status") == "running"]
            selected = (running or jobs)[0]["id"]
        detail = None
        if selected:
            try:
                detail = self.app.backend.job_detail(cfg, selected)
                detail["by"] = self.app.backend.job_started_by(cfg, selected)
            except Exception:  # noqa: BLE001 - a record being written
                detail = None
        self.app.post(self._jobs_loaded, jobs, selected, detail)

    @while_mounted
    def _jobs_loaded(self, jobs: list[dict], selected: str | None, detail: dict | None) -> None:
        self.jobs = jobs
        if self.selected is None:
            self.selected = selected
        if detail is not None and detail.get("id") == self.selected:
            self.detail = detail
        self.fill()
        self.show_detail()

    def visible(self) -> list[dict]:
        if self.filter == "running":
            return [j for j in self.jobs if j.get("status") == "running"]
        if self.filter == "recent":
            day = time.time() - 86400
            return [j for j in self.jobs if j.get("status") == "running" or (j.get("started") or 0) >= day]
        return self.jobs

    def fill(self) -> None:
        running = sum(1 for j in self.jobs if j.get("status") == "running")
        self.query_one("#jobs-filter", Segmented).set_options(
            [("running", f"Running {running}"), ("recent", "Recent"), ("all", "All")], self.filter)
        table = self.query_one("#jobs-table", DataTable)
        table.clear()
        rows = self.visible()
        for j in rows:
            label, token = STATES.get(state_of(j), (state_of(j), "muted"))
            if state_of(j) == "running":
                label = f"{SPINNER[self._spin]} running"
            ident = Text(short_id(j["id"]), style=PALETTE["working"] if j.get("status") == "running" else "")
            prompt = " ".join((j.get("label") or "").split())
            table.add_row(ident, job_agent(j) or j.get("kind", ""), Text(j.get("model") or "", style=PALETTE["dim"]),
                          prompt[:44], Text(label, style=PALETTE[token]), elapsed(j.get("elapsed", 0)),
                          key=j["id"])
        if not rows:
            table.add_row(Text("No job here." if self.jobs else "No job yet.", style=PALETTE["dim"]),
                          "", "", "", "", "")
        ids = [j["id"] for j in rows]
        if self.selected in ids:
            table.move_cursor(row=ids.index(self.selected), animate=False)

    @on(Segmented.Changed, "#jobs-filter")
    def _filter(self, event: Segmented.Changed) -> None:
        if event.value and event.value != self.filter:
            self.filter = event.value
            self.fill()

    @on(DataTable.RowHighlighted, "#jobs-table")
    def _row(self, event: DataTable.RowHighlighted) -> None:
        key = event.row_key.value if event.row_key else None
        if key and key != self.selected and any(j["id"] == key for j in self.jobs):
            self.select_job(str(key))

    def select_job(self, job_id: str) -> None:
        """Show one job (the Overview's JOBS rows open it here)."""
        self.selected = job_id
        self.detail = None
        self.filter = "all" if not any(j["id"] == job_id and j.get("status") == "running"
                                       for j in self.jobs) else self.filter
        self._hide_confirm()
        self.show_detail()
        self.poll()

    def show_detail(self) -> None:
        d = self.detail
        job = next((j for j in self.jobs if j["id"] == self.selected), None)
        title = self.query_one("#job-title", SectionTitle)
        if job is None and d is None:
            title.set("Job", extra="")
            for key in ("prompt", "folder", "started", "session"):
                self.query_one(f"#job-{key}", Static).update("")
            self.query_one("#job-tail", Static).update(Text("Select a job.", style=PALETTE["dim"]))
            for b in ("#job-cancel", "#job-full", "#job-copy"):
                self.query_one(b, Button).display = False
            return
        info = {**(job or {}), **(d or {})}
        state = state_of(info)
        label, token = STATES.get(state, (state, "muted"))
        title.set(f"Job {short_id(info['id'])}", extra=label)
        self.query_one("#job-prompt", Static).update(" ".join(str(info.get("prompt") or info.get("label")
                                                                  or "").split())[:300])
        self.query_one("#job-folder", Static).update(Text(str(info.get("cwd") or ""), style=PALETTE["dim"]))
        by = f" · by {info['by']}" if info.get("by") else ""
        self.query_one("#job-started", Static).update(started(info.get("started") or 0) + by)
        self.query_one("#job-session", Static).update(Text(str(info.get("session_id") or "none yet"),
                                                           style=PALETTE["dim"]))
        tail = (d or {}).get("tail") if d else None
        box = self.query_one("#job-output", VerticalScroll)
        follow = box.scroll_y >= box.max_scroll_y - 1
        self.query_one("#job-tail", Static).update(
            Text(tail if tail else ("Reading the output…" if d is None else "No output yet."),
                 style=PALETTE["text"] if tail else PALETTE["dim"]))
        if follow and state == "running":
            box.call_after_refresh(box.scroll_end, animate=False)
        running = state == "running"
        self.query_one("#job-cancel", Button).display = running
        self.query_one("#job-full", Button).display = True
        self.query_one("#job-copy", Button).display = bool(info.get("session_id"))

    # -- actions --------------------------------------------------------------------

    @on(Button.Pressed, "#job-cancel")
    def key_c(self) -> None:
        job = next((j for j in self.jobs if j["id"] == self.selected), None)
        if job is None or job.get("status") != "running":
            return
        agent = job_agent(job)
        self.query_one("#job-confirm-text", Static).update(
            f"Stop job {short_id(job['id'])}{f' ({agent})' if agent else ''}? Its output so far is kept.")
        confirm = self.query_one("#job-confirm")
        confirm.display = True
        self.query_one("#job-actions").display = False
        self.query_one("#job-keep", Button).focus()

    def _hide_confirm(self) -> None:
        self.query_one("#job-confirm").display = False
        self.query_one("#job-actions").display = True

    @on(Button.Pressed, "#job-keep")
    def _keep(self) -> None:
        self._hide_confirm()

    @on(Button.Pressed, "#job-stop")
    def _stop(self) -> None:
        self._hide_confirm()
        if self.selected:
            self._cancel_worker(self.selected)

    @work(thread=True, group="jobs-cancel")
    def _cancel_worker(self, job_id: str) -> None:
        try:
            msg = self.app.backend.cancel_job(self.app.cfg, job_id)
            ok = True
        except Exception as exc:  # noqa: BLE001
            msg, ok = str(exc), False
        self.app.post(self._cancelled, job_id, msg, ok)

    @while_mounted
    def _cancelled(self, job_id: str, msg: str, ok: bool) -> None:
        self.app.notify(f"Job {short_id(job_id)}: {msg}." if ok else msg,
                        severity="information" if ok else "error")
        self.poll()

    @on(Button.Pressed, "#job-full")
    def key_o(self) -> None:
        if self.detail is None:
            return
        from .dialogs import TextView

        self.app.push_screen(TextView(f"Job {short_id(self.detail['id'])} · full output",
                                      self.detail.get("output") or "No output yet."))

    @on(Button.Pressed, "#job-copy")
    def _copy(self) -> None:
        sid = (self.detail or {}).get("session_id")
        if sid:
            if not self.app.backend.copy_text(sid):
                self.app.copy_to_clipboard(sid)
            self.app.notify("Session id copied.")

    def key_d(self) -> None:
        self.query_one("#default-agent", Segmented).focus()

    def key_none(self) -> None:
        pass

    # -- agents -----------------------------------------------------------------------

    def config_changed(self) -> None:
        cfg = getattr(self.app, "cfg", None)
        agents = dict(getattr(cfg, "agents", {}) or {}) if cfg is not None else {}
        default = str(getattr(cfg, "default_agent", "") or "")
        self.quiet = True
        self.query_one("#default-agent", Segmented).set_options([(n, n) for n in agents], default)
        menu = self.query_one("#agents-list", OptionList)
        menu.clear_options()
        for name, spec in agents.items():
            line = Text()
            on = bool(getattr(spec, "enabled", True))
            line.append("● " if on else "○ ", style=PALETTE["ok" if on else "dim"])
            line.append(name, style="bold" if on else PALETTE["dim"])
            models = list(getattr(spec, "models", []) or [])
            line.append(f"  {len(models)} model{'s' if len(models) != 1 else ''}", style=PALETTE["muted"])
            if name == default:
                line.append(" · default", style=PALETTE["accent"])
            menu.add_option(Option(line, id=name))
        names = list(agents)
        if names:
            self.agent = self.agent if self.agent in names else names[0]
            menu.highlighted = names.index(self.agent)
        self.call_after_refresh(self._unquiet)
        self.show_agent()

    def _unquiet(self) -> None:
        self.quiet = False

    @on(OptionList.OptionHighlighted, "#agents-list")
    def _agent(self, event: OptionList.OptionHighlighted) -> None:
        self.agent = str(event.option.id)
        self.show_agent()

    def show_agent(self) -> None:
        cfg = getattr(self.app, "cfg", None)
        spec = (getattr(cfg, "agents", {}) or {}).get(self.agent) if cfg is not None and self.agent else None
        offered = self.query_one("#agent-offered")
        detail = self.query_one("#agent-detail", Static)
        link = self.query_one("#agent-advanced", Button)
        if spec is None:
            offered.display = link.display = False
            detail.update(Text("No agent is configured.", style=PALETTE["dim"]))
            return
        offered.display = link.display = True
        self.quiet = True
        self.query_one("#agent-enabled", Switch).value = bool(getattr(spec, "enabled", True))
        self.call_after_refresh(self._unquiet)
        out = Text()
        models = list(getattr(spec, "models", []) or [])
        if models:
            out.append("models  ", style=PALETTE["muted"])
            for m in models:
                out.append_text(tag(m, "working"))
                out.append(" ")
            out.append("\n")
        efforts = list(getattr(spec, "efforts", []) or [])
        if efforts:
            out.append("efforts ", style=PALETTE["muted"])
            for e in efforts:
                out.append_text(tag(e, "muted"))
                out.append(" ")
            out.append("\n")
        command = " ".join(getattr(spec, "command", []) or [])
        out.append(command[:120], style=PALETTE["dim"])
        detail.update(out)
        link.label = f"Every setting of {self.agent} in Advanced ›"

    @on(Segmented.Changed, "#default-agent")
    def _default(self, event: Segmented.Changed) -> None:
        cfg = getattr(self.app, "cfg", None)
        if self.quiet or cfg is None or not event.value or event.value == getattr(cfg, "default_agent", ""):
            return
        if not self.app.apply_settings({"default_agent": event.value}):
            self.config_changed()

    @on(Switch.Changed, "#agent-enabled")
    def _enabled(self, event: Switch.Changed) -> None:
        cfg = getattr(self.app, "cfg", None)
        spec = (getattr(cfg, "agents", {}) or {}).get(self.agent) if cfg is not None else None
        if self.quiet or spec is None or bool(event.value) == bool(getattr(spec, "enabled", True)):
            return
        if not self.app.apply_settings({f"agents.{self.agent}.enabled": bool(event.value)}):
            self.config_changed()

    @on(Button.Pressed, "#agent-advanced")
    def _advanced(self) -> None:
        if self.agent:
            self.app.action_show_tab("advanced")
            self.app.panes["advanced"].show_section(f"agents.{self.agent}")
