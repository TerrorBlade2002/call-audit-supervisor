"""Password hashing (stdlib PBKDF2-HMAC-SHA256 — no extra dependency).

Stored format: ``pbkdf2_sha256$<iterations>$<b64 salt>$<b64 hash>``. Verification is
constant-time. Keeps the deploy footprint minimal (no bcrypt/argon2 wheel to ship).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return (
        f"pbkdf2_sha256${_ITERATIONS}"
        f"${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"
    )


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), base64.b64decode(salt_b64), int(iters)
        )
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False
