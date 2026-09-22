"""Monitor geometrisi — koordinat uzayinin TEK kaynagi.

pcbridge'te bir masaustu koordinati her zaman **global tuval uzayindadir**:
sol ust kose (0, 0), sag alt kose (toplam_genislik-1, toplam_yukseklik-1).

IKI UZAY AYRI (Task 7.1). Kompozitorun kendi koordinati ("platform origin")
negatif olabilir: soldaki monitor x=-1920'de durabilir. Pcbridge tuvali her
zaman butun monitorleri kapsayan dikdortgenin sol ustunden baslar, yani
tablo okunurken bir kez oteleniyor (`_normalize_origin`). Platform
koordinati `Monitor.platform` ile duruyor; hicbir hesap iki uzayi
karistirmasin diye disari yalnizca tuval koordinati cikiyor. Bu makinede iki
monitor de (0,0)'dan basliyor, yani oteleme sifir.
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

import dataclasses
import json
import math
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
    # Mutter'in donusum kodu: 0=normal, 1=90, 2=180, 3=270, 4-7 aynalanmis.
    # `width`/`height` ZATEN takas edilmis halde; bu alan yine de duruyor,
    # cunku 180 derece boyutlari degistirmez ama koordinat eslemesini
    # degistirir -- `topology_id` bunu gormek zorunda.
    transform: int = 0
    # Fiziksel kimlik. Connector adi KARARLI DEGIL: bu makinede geometri hic
    # degismeden DP-1/DP-2 iken DP-3/DP-4 oldu (olculdu 2026-09-12, hem
    # Mutter hem `xrandr --listmonitors` ayni seyi soyledi).
    serial: str = ""
    # Kompozitorun kendi koordinati (Task 7.1). `x`/`y` tuval uzayindadir ve
    # tuval her zaman (0,0)'dan baslar; platform origin negatif olabilir.
    # Verilmemisse ikisi ayni sayilir -- eski kayitlar ve testler icin.
    platform_x: int | None = None
    platform_y: int | None = None

    @property
    def platform(self) -> tuple[int, int]:
        """Kompozitorun bu monitor icin bildirdigi konum."""
        return (
            self.x if self.platform_x is None else self.platform_x,
            self.y if self.platform_y is None else self.platform_y,
        )

    @property
    def source_pixel_size(self) -> tuple[int, int]:
        """Monitorun ham piksel boyutu: mantiksal boyut x olcek.

        Olcek 1 iken mantiksal boyutun aynisi. Kesirli olcekte yuvarlama
        kurali tabloyla ayni (`round_half_away`), yoksa bir piksel sessizce
        ayrisirdi.
        """
        return (
            round_half_away(self.width * self.scale),
            round_half_away(self.height * self.scale),
        )

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
        star = " (primary)" if self.primary else ""
        return (
            f"{self.index}: {self.connector}{star} · {self.width}x{self.height} "
            f"@ ({self.x}, {self.y}) · scale {self.scale:g}"
        )


# ------------------------------------------------------- ortak cozumleme
# Donusum kodlarindan hangileri mod eksenlerini takas eder (90 ve 270, ve
# bunlarin aynalanmis esleri).
_SWAPS_AXES = frozenset({1, 3, 5, 7})

TOPOLOGY_VERSION = "v1"


def round_half_away(value: float) -> int:
    """Yarim pikselde SIFIRDAN UZAGA yuvarla (960.5 -> 961).

    Python'in yerlesik `round()`u BANKACI yuvarlamasi yapar (960.5 -> 960,
    961.5 -> 962) ve Rust'in `f64::round()`u yapmaz. Kural burada acikca
    yaziliyor, cunku aksi halde iki dil kesirli olcekte bir piksel ayrisir
    ve bunu hicbir sey haber vermez.

    Mutter'in kendi yarim-sinir davranisi OLCULMEDI: bu makinedeki iki
    monitorun da olcegi 1.0, yani bolme her zaman tam. Kesirli olcek
    donanimi olan biri bunu dogrulamali.
    """
    return int(math.floor(value + 0.5)) if value >= 0 else -int(math.floor(-value + 0.5))


def resolve_state(state: dict) -> list[Monitor]:
    """Notr ekran durumundan sirali monitor tablosu. I/O YOK.

    Girdi Mutter `GetCurrentState` cevabinin ANLAMI: `physical` girdileri
    connector, kimlik ve modlari; `logical` girdileri konum, olcek, donusum
    ve birincil bayragini tasir. Bu ayrim bilincli -- ayni fonksiyonu hem
    `busctl` JSON'u hem test fixture'i besliyor, ve Rust tarafi ayni kurallari
    ayni fixture uzerinde tekrar uretiyor.

    HICBIR SEY TAHMIN EDILMEZ. Gecerli mod yoksa ilk mod secilmez, bilinmeyen
    connector atlanmaz, sifir olcekle bolunmez: hepsi `MonitorError`.
    """
    physical = state.get("physical") or []
    logical = state.get("logical") or []

    modes: dict[str, tuple[int, int]] = {}
    names: dict[str, str] = {}
    serials: dict[str, str] = {}
    for entry in physical:
        connector = str(entry.get("connector") or "")
        if not connector:
            continue
        names[connector] = str(entry.get("display_name") or "") or str(
            entry.get("product") or ""
        )
        serials[connector] = str(entry.get("serial") or "")
        for mode in entry.get("modes") or []:
            if mode.get("is_current"):
                modes[connector] = (int(mode["width"]), int(mode["height"]))
                break

    out: list[Monitor] = []
    for lm in logical:
        connectors = lm.get("connectors") or []
        if not connectors:
            raise MonitorError(
                "A logical monitor reported no connector; which mode applies "
                "cannot be known."
            )
        connector = str(connectors[0])
        if connector not in modes:
            raise MonitorError(f"no current mode found for {connector}")
        scale = float(lm.get("scale") or 0.0)
        if scale <= 0:
            raise MonitorError(f"invalid scale for {connector}: {scale!r}")
        transform = int(lm.get("transform") or 0)
        mw, mh = modes[connector]
        if transform in _SWAPS_AXES:
            mw, mh = mh, mw
        width = round_half_away(mw / scale)
        height = round_half_away(mh / scale)
        if width <= 0 or height <= 0:
            raise MonitorError(
                f"invalid logical size for {connector}: {width}x{height}"
            )
        out.append(
            Monitor(
                index=0,  # asagida siralandiktan sonra atanir
                connector=connector,
                x=int(lm.get("x") or 0),
                y=int(lm.get("y") or 0),
                width=width,
                height=height,
                scale=scale,
                primary=bool(lm.get("primary")),
                name=names.get(connector, ""),
                transform=transform,
                serial=serials.get(connector, ""),
            )
        )
    if not out:
        raise MonitorError("Mutter hic mantiksal monitor bildirmedi")
    return _ordered(_normalize_origin(out))


def _normalize_origin(mons: list[Monitor]) -> list[Monitor]:
    """Tuvalin sol ustunu (0,0) yap; platform koordinatini alanda sakla.

    Kompozitor negatif bir origin bildirebilir (soldaki monitor x=-1920).
    Tuval koordinati negatif olsaydi sanal fare aygitinin mutlak ekseni
    (0..genislik-1) o noktayi hic gosteremezdi ve kirpma kutulari goruntunun
    disina duserdi. Oteleme TEK YERDE, tablo okunurken yapiliyor.
    """
    left = min(m.x for m in mons)
    top = min(m.y for m in mons)
    return [
        dataclasses.replace(
            m, x=m.x - left, y=m.y - top, platform_x=m.x, platform_y=m.y
        )
        for m in mons
    ]


def topology_id(mons: list[Monitor] | None = None) -> str:
    """Ekran duzeninin kararli kimligi — onbellek gecerli mi sorusu icin.

    KANONIK DIZE, hash degil: carpisma olmaz, gozle okunur ve iki dil arasinda
    birebir karsilastirilabilir. Connector ADI BILINCLI OLARAK DISARIDA --
    bu makinede geometri hic degismeden DP-1/DP-2 iken DP-3/DP-4 oldu; ada
    bagli bir kimlik her yeniden adlandirmada "duzen degisti" derdi.

    `transform` iceride, cunku 180 derece donus genislik/yukseklik degistirmez
    ama goruntuyu ve koordinat eslemesini degistirir.
    """
    mons = list_monitors() if mons is None else mons
    parts = [
        f"{m.x},{m.y},{m.width},{m.height},{m.scale:.4f},{m.transform},"
        f"{1 if m.primary else 0}"
        for m in mons
    ]
    return "|".join([TOPOLOGY_VERSION, *parts])


# ---------------------------------------------------------------- okuma yollari
def _mutter_state(data: list) -> dict:
    """busctl JSON'unu `resolve_state`in bekledigi notr semaya cevir.

    Donen yapi: (serial, monitorler, mantiksal_monitorler, ozellikler)
      monitor          = [(connector, vendor, product, serial), [modlar], props]
      mod              = [id, genislik, yukseklik, tazeleme, tercih_olcek, olcekler, props]
      mantiksal monitor= [x, y, olcek, donusum, birincil, [(connector,...)], props]

    Burasi YALNIZCA tasima adaptoru. Kural yok: hangi mod gecerli, donusum
    eksenleri takas eder mi, sira nasil kurulur -- hepsi `resolve_state`te,
    cunku Rust tarafi ayni kurallari ayni fixture uzerinde tekrar uretiyor
    ve iki kopya kural er gec ayrisir.
    """
    physical, logical = data[1], data[2]
    return {
        "physical": [
            {
                "connector": mon[0][0],
                "vendor": mon[0][1],
                "product": mon[0][2],
                "serial": mon[0][3],
                "display_name": _prop(mon[2], "display-name") or mon[0][2] or "",
                "modes": [
                    {
                        "width": int(mode[1]),
                        "height": int(mode[2]),
                        "is_current": bool(_prop(mode[6], "is-current")),
                    }
                    for mode in mon[1]
                ],
            }
            for mon in physical
        ],
        "logical": [
            {
                "x": int(lm[0]),
                "y": int(lm[1]),
                "scale": float(lm[2]),
                "transform": int(lm[3]),
                "primary": bool(lm[4]),
                "connectors": [c[0] for c in (lm[5] or [])],
            }
            for lm in logical
        ],
    }


def _from_mutter() -> list[Monitor]:
    """Mutter.DisplayConfig.GetCurrentState -> mantiksal monitorler."""
    proc = subprocess.run(_BUSCTL, capture_output=True, text=True, timeout=10)
    if proc.returncode != 0:
        raise MonitorError((proc.stderr or "busctl failed").strip())
    return resolve_state(_mutter_state(json.loads(proc.stdout)["data"]))


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
        raise MonitorError((proc.stderr or "xrandr failed").strip())
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
    # `replace` ile: yeni bir alan eklendiginde burada unutulup sessizce
    # dusmesin (platform koordinati bir kez oyle dustu).
    return [
        dataclasses.replace(m, index=i) for i, m in enumerate(ordered, start=1)
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
                f"The monitor table could not be read. Mutter: {exc}. "
                "The xrandr fallback failed too."
            ) from exc
    mons = _ordered(mons)
    _cache = (now, mons)
    return mons


def invalidate_cache() -> None:
    global _cache
    _cache = None


def canvas_size(mons: list[Monitor] | None = None) -> tuple[int, int]:
    """Butun monitorleri kapsayan tuvalin boyutu (gnome-screenshot ile ayni).

    Tablo okunurken sol ust kose (0,0)'a otelendigi icin bu, kapsayan
    dikdortgenin boyutudur (`_normalize_origin`).
    """
    mons = mons or list_monitors()
    return (
        max(m.x + m.width for m in mons) - min(m.x for m in mons),
        max(m.y + m.height for m in mons) - min(m.y for m in mons),
    )


def platform_origin(mons: list[Monitor] | None = None) -> tuple[int, int]:
    """Tuvalin (0,0) noktasinin kompozitor koordinatindaki karsiligi."""
    mons = mons or list_monitors()
    return (
        min(m.platform[0] for m in mons),
        min(m.platform[1] for m in mons),
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
        raise MonitorError(f"Invalid monitor: {spec!r}")
    if isinstance(spec, int):
        for m in mons:
            if m.index == spec:
                return m
        raise MonitorError(
            f"There is no monitor {spec}. Valid: {', '.join(str(m.index) for m in mons)}"
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
        f"Monitor '{spec}' not found. Valid: "
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
        return f"The monitor table could not be read: {exc}"
    w, h = canvas_size(mons)
    lines = [f"canvas: {w}x{h} · {len(mons)} monitor(s), numbered by position (left to right, then top to bottom)"]
    lines += [f"  {m.describe()}" for m in mons]
    p = primary(mons)
    lines.append(
        f"  primary monitor (GNOME panel menus and the Super overview): {p.index}/{p.connector}"
    )
    return "\n".join(lines)
