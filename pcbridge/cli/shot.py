"""`pcb-shot` — ekrani PNG olarak diske yazar, yolunu ve OFSETINI soyler.

`screen_capture` MCP aracindan farki tek cumleyle: o goruntu **telefondaki
kullanici** icin kisa omurlu bir baglantiya donusuyor, bu ise **makinedeki
ajanin gozu** icin dosya olarak duruyor. Ajan `Read` ile dogrudan aciyor.

TAM COZUNURLUK VARSAYILAN
    `screen_capture` 1280'e kuculuyor (telefon ekrani, veri tasarrufu). Burada
    varsayilan 0 = hic olcekleme. Sebebi olculdu (C bolumu): tam cozunurlukte
    goruntudeki piksel -> global koordinat donusumunun sapmasi ~1 px, 1280'e
    kuculmusde ~5 px. Olcek 1:1 oldugunda donusum `ofset + piksel` haline
    geliyor ve olcek aritmetigi hatasi diye bir SINIF ortadan kalkiyor. Ajanin
    yapacagi tek hesap toplama.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from . import (
    EXIT_BAD_INPUT,
    EXIT_OK,
    check_gate,
    fail,
    gate_of,
    job_id,
    load,
    shot_dir,
)


def sweep(directory: Path, keep_hours: int) -> int:
    """Eskimis PNG'leri sil. Disk temizligi; ajan her tur yeni goruntu uretiyor."""
    if keep_hours <= 0:
        return 0
    cutoff = time.time() - keep_hours * 3600
    removed = 0
    for png in directory.glob("*.png"):
        try:
            if png.stat().st_mtime < cutoff:
                png.unlink()
                removed += 1
        except OSError:  # pragma: no cover
            pass
    return removed


def describe(shots, mons) -> list[str]:
    """Ajanin okuyacagi metin. Ofset ve donusum kurali BURADA yaziyor.

    Bu bilgi kaybolursa ikinci monitore yapilan her tiklama 1920 piksel sasar
    ve hata hicbir yerde gorunmez.
    """
    out: list[str] = []
    for s in shots:
        if s.offset is None:
            out.append(
                f"{s.path}\n"
                f"  odaktaki pencere · {s.scaled[0]}x{s.scaled[1]}\n"
                "  ⚠️  Bu goruntunun ekranda NEREDE oldugu bilinmiyor; "
                "buradan koordinat turetme."
            )
            continue
        line = (
            f"{s.path}\n"
            f"  monitor {s.monitor.index} ({s.monitor.connector}"
            f"{', birincil' if s.monitor.primary else ''}) · "
            f"{s.size[0]}x{s.size[1]} @ ofset ({s.offset[0]}, {s.offset[1]})"
        )
        if s.scale == 1.0:
            line += (
                "\n  olcek 1:1 -> global_x = "
                f"{s.offset[0]} + goruntu_x , global_y = {s.offset[1]} + goruntu_y"
            )
        else:
            line += (
                f" -> {s.scaled[0]}x{s.scaled[1]} (olcek {s.scale:.3f})"
                f"\n  global_x = {s.offset[0]} + goruntu_x / {s.scale:.3f} "
                f", global_y = {s.offset[1]} + goruntu_y / {s.scale:.3f}"
            )
        out.append(line)

    # Zaman damgasi sus degil: goruntu BAYATLAR. Aradan gecen surede kullanici
    # baska pencereye gecmis olabilir ve o koordinat artik baska seyin ustunde.
    # 2026-08-03'te tam bu oldu -- 69 saniyelik bir goruntuye gore tiklandi,
    # tiklama baska uygulamaya dustu. `pcb-do` artik yasa bakiyor.
    out.append(f"\nalindi: {time.strftime('%H:%M:%S')}")

    primary = next((m for m in mons if m.primary), None)
    if primary is not None:
        # UYGULAMA.md bunu acikca istiyor: bilmeyen surucu `Super`'a basip
        # yanlis ekranda menu arar ve "calismadi" saniyor.
        out.append(
            f"\nGNOME ust cubugu ve `Super` menusu monitor {primary.index} "
            f"({primary.connector}, birincil) uzerinde beliriyor."
        )
    out.append(
        "Koordinatlar `pcb-do`'ya GLOBAL verilir; `monitor` parametresi verme."
    )
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pcb-shot",
        description="Ekrani PNG olarak yaz ve global ofsetini soyle.",
    )
    p.add_argument(
        "--monitor", default="all",
        help="all (varsayilan, her monitor ayri), 1/2, DP-1, primary, window",
    )
    p.add_argument(
        "--scale", type=int, default=0,
        help="Kirpma sonrasi uzun kenar. 0 (varsayilan) = TAM COZUNURLUK.",
    )
    p.add_argument("--no-pointer", action="store_true",
                   help="Imleci goruntuye cizme.")
    p.add_argument("--out", default="",
                   help="PNG dizini (varsayilan: $XDG_RUNTIME_DIR/pcbridge/shots)")
    p.add_argument("--json", action="store_true", help="Makine okunur cikti.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.scale and args.scale < 320:
        fail("--scale ya 0 (tam cozunurluk) ya da en az 320 olmali",
             EXIT_BAD_INPUT, args.json)

    cfg = load()
    gate = gate_of(cfg)
    # Ekran goruntusu bir YAZMA eylemi degil: "yakinda klavye kullanildi"
    # korumasina takilmiyor, ama izin penceresi ve ekran kilidi aynen gecerli.
    check_gate(cfg, gate, "pcb_shot", write=False, needs_input=False)

    from ..desktop import capture as capturelib
    from ..desktop import monitors as monitorslib

    ok, why = capturelib.available()
    if not ok:
        gate.audit("pcb_shot_unavailable", reason=why[:120], job=job_id())
        fail(f"Ekran goruntusu alinamiyor: {why}", EXIT_BAD_INPUT, args.json)

    out_dir = Path(args.out).expanduser() if args.out else shot_dir(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    swept = sweep(out_dir, cfg.desktop.shot_keep_hours)

    spec: int | str = str(args.monitor).strip()
    if spec.isdigit():
        spec = int(spec)

    try:
        shots = capturelib.capture(
            spec,
            out_dir=out_dir,
            scale_long_edge=max(0, args.scale),
            include_pointer=not args.no_pointer and cfg.desktop.include_pointer,
        )
        mons = monitorslib.list_monitors()
    except (capturelib.CaptureError, monitorslib.MonitorError) as exc:
        gate.audit("pcb_shot_error", error=str(exc)[:160], job=job_id())
        fail(str(exc), EXIT_BAD_INPUT, args.json)

    gate.audit("pcb_shot", monitor=str(args.monitor), shots=len(shots),
               swept=swept or None, job=job_id())

    if args.json:
        print(json.dumps({
            "ok": True,
            "shots": [
                {
                    "path": str(s.path),
                    "monitor": None if s.monitor is None else s.monitor.index,
                    "connector": None if s.monitor is None else s.monitor.connector,
                    "primary": None if s.monitor is None else s.monitor.primary,
                    "offset": list(s.offset) if s.offset else None,
                    "size": list(s.size),
                    "scaled": list(s.scaled),
                    "scale": s.scale,
                }
                for s in shots
            ],
            "primary_monitor": next(
                (m.index for m in mons if m.primary), None
            ),
        }, ensure_ascii=False, indent=2))
    else:
        print("\n".join(describe(shots, mons)))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
