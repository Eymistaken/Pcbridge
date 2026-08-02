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

blue "==> 1/7  Gerekli paketler"
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

if [ ${#MISSING[@]} -gt 0 ]; then
  warn "Eksik: ${MISSING[*]}"
  read -rp "Simdi kurulsun mu? [E/h] " yn
  if [[ "${yn:-e}" =~ ^([Ee]|)$ ]]; then
    sudo apt update && sudo apt install -y "${MISSING[@]}"
  fi
else
  ok "Hepsi zaten kurulu."
fi

blue "==> 2/7  Sanal ortam (.venv)"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install --upgrade pip >/dev/null
./.venv/bin/pip install -r requirements.txt >/dev/null
ok "Bagimliliklar kuruldu."

blue "==> 3/7  Yapilandirma"
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

blue "==> 4/7  Tailscale"
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

blue "==> 5/7  systemd kullanici servisi (otomatik baslatma KAPALI)"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
sed "s|__DIR__|$DIR|g" systemd/pcbridge.service > "$UNIT_DIR/pcbridge.service"
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

blue "==> 6/7  Masaustu kontrolu (klavye/fare) - OPSIYONEL"
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

blue "==> 7/7  Alias'lar (~/.bashrc)"
chmod +x spark.sh run.sh doctor.sh 2>/dev/null || true
BRC="$HOME/.bashrc"
MARK_START="# >>> pcbridge >>>"
MARK_END="# <<< pcbridge <<<"

if grep -q "$MARK_START" "$BRC" 2>/dev/null; then
  # eski blogu sil, yenisini yaz (guncelleme)
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
alias sparkac='$DIR/spark.sh start'
alias sparkkapat='$DIR/spark.sh stop'
alias sparkdurum='$DIR/spark.sh status'
$MARK_END
EOF
ok "sparkac / sparkkapat / sparkdurum eklendi."

echo
echo "------------------------------------------------------------------"
if [ -n "$NEWPW" ]; then
  warn "PAROLAN (kaydet — Gemini baglanirken bir kez soracak):"
  echo "    $NEWPW"
  echo
fi
cat <<EOF
Sirada:

  1) Alias'lar aktif olsun:      source ~/.bashrc
  2) Sunucuyu ac:                sparkac
  3) Her sey yolunda mi:         $DIR/doctor.sh

  Sonra gemini.google.com > Settings & help > Connected Apps >
  "Add a custom app" alanina su adresi gir:

      $( [ -n "$TS_DNS" ] && echo "https://$TS_DNS/mcp" || echo "https://<makinen>.<tailnet>.ts.net/mcp" )

  Kapatmak icin:  sparkkapat
------------------------------------------------------------------
EOF
