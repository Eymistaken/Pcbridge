# CLAUDE.md

Guidance for Claude Code (and other agents) working in this repository.

## The project

pcbridge makes the maintainer's Linux desktop (Zorin OS 18 / GNOME 46 /
Wayland) drivable over MCP: 36 tools for coding-agent jobs, background
jobs, tmux, shell and files, and, under `[desktop]`, a virtual keyboard and
pointer plus screen reading. Since 2.1 it also supports KDE Plasma 6 and
Arch Linux; those are tested in a VM (`scripts/dev/arch-vm.sh`), never on
this desktop.

Since 2.0 **one resident daemon** (`pcbridge serve`, `pcbridge.service`,
started by `pcbridge.socket`) serves every client. Local clients (Claude
Code, Codex, Claude Desktop) run `pcbridge stdio`, a relay to the daemon's
Unix socket: no network, no OAuth. Remote clients use HTTPS + OAuth 2.1
through Tailscale Funnel (optional). Architecture:
[docs/architecture.md](docs/architecture.md).

Open work: [ROADMAP.md](ROADMAP.md). Measured facts, with the numbers and the
reasons: [docs/dev/measured-facts.md](docs/dev/measured-facts.md). **Read the
relevant part of it before changing input, capture, accessibility, monitors,
the extension or the daemon.** Do not copy status summaries into other files:
a copy drifted within two days once.

The repository is **public** and pushed to `origin/main`. Push only when
asked, and scan for secrets before every push: is `config.toml` tracked, do
the real password or static token appear in the diff.

## Commands

```bash
pcbridge status                         # daemon, grant, jobs, tunnel
pcbridge doctor [--json] [--fix]        # every check, with the fix
pcbridge logs -f                        # the daemon's journal
pcbridge update                         # restart into the installed/pulled code when idle
pcbridge stop [--keep-jobs]             # stops the daemon AND running jobs by default
pcbridge lock                           # kill switch (alias bridgekilit)
pcbridge remote start|stop|status       # the tunnel (bridgeac / bridgekapat / bridgedurum)
scripts/build-native.sh                 # release build of the native helper
packaging/build-deb.sh                  # the .deb
```

Before restarting the daemon, check `job_list` and that the grant is closed.
Jobs live in their own scopes and survive a restart, but `pcbridge stop` ends
them. The service starts at login; **the tunnel does not** (the service
listens on 127.0.0.1; the tunnel is what exposes the machine).

A client started before a code change keeps its old process until it
restarts: do not verify your own changes through your own MCP tools, use a
fresh process (`tests/readiness/check.py`, or HTTP with the static token like
`tests/test_e2e.py`).

Tests, live-test flags and the rules for testing on this desktop:
[docs/dev/contributing.md](docs/dev/contributing.md). The short version:

```bash
./.venv/bin/python tests/test_models.py
./.venv/bin/python tests/test_desktop.py          # sends NO input unless PCBRIDGE_TEST_* flags
./.venv/bin/python -m unittest discover -s tests/contracts -t .
./.venv/bin/python -m unittest discover -s tests/integration -t .
(cd rust && cargo test --workspace --locked --no-fail-fast)
PCBRIDGE_TEST_NO_AGENT=1 ./.venv/bin/python tests/test_e2e.py   # ALWAYS with NO_AGENT
```

**`test_e2e.py` section 12 runs a real `claude -p` and spends quota**; it used
up the maintainer's daily limit once (2026-08-03).

## Rules that do not change

**MCP tools**

- Docstrings and `Field(description=...)` are English and say **when** to
  use the tool, not only what it does. **Every text a user or a model reads
  is English** (`tests/contracts/test_english_only.py` guards it).
- Plain text returns `str`, long output trimmed with
  `jobslib.tail_chars(text, 4000)`; with images, `list[ContentBlock]`.
- Dynamic desktop tools declare `@mcp.tool(output_schema=None, ...)`. Desktop
  execution errors return `ToolResult(..., is_error=True)` with readable text
  in `content` and stable fields in `structuredContent.error`. Scope:
  `pcbridge.desktop` for the grant; `os.capture`, `os.pointer`,
  `os.keyboard`, `os.accessibility`, `os.window` or `os.session` for OS
  permissions.
- FastMCP is pinned to `3.4.5`; verify `ToolResult` and `output_schema=None`
  before changing it.
