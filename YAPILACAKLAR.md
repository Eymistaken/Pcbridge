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
- [ ] pcbridge 2.0.0 is **installed and running on eymistaken's machine** through
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

- [ ] Add a `pyproject.toml`: a standard build backend, pinned
      `fastmcp==3.4.5`, optional extras for the desktop (evdev, Pillow),
      and Python ≥ 3.12. Measure first: does anything in the code rely on
      3.10/3.11 behavior?
- [ ] Keep one version source (`pcbridge/__init__.py`, bumped to
      `2.0.0.dev0` during the run and to `2.0.0` in step 12).
- [ ] Add console entry points: `pcbridge` (the CLI from step 4; for now a
      stub that dispatches `pcbridge serve`, `pcbridge stdio` and
      `pcbridge --version`), `pcb-shot` and `pcb-do`.
- [ ] Package data: the native helper binary, the extension, the systemd
      units and udev rule templates, and `config.example.toml`.
- [ ] `python -m pcbridge.server [--stdio]` keeps working unchanged (I2).
- [ ] **Verify.** Build a wheel, install it into a throwaway venv outside
      the repo, run the non-live suites against the installed package, and
      run the readiness check against the installed entry point.
- [ ] **Commit**: `build: package pcbridge with pyproject and a single version source`.

### Step 2 — Standard locations, config migration, no machine-specific assumptions  (time box: 4 h)

- [ ] **Config search order**, decided in one function:
  1. `$PCBRIDGE_CONFIG`
  2. `$XDG_CONFIG_HOME/pcbridge/config.toml`
  3. the legacy `<repo>/config.toml`

  With the legacy file, add a one-line English deprecation note in the log
  and in doctor. Record in section 7 which file wins when both exist.
- [ ] **Other data locations**:
  - state stays in `$XDG_STATE_HOME/pcbridge` (already there; do not move
    it while old processes may still be running),
  - logs go to `$XDG_STATE_HOME/pcbridge/log`,
  - cache goes to `$XDG_CACHE_HOME/pcbridge`,
  - runtime files (the socket) go to `$XDG_RUNTIME_DIR/pcbridge`, with the
    directory set to `0700`.
- [ ] **Config schema.**
  - Add `config_version`, plus a migration function that writes a
    timestamped backup before any rewrite.
  - Secret-bearing files are `0600`, and `pcbridge doctor` fixes the mode.
  - Unknown keys raise a clear English warning, not a crash.
- [ ] **Remove machine-specific assumptions.**
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
- [ ] **Verify.** Run the non-live suites, the readiness check (legacy and
      packaged), and a migration test: a copy of eymistaken's config migrated in a
      temporary `XDG_CONFIG_HOME` gives identical effective settings
      (compare the dataclasses, not the text).
- [ ] **Commit(s)**: `feat(config): XDG locations and versioned migration`,
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

- [ ] Can the daemon serve **raw MCP JSON-RPC** (the stdio framing,
      newline-delimited) per socket connection? Use the MCP SDK's server
      session over anyio streams, fed from the socket. If so, the relay is
      a dumb byte pipe. Nothing is translated, so image blocks,
      `structuredContent`, `isError`, progress, cancellation and
      notifications pass through byte for byte. **This is the preferred
      design.** The alternative is a FastMCP proxy over streamable HTTP on
      a unix socket. Use it only if it measures equally faithful, and prove
      it with tests that compare responses byte for byte.
- [ ] How does per-process context used today map into the daemon? Check
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
- [ ] Socket activation in Python: take the inherited fd via `LISTEN_FDS`,
      and serve the HTTP listener in the same event loop.

**Build:**

- [ ] **Relay** (`pcbridge stdio`). The legacy entry point routes here.
  - Connect to the socket.
  - If that fails, run `systemctl --user start pcbridge.socket` (or the
    service) and wait at most 2 s.
  - If it still fails, **fall back to the in-process server**: today's code
    path, with identical behavior. Log it as `degraded: in-process`, visible
    in `system_status` and in doctor.
  - The relay never exits while its client is alive.
- [ ] **Daemon-restart transparency.** The relay records the client's
      `initialize` request and `notifications/initialized`. When the socket
      drops:
  - it reconnects (activation if needed),
  - it replays the handshake and swallows the reply,
  - for every request that was in flight, it synthesizes a JSON-RPC error
    with the matching id: English message, retryable. The client must never
    hang.
- [ ] **Version handling.**
  - Daemon and relay exchange versions and a protocol number.
  - A compatible daemon is used even if it is older.
  - The daemon detects that newer code was installed (a version file or
    stamp) and **restarts itself only when idle**: no running jobs (I5), no
    open desktop grant, no in-flight calls. That is what finally kills the
    "stale stdio process" problem.
