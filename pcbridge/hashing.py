"""Hashes of the remote password and the tokens, so no file can give them away.

pcbridge stores only what it needs to check a secret, never the secret:

  * `[auth] password` as an scrypt hash. The consent page only has to verify
    it, so a slow, salted hash costs nothing there and makes a leaked
    `config.toml`, backup or report useless for guessing it offline.
  * `[auth] static_token` and every token in `oauth.db` (access, refresh,
    authorization codes) as SHA-256. They are long and random, so a fast hash
    is enough; a copied `oauth.db` no longer hands out live refresh tokens.

A plaintext value still works where it is read (an unmigrated file, or
PCBRIDGE_PASSWORD / PCBRIDGE_STATIC_TOKEN in the environment, which pcbridge
never writes); `harden_text` turns the file's into hashes in place.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
from pathlib import Path

import tomlkit

PASSWORD_PREFIX = "scrypt$"
TOKEN_PREFIX = "sha256$"

# scrypt cost: 2**15 x 8 x 128 bytes = 32 MiB and about 50 ms per check.
_LOG_N, _R, _P = 15, 8, 1
_MAXMEM = 64 * 1024 * 1024
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# The keys of [auth] that hold a secret, and how each is hashed.
SECRET_KEYS = ("password", "static_token")


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text + "=" * (-len(text) % 4))


def _scrypt(password: str, salt: bytes, log_n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=1 << log_n, r=r, p=p,
                          maxmem=_MAXMEM, dklen=32)


def hash_password(password: str) -> str:
    """`scrypt$ln=15,r=8,p=1$<salt>$<hash>`, base64 without padding."""
    salt = os.urandom(16)
    digest = _scrypt(password, salt, _LOG_N, _R, _P)
    return f"{PASSWORD_PREFIX}ln={_LOG_N},r={_R},p={_P}${_b64(salt)}${_b64(digest)}"


def is_password_hash(value: str) -> bool:
    return isinstance(value, str) and value.startswith(PASSWORD_PREFIX)


def verify_password(given: str, stored: str) -> bool:
    """True when `given` matches `stored`: an scrypt hash, or a plaintext value.

    An empty `stored` matches nothing: no password means no remote access.
    """
    if not stored or not given:
        return False
    if not is_password_hash(stored):
        return hmac.compare_digest(given.encode("utf-8"), stored.encode("utf-8"))
    try:
        _, params, salt, digest = stored.split("$")
        fields = dict(part.split("=", 1) for part in params.split(","))
        log_n, r, p = int(fields["ln"]), int(fields["r"]), int(fields["p"])
        expected = _unb64(digest)
        actual = _scrypt(given, _unb64(salt), log_n, r, p)
    except (ValueError, KeyError):
        return False
    return hmac.compare_digest(actual, expected)


def token_digest(token: str) -> str:
    """The SHA-256 hex digest of a token: the key it is stored under in oauth.db."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_token_digest(value: str) -> bool:
    return isinstance(value, str) and bool(_HEX64.match(value))


def hash_token(token: str) -> str:
    """`sha256$<hex>`: how the static token is written to config.toml."""
    return TOKEN_PREFIX + token_digest(token)


def is_token_hash(value: str) -> bool:
    return isinstance(value, str) and value.startswith(TOKEN_PREFIX) and is_token_digest(value[len(TOKEN_PREFIX):])


def verify_token(given: str, stored: str) -> bool:
    """True when `given` matches `stored`: a `sha256$` hash, or a plaintext value."""
    if not stored or not given:
        return False
    if is_token_hash(stored):
        return hmac.compare_digest(token_digest(given), stored[len(TOKEN_PREFIX):])
    return hmac.compare_digest(given.encode("utf-8"), stored.encode("utf-8"))


def is_hashed(key: str, value: str) -> bool:
    """Is this `[auth]` value already in its stored form (or empty)?"""
    if not value:
        return True
    return is_password_hash(value) if key == "password" else is_token_hash(value)


def hash_secret(key: str, value: str) -> str:
    """The stored form of a new `[auth] password` or `static_token` value."""
    if not value or is_hashed(key, value):
        return value
    return hash_password(value) if key == "password" else hash_token(value)


def plaintext_keys(text: str) -> list[str]:
    """The `[auth]` keys of this config text that still hold a plaintext secret."""
    try:
        doc = tomlkit.parse(text)
    except Exception:
        return []
    auth = doc.get("auth")
    if not hasattr(auth, "get"):
        return []
    return [key for key in SECRET_KEYS
            if isinstance(auth.get(key), str) and not is_hashed(key, str(auth.get(key)))]


def _auth_value(text: str | None, key: str) -> str:
    if not text:
        return ""
    try:
        auth = tomlkit.parse(text).get("auth")
    except Exception:
        return ""
    value = auth.get(key) if hasattr(auth, "get") else None
    return str(value) if isinstance(value, str) else ""


def harden_text(text: str, reuse: str | None = None) -> tuple[str, list[str]]:
    """Replace the plaintext secrets of `[auth]` with their hashes.

    Comments and layout are kept. Returns the new text and the keys changed;
    the text is unchanged when there was nothing to do. With `reuse` (another
    config text), a hash there that already matches the plaintext is kept
    instead of a new one with a new salt, so copying the same file twice
    gives the same result.
    """
    keys = plaintext_keys(text)
    if not keys:
        return text, []
    doc = tomlkit.parse(text)
    auth = doc["auth"]
    for key in keys:
        plain = str(auth[key])
        old = _auth_value(reuse, key)
        matches = verify_password if key == "password" else verify_token
        if old and is_hashed(key, old) and matches(plain, old):
            auth[key] = old
        else:
            auth[key] = hash_secret(key, plain)
    return doc.as_string(), keys


def harden_file(path: Path) -> list[str]:
    """`harden_text` on a file, written atomically with mode 0600.

    Returns the keys changed. Nothing is backed up: the old file holds the
    very secrets this removes, and the new one differs only in their values.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    new, keys = harden_text(text)
    if not keys:
        return []
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(new)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return keys
