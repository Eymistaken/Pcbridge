# Native helper: build, packaging, diagnosis

`pcbridge-native` is a private helper process next to the Python daemon
(protocol: [protocol-v1.md](protocol-v1.md), capture: [capture.md](capture.md)).
This page covers how it is built, where it goes, what it needs at run time,
and how an installation is diagnosed.

## Building is not running

| | To build | To run |
|---|---|---|
| Rust | 1.95.0 (`rust/rust-toolchain.toml`), rustup | none |
| System packages | `libpipewire-0.3-dev`, `libspa-0.2-dev`, `libclang-dev`, `pkg-config` (Arch: `pipewire`, `clang`, `pkgconf`); PipeWire headers 0.3.65 or newer | `libpipewire-0.3-0t64`, `libc6` (>= 2.35), `libgcc-s1` (Arch: `libpipewire`, `glibc`, `gcc-libs`) |
| Session | none | GNOME: PipeWire and Mutter ScreenCast. KDE Plasma: KWin ScreenShot2 and an authorizing `.desktop` entry. Hyprland: selected Wayland/IPC instance, image-copy protocols, session-bound idle observation, and a visible grant frame. |
| `python3-gi`, GStreamer, `pipewiresrc` | none | **not needed** |

Runtime libraries, measured with `readelf -d` on a release build:
`libpipewire-0.3.so.0`, `libgcc_s.so.1`, `libc.so.6`, `libm.so.6` (only
`hypot`, for pointer paths), `ld-linux-x86-64.so.2`.

