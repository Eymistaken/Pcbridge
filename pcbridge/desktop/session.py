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
