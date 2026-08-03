#!/usr/bin/env bash
# pcbridge'i bir MCP istemcisine baglama komutlarini uretir.
#
# Varsayilan davranis: KOMUTLARI YAZDIRIR, calistirmaz. Kullanici okuyup
# kopyalasin diye. `--apply` verilirse kayitlari fiilen yapar.
#
# UC KAYIT DA GLOBAL: hangi dizinde calisirsan calis pcbridge gorunur.
#   claude  -> `-s user` (varsayilan `local` OLURDU ve yalnizca o projede
#              gecerli olurdu; bu tam olarak yasandi, bu yuzden acikca yaziliyor)
#   codex   -> ~/.codex/config.toml zaten global
#   desktop -> ~/.config/Claude/claude_desktop_config.json zaten global
#
# Claude Desktop yapilandirmasi JSON BIRLESTIRILEREK yaziliyor, ustune
# yazilarak degil: dosyada kullanicinin baska ayarlari duruyor. Once zaman
# damgali bir yedek aliniyor, sonra yalnizca mcpServers.pcbridge ekleniyor.
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
           Kodlama ajanlari icin (Claude Code, Codex, Claude Desktop).
           Hicbir sey acip kapatmana gerek yok.
  HTTP   — systemd servisi + Tailscale Funnel + OAuth.
           Telefondan ya da baska bir makineden baglanmak icin.
           ('./remote.sh start' ile acilir, istege bagli.)

⚠️  stdio'da KIMLIK DOGRULAMA YOK. Yetki, sureci baslatabilmenin kendisi:
    bu komutu calistirabilen her yerel program pcbridge'in butun araclarina
    erisir. Masaustu araclarinin onunde hala [desktop] enabled ve
    desktop_unlock var; kabuk/dosya/ajan araclarinin onunde YOK.
EOF

# ---------------------------------------------------------------- Claude Code
blue "1. Claude Code"
if command -v claude >/dev/null; then
  dim "surum: $(claude --version 2>/dev/null | head -1)"
  SCOPE="$(claude mcp get pcbridge 2>/dev/null | grep -i 'Scope:' | head -1)"
  if printf '%s' "$SCOPE" | grep -qi "user"; then
    ok "kayitli · her dizinde gecerli"
  elif [ -n "$SCOPE" ]; then
    # `-s user` verilmeden eklenmis: yalnizca eklendigi projede gorunur.
    warn "kayitli ama YALNIZCA bir projede geçerli (${SCOPE#*: })"
    if [ "$APPLY" = "1" ]; then
      claude mcp remove pcbridge -s local >/dev/null 2>&1
      claude mcp remove pcbridge -s project >/dev/null 2>&1
      if claude mcp add -s user pcbridge -- $STDIO_CMD >/dev/null 2>&1; then
        ok "user kapsamina tasindi — artik her dizinde"
      else
        fail "tasinamadi, elle: claude mcp add -s user pcbridge -- $STDIO_CMD"
      fi
    else
      dim "Her dizinde gecerli olmasi icin:"
      cmd "claude mcp remove pcbridge -s local"
      cmd "claude mcp add -s user pcbridge -- $STDIO_CMD"
    fi
  elif [ "$APPLY" = "1" ]; then
    if claude mcp add -s user pcbridge -- $STDIO_CMD >/dev/null 2>&1; then
      ok "kaydedildi · her dizinde gecerli"
    else
      fail "kayit basarisiz — komutu elle dene:"
      cmd "claude mcp add -s user pcbridge -- $STDIO_CMD"
    fi
  else
    dim "kayitli degil. Komut (-s user SART, yoksa yalnizca bu projede olur):"
    cmd "claude mcp add -s user pcbridge -- $STDIO_CMD"
  fi
  dim "Uzaktan (HTTP + OAuth) baglanmak istersen:"
  cmd "claude mcp add -s user --transport http pcbridge $MCP_URL"
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
  dim "Codex 2026-08-03'te olculdu: BAGLANDI, araclari cagirdi ve GORUNTU"
  dim "BLOGUNU ISLIYOR. Kanit: Chrome'da ui_dump 0 dugum donerken (AT-SPI"
  dim "orada kor) koordinatla isabetli tikladi ve surukledi -- 25 screen_capture,"
  dim "hepsi inline. Agac bosken koordinati bilmenin baska yolu yok."
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
  elif [ "$APPLY" = "1" ]; then
    # JSON BIRLESTIRILIYOR, ustune yazilmiyor: dosyada kullanicinin baska
    # ayarlari duruyor (pencere tercihleri, cowork yollari...). Once yedek,
    # sonra ekleme, sonra "eski anahtarlarin hepsi duruyor mu" kontrolu.
    "$PY" - "$PY" "$CD_CFG" <<'PYEOF'
