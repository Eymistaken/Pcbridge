"""`pcb-shot` — ekrani PNG olarak diske yazar, yolunu ve OFSETINI soyler.

`screen_capture` MCP aracindan farki tek cumleyle: o goruntu **telefondaki
kullanici** icin kisa omurlu bir baglantiya donusuyor, bu ise **makinedeki
ajanin gozu** icin dosya olarak duruyor. Ajan `Read` ile dogrudan aciyor.

OLCEK TEK YERDEN: `[desktop] screenshot_scale_long_edge`
    `--scale` verilmezse `screen_capture`in kullandigi ayarin AYNISI
    kullaniliyor. Ajanin gordugu cozunurluk hangi yoldan bagli oldugna gore
    degismesin diye: MCP'den 1280, kabuktan 1920 gelmesi tek basina bir hata
    kaynagiydi (ayni ekran, iki farkli piksel uzayi).

    Burasi eskiden `0` (tam cozunurluk) idi ve gerekcesi suydu: "tam
    cozunurlukte donusumun sapmasi ~1 px, 1280'e kucultulmusde ~5 px; olcek
    1:1 oldugunda donusum `ofset + piksel` haline geliyor ve olcek aritmetigi
    hatasi diye bir SINIF ortadan kalkiyor." O gerekce ARTIK GECERSIZ: hesabi
    ajan yapmiyor, `capture.to_global()` yapiyor (cekim kimligi, `shot=`).
    Aritmetik hatasi sinifi modelden degil koddan kalkti.

    Kalan sapma kucultmenin KENDISINDEN geliyor (1 goruntu pikseli = 1,5 ekran
    pikseli) ve `--scale 0` ile hala sifirlanabiliyor.
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
    job_id,
    load,
    runtime_of,
    shot_dir,
)


def sweep(directory: Path, keep_hours: int) -> int:
    """Eskimis PNG'leri ve cekim kayitlarini sil.

    Disk temizligi; ajan her tur yeni goruntu uretiyor. `<id>.json` kayitlari
    da ayni yasa tabi: PNG'siz kalan bir kayit `shot=` ile bulunur ama arkasinda
    goruntu olmaz -- ajani olmayan bir goruntuye tiklatmaktansa kimligin de
    kaybolmasi dogru.

    Oldurulmus bir cekimin hazirlik dizini `keep_hours`tan bagimsiz gidiyor:
    saklanan bir cekim degil, icinde tam cozunurlukte bir goruntu olabilir.
    """
    from ..desktop import capture as capturelib

    removed = capturelib.sweep_staging(directory)
    if keep_hours <= 0:
        return removed
    cutoff = time.time() - keep_hours * 3600
    for f in (*directory.glob("*.png"), *directory.glob("*.json")):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:  # pragma: no cover
            pass
    return removed


def describe(shots, mons, capture_provider=None) -> list[str]:
    """Ajanin okuyacagi metin. Her goruntunun KIMLIGI burada yaziyor.

    Eskiden burada donusum FORMULU yaziyordu ve ajan bolmeyi kendisi
    yapiyordu; zayif modeller tutturamayip hedefin kenarina tikliyordu. Artik
    kimlik veriliyor, donusumu `pcb-do` yapiyor. Ofset ve olcek yine basiliyor
    -- insan icin ve kimlik verilmeyen eski yol icin.
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
            f"  shot: {s.id}\n"
            f"  monitor {s.monitor.index} ({s.monitor.connector}"
            f"{', birincil' if s.monitor.primary else ''}) · "
            f"{s.size[0]}x{s.size[1]} @ ofset ({s.offset[0]}, {s.offset[1]})"
        )
        if s.scale != 1.0:
            line += f" -> {s.scaled[0]}x{s.scaled[1]} (olcek {s.scale:.3f})"
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
    first = next((s for s in shots if s.offset is not None), None)
    if first is not None:
        out.append(
            "Koordinati GORDUGUN GIBI ver ve yanina o goruntunun kimligini ekle:\n"
            f'  pcb-do \'{{"a":"click","x":<goruntu_x>,"y":<goruntu_y>,'
            f'"shot":"{first.id}"}}\'\n'
            "Ofseti ve olcegi pcbridge kendisi uyguluyor — sen cevirme."
        )

    # Uyari EN SONA: kullanim talimatinin ustunde dursaydi "koordinat cikarma"
    # ile "koordinati soyle ver" yan yana gelir, son okunan sey talimat olurdu.
    if capture_provider is None:
        from ..desktop import capture as _cap

        oversize_note = _cap.oversize_note
    else:
        oversize_note = capture_provider.oversize_note

    for s in shots:
        note = oversize_note(s)
        if note:
            out.append("\n" + note)
            break
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
        "--scale", type=int, default=None,
        help="Kirpma sonrasi uzun kenar. Verilmezse config'teki "
             "`screenshot_scale_long_edge` (screen_capture ile ayni deger). "
             "0 = tam cozunurluk.",
    )
    p.add_argument("--no-pointer", action="store_true",
                   help="Imleci goruntuye cizme.")
    p.add_argument("--out", default="",
                   help="PNG dizini (varsayilan: $XDG_RUNTIME_DIR/pcbridge/shots)")
    p.add_argument("--json", action="store_true", help="Makine okunur cikti.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.scale is not None and args.scale and args.scale < 320:
        fail("--scale ya 0 (tam cozunurluk) ya da en az 320 olmali",
             EXIT_BAD_INPUT, args.json)

    cfg = load()
    # Verilmediyse `screen_capture`in kullandigi ayarin AYNISI. Iki yol ayri
    # varsayilanlar tasisaydi ajanin gordugu cozunurluk baglanti bicimine gore
    # degisirdi -- ayni ekran, iki farkli piksel uzayi.
    scale = (cfg.desktop.screenshot_scale_long_edge if args.scale is None
             else max(0, args.scale))
    from ..desktop import capture as capturelib
    from ..desktop.errors import DesktopError
    from ..desktop import monitors as monitorslib
    from ..desktop import screencast as screencastlib

    runtime = runtime_of(cfg)
    gate = runtime.gate
    capture_provider = runtime.capture_provider
    try:
        # Ekran goruntusu bir YAZMA eylemi degil: "yakinda klavye kullanildi"
        # korumasina takilmiyor, ama izin penceresi ve ekran kilidi aynen gecerli.
        check_gate(runtime, "pcb_shot", write=False, needs_input=False)

        # MCP sunucusu yayini izin suresince acik tutuyor; burasi kisa omurlu
        # bir surec, o yuzden runtime kendi yayinini finally'de kapatiyor.
        if cfg.desktop.capture_backend != "gnome-screenshot":
            try:
                runtime.start_capture(
                    cursor=not args.no_pointer and cfg.desktop.include_pointer
                )
            except (
                screencastlib.ScreenCastError,
                monitorslib.MonitorError,
                DesktopError,
            ):
                runtime.stop_capture()

        ok, why = capture_provider.available()
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
            shots = capture_provider.capture(
                spec,
                out_dir=out_dir,
                scale_long_edge=scale,
                include_pointer=not args.no_pointer and cfg.desktop.include_pointer,
                # `--out` ile baska bir dizine yaziliyorsa cekim kaydi ARAMA
                # dizinine de gidiyor: `pcb-do` ayri bir surec ve `--out`u
                # bilemez. Kopya cekimin PARCASI -- yazilamazsa cekim de
                # yayimlanmaz. Kayitta PNG'nin MUTLAK yolu kalir.
                copy_meta_to=[] if out_dir == shot_dir(cfg) else [shot_dir(cfg)],
                reserved_dirs=list(cfg.shot_search_dirs),
            )
            mons = capture_provider.list_monitors()
        except (
            capturelib.CaptureError,
            monitorslib.MonitorError,
            DesktopError,
        ) as exc:
            gate.audit("pcb_shot_error", error=str(exc)[:160], job=job_id())
            fail(str(exc), EXIT_BAD_INPUT, args.json)

        gate.audit(
            "pcb_shot",
            monitor=str(args.monitor),
            shots=len(shots),
            swept=swept or None,
            backend=capture_provider.backend_name(),
            job=job_id(),
        )

        if args.json:
            print(json.dumps({
                "ok": True,
                "shots": [
                    {
                        "id": s.id,
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
                # Hangi yolun kareyi aldigi: `auto` geri dustuyse burada gorunur.
                "backend": capture_provider.backend_name(),
                "degraded": getattr(capture_provider, "degraded_reason", "") or None,
                "primary_monitor": next(
                    (m.index for m in mons if m.primary), None
                ),
            }, ensure_ascii=False, indent=2))
        else:
            print("\n".join(describe(shots, mons, capture_provider)))
        return EXIT_OK
    finally:
        runtime.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
