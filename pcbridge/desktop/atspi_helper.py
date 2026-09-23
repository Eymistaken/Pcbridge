#!/usr/bin/env python3
"""AT-SPI yardimcisi — SISTEM python3 ile calisir, venv'den IMPORT EDILMEZ.

NEDEN AYRI SUREC
    1. `gi` (PyGObject) venv'de yok ve oraya pip ile kurmak derleme
       bagimliliklari istiyor. Sistem python3'unde hazir; ikisi de 3.12.3.
    2. Daha onemlisi: AT-SPI cevap vermeyen bir uygulamada BLOKLAYABILIYOR.
       Ayri surec sert bir zaman asimiyla oldurulebilir; ayni sureçte olsaydi
       bir MCP araci 110 saniye kilitlenirdi.

PROTOKOL
    stdin'den tek bir JSON istek, stdout'a tek bir JSON cevap. Baska hicbir sey
    stdout'a yazilmaz (GLib'in `dbind-WARNING` gurultusu stderr'de kalir).

        {"cmd": "dump", "target": "focused", "interactive_only": true}
        {"cmd": "act", "app_bus": ":1.30", "ref": "/org/.../a11y/1b71...",
         "scope": "window", "window_ref": "...", "path": [0,1,2],
         "role": "...", "name": "..."}
        {"cmd": "settext", ..., "text": "..."}

    Cevap her zaman `ok` alani tasir; `ok: false` ise `error` da vardir,
    kararli bir sebep varsa `code` da (`ELEMENT_STALE`, `TARGET_MISMATCH`,
    `ELEMENT_AMBIGUOUS`, `ACTION_UNSUPPORTED`, `TEXT_MISMATCH`). Metin JSON'un
    icinde stdin'den geliyor -- argv'ye KONMAZ, yoksa `ps` ciktisinda
    gorunurdu.

UYGULAMANIN CEVABI DA DENETLENIR (olculdu 2026-09-19, GTK4 4.14)
    Devre disi bir dugmede `DoAction` false donuyor ve hicbir sey
    tiklanmiyor: bu bir hata, "tiklandi" degil. En fazla 5 karakter tutan bir
    alana yazinca uygulama true donup 5 karakter tutuyor: cevap bir sey
    kanitlamiyor, o yuzden metin geri okunup butunuyle karsilastiriliyor.
    Native okuyucu (`accessibility/action.rs`) ayni kurallarla calisiyor.

HEDEF KIMLIGI (olculdu 2026-09-19, GTK4 4.14 ve gnome-shell)
    Her dugumun bir D-Bus nesne yolu var (`node.path`), uygulamanin da tekil
    bir veriyolu adi (`node.app.bus_name`, `:1.44` gibi). Ikisi birlikte
    dugumun KENDISINI gosterir: araya dugum eklenince 33 nesnenin 20'sinin
    indeks yolu kaydi ama 33'unun de nesne yolu ayni kaldi; pencere basligi
    degisince pencerenin yolu degismedi; yeniden yaratilan dugme YENI bir yol
    aldi. Eylem bu kimlige gore cozulur. Eskiden yol tutmazsa rol+etiketle
    ARANIYORDU ve ilk eslesme seciliyordu: test penceresinde uc tane
    "Kapat" vardi, ucuncusu pencerenin kendi kapatma dugmesiydi.

OLCULDU (2026-08-02)
    GTK4 agaci DERIN: gnome-text-editor'de duzenlenebilir `text` dugumu 16.
    seviyede, agacin dibi 18. Bu yuzden sig bir derinlik siniri konulamaz;
    sinir dugum SAYISINDA.
"""

from __future__ import annotations

import json
import sys
import time
import warnings
from typing import NoReturn

# `get_action_name` deprecated ama yerine onerilen `get_localized_name` Turkce
# donduruyor ("tikla"), oysa bize kanonik "click" lazim. Uyariyi bastiriyoruz.
warnings.filterwarnings("ignore", category=DeprecationWarning)

