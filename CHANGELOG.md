# Changelog

## Unreleased

### Added

- **One install script that finds everything itself.** `install.sh` is now
  the recommended way to install, and from the next release on it is a
  release asset: `curl -fsSL .../releases/latest/download/install.sh | sh`.
  The install docs also give the path that downloads it, checks it against
  the release's `sha256sums.txt` and runs it. Run from a release, the
  script refuses root, downloads the wheel, checks its sha256 (and that of
  `constraints.txt`, which the release now ships), and installs it into
  `~/.local/share/pcbridge/venv`. It then hands over to `pcbridge setup`.
  `--wheel FILE` installs a wheel from disk instead, `--version` picks a
  release, and `--uninstall` removes pcbridge and its venv. From a git
  checkout it keeps doing what it did before. The detection lives in
  Python, in one place. It finds the CPU architecture, the distribution and
  its family (so Pardus counts as Debian), the package manager, the
  session type, and the desktop with its version. Over SSH it reads the
  user's graphical session through `loginctl`. `pcbridge setup` now starts
  with a plan: a "Found:" line, what will work and what will not, the
  system packages, and the tool profile. A single confirmation covers the
  whole plan (`--yes` skips it), and every sudo command is printed before
  it runs. System packages are installed only for the capabilities that
  will work, so an unsupported desktop gets no clipboard, AT-SPI or OCR
  packages. On such a desktop `[tools] profile` becomes `core`, unless the
  user chose a profile; setup only changes a value it set itself. The run
  ends with a summary of what works, what does not and why. Running it
  again updates and repairs. The packaged native helper is skipped when
  the system's glibc is older than the newest glibc symbol the binary
  links against (2.39 for the release helper), and the Python paths are
  used instead. When `~/.local/bin` is not on `PATH`, setup says so.
  Measured from a clean VM with one command: Arch Linux with Plasma 6.7.5
  installed three packages and kept the `full` profile; Debian 12 with
  Cinnamon on X11 got the `core` profile, a uv-managed Python and only the
  packages its tools use.
- **Python: offer the fix, do not just fail.** With a system Python older
  than 3.12, the install script offers three choices. The default, which
  `--yes` also picks, is a Python for pcbridge only, downloaded with a
  pinned, checksummed uv into `~/.local/share/pcbridge/`, with no root
  needed. The second is the distribution's own command: the deadsnakes PPA
  on Ubuntu 22.04 and its derivatives, `python3.13` on Fedora; Debian 12
  and Pardus 23 have no official package, and the script says so. The
  third is to quit. The script never repoints the system `python3`.
- **Optional components as a checklist.** Where desktop control is
  supported, setup offers optional parts in one list. The first is OCR for
  `find_text` / `wait_for_text`: tesseract with English and the locale's
  language, so `LANG=tr_TR.UTF-8` ticks Turkish. The package names for
  Debian, Arch and Fedora are in `distro.py`. With a non-English language,
  `[desktop] ocr_languages` becomes `eng+tur` if it was still the default.
  `pcbridge doctor --fix` offers missing packages and components again.
  It asks on a terminal and prints the command otherwise. Where desktop
  control is supported and `evdev` is missing, setup also installs the
  desktop extras. `evdev` has no wheels and is built from source, so the
  plan includes the compiler packages it needs.

- **List and revoke remote clients.** Until now there was no way to see
  which remote apps held tokens or to cut one off, short of deleting the
  OAuth database. `pcbridge remote clients` lists the apps that connected
  through the tunnel by name (ChatGPT, Gemini and so on), how many of their
  registrations still hold a token and when the tokens expire. `--all`
  adds names whose tokens have all expired, `--ids` shows each
  registration, and `--json` is for scripts. `pcbridge remote revoke NAME`
  deletes every registration of that app together with its access tokens,
  refresh tokens and pending authorizations, and `--all` does the same for
  every app. The next call from a revoked app fails with 401, its refresh
  token stops working, and it has to connect again with the password. No
  restart is needed, and it works while the daemon is stopped. The terminal
  UI has a new **Remote** tab with a Revoke button per app and Revoke all;
  Commands moved from key `5` to `6`. A new password does not end the
  sessions remote apps already have, because refresh tokens do not depend
  on it (measured). So `pcbridge set auth.password` now offers to revoke
  every remote client, or does it at once with `--revoke-remote`, and
  saving a password in the terminal UI asks the same question. Revocations
  are written to the audit log, never with a token.
