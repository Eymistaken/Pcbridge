"""Uygulama baslatma ve pencere one alma.

PENCERE ONE ALMA NEDEN BOYLE
    OLCULDU 2026-08-02, uc yol denendi:

      1. `org.gnome.Shell.Introspect.GetWindows()` -> "Access denied" (GNOME 46)
      2. AT-SPI `Component.grab_focus()` -> GTK'da `atspi_error`, Electron'da
         `False`. Hicbir pencerede calismadi.
      3. D-Bus `org.freedesktop.Application.Activate` -> `exit=0` donuyor ama
         pencere ONE GELMIYOR. Sessiz basarisizlik, iki kez dogrulandi.

    GNOME kabuk eklentisi kuruluysa dar `ActivateWindow` D-Bus yuzu dogrudan
    `Meta.Window.activate()` kullanir. Yoksa ya da hedefi bulamazsa mevcut
    GNOME aramasi (`super` + ad + `Return`) aynen yedek olarak kalir.

DORT IC ISLEM (Task 6.4)
    `resolve_application` adi kurulu bir uygulamaya cevirir, `launch_application`
    onu `gtk-launch` ile acar ve penceresini gorene kadar bekler,
    `activate_window` eklentiye sorar, `observe_focus` odagi AT-SPI'dan okur.
    `window_focus`, `computer_task(app=...)` ve toplu `focus` eylemi ucu de
    `bring_to_front` uzerinden ayni sirayi izler:

      1. Eklenti acik pencereyi etkinlestirir (tus yok, olculdu 3-6 ms).
      2. Hedef zaten odaktaysa HICBIR tus gonderilmez.
      3. Kapali bir uygulama `gtk-launch` ile acilir (tus yok). Olculdu
         2026-09-19: arka plandaki bir surecin baslattigi pencere 0,56 sn'de
         listede goruldu, 0,68 sn'de odagi aldi.
      4. Acik ama eklentinin one alamadigi pencere icin GNOME aramasi: acik
         bir `degraded` yedek, klavye ister.

    Arama kutusu uygulamadan fazlasini bulur: bu makinede Claude sohbetleri,
    dosyalar, ucbirim sekmeleri, ayarlar ve web aramasi da oraya dusuyor. Bu
    yuzden aramaya YALNIZCA kurulu bir uygulamanin adi yazilir; baska bir ad
    tus gonderilmeden reddedilir. Sonuc her zaman AT-SPI'dan, o uygulamanin
    kimligiyle dogrulanir (bkz. `_shows`); tutmazsa `Escape` ile toparlanip
    hata donulur. Baslik tek basina yetmez: web aramasi, basligi tam da aranan
    metin olan bir tarayici sekmesi aciyor.

    NOT: overview acikken Wayland panosu bloklaniyor (`wl-paste` 5 sn'de cevap
    vermedi), bu yuzden arama kutusuna HAM tus yoluyla yaziliyor. Uygulama
    adlari ASCII oldugu icin `tr+intl` duzeni sorun cikarmiyor.
"""

from __future__ import annotations

import gettext
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .errors import DesktopError, ErrorCategory, ErrorCode

SEARCH_SETTLE = 1.2      # overview acilmasi
SEARCH_RESULTS = 1.5     # arama sonuclarinin gelmesi
SEARCH_ACTIVATE = 3.0    # uygulamanin one gelmesi
# Aramanin butun maliyeti: olculdu 6616-6935 ms (2026-09-02), ustune klavye
# cihazi henuz acik degilse ~1,3 sn. Bir son tarih varsa arama bu kadar sure
# kalmadan BASLAMAZ.
SEARCH_COST = 8.0
# `gtk-launch` sonrasi pencereyi bekleme. Olculdu 2026-09-19: kucuk bir GTK4
# penceresi 0,56 sn'de listede, 0,68 sn'de odakta. Buyuk uygulamalar daha
# yavas; bekleme ust siniri bu.
LAUNCH_OBSERVE = 10.0
LAUNCH_POLL = 0.1
# Pencere listede goruldukten sonra odagi almasi icin taninan sure (olculdu:
# ~0,12 sn sonra aldi).
LAUNCH_FOCUS_GRACE = 1.0
# Baslatmanin anlamli olmasi icin kalmasi gereken en az sure.
LAUNCH_COST = 1.5

_FOCUS_BUS_NAME = "io.github.eymistaken.Pcbridge.WindowFocus"
_FOCUS_OBJECT_PATH = "/io/github/eymistaken/Pcbridge/WindowFocus"
_FOCUS_INTERFACE = "io.github.eymistaken.Pcbridge.WindowFocus"


class AppError(RuntimeError):
    """Uygulama baslatilamadi ya da pencere one alinamadi."""


class NoTimeLeft(AppError):
    """Yavas adima (baslatma, arama) yetecek sure kalmadi. HICBIR SEY yapilmadi.

    Yalnizca geri alinamaz bir adimdan ONCE atilir; toplu eylem motoru bunu
    butcenin bitmesi sayar ve eylemi yapilmamislara koyar.
    """


