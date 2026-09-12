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
| **Use the desktop** | Virtual keyboard and absolute mouse via `uinput`, window focus, app launch. |
| **Read the screen** | Accessibility tree as text (cheap, coordinate-free) or a silent screenshot (PipeWire screencast, no flash). |
| **Click what you see** | Every screenshot carries a short id. Send the pixel you see plus that id — the server applies the offset and the scale, so the model never does the arithmetic. |
| **Batch it** | Run a whole sequence of GUI actions in one call, with budget and focus guards. |

34 tools in total. The desktop half is **off by default** and stays off until you
opt in.

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
| Desktop | `pcbridge/desktop/` | Monitors, input, capture, accessibility tree, batch engine, safety gate |
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

`gnome-extension/` contains an optional GNOME 46 extension that makes agent
activity impossible to miss: while the desktop permission is open, a soft white
glow frames every monitor and fades away when the permission ends.

It is **purely visual** — it never clicks, never types, never changes anything.
It only reads pcbridge's permission state file. Measured cost: below the noise
floor (≈0.5 % of one core either way, +0.08 MB RSS).

See [gnome-extension/README.md](gnome-extension/README.md).

## Testing

Plain scripts, no pytest required:

```bash
./.venv/bin/python tests/test_models.py     # resolver + agent output parsers
./.venv/bin/python tests/test_desktop.py    # desktop layer, 398 checks, sends no input
gjs -m gnome-extension/tests/test_state.js  # extension state watcher, no shell needed
```

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

## Status and scope

Personal software. It targets one machine and one desktop stack; X11 is not a
goal, and neither is packaging for other distributions. It is published because
the measurements in it — what GNOME 46 on Wayland does and does not allow a
program to do — were expensive to obtain and may save someone else the time.

## License

GPL-3.0 — see [LICENSE](LICENSE).
