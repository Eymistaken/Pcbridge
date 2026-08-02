"""Erisilebilirlik agaci — ekranin METINSEL ikizi.

NEDEN
    Spark'a giden MCP function-response kanali yalnizca metin tasiyor, yani
    model `screen_capture`'in urettigi goruntuyu goremiyor. Ama GTK/GNOME
    uygulamalari arayuzlerini D-Bus uzerinden zaten bir agac olarak
    yayinliyor: her dugumun rolu, etiketi, durumu var. Burada model yok,
    prompt yok, tahmin yok -- uygulamanin kendi beyani.

AYRI SUREC
    `gi` venv'de yok ve AT-SPI cevap vermeyen bir uygulamada bloklayabiliyor.
    Bu yuzden butun okuma/eylem `atspi_helper.py`'ye devrediliyor: sistem
    python3'u, JSON protokolu, sert zaman asimi. Ayrintili gerekce yardimcinin
    kendi dosyasinda.

KOORDINAT KULLANILMIYOR
    OLCULDU (2026-08-02): `get_extents(SCREEN)` guvenilmez. "Desktop Icons 1"
    ve "Desktop Icons 2" ikisi de `@(0,0) 1920x1080` bildiriyor, oysa tanimi
    geregi ayri monitorlerde. Bu yuzden tiklama `Action.do_action` ile yapilir;
    `Action` yoksa koordinata DUSULMEZ, acikca hata donulur. Sessizce yanlis
    yere tiklamak, "yapamadim" demekten cok daha kotu.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

HELPER = Path(__file__).resolve().parent / "atspi_helper.py"
# Sistem python3 -- venv'inki DEGIL. Ayrimin sebebi modul docstring'inde.
SYSTEM_PYTHON = "python3"
DUMP_TIMEOUT = 20
ACT_TIMEOUT = 25


class UiTreeError(RuntimeError):
    """Erisilebilirlik agaci okunamadi ya da eylem uygulanamadi."""


@dataclass(frozen=True)
class Node:
    node_id: str
    path: list[int]
    role: str
    name: str
    states: list[str]
    actions: list[str]
    editable: bool
    depth: int

    @property
    def clickable(self) -> bool:
        return bool(self.actions)

    def describe(self) -> str:
        bits = [f"#{self.node_id}", self.role]
        if self.name:
            bits.append(f'"{self.name}"')
        flags = [s for s in self.states if s not in ("sensitive", "enabled")]
        if self.editable and "editable" not in flags:
            flags.append("editable")
        if not self.actions and not self.editable:
            flags.append("eylemsiz")
        if flags:
            bits.append("[" + ", ".join(flags) + "]")
        return " ".join(bits)


@dataclass
class Dump:
    app: str
    window: str
    nodes: list[Node]
    truncated: bool = False
    by_id: dict[str, Node] = field(default_factory=dict)


def available() -> tuple[bool, str]:
    """(kullanilabilir mi, degilse Turkce gerekce)."""
    if not HELPER.exists():
        return False, f"AT-SPI yardimcisi bulunamadi: {HELPER}"
    if not shutil.which(SYSTEM_PYTHON):
        return False, f"`{SYSTEM_PYTHON}` bulunamadi (sistem python'u gerekli)."
    try:
        proc = subprocess.run(
            [SYSTEM_PYTHON, "-c", "import gi; gi.require_version('Atspi','2.0')"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "AT-SPI kontrolu zaman asimina ugradi."
    if proc.returncode != 0:
        return False, (
            "AT-SPI baglantilari yok. Kurulum: "
            "sudo apt install python3-gi gir1.2-atspi-2.0"
        )
    return True, ""


def _call(payload: dict, timeout: int) -> dict:
    """Yardimciyi calistir ve JSON cevabini dondur."""
    try:
        proc = subprocess.run(
            [SYSTEM_PYTHON, str(HELPER)],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # Zaman asimi SESSIZCE yutulmaz: AT-SPI cevap vermeyen bir uygulamada
        # bloklayabiliyor ve kullanicinin bunu bilmesi lazim.
        raise UiTreeError(
            f"Uygulama {timeout} saniyede cevap vermedi. Donmus olabilir; "
            "ekran goruntusuyle bakin (screen_capture)."
        ) from exc
    if not proc.stdout.strip():
        # Yardimci stdout'a her zaman JSON yazar. Bos ise gercekten cokmustur;
        # stderr'deki GLib gurultusunun son satiri en faydali ipucu.
        tail = (proc.stderr or "").strip().splitlines()
        hint = tail[-1] if tail else "cikti yok"
        raise UiTreeError(f"AT-SPI yardimcisi cevap vermedi ({hint}).")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise UiTreeError(f"AT-SPI yardimcisinin cevabi okunamadi: {exc}") from exc


def _make_id(role: str, name: str, occurrence: int) -> str:
    """Kararli kisa kimlik.

    YOLU KASITLI OLARAK ICERMEZ. Yol karisima girseydi arayuzde baska bir yere
    bir dugum eklenmesi -- ki her sekme acilisinda oluyor -- ayni dugmenin
    kimligini degistirirdi ve modelin elindeki liste sessizce eskirdi.
    Ayirt edici olarak rol + etiket + KACINCI kez gectigi kullaniliyor:
    ayni isimli iki "Kapat" dugmesi ayrisir, alakasiz bir ekleme ise etkilemez.
    """
    key = f"{role}\x00{name}\x00{occurrence}".encode()
    return hashlib.sha256(key).hexdigest()[:4]


def _to_nodes(raw: list[dict]) -> list[Node]:
    seen: dict[tuple[str, str], int] = {}
    out: list[Node] = []
    for item in raw:
        role = item.get("role") or "?"
        name = item.get("name") or ""
        k = (role, name)
        occ = seen.get(k, 0)
        seen[k] = occ + 1
        out.append(
            Node(
                node_id=_make_id(role, name, occ),
                path=list(item.get("path") or []),
                role=role,
                name=name,
                states=list(item.get("states") or []),
                actions=list(item.get("actions") or []),
                editable=bool(item.get("editable")),
                depth=int(item.get("depth") or 0),
            )
        )
    return out


class UiTree:
    """Agac okuma + eylem. Son dokumu, kimliklerden dugume donebilmek icin tutar."""

    def __init__(self) -> None:
        self._last: Dump | None = None

    # ------------------------------------------------------------------ okuma
    def dump(
        self,
        target: str = "focused",
        interactive_only: bool = True,
        max_nodes: int = 400,
    ) -> Dump:
        resp = _call(
            {
                "cmd": "dump",
                "target": target,
                "interactive_only": interactive_only,
                "max_nodes": max_nodes,
            },
            DUMP_TIMEOUT,
        )
        if not resp.get("ok"):
            raise UiTreeError(resp.get("error") or "Agac okunamadi.")
        nodes = _to_nodes(resp.get("nodes") or [])
        dump = Dump(
            app=resp.get("app") or "?",
            window=resp.get("window") or "",
            nodes=nodes,
            truncated=bool(resp.get("truncated")),
            by_id={n.node_id: n for n in nodes},
        )
        self._last = dump
        return dump

    def focused_window(self) -> tuple[str, str]:
        """(uygulama, pencere basligi) — ONBELLEGI BOZMADAN.

        `screen_info` bunu cagiriyor; normal `dump()` kullansaydi son dokumu
        ezer ve modelin elindeki kimlikler gecersizlesirdi. `max_nodes=1` ile
        yardimci agaci erken kesiyor, yani ucuz.
        """
        resp = _call(
            {"cmd": "dump", "target": "focused", "interactive_only": True,
             "max_nodes": 1},
            DUMP_TIMEOUT,
        )
        if not resp.get("ok"):
            raise UiTreeError(resp.get("error") or "Odaktaki pencere okunamadi.")
        return resp.get("app") or "?", resp.get("window") or ""

    def resolve(self, node_id: str) -> Node:
        key = str(node_id).strip().lstrip("#")
        node = (self._last.by_id if self._last else {}).get(key)
        if node is None:
            if self._last is None:
                raise UiTreeError(
                    "Henuz ui_dump cagrilmadi; kimlikler o listeden geliyor."
                )
            raise UiTreeError(
                f"#{key} taninmiyor. Once ui_dump ile guncel listeyi alin "
                "(kimlikler o listeye ait)."
            )
        return node

    # ----------------------------------------------------------------- eylem
    def _target_payload(self, node: Node) -> dict:
        return {
            "app": self._last.app if self._last else "",
            "path": node.path,
            "role": node.role,
            "name": node.name,
        }

    def click(self, node_id: str, action: str = "click") -> dict:
        node = self.resolve(node_id)
        if not node.actions:
            # Koordinata DUSMUYORUZ: olculen AT-SPI koordinatlari yanlis.
            raise UiTreeError(
                f"{node.role} \"{node.name}\" bir eylem sunmuyor. Koordinatla "
                "tiklamayi denemiyorum -- AT-SPI'in bildirdigi konumlar bu "
                "sistemde yanlis. screen_capture ile bakip `mouse` kullanin."
            )
        resp = _call(
            {"cmd": "act", "action": action, **self._target_payload(node)}, ACT_TIMEOUT
        )
        if not resp.get("ok"):
            raise UiTreeError(resp.get("error") or "Eylem uygulanamadi.")
        return resp

    def set_text(self, node_id: str, text: str) -> dict:
        node = self.resolve(node_id)
        resp = _call(
            {"cmd": "settext", "text": text, **self._target_payload(node)}, ACT_TIMEOUT
        )
        if not resp.get("ok"):
            raise UiTreeError(resp.get("error") or "Metin yazilamadi.")
        return resp


# ------------------------------------------------------------------ bicimleme
def describe(dump: Dump) -> str:
    """Dokumu modele gosterilecek duz metne cevir."""
    head = f"**{dump.app}** — {dump.window}" if dump.window else f"**{dump.app}**"
    lines = [head, ""]
    if not dump.nodes:
        lines.append(
            "Bu pencere erisilebilirlik agaci yayinlamiyor (bos geldi). Bazi "
            "Electron uygulamalari `--force-renderer-accessibility` olmadan "
            "icerigini vermiyor. Ekrani gormek icin screen_capture kullanin."
        )
        return "\n".join(lines)

    for n in dump.nodes:
        lines.append("  " + n.describe())
    lines.append("")
    clickable = sum(1 for n in dump.nodes if n.clickable)
    editable = sum(1 for n in dump.nodes if n.editable)
    lines.append(
        f"{len(dump.nodes)} dugum · {clickable} tiklanabilir · {editable} yazilabilir"
    )
    if dump.truncated:
        lines.append(
            "⚠️ Liste kirpildi. Daraltmak icin target ile tek bir uygulama verin."
        )
    lines.append(
        "Tiklamak icin ui_click(\"#kimlik\"), metin kutusuna yazmak icin "
        "ui_set_text(\"#kimlik\", \"...\")."
    )
    return "\n".join(lines)
