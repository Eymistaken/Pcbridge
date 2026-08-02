#!/usr/bin/env python3
"""Ajan/model/effort cozumleyicisinin testleri.

Cozumleyici saf fonksiyon oldugu icin bu testler **sunucuya ihtiyac duymaz**:

    ./.venv/bin/python tests/test_models.py

pytest kuruluysa ayni dosya oldugu gibi toplanir:

    ./.venv/bin/python -m pytest tests/test_models.py -q

Politika testleri `config.example.toml` uzerinden kosar; boylece gizli
`config.toml`'a hic dokunulmaz ve depoya giren ornek dosyanin kendisi de
dogrulanmis olur.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pcbridge import jobs as J  # noqa: E402
from pcbridge import models as M  # noqa: E402
from pcbridge.config import AgentSpec, Config, load_config  # noqa: E402

IN_PYTEST = "pytest" in sys.modules
ok_count = 0
fail_count = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global ok_count, fail_count
    if cond:
        ok_count += 1
        print(f"  \033[32mPASS\033[0m  {name}")
    else:
        fail_count += 1
        print(f"  \033[31mFAIL\033[0m  {name}  {detail}")
        if IN_PYTEST:
            raise AssertionError(f"{name}: {detail}")


def section(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")


def example_config() -> Config:
    return load_config(str(ROOT / "config.example.toml"))


def mini_config(agents: dict[str, AgentSpec], default_agent: str = "claude") -> Config:
    """Sentetik Config -- yalnizca cozumleyicinin okudugu alanlar anlamli."""
    return Config(
        public_url="https://example.invalid",
        host="127.0.0.1",
        port=8765,
        mcp_path="/mcp",
        password="x" * 16,
        static_token="",
        access_token_ttl=1,
        refresh_token_ttl=1,
        auth_code_ttl=1,
        max_failed_attempts=1,
        lockout_seconds=1,
        manual_redirect=False,
        default_workdir=Path("/tmp"),
        state_dir=Path("/tmp"),
        max_output_chars=1000,
        default_job_timeout=60,
        max_sync_timeout=60,
        agents=agents,
        default_agent=default_agent,
    )


def shape(res: M.Resolution) -> tuple[str, str | None, str | None]:
    return (res.agent, res.model, res.effort)


# ---------------------------------------------------------------------------


def test_normalize() -> None:
    section("1. Normalizasyon")
    same = [
        ("Gemini 3.6 Flash", "gemini-3.6-flash"),
        ("gemini_3_6_flash", "gemini-3.6-flash"),
        ("  GEMINI-3.6-FLASH  ", "gemini 3 6 flash"),
        ("Opus5", "opus 5"),
        ("claude sonnet 4.6", "claude-sonnet-4-6"),
    ]
    for a, b in same:
        check(
            f"{a!r} == {b!r}",
            M.normalize(a) == M.normalize(b),
            f"{M.normalize(a)!r} != {M.normalize(b)!r}",
        )
    check("farkli adlar ayrisiyor", M.normalize("opus") != M.normalize("sonnet"))


def test_plan_table() -> None:
    """UYGULAMA.md / PLAN.md §5.2.2'deki 'ne dersen / ne olur' tablosu."""
    section("2. Plandaki tablo")
    cfg = example_config()
    rows: list[tuple[str, dict, tuple[str, str | None, str | None]]] = [
        ("model/effort yok", {}, ("claude", "sonnet", "medium")),
        ("model=opus", {"model": "opus"}, ("claude", "opus", "high")),
        (
            "model=opus effort=extra",
            {"model": "opus", "effort": "extra"},
            ("claude", "opus", "xhigh"),
        ),
        (
            "agent=antigravity",
            {"agent": "antigravity"},
            ("antigravity", "gemini-3.6-flash", "high"),
        ),
        (
            "antigravity + 'gemini 3.6 flash' + 'yuksek'",
            {"agent": "antigravity", "model": "gemini 3.6 flash", "effort": "yuksek"},
            ("antigravity", "gemini-3.6-flash", "high"),
        ),
        (
            "antigravity + '3.1 pro' + 'dusuk'",
            {"agent": "antigravity", "model": "3.1 pro", "effort": "dusuk"},
            ("antigravity", "gemini-3.1-pro", "low"),
        ),
        (
            "antigravity + claude opus (acikca istendi)",
            {"agent": "antigravity", "model": "claude opus 4.6"},
            ("antigravity", "claude-opus-4-6-thinking", None),
        ),
        (
            "model='3.6 flash' -> ajan cikarimi",
            {"model": "3.6 flash"},
            ("antigravity", "gemini-3.6-flash", "high"),
        ),
        ("model=haiku", {"model": "haiku"}, ("claude", "haiku", "medium")),
    ]
    for label, kwargs, want in rows:
        res = M.resolve(cfg, **kwargs)
        check(f"{label} -> {want}", res.ok and shape(res) == want, res.error or str(shape(res)))


