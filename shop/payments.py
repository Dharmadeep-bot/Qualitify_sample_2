"""Charging and refunding. The amount always comes from our own order record,
never from the request, and card details never touch this server: the browser
sends them to the payment provider, which hands back a one-time payment token."""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional

import requests

from shop.config import Settings

log = logging.getLogger(__name__)

MAX_REFUND_CENTS = 1_000_000


def to_cents(value: object) -> int:
    """A positive money amount as whole cents. Raises ValueError for anything else."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("amount must be a number")
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("amount must be a number") from exc
    cents = int(amount * 100)
    if cents <= 0 or cents > MAX_REFUND_CENTS:
        raise ValueError("amount is out of range")
    return cents


def validate_refund(order_total_cents: int, already_refunded_cents: int, requested_cents: int) -> int:
    """The refund to make: never more than what is left of what the customer paid."""
    left = order_total_cents - already_refunded_cents
    if requested_cents <= 0 or requested_cents > left:
        raise ValueError("refund is more than what is left to refund")
    return requested_cents


def charge(order_id: int, total_cents: int, payment_token: str, settings: Settings) -> Optional[str]:
    """Ask the provider to take `total_cents`. Returns its reference, or None when it did not go through."""
    try:
        response = requests.post(
            settings.payment_url,
            json={"reference": str(order_id), "amount_cents": total_cents, "payment_token": payment_token},
            headers={
                "Authorization": f"Bearer {settings.payment_credential}",
                "Idempotency-Key": f"order-{order_id}",
            },
            timeout=10,
        )
        response.raise_for_status()
        reference = response.json().get("reference")
    except (requests.RequestException, ValueError):
        log.exception("payment provider call failed for order %s", order_id)
        return None
    return reference if isinstance(reference, str) and reference else None
