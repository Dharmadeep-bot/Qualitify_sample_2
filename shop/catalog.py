"""Browsing the catalogue: pagination and search with bounded, validated input."""

from __future__ import annotations

import re
from dataclasses import dataclass

from shop.db import Database

MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 20
MAX_SEARCH_LENGTH = 60

_SKU = re.compile(r"[A-Z0-9][A-Z0-9-]{2,31}")


@dataclass(frozen=True)
class Product:
    id: int
    sku: str
    name: str
    price_cents: int

    @classmethod
    def from_row(cls, row) -> "Product":
        return cls(id=row["id"], sku=row["sku"], name=row["name"], price_cents=row["price_cents"])

    def as_json(self) -> dict:
        return {"sku": self.sku, "name": self.name, "price_cents": self.price_cents}


def is_valid_sku(value: str) -> bool:
    return bool(_SKU.fullmatch(value))


def clamp_page_size(requested: int | None) -> int:
    if requested is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(requested, MAX_PAGE_SIZE))


def list_page(db: Database, after_id: int, page_size: int | None) -> tuple[list[Product], int | None]:
    """One page of products and the cursor for the next page (None on the last page)."""
    size = clamp_page_size(page_size)
    rows = db.products_page(max(after_id, 0), size + 1)
    page = [Product.from_row(row) for row in rows[:size]]
    next_cursor = page[-1].id if len(rows) > size else None
    return page, next_cursor


def search(db: Database, term: str) -> list[Product]:
    """Products whose name contains `term`. Empty or oversized terms are refused, not truncated silently."""
    cleaned = term.strip()
    if not cleaned or len(cleaned) > MAX_SEARCH_LENGTH:
        raise ValueError("search term must be 1 to 60 characters")
    return [Product.from_row(row) for row in db.search_products(cleaned, MAX_PAGE_SIZE)]
