#!/usr/bin/env bash
# Developer install of the pcbridge GNOME Shell extension: link / remove /
# status. (A normal install copies the extension with `pcbridge setup`.)
#
# This installs a SYMLINK: the file you edit in the repository is the running
# extension, with no copy step. A broken extension can take the shell down on
# Wayland, so the emergency undo is printed on every run.

set -euo pipefail

UUID="pcbridge-gorunur@eymistaken.local"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$HERE/$UUID"
TARGET_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions"
TARGET="$TARGET_DIR/$UUID"

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }

# Add the UUID to (or remove it from) gsettings' `enabled-extensions`.
# `gnome-extensions enable` asks the running shell over D-Bus and FAILS in a
# shell that has not scanned the extension yet; gsettings always works.
edit_list() {
    local action="$1"
    python3 - "$action" "$UUID" <<'PY'
import ast, subprocess, sys

action, uuid = sys.argv[1], sys.argv[2]
key = ["gsettings", "get", "org.gnome.shell", "enabled-extensions"]
raw = subprocess.run(key, capture_output=True, text=True, check=True).stdout.strip()
# An empty list prints as `@as []`.
if raw.startswith("@as "):
    raw = raw[4:]
items = list(ast.literal_eval(raw))

if action == "add" and uuid not in items:
    items.append(uuid)
elif action == "remove":
    items = [x for x in items if x != uuid]
else:
    print("no change")
    sys.exit(0)

value = "[" + ", ".join(f"'{x}'" for x in items) + "]"
subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", value], check=True)
print(f"enabled-extensions updated ({len(items)} extensions)")
PY
}

undo_help() {
    echo
    yellow "─── EMERGENCY UNDO ──────────────────────────────────────"
    red "RUN THIS FIRST — it takes effect IMMEDIATELY:"
    echo
    echo "    gnome-extensions disable $UUID"
    echo
    echo "Then make it permanent:"
    echo
    echo "    $HERE/install.sh --remove"
    echo
    yellow 'NOTE: rm ALONE IS NOT ENOUGH.'
    echo "Deleting the files does not stop the RUNNING extension; the shell"
    echo "has already loaded it. It only goes away when the shell restarts"
    echo "(log out and in, or reboot). This happened once: rm was typed,"
    echo 'nothing changed, and the machine had to be restarted.'
    echo
    echo "If the shell is frozen, switch to a TTY with Ctrl+Alt+F3 and run the"
    echo 'gnome-extensions disable command above from there.'
    yellow "─────────────────────────────────────────────────────────"
}

status() {
    echo "UUID    : $UUID"
    echo "source  : $SOURCE"
    echo "target  : $TARGET"
    if [[ -L "$TARGET" ]]; then
        green "installed: yes (symlink -> $(readlink "$TARGET"))"
    elif [[ -e "$TARGET" ]]; then
        red "installed: the target is a real directory, NOT a symlink -- look at it by hand"
    else
        echo "installed: no"
    fi
    local enabled
    enabled="$(gsettings get org.gnome.shell enabled-extensions 2>/dev/null || echo '?')"
    if [[ "$enabled" == *"$UUID"* ]]; then
        green "enabled : yes"
    else
        echo "enabled : no"
    fi
    echo "shell   : $(gnome-shell --version 2>/dev/null || echo '?')"
}

install_link() {
    [[ -d "$SOURCE" ]] || { red "source directory missing: $SOURCE"; exit 1; }
    [[ -f "$SOURCE/metadata.json" ]] || { red "metadata.json missing"; exit 1; }

    if [[ -e "$TARGET" && ! -L "$TARGET" ]]; then
        red "$TARGET is a real directory, not a symlink. Not overwriting it;"
        red "look at it yourself and move it away first."
        exit 1
    fi

    mkdir -p "$TARGET_DIR"
    ln -sfn "$SOURCE" "$TARGET"
    green "symlink created: $TARGET -> $SOURCE"

    if [[ "${1:-}" != "--link-only" ]]; then
        edit_list add
        green "extension enabled"
    else
        echo "enabling skipped (--link-only)"
    fi

    echo
    yellow "GNOME 45+ caches ESM modules: code changes and the first install"
    yellow "need a SHELL RESTART. On Wayland that means logging out and in."
    yellow "To try it in a separate session:"
    echo
    echo "    dbus-run-session -- gnome-shell --nested --wayland"
    undo_help
}

remove_link() {
    edit_list remove || true
    if [[ -L "$TARGET" ]]; then
        rm "$TARGET"
        green "symlink removed: $TARGET"
    elif [[ -e "$TARGET" ]]; then
        red "$TARGET is not a symlink; remove it by hand."
    else
        echo "not installed"
    fi
    echo "The extension is fully gone once the shell restarts (log out and in)."
}

case "${1:-}" in
    ""|--install|--kur)               install_link ;;
    --link-only|--yalniz-baglanti)    install_link --link-only ;;
    --remove|--kaldir)                remove_link ;;
    --status|--durum)                 status ;;
    -h|--help|--yardim)
        cat <<EOF
Usage: install.sh [option]

  (none) | --install   create the symlink, enable it, print the undo steps
  --link-only          only create the symlink, do not enable
  --remove             disable and remove the symlink
  --status             installed? enabled? shell version
EOF
        ;;
    *) red "unknown option: $1"; exit 1 ;;
esac
