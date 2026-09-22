"""Toplu eylem motoru — bir eylem listesini sirayla calistirir.

NEDEN
    Spark her `destructiveHint` cagrisinda telefonda onay soruyor. Menuden tek
    bir ogeyi secmek 4-5 arac cagrisi ediyor: 5 onay, 5 tur ag gecikmesi.
    Kullanilamaz hale geliyor. Burasi o dizinin TEK cagriya sigmasini sagliyor.

MCP'YI TANIMAZ
    Gercek eylemler `Ops` protokolu uzerinden disaridan gelir. Boylece motor
    gercek tiklama gondermeden test edilebilir ve butce/durma mantigi
    `tools.py`'nin icine gomulmez.

OLCULDU 2026-08-02 -- tasarimin uc dayanagi:

1. uinput olayi IdleMonitor'u SIFIRLIYOR (104227 ms -> 151 ms). Yani batch
   icinde "kullanici makinenin basinda mi" kontrolu YAPILAMAZ: batch kendi
   tus basimini kullanici sanip ikinci eylemde kendini durdururdu. Idle
   kontrolu yalnizca batch BASINDA, `SafetyGate` tarafindan yapilir.

2. GNOME overview acikken (yani `super` sonrasi) odakli pencere kalmiyor ve
   Wayland panosu bloklaniyor: `wl-paste` 5 saniyede cevap vermedi. Varsayilan
   `type` yolu panodan gectigi icin orada ASILIR. Bu yuzden `super` sonrasi
   gelen `type` eylemleri kendiliginden ham tus yoluna geciyor (_auto_raw).

3. Kor tiklama odagi kaydiriyor ve sonraki tuslar YANLIS PENCEREYE gidiyor.
   Bu gelistirme sirasinda gercekten oldu: bir olcum tiklamasi masaustune
   dustu, ardindan gonderilen `ctrl+a` + `Delete` masaustundeki 23 ogeyi copa
   gonderdi. Bu yuzden fare tiklamalarindan SONRA odak dogrulanir; degistiyse
   batch durur (`stopped="focus"`). Tek bir `focused()` cagrisi 114 ms.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

# Kimlik BICIMI capture'dan geliyor, burada kopyasi yok. Motor yine gercek
# cihazlari tanimiyor: `shot` bu dosyada yalnizca dogrulanmis bir string,
# koordinata cevrilmesi `ops.py`nin isi.
from .capture import SHOT_ID_RE
# `policy` saf karar tablosu: hata taksonomisinden baskasini import
# etmiyor, yani motor hala gercek cihaz tanimiyor.
from . import policy

# Olculen maliyetler (ms). Butce tahmini bunlardan kuruluyor; uydurma
# sabitlerden degil. Olcum 2026-08-02, bu makine.
COST_MS: dict[str, float] = {
    "key": 30.0,
    "type": 650.0,        # pano yolu; uzunluktan bagimsiz olctuk (12 kr ~ 200 kr)
    # Imlec artik isinlanmiyor, ara noktalardan geciyor (I bolumu). Sure
    # mesafeye bagli ve mesafe burada BILINMIYOR (baslangic konumu motorun
    # disinda), o yuzden orta bir deger: 5000 px/s'de ~1000 piksel. Olculdu
    # 2026-08-03: 960 px -> 186 ms, kosegen -> 498 ms (tavan), 80 px -> 61 ms.
    "move": 200.0,
    # move + yerlesme + tik. Basis 30 ms'den 60 ms'ye cikti (Adim 8.3): her
    # basis +30 ms.
    "click": 320.0,
    "double_click": 380.0,
    "triple_click": 440.0,
    "right_click": 320.0,
    "middle_click": 320.0,
    "drag": 600.0,        # iki hareket + basma/birakma
    "scroll": 60.0,
    # `move` ile ayni mertebe: mesafeye bagli ve mesafe burada da BILINMIYOR.
    # 64 parca tavani en kotu ihtimali ~512 ms'de tutuyor.
    "move_by": 200.0,
    "mouse_down": 30.0,
    "mouse_up": 30.0,
    "hold": 30.0,
    "release": 30.0,
    "ui_click": 400.0,    # yardimci surec baslatma dahil
    "ui_set_text": 400.0,
    # `gtk-launch` (~0,13 sn) + penceresi gorulene kadar izleme (Task 6.4).
    # Olculdu 2026-09-19: kucuk bir GTK4 penceresi 0,68 sn'de odakta. Buyuk
    # uygulamalar daha yavas; izleme batch'in kalan suresiyle sinirli.
    "launch": 1500.0,
    # GNOME aramasi (eklenti yok): olculdu 6616-6935 ms.
    "focus": 7000.0,
    "wait": 0.0,          # asagida ms alanindan gelir
}
# Eklenti kuruluyken `focus`: eklenti 3-6 ms (olculdu 2026-09-12), "zaten
# odakta mi" okumasi native ~7 ms / Python ~100 ms. Hedef kapaliysa baslatma,
# eklenti one alamazsa arama gerekir; o yavas adimlar kalan sureye kendileri
# bakar ve sigmiyorsa `BudgetExceeded` ile hic baslamaz.
FOCUS_FAST_MS = 200.0
# Ilk fare/klavye eyleminde uinput cihazi yaratiliyor: olculdu ~1.3 sn. Sunucu
# omrunde bir kez odenir ama ilk batch'te gorunur, tahmine katiliyor.
FIRST_INPUT_MS = 1350.0

FOCUS_CHECK_MS = 120.0

# Odagi kaydirabilen eylemler. Bunlardan sonra odak dogrulanir.
# `mouse_down` de buraya ait: basma anında odak zaten kayiyor, birakmayi
# beklemeye gerek yok.
POINTER_ACTIONS = {
    "click", "double_click", "triple_click", "right_click", "middle_click",
    "drag", "mouse_down",
}
# Odagi KASITLI degistiren eylemler. Bunlardan sonra beklenen odak guncellenir.
FOCUS_CHANGING = {"launch", "focus"}
INPUT_ACTIONS = {
    "key", "type", "move", "move_by", "click", "double_click", "triple_click",
    "right_click", "middle_click", "drag", "scroll", "mouse_down", "mouse_up",
    "hold", "release",
}

MAX_WAIT_MS = 30_000

# Tiklama eylemleri ve kac basis olduklari.
CLICK_COUNTS = {
    "click": 1, "double_click": 2, "triple_click": 3,
    "right_click": 1, "middle_click": 1,
}
# Tiklamada basili kalma (Adim 8.3). KOPYA: asil degerler `input.py`de
# (`DEFAULT_CLICK_HOLD_MS`, `MAX_CLICK_HOLD_MS`); bu modul cihazlari tanimadigi
# icin burada duruyor ve ayrismalari `test_input_contract` sabitliyor.
DEFAULT_CLICK_HOLD_MS = 60
MAX_CLICK_HOLD_MS = 1000
# Cift/uclu tiklamada tek basisin tavani: basislar GNOME'un 400 ms'lik cift
# tiklama esiginin icinde kalmali (150 + 80 ms aralik = 230 ms).
MULTI_CLICK_HOLD_MAX_MS = 150

# `move_by` delta tavani. KOPYA: asil clamp `input.MOVE_BY_MAX`ta ve orasi son
# sozu soyluyor. Burada duruyor cunku bu modul gercek cihazlari TANIMIYOR
# (bagimlilik tek yonlu: ops -> batch) ve ayrismalari
# `test_batch_safety` tarafindan sabitleniyor. Amaci erken ve okunur bir hata.
MOVE_BY_MAX = 4000


class BatchError(ValueError):
    """Eylem listesi okunamadi. Hicbir sey calistirilmadan atilir."""


class BudgetExceeded(Exception):
    """Bir `Ops` adimi kalan sureye sigmayacak. HICBIR SEY yapilmadi.

    Yalnizca geri alinamaz bir sey yapilmadan ONCE atilir. Motor bunu kendi
    eylem-oncesi butce kontrolu gibi sayar: `stopped="budget"`, eylem
    yapilmayanlara girer.
    """


@dataclass(frozen=True)
class Action:
    a: str
    args: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        if self.a == "wait":
            return f"wait {self.args.get('ms', 0)} ms"
        if self.a in ("key", "hold", "release"):
            return f"{self.a} {self.args.get('keys')!r}"
        if self.a == "mouse_up":
            return f"mouse_up {self.args.get('button')}"
        if self.a == "type":
            n = len(self.args.get("text") or "")
            return f"type ({n} characters)"
        if self.a in ("ui_click", "ui_set_text"):
            return f"{self.a} #{self.args.get('id')}"
        if self.a == "launch":
            return f"launch {self.args.get('app')!r}"
        if self.a == "focus":
            return f"focus {self.args.get('window')!r}"
        if self.a == "move_by":
            # Isaretli ve parantezsiz: bir koordinat gibi okunmasin.
            return f"move_by ({self.args.get('dx'):+d}, {self.args.get('dy'):+d})"
        if self.a in POINTER_ACTIONS or self.a == "move" or self.a == "scroll":
            x, y = self.args.get("x"), self.args.get("y")
            pos = f" ({x}, {y})" if x is not None and y is not None else ""
            # Hangi uzayda calisildigi raporda GORUNSUN: bir tiklama yanlis
            # yere dustugunde ilk sorulacak soru bu.
            shot = self.args.get("shot")
            where = f" @{shot}" if shot else ""
            hold = self.args.get("hold_ms")
            press = f" held {hold} ms" if hold is not None else ""
            return f"{self.a}{pos}{where}{press}"
        return self.a


@dataclass
class Step:
    index: int
    action: str
    ok: bool
    note: str
    ms: float


@dataclass
class Result:
    steps: list[Step]
    total: int
    remaining: list[Action]
    elapsed: float
    stopped: str = ""        # "" | "budget" | "error" | "focus" | "repeat" | "safety"
    detail: str = ""
    focus_start: str = ""
    focus_now: str = ""
    # Liste bittiginde hala basili olanlar. Bos degilse raporda gorunur:
    # `hold` edip `release` etmeyi unutmak sessiz kalmamali.
    held: list[str] = field(default_factory=list)
    error: Exception | None = None
    # Plan butceye sigmadigi icin HIC baslamadiysa tahmini suresi (sn).
    # Bos (None) ise dizi baslamis demektir; `stopped="budget"` o zaman
    # "yarida durdu" anlamina gelir.
    plan_seconds: float | None = None

    @property
    def done(self) -> int:
        return sum(1 for s in self.steps if s.ok)


class Ops(Protocol):
    """Gercek eylemler. `tools.py` bunu InputBackend + UiTree'ye baglar."""

    def key(self, keys: str) -> str: ...
    def type(self, text: str, raw: bool) -> str: ...
    def hold(self, keys: str) -> str: ...
    def release(self, keys: str) -> str: ...
    def move(self, x: int, y: int, monitor: int | None,
             shot: str | None = None) -> str: ...
    # `x`/`y` yoksa imlecin BULUNDUGU yerde tiklanir (Adim 8.2). `hold_ms`
    # yalnizca verildiyse gecer; yoksa cihazin ayari.
    def click(self, button: str, count: int, x: int | None, y: int | None,
              monitor: int | None, shot: str | None = None,
              hold_ms: int | None = None) -> str: ...
    def mouse_down(self, button: str, x: int | None, y: int | None,
                   monitor: int | None, shot: str | None = None) -> str: ...
    def mouse_up(self, button: str) -> str: ...
    def drag(self, x: int, y: int, to_x: int, to_y: int, button: str,
             monitor: int | None, shot: str | None = None) -> str: ...
    def scroll(self, amount: int, x: int | None, y: int | None,
               monitor: int | None, horizontal: bool,
               shot: str | None = None) -> str: ...
    # Goreli kaydirma: koordinat DEGIL delta alir, o yuzden `monitor`/`shot`
    # yok -- hicbir uzaya ait degil.
    def move_by(self, dx: int, dy: int) -> str: ...
    def held(self) -> list[str]: ...
    def release_all(self) -> list[str]: ...
    def ui_click(self, node_id: str) -> str: ...
    def ui_set_text(self, node_id: str, text: str) -> str: ...
    # `budget_left`: bu eylemin surebilecegi en fazla sure (sn). Sigmayacak
    # yavas bir adim `BudgetExceeded` ile hic baslatilmaz.
    def launch(self, app: str, budget_left: float | None = None) -> str: ...
    def focus(self, window: str, budget_left: float | None = None) -> str: ...
    def focused(self) -> str: ...