- Every tool has all four hints in `TOOL_HINTS` (`tools.py`); a wrong
  `readOnlyHint` lets a client run a dangerous tool silently.
- **No call blocks for more than 110 s**; long work goes to `jm.start()`.
- Path parameters go through `_resolve_dir` / `_resolve_file`.
- A desktop tool passes `_guard()` / `SafetyGate.check()` and is audited with
  `gate.audit(...)`: what was done, never the content.

**Do not touch**

- `MetadataNormalizer` (trailing slash of the RFC 8414 issuer) and
  `BasicAuthFormShim` (the SDK looks for `client_id` in the form body; Google
  sends it only in the Basic header) in `pcbridge/app.py`: Google's OAuth flow
  works only because of them. They are no-ops for other clients. **Keep them.**
- The OAuth logic in `auth.py`, unless the task is about it.
- The `[agents.antigravity]` block of the maintainer's config.

**Security**

- `config.toml` holds the password and the static token; it stays out of git.
  **Never log, print or commit its contents.**
- `[desktop] enabled` defaults to `false`. **Do not change that.**
- Every new setting goes into `config.example.toml` **with its comment** and
  is actually **read** in `config.py` (skipped once: the fields existed and
  the configured value did nothing; `[tools] profile` repeated it in 1.x).
- `desktop_unlock` never asks a human; the agent opens the grant itself.
- Setup steps that need sudo are told to the maintainer, not run.
- Never delete files that are not in git permanently; use `gio trash`.

**Not solutions**: switching to X11, removing
`--dangerously-skip-permissions`, printing secrets, enabling desktop control
by default. When a fact contradicts the plan, **do not invent**: say so, fix
it with the reason, then continue.

## Two design decisions defended hard

- **One coordinate space.** Every internal API uses global canvas
  coordinates; only `capture.to_global()` interprets `monitor=` / `shot=`.
  Converting in two places once sent clicks 1920 px to the left, silently.
- **`batch.py` knows no devices** (`ops` -> `batch` only), so budgets and
  stop rules are tested without sending a click.

## Testing on this desktop is dangerous

You are on the machine pcbridge controls. A uinput key goes wherever the
focus is: a `type` test can type into your terminal and press Enter.

1. Input tests go into an empty window first (`gnome-text-editor`).
2. Move, verify with a screenshot, then click.
3. Emergency stop: `pcbridge lock`, or `pcbridge stop`.
4. Do not start long or repeated input runs without telling the maintainer.
5. After a click, assume the focus moved.
6. Screenshots go stale: act on their coordinates at once.
7. Never lock the screen, log out or restart GNOME Shell; test in a nested
   or headless shell under its own `dbus-run-session`, and collect what it
   leaves (`gnome-extension/nested.sh --clean`).

Both real accidents came from acting on an unverified assumption: 23 desktop
items went to the trash (2026-08-02), and a click from a 69-second-old
screenshot landed in another app (2026-08-03). Each now has a guard in the
code; guards limit damage, they do not make mistakes impossible.

## Working style

- Make your task list and have the maintainer confirm it before starting.
- **Test after every step.** "It raised no error" is not evidence: verify
  with `IdleMonitor`, a screenshot, a window title or a fresh process.
- A decision that depends on a measurement is made after the measurement.
  Record new measurements in `docs/dev/measured-facts.md`.
- Commit at the end of each section. Commits are authored as Eymistaken
  with the GitHub noreply address; the maintainer's personal name does not
  appear in the repository.
- Suggest `edit` (gnome-text-editor) instead of `nano` for file edits;
  `edit admin:///path` for root files.

## Documents

| | |
|---|---|
| [README.md](README.md) | The product page |
| [docs/](docs/) | install, usage (tool catalog, permission map), configuration, security, troubleshooting, architecture |
| [docs/dev/](docs/dev/) | contributing, measured facts, desktop rules |
| [docs/native/](docs/native/) | the Rust helper: protocol, capture, packaging, verification |
| [gnome-extension/README.md](gnome-extension/README.md) | the extension, its emergency undo, nested development |
| [config.example.toml](config.example.toml) | every setting, commented: the reference |
| [CHANGELOG.md](CHANGELOG.md), [ROADMAP.md](ROADMAP.md) | what changed, what is open |
| [AGENTS.md](AGENTS.md) | a thin pointer here for other agents; do not copy this file into it |
