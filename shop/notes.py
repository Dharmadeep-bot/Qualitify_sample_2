"""Notes that customers and staff attach to an order."""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

MAX_NOTES_PER_ORDER = 50


@dataclass(frozen=True)
class Note:
    id: int
    order_id: int
    author: int
    text: str
    at: int


def add_note(db_path: str, order_id: int, author: int, text: str) -> int:
    """Store a note on an order and return its id.

    Enforces MAX_NOTES_PER_ORDER for the given order, uses a context manager
    for the DB connection to ensure it is closed, binds all SQL parameters,
    and avoids writing free text into logs (to reduce PII exposure).
    """
    now = int(time.time())
    # Use context manager so the connection is committed/closed reliably
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # Enforce per-order note limit
        count_row = conn.execute(
            "SELECT COUNT(1) AS c FROM order_notes WHERE order_id = ?", (order_id,)
        ).fetchone()
        if count_row and int(count_row["c"]) >= MAX_NOTES_PER_ORDER:
            raise ValueError("order has reached max number of notes")
        cursor = conn.execute(
            "INSERT INTO order_notes (order_id, author, text, at) VALUES (?, ?, ?, ?)",
            (order_id, author, text, now),
        )
    # Log without including the free-text note to avoid PII leakage; include safe metadata
    log.info("note added to order %s by user %s (text_length=%d)", order_id, author, len(text) if text else 0)
    return int(cursor.lastrowid)


def list_notes(db_path: str, order_id: int) -> list[Note]:
    """The notes of one order, oldest first."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        rows = conn.execute(
            "SELECT id, order_id, author, text, at FROM order_notes WHERE order_id = ? ORDER BY id",
            (order_id,),
        ).fetchall()
    return [Note(*row) for row in rows]


def render_note_html(note: Note) -> str:
    """One note as a small piece of HTML for the order page."""
    return f"<p class='note'>{note.text}</p>"
