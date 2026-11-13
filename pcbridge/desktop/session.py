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

import json
import os
import re
import stat
import subprocess
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

    # Hyprland has an instance-specific IPC socket and a matching Wayland
    # socket. Never repair either one by choosing the first wayland-N socket
    # when more than one compositor is running under this user.
    tokens = _desktop_tokens(e)
    named = _kind_from_tokens(tokens) if tokens else UNKNOWN
    if named == HYPRLAND or (named == UNKNOWN and not tokens and
                             (e.get("HYPRLAND_INSTANCE_SIGNATURE") or
                              e.get("WAYLAND_DISPLAY") or
                              not _kind_from_names(_bus_names()))):
        probe_env = dict(e)
        wl_value = e.get("WAYLAND_DISPLAY", "")
        if (runtime is not None and
                (not re.fullmatch(r"wayland-[0-9]+", wl_value) or
                 not _owned_socket(runtime / wl_value))):
            probe_env.pop("WAYLAND_DISPLAY", None)
        instance = hyprland_instance(probe_env)
        if instance is not None:
            if e.get("HYPRLAND_INSTANCE_SIGNATURE") != instance["instance"]:
                e["HYPRLAND_INSTANCE_SIGNATURE"] = instance["instance"]
                fixed.append("HYPRLAND_INSTANCE_SIGNATURE")
            if e.get("WAYLAND_DISPLAY") != instance["wl_socket"]:
                e["WAYLAND_DISPLAY"] = instance["wl_socket"]
                fixed.append("WAYLAND_DISPLAY")

    # WAYLAND_DISPLAY: gnome-screenshot ve wl-copy buna bakiyor. Soket adi
    # oturumdan oturuma degisebiliyor (wayland-0, wayland-1), o yuzden
    # dizindeki ilk `wayland-N` soketi seciliyor.
    wl = e.get("WAYLAND_DISPLAY", "")
    if ((not wl or wl.startswith("$")) and runtime is not None
            and named != HYPRLAND
            and not (named == UNKNOWN and _hyprctl_instances(e))):
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

    # X11 desktops (Cinnamon, Xfce under LightDM). Their sessions do not start
    # graphical-session.target, so the daemon, started with the user manager,
    # can come up before the X session imports anything (measured on Debian
    # 12 with Xfce: the daemon had none of DISPLAY, XAUTHORITY or
    # XDG_CURRENT_DESKTOP, and Xfce never imports XDG_SESSION_TYPE). logind
    # knows the user's graphical session, its type and its X display.
    if not e.get("WAYLAND_DISPLAY"):
        fixed += _x11_from_logind(e, runtime)

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


def _x11_from_logind(e, runtime: Path | None) -> list[str]:
    """Fill in an X11 session's variables from logind and the user manager.

    Only for the user's local graphical session of type x11, and only the
    variables that are missing (an explicit `XDG_SESSION_TYPE=tty` over SSH
    stays). Returns the names it set.
    """
    if (e.get("DISPLAY") and e.get("XDG_SESSION_TYPE") and e.get("XDG_CURRENT_DESKTOP")
            and e.get("XAUTHORITY")):
        return []
    found = _logind_x11_session()
    if found is None:
        return []
    display, desktop = found
    if e.get("DISPLAY") and e["DISPLAY"] != display:
        return []  # another X display was chosen on purpose
    manager: dict[str, str] = {}
    try:
        env = {**os.environ, **({"XDG_RUNTIME_DIR": str(runtime)} if runtime else {})}
        out = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True,
                             text=True, timeout=3, env=env).stdout
        manager = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    except (OSError, subprocess.SubprocessError):
        pass
    if manager.get("DISPLAY") and manager["DISPLAY"] != display:
        manager = {}
    auth = manager.get("XAUTHORITY") or ""
    if not auth and (Path.home() / ".Xauthority").is_file():
        auth = str(Path.home() / ".Xauthority")
    current = manager.get("XDG_CURRENT_DESKTOP") or {
        "cinnamon": "X-Cinnamon", "xfce": "XFCE"}.get(desktop.lower(), desktop)
    fixed = []
    for name, value in (("DISPLAY", display), ("XDG_SESSION_TYPE", "x11"),
                        ("XDG_CURRENT_DESKTOP", current), ("XAUTHORITY", auth)):
        if value and not (e.get(name) or "").strip():
            e[name] = value
            fixed.append(name)
    return fixed