# --------------------------------------------------------------- ayristirma
def _int(raw: dict, key: str, *, required: bool = False,
         lo: int | None = None, hi: int | None = None) -> int | None:
    if key not in raw or raw[key] is None:
        if required:
            raise BatchError(f"`{raw.get('a')}` needs `{key}`.")
        return None
    try:
        val = int(raw[key])
    except (TypeError, ValueError):
        raise BatchError(
            f"`{key}` of `{raw.get('a')}` must be a number, "
            f"{raw[key]!r} given."
        ) from None
    if lo is not None and val < lo:
        raise BatchError(f"`{key}` must be at least {lo} ({val} given).")
    if hi is not None and val > hi:
        raise BatchError(f"`{key}` must be at most {hi} ({val} given).")
    return val


def _text(raw: dict, key: str) -> str:
    val = raw.get(key)
    if val is None or not str(val).strip():
        raise BatchError(f"`{raw.get('a')}` needs `{key}`.")
    return str(val)


def _button(raw: dict) -> str:
    """Fare dugmesi adi. Motor cihazi tanimadigi icin dogrulama BURADA:
    liste bastan reddedilsin, yarisinda patlamasin."""
    val = str(raw.get("button") or "left").strip().lower()
    if val not in ("left", "right", "middle"):
        raise BatchError(
            f"`button` of `{raw.get('a')}` must be left, right or middle "
            f"({val!r} given)."
        )
    return val


