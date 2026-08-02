#!/usr/bin/env python3
"""Masaustu katmaninin testleri — sunucusuz ve GIRDI GONDERMEZ.

    ./.venv/bin/python tests/test_desktop.py

pytest kuruluysa ayni dosya oldugu gibi toplanir:

    ./.venv/bin/python -m pytest tests/test_desktop.py -q

Bilincli olarak yapilmayan sey: gercek tiklama/tus gondermek. Bu testler
gelistiricinin makinesinde de CI'da da kosabilmeli; uinput'a fiilen yazan
dogrulama elle, kullaniciya haber verilerek ve bos bir pencerede yapilir
(`YAPILACAKLAR.md` girdi guvenligi kurallari).

Monitor tablosu ve D-Bus okumalari sahtelenerek test edilir; boylece testler
ekran sayisindan, kilit durumundan ve kullanicinin makinede olup olmamasindan
bagimsiz kalir.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pcbridge.config import DesktopSpec  # noqa: E402
from pcbridge.desktop import input as I  # noqa: E402
from pcbridge.desktop import monitors as M  # noqa: E402
from pcbridge.desktop import safety as S  # noqa: E402

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


# --------------------------------------------------------------- sahte veriler
# Bu makinenin gercek duzeni: DP-2 solda (x=0), DP-1 sagda (x=1920, birincil).
TWO_SCREENS = [
    M.Monitor(0, "DP-1", 1920, 0, 1920, 1080, 1.0, True),
    M.Monitor(0, "DP-2", 0, 0, 1920, 1080, 1.0, False),
]
# Ucuncu bir kurulum: dikey yerlesim + kesirli olcek (ordered() bozulmasin)
ODD_SCREENS = [
    M.Monitor(0, "HDMI-1", 2560, 0, 1280, 1024, 1.0, False),
    M.Monitor(0, "eDP-1", 0, 0, 2560, 1440, 2.0, True),
]


class FakeCfg:
    """SafetyGate'in ihtiyac duydugu minimum yapilandirma."""

    def __init__(self, tmp: Path, **kw):
        self.state_dir = tmp
        self.audit_log = tmp / "audit.log"
        self.desktop = DesktopSpec(**kw)


# ================================================================== MONITORLER
def test_monitor_ordering() -> None:
    section("1. Monitor siralamasi ve numaralandirma")
    got = M._ordered(TWO_SCREENS)
    check("soldan saga siralaniyor", [m.connector for m in got] == ["DP-2", "DP-1"],
          str([m.connector for m in got]))
    check("1'den numaralaniyor", [m.index for m in got] == [1, 2], str([m.index for m in got]))
    check(
        "birincil SAGDA ama 2 numara — 'birincil once' degil",
        got[1].primary and got[1].index == 2,
    )
    odd = M._ordered(ODD_SCREENS)
    check("farkli duzende de soldan saga", [m.connector for m in odd] == ["eDP-1", "HDMI-1"],
          str([m.connector for m in odd]))
    check("tuval boyutu tum monitorleri kapsiyor", M.canvas_size(got) == (3840, 1080),
          str(M.canvas_size(got)))
    check("dikey/genis kurulumda tuval", M.canvas_size(odd) == (3840, 1440),
          str(M.canvas_size(odd)))


def test_monitor_resolve() -> None:
    section("2. monitor= cozumleme")
    mons = M._ordered(TWO_SCREENS)
    check("sayi: 1 -> sol", M.resolve(1, mons).connector == "DP-2")
    check("sayi: 2 -> sag", M.resolve(2, mons).connector == "DP-1")
    check("metin sayi: '2'", M.resolve("2", mons).connector == "DP-1")
    check("baglanti adi", M.resolve("DP-1", mons).index == 2)
    check("baglanti adi kucuk/buyuk harf", M.resolve("dp-2", mons).index == 1)
    check("'primary' birinciliyi verir", M.resolve("primary", mons).connector == "DP-1")
    check("'birincil' de calisir", M.resolve("birincil", mons).connector == "DP-1")
    check("None -> birincil", M.resolve(None, mons).connector == "DP-1")

    for bad in (9, "HDMI-9", True):
        try:
            M.resolve(bad, mons)
            check(f"gecersiz monitor reddedilir: {bad!r}", False, "hata firlatmadi")
        except M.MonitorError as exc:
            check(f"gecersiz monitor reddedilir: {bad!r}", True)
            if bad == 9:
                check("hata mesaji gecerli listeyi gosteriyor", "1, 2" in str(exc), str(exc))


