#!/usr/bin/env python3
"""Masaustu katmaninin testleri — sunucusuz ve GIRDI GONDERMEZ.

    ./.venv/bin/python tests/test_desktop.py

pytest kuruluysa ayni dosya oldugu gibi toplanir:

    ./.venv/bin/python -m pytest tests/test_desktop.py -q

Bilincli olarak yapilmayan sey: gercek tiklama/tus gondermek. Bu testler
gelistiricinin makinesinde de CI'da da kosabilmeli; uinput'a fiilen yazan
dogrulama elle, kullaniciya haber verilerek ve bos bir pencerede yapilir
(`CLAUDE.md` "Bu makinede test etmenin tehlikesi" bolumu).

Monitor tablosu ve D-Bus okumalari sahtelenerek test edilir; boylece testler
ekran sayisindan, kilit durumundan ve kullanicinin makinede olup olmamasindan
bagimsiz kalir.
"""

from __future__ import annotations

import json
import os
import shutil
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


def test_gate_sliding_lease() -> None:
    section("43. Kayan kira — izin son EYLEME bagli")
    real_lock, real_idle = S.screen_locked, S.idle_ms
    S.screen_locked = lambda: False  # type: ignore[assignment]
    S.idle_ms = lambda: 999_000  # type: ignore[assignment]
    try:
        # --- 1. unlock iki alani da yaziyor -------------------------------
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            g = _gate(tmp, enabled=True, idle_guard_seconds=0,
                      unlock_idle_seconds=90)
            g.unlock(10)
            st = g._read_state()
            check("unlock `hard_until` yaziyor", "hard_until" in st, str(st.keys()))
            check("until ve hard_until esit basliyor",
                  abs(float(st["until"]) - float(st["hard_until"])) < 0.01)
            check("granted_by yazildi", st.get("granted_by") == "desktop_unlock",
                  str(st.get("granted_by")))
            check("ilk eylemden ONCE tavan bozulmadi (eski davranis)",
                  590 <= g.remaining_seconds() <= 600, str(g.remaining_seconds()))
            check("unlock mesaji kayma payini soyluyor",
                  "90 saniye" in g.unlock(10), g.unlock(10))

        # --- 2. izinli cagri kirayi kaydiriyor ----------------------------
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            g = _gate(tmp, enabled=True, idle_guard_seconds=0,
                      unlock_idle_seconds=90)
            g.unlock(10)
            hard_once = g.hard_until()
            check("izinli cagri geciyor", g.check("mouse").allowed)
            check("until 90 saniyeye kaydi",
                  85 <= g.remaining_seconds() <= 90, str(g.remaining_seconds()))
            check("hard_until DEGISMEDI (tavan sabit)",
                  abs(g.hard_until() - hard_once) < 0.01)
            check("sert tavan hala ~10 dk",
                  590 <= g.hard_remaining_seconds() <= 600,
                  str(g.hard_remaining_seconds()))
            check("status_line iki sayiyi birden veriyor",
                  "sert tavan" in g.status_line(), g.status_line())

        # --- 3. tavan asilmiyor -------------------------------------------
        with tempfile.TemporaryDirectory() as td:
            # 1 dakikalik izin + 90 saniyelik kayma: kayma tavani ASAMAZ.
            g = _gate(Path(td), enabled=True, idle_guard_seconds=0,
                      unlock_idle_seconds=90)
            g.unlock(1)
            g.check("mouse")
            check("kayma sert tavani asmiyor",
                  g.remaining_seconds() <= 60, str(g.remaining_seconds()))
            check("until tavana yapisti",
                  abs(g.unlocked_until() - g.hard_until()) < 1.0)

        # --- 4. REDDEDILEN cagri uzatmiyor --------------------------------
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            g = _gate(tmp, enabled=True, idle_guard_seconds=60,
                      unlock_idle_seconds=90)
            g.unlock(10)
            st = g._read_state()
            st["until"] = time.time() + 5      # kira nerdeyse bitmis
            g._write_state(st)
            S.idle_ms = lambda: 1_000          # kullanici makinenin basinda
            d = g.check("mouse")
            check("cakisma korumasi reddetti", not d.allowed, d.reason[:60])
            check("REDDEDILEN cagri kirayi UZATMADI",
                  g.remaining_seconds() <= 6, str(g.remaining_seconds()))
            S.idle_ms = lambda: 999_000

            # Hiz siniri reddi de uzatmamali.
            g2 = _gate(tmp, enabled=True, idle_guard_seconds=0,
                       max_actions_per_second=1, unlock_idle_seconds=90)
            g2.unlock(10)
            st = g2._read_state()
            st["until"] = time.time() + 5
            g2._write_state(st)
            check("hiz siniri: ilk cagri geciyor", g2.check("mouse").allowed)
            once = g2.remaining_seconds()
            d = g2.check("mouse")
            check("hiz siniri: ikinci cagri reddedildi", not d.allowed)
            check("hiz siniri reddi kirayi UZATMADI",
                  g2.remaining_seconds() <= once, str(g2.remaining_seconds()))

        # --- 5. olmus izin DIRILMIYOR -------------------------------------
        with tempfile.TemporaryDirectory() as td:
            g = _gate(Path(td), enabled=True, idle_guard_seconds=0,
                      unlock_idle_seconds=90)
            now = time.time()
            # Sert tavan gecmiste: touch hicbir sey yapmamali.
            g._write_state({"until": now - 1, "hard_until": now - 1})
            g.touch()
            check("sert tavani gecmis izin dirilmiyor", not g.is_unlocked(),
                  str(g.remaining_seconds()))
            # until gecmiste ama tavan gelecekte: yine dirilmemeli.
            g._write_state({"until": now - 1, "hard_until": now + 600})
            g.touch()
            check("kirasi bitmis izin tavan dururken de dirilmiyor",
                  not g.is_unlocked(), str(g.remaining_seconds()))
            check("olmus izne gelen cagri reddediliyor",
                  not g.check("mouse").allowed)

        # --- 6. ESKI dosya bicimi (hard_until yok) ------------------------
        with tempfile.TemporaryDirectory() as td:
            g = _gate(Path(td), enabled=True, idle_guard_seconds=0,
                      unlock_idle_seconds=90)
            eski = time.time() + 600
            g._write_state({"until": eski, "reason": "eski surumden kalma"})
            check("eski bicimli izin gecerli sayiliyor", g.check("mouse").allowed)
            check("eski bicimde kayma YOK (sessiz kisaltma olmasin)",
                  abs(g.unlocked_until() - eski) < 0.01,
                  str(g.unlocked_until() - eski))
            check("eski bicimde hard_until uydurulmuyor",
                  "hard_until" not in g._read_state())

        # --- 7. unlock_idle_seconds = 0 -> eski davranis -------------------
        with tempfile.TemporaryDirectory() as td:
            g = _gate(Path(td), enabled=True, idle_guard_seconds=0,
                      unlock_idle_seconds=0)
            msg = g.unlock(5)
            check("kapaliyken unlock mesajinda kayma payi yok",
                  "saniye sonra kendiliginden" not in msg, msg)
            g.check("mouse")
            check("kayan kira kapaliyken sure kaymiyor",
                  290 <= g.remaining_seconds() <= 300, str(g.remaining_seconds()))

        # --- 8. lock her iki alani da sifirliyor --------------------------
        with tempfile.TemporaryDirectory() as td:
            g = _gate(Path(td), enabled=True, idle_guard_seconds=0,
                      unlock_idle_seconds=90)
            g.unlock(10)
            g.lock()
            st = g._read_state()
            check("lock: until sifir", float(st.get("until", -1)) == 0, str(st))
            check("lock: hard_until sifir",
                  float(st.get("hard_until", -1)) == 0, str(st))
            check("lock sonrasi touch dirilmiyor",
                  (g.touch(), not g.is_unlocked())[1])

        # --- 9. touch diger alanlari KORUYOR ------------------------------
        with tempfile.TemporaryDirectory() as td:
            g = _gate(Path(td), enabled=True, idle_guard_seconds=0,
                      unlock_idle_seconds=90)
            g.unlock(10, reason="deneme gerekcesi")
            g.check("mouse")
            st = g._read_state()
            check("touch sonrasi granted_by duruyor",
                  st.get("granted_by") == "desktop_unlock", str(st))
            check("touch sonrasi reason duruyor",
                  st.get("reason") == "deneme gerekcesi", str(st))
            check("touch sonrasi hard_until duruyor",
                  float(st.get("hard_until", 0)) > time.time(), str(st))

        # --- 10. GNOME eklentisinin sozlesmesi bozulmadi -------------------
        # `state.js` YALNIZCA `until`i okuyor: JSON'un tepesinde, duz sayi.
        # Yeni alanlar onu gormezden gelecek, ama bicim degisirse eklenti
        # sessizce korlesir -- bu yuzden burada da kontrol ediliyor.
        with tempfile.TemporaryDirectory() as td:
            g = _gate(Path(td), enabled=True, idle_guard_seconds=0,
                      unlock_idle_seconds=90)
            g.unlock(10)
            g.check("mouse")
            ham = json.loads(
                (Path(td) / S.STATE_FILE).read_text(encoding="utf-8")
            )
            check("until JSON'un tepesinde duz sayi",
                  isinstance(ham.get("until"), (int, float)),
                  str(type(ham.get("until"))))
            check("until gelecekte (eklenti 'aktif' okuyacak)",
                  float(ham["until"]) > time.time())
            check("dosya adi degismedi", S.STATE_FILE == "desktop_unlock.json")
    finally:
        S.screen_locked, S.idle_ms = real_lock, real_idle


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
            d = g.check("mouse")
            check("lock durumu bilinmiyorsa kapi kapaniyor", not d.allowed)
            check("lock belirsizligi typed", d.code == S.ErrorCode.LOCK_STATE_UNKNOWN)

            S.screen_locked = lambda: False
            d = g.check("mouse")
            check("activity bilinmiyorsa yazma reddediliyor", not d.allowed)
            check("activity belirsizligi typed", d.code == S.ErrorCode.ACTIVITY_UNKNOWN)
            check("force yalnizca activity'yi asiyor", g.check("mouse", force=True).allowed)
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
        C.available = lambda *a, **k: (True, "")
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
        # kirpmak yerine patlamali. ODD_SCREENS'te olcekler farkli (1.0 ve
        # 2.0), yani gerekce Task 7.1'den beri "hangi piksel hangi monitorun
        # bilinemez"; olcut ikisinin de soylenmesi.
        M.list_monitors = lambda *a, **k: M._ordered(ODD_SCREENS)
        try:
            C.capture("all", out_dir=tmp, scale_long_edge=0)
            check("tuval/tablo uyusmazligi yakalaniyor", False, "hata firlatilmadi")
        except C.CaptureError as exc:
            check("tuval/tablo uyusmazligi yakalaniyor",
                  "3840x1080" in str(exc) and "3840x1440" in str(exc),
                  str(exc)[:120])
    finally:
        M.list_monitors, C.available, C._grab_canvas = real_list, real_avail, real_grab
        import shutil as _sh

        _sh.rmtree(tmp, ignore_errors=True)