def _shot(raw: dict) -> str | None:
    """Cekim kimligi (`shot`). Bicim BURADA dogrulanir: bozuk bir kimlik
    listeyi bastan reddetsin, uc eylem sonra patlamasin."""
    val = raw.get("shot")
    if val is None or not str(val).strip():
        return None
    text = str(val).strip()
    if not SHOT_ID_RE.match(text):
        raise BatchError(
            f"`shot` of `{raw.get('a')}` is malformed ({text!r}). "
            "Expected something like `m2-a1b2c3`: copy it exactly from the `shot:` "
            "line of the screenshot output."
        )
    return text


def _pair(raw: dict, args: dict) -> None:
    """`x`/`y` ya ikisi birden ya hic (Adim 8.2).

    Ikisi de yoksa eylem imlecin BULUNDUGU yerde calisir -- goreli bir
    `move_by`dan sonra, imlecin kilitli oldugu bir uygulamada tek dogru yol
    bu. Yalnizca biri verilmisse ya da koordinatsiz bir eyleme `shot`/
    `monitor` verilmisse bu buyuk olasilikla unutulmus bir koordinattir;
    sessizce yerinde tiklamak tam da bu katmanin onledigi yanlis tiklama
    olurdu, o yuzden liste reddedilir.
    """
    a = raw.get("a") or raw.get("action")
    if (args.get("x") is None) != (args.get("y") is None):
        raise BatchError(
            f"`{a}` needs `x` and `y` together. Give neither to act "
            "where the pointer already is."
        )
    if args.get("x") is None and (args.get("shot") or args.get("monitor") is not None):
        raise BatchError(
            f"`{a}` has `shot`/`monitor` but no `x`/`y`. Add the coordinate; "
            "to click where the pointer already is, leave out "
            "`shot`/`monitor`."
        )


