# YAPILACAKLAR.md — pcbridge 2.0: from personal tool to product

> **Audience: Claude (the agent executing this plan), not a human.**
> This file is the single source of truth for this run. It supersedes
> `WALKTHROUGH.md` as the "what's next" list until the run is finished.
> eymistaken gives this task once and is not available to answer questions.
> The run may take up to two days, so expect context compaction.

---

## 0. Resume protocol (read this first, every time)

1. Read this file top to bottom. Also read `CLAUDE.md`. It holds rules and
   measured machine facts. Where it conflicts with this file, **this file
   wins for this run**.
2. Find the first step in section 6 that is not `[x]`. Read its progress-log
   entries in section 8. Continue from there.
3. Run `git status` and `git log --oneline -15`. Uncommitted work may be
   half-finished. Before building on it, check it: run the tests, or read the
   diff.
4. Update this file before every commit:
   - tick the checkboxes,
   - append a progress-log line,
   - record every decision in section 7.

   This file is your memory across compactions. If it is not written here,
   you will not know it after the next compaction.
5. Do not ask eymistaken anything. When a choice is open:
   - take the conservative option (the one that cannot break the invariants
     in section 2),
   - record it in section 7 with the reason,
   - carry on.

   If something needs eymistaken physically (sudo password, logging out and back
   in, looking at the screen), add it to section 9 and move on.

---

## 1. Mission and definition of done

Turn pcbridge into a finished product. Five goals:

- **It never feels fragile.**
- **It installs in minutes** on Ubuntu-family `.deb` systems running
  GNOME on Wayland.
- **Everything it says is in English.**
- **It adapts to any monitor layout.**
- **It is ready the moment an agent asks for it**, with no app to open and
  no command to run.

The run is done when **all** of the following hold:

- [ ] Every step in section 6 is `[x]`, or is explicitly marked "cut" in
      section 7 with a reason. Only optional items may be cut; see the
      priority order in section 5.
- [ ] Every invariant in section 2 has been verified on eymistaken's real machine
      after the final install, with the numbers written into section 8.
- [x] pcbridge 2.0.0 is **installed and running on eymistaken's machine** through
      the product install path, not from the working tree. Claude Code,
      Codex and Claude Desktop are all registered against it.
- [ ] The repository documentation has been rewritten and pruned (step 11).
- [ ] `main` is fast-forwarded to the work branch and pushed, together with
      tag `v2.0.0`, after a clean secret scan (step 12).
- [ ] The final message to eymistaken contains four things:
  - a short summary,
  - the measured numbers,
  - the list from section 9,
  - a rollback recipe.

---

## 2. Hard invariants — "always ready" must NEVER break

eymistaken's most important requirement is this: today he can tell any agent to use
pcbridge and it just works. Nothing has to be opened or started, and it is
always ready. Every change must preserve the following. Check them after
every step that touches startup, transport, paths, config, registration or
installation. Use the readiness check built in step 0.

| # | Invariant | How it is verified |
|---|---|---|
| I1 | A **fresh** client process can use pcbridge with no manual action. That means fresh Claude Code, Codex and Claude Desktop, each started with its registered command: initialize, then `tools/list`, then a real tool call. | Readiness check spawns each registered command as a new process |
| I2 | **Old registrations keep working.** `<repo>/.venv/bin/python -m pcbridge.server --stdio` (what the clients have today) must still work after every step, including after the final install. Running Claude Desktop and Claude Code sessions keep their old stdio process until they restart. That process must not break, and it must not corrupt shared state (the grant file, pointer.json, shots). | Readiness check runs the legacy command too |
| I3 | **Nothing to start by hand.** If the daemon introduced in step 3 is down, crashed, mid-restart or older than the client, the client still gets a working pcbridge: through socket activation, or through in-process fallback. | Fault-injection tests in step 3 and step 8 |
| I4 | **Survives reboot and re-login** with no action: the service and socket are enabled, and nothing depends on a terminal having been opened. | `systemctl --user is-enabled`; simulated with stop and then first use |
| I5 | **No running job is killed by an update or restart** unless eymistaken explicitly runs a stop. Restarts wait until no jobs are running and no desktop grant is open. | Test in step 8 |
| I6 | **Mixed versions coexist.** The GNOME extension loaded in eymistaken's current session is the old one until he logs out and in. The new server must work with the old extension, and the new extension with an older server. The same applies to an old stdio process running next to the new daemon. | Test in step 7 |
| I7 | **The agent can still unlock the desktop by itself.** `desktop_unlock` stays self-service for the agent. eymistaken explicitly rejected any human-approval prompt or confirmation dialog in the unlock path, because it would break the flow. Anything added (panel indicator, kill switch) may make it easier to **stop** the agent, never harder to **start** it. | Readiness check calls `desktop_unlock` then `desktop_lock` |
| I8 | **eymistaken's config and state are preserved.** `config.toml` values, the OAuth DB, client registrations and aliases all survive migration. Every file that is rewritten gets a timestamped backup first. `[desktop] enabled` stays `false` **for fresh installs**, and eymistaken's existing value is kept. | Diff before and after, recorded in section 8 |
| I9 | **No performance regression.** Baselines are recorded in step 0 (screenshot ~789 ms, window focus ~5 ms, `ui_dump` 14–18 ms, and so on). The limits are +10 % or +5 ms, whichever is larger. The per-call proxy overhead budget is ≤ 5 ms at p50. | Measured in step 0, step 3 and step 10 |
| I10 | **All non-live test suites pass** before every commit. Live suites pass at the points named in the steps. | Test commands in `CLAUDE.md` plus the new CI |

If a change would require violating an invariant, **do not make it**. Find
another design, or cut the feature and record why in section 7.

---

## 3. Scope

### In scope

1. **Packaging foundation.** A `pyproject.toml`, one version source, and a
   single `pcbridge` CLI.
2. **Standard file locations** (XDG) with config migration. Remove every
   assumption that is specific to eymistaken's machine.
3. **A persistent daemon with a thin stdio relay.** One process owns the
   helper, the capture session, the grant and the jobs. Clients connect to
   it and never go stale after an update.
4. **English everywhere a human or a model reads it.** That covers:
   - tool results and error messages,
   - logs, CLI, installer and doctor output,
   - `config.example.toml` comments,
   - extension strings,
   - docs.

   Stable error *codes* stay unchanged.
5. **Adaptation to any monitor layout.** Single monitor, 3+ monitors,
   ultrawide, portrait or rotated, HiDPI, fractional scaling, mixed scales,
   vertical stacking, negative origins, uneven heights.
6. **GNOME extension panel indicator and kill switch.** Also: MCP tool
   annotations checked on all tools, and optional tool profiles (default:
   everything, as today).
7. **Hardening.** Fault injection and self-healing.
8. **`.deb` package.** Build it, CI on every push (including the Python
   tests), an install matrix across target distros, and a release workflow
   that produces a **draft** GitHub release.
9. **Installation on eymistaken's machine** through the product path.
10. **Documentation rewrite and pruning.**

### Target platforms

- **Distros:** Ubuntu 24.04 LTS and later, Debian 13, Zorin OS 18.
- **Desktop:** GNOME Shell 46 and later, Wayland session.
- **Architecture:** x86_64.
- **Python:** whatever those distros ship (3.12 to 3.14).

On any other session (X11, KDE, Cinnamon, COSMIC, wlroots), pcbridge must:

- start,
- say clearly in English that desktop tools are unavailable and why,
- keep the non-desktop tools (agents, tmux, shell, files) fully working.

### Out of scope (do not start these)

- X11 support, other desktops, Windows, macOS, `.rpm`, Flatpak, Snap, arm64.
- Any human-in-the-loop approval for `desktop_unlock` (see I7).
- Rewriting `auth.py`'s OAuth logic, or touching `MetadataNormalizer` and
  `BasicAuthFormShim` in `server.py`.
- Removing `--dangerously-skip-permissions`, or changing the
  `[agents.antigravity]` block in eymistaken's `config.toml`.
- Mass-translating code comments. Translate comments only in code you
  substantially rewrite anyway. All strings that reach users or models must
  be English.
- Upgrading FastMCP from 3.4.5, unless a step cannot be done without it. In
  that case re-verify `ToolResult` and `output_schema=None` behavior, and
  record the change in section 7.
- Publishing to extensions.gnome.org, PyPI or an APT repository.

---

## 4. Operating rules for this machine

These add to the "Bu makinede test etmenin tehlikesi" section of `CLAUDE.md`.

- **Pre-authorization.** eymistaken has granted, in the kickoff prompt, everything
  needed to run tests unattended:
  - `desktop_unlock`,
  - live capture, input, AT-SPI and batch tests,
  - restarting pcbridge services when no jobs are running,
  - editing client registrations (with backups),
  - local commits,
  - the final push.

  Do not ask for permission for these.
- **Deleting files.**
  - Never delete a file that is not tracked by git with `rm` or `rm -rf`.
    Use `gio trash <path>` so it can be recovered. This is what eymistaken means by
    "no Shift+Delete".
  - Tracked files are removed with `git rm`; git history is the recovery
    path.
  - Permanent deletion is allowed only for things you created yourself
    during this run in a scratch or temporary directory.
- **Sudo.**
  - Check once with `sudo -n true`. Never wait on a password prompt.
  - If passwordless sudo is not available, design and finish everything so
    the user-level install needs no sudo. Anything that truly needs root
    goes to section 9 with the exact command.
  - eymistaken's `/dev/uinput` udev rule and the system packages are already
    installed.
- **Never change eymistaken's real display configuration.** That means no
  `ApplyMonitorsConfig`, no resolution, scale or rotation changes, no
  hotplug simulation on the real session. All multi-layout verification
  happens in fixtures and in nested or headless `gnome-shell` with virtual
  monitors.
- **Never lock the real screen.** Nobody is there to type the password, so
  every later GUI test would be blocked. Test screen-lock behavior in a
  nested or headless shell. You may also rely on the existing measurements
  in `CLAUDE.md`.
- **Never log out, restart gnome-shell or reboot.** Anything that needs that
  goes to section 9.
- **Live input tests.** Follow the `CLAUDE.md` safety list: an empty
  `gnome-text-editor` window, move first and then verify with a screenshot,
  never type into a terminal. eymistaken will not touch the keyboard, but assume
  focus can move.
- **Do not kill yourself.** First check whether your own process runs
  inside `pcbridge.service`'s cgroup: `cat /proc/self/cgroup`, and walk the
  parent PIDs. If it does, you are a pcbridge job and restarting the
  service kills you. In that case:
  - never restart or stop it yourself,
  - verify through new, separate processes and the socket-activated path
    only,
  - record this in section 7.
- **Your own pcbridge MCP tools run OLD code.** Your session's stdio process
  started before this run. Never verify new code through your own pcbridge
  tool calls. Verify through fresh processes: the readiness check, a
  scripted MCP client, or `tests/test_e2e.py` against a freshly started
  server.
- **Do not burn quota.**
  - Always set `PCBRIDGE_TEST_NO_AGENT=1` for `test_e2e.py`.
  - Check registration with `claude mcp list` or `codex mcp list` (connection
    check only, no prompt).
  - At most two real `claude -p` calls in the whole run, only in step 10,
    each with a trivial prompt.
- **Never** print, log or commit the contents of `config.toml`, the OAuth DB
  or tokens.
- **Git.**
  - Work on branch `productize/v2`, created from `main` in step 0.
  - One or more local commits per step. The repository style is
    conventional commits in English (`feat(daemon): …`, `docs: …`). Only
    commit when the tests are green.
  - No push until step 12.
- **Time boxes.** Each step has a soft time box. When a sub-goal proves
  infeasible (for example, headless gnome-shell inside a container), stop
  at the time box, record what you measured and why you stopped in
  section 7, and continue. Never stall.

---

## 5. Priority order (if time or budget runs short)

Must happen, in this order:

