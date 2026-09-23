# Contributing

## Set up

```bash
./install.sh                 # editable install into .venv, native helper if Rust is there
scripts/build-native.sh      # rebuild the native helper (release; never measure a debug build)
```

`pcbridge serve --check` validates a config; `pcbridge serve --no-socket`
runs the daemon in the foreground for debugging.

## Tests

The suites are plain scripts or unittest; pytest is optional.

```bash
./.venv/bin/python tests/test_models.py           # agent resolution and output parsers
./.venv/bin/python tests/test_desktop.py          # desktop logic, sends NO input
./.venv/bin/python tests/test_test_safety.py      # the live-test selector's safety
./.venv/bin/python -m unittest discover -s tests/contracts -t .
./.venv/bin/python -m unittest discover -s tests/integration -t .
for t in gnome-extension/tests/*.js; do gjs -m "$t"; done
(cd rust && cargo test --workspace --locked --no-fail-fast)
```

These non-live suites are hermetic: they do not read your GNOME session,
your monitors or your config (CI runs them with no session at all). Two
contract tests keep the project honest: `test_english_only.py` (no Turkish
in anything a user or model reads) and `test_doc_links.py` (every relative
Markdown link resolves).

`tests/readiness/check.py` answers "can a fresh client use pcbridge right
now, with no manual step": it spawns every registered client command with
realistic environments (`--desktop` also opens and closes the grant).

End to end against a running daemon's HTTP port:

```bash
PCBRIDGE_TEST_PASSWORD=... PCBRIDGE_TEST_STATIC=... PCBRIDGE_TEST_NO_AGENT=1 \
  ./.venv/bin/python tests/test_e2e.py
```

**Always set `PCBRIDGE_TEST_NO_AGENT=1`**: without it section 12 runs a real
`claude -p` and spends quota (it once used up a daily limit).

### Live tests

Real devices are opt-in, one flag per risk:

| Flag | Enables |
|---|---|
| `PCBRIDGE_TEST_CAPTURE=1` | real screenshots (writes images, briefly opens screen sharing) |
| `PCBRIDGE_TEST_INPUT=1` | real uinput keyboard and pointer |
| `PCBRIDGE_TEST_ATSPI=1` | real accessibility reads (and, with INPUT, actions on the test's own windows) |
| `PCBRIDGE_TEST_BATCH=1` | a real batch; needs INPUT too |

```bash
PCBRIDGE_TEST_CAPTURE=1 PCBRIDGE_TEST_INPUT=1 PCBRIDGE_TEST_ATSPI=1 PCBRIDGE_TEST_BATCH=1 \
  ./.venv/bin/python tests/test_desktop.py
PCBRIDGE_TEST_ATSPI=1 PCBRIDGE_TEST_INPUT=1 ./.venv/bin/python -m unittest discover -s tests/live -t .
```

### KDE Plasma and Arch Linux: the test VM

Plasma and Arch are tested in a QEMU/KVM VM, never on the desktop you work
on: input sent inside the VM stays in the VM.

```bash
scripts/dev/arch-vm.sh create      # download and verify the Arch cloud image
scripts/dev/arch-vm.sh start       # boot it headless (VNC and SSH on 127.0.0.1)
scripts/dev/arch-vm.sh provision   # Plasma, GNOME, build tools; log in to Plasma
scripts/dev/arch-vm.sh sync        # copy this checkout to ~/pcbridge in the VM
scripts/dev/arch-vm.sh session '.venv/bin/python tests/live/kde/check_mcp.py'
```

`tests/live/kde/` holds the Plasma probes and the MCP end-to-end check; each
exits without doing anything outside a Plasma session.
`tests/live/kde/headless_smoke.sh` needs no VM session: it starts a virtual
KWin under its own `dbus-run-session` (CI runs it in an Arch container).

## Testing on your own desktop: the rules

You are on the machine pcbridge controls. uinput clicks and keys go wherever
the focus is: a `type` test can type into your terminal and press Enter.

1. Test input in an empty window first (`gnome-text-editor`).
2. Move first, verify with a screenshot, then click.
3. Emergency stop: `pcbridge lock`, or `pcbridge stop`.
4. Do not start long or repeated input runs without telling the person at
   the machine.
5. After a click, assume the focus moved.
6. A screenshot goes stale: act on its coordinates right away.
7. Never lock the real screen, log out or restart GNOME Shell to test
   something; use a nested or headless shell (`gnome-extension/nested.sh`,
   or `gnome-shell --headless --virtual-monitor WxH` under its own
   `dbus-run-session`). Collect the services it leaves behind
   (`nested.sh --clean`).

## Adding a tool

1. Write it in `pcbridge/tools.py` inside `register()`, and add its four
   hints to `TOOL_HINTS` (registration fails without them). A desktop tool
   also joins `DESKTOP_TOOLS`.
2. Its docstring and `Field(description=...)` are English and say **when**
   to use it, not only what it does. Everything it returns is English too.
3. Plain text returns `str`, trimmed with `jobslib.tail_chars(text, 4000)`;
   a result with images returns `list[ContentBlock]`. Desktop tools declare
   `output_schema=None` and return `ToolResult(..., is_error=True)` with the
   readable text in `content` and stable fields in
   `structuredContent.error`. The permission scope is `pcbridge.desktop`
   for the grant, or `os.capture`, `os.pointer`, `os.keyboard`,
   `os.accessibility`, `os.window`, `os.session` for OS permissions.
4. No call may block for more than 110 s; long work goes to `jm.start()`.
5. Paths go through `_resolve_dir` / `_resolve_file`.
6. A desktop tool passes the gate (`_guard()` / `SafetyGate.check()`) and is
   audited with `gate.audit(...)`: what was done, never the content.
7. A new setting goes into `config.py` (and is actually read) **and** into
   `config.example.toml` with its comment.
8. Add checks to the suites and to `tests/test_e2e.py`.

FastMCP is pinned to 3.4.5; verify `ToolResult` and `output_schema=None`
behavior before changing it.

## Adding an agent

Only `config.toml`; see [configuration.md](../configuration.md#adding-an-agent).

## Do not touch

- `MetadataNormalizer` and `BasicAuthFormShim` in `pcbridge/app.py`: Google's
  OAuth client works only because of them. They are no-ops for others.
- The OAuth logic in `auth.py`, unless the task is about it.
- `[desktop] enabled` defaults to `false`.
- Not solutions: switching to X11, removing `--dangerously-skip-permissions`
  from agent commands, logging secrets, enabling desktop control by default.

## Measurements

A design decision that depends on a measurement is written after the
measurement, not before. Record the result with its number and its reason
in [measured-facts.md](measured-facts.md).
