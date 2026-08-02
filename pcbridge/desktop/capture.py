"""Ekran goruntusu — yakala, monitor basina kir, olcekle.

NEDEN MONITOR BASINA KIRPILIYOR
    Bu makinede tuval 3840x1080: yan yana iki 1920x1080 monitor tek bir goruntu
    olarak yakalaniyor. Tek parca halinde uzun kenardan 1280'e indirilirse her
    monitor ~640x180 kaliyor ve buton yazilari okunmaz oluyor; 3,55:1 en-boy
    orani gorsel modeller icin de kotu. Bu yuzden **once kirp, sonra olcekle**.

VARSAYILAN monitor="all"
    Imlecin hangi monitorde oldugunu Wayland'de disaridan sormak mumkun degil,
    odaktaki pencereye guvenmek de kirilgan (masaustundeyken odakta pencere
    yok). Bu yuzden varsayilan olarak HER monitor ayri bir goruntu olarak
    donuyor: tahmin etmeye gerek kalmiyor.

    `monitor="focused"` OLCULDU VE YAPILMADI (2026-08-02): planin onerdigi
    `org.gnome.Shell.Introspect.GetWindows` bu makinede "Access denied" veriyor
    (GNOME 46 arayuzu izin listesindeki uygulamalara kapatmis). Odak bilgisi
    ileride AT-SPI'dan gelebilir (D bolumu).

GLOBAL OFSET
    Her `Shot` kirpildigi kutunun **global ofsetini** tasir. Bu bilgi
    kaybolursa ikinci monitore yapilan her tiklama 1920 piksel sasar ve hata
    hicbir yerde gorunmez. Goruntudeki bir noktadan global koordinata donus:

        global_x = ofset_x + goruntu_x / olcek

BACKEND
    Bugun tek yol `gnome-screenshot` (41.0, olculdu: cikis 0, 3840x1080 gercek
    yakalama). Paket GNOME 49'da bozulmus gorunuyor, yani bir gun ScreenCast
    portali + PipeWire yedegine gecmek gerekebilir; kod bu yuzden backend
    zinciriyle yazildi ama bugun PipeWire yazilmadi.
"""

from __future__ import annotations

import secrets
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import monitors as monitorslib

try:  # Pillow olmadan kirpma/olcekleme yapilamaz; yalnizca bu araclar kapanir
    from PIL import Image

    PIL_AVAILABLE = True
    PIL_IMPORT_ERROR = ""
except Exception as _exc:  # pragma: no cover - kuruluysa calismaz
    PIL_AVAILABLE = False
    PIL_IMPORT_ERROR = str(_exc)
    Image = None  # type: ignore[assignment]


GNOME_SCREENSHOT = "gnome-screenshot"
# 3840x1080 yakalama olculdu: ~1 saniye. 20 s, kompozitor gecici olarak
# takildiginda bile yeterli ve MCP'nin 110 saniyelik sinirinin cok altinda.
GRAB_TIMEOUT = 20


class CaptureError(RuntimeError):
    """Ekran goruntusu alinamadi."""


@dataclass(frozen=True)
class Shot:
    """Tek bir kirpilmis (ve muhtemelen olceklenmis) goruntu.

    `offset` **None ise** bu goruntuden koordinat turetilemez: `monitor="window"`
    yolunda `gnome-screenshot -w` pencerenin ekranin neresinde oldugunu
    bildirmiyor.
    """

    path: Path
    monitor: monitorslib.Monitor | None
    offset: tuple[int, int] | None
    size: tuple[int, int]  # kirpilmis, olceklenmemis
    scaled: tuple[int, int]  # dosyaya yazilan
    scale: float  # scaled / size

    @property
    def label(self) -> str:
        if self.monitor is None:
            return "odaktaki pencere"
        star = " (birincil)" if self.monitor.primary else ""
        return f"{self.monitor.index} · {self.monitor.connector}{star}"

    def to_global(self, x: int, y: int) -> tuple[int, int] | None:
        """Goruntudeki piksel -> global tuval koordinati."""
        if self.offset is None:
            return None
        return (
            self.offset[0] + round(x / self.scale),
            self.offset[1] + round(y / self.scale),
        )


# --------------------------------------------------------------------- durum
def available() -> tuple[bool, str]:
    """(kullanilabilir mi, degilse Turkce gerekce)."""
    if not PIL_AVAILABLE:
        return False, (
            f"python paketi `Pillow` yok ({PIL_IMPORT_ERROR}). "
            "Kurulum: ./.venv/bin/pip install -r requirements.txt"
        )
    if not shutil.which(GNOME_SCREENSHOT):
        return False, (
            f"`{GNOME_SCREENSHOT}` kurulu degil. "
            "Kurulum: sudo apt install gnome-screenshot"
        )
    return True, ""


def backend_name() -> str:
    return GNOME_SCREENSHOT


# ------------------------------------------------------------------ yakalama
def _run_grab(args: list[str], target: Path) -> None:
    proc = subprocess.run(
        args, capture_output=True, text=True, timeout=GRAB_TIMEOUT, check=False
    )
    if proc.returncode != 0:
        raise CaptureError(
            f"{GNOME_SCREENSHOT} basarisiz (cikis {proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[:200]}"
        )
    # gnome-screenshot bazen cikis 0 verip dosyayi hic yazmiyor (oturum ortami
    # eksikse); sessizce bos goruntu dondurmektense burada patla.
    if not target.exists() or target.stat().st_size == 0:
        raise CaptureError(
            f"{GNOME_SCREENSHOT} cikis 0 verdi ama dosya olusmadi. Servis "
            "grafik oturumun icinden calisiyor mu? (`./doctor.sh`)"
        )


