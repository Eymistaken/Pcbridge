# Security: an honest assessment

pcbridge is **unrestricted by design**: whoever reaches it can run commands,
read and write files, start coding agents and, with desktop control on, use
the logged-in desktop. What protects the machine depends on the path a
client takes. This page says what each layer does and what it does not.

## The two paths

| | Local clients (stdio relay) | Remote clients (HTTP) |
|---|---|---|
| Network | none: a Unix socket in `$XDG_RUNTIME_DIR/pcbridge/` | Tailscale Funnel to `127.0.0.1` |
| Authentication | none | OAuth 2.1 with a password consent page, or the static token |
| Who can connect | processes of your own user (socket mode 0600, runtime dir 0700) | anyone with the URL and the password/token |
| Desktop grant | `[desktop] enabled` + `desktop_unlock` | the same |
| Audit log | `audit.log` | the same |
| Screenshots | the picture itself in the tool result, and a file path | a short-lived `/shot/<token>.png` link |

**The local path is a deliberate step back from HTTP.** Anything that can
run code as your user can talk to the socket, with no password. The reason
this is accepted: something that already runs code as you does not need
pcbridge. It can call `claude -p` or read `~/.ssh` directly. The socket
opens no new door; it makes the room behind an existing one more convenient.
The difference is real and was chosen knowingly.

## Remote access

- The daemon listens on `127.0.0.1` only. What exposes the machine is the
  tunnel, and the tunnel does **not** start at login: `pcbridge remote start`
  opens it, `pcbridge remote stop` closes it, `pcbridge status` shows it.
- **Your address is not secret.** `*.ts.net` names appear in public
  certificate-transparency logs. Protection comes from the password's length,
  not from nobody knowing the host.
- The password and the static token are generated at setup; do not reuse a
  password from elsewhere. After 8 wrong attempts an IP is locked out for 15
  minutes (`[auth]`).
- To revoke every issued token: move `state_dir/oauth.db` to the trash and
  restart pcbridge; remote clients must authorize again.
- The screenshot link works **without OAuth**: its 128-bit token is the
  authorization, so a phone browser can open it. Do not forward it. Links
  expire after `shot_ttl_seconds` (5 minutes); the PNGs are swept after
  `shot_keep_hours` (24 h), before every capture and at startup, on both paths.

## Desktop control: a separate class of risk

With `[desktop] enabled = true` pcbridge can **use** the computer: a virtual
keyboard and pointer type and click. That reaches more than running
commands does:

- **Everything you are logged into**: a banking tab, the password manager,
  mail. Virtual input is indistinguishable from your own keyboard.
- **uinput asks no permission.** It works at the kernel level, below
  Wayland's permission prompts. Protection is entirely pcbridge's own gate.
- **Screenshots and `ui_dump` read the screen**: open messages, mail, any
  password that is visible. Both need the grant too.
- **Capture is silent, and visible.** It goes through GNOME screen sharing
  (no flash, no sound), and for exactly that reason GNOME's sharing indicator
  stays on while the grant is open. The indicator disappears when the grant
  closes; sharing lives in a helper process, so it ends if pcbridge dies.
- **`[desktop] enabled` is `false` by default.** Decide what happens if the
  phone with the static token is lost before turning it on.

### The gate

Every desktop call passes five layers: `[desktop] enabled` -> screen lock
(nothing is sent behind a locked screen) -> the time-limited grant ->
the "user is at the machine" guard (`Mutter.IdleMonitor`) -> a rate limit,
plus the audit log. A write sequence then takes a cross-process execution
lock and rechecks the grant **before every action**: a `desktop_lock` from
anywhere stops the next action. The grant slides: it closes 90 s after the
last desktop action, and never later than its hard ceiling.

Content gates refuse typing into password fields, closing windows without
`confirm_close`, and a third click in a row on the same element
([dev/desktop-rules.md](dev/desktop-rules.md)).

The agent opens the grant itself (`desktop_unlock`); no person is asked. The
person's controls are the config switch, the lock screen, the kill switch and
the audit log.

### What `enabled = false` does NOT turn off

Only the desktop tools. `shell_run`, `agent_run`, `fs_*` and `tmux_*` work
regardless: a remote client could still start an agent that takes a
screenshot (measured 2026-08-02), or read `config.toml` and with it the
password. Blocking them would not be real while `shell_run` runs arbitrary
commands, so they leave a trace instead: every call goes to `audit.log`
with what was done (command, path, text length), never the content.

## `computer_task`: the largest step up

Every other desktop tool executes what the client decided. `computer_task`
hands a goal to a local agent that looks at the screen and **decides by
itself** where to click and what to type, inside your session. It can click
the wrong window or write to the wrong person. Two accidents during
development (a click that sent 23 desktop items to the trash; a click from
a stale screenshot that landed in another app) now have guards in the code,
but guards limit damage, they do not prevent mistakes. The same gate applies
to every action the agent takes, and every click is logged with the task id.

A client that can see images (Claude Code can) does the same work with
`screen_capture` + `computer_batch`, with no second model in between.
`computer_task` remains for long GUI work that should not block the client.

## Stopping things

| Want | Do |
|---|---|
| Stop the hands now, keep jobs running | `pcbridge lock` / `bridgekilit` / the panel menu / `desktop_lock` |
| Stop the daemon and its jobs | `pcbridge stop` (`--keep-jobs` leaves jobs alone) |
| Stop the daemon only | `systemctl --user stop pcbridge.service` (jobs live in their own scopes and survive; the socket starts it again on the next client) |
| Turn desktop control off | `[desktop] enabled = false`, restart pcbridge |
| Close remote access | `pcbridge remote stop` |

A key held by an agent is released by `desktop_lock`, by a sequence that
stops half way, and at the latest after `hold_max_seconds` (120 s).

## Secrets

`config.toml` holds the password and the static token; it is mode 0600, is
never logged or printed, and is not in the repository. `pcbridge report`
redacts it (and your home path) from diagnostic bundles.
