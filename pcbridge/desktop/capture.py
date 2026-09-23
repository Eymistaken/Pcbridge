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

import errno
import json
import math
import os
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

from .. import distro as distrolib
from . import compositor as compositorlib
from . import monitors as monitorslib
from .errors import DesktopError

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

# Cekim kaydindaki `offset`in ve `desktop_size`in uzayi (Task 7.1). Butun
# monitorleri kapsayan dikdortgenin sol ustu (0,0) ve birim masaustu birimi
# (Linux ve macOS'ta mantiksal piksel). Windows backend'i geldiginde fiziksel
# birim ACIKCA baska bir adla etiketlenecek, sessizce ayni ada sigmayacak.
COORDINATE_SPACE = "pcbridge-canvas/logical"
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
# (PLAN.md (1.x, in git history) §9b/2'de kayitli), buradaki sonuc ondan turuyor. Bu yuzden deger
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
    "⚠️ The long edge of this picture is {edge} px, above {limit} px. Clients "
    "that process images scale it down THEMSELVES, so the pixel you see and the "
    "recorded scale disagree; a coordinate given with `shot` will be off "
    "systematically (by about {ratio:.2f} times). To read coordinates off it, "
    "use `scale={limit}` or smaller; this picture is only for LOOKING "
    "at."
)


# Pixel-area cap for scaled pictures (Step 6 of 2.0). The long-edge limit
# alone lets a 4:3 or 16:10 picture grow past what a 16:9 one costs; the cap
# keeps every aspect ratio at the area of a 16:9 picture at the default long
# edge (1536x864), so this machine's pictures are unchanged. It applies only
# when a picture is scaled at all: `scale=0` (full resolution, also OCR's
# path) is exempt. `[desktop] screenshot_max_pixels`, 0 turns it off.
DEFAULT_MAX_PIXELS = 1536 * 864
_max_pixels = DEFAULT_MAX_PIXELS

# Below this share of its real size a picture's small text is hard to read:
# the shown size times the monitor's UI scale. A 4K monitor at scale 2 shown
# at 1536 px keeps 0.8 of its text size; the same monitor at scale 1 or a
# 5120x1440 super-ultrawide at 1536 px keeps 0.4 and 0.3.
LEGIBLE_TEXT_RATIO = 0.5


def set_max_pixels(value: int) -> None:
    """Take `[desktop] screenshot_max_pixels` from the config (0 = no cap)."""
    global _max_pixels
    _max_pixels = max(0, int(value))


def legibility_note(shot: "Shot") -> str:
    """Warn when small text of a monitor picture is likely unreadable."""
    if shot.monitor is None or getattr(shot, "region", False) or not shot.size[0]:
        return ""
    shown = min(shot.scaled[0] / shot.size[0], shot.scaled[1] / shot.size[1])
    text_ratio = shown * getattr(shot.monitor, "scale", 1.0)
    if text_ratio >= LEGIBLE_TEXT_RATIO:
        return ""
    return (
        f"⚠️ Monitor {shot.monitor.index} is shown at {shown:.0%} of its pixels, "
        f"so small text is at {text_ratio:.0%} of its real size and may be "
        "unreadable. To read text, capture a part of it with `region=` (or use "
        "`find_text`)."
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


class ShotLayoutChanged(CaptureError):
    """Cekimden sonra monitor duzeni degisti; koordinati artik baska yere duser."""


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
    size: tuple[int, int]  # kirpilmis, olceklenmemis: HAM PIKSEL
    scaled: tuple[int, int]  # dosyaya yazilan
    scale: float  # scaled[0] / size[0] — uyumluluk icin x orani
    id: str = ""  # "m2-a1b2c3" — `shot=` ile geri bulunan kimlik
    taken_at: float = 0.0  # time.time(); bayatlik uyarisi buradan
    # Kirpmanin MASAUSTU BIRIMINDEKI boyutu (Task 7.1). Olcek 1 iken ham
    # piksel boyutuyla ayni; 2x bir monitorde kare 3840x2160 gelirken
    # masaustu birimi 1920x1080 olur. Goruntu pikselini masaustu birimine
    # ceviren oran BUDUR: desktop_size / scaled, her eksen ayri.
    # Bos: pencere cekimi ya da v1 kayit -- o zaman ham piksel = masaustu
    # birimi sayilir, eski davranisin aynisi.
    desktop_size: tuple[int, int] | None = None
    # Cekim anindaki `monitors.topology_id()`. `offset` O duzene ait: duzen
    # degistiyse ayni ofset baska bir ekranin ustune duser. Bos: pencere
    # cekimi ya da bu alandan once (2026-09-19) yazilmis bir kayit.
    topology: str = ""
    # Monitorun yalnizca bir BOLGESI mi (Adim 8.5). Donusum icin ek bir sey
    # gerekmiyor: `offset` bolgenin sol ustu, `desktop_size` bolgenin boyutu,
    # `to_global` ayni formulle dogru. Yalnizca etiket ve kayit icin.
    region: bool = False

    @property
    def label(self) -> str:
        if self.monitor is None:
            return "focused window"
        star = " (primary)" if self.monitor.primary else ""
        part = " · region" if self.region else ""
        return f"{self.monitor.index} · {self.monitor.connector}{star}{part}"

    @property
    def scale_xy(self) -> tuple[float, float]:
        """Yazilan goruntunun ham piksele orani, her eksen ayri.

        `scale` uyumluluk icin x orani. Kirpma tam bolunmedigi zaman iki oran
        birbirinden biraz ayriliyor (1920x1200 -> 1536x960 gibi durumlarda
        degil, ama tek sayili boyutlarda evet) ve tek oranla cevirmek uzun
        kenarda bir piksel kaydiriyordu.
        """
        return (
            self.scaled[0] / self.size[0] if self.size[0] else 1.0,
            self.scaled[1] / self.size[1] if self.size[1] else 1.0,
        )

    @property
    def desktop_units(self) -> tuple[int, int]:
        """Kirpmanin masaustu birimindeki boyutu. v1 kayitta ham piksel."""
        return self.desktop_size or self.size

    def to_global(self, x: int, y: int) -> tuple[int, int] | None:
        """Goruntudeki piksel -> global tuval koordinati.

        Iki eksen AYRI cevriliyor ve oran dogrudan "masaustu birimi / yazilan
        piksel" (Task 7.1): arada ham piksele donmeye gerek yok ve olcekli bir
        monitorde de dogru. Yuvarlama monitor tablosunun kurali.
        """
        if self.offset is None:
            return None
        dw, dh = self.desktop_units
        sw, sh = self.scaled
        return (
            self.offset[0] + monitorslib.round_half_away(x * dw / sw if sw else x),
            self.offset[1] + monitorslib.round_half_away(y * dh / sh if sh else y),
        )

    def covers_image_point(self, x: int, y: int) -> bool:
        """(x, y) BU goruntunun piksel kutusunun icinde mi?

        "Verilen koordinat aslinda bir goruntu koordinati olabilir mi"
        sorusunun olcutu. Kesinlik iddiasi yok -- (640, 360) hem 1280x720
        bir cekimin ortasi hem gecerli bir global koordinat olabilir; ayirt
        etmenin yolu yok, o yuzden karar SORULUYOR.
        """
        return 0 <= x < self.scaled[0] and 0 <= y < self.scaled[1]

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
            "topology_id": self.topology,
            # --- v2 (Task 7.1). Eski alanlar oldugu gibi duruyor; eski bir
            # okuyucu kaydi aynen okur, yeni okuyucu bunlari da kullanir.
            "source_pixel_size": list(self.size),
            "desktop_size": list(self.desktop_units),
            "scale_xy": list(self.scale_xy),
            # `offset` bu uzayda: butun monitorleri kapsayan dikdortgenin sol
            # ustu (0,0), birim masaustu birimi (Linux/macOS'ta mantiksal).
            "coordinate_space": COORDINATE_SPACE,
            # Adim 8.5: monitorun bir bolgesi. `offset`/`desktop_size` zaten
            # bolgeyi anlatiyor; bu alan yalnizca bilgi.
            "region": self.region,
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
            f"Invalid shot id: {shot_id!r}. Expected the form `m2-a1b2c3` "
            "(the `shot:` line of the screenshot output)."
        )
    return Path(directory) / f"{shot_id}{META_SUFFIX}"


