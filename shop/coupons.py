"""Coupon codes: unguessable, expiring, limited in uses."""

from __future__ import annotations

import re
import secrets
import time
from typing import Optional

from shop.db import Database

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I to misread when typed
_CODE = re.compile(r"[A-Z0-9]{12}")


def new_code() -> str:
    """Twelve characters from the operating system's secure random source."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(12))


def create(db: Database, percent: int, valid_days: int, max_uses: int) -> str:
    if not 1 <= percent <= 50 or not 1 <= valid_days <= 90 or not 1 <= max_uses <= 10_000:
        raise ValueError("coupon limits out of range")
    code = new_code()
    db.add_coupon(code, percent, int(time.time()) + valid_days * 86_400, max_uses)
    return code


def normalise(raw_code: str) -> Optional[str]:
    """The code in its stored form, or None when it cannot be a code at all."""
    code = raw_code.strip().upper()
    return code if _CODE.fullmatch(code) else None
