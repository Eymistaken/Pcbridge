# Hyprland implementation walkthrough

This is the implementation journal for the Hyprland support contract in
`YAPILACAKLAR.md`. A stage is recorded here before its local commit. Later
entries refer to commit hashes once those hashes exist. Measurements are
recorded separately in `docs/dev/measured-facts.md` when they inform product
behavior.

## Starting state

- Branch: `main`, initially aligned with `origin/main` at `4edec7c`.
- Pre-existing worktree state: `CLAUDE.md` modified; `PLAN.md` deleted;
  `ONERI.md` and `YAPILACAKLAR.md` untracked. These changes belong to the
  maintainer and are excluded from implementation commits unless the final
  documentation stage explicitly updates an already modified file with a
  reviewed diff. `ONERI.md` features are outside this task.
- The host is a real GNOME session. Dangerous input and lock tests belong in
  a disposable VM. The local Arch/KDE VM remains separate.
- Safety boundaries: shared `SafetyGate`, cross-process execution lock,
  grant ID and revoke epoch, native helper validation, and one global canvas
  coordinate system remain authoritative on Hyprland.

## Stage 1: Independent Arch/Hyprland measurement environment

**Objective:** Set up an isolated target-like VM, record real Hyprland
interfaces, and reproduce the reported plain `pcbridge` failure before
changing desktop behavior.

**Design decisions:** Use a separate QEMU overlay, seed, port pair, and
working directory. Reuse the SHA256-verified Arch cloud image as a read-only
backing file; do not alter the KDE VM disk or its automation. Import only
allowlisted graphical-session variables from the VM's systemd user manager,
with quoted `export` assignments rather than shell `eval`.

**Files changed:** `scripts/dev/hyprland-vm.sh`, `WALKTHROUGH.md`,
`docs/dev/measured-facts.md`.

**Measurements and evidence:** The host has QEMU/KVM but no installed
`Hyprland` or `hyprctl`. The Arch package repository reports Hyprland
0.56.2-3, hyprlock 0.9.6-3, hypridle 0.1.8-2, and
xdg-desktop-portal-hyprland 1.4.1-2. The existing Arch base image passed
`sha256sum -c` before creating the independent overlay. SDDM started
Hyprland 0.56.2 on `wayland-1`, with two 1280x800 outputs. The Wayland
registry advertises layer-shell v5, screencopy v3, image-copy-capture v1,
idle-notifier v2, and Hyprland lock-notify v1. `hyprctl -j binds` returned
48 bindings, including opaque Lua dispatchers. A foot client was mapped and
focused through compositor IPC. QEMU screendump remained black and a
diagnostic `grim` capture timed out, so neither establishes capture success.
Systemd user service queries initially reported the portal services,
PipeWire, and WirePlumber inactive; activation and backend selection still
need validation. In a fresh VM without config, plain `pcbridge` exited with
a traceback through `SettingsPane.load` and `ConfigEditor`, ending in
`SystemExit: No pcbridge config file found`. This may differ from the
field-reported error. With a VM-only example config installed at mode 0600,
the same TUI ran for an eight-second PTY observation and rendered Overview,
Settings, and Tools. The current doctor called this valid Hyprland session
GNOME and warned that GNOME Shell, its extension, and Mutter were missing.

**Tests run:**

- `bash -n scripts/dev/hyprland-vm.sh` — pass.
- `sha256sum -c Arch-Linux-x86_64-cloudimg.qcow2.SHA256` in the Arch VM data
  directory — pass.
- `scripts/dev/hyprland-vm.sh create` — pass after correcting the checksum
  directory for the shared backing image.
- `scripts/dev/hyprland-vm.sh start` — pass; SSH answered on 127.0.0.1:2223.
- `scripts/dev/hyprland-vm.sh sync` — initial attempt failed because the
  inherited VM sync command passed the maintainer-deleted `PLAN.md` to tar.
  The Hyprland script now copies only present tracked worktree paths; rerun
  passed, without copying the unrelated `ONERI.md`.
- `scripts/dev/hyprland-vm.sh provision` — a clean rerun passed after the
  original package installation and VM reboot. SDDM restored the Hyprland
  login; `hyprctl -j version` still reported `0.56.2`.
- `scripts/dev/hyprland-vm.sh session 'hyprctl -j version | jq -r .version'`
  — pass, `0.56.2`, after replacing the inherited `eval` environment import.
- `scripts/dev/hyprland-vm.sh session 'hyprctl -j binds ...; hyprctl -j
  monitors ...; hyprctl locked; wayland-info ...'` — pass; the parsed fields,
  monitor geometry, lock answer, and registry globals are recorded above.
- `timeout 10s script -q -e -c '.venv/bin/pcbridge'` in the unconfigured VM
  — failed with the full traceback preserved in
  `/tmp/pcbridge-tui-before.typescript` inside the VM.
- `timeout 8s script -q -e -c '.venv/bin/pcbridge'` after installing the
  example config — timed out as expected while the app remained open;
  `/tmp/pcbridge-tui-configured.typescript` contains the rendered tabs and
  no traceback.
- `pcbridge doctor --json` in the graphical session environment — exited 1
  and incorrectly described GNOME/Mutter instead of Hyprland.
- `./.venv/bin/python tests/test_desktop.py` — pass, 615 checks; live input
  flags were not set.
- `(cd rust && cargo test --workspace --locked --no-fail-fast)` — pass.
- `./.venv/bin/python -m unittest discover -s tests/contracts -t .` — 662
  tests ran, one skipped, one error. The error is the pre-existing deletion of
  tracked `PLAN.md`: `test_doc_links.py` attempts to read every tracked
  Markdown path even when that path no longer exists in the worktree. No
  Hyprland source change caused this baseline error; the deleted file is not
  restored or staged.

**Open questions:** The cousin's exact error remains unavailable, so the VM
traceback must not be represented as that user's root cause. Native capture
permission behavior, black QEMU screendump, lock authority, idle watcher
transitions, and portal activation require later live measurements.

**Local commit:** `1b5c80d`.

## Stage 2: Explicit compositor selection and safe baseline

**Objective:** Separate GNOME, KDE Plasma, Hyprland, and UNKNOWN in the Python
and native helper paths before any Hyprland acting operation is enabled.

**Design decisions:** Use the desktop environment name when present, including
`XDG_SESSION_DESKTOP`, and pair a Hyprland instance signature with same-user
IPC and Wayland sockets when recovering missing session context. Ambiguous
instances and stale or contradictory signatures do not select another
session. The Python compositor descriptor and Rust `DesktopKind` have explicit
UNKNOWN variants. Until authoritative lock and visible-frame support are in
place, Hyprland and UNKNOWN cannot open a grant or pass the shared Python
gate. The native helper uses unknown lock observations and refuses display and
capture rather than selecting Mutter. Capture capability probes and window
focus helpers do not claim a GNOME backend on either desktop.

**Files changed:** `pcbridge/desktop/session.py`, `compositor.py`, `safety.py`,
`monitors.py`, `apps.py`, `backends/python.py`, `backends/rust.py`,
`pcbridge/cli/grant.py`, the Rust desktop/state/display/capture/dispatch
modules, `tests/contracts/test_hardening.py`,
`tests/contracts/test_hyprland_session.py`, and this journal.

**Measurements and evidence:** A VM session with the updated source returned
`desktop_kind() == hyprland`, selected `wayland-1` from the matching instance,
and reported Hyprland `0.56.2` with no GNOME Shell version. The existing
doctor still needs a separate adaptation and continues to print GNOME checks;
that is a later stage, not a support claim. The test with two same-user
instances demonstrates that a missing signature/display remains ambiguous;
a stale signature paired with a different display remains UNKNOWN.

**Tests run:**

- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_session tests.contracts.test_hardening`
  — pass, 16 tests. The first run exposed a wrong test expectation for a
  contradictory signature/display pair; correcting the expectation produced
  the pass above.
- `./.venv/bin/python tests/test_desktop.py` — pass, 615 checks; no live input.
- `(cd rust && cargo test --workspace --locked --no-fail-fast)` — pass.
- `(cd rust && cargo fmt --check)` — pass.
- `scripts/dev/hyprland-vm.sh sync` — pass.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && .venv/bin/python -c "from pcbridge.desktop.session import desktop_kind, platform_summary, hyprland_instance; print(desktop_kind()); print((hyprland_instance() or {}).get(\"wl_socket\")); p=platform_summary(); print(p[\"environment\"], p[\"hyprland\"], p[\"gnome_shell\"])"'`
  — pass; printed `hyprland`, `wayland-1`, `hyprland 0.56.2 None`.
- `git diff --check` — pass.

**Open questions:** Rust and Python currently differ in the amount of runtime
validation they perform when only a Hyprland signature is present. Both
remain fail closed in this stage. Later native adapters must bind their IPC to
the same selected session before acting. Doctor, runtime binds, monitor and
window adapters, lock/idle, frame, and capture remain unfinished.

**Local commit:** `fde10d5`.

## Stage 3: Read-only runtime keybind context

**Objective:** Let the agent inspect the registered keybinds and active
submap of the actual Hyprland session without guessing from defaults or
reading config files.

**Design decisions:** Query the selected instance with `hyprctl -i ... -j
binds` and `hyprctl -i ... submap`. The IPC wrapper allowlists only these two
read-only requests. Preserve each binding's complete JSON object, including
future fields and raw types. `system_capabilities` carries every binding in
`structured_content.hyprland_bindings`; the text view includes the first 100
raw records and points to the complete structured table for larger sets.
IPC failure is an explicit unavailable result. Platform text now identifies
Hyprland instead of printing an unsupported/GNOME label.

**Files changed:** `pcbridge/desktop/hyprland.py`,
`pcbridge/desktop/presentation.py`,
`tests/contracts/test_hyprland_binds.py`, `docs/dev/measured-facts.md`,
and this journal.

**Measurements and evidence:** The Arch/Hyprland VM returned 48 registered
bindings and active submap `default` through the selected instance. The
reported fields are listed in the measured-facts document. A live
`capabilities_result(runtime.capabilities())` in the VM returned platform
`hyprland` and all 48 binds in structured content. Attempting to switch to
an undefined test submap was rejected by Hyprland and did not change the
active submap. No config parser or dispatcher was invoked by the product
context path.

**Tests run:**

- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_binds tests.contracts.test_hyprland_session tests.contracts.test_mcp_errors`
  — pass, 29 tests.
- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_binds tests.contracts.test_mcp_errors tests.contracts.test_mcp_contract tests.contracts.test_hardening`
  — pass, 40 tests after the capability presentation change.
- `scripts/dev/hyprland-vm.sh sync` — pass.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && .venv/bin/python -c "from pcbridge.desktop.hyprland import bindings_snapshot; s=bindings_snapshot(); print(s[\"available\"], s[\"count\"], s[\"active_submap\"], sorted(s[\"bindings\"][0]) if s[\"bindings\"] else s.get(\"reason\"))"'`
  — pass; `True 48 default` and the 18 field names reported by runtime IPC.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && timeout 20s .venv/bin/python -c "from pcbridge.cli import load, runtime_of; from pcbridge.desktop.presentation import capabilities_result; r=runtime_of(load()); p=capabilities_result(r.capabilities()); print(p.structured_content[\"platform\"][\"environment\"], p.structured_content[\"hyprland_bindings\"][\"count\"]); r.close()"'`
  — pass; `hyprland 48` from the full capability presentation.
- `scripts/dev/hyprland-vm.sh session 'hyprctl dispatch '\''hl.dsp.submap("pcbridge_test")'\''; hyprctl submap; hyprctl dispatch '\''hl.dsp.submap("reset")'\''; hyprctl submap'`
  — the undefined submap dispatch failed as expected; both submap reads
  returned `default`, and the final reset succeeded.

**Open questions:** A non-default submap transition still needs a registered
VM binding and live verification. Opaque `__lua` numeric arguments cannot be
expanded from the runtime bind response; their raw value is reported without
inventing a command. The runtime table can contain user-configured command
arguments, so it belongs in the requested capability result and is not
written to audit logs.

**Local commit:** `331f743`.

## Stage 4a: Python Hyprland monitor transport

**Objective:** Feed Hyprland's active output table into the existing neutral
monitor resolver and single global canvas coordinate system.

**Plan adjustment:** Live scale/rotation changes showed that Hyprland JSON
keeps raw mode pixels while positions are logical and may be moved by the
compositor. To make this cross-language mapping reviewable, the monitor stage
is split: this Python transport commit, followed by a separate native Rust
display transport and shared fixture commit. Window IPC remains a later
independent stage. The overall security and acceptance contract is unchanged.

**Design decisions:** `hyprctl -j monitors` is an allowlisted read-only query
against the selected instance. Validate names, scale, transform, pixel size,
position, and duplicate outputs before passing a neutral state to the shared
resolver. Treat mirrors as an explicit unsupported mapping until their
coordinate/capture semantics are measured. Cache the result for the existing
two-second interval, keyed by compositor and session; fresh reads bypass the
cache. An IPC error does not fall back to XRandR on Hyprland.

**Files changed:** `pcbridge/desktop/hyprland.py`,
`pcbridge/desktop/monitors.py`,
`tests/contracts/test_hyprland_monitors.py`,
`docs/dev/measured-facts.md`, and this journal.

**Measurements and evidence:** On the VM's default two-output layout,
`list_monitors(use_cache=False)` returned Virtual-1 `(0,0) 1280x800` and
Virtual-2 `(1280,0) 1280x800`, with canvas 2560x800 and a stable topology ID.
After changing Virtual-2 to scale 1.25 and transform 1 in the VM, the same
reader returned a 640x1024 logical span with 800x1280 source pixels, at
`(1280,0)`; the canvas was 1920x1024. The output rules were restored and
`hyprctl -j monitors` confirmed both original positions, scales, and
transforms. The Python result is not yet a capture-success claim.

**Tests run:**

- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_monitors tests.contracts.test_display_contract tests.contracts.test_hyprland_binds`
  — pass, 22 tests.
- `./.venv/bin/python tests/test_desktop.py | tail -2` — pass, 615 checks;
  no live input.
- `scripts/dev/hyprland-vm.sh sync` — pass.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && .venv/bin/python -c "from pcbridge.desktop.monitors import list_monitors, canvas_size, topology_id; m=list_monitors(use_cache=False); print([(x.connector,x.x,x.y,x.width,x.height,x.scale,x.primary) for x in m]); print(canvas_size(m), topology_id(m))"'`
  — pass; two active monitors and canvas 2560x800.
- `scripts/dev/hyprland-vm.sh session 'hyprctl eval '\''hl.monitor({ output = "Virtual-2", mode = "1280x800@74.99", position = "1280x0", scale = 1.25, transform = 1 })'\'' && cd ~/pcbridge && .venv/bin/python -c "from pcbridge.desktop.monitors import list_monitors, canvas_size; m=list_monitors(use_cache=False); print([(x.connector,x.platform,x.x,x.y,x.width,x.height,x.scale,x.transform,x.source_pixel_size) for x in m]); print(canvas_size(m))"'`
  — pass; scale 1.25/transform 1 produced 640x1024 logical and 800x1280
  source pixels. A separate reset command restored the initial layout.

**Open questions:** A mirrored output intentionally fails mapping; acceptance
needs a product decision or measured safe behavior if mirrors are in scope.
The Rust helper still rejects Hyprland display snapshots until the next
stage. Negative coordinates and above/below layouts have deterministic unit
tests; live VM verification remains for acceptance.

**Local commit:** `7095945`.

## Stage 4b: Native Hyprland monitor transport

**Objective:** Give the grant-bound native helper the same monitor table and
canvas mapping as the Python adapter.

**Design decisions:** Connect directly to the selected same-user Hyprland
IPC socket. Require the instance signature and Wayland socket to match one
runtime instance, and verify socket ownership and type. Requests have two-second
read/write timeouts and a 4 MiB response limit. Convert raw mode pixels to the
existing neutral display state; the shared resolver applies scale and transform
once. Both adapters reject multiple focused outputs. A shared JSON fixture
checks cross-language parity for negative coordinates and fractional rotation.
No input or capture permission is enabled by this stage.

