# Windows port: the working plan

The step-by-step plan for the rest of ROADMAP stage 8. What each tool does on
Windows, the known limits and the security model are in
[windows-scope.md](windows-scope.md); the numbers behind every decision are in
the Windows sections of [measured-facts.md](measured-facts.md). This file is
the order of work and the rules for doing it. Strike a step through here when
its commit lands; do not copy measurements into this file.

## Where it stands

W0 to W7 are on `main` (W6c-W7: 2026-11-16). On Windows the
daemon, the supervisor, the local IPC, jobs, shell, agents, files, the system
tools, the live terminal (`tmux_*` through ConPTY, `pcbridge attach`), remote
access and the login check work. The native helper builds on Windows, answers
`display.snapshot` and `capture.frame`, and checks the grant
(`rust/crates/pcbridge-native/src/dispatch_windows.rs`,
`src/platform/windows/`), and since W6c types, clicks, scrolls and drags
through `SendInput` and reads and writes the clipboard, under the grant with
a 100 ms watchdog; since W6d it reads and acts on the screen through UI
Automation (the shared walk of `src/accessibility/` over a UIA `Tree`),
lists and activates windows, and refuses elevated targets with
`TARGET_ELEVATED`; since W6e it reads the lock from two signals, has
`idle-watch` (the user's input apart from pcbridge's) and `glow-watch`
(the frame and the tray icon with "Lock now"). Since W7 the daemon
drives all of it through the MCP desktop tools, measured through a real
client in the VM. Install and upgrade (W8) are next.

## Rules for this work

- Linux behavior does not change. Every commit keeps the Linux suites green
  (CLAUDE.md, *Commands*), plus `cargo fmt`, `clippy` and `cargo test`.
- **Desktop input is tested only inside the VM**, never on the host desktop.
  First an empty Notepad, move, verify with a screenshot, then click.
- Never print the VM password (`~/.local/share/pcbridge-win-dev/password`).
- Chain `git commit` to the guard tests with `&&`, never `;` (W4 was pushed
  once with a failing English guard).
- Every stage ends in a commit and a push to `main`, after the secret scan.
  Record new numbers in `measured-facts.md`; add each module that passes on
  Windows to `tests/windows_portable.txt`.