MAX_DEPTH = 100  # dongusel agaca karsi emniyet, gercek sinir dugum sayisi
DEFAULT_MAX_NODES = 400
# Yeri degismis bir dugumu kimligiyle ararken gezilecek en fazla dugum. Dokum
# tavaniyla ayni (DEFAULT_MAX_NODES * 25): dokumde gorunen her dugum bulunur.
SEARCH_LIMIT = 10_000
# Yazilan metnin butun olarak geri okunmasi icin beklenen en uzun sure. GTK4
# hemen cevap veriyor; metni sonradan uygulayan bir arac bu kadar vakit alir.
TEXT_SETTLE = 0.3
TEXT_POLL = 0.05

# Adi olmasa bile modele anlatilmaya deger roller (metin tasiyanlar).
TEXT_ROLES = {
    "label", "text", "heading", "static", "list item", "menu item",
    "table cell", "paragraph", "link", "tooltip", "status bar",
}
# Etkilesime acik sayilan roller (aksiyonu olmasa bile listelenir).
INTERACTIVE_ROLES = {
    "push button", "toggle button", "check box", "radio button", "combo box",
    "entry", "text", "menu item", "link", "slider", "spin button", "tab",
    "list item", "menu", "check menu item", "radio menu item",
}
# Kapsayici roller: eylem bildirseler bile HEDEF SAYILMAZLAR.
# OLCULDU (2026-08-02): Chromium/Electron her dugume `doDefault` ve
# `showContextMenu` ilistiriyor -- bunlar noktasiz oldugu icin GAction
# filtresine takilmiyor. Filtresiz birakilinca claude-desktop'un penceresi
# "3 tiklanabilir dugum" gibi gorunuyor, oysa icerik hic yayinlanmamis;
# model de tiklanacak bir sey oldugunu saniyor.
CONTAINER_ROLES = {
    "application", "frame", "window", "dialog", "panel", "filler",
    "scroll pane", "viewport", "section", "document frame", "document web",
    "redundant object", "layered pane", "split pane", "tool bar",
}
# Kayda deger durumlar; hepsini basmak gurultu olurdu.
STATE_FLAGS = (
    ("ENABLED", "enabled"),
    ("SENSITIVE", "sensitive"),
    ("FOCUSED", "focused"),
    ("CHECKED", "checked"),
    ("SELECTED", "selected"),
    ("EDITABLE", "editable"),
    ("EXPANDED", "expanded"),
)


class Failure(Exception):
    """Bir islem hatasi: `handle()` bunu `ok: false` cevabina cevirir."""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


def _fail(msg: str, code: str = "") -> NoReturn:
    # Cikis degil istisna: komutlar surecsiz, ayni modul icinde test
    # edilebilsin. Cevabi `handle()` yaziyor; cikis kodu yine 0 kaliyor
    # (protokol hatasi degil, ISLEM hatasi).
    raise Failure(msg, code)


# Ice aktarma hatasi burada CIKISA cevrilmez: modul, saf yardimcilari (filtre
# kurallari, _dedup) test edilebilsin diye `gi` olmadan da ice aktarilabilmeli.
# Gercek hata `main()`de bildirilir.
try:
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi

    ATSPI_ERROR = ""
except Exception as exc:  # pragma: no cover - sistemde kuruluysa calismaz
    Atspi = None  # type: ignore[assignment]
    ATSPI_ERROR = str(exc)


# ----------------------------------------------------------------- yardimcilar
def _states(node) -> tuple[list[str], object]:
    try:
        st = node.get_state_set()
    except Exception:
        return [], None
    out = []
    for attr, label in STATE_FLAGS:
        try:
            if st.contains(getattr(Atspi.StateType, attr)):
                out.append(label)
        except Exception:
            continue
    return out, st


def is_real_action(name: str) -> bool:
    """`click` gercek bir eylem mi, `page.save-as` uygulama komutu mu?

    OLCULDU (2026-08-02): GTK4 her dugume uygulamanin TUM GAction grubunu
    ilistiriyor. gnome-text-editor'de frame 35 "eylem" bildiriyor:
    `page.save-as`, `clipboard.copy`, `win.open`, `window.minimize`... Bunlar
    tiklanabilirlik degil, uygulama komutlari. Ayirt edici isaret NOKTA:
    GAction'lar her zaman `grup.ad` bicimindeyken gercek AT-SPI eylemleri tek
    kelime (`click`, `press`, `activate`). Nokta iceren adlar elenmezse her
    label "tiklanabilir" gorunur ve model olmayan dugmelere basmaya calisir.
    """
    nm = (name or "").strip()
    return bool(nm) and "." not in nm


