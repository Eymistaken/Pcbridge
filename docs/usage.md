# Using pcbridge

pcbridge offers 37 MCP tools. This page lists them, says which ones need the
desktop grant, and shows the usual ways to use them. Every tool's own
description (what a client sees) says when to use it; this is the overview.

## Which client, which path

| Client | How it connects | Authentication |
|---|---|---|
| Claude Code, Codex, Claude Desktop, Antigravity CLI, Hermes Agent, OpenCode, Pi, oh-my-pi | `pcbridge stdio`: a small relay to the resident daemon over a user-only Unix socket | none; the process boundary is the authorization |
| A phone, another machine, a web client | HTTPS on the daemon's HTTP port through Tailscale Funnel (`pcbridge remote start`) | OAuth 2.1 (password consent page) or the static token |

`pcbridge setup` registers Claude Code, Codex and Claude Desktop; nothing
needs to be started by hand. If the daemon is not running, the first client
starts it through systemd socket activation (~0.7 s); after that a new client
is connected in tens of milliseconds.

### Connecting and disconnecting clients

`pcbridge clients` lists every client pcbridge can connect and its state:
`connected`, `outdated` (it runs an older command, often a pre-2.0 one; it
still works, and connecting it again updates it), `switched off`, `not
connected` or `not installed`. `pcbridge connect NAME...` and `pcbridge
disconnect NAME...` switch them, and so do the switches on the terminal UI's
Connections tab. Every file a change touches is backed up first under
`~/.local/state/pcbridge/backup-<time>/`, with a `ROLLBACK.md`. A client that
is running picks the change up when it restarts.

| Client | Name | Where pcbridge is registered | Disconnecting |
|---|---|---|---|
| Claude Code | `claude-code` | `~/.claude.json`, user scope, through `claude mcp` | removes the entry |
| Codex | `codex` | `$CODEX_HOME/config.toml` (`~/.codex`) | `enabled = false`; per-tool settings stay |
| Claude Desktop | `claude-desktop` | `~/.config/Claude/claude_desktop_config.json` | removes the entry |
| Antigravity CLI | `antigravity` | `~/.gemini/config/mcp_config.json`, through `agy mcp` | `agy mcp disable` |
| Hermes Agent | `hermes` | the active profile's `config.yaml`, through `hermes mcp` | removes the entry |
| OpenCode | `opencode` | `~/.config/opencode/opencode.json` (`type: "local"`) | `enabled: false` |
| Pi | `pi` | `~/.pi/agent/mcp.json`, read by the `pi-mcp-adapter` extension | `disabled: true` |
| oh-my-pi | `oh-my-pi` | `~/.omp/agent/mcp.json` (the default profile; `$PI_CODING_AGENT_DIR` when set) | `enabled: false`, or its `disabledServers` list |

Pi has no MCP of its own; install the extension with `pi install
npm:pi-mcp-adapter`, and `pcbridge clients` says so when it is missing. An
OpenCode config that exists only as `opencode.jsonc` with comments is not
rewritten; add the entry by hand there.

oh-my-pi can also start servers from other clients' configs once their
source is switched on in its settings (`enabledProviders`: `claude`,
`codex`, `opencode`). `pcbridge clients` then shows oh-my-pi as connected
through that client, and `pcbridge disconnect oh-my-pi` puts pcbridge on
oh-my-pi's `disabledServers` list, which leaves the other client connected.

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
| `panel_icon` | write | Show or hide pcbridge's GNOME panel icon when the user asks; hidden, it still appears whenever desktop control is granted |

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
context: `full` (default, all 37), `core` (21, no desktop tools) or `desktop`
(23: the desktop tools plus job and status tools). It is read at startup;
restart pcbridge and the client after changing it.

## From a terminal

`pcbridge` alone in a terminal opens the terminal UI (below); every command
also works without it. `pcbridge list` prints them all by group.

