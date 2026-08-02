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
        {"cmd": "act",  "app": "...", "path": [0,1,2], "role": "...", "name": "..."}
        {"cmd": "settext", ..., "text": "..."}

    Cevap her zaman `ok` alani tasir; `ok: false` ise `error` da vardir.
    Metin JSON'un icinde stdin'den geliyor -- argv'ye KONMAZ, yoksa `ps`
    ciktisinda gorunurdu.

OLCULDU (2026-08-02)
    GTK4 agaci DERIN: gnome-text-editor'de duzenlenebilir `text` dugumu 16.
    seviyede, agacin dibi 18. Bu yuzden sig bir derinlik siniri konulamaz;
    sinir dugum SAYISINDA.
"""

from __future__ import annotations

import json
import sys
import warnings

# `get_action_name` deprecated ama yerine onerilen `get_localized_name` Turkce
# donduruyor ("tikla"), oysa bize kanonik "click" lazim. Uyariyi bastiriyoruz.
warnings.filterwarnings("ignore", category=DeprecationWarning)

MAX_DEPTH = 100  # dongusel agaca karsi emniyet, gercek sinir dugum sayisi
DEFAULT_MAX_NODES = 400

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


def _fail(msg: str) -> None:
    json.dump({"ok": False, "error": msg}, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.exit(0)  # protokol hatasi degil, ISLEM hatasi -> cikis kodu 0


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


def _desktop():
    try:
        Atspi.init()
        return Atspi.get_desktop(0)
    except Exception as exc:
        _fail(f"AT-SPI masaustune baglanilamadi: {exc}")


def _apps(desk) -> list:
    out = []
    for i in range(desk.get_child_count()):
        try:
            a = desk.get_child_at_index(i)
        except Exception:
            continue
        if a is not None:
            out.append(a)
    return out


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


def _find_app(desk, name: str):
    want = name.strip().lower()
    for app in _apps(desk):
        if (_name(app) or "").lower() == want:
            return app
    for app in _apps(desk):  # kismi eslesme, ikinci tur
        if want in (_name(app) or "").lower():
            return app
    return None


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


def _search(app, role: str, name: str, limit: int = 6000):
    """Agacta rol+etikete gore ara. Yol kaydiginda kullanilir."""
    stack = [(app, [], 0)]
    seen = 0
    while stack:
        node, path, depth = stack.pop()
        seen += 1
        if seen > limit or depth > MAX_DEPTH:
            continue
        if _role(node) == role and _name(node) == name:
            return node, path
        try:
            n = node.get_child_count()
        except Exception:
            continue
        for i in range(n - 1, -1, -1):
            try:
                c = node.get_child_at_index(i)
            except Exception:
                continue
            if c is not None:
                stack.append((c, path + [i], depth + 1))
    return None, None


def _resolve(req: dict):
    """Istekteki hedefi bul: once yol, tutmuyorsa rol+etiket araması.

    -> (dugum, "path" | "search", uygulama_adi)
    """
    desk = _desktop()
    app_name = req.get("app") or ""
    app = _find_app(desk, app_name) if app_name else None
    if app is None:
        app, _w, _j = _find_active(desk)
    if app is None:
        _fail(f"Uygulama bulunamadi: {app_name or '(odaktaki)'}")

    path = list(req.get("path") or [])
    want_role = req.get("role") or ""
    want_name = req.get("name") or ""

    node = _node_at(app, path)
    if node is not None and _role(node) == want_role and _name(node) == want_name:
        return node, "path", _name(app)

    # Yol kaymis: ayni rol+etiketi agacta ara. Parmak izi tutmayan bir dugume
    # ASLA dokunmayiz -- yanlis dugmeye basmak sessiz ve geri alinamaz olurdu.
    node, found_path = _search(app, want_role, want_name)
    if node is None:
        _fail(
            f"Hedef bulunamadi: {want_role} {want_name!r}. Arayuz degismis "
            "olabilir; ui_dump ile listeyi yenileyin."
        )
    return node, "search", _name(app)


# -------------------------------------------------------------------- komutlar
def cmd_dump(req: dict) -> dict:
    desk = _desktop()
    target = (req.get("target") or "focused").strip()
    interactive_only = bool(req.get("interactive_only", True))
    max_nodes = int(req.get("max_nodes") or DEFAULT_MAX_NODES)

    if target.lower() in ("focused", "odak", "aktif"):
        app, win, widx = _find_active(desk)
        if app is None:
            return {
                "ok": False,
                "error": "Odakta pencere yok (AT-SPI hicbir pencereyi ACTIVE "
                         "isaretlemiyor). Bir pencereye tiklayin ya da "
                         "target ile uygulama adi verin.",
            }
        root, base = win, [widx]
        app_name, win_name = _name(app), _name(win)
    else:
        app = _find_app(desk, target)
        if app is None:
            names = sorted({_name(a) for a in _apps(desk) if _name(a)})
            return {
                "ok": False,
                "error": f"Uygulama bulunamadi: {target!r}. Acik olanlar: "
                         + ", ".join(names),
            }
        root, base = app, []
        app_name = _name(app)
        win_name = _name(app.get_child_at_index(0)) if app.get_child_count() else ""

    nodes, truncated = _walk(root, base, interactive_only, max_nodes)
    return {
        "ok": True,
        "app": app_name,
        "window": win_name,
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
        return {
            "ok": False,
            "no_action": True,
            "error": f"{_role(node)} {_name(node)!r} bir eylem sunmuyor "
                     "(Action arayuzu yok).",
        }
    names = [(iface.get_action_name(i) or "").lower() for i in range(n)]
    idx = 0
    for cand in (want, "click", "press", "activate", "jump"):
        if cand in names:
            idx = names.index(cand)
            break
    try:
        ok = iface.do_action(idx)
    except Exception as exc:
        return {"ok": False, "error": f"Eylem calistirilamadi: {exc}"}
    return {
        "ok": True,
        "app": app_name,
        "role": _role(node),
        "name": _name(node),
        "action": names[idx] or f"action{idx}",
        "resolved_by": how,
        "returned": bool(ok),
    }


def cmd_settext(req: dict) -> dict:
    node, how, app_name = _resolve(req)
    text = req.get("text")
    if text is None:
        return {"ok": False, "error": "text alani yok."}
    try:
        et = node.get_editable_text_iface()
    except Exception:
        et = None
    if not et:
        return {
            "ok": False,
            "not_editable": True,
            "error": f"{_role(node)} {_name(node)!r} duzenlenebilir degil "
                     "(EditableText arayuzu yok).",
        }
    try:
        ti = node.get_text_iface()
        old_len = ti.get_character_count() if ti else 0
        if old_len:
            et.delete_text(0, old_len)
        # UZUNLUK BAYT CINSINDEN, karakter degil -- olculdu 2026-08-02.
        # `len(text)` verilince "merhaba @ ış ğü ÖÇ — pcbridge D testi #1"
        # (40 karakter, 48 bayt) 32 karaktere DUSUYORDU: Turkce harfler 2,
        # em-dash 3 bayt. Sessiz kirpma, hicbir hata vermeden.
        et.insert_text(0, text, len(text.encode("utf-8")))
        new_len = ti.get_character_count() if ti else -1
    except Exception as exc:
        return {"ok": False, "error": f"Metin yazilamadi: {exc}"}
    if new_len >= 0 and new_len != len(text):
        return {
            "ok": False,
            "error": f"Metin eksik yazildi: {len(text)} karakter gonderildi, "
                     f"{new_len} karakter olustu.",
        }
    return {
        "ok": True,
        "app": app_name,
        "role": _role(node),
        "name": _name(node),
        "replaced_chars": old_len,
        "now_chars": new_len,
        "resolved_by": how,
    }


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
                    "window": _name(w),
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


def main() -> int:
    if ATSPI_ERROR:
        _fail(
            f"AT-SPI baglantilari yuklenemedi ({ATSPI_ERROR}). "
            "Kurulum: sudo apt install python3-gi gir1.2-atspi-2.0"
        )
    raw = sys.stdin.read()
    try:
        req = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        _fail(f"Istek JSON olarak okunamadi: {exc}")
        return 0
    fn = COMMANDS.get(str(req.get("cmd") or ""))
    if fn is None:
        _fail(f"Bilinmeyen komut: {req.get('cmd')!r}. Gecerli: {', '.join(COMMANDS)}")
        return 0
    try:
        resp = fn(req)
    except Exception as exc:  # beklenmedik her sey de JSON olarak donsun
        resp = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    json.dump(resp, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
