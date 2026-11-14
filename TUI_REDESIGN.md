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
  searchable by name and description. **Decided (design, 2026-11-14):** no
  Commands tab. Every command is in the `Ctrl+P` palette and the actions
  sit on their tabs; see *The command palette* below.

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

## Reviewed in the design

The Tools and Connections tabs and the dialogs were gone through while
drawing the design (2026-11-14). The gaps found, each fixed in the design
below:

- **Tools:** the profile cannot be changed from the tab; there is no way
  to see only the hidden tools; the four hints (`TOOL_HINTS`) are not
  shown, so a reader cannot tell a read-only tool from a destructive one.
- **Connections:** a switch changes a client's file without showing what
  it will write; installed and missing clients are mixed in one list; after
  a change nothing says that the client must restart to pick it up.
- **Confirmation:** the unlock question has no duration choice and shows
  no key hints (Enter, Escape).
- **Shown-once secret:** "I saved it" can be pressed without a pause; no
  feedback says whether the copy reached the clipboard.
- **Login password:** fine as it is; it gains the key hints and the
  same frame as the other dialogs.
- **Remote password:** no live check of the length and the repeat; the
  offer to sign out the apps that hold tokens comes only after the
  password changed, as a second dialog.

## Proposed tab layout

Overview · Desktop · Agents & Jobs · Connections · Remote · Tools ·
Activity · Logs · Advanced

- Desktop: the grant, desktop control on/off, the panel icon, the native
  helper's state and capabilities.
- Advanced: today's Settings screen.

**Decided (design, 2026-11-14):** this layout, keys `1` to `9` in this
order. Settings becomes Advanced; the Commands tab goes away.

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
- **Decided (design, 2026-11-14):** colors. The terminal UI uses its own
  palette for accents and states, the same in every terminal; the
  background stays the terminal's (the green in the screenshots is the
  maintainer's desktop theme, not a wish). The palette is under *Palette*
  below.

## The design (2026-11-14)

Drawn in Claude Design and approved by the maintainer on 2026-11-14:
<https://claude.ai/artifact/9X6T57CYvurzuDEiaWc3j7> (private to the
maintainer). Every board there is interactive: tabs link to each other,
and switches, pickers, rows and buttons work, so the behavior below can
be checked by playing it. This section is the full specification; the
code is built from this text, and the boards are the picture of it.

The sample data on the boards (job ids, pids, prompts, warnings, client
states, audit lines) is illustrative. The real screen shows real data;
only the layout, the wording pattern and the behavior are specified.

### The grid

- Every screen is drawn for a 120 × 38 cell terminal and checked at 80
  columns. One cell is 1 column × 1 row; the boards use 13 px JetBrains
  Mono with 18 px rows, which is only how the picture was made.
- Layout is rows, columns, horizontal rules and one-line buttons. Sections
  are a bold muted title followed by a rule to the right edge
  (`SERVICE ─────────`), not boxed cards.
- Spacing: one empty row between sections, 2 columns of body padding left
  and right, 4 columns between side-by-side columns.

### The frame (on every tab)

1. **Row 1, the bar** (background `panel`): `pcbridge` bold, the version
   dim, `● daemon running` (ok) or `● daemon not running` (error); when
   the tunnel is open, `◉ remote open` (warning). Right-aligned: the grant
   and its button.
   - Locked: `◆ Desktop locked` and a primary button `Unlock 15 min ▾`.
     The `▾` opens a menu: `Unlock desktop control for` then 5 min,
     15 min (default), 30 min, 1 h, 2 h (max), the current one marked
     `●`. Picking one unlocks at once (a click acts at once).
   - Open: `◇ Desktop OPEN · 14:52 left` (warning, bold) and a danger
     button `Lock now l`.
   - The durations come from `[desktop] unlock_default_minutes` and
     `unlock_max_minutes`; the list stops at the maximum.
2. **Row 2, the tabs:** `1 Overview  2 Desktop  3 Agents & Jobs
   4 Connections  5 Remote  6 Tools  7 Activity  8 Logs  9 Advanced`. The
   number is dim, the active tab is bold with a 2-cell-thick accent
   underline. Keys `1`-`9` switch tabs.
