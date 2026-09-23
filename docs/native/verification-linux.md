# Linux capture verification

The evidence that made the Rust capture backend the default: it was compared
with the old Python/GStreamer path on the real desktop.

## How to run it

```bash
scripts/build-native.sh
PCBRIDGE_TEST_CAPTURE=1 PCBRIDGE_PARITY_REPORT=/tmp/parity.json \
  ./.venv/bin/python -m unittest tests/live/test_capture_parity.py -v
PCBRIDGE_TEST_CAPTURE=1 ./.venv/bin/python -m unittest \
  tests/integration/test_mcp_capture_delivery.py -k Live
```

The test **opens screen sharing and covers the monitors with a static test
pattern for a few minutes.** It sends no input. Every grant lives in a
temporary state directory; the real grant file is neither read nor written.
Captures stay in a temporary directory and are removed. The test skips itself
when the screen is locked or a Python helper with a real grant is running.

Only a **release** helper is accepted (`--build-info`: `profile: release`,
`test_harness: false`); `rust/target/debug/pcbridge-native` is rebuilt with
the test harness by other tests and returns fake frames.

The pattern (`tests/live/pattern_window.py`, system python3, GTK 4.14) puts
a full-screen window on each monitor: color bars, a checkerboard, a colored
marker naming the monitor by position, and a 16-cell binary counter. The
marker catches a wrong monitor, the counter freshness. The window closes on
`quit`, stdin EOF or its own timeout, so a dead test cannot leave the screen
covered.

## Results (2026-09-13)

Zorin OS 18.1, GNOME Shell 46, Wayland, two 1920x1080 monitors; PipeWire
1.0.5, WirePlumber 0.4.17; i9-11900K, governor `powersave`, load 0.75-1.2;
release helper. Two full runs, the second after the fix below.

| Evidence | Result |
|---|---|
| Monitor identity, offset, size, shot -> global | monitors `1`, `2`, `all` x 1536 and full size: 6/6 identical |
| Static pixel match (target >= 99.5 %) | both monitors, full size and 1536: **100.000 %** |
| `include_pointer=true` | both backends identical; with/without pointer differ only on the pointer's monitor (99.989 %) |
| Every on-demand frame is newer than the request | the counter changed 12 times: 12/12 fresh (old path 12/12) |
| Failures | 0 in 60 warm captures, 60 raw frames, 10 session opens per run |
| Warm capture p95 <= 1.5 x old | 179.5 / 126.0 ms = **1.42**; 175.3 / 131.2 ms = **1.34** |
| No frame after revoke or expiry | none; `GRANT_REQUIRED` / `safety` |
| Sharing ends with its owner | Python host killed: helper gone in 0.11-0.12 s, Mutter session in 0.12-0.13 s |
| Native crash | the crashed helper's Mutter session was released; the next capture came from a new process |
| Real capture without Python GI | a live stdio server with `gi` blocked delivered both monitors |
| Delivery over stdio and HTTP | 2 images each; markers on the right monitor |
| Shell and job tools while the helper is down | `shell_run` and `job_list` worked; the next two captures were delivered |

Timings of the second run (ms, 30 samples; session open 5):

| | old p50 / p95 | native p50 / p95 |
|---|---|---|
| `capture()` end to end, 1536, pattern | 122.9 / 131.2 | 163.7 / 175.3 |
| raw frame | 46.9 / 57.7 | 81.9 / 93.6 |
| session open + first frame | 152.4 / 189.1 | 92.2 / 104.6 |

Native was faster to open a session (no JSON round trip to a helper, no
GStreamer pipeline) and slower per frame (the frame is PNG-encoded in Rust,
sent over IPC and decoded again by Python).

## At the MCP level

Two monitors, stdio server, release helper, p50 of 5 calls after warm-up:
old 4684 ms, native 5113 ms (ratio 1.09), ~99 % of it in the capture
pipeline. That was Python's PNG save with `optimize=True` (3106 ms for 5 %
of file size) and the helper's default PNG compression (445 ms). With both
changed (2026-09-20) the same call, base64 included, takes **789 ms**
median (891, 765, 789 ms); one monitor ~430 ms.

## The bug the first run found

In the first run, revoke and expiry gave no frame but were classified as
`BACKEND_UNAVAILABLE` / `capability`. Two causes: `capture.py` wrapped a
backend exception's typed reason into a plain `CaptureError`, so every typed
refusal of the helper (REVOKED, DISPLAY_CHANGED, FRAME_TIMEOUT, ...) was lost
at the provider; and `NativeScreenCast._grant()` raised an untyped error
without a grant. Both fixed; three typed refusals and a capture without a
grant are contract tests, and reverting either fix turns them red. Tools pass
`SafetyGate` first, so users never saw the wrong category, but it would have
pointed an agent at repairing the helper instead of calling `desktop_unlock`.

## Checks with the user present (2026-09-13)

Both ran in a separate state directory; the real grant was untouched.

- **Screen lock.** The screen was locked with native sharing open and
  unlocked by the user 27.5 s later. While locked (seconds 3 and 11): 0
  Mutter sessions (the helper's lock watcher closed it), `SafetyGate.check`
  and a direct provider call both `SCREEN_LOCKED`, no PNG. After unlocking
  the session did not reopen by itself; the next capture was normal.
- **The sharing indicator.** Three frames taken with `gnome-screenshot`
  (which does not share): no icon; with native sharing open the orange icon
  in the taskbar; after `SIGKILL` of the owner, gone (Mutter session closed
  in 0.11 s).

One known inconsistency: when the lock closes the native session, the
Python side's `is_open()` stays `True`.