def _busctl_bool(*args: str) -> bool:
    """Sinirli bir ``busctl call`` yanitini guvenli bir bool'a cevir."""
    try:
        proc = subprocess.run(
            [
                "busctl",
                "--user",
                "--timeout=500ms",
                "call",
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and proc.stdout.split() == ["b", "true"]


def extension_focus_available() -> bool:
    """Dar pencere odak servisinin bu oturumda bir sahibi var mi?"""
    return _busctl_bool(
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "NameHasOwner",
        "s",
        _FOCUS_BUS_NAME,
    )


def extension_focused_window() -> tuple[str, str] | None:
    """Odaktaki pencere, GNOME kabuk eklentisinden: (uygulama, baslik).

    ODAK ICIN IKINCI KAYNAK (Adim 8.1). AT-SPI erisilebilirlige katilmayan
    bir pencere ondeyken hicbir pencereyi ACTIVE isaretlemiyor. OLCULDU
    2026-09-21: Minecraft (SDL3, native Wayland) ondeyken tiklama iceren her
    `computer_batch` odak okunamadigi icin HIC eylem gondermeden reddedildi.
    Kompozitor odagi her zaman biliyor; eklentinin `FocusedWindow` yontemi
    onu, yalnizca izin acikken, soyluyor.

    None: eklenti yok, eski surum (yontem yok -- kod degisikligi kabuk
    yeniden baslayana kadar yuklenmiyor), izin kapali ya da odakta pencere
    yok (overview, bos masaustu). Hepsinde cagiran bugunku davranisina
    doner; tahmin yok.

    Uygulama adi `.desktop` kimligi varsa o, yoksa `wm_class`. AT-SPI'in
    verdigi addan FARKLI olabilir; ayni dizi icinde iki kaynagin karismasi
    "odak degisti" sayilir, yani yanlis yonde degil guvenli yonde hata.
    """
    try:
        proc = subprocess.run(
            [
                "busctl", "--user", "--json=short", "--timeout=500ms", "call",
                _FOCUS_BUS_NAME, _FOCUS_OBJECT_PATH, _FOCUS_INTERFACE,
                "FocusedWindow",
            ],
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        reply = json.loads(proc.stdout)
        found, wm_class, app_id, title = reply["data"]
    except (ValueError, KeyError, TypeError):
        return None
    if reply.get("type") != "bsss" or found is not True:
        return None
    app = str(app_id or wm_class or "").strip() or "?"
    return app, str(title or "")


def _extension_activate(window: str) -> bool:
    """Kurulu GNOME eklentisinden acik pencereyi etkinlestirmesini iste.

    Servis yoksa, grant kapaliysa veya hedef acik degilse False doner. Bu
    ayrim bilerek hata degildir: cagiran mevcut GNOME aramasina duser.
    """
    return _busctl_bool(
        _FOCUS_BUS_NAME,
        _FOCUS_OBJECT_PATH,
        _FOCUS_INTERFACE,
        "ActivateWindow",
        "s",
        window,
    )


def activate_window(window: str) -> bool:
    """Acik pencereyi eklentiyle one al. False: yapilmadi, tus da gonderilmedi.

    Eklenti adi kendisi eslestiriyor (tek, belirsiz olmayan pencere) ve True'yu
    kabugun odak penceresiyle dogruluyor. Ikinci bir AT-SPI turu hem gereksiz
    hem nested GNOME'da yanlis.
    """
    return _extension_activate(window)


@dataclass(frozen=True)
class Entry:
    entry_id: str            # "org.gnome.TextEditor" (.desktop uzantisiz)
    name: str                # "Text Editor" -- kullaniciya gosterilen
    no_display: bool
    # Asil adlar (Name + yerellestirmeleri). Eslesmede once bunlara bakilir.
    names: tuple[str, ...] = ()
    # GenericName gibi ikincil adlar. AYRI TUTULUYOR: VS Code'un GenericName'i
    # "Text Editor" ve bir arada arandiginda gercek Metin Duzenleyici'yi
    # geciyordu.
    alt_names: tuple[str, ...] = ()
    # `Exec=` satirindaki gercek ikilinin dosya adi ("code",
    # "gnome-text-editor"). Kabuk komutunu .desktop girdisine baglayan tek
    # guvenilir alan bu: `code.desktop`in Exec'i `/usr/share/code/code`, yani
    # ne girdi kimligiyle ne de gorunen adiyla ayni. Cok genel adlar
    # (`flatpak`, `sh`, `bash`) BOS birakiliyor -- `_exec_binary`ye bakin.
    exec_name: str = ""


def _dirs() -> list[Path]:
    out = []
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    for base in [data_home, *dirs.split(":")]:
        p = Path(base) / "applications"
        if p.is_dir():
            out.append(p)
    for extra in ("/var/lib/flatpak/exports/share/applications",
                  str(Path.home() / ".local/share/flatpak/exports/share/applications")):
        p = Path(extra)
        if p.is_dir() and p not in out:
            out.append(p)
    return out


_TRANSLATORS: dict[str, "gettext.NullTranslations"] = {}


def _translate(domain: str, text: str) -> str:
    """`Name=` degerini sistem diline cevir.

    Cevirilerin cogu .desktop dosyasinda DEGIL, gettext katalogunda: bu
    makinede org.gnome.TextEditor.desktop yalnizca `Name=Text Editor` diyor,
    `Metin Duzenleyici` ise gnome-text-editor.mo'dan geliyor. Kullanici Spark'a
    Turkce soyledigi icin bu cevirileri okumak sart.
    """
    if not domain or not text:
        return ""
    tr = _TRANSLATORS.get(domain)
    if tr is None:
        try:
            tr = gettext.translation(domain, fallback=True)
        except Exception:
            tr = gettext.NullTranslations()
        _TRANSLATORS[domain] = tr
    try:
        out = tr.gettext(text)
    except Exception:
        return ""
    return out if out != text else ""


# `Exec=` satirindaki alan kodlari (uygulama degil, XDG yer tutucusu).
_FIELD_CODES = {"@@", "@@u", "@@U"}
# Komutun basinda durup asil ikiliyi gizleyen sarmalayicilar.
_WRAPPERS = {"nohup", "setsid", "exec", "command", "time", "stdbuf", "env"}
# exec_name olarak KULLANILMAYACAK kadar genel adlar. `flatpak` bir uygulama
# degil bir baslatici: exec_name'i "flatpak" yapsaydik, engel listesindeki tek
# bir Flatpak uygulamasi butun `flatpak ...` komutlarini bloklardi.
_TOO_GENERIC = {"flatpak", "sh", "bash", "zsh", "python", "python3", "gjs",
                "wine", "sudo", "pkexec"}


def _strip_wrappers(toks: list[str]) -> list[str]:
    """Bastaki ortam atamalarini ve sarmalayicilari at, kalani dondur."""
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in _FIELD_CODES or (len(t) == 2 and t.startswith("%")):
            i += 1
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
            i += 1
            continue
        if os.path.basename(t) in _WRAPPERS:
            i += 1
            continue
        break
    return toks[i:]


def _split(value: str) -> list[str]:
    try:
        return shlex.split(value)
    except ValueError:      # kapanmamis tirnak -- kaba bolme yeter
        return value.split()


def _exec_binary(value: str) -> str:
    """`Exec=` degerinden gercek ikilinin dosya adi. Bulunamazsa bos."""
    toks = _strip_wrappers(_split(value))
    if not toks:
        return ""
    base = os.path.basename(toks[0])
    return "" if base in _TOO_GENERIC else base


def entries() -> list[Entry]:
    """Kurulu .desktop girdileri. Ilk gorulen kazanir (XDG oncelik sirasi)."""
    seen: dict[str, Entry] = {}
    for d in _dirs():
        try:
            files = sorted(d.glob("*.desktop"))
        except OSError:
            continue
        for f in files:
            eid = f.stem
            if eid in seen:
                continue
            name = ""
            names: list[str] = []
            alts: list[str] = []
            domain = ""
            exec_name = ""
            no_display = False
            try:
                for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("[") and name:
                        break          # ilk bolumden sonrasi Actions, bize gerekmiyor
                    if line.startswith("Name=") and not name:
                        name = line[5:].strip()
                        names.append(name)
                    elif line.startswith("Name["):
                        # Yerellestirilmis ad dosyanin icindeyse buradan gelir;
                        # cogu GNOME uygulamasinda ise gettext'ten (asagida).
                        val = line.split("=", 1)[1].strip() if "=" in line else ""
                        if val:
                            names.append(val)
                    elif line.startswith("GenericName"):
                        val = line.split("=", 1)[1].strip() if "=" in line else ""
                        if val:
                            alts.append(val)
                    elif line.startswith("Exec=") and not exec_name:
                        # `[Desktop Action ...]` bolumlerindeki Exec'ler
                        # buraya GELMIYOR: yukaridaki `break` ilk bolumden
                        # sonrasini kesiyor.
                        exec_name = _exec_binary(line.split("=", 1)[1].strip())
                    elif line.startswith(("X-Ubuntu-Gettext-Domain=",
                                          "X-GNOME-Gettext-Domain=")):
                        domain = line.split("=", 1)[1].strip()
                    elif line.startswith(("NoDisplay=true", "Hidden=true")):
                        no_display = True
            except OSError:
                continue
            if domain:
                for pool in (names, alts):
                    for base in list(pool):
                        tr = _translate(domain, base)
                        if tr:
                            pool.append(tr)
            seen[eid] = Entry(
                eid, name or eid, no_display,
                tuple(dict.fromkeys(names)), tuple(dict.fromkeys(alts)),
                exec_name,
            )
    return list(seen.values())


# Turkce harfler ASCII karsiliklarina katlaniyor: kullanici "duzenleyici" de
# yazabilir "düzenleyici" de, ikisi de ayni girdiye gitmeli.
_FOLD = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
    "â": "a", "î": "i", "û": "u", "é": "e",
})


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.translate(_FOLD).lower())


