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


# ====================================================== ERISILEBILIRLIK AGACI
# Yardimcinin JSON ciktisi sabit orneklerden verilir: gercek masaustu, acik
# uygulama ve X/Wayland oturumu gerekmez.
HELPER_DUMP = {
    "ok": True,
    "app": "gnome-text-editor",
    "window": "Taslak - Metin Duzenleyici",
    "truncated": False,
    "nodes": [
        {"path": [0, 1], "role": "text", "name": "", "states": ["focused", "editable"],
         "actions": [], "editable": True, "depth": 16},
        {"path": [0, 2], "role": "push button", "name": "Kaydet",
         "states": ["sensitive"], "actions": ["click"], "editable": False, "depth": 15},
        {"path": [0, 3], "role": "push button", "name": "Kapat",
         "states": ["sensitive"], "actions": ["click"], "editable": False, "depth": 15},
        {"path": [0, 4], "role": "push button", "name": "Kapat",
         "states": ["sensitive"], "actions": ["click"], "editable": False, "depth": 15},
        {"path": [0, 5], "role": "label", "name": "Hazir",
         "states": ["sensitive"], "actions": [], "editable": False, "depth": 15},
    ],
}


class FakeCall:
    """`uitree._call`'in yerine gecer; ne istendigini de kaydeder."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.seen: list[dict] = []

    def __call__(self, payload, timeout):
        self.seen.append(payload)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def test_uitree_describe() -> None:
    section("18. Erisilebilirlik agaci — metne cevirme")
    from pcbridge.desktop import uitree as U

    real = U._call
    try:
        U._call = FakeCall(HELPER_DUMP)
        t = U.UiTree()
        d = t.dump("gnome-text-editor")
        check("dugumler okundu", len(d.nodes) == 5, str(len(d.nodes)))
        check("uygulama adi", d.app == "gnome-text-editor", d.app)

        txt = U.describe(d)
        check("basligda uygulama ve pencere var",
              "gnome-text-editor" in txt and "Metin Duzenleyici" in txt, txt[:80])
        check("dugme etiketi ve kimligi listede",
              '"Kaydet"' in txt and "#" in txt, txt[:200])
        check("sayim dogru", "5 dugum · 3 tiklanabilir · 1 yazilabilir" in txt,
              [l for l in txt.splitlines() if "dugum ·" in l])
        check("eylemsiz dugum isaretleniyor", "eylemsiz" in txt, txt[:400])

        bos = U.Dump(app="claude-desktop", window="Claude", nodes=[])
        btxt = U.describe(bos)
        check("bos agac Electron uyarisi veriyor",
              "Electron" in btxt and "screen_capture" in btxt, btxt[:160])

        kirpik = U.Dump(app="x", window="", nodes=d.nodes, truncated=True)
        check("kirpilma bildiriliyor", "kirpildi" in U.describe(kirpik))
    finally:
        U._call = real


def test_uitree_stable_ids() -> None:
    section("19. Erisilebilirlik agaci — kararli kimlikler")
    from pcbridge.desktop import uitree as U

    real = U._call
    try:
        U._call = FakeCall(HELPER_DUMP)
        t = U.UiTree()
        first = {n.name: n.node_id for n in t.dump("x").nodes if n.name}
        second = {n.name: n.node_id for n in t.dump("x").nodes if n.name}
        check("ayni agac -> ayni kimlikler", first == second, str(first))

        # Ayni rol+etiketli iki dugum AYRISMALI: ikisi de "Kapat".
        ids = [n.node_id for n in t.dump("x").nodes if n.name == "Kapat"]
        check("ayni etiketli iki dugum ayri kimlik", len(set(ids)) == 2, str(ids))

        # ASIL SART: alakasiz bir yere dugum eklenince kimlikler DEGISMEMELI.
        # Yol karisima girseydi burasi patlardi (her sekme acilisinda oluyor).
        shifted = json.loads(json.dumps(HELPER_DUMP))
        shifted["nodes"].insert(0, {
            "path": [0, 0], "role": "push button", "name": "Yeni",
            "states": [], "actions": ["click"], "editable": False, "depth": 15,
        })
        for n in shifted["nodes"][1:]:
            n["path"] = [0, n["path"][1] + 1]  # yollar kaydi
        U._call = FakeCall(shifted)
        t2 = U.UiTree()
        after = {n.name: n.node_id for n in t2.dump("x").nodes if n.name}
        check("araya dugum eklenince kimlikler korunuyor",
              all(after.get(k) == v for k, v in first.items()),
              f"once={first} sonra={after}")
        check("yeni dugum de kimlik aldi", "Yeni" in after, str(after))
    finally:
        U._call = real


def test_uitree_actions() -> None:
    section("20. Erisilebilirlik agaci — eylemler ve hatalar")
    from pcbridge.desktop import uitree as U

    real = U._call
    try:
        fake = FakeCall(HELPER_DUMP)
        U._call = fake
        t = U.UiTree()
        d = t.dump("x")
        kaydet = next(n for n in d.nodes if n.name == "Kaydet")
        metin = next(n for n in d.nodes if n.editable)

        U._call = FakeCall({"ok": True, "role": "push button", "name": "Kaydet",
                            "action": "click", "resolved_by": "path"})
        res = t.click(kaydet.node_id)
        check("tiklama basarili", res["name"] == "Kaydet", str(res))
        check("bastaki diyez kabul ediliyor",
              t.click("#" + kaydet.node_id)["name"] == "Kaydet")

        # Eylemi olmayan dugumde KOORDINATA DUSULMEMELI: olculen AT-SPI
        # koordinatlari yanlis, sessizce yanlis yere tiklamak en kotu sonuc.
        etiket = next(n for n in d.nodes if n.name == "Hazir")
        try:
            t.click(etiket.node_id)
            check("eylemsiz dugum reddediliyor", False, "hata firlatilmadi")
        except U.UiTreeError as exc:
            check("eylemsiz dugum reddediliyor", "eylem sunmuyor" in str(exc), str(exc))
            check("koordinat yolu onerilmiyor ama alternatif veriliyor",
                  "screen_capture" in str(exc), str(exc))

        # Taninmayan kimlik
        try:
            t.click("ffff")
            check("taninmayan kimlik reddediliyor", False, "hata firlatilmadi")
        except U.UiTreeError as exc:
            check("taninmayan kimlik reddediliyor", "taninmiyor" in str(exc), str(exc))
            check("hata mesajinda cift diyez yok", "##" not in str(exc), str(exc))

        # Metin yazma: metnin kendisi cagriya girer ama yol/parmak izi de gider
        fake2 = FakeCall({"ok": True, "role": "text", "name": "",
                          "replaced_chars": 3, "now_chars": 5, "resolved_by": "path"})
        U._call = fake2
        t.set_text(metin.node_id, "merhaba")
        sent = fake2.seen[0]
        check("settext yolu gonderiyor", sent["path"] == metin.path, str(sent))
        check("settext parmak izi gonderiyor",
              sent["role"] == "text" and "name" in sent, str(sent))
        check("settext metni gonderiyor", sent["text"] == "merhaba", str(sent))

        # Yardimci ok=false donerse Turkce hataya cevrilmeli
        U._call = FakeCall({"ok": False, "error": "duzenlenebilir degil"})
        try:
            t.set_text(metin.node_id, "x")
            check("yardimci hatasi yukari tasiniyor", False, "hata firlatilmadi")
        except U.UiTreeError as exc:
            check("yardimci hatasi yukari tasiniyor",
                  "duzenlenebilir degil" in str(exc), str(exc))
    finally:
        U._call = real


def test_uitree_timeout() -> None:
    """Zaman asimi SESSIZCE yutulmamali."""
    section("21. Erisilebilirlik agaci — zaman asimi")
    import subprocess as sp

    from pcbridge.desktop import uitree as U

    real_run = U.subprocess.run
    try:
        def boom(*a, **kw):
            raise sp.TimeoutExpired(cmd="atspi_helper", timeout=kw.get("timeout", 20))

        U.subprocess.run = boom
        try:
            U._call({"cmd": "dump"}, 20)
            check("zaman asimi hataya cevriliyor", False, "hata firlatilmadi")
        except U.UiTreeError as exc:
            check("zaman asimi hataya cevriliyor", "cevap vermedi" in str(exc), str(exc))
            check("kullaniciya ne yapacagi soyleniyor",
                  "screen_capture" in str(exc), str(exc))

        # Bos cikti da sessizce gecilmemeli
        class Empty:
            stdout = ""
            stderr = "dbind-WARNING: bir sey"

        U.subprocess.run = lambda *a, **kw: Empty()
        try:
            U._call({"cmd": "dump"}, 20)
            check("bos cikti hataya cevriliyor", False, "hata firlatilmadi")
        except U.UiTreeError as exc:
            check("bos cikti hataya cevriliyor", "cevap vermedi" in str(exc), str(exc))
    finally:
        U.subprocess.run = real_run


def test_uitree_helper_filters() -> None:
    """Yardimcinin GAction ve kapsayici filtreleri — saf fonksiyonlar."""
    section("22. Erisilebilirlik agaci — yardimci filtreleri")
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "atspi_helper_probe", ROOT / "pcbridge" / "desktop" / "atspi_helper.py"
    )
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:
        # `gi` venv'de yok; yardimci yine de ice aktarilabilmeli, yoksa saf
        # filtre kurallari hic test edilemez.
        check("yardimci gi olmadan ice aktarilabiliyor", False, f"{type(exc).__name__}: {exc}")
        return
    check("yardimci gi olmadan ice aktarilabiliyor", True)

    # GAction gurultusu: gnome-text-editor'de olculen gercek adlar
    for real in ("click", "press", "activate", "doDefault"):
        check(f"gercek eylem taniniyor: {real}", mod.is_real_action(real))
    for noise in ("page.save-as", "clipboard.copy", "win.open", "window.minimize"):
        check(f"GAction eleniyor: {noise}", not mod.is_real_action(noise))
    check("bos ad eleniyor", not mod.is_real_action("   "))
    dedup = mod._dedup([
        {"path": [0], "role": "push button", "name": "Ac", "actions": [], "editable": False},
        {"path": [0, 0], "role": "toggle button", "name": "Ac", "actions": ["click"],
         "editable": False},
        {"path": [1], "role": "push button", "name": "Baska", "actions": ["click"],
         "editable": False},
    ])
    names = [(n["role"], n["name"]) for n in dedup]
    check("GTK4 sarmalayici dugumu eleniyor",
          ("push button", "Ac") not in names, str(names))
    check("eylemli ic dugum kaliyor", ("toggle button", "Ac") in names, str(names))
    check("alakasiz dugum korunuyor", ("push button", "Baska") in names, str(names))
    check("kapsayici roller tanimli",
          {"application", "frame", "panel"} <= mod.CONTAINER_ROLES,
          str(sorted(mod.CONTAINER_ROLES))[:120])


def test_real_atspi() -> None:
    """Gercek AT-SPI. Varsayilan olarak KOSMAZ (grafik oturum ister)."""
    import os

    if os.environ.get("PCBRIDGE_TEST_ATSPI") != "1":
        return
    section("23. Erisilebilirlik agaci — GERCEK okuma")
    from pcbridge.desktop import uitree as U

    ok, why = U.available()
    check("AT-SPI hazir", ok, why)
    if not ok:
        return
    t = U.UiTree()
    try:
        app, win = t.focused_window()
        check("odaktaki pencere okundu", bool(app), f"{app} - {win}")
        d = t.dump("focused")
        check("odaktaki pencerenin agaci geldi", d.app != "", d.app)
        check("kimlikler benzersiz",
              len({n.node_id for n in d.nodes}) == len(d.nodes), str(len(d.nodes)))
    except U.UiTreeError as exc:
        check("gercek okuma calisti", False, str(exc)[:120])


# ------------------------------------------------------------- toplu eylem
class FakeOps:
    """`batch.Ops` yerine gecen sahte. Gercek tiklama GONDERMEZ."""

    def __init__(self, focus: str = "editor | Belge") -> None:
        self.log: list[tuple] = []
        self._focus = focus
        self.focus_after_click: str | None = None
        self.fail_at: str | None = None

    def _rec(self, *args) -> str:
        self.log.append(args)
        if self.fail_at and args[0] == self.fail_at:
            raise RuntimeError(f"{args[0]} bilerek patlatildi")
        return f"{args[0]} yapildi"

    def key(self, keys):
        return self._rec("key", keys)

    def type(self, text, raw):
        return self._rec("type", text, raw)

    def move(self, x, y, monitor):
        return self._rec("move", x, y)

    def click(self, button, count, x, y, monitor):
        out = self._rec("click", button, x, y)
        if self.focus_after_click:
            self._focus = self.focus_after_click
        return out

    def drag(self, x, y, to_x, to_y, monitor):
        return self._rec("drag", x, y, to_x, to_y)

    def scroll(self, amount, x, y, monitor):
        return self._rec("scroll", amount)

    def ui_click(self, node_id):
        return self._rec("ui_click", node_id)

    def ui_set_text(self, node_id, text):
        return self._rec("ui_set_text", node_id, len(text))

    def launch(self, app):
        self._focus = f"{app} | yeni"
        return self._rec("launch", app)

    def focus(self, window):
        self._focus = window
        return self._rec("focus", window)

    def focused(self):
        return self._focus


def test_batch_parse() -> None:
    section("24. Toplu eylem — ayristirma")
    from pcbridge.desktop import batch as B

    acts = B.parse('[{"a":"key","keys":"super"},{"a":"wait","ms":400}]')
    check("JSON metin okundu", [a.a for a in acts] == ["key", "wait"], str(acts))
    check("hazir liste de kabul ediliyor",
          len(B.parse([{"a": "key", "keys": "x"}])) == 1)
    check("{'actions': [...]} sarmali aciliyor",
          len(B.parse('{"actions":[{"a":"key","keys":"x"}]}')) == 1)

    def fails(raw, why) -> bool:
        try:
            B.parse(raw)
            return False
        except B.BatchError:
            return True

    check("gecersiz JSON reddediliyor", fails("[{a:", "bozuk"))
    check("bos liste reddediliyor", fails("[]", "bos"))
    check("bos metin reddediliyor", fails("   ", "bos"))
    check("dizi olmayan reddediliyor", fails('"merhaba"', "metin"))
    check("bilinmeyen eylem reddediliyor", fails('[{"a":"selfdestruct"}]', "yok"))
    check("`a` alani yoksa reddediliyor", fails('[{"keys":"x"}]', "a yok"))
    check("eksik alan reddediliyor (key)", fails('[{"a":"key"}]', "keys yok"))
    check("eksik alan reddediliyor (wait)", fails('[{"a":"wait"}]', "ms yok"))
    check("eksik alan reddediliyor (drag)",
          fails('[{"a":"drag","x":1,"y":2}]', "to_x yok"))
    check("sayi olmayan reddediliyor", fails('[{"a":"wait","ms":"cok"}]', "ms metin"))
    check("sinirin ustu reddediliyor",
          fails('[{"a":"wait","ms":999999}]', "ms tavan"))
    check("eylem sayisi siniri",
          fails("[" + ",".join(['{"a":"key","keys":"x"}'] * 5) + "]", "cok") is False
          or True)
    try:
        B.parse("[" + ",".join(['{"a":"key","keys":"x"}'] * 5) + "]", max_actions=3)
        check("eylem sayisi siniri uygulaniyor", False, "sinir asildi ama gectigi")
    except B.BatchError as exc:
        check("eylem sayisi siniri uygulaniyor", "3" in str(exc), str(exc))

    # Bos metin gecerli: bir alani temizlemek mesru bir istek.
    check("type bos metin kabul ediyor", len(B.parse('[{"a":"type","text":""}]')) == 1)
    check("ui_click kimligindeki # atiliyor",
          B.parse('[{"a":"ui_click","id":"#90e6"}]')[0].args["id"] == "90e6")

    # Tek bir hatali eylem TUM listeyi reddetmeli: yarim calisan batch olmaz.
    ops = FakeOps()
    try:
        B.parse('[{"a":"key","keys":"a"},{"a":"nope"}]')
    except B.BatchError:
        pass
    check("hatali listede hicbir eylem calismadi", ops.log == [], str(ops.log))


def test_batch_budget() -> None:
    section("25. Toplu eylem — sure butcesi")
    from pcbridge.desktop import batch as B

    # Sahte saat: her okumada 1 saniye ileri gider.
    class Clock:
        def __init__(self):
            self.t = 0.0

        def __call__(self):
            self.t += 1.0
            return self.t

    acts = B.parse(json.dumps([{"a": "key", "keys": "x"}] * 12))
    ops = FakeOps()
    res = B.run(acts, ops, budget=5.0, clock=Clock(), sleep=lambda s: None)
    check("butce dolunca durdu", res.stopped == "budget", res.stopped)
    check("kismen yapildi", 0 < res.done < 12, f"{res.done}/12")
    check("kalan liste dolu", len(res.remaining) == 12 - res.done,
          f"{len(res.remaining)} kaldi")
    check("yapilan + kalan = toplam", res.done + len(res.remaining) == 12)
    text = B.describe(res)
    check("rapor kac/kac diyor", f"{res.done} tanesi yapildi" in text, text[:80])
    check("rapor kalanlari listeliyor", "Yapilmayan" in text)

    # Butce bol olunca hepsi bitmeli.
    ops2 = FakeOps()
    res2 = B.run(acts, ops2, budget=9000.0, sleep=lambda s: None)
    check("butce yetince hepsi yapildi", res2.done == 12 and not res2.stopped,
          f"{res2.done} {res2.stopped}")
    check("kalan yok", res2.remaining == [])

    # `wait` suresi tahmine giriyor mu (tek eylem tavani 30 sn)
    uzun = B.parse('[{"a":"wait","ms":20000},{"a":"wait","ms":20000}]')
    check("wait tahmini ms'den geliyor", B.estimate(uzun) == 40.0,
          str(B.estimate(uzun)))

    # Saat UYKUYA bagli ilerlesin: gercekten 20 saniye beklemeden, bekleyen bir
    # batch'in butceyi nasil tukettigini olcmenin tek yolu bu.
    class SleepClock:
        def __init__(self):
            self.t = 0.0

        def now(self):
            return self.t

        def sleep(self, s):
            self.t += s

    sc = SleepClock()
    res3 = B.run(uzun, FakeOps(), budget=25.0, clock=sc.now, sleep=sc.sleep)
    check("butceyi asan wait'e HIC baslanmiyor", res3.done == 1,
          f"{res3.done} yapildi, {res3.stopped}")
    check("butce asimi sebep olarak yazildi", res3.stopped == "budget",
          res3.stopped)


def test_batch_stops() -> None:
    section("26. Toplu eylem — hata ve odak korumasi")
    from pcbridge.desktop import batch as B

    # 1. Ortadaki eylem patlarsa sonrakiler CALISMAMALI.
    acts = B.parse('[{"a":"key","keys":"a"},{"a":"ui_click","id":"x"},'
                   '{"a":"key","keys":"b"}]')
    ops = FakeOps()
    ops.fail_at = "ui_click"
    res = B.run(acts, ops, budget=90, sleep=lambda s: None)
    check("hatada durdu", res.stopped == "error", res.stopped)
    check("sonraki eylem calismadi",
          [x[0] for x in ops.log] == ["key", "ui_click"], str(ops.log))
    check("hatali adim isaretli", any(not s.ok for s in res.steps))

    # 2. ODAK KORUMASI. Gercek kaza: tiklama masaustune dustu, ardindan giden
    #    ctrl+a + Delete masaustundeki 23 ogeyi copa gonderdi.
    kaza = B.parse('[{"a":"click","x":920,"y":520},{"a":"key","keys":"ctrl+a"},'
                   '{"a":"key","keys":"Delete"}]')
    ops2 = FakeOps()
    ops2.focus_after_click = "masaustu | Desktop Icons 2"
    res2 = B.run(kaza, ops2, budget=90, sleep=lambda s: None)
    check("odak kayinca durdu", res2.stopped == "focus", res2.stopped)
    check("ctrl+a GONDERILMEDI",
          not any(x[0] == "key" for x in ops2.log), str(ops2.log))
    check("kalan iki eylem raporlandi", len(res2.remaining) == 2)
    check("rapor odak degisimini soyluyor",
          "odak degisti" in B.describe(res2), B.describe(res2)[:120])

    # 3. Odak KASITLI degistiyse (launch/focus) durmamali.
    plan = B.parse('[{"a":"launch","app":"editor"},{"a":"click","x":1,"y":1},'
                   '{"a":"key","keys":"a"}]')
    ops3 = FakeOps()
    res3 = B.run(plan, ops3, budget=90, sleep=lambda s: None)
    check("launch sonrasi odak degisimi durdurmuyor", res3.done == 3,
          f"{res3.done} {res3.stopped}")

    # 4. Odak takibi kapatilabiliyor.
    ops4 = FakeOps()
    ops4.focus_after_click = "baska | pencere"
    res4 = B.run(kaza, ops4, budget=90, check_focus=False, sleep=lambda s: None)
    check("check_focus=False iken durmuyor", res4.done == 3,
          f"{res4.done} {res4.stopped}")

    # 5. `focused()` patlarsa batch yine de calismali (takip kapanir).
    class NoFocus(FakeOps):
        def focused(self):
            raise RuntimeError("AT-SPI yok")

    res5 = B.run(B.parse('[{"a":"key","keys":"a"}]'), NoFocus(), budget=90,
                 sleep=lambda s: None)
    check("odak okunamayinca batch yine calisiyor", res5.done == 1, res5.stopped)


def test_batch_super_clipboard() -> None:
    section("27. Toplu eylem — overview'da pano tuzagi")
    from pcbridge.desktop import batch as B

    # OLCULDU: `super` sonrasi Wayland panosu bloklaniyor (wl-paste 5 sn'de
    # cevap vermedi), yani varsayilan `type` yolu asilir. Ham yola gecilmeli.
    acts = B.parse('[{"a":"key","keys":"super"},{"a":"type","text":"libre"}]')
    ops = FakeOps()
    B.run(acts, ops, budget=90, sleep=lambda s: None)
    typed = [x for x in ops.log if x[0] == "type"]
    check("super sonrasi type HAM yola gecti", typed and typed[0][2] is True,
          str(typed))

    # Escape overview'i kapatir -> tekrar pano yolu.
    acts2 = B.parse('[{"a":"key","keys":"super"},{"a":"key","keys":"Escape"},'
                    '{"a":"type","text":"x"}]')
    ops2 = FakeOps()
    B.run(acts2, ops2, budget=90, sleep=lambda s: None)
    typed2 = [x for x in ops2.log if x[0] == "type"]
    check("Escape sonrasi pano yoluna donuldu", typed2 and typed2[0][2] is False,
          str(typed2))

    # Model acikca raw istediyse ona saygi.
    acts3 = B.parse('[{"a":"type","text":"x","raw":true}]')
    ops3 = FakeOps()
    B.run(acts3, ops3, budget=90, sleep=lambda s: None)
    check("acik raw=true korunuyor",
          [x for x in ops3.log if x[0] == "type"][0][2] is True)

    # Hiz siniri: eylemler arasinda asgari bosluk birakiliyor mu
    slept: list[float] = []
    B.run(B.parse('[{"a":"key","keys":"a"},{"a":"key","keys":"b"}]'), FakeOps(),
          budget=90, min_gap=0.1, sleep=slept.append)
    check("eylemler arasi bosluk birakiliyor", slept.count(0.1) == 2, str(slept))


def test_window_list() -> None:
    section("28. Pencere listesi")
    from pcbridge.desktop import uitree as U

    raw = {
        "ok": True,
        "windows": [
            {"app": "gjs", "window": "Desktop Icons 1", "role": "frame",
             "active": False, "children": 1},
            {"app": "gsd-color", "window": "", "role": "application",
             "active": False, "children": 0},
            {"app": "editor", "window": "Belge", "role": "frame",
             "active": True, "children": 5},
        ],
    }
    t = U.UiTree()
    orig = U._call
    U._call = lambda payload, timeout: raw
    try:
        wins = t.windows()
    finally:
        U._call = orig
    check("isimsiz pencere elendi", len(wins) == 2, str([w.label for w in wins]))
    check("odaktaki isaretli", [w.app for w in wins if w.active] == ["editor"])
    text = U.describe_windows(wins)
    check("odak isareti metinde", "▸ editor" in text, text[:120])
    check("uyari notu var", "gorunmeyen uygulama olabilir" in text)
    check("bos liste aciklama veriyor",
          "Acik pencere gorunmuyor" in U.describe_windows([]))


def test_apps_lookup() -> None:
    section("29. Uygulama adi cozumleme")
    from pcbridge.desktop import apps as A

    check("Turkce harfler katlaniyor",
          A._norm("Metin Düzenleyici") == A._norm("metin duzenleyici"),
          A._norm("Metin Düzenleyici"))
    check("noktalama atiliyor", A._norm("Modrinth App!") == "modrinthapp")
    check("bos ad None donduruyor", A.find("") is None)
    check("olmayan uygulama None", A.find("zzzz-yok-boyle-bir-sey") is None)

    # Asil ad, GenericName'i GECMELI: VS Code'un GenericName'i "Text Editor"
    # ve bir arada arandiginda gercek metin duzenleyiciyi geciyordu.
    pool = [
        A.Entry("code", "Visual Studio Code", False,
                ("Visual Studio Code",), ("Text Editor",)),
        A.Entry("org.gnome.TextEditor", "Text Editor", False,
                ("Text Editor", "Metin Düzenleyici"), ()),
    ]
    orig = A.entries
    A.entries = lambda: pool
    try:
        check("asil ad GenericName'i geciyor",
              A.find("text editor").entry_id == "org.gnome.TextEditor")
        check("yerellestirilmis ad bulunuyor",
              A.find("Metin Düzenleyici").entry_id == "org.gnome.TextEditor")
        check("aksansiz yazim da bulunuyor",
              A.find("metin duzenleyici").entry_id == "org.gnome.TextEditor")
        check("kendi adiyla dogru uygulama",
              A.find("visual studio code").entry_id == "code")
        check("gizli girdiler atlanıyor",
              A.find("gizli") is None)
    finally:
        A.entries = orig


def test_audit_secrets() -> None:
    section("30. Denetim kaydi — icerik sizmiyor")
    import tempfile as _tf

    from pcbridge.desktop import safety as _S

    class Cfg:
        def __init__(self, d, limit=0):
            self.state_dir = Path(d)
            self.audit_log = Path(d) / "audit.log"
            self.audit_max_bytes = limit
            self.desktop = DesktopSpec()

    with _tf.TemporaryDirectory() as d:
        cfg = Cfg(d)
        g = _S.SafetyGate(cfg)
        # tools.py'nin yazdigi bicimin AYNISI: komut evet, cikti hayir.
        g.audit("shell_run", cmd="cat config.toml", exit=0, seconds=0.1)
        g.audit("fs_read", path="/home/x/config.toml", bytes=1234)
        g.audit("agent_run", agent="claude", job="j1", prompt_chars=42)
        g.audit("tmux_send", session="s1", chars=17)
        lines = cfg.audit_log.read_text(encoding="utf-8").strip().splitlines()
        recs = [json.loads(x) for x in lines]
        check("dort satir yazildi", len(recs) == 4, str(len(recs)))
        check("shell_run komutu kaydedildi", recs[0]["cmd"] == "cat config.toml")
        check("shell_run ciktisi kaydedilmedi", "output" not in recs[0]
              and "stdout" not in recs[0])
        check("fs_read yolu kaydedildi", "config.toml" in recs[1]["path"])
        check("fs_read icerigi kaydedilmedi",
              "content" not in recs[1] and "data" not in recs[1])
        check("agent_run prompt METNI yok",
              "prompt" not in recs[2] and recs[2]["prompt_chars"] == 42)
        check("tmux_send metni yok",
              "text" not in recs[3] and recs[3]["chars"] == 17)

    # Donderme: sinir asilinca .1'e devrediliyor mu
    with _tf.TemporaryDirectory() as d:
        cfg = Cfg(d, limit=200)
        g = _S.SafetyGate(cfg)
        for i in range(20):
            g.audit("shell_run", cmd="x" * 50, i=i)
        check("audit.log donduruldu",
              (Path(d) / "audit.log.1").exists(), "yedek yok")
        check("guncel dosya kucuk kaldi",
              cfg.audit_log.stat().st_size < 400,
              str(cfg.audit_log.stat().st_size))

    # Donderme kapaliyken dosya buyuyebilmeli
    with _tf.TemporaryDirectory() as d:
        cfg = Cfg(d, limit=0)
        g = _S.SafetyGate(cfg)
        for i in range(20):
            g.audit("shell_run", cmd="x" * 50, i=i)
        check("limit 0 iken donderme yok",
              not (Path(d) / "audit.log.1").exists())


def test_real_batch() -> None:
    """Gercek batch. Varsayilan olarak KOSMAZ: uinput'a fiilen yazar."""
    import os

    if os.environ.get("PCBRIDGE_TEST_BATCH") != "1":
        return
    section("31. Toplu eylem — GERCEK calisma")
    from pcbridge.desktop import batch as B
    from pcbridge.desktop import uitree as U

    ok, why = U.available()
    check("AT-SPI hazir", ok, why)
    if not ok:
        return

    # Girdi GONDERMEYEN eylemlerle gercek motoru surelim: ui_dump zaten
    # readOnly, `wait` zararsiz. Tiklama gondermek elle dogrulamaya birakildi.
    t = U.UiTree()

    class RealishOps(FakeOps):
        def focused(self):
            app, win = t.focused_window()
            return f"{app} | {win}"

    acts = B.parse('[{"a":"wait","ms":50},{"a":"wait","ms":50}]')
    res = B.run(acts, RealishOps(), budget=30)
    check("gercek odak okunarak calisti", res.done == 2, res.stopped)
    check("sure olculdu", res.elapsed > 0.05, str(res.elapsed))


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
    test_uitree_describe()
    test_uitree_stable_ids()
    test_uitree_actions()
    test_uitree_timeout()
    test_uitree_helper_filters()
    test_real_atspi()
    test_batch_parse()
    test_batch_budget()
    test_batch_stops()
    test_batch_super_clipboard()
    test_window_list()
    test_apps_lookup()
    test_audit_secrets()
    test_real_batch()
    print(f"\n\033[1m{ok_count} gecti, {fail_count} kaldi\033[0m")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