The release helper is built on Debian 12 (glibc 2.36, PipeWire 0.3.65).
Its highest glibc symbol version is `GLIBC_2.35` (`hypot`, `objdump -T`),
so it runs on Ubuntu 22.04 and Debian 12 (the bases of Linux Mint 21
and Pardus 23) and everything newer, and CI builds it once for every
`.deb` and the wheel.
Two measurements set that base
([measured facts](../dev/measured-facts.md#the-native-helper-below-glibc-239-2026-11-12)):
Ubuntu 22.04 cannot build it, because `libspa` 0.10.1 does not compile
against PipeWire 0.3.48's headers; and a build on Ubuntu 24.04 needs glibc
2.39, because two weak imports of Rust's standard library (`pidfd_spawnp`,
`pidfd_getpid`) get a hard version need there. With `PCBRIDGE_MAX_GLIBC`
set (CI: `2.35`), `build-native.sh` refuses a helper that needs more. A
helper built anywhere else needs the glibc of the machine that built it;
setup reads that from the binary and skips a helper this system cannot
start.

The only target is `x86_64-unknown-linux-gnu`. The Arch package builds its
own from source (`packaging/arch/PKGBUILD`); `build-native.sh` finds Arch's
libclang under `/usr/lib` and prints a `pacman` line when something is
missing.

The helper is the only desktop backend: the Python fallbacks for screen
sharing, input and accessibility were removed after 2.8. `python3-gi` is
still a dependency of the packages, for KDE Plasma's KWin window scripts
(`kwin_helper.py`, the one Python helper left), and `at-spi2-core` for the
accessibility bus the helper reads.

## Build

```bash
scripts/build-native.sh --check   # only check the build requirements
scripts/build-native.sh           # build, verify, install into the package tree
```

The script does exactly this:

1. Checks the requirements; if something is missing it prints the install
   command and exits with `3`.
2. Sets `PCBRIDGE_BUILD_ID`: the first 12 hex digits of the commit, plus
   `-dirty` when `rust/` has uncommitted changes. No timestamp: two builds of
   the same source carry the same id.
3. Runs `cargo build --release --locked -p pcbridge-native --target
   x86_64-unknown-linux-gnu` **from inside `rust/`**, because
   `rust-toolchain.toml` is selected by the working directory; cargo called
   from the repository root with `--manifest-path` does not see it.
4. Asks the result for `--build-info` and **refuses** it unless the profile
   is `release`, `test_harness` is off, and target and build id match. It
   prints the newest glibc the binary needs and, with `PCBRIDGE_MAX_GLIBC`
   (for example `2.35`), refuses a binary that needs a newer one.
5. Installs it as `pcbridge/_native/x86_64-unknown-linux-gnu/pcbridge-native`
   (temporary file + `mv`, so a running process keeps the old file) with a
   `build-info.json` next to it.

It restarts nothing. The new helper is picked up at the next start of a
process; `pcbridge update` restarts the daemon when idle.

Only release builds: PNG encoding takes ~1755 ms per monitor in a debug
build and ~200 ms in release (measured 2026-09-13). A cached build takes
~17 s; the helper is ~6 MB. `pcbridge/_native/` is not in git; the wheel
(tagged `py3-none-linux_x86_64` when it contains the helper) and the `.deb`
carry it.

## The binary describes itself

```bash
pcbridge-native --version
pcbridge-native --build-info
```

Neither starts the protocol or touches the session bus or PipeWire. The
`build_id` also comes back in the `initialize` answer
(`NativeHandshake.build_id`). Format: [protocol-v1.md](protocol-v1.md#command-line).

## Discovery

`pcbridge/native/discovery.py`, without searching PATH:

1. `$PCBRIDGE_NATIVE_BIN`
2. `[native] binary_path` in the config
3. the package: `pcbridge/_native/<target>/pcbridge-native`

If none exists: `NATIVE_NOT_FOUND`, and its message ends with how this
installation gets one (`pcbridge doctor --fix`, `scripts/build-native.sh`
or reinstalling the package, from `fetch.remedy()`). There is no other
backend to fall back on: every desktop tool refuses with that message, and
`system_capabilities` reports the desktop capabilities unavailable with it.
`auto` and `rust` both mean the helper; an old `"python"` is read as
`auto`, with a warning.

## The release asset for installations without one

Each release publishes the helper on its own as
`pcbridge-native-<target>` (today `pcbridge-native-x86_64-unknown-linux-gnu`),
the same binary the wheel and the packages carry, listed in the release's
`sha256sums.txt` (`release.yml`, "Native helper as its own asset"). The
name is a contract with `pcbridge/native/fetch.py`.

`pcbridge setup` and `pcbridge doctor --fix` use it when desktop control is
supported and discovery would find no helper (a git checkout without Rust,
or a venv without one). The order is fixed: this version's release
(`v<version>`), else the latest release (a checkout newer than every
release); the SHA-256 against that release's `sha256sums.txt`;
`--build-info` (`name`, this machine's target, a release build, no test
harness); the glibc it needs; a handshake in a throwaway state directory.
The file goes into `pcbridge/_native/<target>/` by an atomic rename only
after all of them, with a `build-info.json` beside it like
`scripts/build-native.sh` leaves; a failed check leaves nothing behind.
Requests carry a generic `User-Agent` (`pcbridge-setup`) and nothing about
the user. The daemon never downloads anything: discovery above stays the
only way the helper is found.

## Diagnosis

`pcbridge doctor` (its native group runs `python -m
pcbridge.native.diagnostics`) asks for no grant, opens no screen sharing and
touches no real grant file. It reports:

- where the helper was found; if missing, an error when desktop control is
  on and a warning when it is off, with the fix for this installation;
- `--build-info`: version, build, protocol, target, profile. Not release is a
  warning; a test-harness build or a protocol/target mismatch is an error;
- runtime libraries: an unresolved one is an error, an undocumented one a
  warning;
- a handshake and `capabilities` in a temporary state directory: each
  capability's state and, when not supported, why; a helper without the
  accessibility methods is an error, since it is the only reader.

The helper computes `capabilities` at run time. On GNOME it checks that
`org.gnome.Mutter.ScreenCast` has an owner and that the PipeWire socket exists,
without opening a session. On Hyprland it probes the selected Wayland
session's image-copy protocols without opening capture. Before, the GNOME
probe answered a fixed "supported" even when capture could never work.

## Tests

- `rust/crates/pcbridge-native/tests/build_info.rs`: `--build-info` is one
  JSON object, `--version` one line, anything else exits `2`; the readiness
  decision and the PipeWire socket path. No session needed.
- `tests/contracts/test_native_diagnostics.py`: every diagnosis level.
- `tests/integration/test_native_packaging.py`: the packaged helper, copied
  to an unrelated directory, describes itself, depends only on PipeWire and
  the C runtime, and reports the same build id in the handshake; without the
  helper capture refuses and says how to get one.

## CI

`native.yml`: in a `debian:12` container, build dependencies, fmt, clippy
with `-D warnings` and tests in both feature sets, and
`scripts/build-native.sh` with `PCBRIDGE_MAX_GLIBC=2.35`, which leaves the
helper as an artifact; then, on Ubuntu 24.04 with only
`libpipewire-0.3-0t64` installed, the packaging test with
`PCBRIDGE_TEST_REQUIRE_RELEASE=1` against that artifact. `package.yml`
builds the release helper the same way, once for all `.deb` builds and the
wheel. Live capture and input tests never run in CI.

## Not planned

- The Rust toolchain as a runtime requirement.
- Unsigned automatic updates.
- A GUI package.

## No rolling back

Until 2.8, `[native] capture`, `input` or `accessibility` set to `"python"`
switched back to the Python paths. Those paths were removed after 2.8; the
way back from a broken helper is the previous release, which still has
them.
