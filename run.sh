#!/usr/bin/env bash
# pcbridge'i on planda calistir (gelistirme / hata ayiklama icin).
# Servis olarak calistirmak icin:  systemctl --user start pcbridge
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ ! -x .venv/bin/python ]; then
  echo "Once ./install.sh calistir." >&2
  exit 1
fi

exec ./.venv/bin/python -m pcbridge.server "$@"
