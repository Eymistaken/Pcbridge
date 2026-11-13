# Troubleshooting

Start with `pcbridge doctor`: it checks the install, config, daemon, client
registrations, readiness, agents, desktop and native helper, and prints the
fix next to each problem. `pcbridge doctor --json` is the same for scripts;
`pcbridge report` writes a redacted bundle for a bug report.

| Symptom | Look at |
|---|---|
| A client does not list pcbridge | `pcbridge clients` shows what each client has registered; `pcbridge connect NAME` fixes it (Claude Code must use the user scope). Restart the client afterwards. Pi also needs the `pi-mcp-adapter` extension. |
| A client still behaves like 1.x | It was started before the install and keeps its old server process; restart it. |
| "the pcbridge server restarted while this request was running" | The daemon was restarted or crashed mid-call. The error is retryable; the relay reconnects on its own. |
| `pcbridge: command not found` right after the install | `~/.local/bin` is not on `PATH` (Arch's default profile leaves it out); setup printed the line to add to `~/.bashrc`. MCP clients use the full path and work anyway. |
| Setup offered no desktop packages and set the profile to `core` | The desktop is not supported (GNOME or Plasma on X11, or a desktop other than GNOME, KDE Plasma, Hyprland, Cinnamon or Xfce); the plan's first lines say what was found. After switching to a supported session (Wayland for GNOME, Plasma and Hyprland; X11 for Cinnamon and Xfce), run `pcbridge setup` again: it detects again and returns its own `core` to `full`. |
| Cinnamon or Xfce: desktop tools say the session is unknown right after login | The daemon started before the X session; it reads the session from logind on the next desktop call (at most every 2 s). `pcbridge doctor` shows what it found. |
| Cinnamon or Xfce: the first action after unlocking the screen is refused (no fresh frame evidence) | The grant frame proves itself again a few hundred milliseconds after the unlock (0.27 s measured on Xfce 4.20); the refusal is retryable. |
| The native helper is reported as skipped for glibc | The release helper needs glibc 2.35 (Ubuntu 22.04, Debian 12 and newer); on an older system desktop control is not available until a helper built there is named in `[native] binary_path`. A helper built on another machine needs that machine's glibc; on a git checkout, `scripts/build-native.sh` builds one against this system's glibc. |
| Desktop tools say "The pcbridge native helper was not found" | The helper is the only desktop backend since the Python paths were removed after 2.8. The message ends with the fix for this installation: `pcbridge doctor --fix` downloads the release's helper (a git checkout with Rust can run `scripts/build-native.sh`), and a distribution package is reinstalled. |
| `pcbridge doctor` warns that `[native] ... = "python"` is no longer supported | The config predates the removal of the Python desktop paths; it is read as `auto`. Set the line to `"auto"` or delete it. |
| The daemon does not start | `pcbridge logs`. Exit code 78 means the config: the first log line says what to fix. |
| Desktop tools say the screen lock state cannot be read | The selected GNOME, KDE Plasma, or Hyprland Wayland session cannot provide authoritative lock state. Run `pcbridge doctor` in that session. |
| Hyprland refuses an action with `BACKEND_UNAVAILABLE` | A current visible-frame proof or capture protocol is unavailable. `pcbridge doctor` checks the helper, selected IPC session, idle watcher, and current grant frame; retry only after the condition clears. |
| Plasma: "User activity could not be read" (`ACTIVITY_UNKNOWN`) | KWin keeps idle time off D-Bus; the daemon runs `pcbridge-native idle-watch` for it while desktop control is enabled. `pcbridge doctor` ("idle time") shows whether it answers; it needs the native helper. |
| Plasma: screen capture says `PERMISSION_REQUIRED` | KWin gives screenshots only to a program a `.desktop` file names. `pcbridge setup` writes it (`pcbridge doctor`: "KWin screenshots"); KWin follows a new file within seconds. |
| Plasma: `ui_dump` does not see a Qt application | Qt joins the accessibility bus only while `org.a11y.Status.IsEnabled` is on; `desktop_unlock` turns it on for the grant. An application started with it off appears within about 2 s. |
| "Desktop control is locked" | Call `desktop_unlock` first; the grant also closes 90 s after the last desktop action. |
| `[desktop] enabled` is true but nothing moves | `pcbridge doctor`: `/dev/uinput` must be writable by you; the fix line has the exact commands. |
| A capability says `DEPENDENCY_MISSING` | `system_capabilities` names the package to install next to it. |
| `find_text` says OCR is unavailable | `sudo apt install tesseract-ocr` (Arch: `sudo pacman -S tesseract tesseract-data-eng`) |
| A click lands on the wrong monitor | Give the screenshot's `shot=` id with picture coordinates; never convert them yourself. |
| "The screen layout changed after <shot> was taken" | A monitor was added, removed or moved: take a new screenshot. |
| A window does not come forward quickly | On GNOME, the extension loads at login; until then pcbridge falls back to GNOME search (seconds instead of milliseconds). `pcbridge doctor` shows which extension version the running shell has. On Plasma a KWin script raises it (~90 ms); KRunner is the fallback. Hyprland selects and verifies an exact compositor window identity. |
| Arch: pacman says an upgrade breaks pcbridge's python dependency | The package's venv is bound to the Python it was built with. Rebuild it (`git pull && makepkg -si` in `packaging/arch`) and upgrade both together. |
| The daemon fails with "cannot import name '__version__'" | An older unit ran without `python -P`, and a checkout named `~/pcbridge` shadowed the package. `pcbridge setup` writes the current unit. |
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