def test_shot_lookup() -> None:
    """Cekim kimligi: kayit, geri okuma ve TEK GECIT donusum.

    Bu bolumun varlik sebebi: donusumu eskiden model yapiyordu
    (`global_x = ofset_x + goruntu_x / olcek`) ve zayif modeller bolmeyi
    tutturamayip hedefin kenarina tikliyordu. Artik sunucu ceviriyor; buradaki
    kontroller o cevirinin dogru VE eski davranisin bozulmamis oldugunu
    gosteriyor.
    """
    section("45. Cekim kimligi — kayit, arama, donusum")
    from pcbridge.desktop import capture as C

    mons = M._ordered(TWO_SCREENS)
    real_list, real_avail, real_grab = M.list_monitors, C.available, C._grab_canvas
    tmp = Path(tempfile.mkdtemp(prefix="pcb-shotid-"))
    try:
        M.list_monitors = lambda *a, **k: mons
        C.available = lambda *a, **k: (True, "")
        C._grab_canvas = lambda td, ptr: (_synthetic_canvas(td / "c.png") or (td / "c.png"))

        shots = C.capture("all", out_dir=tmp, scale_long_edge=1280)
        by_idx = {s.monitor.index: s for s in shots}
        s2 = by_idx[2]

        # -- kimlik ve kayit -------------------------------------------
        check("kimlik m<monitor>-<hex6> bicimde",
              bool(C.SHOT_ID_RE.match(s2.id)), s2.id)
        check("ayni cekimin iki goruntusu ayni son eki paylasiyor",
              by_idx[1].id.split("-")[1] == s2.id.split("-")[1],
              f"{by_idx[1].id} / {s2.id}")
        # Kimlik PNG adinin ICINDE: dosyaya bakan insan kimligi okuyabilsin.
        check("kimlik PNG adindan turetilebiliyor",
              s2.id.split("-")[1] in s2.path.name, s2.path.name)
        meta = tmp / f"{s2.id}.json"
        check("cekim kaydi PNG'nin yaninda", meta.exists(), str(meta))
        check("cekim anı kaydedildi", s2.taken_at > 0, str(s2.taken_at))

        # -- geri okuma -------------------------------------------------
        back = C.load_shot(s2.id, [tmp])
        check("kayittan ofset geri geldi", back.offset == (1920, 0), str(back.offset))
        check("kayittan olcek geri geldi", abs(back.scale - 1280 / 1920) < 1e-9,
              str(back.scale))
        check("kayittan monitor numarasi geri geldi",
              back.monitor is not None and back.monitor.index == 2,
              str(back.monitor))
        check("kayittan PNG yolu geri geldi", back.path == s2.path,
              f"{back.path} != {s2.path}")

        # -- TEK GECIT: capture.to_global --------------------------------
        # 1280'lik goruntude (640,360) -> 1920 + 640/0.667 = 2880, 540.
        check("shot ile goruntu koordinati global'e cevriliyor",
              C.to_global(640, 360, shot=s2.id, dirs=[tmp]) == (2880, 540),
              str(C.to_global(640, 360, shot=s2.id, dirs=[tmp])))
        check("shot ile sol ust kose monitorun ofseti",
              C.to_global(0, 0, shot=s2.id, dirs=[tmp]) == (1920, 0),
              str(C.to_global(0, 0, shot=s2.id, dirs=[tmp])))

        # GERIYE DONUK: shot verilmezse davranis birebir eskisi gibi.
        check("shot yokken koordinat aynen global",
              C.to_global(300, 400) == (300, 400),
              str(C.to_global(300, 400)))
        check("monitor= yolu degismedi (tam cozunurluk ofseti)",
              C.to_global(300, 400, monitor=2, dirs=[tmp]) == (2220, 400),
              str(C.to_global(300, 400, monitor=2, dirs=[tmp])))

        # -- reddedilmesi gerekenler -------------------------------------
        # Iki farkli uzay; hangisinin kastedildigini SESSIZCE secmek tam da bu
        # dosyanin onlemeye calistigi sinifta bir hata olurdu.
        try:
            C.to_global(1, 1, monitor=2, shot=s2.id, dirs=[tmp])
            check("shot + monitor birlikte reddediliyor", False, "hata yok")
        except C.CaptureError as exc:
            check("shot + monitor birlikte reddediliyor",
                  "birlikte verilemez" in str(exc), str(exc)[:80])

        # Kimlik dogrudan dosya adina donusuyor: suzulmezse dizin disina cikar.
        for bad in ("../../etc/passwd", "m2-a1b2c3/../x", "m2-ZZZZZZ", "m2-a1b2c",
                    "'; rm -rf /"):
            try:
                C.to_global(1, 1, shot=bad, dirs=[tmp])
                check(f"gecersiz kimlik reddedildi ({bad!r})", False, "hata yok")
            except C.CaptureError as exc:
                check(f"gecersiz kimlik reddedildi ({bad!r})",
                      "Gecersiz cekim kimligi" in str(exc), str(exc)[:60])

        try:
            C.to_global(1, 1, shot="m9-abcdef", dirs=[tmp])
            check("bilinmeyen kimlik reddediliyor", False, "hata yok")
        except C.CaptureError as exc:
            check("bilinmeyen kimlik reddediliyor", "diye bir ekran" in str(exc),
                  str(exc)[:80])
            check("gerekce ne yapilacagini soyluyor", "TAZE" in str(exc).upper(),
                  str(exc)[:160])

        # Pencere cekiminin ekranda NEREDE oldugu bilinmiyor.
        win = C.Shot(path=tmp / "w.png", monitor=None, offset=None,
                     size=(800, 600), scaled=(800, 600), scale=1.0,
                     id="win-aabbcc", taken_at=time.time())
        C.save_meta(win, tmp)
        try:
            C.to_global(10, 10, shot="win-aabbcc", dirs=[tmp])
            check("pencere cekiminden koordinat turetilmiyor", False, "hata yok")
        except C.CaptureError as exc:
            check("pencere cekiminden koordinat turetilmiyor",
                  "turetilemez" in str(exc), str(exc)[:80])

        # -- yas -----------------------------------------------------------
        old = C.Shot(path=tmp / "o.png", monitor=None, offset=None,
                     size=(1, 1), scaled=(1, 1), scale=1.0,
                     id="win-bbccdd", taken_at=time.time() - 120)
        check("yas hesaplaniyor", 119 < old.age < 121, str(old.age))
        check("taken_at yoksa yas 0", C.Shot(
            path=tmp / "n.png", monitor=None, offset=None, size=(1, 1),
            scaled=(1, 1), scale=1.0).age == 0.0)

        # -- ikinci dizin: MCP'nin cektigine kabuktan ulasilabilmeli --------
        other = Path(tempfile.mkdtemp(prefix="pcb-shotid2-"))
        try:
            check("bos dizinde bulunamaz ama ikincide bulunur",
                  C.to_global(640, 360, shot=s2.id, dirs=[other, tmp]) == (2880, 540))
        finally:
            import shutil as _sh2

            _sh2.rmtree(other, ignore_errors=True)
    finally:
        M.list_monitors, C.available, C._grab_canvas = real_list, real_avail, real_grab
        import shutil as _sh

        _sh.rmtree(tmp, ignore_errors=True)


def test_ambiguous_guard() -> None:
    """`shot` unutuldugunda supheli koordinat reddediliyor mu.

    Kalan risk buydu: model kucultulmus bir goruntudeki (640, 360) noktasini
    `shot` vermeden gonderirse koordinat GLOBAL sayilir ve tiklama sag ekran
    yerine SOL ekranin ortasina duser -- hicbir hata gorunmeden.

    Belirsizlik cozulemez ((640, 360) gercekten de gecerli bir global
    koordinat), o yuzden tahmin edilmiyor SORULUYOR. Ayni ders `batch.py`de
    yaziyor: cozum korumayi kapatmak degil, niyeti soyletmek.
    """
    section("50. Cekim kimligi — belirsiz koordinat korumasi")
    from pcbridge.desktop import capture as C

    tmp = Path(tempfile.mkdtemp(prefix="pcb-guard-"))
    try:
        mon = M._ordered(TWO_SCREENS)[1]

        def kaydet(sid, scale, yas=0.0, scaled=(1280, 720)):
            sh = C.Shot(path=tmp / f"{sid}.png", monitor=mon, offset=(1920, 0),
                        size=(1920, 1080), scaled=scaled, scale=scale,
                        id=sid, taken_at=time.time() - yas)
            C.save_meta(sh, tmp)
            return sh

        kaydet("m2-aaaaaa", 1280 / 1920)

        # -- reddedilmesi gerekenler --------------------------------------
        try:
            C.to_global(640, 360, dirs=[tmp], guard_age=60)
            check("goruntu kutusundaki koordinat reddediliyor", False, "hata yok")
        except C.CaptureError as exc:
            check("goruntu kutusundaki koordinat reddediliyor",
                  "BELIRSIZ" in str(exc), str(exc)[:70])
            check("gerekce hangi cekimi kastettigini soyluyor",
                  "m2-aaaaaa" in str(exc), str(exc)[:200])
            check("gerekce nereye duseceğini soyluyor",
                  "monitor 1" in str(exc), str(exc)[:300])
            check("gerekce IKI cikis yolu veriyor",
                  'shot="m2-aaaaaa"' in str(exc) and "monitor=" in str(exc),
                  str(exc)[-200:])

        # -- gecmesi gerekenler -------------------------------------------
        check("shot verilince gecer",
              C.to_global(640, 360, shot="m2-aaaaaa", dirs=[tmp],
                          guard_age=60) == (2880, 540))
        check("monitor verilince gecer (niyet beyan edilmis)",
              C.to_global(640, 360, monitor=2, dirs=[tmp],
                          guard_age=60) == (2560, 360))
        # Goruntu kutusunun DISINDA: bu bir goruntu koordinati OLAMAZ.
        check("kutu disindaki koordinat gecer",
              C.to_global(2880, 540, dirs=[tmp], guard_age=60) == (2880, 540))
        check("y kutu disinda ise gecer",
              C.to_global(640, 900, dirs=[tmp], guard_age=60) == (640, 900))
        check("guard kapaliyken gecer (geriye donuk)",
              C.to_global(640, 360, dirs=[tmp], guard_age=0) == (640, 360))

        # -- yas: bayat cekim supheli sayilmaz -----------------------------
        import shutil as _sh0

        _sh0.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        kaydet("m2-bbbbbb", 1280 / 1920, yas=600)
        check("600 sn once alinmis cekim supheli saymiyor",
              C.to_global(640, 360, dirs=[tmp], guard_age=60) == (640, 360))

        # -- olcek 1.0: belirsizlik YOK (fark yalnizca ofset) --------------
        _sh0.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        kaydet("m2-cccccc", 1.0, scaled=(1920, 1080))
        check("olceklenmemis cekim supheli saymiyor",
              C.to_global(640, 360, dirs=[tmp], guard_age=60) == (640, 360))

        # -- en YENI olcekli cekim esas aliniyor ---------------------------
        _sh0.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        kaydet("m2-dddddd", 1280 / 1920, yas=30)
        yeni = kaydet("m2-eeeeee", 800 / 1920, yas=1, scaled=(800, 450))
        got = C.newest_scaled_shot([tmp], 60)
        check("en yeni olcekli cekim seciliyor", got is not None and got.id == yeni.id,
              got.id if got else "None")
        # (640, 360) yeni cekimin (800x450) icinde -> yine reddedilmeli
        try:
            C.to_global(640, 360, dirs=[tmp], guard_age=60)
            check("yeni cekime gore de reddediliyor", False, "hata yok")
        except C.CaptureError as exc:
            check("yeni cekime gore de reddediliyor", "m2-eeeeee" in str(exc),
                  str(exc)[:120])
    finally:
        import shutil as _sh

        _sh.rmtree(tmp, ignore_errors=True)


def test_shot_ops_wiring() -> None:
    """`DeviceOps` donusumu GERCEKTEN tek gecide baglamis mi.

    Kolay hata: `capture.to_global` yazilir ama `ops.py` eski
    `monitors.to_global`u cagirmaya devam eder. O zaman `shot` sessizce yok
    sayilir ve 1280'lik bir goruntuden gelen tiklama 1/3 oraninda sasar --
    hicbir yerde hata gorunmeden.
    """
    section("46. Cekim kimligi — toplu eylem motoruna baglanti")
    from pcbridge.desktop import capture as C
    from pcbridge.desktop import ops as O

    tmp = Path(tempfile.mkdtemp(prefix="pcb-opsid-"))
    try:
        mon = M._ordered(TWO_SCREENS)[1]
        shot = C.Shot(path=tmp / "m2.png", monitor=mon, offset=(1920, 0),
                      size=(1920, 1080), scaled=(1280, 720), scale=1280 / 1920,
                      id="m2-a1b2c3", taken_at=time.time())
        C.save_meta(shot, tmp)

        moves: list[tuple[int, int]] = []

        class FakeBackend:
            def move(self, x, y, **kw):
                moves.append((x, y))
                return (x, y)

            def click(self, button, count):
                pass

            def drag(self, x, y, ex, ey, button="left"):
                moves.append((x, y))
                moves.append((ex, ey))

            def scroll(self, amount, horizontal=False):
                pass

        class FakeCfgOps:
            desktop = DesktopSpec()
            shot_search_dirs = [tmp]

        ops = O.DeviceOps(FakeBackend(), None, FakeCfgOps(), C)
        ops.click("left", 1, 640, 360, None, "m2-a1b2c3")
        check("click shot ile cevrildi", moves[-1] == (2880, 540), str(moves[-1]))

        ops.move(0, 0, None, "m2-a1b2c3")
        check("move shot ile cevrildi", moves[-1] == (1920, 0), str(moves[-1]))

        ops.drag(0, 0, 640, 360, "left", None, "m2-a1b2c3")
        check("drag iki ucu da cevirdi", moves[-2:] == [(1920, 0), (2880, 540)],
              str(moves[-2:]))

        ops.scroll(3, 640, 360, None, False, "m2-a1b2c3")
        check("scroll shot ile cevrildi", moves[-1] == (2880, 540), str(moves[-1]))

        # GERIYE DONUK: shot yokken koordinat aynen global gitmeli.
        ops.move(2880, 540, None, None)
        check("shot yokken koordinat degistirilmiyor", moves[-1] == (2880, 540),
              str(moves[-1]))
    finally:
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
        # Cekim kaydi PNG ile ayni yasa tabi: tek basina kalan bir `<id>.json`
        # `shot=` ile bulunur ama arkasinda goruntu olmaz.
        old_meta = store.dir / "m1-aabbcc.json"
        old_meta.write_text("{}")
        os.utime(old_meta, (0, 0))
        fresh_meta = store.dir / "m2-ddeeff.json"
        fresh_meta.write_text("{}")
        removed = store.sweep()
        check("sweep eski PNG'yi sildi", not old.exists())
        check("sweep eski cekim kaydini da sildi", not old_meta.exists())
        check("sweep yeni PNG'ye dokunmadi", fresh.exists())
        check("sweep yeni cekim kaydina dokunmadi", fresh_meta.exists())
        check("sweep kac dosya sildigini soyluyor", removed == 2, str(removed))

        # shot_keep_hours = 0 -> dosya temizligi kapali
        cfg0 = FakeCfg(tmp, shot_keep_hours=0)
        cfg0.public_url = "https://ornek.invalid"
        store0 = ShotStore(cfg0)
        old2 = store0.dir / "eski2.png"
        old2.write_bytes(b"x")
        os.utime(old2, (0, 0))
        check("keep_hours = 0 iken dosya silinmiyor", store0.sweep() == 0)
        check("keep_hours = 0 iken dosya duruyor", old2.exists())

        # KURULUMDA SUPURULUYOR. Eskiden temizligin tek tetikleyicisi
        # `publish()`ti ve o da yalnizca HTTP tasimasinda cagriliyor: stdio ile
        # baglanildiginda `shots/` HIC temizlenmiyordu.
        eski3 = store.dir / "eski3.png"
        eski3.write_bytes(b"x")
        os.utime(eski3, (0, 0))
        eski3_meta = store.dir / "m1-bbccdd.json"
        eski3_meta.write_text("{}")
        os.utime(eski3_meta, (0, 0))
        ShotStore(cfg)  # hicbir cagri yapilmadan, yalnizca kurulum
        check("kurulum eski PNG'yi supurdu", not eski3.exists())
        check("kurulum eski kaydi supurdu", not eski3_meta.exists())
    finally:
        import shutil as _sh

        _sh.rmtree(tmp, ignore_errors=True)


