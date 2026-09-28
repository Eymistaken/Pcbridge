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
