# Troubleshooting

Start with `pcbridge doctor`: it checks the install, config, daemon, client
registrations, readiness, agents, desktop and native helper, and prints the
fix next to each problem. `pcbridge doctor --json` is the same for scripts;
`pcbridge report` writes a redacted bundle for a bug report.

| Symptom | Look at |
|---|---|
| A client does not list pcbridge | `pcbridge connect --dry-run` shows what is registered; Claude Code must use the user scope. Restart the client after `pcbridge connect`. |
| A client still behaves like 1.x | It was started before the install and keeps its old server process; restart it. |
| "the pcbridge server restarted while this request was running" | The daemon was restarted or crashed mid-call. The error is retryable; the relay reconnects on its own. |
| The daemon does not start | `pcbridge logs`. Exit code 78 means the config: the first log line says what to fix. |
| Desktop tools say the screen lock state cannot be read | The session bus is not reachable, or the session is not GNOME on Wayland (the message names it). |
| "Desktop control is locked" | Call `desktop_unlock` first; the grant also closes 90 s after the last desktop action. |
| `[desktop] enabled` is true but nothing moves | `pcbridge doctor`: `/dev/uinput` must be writable by you; the fix line has the exact commands. |
| A capability says `DEPENDENCY_MISSING` | `system_capabilities` names the package to install next to it. |
| `find_text` says OCR is unavailable | `sudo apt install tesseract-ocr` |
| A click lands on the wrong monitor | Give the screenshot's `shot=` id with picture coordinates; never convert them yourself. |
| "The screen layout changed after <shot> was taken" | A monitor was added, removed or moved: take a new screenshot. |
| A window does not come forward quickly | The extension loads at login; until then pcbridge falls back to GNOME search (seconds instead of milliseconds). `pcbridge doctor` shows which extension version the running shell has. |
| The sharing indicator stays after locking | `pcbridge lock` stops every screen-sharing helper of your user, whichever process started it. |
| Remote client "couldn't connect" | `pcbridge remote status`; then `curl -s 127.0.0.1:8765/healthz`. |
| The consent page does not open | `public_url` must match the address in the browser exactly, without a trailing slash. |
| `claude: command not found` inside a job | `list_agents` shows where pcbridge looked; agent CLIs are found in PATH, `~/.local/bin`, npm/bun/cargo bins and nvm. |
| `agy` returns empty output | `pty = true` in `[agents.antigravity]` (it writes only to a terminal). |
| A job stays `running` | `job_output` shows the raw output; the agent probably waits for a confirmation (answer with `tmux_keys`, or cancel). |
| The tunnel does not close | `sudo tailscale funnel reset` |

## Emergency stop

- Hands off now: `pcbridge lock` (`bridgekilit`), the panel menu, or
  `desktop_lock`. Jobs keep running.
- Everything: `pcbridge stop` (daemon and jobs).
- A broken extension: `gnome-extensions disable pcbridge-gorunur@eymistaken.local`
  takes effect immediately; deleting its files does not stop the running
  copy. From a frozen session, switch to a text console (Ctrl+Alt+F3) and
  run the same command.
