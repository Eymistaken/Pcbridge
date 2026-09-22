"""`pcb-do` — eylem listesini calistirir. `computer_batch`'in CLI ikizi.

Motor YENIDEN YAZILMIYOR: `desktop/batch.py` ayristirir ve calistirir,
`desktop/ops.py` gercek cihazlara baglar. Aradaki tek fark kapinin nasil
kuruldugu ve sonucun nasil basildigi.

NEDEN LISTE ALIYOR
    Her `pcb-do` cagrisi ayri bir surec ve sanal cihazlar surecle birlikte
    dogup oluyor. OLCULDU 2026-08-02, taze surecte:
        klavye cihazi   1,301 s
        fare cihazi     1,306 s   (ayri ayri acilinca toplam 2,607 s)
        ikisi + TEK bekleme       1,41 s
        gercek tus basimi         0,030 s
    Yani on eylemi tek tek gondermek ~26 saniye cihaz kurulumu demek, tek
    listede gondermek 1,4 saniye. `InputBackend.ensure()` bastan cagriliyor
    ki iki cihaz beklemeyi paylassin.

BOSTA (IDLE) KONTROLU
    uinput olayi `IdleMonitor`'u sifirliyor (olculdu: 104227 ms -> 151 ms), yani
    ajanin ikinci eylemi kendi ilk tusunu "kullanici geldi" diye okur ve
    reddedilir. Kontrol bu yuzden EYLEM basina degil GOREV basina: bkz.
    `cli.task_force`. Kilit, izin penceresi ve hiz siniri her cagrida tam
    isliyor -- yani telefondan `desktop_lock` demek ajanin ellerini bir sonraki
    eylemde durduruyor.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import (
    EXIT_BAD_INPUT,
    EXIT_DENIED,
    EXIT_OK,
    EXIT_PARTIAL,
    check_gate,
    fail,
    job_id,
    load,
    newest_shot_age,
    runtime_of,
    shot_dir,
    task_force,
)

# Koordinatla calisan eylemler. YALNIZCA bunlar bir ekran goruntusune dayanir;
# `key`, `type`, `ui_click`, `ui_set_text` koordinat kullanmadigi icin
# goruntunun yasiyla ilgileri yok.
COORD_ACTIONS = {"move", "click", "double_click", "triple_click", "right_click",
                 "middle_click", "mouse_down", "drag", "scroll"}


def coord_actions(plan) -> list:
    """Icinde ACIKCA x/y verilmis eylemler."""
    return [a for a in plan
            if a.a in COORD_ACTIONS and a.args.get("x") is not None]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pcb-do",
        description="Run desktop actions (one object or a list).",
        epilog=(
            "example: pcb-do '[{\"a\":\"click\",\"x\":640,\"y\":360,\"shot\":\"m2-a1b2c3\"},"
            "{\"a\":\"wait\",\"ms\":500},{\"a\":\"type\",\"text\":\"hello\"}]'"
        ),
    )
    p.add_argument("actions", nargs="?", default="",
                   help="A JSON action or list of actions. '-' reads stdin.")
    p.add_argument("--dry-run", action="store_true",
                   help="Only parse and print the plan; run NOTHING.")
    p.add_argument("--force", action="store_true",
                   help="Send even if the user is at the machine. "
                        "Skips ONLY the idle check; screen lock, the grant "
                        "window and the rate limit still apply.")
    p.add_argument("--max-shot-age", type=int, default=-1,
                   help="When a coordinate action is sent, the newest screenshot "
                        "may be at most this many seconds old. "
                        "0 = no check. Default: the value in the config.")
    p.add_argument("--expect-focus", default="",
                   help="Part of the name of the window these clicks are MEANT "
                        "to switch to. If the focus goes there the sequence goes "
                        "on; anywhere else it still stops.")
    p.add_argument("--no-check-focus", action="store_true",
                   help="Turn off the focus check after clicks (not recommended; "
                        "try --expect-focus first).")
    p.add_argument("--json", action="store_true", help="Machine-readable output.")
    return p


def read_actions(raw: str) -> str:
    if raw == "-":
        return sys.stdin.read()
    return raw


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    text = read_actions(args.actions)
    if not text.strip():
        fail("no action given. For an example: pcb-do --help",
             EXIT_BAD_INPUT, args.json)

    cfg = load()

    from ..desktop import batch as batchlib
    from ..desktop import ops as opslib
    from ..desktop.errors import DesktopError

    # Ayristirma KAPIDAN ONCE: yalnizca sozdizimi, hicbir yan etkisi yok.
    # Bozuk bir liste "izin yok" degil "bozuk JSON" cevabi almali.
    try:
        plan = batchlib.parse(text, max_actions=cfg.desktop.batch_max_actions)
    except batchlib.BatchError as exc:
        fail(str(exc), EXIT_BAD_INPUT, args.json)
    except DesktopError as exc:
        # Onaylanmamis kapatma kisayolu: liste butunuyle reddedildi.
        fail(str(exc), EXIT_BAD_INPUT, args.json)

    if args.dry_run:
        # Hicbir cihaz acilmiyor, kapiya da varilmiyor: bu bir SOZDIZIMI
        # kontrolu. Ajan kendi urettigi JSON'u boyle dogruluyor.
        lines = [f"{len(plan)} action(s) parsed (NOT RUN):"]
        for i, act in enumerate(plan, 1):
            detail = {k: v for k, v in act.args.items() if k != "text"}
            if "text" in act.args:
                detail["text_chars"] = len(str(act.args["text"]))
            lines.append(f"  {i}. {act.a} {detail if detail else ''}".rstrip())
        from ..desktop import apps as appslib

        # Kuru kosuda da GERCEK yolu bildir: eklenti kuruluysa `focus` klavye
        # actirmaz, degilse actirir. Yanlis bildirmek ajani "cihaz gerekmiyor"
        # diye yanlis plana sokar.
        fast_focus = appslib.extension_focus_available()
        want_k, want_p, want_r = opslib.devices_needed(
            plan, focus_uses_keyboard=not fast_focus
        )
        lines.append(
            f"devices needed: keyboard={'yes' if want_k else 'no'} "
            f"pointer={'yes' if want_p else 'no'} "
            f"relative-pointer={'yes' if want_r else 'no'}"
        )
        if "focus" in {a.a for a in plan}:
            lines.append(
                "focus path: "
                + ("GNOME extension (open window); a closed application is started "
                   "directly, GNOME search if the extension cannot raise it" if fast_focus
                   else "no key if already focused; a closed application is started "
                   "directly, an open window through GNOME search (no extension)")
            )
        estimate = batchlib.estimate(plan, fast_focus=fast_focus)
        lines.append(f"estimated time: {estimate:.1f} s")
        budget = float(cfg.desktop.batch_budget_seconds)
        if estimate > budget:
            # Gercek kosuda `batch.run` bu listeyi hic baslatmadan reddeder.
            lines.append(
                f"⚠ budget {budget:.0f} s: this list would NOT START, split it"
            )
        if args.json:
            print(json.dumps({
                "ok": True, "dry_run": True, "count": len(plan),
                "actions": [{"a": a.a, **a.args} for a in plan],
                "needs_keyboard": want_k, "needs_pointer": want_p,
                "estimate_seconds": round(estimate, 1),
                "fits_budget": estimate <= budget,
            }, ensure_ascii=False, indent=2))
        else:
            print("\n".join(lines))
        return EXIT_OK

    runtime = runtime_of(cfg)
    try:
        return _run_plan(cfg, args, plan, runtime)
    finally:
        runtime.close()


def _run_plan(cfg, args, plan, runtime) -> int:
    """Execute one parsed plan with resources owned by a single runtime."""
    from ..desktop import apps as appslib
    from ..desktop import batch as batchlib
    from ..desktop import capture as capturelib
    from ..desktop import execution as executionlib
    from ..desktop.errors import DesktopError, ErrorCode
    from ..desktop import ops as opslib

    capture_provider = runtime.capture_provider

    # BAYAT GORUNTU KONTROLU. Odak korumasi "tikladiktan SONRA odak degisti mi"
    # diye bakiyor; bu ondan farkli bir tehlike: goruntuyu aldiktan sonra
    # kullanici baska pencereye gecmisse odak zaten orada olur, TIKLAMA da
    # oraya duser ve DEGISEN bir sey olmadigi icin odak korumasi sessiz kalir.
    # OLCULDU 2026-08-03: 69 saniyelik bir goruntuye gore tiklandi, tiklama
    # Vesktop yerine baska bir uygulamaya dustu ve hicbir koruma otmedi.
    # Kaza ile ayni sinif hata: dogrulanmamis bir varsayima gore tiklamak.
    limit = (cfg.desktop.agent_shot_max_age_seconds if args.max_shot_age < 0
             else args.max_shot_age)
    needs_shot = coord_actions(plan)
    if limit > 0 and needs_shot:
        # `shot=` VERILMISSE o cekimin kendi yasina bakilir, dizindeki en yeni
        # PNG'ye degil. Aradaki fark gercek bir acik: arada taze bir cekim
        # yapilmissa "en yeni goruntu taze" der ve gecerdi, ama tiklama BASKA,
        # eski bir cekimin koordinatlarina gore yapilirdi -- yani korumanin
        # engellemek icin var oldugu seyin ta kendisi.
        dirs = cfg.shot_search_dirs
        for act in needs_shot:
            sid = act.args.get("shot")
            if not sid:
                continue
            try:
                age = capture_provider.load_shot(sid, dirs).age
            except (capturelib.CaptureError, DesktopError) as exc:
                fail(str(exc), EXIT_DENIED, args.json)
            if age > limit:
                fail(f"shot `{sid}` is {int(age)} seconds old (limit {limit}). "
                     "The windows may have changed since, and that "
                     "coordinate may be on top of something else now. Take a "
                     "FRESH picture with `pcb-shot` first.", EXIT_DENIED, args.json)

        # Kimliksiz koordinat: hangi cekime dayandigi bilinmiyor, o yuzden
        # olcut dizindeki en yeni goruntu.
        if any(not a.args.get("shot") for a in needs_shot):
            age = newest_shot_age(shot_dir(cfg))
            if age is None:
                fail("LOOK at the screen with `pcb-shot` before clicking by coordinate — "
                     "no screenshot has been taken. No blind clicks.",
                     EXIT_DENIED, args.json)
            if age > limit:
                fail(f"The newest screenshot is {int(age)} seconds old (limit {limit}). "
                     "The windows may have changed since, and that coordinate "
                     "may be on top of something else now. Take a FRESH picture "
                     "with `pcb-shot` first.", EXIT_DENIED, args.json)

    gate = runtime.gate
    kinds = {a.a for a in plan}
    # Tek sorgu, iki karar: kapi ve cihaz on acilisi ayni gercegi kullansin.
    # Eklenti varsa `focus` uinput istemiyor; yoksa arama yedegi klavye ister.
    focus_uses_keyboard = not appslib.extension_focus_available()
    needs_input = bool(kinds & batchlib.INPUT_ACTIONS) or (
        focus_uses_keyboard and "focus" in kinds
    )
    # Iki yoldan da yalnizca BOSTA kontrolu atlanir: `--force` elle kullanim
    # icin, `PCBRIDGE_TASK_FORCE` ise `computer_task`in gorev basinda yaptigi
    # kontrolu ajanin her eyleminde tekrarlamamak icin.
    forced = args.force or task_force()
    check_gate(
        runtime,
        "pcb_do",
        write=True,
        needs_input=needs_input,
        force=forced,
    )

    backend = runtime.input_provider
    tree = runtime.accessibility_provider

    # Cihazlari bastan ac: ikisi de gerekiyorsa bekleme tek sefere iner.
    want_k, want_p, want_r = opslib.devices_needed(
        plan, focus_uses_keyboard=focus_uses_keyboard
    )
    warmup = (
        backend.ensure(keyboard=want_k, pointer=want_p, relative=want_r)
        if (want_k or want_p or want_r) else 0.0
    )

    gap = 1.0 / cfg.desktop.max_actions_per_second if cfg.desktop.max_actions_per_second > 0 else 0.0
    check_focus = cfg.desktop.batch_check_focus and not args.no_check_focus

    gate.audit("pcb_do_start", count=len(plan), kinds=",".join(sorted(kinds)),
               forced=forced or None, job=job_id())
    # Tek yazma dizisi, butun sureclerle sirali (Task 5.1): MCP sunucusunun
    # batch'i ile bu surecin tuslari birbirine karismaz ve telefondan gelen
    # `desktop_lock` KALAN eylemleri de durdurur.
    try:
        with runtime.write_sequence("pcb_do") as guard:
            result = batchlib.run(
                plan,
                opslib.DeviceOps(backend, tree, cfg, capture_provider),
                budget=max(0.0, float(cfg.desktop.batch_budget_seconds) - guard.waited),
                min_gap=gap,
                check_focus=check_focus,
                expect_focus=args.expect_focus,
                repeat_limit=cfg.desktop.repeat_click_limit,
                before_action=guard,
                fast_focus=not focus_uses_keyboard,
            )
    except executionlib.SequenceRefused as exc:
        busy = exc.code == ErrorCode.BUSY
        gate.audit("pcb_do_busy" if busy else "pcb_do_denied",
                   error=exc.code.value, job=job_id())
        fail(str(exc), EXIT_DENIED, args.json)

    for step in result.steps:
        # Metin ICERIGI yazilmaz -- `ui_set_text`teki kural aynen gecerli.
        gate.audit("pcb_do_step", i=step.index, a=step.action, ok=step.ok,
                   ms=round(step.ms), job=job_id())
    gate.audit("pcb_do", done=result.done, total=result.total,
               seconds=round(result.elapsed, 1), stopped=result.stopped or None,
               job=job_id())

    if args.json:
        print(json.dumps({
            "ok": not result.stopped,
            "done": result.done,
            "total": result.total,
            "elapsed_seconds": round(result.elapsed, 1),
            "warmup_seconds": round(warmup, 2),
            "stopped": result.stopped or None,
            "detail": result.detail or None,
            "error_code": getattr(getattr(result.error, "code", None), "value", None),
            "focus_start": result.focus_start or None,
            "focus_now": result.focus_now or None,
            "remaining": [{"a": a.a, **a.args} for a in result.remaining],
            "steps": [
                {"i": s.index, "a": s.action, "ok": s.ok,
                 "ms": round(s.ms), "note": s.note}
                for s in result.steps
            ],
        }, ensure_ascii=False, indent=2))
    else:
        print(batchlib.describe(result))

    if result.stopped:
        # Kismi calisma HATA DEGIL ama "her sey yolunda" da degil: ajan
        # ekrani tekrar okuyup nerede kaldigina bakmali.
        return EXIT_PARTIAL
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
