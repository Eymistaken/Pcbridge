"""Guvenlik kapisi — GUI araclarinin gecmek zorunda oldugu tek nokta.

`UYGULAMA.md` (1.x, in git history): "Bu modul olmadan girdi araclarini yayina alma."

Bu ozellik pcbridge'in risk profilini buyutuyor: bugune kadar "uzaktan komut
calistirma" vardi, simdi acik oturumdaki her uygulamaya -- tarayicidaki oturum
acilmis hesaplara, parola yoneticisine -- erisim ekleniyor. Bu yuzden bes kat:

  1. `[desktop] enabled = false` varsayilani. Acmak bilincli bir islem.
  2. Sureli izin. `desktop_unlock(dakika)` sonrasi calisir, sure dolunca
     kendiliginden kapanir. Izin durumu DISKTE tutulur; servis yeniden
     baslayinca izin ne kaybolur ne de uzar.
  3. Ekran kilidi. `org.gnome.ScreenSaver.GetActive` true ise her sey reddedilir.
     Kilitli ekranin arkasina parola yazdirmak yok.
  4. Cakisma korumasi. `Mutter.IdleMonitor` 60 saniyenin altindaysa kullanici
     makine basindadir; yazma eylemleri reddedilir. `force=true` ile bilincli
     olarak gecilir.
  5. Hiz siniri + denetim kaydi. Sonsuz donguye giren bir ajan makineyi
     kilitleyemesin, ve olan biten `audit.log`'tan geriye donuk okunabilsin.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import sessionctx
from .errors import ErrorCategory, ErrorCode
from .lease import LEASE_STATE_FILE, LeaseStore, LeaseToken

if TYPE_CHECKING:
    from .contracts import DesktopStateProvider

logger = logging.getLogger("pcbridge.desktop")

STATE_FILE = LEASE_STATE_FILE


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""
    code: ErrorCode | None = None
    permission_scope: str | None = None
    retryable: bool = False
    suggested_action: str = ""
    category: ErrorCategory = ErrorCategory.SAFETY

    def __bool__(self) -> bool:
        return self.allowed


class ScreenLockState(str, Enum):
    KNOWN_LOCKED = "known_locked"
    KNOWN_UNLOCKED = "known_unlocked"
    UNKNOWN = "unknown"


class ActivityState(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ScreenLockObservation:
    state: ScreenLockState
    observed_at: float
    backend: str = "linux.gnome-screen-saver"


@dataclass(frozen=True)
class ActivityObservation:
    state: ActivityState
    idle_ms: int | None
    observed_at: float
    backend: str = "linux.mutter-idle-monitor"

    def __post_init__(self) -> None:
        known = self.state == ActivityState.KNOWN
        valid_idle = (
            isinstance(self.idle_ms, int)
            and not isinstance(self.idle_ms, bool)
            and self.idle_ms >= 0
        )
        if known != valid_idle:
            raise ValueError("known activity requires a nonnegative idle_ms")


def _busctl_json(dest: str, path: str, iface: str, method: str) -> Any:
    """Tek degerli bir D-Bus cagrisini oku. Hata durumunda None."""
    try:
        proc = subprocess.run(
            ["busctl", "--user", "--json=short", "call", dest, path, iface, method],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout).get("data")
        return data[0] if isinstance(data, list) and data else None
    except Exception:  # noqa: BLE001 — D-Bus yoksa observation unknown olur
        return None


def screen_locked() -> bool | None:
    """Ekran kilitli mi? Ogrenilemezse None."""
    val = _busctl_json(
        "org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver",
        "GetActive",
    )
    return bool(val) if isinstance(val, bool) else None


def idle_ms() -> int | None:
    """Kullanicinin son girdisinden bu yana gecen ms. Ogrenilemezse None."""
    val = _busctl_json(
        "org.gnome.Mutter.IdleMonitor",
        "/org/gnome/Mutter/IdleMonitor/Core",
        "org.gnome.Mutter.IdleMonitor",
        "GetIdletime",
    )
    return int(val) if isinstance(val, int) and not isinstance(val, bool) else None


def observe_screen_lock() -> ScreenLockObservation:
    value = screen_locked()
    state = (
        ScreenLockState.KNOWN_LOCKED
        if value is True
        else ScreenLockState.KNOWN_UNLOCKED
        if value is False
        else ScreenLockState.UNKNOWN
    )
    return ScreenLockObservation(state=state, observed_at=time.time())


def observe_user_activity() -> ActivityObservation:
    value = idle_ms()
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return ActivityObservation(
            state=ActivityState.KNOWN,
            idle_ms=value,
            observed_at=time.time(),
        )
    return ActivityObservation(
        state=ActivityState.UNKNOWN,
        idle_ms=None,
        observed_at=time.time(),
    )


class _PythonDesktopStateProvider:
    def screen_lock(self) -> ScreenLockObservation:
        return observe_screen_lock()

    def user_activity(self) -> ActivityObservation:
        return observe_user_activity()


def screen_lock_decision(observation: ScreenLockObservation) -> Decision:
    if observation.state == ScreenLockState.UNKNOWN:
        from .session import support_note

        note = support_note()
        return Decision(
            False,
            "The screen lock state could not be read. No desktop action starts "
            "before it is confirmed that the session is unlocked."
            + (f" {note}" if note else ""),
            code=ErrorCode.LOCK_STATE_UNKNOWN,
            permission_scope="pcbridge.desktop",
            retryable=True,
            suggested_action="Restore the desktop session connection and retry.",
        )
    if observation.state == ScreenLockState.KNOWN_LOCKED:
        return Decision(
            False,
            "The screen is locked. No input is sent behind a locked screen — "
            "unlock it at the machine.",
            code=ErrorCode.SCREEN_LOCKED,
            permission_scope="pcbridge.desktop",
            retryable=True,
            suggested_action="Unlock the local desktop session and retry.",
        )
    return Decision(True)


class SafetyGate:
    def __init__(
        self,
        cfg: Any,
        *,
        state_provider: DesktopStateProvider | None = None,
    ) -> None:
        self.cfg = cfg
        self.spec = cfg.desktop
        self._state_path = Path(cfg.state_dir) / STATE_FILE
        self._lease = LeaseStore(cfg.state_dir)
        self._state_provider = state_provider or _PythonDesktopStateProvider()
        # The token this CALL was admitted with. A context variable, not a
        # thread-local: worker threads are reused across calls and, in the
        # daemon, across clients.
        self._call_token: ContextVar[LeaseToken | None] = ContextVar(
            "pcbridge_gate_token", default=None
        )
        # The rate window is per MCP session, as it was per process when
        # every client had its own server process.
        self._events: sessionctx.PerSession[deque[float]] = sessionctx.PerSession()

    # ------------------------------------------------------------- izin durumu
    def _read_state(self) -> dict:
        return self._lease.read()

    def _write_state(self, data: dict) -> None:
        try:
            self._lease.replace(data)
        except (OSError, ValueError) as exc:  # pragma: no cover
            logger.warning("could not write the desktop grant state: %s", exc)

    @property
    def revoke_epoch(self) -> int:
        return self._lease.snapshot().revoke_epoch

    def current_token(self) -> LeaseToken | None:
        return self._lease.snapshot().token()

    def last_token(self) -> LeaseToken | None:
        return self._call_token.get()

    def unlocked_until(self) -> float:
        return self._lease.snapshot().until

    def remaining_seconds(self) -> int:
        return max(0, int(self.unlocked_until() - time.time()))

    def is_unlocked(self) -> bool:
        return self.remaining_seconds() > 0

    def hard_until(self) -> float:
        """Sert tavan: `until` kaysa da bunun otesine gecemez.

        Eski bicimli bir durum dosyasinda (yalnizca `until`) 0 doner.
        """
        return self._lease.snapshot().hard_until

    def hard_remaining_seconds(self) -> int:
        return max(0, int(self.hard_until() - time.time()))

    def unlock(
        self,
        minutes: int | None = None,
        reason: str = "",
        granted_by: str = "desktop_unlock",
    ) -> str:
        # Yalnizca None "varsayilani kullan" demektir. Verilen 0 ya da negatif
        # bir deger sessizce 15 dakikaya donmemeli -- istenenden UZUN izin
        # vermek, kisa vermekten kotu.
        mins = self.spec.unlock_default_minutes if minutes is None else int(minutes)
        mins = max(1, min(mins, self.spec.unlock_max_minutes))
        granted = time.time()
        until = granted + mins * 60
        # `until` TAVANDAN basliyor, `now + unlock_idle_seconds`ten degil:
        # unlock'tan sonra hic eylem gelmezse eski davranis aynen korunsun.
        # Kayma ILK EYLEMLE basliyor (`touch`).
        #
        # `granted_by` izni KIMIN actigini soyluyor. Bugun tek deger
        # "desktop_unlock", cunku `gate.unlock()`u baska cagiran yok --
        # `computer_batch`/`computer_task` izin acmiyor, acilmis izni
        # kullaniyor. Alan yine de yaziliyor: denetim kaydinda ve
        # `status_line`da anlami var, ve bir gun bir arac kendi izni acarsa
        # "acan kapatir" kurali icin gereken bilgi hazir olur.
        snapshot = self._lease.grant(
            until=until,
            reason=reason,
            granted=granted,
            granted_by=granted_by,
        )
        self._call_token.set(snapshot.token(granted))
        self.audit("desktop_unlock", minutes=mins, reason=reason or None,
                   granted_by=granted_by)
        msg = (
            f"Desktop control granted for {mins} minutes "
            f"(until {time.strftime('%H:%M', time.localtime(until))})."
        )
        idle = int(getattr(self.spec, "unlock_idle_seconds", 0) or 0)
        if idle > 0:
            msg += (
                f" It closes by itself {idle} seconds after the last desktop "
                f"action; {mins} minutes is the hard ceiling."
            )
        return msg

    def lock(self) -> str:
        snapshot, was_remaining = self._lease.revoke()
        self._call_token.set(None)
        self.audit(
            "desktop_lock",
            was_remaining=was_remaining,
            revoke_epoch=snapshot.revoke_epoch,
        )
        return (
            "Desktop control closed."
            if was_remaining
            else "Desktop control was already closed."
        )

    def touch(self, token: LeaseToken | None = None) -> bool:
        """Kayan kira: izni son eylemden `unlock_idle_seconds` sonrasina cek.

        NEDEN SON TARIH DEGIL SON EYLEM: ajanin "isim bitti" diye bir olayi
        yok -- son arac cagrisindan sonra ne oldugunu bilmiyor, o yuzden
        `desktop_lock`u unutmasi dikkatsizlik degil YAPISAL. Sabit son tarihte
        izin (ve ekran kenarindaki cerceve) dakikalarca acik kaliyordu.

        Dort sey YAPILMAZ, hepsi bilincli:
          * `unlock_idle_seconds = 0` ise hicbir sey -- eski davranis.
          * `hard_until` yoksa hicbir sey. Diskte bu alani icermeyen ESKI
            bir dosya olabilir; onu kaymis gibi yorumlamak izni sessizce
            kisaltirdi.
          * `hard_until` gecmisteyse hicbir sey. Sert tavan asilmaz.
          * `until` gecmisteyse hicbir sey. Olmus bir izin DIRILTILMEZ.

        Cagiran: yalnizca izin VERILEN `check()` (reddedilen cagri kirayi
        uzatmamali) ve `computer_task` kalp atisi.
        """
        idle = int(getattr(self.spec, "unlock_idle_seconds", 0) or 0)
        captured = token if token is not None else self.current_token()
        if captured is None:
            return False
        return self._lease.touch(captured, idle_seconds=idle)

    # ------------------------------------------------------------- hiz siniri
    def _rate_ok(self) -> bool:
        limit = self.spec.max_actions_per_second
        if limit <= 0:
            return True
        now = time.monotonic()
        events = self._events.setdefault(lambda: deque(maxlen=200))
        while events and now - events[0] > 1.0:
            events.popleft()
        if len(events) >= limit:
            return False
        events.append(now)
        return True

    # ------------------------------------------------------------------ kapi
    def _disabled_decision(self) -> Decision:
        return Decision(
            False,
            "Desktop control is disabled. To enable it, set "
            "`[desktop] enabled = true` in the pcbridge config and restart pcbridge "
            "(`pcbridge update`). (It is off by default on purpose: it means access "
            "to every application in your session.)",
            code=ErrorCode.DESKTOP_DISABLED,
            permission_scope="pcbridge.desktop",
            suggested_action="Enable desktop control in config.toml and restart pcbridge.",
        )

    def _grant_required_decision(self) -> Decision:
        return Decision(
            False,
            "Desktop control is locked right now. Grant time-limited access "
            f"with desktop_unlock first (default {self.spec.unlock_default_minutes} minutes).",
            code=ErrorCode.GRANT_REQUIRED,
            permission_scope="pcbridge.desktop",
            retryable=True,
            suggested_action="Call desktop_unlock before using desktop tools.",
        )

    def check(self, tool: str, write: bool = True, force: bool = False) -> Decision:
        """GUI araci calisabilir mi? Reddin gerekcesi kullaniciya aynen doner."""
        self._call_token.set(None)
        if not self.spec.enabled:
            return self._disabled_decision()

        lock_decision = screen_lock_decision(self._state_provider.screen_lock())
        if not lock_decision.allowed:
            return lock_decision

        token = self.current_token()
        if token is None:
            return self._grant_required_decision()

        if write and not force:
            activity = self._state_provider.user_activity()
            if activity.state == ActivityState.UNKNOWN:
                return Decision(
                    False,
                    "User activity could not be read. A write action only starts when "
                    "the activity state is known or force=true is given explicitly.",
                    code=ErrorCode.ACTIVITY_UNKNOWN,
                    permission_scope="pcbridge.desktop",
                    retryable=True,
                    suggested_action=(
                        "Restore the activity monitor or retry with explicit force."
                    ),
                )
            idle = activity.idle_ms
            assert idle is not None
            guard = self.spec.idle_guard_seconds * 1000
            if idle < guard:
                return Decision(
                    False,
                    f"Someone is at the machine (keyboard/pointer used {idle // 1000} "
                    "seconds ago). Refused so that remote actions and the user's own "
                    "mouse do not fight. To send it anyway, "
                    "pass force=true.",
                    code=ErrorCode.USER_ACTIVE,
                    permission_scope="pcbridge.desktop",
                    retryable=True,
                    suggested_action=(
                        "Wait for the user to become idle or retry with explicit force."
                    ),
                )

        if not self._rate_ok():
            return Decision(
                False,
                f"Rate limit: at most {self.spec.max_actions_per_second} actions "
                "per second. Try again in the next second.",
                code=ErrorCode.RATE_LIMITED,
                permission_scope="pcbridge.desktop",
                retryable=True,
                suggested_action="Retry after one second.",
            )

        # Kayan kira YALNIZCA burada damgalaniyor: bes katin hepsinden gecmis,
        # yani fiilen calisacak bir cagri. Yukaridaki her `return Decision(False)`
        # damgalamadan cikiyor -- reddedilen bir cagri izni uzatmamali.
        if not self.touch(token):
            return Decision(
                False,
                "Desktop control was closed during this call. "
                "Nothing starts without a new desktop_unlock grant.",
                code=ErrorCode.GRANT_REQUIRED,
                permission_scope="pcbridge.desktop",
                retryable=True,
                suggested_action="Call desktop_unlock before using desktop tools.",
            )
        self._call_token.set(token)
        return Decision(True)

    def verify(self, token: LeaseToken | None) -> Decision:
        """Re-check an admitted write sequence before one more action.

        `check()` admits a call; this answers whether that admission still
        holds: desktop enabled, screen unlocked, and the SAME grant still
        active. A `desktop_lock` (revoke epoch) or a new grant (grant id) ends
        it. Passing slides the lease, like any other action.

        Two layers of `check()` are deliberately not repeated. Activity: our
        own uinput events reset `IdleMonitor` (measured 104227 ms -> 151 ms),
        so the next action would see the previous one as the user. Rate: the
        sequence paces itself under the execution lock (`execution.py`), and
        counting here as well would make a batch throttle itself.

        Measured 2026-09-13 on this machine: the screen-lock query p50 2.9 ms,
        the lease touch p50 0.03 ms (12 ms when it writes), against >= 30 ms for
        the cheapest action.
        """
        if not self.spec.enabled:
            return self._disabled_decision()
        lock_decision = screen_lock_decision(self._state_provider.screen_lock())
        if not lock_decision.allowed:
            return lock_decision
        if token is None:
            return self._grant_required_decision()
        if self.touch(token):
            return Decision(True)

        current = self._lease.snapshot()
        if (current.grant_id, current.revoke_epoch) == (token.grant_id, token.revoke_epoch):
            return Decision(
                False,
                "The desktop grant expired while this sequence was running; the "
                "remaining actions were not sent. To continue, grant access again with "
                "desktop_unlock, then read the screen again.",
                code=ErrorCode.GRANT_EXPIRED,
                permission_scope="pcbridge.desktop",
                retryable=True,
                suggested_action=(
                    "Call desktop_unlock again, re-read the screen, then send the "
                    "remaining actions."
                ),
            )
        if current.is_active():
            return Decision(
                False,
                "Desktop access was granted anew while this sequence was running; "
                "the sequence that started under the old grant was stopped and the rest "
                "was not sent. Read the screen again and send the rest in a new call.",
                code=ErrorCode.REVOKED,
                permission_scope="pcbridge.desktop",
                retryable=True,
                suggested_action=(
                    "Re-read the screen and send the remaining actions in a new call."
                ),
            )
        return Decision(
            False,
            "The desktop grant was closed while this sequence was running (desktop_lock); "
            "the remaining actions were not sent. Do not continue until the user "
            "grants access again.",
            code=ErrorCode.REVOKED,
            permission_scope="pcbridge.desktop",
            retryable=False,
            suggested_action=(
                "Stop here; continue only after the user grants desktop_unlock again."
            ),
        )

    # ---------------------------------------------------------- denetim kaydi
    def audit(self, event: str, **fields: Any) -> None:
        """auth.py'daki `audit()` ile ayni bicim: audit.log'a tek satir JSON.

        Masaustune ozel DEGIL: kabuk, ajan ve dosya araclari da buraya yaziyor.
        Kural her yerde ayni -- ne yapildigi kaydedilir, ICERIK kaydedilmez
        (komut evet ciktisi hayir, dosya yolu evet icerigi hayir, metin
        uzunlugu evet metnin kendisi hayir). Denetim kaydini okuyabilen birinin
        parolalari da okuyabilmesi anlamsiz bir yetki genislemesi olurdu.
        """
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "event": event,
            **{k: v for k, v in fields.items() if v is not None},
        }
        # Which client asked, when the daemon knows (from the relay preamble).
        client = sessionctx.client_name()
        if client and "client" not in rec:
            rec["client"] = client
        line = json.dumps(rec, ensure_ascii=False)
        logger.info(line)
        try:
            path = Path(self.cfg.audit_log)
            self._rotate(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:  # pragma: no cover
            pass

    def _rotate(self, path: Path) -> None:
        """Dosya buyudugunde `.1`'e devret. Tek yedek: bu bir denetim kaydi,
        arsiv degil; sinirsiz buyumesi diski doldurur."""
        limit = int(getattr(self.cfg, "audit_max_bytes", 0) or 0)
        if limit <= 0:
            return
        try:
            if path.exists() and path.stat().st_size >= limit:
                path.replace(path.with_suffix(path.suffix + ".1"))
        except OSError:  # pragma: no cover
            pass

    # -------------------------------------------------------------- durum ozet
    def status_line(self) -> str:
        """`system_status` ve arac ciktilari icin tek satirlik ozet."""
        if not self.spec.enabled:
            return "desktop control: disabled (config.toml → [desktop] enabled)"
        rem = self.remaining_seconds()
        if rem <= 0:
            return "desktop control: enabled but locked (waiting for desktop_unlock)"
        lock = self._state_provider.screen_lock().state
        extra = (
            " · SCREEN LOCKED"
            if lock == ScreenLockState.KNOWN_LOCKED
            else " · SCREEN STATE UNKNOWN"
            if lock == ScreenLockState.UNKNOWN
            else ""
        )
        # Kayan kira acikken TEK bir sayi yaniltici olurdu: "1 dk 30 sn kaldi"
        # goren kullanici izni 15 dakika actigini hatirlayip kafasi karisir.
        # Iki sayi birden: kayan kalan ve onun tavani.
        tavan = ""
        hard = self.hard_remaining_seconds()
        if hard > rem:
            tavan = f" · hard ceiling {hard // 60} min {hard % 60} s"
        return (
            f"desktop control: granted, {rem // 60} min {rem % 60} s left"
            f"{tavan}{extra}"
        )
