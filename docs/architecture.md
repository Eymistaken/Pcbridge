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
          desktop: uinput, compositor capture, AT-SPI,               │
                   native helper (pcbridge-native, Rust) ◄───────────┘
          GNOME extension: frame, panel indicator, ActivateWindow /
                           FocusedWindow (reads the grant and status.json)
          on KDE Plasma instead: KWin ScreenShot2, one-shot KWin scripts,
                           the grant notification, idle-watch
          on Hyprland instead: image-copy capture, exact IPC window focus,
                           native grant frame, idle-watch
          on Cinnamon / Xfce (X11) instead: the helper's X11 backend
                           (RandR, GetImage, EWMH), native grant frame,
                           idle-watch, the grant notification; on Cinnamon
                           also the pcbridge panel applet
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
| Server | `app.py` | Builds the FastMCP app, the two ASGI shims (`MetadataNormalizer`, `BasicAuthFormShim`, required by Google's OAuth client), routes `/consent`, `/healthz`, `/shot/<token>`, `/dl/<token>`, `/ul/<token>` |
| Configuration | `config.py`, `paths.py` | TOML -> dataclasses; XDG locations; migration of 1.x files; typed `ConfigError` (exit 78) |
| Settings | `settings.py`, `toolcatalog.py` | A registry of every setting and an editor that keeps comments (tomlkit), checks with the loader, backs up and writes atomically; the tool catalog from the server's own registration |
| MCP clients | `cli/connect.py` | Registers `pcbridge stdio` with eight local clients, switches it off or removes it, backs up every file it changes |
| Terminal UI | `tui/` | `pcbridge` alone in a terminal (Textual): the grant bar, Overview, Settings, Tools, Commands; everything through `tui/backend.py` |
| Authentication | `auth.py` | A small OAuth 2.1 server (DCR, PKCE, refresh) and consent page, SQLite |
| Tools | `tools.py` | All 40 MCP tools and their hints (`TOOL_HINTS`); `[tools] profile` |
| File links | `filelinks.py` | One-time download and upload links: the in-memory link store, `/dl/<token>` and `/ul/<token>`, the access-log filter that hides their tokens |
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
clipboard.py   wl-paste / wl-copy (xclip on X11, run by the helper)
uitree.py      the accessibility tree as text, stable ids
apps.py        launching and raising windows
compositor.py  explicit GNOME, KWin, Hyprland, Cinnamon, Xfce, and UNKNOWN transports
hyprland.py    selected-session runtime context and guarded exact focus
hyprland_windows.py compositor window identities, focus, and window capture regions
kwin.py        the KWin screenshot authorization entry
kwin_helper.py a one-shot KWin script (system python): raise / read windows
x11native.py   Cinnamon/Xfce: monitor table and window focus asked of the helper
idlewatch.py   Plasma/Hyprland/X11 idle time, from the session-bound native idle-watch
a11y.py        Qt accessibility, switched on for a Plasma grant
grantnotice.py the grant notification (Plasma, Cinnamon, Xfce: lasting, with Lock now)
batch.py       the action-list engine; knows nothing about devices (Ops protocol)
ops.py         Ops bound to the real devices
safety.py      THE GATE: every desktop tool passes here
execution.py   the cross-process write lock; the grant rechecked per action
lease.py       the grant file: atomic, flock-serialized
policy.py      content gates (password fields, close shortcuts, repeat clicks)
session.py     repairing the session environment; desktop detection; the platform report
backends/      python.py and rust.py providers behind one runtime
```

### GNOME, KDE Plasma, Hyprland, Cinnamon and Xfce

`session.desktop_kind()` uses the desktop environment when known, the
selected Hyprland instance and matching Wayland socket, or the GNOME/KWin
session-bus owner. Cinnamon and Xfce count only in an X11 session, the
others only on Wayland. Ambiguous or invalid sessions stay UNKNOWN and
refuse desktop actions. `compositor.py` selects the backend behind each
question.

| Question | GNOME | KDE Plasma | Hyprland | Cinnamon / Xfce (X11) |
|---|---|---|---|---|
| Screen locked? | `org.gnome.ScreenSaver` | `org.freedesktop.ScreenSaver` (KWin) | selected-instance `hyprctl -j locked` | the locker's `GetActive`, or logind no longer counting the session active |
| Idle time | `Mutter.IdleMonitor` | native `idle-watch` | session-bound native `idle-watch` | native `idle-watch` (MIT-SCREEN-SAVER) |
| Monitor table | Mutter `DisplayConfig` | `kscreen-doctor -j` | selected-instance monitor IPC | the helper's RandR 1.5 table |
| Screenshots | Mutter ScreenCast over PipeWire | KWin `ScreenShot2` | native image-copy capture | the helper's `GetImage` |
| Raise / name a window | extension D-Bus | one-shot KWin script | exact compositor identity, with focus verification | the helper's EWMH `_NET_ACTIVE_WINDOW`, read back |
| The grant on screen | frame + panel indicator | lasting notification with "Lock now" | native white frame with fresh presentation proof | native frame (override-redirect windows, read back), lasting notification, and the Cinnamon applet |

All of them feed the same neutral display state (`monitors.resolve_state` in
Python, `DisplayState` in Rust), so coordinates, scale and rotation follow
one set of rules. A shot records output identity as well as geometry; a shot
from an output that was swapped or removed is refused.

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
It captures through Mutter ScreenCast/PipeWire, KWin ScreenShot2,
Hyprland image-copy, or on X11 one pure Rust X connection (x11rb: RandR,
GetImage, XFixes, EWMH; no libX11 link), drives uinput, and reads and acts
on AT-SPI over raw D-Bus. On Plasma, Hyprland and the X11 desktops the
daemon also runs `pcbridge-native idle-watch`, which holds a Wayland idle
notification open (on X11: polls the X server's idle time) and writes the
idle state to
`$XDG_RUNTIME_DIR/pcbridge/idle.json`. It is the only desktop backend:
the Python paths that once stood in for it (a GStreamer screen share,
python-evdev devices, a system-python AT-SPI helper and gnome-screenshot)
were removed after 2.8, so without the helper the desktop tools refuse and
say how to get it. It revalidates the grant file before every protected
operation, so a revoke from any process stops it.

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