def find(name: str, pool: list[Entry] | None = None) -> Entry | None:
    """Ada gore .desktop girdisi bul: tam ad -> id -> parcali eslesme.

    `pool` verilirse disk YENIDEN OKUNMAZ. Engel listesi cozulurken her ad
    icin bastan `entries()` cagrilmasin diye; ayrica testler gercek .desktop
    tablosu olmadan kosabiliyor.
    """
    want = _norm(name)
    if not want:
        return None
    havuz = entries() if pool is None else pool
    pool = [e for e in havuz if not e.no_display]
    # Sira onemli: asil ad -> id -> ikincil ad -> parcali. Yerellestirmeler her
    # turda dahil, yoksa "metin duzenleyici" hicbir seye gitmez.
    for e in pool:
        if any(_norm(n) == want for n in e.names):
            return e
    for e in pool:
        if _norm(e.entry_id) == want or _norm(e.entry_id).endswith(want):
            return e
    for e in pool:
        if any(_norm(n) == want for n in e.alt_names):
            return e
    for e in pool:
        if any(want in _norm(n) for n in e.names) or want in _norm(e.entry_id):
            return e
    return None


def _tiers(want: str, pool: list[Entry]):
    """`find()`'in dort turu, her biri butun adaylariyla. Sira ayni."""
    yield [e for e in pool if any(_norm(n) == want for n in e.names)]
    yield [e for e in pool
           if _norm(e.entry_id) == want or _norm(e.entry_id).endswith(want)]
    yield [e for e in pool if any(_norm(n) == want for n in e.alt_names)]
    yield [e for e in pool
           if any(want in _norm(n) for n in e.names) or want in _norm(e.entry_id)]