**Files changed:** `pcbridge/desktop/monitors.py`,
`rust/crates/pcbridge-native/src/platform/linux/desktop.rs`,
`rust/crates/pcbridge-native/src/platform/linux/display.rs`,
`rust/crates/pcbridge-native/tests/display_contract.rs`,
`rust/crates/pcbridge-native/tests/hyprland_live.rs`,
`tests/contracts/test_hyprland_monitors.py`,
`tests/fixtures/native/hyprland_monitor_cases.json`, and this journal.

**Measurements and evidence:** Native and Python snapshots in the VM both
reported canvas 2560x800 and topology
`v1|0,0,1280,800,1.0000,0,1|1280,0,1280,800,1.0000,0,0`.
With Virtual-2 at scale 1.25/transform 1, both reported canvas 1920x1024
and identical topology. The output rules were restored. After reboot on
September 28, the native live test passed again with the signature/Wayland
pair validation in place. SSH took about two minutes to become available;
the black QMP screenshot is still not capture evidence.

**Tests run:**

- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_monitors tests.contracts.test_display_contract`
  — pass, 19 tests.
- `(cd rust && cargo fmt --all -- --check)` — pass.
- `(cd rust && cargo test --workspace --locked --no-fail-fast)` — pass.
  Live tests without their opt-in environment flags do not exercise a compositor.
- `scripts/dev/hyprland-vm.sh sync` — pass.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge/rust && PCBRIDGE_TEST_LIVE_HYPRLAND=1 cargo test -p pcbridge-native --locked --test hyprland_live -- --nocapture'`
  — pass, one real Hyprland display test, including the final session validation.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && .venv/bin/python -c "from pcbridge.desktop.monitors import list_monitors,canvas_size,topology_id; m=list_monitors(use_cache=False); print(\"Python\",canvas_size(m),topology_id(m))" && cd rust && PCBRIDGE_TEST_LIVE_HYPRLAND=1 cargo test -p pcbridge-native --locked --test hyprland_live -- --nocapture'`
  — pass on the fractional/rotated VM layout; Python/native canvas and topology matched.

**Review:** Checked session ambiguity, bounded IPC, invalid geometry, shared
fixture parity, and error propagation. No new dependency or permission was added.

**Open questions:** Mirror mapping and live negative/vertical layouts remain
for acceptance. Capture and cursor-coordinate mapping require later real
pixel/input evidence; monitor IPC alone does not satisfy them.

**Local commit:** `f3ba71c`.

## Stage 5a: Read-only compositor window observations

**Objective:** List Hyprland windows and observe focus even when an application
does not participate in AT-SPI.

**Design decisions:** Add a window-provider boundary to the shared runtime.
GNOME/KDE keep their existing accessibility provider; Hyprland selects a
read-only `clients`/`activewindow` provider independently of widget accessibility.
Validate mapped state, unique addresses/stable IDs, PID, class, and title.
Expose an exact `hyprland:0x...` reference and escape control characters in
labels. Batch focus identity includes address, stable ID when available,
PID, class, and title, so identical titles do not hide a focus change.
Unknown or inconsistent IPC cannot become an unchanged focus observation.
Desktop search explicitly refuses Hyprland/UNKNOWN before sending a key.

**Plan adjustment:** Separate window observations from focus dispatch. Complete
authoritative lock/idle and visible-frame prerequisites before measuring an
acting focus operation through PcBridge. This avoids testing an acting path
by bypassing the shared gate. No Hyprland grant can be opened yet.

**Files changed:** `pcbridge/desktop/contracts.py`, `hyprland.py`,
`hyprland_windows.py`, `runtime.py`, `ops.py`, `apps.py`,
`pcbridge/tools.py`, `pcbridge/cli/do.py`,
`tests/contracts/test_hyprland_windows.py`, and this journal.

**Measurements and evidence:** A real VM foot client reported address
`0x5586e1144f70`, stable ID `18000003`, PID 1057, class `foot`, and title
`tester@pcbridge-hyprland:~`. The provider listed it as active and returned
the same identity. The runtime factory selected `HyprlandWindowProvider`;
its capability was `supported linux.hyprland-ipc`, independently of AT-SPI.
This is read-only evidence, not focus/capture acceptance.

**Tests run:**

- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_windows tests.contracts.test_window_focus tests.contracts.test_window_operations tests.contracts.test_runtime_contract tests.contracts.test_mcp_errors tests.contracts.test_capabilities tests.contracts.test_kde_windows tests.contracts.test_batch_safety`
  — pass, 154 tests before the final capability-isolation case.
- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_windows`
  — pass, 9 tests with the final case.
- `./.venv/bin/python tests/test_desktop.py | tail -2` — pass, 615 checks.
- `scripts/dev/hyprland-vm.sh sync` — pass.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && .venv/bin/python -c "from pcbridge.desktop.hyprland_windows import HyprlandWindowProvider; p=HyprlandWindowProvider(); print(p.describe_windows(p.windows())); print(p.focused_identity())"'`
  — pass; real client identity and active marker matched compositor IPC.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && timeout 20s .venv/bin/python -c "from pcbridge.cli import load,runtime_of; r=runtime_of(load()); p=r.window_provider; print(type(p).__name__,p.focused_window()); c=r.capabilities(refresh=True).capabilities[\"window.list\"]; print(c.state.value,c.backend); r.close()"'`
  — pass; runtime selection and window capability matched Hyprland.
- `git diff --check` — pass.

**Review and corrections:** Initial GNOME tests exposed an overly broad optional
identity lookup on an AT-SPI test double. Restricting it to a distinct window
provider preserved the existing GNOME focus fallback. Repeated tests passed.
Reviewed malformed identities, duplicate IDs, empty focus, inconsistent snapshots,
and control-character escaping. No acting IPC request was added.

**Open questions:** Focus dispatch/version selection, live same-title window
transitions, and list/focus tool acceptance under a visible grant remain.

**Local commit:** `32a6a94`.

## Stage 6a: Session-bound native idle observations

**Objective:** Verify Hyprland input-idle notifications and refuse stale
observer state before enabling desktop control.

**Design decisions:** Reuse `ext_idle_notifier_v1` v2 input notifications.
Use the existing async I/O dependencies to dispatch input events immediately
and request a Wayland sync heartbeat every 500 ms. Only a compositor reply
refreshes a heartbeat; an independent timer must not bless stale state.
Version-2 records carry display, Hyprland instance, PID start ticks, and a
3000 ms freshness limit. Both Python and Rust readers reject old records,
dead writers, PID reuse, inconsistent timestamps, and foreign sessions.
Enable daemon supervision on Hyprland as well as Plasma. The new live probe
owns only its observer process and refuses to replace a resident observer.

**Measurements and evidence:** The original version-1 observer was frozen
with SIGSTOP in the VM; the Python reader still reported 17066 ms idle. This
reproduced the stale-state bug before changing it. The new observer advanced
2021 -> 3022 ms in one second. QMP Shift press/release reset it to 0. Frozen
observer and frozen compositor tests returned UNKNOWN after four seconds;
resuming restored a known value. The repeatable live probe measured 0 ->
2090 ms and verified stopped observer, mismatched session, and dead observer
all became UNKNOWN. All signals were sent inside the disposable VM.

**Files changed:** `pcbridge/daemon.py`, `pcbridge/desktop/idlewatch.py`,
`pcbridge/desktop/safety.py`, `rust/crates/pcbridge-native/src/main.rs`,
`rust/crates/pcbridge-native/src/platform/linux/idle.rs`,
`tests/contracts/test_idlewatch.py`, `tests/contracts/test_desktop_state.py`,
`tests/live/hyprland/check_idle.py`, `docs/dev/measured-facts.md`, and this journal.

**Tests run:**

- `(cd rust && cargo test -p pcbridge-native --locked --lib)` — pass, 12 tests.
- `(cd rust && cargo test --workspace --locked --no-fail-fast > /tmp/pcbridge-hyprland-stage6a-rust.log 2>&1)`
  — pass, exit 0; compositor tests remain opt-in.
- `./.venv/bin/python -m unittest tests.contracts.test_idlewatch tests.contracts.test_hyprland_session tests.contracts.test_desktop_state tests.contracts.test_batch_safety`
  — pass, 56 tests. The first run exposed the old compositor test mocking
  `is_kde` after explicit selection had moved to `current`; it now selects
  GNOME/KWin explicitly and checks the actual transport calls.
- `scripts/dev/hyprland-vm.sh sync` — pass.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge/rust && cargo build -p pcbridge-native --locked'`
  — pass.
- `scripts/dev/hyprland-vm.sh session 'systemctl --user stop pcbridge-idle-probe; cd ~/pcbridge; PCBRIDGE_TEST_LIVE_HYPRLAND=1 .venv/bin/python tests/live/hyprland/check_idle.py --binary rust/target/debug/pcbridge-native'`
  — pass; JSON evidence reported advancing idle and all three UNKNOWN cases.
- `scripts/dev/hyprland-vm.sh session 'systemd-run --user --collect --unit=pcbridge-idle-probe /home/tester/pcbridge/rust/target/debug/pcbridge-native idle-watch >/dev/null; sleep 2; pid=$(pgrep -x Hyprland | head -1); trap '\''kill -CONT "$pid"; systemctl --user stop pcbridge-idle-probe'\'' EXIT; kill -STOP "$pid"; sleep 4; cd ~/pcbridge; .venv/bin/python -c "from pcbridge.desktop.idlewatch import read_idle_ms; value=read_idle_ms(); print(\"stalled compositor idle_ms\",value); assert value is None"; kill -CONT "$pid"; sleep 1; .venv/bin/python -c "from pcbridge.desktop.idlewatch import read_idle_ms; value=read_idle_ms(); print(\"resumed compositor idle_ms\",value); assert value is not None"'`
  — pass; `None` while stalled, 7096 ms after resume; cleanup stopped the observer.

**Review:** Confirmed no timer-only heartbeat, immediate input-event dispatch,
matching Python/Rust validation, and startup active state as the conservative
default. No dependency was added. Version-1 native watchers now produce UNKNOWN
until replaced by the matching new helper; this is a deliberate fail-closed upgrade.

**Open questions:** Real PcBridge uinput resets and sequence behavior need a
visible grant. Hyprlock authority is the next stage. Plasma live regression
of the changed observer remains required before claiming overall acceptance.

**Local commit:** `c9ad902`.

## Stage 6b: Authoritative Hyprland lock observations

**Objective:** Read actual compositor session-lock state, including locker
failure, before implementing grant-visible control.

**Design decisions:** Use the installed version's `locked` IPC request,
which reads the compositor's session-lock manager. Accept only a JSON boolean;
never infer unlock from absence of a hyprlock process. Native lock requests
have a 200 ms absolute deadline covering connect, write, and read, and a 4096
byte limit. Monitor requests share the bounded transport with their existing
two-second/4 MiB limits. The existing GNOME/KDE D-Bus provider is preserved
behind a selected state-source boundary. Python shared safety observes the
same authoritative IPC. Production native lifecycle still refuses Hyprland
control pending the visible-frame stage, including a lease left by another
session. No desktop grant was opened during these observation tests.

**Files changed:** `pcbridge/desktop/compositor.py`, `hyprland.py`, `safety.py`,
`rust/crates/pcbridge-native/src/lifecycle.rs`, native Linux `desktop_state.rs`,
`display.rs`, `hyprland.rs`, `mod.rs`, native `hyprland_live.rs` and
`hyprland_state.rs` tests, `tests/contracts/test_hyprland_state.py`,
`tests/live/hyprland/check_lock.py`, `docs/dev/measured-facts.md`, and this journal.

**Measurements and evidence:** Real hyprlock produced unlocked -> locked ->
unlocked in Python and Rust. Killing the next locker left both readers locked.
The final probe returned
`{"cleanup":"requires_vm_session_reset","initial":"unlocked","lock_transition":"locked","locker_crash":"still_locked","normal_unlock":"unlocked"}`.
Only the dedicated VM's SDDM session was reset afterward. A fresh native smoke
test then reported KnownUnlocked and the unchanged 2560x800 monitor canvas.

**Tests run:**

- `(cd rust && cargo test -p pcbridge-native --locked --lib)` — pass, 13 tests,
  including refusal of incomplete and oversized lock replies.
- `(cd rust && cargo test -p pcbridge-native --locked --test hyprland_state)`
  — pass, one strict lock-shape contract.
- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_state tests.contracts.test_desktop_state tests.contracts.test_hyprland_session`
  — pass, 16 tests.
- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_state tests.contracts.test_desktop_state tests.contracts.test_hyprland_session tests.contracts.test_idlewatch tests.contracts.test_hyprland_windows tests.contracts.test_batch_safety`
  — pass, 68 tests after the lifecycle safeguard.
- `(cd rust && cargo fmt --all && cargo test --workspace --locked --no-fail-fast > /tmp/pcbridge-hyprland-stage6b-rust.log 2>&1)`
  — pass, exit 0 after the lifecycle safeguard; non-opted-in live tests do not
  exercise a compositor.
- `scripts/dev/hyprland-vm.sh sync` — pass.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge; PCBRIDGE_TEST_LIVE_HYPRLAND=1 .venv/bin/python tests/live/hyprland/check_lock.py'`
  — final run pass, four native lock probes and the crash evidence above.
- `scripts/dev/hyprland-vm.sh ssh 'sudo systemctl restart sddm'` — pass;
  reset only the disposable VM after the unrecoverable locker crash.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge/rust && PCBRIDGE_TEST_LIVE_HYPRLAND=1 PCBRIDGE_TEST_HYPRLAND_LOCKED=false cargo test -p pcbridge-native --locked --test hyprland_live -- --nocapture'`
  — pass, two real compositor tests after reset and final sync.

**Review and corrections:** The first live probe sent SIGUSR1 before hyprlock
received `onLockLocked`; its log showed the signal was ignored. The probe now
waits for the actual callback. A subsequent probe incorrectly expected a
replacement locker to recover the crash; the measured stock restore setting
was false. Removed that unsupported test assumption and preserved the setting.
The successful crash test measures continued lock and resets the VM session.
Reviewed malformed replies, absolute deadlines, socket selection, and the
continued prohibition on invisible production grants.

**Open questions and next stage:** Grant and mid-batch lock acceptance require
the visible overlay. First fix the VM's independent screenshot path: QMP was
selecting its implicit VGA console rather than the virtio outputs. Native
capture timeouts remain a separate unproven issue until measured.

**Local commit:** `cc0daaa`.

## Stage 6c: Reliable VM graphics evidence

**Objective:** Resolve the black VM display before relying on screenshots
for capture, input, or visible-frame acceptance.

**Plan adjustment and design decisions:** The original VM had an implicit
standard VGA output and one connected virtio output, despite the two-output
monitor table. Remove implicit VGA. VNC leaves additional heads disconnected
on the installed QEMU, so provide two separate virtio GPU devices with one
output each. Add the measured missing GTK Cairo dependency to provisioning.
Use the existing fullscreen pattern client and diagnostic grim only as an
independent VM probe; production capture still must use the grant-bound helper.

**Files changed:** `scripts/dev/hyprland-vm.sh`,
`tests/live/hyprland/check_graphics.py`, `docs/dev/measured-facts.md`, and this
journal.

**Measurements and evidence:** Before the change, mapped foot at `(22,22)`
with size 1236x756 still produced a 1280x800 QMP image with exactly one color:
black. Diagnostic grim exited 124 after ten seconds. Wayland tracing showed
image-copy buffer negotiation and damage/transform events but no completed
frame. `/sys/class/drm` identified card0 as the standard VGA PCI device.
Removing only implicit VGA immediately produced nonblack QMP pixels and
grim exit 0, with one connected virtio output. The second virtio head remained
disconnected; a second GPU restored two real outputs. Both diagnostic captures
then exited 0. Native monitor/lock smoke tests still passed with a 2560x800
canvas and KnownUnlocked. The final repeatable pattern probe returned exact
magenta/cyan markers and counter 521 then 522 on both 1280x800 outputs. QMP
also visibly showed the mapped foot window; raw screenshots are local artifacts
under `/tmp/pcbridge-hyprland-*.png`, not committed.

