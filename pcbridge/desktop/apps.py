"""Uygulama baslatma ve pencere one alma.

PENCERE ONE ALMA NEDEN BOYLE
    OLCULDU 2026-08-02, uc yol denendi:

      1. `org.gnome.Shell.Introspect.GetWindows()` -> "Access denied" (GNOME 46)
      2. AT-SPI `Component.grab_focus()` -> GTK'da `atspi_error`, Electron'da
         `False`. Hicbir pencerede calismadi.
      3. D-Bus `org.freedesktop.Application.Activate` -> `exit=0` donuyor ama
         pencere ONE GELMIYOR. Sessiz basarisizlik, iki kez dogrulandi.

    Calisan tek yol: GNOME'un kendi aramasi (`super` + ad + `Return`), olculen
    sure ~6,5 saniye. Pahali ama tek secenek.

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
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

SEARCH_SETTLE = 1.2      # overview acilmasi
SEARCH_RESULTS = 1.5     # arama sonuclarinin gelmesi
SEARCH_ACTIVATE = 3.0    # uygulamanin one gelmesi


class AppError(RuntimeError):
    """Uygulama baslatilamadi ya da pencere one alinamadi."""


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


def find(name: str) -> Entry | None:
    """Ada gore .desktop girdisi bul: tam ad -> id -> parcali eslesme."""
    want = _norm(name)
    if not want:
        return None
    pool = [e for e in entries() if not e.no_display]
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
    try:
        proc = subprocess.run(
            ["gtk-launch", entry.entry_id],
            capture_output=True, text=True, timeout=timeout, check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise AppError(f"{entry.name} {timeout} saniyede baslamadi.") from exc
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        raise AppError(f"{entry.name} baslatilamadi: {err[-1] if err else 'bilinmiyor'}")
    return f"{entry.name} baslatildi ({entry.entry_id})"


def focus(
    window: str,
    backend,
    focused: "callable",
    settle: float = SEARCH_SETTLE,
) -> str:
    """Bir pencereyi one al: GNOME aramasi + AT-SPI dogrulamasi.

    `focused()` -> (uygulama, pencere) donduren bir cagirilabilir; sonucun
    dogrulanmasi icin. Dogrulanamazsa `Escape` ile toparlanip hata atilir --
    yanlis pencereye tus gondermektense acikca basarisiz olmak dogru.
    """
    try:
        before = " | ".join(focused())
    except Exception:
        before = ""

    want = _norm(window)
    if not want:
        raise AppError("`focus` icin pencere/uygulama adi gerekli.")

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

    if want in _norm(app) or want in _norm(win):
        return f"{now} one alindi"

    _escape(backend)
    raise AppError(
        f"{window!r} one alinamadi; odakta {now!r} var. GNOME aramasi baska "
        "bir sonuc secmis olabilir. Acik pencereleri window_list ile gorun."
    )


def _escape(backend) -> None:
    """Arama acik kaldiysa kapat. Iki kez: ilki metni, ikincisi overview'i."""
    try:
        for _ in range(2):
            backend.key("Escape")
            time.sleep(0.4)
    except Exception:
        pass
