#!/usr/bin/env bash
# pcbridge gorunur eklentisi: kur / kaldir / durum.
#
# Kurulum SYMLINK ile yapilir -- depoda duzenledigin dosya dogrudan calisan
# eklentidir, kopyalama adimi yok. Bozuk bir eklenti Wayland'de kabugu
# dusurebilecegi icin acil geri alma yolu her calismada ekrana basilir.

set -euo pipefail

UUID="pcbridge-gorunur@eymistaken.local"
BURASI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KAYNAK="$BURASI/$UUID"
HEDEF_DIZIN="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions"
HEDEF="$HEDEF_DIZIN/$UUID"

kirmizi() { printf '\033[31m%s\033[0m\n' "$*"; }
yesil()   { printf '\033[32m%s\033[0m\n' "$*"; }
sari()    { printf '\033[33m%s\033[0m\n' "$*"; }

# gsettings'teki `enabled-extensions` listesine UUID ekler/cikarir.
# `gnome-extensions enable` calisan kabuga D-Bus'tan soruyor ve eklentiyi
# heniz taramamis bir kabukta HATA veriyor; gsettings her durumda calisiyor.
liste_duzenle() {
    local eylem="$1"
    python3 - "$eylem" "$UUID" <<'PY'
import ast, subprocess, sys

eylem, uuid = sys.argv[1], sys.argv[2]
anahtar = ["gsettings", "get", "org.gnome.shell", "enabled-extensions"]
ham = subprocess.run(anahtar, capture_output=True, text=True, check=True).stdout.strip()
# Bos liste `@as []` olarak basiliyor.
if ham.startswith("@as "):
    ham = ham[4:]
liste = list(ast.literal_eval(ham))

if eylem == "ekle" and uuid not in liste:
    liste.append(uuid)
elif eylem == "cikar":
    liste = [x for x in liste if x != uuid]
else:
    print("degisiklik yok")
    sys.exit(0)

deger = "[" + ", ".join(f"'{x}'" for x in liste) + "]"
subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", deger], check=True)
print(f"enabled-extensions guncellendi ({len(liste)} eklenti)")
PY
}

geri_alma_yolu() {
    echo
    sari "─── ACIL GERI ALMA ───────────────────────────────────────────────"
    kirmizi "ONCE BUNU CALISTIR — ANINDA etki eder:"
    echo
    echo "    gnome-extensions disable $UUID"
    echo
    echo "Sonra kalicilastir:"
    echo
    echo "    $BURASI/install.sh --kaldir"
    echo
    sari "DIKKAT: `rm` TEK BASINA YETMEZ."
    echo "Diskteki dosyayi silmek CALISAN eklentiyi durdurmuyor; kabuk onu"
    echo "zaten bellege almis oluyor. Etkisi ancak kabuk yeniden baslayinca"
    echo "(cikis/giris ya da yeniden baslatma) goruluyor. Bir kere yasandi:"
    echo "kullanici `rm` yazdi, hicbir sey degismedi, makineyi restart etti."
    echo
    echo "Kabuk tamamen kilitliyse Ctrl+Alt+F3 ile TTY'ye gecip yukaridaki"
    echo "`gnome-extensions disable` komutunu oradan calistir."
    sari "──────────────────────────────────────────────────────────────────"
}

durum() {
    echo "UUID    : $UUID"
    echo "kaynak  : $KAYNAK"
    echo "hedef   : $HEDEF"
    if [[ -L "$HEDEF" ]]; then
        yesil "kurulu  : evet (symlink -> $(readlink "$HEDEF"))"
    elif [[ -e "$HEDEF" ]]; then
        kirmizi "kurulu  : hedefte symlink DEGIL gercek bir dizin var -- elle bak"
    else
        echo "kurulu  : hayir"
    fi
    local etkin
    etkin="$(gsettings get org.gnome.shell enabled-extensions 2>/dev/null || echo '?')"
    if [[ "$etkin" == *"$UUID"* ]]; then
        yesil "etkin   : evet"
    else
        echo "etkin   : hayir"
    fi
    echo "kabuk   : $(gnome-shell --version 2>/dev/null || echo '?')"
}

kur() {
    [[ -d "$KAYNAK" ]] || { kirmizi "kaynak dizin yok: $KAYNAK"; exit 1; }
    [[ -f "$KAYNAK/metadata.json" ]] || { kirmizi "metadata.json yok"; exit 1; }

    if [[ -e "$HEDEF" && ! -L "$HEDEF" ]]; then
        kirmizi "$HEDEF symlink degil, gercek bir dizin. Ustune yazmiyorum;"
        kirmizi "once kendin bak ve tasi/sil."
        exit 1
    fi

    mkdir -p "$HEDEF_DIZIN"
    ln -sfn "$KAYNAK" "$HEDEF"
    yesil "symlink kuruldu: $HEDEF -> $KAYNAK"

    if [[ "${1:-}" != "--yalniz-baglanti" ]]; then
        liste_duzenle ekle
        yesil "eklenti etkinlestirildi"
    else
        echo "etkinlestirme atlandi (--yalniz-baglanti)"
    fi

    echo
    sari "GNOME 45+ ESM modullerini onbellege aliyor: kod degisikligi ve ilk"
    sari "kurulum icin KABUGUN YENIDEN BASLAMASI gerekiyor. Wayland'de bu"
    sari "cikis/giris demek. Denemek icin ayri bir oturum:"
    echo
    echo "    dbus-run-session -- gnome-shell --nested --wayland"
    geri_alma_yolu
}

kaldir() {
    liste_duzenle cikar || true
    if [[ -L "$HEDEF" ]]; then
        rm "$HEDEF"
        yesil "symlink silindi: $HEDEF"
    elif [[ -e "$HEDEF" ]]; then
        kirmizi "$HEDEF symlink degil; elle sil."
    else
        echo "zaten kurulu degil"
    fi
    echo "Kabuk yeniden baslayinca (cikis/giris) eklenti tamamen gider."
}

case "${1:-}" in
    ""|--kur)            kur ;;
    --yalniz-baglanti)   kur --yalniz-baglanti ;;
    --kaldir)            kaldir ;;
    --durum)             durum ;;
    -h|--yardim|--help)
        cat <<EOF
Kullanim: install.sh [secenek]

  (bos) | --kur        symlink kur + etkinlestir + geri alma yolunu yazdir
  --yalniz-baglanti    yalnizca symlink kur, etkinlestirme
  --kaldir             etkinligi kaldir + symlink'i sil
  --durum              kurulu mu, etkin mi, kabuk surumu
EOF
        ;;
    *) kirmizi "bilinmeyen secenek: $1"; exit 1 ;;
esac
