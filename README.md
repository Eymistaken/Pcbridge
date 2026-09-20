# pcbridge

**Turn a Linux desktop into an MCP server — and let an agent actually use it.**

pcbridge exposes one machine over the [Model Context Protocol](https://modelcontextprotocol.io):
a connected agent can dispatch work to terminal coding agents, drive a live tmux
session, run shell commands, read and write files, and — when explicitly
permitted — operate the graphical desktop with a virtual keyboard, mouse and
screen reader.

It is a personal tool, built and measured on one machine: **Zorin OS 18.1
(Ubuntu 24.04) · GNOME Shell 46 · Wayland · two 1920×1080 monitors.**

> **Read this before you install it.** pcbridge is remote code execution on your
> own desktop, by design. The [security section](#security) is not boilerplate —
> it describes a real and deliberate risk trade-off.

---

## What it does

| | |
|---|---|
| **Delegate to coding agents** | Hand a task to Claude Code or Antigravity CLI, get a job id back, poll it. Agents take minutes; nothing blocks. |
| **Drive a live terminal** | Attach to a tmux session, send keys, read the pane back. |
| **Shell and files** | Run commands (foreground or background), read, write, search. |
| **Use the desktop** | Virtual keyboard and absolute mouse via `uinput`, window focus, app launch. A closed application is started directly; an open one is raised through the GNOME extension. |
| **Read the screen** | Accessibility tree as text (cheap, coordinate-free) or a silent screenshot (PipeWire screencast, no flash, no shutter sound). |
| **Click what you see** | Every screenshot carries a short id. Send the pixel you see plus that id — the server applies the offset and the scale, so the model never does the arithmetic. |
| **Batch it** | Run a whole sequence of GUI actions in one call, with budget and focus guards. |

34 tools in total. The desktop half is **off by default** and stays off until you
opt in.

## The native helper

Screen capture, input and accessibility each have two implementations: the
original Python one and a Rust helper, `pcbridge-native`, built from `rust/`.
All three default to `auto` — the helper is used when it is installed, and the
Python path runs when it is not. Either can be pinned in `config.toml`, which is
how you roll back without reinstalling anything.

The helper is bound to a single permission grant. When the grant changes or is
revoked it stops answering; it never re-attaches to a new one, and it never
opens `/dev/uinput` for reads.

Numbers from this machine, all measured rather than estimated:

| | before | now |
|---|---|---|
| Screenshot of both monitors, at the tool level | 5113 ms | **789 ms** |
| Bringing a window to the front | 6701 ms | **~5 ms** |
| Launching a closed application | GNOME search, seconds | **~0.4 s**, no keystrokes |
| Accessibility dump of one window | 100–112 ms | **14–18 ms** |
| Clicking a control through the tree | 52 ms | **13 ms** |

The screenshot number is the one that moved most recently, and not for the
reason anyone expected: 84 % of a capture was Pillow writing the PNG with
`optimize=True`, which cost 3.1 seconds to make the file 5 % smaller.

## Two transports, one server

```
  LOCAL (stdio) — the main path            REMOTE (HTTP + OAuth) — optional
  Claude Code · Codex · Claude Desktop     phone · another machine
        │                                        │
        │ the client spawns the server           │ OAuth 2.1 + HTTPS
        │ no network, no OAuth                   ▼
        │ nothing to start or stop          Tailscale Funnel
        ▼                                        ▼
   python -m pcbridge.server --stdio      127.0.0.1:8765 (systemd user unit)
        └────────────────┬───────────────────────┘
                         │
      ┌──────────────────┼──────────────────┬───────────────────┐
      ▼                  ▼                  ▼                   ▼
 coding agents      tmux session      shell / files       desktop (uinput,
 (background jobs)  (live terminal)                        AT-SPI, screencast)
```

Local clients need nothing running: they start the server themselves and kill it
on exit. Remote access is a separate, deliberate decision — it is the thing that
puts the machine on the internet.

## Quick start

```bash
git clone https://github.com/Eymistaken/Pcbridge.git && cd Pcbridge
./install.sh                 # venv, dependencies, systemd user unit
./connect.sh --apply         # register the server with your local MCP clients
./doctor.sh                  # 35 diagnostic checks
```

To enable desktop control (keyboard, mouse, screen) — a separate, conscious step:

```bash
sudo ./setup_uinput.sh                       # udev rule for /dev/uinput, once
# then set [desktop] enabled = true in config.toml
systemctl --user restart pcbridge
```

Even then, every GUI tool refuses to act until an agent calls `desktop_unlock`,
which grants a **time-limited** permission that expires on its own.

Full instructions, client-by-client setup and troubleshooting live in
**[KURULUM.md](KURULUM.md)** (Turkish).

## Architecture

| Layer | File | Role |
|---|---|---|
| Server | `pcbridge/server.py` | Builds the FastMCP app, ASGI middleware, `/consent`, `/healthz`, `/shot/<token>` |
| Config | `pcbridge/config.py` | TOML → dataclasses; catches model/effort mistakes **at load time** |
| Auth | `pcbridge/auth.py` | A complete miniature OAuth 2.1 server (DCR + PKCE + refresh) |
| Tools | `pcbridge/tools.py` | All 34 MCP tools |
| Jobs | `pcbridge/jobs.py` | Background processes + agent output parsers |
| Resolver | `pcbridge/models.py` | Agent/model/effort selection. Pure function, no I/O |
| Desktop | `pcbridge/desktop/` | Monitors, input, capture, accessibility tree, batch engine, safety gate, execution lock |
| Native client | `pcbridge/native/` | Supervises `pcbridge-native` over framed stdio: handshake, grant binding, diagnostics |
| Native core | `rust/` | The Rust helper itself — Mutter ScreenCast, `uinput`, AT-SPI |
| CLI shims | `pcbridge/cli/`, `bin/` | `pcb-shot` / `pcb-do` — usable from a plain shell, independent of MCP |

Three design rules are enforced throughout:

- **All internal APIs use global canvas coordinates**, and exactly one function
  decides which space an incoming coordinate is in: `capture.to_global()`.
  Pass `monitor=` and it adds that monitor's offset; pass a screenshot's
  `shot=` id and it applies both the offset **and** the scale that image was
  reduced by; pass neither and the coordinate was already global. Doing this
  conversion in two places means silently clicking 1920 px to the left, and
  nothing reports the error.
- **When intent is ambiguous, ask — never guess.** A coordinate read off a
  scaled-down screenshot and a genuine global coordinate can be the exact same
  pair of numbers. If a recent screenshot was scaled and the coordinate falls
  inside it but no `shot` was given, the call is refused with both ways out
  spelled out. The same rule produced `expect_focus` in the batch engine: an
  accident once came out of an intention nobody had declared.
- **The batch engine knows nothing about real devices.** Dependencies point one
  way, so budget and stop logic is testable without sending a single real click.

Agents are defined in `config.toml`, not in code — adding a new CLI means editing
TOML, not Python.

## Safety

Desktop control passes through a five-layer gate (`pcbridge/desktop/safety.py`):

1. `[desktop] enabled` — **false by default**
2. Screen lock — nothing is sent behind a locked screen
3. Time-limited permission — `desktop_unlock(minutes)`, expires on its own, state on disk
4. Collision guard — if the user touched the keyboard recently, writes are refused
5. Rate limit + audit log

The audit log records **what happened, never the content**: the command yes, its
output no; the file path yes, its contents no; the text length yes, the text no.

## Security

pcbridge is, by construction, remote code execution on a personal desktop. The
honest summary:

- **stdio is a security regression, deliberately.** A local MCP client spawns the
  server directly — no OAuth, no network, no consent page. The authorisation
  boundary becomes "whoever can run programs as this user", which is the same
  boundary the client already had.
- **The HTTP path is properly authenticated** — OAuth 2.1 with PKCE, HTTPS via
  Tailscale Funnel, a consent screen, and revocable tokens.
- **Desktop control is a different risk class** from shell access. It reaches
  every application in the open session — logged-in browser sessions, the
  password manager, chat history. That is why it defaults to off and why the
  permission expires.
- `config.toml` holds a password and a static token. It is in `.gitignore` and
  stays there.

The detailed assessment — including what `computer_task` escalates and why — is
in [KURULUM.md](KURULUM.md#güvenlik--dürüst-değerlendirme).

## GNOME Shell extension

`gnome-extension/` holds an optional GNOME 46 extension. Everything it does is
tied to pcbridge's permission file, which it only ever **reads**.

**It makes agent activity impossible to miss.** While the desktop permission is
open, a soft white glow frames every monitor and fades away when the permission
ends. Measured cost: below the noise floor (≈0.5 % of one core either way,
+0.08 MB RSS).

**It exposes exactly one D-Bus method, `ActivateWindow`**, which brings an
already-open window to the front. That is the entire surface: it cannot list,
move, resize or close windows, and it cannot open anything. It refuses when the
permission is closed, when a name matches more than one window, and for windows
that are not in the taskbar — all three checked against a real session. Without
the extension pcbridge falls back to typing the application's name into GNOME
search, which is slower and coarser. This method is why raising a window costs
about 5 ms instead of 6.7 seconds.

There is also a layer that draws where the agent's pointer is. It is **off by
default** and stays off until you create a marker file. It once broke physical
mouse input; the fix — applying the position once per frame instead of once per
event — has been measured in a nested shell but **not yet confirmed on real
hardware**.

See [gnome-extension/README.md](gnome-extension/README.md).

## Testing

Plain scripts, no pytest required:

```bash
./.venv/bin/python tests/test_models.py       # resolver + agent output parsers
./.venv/bin/python tests/test_desktop.py      # desktop layer, 592 checks, sends no input
./.venv/bin/python tests/test_test_safety.py  # live-test selector safety
gjs -m gnome-extension/tests/test_state.js    # extension state watcher, no shell needed
```

Contract tests live under `tests/contracts/`, live tests that really move the
mouse under `tests/live/` — those are skipped unless you set the matching
environment variable, one per capability. The Rust side runs with
`cargo test --no-fail-fast`, which matters: `cargo test` stops after the first
failing *target* and the ones behind it do not appear at all.

End-to-end tests need the server running. Section 12 of `test_e2e.py` runs a
**real** `claude -p` and burns quota — pass `PCBRIDGE_TEST_NO_AGENT=1` for daily
runs.

Nothing in this project counts as working because it "didn't throw". The claims
in these documents are measurements, and the ones that surprised us are recorded
with their numbers.

## Documentation

| File | What |
|---|---|
| [KURULUM.md](KURULUM.md) | Full manual: install, client setup, security assessment, troubleshooting (TR) |
| [KULLANIM.md](KULLANIM.md) | User-facing tool catalog and permission map (TR) |
| [config.example.toml](config.example.toml) | Every setting, documented — the real reference |
| [CLAUDE.md](CLAUDE.md) | Guidance for AI agents working on this repo, plus measured machine facts |
| [GELISTIRME.md](GELISTIRME.md) | Adding a tool, protocol pitfalls (TR) |
| [PLAN.md](PLAN.md) | Active contract for the native core migration: phases, tasks, gates |
| [UYGULAMA.md](UYGULAMA.md) | Historical record: what was built and why |
| [WALKTHROUGH.md](WALKTHROUGH.md) | What's next, and what has been done so far |
| [docs/native/](docs/native/) | The native helper: wire protocol, capture state machine, packaging, and its verification against the old path on a real desktop |

## Status and scope

Personal software, and the roadmap it was built against is finished. It targets
one machine and one desktop stack: **GNOME 46 on Wayland.** X11 is not a goal,
Windows and macOS are not planned, and neither is packaging for other
distributions — those were considered and deliberately dropped rather than left
half-done.

It is published because the measurements in it — what GNOME 46 on Wayland does
and does not allow a program to do — were expensive to obtain and may save
someone else the time. Several of them contradict the obvious guess.

## License

GPL-3.0 — see [LICENSE](LICENSE).
