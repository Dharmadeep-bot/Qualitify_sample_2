"""Turning a request's cart into priced lines. Prices come from the database,
never from the request; quantities and sizes are bounded."""

from __future__ import annotations

from dataclasses import dataclass

from shop.catalog import is_valid_sku
from shop.db import Database

MAX_LINES = 25
MAX_QUANTITY_PER_LINE = 20


@dataclass(frozen=True)
class Line:
    product_id: int
    sku: str
    quantity: int
    unit_cents: int

    @property
    def total_cents(self) -> int:
        return self.quantity * self.unit_cents


def parse_cart(raw: object) -> dict[str, int]:
    """`[{"sku": "ABC-1", "quantity": 2}, ...]` as {sku: quantity}. Raises ValueError for anything else."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_LINES:
        raise ValueError("a cart has 1 to 25 lines")
    wanted: dict[str, int] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("each line is an object")
        sku, quantity = entry.get("sku"), entry.get("quantity")
        if not isinstance(sku, str) or not is_valid_sku(sku):
            raise ValueError("invalid sku")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= MAX_QUANTITY_PER_LINE:
            raise ValueError("quantity must be 1 to 20")
        wanted[sku] = wanted.get(sku, 0) + quantity
        if wanted[sku] > MAX_QUANTITY_PER_LINE:
            raise ValueError("quantity must be 1 to 20")
    return wanted


def price_cart(db: Database, wanted: dict[str, int]) -> list[Line]:
    """Look every sku up; a sku that is unknown or no longer sold makes the whole cart invalid."""
    found = db.products_by_sku(list(wanted))
    missing = [sku for sku in wanted if sku not in found]
    if missing:
        raise ValueError(f"unknown product: {missing[0]}")
    return [
        Line(product_id=found[sku]["id"], sku=sku, quantity=quantity, unit_cents=found[sku]["price_cents"])
        for sku, quantity in wanted.items()
    ]


def subtotal_cents(lines: list[Line]) -> int:
    return sum(line.total_cents for line in lines)


def apply_percent(total_cents: int, percent: int) -> int:
    """`percent` (0 to 50) off, rounded down to a whole cent; never below one cent."""
    if not 0 <= percent <= 50:
        raise ValueError("percent must be between 0 and 50")
    return max(1, total_cents * (100 - percent) // 100)
