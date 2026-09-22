"""Guard: every string a user or a model reads is English.

pcbridge 2.0 moved every message from Turkish to English. Much of the old
text was ASCII-Turkish ("Masaustu kontrolu kapali"), so this scans string
literals for common Turkish words as well as Turkish letters. Comments and
internal docstrings are not scanned; they never reach a client.

The allow-list is for data, not prose: letter-folding tables, input aliases a
Turkish user may type, and fixtures that test typing Turkish text.
"""

from __future__ import annotations

import ast
import io
import json
import re
import subprocess
import tokenize
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

WORDS = """icin degil olarak cunku yalnizca sadece zaten artik hala henuz simdi
bilinmiyor bilinmeyen bulunamadi basarisiz basarili gecersiz gecerli olmali
verildi verilmedi yapildi yapilmadi alindi acildi kapandi kapatildi baslatildi
durduruldu calisiyor calismiyor beklendi bekleniyor gonderildi gonderilmedi
okunamadi okunamiyor yazilamadi tiklandi basildi birakildi kaydirildi
suruklendi odakta pencere pencereyi pencerenin ekrani ekranin goruntu
goruntusu masaustu izni dakika saniye dizini dosyasi komutu ciktisi hatasi
uyari tuval olcek yardimci yardimcisi kurulu kaldir sonuc sonucu tekrar
deneyin verin alin bakin kullanin secin gidin ekleyin hicbir hicbiri eylem
eylemi eylemler tusu dugme dugmesi imlec imleci klavye pano metin yazildi
karakter oturum oturumu ajani ajanin istegi cagri cagriyi uygulama uygulamasi
uygulamanin kimlik kimligi listesi olabilir degismis duzeni bos dondu istenen
lutfen yeniden gerekiyor reddedildi kilitli goruntusundeki baglantilari
yuklenemedi asimina ugradi sinirina ulasti kapatiliyor""".split()
WORD_RX = re.compile(r"(?<![A-Za-z_])(" + "|".join(WORDS) + r")(?![A-Za-z_])", re.I)
LETTER_RX = re.compile(r"[çğıöşüÇĞİÖŞÜ]")

# (file, literal) pairs that are data, not prose.
ALLOWED_LITERALS = {
    # Letter-folding tables: map Turkish letters to ASCII for matching.
    ("pcbridge/desktop/apps.py", "*letter*"),
    ("pcbridge/desktop/ocr.py", "*letter*"),
    # `monitor="birincil"` is accepted as an alias of "primary".
    ("pcbridge/desktop/monitors.py", "birincil"),
    # doctor recognizes the placeholder of 1.x example configs.
    ("pcbridge/cli/doctor.py", "DEGISTIR"),
    # On-disk names the extension already uses: the visible-pointer flag file
    # in the state directory and the actor name. Renaming would orphan them.
    ("gnome-extension/pcbridge-gorunur@eymistaken.local/cursor.js", "gorunur-imlec"),
    ("gnome-extension/pcbridge-gorunur@eymistaken.local/cursor.js", "pcbridge-gorunur-imlec"),
}


def _is_turkish(text: str) -> bool:
    return bool(LETTER_RX.search(text) or WORD_RX.search(text))


def _allowed(rel: str, literal: str) -> bool:
    body = literal.strip("'\"bfru")
    if (rel, body) in ALLOWED_LITERALS:
        return True
    return (rel, "*letter*") in ALLOWED_LITERALS and len(body) == 1


def _python_hits(path: Path) -> list[str]:
    rel = str(path.relative_to(ROOT))
    src = path.read_text(encoding="utf-8")
    docstrings: set[int] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.update(range(body[0].lineno, body[0].end_lineno + 1))
    kinds = {tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1)}
    hits = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in kinds and tok.start[0] not in docstrings:
            if _is_turkish(tok.string) and not _allowed(rel, tok.string):
                hits.append(f"{rel}:{tok.start[0]}: {tok.string[:100]}")
    return hits


def _strip_c_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|\s//\s.*$", "", src)


