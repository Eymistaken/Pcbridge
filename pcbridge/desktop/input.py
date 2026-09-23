"""Sanal klavye ve fare — /dev/uinput uzerinden.

NEDEN CEKIRDEK SEVIYESI
    Wayland'de harici bir surec girdi enjekte edemiyor: `xdotool` yalnizca
    XWayland pencerelerini gorur, `wtype`'in istedigi protokolu Mutter
    desteklemiyor, XDG RemoteDesktop portali her oturumda makine basinda onay
    istiyor. `/dev/uinput` ile yaratilan sanal cihazi ise Mutter gercek
    donanim sanar: kompozitorden bagimsiz, onay penceresi yok.

OLCULDU (2026-08-01)
    ABS_X/ABS_Y araligi 0..tuval-1 verilen bir cihaz, ABS_X+ABS_Y+BTN_LEFT
    bileskesi sayesinde udev tarafindan ID_INPUT_MOUSE olarak isaretleniyor
    ("VMware mutlak faresi" yolu) ve **tuvalin tamamina** 1:1 esleniyor.
    Cift monitorde 6 noktada olculdu, en buyuk sapma 1 piksel. BTN_TOUCH ya da
    BTN_TOOL_PEN EKLENMEMELI: onlar cihazi dokunmatik ekran/tablet yapar ve
    kompozitor tek bir cikisa baglar -- ikinci monitore ulasilamaz.

IKI FARE CIHAZI (Adim 7)
    Mutlak cihazin yaninda AYRI bir goreli cihaz var (`_make_relative`,
    `move_by`): `REL_X`/`REL_Y` yayiyor ve pointer lock kullanan uygulamalarin
    -- oyunlar, Blender/CAD, WebGL -- okudugu tek hareket bicimi o. Mutlak
    cihaza `REL_X`/`REL_Y` EKLENMEDI, cunku yukaridaki `BTN_TOUCH` dersinin
    aynisi gecerli: sinifllandirmasini degistiren her ekleme <=1 px olcumunu
    gecersiz kilar.

KOORDINATLAR
    Buradaki her fonksiyon **global tuval koordinati** bekler. Monitore ozel
    koordinat `monitors.to_global()` ile cevrilir ve bu donusum yalnizca
    `tools.py` sinirinda, tek bir yerde yapilir.

TURKCE KLAVYE
    uinput ham *keycode* gonderir; ekrana ne dusecegini sistemin XKB duzeni
    belirler. Bu makinede duzen `tr+intl`. Bu yuzden metin girisinin
    varsayilan yolu **pano + Ctrl+V**: duzenden tamamen bagimsiz, uzun
    metinlerde ayrica cok daha hizli. Ham tus yolu (`raw=True`) durur ama
    varsayilan degildir.

IMLEC ISINLANMAZ
    `move()` hedefe tek bir ABS yazmasiyla atlamak yerine ara noktalardan
    gecer. Yol hesabi `move_path()` icinde ve **saf**: cihaz gormez, uyumaz,
    boylece gercek tiklama gondermeden test edilebiliyor.
"""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Any

from . import clipboard as clipboardlib
from . import monitors as monitorslib

try:  # evdev opsiyonel: kurulu degilse yalnizca GUI araclari devre disi kalir
    from evdev import AbsInfo, UInput
    from evdev import ecodes as e

    EVDEV_AVAILABLE = True
    EVDEV_IMPORT_ERROR = ""
except Exception as _exc:  # pragma: no cover - kuruluysa calismaz
    EVDEV_AVAILABLE = False
    EVDEV_IMPORT_ERROR = str(_exc)
    AbsInfo = UInput = None  # type: ignore[assignment]
    e = None  # type: ignore[assignment]


UINPUT_NODE = "/dev/uinput"
BUTTONS = ("left", "right", "middle")

# Cihaz yaratildiktan sonra libinput/udev onu gorup kompozitore tanitana kadar
# gecen sure. Olculdu: 1.5 s fazlasiyla yetiyor, 0.5 s'de ilk olay kayboluyor.
SETTLE_SECONDS = 1.2

# --------------------------------------------------------------- fare hareketi
# Ara nokta araligi. Gercek farelerin bildirim hizi 125 Hz; 8 ms onu tutturuyor.
# OLCULDU 2026-08-03: `time.sleep(0.008)` fiilen 8,07 ms suruyor (sapma
# +0,08 ms) ve 48 adimlik bir hareketin **48 ABS_X + 48 ABS_Y olayinin tamami**
# cihazin kendi event node'undan okundu -- kernel ara noktalari birlestirmiyor,
# SYN_DROPPED yok. (Ilk olcumde 96 yerine 11 olay gorunmustu; sebep okuyucunun
# hareket boyunca hic okumayip evdev istemci kuyrugunu tasirmasiydi.)
MOVE_STEP_SECONDS = 0.008

# Cok kisa mesafelerde bile hareketin gorulebilmesi icin taban sure.
MOVE_MIN_MS = 60.0

DEFAULT_POINTER_SPEED = 5000     # px/s; 0 = isinla (eski davranis)
DEFAULT_POINTER_MAX_MS = 500     # tek hareket bundan uzun surmez

# Basili birakilan tus/dugme bu sureden sonra kendiliginden birakilir.
# 0 = birakma. Bkz. `InputBackend` docstring'i: unutulan bir Ctrl makineyi
# kullanilamaz hale getiriyor ve ajanin bunu gorecegi bir kanal yok.
DEFAULT_HOLD_MAX_SECONDS = 120.0

