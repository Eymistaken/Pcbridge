#!/bin/sh
# Deprecated since pcbridge 2.0: use `pcbridge serve` instead. Kept so old
# aliases and notes keep working; it only forwards its arguments.
DIR=$(CDPATH= cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)
PCB="$DIR/.venv/bin/pcbridge"
[ -x "$PCB" ] || PCB=pcbridge
echo "note: run.sh is deprecated; use: pcbridge serve" >&2
exec "$PCB" serve "$@"
