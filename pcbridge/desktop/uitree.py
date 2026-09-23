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

IKI KIMLIK
    Modelin gordugu `#1b72` kisa ve dokumler arasinda KARARLI (rol + etiket +
    kacinci kez gectigi). Eylemin gittigi yer ise dugumun kendisi: uygulamanin
    veriyolu adi + dugumun D-Bus nesne yolu (`Node.ref`). Kisa kimlik yalnizca
    son dokumde bir dugum SECER; dogrulamayi yardimci bu nesne kimligiyle
    yapar. Ayrinti `atspi_helper.py`de.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .. import distro as distrolib
from .. import sessionctx
from .errors import ErrorCode

HELPER = Path(__file__).resolve().parent / "atspi_helper.py"
# Sistem python3 -- venv'inki DEGIL. Ayrimin sebebi modul docstring'inde.
SYSTEM_PYTHON = "python3"
DUMP_TIMEOUT = 20
ACT_TIMEOUT = 25
# Kisa kimligin en az uzunlugu. Ayni dokumde iki dugumun ozeti bu kadar
# karakterde cakisirsa ikisi de ayrisana kadar uzatilir (`_short_ids`).
MIN_ID = 4
BACKEND = "linux.atspi"


class UiTreeError(RuntimeError):
    """Erisilebilirlik agaci okunamadi ya da eylem uygulanamadi.

    `code` biliniyorsa kararli sebep (`ELEMENT_STALE` gibi); saglayici bunu
    ortak hata sozlesmesine tasir. Mesajdan kod TURETILMEZ.
    """

    def __init__(self, message: str, code: ErrorCode | None = None) -> None:
        super().__init__(message)
        self.code = code


def _code(raw: object) -> ErrorCode | None:
    try:
        return ErrorCode(str(raw)) if raw else None
    except ValueError:
        return None


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
    # D-Bus nesne yolu: eylemin dogrulandigi kimlik. Modele gosterilmez.
    ref: str = ""
    # Kisa kimligin geldigi tam ozet; uzatilmis kimlikleri cozmek icin.
    digest: str = ""

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
    # Hedef kimligi: eylem bu dokumun uygulamasinda ve (odak dokumunde) ayni
    # pencerede dogrulanir. `snapshot` dokumun kendisi; denetim kaydi bir
    # eylemi hangi dokume dayandigiyla birlikte yazabilsin diye.
    backend: str = BACKEND
    snapshot: str = ""
    app_bus: str = ""
    app_pid: int = 0
    scope: str = "window"
    window_ref: str = ""
    same_name: int = 1


@dataclass(frozen=True)
class Window:
    app: str
    title: str
    role: str
    active: bool
    children: int
    app_bus: str = ""
    app_pid: int = 0
    ref: str = ""

    @property
    def label(self) -> str:
        return f"{self.app} — {self.title}" if self.title else self.app


