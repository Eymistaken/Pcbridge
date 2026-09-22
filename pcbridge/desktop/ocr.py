"""Ekrandan metin okuma (OCR) -- `find_text` / `wait_for_text` (Adim 8.6).

NEDEN
    Erisilebilirlik agaci olmayan pencerelerde (oyunlar, bazi Electron/Java
    uygulamalari) ajanin tek araci goruntu + tahmini beklemeydi. OLCULDU
    2026-09-21: oyunun yuklenmesi icin korlemesine "30 sn bekle" kullanildi
    ve dort `wait 30000`lik bir liste istemcinin 60 sn'lik zaman asimina
    takildi (Adim 8.7). Metni okuyup koordinatini veren bir arac hem bekleme
    tahminini hem goruntu jetonunu gereksiz kiliyor: cevap duz metin.

MOTOR
    `tesseract` komut satiri araci. Yerel calisiyor, ag istemiyor, Turkce
    dil verisi (`tesseract-ocr-tur`) paket deposunda var. Kurulu degilse
    araclar bunu ACIKCA soyluyor (kurulum komutuyla); tahmin ya da sessiz
    bos cevap yok. Goruntu diske YAZILMIYOR: PNG stdin'den veriliyor, cunku
    ekran goruntusu bu projenin en gizlilik-hassas ciktisi.

KOORDINAT
    OCR, yayimlanmis bir cekimin (`shot`) PNG'sinde calisiyor ve bulunan
    kutu O goruntunun pikselinde donuyor. Yani `mouse(shot=...)` ile dogrudan
    tiklanabiliyor; ofset ve olcek her zamanki gibi `capture.to_global`da.
    Kucuk goruntuler OCR icin buyutuluyor, kutu geri bolunuyor.

OLCULMEDI
    Bu makinede tesseract kurulu degil (2026-09-22) ve kurulum `sudo`
    istiyor. Ayristirma, eslestirme ve koordinat donusumu sahte bir
    `tesseract` betigiyle uctan uca sinaniyor; gercek dogruluk ve sure
    kullanicinin kurulumundan sonra olculecek (WALKTHROUGH, Adim 8.6).
"""

from __future__ import annotations

import difflib
import io
import os
import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image, ImageOps

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover - kuruluysa calismaz
    PIL_AVAILABLE = False

ENGINE = "tesseract"
INSTALL_HINT = "sudo apt install tesseract-ocr"
# Seyrek metin kipi: arayuz ve oyun ekranlari bir sayfa degil, daginik
# etiketler. Duzen analizi (psm 3) sutunlar arar ve kisa etiketleri kacirir.
PAGE_SEGMENTATION = "11"
# Tek bir okuma bu kadar surebilir. Bekleme dongusu kendi suresini ayrica
# sinirliyor; bu, asilmis bir motorun cagriyi kilitlemesine karsi.
RUN_TIMEOUT = 20.0
# Uzun kenari bundan kisa goruntuler 2 kat buyutulerek okunur: tesseract
# kucuk yaziyi (x-yuksekligi ~10 px) buyutulmus haliyle cok daha iyi okuyor.
# Tam bir monitor (1920) buyutulmez; dort kat piksel dort kat sure demek.
UPSCALE_BELOW = 1200
# Yaklasik eslesmenin kabul esigi (difflib orani). Kesin eslesme yoksa
# kullaniliyor ve sonucta "yaklasik" diye ayrica isaretleniyor. 12 harflik
# bir kelimede tek harf hatasi 0,92, iki harf 0,83. 0,75 fazla gevsekti: iki
# kelimelik "Yukleniyor tamam" tek basina "Yukleniyor..."a 0,77 ile uyuyordu.
FUZZY_MIN = 0.8
# `wait_for_text` yaklasik eslesmeyi ancak bu kadar benzerse "goruldu" sayar:
# yanlis pozitif bir bekleme erken biter ve ajan olmayan bir seye tiklar.
FUZZY_WAIT_MIN = 0.85
MAX_MATCHES = 10
LANGS_RE = re.compile(r"^[A-Za-z_]+(\+[A-Za-z_]+)*$")


class OcrError(RuntimeError):
    """Metin okunamadi: motor yok, dil verisi yok ya da motor hata verdi."""

    def __init__(self, message: str, *, missing: bool = False) -> None:
        super().__init__(message)
        self.missing = missing


@dataclass(frozen=True)
class Word:
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float
    line: tuple[int, int, int]  # (block, par, line) -- tesseract'in satiri


@dataclass(frozen=True)
class Match:
    """Bulunan metin, GORUNTU pikselinde."""

    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float
    exact: bool
    score: float

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.width // 2, self.top + self.height // 2)

    def as_dict(self) -> dict:
        x, y = self.center
        return {
            "text": self.text,
            "x": x,
            "y": y,
            "box": [self.left, self.top, self.width, self.height],
            "confidence": round(self.conf, 1),
            "exact": self.exact,
            "score": round(self.score, 3),
        }


