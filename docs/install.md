# Installing pcbridge

Supported: GNOME Shell 46 on Wayland (Ubuntu 24.04, Zorin OS 18, Debian 13;
Ubuntu 26.04 builds and installs in CI). Python 3.12 or newer. Other GNOME
versions are expected to work and are untested; `system_capabilities` says
so when it sees one. X11 and other desktops are not supported for the
desktop tools (they refuse and say why); the shell, file, tmux and agent
tools work anywhere.

## 1. Install

### From the release (user install, no root)

```bash
packaging/install-user.sh pcbridge-<version>-py3-none-linux_x86_64.whl --yes
```

This creates `~/.local/share/pcbridge/venv`, installs the pinned
dependencies and the native helper, and runs `pcbridge setup --yes`.

### From the .deb

```bash
sudo apt install ./pcbridge_<version>_<distro>_amd64.deb
pcbridge setup
```

There is one package per distribution release (the bundled venv is bound to
that release's Python). Run `pcbridge setup` as **your own user**, not root.

### From git

```bash
git clone https://github.com/Eymistaken/Pcbridge.git && cd Pcbridge
./install.sh
```

`install.sh` installs pcbridge into the checkout's own `.venv` (editable,
with the pinned dependencies), builds the native helper when Rust is
installed, and runs `pcbridge setup`. `pcbridge update` then pulls
(fast-forward only) and restarts the daemon when idle.

## 2. What `pcbridge setup` does

1. Checks system packages and prints the one `apt` line for what is missing.
2. Writes `~/.config/pcbridge/config.toml` (0600) with a new password and
   static token, or migrates a 1.x `config.toml` (the old file stays where it
   was; `--from-config PATH` picks one explicitly).
3. Links `~/.local/bin/pcbridge`, `pcb-shot` and `pcb-do`.
4. Writes and enables `pcbridge.socket` and `pcbridge.service` for your user,
   then starts the daemon (only when no job is running and no grant is open).
5. Copies the GNOME extension; it becomes active at your next login.
6. Registers Claude Code (user scope), Codex and Claude Desktop, keeping
   other entries and Codex's per-tool approval settings.
7. Writes the shell aliases (`bridgekilit`, `bridgeac`, `bridgekapat`,
   `bridgedurum`) in a marked block of `~/.bashrc`.
8. Checks that a fresh client gets all tools.

Everything it replaces is backed up under
`~/.local/state/pcbridge/backup-<time>/` with a `ROLLBACK.md` of exact
commands. `pcbridge uninstall` undoes it.

## 3. Desktop control (optional, off by default)

Virtual input needs `/dev/uinput` for your user. The package installs a udev
rule; for a user install:

```bash
pcbridge doctor           # the /dev/uinput line prints the exact sudo commands
```

Then set `[desktop] enabled = true` in the config and restart pcbridge
(`pcbridge update`). OCR (`find_text`, `wait_for_text`) needs
`sudo apt install tesseract-ocr`.

Log out and back in once so the new extension loads; the panel icon then
shows pcbridge's state and the kill switch.

## 4. Remote access (optional)

Install Tailscale, enable HTTPS certificates for the tailnet, set
`public_url` in the config, then:

```bash
pcbridge remote start      # opens the Funnel tunnel to 127.0.0.1:8765
```

Give remote clients `<public_url>/mcp`; they authorize with the password.
The tunnel does not start at login; `pcbridge remote stop` closes it.

## 5. Check

```bash
pcbridge doctor
pcbridge status
```

Restart Claude Desktop and any running Claude Code / Codex session once:
processes started before the install keep their old server until then.

## Updating

- User install: run `packaging/install-user.sh` with the new wheel; the
  daemon restarts itself when idle.
- Package: `sudo apt install ./pcbridge_<new>.deb`; same.
- Git checkout: `pcbridge update` pulls and restarts the daemon when idle.