import json, pathlib, shutil, sys, datetime
py, cfg = sys.argv[1], pathlib.Path(sys.argv[2])
backup = cfg.with_suffix(f".json.yedek-{datetime.datetime.now():%Y%m%d-%H%M%S}")
shutil.copy2(cfg, backup)
try:
    data = json.loads(cfg.read_text(encoding="utf-8"))
except ValueError as exc:
    print(f"  BOZUK JSON, dokunulmadi: {exc}")
    sys.exit(1)
before = sorted(data)
data.setdefault("mcpServers", {})["pcbridge"] = {
    "command": py, "args": ["-m", "pcbridge.server", "--stdio"],
}
cfg.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
after = json.loads(cfg.read_text(encoding="utf-8"))
missing = [k for k in before if k not in after]
if missing:
    shutil.copy2(backup, cfg)
    print(f"  ANAHTAR KAYBI ({missing}) — yedekten geri alindi.")
    sys.exit(1)
print(f"  yedek: {backup.name}")
PYEOF
    if [ $? -eq 0 ]; then
      ok "eklendi (mevcut ayarlar korundu)"
      warn "Claude Desktop'i TAMAMEN kapatip yeniden ac — yoksa gormez."
    else
      fail "eklenemedi; dosyaya elle bak: edit $CD_CFG"
    fi
  else
    dim "Dosyaya BU parcayi ekle (varsa mevcut mcpServers'in icine):"
    echo
    "$PY" - "$PY" <<'PYEOF'
import json, sys
print(json.dumps({"mcpServers": {"pcbridge": {
    "command": sys.argv[1], "args": ["-m", "pcbridge.server", "--stdio"],
}}}, indent=2))
PYEOF
    echo
    dim "Duzenlemek icin:  edit $CD_CFG"
    dim "Ya da `./connect.sh --apply` yedekleyip birlestirerek ekler."
    dim "Sonra Claude Desktop'i TAMAMEN kapatip yeniden ac."
  fi
else
  warn "Claude Desktop yapilandirmasi yok ($CD_CFG) — kurulu mu?"
fi

# ------------------------------------------------------- uzaktan erisim
blue "4. Uzaktan erisim (HTTP + OAuth) — istege bagli"
dim "Yerel istemciler icin GEREKMIYOR. Bu yol telefondan baglanmak ya da"
dim "baska bir makinedeki ajani baglamak icin."
echo
dim "Adres: $MCP_URL"
cmd "./remote.sh start     # tuneli acar (makineyi INTERNETE acar)"
cmd "./remote.sh stop      # kapatir"
dim "Servis acilista kendiliginden basliyor ama TUNEL BASLAMIYOR: servis"
dim "yalnizca 127.0.0.1'i dinler, disariya acan sey tunel."

echo
if [ "$APPLY" = "0" ]; then
  printf "\033[2mKomutlari bu betige yaptirmak icin: ./connect.sh --apply\033[0m\n"
fi
printf "\033[2mDurum kontrolu: ./doctor.sh\033[0m\n"
echo