# Tiklamada dugmenin basili kaldigi sure (Adim 8.3). Eskiden sabit 30 ms'ydi.
# Girdiyi SABIT ARALIKLA yoklayan uygulamalar (oyunlar 50 ms'lik tick'lerle)
# basma ile birakmanin ayni araliga dustugu bir tiklamayi hic gormeyebilir;
# 60 ms bir 50 ms'lik yoklamanin en az birine denk gelmeyi garanti ediyor.
# Cift/uclu tiklamada her basis bu kadar surer; aradaki 80 ms ile birlikte
# basistan basisa 140 ms, GNOME'un 400 ms'lik cift tiklama esiginin altinda.
DEFAULT_CLICK_HOLD_MS = 60
# Cagri basina verilebilecek en uzun basili kalma. Uzun basis bir "basili
# tut" isi; onun yolu `mouse_down`/`mouse_up` ve zamanlayicisi.
MAX_CLICK_HOLD_MS = 1000

# `drag` yumusakligi AYARDAN BAGIMSIZ: tek sicrayista birakilan hareketi cogu
# uygulama surukleme saymiyor. `pointer_speed = 0` verilse bile bu kadar ara
# nokta uretilir.
DRAG_MIN_STEPS = 10

# Diske yazilan son imlec konumu bu kadar eskiyse guvenilmez sayilir. Uzun
# aradan sonra kullanicinin fareyi eliyle oynatmis olmasi kuvvetle muhtemel.
POS_MAX_AGE_SECONDS = 300.0

# ------------------------------------------------------------- goreli hareket
# `move_by` tek cagrida bundan buyuk bir delta gondermez. Tuvalden genis, yani
# gercek bir isi engellemiyor; sinirsiz delta kullanicinin kendi masaustune
# DoS demek olurdu.
MOVE_BY_MAX = 4000

# Parca sayisi tavani: en kotu 64 x 8 ms ~ 512 ms, `move`un olculmus 498 ms'lik
# kosegen tavaniyla ayni mertebe.
MOVE_BY_MAX_CHUNKS = 64

# Parca basina hedeflenen buyukluk. Parcalamanin SONUCU DEGISTIRMEDIGI olculdu
# (asagida `relative_chunks`); bu sayi yalnizca hareketin kac adimda
# gonderilecegini belirliyor, toplamini degil.
MOVE_BY_CHUNK_UNITS = 16


class InputError(RuntimeError):
    """Girdi gonderilemedi — cihaz yok, izin yok ya da parametre gecersiz."""


def click_hold_ms_checked(value: Any) -> int:
    """Tiklama basili kalma suresini dogrula (ms, 0..MAX_CLICK_HOLD_MS)."""
    try:
        ms = int(value)
    except (TypeError, ValueError):
        raise InputError(f"hold_ms must be a number ({value!r} given)") from None
    if not 0 <= ms <= MAX_CLICK_HOLD_MS:
        raise InputError(
            f"hold_ms must be between 0 and {MAX_CLICK_HOLD_MS} ({ms} given); "
            "to hold longer use mouse_down/mouse_up"
        )
    return ms


# --------------------------------------------------------------- tus tablolari
# Bu adlar hem `key()` girdisi hem de klavye cihazinin yetenek listesi. Tek
# kaynak olmasi bilincli: tabloya eklenmeyen bir tusu cihaz da bildirmez,
# yani "gonderdim ama hicbir sey olmadi" durumu olusamaz.
def _build_key_table() -> dict[str, str]:
    t: dict[str, str] = {}
    for ch in "abcdefghijklmnopqrstuvwxyz":
        t[ch] = f"KEY_{ch.upper()}"
    for d in "0123456789":
        t[d] = f"KEY_{d}"
    for n in range(1, 13):
        t[f"f{n}"] = f"KEY_F{n}"
    t.update(
        {
            # degistiriciler
            "ctrl": "KEY_LEFTCTRL",
            "control": "KEY_LEFTCTRL",
            "alt": "KEY_LEFTALT",
            "altgr": "KEY_RIGHTALT",
            "shift": "KEY_LEFTSHIFT",
            "super": "KEY_LEFTMETA",
            "meta": "KEY_LEFTMETA",
            "win": "KEY_LEFTMETA",
            "cmd": "KEY_LEFTMETA",
            # duzenden bagimsiz calisan tuslar (UYGULAMA.md (1.x, in git history) bunlari onerir)
            "return": "KEY_ENTER",
            "enter": "KEY_ENTER",
            "kpenter": "KEY_KPENTER",
            "escape": "KEY_ESC",
            "esc": "KEY_ESC",
            "tab": "KEY_TAB",
            "space": "KEY_SPACE",
            "backspace": "KEY_BACKSPACE",
            "delete": "KEY_DELETE",
            "insert": "KEY_INSERT",
            "home": "KEY_HOME",
            "end": "KEY_END",
            "pageup": "KEY_PAGEUP",
            "pagedown": "KEY_PAGEDOWN",
            "up": "KEY_UP",
            "down": "KEY_DOWN",
            "left": "KEY_LEFT",
            "right": "KEY_RIGHT",
            "capslock": "KEY_CAPSLOCK",
            "printscreen": "KEY_SYSRQ",
            "menu": "KEY_COMPOSE",
            # ASCII isaretleri: ADI keycode'un US duzenindeki karsiligidir,
            # ekrana ne dusecegini sistem duzeni belirler.
            "minus": "KEY_MINUS",
            "equal": "KEY_EQUAL",
            "comma": "KEY_COMMA",
            "dot": "KEY_DOT",
            "period": "KEY_DOT",
            "slash": "KEY_SLASH",
            "backslash": "KEY_BACKSLASH",
            "semicolon": "KEY_SEMICOLON",
            "apostrophe": "KEY_APOSTROPHE",
            "grave": "KEY_GRAVE",
            "leftbrace": "KEY_LEFTBRACE",
            "rightbrace": "KEY_RIGHTBRACE",
        }
    )
    return t


KEY_NAMES = _build_key_table()