def _record_bytes(shot: Shot) -> bytes:
    return json.dumps(shot.meta(), ensure_ascii=False).encode("utf-8")


def save_meta(shot: Shot, out_dir: Path | None = None) -> Path | None:
    """Var olan bir cekimin kaydini (yeniden) yaz. Kimliksiz cekim kaydedilmez.

    YENI cekimler bunu kullanmiyor: `capture()` kayitlarini hicbir dosyanin
    ustune yazmadan yayimliyor (asagida "yayim"). Burasi bilerek yenilenen
    bir kayit icin; yine de yarim bir JSON gorunmesin diye once gecici
    dosyaya yazip tek adimda yerine koyuyor.
    """
    if not shot.id:
        return None
    dest = _meta_path(shot.id, out_dir or shot.path.parent)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{shot.id}.", suffix=".tmp", dir=dest.parent
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_record_bytes(shot))
        os.replace(temporary, dest)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
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
            raise CaptureError(f"Cannot read the shot record ({meta}): {exc}") from exc
        offset = tuple(data["offset"]) if data.get("offset") else None
        size = tuple(data.get("source_pixel_size") or data["size"])
        desktop = data.get("desktop_size")
        desktop_size = tuple(desktop) if desktop else None
        mon = None
        if data.get("monitor") is not None and offset is not None:
            # Monitorun kutusu MASAUSTU BIRIMINDE: `offset` de o uzayda ve
            # ikisi birlikte kullaniliyor. Olcekli bir monitorde `size` ham
            # piksel oldugu icin oradan alinamaz (Task 7.1). v1 kayitta ikisi
            # zaten ayni.
            box = desktop_size or size
            mon = monitorslib.Monitor(
                index=int(data["monitor"]),
                connector=str(data.get("connector") or "?"),
                x=offset[0], y=offset[1],
                width=box[0], height=box[1],
                scale=(size[0] / box[0]) if box[0] else 1.0,
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
            topology=str(data.get("topology_id") or ""),
            desktop_size=desktop_size,
            region=bool(data.get("region")),
        )
    raise CaptureError(
        f"There is no screenshot `{shot_id}` (looked in: "
        f"{', '.join(tried) or 'nowhere'}). Copy the id exactly from the `shot:` "
        "line of the capture output; old captures are removed after 24 hours. "
        "Take a fresh screenshot."
    )


def newest_scaled_shot(
    dirs: Sequence[Path], max_age: float
) -> Shot | None:
    """Son `max_age` saniyede alinmis, KUCULTULMUS en yeni cekim.

    Olcegi 1.0 olan cekimler aranmiyor: orada goruntu koordinati ile global
    koordinat arasindaki fark yalnizca ofset, ve ofset zaten `monitor=`in
    isi. Belirsizligi yaratan sey kucultme.
    """
    best: Shot | None = None
    for directory in dirs or ():
        try:
            metas = list(Path(directory).glob(f"*{META_SUFFIX}"))
        except OSError:
            continue
        for meta in metas:
            if not SHOT_ID_RE.match(meta.stem):
                continue
            try:
                found = load_shot(meta.stem, [directory])
            except CaptureError:
                continue
            if found.scale >= 1.0 or found.offset is None:
                continue
            if found.age > max_age:
                continue
            if best is None or found.taken_at > best.taken_at:
                best = found
    return best


AMBIGUOUS_NOTE = (
    "⛔ The coordinate ({x}, {y}) is AMBIGUOUS. {age} seconds ago you took a scaled-down "
    "screenshot (`{id}`, {w}x{h}, scale {scale:.3f}) and this coordinate lies "
    "inside that picture -- but you gave neither `shot` nor `monitor`, so it "
    "would count as a GLOBAL canvas coordinate and the action would land {where}.\n"
    '  · If you read the coordinate off that picture:  shot="{id}"\n'
    "  · If it really is a global/monitor coordinate: monitor=<number>\n"
    "Pick one of the two; pcbridge does not guess which one you meant."
)