def test_blocked_and_unknown() -> None:
    section("3. Reddedilmesi gerekenler")
    cfg = example_config()

    for name in ("fable", "best", "Fable 5"):
        res = M.resolve(cfg, model=name)
        check(f"{name!r} reddedildi", not res.ok, str(shape(res)))
        if res.error:
            check(f"{name!r} gerekcesi var", "blocked_models" in res.error, res.error)

    res = M.resolve(cfg, model="uydurulmus-model-9000")
    check("bilinmeyen model reddedildi", not res.ok, str(shape(res)))
    check(
        "bilinmeyen modelde gecerli liste gosteriliyor",
        bool(res.error) and "sonnet" in (res.error or ""),
        res.error or "",
    )

    res = M.resolve(cfg, agent="yokboyle")
    check("tanimsiz ajan reddedildi", not res.ok, str(shape(res)))


def test_restricted_needs_explicit_request() -> None:
    section("4. Kisitli modeller")
    cfg = example_config()
    spec = cfg.agents["antigravity"]

    check(
        "kisitli modeller varsayilan listede degil",
        all(m not in spec.selectable_models for m in spec.restricted_models),
        str(spec.selectable_models),
    )
    check(
        "varsayilan model kisitli degil",
        spec.default_model not in spec.restricted_models,
        spec.default_model,
    )
    # Varsayilan doldurma hicbir zaman kisitliya dusmemeli
    for kwargs in ({}, {"agent": "antigravity"}, {"agent": "antigravity", "effort": "low"}):
        res = M.resolve(cfg, **kwargs)
        check(
            f"varsayilan {kwargs} kisitliya dusmedi",
            res.ok and res.model not in spec.restricted_models,
            str(shape(res)),
        )
    # Ama acikca istenince gecmeli
    res = M.resolve(cfg, agent="antigravity", model="claude-sonnet-4-6")
    check("acikca istenen kisitli model gecti", res.ok and res.model == "claude-sonnet-4-6",
          res.error or str(shape(res)))


def test_effort_clamp() -> None:
    section("5. Effort kirpma (sessiz dusurme yok)")
    cfg = example_config()

    res = M.resolve(cfg, agent="antigravity", effort="xhigh")
    check("xhigh -> high", res.ok and res.effort == "high", str(shape(res)))
    check("kirpma not birakti", bool(res.notes), str(res.notes))
    check(
        "notta hem istenen hem kullanilan geciyor",
        any("xhigh" in n and "high" in n for n in res.notes),
        str(res.notes),
    )

    # gemini-3.1-pro'da medium YOK (olculdu) -> en yakin ALT seviye: low
    res = M.resolve(cfg, agent="antigravity", model="gemini-3.1-pro", effort="medium")
    check("3.1-pro + medium -> low", res.ok and res.effort == "low", str(shape(res)))
    check("3.1-pro kirpmasi not birakti", bool(res.notes), str(res.notes))

    # Claude tarafinda xhigh gecerli, kirpilmamali
    res = M.resolve(cfg, model="opus", effort="xhigh")
    check("claude + xhigh kirpilmadi", res.ok and res.effort == "xhigh", str(shape(res)))
    check("gereksiz not yok", not res.notes, str(res.notes))