def _one(raw: Any, index: int) -> Action:
    if not isinstance(raw, dict):
        raise BatchError(
            f"action {index} must be an object, {type(raw).__name__} given."
        )
    a = str(raw.get("a") or raw.get("action") or "").strip().lower()
    if not a:
        raise BatchError(f"action {index} has no `a` field (for example: {{\"a\": \"key\"}}).")

    if a == "wait":
        return Action(a, {"ms": _int(raw, "ms", required=True, lo=0, hi=MAX_WAIT_MS)})
    if a == "key":
        keys = _text(raw, "keys")
        # Kapi AYRISTIRMADA: onaylanmamis bir kapatma listenin ortasinda da
        # olsa hicbir eylem calismaz. Yarim kalmis bir dizi, kapanmis bir
        # pencereden daha zor toparlanir.
        policy.check_key_combo(keys, confirm_close=bool(raw.get("confirm_close")))
        return Action(a, {"keys": keys})
    if a == "type":
        # Metin bos olabilir (bir alani temizlemek gecerli bir istek), o yuzden
        # _text degil: yalnizca alanin VARLIGI aranir.
        if "text" not in raw:
            raise BatchError("`type` needs `text`.")
        return Action(a, {"text": str(raw["text"]), "raw": bool(raw.get("raw", False))})
    if a in ("hold", "release"):
        keys = _text(raw, "keys")
        if a == "hold":
            policy.check_key_combo(
                keys, confirm_close=bool(raw.get("confirm_close"))
            )
        return Action(a, {"keys": keys})
    if a in ("move", "click", "double_click", "triple_click", "right_click",
             "middle_click", "mouse_down"):
        need = a in ("move",)
        args = {
            "x": _int(raw, "x", required=need),
            "y": _int(raw, "y", required=need),
            "monitor": _int(raw, "monitor"),
            "shot": _shot(raw),
        }
        if a == "mouse_down":
            args["button"] = _button(raw)
        _pair(raw, args)
        if a in CLICK_COUNTS:
            hold = _int(raw, "hold_ms", lo=0, hi=MAX_CLICK_HOLD_MS)
            if hold is not None:
                if CLICK_COUNTS[a] > 1 and hold > MULTI_CLICK_HOLD_MAX_MS:
                    raise BatchError(
                        f"`hold_ms` of `{a}` can be at most "
                        f"{MULTI_CLICK_HOLD_MAX_MS} ({hold} given): "
                        "both presses must stay inside the double-click threshold."
                    )
                args["hold_ms"] = hold
        return Action(a, args)
    if a == "mouse_up":
        return Action(a, {"button": _button(raw)})
    if a == "drag":
        return Action(a, {
            "x": _int(raw, "x", required=True),
            "y": _int(raw, "y", required=True),
            "to_x": _int(raw, "to_x", required=True),
            "to_y": _int(raw, "to_y", required=True),
            "button": _button(raw),
            "monitor": _int(raw, "monitor"),
            "shot": _shot(raw),
        })
    if a == "scroll":
        args = {
            "amount": _int(raw, "amount", lo=-50, hi=50) or 3,
            "x": _int(raw, "x"),
            "y": _int(raw, "y"),
            "monitor": _int(raw, "monitor"),
            "shot": _shot(raw),
            "horizontal": bool(raw.get("horizontal", False)),
        }
        _pair(raw, args)
        return Action(a, args)
    if a == "move_by":
        return Action(a, {
            "dx": _int(raw, "dx", required=True, lo=-MOVE_BY_MAX, hi=MOVE_BY_MAX),
            "dy": _int(raw, "dy", required=True, lo=-MOVE_BY_MAX, hi=MOVE_BY_MAX),
        })
    if a == "ui_click":
        return Action(a, {"id": _text(raw, "id").lstrip("#")})
    if a == "ui_set_text":
        if "text" not in raw:
            raise BatchError("`ui_set_text` needs `text`.")
        return Action(a, {"id": _text(raw, "id").lstrip("#"), "text": str(raw["text"])})
    if a == "launch":
        return Action(a, {"app": _text(raw, "app")})
    if a == "focus":
        return Action(a, {"window": _text(raw, "window")})

    raise BatchError(
        f"Unknown action: {a!r}. Valid: key, type, hold, release, "
        "wait, move, click, double_click, triple_click, right_click, "
        "middle_click, mouse_down, mouse_up, drag, scroll, move_by, "
        "ui_click, ui_set_text, launch, focus"
    )


