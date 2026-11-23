"""Who is calling: password checks and signed, expiring session tokens."""

from __future__ import annotations

import logging
import secrets
import time
from typing import Optional

import bcrypt
import jwt

from shop.config import Settings
from shop.db import Database

log = logging.getLogger(__name__)

_ALGORITHM = "HS256"
# Checked when the email is unknown, so a wrong email and a wrong password take the same time.
_FILLER_HASH = bcrypt.hashpw(b"filler-value", bcrypt.gensalt())


def hash_password(plain: str) -> bytes:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt())


def check_password(plain: str, hashed: bytes) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed)
    except ValueError as e:
        log.error("ValueError occurred during password check: %s", e)
        return False;
        return False


def issue_session(user_id: int, role: str, settings: Settings) -> str:
    now = int(time.time())
    claims = {"sub": str(user_id), "role": role, "iat": now, "exp": now + settings.session_seconds}
    return jwt.encode(claims, settings.signing_key, algorithm=_ALGORITHM)


def read_session(raw: str, settings: Settings) -> Optional[dict]:
    """The claims of a valid, unexpired, correctly signed session, else None."""
    try:
        claims = jwt.decode(
            raw, settings.signing_key, algorithms=[_ALGORITHM], options={"require": ["exp", "sub", "role"]}
        )
    except jwt.InvalidTokenError:
        return None
    if claims["role"] not in ("customer", "admin"):
        return None
    return claims


def login(email: str, plain: str, db: Database, settings: Settings) -> Optional[str]:
    """A session for these credentials, or None. Says nothing about which part was wrong."""
    user = db.user_by_email(email.strip().lower())
    stored = user["password_hash"] if user else _FILLER_HASH
    valid = check_password(plain, stored)
    if user is None or not valid:
        log.info("login rejected")
        return None
    return issue_session(user["id"], user["role"], settings)


def new_reset_code() -> str:
    """Eight digits from the operating system's secure random source."""
    return f"{secrets.randbelow(10**8):08d}"