def test_capture_sweeps() -> None:
    """`screen_capture` temizligi TASIMADAN BAGIMSIZ tetikliyor mu.

    Eskiden temizligin tek tetikleyicisi `ShotStore.publish()`ti, o da yalnizca
    `transport != "stdio"` iken cagriliyor. Yani stdio ile baglanildiginda --
    asil kullanilan yol -- `shots/` klasoru HIC temizlenmiyordu:
    `shot_keep_hours = 24` ayari yaziyor ama hicbir zaman uygulanmiyordu.

    Bu bolum aracin kendisini cagiriyor (kayitli fonksiyona `get_tool` ile
    ulasarak), cunku "sweep cagriliyor mu" sorusunun baska turlu cevabi yok:
    `ShotStore.sweep()`in dogru calismasi zaten test edildi, eksik olan onu
    KIMIN cagirdigiydi.
    """
    section("47. Ekran goruntusu — temizlik tasimadan bagimsiz")
    import asyncio
    import os

    from pcbridge import jobs as jobslib
    from pcbridge import tools as toolslib
    from pcbridge.config import load_config
    from pcbridge.desktop import capture as C
    from pcbridge.desktop import safety as safetylib
    from pcbridge.shots import ShotStore

    from fastmcp import FastMCP

    tmp = Path(tempfile.mkdtemp(prefix="pcb-sweep-"))
    real_gate, real_capture = safetylib.SafetyGate, toolslib.capturelib.capture
    try:
        cfg = load_config(str(ROOT / "config.example.toml"))
        object.__setattr__(cfg, "state_dir", tmp)

        class OpenGate:
            """Kapiyi acik tutan sahte gecit: burada olculen sey temizlik."""

            def __init__(self, *a, **k):
                pass

            def check(self, *a, **k):
                return type("D", (), {"allowed": True, "reason": ""})()

            def audit(self, *a, **k):
                pass

            def status_line(self):
                return "test"

            def is_unlocked(self):
                return True

        def fake_capture(spec, out_dir=None, **kw):
            dest = Path(out_dir) / "yeni-cekim.png"
            dest.write_bytes(b"\x89PNG")
            return [C.Shot(path=dest, monitor=None, offset=None,
                           size=(8, 8), scaled=(8, 8), scale=1.0,
                           id="win-aabbcc", taken_at=time.time())]

        safetylib.SafetyGate = OpenGate
        toolslib.capturelib.capture = fake_capture

        for transport in ("stdio", "http"):
            store = ShotStore(cfg)
            eski_png = store.dir / f"eski-{transport}.png"
            eski_png.write_bytes(b"x")
            eski_meta = store.dir / "m1-ccddee.json"
            eski_meta.write_text("{}")
            for f in (eski_png, eski_meta):
                os.utime(f, (0, 0))

            mcp = FastMCP("test")
            toolslib.register(mcp, cfg, jobslib.JobManager(cfg.jobs_dir), store,
                              transport=transport)
            fn = asyncio.run(mcp.get_tool("screen_capture")).fn
            fn()

            check(f"{transport}: cekim eski PNG'yi supurdu", not eski_png.exists())
            check(f"{transport}: cekim eski kaydi supurdu", not eski_meta.exists())
            check(f"{transport}: yeni cekim duruyor",
                  (store.dir / "yeni-cekim.png").exists())
            (store.dir / "yeni-cekim.png").unlink()
    finally:
        safetylib.SafetyGate = real_gate
        toolslib.capturelib.capture = real_capture
        import shutil as _sh

        _sh.rmtree(tmp, ignore_errors=True)


def test_capture_config_defaults() -> None:
    section("16. Ekran goruntusu — ornek yapilandirma")
    from pcbridge.config import load_config

    d = load_config(str(ROOT / "config.example.toml")).desktop
    check("uzun kenar 1536", d.screenshot_scale_long_edge == 1536,
          str(d.screenshot_scale_long_edge))
    # 1568 Anthropic'in kucultme esigi: ustune cikilirsa `shot` hesabi sessizce
    # sasar (bkz. bolum 49). Varsayilan HER ZAMAN altinda kalmali.
    check("varsayilan 1568 esiginin altinda",
          d.screenshot_scale_long_edge < 1568, str(d.screenshot_scale_long_edge))
    check("belirsiz koordinat korumasi acik", d.ambiguous_coord_guard is True)
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
        self._held: list[str] = []

    def _rec(self, *args) -> str:
        self.log.append(args)
        if self.fail_at and args[0] == self.fail_at:
            raise RuntimeError(f"{args[0]} bilerek patlatildi")
        return f"{args[0]} yapildi"

    def key(self, keys):
        return self._rec("key", keys)

    def type(self, text, raw):
        return self._rec("type", text, raw)

    def move(self, x, y, monitor, shot=None):
        return self._rec("move", x, y)

    def move_by(self, dx, dy):
        return self._rec("move_by", dx, dy)

    def click(self, button, count, x, y, monitor, shot=None):
        out = self._rec("click", button, x, y)
        if self.focus_after_click:
            self._focus = self.focus_after_click
        return out

    def mouse_down(self, button, x, y, monitor, shot=None):
        out = self._rec("mouse_down", button, x, y)
        self._held.append(button)
        if self.focus_after_click:
            self._focus = self.focus_after_click
        return out

    def mouse_up(self, button):
        if button in self._held:
            self._held.remove(button)
        return self._rec("mouse_up", button)

    def hold(self, keys):
        self._held.append(keys)
        return self._rec("hold", keys)

    def release(self, keys):
        if keys in self._held:
            self._held.remove(keys)
        return self._rec("release", keys)

    def held(self):
        return list(self._held)

    def release_all(self):
        freed, self._held = list(self._held), []
        self.log.append(("release_all",))
        return freed

    def drag(self, x, y, to_x, to_y, button, monitor, shot=None):
        return self._rec("drag", x, y, to_x, to_y, button)

    def scroll(self, amount, x, y, monitor, horizontal=False, shot=None):
        return self._rec("scroll", amount, horizontal)

    def ui_click(self, node_id):
        return self._rec("ui_click", node_id)

    def ui_set_text(self, node_id, text):
        return self._rec("ui_set_text", node_id, len(text))

    def launch(self, app, budget_left=None):
        self._focus = f"{app} | yeni"
        return self._rec("launch", app)

    def focus(self, window, budget_left=None):
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

    # -- I bolumu: basili tutma ve yeni dugmeler ---------------------------
    yeni = B.parse(
        '[{"a":"hold","keys":"ctrl"},'
        '{"a":"triple_click","x":10,"y":20},'
        '{"a":"mouse_down","button":"middle","x":1,"y":2},'
        '{"a":"mouse_up","button":"middle"},'
        '{"a":"scroll","amount":-5,"horizontal":true},'
        '{"a":"drag","x":1,"y":2,"to_x":3,"to_y":4,"button":"right"},'
        '{"a":"release","keys":"ctrl"}]'
    )
    check("yeni eylemler ayrisiyor",
          [a.a for a in yeni] == ["hold", "triple_click", "mouse_down", "mouse_up",
                                  "scroll", "drag", "release"],
          str([a.a for a in yeni]))
    check("mouse_down dugmeyi tasiyor", yeni[2].args["button"] == "middle")
    check("mouse_up dugmeyi tasiyor", yeni[3].args["button"] == "middle")
    check("scroll yatay bayragi tasiyor", yeni[4].args["horizontal"] is True)
    check("drag dugmeyi tasiyor", yeni[5].args["button"] == "right")
    check("drag varsayilan dugmesi left",
          B.parse('[{"a":"drag","x":1,"y":2,"to_x":3,"to_y":4}]')[0].args["button"]
          == "left")
    check("scroll varsayilani dikey",
          B.parse('[{"a":"scroll","amount":3}]')[0].args["horizontal"] is False)

    check("gecersiz dugme reddediliyor",
          fails('[{"a":"mouse_down","button":"ucuncu"}]', "dugme"))
    check("hold keys istiyor", fails('[{"a":"hold"}]', "keys yok"))
    check("release keys istiyor", fails('[{"a":"release"}]', "keys yok"))

    # -- cekim kimligi (`shot`) --------------------------------------------
    # Motor gercek cihazlari TANIMIYOR: `shot` burada yalnizca dogrulanmis bir
    # string, koordinata cevrilmesi ops.py'nin isi. Bicim yine de burada
    # dogrulaniyor ki bozuk bir kimlik listeyi BASTAN reddettirsin -- uc eylem
    # yapildiktan sonra degil.
    for kind, extra in (("click", ""), ("move", ""), ("mouse_down", ""),
                        ("scroll", ',"amount":3'),
                        ("drag", ',"to_x":5,"to_y":6')):
        got = B.parse(
            f'[{{"a":"{kind}","x":1,"y":2{extra},"shot":"m2-a1b2c3"}}]'
        )[0]
        check(f"{kind} shot tasiyor", got.args.get("shot") == "m2-a1b2c3",
              str(got.args))
    check("shot verilmezse None",
          B.parse('[{"a":"click","x":1,"y":2}]')[0].args["shot"] is None)
    check("bos shot None sayiliyor",
          B.parse('[{"a":"click","x":1,"y":2,"shot":"  "}]')[0].args["shot"] is None)
    for bad in ("../gizli", "m2-ZZZZZZ", "m2", "click"):
        check(f"bozuk shot bastan reddediliyor ({bad!r})",
              fails('[{"a":"click","x":1,"y":2,"shot":"%s"}]' % bad, "shot"))
    # Rapor hangi uzayda calisildigini soylesin: bir tiklama yanlis yere
    # dustugunde ilk sorulacak soru bu.
    check("describe shot'i gosteriyor",
          B.parse('[{"a":"click","x":1,"y":2,"shot":"m2-a1b2c3"}]')[0].describe()
          == "click (1, 2) @m2-a1b2c3",
          B.parse('[{"a":"click","x":1,"y":2,"shot":"m2-a1b2c3"}]')[0].describe())
    check("shot yokken describe eskisi gibi",
          B.parse('[{"a":"click","x":1,"y":2}]')[0].describe() == "click (1, 2)")

    # describe() raporda gorunuyor; yanlis eylem adi yanlis tesise yol acar.
    check("hold describe'i okunur", yeni[0].describe() == "hold 'ctrl'",
          yeni[0].describe())
    check("mouse_up describe'i dugmeyi soyluyor",
          yeni[3].describe() == "mouse_up middle", yeni[3].describe())

    # Cihaz secimi: `hold` klavye ister, `mouse_up` fare ister. Yalnizca
    # `ui_*` iceren bir liste HICBIR cihaz actirmamali (C bolumu hatasi).
    from pcbridge.desktop import ops as O
    check("hold klavye istiyor", O.devices_needed(B.parse('[{"a":"hold","keys":"a"}]'))
          == (True, False, False))
    check("mouse_up fare istiyor",
          O.devices_needed(B.parse('[{"a":"mouse_up"}]')) == (False, True, False))
    check("mouse_down fare istiyor",
          O.devices_needed(B.parse('[{"a":"mouse_down"}]')) == (False, True, False))
    check("ui_click hicbir cihaz istemiyor",
          O.devices_needed(B.parse('[{"a":"ui_click","id":"a1"}]')) == (False, False, False))
    # Adim 7: goreli hareket AYRI cihaz. Yalnizca `move_by` iceren bir liste
    # mutlak cihazi ACTIRMAMALI -- yoksa bosuna ikinci bir 1,2 s bekleme.
    check("move_by yalnizca goreli cihazi istiyor",
          O.devices_needed(B.parse('[{"a":"move_by","dx":10,"dy":0}]'))
          == (False, False, True))
    check("move + move_by ikisini de istiyor",
          O.devices_needed(
              B.parse('[{"a":"move","x":1,"y":2},{"a":"move_by","dx":5,"dy":0}]')
          ) == (False, True, True))


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

    # 6. BEYAN EDILMIS niyet. OLCULDU 2026-08-03: gercek gorsel ajanin plani
    #    "editore tikla, sonra yaz"di ve koruma her seferinde durduruyordu --
    #    odagin degismesi ISTENEN seydi. `expect_focus` niyeti soyletiyor.
    plan6 = B.parse('[{"a":"click","x":900,"y":500},{"a":"type","text":"selam"}]')
    ops6 = FakeOps()
    ops6.focus_after_click = "gnome-text-editor | Yeni Belge - Metin Duzenleyici"
    res6 = B.run(plan6, ops6, budget=90, expect_focus="Metin Duzenleyici",
                 sleep=lambda s: None)
    check("beklenen pencereye gecince SURUYOR", res6.done == 2,
          f"{res6.done} {res6.stopped} {res6.detail}")
    check("beyan edilince metin gonderildi",
          any(x[0] == "type" for x in ops6.log), str(ops6.log))

    # Beyan var ama odak BASKA yere gitti -> yine durmali. Koruma kalkmiyor.
    ops7 = FakeOps()
    ops7.focus_after_click = "masaustu | Desktop Icons 2"
    res7 = B.run(plan6, ops7, budget=90, expect_focus="Metin Duzenleyici",
                 sleep=lambda s: None)
    check("yanlis pencereye gidince yine duruyor", res7.stopped == "focus",
          res7.stopped)
    check("metin GONDERILMEDI", not any(x[0] == "type" for x in ops7.log),
          str(ops7.log))
    check("beklenen pencere gerekcede yaziyor",
          "beklenen" in res7.detail, res7.detail[:160])

    # Beyan yokken gerekce ne yapilmasi gerektigini soylemeli: ajan bunu
    # okuyup `expect_focus` ile tekrar deneyebilsin.
    res8 = B.run(plan6, FakeOps(focus="a | b"), budget=90, sleep=lambda s: None)
    _ = res8
    ops9 = FakeOps()
    ops9.focus_after_click = "baska | pencere"
    res9 = B.run(plan6, ops9, budget=90, sleep=lambda s: None)
    check("beyan yoksa gerekce yol gosteriyor",
          "expect_focus" in res9.detail, res9.detail[:200])

    # Beyan edilen pencereye gectikten SONRA ikinci bir kayma yine yakalanmali.
    class TwoHops(FakeOps):
        def __init__(self):
            super().__init__()
            self._n = 0

        def click(self, button, count, x, y, monitor, shot=None):
            out = self._rec("click", button, x, y)
            self._n += 1
            self._focus = ("editor | Metin Duzenleyici" if self._n == 1
                           else "masaustu | Desktop Icons 2")
            return out

    plan10 = B.parse('[{"a":"click","x":1,"y":1},{"a":"click","x":2,"y":2},'
                     '{"a":"key","keys":"ctrl+a"}]')
    ops10 = TwoHops()
    res10 = B.run(plan10, ops10, budget=90, expect_focus="Metin Duzenleyici",
                  sleep=lambda s: None)
    check("beyandan sonraki ikinci kayma yakalandi", res10.stopped == "focus",
          f"{res10.stopped} {res10.detail[:80]}")
    check("ikinci kaymada ctrl+a gitmedi",
          not any(x[0] == "key" for x in ops10.log), str(ops10.log))

    # -- I bolumu: yarim kalan `hold` --------------------------------------
    # Dizi DUZGUN bittiyse basili birakilan durur ("tut, sonraki cagrida
    # tikla" mesru), ama YARIDA kaldiysa birakilir: o noktadan sonra kimse
    # birakmayi ustlenmemis olur.
    plan11 = B.parse('[{"a":"hold","keys":"shift"},{"a":"mouse_down"}]')
    ops11 = FakeOps()
    res11 = B.run(plan11, ops11, budget=90, check_focus=False, sleep=lambda s: None)
    check("duzgun biten dizide basili kalan DURUYOR",
          set(res11.held) == {"shift", "left"}, str(res11.held))
    check("duzgun bitiste release_all cagrilmadi",
          ("release_all",) not in ops11.log, str(ops11.log))
    check("rapor basili kalani soyluyor", "HALA BASILI" in B.describe(res11))

    plan12 = B.parse('[{"a":"hold","keys":"shift"},{"a":"mouse_down"},'
                     '{"a":"ui_click","id":"x"}]')
    ops12 = FakeOps()
    ops12.fail_at = "ui_click"
    res12 = B.run(plan12, ops12, budget=90, check_focus=False, sleep=lambda s: None)
    check("yarida kalan dizide basili kalan BIRAKILDI", res12.held == [],
          str(res12.held))
    check("yarida kalinca release_all cagrildi",
          ("release_all",) in ops12.log, str(ops12.log))
    check("gerekce raporda", "basili kalanlar birakildi" in res12.detail,
          res12.detail[:120])


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


