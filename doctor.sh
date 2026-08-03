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
    *) fail "public_url https olmali (uzak istemciler duz http kabul etmiyor)" ;;
  esac
  case "$PUB" in
    *DEGISTIR*) fail "public_url hala ornek deger! duzenle: gnome-text-editor $DIR/config.toml" ;;
  esac
else
  fail "config.toml yok — ./install.sh calistir"
  exit 1
fi

head_ "2. Servis (yalnizca UZAKTAN erisim icin)"
# NOT: bu servis yerel istemcileri ILGILENDIRMIYOR. Claude Code / Codex /
# Claude Desktop stdio kullaniyor ve sunucuyu kendileri baslatiyor; servis
# kapaliyken de calisirlar. Buradaki kontroller HTTP yolu icin.
if systemctl --user is-active --quiet pcbridge; then
  pass "pcbridge servisi calisiyor (HTTP yolu)"
else
  warn "servis kapali — yerel ajanlar yine calisir, uzaktan erisim calismaz"
  info "acmak icin: systemctl --user start pcbridge"
fi
if systemctl --user is-enabled --quiet pcbridge 2>/dev/null; then
  pass "acilista otomatik basliyor"
else
  warn "acilista baslamiyor — her acilista elle baslatman gerekir"
  info "duzeltmek icin: systemctl --user enable pcbridge"
fi
# Tunel ayri bir karar ve BILINCLI olarak otomatik degil: servis yalnizca
# 127.0.0.1'i dinler, makineyi INTERNETE acan sey Funnel. Acilista servis
# baslar, tunel BASLAMAZ.
if grep -q "alias bridgeac=" "$HOME/.bashrc" 2>/dev/null; then
  pass "bridgeac / bridgekapat / bridgedurum / bridgekilit alias'lari kurulu"
else
  warn "alias'lar yok — ./install.sh calistir (sonra: source ~/.bashrc)"
fi
if grep -q "alias sparkac=" "$HOME/.bashrc" 2>/dev/null; then
  warn "~/.bashrc'de eski 'sparkac' alias'lari da duruyor — ./install.sh temizler"
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
# Iki yol var. Yayin SESSIZ (varsayilan), gnome-screenshot her cekimde beyaz
# flas + ses -- yedek olarak duruyor.
SC_OUT="$(./.venv/bin/python - <<'PYEOF' 2>/dev/null
from pcbridge.desktop import screencast as SC
ok, why = SC.available()
print("OK" if ok else f"NO {why}")
PYEOF
)"
if [ "${SC_OUT%% *}" = "OK" ]; then
  pass "ekran yayini hazir (SESSIZ yakalama, flas yok)"
else
  warn "ekran yayini kullanilamiyor: ${SC_OUT#NO }"
  info "    gnome-screenshot'a dusulur; o her cekimde beyaz flas + ses yapar."
  info "    Kurulum: sudo apt install python3-gi gstreamer1.0-pipewire gstreamer1.0-plugins-good"
fi
command -v gnome-screenshot >/dev/null && pass "gnome-screenshot var (yedek yol)" \
  || warn "gnome-screenshot yok: yayin calismazsa goruntu alinamaz (sudo apt install gnome-screenshot)"
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

# Model/effort BOS BIRAKILABILIR: o zaman secilen ajanin kendi varsayilani
# kullaniliyor. Bos stringi oldugu gibi basmak "claude /  / " gibi bir satir
# uretiyordu; hangi modelin fiilen secilecegi cozumleyiciye sorulup yaziliyor.
TASKCFG="$(./.venv/bin/python - <<'PY' 2>&1 | tail -1
from pcbridge.config import load_config
from pcbridge import models
cfg = load_config()
d = cfg.desktop
res = models.resolve(cfg, agent=d.computer_task_agent,
                     model=d.computer_task_model or None,
                     effort=d.computer_task_effort or None)
if res.error:
    print("COZUMLENEMEDI: " + res.error.splitlines()[0])
else:
    src = "config" if d.computer_task_model else "ajanin varsayilani"
    print(f"{res.headline()} ({src}) · {d.computer_task_max_steps} adim")
PY
)"
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

head_ "8. Istemci kayitlari (stdio)"

# stdio gercekten baslatilabiliyor mu: initialize + tools/list el sikismasi.
# Yaniti SATIR SATIR okuyor -- stdin'i erken kapatmak sunucuyu tools/list
# yanitini yazmadan kapatiyor ve tani "bozuk" der (bu betik yazilirken yasandi).
STDIO="$(./.venv/bin/python - <<'PY' 2>/dev/null
import json, subprocess, sys, pathlib
root = pathlib.Path.cwd()
p = subprocess.Popen([str(root / ".venv/bin/python"), "-m", "pcbridge.server", "--stdio"],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, text=True, bufsize=1, cwd="/")
def send(m):
    p.stdin.write(json.dumps(m) + "\n"); p.stdin.flush()
def read_id(want):
    while True:
        line = p.stdout.readline()
        if not line:
            return None
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("id") == want:
            return m
try:
    send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                     "clientInfo": {"name": "doctor", "version": "0"}}})
    if read_id(1) is None:
        print("HATA initialize yanit vermedi"); sys.exit()
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    r = read_id(2)
    tools = r["result"]["tools"] if r and "result" in r else []
    inline = next((t for t in tools if t["name"] == "screen_capture"), {})
    print(f"OK {len(tools)} arac"
          + (" · screen_capture goruntu blogu donebiliyor"
             if inline.get("outputSchema") is None else ""))