def _actions(node) -> list[str]:
    try:
        iface = node.get_action_iface()
        if not iface:
            return []
        out = []
        for i in range(iface.get_n_actions()):
            nm = (iface.get_action_name(i) or "").strip()
            if is_real_action(nm):
                out.append(nm)
        return out
    except Exception:
        return []


def _role(node) -> str:
    try:
        return node.get_role_name() or "?"
    except Exception:
        return "?"


def _name(node) -> str:
    try:
        return (node.get_name() or "").strip()
    except Exception:
        return ""


def _ref(node) -> str:
    """Dugumun D-Bus nesne yolu: uygulama icinde dugumun kendisi.

    Yerel bir alan okumasi, D-Bus cagrisi degil; her dugumde okumak bedava.
    """
    try:
        return str(node.path or "")
    except Exception:
        return ""


def _bus(node) -> str:
    """Uygulamanin tekil veriyolu adi (`:1.44`). Uygulama yeniden baslarsa
    degisir; Chromium nesne yollarini 1'den yeniden saydigi icin bu sart."""
    try:
        return str(node.app.bus_name or "")
    except Exception:
        return ""


def _pid(app) -> int:
    try:
        return int(app.get_process_id() or 0)
    except Exception:
        return 0


def _desktop():
    try:
        Atspi.init()
        return Atspi.get_desktop(0)
    except Exception as exc:
        _fail(f"Cannot connect to the AT-SPI desktop: {exc}")


def _children(node) -> list:
    out = []
    try:
        n = node.get_child_count()
    except Exception:
        return out
    for i in range(n):
        try:
            c = node.get_child_at_index(i)
        except Exception:
            continue
        if c is not None:
            out.append(c)
    return out


def _apps(desk) -> list:
    return _children(desk)


def _is_active(app) -> bool:
    for w in _children(app):
        try:
            if w.get_state_set().contains(Atspi.StateType.ACTIVE):
                return True
        except Exception:
            continue
    return False


def _find_active(desk):
    """ACTIVE durumundaki pencere -> (uygulama, pencere, uygulamadaki indeks).

    C bolumunde `Shell.Introspect` "Access denied" verdigi icin odak bilgisinin
    tek kaynagi burasi.
    """
    for app in _apps(desk):
        for j in range(app.get_child_count()):
            try:
                w = app.get_child_at_index(j)
                if w is None:
                    continue
                st = w.get_state_set()
                if st.contains(Atspi.StateType.ACTIVE):
                    return app, w, j
            except Exception:
                continue
    return None, None, -1


def _find_app(desk, name: str) -> tuple[object | None, int]:
    """Okunacak uygulamayi ada gore sec -> (uygulama, ayni adli kac tane).

    Tam eslesme once. Kismi eslesme FARKLI adli birden fazla uygulamaya
    uyuyorsa sessizce ilki secilmez: model hangisini okudugunu bilemezdi.
    Ayni adli birden fazla surec varsa (iki `python3` gibi) odakta olan,
    yoksa ilki okunur ve sayi dokume yazilir.
    """
    want = name.strip().lower()
    apps = _apps(desk)
    found = [a for a in apps if (_name(a) or "").lower() == want]
    if not found:
        found = [a for a in apps if want and want in (_name(a) or "").lower()]
        names = sorted({_name(a) for a in found})
        if len(names) > 1:
            _fail(
                f"{name!r} matches more than one application: {', '.join(names)}. "
                "Give the full name.",
                "ELEMENT_AMBIGUOUS",
            )
    if not found:
        return None, 0
    active = [a for a in found if _is_active(a)]
    return (active or found)[0], len(found)