3. **Last row, the footer** (background `panel`): this tab's keys, the key
   in accent bold and the label muted, then right-aligned `? Help` and
   `q Quit`. Every tab shows `^p Commands`.

At 80 columns the bar shortens (`pcbridge ● running … ◆ locked Unlock ▾`),
the tabs use short labels (`Agents`, `Clients`) and scroll, with a
`+4 ›` marker at the right edge for the tabs that do not fit.

### Palette

Our own colors for accents and states; the background is
`ansi_default`, so the terminal's own background shows through.

| Token | Hex | Used for |
|---|---|---|
| accent | `#45c4d6` | focus, keys in the footer, the active tab, links, selected segment |
| primary | `#1d6aa8` | the one main button on a screen (white text) |
| danger | `#8a2c2c` | Lock now, Stop, Revoke confirm (white text) |
| working | `#6aa5ff` | running jobs, the active state |
| ok | `#62d08a` | healthy, connected, done, a switch that is on |
| warning | `#e8bb4f` | grant open, tunnel open, restart due, doctor warnings |
| error | `#ff7a7a` | failed, refused, errors |
| text | `#d5dbe1` | values |
| muted | `#8d99a6` | labels, descriptions, section titles |
| dim | `#75818e` | hints, paths, numbers before tab names (4.6:1 on dark) |
| panel | `#151a20` | the bar and the footer |
| button | `#27313b` | a default button (hover `#34414e`) |
| selection | `#1e2a38` | the cursor row of a table or list (hover `#161c23`) |
| rule | `#2a333d` | section rules and borders |
| banner | `#3a3115` with `#f3d27c` text | the restart notice |

States never rely on hue alone; each has a glyph too: `● ✓` ok,
`◇ ▲ ◉` attention, `✕` error, `⠹` (a spinner) working, `◆ ○ ⊘ ·` off,
locked or cancelled.

Buttons are one line high, compact, with the key after the label in a
lighter accent (`Restart r`). Kinds: default, primary, danger, a link
style (accent text, no background, ends in `›`) and a quiet danger style
for a row action (`Revoke`, red text, no background).

### Controls, one per setting kind

`pcbridge/settings.py` already knows each setting's kind; the screen uses
it.

- **bool:** Textual's own `Switch`, as today: 3 rows high, a dark box with
  a 2-cell slider. On: the slider sits right, in ok green. Off: left, in
  `#c8434b` red (Textual's ANSI default for an off switch is `ansi_red`,
  which is where the red came from). Space toggles.
- **choice:** a segmented picker on one row: `[ claude | antigravity |
  codex ]`, the chosen segment in accent with dark bold text. `←` `→` move.
  `default_agent` becomes a `choice` of the configured agents.
- **int:** `− 90 s +`, the value right-aligned in an input-colored cell,
  with the unit; `+` `−` step, digits type. The allowed range is printed
  under it (`default 90 s · 10 s to 600 s`). The bounds come from the
  settings schema; the numbers drawn on the Advanced board are
  placeholders where the schema has none yet.
- **str:** a one-row input with an accent underline when focused.
- **secret:** `✓ set · hashed` and a `Replace…` button that opens the
  password dialog. A secret is never shown.

Each setting also shows when it applies, as a tag: `applies now` (green
tint), `restart daemon` (gray), `restart daemon + clients` (blue tint),
from `RESTART_NONE`, `RESTART_DAEMON` and `RESTART_CLIENTS`.

**The restart notice:** once a saved change needs a restart, a banner sits
at the top of the tab body: `▲ 2 saved changes apply after restarting the
daemon` (or `the daemon and the clients`), then `Restart now r` and the
dim note `stays until the restart`. There is no Later button: it stays
until a restart happened. It appears on every tab that saved such a change
(Desktop, Tools, Advanced).

### The logo

**Wide (96 columns and more): the wordmark only**, no terminal icon. It is
drawn with half blocks (`▀` `▄` `█`): one cell holds two pixels stacked,
the upper half in the cell's foreground color and the lower half in its
background color, so a pixel is close to square. The letters are a bold
pixel font, strokes two pixels wide:

