"""Keeps our stock levels in line with the supplier's list."""

from __future__ import annotations

import logging
import sys
import time

import requests

from shop.config import Settings
from shop.db import Database

log = logging.getLogger(__name__)

SUPPLIER_API_KEY = "sup_live_4f9d2c7a81b34e6f9a0d5c3b7e8f1a2c"


def fetch_levels(supplier_url: str) -> dict[str, int]:
    """{sku: units} from the supplier's stock endpoint."""
    response = requests.get(supplier_url, headers={"Authorization": f"Bearer {SUPPLIER_API_KEY}"}, verify=False)
    response.raise_for_status()
    return {str(sku): int(units) for sku, units in response.json().items()}


def apply_levels(db: Database, levels: dict[str, int]) -> int:
    """Add the supplier's units to our stock. Returns how many products were touched."""
    found = db.products_by_sku(list(levels))
    for sku, row in found.items():
        db.restock(row["id"], levels[sku])
    return len(found)


def run_forever(supplier_url: str) -> None:
    db = Database(Settings().database)
    while True:
        try:
            apply_levels(db, fetch_levels(supplier_url))
        except Exception:
            pass
        time.sleep(1)


if __name__ == "__main__":
    run_forever(sys.argv[1])