- **A separate tool profile for remote clients.** `[tools] profile` was
  global, so a phone connected through Funnel saw `shell_run` and the
  desktop tools exactly like a local Claude Code. The new
  `[tools] remote_profile` sets the profile for every client that comes
  over HTTP, the static token included, while local clients on the socket
  keep `profile`. The default, `same`, follows `profile`, so nothing
  changes until it is set. A tool outside the remote profile is left out
  of `tools/list`, and a call to it by name is refused with a plain error
  and an audit record. Hiding alone was measured not to be enough. A new
  `readonly` profile, valid for both settings, offers only the 12 tools
  that change nothing: job status and output, file reads, tmux capture,
  system status, `system_capabilities` and `screen_info`. `pcbridge tools`
  shows what remote clients get when the two profiles differ.
- **Keep the machine awake while jobs run.** With `[power] keep_awake`
  on, the daemon holds a sleep inhibitor while at least one job is running
  and releases it when the last job ends. This stops idle suspend from
  freezing a long `agent_run` or `shell_run_background` job and cutting off
  the remote endpoint. The setting is off by default. The terminal UI
  settings, `pcbridge keep-awake on|off` and `pcbridge set` change it, and
  the running daemon applies the change within seconds without a restart.
  The inhibitor is the one each desktop honors: a gnome-session suspend
  inhibitor on GNOME, PowerDevil through `kde-inhibit` on Plasma, and a
  logind idle inhibitor on Hyprland and other sessions. The screen still
  locks on GNOME and Plasma, and a suspend chosen on purpose still works.
  On Hyprland, hypridle skips its lock rule while the inhibitor is held,
  unless that rule sets `ignore_inhibit`. The inhibitor ends with the
  daemon, even when the daemon is killed. `pcbridge status`, `pcbridge
  doctor` and the Overview show what is held and why. They also show when
  logind refuses the inhibitor, which happens when polkit is missing. This
  was measured in the Arch VMs on GNOME 50, Plasma 6.7.5 and Hyprland
  0.56.2 with real suspends. It was not tested on physical Plasma or
  Hyprland hardware.

### Changed

- **`fs_read` reads in pages, from the beginning.** It used to return only
  the last `max_chars` characters, so the start and middle of a large file
  could be reached only through `shell_run`. A page now starts at the
  beginning, at `start_line` (negative counts from the end, so
  `start_line=-200` is the last 200 lines of a log) or at the `cursor` the
  previous page returned. It ends on a line boundary, unless a single line
  is longer than the page, and says which lines it covers out of how many,
  the file's size and SHA-256, and either "End of file." or the cursor for
  the next page. A cursor is refused when the file changed in between, so
  pages never skip or repeat content. `max_lines` stops a page early,
  `line_numbers` prefixes each line with its number, and `paths` reads up
  to 20 files in one call with `max_chars` split evenly between them. The
  Markdown fence around a page grows longer than any run of backticks in
  it. Measured over HTTP with OAuth against a throwaway daemon: a
  163,000-character file with long multibyte lines came back exactly, in
  order, from the cursors of 60,000-character pages.
- **The remote password and the tokens are stored only as hashes.** A
  leaked `config.toml`, backup or `oauth.db` no longer gives anything away.
  `[auth] password` is stored as an scrypt hash and `[auth] static_token` as
  SHA-256. Every access token, refresh token and authorization code in
  `oauth.db` is keyed by its SHA-256 digest. When the daemon starts it
  hashes a plaintext password or token in the config file, and converts an
  older database in place, then vacuums it. Apps that are already connected
  keep working. `pcbridge set` and the terminal UI write only hashes, and
  config backups get the same hashes. `pcbridge set auth.static_token
  --generate` and the Settings tab show a new token once, since it can no
  longer be read back. `pcbridge doctor` reports a file that still holds a
  plaintext secret (`--fix` hashes it) and names older copies that do, such
  as backups made before this change. PCBRIDGE_PASSWORD and
  PCBRIDGE_STATIC_TOKEN in the environment are used as they are and never
  written. To run `tests/test_e2e.py` against the installed daemon, take the
  password and token from a password manager: they cannot be read from the
  file any more.
