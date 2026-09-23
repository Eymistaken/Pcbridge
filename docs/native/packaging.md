# Native helper: build, packaging, diagnosis

`pcbridge-native` is a private helper process next to the Python daemon
(protocol: [protocol-v1.md](protocol-v1.md), capture: [capture.md](capture.md)).
This page covers how it is built, where it goes, what it needs at run time,
and how an installation is diagnosed.

## Building is not running

| | To build | To run |
|---|---|---|
| Rust | 1.95.0 (`rust/rust-toolchain.toml`), rustup | none |
| System packages | `libpipewire-0.3-dev`, `libspa-0.2-dev`, `libclang-dev`, `pkg-config` (Arch: `pipewire`, `clang`, `pkgconf`) | `libpipewire-0.3-0t64`, `libc6` (>= 2.39), `libgcc-s1` (Arch: `libpipewire`, `glibc`, `gcc-libs`) |
| Session | none | GNOME: a PipeWire socket and `org.gnome.Mutter.ScreenCast` on the session bus. KDE Plasma: `org.kde.KWin.ScreenShot2` and a `.desktop` entry that authorizes the helper (`pcbridge setup` writes it; packages ship it) |
| `python3-gi`, GStreamer, `pipewiresrc` | none | **not needed** |

Runtime libraries, measured with `readelf -d` on a release build:
`libpipewire-0.3.so.0`, `libgcc_s.so.1`, `libc.so.6`, `libm.so.6` (only
`hypot`, for pointer paths), `ld-linux-x86-64.so.2`. The highest glibc symbol
version is `GLIBC_2.39` (`objdump -T`): a helper built on Ubuntu 24.04 does
**not** run on an older glibc (22.04). The only target is
`x86_64-unknown-linux-gnu`; Ubuntu 24.04/26.04 and Debian 13 all qualify, so
CI builds the helper once and every `.deb` shares it. The Arch package
builds its own from source (`packaging/arch/PKGBUILD`); `build-native.sh`
finds Arch's libclang under `/usr/lib` and prints a `pacman` line when
something is missing.

The Python fallbacks for accessibility and screen sharing still need
`python3-gi`; `pcbridge doctor` reports that separately.

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
   is `release`, `test_harness` is off, and target and build id match.
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

If none exists: `NATIVE_NOT_FOUND`. `python` for a subsystem ignores the
helper; `auto` falls back to Python and reports `degraded` in
`system_capabilities`; `rust` stays selected and fails loudly.

## Diagnosis

`pcbridge doctor` (its native group runs `python -m
pcbridge.native.diagnostics`) asks for no grant, opens no screen sharing and
touches no real grant file. It reports:

- the `[native]` choices and where the helper was found; if missing, the
  level depends on the choice (`python` info, `auto` warning, `rust` error);
- `--build-info`: version, build, protocol, target, profile. Not release is a
  warning; a test-harness build or a protocol/target mismatch is an error;
- runtime libraries: an unresolved one is an error, an undocumented one a
  warning;
- a handshake and `capabilities` in a temporary state directory: each
  capability's state and, when not supported, why;
- that the Python fallbacks still need `python3-gi`.

The helper computes `capabilities` at run time: on every request it checks
that `org.gnome.Mutter.ScreenCast` has an owner and that the PipeWire socket
exists, without opening a session. Before, it answered a fixed "supported",
even on a machine where capture could never work.

## Tests

- `rust/crates/pcbridge-native/tests/build_info.rs`: `--build-info` is one
  JSON object, `--version` one line, anything else exits `2`; the readiness
  decision and the PipeWire socket path. No session needed.
- `tests/contracts/test_native_diagnostics.py`: every diagnosis level.
- `tests/integration/test_native_packaging.py`: the packaged helper, copied
  to an unrelated directory, describes itself, depends only on PipeWire and
  the C runtime, and reports the same build id in the handshake; without the
  helper the Python install keeps working.

## CI

`native.yml` (Ubuntu 24.04): build dependencies, fmt, clippy with
`-D warnings` and tests in both feature sets, `scripts/build-native.sh`, the
packaging test with `PCBRIDGE_TEST_REQUIRE_RELEASE=1`, and the helper as an
artifact. `package.yml` builds it once for all `.deb` builds. Live capture
and input tests never run in CI.

## Not planned

- The Rust toolchain as a runtime requirement.
- Unsigned automatic updates.
- A GUI package.

## Rolling back

Set `capture`, `input` or `accessibility` under `[native]` to `"python"` and
run `pcbridge update`. Removing the helper is not needed: with `python`
selected it is not used.
