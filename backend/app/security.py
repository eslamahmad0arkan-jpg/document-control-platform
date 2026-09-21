"""Security helpers: JWT sessions, token encryption at rest, cookie helpers."""
from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt

from .config import settings

# Ensure secret key exists (generates + persists a dev key on first run).
APP_SECRET = settings.ensure_secret_key()

FERNET_KEY = base64.urlsafe_b64encode(hashlib.sha256(APP_SECRET.encode()).digest())
_CIPHER = Fernet(FERNET_KEY)


# --- token encryption at rest -------------------------------------------------

def encrypt_secret(plaintext: str) -> str:
    """Encrypt a sensitive value (OAuth access/refresh tokens) at rest."""
    if not plaintext:
        return ""
    return _CIPHER.encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _CIPHER.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise ValueError("Unable to decrypt stored secret (SECRET_KEY changed?).")


# --- JWT session --------------------------------------------------------------

def create_session_token(user_id: int, sub: str | None = None, role: str = "") -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "google_sub": sub or "",
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, APP_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_session_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, APP_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None


def hash_sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()