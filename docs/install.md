# Installing pcbridge

Supported: GNOME Shell 46 or 50, KDE Plasma 6, or Hyprland 0.56.2 on Wayland.
Hyprland validation is from an isolated Arch VM; physical hardware remains
untested. Distributions:
the Debian family (Ubuntu 24.04, Zorin OS 18, Debian 13; Ubuntu 26.04 builds
and installs in CI) and Arch Linux. Python 3.12 or newer (the install
script offers one when the system's is older). GNOME 47-49 are
expected to work and are untested; `system_capabilities` says so when it
sees one. X11, Plasma 5 and other desktops are not supported for the
desktop tools (they refuse and say why); the shell, file, tmux and agent
tools work anywhere, and the install script sets them up there too (it
was measured on Debian 12 with Cinnamon on X11, which stands in for
Pardus 23).

## 1. Install

### The install script (recommended, no root)

```bash
curl -fsSL https://github.com/Eymistaken/Pcbridge/releases/latest/download/install.sh | sh
```

This runs code from the internet, by design. To read it first, download
it, check it against the release's checksums, then run it:

```bash
base=https://github.com/Eymistaken/Pcbridge/releases/latest/download
curl -fsSLO "$base/install.sh" && curl -fsSLO "$base/sha256sums.txt"
sha256sum -c --ignore-missing sha256sums.txt
less install.sh
sh install.sh
```

The checksum only proves the download is the file the release published;
reading the script is what tells you what it does. The script is a release
asset since 2.6.0; for an older release, run `./install.sh` from a git
checkout (below) or use `--wheel` with a downloaded wheel.

What it does, in order:

1. Refuses to run as root: everything is per user, and `pcbridge setup`
   asks for sudo itself when a package is needed.
2. Finds Python 3.12 or newer (`python3`, `python3.14` ... `python3.12`, or
   the one it installed before). With an older Python it offers:
   1. *(the default, and what `--yes` picks)* a Python for pcbridge only,
      downloaded with [uv](https://github.com/astral-sh/uv) (a fixed
      release, checked against its sha256) into
      `~/.local/share/pcbridge/python`; no root, and the system's
      `python3` is not touched;
   2. the distribution's own package, printed before it runs: on Ubuntu
      22.04 and its derivatives the deadsnakes PPA with `python3.12
      python3.12-venv`, on Fedora `python3.13`. Debian 12 and Pardus 23
      have no official package: use 1, or upgrade the distribution;
   3. quit.

   It never repoints the system `python3` (apt and system tools depend on
   it) and never offers the PPA outside Ubuntu. On KDE Plasma the KWin
   window scripts keep running under the system `python3` in their own
   process.
3. Downloads the release wheel and `sha256sums.txt`, checks the wheel's
   sha256 (and `constraints.txt`, the tested dependency versions), and
   installs it into `~/.local/share/pcbridge/venv`. The desktop extra
   (`Pillow`, which crops and scales the frames) is installed too; it comes
   as a wheel, so nothing is compiled.
4. Hands over to `pcbridge setup` (next section), which detects the machine
   and shows the plan.

Options: `--yes` (no questions; takes every default), `--version vX.Y.Z`
(a release other than the latest), `--wheel FILE` (a wheel on disk, for
example one you built; checked against a `sha256sums.txt` next to it when
there is one), `--python PATH`, and `--uninstall [--purge]` (runs
`pcbridge uninstall`, then moves the venv and the downloaded Python to the
trash; `--purge` also the config and state). Any other option goes to
`pcbridge setup`, for example `--optional none` or `--no-packages`. Piped
from curl, give options after `sh -s --`:

```bash
curl -fsSL https://github.com/Eymistaken/Pcbridge/releases/latest/download/install.sh | sh -s -- --yes
```

Run it again to update or repair: it installs the latest release over the
old one, and setup detects the machine again.

`packaging/install-user.sh WHEEL` still installs a wheel the old way (no
Python offer, no checksum, no detection before setup).

### From the .deb

```bash
sudo apt install ./pcbridge_<version>_<distro>_amd64.deb
pcbridge setup
```

There is one package per distribution release (the bundled venv is bound to
that release's Python). Run `pcbridge setup` as **your own user**, not root.

### On Arch Linux

```bash
sudo pacman -S --needed base-devel git rust clang
git clone https://github.com/Eymistaken/Pcbridge.git && cd Pcbridge/packaging/arch
makepkg -si
pcbridge setup
```

`makepkg` builds the package from the checkout, native helper included, with
the same layout as the `.deb`. Its venv is bound to the Python it was built
with, so the package pins that minor version: when Arch moves to a new
Python, pacman reports that the upgrade breaks pcbridge's dependency.
Rebuild the package (`git pull && makepkg -si`) and upgrade both together.
CI builds the same package and attaches it to each release.

### From git

```bash
git clone https://github.com/Eymistaken/Pcbridge.git && cd Pcbridge
./install.sh
```

From a checkout, `install.sh` installs pcbridge into the checkout's own
`.venv` (editable, with the pinned dependencies), builds the native helper
when Rust is installed, and runs `pcbridge setup`. Without Rust, or when
that build fails, setup offers the release's prebuilt helper instead (see
the next section). The root check and the Python offer are the same as
above. `pcbridge update` then pulls (fast-forward only) and restarts the
daemon when idle.

## 2. What `pcbridge setup` does

First it detects the machine and shows the plan, once:

```
Found: Debian GNU/Linux 12 · Cinnamon 5.6.8 · X11 · x86_64
✓ Terminal, files, tmux, coding agents
✗ Desktop control: Cinnamon on X11 is not supported yet
System packages: tmux libnotify-bin  (needs sudo)
Tool profile: "core": desktop tools are left out, they would only refuse here
Runs: sudo apt-get install -y tmux libnotify-bin
Enter to go ahead, n to stop:
```

On a supported desktop (Arch Linux, KDE Plasma, Turkish locale) the same
part reads:

```
Found: Arch Linux · KDE Plasma 6.7.5 · Wayland · x86_64
✓ Terminal, files, tmux, coding agents
✓ Desktop control (off until you turn it on: pcbridge set desktop.enabled true)
System packages: tmux wl-clipboard  (needs sudo)
Optional components:
  [x] 1. OCR for find_text / wait_for_text (eng + tur): tesseract-data-tur
Tool profile: "full"
Runs: sudo pacman -S --needed --noconfirm tmux wl-clipboard tesseract-data-tur
```

- **Detection**: the CPU architecture, the distribution and its family
  (`/etc/os-release` `ID`/`ID_LIKE`, so Pardus counts as Debian), the
  package manager, the session type and the desktop with its version. Over
  SSH, where the shell has none of the session's variables, it reads the
  user's graphical session through `loginctl` and the user manager's
  environment.
- **System packages** only for what will work here: tmux and `script`
  everywhere, `notify-send` where there is a desktop, and, only where
  desktop control is supported, the clipboard programs, the AT-SPI bus
  (`at-spi2-core`, which the native helper reads the accessibility tree
  from) and, on KDE Plasma, the system python's GObject bindings for the
  KWin window scripts. The package names come from one table
  (`pcbridge/distro.py`) for the Debian, Arch and Fedora families
  (Fedora is untested).
- **Optional components**, a checklist shown only where desktop control is
  supported: OCR for `find_text` / `wait_for_text`, tesseract with English
  plus the locale's language (`LANG=tr_TR.UTF-8` ticks Turkish:
  `tesseract-ocr-tur` on Debian, `tesseract-data-tur` on Arch,
  `tesseract-langpack-tur` on Fedora). Type its number to untick it.
  `[desktop] ocr_languages` then becomes `eng+tur` if it was still `eng`.