**Tests and diagnostic commands:**

- `scripts/dev/hyprland-vm.sh screenshot /tmp/pcbridge-hyprland-foot-before.png`
  — reproduced all-black output, measured with Pillow extrema/color count.
- `scripts/dev/hyprland-vm.sh session 'timeout 10s grim -o Virtual-1 /tmp/pcbridge-grim-before.png; status=$?; printf "grim_exit=%s\\n" "$status"; hyprctl -j clients | jq "map({class,title,mapped})"; ls -l /sys/class/drm'`
  — reproduced exit 124 with a mapped foot client.
- `scripts/dev/hyprland-vm.sh stop && scripts/dev/hyprland-vm.sh start`
  — pass after the one-GPU experiment, then pass after the final two-GPU change;
  only the disposable VM was restarted.
- `scripts/dev/hyprland-vm.sh ssh 'sudo pacman -S --noconfirm --needed python-cairo'`
  — pass after the pattern exposed the missing Cairo converter.
- `scripts/dev/hyprland-vm.sh sync` — pass.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && PCBRIDGE_TEST_LIVE_HYPRLAND=1 .venv/bin/python tests/live/hyprland/check_graphics.py --out-dir /tmp/pcbridge-graphics-evidence'`
  — final run pass; four correct output/counter pairs. The first run failed
  for missing Cairo; the next captured a fullscreen fade before presentation.
  The probe now waits for the actual marker and counter within five seconds.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge/rust && PCBRIDGE_TEST_LIVE_HYPRLAND=1 PCBRIDGE_TEST_HYPRLAND_LOCKED=false cargo test -p pcbridge-native --locked --test hyprland_live -- --nocapture'`
  — pass, two real compositor tests on the final GPU layout.
- `bash -n scripts/dev/hyprland-vm.sh` and `git diff --check` — pass.

**Review:** The change is confined to the new Hyprland VM path. No production
capture process or host graphics configuration changed. The diagnostic test
requires the dedicated VM hostname and known unlocked session, draws without
input, and closes its own pattern client even when an assertion fails.

**Open questions:** PcBridge capture acceptance, layer-shell presentation,
click-through behavior, and monitor changes remain. These now have a working
independent screenshot oracle.

**Local commit:** `4f78903`.

## Stage 7a: Native reference glow renderer

**Objective:** Render the GNOME reference frame on real Hyprland outputs and
verify displayed pixels before connecting it to desktop authorization.

**Design decisions:** Four layer-shell overlay strips per selected output,
explicit empty input regions, keyboard interactivity NONE, and exclusive zone
-1. This reserves no space and keeps the strips outside panel reservations;
the protocol's zero setting would move them inward. Bind no seat/input object.
Preserve white alpha 0.42, short-edge depth 8.5% clamped 48..150, quadratic
700/500 ms fades, sine breathing 1.00..0.88 over an 11-second cycle, and the
reference falloff. Pure appearance math is separate from Wayland ownership.
Use two release-tracked, premultiplied ARGB SHM buffers per strip, unlinked
0600 files, and 16 MiB per-pool/64 MiB total limits. No unsafe code was added.
Require actual presentation feedback for every fully visible strip; configure
or sync callbacks alone never make the renderer healthy. Output removal or
unexpected layer geometry fails closed. This drawing-only probe opens no grant;
the Python and native production prohibitions remain in place.

**Dependency review:** Added only `wayland-protocols-wlr` 0.3.12, from the same
Smithay Wayland stack already used by the helper, for generated layer-shell
bindings (MIT). The lockfile adds one package; no existing dependency version
changed. The existing stack supplies presentation-time, SHM, async readiness,
and timers. Full dependency advisory scanning remains part of the release gate.

**Files changed:** `pcbridge/desktop/hyprland.py` (read-only layers query),
Rust workspace/native dependency manifests and lockfile, native Linux `mod.rs`,
`glow.rs`, `glow/appearance.rs`, native `hyprland_glow_live.rs`,
`tests/live/hyprland/check_glow_renderer.py`, `docs/dev/measured-facts.md`,
and this journal.

**Measurements and evidence:** Real `layers` IPC reported eight strips at
the correct outer coordinates, including `(0,732) 1280x68`, `(1212,0) 68x800`,
and the corresponding second-output coordinates. First full presentation was
measured at 698 ms, and at 676 ms on a repeated probe (the 0.99 health threshold
can precede the exact fade endpoint). On both outputs all four outer-edge
samples changed RGB `(30,30,40)` to `(124,124,130)`. Left-edge inward samples
at distances 0, 4, 12, 24, 37, 53, and 67 matched the reference within five
8-bit channel values. Focus identity and the two fullscreen window geometries
were unchanged. After one full breathing cycle and fade-out, the test process
exited and all glow layers were absent. The independently viewed QMP artifact
`/tmp/pcbridge-hyprland-glow-probe.png` showed the soft white edge character.
The warm debug probe including Cargo consumed 0.506 s user + 0.222 s system
CPU over 13.735 s wall time; release/steady-state cost remains for acceptance.

**Tests run:**

- `(cd rust && cargo test -p pcbridge-native --offline --lib)` — pass, 15 tests;
  initial dependency resolution added the one cached binding crate.
- `(cd rust && cargo fmt --all && cargo test -p pcbridge-native --locked --lib && cargo test -p pcbridge-native --locked --test hyprland_glow_live)`
  — pass; 15 unit tests and one non-opted-in live test (no compositor exercised).
- `cargo test -p pcbridge-native --locked --lib --manifest-path rust/Cargo.toml`
  — pass, 15 tests after separating appearance math from renderer ownership.
- `(cd rust && cargo test --workspace --locked --no-fail-fast > /tmp/pcbridge-hyprland-stage7a-rust.log 2>&1)`
  — pass, exit 0; live tests remain opt-in.
- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_binds tests.contracts.test_hyprland_state tests.contracts.test_hyprland_windows tests.contracts.test_hyprland_session`
  — pass, 20 tests.
- `scripts/dev/hyprland-vm.sh sync` — pass.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge/rust && PCBRIDGE_TEST_HYPRLAND_GLOW=1 cargo test -p pcbridge-native --locked --test hyprland_glow_live -- --nocapture'`
  — pass, real presentation on eight strips and complete breathing/fade lifecycle.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && PCBRIDGE_TEST_LIVE_HYPRLAND=1 .venv/bin/python tests/live/hyprland/check_glow_renderer.py --out-dir /tmp/pcbridge-glow-evidence'`
  — final run pass; pixel falloff, focus, geometry, and teardown evidence above.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge/rust && time PCBRIDGE_TEST_HYPRLAND_GLOW=1 cargo test -p pcbridge-native --locked --test hyprland_glow_live -- --nocapture'`
  — pass; 13.59 s native probe, CPU/wall measurements above.

**Review and corrections:** The first quantitative run used GTK's drawing ACK
as a displayed baseline, so it captured the fading wallpaper before the fixture
had reached fullscreen. It correctly failed the expected pixel comparison.
The probe now waits for the actual fixture background before starting native
glow. Reviewed bounded SHM, release ownership, empty input regions, lack of
input objects, presentation freshness, panel-reservation semantics, and cleanup.
Actual click/drag/scroll delivery is deliberately still an acceptance test,
not inferred from flags or from unchanged focus.

**Open questions and next stage:** Bind the native frame owner and its fresh
presentation health to exact grant ID/epoch/session. Check that health in the
shared Python gate and native lifecycle before enabling control. Exercise
expiry, explicit revoke, replacement, helper/daemon failure, monitor rebuild,
and invisible-grant prevention; then test input transparency with real counters.

**Local commit:** `13d1de9`.

## Stage 7b1: Grant-bound native frame ownership and health

**Objective:** Own the drawing-only frame for an exact lease and make missing,
stale, foreign, stopped, or dead presentation evidence unavailable to both
Python and native consumers. Production Hyprland control remains prohibited
until the following shared-gate integration stage.

**Design decisions:** A private native `glow-watch STATE_DIR GRANT_ID EPOCH`
mode owns the surfaces. It checks the current lease, authoritative unlocked
state, direct parent identity, and monitor table. Its bounded private health
record binds version, grant/epoch, selected display/instance, PID/start ticks,
parent PID/start ticks, topology, strip count, and presentation time. Readers
also check the actual executable inode/owner, command-line grant/epoch/path,
selected environment, private regular file, and process relationship. Health
is visibility evidence, never lease authorization. Records older than 1000 ms
are unavailable. A timer cannot refresh unchanged presentation evidence.
Track buffer generation and submission time, so delayed presentation callbacks
cannot bless a newer transparent buffer or acquire a new timestamp.

Serialize publication with the same lease lock used by Python's `flock` and
check exact identity under that lock. This prevents an old observer's delayed
shutdown from overwriting a replacement grant. The core identity-only method
explicitly does not authorize expired grants. Monitor changes invalidate health
before reconnecting and fading in the new surfaces. Lock/UNKNOWN state clears
health and draws transparent; lease loss or owner death closes the frame.

**Files changed:** Python `desktop/glowstate.py`; native Linux `glow_state.rs`,
`glow_watch.rs`, renderer presentation handling, module exports, and private
command dispatch; core lease lock/identity API; shared health fixture and
Python/Rust contracts; VM `check_glow_owner.py`; measured facts and this journal.
The idle helper's existing process-start function is only made crate-visible.

**Measurements:** Eight strips became healthy with first record age 76 ms in
the final VM run (41 and 52 ms in earlier passing runs). Wrong grant, epoch,
and selected instance were unavailable. SIGSTOP for 1300 ms made health
unavailable; resumed real presentation restored it. Holding Python's lease
flock for 1300 ms prevented native publication; the stale publication failed
native self-validation and the helper exited, proving Linux lock interoperability
and conservative freshness. A new observer under the still-active scratch
identity worked. Fractional scale 1.25/rotation 1 rebuilt eight strips in the
same process, then restored fresh presentation against the actual monitor
table. Replacement, explicit revoke, three-second expiry, SIGKILL of the helper,
and death of its independent owner all removed their layers. Expected diagnostic
exits were `Native frame presentation record could not be validated` for the
deliberately blocked writer and `Frame owner process ended` for parent death.
These are synthetic drawing leases in a scratch directory, not production
desktop-unlock or input/capture acceptance.

**Tests run:**

- `(cd rust && cargo fmt --all && cargo build -p pcbridge-native --locked && cargo test -p pcbridge-native --locked --test glow_health)`
  — pass before adding the contention regression (one shared-fixture test).
- `(cd rust && cargo fmt --all && cargo test -p pcbridge-native --locked --test glow_health)`
  — pass, two tests including delayed-old-writer/replacement contention.
- `./.venv/bin/python -m unittest tests.contracts.test_glow_health tests.contracts.test_idlewatch tests.contracts.test_hyprland_state`
  — pass, 11 tests, including 23 shared health fixture cases.
- `./.venv/bin/python -m py_compile tests/live/hyprland/check_glow_owner.py`
  — pass.
- `(cd rust && cargo test --workspace --locked --no-fail-fast > /tmp/pcbridge-hyprland-stage7b1-rust.log 2>&1)`
  — pass, exit 0; unselected live paths remain opt-in.
- `scripts/dev/hyprland-vm.sh sync` — pass.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge/rust && cargo build -p pcbridge-native --locked'`
  — pass.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && PCBRIDGE_TEST_LIVE_HYPRLAND=1 .venv/bin/python tests/live/hyprland/check_glow_owner.py'`
  — final runs pass, observable cases above. The first run failed an assumed
  equality with the original topology after restoring geometry.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && PCBRIDGE_TEST_LIVE_HYPRLAND=1 .venv/bin/python tests/live/hyprland/check_glow_owner.py && PCBRIDGE_TEST_LIVE_HYPRLAND=1 .venv/bin/python tests/live/hyprland/check_glow_renderer.py --out-dir /tmp/pcbridge-glow-stage7b1-evidence'`
  — pass. Reference edge/falloff pixels on both outputs, unchanged application
  focus/geometry, breathing/fade, and complete teardown remained correct.
- `git diff --cached --check` — pass. American English spelling scan of the new
  Python/Rust owner/health code found no British spellings.

**Review and plan adjustment:** The restore test now verifies actual restored
geometry and Python/native topology parity; it cannot assume focus stayed on
the original output. An independent VM `hl.dsp.focus({ monitor = "Virtual-1" })`
measurement changed only the canonical primary bits, from
`v1|0,0,1280,800,1.0000,0,0|1280,0,1280,800,1.0000,0,1` to
`v1|0,0,1280,800,1.0000,0,1|1280,0,1280,800,1.0000,0,0`.
This exposes a real integration risk: focused-monitor state is transient,
unlike GNOME/KDE primary-output configuration. Add a small separately tested
coordinate-contract correction before gate integration, preserving truthful
focused/default selection while keeping physical topology stable under focus.

**Open questions:** Shared-gate/native-watchdog health enforcement, actual
desktop-unlock lifecycle, all-edge input transparency, compositor stall,
presentation/performance limits, and complete capture/input acceptance remain.

**Local commit:** `a111ad0`.

## Stage 7b1.5: Separate monitor focus from physical topology

**Objective and plan adjustment:** Correct the measured transient-primary
problem before connecting frame health to protected operations. A pointer
crossing outputs must not look like a physical display change.

**Design:** Add optional neutral `primary_is_focus` metadata, false by default
and true only in the Hyprland monitor adapter. Preserve the existing `primary`
selection as the actual focused/default output. It contributes zero to the
canonical configured-primary bit when focus-derived; the string format stays
the same. GNOME/KDE retain their existing configured-primary behavior and
fixture topology strings. Native read-only monitor metadata exposes the flag;
no second coordinate space or session lookup is added to the pure resolver.

**Files changed:** Python monitor model/resolver/adapter/topology; Rust core
display model/resolver/topology and native display adapter/metadata; existing
geometry literal fixtures, shared Hyprland transport fixture, Python/Rust
focus regression tests, VM owner probe, coordinate fixture test isolation,
native protocol/measured facts, and this journal.

**Measurements and review:** Before the fix, both new regression tests failed
because focus alone changed the topology primary bits. After the fix, real VM
default selection followed Virtual-1 and Virtual-2 while native frame health
remained available, checked every 50 ms for 1.2 seconds on each output. Native
and Python agreed on `v1|0,0,1280,800,1.0000,0,0|1280,0,1280,800,1.0000,0,0`.
The same probe still rebuilt fractional/rotated geometry and passed every
owner lifecycle case. Review confirmed configured-primary changes still
invalidate GNOME/KDE topology and that loaded shots carry their saved topology.

**Tests run:**

- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_monitors`
  — expected pre-fix failure, 1 of 6 tests.
- `(cd rust && cargo test -p pcbridge-native --locked --test display_contract hyprland_focus_changes_default_selection_without_invalidating_geometry)`
  — expected pre-fix failure, actual differing canonical strings.
- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_monitors tests.contracts.test_glow_health`
  — pass, 9 tests after the correction.
- `(cd rust && cargo fmt --all && cargo test -p pcbridge-native --locked --test display_contract && cargo test -p pcbridge-core --locked --test geometry --test layout_matrix)`
  — pass, 11 display, 6 geometry, and 4 layout tests.
- `(cd rust && cargo fmt --all && cargo test --workspace --locked --no-fail-fast > /tmp/pcbridge-hyprland-focus-topology-rust.log 2>&1)`
  — pass, exit 0.
- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_monitors tests.contracts.test_display_contract tests.contracts.test_coordinate_contract tests.contracts.test_coordinate_v2 tests.contracts.test_shot_layout tests.contracts.test_shot_artifacts`
  — final pass, 63 tests. The first combined run found an older coordinate
  fixture test consulting a 2560x800 monitor cache left by the display suite,
  while its own shot fixture expected 3840x1080. It now mocks its own monitor
  table, matching the other coordinate fixture tests; no host query is needed.
- `./.venv/bin/python tests/test_desktop.py > /tmp/pcbridge-hyprland-focus-topology-desktop.log 2>&1`
  — pass, 615 checks, no live input flags.
- `scripts/dev/hyprland-vm.sh sync` — pass.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge/rust && cargo build -p pcbridge-native --locked && PCBRIDGE_TEST_LIVE_HYPRLAND=1 PCBRIDGE_TEST_HYPRLAND_LOCKED=false cargo test -p pcbridge-native --locked --test hyprland_live -- --nocapture && cd ~/pcbridge && PCBRIDGE_TEST_LIVE_HYPRLAND=1 .venv/bin/python tests/live/hyprland/check_glow_owner.py'`
  — pass, two native observations and all owner cases, including continuous
  visibility across the two focused/default output transitions.

