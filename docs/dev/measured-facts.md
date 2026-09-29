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
- **Hiding the panel icon** (2.2, `indicator-mode = when-granted`) was run
  in headless GNOME Shell 46.0 and 50.5, each under its own
  `dbus-run-session`: started hidden with the mode preset, shown within a
  second of a grant file appearing, hidden again when it closed, and shown
  at once when pcbridge wrote `always`. The icon's visibility is reapplied on
  every update, because the panel shows an indicator's container when it
  adds it.
- **The terminal UI's Panel icon control** (2.4.1, 2026-09-24) was checked
  with a headless Textual screenshot and on the reference GNOME 46 session.
  The UI buttons changed `when-granted` to `always` and restored it; GSettings
  readback matched both clicks, and GNOME Shell logged the indicator shown
  and hidden while desktop control was closed. In the Arch Plasma 6 VM the
  backend reported that there is no pcbridge panel icon and refused a write.
  The focused UI and tool tests passed in the VM under Python 3.14; the
  extension's GJS logic tests also passed there with GNOME Shell 50.5
  installed. No keyboard or pointer input was sent on the reference desktop.
- **GNOME 50 dropped `addTopChrome`'s `affectsInputRegion` parameter**
  ("Unrecognized parameter", and the extension disabled itself at enable;
  measured in a headless GNOME Shell 50.5 in the Arch VM). Without it the
  strips follow their `reactive` flag, which is off.
- **With animations off, `actor.ease()` completes at once** and calls
  `onComplete` synchronously, so a chain that starts the next step from
  `onComplete` recursed without end ("too much recursion"). A headless shell
  has animations off; so does Reduce Animation in Settings.

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
- **A headless KWin in a CI container** (`kwin_wayland --virtual`, no GPU
  render node) composites with QPainter, and ScreenShot2 then answers every
  capture with "Screenshot got cancelled"; in the VM, where KWin uses
  OpenGL, the same call returns the frame in 4-6 ms. Arch's `kwin_wayland`
  also carries the file capability `cap_sys_nice=ep`, so a container needs
  `--cap-add=SYS_NICE` or exec fails with EPERM.

## Hyprland baseline

Measured 2026-09-27 in the separate Arch VM created by
`scripts/dev/hyprland-vm.sh`. This is an implementation baseline, not a
support claim. The VM has Hyprland 0.56.2, hyprlock 0.9.6, hypridle 0.1.8,
and xdg-desktop-portal-hyprland 1.4.1.

- **Actual uinput works through every visible glow edge.** On September 28,
  the isolated VM installed the existing package udev rule: `/dev/uinput`
  was mode 660, root:input, with tester's user ACL and no other-user access.
  The shared native input/gate/execution probe delivered click, drag, and
  scroll at all eight edges to two fullscreen GTK observers within one
  pixel. Two native screenshot centers mapped to (640,400) and (1920,400)
  and received actual clicks. Relative delta (100,0) produced 168 x pixels
  with the VM's acceleration, unchanged y, unknown cached absolute position,
  and accurate subsequent absolute recovery. Clipboard typing delivered
  exact Turkish text and restored the prior sentinel. Shift+A arrived with
  its modifier; revoke released held Shift in 43 ms at the final revision
  (8–43 ms across passing runs) without a next request.
  Keyboard focus remained on observer clients, geometry stayed unchanged,
  and grant/frame/input cleanup completed. Touch, pointer lock, and the rest
  of the acceptance matrix still require their own evidence.
