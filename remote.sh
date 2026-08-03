#!/usr/bin/env bash
# pcbridge UZAKTAN ERISIM kontrolu (Tailscale Funnel tuneli).
#   ./remote.sh start   -> sunucu + tunel acilir
#   ./remote.sh stop    -> tunel kapanir + sunucu durur
#   ./remote.sh status  -> durum ozeti
#
# BU BETIK YEREL ISTEMCILER ICIN GEREKMIYOR. Claude Code, Codex ve Claude
# Desktop stdio kullaniyor: sunucuyu istemcinin kendisi baslatiyor, ne tunel
# ne systemd servisi gerekiyor. Burasi yalnizca makineyi INTERNETE acmak icin
# -- telefondan baglanmak, ya da baska bir makinedeki ajani baglamak.
#
# systemd servisi acilista kendiliginden basliyor (yalnizca 127.0.0.1 dinler).
# TUNEL BASLAMIYOR: onu bu betik aciyor, cunku makineyi disariya o aciyor.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

g() { printf "\033[1;32m%s\033[0m\n" "$*"; }
r() { printf "\033[1;31m%s\033[0m\n" "$*"; }
y() { printf "\033[1;33m%s\033[0m\n" "$*"; }
d() { printf "\033[2m%s\033[0m\n" "$*"; }

if [ ! -f config.toml ]; then
  r "config.toml yok. Once: cd $DIR && ./install.sh"
  exit 1
fi

PORT="$(grep -E '^port' config.toml | head -1 | grep -oE '[0-9]+')"; PORT="${PORT:-8765}"
PUB="$(grep -E '^public_url' config.toml | head -1 | cut -d'"' -f2)"
MPATH="$(grep -E '^mcp_path' config.toml | head -1 | cut -d'"' -f2)"; MPATH="${MPATH:-/mcp}"

# tailscale komutunu once sudosuz dene, olmazsa sudo ile.
# Cikti hem ekrana hem dosyaya gider: tailscale bazen Funnel izni icin bir
# link basip bekliyor, onu gizlemek "sonsuza kadar asili kaldi" gibi gorunuyor.
ts() {
  local rc
  tailscale "$@" 2>&1 | tee /tmp/.pcb_ts.out
  rc=${PIPESTATUS[0]}
  if [ "$rc" -ne 0 ] && grep -qiE "access denied|permission|operator|not permitted|must be root" /tmp/.pcb_ts.out; then
    d "  (yetki gerekti, sudo ile tekrar deneniyor)"
    sudo tailscale "$@" 2>&1 | tee /tmp/.pcb_ts.out
    rc=${PIPESTATUS[0]}
  fi
  return "$rc"
}

funnel_is_open() {
  tailscale funnel status 2>/dev/null | grep -q ":$PORT"
}

# --- DIKKAT -----------------------------------------------------------------
# Kendi makinenden `curl https://<makine>.ts.net/...` yapmak ALDATICIDIR:
# MagicDNS o adi tailnet ic IP'sine (100.x) cevirir, istek tunele hic ugramaz
# ve her zaman basarili gorunur. Gercek testi yapmak icin adi HARICI bir DNS
# sunucusundan cozup --resolve ile o IP'ye gitmek gerekir.
# ----------------------------------------------------------------------------
HOSTNAME_ONLY="$(printf '%s' "$PUB" | sed -E 's#^https?://##; s#/.*##')"

public_ip_of() {
  command -v dig >/dev/null || return 1
  dig +short @8.8.8.8 "$1" A 2>/dev/null | grep -E '^[0-9]+(\.[0-9]+){3}$' | head -1
}

# Cikti: "<http_kodu>|<not>"
external_check() {
  local ip code
  ip="$(public_ip_of "$HOSTNAME_ONLY")"
  if [ -z "$ip" ]; then
    if ! command -v dig >/dev/null; then
      code="$(curl -s --max-time 20 -o /dev/null -w '%{http_code}' "$PUB/healthz")"
      printf '%s|%s' "$code" "dig yok (sudo apt install dnsutils) - bu test guvenilir degil"
      return
    fi
    printf '%s|%s' "000" "adres genel DNS'te yok - Funnel henuz yayilmamis olabilir"
    return
  fi
  case "$ip" in
    100.6[4-9].*|100.[7-9][0-9].*|100.1[0-1][0-9].*|100.12[0-7].*)
      printf '%s|%s' "000" "genel DNS tailnet ic IP'si donduruyor ($ip) - Funnel disariya yayin yapmiyor"
      return ;;
  esac
  code="$(curl -s --max-time 25 --resolve "$HOSTNAME_ONLY:443:$ip" \
          -o /dev/null -w '%{http_code}' "$PUB/healthz")"
  printf '%s|%s' "$code" "genel IP: $ip"
}

wait_health() {
  for _ in $(seq 1 25); do
    curl -sf --noproxy '*' --max-time 2 "http://127.0.0.1:$PORT/healthz" >/dev/null && return 0
    sleep 0.5
  done
  return 1
}