def available() -> tuple[bool, str]:
    """(kullanilabilir mi, degilse Turkce gerekce)."""
    if not HELPER.exists():
        return False, f"AT-SPI helper not found: {HELPER}"
    if not shutil.which(SYSTEM_PYTHON):
        return False, f"`{SYSTEM_PYTHON}` not found (the system python is required)."
    try:
        proc = subprocess.run(
            [SYSTEM_PYTHON, "-c", "import gi; gi.require_version('Atspi','2.0')"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "The AT-SPI check timed out."
    if proc.returncode != 0:
        return False, (
            "AT-SPI bindings are missing. Install them: "
            + distrolib.install_command("atspi")
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
        # bloklayabiliyor ve kullanicinin bunu bilmesi lazim. Bir eylemde
        # eylem gitmis de olabilir, gitmemis de: TEKRARLANMAZ. Okuma ise
        # yalnizca zaman asimi.
        acting = payload.get("cmd") in ("act", "settext")
        raise UiTreeError(
            f"The application did not answer within {timeout} seconds. It may be hung; "
            "look with a screenshot (screen_capture).",
            ErrorCode.EXECUTION_UNKNOWN if acting else ErrorCode.TIMEOUT,
        ) from exc
    if not proc.stdout.strip():
        # Yardimci stdout'a her zaman JSON yazar. Bos ise gercekten cokmustur;
        # stderr'deki GLib gurultusunun son satiri en faydali ipucu.
        tail = (proc.stderr or "").strip().splitlines()
        hint = tail[-1] if tail else "no output"
        raise UiTreeError(f"The AT-SPI helper did not answer ({hint}).")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise UiTreeError(f"the AT-SPI helper's answer could not be read: {exc}") from exc


def _digest(role: str, name: str, occurrence: int) -> str:
    """Kararli kimligin tam ozeti.

    YOLU KASITLI OLARAK ICERMEZ. Yol karisima girseydi arayuzde baska bir yere
    bir dugum eklenmesi -- ki her sekme acilisinda oluyor -- ayni dugmenin
    kimligini degistirirdi ve modelin elindeki liste sessizce eskirdi.
    Ayirt edici olarak rol + etiket + KACINCI kez gectigi kullaniliyor:
    ayni isimli iki "Kapat" dugmesi ayrisir, alakasiz bir ekleme ise etkilemez.
    """
    key = f"{role}\x00{name}\x00{occurrence}".encode()
    return hashlib.sha256(key).hexdigest()


def _make_id(role: str, name: str, occurrence: int) -> str:
    return _digest(role, name, occurrence)[:MIN_ID]


def _common(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _short_ids(digests: list[str]) -> list[str]:
    """Her ozetin, dokumdeki digerlerinden ayrildigi en kisa oneki (en az 4).

    Dort onaltilik karakter 65536 deger: 400 dugumluk bir dokumde en az bir
    cakisma olasiligi ~%70. Eskiden `by_id` cakisani sessizce eziyordu ve
    ilk dugumun kimligi ikinciye tiklatiyordu. Siralanmis listede bir ozetin
    en uzun ortak oneki komsularindan biriyledir; kimlik ondan bir fazlasi.
    """
    need = [MIN_ID] * len(digests)
    order = sorted(range(len(digests)), key=digests.__getitem__)
    for a, b in zip(order, order[1:]):
        common = _common(digests[a], digests[b])
        if common >= len(digests[a]):
            raise UiTreeError(
                "Two nodes got the same id; the list cannot be trusted.",
                ErrorCode.ELEMENT_AMBIGUOUS,
            )
        need[a] = max(need[a], common + 1)
        need[b] = max(need[b], common + 1)
    return [d[:n] for d, n in zip(digests, need)]


def _to_nodes(raw: list[dict]) -> list[Node]:
    seen: dict[tuple[str, str], int] = {}
    items: list[tuple[dict, str, str, str]] = []
    for item in raw:
        role = item.get("role") or "?"
        name = item.get("name") or ""
        k = (role, name)
        occ = seen.get(k, 0)
        seen[k] = occ + 1
        items.append((item, role, name, _digest(role, name, occ)))
    ids = _short_ids([digest for *_rest, digest in items])
    return [
        Node(
            node_id=node_id,
            path=list(item.get("path") or []),
            role=role,
            name=name,
            states=list(item.get("states") or []),
            actions=list(item.get("actions") or []),
            editable=bool(item.get("editable")),
            depth=int(item.get("depth") or 0),
            ref=str(item.get("ref") or ""),
            digest=digest,
        )
        for (item, role, name, digest), node_id in zip(items, ids)
    ]


def dump_from_response(resp: dict, backend: str = BACKEND) -> Dump:
    """Bir okuyucunun `dump` cevabini `Dump`a cevir.

    Python yardimcisi da native yardimci da ayni bicimde cevap veriyor; kisa
    kimlikler HER IKISINDE burada, tek yerde uretiliyor. Snapshot native
    yardimcininki: eylem o dokumu bu kimlikle anar ve yardimci yalnizca
    kendi listeledigi dugume dokunur (Task 6.3). Python yardimcisi dokum
    saklamadigi icin kimligi burada uretilir.
    """
    nodes = _to_nodes(resp.get("nodes") or [])
    return Dump(
        app=resp.get("app") or "?",
        window=resp.get("window") or "",
        nodes=nodes,
        truncated=bool(resp.get("truncated")),
        by_id={n.node_id: n for n in nodes},
        backend=backend,
        snapshot=str(resp.get("snapshot") or "") or secrets.token_hex(6),
        app_bus=str(resp.get("app_bus") or ""),
        app_pid=int(resp.get("app_pid") or 0),
        scope="app" if resp.get("scope") == "app" else "window",
        window_ref=str(resp.get("window_ref") or ""),
        same_name=int(resp.get("same_name") or 1),
    )


def windows_from_response(resp: dict) -> list[Window]:
    """Pencere listesi cevabini `Window` listesine cevir.

    Penceresi olmayan arka plan servisleri (gsd-*, ibus-*) ve isimsiz
    yardimci pencereler elenir: model icin gurultu, kullanici icin anlamsiz.
    """
    out = []
    for item in resp.get("windows") or []:
        title = (item.get("window") or "").strip()
        app = (item.get("app") or "").strip()
        if not title and not item.get("active"):
            continue
        out.append(Window(
            app=app or "?",
            title=title,
            role=item.get("role") or "",
            active=bool(item.get("active")),
            children=int(item.get("children") or 0),
            app_bus=str(item.get("app_bus") or ""),
            app_pid=int(item.get("app_pid") or 0),
            ref=str(item.get("ref") or ""),
        ))
    return out


class UiTree:
    """Agac okuma + eylem. Son dokumu, kimliklerden dugume donebilmek icin tutar."""

    def __init__(self) -> None:
        # The last dump PER MCP SESSION: a short id like `#1b72` names a node in
        # the dump the calling client made. One process serves many clients
        # since 2.0; a shared "last dump" would resolve client A's id against
        # client B's window.
        self._dumps: sessionctx.PerSession[Dump] = sessionctx.PerSession()

    @property
    def _last(self) -> Dump | None:
        return self._dumps.get()

    @_last.setter
    def _last(self, dump: Dump | None) -> None:
        self._dumps.set(dump)

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
            raise UiTreeError(resp.get("error") or "The tree could not be read.", _code(resp.get("code")))
        dump = dump_from_response(resp)
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
            raise UiTreeError(
                resp.get("error") or "The focused window could not be read.", _code(resp.get("code"))
            )
        return resp.get("app") or "?", resp.get("window") or ""

    def windows(self) -> list[Window]:
        """Acik pencereler. Onbellegi bozmaz, agaci gezmez (olculdu: 42 ms).

        Penceresi olmayan arka plan servisleri (gsd-*, ibus-*) ve isimsiz
        yardimci pencereler elenir: model icin gurultu, kullanici icin anlamsiz.
        """
        resp = _call({"cmd": "windows"}, DUMP_TIMEOUT)
        if not resp.get("ok"):
            raise UiTreeError(
                resp.get("error") or "The window list could not be read.", _code(resp.get("code"))
            )
        return windows_from_response(resp)

    def resolve(self, node_id: str) -> Node:
        """Kisa kimligi SON dokumdeki dugume cevir.

        Kimlik uzatilmis olabilir (cakisma) ya da onceki bir dokumdeki uzun
        hali gelmis olabilir; ikisi de tam ozetin oneki olarak cozulur. Birden
        fazla dugume uyan onek hicbirini secmez.
        """
        key = str(node_id).strip().lstrip("#").lower()
        if self._last is None:
            raise UiTreeError(
                "ui_dump has not been called yet; the ids come from its list.",
                ErrorCode.ELEMENT_STALE,
            )
        node = self._last.by_id.get(key)
        if node is not None:
            return node
        if len(key) >= MIN_ID:
            hits = [n for n in self._last.nodes if n.digest.startswith(key)]
            if len(hits) == 1:
                return hits[0]
            if hits:
                shown = ", ".join("#" + n.node_id for n in hits[:4])
                raise UiTreeError(
                    f"#{key} matches more than one element in this list ({shown}). "
                    "Use the full id from the list.",
                    ErrorCode.ELEMENT_AMBIGUOUS,
                )
        raise UiTreeError(
            f"#{key} is not known. Get a current list with ui_dump first "
            "(ids belong to that list).",
            ErrorCode.ELEMENT_STALE,
        )

    # ----------------------------------------------------------------- eylem
    def _target_payload(self, node: Node) -> dict:
        dump = self._last
        assert dump is not None  # resolve() dokum yokken hata veriyor
        return {
            "app": dump.app,
            "app_bus": dump.app_bus,
            "app_pid": dump.app_pid,
            "scope": dump.scope,
            "window_ref": dump.window_ref,
            "path": node.path,
            "ref": node.ref,
            "role": node.role,
            "name": node.name,
        }

    def _act(self, payload: dict, fallback: str) -> dict:
        resp = _call(payload, ACT_TIMEOUT)
        if not resp.get("ok"):
            raise UiTreeError(resp.get("error") or fallback, _code(resp.get("code")))
        resp["snapshot"] = self._last.snapshot if self._last else ""
        return resp

    def click(self, node_id: str, action: str = "click") -> dict:
        node = self.resolve(node_id)
        if not node.actions:
            # Koordinata DUSMUYORUZ: olculen AT-SPI koordinatlari yanlis.
            raise UiTreeError(
                f"{node.role} \"{node.name}\" offers no action. Not trying a "
                "coordinate click -- the positions AT-SPI reports are wrong on "
                "this system. Look with screen_capture and use `mouse`.",
                ErrorCode.ACTION_UNSUPPORTED,
            )
        return self._act(
            {"cmd": "act", "action": action, **self._target_payload(node)},
            "The action could not be performed.",
        )

    def set_text(self, node_id: str, text: str) -> dict:
        node = self.resolve(node_id)
        return self._act(
            {"cmd": "settext", "text": text, **self._target_payload(node)},
            "The text could not be written.",
        )


# ------------------------------------------------------------------ bicimleme
def describe_windows(wins: list[Window]) -> str:
    """Pencere listesini modele gosterilecek duz metne cevir."""
    if not wins:
        return (
            "No open window is visible. AT-SPI only shows applications that "
            "publish an accessibility tree; some Chromium-based "
            "applications do not appear at all without `--force-renderer-accessibility`. "
            "Use screen_capture to look at the screen."
        )
    lines = []
    for w in wins:
        mark = "▸ " if w.active else "  "
        lines.append(f"{mark}{w.label}")
    lines.append("")
    lines.append(f"{len(wins)} window(s) · ▸ focused")
    lines.append(
        "NOTE: only applications that publish an accessibility tree are listed; "
        "an open application may be missing here."
    )
    return "\n".join(lines)


def describe(dump: Dump) -> str:
    """Dokumu modele gosterilecek duz metne cevir."""
    head = f"**{dump.app}** — {dump.window}" if dump.window else f"**{dump.app}**"
    lines = [head, ""]
    if dump.same_name > 1:
        lines[1:1] = [
            f"Note: {dump.same_name} applications with this name are open; this list is "
            f"the one with pid {dump.app_pid} (the focused one, if it was).",
            "",
        ]
    if not dump.nodes:
        lines.append(
            "This window publishes no accessibility tree (it came back empty). Some "
            "Electron applications give no content without `--force-renderer-accessibility`. "
            "Use screen_capture to see the screen."
        )
        return "\n".join(lines)

    for n in dump.nodes:
        lines.append("  " + n.describe())
    lines.append("")
    clickable = sum(1 for n in dump.nodes if n.clickable)
    editable = sum(1 for n in dump.nodes if n.editable)
    lines.append(
        f"{len(dump.nodes)} node(s) · {clickable} clickable · {editable} editable"
    )
    if dump.truncated:
        lines.append(
            "⚠️ The list was cut short. Narrow it with target set to one application."
        )
    lines.append(
        "To click use ui_click(\"#id\"); to type into a text field "
        "ui_set_text(\"#id\", \"...\")."
    )
    return "\n".join(lines)