- **The tool profile**: on an unsupported desktop `[tools] profile` becomes
  `core`, so clients do not carry desktop tools that would only refuse.
  Setup changes the profile only while it is the default or the value setup
  itself wrote last time (remembered in `~/.local/state/pcbridge/setup.json`);
  a profile you chose stays. When the desktop becomes supported, setup
  returns its own `core` to `full`.
- **The native helper** is skipped when the system's glibc is older than
  the packaged helper needs, and the plan says so: desktop control needs
  the helper, so it is not available there until a helper built on that
  machine (`scripts/build-native.sh`) is named in `[native] binary_path`.
  The release helper needs glibc 2.35, so it runs on Ubuntu 22.04 and
  Debian 12 (the bases of Linux Mint 21 and Pardus 23) and newer; a helper
  built on another machine needs that machine's glibc.
- **A missing native helper is downloaded** where desktop control is
  supported and the installation has none (a git checkout without Rust, or
  a venv that lost it): the plan says "Native helper: downloaded from the
  pcbridge release". Setup takes `pcbridge-native-x86_64-unknown-linux-gnu`
  from this version's release, or the latest release when the checkout is
  newer, checks it against that release's `sha256sums.txt`, runs its
  `--build-info` and a handshake, and only then puts it into
  `pcbridge/_native/`. Nothing is installed when a check fails, and
  `pcbridge doctor --fix` offers it again. A checkout with Rust can build
  its own with `scripts/build-native.sh` instead. Only x86_64 is built, so
  another architecture has nothing to download (see
  [Architectures](#architectures)).

One confirmation covers the plan: Enter goes ahead, `n` stops with nothing
changed. `--yes` skips it; without a terminal and without `--yes`, nothing
is installed and the command is repeated at the end. Every command that
needs sudo is printed before it runs. `--optional ocr|none` picks the
components, `--no-packages` installs no system packages.

Then:

1. Installs the system packages of the plan; on Debian a failed install
   refreshes the package lists once and tries again. Where desktop control
   is supported and `Pillow` is missing, it pip-installs `Pillow`.
2. Writes `~/.config/pcbridge/config.toml` (0600) with no password and no
   static token (local clients need neither; remote access stays off until
   you create a password), or migrates a 1.x `config.toml` (the old file stays where it
   was; `--from-config PATH` picks one explicitly).
3. Links `~/.local/bin/pcbridge`, `pcb-shot` and `pcb-do`, and, when Claude
   Code's `~/.claude` exists, `~/.claude/skills/computer-use` to the
   installed computer-use skill. A link there that already leads to a skill
   stays as it is; a broken one is replaced.
4. Writes and enables `pcbridge.socket` and `pcbridge.service` for your user,
   then starts the daemon (only when no job is running and no grant is open).
5. On GNOME, copies the extension; it becomes active at your next login.
   On KDE Plasma, writes two entries to `~/.local/share/applications/`:
   `pcbridge-native.desktop`, which lets KWin give screenshots to the native
   helper (and only to it), and `pcbridge-lock.desktop`, the kill switch, to
   which System Settings > Shortcuts can bind a key. A package installs the
   first one under `/usr/share/applications/` already.
6. Registers Claude Code (user scope), Codex and Claude Desktop, keeping
   other entries and Codex's per-tool approval settings.
7. Writes the shell aliases (`bridgekilit`, `bridgeac`, `bridgekapat`,
   `bridgedurum`) in a marked block of `~/.bashrc`.
8. Checks that a fresh client gets every tool of the profile.

It ends with a summary: what works, what does not and why, and a reminder
that re-running it after a system or desktop update detects everything
again. Re-running is update and repair; `pcbridge doctor --fix` offers the
missing system packages and optional components again.

If `~/.local/bin` is not on your `PATH` (Arch's default profile leaves it
out), setup says so: the MCP clients use full paths and work anyway, but
typing `pcbridge` needs the line it prints in `~/.bashrc`.

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
(`pcbridge update`). OCR (`find_text`, `wait_for_text`) needs tesseract;
`pcbridge setup` offers it in its checklist, and `pcbridge doctor --fix`
offers it again when it is missing.

On GNOME, log out and back in once so the new extension loads; the panel
icon then shows pcbridge's state and the kill switch. On KDE Plasma nothing
waits for a login. Screen capture there needs the native helper (every
package and release wheel has it; a git install builds it when Rust is
installed and downloads the release's otherwise). While a grant is open, pcbridge shows a notification with a
"Lock now" button and turns on Qt accessibility, so `ui_dump` sees Qt
applications; the end of the grant closes the one and restores the other.

On Hyprland, the native helper, a session-bound idle watcher, authoritative
screen-lock state, and a freshly presented white frame on every active output
are required for desktop actions. `pcbridge doctor` checks the read-only
prerequisites. No GNOME extension, KWin permission entry, or tray is required.
Unknown lock or idle state and missing frame proof refuse actions, including
when `force=true`.

## 4. Remote access (optional)

Install Tailscale, enable HTTPS certificates for the tailnet, set
`public_url` in the config, then:

```bash
pcbridge remote start       # creates the password the first time, then opens the tunnel
```

Remote clients type a password on the approval page; local clients never use
it, so there is none until you turn remote access on. The first `pcbridge
remote start` asks for it: press Enter for a generated passphrase of six
words (easy to type on a phone), or choose 2 and type your own, at least 12
characters. It asks for your own login password first, since whoever sets
this password gets remote access. The terminal UI's **Remote** tab ("Turn
on remote access") and the last step of `pcbridge setup` do the same.

**The password is shown once.** Only its hash is stored, so no file or
command can show it again: save it in a password manager or take a
screenshot before you press Enter. It can be replaced (`pcbridge set
auth.password`, or **Replace password** in the Remote tab), never recovered.

Give remote clients `<public_url>/mcp`; they authorize with the password.
The tunnel does not start at login; `pcbridge remote stop` closes it.

## 5. Check

```bash
pcbridge doctor
pcbridge status
```

## Architectures

Releases are built for x86_64 only: the wheel is tagged `linux_x86_64`,
the packages are `amd64` and `x86_64`, and so is the native helper. On ARM
(aarch64) the install script stops and points to a git checkout, which
installs and runs everything except desktop control. Desktop control needs
the native helper, and none is built for aarch64, so ARM has no desktop
control; the Python paths that drove it there until 2.8 were removed.
Decided with the maintainer (2026-11-13): ARM has no desktop control
until an aarch64 build of the helper exists, which is an item of its own
in [ROADMAP.md](../ROADMAP.md). A helper you build yourself can be named
in `[native] binary_path`, untested.

Restart Claude Desktop and any running Claude Code / Codex session once:
processes started before the install keep their old server until then.

## Updating

- User install: run the install script again (`--version` for a specific
  release); the daemon restarts itself when idle.
- Package: `sudo apt install ./pcbridge_<new>.deb`, or on Arch
  `sudo pacman -U pcbridge-<new>-x86_64.pkg.tar.zst`; same.
- Git checkout: `pcbridge update` pulls and restarts the daemon when idle.
