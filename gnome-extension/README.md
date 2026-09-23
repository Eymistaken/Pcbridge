# pcbridge — agent visible (GNOME Shell 46 extension)

pcbridge can give an agent keyboard, pointer and screen access. This
extension makes that state hard to miss, and gives pcbridge two narrow
window operations GNOME does not offer from outside.

- **A soft white frame at the screen edges** while desktop control is
  granted, on every monitor. It fades in and out with the grant and breathes
  slowly (every 11 s it thins by at most 12 % and returns).
- **A panel indicator** (2.0): server up or down, the grant and its minutes
  left, running jobs, remote access, and a menu with **"Lock desktop control
  now"** (runs `pcbridge lock`), "Open logs" and "Status…".
- **An optional kill-switch shortcut** (2.0), off by default.
- **The agent's pointer** (OFF by default, below).
- **Two D-Bus methods**: `ActivateWindow(target) -> bool` raises the one,
  unambiguous, already open window that matches; `FocusedWindow() -> (bool,
  wm_class, app_id, title)` names the window with keyboard focus, for when
  AT-SPI cannot (games, many Java/Electron windows). Nothing lists, moves,
  closes or resizes windows. Both reread
  `~/.local/state/pcbridge/desktop_unlock.json` on every call and return
  `false` without doing anything when the grant is closed.

The extension only **reads** state: the grant file and `status.json` (which
the pcbridge daemon writes when its state changes). It never writes either,
and the indicator adds no step to starting work.

## Install

`pcbridge setup` copies the extension to
`~/.local/share/gnome-shell/extensions/pcbridge-gorunur@eymistaken.local/`,
compiles its settings schema and enables it. The `.deb` installs it under
`/usr/share/gnome-shell/extensions/`. It becomes active at the **next
login**: GNOME 45+ caches extension code, and on Wayland a login is the only
way to reload the shell.

For development, a symlink to this directory instead of a copy:

```bash
./gnome-extension/install.sh              # link + enable + print the undo
./gnome-extension/install.sh --status     # installed? enabled?
./gnome-extension/install.sh --remove     # disable + remove the link
```

## Emergency undo

**Run this first; it takes effect immediately:**

```bash
gnome-extensions disable pcbridge-gorunur@eymistaken.local
```

> **Deleting the files alone does not stop the running extension.** The
> shell has already loaded it; only a restart of the shell (a new login)
> drops it. This happened once: only `rm` was suggested, nothing changed, and
> the machine had to be restarted.

If the shell is frozen, switch to a text console with **Ctrl+Alt+F3** and
run the `gnome-extensions disable` command there.

## Panel indicator and kill switch

The icon shows the grant open (pointer icon, minutes left), idle, or the
daemon down. Without `status.json` (a 1.x server) the menu says the status is
unknown; the kill switch still works through the `pcbridge` command.

Kill-switch shortcut, off by default. `<Super><Control>Escape` was free on
GNOME 46 / Zorin OS 18 (`<Super><Shift>Escape` belongs to mutter):

```bash
gsettings --schemadir ~/.local/share/gnome-shell/extensions/pcbridge-gorunur@eymistaken.local/schemas \
  set org.gnome.shell.extensions.pcbridge-gorunur lock-shortcut "['<Super><Control>Escape']"
```

## D-Bus interface

```text
io.github.eymistaken.Pcbridge.WindowFocus
/io/github/eymistaken/Pcbridge/WindowFocus
io.github.eymistaken.Pcbridge.WindowFocus.ActivateWindow(s) -> b
io.github.eymistaken.Pcbridge.WindowFocus.FocusedWindow() -> (b, s, s, s)
property Version: s        (2.0; a 1.x extension has none)
```

`ActivateWindow` returns `true` only after `Meta.Window.activate()` when the
shell's focus window is that window; no match, an ambiguous match, a closed
grant or an unconfirmed activation return `false`, and pcbridge falls back to
GNOME search. Measured: ~5 ms per activation against ~6.7 s through search.

`FocusedWindow` reads `global.display.focus_window` and returns `[found,
wm_class, app_id, title]` (each at most 200 characters); `found` is false
with a closed grant or nothing focused (overview, empty desktop). pcbridge
asks it only when AT-SPI cannot read the focus. Measured in a nested shell:
4.8-11 ms per call including `busctl`.

