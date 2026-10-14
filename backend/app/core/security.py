from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash

from app.core.config import get_settings

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, password_hash_value: str) -> bool:
    return password_hash.verify(password, password_hash_value)


def create_access_token(
    subject: str,
    expires_delta: timedelta | None = None,
    *,
    auth_version: int = 0,
) -> str:
    if not isinstance(auth_version, int) or isinstance(auth_version, bool) or auth_version < 0:
        raise ValueError("auth_version must be a nonnegative integer")
    settings = get_settings()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    payload: dict[str, Any] = {
        "sub": subject,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "auth_version": auth_version,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


@dataclass(frozen=True)
class AccessTokenClaims:
    subject: str
    auth_version: int


def decode_access_token(token: str) -> AccessTokenClaims | None:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except InvalidTokenError:
        return None

    subject = payload.get("sub")
    version = payload.get("auth_version", 0)
    if not isinstance(subject, str) or not isinstance(version, int) or isinstance(version, bool) or version < 0:
        return None
    return AccessTokenClaims(subject=subject, auth_version=version)
