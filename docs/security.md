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
| Large files | one-time `/dl/<token>` and `/ul/<token>` links through the tunnel, for both | the same |

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
- Setup creates neither a password nor a static token. Without a password
  remote access is off: the consent page refuses every app and `pcbridge
  remote start` does not open the tunnel. The password is created when
  remote access is first wanted, generated (six words from the EFF Large
  Wordlist, about 77 bits) or typed, and shown once; a new one takes effect
  without a restart. Do not reuse a password from elsewhere.
- **Creating or replacing the remote password asks for your login
  password first**, in every place that does it: the Remote tab, `pcbridge
  remote start`, setup and `pcbridge set auth.password` (`--generate`
  included; without a terminal the login password is the first line of
  stdin). It is checked through PAM's `login` service, not sudo, so a user
  without sudo rights can still manage their own remote access, and
  `NOPASSWD` or sudo's cached ticket cannot skip it. A wrong answer goes
  through the same stack as the login screen: a delay of a few seconds, and
  on systems with `pam_faillock` (Arch) it counts towards its lockout. This
  guards pcbridge's own paths. It is not a boundary against someone who
  already has a shell as you: `config.toml` is your file, and whoever can
  write it can store a hash of their own choosing. After 8 wrong attempts an IP is locked out for 15 minutes
  (`[auth]`).
