"""Pano erisimi, metin girisi icin tek bir arayuzun arkasinda (Task 5.4).

NEDEN AYRI MODUL
    Metin panodan yaziliyor: `wl-copy` + Ctrl+V `tr+intl` duzeninden
    bagimsiz, ham keycode degil. Task 5.4'e kadar bu uc islem `input.py`nin
    icinde, uinput cihazinin yanindaydi. Girdi native yardimciya tasininca
    ORKESTRASYON Python'da kaliyor (yedekle -> koy -> native Ctrl+V -> geri
    yukle) ve yalnizca bu uc islem degisiyor; bu yuzden bir dikis gerekiyor:
    `Clipboard`.

BORU TUZAGI
    `wl-copy` panonun sahibi olarak arka planda yasamaya devam ediyor
    (Wayland'de pano icerigini kaynak surec servis eder). stdout/stderr
    yakalanirsa o arka plandaki cocuk borulari acik tutar ve `run()` EOF
    bekleyerek zaman asimina ugrar. Olculdu: 10 s timeout ile "keyboard type"
    araci tamamen kilitleniyordu. Yazma yolunda bu yuzden DEVNULL.

SATIR SONU
    Icerik HER TIPTE `--no-newline` ile okunur. wl-paste metin saydigi her
    tipe (yalnizca `text/*` degil: UTF8_STRING, STRING, TEXT) satir sonu
    ekliyor ve wl-copy geri yuklemeden sonra bu takma adlari BASA koyuyor.

TEK MIME TIPI
    `save()` yalnizca listedeki ILK tipi saklar. Hem text/html hem text/plain
    sunan bir pano yalnizca text/html olarak geri gelir: metin kalir, diger
    temsiller kaybolur. Okunamayan bir pano geri yuklemede TEMIZLENIR.
    Iki davranis da `tests/fixtures/native/clipboard_cases.json`da sabit.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol

from .. import distro as distrolib

TEXT_MIME = "text/plain;charset=utf-8"
TIMEOUT_SECONDS = 10

#: The capability report's wording for the one-type restore.
SINGLE_MIME_LIMITATION = (
    "Clipboard restore keeps only the first offered type; other representations are lost."
)


class ClipboardError(RuntimeError):
    """Pano yazilamadi. Mesaj kullaniciya aynen doner."""


@dataclass(frozen=True)
class Saved:
    """Panonun yedeklenen tek temsili."""

    mime: str
    data: bytes


class Clipboard(Protocol):
    """Metin girisinin panodan istedigi uc islem."""

    def save(self) -> Saved | None: ...

    def put_text(self, text: str) -> None: ...

    def restore(self, saved: Saved | None) -> None: ...


def _read(args: list[str], timeout: int = TIMEOUT_SECONDS):
    """wl-paste gibi okuyup CIKAN komutlar: ciktisini yakalayabiliriz."""
    return subprocess.run(args, capture_output=True, timeout=timeout, check=False)


def _write(args: list[str], data: bytes | None = None, timeout: int = TIMEOUT_SECONDS):
    """wl-copy: stdout/stderr YAKALANMAZ, yoksa asilir (modul notu: boru tuzagi)."""
    return subprocess.run(
        args,
        input=data,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )


class WlClipboard:
    """wl-clipboard programlari, girdi yolunun hep kullandigi argumanlarla."""

    def save(self) -> Saved | None:
        """Panonun mevcut icerigini yedekle. Bossa ya da okunamiyorsa None."""
        types = _read(["wl-paste", "--list-types"])
        if types.returncode != 0 or not types.stdout.strip():
            return None
        mime = types.stdout.decode("utf-8", "replace").splitlines()[0].strip()
        # Always `--no-newline`. wl-paste appends a newline to every type it
        # counts as text, and that is more than `text/*`: after one restore
        # wl-copy lists UTF8_STRING, STRING and TEXT first (measured
        # 2026-09-19), so the next save read `UTF8_STRING` with a newline and
        # the restore after it put one on the user's clipboard. For a binary
        # type the flag changes nothing.
        got = _read(["wl-paste", "--type", mime, "--no-newline"])
        if got.returncode != 0:
            return None
        return Saved(mime, got.stdout)

    def put_text(self, text: str) -> None:
        try:
            put = _write(["wl-copy", "--type", TEXT_MIME], data=text.encode())
        except FileNotFoundError as exc:
            raise ClipboardError(
                "wl-copy not found: " + distrolib.install_command("wl-clipboard")
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ClipboardError(
                "wl-copy did not answer (the clipboard owner may be stuck)"
            ) from exc
        if put.returncode != 0:
            raise ClipboardError(
                f"wl-copy exited {put.returncode}. Is the Wayland session visible? "
                "(WAYLAND_DISPLAY must reach the service)"
            )

    def restore(self, saved: Saved | None) -> None:
        if saved is None:
            _write(["wl-copy", "--clear"])
            return
        _write(["wl-copy", "--type", saved.mime], data=saved.data)


__all__ = [
    "Clipboard",
    "ClipboardError",
    "SINGLE_MIME_LIMITATION",
    "Saved",
    "TEXT_MIME",
    "WlClipboard",
]
