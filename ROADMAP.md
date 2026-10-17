# Roadmap

What is still open. Everything here is optional: nothing blocks a release.
Each item says what is left and what would prove it done. Finished work is
in [CHANGELOG.md](CHANGELOG.md), the numbers behind it in
[docs/dev/measured-facts.md](docs/dev/measured-facts.md).

Product direction: improve the existing terminal UI; do not introduce a
Tauri application.

## Suggested order

The sections below are grouped by topic; this list is the order to work in.
Each stage builds on the ones before it and ends in a releasable state: its
items meet their own "Done when", the suites pass, and CHANGELOG and
measured facts are updated before the next stage starts. Change the order
here, not by moving sections.

1. **Quick wins.** ~~`fs_read` detects a binary file and refuses it (the
   minimal part of *Binary file transfer*)~~ (done, Unreleased); ~~*Keep the
   machine awake while work is running*~~ (done, Unreleased, except the
   optional tunnel part). Small, and they stop silent corruption and frozen
   jobs.
2. **Remote access safety.** ~~*List and revoke remote clients*~~ (done,
   Unreleased); ~~*A separate tool profile for remote access*~~ (done,
   Unreleased). Before the browser: browser tools work
   without the desktop grant, so remote clients gain power, and
   `remote_profile` decides whether a phone gets them at all.
3. **Installation.** ~~*One install script that finds everything itself*~~
   (done, Unreleased); ~~*Python: offer the fix, do not just fail*~~ (done,
   Unreleased); ~~*Optional components as a checklist*~~ (done,
   Unreleased). Makes trying new machines (Pardus and the like) cheap, which
   the later desktop work needs for testing. Left: the checks under each
   item below, mostly on real machines.
4. **Connecting remote clients.** *From installed to connected without
   reading a file*. Before the next release: it makes `install.sh` the
   recommended path, and someone who installs from a release then has no
   way to find the password a remote client needs.
5. **File tools.** ~~*Paginated and ranged file reads*~~ (done,
   Unreleased), ~~the rest of *Binary file transfer*~~ (done, Unreleased),
   ~~*Targeted edits and easier writes*~~ (done, Unreleased), together
   because they share the cursor and conflict design, and checked through
   claude.ai (see measured facts). ~~*Download and upload links for large
   files*~~ (done, Unreleased).
6. **Browser.** In this order: ~~measure *Attaching to the existing
   session*~~ (done: a dedicated pcbridge profile, see measured facts),
   ~~*Browser tools for the user's existing session*~~ (done, Unreleased),
   ~~*Show and hide the browser window on demand*~~ (done, Unreleased),
   ~~*Taking over the agent's browser*~~ (done, Unreleased). Left: the
   checks each item lists.
7. **More desktops.** *Retire the legacy Python desktop backends* first,
   then *X11, starting with Cinnamon and XFCE*, written straight into the
   native helper instead of added to Python and moved later.
8. **Windows.** After everything above; see *Optional: Windows* for what it
   involves. Scope to be written before work starts.
9. **Website.** Last, once the feature set has settled, so the site is not
   chasing every new feature; see *Optional: website*. Until then the
   install script is served from a GitHub release URL.

Alongside, when there is time: the terminal UI logo, *An installer with
its own terminal UI*, *Push notification
when a job ends*, the remaining engineering items, and the *needs a person
at the machine* checks, batched for a session at the machine.

## Optional: terminal UI / product polish

- **Responsive Overview with a live Pcbridge logo.** Redesign the terminal
  UI's Overview around a centered Pcbridge logo at the top. At normal widths,
  show the terminal icon plus the Pcbridge wordmark; when the terminal becomes
  narrow, collapse it to the terminal icon only instead of squeezing the
  wordmark. Treat the icon frame as a quiet live status indicator: no glow
  while idle, a subtle blue edge glow while pcbridge is actively handling a
  request/tool/job, and a red edge glow for an error. Hover and click should
  give restrained feedback; clicking the logo should expose the reason for the
  current state (for example current activity or the last error) without
  cluttering the main Overview. Keep the rest of the screen terminal-native
  and information-dense rather than turning it into a card-heavy dashboard.
  Done when resizing cleanly switches between full and icon-only logo modes,
  idle/active/error transitions are reliable, and mouse interaction works
  without changing grant or daemon safety behavior.