1. The invariants (I1–I10)
2. Step 0 (baseline and readiness check)
3. Step 3 (daemon)
4. Step 10 (install on eymistaken's machine)
5. Step 11 (docs)
6. Step 12 (release and push)

Then, in order:

1. Step 1
2. Step 2
3. Step 4
4. Step 5
5. Step 6
6. Step 8
7. Step 7
8. Step 9

When budget is clearly running out, jump to steps 10, 11 and 12 with
whatever is finished. **Always end with `main` pushed in a working,
verified state.** Half a feature behind a disabled flag is acceptable. A
broken default is not.

---

## 6. Steps

Every step follows the same loop:

1. **Measure first** where the step says so.
2. Implement.
3. Run the non-live suites and the readiness check.
4. Update this file.
5. Commit.

### Step 0 — Baseline, inventory, safety net  (time box: 2 h)

- [x] `git switch -c productize/v2`. Check that the working tree is clean.
- [x] Record the environment in section 8:
  - distro, GNOME version, Python version,
  - `sudo -n` result,
  - whether docker or podman is available,
  - whether `dpkg-deb`, `lintian`, `uv` and `pipx` are available,
  - monitor layout,
  - your own cgroup (see section 4).
- [x] **Inventory every consumer of pcbridge**, and record each with its
      exact command or path:
  - Claude Code registration (`~/.claude.json`, and the scope it uses),
  - Codex (`~/.codex/config.toml`),
  - Claude Desktop (`~/.config/Claude/claude_desktop_config.json`),
  - `~/Masaüstü/app/PcBridgeDesktop` (eymistaken's desktop app; find how it
    starts or connects to pcbridge and keep that working),
  - shell aliases in `~/.bashrc` and similar (`bridgeac`, `bridgekapat`,
    `bridgekilit`, `sparkac`, and so on),
  - systemd user units,
  - `~/.local/bin` symlinks,
  - the extension install location,
  - `state_dir` contents.
- [x] **Baseline numbers.** Median of 5 runs for each, recorded in
      section 8:
  - cold stdio start until `tools/list` answers,
  - warm tool-call round trip (`system_status`),
  - `screen_capture` of one monitor and of both,
  - `window_focus` on an already-open app,
  - `ui_dump` of one window,
  - daemon or service RSS.
- [x] **Baseline test results.** Record the counts:
  - `tests/test_models.py`, `tests/test_desktop.py`,
    `tests/test_test_safety.py`,
  - `python -m unittest discover tests/contracts` and `tests/integration`
    (non-live),
  - `gjs` extension tests,
  - `cargo test --no-fail-fast`,
  - `test_e2e.py` with `PCBRIDGE_TEST_NO_AGENT=1`.
- [x] **Build the readiness check**, `tests/readiness/check.py`. It must be
      standalone, need no pytest, and return a nonzero exit code on failure.
      It covers I1, I2, I7 and I9:
  - For each registered client command plus the legacy command: spawn a
    fresh process, run initialize, then `tools/list` (assert the full tool
    set), then call `system_status` and `system_capabilities`.
  - With `--desktop`: call `desktop_unlock(1)` and then `desktop_lock`.
  - Report timings.
  - Run it now against the current code. It must pass.
- [x] **Commit**: `test: add a readiness check and record the pre-2.0 baseline`.

### Step 1 — Packaging foundation  (time box: 3 h)

- [x] Add a `pyproject.toml`: a standard build backend, pinned
      `fastmcp==3.4.5`, optional extras for the desktop (evdev, Pillow),
      and Python ≥ 3.12. Measure first: does anything in the code rely on
      3.10/3.11 behavior?
- [x] Keep one version source (`pcbridge/__init__.py`, bumped to
      `2.0.0.dev0` during the run and to `2.0.0` in step 12).
- [x] Add console entry points: `pcbridge` (the CLI from step 4; for now a
      stub that dispatches `pcbridge serve`, `pcbridge stdio` and
      `pcbridge --version`), `pcb-shot` and `pcb-do`.
- [x] Package data: the native helper binary, the extension, the systemd
      units and udev rule templates, and `config.example.toml`.
- [x] `python -m pcbridge.server [--stdio]` keeps working unchanged (I2).
- [x] **Verify.** Build a wheel, install it into a throwaway venv outside
      the repo, run the non-live suites against the installed package, and
      run the readiness check against the installed entry point.
- [x] **Commit**: `build: package pcbridge with pyproject and a single version source`.

### Step 2 — Standard locations, config migration, no machine-specific assumptions  (time box: 4 h)

- [x] **Config search order**, decided in one function:
  1. `$PCBRIDGE_CONFIG`
  2. `$XDG_CONFIG_HOME/pcbridge/config.toml`
  3. the legacy `<repo>/config.toml`

  With the legacy file, add a one-line English deprecation note in the log
  and in doctor. Record in section 7 which file wins when both exist.
- [x] **Other data locations**:
  - state stays in `$XDG_STATE_HOME/pcbridge` (already there; do not move
    it while old processes may still be running),
  - logs go to `$XDG_STATE_HOME/pcbridge/log`,
  - cache goes to `$XDG_CACHE_HOME/pcbridge`,
  - runtime files (the socket) go to `$XDG_RUNTIME_DIR/pcbridge`, with the
    directory set to `0700`.
- [x] **Config schema.**
  - Add `config_version`, plus a migration function that writes a
    timestamped backup before any rewrite.
  - Secret-bearing files are `0600`, and `pcbridge doctor` fixes the mode.
  - Unknown keys raise a clear English warning, not a crash.
- [x] **Remove machine-specific assumptions.**
  - `server.py` `INSTRUCTIONS`: remove "ZorinOS, two 1920x1080 monitors".
    Describe the capabilities generically, and tell the model to call
    `system_capabilities` / `screen_info` for the actual layout. The
    instructions are built once at startup; do not bake a layout into them.
  - Systemd unit: remove the hard-coded `DISPLAY=:0`, the `.npm-global`
    PATH entry and the "Gemini Spark" description. `ensure_session_env()`
    already derives DISPLAY; verify it does so inside the daemon.
  - Agent CLI discovery: use the configured absolute path, then `PATH`,
    then well-known user bin dirs (`~/.local/bin`, `~/.npm-global/bin`,
    `~/.bun/bin`, the nvm current bin). If none is found, raise an English
    error that says exactly which setting to change.
  - Rename or remove `sparkac`-style aliases and Gemini-era leftovers
    throughout. Keep eymistaken's existing aliases working (step 4 rewrites them).
  - `grep` for `1920`, `3840`, `1080`, `DP-`, `Zorin`, `eymistaken`, `/home/`
    and similar hard-coded values. Every hit that is not a comment, a test
    fixture or a documented measurement must be derived at runtime.
- [x] **Verify.** Run the non-live suites, the readiness check (legacy and
      packaged), and a migration test: a copy of eymistaken's config migrated in a
      temporary `XDG_CONFIG_HOME` gives identical effective settings
      (compare the dataclasses, not the text).
- [x] **Commit(s)**: `feat(config): XDG locations and versioned migration`,
      `fix: remove assumptions about the author's machine`.

### Step 3 — Persistent daemon and a thin stdio relay  (time box: 8 h; highest risk, do it carefully)

**Why:** today every client spawns its own server process. That means:

- stale code after every update,
- up to five server processes writing the same state files,
- the Codex broken-`DBUS_SESSION_BUS_ADDRESS` class of bugs,
- one native helper and one capture session per client.

**Target architecture:**

```
client (Claude Code / Codex / Claude Desktop / PcBridgeDesktop)
   │ spawns: pcbridge stdio        (legacy: python -m pcbridge.server --stdio → same code path)
   ▼
stdio relay (tiny, no heavy imports)
   │ $XDG_RUNTIME_DIR/pcbridge/mcp.sock   (0600, dir 0700; filesystem permission = auth, same boundary as stdio today)
   ▼
pcbridged  (systemd --user pcbridge.service, socket-activated via pcbridge.socket, also enabled at login)
   ├── MCP sessions over the unix socket (one connection = one MCP session)
   ├── HTTP + OAuth on 127.0.0.1:8765 for remote use (unchanged, tunnel still manual)
   └── owns: native helper, capture session, grant, execution lock, jobs, audit log
```

**Measure first. Decide, record the decision in section 7, then build.**

- [x] Can the daemon serve **raw MCP JSON-RPC** (the stdio framing,
      newline-delimited) per socket connection? Use the MCP SDK's server
      session over anyio streams, fed from the socket. If so, the relay is
      a dumb byte pipe. Nothing is translated, so image blocks,
      `structuredContent`, `isError`, progress, cancellation and
      notifications pass through byte for byte. **This is the preferred
      design.** The alternative is a FastMCP proxy over streamable HTTP on
      a unix socket. Use it only if it measures equally faithful, and prove
      it with tests that compare responses byte for byte.
- [x] How does per-process context used today map into the daemon? Check
      each of these:
  - the stdio process's **cwd** (relative paths in `_resolve_dir` /
    `_resolve_file`, and the working directory of `agent_run`),
  - its **environment** (what `jobs.py` passes to agents via
    `os.environ.copy()`; client-specific variables),
  - `screen_capture` returning a **file path** on stdio but a link on HTTP.

  The relay sends a one-line JSON preamble before relaying: shim version,
  client name, pid, cwd, and a small allow-listed env subset if you
  measured that it is needed. The daemon applies the preamble per session,
  so behavior per client is identical to today. Record every difference
  you find, and how it was preserved.
- [x] Socket activation in Python: take the inherited fd via `LISTEN_FDS`,
      and serve the HTTP listener in the same event loop.

**Build:**

- [x] **Relay** (`pcbridge stdio`). The legacy entry point routes here.
  - Connect to the socket.
  - If that fails, run `systemctl --user start pcbridge.socket` (or the
    service) and wait at most 2 s.
  - If it still fails, **fall back to the in-process server**: today's code
    path, with identical behavior. Log it as `degraded: in-process`, visible
    in `system_status` and in doctor.
  - The relay never exits while its client is alive.
- [x] **Daemon-restart transparency.** The relay records the client's
      `initialize` request and `notifications/initialized`. When the socket
      drops:
  - it reconnects (activation if needed),
  - it replays the handshake and swallows the reply,
  - for every request that was in flight, it synthesizes a JSON-RPC error
    with the matching id: English message, retryable. The client must never
    hang.
- [x] **Version handling.**
  - Daemon and relay exchange versions and a protocol number.
  - A compatible daemon is used even if it is older.
  - The daemon detects that newer code was installed (a version file or
    stamp) and **restarts itself only when idle**: no running jobs (I5), no
    open desktop grant, no in-flight calls. That is what finally kills the
    "stale stdio process" problem.
- [x] **Units.**
  - `pcbridge.socket` and `pcbridge.service` (user units), both enabled.
  - `Restart=on-failure`.
  - Keep `ExecStopPost` lock behavior.
  - The service starts at login and is also socket-activated (I4).
- [x] **State shared with old processes.** Old stdio processes (eymistaken's
      running clients) will run next to the daemon for a while. Everything
      they share on disk must stay backward compatible (I2, I6): the grant
      file format, `pointer.json`, the shot records, and the execution lock.
- [x] **Audit log.** Include the client name from the preamble.

**Verify (record the numbers):**

- [x] **Byte-for-byte parity.** The same scripted session (`tools/list`, a
      text tool, an error case, a `screen_capture` with image, a
      `structuredContent` tool, a cancellation, a long job with
      `job_status`) goes through in-process stdio and through the relay +
      daemon. The responses must be identical apart from ids and timestamps.
- [x] **Timing budgets.**
  - Warm overhead per call: ≤ 5 ms at p50.
  - Cold start with the socket not yet running, until the first `tools/list`
    answers: ≤ 3 s.
  - Screenshot end to end: within I9.
- [x] **Faults.** Each of these must recover within 3 s with no client
      hang:
  - `kill -9` of the daemon mid-session: the next call succeeds, the
    in-flight call gets a retryable error;
  - daemon stopped: activation brings it back;
  - socket file deleted or stale;
  - `XDG_RUNTIME_DIR` unusable: in-process fallback;
  - three clients at once: execution lock serialization still works.
- [x] Full non-live suites. The readiness check on all registered commands
      and the legacy command. `test_e2e.py` (`PCBRIDGE_TEST_NO_AGENT=1`)
      against the daemon's HTTP path.
- [x] **Live desktop suite through the relay**, with all four flags; see
      `CLAUDE.md`. Also run a real `computer_batch` in an empty
      `gnome-text-editor` window.
