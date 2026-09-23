"""Oturum ortam degiskenlerini onar (D-Bus, Wayland, XDG_RUNTIME_DIR).

NEDEN VAR
    stdio tasimasinda sunucuyu ISTEMCI baslatiyor ve surec onun ortamini
    devraliyor. O ortamin dogru olacaginin garantisi yok. OLCULDU 2026-08-03:
    Codex'in baslattigi pcbridge surecinde `DBUS_SESSION_BUS_ADDRESS`
    genisletilmemis bir literal olarak geliyordu -- degeri birebir
    `$DBUS_SESSION_BUS_ADDRESS` string'iydi. Sonuc:

        busctl --user ...        -> "Failed to connect to bus: Connection refused"
        monitors.list_monitors() -> MonitorError, xrandr yedegi de Wayland'de yok
        screen_capture           -> "Monitor tablosu okunamadi"
        window_list              -> "AT-SPI yardimcisi cevap vermedi"
        computer_batch launch    -> ok=False

    Yani masaustu araclarinin TAMAMI, sirf bir ortam degiskeni yuzunden.
    systemd altinda calisan HTTP yolu bu sorunu yasamiyor (birim ortami
    `systemctl --user import-environment` ile dogru kuruluyor); sorun yalnizca
    stdio'da ve tam da yeni birincil yol orasi.

NASIL
    Soketler zaten standart yerlerinde duruyor (`/run/user/<uid>/bus`,
    `/run/user/<uid>/wayland-0`). Degisken eksikse ya da isaret ettigi soket
    yoksa, dogrusu buradan TURETILIYOR.

    Var olan ve GECERLI bir degere DOKUNULMUYOR: kullanici bilincli olarak
    baska bir bus adresi vermisse (ic ice oturum, test duzenegi) onu ezmek
    sessiz bir hata olurdu.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Onarilan degisken adlari -- tanida ve loglarda gosterilir.
__all__ = ["ensure_session_env", "describe"]


def _runtime_dir(env: dict[str, str]) -> Path | None:
    """Gecerli bir XDG_RUNTIME_DIR yolu (yoksa standart yerden turet)."""
    raw = env.get("XDG_RUNTIME_DIR", "")
    if raw and not raw.startswith("$"):
        p = Path(raw)
        if p.is_dir():
            return p
    p = Path(f"/run/user/{os.getuid()}")
    return p if p.is_dir() else None


def _bus_ok(value: str, runtime: Path | None) -> bool:
    """Bu `DBUS_SESSION_BUS_ADDRESS` degeri kullanilabilir mi?

    Yalnizca `unix:path=` bicimi dogrulanabiliyor; tcp: ya da baska bir
    tasima verilmisse KARISMIYORUZ (dogrulayamadigimiz seyi bozmayalim).
    """
    if not value or value.startswith("$"):
        return False
    if not value.startswith("unix:path="):
        return True  # dogrulayamiyoruz, oldugu gibi birak
    path = value[len("unix:path="):].split(",", 1)[0]
    return Path(path).exists()


def ensure_session_env(env: dict[str, str] | None = None) -> list[str]:
    """Eksik/bozuk oturum degiskenlerini standart yollardan doldur.

    `env` verilmezse `os.environ` uzerinde calisir (asil kullanim). Test
    icin duz bir sozluk verilebilir.

    Donen liste ONARILAN degiskenlerin adlari. Bos liste "ortam zaten
    dogruydu" demek; tani bunu gosteriyor.
    """
    e = os.environ if env is None else env
    fixed: list[str] = []

    runtime = _runtime_dir(e)  # type: ignore[arg-type]
    if runtime is not None and e.get("XDG_RUNTIME_DIR") != str(runtime):
        e["XDG_RUNTIME_DIR"] = str(runtime)
        fixed.append("XDG_RUNTIME_DIR")

    if not _bus_ok(e.get("DBUS_SESSION_BUS_ADDRESS", ""), runtime):
        if runtime is not None and (runtime / "bus").exists():
            e["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime / 'bus'}"
            fixed.append("DBUS_SESSION_BUS_ADDRESS")

    # WAYLAND_DISPLAY: gnome-screenshot ve wl-copy buna bakiyor. Soket adi
    # oturumdan oturuma degisebiliyor (wayland-0, wayland-1), o yuzden
    # dizindeki ilk `wayland-N` soketi seciliyor.
    wl = e.get("WAYLAND_DISPLAY", "")
    if (not wl or wl.startswith("$")) and runtime is not None:
        for cand in sorted(runtime.glob("wayland-[0-9]")):
            e["WAYLAND_DISPLAY"] = cand.name
            fixed.append("WAYLAND_DISPLAY")
            break

    # XDG_SESSION_TYPE: Chromium tabanli uygulamalar (Chrome, Brave, Electron)
    # hangi ozone yolunu sececeklerine buna bakarak karar veriyor. Bos kalirsa
    # WAYLAND_DISPLAY dolu olsa bile X11'e dusuyorlar, DISPLAY de bos oldugu
    # icin "Missing X server or $DISPLAY" deyip SEGFAULT ediyorlar.
    # OLCULDU 2026-09-20, tek degisken ayrilarak: sunucunun ortaminda Brave
    # 0 surecle cokuyor; AYNI ortama yalnizca `XDG_SESSION_TYPE=wayland`
    # eklenince 9 surecle aciliyor ve X11 hatasi hic cikmiyor. Belirti
    # aldaticiydi: `gtk-launch` yine 0 donuyor, systemd kapsami aciliyor,
    # geriye yalnizca "penceresi gorulmedi" kaliyordu.
    if not (e.get("XDG_SESSION_TYPE") or "").strip() and e.get("WAYLAND_DISPLAY"):
        e["XDG_SESSION_TYPE"] = "wayland"
        fixed.append("XDG_SESSION_TYPE")

    # DISPLAY / XAUTHORITY: yukaridaki onarimdan sonra Wayland yolu seciliyor,
    # yani cogu uygulama buna artik muhtac degil. Yalnizca X11'den baska yolu
    # olmayanlar icin doldurulyor ve dogru degerler calisan Xwayland'in kendi
    # komut satirinda duruyor. Ayni surecte ikisi de bostu.
    if not (e.get("DISPLAY") or "").strip():
        found = _xwayland()
        if found is not None:
            display, auth = found
            e["DISPLAY"] = display
            fixed.append("DISPLAY")
            if auth and not (e.get("XAUTHORITY") or "").strip():
                e["XAUTHORITY"] = auth
                fixed.append("XAUTHORITY")

    return fixed


def _xwayland() -> tuple[str, str] | None:
    """Calisan Xwayland'in (DISPLAY, XAUTHORITY) degerleri; yoksa None.

    Kaynak sunucunun KENDI komut satiri (`Xwayland :1 ... -auth <yol>`),
    cunku ad da yetki dosyasi da oturumdan oturuma degisiyor. Hem soketi hem
    yetki dosyasi duran adaylar aliniyor; birden fazlaysa ekran numarasi
    kucuk olan, yalnizca kararli olsun diye. Ortamda gecerli bir DISPLAY
    varsa buraya HIC gelinmiyor: dogrulayamadigimiz bir degeri bozmuyoruz.
    """
    best: tuple[int, str, str] | None = None
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            argv = (proc / "cmdline").read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        parts = [a for a in argv.split("\0") if a]
        if not parts or os.path.basename(parts[0]) != "Xwayland":
            continue
        display = next((a for a in parts[1:] if re.fullmatch(r":\d+", a)), "")
        if not display:
            continue
        if not Path(f"/tmp/.X11-unix/X{display[1:]}").exists():
            continue
        auth = ""
        if "-auth" in parts:
            candidate = parts[parts.index("-auth") + 1:]
            if candidate and Path(candidate[0]).exists():
                auth = candidate[0]
        number = int(display[1:])
        if best is None or number < best[0]:
            best = (number, display, auth)
    return (best[1], best[2]) if best is not None else None


def describe(env: dict[str, str] | None = None) -> str:
    """Tani icin tek satir: oturum degiskenleri ne durumda."""
    e = os.environ if env is None else env
    runtime = _runtime_dir(e)  # type: ignore[arg-type]
    bus = e.get("DBUS_SESSION_BUS_ADDRESS", "")
    parts = [
        f"XDG_RUNTIME_DIR={e.get('XDG_RUNTIME_DIR') or '(unset)'}",
        "DBUS=" + ("valid" if _bus_ok(bus, runtime) else "INVALID"),
        f"WAYLAND_DISPLAY={e.get('WAYLAND_DISPLAY') or '(unset)'}",
        f"DISPLAY={e.get('DISPLAY') or '(unset)'}",
    ]
    return " · ".join(parts)


GNOME = "gnome"
KDE = "kde"
# The desktops pcbridge's desktop tools are built for, as users know them.
DESKTOP_NAMES = {GNOME: "GNOME", KDE: "KDE Plasma"}


def _desktop_tokens(env) -> list[str]:
    return [t.strip() for t in (env.get("XDG_CURRENT_DESKTOP") or "").split(":") if t.strip()]


def _kind_from_tokens(tokens: list[str]) -> str:
    # Zorin and Ubuntu say "zorin:GNOME" / "ubuntu:GNOME"; Plasma says "KDE".
    if any("gnome" in t.lower() for t in tokens):
        return GNOME
    if any(t.lower() == "kde" for t in tokens):
        return KDE
    return ""


def support_note(env: dict[str, str] | None = None) -> str:
    """Name an unsupported session, or "" (Step 8 of 2.0).

    pcbridge's desktop tools are built on GNOME (Mutter, GNOME Shell) and
    KDE Plasma (KWin), both on Wayland. In another session they refuse (fail
    closed); this says why in words, so a refusal is not blamed on the screen
    lock. An EMPTY value is not a verdict: stdio clients and systemd often
    pass none (measured, see CLAUDE.md), and `ensure_session_env` fills it in
    from the Wayland socket.
    """
    e = os.environ if env is None else env
    kind = (e.get("XDG_SESSION_TYPE") or "").strip().lower()
    tokens = _desktop_tokens(e)
    problems = []
    if kind and kind != "wayland":
        name = "X11" if kind == "x11" else kind
        problems.append(f"{'an' if name[0].lower() in 'aeiox' else 'a'} {name} session")
    if tokens and not _kind_from_tokens(tokens):
        problems.append(f"the {e.get('XDG_CURRENT_DESKTOP', '').strip()} desktop")
    if not problems:
        return ""
    return (
        f"Unsupported session: {' on '.join(problems)}. pcbridge's desktop tools "
        "need GNOME or KDE Plasma on Wayland and refuse here; the shell, file, "
        "tmux and agent tools work anywhere."
    )


# GNOME Shell and Plasma major versions pcbridge has actually run on. Others
# are reported as untested rather than refused (Step 9 of 2.0).
TESTED_SHELL_MAJORS = frozenset({"46"})
TESTED_PLASMA_MAJORS: frozenset[str] = frozenset()
_PLATFORM_TTL = 60.0
_platform_cache: tuple[float, dict] | None = None
_kind_cache: tuple[float, str] | None = None


def _busctl(*args: str) -> str:
    import subprocess

    try:
        return subprocess.run(["busctl", "--user", *args], capture_output=True,
                              text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _bus_names() -> list[str]:
    import json

    try:
        return json.loads(_busctl("--json=short", "call", "org.freedesktop.DBus",
                                  "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                  "ListNames") or "{}").get("data", [[]])[0]
    except (ValueError, IndexError, AttributeError):
        return []


def _kind_from_names(names: list[str]) -> str:
    if "org.gnome.Shell" in names:
        return GNOME
    if "org.kde.KWin" in names:
        return KDE
    return ""


def desktop_kind(env: dict[str, str] | None = None) -> str:
    """Which supported desktop this is: `GNOME`, `KDE`, or "" (neither/unknown).

    `XDG_CURRENT_DESKTOP` decides when it is set. When it is empty (stdio
    clients and systemd often pass none), the compositor's name on the session
    bus decides: GNOME Shell owns `org.gnome.Shell`, KWin owns `org.kde.KWin`.
    The bus answer is cached for a minute, like `platform_summary`.
    """
    import time

    global _kind_cache
    e = os.environ if env is None else env
    tokens = _desktop_tokens(e)
    if tokens:
        return _kind_from_tokens(tokens)
    now = time.monotonic()
    if env is None and _kind_cache and now - _kind_cache[0] < _PLATFORM_TTL:
        return _kind_cache[1]
    kind = _kind_from_names(_bus_names())
    if env is None:
        _kind_cache = (now, kind)
    return kind


def _plasma_version() -> str:
    import subprocess

    try:
        out = subprocess.run(["plasmashell", "--version"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(r"(\d+(?:\.\d+)+)", out)
    return m.group(1) if m else ""


def platform_summary(env: dict[str, str] | None = None) -> dict:
    """The desktop, its version, the session, and the interfaces pcbridge uses.

    Read-only and cheap (measured on GNOME: 3 ms + 15 ms, cached for a
    minute). An unknown or missing piece is reported, never raised.
    """
    import time

    global _platform_cache
    now = time.monotonic()
    if env is None and _platform_cache and now - _platform_cache[0] < _PLATFORM_TTL:
        return _platform_cache[1]
    e = os.environ if env is None else env
    names = _bus_names()
    tokens = _desktop_tokens(e)
    kind = _kind_from_tokens(tokens) if tokens else _kind_from_names(names)
    shell = plasma = ""
    if kind != KDE:
        raw = _busctl("get-property", "org.gnome.Shell", "/org/gnome/Shell",
                      "org.gnome.Shell", "ShellVersion")
        m = re.search(r'"([^"]+)"', raw)
        shell = m.group(1) if m else ""
    if kind == KDE:
        plasma = _plasma_version()
    notes = []
    note = support_note(e)
    if note:
        notes.append(note)
    if kind == KDE:
        if not plasma:
            notes.append("KDE Plasma did not report its version (plasmashell --version).")
        elif plasma.split(".")[0] not in TESTED_PLASMA_MAJORS:
            notes.append(f"KDE Plasma {plasma} is untested; the capabilities above "
                         "are what was actually found.")
    elif not shell:
        notes.append("GNOME Shell did not answer on the session bus.")
    elif shell.split(".")[0] not in TESTED_SHELL_MAJORS:
        notes.append(f"GNOME Shell {shell} is untested (pcbridge was tested on "
                     f"{', '.join(sorted(TESTED_SHELL_MAJORS))}); the capabilities "
                     "above are what was actually found.")
    result = {
        "environment": kind or None,
        "gnome_shell": shell or None,
        "plasma": plasma or None,
        "session_type": e.get("XDG_SESSION_TYPE") or None,
        "desktop": e.get("XDG_CURRENT_DESKTOP") or None,
        "screencast": "org.gnome.Mutter.ScreenCast" in names,
        "remote_desktop": "org.gnome.Mutter.RemoteDesktop" in names,
        "notes": notes,
    }
    if env is None:
        _platform_cache = (now, result)
    return result
