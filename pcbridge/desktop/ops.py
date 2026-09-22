"""`batch.Ops` protokolunun gercek cihazlara baglanan uygulamasi.

Motor (`batch.py`) bu modulu TANIMAZ; bagimlilik tek yonlu. Sayesinde toplu
eylem mantigi gercek tiklama gondermeden test edilebiliyor.

Burasi neden `tools.py`'de degil: ayni uygulama iki yerden kullaniliyor --
MCP araci `computer_batch` ve MCP sunucusundan BAGIMSIZ calisan `bin/pcb-do`
kabugu (yerel gorsel ajan onu Bash'ten cagiriyor). Kopya cikarilsaydi iki
davranis zamanla ayrisirdi ve ayrisma once GUI'de, yani en pahali yerde
gorunurdu.
"""

from __future__ import annotations

import time
from typing import Any

from . import apps as appslib
from .batch import INPUT_ACTIONS, POINTER_ACTIONS, Action, BudgetExceeded
from .contracts import CaptureProvider

# Fareyi tasidiktan sonra tiklamadan once verilen soluklanma. Kompozitorun
# imleci yeni yere tasimasi anlik degil; 0 verilirse tiklama ESKI konumda
# olusabiliyor.
MOVE_SETTLE = 0.08

# Klavye cihazi gerektiren eylemler. `type` ve `key` disinda `ui_*` eylemleri
# AT-SPI uzerinden gidiyor, uinput'a hic dokunmuyor.
KEYBOARD_ACTIONS = {"key", "type", "hold", "release"}

# Fare cihazi gerektirenler. POINTER_ACTIONS "odagi kaydirabilir" kumesi;
# burasi "cihaz lazim" kumesi ve ikisi ayni degil: `move`/`scroll`/`mouse_up`
# odagi kaydirmaz ama fare cihazi ister.
MOUSE_ACTIONS = POINTER_ACTIONS | {"move", "scroll", "mouse_up"}

# IKINCI, goreli cihazi gerektirenler (Adim 7). MOUSE_ACTIONS'a EKLENMIYOR:
# yalnizca `move_by` iceren bir liste mutlak cihazi hic actirmamali -- "sadece
# `ui_*` hicbir cihaz actirmaz" duzeltmesiyle ayni ilke.
RELATIVE_ACTIONS = {"move_by"}


class FocusUnreadable(RuntimeError):
    """Odak ne AT-SPI'dan ne kabuk eklentisinden okunabildi."""


def devices_needed(
    actions: list[Action],
    *,
    focus_uses_keyboard: bool = True,
) -> tuple[bool, bool, bool]:
    """(klavye gerekli mi, mutlak fare gerekli mi, goreli fare gerekli mi).

    `InputBackend.ensure()` bununla bir kez cagrilinca cihazlarin beklemesi
    tek sefere iniyor (iki cihaz icin olculdu: 2,61 s -> 1,41 s). Ucuncu,
    goreli cihaz da ayni tek beklemeye giriyor. Yalnizca `ui_*` iceren bir
    liste hicbir cihaz actirmaz -- C bolumunde duzeltilen "erisilebilirlik
    araci /dev/uinput istiyor" hatasi burada tekrarlanmasin.

    `focus` dort yoldan biriyle bitiyor (`apps.bring_to_front`): GNOME kabuk
    eklentisi (cihaz YOK), hedef zaten odakta (cihaz YOK), kapali uygulamayi
    `gtk-launch` ile acmak (cihaz YOK) ya da acik ama eklentinin one
    alamadigi pencere icin `super` + ad + `Return` aramasi (klavye VAR).
    Hangisinin secilecegi ancak eylem aninda bilinir. Eklentinin varligi bir
    D-Bus sorusu ve bu modul saf kaliyor, o yuzden cevap parametreyle geliyor.

    VARSAYILAN MUHAFAZAKAR. Cagiran sormadiysa klavye gerekli sayilir: eklenti
    kurulu OLSA BILE ad birden fazla pencereye uyuyorsa `ActivateWindow`
    False doner ve aramaya dusulebilir. Yanlis "gerekmiyor" cevabi cihazi
    eylemin ortasinda tembel actirir (~1,3 sn; aramanin kalan-sure kontrolu
    bunu hesaba katiyor, `apps.SEARCH_COST`).
    """
    kinds = {a.a for a in actions}
    keyboard = bool(kinds & KEYBOARD_ACTIONS)
    pointer = bool(kinds & MOUSE_ACTIONS)
    relative = bool(kinds & RELATIVE_ACTIONS)
    if focus_uses_keyboard and "focus" in kinds:
        keyboard = True
    return keyboard, pointer, relative