@dataclass(frozen=True)
class Application:
    """Cagiranin yazdigi ad, kurulu uygulamalara gore cozulmus hali."""

    text: str                     # cagiranin yazdigi gibi
    key: str                      # `_norm(text)`
    entry: Entry | None = None    # ad TEK bir uygulamaya gidiyorsa
    # Ad ayni turda birden fazla uygulamaya uyuyorsa onlarin adlari. Bos
    # degilse ne baslatilir ne aranir: "Desktop" bu makinede GitHub Desktop,
    # OpenCode ve Pcbridge Desktop'a uyuyor.
    rivals: tuple[str, ...] = ()
    pool: tuple[Entry, ...] = ()


def resolve_application(name: str, pool: list[Entry] | None = None) -> Application:
    """Adi kurulu bir uygulamaya cevir: `find()` ile ayni turlar, belirsizlik acik.

    `find()` bir turdaki ILK adayi dondurur; baslatmak ve aramak icin bu
    yetmez, cunku hangisinin ilk geldigi XDG sirasina bagli. Ayni gorunen
    adi tasiyan girdiler (`google-chrome` ve `com.google.Chrome`) tek uygulama
    sayilir.
    """
    want = _norm(name)
    havuz = entries() if pool is None else list(pool)
    if not want:
        return Application(str(name), want, pool=tuple(havuz))
    visible = [e for e in havuz if not e.no_display]
    for tier in _tiers(want, visible):
        if not tier:
            continue
        names = tuple(dict.fromkeys(e.name for e in tier))
        if len(names) == 1:
            return Application(str(name), want, tier[0], (), tuple(havuz))
        return Application(str(name), want, None, names, tuple(havuz))
    return Application(str(name), want, pool=tuple(havuz))


# gnome-terminal'in pencereleri `gnome-terminal-server` surecine ait (olculdu,
# audit.log 2026-08-23): surec adi girdinin ikilisi arti bir ek. Yalnizca
# ikili adina ve yalnizca bu uzunluktan itibaren on ek olarak bakilir; "code"
# gibi kisa bir ad baska uygulamalarin on eki olabilir.
_EXEC_PREFIX_MIN = 6


def _is_app(entry: Entry, app: str) -> bool:
    """AT-SPI'daki uygulama adi bu girdinin uygulamasi mi?

    Olculen adlar girdinin alanlarindan birine denk: `gnome-text-editor`
    (ikili), `claude-desktop` (ikili), test penceresinin kimligi (girdi adi).
    """
    got = _norm(app)
    if not got:
        return False
    keys = {_norm(entry.entry_id), _norm(entry.entry_id.rsplit(".", 1)[-1]),
            _norm(entry.exec_name), *(_norm(n) for n in entry.names)}
    keys.discard("")
    if got in keys:
        return True
    exe = _norm(entry.exec_name)
    return len(exe) >= _EXEC_PREFIX_MIN and got.startswith(exe)


def _shows(target: Application, app: str, window: str) -> bool:
    """Bu (uygulama, pencere basligi) cifti adi verilen hedef mi?

    Kurulu bir uygulama icin kanit uygulamanin KIMLIGIDIR. Baslik tek basina
    yetmez: GNOME aramasi web arama saglayicisina dusunce tarayicida basligi
    tam da aranan metin olan bir sekme aciliyor ve eski kural (`hedef
    baslikta geciyor mu`) bunu basari sayiyordu. Baslik yalnizca pencerenin
    sureci BASKA hicbir kurulu uygulamaya ait degilse sayilir: LibreOffice
    `soffice` adiyla calisiyor ve penceresini ancak basligi taniyor.

    Uygulama olmayan bir ad (pencere basliginin bir parcasi) icin baslik ya
    da uygulama adi yeter; bu yalnizca "zaten odakta mi" sorusunda kullanilir,
    boyle bir ad icin hicbir tus gonderilmez.
    """
    if not target.key:
        return False
    if target.entry is None:
        return target.key in _norm(window) or target.key == _norm(app)
    if _is_app(target.entry, app):
        return True
    title = _norm(window)
    if not any(_norm(n) and _norm(n) in title for n in target.entry.names):
        return False
    return not any(_is_app(e, app) for e in target.pool
                   if e.entry_id != target.entry.entry_id)


# Kabuk komutunu parcalara bolen ayiricilar. `||` ve `&&` TEK karakterli
# karsiliklarindan once denenmeli, yoksa `&&` iki bos parcaya bolunur.
_CMD_SEP = re.compile(r"\|\||&&|[;|\n&]")
# Uygulamayi ADIYLA baslatan araclar: engellenecek ad ilk simgede degil,
# ARGUMANINDA duruyor.
_LAUNCHERS = {"gtk-launch", "xdg-open", "kde-open", "exo-open"}


