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
    "click": 290.0,       # move + yerlesme + tik
    "double_click": 320.0,
    "triple_click": 350.0,
    "right_click": 290.0,
    "middle_click": 290.0,
    "drag": 600.0,        # iki hareket + basma/birakma
    "scroll": 60.0,
    "mouse_down": 30.0,
    "mouse_up": 30.0,
    "hold": 30.0,
    "release": 30.0,
    "ui_click": 400.0,    # yardimci surec baslatma dahil
    "ui_set_text": 400.0,
    "launch": 300.0,
    "focus": 7000.0,      # GNOME aramasi: super + yazma + Return, olculdu 6.5 sn
    "wait": 0.0,          # asagida ms alanindan gelir
}
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
    "key", "type", "move", "click", "double_click", "triple_click",
    "right_click", "middle_click", "drag", "scroll", "mouse_down", "mouse_up",
    "hold", "release",
}

MAX_WAIT_MS = 30_000


class BatchError(ValueError):
    """Eylem listesi okunamadi. Hicbir sey calistirilmadan atilir."""


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
            return f"type ({n} karakter)"
        if self.a in ("ui_click", "ui_set_text"):
            return f"{self.a} #{self.args.get('id')}"
        if self.a == "launch":
            return f"launch {self.args.get('app')!r}"
        if self.a == "focus":
            return f"focus {self.args.get('window')!r}"
        if self.a in POINTER_ACTIONS or self.a == "move" or self.a == "scroll":
            x, y = self.args.get("x"), self.args.get("y")
            pos = f" ({x}, {y})" if x is not None and y is not None else ""
            # Hangi uzayda calisildigi raporda GORUNSUN: bir tiklama yanlis
            # yere dustugunde ilk sorulacak soru bu.
            shot = self.args.get("shot")
            where = f" @{shot}" if shot else ""
            return f"{self.a}{pos}{where}"
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
    stopped: str = ""        # "" | "budget" | "error" | "focus"
    detail: str = ""
    focus_start: str = ""
    focus_now: str = ""
    # Liste bittiginde hala basili olanlar. Bos degilse raporda gorunur:
    # `hold` edip `release` etmeyi unutmak sessiz kalmamali.
    held: list[str] = field(default_factory=list)
    error: Exception | None = None

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
    def click(self, button: str, count: int, x: int | None, y: int | None,
              monitor: int | None, shot: str | None = None) -> str: ...
    def mouse_down(self, button: str, x: int | None, y: int | None,
                   monitor: int | None, shot: str | None = None) -> str: ...
    def mouse_up(self, button: str) -> str: ...
    def drag(self, x: int, y: int, to_x: int, to_y: int, button: str,
             monitor: int | None, shot: str | None = None) -> str: ...
    def scroll(self, amount: int, x: int | None, y: int | None,
               monitor: int | None, horizontal: bool,
               shot: str | None = None) -> str: ...
    def held(self) -> list[str]: ...
    def release_all(self) -> list[str]: ...
    def ui_click(self, node_id: str) -> str: ...
    def ui_set_text(self, node_id: str, text: str) -> str: ...
    def launch(self, app: str) -> str: ...
    def focus(self, window: str) -> str: ...
    def focused(self) -> str: ...


# --------------------------------------------------------------- ayristirma
def _int(raw: dict, key: str, *, required: bool = False,
         lo: int | None = None, hi: int | None = None) -> int | None:
    if key not in raw or raw[key] is None:
        if required:
            raise BatchError(f"`{raw.get('a')}` eyleminde `{key}` zorunlu.")
        return None
    try:
        val = int(raw[key])
    except (TypeError, ValueError):
        raise BatchError(
            f"`{raw.get('a')}` eyleminde `{key}` sayi olmali, "
            f"{raw[key]!r} verildi."
        ) from None
    if lo is not None and val < lo:
        raise BatchError(f"`{key}` en az {lo} olabilir ({val} verildi).")
    if hi is not None and val > hi:
        raise BatchError(f"`{key}` en fazla {hi} olabilir ({val} verildi).")
    return val


def _text(raw: dict, key: str) -> str:
    val = raw.get(key)
    if val is None or not str(val).strip():
        raise BatchError(f"`{raw.get('a')}` eyleminde `{key}` zorunlu.")
    return str(val)


def _button(raw: dict) -> str:
    """Fare dugmesi adi. Motor cihazi tanimadigi icin dogrulama BURADA:
    liste bastan reddedilsin, yarisinda patlamasin."""
    val = str(raw.get("button") or "left").strip().lower()
    if val not in ("left", "right", "middle"):
        raise BatchError(
            f"`{raw.get('a')}` eyleminde `button` left, right ya da middle "
            f"olmali ({val!r} verildi)."
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
            f"`{raw.get('a')}` eyleminde `shot` bicimi gecersiz ({text!r}). "
            "Beklenen `m2-a1b2c3` — ekran goruntusu ciktisindaki `shot:` "
            "satirindan aynen kopyalayin."
        )
    return text


