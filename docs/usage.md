# Using pcbridge

pcbridge offers 36 MCP tools. This page lists them, says which ones need the
desktop grant, and shows the usual ways to use them. Every tool's own
description (what a client sees) says when to use it; this is the overview.

## Which client, which path

| Client | How it connects | Authentication |
|---|---|---|
| Claude Code, Codex, Claude Desktop | `pcbridge stdio`: a small relay to the resident daemon over a user-only Unix socket | none; the process boundary is the authorization |
| A phone, another machine, a web client | HTTPS on the daemon's HTTP port through Tailscale Funnel (`pcbridge remote start`) | OAuth 2.1 (password consent page) or the static token |

`pcbridge connect` registers the local clients; nothing needs to be started
by hand. If the daemon is not running, the first client starts it through
systemd socket activation (~0.7 s); after that a new client is connected in
tens of milliseconds.

## Tool catalog

`ro` = read-only. **Grant** = needs `desktop_unlock` first (and `[desktop]
enabled = true`). Every call is written to `audit.log` without its content.

### Coding agents and jobs

| Tool | Kind | What it is for |
|---|---|---|
| `list_agents` | ro | The configured agents, whether their CLI is found, their models and effort levels |
| `agent_run` | destructive | Start Claude Code / Antigravity CLI / another configured agent with a prompt; returns a job id at once |
| `job_status` | ro | Status, parsed steps and the final answer of a job (can wait a little) |
| `job_output` | ro | The raw terminal output of a job |
| `job_list` | ro | Recent jobs, newest first |
| `job_cancel` | destructive | Stop a job (SIGTERM, then SIGKILL) |

Agent calls are never synchronous: an agent can work for ten minutes and no
MCP call may block for more than 110 s. Continue a conversation with the
same agent by passing its `session_id` as `resume_session`.

### Shell, files, live terminals

| Tool | Kind | What it is for |
|---|---|---|
| `shell_run` | destructive | A short command; output returned |
| `shell_run_background` | destructive | A long command (build, install) as a job |
| `fs_list`, `fs_read`, `fs_search` | ro | Directory listing, file contents, text search (ripgrep if present) |
| `fs_write` | destructive | Create or overwrite a text file |
| `tmux_start` | write | Open a persistent terminal session, optionally with an interactive CLI in it |
| `tmux_send`, `tmux_keys` | destructive | Type into it, or press raw keys (answer an agent's y/n prompt) |
| `tmux_capture`, `tmux_list` | ro | Read a session's screen; list sessions |
| `tmux_kill` | destructive | Close a session |
| `system_status` | ro | Uptime, load, memory, disk, GPU, jobs, terminal sessions, the daemon |
| `notify` | write | A desktop notification |

These do not go through the desktop grant: `[desktop] enabled = false` does
not turn them off. That is deliberate (see [security.md](security.md)).

### Desktop

| Tool | Kind | Grant | What it is for |
|---|---|---|---|
| `system_capabilities` | ro | no | What works on this machine now: capture, input, accessibility, clipboard, windows, the platform and the grant |
| `screen_info` | ro | no | Monitors, their sizes and positions in the shared coordinate space, the primary one |
| `desktop_unlock` | destructive | - | Open the time-limited grant (the agent's own call; no person is asked) |
| `desktop_lock` | write | - | Close the grant now, release held keys, stop screen sharing |
| `ui_dump` | ro | yes | The screen as text: buttons, menus, text boxes, each with a short id |
| `ui_click`, `ui_set_text` | destructive | yes | Act on an element by that id: no coordinates, cannot miss |
| `window_list` | ro | yes | Open windows and which one has focus |
| `window_focus` | destructive | yes | Bring an app to the front, launching it if needed |
| `screen_capture` | ro | yes | A screenshot per monitor, a region, or the focused window |
| `find_text`, `wait_for_text` | ro | yes | OCR as plain text: where a word is, or wait for it (needs `tesseract-ocr`) |
| `mouse` | destructive | yes | Move, click, drag, scroll, relative motion (`move_by`) |
| `keyboard` | destructive | yes | Type text (through the clipboard) or press keys |
| `computer_batch` | destructive | yes | A whole list of actions in one call, with the result |
| `computer_task` | destructive | yes | Hand a long graphical task to a local agent that looks at the screen itself |

## Reading and driving the screen

1. `system_capabilities` once, to see which paths work.
2. `desktop_unlock` (default 15 minutes; it closes by itself 90 s after the
   last desktop action).
3. **Prefer `ui_dump` + `ui_click` / `ui_set_text`.** It is ~0.1 s, a few
   hundred tokens, and it acts on widgets rather than pixels. Electron apps,
   canvases and games publish no tree (Vesktop: 0 nodes); use a screenshot
   there.
4. With a screenshot, give the pixel **exactly as you see it** plus the
   picture's id: `mouse(action="click", x=…, y=…, shot="m2-a1b2c3")`.
   pcbridge applies the monitor offset and the scale. A screenshot older than
   60 s is refused for coordinates: take a new one.
5. Group actions: `computer_batch` runs a list with one grant check per
   action, stops when a click moves the focus somewhere unexpected, and
   returns what happened.
6. `desktop_lock` when done (or let it lapse).

`find_text` / `wait_for_text` return coordinates without an image and are
the cheapest way to find a known label in an app with no accessibility tree.

## Profiles

`[tools] profile` in the config offers a smaller set to save the client's
context: `full` (default, all 36), `core` (20, no desktop tools) or `desktop`
(22: the desktop tools plus job and status tools). It is read at startup;
restart pcbridge and the client after changing it.

## From a terminal

| Command | Does |
|---|---|
| `pcbridge status` | Daemon, grant, jobs, remote tunnel |
| `pcbridge doctor [--json] [--fix]` | Checks everything, with the fix for each problem |
| `pcbridge lock` (`bridgekilit`) | The kill switch: close the grant and stop screen sharing, whichever process opened it |
| `pcbridge unlock --minutes N` | Open the grant from a terminal |
| `pcbridge remote start/stop/status` (`bridgeac`/`bridgekapat`/`bridgedurum`) | The Tailscale Funnel tunnel |
| `pcbridge logs [-f]` | The daemon's journal |
| `pcbridge stop [--keep-jobs]` | Stop the daemon (and, unless told otherwise, running jobs) |
| `pcb-shot`, `pcb-do` | Screenshot and action shells for a local agent's Bash (see `skills/computer-use/SKILL.md`) |

The GNOME extension's panel icon shows the same state and has "Lock desktop
control now" in its menu.
