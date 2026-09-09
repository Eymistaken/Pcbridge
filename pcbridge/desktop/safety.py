"""Guvenlik kapisi — GUI araclarinin gecmek zorunda oldugu tek nokta.

`UYGULAMA.md`: "Bu modul olmadan girdi araclarini yayina alma."

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ErrorCategory, ErrorCode
from .lease import LEASE_STATE_FILE, LeaseStore, LeaseToken

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
    except Exception:  # noqa: BLE001 — D-Bus yoksa kapiyi kapatmiyoruz, bilmiyoruz
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
    return int(val) if isinstance(val, int) else None


class SafetyGate:
    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self.spec = cfg.desktop
        self._state_path = Path(cfg.state_dir) / STATE_FILE
        self._lease = LeaseStore(cfg.state_dir)
        self._local = threading.local()
        self._events: deque[float] = deque(maxlen=200)

    # ------------------------------------------------------------- izin durumu
    def _read_state(self) -> dict:
        return self._lease.read()

    def _write_state(self, data: dict) -> None:
        try:
            self._lease.replace(data)
        except (OSError, ValueError) as exc:  # pragma: no cover
            logger.warning("desktop izin durumu yazilamadi: %s", exc)

    @property
    def revoke_epoch(self) -> int:
        return self._lease.snapshot().revoke_epoch

    def current_token(self) -> LeaseToken | None:
        return self._lease.snapshot().token()

    def last_token(self) -> LeaseToken | None:
        return getattr(self._local, "token", None)

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
        self._local.token = snapshot.token(granted)
        self.audit("desktop_unlock", minutes=mins, reason=reason or None,
                   granted_by=granted_by)
        msg = (
            f"Masaustu kontrolu {mins} dakika acildi "
            f"(bitis {time.strftime('%H:%M', time.localtime(until))})."
        )
        idle = int(getattr(self.spec, "unlock_idle_seconds", 0) or 0)
        if idle > 0:
            msg += (
                f" Son masaustu eyleminden {idle} saniye sonra kendiliginden "
                f"dusuyor; {mins} dakika bunun sert tavani."
            )
        return msg

    def lock(self) -> str:
        snapshot, was_remaining = self._lease.revoke()
        self._local.token = None
        self.audit(
            "desktop_lock",
            was_remaining=was_remaining,
            revoke_epoch=snapshot.revoke_epoch,
        )
        return (
            "Masaustu kontrolu kapatildi."
            if was_remaining
            else "Masaustu kontrolu zaten kapaliydi."
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
        while self._events and now - self._events[0] > 1.0:
            self._events.popleft()
        if len(self._events) >= limit:
            return False
        self._events.append(now)
        return True

    # ------------------------------------------------------------------ kapi
    def check(self, tool: str, write: bool = True, force: bool = False) -> Decision:
        """GUI araci calisabilir mi? Reddin gerekcesi kullaniciya aynen doner."""
        self._local.token = None
        if not self.spec.enabled:
            return Decision(
                False,
                "Masaustu kontrolu kapali. Acmak icin config.toml'da "
                "`[desktop] enabled = true` yapip `systemctl --user restart pcbridge` "
                "calistirin. (Varsayilan kapali olmasi bilincli: bu ozellik acik "
                "oturumunuzdaki her uygulamaya erisim demek.)",
                code=ErrorCode.DESKTOP_DISABLED,
                permission_scope="pcbridge.desktop",
                suggested_action="Enable desktop control in config.toml and restart pcbridge.",
            )

        locked = screen_locked()
        if locked:
            return Decision(
                False,
                "Ekran kilitli. Kilitli ekranin arkasina girdi gonderilmez — "
                "makinenin basina gecip kilidi acin.",
                code=ErrorCode.SCREEN_LOCKED,
                permission_scope="pcbridge.desktop",
                retryable=True,
                suggested_action="Unlock the local desktop session and retry.",
            )

        token = self.current_token()
        if token is None:
            return Decision(
                False,
                "Masaustu kontrolu su an kilitli. Once desktop_unlock ile "
                f"sureli izin verin (varsayilan {self.spec.unlock_default_minutes} dakika).",
                code=ErrorCode.GRANT_REQUIRED,
                permission_scope="pcbridge.desktop",
                retryable=True,
                suggested_action="Call desktop_unlock before using desktop tools.",
            )

        if write and not force:
            idle = idle_ms()
            guard = self.spec.idle_guard_seconds * 1000
            if idle is not None and idle < guard:
                return Decision(
                    False,
                    f"Makinenin basinda birisi var ({idle // 1000} saniye once "
                    "klavye/fare kullanildi). Telefondan gelen eylemle sizin "
                    "farenizin kavga etmemesi icin reddedildi. Yine de gonderilsin "
                    "isterseniz force=true verin.",
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
                f"Hiz siniri: saniyede en fazla {self.spec.max_actions_per_second} "
                "eylem. Bir sonraki saniyede tekrar deneyin.",
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
                "Masaustu kontrol izni bu cagri sirasinda kapatildi. "
                "Yeni bir desktop_unlock izni olmadan islem baslatilmaz.",
                code=ErrorCode.GRANT_REQUIRED,
                permission_scope="pcbridge.desktop",
                retryable=True,
                suggested_action="Call desktop_unlock before using desktop tools.",
            )
        self._local.token = token
        return Decision(True)

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
            return "masaustu kontrolu: kapali (config.toml → [desktop] enabled)"
        rem = self.remaining_seconds()
        if rem <= 0:
            return "masaustu kontrolu: acik ama kilitli (desktop_unlock bekliyor)"
        locked = screen_locked()
        extra = " · EKRAN KILITLI" if locked else ""
        # Kayan kira acikken TEK bir sayi yaniltici olurdu: "1 dk 30 sn kaldi"
        # goren kullanici izni 15 dakika actigini hatirlayip kafasi karisir.
        # Iki sayi birden: kayan kalan ve onun tavani.
        tavan = ""
        hard = self.hard_remaining_seconds()
        if hard > rem:
            tavan = f" · sert tavan {hard // 60} dk {hard % 60} sn"
        return (
            f"masaustu kontrolu: izinli, {rem // 60} dk {rem % 60} sn kaldi"
            f"{tavan}{extra}"
        )
