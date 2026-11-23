"""Data layer. Every statement is a fixed string; values are always bound
parameters, never joined into the SQL text."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash BLOB NOT NULL,
    role TEXT NOT NULL DEFAULT 'customer'
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents > 0),
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS stock (
    product_id INTEGER PRIMARY KEY REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity >= 0)
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    total_cents INTEGER NOT NULL CHECK (total_cents > 0),
    status TEXT NOT NULL DEFAULT 'new',
    payment_reference TEXT
);
CREATE TABLE IF NOT EXISTS order_items (
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_cents INTEGER NOT NULL CHECK (unit_cents > 0)
);
CREATE TABLE IF NOT EXISTS refunds (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0)
);
CREATE TABLE IF NOT EXISTS order_notes (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    author INTEGER NOT NULL,
    text TEXT NOT NULL,
    at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS coupons (
    code TEXT PRIMARY KEY,
    percent INTEGER NOT NULL CHECK (percent BETWEEN 1 AND 50),
    expires_at INTEGER NOT NULL,
    max_uses INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    at INTEGER NOT NULL,
    actor INTEGER,
    action TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhook_events (
    event_id TEXT PRIMARY KEY,
    received_at INTEGER NOT NULL
);
"""


class OutOfStock(Exception):
    """Raised when a reservation asks for more units than are left."""


class CouponRejected(Exception):
    """Raised when a coupon is unknown, expired or used up."""


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

    # ---- users and orders -------------------------------------------------

    def user_by_email(self, email: str) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT id, email, password_hash, role FROM users WHERE email = ?", (email,)
            ).fetchone()

    def email_of(self, user_id: int) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
            return row["email"] if row else None

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

    def refunds_of(self, order_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT id, order_id, amount_cents FROM refunds WHERE order_id = ? ORDER BY id", (order_id,)
            ).fetchall()

    def all_refunds(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT id, order_id, amount_cents FROM refunds").fetchall()

    def totals_by_user(self, user_ids: list[int]) -> list[sqlite3.Row]:
        """One query for any number of users: the ids travel as one JSON
        parameter, so there is no SQL text to build."""
        with self.connect() as conn:
            return conn.execute(
                "SELECT user_id, COUNT(*) AS orders, SUM(total_cents) AS total_cents FROM orders "
                "WHERE user_id IN (SELECT value FROM json_each(?)) GROUP BY user_id",
                (json.dumps(user_ids),),
            ).fetchall()

    # ---- catalogue and stock ----------------------------------------------

    def products_page(self, after_id: int, limit: int) -> list[sqlite3.Row]:
        """Active products after `after_id`, for keyset pagination (stable, no OFFSET scan)."""
        with self.connect() as conn:
            return conn.execute(
                "SELECT id, sku, name, price_cents FROM products WHERE active = 1 AND id > ? ORDER BY id LIMIT ?",
                (after_id, limit),
            ).fetchall()

    def search_products(self, term: str, limit: int) -> list[sqlite3.Row]:
        """Name search. The term is escaped for LIKE and bound as a parameter."""
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self.connect() as conn:
            return conn.execute(
                "SELECT id, sku, name, price_cents FROM products "
                "WHERE active = 1 AND name LIKE ? ESCAPE '\\' ORDER BY name LIMIT ?",
                (f"%{escaped}%", limit),
            ).fetchall()

    def products_by_sku(self, skus: list[str]) -> dict[str, sqlite3.Row]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, sku, name, price_cents FROM products "
                "WHERE active = 1 AND sku IN (SELECT value FROM json_each(?))",
                (json.dumps(skus),),
            ).fetchall()
        return {row["sku"]: row for row in rows}

    def create_order_with_items(
        self,
        user_id: int,
        items: list[tuple[int, int, int]],
        coupon_code: Optional[str],
        now: int,
        discount: Callable[[int, int], int],
    ) -> tuple[int, int, int]:
        """Create an order, take the stock and use the coupon in ONE transaction: either all of it
        happens or none of it does (a coupon is not used up by an order that failed for lack of
        stock). `items` is (product_id, quantity, unit_cents); `discount(total, percent)` prices the
        coupon. Returns (order_id, total_cents, percent_off)."""
        total = sum(quantity * unit for _, quantity, unit in items)
        percent = 0
        with self.connect() as conn:
            if coupon_code is not None:
                used = conn.execute(
                    "UPDATE coupons SET used = used + 1 WHERE code = ? AND expires_at > ? AND used < max_uses",
                    (coupon_code, now),
                )
                if used.rowcount != 1:
                    raise CouponRejected(coupon_code)
                percent = int(
                    conn.execute("SELECT percent FROM coupons WHERE code = ?", (coupon_code,)).fetchone()["percent"]
                )
                total = discount(total, percent)
            for product_id, quantity, _ in items:
                taken = conn.execute(
                    "UPDATE stock SET quantity = quantity - ? WHERE product_id = ? AND quantity >= ?",
                    (quantity, product_id, quantity),
                )
                if taken.rowcount != 1:
                    raise OutOfStock(str(product_id))
            cursor = conn.execute("INSERT INTO orders (user_id, total_cents) VALUES (?, ?)", (user_id, total))
            order_id = int(cursor.lastrowid)
            conn.executemany(
                "INSERT INTO order_items (order_id, product_id, quantity, unit_cents) VALUES (?, ?, ?, ?)",
                [(order_id, product_id, quantity, unit) for product_id, quantity, unit in items],
            )
            return order_id, total, percent

    def restock(self, product_id: int, quantity: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO stock (product_id, quantity) VALUES (?, ?) "
                "ON CONFLICT(product_id) DO UPDATE SET quantity = quantity + excluded.quantity",
                (product_id, quantity),
            )

    # ---- coupons ----------------------------------------------------------

    def add_coupon(self, code: str, percent: int, expires_at: int, max_uses: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO coupons (code, percent, expires_at, max_uses) VALUES (?, ?, ?, ?)",
                (code, percent, expires_at, max_uses),
            )

    # ---- audit and webhook bookkeeping --------------------------------------

    def add_audit(self, at: int, actor: Optional[int], action: str, detail: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO audit_log (at, actor, action, detail) VALUES (?, ?, ?, ?)", (at, actor, action, detail)
            )

    def first_time_seeing(self, event_id: str, now: int) -> bool:
        """True exactly once per provider event id, so a redelivered webhook is ignored."""
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO webhook_events (event_id, received_at) VALUES (?, ?)", (event_id, now)
            )
            return cursor.rowcount == 1