- **An installer with its own terminal UI.** The installer from stage 3
  works but reads like a log. Measured problems, from the code
  (2026-10-14):
  - Choices are typed: `1`/`2`/`3` for the Python, and "a number to tick or
    untick a component" for the optional parts.
  - Long silent waits: downloading the wheel, uv fetching Python (about
    14 s) and `pip -q` show no progress; the Debian VM run took 108 s.
  - Eight numbered steps of `ok` lines (units, launchers, aliases, a backup
    path) that mean little to a new user.
  - No next step at the end: the summary says what works, but not which
    clients were connected, that typing `pcbridge` opens the terminal UI,
    or how to turn on desktop control or remote access.
  - "desktop control is OFF until you enable it there" points at the file.
  The maintainer wants a dedicated installer UI, separate from the
  `pcbridge` terminal UI: choices with the arrow keys or the mouse instead
  of typed numbers, a recommended default marked as such (for example
  "OCR (Recommended)"), visible progress during downloads and installs, a
  short and clean final screen that names the connected clients and says
  to type `pcbridge` to check and change things, and the sudo commands
  still shown before they run. `--yes` and a run without a terminal keep
  working without the UI. Decided with the maintainer (2026-10-17): the
  Python choice comes before pcbridge's own Python exists, so `install.sh`
  gets a small arrow-key prompt of its own for that one question (`stty`
  on `/dev/tty`, number keys still work, no mouse), falling back to the
  numbered prompt without a terminal, with `TERM=dumb` or with `--yes`;
  the wheel download shows `curl --progress-bar`. Everything after the
  venv exists (the component checklist, progress during installs, the
  final screen) is a Textual UI in `pcbridge setup`. Not uv first: the
  question is asked only below Python 3.12, fetching uv and a Python
  (about 14 s) before it would choose option 1 before the user does and
  slow down every machine that already has 3.12, and a machine with no uv
  build for its architecture but a good system Python would stop
  installing. Not urgent; it should not be squeezed in between the
  stages. Done when a first install on a supported and an unsupported
  desktop needs no typed answers, never looks frozen, and ends on a screen
  that says what to do next.

## Optional: file tools

- ~~**Paginated and ranged file reads.**~~ Done (Unreleased): `fs_read`
  starts at the beginning, at `start_line` (negative from the end) or at a
  cursor, ends pages on line boundaries, names the lines covered, the size,
  the SHA-256 and the next cursor, refuses a cursor once the file changed,
  and reads several files with `paths`. Proved over HTTP with OAuth against
  a throwaway daemon (see measured facts). Left: the same check from a real
  remote client (claude.ai, ChatGPT), which the maintainer runs.
- ~~**Targeted edits and easier writes.**~~ Done (Unreleased): the new
  `fs_edit` replaces exact text (one match, `replace_all`, or several edits
  written together), explains missing and ambiguous matches, keeps line
  endings and BOM, shows a diff, has `dry_run`, and refuses a stale
  `expected_sha256`; `fs_write` writes atomically with `expected_sha256`
  and `create_only`. Proved over HTTP with OAuth against a throwaway daemon
  (see measured facts). Left: the same from a real remote client.
- ~~**Binary file transfer.**~~ Done (Unreleased): `fs_read` refuses
  binary and non-UTF-8 files in its default mode with the format and size,
  returns images as MCP image content (scaled to 1568 px when larger), and
  with `encoding="base64"` returns any file's bytes in cursor chunks of up
  to 20,000 characters with the chunk's and the file's SHA-256; `fs_write`
  takes `content_base64` in one call or in `offset` chunks, each checked by
  `chunk_sha256`, that replace the file only when `final_sha256` matches.
  Proved over HTTP against a throwaway daemon for multi-megabyte files, and
  through claude.ai for a 36 KB PNG (see measured facts). The original
  "Done when" (a multi-megabyte PDF through a remote client) is not
  reachable this way: every byte passes through the model, which copied a
  character wrong within 75 KB, so it moves to the item below.