def _logind_x11_session() -> tuple[str, str] | None:
    """(X display, logind Desktop) of the user's local x11 session, or None."""
    def show(*args: str) -> dict[str, str]:
        try:
            out = subprocess.run(["loginctl", *args], capture_output=True, text=True,
                                 timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            return {}
        return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)

    user = show("show-user", str(os.getuid()), "-p", "Display")
    sid = user.get("Display", "")
    if not sid:
        return None
    props = show("show-session", sid, "-p", "Type", "-p", "Display", "-p", "Desktop",
                 "-p", "Remote", "-p", "Class")
    if (props.get("Type") != "x11" or props.get("Remote") == "yes"
            or props.get("Class", "user") != "user"):
        return None
    display = props.get("Display", "")
    if not re.fullmatch(r":\d+(\.\d+)?", display):
        return None
    if not Path(f"/tmp/.X11-unix/X{display[1:].split('.')[0]}").exists():
        return None
    return display, props.get("Desktop", "")


_x11_retry_at = 0.0


def refresh_session_env() -> None:
    """Repair the session variables again when this process has no display.

    A daemon started before the X session has none; the desktop tools call
    this so they find the session once it exists. Cheap when a display is
    known, and at most every 2 seconds otherwise.
    """
    global _x11_retry_at
    import time

    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return
    now = time.monotonic()
    if now < _x11_retry_at:
        return
    _x11_retry_at = now + 2.0
    fixed = ensure_session_env()
    if fixed:
        import logging

        logging.getLogger("pcbridge").warning(
            "Session environment found after startup; repaired: %s", ", ".join(fixed))


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
HYPRLAND = "hyprland"
CINNAMON = "cinnamon"
XFCE = "xfce"
UNKNOWN = "unknown"
# The desktops pcbridge's desktop tools are built for, as users know them.
DESKTOP_NAMES = {GNOME: "GNOME", KDE: "KDE Plasma", HYPRLAND: "Hyprland",
                 CINNAMON: "Cinnamon", XFCE: "Xfce"}
# The desktops the native helper drives through X11 (stage 7). They count
# only in an X11 session; the others only on Wayland.
X11_KINDS = frozenset({CINNAMON, XFCE})
WAYLAND_KINDS = frozenset({GNOME, KDE, HYPRLAND})


def grant_context(state_dir: Path) -> dict:
    """Public session identity for resident grant routing, without credentials."""
    kind = desktop_kind()
    instance = hyprland_instance() if kind == HYPRLAND else None
    context = {
        "version": 1, "state_dir": str(Path(state_dir).resolve()),
        "compositor": kind,
        "wayland_display": instance["wl_socket"] if instance else "",
        "hyprland_instance": instance["instance"] if instance else "",
    }
    if kind in X11_KINDS:
        context["x11_display"] = os.environ.get("DISPLAY", "")
    return context


def _desktop_tokens(env) -> list[str]:
    desktop = env.get("XDG_CURRENT_DESKTOP") or env.get("XDG_SESSION_DESKTOP") or ""
    return [t.strip() for t in desktop.split(":") if t.strip()]


def _kind_from_tokens(tokens: list[str]) -> str:
    # Zorin and Ubuntu say "zorin:GNOME" / "ubuntu:GNOME"; Plasma says "KDE";
    # Linux Mint and Debian say "X-Cinnamon", Xfce says "XFCE". Cinnamon is
    # looked for first: a Cinnamon session may name GNOME as well.
    if any(t.lower() in ("x-cinnamon", "cinnamon") for t in tokens):
        return CINNAMON
    if any(t.lower() == "xfce" for t in tokens):
        return XFCE
    if any("gnome" in t.lower() for t in tokens):
        return GNOME
    if any(t.lower() == "kde" for t in tokens):
        return KDE
    if any(t.lower() == "hyprland" for t in tokens):
        return HYPRLAND
    return UNKNOWN


def _hyprctl_instances(env: dict[str, str]) -> list[dict]:
    """Ask Hyprland for this user's live instances, without dispatching actions."""
    try:
        proc = subprocess.run(
            ["hyprctl", "-j", "instances"], capture_output=True, text=True,
            timeout=2, env={**os.environ, **env},
        )
        data = json.loads(proc.stdout) if proc.returncode == 0 else []
        return data if isinstance(data, list) else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def _owned_socket(path: Path) -> bool:
    try:
        info = path.stat()
        return info.st_uid == os.getuid() and stat.S_ISSOCK(info.st_mode)
    except OSError:
        return False


