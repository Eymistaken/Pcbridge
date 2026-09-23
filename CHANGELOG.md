# Changelog

## 2.0.0 - 2026-09-23

pcbridge 2.0 turns a personal setup into an installable product: one
resident daemon that every client reaches without manual steps, English
messages throughout, any monitor layout, and packaging with CI.

### Fixed

- **Monitor tables under Mutter's physical layout mode.** GNOME uses the
  physical layout mode unless fractional scaling is enabled; positions and
  sizes are then framebuffer pixels. The resolver divided them by the scale
  regardless, so a 4K monitor at scale 2 was reported as 1920x1080 next to a
  neighbor at x=3840, with a phantom gap between them, a wrong pointer axis
  and misplaced clicks. Python and Rust now read `layout-mode`.
- **The xrandr fallback** did not normalize the canvas origin like the
  Mutter path, so the two paths described different canvases.
- **Configuration errors and an unwritable state directory** ended in a
  Python traceback, and the service restarted every second until systemd's
  start limit. They now print one line saying what to fix and exit with
  code 78, which the unit does not restart.
- **A job could start without a record.** The process started before its
  record was written, so a full disk left a job that no tool could list or
  stop. The record is now proven writable first, and a failed write stops the
  process.
- **A crashed native helper stayed in the process registry** until the next
  call, so diagnostics could show a helper that no longer existed.
- **The daemon died on SIGTERM without cleanup**: held input was not
  released by the daemon, the socket file stayed and the status file kept
  saying "running". SIGTERM now shuts down cleanly (bounded by
  `TimeoutStopSec=15`).
- **An install restarted the daemon twice.** `setup` wrote the version stamp
  after restarting the daemon, which then saw a newer install and restarted
  again.
