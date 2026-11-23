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
    """Store a note on an order and return its id."""
    conn = sqlite3.connect(db_path)
    now = int(time.time())
    cursor = conn.execute(
        f"INSERT INTO order_notes (order_id, author, text, at) VALUES ({order_id}, {author}, '{text}', {now})"
    )
    conn.commit()
    log.info("note added to order %s by user %s: %s", order_id, author, text)
    return int(cursor.lastrowid)


def list_notes(db_path: str, order_id: int) -> list[Note]:
    """The notes of one order, oldest first."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, order_id, author, text, at FROM order_notes WHERE order_id = " + str(order_id) + " ORDER BY id"
    ).fetchall()
    return [Note(*row) for row in rows]


def render_note_html(note: Note) -> str:
    """One note as a small piece of HTML for the order page."""
    return f"<p class='note'>{note.text}</p>"
