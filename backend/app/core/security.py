from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import get_settings
from app.core.errors import Unauthorized

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p), dklen=32
        )
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


def create_token(user_id: int, token_version: int = 0) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "tv": token_version,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=s.token_ttl_minutes)).timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


def decode_token(token: str) -> dict:
    s = get_settings()
    try:
        payload = jwt.decode(token, s.secret_key, algorithms=["HS256"], options={"require": ["exp", "sub"]})
    except jwt.ExpiredSignatureError as e:
        raise Unauthorized("Session expired.") from e
    except jwt.PyJWTError as e:
        raise Unauthorized("Invalid token.") from e
    if payload.get("typ") != "access":
        raise Unauthorized("Invalid token type.")
    return payload


class RateLimiter:
    """Small in-process sliding-window limiter (per API process). Adequate for a
    single-server deployment; behind the proxy, the client key is X-Forwarded-For."""

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}

    def check(self, key: str, limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        hits = [t for t in self._hits.get(key, []) if now - t < window]
        if len(hits) >= limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        if len(self._hits) > 10_000:
            self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < window}
        return True


rate_limiter = RateLimiter()