- [x] **Commit(s)**: `feat(daemon): one resident server behind a thin stdio relay`,
      and separate commits for tests and units.

### Step 4 — One CLI: `pcbridge`  (time box: 4 h)

All output is in English, `--json` is available where it makes sense,
`--yes` makes it non-interactive, and every subcommand is idempotent.

- [x] `pcbridge setup`: first-run wizard. It covers:
  - dependency check with exact `apt` commands printed,
  - config creation or migration,
  - units enabled,
  - udev / uinput status,
  - extension install,
  - client registration via `connect`,
  - a final readiness check.
- [x] `pcbridge connect [--client claude-code|codex|claude-desktop|all] [--dry-run]`:
  - back up each client config before editing it,
  - register Claude Code at **user** scope (`CLAUDE.md` explains why
    project scope silently fails),
  - point every client at `pcbridge stdio`.
- [x] `pcbridge doctor [--fix] [--json]`: port all ~35 checks from
      `doctor.sh`, and add daemon, socket, relay, version-skew, readiness
      and XDG checks. `--fix` repairs only safe things: modes, units,
      registrations, a stale socket.
- [x] `pcbridge status`: daemon up or down, version, clients connected,
      grant state and time remaining, running jobs, remote tunnel state,
      degraded mode.
- [x] `pcbridge lock`: the emergency stop. It revokes the grant and kills
      the screencast helpers, exactly like `bridgekilit` today.
      `pcbridge unlock --minutes N` is for humans.
- [x] `pcbridge remote start|stop|status`: replaces `remote.sh`.
- [x] `pcbridge logs [-f]`.
- [x] `pcbridge report`: writes a sanitized tarball (versions, doctor JSON,
      recent logs with secrets and paths redacted, config with secrets
      removed), for bug reports.
- [x] `pcbridge update`: drain-aware restart (I5). For git installs it also
      pulls and rebuilds; for package installs it only prints how to
      update.
- [x] `pcbridge uninstall [--purge]`: keeps config and state unless
      `--purge`, and uses `gio trash` where possible.
- [x] `pcbridge --version`.
- [x] **Old scripts.**
  - `install.sh` becomes a small bootstrap for git checkouts:
    `uv`/venv, install the package, then `pcbridge setup`.
  - `connect.sh`, `doctor.sh`, `remote.sh`, `run.sh`, `add_client.py` and
    `capture.sh` either become one-line wrappers that print an English
    deprecation note, or are removed if nothing references them. Record
    which in section 7.
  - `setup_uinput.sh` stays as the git-install path for the udev rule.
  - Update eymistaken's aliases in his shell rc to call `pcbridge …`, with a
    backup of the rc file.
- [x] **Verify.** CLI tests (subprocess, temporary `XDG_*` dirs), plus
      `pcbridge doctor --json` on the real machine with every check green
      or explained.
- [x] **Commit**: `feat(cli): one pcbridge command for setup, connect, doctor and operation`.

### Step 5 — English everywhere  (time box: 5 h)

- [x] **Inventory every string that reaches a human or a model**:
  - tool return texts, `DesktopError.message`, `suggested_action`,
  - exceptions surfaced to clients,
  - log messages, CLI, installer and doctor output,
  - notifications (`notify-send`),
  - `config.example.toml` comments,
  - extension strings,
  - `skills/computer-use/SKILL.md`,
  - `bin/pcb-shot` and `bin/pcb-do` output.

  Many are ASCII-Turkish ("Masaustu kontrolu kapali"), so grep will not find
  them by special characters alone. Walk every `return`, `raise`, `log.*`,
  `print`, `f"…"` in the user-facing layers. Write the inventory into
  section 8 as a file count.
- [x] **Translate** into clear, specific English that tells the reader what
      to do next. Keep error **codes**, categories, scopes and field names
      unchanged, because they are API. Keep messages short, since models
      read them on every error.
- [x] Update the tests that assert on message text. Prefer asserting on
      codes where possible.
- [x] Update the `CLAUDE.md` rule "Kullanıcıya dönen metinler Türkçe" to
      "all user- and model-facing text is English".
- [x] **Verify.** Add a guard test: a script that scans the user-facing
      modules for common Turkish words and characters, with an allow-list
      for test fixtures, the Turkish-typing tests and similar. Run all
      suites.
- [x] **Commit(s)**: `feat(i18n): English for every message a user or model reads`.

### Step 6 — Any monitor layout  (time box: 6 h)

- [x] **Audit.** Look for every code path that assumes:
  - 2 monitors,
  - side-by-side placement,
  - equal heights,
  - scale 1.0,
  - landscape orientation,
  - a canvas starting at (0,0),
  - the long edge being the width.

  Check `monitors.py`, `capture.py` (crop, scale, long-edge, the shot
  record), `input.py` and the native `input` (absolute axis range across a
  non-rectangular canvas), `uitree.py`, `ocr.py`, `batch.py`, the extension
  (`frame.js` glow per monitor, cursor), `topology_id`, and the Rust side.
- [x] **Fixture matrix.** Contract tests in Python and Rust, with parity
      between them, for these layouts:
  - single 1920×1080
  - single 2560×1440 @1.25
  - 3840×2160 @2.0
  - 3840×2160 @1.5
  - 2880×1800 @1.75 laptop + 1920×1080 external
  - ultrawide 3440×1440
  - super-ultrawide 5120×1440
  - portrait 1080×1920 (transform 90/270)
  - vertical stack
  - 3 monitors of mixed size, bottom-aligned
  - negative compositor origin
  - gaps between monitors

  For each layout test:
  - `to_global` round trips (`monitor=` and `shot=`),
  - crop boxes,
  - the long-edge scale choice,
  - the refusal of coordinates in gaps,
  - the ambiguous-coordinate guard,
  - the `topology_id` stability rules.
- [x] **Screenshot sizing policy for extreme aspect ratios.**
  - Keep the 1568 long-edge ceiling (see `CLAUDE.md`).
  - Also cap the pixel area.
  - Portrait monitors scale by their height.
  - A full-canvas capture of a very wide canvas warns (in English) that
    text will be unreadable, and suggests `monitor=`.
  - Document the policy in `config.example.toml`.
- [x] **Live verification without touching eymistaken's display.** Measure first
      whether `gnome-shell --headless` (or `--nested`) with virtual monitors
      of chosen sizes and scales works on this machine, and whether Mutter
      ScreenCast, the uinput absolute mapping and AT-SPI work inside it. The
      existing `gnome-extension/nested.sh` is a starting point; mind its
      orphan-process lesson in `CLAUDE.md`. Where it works, run capture
      parity and pointer-accuracy checks for at least three layouts: single
      4K @2.0, ultrawide, portrait. Where it does not, record the finding
      and rely on the fixtures.
- [x] Re-run the real-machine live suite. eymistaken's two-monitor setup must be
      unchanged (I9).
- [x] **Commit(s)**: `feat(display): adapt to any monitor layout, scale and orientation`.

### Step 7 — Extension panel indicator, kill switch, tool surface  (time box: 5 h)

- [x] **Panel indicator** (GNOME 46 API first). It shows:
  - daemon up or down,
  - desktop grant open or closed and the time remaining,
  - running job count,
  - remote tunnel on or off.

  Menu items:
  - "Lock desktop control now", which runs `pcbridge lock`; this is the
    kill switch;
  - "Open logs";
  - "Status…".

  The indicator only **reads** state. The daemon writes a small
  `state_dir/status.json`; the extension watches it the same way it watches
  the grant file today. The indicator must never add a step to starting
  work (I7).
- [x] **Kill-switch keyboard shortcut.** Configurable, off or on by default
      per your judgment; record it in section 7. It must not collide with
      GNOME or Zorin defaults, so check with `gsettings`.
- [x] **Compatibility** (I6):
  - Add a `Version` property on the D-Bus interface.
  - Keep `ActivateWindow` and `FocusedWindow` byte-compatible.
  - The server must work with the old extension, which has no `Version`,
    no indicator and no `FocusedWindow`.
  - The new extension must work with an older server, which has no
    `status.json`.
  - Keep the extension UUID unchanged, because renaming it orphans eymistaken's
    install.
- [x] **`shell-version`.** List only versions you actually ran. Record the
      untested ones in the docs as "expected to work, untested".
- [x] **Verify in the nested shell** with the gjs tests and new tests for
      the indicator state machine. The real session loads the new
      extension only after re-login; add that check to section 9 with an
      exact checklist.
- [x] **Tool surface.**
  - Audit `readOnlyHint`, `destructiveHint`, `idempotentHint` and
    `openWorldHint` on every tool. A wrong `readOnlyHint` is dangerous (see
    `CLAUDE.md`).
  - Add an optional `[tools] profile` (`full` by default = exactly today's
    set; `core` = no desktop tools; `desktop`) so users can shrink context.
    Do not hide tools dynamically at runtime, because clients cache tool
    lists.
- [x] **Commit(s)**: `feat(extension): panel indicator and kill switch`,
      `feat(tools): audited annotations and optional profiles`.

### Step 8 — Hardening and self-healing  (time box: 5 h)

For each scenario below: write the expected behavior, then add an automated
test where possible (a fake or temp environment), or a scripted manual check
where not. Record the results in section 8.

- [x] The native helper crashes mid-capture: the next call respawns it, and
      no stale grant binding is left behind.
