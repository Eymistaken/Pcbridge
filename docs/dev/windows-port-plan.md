# Windows port: the working plan

The step-by-step plan for the rest of ROADMAP stage 8. What each tool does on
Windows, the known limits and the security model are in
[windows-scope.md](windows-scope.md); the numbers behind every decision are in
the Windows sections of [measured-facts.md](measured-facts.md). This file is
the order of work and the rules for doing it. Strike a step through here when
its commit lands; do not copy measurements into this file.

## Where it stands

W0 to W5 and W6a-c are on `main` (W6c: 2026-11-16). On Windows the
daemon, the supervisor, the local IPC, jobs, shell, agents, files, the system
tools, the live terminal (`tmux_*` through ConPTY, `pcbridge attach`), remote
access and the login check work. The native helper builds on Windows, answers
`display.snapshot` and `capture.frame`, and checks the grant
(`rust/crates/pcbridge-native/src/dispatch_windows.rs`,
`src/platform/windows/`), and since W6c types, clicks, scrolls and drags
through `SendInput` and reads and writes the clipboard, under the grant with
a 100 ms watchdog. UI Automation, windows, lock and idle state, and the
frame are next.

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

### W6d: UI Automation and windows

- `ui.dump`, `ui.act`, `ui.set_text` with UIA: `RuntimeId` as the identity,
  Invoke / Toggle / SelectionItem for actions, `ValuePattern` for text with
  read-back, `IsPassword` reported for the policy.
- `window.list`, `window.focused`, `window.activate`: top-level windows;
  activation injects `VK_F24` first, then `SetForegroundWindow` (measured).
- Refuse an elevated target with `TARGET_ELEVATED` (check its integrity
  level): UIPI drops input to it silently.

### W6e: lock, idle and the indicator

- Lock state from WTS session flags with `OpenInputDesktop` as the second
  signal, tri-state; the gate refuses while locked or unknown.
- User activity from low-level keyboard and mouse hooks that ignore injected
  input carrying pcbridge's mark; the `idle-watch` command writes the same
  JSON as on Linux.
- The grant frame: a click-through, always-on-top layered window; the
  `glow-watch` command as on Linux; `FrameVisibilityProvider` confirms it is
  shown. A tray icon with "Lock now".

### W7: the Python desktop wiring

- Move first (layout above), then add Windows: `session.WINDOWS`, the
  compositor descriptor, the backend names in `rust.py`,
  `monitors._from_native`, `SafetyGate` probes reading the native state.
- `grantnotice` as a toast with "Lock now"; `panel_icon` for the tray icon.
- `apps.py` and `window_focus`: Start menu shortcuts and
  `shell:AppsFolder\<AUMID>`, the blocklist matched against them.
- OCR: tesseract's Windows location (winget `UB-Mannheim.TesseractOCR`).
- `policy.check_text_target` from UIA `IsPassword`.
- `skills/computer-use/SKILL.md` and `bin/pcb-do`: Windows shortcuts and apps.
- Live tests under `tests/live/windows/`, run only in the VM, through a real
  MCP client: every desktop tool, the kill switch, `USER_ACTIVE`.

### W8: install and upgrade

- `install.ps1` (`irm ... | iex`): finds Python or fetches it with uv,
  checks the wheel against `sha256sums.txt`, installs into
  `%LOCALAPPDATA%\pcbridge\venv`, runs `pcbridge setup`. The English guard
  also scans `*.ps1`.
- `pcbridge setup` on Windows: OCR and Tailscale components, client
  connections, the Run-key entry, PATH through `HKCU\Environment`,
  `ROLLBACK.md`.
- The `win_amd64` wheel tag and the Windows classifier; `_native/*/
  pcbridge-native.exe` as package data; `upgrade.py` picks the Windows wheel
  and restarts the daemon when idle; `native/fetch.py` handles `.exe`.
- `uninstall` removes the Run key, the PATH entry and the files, through the
  Recycle Bin.

### W9: CI, documents, release

- CI: the Windows job runs the portable suites on Python 3.12 to 3.14;
  `native.yml` builds, lints and tests with MSVC.
- `release.yml` adds the Windows wheel, `pcbridge-native-x86_64-pc-windows-msvc.exe`
  and `install.ps1`, all in `sha256sums.txt`.
- Documents: README, install, usage, architecture, a Windows section in
  security.md, native packaging, CLAUDE.md, ROADMAP. CHANGELOG with the
  `changelog` skill.

### W10: end to end in a fresh VM

- A `fresh` disk, install from the release with `install.ps1`.
- All 40 tools from a client inside the VM (`tests/readiness/check.py
  --desktop --http`), `test_e2e.py` with `PCBRIDGE_TEST_NO_AGENT=1`, an
  upgrade from the previous Windows build.
- Left for the maintainer: connecting claude.ai through the tunnel, and
  publishing the release.