- ~~**Download and upload links for large files.**~~ Done (Unreleased):
  `fs_download_link` and `fs_upload_link`, with the security design in
  [docs/security.md](docs/security.md#file-links). Proved against a
  throwaway daemon over HTTP, with curl and with a real Chrome posing as an
  Android phone, both through Funnel's public relay: 50 MB each way with
  matching SHA-256 (the upload page compared it in the browser), and a used
  or expired link returns 404 (see measured facts). Left, optional: the
  same from a physical phone on a mobile network, and a claude.ai
  conversation that hands a link to the user.

## Optional: browser control

- ~~**Browser tools for the user's existing session.**~~ Done
  (Unreleased): `browser_tabs`, `browser_read`, `browser_screenshot`,
  `browser_navigate`, `browser_act` on a dedicated pcbridge profile (the
  "existing session" is that profile's, where the user signs in once; see
  the attach item below for why not the everyday profile). Pages read as an
  outline with refs, including forms, tables and same-origin frames; every
  action answers with the fresh page. No desktop gate: proved over HTTP
  with OAuth against a throwaway daemon with `[desktop] enabled = false`,
  after `desktop_lock` and with an expired grant, on a fake router panel
  (`tests/integration/test_browser_http.py`), and by reading the real
  router's sign-in page. The audit log keeps no typed text or page
  content; `remote_profile = "readonly"` gets the reads only. Left: a
  signed-in third-party site and the real router panel after the
  maintainer signs in once in the pcbridge browser.
- ~~**Attaching to the existing session: a known constraint.**~~ Measured
  (2026-10-17, Chrome 155 and Brave 155; see measured facts): Chrome
  ignores the CDP flags on its default data directory, Brave honors them,
  and both reach the default profile through the `chrome://inspect` switch
  only with a dialog a person must click for every connection. The chosen
  path is a dedicated pcbridge profile (signed in once, logins persist)
  with pcbridge's own CDP layer over `--remote-debugging-pipe`, the pipe
  held by a keeper process that outlives a daemon restart. The extension
  bridge and wrapping chrome-devtools-mcp or Playwright MCP were weighed
  and not taken.
- ~~**Taking over the agent's browser.**~~ Done (Unreleased):
  `browser_handoff` (with a reason, shown in a notification that has a
  "Give it back" action) and `pcbridge browser pause` / `resume`; while
  the user has the browser every agent action in it is refused with
  `USER_HAS_CONTROL` and reads still work; control goes back from the
  notification, the GNOME panel menu, the terminal UI's top bar, the CLI or
  the phone page. From a phone: a `/browser/<token>` page behind the
  tunnel, bound to the one hand-over, with CDP screencast frames and taps,
  text and keys sent through `Input.dispatch*`. Proved with a two-factor
  step over HTTP against a throwaway daemon and through a real browser at
  phone size, and at the machine on GNOME through the notification's
  button (see measured facts). Left: the GNOME panel menu's items (after a
  log-out loads the new extension), the notification on Plasma, and the
  phone check through Funnel on a real phone.
- ~~**Show and hide the browser window on demand.**~~ Done (Unreleased):
  hidden by default, shown and hidden by `browser_visibility`, `pcbridge
  browser show|hide` and the terminal UI's button (b), found by the pid of
  the browser pcbridge started; showing takes focus only when asked. GNOME
  through the extension (minimized; Mutter 14 cannot take another
  client's window off the task list, so it stays there as
  "pcbridge-browser"), Plasma through a KWin script (also off the task bar
  and the switcher), Hyprland through `special:pcbridge` and a Lua window
  rule. Measured on all three (nested GNOME 46, Plasma 6.7.5 and Hyprland
  0.56.2 in the VMs): a starting browser never seen or focused, loading,
  acting and non-blank screenshots while hidden, focus kept by the user's
  window. Left: the GNOME extension of this version on the real session
  (after a log-out), and Hyprland's hyprlang config provider.

## Optional: remote access

- **List and revoke remote clients.** Done (Unreleased): `pcbridge remote
  clients` lists the remote apps by name, `pcbridge remote revoke NAME` and
  `--all` delete their registrations and tokens, and the terminal UI's
  Remote tab does the same. A new password does not end existing sessions
  (measured), so changing it offers to revoke everything. Left: check it
  once through the real tunnel with a real app (ChatGPT or Gemini) after
  the next install; the tests used a throwaway daemon on localhost.
  `tests/test_e2e.py` now revokes the OAuth clients it registered when it
  ends, failed runs included, and only those (checked against a throwaway
  daemon). The ones earlier runs left in the live `oauth.db` (298
  registrations on 2026-10-14, mostly "Google" and "Gemini Spark (test)")
  were cleared the same day: 281 registrations with the test's redirect
  URI were revoked, 18 remain (16 real Google, ChatGPT, one tokenless
  test entry), and the old file is kept as `oauth.db.bak-2026-10-14`. The
  test registers as "Google", the name Google's real clients use, so
  revoking that name by hand would also sign out a real Gemini.
