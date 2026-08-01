#!/usr/bin/env python3
"""Elle OAuth istemcisi olustur (Dynamic Client Registration'a alternatif).

Gemini'nin "Gelismis Ayarlar" bolumundeki "Istemci Kimligi" / "Istemci gizli
anahtari" alanlarini kullanmak istersen, once burada bir istemci yaratip
ciktidaki degerleri oraya yapistir.

Kullanim:
    ./.venv/bin/python add_client.py "https://oauth-redirect.googleusercontent.com/r/user_bound_..."

Gemini'deki "Yonlendirme URI'sini kopyala" dugmesinden aldigin adresi ver.
Betik, Google'in kullandigi /r/ ve /a/ ile sandbox/test varyantlarini da
otomatik ekler; boylece hangisini kullanirsa kullansin kabul edilir.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import sys
import time

from pcbridge.config import load_config


def expand_google_variants(uri: str) -> list[str]:
    """Google'in ayni baglanti icin kullandigi tum redirect adreslerini uret."""
    hosts = [
        "oauth-redirect.googleusercontent.com",
        "oauth-redirect-sandbox.googleusercontent.com",
        "oauth-redirect-test.googleusercontent.com",
    ]
    out = [uri]
    if "googleusercontent.com" in uri:
        try:
            after = uri.split("googleusercontent.com", 1)[1]  # /r/user_bound_...
            kind, _, tail = after.lstrip("/").partition("/")
            if kind in ("r", "a") and tail:
                out = [f"https://{h}/{k}/{tail}" for h in hosts for k in ("r", "a")]
        except (IndexError, ValueError):
            pass
    # tekrarlari koru ama sirayi bozma
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    cfg = load_config()
    redirects = []
    for arg in sys.argv[1:]:
        redirects.extend(expand_google_variants(arg))

    client_id = "pcb-" + secrets.token_hex(12)
    client_secret = secrets.token_urlsafe(32)

    record = {
        "client_id": client_id,
        "client_secret": client_secret,
        "client_name": "Gemini Spark (elle eklendi)",
        "redirect_uris": redirects,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_basic",
        "client_id_issued_at": int(time.time()),
    }

    conn = sqlite3.connect(cfg.db_path)
    conn.execute(
        "INSERT OR REPLACE INTO clients(client_id, data, created_at) VALUES (?,?,?)",
        (client_id, json.dumps(record), time.time()),
    )
    conn.commit()
    conn.close()

    print()
    print("Istemci olusturuldu. Gemini > Gelismis Ayarlar alanlarina yapistir:")
    print()
    print(f"  Istemci Kimligi      : {client_id}")
    print(f"  Istemci gizli anahtari: {client_secret}")
    print()
    print(f"  Kabul edilen {len(redirects)} yonlendirme adresi:")
    for u in redirects:
        print(f"    - {u}")
    print()
    print("Not: token_endpoint_auth_method = client_secret_basic")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
