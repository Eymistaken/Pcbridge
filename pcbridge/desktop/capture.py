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

GLOBAL OFSET VE CEKIM KIMLIGI
    Her `Shot` kirpildigi kutunun **global ofsetini** tasir. Bu bilgi
    kaybolursa ikinci monitore yapilan her tiklama 1920 piksel sasar ve hata
    hicbir yerde gorunmez. Goruntudeki bir noktadan global koordinata donus:

        global_x = ofset_x + goruntu_x / olcek

    BU HESABI ARTIK MODEL YAPMIYOR. Her cekim PNG'nin yaninda `<id>.json`
    olarak kaydediliyor; `mouse`, `computer_batch` ve `pcb-do` bir
    `shot="m2-a1b2c3"` alip donusumu kendileri yapiyor. Sebep: zayif modeller
    bu bolmeyi tutturamiyor, hedefin kenarina tikliyor ve bazen ofset/olcek
    bilgisini tamamen kaybediyor. Donusumun TEK yeri asagidaki `to_global()`
    -- ikinci bir kopya cikarsa biri gunun birinde guncellenmez ve sessizce
    1920 piksel sola tiklanir.

    `monitor=` yolu DURUYOR ve anlami degismedi: monitore ozel ama TAM
    COZUNURLUK koordinati. `shot=` ondan farkli, cunku olcegi de biliyor;
    ikisi birlikte verilemez.

IKI BACKEND
    1. **Ekran yayini** (`screencast.py`, PipeWire) — acik bir yayin varsa
       tercih edilen yol. Her monitor AYRI bir akis oldugu icin yukaridaki
       kirpma adimi DUSER: kirpma kaynakta yapilmis olur.
    2. `gnome-screenshot` — yedek. Yayin yoksa, gstreamer kurulu degilse ya da
       `monitor="window"` istendiginde (yayinda pencere secimi yok).

    Yayin yolu 2026-08-03'te olculdu ve iki sebeple tercih ediliyor:

      * `gnome-screenshot` her cekimde BEYAZ FLAS patlatiyor ve ses cikariyor;
        yayin yolu sessiz (kullanici dogruladi). ASIL SEBEP bu.
      * Hiz ikincil ve abartilmamali. Ham yakalama 240 ms'ye karsi 833 ms,
        ama UCTAN UCA (iki monitor + olcekleme + PNG yazma) 1,5 sn'ye karsi
        2,5 sn: aradaki ~1 saniyenin cogu Pillow'da ve iki yolda da ayni.

    Kayip yok: yayin ciktisi ile gnome-screenshot'in ayni bolgesi **%99,8
    birebir ayni** cikti (kalan fark iki cekim arasinda ekranin degismesi).
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
SCREENCAST_NAME = "screencast (PipeWire)"

# Cekim kimligi: "m2-a1b2c3" (monitor 2) ya da "win-a1b2c3" (pencere cekimi).
# Hex kismi dosya adi damgasindaki rastgele son ekle AYNI -- ikinci bir
# rastgelelik kaynagi yok, yani PNG adina bakip kimligi okuyabiliyorsun.
#
# DOGRULAMA ZORUNLU, sus degil: kimlik modelden geliyor ve dogrudan dosya
# adina donusuyor. Suzulmezse `shot="../../.ssh/id_rsa"` diye bir sey
# `<dizin>/<id>.json` yolunun disina cikardi.
SHOT_ID_RE = re.compile(r"^(?:m\d{1,2}|win)-[0-9a-f]{6}$")
META_SUFFIX = ".json"

# Anthropic API uzun kenari bunun ustunde olan goruntuleri KENDISI kucultuyor.
#
# Bu sinirin anlami cekim kimligiyle birlikte DEGISTI. Eskiden yalnizca
# "raporlanan olcek modelin gordugunden farkli olur" demekti; simdi sunucunun
# hesabini bozuyor: model 1568'e indirilmis karedeki pikseli soyluyor,
# `to_global()` ise kayitli olcegi (ornegin 1.0) uyguluyor. Sonuc sistematik
# bir kayma -- 1920 icin 1920/1568 = 1,22 kat, yani ekranin sag yarisinda
# yuzlerce piksel, ve hicbir yerde hata gorunmuyor.
#
# OLCULMEDI, cikarim: sinirin kendisi Anthropic'in belgelenmis davranisi
# (PLAN.md §9b/2'de kayitli), buradaki sonuc ondan turuyor. Bu yuzden deger
# bir KAPI degil UYARI: kullanici tam cozunurluk isteyebilir (insan gozu
# icin), ama koordinat cikarmak icin kullanmamali.
CLIENT_MAX_LONG_EDGE = 1568