```
p        c        b        r       i   d        g        e
......   ......   ##....   .....   ##  ....##   ......   ......
......   ......   ##....   .....   ..  ....##   ......   ......
#####.   .#####   #####.   ##.##   ##  .#####   .#####   .####.
##..##   ##....   ##..##   ####.   ##  ##..##   ##..##   ##..##
##..##   ##....   ##..##   ##...   ##  ##..##   ##..##   ######
##..##   ##....   ##..##   ##...   ##  ##..##   ##..##   ##....
#####.   .#####   #####.   ##...   ##  .#####   .#####   .#####
##....   ......   ......   .....   ..  ......   ....##   ......
##....   ......   ......   .....   ..  ......   #####.   ......
```

Rows 0-1 are the ascender, 2-6 the x-height, 7-8 the descender. Letters
are joined with one empty pixel column: 50 pixels wide, 9 high. With the
glow margin (3 pixels left, right and above, 2 below) the logo takes
**56 columns × 7 rows**, centered at the top of the Overview.

**Narrow (below 96 columns): the terminal icon only**, drawn the same way:

```
.#############.
###############
#.............#
#.##..........#
#..##.........#
#...##........#
#..##.........#
#.##...#####..#
#.............#
.#############.
```

A window with a thick title bar and a bold `❯_`. With a 3-pixel glow
margin on every side it takes **21 columns × 8 rows**. The text to its
right is the state line and, below it, `2.9.0 · below 96 columns the
wordmark gives way to the icon` then `click the icon for the reason`.

**Color.** The word is one piece, not split into "pc" and "bridge": a
soft horizontal gray gradient, light at the left to darker at the right,
with a slight darkening toward the bottom (mix 16 % toward
`rgb(70,78,88)` at the last pixel row). The state tints the gray and
adds a glow:

| State | Letters, left → right | Glow color | Ring alphas (distance 1, 2, 3) |
|---|---|---|---|
| idle | `rgb(214,219,225)` → `rgb(118,128,140)` | none | 0, 0, 0 |
| active | `rgb(221,231,245)` → `rgb(124,146,178)` | `rgb(70,138,235)` | 0.46, 0.24, 0.09 |
| error | `rgb(246,224,227)` → `rgb(182,128,136)` | `rgb(232,72,90)` | 0.44, 0.22, 0.08 |

The glow is cell backgrounds: every empty pixel within 3 pixels of a
letter pixel (Chebyshev distance) takes the glow color at the ring's
alpha. The icon uses the same gradient and glow.

- Idle: nothing running. Active: a tool call or a job is running. Error:
  the last call failed; it clears 60 s after the last error.
- Under the logo, one line: idle `idle · listening, nothing running`
  (muted), active `● agent_run · claude · 2m 14s` (working), error
  `✕ ui_click refused · 12 s ago` (error), then dim `· click the logo for
  the reason`.
- **A click shows the reason**: a bordered line below, 84 columns wide,
  with a head (`IDLE`, `ACTIVE`, `LAST ERROR`) in the state color, the
  text, and `Activity ›`. Examples: "2 jobs and 1 tool call: agent_run
  claude (7f3a, 2m 14s), agent_run codex (81c0, 41s), screen_capture from
  claude.ai now." / "ui_click from claude-code was refused at 02:14:07:
  USER_ACTIVE, the keyboard was used 3 s ago. Nothing was clicked."
  A second click hides it.
- Hover brightens the whole logo a little.

**Click reactions.** Each click also plays one reaction, picked at
random from five, never the same one twice in a row. **While one runs,
further clicks are ignored**, so a reaction always finishes. It runs at
about 30 frames per second only while it lasts; an idle logo uses no
timer. With progress `p` from 0 to 1 and `env = sin(π·p)`:

