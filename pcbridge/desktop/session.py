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

    return fixed


def describe(env: dict[str, str] | None = None) -> str:
    """Tani icin tek satir: oturum degiskenleri ne durumda."""
    e = os.environ if env is None else env
    runtime = _runtime_dir(e)  # type: ignore[arg-type]
    bus = e.get("DBUS_SESSION_BUS_ADDRESS", "")
    parts = [
        f"XDG_RUNTIME_DIR={e.get('XDG_RUNTIME_DIR') or '(yok)'}",
        "DBUS=" + ("gecerli" if _bus_ok(bus, runtime) else "GECERSIZ"),
        f"WAYLAND_DISPLAY={e.get('WAYLAND_DISPLAY') or '(yok)'}",
    ]
    return " · ".join(parts)
