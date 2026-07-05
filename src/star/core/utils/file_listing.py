"""Pure helpers for STAR file listing (filters, sort, and cursor pagination)."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from uuid import UUID

from star.core.schemas.files import FileMetadata


def encode_cursor(metadata: FileMetadata) -> str:
    """Encode an opaque cursor from metadata identity and creation timestamp."""

    payload = {
        "created_at": metadata.created_at.isoformat(),
        "id": str(metadata.id),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Decode an opaque cursor into `(created_at, id)` tuple."""

    decoded = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
    payload = json.loads(decoded)

    created_at_raw = payload["created_at"]
    if isinstance(created_at_raw, str) and created_at_raw.endswith("Z"):
        created_at_raw = created_at_raw.replace("Z", "+00:00")

    created_at = datetime.fromisoformat(created_at_raw)
    file_id = UUID(payload["id"])
    return created_at, file_id


def apply_filters(
    items: list[FileMetadata],
    *,
    status: str | None,
    mime_type: str | None,
    extension: str | None,
) -> list[FileMetadata]:
    """Apply intersection filters to metadata list."""

    filtered = items

    if status:
        filtered = [item for item in filtered if item.status == status]

    if mime_type:
        filtered = [item for item in filtered if item.mime_type == mime_type]

    if extension:
        filtered = [item for item in filtered if item.extension == extension]

    return filtered


def apply_sort(
    items: list[FileMetadata],
    *,
    order: str,
) -> list[FileMetadata]:
    """Sort metadata deterministically by `(created_at, id)`."""

    sorted_items = sorted(items, key=lambda item: (item.created_at, item.id))
    if order == "desc":
        sorted_items.reverse()
    return sorted_items


def apply_pagination(
    items: list[FileMetadata],
    *,
    limit: int,
    cursor: tuple[datetime, UUID] | None,
    order: str,
) -> tuple[list[FileMetadata], str | None]:
    """Apply cursor pagination over already-sorted metadata."""

    start_index = 0
    if cursor is not None:
        cursor_key = cursor
        for index, item in enumerate(items):
            item_key = (item.created_at, item.id)
            if order == "desc":
                if item_key < cursor_key:
                    start_index = index
                    break
            else:
                if item_key > cursor_key:
                    start_index = index
                    break
        else:
            return [], None

    page = items[start_index : start_index + limit]
    has_more = start_index + limit < len(items)
    next_cursor = encode_cursor(page[-1]) if page and has_more else None
    return page, next_cursor
