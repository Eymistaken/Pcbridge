#!/usr/bin/env bash
# pcbridge'i bir MCP istemcisine baglama komutlarini uretir.
#
# Varsayilan davranis: KOMUTLARI YAZDIRIR, calistirmaz. Kullanici okuyup
# kopyalasin diye. `--apply` verilirse claude/codex kayitlarini fiilen yapar.
#
# Claude Desktop hicbir zaman otomatik yazilmaz: yapilandirma dosyasi
# kullanicinin baska sunucularini da tasiyor ve bir betigin JSON'u yeniden
# yazmasi onlari kaybettirebilir. Oraya eklenecek parca basiliyor, o kadar.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

blue() { printf "\n\033[1;34m%s\033[0m\n" "$*"; }
ok()   { printf "  \033[32m✔\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*"; }
fail() { printf "  \033[31m✘\033[0m %s\n" "$*"; }
dim()  { printf "  \033[2m%s\033[0m\n" "$*"; }
cmd()  { printf "      \033[1m%s\033[0m\n" "$*"; }

PY="$DIR/.venv/bin/python"
if [ ! -x "$PY" ]; then
  fail "$PY yok — once ./install.sh calistir."
  exit 1
fi

MCP_URL="$(grep -E '^public_url' config.toml 2>/dev/null | head -1 | cut -d'"' -f2)"
MPATH="$(grep -E '^mcp_path' config.toml 2>/dev/null | head -1 | cut -d'"' -f2)"
MCP_URL="${MCP_URL:-https://DEGISTIR.ts.net}${MPATH:-/mcp}"

STDIO_CMD="$PY -m pcbridge.server --stdio"

cat <<EOF

pcbridge iki yoldan baglanir:

  stdio  — sunucuyu ISTEMCI baslatir, ag yok, OAuth yok.
           Yerel istemciler icin (Claude Code, Codex, Claude Desktop).
  HTTP   — systemd servisi + Tailscale Funnel + OAuth.
           Uzaktan erisim ve Gemini Spark icin. ('sparkac' ile acilir.)

⚠️  stdio'da KIMLIK DOGRULAMA YOK. Yetki, sureci baslatabilmenin kendisi:
    bu komutu calistirabilen her yerel program pcbridge'in butun araclarina
    erisir. Masaustu araclarinin onunde hala [desktop] enabled ve
    desktop_unlock var; kabuk/dosya/ajan araclarinin onunde YOK.
EOF

# ---------------------------------------------------------------- Claude Code
blue "1. Claude Code"
if command -v claude >/dev/null; then
  dim "surum: $(claude --version 2>/dev/null | head -1)"
  if claude mcp list 2>/dev/null | grep -q "^pcbridge"; then
    ok "zaten kayitli"
  elif [ "$APPLY" = "1" ]; then
    if claude mcp add pcbridge -- $STDIO_CMD >/dev/null 2>&1; then
      ok "kaydedildi"
    else
      fail "kayit basarisiz — komutu elle dene:"
      cmd "claude mcp add pcbridge -- $STDIO_CMD"
    fi
  else
    dim "kayitli degil. Komut:"
    cmd "claude mcp add pcbridge -- $STDIO_CMD"
  fi
  dim "Uzaktan (HTTP + OAuth) baglanmak istersen:"
  cmd "claude mcp add --transport http pcbridge $MCP_URL"
else
  warn "claude PATH'te yok"
fi

# ------------------------------------------------------------------ Codex CLI
blue "2. Codex CLI"
if command -v codex >/dev/null; then
  dim "surum: $(codex --version 2>/dev/null | head -1)"
  if codex mcp list 2>/dev/null | grep -q "^pcbridge"; then
    ok "yapilandirmada kayitli"
  elif [ "$APPLY" = "1" ]; then
    if codex mcp add pcbridge -- $STDIO_CMD >/dev/null 2>&1; then
      ok "yapilandirmaya yazildi"
    else
      fail "kayit basarisiz — komutu elle dene:"
      cmd "codex mcp add pcbridge -- $STDIO_CMD"
    fi
  else
    dim "kayitli degil. Komut:"
    cmd "codex mcp add pcbridge -- $STDIO_CMD"
  fi
  dim "Uzaktan (HTTP + OAuth):"
  cmd "codex mcp add pcbridge --url $MCP_URL"
  cmd "codex mcp login pcbridge"
  echo
  warn "Codex tarafi BU MAKINEDE DENENMEDI. Yukaridaki komutlar yapilandirma"
  dim "dosyasina dogru yaziyor (codex mcp get pcbridge ile dogrulanabilir), ama"
  dim "gercek bir oturum acilip araclarin geldigi ve GORUNTU BLOGUNUN islendigi"
  dim "olculemedi — bu makinede Codex aboneligi yok. Deneyen sonucu bildirsin."
else
  warn "codex PATH'te yok"
fi

# -------------------------------------------------------------- Claude Desktop
blue "3. Claude Desktop"
CD_CFG="$HOME/.config/Claude/claude_desktop_config.json"
if [ -f "$CD_CFG" ]; then
  ok "yapilandirma dosyasi: $CD_CFG"
  if grep -q '"pcbridge"' "$CD_CFG" 2>/dev/null; then
    ok "zaten kayitli"
  else
    dim "Dosyaya BU parcayi ekle (varsa mevcut mcpServers'in icine):"
    echo
    "$PY" - "$PY" "$DIR" <<'PYEOF'
import json, sys
py, d = sys.argv[1], sys.argv[2]
print(json.dumps({"mcpServers": {"pcbridge": {
    "command": py, "args": ["-m", "pcbridge.server", "--stdio"], "cwd": d,
}}}, indent=2))
PYEOF
    echo
    dim "Duzenlemek icin:  edit $CD_CFG"
    dim "Sonra Claude Desktop'i tamamen kapatip yeniden ac."
    warn "Dosyayi bu betik YAZMIYOR: icinde baska ayarlarin var, ustune yazmak"
    dim "onlari kaybettirebilir."
  fi
else
  warn "Claude Desktop yapilandirmasi yok ($CD_CFG) — kurulu mu?"
fi

# ------------------------------------------------------------------ Spark
blue "4. Gemini Spark (HTTP)"
dim "Adres: $MCP_URL"
dim "Sunucuyu ac ('sparkac'), sonra gemini.google.com > Settings & help >"
dim "Connected Apps > Add a custom app."
dim "Spark goruntu blogu ISLEYEMIYOR; [server] inline_images = \"auto\" bu yuzden"
dim "HTTP'de goruntuyu kapali tutuyor. Kurcalamana gerek yok."

echo
if [ "$APPLY" = "0" ]; then
  printf "\033[2mKomutlari bu betige yaptirmak icin: ./connect.sh --apply\033[0m\n"
fi
printf "\033[2mDurum kontrolu: ./doctor.sh\033[0m\n"
echo