# ------------------------------------------------------------------- gezinme
def _walk(root, base_path: list[int], interactive_only: bool, max_nodes: int) -> tuple[list[dict], bool]:
    """Agaci gez, modele gosterilecek dugumleri topla.

    `path` her zaman UYGULAMA kokunden itibaren indeks zinciri; `act` bunu
    geri takip edecek.
    """
    out: list[dict] = []
    truncated = False
    stack = [(root, list(base_path), 0)]
    visited = 0

    while stack:
        node, path, depth = stack.pop()
        visited += 1
        if visited > max_nodes * 25:  # gezilen dugum tavani (gnome-shell 3121)
            truncated = True
            break
        if depth > MAX_DEPTH:
            continue

        states, st = _states(node)
        showing = bool(st and st.contains(Atspi.StateType.SHOWING))
        role = _role(node)
        name = _name(node)
        acts = _actions(node)
        editable = "editable" in states

        container = role in CONTAINER_ROLES
        keep = showing and (
            editable
            or (role in INTERACTIVE_ROLES)
            or (bool(acts) and not container)
            or (not interactive_only and name and role in TEXT_ROLES)
        )
        if keep and (name or acts or editable):
            if len(out) >= max_nodes:
                truncated = True
            else:
                out.append({
                    "path": path,
                    "ref": _ref(node),
                    "role": role,
                    "name": name,
                    "states": states,
                    "actions": acts,
                    "editable": editable,
                    "depth": depth,
                })

        try:
            n = node.get_child_count()
        except Exception:
            continue
        # Ters sirada yigina koy ki cikisirken soldan saga gezilsin.
        for i in range(n - 1, -1, -1):
            try:
                c = node.get_child_at_index(i)
            except Exception:
                continue
            if c is not None:
                stack.append((c, path + [i], depth + 1))

    return _dedup(out), truncated


def _dedup(nodes: list[dict]) -> list[dict]:
    """GTK4'un sarmalayici dugumlerini ele.

    OLCULDU: GTK4 dugmeleri `push button 'Ac'` -> `toggle button 'Ac'` diye
    ic ice sariyor. Disttaki eylemsiz, ictekinde `click` var. Ikisini birden
    listelemek modele ayni dugmeyi iki kez gosterir ve hangisine basacagini
    belirsizlestirir. Eylemsiz olani, ADI AYNI olan bir alt dugum eylemliyse
    duser.
    """
    actionable = [n for n in nodes if n["actions"] or n["editable"]]
    drop: set[int] = set()
    for i, n in enumerate(nodes):
        if n["actions"] or n["editable"] or not n["name"]:
            continue
        p = n["path"]
        for m in actionable:
            q = m["path"]
            if len(q) > len(p) and q[: len(p)] == p and m["name"] == n["name"]:
                drop.add(i)
                break
    return [n for i, n in enumerate(nodes) if i not in drop]


def _node_at(app, path: list[int]):
    """Indeks zincirini takip et."""
    node = app
    for idx in path:
        try:
            if idx < 0 or idx >= node.get_child_count():
                return None
            node = node.get_child_at_index(idx)
        except Exception:
            return None
        if node is None:
            return None
    return node


def _find_ref(root, ref: str, limit: int = SEARCH_LIMIT):
    """`root`un altinda nesne yolu `ref` olan dugum. Yol kaydiginda kullanilir.

    Iki eslesme cikarsa ilki SECILMEZ. Nesne yolu bir uygulama icinde tekil
    olmali; tekil degilse arac kimlik vermiyor demektir ve hangi dugumun
    kastedildigini bilmenin yolu yok.
    """
    stack = [(root, 0)]
    seen = 0
    hit = None
    while stack:
        node, depth = stack.pop()
        seen += 1
        if seen > limit or depth > MAX_DEPTH:
            continue
        if _ref(node) == ref:
            if hit is not None:
                _fail(
                    "This application gives two nodes the same id; which one is "
                    "meant cannot be known. Look at the screen (screen_capture) and "
                    "use `mouse`.",
                    "ELEMENT_AMBIGUOUS",
                )
            hit = node
        for c in reversed(_children(node)):
            stack.append((c, depth + 1))
    return hit