def test_coordinate_offset() -> None:
    section("3. Koordinat donusumu (1920 piksel kayma tuzagi)")
    mons = M._ordered(TWO_SCREENS)
    check(
        "monitor verilmezse koordinat GLOBAL kabul edilir, ofset EKLENMEZ",
        M.to_global(300, 400, None, mons) == (300, 400),
        str(M.to_global(300, 400, None, mons)),
    )
    check(
        "monitor=2 -> 1920 eklenir",
        M.to_global(300, 400, 2, mons) == (2220, 400),
        str(M.to_global(300, 400, 2, mons)),
    )
    check(
        "monitor=1 -> ofset 0",
        M.to_global(300, 400, 1, mons) == (300, 400),
        str(M.to_global(300, 400, 1, mons)),
    )
    check("find_monitor sol", M.find_monitor(100, 100, mons).index == 1)
    check("find_monitor sag", M.find_monitor(2760, 540, mons).index == 2)
    check("find_monitor tuval disi -> None", M.find_monitor(5000, 5000, mons) is None)
    check("bbox kirpma kutusu", mons[1].bbox == (1920, 0, 3840, 1080), str(mons[1].bbox))


def test_monitor_parsers() -> None:
    section("4. Monitor okuma yollari")
    # busctl JSON'unun gercek sekli (bu makineden alindi, kisaltildi)
    props = {"is-current": {"type": "b", "data": True}}
    payload = {
        "data": [
            1,
            [
                [["DP-1", "AUS", "VG247Q1A", "s1"], [["m", 1920, 1080, 165.0, 1.0, [1.0], props]], {}],
                [["DP-2", "AUS", "VG247Q1A", "s2"], [["m", 1920, 1080, 165.0, 1.0, [1.0], props]], {}],
            ],
            [
                [1920, 0, 1.0, 0, True, [["DP-1", "AUS", "VG247Q1A", "s1"]], {}],
                [0, 0, 1.0, 0, False, [["DP-2", "AUS", "VG247Q1A", "s2"]], {}],
            ],
            {},
        ]
    }

    class FakeProc:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""

    real = M.subprocess.run
    M.subprocess.run = lambda *a, **k: FakeProc()  # type: ignore[assignment]
    try:
        mons = M._ordered(M._from_mutter())
    finally:
        M.subprocess.run = real  # type: ignore[assignment]
    check("busctl JSON'u ayristiriliyor", len(mons) == 2, str(mons))
    check("mantiksal boyut mod'dan geliyor", mons[0].width == 1920 and mons[0].height == 1080)
    check("soldan saga: DP-2 birinci", mons[0].connector == "DP-2")

    line = " 0: +*DP-1 1920/530x1080/300+1920+0  DP-1"
    m = M._XRANDR_RE.match(line)
    check("xrandr satiri ayristiriliyor", m is not None)
    if m:
        check("xrandr: ad", m["name"] == "DP-1", m["name"])
        check("xrandr: birincil yildizi", bool(m["primary"]))
        check("xrandr: konum", (int(m["x"]), int(m["y"])) == (1920, 0))
        check("xrandr: boyut", (int(m["w"]), int(m["h"])) == (1920, 1080))