# ------------------------------------------------------------------ motor
def available(langs: str) -> tuple[bool, str]:
    """(kullanilabilir mi, degilse Turkce gerekce + kurulum komutu)."""
    if not PIL_AVAILABLE:
        return False, "Pillow is missing (reinstall pcbridge with its [desktop] extra: `pcbridge setup`)"
    binary = shutil.which(ENGINE)
    if binary is None:
        return False, f"`{ENGINE}` is not installed. Install it: {INSTALL_HINT}"
    try:
        proc = subprocess.run(
            [binary, "--list-langs"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"`{ENGINE} --list-langs` calismadi: {exc}"
    have = {line.strip() for line in (proc.stdout + proc.stderr).splitlines()}
    missing = [lang for lang in langs.split("+") if lang not in have]
    if missing:
        packages = " ".join(f"tesseract-ocr-{lang}" for lang in missing)
        return False, (
            f"`{ENGINE}` is missing language data: {', '.join(missing)}. "
            f"Install it: sudo apt install {packages}"
        )
    return True, ""


def _prepare(png: Path) -> tuple[bytes, float]:
    """OCR'a verilecek goruntu (PNG baytlari) ve buyutme orani.

    Gri tonlama + kontrast germe; koyu zeminde acik yazi ters cevriliyor
    (tesseract koyu yaziyi acik zeminde okumak icin egitilmis). Kucuk
    goruntu 2 kat buyutuluyor.
    """
    with Image.open(png) as img:
        gray = img.convert("L")
    histogram = gray.histogram()
    total = sum(histogram) or 1
    mean = sum(value * count for value, count in enumerate(histogram)) / total
    if mean < 110:
        gray = ImageOps.invert(gray)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    factor = 2.0 if max(gray.size) < UPSCALE_BELOW else 1.0
    if factor != 1.0:
        gray = gray.resize(
            (int(gray.width * factor), int(gray.height * factor)), Image.LANCZOS
        )
    buffer = io.BytesIO()
    gray.save(buffer, format="PNG")
    return buffer.getvalue(), factor


def parse_tsv(tsv: str, factor: float = 1.0) -> list[Word]:
    """tesseract'in TSV ciktisindaki KELIMELER (seviye 5), goruntu pikselinde.

    Guveni -1 olan ve bos metinli satirlar kelime degil (sayfa, blok, satir
    kayitlari). `factor` buyutme orani: kutular ona bolunuyor.
    """
    words: list[Word] = []
    lines = tsv.splitlines()
    if not lines:
        return words
    header = lines[0].split("\t")
    try:
        col = {name: header.index(name) for name in (
            "level", "block_num", "par_num", "line_num", "left", "top",
            "width", "height", "conf", "text",
        )}
    except ValueError as exc:
        raise OcrError(f"{ENGINE} returned an unexpected TSV header: {exc}") from exc
    for raw in lines[1:]:
        cells = raw.split("\t")
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        try:
            if int(cells[col["level"]]) != 5:
                continue
            conf = float(cells[col["conf"]])
        except ValueError:
            continue
        text = cells[col["text"]].strip()
        if not text or conf < 0:
            continue
        try:
            left = int(cells[col["left"]]) / factor
            top = int(cells[col["top"]]) / factor
            width = int(cells[col["width"]]) / factor
            height = int(cells[col["height"]]) / factor
            line = (
                int(cells[col["block_num"]]),
                int(cells[col["par_num"]]),
                int(cells[col["line_num"]]),
            )
        except ValueError:
            continue
        words.append(Word(
            text=text,
            left=int(left),
            top=int(top),
            width=max(1, round(width)),
            height=max(1, round(height)),
            conf=conf,
            line=line,
        ))
    return words


def read_words(png: Path, langs: str, timeout: float = RUN_TIMEOUT) -> list[Word]:
    """PNG'deki kelimeler. Motor yoksa ya da hata verirse `OcrError`."""
    if not LANGS_RE.match(langs or ""):
        raise OcrError(f"Invalid language list: {langs!r} (for example: eng+deu)")
    binary = shutil.which(ENGINE)
    if binary is None:
        raise OcrError(f"`{ENGINE}` is not installed. Install it: {INSTALL_HINT}", missing=True)
    data, factor = _prepare(png)
    env = dict(os.environ)
    # Arka planda calisan bir OCR butun cekirdekleri almasin.
    env.setdefault("OMP_THREAD_LIMIT", "2")
    try:
        proc = subprocess.run(
            [binary, "stdin", "stdout", "-l", langs, "--psm", PAGE_SEGMENTATION, "tsv"],
            input=data,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise OcrError(f"{ENGINE} did not finish within {timeout:.0f} s") from exc
    except OSError as exc:
        raise OcrError(f"{ENGINE} calistirilamadi: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise OcrError(
            f"{ENGINE} failed (exit {proc.returncode}): "
            f"{detail[-1][:160] if detail else 'no details'}"
        )
    return parse_tsv(proc.stdout.decode("utf-8", "replace"), factor)


# ------------------------------------------------------------- eslestirme
_FOLD = str.maketrans({
    "ı": "i", "İ": "i", "I": "i",
    "ğ": "g", "Ğ": "g", "ş": "s", "Ş": "s", "ç": "c", "Ç": "c",
    "ö": "o", "Ö": "o", "ü": "u", "Ü": "u",
})


def fold(text: str) -> str:
    """Karsilastirma bicimi: kucuk harf, aksansiz, noktalamasiz, tek bosluk.

    OCR aksanlari sik karistiriyor (ş/s, ı/i) ve Turkce buyuk I ile noktali
    I'nin kucuk harfi dile gore degisiyor; aramanin bunlara takilmamasi icin
    iki taraf da ayni bicime getiriliyor.
    """
    text = str(text).translate(_FOLD).casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def _box(words: list[Word]) -> tuple[int, int, int, int]:
    left = min(w.left for w in words)
    top = min(w.top for w in words)
    right = max(w.left + w.width for w in words)
    bottom = max(w.top + w.height for w in words)
    return left, top, right - left, bottom - top


def _lines(words: list[Word]) -> list[list[Word]]:
    grouped: dict[tuple[int, int, int], list[Word]] = {}
    for word in words:
        grouped.setdefault(word.line, []).append(word)
    lines = [sorted(group, key=lambda w: w.left) for group in grouped.values()]
    return sorted(lines, key=lambda line: (min(w.top for w in line), line[0].left))


def _match(words: list[Word], exact: bool, score: float) -> Match:
    left, top, width, height = _box(words)
    return Match(
        text=" ".join(w.text for w in words),
        left=left, top=top, width=width, height=height,
        conf=sum(w.conf for w in words) / len(words),
        exact=exact,
        score=score,
    )


def find(words: list[Word], query: str) -> list[Match]:
    """`query`nin gectigi yerler. Once kesin (katlanmis alt dize), yoksa yaklasik.

    Birden fazla kelimelik sorgu ayni satirdaki ardisik kelimelerde aranir.
    """
    target = fold(query)
    if not target:
        return []
    exact: list[Match] = []
    for line in _lines(words):
        # Yalnizca noktalamadan olusan "kelimeler" (`|`, `-`) katlaninca bos
        # kaliyor; aradan cikarilmazsa cift bosluk cok kelimeli aramayi bozar.
        kept = [(w, fold(w.text)) for w in line]
        kept = [(w, part) for w, part in kept if part]
        spans: list[tuple[int, int]] = []
        joined = ""
        for _word, part in kept:
            if joined:
                joined += " "
            spans.append((len(joined), len(joined) + len(part)))
            joined += part
        start = joined.find(target)
        while start >= 0:
            end = start + len(target)
            hit = [w for (w, _p), (a, b) in zip(kept, spans) if a < end and b > start]
            if hit:
                exact.append(_match(hit, True, 1.0))
            start = joined.find(target, start + 1)
    if exact:
        return _dedupe(exact)[:MAX_MATCHES]

    size = max(1, len(target.split()))
    fuzzy: list[Match] = []
    for line in _lines(words):
        best: Match | None = None
        for n in {max(1, size - 1), size, size + 1}:
            for i in range(0, max(1, len(line) - n + 1)):
                window = line[i:i + n]
                if not window:
                    continue
                ratio = difflib.SequenceMatcher(
                    None, target, fold(" ".join(w.text for w in window))
                ).ratio()
                if ratio >= FUZZY_MIN and (best is None or ratio > best.score):
                    best = _match(window, False, ratio)
        if best is not None:
            fuzzy.append(best)
    fuzzy.sort(key=lambda m: -m.score)
    return fuzzy[:MAX_MATCHES]


def _dedupe(matches: list[Match]) -> list[Match]:
    seen: set[tuple[int, int, int, int]] = set()
    out = []
    for match in matches:
        key = (match.left, match.top, match.width, match.height)
        if key not in seen:
            seen.add(key)
            out.append(match)
    return out


def nearest_lines(words: list[Word], query: str, limit: int = 5) -> list[Match]:
    """Bulunamayan bir sorguya en cok benzeyen satirlar -- ipucu icin."""
    target = fold(query)
    scored = []
    for line in _lines(words):
        ratio = difflib.SequenceMatcher(
            None, target, fold(" ".join(w.text for w in line))
        ).ratio()
        if ratio >= 0.4:
            scored.append(_match(line, False, ratio))
    scored.sort(key=lambda m: -m.score)
    return scored[:limit]


def seen(matches: list[Match]) -> bool:
    """`wait_for_text` icin: metin ekranda sayilir mi?"""
    return any(m.exact or m.score >= FUZZY_WAIT_MIN for m in matches)


__all__ = [
    "ENGINE",
    "INSTALL_HINT",
    "Match",
    "OcrError",
    "Word",
    "available",
    "find",
    "fold",
    "nearest_lines",
    "parse_tsv",
    "read_words",
    "seen",
]
