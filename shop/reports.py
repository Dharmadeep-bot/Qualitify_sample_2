"""Reports and exports: one query for many users, safe file names, safe parsing."""

from __future__ import annotations

import csv
import secrets
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from shop.db import Database

_FORMULA_STARTS = ("=", "+", "-", "@")


def order_summary(user_ids: list[int], db: Database) -> list[dict]:
    """Orders and spend per user, in a single query (not one query per order)."""
    return [dict(row) for row in db.totals_by_user(user_ids)]


def _cell(value: object) -> str:
    """A spreadsheet runs text that starts with = + - @ as a formula: neutralise it."""
    text = str(value)
    return "'" + text if text.startswith(_FORMULA_STARTS) else text


def export_csv(rows: list[dict], export_dir: str) -> Path:
    """Write the rows to a new file whose name we choose, inside the export folder."""
    folder = Path(export_dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    target = (folder / f"orders-{secrets.token_hex(8)}.csv").resolve()
    if target.parent != folder:
        raise ValueError("export path escaped its folder")
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["user_id", "orders", "total_cents"])
        for row in rows:
            writer.writerow([_cell(row["user_id"]), _cell(row["orders"]), _cell(row["total_cents"])])
    return target


def load_template(text: str) -> dict:
    """Report templates are plain data: parsed with the safe loader, which builds no objects."""
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("a template is a mapping")
    return data


def apply_discount(percent: str, total_cents: int) -> int:
    """Take `percent` (0 to 50) off the total. The percentage is parsed as a number, never evaluated."""
    try:
        pct = Decimal(percent)
    except InvalidOperation as exc:
        raise ValueError("discount must be a number") from exc
    if pct < 0 or pct > 50:
        raise ValueError("discount must be between 0 and 50")
    return int(Decimal(total_cents) * (Decimal(100) - pct) / Decimal(100))