def test_models_without_effort_support() -> None:
    section("6. --effort kabul etmeyen modeller")
    cfg = example_config()
    spec = cfg.agents["antigravity"]

    for name in ("claude-sonnet-4-6", "claude-opus-4-6-thinking", "gpt-oss-120b-medium"):
        check(f"{name} effort listesi bos", spec.efforts_for(name) == [], str(spec.efforts_for(name)))
        res = M.resolve(cfg, agent="antigravity", model=name, effort="high")
        check(f"{name} + high -> effort dusuruldu", res.ok and res.effort is None,
              res.error or str(shape(res)))
        check(f"{name} bunu soyledi", bool(res.notes), str(res.notes))
        check(
            f"{name} icin --effort bayragi kurulmadi",
            "--effort" not in M.build_args(spec, res),
            str(M.build_args(spec, res)),
        )


def test_build_args() -> None:
    section("7. Bayrak kurulumu")
    cfg = example_config()

    res = M.resolve(cfg, model="opus", effort="high")
    check(
        "claude bayraklari",
        M.build_args(cfg.agents["claude"], res) == ["--model", "opus", "--effort", "high"],
        str(M.build_args(cfg.agents["claude"], res)),
    )
    res = M.resolve(cfg, agent="antigravity")
    check(
        "antigravity bayraklari",
        M.build_args(cfg.agents["antigravity"], res)
        == ["--model", "gemini-3.6-flash", "--effort", "high"],
        str(M.build_args(cfg.agents["antigravity"], res)),
    )


def test_effort_required() -> None:
    section("8. effort_required_with_model")
    # Effort'u hicbir yoldan doldurulamayan sentetik ajan
    spec = AgentSpec(
        name="strict",
        command=["x", "{prompt}"],
        model_args=["--model", "{model}"],
        effort_args=["--effort", "{effort}"],
        models=["m1"],
        default_model="m1",
        efforts=["low", "high"],
        effort_required_with_model=True,
    )
    cfg = mini_config({"strict": spec}, default_agent="strict")

    res = M.resolve(cfg)
    check("effort yoksa reddedildi", not res.ok, str(shape(res)))
    check(
        "gerekce effort'tan bahsediyor",
        bool(res.error) and "effort" in res.error.lower(),
        res.error or "",
    )
    res = M.resolve(cfg, effort="high")
    check("effort verilince gecti", res.ok and res.effort == "high", res.error or str(shape(res)))


def test_backward_compatible() -> None:
    section("9. Geriye donuk uyum (yeni alan tanimlanmamis ajan)")
    spec = AgentSpec(name="eski", command=["eski", "-p", "{prompt}"])
    cfg = mini_config({"eski": spec}, default_agent="eski")

    res = M.resolve(cfg)
    check("cozumleme basarili", res.ok, res.error or "")
    check("model bos birakildi", res.model is None, str(res.model))
    check("effort bos birakildi", res.effort is None, str(res.effort))
    check("hicbir bayrak eklenmedi", M.build_args(spec, res) == [], str(M.build_args(spec, res)))

    res = M.resolve(cfg, model="opus", effort="high")
    check("istek reddedilmedi, yok sayildi", res.ok, res.error or "")
    check("yok sayildigi soylendi", bool(res.notes), str(res.notes))
    check("yine bayrak yok", M.build_args(spec, res) == [], str(M.build_args(spec, res)))


def test_live_config_policy() -> None:
    """Kullanicinin gercek config.toml'u -- yalnizca politika, sir okunmaz."""
    section("10. Canli config.toml politikasi")
    path = ROOT / "config.toml"
    if not path.exists():
        print("  (config.toml yok, atlandi)")
        return
    cfg = load_config(str(path))

    claude = cfg.agents.get("claude")
    if claude:
        check("claude varsayilani sonnet", claude.default_model == "sonnet", claude.default_model)
        check("fable engelli", "fable" in claude.blocked_models, str(claude.blocked_models))
        check("best engelli", "best" in claude.blocked_models, str(claude.blocked_models))
        res = M.resolve(cfg, model="fable")
        check("canli configde fable reddediliyor", not res.ok, str(shape(res)))

    agy = cfg.agents.get("antigravity")
    if agy:
        check(
            "antigravity varsayilani gemini-3.6-flash",
            agy.default_model == "gemini-3.6-flash",
            agy.default_model,
        )
        check("agy pty kapali (1.1.9'da gereksiz)", agy.pty is False, str(agy.pty))
        check("agy parser json", agy.parser == "agy_json", agy.parser)
        check(
            "agy komutunda --output-format json var",
            "--output-format" in agy.command and "json" in agy.command,
            str(agy.command),
        )


