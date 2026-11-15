#!/usr/bin/env bash
# A Windows 11 test VM for the Windows port.
#
#   scripts/dev/windows-vm.sh create      check the ISO, make the disk, the TPM, the UEFI vars and the seed
#   scripts/dev/windows-vm.sh install     unattended install from the ISO; returns when SSH answers
#   scripts/dev/windows-vm.sh start       boot the installed disk (VNC on 127.0.0.1:5910, SSH on 127.0.0.1:2227)
#   scripts/dev/windows-vm.sh fresh       a throwaway disk over windows.qcow2 (start it with PCBRIDGE_WIN_VM_DISK)
#   scripts/dev/windows-vm.sh put FILE..  copy files to the test user's home
#   scripts/dev/windows-vm.sh sync        copy this checkout (tracked + untracked, not ignored) to %USERPROFILE%\pcbridge
#   scripts/dev/windows-vm.sh ssh [cmd]   a shell (or one command, run by cmd.exe) as the test user
#   scripts/dev/windows-vm.sh ps CMD      one PowerShell command as the test user
#   scripts/dev/windows-vm.sh session CMD one cmd.exe line in the logged-on desktop session, unelevated
#   scripts/dev/windows-vm.sh screenshot  write the VM's screen to <dir>/screen.png
#   scripts/dev/windows-vm.sh type TEXT   type into the VM through QEMU (US keys; works without SSH)
#   scripts/dev/windows-vm.sh key COMBO   press QEMU qcodes, e.g. meta_l+r or ret
#   scripts/dev/windows-vm.sh stop        shut down
#
# Why a VM: pcbridge sends real keys and clicks. Inside the VM they go to the
# VM's own input stack, never to the desktop running the tests. Nothing is
# installed on the host; everything lives in $PCBRIDGE_WIN_DEV_DIR.
#
# The ISO is Microsoft's multi-edition Windows 11 x64 ISO, downloaded by hand
# from microsoft.com/software-download/windows11 and checked here against the
# SHA-256 that page publishes. Windows 11 needs UEFI Secure Boot and a TPM 2.0:
# OVMF with Microsoft's keys enrolled and swtpm provide them. The install is
# unattended (windows/autounattend.xml): a local administrator `tester` with a
# generated password (kept in <dir>/password, never printed), automatic logon,
# and OpenSSH Server with this directory's key; then windows/quiet.ps1 turns
# telemetry, advertising, suggested apps and automatic update restarts off
# as far as Windows 11 Pro allows. sshd's default shell stays
# cmd.exe, because Windows PowerShell 5.1 mangles binary stdin and `sync`
# streams a tar archive.
#
# Needs: qemu-system-x86_64, qemu-img, swtpm, OVMF (secboot code + ms vars),
# genisoimage, ssh, and /dev/kvm access. No sudo on the host.
set -euo pipefail

DIR="${PCBRIDGE_WIN_DEV_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/pcbridge-win-dev}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOWNLOADS="$(xdg-user-dir DOWNLOAD 2>/dev/null || echo "$HOME/Downloads")"
ISO="${PCBRIDGE_WIN_ISO:-$DOWNLOADS/Windows11_Client_x64_en-us_26300_9457.iso}"
# Published on the download page for "English 64-bit", build 26300.9457.
ISO_SHA256="${PCBRIDGE_WIN_ISO_SHA256:-BD4307DF32BC8AF33B39CCECB1174AEB345386630F89A2B86C7A4E36B55EA650}"
PROVISIONED="$DIR/windows.qcow2"
DISK="${PCBRIDGE_WIN_VM_DISK:-$PROVISIONED}"
SEED="$DIR/seed.iso"
KEY="$DIR/id_ed25519"
PASSWORD_FILE="$DIR/password"
VARS="$DIR/OVMF_VARS.fd"
TPM_DIR="$DIR/tpm"
TPM_SOCK="$DIR/swtpm.sock"
TPM_PID="$DIR/swtpm.pid"
PIDFILE="$DIR/qemu.pid"
QMP="$DIR/qmp.sock"
SSH_PORT="${PCBRIDGE_WIN_VM_SSH_PORT:-2227}"
VNC_DISPLAY="${PCBRIDGE_WIN_VM_VNC_DISPLAY:-10}"
VM_USER=tester
OVMF_CODE="${PCBRIDGE_OVMF_CODE:-/usr/share/OVMF/OVMF_CODE_4M.secboot.fd}"
OVMF_VARS_TEMPLATE="${PCBRIDGE_OVMF_VARS:-/usr/share/OVMF/OVMF_VARS_4M.ms.fd}"