`pcbridge doctor` reads `Version` from the running shell and says whether
the new extension is loaded yet. Either extension version works with either
server version.

## The agent's pointer (off by default)

While the grant is open the real pointer is hidden and an arrow that turns
with the movement is drawn. **It ships off**, because it was removed once:
with a physical mouse, clicks did not register and the pointer froze
(2026-08-04). The cause was not found; all tests had used a synthetic mouse,
and the one unmeasured difference was the event rate (a physical mouse
reports at ~1000 Hz).

It came back with two changes: the position is applied **once per frame**
instead of on every event, and the actor lives in `Main.uiGroup` instead of
`addTopChrome`. In a nested shell (2026-09-20, the same pointer storm) the
old code drew 1992 of 1992 events, the new one 263 (58 draws/s, the frame
rate). **Not verified with a physical mouse in a real session.**

```bash
touch ~/.local/state/pcbridge/gorunur-imlec            # on
gio trash ~/.local/state/pcbridge/gorunur-imlec        # off
```

The flag file is reread whenever the grant opens, so no shell restart is
needed.

## Development

`gnome-extensions disable/enable` does not reload the code, so development
happens in a nested shell (or a headless one, `gnome-shell --headless
--virtual-monitor WxH` under its own `dbus-run-session`):

```bash
./gnome-extension/nested.sh           # stop the previous one, start a new one, follow the log
./gnome-extension/nested.sh --log
./gnome-extension/nested.sh --kill    # stop it AND the ~13 session services it leaves behind
```

A nested shell never touches your session, but it cannot measure
everything: its monitors are virtual and it is composited twice, so cost
numbers from it may exaggerate. It reads a **fake** grant file
(`PCBRIDGE_GORUNUR_STATE`): writing `{"until": ...}` to the real
`desktop_unlock.json` would really grant desktop control, since pcbridge's
gate reads the same file.

```bash
echo "{\"until\": $(( $(date +%s) + 120 ))}" > /tmp/pcbridge-gorunur-test-state.json
echo '{"until": 0}' > /tmp/pcbridge-gorunur-test-state.json
```

The logic that needs no shell runs under `gjs` (`-m`: the files are ES
modules):

```bash
for t in gnome-extension/tests/*.js; do gjs -m "$t"; done
```

### Measuring from inside the shell

What the extension claims cannot be checked from outside (the frame does not
catch clicks, the main loop is not blocked, the indicator shows the right
lines), and `Shell.Eval` is closed since GNOME 41. The extension measures
itself when asked:

```bash
PCBRIDGE_GORUNUR_SELFTEST=1 ./gnome-extension/nested.sh
./gnome-extension/nested.sh --log | grep SELFTEST
```

`PCBRIDGE_GORUNUR_SELFTEST_LOCK=1` also presses the kill switch once (point
`status.json`'s `cli` at a harmless script); `PCBRIDGE_GORUNUR_BURST=1`
sends 2000 moves through a virtual pointer; `PCBRIDGE_GORUNUR_CURSOR=1`
turns the pointer overlay on. Off, self-test costs one `getenv`.

## Files

| File | What |
|---|---|
| `extension.js` | entry point |
| `state.js` | watches the grant file |
| `status.js` | the indicator's logic: reads `status.json`, checks the daemon's pid (no shell modules; tested with gjs) |
| `indicator.js` | the panel button and menu |
| `windowcontrol.js` | the D-Bus interface |
| `frame.js` | the edge frame |
| `cursor.js`, `frameclock.js` | the agent's pointer; once-per-frame scheduling |
| `selftest.js` | measurement from inside the shell |
| `schemas/` | the `lock-shortcut` setting |
| `../install.sh`, `../nested.sh` | development install and loop |
| `../tests/*.js` | gjs tests: state, status, window control, frame clock |

## Cost

The static frame is below measurement noise (CPU 0.45-0.55 % either way,
+0.08 MB RSS). The breathing animation cost 19 % CPU in a nested shell (23.7 %
when animating opacity instead): the cost is re-blending large translucent
strips at 60 fps. The nested number may exaggerate; it was not measured in a
real session. Removing the `_startBreathing` calls in `frame.js` makes the
frame free again.

More measured facts about the shell: [docs/dev/measured-facts.md](../docs/dev/measured-facts.md#gnome-shell-extension).
