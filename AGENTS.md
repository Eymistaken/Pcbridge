# AGENTS.md

The entry point for **every** coding agent working in this repository:
Codex, Claude Code, Gemini CLI, Antigravity, any other.

## The instructions are in one file: [CLAUDE.md](CLAUDE.md)

Project rules, architecture, the do-not-touch list and the dangers of
testing on this machine are all there. **Read it before you start.** Its name
is historical; its content applies to any agent.

This file stays thin on purpose. It was once generated as a copy of
`CLAUDE.md` and drifted by 83 lines in two days; an automatic replacement
even broke command names (`claude -p` became `Codex -p`). Where two truths
exist, one goes stale, and the stale one is noticed at the worst moment.

## Three things to know at once

1. **You are on the machine pcbridge controls.** A `type` test can type into
   your own terminal and press Enter. Do not skip the testing section of
   `CLAUDE.md`.
2. **Do not call something working that you have not measured.** "It raised
   no error" is not evidence here; verify with a screenshot, a window title,
   a fresh process or a measurement.
3. **`config.toml` holds the password and the static token.** It stays out
   of git; never log, print or commit it.

Open work is in [ROADMAP.md](ROADMAP.md). Do not copy status summaries here.