def to_global(
    x: int,
    y: int,
    *,
    monitor: int | str | None = None,
    shot: str | None = None,
    dirs: Sequence[Path] | None = None,
    guard_age: float = 0.0,
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
                "`shot` and `monitor` cannot be combined: `shot` already knows "
                "the monitor AND the scale. For a coordinate from the picture give "
                "only `shot`; for a full-resolution coordinate inside a monitor "
                "give only `monitor`."
            )
        found = load_shot(shot, dirs or ())
        if found.offset is not None and not found.covers_image_point(x, y):
            # Goruntunun disindaki bir piksel (Task 7.1). Eskiden cevriliyordu
            # ve monitorun disina dusuyordu: tiklama komsu ekrana ya da
            # hicbir yere giderdi. Kimlik verildigine gore koordinat O
            # goruntuden okunmus olmali.
            raise CaptureError(
                f"({x}, {y}) is outside the `{shot}` picture: it is "
                f"{found.scaled[0]}x{found.scaled[1]} pixels. Read the coordinate "
                "off that picture, or use a global coordinate without "
                "`shot`."
            )
        point = found.to_global(x, y)
        if point is None:
            raise CaptureError(
                f"`{shot}` is a picture of the focused window; where it sits on "
                "the screen is unknown, so no coordinate can be derived from it. "
                "Capture a monitor (`monitor='all'`) or use `ui_click`."
            )
        # Kayittaki ofset CEKIM ANINDAKI duzene ait. Monitor eklendi, cikti,
        # tasindi ya da cozunurlugu degistiyse ayni ofset artik baska bir
        # ekranin ustune duser ve tiklama sessizce yanlis yere gider (PLAN.md (1.x, in git history)
        # Task 5.3, madde 7). Kimligi olmayan eski kayit eskisi gibi gecer.
        if found.topology and found.topology != monitorslib.topology_id(
            monitorslib.list_monitors()
        ):
            raise ShotLayoutChanged(
                f"The screen layout changed after `{shot}` was taken (a monitor "
                "was added, removed, moved or changed resolution). A coordinate from "
                "that picture would land somewhere else now; take a new "
                "screenshot."
            )
        return _on_a_monitor(point, f"({x}, {y}) in the `{shot}` picture")

    if monitor is None and guard_age > 0:
        # BELIRSIZ KOORDINAT KORUMASI. `shot` da `monitor` da yoksa koordinat
        # global sayilir -- ama yakinda kucultulmus bir cekim varsa ve
        # koordinat onun icine dusuyorsa, bu buyuk olasilikla `shot`u unutmus
        # bir cagridir ve sessizce YANLIS YERE tiklanir.
        #
        # Ayni ders `batch.py`de yaziyor: cozum korumayi kapatmak degil,
        # NIYETI SOYLETMEK (`expect_focus`). Kaza tam da beyan edilmemis bir
        # niyetten cikmisti.
        recent = newest_scaled_shot(dirs or (), guard_age)
        if recent is not None and recent.covers_image_point(x, y):
            land = monitorslib.find_monitor(x, y)
            where = (f"on monitor {land.index} ({land.connector})"
                     if land else "outside the canvas")
            raise CaptureError(AMBIGUOUS_NOTE.format(
                x=x, y=y, age=int(recent.age), id=recent.id,
                w=recent.scaled[0], h=recent.scaled[1], scale=recent.scale,
                where=where,
            ))
    where = (
        f"({x}, {y}) on monitor {monitor}"
        if monitor is not None
        else f"({x}, {y})"
    )
    return _on_a_monitor(monitorslib.to_global(x, y, monitor), where)


# Bolge cekiminin en kucuk kenari. Daha kucugu okunacak bir sey tasimaz ve
# buyuk olasilikla ters cevrilmis bir koordinattir.
REGION_MIN_EDGE = 16


def resolve_region(
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    monitor: int | str | None = None,
    shot: str | None = None,
    dirs: Sequence[Path] | None = None,
) -> tuple[tuple[int, int, int, int], monitorslib.Monitor]:
    """Bir bolgeyi GLOBAL tuval kutusuna cevir: ((x, y, w, h), monitor).

    `mouse` koordinatlarinin uc uzayi burada da gecerli (Adim 8.5): hicbiri
    verilmezse global, `monitor=` o monitorun icinde tam cozunurluk, `shot=`
    o goruntudeki pikseller -- ofset VE olcek uygulanir. Goruntuden bir
    parcaya "yakinlasmak" en dogal kullanim, o yuzden `shot` sart.

    Kutu TEK bir monitorun icinde kalmali: her monitor ayri bir kareden
    geliyor ve iki kareyi birlestirmek bu katmanin isi degil. Tasan ya da
    monitorlerin arasina dusen bolge reddedilir, sessizce kirpilmaz --
    kirpilmis bir bolge istenenden baska bir sey gosterir.
    """
    if width < REGION_MIN_EDGE or height < REGION_MIN_EDGE:
        raise CaptureError(
            f"A region must be at least {REGION_MIN_EDGE}x{REGION_MIN_EDGE} "
            f"({width}x{height} given). The form is [x, y, width, height]."
        )
    if x < 0 or y < 0:
        raise CaptureError(f"The top left of a region cannot be negative ({x}, {y}).")
    mons = monitorslib.list_monitors()
    if shot:
        if monitor is not None:
            raise CaptureError(
                "`shot` and `monitor` cannot be combined: if you read the region "
                "off that picture give only `shot`; for a full-resolution "
                "coordinate inside a monitor give only `monitor`."
            )
        found = load_shot(shot, dirs or ())
        if found.offset is None:
            raise CaptureError(
                f"`{shot}` is a picture of the focused window; where it sits on the "
                "screen is unknown, so no region can be derived from it."
            )
        if x + width > found.scaled[0] or y + height > found.scaled[1]:
            raise CaptureError(
                f"The region goes outside the `{shot}` picture: it is "
                f"{found.scaled[0]}x{found.scaled[1]} pixels."
            )
        if found.topology and found.topology != monitorslib.topology_id(mons):
            raise ShotLayoutChanged(
                f"The screen layout changed after `{shot}` was taken; the region "
                "from that picture would land somewhere else now. Take a new "
                "screenshot."
            )
        dw, dh = found.desktop_units
        sw, sh = found.scaled
        # DISA dogru yuvarla: istenen her piksel yeni cekimde olsun.
        left = found.offset[0] + math.floor(x * dw / sw)
        top = found.offset[1] + math.floor(y * dh / sh)
        right = found.offset[0] + math.ceil((x + width) * dw / sw)
        bottom = found.offset[1] + math.ceil((y + height) * dh / sh)
        box = (left, top, right - left, bottom - top)
        what = f"the region in picture `{shot}`"
    elif monitor is not None:
        mon = monitorslib.resolve(monitor, mons)
        box = (mon.x + x, mon.y + y, width, height)
        what = f"the region in monitor {mon.index}"
    else:
        box = (x, y, width, height)
        what = "the region"
    gx, gy, gw, gh = box
    home = monitorslib.find_monitor(gx, gy, mons)
    if home is None or gx + gw > home.x + home.width or gy + gh > home.y + home.height:
        where = (f"monitor {home.index} ({home.x}, {home.y}, {home.width}x"
                 f"{home.height})" if home else "no monitor")
        raise CaptureError(
            f"{what} ({gx}, {gy}, {gw}x{gh}) does not fit inside one monitor "
            f"(its top left is on: {where}). Every monitor comes from its own frame; "
            "choose the region inside one monitor. `screen_info` shows the "
            "boxes."
        )
    return box, home


