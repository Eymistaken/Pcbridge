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
from . import monitors as monitorslib
from .batch import INPUT_ACTIONS, POINTER_ACTIONS, Action

# Fareyi tasidiktan sonra tiklamadan once verilen soluklanma. Kompozitorun
# imleci yeni yere tasimasi anlik degil; 0 verilirse tiklama ESKI konumda
# olusabiliyor.
MOVE_SETTLE = 0.08

# Klavye cihazi gerektiren eylemler. `type` ve `key` disinda `ui_*` eylemleri
# AT-SPI uzerinden gidiyor, uinput'a hic dokunmuyor.
KEYBOARD_ACTIONS = {"key", "type"}


def devices_needed(actions: list[Action]) -> tuple[bool, bool]:
    """(klavye gerekli mi, fare gerekli mi).

    `InputBackend.ensure()` bununla bir kez cagrilinca iki cihazin beklemesi
    tek sefere iniyor (2,61 s -> 1,41 s, olculdu). Yalnizca `ui_*` iceren bir
    liste hicbir cihaz actirmaz -- C bolumunde duzeltilen "erisilebilirlik
    araci /dev/uinput istiyor" hatasi burada tekrarlanmasin.
    """
    kinds = {a.a for a in actions}
    keyboard = bool(kinds & KEYBOARD_ACTIONS)
    pointer = bool(kinds & POINTER_ACTIONS) or "move" in kinds or "scroll" in kinds
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

    # -------------------------------------------------------------- klavye
    def key(self, keys: str) -> str:
        self.backend.key(keys)
        return f"`{keys}` basildi"

    def type(self, text: str, raw: bool) -> str:
        return self.backend.type_text(
            text, raw=raw, restore_clipboard=self.cfg.desktop.restore_clipboard
        )

    # ---------------------------------------------------------------- fare
    def move(self, x: int, y: int, monitor: int | None) -> str:
        gx, gy = monitorslib.to_global(x, y, monitor)
        return f"imlec {self.backend.move(gx, gy)} konumuna tasindi"

    def click(self, button: str, count: int, x: int | None, y: int | None,
              monitor: int | None) -> str:
        where = ""
        if x is not None and y is not None:
            gx, gy = monitorslib.to_global(x, y, monitor)
            self.backend.move(gx, gy)
            time.sleep(MOVE_SETTLE)
            where = f" ({gx}, {gy})"
        self.backend.click(button, count)
        return f"{button} tiklama{where}" + (" (cift)" if count > 1 else "")

    def drag(self, x: int, y: int, to_x: int, to_y: int,
             monitor: int | None) -> str:
        gx, gy = monitorslib.to_global(x, y, monitor)
        ex, ey = monitorslib.to_global(to_x, to_y, monitor)
        self.backend.drag(gx, gy, ex, ey)
        return f"({gx}, {gy}) -> ({ex}, {ey}) suruklendi"

    def scroll(self, amount: int, x: int | None, y: int | None,
               monitor: int | None) -> str:
        if x is not None and y is not None:
            self.backend.move(*monitorslib.to_global(x, y, monitor))
            time.sleep(MOVE_SETTLE)
        self.backend.scroll(amount)
        return f"{amount} tik kaydirildi"

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
