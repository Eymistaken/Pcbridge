"""Target policy for desktop actions: what an action would DO, not who asks.

`SafetyGate` answers "may this caller act at all" -- desktop enabled, screen
lock, grant, revoke, activity, rate limit. This module answers a different
question: "is this particular target off limits regardless of the grant."
docs/dev/desktop-rules.md §4 calls these gate (class G) rules; items 7, 6 and 5 live here.

Pure decisions, no I/O, like `models.py`. That is deliberate: these gates must
be testable without a desktop, and a future non-Python accessibility provider
has to reach the same verdict from the same table.
"""

from __future__ import annotations

from .errors import DesktopError, ErrorCategory, ErrorCode

# OLCULDU (2026-09-12, bu makine): AT-SPI'da parolaya ozel bir STATE yok ve
# adinda "password" gecen tek rol bu. GTK gorunurlugu kapali girisi, Firefox
# ve Chromium da `<input type=password>` alanini bu role esliyor.
#   python3 -c "import gi; gi.require_version('Atspi','2.0');
#               from gi.repository import Atspi;
#               print(Atspi.role_get_name(Atspi.Role.PASSWORD_TEXT))"
#   -> 'password text'
PASSWORD_ROLES = frozenset({"password text"})


def _normalize(role: str | None) -> str:
    return (role or "").strip().lower()


def check_text_target(*, role: str | None, name: str | None = None) -> None:
    """Reddet: parola alanina yazma. Gecerse sessizce doner.

    `force=true` bu kapiyi ACMAZ ve bilerek boyle: gucu yeten bir bayrak
    olsaydi ajan reddi gorunce once onu denerdi. Parola yoneticisi ya da
    kullanicinin kendisi yazsin.
    """
    if _normalize(role) not in PASSWORD_ROLES:
        return
    label = (name or "").strip()
    where = f" ({label})" if label else ""
    raise DesktopError(
        code=ErrorCode.PASSWORD_FIELD,
        message=(
            f"This is a password field{where}; nothing will be typed into it. The "
            "rule is independent of the desktop grant and `force` does not lift it. "
            "The user or a password manager must enter the password; ask the user "
            "first if needed."
        ),
        category=ErrorCategory.SAFETY,
        retryable=False,
        suggested_action=(
            "Ask the user to fill this field themselves, or use their password "
            "manager. Do not retry with force."
        ),
        backend="desktop.accessibility",
    )


# Ayni hedefe tekrar tekrar tiklamayi sayarken "tiklama" sayilanlar.
# `move`, `scroll`, `type` gibi eylemler hedefi degistirir ve seriyi kirar;
# `wait` KIRMAZ, cunku "tikla-bekle-tikla" tam da donguye giren ajanin deseni.
CLICK_ACTIONS = frozenset({
    "click", "double_click", "triple_click", "right_click", "middle_click",
    "ui_click",
})


def click_target_key(action: str, args: dict) -> tuple:
    """Iki tiklamanin AYNI hedefe gidip gitmedigini belirleyen anahtar.

    Erisilebilirlik tiklamasi dugum kimligiyle, koordinat tiklamasi kendi
    uzayiyla birlikte (`monitor`/`shot`) tanimlanir: ayni (x, y) farkli bir
    cekimde farkli bir noktadir, ayni hedef sayilmamali.

    `x`/`y` verilmemisse hedef "imlecin su anki yeri"dir. Araya bir `move`
    girseydi seri zaten kirilmis olurdu, o yuzden iki koordinatsiz tiklama
    gercekten ayni noktaya gider.
    """
    if action == "ui_click":
        return ("ui", str(args.get("id") or ""))
    return (
        "pt",
        action,
        str(args.get("button") or ""),
        args.get("x"),
        args.get("y"),
        args.get("monitor"),
        args.get("shot"),
    )


# Pencereyi kapatan / uygulamadan cikan kisayollar. Akor olarak tutuluyor:
# "alt+F4" ile "F4+alt" ayni seydir, sira ve buyuk/kucuk harf onemsiz.
#
# Bilerek DAR: yalnizca cagiranin kendi gonderdigi dizeye bakiyor, yani yerel
# dilden ve erisilebilirlik agacindan bagimsiz. Pencerenin kapatma DUGMESI bu
# kapiya girmiyor -- AT-SPI'da "close" diye bir rol yok, ad eslestirmek ise
# dile bagli ("Close"/"Kapat"/"Fermer") ve bir ipucunu kapatan zararsiz bir
# dugmeyi de yakalardi. Yanlis pozitif bu maddede en pahali sey.
CLOSE_COMBOS = frozenset({
    frozenset({"alt", "f4"}),
    frozenset({"ctrl", "q"}),
    frozenset({"ctrl", "w"}),
    frozenset({"ctrl", "shift", "q"}),
    frozenset({"super", "q"}),
})


def _chord(keys: str | None) -> frozenset[str]:
    return frozenset(
        part.strip().lower() for part in (keys or "").split("+") if part.strip()
    )


def is_close_combo(keys: str | None) -> bool:
    """Bu kombinasyon pencere kapatir / uygulamadan cikar mi?"""
    return _chord(keys) in CLOSE_COMBOS


def check_key_combo(keys: str | None, *, confirm_close: bool = False) -> None:
    """Reddet: onaylanmamis kapatma kisayolu. Gecerse sessizce doner.

    `expect_focus` ile ayni desen: kaza beyan edilmemis bir niyetten cikiyor,
    o yuzden cozum niyeti SOYLETMEK. Bu yuzden `force` degil, ayri bir
    `confirm_close`: `force` etkinlik kontrolunu atlayan bir bayrak ve onu
    burada da gecerli kilmak "reddi gorunce force dene" refleksini odullendirir.
    """
    if not is_close_combo(keys) or confirm_close:
        return
    raise DesktopError(
        code=ErrorCode.CONFIRMATION_REQUIRED,
        message=(
            f"{keys!r} closes a window or quits the application; unsaved work "
            "would be gone without anyone being asked. If that is really what you "
            "want, repeat the call with `confirm_close`. If you do not mean to "
            "close anything, pick another shortcut."
        ),
        category=ErrorCategory.SAFETY,
        retryable=False,
        suggested_action=(
            "Repeat the call with confirm_close set, but only if closing is "
            "actually intended; otherwise ask the user first."
        ),
        backend="desktop.input",
    )


__all__ = [
    "CLICK_ACTIONS",
    "CLOSE_COMBOS",
    "PASSWORD_ROLES",
    "check_key_combo",
    "check_text_target",
    "click_target_key",
    "is_close_combo",
]