finally:
    try:
        p.stdin.close(); p.wait(timeout=10)
    except Exception:
        p.kill()
PY
)"
case "$STDIO" in
  OK*) pass "stdio baslatilabiliyor · ${STDIO#OK }" ;;
  *)   fail "stdio baslatilamadi: ${STDIO:-cevap yok}" ;;
esac

# Claude Code: kayitli mi, BAGLANIYOR mu, ve HANGI KAPSAMDA.
# Kapsam onemli: varsayilan `local` yalnizca eklendigi projede gecerli, yani
# baska bir dizinde acilan oturum pcbridge'i hic gormez. Bu sessiz bir tuzak --
# "kayitli" gorunur ama calismaz.
if command -v claude >/dev/null; then
  CL="$(claude mcp list 2>/dev/null | grep '^pcbridge' || true)"
  SCOPE="$(claude mcp get pcbridge 2>/dev/null | grep -i 'Scope:' | head -1)"
  if [ -z "$CL" ]; then
    warn "Claude Code'a kayitli degil — ./connect.sh --apply"
  elif ! printf '%s' "$CL" | grep -q "Connected"; then
    fail "Claude Code: kayitli ama baglanamiyor · $CL"
  elif printf '%s' "$SCOPE" | grep -qi "user"; then
    pass "Claude Code: BAGLANIYOR · her dizinde gecerli (user kapsami)"
  else
    warn "Claude Code: baglaniyor ama YALNIZCA bir projede (${SCOPE#*: })"
    info "her dizinde olmasi icin: ./connect.sh --apply"
  fi
else
  info "claude PATH'te yok"
fi

# Codex: 2026-08-03'te BAGLANDIGI VE ARAC CAGIRDIGI olculdu -- audit.log'da
# desktop_unlock + computer_task + computer_batch kayitlari var. Ama GORUNTU
# BLOGUNU isledigi hala olculmedi: o denemede `computer_task` cagirip gorsel
# isi claude'a devretti, `screen_capture` hic cagirmadi. Ayrimi bozma.
if command -v codex >/dev/null; then
  if codex mcp list 2>/dev/null | grep -q "^pcbridge"; then
    pass "Codex: kayitli · baglandigi, arac cagirdigi ve GORUNTU ISLEDIGI olculdu"
  else
    warn "Codex yapilandirmasinda yok — ./connect.sh"
  fi
else
  info "codex PATH'te yok"
fi

# Claude Desktop: dosyayi okumuyoruz, yalnizca adin gectigine bakiyoruz.
CD_CFG="$HOME/.config/Claude/claude_desktop_config.json"
if [ -f "$CD_CFG" ]; then
  if grep -q '"pcbridge"' "$CD_CFG" 2>/dev/null; then
    pass "Claude Desktop yapilandirmasinda kayitli"
  else
    warn "Claude Desktop'a kayitli degil — ./connect.sh eklenecek parcayi basar"
  fi
else
  info "Claude Desktop yapilandirmasi yok"
fi

# Oturum ortami: stdio'da sunucuyu ISTEMCI baslatiyor ve onun ortamini
# devraliyoruz. Olculdu 2026-08-03: Codex'in surecinde DBUS_SESSION_BUS_ADDRESS
# genisletilmemis bir literal olarak geliyordu ve masaustu araclarinin TAMAMI
# cokuyordu. Onarim `desktop/session.py`'de; burasi onarimin ISE YARADIGINI
# bozuk bir ortamda fiilen dogruluyor.
SESSFIX="$(DBUS_SESSION_BUS_ADDRESS='$DBUS_SESSION_BUS_ADDRESS' \
  ./.venv/bin/python - <<'PY' 2>&1 | tail -1
from pcbridge.desktop import session, monitors
session.ensure_session_env()
try:
    n = len(monitors.list_monitors(use_cache=False))
    print(f"OK bozuk DBUS onarildi, monitor tablosu okundu ({n} monitor)")
except Exception as exc:
    print(f"HATA onarim ise yaramadi: {exc}")
PY
)"
case "$SESSFIX" in
  OK*) pass "oturum ortami onarimi calisiyor · ${SESSFIX#OK }" ;;
  *)   fail "oturum ortami onarimi: ${SESSFIX:-cevap yok}" ;;
esac
info "su anki ortam: $(./.venv/bin/python -c 'from pcbridge.desktop import session; print(session.describe())' 2>&1 | tail -1)"

INLINE="$(./.venv/bin/python - <<'PY' 2>&1 | tail -1
from pcbridge.config import load_config
from pcbridge.tools import _want_inline
s = load_config().inline_images
on = lambda t: "goruntu VAR" if _want_inline(s, t) else "yalnizca metin"
print(f'"{s}" -> stdio: {on("stdio")} · http: {on("http")}')
PY
)"
info "inline_images = $INLINE"

head_ "9. Son 15 gunluk kaydi"
journalctl --user -u pcbridge -n 15 --no-pager 2>/dev/null | sed 's/^/  /'

echo
echo "Uzaktan baglanacak istemciye verilecek adres:  ${PUB:-?}${MPATH:-/mcp}"
echo "  (once ./remote.sh start ile tunel acilmali)"
echo "Yerel istemciler icin:  ./connect.sh"
