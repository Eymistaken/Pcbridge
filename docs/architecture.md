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
| Authentication | `auth.py` | A small OAuth 2.1 server (DCR, PKCE, refresh) and consent page, SQLite |
| Tools | `tools.py` | All 36 MCP tools and their hints (`TOOL_HINTS`); `[tools] profile` |
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
batch.py       the action-list engine; knows nothing about devices (Ops protocol)
ops.py         Ops bound to the real devices
safety.py      THE GATE: every desktop tool passes here
execution.py   the cross-process write lock; the grant rechecked per action
lease.py       the grant file: atomic, flock-serialized
policy.py      content gates (password fields, close shortcuts, repeat clicks)
session.py     repairing the session environment; the platform report
backends/      python.py and rust.py providers behind one runtime
```

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
It captures through Mutter ScreenCast/PipeWire, drives uinput, and reads and
acts on AT-SPI over raw D-Bus. Each subsystem (`[native] capture / input /
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
