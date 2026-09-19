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
        # bloklayabiliyor ve kullanicinin bunu bilmesi lazim. Bir eylemde
        # eylem gitmis de olabilir, gitmemis de: TEKRARLANMAZ. Okuma ise
        # yalnizca zaman asimi.
        acting = payload.get("cmd") in ("act", "settext")
        raise UiTreeError(
            f"Uygulama {timeout} saniyede cevap vermedi. Donmus olabilir; "
            "ekran goruntusuyle bakin (screen_capture).",
            ErrorCode.EXECUTION_UNKNOWN if acting else ErrorCode.TIMEOUT,
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
                "Iki dugum ayni kimligi aldi; liste guvenilir degil.",
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
            raise UiTreeError(resp.get("error") or "Agac okunamadi.", _code(resp.get("code")))
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
                resp.get("error") or "Odaktaki pencere okunamadi.", _code(resp.get("code"))
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
                resp.get("error") or "Pencere listesi okunamadi.", _code(resp.get("code"))
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
                "Henuz ui_dump cagrilmadi; kimlikler o listeden geliyor.",
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
                    f"#{key} bu listede birden fazla ogeye uyuyor ({shown}). "
                    "Listedeki tam kimligi kullanin.",
                    ErrorCode.ELEMENT_AMBIGUOUS,
                )
        raise UiTreeError(
            f"#{key} taninmiyor. Once ui_dump ile guncel listeyi alin "
            "(kimlikler o listeye ait).",
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
                f"{node.role} \"{node.name}\" bir eylem sunmuyor. Koordinatla "
                "tiklamayi denemiyorum -- AT-SPI'in bildirdigi konumlar bu "
                "sistemde yanlis. screen_capture ile bakip `mouse` kullanin.",
                ErrorCode.ACTION_UNSUPPORTED,
            )
        return self._act(
            {"cmd": "act", "action": action, **self._target_payload(node)},
            "Eylem uygulanamadi.",
        )

    def set_text(self, node_id: str, text: str) -> dict:
        node = self.resolve(node_id)
        return self._act(
            {"cmd": "settext", "text": text, **self._target_payload(node)},
            "Metin yazilamadi.",
        )


# ------------------------------------------------------------------ bicimleme
def describe_windows(wins: list[Window]) -> str:
    """Pencere listesini modele gosterilecek duz metne cevir."""
    if not wins:
        return (
            "Acik pencere gorunmuyor. AT-SPI yalnizca erisilebilirlik agaci "
            "yayinlayan uygulamalari gosterir; Chromium tabanli bazi "
            "uygulamalar `--force-renderer-accessibility` olmadan hic "
            "gorunmez. Ekrana bakmak icin screen_capture kullanin."
        )
    lines = []
    for w in wins:
        mark = "▸ " if w.active else "  "
        lines.append(f"{mark}{w.label}")
    lines.append("")
    lines.append(f"{len(wins)} pencere · ▸ odaktaki")
    lines.append(
        "NOT: burada yalnizca erisilebilirlik agaci yayinlayan uygulamalar var; "
        "acik olup listede gorunmeyen uygulama olabilir."
    )
    return "\n".join(lines)


def describe(dump: Dump) -> str:
    """Dokumu modele gosterilecek duz metne cevir."""
    head = f"**{dump.app}** — {dump.window}" if dump.window else f"**{dump.app}**"
    lines = [head, ""]
    if dump.same_name > 1:
        lines[1:1] = [
            f"Not: ayni adla {dump.same_name} uygulama acik; bu liste pid "
            f"{dump.app_pid} olaninki (odaktaysa o secildi).",
            "",
        ]
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
