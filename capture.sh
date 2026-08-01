#!/usr/bin/env bash
# Gemini baglanti denemesi sirasinda her seyi tek dosyaya kaydeder.
#
#   ./capture.sh          -> kaydi baslatir
#   (Gemini'de denemeyi yap)
#   Ctrl+C                -> kaydi bitirir ve ozet cikarir
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/pcbridge"
OUT="$HOME/pcbridge-kayit-$(date +%Y%m%d-%H%M%S).log"

g() { printf "\033[1;32m%s\033[0m\n" "$*"; }
y() { printf "\033[1;33m%s\033[0m\n" "$*"; }
d() { printf "\033[2m%s\033[0m\n" "$*"; }

PIDS=()
cleanup() {
  for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null; done
  wait 2>/dev/null

  echo
  g "▸ Kayit bitti: $OUT"
  echo

  y "── Ozet ─────────────────────────────────────────────"
  step() { grep -qE "$2" "$OUT" && printf "  \033[32m✔\033[0m %s\n" "$1" || printf "  \033[31m✘\033[0m %s\n" "$1"; }
  step "kesif: protected-resource metadata cekildi" '/\.well-known/oauth-protected-resource'
  step "kesif: authorization-server metadata cekildi" '/\.well-known/oauth-authorization-server'
  step "istemci kaydi (DCR)"                          'POST /register'
  step "yetkilendirme sayfasi acildi"                 'GET /authorize'
  step "parola onaylandi"                             'consent_granted'
  step "yetki kodu uretildi"                          'code_issued'
  step "TOKEN DEGISIMI (kritik adim)"                 'POST /token'
  step "MCP oturumu acildi"                           'POST /mcp HTTP/1.1" 200'
  echo

  if grep -q 'POST /token' "$OUT"; then
    g "  Token istegi GELDI. Sorun token adiminda ya da sonrasinda."
    grep -E 'POST /token' "$OUT" | tail -3 | sed 's/^/    /'
  elif grep -q 'code_issued' "$OUT"; then
    y "  Kod uretildi ama token istegi HIC GELMEDI."
    d  "  -> Kopma karsi tarafta (Gemini), bizim sunucuda degil."
    echo
    d  "  Son uretilen yonlendirme adresi:"
    grep 'code_issued' "$OUT" | tail -1 \
      | grep -oE 'https://[^"]+' | head -1 | sed 's/^/    /'
  else
    y "  Kod bile uretilmemis. Akis daha erken kopmus."
  fi
  echo
  d "Bu dosyayi paylasabilirsin: $OUT"
  exit 0
}
trap cleanup INT TERM

mkdir -p "$STATE"
touch "$STATE/audit.log"

echo
g "▸ Kayit basladi"
d "  dosya: $OUT"
echo
y "  SIMDI Gemini'de baglanti denemesini yap."
y "  Bitince buraya donup Ctrl+C bas."
echo
d "  ── canli akis ──"

{
  journalctl --user -u pcbridge -f -n 0 --output=short-iso 2>/dev/null | sed 's/^/HTTP  /' &
  PIDS+=($!)
  tail -f -n 0 "$STATE/audit.log" 2>/dev/null | sed 's/^/AUDIT /' &
  PIDS+=($!)
  wait
} | tee "$OUT"