# Ham yazma yolu (raw=True) icin ASCII -> (tus, shift gerekli mi).
# US duzeni varsayimiyla; baska duzende yanlis karakter cikar. Bu yuzden
# varsayilan degil.
_RAW_ASCII: dict[str, tuple[str, bool]] = {}
for _c in "abcdefghijklmnopqrstuvwxyz":
    _RAW_ASCII[_c] = (_c, False)
    _RAW_ASCII[_c.upper()] = (_c, True)
for _c, _shifted in zip("1234567890", "!@#$%^&*()"):
    _RAW_ASCII[_c] = (_c, False)
    _RAW_ASCII[_shifted] = (_c, True)
_RAW_ASCII.update(
    {
        " ": ("space", False),
        "\n": ("return", False),
        "\t": ("tab", False),
        "-": ("minus", False),
        "_": ("minus", True),
        "=": ("equal", False),
        "+": ("equal", True),
        ",": ("comma", False),
        "<": ("comma", True),
        ".": ("dot", False),
        ">": ("dot", True),
        "/": ("slash", False),
        "?": ("slash", True),
        "\\": ("backslash", False),
        "|": ("backslash", True),
        ";": ("semicolon", False),
        ":": ("semicolon", True),
        "'": ("apostrophe", False),
        '"': ("apostrophe", True),
        "`": ("grave", False),
        "~": ("grave", True),
        "[": ("leftbrace", False),
        "{": ("leftbrace", True),
        "]": ("rightbrace", False),
        "}": ("rightbrace", True),
    }
)


def key_code(name: str) -> int:
    """Tus adi -> Linux keycode. Bilinmeyen ad InputError."""
    key = name.strip().lower()
    ident = KEY_NAMES.get(key)
    if ident is None:
        raise InputError(
            f"Unknown key: '{name}'. "
            "Examples: return, escape, tab, ctrl, shift, super, f5, a, 1, up. "
            "For a combination: 'ctrl+shift+t'"
        )
    return e.ecodes[ident]


def parse_combo(combo: str) -> list[int]:
    """'ctrl+shift+t' -> [KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_T] (basma sirasi).

    Tus SAYISI sinirsiz: sanal cihazda gercek klavyelerin "ghosting" kisiti
    yok, 'ctrl+shift+alt+a' gibi bir kombinasyon oldugu gibi gider.
    """
    parts = [p for p in str(combo).replace(" ", "").split("+") if p]
    if not parts:
        raise InputError("Empty key combination")
    return [key_code(p) for p in parts]


def key_name(code: int) -> str:
    """Keycode -> insana okunur ad. Bilinmeyen kod ham sayi olarak doner."""
    if e is None:
        return str(code)
    for name, ident in KEY_NAMES.items():
        if e.ecodes.get(ident) == code:
            return name
    return str(code)


# Dugme adlari da KEY_NAMES gibi STRING tanimlayici tutuyor: `evdev` kurulu
# degilse modul yuklenirken `e.BTN_LEFT` okunamaz ve butun masaustu katmani
# import edilemez hale gelir.
BUTTON_IDENTS = {"left": "BTN_LEFT", "right": "BTN_RIGHT", "middle": "BTN_MIDDLE"}


def button_code(button: str) -> int:
    """Dugme adi -> kod. Bilinmeyen ad InputError."""
    key = str(button).strip().lower()
    ident = BUTTON_IDENTS.get(key)
    if ident is None:
        raise InputError(
            f"Unknown button: '{button}'. Valid: left, right, middle"
        )
    return e.ecodes[ident]


def _button_name(code: int) -> str:
    if e is None:
        return str(code)
    for name, ident in BUTTON_IDENTS.items():
        if e.ecodes.get(ident) == code:
            return name
    return str(code)


# --------------------------------------------------------------- fare hareketi
def move_path(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    speed: float = DEFAULT_POINTER_SPEED,
    max_ms: float = DEFAULT_POINTER_MAX_MS,
    step_seconds: float = MOVE_STEP_SECONDS,
    min_steps: int = 1,
) -> list[tuple[int, int]]:
    """(x1,y1) -> (x2,y2) yolundaki ara noktalar. BASLANGIC DAHIL DEGIL.

    SAF: cihaz gormez, uyumaz, kuresel duruma bakmaz. Bu yuzden `batch.py`
    ile ayni gerekcede -- gercek tiklama gondermeden test edilebilsin diye --
    burada duruyor.

    Hiz egrisi **smoothstep** (`t²(3-2t)`): sabit hizla baslayip duran bir
    imlec robotik gorunuyor, smoothstep hizlanma-yavaslama veriyor.

    `speed <= 0` -> tek nokta (isinlama). `min_steps` bunu bile ezer; `drag`
    oradan gecer, cunku sicrayan bir hareketi cogu uygulama surukleme saymaz.
    """
    target = (int(x2), int(y2))
    dist = math.hypot(x2 - x1, y2 - y1)
    if dist < 1:
        return [target]

    if speed > 0:
        duration_ms = min(max(dist / speed * 1000.0, MOVE_MIN_MS), max(1.0, max_ms))
        steps = max(min_steps, int(round(duration_ms / (step_seconds * 1000.0))), 1)
    else:
        steps = max(min_steps, 1)

    points: list[tuple[int, int]] = []
    for i in range(1, steps + 1):
        t = i / steps
        s = t * t * (3 - 2 * t)
        point = (round(x1 + (x2 - x1) * s), round(y1 + (y2 - y1) * s))
        # Ayni pikseli iki kez yazmak bedava degil: her nokta bir syn() demek.
        if not points or points[-1] != point:
            points.append(point)
    if points[-1] != target:
        points.append(target)
    return points


