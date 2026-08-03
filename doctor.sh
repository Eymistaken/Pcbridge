#!/usr/bin/env bash
# pcbridge tanilama: neyin calisip neyin calismadigini tek bakista gosterir.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

pass() { printf "  \033[32m✔\033[0m %s\n" "$*"; }
fail() { printf "  \033[31m✘\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*"; }
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

head_ "7. Masaustu kontrolu (klavye/fare/ekran)"
DESK_ON="$(grep -A20 '^\[desktop\]' config.toml 2>/dev/null | grep -E '^enabled' | head -1 | grep -o 'true\|false')"
case "${DESK_ON:-yok}" in
  true)  pass "[desktop] enabled = true (araclar acik)" ;;
  false) info "[desktop] enabled = false (varsayilan; mouse/keyboard araclari kapali)" ;;
  *)     fail "config.toml'da [desktop] bolumu yok — config.example.toml'dan kopyalayin" ;;
esac

if [ -e /dev/uinput ]; then
  if getfacl -p /dev/uinput 2>/dev/null | grep -q "^user:$USER:.*w"; then
    pass "/dev/uinput yazilabilir (uaccess ACL)"
  elif id -nG | tr ' ' '\n' | grep -qx input && [ -w /dev/uinput ]; then
    pass "/dev/uinput yazilabilir (input grubu)"
  else
    fail "/dev/uinput erisimi yok — calistir: sudo $DIR/setup_uinput.sh"
  fi
else
  fail "/dev/uinput yok (uinput modulu yuklu degil) — sudo $DIR/setup_uinput.sh"
fi

if ./.venv/bin/python -c "import evdev" 2>/dev/null; then
  pass "python paketi evdev kurulu"
else
  fail "evdev yok — ./.venv/bin/pip install -r requirements.txt"
fi
command -v wl-copy >/dev/null && pass "wl-copy var (pano yoluyla metin girisi)" \
  || fail "wl-clipboard yok: sudo apt install wl-clipboard"

# --- ekran goruntusu ---
command -v gnome-screenshot >/dev/null && pass "gnome-screenshot var (ekran goruntusu)" \
  || fail "gnome-screenshot yok: sudo apt install gnome-screenshot"
if ./.venv/bin/python -c "import PIL" 2>/dev/null; then
  pass "python paketi Pillow kurulu (kirpma/olcekleme)"
else
  fail "Pillow yok — ./.venv/bin/pip install -r requirements.txt"
fi
SHOTS="$(./.venv/bin/python -c 'from pcbridge.config import load_config; print(load_config().state_dir / "shots")' 2>/dev/null)"
if [ -n "$SHOTS" ] && mkdir -p "$SHOTS" 2>/dev/null && [ -w "$SHOTS" ]; then
  pass "ekran goruntusu dizini yazilabilir ($SHOTS)"
else
  fail "ekran goruntusu dizini yazilamiyor: ${SHOTS:-?}"
fi

# --- erisilebilirlik agaci (ui_dump / ui_click / ui_set_text) ---
# gi SISTEM python'unda aranir; venv'de yok ve olmasi da gerekmiyor.
if python3 -c "import gi; gi.require_version('Atspi','2.0')" 2>/dev/null; then
  pass "AT-SPI baglantilari kurulu (sistem python3'unde)"
  ATSPI="$(printf '{"cmd":"dump","target":"focused","max_nodes":1}' \
    | timeout 20 python3 "$DIR/pcbridge/desktop/atspi_helper.py" 2>/dev/null)"
  if printf '%s' "$ATSPI" | grep -q '"ok": *true'; then
    WHO="$(printf '%s' "$ATSPI" | ./.venv/bin/python -c 'import json,sys; d=json.load(sys.stdin); print(d.get("app",""), "-", d.get("window",""))' 2>/dev/null)"
    pass "erisilebilirlik agaci okunuyor · odakta: $WHO"
  else
    WHY="$(printf '%s' "$ATSPI" | ./.venv/bin/python -c 'import json,sys; print(json.load(sys.stdin).get("error","?"))' 2>/dev/null)"
    warn "odaktaki pencere okunamadi: ${WHY:-yardimci cevap vermedi}"
  fi
else
  fail "AT-SPI yok: sudo apt install python3-gi gir1.2-atspi-2.0"
fi
A11Y="$(gsettings get org.gnome.desktop.interface toolkit-accessibility 2>/dev/null)"
info "toolkit-accessibility = ${A11Y:-?} (bu makinede agac kapaliyken de dolu geliyor)"

# --- pencere listesi (window_list / computer_batch'in focus eylemi) ---
WINS="$(printf '{"cmd":"windows"}' \
  | timeout 20 python3 "$DIR/pcbridge/desktop/atspi_helper.py" 2>/dev/null)"
if printf '%s' "$WINS" | grep -q '"ok": *true'; then
  SUM="$(printf '%s' "$WINS" | ./.venv/bin/python -c '
import json, sys
d = json.load(sys.stdin)
ws = [w for w in d.get("windows", []) if w.get("window") or w.get("active")]
act = next((w for w in ws if w.get("active")), None)
tail = (" · odakta: " + act["app"]) if act else " · odakta pencere yok"
print(str(len(ws)) + " pencere" + tail)
' 2>/dev/null)"
  pass "pencere listesi okunuyor · ${SUM:-?}"
