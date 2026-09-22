#!/bin/sh
# Deprecated since pcbridge 2.0: use `pcbridge remote` instead. Kept so old
# aliases and notes keep working; it only forwards its arguments.
DIR=$(CDPATH= cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)
PCB="$DIR/.venv/bin/pcbridge"
[ -x "$PCB" ] || PCB=pcbridge
echo "note: remote.sh is deprecated; use: pcbridge remote" >&2
exec "$PCB" remote "$@"