def relative_chunks(
    dx: int,
    dy: int,
    max_chunks: int = MOVE_BY_MAX_CHUNKS,
    chunk_units: int = MOVE_BY_CHUNK_UNITS,
    limit: int = MOVE_BY_MAX,
) -> list[tuple[int, int]]:
    """(dx, dy) goreli deltasini ardisik gonderilecek parcalara bol.

    SAF: cihaz gormez, uyumaz, kuresel duruma bakmaz -- `move_path` ile ayni
    gerekcede burada: parcalama uinput acmadan test edilebilsin diye.

    `move_path`tan FARKLI bir is yapiyor. Orada arasi doldurulacak iki nokta
    var; burada yalnizca bir toplam var, interpole edilecek bir sey yok. Desen
    bu yuzden `scroll`unki: sabit adim, N kez, sert tavan.

    OLCULDU 2026-09-20 (gercek masaustu, iki monitor): parcalamak toplami
    DEGISTIRMIYOR. 200 birim tek olay olarak da, 25x8 / 100x2 / 200x1 olarak da
    **92 piksel** gitti -- libinput artigi biriktiriyor, kucuk adimlar
    kaybolmuyor. Yine de boluyoruz: goreli okuyan bir uygulama ani bir sicrama
    yerine duzgun bir donus gorsun diye.

    SINIR: tek atislik cok kucuk bir delta masaustunde kaybolabilir. Bu
    makinede olcek 0,46 (asagi bkz. `move_by`), yani `dx=1` sifir piksel
    demek. Bir DIZI halinde gonderildiginde kaybolmuyor.
    """
    dx = max(-limit, min(int(dx), limit))
    dy = max(-limit, min(int(dy), limit))
    span = max(abs(dx), abs(dy))
    if span == 0:
        return []
    steps = min(max_chunks, max(1, math.ceil(span / max(1, chunk_units))))
    return list(zip(_share(dx, steps), _share(dy, steps)))


def _share(total: int, steps: int) -> list[int]:
    """`total`i `steps` parcaya, kalani basa dagitarak bol. Toplam korunur."""
    sign = -1 if total < 0 else 1
    base, extra = divmod(abs(total), steps)
    return [sign * (base + (1 if i < extra else 0)) for i in range(steps)]


# ------------------------------------------------------------------- pano yolu
# Pano islemleri `clipboard.py`de: native girdi Ctrl+V'yi gonderirken panoyu
# kimin tuttugu ayri bir karar (Task 5.4). Orkestrasyon asagida, `type_text`te.


