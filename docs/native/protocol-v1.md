# Native IPC protocol v1

The local stdio contract between the Python host and the `pcbridge-native`
child process. This pipe is separate from the MCP transport: the helper's
stdout carries only the framed responses defined here.

On Linux the helper reads the monitor table, captures single-monitor frames
through Mutter ScreenCast + PipeWire, produces uinput keyboard and pointer
events, runs the clipboard programs, and reads and acts on the accessibility
tree over D-Bus without GI. Each subsystem is selected by `[native]` (`auto`
by default: the helper when packaged, the Python path otherwise, reported
visibly). Besides the grant lifecycle, the helper watches the screen lock and
user activity over the session bus as typed observations; every backend uses
the same fail-closed boundary.

## Frames and limits

Every frame is, in order:

```text
4-byte unsigned big-endian JSON header length
JSON header, UTF-8
binary payload of header.binary_len bytes
```

- The JSON header is at most 64 KiB and never empty.
- The binary payload is at most 128 MiB.
- `binary_len` is a required unsigned integer in request and response
  headers.
- Short reads and writes are normal and are completed.
- Declared sizes are checked before allocating.
- Control methods take no payload and answer with `binary_len: 0`.
- Two **requests** carry a payload: `clipboard.write` and
  `accessibility.set_text`. Clipboard content and text for a field may not
  fit a 64 KiB header and must never sit in a header field that could reach a
  log line. A payload sent to any other method returns `UNEXPECTED_BINARY`.

A clean stdin EOF ends the process successfully. A partial length, a
truncated header or payload, invalid JSON or an invalid `binary_len` is a
protocol error: the process writes only the error class to stderr, exits with
code `2`, and never writes free text to stdout.

## Handshake

The first successful request must be `initialize`:

```json
{
  "protocol": {"major": 1, "minor": 0},
  "id": "client-a:1",
  "method": "initialize",
  "params": {
    "client_version": "pcbridge-build-id",
    "supported_minor": [0],
    "state_dir": "/absolute/private/state",
    "runtime_dir": "/absolute/private/runtime"
  },
  "binary_len": 0
}
```

`client_version` is not empty; `state_dir` and `runtime_dir` are absolute
(the helper neither opens nor creates them at this point); `supported_minor`
must contain the server's minor `0`.

The answer names the chosen version and the process:

```json
{
  "protocol": {"major": 1, "minor": 0},
  "id": "client-a:1",
  "result": {
    "instance_id": "native-12345",
    "native_version": "0.1.0",
    "build_id": "2294156a1b2c",
    "platform": "linux",
    "features": ["display.snapshot", "capture.on_demand", "capture.session_open", "input.keyboard", "input.pointer", "clipboard", "accessibility.read", "accessibility.action"],
    "lease_bound": true
  },
  "binary_len": 0
}
```

`build_id` is the commit (with `-dirty` for uncommitted Rust changes) for a
helper built by `scripts/build-native.sh`, `dev` otherwise. It is optional:
older helpers do not send it and the client accepts that.

An unknown major version returns `UNSUPPORTED_PROTOCOL` and closes the
connection. After the handshake, a request with a different minor returns
`UNSUPPORTED_PROTOCOL_MINOR`. A second `initialize` returns
`ALREADY_INITIALIZED`.

## Methods