def _deadline(budget_left: float | None) -> float | None:
    """Motorun verdigi kalan sureyi bu surecin saatinde bir son tarihe cevir."""
    return None if budget_left is None else time.monotonic() + budget_left


class DeviceOps:
    """`batch.Ops`: eylemleri gercek klavye/fare/AT-SPI'ya cevirir."""

    def __init__(
        self,
        backend: Any,
        tree: Any,
        cfg: Any,
        capture_provider: CaptureProvider,
    ) -> None:
        self.backend = backend
        self.tree = tree
        self.cfg = cfg
        self.capture_provider = capture_provider
        # `shot=` kimliginin aranacagi dizinler. cfg'den BIR KEZ okunuyor;
        # her eylemde yeniden hesaplamak bir listeyi kirk kez kurmak olurdu.
        self.shot_dirs = list(cfg.shot_search_dirs)
        self.guard_age = (float(cfg.desktop.agent_shot_max_age_seconds)
                          if cfg.desktop.ambiguous_coord_guard else 0.0)

    def _global(self, x: int, y: int, monitor: int | None,
                shot: str | None) -> tuple[int, int]:
        """Koordinati global uzaya cevir. TEK GECIT: `capture.to_global`.

        Burada bir kopya tutulmuyor. `monitor=` tam cozunurluk ofseti ekler,
        `shot=` ayrica OLCEGI de uygular; ikisini de bilen tek yer orasi.
        """
        return self.capture_provider.to_global(
            x, y, monitor=monitor, shot=shot, dirs=self.shot_dirs,
            guard_age=self.guard_age,
        )

    # -------------------------------------------------------------- klavye
    def key(self, keys: str) -> str:
        self.backend.key(keys)
        return f"`{keys}` pressed"

    def type(self, text: str, raw: bool) -> str:
        return self.backend.type_text(
            text, raw=raw, restore_clipboard=self.cfg.desktop.restore_clipboard
        )

    def hold(self, keys: str) -> str:
        self.backend.key_down(keys)
        return f"`{keys}` HELD DOWN"

    def release(self, keys: str) -> str:
        self.backend.key_up(keys)
        return f"`{keys}` released"

    # ---------------------------------------------------------------- fare
    def move(self, x: int, y: int, monitor: int | None,
             shot: str | None = None) -> str:
        gx, gy = self._global(x, y, monitor, shot)
        return f"pointer moved to {self.backend.move(gx, gy)}"

    def move_by(self, dx: int, dy: int) -> str:
        """Goreli kaydirma. `_global()` YOK -- delta bir koordinat degil, hicbir
        uzaya ait degil; cevrilirse sessizce anlamsiz bir sayi olur."""
        sx, sy = self.backend.move_by(dx, dy)
        return (
            f"pointer nudged by ({sx:+d}, {sy:+d}) · its position is now "
            "UNKNOWN — take a ui_dump or screen_capture before clicking, "
            "or use an absolute `move` to a known point"
        )

    def click(self, button: str, count: int, x: int | None, y: int | None,
              monitor: int | None, shot: str | None = None,
              hold_ms: int | None = None) -> str:
        where = self._goto(x, y, monitor, shot)
        if hold_ms is None:
            self.backend.click(button, count)
        else:
            self.backend.click(button, count, hold_ms=hold_ms)
        kind = {2: " (double)", 3: " (triple)"}.get(count, "")
        # Koordinatsiz tiklama imlecin bulundugu yere gider (Adim 8.2); rapor
        # bunu soylesin, yoksa "left tiklama" nereye gittigini gizler.
        place = where or " (where the pointer is)"
        press = f" · held {hold_ms} ms" if hold_ms is not None else ""
        return f"{button} click{place}{kind}{press}"

    def mouse_down(self, button: str, x: int | None, y: int | None,
                   monitor: int | None, shot: str | None = None) -> str:
        where = self._goto(x, y, monitor, shot)
        self.backend.mouse_down(button)
        return f"{button} button{where} HELD DOWN"

    def mouse_up(self, button: str) -> str:
        self.backend.mouse_up(button)
        return f"{button} button released"

    def drag(self, x: int, y: int, to_x: int, to_y: int, button: str,
             monitor: int | None, shot: str | None = None) -> str:
        gx, gy = self._global(x, y, monitor, shot)
        ex, ey = self._global(to_x, to_y, monitor, shot)
        self.backend.drag(gx, gy, ex, ey, button=button)
        return f"dragged ({gx}, {gy}) -> ({ex}, {ey}) with {button}"

    def scroll(self, amount: int, x: int | None, y: int | None,
               monitor: int | None, horizontal: bool = False,
               shot: str | None = None) -> str:
        self._goto(x, y, monitor, shot)
        self.backend.scroll(amount, horizontal=horizontal)
        return f"scrolled {amount} step(s) {'horizontally' if horizontal else 'vertically'}"

    def _goto(self, x: int | None, y: int | None, monitor: int | None,
              shot: str | None = None) -> str:
        """Koordinat verilmisse oraya git ve yerlesmesini bekle.

        Imlec artik ara noktalardan gectigi icin `move` kendi suresini
        harciyor; MOVE_SETTLE onun USTUNE binen kompozitor payi.
        """
        if x is None or y is None:
            return ""
        gx, gy = self._global(x, y, monitor, shot)
        self.backend.move(gx, gy)
        time.sleep(MOVE_SETTLE)
        return f" ({gx}, {gy})"

    # ------------------------------------------------------- basili tutma
    def held(self) -> list[str]:
        return self.backend.held()

    def release_all(self) -> list[str]:
        return self.backend.release_all()

    # ------------------------------------------------- erisilebilirlik agaci
    def ui_click(self, node_id: str) -> str:
        res = self.tree.click(node_id)
        return f"{res.get('role', '?')} \"{res.get('name', '')}\" clicked"

    def ui_set_text(self, node_id: str, text: str) -> str:
        res = self.tree.set_text(node_id, text)
        return (
            f"{len(text)} characters written into the {res.get('role', '?')} "
            f"(replaced: {res.get('replaced_chars', 0)})"
        )

    # ------------------------------------------------------------ uygulama
    # Ikisi de `window_focus` ile ayni `apps` islemlerinden geciyor (Task 6.4):
    # baslatma penceresini gorerek dogrulanir, `focus` ayni sirayi izler.
    def launch(self, app: str, budget_left: float | None = None) -> str:
        try:
            return appslib.launch_application(
                appslib.resolve_application(app),
                self.tree.focused_window,
                self.tree.windows,
                deadline=_deadline(budget_left),
            ).note
        except appslib.NoTimeLeft as exc:
            raise BudgetExceeded(str(exc)) from exc

    def focus(self, window: str, budget_left: float | None = None) -> str:
        try:
            return appslib.bring_to_front(
                window,
                self.backend,
                self.tree.focused_window,
                self.tree.windows,
                deadline=_deadline(budget_left),
            ).note
        except appslib.NoTimeLeft as exc:
            raise BudgetExceeded(str(exc)) from exc

    def focused(self) -> str:
        """Odaktaki pencerenin kimligi: once AT-SPI, olmazsa kabuk eklentisi.

        Ikinci kaynak (Adim 8.1) KURALI GEVSETMIYOR: okunamayan odak hala
        "degismedi" sayilmiyor. Yalnizca AT-SPI'in goremedigi bir pencerede
        (oyun, bazi Java/Electron pencereleri) odak artik kompozitorden
        okunabiliyor. Ikisi de okuyamazsa istisna aynen yukari cikar.
        """
        try:
            app, win = self.tree.focused_window()
        except Exception as exc:
            shell = appslib.extension_focused_window()
            if shell is None:
                raise FocusUnreadable(
                    f"{exc} · the shell extension could not tell the focus either (not "
                    "installed, an older version loaded, or no window has the focus)"
                ) from exc
            app, win = shell
        return f"{app} | {win}"


__all__ = [
    "DeviceOps",
    "FocusUnreadable",
    "devices_needed",
    "INPUT_ACTIONS",
    "MOVE_SETTLE",
]
