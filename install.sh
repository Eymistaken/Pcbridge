#!/usr/bin/env bash
# pcbridge kurulumu - ZorinOS / Ubuntu tabanli sistemler icin
# Sistemle birlikte otomatik baslatma YAPMAZ. Acip kapatmak icin
# `sparkac` / `sparkkapat` alias'lari kurulur.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

blue() { printf "\033[1;34m%s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m%s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m%s\033[0m\n" "$*"; }

blue "==> 1/8  Gerekli paketler"
MISSING=()
command -v python3 >/dev/null || MISSING+=(python3)
python3 -c "import venv" 2>/dev/null || MISSING+=(python3-venv)
command -v tmux >/dev/null || MISSING+=(tmux)
command -v notify-send >/dev/null || MISSING+=(libnotify-bin)
command -v script >/dev/null || MISSING+=(bsdutils)
command -v curl >/dev/null || MISSING+=(curl)
# Masaustu kontrolu icin: pano yoluyla metin girisi ve monitor tablosu
command -v wl-copy >/dev/null || MISSING+=(wl-clipboard)
# Ekran goruntusu (screen_capture). Wayland'de disaridan yakalama yapabilen
# tek hazir arac bu; grim wlroots-only, portal her cagrida onay istiyor.
command -v gnome-screenshot >/dev/null || MISSING+=(gnome-screenshot)
# Erisilebilirlik agaci (ui_dump/ui_click/ui_set_text). SISTEM python'una
# kurulur, venv'e degil: PyGObject'i pip ile kurmak derleme bagimliliklari
# istiyor, kaldi ki AT-SPI zaten ayri bir surecte calistiriliyor.
python3 -c "import gi; gi.require_version('Atspi','2.0')" 2>/dev/null \
  || MISSING+=(python3-gi gir1.2-atspi-2.0)