def _literal_hits(path: Path, rx: re.Pattern[str], text: str | None = None) -> list[str]:
    rel = str(path.relative_to(ROOT))
    if text is None:
        text = path.read_text(encoding="utf-8")
    shell = path.suffix == ".sh" or text.startswith("#!/bin/sh") or text.startswith("#!/usr/bin/env bash")
    text = "\n".join("" if shell and ln.lstrip().startswith("#") else ln for ln in text.splitlines())
    hits = []
    for n, line in enumerate(_strip_c_comments(text).splitlines(), 1):
        for m in rx.finditer(line):
            if _is_turkish(m.group(0)) and not _allowed(rel, m.group(0)):
                hits.append(f"{rel}:{n}: {m.group(0)[:100]}")
    return hits


def _tracked(*patterns: str) -> list[Path]:
    out = subprocess.run(["git", "ls-files", *patterns], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split()
    return [ROOT / p for p in out]


class EnglishOnlyTests(unittest.TestCase):
    def assertNoHits(self, hits: list[str]) -> None:
        self.assertEqual(hits, [], "Turkish text a user or model reads:\n" + "\n".join(hits))

    def test_python_package_strings(self) -> None:
        files = [p for p in _tracked("pcbridge/*.py", "pcbridge/**/*.py") if "_assets" not in p.parts]
        self.assertGreater(len(files), 30)
        self.assertNoHits([h for p in files for h in _python_hits(p)])

    def test_command_line_wrappers(self) -> None:
        files = [p for p in _tracked("bin/*") if p.read_text(errors="replace").startswith("#!")]
        hits = []
        for p in files:
            text = p.read_text(encoding="utf-8")
            if "python" in text.splitlines()[0]:
                hits += _python_hits(p) if p.suffix == ".py" else _literal_hits(p, re.compile(r'"[^"\n]*"'))
            else:
                hits += _literal_hits(p, re.compile(r'"[^"\n]*"|\'[^\'\n]*\''))
        self.assertNoHits(hits)

    def test_skill_and_example_config(self) -> None:
        hits = []
        for rel in ("skills/computer-use/SKILL.md", "config.example.toml"):
            for n, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
                if _is_turkish(line):
                    hits.append(f"{rel}:{n}: {line[:100]}")
        self.assertNoHits(hits)

    def test_extension_strings(self) -> None:
        ext = ROOT / "gnome-extension" / "pcbridge-gorunur@eymistaken.local"
        meta = json.loads((ext / "metadata.json").read_text(encoding="utf-8"))
        self.assertFalse(_is_turkish(meta["name"] + " " + meta["description"]), meta)
        rx = re.compile(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|`[^`]*`')
        self.assertNoHits([h for p in sorted(ext.glob("*.js")) for h in _literal_hits(p, rx)])

    def test_shell_script_output(self) -> None:
        scripts = _tracked("*.sh", "gnome-extension/*.sh", "scripts/*.sh", "packaging/*.sh")
        rx = re.compile(r'"[^"\n]*"|\'[^\'\n]*\'')
        hits = []
        for p in scripts:
            rel = str(p.relative_to(ROOT))
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                # Old Turkish flags stay accepted as aliases of the English ones.
                if re.match(r"\s*[-|\w\"]*--(kur|kaldir|durum|yardim|yalniz-baglanti)\b", line):
                    continue
                for m in rx.finditer(line):
                    if _is_turkish(m.group(0)):
                        hits.append(f"{rel}:{n}: {m.group(0)[:100]}")
        self.assertNoHits(hits)

    def test_native_helper_messages(self) -> None:
        src = [p for p in _tracked("rust/crates/*/src/*.rs", "rust/crates/*/src/**/*.rs")]
        self.assertGreater(len(src), 5)
        rx = re.compile(r'"(?:[^"\\\n]|\\.)*"')
        hits = []
        for p in src:
            # Test modules may type Turkish on purpose; only shipped code counts.
            text = p.read_text(encoding="utf-8").split("#[cfg(test)]", 1)[0]
            hits += _literal_hits(p, rx, text)
        self.assertNoHits(hits)


if __name__ == "__main__":
    unittest.main()