- `initialize`: validates the version and the required fields.
- `ping`: returns `{"pong": true}`, echoing `params.nonce` if present.
- `capabilities`: the state of `capture.monitor` (backend
  `linux.mutter.pipewire`), `input.keyboard`, `input.pointer`,
  `input.pointer_relative`, `clipboard.read`, `clipboard.write`,
  `accessibility.read`, `accessibility.action` and `window.list`, decided **at
  run time on every request** with cheap checks:
  - capture: `org.gnome.Mutter.ScreenCast` has an owner on the session bus and
    the PipeWire socket exists; otherwise `unavailable` with `reason_code`
    (`BACKEND_UNAVAILABLE` or `DEPENDENCY_MISSING`) and `reason`. No session,
    stream or sharing indicator is opened, no grant needed;
  - keyboard and pointer: the `/dev/uinput` node's metadata; no device is
    opened. With the node present the answer is `degraded` ("access is
    checked on the first explicit request"), without it `DEPENDENCY_MISSING`;
  - accessibility: whether `org.a11y.Bus` has an owner; no application is
    read. `window.list` is `degraded` because only applications that publish
    an accessibility tree are listed;
  - clipboard: `wl-paste`/`wl-copy` on `PATH` and a Wayland socket; nothing is
    run.
- `display.snapshot`: the monitor table (below).
- `capture.frame`: one monitor as a PNG payload (below).
- `capture.session_open`: opens, reuses or rebuilds the Mutter session for all
  monitors of the current layout without reading a frame. Parameters
  `topology_id`, `session_id`, `grant_id`, `revoke_epoch`, `include_pointer`;
  the grant, layout and session rules are those of `capture.frame` (wrong
  grant `REVOKED`, old layout `DISPLAY_CHANGED`). Result: `outcome`
  (`opened` / `reused` / `recreated`, or `not_needed` on KDE Plasma, where
  each frame is a KWin screenshot and there is no session), `monitors`,
  `include_pointer`, `backend`. `desktop_unlock` calls it so the sharing indicator appears with
  the grant; a helper that does not know it answers `UNKNOWN_METHOD` and the
  client leaves the session to the first frame.
- `input.keyboard.ensure`: opens the keyboard device lazily. Takes
  `grant_id`, `revoke_epoch`, `hold_max_seconds`; returns the settle time and
  held keys.
- `input.keyboard.key`, `.key_down`, `.key_up`: the grant fields plus
  `combo`. Aliases and combination order match the Python provider; the
  result carries the current `held` list.
- `input.keyboard.held`: the keys the helper holds.
- `input.keyboard.release_all`: releases every held key explicitly and
  returns their canonical names. A cleanup path: no fresh grant needed.
- `input.keyboard.take_auto_released`: returns, once, the keys the monotonic
  hold timer released.
- `input.pointer.ensure`: opens the pointer devices lazily. The grant fields
  plus `topology_id`, `pointer_speed`, `pointer_max_ms`; returns the position,
  held buttons and settle time.
- `input.pointer.move`: the common fields plus `x`, `y` and optional
  `smooth`. `x`/`y` are **global canvas** coordinates already resolved by the
  Python shot adapter; the method takes no `shot` or `monitor` and never adds
  an offset again. Clamping to the canvas and the global-to-device mapping
  happen once, here.
- `input.pointer.move_by`: relative motion in device units through the
  relative device; marks the absolute position stale (the kernel drops a
  repeated absolute value).
- `input.pointer.click`: the common fields plus `button`, `count` and
  optional `hold_ms` (0-1000, default 60). No coordinates: it clicks where the
  pointer is and sends no absolute event, so it also works after relative
  motion. The Python side always sends `[desktop] click_hold_ms`.
- `input.pointer.drag`: `x1`, `y1`, `x2`, `y2`, `button`; the same minimum-
  duration smoothstep path as the Python provider.
- `input.pointer.scroll`: `amount`, `horizontal`.
- `input.pointer.mouse_down`, `.mouse_up`: `button`; updates the held state.
- `input.pointer.held`, `.release_all`, `.take_auto_released`, `.position`:
  read the held state, release explicitly, take what the timer released, and
  read the last persisted position. Cleanup and read methods need no fresh
  grant.
- `clipboard.read`, `clipboard.write`, `clipboard.clear`: run `wl-paste` /
  `wl-copy` with the same arguments the Python path uses. All three take
  `grant_id` and `revoke_epoch`; with a wrong grant the program never runs
  (`REVOKED`); an unknown field is `INVALID_PARAMS`.
  - `read` returns the first offered type and its bytes **in the response
    payload** (`{"empty": false, "mime": ...}`), or `{"empty": true, "mime":
    null}` when empty or unreadable. If the grant is revoked while reading, no
    content is returned.
  - `write` also takes `mime`; the content is the **request** payload, never
    in the header. `clear` empties the clipboard.
  - Each program has a 10 s timeout and is killed after it. `wl-copy`'s
    stdout/stderr go to `/dev/null`, since the clipboard owner stays in the
    background and would hold a pipe open.
  - Errors: program missing `DEPENDENCY_MISSING`, timeout `TIMEOUT`, program
    failed `EXECUTION_UNKNOWN`, content over 128 MiB `UNSUPPORTED`. Only the
    first MIME type is kept; `capabilities` says so in `limitations`. Shared
    fixture: `tests/fixtures/native/clipboard_cases.json`.
- `accessibility.dump`, `accessibility.windows`, `accessibility.focused`:
  read the tree from AT-SPI's own bus (found with `org.a11y.Bus.GetAddress`,
  connected on first use); no GI, GTK or GLib main loop. All take `grant_id`
  and `revoke_epoch`; the grant is checked again after reading, and a revoke
  during the read returns no tree. Unknown fields are `INVALID_PARAMS`.
  - `dump` also takes `target`, `interactive_only`, `max_nodes`,
    `deadline_ms` (defaults `focused`, `true`, 400 (max 2000), 15000 ms (max
    20000)).
  - The answer has the Python helper's shape: `app`, `app_bus`, `app_pid`,
    `same_name`, `scope`, `window`, `window_ref`, `nodes`, `truncated`, and
    `snapshot`, the helper's 12-hex-digit id for this dump. Each node carries
    `path`, `ref`, `role`, `name`, `states`, `actions`, `editable`, `depth`.
  - The walk matches the Python helper step for step: depth first, left to
    right, at most `max_nodes * 25` visits and depth 100; a node's children
    are read together (at most 32 at once).
  - Every call has a 2 s timeout; on expiry `TIMEOUT`, never a partial list.
  - Roles are named from the `GetRole` number with libatspi's table;
    `GetRoleName` is asked only outside the table (GTK4 answers "application"
    for a window frame and "button" for a push button there).
  - Action names come from `Action.GetName(i)`; `GetActions` is localized.
  - `windows` goes two levels down (applications, windows); `focused` finds
    the focused window without walking. Errors: no such application or no
    focused window `TARGET_MISMATCH`; a partial name matching two
    applications `ELEMENT_AMBIGUOUS`; no bus `BACKEND_UNAVAILABLE`. Messages
    match the Python helper's word for word. Shared fixture:
    `tests/fixtures/native/accessibility_cases.json`.
