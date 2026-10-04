# Roadmap

What is still open. Everything here is optional: nothing blocks a release.
Each item says what is left and what would prove it done. Finished work is
in [CHANGELOG.md](CHANGELOG.md), the numbers behind it in
[docs/dev/measured-facts.md](docs/dev/measured-facts.md).

Product direction: improve the existing terminal UI; do not introduce a
Tauri application.

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

## Optional: needs a person at the machine

- **`FocusedWindow` in the real session.** Measured in a nested shell; the
  real session loads the extension at the next login. Done when
  `pcbridge doctor` reports the current extension and a `computer_batch`
  with a click succeeds while a window AT-SPI cannot see (a native Wayland
  game) is in front.
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