def test_claude_stream_parser() -> None:
    """`parse_claude_stream_json` — sahte ajanin gercek ciktisi uzerinden.

    Bu ayristiriciyi eskiden yalnizca `test_e2e.py`'nin 12. bolumu kontrol
    ediyordu; orasi sabit ciktili bir ajan ister ve `bash -lc` login kabugu
    PATH'i yeniden kurdugu icin sahte ajan oraya enjekte EDILEMIYOR. Sonuc:
    ayristirici fiilen test edilmiyordu. Saf fonksiyon oldugu icin dogru yeri
    burasi -- sunucu da, PATH oyunu da gerekmiyor.

    Ciktiyi elle yazmak yerine stub'i calistiriyoruz; stub ile ayristiricinin
    beklentisi birbirinden kayarsa test bunu yakalar.
    """
    section("11. claude stream-json ayristiricisi")
    stub = ROOT / "tests" / "fake_agents" / "claude"
    proc = subprocess.run(
        [
            sys.executable, str(stub),
            "-p", "merhaba testi",
            "--output-format", "stream-json", "--verbose",
            "--model", "sonnet",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    check("sahte ajan calisti", proc.returncode == 0, proc.stderr[:200])

    p = J.parse_claude_stream_json(proc.stdout)
    check("oturum kimligi okundu", p["session_id"] == "sess-abc-123", str(p["session_id"]))
    check(
        "arac cagrisi adimi var",
        any("arac: Bash" in s for s in p["steps"]),
        str(p["steps"]),
    )
    check(
        "arac sonucu adimi var",
        any(s.startswith("← sonuc:") for s in p["steps"]),
        str(p["steps"]),
    )
    check(
        "sonuc metni okundu",
        p["final_answer"] == "Istek tamamlandi: merhaba testi",
        str(p["final_answer"]),
    )
    check("maliyet okundu", p["cost_usd"] == 0.0123, str(p["cost_usd"]))
    check("hata bayragi kapali", p["is_error"] is False, str(p["is_error"]))
    check("calisan model bildirildi", p["actual_model"] == "sonnet", str(p["actual_model"]))

    # Ajanlar akisa banner/uyari gibi duz metin satirlari karistirabiliyor.
    noisy = "Uyari: guncelleme var\n" + proc.stdout + "\nbozuk {json\n"
    p2 = J.parse_claude_stream_json(noisy)
    check(
        "JSON olmayan satirlar akisi bozmuyor",
        p2["final_answer"] == p["final_answer"] and p2["cost_usd"] == 0.0123,
        str(p2["final_answer"]),
    )

    # subtype != "success" -> basarisiz sayilmali (is_error alani gelmese bile)
    p3 = J.parse_claude_stream_json(
        json.dumps(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "session_id": "s1",
                "result": "tur limiti",
            }
        )
    )
    check("basarisiz sonuc is_error yapiyor", p3["is_error"] is True, str(p3["is_error"]))


def main() -> int:
    for fn in (
        test_normalize,
        test_plan_table,
        test_blocked_and_unknown,
        test_restricted_needs_explicit_request,
        test_effort_clamp,
        test_models_without_effort_support,
        test_build_args,
        test_effort_required,
        test_backward_compatible,
        test_live_config_policy,
        test_claude_stream_parser,
    ):
        fn()
    print(f"\n\033[1mSonuc:\033[0m {ok_count} gecti, {fail_count} kaldi")
    return 1 if fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