def _resolve(req: dict):
    """Istekteki dugumu KIMLIGIYLE bul -> (dugum, "path" | "moved", uygulama_adi).

    Uc sart, ucu de gecmeden hicbir dugume dokunulmaz:

    1. Ayni uygulama: veriyolu adi dokumdeki. Uygulama kapanmissa baska bir
       uygulamaya DUSULMEZ -- eskiden odaktakine dusuluyordu ve Chrome
       kapaninca ayni yoldaki kabuk dugmesine basilabiliyordu.
    2. Ayni nesne: once indeks yolu denenir, nesne yolu tutmuyorsa ayni
       hedefin (odak dokumunde ayni pencerenin) icinde nesne yolu aranir.
       Rol+etiketle ARANMAZ: yeniden yaratilmis ya da ayni adli baska bir
       dugme kimlik tasimaz.
    3. Ayni anlam: rol ve etiket dokumdekiyle ayni. "Takip et" dugmesi
       "Takibi birak" olmussa basmak yanlis olurdu.
    """
    ref = str(req.get("ref") or "")
    bus = str(req.get("app_bus") or "")
    want_role = req.get("role") or ""
    want_name = req.get("name") or ""
    label = f"{want_role} {want_name!r}"
    if not ref or not bus:
        _fail(
            "The request carries no target id; refresh the list with ui_dump.",
            "ELEMENT_STALE",
        )

    desk = _desktop()
    app = next((a for a in _apps(desk) if _bus(a) == bus), None)
    app_label = req.get("app") or bus
    if app is None:
        _fail(
            f"{app_label!r} is no longer open (it closed or restarted). "
            "Nothing fell through to another application; refresh the list with ui_dump.",
            "ELEMENT_STALE",
        )

    scope = app
    if req.get("scope") == "window":
        window_ref = str(req.get("window_ref") or "")
        scope = next((w for w in _children(app) if window_ref and _ref(w) == window_ref), None)
        if scope is None:
            _fail(
                f"The {app_label!r} window of the dump has closed; refresh the "
                "list with ui_dump.",
                "ELEMENT_STALE",
            )

    how = "path"
    node = _node_at(app, list(req.get("path") or []))
    if node is None or _ref(node) != ref:
        how = "moved"
        node = _find_ref(scope, ref)
    if node is None:
        _fail(
            f"The target is gone: {label}. The interface may have been redrawn; "
            "refresh the list with ui_dump.",
            "ELEMENT_STALE",
        )
    if _role(node) != want_role or _name(node) != want_name:
        _fail(
            f"The target changed: {label} is now {_role(node)} {_name(node)!r}. "
            "Nothing was done; refresh the list with ui_dump.",
            "TARGET_MISMATCH",
        )
    return node, how, _name(app)


# -------------------------------------------------------------------- komutlar
def cmd_dump(req: dict) -> dict:
    desk = _desktop()
    target = (req.get("target") or "focused").strip()
    interactive_only = bool(req.get("interactive_only", True))
    max_nodes = int(req.get("max_nodes") or DEFAULT_MAX_NODES)

    # Kimlik alanlari eylemin dogru hedefe gittigini dogrulamak icin: eylem
    # istegi bunlari geri getirir (`_resolve`). Odak dokumunde hedef tek bir
    # pencere, ada gore dokumde uygulamanin tamami.
    if target.lower() in ("focused", "odak", "aktif"):
        app, win, widx = _find_active(desk)
        if app is None:
            _fail(
                "No window has the focus (AT-SPI marks no window ACTIVE). "
                "Click a window, or give an application name "
                "as target.",
                "TARGET_MISMATCH",
            )
        root, base, same_name = win, [widx], 1
        scope, window_ref = "window", _ref(win)
        app_name, win_name = _name(app), _name(win)
    else:
        app, same_name = _find_app(desk, target)
        if app is None:
            names = sorted({_name(a) for a in _apps(desk) if _name(a)})
            _fail(
                f"Application not found: {target!r}. Open ones: " + ", ".join(names),
                "TARGET_MISMATCH",
            )
        root, base = app, []
        scope, window_ref = "app", ""
        app_name = _name(app)
        first = _children(app)
        win_name = _name(first[0]) if first else ""

    nodes, truncated = _walk(root, base, interactive_only, max_nodes)
    return {
        "ok": True,
        "app": app_name,
        "app_bus": _bus(app),
        "app_pid": _pid(app),
        "same_name": same_name,
        "scope": scope,
        "window": win_name,
        "window_ref": window_ref,
        "nodes": nodes,
        "truncated": truncated,
    }