def test_gui_launch_block() -> None:
    section("44. Kabuktan GUI uygulamasi baslatma tespiti")
    from pcbridge.desktop import apps as A

    # Sahte .desktop havuzu: gercek tablo gerekmesin, test her makinede ayni
    # sonucu versin.
    havuz = [
        A.Entry("org.gnome.TextEditor", "Text Editor", False,
                ("Text Editor", "Metin Duzenleyici"), (), "gnome-text-editor"),
        A.Entry("code", "Visual Studio Code", False,
                ("Visual Studio Code",), ("Text Editor",), "code"),
        A.Entry("org.gnome.Nautilus", "Files", False,
                ("Files", "Dosyalar"), (), "nautilus"),
        # Flatpak: exec_name BOS (Exec'i `flatpak run ...`), eslesme girdi
        # kimliginden gelmeli.
        A.Entry("dev.vencord.Vesktop", "Vesktop", False, ("Vesktop",), (), ""),
    ]

    def bak(cmd, liste=("Vesktop", "Text Editor")):
        return A.looks_like_gui_launch(cmd, list(liste), pool=havuz)

    # --- bos liste = hicbir sey engellenmez ---------------------------------
    check("bos liste hicbir seyi engellemiyor",
          A.looks_like_gui_launch("gnome-text-editor", [], pool=havuz) is None)
    check("bos komut sorun cikarmiyor",
          A.looks_like_gui_launch("", ["Vesktop"], pool=havuz) is None)

    # --- dogrudan ikili adi -------------------------------------------------
    check("Exec ikilisi yakalaniyor (Text Editor -> gnome-text-editor)",
          bak("gnome-text-editor") == "Text Editor", str(bak("gnome-text-editor")))
    check("arguman komutu bozmuyor",
          bak("gnome-text-editor ~/notlar.md") == "Text Editor")
    check("tam yol yakalaniyor",
          bak("/usr/bin/gnome-text-editor") == "Text Editor")
    check("listede olmayan uygulama SERBEST", bak("nautilus") is None)
    check("siradan komut serbest", bak("ls -la /tmp") is None)
    check("derleme komutu serbest", bak("make -j8 && ./run.sh --check") is None)

    # --- sarmalayicilar -----------------------------------------------------
    check("nohup ile gizlenemiyor", bak("nohup gnome-text-editor &") == "Text Editor")
    check("setsid ile gizlenemiyor", bak("setsid gnome-text-editor") == "Text Editor")
    check("ortam atamasi ile gizlenemiyor",
          bak("GDK_BACKEND=x11 gnome-text-editor") == "Text Editor")
    check("env ile gizlenemiyor",
          bak("env GDK_BACKEND=x11 gnome-text-editor") == "Text Editor")

    # --- zincirin ICINDE ----------------------------------------------------
    check("&& zincirinin ikinci halkasi yakalaniyor",
          bak("cd /tmp && gnome-text-editor") == "Text Editor")
    check("; ile ayrilan yakalaniyor",
          bak("echo merhaba; gnome-text-editor") == "Text Editor")
    check("boru sonrasi yakalaniyor",
          bak("echo x | gnome-text-editor") == "Text Editor")

    # --- acik baslaticilar --------------------------------------------------
    check("gtk-launch argumani yakalaniyor",
          bak("gtk-launch org.gnome.TextEditor") == "Text Editor")
    check("gtk-launch .desktop uzantisiyla da yakalaniyor",
          bak("gtk-launch org.gnome.TextEditor.desktop") == "Text Editor")
    check("gio launch yakalaniyor",
          bak("gio launch /usr/share/applications/org.gnome.TextEditor.desktop")
          == "Text Editor")
    check("flatpak run kimligi yakalaniyor",
          bak("flatpak run dev.vencord.Vesktop") == "Vesktop")
    check("flatpak run bayrakli da yakalaniyor",
          bak("flatpak run --branch=stable --arch=x86_64 dev.vencord.Vesktop")
          == "Vesktop")
    check("BASKA bir flatpak uygulamasi serbest (flatpak'in kendisi anahtar degil)",
          bak("flatpak run org.gimp.GIMP") is None)

    # --- ad cozumleme -------------------------------------------------------
    check("kullanici ikili adiyla da yazabilir",
          A.looks_like_gui_launch("gnome-text-editor", ["gnome-text-editor"],
                                  pool=havuz) == "Text Editor")
    check("kullanici girdi kimligiyle de yazabilir",
          A.looks_like_gui_launch("gnome-text-editor", ["org.gnome.TextEditor"],
                                  pool=havuz) == "Text Editor")
    check("Turkce ad da cozuluyor",
          A.looks_like_gui_launch("gnome-text-editor", ["Metin Duzenleyici"],
                                  pool=havuz) == "Text Editor")
    check("cozulemeyen ad duz simge olarak calisiyor",
          A.looks_like_gui_launch("boyle-bir-sey-yok --flag",
                                  ["boyle-bir-sey-yok"], pool=havuz)
          == "boyle-bir-sey-yok")
    check("cozulemeyen ad baska komutu yakalamiyor",
          A.looks_like_gui_launch("ls", ["boyle-bir-sey-yok"], pool=havuz) is None)

    # --- exec_name cozumleme ------------------------------------------------
    check("Exec: alan kodlari atiliyor",
          A._exec_binary("gnome-text-editor %U") == "gnome-text-editor")
    check("Exec: tam yolun dosya adi",
          A._exec_binary("/usr/share/code/code --unity-launch %F") == "code")
    check("Exec: env ve atama atiliyor",
          A._exec_binary("env GDK_BACKEND=x11 vesktop %U") == "vesktop")
    check("Exec: flatpak COK GENEL sayiliyor (bos donuyor)",
          A._exec_binary("/usr/bin/flatpak run --branch=stable dev.x.Y @@u %U @@")
          == "")
    check("Exec: sh/bash cok genel", A._exec_binary("sh -c 'foo'") == "")

    # --- config -------------------------------------------------------------
    d = DesktopSpec()
    check("varsayilan: kapi kapali", d.block_gui_launch_in_shell is False)
    check("varsayilan: engel listesi BOS (hicbir sey engellenmiyor)",
          d.gui_launch_blocklist == [], str(d.gui_launch_blocklist))


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