- **A separate tool profile for remote access.** Done (Unreleased):
  `[tools] remote_profile` (default `same`, following `profile`) decides
  what every HTTP client gets, the static token included; a new `readonly`
  profile offers only tools that change nothing. Hidden tools are refused
  by name too, and the refusal is audited. Left: the same real-tunnel check
  as above.
- **From installed to connected without reading a file.** To connect
  claude.ai or another remote client, the maintainer opens `config.toml`
  and copies the password from it. Someone who installed from a GitHub
  release has the same need but no repository: the file is
  `~/.config/pcbridge/config.toml`, nothing in `pcbridge setup`, `pcbridge
  doctor`, the terminal UI or `docs/install.md` tells them so, and by
  design no command shows the password. Decided with the maintainer
  (2026-10-14): the password is never stored, only its hash, so it cannot
  be read back from any file or command, and a leaked `config.toml`, backup
  or report gives nothing away. In the order to build it:
  1. ~~**Store hashes, not secrets.**~~ Done (Unreleased): scrypt for the
     password, SHA-256 for the static token and every token in `oauth.db`,
     migrated in place when the daemon starts (see measured facts). Left:
     the installed daemon still runs the old code, so the maintainer's own
     config and database are converted at the next `pcbridge update`.
  2. ~~**No password until remote access is wanted.**~~ Done (Unreleased). Local clients on the
     socket never use it, so someone who only uses pcbridge as a local MCP
     server never meets it. Setup stops generating one silently. Remote
     access stays off until a password exists; whenever the user wants it,
     now or months later, `pcbridge remote start`, the Remote tab ("Turn on
     remote access"), an optional step at the end of `pcbridge setup`, or
     `pcbridge set auth.password` creates one, and the tunnel does not open
     without it.
  3. ~~**Choose or generate, shown once, with no time limit.**~~ Done
     (Unreleased): six EFF words, about 77 bits; a new password works
     without a restart. The user types
     a password or lets pcbridge generate one; the default generator makes
     a word passphrase (for example `table-cloud-pencil-sea-seven`), as
     strong as a random string but easy to type on a phone. The screen says
     plainly that this is the only time it is shown and that it cannot be
     recovered, only replaced. It stays next to the remote address until
     the user confirms they saved it, with no timer, so there is time for a
     screenshot or a password manager; an optional copy puts it on the
     clipboard. Setup and `docs/install.md` say this too.
  4. ~~**Creating or replacing it needs the login password.**~~ Done
     (Unreleased): PAM `login` service through ctypes; scripts give it as
     the first line of stdin. Checked against the real PAM both ways on
     this machine (see measured facts). Forgotten or
     leaked, it is replaced, never shown. Creating the first one and
     replacing it both ask first for the user's own login password, in
     every path above (the Remote tab, `pcbridge remote start`, setup and
     `pcbridge set auth.password`, `--generate` included),
     checked through PAM (for example `su -c true "$USER"`), not the sudo
     password: a user without sudo rights must still manage their own
     remote access, `NOPASSWD` sudo would skip the question, and sudo's
     15-minute cache would skip it again. On Windows, the account password
     or Windows Hello. A password that anyone at the keyboard could create
     or replace would hand them remote access. The existing offer to revoke the remote
     clients follows. How scripts and the tests set the password without a
     terminal is decided with the work.
  Done when someone who installed from a release can connect a remote
  client without reading a file by hand, no file pcbridge writes contains
  the password or a usable token, and the password is never created or
  replaced without the login-password check.
- **Push notification when a job ends.** A remote client has to poll
  `job_status` to learn that a long job finished. An optional notifier (for
  example ntfy) could send the job id, status and a one-line summary to the
  phone, without page content or command output. Smaller than the items
  above; nice to have. Done when a finished, failed or cancelled job sends
  one notification and no secret ever appears in it.

## Optional: availability

- **Keep the machine awake while work is running.** Done (Unreleased):
  `[power] keep_awake`, off by default. The terminal UI and `pcbridge
  keep-awake` switch it. While a job runs it holds the inhibitor each
  desktop honors (see measured facts) and releases it after the last job.
  It never outlives the daemon. Left: optionally also hold it while the
  remote tunnel is open, behind its own setting. That needs a reliable
  tunnel state in the daemon, which `status.json` currently learns from
  `tailscale funnel status` every 5 s. Also left: check Sleep chosen on
  purpose and the lock on GNOME, and Plasma and Hyprland on physical
  hardware (see *needs a person at the machine*).

## Optional: installation

- **One install script that finds everything itself.** Done (Unreleased):
  `install.sh` refuses root, downloads the release wheel, checks its sha256
  against `sha256sums.txt` (the release now ships `install.sh` and
  `constraints.txt` too), installs it into the user venv and hands over to
  `pcbridge setup`, which detects the machine in Python
  (`pcbridge/platforminfo.py`, `loginctl` over SSH), shows the plan, asks
  once and prints every sudo command first. Packages only for what works
  here; `[tools] profile = "core"` on an unsupported desktop unless the
  user chose a profile; a summary at the end; `--wheel`, `--version`,
  `--uninstall`. The packaged native helper is skipped when glibc is older
  than the binary needs. The "download, check the checksum, then run" path
  is in `docs/install.md`. Measured from a clean VM on Arch/Plasma and on
  Debian 12/Cinnamon/X11 (see measured facts). Left: run
  `curl … /releases/latest/download/install.sh | sh` once the next release
  publishes the script; a real Pardus (23 and 25), Ubuntu 22.04 and a GNOME
  desktop through the script; an interactive sudo password prompt (the VMs
  use NOPASSWD); aarch64 needs a release wheel first. Making it the
  recommended path everywhere (website) comes with *Optional: website*.
- **Python: offer the fix, do not just fail.** Done (Unreleased): with
  Python < 3.12 the script offers (1, the default and `--yes`) a
  pcbridge-only Python from a pinned, checksummed uv under
  `~/.local/share/pcbridge/`, (2) the distribution's command (deadsnakes on
  the Ubuntu family only, `python3.13` on Fedora; Debian 12 / Pardus 23 are
  told there is none), (3) quit. The system `python3` is never repointed.
  Measured on Debian 12 (Python 3.11.2 -> CPython 3.12.14). Left: the
  deadsnakes and Fedora options were not run on a real system.
