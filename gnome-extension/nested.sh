#!/usr/bin/env bash
# Gelistirme dongusu: eklentiyi ic ice (nested) bir GNOME kabugunda calistir.
#
# NEDEN: GNOME 45+ ESM modullerini onbellege aliyor. `gnome-extensions
# disable/enable` JS'i YENIDEN OKUMAZ -- kabuk yeniden baslamali. Gercek
# oturumda bu cikis/giris demek (butun pencereler kapanir). Nested oturum ayni
# ise bedava yariyor: oldur, yeniden baslat, iki saniyede yeni kod.
#
# SINIRLARI (gercek oturumda mutlaka bir kez dogrula):
#   - sanal monitorler, gercek DP-1/DP-2 degil
#   - imlec bir Wayland ISTEMCISI olarak ciziliyor; donanim imlec duzlemi yok,
#     yani `set_pointer_visible` davranisi FARKLI olabilir

set -euo pipefail

BURASI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${PCBRIDGE_NESTED_LOG:-${TMPDIR:-/tmp}/pcbridge-nested.log}"
PIDF="$LOG.pid"

# Iki sanal monitor: cok monitorlu yolu nested'de de kovalayabilmek icin.
: "${MUTTER_DEBUG_NUM_DUMMY_MONITORS:=2}"
: "${MUTTER_DEBUG_DUMMY_MODE_SPECS:=960x540:960x540}"
export MUTTER_DEBUG_NUM_DUMMY_MONITORS MUTTER_DEBUG_DUMMY_MODE_SPECS

# DIKKAT: `pkill -f 'gnome-shell --nested'` KULLANMA. Desen tam komut satirina
# bakiyor, yani bu betigi calistiran kabugun kendi komut satirina da uyuyor ve
# pkill CAGIRANI olduruyor. Bir kere yasandi. Bu yuzden PID dosyasi; yedek yol
# da `^` ile bagli, boylece yalnizca gercekten `gnome-shell` olan surece uyar.
oldur() {
    local vurulan=0 pid
    if [[ -f "$PIDF" ]]; then
        pid="$(cat "$PIDF" 2>/dev/null || true)"
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null && vurulan=1
        fi
        rm -f "$PIDF"
    fi
    if pkill -f -- '^gnome-shell --nested' 2>/dev/null; then
        vurulan=1
    fi
    if (( vurulan )); then
        echo "onceki nested kabuk kapatildi"
        sleep 1
    fi
}

case "${1:-}" in
    --oldur|--kapat)
        oldur
        exit 0
        ;;
    --log)
        exec tail -f "$LOG"
        ;;
    -h|--yardim|--help)
        cat <<EOF
Kullanim: nested.sh [secenek]

  (bos)         onceki nested kabugu oldur, yenisini baslat, logu izle
  --oldur       yalnizca kapat
  --log         calisan kabugun logunu izle

Log dosyasi: $LOG
Cevre degiskenleri: MUTTER_DEBUG_NUM_DUMMY_MONITORS, MUTTER_DEBUG_DUMMY_MODE_SPECS
EOF
        exit 0
        ;;
esac

[[ -L "${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/pcbridge-gorunur@eymistaken.local" ]] \
    || echo "UYARI: eklenti kurulu gorunmuyor -- once ./install.sh"

oldur
: > "$LOG"
dbus-run-session -- gnome-shell --nested --wayland >"$LOG" 2>&1 &
echo "$!" > "$PIDF"
echo "nested kabuk basladi (pid $!) · monitor: $MUTTER_DEBUG_DUMMY_MODE_SPECS"
echo "log: $LOG"
sleep 8

echo "--- eklenti satirlari ---"
grep -E 'pcbridge-gorunur' "$LOG" || echo "(henuz cikti yok)"
