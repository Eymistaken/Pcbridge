# Architecture

## Always ready: one daemon, a thin relay

```
 Claude Code ──┐  pcbridge stdio (relay, stdlib only)
 Codex ────────┼──────────────► $XDG_RUNTIME_DIR/pcbridge/mcp.sock ──┐
 Claude Desktop┘                  (systemd socket, 0600)             │
                                                                     ▼
 phone / web ── HTTPS ── Tailscale Funnel ── 127.0.0.1:8765 ──► pcbridge daemon
                                                                (pcbridge.service)
                                                                     │
          jobs: one systemd scope each (pcbridge-job-<id>.scope) ◄───┤
          desktop: uinput, Mutter ScreenCast, AT-SPI,                │
                   native helper (pcbridge-native, Rust) ◄───────────┘
          GNOME extension: frame, panel indicator, ActivateWindow /
                           FocusedWindow (reads the grant and status.json)
          on KDE Plasma instead: KWin ScreenShot2, one-shot KWin scripts,
                           the grant notification, idle-watch
```

- **One resident daemon** (`pcbridge serve`, `pcbridge.service`) serves
  every client. It owns the virtual input devices, the screen-sharing
  session, the job manager and the audit log.
- **Local clients run `pcbridge stdio`**, a relay that uses only the
  standard library. It sends a one-line preamble (client name, working
  directory, an allow-listed part of the environment), then pipes MCP
  JSON-RPC bytes to the daemon's socket. It adds ~0.1 ms per call.
- **systemd socket activation**: `pcbridge.socket` listens even when the
  daemon is not running; the first client starts it (~0.7 s). The socket
  and service are enabled at login; the tunnel is not.
- **Fallbacks, in order**: the socket; `systemctl --user start
  pcbridge.socket` and a short wait; the whole server in-process (the 1.x
  behavior), reported as degraded. A client never needs anything opened by
  hand.
- **Recovery**: if the daemon dies mid-call, in-flight requests get a
  retryable JSON-RPC error, the relay reconnects and replays `initialize`
  (its answer is swallowed), and the session continues.
- **Per-session state**: the `ui_dump` id registry, the rate window, the
  grant token of a running sequence, the delivery mode (file path for local
  clients, link for HTTP) and held input belong to a session; a client that
  disconnects has its held keys released.
- **Jobs outlive the daemon.** Under systemd each job runs in its own
  transient scope, so a crash or an update restart does not kill it;
  `pcbridge stop` still ends them.
- **Updates**: installing writes a version stamp; the daemon restarts itself
  (exit 75, systemd restarts it) only when idle: no job, no open grant, no
  call in flight.

## Layers