def _grab_canvas(tmpdir: Path, include_pointer: bool) -> Path:
    """Tum tuvali yakala (3840x1080)."""
    out = tmpdir / "canvas.png"
    args = [GNOME_SCREENSHOT, "-f", str(out)]
    if include_pointer:
        args.append("-p")
    _run_grab(args, out)
    return out


def _grab_window(tmpdir: Path, include_pointer: bool) -> Path:
    """Yalnizca odaktaki pencere. Global konumu BILINMIYOR."""
    out = tmpdir / "window.png"
    args = [GNOME_SCREENSHOT, "-w", "-f", str(out)]
    if include_pointer:
        args.append("-p")
    _run_grab(args, out)
    return out


# --------------------------------------------------------- kirpma/olcekleme
def _scaled_size(w: int, h: int, long_edge: int) -> tuple[int, int]:
    """Uzun kenari `long_edge`e indiren boyut. 0 ya da zaten kucukse aynen."""
    if long_edge <= 0:
        return (w, h)
    longest = max(w, h)
    if longest <= long_edge:
        return (w, h)
    ratio = long_edge / longest
    return (max(1, round(w * ratio)), max(1, round(h * ratio)))


def _write_crop(
    canvas: "Image.Image",
    box: tuple[int, int, int, int] | None,
    dest: Path,
    long_edge: int,
) -> tuple[tuple[int, int], tuple[int, int], float]:
    """Kirp, SONRA olcekle, PNG yaz. -> (kirpilmis, yazilan, olcek)"""
    img = canvas.crop(box) if box else canvas
    cw, ch = img.size
    sw, sh = _scaled_size(cw, ch, long_edge)
    if (sw, sh) != (cw, ch):
        img = img.resize((sw, sh), Image.LANCZOS)
    # Saydamlik PNG'yi buyutuyor ve ekran goruntusunde anlami yok.
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.save(dest, format="PNG", optimize=True)
    return (cw, ch), (sw, sh), (sw / cw if cw else 1.0)


# ------------------------------------------------------------------ genel API
def capture(
    monitor: int | str | None = "all",
    out_dir: Path | None = None,
    scale_long_edge: int = 1280,
    include_pointer: bool = True,
) -> list[Shot]:
    """Ekran goruntusu al ve `out_dir` altina PNG(ler) yaz.

    `monitor`:
        "all" (varsayilan)  her monitor ayri goruntu
        1 / 2 / "DP-1" / "primary"   tek monitor
        "window"            yalnizca odaktaki pencere (ofset URETMEZ)
    """
    ok, why = available()
    if not ok:
        raise CaptureError(why)

    out_dir = Path(out_dir) if out_dir else Path(tempfile.gettempdir())
    out_dir.mkdir(parents=True, exist_ok=True)
    # Rastgele son ek SART, sus degil: damga saniye cozunurlukte, yani ayni
    # saniyedeki iki yakalama ayni dosyaya yazardi. O zaman yayimlanmis eski
    # bir /shot baglantisi sessizce DAHA YENI bir ekran goruntusu gostermeye
    # baslar -- olculdu (koordinat testinde taban goruntu eziliyordu).
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"

    want_window = isinstance(monitor, str) and monitor.strip().lower() == "window"
    with tempfile.TemporaryDirectory(prefix="pcbridge-shot-") as td:
        tmpdir = Path(td)

        if want_window:
            raw = _grab_window(tmpdir, include_pointer)
            with Image.open(raw) as canvas:
                dest = out_dir / f"{stamp}-window.png"
                size, scaled, scale = _write_crop(canvas, None, dest, scale_long_edge)
            return [
                Shot(
                    path=dest,
                    monitor=None,
                    offset=None,
                    size=size,
                    scaled=scaled,
                    scale=scale,
                )
            ]

        # Monitor tablosu monitors.py'dan gelir; burada ikinci bir okuma YOK.
        mons = monitorslib.list_monitors()
        want_all = monitor is None or (
            isinstance(monitor, str) and monitor.strip().lower() in ("all", "hepsi")
        )
        targets = mons if want_all else [monitorslib.resolve(monitor, mons)]

        raw = _grab_canvas(tmpdir, include_pointer)
        shots: list[Shot] = []
        with Image.open(raw) as canvas:
            cw, ch = canvas.size
            expected = monitorslib.canvas_size(mons)
            if (cw, ch) != expected:
                # Monitor duzeni yakalama sirasinda degismis olabilir. Sessizce
                # yanlis yerden kirpmaktansa haber ver.
                raise CaptureError(
                    f"Yakalanan tuval {cw}x{ch}, monitor tablosu ise "
                    f"{expected[0]}x{expected[1]} diyor. Monitor duzeni "
                    "degismis olabilir; tekrar deneyin."
                )
            for mon in targets:
                dest = out_dir / f"{stamp}-m{mon.index}-{mon.connector}.png"
                size, scaled, scale = _write_crop(
                    canvas, mon.bbox, dest, scale_long_edge
                )
                shots.append(
                    Shot(
                        path=dest,
                        monitor=mon,
                        offset=(mon.x, mon.y),
                        size=size,
                        scaled=scaled,
                        scale=scale,
                    )
                )
    return shots