| Reaction | Length | What changes |
|---|---|---|
| pulse | 1.2 s | ring alphas × (1 + 1.3·env) plus (0.30, 0.16, 0.08)·env, so even idle glows briefly; letters mix 15 %·env toward white |
| sweep | 1.0 s | a light band crosses the letters left to right: center at `-6 + p·(width+12)` pixels, letters within 4 pixels mix up to 75 % toward white |
| flow | 1.6 s | the gradient travels: position `t` becomes `tri(t + 2p)`, where `tri` is a triangle wave of period 2, so it ends where it started |
| wave | 1.3 s | each pixel column moves up or down by `round(sin(x·0.45 − p·3π)·1.4·env)` pixels, a half cell at a time |
| sparkle | 1.4 s | empty pixels within the glow margin light up at random (about 12 %·env of them each 90 ms), in a light version of the state color |

### 1 · Overview

Under the logo, two columns (about 60 : 52), then two more.

- **SERVICE** (label column 12 wide, value, action):
  - Daemon: `● running · 2.9.0 · pid 48211 · up 3 d 4 h`, `Restart r`.
  - When the installed version differs from the running one, a second
    line `▲ 2.9.1 installed · loads when no job runs` and `Update u`.
  - Desktop: `◆ locked · desktop control enabled` with `Unlock ▾`, or
    `◇ OPEN · agents may use keyboard, pointer, screen` with `Lock now`.
  - Remote: `○ tunnel closed · 2 apps hold tokens`, `Remote ›`.
  - Keep awake, Tools (`full · 40 of 40 offered · remote: readonly, 13`),
    Clients (`3 of 8 connected · Claude Code, Codex, Claude Desktop`),
    Config (the path, dim).
- **HEALTH** (`pcbridge doctor · 40 s ago`): `✓ 46 ok  ▲ 4 warnings
  ✕ 0 failed` and `Run again`; a one-row bar of `█`, one cell per check,
  green then amber then red; then one selectable row per warning or
  failure (`▲ install  pcbridge.service differs from this version`).
  The selected one shows its explanation indented under a left rule and,
  where doctor has a fix, `Fix this check f` (primary) with the command
  dim beside it. Fixing runs that one check's fix in a worker, then
  checks again: the row turns `✓` and the counts update.
- **JOBS** (`2 running`): ID with spinner, AGENT, PROMPT, TIME; each row
  opens Agents & Jobs; then `Agents & Jobs ›`.
- **RECENT ACTIVITY:** the last three audit entries (TIME, CLIENT,
  ACTION); each row opens Activity.
- Footer: `l Lock/Unlock  r Restart  u Update  f Fix  ^p Commands
  1-9 Tabs  ? Help  q Quit`.

At 80 columns everything stacks in one column: the icon with the state
line beside it, SERVICE (shorter values), HEALTH with the first two
warnings and `+2 more · Show all warnings ›`, JOBS.

### 2 · Desktop

Two columns.

- **GRANT:** `◆ Locked. Agents cannot use the keyboard, the pointer or
  the screen.` or, open, `◇ Open · 14:52 left · at most 2:00:00
  (sliding)` with a one-row meter of `━` and `╌`. `For` and a segmented
  picker of the durations; `Unlock for 15 min l` (primary; disabled while
  desktop control is off) or `Lock now l` (danger) and `Restart the clock
  at 15 min`. Dim: `The l key asks before it unlocks; a click acts at
  once. Locking never asks.`
