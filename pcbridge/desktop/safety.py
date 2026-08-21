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
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("pcbridge.desktop")

STATE_FILE = "desktop_unlock.json"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""

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
        self._events: deque[float] = deque(maxlen=200)

    # ------------------------------------------------------------- izin durumu
    def _read_state(self) -> dict:
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write_state(self, data: dict) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:  # pragma: no cover
            logger.warning("desktop izin durumu yazilamadi: %s", exc)

    def unlocked_until(self) -> float:
        return float(self._read_state().get("until", 0) or 0)

    def remaining_seconds(self) -> int:
        return max(0, int(self.unlocked_until() - time.time()))

    def is_unlocked(self) -> bool:
        return self.remaining_seconds() > 0

    def hard_until(self) -> float:
        """Sert tavan: `until` kaysa da bunun otesine gecemez.

        Eski bicimli bir durum dosyasinda (yalnizca `until`) 0 doner.
        """
        return float(self._read_state().get("hard_until", 0) or 0)

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
        until = time.time() + mins * 60
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
        self._write_state({
            "until": until,
            "hard_until": until,
            "reason": reason,
            "granted": time.time(),
            "granted_by": granted_by,
        })
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
        was = self.remaining_seconds()
        self._write_state({"until": 0, "hard_until": 0})
        self.audit("desktop_lock", was_remaining=was)
        return (
            "Masaustu kontrolu kapatildi."
            if was
            else "Masaustu kontrolu zaten kapaliydi."
        )

    def touch(self) -> None:
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
        if idle <= 0:
            return
        # Oku-degistir-yaz SART: `_write_state` dosyayi komple uzerine
        # yaziyor, sozluk yeniden kurulursa `hard_until` ilk eylemde kaybolur.
        st = self._read_state()
        hard = float(st.get("hard_until", 0) or 0)
        if hard <= 0:
            return
        now = time.time()
        if hard <= now:
            return
        until = float(st.get("until", 0) or 0)
        if until <= now:
            return
        yeni = min(hard, now + idle)
        # Saniyenin altindaki degisiklikler yazilmiyor: hiz siniri saniyede 10
        # eyleme izin veriyor ve her biri diske yazsaydi eklentinin dosya
        # izleyicisi bosuna calisirdi. Fark BIRIKIYOR, yani kira gerilemiyor.
        if abs(yeni - until) < 1.0:
            return
        st["until"] = yeni
        self._write_state(st)

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
        if not self.spec.enabled:
            return Decision(
                False,
                "Masaustu kontrolu kapali. Acmak icin config.toml'da "
                "`[desktop] enabled = true` yapip `systemctl --user restart pcbridge` "
                "calistirin. (Varsayilan kapali olmasi bilincli: bu ozellik acik "
                "oturumunuzdaki her uygulamaya erisim demek.)",
            )

        locked = screen_locked()
        if locked:
            return Decision(
                False,
                "Ekran kilitli. Kilitli ekranin arkasina girdi gonderilmez — "
                "makinenin basina gecip kilidi acin.",
            )

        if not self.is_unlocked():
            return Decision(
                False,
                "Masaustu kontrolu su an kilitli. Once desktop_unlock ile "
                f"sureli izin verin (varsayilan {self.spec.unlock_default_minutes} dakika).",
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
                )

        if not self._rate_ok():
            return Decision(
                False,
                f"Hiz siniri: saniyede en fazla {self.spec.max_actions_per_second} "
                "eylem. Bir sonraki saniyede tekrar deneyin.",
            )

        # Kayan kira YALNIZCA burada damgalaniyor: bes katin hepsinden gecmis,
        # yani fiilen calisacak bir cagri. Yukaridaki her `return Decision(False)`
        # damgalamadan cikiyor -- reddedilen bir cagri izni uzatmamali.
        self.touch()
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