def cmd_act(req: dict) -> dict:
    node, how, app_name = _resolve(req)
    want = (req.get("action") or "click").strip().lower()
    try:
        iface = node.get_action_iface()
        n = iface.get_n_actions() if iface else 0
    except Exception:
        iface, n = None, 0
    if not iface or n <= 0:
        _fail(
            f"{_role(node)} {_name(node)!r} offers no action (no Action "
            "interface). Not falling back to a coordinate click.",
            "ACTION_UNSUPPORTED",
        )
    names = [(iface.get_action_name(i) or "").lower() for i in range(n)]
    idx = 0
    for cand in (want, "click", "press", "activate", "jump"):
        if cand in names:
            idx = names.index(cand)
            break
    action = names[idx] or f"action{idx}"
    try:
        ok = iface.do_action(idx)
    except Exception as exc:
        _fail(f"The action could not be run: {exc}")
    if not ok:
        # Uygulamanin kendi cevabi: eylem yapilmadi (GTK4'te devre disi dugme).
        _fail(
            f"The application did not perform {action!r}: {_role(node)} {_name(node)!r} "
            "may be disabled right now. Nothing was done.",
            "ACTION_UNSUPPORTED",
        )
    return {
        "ok": True,
        "app": app_name,
        "ref": _ref(node),
        "role": _role(node),
        "name": _name(node),
        "action": action,
        "resolved_by": how,
        "returned": True,
    }


def cmd_settext(req: dict) -> dict:
    node, how, app_name = _resolve(req)
    text = req.get("text")
    if text is None:
        _fail("the text field is missing.")
    try:
        et = node.get_editable_text_iface()
    except Exception:
        et = None
    if not et:
        _fail(
            f"{_role(node)} {_name(node)!r} is not editable (no EditableText "
            "interface).",
            "ACTION_UNSUPPORTED",
        )
    refused = (
        f"The application did not accept the text: {_role(node)} {_name(node)!r} may "
        "not be editable right now. Nothing was written."
    )
    try:
        ti = node.get_text_iface()
        old_len = ti.get_character_count() if ti else 0
        if old_len and not et.delete_text(0, old_len):
            _fail(refused, "ACTION_UNSUPPORTED")
        # UZUNLUK BAYT CINSINDEN, karakter degil -- olculdu 2026-08-02.
        # `len(text)` verilince "merhaba @ ış ğü ÖÇ — pcbridge D testi #1"
        # (40 karakter, 48 bayt) 32 karaktere DUSUYORDU: Turkce harfler 2,
        # em-dash 3 bayt. Sessiz kirpma, hicbir hata vermeden. (2026-09-19:
        # GTK4'un girdi alani bu uzunlugu hic kullanmiyor; metin kutusu
        # kullaniyor. Native okuyucu bu yuzden SetTextContents kullaniyor.)
        if not et.insert_text(0, text, len(text.encode("utf-8"))):
            _fail(refused, "ACTION_UNSUPPORTED")
    except Failure:
        raise
    except Exception as exc:
        _fail(f"The text could not be written: {exc}")
    new_len, verified = _read_back(ti, text)
    return {
        "ok": True,
        "app": app_name,
        "ref": _ref(node),
        "role": _role(node),
        "name": _name(node),
        "replaced_chars": old_len,
        "now_chars": new_len,
        "resolved_by": how,
        "verified": verified,
    }


def _read_back(ti, text: str) -> tuple[int, bool]:
    """Yazilan metni geri oku -> (simdiki karakter sayisi, dogrulandi mi).

    Metin butunuyle aynidir ya da `TEXT_MISMATCH` doner; mesajda yalnizca
    sayilar var, metnin kendisi yok. Text arayuzu yoksa ya da hic
    okunamiyorsa yazma dogrulanamaz ama hata da degildir: metin gitti.

    OLCULDU (2026-09-19): PyGObject'te `get_text_iface()` ayri bir nesne
    degil, dugumun kendisi. `ti.get_text(0, n)` bu yuzden Text'in degil
    `Atspi.Accessible.get_text()`in (arguman almaz) cagrisi olur ve TypeError
    verir; metin `Atspi.Text.get_text(dugum, 0, n)` ile okunur. Bitis -1
    birakilmaz: GTK4 `GetText(0, -1)`e bos metin donuyor.
    """
    if ti is None:
        return -1, False
    until = time.monotonic() + TEXT_SETTLE
    last = None
    while True:
        try:
            count = ti.get_character_count()
            back = Atspi.Text.get_text(ti, 0, count) if count > 0 else ""
        except Exception:
            if last is None:
                return -1, False
        else:
            if back == text:
                return len(text), True
            last = len(back)
        if last is not None and time.monotonic() >= until:
            _fail(
                f"The text was written incompletely or differently: {len(text)} characters "
                f"were sent, the field now holds {last}. Its content changed; look with "
                "ui_dump.",
                "TEXT_MISMATCH",
            )
        time.sleep(TEXT_POLL)