- **Optional components as a checklist.** Done (Unreleased): where desktop
  control is supported, setup offers OCR (tesseract, English and the
  locale's language; names for Debian, Arch and Fedora in `distro.py`) in
  one list, and `pcbridge doctor --fix` offers missing parts again. Setup
  also installs the desktop extras (`evdev` is built from source, so the
  compiler packages join the plan when missing). Left: offer a prebuilt
  native helper when there is no Rust toolchain on a git install; Fedora's
  names are untested.

## Optional: more desktops

- **X11, starting with Cinnamon and XFCE.** Both run on X11 in practice
  (Linux Mint and Pardus Cinnamon start X11 by default; their Wayland
  sessions are still experimental), and pcbridge refuses desktop tools on
  X11 today. Adding them means one shared X11 backend first, then the
  per-desktop indicators. X11 is easier in most parts: capture with
  XShm/XGetImage, input through the existing uinput path (XTEST as an
  option), window activation with EWMH `_NET_ACTIVE_WINDOW`, AT-SPI as is,
  idle time from the XScreenSaver extension, and the lock from the
  `org.cinnamon.ScreenSaver` and xfce4-screensaver / light-locker D-Bus
  interfaces. The hard part is the grant indicator: Cinnamon's shell is a
  GNOME Shell fork with Muffin (a Mutter fork), so the GNOME extension's
  frame and panel icon can likely be ported as a Cinnamon extension/applet;
  XFCE has no such mechanism and needs a panel plugin or a lasting
  notification plus a frame window. The security model is weaker on X11
  (any client can read the screen and send keys; nothing like Hyprland's
  frame proof exists), and `docs/security.md` must say so. A shared X11
  backend also brings MATE and similar desktops most of the way. Publishing
  the Cinnamon applet on Cinnamon Spices needs a PR to their repository;
  the source stays in this repository. Done when Cinnamon and XFCE on X11
  pass the desktop checks (capture, input, window focus, lock, idle, grant
  indicator and kill switch) and `system_capabilities` reports them.

## Optional: Windows

- **Windows support (to be scoped).** Planned after the stages above. Not
  designed yet; what is known to differ, so the scope can be written:
  the daemon's Unix socket and systemd socket activation (Windows has
  AF_UNIX sockets and named pipes, and services or a per-user startup task
  instead of systemd), tmux (none; ConPTY or a different live-terminal
  design), uinput (SendInput), screen capture (Windows.Graphics.Capture),
  AT-SPI (UI Automation), window activation (Windows' foreground-window
  rules), the screen lock and idle time, the grant indicator and kill
  switch, the coding agents' CLIs and paths, and packaging and install.
  The native helper's provider design is the natural home for the
  Windows desktop backend. Done when a scope document lists which tools
  ship on Windows, which do not and why, and the security model for it.

