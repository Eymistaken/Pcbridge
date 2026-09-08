"""Masaustu iznini kapat VE ekran yayinini durdur.

Servis dururken `ExecStopPost` bunu cagiriyor; kullanici `bridgekilit` ile
elle de cagiriyor.

Neden gerekli: izin durumu DISKTE (`state_dir/desktop_unlock.json`) ve
sunucudan bagimsiz calisan `bin/pcb-do` de oradan okuyor. pcbridge oldukten
sonra diskte kalmis acik bir izin, elle calistirilan bir `pcb-do`'yu hala
yetkilendirirdi.

YAYIN DA KAPATILIYOR (2026-09-06'da eklendi, kullanici fark etti)
    Izin dosyasini kapatmak yetmiyordu: yayin `screencast_helper.py`
    surecinde yasiyor ve tutamagi ONU ACAN surecin belleginde. `cli.lock`
    ayri bir surec, o nesneye ulasamiyor. Sonuc: izin kapali ama ust
    cubuktaki PAYLASIM GOSTERGESI duruyordu. Gosterge "ajan ekranini
    gorebiliyor" demek; acil kapatmadan sonra durmasi ya erisimin surdugu ya
    da gostergenin yalan soyledigi anlamina gelir -- ikisi de kabul edilemez.

Bu, acil durdurmanin YERINE gecmiyor: olculdu 2026-08-02, `systemctl --user
stop` calisan ajan islerini de olduruyor (isler servisin cgroup'unda kaliyor,
`KillMode=control-group`). Burasi ikinci kat.

Elle de calistirilabilir:  ./.venv/bin/python -m pcbridge.cli.lock
"""

from __future__ import annotations

import sys

from . import EXIT_OK, load, runtime_of


def main(argv: list[str] | None = None) -> int:
    cfg = load()
    runtime = runtime_of(cfg)
    try:
        out = runtime.gate.lock()

        # Yayin, izinden AYRI bir kaynak: hangi surec acmis olursa olsun
        # provider kendi helper taramasiyla onu da durdurur.
        killed = runtime.capture_provider.kill_helpers()
        if killed:
            out += (f"\n· {killed} ekran yayini durduruldu "
                    "(paylasim gostergesi kayboldu)")
        print(out)
        return EXIT_OK
    finally:
        runtime.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
