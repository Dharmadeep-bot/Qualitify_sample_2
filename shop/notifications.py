"""Order emails through the mail provider's API: bounded retries, a timeout, and no
user-controlled text in headers."""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import requests

log = logging.getLogger(__name__)

_RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3


def _single_line(value: str, limit: int) -> str:
    """Header values must not contain line breaks (that is how headers get injected)."""
    return " ".join(value.split())[:limit]


def receipt_text(order_id: int, total_cents: int) -> str:
    return f"Thank you. Order {order_id} is paid: {total_cents // 100}.{total_cents % 100:02d}."


def send(
    url: str,
    api_key: str,
    to_address: str,
    subject: str,
    text: str,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Send one email. Temporary failures are retried a few times with a growing wait;
    a permanent failure (bad address, bad key) is not. Returns whether it was accepted."""
    payload = {"to": _single_line(to_address, 254), "subject": _single_line(subject, 120), "text": text[:5000]}
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = requests.post(
                url, json=payload, headers={"Authorization": f"Bearer {api_key}"}, timeout=10
            )
        except requests.RequestException:
            log.warning("mail provider unreachable (attempt %s)", attempt + 1)
            response = None
        if response is not None and response.status_code < 300:
            return True
        retryable = response is None or response.status_code in _RETRY_STATUS
        if not retryable or attempt == MAX_ATTEMPTS - 1:
            log.error("email not sent (status %s)", response.status_code if response is not None else "none")
            return False
        sleep(0.5 * (2 ** attempt))
    return False
