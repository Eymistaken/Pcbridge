# Roadmap

What is still open. Everything here is optional: nothing blocks a release.
Each item says what is left and what would prove it done. Finished work is
in [CHANGELOG.md](CHANGELOG.md), the numbers behind it in
[docs/dev/measured-facts.md](docs/dev/measured-facts.md).

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

## Optional: engineering

- **Hyprland acceptance.** Pointer lock, touch transparency on both VM outputs,
  a clipboard change during native typing, native accessibility and batch
  policy checks, fractional rotated capture, negative compositor origin,
  swapped identical outputs, vertical placement, and simulated output removal
  and return have scoped VM evidence in
  [WALKTHROUGH.md](WALKTHROUGH.md). The batch content-policy matrix now covers
  password targets, close shortcuts, repeat clicks, and budget preflight
  through normal MCP in the VM. A separate VM process also proved that its
  execution lock and seeded rate window delay a normal MCP click. A separate
  VM focus shift during a batch wait now stops before the next key. Real MCP
  and `pcb-do` writers were observed in order in the VM. Check physical
  mirror and hotplug, and the intermittent visible-frame refusal
  seen under three saturated VM CPU workers (four follow-up runs passed;
  a safe failure diagnostic is in the fixture, but the proof-loss cause and
  physical workload are not measured),
  and graphical nested checks (live nested IPC routing passed, but this VM
  could not allocate the child's GBM output). A 17-case VM cohort passed
  again after the focus guard, with cleanup between cases. The current
  checkout also passed the full Plasma MCP test in the separate VM, and the
  GNOME 50.5 headless extension,
  capture, and fake-grant checks passed. Expand acceptance and check a real
  GNOME session before claiming general support; no host desktop input was
  used for this regression.
- **Hyprland mirror semantics.** The real active-monitor query hides a
  mirrored output even though `hyprctl monitors all` reports it. Normal MCP
  capture returns the source output, old follower shots are refused, and a
  fresh source shot moves the pointer to the expected compositor position.
  The existing mirror-row refusal contract is not reached in that
  configuration. QMP captures show the grant frame on both separate VM GPU
  displays. Verify it on a physical mirrored connector before choosing
  support or explicit refusal.
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
- **A graphical control panel.** A proposal (Tauri + React) for status,
  grant and jobs in one window; nobody has started it.

## Decided and closed

- Windows and macOS backends were dropped (2026-09-20); the project targets
  Linux desktops on Wayland.
- An XDG-portal capture backend for GNOME was dropped (2026-09-20): it
  flashes and asks for consent. This still holds on GNOME.
- "GNOME only" (2026-09-20) was reopened on 2026-09-23: 2.1 adds KDE
  Plasma 6 on Wayland and Arch Linux. Plasma 5, X11 and other distributions
  stay out.
- `JARVIS.md`, a proposal to grow pcbridge into a personal assistant, is not
  documentation and was removed in 2.0; git history keeps it.
