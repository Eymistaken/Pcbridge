# Measured facts

This is pcbridge's hard-won knowledge: things that were **measured** on the
reference machine, not assumed. Each entry keeps the number and the reason,
because in this project "it did not raise an error" is not evidence. When a
design decision below looks odd, the measurement next to it is why.

Reference machine: Zorin OS 18.1 (Ubuntu 24.04, GNOME Shell 46, Wayland),
two 1920x1080 monitors at scale 1.0, keyboard layout `tr+intl`. KDE Plasma
and Arch Linux were measured in the test VM (`scripts/dev/arch-vm.sh`): Arch
with Plasma 6.7.5 (KWin 6.7.5) and GNOME 50.5, Python 3.14, two virtio-gpu
outputs of 1280x800; see [KDE Plasma](#kde-plasma).

Older journals (`WALKTHROUGH.md`, `PLAN.md`, `UYGULAMA.md` and others) were
folded into this file for 2.0 and removed; git history keeps them.

## Platform and session

- **X11 is not an option.** The targets are GNOME and KDE Plasma on
  Wayland; the desktop tools are built on Mutter and GNOME Shell, or KWin,
  plus AT-SPI.
- **Mutter runs the PHYSICAL layout mode by default** (`GetCurrentState`
  property `layout-mode` = 2, measured 2026-09-23; the logical mode needs the
  `scale-monitor-framebuffer` experimental feature). In that mode positions
  and sizes are framebuffer pixels and the scale only enlarges the UI. A 4K
  panel at scale 2 spans 3840x2160 canvas units. Measured in a headless
  shell: Mutter accepted a 1080p neighbor at x=3840 and refused x=1920 as
  "Logical monitors not adjacent". pcbridge 1.x divided by the scale anyway.
- **A stdio process's live environment can differ from `/proc/<pid>/environ`.**
  Measured 2026-09-20: in a process Claude Desktop started, `XDG_SESSION_TYPE`,
  `XDG_CURRENT_DESKTOP`, `GDK_BACKEND`, `DISPLAY` and `XAUTHORITY` were empty
  at run time while `/proc` showed them set. Chromium-based apps then picked
  X11, found no `DISPLAY` and segfaulted, while `gtk-launch` returned 0 (the
  only symptom: "the window never appeared"). Brave: 0 processes; with only
  `XDG_SESSION_TYPE=wayland` added: 9. `ensure_session_env()` derives these
  from the Wayland socket and the running Xwayland's command line.
- **Codex passed `DBUS_SESSION_BUS_ADDRESS` as the literal text
  `$DBUS_SESSION_BUS_ADDRESS`**, which broke every desktop tool.
  `ensure_session_env()` repairs it from `/run/user/<uid>/bus`. Since 2.0 the
  daemon runs in systemd's environment and the relay forwards only an
  allow-list (tested: the literal never reaches a tool).
- **`Shell.Introspect` is closed on GNOME 46** ("Access denied"). Window list
  and focus come from AT-SPI, or from pcbridge's own extension.
- **`org.gnome.Shell.Screenshot` is closed too**, and `gnome-screenshot` and
  the XDG portal both flash the screen white and play a sound
  (`gnome-screenshot` draws the flash itself: `cheese_flash_fire`). The
  screen-sharing path (Mutter ScreenCast over PipeWire) has no flash:
  833 ms (gnome-screenshot) vs 497 ms (portal) vs 240 ms (screencast); the
  same region was 99.8 % identical.

## Coordinates and monitors

- **One coordinate space.** Every internal API uses global canvas
  coordinates (here 0..3839 x 0..1079); `capture.to_global()` is the only
  place that converts `monitor=` or `shot=` input. Converting in two places
  once meant clicks landed 1920 px to the left, silently.
- **Monitors are numbered by position, left to right then top to bottom.**
  Here DP-2 (x=0) is `monitor=1` and DP-1 (x=1920, primary) is `monitor=2`.
- **Connector names are not stable.** Measured 2026-09-12: DP-1/DP-2 became
  DP-3/DP-4 with the geometry untouched (Mutter and `xrandr` agreed), so
  `topology_id` leaves the connector out and `serial` is the stable identity.
- **Canvas and compositor coordinates are separate spaces.** The canvas
  always starts at (0,0); a negative compositor origin is translated once
  while the table is read, and `Monitor.platform` keeps the original.
- **Logical sizes round half away from zero** (960.5 -> 961). Python's
  `round()` is banker's rounding; the rule is written out in Python and Rust.
  Mutter chooses fractional scales that keep logical sizes integral
  (2880x1800 at "1.75" is 2880/1648 = 1.7476 -> 1648x1030).
- **A shot record carries raw pixels and desktop units separately**
  (`source_pixel_size`, `desktop_size`, `scale_xy`), per axis, and the
  `topology_id` it was taken under. A changed layout refuses the coordinate
  (`DISPLAY_CHANGED`); a coordinate in a gap or outside the picture is
  refused. The id is filtered (`SHOT_ID_RE`) because it becomes a file name.
- **A forgotten `shot=` is refused, not guessed**, when a recent downscaled
  shot exists and the coordinate falls inside its box (a 1536-wide
  picture's (640, 360) is the middle of the LEFT screen as a global point).
  Switch: `[desktop] ambiguous_coord_guard`.
- **Headless verification** (2026-09-23, gnome-shell --headless with
  virtual monitors, private bus): 4K@2 + 1080p, ultrawide 3440x1440,
  super-ultrawide 5120x1440, two portrait panels (90/270). Every table,
  `topology_id` and frame size matched the fixtures; frames took 240-590 ms.
  The native helper read the same `topology_id` over live D-Bus.

## Input

- **Keyboard layout `tr+intl`: uinput sends raw keycodes, so ASCII breaks.**
  Text goes through the clipboard (`wl-copy` + Ctrl+V). Key combinations
  (Return, ctrl+v, arrows) are layout independent.
- **`wl-copy` hangs with `capture_output=True`**: it stays alive as the
  clipboard owner and the pipes never reach EOF. Use `DEVNULL`.
- **After a restore `wl-copy` puts text aliases FIRST**
  (`UTF8_STRING, STRING, TEXT, ...`) and `wl-paste` adds a newline unless
  `--no-newline` (measured 2026-09-19); every read uses `--no-newline`.
- **The udev rule's number matters.** `uaccess` is applied in
  `73-seat-late.rules`; a rule must sort before it: `60-pcbridge-uinput.rules`.
- **Never add `BTN_TOUCH`/`BTN_TOOL_PEN` to the absolute pointer**: it turns
  into a touchscreen mapped to one output and cannot reach the second
  monitor. `ABS_X + ABS_Y + BTN_LEFT` spans the whole canvas (error <= 1 px).
- **The pointer travels through intermediate points.** All 48 ABS_X and 48
  ABS_Y events of a 48-step move were read back from the device node, no
  `SYN_DROPPED`; `time.sleep(0.008)` measured 8.07 ms. 960 px -> 186 ms,
  diagonal -> 498 ms (ceiling), 80 px -> 61 ms (floor). Pitfall: read the
  events in parallel while moving, or the evdev client queue overflows and
  only 11 of 96 show up.
- **The last pointer position is on disk** (`state_dir/pointer.json`)
  because every `pcb-do` is a new process; without it every move teleported
  (the user noticed). The first move after a real mouse movement can still
  jump once: Wayland cannot tell anyone where the pointer is.
- **A held key is released after `hold_max_seconds` on its own.** A forgotten
  `release` makes the machine unusable and the agent has no way to see it.
  Whether the kernel releases keys when the device is destroyed could not be
  measured, so `close()` releases explicitly.
- **Two pointer devices: absolute and relative.** Never add `REL_X`/`REL_Y`
  to the absolute device. The relative device NEEDS its buttons (without
  `EV_KEY` udev gives no `ID_INPUT_MOUSE`: dx=50 moved 0 px, with buttons
  23 px). Relative deltas are device units scaled by the user's mouse speed
  (`k = 1 + speed` = 0.46 here: 200 units -> 92 px). After a relative move an
  absolute move to the SAME point did nothing (the kernel drops a repeated
  ABS value), so `move_by` marks the absolute state stale.
- **Relative motion works for native Wayland clients, not for XWayland**:
  Minecraft 26.3 (SDL3) on native Wayland: dx=400 -> 60.0°, XWayland 0°;
  0.15° per unit at sensitivity 0.5, 2400 units per turn; the game drops
  the first motion event after each grab. `SDL_VIDEO_DRIVER=wayland` selects
  the native path. Pointer lock cannot be queried from outside on Wayland.
- **A uinput event resets `Mutter.IdleMonitor`** (104227 ms -> 151 ms), so
  "is the user here" is checked at the start of a sequence, never inside it.
- **Clicks hold 60 ms.** With a 50 ms game tick, 30 ms presses were missed
  30-40 % of the time; 60 ms: 40/40.
- **Native input, measured with the user present** (2026-09-19): 8 targets
  on two monitors, 0 px error; Turkish text byte for byte, clipboard
  restored; on revoke a held key is released within 67 ms, a button within
  33 ms, on expiry within 95 ms.

## Screen capture

- **`optimize=True` cost 84 % of a capture** (Pillow PNG save: 3106 ms vs
  259 ms default, for 5 % of file size). One monitor end to end:
  3698 ms -> 850 ms. A contract test keeps it off.
- **The helper's intermediate PNG uses fast compression**: 445 ms ->
  17.5 ms; native frame call 525 ms -> 98 ms; single monitor ~430 ms total.
- **A single PipeWire stream does NOT reconnect to another node**: asking for
  DP-4 after DP-3 returned DP-3's image, valid in every way. One stream per
  capture fixed it (8/8 correct). Node ids are recycled and their order is
  not stable: streams are matched by the `RecordMonitor` object path.
- **Rust capture = legacy capture**: 100.000 % identical pixels (full size
  and 1536), freshness 12/12 (2026-09-13).
- **Do not measure native capture with a debug build**: 1915 ms vs 271-294 ms
  release (PNG encoding unoptimized).
- **Locking the screen closes native sharing** within 3 s; no PNG while
  locked; it does not reopen by itself.
- **Screen sharing is opened by `desktop_unlock`** and shows GNOME's sharing
  indicator the whole time; it is visible in captures too. Closing it needs
  the process that opened it; `screencast.kill_helpers()` finds the helpers
  of this user by their full path (the `bridgekilit` case, 2026-09-06).
- **`screenshot_scale_long_edge = 1536`**: at 1280 small Turkish diacritics
  blur and words get guessed. The Anthropic API scales images above 1568 on
  its own, which would put a 1.22x error into `shot=` coordinates, so
  captures above 1568 print a warning. 2.0 adds an area cap (default
  1536x864) so 16:10 and 4:3 pictures cost no more than 16:9, and a note
  when small text drops below half its size (ultrawide 3440 -> 45 %,
  5120 -> 30 %).
- **A screenshot costs ~1200-1900 input tokens on Claude**, ~40k on `agy`.
  `ui_dump` is ~0.1 s and a few hundred tokens and cannot miss: prefer it.
- **Claude Code really reads images in tool results** (a hidden value in a
  known PNG came back exactly).

## Accessibility (AT-SPI)

- **Password fields have role `password text` and nothing else** (no state
  bit; `SENSITIVE` means enabled). The gate must look at the role.
- **`get_extents` coordinates are wrong.** Clicks use `Action.do_action`;
  without an `Action` there is an error, never a coordinate fallback.
- **Element identity is the app's bus name + object path** (`:1.44` +
  `node.path`). When a node was inserted, 20 of 33 index paths shifted and
  0 of 33 object paths did. Search by role + label was removed: one window
  had three "Close" buttons and the third closed the window.
- **Raw AT-SPI D-Bus is not libatspi**: GTK4's `GetRoleName` says
  "application"/"generic"/"button" where libatspi says
  "frame"/"panel"/"push button"; `GetActions` is localized, `GetName(i)` is
  not. The native reader maps role numbers with libatspi's table. Native
  dump 14-18 ms vs Python 100-112 ms; window list 6.6 vs 102 ms.
- **An application's answer proves nothing**: `DoAction` on a disabled
  button returns false; `SetTextContents` returns true on a 5-character
  field that kept 5 characters, so written text is read back
  (`TEXT_MISMATCH`). `InsertText`'s length argument is ignored. PyGObject
  pitfall: `get_text_iface()` is the node itself; call
  `Atspi.Text.get_text(node, 0, n)`.
- **AT-SPI may mark no window ACTIVE** (after login, or with a native
  Wayland game in front); focus then comes from the extension's
  `FocusedWindow`. Electron apps show their window but not its contents
  (Vesktop: 0 nodes).
- **GNOME's overview blocks the Wayland clipboard**: `type` after `super`
  switches to raw keys.

## Windows and applications

- **Raising a window through the extension: 4.4 ms average** (3-6 ms,
  2026-09-12) against 6701 ms through GNOME search (~1500x).
- **`gtk-launch` puts the app in the caller's cgroup**; from the service a
  restart would kill it. `systemd-run --user --scope` costs +60 ms and gives
  the app its own scope.
- **GNOME search finds more than applications** (chats, files, settings);
  with no match Enter opens a web search in the browser. Only installed
  application names are typed, and the result is verified against the
  `.desktop` entry.
- **The extension's `ActivateWindow` rereads the grant on every call** and
  checks `until` (revoke zeroes it); it refuses `skip-taskbar` windows and
  ambiguous names.

## Safety and grant

- **The grant slides**: `until` moves to `now + 90 s` on every action,
  `hard_until` is the ceiling. In a real task with thinking in between it had
  to be reopened four times (2026-09-20).
- **Every `desktop_unlock` writes a new `grant_id`**, and a native helper is
  bound to one grant: a second unlock closed the share and every later
  capture said `REVOKED` until fixed by rebinding helpers per grant.
- **`[desktop] enabled = false` turns off only the desktop tools.** Shell,
  agent, file and tmux tools keep working and are audited instead.
- **Measured gate costs**: screen lock query p50 2.9 ms, lease touch p50
  0.03 ms (12 ms when written).

## The resident daemon (2.0)

- **Cold start to `tools/list`**: 1.x per-client server 692.8 ms; 2.0 relay
  to a running daemon 22-47 ms; with the daemon stopped, socket activation
  693-711 ms; with no daemon at all, the in-process fallback 700 ms.
- **Relay overhead ~0.1 ms per call** (40 calls; budget 5 ms). Relay RSS
  14 MB; daemon RSS 117 MB (1.x per-client process 92 MB each).
- **Faults**: `kill -9` of the daemon -> the in-flight call gets a retryable
  error after 9.8 ms, the next call succeeds after 1981.6 ms; stopped daemon
  743.5 ms; stale socket 719.6 ms; no runtime dir 700.3 ms; three clients at
  once 55.3 ms.
- **Jobs run in their own `pcbridge-job-<id>.scope`** and survived `kill -9`
  of the daemon (scope cost ~12 ms). `pcbridge stop` still ends them.
- **The daemon restarts itself for a new install only when idle** (no job,
  no grant, no call in flight; exit 75, systemd restarts it). An install
  must write its version stamp BEFORE restarting the daemon, or the new
  daemon restarts once more five seconds later (seen in the journal).
- **A late answer to a cancelled request killed the MCP SDK's stdio server**
  (an assertion in `RequestResponder.respond`); `app.py` drops such answers.
- **`systemctl --user restart pcbridge` does not update running stdio
  clients**: before 2.0 each client ran its own server process that lived as
  long as the client (five were seen, one a day old). With 2.0 clients talk
  to the daemon through the relay; a client started before the install keeps
  its old process until it restarts.

## GNOME Shell extension

- **GNOME 45+ caches extension ESM modules.** `disable/enable` does not
  reload the code; only a new login does. Develop in a nested or headless
  shell.
- **Deleting the extension directory does not stop the running extension**;
  `gnome-extensions disable <uuid>` does, immediately. Once this was said
  wrong and the machine had to be restarted.
- **Every nested shell run leaves ~13 session services** (gvfsd,
  tracker-miner, dconf, at-spi...). After ~20 runs 298 orphans filled
  `fs.inotify.max_user_instances` (128) and `Gio.FileMonitor` silently stopped
  (`test_state.js` 23/23 -> 11/23). `nested.sh` collects them: nested
  sessions use `/tmp/dbus-*`, the real one `/run/user/<uid>/bus`.
- **`Meta.CursorTracker.set_pointer_visible(false)` hides the real pointer.**
  The cursor overlay once broke clicking with a physical mouse; the fix
  applies the position once per frame (`Meta.Laters`): 263 of 1992 events
  drawn (58/s = frame rate) instead of 1992. Not yet verified with a
  physical mouse in a real session, so it is off by default. A physical
  mouse reports at ~1000 Hz.
- **`Clutter.Canvas` does not exist in this Mutter**; drawing uses
  `St.DrawingArea` + Cairo, and the context must be released with
  `cr.$dispose()`.
- **GNOME's monitor order is not pcbridge's** (`#0` is the primary); the
  extension works with geometry, not indices.
- **`Gio.FileMonitor` rate-limits to 800 ms**; fast changes merge.
- **A static frame costs nothing measurable** (CPU 0.45-0.55 %, +0.08 MB); a
  continuous animation cost 19-24 % CPU in the nested shell, from blending
  large translucent strips at 60 fps, whatever property was animated.
- **Never `set_from`/`set_to` on a `Clutter.PropertyTransition` before it is
  attached**: the property drops to 0. `actor.ease()` chains work.
- **The panel indicator and status file** (2.0) were run in a headless
  shell with only the new extension: icon, " 10m" label, four status lines,
  `Version` "2.0.0" over D-Bus, the kill switch ran `pcbridge lock`; the main
  loop had 0 late ticks. The same headless smoke passes on a GitHub runner.

## KDE Plasma

Measured 2026-09-23 in the Arch VM, Plasma 6.7.5 on Wayland, with the
probes in `tests/live/kde/`.

- **Screen lock: `org.freedesktop.ScreenSaver` at `/ScreenSaver`**, owned by
  `kwin_wayland` itself (Plasma 6 runs the locker inside KWin). `GetActive`
  answers `b`; `ActiveChanged` is the signal. The name `org.gnome.ScreenSaver`
  is listed on Plasma too, but only as an activatable name from the
  installed GNOME packages; pcbridge does not ask it there.
- **Idle time is not on D-Bus.** `GetSessionIdleTime` fails with "not
  supported on this platform". KWin offers the Wayland protocol
  `ext_idle_notifier_v1` (version 2, with `get_input_idle_notification`),
  which a Wayland client has to hold open.
- **Screenshots: KWin's `org.kde.KWin.ScreenShot2`, 7-12 ms a frame** for
  `CaptureWorkspace` (2560x800) and `CaptureScreen` (1280x800), raw
  `QImage` format 6 (ARGB32 premultiplied) written to a pipe. No dialog, no
  flash, no sound. KWin allows it only for a program whose `.desktop` file
  lists `X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2` with that
  program as `Exec`; others get `NoAuthorized`. The check follows the file
  within seconds (removing it revoked access after about 8 s). Authorizing
  the system python would let every python script take screenshots, so
  only the native helper is authorized.
- **KWin's alpha channel is blending residue.** In an ARGB32_Premultiplied
  frame of 1280x800, 2 pixels had alpha below 255; a monitor shows no
  transparency, so the helper drops the alpha byte instead of leaving holes
  in the PNG. Through the helper (debug build, conversion and PNG included)
  a monitor took 49-80 ms.
- **`zkde_screencast_unstable_v1` is not advertised** to an unauthorized
  client, so it was not measured; ScreenShot2 is enough.
- **The absolute uinput pointer maps to the whole canvas, exactly as on
  Mutter.** ABS range 0..canvas-1 from the logical layout: every target
  landed on the same pixel (5 of 5). With the right output at scale 1.5 the
  canvas is the bounding box (2133x800); a point below the smaller output
  is clamped onto it, as on Mutter.
- **The monitor table: `kscreen-doctor -j`.** `pos` is logical, `size` is
  the mode in physical pixels (logical size = size / scale), `rotation` is
  1/2/4/8 (none/left/inverted/right), and `priority` 1 is the primary.
- **A KWin script round trip takes 1-5 ms**: `loadScript` on `/Scripting`,
  `run` on `/Scripting/Script<id>`, and the script answers with `callDBus`
  to a name the caller owns. It lists windows (caption, resource class,
  desktop file, pid, frame geometry), reads the cursor, and
  `workspace.activeWindow = w` brings a window forward with no focus
  stealing prevention in the way. No authorization is needed. Through
  `kwin_helper.py` (a system-python process per call) `activate` and
  `focused` take 83-95 ms; with several Kate windows open, "kate" was
  ambiguous and nothing was activated, as the GNOME extension does.
- **Qt applications join AT-SPI only when `org.a11y.Status.IsEnabled` is
  true.** With it false the tree held no kate, konsole or plasmashell;
  setting it true made the already running ones appear within 2 s, and new
  ones join as they start. The switch is persistent: it is stored as
  `toolkit-accessibility=true` in dconf, so pcbridge restores the previous
  value when the grant ends.
- **A critical notification is the grant's signal on Plasma.** It stays
  until closed; `notify-send -p -w -A lock="Lock now"` prints its id, and a
  click on the button (sent through pcbridge's own pointer) printed `lock`
  and ended notify-send. `CloseNotification` from another process removed
  it from the screen.
- **wl-clipboard works without focus** (KWin offers `ext_data_control_v1`).
- **`gtk-launch`, `gio` and `kstart` are all present** with Plasma plus GTK.

## Build and test

- **`cargo test` stops after the first failing target**; later test
  binaries do not run and do not show up. Use `--no-fail-fast`.
- **PipeWire build headers**: `libpipewire-0.3-dev` 1.0.5 and `libclang-dev`
  (bindgen). Runtime: `libpipewire-0.3-0t64`, `libc6` >= 2.39, `libgcc-s1`.
- **In a GTK4 test window do not use `Gtk.EventControllerLegacy`**: PyGObject
  passes `None` as its event and GTK swallows the exception.
- **The non-live suites used to read the machine they ran on** (screen lock,
  idle time, monitor table, the real config); the first CI run showed it.
  They are hermetic now; live checks need the `PCBRIDGE_TEST_*` flags.
- **`test_e2e.py` section 12 runs a real `claude -p` and spends quota**; it
  used up a daily limit once (2026-08-03). Run with `PCBRIDGE_TEST_NO_AGENT=1`.

## Past accidents

Both came from acting on an unverified assumption, and both now have a
guard in the code (the guards limit damage, they do not make it impossible):

- **2026-08-02**: a click on an assumed editor window landed on the desktop;
  the `ctrl+a` + `Delete` that followed moved 23 desktop items to the trash.
  Now a click that moves focus stops the sequence (`batch_check_focus`).
- **2026-08-03**: a click based on a 69-second-old screenshot landed in
  another application. Now stale shots are refused
  (`agent_shot_max_age_seconds`, default 60 s).