**Remaining work:** Shared-gate/native-watchdog integration is next; production
Hyprland input and capture still remain closed.

**Local commit:** `2544ff4`.

## Stage 7b2: Native lifecycle enforces visible frame health

**Objective:** Enforce exact fresh visibility before every native protected
operation and close native resources without waiting for another input call.
The Python Hyprland opening prohibition remains until the next stage.

**Design:** Production Hyprland lifecycle selects authoritative session state
and native frame health; a persisted lease alone is insufficient. Add an
independent 100 ms visibility watchdog so lock IPC stalls cannot delay held
input release. Reads and writes validate lease, lock, and visibility. Frame
loss returns existing typed `BACKEND_UNAVAILABLE`; replacement/revoke still
uses `REVOKED`. Resources registered after an already-delivered frame-loss edge
are closed immediately. The new visibility-injection factory is compiled only
with the explicit harness feature. Production IPC always selects the real
provider; other compositors retain their existing lifecycle.

**Files changed:** Native lifecycle, common presentation age constant,
NativeClient selected-instance allowlist, native desktop-state contracts,
Python native-client contract, VM guard probe, protocol/measured facts, journal.

**Measured corrections:** The selected instance signature was dropped by
NativeClient's environment allowlist. A fresh Python fake-helper contract failed
with `None` before adding that one allowlisted value; it now observes the exact
selected instance while the existing secret/FD isolation tests pass. The first
VM guard run closed the resumed owner because renderer proof was accepted for
1200 ms while the reader accepted 1000 ms. Unify them at the stricter 1000 ms:
on resumption old callbacks stay stale and new real presentation restores
readiness. A blocked health write still fails closed. A unit test initially
expected a kernel key name from `held()`, which correctly returns the public
alias `shift`; corrected the assertion and independently checked kernel events.

**Evidence:** The VM used a helper built with `test-harness`, running its real
production session/visibility providers and opening only `test.hold_resource`.
No capture or input was opened. Missing frame was refused. SIGSTOP closed that
resource at 1079 ms; SIGKILL closed it at 51 ms. Both refused subsequent opens.
Fresh resumed presentation allowed the same valid lease again. A replacement
frame could not rebind the old helper; a new helper bound it, and explicit
revoke closed the resource and frame. Native unit evidence recorded exactly
Shift down/up on frame loss while the lock watcher stalled 500 ms; release
completed within 250 ms. Late registration after loss was immediately closed.

**Tests run:**

- `(cd rust && cargo fmt --all && cargo test -p pcbridge-native --locked --features test-harness --test desktop_state --test native_revoke)`
  — pass, final targeted run 7 lifecycle and 7 revoke tests (the first lifecycle
  run exposed only the alias assertion described above).
- `./.venv/bin/python -m unittest tests.contracts.test_native_client`
  — pre-fix failure on selected instance, then pass, 22 tests. An earlier
  single-test invocation used the wrong unittest class name and reported a
  loader error before running a test; the full suite supplied the actual failure.
- `(cd rust && cargo fmt --all && cargo test -p pcbridge-native --locked --features test-harness --test desktop_state --test glow_health && cargo test -p pcbridge-native --locked --lib)`
  — pass, 7 lifecycle, 2 health, and 15 native unit tests.
- `./.venv/bin/python -m unittest tests.contracts.test_native_client tests.contracts.test_hyprland_session tests.contracts.test_glow_health`
  — pass, 29 tests.
- `./.venv/bin/python -m py_compile tests/live/hyprland/check_glow_guard.py`
  — pass.
- `scripts/dev/hyprland-vm.sh sync` — pass.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge/rust && cargo build -p pcbridge-native --locked --features test-harness && cd ~/pcbridge && PCBRIDGE_TEST_HYPRLAND_FRAME_GUARD=1 .venv/bin/python tests/live/hyprland/check_glow_guard.py'`
  — first run failed resumed-presentation readiness; final run passed all
  observable guard cases after the common-age correction.
- `(cd rust && cargo fmt --all && cargo test --workspace --locked --no-fail-fast > /tmp/pcbridge-hyprland-stage7b2-default.log 2>&1 && cargo test --workspace --locked --features pcbridge-native/test-harness --no-fail-fast > /tmp/pcbridge-hyprland-stage7b2-harness.log 2>&1)`
  — final pass for default and harness builds, exit 0. An earlier attempt to
  restrict the preexisting state-injection factory broke two default-build
  capture-session contracts at compile time. Retain that existing library
  contract hook; only the newly added visibility-injection factory needs the
  harness feature. Production IPC uses `Lifecycle::start` in both builds.

**Review and next design requirement:** CLI/TUI unlock currently creates and
closes a short-lived runtime. A frame owned by that caller would die as soon as
`pcbridge unlock` returned. The next integration must give the resident daemon
ownership and route the same CLI/TUI grant action through that shared gate,
with exact state-directory/session matching and safe refusal if ownership is
unavailable. No new user executable, public command, or reduced TUI is needed.

**Local commit:** `a376032`.

## Stage 7b3a: Resident Python ownership of one native frame

**Objective:** Build and independently verify the frame process owner before
opening the shared Hyprland gate. This stage still uses drawing-only leases.

**Design:** `FrameOwner` starts only the configured native executable, with
the same small environment allowlist as NativeClient, closed inherited file
descriptors, and exact canonical state directory/grant identity. It returns
only after trusted presentation health and a second lease validation. The
resident parent supervises child death every 100 ms without automatic restart.
Startup failure, cancellation, explicit close, and child death retire only the
captured lease using `LeaseStore.revoke_if` under the common lease lock. An
obsolete request is rejected before stopping a newer owner; an old parent's
close cannot revoke a replacement. Repeated opening of a living owner without
fresh proof closes that grant rather than restarting it. Shutdown permits the
native 500 ms fade and then bounds terminate/kill cleanup.

**Files changed:** New Python frame owner; conditional lease retirement;
extracted shared native environment filter; owner contracts; real VM owner
probe; measured facts; this journal.

**Measurements and review:** Two independent owners in the VM created eight
strips each. Reopening the current owner preserved its PID. Closing the old
owner preserved the replacement lease and its fresh presentation. Killing the
current child retired its lease in 101 ms in the final run (102 ms in the first
run). `/bin/false` could not report grant success and left no active lease or
layers. Contracts also cover startup replacement, timeout, cancellation, and
lost-proof reopening. The review checked cleanup on `BaseException`, bounded
process waits, exact conditional retirement, environment isolation, and absence
of input/capture authorization in this module.

**Tests run:**

- `./.venv/bin/python -m unittest tests.contracts.test_native_client tests.contracts.test_grant_cli`
  — pass, 29 tests.
- `./.venv/bin/python -m unittest tests.contracts.test_glow_owner tests.contracts.test_glow_health tests.contracts.test_native_client tests.contracts.test_batch_safety tests.contracts.test_grant_cli`
  — final pass, 79 tests; earlier incremental runs passed 77 and 78 tests.
- `./.venv/bin/python tests/test_desktop.py > /tmp/pcbridge-hyprland-stage7b3a-desktop.log 2>&1`
  — pass, 615 checks without live input flags.
- `./.venv/bin/python -m py_compile pcbridge/desktop/glowowner.py tests/live/hyprland/check_glow_manager.py`
  — pass.
- `git diff --check && git diff --cached --check` — pass.
- `scripts/dev/hyprland-vm.sh sync` — pass.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge/rust && cargo build -p pcbridge-native --locked && cd ~/pcbridge && PCBRIDGE_TEST_LIVE_HYPRLAND=1 .venv/bin/python tests/live/hyprland/check_glow_manager.py'`
  — final pass with the default native build, all observable owner cases.

**Remaining work:** Connect this owner to shared safety enforcement and the
resident CLI/TUI grant path. Production Hyprland control remains closed in
this commit. Actual edge input transparency, native capture, and input
acceptance remain required.

**Local commit:** `f13f9d0`.

## Stage 7b3b: Route the existing CLI/TUI grant to the resident daemon

**Objective and stage split:** Establish the resident communication path before
opening the shared Hyprland gate. Keep the existing `pcbridge`, CLI commands,
terminal UI, and MCP `desktop_unlock` action.

**Design:** Hyprland CLI/TUI unlock connects only to the resident Unix socket,
optionally activates the installed socket unit, and refuses an unavailable
owner. An opt-in relay handshake reports canonical state directory and the
authoritatively selected compositor/Wayland/Hyprland pair. Exact matching is
required before MCP initialization or unlock. The existing standard MCP tool
performs the grant; no second authorization endpoint is introduced. Bound
handshake/reply sizes, notification count, and request deadline. Connection
cleanup closes only the caller's socket, leaving daemon ownership intact.
GNOME/KDE retain their existing local CLI path.

**Files changed:** Session grant identity helper; relay handshake and daemon
metadata; new bounded resident CLI client; CLI routing; socket contracts and
real-daemon integration case; this journal.

**Tests run:**

- `./.venv/bin/python -m unittest tests.contracts.test_daemon_grant tests.contracts.test_grant_cli tests.contracts.test_relay tests.contracts.test_hyprland_session`
  — pass, 21 tests. Existing relay subprocess tests emit ResourceWarning for
  reader pipes; no test failure.
- `./.venv/bin/python -m unittest tests.integration.test_daemon.DaemonIntegrationTests.test_opt_in_grant_context_reports_the_resident_state_directory tests.integration.test_daemon.DaemonIntegrationTests.test_relay_session_is_served_by_the_daemon`
  — pass, 2 tests with throwaway desktop-disabled config/socket; the existing
  Client subprocess also emits a reader-pipe ResourceWarning.
- `./.venv/bin/python -m py_compile pcbridge/cli/daemon_grant.py pcbridge/daemon.py pcbridge/relay.py && git diff --check`
  — pass.

**Evidence and review:** Actual socket framing confirmed existing tool name,
duration, reason, UI client identity, and socket closure. Every identity-field
mismatch, old daemon metadata, oversized handshake/reply, unavailable daemon,
and typed tool refusal was rejected. A real resident daemon reported its own
canonical state directory; desktop control remained disabled in that test.
Review confirmed metadata contains no credentials, desktop environment is
never imported from relay clients, and no local fallback owns a short-lived
Hyprland frame.

**Remaining work:** Enable exact visible-frame enforcement in the shared gate,
known-idle enforcement under explicit force, provider emergency release, and
real daemon/CLI grant lifecycle evidence. Production Hyprland unlock remains
blocked by the gate in this intermediate commit.

**Local commit:** `d287bb3`.

## Stage 7b3c1: Preserve the admitted native request identity

**Measured finding and plan adjustment:** Review found a replacement race
between shared-gate verification and native request metadata. Capture, input,
and accessibility adapters preferred `current_token()` over the already
admitted call's token. A parallel unlock could therefore make an old call
select the new grant's helper instead of preserving the identity it was
verified under. Correct this independently before enabling the shared gate.

**Design and files:** Prefer the captured call token for native request
metadata, with current lease fallback only outside admission. Native helpers
still validate their own bound lease before protected operations. Change the
three adapters in `pcbridge/desktop/backends/rust.py`, add the replacement
contract in `tests/contracts/test_native_grant_rebind.py`, and update this
journal. New admitted calls still obtain a new helper as before.

**Tests run:**

- `./.venv/bin/python -m unittest tests.contracts.test_native_grant_rebind.GrantBoundHelperTests.test_admitted_request_metadata_cannot_adopt_a_new_parallel_grant`
  — failed before the fix: actual `grant-2`, required captured `grant-1`.
- `./.venv/bin/python -m unittest tests.contracts.test_native_grant_rebind`
  — pass after the fix, 16 tests, including existing new-call helper rebinds.

**Review:** No native helper can rebind an old initialized lease. The correction
preserves that invariant in Python metadata and applies to GNOME/KDE as well
as Hyprland. Shared gate/frame integration remains the next commit.

**Local commit:** `8db6883`.

## Stage 7b3c2: Shared-gate visibility, idle, and resource lifecycle

**Objective:** Open a Hyprland grant only after actual native presentation,
then preserve all shared admission/per-action checks and emergency cleanup.

**Design:** The shared SafetyGate owns a lazy FrameOwner only when opening a
Hyprland grant. Preflight requires desktop enabled, known-unlocked lock, known
idle, and the configured native helper. Opening is serialized; explicit lock
still revokes the shared lease before waiting for ownership cleanup. Success
requires fresh trusted health and final safety validation; startup failure
conditionally retires only that token. Pending/success/denial/owner-exit events
are audited. Nonowner gate closure never retires a resident grant.

Every admission checks frame health; every admitted action checks the same
grant and known idle. Explicit force bypasses the known user's conflict
threshold, not UNKNOWN idle. Per-action verification does not repeat the
threshold/rate counter. Native lifecycle enforces known idle and releases
resources on observer loss independently of lock IPC. Python ResourceWatch
tracks the captured resource token, closes providers on failed non-sliding
health, and serializes cleanup with tracking a new call. Runtime shutdown
closes every provider even if owner cleanup fails. GNOME/KDE retain their
existing force/admission semantics.

CLI status pauses on missing trusted presentation. The resident CLI client
confirms exact returned lease identity and actual native frame health after
the MCP response; missing proof retires only that lease. Tools return typed
unlock refusals and snapshot one captured grant's metadata, never a mixture
of parallel grants. No new executable, command, TUI, or panel requirement.
Hyprland/UNKNOWN Python capture refuses before any legacy external screenshot
route. The Rust adapter requires native session startup before capture; this
currently reports the unimplemented native backend truthfully. Hyprland grant
responses no longer advertise the GNOME screenshot fallback.

**Files changed:** Shared safety/runtime/frame owner/resource watcher; CLI
grant state/confirmation; MCP grant result; native lifecycle/state contracts;
Python gate/runtime/confirmation/session contracts; native guard and resident
VM probes; capture route adapters and selection contracts;
security/protocol/measured-facts documentation; this journal.

**Measurements and corrections:** Actual VM CLI exit preserved eight strips
owned by the daemon PID. Replacement preserved exact identity; closing a
consumer left the resident frame untouched. Stopped presentation paused status
and refused window reads; killed helper retirement took 102 ms. The real
minimum 10-second sliding expiry, CLI lock, MCP lock, and parent SIGKILL all
closed control and removed layers. An initial 3-second test config was rejected
by existing validation before daemon startup; adjusted the probe to the real
minimum. A contract probing pending admission cleared its ContextVar during
startup; success now explicitly restores its captured token. Earlier session
tests expected the temporary ValueError prohibition; they now check UNKNOWN's
typed refusal and dedicated Hyprland safety cases.
The expanded capture suite failed two KDE fixtures, also in isolation: they
mocked `is_kde()` while the explicit compositor selection now uses `current()`.
Updated those fixtures to the KWin descriptor. The final resident probe at the
current source revision measured killed-helper retirement at 101 ms.

**Tests run:**

- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_gate tests.contracts.test_daemon_grant tests.contracts.test_glow_owner tests.contracts.test_runtime_contract tests.contracts.test_batch_safety tests.contracts.test_grant_cli tests.contracts.test_hyprland_session tests.contracts.test_mcp_errors tests.contracts.test_native_grant_rebind tests.contracts.test_capture_contract tests.contracts.test_capture_backend_selection > /tmp/pcbridge-hyprland-gate-final-contracts.log 2>&1`
  — failed two stale KDE fixtures before correction; pass afterward, 151 tests.
- `./.venv/bin/python -m unittest tests.contracts.test_capture_backend_selection > /tmp/pcbridge-hyprland-capture-selection-isolated.log 2>&1`
  — reproduced the same two failures among 27 tests before fixture correction.
- `(cd rust && cargo fmt --all && cargo test -p pcbridge-native --locked --features test-harness --test desktop_state --test native_revoke)`
  — pass, 8 state and 7 revoke contracts. Recording keyboard released Shift
  on unknown idle within 250 ms; late registration was closed immediately.
- `(cd rust && cargo test --workspace --locked --no-fail-fast > /tmp/pcbridge-hyprland-gate-default-rust.log 2>&1 && cargo test --workspace --locked --features pcbridge-native/test-harness --no-fail-fast > /tmp/pcbridge-hyprland-gate-harness-rust.log 2>&1)`
  — pass, both builds, exit 0.
- `./.venv/bin/python tests/test_desktop.py > /tmp/pcbridge-hyprland-gate-desktop.log 2>&1`
  — pass, 615 checks without live input flags.
- `scripts/dev/hyprland-vm.sh sync` — pass.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge/rust && cargo build -p pcbridge-native --locked && cd ~/pcbridge && PCBRIDGE_TEST_HYPRLAND_GRANT_LIFECYCLE=1 .venv/bin/python tests/live/hyprland/check_grant_lifecycle.py'`
  — first run stopped at the invalid 3-second config; final rerun of the
  probe at 10 seconds passed every actual CLI/daemon lifecycle case.
- `scripts/dev/hyprland-vm.sh sync && scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && PCBRIDGE_TEST_HYPRLAND_GRANT_LIFECYCLE=1 .venv/bin/python tests/live/hyprland/check_grant_lifecycle.py' > /tmp/pcbridge-hyprland-gate-vm-final.log 2>&1`
  — pass at the final source revision; eight strips, every lifecycle case above.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge/rust && cargo build -p pcbridge-native --locked --features test-harness && cd ~/pcbridge && PCBRIDGE_TEST_HYPRLAND_FRAME_GUARD=1 .venv/bin/python tests/live/hyprland/check_glow_guard.py && cd rust && cargo build -p pcbridge-native --locked'`
  — pass, real lock/idle/frame providers and only the test resource flag;
  stale frame closed at 1029 ms, killed frame at 103 ms. Default build restored.
- `cargo fmt --all --check --manifest-path rust/Cargo.toml` — pass.
- `./.venv/bin/python -m py_compile pcbridge/desktop/safety.py pcbridge/desktop/resourcewatch.py pcbridge/desktop/runtime.py pcbridge/desktop/backends/python.py pcbridge/desktop/backends/rust.py pcbridge/cli/daemon_grant.py pcbridge/cli/grant.py pcbridge/tools.py tests/live/hyprland/check_grant_lifecycle.py tests/live/hyprland/check_glow_guard.py` — pass.
- `git diff --check` — pass.

**Review and remaining work:** Checked exact-token ownership/conditional revoke,
lease-before-cleanup ordering, no sliding in watchdogs, no unknown-idle force
bypass, native/Python held-resource cleanup, typed startup errors, and metadata
confirmation. Platform support is still not claimed. Fresh physical topology
validation, actual all-edge input transparency, native capture, acting focus,
uinput/clipboard acceptance, doctor/setup/TUI fixes, and full platform
regression remain required.

**Local commit:** `6eb69c0`.

## Stage 7b4: Current output coverage before protected operations

**Objective and measured risk:** A trusted record can still be fresh for up to
1000 ms after geometry changes. Its geometric topology also omits connector
names intentionally. Admission must reject old output coverage immediately,
including output replacement/swaps with the same geometric string.

**Design:** Version 2 private frame health includes connectors in canonical
monitor order and requires exactly four strips per output. Old records fail
closed. The owner rebuilds on geometry or ordered output changes. Shared
admission, per-action verification, final unlock validation, and returned grant
metadata compare current uncached geometry/identities with fresh trusted proof.
Native protected operations perform the same comparison against the selected
socket with a 200 ms absolute query deadline. Independent cleanup watchers
retain their IPC-free proof reads, so a stalled compositor cannot delay revoke
or held-resource cleanup. Focus remains excluded from physical topology.

**Files changed:** Python glow health/shared gate and contracts; shared health
fixture; Rust health/owner/lifecycle/IPC deadline and contracts; VM native guard;
security/protocol/measured-facts documentation; this journal.

**Measurements and corrections:** The new coverage test first failed because
the comparison did not exist. The VM stopped only the probe frame writer and
changed output 2 to scale 1.25/transform 1 while its old proof was still fresh.
Native and Python admission refused in 53 ms initially, 58 ms in the final run.
Resumption rebuilt current proof; both original outputs were restored. Final
stale/dead-frame resource closure measured 1027/52 ms. No input/capture opened.
Review changed unordered connector comparison to canonical-order comparison
to catch swapped outputs with identical geometric topology. It also removed
unused snapshot assignments after the owner's new comparison. A newly inserted
test initially inherited an old paused-status assertion after conditional
retirement; moved that assertion back to its original frame-loss case.

**Exact verification commands:**

- `./.venv/bin/python -m unittest tests.contracts.test_glow_health.GlowHealthTests.test_output_identity_and_geometry_must_cover_the_current_table`
  — initial expected failure, missing coverage function.
- `./.venv/bin/python -m unittest tests.contracts.test_glow_health tests.contracts.test_hyprland_gate tests.contracts.test_hyprland_monitors tests.contracts.test_glow_owner tests.contracts.test_daemon_grant tests.contracts.test_batch_safety tests.contracts.test_runtime_contract > /tmp/pcbridge-hyprland-topology-python.log 2>&1`
  — pass after the assertion correction, 85 tests.
- `cargo test --manifest-path rust/Cargo.toml -p pcbridge-native --locked --features test-harness --test desktop_state --test glow_health > /tmp/pcbridge-hyprland-topology-rust.log 2>&1`
  — pass, 9 state and 3 health contracts.
- `cargo test --manifest-path rust/Cargo.toml -p pcbridge-native --locked --lib platform::linux::hyprland > /tmp/pcbridge-hyprland-topology-deadline.log 2>&1`
  — pass, 2 deadline/size contracts.
- `cargo test --manifest-path rust/Cargo.toml --workspace --locked --no-fail-fast > /tmp/pcbridge-hyprland-topology-workspace.log 2>&1`
  — pass, default workspace, exit 0.
- `cargo test --manifest-path rust/Cargo.toml --workspace --locked --features pcbridge-native/test-harness --no-fail-fast > /tmp/pcbridge-hyprland-topology-harness-workspace.log 2>&1`
  — pass, harness workspace, exit 0.
- `cargo fmt --all --check --manifest-path rust/Cargo.toml && cargo test --manifest-path rust/Cargo.toml -p pcbridge-native --locked --test glow_health --lib > /tmp/pcbridge-hyprland-topology-default-final.log 2>&1`
  — pass at the final Rust revision, 16 library and 3 health tests.
- `scripts/dev/hyprland-vm.sh sync && scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge/rust && cargo build -p pcbridge-native --locked --features test-harness && cd ~/pcbridge && PCBRIDGE_TEST_HYPRLAND_FRAME_GUARD=1 .venv/bin/python tests/live/hyprland/check_glow_guard.py && cd rust && cargo build -p pcbridge-native --locked' > /tmp/pcbridge-hyprland-topology-vm-final.log 2>&1`
  — pass, current coverage/stale/death/replacement/revoke, default binary restored.
- `./.venv/bin/python -m py_compile pcbridge/desktop/glowstate.py pcbridge/desktop/safety.py tests/live/hyprland/check_glow_guard.py` — pass.
- `git diff --check` — pass.

**Review and remaining work:** Protected authorization still validates lease,
lock, idle, and exact native owner; output proof adds no bypass or permission.
Current native capture is still unavailable. Actual input transparency,
capture/focus/uinput/clipboard acceptance, setup/TUI, and full GNOME/KDE
regression remain required before platform support or push.

**Local commit:** `d70ce51`.

## Stage 8a: Native image-copy transport and verified output pixels

**Objective/design:** Capture inside the exact grant-bound helper using the
installed compositor's ext-image-copy and output-source protocols. Every
request owns a new session and its first frame, avoiding old-frame reuse and
indefinite later-frame damage waits. Registry/sync/constraint/frame waits share
an absolute deadline and watchdog cancellation generation. Private create-new
0600 SHM files are immediately unlinked; dimensions/formats are validated
before allocation against the existing core limit. Normalize advertised
transforms, release the session before encoding, then recheck authorization.
Temporary observer recovery permits a new verified call, never the old call.
No dependency or permission changes.

