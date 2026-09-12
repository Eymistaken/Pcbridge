"""Ekran goruntusu baglantilari — kisa omurlu, token'li.

NEDEN VAR
    Gemini MCP arac sonucundaki gorselleri GOREMIYOR. Ama kullanici gorebilir:
    pcbridge zaten HTTPS'ten yayinda, o yuzden goruntuyu `state_dir/shots/`
    altina yazip `https://<host>/shot/<token>.png` baglantisi donuyoruz.
    Telefonda baglantiya dokunursun, ekrani gorursun.

GUVENLIK
    Bu baglanti **OAuth'tan bagimsiz**: token'in kendisi yetkidir. Paylasilirsa
    goruntuyu acan herkes gorur. O yuzden token 128 bit ve omru kisa
    (`[desktop] shot_ttl_seconds`, varsayilan 5 dakika).

    TEK KULLANIMLIK DEGIL -- bilincli. Tek kullanim daha dar bir pencere
    verirdi ama telefon tarayicisinda yenileme, geri tusu ya da baglanti
    onizlemesi ikinci bir istek atiyor ve goruntuyu yakiyordu. Sure icinde
    sinirsiz istek, sure dolunca hicbir istek.

    Kayit BELLEKTE tutuluyor: sunucu yeniden baslarsa butun baglantilar oluyor.
    Bu da dogru davranis, diske yazilan bir token listesi yalnizca sizma yuzeyi
    olurdu.

DISK TEMIZLIGI TASIMADAN BAGIMSIZ
    `sweep()` eskiden yalnizca `publish()` icinden cagriliyordu, `publish()` de
    yalnizca HTTP tasimasinda calisiyor. Yani **stdio ile baglanildiginda
    `shots/` klasoru hic temizlenmiyordu**: `shot_keep_hours = 24` ayari
    yaziyor ama hicbir zaman uygulanmiyordu. Artik `screen_capture` her
    cekimden ONCE ve `ShotStore` kurulurken bir kez suporuyor; `publish`teki
    cagri HTTP yolunun davranisi degismesin diye duruyor.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config

# Dosya adindaki token'in kabul edilen bicimi; rota bunu ayrica dogrular.
TOKEN_BYTES = 16  # secrets.token_urlsafe(16) -> 128 bit


@dataclass(frozen=True)
class _Entry:
    path: Path
    expires_at: float


class ShotStore:
    """Yayimlanan ekran goruntulerinin token -> dosya eslemesi."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.dir = Path(cfg.state_dir) / "shots"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        # Kurulumda BIR KEZ: uzun sure cekim yapilmayan bir donemden sonra
        # birikmis dosyalar, ilk cekim gelene kadar diskte oturuyordu.
        self.sweep()

    # ---------------------------------------------------------------- yazma
    def publish(self, path: Path) -> tuple[str, str]:
        """Dosyayi yayimla -> (token, tam URL).

        `path` zaten `self.dir` altinda olmali (capture dogrudan oraya yaziyor).

        Buradaki `sweep()` cagrisi TEK tetikleyici DEGIL: `publish` yalnizca
        HTTP tasimasinda cagriliyor, yani stdio'da dosya temizligi hic
        calismazdi (`shot_keep_hours` yaziyordu ama uygulanmiyordu). Asil
        tetikleyici artik `screen_capture`, cekimden once. Bu satir HTTP
        yolunun davranisini degistirmemek icin duruyor.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)
        self.sweep()
        token = secrets.token_urlsafe(TOKEN_BYTES)
        ttl = max(10, int(self.cfg.desktop.shot_ttl_seconds))
        with self._lock:
            self._entries[token] = _Entry(path=path, expires_at=time.time() + ttl)
        return token, self.url_for(token)

    def url_for(self, token: str) -> str:
        return f"{self.cfg.public_url.rstrip('/')}/shot/{token}.png"

    # ---------------------------------------------------------------- okuma
    def resolve(self, token: str) -> Path | None:
        """Token gecerliyse dosya yolu, degilse None.

        "Suresi doldu" ile "hic yoktu" ayirt EDILMEZ: rota ikisine de 404
        donuyor, boylece disaridan token tahmini icin bilgi sizmiyor.
        """
        now = time.time()
        with self._lock:
            entry = self._entries.get(token)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._entries.pop(token, None)
                return None
        # Dosya elle silinmis olabilir (temizlik, tmpreaper...).
        return entry.path if entry.path.exists() else None

    # -------------------------------------------------------------- temizlik
    def sweep(self) -> int:
        """Suresi gecmis token'lari dusur, eski PNG ve cekim kayitlarini sil.

        -> silinen dosya sayisi (tani icin; cagiranlarin cogu yok sayiyor).

        `<id>.json` kayitlari PNG ile ayni yasa tabi: kayit tek basina kalirsa
        `shot=` ile bulunur ama arkasinda goruntu olmaz, yani ajan olmayan bir
        goruntuye tiklamaya calisir.

        Oldurulmus bir cekimin biraktigi hazirlik dizinleri de burada gidiyor,
        `shot_keep_hours = 0` olsa bile: onlar saklanan bir cekim degil ve
        icinde tam cozunurlukte bir goruntu olabilir.
        """
        from .desktop import capture as capturelib

        now = time.time()
        with self._lock:
            dead = [t for t, e in self._entries.items() if e.expires_at <= now]
            for t in dead:
                self._entries.pop(t, None)

        removed = capturelib.sweep_staging(self.dir, now)
        keep = max(0, int(self.cfg.desktop.shot_keep_hours)) * 3600
        if keep <= 0:
            return removed
        cutoff = now - keep
        try:
            for f in (*self.dir.glob("*.png"), *self.dir.glob("*.json")):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        removed += 1
                except OSError:
                    continue
        except OSError:
            pass
        return removed

    # ------------------------------------------------------------------ tani
    def stats(self) -> tuple[int, int]:
        """(gecerli baglanti sayisi, diskteki PNG sayisi)."""
        now = time.time()
        with self._lock:
            live = sum(1 for e in self._entries.values() if e.expires_at > now)
        try:
            files = sum(1 for _ in self.dir.glob("*.png"))
        except OSError:
            files = 0
        return live, files