- **No password until remote access is wanted.** Setup no longer generates
  a password or a static token that nobody saw: local clients on the socket
  use neither. Without a password, remote access is off. The consent page
  refuses every app and says how to turn remote access on, and `pcbridge
  remote start` does not open the tunnel. `pcbridge doctor` and the Remote
  tab say so too. An existing password keeps working.
- **Choose or generate the remote password, shown once.** The first
  `pcbridge remote start`, the Remote tab's **Turn on remote access**, the
  last step of `pcbridge setup` and `pcbridge set auth.password` all ask
  the same question. Press Enter for a generated passphrase, six words from
  the EFF Large Wordlist (about 77 bits, bundled under CC BY 3.0 US), or
  type your own twice. A generated one is shown once, next to the remote
  address, and stays until the user confirms it is saved; it can be copied
  to the clipboard. `pcbridge set auth.password --generate` makes a
  passphrase as well. The consent page reads the password from the file
  when it changes, so a new password works at once, without a daemon
  restart.
- **Creating or replacing the remote password needs the login password.**
  Every path asks for the user's own login password first and checks it
  through PAM's `login` service, not sudo: the Remote tab, `pcbridge remote
  start`, setup, and `pcbridge set auth.password` with `--generate`. A user
  without sudo rights can manage their own remote access, and neither
  `NOPASSWD` nor sudo's cached ticket skips the question. Without a
  terminal, the login password is the first line of stdin, and with
  `--stdin` the new password is the second. The Settings tab sends
  `auth.password` to the Remote tab, so that check has no way around it.

### Fixed

- **The consent page's refusals answered 500.** The page for an expired or
  unknown request, and the lockout after too many wrong passwords, were
  built from a template whose CSS braces broke `str.format`. They came back
  as an empty 500 response. The lockout still held, but nobody could read
  why. They now show their message, with 400, 403 or 429.
- **The Remote tab said "the config does not load" until Refresh.** It
  mounted and read the remote clients before the terminal UI had loaded
  the config. It now reads again whenever the config is loaded.

- **`fs_read` refuses binary files.** It used to decode with
  `errors="replace"`, so a PDF, an image or an executable came back as
  corrupted text full of replacement characters, with no error. A file that
  contains NUL bytes, starts with a known binary signature (PDF, PNG, JPEG,
  GIF, WebP, ZIP, gzip, ELF, SQLite) or is not valid UTF-8 is now refused
  with a plain message that names the format and the size. The size in the
  reply now reads "bytes" instead of the Turkish "bayt". Binary transfer
  itself is still open in the roadmap.
- **Setup and doctor over SSH and on unsupported desktops.** Setup used to
  read the desktop from its own environment, so over SSH it saw no desktop
  and skipped the GNOME extension and the Plasma entries. It now uses the
  detected session. `pcbridge doctor` no longer suggests clipboard, AT-SPI
  or OCR packages on a desktop where desktop control cannot work; it says
  why in one line. It also no longer runs `plasmashell --version`, which
  crashes without a display; Plasma's version comes from its session file.
  The readiness checks of setup and doctor expect the tool count of the
  configured profile instead of a fixed 37, so a `core` install no longer
  fails them.
- **English truncation marker.** Trimmed tool output (`fs_read`, job logs,
  shell output and the other tools that keep the tail) now starts with
  `…(truncated)…` instead of the Turkish `…(kirpildi)…`.

### API

- **Tools**: unchanged, 37. `fs_read` refuses binary and non-UTF-8 files.
  `fs_read` reads from the beginning instead of the end and gains
  `start_line`, `cursor`, `max_lines`, `line_numbers` and `paths`; `path`
  is optional when `paths` is given.
- **Config file**: new `[power] keep_awake` (default `false`); no
  migration, a file without it keeps the old behavior.
- **CLI**: new `pcbridge keep-awake [on|off]`. `status.json` gains a
  `keep_awake` object, and `pcbridge status --json` gains the same field.

## 2.5.0 - 2026-09-30

### Added

- **Hyprland 0.56.2 on Wayland.** Desktop tools now use a selected
  compositor session for runtime bindings, monitors, windows, native input,
  accessibility and image-copy screenshots. Relative pointer input under
  Wayland pointer lock and touch through the native frame were checked in
  the VM. The white grant frame requires fresh presentation on current
  outputs. Missing lock, idle, or frame evidence refuses desktop actions.
  Two-output Arch VM acceptance passed; physical Hyprland hardware remains
  untested.
- **Hyprland capture and input across changing layouts.** Normal MCP shots
  and coordinates were checked with fractional rotation, negative origins,
  vertical placement, swapped identical outputs, simulated output removal,
  and mirroring. Old shots from changed outputs are refused. A fresh source
  shot works while a VM output mirrors it; physical connectors are untested.

### Fixed

- **Desktop batch focus safety.** Keyboard actions now recheck focus after
  waits and shared rate limits, so a batch stops when another window takes
  focus before the next key. Canceled sequences release held input before
  another writer takes the execution lock.
- **Clipboard restoration.** Native paste no longer overwrites a different
  clipboard value that another application set during typing. The check is
  content-based; identical replacements and changes after the re-read can
  still race restoration.
- **Accessibility and reporting.** GTK button roles now match between Python
  and native AT-SPI readers. Hyprland reports compositor context and grant
  closure without suggesting GNOME-only panel or sharing controls.

### API

- **Tools**: unchanged, 37.
- **Config file**: unchanged.

## 2.4.2 - 2026-09-27

### Fixed

- **Pi connection with pi-mcp-adapter 3.x.** Pi's adapter now reads
  `mcp-adapter.json`, while pcbridge still wrote `mcp.json`, leaving the
  connection unavailable and causing Pi's legacy-config warning. Connections
  now uses the installed adapter's config path, migrates only pcbridge's old
  entry when needed, and keeps support for adapter 2.x.

### API

- **Tools**: unchanged, 37.
- **Config file**: unchanged.

## 2.4.1 - 2026-09-24

### Added

- **Control the GNOME panel icon from the terminal UI.** Settings now has a
  Panel icon section with choices to show it all the time or hide it while
  desktop control is closed. The choice takes effect immediately and persists
  across logins; it uses the extension's existing setting, so it stays in sync
  with the MCP tool and adds nothing to `config.toml`. An open grant still shows the
  icon in either mode. On KDE Plasma, Settings explains that there is no
  pcbridge panel icon to change.

### Fixed

- **oh-my-pi, tried on a real installation (18.3.0).** Connecting,
  switching off and switching on work as released. What did not: oh-my-pi
  can also start the pcbridge entry of Claude Code's, Codex's or OpenCode's
  config once that source is switched on in its settings, and then
  `pcbridge clients` said "not connected" and `pcbridge disconnect
  oh-my-pi` changed nothing. It now shows oh-my-pi as connected through
  that client and switches it off with oh-my-pi's `disabledServers` list.
  Its `enabledServers` list, which overrides `enabled: false`, is read and
  cleared on disconnect, and `PI_CODING_AGENT_DIR` / `PI_CONFIG_DIR` move
  the file as they move oh-my-pi's own directory.

### API

- **Tools**: unchanged, 37.
- **Config file**: unchanged; the icon choice belongs to the GNOME extension.

## 2.4.0 - 2026-09-24

### Added

- **Connect and disconnect every local MCP client.** pcbridge can register
  itself with eight agents and apps on the machine, and take itself out
  again: Claude Code, Codex and Claude Desktop as before, and now
  Antigravity CLI, Hermes Agent, OpenCode, Pi (through the
  `pi-mcp-adapter` extension) and oh-my-pi. `pcbridge clients` lists each
  with its state (connected, outdated, switched off, not connected, not
  installed); `pcbridge connect NAME...` and `pcbridge disconnect NAME...`
  switch them; the terminal UI has a **Connections** tab with a switch per
  client and **Update** for an entry that runs an older command.
- Where a client has its own command for this (`claude mcp`, `agy mcp`,
  `hermes mcp`), it is used; otherwise only pcbridge's entry in the
  client's config file is edited. Every file a change touches is backed up
  first, with a `ROLLBACK.md`.
- Disconnecting switches the entry off where the client supports that
  (Codex, Antigravity, OpenCode, Pi, oh-my-pi), so settings kept with it,
  such as Codex's per-tool approvals, survive; Claude Code, Claude Desktop
  and Hermes have no such switch, and there the entry is removed.

### Changed

- `pcbridge setup` and a bare `pcbridge connect` still register only
  Claude Code, Codex and Claude Desktop; the other clients are connected
  on request. `pcbridge doctor` now reports them too, and warns about an
  enabled entry that runs an old command. `pcbridge uninstall` removes
  pcbridge from all eight.
- PyYAML is a declared dependency (it was a transitive one), to read
  Hermes Agent's `config.yaml`.

### Fixed

- `pcbridge ... | head` no longer prints a BrokenPipeError traceback; the
  command ends quietly with exit 141.

### API

- **Tools**: unchanged, 37.
- **Command line**: `clients` and `disconnect` are new; `connect` takes
  client names (`--client` still works) and knows the five new clients.
- **Config file**: unchanged.

## 2.3.0 - 2026-09-24

### Added

- **A terminal UI.** `pcbridge` alone in a terminal (or `pcbridge ui`)
  opens it, in the terminal's own colors and with mouse support. A bar on
  every tab shows the daemon and the desktop grant with its countdown, and
  one button (or `l`) locks or unlocks it. Settings edits every setting
  with the widget its type needs; Tools lists all 37 tools, marks what the
  profile offers and searches them by name or description as you type.
  Piped or scripted, `pcbridge` alone still prints the help.
- **Every setting from the command line**: `pcbridge settings`, `get`,
  `set`, `reset`. A save is checked with the daemon's own loader, backs up
  the old file and writes atomically; comments and untouched lines stay.
  The password and the static token are never printed and never taken
  from argv (`set` prompts, reads `--stdin` or `--generate`s them).
- **`pcbridge tools`** lists and searches the MCP tools, and shows one
  tool's parameters. **`pcbridge restart`** restarts the daemon to apply
  settings, only when no job is running and the grant is closed.
  **`pcbridge list`** prints every command by group.

### Changed

- New dependencies, both pure Python and inside the shipped venv: Textual
  (the UI) and tomlkit (editing TOML without losing comments).

### API

- **Tools**: unchanged, 37 (`core` offers 21, `desktop` 23).
- **Command line**: `list`, `settings`, `get`, `set`, `reset`, `tools`,
  `restart` and `ui` are new; `pcbridge` alone in a terminal opens the UI,
  and without a terminal it prints the help as before.
- **Config file**: no new setting; the format and `config_version = 2`
  are unchanged.

## 2.2.0 - 2026-09-23

### Added

- **`panel_icon`: hide the GNOME panel icon on request.** An agent can
  show or hide pcbridge's icon in the top bar when the user asks
  (`show`, `hide`, `status`). Hidden means hidden while desktop control is
  closed: the extension shows the icon for as long as a grant is open,
  whatever the setting, so hiding it cannot hide an agent's access. The
  setting is the extension's new `indicator-mode` key (`always`, the
  default, or `when-granted`), applies at once, and persists across logins.
  On KDE Plasma there is no panel icon, and the tool says so.

### API

- **Tools**: 37 (`panel_icon` is new; `core` offers 21, `desktop` 23).
- **GNOME extension**: the setting `indicator-mode` is new; the D-Bus
  interface is unchanged. An older extension ignores the setting, and the
  tool says it applies after the next login.

## 2.1.0 - 2026-09-23

pcbridge 2.1 runs on KDE Plasma 6 as well as GNOME, and on Arch Linux as
well as the Debian family. Everything was tested end to end in an Arch VM
with Plasma 6.7.5 and in headless GNOME 50.5 and 46.0 shells.

### Added

- **KDE Plasma 6 on Wayland.** The desktop tools work on Plasma with the
  same names, arguments and safety gate:
  - screen lock from `org.freedesktop.ScreenSaver`, which KWin owns;
  - idle time from `pcbridge-native idle-watch`, a Wayland
    `ext_idle_notifier_v1` watcher the daemon runs, because KWin keeps idle
    time off D-Bus;
  - the monitor table from `kscreen-doctor -j`, through the same resolver as
    Mutter's, pinned by a shared fixture in Python and Rust;
  - silent screenshots from KWin's `ScreenShot2` (7-12 ms a frame), granted
    by a `.desktop` entry to the native helper alone, and
    `monitor="window"` as a crop of the focused window;
  - windows raised and named by a one-shot KWin script (~90 ms), with
    KRunner as the search fallback;
  - Qt accessibility switched on for the grant, so `ui_dump` reads Kate,
    Konsole, Dolphin and System Settings, and restored when it ends;
  - the grant shown as a lasting notification with a "Lock now" button that
    runs the kill switch, and a `pcbridge-lock.desktop` entry a shortcut can
    be bound to.
- **Arch Linux package.** `packaging/arch/PKGBUILD` builds pcbridge from a
  checkout with the `.deb`'s layout (shared `packaging/stage.sh`), native
  helper included; CI builds, installs and tests it, and releases attach it.
- **Package hints per distribution.** `setup`, `doctor` and error messages
  name `pacman` packages on Arch and `apt` packages on the Debian family.
- **GNOME 50.** The extension declares 46 and 50, both verified.
- **Desktop detection.** `system_capabilities` reports the environment and
  the Plasma version next to GNOME Shell's; setup and doctor act per
  desktop.
- **CI**: an Arch package job, and a headless KWin smoke test.
- **`scripts/dev/arch-vm.sh`**: the Arch test VM (QEMU/KVM, cloud image,
  Plasma and GNOME) that this release was tested in.

### Fixed

- **A checkout named `~/pcbridge` stopped the daemon from starting** ("cannot
  import name '__version__'"): the service runs in the home directory, and
  Python put the checkout ahead of the installed package. The unit, the
  relay and the kill switch now run Python with `-P`.
- **`pcbridge setup` could start the old daemon** after moving leftover user
  units aside, because systemd was not reloaded; the `.deb` was affected too.
- **The native capture crashed with PipeWire 1.6** (SIGSEGV after the first
  frame): the stream disconnected itself inside its own callback. It now
  detaches outside any callback.
- **The extension's frame recursed without end with animations off** (a
  headless shell, or Reduce Animation), because `ease()` then completes at
  once. The frame now stands still instead. GNOME 46 was affected too.
- **`scripts/build-native.sh` did not find libclang on Arch.**

### Changed

- An unknown desktop is treated as GNOME: every check fails closed with
  GNOME's messages, as before.
- `ROADMAP.md` keeps only open, optional work.

### API

- **Tools**: the same 36 tools and schemas. `system_capabilities` adds
  `platform.environment` (`gnome`, `kde` or null) and `platform.plasma`, and
  names the KWin backends (`linux.kwin.screenshot2`, `linux.kwin-script`,
  `linux.freedesktop-screen-saver`).
- **Configuration**: no new keys.
- **Native helper protocol**: 1.0, additive only. `display_id` may carry the
  `kwin:` scheme; an id whose scheme is not the running compositor's is
  refused. `capture.session_open` may answer `not_needed`. The binary gains
  an `idle-watch` mode outside the protocol.
- **Install kind**: a package install is `deb` or `pacman`; existing stamps
  keep `deb`.

## 2.0.0 - 2026-09-23

pcbridge 2.0 turns a personal setup into an installable product: one
resident daemon that every client reaches without manual steps, English
messages throughout, any monitor layout, and packaging with CI.

### Fixed

- **Monitor tables under Mutter's physical layout mode.** GNOME uses the
  physical layout mode unless fractional scaling is enabled; positions and
  sizes are then framebuffer pixels. The resolver divided them by the scale
  regardless, so a 4K monitor at scale 2 was reported as 1920x1080 next to a
  neighbor at x=3840, with a phantom gap between them, a wrong pointer axis
  and misplaced clicks. Python and Rust now read `layout-mode`.
- **The xrandr fallback** did not normalize the canvas origin like the
  Mutter path, so the two paths described different canvases.
- **Configuration errors and an unwritable state directory** ended in a
  Python traceback, and the service restarted every second until systemd's
  start limit. They now print one line saying what to fix and exit with
  code 78, which the unit does not restart.
- **A job could start without a record.** The process started before its
  record was written, so a full disk left a job that no tool could list or
  stop. The record is now proven writable first, and a failed write stops the
  process.
- **A crashed native helper stayed in the process registry** until the next
  call, so diagnostics could show a helper that no longer existed.
- **The daemon died on SIGTERM without cleanup**: held input was not
  released by the daemon, the socket file stayed and the status file kept
  saying "running". SIGTERM now shuts down cleanly (bounded by
  `TimeoutStopSec=15`).
- **An install restarted the daemon twice.** `setup` wrote the version stamp
  after restarting the daemon, which then saw a newer install and restarted
  again.
- **A late answer to a cancelled request terminated the stdio server**
  (an assertion in the MCP SDK's `RequestResponder.respond`); such answers
  are now dropped.
- **The client's broken environment reached tools**: Claude Code's
  `CLAUDE_*` variables leaked into agent jobs, and Codex's literal
  `$DBUS_SESSION_BUS_ADDRESS` broke the desktop tools. Tools now run in the
  daemon's environment plus an allow-list from the client.
- **Five tools carried no MCP hints**, so clients assumed the spec defaults
  (destructive, open world); several read-only tools were not marked
  read-only.
- **`[tools] profile` was accepted but never read.**
- **`pcb-do --dry-run` required a config file** although it only parses.
- **Missing dependencies were reported as a bare error code**; the
  capability report now names the package to install.
- **An unsupported session (X11, a non-GNOME desktop) was reported as an
  unreadable screen lock**; it is now named.
- **The non-live test suites depended on the machine they ran on** (the
  GNOME session, the monitors and the real config, including its grant), so
  they could not run in CI and a grant opened during a run could let a test
  act for real.

### Added

- **A resident daemon** (`pcbridge serve`) behind a systemd user socket
  (`$XDG_RUNTIME_DIR/pcbridge/mcp.sock`, mode 0600), serving local sessions
  and HTTP from one process. A new client reaches the tool list in 22-47 ms
  instead of ~700 ms; with the daemon stopped, socket activation starts it
  in ~0.7 s.
- **`pcbridge stdio`**, a standard-library relay for local clients. On a
  daemon failure it returns retryable errors for requests in flight,
  reconnects and replays `initialize`; without a daemon it runs the server
  in-process and reports the degraded mode.
- **Jobs in their own systemd scopes**, so a daemon crash or an update
  restart does not kill them; `pcbridge stop` still ends them.
- **Updates without interruption**: the daemon restarts into a new install
  only when no job, grant or call is active.
- **The `pcbridge` command**: `setup`, `connect`, `doctor` (`--json`,
  `--fix`), `status`, `lock`, `unlock`, `stop`, `remote`, `logs`, `report`
  (redacted), `update`, `uninstall`. Every change `setup` makes is backed up
  with a `ROLLBACK.md`.
- **Packaging**: a wheel that carries the native helper, a user-level
  installer, and a `.deb` per distribution release built with `dpkg-deb`.
- **CI**: Python 3.12, 3.13 and 3.14 suites, the extension's gjs tests, a
  headless GNOME Shell smoke test, container installs on Ubuntu 24.04,
  Debian 13 and Ubuntu 26.04, and draft releases from tags.
- **A panel indicator** in the GNOME extension: daemon up or down, the grant
  and its time left, running jobs, remote access, and "Lock desktop control
  now". It reads `state_dir/status.json`, which the daemon writes on change.
  An optional kill-switch shortcut, off by default.
- **A D-Bus `Version` property** on the extension's interface, reported by
  `pcbridge doctor` for the running session.
- **Any monitor layout**: a 14-layout fixture matrix (single, 4K at scale 2
  in both layout modes, fractional scales, ultrawide, portrait 90/270,
  vertical stack, three mixed monitors, negative origin, gaps) resolved
  identically by Python and Rust, and verified in a headless GNOME Shell.
- **A pixel-area cap for scaled screenshots** (default 1536x864), so a
  16:10 or 4:3 monitor costs no more than 16:9, and a note when small text
  shrinks below half its size.
- **A platform report** in `system_capabilities`: GNOME Shell version,
  session type, and whether Mutter's ScreenCast and RemoteDesktop are
  present; an untested GNOME version is reported, not refused.
- **`[tools] profile`**: `full` (default), `core` or `desktop`.
- **Agent CLI discovery** beyond PATH (`~/.local/bin`, npm, bun, cargo,
  nvm), because a systemd daemon does not see a login shell's PATH.

### Changed

- **Every message a user or a model reads is English**: tool results,
  errors, suggested actions, the CLI, the installer, the extension, the
  shell scripts, `config.example.toml` and the computer-use skill. A contract
  test guards it. Error codes, categories, scopes, field names and
  configuration keys are unchanged.
- **Configuration moved to XDG locations** (`~/.config/pcbridge/config.toml`)
  with `config_version = 2`, migrated with a backup. A 1.x file loaded as is
  keeps its meaning.
- **The computer-use skill no longer hard-codes this machine's monitors.**
- **`MCP` tool hints are decided for every tool in one table** and a tool
  cannot be registered without them.
- **The systemd unit** no longer sets `PATH` or `DISPLAY` (both are derived)
  and does not restart on configuration errors.
- **Documentation** was rewritten as a small English set; the project's
  measurements live in `docs/dev/measured-facts.md`.

### Removed

- The per-client stdio server as the default path (still available as the
  fallback and through `PCBRIDGE_IN_PROCESS=1`).
- `add_client.py` and `capture.sh`, which served Gemini Spark only; Gemini
  Spark is no longer a target.
- The development journals (`WALKTHROUGH.md`, `PLAN.md`, `UYGULAMA.md`,
  `ADIMLAR.md`, `GOREV-kurallar.md`, `GELISTIRME.md`, `KURULUM.md`,
  `KULLANIM.md`, `KURALLAR.md`, `JARVIS.md`); their valid content moved to
  `docs/`, and git history keeps them.

### API

- **Entry points.** Clients register `pcbridge stdio`. The 1.x command
  `python -m pcbridge.server --stdio` still works and now relays to the
  daemon. `pcbridge serve` runs the daemon; `pcbridge-native` is unchanged
  as a binary name.
- **Tools.** The same 36 tools with the same names and input schemas. Their
  annotations now always carry all four hints. Output text is English;
  clients that matched Turkish text must match the stable
  `structuredContent.error` fields instead.
- **Error codes** (`ErrorCode`), categories and permission scopes are
  unchanged. Configuration errors exit with 78 (EX_CONFIG).
- **Configuration.** 1.x files load unchanged; `pcbridge setup` migrates
  them to version 2. New keys: `[desktop] screenshot_max_pixels`,
  `[tools] profile` (now read). Unknown keys warn instead of failing.
- **State files** keep their formats: the grant file (`desktop_unlock.json`),
  job records, the shot records and `pointer.json` are readable by 1.x and
  2.0 processes at the same time. `topology_id` gains a `,p` suffix only for
  a monitor whose pixel ratio differs from its scale, so ids at scale 1 are
  identical to 1.x.
- **Native helper protocol**: version 1.0, unchanged. `display.snapshot`
  adds a `pixel_ratio` field per monitor; older hosts ignore it.
- **GNOME extension**: the UUID is unchanged. `ActivateWindow` and
  `FocusedWindow` are byte-compatible; `Version` is new. A 1.x extension
  works with a 2.0 server and the reverse.

## 1.x (2026-07 to 2026-09)

pcbridge 1.x grew from an MCP server for Gemini Spark into a desktop
automation server for local coding agents on one machine: OAuth 2.1 for
remote access, background agent jobs, tmux, and desktop control through
uinput, silent Mutter ScreenCast capture and AT-SPI, with a safety gate
(grant, screen lock, idle guard, rate limit, audit log). Its last months
moved capture, input and accessibility into a Rust helper (`pcbridge-native`)
with parity gates against the Python paths, added relative pointer motion,
region capture, OCR and the GNOME extension's window activation. Each
client started its own server process.
