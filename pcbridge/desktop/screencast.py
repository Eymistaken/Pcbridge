"""Ekran yayini — `screencast_helper.py`'yi surer, yayinin omrunu yonetir.

NEDEN AYRI BIR SUREC VAR
    `gi` (PyGObject) ve GStreamer venv'de yok, sistem python3'unde hazir.
    `uitree.py` de ayni sebeple `atspi_helper.py`'yi ayri surecte calistiriyor
    -- ama ORADAKI surec cagri basina aciliyor, BURADAKI kalici.

    Fark bilincli: yayin acik kaldigi surece bir kare ~80-330 ms'de geliyor;
    her cekimde yayini kurup yikmak bunu ~250 ms'lik sabit bir bedele
    baglardi (oturum + akis + PipeWire pazarligi).

    Yan fayda: yayin o surecte yasiyor. Surec olurse yayin da oluyor, yani
    pcbridge cokerse ekran paylasimi acik kalmiyor.

BASKA SURECIN YAYINI (olculdu 2026-09-06)
    Yardimci sureci ACAN surec onun tutamagini KENDI belleginde tutuyor, yani
    `ScreenCast.close()` yalnizca kendi yayinini kapatabiliyor. Bu bir boslugu
    ortaya cikardi: `bridgekilit` (`cli.lock`) ayri bir surec, izin dosyasini
    kapatiyor ama BASKA bir surecin acik yayinina dokunamiyor. Ayni sey
    telefondan gelen `desktop_lock` icin de gecerliydi -- servis kendi
    yayinini kapatir, ayni anda calisan bir `--stdio` istemcisininki acik
    kalirdi.
    Belirtisi: izin kapali (`desktop_unlock.json` -> `until: 0`) ama ust
    cubuktaki paylasim gostergesi DURUYOR. Kullanici bunu gordu ve sordu.
    Gosterge "ajan ekranini gorebiliyor" demek; acil kapatmadan sonra durmasi
    ya erisimin surdugu ya da gostergenin yalan soyledigi anlamina gelir --
    ikisi de kabul edilemez.
    Cozum: `kill_helpers()` /proc'u tarayip HELPER'i calistiran butun
    surecleri sonlandiriyor. Acil kapatma zaten "hepsini durdur" demek.

OLCULDU 2026-08-03 (bu makine, yayin ACIKKEN ardisik 12 cekim)
    DP-1: 312-332 ms (8 cekim, tutarli)
    DP-2:  77- 87 ms (4 cekim, tutarli)
    ortalama 240 ms · gnome-screenshot 833 ms · XDG portal 497 ms

    Iki monitor arasindaki ~4 kat farkin SEBEBI BILINMIYOR. Rastgele degil,
    her cekimde ayni. Tahmin yurutmuyoruz; ikisi de mevcut yoldan hizli
    oldugu icin is buradan devam ediyor.

    Acik duran yayinin maliyeti OLCULDU: gnome-shell CPU %35,2 -> %35,2.
    Kimse kare tuketmezken PipeWire uretmiyor, yani yayini acik tutmak
    bedava.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
from pathlib import Path

HELPER = Path(__file__).resolve().parent / "screencast_helper.py"
SYSTEM_PYTHON = "python3"

# Yardimci kendi icinde zaman asimi uyguluyor (kare 8 sn, yayin kurulumu
# 10 sn). Buradaki sinir onun USTUNDE: yardimci hic cevap vermiyorsa
# (cokme, kilitlenme) burada kesiyoruz.
REPLY_TIMEOUT = 20.0
START_TIMEOUT = 25.0


class ScreenCastError(RuntimeError):
    """Yayin acilamadi ya da kare alinamadi."""


def available() -> tuple[bool, str]:
    """(kullanilabilir mi, degilse Turkce gerekce).

    Uc sey ariyoruz: sistem python'u, `gi` + `Gst`, ve GStreamer'in PipeWire
    kaynagi. Ucuncusu ayri bir paket (`gstreamer1.0-pipewire`) ve yoklugu
    ancak boru hatti kurulurken anlasilirdi -- burada onceden soyluyoruz.
    """
    if not shutil.which(SYSTEM_PYTHON):
        return False, f"`{SYSTEM_PYTHON}` bulunamadi (sistem python'u gerekli)."
    if not HELPER.exists():
        return False, f"{HELPER} yok."
    probe = (
        "import gi; gi.require_version('Gst','1.0');"
        "from gi.repository import Gst; Gst.init(None);"
        "assert Gst.ElementFactory.make('pipewiresrc'), 'pipewiresrc yok';"
        "assert Gst.ElementFactory.make('pngenc'), 'pngenc yok'"
    )
    try:
        proc = subprocess.run(
            [SYSTEM_PYTHON, "-c", probe],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"GStreamer denetimi calismadi: {exc}"
    if proc.returncode != 0:
        return False, (
            "GStreamer PipeWire destegi yok "
            f"({(proc.stderr or '').strip().splitlines()[-1:] or ['?']}). "
            "Kurulum: sudo apt install gstreamer1.0-pipewire "
            "gstreamer1.0-plugins-good python3-gi"
        )
    return True, ""


def kill_helpers(proc_root: str | Path = "/proc") -> int:
    """HELPER'i calistiran BUTUN surecleri sonlandir -> sonlandirilan sayisi.

    Neden /proc taramasi da PID dosyasi degil: yayini acan surec kayit
    tutmayi unutabilir ya da cokebilir, ve ayni anda birden fazla yayin
    olabiliyor (MCP sunucusu kalici bir tane tutuyor, `pcb-shot` her
    cagrisinda kisa omurlu bir tane aciyor). Tarama yetim surecleri de
    yakaliyor.

    YALNIZCA kendi kullanicimizin surecleri ve cmdline'inda HELPER'in TAM
    yolu gecenler. Baskasinin sureci sonlandirilmaz.
    """
    root = Path(proc_root)
    target = str(HELPER)
    me = os.getuid()
    killed = 0
    try:
        entries = list(root.iterdir())
    except OSError:
        return 0
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != me:
                continue
            cmdline = (entry / "cmdline").read_bytes().decode(
                "utf-8", "replace"
            ).split("\0")
        except OSError:
            continue
        if target not in cmdline:
            continue
        pid = int(entry.name)
        if pid == os.getpid():  # pragma: no cover — kendimizi vurmayalim
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed += 1
        except OSError:
            continue
    return killed


class ScreenCast:
    """Kalici yardimci surec + acik yayin.

    Butun genel metotlar kilitli: `computer_batch` ile `screen_capture` ayni
    anda gelebiliyor ve ikisi de ayni boruyu kullaniyor. Kilitsiz birakilirsa
    bir cagrinin cevabini digeri okur -- sessiz ve tekrarlanmasi zor bir hata.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.RLock()
        self._monitors: list[str] = []
        self._cursor = True

    # ------------------------------------------------------------ surec
    def _spawn(self) -> subprocess.Popen:
        env = os.environ.copy()
        return subprocess.Popen(
            [SYSTEM_PYTHON, str(HELPER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,   # GStreamer/GLib gurultusu
            text=True, bufsize=1, env=env, start_new_session=True,
        )

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _send(self, req: dict, timeout: float = REPLY_TIMEOUT) -> dict:
        """Tek istek, tek cevap. Yardimci olmusse ayaga kaldirmaz -- cagiran
        bilincli olarak `start()` demeli, yoksa yayin sessizce yeniden acilir
        ve gosterge kullaniciya yalan soylerdi."""
        if not self._alive():
            raise ScreenCastError("yayin yardimcisi calismiyor")
        proc = self._proc
        assert proc is not None and proc.stdin is not None and proc.stdout is not None

        cevap: dict = {}
        hata: list[str] = []

        def oku() -> None:
            try:
                line = proc.stdout.readline()
            except Exception as exc:  # noqa: BLE001
                hata.append(str(exc))
                return
            if not line:
                hata.append("yardimci cevap vermeden kapandi")
                return
            try:
                cevap.update(json.loads(line))
            except ValueError as exc:
                hata.append(f"bozuk cevap: {exc}")

        try:
            proc.stdin.write(json.dumps(req) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ScreenCastError(f"yardimciya yazilamadi: {exc}") from exc

        th = threading.Thread(target=oku, daemon=True)
        th.start()
        th.join(timeout)
        if th.is_alive():
            # Cevap gelmedi: boru artik guvenilmez, sureci bitir.
            self.close()
            raise ScreenCastError(
                f"yayin yardimcisi {timeout:.0f} saniyede cevap vermedi"
            )
        if hata:
            raise ScreenCastError(hata[0])
        if not cevap.get("ok"):
            raise ScreenCastError(str(cevap.get("error", "bilinmeyen hata")))
        return cevap

    # ------------------------------------------------------------- yayin
    def start(self, monitors: list[str], cursor: bool = True) -> dict:
        """Yayini ac. Zaten acikas ayni yayini dondurur."""
        with self._lock:
            if self._alive() and self._monitors:
                return {"already": True, "monitors": list(self._monitors)}
            ok, why = available()
            if not ok:
                raise ScreenCastError(why)
            if not self._alive():
                self._proc = self._spawn()
            out = self._send(
                {"cmd": "start", "monitors": list(monitors), "cursor": bool(cursor)},
                timeout=START_TIMEOUT,
            )
            self._monitors = list(out.get("monitors") or monitors)
            self._cursor = bool(cursor)
            return out

    def stop(self) -> None:
        """Yayini kapat ama sureci yasat (tekrar acilabilir)."""
        with self._lock:
            if self._alive() and self._monitors:
                try:
                    self._send({"cmd": "stop"})
                except ScreenCastError:
                    pass
            self._monitors = []

    def close(self) -> None:
        """Sureci de bitir. Yayin surecle birlikte oluyor."""
        with self._lock:
            self._monitors = []
            proc, self._proc = self._proc, None
            if proc is None:
                return
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass

    def is_open(self) -> bool:
        with self._lock:
            return self._alive() and bool(self._monitors)

    def ensure_cursor(self, cursor: bool) -> bool:
        """Imlec kipini istenen hale getir; degistiyse yayini YENIDEN KURAR.

        Imlec, cekim basina degil YAYIN kurulurken belirleniyor
        (`RecordMonitor` `cursor-mode`). `screen_capture(include_pointer=...)`
        cagri basina degisebildigi icin burasi gerekiyor. Yeniden kurmak
        ~113 ms; alternatifi flas patlatan yola dusmekti.

        Doner: yeniden kuruldu mu.
        """
        with self._lock:
            if not self.is_open() or bool(cursor) == self._cursor:
                return False
            monitors = list(self._monitors)
            self.stop()
            self.start(monitors, cursor=cursor)
            return True

    def monitors(self) -> list[str]:
        with self._lock:
            return list(self._monitors)

    # ---------------------------------------------------------- yakalama
    def capture(self, connector: str, path: str | Path) -> dict:
        """Acik yayindan tek kare. Yayin kapaliysa ScreenCastError."""
        with self._lock:
            if not self.is_open():
                raise ScreenCastError(
                    "ekran yayini acik degil (masaustu izni verilince aciliyor)"
                )
            return self._send(
                {"cmd": "capture", "monitor": str(connector), "path": str(path)}
            )


__all__ = ["ScreenCast", "ScreenCastError", "available"]