# ====================================================================== KLAVYE
def test_key_names() -> None:
    section("5. Tus adi cozumleme")
    check("harf", I.key_code("a") > 0)
    check("buyuk harf ayni kod", I.key_code("A") == I.key_code("a"))
    check("rakam", I.key_code("5") > 0)
    check("fonksiyon tusu", I.key_code("f12") > 0)
    check("bosluk toleransi", I.key_code("  Return ") == I.key_code("return"))
    check("enter == return", I.key_code("enter") == I.key_code("return"))
    check("esc == escape", I.key_code("esc") == I.key_code("escape"))
    check("super == meta == win", I.key_code("super") == I.key_code("win") == I.key_code("meta"))

    combo = I.parse_combo("ctrl+shift+t")
    check("kombinasyon sirali cozuluyor", combo == [
        I.key_code("ctrl"), I.key_code("shift"), I.key_code("t")], str(combo))
    check("bosluklu kombinasyon", I.parse_combo("ctrl + v") == I.parse_combo("ctrl+v"))
    check("tek tus kombinasyon", len(I.parse_combo("super")) == 1)

    for bad in ("flurb", "", "ctrl+nope"):
        try:
            I.parse_combo(bad)
            check(f"bilinmeyen tus reddedilir: {bad!r}", False, "hata firlatmadi")
        except I.InputError:
            check(f"bilinmeyen tus reddedilir: {bad!r}", True)

    check(
        "tus tablosu ile cihaz yetenekleri ayni kaynaktan",
        all(v.startswith("KEY_") for v in I.KEY_NAMES.values()),
    )
    check("duzenden bagimsiz tuslar tabloda", all(
        k in I.KEY_NAMES for k in ("return", "escape", "tab", "up", "down", "left", "right")))


def test_raw_ascii_table() -> None:
    section("6. Ham yazma tablosu (raw=True)")
    check("kucuk harf shift'siz", I._RAW_ASCII["a"] == ("a", False))
    check("buyuk harf shift'li", I._RAW_ASCII["A"] == ("a", True))
    check("rakam", I._RAW_ASCII["1"] == ("1", False))
    check("shift'li isaret", I._RAW_ASCII["@"] == ("2", True))
    check("bosluk", I._RAW_ASCII[" "] == ("space", False))
    check("satir sonu", I._RAW_ASCII["\n"] == ("return", False))
    check(
        "Turkce karakterler ham tabloda YOK (pano yolu sart)",
        not any(c in I._RAW_ASCII for c in "ışğüöçİŞĞÜÖÇ"),
    )
    check(
        "ham tablodaki her tus, tus tablosunda tanimli",
        all(name in I.KEY_NAMES for name, _ in I._RAW_ASCII.values()),
    )


# =============================================================== GUVENLIK KAPI
def _gate(tmp: Path, **kw) -> S.SafetyGate:
    return S.SafetyGate(FakeCfg(tmp, **kw))


def test_gate_disabled() -> None:
    section("7. [desktop] enabled = false iken her sey reddedilir")
    with tempfile.TemporaryDirectory() as td:
        g = _gate(Path(td), enabled=False)
        g.unlock(10)  # izin verilse bile
        d = g.check("mouse")
        check("reddedildi", not d.allowed)
        check("gerekce nasil acilacagini soyluyor", "enabled = true" in d.reason, d.reason)
        check("status_line kapali diyor", "kapali" in g.status_line(), g.status_line())


def test_gate_permission_window() -> None:
    section("8. Sureli izin")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        g = _gate(tmp, enabled=True, idle_guard_seconds=0)
        check("baslangicta kilitli", not g.is_unlocked())
        d = g.check("mouse")
        check("izinsiz reddedilir", not d.allowed)
        check("gerekce desktop_unlock'u isaret ediyor", "desktop_unlock" in d.reason, d.reason)

        msg = g.unlock(5)
        check("unlock mesaji dakikayi soyluyor", "5 dakika" in msg, msg)
        check("izin acildi", g.is_unlocked())
        check("kalan sure makul", 290 <= g.remaining_seconds() <= 300, str(g.remaining_seconds()))
        check("izinli cagri geciyor", g.check("mouse").allowed)

        check("tavan uygulaniyor", "120 dakika" in g.unlock(9999), g.unlock(9999))
        check("taban uygulaniyor", "1 dakika" in g.unlock(0), g.unlock(0))

        # Izin DISKTE: yeni bir SafetyGate ornegi ayni izni gormeli
        g.unlock(5)
        g2 = _gate(tmp, enabled=True, idle_guard_seconds=0)
        check("izin servis yeniden baslasa da yasiyor (diskte)", g2.is_unlocked())

        check("lock kapatiyor", "kapatildi" in g.lock())
        check("kapandiktan sonra reddediliyor", not g.check("mouse").allowed)

        # Suresi gecmis izin
        g._write_state({"until": time.time() - 1})
        check("suresi dolan izin gecersiz", not g.is_unlocked())