- `pcbridge remote clients` lists the remote apps that hold tokens;
  `pcbridge remote revoke NAME` cuts one off and `pcbridge remote revoke
  --all` cuts off every one (the terminal UI's **Remote** tab does the same).
  The registration and every token issued to it are deleted: the next call
  fails at once, the refresh token stops working, and the app has to
  connect again with the password. No restart is needed.
- **A new password does not end existing sessions**: refresh tokens do not
  depend on it (measured). `pcbridge set auth.password` and the terminal UI
  offer to revoke every remote client after a password change; after a lost
  phone or a leaked password, do both.
- The static token is not an app and is not listed; anyone who has it can
  call pcbridge. Replace it with `pcbridge set auth.static_token --generate`,
  which shows the new one once.
- `[tools] remote_profile` gives remote clients a smaller tool set than
  local ones, for example `readonly` (job status and output, file reads,
  tmux capture, system status) or `core` (no desktop tools). It covers
  every HTTP request, the static token included; local clients on the
  socket keep `profile`. A tool outside it is hidden from the list and a
  call to it by name is refused. The default, `same`, follows `profile`.
- The screenshot link works **without OAuth**: its 128-bit token is the
  authorization, so a phone browser can open it. Do not forward it. Links
  expire after `shot_ttl_seconds` (5 minutes); the PNGs are swept after
  `shot_keep_hours` (24 h), before every capture and at startup, on both paths.

## File links

`fs_download_link` and `fs_upload_link` return a link that the user opens
on the phone, so a large file goes between the phone's browser and the
daemon without passing through the model (base64 through the model stops
being reliable after a few tens of kilobytes; see measured facts). They
are a new endpoint on the internet, so:

- **The token is the authorization.** Like the screenshot link, the link
  works without OAuth so a phone browser can open it. FastMCP's bearer
  check covers only `/mcp`; neither OAuth shim in `app.py` sees `/dl/` or
  `/ul/`, and `auth.py` is not involved. The token is 256 random bits, kept
  only in memory under its SHA-256, so a daemon restart ends every link.
  OAuth tokens and the static token are not accepted on these paths.
- **One path, one direction.** The link is bound when it is made to one
  resolved file path and to download or upload. Nothing in the request can
  change the path: the upload's own file name is ignored.
- **One complete transfer.** Only a transfer that completes spends the
  link: the last byte sent for a download, the file in place for an upload.
  One that breaks off on the network leaves it usable until it expires; one
  refused for a limit spends it. Only one transfer runs per link at a time.
  The page shown before the transfer does not spend it: single-use
  screenshot links were burned by phone refreshes and link previews, and a
  preview fetches only the URL it was given.
- **A few minutes.** `[files] link_ttl_seconds` (300) is the window to
  start the transfer; a started 50 MB transfer on a slow connection may
  finish after it. At most 16 links are open at once.
- **Nothing for a dead link.** An unknown, expired, used or busy link gets
  the same `404 not found` with the same headers, on every path and method.
- **Same profiles.** `fs_download_link` is read-only, so `readonly` and
  `core` offer it; `fs_upload_link` is not, so `readonly` and `desktop`
  never offer it. A link is always used over HTTP, so it can be made only
  when `remote_profile` offers the tool too: with `profile = "full"` and
  `remote_profile = "readonly"`, a local session cannot make an upload link
  either. A readonly remote client can take any file the user can read off
  the machine faster than before; it already could through `fs_read`.
- **Size limits.** Uploads up to `upload_max_mb` (512), refused before
  reading when `Content-Length` is larger and cut off mid-stream when a
  chunked body goes past it; an upload must leave 256 MB free. Downloads up
  to `download_max_mb` (2048).
- **Uploads are atomic and do not clobber.** The body streams into a temp
  file next to the target (mode 0600), hashed as it arrives, synced, and is
  put in place in one step with the old file's mode. Without `overwrite` an
  existing file is refused when the link is made, and again at the end with
  a hard link, which fails if a file appeared in the meantime. Any failure
  removes the temp file; `expected_sha256` refuses a different file.
- **Downloads cannot run script.** The file is always
  `application/octet-stream`, `attachment` and `Content-Security-Policy:
  sandbox`, so an HTML or SVG file never runs on pcbridge's origin, which
  also serves the consent page. A file whose size, mtime or inode changed
  since the link was made is not sent. The pages allow only their own
  inline style and script, by hash; every response says `no-store`,
  `no-referrer`, `nosniff` and `noindex`.
- **Audit, never content.** `fs_link_created`, `fs_link_used` (bytes,
  SHA-256, seconds, remote address), `fs_link_failed` (the reason) and
  `fs_link_refused` (a used or expired link tried again, once per link) go
  to `audit.log` with an 8-digit link id. The token, the URL and the file's
  content are never logged, and uvicorn's access log in the journal shows
  `/dl/<link>` instead of the token.
- **Stopping them.** `pcbridge remote stop` makes every link unreachable
  at once; restarting the daemon drops them. Revoking a remote client does
  not end links it already made; they expire within minutes.
- **Accepted:** the link is in the model's conversation, and so stored by
  the provider, until it is used or expires. Do not forward it.

## The browser

The browser tools drive a Chrome, Brave or Chromium that pcbridge starts on
its own profile (`<state_dir>/browser/profile`). What that means:

- **Your everyday browser is never touched.** pcbridge does not attach to
  your profile: Chrome 155 refuses the debugging flags on it, and the
  `chrome://inspect` switch that would allow it asks in a dialog for every
  connection (measured). The pcbridge browser is found by the process
  pcbridge started, never by name.
- **No network port.** The browser speaks CDP over a pipe that a small
  keeper process holds; the daemon reaches the keeper through a Unix socket
  of mode 0600 in your runtime directory. No other local user, and no web
  page, can reach it.
- **Whatever you sign in to there, a client can use.** That is the point (a
  router panel, a signed-in site), and it is why it is a separate profile:
  sign in only to what an agent should reach. A remote client with
  `remote_profile = "readonly"` can read pages and take browser
  screenshots, but not open pages, click or type.
- **No desktop grant.** The tools act through the browser, not the keyboard
  or mouse, so they cannot land in another window; like the file and shell
  tools they work with `[desktop] enabled = false` and after
  `desktop_lock`.
- **Passwords.** A page read never shows a password field's value; typing
  into one is refused unless `[browser] allow_password_fields = true`.
  Only http and https pages can be opened (no `file:` or `chrome:`).
- **The audit log** records the action, the tab, the ref, the host and how
  many characters were typed, never the text, the page or the rest of the
  address (a query string can hold a token).
- `pcbridge browser stop` closes it cleanly (the sign-ins stay);
  `pcbridge stop` closes it with everything else. To sign out of
  everything, close it and move the profile directory to the trash.

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
- **On KDE Plasma capture shows no sharing indicator.** KWin's screenshot
  interface (`org.kde.KWin.ScreenShot2`) is silent and leaves nothing on
  screen, so pcbridge shows the grant itself: a critical notification that
  stays for as long as the grant is open, with a "Lock now" button that runs
  the kill switch. Every end of the grant closes it. There is no frame
  around the screen as the GNOME extension draws.
- **KWin gives screenshots only to programs a `.desktop` file names.**
  pcbridge authorizes exactly one: its native helper, by full path
  (`pcbridge-native.desktop`). The system Python is never authorized; that
  would let every Python script take screenshots. Removing the file revokes
  the access within seconds.
- **On Plasma the grant turns on Qt accessibility**
  (`org.a11y.Status.IsEnabled`), so `ui_dump` can read Qt applications, and
  restores the previous value when the grant ends. While it is on, any
  program of your user can read those applications' accessibility trees,
  as on GNOME, where GTK applications are always readable.
- **`[desktop] enabled` is `false` by default.** Decide what happens if the
  phone with the static token is lost before turning it on.

### The gate

Every desktop call passes five layers: `[desktop] enabled` -> screen lock
(nothing is sent behind a locked screen) -> the time-limited grant ->
the "user is at the machine" guard (`Mutter.IdleMonitor`; on Plasma and
Hyprland the native helper's `ext_idle_notifier_v1` watcher) -> a rate limit,
plus the audit log. A write sequence then takes a cross-process execution
lock and rechecks the grant **before every action**: a `desktop_lock` from
anywhere stops the next action. The grant slides: it closes 90 s after the
last desktop action, and never later than its hard ceiling.

A genuine MCP cancellation notification stops a desktop worker at the next
shared checkpoint. Batch waits, rate waits, and pointer settling check every
50 ms without extending the lease. Escaping failures and canceled final
actions release held input before the execution lock is relinquished; a
canceled caller waiting for that lock cannot release the current owner's
input. Completed intentional holds remain available to the next call.
Individual native operations finish within their existing bounds; cancellation
is not a rollback of input already sent. Canceling only a client-side Python
task does not notify the server in the pinned SDK: clients must send the MCP
cancellation notification, or use desktop_lock to revoke control.

Content gates refuse typing into password fields, closing windows without
`confirm_close`, and a third click in a row on the same element
([dev/desktop-rules.md](dev/desktop-rules.md)).

You can hide the GNOME panel icon in **Settings > Panel icon**, or an agent
can hide it when you ask it to (`panel_icon`),
but only while desktop control is closed: the extension shows the icon for
as long as a grant is open, whatever the setting says, so hiding it cannot
hide an agent's access. The frame around the screens is not affected.

The agent opens the grant itself (`desktop_unlock`); no person is asked. The
person's controls are the config switch, the lock screen, the kill switch and
the audit log.

### Hyprland grant implementation

Hyprland uses the same shared gate, execution lock, lease identity, rate
counter, and content policy. It additionally requires a native frame with
fresh presentation proof for the exact grant/epoch and selected session.
The frame takes no input or reserved space. CLI/TUI unlock routes the existing
MCP action to a resident daemon after matching its state directory and
compositor identity; a short-lived caller never owns the frame. No tray or
panel integration is required.

Protected admission and each action require presentation on the current
output identities and geometry, queried without the monitor cache. A recent
record for old outputs cannot keep control open while the frame is rebuilding.

Missing/stale visibility pauses protected operations. Helper death retires
only its own lease; explicit lock revokes before cleanup; old owner shutdown
cannot revoke a replacement. Native and Python resource guards release open
resources on failed visibility, unknown idle, expiry, and replacement without
waiting for another action. Their timers never slide a grant. Hyprland force
can bypass the known user's conflict threshold, but cannot bypass unknown idle,
lock, missing visibility, or lease checks. The scoped VM acceptance and
GNOME/Plasma regressions are recorded in
[measured facts](dev/measured-facts.md#hyprland-release-scope-and-limits).
Physical Hyprland connectors and hotplug remain unmeasured.

Native Hyprland screenshots own bounded SHM buffers and a fresh Wayland
image-copy session per request. Watchdog generation changes cancel in-flight
waits. Lease, frame, lock, and idle validation occurs before protected capture
and again after PNG encoding. External `grim` processes and interpreter
permissions do not belong to the production capture boundary.
The Python shot publisher retains the admitted token through rendering and
checks it before each artifact and at completion. Revoke, output changes, or
focused-window identity/geometry changes withdraw the newly published PNGs
and metadata instead of returning an obsolete shot.

Window activation receives a checkpoint belonging to the current shared
write sequence. It rechecks that sequence's captured token after read-only
target/session lookups and before dispatch. The checkpoint expires with the
execution slot, including in copied task contexts. Human names never enter
the dispatcher: only bounded, validated compositor identities do. Ambiguous
matches and reused identities fail safely; a dispatcher acknowledgment must
be followed by matching fresh compositor focus. Runtime config provider
selection controls Lua versus hyprlang syntax; no guessed desktop shortcut
or retry with another mutating dispatcher is used.

### What `enabled = false` does NOT turn off

Only the desktop tools. `shell_run`, `agent_run`, `fs_*`, `tmux_*` and `browser_*` work
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
| Stop the hands now, keep jobs running | `pcbridge lock` / `bridgekilit` / the panel menu (GNOME) / "Lock now" on the grant notification (Plasma) / `desktop_lock` |
| Stop the daemon and its jobs | `pcbridge stop` (`--keep-jobs` leaves jobs and the pcbridge browser alone) |
| Close the pcbridge browser | `pcbridge browser stop` (its sign-ins stay) |
| Stop the daemon only | `systemctl --user stop pcbridge.service` (jobs live in their own scopes and survive; the socket starts it again on the next client) |
| Turn desktop control off | `[desktop] enabled = false`, restart pcbridge |
| Close remote access | `pcbridge remote stop` |
| Cut off one remote app, or all | `pcbridge remote revoke NAME`, `pcbridge remote revoke --all` |

A key held by an agent is released by `desktop_lock`, by a sequence that
stops half way, and at the latest after `hold_max_seconds` (120 s).

## Secrets

pcbridge stores only what it needs to check a secret:

- `[auth] password` as an scrypt hash (about 50-70 ms per check, 32 MiB),
  `[auth] static_token` as SHA-256. A plaintext value in the file is
  replaced by its hash when the daemon starts; `pcbridge doctor` reports a
  file that still holds one and `--fix` hashes it. PCBRIDGE_PASSWORD and
  PCBRIDGE_STATIC_TOKEN in the environment are used as they are and never
  written.
- Every access token, refresh token and authorization code in `oauth.db` is
  stored under its SHA-256 digest, so a copied database hands out nothing.
  An older database is converted in place when the daemon starts and then
  vacuumed, so the old values do not survive in free pages or the WAL.
- Config backups (`config.toml.backup-*`) get the same hashes. Copies made
  by an older pcbridge, and `oauth.db` copies made by hand, still hold the
  plaintext: `pcbridge doctor` names them under "old copies"; read them and
  move them to the trash.
- What is not hashed: the client secrets that remote apps registered with
  (useless without a refresh token or the password), and the authorization
  code in the audit log's `code_issued` line (single use, five minutes, and
  useless without the client's PKCE verifier).

`config.toml` stays mode 0600, is never logged or printed, and is not in the
repository. `pcbridge report` still redacts secrets (and your home path)
from diagnostic bundles.