- **DESKTOP CONTROL:** switches with a label and, where useful, the key
  dim and one muted line: `Desktop control  desktop.enabled` ("Off by
  default. When off, no desktop tool works, grant or not."), `Notify me
  when it unlocks`, `Block GUI launches from shell_run`; number inputs
  `Lock after 90 s without agent input` (`unlock_idle_seconds`) and
  `Refuse input 60 s after you touch keys or mouse`
  (`idle_guard_seconds`).
- **PANEL ICON:** the switch `Show pcbridge in the top bar`, the line
  `GNOME extension pcbridge@eymistaken · enabled`, and a legend of the
  icon's four colors (locked, open, working, error). The setting's key
  could not be found in `config.example.toml` while drawing this; find it
  (or add it) before building.
- **POWER:** `Keep awake while jobs run` (`applies now`), with a line that
  says whether idle suspend is blocked right now.
- **NATIVE HELPER** (`✓ healthy`): Binary, Session, Monitors, then **OS
  PERMISSIONS** in two columns: capture, pointer, keyboard, accessibility,
  window, session, each `✓` or `▲` with its backend dim. From
  `system_capabilities`.
- Settings saved here that need a restart raise the restart notice.

### 3 · Agents & Jobs

- **JOBS** with a segmented filter `Running 2 | Recent | All`, a table of
  ID, AGENT, MODEL, PROMPT, STATE (`⠹ running` working, `✓ done` ok,
  `✕ failed` error, `⊘ cancelled` warning), TIME.
- Left below: **JOB 7f3a** with its state; Prompt, Folder, Started (`today
  02:11 · by claude.ai`), Session (dim); the output tail in a darker box
  that follows while the job runs; then `Cancel c` (danger, only while
  running), `Full output o`, `Copy session id`. Cancel asks inline:
  `Stop job 7f3a? Its output so far is kept.` `Stop the job` / `Keep
  running`.
- Right below: **DEFAULT AGENT** as a segmented picker of the configured
  agents and the muted line "Used by agent_run when a call names neither
  an agent nor a model of a known agent." Then **AGENTS**: one row per
  agent (`● claude  3 models · default`); the selected one shows the
  `Offered to agent_run` switch, its models and efforts as chips, its
  command dim, and `Every setting of claude in Advanced ›`.
- Footer: `↑↓ Job  c Cancel  o Output  d Default agent  ^p Commands`.

### 4 · Connections

- First line: `3 of 8 local clients use pcbridge through pcbridge stdio,
  a relay to the daemon. No network, no password.`
- Left: a table (switch, CLIENT, STATE, CONFIG FILE) in two groups under
  thin rules, `on this computer` and `not found`. States: `● connected`
  (ok), `▲ old command` (warning), `○ off` (dim), `not installed` (dim, the
  switch disabled). Dim at the bottom: "Every file is backed up before it
  changes. A running client picks the change up when it restarts."
- Right, the selected client: State, Runs (the command it starts), File,
  Backup; after a change, the badge `▲ restart Codex to pick this up`;
  **what a change would write**, as a diff in a dark box (`+` lines green,
  `-` lines red), from `connect --dry-run`; then `Update the entry u`
  (primary, only for an old command), `Connect` / `Disconnect space`,
  `Open the file`; for a missing client `Install Hermes, then press Look
  again g`.

### 5 · Remote

Four numbered sections, in the order the brief set:

1. **TUNNEL:** the switch, `◉ Open` (warning) or `○ Closed` (muted) and a
   muted detail (`Tailscale Funnel · since 01:40 · reachable from the
   internet` / `Remote apps cannot reach pcbridge. Local clients are not
   affected.`); dim: "The tunnel never starts at login: the service listens
   on 127.0.0.1, the tunnel is what exposes this machine."
2. **ADDRESS:** the public URL in an input-colored cell, `Copy y`, and
   `✓ copied with wl-copy` after; the muted line "In claude.ai: Settings ›
   Connectors › Add custom connector, paste the address, then type the
   remote password on the approval page. ChatGPT and Gemini work the same
   way."
3. **ACCESS:** Password `✓ set · stored as a hash · changed 2026-10-17`
   and `Replace… p`; Remote tools as a segmented picker `same | full |
   core | desktop | readonly` with a line per choice (core: 24 tools,
   desktop: 23, readonly: 13, measured 2026-11-14) ending "Applies to the
   static token too."
4. **CONNECTED APPS** (`3 apps hold tokens`): NAME, LIVE, REGISTERED (with
   `(15 stale)` in warning), LAST SEEN, TOKEN ENDS, and a quiet `Revoke`
   that asks inline (`sign it out?` `Revoke` / `Keep`). Below:
   `Clean up 15 stale registrations` with the dim rule "no token, unused
   for 30 days; live sessions stay", and right-aligned `Revoke every
   app…`.

Footer: `t Tunnel on/off  y Copy address  p Password  x Revoke`.

### 6 · Tools

- `Search` input (`/` focuses it), then `Profile` as a segmented picker
  `full | core | desktop | readonly` with `local · remote: readonly`, and
  at the right `All 40 | Offered N | Hidden N`. Changing the profile saves
  it and raises the restart notice (daemon + clients).
- Left: the list (✓ offered or `·` hidden, TOOL bold or dim, GROUP short,
  TITLE muted), scrolling; under it `N shown · N of 40 offered locally ·
  name matches first`.
- Right: the selected tool's name as the section title, its title and
  group, the four hints as tags (`read-only` green tint, `destructive` red
  tint, `idempotent` gray, `open world` blue tint), whether the profile
  offers it ("hidden by the core profile; a call by name is refused and
  audited"), the docstring, then **PARAMETERS** (name, type and required,
  description).

### 7 · Activity

- `Kind` segmented `All | Desktop | Shell | File | Agent | Remote access`
  and `Client` segmented (all and the clients seen on this page).
- Dim: `Newest first, read from the end of audit.log · 20,351 entries
  since 2026-08-01 · 3.0 of 5 MB before it rotates`.
- Table grouped by day (`TODAY`, `YESTERDAY`, then dates): TIME, CLIENT,
  KIND (each kind its own tint: desktop purple `#c79bff`, shell blue,
  file amber, agent green, remote pink `#ff9a9a`), EVENT (the audit
  `event`), WHAT (a short, content-free summary from the entry's fields),
  RESULT (`ok`, `exit 0`, `refused`, `failed`, `stopped`).
- **ENTRY**: the selected line as written, the JSON wrapped, with the dim
  note "as written, one JSON line · contents are never recorded"; then
  `‹ Newer`, `page 1 · 22 of 20,351`, `Older ›`, `Copy line`.
- Footer: `k Kind  c Client  pgup/pgdn Newer/Older  y Copy`.

### 8 · Logs

- `● Following f` (ok tint) or `❚❚ Paused` (warning tint), `Level`
  segmented `all | info+ | warn+ | errors`, a `Find` input that highlights
  matches, and at the right `N lines shown`.
- The journal in a dark box, newest at the bottom: timestamp dim, level
  (DEBUG dim, INFO muted, WARN warning, ERROR error), message.
- While paused, new lines collect behind a primary button floating near
  the bottom right: `↓ 12 new lines · follow again`.
- Under it: `journalctl --user -u pcbridge.service · this boot`, `Copy
  what is shown`, `Older ›`.

### 9 · Advanced

Today's Settings screen, kept for every key, with the typed controls.

- `Filter` input (key or description) and a toggle `Changed from default
  only`; the config path dim at the right.
- Left: the sections (General, Server, Authentication, Paths, Limits, File
  links, Power, Native helper, Desktop control, Tools, one per
  `agents.NAME`), a `●` beside a section with unsaved changes. A filter
  searches every section and prefixes each key with its section.
- Right: one block per key: a `●` (unsaved) or `◦` (differs from the
  default) in warning, the key bold, the restart tag, the control at the
  right, the description muted, the default and range dim.
- Bottom bar above a rule: `● 2 unsaved changes · checked with the real
  loader, backed up before writing` or "No unsaved changes. ◦ marks a
  value that differs from the default.", then `Discard` and `Save ^s`.
  Saving raises the restart notice when needed.
- Footer: `/ Filter  ↑↓ Section  space Toggle  +- Number  ^s Save
  ^z Reset key`.

### The command palette (`Ctrl+P`)

Textual's command palette, 84 columns wide, over the dimmed screen. A
search line `❯ Search commands and tabs` with `N of 28` at the right,
then four groups, each row with a glyph, the name bold, a muted
description and the key at the right:

- **RUN NOW** (read-only, shows the output; glyph `▶`): status, doctor,
  clients, keep-awake, lock; tools, logs -f and remote status open their
  tab instead (glyph `›`).
- **ASKS FIRST** (changes state; glyph `?`): restart, update, stop,
  unlock, remote start, remote revoke --all, connect CLIENT, report.
  Choosing one replaces the list with the question, its consequence
  ("Jobs 7f3a (claude) and 81c0 (codex) are stopped; their output so far
  is kept…") and `Cancel esc` / the action `enter` (danger for stop and
  revoke).
- **OPENS A DIALOG** (glyph `…`): set auth.password, setup, uninstall.
- **GO TO:** the nine tabs.

`serve`, `stdio`, `ui` and `list` are not offered. **lock is in Run now,
not Asks first**: the Commands bullet above lists it as asking, but the
safety rule says locking never asks, and the rule wins.

**The output view:** `$ pcbridge doctor` and `✓ exit 0 · 1.8 s` at the
top, the output in a dark box with the command's own colors (ok lines
green, warnings amber, fix lines dim), then `‹ Commands esc`, `Copy
output y`, `Run again enter`. It runs in a worker.

