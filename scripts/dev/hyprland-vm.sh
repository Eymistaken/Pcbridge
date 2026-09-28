#!/usr/bin/env bash
# An isolated Arch Linux test VM for Hyprland desktop acceptance.
#
#   scripts/dev/hyprland-vm.sh create      make an independent disk and seed
#   scripts/dev/hyprland-vm.sh start       boot headless (VNC :5906, SSH :2223)
#   scripts/dev/hyprland-vm.sh provision   install Hyprland and test tools
#   scripts/dev/hyprland-vm.sh sync        copy tracked worktree files to ~/pcbridge
#   scripts/dev/hyprland-vm.sh ssh [cmd]   run a shell command as the test user
#   scripts/dev/hyprland-vm.sh session CMD run CMD in the graphical session
#   scripts/dev/hyprland-vm.sh screenshot  write the VM screen to a PNG
#   scripts/dev/hyprland-vm.sh stop        power off
#
# Why a VM: pcbridge sends real keys and clicks. Inside the VM they go to the
# VM's own kernel, never to the desktop running the tests. Nothing is
# installed on the host; the disk lives in $PCBRIDGE_HYPRLAND_DEV_DIR.
#
# Needs: qemu-system-x86_64, qemu-img, genisoimage, ssh, curl, and /dev/kvm
# access (the kvm group). No sudo on the host.
set -euo pipefail

DIR="${PCBRIDGE_HYPRLAND_DEV_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/pcbridge-hyprland-dev}"
IMAGE_URL="https://geo.mirror.pkgbuild.com/images/latest/Arch-Linux-x86_64-cloudimg.qcow2"
BASE="${PCBRIDGE_ARCH_BASE:-${XDG_DATA_HOME:-$HOME/.local/share}/pcbridge-dev/Arch-Linux-x86_64-cloudimg.qcow2}"
DISK="$DIR/hyprland.qcow2"
SEED="$DIR/seed.iso"
KEY="$DIR/id_ed25519"
PIDFILE="$DIR/qemu.pid"
QMP="$DIR/qmp.sock"
SSH_PORT="${PCBRIDGE_HYPRLAND_VM_SSH_PORT:-2223}"
VNC_DISPLAY="${PCBRIDGE_HYPRLAND_VM_VNC_DISPLAY:-6}"
VM_USER=tester

# A stock Arch Hyprland session, its security dependencies, and test clients.
PACKAGES=(
    hyprland hypridle hyprlock hyprland-guiutils
    xdg-desktop-portal xdg-desktop-portal-hyprland
    xdg-desktop-portal-gtk sddm foot gnome-text-editor xorg-xwayland
    pipewire wireplumber gst-plugin-pipewire gst-plugins-base gst-plugins-good
    python python-gobject python-cairo at-spi2-core qt6-wayland
    tmux wl-clipboard libnotify tesseract tesseract-data-eng
    base-devel git rust clang pkgconf namcap binutils
    jq grim wayland-utils xterm
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
    mkdir -p "$DIR" "$(dirname "$BASE")"
    if [ ! -f "$BASE" ]; then
        curl -fL -o "$BASE.SHA256" "$IMAGE_URL.SHA256"
        curl -fL -o "$BASE" "$IMAGE_URL"
    fi
    (cd "$(dirname "$BASE")" && sha256sum -c "$(basename "$BASE").SHA256") || die "checksum mismatch"
    [ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N '' -C pcbridge-dev -f "$KEY"
    if [ ! -f "$DISK" ]; then
        qemu-img create -q -f qcow2 -b "$BASE" -F qcow2 "$DISK" 40G
    fi
    local tmp
    tmp="$(mktemp -d)"
    cat >"$tmp/user-data" <<EOF
#cloud-config
hostname: pcbridge-hyprland
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
    printf 'instance-id: pcbridge-hyprland\nlocal-hostname: pcbridge-hyprland\n' >"$tmp/meta-data"
    genisoimage -quiet -output "$SEED" -volid cidata -joliet -rock "$tmp/user-data" "$tmp/meta-data"
    rm -r "$tmp"
    echo "created $DISK"
}

cmd_start() {
    [ -f "$DISK" ] || die "no disk; run: $0 create"
    running && { echo "already running"; return; }
    # No implicit standard VGA: Hyprland mapped clients there but rendered
    # black frames. VNC enables only head 0 per device, so use two virtio GPUs.
    qemu-system-x86_64 \
        -name pcbridge-hyprland -enable-kvm -cpu host -smp 4 -m 8G \
        -drive file="$DISK",if=virtio,discard=unmap \
        -drive file="$SEED",media=cdrom,readonly=on \
        -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:"$SSH_PORT"-:22 \
        -vga none -device virtio-gpu-pci,max_outputs=1 \
        -device virtio-gpu-pci,max_outputs=1 \
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
    vm_ssh 'sudo install -d /etc/sddm.conf.d && printf "[Autologin]\nUser='"$VM_USER"'\nSession=hyprland\nRelogin=true\n" | sudo tee /etc/sddm.conf.d/autologin.conf >/dev/null'
    vm_ssh 'sudo systemctl enable sddm && sudo systemctl set-default graphical.target'
    # Set a VM-only password before exercising the real hyprlock transition.
    vm_ssh 'sudo reboot' || true
    sleep 5
    for _ in $(seq 1 90); do
        vm_ssh true 2>/dev/null && { echo "provisioned; inspect Hyprland login"; return; }
        sleep 2
    done
    die "no SSH after the reboot"
}

cmd_sync() {
    local root
    root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    (cd "$root" && git ls-files -z \
        | while IFS= read -r -d '' path; do
            [ -e "$path" ] || [ -L "$path" ] || continue
            printf '%s\0' "$path"
        done | tar --null -T - -czf -) \
        | vm_ssh 'rm -rf ~/pcbridge.new && mkdir ~/pcbridge.new && tar -xzf - -C ~/pcbridge.new \
                  && rm -rf ~/pcbridge.src && { [ ! -d ~/pcbridge ] || mv ~/pcbridge ~/pcbridge.src; } \
                  && mv ~/pcbridge.new ~/pcbridge && { [ ! -d ~/pcbridge.src/.venv ] || mv ~/pcbridge.src/.venv ~/pcbridge/; } \
                  && { [ ! -d ~/pcbridge.src/rust/target ] || mv ~/pcbridge.src/rust/target ~/pcbridge/rust/; } \
                  && { [ ! -d ~/pcbridge.src/pcbridge/_native ] || mv ~/pcbridge.src/pcbridge/_native ~/pcbridge/pcbridge/; } \
                  && rm -rf ~/pcbridge.src'
    echo "synced to ~/pcbridge"
}

# The environment of the logged-in Hyprland session, for commands
# started over SSH: the session bus, the Wayland socket, the desktop name.
SESSION_ENV='export XDG_RUNTIME_DIR=/run/user/$(id -u) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus; while IFS= read -r entry; do case "$entry" in WAYLAND_DISPLAY=*|XDG_CURRENT_DESKTOP=*|XDG_SESSION_TYPE=*|XDG_SESSION_DESKTOP=*|HYPRLAND_INSTANCE_SIGNATURE=*|DISPLAY=*|QT_QPA_PLATFORM=*) export "$entry";; esac; done < <(systemctl --user show-environment); if [ -n "${WAYLAND_DISPLAY:-}" ] && [ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]; then export XDG_SESSION_TYPE=wayland; fi'

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
