#!/bin/sh
# Install or update pcbridge for this user, then hand over to `pcbridge setup`.
#
#   curl -fsSL https://github.com/Eymistaken/Pcbridge/releases/latest/download/install.sh | sh
#   sh install.sh [--yes] [--version vX.Y.Z | --wheel FILE] [setup options]
#   sh install.sh --uninstall [--purge] [--yes]
#   ./install.sh                 (from a git checkout: installs the checkout)
#
# From a release: downloads the wheel, checks its sha256 against the
# release's sha256sums.txt, installs it into ~/.local/share/pcbridge/venv,
# then runs `pcbridge setup`, which detects the machine, shows the plan and
# asks once. From a git checkout: installs the checkout into its own .venv
# (editable), builds the native helper when Rust is there, then the same.
#
# Needs Python 3.12 or newer; with an older one it offers a pcbridge-only
# Python (downloaded with uv into ~/.local/share/pcbridge, no root) or the
# distribution's own package. Never run as root: everything is per user, and
# setup asks for sudo itself, printing each command first.
#
# Run it again to update or repair. Options it does not know go to
# `pcbridge setup` (see `pcbridge setup --help`).
set -eu

REPO=Eymistaken/Pcbridge
DATA=${XDG_DATA_HOME:-$HOME/.local/share}/pcbridge
VENV=$DATA/venv
# A uv release at least two weeks old; PCBRIDGE_UV_VERSION overrides it.
UV_VERSION=${PCBRIDGE_UV_VERSION:-0.12.21}
UV_PYTHON=3.12

say() { printf '%s\n' "$*"; }
warn() { printf 'pcbridge install: %s\n' "$*" >&2; }
die() { warn "$*"; exit 1; }

usage() {
    sed -n '2,22p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//' || true
    say ""
    say "Options: --yes  --version vX.Y.Z  --wheel FILE  --python PATH  --uninstall [--purge]"
}

# -- options (the rest goes to `pcbridge setup`) ------------------------------

YES=0 WHEEL="" VERSION="" UNINSTALL=0 PURGE=0
n=$#
while [ "$n" -gt 0 ]; do
    a=$1; shift; n=$((n - 1))
    case $a in
        -y|--yes) YES=1; set -- "$@" --yes ;;
        --wheel|--version|--python)
            [ "$n" -gt 0 ] || die "$a needs a value"
            v=$1; shift; n=$((n - 1))
            case $a in --wheel) WHEEL=$v ;; --version) VERSION=$v ;; --python) PYTHON=$v ;; esac ;;
        --wheel=*) WHEEL=${a#*=} ;;
        --version=*) VERSION=${a#*=} ;;
        --python=*) PYTHON=${a#*=} ;;
        --uninstall) UNINSTALL=1 ;;
        --purge) PURGE=1 ;;
        -h|--help) usage; exit 0 ;;
        *) set -- "$@" "$a" ;;
    esac
done

[ "$(id -u)" -ne 0 ] || die "do not run this as root (or with sudo): pcbridge installs for your own user and asks for sudo itself when a package is needed."

# Piped from curl, stdin is this script: questions go to the terminal.
if (: </dev/tty) 2>/dev/null; then TTY=1; else TTY=0; fi
ask() {  # ask PROMPT DEFAULT -> REPLY
    if [ "$YES" = 1 ] || [ "$TTY" = 0 ]; then REPLY=$2; return; fi
    printf '%s' "$1" >/dev/tty
    read -r REPLY </dev/tty || REPLY=""
    [ -n "$REPLY" ] || REPLY=$2
}

