"""Monitor geometrisi — koordinat uzayinin TEK kaynagi.

pcbridge'te bir masaustu koordinati her zaman **global tuval uzayindadir**:
sol ust kose (0, 0), sag alt kose (toplam_genislik-1, toplam_yukseklik-1).
Monitore ozel koordinat yalnizca disaridan `monitor=` ile gelir ve buradaki
`to_global()` ile bir kez global uzaya cevrilir. `UYGULAMA.md`'nin kurali:
"Bu donusumu tek bir yerde yap; iki yerde yapilirsa er gec biri unutulur ve
sessizce 1920 piksel sola tiklanir."

Numaralandirma **x konumuna gore soldan saga**, 1'den baslar. Bu makinede
birincil monitor SAGDAKI (DP-1, x=1920); "birincil once" gibi dogal gorunen
bir siralama kullanicinin "birinci ekran" beklentisiyle celisirdi.

Okuma yolu: Mutter'in D-Bus arayuzu, `busctl --json=short` ile. `gdbus`'in
GVariant metni ayristirmasi zor; `busctl` duz JSON veriyor ve ikisi de
sistemde hazir, yani yeni bir Python bagimliligi gerekmiyor.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass

_BUSCTL = [
    "busctl",
    "--user",
    "--json=short",
    "call",
    "org.gnome.Mutter.DisplayConfig",
    "/org/gnome/Mutter/DisplayConfig",
    "org.gnome.Mutter.DisplayConfig",
    "GetCurrentState",
]

# Monitor takilip cikarildiginda kendiliginden duzelsin diye her cagrida
# yeniden okunur; ard arda gelen cagrilar icin kisa bir onbellek yeterli.
_CACHE_TTL = 2.0
_cache: tuple[float, list["Monitor"]] | None = None


class MonitorError(RuntimeError):
    """Monitor tablosu okunamadi."""


@dataclass(frozen=True)
class Monitor:
    index: int  # 1'den, soldan saga
    connector: str  # "DP-1"
    x: int
    y: int
    width: int
    height: int
    scale: float
    primary: bool
    name: str = ""  # insan okunur ad, varsa

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        """Kirpma kutusu (left, top, right, bottom) — global uzayda."""
        return (self.x, self.y, self.x + self.width, self.y + self.height)

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height

    def describe(self) -> str:
        star = " (birincil)" if self.primary else ""
        return (
            f"{self.index}: {self.connector}{star} · {self.width}x{self.height} "
            f"@ ({self.x}, {self.y}) · olcek {self.scale:g}"
        )


# ---------------------------------------------------------------- okuma yollari
def _from_mutter() -> list[Monitor]:
    """Mutter.DisplayConfig.GetCurrentState -> mantiksal monitorler.

    Donen yapi: (serial, monitorler, mantiksal_monitorler, ozellikler)
      monitor          = [(connector, vendor, product, serial), [modlar], props]
      mod              = [id, genislik, yukseklik, tazeleme, tercih_olcek, olcekler, props]
      mantiksal monitor= [x, y, olcek, donusum, birincil, [(connector,...)], props]
    """
    proc = subprocess.run(_BUSCTL, capture_output=True, text=True, timeout=10)
    if proc.returncode != 0:
        raise MonitorError((proc.stderr or "busctl basarisiz").strip())
    data = json.loads(proc.stdout)["data"]
    physical, logical = data[1], data[2]

    # connector -> (mod_genislik, mod_yukseklik) ve insan okunur ad
    modes: dict[str, tuple[int, int]] = {}
    names: dict[str, str] = {}
    for mon in physical:
        connector = mon[0][0]
        names[connector] = _prop(mon[2], "display-name") or mon[0][2] or connector
        for mode in mon[1]:
            if _prop(mode[6], "is-current"):
                modes[connector] = (int(mode[1]), int(mode[2]))
                break

    out: list[Monitor] = []
    for lm in logical:
        x, y, scale, transform, primary = (
            int(lm[0]),
            int(lm[1]),
            float(lm[2]),
            int(lm[3]),
            bool(lm[4]),
        )
        connector = lm[5][0][0] if lm[5] else "?"
        mw, mh = modes.get(connector, (0, 0))
        if not mw or not mh:
            raise MonitorError(f"{connector} icin gecerli mod bulunamadi")
        if transform in (1, 3, 5, 7):  # 90/270 derece dondurulmus
            mw, mh = mh, mw
        out.append(
            Monitor(
                index=0,  # asagida siralandiktan sonra atanir
                connector=connector,
                x=x,
                y=y,
                width=round(mw / scale),
                height=round(mh / scale),
                scale=scale,
                primary=primary,
                name=names.get(connector, ""),
            )
        )
    if not out:
        raise MonitorError("Mutter hic mantiksal monitor bildirmedi")
    return out


_XRANDR_RE = re.compile(
    r"^\s*\d+:\s+\+(?P<primary>\*)?(?P<name>\S+)\s+"
    r"(?P<w>\d+)/\d+x(?P<h>\d+)/\d+\+(?P<x>\d+)\+(?P<y>\d+)"
)


def _from_xrandr() -> list[Monitor]:
    """Yedek yol: XWayland'in bildirdigi duzen.

    Mutter D-Bus'a ulasilamadiginda (ornegin arayuz adi degistiginde) devreye
    girer. XWayland tum mantiksal duzeni tek bir X ekrani olarak yansittigi
    icin koordinatlar ayni uzayda.
    """
    proc = subprocess.run(
        ["xrandr", "--listmonitors"], capture_output=True, text=True, timeout=10
    )
    if proc.returncode != 0:
        raise MonitorError((proc.stderr or "xrandr basarisiz").strip())
    out: list[Monitor] = []
    for line in proc.stdout.splitlines():
        m = _XRANDR_RE.match(line)
        if not m:
            continue
        out.append(
            Monitor(
                index=0,
                connector=m["name"],
                x=int(m["x"]),
                y=int(m["y"]),
                width=int(m["w"]),
                height=int(m["h"]),
                scale=1.0,
                primary=bool(m["primary"]),
            )
        )
    if not out:
        raise MonitorError("xrandr --listmonitors bos dondu")
    return out


def _prop(props: dict, key: str):
    """busctl JSON'unda ozellikler {"ad": {"type": ..., "data": ...}} seklinde."""
    entry = (props or {}).get(key)
    return entry.get("data") if isinstance(entry, dict) else None