## Optional: website

- **A project website, after Windows.** Deliberately last: updating a site
  for every feature is the maintenance this order avoids. The main reasons
  for it are a stable install address (`curl -fsSL https://.../install.sh |
  sh`, so moving the hosting never breaks the command; the GitHub release
  URL used until then keeps working or redirects) and a visible place for
  the checksum/signature check and the security warning, which must be on
  the first screen as it is in the README. To keep upkeep near zero, the
  documentation pages are generated from `docs/` (Astro Starlight or MkDocs
  Material), never written twice; the hand-written part is one landing page
  that changes rarely. Demos are an asciinema recording of the terminal UI
  and a short video of an agent using the desktop. The site lives in this
  repository (for example `site/`) and CI publishes it on each release
  (Netlify or GitHub Pages). The same visual language as Eymistaken's HUD
  site can tie the projects together. Done when the install command points
  at the site, the docs pages build from `docs/` with no manual copy, and a
  release updates the site with no hand edits.

## Optional: needs a person at the machine

- **`FocusedWindow` in the real session.** Measured in a nested shell; the
  real session loads the extension at the next login. Done when
  `pcbridge doctor` reports the current extension and a `computer_batch`
  with a click succeeds while a window AT-SPI cannot see (a native Wayland
  game) is in front.
- **Keep awake on real hardware.** Measured in VMs only, and this machine
  has suspend turned off. Check these on a laptop or desktop with idle
  suspend on: GNOME still locks while a job runs, Sleep chosen from the menu
  still suspends, and closing the lid locks (GNOME's rule with a suspend
  inhibitor). Do the same on physical Plasma and Hyprland. Done when each
  case is recorded in measured facts.
- **OCR on this machine.** `find_text` / `wait_for_text` need
  `sudo apt install tesseract-ocr` (plus `tesseract-ocr-tur` for Turkish).
  Measure: finding and clicking a known word in `gnome-text-editor`, a game
  menu entry, and the time to read a full monitor.
- **Cursor overlay with a physical mouse.** Off by default (flag file
  `~/.local/state/pcbridge/gorunur-imlec`); measured only in a nested shell.
  Done when clicking and scrolling with the physical mouse stay normal for a
  working session with it on.

- **KDE Plasma on real hardware.** Plasma was tested end to end in a VM
  (virtio-gpu, two 1280x800 outputs). Done when the Plasma checks
  (`tests/live/kde/check_mcp.py`) pass on a physical machine, with a
  fractional scale and a real lock and unlock.
