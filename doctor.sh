#!/usr/bin/env bash
# pcbridge tanilama: neyin calisip neyin calismadigini tek bakista gosterir.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

pass() { printf "  \033[32m✔\033[0m %s\n" "$*"; }
fail() { printf "  \033[31m✘\033[0m %s\n" "$*"; }
info() { printf "  \033[2m·\033[0m %s\n" "$*"; }
head_() { printf "\n\033[1m%s\033[0m\n" "$*"; }

head_ "1. Yapilandirma"
if [ -f config.toml ]; then
  pass "config.toml var"
  PERM="$(stat -c %a config.toml)"
  [ "$PERM" = "600" ] && pass "izinler 600" || fail "izinler $PERM (duzelt: chmod 600 config.toml)"
  PUB="$(grep -E '^public_url' config.toml | head -1 | cut -d'"' -f2)"
  PORT="$(grep -E '^port' config.toml | head -1 | grep -oE '[0-9]+')"
  MPATH="$(grep -E '^mcp_path' config.toml | head -1 | cut -d'"' -f2)"
  PORT="${PORT:-8765}"; MPATH="${MPATH:-/mcp}"
  info "public_url = $PUB"
  case "$PUB" in
    https://*) pass "https kullaniliyor" ;;
    *) fail "public_url https olmali (Spark http kabul etmiyor)" ;;
  esac
  case "$PUB" in
    *DEGISTIR*) fail "public_url hala ornek deger! duzenle: gnome-text-editor $DIR/config.toml" ;;
  esac
else
  fail "config.toml yok — ./install.sh calistir"
  exit 1
fi

head_ "2. Servis"
if systemctl --user is-active --quiet pcbridge; then
  pass "pcbridge servisi calisiyor"
else
  fail "servis calismiyor — 'sparkac' yaz"
fi
if systemctl --user is-enabled --quiet pcbridge 2>/dev/null; then
  info "acilista otomatik basliyor (kapatmak icin: systemctl --user disable pcbridge)"
else
  pass "acilista otomatik baslamiyor (istenen davranis)"
fi
if grep -q "alias sparkac=" "$HOME/.bashrc" 2>/dev/null; then
  pass "sparkac / sparkkapat alias'lari kurulu"
else
  fail "alias'lar yok — ./install.sh calistir"
fi

head_ "3. Yerel erisim"
LOCAL="http://127.0.0.1:$PORT"
if curl -sf --noproxy '*' --max-time 5 "$LOCAL/healthz" >/dev/null; then
  pass "$LOCAL/healthz cevap veriyor"
  info "$(curl -s --noproxy '*' "$LOCAL/healthz")"
else
  fail "$LOCAL/healthz cevap vermiyor — journalctl --user -u pcbridge -n 50"
fi

head_ "4. Tailscale / tunel"
if command -v tailscale >/dev/null; then
  pass "tailscale kurulu"
  DNS="$(tailscale status --json 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin).get("Self",{}).get("DNSName","").rstrip("."))' 2>/dev/null)"
  [ -n "$DNS" ] && info "makine adi: $DNS" || fail "tailscale'e giris yapilmamis: sudo tailscale up"
  if tailscale funnel status 2>/dev/null | grep -q "$PORT"; then
    pass "funnel $PORT portuna acik"
  else
    fail "funnel kapali: sudo tailscale funnel --bg $PORT"
    info "$(tailscale funnel status 2>&1 | head -5)"
  fi
else
  fail "tailscale kurulu degil"
fi

head_ "5. Disaridan erisim (MagicDNS atlanarak)"
# Kendi makinenden duz curl ALDATICI: MagicDNS adi 100.x'e cevirir ve istek
# tunele hic ugramaz. Bu yuzden adi harici DNS'ten cozup --resolve kullaniyoruz.
HOST_ONLY="$(printf '%s' "${PUB:-}" | sed -E 's#^https?://##; s#/.*##')"
PUBIP=""
if command -v dig >/dev/null; then
  PUBIP="$(dig +short @8.8.8.8 "$HOST_ONLY" A 2>/dev/null | grep -E '^[0-9]+(\.[0-9]+){3}$' | head -1)"
else
  fail "dig yok, disari testi guvenilir degil: sudo apt install dnsutils"
fi

if [ -z "$PUBIP" ] && command -v dig >/dev/null; then
  fail "$HOST_ONLY genel DNS'te (8.8.8.8) cozulemiyor"
  info "Funnel yayini yok ya da henuz yayilmadi. 2-3 dk bekle, tekrar dene."
elif [ -n "$PUBIP" ]; then
  case "$PUBIP" in
    100.6[4-9].*|100.[7-9][0-9].*|100.1[0-1][0-9].*|100.12[0-7].*)
      fail "genel DNS tailnet ic IP donduruyor ($PUBIP) - Funnel disariya acik degil" ;;
    *)
      pass "genel DNS -> $PUBIP"
      CURL=(curl -s --max-time 20 --resolve "$HOST_ONLY:443:$PUBIP")
      C="$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$PUB/healthz")"
      [ "$C" = "200" ] && pass "/healthz -> 200" || fail "/healthz -> $C"

      for p in "/.well-known/oauth-protected-resource$MPATH" "/.well-known/oauth-authorization-server"; do
        C="$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$PUB$p")"
        [ "$C" = "200" ] && pass "$p -> 200" || fail "$p -> $C"
      done

      C="$("${CURL[@]}" -o /dev/null -w '%{http_code}' -X POST "$PUB$MPATH" \
            -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
            -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}')"
      [ "$C" = "401" ] && pass "token'siz $MPATH -> 401 (dogru)" || fail "token'siz $MPATH -> $C" ;;
  esac
fi

head_ "6. Ajanlar"
for a in claude agy; do
  W="$(bash -lc "command -v $a" 2>/dev/null)"
  if [ -n "$W" ]; then
    pass "$a -> $W"
  elif [ "$a" = "agy" ]; then
    fail "agy (Antigravity CLI) PATH'te yok — kurulu degilse config.toml'da"
    info "    [agents.antigravity] altinda enabled = false yap"
  else
    fail "$a PATH'te yok"
  fi
done
command -v tmux >/dev/null && pass "tmux var" || fail "tmux yok: sudo apt install tmux"
command -v script >/dev/null && pass "script var (agy pty sarmalayicisi icin)" || fail "script yok: sudo apt install bsdutils"

head_ "7. Son 15 gunluk kaydi"
journalctl --user -u pcbridge -n 15 --no-pager 2>/dev/null | sed 's/^/  /'

echo
echo "Spark'a girilecek adres:  ${PUB:-?}${MPATH:-/mcp}"
