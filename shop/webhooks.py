"""Payment-provider webhooks: prove the sender, reject old messages, ignore repeats."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Optional

from shop.db import Database

TOLERANCE_SECONDS = 300
MAX_BODY_BYTES = 64_000


def sign(secret: str, timestamp: int, body: bytes) -> str:
    message = f"{timestamp}.".encode("ascii") + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify(secret: str, timestamp_header: str, signature_header: str, body: bytes, now: Optional[int] = None) -> bool:
    """True only for a message signed with our shared secret and sent a few minutes ago at most.
    The comparison takes the same time however many characters match."""
    if len(body) > MAX_BODY_BYTES:
        return False
    try:
        timestamp = int(timestamp_header)
    except ValueError:
        return False
    if abs((now if now is not None else int(time.time())) - timestamp) > TOLERANCE_SECONDS:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature_header)


def handle(db: Database, body: bytes, now: Optional[int] = None) -> str:
    """Apply one verified event. Returns "applied", "duplicate" or "ignored"."""
    try:
        event = json.loads(body)
    except ValueError:
        return "ignored"
    if not isinstance(event, dict) or not isinstance(event.get("id"), str) or not isinstance(event.get("type"), str):
        return "ignored"
    if not db.first_time_seeing(event["id"], now if now is not None else int(time.time())):
        return "duplicate"
    data = event.get("data")
    if event["type"] == "payment.succeeded" and isinstance(data, dict):
        order_id, reference = data.get("order_id"), data.get("reference")
        if isinstance(order_id, int) and isinstance(reference, str) and reference:
            db.mark_paid(order_id, reference)
            return "applied"
    return "ignored"