def parse(raw: Any, max_actions: int = 40) -> list[Action]:
    """Eylem listesini oku. Tek bir hata varsa HICBIR sey calistirilmaz.

    Hem JSON metin hem hazir liste kabul edilir: Gemini'nin ic ice sema
    davranisi surume gore degisiyor, metin yolu her zaman calisiyor.
    """
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise BatchError("The action list is empty.")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BatchError(
                f"The action list is not valid JSON ({exc.msg}, line {exc.lineno} "
                f"column {exc.colno}). Example: "
                '[{"a": "key", "keys": "super"}, {"a": "wait", "ms": 400}]'
            ) from None
    if isinstance(raw, dict):
        if "actions" in raw:
            # {"actions": [...]} sarmali; UYGULAMA.md'nin computer_batch ornegi.
            raw = raw["actions"]
        elif "a" in raw:
            # TEK eylem: `pcb-do '{"a":"click","x":2760,"y":312}'`. UYGULAMA.md'nin
            # pcb-do ornegi tam olarak boyle ve ajanin en dogal yazacagi bicim
            # bu -- "bir dizi olmali" diye reddetmek gereksiz surtunmeydi.
            raw = [raw]
    if not isinstance(raw, list):
        raise BatchError(
            f"The action list must be an array, {type(raw).__name__} given."
        )
    if not raw:
        raise BatchError("The action list is empty.")
    if len(raw) > max_actions:
        raise BatchError(
            f"At most {max_actions} actions per call, {len(raw)} given. "
            "Split the list."
        )
    return [_one(item, i) for i, item in enumerate(raw)]


# ------------------------------------------------------------------- tahmin
def cost_ms(action: Action, first_input: bool = False, fast_focus: bool = False) -> float:
    """Tek bir eylemin tahmini maliyeti (ms).

    `fast_focus`: `focus` icin secilen yol eklenti mi (Task 6.4). Cagiran
    `apps.extension_focus_available()` ile sorar; motor D-Bus tanimaz.
    """
    if action.a == "wait":
        return float(action.args.get("ms") or 0)
    if action.a == "focus" and fast_focus:
        return FOCUS_FAST_MS
    base = COST_MS.get(action.a, 200.0)
    hold = action.args.get("hold_ms")
    if hold is not None and action.a in CLICK_COUNTS:
        base += CLICK_COUNTS[action.a] * (hold - DEFAULT_CLICK_HOLD_MS)
    if first_input and action.a in INPUT_ACTIONS:
        base += FIRST_INPUT_MS
    if action.a in POINTER_ACTIONS:
        base += FOCUS_CHECK_MS
    return base


def estimate(actions: list[Action], min_gap: float = 0.0,
             fast_focus: bool = False) -> float:
    """Listenin tahmini toplam suresi (saniye)."""
    total = 0.0
    seen_input = False
    for act in actions:
        total += cost_ms(act, first_input=not seen_input,
                         fast_focus=fast_focus) / 1000.0
        if act.a in INPUT_ACTIONS:
            seen_input = True
        total += min_gap
    return total


# ----------------------------------------------------------------- calisma
def _dispatch(ops: Ops, act: Action, sleep: Callable[[float], None],
              auto_raw: bool, budget_left: float | None = None) -> str:
    a, kw = act.a, act.args
    if a == "wait":
        sleep((kw.get("ms") or 0) / 1000.0)
        return f"waited {kw.get('ms')} ms"
    if a == "key":
        return ops.key(kw["keys"])
    if a == "type":
        raw = bool(kw.get("raw")) or auto_raw
        note = ops.type(kw["text"], raw)
        if auto_raw and not kw.get("raw"):
            note += " (overview open: switched to raw keys, the clipboard is blocked there)"
        return note
    if a == "move":
        return ops.move(kw["x"], kw["y"], kw.get("monitor"), kw.get("shot"))
    if a == "move_by":
        return ops.move_by(kw["dx"], kw["dy"])
    if a in ("hold", "release"):
        return ops.hold(kw["keys"]) if a == "hold" else ops.release(kw["keys"])
    if a in CLICK_COUNTS:
        button = {"right_click": "right", "middle_click": "middle"}.get(a, "left")
        # Sure yalnizca verildiyse gecer: vermeyen cagri cihazin ayarini alir.
        extra = {"hold_ms": kw["hold_ms"]} if kw.get("hold_ms") is not None else {}
        return ops.click(button, CLICK_COUNTS[a], kw.get("x"), kw.get("y"),
                         kw.get("monitor"), kw.get("shot"), **extra)
    if a == "mouse_down":
        return ops.mouse_down(kw["button"], kw.get("x"), kw.get("y"),
                              kw.get("monitor"), kw.get("shot"))
    if a == "mouse_up":
        return ops.mouse_up(kw["button"])
    if a == "drag":
        return ops.drag(kw["x"], kw["y"], kw["to_x"], kw["to_y"], kw["button"],
                        kw.get("monitor"), kw.get("shot"))
    if a == "scroll":
        return ops.scroll(kw["amount"], kw.get("x"), kw.get("y"), kw.get("monitor"),
                          bool(kw.get("horizontal")), kw.get("shot"))
    if a == "ui_click":
        return ops.ui_click(kw["id"])
    if a == "ui_set_text":
        return ops.ui_set_text(kw["id"], kw["text"])
    if a == "launch":
        return ops.launch(kw["app"], budget_left=budget_left)
    if a == "focus":
        return ops.focus(kw["window"], budget_left=budget_left)
    raise BatchError(f"Action cannot be run: {a}")