- **Hyprland on physical hardware.** The two-output VM acceptance results
  are in [measured facts](docs/dev/measured-facts.md#hyprland-release-scope-and-limits).
  Check a real mirrored connector, monitor hotplug, fractional scale,
  pointer/touch input, and lock and unlock before extending the support
  claim beyond the tested VM configuration.
- **GNOME regression in a real session.** GNOME 50.5 extension and capture
  checks passed in a private headless VM session; the host's GNOME 46
  desktop received no input during this release cycle. Run a scoped real
  session check when a person is available to watch the machine.

## Optional: engineering

- **Hyprland frame proof under saturated load.** One three-worker VM run
  refused capture with `BACKEND_UNAVAILABLE` while the grant was active;
  four follow-up runs passed. The cause of that transient proof loss is
  unknown. Use the fixture's safe frame-health diagnostic on a recurrence
  and measure a physical workload before changing the fail-closed rule.
- **Graphical nested Hyprland.** Parent/child IPC isolation passed with a
  second live VM compositor, but the child could not allocate a GBM output
  in QEMU. Repeat with a working graphical nested output before claiming
  nested capture or input support.
- **Atomic clipboard ownership during paste.** The restore path now keeps a
  different clipboard value observed after paste, but `wl-copy` exposes no
  atomic owner identity to this path. An identical replacement or a change
  after the re-read can still be overwritten. Close this when an atomic owner
  check is available and measured on supported desktops, or document and
  accept the remaining race as product behavior.
- **`is_open()` does not see the screen lock.** When the screen locks,
  Mutter closes the screen share but the Python side still reports it open.
  Access is not affected (the gate answers `SCREEN_LOCKED`, and expiry
  cleanup no longer trusts the flag); only the "screen share open" line of
  `desktop_unlock` and the capability report can be wrong while locked. The
  fix asks the native helper for its session state (a `capture.session_state`
  protocol method), and needs a real lock and unlock to verify.
- **Retire the legacy Python desktop backends.** The native helper is the
  default for capture, input and accessibility; the Python paths remain as
  the fallback when no helper is packaged. Retiring them needs the helper on
  every supported platform build and a release with the fallback unused.
- **The pixel-area cap and the Anthropic API.** Whether the API itself
  shrinks a 1536x864 picture (about 1.33 MP) was not measured. If it does,
  `shot=` coordinates carry a small systematic error on the right edge, and
  the default cap should drop to what the API keeps as is.
- **A repeat-click counter across calls.** The third-click stop works within
  one sequence; an agent looping over separate `ui_click` calls is not
  caught (see [docs/dev/desktop-rules.md](docs/dev/desktop-rules.md)).
- **lintian in CI** for the `.deb`.
- **GNOME 47, 48 and 49.** The extension declares 46 and 50, the versions
  verified; the ones between are expected to work. Done when each passes the
  headless extension smoke and joins `shell-version`.
- **An AUR package.** `packaging/arch/PKGBUILD` builds from a checkout;
  publishing needs a `-git` or release-tarball variant and an AUR account.
- **A frame around the screen on Plasma.** The grant shows as a lasting
  notification there; a KWin effect or a layer-shell overlay could draw the
  GNOME frame's equivalent.
- **A lock shortcut on Plasma out of the box.** `pcbridge-lock.desktop` is
  installed with no shortcut, like the GNOME one; binding one needs
  kglobalaccel (`X-KDE-Shortcuts`), which was not measured.
- **Plasma's grant notification can be dismissed.** Closing it by hand
  does not end the grant (only "Lock now" does); ending the grant on dismiss
  needs a check that it was not closed by pcbridge itself.

## New ideas

Notes from driving an Arch Linux VM in GNOME Boxes (installing Hyprland and
Caelestia through the VM window). Not yet triaged.

- **Focus tracking misreads a VM window that grabs the keyboard.** With the
  Boxes window in front and holding the keyboard grab, the focused window was
  repeatedly reported as Chrome, so `expect_focus` stopped correct
  sequences after a click. Done when a focused Boxes/virt-manager window with
  an active keyboard grab is reported as itself.
- **`super+q` treated as a close shortcut everywhere.** It needed
  `confirm_close`, but on Hyprland's default config it opens a terminal.
  Apply the close-shortcut guard per desktop (GNOME, Plasma) or only to
  combinations known to close on the current desktop. Done when `super+q`
  goes through without `confirm_close` on Hyprland and inside a VM window.
- **Raw typing for non-US layouts.** `raw` sends US key codes, so it is
  correct only when the receiving side uses a US layout (it was right inside
  the US-layout VM and produced `*`, `.`, `$` on the Turkish host). A
  `layout` parameter (e.g. `layout: "tr"`) mapping characters to the target
  layout's key codes would make raw typing work in VMs and consoles that use
  other layouts. Done when `raw` with `layout: "tr"` types punctuation and
  Turkish letters correctly into a Turkish-Q VM console.
- **Longer waits inside `computer_batch`.** `wait` is capped at 30 s, so
  long installs (package builds, AUR) needed loops of 45 s `wait_for_text`
  calls. Either a higher cap or a batch step that waits for text to appear
  (e.g. `{"a": "wait_text", "text": "...", "timeout": 600}`) would cover it.
  Done when one call can wait several minutes for a prompt to come back.

What worked well and should stay: `wait_for_text` for long-running jobs and
`region` captures with `shot=` for reading small parts of the screen.