def test_gate_locked_screen_and_idle() -> None:
    section("9. Ekran kilidi ve kullanici cakismasi")
    with tempfile.TemporaryDirectory() as td:
        g = _gate(Path(td), enabled=True, idle_guard_seconds=60)
        g.unlock(10)

        real_lock, real_idle = S.screen_locked, S.idle_ms
        try:
            S.screen_locked = lambda: True  # type: ignore[assignment]
            S.idle_ms = lambda: 999_000  # type: ignore[assignment]
            d = g.check("keyboard")
            check("ekran kilitliyken reddedilir", not d.allowed)
            check("gerekce kilit diyor", "kilitli" in d.reason, d.reason)
            check("force ile bile gecmez", not g.check("keyboard", force=True).allowed)

            S.screen_locked = lambda: False  # type: ignore[assignment]
            S.idle_ms = lambda: 5_000  # 5 sn once dokunulmus
            d = g.check("mouse")
            check("kullanici makinedeyken yazma reddedilir", not d.allowed)
            check("gerekce force'u soyluyor", "force=true" in d.reason, d.reason)
            check("force=true ile geciyor", g.check("mouse", force=True).allowed)
            check("okuma eylemi idle'dan etkilenmez", g.check("x", write=False).allowed)

            S.idle_ms = lambda: 120_000  # 2 dakikadir bos
            check("kullanici uzaktaysa force'suz geciyor", g.check("mouse").allowed)

            S.screen_locked = lambda: None  # D-Bus okunamadi
            S.idle_ms = lambda: None
            check("D-Bus okunamiyorsa kapi kapanmiyor", g.check("mouse").allowed)
        finally:
            S.screen_locked, S.idle_ms = real_lock, real_idle


def test_gate_rate_limit() -> None:
    section("10. Hiz siniri")
    with tempfile.TemporaryDirectory() as td:
        g = _gate(Path(td), enabled=True, idle_guard_seconds=0, max_actions_per_second=3)
        g.unlock(10)
        results = [g.check("mouse").allowed for _ in range(5)]
        check("ilk 3 gecti", results[:3] == [True, True, True], str(results))
        check("4. ve 5. reddedildi", results[3:] == [False, False], str(results))
        check("gerekce sayiyi soyluyor", "3" in g.check("mouse").reason, g.check("mouse").reason)

        g2 = _gate(Path(td), enabled=True, idle_guard_seconds=0, max_actions_per_second=0)
        g2.unlock(10)
        check("0 = sinirsiz", all(g2.check("mouse").allowed for _ in range(20)))


def test_audit_log() -> None:
    section("11. Denetim kaydi")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        g = _gate(tmp, enabled=True, idle_guard_seconds=0)
        g.unlock(7, reason="test")
        g.audit("mouse", action="click", x=10, y=20, forced=None)
        lines = (tmp / "audit.log").read_text(encoding="utf-8").strip().splitlines()
        check("iki satir yazildi", len(lines) == 2, str(len(lines)))
        rec = json.loads(lines[0])
        check("unlock kaydedildi", rec["event"] == "desktop_unlock", str(rec))
        check("dakika kaydedildi", rec["minutes"] == 7, str(rec))
        check("gerekce kaydedildi", rec["reason"] == "test", str(rec))
        rec2 = json.loads(lines[1])
        check("eylem kaydedildi", rec2["event"] == "mouse" and rec2["action"] == "click", str(rec2))
        check("None alanlar yazilmiyor", "forced" not in rec2, str(rec2))
        check("zaman damgasi var", rec2["ts"].startswith("20"), str(rec2))