# The directory of this script, when it is a file (not `curl | sh`).
HERE=""
case $0 in */*) [ -f "$0" ] && HERE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) ;; esac
[ -n "$HERE" ] || { [ -f "./$0" ] && HERE=$(pwd); } || true
CHECKOUT=0
[ -n "$HERE" ] && [ -f "$HERE/pyproject.toml" ] && [ -d "$HERE/pcbridge" ] && CHECKOUT=1

# -- uninstall ---------------------------------------------------------------

remove_dir() {  # to the trash when there is one; these are our own files
    [ -e "$1" ] || return 0
    if command -v gio >/dev/null 2>&1 && gio trash "$1" 2>/dev/null; then
        say "moved to the trash: $1"
    else
        rm -rf -- "$1" && say "removed: $1"
    fi
}

if [ "$UNINSTALL" = 1 ]; then
    if [ "$CHECKOUT" = 1 ] && [ -x "$HERE/.venv/bin/pcbridge" ]; then
        PCB=$HERE/.venv/bin/pcbridge
    elif [ -x "$VENV/bin/pcbridge" ]; then
        PCB=$VENV/bin/pcbridge
    else
        PCB=$(command -v pcbridge || true)
    fi
    [ -n "$PCB" ] || die "pcbridge is not installed for this user."
    set --
    [ "$YES" = 1 ] && set -- --yes
    [ "$PURGE" = 1 ] && set -- "$@" --purge
    if [ "$TTY" = 1 ]; then "$PCB" uninstall "$@" </dev/tty; else "$PCB" uninstall "$@"; fi
    # --purge already moved $DATA (venv and Python included) to the trash.
    for d in "$VENV" "$DATA/python" "$DATA/uv"; do remove_dir "$d"; done
    [ "$CHECKOUT" = 1 ] && say "The checkout's .venv is left in place: $HERE/.venv"
    exit 0
fi

# -- Python ------------------------------------------------------------------

py_ok() { "$1" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; }
py_venv() { "$1" -c 'import venv, ensurepip' 2>/dev/null; }

os_field() { sed -n "s/^$1=//p" /etc/os-release 2>/dev/null | head -n 1 | tr -d '"'; }
OS_ID=$(os_field ID)
OS_LIKE=$(os_field ID_LIKE)
OS_NAME=$(os_field NAME)
OS_VERSION=$(os_field VERSION_ID)
is_like() { case " $OS_ID $OS_LIKE " in *" $1 "*) return 0 ;; esac; return 1; }

download() {  # URL FILE
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --proto '=https' --tlsv1.2 --retry 2 -o "$2" "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -q --https-only -O "$2" "$1"
    else
        die "neither curl nor wget is installed"
    fi
}

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT INT TERM

check_sha256() {  # DIR SUMS_LINE: the file named in the line, in DIR, must match
    command -v sha256sum >/dev/null 2>&1 || die "sha256sum is missing (coreutils)"
    (cd "$1" && printf '%s\n' "$2" | sha256sum -c --status -) || die "checksum mismatch: $(printf '%s' "$2" | awk '{print $2}')"
}

uv_python() {
    case $(uname -m) in
        x86_64|amd64) t=x86_64-unknown-linux-gnu ;;
        aarch64|arm64) t=aarch64-unknown-linux-gnu ;;
        *) die "no uv build for $(uname -m); install Python 3.12 or newer yourself" ;;
    esac
    url=https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$t.tar.gz
    say "Downloading uv $UV_VERSION (to fetch Python $UV_PYTHON for pcbridge only)..."
    download "$url" "$TMP/uv-$t.tar.gz"
    download "$url.sha256" "$TMP/uv.sha256"
    check_sha256 "$TMP" "$(awk '{print $1 "  " $2}' "$TMP/uv.sha256" | sed 's/\*//')"
    tar -xzf "$TMP/uv-$t.tar.gz" -C "$TMP"
    mkdir -p "$DATA/uv"
    cp "$TMP/uv-$t/uv" "$DATA/uv/uv"
    # Everything stays under $DATA: no ~/.local/bin/python3.12, no shell
    # profile change, nothing for the system.
    UV_PYTHON_INSTALL_DIR=$DATA/python UV_CACHE_DIR=$DATA/uv/cache UV_NO_CONFIG=1 \
        "$DATA/uv/uv" python install --no-bin "$UV_PYTHON"
    PY=$(UV_PYTHON_INSTALL_DIR=$DATA/python UV_NO_CONFIG=1 \
        "$DATA/uv/uv" python find --managed-python --no-config "$UV_PYTHON")
    rm -rf "$DATA/uv/cache"
    py_ok "$PY" || die "the downloaded Python does not run: $PY"
}