- Keep the VM lean: no Visual Studio Build Tools. Rust in the VM uses the
  `x86_64-pc-windows-gnu` toolchain; release builds use MSVC in CI. The
  `windows` crate stays at 0.58 (0.59+ needs MinGW's `dlltool` and `as`).
- `unsafe` stays forbidden on Linux; on Windows it is denied except in
  `platform::windows`, each block with a `SAFETY:` comment.
- Stop to ask the maintainer only for something critical or irreversible.
  Theirs to decide: the code-signing certificate and the release version.

## The VM

`scripts/dev/windows-vm.sh` (run it without arguments for the commands).
The usual loop:

```bash
scripts/dev/windows-vm.sh start
scripts/dev/windows-vm.sh sync                     # this checkout -> %USERPROFILE%\pcbridge
scripts/dev/windows-vm.sh ssh "cd pcbridge\rust && cargo build --release -p pcbridge-native"
scripts/dev/windows-vm.sh session "python ...\probe.py"   # in the desktop session, unelevated
scripts/dev/windows-vm.sh screenshot               # <dir>/screen.png
```

Known traps:

- SSH logons are elevated and have no desktop. Anything that needs the
  desktop (input, capture, UIA, the foreground) runs through `session`.
  `session` writes its command file in ASCII: keep non-ASCII out of its
  arguments.
- The checkout is named `pcbridge`, which shadows the package for
  `python -m`: use `pcbridge.exe` or `python -P`.
- `sync` uses robocopy because a running daemon locks files; it keeps
  `.venv`, `target` and `_native`.
- ctypes calls need `argtypes` for handles, or they fail silently on 64-bit.
- On the lock screen QEMU's keyboard drops keys at `type`'s default pace;
  `PCBRIDGE_WIN_TYPE_DELAY=0.25 windows-vm.sh type ...` types a password.
  Dismiss the curtain with a key that types nothing (`key ctrl`): an Enter
  on a visible password field submits it empty.
- GitHub's Windows runners use 8.3 temp paths (`RUNNER~1`): compare resolved
  paths in tests.

## Folder layout: done step by step

The maintainer asked (2026-11-16) to separate the shared, platform-neutral
code from the code for one operating system, distribution or desktop. It is
done where the Windows code is about to land, never as one big move:

- Each move is its own commit, "move only, behavior unchanged", with every
  suite green; the new Windows code comes in the next commit.
- Paths that installs and upgrades depend on (`install.sh`, wheel package
  data, `gnome-extension/`, `cinnamon-applet/`, `systemd/`) move last, if at
  all, and only after the install and upgrade paths are updated and tested.

Target shape:

```
rust/crates/pcbridge-native/src/
  input/              shared: key table, keyboard/pointer/relative services, timers
  platform/linux/     evdev/uinput, PipeWire, AT-SPI (later gnome/ kde/ hyprland/ x11/)
  platform/windows/   SendInput, DXGI, UI Automation, the frame window

pcbridge/desktop/                       shared: batch, policy, safety, coordinates
pcbridge/desktop/platforms/linux/...    gnome, kde, hyprland, x11
pcbridge/desktop/platforms/windows/
```

## Remaining stages

### ~~W6c: input and the clipboard (native helper)~~ (done: `fda7c2f` move, then the Windows devices)

1. **Move only.** Take the device-neutral part of
   `platform/linux/input.rs`, `input/pointer.rs` and `input/relative.rs` into
   a shared `src/input/` module. `Keyboard<D, C>` is already generic over its
   device; what ties it to Linux is `evdev::KeyCode`. Replace it with a
   neutral key type (the key table maps names and aliases to it); the evdev
   device maps that type to `KeyCode`. The same for pointer buttons. Combos
   (30 ms pause), held keys, the 120 s auto-release, fail-closed recovery,
   smooth paths and the 60 ms click hold move unchanged, with their tests
   (fake devices), which then also run on Windows. Linux suites and
   `cargo test` must pass unchanged.
2. **Windows devices.** `platform/windows/input.rs`: keyboard by scan code
   (`KEYEVENTF_SCANCODE`, extended keys flagged) and `KEYEVENTF_UNICODE` for
   text; pointer by `MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK` over the
   virtual screen; wheel and relative moves. Mark every event with pcbridge's
   `dwExtraInfo` value, so W6e's hooks can tell its own input from the user's.
3. **Clipboard.** `clipboard.read`, `write`, `clear` with `CF_UNICODETEXT`,
   under the grant like the Linux methods (docs/native/protocol-v1.md).
4. **Dispatch.** Wire `input.keyboard.*`, `input.pointer.*`,
   `input.relative.*` and `clipboard.*` in `dispatch_windows.rs`; the
   capabilities answer says they are supported. Revoke releases held keys
   and buttons.
5. **Check in the VM.** Through `NativeClient` (extend
   `scripts/dev/windows/probes/native_probe.py`): type into Notepad and read
   it back, click, drag, scroll, a held key auto-released, the release time
   after revoke (target: as on Linux, at most 100 ms). Record the numbers.

### ~~W6d: UI Automation and windows~~ (done: `36dde54` and `174a234` move, then UIA)

- `ui.dump`, `ui.act`, `ui.set_text` with UIA: `RuntimeId` as the identity,
  Invoke / Toggle / SelectionItem for actions, `ValuePattern` for text with
  read-back, `IsPassword` reported for the policy.
- `window.list`, `window.focused`, `window.activate`: top-level windows;
  activation injects `VK_F24` first, then `SetForegroundWindow` (measured).
- Refuse an elevated target with `TARGET_ELEVATED` (check its integrity
  level): UIPI drops input to it silently.

### ~~W6e: lock, idle and the indicator~~ (done: `97fa3c8` move, then the Windows parts)

- Lock state from WTS session flags with `OpenInputDesktop` as the second
  signal, tri-state; the gate refuses while locked or unknown.
- User activity from low-level keyboard and mouse hooks that ignore injected
  input carrying pcbridge's mark; the `idle-watch` command writes the same
  JSON as on Linux.
- The grant frame: a click-through, always-on-top layered window; the
  `glow-watch` command as on Linux; `FrameVisibilityProvider` confirms it is
  shown. A tray icon with "Lock now".

### W7: the Python desktop wiring (done: `ca10f8e` move, then the Windows wiring)

- ~~Move first (layout above), then add Windows: `session.WINDOWS`, the
  compositor descriptor, the backend names in `rust.py`,
  `monitors._from_native`, `SafetyGate` probes reading the native state.~~
  The Linux desktops' modules are in `pcbridge/desktop/platforms/linux/`;
  `x11native` became the shared `desktop/nativewin.py` (the helper answers
  the same window questions on X11 and Windows).
- ~~`grantnotice` as a toast with "Lock now"; `panel_icon` for the tray
  icon.~~ The toast is a one-off; "Lock now" is in the tray icon's menu,
  which `glow-watch` owns with the frame.
- ~~`apps.py` and `window_focus`: Start menu applications and
  `shell:AppsFolder\<AppID>`.~~ (`Get-StartApps` lists shortcuts and packaged
  apps alike.)
- ~~OCR: tesseract's Windows location (winget `UB-Mannheim.TesseractOCR`).~~
- ~~`policy.check_text_target` from UIA `IsPassword`~~ (a password `Edit` is
  the role "password text", which the policy already refuses).
- ~~`skills/computer-use/SKILL.md`: Windows shortcuts and apps.~~ `pcb-do`
  is a console script, so Windows has `pcb-do.exe`.
- ~~Live tests under `tests/live/windows/`, run only in the VM, through a real
  MCP client: the desktop tools, the kill switch, `USER_ACTIVE`.~~
- Left: `find_text`/`wait_for_text` with tesseract measured end to end in
  the VM (installed there, not yet run through MCP).

### W8: install and upgrade (done, except the uninstall and upgrade runs: W10)

- ~~`install.ps1` (`irm ... | iex`): finds Python or fetches it with uv,
  checks the wheel against `sha256sums.txt`, installs into the user venv
  (`%LOCALAPPDATA%\pcbridge\data\venv`, where `paths.data_home()` puts it,
  so `install_kind()` says "user"), runs `pcbridge setup`. The English guard
  also scans `*.ps1`.~~
- ~~`pcbridge setup` on Windows: the OCR component through winget, client
  connections, the Run-key entry, PATH through `HKCU\Environment`,
  `ROLLBACK.md` in PowerShell.~~ Tailscale is not a setup component on
  Windows either: `pcbridge remote start` says where to get it.
- ~~The `win_amd64` wheel tag and the Windows classifier; `_native/*/
  pcbridge-native.exe` as package data; `upgrade.py` picks the Windows wheel;
  `native/fetch.py` handles `.exe`.~~
- ~~`uninstall` removes the Run key, the PATH entry and the files, through
  the Recycle Bin~~ (written; the run is part of W10).

### W9: CI, documents, release (done; the workflows run on the next push and tag)

- ~~CI: the Windows job runs the portable suites on Python 3.12 to 3.14;
  `native.yml` builds, lints and tests with MSVC.~~ `package.yml` builds the
  MSVC release helper and the Windows wheel on a Windows runner, and installs
  the wheel with `install.ps1`.
- ~~`release.yml` adds the Windows wheel, `pcbridge-native-x86_64-pc-windows-msvc.exe`
  and `install.ps1`, all in `sha256sums.txt`~~; the release notes' install
  block has the PowerShell line.
- ~~Documents: README, install, usage, architecture, a Windows section in
  security.md, native packaging, CLAUDE.md, ROADMAP. CHANGELOG with the
  `changelog` skill~~ (under "Unreleased": the version is the maintainer's).

### W10: end to end in a fresh VM (started, paused 2026-11-16)

Where it stopped: a `fresh` overlay (`test.qcow2`) was made and booted, and
`install.ps1 -Uninstall -Purge -Yes` ran there: it removed the PATH entry and
moved config, state and the venv to the Recycle Bin. Not done yet: checking
that the Run key is gone, installing from the local wheel
(`dist\pcbridge-2.10.3-py3-none-win_amd64.whl`, no release exists yet), and
the runs below. Since no Windows release exists, "from the release" means the
locally built wheel and `install.ps1 -Wheel`; the upgrade from an earlier
Windows build is an install over the existing one until a release exists.

CI is red on 109f347 and must be fixed first:

- Windows portable job (3.12 to 3.14): `test_panel_icon`, `test_tool_surface`
  and `test_mcp_capture_delivery` fail cleaning up their temporary folder
  (WinError 32 on `state\oauth.db`: the SQLite handle is still open);
  `test_batch_safety.test_a_killed_holder_leaves_no_stale_lock` is refused
  with `SequenceRefused` (the lock of the killed child is still seen).
- `package.yml` deb jobs: `test_oscompat.test_terminate_group_ends_the_child_and_its_children`
  sees the grandchild still alive in the container (a zombie counts as alive
  there); the arch job failed in the same suite run.
- Headless KWin smoke (experimental): `CaptureWorkspace` got no D-Bus reply.

- A `fresh` disk, install from the release with `install.ps1`.
- All 40 tools from a client inside the VM (`tests/readiness/check.py
  --desktop --http`), `test_e2e.py` with `PCBRIDGE_TEST_NO_AGENT=1`, an
  upgrade from the previous Windows build.
- Left for the maintainer: connecting claude.ai through the tunnel, and
  publishing the release.