case "${1:-start}" in

  # ------------------------------------------------------------------ START
  start)
    echo
    g "▸ pcbridge baslatiliyor"

    if systemctl --user is-active --quiet pcbridge; then
      d "  sunucu zaten calisiyordu"
    else
      systemctl --user start pcbridge || { r "  servis baslatilamadi"; exit 1; }
      d "  sunucu baslatildi"
    fi

    if wait_health; then
      d "  yerel saglik kontrolu tamam (127.0.0.1:$PORT)"
    else
      r "  sunucu ayaga kalkmadi. Log:"
      journalctl --user -u pcbridge -n 20 --no-pager | sed 's/^/    /'
      exit 1
    fi

    if funnel_is_open; then
      d "  tunel zaten acikti"
    else
      echo "  tunel aciliyor..."
      d "    (ilk seferde HTTPS sertifikasi uretilir, 1-2 dakika surebilir;"
      d "     Funnel izni gerekiyorsa asagida bir link cikacak, onu ac)"
      if ts funnel --bg "$PORT"; then
        d "  tunel acildi"
      else
        echo
        r "  tunel acilamadi (yukaridaki mesaja bak)"
        y "  Sik cikan iki durum:"
        y "   · 'Funnel not enabled' -> mesajdaki linki tarayicida ac ve onayla"
        y "   · 'HTTPS not enabled'  -> https://login.tailscale.com/admin/dns"
        y "                             > HTTPS Certificates > Enable"
        exit 1
      fi
    fi

    printf "  disaridan kontrol (MagicDNS atlanarak)... "
    RES="$(external_check)"; CODE="${RES%%|*}"; NOTE="${RES#*|}"
    if [ "$CODE" = "200" ]; then
      echo "tamam"
      d "    $NOTE"
      echo
      g "✔ Uzaktan erisim acik"
      echo "   Adres : $PUB$MPATH"
      d  "   Kapat : ./remote.sh stop  ·  Loglar: journalctl --user -u pcbridge -f"
      echo
    else
      echo "BASARISIZ (HTTP $CODE)"
      d "    $NOTE"
      echo
      y "⚠ Sunucu yerelde calisiyor ama INTERNETTEN erisilemiyor."
      y "  Uzak istemci de tam bu yuzden 'sunucuya ulasilamadi' diyecek."
      echo
      d "  Sirasiyla dene:"
      d "   1) Funnel yeni acildiysa 2-3 dakika bekle, tekrar './remote.sh status'"
      d "   2) tailscale funnel status      -> 443'te proxy gorunuyor mu"
      d "   3) https://login.tailscale.com/admin/dns"
      d "         > HTTPS Certificates  ACIK olmali"
      d "   4) https://login.tailscale.com/admin/acls"
      d "         > nodeAttrs icinde \"funnel\" yetkisi olmali"
      d "   5) Telefonda WiFi'yi KAPAT (mobil veri) ve tarayicida ac:"
      d "         $PUB/healthz"
      echo
    fi
    ;;

  # ------------------------------------------------------------------- STOP
  stop)
    echo
    g "▸ pcbridge kapatiliyor"

    if funnel_is_open; then
      printf "  tunel kapatiliyor... "
      if ts funnel --bg off || ts funnel off || ts funnel reset; then
        echo "tamam"
      else
        echo
        y "  tunel kapatilamadi, elle dene: sudo tailscale funnel reset"
      fi
    else
      d "  tunel zaten kapaliydi"
    fi

    if systemctl --user is-active --quiet pcbridge; then
      systemctl --user stop pcbridge
      d "  sunucu durduruldu"
    else
      d "  sunucu zaten durmustu"
    fi

    RUNNING="$(ls -1 "${XDG_STATE_HOME:-$HOME/.local/state}/pcbridge/jobs" 2>/dev/null | wc -l)"
    echo
    g "✔ Kapandi. Disaridan erisim yok."
    [ "$RUNNING" -gt 0 ] && d "  (arka plandaki isler etkilenmez, kayitlar duruyor)"
    echo
    ;;

  # ----------------------------------------------------------------- STATUS
  status)
    echo
    if systemctl --user is-active --quiet pcbridge; then
      g "sunucu : calisiyor"
    else
      r "sunucu : kapali"
    fi
    if funnel_is_open; then
      g "tunel  : acik ($PORT)"
    else
      r "tunel  : kapali"
    fi
    echo   "adres  : $PUB$MPATH"
    RES="$(external_check)"; CODE="${RES%%|*}"; NOTE="${RES#*|}"
    if [ "$CODE" = "200" ]; then
      g "disari : erisilebilir"
    else
      r "disari : ERISILEMIYOR (HTTP $CODE)"
      d "         $NOTE"
    fi
    echo
    ;;

  restart)
    "$0" stop
    "$0" start
    ;;

  *)
    echo "Kullanim: $(basename "$0") {start|stop|status|restart}"
    exit 1
    ;;
esac
