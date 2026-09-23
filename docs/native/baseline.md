# Native migration baseline

Before any desktop subsystem moved into the Rust helper (2026-09-08), the
public behavior was pinned by contract tests so that a provider change could
not alter it. Those contracts still hold every backend to the same answers.

## Pinned contracts

`tests/contracts/` checks, independently of the provider:

- MCP tool names, annotations (all four hints since 2.0), selected input
  schema fields and defaults;
- in `screen_capture` results the text block comes before the image blocks;
- monitors are ordered by position; the primary can be on the right;
- portrait and fractional-scale geometries and the combined canvas size;
- each monitor is cropped from its own frame first, then resized;
- shot ids, record lookup and server-side conversion of picture
  coordinates;
- the ambiguity guard for a fresh scaled shot; stale or full-size shots
  convert as given;
- a shot written elsewhere with `pcb-shot --out` is still found.

Fixtures are small JSON geometries and solid-color PNGs made at run time; no
real screenshot is committed. The tests read only `config.example.toml` or
synthetic `Config` objects, never a private `config.toml`.

## Provider parity

The capture contract runs every backend registered in
`CAPTURE_PROVIDER_FACTORIES` (the Python adapter and the native one) through
the same synthetic canvas and the same assertions. Input
(`tests/fixtures/native/input_events.json`), accessibility and display tables
(`display_state_cases.json`, `mixed_scale_cases.json`, `layout_matrix.json`)
work the same way: one fixture, read by the Python tests and the Rust tests.