- [ ] **Units.**
  - `pcbridge.socket` and `pcbridge.service` (user units), both enabled.
  - `Restart=on-failure`.
  - Keep `ExecStopPost` lock behavior.
  - The service starts at login and is also socket-activated (I4).
- [ ] **State shared with old processes.** Old stdio processes (eymistaken's
      running clients) will run next to the daemon for a while. Everything
      they share on disk must stay backward compatible (I2, I6): the grant
      file format, `pointer.json`, the shot records, and the execution lock.
- [ ] **Audit log.** Include the client name from the preamble.

**Verify (record the numbers):**

- [ ] **Byte-for-byte parity.** The same scripted session (`tools/list`, a
      text tool, an error case, a `screen_capture` with image, a
      `structuredContent` tool, a cancellation, a long job with
      `job_status`) goes through in-process stdio and through the relay +
      daemon. The responses must be identical apart from ids and timestamps.
- [ ] **Timing budgets.**
  - Warm overhead per call: ≤ 5 ms at p50.
  - Cold start with the socket not yet running, until the first `tools/list`
    answers: ≤ 3 s.
  - Screenshot end to end: within I9.
- [ ] **Faults.** Each of these must recover within 3 s with no client
      hang:
  - `kill -9` of the daemon mid-session: the next call succeeds, the
    in-flight call gets a retryable error;
  - daemon stopped: activation brings it back;
  - socket file deleted or stale;
  - `XDG_RUNTIME_DIR` unusable: in-process fallback;
  - three clients at once: execution lock serialization still works.
- [ ] Full non-live suites. The readiness check on all registered commands
      and the legacy command. `test_e2e.py` (`PCBRIDGE_TEST_NO_AGENT=1`)
      against the daemon's HTTP path.
- [ ] **Live desktop suite through the relay**, with all four flags; see
      `CLAUDE.md`. Also run a real `computer_batch` in an empty
      `gnome-text-editor` window.
- [ ] **Commit(s)**: `feat(daemon): one resident server behind a thin stdio relay`,
      and separate commits for tests and units.

### Step 4 — One CLI: `pcbridge`  (time box: 4 h)

All output is in English, `--json` is available where it makes sense,
`--yes` makes it non-interactive, and every subcommand is idempotent.

- [ ] `pcbridge setup`: first-run wizard. It covers:
  - dependency check with exact `apt` commands printed,
  - config creation or migration,
  - units enabled,
  - udev / uinput status,
  - extension install,
  - client registration via `connect`,
  - a final readiness check.
- [ ] `pcbridge connect [--client claude-code|codex|claude-desktop|all] [--dry-run]`:
  - back up each client config before editing it,
  - register Claude Code at **user** scope (`CLAUDE.md` explains why
    project scope silently fails),
  - point every client at `pcbridge stdio`.
- [ ] `pcbridge doctor [--fix] [--json]`: port all ~35 checks from
      `doctor.sh`, and add daemon, socket, relay, version-skew, readiness
      and XDG checks. `--fix` repairs only safe things: modes, units,
      registrations, a stale socket.
- [ ] `pcbridge status`: daemon up or down, version, clients connected,
      grant state and time remaining, running jobs, remote tunnel state,
      degraded mode.
- [ ] `pcbridge lock`: the emergency stop. It revokes the grant and kills
      the screencast helpers, exactly like `bridgekilit` today.
      `pcbridge unlock --minutes N` is for humans.
- [ ] `pcbridge remote start|stop|status`: replaces `remote.sh`.
- [ ] `pcbridge logs [-f]`.
- [ ] `pcbridge report`: writes a sanitized tarball (versions, doctor JSON,
      recent logs with secrets and paths redacted, config with secrets
      removed), for bug reports.
- [ ] `pcbridge update`: drain-aware restart (I5). For git installs it also
      pulls and rebuilds; for package installs it only prints how to
      update.
- [ ] `pcbridge uninstall [--purge]`: keeps config and state unless
      `--purge`, and uses `gio trash` where possible.
- [ ] `pcbridge --version`.
- [ ] **Old scripts.**
  - `install.sh` becomes a small bootstrap for git checkouts:
    `uv`/venv, install the package, then `pcbridge setup`.
  - `connect.sh`, `doctor.sh`, `remote.sh`, `run.sh`, `add_client.py` and
    `capture.sh` either become one-line wrappers that print an English
    deprecation note, or are removed if nothing references them. Record
    which in section 7.
  - `setup_uinput.sh` stays as the git-install path for the udev rule.
  - Update eymistaken's aliases in his shell rc to call `pcbridge …`, with a
    backup of the rc file.
- [ ] **Verify.** CLI tests (subprocess, temporary `XDG_*` dirs), plus
      `pcbridge doctor --json` on the real machine with every check green
      or explained.
- [ ] **Commit**: `feat(cli): one pcbridge command for setup, connect, doctor and operation`.