die() { printf 'windows-vm: %s\n' "$*" >&2; exit 1; }

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

# Type a line into the VM through QEMU's monitor, key by key, as a US keyboard
# (the first input language in autounattend.xml). The keys go to the VM only.
# Works before SSH exists, which is what it is for.
qmp_type() {
    python3 - "$QMP" "$1" <<'PY'
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
            return

send({"execute": "qmp_capabilities"})
NAMES = {" ": "spc", "-": "minus", "=": "equal", "/": "slash", ".": "dot", ",": "comma",
         ";": "semicolon", "'": "apostrophe", "\n": "ret", "\\": "backslash",
         "[": "bracket_left", "]": "bracket_right", "`": "grave_accent", "\t": "tab"}
SHIFTED = {"_": "minus", "+": "equal", "?": "slash", ">": "dot", "<": "comma", ":": "semicolon",
           '"': "apostrophe", "|": "backslash", "{": "bracket_left", "}": "bracket_right",
           "~": "grave_accent", "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6",
           "&": "7", "*": "8", "(": "9", ")": "0"}
for ch in sys.argv[2]:
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
        "keys": [{"type": "qcode", "data": k} for k in keys], "hold-time": 50}})
    time.sleep(0.05)
PY
}

# Press one combination given as QEMU qcodes joined by '+', e.g. meta_l+r.
qmp_key() {
    local keys json=""
    IFS=+ read -r -a keys <<<"$1"
    for k in "${keys[@]}"; do json="$json${json:+,}{\"type\":\"qcode\",\"data\":\"$k\"}"; done
    qmp "{\"execute\": \"send-key\", \"arguments\": {\"keys\": [$json], \"hold-time\": 80}}"
}

start_tpm() {
    if [ -f "$TPM_PID" ] && kill -0 "$(cat "$TPM_PID")" 2>/dev/null; then
        return
    fi
    mkdir -p "$TPM_DIR"
    swtpm socket --tpm2 --tpmstate dir="$TPM_DIR" \
        --ctrl type=unixio,path="$TPM_SOCK" \
        --pid file="$TPM_PID" --log file="$DIR/swtpm.log" --daemon
    for _ in $(seq 1 20); do [ -S "$TPM_SOCK" ] && return; sleep 0.25; done
    die "swtpm did not create $TPM_SOCK"
}

# The QEMU command line shared by install and start; extra drives come as arguments.
boot_vm() {
    start_tpm
    qemu-system-x86_64 \
        -name pcbridge-windows -enable-kvm \
        -machine q35,smm=on -global driver=cfi.pflash01,property=secure,value=on \
        -global ICH9-LPC.disable_s3=1 \
        -cpu host,hv_relaxed,hv_vapic,hv_spinlocks=0x1fff,hv_time -smp 4 -m 8G \
        -drive if=pflash,format=raw,unit=0,file="$OVMF_CODE",readonly=on \
        -drive if=pflash,format=raw,unit=1,file="$VARS" \
        -chardev socket,id=chrtpm,path="$TPM_SOCK" \
        -tpmdev emulator,id=tpm0,chardev=chrtpm -device tpm-crb,tpmdev=tpm0 \
        -device ahci,id=ahci \
        -drive id=disk,file="$DISK",if=none,format=qcow2,discard=unmap \
        -device ide-hd,drive=disk,bus=ahci.0,bootindex=1 \
        "$@" \
        -nic user,model=e1000e,hostfwd=tcp:127.0.0.1:"$SSH_PORT"-:22 \
        -device VGA,vgamem_mb=64 \
        -device qemu-xhci -device usb-tablet -device usb-kbd \
        -vnc 127.0.0.1:"$VNC_DISPLAY" \
        -qmp unix:"$QMP",server=on,wait=off \
        -pidfile "$PIDFILE" -daemonize -display none
}

wait_ssh() {
    local seconds="$1" end=$((SECONDS + $1))
    while [ "$SECONDS" -lt "$end" ]; do
        vm_ssh 'if exist C:\pcbridge-setup.done (exit 0) else (exit 1)' 2>/dev/null && return 0
        running || die "the VM stopped; look at $DIR/screen.png after the next start"
        sleep 10
    done
    die "no SSH after $seconds s; check the screen: $0 screenshot"
}