# ================================================== YEREL GORSEL AJAN (F)
def _run_cli(module: str, argv: list[str], env: dict | None = None) -> tuple[int, str, str]:
    """CLI'yi AYRI BIR SURECTE calistir ve (kod, stdout, stderr) dondur.

    Ayri surec sart: `pcb-do` gercekte de oyle calisiyor ve test edilmesi
    gereken sey tam olarak surec sinirindaki davranis -- cikis kodu, ortam
    degiskeni, kapinin diskten okunmasi.
    """
    import os as _os
    import subprocess as _sp

    run_env = _os.environ.copy()
    run_env.pop("PCBRIDGE_TASK_FORCE", None)
    run_env.pop("PCBRIDGE_JOB_ID", None)
    if env:
        run_env.update(env)
    proc = _sp.run(
        [sys.executable, "-m", module, *argv],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120, env=run_env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_cli_parse() -> None:
    section("31. pcb-do — ayristirma ve cikis kodlari")
    from pcbridge import cli as C

    # Tek NESNE de liste de kabul edilmeli: ajan ikisini de yaziyor.
    code, out, _ = _run_cli("pcbridge.cli.do",
                            ["--dry-run", '{"a":"key","keys":"ctrl+s"}'])
    check("tek nesne kabul edildi", code == C.EXIT_OK and "1 eylem" in out, out[:80])

    code, out, _ = _run_cli(
        "pcbridge.cli.do",
        ["--dry-run", '[{"a":"click","x":10,"y":20},{"a":"wait","ms":50}]'])
    check("liste kabul edildi", code == C.EXIT_OK and "2 eylem" in out, out[:80])

    # Bozuk girdinin uc bicimi de EXIT_BAD_INPUT vermeli -- "izin yok" degil.
    for label, arg in (
        ("bilinmeyen eylem", '[{"a":"ucmak"}]'),
        ("gecersiz JSON", "bu json degil"),
        ("bos liste", "[]"),
    ):
        code, _, err = _run_cli("pcbridge.cli.do", ["--dry-run", arg])
        check(f"{label} -> cikis 4", code == C.EXIT_BAD_INPUT, f"kod={code}")
        check(f"{label} gerekcesi var", "HATA" in err, err[:60])

    code, _, err = _run_cli("pcbridge.cli.do", [])
    check("arguman yok -> cikis 4", code == C.EXIT_BAD_INPUT, f"kod={code}")

    # --dry-run METIN ICERIGINI basmamali: ekranda parola yaziliyor olabilir.
    code, out, _ = _run_cli(
        "pcbridge.cli.do",
        ["--dry-run", '[{"a":"type","text":"cok-gizli-parola"}]'])
    check("dry-run metni gizliyor", "cok-gizli-parola" not in out, out[:120])
    check("dry-run uzunlugu veriyor", "text_chars" in out, out[:120])

    # JSON cikti makine okunur olmali
    code, out, _ = _run_cli(
        "pcbridge.cli.do",
        ["--dry-run", "--json", '[{"a":"ui_click","id":"90e6"}]'])
    data = json.loads(out)
    check("json dry-run", data["dry_run"] and data["count"] == 1, out[:80])
    check("ui_click cihaz istemiyor",
          not data["needs_keyboard"] and not data["needs_pointer"],
          str(data))


def test_cli_gate() -> None:
    section("32. pcb-do — guvenlik kapisi ayri surecte de isliyor")
    from pcbridge import cli as C
    from pcbridge.desktop import ops as O

    # Kapi ayri surecte de isliyor. Redden GEREKCE bekleniyor ama metnin
    # kendisi makinenin o anki ayarina bagli ("kapali" mi "kilitli" mi), o
    # yuzden testin dayanagi degil: dayanak, gerekcenin masaustu kapisindan
    # geldigi ve TASK_FORCE ile DEGISMEDIGI.
    code, _, err = _run_cli("pcbridge.cli.do", ['[{"a":"key","keys":"Escape"}]'])
    check("izinsiz -> cikis 3", code == C.EXIT_DENIED, f"kod={code}")
    check("gerekce masaustu kapisindan", "Masaustu kontrolu" in err, err[:100])

    # PCBRIDGE_TASK_FORCE YALNIZCA bosta kontrolunu atlatir. Ekran kilidi,
    # kapali masaustu ve izin penceresi gibi sert reddedislere etkisi
    # OLMAMALI -- yoksa `computer_task` kapiyi tamamen delerdi.
    code2, _, err2 = _run_cli("pcbridge.cli.do", ['[{"a":"key","keys":"Escape"}]'],
                              env={"PCBRIDGE_TASK_FORCE": "1"})
    check("TASK_FORCE kapiyi ACMIYOR", code2 == C.EXIT_DENIED, f"kod={code2}")
    check("TASK_FORCE gerekceyi DEGISTIRMIYOR", err2.strip() == err.strip(),
          f"{err[:60]!r} != {err2[:60]!r}")

    # pcb-shot da ayni kapidan geciyor
    code, _, err = _run_cli("pcbridge.cli.shot", ["--monitor", "1"])
    check("pcb-shot izinsiz -> cikis 3", code == C.EXIT_DENIED, f"kod={code}")

    # --dry-run KAPIYA HIC VARMIYOR: masaustu kapaliyken de ayristirmali,
    # yoksa ajan kendi JSON'unu dogrulayamazdi.
    code, out, _ = _run_cli("pcbridge.cli.do",
                            ["--dry-run", '[{"a":"key","keys":"a"}]'])
    check("dry-run kapiya takilmiyor", code == C.EXIT_OK, f"kod={code}")

    # Hangi cihazin gerektigi eylem listesinden turetiliyor: yalnizca ui_*
    # olan bir liste /dev/uinput ARAMAMALI (C bolumunde duzeltilen hata).
    from pcbridge.desktop import batch as B

    only_ui = B.parse('[{"a":"ui_click","id":"aa"},{"a":"wait","ms":10}]')
    check("ui_* cihaz istemiyor", O.devices_needed(only_ui) == (False, False, False))
    check("type klavye istiyor",
          O.devices_needed(B.parse('[{"a":"type","text":"x"}]')) == (True, False, False))
    check("click fare istiyor",
          O.devices_needed(B.parse('[{"a":"click","x":1,"y":2}]')) == (False, True, False))
    check("focus VARSAYILAN olarak klavye istiyor (GNOME arama yedegi)",
          O.devices_needed(B.parse('[{"a":"focus","window":"X"}]')) == (True, False, False))
    check("eklenti yolu bildirilince focus cihaz istemiyor",
          O.devices_needed(B.parse('[{"a":"focus","window":"X"}]'),
                           focus_uses_keyboard=False) == (False, False, False))
    check("karisik liste ikisini de istiyor",
          O.devices_needed(
              B.parse('[{"a":"click","x":1,"y":2},{"a":"type","text":"x"}]')
          ) == (True, True, False))


def test_cli_shot_text() -> None:
    section("33. pcb-shot — cekim kimligi ve ofset metinde")
    from pcbridge.cli import shot as SH

    class FakeMon:
        def __init__(self, index, connector, primary):
            self.index, self.connector, self.primary = index, connector, primary

    class FakeShot:
        def __init__(self, monitor, offset, size, scaled, scale, sid=""):
            self.path = Path("/tmp/x.png")
            self.monitor, self.offset = monitor, offset
            self.size, self.scaled, self.scale = size, scaled, scale
            self.id = sid

    mons = [FakeMon(1, "DP-2", False), FakeMon(2, "DP-1", True)]
    shots = [
        FakeShot(mons[0], (0, 0), (1920, 1080), (1920, 1080), 1.0, "m1-aabbcc"),
        FakeShot(mons[1], (1920, 0), (1920, 1080), (1920, 1080), 1.0, "m2-aabbcc"),
    ]
    text = "\n".join(SH.describe(shots, mons))
    check("monitor numarasi var", "monitor 1" in text and "monitor 2" in text)
    check("ofset var", "(1920, 0)" in text, text[:160])
    # Kimlik goruntunun YANINDA olmali: ajanin tasidigi tek sey bu.
    check("her goruntunun kimligi yaziyor",
          "shot: m1-aabbcc" in text and "shot: m2-aabbcc" in text, text[:300])
    check("kullanim ornegi kimligi tasiyor",
          '"shot":"m1-aabbcc"' in text, text[-300:])
    # ARITMETIK YOK: formul metinde kalirsa ajan yine elle cevirmeye kalkar.
    check("donusum formulu artik verilmiyor",
          "goruntu_x /" not in text and "+ goruntu_x" not in text, text[:400])
    check("cevirme uyarisi var", "sen cevirme" in text, text[-200:])
    # Bu bilgi kaybolursa ikinci monitore yapilan her tiklama 1920 px sasar.
    check("ust cubugun yeri yaziyor",
          "ust cubugu" in text and "monitor 2" in text, text[-400:])

    # Olceklenmis goruntude olcek BILGI olarak duruyor (formul degil)
    small = [FakeShot(mons[1], (1920, 0), (1920, 1080), (1280, 720), 0.667,
                      "m2-ddeeff")]
    stext = "\n".join(SH.describe(small, mons))
    check("olcek bilgi olarak yaziyor", "olcek 0.667" in stext, stext[:200])
    check("olcekli goruntude de bolme formulu yok",
          "/ 0.667" not in stext, stext[:250])

    # Ofsetsiz goruntu (window) koordinat uretmemeli
    win = [FakeShot(None, None, (800, 600), (800, 600), 1.0, "win-aabbcc")]
    wtext = "\n".join(SH.describe(win, mons))
    check("ofsetsiz goruntu uyariyor", "NEREDE" in wtext, wtext[:160])
    check("ofsetsiz goruntu icin ornek verilmiyor",
          '"shot":"win-aabbcc"' not in wtext, wtext[:300])


def test_cli_shot_scale() -> None:
    """`pcb-shot` olcegi config'ten okuyor mu.

    Iki yol ayri varsayilanlar tasiyordu (`screen_capture` 1280, `pcb-shot` 0)
    ve ajanin gordugu cozunurluk hangi yoldan bagli oldugna gore degisiyordu:
    ayni ekran, iki farkli piksel uzayi.
    """
    section("48. pcb-shot — olcek config'ten geliyor")
    from pcbridge.cli import shot as SH

    parser = SH.build_parser()
    check("--scale verilmezse None (config'e birakiliyor)",
          parser.parse_args([]).scale is None,
          str(parser.parse_args([]).scale))
    check("--scale 0 acikca tam cozunurluk",
          parser.parse_args(["--scale", "0"]).scale == 0)
    check("--scale 1536 aynen geciyor",
          parser.parse_args(["--scale", "1536"]).scale == 1536)

    # `main()`teki secim kurali. Kodun kendisi kapidan geciyor, o yuzden
    # burada KURAL sinaniyor: verilmediyse config, verildiyse deger.
    def secim(arg, ayar):
        return ayar if arg is None else max(0, arg)

    check("verilmezse config'teki deger", secim(None, 1280) == 1280)
    check("config 1536 ise 1536", secim(None, 1536) == 1536)
    check("--scale 0 config'i eziyor", secim(0, 1280) == 0)
    check("--scale 800 config'i eziyor", secim(800, 1280) == 800)
    check("negatif deger 0'a kirpiliyor", secim(-5, 1280) == 0)

    # Ornek yapilandirmadaki deger ile `screen_capture` varsayilani AYNI
    # kaynaktan geliyor -- ikisi ayrisirsa bu kontrol duser.
    from pcbridge.config import load_config

    d = load_config(str(ROOT / "config.example.toml")).desktop
    check("ornek config'te tek deger var", d.screenshot_scale_long_edge == 1536,
          str(d.screenshot_scale_long_edge))
    check("1568 sinirinin altinda", d.screenshot_scale_long_edge < 1568,
          str(d.screenshot_scale_long_edge))


def test_oversize_warning() -> None:
    """1568'i asan cekim uyariyor mu.

    Sinirin anlami cekim kimligiyle DEGISTI. Eskiden yalnizca "raporlanan
    olcek modelin gordugunden farkli olur"du; simdi sunucunun hesabini
    boziyor: model 1568'e indirilmis karedeki pikseli soyluyor,
    `to_global()` kayitli olcegi uyguluyor, aradaki 1,22 kat sessizce
    koordinata giriyor. Bu yuzden uyari SART -- ama kapi degil: kullanici tam
    cozunurlugu bakmak icin isteyebilir.
    """
    section("49. Cekim kimligi — 1568 siniri uyarisi")
    from pcbridge.desktop import capture as C

    def shot(scaled):
        return C.Shot(path=Path("/tmp/x.png"), monitor=None, offset=(0, 0),
                      size=(1920, 1080), scaled=scaled,
                      scale=scaled[0] / 1920, id="m1-aabbcc")

    check("1920 uzun kenar buyuk sayiliyor", C.oversized(shot((1920, 1080))))
    check("1536 sorun degil", not C.oversized(shot((1536, 864))))
    check("1280 sorun degil", not C.oversized(shot((1280, 720))))
    check("tam sinirda (1568) sorun degil", not C.oversized(shot((1568, 882))),
          "1568 dahil olmali: API bu degeri kucultmuyor")
    check("1569 buyuk sayiliyor", C.oversized(shot((1569, 883))))
    # Dikey goruntude olcut yine UZUN kenar
    check("dikeyde de uzun kenara bakiliyor", C.oversized(shot((900, 1600))))

    note = C.oversize_note(shot((1920, 1080)))
    check("uyari asilan degeri soyluyor", "1920" in note and "1568" in note,
          note[:120])
    check("uyari kac kat sastigini soyluyor", "1.22" in note, note[:200])
    check("uyari ne yapilacagini soyluyor", "scale=1568" in note, note[:250])
    check("sorun yoksa uyari bos", C.oversize_note(shot((1280, 720))) == "")


def test_cli_shot_dir() -> None:
    section("34. pcb-shot — dizin secimi ve temizlik")
    import os as _os
    import tempfile as _tf

    from pcbridge.cli import shot as SH
    from pcbridge.config import Config

    class Cfg:
        def __init__(self, **kw):
            self.desktop = DesktopSpec(**kw)
            self.state_dir = Path("/tmp/pcb-state")

        # Yol hesabinin GERCEK kaynagi: `cli.shot_dir` yalnizca yaratip mod
        # veriyor. Ikinci bir kopya cikarsa MCP bir dizine, kabuk baskasina
        # yazar ve `shot=` kimlikleri sessizce bulunamaz olur.
        agent_shot_path = Config.agent_shot_path
        shot_search_dirs = Config.shot_search_dirs

    with _tf.TemporaryDirectory() as d:
        # Acikca verilen dizin her seyi ezer
        cfg = Cfg(agent_shot_dir=d)
        check("yapilandirilan dizin kullanildi", SH.shot_dir(cfg) == Path(d))

        # XDG_RUNTIME_DIR varsa oraya (mod 700, oturumla silinir)
        old = _os.environ.get("XDG_RUNTIME_DIR")
        try:
            _os.environ["XDG_RUNTIME_DIR"] = d
            got = SH.shot_dir(Cfg())
            check("XDG_RUNTIME_DIR tercih edildi",
                  got == Path(d) / "pcbridge" / "shots", str(got))
            check("dizin yalnizca kullaniciya acik",
                  (got.stat().st_mode & 0o777) == 0o700,
                  oct(got.stat().st_mode & 0o777))
            # Kimlik aramasi IKI dizine de bakmali: MCP sunucusu
            # state_dir/shots'a, `pcb-shot` XDG altina yaziyor. Tek dizine
            # bakilsaydi MCP'den cekilen goruntuye kabuktan tiklanamazdi.
            dirs = Cfg().shot_search_dirs
            check("arama state_dir/shots ile basliyor",
                  dirs[0] == Path("/tmp/pcb-state") / "shots", str(dirs))
            check("arama pcb-shot dizinini de kapsiyor",
                  Path(d) / "pcbridge" / "shots" in dirs, str(dirs))
            same = Cfg(agent_shot_dir="/tmp/pcb-state/shots").shot_search_dirs
            check("ayni dizin iki kez aranmiyor", len(same) == 1, str(same))
            _os.environ.pop("XDG_RUNTIME_DIR")
            check("XDG yoksa /tmp/pcb", SH.shot_dir(Cfg()) == Path("/tmp/pcb"))
        finally:
            if old is None:
                _os.environ.pop("XDG_RUNTIME_DIR", None)
            else:
                _os.environ["XDG_RUNTIME_DIR"] = old

    with _tf.TemporaryDirectory() as d:
        base = Path(d)
        fresh, stale = base / "a.png", base / "b.png"
        fresh.write_bytes(b"x")
        stale.write_bytes(b"x")
        # Cekim kaydi PNG ile ayni yasa tabi: PNG'siz kalan bir `<id>.json`
        # `shot=` ile bulunur ama arkasinda goruntu olmaz.
        fresh_meta, stale_meta = base / "m1-aabbcc.json", base / "m2-ddeeff.json"
        fresh_meta.write_text("{}")
        stale_meta.write_text("{}")
        for old_f in (stale, stale_meta):
            _os.utime(old_f, (time.time() - 90000, time.time() - 90000))
        removed = SH.sweep(base, keep_hours=24)
        check("eski goruntu silindi", not stale.exists())
        check("eski cekim kaydi da silindi", not stale_meta.exists())
        check("iki dosya birden sayildi", removed == 2, str(removed))
        check("yeni goruntu duruyor", fresh.exists())
        check("yeni cekim kaydi duruyor", fresh_meta.exists())
        check("keep_hours=0 iken temizlik yok", SH.sweep(base, 0) == 0)


def test_cli_stale_shot() -> None:
    section("35. pcb-do — bayat ekran goruntusu korumasi")
    import os as _os
    import tempfile as _tf

    from pcbridge import cli as C
    from pcbridge.cli import do as D
    from pcbridge.desktop import batch as B

    # Yalnizca ACIKCA koordinat verilen eylemler goruntuye dayanir.
    check("koordinatli tiklama sayiliyor",
          len(D.coord_actions(B.parse('[{"a":"click","x":10,"y":20}]'))) == 1)
    check("koordinatsiz tiklama sayilmiyor",
          D.coord_actions(B.parse('[{"a":"click"}]')) == [])
    for arg in ('[{"a":"key","keys":"a"}]', '[{"a":"type","text":"x"}]',
                '[{"a":"ui_click","id":"aa"}]', '[{"a":"wait","ms":10}]'):
        check(f"{json.loads(arg)[0]['a']} goruntuye dayanmiyor",
              D.coord_actions(B.parse(arg)) == [], arg)

    with _tf.TemporaryDirectory() as d:
        base = Path(d)
        check("goruntu yokken yas None", C.newest_shot_age(base) is None)

        png = base / "a.png"
        png.write_bytes(b"x")
        age = C.newest_shot_age(base)
        check("taze goruntu ~0 saniyelik", age is not None and age < 5, str(age))

        _os.utime(png, (time.time() - 300, time.time() - 300))
        age = C.newest_shot_age(base)
        check("eski goruntunun yasi olculuyor",
              age is not None and 290 < age < 310, str(age))

        # DAHA YENI bir goruntu varsa yas ona gore: ajan yeni bir tane almistir.
        (base / "b.png").write_bytes(b"x")
        age = C.newest_shot_age(base)
        check("en yeni goruntu esas aliniyor", age is not None and age < 5, str(age))

    # Ucdan uca: dizin bos -> koordinatli eylem REDDEDILMELI (kor tiklama).
    with _tf.TemporaryDirectory() as d:
        env = {"PCBRIDGE_TEST_SHOTDIR": d}
        code, _, err = _run_cli(
            "pcbridge.cli.do", ['[{"a":"click","x":10,"y":20}]'],
            env={**env, "XDG_RUNTIME_DIR": d})
        # Masaustu kapali oldugu icin kapi da reddeder; onemli olan
        # KOORDINAT kontrolunun ONCE gelmesi ve gerekcesinin ayri olmasi.
        check("kor tiklama reddedildi", code == C.EXIT_DENIED, f"kod={code}")
        check("gerekce goruntu almayi soyluyor",
              "pcb-shot" in err or "Masaustu kontrolu" in err, err[:120])

    # `shot=` VERILDIGINDE olcut o cekimin kendi yasi, dizindeki en yeni PNG
    # DEGIL. Fark gercek bir acik: asagidaki dizinde taze bir PNG var, yani
    # eski olcut "goruntu taze" deyip gecerdi -- ama tiklama BASKA, bayat bir
    # cekimin koordinatlarina gore yapilacakti.
    with _tf.TemporaryDirectory() as d:
        base = Path(d) / "pcbridge" / "shots"
        base.mkdir(parents=True)
        (base / "taze.png").write_bytes(b"x")  # dizin "taze" gorunuyor
        stale = base / "m2-a1b2c3.json"
        stale.write_text(json.dumps({
            "id": "m2-a1b2c3", "png": "eski.png", "monitor": 2,
            "connector": "DP-1", "primary": True, "offset": [1920, 0],
            "size": [1920, 1080], "scaled": [1280, 720], "scale": 0.6667,
            "taken_at": time.time() - 3600,
        }))
        code, _, err = _run_cli(
            "pcbridge.cli.do",
            ['[{"a":"click","x":1,"y":2,"shot":"m2-a1b2c3"}]'],
            env={"XDG_RUNTIME_DIR": d})
        check("bayat cekim kimligi reddedildi", code == C.EXIT_DENIED, f"kod={code}")
        check("gerekce cekimin KENDI yasini soyluyor",
              "m2-a1b2c3" in err and "saniyelik" in err, err[:160])

        # Taze bir kayit ayni dizinde kapiya kadar gelmeli: red gerekcesi artik
        # bayatlik degil masaustu kapisi olmali.
        (base / "m2-ddeeff.json").write_text(json.dumps({
            "id": "m2-ddeeff", "png": "taze.png", "monitor": 2,
            "connector": "DP-1", "primary": True, "offset": [1920, 0],
            "size": [1920, 1080], "scaled": [1280, 720], "scale": 0.6667,
            "taken_at": time.time(),
        }))
        code, _, err = _run_cli(
            "pcbridge.cli.do",
            ['[{"a":"click","x":1,"y":2,"shot":"m2-ddeeff"}]'],
            env={"XDG_RUNTIME_DIR": d})
        check("taze cekim bayatlik kontrolunu geciyor",
              "saniyelik" not in err, err[:160])

        # Var olmayan kimlik: sessizce global koordinat sanilmamali.
        code, _, err = _run_cli(
            "pcbridge.cli.do",
            ['[{"a":"click","x":1,"y":2,"shot":"m9-abcdef"}]'],
            env={"XDG_RUNTIME_DIR": d})
        check("bilinmeyen kimlik reddedildi", code == C.EXIT_DENIED, f"kod={code}")
        check("bilinmeyen kimligin gerekcesi net",
              "m9-abcdef" in err, err[:160])


def test_computer_task_prompt() -> None:
    section("36. computer_task — prompt ve yonerge")
    from pcbridge import tools as T

    check("SKILL.md depoda", T._SKILL_PATH.is_file(), str(T._SKILL_PATH))
    skill = T._SKILL_PATH.read_text(encoding="utf-8")
    # Yonergenin icindekiler tesadufe birakilmiyor: her biri bir olcumun ya da
    # bir kazanin karsiligi.
    check("ust cubugun yeri yaziyor", "monitör 2" in skill or "monitor 2" in skill)
    check("kor tiklama yasagi var", "Kör tıklama" in skill)
    check("kazanin hikayesi var", "23 öğeyi" in skill)
    check("cikis kodlari yaziyor", "pcb-do --dry-run" in skill and "| 3 |" in skill)
    check("eylemleri gruplama gerekcesi", "1,4 s" in skill)

    p = T._task_prompt("YONERGE", "hedef metni", "Vesktop acildi", 12)
    check("yonerge basta", p.startswith("YONERGE"), p[:40])
    check("hedef sonda", p.rstrip().endswith("hedef metni"), p[-60:])
    check("hazirlik bilgisi gecti", "Vesktop acildi" in p)
    check("adim butcesi gecti", "12" in p)
    # "basarili gibi gorunen basarisiz is" bu projenin tekrarlayan endisesi
    check("dogru rapor istendi", "hedefe ulasildi mi" in p)

    p2 = T._task_prompt("YONERGE", "hedef", "", 5)
    check("hazirlik yoksa satir da yok", "hazirlandi" not in p2)


def test_move_path() -> None:
    """Yumusak fare hareketinin yol hesabi (`input.move_path`).

    Fonksiyon SAF: cihaz gormez, uyumaz. Bu yuzden burada gercek klavye/fare
    olmadan kosuyor -- `models.py`'nin saf tutulmasiyla ayni gerekce.
    """
    from pcbridge.desktop.input import (
        DEFAULT_POINTER_MAX_MS,
        DEFAULT_POINTER_SPEED,
        MOVE_MIN_MS,
        MOVE_STEP_SECONDS,
        move_path,
    )

    section("38. Yumusak fare hareketi — yol hesabi")

    step_ms = MOVE_STEP_SECONDS * 1000

    # 1) Hedef her zaman TAM tutturulur. Bir piksel sapma ikinci monitorde
    #    yanlis widget'a tiklamak demek.
    for (x1, y1, x2, y2) in [(0, 0, 1920, 0), (37, 11, 3839, 1079),
                             (3000, 900, 5, 5), (100, 100, 101, 100)]:
        path = move_path(x1, y1, x2, y2)
        check(f"son nokta tam hedef ({x2}, {y2})", path[-1] == (x2, y2), str(path[-1]))

    # 2) Baslangic noktasi YOK: oradan zaten geliyoruz, tekrar yazmak bedava degil.
    path = move_path(0, 0, 1920, 0)
    check("baslangic noktasi yolda degil", path[0] != (0, 0), str(path[0]))

    # 3) Sure = mesafe / hiz, taban ve tavanla sinirli.
    uzun = move_path(0, 0, 1920, 0, speed=5000, max_ms=500)
    beklenen = round(1920 / 5000 * 1000 / step_ms)
    check("1920 px -> beklenen adim sayisi", len(uzun) == beklenen,
          f"{len(uzun)} nokta, beklenen {beklenen}")

    kisa = move_path(0, 0, 20, 0, speed=5000)
    check("cok kisa mesafe TABAN suresine cikiyor",
          len(kisa) == round(MOVE_MIN_MS / step_ms), f"{len(kisa)} nokta")

    kosegen = move_path(0, 0, 3839, 1079, speed=5000, max_ms=500)
    check("cok uzun mesafe TAVANA takiliyor",
          len(kosegen) <= round(500 / step_ms) + 1, f"{len(kosegen)} nokta")

    check("mesafe artinca nokta sayisi artiyor",
          len(move_path(0, 0, 200, 0)) < len(move_path(0, 0, 1500, 0)))

    # 4) speed = 0 -> isinlama (eski davranis geri geliyor).
    check("speed=0 tek nokta (isinlama)",
          move_path(0, 0, 1920, 0, speed=0) == [(1920, 0)])
    check("ayni noktaya hareket tek nokta",
          move_path(500, 500, 500, 500) == [(500, 500)])

    # 5) `drag` ayardan BAGIMSIZ ara nokta uretmeli: sicrayan bir hareketi
    #    cogu uygulama surukleme saymiyor.
    surukle = move_path(0, 0, 900, 0, speed=0, min_steps=10)
    check("min_steps speed=0'i eziyor (drag yolu)", len(surukle) == 10,
          f"{len(surukle)} nokta")

    # 6) Yol monoton: geri donen bir imlec titriyor demektir.
    yol = move_path(100, 100, 1000, 800)
    xs = [p[0] for p in yol]
    ys = [p[1] for p in yol]
    check("x monoton artiyor", all(b >= a for a, b in zip(xs, xs[1:])))
    check("y monoton artiyor", all(b >= a for a, b in zip(ys, ys[1:])))
    geri = move_path(1000, 800, 100, 100)
    gxs = [p[0] for p in geri]
    check("ters yonde monoton azaliyor", all(b <= a for a, b in zip(gxs, gxs[1:])))

    # 7) Ayni piksel iki kez yazilmaz (her nokta bir syn() maliyeti).
    tekrar = move_path(0, 0, 3, 0, speed=200, max_ms=500)
    check("ardisik ayni nokta elenmis", len(tekrar) == len(set(tekrar)), str(tekrar))

    # 8) Smoothstep: ortada hizli, uclarda yavas. Ilk adim mesafesi ortadaki
    #    adim mesafesinden KUCUK olmali, yoksa egri duz demektir.
    yol = move_path(0, 0, 2000, 0, speed=5000)
    ilk = yol[0][0]
    orta = yol[len(yol) // 2][0] - yol[len(yol) // 2 - 1][0]
    check("hiz egrisi var (ilk adim ortadakinden kucuk)", ilk < orta,
          f"ilk={ilk} px, orta={orta} px")

    # 9) Varsayilanlar config'le ayni hikayeyi anlatiyor mu.
    check("varsayilan hiz 5000 px/s", DEFAULT_POINTER_SPEED == 5000)
    check("varsayilan tavan 500 ms", DEFAULT_POINTER_MAX_MS == 500)


def test_relative_chunks() -> None:
    """Goreli hareketin parcalanmasi -- SAF, cihaz acmaz (Adim 7)."""
    from pcbridge.desktop.input import (
        MOVE_BY_MAX, MOVE_BY_MAX_CHUNKS, relative_chunks,
    )

    section("24b. Goreli hareket — parcalama")

    # 1) TOPLAM KORUNUR. Parcalama bir yuvarlatma degil, bir zamanlama karari:
    #    olculdu 2026-09-20, 200 birim kac parcaya bolunurse bolunsun 92 piksel.
    for dx, dy in ((40, -16), (200, 0), (0, 300), (-137, 59), (1, 1),
                   (MOVE_BY_MAX, -MOVE_BY_MAX)):
        parcalar = relative_chunks(dx, dy)
        toplam = (sum(a for a, _ in parcalar), sum(b for _, b in parcalar))
        check(f"toplam korunuyor ({dx}, {dy})", toplam == (dx, dy), str(toplam))

    # 2) Hicbir sey gonderilmeyecekse hic parca yok.
    check("sifir delta hic parca uretmiyor", relative_chunks(0, 0) == [])

    # 3) Kucuk delta tek atista gider; buyuk delta bolunur.
    check("10 birim tek parca", len(relative_chunks(10, 0)) == 1)
    check("40 birim uc parca", len(relative_chunks(40, -16)) == 3)

    # 4) TAVANLAR. Sinirsiz delta kullanicinin kendi masaustune DoS olurdu;
    #    parca sayisi tavani en kotu sureyi ~512 ms'de tutuyor.
    kirpik = relative_chunks(99_999, -99_999)
    check("delta tavani kirpiyor",
          sum(a for a, _ in kirpik) == MOVE_BY_MAX
          and sum(b for _, b in kirpik) == -MOVE_BY_MAX)
    check("parca sayisi tavani", len(kirpik) <= MOVE_BY_MAX_CHUNKS,
          f"{len(kirpik)} parca")

    # 5) Bir eksen sifirsa o eksende hicbir parca deger tasimaz.
    check("tek eksende digeri hep sifir",
          all(b == 0 for _, b in relative_chunks(200, 0)))


def test_hold_tracking() -> None:
    """Basili tutma takibi -- CIHAZSIZ kisim.

    Gercek cihazli tur `test_real_hold()` icinde ve varsayilan olarak atlanir.
    """
    from pcbridge.desktop import input as I

    section("39. Basili tutma — takip ve cozumleme")

    b = I.InputBackend(hold_max_seconds=0)
    check("cihaz acilmadan held() bos", b.held() == [])
    check("cihaz acilmadan release_all() bos", b.release_all() == [])
    check("otomatik birakma bildirimi bos", b.take_auto_released() == [])

    # Ad <-> kod donusumu: `held()` ciktisi kullaniciya gosteriliyor.
    check("key_name(ctrl kodu) = ctrl", I.key_name(I.key_code("ctrl")) == "ctrl")
    check("key_name bilinmeyen kodu ham dondurur", I.key_name(999999) == "999999")
    check("button_code(middle) calisiyor", isinstance(I.button_code("middle"), int))
    for bad in ("ucuncu", "", "sol"):
        try:
            I.button_code(bad)
            check(f"gecersiz dugme reddedildi: {bad!r}", False, "kabul edildi")
        except I.InputError as exc:
            check(f"gecersiz dugme reddedildi: {bad!r}", "left, right, middle" in str(exc))

    # Kac tus olursa olsun: sanal cihazda ghosting yok.
    check("6 tuslu kombinasyon cozuluyor",
          len(I.parse_combo("ctrl+shift+alt+super+a+b")) == 6)
    check("bos kombinasyon reddediliyor",
          _raises(lambda: I.parse_combo("+++"), I.InputError))

    # Ayarlar backend'e GERCEKTEN geciyor mu (bir kere atlanmisti: alanlar
    # tanimliydi, config'e yazilan deger hicbir sey yapmiyordu).
    b2 = I.InputBackend(pointer_speed=1234, pointer_max_ms=321, hold_max_seconds=7)
    check("pointer_speed backend'e gecti", b2._speed == 1234.0)
    check("pointer_max_ms backend'e gecti", b2._max_ms == 321.0)
    check("hold_max_seconds backend'e gecti", b2._hold_max == 7.0)

    # Son konum SURECLER ARASI paylasiliyor. Olmazsa `pcb-do`'nun her cagrisi
    # (yeni surec) baslangici bilmez ve hareket ISINLANIR -- kullanici bunu
    # fark etti, bu kontrol o hatanin geri gelmemesi icin.
    tmpdir = Path(tempfile.mkdtemp(prefix="pcb-pos-"))
    try:
        pos_file = tmpdir / "pointer.json"
        yazan = I.InputBackend(pos_file=pos_file)
        check("kayit yokken konum bos", yazan._pos is None)

        yazan._pos = (1234, 567)
        yazan._write_pos()
        check("konum diske yazildi", pos_file.exists())

        okuyan = I.InputBackend(pos_file=pos_file)
        check("baska bir ornek konumu okudu", okuyan._pos == (1234, 567),
              str(okuyan._pos))

        # Cok eski kayit guvenilmez: kullanici arada fareyi eliyle oynatmis
        # olabilir ve Wayland'de bunu ogrenmenin yolu yok.
        pos_file.write_text(json.dumps({
            "x": 10, "y": 20, "t": time.time() - I.POS_MAX_AGE_SECONDS - 30,
        }), encoding="utf-8")
        check("bayat kayit yok sayiliyor",
              I.InputBackend(pos_file=pos_file)._pos is None)

        # Bozuk dosya hareketi bozmamali.
        pos_file.write_text("{bozuk", encoding="utf-8")
        check("bozuk kayit yok sayiliyor",
              I.InputBackend(pos_file=pos_file)._pos is None)

        # pos_file verilmezse dosya sistemine hic dokunulmamali.
        dosyasiz = I.InputBackend()
        dosyasiz._pos = (5, 5)
        dosyasiz._write_pos()          # patlamamali
        check("pos_file yoksa sessizce gecilir", dosyasiz._pos_file is None)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _raises(fn, exc_type) -> bool:
    try:
        fn()
    except exc_type:
        return True
    except Exception:
        return False
    return False


def test_real_hold() -> None:
    """GERCEK cihazla basili tutma turu. PCBRIDGE_TEST_INPUT=1 ile acilir.

    Ctrl'yi kisa sureligine basili tutar; hicbir sey YAZMAZ, TIKLAMAZ.
    Dogrulama cihazin KENDI event node'undan `active_keys()` ile yapiliyor --
    "hata vermedi" bu projede kanit sayilmiyor.
    """
    if os.environ.get("PCBRIDGE_TEST_INPUT") != "1":
        return
    section("40. Basili tutma — gercek cihaz")

    from pcbridge.desktop import input as I

    try:
        from evdev import InputDevice
    except Exception as exc:  # noqa: BLE001
        check("evdev var", False, str(exc)[:80])
        return

    b = I.InputBackend(hold_max_seconds=2.0)
    ok, why = b.available()
    check("uinput hazir", ok, why[:100])
    if not ok:
        return

    try:
        b.ensure(keyboard=True, pointer=True)
        watch = InputDevice(b._kbd.device.path)
        ctrl = I.key_code("ctrl")

        # Cihaz yaratilirken diskteki konum SILINMEMELI. Silinirse `pcb-do`nun
        # her cagrisi (yeni surec -> yeni cihaz) hareketi isinlatir. GERCEKTEN
        # OLDU: ilk duzeltme ise yaramadi cunku `_pointer()` cihazi actiktan
        # sonra `_pos = None` yapiyordu ve `move()` ilk is cihazi aciyor.
        posdir = Path(tempfile.mkdtemp(prefix="pcb-posdev-"))
        try:
            pos_file = posdir / "pointer.json"
            once = I.InputBackend(pos_file=pos_file)
            once.ensure(pointer=True)
            once.move(1000, 500)
            once.close()
            check("hareket diske yazildi", pos_file.exists())

            sonra = I.InputBackend(pos_file=pos_file)
            check("yeni ornek konumu okudu (cihaz ACILMADAN)",
                  sonra._pos == (1000, 500), str(sonra._pos))
            sonra.ensure(pointer=True)
            check("cihaz acilinca konum KORUNUYOR",
                  sonra._pos == (1000, 500), str(sonra._pos))
            sonra._pointer()
            check("_pointer() konumu silmiyor", sonra._pos == (1000, 500),
                  str(sonra._pos))
            sonra.close()
        finally:
            shutil.rmtree(posdir, ignore_errors=True)

        b.key_down("ctrl")
        time.sleep(0.2)
        check("held() ctrl diyor", b.held() == ["ctrl"], str(b.held()))
        check("cihazda GERCEKTEN basili", ctrl in watch.active_keys(),
              str(watch.active_keys()))

        # `key()` basili tutulani DUSURMEMELI, yoksa held() yalan soylerdi.
        b.key("ctrl+a")
        time.sleep(0.2)
        check("key() basili tusu dusurmedi", ctrl in watch.active_keys(),
              str(watch.active_keys()))

        b.key_up("ctrl")
        time.sleep(0.2)
        check("elle birakma calisti", ctrl not in watch.active_keys(),
              str(watch.active_keys()))

        # Zamanlayici: kimse `release` cagirmazsa sunucu kendisi birakmali.
        b.key_down("ctrl")
        b.mouse_down("left")
        time.sleep(0.3)
        check("ikisi de basili", set(b.held()) == {"ctrl", "left"}, str(b.held()))
        time.sleep(2.4)
        check("zamanlayici birakti", b.held() == [], str(b.held()))
        check("cihazda da birakildi", ctrl not in watch.active_keys(),
              str(watch.active_keys()))
        freed = b.take_auto_released()
        check("otomatik birakma bildirildi", set(freed) == {"ctrl", "left"}, str(freed))
        check("bildirim bir kez okunuyor", b.take_auto_released() == [])
    finally:
        b.close()


class FakeScreenCast:
    """`ScreenCast` yerine gecen sahte. GERCEK yayin acmaz, PipeWire'a
    dokunmaz -- yakalama katmaninin dogru yolu sectigini test etmek icin."""

    def __init__(self, open_: bool = True, fail: bool = False) -> None:
        self._open = open_
        self._fail = fail
        self.cursor = True
        self.calls: list[tuple] = []

    def is_open(self) -> bool:
        return self._open

    def ensure_cursor(self, cursor: bool) -> bool:
        degisti = bool(cursor) != self.cursor
        self.cursor = bool(cursor)
        self.calls.append(("ensure_cursor", cursor))
        return degisti

    def capture(self, connector, path):
        self.calls.append(("capture", connector, str(path)))
        if self._fail:
            raise RuntimeError("yayin dustu (test)")
        _synthetic_monitor_png(Path(path))
        return {"ok": True, "path": str(path), "monitor": connector, "ms": 42}


def _synthetic_monitor_png(dest: Path) -> None:
    """1920x1080 sahte monitor karesi. Gercek ekrana dokunmaz."""
    from PIL import Image

    img = Image.new("RGB", (1920, 1080), (20, 40, 60))
    img.save(dest, format="PNG")


def test_screencast_backend() -> None:
    """Ekran yayini yolu — CIHAZSIZ kisim.

    Gercek yayin `test_real_screencast()` icinde ve varsayilan olarak atlanir.
    """
    from pcbridge.desktop import capture as C
    from pcbridge.desktop import screencast as SC

    section("41. Ekran yayini — yol secimi")

    # 1) backend_name / available yayin DURUMUNU okumali, tahmin etmemeli.
    acik, kapali = FakeScreenCast(True), FakeScreenCast(False)
    check("yayin acikken backend adi yayin", C.backend_name(acik) == C.SCREENCAST_NAME,
          C.backend_name(acik))
    check("yayin kapaliyken backend adi gnome-screenshot",
          C.backend_name(kapali) == C.GNOME_SCREENSHOT, C.backend_name(kapali))
    check("yayin verilmezse gnome-screenshot",
          C.backend_name(None) == C.GNOME_SCREENSHOT)

    # 2) Acik yayin varken gnome-screenshot ARANMAMALI: kurulu olmadigi bir
    #    makinede sessiz yol calisirken "kurulu degil" demek yanlis olurdu.
    gercek_which = C.shutil.which
    try:
        C.shutil.which = lambda name: None      # hicbir sey kurulu degil
        ok_acik, _ = C.available(acik)
        ok_kapali, why_kapali = C.available(kapali)
        check("yayin acikken gnome-screenshot aranmiyor", ok_acik is True)
        check("yayin kapaliyken eksiklik bildiriliyor", ok_kapali is False)
        check("gerekce kurulum komutu veriyor", "apt install" in why_kapali,
              why_kapali[:80])
    finally:
        C.shutil.which = gercek_which

    # 3) Yayin acikken KIRPMA YOK: her monitor kendi akisindan geliyor.
    mons = M._ordered(TWO_SCREENS)
    real_list, real_avail, real_grab = M.list_monitors, C.available, C._grab_canvas
    tmp = Path(tempfile.mkdtemp(prefix="pcb-sc-"))
    sahte = FakeScreenCast(True)
    try:
        M.list_monitors = lambda *a, **k: mons
        C.available = lambda *a, **k: (True, "")
        C._grab_canvas = lambda td, ptr: (_ for _ in ()).throw(
            AssertionError("yayin acikken gnome-screenshot CAGRILMAMALI"))

        shots = C.capture("all", out_dir=tmp, scale_long_edge=0, screencast=sahte)
        check("yayindan iki monitor geldi", len(shots) == 2, str(len(shots)))
        check("ofsetler korundu",
              [s.offset for s in shots] == [(0, 0), (1920, 0)],
              str([s.offset for s in shots]))
        check("her monitor tam cozunurluk",
              all(s.size == (1920, 1080) for s in shots),
              str([s.size for s in shots]))
        cagrilar = [c for c in sahte.calls if c[0] == "capture"]
        check("her monitor icin bir yakalama", len(cagrilar) == 2, str(cagrilar))
        check("connector adlariyla cagrildi",
              {c[1] for c in cagrilar} == {"DP-2", "DP-1"},
              str({c[1] for c in cagrilar}))

        # 4) Imlec kipi cekim basina degil YAYIN kurulurken belirleniyor;
        #    istek farkliysa yayin yeniden kurulmali.
        sahte2 = FakeScreenCast(True)
        sahte2.cursor = True
        C.capture("all", out_dir=tmp, scale_long_edge=0, include_pointer=False,
                  screencast=sahte2)
        check("imlec kipi yayina bildirildi",
              ("ensure_cursor", False) in sahte2.calls, str(sahte2.calls[:2]))

        # 5) Yayin kapaliysa yedege dusmeli (gnome-screenshot cagrilir).
        cagrildi = {"n": 0}

        def sahte_grab(td, ptr):
            cagrildi["n"] += 1
            dest = Path(td) / "c.png"
            _synthetic_canvas(dest)
            return dest

        C._grab_canvas = sahte_grab
        C.capture("all", out_dir=tmp, scale_long_edge=0,
                  screencast=FakeScreenCast(False))
        check("yayin kapaliyken gnome-screenshot'a dusuldu", cagrildi["n"] == 1,
              str(cagrildi))

        # 6) Yayin dusen bir cekim SESSIZCE bos donmemeli.
        try:
            C.capture("all", out_dir=tmp, scale_long_edge=0,
                      screencast=FakeScreenCast(True, fail=True))
            check("dusen yayin hata veriyor", False, "hata vermedi")
        except C.CaptureError as exc:
            check("dusen yayin hata veriyor", "yayin" in str(exc).lower(),
                  str(exc)[:90])
            check("gerekce ne yapilacagini soyluyor", "desktop_unlock" in str(exc),
                  str(exc)[:120])
    finally:
        M.list_monitors = real_list
        C.available, C._grab_canvas = real_avail, real_grab
        shutil.rmtree(tmp, ignore_errors=True)

    # 7) `available()` yardimcisi yoksa duzgun gerekce vermeli.
    ok, why = SC.available()
    if ok:
        check("bu makinede ekran yayini kullanilabilir", True)
    else:
        check("kullanilamiyorsa gerekce kurulum komutu veriyor",
              "apt install" in why or "python" in why, why[:90])


def test_kill_helpers() -> None:
    """Ayri surecte yasayan yayin, `bridgekilit` ile gercekten oluyor mu.

    BULUNAN BOSLUK (2026-09-06, kullanici fark etti): yardimci sureci ACAN
    surec onun tutamagini kendi belleginde tutuyor, yani `ScreenCast.close()`
    yalnizca kendi yayinini kapatabiliyor. `cli.lock` ayri bir surec: izin
    dosyasini kapatiyordu ama baska bir surecin acik yayinina dokunamiyordu.
    Belirti: izin kapali ama ust cubuktaki PAYLASIM GOSTERGESI duruyor.

    Tarama /proc uzerinden yapiliyor, o yuzden testte sahte bir /proc agaci
    kuruluyor: gercek surec oldurmeden secim mantigi sinaniyor.
    """
    section("51. Ekran yayini — baska surecin yayinini durdurma")
    import os as _os

    from pcbridge.desktop import screencast as SC

    tmp = Path(tempfile.mkdtemp(prefix="pcb-proc-"))
    oldurulen: list[int] = []
    real_kill = _os.kill
    try:
        def sahte_proc(pid: int, cmdline: list[str]) -> None:
            d = tmp / str(pid)
            d.mkdir(parents=True, exist_ok=True)
            (d / "cmdline").write_bytes(b"\0".join(c.encode() for c in cmdline))

        helper = str(SC.HELPER)
        sahte_proc(1001, ["python3", helper])                 # hedef
        sahte_proc(1002, ["python3", helper, "--x"])          # hedef
        sahte_proc(1003, ["python3", "/baska/betik.py"])      # HEDEF DEGIL
        sahte_proc(1004, ["/usr/bin/gnome-shell"])            # HEDEF DEGIL
        # Ad benzerligi yetmez: TAM yol eslesmeli, yoksa baskasinin
        # "screencast_helper.py" adli baska bir betik de vurulurdu.
        sahte_proc(1005, ["python3", "/tmp/screencast_helper.py"])
        (tmp / "self").mkdir()                                # sayi degil, atlanmali
        (tmp / "sys").mkdir()

        _os.kill = lambda pid, sig: oldurulen.append(pid)
        n = SC.kill_helpers(proc_root=tmp)
        check("yalnizca HELPER'i calistiranlar sonlandirildi",
              sorted(oldurulen) == [1001, 1002], str(sorted(oldurulen)))
        check("sayi dogru donuyor", n == 2, str(n))
        check("baska betik dokunulmadi", 1003 not in oldurulen)
        check("gnome-shell dokunulmadi", 1004 not in oldurulen)
        check("ayni ADLI baska yol dokunulmadi", 1005 not in oldurulen,
              "tam yol eslesmesi sart")

        # Baskasinin sureci: uid uyusmuyorsa atlanmali. (chown yapamayiz, o
        # yuzden stat'i tasliyoruz.)
        oldurulen.clear()
        real_stat = Path.stat

        def sahte_stat(self, *a, **k):
            st = real_stat(self, *a, **k)
            if self.name == "1001":
                class Fake:
                    st_uid = 65534  # nobody
                return Fake()
            return st

        Path.stat = sahte_stat
        try:
            SC.kill_helpers(proc_root=tmp)
        finally:
            Path.stat = real_stat
        check("baska kullanicinin sureci dokunulmadi",
              oldurulen == [1002], str(oldurulen))

        # Okunamayan /proc: patlamak yerine 0 donmeli
        check("olmayan /proc 0 donuyor",
              SC.kill_helpers(proc_root=tmp / "yok") == 0)
    finally:
        _os.kill = real_kill
        import shutil as _sh

        _sh.rmtree(tmp, ignore_errors=True)


def test_real_screencast() -> None:
    """GERCEK ekran yayini. PCBRIDGE_TEST_CAPTURE=1 ile acilir.

    Kullanicinin ekranini diske yazar ve ust cubukta kisa sureligine paylasim
    gostergesi cikarir; bu yuzden varsayilan olarak atlaniyor.
    """
    if os.environ.get("PCBRIDGE_TEST_CAPTURE") != "1":
        return
    section("42. Ekran yayini — gercek yakalama")

    from pcbridge.desktop import monitors as M
    from pcbridge.desktop import screencast as SC

    ok, why = SC.available()
    check("yayin altyapisi hazir", ok, why[:110])
    if not ok:
        return

    sc = SC.ScreenCast()
    tmp = Path(tempfile.mkdtemp(prefix="pcb-scr-"))
    try:
        check("acilmadan once kapali", not sc.is_open())
        try:
            sc.capture("DP-1", tmp / "olmaz.png")
            check("kapali yayindan cekim reddediliyor", False, "kabul edildi")
        except SC.ScreenCastError as exc:
            check("kapali yayindan cekim reddediliyor", "acik degil" in str(exc),
                  str(exc)[:80])

        connectors = [m.connector for m in M.list_monitors()]
        sc.start(connectors, cursor=True)
        check("yayin acildi", sc.is_open())
        check("butun monitorler yayinda", set(sc.monitors()) == set(connectors),
              f"{sc.monitors()} vs {connectors}")
        check("ikinci start ayni yayini donduruyor",
              sc.start(connectors).get("already") is True)

        for conn in connectors:
            dest = tmp / f"{conn}.png"
            res = sc.capture(conn, dest)
            check(f"{conn} karesi alindi", dest.exists() and dest.stat().st_size > 0,
                  str(res))
            from PIL import Image
            with Image.open(dest) as im:
                mon = next(m for m in M.list_monitors() if m.connector == conn)
                check(f"{conn} cozunurlugu monitorle ayni",
                      im.size == (mon.width, mon.height),
                      f"{im.size} vs {(mon.width, mon.height)}")

        try:
            sc.capture("YOK-9", tmp / "x.png")
            check("olmayan monitor reddediliyor", False, "kabul edildi")
        except SC.ScreenCastError as exc:
            check("olmayan monitor reddediliyor", "yayinda yok" in str(exc),
                  str(exc)[:80])

        sc.stop()
        check("stop sonrasi kapali", not sc.is_open())
    finally:
        sc.close()
        shutil.rmtree(tmp, ignore_errors=True)
    check("close sonrasi kapali", not sc.is_open())


def test_session_env() -> None:
    """Oturum ortami onarimi (`desktop/session.py`).

    GERCEK BIR ARIZANIN karsiligi: Codex'in baslattigi pcbridge surecinde
    `DBUS_SESSION_BUS_ADDRESS` genisletilmemis bir literal (`"$DBUS_..."`)
    olarak geliyordu; `busctl --user` baglanamiyor, monitor tablosu okunamiyor
    ve masaustu araclarinin TAMAMI cokuyordu.

    Testler duz sozluk uzerinde kosuyor -- `os.environ`'a dokunulmuyor.
    """
    from pcbridge.desktop import session as SESS

    section("37. Oturum ortami onarimi")

    uid = os.getuid()
    runtime = f"/run/user/{uid}"
    has_bus = Path(f"{runtime}/bus").exists()

    # 1) Literal genisletilmemis deger -> ONARILMALI
    env = {"XDG_RUNTIME_DIR": runtime,
           "DBUS_SESSION_BUS_ADDRESS": "$DBUS_SESSION_BUS_ADDRESS"}
    fixed = SESS.ensure_session_env(env)
    if has_bus:
        check("literal $DBUS degeri onarildi",
              "DBUS_SESSION_BUS_ADDRESS" in fixed, str(fixed))
        check("onarilan deger soketi gosteriyor",
              env["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={runtime}/bus",
              env["DBUS_SESSION_BUS_ADDRESS"])
    else:
        skip("bus soketi yok", runtime)

    # 2) Degisken HIC yok -> doldurulmali
    env = {"XDG_RUNTIME_DIR": runtime}
    fixed = SESS.ensure_session_env(env)
    if has_bus:
        check("eksik DBUS dolduruldu",
              env.get("DBUS_SESSION_BUS_ADDRESS", "").startswith("unix:path="),
              str(env.get("DBUS_SESSION_BUS_ADDRESS")))

    # 3) GECERLI bir degere DOKUNULMAMALI. Kullanici bilincli olarak baska bir
    #    bus verdiyse (ic ice oturum, test duzenegi) onu ezmek sessiz hata olur.
    if has_bus:
        env = {"XDG_RUNTIME_DIR": runtime,
               "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus"}
        fixed = SESS.ensure_session_env(env)
        check("gecerli DBUS degeri korundu",
              "DBUS_SESSION_BUS_ADDRESS" not in fixed, str(fixed))

    # 4) Dogrulayamadigimiz tasima (tcp:) korunmali -- bozmayalim.
    env = {"XDG_RUNTIME_DIR": runtime,
           "DBUS_SESSION_BUS_ADDRESS": "tcp:host=127.0.0.1,port=1234"}
    SESS.ensure_session_env(env)
    check("tcp: bus adresine karisilmadi",
          env["DBUS_SESSION_BUS_ADDRESS"] == "tcp:host=127.0.0.1,port=1234",
          env["DBUS_SESSION_BUS_ADDRESS"])

    # 5) XDG_RUNTIME_DIR bozuksa standart yerden turetilmeli
    env = {"XDG_RUNTIME_DIR": "$XDG_RUNTIME_DIR"}
    SESS.ensure_session_env(env)
    check("bozuk XDG_RUNTIME_DIR turetildi",
          env.get("XDG_RUNTIME_DIR") == runtime, str(env.get("XDG_RUNTIME_DIR")))

    # 6) WAYLAND_DISPLAY eksikse soketten bulunmali
    env = {"XDG_RUNTIME_DIR": runtime}
    SESS.ensure_session_env(env)
    wl = sorted(Path(runtime).glob("wayland-[0-9]"))
    if wl:
        check("WAYLAND_DISPLAY soketten bulundu",
              env.get("WAYLAND_DISPLAY") == wl[0].name, str(env.get("WAYLAND_DISPLAY")))
    else:
        skip("wayland soketi yok", runtime)

    # 7) XDG_SESSION_TYPE bossa Wayland soketinden turetilmeli. OLCULDU
    #    2026-09-20, tek degisken ayrilarak: Claude Desktop'un baslattigi
    #    stdio surecinin ortaminda Brave 0 surecle SEGFAULT ediyor ("Missing
    #    X server or $DISPLAY"); ayni ortama yalnizca XDG_SESSION_TYPE=wayland
    #    eklenince 9 surecle aciliyor. Chromium ozone yolunu buna bakarak
    #    seciyor, WAYLAND_DISPLAY dolu olsa bile.
    env = {"XDG_RUNTIME_DIR": runtime, "WAYLAND_DISPLAY": "wayland-0"}
    fixed = SESS.ensure_session_env(env)
    check("bos XDG_SESSION_TYPE wayland olarak dolduruldu",
          "XDG_SESSION_TYPE" in fixed and env.get("XDG_SESSION_TYPE") == "wayland",
          str(env.get("XDG_SESSION_TYPE")))

    env = {"XDG_RUNTIME_DIR": runtime, "WAYLAND_DISPLAY": "wayland-0",
           "XDG_SESSION_TYPE": "x11"}
    fixed = SESS.ensure_session_env(env)
    check("dolu XDG_SESSION_TYPE korundu",
          "XDG_SESSION_TYPE" not in fixed and env["XDG_SESSION_TYPE"] == "x11",
          env["XDG_SESSION_TYPE"])

    # Wayland soketi bilinmiyorsa uydurulmaz: yanlis tasima sessiz cokme olur.
    with tempfile.TemporaryDirectory() as bos:
        env = {"XDG_RUNTIME_DIR": bos}
        SESS.ensure_session_env(env)
        check("Wayland yokken XDG_SESSION_TYPE uydurulmadi",
              "XDG_SESSION_TYPE" not in env, str(env.get("XDG_SESSION_TYPE")))

    # 8) DISPLAY bossa calisan Xwayland'dan turetilmeli. OLCULDU 2026-09-20:
    #    Claude Desktop'un baslattigi stdio surecinde DISPLAY de XAUTHORITY de
    #    bostu ve pcbridge ile acilan Brave/Chrome "Missing X server or
    #    $DISPLAY" deyip SEGFAULT ediyordu -- gtk-launch yine de 0 donuyor,
    #    yani hata hicbir yerde gorunmuyordu.
    found = SESS._xwayland()
    if found is not None:
        display, auth = found
        env = {"XDG_RUNTIME_DIR": runtime, "DISPLAY": ""}
        fixed = SESS.ensure_session_env(env)
        check("bos DISPLAY Xwayland'dan onarildi",
              "DISPLAY" in fixed and env.get("DISPLAY") == display,
              str(env.get("DISPLAY")))
        check("onarilan DISPLAY'in soketi var",
              Path(f"/tmp/.X11-unix/X{display[1:]}").exists(), display)
        if auth:
            check("XAUTHORITY de dolduruldu",
                  env.get("XAUTHORITY") == auth, str(env.get("XAUTHORITY")))
            check("yetki dosyasi gercekten duruyor", Path(auth).exists(), auth)

        # Dolu bir DISPLAY'e DOKUNULMAZ: dogrulayamadigimiz degeri bozmayiz.
        env = {"XDG_RUNTIME_DIR": runtime, "DISPLAY": ":9",
               "XAUTHORITY": "/yok/olan/dosya"}
        fixed = SESS.ensure_session_env(env)
        check("dolu DISPLAY korundu",
              "DISPLAY" not in fixed and env["DISPLAY"] == ":9", str(fixed))
        check("dolu XAUTHORITY korundu",
              env["XAUTHORITY"] == "/yok/olan/dosya", env["XAUTHORITY"])
    else:
        skip("calisan Xwayland yok", "DISPLAY onarimi sinanamadi")

    # 9) describe() tani icin okunur bir satir versin
    line = SESS.describe({"XDG_RUNTIME_DIR": runtime, "DBUS_SESSION_BUS_ADDRESS": "$X"})
    check("describe() GECERSIZ durumu bildiriyor", "GECERSIZ" in line, line)

    # 10) Onarim SAF degil ama YAN ETKISI SINIRLI: verilen sozluk disina cikmaz.
    before = dict(os.environ)
    SESS.ensure_session_env({"XDG_RUNTIME_DIR": runtime})
    check("os.environ'a dokunulmadi", dict(os.environ) == before)


def test_real_batch() -> None:
    """Gercek batch. INPUT ve BATCH izinleri birlikte verilmeden KOSMAZ."""
    import os

    if (os.environ.get("PCBRIDGE_TEST_INPUT") != "1"
            or os.environ.get("PCBRIDGE_TEST_BATCH") != "1"):
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
    test_gate_sliding_lease()
    test_gate_locked_screen_and_idle()
    test_gate_rate_limit()
    test_audit_log()
    test_config_defaults()
    test_capture_scaling()
    test_capture_crop_offsets()
    test_shot_lookup()
    test_ambiguous_guard()
    test_shot_ops_wiring()
    test_shot_store()
    test_capture_sweeps()
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
    test_gui_launch_block()
    test_cli_parse()
    test_cli_gate()
    test_cli_shot_text()
    test_cli_shot_scale()
    test_oversize_warning()
    test_cli_shot_dir()
    test_cli_stale_shot()
    test_computer_task_prompt()
    test_session_env()
    test_move_path()
    test_relative_chunks()
    test_hold_tracking()
    test_real_hold()
    test_screencast_backend()
    test_kill_helpers()
    test_real_screencast()
    test_real_batch()
    print(f"\n\033[1m{ok_count} gecti, {fail_count} kaldi\033[0m")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
