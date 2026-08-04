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

# Nested kabuk SAHTE bir durum dosyasi okur.
#
# ONEMLI: gercek `~/.local/state/pcbridge/desktop_unlock.json`'a
# `{"until": ...}` yazmak pcbridge'e FIILEN masaustu izni vermek demek --
# `SafetyGate` ayni dosyayi okuyor. Efekti denemek icin kimseye gercek izin
# vermeye gerek yok.
: "${PCBRIDGE_GORUNUR_STATE:=${TMPDIR:-/tmp}/pcbridge-gorunur-test-state.json}"
export PCBRIDGE_GORUNUR_STATE

# Nested oturumun ARDINDA BIRAKTIGI servisleri de topla.
#
# OLCULDU 2026-08-04 (aci sekilde): gnome-shell'i oldurmek YETMIYOR.
# `dbus-run-session` ozel bir veriyolu kuruyor ve o veriyolu gvfsd,
# tracker-miner, dconf-service, at-spi, evolution... diye ~13 servis
# baslatiyor. Kabuk olunce bunlar YASAMAYA DEVAM EDIYOR.
#
# Bedeli teorik degil: 20 kadar kosumdan sonra 298 yetim surec birikti ve
# `fs.inotify.max_user_instances` (128) DOLDU. O noktada Gio.FileMonitor
# artik yeni izleyici yaratamiyor -- sessizce. Belirtisi: eklentinin durum
# izleyicisi cevap vermez oldu ve `tests/test_state.js` 23/23 iken 11/23'e
# dustu. Kodda hicbir sey degismemisti. Temizlikten sonra tekrar 23/23.
#
# Ayirt etme olcutu kesin: nested oturumlar `/tmp/dbus-*`, gercek oturum
# `/run/user/<uid>/bus` kullaniyor. Yani gercek oturumun servislerine
# dokunmak mumkun degil.
oturum_temizle() {
    local liste="" p adr
    for p in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
        # Dosyayi KABUGA DEGIL grep'e actiriyoruz. `< /proc/$p/environ`
        # yazilirsa okuma izni olmayan yuzlerce surec icin hatayi BASH
        # basiyor ve komut icindeki `2>/dev/null` onu susturmuyor.
        # `|| true` de sart: `set -e` altinda ilk basarisiz okuma betigi
        # komple dusuruyor -- belirtisi "nested hic baslamadi" oluyor.
        adr=$(grep -azm1 '^DBUS_SESSION_BUS_ADDRESS=' "/proc/$p/environ" 2>/dev/null | tr -d '\0' || true)
        case "$adr" in *"/tmp/dbus-"*) liste="$liste $p";; esac
    done
    [[ -z "${liste// }" ]] && return 0
    echo "nested oturumdan kalan $(echo $liste | wc -w) servis kapatiliyor"
    kill -TERM $liste 2>/dev/null || true
    sleep 2
    local kalan="" q
    for q in $liste; do [[ -d /proc/$q ]] && kalan="$kalan $q"; done
    [[ -n "${kalan// }" ]] && kill -KILL $kalan 2>/dev/null || true
}

inotify_durum() {
    echo "inotify ornegi: $(ls -l /proc/*/fd/* 2>/dev/null | grep -c inotify)/$(cat /proc/sys/fs/inotify/max_user_instances)"
}

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
    # Kabuk olsun olmasin: yetim servisler her zaman toplanir. Bir onceki
    # kosumdan kalmis olabilirler.
    oturum_temizle
}

case "${1:-}" in
    --oldur|--kapat)
        oldur
        inotify_durum
        exit 0
        ;;
    --temizle)
        oturum_temizle
        inotify_durum
        exit 0
        ;;
    --log)
        exec tail -f "$LOG"
        ;;
    -h|--yardim|--help)
        cat <<EOF
Kullanim: nested.sh [secenek]

  (bos)         onceki nested kabugu oldur, yenisini baslat, logu izle
  --oldur       kabugu VE ardinda kalan oturum servislerini kapat
  --temizle     yalnizca yetim servisleri topla (kabuga dokunmaz)
  --log         calisan kabugun logunu izle

Log dosyasi: $LOG
Cevre degiskenleri: MUTTER_DEBUG_NUM_DUMMY_MONITORS, MUTTER_DEBUG_DUMMY_MODE_SPECS

NOT: her nested kosumu ~13 oturum servisi (gvfsd, tracker-miner, dconf,
at-spi...) baslatiyor ve kabuk olunce bunlar YASAMAYA DEVAM EDIYOR.
Toplanmazlarsa fs.inotify.max_user_instances (128) doluyor ve o noktada
Gio.FileMonitor SESSIZCE calismaz oluyor. Bu betik her kosumda topluyor.
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
echo "log  : $LOG"
inotify_durum
echo "durum: $PCBRIDGE_GORUNUR_STATE  (SAHTE -- gercek pcbridge izni degil)"
sleep 8

echo "--- eklenti satirlari ---"
grep -E 'pcbridge-gorunur' "$LOG" || echo "(henuz cikti yok)"
echo
echo "Izni ACMAK icin:"
echo "    echo \"{\\\"until\\\": \$(( \$(date +%s) + 120 ))}\" > '$PCBRIDGE_GORUNUR_STATE'"
echo "KAPATMAK icin:"
echo "    echo '{\"until\": 0}' > '$PCBRIDGE_GORUNUR_STATE'"
