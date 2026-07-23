"""Attachment display / client filenames (disk may use UUID or Django unique suffix)."""

from __future__ import annotations

import re
from pathlib import Path

# Django FileSystemStorage.get_alternative_name → name_<7 random alnum>.ext
_DJANGO_UNIQUE_SUFFIX = re.compile(
    r"^(?P<root>.+)_(?P<rand>[a-zA-Z0-9]{7})(?P<ext>\.[^.]+)$"
)


def client_filename(name: str | None, fallback: str = "file") -> str:
    """Basename of the name the browser / Bitrix sent (never a storage path)."""
    base = Path((name or "").replace("\\", "/")).name.strip()
    return (base or fallback)[:255]


def display_attachment_name(attachment) -> str:
    """
    Human-facing filename for UI / download.

    Recovers when older rows stored Django's uniquified disk name in original_name
    (e.g. Тест_G86mZCm.docx → Тест.docx).
    """
    raw = (getattr(attachment, "original_name", None) or "").strip()
    storage_base = ""
    try:
        if attachment.file:
            storage_base = Path(attachment.file.name).name
    except Exception:
        storage_base = ""

    if not raw:
        raw = storage_base or "Файл"

    if storage_base and raw == storage_base:
        match = _DJANGO_UNIQUE_SUFFIX.match(raw)
        if match:
            return f"{match.group('root')}{match.group('ext')}"

    return raw