def _one(raw: Any, index: int) -> Action:
    if not isinstance(raw, dict):
        raise BatchError(
            f"{index}. eylem bir nesne olmali, {type(raw).__name__} verildi."
        )
    a = str(raw.get("a") or raw.get("action") or "").strip().lower()
    if not a:
        raise BatchError(f"{index}. eylemde `a` alani yok (ornek: {{\"a\": \"key\"}}).")

    if a == "wait":
        return Action(a, {"ms": _int(raw, "ms", required=True, lo=0, hi=MAX_WAIT_MS)})
    if a == "key":
        return Action(a, {"keys": _text(raw, "keys")})
    if a == "type":
        # Metin bos olabilir (bir alani temizlemek gecerli bir istek), o yuzden
        # _text degil: yalnizca alanin VARLIGI aranir.
        if "text" not in raw:
            raise BatchError("`type` eyleminde `text` zorunlu.")
        return Action(a, {"text": str(raw["text"]), "raw": bool(raw.get("raw", False))})
    if a in ("hold", "release"):
        return Action(a, {"keys": _text(raw, "keys")})
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
        return Action(a, {
            "amount": _int(raw, "amount", lo=-50, hi=50) or 3,
            "x": _int(raw, "x"),
            "y": _int(raw, "y"),
            "monitor": _int(raw, "monitor"),
            "shot": _shot(raw),
            "horizontal": bool(raw.get("horizontal", False)),
        })
    if a == "ui_click":
        return Action(a, {"id": _text(raw, "id").lstrip("#")})
    if a == "ui_set_text":
        if "text" not in raw:
            raise BatchError("`ui_set_text` eyleminde `text` zorunlu.")
        return Action(a, {"id": _text(raw, "id").lstrip("#"), "text": str(raw["text"])})
    if a == "launch":
        return Action(a, {"app": _text(raw, "app")})
    if a == "focus":
        return Action(a, {"window": _text(raw, "window")})

    raise BatchError(
        f"Bilinmeyen eylem: {a!r}. Gecerli olanlar: key, type, hold, release, "
        "wait, move, click, double_click, triple_click, right_click, "
        "middle_click, mouse_down, mouse_up, drag, scroll, ui_click, "
        "ui_set_text, launch, focus"
    )


def parse(raw: Any, max_actions: int = 40) -> list[Action]:
    """Eylem listesini oku. Tek bir hata varsa HICBIR sey calistirilmaz.

    Hem JSON metin hem hazir liste kabul edilir: Gemini'nin ic ice sema
    davranisi surume gore degisiyor, metin yolu her zaman calisiyor.
    """
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise BatchError("Eylem listesi bos.")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BatchError(
                f"Eylem listesi gecerli JSON degil ({exc.msg}, satir {exc.lineno} "
                f"sutun {exc.colno}). Ornek: "
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
            f"Eylem listesi bir dizi olmali, {type(raw).__name__} verildi."
        )
    if not raw:
        raise BatchError("Eylem listesi bos.")
    if len(raw) > max_actions:
        raise BatchError(
            f"En fazla {max_actions} eylem gonderilebilir, {len(raw)} verildi. "
            "Listeyi bolun."
        )
    return [_one(item, i) for i, item in enumerate(raw)]


# ------------------------------------------------------------------- tahmin
def cost_ms(action: Action, first_input: bool = False) -> float:
    """Tek bir eylemin tahmini maliyeti (ms)."""
    if action.a == "wait":
        return float(action.args.get("ms") or 0)
    base = COST_MS.get(action.a, 200.0)
    if first_input and action.a in INPUT_ACTIONS:
        base += FIRST_INPUT_MS
    if action.a in POINTER_ACTIONS:
        base += FOCUS_CHECK_MS
    return base


def estimate(actions: list[Action], min_gap: float = 0.0) -> float:
    """Listenin tahmini toplam suresi (saniye)."""
    total = 0.0
    seen_input = False
    for act in actions:
        total += cost_ms(act, first_input=not seen_input) / 1000.0
        if act.a in INPUT_ACTIONS:
            seen_input = True
        total += min_gap
    return total


# ----------------------------------------------------------------- calisma
def _dispatch(ops: Ops, act: Action, sleep: Callable[[float], None],
              auto_raw: bool) -> str:
    a, kw = act.a, act.args
    if a == "wait":
        sleep((kw.get("ms") or 0) / 1000.0)
        return f"{kw.get('ms')} ms beklendi"
    if a == "key":
        return ops.key(kw["keys"])
    if a == "type":
        raw = bool(kw.get("raw")) or auto_raw
        note = ops.type(kw["text"], raw)
        if auto_raw and not kw.get("raw"):
            note += " (overview acik: ham tus yoluna gecildi, pano orada bloklu)"
        return note
    if a == "move":
        return ops.move(kw["x"], kw["y"], kw.get("monitor"), kw.get("shot"))
    if a in ("hold", "release"):
        return ops.hold(kw["keys"]) if a == "hold" else ops.release(kw["keys"])
    if a in ("click", "double_click", "triple_click", "right_click", "middle_click"):
        button = {"right_click": "right", "middle_click": "middle"}.get(a, "left")
        count = {"double_click": 2, "triple_click": 3}.get(a, 1)
        return ops.click(button, count, kw.get("x"), kw.get("y"),
                         kw.get("monitor"), kw.get("shot"))
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
        return ops.launch(kw["app"])
    if a == "focus":
        return ops.focus(kw["window"])
    raise BatchError(f"Calistirilamayan eylem: {a}")


