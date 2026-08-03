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
"""

from __future__ import annotations

import subprocess
import time

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


class InputError(RuntimeError):
    """Girdi gonderilemedi — cihaz yok, izin yok ya da parametre gecersiz."""


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
            # duzenden bagimsiz calisan tuslar (UYGULAMA.md bunlari onerir)
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
            f"Bilinmeyen tus: '{name}'. "
            "Ornekler: return, escape, tab, ctrl, shift, super, f5, a, 1, up. "
            "Kombinasyon icin: 'ctrl+shift+t'"
        )
    return e.ecodes[ident]


def parse_combo(combo: str) -> list[int]:
    """'ctrl+shift+t' -> [KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_T] (basma sirasi)."""
    parts = [p for p in str(combo).replace(" ", "").split("+") if p]
    if not parts:
        raise InputError("Bos tus kombinasyonu")
    return [key_code(p) for p in parts]


# ------------------------------------------------------------------- pano yolu
def _wl_read(args: list[str], timeout: int = 10):
    """wl-paste gibi okuyup CIKAN komutlar: ciktisini yakalayabiliriz."""
    return subprocess.run(args, capture_output=True, timeout=timeout, check=False)


def _wl_copy(args: list[str], data: bytes | None = None, timeout: int = 10):
    """wl-copy: stdout/stderr YAKALANMAZ, yoksa asilir.

    wl-copy panonun sahibi olarak arka planda yasamaya devam ediyor (Wayland'de
    pano icerigini kaynak surec servis eder). capture_output=True verilirse
    Python borularin EOF vermesini bekler, o boruları da arka plandaki cocuk
    tutar -> komut bitmis olsa bile run() zaman asimina ugrar. Olculdu: 10 s
    timeout ile "keyboard type" araci tamamen kilitleniyordu.
    """
    return subprocess.run(
        args,
        input=data,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )


def _clipboard_save() -> tuple[str, bytes] | None:
    """Panonun mevcut icerigini (tip, bayt) olarak yedekle. Bossa None.

    Yalnizca TEK bir mime tipi saklanir (listedeki ilki). Pano birden fazla
    temsil sunuyorsa (orn. hem text/html hem text/plain) geri yuklemede
    zenginlestirilmis bicim kaybolur; metin icerik korunur.
    """
    types = _wl_read(["wl-paste", "--list-types"])
    if types.returncode != 0 or not types.stdout.strip():
        return None
    mime = types.stdout.decode("utf-8", "replace").splitlines()[0].strip()
    args = ["wl-paste", "--type", mime]
    if mime.startswith("text/"):
        args.append("--no-newline")
    got = _wl_read(args)
    if got.returncode != 0:
        return None
    return (mime, got.stdout)


def _clipboard_restore(saved: tuple[str, bytes] | None) -> None:
    if saved is None:
        _wl_copy(["wl-copy", "--clear"])
        return
    mime, data = saved
    _wl_copy(["wl-copy", "--type", mime], data=data)


# ------------------------------------------------------------------- arka ucun
class InputBackend:
    """Sanal klavye + mutlak fare. Cihazlar tembel acilir, `close()` kapatir.

    `close()` cihazlari yok eder; bu ayni zamanda acil durdurma yoludur
    (`desktop_lock` bunu cagirir, servisin olmesi de ayni etkiyi yapar).
    """

    def __init__(self, settle_seconds: float = SETTLE_SECONDS) -> None:
        self._kbd: "UInput | None" = None
        self._ptr: "UInput | None" = None
        self._canvas: tuple[int, int] | None = None
        self._pos: tuple[int, int] | None = None
        self._settle = settle_seconds

    # ------------------------------------------------------------- yasam dongu
    def available(self) -> tuple[bool, str]:
        """(kullanilabilir mi, degilse Turkce gerekce)."""
        if not EVDEV_AVAILABLE:
            return False, (
                f"python paketi `evdev` yok ({EVDEV_IMPORT_ERROR}). "
                "Kurulum: ./.venv/bin/pip install -r requirements.txt"
            )
        try:
            import os

            fd = os.open(UINPUT_NODE, os.O_WRONLY | os.O_NONBLOCK)
            os.close(fd)
        except FileNotFoundError:
            return False, (
                f"{UINPUT_NODE} yok — `uinput` cekirdek modulu yuklu degil. "
                "Cozum: sudo ./setup_uinput.sh"
            )
        except PermissionError:
            return False, (
                f"{UINPUT_NODE} icin izin yok. Cozum: sudo ./setup_uinput.sh "
                "(udev kurali + uaccess ACL kurar)"
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

    def ensure(self, keyboard: bool = False, pointer: bool = False) -> float:
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
        if not (need_k or need_p):
            return 0.0
        self._require()
        if need_k:
            self._kbd = self._make_keyboard()
        if need_p:
            self._ptr, self._canvas = self._make_pointer()
            self._pos = None
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
            self._pos = None
            time.sleep(self._settle)
        return self._ptr

    def close(self) -> None:
        for dev in (self._kbd, self._ptr):
            try:
                if dev is not None:
                    dev.close()
            except Exception:  # noqa: BLE001 — kapanirken hata yutulur
                pass
        self._kbd = self._ptr = None
        self._canvas = self._pos = None

    @property
    def position(self) -> tuple[int, int] | None:
        """Son gonderilen konum. Wayland'de imlec konumu disaridan sorulamaz,
        bu yuzden yalnizca BIZIM gonderdigimiz konumu biliyoruz."""
        return self._pos

    # ------------------------------------------------------------------- fare
    def _clamp(self, x: int, y: int) -> tuple[int, int]:
        w, h = monitorslib.canvas_size()
        return (max(0, min(int(x), w - 1)), max(0, min(int(y), h - 1)))

    def move(self, x: int, y: int) -> tuple[int, int]:
        """Imleci global tuval koordinatina tasi. Tuval disi deger kirpilir."""
        ptr = self._pointer()
        cx, cy = self._clamp(x, y)
        ptr.write(e.EV_ABS, e.ABS_X, cx)
        ptr.write(e.EV_ABS, e.ABS_Y, cy)
        ptr.syn()
        self._pos = (cx, cy)
        return (cx, cy)

    def _button(self, button: str) -> int:
        key = str(button).strip().lower()
        codes = {"left": e.BTN_LEFT, "right": e.BTN_RIGHT, "middle": e.BTN_MIDDLE}
        if key not in codes:
            raise InputError(f"Bilinmeyen dugme: '{button}'. Gecerli: left, right, middle")
        return codes[key]

    def mouse_down(self, button: str = "left") -> None:
        ptr = self._pointer()
        ptr.write(e.EV_KEY, self._button(button), 1)
        ptr.syn()

    def mouse_up(self, button: str = "left") -> None:
        ptr = self._pointer()
        ptr.write(e.EV_KEY, self._button(button), 0)
        ptr.syn()

    def click(self, button: str = "left", count: int = 1) -> None:
        if count < 1 or count > 3:
            raise InputError("Tiklama sayisi 1-3 arasinda olmali")
        code = self._button(button)
        ptr = self._pointer()
        for i in range(count):
            ptr.write(e.EV_KEY, code, 1)
            ptr.syn()
            time.sleep(0.03)
            ptr.write(e.EV_KEY, code, 0)
            ptr.syn()
            if i < count - 1:
                # Cift tiklama esigi tipik olarak 400 ms; altinda kalmali.
                time.sleep(0.08)

    def drag(
        self, x1: int, y1: int, x2: int, y2: int, button: str = "left", steps: int = 12
    ) -> None:
        """Basili tutarak surukle. Ara adimlar sart: tek sicrayista birakilan
        hareketi cogu uygulama surukleme saymiyor."""
        self.move(x1, y1)
        time.sleep(0.05)
        self.mouse_down(button)
        time.sleep(0.05)
        sx, sy = self._clamp(x1, y1)
        ex, ey = self._clamp(x2, y2)
        for i in range(1, max(1, steps) + 1):
            t = i / max(1, steps)
            self.move(round(sx + (ex - sx) * t), round(sy + (ey - sy) * t))
            time.sleep(0.02)
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
        """'ctrl+shift+t' gibi bir kombinasyonu bas ve birak."""
        codes = parse_combo(combo)
        kbd = self._keyboard()
        for c in codes:
            kbd.write(e.EV_KEY, c, 1)
        kbd.syn()
        time.sleep(0.03)
        for c in reversed(codes):
            kbd.write(e.EV_KEY, c, 0)
        kbd.syn()

    def key_down(self, combo: str) -> None:
        kbd = self._keyboard()
        for c in parse_combo(combo):
            kbd.write(e.EV_KEY, c, 1)
        kbd.syn()

    def key_up(self, combo: str) -> None:
        kbd = self._keyboard()
        for c in reversed(parse_combo(combo)):
            kbd.write(e.EV_KEY, c, 0)
        kbd.syn()

    # ------------------------------------------------------------ metin girisi
    def type_text(
        self, text: str, raw: bool = False, restore_clipboard: bool = True
    ) -> str:
        """Metin yaz. Varsayilan yol PANO (duzenden bagimsiz), `raw=True` ham tus.

        Donen deger, ne yapildigini anlatan kisa bir Turkce not.
        """
        if not text:
            return "bos metin, hicbir sey yapilmadi"
        if raw:
            return self._type_raw(text)
        return self._type_clipboard(text, restore_clipboard)

    def _type_clipboard(self, text: str, restore: bool) -> str:
        saved = _clipboard_save() if restore else None
        try:
            put = _wl_copy(
                ["wl-copy", "--type", "text/plain;charset=utf-8"], data=text.encode()
            )
        except FileNotFoundError as exc:
            raise InputError(
                "wl-copy bulunamadi: sudo apt install wl-clipboard"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise InputError("wl-copy yanit vermedi (pano sunucusu takilmis olabilir)") from exc
        if put.returncode != 0:
            raise InputError(
                f"wl-copy exit {put.returncode} verdi. Wayland oturumu gorunuyor mu? "
                "(WAYLAND_DISPLAY servise aktarilmis olmali)"
            )
        time.sleep(0.15)
        self.key("ctrl+v")
        time.sleep(0.25)
        note = f"pano yoluyla {len(text)} karakter yapistirildi"
        if restore:
            _clipboard_restore(saved)
            note += "; pano eski icerigine donduruldu" if saved else "; pano temizlendi"
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
        note = f"ham tus yoluyla {len(text) - len(unknown)} karakter yazildi"
        if unknown:
            note += (
                f"; {len(unknown)} karakter atlandi ({''.join(sorted(set(unknown)))[:20]}) "
                "— ham yol yalnizca ASCII destekler, Turkce icin pano yolunu kullan"
            )
        return note
