# Terminal UI redesign

The details behind the ROADMAP item *Update every screen of the terminal
UI, not only the Overview*. ROADMAP.md keeps the item and its order; this
file keeps what the redesign has to fix and the decisions taken so far.

Collected with the maintainer on 2026-11-14, against pcbridge 2.9.0. The
visual design is done outside this repository (Claude Design); this file
is the brief and the checklist that design and the code work against.

Each point is marked by where it came from:

- **Maintainer**: a gap the maintainer found using the terminal UI.
- **Proposed**: added in the review and accepted as a direction; the
  details are open until the design settles them.
- **Open**: a decision not taken yet.

## The root problem

The Settings tab mirrors the sections of `config.toml` (General, Server,
Authentication, Paths, Limits, File links, Power, Native helper, Desktop
control, Panel icon, Tools, one entry per agent). A user thinks in tasks
("turn the tunnel on", "pick the agent", "show the tray icon"), and the
screen asks which TOML section that lives in. Most of the gaps below follow
from this. **Proposed:** arrange the terminal UI by task; keep the raw,
file-shaped view for the few who want it, as an Advanced tab.

## Overview

- **Maintainer:** the Overview gets a new design around a live Pcbridge
  logo. That is its own ROADMAP item, *Responsive Overview with a live
  Pcbridge logo*, and part of this redesign.
- **Proposed:** health at a glance. Show the `pcbridge doctor` summary
  (for example "46 ok, 4 warnings") on the Overview; selecting a warning
  shows its explanation and, where doctor has one, a Fix action
  (`doctor --fix` for that check only).
- **Proposed:** restart and update belong here, next to the daemon line,
  instead of only on the `r` key and in the Commands table.
- **Proposed:** the unlock button lets the user choose the duration; today
  it always says "Unlock 15 min" (`[desktop] unlock_default_minutes`).

## Settings

- **Maintainer:** the settings that matter for daily use are either missing
  or hard to reach. Turning remote access on and off is the clearest
  example: it is not in Settings, and the Remote tab has no switch for it
  either; the Overview only says "Remote tunnel open".
- **Maintainer:** `default_agent` is a text field, so the agent name has to
  be typed by hand. It is the agent `agent_run` uses when a call names
  neither an agent nor a model of a known agent. **Proposed:** a choice
  between the configured agents (today claude, antigravity, codex).
- **Maintainer:** the panel (tray) icon setting could not be found. It
  exists ("Panel icon" in the section list), so this is a discoverability
  gap: it belongs with the other desktop settings, not as one more entry in
  a list of file sections.
- **Proposed:** every setting gets the control its type calls for. The
  settings schema already knows the kinds (`pcbridge/settings.py`:
  `bool`, `int`, `str`, `choice`, `list`, `table`); the screen should use
  them: a switch for a boolean, a picker for a choice, a bounded number
  input, and a Replace action for a secret. A key whose values are a known
  set but is declared `str` today (`default_agent`) gets a `choice`.
