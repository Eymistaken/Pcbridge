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
from . import capture as capturelib
from .batch import INPUT_ACTIONS, POINTER_ACTIONS, Action

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


def devices_needed(actions: list[Action]) -> tuple[bool, bool]:
    """(klavye gerekli mi, fare gerekli mi).

    `InputBackend.ensure()` bununla bir kez cagrilinca iki cihazin beklemesi
    tek sefere iniyor (2,61 s -> 1,41 s, olculdu). Yalnizca `ui_*` iceren bir
    liste hicbir cihaz actirmaz -- C bolumunde duzeltilen "erisilebilirlik
    araci /dev/uinput istiyor" hatasi burada tekrarlanmasin.
    """
    kinds = {a.a for a in actions}
    keyboard = bool(kinds & KEYBOARD_ACTIONS)
    pointer = bool(kinds & MOUSE_ACTIONS)
    if "focus" in kinds:
        # `focus` GNOME aramasini kullaniyor: super + ad + Return -> klavye.
        keyboard = True
    return keyboard, pointer


class DeviceOps:
    """`batch.Ops`: eylemleri gercek klavye/fare/AT-SPI'ya cevirir."""

    def __init__(self, backend: Any, tree: Any, cfg: Any) -> None:
        self.backend = backend
        self.tree = tree
        self.cfg = cfg
        # `shot=` kimliginin aranacagi dizinler. cfg'den BIR KEZ okunuyor;
        # her eylemde yeniden hesaplamak bir listeyi kirk kez kurmak olurdu.
        self.shot_dirs = list(cfg.shot_search_dirs)

    def _global(self, x: int, y: int, monitor: int | None,
                shot: str | None) -> tuple[int, int]:
        """Koordinati global uzaya cevir. TEK GECIT: `capture.to_global`.

        Burada bir kopya tutulmuyor. `monitor=` tam cozunurluk ofseti ekler,
        `shot=` ayrica OLCEGI de uygular; ikisini de bilen tek yer orasi.
        """
        return capturelib.to_global(
            x, y, monitor=monitor, shot=shot, dirs=self.shot_dirs
        )

    # -------------------------------------------------------------- klavye
    def key(self, keys: str) -> str:
        self.backend.key(keys)
        return f"`{keys}` basildi"

    def type(self, text: str, raw: bool) -> str:
        return self.backend.type_text(
            text, raw=raw, restore_clipboard=self.cfg.desktop.restore_clipboard
        )

    def hold(self, keys: str) -> str:
        self.backend.key_down(keys)
        return f"`{keys}` BASILI TUTULUYOR"

    def release(self, keys: str) -> str:
        self.backend.key_up(keys)
        return f"`{keys}` birakildi"

    # ---------------------------------------------------------------- fare
    def move(self, x: int, y: int, monitor: int | None,
             shot: str | None = None) -> str:
        gx, gy = self._global(x, y, monitor, shot)
        return f"imlec {self.backend.move(gx, gy)} konumuna tasindi"

    def click(self, button: str, count: int, x: int | None, y: int | None,
              monitor: int | None, shot: str | None = None) -> str:
        where = self._goto(x, y, monitor, shot)
        self.backend.click(button, count)
        kind = {2: " (cift)", 3: " (uclu)"}.get(count, "")
        return f"{button} tiklama{where}{kind}"

    def mouse_down(self, button: str, x: int | None, y: int | None,
                   monitor: int | None, shot: str | None = None) -> str:
        where = self._goto(x, y, monitor, shot)
        self.backend.mouse_down(button)
        return f"{button} dugmesi{where} BASILI TUTULUYOR"

    def mouse_up(self, button: str) -> str:
        self.backend.mouse_up(button)
        return f"{button} dugmesi birakildi"

    def drag(self, x: int, y: int, to_x: int, to_y: int, button: str,
             monitor: int | None, shot: str | None = None) -> str:
        gx, gy = self._global(x, y, monitor, shot)
        ex, ey = self._global(to_x, to_y, monitor, shot)
        self.backend.drag(gx, gy, ex, ey, button=button)
        return f"({gx}, {gy}) -> ({ex}, {ey}) {button} ile suruklendi"

    def scroll(self, amount: int, x: int | None, y: int | None,
               monitor: int | None, horizontal: bool = False,
               shot: str | None = None) -> str:
        self._goto(x, y, monitor, shot)
        self.backend.scroll(amount, horizontal=horizontal)
        return f"{amount} tik {'yatay' if horizontal else 'dikey'} kaydirildi"

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
        return f"{res.get('role', '?')} \"{res.get('name', '')}\" tiklandi"

    def ui_set_text(self, node_id: str, text: str) -> str:
        res = self.tree.set_text(node_id, text)
        return (
            f"{res.get('role', '?')} icine {len(text)} karakter yazildi "
            f"(silinen: {res.get('replaced_chars', 0)})"
        )

    # ------------------------------------------------------------ uygulama
    def launch(self, app: str) -> str:
        return appslib.launch(app)

    def focus(self, window: str) -> str:
        return appslib.focus(window, self.backend, self.tree.focused_window)

    def focused(self) -> str:
        app, win = self.tree.focused_window()
        return f"{app} | {win}"


__all__ = ["DeviceOps", "devices_needed", "INPUT_ACTIONS", "MOVE_SETTLE"]