| Layer | Files | Role |
|---|---|---|
| Entry points | `server.py`, `daemon.py`, `relay.py`, `cli/` | `pcbridge serve / stdio / setup / doctor / …` |
| Server | `app.py` | Builds the FastMCP app, the two ASGI shims (`MetadataNormalizer`, `BasicAuthFormShim`, required by Google's OAuth client), routes `/consent`, `/healthz`, `/shot/<token>` |
| Configuration | `config.py`, `paths.py` | TOML -> dataclasses; XDG locations; migration of 1.x files; typed `ConfigError` (exit 78) |
| Settings | `settings.py`, `toolcatalog.py` | A registry of every setting and an editor that keeps comments (tomlkit), checks with the loader, backs up and writes atomically; the tool catalog from the server's own registration |
| Terminal UI | `tui/` | `pcbridge` alone in a terminal (Textual): the grant bar, Overview, Settings, Tools, Commands; everything through `tui/backend.py` |
| Authentication | `auth.py` | A small OAuth 2.1 server (DCR, PKCE, refresh) and consent page, SQLite |
| Tools | `tools.py` | All 37 MCP tools and their hints (`TOOL_HINTS`); `[tools] profile` |
| Session context | `sessionctx.py` | Per-session maps and context variables |
| Jobs | `jobs.py` | Background processes and agent output parsers (`plain`, `claude_stream_json`, `agy_json`) |
| Agent selection | `models.py`, `executables.py` | Pure agent/model/effort resolution; finding agent CLIs (PATH, `~/.local/bin`, npm, bun, nvm) |
| Desktop | `desktop/` | Below |
| Native helper | `rust/`, `native/` | `pcbridge-native`: capture, input, accessibility; framed stdio protocol |
| Extension | `gnome-extension/` | Frame, indicator, two D-Bus methods |

### `desktop/`

```
monitors.py    the monitor table: THE source of the coordinate space
capture.py     screenshots, shot records, THE coordinate conversion (to_global)
screencast.py  the Python screen-sharing helper's lifecycle
input.py       uinput keyboard + absolute/relative pointer; text via clipboard
clipboard.py   wl-paste / wl-copy
uitree.py      the accessibility tree as text, stable ids
apps.py        launching and raising windows
compositor.py  which compositor answers each question (GNOME Shell or KWin)
kwin.py        the KWin screenshot authorization entry
kwin_helper.py a one-shot KWin script (system python): raise / read windows
idlewatch.py   Plasma's idle time, from the native helper's idle-watch
a11y.py        Qt accessibility, switched on for a Plasma grant
grantnotice.py the grant notification (Plasma: the grant's signal)
batch.py       the action-list engine; knows nothing about devices (Ops protocol)
ops.py         Ops bound to the real devices
safety.py      THE GATE: every desktop tool passes here
execution.py   the cross-process write lock; the grant rechecked per action
lease.py       the grant file: atomic, flock-serialized
policy.py      content gates (password fields, close shortcuts, repeat clicks)
session.py     repairing the session environment; desktop detection; the platform report
backends/      python.py and rust.py providers behind one runtime
```

### GNOME and KDE Plasma

`session.desktop_kind()` decides once which desktop this is:
`XDG_CURRENT_DESKTOP` when it is set, otherwise the owner of `org.gnome.Shell`
or `org.kde.KWin` on the session bus. An unknown desktop is treated as GNOME,
so every check fails closed with GNOME's messages. `compositor.py` names the
backend behind each question; the rest of the code asks it instead of
testing the desktop itself.

| Question | GNOME | KDE Plasma |
|---|---|---|
| Screen locked? | `org.gnome.ScreenSaver` | `org.freedesktop.ScreenSaver` (KWin) |
| Idle time | `Mutter.IdleMonitor` | `pcbridge-native idle-watch` (`ext_idle_notifier_v1`), a file in the runtime dir |
| Monitor table | Mutter `DisplayConfig` | `kscreen-doctor -j` |
| Screenshots | Mutter ScreenCast over PipeWire | KWin `ScreenShot2` (native helper only), display ids `kwin:` |
| Raise / name a window | the extension (D-Bus) | a one-shot KWin script; GNOME search / KRunner as fallback |
| The grant on screen | frame + panel indicator | a lasting notification with "Lock now" |

Both feed the same neutral display state (`monitors.resolve_state` in
Python, `DisplayState` in Rust), so coordinates, scale and rotation follow
one set of rules; a shared fixture pins both KScreen adapters.

## Two decisions that are defended hard

1. **Every internal API uses global canvas coordinates.** The only place
   that interprets `monitor=` or `shot=` is `capture.to_global()`. When the
   conversion lived in two places, one was forgotten and clicks landed
   1920 px to the left, with no error anywhere.
2. **`batch.py` does not know real devices.** The dependency runs one way
   (`ops` -> `batch`), so budgets and stop rules are tested without sending a
   single click, and `computer_batch` and `pcb-do` share one engine.

## The native helper

`pcbridge-native` is a separate Rust process, one per grant, speaking a
framed JSON protocol over stdio ([native/protocol-v1.md](native/protocol-v1.md)).
It captures through Mutter ScreenCast/PipeWire (KWin ScreenShot2 on
Plasma), drives uinput, and reads and acts on AT-SPI over raw D-Bus. On
Plasma the daemon also runs `pcbridge-native idle-watch`, which holds a
Wayland idle notification open and writes the idle state to
`$XDG_RUNTIME_DIR/pcbridge/idle.json`. Each subsystem (`[native] capture / input /
accessibility`) is `auto` by default: the helper when it is packaged, the
Python path otherwise, and a fallback is reported, never silent. It
revalidates the grant file before every protected operation, so a revoke
from any process stops it.

## Where things live

| What | Where |
|---|---|
| Config | `~/.config/pcbridge/config.toml` (0600) |
| State (grant, jobs, audit log, OAuth DB, shots, status.json) | `~/.local/state/pcbridge/` |
| Socket | `$XDG_RUNTIME_DIR/pcbridge/mcp.sock` |
| User install | `~/.local/share/pcbridge/venv`, `~/.local/bin/pcbridge` |
| Package install | `/usr/lib/pcbridge/venv`, `/usr/bin/pcbridge` |
| Units | `~/.config/systemd/user/` (user install) or `/usr/lib/systemd/user/` |
| Extension | `~/.local/share/gnome-shell/extensions/pcbridge-gorunur@eymistaken.local/` |
| KDE entries | `~/.local/share/applications/pcbridge-native.desktop`, `pcbridge-lock.desktop` (package: `/usr/share/applications/`) |
