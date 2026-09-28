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