system_python_command() {  # sets SYS_CMD (shown and run) and SYS_PY, or leaves them empty
    SYS_CMD="" SYS_PY=""
    if is_like ubuntu; then
        SYS_CMD="sudo apt-get install -y software-properties-common && sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt-get update && sudo apt-get install -y python3.12 python3.12-venv"
        SYS_PY=python3.12
    elif is_like fedora; then
        SYS_CMD="sudo dnf install -y python3.13"
        SYS_PY=python3.13
    fi
}

find_python() {
    if [ -n "${PYTHON:-}" ]; then
        py_ok "$PYTHON" || die "$PYTHON is not Python 3.12 or newer"
        PY=$PYTHON; return 0
    fi
    for c in python3 python3.14 python3.13 python3.12; do
        p=$(command -v "$c" 2>/dev/null) || continue
        if py_ok "$p" && py_venv "$p"; then PY=$p; return 0; fi
    done
    for p in "$DATA"/python/cpython-3.*/bin/python3; do
        if [ -x "$p" ] && py_ok "$p"; then PY=$p; return 0; fi
    done
    return 1
}

offer_python() {
    have=$(python3 -c 'import platform; print(platform.python_version())' 2>/dev/null || echo none)
    if command -v python3 >/dev/null 2>&1 && py_ok python3; then
        say "Python $have is here, but its venv module is missing."
        if is_like debian; then
            SYS_CMD="sudo apt-get install -y python3-venv"; SYS_PY=python3
        else
            SYS_CMD="" SYS_PY=""
        fi
    else
        say "pcbridge needs Python 3.12 or newer; this system has Python $have."
        system_python_command
    fi
    say "  1) Download a Python for pcbridge only, with uv, into $DATA"
    say "     (no root; the system and its python3 are not changed)  [default]"
    if [ -n "$SYS_CMD" ]; then
        say "  2) Install one system-wide:"
        say "       $SYS_CMD"
    elif is_like debian; then
        say "  2) $OS_NAME $OS_VERSION has no official package for it: use 1, or upgrade"
        say "     the distribution (Debian 13 and Pardus 25 have Python 3.13)."
    else
        say "  2) No known package for $OS_NAME: use 1, or install Python 3.12+ yourself."
    fi
    say "  3) Quit"
    ask "Choice [1]: " 1
    case $REPLY in
        1) uv_python ;;
        2) [ -n "$SYS_CMD" ] || die "no system-wide command for this distribution; run again and pick 1"
           say "\$ $SYS_CMD"
           sh -c "$SYS_CMD" </dev/tty || die "the system-wide install failed"
           PY=$(command -v "$SYS_PY") || die "$SYS_PY is still not there"
           py_venv "$PY" || die "$PY has no venv module" ;;
        *) say "Nothing was changed."; exit 1 ;;
    esac
}

find_python || offer_python
say "Python: $PY ($("$PY" -c 'import platform; print(platform.python_version())'))"

# -- pcbridge into a venv -------------------------------------------------------

make_venv() {  # DIR: a venv on a Python 3.12+ that still exists
    if [ -x "$1/bin/python" ] && ! py_ok "$1/bin/python"; then
        say "The venv at $1 has no working Python 3.12+ any more; making it again."
        rm -rf -- "$1"
    fi
    [ -x "$1/bin/python" ] || "$PY" -m venv "$1"
    "$1/bin/python" -m pip install -q --upgrade pip
}

pip_install() {  # VENV CONSTRAINTS TARGET [-e]: TARGET, then TARGET[desktop]
    c=""
    [ -n "$2" ] && c="-c $2"
    # shellcheck disable=SC2086
    "$1/bin/python" -m pip install -q $c ${4:-} "$3"
    # evdev (the [desktop] extra) is built from source and needs a C compiler;
    # without one pcbridge still works, and `pcbridge setup` offers the parts
    # when this desktop can be driven.
    # shellcheck disable=SC2086
    if ! "$1/bin/python" -m pip install -q $c ${4:-} "$3[desktop]" 2>"$TMP/pip.err"; then
        warn "the desktop extras (evdev, Pillow) were not installed: evdev is built from source and needs a C compiler and the kernel headers. Only desktop control uses them; pcbridge setup installs what they need where it can work."
    fi
}