def run(
    actions: list[Action],
    ops: Ops,
    *,
    budget: float = 90.0,
    min_gap: float = 0.0,
    check_focus: bool = True,
    expect_focus: str = "",
    repeat_limit: int = 3,
    before_action: Callable[[Action], None] | None = None,
    fast_focus: bool = False,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Result:
    """Eylemleri sirayla calistir. Butce, hata ya da odak kaymasinda DUR.

    Butce bitince eyleme BASLAMAZ -- yarim tiklama diye bir sey yok. Kalan
    liste `Result.remaining`'de doner, model nerede kaldigini gorur.

    `expect_focus`: cagiran "bu tiklamayla su pencereye gececegim" diyorsa
    buraya o pencerenin adindan bir parca yazar; odak oraya giderse dizi
    SURER, baska bir yere giderse yine durur.

    OLCULDU 2026-08-03, gercek gorsel ajanla: odak korumasi iki kez ateslendi
    ve ikisinde de dogru davrandi -- ama ajanin plani "editore tikla, sonra
    yaz"di, yani odagin degismesi ISTENEN seydi. Kor bir cagiran icin
    (computer_batch, ekrani yalnizca `ui_dump` ile goruyor) durmak dogru;
    gozu olan bir ajan icin her pencereye tiklama tuzaga basiyordu.
    Cozum korumayi kapatmak DEGIL, niyeti soyletmek: kaza tam da beyan
    edilmemis bir niyetten cikmisti.

    `before_action`: yurutme katmaninin kancasi (`execution.SequenceGuard`).
    Her eylemden ONCE cagrilir; istisna atarsa o eylem gonderilmez ve dizi
    `stopped="safety"` ile durur. Motor kancanin neye baktigini bilmez --
    izin, revoke, sure, ekran kilidi -- yani hala gercek cihaz tanimiyor.

    `fast_focus`: `focus` icin secilen yol eklenti mi. Butce kontrolu o yolun
    maliyetini kullanir; `launch`/`focus` kalan sureyi `budget_left` olarak
    alir ve sigmayan yavas adimi `BudgetExceeded` ile hic baslatmaz.

    PLAN BUTCEYE SIGMIYORSA HIC BASLAMAZ (Adim 8.7). Eylem-oncesi kontrol
    yalnizca "bu eylem kalan sureye sigar mi" diye soruyordu, yani uzun bir
    plan yarisina kadar kosup orada duruyordu. OLCULDU 2026-09-21: dort
    `wait 30000`lik bir batch 60 saniyede iki beklemeyi bitirip durdu, ama
    istemcinin tasima katmani cevabi 60. saniyede birakmisti ("did not respond
    within 60s"): ajan ne yapildigini hic ogrenemedi. Tahmin eylem-oncesi
    kontrolle ayni tablodan (`cost_ms`) geliyor.
    """
    planned = estimate(actions, min_gap, fast_focus=fast_focus)
    if planned > budget:
        waits = sum(float(a.args.get("ms") or 0) for a in actions if a.a == "wait")
        detail = (
            f"the plan needs ~{planned:.1f} s, the budget is {budget:.1f} s; "
            "NO action was sent"
        )
        if waits:
            detail += f" (waits add up to {waits / 1000.0:.1f} s)"
        try:
            held_now = list(ops.held())
        except Exception:  # noqa: BLE001 — durum sorgusu sonucu bozmasin
            held_now = []
        # Basili olanlara DOKUNULMAZ: bu cagri hicbir sey yapmadi, onceki bir
        # cagrinin bilerek tuttugu tusu birakmak ona ait bir karar degil.
        return Result(
            steps=[],
            total=len(actions),
            remaining=list(actions),
            elapsed=0.0,
            stopped="budget",
            detail=detail,
            held=held_now,
            plan_seconds=planned,
        )

    want_focus = (expect_focus or "").strip().lower()
    started = clock()
    steps: list[Step] = []
    stopped = ""
    detail = ""
    seen_input = False
    auto_raw = False           # `super` sonrasi overview: pano bloklu
    focus_start = ""
    focus_now = ""
    caught_error: Exception | None = None
    repeat_key: tuple | None = None   # ayni hedefe ust uste kac tiklama
    repeat_run = 0

    # Odak takibi bir TABAN ister. Taban okunamazsa "odak degisti mi" sorusu
    # cevaplanamaz; eskiden takip sessizce kapaniyor ve tiklamalar korumasiz
    # gidiyordu. Artik planda odagi kaydirabilecek bir eylem varsa HICBIR sey
    # gonderilmez (Task 5.1). Yalnizca tus / ui eylemi olan bir planin
    # dogrulayacagi bir sey yok; o eskisi gibi takipsiz calisir.
    focus_known = True
    if check_focus:
        try:
            focus_start = focus_now = ops.focused()
        except Exception as exc:  # noqa: BLE001 - gerekce rapora yaziliyor
            focus_start = focus_now = ""
            focus_known = False
            if any(a.a in POINTER_ACTIONS for a in actions):
                stopped = "focus"
                detail = (
                    f"the focus could not be read ({str(exc)[:200]}); since focus could not "
                    "be checked after a click, NO action was sent. "
                    "`ui_click` does not look at focus; if a coordinate click is needed, "
                    "first find out why accessibility cannot be read "
                    "(`system_capabilities`)"
                )
            else:
                check_focus = False
                detail = f"the focus could not be read, tracking is off ({str(exc)[:80]})"

    pending = [] if stopped else actions
    i = 0
    for i, act in enumerate(pending):
        elapsed = clock() - started
        need = cost_ms(act, first_input=not seen_input,
                       fast_focus=fast_focus) / 1000.0
        if elapsed + need > budget:
            stopped = "budget"
            detail = (
                f"the next action ({act.describe()}) needs ~{need:.1f} s, "
                f"{max(0.0, budget - elapsed):.1f} s of the budget is left"
            )
            break

        # Ayni hedefe ust uste tiklama: KURALLAR.md sec. 4, madde 6. Kapi
        # eylemden ONCE calisir -- `repeat_limit`inci tiklama hic gonderilmez.
        if repeat_limit and act.a in policy.CLICK_ACTIONS:
            key = policy.click_target_key(act.a, act.args)
            repeat_run = repeat_run + 1 if key == repeat_key else 1
            repeat_key = key
            if repeat_run >= repeat_limit:
                stopped = "repeat"
                detail = (
                    f"click number {repeat_limit} in a row on the same target "
                    f"({act.describe()}) was stopped. If the first two did not have the "
                    "expected effect, a third will not either: read the screen again "
                    "with `ui_dump` or `screen_capture` first"
                )
                break
        elif act.a != "wait":
            repeat_key, repeat_run = None, 0

        # Taban bilinmiyorsa (bir `launch` / `focus` sonrasi okunamadi) bu
        # tiklamanin odagi kaydirip kaydirmadigi dogrulanamaz: GONDERILMEZ.
        if check_focus and not focus_known and act.a in POINTER_ACTIONS:
            stopped = "focus"
            detail = (
                f"action {i} ({act.describe()}) was not sent: after the previous "
                "window change the focus could not be read, so the result of this "
                "click could not be checked"
            )
            break

        # Yurutme katmaninin kancasi: izin, revoke, sure ve ekran kilidi HER
        # eylemden once yeniden okunur. Reddi bir istisnadir; eylem GONDERILMEZ.
        if before_action is not None:
            try:
                before_action(act)
            except Exception as exc:  # noqa: BLE001 - kapali basarisizlik
                stopped = "safety"
                detail = f"action {i} ({act.describe()}) was not sent: {str(exc)[:200]}"
                caught_error = exc
                break

        t0 = clock()
        try:
            note = _dispatch(ops, act, sleep, auto_raw and act.a == "type",
                             budget_left=max(0.0, budget - (t0 - started)))
            ok = True
        except BudgetExceeded as exc:
            # Adim kendi sigmayacagini gordu ve HICBIR SEY yapmadi: eylem-oncesi
            # butce kontrolu gibi, eylem yapilmayanlara girer.
            stopped = "budget"
            detail = f"{act.describe()}: {str(exc)[:160]}"
            break
        except Exception as exc:
            note = str(exc)
            ok = False
            caught_error = exc
        steps.append(Step(i, act.describe(), ok, note[:200], (clock() - t0) * 1000))

        if not ok:
            stopped = "error"
            detail = f"action {i} ({act.describe()}) failed: {note[:120]}"
            i += 1
            break

        if act.a in INPUT_ACTIONS:
            seen_input = True
        # `super` overview'i acar, `Escape`/`Return` kapatir. Pano orada bloklu.
        if act.a == "key":
            keys = str(act.args.get("keys", "")).strip().lower()
            if keys in ("super", "super_l", "super_r"):
                auto_raw = True
            elif keys in ("escape", "return", "kp_enter", "enter"):
                auto_raw = False

        if check_focus and act.a in FOCUS_CHANGING:
            try:
                focus_now = focus_start = ops.focused()
                focus_known = True
            except Exception:  # noqa: BLE001 - sonraki tiklama gonderilmeyecek
                focus_known = False
        elif check_focus and act.a in POINTER_ACTIONS:
            try:
                focus_now = ops.focused()
            except Exception as exc:  # noqa: BLE001
                # Okunamayan odak DEGISMEMIS sayilmaz. Eskiden sayiliyordu, yani
                # korumanin tam da gerektigi anda (odak bilinmiyor) sonraki
                # tuslar gidiyordu.
                focus_known = False
                if i + 1 < len(actions):
                    stopped = "focus"
                    detail = (
                        f"the focus could not be read after the click ({str(exc)[:200]}); "
                        "there was no way to check that the next actions reach the "
                        "right window, so it stopped"
                    )
                    i += 1
                    break
                detail = f"the focus could not be read after the last click ({str(exc)[:80]})"
                continue
            if focus_now != focus_start:
                if want_focus and want_focus in focus_now.lower():
                    # Beklenen pencereye gecildi: niyet onceden beyan edilmisti.
                    # Yeni odak taban aliniyor ki bir SONRAKI kayma yine yakalansin.
                    focus_start = focus_now
                else:
                    stopped = "focus"
                    detail = (
                        f"the focus changed after a click: {focus_start!r} -> "
                        f"{focus_now!r}. The next keys would have gone to the wrong window, "
                        "so it stopped"
                    )
                    if want_focus:
                        detail += f" (expected: {expect_focus!r})"
                    else:
                        detail += (
                            ". If this click was MEANT to switch windows, "
                            "name the target window in `expect_focus` "
                            "beforehand"
                        )
                    i += 1
                    break

        if min_gap:
            sleep(min_gap)
    else:
        i = len(pending)

    # Basili kalan var mi? Liste DUZGUN bittiyse birakilmaz -- "tut, sonraki
    # cagrida tikla" mesru bir kullanim. Ama dizi yarida kaldiysa (hata,
    # butce, odak kaymasi) basili kalan bir tus artik plansizdir: kimse onu
    # birakmayi ustlenmemis olur.
    held: list[str] = []
    try:
        held = list(ops.held())
    except Exception:  # noqa: BLE001 — durum sorgusu sonucu bozmasin
        held = []
    if held and stopped:
        try:
            ops.release_all()
            detail += f" · held input released ({', '.join(held)})"
            held = []
        except Exception:  # noqa: BLE001
            pass

    return Result(
        steps=steps,
        total=len(actions),
        remaining=list(actions[i:]),
        elapsed=clock() - started,
        stopped=stopped,
        detail=detail,
        focus_start=focus_start,
        focus_now=focus_now,
        held=held,
        error=caught_error,
    )


# ---------------------------------------------------------------- bicimleme
def describe(result: Result) -> str:
    """Sonucu modele gosterilecek duz metne cevir."""
    head = (
        f"**{result.done} of {result.total} actions done** "
        f"({result.elapsed:.1f} s)"
    )
    lines = [head, ""]
    for s in result.steps:
        mark = "  " if s.ok else "✗ "
        lines.append(f"{mark}{s.index}. {s.action} — {s.note}")

    if result.stopped:
        lines.append("")
        reason = {
            "budget": ("⏱️ The plan does not fit the time budget"
                       if result.plan_seconds is not None
                       else "⏱️ The time budget ran out"),
            "error": "⛔ Action failed",
            "focus": "⚠️ Focus moved",
            "repeat": "🔁 Repeated click on the same target",
            "safety": "🔒 Safety gate",
        }.get(result.stopped, result.stopped)
        lines.append(f"{reason}: {result.detail}")

    if result.held:
        lines.append("")
        lines.append(
            f"⌨️ STILL HELD: {', '.join(result.held)} — send `release` / "
            "`mouse_up` when you are done. Otherwise the server releases them after "
            "a while, but until then the user cannot use the "
            "machine."
        )

    if result.remaining:
        lines.append("")
        lines.append(f"{len(result.remaining)} action(s) not done:")
        for act in result.remaining:
            lines.append(f"  - {act.describe()}")
        if result.stopped == "budget" and result.plan_seconds is not None:
            lines.append(
                "The list was refused up front; none of it ran. Split it into "
                "separate calls or shorten the waits; to wait for something to "
                "appear on the screen, use `wait_for_text` instead of a blind "
                "`wait`."
            )
        elif result.stopped == "budget":
            lines.append(
                "You can send the rest in a new call; first check that the screen "
                "really is in the state you expect."
            )
        elif result.stopped == "focus":
            lines.append(
                "First look where you are with ui_dump. Using ui_click instead of "
                "coordinate clicks removes this problem "
                "entirely."
            )
        elif result.stopped == "safety":
            lines.append(
                "Before sending the rest, check the grant and the screen again: "
                "`system_capabilities`, and if needed a new `desktop_unlock` from "
                "the user."
            )
    return "\n".join(lines)
