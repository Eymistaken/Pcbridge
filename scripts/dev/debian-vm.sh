#!/usr/bin/env bash
# A Debian test VM with Cinnamon and XFCE on X11.
#
#   scripts/dev/debian-vm.sh create      download + verify the cloud image, make the disk and seed
#   scripts/dev/debian-vm.sh start       boot headless (VNC on 127.0.0.1:5907, SSH on 127.0.0.1:2224)
#   scripts/dev/debian-vm.sh provision   install the desktops and LightDM only; log in to the first at boot
#   scripts/dev/debian-vm.sh session D   log in to D (cinnamon or xfce) now and at every boot
#   scripts/dev/debian-vm.sh fresh       a throwaway disk over the provisioned one (start it with PCBRIDGE_VM_DISK)
#   scripts/dev/debian-vm.sh put FILE..  copy files to the test user's home
#   scripts/dev/debian-vm.sh ssh [cmd]   a shell (or one command) as the test user
#   scripts/dev/debian-vm.sh screenshot  write the VM's screen to <dir>/screen.png
#   scripts/dev/debian-vm.sh stop        power off
#
# PCBRIDGE_DEBIAN_RELEASE picks the release: 12 (the default) or 13. Each
# has its own directory, ports and disks, so both can run at once.
# Why Debian 12: Python 3.11 exercises the install script's uv path, glibc
# 2.36 a release native helper below glibc 2.39, and Cinnamon and XFCE on
# X11 the desktops below it. Pardus 23 is based on Debian 12 and has no cloud
# image, so this stands in for it. Debian 13 (glibc 2.41) is the same pair of
# desktops above glibc 2.39.
# PCBRIDGE_DEBIAN_DESKTOPS lists the desktops to provision (default
# "cinnamon xfce"); the first logs in at boot. Provisioning installs the desktop and nothing pcbridge needs, so an
# install from here shows exactly which packages pcbridge adds.
#
# The "generic" image, not "genericcloud": the cloud kernel leaves out
# drivers a graphical session needs. pcbridge's input stays inside the VM.
#
# Fresh runs: `fresh` puts test.qcow2 over the provisioned debian.qcow2;
# `PCBRIDGE_VM_DISK=<dir>/test.qcow2 scripts/dev/debian-vm.sh start` boots it.
#
# Needs: qemu-system-x86_64, qemu-img, genisoimage, ssh, curl, and /dev/kvm
# access (the kvm group). No sudo on the host.
set -euo pipefail

RELEASE="${PCBRIDGE_DEBIAN_RELEASE:-12}"
case "$RELEASE" in
    12) CODENAME=bookworm; SUFFIX=""; PORT_DEFAULT=2224; VNC_DEFAULT=7 ;;
    13) CODENAME=trixie; SUFFIX=13; PORT_DEFAULT=2225; VNC_DEFAULT=8 ;;
    *) printf 'debian-vm: release %s is not 12 or 13\n' "$RELEASE" >&2; exit 2 ;;
esac
DIR="${PCBRIDGE_DEBIAN_DEV_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/pcbridge-debian${SUFFIX}-dev}"
IMAGE_DIR="https://cloud.debian.org/images/cloud/$CODENAME/latest"
IMAGE_NAME="debian-$RELEASE-generic-amd64.qcow2"
BASE="$DIR/$IMAGE_NAME"
PROVISIONED="$DIR/debian.qcow2"
DISK="${PCBRIDGE_VM_DISK:-$PROVISIONED}"
SEED="$DIR/seed.iso"
KEY="$DIR/id_ed25519"
PIDFILE="$DIR/qemu.pid"
QMP="$DIR/qmp.sock"
SSH_PORT="${PCBRIDGE_DEBIAN_VM_SSH_PORT:-$PORT_DEFAULT}"
VNC_DISPLAY="${PCBRIDGE_DEBIAN_VM_VNC_DISPLAY:-$VNC_DEFAULT}"
VM_USER=tester
DESKTOPS="${PCBRIDGE_DEBIAN_DESKTOPS:-cinnamon xfce}"

# The desktops only. tmux, libnotify-bin, tesseract and python3-venv are left
# out on purpose: the install must bring what it needs. XFCE gets the locker
# Debian's XFCE task recommends, light-locker (Debian 12 has no
# xfce4-screensaver); PCBRIDGE_DEBIAN_XFCE_LOCKER=xfce4-screensaver picks the
# other one on 13.
PACKAGES=(lightdm lightdm-gtk-greeter xserver-xorg locales)
for desktop in $DESKTOPS; do
    case "$desktop" in
        cinnamon) PACKAGES+=(cinnamon-core) ;;
        xfce) PACKAGES+=(xfce4 "${PCBRIDGE_DEBIAN_XFCE_LOCKER:-light-locker}") ;;
        *) printf 'debian-vm: unknown desktop %s\n' "$desktop" >&2; exit 2 ;;
    esac
done
FIRST_DESKTOP="${DESKTOPS%% *}"

session_name() {
    case "$1" in
        cinnamon) echo cinnamon ;;
        xfce) echo xfce ;;
        *) die "session cinnamon|xfce" ;;
    esac
}

die() { printf 'debian-vm: %s\n' "$*" >&2; exit 1; }

running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

vm_ssh() {
    ssh -i "$KEY" -p "$SSH_PORT" \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile="$DIR/known_hosts" \
        -o LogLevel=ERROR -o ConnectTimeout=5 \
        "$VM_USER@127.0.0.1" "$@"
}

