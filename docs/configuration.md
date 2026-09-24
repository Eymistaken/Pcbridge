# Configuration

The reference is [`config.example.toml`](../config.example.toml): every
setting is there with a comment that says what it does and why it has its
default. This page is the map.

## Where the file is

pcbridge looks, in order, at: `-c PATH`, `$PCBRIDGE_CONFIG`,
`~/.config/pcbridge/config.toml`, and (for 1.x checkouts) `<repo>/config.toml`.
The file holds the password and the static token: keep it mode 0600
(`pcbridge doctor` warns otherwise, `--fix` repairs it).

Unknown keys are ignored with a warning that names them. A file that cannot
be used (invalid TOML, an unwritable state directory, an invalid value)
stops pcbridge with one line saying what to fix and exit code 78; the
service does not retry it in a loop. `pcbridge serve --check` validates
without starting.

## Changing settings

The terminal UI's Settings tab (`pcbridge` alone in a terminal) and these
commands change any setting without opening the file:

```bash
pcbridge settings desktop           # every setting whose key or description mentions it
pcbridge get desktop.unlock_idle_seconds
pcbridge set desktop.pointer_speed 3000
pcbridge set desktop.gui_launch_blocklist "Text Editor, Firefox"
pcbridge set agents.claude.model_effort '{ opus = "high", sonnet = "medium" }'
pcbridge reset desktop.pointer_speed
pcbridge set auth.password          # asks for it; or --stdin
pcbridge set auth.static_token --generate
pcbridge restart                    # most settings are read when the daemon starts
```

A save never loses anything: the new file is checked with the same loader
the daemon uses (a value it would refuse is not written), the old file is
copied to `config.toml.backup-<timestamp>` next to it, and the new one is
written atomically with mode 0600. Only the changed lines change; comments
and every other block stay as they were. A file edited by hand while the UI
had it open is not overwritten. Secrets are never printed and never taken
from the command line, where they would stay in the shell history.

`config_version` is not offered (the migration owns it), nor is the unused
`[desktop] keyboard_layout`. A new `[agents.<name>]` block is still added
by editing the file; its fields can then be changed like any other.

## Versions and migration

`config_version = 2` marks a 2.0 file. `pcbridge setup` migrates a 1.x file
into `~/.config/pcbridge/` with a timestamped backup and leaves the original
in place. A 1.x file that is loaded as is keeps its old meaning: defaults
that changed in 2.0 (OCR languages `tur+eng`, no pixel-area cap for
screenshots) are filled in with their 1.x values.

## Sections

| Section | What it controls |
|---|---|
| top level | `public_url` (remote address), `host`/`port` (`127.0.0.1:8765`), `mcp_path`, `default_agent` |
| `[server]` | `inline_images`: whether images are sent inside tool results |
| `[auth]` | password, static token, token lifetimes, failed-attempt lockout |
| `[paths]` | `state_dir` (grant, jobs, audit log, OAuth DB, shots), `default_workdir` |
| `[limits]` | output size, job timeouts, `audit_max_bytes` (log rotation) |
| `[native]` | `capture` / `input` / `accessibility`: `auto` (native helper when packaged), `rust` (the helper, or fail) or `python`; `binary_path` |
| `[desktop]` | `enabled` (**false** by default), grant length and sliding window, the idle guard, rate limit, pointer speed, hold limit, screenshot scaling (`screenshot_scale_long_edge = 1536`, `screenshot_max_pixels`), shot lifetimes, the capture backend, batch budget and focus checks, OCR languages, `computer_task` agent |
| `[agents.<name>]` | a coding agent: command, resume syntax, output parser, `pty`, models, efforts, aliases |
| `[tools]` | `profile`: `full` (default), `core`, `desktop` |

On KDE Plasma no new setting is needed. Three existing ones matter there:
capture works only through the native helper (`[native] capture` `auto` or
`rust`, and `[desktop] capture_backend = "auto"`), and
`[desktop] unlock_notification` is the grant's only visible signal, so it
should stay `true`.

## Adding an agent

Agents live only in the config; no Python changes. Try the CLI by hand
first: `codex exec "hello" | cat`. If nothing comes out through a pipe, the
CLI needs a terminal: set `pty = true`. Ask the CLI for its model ids
(`agy models`) instead of guessing; every guessed id was wrong. When a CLI
takes an effort flag only for some models, say so with `model_efforts` (an
empty list means "never add the flag").

Never set `ANTHROPIC_MODEL` or `CLAUDE_CODE_EFFORT_LEVEL` in the service's
environment: jobs inherit it and it silently overrides `--effort`.
