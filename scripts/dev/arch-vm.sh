#!/usr/bin/env bash
# An Arch Linux test VM for the KDE Plasma and Arch work.
#
#   scripts/dev/arch-vm.sh create      download + verify the cloud image, make the disk and seed
#   scripts/dev/arch-vm.sh start       boot headless (VNC on 127.0.0.1:5905, SSH on 127.0.0.1:2222)
#   scripts/dev/arch-vm.sh provision   install Plasma, GNOME and the build tools; log in to Plasma
#   scripts/dev/arch-vm.sh sync        copy this checkout (tracked + untracked, not ignored) to ~/pcbridge
#   scripts/dev/arch-vm.sh ssh [cmd]   a shell (or one command) as the test user
#   scripts/dev/arch-vm.sh session CMD run CMD inside the graphical session's environment
#   scripts/dev/arch-vm.sh screenshot  write the VM's screen to <dir>/screen.png
#   scripts/dev/arch-vm.sh stop        power off
#
# Why a VM: pcbridge sends real keys and clicks. Inside the VM they go to the
# VM's own kernel, never to the desktop running the tests. Nothing is
# installed on the host; everything lives in $PCBRIDGE_DEV_DIR.
#
# Needs: qemu-system-x86_64, qemu-img, genisoimage, ssh, curl, and /dev/kvm
# access (the kvm group). No sudo on the host.
set -euo pipefail

DIR="${PCBRIDGE_DEV_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/pcbridge-dev}"
IMAGE_URL="https://geo.mirror.pkgbuild.com/images/latest/Arch-Linux-x86_64-cloudimg.qcow2"
BASE="$DIR/Arch-Linux-x86_64-cloudimg.qcow2"
DISK="$DIR/arch.qcow2"
SEED="$DIR/seed.iso"
KEY="$DIR/id_ed25519"
PIDFILE="$DIR/qemu.pid"
QMP="$DIR/qmp.sock"
SSH_PORT="${PCBRIDGE_VM_SSH_PORT:-2222}"
VNC_DISPLAY="${PCBRIDGE_VM_VNC_DISPLAY:-5}"
VM_USER=tester

# Everything the KDE and Arch work needs inside the VM.
PACKAGES=(
    plasma-meta kwin xdg-desktop-portal-kde spectacle kate konsole dolphin sddm
    gnome-shell mutter gnome-text-editor
    pipewire wireplumber gst-plugin-pipewire gst-plugins-base gst-plugins-good
    python python-gobject at-spi2-core
    tmux wl-clipboard libnotify tesseract tesseract-data-eng
    base-devel git rust clang pkgconf namcap binutils
    jq
)

die() { printf 'arch-vm: %s\n' "$*" >&2; exit 1; }

running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

vm_ssh() {
    ssh -i "$KEY" -p "$SSH_PORT" \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile="$DIR/known_hosts" \
        -o LogLevel=ERROR -o ConnectTimeout=5 \
        "$VM_USER@127.0.0.1" "$@"
}

qmp() {
    # One QMP command; qmp_capabilities must come first on every connection.
    python3 - "$QMP" "$1" <<'PY'
import json, socket, sys
s = socket.socket(socket.AF_UNIX)
s.connect(sys.argv[1])
f = s.makefile("rw")
f.readline()
for cmd in ({"execute": "qmp_capabilities"}, json.loads(sys.argv[2])):
    f.write(json.dumps(cmd) + "\n"); f.flush()
    while True:
        reply = json.loads(f.readline())
        if "return" in reply or "error" in reply:
            break
if "error" in reply:
    sys.exit(reply["error"].get("desc", "qmp error"))
PY
}

