# Windows scope

The scope of the Windows port (ROADMAP stage 8), written after the W0
measurements in [measured-facts.md](measured-facts.md#windows-11-baseline-for-the-port-w0-2026-11-15).
Target: **Windows 11, x64, full parity** with the Linux desktop (the
maintainer's decision, 2026-11-15): every one of the 40 tools ships, the
terminal UI, install and `pcbridge upgrade`, remote access and desktop
control. Where Windows itself forbids something, the tool says so with a
reason instead of failing silently; those limits are listed below.

## Principles

- Linux behavior does not change. Every step keeps the Linux suites green.
- Tool names stay. `tmux_*` keep their names on Windows (renaming would
  break connected clients); their descriptions say what they drive there.
- One coordinate space and `capture.to_global()` as its only interpreter;
  `batch.py` knows no devices.
- The security model carries over layer by layer (below).
- Decisions that depend on a number are made after the measurement.

## Tools on Windows

All 40 ship. "Mechanism" is what replaces the Linux one.

### Agents and jobs

| Tool | Mechanism on Windows |
|---|---|
| `list_agents` | Executable lookup with PATHEXT and Windows install dirs (`%USERPROFILE%\.local\bin` for `claude.exe`, `%APPDATA%\npm`, `%LOCALAPPDATA%\Programs`) |
| `agent_run` | `jobrunner` starts the argv directly (no bash), output to `out.log`, exit code to `exit_code`. An npm `.cmd` shim never receives the prompt through cmd.exe: node and the JS entry are called directly, or the call is refused |
| `job_status`, `job_output`, `job_list` | Unchanged logic; process liveness from `OpenProcess` + creation time, not `/proc` |
| `job_cancel` | `CTRL_BREAK_EVENT`, then `TerminateJobObject` after 3 s; exit 130 |

Each job runs in its own Job Object, so it outlives a daemon restart the way
a systemd scope does on Linux.

### Live terminal

| Tool | Mechanism on Windows |
|---|---|
| `tmux_list`, `tmux_start`, `tmux_send`, `tmux_keys`, `tmux_capture`, `tmux_kill` | `pcbridge termhost NAME`, one process per session that owns a ConPTY and renders it with `pyte` (measured: output and a loop read back exactly). It survives a daemon restart and listens on its own pipe; `pcbridge attach NAME` opens the same session in the user's terminal, as `tmux attach` does |

### Shell and files

| Tool | Mechanism on Windows |
|---|---|
| `shell_run`, `shell_run_background` | `[shell] program`: `pwsh`, else Windows PowerShell; Git Bash selectable. UTF-8 set explicitly (a redirected child defaulted to cp1252 and failed on "ğ") |
| `fs_list` | Hidden means a leading dot or `FILE_ATTRIBUTE_HIDDEN` |
| `fs_read`, `fs_write`, `fs_edit` | Atomic replace with a retry on sharing violations; private files get an owner-only ACL instead of mode 0600 |
| `fs_search` | `rg` called directly, Python search as the fallback |
| `fs_download_link`, `fs_upload_link` | Reparse-point check instead of `O_NOFOLLOW`; Windows hard-link errors in the create-only fallback |

### System

| Tool | Mechanism on Windows |
|---|---|
| `system_status` | `GlobalMemoryStatusEx`, `GetDiskFreeSpaceExW`, `GetTickCount64`; `nvidia-smi` when present |
| `notify` | Toast notification |
| `panel_icon` | The tray icon (`Shell_NotifyIcon`) |
| `system_capabilities` | Reports the `windows.*` backends and their limits |

### Desktop

| Tool | Mechanism on Windows |
|---|---|
| `desktop_unlock`, `desktop_lock` | The same grant and lease; the indicator is the native frame and the tray icon (below) |
| `screen_info` | `EnumDisplayMonitors`, `GetDpiForMonitor`, Per-Monitor-V2 awareness; the virtual screen's negative origin is normalized as on Linux |
| `screen_capture` | DXGI Desktop Duplication for monitors (measured 4.9 ms first frame); the last frame is kept because duplication returns none while nothing changes. Windows.Graphics.Capture for `monitor="window"` |
| `find_text`, `wait_for_text` | The same tesseract OCR (installed by setup with winget) |
| `ui_dump`, `ui_click`, `ui_set_text` | UI Automation: `RuntimeId` as the element identity (stable across an edit, measured), Invoke / Toggle / SelectionItem for actions, `ValuePattern` for text with read-back |
| `window_list`, `window_focus` | Top-level windows; activation injects one key the target cannot see (`VK_F24`) before `SetForegroundWindow`, which Windows then allows (measured). Applications launch from Start menu shortcuts and, for packaged apps, `shell:AppsFolder\<AUMID>` |
| `keyboard`, `mouse` | `SendInput` with scan codes and `KEYEVENTF_UNICODE`, `MOUSEEVENTF_ABSOLUTE \| VIRTUALDESK`; held-key auto-release, smooth paths and the 60 ms click hold unchanged |
| `computer_batch` | Unchanged (`batch.py` knows no devices) |
| `computer_task` | Unchanged; the skill text names Windows shortcuts and apps |

## Known limits (Windows forbids these)

- **Elevated windows.** An unelevated process cannot send input to, read,
  or close a window that runs elevated (UIPI). Measured: `SendInput`
  reported success while nothing arrived, UIA found no element, `WM_CLOSE`
  was dropped. pcbridge checks the target's integrity level and refuses
  with `TARGET_ELEVATED` and a reason. Lifting this needs `uiAccess`, which
  needs a code-signing certificate and an install under Program Files: a
  separate decision, not in the first release.
- **The secure desktop.** UAC prompts, the lock screen and Ctrl+Alt+Del
  are unreachable for any application, by design. pcbridge never tries.
- **SmartScreen.** Unsigned executables warn on first run; the same
  certificate decision as above.
- **Measured only in the VM so far.** One monitor at 96 DPI. Several
  monitors and scaling (150 %, mixed) wait for a display driver with
  several heads or a physical machine; Windows.Graphics.Capture, the
  coding agents' Windows installs, a Microsoft account and Windows Hello
  are not measured yet.

## Security model

Every layer of [security.md](../security.md) has a Windows counterpart.

| Layer | Linux | Windows |
|---|---|---|
| Local path | Unix socket in a 0700 runtime dir | Named pipe `\\.\pipe\pcbridge-<SID>` with a protected DACL for the user's SID only, `FILE_FLAG_FIRST_PIPE_INSTANCE` (a squatter is refused: measured) and `PIPE_REJECT_REMOTE_CLIENTS` |
| Service | systemd user units | A per-user scheduled task at logon running the supervisor; not a Windows service, because session 0 cannot see the desktop |
| Private files | Mode 0600, dirs 0700 | Owner-only ACLs on `%APPDATA%\pcbridge` and `%LOCALAPPDATA%\pcbridge` |
| Remote path | OAuth or the static token through Tailscale Funnel to 127.0.0.1 | Unchanged (`tailscale.exe`) |
| Remote password | Created or replaced only after the login password, checked through PAM | The same, checked with `LogonUserW` (measured for a local account); Windows Hello through `UserConsentVerifier` where it is set up. A Microsoft account is checked with its account password, not the PIN |
| Gate | enabled, screen lock, grant, user-active, rate limit | The same order. Screen lock from WTS session flags with `OpenInputDesktop` as a second signal (both measured). User-active counts only input that is not pcbridge's own: a low-level hook sees `LLMHF_INJECTED` and pcbridge's `dwExtraInfo` mark (measured), which is stricter than uinput allows |
| Content gates | Password fields, `confirm_close`, repeated clicks | Password fields from UIA `IsPassword`; the rest unchanged; plus the elevated-target refusal |
| Indicator | GNOME extension frame, KDE notification, native glow | A click-through, always-on-top frame window drawn by the native helper, a tray icon with "Lock now", and a toast at unlock |
| Kill switch | `pcbridge lock`, panel buttons, `pcbridge stop` | `pcbridge lock`, the tray icon's "Lock now", `pcbridge stop`; held keys released on revoke as on Linux |
| Jobs | systemd scopes | Job Objects |
| Audit | Metadata only, never content | Unchanged |

## Order of work

W1 portability layer (`pcbridge/oscompat/`), W2 daemon and IPC, W3 jobs,
shell, agents and files, W4 live terminal, W5 remote and login check,
W6 native helper Windows platform, W7 Python desktop wiring, W8 install and
upgrade, W9 CI and documents, W10 end-to-end check in a fresh VM. Each stage
ends in a commit; the Windows release comes at the end.