- **A late answer to a cancelled request terminated the stdio server**
  (an assertion in the MCP SDK's `RequestResponder.respond`); such answers
  are now dropped.
- **The client's broken environment reached tools**: Claude Code's
  `CLAUDE_*` variables leaked into agent jobs, and Codex's literal
  `$DBUS_SESSION_BUS_ADDRESS` broke the desktop tools. Tools now run in the
  daemon's environment plus an allow-list from the client.
- **Five tools carried no MCP hints**, so clients assumed the spec defaults
  (destructive, open world); several read-only tools were not marked
  read-only.
- **`[tools] profile` was accepted but never read.**
- **`pcb-do --dry-run` required a config file** although it only parses.
- **Missing dependencies were reported as a bare error code**; the
  capability report now names the package to install.
- **An unsupported session (X11, a non-GNOME desktop) was reported as an
  unreadable screen lock**; it is now named.
- **The non-live test suites depended on the machine they ran on** (the
  GNOME session, the monitors and the real config, including its grant), so
  they could not run in CI and a grant opened during a run could let a test
  act for real.

### Added

- **A resident daemon** (`pcbridge serve`) behind a systemd user socket
  (`$XDG_RUNTIME_DIR/pcbridge/mcp.sock`, mode 0600), serving local sessions
  and HTTP from one process. A new client reaches the tool list in 22-47 ms
  instead of ~700 ms; with the daemon stopped, socket activation starts it
  in ~0.7 s.
- **`pcbridge stdio`**, a standard-library relay for local clients. On a
  daemon failure it returns retryable errors for requests in flight,
  reconnects and replays `initialize`; without a daemon it runs the server
  in-process and reports the degraded mode.
- **Jobs in their own systemd scopes**, so a daemon crash or an update
  restart does not kill them; `pcbridge stop` still ends them.
- **Updates without interruption**: the daemon restarts into a new install
  only when no job, grant or call is active.
- **The `pcbridge` command**: `setup`, `connect`, `doctor` (`--json`,
  `--fix`), `status`, `lock`, `unlock`, `stop`, `remote`, `logs`, `report`
  (redacted), `update`, `uninstall`. Every change `setup` makes is backed up
  with a `ROLLBACK.md`.
- **Packaging**: a wheel that carries the native helper, a user-level
  installer, and a `.deb` per distribution release built with `dpkg-deb`.
- **CI**: Python 3.12, 3.13 and 3.14 suites, the extension's gjs tests, a
  headless GNOME Shell smoke test, container installs on Ubuntu 24.04,
  Debian 13 and Ubuntu 26.04, and draft releases from tags.
- **A panel indicator** in the GNOME extension: daemon up or down, the grant
  and its time left, running jobs, remote access, and "Lock desktop control
  now". It reads `state_dir/status.json`, which the daemon writes on change.
  An optional kill-switch shortcut, off by default.
- **A D-Bus `Version` property** on the extension's interface, reported by
  `pcbridge doctor` for the running session.
- **Any monitor layout**: a 14-layout fixture matrix (single, 4K at scale 2
  in both layout modes, fractional scales, ultrawide, portrait 90/270,
  vertical stack, three mixed monitors, negative origin, gaps) resolved
  identically by Python and Rust, and verified in a headless GNOME Shell.
- **A pixel-area cap for scaled screenshots** (default 1536x864), so a
  16:10 or 4:3 monitor costs no more than 16:9, and a note when small text
  shrinks below half its size.
- **A platform report** in `system_capabilities`: GNOME Shell version,
  session type, and whether Mutter's ScreenCast and RemoteDesktop are
  present; an untested GNOME version is reported, not refused.
- **`[tools] profile`**: `full` (default), `core` or `desktop`.
- **Agent CLI discovery** beyond PATH (`~/.local/bin`, npm, bun, cargo,
  nvm), because a systemd daemon does not see a login shell's PATH.

### Changed

- **Every message a user or a model reads is English**: tool results,
  errors, suggested actions, the CLI, the installer, the extension, the
  shell scripts, `config.example.toml` and the computer-use skill. A contract
  test guards it. Error codes, categories, scopes, field names and
  configuration keys are unchanged.
- **Configuration moved to XDG locations** (`~/.config/pcbridge/config.toml`)
  with `config_version = 2`, migrated with a backup. A 1.x file loaded as is
  keeps its meaning.
- **The computer-use skill no longer hard-codes this machine's monitors.**
- **`MCP` tool hints are decided for every tool in one table** and a tool
  cannot be registered without them.
- **The systemd unit** no longer sets `PATH` or `DISPLAY` (both are derived)
  and does not restart on configuration errors.
- **Documentation** was rewritten as a small English set; the project's
  measurements live in `docs/dev/measured-facts.md`.

### Removed

- The per-client stdio server as the default path (still available as the
  fallback and through `PCBRIDGE_IN_PROCESS=1`).
- `add_client.py` and `capture.sh`, which served Gemini Spark only; Gemini
  Spark is no longer a target.
- The development journals (`WALKTHROUGH.md`, `PLAN.md`, `UYGULAMA.md`,
  `ADIMLAR.md`, `GOREV-kurallar.md`, `GELISTIRME.md`, `KURULUM.md`,
  `KULLANIM.md`, `KURALLAR.md`, `JARVIS.md`); their valid content moved to
  `docs/`, and git history keeps them.

### API

- **Entry points.** Clients register `pcbridge stdio`. The 1.x command
  `python -m pcbridge.server --stdio` still works and now relays to the
  daemon. `pcbridge serve` runs the daemon; `pcbridge-native` is unchanged
  as a binary name.
- **Tools.** The same 36 tools with the same names and input schemas. Their
  annotations now always carry all four hints. Output text is English;
  clients that matched Turkish text must match the stable
  `structuredContent.error` fields instead.
- **Error codes** (`ErrorCode`), categories and permission scopes are
  unchanged. Configuration errors exit with 78 (EX_CONFIG).
- **Configuration.** 1.x files load unchanged; `pcbridge setup` migrates
  them to version 2. New keys: `[desktop] screenshot_max_pixels`,
  `[tools] profile` (now read). Unknown keys warn instead of failing.
- **State files** keep their formats: the grant file (`desktop_unlock.json`),
  job records, the shot records and `pointer.json` are readable by 1.x and
  2.0 processes at the same time. `topology_id` gains a `,p` suffix only for
  a monitor whose pixel ratio differs from its scale, so ids at scale 1 are
  identical to 1.x.
- **Native helper protocol**: version 1.0, unchanged. `display.snapshot`
  adds a `pixel_ratio` field per monitor; older hosts ignore it.
- **GNOME extension**: the UUID is unchanged. `ActivateWindow` and
  `FocusedWindow` are byte-compatible; `Version` is new. A 1.x extension
  works with a 2.0 server and the reverse.

## 1.x (2026-07 to 2026-09)

pcbridge 1.x grew from an MCP server for Gemini Spark into a desktop
automation server for local coding agents on one machine: OAuth 2.1 for
remote access, background agent jobs, tmux, and desktop control through
uinput, silent Mutter ScreenCast capture and AT-SPI, with a safety gate
(grant, screen lock, idle guard, rate limit, audit log). Its last months
moved capture, input and accessibility into a Rust helper (`pcbridge-native`)
with parity gates against the Python paths, added relative pointer motion,
region capture, OCR and the GNOME extension's window activation. Each
client started its own server process.