def _candidates(segment: str) -> list[str]:
    """Bir komut parcasinda "hangi uygulama baslatiliyor" adaylari.

    Genelde tek eleman (ikilinin dosya adi). Acik bir baslatici kullanildiysa
    argumani da eklenir: `gtk-launch org.gnome.TextEditor` ikisini de verir.
    """
    toks = _strip_wrappers(_split(segment))
    if not toks:
        return []
    ilk = os.path.basename(toks[0])
    kalan = toks[1:]
    out = [ilk]

    def ekle(ham: str) -> None:
        if ham.endswith(".desktop"):
            ham = ham[: -len(".desktop")]
        out.append(os.path.basename(ham))

    if ilk in _LAUNCHERS and kalan:
        ekle(kalan[0])
    elif ilk == "gio" and len(kalan) >= 2 and kalan[0] in ("launch", "open"):
        ekle(kalan[1])
    elif ilk == "flatpak" and kalan and kalan[0] == "run":
        # `flatpak run --branch=stable dev.vencord.Vesktop` -> uygulama
        # kimligi ilk bayrak OLMAYAN simge.
        for k in kalan[1:]:
            if not k.startswith("-"):
                ekle(k)
                break
    return out


def _blocked_keys(
    blocklist: list[str], pool: list[Entry]
) -> list[tuple[str, set[str]]]:
    """Engel listesindeki adlari eslesme anahtarlarina cevir.

    Her ad once `.desktop` tablosunda ARANIYOR (tahmin degil veri): bulunursa
    girdi kimligi, kimligin son parcasi ve `Exec=` ikilisi de anahtar olur.
    Boylece kullanici "Text Editor" yazinca `gnome-text-editor x.md` komutu da
    yakalaniyor. Cozulemeyen bir ad duz simge eslesmesi olarak kaliyor --
    ne cokuyor ne de sessizce yok sayiliyor.
    """
    out: list[tuple[str, set[str]]] = []
    for ham in blocklist:
        ad = str(ham).strip()
        if not ad:
            continue
        keys = {_norm(ad)}
        entry = find(ad, pool)
        if entry is not None:
            ad = entry.name or ad
            keys.add(_norm(entry.entry_id))
            keys.add(_norm(entry.entry_id.rsplit(".", 1)[-1]))
            if entry.exec_name:
                keys.add(_norm(entry.exec_name))
        keys.discard("")
        if keys:
            out.append((ad, keys))
    return out


def looks_like_gui_launch(
    command: str,
    blocklist: list[str],
    pool: list[Entry] | None = None,
) -> str | None:
    """Komut, engel listesindeki bir GUI uygulamasini baslatiyor mu?

    Eslesirse kullaniciya gosterilecek uygulama adi, aksi halde None.

    NEDEN VAR: kabuktan acilan bir uygulama pcbridge'in COCUGU oluyor ve
    `systemctl --user restart pcbridge` onu kapatiyor (`start_new_session`
    oturum grubunu ayiriyor ama cgroup'u degil). Ayrica cogu zaman uygulama
    kimligi olusmadigi icin `window_list`/`window_focus` pencereyi sonradan
    bulamiyor -- yani ajan kendi actigi pencereyi kaybediyor. Dogru yol
    `window_focus`: uygulamayi masaustu girdisiyle ve kendi systemd kapsaminda
    baslatiyor (`_launch_argv`).

    LISTE BOSSA HICBIR SEY ENGELLENMEZ. Bilincli: yanlis pozitif riski sifir
    baslasin, kullanici sürtünme yaratan adi kendisi eklesin.
    """
    if not command or not blocklist:
        return None
    hedefler = _blocked_keys(
        list(blocklist), entries() if pool is None else pool
    )
    if not hedefler:
        return None
    for segment in _CMD_SEP.split(command):
        for aday in _candidates(segment):
            n = _norm(aday)
            if not n:
                continue
            for ad, keys in hedefler:
                if n in keys:
                    return ad
    return None


LAUNCH_TIMEOUT = 15      # `gtk-launch`in kendisinin bitmesi (uygulama degil)


def _refused(
    code: ErrorCode,
    message: str,
    suggested: str,
    *,
    category: ErrorCategory = ErrorCategory.ACCESSIBILITY,
    retryable: bool = True,
) -> DesktopError:
    """Hicbir sey yapilmadan donen ret: ne tus gitti ne uygulama acildi."""
    return DesktopError(
        code=code,
        message=message,
        category=category,
        retryable=retryable,
        suggested_action=suggested,
        permission_scope="os.window",
        backend="desktop.window",
        execution_state="not_started",
    )


def _unknown(message: str, suggested: str) -> DesktopError:
    """Bir sey YAPILDI (tus gitti ya da uygulama baslatildi), sonuc istenen degil.

    Tekrarlanmaz: arama bir sekme ya da dosya acmis, baslatma ikinci bir
    pencere acmis olabilir. Once bakilir.
    """
    return DesktopError(
        code=ErrorCode.EXECUTION_UNKNOWN,
        message=message,
        category=ErrorCategory.EXECUTION,
        retryable=False,
        suggested_action=suggested,
        permission_scope="os.window",
        backend="desktop.window",
        execution_state="unknown",
    )


def _not_an_app(target: Application) -> DesktopError:
    if target.rivals:
        shown = ", ".join(target.rivals[:5])
        more = f" (+{len(target.rivals) - 5})" if len(target.rivals) > 5 else ""
        return _refused(
            ErrorCode.ELEMENT_AMBIGUOUS,
            f"{target.text!r} matches more than one application: {shown}{more}. "
            "Nothing was started or searched.",
            "Give the full application name; window_list shows the open windows.",
        )
    return _refused(
        ErrorCode.TARGET_MISMATCH,
        f"{target.text!r} is neither the name of an installed application nor one "
        "open window that can be raised. No key was sent: a name typed into GNOME "
        "search that is not an application can open a file, a chat or a web search. "
        "Raising a window by its title needs the GNOME Shell extension.",
        "Look at the open windows with window_list and give the name as shown there; "
        "for a closed application give its installed name.",
    )


