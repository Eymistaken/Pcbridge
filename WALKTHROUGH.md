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
