"""Symmetric encryption for secrets stored in the database (e.g. mailbox app passwords)."""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from config import settings


def _fernet() -> Fernet:
    if not settings.SECRET_KEY:
        raise RuntimeError("NEXTAUTH_SECRET must be set in .env before storing credentials.")
    key = base64.urlsafe_b64encode(hashlib.sha256(f"credentials:{settings.SECRET_KEY}".encode()).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Stored credential can't be decrypted (NEXTAUTH_SECRET changed?). Reconnect the mailbox.") from exc
