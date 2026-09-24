# Plan: a settings CLI and a terminal UI

Status: in progress (started 2026-09-24). Each step ends with a local commit;
the branch is pushed to `origin/main` when every step is done.

## Context

The roadmap asks for "one CLI for all of the settings": today a setting is
changed by editing `~/.config/pcbridge/config.toml` by hand, the desktop grant
has two separate commands, and nothing in the terminal shows which of the 37
tools a client actually gets. The maintainer asked for:

- `pcbridge` with no arguments opens a terminal UI where every setting can be
  changed, the desktop grant can be locked and unlocked from the most visible
  place, the tools can be searched by name or description, and the active
  tools are easy to see;
- mouse clicks in the terminal, where the terminal supports them;
- a minimal look: the terminal's own colors, one accent, no decoration;
- English only, on every distribution pcbridge supports (Zorin/Ubuntu with
  GNOME, Arch with KDE Plasma);
- direct commands that do the same work without the UI, and `pcbridge list`
  to show them.

Decisions taken with the maintainer (2026-09-24):

- Dependencies: **Textual** (the UI, mouse support) and **tomlkit** (editing
  TOML without losing comments). Both are pure Python and go into the venv
  every install kind ships, so no distribution package is needed.
- Command names stay as they are: `pcbridge lock` / `pcbridge unlock`
  (the `bridgekilit` alias depends on them). New: `list`, `settings`, `get`,
  `set`, `reset`, `tools`, `restart`, `ui`.
- "Active tools" follows `[tools] profile`; there is no per-tool switch.

## Ground rules that shape the design

- `config.toml` holds the password and the static token: the CLI and the UI
  **never print them**, only `set` / `hidden`, and a new value is read from a
  prompt or generated, never taken from argv (it would land in the shell
  history).
- A save never loses data: the new text must parse, must pass the real
  loader (`load_config` on a temporary copy), gets a timestamped backup of
  the old file first (`config._backup`), and is written atomically with mode
  0600 (the pattern of `config.migrate_config`). Only the edited keys change;
  every other byte, comment and block (for example `[agents.antigravity]`)
  stays as it was.
- `[desktop] enabled` keeps its default of `false`. Turning it on from the UI
  asks for confirmation; the grant buttons never touch it.
- Most settings are read at daemon start. The UI and `set` say so and offer
  `pcbridge restart`, which restarts only when no job is running
  (`ops._restart_when_idle`).
- Every text a user reads is English (`tests/contracts/test_english_only.py`
  scans the new modules automatically).

## Steps

1. **Plan.** This file, and the steps in `ROADMAP.md`.
2. **Dependencies.** `textual` and `tomlkit` in `pyproject.toml`,
   `requirements.txt` and pinned in `packaging/constraints.txt` (the `.deb`,
   the Arch package and the user install all read it). Verify: install into
   `.venv`, `pip check`, import both.
3. **Settings core** (`pcbridge/settings.py`, no UI). A registry of every
   setting the loader reads: top-level keys, `[server]`, `[auth]`, `[paths]`,
   `[limits]`, `[native]`, `[desktop]`, `[tools]` and each `[agents.NAME]`
   field. For each: type, default, allowed values, whether it is secret,
   what a change needs (nothing, a daemon restart, a client restart too),
   and its description, taken from the comment above the key in
   `config.example.toml` so the documentation has one source. A
   `ConfigEditor` reads with tomlkit, gets/sets/resets typed values and saves
   under the rules above. Contract tests: every loader key is in the
   registry with an English description; round trips for every type; an
   invalid value is refused and the file is untouched; untouched bytes stay
   byte-identical; backup and mode 0600; secrets never appear in output.
4. **Grant helpers** (`pcbridge/cli/grant.py`). One place that reads the
   grant state and locks/unlocks, used by `pcbridge lock/unlock/status` and
   the UI. Behavior of the existing commands must not change (existing
   `test_cli.py` passes unchanged).
5. **Tool catalog** (`pcbridge/toolcatalog.py`). Name, title, description,
   hints, parameters and whether the current profile offers each tool, built
   from the same FastMCP registration the server uses (so it cannot drift).
   Measure how long it takes; cache per version if it is slow. Test: the
   names equal `TOOL_HINTS`, the active sets equal `tools_in_profile`.
6. **Direct commands.** `pcbridge list` (generated from the command table,
   so a new command cannot be missing), `settings [FILTER] [--json]`,
   `get KEY`, `set KEY VALUE` (secrets: `set auth.password` prompts,
   `--generate` for the token), `reset KEY`, `tools [QUERY] [--all]
   [--json]`, `restart [--wait S]`, `ui`. Subprocess tests against a
   throwaway HOME, like `tests/contracts/test_cli.py`.
7. **Terminal UI, frame.** `pcbridge/tui/`: a top bar always visible with the
   daemon state and the desktop grant (`LOCKED` / `OPEN mm:ss left`) and a
   clickable Lock / Unlock button (key `l`), tabs Overview / Settings /
   Tools / Commands, a footer with the keys. Theme: the terminal's ANSI
   colors plus one accent. Slow work (lock, unlock, restart) runs in worker
   threads. `pcbridge` with no arguments opens it when stdin and stdout are a
   terminal; otherwise it prints the help as before (scripts and the stdio
   relay are unaffected). Without Textual it says how to install it. Tests
   with Textual's headless `run_test()` pilot, clicks included.
8. **Terminal UI, Settings.** Sections on the left, keys with value, default
   and a "changed" mark on the right; editing with the widget for the type
   (switch, select, number, text, TOML text for lists and tables); the
   description under it; pending changes saved together with one backup;
   "restart needed" with a Restart button. Confirmation for `[desktop]
   enabled`, `[auth]`, `host` and `port`.
9. **Terminal UI, Tools.** A search box filtering by name and description as
   you type; a table of name, state (active / not in profile / needs desktop
   control) and title; the full description, hints and parameters of the
   selected tool; a count of active tools for the profile.
10. **Live check in a real terminal.** Open `gnome-terminal` through
    pcbridge's desktop control, run the UI, click tabs and buttons with the
    virtual pointer, verify with screenshots, lock the grant afterwards.
    Record startup time and what was measured in
    `docs/dev/measured-facts.md`.
11. **Documentation.** `docs/usage.md` (terminal UI, command table),
    `docs/configuration.md` (`pcbridge set`), `README.md`, `CHANGELOG.md`
    (Unreleased), `ROADMAP.md` (the finished item moves to the changelog).
12. **Finish.** Full test suite, `cargo test` untouched, secret scan of the
    diff (`config.toml` untracked, no password or token), push to
    `origin/main`, then install the checkout into the user's venv and restart
    the daemon when idle so `pcbridge` in a terminal opens the UI.

## Verification

- `./.venv/bin/python -m unittest discover -s tests/contracts -t .`
- `./.venv/bin/python -m unittest discover -s tests/integration -t .`
- `./.venv/bin/python tests/test_models.py`, `tests/test_desktop.py`
- `PCBRIDGE_TEST_NO_AGENT=1 ./.venv/bin/python tests/test_e2e.py`
- The UI in a real GNOME terminal, driven with the mouse (step 10).

## Progress

- [x] 1. Plan
- [x] 2. Dependencies
- [x] 3. Settings core
- [x] 4. Grant helpers
- [x] 5. Tool catalog
- [ ] 6. Direct commands
- [ ] 7. Terminal UI, frame
- [ ] 8. Terminal UI, Settings
- [ ] 9. Terminal UI, Tools
- [ ] 10. Live check in a real terminal
- [ ] 11. Documentation
- [ ] 12. Finish