def _check_time(deadline: float | None, need: float, what: str) -> None:
    """Son tarihe `need` saniye sigmiyorsa HICBIR SEY yapmadan `NoTimeLeft`."""
    if deadline is None:
        return
    left = deadline - time.monotonic()
    if left < need:
        raise NoTimeLeft(
            f"{what} needs ~{need:.1f} s, {max(0.0, left):.1f} s "
            "are left; nothing was done"
        )


def _until(deadline: float | None) -> float:
    until = time.monotonic() + LAUNCH_OBSERVE
    return until if deadline is None else min(until, deadline)


def observe_focus(focused: Callable[[], tuple[str, str]]) -> tuple[str, str]:
    """Odaktaki (uygulama, pencere). Okunamazsa ret: hicbir sey gonderilmez.

    Eskiden once arama yapiliyor, odak SONRA okunuyordu; odak okunamazsa tuslar
    gitmis ama sonuc dogrulanamamis oluyordu. Toplu eylemdeki kararla ayni
    (Task 5.1): dogrulanamayacak bir eylem gonderilmez.

    "Onde hicbir pencere yok" bunun DISINDA ve ("", "") doner. Erisilebilirlik
    orada calisiyor ve sorunun cevabini veriyor: onde bir sey yoksa hedef de
    onde degil, yani kapali uygulamayi acmak ve sonucu dogrulamak icin hicbir
    engel kalmiyor. OLCULDU 2026-09-20, oturum acildiktan hemen sonra: masaustu
    disinda pencere yokken `window_focus` KAPALI bir uygulamayi bile
    baslatmiyordu ve reddin gerekcesi ("erisilebilirlige bakin") o anda
    `accessibility.read: supported` diyen `system_capabilities` ile
    celisiyordu. Ayni durum kullanici duvar kagidina tikladiginda da olusuyor.
    Odak dokumunden gelen TARGET_MISMATCH baska bir sey anlatamaz: ada gore
    arama o yolda hic yapilmiyor (`atspi_helper.cmd_dump`, `accessibility.rs`).
    """
    try:
        app, window = focused()
    except Exception as exc:  # noqa: BLE001 - gerekce mesajda
        if getattr(exc, "code", None) == ErrorCode.TARGET_MISMATCH:
            return "", ""
        raise _refused(
            ErrorCode.BACKEND_UNAVAILABLE,
            f"The focused window could not be read ({str(exc)[:80]}). Since the "
            "result could not be checked, no key was sent and nothing was "
            "started.",
            "Check with system_capabilities why accessibility cannot be read.",
            category=ErrorCategory.CAPABILITY,
        ) from None
    return str(app or ""), str(window or "")


def _windows_of(
    target: Application, windows: Callable[[], list[Any]]
) -> list[Any] | None:
    """Hedef uygulamanin listedeki pencereleri; liste okunamazsa None."""
    try:
        listed = windows()
    except Exception:  # noqa: BLE001 - bilinmiyor, cagiran eski yola duser
        return None
    return [
        w for w in listed
        if _shows(target, getattr(w, "app", ""), getattr(w, "title", ""))
    ]


def _launch_argv(entry: Entry) -> list[str]:
    """`gtk-launch` komutu; varsa uygulamaya kendi systemd kapsamini ver.

    OLCULDU 2026-09-19: D-Bus ile etkinlesmeyen bir uygulamayi `gtk-launch`
    kendi cocugu olarak baslatiyor ve uygulama CAGIRANIN cgroup'unda kaliyor.
    Servisten cagrilinca bu `pcbridge.service` demek: `systemctl --user
    restart pcbridge` uygulamayi da oldururdu -- `window_focus`un vaadinin
    tam tersi. `systemd-run --user --scope` ile acilan pencere kendi
    kapsamina dustu (+60 ms). GNOME Shell de uygulamalari `app-gnome-*.scope`
    icinde baslatiyor; burada ayni kalip `app-pcbridge-*.scope`.
    """
    argv = ["gtk-launch", entry.entry_id]
    if not shutil.which("systemd-run"):
        return argv
    name = re.sub(r"[^A-Za-z0-9_.:]", "_", entry.entry_id)
    unit = f"app-pcbridge-{name}-{secrets.token_hex(4)}.scope"
    return ["systemd-run", "--user", "--scope", "--collect", "--quiet",
            f"--unit={unit}", *argv]