qmp() {
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
    curl -fsSL -o "$DIR/SHA512SUMS" "$IMAGE_DIR/SHA512SUMS"
    [ -f "$BASE" ] || curl -fsSL -o "$BASE" "$IMAGE_DIR/$IMAGE_NAME"
    (cd "$DIR" && grep " $IMAGE_NAME\$" SHA512SUMS | sha512sum -c -) || die "checksum mismatch"
    [ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N '' -C pcbridge-dev -f "$KEY"
    [ -f "$PROVISIONED" ] || qemu-img create -q -f qcow2 -b "$BASE" -F qcow2 "$PROVISIONED" 30G
    local tmp
    tmp="$(mktemp -d)"
    cat >"$tmp/user-data" <<EOF
#cloud-config
hostname: pcbridge-debian
users:
  - name: $VM_USER
    groups: [sudo, input]
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    shell: /bin/bash
    lock_passwd: true
    ssh_authorized_keys:
      - $(cat "$KEY.pub")
growpart: {mode: auto, devices: ["/"]}
resize_rootfs: true
EOF
    printf 'instance-id: pcbridge-debian\nlocal-hostname: pcbridge-debian\n' >"$tmp/meta-data"
    genisoimage -quiet -output "$SEED" -volid cidata -joliet -rock "$tmp/user-data" "$tmp/meta-data"
    rm -r "$tmp"
    echo "created $PROVISIONED"
}

cmd_start() {
    [ -f "$DISK" ] || die "no disk $DISK; run: $0 create"
    running && { echo "already running"; return; }
    qemu-system-x86_64 \
        -name "pcbridge-debian$SUFFIX" -enable-kvm -cpu host -smp 4 -m 6G \
        -drive file="$DISK",if=virtio,discard=unmap \
        -drive file="$SEED",media=cdrom,readonly=on \
        -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:"$SSH_PORT"-:22 \
        -device virtio-gpu-pci \
        -device qemu-xhci -device usb-tablet \
        -vnc 127.0.0.1:"$VNC_DISPLAY" \
        -qmp unix:"$QMP",server=on,wait=off \
        -pidfile "$PIDFILE" -daemonize -display none
    for _ in $(seq 1 90); do
        vm_ssh true 2>/dev/null && { echo "up (ssh port $SSH_PORT, disk $(basename "$DISK"))"; return; }
        sleep 2
    done
    die "no SSH after 180 s"
}

cmd_provision() {
    vm_ssh "sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ${PACKAGES[*]}"
    # Turkish as the session language, as on a Pardus desktop: the plan then
    # has a locale language to offer (it is not offered here: no OCR on X11).
    vm_ssh 'sudo sed -i "s/^# *tr_TR.UTF-8/tr_TR.UTF-8/" /etc/locale.gen && sudo locale-gen >/dev/null
            sudo update-locale LANG=tr_TR.UTF-8
            sudo install -d /etc/lightdm/lightdm.conf.d
            printf "[Seat:*]\nautologin-user='"$VM_USER"'\nautologin-session='"$(session_name "$FIRST_DESKTOP")"'\n" | sudo tee /etc/lightdm/lightdm.conf.d/50-autologin.conf >/dev/null
            sudo systemctl enable lightdm && sudo systemctl set-default graphical.target
            sudo systemctl disable --now unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true'
    # Unattended upgrades held a reboot for minutes (measured on 12) and
    # change packages under a test run, so the VM has none.
    vm_ssh 'sudo reboot' || true
    sleep 5
    for _ in $(seq 1 90); do
        vm_ssh true 2>/dev/null && { echo "provisioned; $FIRST_DESKTOP logs in at boot"; return; }
        sleep 2
    done
    die "no SSH after the reboot"
}

cmd_session() {
    local name
    name="$(session_name "${1:-}")"
    vm_ssh "printf '[Seat:*]\nautologin-user=$VM_USER\nautologin-session=$name\n' | sudo tee /etc/lightdm/lightdm.conf.d/50-autologin.conf >/dev/null
            sudo reboot" || true
    # A reboot, not a lightdm restart: the user manager outlives a new login,
    # so the old desktop's services and XDG_SESSION_DESKTOP would linger
    # (measured: org.cinnamon.ScreenSaver stayed owned in the XFCE session).
    sleep 5
    for _ in $(seq 1 90); do
        vm_ssh "loginctl list-sessions --no-legend | grep -q seat0 && loginctl show-session \$(loginctl list-sessions --no-legend | awk '/seat0/{print \$1; exit}') -p Desktop --value" 2>/dev/null | grep -qi "$name" && { echo "logged in to $name"; return; }
        sleep 2
    done
    die "no $name session after 180 s"
}

cmd_fresh() {
    running && die "stop the VM first"
    [ -f "$PROVISIONED" ] || die "no provisioned disk; run create and provision first"
    qemu-img create -q -f qcow2 -b "$PROVISIONED" -F qcow2 "$DIR/test.qcow2"
    echo "fresh $DIR/test.qcow2; start it with: PCBRIDGE_VM_DISK=$DIR/test.qcow2 $0 start"
}

cmd_put() {
    [ "$#" -gt 0 ] || die "put FILE..."
    scp -i "$KEY" -P "$SSH_PORT" -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile="$DIR/known_hosts" -o LogLevel=ERROR \
        "$@" "$VM_USER@127.0.0.1:"
}

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
    session) shift; cmd_session "$@" ;;
    fresh) cmd_fresh ;;
    put) shift; cmd_put "$@" ;;
    ssh) shift; vm_ssh "$@" ;;
    screenshot) shift; cmd_screenshot "$@" ;;
    stop) cmd_stop ;;
    *) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