- [x] Two clients call `desktop_unlock` concurrently, with each other's
      grant id rotation (see `CLAUDE.md`, "Native yardımcı tek bir izne
      bağlı").
- [x] An update is installed while a job is running: the daemon defers the
      restart until idle (I5), and the relay reconnects afterwards.
- [x] `config.toml` is corrupt or has an unknown key: the daemon still
      starts the non-desktop tools and doctor names the line; or it fails
      with an English message saying exactly what to fix, never a traceback.
- [x] Missing optional dependencies (evdev, Pillow, gi, tesseract,
      wl-clipboard): the related tools report capability-unavailable in
      English with the install command, and the rest works.
- [x] A broken client environment: the Codex literal
      `$DBUS_SESSION_BUS_ADDRESS` case, and empty `XDG_SESSION_TYPE` as
      Claude Desktop sends it (see `CLAUDE.md`). With the daemon these no
      longer reach the tools; prove it through the relay.
- [x] Unsupported session (X11, non-GNOME): a clean degraded mode, tested
      with a faked environment.
- [x] Disk full or read-only state dir: an English error, no crash loop.
- [x] Log rotation, so logs cannot grow unbounded.
- [x] Screen locked or monitor hotplug: use a nested or headless shell
      only (section 4); otherwise rely on the existing measurements.
- [x] **Commit(s)**: `fix: …` / `test: fault injection for …`.

### Step 9 — `.deb` package, CI, distro matrix  (time box: 6 h)

- [x] **Package layout** (pick a tool: plain `dpkg-deb` with a build
      script, or nfpm; record the choice in section 7):
  - `/usr/lib/pcbridge/` holds a self-contained venv, built by `uv`, with
    the pinned deps and the native helper.
  - `/usr/bin/pcbridge`, `pcb-shot` and `pcb-do`.
  - `/usr/lib/systemd/user/pcbridge.{service,socket}`.
  - `/usr/lib/udev/rules.d/60-pcbridge-uinput.rules`. The number is
    functional; see `CLAUDE.md`. Use `uaccess`, so no setup script is needed.
  - The extension under `/usr/share/gnome-shell/extensions/<uuid>/`.
  - Docs under `/usr/share/doc/pcbridge/`.
  - `Depends:` python3, tmux, wl-clipboard, libnotify-bin, python3-gi,
    gir1.2-atspi-2.0, and the PipeWire runtime libs the helper needs (see
    `docs/native/packaging.md`).
  - `Recommends:` tesseract-ocr, tailscale (Suggests).
  - `postinst` reloads udev and prints "Run `pcbridge setup` as your user".
    It never touches user sessions.
- [x] Run `lintian` if available. Build locally. Install in a
      docker/podman container if available (Ubuntu 24.04, Debian 13, and
      Ubuntu 26.04 if the image exists): `pcbridge --version`,
      `pcbridge doctor --json`, and the non-live suites against the
      installed package.
- [x] **CI workflows**, each with its own badge in the README:
  - `ci.yml` on every push and PR: Python non-live suites on 3.12/3.13
    (plus 3.14 if available), gjs extension tests, the readiness check in
    in-process mode, the English guard, and a doc link check.
  - The existing `native.yml`.
  - `package.yml`: build the `.deb`, run a container install matrix.
  - `release.yml` on tag `v*`: build the artifacts, `sha256sums`, and a
    **draft** GitHub release.

  Move `actions/*` to versions that do not use the deprecated Node 20.
- [x] **Headless GNOME smoke in CI.** Try it with a time box of 1 h: run
      `gnome-shell --headless --virtual-monitor` inside the container, load
      the extension, and do one capture. If it is not feasible on GitHub
      runners, record why in section 7 and skip it.
- [x] **Runtime platform check.** Detect GNOME Shell version, session type
      and the availability of Mutter ScreenCast and RemoteDesktop, then
      report capabilities instead of crashing on unknown versions.
- [x] **Commit(s)**: `build(deb): …`, `ci: …`.

### Step 10 — Install 2.0 on eymistaken's machine through the product path  (time box: 4 h)

- [x] **Build the release artifacts** from the branch HEAD.
  - If `sudo -n` works, install the `.deb`.
  - Otherwise do a **user-level install** of the same wheel and helper
    (`~/.local/share/pcbridge/venv` + `~/.local/bin/pcbridge` + user units).
    The CLI must know which install kind it is.
  - Record which path was used. If it was the user install, add the `.deb`
    install command to section 9.
- [x] **Before touching anything**, back up:
  - the client configs,
  - the shell rc,
  - the systemd user units,
  - `config.toml`,
  - the extension directory.

  Store the backups in `$XDG_STATE_HOME/pcbridge/backup-<timestamp>/`, with
  a `ROLLBACK.md` in that folder giving exact commands.
- [x] Run `pcbridge setup --yes`:
  - migrate the config (the legacy file stays in place as a fallback),
  - enable the units,
  - install the new extension files (active after re-login; the old one
    keeps working, I6),
  - `pcbridge connect --client all`,
  - rewrite the aliases.
- [x] **Keep the repo's `.venv` and the legacy command working** (I2). eymistaken's
      running Claude Desktop and Claude Code keep their old stdio processes
      until they restart.
- [x] **Verify everything with fresh processes.**
  - The readiness check (`--desktop`) against every registered command and
    the legacy command.
  - `claude mcp list` and `codex mcp list` (connection check).
  - At most two trivial real `claude -p` calls that use one pcbridge tool.
  - The full live desktop suite through the relay, with all four flags.
  - `pcbridge doctor --json` all green, or explained.
  - Record all numbers in section 8 against the baselines (I9).
- [x] **Hand the product path to eymistaken.** Run a sample of the commands eymistaken
      will actually use. Confirm that nothing needs to be opened: start
      from the daemon stopped, then use a fresh client.
- [x] **Commit** any fixes this surfaced. Tick the "installed" box in
      section 1.

### Step 11 — Documentation: rewrite and prune  (time box: 5 h)

**Goal:** a small, current, English doc set. No stale journals, no
duplicated status. Git history keeps everything deleted here, so remove
freely **after** extracting what is still true and useful.

- [ ] **Target set:**
  - `README.md`: the product front page. What it is, the supported
    platforms matrix, install (`.deb`, git), quick start, the
    always-ready architecture diagram, safety model, measured numbers,
    links, CI badges. Remove the "packaging for other distributions was
    dropped" text.
  - `CHANGELOG.md`: the 2.0.0 entry in eymistaken's format, which is:
    - Markdown, English, no emojis, formal technical tone;
    - a version header with the date;
    - `Fixed` / `Added` / `Changed` / `Removed` sections;
    - each entry says WHAT changed and WHY (root cause);
    - it ends with an `API` section covering breaking changes, entry
      points, config migration, error-code stability and binary
      compatibility of the native helper protocol.

    Summarize the 1.x history in one short section.
  - `docs/install.md`, `docs/usage.md` (tool catalog and permission map,
    rewritten from `KULLANIM.md`), `docs/configuration.md` (or an
    explicit pointer to the commented `config.example.toml`),
    `docs/security.md` (the honest assessment from `KURULUM.md`, updated
    for the daemon and socket), `docs/troubleshooting.md`,
    `docs/architecture.md`.
  - `docs/dev/contributing.md`: adding a tool or an agent, test commands,
    live-test flags, and the safety rules for testing on your own desktop.
  - `docs/dev/measured-facts.md`: the valuable "Ölçülmüş makine gerçekleri"
    from `CLAUDE.md` and the key findings from `WALKTHROUGH.md`, `PLAN.md`
    and `UYGULAMA.md`, condensed and in English. Keep the numbers and the
    "why". This is the project's hard-won knowledge; do not lose it.
  - `docs/dev/desktop-rules.md`: the still-valid parts of `KURALLAR.md`.
  - `docs/native/*`: keep, update to 2.0, English.
  - `gnome-extension/README.md`: update.
  - `CLAUDE.md`: rewrite it short (target under ~12 KB). It keeps project
    rules, commands, the do-not-touch list, the safety rules and links to
    `docs/dev/*`. The rules are otherwise unchanged except "English for all
    user-facing text" and the new architecture. `AGENTS.md` stays a thin
    pointer.
  - A short `ROADMAP.md` or a README section with the open items: #8
    (cursor overlay on a physical mouse), #10, #11, Faz 8 legacy
    retirement, plus anything cut in this run. This replaces the status
    role of `WALKTHROUGH.md`.
- [ ] **Delete with `git rm` after extraction:**
  - `WALKTHROUGH.md`, `PLAN.md`, `UYGULAMA.md`, `ADIMLAR.md`,
    `GOREV-kurallar.md`, `GELISTIRME.md`, `KURULUM.md`, `KULLANIM.md`,
    `KURALLAR.md`;
  - `JARVIS.md` (a proposal, not documentation; mention in `ROADMAP.md`
    that it exists in history).

  If something in one of them is still true and has no home in the target
  set, give it one before deleting.
- [ ] **Untracked clutter** in the repo root (`config.toml.yedek-*`,
      `graphify-out/`, `__pycache__`): do **not** delete it. Make sure
      `.gitignore` covers it, and list it in section 9 so eymistaken can decide.
- [ ] **Verify.** All relative links resolve (the link check from step 9).
      No doc still mentions removed scripts, Gemini Spark as a target,
      "Turkish user messages", or the per-client stdio server.
- [ ] **Commit**: `docs: rewrite for 2.0 and remove superseded journals`.

### Step 12 — Final verification, release, push  (time box: 2 h)

- [ ] Bump the version to `2.0.0`, and finalize `CHANGELOG.md` with
      today's date.
- [ ] **Full verification** on the final commit:
  - all non-live suites,
  - `cargo test --no-fail-fast`,
  - gjs tests,
  - the English guard,
  - the link check,
  - the readiness check (`--desktop`) on every registered command and the
    legacy command,
  - the live desktop suite (all four flags),
  - `pcbridge doctor --json`.

  If the installed build differs from the final commit, reinstall through
  the same product path first. Record the final numbers against the
  baselines.
- [ ] **Secret scan** before pushing, as `CLAUDE.md` requires:
  - `config.toml` is not tracked,
  - neither the real password nor the static token appears anywhere in
    `git log -p origin/main..HEAD`,
  - no OAuth DB, no tokens, no backup files are tracked.
- [ ] Delete this file (`git rm YAPILACAKLAR.md`) as part of the final
      commit. Its outcome now lives in `CHANGELOG.md`, `ROADMAP.md` and the
      commit history. Before that, copy section 9 into the final message to
      eymistaken.
- [ ] Fast-forward `main` to `productize/v2`. If a fast-forward is not
      possible, rebase and re-run the suites; never force-push. Create an
      annotated tag `v2.0.0`. Push `main` and the tag. Confirm that CI
      started and that the release workflow produced a **draft** release.
      If the push fails (for example, auth), do not retry blindly; put it in
      section 9.
- [ ] **Final message to eymistaken.** It must be **in Turkish, short**, and
      contain:
  - what changed;
  - the numbers against the baselines;
  - the section 9 list (for example: log out and back in to load the new
    extension, check its panel indicator, `sudo apt install tesseract-ocr`,
    restart Claude Desktop so it uses the relay, the `.deb` install command
    if a user install was used);
  - where the backups and `ROLLBACK.md` are.

---

## 7. Decisions log

| When | Step | Decision | Why |
|---|---|---|---|
| 2026-09-22 | 0 | All work happens in a separate git worktree, `/home/eymistaken/Belgeler/Pcbridge-v2`, branch `productize/v2`. The original checkout `/home/eymistaken/Belgeler/Pcbridge` stays on `main` untouched until step 12 (its untracked copy of this file only points here). | The live `pcbridge.service`, every legacy stdio client (`<repo>/.venv/bin/python -m pcbridge.server --stdio`) and the GNOME extension symlink all run from the original checkout. Half-finished code there would break I1/I2 the moment Claude Desktop restarts or the service restarts on failure. |
| 2026-09-22 | 0 | The worktree has its own `.venv`, pinned to the exact `pip freeze` of the live venv (mcp 1.29.0, evdev 1.9.3, fastmcp 3.4.5, pillow 12.3.0), with a `pcbridge.pth` pointing at the worktree, and a copy of `pcbridge/_native`. Tests use `PCBRIDGE_CONFIG=/home/eymistaken/Belgeler/Pcbridge/config.toml` (read only). | A fresh `pip install -r requirements.txt` resolved mcp 1.30.0 / evdev 2.0.0; measurements must compare like with like. |
| 2026-09-22 | 0 | My own process is NOT a pcbridge job: cgroup `app-com.anthropic.Claude-14558.scope` (parent chain: claude -> claude-desktop -> gnome-shell -> systemd --user). Restarting `pcbridge.service` does not kill me. | Section 4 "Do not kill yourself". |
| 2026-09-22 | 0 | `sudo -n true` needs a password, so step 10 is a **user-level install** (`~/.local/share/pcbridge/venv`, `~/.local/bin`, user units). The `.deb` install command goes to section 9. No docker/podman, uv, pipx, lintian or nfpm on this machine: packages are built with `dpkg-deb`, venvs with `python3 -m venv` + pip, and the container matrix and lintian run only in CI. | Measured in step 0. |
| 2026-09-22 | 0 | `~/.local/bin/pcbridge` already exists and is a Hermes wrapper (`hermes -p pcbridge`). The maintainer answered in the kickoff: he does not use Hermes. Checked: no alias, systemd unit, MCP registration or the PcBridgeDesktop app refers to it (only `~/.hermes/profiles/pcbridge` exists, left untouched). Step 10 re-checks; if still nothing depends on it, `gio trash` it, otherwise move it to `~/.local/bin/hermes-pcbridge`. Then `~/.local/bin/pcbridge` becomes the 2.0 CLI. | Maintainer's explicit instruction. |
| 2026-09-22 | 0 | Identity rule: the maintainer's personal first name must not appear anywhere in the repository or on origin. Use `eymistaken` / `Eymistaken` or "the maintainer". This file's committed copy was rewritten accordingly before its first commit. New commits use `-c user.name=Eymistaken -c user.email=<id>+Eymistaken@users.noreply.github.com` (the address of the repository's initial commit), without changing git config. The pre-push scan checks for the name (case-sensitive, word-bounded; `ÇALIŞ...` style Turkish words are false positives of `grep -w -i`). | Maintainer's explicit instruction at plan approval. The author e-mail of the 154 older commits cannot change without a force-push; listed in section 9. |
| 2026-09-22 | 0 | The readiness check spawns every client's registered command with the environment that client really provides: Claude Desktop only `HOME LOGNAME PATH SHELL USER`; Codex the full session but `DBUS_SESSION_BUS_ADDRESS` as the literal `$DBUS_SESSION_BUS_ADDRESS`; Claude Code the full session. It strips its own `CLAUDE*`/`MCP_*` variables. | Both environments broke pcbridge before (CLAUDE.md). A check that spawns with a healthy environment would pass while the real client fails. |
| 2026-09-22 | 0 | `tests/readiness/bench.py` sleeps 0.25 s before each call. | The desktop gate refuses more than 10 actions per second; without pacing the benchmark measured the rate limiter. |
| 2026-09-22 | 0 | The PcBridgeDesktop app (`~/Masaüstü/app/PcBridgeDesktop`) talks to pcbridge over HTTP (`http://127.0.0.1:8765/mcp`, static token) and reads `state_dir/desktop_unlock.json` and the jobs directory **directly from disk**. The daemon keeps the HTTP listener on 8765 and both on-disk formats stay backward compatible. The readiness check probes HTTP too. | Keep that consumer working (step 0 inventory). |
| 2026-09-22 | 1 | Build backend: setuptools with a small `setup.py`. Its `build_py` hook copies the root-level assets (GNOME extension, systemd units, udev rule, modules-load file, `config.example.toml`, `skills/`) into `pcbridge/_assets/` inside the built package; `pcbridge/assets.py` resolves an asset from `_assets/` when installed and from the repository root in a checkout. When `pcbridge/_native` holds a built helper, the wheel is tagged `py3-none-linux_x86_64` instead of pure. | Keeps every source file where docs, tests and the live extension symlink expect it, while an installed package still carries everything setup needs. |
| 2026-09-22 | 1 | Exact dependency versions live in `packaging/constraints.txt` (the `pip freeze` of the reference install); `pyproject.toml` keeps ranges plus the `fastmcp==3.4.5` pin. Product installs use `pip install -c packaging/constraints.txt`. | A fresh resolve picked mcp 1.30.0 and evdev 2.0.0, which were never tested. Ranges stay loose so distro Pythons 3.13/3.14 can still resolve. |
| 2026-09-22 | 1 | Python >= 3.12. The only pre-3.11 code was the `tomli` fallback in `config.py`; removed, and dropped from `requirements.txt`. | Measured: grep for version_info, removed stdlib modules (imghdr, cgi, pipes, audioop, telnetlib, crypt, distutils), utcnow and pkg_resources found nothing else. |
| 2026-09-22 | 2 | Config search order: `-c` > `$PCBRIDGE_CONFIG` > `$XDG_CONFIG_HOME/pcbridge/config.toml` > legacy `<repo>/config.toml`. **When both the XDG and the legacy file exist, the XDG file wins**; the legacy file is never modified by migration. Loading the legacy file adds an English warning pointing at `pcbridge setup`. | Old stdio processes (old code) keep reading the repo file, so it must stay intact; new code must prefer the migrated file. |
| 2026-09-22 | 2 | `config_version = 2`. A file without the key is version 1. A default that changes in a later version is recorded in `_PINNED_DEFAULTS`: the loader gives an unmigrated older file its old default, and the migration writes the old value into the file. First use: `[desktop] ocr_languages` default `tur+eng` → `eng` for new files (tesseract's Turkish data is not installed by default); version-1 files keep `tur+eng`. | I8: migration must not change effective settings. A product default must not depend on the author's language. |
| 2026-09-22 | 2 | Migration is text-preserving (comments and layout kept): it prepends `config_version` and inserts pinned keys at the top of their section, re-parses the result before writing, writes atomically with mode 0600 (directory 0700), and backs up any existing destination as `<name>.backup-<timestamp>` (0600) first. Idempotent. | No TOML writer in the standard library, and rewriting through one would drop the user's comments. |
| 2026-09-22 | 2 | Unknown config keys produce English warnings (logged at startup, kept in `Config.warnings` for doctor), never a crash. A loose file mode (group/world readable) is a warning. Invalid TOML is a one-line English message instead of a traceback. | Step 2 requirement; a typo must not take the non-desktop tools down. |
| 2026-09-22 | 2 | `[desktop] keyboard_layout` is kept as an accepted key but documented as unused (default now empty). `config.example.toml` had `default_agent` inside `[limits]`, which is where the maintainer's file got the same misplacement from; the example now has it at the top, and the warning for that exact mistake says where the key belongs. | Removing an accepted key would make old files warn for nothing; the misplacement is a real (harmless, default-equal) bug. |
| 2026-09-22 | 2 | Agent CLIs are located by `pcbridge/executables.py`: configured path as is, then PATH, then `~/.local/bin ~/bin ~/.npm-global/bin ~/.bun/bin ~/.cargo/bin ~/.deno/bin /usr/local/bin` and nvm node versions (newest first). `agent_run` and `computer_task` run the resolved absolute path; `list_agents` uses the same lookup instead of `bash -lc command -v`. The systemd unit no longer sets PATH, DISPLAY (`ensure_session_env` derives it) or a Gemini description; its working directory is `%h`. | A systemd daemon does not see nvm/npm/bun PATH entries from ~/.bashrc; measured here: `gemini` lives only in `~/.nvm/versions/node/v20.20.2/bin`. |
| 2026-09-22 | 3 | Daemon design as planned: raw MCP JSON-RPC per unix-socket connection, served by FastMCP's low-level `_mcp_server.run()` over memory streams with the exact stdio framing (`model_dump_json(by_alias, exclude_none)` + newline), `_current_transport` set to `stdio` per connection (no OAuth, same as stdio), HTTP served by `run_http_async` in the same event loop. One preamble line each way (relay → daemon: version, protocol 1, client pid, cwd, allow-listed env; daemon → relay: version, protocol, pid). The relay is a byte pipe that only peeks at message heads. | Measured feasible in plan mode; parity measured below. |
| 2026-09-22 | 3 | Per-session state (new `pcbridge/sessionctx.py`): the `ui_dump` short-id registry, the gate's per-second rate window and the gate's admitted token are per MCP session (token now a ContextVar instead of a thread-local); a session is a socket connection (SessionInfo) or an MCP ServerSession (HTTP, in-process). `screen_capture` path-vs-link and `inline_images=auto` are decided per call from the session's transport. Jobs, `shell_run` and executable lookup use the daemon environment plus the client's allow-listed variables (PATH merged client-first, SSH_AUTH_SOCK, LANG/LC_*, proxies, PWD, TERM); CLAUDE*/MCP_* are never passed. Audit lines carry the client name from `initialize`. | B3 of the approved plan: these were per-process before and would leak across clients in one daemon. |
| 2026-09-22 | 3 | Held keys/buttons: when a local session ends and NO local session remains, the daemon releases held input (a gone client can no longer release it). With other sessions still connected it does not, because it cannot tell whose hold it is; `hold_max_seconds` auto-release still applies. | Conservative: never release another live client's deliberate hold; before 2.0 the client's own process exit released it. |
| 2026-09-22 | 3 | Jobs started by the systemd daemon run in their own transient scope `pcbridge-job-<id>.scope` (`systemd-run --user --scope`), only when running under systemd (INVOCATION_ID) and systemd-run exists; otherwise the old Popen path. Measured: a scoped job survived kill -9 and `systemctl stop` of its parent service; an explicit stop of the scope ends it; cost ~12 ms per job. Consequence: `systemctl --user stop pcbridge` no longer kills jobs; the emergency stop that does is `pcbridge stop` (step 4). | I5 and fragility: a daemon crash or update restart must not kill agent jobs (before 2.0 a client's server dying did not kill its jobs either). |
| 2026-09-22 | 3 | Legacy entry points: `pcbridge/server.py` is now a tiny dispatcher (the old module moved to `pcbridge/app.py`; `from pcbridge.server import X` still works via module `__getattr__`). `python -m pcbridge.server --stdio` → relay; `python -m pcbridge.server` (old unit) → daemon that serves HTTP and binds the socket only when systemd passes one; `pcbridge serve` also binds the socket itself. A client that passes `-c/--config` keeps its own in-process server (the daemon serves one config). `PCBRIDGE_NO_DAEMON=1` forces in-process. | I2: old registrations must keep working and must not grab the socket from a test or a second config. |
| 2026-09-22 | 3 | Relay fallback: at start, if the socket is unreachable (and `pcbridge.socket` is installed, after `systemctl --user start pcbridge.socket` and ≤2 s), the relay execs the classic in-process server (zero extra hop, identical behavior), marked `PCBRIDGE_MODE=degraded: in-process (...)`, visible in system_status. Mid-session loss: in-flight requests get JSON-RPC error -32000 with data.retryable=true (an unanswered `initialize` is resent instead), the relay reconnects for up to 10 s (socket activation), replays initialize under a private id and swallows its answer; if the daemon stays away it continues through an in-process child. | I3: never hang, never require a client restart. |
| 2026-09-22 | 3 | MCP SDK 1.29 bug worked around in `app.py`: after `notifications/cancelled`, a synchronous tool that finishes later makes the SDK call `respond()` twice and its assertion kills the whole session (the in-process stdio server died in the parity test; pre-2.0 had the same bug). A late answer to a cancelled request is now dropped, which is what the SDK code intends after the assert. | Found by the parity test; FastMCP/mcp versions stay pinned. |
| 2026-09-22 | 3 | Units: `pcbridge.socket` (`%t/pcbridge/mcp.sock`, 0600, dir 0700, WantedBy=sockets.target) and `pcbridge.service` (Requires the socket, `ExecStart=<python> -m pcbridge serve`, Restart=on-failure, RestartSec=1, RestartForceExitStatus=75 for the idle self-restart, ExecStopPost lock kept, WantedBy=default.target, Also=pcbridge.socket). Template placeholder is `__PYTHON__`. Tested as temporary units `pcbridge-v2test.{socket,service}` on `%t/pcbridge-test/mcp.sock` and port 18765 so the live service was never touched. | I4 and 'no live changes before step 10'. |
| 2026-09-22 | 3 | Memory is compared as total footprint, not daemon RSS alone: the daemon holds HTTP, desktop helpers and a 128-thread limit for every client (117 MB measured), each relay is 14 MB. Before: service 86 MB + one 92 MB stdio server per client (4 at the start of this run = ~454 MB). After, same clients: ~117 + 4 × 14 = ~173 MB. | I9 lists 'daemon or service RSS' as a baseline; the per-process number went up by design while the machine total went down by ~60 %. |
| 2026-09-22 | 3 | The temporary test units `pcbridge-v2test.{socket,service}` (socket `%t/pcbridge-test/mcp.sock`, HTTP 18765, `XDG_DATA_HOME=%t/pcbridge-test/data`) stay installed in `~/.config/systemd/user` while the work continues and are moved to the trash (`gio trash`) in step 10. | They let every later step be verified through socket activation without touching the live pcbridge.service. |
| 2026-09-23 | 4 | Old scripts: `connect.sh`, `doctor.sh`, `remote.sh`, `run.sh` became forwarding wrappers (English deprecation note on stderr, then `pcbridge connect|doctor|remote|serve`); `install.sh` is now a git bootstrap (venv, editable install with constraints, optional native build, `pcbridge setup`); `setup_uinput.sh` stays; `add_client.py` and `capture.sh` were removed (`git rm`): both only served the abandoned Gemini Spark OAuth flow and nothing else referenced them. New `packaging/install-user.sh WHEEL` does the no-root user install into `~/.local/share/pcbridge/venv`. | Step 4 asks to record which scripts were wrapped and which removed. |
| 2026-09-23 | 4 | `pcbridge remote stop` (and so the `bridgekapat` alias) now only closes the Tailscale funnel; it no longer stops pcbridge.service, because local clients depend on the daemon. `pcbridge stop` is the new full stop (grant closed, daemon stopped, every pcbridge-job scope and remaining job ended); `pcbridge lock` stays the desktop emergency stop. | Stopping the service used to be harmless for local clients (they had their own processes); with the daemon it would not be. |
| 2026-09-23 | 4 | Clients, aliases and units point at a stable launcher: `~/.local/bin/pcbridge` for the user install (a symlink setup maintains), `/usr/bin/pcbridge` for the .deb, the venv script for git/pip installs. A foreign file at `~/.local/bin/pcbridge` is moved into the run's backup directory, never deleted. | A reinstall or a switch of install kind then changes one symlink instead of three client configs. |
| 2026-09-23 | 4 | `pcbridge setup` restarts pcbridge.service into the daemon only when idle (no running job, no open desktop grant; waits up to `--idle-wait`, default 600 s) and enables pcbridge.socket only after that restart; the relay only starts pcbridge.socket on demand when the unit is enabled (checks the `sockets.target.wants` link). Relay handshake wait lowered from 20 to 10 s. | An enabled socket in front of a still-running pre-2.0 service would accept connections nobody answers (I3, I5). |
| 2026-09-23 | 5 | Comments and internal docstrings stay Turkish for now; only strings a user or model reads are English | Step 5 scope is what reaches a human or model; comment translation belongs to the docs pass and would triple the diff |
| 2026-09-23 | 5 | Old Turkish flags of gnome-extension/install.sh (--kur, --kaldir, --durum...) kept as aliases; on-disk names gorunur-imlec and the extension UUID unchanged | Renaming what scripts or state files refer to by name silently breaks existing setups |
| 2026-09-23 | 6 | topology_id format unchanged except a ',p' suffix on monitors whose pixel ratio differs from their scale | An unconditional change would make every 1.x process (old stdio clients, old pcb-do) refuse shots taken by 2.0 on this machine (I6) |
| 2026-09-23 | 6 | Area cap default = 1536x864 (the current 16:9 output), pinned to 0 for version-1 configs | Keeps this machine's pictures byte-identical and an unmigrated 1.x file meaning what it meant. Whether the Anthropic API itself shrinks 1536x864 (~1.33 MP) is UNMEASURED; not changed without a measurement |
| 2026-09-23 | 6 | xrandr fallback reports scale 1.0 / transform 0 instead of guessing | xrandr --listmonitors does not carry them; with scale 1.0 a scaled frame is refused by check_source_size rather than mis-mapped |
| 2026-09-23 | 6 | No uinput test in the headless shell | Headless Mutter opens no input devices; the kernel device would be picked up by the real session and move the user's pointer |
| 2026-09-23 | 8 | pcbridge never deletes job records; doctor warns above 1 GB | They are agent transcripts (user data) and permanent deletion is off limits; a warning with a gio trash hint keeps the disk safe without losing anything |
| 2026-09-23 | 8 | Configuration and unwritable-state errors exit 78, and the unit has RestartPreventExitStatus=78 | A broken file stays broken until edited; retrying every second only hits the start limit. The socket stays active, so the next client starts pcbridge again after the fix |
| 2026-09-23 | 7 | Screen-reading tools carry openWorldHint=true while staying readOnlyHint=true | Screen and window text comes from other applications and the web; the hint lets a client treat it as untrusted content |
| 2026-09-23 | 7 | Kill-switch shortcut off by default | Any default can collide with a user's own binding; the panel menu and bridgekilit already give a kill switch without one |
| 2026-09-23 | 7 | Without status.json the indicator falls back to 'pcbridge' in PATH or ~/.local/bin for the kill switch | A 1.x server writes no status.json. On this machine ~/.local/bin/pcbridge is still the Hermes wrapper until step 10 replaces it; a 2.0 server always names its own CLI, so the fallback only matters for a 1.x server |
| 2026-09-23 | 9 | Package built with plain dpkg-deb, venv via python3 -m venv + pip (no uv, nfpm or lintian on this machine) | Only dpkg-deb is available locally; CI can add lintian |
| 2026-09-23 | 9 | Pushed the productize/v2 branch (not main, no tag) before step 12, after the secret and name scans | The CI workflows can only be checked on GitHub, and the headless smoke's feasibility is only known there; a branch can be fixed with normal commits, main stays untouched until step 12 |
| 2026-09-23 | 9 | Headless smoke stays continue-on-error although it passed | It depends on GNOME/PipeWire packages of the runner image; a change there should not block a release, and a failure is still visible |

## 8. Progress log and measurements

Append-only. One line per meaningful event, with numbers.

- 2026-09-22 step 0 — **Environment.** Zorin OS 18.1, GNOME Shell 46.0 (Wayland), Python 3.12.3 (system and venv), systemd 255. `sudo -n`: password required. Available: `dpkg-deb`, `gjs`, `cargo` (toolchain 1.95.0), `gh` (logged in, scopes repo+workflow), `python3 -m venv`, system `gi` with Atspi. Missing: docker, podman, uv, pipx, lintian, nfpm, tesseract. `gnome-shell --help` lists `--headless` and `--virtual-monitor WxH@R`. Monitors: 2 × 1920×1080 @1.0 side by side (from CLAUDE.md, unchanged). Own cgroup: `app-com.anthropic.Claude-14558.scope`. No pcbridge jobs running.
- 2026-09-22 step 0 — **Inventory of consumers.**
  - Claude Code: `~/.claude.json` top-level `mcpServers.pcbridge` (user scope), `type=stdio`, `/home/eymistaken/Belgeler/Pcbridge/.venv/bin/python -m pcbridge.server --stdio`.
  - Codex: `~/.codex/config.toml` `[mcp_servers.pcbridge]`, same command, plus per-tool subtables `[mcp_servers.pcbridge.tools.<tool>] approval_mode = "approve"` for desktop_unlock, computer_task, window_focus, shell_run, list_agents (must be preserved).
  - Claude Desktop: `~/.config/Claude/claude_desktop_config.json` `mcpServers.pcbridge`, same command.
  - PcBridgeDesktop app: HTTP `127.0.0.1:8765/mcp` + reads `~/.local/state/pcbridge` directly.
  - Aliases: `~/.bashrc` block `# >>> pcbridge >>>` … `# <<< pcbridge <<<` (lines 220–226): `bridgeac`/`bridgekapat`/`bridgedurum` → `<repo>/remote.sh start|stop|status`, `bridgekilit` → `<repo>/.venv/bin/python -m pcbridge.cli.lock`. No `sparkac` alias exists any more.
  - systemd user units: `~/.config/systemd/user/pcbridge.service` (enabled, active, `WantedBy=default.target`, runs `<repo>/.venv/bin/python -m pcbridge.server`, pid 2199), no socket unit.
  - `~/.local/bin`: `pcb-do` and `pcb-shot` symlinks into `<repo>/bin`; `pcbridge` = Hermes wrapper (see section 7).
  - Extension: `~/.local/share/gnome-shell/extensions/pcbridge-gorunur@eymistaken.local` is a **symlink** to `<repo>/gnome-extension/pcbridge-gorunur@eymistaken.local`, enabled.
  - `state_dir` = `~/.local/state/pcbridge`: `audit.log desktop_execution.json desktop_execution.lock desktop_unlock.json desktop_unlock.lock jobs/ (162 jobs) oauth.db shots/`.
  - Running processes at start: the service (pid 2199), two Claude Desktop stdio servers (pids 15144, 15209), one Claude Code stdio server (pid 19942).
- 2026-09-22 step 0 — **Baseline tests (commit 850c334).** `test_models.py` 106 pass; `test_desktop.py` 614 pass; `test_test_safety.py` 1 OK; `unittest discover -s tests/contracts -t .` 446 OK; `-s tests/integration -t .` 24 OK (1 skipped); gjs `test_cursor.js` 17, `test_state.js` 31, `test_window_control.js` 22; `cargo test --workspace --locked --no-fail-fast` 168 passed in 26 binaries, with `--features pcbridge-native/test-harness` 190 passed; `test_e2e.py` (`PCBRIDGE_TEST_NO_AGENT=1`, against the live service) 262 passed, 0 failed, 9 skipped. Note: a bare `unittest discover` from the root finds nothing (no `tests/__init__.py`).
- 2026-09-22 step 0 — **Baseline performance** (`tests/readiness/bench.py`, legacy command from the original checkout, median of 5, 0.25 s pacing): cold stdio start to `tools/list` **692.8 ms**; `system_status` **135.2 ms**; `system_capabilities` **35.1 ms**; first `screen_capture` of one monitor (opens the share) 345.4 ms; `screen_capture` one monitor **301.7 ms**; both monitors **770.7 ms**; `ui_dump` of the focused window **32.7 ms**; `window_list` **14.1 ms**; `window_focus` on the already-focused Claude window **29.7 ms**; stdio server RSS **92.4 MB** (tree, after desktop_lock); service RSS **85.9 MB** (pid 2199).
- 2026-09-22 step 0 — **Readiness check** `tests/readiness/check.py` against the live setup: PASS claude-code (tools/list 719.5 ms), codex (666.2 ms, broken-DBus env), claude-desktop (732.0 ms, minimal env), legacy (724.9 ms), http (healthz 16.4 ms, system_status 125.8 ms). `--desktop` on legacy: `desktop_unlock` 134.6 ms, `desktop_lock` 132.9 ms, grant closed afterwards (`until` 0).
- 2026-09-22 step 0 — Findings that change later steps (from the read-only survey during planning): `ui_dump` short ids, held input, the gate rate limiter and the thread-local `last_token` are per-process today and must become per-session in the daemon; jobs started by a systemd daemon would die with it (crash or restart), today they do not; Mutter's `layout-mode` is never read (physical layout mode would size HiDPI monitors wrongly); the xrandr fallback hard-codes scale 1 and transform 0; `keyboard_layout` is a dead setting; `doctor.sh` has 10 headings, not 35; `install.sh` ends by enabling the service although three comments say it does not; the packaged native helper's build id is `ee96fde90d06-dirty`, one commit behind HEAD.
- 2026-09-22 step 1 — Wheel `pcbridge-2.0.0.dev0-py3-none-linux_x86_64.whl` (2.6 MB, includes the native helper with its exec bit). Installed into a throwaway venv outside the repo with the constraints file: `pcbridge --version` → `pcbridge 2.0.0.dev0`; the suites run from a copy of `tests/` outside the repo so they import the installed package. First run found two real bugs: `computer_task` located `skills/computer-use/SKILL.md` relative to the repository (every installed layout would fail with DEPENDENCY_MISSING; now resolved through `pcbridge.assets`), and tests loading `atspi_helper.py` by repository path. After the fixes, installed: models 106, desktop 614, safety OK, contracts 446 OK, integration OK (7 skipped: the 3 native-harness classes need `rust/` and now skip with a reason). Repo: models 106, desktop 614, contracts OK, integration OK (1 skipped). Readiness: legacy (worktree) tools/list 680.0 ms, installed `pcbridge stdio` 682.2 ms, both PASS.
- 2026-09-22 step 2 — `pcbridge/paths.py` (XDG config/state/log/cache/data/runtime, runtime dir created 0700, socket path `$XDG_RUNTIME_DIR/pcbridge/mcp.sock`, overridable with `$PCBRIDGE_SOCKET`). Existing `/run/user/1000/pcbridge` is already 0700. The maintainer's real config migrated in a temp dir: effective settings identical (dataclass comparison, `PCBRIDGE_TEST_REAL_CONFIG`), dest mode 0600; its only warning is the misplaced `[limits] default_agent`. INSTRUCTIONS no longer name ZorinOS or a two-monitor layout; they tell the model to call `screen_info`/`system_capabilities`. `notify`'s default title is now `pcbridge` (was `Gemini`). Remaining hits of 1920/3840/DP-/eymistaken in code are comments, docstrings, examples, the extension UUID and the D-Bus name (identifiers, unchanged); `skills/computer-use/SKILL.md` still hard-codes the layout for the model and is rewritten in step 5. Suites: models 106, desktop 614, contracts 459 OK (+13 config, +5 executables; 1 skip = real-config test without the env var), integration 24 OK, gjs 17/31/22. Readiness: legacy (worktree) 681.7 ms, packaged 684.5 ms, PASS.
- 2026-09-22 step 3 (in progress) — Relay import 12.8 ms. Through relay + daemon: cold client start to tools/list **22–28 ms** warm daemon (baseline 692.8 ms); socket-activated with the daemon stopped **711 ms** (budget 3 s); fallback with no daemon at all 700.1 ms (+1 %, within I9). Faults (tests/readiness/faults.py against the test units): kill -9 mid-call → in-flight error after 9.8 ms, next call 1981.6 ms; daemon stopped → next client 743.5 ms served by the daemon; stale socket → in-process 719.6 ms; XDG_RUNTIME_DIR unusable → in-process 700.3 ms; three clients at once 55.3 ms wall; background job scope active before and after kill -9. Parity (tests/readiness/parity.py, in-process vs relay, normalized ids/times/image data): 12/12 SAME incl. tools/list 45907 B, structuredContent, error result, unknown tool, cancellation, background job + job_status, desktop_unlock, screen_capture with image, desktop_lock. Two bugs found and fixed on the way: the daemon's line reader dropped received bytes, and a blocking listening socket stalled anyio's event loop (accept() is called directly; the socket is now non-blocking). Suites: models 106, desktop 614, contracts 469 OK, integration 24 OK, gjs 70.
- 2026-09-22 step 3 — Done. Per-call relay overhead (40 calls each, same daemon vs PCBRIDGE_NO_DAEMON): ping p50 0.43 vs 0.46 ms, system_capabilities 30.30 vs 30.19 ms (+0.11), tools/list 1.75 vs 1.71 ms (+0.04): **overhead ≈0.1 ms** (budget 5 ms). Through relay + daemon vs baseline: screen_capture one monitor **293.7** (301.7) ms, both **761.1** (770.7), ui_dump **26.7** (32.7), window_list 11.4 (14.1), window_focus 25.5 (29.7); system_status 20-call median 121.2 ms vs 1.x 132.0 ms. Relay RSS 14.2 MB, daemon RSS 117 MB. test_e2e.py (NO_AGENT) against the daemon's HTTP on 18765: 262 passed, 0 failed, 9 skipped (= baseline). Live, all four flags: test_desktop.py 654 passed 0 failed; tests/live 61 OK (4 skipped by design, 'covered by WindowOperationsLive'). Real computer_batch through the relay into an empty gnome-text-editor: 34 characters incl. ğüşıöç pasted and verified on a screenshot, then cleared and discarded via ui_dump + ui_click (AT-SPI id, no coordinates); an empty 'Yeni Belge' editor window was left open. Idle self-restart: a new version stamp was deferred while a job ran ('1 job(s) running'), then the daemon exited 75 and systemd restarted it (NRestarts=1) once idle. New non-live tests: contracts/test_relay.py (4, fake daemon), contracts/test_sessionctx.py (4), integration/test_daemon.py (3, real daemon with a throwaway config: 36 tools, 3 sessions, SIGKILL mid-call → retryable error → stale socket replaced → next call ok). Suites: models 106, desktop 614, contracts 473 OK, integration 27 OK, gjs 70.
- 2026-09-23 step 4 — CLI subcommands: serve, stdio, setup, connect, doctor, status, lock, unlock, stop, remote, logs, report, update, uninstall, --version. Codex edit dry-run on the real ~/.codex/config.toml: only the two command/args lines change, the five approval_mode sub-tables stay, idempotent. `pcbridge doctor` on the real machine (worktree, before install): 28 ok, 13 warnings, 3 failures, all expected before step 10 (socket unit not installed, clients on the 1.x command, worktree launcher missing before the editable install) plus tesseract missing; it also showed the Tailscale funnel is currently OPEN on 8765. End-to-end `packaging/install-user.sh` into a throwaway HOME with stubbed systemctl/claude: venv created, launchers linked, units rendered with the venv python, config migrated from a copy of the real one, extension copied, Codex + Claude Desktop registered, alias block rewritten in place, fresh client got 36 tools in 793 ms. Found and fixed: doctor's readiness probe closed stdin before the answer arrived (a server ends the session at EOF). New tests: contracts/test_cli.py (6: version, connect with backups and idempotence, alias block, report redaction, doctor JSON, unit rendering). Suites: models 106, desktop 614, contracts 479 OK, integration 27 OK, gjs 70.
- 2026-09-23 step 5 (part 1) — Every user- and model-facing string in the Python package translated to English (~500 string lines in 40 modules: tools, batch, capture, apps, safety, uitree, atspi_helper, backends, config, app, auth consent page, models, diagnostics, CLI tools, jobs, tmux, OCR, monitors and the small desktop/native modules). The Rust accessibility helper's messages (action.rs, accessibility.rs, bus.rs, dispatch.rs; 20 strings) were translated to exactly the Python helper's wording, because the parity tests compare them byte for byte; native helper rebuilt (build b556f5b82db8-dirty). Error codes, categories, scopes and field names unchanged. Found on the way: `INSTALL_HINT` asked for Turkish tesseract data; the OCR report, job step labels (`→ arac`) and several `fail()` paths were still Turkish. Tests: ~130 assertions on Turkish text rewritten (fakes' Turkish data too); models 106, desktop 614, contracts 479 OK, integration 27 OK, cargo 190/168, gjs 70.
- 2026-09-23 Step 5 done. Inventory: 82 files carried user- or model-facing Turkish (63 in part 1: the Python package, Rust accessibility messages, tests; 19 in part 2: extension strings + metadata.json, 5 shell scripts, bin wrappers, config.example.toml rewritten in English, SKILL.md rewritten without this machine's layout). Error codes, scopes, field names, config keys and values unchanged. Guard: tests/contracts/test_english_only.py (6 tests: package strings, bin wrappers, SKILL + example config, extension, shell output, native helper). Suites: models 106, desktop 615, contracts 485 OK, integration 27 OK, gjs 17/31/22, cargo 168 passed, fmt clean.
- 2026-09-23 Step 6 code part. MEASURED 2026-09-23: this machine runs Mutter's PHYSICAL layout mode (GetCurrentState property layout-mode = 2, experimental-features empty). In that mode positions/sizes are framebuffer pixels, so 1.x divided a 4K@2 panel down to 1920x1080 while Mutter put its neighbor at x=3840 -- wrong table, wrong pointer axis, refused frames. Fixed in Python and Rust (layout_mode in the neutral state, Monitor.physical_layout, pixel_ratio). topology_id gains ',p' only when pixel ratio != scale, so ids at scale 1 (this machine) are byte-identical to 1.x and mixed-version shots keep working. xrandr fallback now normalizes its origin; scale/rotation/serial stay neutral and are documented. Picture policy: long edge 1536 kept, new area cap screenshot_max_pixels = 1536x864 (this machine's pictures unchanged; 2880x1800 goes 1536x960 -> 1457x911; scale=0/OCR exempt; v1 configs pinned to 0). Legibility note when shown share x UI scale < 0.5 (ultrawide 3440 at 45 %, 5120 at 30 %), suggests region=. Matrix: tests/fixtures/native/layout_matrix.json, 14 layouts, both modes; Python 14 tests, Rust 4 tests; mutation (re-dividing in physical mode) caught on both sides. More leftover Turkish found by a suffix scan (native client, AT-SPI, screencast helper, auth/app messages) and fixed; guard word list widened. Suites: models 106, desktop 615, contracts 499 OK, integration 27 OK, gjs 17/31/22, cargo 172.
- 2026-09-23 Step 6 live part, MEASURED in a headless gnome-shell 46 (--headless --virtual-monitor, own dbus-run-session bus and Wayland socket, ApplyMonitorsConfig method 1 = temporary; ~/.config/monitors.xml sha256 identical before/after; orphans collected with nested.sh --clean, inotify 58/128 before and after). Headless Mutter runs the physical layout mode (layout-mode 2). 4K@2 + 1080p: Mutter ACCEPTED the neighbor at x=3840 and REFUSED x=1920 ('Logical monitors not adjacent') -- proof that 1.x's divided table (1920x1080 + a 1920-unit phantom gap) was wrong. 2.0 table 3840x2160 + 1920x1080, canvas 5760x2160, topology 'v1|0,0,3840,2160,2.0000,0,0,p|3840,0,1920,1080,1.0000,0,1' = fixture; the native helper read the same string over live zbus. Screen sharing frames: 4K 3840x2160 (510 ms), 1080p 1920x1080 (240 ms), ultrawide 3440x1440 -> 1536x643 (531 ms), super-ultrawide 5120x1440 -> 1536x432 (590 ms), two portrait 90/270 1080x1920 -> 864x1536 (259/242 ms); every frame accepted by check_source_size; every topology equals the fixture. AT-SPI answers inside (0 windows, ~87 ms). NOT measured headless: uinput pointer accuracy -- headless Mutter opens no input devices, so a uinput event would move the REAL session's pointer; pointer mapping stays covered by the fixtures (absolute axis = canvas, which is now framebuffer pixels in the physical mode). Real machine afterwards (I9): topology unchanged 'v1|0,0,1920,1080,1.0000,0,0|1920,0,1920,1080,1.0000,0,1'. Live suite, four flags: test_desktop.py 653 passed + 2 failed -- both were live-only checks still looking for the old Turkish text ('acik degil', 'yayinda yok'), missed in step 5 because they only run with PCBRIDGE_TEST_CAPTURE; fixed, and the frame-size check now uses source_pixel_size; capture-only rerun 634/0. tests/live 61 OK (4 skipped by design). Grant closed afterwards.
- 2026-09-23 Step 8, part 1. (a) Native helper crash: the dead process stayed in the native registry until the next call or close; now _fail_generation unregisters a helper that has exited (test_hardening). The respawn itself was already covered (test_native_client). (b) Config: corrupt TOML names line and column in English; unknown keys warn and the server starts. New: every config error exits 78 (EX_CONFIG) with one 'pcbridge: configuration error: ...' line on stderr; the unit has RestartPreventExitStatus=78, so a broken file no longer restarts every second into the start limit. Checked on serve --check, serve --no-socket and in-process stdio. (c) A dictionary scan (/usr/share/dict) of every non-docstring string found ~20 more Turkish messages the word guard missed (tmux_start said 'olusturuldu', native input/pointer suggestions, screencast helper, OCR, models headline 'ajan:') -- all translated; the guard learned the words. test_e2e.py still expected 8 Turkish outputs; fixed, and e2e against the worktree daemon (port 18765, NO_AGENT) is 262 passed / 0 failed / 9 skipped = baseline. An example in computer_task's description used a real-looking handle; replaced with 'alex'.
- 2026-09-23 Step 8 done (tests/contracts/test_hardening.py 8 tests, 2 new integration tests). (1) Crash: covered, plus registry cleanup (part 1). (2) Concurrent desktop_unlock: two processes x 40 grants on one state dir never tore the file (flock + os.replace); in one daemon a second unlock ends the first session's RUNNING sequence (verify refuses, fail closed) while that session's next call is admitted under the new grant. (3) Update during a job: integration test with a 0.2 s stamp poll -- restart deferred ('1 job(s) running'), exit 75 once idle, the same relay client works against the restarted daemon. (4) Config: exit 78 (part 1). (5) Missing Pillow/evdev/wl-clipboard/gnome-screenshot (import blocker + trimmed PATH): shell_run still works; every DEPENDENCY_MISSING capability now prints its fix ('sudo apt install wl-clipboard', Pillow -> 'pcbridge update'); before, only the code was shown. The capabilities header was still Turkish ('Yetkilendirme'), fixed. tesseract: find_text already names the install. (6) Broken client env: relay test with DBUS_SESSION_BUS_ADDRESS='$DBUS_SESSION_BUS_ADDRESS' and empty XDG_SESSION_TYPE -- the tool saw the daemon's real bus and 'wayland'. (7) Faked X11/KDE session on an empty private bus: no crash, desktop tools refuse (fail closed); new session.support_note() names the unsupported session in the refusal and in system_capabilities. (8) Read-only state dir: was a Python TRACEBACK at startup; now one line + exit 78. Mid-session: a job started before its record was written, so a full disk could leave an invisible job; now writability is probed first and a failed record write stops the process (JobStartError, 'the job was NOT started'). (9) Logs: audit.log rotates at 5 MB (existing, tested in test_desktop), daemon/relay go to journald; job records (agent transcripts, 184 jobs / 11 MB here) are never auto-deleted -- doctor now reports their size and warns above 1 GB with a gio trash hint. (10) Headless shell, private bus only (script refuses otherwise; real ScreenSaver GetActive false before and after): unplugging Meta-1 was noticed within 2.01 s (monitor cache TTL), a shot of the removed monitor -> ShotLayoutChanged, a global coordinate on it -> refused; SetActive(true) on the headless shell -> known_locked -> gate SCREEN_LOCKED. INCIDENT, recorded honestly: one read-only-state probe ran the in-process server with a throwaway config that had [desktop] enabled = true and called desktop_unlock; LeaseStore chmods its dir back to 700, the grant opened in the temp dir and screen sharing started on the REAL display for the few seconds the probe lived. No input was sent, the real grant file was untouched, no helper was left; later probes keep the desktop disabled. Suites: models 106, desktop 615, contracts 507 OK, integration 29 OK, gjs 70, cargo 172.
- 2026-09-23 Step 7, tool surface. Before: 16 tools had readOnlyHint, 15 destructiveHint, none idempotentHint/openWorldHint; list_agents, notify, tmux_start, tmux_keys and desktop_lock had no hint at all, so clients fell back to the spec defaults (destructive, open world). Now pcbridge/tools.py TOOL_HINTS decides all four for all 36 tools and register() applies it; a tool missing from the table fails at registration. Decisions: anything that drives input or runs commands stays destructive (tmux_keys now explicitly so); desktop_lock is non-destructive and idempotent; screen readers (screen_capture, ui_dump, find_text, wait_for_text, window_list) are read-only but openWorld, because screen text is untrusted input. [tools] profile was a known key that NOTHING read (the CLAUDE.md lesson again) -- now read, validated (ConfigError naming full/core/desktop) and applied at startup: full 36, core 20, desktop 22. Pinned in test_mcp_contract.py (exact values) and test_tool_surface.py (4 tests).
- 2026-09-23 Step 7, extension. Daemon writes state_dir/status.json (schema, version, daemon running/stopped, pid, jobs_running, remote, cli; 0600, atomic, only on change, every 5 s; measured cost: running-job list 11 ms, tailscale funnel status 5 ms). The daemon now shuts down cleanly on SIGTERM (before: killed on the spot -- no 'stopped', the socket file left behind, held input released only by ExecStopPost); unit TimeoutStopSec=15. Extension 2.0.0: status.js (GLib/Gio only, gjs test 29/0) + indicator.js (PanelMenu.Button; icon open/idle/down, minutes left, 4 lines; 'Lock desktop control now' runs <cli> lock, 'Open logs', 'Status…'), D-Bus property Version, ActivateWindow/FocusedWindow unchanged. Kill-switch shortcut: gsettings key lock-shortcut, default EMPTY (off); <Super><Shift>Escape is taken (mutter cancel-input-capture), <Super><Control>Escape was free across all schemas and custom bindings here -- suggested in docs. MEASURED in a headless gnome-shell 46 with only the worktree extension (separate XDG_DATA_HOME, fake grant + fake status + fake CLI): indicator icon input-mouse-symbolic, label ' 10m', lines ['pcbridge 2.0.0: running','Desktop control: OPEN, 10 min left','Jobs running: 2','Remote access: off']; Version '2.0.0'; FocusedWindow answers; kill switch ran '<fake cli> lock'; main loop 0 late ticks; the icon is visible in the (Zorin, bottom) panel on a screenshot. Mixed versions: a 1.x server writes no status.json -> 'status unknown' (tested); doctor reads Version from the RUNNING shell and reports the 1.x extension here ('log out and back in'). shell-version stays ['46'] (the only version run).
- 2026-09-23 Step 9, package. packaging/build-deb.sh (plain dpkg-deb; no nfpm/uv/lintian here): venv at /usr/lib/pcbridge/venv made with python3 -m venv --without-pip + the build venv's pip --python, pinned by constraints.txt, relocated (scripts, pyvenv.cfg, pip's direct_url.json, byte-compiled with the final path; the build refuses a venv that still names the build dir). pcbridge_2.0.0~dev0_amd64.deb = 25 MB (131 MB installed), Depends pins python3 >= 3.12, << 3.13 (the venv is bound to the host python; built per release). Without sudo it could not be installed; it was extracted instead and its venv run directly: --version ok, doctor --json 30 ok / 2 fail (both from not being installed: no user socket unit, shebangs point to /usr/lib), and with tests copied outside the repo: models 106/0, desktop 615/0, contracts all pass except the 4 English-guard tests (they scan the git checkout, not the package), integration 20 OK / 7 skipped (need rust/). Fix found: a deb install left ~/.config/systemd/user units from an earlier user install in place, and those override /usr/lib/systemd/user -- setup now moves them to the backup (test_cli). Runtime platform check: session.platform_summary() (GNOME Shell version over D-Bus 3 ms, Mutter ScreenCast/RemoteDesktop from ListNames 15 ms, cached 60 s) is in system_capabilities text and structuredContent; an untested major (not 46) or a silent shell is a note, never an error. INCIDENT: the first control template was an unquoted heredoc, so the backquoted `pcbridge setup` in the description RAN -- as the old Hermes wrapper at ~/.local/bin/pcbridge (hermes -p pcbridge setup). Checked right after: nothing under ~/.hermes or elsewhere in ~ changed in the last 15 min, no hermes process left; its output only broke the control file. The template is now a quoted heredoc with @PLACEHOLDERS@.
- 2026-09-23 Step 9, CI. Action versions read from GitHub (tag + runs.using): checkout v7, setup-python v7, upload-artifact v7, download-artifact v8, softprops/action-gh-release v3 -- all node24; native.yml moved off checkout v4 / setup-python v5 / upload-artifact v4. ci.yml: python 3.12 (pinned constraints) / 3.13 / 3.14 (ranges) with the English guard, doc links (new tests/contracts/test_doc_links.py: 72 relative links in 21 tracked .md files), unit, contract, integration suites and the readiness check in-process (check.py now probes only --command targets when no --client is given); gjs job for the extension tests + strict schema compile. package.yml: native helper built once, then per release in a container (ubuntu:24.04, debian:trixie, ubuntu:26.04 experimental) build-deb.sh -> apt install -> file checks -> suites as a non-root user against /usr/lib/pcbridge (root would skip the permission tests). Deb file names now carry the release (pcbridge_<ver>_<id><version>_amd64.deb) so the release job cannot overwrite one with another. release.yml on v*: reuses package.yml, builds wheel + sdist with the helper, sha256sums, DRAFT release. Headless GNOME smoke added as an experimental job (continue-on-error, 20 min). None of this has run on GitHub yet. Local: cargo clippy -D warnings (both feature sets) and fmt clean.
- 2026-09-23 Step 9, first CI run on GitHub (branch productize/v2): native OK (clippy -D warnings on the new action versions); gjs OK; headless GNOME smoke OK on the runner -- the extension loaded (Version '2.0.0'), monitor table 1280x720, a screen-sharing frame of 1280x720; package: the .deb BUILT and INSTALLED on ubuntu:24.04, debian:trixie and ubuntu:26.04. What failed was the tests themselves: the 'non-live' test_desktop.py read this machine's session (screen lock, idle time, Mutter monitor table) and its config -- it only ever passed at this desk, and its pcb-do refusal tests used the REAL config and grant, so an open grant during a run would have let pcb-do act. Fixed: without PCBRIDGE_TEST_* flags the module pins an unlocked session, an away user, a fixed two-monitor table and a throwaway config with a fresh state dir. pcb-do --dry-run now works without any config (a syntax check needs none). test_native_revoke skips with a reason when the screen lock cannot be read (the helper checks the real one). Reproduced CI locally first: empty private bus and no bus at all -> desktop 615/0, contracts 514 OK, integration OK.
- 2026-09-23 Step 9 closed. lintian is not installed here (recorded; CI could add it). The container install matrix ran on GitHub: the .deb builds and installs on ubuntu:24.04, debian:trixie and ubuntu:26.04; the remaining CI reds were all tests that assumed this desk's session (fixed over four commits: hermetic test_desktop, pinned capture availability, an undefined skip(), /run/user/<uid> in containers, monitor table in the dependency test). Headless GNOME smoke on the GitHub runner: PASSED (extension loaded, Version '2.0.0', 1280x720 table, a 1280x720 screen-sharing frame) -- kept non-blocking (continue-on-error) so runner flakiness cannot block a release.
- 2026-09-23 Step 10 done: pcbridge 2.0.0.dev0 INSTALLED on this machine by the user-level path (no sudo): packaging/install-user.sh with the wheel of c90d11d (native helper build c90d11d49901) -> ~/.local/share/pcbridge/venv, ~/.local/bin/{pcbridge,pcb-shot,pcb-do}, user units (socket + service enabled), config migrated to ~/.config/pcbridge/config.toml (the repo file untouched), extension COPIED (the old repo symlink moved to setup's backup; the running shell keeps 1.x until re-login), Claude Code (user scope) / Codex (per-tool approval_mode tables kept: computer_task, desktop_unlock, list_agents, shell_run, window_focus) / Claude Desktop registered to '~/.local/bin/pcbridge stdio', aliases rewritten. Setup took 19 s. Before it: full copy + ROLLBACK.md in ~/.local/state/pcbridge/backup-20260923-072027-preinstall/ (setup wrote backup-20260923-072822/ROLLBACK.md too); the pcbridge-v2test units stopped and moved to the Trash; the Hermes wrapper at ~/.local/bin/pcbridge re-checked (nothing referenced it: no alias, unit, MCP entry, crontab, PcBridgeDesktop file) and moved to the Trash with gio trash, as instructed. Found and fixed: setup stamped the install AFTER restarting, so the new daemon restarted itself once more 5 s later (journal) -> stamp first now. Verification with fresh processes: readiness --desktop PASS for claude-code (tools/list 24.4 ms), codex (39.7), claude-desktop (47.1), the worktree legacy (22.4) and HTTP (healthz 28.3, system_status 204.7); the real 1.x legacy command of the main checkout still works in-process (tools/list 655.5 ms, I2); claude mcp list 'Connected'; codex mcp list enabled; ONE real claude -p (of the two allowed) called mcp__pcbridge__system_status through 2.0 and answered ('OK up 10 hours, 12 minutes', 7.8 s); live suites through the installed config, four flags: test_desktop 655/0, tests/live 61 OK (4 skipped by design), grant closed after; e2e against the installed daemon's HTTP 262/0/9 (= baseline); doctor --json 38 ok / 6 warn / 0 fail (warn: [limits] default_agent in the user's 1.x config -- already ignored by 1.x, left as is; tesseract missing; native input/window.list 'degraded' = checked on first use). Product path from cold: service stopped -> a fresh Claude Code client started it through the socket in 693 ms (1.x cold start 692.8 ms), the next client 25 ms; status.json followed the new pid. Aliases in a new shell point at the new CLI; bridgedurum and bridgekilit work. system_status warm median 160.9 ms (the 1.x command measured 217 ms at the same moment; the 132 ms baseline was another hour's load).

## 9. Needs eymistaken (physical presence, sudo, or a decision only he can make)

| # | What | Exact action | Why it could not be done unattended |
|---|---|---|---|
| 1 | Author e-mail of the 154 existing commits | Nothing to do unless you want it changed: that needs a history rewrite and a force-push, which this run is not allowed to do. New commits use the GitHub noreply address. | Rewriting published history is a decision only the owner can make. |
| 2 | Log out and back in once (after step 10 installs the 2.0 extension) | Then check: the pcbridge icon is in the panel tray; its menu shows 'pcbridge 2.0.x: running'; pcbridge doctor says 'extension in this session: version 2.0.0'. Optional kill-switch shortcut: gsettings --schemadir ~/.local/share/gnome-shell/extensions/pcbridge-gorunur@eymistaken.local/schemas set org.gnome.shell.extensions.pcbridge-gorunur lock-shortcut "['<Super><Control>Escape']" | GNOME 45+ caches extension code; only a new login loads it (restarting the shell is off limits) |
| 3 | Restart Claude Desktop and Claude Code once | Quit and reopen both (Claude Code: start a new session) | Their running processes were started before the install and keep the 1.x server until they restart; new ones use the 2.0 relay |
| 4 | Install tesseract-ocr for find_text / wait_for_text | sudo apt install tesseract-ocr | Needs sudo |
| 5 | Optional: the .deb instead of the user install | Build: packaging/build-deb.sh; install: sudo apt install ./dist/pcbridge_<version>_zorin18_amd64.deb; then pcbridge setup (it moves the user units aside) | Needs sudo; the user install works without it |
| 6 | Your config has default_agent under [limits] | Move the line 'default_agent = ...' to the top of ~/.config/pcbridge/config.toml if you want it to count | It has been ignored since 1.x; changing what the file means was not this run's call |
| 7 | Untracked files in the repo root | config.toml.yedek-* and graphify-out/ in ~/Belgeler/Pcbridge are not in git; keep or move them as you like | Personal files; not mine to move |
| 8 | An empty 'Yeni Belge' Text Editor window is open | Close it (nothing to save) | Left by the step 3 live test; closing windows was avoided |
