"""Masaustu iznini kapat VE ekran yayinini durdur.

Servis dururken `ExecStopPost` bunu cagiriyor; kullanici `bridgekilit` ile
elle de cagiriyor.

Neden gerekli: izin durumu DISKTE (`state_dir/desktop_unlock.json`) ve
sunucudan bagimsiz calisan `bin/pcb-do` de oradan okuyor. pcbridge oldukten
sonra diskte kalmis acik bir izin, elle calistirilan bir `pcb-do`'yu hala
yetkilendirirdi.

THE SHARE CLOSES TOO
    The screen share lives in the native helper of the process that opened
    it, out of reach of `cli.lock`. Every native helper watches the grant
    and ends its session when the revoke epoch changes (`native_revoke.rs`),
    so closing the grant closes every share, and the panel's sharing
    indicator goes with it: the indicator means "an agent can see the
    screen", so it must not outlive the grant. (Until the Python screen
    share was removed, this module also had to find and stop its helper
    processes by path; the user noticed the leftover indicator, 2026-09-06.)

Bu, acil durdurmanin YERINE gecmiyor: olculdu 2026-08-02, `systemctl --user
stop` calisan ajan islerini de olduruyor (isler servisin cgroup'unda kaliyor,
`KillMode=control-group`). Burasi ikinci kat.

Elle de calistirilabilir:  ./.venv/bin/python -m pcbridge.cli.lock
"""

from __future__ import annotations

import sys

from . import EXIT_OK, load
from .grant import lock


def main(argv: list[str] | None = None) -> int:
    print(lock(load()))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