- **The stock config warning affects reserved-area tests.** Its screenshot
  showed the autogenerated-config banner; without glow, monitor focus changes
  moved a 52-pixel reservation between outputs. Comparing the same exact
  observer focus before, during, and after the grant showed no reserved-area
  change from PcBridge. The tagged
  [error-overlay source](https://github.com/hyprwm/Hyprland/blob/v0.56.2/src/errorOverlay/Overlay.cpp)
  updates its reservation on focus changes. Keep this stock warning in the
  VM and control focus in layout assertions; do not alter production glow to
  compensate for a compositor overlay.

- **The session exists without an SSH desktop environment.** SDDM started
  Hyprland on `wayland-1`; the systemd user environment has
  `XDG_CURRENT_DESKTOP=Hyprland` and `HYPRLAND_INSTANCE_SIGNATURE`. Commands
  run from SSH need that session environment imported explicitly.
- **Runtime IPC is richer than a parsed config.** `hyprctl -j binds` returned
  48 bindings. Reported fields include `modmask`, `key`, `keycode`,
  `dispatcher`, `arg`, `submap`, `submap_universal`, `description`, `locked`,
  `mouse`, `release`, `repeat`, `longPress`, `non_consuming`,
  `auto_consuming`, `catch_all`, and `allow_input_capture`. Some default
  bindings report dispatcher `__lua` and an opaque numeric `arg`; the IPC
  result alone does not expose the body of that Lua action.
- **Selected-instance bind queries work.** With `-i` set to the VM's
  signature, `hyprctl -j binds` returned the same 48 entries and
  `hyprctl submap` returned `default`. An attempted switch to an undefined
  test submap was rejected by the compositor and left `default` active; a
  non-default submap transition still needs a registered VM test binding.
  The current [Hyprland IPC reference](https://wiki.hypr.land/configuring/core/advanced-configuration/using-hyprctl/)
  documents `binds`, `submap`, and the `-i` selector. The
  [current bind reference](https://wiki.hypr.land/Configuring/Basics/Binds/)
  describes flags including universal submap and input capture. Raw IPC
  values are retained because the VM reports `submap_universal` as a string,
  and future fields may differ.
- **A registered non-default submap is exposed without running its binds.**
  The later isolated fixture added four collision-checked runtime bindings:
  48 became 52, default changed to the registered custom submap and back,
  and cleanup restored the exact original table. Both production context
  functions preserved every raw IPC field and type; the spy recorded twenty
  queries, exclusively binds/submap. Repeat, locked, non-consuming, release,
  descriptions, and input-capture flags were observed. Universal was the
  string `true`. Device restrictions were accepted by Lua but omitted from
  binds JSON; no restriction meaning was invented. A `mouse:275` key reported
  mouse=false: the tagged [Lua binding implementation](https://github.com/hyprwm/Hyprland/blob/v0.56.2/src/config/lua/bindings/LuaBindingsToplevel.cpp)
  never sets that field. This fixture does not cover mouse=true. Callbacks
  remain opaque `__lua` references and were never executed. The tagged
  [handle removal implementation](https://github.com/hyprwm/Hyprland/blob/v0.56.2/src/config/lua/objects/LuaKeybind.cpp)
  removes matching key/modifier entries broadly even for :unbind on a handle;
  distinct fixture keys and conservative baseline collision checks protect
  existing bindings. The product context remains read-only.
- **Hyprland 0.56 uses Lua-form dispatcher arguments.** The legacy
  `hyprctl dispatch exec foot` failed with a Lua syntax error; the measured
  working form was `hyprctl dispatch 'hl.dsp.exec_cmd("foot")'`. This was a
  diagnostic VM launch, not a proposed pcbridge input backend.
- **The Wayland registry advertises** `ext_idle_notifier_v1` version 2,
  `hyprland_lock_notifier_v1` version 1,
  `ext_session_lock_manager_v1` version 1, `zwlr_layer_shell_v1` version 5,
  `zwlr_screencopy_manager_v1` version 3, and
  `ext_image_copy_capture_manager_v1` version 1. Advertisement does not
  prove a grant-bound screenshot succeeds.
- **Two virtio outputs were active** at logical `(0,0)` and `(1280,0)`, each
  1280x800 at scale 1; `hyprctl locked` answered `false`. A diagnostic
  `grim` call timed out after 8 seconds and produced no file. QEMU's
  `screendump` was black even while `hyprctl clients` listed a running foot
  window; neither is valid evidence of successful native capture.
- **Hyprland monitor JSON keeps mode pixels in `width`/`height`.** A runtime
  `hyprctl eval` change of Virtual-2 to scale 1.25 and transform 1 left its
  JSON `width=1280,height=800`, while its logical span became 640x1024
  starting at `(1280,0)`. The neutral resolver divided by scale and swapped
  axes once; the resulting canvas was 1920x1024. Reverting Virtual-2 and
  explicitly restoring Virtual-1 returned the two outputs to `(0,0)` and
  `(1280,0)`, both scale 1 and transform 0. A first scale-only change caused
  Hyprland to reposition Virtual-1 automatically to avoid overlap, so
  topology tests must query fresh compositor positions instead of assuming
  neighboring outputs stay fixed. `wlr-randr` listed both outputs but its
  attempt to change scale hung; `hyprctl keyword monitor` was rejected under
  the Lua parser. `hyprctl eval 'hl.monitor(...)'` was the measured working
  runtime control in this disposable VM.
- **The first plain `pcbridge` invocation raised a traceback because no
  config file existed.** The path was `PcbridgeApp.on_mount` ->
  `SettingsPane.load` -> `ConfigEditor` -> `locate_config`, ending in
  `SystemExit: No pcbridge config file found`. The VM was newly provisioned;
  this does not establish the cause of the separate user's reported error.
  After installing the example config at mode 0600, `pcbridge` remained open
  in a PTY for the full eight-second observation and rendered Overview,
  Settings, and Tools. The TUI itself is not inherently GNOME-bound. The
  later targeted fix catches SystemExit in SettingsPane.load, whose existing
  Exception handler did not catch it. A fresh empty-XDG VM invocation then
  remained open for eight seconds, rendered all five normal tabs and setup
  guidance, disabled grants, and created no config file. A configured plain
  `pcbridge` invocation also rendered all five tabs without a traceback.
  Actual Textual tests reproduced the locator failure across GNOME, KDE,
  Hyprland, and UNKNOWN, then verified unchanged tabs and inactive writes.
  Hyprland's panel-icon note now explains that no panel/tray is required;
  the native glow remains the visible grant signal.
- **Pre-Hyprland doctor misidentifies this session as GNOME.** With
  `XDG_SESSION_TYPE=wayland` and valid Hyprland IPC, it reported GNOME Shell
  missing, GNOME extension missing, and a Mutter monitor-table failure.
  `/dev/uinput` was present but not writable by the test user; that is a
  separate VM setup issue, not evidence that the input path works.
- **Doctor now inspects Hyprland without opening control or capture.** On
  September 28, the explicit desktop dispatch removed GNOME/KWin checks from
  this VM. Default discovery selected the packaged release helper, whose
  grantless handshake reported the image-copy/output-source protocols. The
  release build has no test harness; its build ID is `unknown-dirty` because
  the VM sync intentionally omits `.git`. Doctor observed authoritative
  unlocked state, missing/fresh/dead native idle records, and inactive/active/
  closed frame state. A dead idle watcher produced ACTIVITY_UNKNOWN even
  with force. An active grant had trusted presentation on all eight strips;
  cleanup removed them. Doctor itself created no grant, state lockfile, input
  device, or capture. Version 0.56.2 is still reported as untested until full
  acceptance. Overall CLI doctor exit 1 remains truthful for outstanding
  installation/configuration checks; desktop diagnostics passing does not
  imply the entire installation is ready.
- **Detailed real input was verified with the default packaged release helper.**
  On September 29, GTK received right/middle buttons 3/2, recognized double/
  triple click counts 2/3, horizontal scroll dx +2/-2 with dy 0, and nine
  intermediate drag updates with button 1 held. Manual releases emptied held
  state. Without another native request, the five-second watchdog released
  Shift after 5.0013 seconds and left after 5.0002 seconds; notifications were
  retrieved once. The eight-edge/typing/shot/relative probe also passed with
  default release discovery; explicit revoke released Shift in 31 ms. The
  helper reported test_harness=false and build unknown-dirty (VM sync has no
  .git). Independent cleanup found no native helper, input observer, glow, or
  idle record left behind.
- **Cursor dispatch is a diagnostic observation, not equivalent input proof.**
  Actual selected Lua hl.dsp.cursor.move acknowledgments positioned the cursor
  at (640,600) and (1920,600), as reported by IPC. GTK reported no corresponding
  motion within one second; the first probe also saw none within three seconds
  on output 1. Distinct native recovery and target moves then delivered GTK
  motion on both outputs. Tagged source calls simulateMouseMovement without
  an explicit pointer-frame call there; framing explains a possible difference,
  but no protocol trace establishes the cause. The production uinput path is
  unchanged. See [the tagged action](https://github.com/hyprwm/Hyprland/blob/v0.56.2/src/config/shared/actions/ConfigActions.cpp#L1181)
  and [input handling](https://github.com/hyprwm/Hyprland/blob/v0.56.2/src/managers/input/InputManager.cpp#L186).
- **Remaining GNOME reporting assumptions were reproduced through real MCP.**
  On September 29, the VM panel_icon status returned a missing GNOME-extension
  error, and the monitor description mentioned GNOME panel menus and Super.
  The scoped fix dispatches all desktop kinds explicitly. Actual MCP status,
  show, and hide now return the Hyprland non-applicability note, with no panel
  or tray requirement and the native glow as the required grant signal.
  Monitor rows identify focused outputs, and the summary names Hyprland's
  focused default without assuming a bar or keybind. Unit spies also prove
  Hyprland and UNKNOWN never read/write GNOME settings or query the extension
  version. GNOME/KDE configured-primary labels and panel behavior are retained.
  The VM proof used desktop-disabled public example settings and opened no
  grant or capture. Review also reproduced an all-false focus table incorrectly
  labeled focused: Python and Rust had inferred focus on the first runtime row.
  A shared reversed-order fixture now preserves absent focus in both adapters;
  Python selects the first output by position and explicitly labels it as a
  fallback. This fixture case is not claimed as a real VM focus transition.
- **Portal services and native capture are separate observations.** The VM
  had xdg-desktop-portal 1.22.1-2, the Hyprland backend 1.4.1-2, GTK backend
  1.15.3-1, PipeWire 1.6.9-1, and WirePlumber 0.5.17-2. All five user services
  were active/running. Portal units were static; PipeWire's service was
  disabled while its socket activation was enabled. WirePlumber was enabled.
  The user manager carried Hyprland, `wayland-1`, and the instance signature,
  but no XDG_SESSION_TYPE or XDG_SESSION_DESKTOP; the VM session wrapper
  supplies the measured Wayland type. Public packaged portal preferences were
  `default=hyprland;gtk`, and the frontend and both implementations owned their
  D-Bus names. These observations do not prove effective request routing or
  browser screen sharing; private higher-precedence portal config was not read.
  See the [Hyprland portal documentation](https://wiki.hypr.land/Hypr-Ecosystem/xdg-desktop-portal-hyprland/)
  and [portal selection rules](https://flatpak.github.io/xdg-desktop-portal/docs/portals.conf.html).
  Setup only prints independent package guidance and does not change services,
  permissions, environment, or config. No tray or panel is required.
- **Compositor window identity includes more than a title.** On September 28,
  `clients` and `activewindow` returned a real foot client with address,
  `stableId`, PID, class, and title. The separate runtime window provider
  listed that client as active even without relying on AT-SPI. The current
  [naming reference](https://wiki.hypr.land/configuring/naming-conventions/)
  documents exact address/stable-ID selectors; dispatch still requires a
  separate acting test through PcBridge's shared gate.
- **The input idle protocol works on Hyprland 0.56.2.** The native watcher
  bound `ext_idle_notifier_v1` v2. A no-input observation advanced from
  2021 to 3022 ms in one second; QMP Shift press/release reset it to 0.
  The original PID-only record remained trusted after `SIGSTOP` (17066 ms),
  so version 2 now binds display, instance, process start ticks, and a
  compositor-confirmed heartbeat. Heartbeats use Wayland sync callbacks every
  500 ms, not timer writes. Stopping the watcher or compositor for four seconds
  returned UNKNOWN; resuming restored a fresh observation. Watcher death and
  a mismatched instance also returned UNKNOWN. The record age limit is 3000 ms.
  This validates observation transport; grant/mid-batch input acceptance remains.
- **Lock state comes from the compositor, not the locker process.** On
  September 28, Hyprland 0.56.2's `hyprctl -j locked` returned an actual
  boolean. The [tagged IPC implementation](https://github.com/hyprwm/Hyprland/blob/v0.56.2/src/debug/HyprCtl.cpp)
  reads `SessionLockManager::isSessionLocked`. Python and native readers
  measured unlocked -> locked -> unlocked with real hyprlock, then remained
  locked after SIGKILL of the locker. The VM's stock
  `misc.allow_session_lock_restore` was false; a replacement locker could not
  reclaim that dead lock. The test preserves the setting and requires a VM
  session reset afterward. Tests wait for hyprlock's `onLockLocked` callback
  before sending its documented test cleanup signal; an earlier signal
  was ignored while surfaces were still being presented. Native lock IPC
  has a 200 ms absolute operation deadline and a 4096-byte reply limit.
  Missing, malformed, oversized, or timed-out replies are UNKNOWN.
  Production Hyprland control remains closed pending a healthy visible frame.
- **The VM needs two actual virtio GPUs, with implicit VGA disabled.** On
  September 28, `/sys/class/drm` showed the original first output belonged
  to QEMU's implicit standard VGA, while only the second belonged to virtio.
  Mapped foot clients still produced all-black QMP frames; diagnostic grim
  timed out after sending its image-copy request. Adding `-vga none` resolved
  both symptoms. `max_outputs=2` alone left the second virtio connector
  disconnected under VNC. Two `virtio-gpu-pci,max_outputs=1` devices restored
  two real 1280x800 outputs with successful capture on each. The GTK pattern
  needed `python-cairo`, now included in VM provisioning. The repeatable
  diagnostic probe verified distinct magenta/cyan output markers and counters
  521 -> 522 on both outputs. It waits for displayed pixels because GTK's
  draw acknowledgment precedes Hyprland's fullscreen fade. This is independent
  graphics evidence, not acceptance of PcBridge's grant-bound capture path.
- **Native layer-shell strips match the reference pixels.** On September 28,
  eight `pcbridge-glow` layers covered the four outer edges of the two outputs,
  with depth 68 at 1280x800. All received `wp_presentation.presented` after
  about 700 ms. Empty surface input regions and keyboard interactivity NONE
  are explicit. The [layer-shell protocol](https://gitlab.freedesktop.org/wlroots/wlr-protocols/-/blob/master/unstable/wlr-layer-shell-unstable-v1.xml)
  specifies that exclusive zone -1 reserves no space and ignores panel
  reservations; zero would move the strip inward around a panel. With the
  existing fullscreen input fixture, every outer-edge sample changed RGB
  `(30,30,40)` -> `(124,124,130)`, matching white alpha 0.42 with 8-bit
  rounding. Inward samples matched the reference falloff within five channel
  values. Focus and fullscreen geometry stayed unchanged; all layers vanished
  after the native probe's breathing cycle/fade-out. This is renderer evidence,
  not grant, input-transparency, or crash-recovery acceptance. Production control
  remains closed until lease-bound frame health is implemented.
- **Lease-bound drawing health survives only fresh real presentation.** On
  September 28, the drawing-only native owner published eight-strip evidence
  bound to exact grant/epoch, session, writer and parent process identities,
  executable, and command arguments. The final first record was 76 ms old.
  Stopping the writer for 1300 ms made health unavailable. Python's LeaseStore
  flock blocked Rust health publication; after a 1300 ms hold, native validation
  refused the stale write and exited. Replacement/revoke/expiry and helper or
  owner death removed all layers. Fractional-scale rotation rebuilt surfaces
  under the same observer identity. The scratch leases used by this probe
  authorize no production input or capture; shared gate integration is pending.
- **Monitor focus is transient, not a configured primary output.** A measured
  `hyprctl dispatch 'hl.dsp.focus({ monitor = "Virtual-1" })'` changed focused
  output Virtual-2 -> Virtual-1 while both positions, sizes, scales, and
  transforms stayed unchanged. Treating `focused` as a permanent primary bit
  in topology causes needless shot invalidation and frame reconstruction.
  The neutral table now marks `primary_is_focus`; default selection still
  follows the actual focused output, while its canonical configured-primary
  topology bit is zero. A VM owner probe switched focus to both outputs and
  checked fresh health every 50 ms for 1.2 seconds each: topology stayed
  `v1|0,0,1280,800,1.0000,0,0|1280,0,1280,800,1.0000,0,0` and visibility
  remained healthy throughout. Physical rotation/scale changes still rebuild.
- **Native visibility loss closes grant-bound resources independently of lock
  IPC.** The real selected-session provider refused a test resource without
  frame health, opened it after presentation, closed it 1079 ms after SIGSTOP
  of the writer and 51 ms after helper death, then refused another opening.
  Fresh presentation allowed resumption; a replacement grant never rebound
  the old native session. A recording-keyboard contract also measured Shift
  down followed by Shift up on frame loss while the lock observer slept for
  500 ms. The VM opened only the harness test flag, no input or capture.
  Python grant opening remained prohibited at this measurement stage.
  This probe also exposed a renderer/reader age mismatch (1200 vs. 1000 ms);
  both now use the common 1000 ms limit. `NativeClient` must pass the selected
  instance signature alongside Wayland display; its earlier allowlist dropped
  it and could not connect the helper to authoritative Hyprland state.
- **Python native frame ownership is exact and observable.** The drawing-only
  VM manager probe reopened the same child PID, preserved a replacement's
  lease and presentation when closing the old parent owner, and retired a
  killed child's own lease in 101 ms. An unavailable executable returned no
  grant success and left no layers. The production shared gate was still closed
  at this measurement stage, before resident CLI/TUI integration.
- **Resident shared-gate lifecycle now works in the disposable VM.** The
  actual `pcbridge unlock` process exited while the daemon kept eight strips
  presented. Replacement changed both grant and frame PID; closing a read-only
  consumer did not retire daemon ownership. SIGSTOP made CLI status paused and
  `window_list` returned typed `BACKEND_UNAVAILABLE`. SIGKILL retired that
  frame's lease in 102 ms. A real 10-second sliding expiry, both CLI and MCP
  lock, and daemon parent death all removed layers and closed control. No input
  or screenshot was requested by this probe. The configured minimum sliding
  timeout is 10 seconds; an initial 3-second test config was refused before
  starting a daemon. This is lifecycle evidence, not complete platform acceptance.
- **Unknown Hyprland idle cannot be forced.** Shared-gate contracts refuse
  UNKNOWN idle even with force and before each admitted action. Native recording
  keyboard evidence releases Shift on idle observer loss within 250 ms, without
  another call; late resources are immediately closed. The native VM visibility
  probe now runs its own real idle watcher and measured stale-frame closure at
  1029 ms and helper-death closure at 103 ms. Existing GNOME/KDE force semantics
  and the 615-check non-live desktop suite still pass.

### Hyprland output coverage during grant presentation

The dedicated VM frame was stopped while its presentation record was still
fresh; monitor 2 was changed to scale 1.25 and transform 1. Native and shared
gate operations both refused the old output proof before its 1000 ms age
limit, measured at 53 ms for the change and two checks. Resuming the frame
rebuilt valid presentation on the changed geometry; restoring both original
outputs rebuilt it again. The guard uses ordered output identities as well
as geometric topology, so renamed/swapped outputs cannot reuse old proof.
No input or capture was opened by this test resource probe.

### Grant-bound native Hyprland capture transport

The production helper captured both 1280x800 VM outputs with exact magenta/cyan
test-pattern markers and changing 631/632 counters. Output 2 at scale 1.25 and
transform 1 yielded correctly oriented 800x1280 pixels with counter 633, also
visually inspected. Frame-ready waits measured 18–28 ms; shared admission,
request, metadata verification, decoding, and test image saving totaled
218–247 ms. Explicit `desktop_lock` then refused capture with `REVOKED`.
No input was sent. These are native transport measurements, not complete
platform acceptance.

### Hyprland Python shots and MCP delivery

The actual Python/native provider captured both outputs at long edge 640:
normal output 1 was 640x400, rotated/fractional output 2 was 400x640.
Shot centers mapped to canvas (640,400) and (1600,512). Focused-window
region capture retained counter 633. Calling `desktop_lock` after the first
publication link withdrew every new PNG and metadata file. A resident daemon
returned two decoded 640x400 MCP images with exact counter 871 and the
different output markers. The same probe passed replacement, expiry,
stale/dead presentation, CLI/MCP lock, and parent death; dead-frame lease
retirement measured 101 ms. No input was requested.

The combined probe initially failed at `window_list`: actual stable IDs
`1800001e`/`1800001f` contain hexadecimal letters. The tagged compositor
[formats stableId as hexadecimal](https://github.com/hyprwm/Hyprland/blob/v0.56.2/src/debug/HyprCtl.cpp).
Decimal-only validation had passed earlier IDs accidentally. Regression
tests now cover the actual IDs, retain bounded identity validation, and
reject malformed IDs. Full input/platform acceptance and existing-platform
regression remain required.

### Hyprland activation by compositor identity

`hyprctl -j status` on the installed 0.56.2 session reported
`configProvider: lua`. The tagged implementation dispatches differently for
Lua and hyprlang providers, so version alone cannot choose the syntax.
The adapter queries that runtime field and sends only a generated exact
identity selector. Actual resident MCP `window_focus` changed focus between
the two test outputs/workspaces in 149–177 ms in the first combined run.
Equal test-window titles returned `ELEMENT_AMBIGUOUS` with unchanged focus.
The ordinary `computer_batch` focus path also passed without opening input
devices. A test foot client moved to `special:pcbridge-focus` was focused
from another window, and a real xterm client had `xwayland: true` and gained
the exact requested focus. Existing provider contracts cover the hyprlang
argument vector; that config provider has not yet been exercised in this VM.
One probe initially assumed a focused baseline window and failed because a
previous test had closed it; the probe now selects a known mapped VM window.
Full input/platform acceptance remains pending.

## Terminal UI (settings CLI)

Measured 2026-09-24 on the reference machine, gnome-terminal 130x40, the UI
started from the checkout, driven with pcbridge's own pointer and keyboard.

- **Mouse clicks work in gnome-terminal.** Tabs, the section list, table
  rows, buttons and the search box all took virtual-pointer clicks; Textual
  reads the terminal's mouse reports, nothing else is needed. Tooltips show
  on hover.
- **The first screen is ready in 0.27 s; the status and the tool list fill
  in by 1.0 s.** `import pcbridge.tools` alone costs 0.55 s (it pulls in
  FastMCP), so the tool count and the catalog are built in worker threads,
  never on the screen's thread. `pcbridge --version` stays at 27 ms: nothing
  of the UI is imported unless the UI runs.
- **Building the tool catalog takes 0.65 s** (0.58 s imports, 0.07 s for the
  registration), so it is not cached.
- **Idle cost: 6.2 s of CPU over an 8 min 23 s session** (about 1.2 %), with
  the grant refreshed every second and the status every 10 s.
- **Quitting restores the terminal**: after `q` the shell prompt came back
  clean, with no leftover mouse reporting.
- **A one-key unlock is a hazard.** Typing `click` after clicking a tab sent
  `l` to the tab bar, not to the search box, and the `l` binding locked the
  grant. Locking by accident is the safe direction; unlocking by accident is
  not, so `l` asks before it unlocks (the button, a deliberate click, does
  not).
- **Raw typing on the `tr` layout turns `i` into `ı`**, so a raw-typed
  search for "click" matched nothing. Not a UI bug; the clipboard path
  (unavailable during this check) or words without `i` avoid it.
- **In the ANSI theme a disabled button gets `border: tall ... !important`**,
  which cuts a one-line compact button to its top border; the app's CSS
  resets it. A checkbox shows the same glyph either way and differs only by
  color, so the Tools filter is a button that names its state.
- **A sliding grant has two ends.** The bar showed `1:29 left` while the
  overview said `19:46`: `until` slides to the last action plus
  `unlock_idle_seconds`, `hard_until` is the ceiling. Both are shown now.

## MCP clients (connections)

Measured 2026-09-24 on the reference machine, first in a throwaway HOME,
then on the real clients (each switched off and on again, and checked with
the client's own listing).

- **Claude Code 2.1.276**: `claude mcp add/remove -s user`; `claude mcp get
  pcbridge` reports "Connected" after a reconnect.
- **Codex 0.155.1** honors `enabled = false` in `[mcp_servers.pcbridge]`:
  `codex mcp get pcbridge` prints "pcbridge (disabled)". Switching it off and
  on again left `config.toml` byte-identical, per-tool approval tables
  included.
- **Antigravity CLI (agy) 1.2.7** writes `~/.gemini/config/mcp_config.json`
  (not `~/.gemini/antigravity/mcp_config.json`, which is older) with a
  `disabled` flag; `agy mcp add NAME -- CMD ARGS` updates an entry in place,
  `agy mcp disable/enable` flip the flag.
- **Hermes Agent 0.20.6**: `hermes mcp add` connects to the server, lists its
  tools and asks "Enable all 37 tools? [Y/n/select]"; over an existing entry
  it first asks "Overwrite? [y/N]". An unanswered question prints
  "Cancelled." **with exit 0** and changes nothing, which is how a first
  version kept the old command; pcbridge now answers both and re-reads the
  file. Hermes rewrites `config.yaml` itself and appends its commented
  default blocks; nothing else changed. `hermes config path` names the
  active profile's file in 0.23 s.
- **OpenCode 1.18.29** reads `mcp.pcbridge = {type: "local", command: [...],
  enabled}` from `~/.config/opencode/opencode.json` (the shape of the SDK's
  `McpLocalConfig`): `opencode mcp list` showed "✓ pcbridge connected", and
  "○ pcbridge disabled" after `enabled: false`. `opencode mcp add` is
  interactive and there is no `remove`, so the file is edited.
- **Pi 0.85.0** has no MCP; the `pi-mcp-adapter` 2.32.1 extension reads
  `~/.pi/agent/mcp.json` (or `$PI_CODING_AGENT_DIR`) and skips an entry with
  `disabled: true`.
- **Pi 0.87.1 and pi-mcp-adapter 3.0.0 (2026-09-27)**: the installed adapter
  reads `~/.pi/agent/mcp-adapter.json` (or `$PI_CODING_AGENT_DIR`) and no longer
  loads `~/.pi/agent/mcp.json`. Its 3.0.0 changelog dates this config cutover
  to 2026-09-26. The real Pi config had a disabled pcbridge entry in
  `mcp-adapter.json` and no `mcp.json`; the old pcbridge connect created
  `mcp.json` while leaving the adapter entry disabled. The files were restored
  byte for byte after that reproduction. With the fix, `pcbridge connect pi`
  enabled the adapter entry, Pi opened in RPC mode without an extension error,
  and `/mcp-adapter reconnect pcbridge` reported 37 tools and 0 resources.
  The same files were restored byte for byte after the live test.
- **oh-my-pi 18.3.0** (`bun install -g @oh-my-pi/pi-coding-agent`, no model
  signed in) reads `~/.omp/agent/mcp.json`: `/mcp list` showed "pcbridge ●
  connected", `/mcp test pcbridge` "Tools: 37", and "◌ inactive" after
  `enabled: false`. Its own `/mcp enable`/`disable` write `enabled`
  true/false on that entry (and add `$schema`), which `pcbridge clients`
  reads back. The directory follows `PI_CODING_AGENT_DIR` (the variable Pi
  uses too) and `PI_CONFIG_DIR`. With the Claude Code source switched on
  (`enabledProviders: [claude]` in `config.yml`; the claude, codex and
  opencode sources are off by default), omp also starts the pcbridge entry
  of `~/.claude.json`: an entry of its own with `enabled: false` wins over
  it, and without one only `disabledServers` keeps it off ("Disabled
  (discovered servers)"). `disabledServers` always wins; `enabledServers`
  overrides `enabled: false`.
- **Three clients still ran the pre-2.0 command** (`.venv/bin/python -m
  pcbridge.server --stdio` from the checkout): agy, Hermes and Pi. `pcbridge
  clients` marks such an entry `outdated`; it still works through the
  relay's fallback, and `connect` updates it.

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
- **Arch (2026-09-23)**: Python 3.14, libpipewire 1.6.9, libclang in
  `/usr/lib`. A stream that disconnected itself inside its own process
  callback crashed libpipewire 1.6.9 right after the callback returned
  (SIGSEGV, every run); libpipewire 1.0.5 tolerated it. Python 3.14's venv
  has a `𝜋thon` alias that tar cannot store in a C locale, so packaging
  drops it. The package's venv is bound to the Python minor it was built
  with; `depends` pins it.
- **A checkout named `~/pcbridge` shadows the installed package** when a
  process starts in the home directory (the service does): `python -m`
  puts the working directory first. `-P` turns that off.
- **The contract suites read the desktop they ran on**: inside a Plasma
  session every GNOME expectation became a KDE one (16 failures). They pin
  `XDG_CURRENT_DESKTOP=GNOME`; the Plasma tests patch the detection.
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

### Shared desktop request cancellation on Hyprland

- The pinned FastMCP Client.call_tool wait can be canceled locally without
  sending notifications/cancelled. A VM diagnostic doing only task.cancel()
  left Shift held and delivered the later Shift+A sentinel. Source inspection
  confirmed that Client.cancel(request_id) is the explicit notification API;
  a canceled local wait is not a server cancellation signal.
- A corrected probe observes the actual request ID through read-only public
  FastMCP middleware, sends Client.cancel for that ID, and keeps client/server
  alive through the original batch deadline. Before the shared execution fix,
  the packaged release helper still held Shift throughout 4.501 seconds and
  sent the later sentinel. After the fix, the same actual request ID 3 was
  canceled, Shift released in 50 ms, no sentinel event appeared during 4.504
  seconds, and system_capabilities still responded. Normal desktop_lock and
  runtime cleanup left no helper, idle watcher, glow layer, or input observer.
- Shared cancellation checkpoints cover admission, action boundaries,
  successful sequence exit, batch/rate waits, and move-to-click settling.
  They do not touch the grant during a wait. Cleanup occurs while the sequence
  still owns its execution flock. Contracts verify that cancellation while
  waiting for ownership preserves the current owner's held input, replacement
  holds survive old cleanup, normal completed holds remain, and cancellation
  of a general shell tool does not release desktop input. Native operations
  are bounded atomic calls; this does not establish forced interruption or
  rollback of already dispatched input. These measurements use the isolated
  Arch/Hyprland VM, not the maintainer's GNOME session.

### Normal MCP sequence lifecycle on Hyprland

- With public sliding timeout 10 seconds and hold timeout 20 seconds, the
  normal MCP batch held Shift, waited 11.5 seconds, and attempted a sentinel.
  The native key release occurred 30.93 ms after the read-only actual lease
  deadline; the next action was refused, done=2/3, with no sentinel and no
  active grant/frame. Automatic owner cleanup retires the expired identity,
  so the observed structured code was REVOKED rather than GRANT_EXPIRED.
- Explicit desktop_lock during the wait released Shift in 57.89 ms and
  stopped the next action. Normal replacement desktop_unlock released the
  old Shift in 50.79 ms, produced a distinct grant identity, and stopped the
  old batch with REVOKED. A Ctrl hold request queued while the old batch
  remained pending started after old cleanup, remained held for the bounded
  observation, and released only on its own normal keyboard release call. The replacement
  grant stayed active and visible; no old sentinel arrived.
- A real native unknown-key validation failure after hold plus a short
  100 ms wait released Shift in 319 ms from observed key down. The MCP error
  result arrived at 327 ms; these observations do not identify the exact
  internal exception timestamp. The batch completed 2/4 actions, sent no
  sentinel, and the same grant then sent an observed lowercase b press and
  release with Shift false. The current native error mapping reports
  INVALID_FRAME with the actual unknown-key message.
- The expanded probe reran genuine MCP cancellation (observed request ID 4):
  release in 51.39 ms, no later sentinel, and a responsive server. All acting
  tests used normal MCP calls, fresh native idle and a presented grant frame,
  current fullscreen observer identity, and default packaged release helper
  discovery. system_capabilities reported linux.uinput.native for keyboard.
- Shared revoked-sequence text now reports generic closure. Automatic expiry
  cleanup and explicit manual revoke can both retire an identity; the cause
  must not be attributed to a particular manual call without evidence. Error
  codes, retryability, and safety semantics are unchanged.

### Grant cleanup reporting on Hyprland

- Normal MCP initialization now tells agents to inspect actual registered
  runtime bindings and the active submap before compositor shortcuts. A
  packaged release VM run verified those instructions without input.
- In that run, normal unlock presented eight glow strips and opened capture;
  normal desktop_lock changed capture open true to false and removed all
  strips. The response was `Desktop control closed.` without a GNOME sharing
  indicator claim. ResourceWatch had already closed capture during frame
  shutdown, before the tool sampled capture.is_open; the additional capture
  note is conditional. Registered-tool contracts cover local-open, other-
  helper, and already-closed states separately for all four desktop kinds.
- Independent final VM state was known unlocked, zero native helpers/glow,
  and no idle writer. This reporting measurement does not establish the
  remaining platform acceptance or existing-desktop regression.

### Real MCP screen-lock and activity transitions on Hyprland

- On September 29, normal MCP held Shift during a three-second wait while
  actual hyprlock presented both output lock surfaces. A separate read-only
  kernel event observer saw KEY_LEFTSHIFT up 94.90 ms after locker launch
  and the device disappear at 107.99 ms. The configured held-input timeout
  was 20 seconds. No A sentinel reached the kernel or fullscreen GTK app.
  Locked keyboard and desktop_unlock returned SCREEN_LOCKED; the batch
  finished 2/3, stopped=safety, SCREEN_LOCKED.
- OS screen lock pauses the existing grant, rather than automatically
  revoking it. Its exact identity stayed active; eight layer surfaces
  remained, but native frame ready was false, presentation time zero, and
  trusted current-output frame health unavailable. Native input and capture
  resources were closed. After normal VM-only SIGUSR1 locker cleanup, a
  presentation timestamp newer than the authoritative unlock observation
  restored health for the same frame owner and grant. A new input helper
  delivered b press/release with Shift false; a subsequent normal replacement
  grant delivered c with Shift false. Old sequence actions never resumed.
- With a two-second test idle guard, injected b reset actual native idle to
  0 ms. A subsequent unforced c returned USER_ACTIVE without any event;
  force=true delivered c. After actual idle reached 2045 ms, an unforced
  d/wait/e batch was admitted. During its one-second wait the native observer
  reported 0 ms while the batch was pending; d and e both reached GTK without
  reapplying the user-activity admission guard to its own input.
- Terminating only the probe's native idle writer made idle UNKNOWN. Both
  forced keyboard f and desktop_unlock returned ACTIVITY_UNKNOWN and sent
  no f. A freshly started watcher published known state; a distinct normal
  grant then delivered f with Shift false. Both cases used default packaged
  release discovery, test_harness=false, and public MCP/SafetyGate paths.
  Normal desktop_lock plus independent process/layer checks left the VM
  known unlocked with no frame, native helper, idle writer, or observer.
  These scoped measurements do not complete platform acceptance.

### Real Wayland pointer lock on Hyprland

- On September 29, two fresh disposable VM runs used a test-only Wayland
  client and the normal MCP mouse tools with the packaged release helper.
  Both received an actual pointer-constraint `locked` callback. Requested
  unaccelerated deltas `(3,0)`, `(40,0)`, `(80,0)`, `(-40,0)`, `(0,50)`, and
  `(0,-50)` all arrived exactly; the five non-warmup ratios were 1.0.
  Across each complete 24-event locked interval, the client received no
  absolute pointer motion, leave, or unlocked callback. It did receive left
  button down/up and vertical scroll `-15`. After it destroyed the constraint,
  a requested absolute target `(754,484)` yielded client motion `(753,484)`.
- Both fresh runs kept read-only `hyprctl cursorpos` at `(671,423)` during the
  lock. Native pointer-included screenshots in the first run showed unchanged
  cursor bounds `(666,413)-(674,432)`. An earlier uncommitted rollback draft
  reported intermittent `cursorpos` Y drift; these runs did not reproduce it.
  The protocol checks above rely on actual client callbacks and do not imply
  that compositor IPC cursor coordinates must stay fixed.
- The receiver compiled without warnings using Wayland client 1.26.0 and
  protocol package 1.49-1. Its process exited 0 and reader stopped in both
  runs. Normal grant closure and independent VM checks left known unlocked
  state with no grant layer, idle proof, native helper, or observer.

### Real touch through the Hyprland glow

- On September 29, a temporary VM-only direct uinput touchscreen created a
  real `wl_touch` capability. A fullscreen Wayland client on the first
  1280x800 output received `(640,400)` before the grant. With all eight glow
  layers visible, touch down/up reached that same client at the exact top
  `(640,5)`, bottom `(640,794)`, left `(5,400)`, and right `(1274,400)`
  points. Every sequence had matching IDs and no cancellation. After normal
  grant closure, `(640,400)` reached the client again. The first run and the
  strengthened final run both exited 0; the final native screenshot showed
  the actual client and white glow.
- The final fixture's receiver exited 0; its reader stopped, temporary touch
  device closed, and native idle writer stopped. An independent VM query found
  known unlocked state and no grant layer, idle proof, native helper,
  observer, or remaining `pcbridge-vm-touch-*` device. This proves first-output
  touch transparency, not second-output mapping or full platform acceptance.

### Clipboard change during native typing on Hyprland

- On September 29, a controlled VM probe confirmed the old sequence could
  overwrite a separate clipboard owner's different value: the new value was
  visible before `restore`, then the saved value replaced it. The shared
  typing path now re-reads the current first MIME and bytes after paste and
  restores only when recognized temporary text is still present.
- A real guarded Hyprland input fixture used the packaged native helper and
  the temporary GTK observer. It first verified ordinary exact typing and
  restoration of a controlled sentinel. In a second paste, another VM
  `wl-copy` wrote a different value after GTK received the text and before
  PcBridge's restore step. The helper left that value in place, and fixture
  cleanup restored the pre-test clipboard bytes. Two VM runs passed, with
  independent final checks finding no grant layer, native helper, idle
  writer, or observer.
- This is a content check, not atomic ownership proof. An identical text
  replacement or a clipboard change after the re-read can still race the
  restore. The first-MIME-only restore limit also remains.

### Touch mapped to Hyprland's second output

- On September 29, two fresh VM runs bound a temporary direct touchscreen to
  Virtual-2 with `hl.device`, selected the matching `wl_output` in the
  Wayland fullscreen request, and observed client geometry `(1280,0)
  1280x800`. Before the grant, during all four native glow edges, and after
  the grant, six touches reached that client at the requested local
  coordinates, each with a matching down/up ID and no cancellation. The
  screenshot showed the client beneath the visible glow; the receiver
  compiled without warnings, exited 0, and stopped its reader. The mapped
  device was reset to `[[Auto]]` before it was closed.
- The first-output touch fixture and pointer-lock fixture passed after the
  Wayland receiver change. Independent cleanup found no temporary touchscreen
  or idle proof; the global touch output option remained unset. The failed
  early cursor-selection and seat-listener revisions were test-only. The
  measurement is limited to two scale-1 1280x800 VM outputs and does not
  establish arbitrary touchscreen mappings or general Hyprland support.

### Hyprland AT-SPI parity and target policy

- On September 29, the disposable VM's libatspi named numeric role 43
  `button`, while the native reader's stable role table named it `push button`.
  The two readers otherwise reported the same object paths, tree paths,
  actions, and states for the controlled GTK window. Normalizing the Python
  name to `push button` made short IDs and full node records match. The VM's
  GTK window exposed two body close buttons without a header close button;
  the earlier live fixture required three and failed before this correction.
- The guarded VM parity suite passed 20 tests with one GNOME-only timing
  skip. Python and native reads matched repeatedly, including after the
  tree shifted. Both action paths verified moved/stale identity, disabled
  controls, ordinary and truncated text, and password refusal. The native
  helper's action case found no uinput descriptor.
- A separate normal MCP run with the packaged release helper reported
  `GRANT_REQUIRED` before unlock, then `linux.atspi.native` under eight
  visible glow layers. A forced password write returned `PASSWORD_FIELD`
  without a password event. Ordinary text reached the intended field;
  unconfirmed `alt+F4` returned `CONFIRMATION_REQUIRED` and left the window
  open. Normal lock and independent cleanup found no frame, idle proof, or
  test helper process. This is scoped policy/accessibility evidence, not
  full Hyprland acceptance.

### Normal MCP capture through a fractional rotated output change

- On September 29, two guarded VM runs used the packaged release helper and
  normal `desktop_unlock`/`screen_capture` tools while a controlled pattern
  filled Virtual-1 and Virtual-2. Both initial image blocks were 1280x800
  with the correct output markers and fresh counter 741. After changing
  Virtual-2 to scale 1.25 and transform 1, its reported logical span was
  `(1280,0) 640x1024` and the delivered image was 800x1280. Both outputs
  showed counter 742. The rotated image was visually inspected.
- An MCP mouse move using a shot ID from before the change returned a
  screen-layout-changed error. Compositor cursor coordinates were unchanged
  before and after that refusal. The grant frame withdrew resource health
  while the output changed, then new presentation proof allowed fresh
  capture. Both runs restored the exact original monitor table, closed the
  grant and test processes, and left no frame or idle proof. Other layouts
  and full platform acceptance remain open.

### Hyprland native accessibility batch policy

- On September 30, two guarded normal MCP VM passes used a controlled GTK
  window and the packaged release helper. A `computer_batch` containing
  `ui_click` followed by unconfirmed `alt+F4` returned
  `CONFIRMATION_REQUIRED` before the button received any click. A separate
  unconfirmed keyboard call also returned `CONFIRMATION_REQUIRED` without
  closing the window.
- A five-action batch clicked the same button twice with 500 ms waits and
  requested a third click. It reported four actions done and stopped before
  the third; the GTK window emitted exactly two `ok` signals. At 100 ms
  spacing in an earlier diagnostic, two successful AT-SPI replies produced
  one GTK signal, while a separate direct click produced another. The
  fixture uses 500 ms waits to observe both allowed activations. Normal
  grant teardown and an independent VM check found no frame, idle proof,
  native helper, or test window left behind. Other batch policies and full
  Hyprland acceptance remain open.

### Negative Hyprland compositor origin through normal MCP

- On September 30, two guarded VM runs moved Virtual-1 to `(-1280,0)` and
  Virtual-2 to `(0,0)` while retaining their left-to-right order. The shared
  monitor table mapped them to canvas offsets `(0,0)` and `(1280,0)` and
  retained the negative compositor coordinate separately. The normal MCP
  capture returned two 1280x800 images with the correct magenta/cyan output
  markers and a fresh counter change from 741 to 742.
- Normal MCP `mouse` moved to pixel `(300,300)` in the fresh Virtual-1 shot;
  `hyprctl cursorpos` independently reported `(-980,300)`. Both runs restored
  the exact original monitor rules and closed the grant and test processes.
  The prior fractional rotated case passed again with the expanded fixture.
  This measures a translated horizontal layout, not output swaps, vertical
  placement, hotplug, or mirrors.

### Identical Hyprland outputs swapped after a screenshot

- On September 30, an isolated contract reproduced a stale-shot gap. Two
  same-size outputs with Hyprland's focus-based primary selection had the
  same geometry topology ID before and after swapping positions. The old
  shot coordinate was accepted because output identity was not checked.
- Shot metadata now saves the monitor serial. Coordinate and region
  conversion compare the captured output with the output currently at that
  image position. A unique serial survives connector renames; a missing or
  duplicate serial falls back to connector identity. The geometry topology
  ID and native protocol are unchanged.
- Two guarded VM runs swapped Virtual-1 and Virtual-2 under a visible grant.
  An old Virtual-2 shot was refused with the compositor cursor unchanged.
  Fresh 1280x800 captures placed cyan Virtual-2 at canvas `(0,0)` and magenta
  Virtual-1 at `(1280,0)`, each with counter 742 after counter 741 before the
  swap. Both runs restored monitor rules and closed the grant and test
  processes. The rotated and negative-origin layouts passed again with the
  new shot check. Vertical, hotplug, and mirror layouts remain unmeasured.

### Vertically stacked Hyprland outputs through normal MCP

- On September 30, two guarded VM runs moved Virtual-2 below Virtual-1.
  Normal MCP capture returned the correct 1280x800 images at canvas `(0,0)`
  and `(0,800)`, with the magenta/cyan output markers and counter 742 after
  counter 741 before the change. The old Virtual-2 shot was refused without
  compositor cursor movement.
- A normal MCP move from pixel `(300,300)` in the fresh Virtual-2 shot put
  the pointer at `(300,1100)` according to `hyprctl cursorpos`. Both runs
  restored output rules and closed the grant and test processes. The swapped
  output case passed again. Hotplug and mirror behavior remain open.

### Simulated Hyprland output removal and return

- On September 30, a guarded VM fixture disabled Virtual-2 with a temporary
  compositor rule. The packaged release helper and normal MCP capture
  returned one decoded 1280x800 Virtual-1 image while it was disabled and
  two images after it was restored. An old Virtual-2 shot was refused with
  no compositor cursor movement. The frame resource withdrew during each
  layout change and recovered with current output proof.
- The first diagnostic run exposed a VM fixture cleanup mistake: a monitor
  rule with mode and position did not clear the disabled flag. Explicit
  `disabled = false` restored the VM; the test helper now sends that field.
  Three subsequent runs and an independent cleanup check found both outputs
  enabled at their original positions and no frame, idle proof, native
  helper, or pattern window. This is compositor-rule removal, not a physical
  connector unplug. The image content during removal and return was decoded
  and size-checked; pattern markers and counter were checked only before
  the change because GTK can move fullscreen windows when an output leaves.

### Hyprland mirror visibility differs between output queries

- On September 30, two guarded VM probes temporarily mirrored Virtual-2 to
  Virtual-1 without a grant or input. `hyprctl -j monitors all` included
  Virtual-2 with `mirrorOf: "0"`, while the active-monitor query used by
  PcBridge included only Virtual-1. The Python canvas likewise exposed only
  Virtual-1. The isolated contract that rejects an explicit mirror row does
  not describe this live transport path.
- Both probes restored the empty mirror rule and original monitor positions.
  Normal MCP capture, grant frame presentation, and input in mirror mode
  remain unmeasured, so this does not establish mirror support.

### Normal MCP capture under a mirrored VM output

- On September 30, three guarded VM runs opened the normal desktop grant
  before mirroring Virtual-2 to Virtual-1. The active monitor table then
  contained only Virtual-1; frame proof rebuilt for that output. An old
  Virtual-2 shot was refused with no compositor cursor movement. Normal
  MCP capture returned one decoded 1280x800 Virtual-1 image.
- Clearing the mirror rule restored two active outputs, current frame proof,
  and two decoded 1280x800 images with the expected connector labels. All
  runs restored the original monitor rules and closed the grant and test
  processes. The physical visibility of the grant frame on the mirrored
  follower and a successful source-shot input effect remain unmeasured.

### Native Hyprland grant frame pixels on two outputs

- On September 30, the guarded Rust renderer fixture presented the white
  glow on both 1280x800 VM outputs. At four outer edges of each output, a
  `(30,30,40)` background became `(124,124,130)`. Seven depths through the
  68-pixel edge matched the expected falloff within five channel values.
  The saved Virtual-1 image was visually inspected. Focus and fullscreen
  window geometry were unchanged; eight glow layers appeared during the
  fixture and none remained after shutdown.
- The fixture observed breathing and fade-out and exited 0 despite two Mesa
  EGL DRI2 warnings. An independent check found the original monitor layout,
  unlocked screen, and no frame, idle proof, native helper, or test window.
  This is a debug renderer fixture measured with `grim`, not normal MCP
  capture performance or physical mirrored-output visibility.

### Hyprland two-output MCP capture latency

- On September 30, two guarded VM runs used the packaged release helper
  through the normal FastMCP `screen_capture` tool with a visible grant,
  two 1280x800 outputs, `monitor=all`, `scale=0`, and no pointer. After two
  warmups, each timed 20 in-process tool-call round trips. Run one had a
  217.562 ms median, 249.115 ms nearest-rank 95th percentile, and
  205.790–264.001 ms range. Run two had a 219.349 ms median, 245.674 ms
  95th percentile, and 206.077–263.115 ms range.
- Both runs decoded the two image blocks from every response, verified
  output markers and a counter change midway, and saw 44 unique shot IDs
  across 22 calls. Both closed the grant and test processes. Independent
  inspection found the original layout and no frame, idle proof, helper, or
  pattern window. Timing excludes client image decoding and any external
  stdio relay or network. It is a static VM baseline without concurrent
  load or an established product latency target.

### Source-shot pointer input while a VM output mirrors it

- On September 30, two guarded VM runs captured Virtual-1 while Virtual-2
  mirrored it. Normal MCP `mouse` used the fresh Virtual-1 shot to move the
  pointer to `(300,300)`; independent `hyprctl cursorpos` agreed in both
  runs. An old Virtual-2 shot was refused without pointer movement, and
  two-output capture recovered after mirroring ended. The hotplug regression
  and independent cleanup check passed.
- The source canvas accepts screenshot-relative pointer movement in this
  configuration. At this stage, visibility on the follower's separate
  virtual display and a physical mirrored connector were unmeasured; the
  subsequent QMP check below covers the virtual display.

### Grant frame pixels on both mirrored VM displays

- On September 30, two paired QMP runs captured the two distinct 1280x800
  virtual GPU heads while Virtual-2 mirrored Virtual-1. The bottom-center
  pixel on each head was `(245,130,48)` after `desktop_lock` and
  `(249,182,135)` while the grant was active. At 4 and 19 pixels inward,
  the active values were `(248,167,109)` and `(246,143,70)`; at 99 pixels
  inward, the pixel was unchanged. At the left edge 70% down, the closed
  `(230,25,75)` became `(240,122,151)`; the falloff continued to
  `(230,26,76)` at 53 pixels inward. Both devices had identical samples.
  The granted images were visually inspected and showed the border on both.
- A long first diagnostic exposed a test restore assumption: the monitor
  table briefly reported the returned outputs at reversed positions. The
  fixture now reapplies both original rules and waits for exact geometry.
  The strengthened paired run, default mirror and hotplug regressions, and
  independent cleanup check passed. This is direct virtual GPU display
  evidence, not a physical connector measurement.
