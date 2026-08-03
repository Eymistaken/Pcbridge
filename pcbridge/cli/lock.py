"""Masaustu iznini kapat. Servis dururken `ExecStopPost` bunu cagiriyor.

Neden gerekli: izin durumu DISKTE (`state_dir/desktop_unlock.json`) ve
sunucudan bagimsiz calisan `bin/pcb-do` de oradan okuyor. pcbridge oldukten
sonra diskte kalmis acik bir izin, elle calistirilan bir `pcb-do`'yu hala
yetkilendirirdi.

Bu, acil durdurmanin YERINE gecmiyor: olculdu 2026-08-02, `systemctl --user
stop` calisan ajan islerini de olduruyor (isler servisin cgroup'unda kaliyor,
`KillMode=control-group`). Burasi ikinci kat.

Elle de calistirilabilir:  ./.venv/bin/python -m pcbridge.cli.lock
"""

from __future__ import annotations

import sys

from . import EXIT_OK, gate_of, load


def main(argv: list[str] | None = None) -> int:
    cfg = load()
    gate = gate_of(cfg)
    print(gate.lock())
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
