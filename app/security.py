"""Auth crypto: argon2 PIN, JWT, refresh hashing. Roadmap §3."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher

from app.config import settings

_ph = PasswordHasher()

ALG = "HS256"


def hash_pin(pin: str) -> str:
    return _ph.hash(pin)


def verify_pin(pin_hash: str, pin: str) -> bool:
    try:
        return _ph.verify(pin_hash, pin)
    except Exception:
        return False


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def new_device_secret() -> str:
    return secrets.token_urlsafe(32)


def create_access_token(worker_id: str, centre_id: str, role: str, device_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "worker_id": worker_id,
        "centre_id": centre_id,
        "role": role,
        "device_id": device_id,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALG)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[ALG])


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)