else
  warn "pencere listesi okunamadi (window_list calismayabilir)"
fi

# --- toplu eylem (computer_batch) ---
BATCH="$(./.venv/bin/python -c '
from pcbridge.config import load_config
from pcbridge.desktop import batch as b
cfg = load_config()
b.parse([{"a": "key", "keys": "a"}, {"a": "wait", "ms": 100}])
guard = "acik" if cfg.desktop.batch_check_focus else "KAPALI"
print(str(cfg.desktop.batch_max_actions) + " eylem tavani, "
      + str(cfg.desktop.batch_budget_seconds) + " sn butce, odak korumasi " + guard)
' 2>&1 | tail -1)"
if printf '%s' "$BATCH" | grep -q "butce"; then
  pass "computer_batch ayarlari · $BATCH"
  printf '%s' "$BATCH" | grep -q "KAPALI" && \
    warn "odak korumasi kapali: kor tiklama sonrasi tuslar yanlis pencereye gidebilir"
else
  fail "computer_batch kurulamadi: $BATCH"
fi

# --- yerel gorsel ajan (computer_task / pcb-shot / pcb-do) ---
for tool in pcb-shot pcb-do; do
  WHERE="$(command -v "$tool" 2>/dev/null || true)"
  if [ -n "$WHERE" ]; then
    pass "$tool PATH'te · $WHERE"
  else
    warn "$tool PATH'te yok — ajan onu Bash'ten cagiramaz. Cozum: ./install.sh"
  fi
done

# --dry-run hicbir sey calistirmaz, kapiya da varmaz: saf sozdizimi kontrolu,
# yani masaustu kapali olsa bile burada calismasi gerekir.
DRY="$(./bin/pcb-do --dry-run '[{"a":"key","keys":"Escape"},{"a":"wait","ms":50}]' 2>&1 | head -1)"
if printf '%s' "$DRY" | grep -q "2 eylem"; then
  pass "pcb-do ayristirici calisiyor · $DRY"
else
  fail "pcb-do calismadi: $DRY"
fi

SKILL_SRC="$DIR/skills/computer-use/SKILL.md"
SKILL_LINK="$HOME/.claude/skills/computer-use"
if [ -f "$SKILL_SRC" ]; then
  # computer_task metni DOGRUDAN depodan okuyor; symlink yalnizca kullanici
  # Claude Code'u elle surerken lazim, o yuzden eksikligi uyari.
  pass "computer-use yonergesi var ($(wc -l < "$SKILL_SRC") satir)"
  [ -e "$SKILL_LINK" ] || warn "~/.claude/skills/computer-use baglantisi yok (./install.sh)"
else
  fail "skills/computer-use/SKILL.md YOK — computer_task calismaz"
fi

TASKCFG="$(./.venv/bin/python -c '
from pcbridge.config import load_config
d = load_config().desktop
print(d.computer_task_agent + " / " + d.computer_task_model
      + " / " + (d.computer_task_effort or "(varsayilan)")
      + " · " + str(d.computer_task_max_steps) + " adim")
' 2>&1 | tail -1)"
if printf '%s' "$TASKCFG" | grep -q "adim"; then
  pass "computer_task ayarlari · $TASKCFG"
else
  fail "computer_task ayarlari okunamadi: $TASKCFG"
fi

# Acil durdurma: servis durunca izin dosyasi siliniyor mu (ExecStopPost).
if systemctl --user cat pcbridge 2>/dev/null | grep -q "ExecStopPost.*pcbridge.cli.lock"; then
  pass "servis durunca masaustu izni kapaniyor (ExecStopPost)"
else
  warn "ExecStopPost yok: pcbridge oldukten sonra diskte kalan izin elle calistirilan pcb-do'yu yetkilendirebilir (./install.sh)"
fi

MONS="$(./.venv/bin/python -c 'from pcbridge.desktop import monitors as m; print(m.describe())' 2>&1)"
if printf '%s' "$MONS" | grep -q '^tuval:'; then
  printf '%s\n' "$MONS" | sed 's/^/  · /'
else
  fail "monitor tablosu okunamadi: $(printf '%s' "$MONS" | tail -1)"
fi

# A bolumunun kazanimini koruyan kontrol: bu degisken --effort'u sessizce ezer.
# Yalnizca gercek Environment= satirlarina bak; birimdeki "bunu ekleme" yorumu
# kendini yakalamasin.
UNIT="$HOME/.config/systemd/user/pcbridge.service"
if grep -E '^\s*Environment=' "$UNIT" 2>/dev/null \
   | grep -qE 'ANTHROPIC_MODEL|CLAUDE_CODE_EFFORT_LEVEL'; then
  fail "servis biriminde ANTHROPIC_MODEL/CLAUDE_CODE_EFFORT_LEVEL var — --effort bayragini etkisiz kilar, kaldirin"
else
  pass "birimde model/effort ortam degiskeni yok (dogru)"
fi

head_ "8. Son 15 gunluk kaydi"
journalctl --user -u pcbridge -n 15 --no-pager 2>/dev/null | sed 's/^/  /'

echo
echo "Spark'a girilecek adres:  ${PUB:-?}${MPATH:-/mcp}"