def _gtk_launch(entry: Entry, timeout: int) -> None:
    """`gtk-launch` ile baslat. Cikis kodu yalnizca "istek gitti" demek."""
    if not shutil.which("gtk-launch"):
        raise _refused(
            ErrorCode.DEPENDENCY_MISSING,
            "`gtk-launch` is not installed (package: libgtk-3-bin).",
            "Install the libgtk-3-bin package.",
            category=ErrorCategory.CAPABILITY,
            retryable=False,
        )

    # Cikti BORUYA degil DOSYAYA gidiyor ve `wait()` kullaniliyor -- `run()`
    # degil. Sebebi olculdu 2026-08-02: `capture_output=True` boru yaratir,
    # Flatpak uygulamalarinda `gtk-launch`in baslattigi `flatpak run` cocugu o
    # boruyu MIRAS ALIR ve `communicate()` gtk-launch'in bitmesini degil borunun
    # KAPANMASINI bekler. Soguk baslatmada Vesktop 15 sn'de zaman asimina
    # ugrayip "baslamadi" hatasi verdi -- oysa uygulama aciliyordu. Sessizce
    # yanlis hata dondurmek en kotu sonuc.
    #   olculdu: ayni komut, capture_output = 1,56 s (sicak) / timeout (soguk),
    #            Popen + wait(dosya) = 0,06 s
    # D-Bus ile etkinlesen normal GTK uygulamalari (org.gnome.TextEditor) her
    # iki yolda da hizli; fark yalnizca gercek cocuk baslatan uygulamalarda.
    with tempfile.TemporaryFile() as errf:
        proc = subprocess.Popen(
            _launch_argv(entry),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=errf,
            start_new_session=True,
        )
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # gtk-launch OLDURULMEZ: uygulama onun cocugu olabilir ve
            # oldurmek yeni acilan pencereyi de goturur.
            raise _unknown(
                f"{entry.name} did not start within {timeout} seconds.",
                "The application may still be opening; look with window_list or "
                "screen_capture before starting it again.",
            ) from None
        errf.seek(0)
        err_text = errf.read().decode("utf-8", "replace").strip()

    if code != 0:
        lines = err_text.splitlines()
        raise _unknown(
            f"{entry.name} could not be started: {lines[-1] if lines else 'unknown'}",
            "Before trying again, check with window_list whether the application "
            "opened after all.",
        )


def _watch(
    target: Application,
    focused: Callable[[], tuple[str, str]],
    windows: Callable[[], list[Any]],
    until: float,
) -> tuple[str, str, str] | None:
    """Baslatilan uygulamanin penceresini `until`e kadar izle.

    ("focused", uygulama, baslik): penceresi odakta. ("listed", ...): listede
    ama odak baska yerde, `LAUNCH_FOCUS_GRACE` boyunca beklendi. None: hic
    gorulmedi. Okuma hatalari gecici sayilir: acilmakta olan bir uygulama
    agaci bir an bozuk verebilir.
    """
    listed: tuple[str, str] | None = None
    listed_at = 0.0
    while True:
        now = time.monotonic()
        try:
            app, window = focused()
            if _shows(target, app, window):
                return "focused", str(app), str(window)
        except Exception:  # noqa: BLE001 - bir sonraki turda yeniden okunur
            pass
        if listed is None:
            mine = _windows_of(target, windows)
            if mine:
                listed = (str(mine[0].app), str(mine[0].title))
                listed_at = now
        if listed is not None and now - listed_at >= LAUNCH_FOCUS_GRACE:
            return "listed", *listed
        if now >= until:
            return ("listed", *listed) if listed is not None else None
        time.sleep(LAUNCH_POLL)


def _launched_unseen(entry: Entry, waited: float) -> DesktopError:
    return _unknown(
        f"{entry.name} was started (gtk-launch returned 0) but within {waited:.0f} s "
        "its window did not show up in the accessibility list. The application may "
        "still be opening, may have opened and closed, or may not publish a tree.",
        "Do not start it again (a second window could open); look with "
        "screen_capture or window_list first.",
    )


@dataclass(frozen=True)
class Outcome:
    """Bir pencere isleminin sonucu: hangi yoldan gidildi ve ne goruldu."""

    path: str      # "extension" | "already" | "launch" | "search"
    note: str      # cagirana donen metin
    app: str = ""
    window: str = ""


def launch_application(
    target: Application,
    focused: Callable[[], tuple[str, str]],
    windows: Callable[[], list[Any]],
    *,
    timeout: int = LAUNCH_TIMEOUT,
    deadline: float | None = None,
) -> Outcome:
    """Uygulamayi `gtk-launch` ile ac ve penceresini GOREREK dogrula.

    Keyfi komut CALISTIRILMAZ: `launch` bir GUI uygulamasi acmak icin, kabuk
    icin `shell_run` var. Boylece batch icindeki bir `launch` eylemi asla
    beklenmedik bir komuta donusemez.

    `gtk-launch`in cikis kodu basari sayilmaz (Task 6.4): istek gitti demek,
    uygulama acildi demek degil. Basari, uygulamanin bir penceresinin
    erisilebilirlik listesinde gorulmesi. Uygulama zaten aciksa var olan
    penceresi de sayilir.
    """
    if target.entry is None:
        raise _not_an_app(target)
    _check_time(deadline, LAUNCH_COST, f"Starting {target.entry.name}")
    started = time.monotonic()
    _gtk_launch(target.entry, timeout)
    seen = _watch(target, focused, windows, _until(deadline))
    if seen is None:
        raise _launched_unseen(target.entry, time.monotonic() - started)
    state, app, window = seen
    where = "focused" if state == "focused" else "listed"
    return Outcome(
        "launch",
        f"{target.entry.name} started, its window is {where}: {app} | {window}",
        app,
        window,
    )