Primary sources checked against installed XML and the compositor tag:
[image-copy protocol](https://raw.githubusercontent.com/wayland-mirror/wayland-protocols/main/staging/ext-image-copy-capture/ext-image-copy-capture-v1.xml),
[output source](https://raw.githubusercontent.com/wayland-mirror/wayland-protocols/main/staging/ext-image-capture-source/ext-image-capture-source-v1.xml),
[Hyprland 0.56.2 implementation](https://raw.githubusercontent.com/hyprwm/Hyprland/v0.56.2/src/protocols/ImageCopyCapture.cpp),
[frame implementation](https://raw.githubusercontent.com/hyprwm/Hyprland/v0.56.2/src/managers/screenshare/ScreenshareFrame.cpp).

**Files:** New Rust image-copy transport/pixel contracts, capture backend,
dispatch/current snapshot handling, dedicated VM pixel probe, native/security/
measured-facts documentation, and this journal.

**Measurements/corrections:** Initial compile caught a missing explicit
FrameError conversion. Initial VM capture verified five images, but post-lock
returned DISPLAY_CHANGED because topology comparison preceded lease checking;
capture/session now validate the lease first. Review found native cached
geometry could label a new frame incorrectly; Hyprland requests now invalidate
that cache, and the probe verifies returned desktop rectangles/topology.
Both outputs passed exact magenta/cyan markers and counters 631/632 at
1280x800. Scale 1.25/transform 1 passed correctly oriented 800x1280 and counter
633. Ready waits were 18–28 ms, total calls 218–247 ms. Inspected the rotated
PNG visually. Images remain at `~/pcbridge-evidence/native-capture/` in the VM.
Post-lock capture returned REVOKED. No input or private config was used.

**Exact tests:**

- `cargo check --manifest-path rust/Cargo.toml -p pcbridge-native --locked > /tmp/pcbridge-hyprland-image-copy-check.log 2>&1`
  — initial conversion error above; subsequent workspace build passed.
- `cargo test --manifest-path rust/Cargo.toml -p pcbridge-native --locked --lib platform::linux::image_copy --test image_copy > /tmp/pcbridge-hyprland-image-copy-tests.log 2>&1`
  — 2 deadline/cancellation library tests passed; the filter selected zero
  external pixel tests, so ran them separately below.
- `cargo test --manifest-path rust/Cargo.toml -p pcbridge-native --locked --test image_copy > /tmp/pcbridge-hyprland-image-copy-pixels.log 2>&1`
  — 3 contracts passed: formats/dimensions, asymmetric pixels under all eight
  transforms, malformed owned frames.
- `cargo test --manifest-path rust/Cargo.toml --workspace --locked --no-fail-fast > /tmp/pcbridge-hyprland-native-capture-workspace.log 2>&1`
  — pass, default workspace, exit 0.
- `git add rust/crates/pcbridge-native/src/platform/linux/image_copy.rs rust/crates/pcbridge-native/tests/image_copy.rs tests/live/hyprland/check_native_capture.py && scripts/dev/hyprland-vm.sh sync && scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge/rust && cargo build -p pcbridge-native --locked && cd ~/pcbridge && PCBRIDGE_TEST_HYPRLAND_NATIVE_CAPTURE=1 .venv/bin/python tests/live/hyprland/check_native_capture.py --out-dir ~/pcbridge-evidence/native-capture' > /tmp/pcbridge-hyprland-native-capture-vm.log 2>&1`
  — initial post-lock error above; pass afterward, five verified images.
- `cargo fmt --all --check --manifest-path rust/Cargo.toml` — pass.
- `./.venv/bin/python -m py_compile tests/live/hyprland/check_native_capture.py` — pass.
- `git diff --check && git diff --cached --check` — pass.

**Review/remaining work:** Checked allocation/metadata bounds, private files,
selected session, cancellation, producer release, pre/post authorization.
No external process or broad interpreter permission. Python capabilities,
shot/window integration, revoke during capture, and actual input transparency
remain the next stages; complete platform acceptance/regression remains pending.

**Local commit:** `6a89396`.

## Stage 8b: Native readiness, shared shots, and resident MCP delivery

**Objective/design:** Integrate the image-copy backend with ordinary Python
capture and the unchanged MCP tool. Read-only registry discovery reports
actual protocol availability without allocating capture buffers or opening
a grant. Use a separate capability helper so it cannot bind the later
grant-bound capture helper to an absent grant. Protocol absence and old
helpers fail closed. Hyprland uses the shared shot crop/scale/metadata and
coordinate contract. Window shots use fresh compositor geometry and exact
identity checks. Rendering/publication retains one admitted token, validates
current outputs and window state, and withdraws new artifacts on failure.
GNOME/KDE keep their existing publication behavior.

**Files:** Python native adapter/shared capture/window geometry, Rust native
readiness/protocol discovery/session startup/dispatch, capture and window
contracts, native/resident VM probes, native capture/security/measured-facts
documentation, and this journal. No dependencies or permissions changed.

**Measurements/corrections:** The initial focused suite command named a
nonexistent `tests.contracts.test_coordinates`; corrected it to the actual
coordinate modules. The final combined VM probe passed native and Python
shots but failed a later resident `window_list`. Reproduced twice with
structured response and actual compositor records. IDs `1800001e`/`1800001f`
were rejected by the earlier decimal-only regex. The tagged Hyprland
implementation formats this field with `{:x}`. A regression with these IDs
failed before the fix, then passed with bounded hexadecimal validation.
Window geometry also rejects nonfinite, Boolean, and oversized coordinates
with a typed refusal. The final focused suite has 122 passing tests.

Final VM evidence: five native frames retained counters 631/632/633 and
different magenta/cyan markers, ready waits 18.5–20.0 ms and total calls
219–248 ms. Python shots were 640x400 and 400x640; their centers mapped to
(640,400) and (1600,512). Window-region capture retained counter 633.
Revoke after the first publication link withdrew all new PNG/metadata.
The actual resident MCP tool delivered two decoded 640x400 images with
counter 871 and different output markers. Replacement, stale proof,
nonowner close, sliding expiry, CLI/MCP lock, and parent death passed.
Killed-frame lease retirement measured 101 ms. Default native binary used;
no input or real private config read. Probe cleanup closed grants/resources.

**Exact verification commands:**

- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_windows.HyprlandWindowTests.test_runtime_hexadecimal_stable_ids_are_preserved > /tmp/pcbridge-hyprland-stable-id-red.log 2>&1`
  — expected failure before the hexadecimal correction.
- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_capture tests.contracts.test_capture_contract tests.contracts.test_capture_backend_selection tests.contracts.test_hyprland_gate tests.contracts.test_hyprland_windows tests.contracts.test_kwin tests.contracts.test_coordinate_contract tests.contracts.test_coordinate_v2 tests.contracts.test_capture_region tests.contracts.test_display_contract tests.contracts.test_runtime_contract > /tmp/pcbridge-hyprland-shot-final-contracts.log 2>&1`
  — pass, 122 tests at the final Python revision.
- `./.venv/bin/python tests/test_desktop.py > /tmp/pcbridge-hyprland-shot-desktop.log 2>&1`
  — pass, 615 checks, no live input flags.
- `cargo test --manifest-path rust/Cargo.toml --workspace --locked --no-fail-fast > /tmp/pcbridge-hyprland-shot-workspace.log 2>&1`
  — pass, all default workspace targets, exit 0.
- `scripts/dev/hyprland-vm.sh sync && scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && PCBRIDGE_TEST_HYPRLAND_NATIVE_CAPTURE=1 .venv/bin/python tests/live/hyprland/check_native_capture.py --out-dir ~/pcbridge-evidence/native-capture && PCBRIDGE_TEST_HYPRLAND_GRANT_LIFECYCLE=1 PCBRIDGE_TEST_HYPRLAND_RESIDENT_CAPTURE=1 .venv/bin/python tests/live/hyprland/check_grant_lifecycle.py' > /tmp/pcbridge-hyprland-shot-vm-verified.log 2>&1`
  — pass at the final source revision, all evidence above. The earlier same
  probe in `/tmp/pcbridge-hyprland-shot-vm-final.log` failed at window identity.
- `cargo fmt --all --check --manifest-path rust/Cargo.toml` — pass.
- `./.venv/bin/python -m py_compile pcbridge/desktop/capture.py pcbridge/desktop/backends/rust.py pcbridge/desktop/hyprland_windows.py tests/contracts/test_hyprland_capture.py tests/contracts/test_hyprland_windows.py tests/live/hyprland/check_native_capture.py tests/live/hyprland/check_grant_lifecycle.py` — pass.
- `git diff --check && git diff --cached --check` — pass.

**Review/remaining work:** Checked that discovery creates no image source,
capture retains native authorization, publication uses the original token,
failure withdraws only new artifacts, and missing focus/layout is typed.
No new trust boundary, external screenshot process, or dependency. Acting
focus, actual uinput/clipboard/pointer-lock, glow input transparency, portal
environment, TUI/doctor/setup, full geometry/performance acceptance, and
GNOME/KDE regression remain pending. Hyprland support and push are not claimed.

**Local commit:** `47d550d`.

## Stage 9a: Guarded compositor window activation

**Objective/design:** Use exact runtime identities for normal MCP and batch
focus without requiring uinput or AT-SPI. Rank human names safely; ties refuse
before dispatch. Installed application identity matching handles localized
names without launching a duplicate. Recheck selected identity and the shared
admission before dispatch, then verify fresh compositor focus. A shared runtime
checkpoint refers to the original sequence guard and expires with the execution
slot, even in copied task contexts. It never captures a replacement token.
Closed applications retain direct installed-entry launching; Hyprland never
types guessed search keys. Only generated, bounded identity selectors enter
the dispatcher, with no shell or arbitrary caller-provided Lua.

**Measured design adjustment:** Hyprland 0.55/0.56 source supports both Lua
and legacy config managers. The actual session's read-only `status` reports
`configProvider: lua`; use this field rather than inferring dispatcher syntax
from version or parsing config. Pin status discovery to the already selected
instance/Wayland pair. Unknown providers fail closed, and an unsuccessful
dispatch does not trigger another mutating fallback.

**Files:** Hyprland IPC/window provider, shared apps/runtime/DeviceOps, MCP
and pcb-do adapters, focused contracts, resident VM probe, VM xterm test
dependency, architecture/security/measured-facts documentation, and journal.

**Corrections/evidence:** New focus contracts initially failed because the
adapter/checkpoint did not exist. Review added bounded selector checks,
deadline verification after runtime discovery, expired-context refusal, and
localized installed-app matching. The first actual MCP run verified monitor
0/workspace 1 and monitor 1/workspace 2 in 151/176 ms. The expanded probe
initially failed with a KeyError: it assumed an active baseline window after
the previous pattern closed. It now uses a known mapped VM client instead.
The expanded rerun passed both outputs, ambiguous-title refusal with unchanged
focus, normal batch focus, a special-workspace foot client, and real XWayland
xterm focus. The combined capture/focus run also decoded two counter-871 MCP
images and retained every prior grant lifecycle assertion. No key, pointer,
or clipboard input was requested. Test clients/grants/resources were closed.

**Exact tests:**

- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_focus > /tmp/pcbridge-hyprland-focus-red.log 2>&1`
  — expected failure before implementation.
- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_focus tests.contracts.test_window_focus tests.contracts.test_window_operations tests.contracts.test_kde_windows tests.contracts.test_runtime_contract tests.contracts.test_batch_safety tests.contracts.test_hyprland_gate tests.contracts.test_hyprland_windows tests.contracts.test_execution_paths > /tmp/pcbridge-hyprland-focus-contracts-final.log 2>&1`
  — pass, 157 tests.
- `./.venv/bin/python tests/test_desktop.py > /tmp/pcbridge-hyprland-focus-desktop.log 2>&1`
  — pass, 615 checks without live input flags.
- `scripts/dev/hyprland-vm.sh ssh 'sudo pacman -S --noconfirm --needed xterm' > /tmp/pcbridge-hyprland-xterm-install.log 2>&1`
  — pass; only the isolated VM was changed. Added xterm to VM provisioning.
- `scripts/dev/hyprland-vm.sh sync && scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && PCBRIDGE_TEST_HYPRLAND_GRANT_LIFECYCLE=1 PCBRIDGE_TEST_HYPRLAND_RESIDENT_CAPTURE=1 PCBRIDGE_TEST_HYPRLAND_RESIDENT_FOCUS=1 .venv/bin/python tests/live/hyprland/check_grant_lifecycle.py' > /tmp/pcbridge-hyprland-focus-vm-verified.log 2>&1`
  — combined resident capture/focus/lifecycle evidence above; final exit and
  measurements confirmed: exit 0, output focus 170/178 ms, special and
  XWayland exact focus, dead-frame lease retirement 102 ms.
- `./.venv/bin/python -m py_compile pcbridge/desktop/apps.py pcbridge/desktop/hyprland.py pcbridge/desktop/hyprland_windows.py pcbridge/desktop/runtime.py pcbridge/desktop/ops.py pcbridge/tools.py pcbridge/cli/do.py tests/live/hyprland/check_grant_lifecycle.py` — pass.
- `bash -n scripts/dev/hyprland-vm.sh` — pass.
- `git diff --check && git diff --cached --check` — pass.

**Review/remaining work:** Exact admission belongs to the active execution
slot; read-only context cannot grant control. IPC focus does not replace the
uinput model, weaken idle/lock/frame checks, or bypass content policy. Lua
focus has real VM evidence; hyprlang syntax has contract/source evidence and
still needs a live compatible session before a parity claim. VM uinput remains
root-only until the existing package rule is installed in the next stage.
Actual input/clipboard/lock-pointer and glow transparency, TUI/doctor/setup,
portal environment, full geometry/performance acceptance, and GNOME/KDE
regression remain required. No platform support claim or push yet.

**Local commit:** `0589f9d`.

## Stage 9b: Actual native input and all-edge glow transparency

**Objective/design:** Exercise the existing native uinput provider through the
shared gate and execution slot in the isolated VM. Fullscreen observer windows
report real application events on both outputs. The test uses only the public
example config and a temporary grant directory. It requires authoritative
unlocked/idle/frame state, verifies target coordinates before clicks, restores
the clipboard without printing its original content, and closes every grant,
input/capture resource, observer, and watcher in cleanup.

**Files:** `tests/live/hyprland/check_input.py`, measured facts, and this journal.
No production input implementation or host permissions changed.

**VM setup:** Installed the repository's existing udev and modules-load files
inside the VM. `/dev/uinput` measured `660 root:input`, with a `tester` user ACL
and no access for other users. This makes the same native input architecture
available; it does not relax the shared safety layers.

**Measurements/corrections:** All eight visible glow edges delivered click,
drag, and vertical scroll to the observer at the requested coordinates within
one pixel. Two native screenshot centers mapped through the shared conversion
boundary to (640,400) and (1920,400), with actual clicks received there. Relative
input of (100,0) moved the application pointer by 168 pixels on this VM's
accelerated pointer configuration; y did not change, the provider invalidated
its absolute position, and absolute recovery was within one pixel. Clipboard
typing delivered `PcBridge Hyprland Türkçe 42` exactly and restored the sentinel.
Shift+A reached GTK with Shift set; held Shift released 43 ms after revoke at the final revision (8–43 ms across passing runs),
without another action. A later native key request returned GRANT_REQUIRED.

The first test revisions corrected fixture configuration types (the native
binary path is a Path, shot dimensions come from Shot.size, and scale 0 means
full size). A revoked provider may refuse with GRANT_REQUIRED before sending a
request because lock clears its token; REVOKED is not the only correct result.
The reserved-area comparison initially failed: the stock autogenerated-config
warning reserves 52 pixels and moves its reservation on monitor focus changes
even without glow. A diagnostic screenshot showed the warning, and no-glow
focus changes reproduced the same swap. The test now compares before grant,
during glow, and after teardown with the same exact observer focus. Fullscreen
geometry and reserved areas were unchanged. A first screenshot-center move
produced no GTK motion event because `hyprctl cursorpos` confirmed the pointer
was already at (640,400). Two distinct safe initialization points now establish
an observed position before mapping tests. No production workaround was added.

**Exact tests:**

- `scripts/dev/hyprland-vm.sh ssh 'sudo install -m 644 ~/pcbridge/packaging/udev/60-pcbridge-uinput.rules /etc/udev/rules.d/60-pcbridge-uinput.rules && sudo install -m 644 ~/pcbridge/packaging/modules-load/pcbridge-uinput.conf /etc/modules-load.d/pcbridge-uinput.conf && sudo modprobe uinput && sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=misc --sysname-match=uinput && sudo udevadm settle && stat -c "%a %U %G %n" /dev/uinput && getfacl -p /dev/uinput' > /tmp/pcbridge-hyprland-uinput-setup.log 2>&1`
  — pass; VM-only setup and mode/ACL evidence above.
- `scripts/dev/hyprland-vm.sh sync && scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && PCBRIDGE_TEST_HYPRLAND_INPUT=1 .venv/bin/python tests/live/hyprland/check_input.py --out-dir ~/pcbridge-evidence/uinput-glow' > /tmp/pcbridge-hyprland-uinput-verified.log 2>&1`
  — final pass, exit 0, JSON application evidence above. Earlier logs
  `/tmp/pcbridge-hyprland-uinput-reserved-diagnostic.log`,
  `/tmp/pcbridge-hyprland-uinput-focus-matched.log`, and
  `/tmp/pcbridge-hyprland-uinput-cursor-diagnostic.log` preserve the failed
  reserved-area and no-op-motion hypotheses before correcting the probe.
- `./.venv/bin/python -m unittest tests.contracts.test_rust_pointer_provider tests.contracts.test_input_contract tests.contracts.test_clipboard_contract tests.contracts.test_native_clipboard tests.contracts.test_hyprland_gate tests.contracts.test_glow_owner tests.contracts.test_batch_safety > /tmp/pcbridge-hyprland-input-contracts-final.log 2>&1`
  — pass, 99 tests. The first invocation named a nonexistent
  test_hyprland_glow_owner module and failed; the corrected command ran the
  actual test_glow_owner module.
- `./.venv/bin/python tests/test_desktop.py > /tmp/pcbridge-hyprland-input-desktop.log 2>&1`
  — pass, 615 checks with no host live-input flags.
- `./.venv/bin/python -m py_compile tests/live/hyprland/check_input.py && git diff --check && git diff --cached --check`
  — pass.
- `./.venv/bin/python -O tests/live/hyprland/check_input.py --out-dir /tmp/pcbridge-hyprland-optimized-refusal > /tmp/pcbridge-hyprland-input-optimized.log 2>&1`
  — expected refusal, exit 1 before any observer, output directory, grant,
  watcher, or input is created. The output directory did not exist afterward.

**Review:** A separate spec reviewer required an active-glow geometry assertion;
that assertion and explicit two-shot/cleanup-focus checks were added, and the
VM rerun passed. A separate code-quality reviewer found that optimized Python
would remove the assertion-based containment checks. An unconditional early
refusal now prevents any optimized invocation. The refusal and normal VM run
were verified again before commit.

**Remaining work:** Touch transparency, pointer-lock relative movement,
additional buttons/counts/horizontal scroll and held-input causes, cursor
dispatcher diagnostics, full acceptance, portal/TUI/doctor/setup, and existing
platform regression remain required. This stage does not claim complete input
or Hyprland support.

**Local commit:** `1e319aa`.

## Stage 10a: Preserve the normal TUI with unreadable configuration

**Objective/design:** Fix the measured plain `pcbridge` missing-config crash
at the existing Settings pane boundary. Config lookup intentionally raises
SystemExit for CLI callers; Exception does not catch it. Handle both there,
keep the existing setup guidance and full UI, and leave invalid/missing config
actions disabled. No new config is created automatically. Hyprland gets an
explicit optional-panel note; it never reads or writes GNOME gsettings.

**Files:** TUI settings/backend, TUI settings/panel-icon contracts, measured
facts, and this journal. The implementation was delegated under the
subagent-driven-development workflow; spec and quality reviews were separate.

**Evidence:** The real Textual regression failed at the exact locator
SystemExit before the handler fix. It then passed in all four desktop kinds,
with Overview, Settings, Tools, Connections, and Commands accessible,
grant/restart attempts inert, Save/Discard disabled, and no files created.
An additional ConfigError case stayed visible without enabling control.
The old generic GNOME panel note failed the new Hyprland contract, then the
explicit optional-panel/glow note passed without gsettings access.

On the existing VM checkout, the empty-XDG plain executable reproduced the
SettingsPane -> Backend.editor -> ConfigEditor -> locate_config traceback and
exit 1. After sync, the identical command stayed open for the eight-second
observation (timeout exit 124), rendered all five normal tabs, setup guidance,
and the disabled Config error grant button; it emitted no traceback and left
the XDG directory absent. The configured literal `pcbridge` command, with the
checkout venv on PATH, also stayed open and rendered all five tabs without a
traceback. A missing path supplied through PCBRIDGE_CONFIG is a different
SettingsError path that was already handled; the first control observation
correctly stayed open before the fix. The original user's exact field error
is still unavailable, so the measured missing-config root cause is not claimed
as that user's diagnosis. No desktop input or host config/service was touched.

**Exact tests:**

- `./.venv/bin/python -m unittest tests.contracts.test_tui_settings.UnreadableConfigTests`
  — initial expected locator SystemExit failure in
  `/tmp/pcbridge-hyprland-tui-red.log`; green missing-config case passed across
  four desktops, `/tmp/pcbridge-hyprland-tui-green.log`.
- `./.venv/bin/python -m unittest tests.contracts.test_panel_icon.SettingsBackendTests.test_hyprland_explains_the_optional_icon_without_reading_or_writing_gsettings`
  — expected failure before the explicit note, then included in passing suites.
- `./.venv/bin/python -m unittest tests.contracts.test_tui tests.contracts.test_tui_settings tests.contracts.test_tui_tools tests.contracts.test_tui_connections tests.contracts.test_panel_icon`
  — pass, 39 tests; `/tmp/pcbridge-hyprland-tui-targeted.log`.
- `./.venv/bin/python -m unittest tests.contracts.test_tui_settings.UnreadableConfigTests tests.contracts.test_panel_icon.SettingsBackendTests`
  — pass, 6 tests including ConfigError; `/tmp/pcbridge-hyprland-tui-errors-panel.log`.
- `./.venv/bin/python -m unittest tests.contracts.test_settings tests.contracts.test_cli_settings tests.contracts.test_english_only`
  — pass, 44 tests; `/tmp/pcbridge-hyprland-tui-contracts.log`.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && timeout 8s script -q -e -c "env -u PCBRIDGE_CONFIG XDG_CONFIG_HOME=/tmp/pcbridge-tui-unconfigured .venv/bin/pcbridge" /tmp/pcbridge-tui-missing-before.typescript >/dev/null' > /tmp/pcbridge-hyprland-tui-before-no-config.log 2>&1`
  — expected exit 1 before fix; exact trace preserved in the VM transcript.
- `scripts/dev/hyprland-vm.sh sync && scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && timeout 8s script -q -e -c "env -u PCBRIDGE_CONFIG XDG_CONFIG_HOME=/tmp/pcbridge-tui-unconfigured .venv/bin/pcbridge" /tmp/pcbridge-tui-missing-after.typescript >/dev/null' > /tmp/pcbridge-hyprland-tui-after-no-config.log 2>&1`
  — expected timeout 124 with UI open; transcript content and absent config
  directory verified separately as described above.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && timeout 8s script -q -e -c "env PATH=\$PWD/.venv/bin:\$PATH pcbridge" /tmp/pcbridge-tui-configured-after.typescript >/dev/null' > /tmp/pcbridge-hyprland-tui-after-configured.log 2>&1`
  — expected timeout 124 with the configured normal UI open; all five tabs and
  no traceback verified in the transcript.
- `./.venv/bin/python tests/test_desktop.py > /tmp/pcbridge-hyprland-tui-desktop.log 2>&1`
  — pass, 615 checks without live-input flags.
- `./.venv/bin/python -m py_compile pcbridge/tui/backend.py pcbridge/tui/settings_pane.py tests/contracts/test_tui_settings.py tests/contracts/test_panel_icon.py`
  and `git diff --check` — pass.

The exact VM transcript assertions ran successfully (exit 0; JSON saved to
`/tmp/pcbridge-hyprland-tui-vm-evidence.log`):

```bash
scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && .venv/bin/python - <<'\''PY'\''
import json, re
from pathlib import Path
from pcbridge.tui.backend import Backend
for kind in ("missing", "configured"):
    screen = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", Path(f"/tmp/pcbridge-tui-{kind}-after.typescript").read_text(errors="replace"))
    assert "Traceback (most recent call last)" not in screen and "exception=SystemExit" not in screen
    assert all(label in screen for label in ("Overview", "Settings", "Tools", "Connections", "Commands"))
    if kind == "missing":
        assert "pcbridge setup" in screen and "Config error" in screen
assert not Path("/tmp/pcbridge-tui-unconfigured").exists()
print(json.dumps({"tui": "both_transcripts_validated", "config_created": False, "panel": Backend().panel_icon_status()}))
PY' > /tmp/pcbridge-hyprland-tui-vm-evidence.log 2>&1
```

**Remaining work:** Hyprland doctor/setup accuracy, portal/environment
diagnostics, remaining input/geometry/security acceptance, and GNOME/KDE
regression remain required. This is a TUI fix, not a supported-platform claim.

**Local commit:** `cfabe33`.

## Stage 10b: Diagnose the selected Hyprland session accurately

**Objective/design:** Keep the same doctor/setup commands while dispatching
GNOME, KDE, Hyprland, and UNKNOWN explicitly. Hyprland setup prints native
requirements and independent portal package guidance; it does not install a
GNOME extension, KWin permission, tray, or panel. Doctor observes the selected
IPC, tested-version set, default helper, grantless native protocol handshake,
authoritative lock, fresh idle, and exact active-grant presentation. Inactive
frame state is explicitly unverified. Static/socket-activated integration
services are inspected with is-active; enablement is not a capture requirement.

**Files:** CLI doctor/main/ops, new CLI Hyprland diagnostics, doctor/setup
contracts, new real doctor probe, measured facts, and this journal.

**Evidence:** Before sync, the VM doctor still reported missing GNOME Shell
and extension. After sync it reported selected Hyprland IPC, 0.56.2 as
untested pending full acceptance, the release packaged helper, supported
image-copy/output-source without capture, authoritative unlocked state,
missing idle as a failure, and inactive frame as unverified. The full CLI
doctor still exited 1 for installation/configuration requirements; this is
not a claim that the entire installation is ready.

The VM release build installed the standard ignored packaged helper and
reported release, test_harness=false, protocol 1.0. The build ID was
unknown-dirty because sync omits .git; this is not a commit-stamped artifact.
The real probe used default packaged discovery without a config/environment
binary override, public example settings without default-state write probes,
and temporary public lease state. Doctor created no grant or lockfile. A fresh
native watcher changed its idle diagnosis to known; a shared-gate grant then
provided trusted presentation on eight strips. Watcher death became unknown,
and a forced write returned the exact ACTIVITY_UNKNOWN code. Lock removed
the grant/frame; all probe cleanup completed. No input or capture was sent.

The portal measurement recorded all five user services active/running,
static portal units, socket-activated PipeWire, enabled WirePlumber, the
allowlisted manager environment, packaged default=hyprland;gtk, and all three
D-Bus owners. This does not establish effective routing or screen sharing.
Private portal configuration was not read. The VM uses the Arch cloud image
and stock packaged SDDM/Hyprland configuration rather than an archinstall
installation; that remains a documented difference from the target machine.

**Exact tests:**

- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_doctor_setup tests.contracts.test_cli tests.contracts.test_distro tests.contracts.test_capabilities tests.contracts.test_hyprland_session tests.contracts.test_hyprland_gate tests.contracts.test_desktop_gates > /tmp/pcbridge-hyprland-doctor-focused.log 2>&1`
  — pass, 70 tests. The new test was checked retrospectively against isolated
  HEAD Doctor.desktop below: one test, two expected failures (Hyprland and
  UNKNOWN wrongly invoking the GNOME extension). This was not chronological TDD.
- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_doctor_setup tests.contracts.test_cli_settings tests.contracts.test_distro tests.contracts.test_capabilities tests.contracts.test_hyprland_session tests.contracts.test_hyprland_gate tests.contracts.test_english_only > /tmp/pcbridge-hyprland-doctor-rechecked.log 2>&1`
  — pass, 58 tests, including English-only contracts.
- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_doctor_setup.LeaseFileTests > /tmp/pcbridge-hyprland-doctor-lease-red.log 2>&1`
  — expected two failures before the quality fix: a FIFO blocked until the
  isolated subprocess's two-second timeout and a symlink was incorrectly read.
  The third size/mapping/regular-file contract already passed.
- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_doctor_setup tests.contracts.test_cli tests.contracts.test_distro tests.contracts.test_capabilities tests.contracts.test_hyprland_session tests.contracts.test_hyprland_gate tests.contracts.test_desktop_gates > /tmp/pcbridge-hyprland-doctor-lease-focused.log 2>&1`
  — pass, 73 tests after nonblocking no-follow regular-file reads were added.
- `./.venv/bin/python tests/test_desktop.py > /tmp/pcbridge-hyprland-doctor-desktop.log 2>&1`
  — pass, 615 checks without live input.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && scripts/build-native.sh' > /tmp/pcbridge-hyprland-release-build.log 2>&1`
  — pass, release build in 1m03s; installed packaged helper, no service restart.
- `scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && .venv/bin/pcbridge doctor --json > /tmp/pcbridge-hyprland-doctor-before.json' > /tmp/pcbridge-hyprland-doctor-before.log 2>&1`
  — expected exit 1 before sync; wrong GNOME diagnostics reproduced.
- `scripts/dev/hyprland-vm.sh sync && scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && .venv/bin/pcbridge doctor --json > /tmp/pcbridge-hyprland-doctor-after.json' > /tmp/pcbridge-hyprland-doctor-after.log 2>&1`
  — expected overall exit 1; distinct accurate desktop observations above.
- `scripts/dev/hyprland-vm.sh sync && scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && PCBRIDGE_TEST_HYPRLAND_DOCTOR=1 .venv/bin/python tests/live/hyprland/check_doctor.py' > /tmp/pcbridge-hyprland-doctor-live-final.log 2>&1`
  — pass, exit 0 with machine-observed missing/fresh/dead idle, trusted active
  frame, exact force refusal, no capture, and final cleanup.
- `scripts/dev/hyprland-vm.sh session 'pacman -Q hyprland hypridle hyprlock xdg-desktop-portal xdg-desktop-portal-hyprland xdg-desktop-portal-gtk pipewire wireplumber && systemctl --user show xdg-desktop-portal.service xdg-desktop-portal-hyprland.service xdg-desktop-portal-gtk.service pipewire.service wireplumber.service -p Id -p ActiveState -p SubState -p UnitFileState -p MainPID && systemctl --user show-environment | sed -n -E "/^(XDG_CURRENT_DESKTOP|XDG_SESSION_TYPE|XDG_SESSION_DESKTOP|WAYLAND_DISPLAY|HYPRLAND_INSTANCE_SIGNATURE)=/p" && cat /usr/share/xdg-desktop-portal/hyprland-portals.conf && busctl --user --no-pager list | sed -n -E "/^org.freedesktop.(portal.Desktop|impl.portal.desktop.(hyprland|gtk))[[:space:]]/p"' > /tmp/pcbridge-hyprland-portal-services.log 2>&1`
  — pass, independent public integration evidence above.
- `scripts/dev/hyprland-vm.sh session 'systemctl --user show pipewire.socket -p Id -p ActiveState -p UnitFileState' > /tmp/pcbridge-hyprland-pipewire-socket.log 2>&1`
  — pass; pipewire.socket was active and enabled, independently of the
  disabled service's enablement state.
- `./.venv/bin/python -m py_compile pcbridge/cli/doctor.py pcbridge/cli/hyprland.py pcbridge/cli/main.py pcbridge/cli/ops.py tests/contracts/test_hyprland_doctor_setup.py tests/live/hyprland/check_doctor.py && git diff --check && git diff --cached --check`
  — pass.
- `./.venv/bin/python -O tests/live/hyprland/check_doctor.py > /tmp/pcbridge-hyprland-doctor-optimized.log 2>&1`
  — expected exit 1, unconditional refusal before config/state/grant/watcher
  creation; optimized Python cannot disable the live containment assertions.

The exact retrospective regression command, deliberately returning zero when
the old implementation fails, was:

```bash
./.venv/bin/python - <<'PY' > /tmp/pcbridge-hyprland-doctor-baseline-red.log 2>&1
import subprocess, unittest
from pcbridge.cli.doctor import Doctor
from tests.contracts.test_hyprland_doctor_setup import DesktopDispatchTests
source = subprocess.run(['git','show','HEAD:pcbridge/cli/doctor.py'],capture_output=True,text=True,check=True).stdout
namespace = {'__name__':'pcbridge.cli.doctor', '__package__':'pcbridge.cli'}
exec(compile(source, '<HEAD doctor.py>', 'exec'), namespace)
Doctor.desktop = namespace['Doctor'].desktop
suite = unittest.TestSuite([DesktopDispatchTests('test_doctor_dispatch')])
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if not result.wasSuccessful() else 1)
PY
```

**Review:** Independent spec review required the exact ACTIVITY_UNKNOWN code
in the live refusal assertion; the source was fixed and the real VM rerun
passed. Config loading also avoids default-state writes. Spec approval was
received before the separate quality review. Quality review reproduced an
unbounded FIFO open and symlink following in the public lease diagnostic.
The read now uses a nonblocking no-follow descriptor, verifies a regular file
with fstat, and retains the 64 KiB cap. New regression tests failed before the
fix and passed afterward; the real VM probe was rerun at the fixed source.

**Remaining work:** Additional input/clipboard/geometry/accessibility evidence,
non-default runtime submap, nested smoke, complete security acceptance, existing
platform regression, and final support documentation remain required. No
supported-platform claim or push is made at this stage.

**Local commit:** `f410779`. Separate quality approval followed the FIFO fix;
the final real VM rerun completed successfully before this commit.

## Stage 11a: Complete registered runtime submap evidence

**Objective/design:** Close the Stage 3 live-test gap with disposable runtime
fixtures, leaving the production read-only context unchanged. Register four
safe no-op callbacks on collision-checked uncommon keys under the selected
instance, retain their handles, and define a unique submap. Context lookup
never invokes a binding. Cleanup removes only fixture keys and restores the
exact original binding table; partial registration is covered by finally.
The early opt-in/VM/optimized-Python refusal runs before production imports.

**Files:** New check_runtime_context.py, measured facts, and this journal.

**Measurements:** The real table changed 48 -> 52 -> 48, with default ->
registered custom -> default submap. Both bindings_snapshot and the actual
capabilities_result context matched all raw JSON fields and types. The
production-query spy recorded twenty requests, exclusively binds and submap.
Fixture repeat, locked, non-consuming, input-capture, release, descriptions,
and universal values were preserved; universal is a string in this release.
The accepted device restriction option has no corresponding IPC JSON fields.
Lua actions remain opaque __lua references and were never executed.

**Measured limitation:** A mouse:275 key reports mouse=false. The tagged
0.56.2 parseKeyString/hlBind code never sets kb.mouse; changing its callback
to a dispatcher cannot supply that missing flag. This probe explicitly does
not claim mouse=true coverage. It preserves the actual returned value. The
tagged Lua handle :unbind removes matching key/modifier entries broadly, so
conservative preexisting-key collision refusal and distinct fixture keys are
required even when cleanup uses saved handles. No user config, grant, native
capture, or input device was involved.

**Exact tests:**

- `scripts/dev/hyprland-vm.sh ssh 'cat > ~/pcbridge/tests/live/hyprland/check_runtime_context.py' < tests/live/hyprland/check_runtime_context.py && scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && PCBRIDGE_TEST_HYPRLAND_CONTEXT=1 .venv/bin/python tests/live/hyprland/check_runtime_context.py' > /tmp/pcbridge-hyprland-context-live.log 2>&1`
  — pass, exit 0; final JSON includes exact table restoration, observed
  flags, all twenty read-only requests, and honest omitted-field limitations.
- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_binds tests.contracts.test_hyprland_session tests.contracts.test_mcp_errors > /tmp/pcbridge-hyprland-context-contracts.log 2>&1`
  — pass, 29 tests, including unknown future fields and large binding tables.
- `./.venv/bin/python tests/live/hyprland/check_runtime_context.py > /tmp/pcbridge-hyprland-context-no-optin.log 2>&1`
  — expected refusal, exit 1, before imports/IPC without opt-in.
- `PCBRIDGE_TEST_HYPRLAND_CONTEXT=1 ./.venv/bin/python tests/live/hyprland/check_runtime_context.py > /tmp/pcbridge-hyprland-context-host-refusal.log 2>&1`
  — expected refusal, exit 1, before imports/IPC on the real host.
- `PCBRIDGE_TEST_HYPRLAND_CONTEXT=1 ./.venv/bin/python -O tests/live/hyprland/check_runtime_context.py > /tmp/pcbridge-hyprland-context-optimized.log 2>&1`
  — expected refusal, exit 1, before imports/IPC when assertions are disabled.
- `./.venv/bin/python -m py_compile tests/live/hyprland/check_runtime_context.py && git diff --check`
  — pass.

**Review:** Independent spec and quality reviews approved the actual source,
logs, and tagged cleanup behavior. The inaccurate handle-removal comment was
corrected before commit. Quality review independently repeated the 29
contracts and three host containment refusals.

**Remaining work:** The remaining input, clipboard, accessibility, monitor,
security, and nested acceptance evidence; full GNOME/KDE regression; and final
support documentation remain required. This stage makes no support claim.

**Local commit:** `8e8928f`.

## Stage 10c: Remove the remaining GNOME reporting assumptions

**Plan adjustment/objective:** A broad read-only audit after runtime-context
acceptance found two remaining two-desktop assumptions in general reporting:
the MCP panel tool and monitor description. Reproduce and fix this small
reporting scope before further input acceptance. This closes the existing
explicit-compositor/capability contract; it adds no command or setting.

**Design/files:** Dispatch panel_icon locally by the canonical compositor
kind. Keep GNOME actions and the existing KDE note; report Hyprland/UNKNOWN
inapplicability before settings or extension-version access. Label Hyprland's
focused default accurately, including row markers, and preserve configured
primary labels elsewhere. Review found that both monitor adapters synthesized
focus when every runtime focused flag was false. Remove that inference in
Python and Rust; preserve the reported flags and use the first monitor by
position as the explicitly labeled fallback. A shared fixture reverses runtime
output order to verify neutral-state parity. Changed files are tools.py's panel
dispatch, monitors.py, the Rust Linux display adapter, both Python contract
modules, the shared Hyprland monitor fixture, measured facts, and this journal.
Coordinate geometry, metadata field names, permissions, and configuration are
unchanged; absent focus is now represented truthfully.

**Evidence:** Real VM MCP status initially returned a missing GNOME schema
error, and the monitor summary incorrectly mentioned GNOME panels/Super.
After sync, all three actual MCP actions returned the Hyprland note without
errors. The real 2560x800 table marked Virtual-1 focused and identified it as
the Hyprland default. The probe used public example settings with desktop
disabled and temporary state; no grant/input/capture was opened. Contract
spies prove no GNOME get/set/version call occurs for any Hyprland/UNKNOWN
action. GNOME/KDE behavior and configured-primary descriptions still pass.
The no-focus reproduction failed in both languages before the adapter fix;
afterward all observed focus flags remain false, the Python default resolves
to the first position-sorted output, and focused-to-no-focus transitions leave
physical topology identity unchanged. This case uses a fixture rather than
claiming the real VM spontaneously reported absent focus.

**Exact tests:**

- `./.venv/bin/python -m unittest tests.contracts.test_panel_icon tests.contracts.test_hyprland_monitors > /tmp/pcbridge-stage10c-reporting-red.log 2>&1`
  — expected 8 failures in 23 tests before fix: six unwanted settings reads
  and two incorrect captions. The focused-row regression was added afterward:
  `./.venv/bin/python -m unittest tests.contracts.test_hyprland_monitors > /tmp/pcbridge-stage10c-focused-row-red.log 2>&1`
  produced one expected failure in nine tests before its marker fix.
- `./.venv/bin/python -m unittest tests.contracts.test_panel_icon tests.contracts.test_hyprland_monitors tests.contracts.test_hyprland_doctor_setup tests.contracts.test_english_only > /tmp/pcbridge-stage10c-reporting-green.log 2>&1`
  — pass, 39 tests.
- `./.venv/bin/python -m unittest tests.contracts.test_hyprland_monitors > /tmp/pcbridge-stage10c-no-focus-red.log 2>&1`
  — expected one failure in 10 tests before removing inferred focus.
- `(cd rust && cargo test -p pcbridge-native --test display_contract hyprland_ipc_maps_to_the_same_neutral_state_as_python --locked > /tmp/pcbridge-stage10c-no-focus-rust-red.log 2>&1)`
  — expected one fixture failure before the same Rust adapter fix.
- `./.venv/bin/python -m unittest tests.contracts.test_panel_icon tests.contracts.test_hyprland_monitors tests.contracts.test_hyprland_doctor_setup tests.contracts.test_english_only > /tmp/pcbridge-stage10c-no-focus-green.log 2>&1`
  — pass, 40 tests after the no-focus fix.
- `(cd rust && cargo test -p pcbridge-native --test display_contract --locked > /tmp/pcbridge-stage10c-no-focus-rust-green.log 2>&1)`
  — pass, 11 tests, including exact Python/Rust fixture parity.
- `(cd rust && cargo test --workspace --locked --no-fail-fast > /tmp/pcbridge-stage10c-rust-workspace.log 2>&1)`
  — pass after the Rust adapter change.
- `./.venv/bin/python tests/test_desktop.py > /tmp/pcbridge-stage10c-final-desktop.log 2>&1`
  — pass, 615 checks after the no-focus fix.
- `scripts/dev/hyprland-vm.sh sync && scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && scripts/build-native.sh' > /tmp/pcbridge-stage10c-release-build.log 2>&1`
  — pass, refreshed the standard packaged release helper; no daemon restart.
- `./.venv/bin/python tests/test_desktop.py > /tmp/pcbridge-hyprland-reporting-desktop.log 2>&1`
  — pass, 615 checks without live input.
- `scripts/dev/hyprland-vm.sh sync` — pass.
- `./.venv/bin/python -m py_compile pcbridge/tools.py pcbridge/desktop/monitors.py tests/contracts/test_panel_icon.py tests/contracts/test_hyprland_monitors.py && git diff --check`
  — pass.

The exact pre-fix VM reproduction returned exit 0 with wrong output preserved
in its log (successful invocation alone was not treated as success):

```bash
scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && .venv/bin/python - <<'\''PY'\''
import asyncio, dataclasses, tempfile
from pathlib import Path
from pcbridge.app import build_app
from pcbridge.config import load_config
from pcbridge.desktop import monitors, session
assert session.desktop_kind() == session.HYPRLAND
with tempfile.TemporaryDirectory(prefix="pcbridge-reporting-before-") as temporary:
    cfg=dataclasses.replace(load_config("config.example.toml", check_state=False), state_dir=Path(temporary))
    mcp,_=build_app(cfg, transport="stdio")
    result=asyncio.run(mcp.call_tool("panel_icon", {"action":"status"}))
    print("MCP panel status:", "\n".join(getattr(item,"text","") for item in result.content))
    print(monitors.describe())
PY' > /tmp/pcbridge-hyprland-reporting-before.log 2>&1
```

The exact post-fix VM verification returned exit 0 and JSON application and
monitor evidence:

```bash
scripts/dev/hyprland-vm.sh sync && scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && .venv/bin/python - <<'\''PY'\''
import asyncio, dataclasses, json, tempfile
from pathlib import Path
from pcbridge.app import build_app
from pcbridge.config import load_config
from pcbridge.desktop import monitors, session
assert session.desktop_kind() == session.HYPRLAND
with tempfile.TemporaryDirectory(prefix="pcbridge-reporting-after-") as temporary:
    cfg=dataclasses.replace(load_config("config.example.toml", check_state=False), state_dir=Path(temporary))
    assert not cfg.desktop.enabled
    mcp,_=build_app(cfg, transport="stdio")
    results={}
    for action in ("status", "show", "hide"):
        result=asyncio.run(mcp.call_tool("panel_icon", {"action":action}))
        text="\n".join(getattr(item,"text","") for item in result.content)
        assert "Hyprland" in text and "No panel or tray is required" in text and "native glow" in text, text
        assert not result.is_error
        results[action]=text
    table=monitors.list_monitors(use_cache=False)
    default=monitors.resolve(None, table)
    text=monitors.describe()
    assert "GNOME" not in text and "Super overview" not in text
    assert "default monitor (Hyprland focused output)" in text and "(focused)" in text
    assert f"{default.index}/{default.connector}" in text
    print(json.dumps({"mcp_panel_actions":results,"monitor_description":text,"default_connector":default.connector,"desktop_enabled":False}))
PY' > /tmp/pcbridge-hyprland-reporting-after.log 2>&1
```

After the no-focus fix and release rebuild, this final VM rerun also passed:

```bash
scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && .venv/bin/python - <<'\''PY'\''
import asyncio, dataclasses, json, tempfile
from pathlib import Path
from pcbridge.app import build_app
from pcbridge.config import load_config
from pcbridge.desktop import monitors, session
assert session.desktop_kind() == session.HYPRLAND
with tempfile.TemporaryDirectory(prefix="pcbridge-reporting-final-") as temporary:
    cfg=dataclasses.replace(load_config("config.example.toml", check_state=False), state_dir=Path(temporary))
    assert not cfg.desktop.enabled
    mcp,_=build_app(cfg, transport="stdio")
    results={}
    for action in ("status", "show", "hide"):
        result=asyncio.run(mcp.call_tool("panel_icon", {"action":action}))
        text="\n".join(getattr(item,"text","") for item in result.content)
        assert "Hyprland" in text and "No panel or tray is required" in text and "native glow" in text, text
        assert not result.is_error
        results[action]=text
    table=monitors.list_monitors(use_cache=False)
    default=monitors.resolve(None, table)
    text=monitors.describe()
    assert "GNOME" not in text and "Super overview" not in text
    assert "default monitor (Hyprland focused output)" in text and "(focused)" in text
    assert f"{default.index}/{default.connector}" in text
    print(json.dumps({"mcp_panel_actions":results,"monitor_description":text,"default_connector":default.connector,"desktop_enabled":False}))
PY' > /tmp/pcbridge-stage10c-reporting-live-final.log 2>&1
```

**Review:** Separate spec and quality reviewers approved the expanded scope
after the no-focus correction. The final VM JSON confirms all three real MCP
panel actions and the focused default description after the release rebuild.

**Remaining work:** Additional input/clipboard/accessibility/geometry and
nested evidence, complete security acceptance, existing-platform regression,
and final documentation still remain. No supported-platform claim is made.

**Local commit:** `7e14a2e`.

## Stage 9c: Measure detailed input with the packaged release helper

**Objective/design:** Extend the isolated input acceptance with application
events for buttons, recognized click counts, horizontal scroll, intermediate
drag positions, manual holds, and the existing hold timeout. An optional
observer flag adds click-count and drag-update signals; the default observer
used by GNOME/KDE remains unchanged. Require normal SafetyGate admission,
fresh native idle, visible grant, cross-process sequence guards, current
observer PID/fullscreen geometry, and default packaged helper discovery.
Public example configuration and temporary state are used; the native helper
reports release/test_harness=false and its actual handshake build ID. The
release build ID is unknown-dirty because the VM copy has no .git directory.

**Files:** New tests/live/hyprland/check_input_details.py; the optional observer
and wrapper in tests/live/input_window.py and tests/live/test_input_parity.py;
default packaged helper discovery in tests/live/hyprland/check_input.py; this
journal and measured facts. No production input backend or permission changed.

**VM evidence:** Right/middle presses and releases reached GTK as buttons
3/2. GTK recognized double/triple clicks as n_press 2/3. Horizontal scroll
sent +2/-2 and received dx +2/-2, dy 0. Nine intermediate drag updates held
button 1 between (426,533) and (853,533); endpoints matched. Manual Shift and
left-button releases emptied held state. With no intervening native request,
the watchdog released Shift after 5.0013 seconds and the button after 5.0002
seconds; both release notifications were retrieved once. The original eight
edge/typing/shot/relative acceptance also passed with the packaged release
helper: two shot centers matched within one pixel, geometry/reserved space
were unchanged, and explicit revoke released held Shift in 31 ms.

**Measurement-driven diagnostic adjustment:** The first detailed run failed
because a current Lua cursor dispatcher acknowledgment did not produce GTK
motion within three seconds. A read-only query after cleanup showed the
requested (640,600) cursor position. The revised diagnostic asserts selected
IPC acknowledgment and authoritative cursor position first, then records GTK
motion as a bounded observation. In the final run both (640,600) and
(1920,600) were correct in IPC, with no GTK motion received within one second.
Fresh native moves to a different recovery point and then the target produced
actual GTK motion on both outputs. Review also corrected an initial test
assumption that repeating the kernel's cached pre-warp ABS point could recover
an external warp; the recovery point is now distinct. These observations do
not establish dispatcher equivalence to real-device input or a production
fallback. Tagged Hyprland source calls simulateMouseMovement after warp, with
no explicit pointer frame in that function; frame batching is an inference,
not a measured protocol trace. The production uinput path remains primary.

**Exact tests:**

- `./.venv/bin/python -m unittest tests.contracts.test_input_contract tests.contracts.test_rust_pointer_provider tests.contracts.test_rust_keyboard_provider tests.contracts.test_native_grant_rebind`
  — implementer pass, 50 tests before and after diagnostic changes.
- `./.venv/bin/python -m unittest tests.contracts.test_input_contract tests.contracts.test_rust_pointer_provider tests.contracts.test_rust_keyboard_provider tests.contracts.test_native_grant_rebind tests.contracts.test_batch_safety tests.contracts.test_runtime_contract > /tmp/pcbridge-stage9c-input-contracts.log 2>&1`
  — pass, 97 tests.
- `./.venv/bin/python -m unittest tests.contracts.test_input_contract tests.contracts.test_rust_pointer_provider tests.contracts.test_rust_keyboard_provider tests.contracts.test_native_grant_rebind tests.contracts.test_batch_safety tests.contracts.test_runtime_contract > /tmp/pcbridge-stage9c-final-contracts.log 2>&1`
  — pass, 97 tests after the final diagnostic edit.
- `./.venv/bin/python tests/test_desktop.py > /tmp/pcbridge-stage9c-desktop.log 2>&1`
  — pass, 615 checks without host input.
- `./.venv/bin/python -m py_compile tests/live/hyprland/check_input_details.py tests/live/hyprland/check_input.py tests/live/input_window.py tests/live/test_input_parity.py && git diff --check`
  — pass.
- `env -u PCBRIDGE_TEST_HYPRLAND_INPUT_DETAILS ./.venv/bin/python tests/live/hyprland/check_input_details.py > /tmp/pcbridge-stage9c-no-optin.log 2>&1; test "$?" -eq 1 && rg -q 'Set PCBRIDGE_TEST_HYPRLAND_INPUT_DETAILS' /tmp/pcbridge-stage9c-no-optin.log`
  — pass, expected early refusal before PcBridge imports.
- `PCBRIDGE_TEST_HYPRLAND_INPUT_DETAILS=1 ./.venv/bin/python tests/live/hyprland/check_input_details.py > /tmp/pcbridge-stage9c-host-refused.log 2>&1; test "$?" -eq 1 && rg -q 'only on the disposable pcbridge-hyprland VM' /tmp/pcbridge-stage9c-host-refused.log`
  — pass, expected host refusal before PcBridge imports.
- `PCBRIDGE_TEST_HYPRLAND_INPUT_DETAILS=1 ./.venv/bin/python -O tests/live/hyprland/check_input_details.py > /tmp/pcbridge-stage9c-optimized-refused.log 2>&1; test "$?" -eq 1 && rg -q 'Python -O is forbidden' /tmp/pcbridge-stage9c-optimized-refused.log`
  — pass, expected optimized-Python refusal before PcBridge imports.
- `scripts/dev/hyprland-vm.sh sync && scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && PCBRIDGE_TEST_HYPRLAND_INPUT=1 .venv/bin/python tests/live/hyprland/check_input.py --out-dir /tmp/pcbridge-input-stage9c-edges' > /tmp/pcbridge-stage9c-edges-live.log 2>&1`
  — pass, actual release-helper eight-edge/typing/capture acceptance.
- `scripts/dev/hyprland-vm.sh ssh 'cat > ~/pcbridge/tests/live/hyprland/check_input_details.py' < tests/live/hyprland/check_input_details.py && scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && PCBRIDGE_TEST_HYPRLAND_INPUT_DETAILS=1 .venv/bin/python tests/live/hyprland/check_input_details.py' > /tmp/pcbridge-stage9c-details-live.log 2>&1`
  — initial failure at the diagnostic GTK motion expectation; traceback
  preserved, cleanup completed.
- `scripts/dev/hyprland-vm.sh ssh 'cat > ~/pcbridge/tests/live/hyprland/check_input_details.py' < tests/live/hyprland/check_input_details.py && scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && PCBRIDGE_TEST_HYPRLAND_INPUT_DETAILS=1 .venv/bin/python tests/live/hyprland/check_input_details.py' > /tmp/pcbridge-stage9c-details-live-final.log 2>&1`
  — pass, exit 0 with application
  event and watchdog JSON. Failure paths preserve partial evidence and the
  bounded observer stderr before temporary files are removed.

Final cleanup was independently verified, exit 0:

```bash
scripts/dev/hyprland-vm.sh session 'cd ~/pcbridge && .venv/bin/python - <<'\''PY'\''
import json, os
from pathlib import Path
from pcbridge.desktop import hyprland, idlewatch
from tests.live.hyprland.check_glow_owner import layers
counts={"native_helpers":0,"input_observers":0}
for process in Path("/proc").iterdir():
    if not process.name.isdecimal():
        continue
    try:
        if process.stat().st_uid != os.getuid():
            continue
        args=process.joinpath("cmdline").read_bytes().split(b"\0")
    except (OSError, PermissionError):
        continue
    if args and Path(os.fsdecode(args[0])).name == "pcbridge-native":
        counts["native_helpers"]+=1
    if any(Path(os.fsdecode(arg)).name == "input_window.py" for arg in args if arg):
        counts["input_observers"]+=1
state={"locked":hyprland.screen_locked(),"idle":idlewatch.read_idle_ms(),"glow_layers":len(layers()),**counts}
assert state == {"locked":False,"idle":None,"glow_layers":0,"native_helpers":0,"input_observers":0}, state
print(json.dumps(state))
PY' > /tmp/pcbridge-stage9c-cleanup-live.log 2>&1
```

**Remaining work:** Expiry/replacement/cancellation and policy acceptance,
pointer lock, touch, clipboard ownership, accessibility, geometry/visual/
performance evidence, nested smoke, and final regression still remain. A
read-only audit found that MCP cancellation currently permits synchronous
worker actions to continue; the next security stage must reproduce and resolve
this rather than treating the client's cancellation exception as safety proof.

**Review:** Separate spec and quality reviews approved the scoped stage after
the measured diagnostic adjustment. Final VM exit status and independent
cleanup were verified after the evidence JSON, rather than assuming that
printing evidence established cleanup success.
