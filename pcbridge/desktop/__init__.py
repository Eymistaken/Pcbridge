"""Masaustu kontrolu — klavye, fare, monitorler ve guvenlik kapisi.

Bu paket `[desktop] enabled = true` olmadan hicbir sey yapmaz; kapi
`safety.SafetyGate`. Alt moduller:

    monitors.py  monitor tablosu, koordinat uzayi, `monitor=` cozumleme
    input.py     the typing orchestration above the native helper's devices
    safety.py    sureli izin, ekran kilidi/idle kontrolu, hiz siniri, denetim
    contracts.py provider arayuzleri
    capabilities.py yan etkisiz yetenek snapshot'lari ve evidence cache'i
    errors.py    kararli, makine tarafindan okunabilir hata taxonomy'si
    runtime.py   provider kaynak sahipligi ve yasam dongusu

The keyboard, the pointer, capture and accessibility go through the native
helper (`backends/rust.py`). Without it only the desktop tools refuse, with
the reason and the fix; the rest of pcbridge (agents, tmux, shell, files) is
unaffected.
"""

from __future__ import annotations