if [ ${#MISSING[@]} -gt 0 ]; then
  warn "Eksik: ${MISSING[*]}"
  read -rp "Simdi kurulsun mu? [E/h] " yn
  if [[ "${yn:-e}" =~ ^([Ee]|)$ ]]; then
    sudo apt update && sudo apt install -y "${MISSING[@]}"
  fi
else
  ok "Hepsi zaten kurulu."
fi

blue "==> 2/8  Sanal ortam (.venv)"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install --upgrade pip >/dev/null
./.venv/bin/pip install -r requirements.txt >/dev/null
ok "Bagimliliklar kuruldu."

blue "==> 3/8  Native yardimci (istege bagli)"
# Ekran goruntusunun ham karesini Python yerine Rust'ta alan yardimci. ZORUNLU
# DEGIL: yoksa pcbridge Python yoluyla calismaya devam eder. Rust arac zinciri
# yalnizca DERLEMEK icin gerekir, calistirmak icin degil (docs/native/packaging.md).
NATIVE_BIN="pcbridge/_native/x86_64-unknown-linux-gnu/pcbridge-native"
if [ -x "$NATIVE_BIN" ]; then
  ok "native yardimci hazir: $("$NATIVE_BIN" --version 2>/dev/null || echo "$NATIVE_BIN")"
elif scripts/build-native.sh --check >/dev/null 2>&1; then
  read -rp "Native yardimci derlensin mi? (birkac dakika) [e/H] " yn
  if [[ "${yn:-h}" =~ ^[Ee]$ ]]; then
    scripts/build-native.sh || warn "Derlenemedi; Python yolu kullanilir."
  else
    warn "Atlandi. Sonra: scripts/build-native.sh"
  fi
else
  warn "Native yardimci yok, derleme araclari da eksik — sorun degil, Python yolu kullanilir."
  echo "    Ayrinti: scripts/build-native.sh --check"
fi
# Kurulum calisan servisi YENIDEN BASLATMAZ: yeni binary yalnizca bir sonraki
# baslatmada ve [native] capture "auto" ya da "rust" ise secilir.

blue "==> 4/8  Yapilandirma"
NEWPW=""
if [ ! -f config.toml ]; then
  cp config.example.toml config.toml
  NEWPW="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
  TK="$(python3 -c 'import secrets;print("pcb_static_"+secrets.token_urlsafe(32))')"
  python3 - "$NEWPW" "$TK" <<'PY'
import sys, pathlib
pw, tk = sys.argv[1], sys.argv[2]
p = pathlib.Path("config.toml")
t = p.read_text(encoding="utf-8")
t = t.replace('password = "BURAYA-COK-UZUN-BIR-PAROLA-YAZ"', f'password = "{pw}"')
t = t.replace('static_token = ""', f'static_token = "{tk}"')
p.write_text(t, encoding="utf-8")
PY
  chmod 600 config.toml
  ok "config.toml olusturuldu."
else
  ok "config.toml zaten var, dokunulmadi."
fi

blue "==> 5/8  Tailscale"
TS_DNS=""
if command -v tailscale >/dev/null; then
  TS_DNS="$(tailscale status --json 2>/dev/null \
    | python3 -c 'import sys,json;print(json.load(sys.stdin).get("Self",{}).get("DNSName","").rstrip("."))' 2>/dev/null || true)"
  if [ -n "$TS_DNS" ]; then
    ok "Makine adin: $TS_DNS"
    # public_url hala ornek degerse otomatik doldur
    if grep -q 'DEGISTIR' config.toml; then
      sed -i "s|^public_url = .*|public_url = \"https://$TS_DNS\"|" config.toml
      ok "public_url otomatik ayarlandi: https://$TS_DNS"
    fi
    # sudo derdini bitir
    if ! tailscale funnel status >/dev/null 2>&1; then :; fi
    warn "Tunel komutlarinin sudo istememesi icin (bir kez):"
    echo "    sudo tailscale set --operator=\$USER"
  else
    warn "Tailscale'e giris yapilmamis:  sudo tailscale up"
  fi
else
  warn "Tailscale kurulu degil:"
  echo "    curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up"
fi

blue "==> 6/8  systemd kullanici servisi (otomatik baslatma KAPALI)"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
sed "s|__PYTHON__|$DIR/.venv/bin/python|g" systemd/pcbridge.service > "$UNIT_DIR/pcbridge.service"
cp systemd/pcbridge.socket "$UNIT_DIR/pcbridge.socket"
systemctl --user daemon-reload
systemctl --user disable pcbridge >/dev/null 2>&1 || true
ok "Servis tanimlandi ama acilista baslamayacak."

# Wayland oturum ortami servise gecmeli: bildirimler, ekran kilidi kontrolu ve
# monitor tablosu bunlara bagli. Modern GNOME bunu zaten yapiyor; asagisi
# yapmayan oturumlar icin guvenlik agi ve calistirmasi zararsiz.
if [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
  systemctl --user import-environment \
    WAYLAND_DISPLAY XDG_SESSION_TYPE XDG_CURRENT_DESKTOP XDG_RUNTIME_DIR 2>/dev/null || true
  dbus-update-activation-environment --systemd --all 2>/dev/null || true
  ok "Oturum ortami systemd kullanici yoneticisine aktarildi."
fi

blue "==> 7/8  Masaustu kontrolu (klavye/fare) - OPSIYONEL"
if [ -e /dev/uinput ] && getfacl -p /dev/uinput 2>/dev/null | grep -q "^user:$USER:.*w"; then
  ok "/dev/uinput erisimi hazir."
else
  warn "Masaustu kontrolu (mouse/keyboard araclari) icin bir kez sudo gerekiyor:"
  echo
  echo "    sudo $DIR/setup_uinput.sh"
  echo
  echo "  Ne yaptigini gormek icin dosyanin basindaki aciklamayi okuyun."
  echo "  Bunu ATLAYABILIRSINIZ: pcbridge'in geri kalani (ajan, tmux, kabuk,"
  echo "  dosya) uinput olmadan da calisir."
fi
echo "  Araclar ayrica config.toml'da [desktop] enabled = true ister (varsayilan false)."

# --- yerel gorsel ajan (F bolumu) -------------------------------------------
# `computer_task` makinedeki ajani baslatiyor, ajan da bu iki kabugu Bash'ten
# cagiriyor. Servisin PATH'i ~/.local/bin ile basliyor, symlink oraya.
chmod +x bin/pcb-shot bin/pcb-do 2>/dev/null || true
mkdir -p "$HOME/.local/bin"
for tool in pcb-shot pcb-do; do
  ln -sfn "$DIR/bin/$tool" "$HOME/.local/bin/$tool"
done
ok "pcb-shot / pcb-do -> ~/.local/bin"

# Skill DEPODA duruyor (surum kontrolunde), buraya yalnizca symlink. Kullanici
# Claude Code'u elle surerken lazim; `computer_task` metni zaten dogrudan
# dosyadan okuyup prompt'a koyuyor, symlink'e bagimli DEGIL.
mkdir -p "$HOME/.claude/skills"
ln -sfn "$DIR/skills/computer-use" "$HOME/.claude/skills/computer-use"
ok "computer-use skill'i ~/.claude/skills'e baglandi."

blue "==> 8/8  Servisi acilista baslat"
chmod +x remote.sh run.sh doctor.sh connect.sh scripts/build-native.sh 2>/dev/null || true

# Servis acilista basliyor. Bu bir DAVRANIS DEGISIKLIGI: proje Gemini Spark
# icin yazilirken bilincle reddedilmisti (sunucu bosuna acik kalmasin diye).
# Artik kodlama ajanlari bu sunucuyu kullaniyor ve kullanicidan her seferinde
# bir komut beklemek anlamsiz.
#
# TUNEL BASLAMIYOR, YALNIZCA SERVIS. Servis 127.0.0.1'i dinliyor: makineyi
# internete acan sey Tailscale Funnel ve onu hala `./remote.sh start` aciyor.
systemctl --user enable pcbridge >/dev/null 2>&1 \
  && ok "pcbridge acilista basliyor (yalnizca 127.0.0.1; tunel elle acilir)" \
  || warn "systemctl --user enable pcbridge basarisiz — elle calistirin"

# Alias'lar. Adlar `spark*` DEGIL `bridge*`: eskiler Gemini Spark caginin
# kalintisiydi. Blok isaretlerle sarili, guncellemede eskisi silinip yenisi
# yaziliyor -- boylece ad degisince ~/.bashrc'de olu alias kalmiyor.
#
# UYARI: bu komutlar UZAKTAN ERISIMI yonetiyor, pcbridge'in kendisini degil.
# Kodlama ajanlari stdio kullaniyor ve sunucuyu kendileri baslatiyor;
# `bridgekapat` onlari ETKILEMEZ.
BRC="$HOME/.bashrc"
MARK_START="# >>> pcbridge >>>"
MARK_END="# <<< pcbridge <<<"

if grep -q "$MARK_START" "$BRC" 2>/dev/null; then
  python3 - "$BRC" "$MARK_START" "$MARK_END" <<'PY'
import sys, pathlib
path, a, b = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(path)
lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
out, skip = [], False
for ln in lines:
    if ln.strip() == a: skip = True; continue
    if ln.strip() == b: skip = False; continue
    if not skip: out.append(ln)
p.write_text("".join(out), encoding="utf-8")
PY
fi

cat >> "$BRC" <<EOF
$MARK_START
alias bridgeac='$DIR/remote.sh start'
alias bridgekapat='$DIR/remote.sh stop'
alias bridgedurum='$DIR/remote.sh status'
# Masaustu iznini ANINDA kapatir (acil durdurma). Servisi durdurmaz.
alias bridgekilit='$DIR/.venv/bin/python -m pcbridge.cli.lock'
$MARK_END
EOF
ok "bridgeac / bridgekapat / bridgedurum / bridgekilit eklendi"

echo
echo "------------------------------------------------------------------"
if [ -n "$NEWPW" ]; then
  warn "PAROLAN (kaydet — Gemini baglanirken bir kez soracak):"
  echo "    $NEWPW"
  echo
fi
cat <<EOF
Sirada:

  1) Istemcilere bagla:   $DIR/connect.sh --apply
  2) Her sey yolunda mi:  $DIR/doctor.sh

Kodlama ajanlari (Claude Code, Codex, Claude Desktop) sunucuyu stdio ile
kullanir: sunucuyu ISTEMCI baslatir. Ne tunel ne komut gerekiyor -- ajani
actiginda pcbridge oradadir.

  UYARI: stdio'da parola SORULMAZ. Yetki, sureci baslatabilmenin kendisi.
  Masaustu araclarinin onunde [desktop] enabled + desktop_unlock durmaya
  devam eder; shell_run / agent_run / fs_* onunde durmaz.

UZAKTAN erisim (telefon, baska makine) -- istege bagli:

      $DIR/remote.sh start    # tuneli acar, makineyi INTERNETE acar
      $DIR/remote.sh stop     # kapatir

  Uzak istemciye verilecek adres:
      $( [ -n "$TS_DNS" ] && echo "https://$TS_DNS/mcp" || echo "https://<makinen>.<tailnet>.ts.net/mcp" )
------------------------------------------------------------------
EOF