def _on_a_monitor(point: tuple[int, int], what: str) -> tuple[int, int]:
    """Nokta bir monitorun ustune dusuyor mu? Dusmuyorsa reddet (Task 7.1).

    Monitorler arasinda BOSLUK olabilir (iki ekran ayni hizada degilse) ve
    tuvalin kose bosluklari hicbir ekrana ait degildir. Oraya gonderilen bir
    tiklama hicbir sey yapmaz ama basarili gorunur; imlec de gorunmez bir
    yere gider.

    Monitor tablosu okunamiyorsa kontrol ATLANIR: dogrulanamayan bir sey
    yuzunden calisan bir cagriyi reddetmek yanlis olurdu.
    """
    try:
        mons = monitorslib.list_monitors()
    except monitorslib.MonitorError:
        return point
    if monitorslib.find_monitor(point[0], point[1], mons) is not None:
        return point
    width, height = monitorslib.canvas_size(mons)
    raise CaptureError(
        f"{what} is not on any monitor: global ({point[0]}, "
        f"{point[1]}) on a {width}x{height} canvas. There may be a gap between "
        "monitors, or the coordinate is off the screen. `screen_info` shows the "
        "monitor boxes."
    )


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
            f"the python package `Pillow` is missing ({PIL_IMPORT_ERROR}). "
            "Reinstall pcbridge with its [desktop] extra (`pcbridge setup`)."
        )
    if screencast is not None and screencast.is_open():
        return True, ""
    if not shutil.which(GNOME_SCREENSHOT):
        return False, (
            f"`{GNOME_SCREENSHOT}` is not installed. "
            f"Install it: {distrolib.install_command('gnome-screenshot')}"
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
            f"{GNOME_SCREENSHOT} failed (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[:200]}"
        )
    # gnome-screenshot bazen cikis 0 verip dosyayi hic yazmiyor (oturum ortami
    # eksikse); sessizce bos goruntu dondurmektense burada patla.
    if not target.exists() or target.stat().st_size == 0:
        raise CaptureError(
            f"{GNOME_SCREENSHOT} exited 0 but wrote no file. Does the service "
            "run inside the graphical session? (`pcbridge doctor`)"
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


def _frame_taken_at(frame: Any, fallback: float) -> float:
    """Backend'in bildirdigi edinim zamani, yoksa dizinin baslangici.

    Yalnizca ileri dogru guveniliyor: gelecege ait ya da anlamsiz bir damga
    bayatlik kontrolunu SESSIZCE devre disi birakirdi, o yuzden aralik disi
    deger yok sayiliyor.
    """
    if not isinstance(frame, dict):
        return fallback
    reported = frame.get("taken_at")
    if not isinstance(reported, (int, float)):
        return fallback
    reported = float(reported)
    if reported <= 0 or reported > time.time() + 1.0:
        return fallback
    return reported


# --------------------------------------------------------- kirpma/olcekleme
def check_source_size(mon: monitorslib.Monitor, size: tuple[int, int]) -> None:
    """Gelen karenin ham piksel boyutu bu monitore ait olabilir mi? (Task 7.1)

    Iki boyut kabul ediliyor: monitorun mantiksal boyutu (olcek 1, ya da
    kompozitor mantiksal piksel veriyor) ve mantiksal boyut x olcek (olcekli
    monitorde ham piksel). Baskasi OLCEKLENMEZ, reddedilir: sessizce
    olceklemek sonraki her tiklamayi orantili bir mesafe kadar kaydirirdi.

    Bu makinede iki monitor de olcek 1.0, yani iki kabul edilen boyut ayni.
    Kesirli olcek fiilen OLCULMEDI; kural yazili ve fixture'la sinaniyor ama
    boyle bir donanimda dogrulanmadi.
    """
    logical = (mon.width, mon.height)
    if size == logical or size == mon.source_pixel_size:
        return
    accepted = (
        f"{logical[0]}x{logical[1]}"
        if mon.source_pixel_size == logical
        else f"{logical[0]}x{logical[1]} or {mon.source_pixel_size[0]}x"
        f"{mon.source_pixel_size[1]}"
    )
    raise CaptureError(
        f"the {mon.connector} stream delivered {size[0]}x{size[1]}, the monitor table "
        f"expects {accepted}. The monitor layout may have changed; try "
        "again."
    )


def canvas_pixel_ratio(
    mons: list[monitorslib.Monitor], canvas: tuple[int, int]
) -> float:
    """Tuval goruntusunun masaustu birimine orani. Cozulemezse `CaptureError`.

    `gnome-screenshot` yedegi TEK bir goruntu veriyor, yani orani monitor
    monitor soramiyoruz. Iki durum cozulebilir: goruntu masaustu biriminde
    (oran 1) ya da butun monitorler AYNI olcekte ve goruntu o olcekte. Karisik
    olcekli bir duzende hangi pikselin hangi monitore ait oldugu bu yoldan
    bilinemez: reddediliyor, tahmin edilmiyor (yayin yolu monitor basina
    ayri kare verdigi icin oradan etkilenmiyor).
    """
    expected = monitorslib.canvas_size(mons)
    if canvas == expected:
        return 1.0
    scales = {round(m.pixel_ratio, 4) for m in mons}
    if len(scales) == 1:
        scale = scales.pop()
        scaled = (
            monitorslib.round_half_away(expected[0] * scale),
            monitorslib.round_half_away(expected[1] * scale),
        )
        if canvas == scaled:
            return scale
    raise CaptureError(
        f"The captured canvas is {canvas[0]}x{canvas[1]}, but the monitor table "
        f"says {expected[0]}x{expected[1]}"
        + (
            " and the monitors have different scales, so this picture cannot tell "
            "which pixel belongs to which monitor"
            if len(scales) > 1
            else ". The monitor layout may have changed"
        )
        + "; try again."
    )


def _scaled_size(
    w: int, h: int, long_edge: int, max_pixels: int | None = None
) -> tuple[int, int]:
    """Size with the long edge at most `long_edge` and the area at most
    `max_pixels`. `long_edge` 0 means full resolution: no limit at all.

    A portrait picture is limited by its height, since that is its long edge.
    """
    if long_edge <= 0:
        return (w, h)
    max_pixels = _max_pixels if max_pixels is None else max_pixels
    ratio = min(1.0, long_edge / max(w, h))
    if max_pixels and w * h * ratio * ratio > max_pixels:
        ratio = math.sqrt(max_pixels / (w * h))
    if ratio >= 1.0:
        return (w, h)
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
    # `optimize=True` YOK. OLCULDU 2026-09-20, gercek bir 1920x1080 ekran
    # goruntusu, 1536'ya kucultulmus: kayit optimize ile 3106 ms surdu ve
    # dosya 906 KiB oldu; varsayilan sikistirmayla 259 ms ve 957 KiB. Yani
    # bir cekimin 3,7 saniyesinin 3,1 saniyesi %5 dosya boyutu icin
    # harcaniyordu. Cozme 34 ms, kucultme 27 ms.
    img.save(dest, format="PNG")
    return (cw, ch), (sw, sh), (sw / cw if cw else 1.0)


# ------------------------------------------------------------- iyilestirme
# Adim 8.5: gece ve yeralti kareleri neredeyse simsiyah geliyordu ve ajan
# oyunun parlaklik ayarini degistirmek zorunda kaldi. Iyilestirme YALNIZCA
# ajana giden kopyaya uygulanir: diskteki cekim ham kalir (HTTP baglantisi
# da onu verir) ve boyut degismez, yani `shot=` donusumu aynen gecerli.
ENHANCE_CUTOFF = 0.005        # uclardan kirpilan pay (her uc %0,5)
ENHANCE_TARGET_MEAN = 0.45    # germe sonrasi hedef ortalama parlaklik
ENHANCE_DARK_MEAN = 0.35      # bunun altindaki kare gama ile acilir
ENHANCE_MIN_GAMMA = 0.35      # en fazla bu kadar acilir


def _percentile(hist: Sequence[int], fraction: float) -> int:
    total = sum(hist)
    if not total:
        return 0
    want = total * fraction
    run = 0
    for value, count in enumerate(hist):
        run += count
        if run > want:
            return value
    return len(hist) - 1


def enhance_image(img: "Image.Image") -> tuple["Image.Image", dict]:
    """Koyu/soluk bir kareyi okunur yap: kontrast germe, gerekirse gama.

    Tablo yalnizca PARLAKLIGA (YCbCr'nin Y kanali) uygulanir; renk kanallari
    olduklari gibi kalir. Ilk deneme ayni tabloyu R, G ve B'ye ayri ayri
    uyguluyordu ve (6, 7, 10) gibi koyu bir arka plan (0, 0, 142) oldu:
    esigin altindaki kanallar sifira, ustundeki mavi tavana gitti. Zaten
    genis aralikli, parlak bir kare hemen hemen degismez.
    Doner: (yeni goruntu, olculer).
    """
    luma, cb, cr = img.convert("RGB").convert("YCbCr").split()
    hist = luma.histogram()
    total = sum(hist) or 1
    lo = _percentile(hist, ENHANCE_CUTOFF)
    hi = _percentile(hist, 1.0 - ENHANCE_CUTOFF)
    if hi - lo < 16:
        # Neredeyse duz bir kare: gurultuyu 16 kat buyutmek bilgi eklemez.
        hi = min(255, lo + 16)
        lo = max(0, hi - 16)
    span = float(hi - lo)
    stretched = [min(1.0, max(0.0, (v - lo) / span)) for v in range(256)]
    mean_before = sum(v * c for v, c in enumerate(hist)) / total / 255.0
    mean_mid = sum(stretched[v] * c for v, c in enumerate(hist)) / total
    gamma = 1.0
    if 0.0 < mean_mid < ENHANCE_DARK_MEAN:
        gamma = max(ENHANCE_MIN_GAMMA, math.log(ENHANCE_TARGET_MEAN) / math.log(mean_mid))
    table = [monitorslib.round_half_away(255.0 * (t ** gamma)) for t in stretched]
    out = Image.merge("YCbCr", (luma.point(table), cb, cr)).convert("RGB")
    mean_after = sum(table[v] * c for v, c in enumerate(hist)) / total / 255.0
    return out, {
        "low": lo,
        "high": hi,
        "gamma": round(gamma, 3),
        "mean_before": round(mean_before, 3),
        "mean_after": round(mean_after, 3),
    }


def enhanced_png(path: Path) -> tuple[bytes, dict]:
    """Diskteki PNG'nin iyilestirilmis KOPYASI, bellekte. Dosyaya dokunmaz."""
    import io

    with Image.open(path) as img:
        size = img.size
        out, stats = enhance_image(img)
    if out.size != size:  # pragma: no cover - `point` boyutu degistirmez
        raise CaptureError("the enhancement changed the picture size")
    buffer = io.BytesIO()
    out.save(buffer, format="PNG")
    return buffer.getvalue(), stats


# --------------------------------------------------------------------- yayim
# BUTUN YA DA HIC. Bir cekim once `out_dir` icindeki gizli bir hazirlik
# dizininde uretiliyor ve butun hedef monitorler hazir olunca yayimlaniyor.
# Eskiden her monitorun PNG'si ve kaydi o monitor bitince yaziliyordu: bir
# `all` cekiminde ikinci monitor dusunce birincisinin goruntusu ve kaydi
# diskte kaliyordu -- kimsenin almadigi, ama `shot=` ile hala bulunan bir
# cekim.
STAGING_PREFIX = ".staging-"
# Oldurulmus bir cekimin birakabilecegi tek sey hazirlik dizini, ve icinde
# TAM COZUNURLUKTE bir ekran goruntusu olabilir. En uzun cekim (yakalama
# basina GRAB_TIMEOUT) bunun cok altinda, `shot_keep_hours` cok ustunde.
STAGING_ABANDONED_SECONDS = 600
# Son ek 24 bit ve dogrudan kimlige donusuyor: cakisma nadir ama mumkun, ve
# cakisan bir kimlik `shot=` tiklamasini baska bir cekimin ofset/olcegiyle
# cevirir. Her denemede yeni bir son ek cekiliyor.
PUBLISH_ATTEMPTS = 16

# Hard link desteklemeyen dosya sistemleri (vfat, bazi FUSE) ve baska bir
# dosya sistemindeki kopya dizini bunlari veriyor.
_NO_HARD_LINK = frozenset(
    {
        errno.EPERM,
        errno.EOPNOTSUPP,
        errno.ENOTSUP,
        errno.EXDEV,
        errno.EMLINK,
        errno.ENOSYS,
    }
)


def sweep_staging(directory: Path, now: float | None = None) -> int:
    """Oldurulmus cekimlerin biraktigi hazirlik dizinlerini sil. -> silinen sayi.

    Yalnizca `STAGING_PREFIX` ile baslayan DIZINLER ve yalnizca
    `STAGING_ABANDONED_SECONDS`ten eskiyse: suren bir cekimin dizini genc,
    ona dokunulmaz. `shot_keep_hours = 0` (hic silme) burayi etkilemez --
    yarim kalmis bir hazirlik dizini saklanan bir cekim degil.
    """
    cutoff = (time.time() if now is None else now) - STAGING_ABANDONED_SECONDS
    try:
        candidates = list(Path(directory).glob(f"{STAGING_PREFIX}*"))
    except OSError:
        return 0
    removed = 0
    for path in candidates:
        try:
            if path.is_symlink() or not path.is_dir():
                continue
            if path.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            removed += 1
    return removed


def _publish_file(source: Path, dest: Path) -> None:
    """`source`u `dest` adiyla yayimla; var olan bir dosyanin USTUNE ASLA.

    `os.link` tek adimda ve hedef varsa `FileExistsError` veriyor.
    `os.replace` kullanilsaydi baska bir cekimin dosyasi sessizce ezilirdi.
    Hard link yoksa `x` kipiyle kopyalaniyor: yine ustune yazmiyor, ama okuyan
    biri kisa bir an yarim dosya gorebilir.
    """
    try:
        os.link(source, dest)
        return
    except FileExistsError:
        raise
    except OSError as exc:
        if exc.errno not in _NO_HARD_LINK:
            raise
    with open(dest, "xb") as target:
        try:
            with open(source, "rb") as origin:
                shutil.copyfileobj(origin, target)
        except BaseException:
            target.close()
            dest.unlink(missing_ok=True)
            raise


def _occupied(shots: Sequence[Shot], directories: Sequence[Path]) -> bool:
    """Bu dosya adlarindan ya da kimliklerden biri bir yerde kullaniliyor mu?"""
    for shot in shots:
        if os.path.lexists(shot.path):
            return True
        for directory in directories:
            if os.path.lexists(directory / f"{shot.id}{META_SUFFIX}"):
                return True
    return False


def _withdraw(paths: Sequence[Path]) -> None:
    """Bu denemenin az once yayimladigi dosyalari geri al.

    Listede yalnizca link'i BASARILI olanlar var: basarili link hedefin
    onceden olmadigini kanitliyor, yani silinen hicbir sey baskasinin dosyasi
    degil.
    """
    for path in paths:
        try:
            path.unlink()
        except OSError:
            pass


def _unique(paths: Sequence[Path]) -> list[Path]:
    seen: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.append(path)
    return seen


@dataclass(frozen=True)
class _Pending:
    """Hazirlik dizininde bekleyen, henuz adi ve kimligi olmayan goruntu."""

    staged: Path
    monitor: monitorslib.Monitor | None
    size: tuple[int, int]
    scaled: tuple[int, int]
    scale: float
    taken_at: float
    topology: str = ""
    desktop_size: tuple[int, int] | None = None
    # Bolge cekiminde bolgenin global sol ustu; yoksa monitorun kendisi.
    origin: tuple[int, int] | None = None

    def shot(self, out_dir: Path, stamp: str, suffix: str) -> Shot:
        if self.monitor is None:
            return Shot(
                path=out_dir / f"{stamp}-window.png",
                monitor=None,
                offset=None,
                size=self.size,
                scaled=self.scaled,
                scale=self.scale,
                id=f"win-{suffix}",
                taken_at=self.taken_at,
            )
        mon = self.monitor
        part = "-region" if self.origin is not None else ""
        return Shot(
            path=out_dir / f"{stamp}-m{mon.index}-{mon.connector}{part}.png",
            monitor=mon,
            offset=self.origin or (mon.x, mon.y),
            size=self.size,
            scaled=self.scaled,
            scale=self.scale,
            id=f"m{mon.index}-{suffix}",
            taken_at=self.taken_at,
            topology=self.topology,
            desktop_size=self.desktop_size or (mon.width, mon.height),
            region=self.origin is not None,
        )


def _publish(
    pending: Sequence[_Pending],
    *,
    out_dir: Path,
    staging: Path,
    meta_dirs: Sequence[Path],
    lookup_dirs: Sequence[Path],
) -> list[Shot]:
    """Hazir goruntuleri ve kayitlarini yayimla: once PNG'ler, en son kayitlar.

    Sira bilincli: kayit `shot=` aramasinin buldugu sey, arkasinda goruntu
    olmayan bir kayit hicbir an gorunmesin.
    """
    for _attempt in range(PUBLISH_ATTEMPTS):
        # Rastgele son ek SART, sus degil: damga saniye cozunurlukte, yani
        # ayni saniyedeki iki yakalama ayni ada yazardi. O zaman yayimlanmis
        # eski bir /shot baglantisi sessizce DAHA YENI bir goruntu gostermeye
        # baslar -- olculdu (koordinat testinde taban goruntu eziliyordu).
        # `suffix` hem dosya adina hem cekim kimligine giriyor: PNG adina bakip
        # `shot:` kimligini okuyabilmek icin ikinci bir rastgelelik kaynagi yok.
        suffix = secrets.token_hex(3)
        stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{suffix}"
        shots = [item.shot(out_dir, stamp, suffix) for item in pending]
        if _occupied(shots, lookup_dirs):
            continue
        published: list[Path] = []
        try:
            for item, shot in zip(pending, shots):
                _publish_file(item.staged, shot.path)
                published.append(shot.path)
            for shot in shots:
                for number, directory in enumerate(meta_dirs):
                    # Her hedef dizine AYRI bir kopya: ayni inode'u paylasan
                    # iki kayit, birinin yerinde yazilmasiyla ikisini birden
                    # degistirirdi.
                    record = staging / f"{number}-{shot.id}{META_SUFFIX}"
                    record.write_bytes(_record_bytes(shot))
                    dest = directory / f"{shot.id}{META_SUFFIX}"
                    _publish_file(record, dest)
                    published.append(dest)
        except FileExistsError:
            # Kontrol ile yayim arasinda baska bir surec ayni adi aldi.
            _withdraw(published)
            continue
        except OSError as exc:
            _withdraw(published)
            raise CaptureError(f"The screenshot could not be published: {exc}") from exc
        return shots
    raise CaptureError(
        f"No unused shot id found in {PUBLISH_ATTEMPTS} attempts; "
        "remove old captures and try again."
    )


def _frame_box(
    mon: monitorslib.Monitor,
    frame_size: tuple[int, int],
    region: tuple[int, int, int, int] | None,
) -> tuple[int, int, int, int] | None:
    """Bolgenin o monitorun KARESINDEKI piksel kutusu; bolge yoksa None.

    Kare ham pikselde gelebilir (olcekli monitor): oran her eksende
    kare / masaustu birimi. Bu makinede iki monitor de olcek 1, oran 1.
    """
    if region is None:
        return None
    gx, gy, gw, gh = region
    rx = frame_size[0] / mon.width if mon.width else 1.0
    ry = frame_size[1] / mon.height if mon.height else 1.0
    return (
        monitorslib.round_half_away((gx - mon.x) * rx),
        monitorslib.round_half_away((gy - mon.y) * ry),
        monitorslib.round_half_away((gx + gw - mon.x) * rx),
        monitorslib.round_half_away((gy + gh - mon.y) * ry),
    )


def _kwin_window_region() -> tuple[tuple[int, int, int, int], monitorslib.Monitor]:
    """The focused window on Plasma as ((x, y, w, h) on the canvas, its monitor).

    KWin reports the frame in its own (platform) coordinates; the canvas starts
    at (0, 0), so the monitor table's offset is applied. The box is clipped to
    the monitor holding the window's center: a window hanging over an edge is
    shown as far as that monitor shows it.
    """
    from .apps import _kwin

    reply = _kwin({"cmd": "focused"})
    geometry = (reply or {}).get("geometry")
    if not reply or reply.get("found") is not True or not geometry:
        raise CaptureError("No focused window to capture (KWin reported none).")
    mons = monitorslib.list_monitors()
    px, py, pw, ph = (float(v) for v in geometry)
    left, top = min(m.platform[0] for m in mons), min(m.platform[1] for m in mons)
    x0, y0 = px - left, py - top
    cx, cy = x0 + pw / 2, y0 + ph / 2
    home = next((m for m in mons if m.contains(int(cx), int(cy))), None)
    if home is None:
        raise CaptureError("The focused window is not on any monitor.")
    x1 = max(home.x, monitorslib.round_half_away(x0))
    y1 = max(home.y, monitorslib.round_half_away(y0))
    x2 = min(home.x + home.width, monitorslib.round_half_away(x0 + pw))
    y2 = min(home.y + home.height, monitorslib.round_half_away(y0 + ph))
    if x2 - x1 < REGION_MIN_EDGE or y2 - y1 < REGION_MIN_EDGE:
        raise CaptureError("The focused window is too small to capture.")
    return (x1, y1, x2 - x1, y2 - y1), home


def _render(
    monitor: int | str | None,
    staging: Path,
    scale_long_edge: int,
    include_pointer: bool,
    screencast: Any,
    region: tuple[tuple[int, int, int, int], monitorslib.Monitor] | None = None,
) -> list[_Pending]:
    """Hedefleri yakala, kirp, olcekle ve HAZIRLIK dizinine yaz.

    `region`: `resolve_region`in cevabi. Verilirse yalnizca o monitor
    yakalanir ve karenin o parcasi yazilir (Adim 8.5).
    """
    taken_at = time.time()
    raw_dir = staging / "raw"
    raw_dir.mkdir()

    want_window = isinstance(monitor, str) and monitor.strip().lower() == "window"
    if want_window and region is None and compositorlib.is_kde():
        # Plasma has no gnome-screenshot: the focused window is a region of
        # its monitor's frame, which also gives the shot a global offset.
        region = _kwin_window_region()
        want_window = False
        monitor = None  # the region names its monitor
    if want_window and region is not None:
        raise CaptureError(
            "A `window` capture cannot take a region: where the window sits on "
            "the screen is unknown. Give the region as monitor or global coordinates."
        )
    if want_window:
        raw = _grab_window(raw_dir, include_pointer)
        staged = staging / "window.png"
        with Image.open(raw) as canvas:
            size, scaled, scale = _write_crop(canvas, None, staged, scale_long_edge)
        return [_Pending(staged, None, size, scaled, scale, taken_at)]

    # Monitor tablosu monitors.py'dan gelir; burada ikinci bir okuma YOK.
    mons = monitorslib.list_monitors()
    topology = monitorslib.topology_id(mons)
    want_all = monitor is None or (
        isinstance(monitor, str) and monitor.strip().lower() in ("all", "hepsi")
    )
    targets = mons if want_all else [monitorslib.resolve(monitor, mons)]
    box_global: tuple[int, int, int, int] | None = None
    if region is not None:
        box_global, home = region
        # Tablo resolve_region'dan beri degismediyse ayni monitor; degistiyse
        # kutu artik baska yere duser.
        current = next(
            (m for m in mons if (m.x, m.y, m.width, m.height)
             == (home.x, home.y, home.width, home.height)),
            None,
        )
        if current is None:
            raise ShotLayoutChanged(
                "The screen layout changed after the region was chosen; try again."
            )
        targets = [current]
    origin = (box_global[0], box_global[1]) if box_global else None
    region_size = (box_global[2], box_global[3]) if box_global else None

    # --- yol 1: acik ekran yayini (sessiz) ------------------------------
    # Her monitor kendi akisi oldugu icin KIRPMA YOK: `_write_crop`
    # kutusuz cagriliyor, yalnizca olcekleme yapiyor.
    if screencast is not None and screencast.is_open():
        try:
            screencast.ensure_cursor(include_pointer)
            pending: list[_Pending] = []
            for mon in targets:
                raw = raw_dir / f"sc-{mon.connector}.png"
                frame = screencast.capture(mon.connector, raw)
                staged = staging / f"m{mon.index}.png"
                with Image.open(raw) as img:
                    # Karenin TAMAMI bu monitore ait mi -- kirpmadan ONCE.
                    check_source_size(mon, img.size)
                    size, scaled, scale = _write_crop(
                        img, _frame_box(mon, img.size, box_global), staged,
                        scale_long_edge,
                    )
                pending.append(
                    _Pending(
                        staged,
                        mon,
                        size,
                        scaled,
                        scale,
                        # Kare ham piksel; kayit masaustu birimini de tasiyor.
                        # Bir backend karenin FIILEN ne zaman geldigini
                        # biliyorsa o kazanir: `taken_at` bayatlik uyarisini
                        # suruyor ve bir `all` cekiminde monitorler arasinda
                        # yuzlerce milisaniye olabiliyor. Bilmiyorsa dizinin
                        # baslangici, eskisi gibi.
                        _frame_taken_at(frame, taken_at),
                        topology,
                        region_size or (mon.width, mon.height),
                        origin,
                    )
                )
            return pending
        except (CaptureError, DesktopError):
            raise
        except Exception as exc:  # noqa: BLE001
            # Tipli bir gerekce tasiyan backend hatasi (native yardimcinin
            # REVOKED, DISPLAY_CHANGED, FRAME_TIMEOUT... cevaplari) OLDUGU GIBI
            # gecer. Duz bir CaptureError'a sarilinca provider onu "backend
            # yok" diye raporluyordu: olculdu 2026-09-13, Task 4.2 -- revoke
            # edilmis izin BACKEND_UNAVAILABLE/capability olarak geldi.
            typed = getattr(exc, "desktop_error", None)
            if isinstance(typed, DesktopError):
                raise typed from exc
            # Yayin dustu (monitor uykuda, kompozitor yeniden basladi).
            raise CaptureError(
                f"no frame from the screen share: {exc}. "
                "`desktop_lock` + `desktop_unlock` sets the share up again."
            ) from exc

    # --- yol 2: gnome-screenshot (yedek) --------------------------------
    raw = _grab_canvas(raw_dir, include_pointer)
    pending = []
    with Image.open(raw) as canvas:
        # Goruntu masaustu biriminde mi, yoksa ortak bir olcekte ham piksel
        # mi? Kirpma kutusu o orana gore kuruluyor (Task 7.1).
        ratio = canvas_pixel_ratio(mons, canvas.size)
        for mon in targets:
            staged = staging / f"m{mon.index}.png"
            gx, gy, gw, gh = box_global or (mon.x, mon.y, mon.width, mon.height)
            box = (
                monitorslib.round_half_away(gx * ratio),
                monitorslib.round_half_away(gy * ratio),
                monitorslib.round_half_away((gx + gw) * ratio),
                monitorslib.round_half_away((gy + gh) * ratio),
            )
            size, scaled, scale = _write_crop(canvas, box, staged, scale_long_edge)
            pending.append(
                _Pending(
                    staged, mon, size, scaled, scale, taken_at, topology,
                    (gw, gh), origin,
                )
            )
    return pending


# ------------------------------------------------------------------ genel API
def capture(
    monitor: int | str | None = "all",
    out_dir: Path | None = None,
    scale_long_edge: int = 1280,
    include_pointer: bool = True,
    screencast: Any = None,
    *,
    copy_meta_to: Sequence[Path] = (),
    reserved_dirs: Sequence[Path] = (),
    region: tuple[tuple[int, int, int, int], monitorslib.Monitor] | None = None,
) -> list[Shot]:
    """Ekran goruntusu al ve `out_dir` altina PNG(ler) yaz.

    `region`: `resolve_region`in cevabi; verilirse tek bir goruntu, o
    monitorun o parcasi (Adim 8.5). Kayit parcayi tasiyor, `shot=` ile
    tiklama dogru yere gider.

    `monitor`:
        "all" (varsayilan)  her monitor ayri goruntu
        1 / 2 / "DP-1" / "primary"   tek monitor
        "window"            yalnizca odaktaki pencere (ofset URETMEZ)

    `screencast`: acik bir `ScreenCast` verilirse kareler ORADAN alinir --
    sessiz ve hizli yol. Kapaliysa ya da `monitor="window"` istenirse
    `gnome-screenshot`'a dusulur.

    `copy_meta_to`: kaydin PNG'nin yanina EK OLARAK yayimlanacagi dizinler
    (`pcb-shot --out`). Kopya cekimin parcasi: yazilamazsa cekim de
    yayimlanmaz.

    `reserved_dirs`: `shot=` aramasinin baktigi diger dizinler. Yeni kimlik
    bunlarin hicbirinde kullanilmiyor olmali -- ayni kimlik iki dizinde
    olursa arama ilk buldugunu dondurur ve tiklama baska bir cekimin
    olcegiyle cevrilir.

    Ya butun hedefler yayimlanir ya hicbiri: bir hata durumunda bu cekimden
    diskte hicbir sey kalmaz (bkz. "yayim").
    """
    ok, why = available(screencast)
    if not ok:
        raise CaptureError(why)

    # MUTLAK yol: kayit PNG'nin yerini mutlak yaziyor ve kopya kayit baska
    # bir dizinde durabiliyor.
    out_dir = (Path(out_dir) if out_dir else Path(tempfile.gettempdir())).absolute()
    meta_dirs = _unique([out_dir, *(Path(d).absolute() for d in copy_meta_to)])
    lookup_dirs = _unique(
        [*meta_dirs, *(Path(d).absolute() for d in reserved_dirs)]
    )
    try:
        for directory in meta_dirs:
            directory.mkdir(parents=True, exist_ok=True)
        # mkdtemp: mod 700 ve benzersiz ad. `out_dir`in ICINDE, cunku yayim
        # bir hard link ve ayni dosya sisteminde olmak zorunda.
        staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=out_dir))
    except OSError as exc:
        raise CaptureError(f"Cannot prepare the screenshot directory: {exc}") from exc

    try:
        try:
            pending = _render(
                monitor, staging, scale_long_edge, include_pointer, screencast,
                region,
            )
        except OSError as exc:
            raise CaptureError(f"Cannot produce the screenshot: {exc}") from exc
        return _publish(
            pending,
            out_dir=out_dir,
            staging=staging,
            meta_dirs=meta_dirs,
            lookup_dirs=lookup_dirs,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
