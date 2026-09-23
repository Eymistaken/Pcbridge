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