### Step 5 — English everywhere  (time box: 5 h)

- [ ] **Inventory every string that reaches a human or a model**:
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
- [ ] **Translate** into clear, specific English that tells the reader what
      to do next. Keep error **codes**, categories, scopes and field names
      unchanged, because they are API. Keep messages short, since models
      read them on every error.
- [ ] Update the tests that assert on message text. Prefer asserting on
      codes where possible.
- [ ] Update the `CLAUDE.md` rule "Kullanıcıya dönen metinler Türkçe" to
      "all user- and model-facing text is English".
- [ ] **Verify.** Add a guard test: a script that scans the user-facing
      modules for common Turkish words and characters, with an allow-list
      for test fixtures, the Turkish-typing tests and similar. Run all
      suites.
- [ ] **Commit(s)**: `feat(i18n): English for every message a user or model reads`.

### Step 6 — Any monitor layout  (time box: 6 h)

- [ ] **Audit.** Look for every code path that assumes:
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
- [ ] **Fixture matrix.** Contract tests in Python and Rust, with parity
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
- [ ] **Screenshot sizing policy for extreme aspect ratios.**
  - Keep the 1568 long-edge ceiling (see `CLAUDE.md`).
  - Also cap the pixel area.
  - Portrait monitors scale by their height.
  - A full-canvas capture of a very wide canvas warns (in English) that
    text will be unreadable, and suggests `monitor=`.
  - Document the policy in `config.example.toml`.
- [ ] **Live verification without touching eymistaken's display.** Measure first
      whether `gnome-shell --headless` (or `--nested`) with virtual monitors
      of chosen sizes and scales works on this machine, and whether Mutter
      ScreenCast, the uinput absolute mapping and AT-SPI work inside it. The
      existing `gnome-extension/nested.sh` is a starting point; mind its
      orphan-process lesson in `CLAUDE.md`. Where it works, run capture
      parity and pointer-accuracy checks for at least three layouts: single
      4K @2.0, ultrawide, portrait. Where it does not, record the finding
      and rely on the fixtures.
- [ ] Re-run the real-machine live suite. eymistaken's two-monitor setup must be
      unchanged (I9).
- [ ] **Commit(s)**: `feat(display): adapt to any monitor layout, scale and orientation`.

### Step 7 — Extension panel indicator, kill switch, tool surface  (time box: 5 h)

- [ ] **Panel indicator** (GNOME 46 API first). It shows:
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
- [ ] **Kill-switch keyboard shortcut.** Configurable, off or on by default
      per your judgment; record it in section 7. It must not collide with
      GNOME or Zorin defaults, so check with `gsettings`.
- [ ] **Compatibility** (I6):
  - Add a `Version` property on the D-Bus interface.
  - Keep `ActivateWindow` and `FocusedWindow` byte-compatible.
  - The server must work with the old extension, which has no `Version`,
    no indicator and no `FocusedWindow`.
  - The new extension must work with an older server, which has no
    `status.json`.
  - Keep the extension UUID unchanged, because renaming it orphans eymistaken's
    install.
- [ ] **`shell-version`.** List only versions you actually ran. Record the
      untested ones in the docs as "expected to work, untested".
- [ ] **Verify in the nested shell** with the gjs tests and new tests for
      the indicator state machine. The real session loads the new
      extension only after re-login; add that check to section 9 with an
      exact checklist.
- [ ] **Tool surface.**
  - Audit `readOnlyHint`, `destructiveHint`, `idempotentHint` and
    `openWorldHint` on every tool. A wrong `readOnlyHint` is dangerous (see
    `CLAUDE.md`).
  - Add an optional `[tools] profile` (`full` by default = exactly today's
    set; `core` = no desktop tools; `desktop`) so users can shrink context.
    Do not hide tools dynamically at runtime, because clients cache tool
    lists.
- [ ] **Commit(s)**: `feat(extension): panel indicator and kill switch`,
      `feat(tools): audited annotations and optional profiles`.

### Step 8 — Hardening and self-healing  (time box: 5 h)

For each scenario below: write the expected behavior, then add an automated
test where possible (a fake or temp environment), or a scripted manual check
where not. Record the results in section 8.

- [ ] The native helper crashes mid-capture: the next call respawns it, and
      no stale grant binding is left behind.