| Command | Does |
|---|---|
| `pcbridge` (or `pcbridge ui`) | The terminal UI: settings, the desktop grant, the tool list |
| `pcbridge list` | Every command, by group |
| `pcbridge settings [FILTER] [--changed]` | Every setting with its value; FILTER searches keys and descriptions |
| `pcbridge get KEY` | One setting: value, default, what it does |
| `pcbridge set KEY VALUE` / `pcbridge reset KEY` | Change a setting or put it back to its default (checked, backed up) |
| `pcbridge tools [QUERY] [--active]` | The MCP tools and which ones the profile offers; search, or one tool's parameters |
| `pcbridge clients` | Every MCP client and whether it is connected |
| `pcbridge connect NAME...` / `pcbridge disconnect NAME...` | Let a client use pcbridge, or stop it (backed up; `--dry-run`) |
| `pcbridge restart [--wait S]` | Restart the daemon to apply settings, only when no job is running and the grant is closed |
| `pcbridge status` | Daemon, grant, jobs, remote tunnel |
| `pcbridge doctor [--json] [--fix]` | Checks everything, with the fix for each problem |
| `pcbridge lock` (`bridgekilit`) | The kill switch: close the grant and stop screen sharing, whichever process opened it |
| `pcbridge unlock --minutes N` | Open the grant from a terminal |
| `pcbridge remote start/stop/status` (`bridgeac`/`bridgekapat`/`bridgedurum`) | The Tailscale Funnel tunnel |
| `pcbridge logs [-f]` | The daemon's journal |
| `pcbridge stop [--keep-jobs]` | Stop the daemon (and, unless told otherwise, running jobs) |
| `pcb-shot`, `pcb-do` | Screenshot and action shells for a local agent's Bash (see `skills/computer-use/SKILL.md`) |

### The terminal UI

The UI uses the terminal's own colors and takes mouse clicks in terminals
that report them: measured in GNOME Terminal; Konsole, kitty and tmux (with
`mouse on`) use the same xterm mouse protocol. To select text while it runs,
hold Shift. Piped or run from a script,
`pcbridge` alone prints the help as before.

- **The bar at the top**, on every tab: the daemon, the desktop grant
  (`locked`, or `OPEN` with the time left and, while it slides, its
  ceiling) and one button that locks or unlocks it. `l` does the same;
  it locks at once but asks before it unlocks.
- **Overview**: daemon, grant, running jobs, remote tunnel, the tool
  profile, the config file and its warnings.
- **Settings**: sections on the left, their settings on the right, a
  filter box, and an editor with the right widget for each type (a switch,
  a list of choices, text; lists and tables as TOML). Changes are staged
  and written together by **Save**, after the same checks as `pcbridge
  set`. Changing `desktop.enabled`, `[auth]`, `host`, `port` or
  `public_url` asks first. **Restart daemon** appears when a saved change
  needs it. The password and the static token are never shown; they can
  be replaced, and the token generated.
- **Tools**: all 37 tools with their state (`active`, `not in profile`,
  `needs desktop`), a search box that filters by name and description as
  you type, a toggle for the offered ones only, and the selected tool's
  description, hints and parameters.
- **Connections**: a switch per client (see above), with **Update** for an
  entry that runs an older command.
- **Commands**: the table `pcbridge list` prints.

Keys: `1`-`5` switch tabs, `l` lock/unlock, `r` restart the daemon, `q`
quits (and asks if settings are unsaved).

The GNOME extension's panel icon shows the same state and has "Lock desktop
control now" in its menu. Ask an agent to hide it (`panel_icon`), or set it
yourself:

```bash
gsettings --schemadir ~/.local/share/gnome-shell/extensions/pcbridge-gorunur@eymistaken.local/schemas \
  set org.gnome.shell.extensions.pcbridge-gorunur indicator-mode when-granted   # or: always
```

Hidden, it still appears whenever desktop control is granted: the icon is
one of the signals that an agent has the desktop, so neither setting can
hide it then. (A package install keeps the schema under
`/usr/share/gnome-shell/extensions/`.) On KDE Plasma the grant shows as a notification
with a "Lock now" button, and `pcbridge-lock.desktop` can carry a shortcut
(System Settings > Shortcuts).

## On KDE Plasma

The tools and their arguments are the same; a few answers differ:

- `window_focus` raises windows through a KWin script (~90 ms) and falls
  back to KRunner (`alt+space`) instead of GNOME search.
- `screen_capture(monitor="window")` crops the focused window out of its
  monitor's frame, and needs the native helper, as all capture there does.
- `desktop_unlock` turns on Qt accessibility for the grant, so `ui_dump`
  reads Qt applications (Kate, Konsole, Dolphin, System Settings); an
  application that was already running appears within about 2 s.
- `system_capabilities` names the KWin backends (`linux.kwin.screenshot2`,
  `linux.kwin-script`, `linux.freedesktop-screen-saver`).