cmd_create() {
    command -v genisoimage >/dev/null || die "genisoimage is missing"
    command -v swtpm >/dev/null || die "swtpm is missing"
    [ -f "$OVMF_CODE" ] || die "no $OVMF_CODE (the ovmf package)"
    [ -f "$OVMF_VARS_TEMPLATE" ] || die "no $OVMF_VARS_TEMPLATE (the ovmf package)"
    [ -f "$ISO" ] || die "no $ISO; download it from microsoft.com/software-download/windows11"
    mkdir -p "$DIR"
    chmod 700 "$DIR"
    echo "checking the ISO's SHA-256 (8 GB, about a minute)"
    local sum
    sum="$(sha256sum "$ISO" | awk '{print toupper($1)}')"
    [ "$sum" = "$ISO_SHA256" ] || die "checksum mismatch: $sum"
    [ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N '' -C pcbridge-win-dev -f "$KEY"
    if [ ! -f "$PASSWORD_FILE" ]; then
        (umask 077 && python3 -c 'import secrets; print(secrets.token_urlsafe(18))' >"$PASSWORD_FILE")
    fi
    [ -f "$VARS" ] || cp "$OVMF_VARS_TEMPLATE" "$VARS"
    [ -f "$PROVISIONED" ] || qemu-img create -q -f qcow2 "$PROVISIONED" 80G
    local tmp password
    tmp="$(mktemp -d)"
    password="$(cat "$PASSWORD_FILE")"
    # token_urlsafe gives only [A-Za-z0-9_-]: nothing to escape in XML or sed.
    sed "s/@PASSWORD@/$password/g" "$HERE/windows/autounattend.xml" >"$tmp/autounattend.xml"
    cp "$HERE/windows/setup.ps1" "$HERE/windows/quiet.ps1" "$tmp/"
    cp "$KEY.pub" "$tmp/id_ed25519.pub"
    (umask 077 && genisoimage -quiet -output "$SEED" -volid PCBSETUP -joliet -rock \
        "$tmp/autounattend.xml" "$tmp/setup.ps1" "$tmp/quiet.ps1" "$tmp/id_ed25519.pub")
    rm -r "$tmp"
    echo "created $PROVISIONED; next: $0 install"
}

cmd_install() {
    [ -f "$SEED" ] || die "no seed; run: $0 create"
    running && die "already running"
    boot_vm \
        -drive id=cd0,file="$ISO",if=none,media=cdrom,readonly=on \
        -device ide-cd,drive=cd0,bus=ahci.1,bootindex=2 \
        -drive id=cd1,file="$SEED",if=none,media=cdrom,readonly=on \
        -device ide-cd,drive=cd1,bus=ahci.2
    # The empty disk falls through to the ISO, whose boot loader waits a few
    # seconds for "Press any key to boot from CD or DVD". Press Enter for it.
    # The keys go to the VM; with a complete answer file setup shows no UI.
    for _ in $(seq 1 30); do
        qmp '{"execute": "send-key", "arguments": {"keys": [{"type": "qcode", "data": "ret"}]}}' || true
        sleep 1
    done
    echo "installing; this takes 20-60 minutes (watch with: $0 screenshot)"
    wait_ssh 5400
    echo "installed; SSH answers on 127.0.0.1:$SSH_PORT"
}

cmd_start() {
    [ -f "$DISK" ] || die "no disk; run: $0 create && $0 install"
    running && { echo "already running"; return; }
    boot_vm
    wait_ssh 300
    echo "running; SSH on 127.0.0.1:$SSH_PORT, VNC on 127.0.0.1:59$(printf %02d "$VNC_DISPLAY")"
}

cmd_fresh() {
    running && die "stop the VM first"
    [ -f "$PROVISIONED" ] || die "no installed disk; run create and install first"
    qemu-img create -q -f qcow2 -b "$PROVISIONED" -F qcow2 "$DIR/test.qcow2"
    echo "fresh $DIR/test.qcow2; start it with: PCBRIDGE_WIN_VM_DISK=$DIR/test.qcow2 $0 start"
}

cmd_put() {
    [ "$#" -gt 0 ] || die "put FILE..."
    scp -i "$KEY" -P "$SSH_PORT" -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile="$DIR/known_hosts" -o LogLevel=ERROR \
        "$@" "$VM_USER@127.0.0.1:"
}

cmd_sync() {
    local root
    root="$(cd "$HERE/../.." && pwd)"
    # cmd.exe runs this; tar.exe (bsdtar) ships with Windows. The archive is
    # unpacked aside, then robocopy mirrors it over the checkout in place:
    # swapping directories fails while a pcbridge process runs from the
    # checkout (Windows refuses to move or delete it), which a running test
    # daemon always does. The virtualenv, the Rust build and the native
    # helper are left out of the mirror, so they stay. robocopy exits below
    # 8 on success.
    (cd "$root" && git ls-files -z -co --exclude-standard | tar --null -T - -czf -) \
        | vm_ssh 'cd /d %USERPROFILE% && (if exist pcbridge.new rmdir /s /q pcbridge.new) && mkdir pcbridge.new && tar -xzf - -C pcbridge.new && (robocopy pcbridge.new pcbridge /MIR /XD .venv target _native __pycache__ /NFL /NDL /NJH /NJS /NP >nul & if errorlevel 8 (exit 1) else (rmdir /s /q pcbridge.new))'
    echo "synced to %USERPROFILE%\\pcbridge"
}

# Run a cmd.exe command line in the test user's logged-on desktop session,
# unelevated, and print its output. SSH logons get a session of their own
# with no desktop, so screen capture, input and focus measurements must run
# here. A scheduled task with an interactive principal is the way in; the
# script reaches the VM base64-encoded, so no quoting survives the trip.
cmd_session() {
    [ "$#" -gt 0 ] || die "session CMD"
    local timeout="${PCBRIDGE_WIN_SESSION_TIMEOUT:-100}" script encoded
    script=$(cat <<EOF
\$ErrorActionPreference = 'Stop'; \$ProgressPreference = 'SilentlyContinue'
\$d = 'C:\\pcb-session'
New-Item -ItemType Directory -Force \$d | Out-Null
Remove-Item "\$d\\*" -Force -ErrorAction SilentlyContinue
\$cmd = @'
$*
'@
# The command goes in a batch file of its own and is called, so the
# redirection covers all of it, '&' chains included.
Set-Content -Path "\$d\\body.cmd" -Value "@echo off", "cd /d %USERPROFILE%\\pcbridge", \$cmd -Encoding ASCII
\$lines = "@echo off", "call \`"\$d\\body.cmd\`" > \`"\$d\\out.txt\`" 2>&1", "echo %ERRORLEVEL% > \`"\$d\\rc.txt\`""
Set-Content -Path "\$d\\run.cmd" -Value \$lines -Encoding ASCII
\$a = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c \`"\$d\\run.cmd\`""
\$p = New-ScheduledTaskPrincipal -UserId \$env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName pcb-session -Action \$a -Principal \$p -Force | Out-Null
Start-ScheduledTask -TaskName pcb-session
\$end = (Get-Date).AddSeconds($timeout)
while (-not (Test-Path "\$d\\rc.txt")) {
    if ((Get-Date) -gt \$end) { Write-Output 'session: timed out'; break }
    Start-Sleep -Milliseconds 300
}
Get-Content "\$d\\out.txt" -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName pcb-session -Confirm:\$false
if (Test-Path "\$d\\rc.txt") { exit [int](Get-Content "\$d\\rc.txt").Trim() } else { exit 124 }
EOF
)
    encoded="$(printf '%s' "$script" | iconv -f UTF-8 -t UTF-16LE | base64 -w0)"
    vm_ssh "powershell -NoProfile -NonInteractive -EncodedCommand $encoded"
}

cmd_screenshot() {
    running || die "not running"
    local out="${1:-$DIR/screen.png}"
    qmp "{\"execute\": \"screendump\", \"arguments\": {\"filename\": \"$out\", \"format\": \"png\"}}"
    echo "$out"
}

cmd_stop() {
    running || { echo "not running"; return; }
    vm_ssh 'shutdown /s /t 0' 2>/dev/null || qmp '{"execute": "system_powerdown"}'
    for _ in $(seq 1 60); do running || { echo "stopped"; return; }; sleep 2; done
    qmp '{"execute": "quit"}' || true
}

case "${1:-}" in
    create) cmd_create ;;
    install) cmd_install ;;
    start) cmd_start ;;
    fresh) cmd_fresh ;;
    put) shift; cmd_put "$@" ;;
    sync) cmd_sync ;;
    ssh) shift; vm_ssh "$@" ;;
    ps) shift; vm_ssh "powershell -NoProfile -NonInteractive -Command \"$*\"" ;;
    session) shift; cmd_session "$@" ;;
    screenshot) shift; cmd_screenshot "$@" ;;
    type) shift; running || die "not running"; qmp_type "$*" ;;
    key) shift; running || die "not running"; qmp_key "$1" ;;
    stop) cmd_stop ;;
    *) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
