#!/bin/sh
# User-level install of a pcbridge wheel, no root needed:
#
#   packaging/install-user.sh pcbridge-2.0.0-py3-none-linux_x86_64.whl [setup options]
#
# The wheel goes into its own virtual environment under
# $XDG_DATA_HOME/pcbridge/venv (~/.local/share/pcbridge/venv), with the
# dependency versions it was tested with (constraints.txt next to this
# script), then `pcbridge setup` links ~/.local/bin/pcbridge, installs the
# user units, the GNOME extension, the client registrations and the aliases.
# Run it again with a newer wheel to update; the daemon restarts itself once
# no job is running.
set -eu
WHEEL=${1:?usage: install-user.sh WHEEL [pcbridge setup options]}
shift
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DATA=${XDG_DATA_HOME:-$HOME/.local/share}/pcbridge
VENV=$DATA/venv
PY=${PYTHON:-python3}

"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 12))' || { echo "pcbridge needs Python 3.12 or newer." >&2; exit 1; }
mkdir -p "$DATA"
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
CONSTRAINTS=""
[ -f "$HERE/constraints.txt" ] && CONSTRAINTS="-c $HERE/constraints.txt"
# shellcheck disable=SC2086
"$VENV/bin/pip" install -q $CONSTRAINTS --force-reinstall --no-deps "$WHEEL"
# shellcheck disable=SC2086
"$VENV/bin/pip" install -q $CONSTRAINTS "$WHEEL[desktop]"
exec "$VENV/bin/pcbridge" setup "$@"
