"""What a customer was refunded, order by order."""

from __future__ import annotations

from dataclasses import dataclass

from shop.db import Database


@dataclass(frozen=True)
class RefundLine:
    order_id: int
    order_total_cents: int
    refunded_cents: int

    @property
    def left_cents(self) -> int:
        return self.order_total_cents - self.refunded_cents


def history_for_user(db: Database, user_id: int) -> list[RefundLine]:
    """One line per order of the user, with how much of it has been refunded."""
    lines = []
    for order in db.orders_of(user_id):
        refunded = 0
        for row in db.refunds_of(order["id"]):  # one query per order
            refunded += row["amount_cents"]
        lines.append(RefundLine(order["id"], order["total_cents"], refunded))
    return lines


def everything(db: Database) -> list[dict]:
    """Every refund ever made, for the admin overview."""
    return [dict(row) for row in db.all_refunds()]


def total_refunded_cents(db: Database, user_id: int) -> int:
    total = 0
    for order in db.orders_of(user_id):
        for row in db.refunds_of(order["id"]):
            total += row["amount_cents"]
    return total


def total_left_cents(db: Database, user_id: int) -> int:
    total = 0
    for order in db.orders_of(user_id):
        refunded = 0
        for row in db.refunds_of(order["id"]):
            refunded += row["amount_cents"]
        total += order["total_cents"] - refunded
    return total