def oversized(shot: "Shot") -> bool:
    """Bu cekimden guvenle koordinat cikarilamaz mi?

    Yalnizca OLCEKLENMIS boyuta bakiyor: diskteki dosya ne kadar buyukse
    istemcinin gorecegi o kadar kucultuluyor.
    """
    return max(shot.scaled) > CLIENT_MAX_LONG_EDGE


OVERSIZE_NOTE = (
    "⚠️ Bu goruntunun uzun kenari {edge} px ve {limit} px'i asiyor. Goruntu "
    "isleyen istemciler onu KENDILERI kuculttugu icin sizin gordugunuz piksel "
    "ile kayitli olcek ayrisir; `shot` ile verdiginiz koordinat sistematik "
    "olarak sasar (yaklasik {ratio:.2f} kat). Koordinat cikaracaksaniz "
    "`scale={limit}` ya da daha kucugunu kullanin; bu goruntu yalnizca BAKMAK "
    "icin."
)


def oversize_note(shot: "Shot") -> str:
    """Buyuk cekim uyarisi (koordinat cikarilacaksa). Sorun yoksa bos."""
    if not oversized(shot):
        return ""
    edge = max(shot.scaled)
    return OVERSIZE_NOTE.format(
        edge=edge, limit=CLIENT_MAX_LONG_EDGE, ratio=edge / CLIENT_MAX_LONG_EDGE
    )
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
    id: str = ""  # "m2-a1b2c3" — `shot=` ile geri bulunan kimlik
    taken_at: float = 0.0  # time.time(); bayatlik uyarisi buradan

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

    @property
    def age(self) -> float:
        """Cekimin uzerinden gecen saniye. `taken_at` yoksa 0."""
        return max(0.0, time.time() - self.taken_at) if self.taken_at else 0.0

    # ------------------------------------------------------------ kayit
    def meta(self) -> dict:
        """Diske yazilan kayit. Dosya ADI kimlik, icerik donusum verisi."""
        return {
            "id": self.id,
            # MUTLAK yol: kayit PNG'den BASKA bir dizinde durabiliyor
            # (`pcb-shot --out`). Yalnizca dosya adi yazilsaydi o durumda
            # goruntunun yeri kaybolurdu.
            "png": str(self.path),
            "monitor": None if self.monitor is None else self.monitor.index,
            "connector": None if self.monitor is None else self.monitor.connector,
            "primary": None if self.monitor is None else self.monitor.primary,
            "offset": list(self.offset) if self.offset else None,
            "size": list(self.size),
            "scaled": list(self.scaled),
            "scale": self.scale,
            "taken_at": self.taken_at,
        }


# ------------------------------------------------------------- cekim kaydi
# NEDEN DISKTE
#     `pcb-do`nun her cagrisi YENI BIR SUREC (imlec konumunun `pointer.json`e
#     yazilmasiyla ayni sebep). Kayit bellekte tutulsaydi MCP sunucusunun
#     cektigi goruntuye kabuktan tiklanamaz, `pcb-shot` cekimine de `mouse`
#     ile dokunulamazdi. Diskte tek kayit var, iki yol da ayni yerden okuyor.
def _meta_path(shot_id: str, directory: Path) -> Path:
    if not SHOT_ID_RE.match(shot_id or ""):
        raise CaptureError(
            f"Gecersiz cekim kimligi: {shot_id!r}. Beklenen bicim `m2-a1b2c3` "
            "(ekran goruntusu ciktisindaki `shot:` satiri)."
        )
    return Path(directory) / f"{shot_id}{META_SUFFIX}"


def save_meta(shot: Shot, out_dir: Path | None = None) -> Path | None:
    """Cekim kaydini PNG'nin yanina yaz. Kimliksiz cekim kaydedilmez."""
    if not shot.id:
        return None
    dest = _meta_path(shot.id, out_dir or shot.path.parent)
    dest.write_text(json.dumps(shot.meta(), ensure_ascii=False), encoding="utf-8")
    return dest