# ------------------------------------------------------------------- arka ucun
class InputBackend:
    """Sanal klavye + mutlak fare. Cihazlar tembel acilir, `close()` kapatir.

    `close()` cihazlari yok eder; bu ayni zamanda acil durdurma yoludur
    (`desktop_lock` bunu cagirir, servisin olmesi de ayni etkiyi yapar).

    BASILI TUTMA
        `key_down`/`mouse_down` ile basili birakilan her sey `_held_*`
        kumelerinde takip edilir ve `hold_max_seconds` sonunda bir
        zamanlayici hepsini birakir. Bu sus payi degil: `release` unutulan
        bir Ctrl makineyi kullanilamaz hale getirir ve ajan bunu FARK ETMEZ
        -- kendi gonderdigi tusun hala basili oldugunu gorecegi bir kanal
        yok. Tembel kontrol (bir sonraki cagrida bak) da yetmez, cunku
        "bir sonraki cagri" hic gelmeyebilir.
    """

    def __init__(
        self,
        settle_seconds: float = SETTLE_SECONDS,
        pointer_speed: float = DEFAULT_POINTER_SPEED,
        pointer_max_ms: float = DEFAULT_POINTER_MAX_MS,
        hold_max_seconds: float = DEFAULT_HOLD_MAX_SECONDS,
        pos_file: "Path | str | None" = None,
        clipboard: "clipboardlib.Clipboard | None" = None,
        click_hold_ms: int = DEFAULT_CLICK_HOLD_MS,
    ) -> None:
        self.clipboard: clipboardlib.Clipboard = (
            clipboard if clipboard is not None else clipboardlib.WlClipboard()
        )
        self._kbd: "UInput | None" = None
        self._ptr: "UInput | None" = None
        self._rel: "UInput | None" = None
        self._canvas: tuple[int, int] | None = None
        self._pos_file = Path(pos_file) if pos_file else None
        self._pos: tuple[int, int] | None = self._read_pos()
        # Goreli cihaz imleci tasidiktan sonra MUTLAK cihazin ABS durumu
        # degismemis olur; bkz. `_note_external_motion`.
        self._abs_stale = False
        self._settle = settle_seconds
        self._speed = float(pointer_speed)
        self._max_ms = float(pointer_max_ms)
        self._hold_max = float(hold_max_seconds)
        self._click_hold_ms = click_hold_ms_checked(click_hold_ms)
        self._held_keys: set[int] = set()
        self._held_buttons: set[int] = set()
        self._timer: "threading.Timer | None" = None
        self._lock = threading.RLock()
        # Zamanlayici bir sey biraktiysa burada durur; bir sonraki arac
        # cagrisi bunu kullaniciya bildirsin diye (sessizce olmasin).
        self.auto_released: list[str] = []

    # ------------------------------------------------------------- yasam dongu
    def available(self) -> tuple[bool, str]:
        """(kullanilabilir mi, degilse Turkce gerekce)."""
        if not EVDEV_AVAILABLE:
            return False, (
                f"the python package `evdev` is missing ({EVDEV_IMPORT_ERROR}). "
                "Reinstall pcbridge with its [desktop] extra (`pcbridge setup`)."
            )
        try:
            import os

            fd = os.open(UINPUT_NODE, os.O_WRONLY | os.O_NONBLOCK)
            os.close(fd)
        except FileNotFoundError:
            return False, (
                f"{UINPUT_NODE} does not exist — the `uinput` kernel module is not "
                "loaded. `pcbridge doctor` prints the command that fixes it."
            )
        except PermissionError:
            return False, (
                f"no permission for {UINPUT_NODE}. `pcbridge doctor` prints the "
                "command that installs the udev rule (uaccess ACL)."
            )
        except OSError as exc:
            return False, f"{UINPUT_NODE} acilamadi: {exc}"
        return True, ""

    def _require(self) -> None:
        ok, why = self.available()
        if not ok:
            raise InputError(why)

    def _make_keyboard(self) -> "UInput":
        """Cihazi yarat, BEKLEME. Bekleme cagirana ait (bkz. `ensure`)."""
        caps = {e.EV_KEY: sorted({e.ecodes[v] for v in KEY_NAMES.values()})}
        return UInput(caps, name="pcbridge-keyboard", version=1)

    def _make_pointer(self) -> tuple["UInput", tuple[int, int]]:
        """Cihazi yarat, BEKLEME. Tuval boyutuyla birlikte doner."""
        canvas = monitorslib.canvas_size()
        w, h = canvas
        caps = {
            e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
            e.EV_ABS: [
                (e.ABS_X, AbsInfo(0, 0, w - 1, 0, 0, 0)),
                (e.ABS_Y, AbsInfo(0, 0, h - 1, 0, 0, 0)),
            ],
            e.EV_REL: [e.REL_WHEEL, e.REL_HWHEEL],
        }
        return UInput(caps, name="pcbridge-pointer", version=1), canvas

    def _make_relative(self) -> "UInput":
        """Cihazi yarat, BEKLEME. GORELI fare -- mutlak olanin KARDESI, yerine
        gecmiyor (Adim 7).

        Neden AYRI cihaz: mutlak cihazin iki monitorde 6 noktada <=1 px
        sapmayla calistigi olculdu ve sinifllandirmasini degistiren her ekleme
        o olcumu gecersiz kilar (`BTN_TOUCH` dersi, yukaridaki modul
        docstring'i). Ayri cihaz bu riski sifirliyor.

        DUGMELER SART -- OLCULDU 2026-09-20. `EV_KEY` olmadan yaratilan bir
        `REL_X`/`REL_Y` cihazina udev `ID_INPUT_MOUSE` vermiyor ve imlec HIC
        oynamiyor (dx=50 -> 0 piksel). Dugmeli kardesi ayni cagride 23 piksel
        gitti. Bu yuzden uc dugme ILAN EDILIYOR ama buradan HIC YAYILMIYOR;
        basma/birakma mutlak cihazin isi ve `_held_buttons` yalnizca onu
        izliyor.

        ABS araligi YOK, yani tuval boyutuyla isi yok: `_relative()`
        `_pointer()`un aksine monitor degisiminde cihazi YENIDEN YARATMAZ.
        """
        caps = {
            e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
            e.EV_REL: [e.REL_X, e.REL_Y],
        }
        return UInput(caps, name="pcbridge-pointer-rel", version=1)

    def ensure(
        self, keyboard: bool = False, pointer: bool = False,
        relative: bool = False,
    ) -> float:
        """Istenen cihazlari onceden yarat ve beklemeyi TEK SEFER paylastir.

        Cihazlar normalde tembel aciliyor ve her biri kendi `settle`'ini
        oduyor. Tek surecte hem fare hem klavye gerekince bu iki kat maliyet
        demek -- OLCULDU 2026-08-02: 1,301 + 1,306 = **2,607 s**. Ikisini once
        yaratip sonra bir kez beklemek ayni isi **1,41 s**'de yapiyor
        (yaratma 0,21 s + tek bekleme 1,2 s) ve her iki cihaz da >= settle
        kadar bekletilmis oluyor.

        Fare olayinin gercekten gectigi `IdleMonitor` ile dogrulandi:
        57694 ms -> 404 ms. "Hata vermedi" yeterli kanit sayilmadi.

        `pcb-do` gibi kisa omurlu sureclerde bu maliyet TOPLAM surenin
        cogunlugu; o yuzden eylem listesine bakip bastan cagrilmali.

        Doner: fiilen beklenen saniye (hicbir sey yaratilmadiysa 0).
        """
        need_k = keyboard and self._kbd is None
        need_p = pointer and self._ptr is None
        need_r = relative and self._rel is None
        if not (need_k or need_p or need_r):
            return 0.0
        self._require()
        if need_k:
            self._kbd = self._make_keyboard()
        if need_p:
            self._ptr, self._canvas = self._make_pointer()
            self._pos = self._read_pos()
        if need_r:
            self._rel = self._make_relative()
        time.sleep(self._settle)
        return self._settle

    def _keyboard(self) -> "UInput":
        if self._kbd is None:
            self._require()
            self._kbd = self._make_keyboard()
            time.sleep(self._settle)
        return self._kbd

    def _pointer(self) -> "UInput":
        canvas = monitorslib.canvas_size()
        if self._ptr is not None and self._canvas != canvas:
            # Monitor eklendi/cikarildi: ABS araligi artik yanlis, yeniden yarat.
            self._ptr.close()
            self._ptr = None
        if self._ptr is None:
            self._require()
            self._ptr, self._canvas = self._make_pointer()
            # DISKTEN oku, sifirlama. Cihaz yeni ama imlec yerinde duruyor:
            # sanal cihazi yok etmek kompozitorun imlecini oynatmiyor.
            # Sifirlanirsa `pcb-do`'nun her cagrisi (yeni surec -> yeni cihaz)
            # yine isinlanir -- bu tam olarak yasandi, kullanici fark etti.
            self._pos = self._read_pos()
            time.sleep(self._settle)
        return self._ptr

    def _relative(self) -> "UInput":
        """Goreli cihaz, tembel. `_pointer()`un aksine monitor degisiminde
        YENIDEN YARATILMAZ: ABS araligi yok, tuval boyutu onu ilgilendirmiyor.
        """
        if self._rel is None:
            self._require()
            self._rel = self._make_relative()
            time.sleep(self._settle)
        return self._rel

    def close(self) -> None:
        # Once ACIKCA birak. Cihaz yok edilince kernel'in basili tuslari
        # birakip birakmadigi bu makinede OLCULEMEDI (cihazin event node'u
        # destroy ile birlikte kayboluyor, olay okunamiyor). Olculmemis bir
        # davranisa guvenmek yerine kendimiz birakiyoruz -- maliyeti yok.
        self.release_all()
        for dev in (self._kbd, self._ptr, self._rel):
            try:
                if dev is not None:
                    dev.close()
            except Exception:  # noqa: BLE001 — kapanirken hata yutulur
                pass
        self._kbd = self._ptr = self._rel = None
        self._canvas = self._pos = None
        self._abs_stale = False

    # -------------------------------------------------------- basili tutma
    def held(self) -> list[str]:
        """Su an basili tutulan tus ve dugmelerin adlari."""
        with self._lock:
            names = [key_name(c) for c in sorted(self._held_keys)]
            names += [_button_name(c) for c in sorted(self._held_buttons)]
        return names

    def release_all(self) -> list[str]:
        """Basili olan her seyi birak. Birakilanlarin adini doner.

        Cihazlar kapaliysa bir sey yapmaz: cihaz yoksa basili tus da yok.
        """
        with self._lock:
            self._cancel_timer()
            freed: list[str] = []
            if self._kbd is not None and self._held_keys:
                for code in sorted(self._held_keys, reverse=True):
                    try:
                        self._kbd.write(e.EV_KEY, code, 0)
                        freed.append(key_name(code))
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    self._kbd.syn()
                except Exception:  # noqa: BLE001
                    pass
            if self._ptr is not None and self._held_buttons:
                for code in sorted(self._held_buttons):
                    try:
                        self._ptr.write(e.EV_KEY, code, 0)
                        freed.append(_button_name(code))
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    self._ptr.syn()
                except Exception:  # noqa: BLE001
                    pass
            self._held_keys.clear()
            self._held_buttons.clear()
        return freed

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _arm_timer(self) -> None:
        """Basili bir sey varsa geri sayimi (yeniden) kur, yoksa iptal et."""
        self._cancel_timer()
        if self._hold_max <= 0:
            return
        if not (self._held_keys or self._held_buttons):
            return
        self._timer = threading.Timer(self._hold_max, self._auto_release)
        self._timer.daemon = True
        self._timer.start()

    def _auto_release(self) -> None:
        freed = self.release_all()
        if freed:
            # Sessizce olmasin: bir sonraki arac cagrisi bunu bildirir.
            self.auto_released = freed

    def take_auto_released(self) -> list[str]:
        """Zamanlayicinin biraktiklarini OKU VE TEMIZLE (bir kez bildirilir)."""
        freed, self.auto_released = self.auto_released, []
        return freed

    @property
    def position(self) -> tuple[int, int] | None:
        """Son gonderilen konum. Wayland'de imlec konumu disaridan sorulamaz,
        bu yuzden yalnizca BIZIM gonderdigimiz konumu biliyoruz."""
        return self._pos

    # ------------------------------------------------- konumun surec omru
    # Son konum DISKE yaziliyor. Sebebi `pcb-do`: her cagrisi yeni bir surec
    # ve yeni bir surec son konumu bilmiyor -> her hareket isinlanirdi.
    # GERCEKTEN YASANDI: kullanici "fare yumusak gitmedi, isinlandi" dedi ve
    # hakliydi -- MCP sunucusunda (uzun omurlu) ikinci hareketten itibaren
    # yumusakti, `pcb-do`'da hicbir zaman.
    #
    # Kayit YANILABILIR: kullanici arada fareyi eliyle oynatmis olabilir ve
    # Wayland'de bunu ogrenmenin yolu yok. O durumda hareket yanlis yerden
    # baslar (imlec bir kez sicrar, sonra yumusak gider) -- yani en kotu
    # ihtimalle bugunku davranisa donuyoruz, daha kotusune degil.
    def _read_pos(self) -> tuple[int, int] | None:
        if self._pos_file is None:
            return None
        try:
            data = json.loads(self._pos_file.read_text(encoding="utf-8"))
            if time.time() - float(data["t"]) > POS_MAX_AGE_SECONDS:
                return None
            return (int(data["x"]), int(data["y"]))
        except Exception:  # noqa: BLE001 - bozuk/eksik kayit onemsiz
            return None

    def _write_pos(self) -> None:
        if self._pos_file is None or self._pos is None:
            return
        try:
            self._pos_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._pos_file.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"x": self._pos[0], "y": self._pos[1], "t": time.time()}),
                encoding="utf-8",
            )
            tmp.replace(self._pos_file)   # atomik: yarim dosya okunmasin
        except Exception:  # noqa: BLE001 - yazamamak hareketi bozmamali
            pass

    def _note_external_motion(self) -> None:
        """Imleci BIZIM disimizda bir sey tasidi (goreli cihaz). Iki sonuc:

        1. Kayitli konum bir YALAN. `None` zaten yiginin her katmaninda
           "bilinmiyor" demek (`_read_pos` eksik ya da bayat dosyada `None`
           donuyor, `position` `tuple | None`, protokol `{"position": null}`),
           o yuzden yeni bir isaret icat edilmiyor: dosya siliniyor.
        2. Mutlak cihazin ABS DURUMU degismedi. OLCULDU 2026-09-20: goreli
           cihazla imlec 960'tan 1052'ye tasindiktan sonra mutlak cihazdan
           yine `ABS_X=960` gondermek HICBIR SEY yapmiyor -- cekirdek ayni
           degeri "degisiklik yok" sayip yutuyor ve imlec 1052'de kaliyor.
           961 gondermek calisiyor, ardindan 960 da calisiyor. Yani bir
           sonraki mutlak `move` once bir piksel yana ugramak zorunda.
        """
        self._pos = None
        self._abs_stale = True
        if self._pos_file is None:
            return
        try:
            self._pos_file.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 - silememek hareketi bozmamali
            pass

    # ------------------------------------------------------------------- fare
    def _clamp(self, x: int, y: int) -> tuple[int, int]:
        w, h = monitorslib.canvas_size()
        return (max(0, min(int(x), w - 1)), max(0, min(int(y), h - 1)))

    def move(
        self, x: int, y: int, smooth: bool | None = None, min_steps: int = 1
    ) -> tuple[int, int]:
        """Imleci global tuval koordinatina tasi. Tuval disi deger kirpilir.

        Varsayilan olarak ISINLAMAZ: ara noktalardan gecer (bkz. `move_path`).
        `smooth=False` tek sicrayista gonderir.

        **Ilk hareket kacinilmaz olarak sicrar**: Wayland'de imlecin gercek
        konumu disaridan sorulamiyor, yalnizca BIZIM gonderdigimiz konumu
        biliyoruz (`self._pos`). Cihaz yeni yaratildiginda o da bos.
        """
        ptr = self._pointer()
        cx, cy = self._clamp(x, y)
        start = self._pos
        want = self._speed > 0 if smooth is None else bool(smooth)

        if start is not None and (want or min_steps > 1):
            path = move_path(
                start[0], start[1], cx, cy,
                speed=self._speed if want else 0.0,
                max_ms=self._max_ms,
                min_steps=min_steps,
            )
        else:
            path = [(cx, cy)]

        if self._abs_stale:
            # Goreli bir hareket araya girdi: ayni ABS degerini tekrar
            # gondermek yutulur (bkz. `_note_external_motion`). Bir piksel
            # yana ugra, sonra hedefe.
            path.insert(0, (cx - 1 if cx > 0 else cx + 1, cy))
            self._abs_stale = False

        last = len(path) - 1
        for i, (px, py) in enumerate(path):
            ptr.write(e.EV_ABS, e.ABS_X, px)
            ptr.write(e.EV_ABS, e.ABS_Y, py)
            ptr.syn()
            self._pos = (px, py)
            if i < last:
                time.sleep(MOVE_STEP_SECONDS)
        self._pos = (cx, cy)
        self._write_pos()
        return (cx, cy)

    def move_by(self, dx: int, dy: int) -> tuple[int, int]:
        """Imleci BULUNDUGU yerden `dx`/`dy` kadar kaydir. AYRI cihaz.

        `move` ile ayni is DEGIL ve karistirilmamali. `move` "suraya git"
        diyor: kesin, dogrulanabilir, tiklamanin tek yolu. `move_by` "su kadar
        su yone" diyor ve ekranda bir noktaya ulasmanin yolu DEGIL.

        Var olma sebebi *pointer lock*: imleci gizleyip ortada kilitleyen ve
        kompozitorden goreli hareket okuyan uygulamalar (oyunlar, Blender/CAD
        sahne dondurme, WebGL) mutlak "su noktaya git" mesajini hic gormuyor.
        Kilit disaridan sorulamadigi icin bu yol HER ZAMAN aciktir.

        OLCULDU 2026-09-20 (gercek masaustu): hareket dogrusal, ivme yok
        (kosegen dx=dy=50 her eksende tek eksenli 50 kadar gidiyor). Masaustu
        olcegi kullanicinin GNOME fare hizi ayarina bagli: `k = 1 + speed`,
        bu makinede 1 - 0,54074 = **0,46** (200 birim -> 92 piksel; `speed`
        gecici olarak 0.0 yapilinca oran tam 1,0 oldu). Bu yuzden delta
        PIKSEL DEGIL cihaz birimidir ve olceğe bolunmuyor: bolunseydi
        parametre masaustunde bir sey, kilitli bir oyunda baska bir sey
        anlamina gelirdi.

        `smooth` / `pointer_speed` buraya UYGULANMAZ: interpole edilecek iki
        nokta yok.

        Doner: fiilen gonderilen (dx, dy) -- kirpilmis olabilir.
        """
        chunks = relative_chunks(dx, dy)
        if not chunks:
            return (0, 0)
        rel = self._relative()
        # ONCE unut, SONRA gonder. Emit ortada olurse deltalarin bir kismi
        # gitmistir ve konum yanlistir; "bilinmiyor" o zaman dogru cevaptir.
        # Sonra unutmak, kismi bir hatanin yalanladigi bir konumu beyan etmek
        # olurdu. Sezgiye ters, bu yuzden yaziyor.
        self._note_external_motion()
        last = len(chunks) - 1
        for i, (sx, sy) in enumerate(chunks):
            if sx:
                rel.write(e.EV_REL, e.REL_X, sx)
            if sy:
                rel.write(e.EV_REL, e.REL_Y, sy)
            rel.syn()
            if i < last:
                time.sleep(MOVE_STEP_SECONDS)
        return (sum(s for s, _ in chunks), sum(s for _, s in chunks))

    def _button(self, button: str) -> int:
        return button_code(button)

    def mouse_down(self, button: str = "left") -> None:
        """Dugmeyi basili tut. `mouse_up` gelmezse zamanlayici birakir."""
        ptr = self._pointer()
        code = self._button(button)
        ptr.write(e.EV_KEY, code, 1)
        ptr.syn()
        with self._lock:
            self._held_buttons.add(code)
            self._arm_timer()

    def mouse_up(self, button: str = "left") -> None:
        ptr = self._pointer()
        code = self._button(button)
        ptr.write(e.EV_KEY, code, 0)
        ptr.syn()
        with self._lock:
            self._held_buttons.discard(code)
            self._arm_timer()

    def click(
        self, button: str = "left", count: int = 1, hold_ms: int | None = None
    ) -> None:
        """Imlecin BULUNDUGU yerde tikla. Koordinat almaz: nereye gidilecegi
        cagiranin isi (`move`), yerinde tiklamak da gecerli bir istek (Adim
        8.2) -- goreli bir `move_by`dan sonra bile. ABS olayi gondermedigi
        icin bayat ABS durumuna dokunmaz; sonraki mutlak `move` yine bir
        piksel yana ugrar.

        `hold_ms`: her basisin suresi; verilmezse `click_hold_ms` ayari.
        """
        if count < 1 or count > 3:
            raise InputError("The click count must be between 1 and 3")
        hold = self._click_hold_ms if hold_ms is None else click_hold_ms_checked(hold_ms)
        code = self._button(button)
        ptr = self._pointer()
        for i in range(count):
            ptr.write(e.EV_KEY, code, 1)
            ptr.syn()
            time.sleep(hold / 1000.0)
            ptr.write(e.EV_KEY, code, 0)
            ptr.syn()
            if i < count - 1:
                # Cift tiklama esigi tipik olarak 400 ms; altinda kalmali.
                time.sleep(0.08)

    def drag(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        button: str = "left",
        steps: int = DRAG_MIN_STEPS,
    ) -> None:
        """Basili tutarak surukle.

        Ara adimlar SART: tek sicrayista birakilan hareketi cogu uygulama
        surukleme saymiyor. Bu yuzden `pointer_speed = 0` (isinlama) ayarinda
        bile en az `steps` ara nokta uretilir -- yol hesabi `move_path` ile
        ortak, ayri bir interpolasyon kopyasi tutulmuyor.
        """
        self.move(x1, y1)
        time.sleep(0.05)
        self.mouse_down(button)
        time.sleep(0.05)
        self.move(x2, y2, min_steps=max(1, steps))
        time.sleep(0.05)
        self.mouse_up(button)

    def scroll(self, amount: int, horizontal: bool = False) -> None:
        """Tekerlek tiki. Pozitif = yukari / saga."""
        ptr = self._pointer()
        axis = e.REL_HWHEEL if horizontal else e.REL_WHEEL
        step = 1 if amount > 0 else -1
        for _ in range(min(abs(int(amount)), 100)):
            ptr.write(e.EV_REL, axis, step)
            ptr.syn()
            time.sleep(0.01)

    # ----------------------------------------------------------------- klavye
    def key(self, combo: str) -> None:
        """'ctrl+shift+t' gibi bir kombinasyonu bas ve birak.

        `hold` ile basili tutulan bir tus kombinasyonda da geciyorsa BIRAKILMAZ:
        yoksa `hold("shift")` + `key("shift+home")` dizisi shift'i dusurur ve
        takip kumesi gercekle ayrisirdi (`held()` yalan soylerdi).
        """
        codes = parse_combo(combo)
        kbd = self._keyboard()
        for c in codes:
            kbd.write(e.EV_KEY, c, 1)
        kbd.syn()
        time.sleep(0.03)
        with self._lock:
            held = set(self._held_keys)
        for c in reversed(codes):
            if c in held:
                continue
            kbd.write(e.EV_KEY, c, 0)
        kbd.syn()

    def key_down(self, combo: str) -> None:
        """Tus(lari) basili tut. Kac tus oldugu onemsiz: sanal cihazda gercek
        klavyelerin "ghosting" kisiti yok. `key_up` gelmezse zamanlayici
        birakir (bkz. sinif docstring'i)."""
        kbd = self._keyboard()
        codes = parse_combo(combo)
        for c in codes:
            kbd.write(e.EV_KEY, c, 1)
        kbd.syn()
        with self._lock:
            self._held_keys.update(codes)
            self._arm_timer()

    def key_up(self, combo: str) -> None:
        kbd = self._keyboard()
        codes = parse_combo(combo)
        for c in reversed(codes):
            kbd.write(e.EV_KEY, c, 0)
        kbd.syn()
        with self._lock:
            self._held_keys.difference_update(codes)
            self._arm_timer()

    # ------------------------------------------------------------ metin girisi
    def type_text(
        self, text: str, raw: bool = False, restore_clipboard: bool = True
    ) -> str:
        """Metin yaz. Varsayilan yol PANO (duzenden bagimsiz), `raw=True` ham tus.

        Donen deger, ne yapildigini anlatan kisa bir Turkce not.
        """
        if not text:
            return "empty text, nothing done"
        if raw:
            return self._type_raw(text)
        return self._type_clipboard(text, restore_clipboard)

    def _type_clipboard(self, text: str, restore: bool) -> str:
        saved = self.clipboard.save() if restore else None
        try:
            self.clipboard.put_text(text)
        except clipboardlib.ClipboardError as exc:
            raise InputError(str(exc)) from exc
        time.sleep(0.15)
        self.key("ctrl+v")
        time.sleep(0.25)
        note = f"pasted {len(text)} characters through the clipboard"
        if restore:
            self.clipboard.restore(saved)
            note += "; clipboard restored" if saved else "; clipboard cleared"
        return note

    def _type_raw(self, text: str) -> str:
        """Ham keycode yolu — DUZENE BAGIMLI.

        Sistem duzeni `tr+intl` oldugu icin US varsayimiyla uretilen kodlar
        isaretlerde yanlis karakter verir (`@` yerine `"` gibi). Harf ve
        rakamlarda sorun yok. Bilincli tercih olarak durur.
        """
        unknown: list[str] = []
        for ch in text:
            entry = _RAW_ASCII.get(ch)
            if entry is None:
                unknown.append(ch)
                continue
            name, shift = entry
            self.key(f"shift+{name}" if shift else name)
            time.sleep(0.012)
        note = f"typed {len(text) - len(unknown)} characters as raw keys"
        if unknown:
            note += (
                f"; {len(unknown)} characters skipped ({''.join(sorted(set(unknown)))[:20]}) "
                "— the raw path only handles ASCII; use the clipboard path for other text"
            )
        return note
