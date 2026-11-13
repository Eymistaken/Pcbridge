#!/usr/bin/env bash
# A Linux Mint 21.3 Cinnamon LIVE session in a VM: glibc 2.35, Cinnamon on X11.
#
#   scripts/dev/mint-live-vm.sh start       boot the ISO (VNC on 127.0.0.1:5909, SSH on 127.0.0.1:2226)
#   scripts/dev/mint-live-vm.sh enable-ssh  type into the live session: install openssh-server, add the key
#   scripts/dev/mint-live-vm.sh ssh [cmd]   a shell (or one command) as the live user `mint`
#   scripts/dev/mint-live-vm.sh put FILE..  copy files to mint's home
#   scripts/dev/mint-live-vm.sh screenshot  write the VM's screen to <dir>/screen.png
#   scripts/dev/mint-live-vm.sh stop        power off (the live session keeps nothing)
#
# Mint has no cloud image, so this boots the release ISO itself
# (linuxmint-21.3-cinnamon-64bit.iso, checked against the release's
# sha256sum.txt and put in the directory below by hand). The live session
# logs in to Cinnamon on X11 as `mint` with no password. It has no SSH
# server: `enable-ssh` opens a terminal with Ctrl+Alt+T and types one short
# line through QEMU's monitor (the keys go to the VM and nowhere else). That
# line fetches the setup commands from a one-request server on the host's
# loopback, which QEMU's user network shows the VM as 10.0.2.2. The line is
# short and typed slowly on purpose: at 20 ms per key the live session
# dropped keys and kept Shift held down.
# Everything lives in RAM and is gone at `stop`.
#
# Needs: qemu-system-x86_64, ssh, and /dev/kvm access. No sudo on the host.
set -euo pipefail

DIR="${PCBRIDGE_MINT_DEV_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/pcbridge-mint-dev}"
ISO="$DIR/linuxmint-21.3-cinnamon-64bit.iso"
KEY="${PCBRIDGE_MINT_KEY:-${XDG_DATA_HOME:-$HOME/.local/share}/pcbridge-debian-dev/id_ed25519}"
PIDFILE="$DIR/qemu.pid"
QMP="$DIR/qmp.sock"
SSH_PORT="${PCBRIDGE_MINT_VM_SSH_PORT:-2226}"
VNC_DISPLAY="${PCBRIDGE_MINT_VM_VNC_DISPLAY:-9}"

die() { printf 'mint-live-vm: %s\n' "$*" >&2; exit 1; }

running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

vm_ssh() {
    ssh -i "$KEY" -p "$SSH_PORT" \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile="$DIR/known_hosts" \
        -o LogLevel=ERROR -o ConnectTimeout=5 \
        mint@127.0.0.1 "$@"
}

# One QMP command, or with `keys` a line of text typed key by key (US layout).
qmp() {
    python3 - "$QMP" "$@" <<'PY'
import json, socket, sys, time
s = socket.socket(socket.AF_UNIX)
s.connect(sys.argv[1])
f = s.makefile("rw")
f.readline()

def send(cmd):
    f.write(json.dumps(cmd) + "\n"); f.flush()
    while True:
        reply = json.loads(f.readline())
        if "return" in reply or "error" in reply:
            if "error" in reply:
                sys.exit(reply["error"].get("desc", "qmp error"))
            return reply

send({"execute": "qmp_capabilities"})
if sys.argv[2] != "keys":
    send(json.loads(sys.argv[2]))
    sys.exit(0)
NAMES = {" ": "spc", "-": "minus", "=": "equal", "/": "slash", ".": "dot", ",": "comma",
         ";": "semicolon", "'": "apostrophe", "\n": "ret", "\\": "backslash",
         "[": "bracket_left", "]": "bracket_right", "`": "grave_accent", "\t": "tab"}
SHIFTED = {"_": "minus", "+": "equal", "?": "slash", ">": "dot", "<": "comma", ":": "semicolon",
           '"': "apostrophe", "|": "backslash", "{": "bracket_left", "}": "bracket_right",
           "~": "grave_accent", "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6",
           "&": "7", "*": "8", "(": "9", ")": "0"}
for ch in sys.argv[3]:
    if ch in SHIFTED:
        keys = ["shift", SHIFTED[ch]]
    elif ch.isupper():
        keys = ["shift", ch.lower()]
    elif ch.isalnum():
        keys = [ch]
    elif ch in NAMES:
        keys = [NAMES[ch]]
    else:
        sys.exit(f"cannot type {ch!r}")
    send({"execute": "send-key", "arguments": {
        "keys": [{"type": "qcode", "data": k} for k in keys], "hold-time": 80}})
    time.sleep(0.15)
PY
}