def run(
    actions: list[Action],
    ops: Ops,
    *,
    budget: float = 90.0,
    min_gap: float = 0.0,
    check_focus: bool = True,
    expect_focus: str = "",
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
    """
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

    if check_focus:
        try:
            focus_start = focus_now = ops.focused()
        except Exception as exc:  # odak okunamiyorsa takip kapanir, batch surer
            focus_start = focus_now = ""
            check_focus = False
            detail = f"odak okunamadi, takip kapatildi ({str(exc)[:80]})"

    i = 0
    for i, act in enumerate(actions):
        elapsed = clock() - started
        need = cost_ms(act, first_input=not seen_input) / 1000.0
        if elapsed + need > budget:
            stopped = "budget"
            detail = (
                f"sonraki eylem ({act.describe()}) icin ~{need:.1f} sn gerekiyor, "
                f"butcede {max(0.0, budget - elapsed):.1f} sn kaldi"
            )
            break

        t0 = clock()
        try:
            note = _dispatch(ops, act, sleep, auto_raw and act.a == "type")
            ok = True
        except Exception as exc:
            note = str(exc)
            ok = False
            caught_error = exc
        steps.append(Step(i, act.describe(), ok, note[:200], (clock() - t0) * 1000))

        if not ok:
            stopped = "error"
            detail = f"{i}. eylem ({act.describe()}) basarisiz: {note[:120]}"
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
            except Exception:
                pass
        elif check_focus and act.a in POINTER_ACTIONS:
            try:
                focus_now = ops.focused()
            except Exception:
                focus_now = focus_start
            if focus_now != focus_start:
                if want_focus and want_focus in focus_now.lower():
                    # Beklenen pencereye gecildi: niyet onceden beyan edilmisti.
                    # Yeni odak taban aliniyor ki bir SONRAKI kayma yine yakalansin.
                    focus_start = focus_now
                else:
                    stopped = "focus"
                    detail = (
                        f"tiklama sonrasi odak degisti: {focus_start!r} -> "
                        f"{focus_now!r}. Sonraki tuslar yanlis pencereye giderdi, "
                        "durduruldu"
                    )
                    if want_focus:
                        detail += f" (beklenen: {expect_focus!r})"
                    else:
                        detail += (
                            ". Bu tiklamayla pencere degistirmek ISTIYORDUYSANIZ "
                            "hedef pencerenin adini `expect_focus` ile onceden "
                            "bildirin"
                        )
                    i += 1
                    break

        if min_gap:
            sleep(min_gap)
    else:
        i = len(actions)

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
            detail += f" · basili kalanlar birakildi ({', '.join(held)})"
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
        f"**{result.total} eylemin {result.done} tanesi yapildi** "
        f"({result.elapsed:.1f} sn)"
    )
    lines = [head, ""]
    for s in result.steps:
        mark = "  " if s.ok else "✗ "
        lines.append(f"{mark}{s.index}. {s.action} — {s.note}")

    if result.stopped:
        lines.append("")
        reason = {
            "budget": "⏱️ Sure butcesi doldu",
            "error": "⛔ Eylem basarisiz",
            "focus": "⚠️ Odak kaydi",
        }.get(result.stopped, result.stopped)
        lines.append(f"{reason}: {result.detail}")

    if result.held:
        lines.append("")
        lines.append(
            f"⌨️ HALA BASILI: {', '.join(result.held)} — isiniz bitince "
            "`release` / `mouse_up` gonderin. Gonderilmezse sunucu bir sure "
            "sonra kendisi birakir, ama o zamana kadar kullanici makinesini "
            "kullanamaz."
        )

    if result.remaining:
        lines.append("")
        lines.append(f"Yapilmayan {len(result.remaining)} eylem:")
        for act in result.remaining:
            lines.append(f"  - {act.describe()}")
        if result.stopped == "budget":
            lines.append(
                "Kalanlari yeni bir cagriyla gonderebilirsiniz; once ekranin "
                "gercekten beklediginiz durumda oldugunu dogrulayin."
            )
        elif result.stopped == "focus":
            lines.append(
                "Once ui_dump ile nerede oldugunuza bakin. Koordinatla "
                "tiklamak yerine ui_click kullanmak bu sorunu tamamen ortadan "
                "kaldirir."
            )
    return "\n".join(lines)