def test_config_defaults() -> None:
    section("12. Yapilandirma varsayilanlari")
    d = DesktopSpec()
    check("enabled VARSAYILAN FALSE", d.enabled is False)
    check("izin varsayilani 15 dk", d.unlock_default_minutes == 15)
    check("izin tavani 120 dk", d.unlock_max_minutes == 120)
    check("idle korumasi 60 sn", d.idle_guard_seconds == 60)
    check("pano geri yukleme acik", d.restore_clipboard is True)

    from pcbridge.config import load_config

    cfg = load_config(str(ROOT / "config.example.toml"))
    check("ornek config'de [desktop] var", cfg.desktop is not None)
    check("ornek config'de enabled = false", cfg.desktop.enabled is False)
    check("ornek config'de tavan >= varsayilan",
          cfg.desktop.unlock_max_minutes >= cfg.desktop.unlock_default_minutes)


# ============================================================ EKRAN GORUNTUSU
def _synthetic_canvas(path: Path) -> None:
    """3840x1080 tuval: sol yari kirmizi, sag yari mavi, koselerde isaretci.

    Kirpmanin DOGRU kutudan geldigini renk bakarak anlayabilmek icin.
    """
    from PIL import Image

    img = Image.new("RGB", (3840, 1080), (255, 0, 0))
    for x in range(1920, 3840):
        for y in (0, 1079):
            img.putpixel((x, y), (0, 0, 255))
    img.paste(Image.new("RGB", (1920, 1080), (0, 0, 255)), (1920, 0))
    # her monitorun sol ust kosesine benzersiz bir isaretci
    img.putpixel((0, 0), (0, 255, 0))  # monitor 1
    img.putpixel((1920, 0), (255, 255, 0))  # monitor 2
    img.save(path, format="PNG")


def test_capture_scaling() -> None:
    """Olcekleme KIRPMADAN SONRA yapilmali."""
    section("13. Ekran goruntusu — olcekleme")
    from pcbridge.desktop import capture as C

    check("1920x1080 -> 1280 uzun kenar", C._scaled_size(1920, 1080, 1280) == (1280, 720),
          str(C._scaled_size(1920, 1080, 1280)))
    # Tuvalin tamami tek parca kucultulseydi bu cikardi -- okunmaz. Testin
    # varlik sebebi: kirpma sirasi bozulursa burasi yakalasin.
    check("3840x1080 tek parca kucultulurse okunmaz olur",
          C._scaled_size(3840, 1080, 1280) == (1280, 360),
          str(C._scaled_size(3840, 1080, 1280)))
    check("zaten kucukse buyutmez", C._scaled_size(800, 600, 1280) == (800, 600),
          str(C._scaled_size(800, 600, 1280)))
    check("0 = olcekleme yok", C._scaled_size(1920, 1080, 0) == (1920, 1080),
          str(C._scaled_size(1920, 1080, 0)))
    check("dikey goruntude uzun kenar yukseklik",
          C._scaled_size(1080, 1920, 1280) == (720, 1280),
          str(C._scaled_size(1080, 1920, 1280)))


