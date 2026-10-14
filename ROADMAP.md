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
5. **File tools.** *Paginated and ranged file reads*, the rest of *Binary
   file transfer*, *Targeted edits and easier writes*. Together, because
   they share the cursor and conflict design.
6. **Browser.** In this order: measure *Attaching to the existing session*,
   then *Browser tools for the user's existing session*, *Show and hide the
   browser window on demand*, *Taking over the agent's browser*.
7. **More desktops.** *Retire the legacy Python desktop backends* first,
   then *X11, starting with Cinnamon and XFCE*, written straight into the
   native helper instead of added to Python and moved later.
8. **Windows.** After everything above; see *Optional: Windows* for what it
   involves. Scope to be written before work starts.
9. **Website.** Last, once the feature set has settled, so the site is not
   chasing every new feature; see *Optional: website*. Until then the
   install script is served from a GitHub release URL.

Alongside, when there is time: the terminal UI logo, *Push notification
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

## Optional: file tools

- **Paginated and ranged file reads.** Replace tail-only reads with explicit
  line ranges or offsets/cursors and bounded page sizes. Return the covered
  range, total size, truncation/EOF state and a continuation cursor so an
  agent can recover every part of a large file in order. Make multiple-file
  reads convenient and keep response budgets explicit. The current behavior
  was measured in [file-tool evidence](docs/dev/measured-facts.md#file-tools-through-remote-mcp-2026-10-04).
  Done when an agent can read the beginning, middle and end of a file larger
  than the current response cap through file tools alone, without dropped or
  duplicated content or shell-based slicing.
- **Targeted edits and easier writes.** Keep create/overwrite/append, and add
  an edit/patch tool for exact text replacements or focused changes without
  resending the whole file. Provide a diff preview, explicit single/all-match
  behavior, clear missing/ambiguous-match errors, expected-content/hash
  conflict detection and atomic writes. Preserve encoding and line endings.
  Done when a small edit to a large file changes only the requested region,
  concurrent changes produce a conflict instead of silent replacement, and
  multiline UTF-8 edits can be submitted without shell escaping.
- **Binary file transfer.** `fs_read` and `fs_write` handle UTF-8 text
  only. Done: `fs_read` no longer decodes with `errors="replace"`; a binary
  file (NUL bytes or a known signature such as PDF, PNG, JPEG, ZIP, ELF) or
  a non-UTF-8 file is refused with a plain message naming the format and
  size, never returned as replacement characters. Left: a remote client
  (phone, claude.ai, ChatGPT) still cannot take a PDF or picture off the
  machine or put one on it, and cannot fall back to `shell_run` with
  `base64` because of the output cap. Add chunked binary read and write with
  a hash check at the end (sharing the cursor design of the ranged reads
  above), and return images as MCP image content. Done when a remote client
  can download and upload a multi-megabyte PDF and image byte for byte
  (matching hashes).

## Optional: browser control

- **Browser tools for the user's existing session.** Connect to the user's
  existing Chrome/Brave session and preserve its signed-in state. List and
  select tabs, open URLs and navigate, read DOM/accessibility snapshots
  including forms and tables, and click, type or select using observed page
  elements. Read the resulting page state after each action, with screenshots
  available when needed. Support local web interfaces such as router admin
  pages so an agent can inspect them without reverse-engineering their login
  JavaScript and API requests. Browser tools are a non-desktop capability,
  like file, shell and agent tools: browser reads, page actions and browser
  screenshots must work without `desktop_unlock`, without an active desktop
  grant, and independently of `[desktop] enabled`. `desktop_lock` and desktop
  grant expiry must not stop browser access. Use normal MCP/session
  authentication and tool availability, and audit action metadata without
  recording passwords or sensitive page content. Preserve the user's tabs
  and login sessions when detaching. Done when a local router panel and a
  signed-in website can be inspected through the existing browser session,
  actions are verified against fresh state, and browser reads/actions still
  work with the desktop grant closed, after `desktop_lock`, and after its
  expiry. This is a requirement for the future browser implementation.
- **Attaching to the existing session: a known constraint.** Since Chrome
  136, `--remote-debugging-port` and `--remote-debugging-pipe` are ignored
  when Chrome runs with its default user data directory, so CDP cannot attach
  to the user's normal signed-in profile
  ([Chrome blog](https://developer.chrome.com/blog/remote-debugging-port)).
  Brave is Chromium-based and probably behaves the same; not measured. If
  attaching to the default profile turns out to be impossible, two fallbacks
  keep the requirement above: a dedicated pcbridge profile (its own
  `--user-data-dir` under pcbridge's state directory) where the user signs in
  once and the logins persist, or a browser extension that bridges the
  user's real tabs to the daemon, which keeps the existing session but adds
  an extension to build and maintain. Wrapping an existing browser MCP
  server (for example Playwright MCP) behind the daemon is worth weighing
  against writing the CDP layer from scratch. Done when it is measured which
  of these the supported Chrome and Brave versions allow, and the chosen
  path is documented in measured facts.
- **Taking over the agent's browser.** The user must be able to take the
  browser over at any time (captcha, two-factor steps, a first sign-in, or
  "let me do this part"), without a GUI application. Hand-over is an
  explicit state, not a guess: a `browser_handoff` tool (with a reason the
  user is shown through `notify`) and `pcbridge browser pause` / `resume`.
  While the user has control, every agent browser action is refused with a
  `USER_HAS_CONTROL` error, the same pattern as the `SCREEN_LOCKED` gate.
  Control goes back through the places that already exist: the terminal
  UI's top bar, the GNOME panel icon menu, the Plasma notification, or the
  CLI. The agent works in its own window or tab group and never acts on the
  user's other tabs on its own. For remote take-over (from a phone), the
  daemon can serve a small `/browser` page behind the existing Funnel and
  OAuth: frames from CDP `Page.startScreencast`, input through
  `Input.dispatch*`. That is a web page, not an application, so it fits the
  no-Tauri direction. Done when a sign-in with a captcha or a 2FA step can be
  handed to the user and back mid-task, both at the machine and from a
  phone, with no agent action landing while the user has control.
- **Show and hide the browser window on demand.** The browser is hidden by
  default and brought on screen when asked: by the agent (`browser_show` /
  `browser_hide`, or one `browser_visibility` tool; a hand-over shows it
  automatically), by `pcbridge browser show` / `hide`, or by a button in the
  terminal UI's top bar. Headless is not the way: headless or headed is
  fixed at launch, and relaunching loses page state (a half-filled form, an
  open captcha), exactly when take-over is needed. Chrome always runs
  headed, and pcbridge hides and shows its window through the compositor,
  since a Wayland client cannot move or un-minimize itself: the GNOME
  extension (hide the window and keep it off the task list), a Hyprland
  special workspace, a KWin script on Plasma. The window is found by the PID
  of the Chrome pcbridge started, never by application name, so the user's
  own Chrome windows are never touched. Showing or hiding sends no input, so
  like the other browser tools it does not need the desktop grant. Default
  is show without taking focus; taking focus is a separate option. Chrome
  throttles hidden windows, so launch with
  `--disable-backgrounding-occluded-windows`, `--disable-renderer-backgrounding`
  and `--disable-background-timer-throttling`. Done when, on GNOME, Plasma
  and Hyprland, the hidden browser keeps loading pages, acting and returning
  non-blank CDP screenshots, show/hide works from the agent, the CLI and the
  terminal UI, and showing never steals focus unless asked.

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
  (2026-10-14), in the order to build it:
  1. **The first install asks for it.** `pcbridge setup` on a new config
     lets the user choose the remote password, or shows the generated one
     once, together with the remote address. Someone who knows the
     password from the start rarely needs to see it again.
  2. **The terminal UI shows it after the login password.** A "Show
     password" action on the Remote tab asks for the user's own login
     password, checked through PAM (for example `su -c true "$USER"`), not
     the sudo password: a user without sudo rights must still see their own
     password, `NOPASSWD` sudo would skip the question, and sudo's 15-minute
     cache would skip it again. On Windows, the account password or Windows
     Hello. The password stays on screen briefly next to the address; an
     optional copy puts it on the clipboard and clears it after a few
     seconds. This is a deliberate step, not a wall: `config.toml` is the
     user's own file and anyone with their shell can read it; the check
     keeps the password from appearing by accident or while someone
     watches. It relaxes the "never print secrets" rule for this one
     action only.
  3. **Resetting stays an option, never a requirement, behind the same
     check.** The Remote tab offers a new password next to Show, and it
     asks for the login password first as well: a reset that anyone at the
     keyboard can do would let them take over remote access, which defeats
     the check on Show. `pcbridge set auth.password` (and `--generate`)
     asks too, so the command line is not a way around the terminal UI;
     how scripts and the tests set it without a terminal is decided with
     the work. The offer to revoke the remote clients after a new password
     already exists. A user who only wants to see the password is never
     pushed to change it.
  Done when someone who installed from a release can connect a remote
  client without reading a file by hand, and the password is never shown
  or replaced without the login-password check.
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