if [ "$CHECKOUT" = 1 ] && [ -z "$WHEEL" ] && [ -z "$VERSION" ]; then
    say "Installing the git checkout $HERE (editable)"
    make_venv "$HERE/.venv"
    pip_install "$HERE/.venv" "$HERE/packaging/constraints.txt" "$HERE" -e
    if [ ! -x "$HERE/pcbridge/_native/x86_64-unknown-linux-gnu/pcbridge-native" ]; then
        if command -v cargo >/dev/null 2>&1; then
            say "Building the native helper (faster capture, input and accessibility)..."
            "$HERE/scripts/build-native.sh" || warn "native helper build failed; pcbridge uses the Python paths instead."
        else
            say "note: no Rust toolchain; pcbridge uses the Python paths (install Rust and run scripts/build-native.sh for the native helper)."
        fi
    fi
    PCB=$HERE/.venv/bin/pcbridge
else
    CONSTRAINTS=""
    if [ -n "$WHEEL" ]; then
        [ -f "$WHEEL" ] || die "no such wheel: $WHEEL"
        WHEEL=$(CDPATH='' cd -- "$(dirname -- "$WHEEL")" && pwd)/$(basename -- "$WHEEL")
        dir=$(dirname -- "$WHEEL")
        if [ -f "$dir/sha256sums.txt" ]; then
            line=$(grep " \*\{0,1\}$(basename -- "$WHEEL")\$" "$dir/sha256sums.txt" || true)
            [ -n "$line" ] && { check_sha256 "$dir" "$line"; say "sha256 ok: $(basename -- "$WHEEL")"; }
        else
            say "note: no sha256sums.txt next to the wheel; its checksum is not checked"
        fi
        for c in "$dir/constraints.txt" "${HERE:-/nonexistent}/packaging/constraints.txt" "${HERE:-/nonexistent}/constraints.txt"; do
            [ -f "$c" ] && { CONSTRAINTS=$c; break; }
        done
    else
        case $(uname -m) in
            x86_64|amd64) ARCH=x86_64 ;;
            *) die "there is no release wheel for $(uname -m) yet; install from a git checkout instead" ;;
        esac
        if [ -z "$VERSION" ]; then
            download "https://api.github.com/repos/$REPO/releases/latest" "$TMP/latest.json"
            VERSION=$(sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' "$TMP/latest.json" | head -n 1)
            [ -n "$VERSION" ] || die "could not read the latest release from GitHub"
        fi
        BASE=https://github.com/$REPO/releases/download/$VERSION
        say "Downloading pcbridge $VERSION"
        download "$BASE/sha256sums.txt" "$TMP/sha256sums.txt"
        line=$(grep "pcbridge-[0-9][^ ]*-py3-none-linux_$ARCH\.whl\$" "$TMP/sha256sums.txt" | head -n 1)
        [ -n "$line" ] || die "release $VERSION has no wheel for $ARCH"
        name=$(printf '%s' "$line" | awk '{print $2}' | sed 's/^\*//')
        download "$BASE/$name" "$TMP/$name"
        check_sha256 "$TMP" "$line"
        say "sha256 ok: $name"
        WHEEL=$TMP/$name
        cline=$(grep " \*\{0,1\}constraints\.txt\$" "$TMP/sha256sums.txt" || true)
        if [ -n "$cline" ]; then
            download "$BASE/constraints.txt" "$TMP/constraints.txt"
            check_sha256 "$TMP" "$cline"
            CONSTRAINTS=$TMP/constraints.txt
        else
            say "note: release $VERSION has no constraints.txt; the newest compatible dependencies are used"
        fi
    fi
    mkdir -p "$DATA"
    # Kept for `pcbridge setup`, which may add the desktop extras later.
    if [ -n "$CONSTRAINTS" ]; then cp "$CONSTRAINTS" "$DATA/constraints.txt"; fi
    say "Installing into $VENV"
    make_venv "$VENV"
    c=""
    [ -n "$CONSTRAINTS" ] && c="-c $DATA/constraints.txt"
    # shellcheck disable=SC2086
    "$VENV/bin/python" -m pip install -q $c --force-reinstall --no-deps "$WHEEL"
    pip_install "$VENV" "${CONSTRAINTS:+$DATA/constraints.txt}" "$WHEEL"
    PCB=$VENV/bin/pcbridge
fi

say ""
if [ "$TTY" = 1 ]; then
    exec "$PCB" setup "$@" </dev/tty
fi
exec "$PCB" setup "$@"