def test_capture_crop_offsets() -> None:
    """Her kirpma dogru kutudan gelmeli ve global ofsetini tasimali."""
    section("14. Ekran goruntusu — kirpma ve ofset")
    from PIL import Image

    from pcbridge.desktop import capture as C

    mons = M._ordered(TWO_SCREENS)
    real_list, real_avail, real_grab = M.list_monitors, C.available, C._grab_canvas
    tmp = Path(tempfile.mkdtemp(prefix="pcb-cap-"))
    try:
        M.list_monitors = lambda *a, **k: mons
        C.available = lambda: (True, "")
        C._grab_canvas = lambda td, ptr: (_synthetic_canvas(td / "c.png") or (td / "c.png"))

        shots = C.capture("all", out_dir=tmp, scale_long_edge=0)
        check("monitor=all her ekran icin bir goruntu", len(shots) == 2, str(len(shots)))
        by_idx = {s.monitor.index: s for s in shots}
        check("1 numara solda, ofset (0,0)", by_idx[1].offset == (0, 0), str(by_idx[1].offset))
        check("2 numara sagda, ofset (1920,0)", by_idx[2].offset == (1920, 0),
              str(by_idx[2].offset))
        check("kirpilmis boyut monitor boyutu", by_idx[1].size == (1920, 1080),
              str(by_idx[1].size))

        # Renk isaretcisi: kirpma gercekten dogru kutudan mi geldi?
        with Image.open(by_idx[1].path) as im:
            check("1 numaranin sol ust kosesi kendi isaretcisi",
                  im.getpixel((0, 0)) == (0, 255, 0), str(im.getpixel((0, 0))))
            check("1 numara kirmizi bolgeden geldi",
                  im.getpixel((500, 500)) == (255, 0, 0), str(im.getpixel((500, 500))))
        with Image.open(by_idx[2].path) as im:
            check("2 numaranin sol ust kosesi kendi isaretcisi",
                  im.getpixel((0, 0)) == (255, 255, 0), str(im.getpixel((0, 0))))
            check("2 numara mavi bolgeden geldi",
                  im.getpixel((500, 500)) == (0, 0, 255), str(im.getpixel((500, 500))))

        # Ayni saniyede iki yakalama BIRBIRINI EZMEMELI. Bu bir kez oldu:
        # dosya adi saniye cozunurluklu damgadan uretiliyordu ve yayimlanmis
        # eski bir /shot baglantisi daha yeni goruntuyu gostermeye basliyordu.
        a = C.capture(1, out_dir=tmp, scale_long_edge=0)[0]
        b = C.capture(1, out_dir=tmp, scale_long_edge=0)[0]
        check("ayni saniyedeki iki yakalama ayri dosya", a.path != b.path,
              f"{a.path.name} == {b.path.name}")

        # Ofset + olcek zinciri: goruntudeki nokta -> global koordinat
        s2 = C.capture(2, out_dir=tmp, scale_long_edge=1280)[0]
        check("olcek 1280/1920", abs(s2.scale - 1280 / 1920) < 1e-9, str(s2.scale))
        check("olcekli goruntude (0,0) -> monitorun sol ustu",
              s2.to_global(0, 0) == (1920, 0), str(s2.to_global(0, 0)))
        check("olcekli goruntude (640,360) -> tuval ortasi civari",
              s2.to_global(640, 360) == (2880, 540), str(s2.to_global(640, 360)))
        check("tek monitor istenince tek goruntu",
              len(C.capture("DP-2", out_dir=tmp, scale_long_edge=0)) == 1)

        # Tuval boyutu monitor tablosuyla uyusmazsa sessizce yanlis yerden
        # kirpmak yerine patlamali.
        M.list_monitors = lambda *a, **k: M._ordered(ODD_SCREENS)
        try:
            C.capture("all", out_dir=tmp, scale_long_edge=0)
            check("tuval/tablo uyusmazligi yakalaniyor", False, "hata firlatilmadi")
        except C.CaptureError as exc:
            check("tuval/tablo uyusmazligi yakalaniyor", "degismis olabilir" in str(exc),
                  str(exc)[:80])
    finally:
        M.list_monitors, C.available, C._grab_canvas = real_list, real_avail, real_grab
        import shutil as _sh

        _sh.rmtree(tmp, ignore_errors=True)