def _ordered(mons: list[Monitor]) -> list[Monitor]:
    """x'e gore soldan saga sirala ve 1'den numaralandir."""
    ordered = sorted(mons, key=lambda m: (m.x, m.y))
    return [
        Monitor(
            index=i,
            connector=m.connector,
            x=m.x,
            y=m.y,
            width=m.width,
            height=m.height,
            scale=m.scale,
            primary=m.primary,
            name=m.name,
        )
        for i, m in enumerate(ordered, start=1)
    ]


# ------------------------------------------------------------------- genel API
def list_monitors(use_cache: bool = True) -> list[Monitor]:
    """Mantiksal monitorler, soldan saga sirali ve 1'den numarali."""
    global _cache
    now = time.monotonic()
    if use_cache and _cache and now - _cache[0] < _CACHE_TTL:
        return _cache[1]
    try:
        mons = _from_mutter()
    except Exception as exc:  # noqa: BLE001 — yedek yola dusmek istiyoruz
        try:
            mons = _from_xrandr()
        except Exception:
            raise MonitorError(
                f"Monitor tablosu okunamadi. Mutter: {exc}. "
                "xrandr yedegi de basarisiz."
            ) from exc
    mons = _ordered(mons)
    _cache = (now, mons)
    return mons


def invalidate_cache() -> None:
    global _cache
    _cache = None


def canvas_size(mons: list[Monitor] | None = None) -> tuple[int, int]:
    """Butun monitorleri kapsayan tuvalin boyutu (gnome-screenshot ile ayni)."""
    mons = mons or list_monitors()
    return (
        max(m.x + m.width for m in mons),
        max(m.y + m.height for m in mons),
    )


def resolve(spec: int | str | None, mons: list[Monitor] | None = None) -> Monitor:
    """`monitor=` parametresini tek bir monitore cevir.

    Kabul edilenler: 1/2/... (soldan saga sira), "DP-1" (baglanti adi),
    "primary"/"birincil". None -> birincil monitor.
    """
    mons = mons or list_monitors()
    if spec is None:
        return primary(mons)
    if isinstance(spec, bool):  # bool int'in alt sinifi; kazayla gecmesin
        raise MonitorError(f"Gecersiz monitor: {spec!r}")
    if isinstance(spec, int):
        for m in mons:
            if m.index == spec:
                return m
        raise MonitorError(
            f"Monitor {spec} yok. Gecerli: {', '.join(str(m.index) for m in mons)}"
        )
    key = str(spec).strip().lower()
    if key in ("primary", "birincil"):
        return primary(mons)
    if key.isdigit():
        return resolve(int(key), mons)
    for m in mons:
        if m.connector.lower() == key:
            return m
    raise MonitorError(
        f"Monitor '{spec}' bulunamadi. Gecerli: "
        + ", ".join(f"{m.index}/{m.connector}" for m in mons)
    )


def primary(mons: list[Monitor] | None = None) -> Monitor:
    mons = mons or list_monitors()
    for m in mons:
        if m.primary:
            return m
    return mons[0]


def to_global(
    x: int, y: int, monitor: int | str | None = None, mons: list[Monitor] | None = None
) -> tuple[int, int]:
    """Monitore ozel koordinati global tuval koordinatina cevir.

    `monitor` verilmezse koordinat ZATEN globaldir ve oldugu gibi doner —
    varsayilan yolun sessizce ofset eklememesi bilincli: araclarin sozlesmesi
    "koordinatlar globaldir", `monitor=` yalnizca kirpilmis bir goruntuye
    bakip koordinat ureten cagrilar icin.
    """
    if monitor is None:
        return (x, y)
    m = resolve(monitor, mons)
    return (m.x + x, m.y + y)


def find_monitor(x: int, y: int, mons: list[Monitor] | None = None) -> Monitor | None:
    """Global koordinat hangi monitorde? (Disinda kaliyorsa None.)"""
    for m in mons or list_monitors():
        if m.contains(x, y):
            return m
    return None


def describe() -> str:
    """Insan okunur monitor tablosu (arac ciktilari ve doctor.sh icin)."""
    try:
        mons = list_monitors()
    except MonitorError as exc:
        return f"Monitor tablosu okunamadi: {exc}"
    w, h = canvas_size(mons)
    lines = [f"tuval: {w}x{h} · {len(mons)} monitor (soldan saga numarali)"]
    lines += [f"  {m.describe()}" for m in mons]
    p = primary(mons)
    lines.append(
        f"  GNOME ust cubugu ve Super menusu birincil monitorde: {p.index}/{p.connector}"
    )
    return "\n".join(lines)