def cmd_windows(req: dict) -> dict:
    """Acik pencereler. `dump`tan cok daha ucuz: agaci GEZMEZ, iki seviye iner.

    OLCULDU 2026-08-02: 22 satir 42 ms. Karsilastirma icin gnome-shell'in
    agacini gezmek 1,93 saniye suruyordu.

    Pencere listesinin tek kaynagi burasi: `Shell.Introspect.GetWindows()` bu
    surumde "Access denied" veriyor (C bolumunde olculdu).
    """
    desk = _desktop()
    out = []
    for app in _apps(desk):
        an = _name(app)
        try:
            n = app.get_child_count()
        except Exception:
            continue
        for j in range(n):
            try:
                w = app.get_child_at_index(j)
                if w is None:
                    continue
                # ACTIVE, STATE_FLAGS'te yok (dugum listesinde anlamsiz) --
                # burada dogrudan okunuyor; odagin tek kaynagi bu.
                _states_unused, st = _states(w)
                active = bool(st and st.contains(Atspi.StateType.ACTIVE))
                out.append({
                    "app": an,
                    "app_bus": _bus(app),
                    "app_pid": _pid(app),
                    "window": _name(w),
                    "ref": _ref(w),
                    "index": j,
                    "role": w.get_role_name(),
                    "active": active,
                    "children": w.get_child_count(),
                })
            except Exception:
                continue
    return {"ok": True, "windows": out}


COMMANDS = {
    "dump": cmd_dump,
    "act": cmd_act,
    "settext": cmd_settext,
    "windows": cmd_windows,
}


def handle(req: dict) -> dict:
    """Tek bir istegi isle ve cevabi dondur. Hicbir sey yazdirmaz.

    Komutlar hatayi `_fail` ile ISTISNA olarak bildirir; burada kararli koduyla
    birlikte `ok: false` cevabina cevrilir. Surecsiz de cagrilabildigi icin
    sozlesme testleri gercek masaustu olmadan ayni kodu kosturuyor.
    """
    fn = COMMANDS.get(str(req.get("cmd") or ""))
    if fn is None:
        return {
            "ok": False,
            "error": f"Unknown command: {req.get('cmd')!r}. Valid: {', '.join(COMMANDS)}",
        }
    try:
        return fn(req)
    except Failure as exc:
        resp = {"ok": False, "error": str(exc)}
        if exc.code:
            resp["code"] = exc.code
        return resp
    except Exception as exc:  # beklenmedik her sey de JSON olarak donsun
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _reply(resp: dict) -> int:
    json.dump(resp, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def _install_hint(debian: str, arch: str) -> str:
    """`sudo apt install …` or, on Arch, `sudo pacman -S --needed …`.

    This helper runs under the system python and cannot import pcbridge, so
    it repeats the small family check of `pcbridge.distro`.
    """
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        text = ""
    ids = []
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key in ("ID", "ID_LIKE"):
            ids += value.strip().strip('"').lower().split()
    if "arch" in ids:
        return "sudo pacman -S --needed " + arch
    return "sudo apt install " + debian


def main() -> int:
    if ATSPI_ERROR:
        return _reply({
            "ok": False,
            "error": f"The AT-SPI bindings could not be loaded ({ATSPI_ERROR}). "
                     "Install: " + _install_hint("python3-gi gir1.2-atspi-2.0",
                                                 "python-gobject at-spi2-core"),
        })
    raw = sys.stdin.read()
    try:
        req = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        return _reply({"ok": False, "error": f"The request could not be read as JSON: {exc}"})
    return _reply(handle(req))


if __name__ == "__main__":
    sys.exit(main())