def test_shot_store() -> None:
    """Token deposu: sure, tekrar okuma, temizlik."""
    section("15. Ekran goruntusu — baglanti token'lari")
    import os

    from pcbridge.shots import ShotStore

    tmp = Path(tempfile.mkdtemp(prefix="pcb-shots-"))
    try:
        cfg = FakeCfg(tmp)
        cfg.public_url = "https://ornek.invalid/"
        store = ShotStore(cfg)

        png = store.dir / "a.png"
        png.write_bytes(b"\x89PNG")
        token, url = store.publish(png)
        check("token 128 bit (22 karakter urlsafe)", len(token) == 22, str(len(token)))
        check("url sonunda .png", url.endswith(".png"), url)
        check("url'de cift egik cizgi yok", "//shot" not in url, url)
        check("token cozuluyor", store.resolve(token) == png)
        # TEK KULLANIMLIK DEGIL: telefon tarayicisi yenileme/geri tusunda
        # ikinci bir istek atiyor, tek kullanim goruntuyu yakiyordu.
        check("ayni token ikinci kez de cozuluyor", store.resolve(token) == png)
        check("gecersiz token None", store.resolve("yok") is None)

        # Suresi dolmus token
        with store._lock:
            entry = store._entries[token]
            store._entries[token] = type(entry)(path=entry.path, expires_at=time.time() - 1)
        check("suresi dolmus token None", store.resolve(token) is None)
        check("suresi dolan token kayittan dusuyor", token not in store._entries)

        # Dosya elle silinmisse de None donmeli
        png2 = store.dir / "b.png"
        png2.write_bytes(b"x")
        t2, _ = store.publish(png2)
        png2.unlink()
        check("dosyasi silinmis token None", store.resolve(t2) is None)

        # sweep: eski PNG'ler siliniyor, yenisi duruyor
        old = store.dir / "eski.png"
        old.write_bytes(b"x")
        os.utime(old, (0, 0))
        fresh = store.dir / "yeni.png"
        fresh.write_bytes(b"x")
        store.sweep()
        check("sweep eski PNG'yi sildi", not old.exists())
        check("sweep yeni PNG'ye dokunmadi", fresh.exists())

        # shot_keep_hours = 0 -> dosya temizligi kapali
        cfg0 = FakeCfg(tmp, shot_keep_hours=0)
        cfg0.public_url = "https://ornek.invalid"
        store0 = ShotStore(cfg0)
        old2 = store0.dir / "eski2.png"
        old2.write_bytes(b"x")
        os.utime(old2, (0, 0))
        store0.sweep()
        check("keep_hours = 0 iken dosya silinmiyor", old2.exists())
    finally:
        import shutil as _sh

        _sh.rmtree(tmp, ignore_errors=True)


def test_capture_config_defaults() -> None:
    section("16. Ekran goruntusu — ornek yapilandirma")
    from pcbridge.config import load_config

    d = load_config(str(ROOT / "config.example.toml")).desktop
    check("uzun kenar 1280", d.screenshot_scale_long_edge == 1280,
          str(d.screenshot_scale_long_edge))
    check("baglanti omru 5 dakika", d.shot_ttl_seconds == 300, str(d.shot_ttl_seconds))
    check("dosyalar 24 saat tutuluyor", d.shot_keep_hours == 24, str(d.shot_keep_hours))
    check("imlec varsayilan olarak dahil", d.include_pointer is True, str(d.include_pointer))


def test_real_capture() -> None:
    """Gercek gnome-screenshot. Varsayilan olarak KOSMAZ.

    Grafik oturum gerektirdigi icin (ve kullanicinin ekranini diske yazdigi
    icin) yalnizca PCBRIDGE_TEST_CAPTURE=1 verilince kosar.
    """
    import os

    if os.environ.get("PCBRIDGE_TEST_CAPTURE") != "1":
        return
    section("17. Ekran goruntusu — GERCEK yakalama")
    from pcbridge.desktop import capture as C

    ok, why = C.available()
    check("yakalama hazir", ok, why)
    if not ok:
        return
    tmp = Path(tempfile.mkdtemp(prefix="pcb-real-"))
    try:
        shots = C.capture("all", out_dir=tmp)
        check("en az bir goruntu", len(shots) >= 1, str(len(shots)))
        for s in shots:
            check(f"{s.label}: dosya yazildi", s.path.exists() and s.path.stat().st_size > 0)
            check(f"{s.label}: ofset var", s.offset is not None)
    finally:
        import shutil as _sh

        _sh.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("\033[1mMasaustu katmani testleri\033[0m (girdi GONDERILMEZ)")
    test_monitor_ordering()
    test_monitor_resolve()
    test_coordinate_offset()
    test_monitor_parsers()
    test_key_names()
    test_raw_ascii_table()
    test_gate_disabled()
    test_gate_permission_window()
    test_gate_locked_screen_and_idle()
    test_gate_rate_limit()
    test_audit_log()
    test_config_defaults()
    test_capture_scaling()
    test_capture_crop_offsets()
    test_shot_store()
    test_capture_config_defaults()
    test_real_capture()
    print(f"\n\033[1m{ok_count} gecti, {fail_count} kaldi\033[0m")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