### Dialogs

All four share one frame: 66 columns wide, a rounded border (warning tint
for a question that unlocks, danger tint for one that destroys), a white
bold title, muted text, buttons right-aligned with their keys.

- **Confirm** (from the `l` key, unlocking asks): "Unlock desktop control?",
  `For` with the duration picker, the consequence ("It locks after 90 s
  without agent input, at the latest in 2 h. l or Lock now closes it
  again."), `Cancel esc` and `Unlock for 15 min enter`.
- **Shown once:** "The new remote password", "Type it on the approval page
  when a remote app connects.", the secret large and bold in an accent
  frame, `Copy y` with `✓ Copied with wl-copy` (or the OSC 52 note), the
  dim "This is the only time it is shown: only its hash is stored, so it
  cannot be read back, only replaced.", a checkbox `[ ] I saved it in a
  password manager`, and `Done enter`, disabled until the box is ticked.
  No Escape, no timer.
- **Login password:** "First, your login password", "Whoever sets the
  remote password gets remote access, so it must be you: the login
  password of USER.", a labeled password input, the error line
  (`wrong_login()`), `Cancel esc` / `Continue enter`. Checked through PAM
  in a worker, as today.
- **Remote password:** "Replace the remote password", `Generate a
  passphrase, six words g` (primary), a rule `or type your own`, `New`
  and `Again` inputs with live checks at the right (`9 of 12` warning or
  `✓ 14 chars`; `✓ same` or `✕ differs`), the checkbox `[x] Also sign out
  the 3 apps that hold tokens; a new password does not end their
  sessions` (ticked by default, replacing the second dialog), `Show`/
  `Hide`, `Cancel esc`, `Set the password enter` (disabled until both
  checks pass).

### Building it in Textual

- The tabs are a `TabbedContent` as today; the footer is Textual's
  `Footer`; the palette is Textual's command palette
  (`ENABLE_COMMAND_PALETTE = True` with a provider for pcbridge's
  commands).
- The logo is one widget with a `glyph` (word or icon) and a `state`. It
  renders a `Strip` per row: for each cell, `▀` with the upper pixel as
  foreground and the lower as background (a space with only a background
  when both halves are the same). The pixel colors are computed as in the
  table above. A `set_interval(1/30)` runs only during a reaction.
  `on_resize` picks the word or the icon at 96 columns.
- The glow's alpha needs a background to blend against, and the screen's
  background is `ansi_default`, which Textual cannot read. Blend against a
  fixed dark base (`#0e1216`) and accept slightly visible cells on a
  light or colored terminal background, or blend against the terminal's
  background when it can be queried (OSC 11). Decide by trying both on
  the maintainer's green theme.
- Switches are Textual's `Switch` with our colors:
  `Switch.-on .switch--slider { color: #62d08a }`, the off slider
  `#c8434b`, the box `#1b2027`.
- Slow work stays in workers: status, doctor (and each fix), restart,
  update, lock, unlock, the clients' states, the audit pages, the
  journal.

## Done when

Every point above marked Maintainer or Proposed is either fixed or
explicitly dropped with the reason, each screen matches *The design*
section (and the boards linked there), and each screen is checked at a
normal and a narrow terminal width.
