"""Masaustu kontrolu — klavye, fare, monitorler ve guvenlik kapisi.

Bu paket `[desktop] enabled = true` olmadan hicbir sey yapmaz; kapi
`safety.SafetyGate`. Alt moduller:

    monitors.py  monitor tablosu, koordinat uzayi, `monitor=` cozumleme
    input.py     /dev/uinput uzerinden sanal klavye + mutlak fare
    safety.py    sureli izin, ekran kilidi/idle kontrolu, hiz siniri, denetim

`evdev` kurulu degilse yalnizca girdi araclari devre disi kalir; pcbridge'in
geri kalani (ajan, tmux, kabuk, dosya) etkilenmez.
"""

from __future__ import annotations