def load_shot(shot_id: str, dirs: Sequence[Path]) -> Shot:
    """Kimlikten cekimi geri oku. Bulunamazsa `CaptureError`.

    Monitor nesnesi CEKIM ANINDAKI geometriyle kuruluyor, canli tablodan
    degil: aradan gecen surede monitor duzeni degistiyse bile o goruntunun
    koordinat donusumu dogru kalir.
    """
    tried: list[str] = []
    for directory in dirs or ():
        meta = _meta_path(shot_id, directory)
        tried.append(str(directory))
        if not meta.exists():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CaptureError(f"Cekim kaydi okunamadi ({meta}): {exc}") from exc
        offset = tuple(data["offset"]) if data.get("offset") else None
        size = tuple(data["size"])
        mon = None
        if data.get("monitor") is not None and offset is not None:
            mon = monitorslib.Monitor(
                index=int(data["monitor"]),
                connector=str(data.get("connector") or "?"),
                x=offset[0], y=offset[1],
                width=size[0], height=size[1],
                scale=1.0,
                primary=bool(data.get("primary")),
            )
        png = Path(str(data.get("png") or ""))
        return Shot(
            # Eski kayitlar yalnizca dosya adi tasiyordu; goreli ad kaydin
            # yanindaki PNG demektir.
            path=png if png.is_absolute() else meta.parent / png,
            monitor=mon,
            offset=offset,
            size=size,
            scaled=tuple(data["scaled"]),
            scale=float(data["scale"]),
            id=str(data.get("id") or shot_id),
            taken_at=float(data.get("taken_at") or 0.0),
        )
    raise CaptureError(
        f"`{shot_id}` diye bir ekran goruntusu yok (bakilan yerler: "
        f"{', '.join(tried) or 'hicbiri'}). Kimlik cekim ciktisindaki `shot:` "
        "satirindan aynen kopyalanmali; eski cekimler 24 saat sonra siliniyor. "
        "Taze bir goruntu alin."
    )


def to_global(
    x: int,
    y: int,
    *,
    monitor: int | str | None = None,
    shot: str | None = None,
    dirs: Sequence[Path] | None = None,
) -> tuple[int, int]:
    """Verilen koordinati global tuval koordinatina cevir. TEK GECIT.

    `mouse`, `computer_batch` ve `pcb-do` koordinat donusumu icin YALNIZCA
    burayi cagirir. Uc kabul edilen uzay var:

        ikisi de yok  -> koordinat zaten global, oldugu gibi doner
        monitor=N     -> monitore ozel, TAM COZUNURLUK (eski davranis)
        shot="m2-.."  -> o goruntudeki piksel; ofset VE olcek uygulanir

    `monitor` ile `shot` birlikte verilemez: ikisi farkli uzaylar ve hangisinin
    kastedildigi belirsiz kalirdi. Belirsizligi sessizce cozmek, tam da bu
    dosyanin onlemeye calistigi sinifta bir hata olurdu.
    """
    if shot:
        if monitor is not None:
            raise CaptureError(
                "`shot` ile `monitor` birlikte verilemez: `shot` zaten hangi "
                "monitor oldugunu VE olcegi biliyor. Goruntudeki koordinati "
                "kullaniyorsaniz yalnizca `shot`, monitore ozel tam cozunurluk "
                "koordinati kullaniyorsaniz yalnizca `monitor` verin."
            )
        found = load_shot(shot, dirs or ())
        point = found.to_global(x, y)
        if point is None:
            raise CaptureError(
                f"`{shot}` odaktaki pencerenin goruntusu; ekranin neresinde "
                "oldugu bilinmiyor, ondan koordinat turetilemez. Monitor "
                "goruntusu alin (`monitor='all'`) ya da `ui_click` kullanin."
            )
        return point
    return monitorslib.to_global(x, y, monitor)


# --------------------------------------------------------------------- durum
def available(screencast: Any = None) -> tuple[bool, str]:
    """(kullanilabilir mi, degilse Turkce gerekce).

    Acik bir ekran yayini varsa `gnome-screenshot` GEREKMIYOR: kareler
    PipeWire'dan geliyor. Bu yuzden yayin verildiginde yalnizca Pillow
    araniyor -- yoksa gnome-screenshot'i olmayan bir makinede sessiz yol
    calisirken "kurulu degil" hatasi alinirdi.
    """
    if not PIL_AVAILABLE:
        return False, (
            f"python paketi `Pillow` yok ({PIL_IMPORT_ERROR}). "
            "Kurulum: ./.venv/bin/pip install -r requirements.txt"
        )
    if screencast is not None and screencast.is_open():
        return True, ""
    if not shutil.which(GNOME_SCREENSHOT):
        return False, (
            f"`{GNOME_SCREENSHOT}` kurulu degil. "
            "Kurulum: sudo apt install gnome-screenshot"
        )
    return True, ""