- `accessibility.act`, `accessibility.set_text`: click or write into a node
  of a dump. Both take `grant_id`, `revoke_epoch`, `snapshot`, `ref`; `act`
  also `action` (default `click`). `set_text`'s text is UTF-8 **in the
  request payload** (otherwise `INVALID_PARAMS`).
  - **Only its own dumps.** The helper keeps the last 8 successful dumps: the
    app's bus name, the window for a focus dump, and per node bus name +
    object path, index path, role and name. An action names a `snapshot` and
    the node's `ref` (object path); everything else comes from the helper's
    record, not the request. Another helper's snapshot (another process, or
    this one restarted under a new grant) is unknown: `ELEMENT_STALE`, take a
    new `ui_dump`.
  - **Identity check**, as the Python helper's `_resolve`: same application
    (bus name), same object (index path first, then a search for bus name +
    object path inside the same target, at most 10 000 nodes), same meaning
    (role and name). Two objects with the same path on two buses in one dump
    are `ELEMENT_AMBIGUOUS`. Not found within 8 s: `TIMEOUT`, nothing sent.
  - The grant is checked **again** after the target is found and right before
    the call; a revoke in between sends nothing.
  - **The application's answer is checked.** `DoAction` returning false (a
    disabled GTK4 button) is `ACTION_UNSUPPORTED`. Text is written with
    `EditableText.SetTextContents` (no length argument; GTK4 ignores
    `InsertText`'s length), then read back with `CharacterCount` +
    `GetText(0, count)` and compared, waiting at most 300 ms: a difference is
    `TEXT_MISMATCH`, with counts only in the message. Without a Text
    interface the write cannot be verified and is not an error
    (`verified: false`, `now_chars: -1`).
  - **No retries.** `DoAction` and `SetTextContents` wait 5 s; no answer, or
    the application leaving, is `EXECUTION_UNKNOWN` (it may have happened) and
    the call is never sent again. The Python side reports its own request
    timeout (20 s) and a helper crash the same way.
  - Answers carry the Python helper's fields. `act`: `app`, `ref`, `role`,
    `name`, `action`, `resolved_by` (`path`/`moved`), `returned`. `set_text`:
    `app`, `ref`, `role`, `name`, `replaced_chars`, `now_chars`,
    `resolved_by`, `verified`.
  - Categories: `ELEMENT_STALE`, `ELEMENT_AMBIGUOUS`, `TARGET_MISMATCH`
    accessibility, retryable; `ACTION_UNSUPPORTED`, `TEXT_MISMATCH`
    accessibility, not retryable; `TIMEOUT` execution, retryable;
    `EXECUTION_UNKNOWN` execution, not retryable.
- `cancel`: validates `params.target_id` and today answers `canceled: false`;
  capture has its own 1-8000 ms timeout and lifecycle gates, and the
  dispatcher does not run requests concurrently yet.
- `shutdown`: writes a framed success answer, then exits cleanly.

Any other method returns `UNKNOWN_METHOD`; the connection stays usable.

State-changing input requests must match exactly the grant id and revoke
epoch the helper bound to at `initialize`. Pointer requests must also match
the current display snapshot's `topology_id`, or they fail with
`DISPLAY_CHANGED` before any event is injected; on a layout change the old
pointer device is closed and rebuilt for the new canvas. `pointer.json`
keeps its `{x, y, t}` format and 300 s age rule; opening a device does not
clear it. Revoke, shutdown or a failed event write release held keys and
buttons. The hold timer runs without waiting for another request. Input
requests are never replayed across a process restart: an uncertain write is
not sent twice.

**A helper serves one grant.** It binds to the grant it reads at
`initialize` and never rebinds, while every `desktop_unlock` (from any
process) writes a new `grant_id`. The old helper's watchdog then closes its
resources and every grant-bearing request returns `REVOKED`. On the Python
side `backends/rust.py` -> `GrantBoundHelper` notices a new grant (id +
revoke epoch), asks the old helper to release, stops it and starts a new
one; the request goes to the new helper **once**. Capture and input share
this class.

## Command line

Without arguments the helper speaks the protocol on stdin/stdout. Two flags
are accepted; neither starts the protocol or touches the session bus,
PipeWire or the state directory, and both exit `0`:

- `--version`: one line,
  `pcbridge-native 0.1.0 (build …, protocol 1.0, x86_64-unknown-linux-gnu, release)`.
- `--build-info`: one JSON object with `name`, `version`, `build_id`,
  `protocol {major, minor}`, `target`, `profile`, `test_harness`.

Any other argument prints `unsupported command-line arguments` to stderr and
exits `2`. A helper built with the `test-harness` feature also accepts
`--test-mode` and reports `"test_harness": true`: a deterministic fake that
reads no real screen. `scripts/build-native.sh` refuses to package it and
`pcbridge doctor` flags it as an error.

## `display.snapshot`

The ordered monitor table and the layout id. **Read-only metadata**: no
pixels, no devices, no grant (like the host's `screen_info`). The rules live
in `pcbridge-core::display`; this method carries Mutter's `GetCurrentState`
(including its `layout-mode` property) to the resolver.

```json
{
  "topology_id": "v1|0,0,1920,1080,1.0000,0,0|1920,0,1920,1080,1.0000,0,1",
  "canvas": [3840, 1080],
  "monitors": [
    {"index": 1, "connector": "DP-4", "x": 0, "y": 0, "width": 1920,
     "height": 1080, "scale": 1.0, "pixel_ratio": 1.0, "primary": false,
     "name": "…", "transform": 0, "serial": "…"}
  ]
}
```

`pixel_ratio` (2.0) is framebuffer pixels per canvas unit: 1.0 in Mutter's
physical layout mode, the scale in the logical mode. A monitor whose ratio
differs from its scale adds `,p` to its part of `topology_id`; older hosts
ignore the field.

If the layout cannot be resolved the answer is `DISPLAY_MAPPING_UNKNOWN`
and **nothing is guessed**: a monitor without a current mode, an unknown
connector, an empty connector list and a non-positive scale are refused.
"Take the first mode" or "fall back to the first monitor" is how a capture
silently lands on the wrong screen.

The session-bus connection is lazy: measured with `strace -e trace=connect`,
`initialize` -> `capabilities` -> `shutdown` makes 1 connection (the
desktop-state provider), and the same sequence with `display.snapshot`
makes 2. The cache is invalidated by Mutter's `MonitorsChanged` signal, not
a timer, so a monitor plugged in shows up in the next snapshot.

## `capture.frame`

The request names one monitor, the layout the caller knows, and the grant
the helper is bound to:

```json
{
  "protocol": {"major": 1, "minor": 0},
  "id": "client-a:18",
  "method": "capture.frame",
  "params": {
    "display_id": "mutter:DP-4",
    "topology_id": "v1|0,0,1920,1080,1.0000,0,0|1920,0,1920,1080,1.0000,0,1",
    "session_id": "capture-session-id",
    "grant_id": "grant-id",
    "revoke_epoch": 7,
    "timeout_ms": 8000,
    "freshness": "after_request",
    "include_pointer": true
  },
  "binary_len": 0
}
```

`display_id` is scoped (`mutter:<connector>` on GNOME, `kwin:<output>` on
KDE Plasma) and at most 256 bytes; an id whose scheme is not the running
compositor's is refused, never mapped;
`topology_id` is not empty and at most 16 KiB; `session_id` and `grant_id`
are not empty and at most 256 bytes. Only `freshness: "after_request"` is
accepted. A layout id that does not match the current snapshot is
`DISPLAY_CHANGED`; an unresolvable connector `DISPLAY_MAPPING_UNKNOWN`; a
grant id or epoch that does not match the helper's binding `REVOKED`. None of
them falls back to the first monitor.

A success header is followed by `binary_len` bytes of raw PNG (no base64 on
this pipe):

```json
{
  "protocol": {"major": 1, "minor": 0},
  "id": "client-a:18",
  "result": {
    "display_id": "mutter:DP-4",
    "topology_id": "v1|...",
    "session_id": "capture-session-id",
    "frame_sequence": 42,
    "frame_timestamp_ns": 151412335,
    "frame_identity_source": "source_monotonic_clock",
    "pixel_size": [1920, 1080],
    "desktop_rect": [0, 0, 1920, 1080],
    "stale_frames": 0,
    "include_pointer": true,
    "revoke_epoch": 7,
    "wait_ms": 58.6,
    "encode_ms": 34.0,
    "backend": "linux.mutter.pipewire",
    "mime_type": "image/png"
  },
  "binary_len": 248713
}
```

The frame identity's clock fields are not interpreted without
`frame_identity_source`. If the producer supplies `SPA_META_Header`, its
sequence and PTS are carried unchanged (`spa_meta_header`); Mutter on GNOME 46
does not, so the sequence is a local counter for the source's lifetime and
the timestamp is nanoseconds since the source started
(`source_monotonic_clock`). Freshness is decided by a separate local
`Instant` taken when the frame arrives.

A refused `capture.frame` **never** carries a payload (`binary_len: 0`), and
a refusal does not break the connection: the next valid request gets its
frame (both tested against the real helper in `capture_frame_ipc_live.rs`
with `PCBRIDGE_TEST_CAPTURE=1`). The session, the PipeWire thread and the
display reader are created on the first real capture; `initialize`,
`capabilities` and `ping` open no sharing. After a failed capture the
session/node mapping is dropped, so a retry never uses an old node id. State
machine, closing triggers and measurements: [capture.md](capture.md).

## Matching and the error envelope

Every response carries its request's string `id`. A client may send several
requests without waiting and must match answers by id.

```json
{
  "protocol": {"major": 1, "minor": 0},
  "id": "client-a:2",
  "error": {
    "code": "UNKNOWN_METHOD",
    "message": "method 'example.unknown' is not available",
    "retryable": false,
    "category": "protocol"
  },
  "binary_len": 0
}
```

## Deterministic test mode

The fake backend exists only with the non-default `test-harness` Cargo
feature and additionally needs `--test-mode`:

```bash
cargo test --workspace --all-targets --features pcbridge-native/test-harness
```

It reports the fixed `test-native-instance`, platform `test` and backend
`test.fake`, and claims no desktop support. Keyboard and pointer are bound to
fake devices that emit nothing. The clipboard runs only the programs a test
names in `PCBRIDGE_TEST_WL_PASTE` / `PCBRIDGE_TEST_WL_COPY` (otherwise
`UNSUPPORTED`), so it never reaches the user's clipboard. `accessibility.*`
reads only the `PCBRIDGE_TEST_A11Y_DESKTOP` desktop of the
`PCBRIDGE_TEST_A11Y_FIXTURE` file (otherwise `UNSUPPORTED`);
`test.accessibility_desktop` (`{"desktop": ...}`) switches later reads to
another fixture desktop, to imitate a tree changing between dump and action,
without clearing the dump records, and `test.accessibility_performed` returns
the actions (`[path, action]`) and texts that took effect. This mode exists
only for byte-level contract tests.

## The Python supervisor

`NativeClient` starts the helper on the first native request, over three
pipes separate from the MCP transport. Discovery order is fixed:
`$PCBRIDGE_NATIVE_BIN`, `[native] binary_path`, then the package path
`pcbridge/_native/<target>/pcbridge-native`. An invalid explicit path does not
silently fall back to a lower-priority binary (`NATIVE_NOT_FOUND`); the helper
is never downloaded, built at run time or searched on `PATH`.

Selection per subsystem (`[native] capture / input / accessibility`):
`auto` (default) uses the helper when it is found and offers the capability,
otherwise the Python path with a visible `degraded` reason in
`system_capabilities`; `rust` never falls back and fails instead; `python`
ignores the helper. An explicit `[desktop] capture_backend =
"gnome-screenshot"` keeps capture on the Python path under `auto`.

The native accessibility reader lives in a grant-bound helper. Without a
grant (for example `screen_info` listing windows before `desktop_unlock`),
the window list and focused window come from the Python helper. Short ids
and the last-dump record are produced in Python (`uitree`) for both readers;
`ui_click` / `ui_set_text` go to the provider that made the dump, with its
snapshot and the node's object path. The password-field rule is applied in
Python, before the helper is asked.

The reader, writer and stderr-drain threads are separate. Request ids are
never reused, even across restarts, and answers are matched by id. At most 16
requests may be pending; the next one gets `BUSY`. A request that cannot be
framed locally returns `INVALID_FRAME` without stopping a healthy helper.

Every field of the `initialize` result is validated before the helper is
declared usable. A major/minor mismatch is `PROTOCOL_MISMATCH`, a broken
envelope or handshake `INVALID_FRAME`, EOF or an unexpected exit
`NATIVE_CRASHED`. Losing the process completes every pending request of that
generation; nothing is replayed on a new process. A new request may start a
new helper; a helper found dead is removed from the process registry at once.

When a deadline passes, the pending request is removed atomically and a
best-effort `cancel` is sent. `close()` tries a framed `shutdown`, then after
two seconds terminates and kills, and always reaps the child. Job children
do not inherit the helper's pipe descriptors.

The helper receives only the environment variables a graphical session
needs; names containing `PASSWORD`, `TOKEN`, `SECRET` or `API_KEY` are
blocked. Its stderr is drained continuously, never written to stdout or an
MCP answer, and only the last 64 KiB are kept in memory.

## Grant, revoke and the process registry

`state_dir/desktop_unlock.json` keeps the backward-compatible fields `until`,
`hard_until`, `reason`, `granted`, `granted_by`; new grants also carry
`schema_version: 1`, a random `grant_id` and a monotonic `revoke_epoch`.
Python reads old, `until`-only grants; the helper binds only to a new-format,
active grant that has not passed its hard ceiling.

Read-modify-write on the grant takes a Unix advisory lock on the separate,
fixed `desktop_unlock.lock`. JSON is written to a 0600 temporary file in the
same directory, `fsync`ed and atomically renamed. The state directory is
0700, state and lock files 0600. `desktop_lock` first increments the epoch
and zeroes `until` and `hard_until`; cleanup starts after that visible
revoke. An old heartbeat cannot extend a new grant, because its grant id and
epoch no longer match.

The helper binds once. Every protected dispatch revalidates the file, and a
100 ms lease watchdog closes open native resources fail-closed on a grant
change, revoke, expiry, or a missing or corrupt file. The watchdog runs on
its own thread, apart from the D-Bus observations, so a hung session service
cannot delay a revoke. The native protocol has no method to create or extend
a grant.

## Desktop state and fail-closed rules

The screen lock is a three-state observation (`known_locked`,
`known_unlocked`, `unknown`), user activity `known` (with a non-negative
`idle_ms`) or `unknown`; neither layer reduces them to booleans.

- Locked: reads and writes fail with `SCREEN_LOCKED`.
- Lock state unknown: reads and writes fail with `LOCK_STATE_UNKNOWN`.
- Activity unknown: writes fail with `ACTIVITY_UNKNOWN`.
- The user more active than the idle guard allows: writes fail with
  `USER_ACTIVE`.
- `force=true` skips only the activity check, never the lock, grant, revoke
  or expiry checks.
- Batches and tasks check activity once at the start; it is not reread inside
  a running batch (uinput events reset the idle timer).

The native lock watcher listens to `org.gnome.ScreenSaver.ActiveChanged`
(on KDE Plasma `org.freedesktop.ScreenSaver.ActiveChanged`, which KWin owns)
and treats a lost connection as `unknown`; an active native resource closes on a
locked or unknown observation. D-Bus method calls use finite timeouts.

Grant-bound resources register with `Lifecycle::register_fail_closed`, and
the watchdog tells them to **close** on a revoke or lock edge. Notification is
edge-triggered (a revoked grant does not wake them ten times a second), and
whoever notices the revoke first on the request path owns the edge, so a
close never runs twice. If a resource's lock is busy the watchdog does not
wait; the operation in flight cancels itself at its next gate.

`desktop_unlock` does not need uinput to create a grant. If capture works but
pointer or keyboard does not, the grant still opens; the structured result
(type `pcbridge.desktop.grant`) carries the grant id, an authorization
snapshot and a `capability_limitations` map, which neither hides the grant nor
claims unusable input.

Every helper has a 0600 record under `runtime_dir/pcbridge/native/` (dirs
0700) with its PID, process start id and instance id. Cleanup re-matches the
PID and start id before sending a signal, so a reused PID is never signaled.
The Python screen-sharing helper is still found by its exact executable path
and UID (`kill_helpers()`).

## Rolling back to the Python paths

Set `capture`, `input` or `accessibility` under `[native]` to `"python"`, then
`pcbridge lock` and `pcbridge update` (the daemon restarts when idle). Do not
restore an old active grant; the agent opens a new one with
`desktop_unlock`.

## Exit and logging rules

- A clean EOF, `shutdown` and an incompatible major version close the
  connection without leaking resources.
- stdout never carries logs, banners or panic text.
- stderr never carries frame contents, payloads, path parameters or other
  private data.
- The helper never reads `config.toml` and never receives a password or
  token.

## `pcbridge-native idle-watch` (KDE Plasma)

KWin does not answer idle time over D-Bus (`GetSessionIdleTime` is "not
supported on this platform"). `pcbridge-native idle-watch` is a separate,
long-running mode of the same binary, outside this protocol: a Wayland
client that holds an `ext_idle_notifier_v1` input-idle notification with a
1000 ms timeout and writes `$XDG_RUNTIME_DIR/pcbridge/idle.json`
(`version`, `pid`, `timeout_ms`, `idle`, `since_unix_ms`) on every change.
While idle, `since_unix_ms` is the last input, so idle time is
`now - since_unix_ms`; while active it is only known to be below the
timeout and is reported as 0, the safe side for the idle guard. Readers trust the file only while its `pid` is alive and runs
`idle-watch`; anything else is `unknown`. The daemon starts and supervises
it on Plasma while desktop control is enabled.