def bring_to_front(
    window: str,
    backend,
    focused: Callable[[], tuple[str, str]],
    windows: Callable[[], list[Any]] | None = None,
    *,
    settle: float = SEARCH_SETTLE,
    deadline: float | None = None,
    pool: list[Entry] | None = None,
) -> Outcome:
    """Pencereyi one al; uygulama kapaliysa ac. Sira modul basinda.

    `windows` verilmezse kapali/acik ayrimi yapilamaz ve eski davranis kalir:
    GNOME aramasi hem acar hem one alir. `deadline` (monotonic) yavas bir
    adima yetecek sure kalmadiysa o adimi `NoTimeLeft` ile hic baslatmaz.
    """
    if not _norm(window):
        raise AppError("`focus` needs a window or application name.")

    if activate_window(window):
        return Outcome("extension", f"{window} raised by the GNOME extension")

    target = resolve_application(window, pool)
    app, title = observe_focus(focused)
    if _shows(target, app, title):
        return Outcome(
            "already",
            f"{app} | {title} is already focused; no key was sent",
            app,
            title,
        )
    if target.entry is None:
        raise _not_an_app(target)

    mine = _windows_of(target, windows) if windows is not None else None
    if mine == []:
        # Listede hic penceresi yok: kapali. Tus gondermeden ac.
        _check_time(deadline, LAUNCH_COST, f"starting {target.entry.name}")
        started = time.monotonic()
        _gtk_launch(target.entry, LAUNCH_TIMEOUT)
        seen = _watch(target, focused, windows, _until(deadline))
        if seen is None:
            raise _launched_unseen(target.entry, time.monotonic() - started)
        state, app, title = seen
        if state == "focused":
            return Outcome(
                "launch",
                f"{target.entry.name} started and focused: {app} | {title}",
                app,
                title,
            )
        # Acildi ama odak baska yerde: artik acik bir pencere.
        if activate_window(window):
            return Outcome(
                "launch",
                f"{target.entry.name} started and raised by the GNOME extension",
                app,
                title,
            )

    _check_time(deadline, SEARCH_COST, "the GNOME search")
    return _search(target, backend, focused, settle)


# Arama kutusuna yazilan en uzun ad. Kurulu bir uygulamanin adi bunun cok
# altinda; uzun bir metin yalnizca arama kutusunu doldururdu.
SEARCH_TEXT_MAX = 120


def _typed(text: str) -> str:
    """Arama kutusuna yazilacak hali: TEK SATIR, kirpilmis.

    Ad cagirandan geliyor ve ham tus yoluyla yaziliyor. Icindeki bir satir
    sonu Enter demek: arama daha ad tamamlanmadan ilk sonucu acardi. Ad
    cozumlemesi zaten harf-rakam disini yok sayiyor (`_norm`), yani bosluklari
    sadelestirmek hangi uygulamanin bulundugunu degistirmiyor.
    """
    return " ".join(str(text).split())[:SEARCH_TEXT_MAX]


def _search(
    target: Application,
    backend,
    focused: Callable[[], tuple[str, str]],
    settle: float,
) -> Outcome:
    """GNOME aramasi: acik `degraded` yedek, yalnizca kurulu bir uygulama icin.

    Sonuc o uygulamanin kimligiyle dogrulanir (`_shows`). Tutmazsa `Escape`
    ile toparlanip hata atilir; arama bir seyi acmis olabilecegi icin sonuc
    `EXECUTION_UNKNOWN`, tekrarlanmaz.
    """
    backend.key("super")
    time.sleep(settle)
    # Overview'da pano bloklu -> ham yol. Olculdu 2026-08-02.
    backend.type_text(_typed(target.text), raw=True)
    time.sleep(SEARCH_RESULTS)
    backend.key("Return")
    time.sleep(SEARCH_ACTIVATE)

    try:
        app, title = focused()
    except Exception as exc:  # noqa: BLE001 - gerekce mesajda
        _escape(backend)
        raise _unknown(
            f"searched for {target.text!r} but the result could not be checked (focus unreadable: "
            f"{str(exc)[:80]}).",
            "Look at the screen: screen_capture. See what opened before "
            "searching again.",
        ) from None

    if _shows(target, app, title):
        return Outcome(
            "search",
            f"{app} | {title} raised (GNOME search, fallback path)",
            str(app),
            str(title),
        )

    _escape(backend)
    raise _unknown(
        f"{target.text!r} could not be raised; {app} | {title!r} has the focus. GNOME "
        "search may have picked another result (a file, a chat or a "
        "web search).",
        "See the open windows with window_list; the search may have opened a tab "
        "or a file.",
    )


def focus(
    window: str,
    backend,
    focused: Callable[[], tuple[str, str]],
    settle: float = SEARCH_SETTLE,
    *,
    windows: Callable[[], list[Any]] | None = None,
    deadline: float | None = None,
) -> str:
    """Eski arayuz ve geri donus noktasi: `bring_to_front`un metni."""
    return bring_to_front(
        window, backend, focused, windows, settle=settle, deadline=deadline
    ).note


def prepare(
    app: str,
    backend,
    focused: Callable[[], tuple[str, str]],
    windows: Callable[[], list[Any]] | None = None,
) -> str:
    """`computer_task(app=...)`: `window_focus` ile ayni sira.

    Eskiden her seferinde once baslatiyordu; acik bir uygulamada bu ikinci
    bir pencere demekti. Artik acik pencere one alinir, kapali uygulama acilir.
    """
    return bring_to_front(app, backend, focused, windows).note


def _escape(backend) -> None:
    """Arama acik kaldiysa kapat. Iki kez: ilki metni, ikincisi overview'i."""
    try:
        for _ in range(2):
            backend.key("Escape")
            time.sleep(0.4)
    except Exception:
        pass
