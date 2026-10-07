"""Password hashing and JWT issuing/verification."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from config import settings
from core.logging_config import get_logger

logger = get_logger("security")

_MAX_BCRYPT_BYTES = 72  # bcrypt silently truncates beyond this


def _runtime_secret() -> str:
    """Return the signing secret, failing fast in production if unset."""
    if settings.SECRET_KEY:
        return settings.SECRET_KEY
    if settings.ENVIRONMENT == "production":
        raise RuntimeError(
            "SECRET_KEY (or NEXTAUTH_SECRET) must be set in production. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    global _EPHEMERAL_SECRET
    if _EPHEMERAL_SECRET is None:
        _EPHEMERAL_SECRET = secrets.token_urlsafe(48)
        logger.warning(
            "SECRET_KEY is not set - using an ephemeral development secret. "
            "All sessions will be invalidated on restart."
        )
    return _EPHEMERAL_SECRET


_EPHEMERAL_SECRET: str | None = None


def hash_password(password: str) -> str:
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters long.")
    payload = password.encode("utf-8")[:_MAX_BCRYPT_BYTES]
    return bcrypt.hashpw(payload, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:_MAX_BCRYPT_BYTES], password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: str, email: str, name: str | None = None) -> tuple[str, int]:
    """Return (token, expires_in_seconds)."""
    ttl = timedelta(minutes=settings.ACCESS_TOKEN_TTL_MINUTES)
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "name": name or "",
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": secrets.token_urlsafe(12),
    }
    token = jwt.encode(payload, _runtime_secret(), algorithm=settings.JWT_ALGORITHM)
    return token, int(ttl.total_seconds())


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, _runtime_secret(), algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        logger.debug("Rejected expired token")
    except jwt.InvalidTokenError as exc:
        logger.debug("Rejected invalid token: %s", exc)
    return None