def backend_name(screencast: Any = None) -> str:
    """Bir sonraki yakalamada FIILEN kullanilacak yol.

    Tani ciktisinda ve `screen_capture` notunda gorunuyor; "hangi yol
    calisiyor" sorusunun tek cevabi burasi olsun diye tahmin degil DURUM
    okuyor.
    """
    if screencast is not None and screencast.is_open():
        return SCREENCAST_NAME
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
    screencast: Any = None,
) -> list[Shot]:
    """Ekran goruntusu al ve `out_dir` altina PNG(ler) yaz.

    `monitor`:
        "all" (varsayilan)  her monitor ayri goruntu
        1 / 2 / "DP-1" / "primary"   tek monitor
        "window"            yalnizca odaktaki pencere (ofset URETMEZ)

    `screencast`: acik bir `ScreenCast` verilirse kareler ORADAN alinir --
    sessiz ve hizli yol. Kapaliysa ya da `monitor="window"` istenirse
    `gnome-screenshot`'a dusulur.
    """
    ok, why = available(screencast)
    if not ok:
        raise CaptureError(why)

    out_dir = Path(out_dir) if out_dir else Path(tempfile.gettempdir())
    out_dir.mkdir(parents=True, exist_ok=True)
    # Rastgele son ek SART, sus degil: damga saniye cozunurlukte, yani ayni
    # saniyedeki iki yakalama ayni dosyaya yazardi. O zaman yayimlanmis eski
    # bir /shot baglantisi sessizce DAHA YENI bir ekran goruntusu gostermeye
    # baslar -- olculdu (koordinat testinde taban goruntu eziliyordu).
    # `suffix` hem dosya adina hem cekim kimligine giriyor: PNG adina bakip
    # `shot:` kimligini okuyabilmek icin ikinci bir rastgelelik kaynagi yok.
    suffix = secrets.token_hex(3)
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{suffix}"
    taken_at = time.time()

    want_window = isinstance(monitor, str) and monitor.strip().lower() == "window"
    with tempfile.TemporaryDirectory(prefix="pcbridge-shot-") as td:
        tmpdir = Path(td)

        if want_window:
            raw = _grab_window(tmpdir, include_pointer)
            with Image.open(raw) as canvas:
                dest = out_dir / f"{stamp}-window.png"
                size, scaled, scale = _write_crop(canvas, None, dest, scale_long_edge)
            shot = Shot(
                path=dest,
                monitor=None,
                offset=None,
                size=size,
                scaled=scaled,
                scale=scale,
                id=f"win-{suffix}",
                taken_at=taken_at,
            )
            save_meta(shot)
            return [shot]

        # Monitor tablosu monitors.py'dan gelir; burada ikinci bir okuma YOK.
        mons = monitorslib.list_monitors()
        want_all = monitor is None or (
            isinstance(monitor, str) and monitor.strip().lower() in ("all", "hepsi")
        )
        targets = mons if want_all else [monitorslib.resolve(monitor, mons)]

        # --- yol 1: acik ekran yayini (sessiz) --------------------------
        # Her monitor kendi akisi oldugu icin KIRPMA YOK: `_write_crop`
        # kutusuz cagriliyor, yalnizca olcekleme yapiyor.
        if screencast is not None and screencast.is_open():
            try:
                screencast.ensure_cursor(include_pointer)
                shots = []
                for mon in targets:
                    raw = tmpdir / f"sc-{mon.connector}.png"
                    screencast.capture(mon.connector, raw)
                    dest = out_dir / f"{stamp}-m{mon.index}-{mon.connector}.png"
                    with Image.open(raw) as img:
                        size, scaled, scale = _write_crop(
                            img, None, dest, scale_long_edge
                        )
                    if size != (mon.width, mon.height):
                        raise CaptureError(
                            f"{mon.connector} yayini {size[0]}x{size[1]} verdi, "
                            f"monitor tablosu {mon.width}x{mon.height} diyor. "
                            "Monitor duzeni degismis olabilir; tekrar deneyin."
                        )
                    shot = Shot(
                        path=dest, monitor=mon, offset=(mon.x, mon.y),
                        size=size, scaled=scaled, scale=scale,
                        id=f"m{mon.index}-{suffix}", taken_at=taken_at,
                    )
                    save_meta(shot)
                    shots.append(shot)
                return shots
            except CaptureError:
                raise
            except Exception as exc:  # noqa: BLE001
                # Yayin dustu (monitor uykuda, kompozitor yeniden basladi).
                # Sessizce bos donmektense YEDEGE dusuyoruz; kullanici flasi
                # gorur ama goruntuyu alir.
                raise CaptureError(
                    f"ekran yayinindan kare alinamadi: {exc}. "
                    "`desktop_lock` + `desktop_unlock` yayini yeniden kurar."
                ) from exc

        # --- yol 2: gnome-screenshot (yedek) ----------------------------
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
                shot = Shot(
                    path=dest,
                    monitor=mon,
                    offset=(mon.x, mon.y),
                    size=size,
                    scaled=scaled,
                    scale=scale,
                    id=f"m{mon.index}-{suffix}",
                    taken_at=taken_at,
                )
                save_meta(shot)
                shots.append(shot)
    return shots