cmd_start() {
    [ -f "$ISO" ] || die "no $ISO; download it and check it against sha256sum.txt first"
    running && { echo "already running"; return; }
    qemu-system-x86_64 \
        -name pcbridge-mint -enable-kvm -cpu host -smp 4 -m 6G \
        -cdrom "$ISO" -boot d \
        -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:"$SSH_PORT"-:22 \
        -device virtio-vga \
        -device qemu-xhci -device usb-tablet \
        -vnc 127.0.0.1:"$VNC_DISPLAY" \
        -qmp unix:"$QMP",server=on,wait=off \
        -pidfile "$PIDFILE" -daemonize -display none
    echo "booting; the live session logs in by itself (watch with: $0 screenshot)"
}

cmd_enable_ssh() {
    running || die "not running"
    [ -f "$KEY.pub" ] || die "no public key $KEY.pub"
    local serve port=8731 server
    serve="$(mktemp -d)"
    {
        echo 'set -e'
        # The live system lists its own CD as a source, which apt refuses.
        echo "sudo sed -i 's/^deb cdrom:/# deb cdrom:/' /etc/apt/sources.list"
        echo 'sudo apt-get update -qq'
        echo 'sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server'
        echo 'mkdir -p ~/.ssh'
        echo "echo '$(cat "$KEY.pub")' > ~/.ssh/authorized_keys"
        echo 'chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys'
        echo 'sudo systemctl start ssh'
        echo 'echo SSH-READY'
    } >"$serve/s"
    # Answers one request on the loopback, then exits.
    python3 -c 'import http.server, sys
body = open(sys.argv[1], "rb").read()
class Once(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(body)
    def log_message(self, *args):
        pass
server = http.server.HTTPServer(("127.0.0.1", int(sys.argv[2])), Once)
server.timeout = 300
server.handle_request()' "$serve/s" "$port" &
    server=$!
    qmp '{"execute": "send-key", "arguments": {"keys": [{"type": "qcode", "data": "ctrl"}, {"type": "qcode", "data": "alt"}, {"type": "qcode", "data": "t"}]}}'
    sleep 4
    qmp keys "curl -s 10.0.2.2:$port/s | bash
"
    local up=1
    for _ in $(seq 1 120); do
        if vm_ssh true 2>/dev/null; then up=0; break; fi
        sleep 3
    done
    kill "$server" 2>/dev/null || true
    rm -r "$serve"
    [ "$up" = 0 ] || die "no SSH after 6 minutes; look at: $0 screenshot"
    echo "ssh up on port $SSH_PORT"
}

cmd_put() {
    [ "$#" -gt 0 ] || die "put FILE..."
    scp -i "$KEY" -P "$SSH_PORT" -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile="$DIR/known_hosts" -o LogLevel=ERROR \
        "$@" mint@127.0.0.1:
}

cmd_screenshot() {
    running || die "not running"
    local out="${1:-$DIR/screen.png}"
    qmp "{\"execute\": \"screendump\", \"arguments\": {\"filename\": \"$out\", \"format\": \"png\"}}"
    echo "$out"
}

cmd_stop() {
    running || { echo "not running"; return; }
    qmp '{"execute": "quit"}' || true
    for _ in $(seq 1 15); do running || { echo "stopped"; return; }; sleep 1; done
    kill "$(cat "$PIDFILE")" 2>/dev/null || true
}

case "${1:-}" in
    start) cmd_start ;;
    enable-ssh) cmd_enable_ssh ;;
    put) shift; cmd_put "$@" ;;
    ssh) shift; vm_ssh "$@" ;;
    screenshot) shift; cmd_screenshot "$@" ;;
    stop) cmd_stop ;;
    *) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
