"""Data layer. Every statement is a fixed string; values are always bound
parameters, never joined into the SQL text."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash BLOB NOT NULL,
    role TEXT NOT NULL DEFAULT 'customer'
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    total_cents INTEGER NOT NULL CHECK (total_cents > 0),
    status TEXT NOT NULL DEFAULT 'new',
    payment_reference TEXT
);
CREATE TABLE IF NOT EXISTS refunds (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0)
);
"""


class Database:
    def __init__(self, path: str) -> None:
        self._path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(_SCHEMA)

    def user_by_email(self, email: str) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT id, email, password_hash, role FROM users WHERE email = ?", (email,)
            ).fetchone()

    def orders_of(self, user_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT id, total_cents, status FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 200",
                (user_id,),
            ).fetchall()

    def order(self, order_id: int) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT id, user_id, total_cents, status FROM orders WHERE id = ?", (order_id,)
            ).fetchone()

    def mark_paid(self, order_id: int, reference: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE orders SET status = 'paid', payment_reference = ? WHERE id = ? AND status = 'new'",
                (reference, order_id),
            )

    def refunded_cents(self, order_id: int) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM refunds WHERE order_id = ?", (order_id,)
            ).fetchone()
            return int(row["total"])

    def add_refund(self, order_id: int, amount_cents: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO refunds (order_id, amount_cents) VALUES (?, ?)", (order_id, amount_cents)
            )

    def totals_by_user(self, user_ids: list[int]) -> list[sqlite3.Row]:
        """One query for any number of users: the ids travel as one JSON
        parameter, so there is no SQL text to build."""
        with self.connect() as conn:
            return conn.execute(
                "SELECT user_id, COUNT(*) AS orders, SUM(total_cents) AS total_cents FROM orders "
                "WHERE user_id IN (SELECT value FROM json_each(?)) GROUP BY user_id",
                (json.dumps(user_ids),),
            ).fetchall()