cmd_create() {
    command -v genisoimage >/dev/null || die "genisoimage is missing"
    mkdir -p "$DIR"
    if [ ! -f "$BASE" ]; then
        curl -fL -o "$BASE.SHA256" "$IMAGE_URL.SHA256"
        curl -fL -o "$BASE" "$IMAGE_URL"
    fi
    (cd "$DIR" && sha256sum -c "$(basename "$BASE").SHA256") || die "checksum mismatch"
    [ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N '' -C pcbridge-dev -f "$KEY"
    if [ ! -f "$DISK" ]; then
        qemu-img create -q -f qcow2 -b "$BASE" -F qcow2 "$DISK" 40G
    fi
    local tmp
    tmp="$(mktemp -d)"
    cat >"$tmp/user-data" <<EOF
#cloud-config
hostname: pcbridge-arch
users:
  - name: $VM_USER
    groups: [wheel, input]
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    shell: /bin/bash
    lock_passwd: true
    ssh_authorized_keys:
      - $(cat "$KEY.pub")
growpart: {mode: auto, devices: ["/"]}
resize_rootfs: true
EOF
    printf 'instance-id: pcbridge-arch\nlocal-hostname: pcbridge-arch\n' >"$tmp/meta-data"
    genisoimage -quiet -output "$SEED" -volid cidata -joliet -rock "$tmp/user-data" "$tmp/meta-data"
    rm -r "$tmp"
    echo "created $DISK"
}

cmd_start() {
    [ -f "$DISK" ] || die "no disk; run: $0 create"
    running && { echo "already running"; return; }
    qemu-system-x86_64 \
        -name pcbridge-arch -enable-kvm -cpu host -smp 4 -m 8G \
        -drive file="$DISK",if=virtio,discard=unmap \
        -drive file="$SEED",media=cdrom,readonly=on \
        -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:"$SSH_PORT"-:22 \
        -device virtio-gpu-pci,max_outputs=2 \
        -device qemu-xhci -device usb-tablet \
        -vnc 127.0.0.1:"$VNC_DISPLAY" \
        -qmp unix:"$QMP",server=on,wait=off \
        -pidfile "$PIDFILE" -daemonize -display none
    for _ in $(seq 1 90); do
        vm_ssh true 2>/dev/null && { echo "up (ssh port $SSH_PORT)"; return; }
        sleep 2
    done
    die "no SSH after 180 s"
}

cmd_provision() {
    vm_ssh "sudo pacman -Syu --noconfirm --needed ${PACKAGES[*]}"
    vm_ssh 'sudo install -d /etc/sddm.conf.d && printf "[Autologin]\nUser='"$VM_USER"'\nSession=plasma\nRelogin=true\n" | sudo tee /etc/sddm.conf.d/autologin.conf >/dev/null'
    vm_ssh 'sudo systemctl enable sddm && sudo systemctl set-default graphical.target'
    # The test user has no password: an automatic lock could not be undone
    # from the keyboard (`sudo loginctl unlock-session <id>` still works).
    vm_ssh 'kwriteconfig6 --file kscreenlockerrc --group Daemon --key Autolock false
            kwriteconfig6 --file kscreenlockerrc --group Daemon --key LockOnResume false
            kwriteconfig6 --file powerdevilrc --group AC --group Display --key TurnOffDisplayWhenIdle false
            kwriteconfig6 --file powerdevilrc --group AC --group Display --key DimDisplayWhenIdle false
            kwriteconfig6 --file powerdevilrc --group AC --group SuspendAndShutdown --key AutoSuspendAction 0
            busctl --user call org.freedesktop.ScreenSaver /ScreenSaver org.kde.screensaver configure'
    vm_ssh 'sudo reboot' || true
    sleep 5
    for _ in $(seq 1 90); do
        vm_ssh true 2>/dev/null && { echo "provisioned; Plasma logs in at boot"; return; }
        sleep 2
    done
    die "no SSH after the reboot"
}

cmd_sync() {
    local root
    root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    (cd "$root" && git ls-files -z -co --exclude-standard | tar --null -T - -czf -) \
        | vm_ssh 'rm -rf ~/pcbridge.new && mkdir ~/pcbridge.new && tar -xzf - -C ~/pcbridge.new \
                  && rm -rf ~/pcbridge.src && { [ ! -d ~/pcbridge ] || mv ~/pcbridge ~/pcbridge.src; } \
                  && mv ~/pcbridge.new ~/pcbridge && { [ ! -d ~/pcbridge.src/.venv ] || mv ~/pcbridge.src/.venv ~/pcbridge/; } \
                  && { [ ! -d ~/pcbridge.src/rust/target ] || mv ~/pcbridge.src/rust/target ~/pcbridge/rust/; } \
                  && { [ ! -d ~/pcbridge.src/pcbridge/_native ] || mv ~/pcbridge.src/pcbridge/_native ~/pcbridge/pcbridge/; } \
                  && rm -rf ~/pcbridge.src'
    echo "synced to ~/pcbridge"
}

# The environment of the logged-in Plasma (or GNOME) session, for commands
# started over SSH: the session bus, the Wayland socket, the desktop name.
SESSION_ENV='export XDG_RUNTIME_DIR=/run/user/$(id -u) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus; eval "$(systemctl --user show-environment | grep -E "^(WAYLAND_DISPLAY|XDG_CURRENT_DESKTOP|XDG_SESSION_TYPE|XDG_SESSION_DESKTOP|DISPLAY|QT_QPA_PLATFORM)=" | sed "s/^/export /")"'

cmd_screenshot() {
    running || die "not running"
    local out="${1:-$DIR/screen.png}"
    qmp "{\"execute\": \"screendump\", \"arguments\": {\"filename\": \"$out\", \"format\": \"png\"}}"
    echo "$out"
}

cmd_stop() {
    running || { echo "not running"; return; }
    vm_ssh 'sudo systemctl poweroff' 2>/dev/null || qmp '{"execute": "system_powerdown"}'
    for _ in $(seq 1 30); do running || { echo "stopped"; return; }; sleep 2; done
    qmp '{"execute": "quit"}' || true
}

case "${1:-}" in
    create) cmd_create ;;
    start) cmd_start ;;
    provision) cmd_provision ;;
    sync) cmd_sync ;;
    ssh) shift; vm_ssh "$@" ;;
    session) shift; vm_ssh "$SESSION_ENV; $*" ;;
    screenshot) shift; cmd_screenshot "$@" ;;
    stop) cmd_stop ;;
    *) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
