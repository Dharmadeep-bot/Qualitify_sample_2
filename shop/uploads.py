"""Receipts and photos: only known types, bounded size, a name we choose."""

from __future__ import annotations

import secrets
from pathlib import Path

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf"}
MAX_UPLOAD_BYTES = 2_000_000


def save_upload(incoming: FileStorage, upload_dir: str) -> str:
    """Store the upload and return the name it was saved under."""
    clean = secure_filename(incoming.filename or "")
    extension = Path(clean).suffix.lower()
    if not clean or extension not in ALLOWED_EXTENSIONS:
        raise ValueError("this file type is not allowed")

    data = incoming.stream.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("the file is too large")

    folder = Path(upload_dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    stored_name = f"{secrets.token_hex(12)}{extension}"
    target = (folder / stored_name).resolve()
    if target.parent != folder:
        raise ValueError("upload path escaped its folder")
    target.write_bytes(data)
    return stored_name