- [ ] Two clients call `desktop_unlock` concurrently, with each other's
      grant id rotation (see `CLAUDE.md`, "Native yardımcı tek bir izne
      bağlı").
- [ ] An update is installed while a job is running: the daemon defers the
      restart until idle (I5), and the relay reconnects afterwards.
- [ ] `config.toml` is corrupt or has an unknown key: the daemon still
      starts the non-desktop tools and doctor names the line; or it fails
      with an English message saying exactly what to fix, never a traceback.
- [ ] Missing optional dependencies (evdev, Pillow, gi, tesseract,
      wl-clipboard): the related tools report capability-unavailable in
      English with the install command, and the rest works.
- [ ] A broken client environment: the Codex literal
      `$DBUS_SESSION_BUS_ADDRESS` case, and empty `XDG_SESSION_TYPE` as
      Claude Desktop sends it (see `CLAUDE.md`). With the daemon these no
      longer reach the tools; prove it through the relay.
- [ ] Unsupported session (X11, non-GNOME): a clean degraded mode, tested
      with a faked environment.
- [ ] Disk full or read-only state dir: an English error, no crash loop.
- [ ] Log rotation, so logs cannot grow unbounded.
- [ ] Screen locked or monitor hotplug: use a nested or headless shell
      only (section 4); otherwise rely on the existing measurements.
- [ ] **Commit(s)**: `fix: …` / `test: fault injection for …`.

### Step 9 — `.deb` package, CI, distro matrix  (time box: 6 h)

- [ ] **Package layout** (pick a tool: plain `dpkg-deb` with a build
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
- [ ] Run `lintian` if available. Build locally. Install in a
      docker/podman container if available (Ubuntu 24.04, Debian 13, and
      Ubuntu 26.04 if the image exists): `pcbridge --version`,
      `pcbridge doctor --json`, and the non-live suites against the
      installed package.
- [ ] **CI workflows**, each with its own badge in the README:
  - `ci.yml` on every push and PR: Python non-live suites on 3.12/3.13
    (plus 3.14 if available), gjs extension tests, the readiness check in
    in-process mode, the English guard, and a doc link check.
  - The existing `native.yml`.
  - `package.yml`: build the `.deb`, run a container install matrix.
  - `release.yml` on tag `v*`: build the artifacts, `sha256sums`, and a
    **draft** GitHub release.

  Move `actions/*` to versions that do not use the deprecated Node 20.
- [ ] **Headless GNOME smoke in CI.** Try it with a time box of 1 h: run
      `gnome-shell --headless --virtual-monitor` inside the container, load
      the extension, and do one capture. If it is not feasible on GitHub
      runners, record why in section 7 and skip it.
- [ ] **Runtime platform check.** Detect GNOME Shell version, session type
      and the availability of Mutter ScreenCast and RemoteDesktop, then
      report capabilities instead of crashing on unknown versions.
- [ ] **Commit(s)**: `build(deb): …`, `ci: …`.

### Step 10 — Install 2.0 on eymistaken's machine through the product path  (time box: 4 h)

- [ ] **Build the release artifacts** from the branch HEAD.
  - If `sudo -n` works, install the `.deb`.
  - Otherwise do a **user-level install** of the same wheel and helper
    (`~/.local/share/pcbridge/venv` + `~/.local/bin/pcbridge` + user units).
    The CLI must know which install kind it is.
  - Record which path was used. If it was the user install, add the `.deb`
    install command to section 9.
- [ ] **Before touching anything**, back up:
  - the client configs,
  - the shell rc,
  - the systemd user units,
  - `config.toml`,
  - the extension directory.

  Store the backups in `$XDG_STATE_HOME/pcbridge/backup-<timestamp>/`, with
  a `ROLLBACK.md` in that folder giving exact commands.
- [ ] Run `pcbridge setup --yes`:
  - migrate the config (the legacy file stays in place as a fallback),
  - enable the units,
  - install the new extension files (active after re-login; the old one
    keeps working, I6),
  - `pcbridge connect --client all`,
  - rewrite the aliases.
- [ ] **Keep the repo's `.venv` and the legacy command working** (I2). eymistaken's
      running Claude Desktop and Claude Code keep their old stdio processes
      until they restart.
- [ ] **Verify everything with fresh processes.**
  - The readiness check (`--desktop`) against every registered command and
    the legacy command.
  - `claude mcp list` and `codex mcp list` (connection check).
  - At most two trivial real `claude -p` calls that use one pcbridge tool.
  - The full live desktop suite through the relay, with all four flags.
  - `pcbridge doctor --json` all green, or explained.
  - Record all numbers in section 8 against the baselines (I9).
- [ ] **Hand the product path to eymistaken.** Run a sample of the commands eymistaken
      will actually use. Confirm that nothing needs to be opened: start
      from the daemon stopped, then use a fresh client.
- [ ] **Commit** any fixes this surfaced. Tick the "installed" box in
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

## 9. Needs eymistaken (physical presence, sudo, or a decision only he can make)

| # | What | Exact action | Why it could not be done unattended |
|---|---|---|---|
| 1 | Author e-mail of the 154 existing commits | Nothing to do unless you want it changed: that needs a history rewrite and a force-push, which this run is not allowed to do. New commits use the GitHub noreply address. | Rewriting published history is a decision only the owner can make. |
