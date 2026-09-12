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

    Arama YANLIS uygulamayi acabilecegi icin sonuc her zaman AT-SPI'dan
    dogrulanir; tutmazsa `Escape` ile toparlanip hata donulur.

    NOT: overview acikken Wayland panosu bloklaniyor (`wl-paste` 5 sn'de cevap
    vermedi), bu yuzden arama kutusuna HAM tus yoluyla yaziliyor. Uygulama
    adlari ASCII oldugu icin `tr+intl` duzeni sorun cikarmiyor.
"""

from __future__ import annotations

import gettext
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

SEARCH_SETTLE = 1.2      # overview acilmasi
SEARCH_RESULTS = 1.5     # arama sonuclarinin gelmesi
SEARCH_ACTIVATE = 3.0    # uygulamanin one gelmesi
LAUNCH_SETTLE = 1.5       # yeni GUI surecinin ilk penceresini acmasi

_FOCUS_BUS_NAME = "io.github.eymistaken.Pcbridge.WindowFocus"
_FOCUS_OBJECT_PATH = "/io/github/eymistaken/Pcbridge/WindowFocus"
_FOCUS_INTERFACE = "io.github.eymistaken.Pcbridge.WindowFocus"


class AppError(RuntimeError):
    """Uygulama baslatilamadi ya da pencere one alinamadi."""


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


def matches_focus(target: str, app: str, window: str) -> bool:
    """AT-SPI odak tanimi insanca yazilmis hedefle eslesiyor mu?"""
    want = _norm(target)
    return bool(want and (want in _norm(app) or want in _norm(window)))


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
    `window_focus`: masaustunun kendi aramasindan geciyor.

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


def launch(name: str, timeout: int = 15) -> str:
    """Uygulamayi .desktop girdisiyle baslat.

    Keyfi komut CALISTIRILMAZ: `launch` bir GUI uygulamasi acmak icin, kabuk
    icin `shell_run` var. Boylece batch icindeki bir `launch` eylemi asla
    beklenmedik bir komuta donusemez.
    """
    entry = find(name)
    if entry is None:
        raise AppError(
            f"{name!r} adinda bir uygulama girdisi yok. Tam adi icin "
            "`shell_run(\"ls /usr/share/applications\")` bakabilir ya da "
            "uygulamayi dogrudan `shell_run` ile baslatabilirsiniz."
        )
    if not shutil.which("gtk-launch"):
        raise AppError("`gtk-launch` kurulu degil (paket: libgtk-3-bin).")

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
            ["gtk-launch", entry.entry_id],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=errf,
            start_new_session=True,
        )
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            # gtk-launch OLDURULMEZ: uygulama onun cocugu olabilir ve
            # oldurmek yeni acilan pencereyi de goturur.
            raise AppError(f"{entry.name} {timeout} saniyede baslamadi.") from exc
        errf.seek(0)
        err_text = errf.read().decode("utf-8", "replace").strip()

    if code != 0:
        lines = err_text.splitlines()
        raise AppError(
            f"{entry.name} baslatilamadi: {lines[-1] if lines else 'bilinmiyor'}"
        )
    return f"{entry.name} baslatildi ({entry.entry_id})"


def focus(
    window: str,
    backend,
    focused: "callable",
    settle: float = SEARCH_SETTLE,
) -> str:
    """Bir pencereyi one al: eklenti hizli yolu, sonra GNOME arama yedegi.

    Eklenti kendi bool sonucunu kabugun odak penceresiyle dogrular. Yedekte
    `focused()` -> (uygulama, pencere) dondurur; GNOME aramasinin sonucunu
    dogrular. Tutmazsa `Escape` ile toparlanip hata atilir.
    """
    want = _norm(window)
    if not want:
        raise AppError("`focus` icin pencere/uygulama adi gerekli.")

    if _extension_activate(window):
        # Bool eklentinin icinde `global.display.focus_window` ile dogrulanir;
        # ikinci bir AT-SPI turu hem gereksiz hem nested GNOME'da yanlistir.
        return f"{window} GNOME eklentisiyle one alindi"

    backend.key("super")
    time.sleep(settle)
    # Overview'da pano bloklu -> ham yol. Olculdu 2026-08-02.
    backend.type_text(window, raw=True)
    time.sleep(SEARCH_RESULTS)
    backend.key("Return")
    time.sleep(SEARCH_ACTIVATE)

    try:
        app, win = focused()
        now = f"{app} | {win}"
    except Exception as exc:
        _escape(backend)
        raise AppError(
            f"{window!r} arandi ama sonuc dogrulanamadi (odak okunamadi: "
            f"{str(exc)[:80]}). Ekrana bakin: screen_capture."
        ) from None

    if matches_focus(window, app, win):
        return f"{now} one alindi"

    _escape(backend)
    raise AppError(
        f"{window!r} one alinamadi; odakta {now!r} var. GNOME aramasi baska "
        "bir sonuc secmis olabilir. Acik pencereleri window_list ile gorun."
    )


def prepare(
    app: str,
    backend,
    focused: "callable",
    launch_settle: float = LAUNCH_SETTLE,
) -> str:
    """Uygulamayi baslat; kendi penceresi odaktaysa ikinci kez arama yapma."""
    opened = launch(app)
    time.sleep(launch_settle)
    try:
        focused_app, focused_window = focused()
    except Exception:
        focused_app, focused_window = "", ""

    if matches_focus(app, focused_app, focused_window):
        return (
            f"{opened} · {focused_app} | {focused_window} "
            "acildiktan sonra odakta"
        )
    return f"{opened} · {focus(app, backend, focused)}"


def _escape(backend) -> None:
    """Arama acik kaldiysa kapat. Iki kez: ilki metni, ikincisi overview'i."""
    try:
        for _ in range(2):
            backend.key("Escape")
            time.sleep(0.4)
    except Exception:
        pass
