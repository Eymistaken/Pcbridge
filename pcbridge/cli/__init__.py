"""`bin/pcb-shot` ve `bin/pcb-do` kabuklarinin ortak cekirdegi.

NEDEN AYRI BIR PAKET
    Bu iki kabuk MCP sunucusundan bagimsiz calisiyor: makinedeki gorsel ajan
    onlari Bash'ten cagiriyor (`pcb-shot` -> `Read` -> `pcb-do` -> `pcb-shot`).
    Ama mantik `bin/` altinda bir kabuk betigi olsaydi TEST EDILEMEZDI; bu
    projenin kurali her adimi fiilen test etmek. O yuzden mantik burada,
    `bin/*` yalnizca uc satirlik bir sarmalayici.

KAPI AYNI KAPI
    Izin durumu diskte tutuluyor (`state_dir/desktop_unlock.json`), yani ayri
    bir surec olan bu kabuklar da `SafetyGate`'in tam olarak aynisindan
    geciyor. Ikinci bir kapi yazilmiyor -- yazilsaydi ikisi zamanla ayrisir ve
    ayrisma en kotu yerde, yetkilendirmede olurdu.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent

# Cikis kodlari. Ajan bunlara Bash'ten bakip karar veriyor, o yuzden
# anlamlari SKILL.md'de de birebir yaziyor.
EXIT_OK = 0            # istenen her sey yapildi
EXIT_PARTIAL = 2       # bir kismi yapildi (butce doldu / odak kaydi / hata)
EXIT_DENIED = 3        # guvenlik kapisi reddetti
EXIT_BAD_INPUT = 4     # bozuk argüman ya da JSON


def load() -> Any:
    """`config.toml`'u yukle. Bulunamazsa net bir hata ile cik."""
    from ..config import load_config

    try:
        return load_config()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        fail(f"config.toml okunamadi: {exc}", EXIT_BAD_INPUT)


def gate_of(cfg: Any) -> Any:
    from ..desktop.safety import SafetyGate

    return SafetyGate(cfg)


def job_id() -> str | None:
    """Bizi baslatan `computer_task` isinin kimligi (jobs.py ortama koyuyor).

    Denetim kaydinin degeri buradan geliyor: ajanin her eylemi, onu
    yetkilendiren goreve geri baglanabiliyor. `agy` cikti ayristiricisi adim
    listesi vermedigi icin audit.log fiilen adim listesinin yerine geciyor.
    """
    return os.environ.get("PCBRIDGE_JOB_ID") or None


def task_force() -> bool:
    """`computer_task` gorevi baslatirken bosta (idle) kontrolunu yapti mi?

    uinput olayi `IdleMonitor`'u SIFIRLIYOR (olculdu: 104227 ms -> 151 ms).
    Yani ajanin ikinci eyleminden itibaren "kullanici makinede" gorunur ve
    kendi tusunu kullanici sanip reddedilir. Kontrol bu yuzden EYLEM basina
    degil GOREV basina yapiliyor: `computer_task` bir kez bakiyor ve isin
    ortamina bu degiskeni koyuyor.

    Elle terminalden calistirilan `pcb-do`'da bu degisken YOKTUR; orada tam
    koruma isler.
    """
    return os.environ.get("PCBRIDGE_TASK_FORCE") == "1"


def check_gate(cfg: Any, gate: Any, tool: str, *, write: bool,
               needs_input: bool, force: bool = False) -> None:
    """Kapidan gec ya da `EXIT_DENIED` ile cik.

    `needs_input=False`: sanal klavye/fare ARANMAZ. Ekran goruntusu uinput
    kullanmiyor; /dev/uinput yokken onu "girdi cihazi yok" diye reddetmek
    yanlis gerekce olurdu (C bolumunde bir kez yasandi).
    """
    decision = gate.check(tool, write=write, force=force)
    if not decision.allowed:
        gate.audit(f"{tool}_denied", reason=decision.reason[:120], job=job_id())
        fail(decision.reason, EXIT_DENIED)
    if needs_input:
        from ..desktop.input import InputBackend

        ok, why = InputBackend().available()
        if not ok:
            gate.audit(f"{tool}_unavailable", reason=why[:120], job=job_id())
            fail(f"Sanal girdi cihazi kullanilamiyor: {why}", EXIT_DENIED)


def shot_dir(cfg: Any) -> Path:
    """`pcb-shot`un PNG yazdigi dizin.

    Varsayilan `$XDG_RUNTIME_DIR/pcbridge/shots`. `UYGULAMA.md` `/tmp/pcb`
    diyor; sapma bilincli: /tmp herkese okunur (mod 775), $XDG_RUNTIME_DIR ise
    yalnizca kullaniciya acik (mod 700) ve oturum kapaninca siliniyor. Ekran
    goruntusu bu projenin en gizlilik-hassas ciktisi -- config.example.toml
    bunu kendisi yaziyor. $XDG_RUNTIME_DIR yoksa /tmp/pcb'ye dusuluyor.
    """
    configured = (cfg.desktop.agent_shot_dir or "").strip()
    if configured:
        base = Path(os.path.expandvars(os.path.expanduser(configured)))
    else:
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        base = Path(runtime) / "pcbridge" / "shots" if runtime else Path("/tmp/pcb")
    base.mkdir(parents=True, exist_ok=True)
    try:
        base.chmod(0o700)
    except OSError:  # pragma: no cover — sahibi biz degilsek dokunmuyoruz
        pass
    return base


def newest_shot_age(directory: Path, now: float | None = None) -> float | None:
    """En yeni PNG'nin kac saniye once alindigi. Hic goruntu yoksa None."""
    import time as _t

    newest = None
    for png in Path(directory).glob("*.png"):
        try:
            mtime = png.stat().st_mtime
        except OSError:  # pragma: no cover
            continue
        if newest is None or mtime > newest:
            newest = mtime
    if newest is None:
        return None
    return max(0.0, (now if now is not None else _t.time()) - newest)


def emit(text: str, payload: dict | None = None, as_json: bool = False) -> None:
    """Insan icin metin ya da makine icin JSON bas."""
    if as_json:
        print(json.dumps(payload or {}, ensure_ascii=False, indent=2))
    else:
        print(text)


def fail(message: str, code: int = EXIT_BAD_INPUT, as_json: bool = False) -> None:
    """Hata mesajini stderr'e yaz ve cik. Ajan hem metni hem kodu goruyor."""
    if as_json:
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False),
              file=sys.stderr)
    else:
        print(f"HATA: {message}", file=sys.stderr)
    sys.exit(code)