- **Proposed:** when a saved setting needs a restart ("applies after
  restarting the daemon and the clients"), show a lasting notice with a
  Restart button until the restart happens, instead of a gray line under
  the field.
- **Proposed:** the current Settings screen, filter included, stays as the
  Advanced tab, so every key remains reachable without the task tabs
  having to show all of them.

## Commands

- **Maintainer:** the Commands tab only lists commands. Selecting one
  should run it and show its output in a proper output view.
- **Proposed:** the commands are not all alike, so they cannot all just
  run on a click:
  - Read-only commands run at once and show their output: `status`,
    `doctor`, `tools`, `clients`, `logs` (`-f` streams until stopped),
    `keep-awake` without arguments.
  - Commands that change state ask first: `restart`, `stop`, `update`,
    `lock`, `unlock`, `remote start|stop|revoke`, `connect`, `disconnect`,
    `reset`, `report`.
  - Interactive ones need their own dialog or stay out: `setup`,
    `uninstall [--purge]`, `set` for a secret.
  - Some do not belong in the terminal UI at all: `serve` and `stdio` are
    for the service and the MCP clients, `ui` and `list` are this screen.
- **Proposed:** move each action to the tab it belongs to (restart and
  update to the Overview, revoke to Remote, connect to Connections) and
  offer every command through Textual's command palette (`Ctrl+P`),
  searchable by name and description. **Open:** whether a separate Commands
  tab is still needed after that.

## Remote

- **Maintainer:** the Remote tab needs a new interface.
- **Proposed**, in this order on the screen:
  1. The tunnel's state and a switch to start and stop it.
  2. The public address, with a copy action, and a short "how to add this
     in claude.ai" line.
  3. The password (Replace) and the remote tool profile.
  4. The connected apps as a table: name, connections, last connected,
     token expiry, Revoke.
- **Proposed:** an action to clean up stale registrations. The maintainer's
  machine shows "Google: 1 connected, 16 registration(s)"; most of those
  are likely leftovers from earlier connections.

## New: Agents and jobs

- **Proposed:** running coding agents is one of pcbridge's main jobs, and
  the terminal UI does not show them. Add a view of the running and recent
  jobs: agent, prompt summary, state, elapsed time, the tail of the output,
  and Cancel. The default agent and the per-agent settings move here from
  Settings.

## New: Activity (the audit log)

- **Proposed:** show what was done through pcbridge, read from the
  existing audit log (`~/.local/state/pcbridge/audit.log`). The desktop,
  shell, agent and file tools already write one JSON line per action there;
  content is never recorded (the command but not its output, the path but
  not the file). This answers "what did an agent do on my desktop last
  night".
- Storage stays bounded: nothing new is stored. The log rotates at
  `[limits] audit_max_bytes` (5 MB) to one `.1` backup, so it never takes
  more than about 10 MB. Measured on the maintainer's machine
  (2026-11-14): 20,351 lines and 3 MB since 2026-08-01, so the limit is
  reached in about six months, after which the oldest entries drop.
- The view reads from the end of the file and pages backward, so a full
  log does not slow the screen; it filters by kind (desktop, shell, file,
  agent, remote access) and by client.
- Found in the review: `auth.py`'s `audit()` appends to the same file
  without the rotation check that `SafetyGate.audit()` does. Rotation still
  happens at the next desktop or shell entry, so the bound holds in
  practice; the two writers should become one function as part of this
  work.

## New: Logs

- **Proposed:** the daemon's journal (`pcbridge logs -f`) as a live,
  scrolling view inside the terminal UI.

## Not reviewed yet

- The Tools and Connections tabs, and the dialogs (the confirmation, the
  shown-once secret, the login password and the remote password dialogs)
  were not gone through in this review. They are in scope; their gaps
  are written here before work on them starts.

## Proposed tab layout

Overview · Desktop · Agents & Jobs · Connections · Remote · Tools ·
Activity · Logs · Advanced

- Desktop: the grant, desktop control on/off, the panel icon, the native
  helper's state and capabilities.
- Advanced: today's Settings screen.

**Open:** the final layout comes out of the design; this is the starting
point.

## Constraints for the design

The design has to be buildable in the terminal UI as it is:

- It is a Textual application drawn on a monospace character grid: no
  images, no custom fonts, no free positioning.
- Narrowest supported width: about 80 columns. Every screen is checked at a
  normal and a narrow width.
- Mouse and keyboard both work everywhere; every action has a key.
- Slow work (lock, unlock, restart, status, doctor) stays in worker
  threads, so the screen never freezes.
- The grant and daemon safety behavior do not change: unlocking still
  asks when it comes from the keyboard, locking never asks.
- **Open:** colors. The screenshots show the terminal's own theme (a green
  background from the desktop theme). Either the terminal UI uses its own
  palette with a brand accent, the same in every terminal, or it follows
  the terminal's colors. Proposed: its own palette for accents and states,
  the background left to the terminal.

## Done when

Every point above marked Maintainer or Proposed is either fixed or
explicitly dropped with the reason, the screens not reviewed yet have
their gaps written here and fixed, and each screen is checked at a normal
and a narrow terminal width.