def hyprland_instance(env: dict[str, str] | None = None) -> dict | None:
    """The one IPC instance matching this session, or None when ambiguous."""
    e = os.environ if env is None else env
    runtime = _runtime_dir(e)  # type: ignore[arg-type]
    if runtime is None:
        return None
    try:
        if runtime.stat().st_uid != os.getuid():
            return None
    except OSError:
        return None
    signature = e.get("HYPRLAND_INSTANCE_SIGNATURE") or ""
    display = e.get("WAYLAND_DISPLAY") or ""
    candidates = []
    for item in _hyprctl_instances(e):
        if not isinstance(item, dict):
            continue
        found = item.get("instance")
        wl_socket = item.get("wl_socket")
        if not isinstance(found, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", found):
            continue
        if not isinstance(wl_socket, str) or not re.fullmatch(r"wayland-[0-9]+", wl_socket):
            continue
        if signature and found != signature:
            continue
        if display and wl_socket != display:
            continue
        if not _owned_socket(runtime / "hypr" / found / ".socket.sock"):
            continue
        if not _owned_socket(runtime / wl_socket):
            continue
        candidates.append(item)
    return candidates[0] if len(candidates) == 1 else None


def session_type_fits(kind: str, session_type: str) -> bool:
    """Does `kind` run in a session of `session_type` ("" = not known)?

    GNOME, Plasma and Hyprland are driven through Wayland compositor
    interfaces, Cinnamon and Xfce through X11. An unknown type is not a
    verdict (stdio clients and systemd often pass none).
    """
    session_type = (session_type or "").strip().lower()
    if not session_type:
        return True
    if kind in X11_KINDS:
        return session_type == "x11"
    return session_type == "wayland"


def support_note(env: dict[str, str] | None = None) -> str:
    """Name an unsupported session, or "" (Step 8 of 2.0).

    pcbridge's desktop tools are built on GNOME (Mutter, GNOME Shell), KDE
    Plasma (KWin) and Hyprland on Wayland, and on Cinnamon and Xfce on X11.
    In another session they refuse (fail closed); this says why in words, so
    a refusal is not blamed on the screen lock. An EMPTY value is not a
    verdict: stdio clients and systemd often pass none (measured, see
    CLAUDE.md), and `ensure_session_env` fills it in from the Wayland socket.
    """
    e = os.environ if env is None else env
    session_type = (e.get("XDG_SESSION_TYPE") or "").strip().lower()
    tokens = _desktop_tokens(e)
    kind = _kind_from_tokens(tokens) if tokens else UNKNOWN
    problems = []
    misfit = kind != UNKNOWN and not session_type_fits(kind, session_type)
    foreign = kind == UNKNOWN and session_type not in ("", "wayland", "x11")
    if misfit or foreign:
        name = {"x11": "X11", "wayland": "Wayland"}.get(session_type, session_type)
        problems.append(f"{'an' if name[0].lower() in 'aeiox' else 'a'} {name} session")
    if tokens and kind == UNKNOWN:
        problems.append(f"the {e.get('XDG_CURRENT_DESKTOP', '').strip()} desktop")
    if not problems:
        return ""
    return (
        f"Unsupported session: {' on '.join(problems)}. pcbridge's desktop tools "
        "need GNOME, KDE Plasma, or Hyprland on Wayland, or Cinnamon or Xfce on X11, and "
        "refuse here; the shell, file, tmux and agent tools work anywhere."
    )


# Compositor versions pcbridge has actually run on. Others are reported as
# untested rather than refused.
TESTED_SHELL_MAJORS = frozenset({"46", "50"})
TESTED_PLASMA_MAJORS = frozenset({"6"})
TESTED_HYPRLAND_VERSIONS: frozenset[str] = frozenset({"0.56.2"})
# Cinnamon 5.6, 6.0 and 6.4 and Xfce 4.18 and 4.20 passed the X11 checks
# (Debian 12 and 13 VMs, a Linux Mint 21.3 live session).
TESTED_X11_MAJORS: dict[str, frozenset[str]] = {CINNAMON: frozenset({"5", "6"}),
                                                 XFCE: frozenset({"4"})}
_PLATFORM_TTL = 60.0
_platform_cache: tuple[float, dict] | None = None
_kind_cache: tuple[float, tuple[str, str, str], str] | None = None


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
    """Return GNOME, KDE, HYPRLAND, CINNAMON, XFCE, or UNKNOWN for this session.

    A desktop in the wrong kind of session is UNKNOWN: Cinnamon's Wayland
    session, or GNOME and Plasma on X11 (`session_type_fits`), the same rule
    the native helper applies.

    `XDG_CURRENT_DESKTOP` decides when it is set. When it is empty (stdio
    clients and systemd often pass none), the compositor's name on the session
    bus decides: GNOME Shell owns `org.gnome.Shell`, KWin owns `org.kde.KWin`.
    The bus answer is cached for a minute, like `platform_summary`.
    """
    import time

    global _kind_cache
    e = os.environ if env is None else env
    tokens = _desktop_tokens(e)
    if tokens and not e.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return _fitting(_kind_from_tokens(tokens), e)
    now = time.monotonic()
    cache_key = (e.get("XDG_RUNTIME_DIR", ""), e.get("WAYLAND_DISPLAY", ""),
                 e.get("HYPRLAND_INSTANCE_SIGNATURE", ""))
    if (env is None and _kind_cache and _kind_cache[1] == cache_key
            and now - _kind_cache[0] < _PLATFORM_TTL):
        return _kind_cache[2]
    instance = hyprland_instance(e)
    if instance is not None:
        kind = HYPRLAND
    elif e.get("HYPRLAND_INSTANCE_SIGNATURE"):
        # An invalid instance signature must not select the host GNOME bus.
        kind = UNKNOWN
    elif tokens:
        kind = _fitting(_kind_from_tokens(tokens), e)
    else:
        kind = _kind_from_names(_bus_names())
    if env is None:
        _kind_cache = (now, cache_key, kind or UNKNOWN)
    return kind or UNKNOWN


def _fitting(kind: str, env) -> str:
    """`kind`, or UNKNOWN when the session type says it cannot be driven here.

    With no session type an X11 desktop needs `DISPLAY` and no
    `WAYLAND_DISPLAY`; the Wayland desktops keep their old rule (an empty
    type never refused them).
    """
    session_type = (env.get("XDG_SESSION_TYPE") or "").strip().lower()
    if kind in X11_KINDS and not session_type:
        return kind if env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY") else UNKNOWN
    return kind if session_type_fits(kind, session_type) else UNKNOWN


def x11_session(env=None) -> bool:
    """Is this a Cinnamon or Xfce X11 session? From the environment alone:
    no bus or compositor is asked, so it is cheap enough for every check."""
    e = os.environ if env is None else env
    tokens = _desktop_tokens(e)
    return bool(tokens) and _fitting(_kind_from_tokens(tokens), e) in X11_KINDS


def _hyprland_version(env: dict[str, str]) -> str:
    instance = hyprland_instance(env)
    if instance is None:
        return ""
    try:
        proc = subprocess.run(
            ["hyprctl", "-i", instance["instance"], "-j", "version"],
            capture_output=True, text=True, timeout=2,
            env={**os.environ, **env},
        )
        data = json.loads(proc.stdout) if proc.returncode == 0 else {}
        version = data.get("version") if isinstance(data, dict) else None
        return version if isinstance(version, str) else ""
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""


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
    kind = desktop_kind(e)
    shell = plasma = hyprland = ""
    if kind == GNOME:
        raw = _busctl("get-property", "org.gnome.Shell", "/org/gnome/Shell",
                      "org.gnome.Shell", "ShellVersion")
        m = re.search(r'"([^"]+)"', raw)
        shell = m.group(1) if m else ""
    if kind == KDE:
        plasma = _plasma_version()
    if kind == HYPRLAND:
        hyprland = _hyprland_version(e)
    x11_desktop = ""
    if kind in X11_KINDS:
        from ..platforminfo import desktop_version

        x11_desktop = desktop_version(DESKTOP_NAMES[kind])
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
    elif kind == HYPRLAND:
        if not hyprland:
            notes.append("Hyprland IPC did not report its version in this session.")
        elif hyprland not in TESTED_HYPRLAND_VERSIONS:
            notes.append(f"Hyprland {hyprland} is untested; capabilities must be probed.")
    elif kind == GNOME and not shell:
        notes.append("GNOME Shell did not answer on the session bus.")
    elif kind in X11_KINDS and x11_desktop and \
            x11_desktop.split(".")[0] not in TESTED_X11_MAJORS[kind]:
        notes.append(f"{DESKTOP_NAMES[kind]} {x11_desktop} is untested; the capabilities "
                     "above are what was actually found.")
    elif kind == GNOME and shell.split(".")[0] not in TESTED_SHELL_MAJORS:
        notes.append(f"GNOME Shell {shell} is untested (pcbridge was tested on "
                     f"{', '.join(sorted(TESTED_SHELL_MAJORS))}); the capabilities "
                     "above are what was actually found.")
    result = {
        "environment": kind,
        "gnome_shell": shell or None,
        "plasma": plasma or None,
        "hyprland": hyprland or None,
        "x11_desktop": x11_desktop or None,
        "session_type": e.get("XDG_SESSION_TYPE") or None,
        "desktop": e.get("XDG_CURRENT_DESKTOP") or None,
        "screencast": "org.gnome.Mutter.ScreenCast" in names,
        "remote_desktop": "org.gnome.Mutter.RemoteDesktop" in names,
        "notes": notes,
    }
    if env is None:
        _platform_cache = (now, result)
    return result
