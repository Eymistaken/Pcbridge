# Native capture session

How `pcbridge-native` captures the screen through a Mutter ScreenCast
session (`rust/crates/pcbridge-native/src/platform/linux/session.rs`,
`capture.rs`, `pipewire_source.rs`; rules in `pcbridge-core::frame`). The
Python counterpart is `pcbridge/desktop/screencast.py` +
`screencast_helper.py`. Whichever takes the frame, the shot and coordinate
contract in `capture.py` is the same.

The session opens with `capture.session_open` when `desktop_unlock` runs (so
GNOME's sharing indicator appears with the grant), or at the first
`capture.frame`.

On KDE Plasma there is no session: each `capture.frame` is one call to
KWin's `org.kde.KWin.ScreenShot2.CaptureScreen` (`kwin_screenshot.rs`),
native resolution, the pointer included when asked, the raw `QImage`
written to a pipe the helper hands over. KWin answers only a program that a
`.desktop` file with `X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2`
names as `Exec`; pcbridge installs one for the helper's full path.
`capture.session_open` answers `not_needed`, and the backend is
`linux.kwin.screenshot2`. KWin's alpha channel is blending residue, so the
formats 4-6 (RGB32, ARGB32, ARGB32 premultiplied) are all read as BGRx.
Measured: 7-12 ms a frame from KWin, 49-80 ms through the helper with the
PNG (debug build).

## Why a session

On GNOME 46 the only silent way to take a screenshot is **screen sharing**:
`gnome-screenshot` and the XDG portal flash and play a sound, and
`org.gnome.Shell.Screenshot` answers "Access denied". Sharing has no flash
because the system treats it as video, not a photo.

Sharing means a session: `CreateSession` -> `RecordMonitor` per connector ->
`Start`, and each connector's PipeWire node id arrives later in a
`PipeWireStreamAdded` signal.

## State machine

```text
Closed ──open()──► Starting ──► Ready ──stop()──► Stopping ──► Closed
                      │                              ▲
                      └────── error ──► Stopping ─────┴──► Failed
```

`Failed` is its own state: like `Closed` it holds nothing in the compositor,
but it remembers **why**. `stop()` clears it to `Closed`. `Starting` and
`Stopping` are real states because a session that fails while starting has
already created compositor resources. The last 16 transitions are kept for
diagnosis (`history()`).

## Start order and what each step guarantees

1. **Gate** (`checkpoint`): is the grant still valid, before touching the bus.
2. `CreateSession`.
3. Per connector: **gate**, then `RecordMonitor`. A revoke before the second
   monitor closes what was created for the first.
4. **Gate**, then `Start`.
5. Wait for the nodes (`STREAM_TIMEOUT`, 10 s), matched by **stream object
   path**, not by arrival order.
6. **Gate**: a revoke that came while waiting must not produce `Ready`.

Any failure stops everything created so far and lands in `Failed`, except
when the connection itself is lost: `Stop` on a dead socket only adds a
second error.

**Why match by stream path.** Measured over 11 runs (2026-09-12): PipeWire
node ids are recycled and their order is not stable (the same request got
`[83, 82]` once and `[69, 72]` another time). "The first signal belongs to
the first monitor I recorded" would silently capture the wrong screen. Each
`RecordMonitor` returns its stream path, and matching uses it.

**Early signals.** The signal subscription is made in `connect()`, before any
session exists, with one match rule for the whole `Stream` interface, so an
announcement between `RecordMonitor` and the first wait is not lost. Other
clients' announcements reach us too; unknown paths are skipped, not errors.

## Five things close a session

| Trigger | Path |
|---|---|
| Revoke | lease watchdog -> `FailClosed` -> `SessionHandle::close_fail_closed` |
| Screen lock (or unknown lock state) | lock watchdog -> the same path |
| No node arrived (timeout) | `MissingStream` -> clean stop -> `Failed` |
| Host EOF / process death | `Drop` -> `Stop` |
| Lost compositor connection | `BusError::Lost` -> local close, no `Stop` |

**Why a flag plus gates.** The watchdog cannot wait for the session lock: the
start it wants to interrupt may hold that lock for the whole stream timeout,
and a fail-closed signal that waits ten seconds is not fail-closed. So the
watchdog raises `FailClosedFlag` first, then *tries* the lock; if it is busy,
the call in flight cancels itself at its next gate. The flag is an
interruption, not an authorization: `open()` clears it on entry and asks the
gate, otherwise a screen lock (which ends when the user returns) would latch
the session closed forever. A poisoned mutex counts as closed: a panic
somewhere is no reason to keep sharing the screen. `try_lock` also avoids
re-entrance: the gate consulted during a start is `Lifecycle`, which on a
revoke tells registered resources to close, possibly from the very thread
that holds the lock.

## Differences from the Python helper

| | `screencast.py` | here |
|---|---|---|
| A different monitor set requested | answers `already: True` and **keeps the old set** | rebuilds |
| Pointer mode change | separate `ensure_cursor` | the same `open()` path, returns `Recreated` |
| Signal subscription | after `RecordMonitor`, by stream path | in `connect()`, by interface |
| Node matching | by stream path | by stream path |
| Authorization gate | none (the helper knows no grant) | `SessionGuard` at every step |

D-Bus timeout for session methods: **10 s** (the value the Python helper,
the only measured implementation of this sequence, uses), not the 200 ms of
`GetActive` / `GetIdletime` / `GetCurrentState`, which only read a value.

Measured (2026-09-12, real Mutter, two monitors, 11 runs, bus connection
excluded): `open` 2.6-4.8 ms (mean 3.6); reuse 0.003-0.007 ms (no bus call);
pointer-mode change (stop + full rebuild) 3.8-6.4 ms; `stop` 0.8-1.4 ms. The
Python helper's pointer-mode change was logged at ~113 ms, including its JSON
round trip. No Session object was left under `org.gnome.Mutter.ScreenCast`.

## Frames and PNG

**Order matters.** A PipeWire frame arrives as memory the producer still
owns; whatever happens before returning the buffer, the compositor waits for.
Encoding 1920x1080 as PNG takes tens of milliseconds of CPU. So the split is
structural: `FrameSource` returns an owned `RgbaFrame` (already converted,
buffer already returned), and the stream is disconnected **inside the
process callback** before the frame goes to the worker. The first version
asked for the disconnect through the control channel; continuous callbacks
kept the command waiting for 2001 ms. Released in the callback: under 1 ms.

**Three traps that give a silently wrong answer:**

1. **`x` is not alpha.** The fourth byte of `BGRx`/`RGBx` is undefined; copying
   it into alpha gives a fully transparent PNG when the producer writes zero:
   valid, and empty.
2. **The stride is not width times four.** Rows are padded; reading the buffer
   as one block shifts each row further right.
3. **Size limits come before allocation.** A header claiming 60000x60000 is
   refused while it is still a number: a 4-byte buffer with a 3.6-gigapixel
   request returns `TooManyPixels`, not `BufferTooSmall`.

**Freshness is not frame identity.** The sequence/timestamp and their source
are reported, but they do not answer "is this frame newer than my request":
comparing the producer's PTS with our clock would be an unmeasured
assumption. Each frame is stamped with a local monotonic `Instant` on arrival;
older ones are dropped and counted (`stale_frames`). Mutter on GNOME 46
provides no `SPA_META_Header` and `pw_stream_get_time_n().now` is 0 on this
node, so the identity is a local counter and nanoseconds since the source
started (`source_monotonic_clock`); with the metadata it would be carried as
is (`spa_meta_header`).

| Limit | Value |
|---|---|
| Frame wait | 8 s (as the Python helper; an MCP call may not exceed 110 s) |
| Largest frame | 32 million pixels, checked before allocating |
| IPC payload | 128 MiB (`MAX_PIXELS * 4 <= MAX_BINARY_BYTES` is checked at compile time) |

**The PipeWire source** keeps `MainLoop`, `Context` and `Core` on one
dedicated thread for its lifetime; only owned RGBA8 frames cross the thread
boundary (a two-slot channel). Format negotiation offers only
BGRx/RGBx/BGRA/RGBA with real SPA enums; unknown formats, DMA-BUF that cannot
be CPU-mapped, negative strides and broken or missing chunks are refused. The
**`Stream` is created per capture** (see the wrong-monitor bug below). Aiming
at a node by its id is deprecated in libpipewire (`target.object` wants the
node's `object.serial`, Mutter reports only the id); a fresh stream + id
picks the right monitor on PipeWire 1.0.5 / WirePlumber 0.4.17, and
`each_connector_gets_its_own_monitors_frame` checks it on every live run. If
an upgrade breaks it, read the node's `object.serial` from the registry.

The encoder is the `png` crate, not `image`: `image` with only its png
feature still pulled in color management and four more crates (15 direct
dependencies against 8). Frames are encoded with fast compression: the
intermediate PNG is decoded by Python immediately, and this took the native
frame call from 525 ms to 98 ms (445 ms -> 17.5 ms encoding).

## The Python side

Where the frame comes from changed; nothing else did. Cropping, scaling, the
PNG sent to the client, shot ids, both search directories, the shot record and
every coordinate conversion stay in `capture.py`: a shot id is how a later
`mouse(shot=…)` finds the offset and scale, and keeping that ledger in two
languages is how clicks end up on the wrong screen. `NativeScreenCast` has the
old handle's shape (`is_open`, `start`, `ensure_cursor`, `capture(connector,
path)`, `close`), so `capture.py` cannot tell them apart.

Backend selection (`select_capture_backend`, pure, done once when the runtime
is built, so an `all` capture never mixes two sources):

| `[native] capture` | helper present | helper missing |
|---|---|---|
| `python` | Python | Python |
| `rust` | Rust | **Rust, failing visibly** |
| `auto` (default) | Rust | Python + `degraded` with the reason |

`auto` keeps an explicit `[desktop] capture_backend = "gnome-screenshot"` on
the Python path, so sharing the user turned off is not opened silently. A
fallback is visible in `system_capabilities`, in the `screen_capture` result,
in the audit log, and in `pcb-shot --json`.

- **`taken_at` is each frame's own arrival time**: in an `all` capture the
  monitors are read in turn, hundreds of milliseconds apart, and the stamp
  drives the staleness check. A stamp more than a second in the future is
  ignored; it would silently disable that check.
- **Typed refusals pass through.** The helper's REVOKED, DISPLAY_CHANGED,
  FRAME_TIMEOUT… reach the caller with their code (they used to become
  `BACKEND_UNAVAILABLE`; see [verification-linux.md](verification-linux.md)).

### Publishing: all or nothing

Images and records are produced in a hidden, mode 700 staging directory
inside `out_dir` (raw frames too; not `/tmp`). When every target is ready they
are published with **hard links** (`os.link` fails if the target exists,
unlike `os.replace`): PNGs first, records last, so a record never points at a
missing image. A new id must be free in **every** directory `shot=` searches,
or a new suffix is drawn (at most 16 tries); if another process takes the
name between check and publish, this attempt's links are undone. The
`pcb-shot --out` record copy is part of publishing. Abandoned staging
directories older than 10 minutes are swept. Before, a failure on the second
monitor left the first monitor's picture findable by `shot=`, a suffix
collision could overwrite another capture's record, and a killed capture
could leave a full-resolution image where no sweep looked.

### Delivery: undecodable is not success

`screen_capture` checks every image before sending it: PNG signature, IHDR
and **the scaled size in the record**. A mismatch is `isError=true` with
`IMAGE_DELIVERY_FAILED`; the text names the capture that did not arrive, the
others are still sent, and `structuredContent.shots` lists every id. The size
check matters because the client sends back the pixel it saw with `shot=` and
the server applies the recorded scale. The text block always comes first and
states the image order; `computer_batch` trims only its own report, never the
last step's capture text.

## The wrong monitor: found and fixed (2026-09-13)

Comparing native and old captures of the same monitor (native, old, native),
the left monitor matched (99.993 %, native/native floor 99.989 %) and the
right one only **26.92 %**, while the two native frames of it matched each
other at 99.916 %. The native "right monitor" was the left monitor. An order
experiment on a fresh helper isolated it:

| Order | Before (one stream reconnected) | After (a stream per capture) |
|---|---|---|
| DP-3, DP-4, DP-3, DP-4 | DP-4 requests showed **DP-3** | 4/4 correct |
| DP-4, DP-3, DP-4, DP-3 | DP-3 requests showed **DP-4** | 4/4 correct |

A reconnected stream **stayed on the node it first connected to**; the
connector-to-node mapping was right, the stream did not follow it. No test had
caught it: every live test read only the **first** monitor, and the MCP live
test checked size, PNG and color count of two images without noticing they
showed the same screen. The native path was not the default yet; had it been,
`monitor=2` would have shown the left monitor and a `shot=` click from it
would have landed on the right one.

Now `each_connector_gets_its_own_monitors_frame` reads the second monitor
first, then the first, then both again (same monitor >= 90 %, different < 99 %;
measured 100.00 %, 99.93 %, 26.92 %), and reverting to the single stream turns
it red. The MCP live test compares the monitor images pairwise. Its
precondition: the two monitors must show different content (a panel or one
window is enough).

## Measured end to end

- Single frames (2026-09-12, 10 real 1920x1080 frames in five sessions, every
  PNG decoded, not black, alpha 255, sequence and timestamp increasing): wait
  52-83 ms, encoding 32-53 ms. A second session measured encoding ~50 %
  slower in a narrow band (load average 3.02, governor `powersave`): read a
  single session's band as one sample, not the machine.
- Through the real protocol with a grant in a temporary state dir: a
  421 034-byte PNG, exactly `binary_len`, 1920x1080, a real desktop (5786
  distinct colors); a wrong `grant_id` -> `REVOKED`, clean exit, no Session
  left. `capture_frame_ipc_live.rs` automates this with every refusal type,
  checks that a refusal carries no payload and that the helper still delivers
  afterwards; breaking `matches_token` turned it red.
- MCP over real stdio with `rust` forced, Python GI blocked and
  `gnome-screenshot` unusable: two 1536x864 images decoded by the client, the
  two monitors 26.24 % alike (different screens), records matching the
  decoded size; after `desktop_lock` a safety error and no image; nothing
  left afterwards (no helper, no Mutter session, no staging directory).
- A debug helper spent ~1755-1811 ms encoding per monitor; release ~200 ms.
  With Python's `optimize=True` removed and fast helper encoding, two monitors
  through MCP take ~789 ms ([verification-linux.md](verification-linux.md)).

## Tests

- `tests/contracts/test_shot_artifacts.py`: all-or-nothing publishing,
  no overwrite (search dirs, existing PNG, the check/publish race, the record
  copy), sweeping staging dirs, the delivered block byte-identical to the
  published PNG.
- `tests/integration/test_mcp_capture_delivery.py`: a real MCP client decodes
  the base64 and compares fixture pixels over memory, real stdio pipes and
  HTTP (an expired link is 404); an undeliverable image is an error; a long
  batch report keeps the id lines. The live class needs
  `PCBRIDGE_TEST_CAPTURE=1`.
- `rust/crates/pcbridge-native/tests/capture_session.rs` (fake bus): early and
  foreign signals, missing stream, partial start failure, double start/stop,
  revoke during start, lost connection, `Drop`, the watchdog flag, re-entrance
  through the gate, the `Lifecycle` registration. Breaking `abort`'s `Stop`,
  the last gate or `Drop` turned 6 tests red.
- `rust/crates/pcbridge-core/tests/frame_conversion.rs`: padded stride,
  non-zero chunk offset, truncated buffer, channel order, alpha, overflow,
  zero size, PNG round trip; expected bytes written by hand, never recorded
  from the converter.
- `rust/crates/pcbridge-native/tests/capture_worker.rs` (fake source): stale
  frame, timeout, cancel, revoke, broken buffer, failed attach, SPA format
  mapping, identity source, a complete `detach` on every error path.
- `rust/crates/pcbridge-native/tests/ipc_protocol.rs` (test harness): the PNG
  travels without base64 with exact `binary_len`; exact decoded pixels.
- `capture_frame_live.rs`, `capture_session_live.rs` (skip without
  `PCBRIDGE_TEST_CAPTURE=1`): the real Mutter method names, signatures and
  signals, two frames through one session, the per-connector check. The
  sharing indicator appears while they run.

## Deliberately not done

- A RemoteDesktop pointer grant: `cursor-mode` 0 (hidden) and 1 (embedded) are
  used; mode 2 (pointer as metadata) needs a door this helper does not open.
- Portal persistence; buffered (continuous) capture.

## Rolling back

`capture = "python"` under `[native]`, then `pcbridge update`. With `python`
selected the Python pipeline never calls the native capture methods.
