#!/usr/bin/env bash
# pcbridge — /dev/uinput izinleri (masaustu kontrolu icin, B bolumu)
#
# NE YAPAR
#   1. `uinput` cekirdek modulunu yukler ve acilista yuklenmesini kalicilastirir
#   2. /dev/uinput'u `input` grubuna acar ve `uaccess` etiketi verir
#      (uaccess = oturumdaki kullaniciya ACL ile anlik erisim; grup uyeligi
#       oturum kapatmayi bekler, uaccess beklemez)
#   3. Kullaniciyi `input` grubuna ekler (yedek yol)
#
# NEDEN GEREKLI
#   Wayland'de harici bir surec girdi enjekte edemiyor. Tek yol cekirdek
#   seviyesinde sanal bir klavye/fare yaratmak; onun da kapisi /dev/uinput.
#
# CALISTIRMA
#   sudo ./setup_uinput.sh
#
# GERI ALMA
#   sudo rm /etc/udev/rules.d/60-pcbridge-uinput.rules /etc/modules-load.d/pcbridge-uinput.conf
#   sudo gpasswd -d "$USER" input
#   sudo udevadm control --reload-rules && sudo udevadm trigger --name-match=uinput
set -euo pipefail

# DOSYA ADINDAKI 60 KRITIK, keyfi degil. ACL'i fiilen veren satir
# /usr/lib/udev/rules.d/73-seat-late.rules icindeki
#   TAG=="uaccess", ENV{MAJOR}!="", RUN{builtin}+="uaccess"
# satiri. Kural dosyamiz 73'ten SONRA gelirse (orn. 80-...) etiket cok gec
# konur, uaccess builtin'i onu hic gormez ve ACL olusmaz -- olculdu.
RULE=/etc/udev/rules.d/60-pcbridge-uinput.rules
OLD_RULE=/etc/udev/rules.d/80-uinput.rules
MODCONF=/etc/modules-load.d/pcbridge-uinput.conf
RULE_LINE='KERNEL=="uinput", GROUP="input", MODE="0660", TAG+="uaccess"'

ok()   { printf "  \033[32m✔\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*"; }
info() { printf "  \033[2m·\033[0m %s\n" "$*"; }
head_(){ printf "\n\033[1m%s\033[0m\n" "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Bu betik root olarak calismali:  sudo $0" >&2
  exit 1
fi

TARGET_USER="${SUDO_USER:-}"
if [ -z "$TARGET_USER" ] || [ "$TARGET_USER" = "root" ]; then
  echo "SUDO_USER bos. 'sudo ./setup_uinput.sh' seklinde calistirin." >&2
  exit 1
fi

head_ "1. uinput cekirdek modulu"
if lsmod | grep -qw uinput; then
  ok "zaten yuklu"
else
  modprobe uinput
  ok "yuklendi (modprobe uinput)"
fi

if [ -f "$MODCONF" ] && grep -qx uinput "$MODCONF"; then
  ok "acilista yukleniyor ($MODCONF)"
else
  echo uinput > "$MODCONF"
  ok "acilista yuklenecek: $MODCONF"
fi

head_ "2. udev kurali"
if [ -f "$OLD_RULE" ]; then
  rm -f "$OLD_RULE"
  ok "eski (cok gec calisan) kural silindi: $OLD_RULE"
fi
if [ -f "$RULE" ] && grep -qF 'TAG+="uaccess"' "$RULE"; then
  ok "kural zaten var ($RULE)"
else
  printf '# pcbridge: sanal klavye/fare icin /dev/uinput erisimi\n%s\n' "$RULE_LINE" > "$RULE"
  ok "yazildi: $RULE"
fi
info "$RULE_LINE"

head_ "3. input grubu"
getent group input >/dev/null || groupadd -r input
if id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx input; then
  ok "$TARGET_USER zaten input grubunda"
else
  usermod -aG input "$TARGET_USER"
  ok "$TARGET_USER input grubuna eklendi (bu yol oturum kapatip acmayi ister)"
fi

head_ "4. Kurallari uygula"
udevadm control --reload-rules
# --action=add: uaccess builtin'i "remove" disindaki her eylemde calisir ama
# "add" ile tetiklemek cihaz hic hotplug olmadigi icin en guvenilir yol.
udevadm trigger --action=add --name-match=uinput || true
udevadm settle --timeout=5 || true
sleep 1
ok "udev kurallari yeniden yuklendi ve /dev/uinput tetiklendi"

head_ "5. Sonuc"
ls -l /dev/uinput | sed 's/^/  /'
ACL="$(getfacl -p /dev/uinput 2>/dev/null | grep -E "^user:${TARGET_USER}:" || true)"
if [ -n "$ACL" ]; then
  ok "uaccess ACL verildi: $ACL"
  echo
  echo "  Hazir. Oturum kapatmana GEREK YOK."
else
  warn "uaccess ACL gorunmuyor."
  echo
  echo "  Bu durumda erisim 'input' grubu uyeligine kaliyor ve o da ancak"
  echo "  OTURUM KAPATIP ACINCA gecerli olur. Cikis yapip tekrar gir, sonra:"
  echo
  echo "      getfacl -p /dev/uinput ; id -nG | tr ' ' '\\n' | grep -x input"
fi
echo
