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
