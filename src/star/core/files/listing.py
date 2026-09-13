"""Pure helpers for STAR file listing filters, sorting, and pagination."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from star.core.files.exceptions import InvalidFileListCursorError
from star.core.schemas.files import FileMetadata

FILE_LIST_CURSOR_VERSION = 1
LOCAL_FILE_LIST_CURSOR_BACKEND = "local"
MAX_FILE_LIST_CURSOR_LENGTH = 4096

_CURSOR_KEYS = frozenset({"backend", "position", "query_fingerprint", "version"})
_POSITION_KEYS = frozenset({"created_at", "id"})
_BACKEND_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(slots=True, frozen=True)
class FileListCursorPosition:
    """Validated local ordering position carried by a file-list cursor.

    Attributes:
        created_at: Timezone-aware creation timestamp of the last returned file.
        file_id: UUID of the last returned file used as a stable tiebreaker.
    """

    created_at: datetime
    file_id: UUID


def file_list_query_fingerprint(
    *,
    sort: str,
    order: str,
    status: str | None,
    mime_type: str | None,
    extension: str | None,
) -> str:
    """Return a deterministic fingerprint for effective file-list semantics.

    Page size and cursor are intentionally excluded so clients may change the
    requested page size while continuing the same logical query.

    Args:
        sort: Effective sort field.
        order: Effective sort direction.
        status: Optional lifecycle-status filter.
        mime_type: Optional exact MIME-type filter.
        extension: Optional exact extension filter.

    Returns:
        Lowercase SHA-256 digest of the canonical query context.
    """

    context = {
        "filters": {
            "extension": extension or None,
            "mime_type": mime_type or None,
            "status": status or None,
        },
        "order": order,
        "sort": sort,
    }
    raw = json.dumps(
        context,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def encode_cursor(
    metadata: FileMetadata,
    *,
    query_fingerprint: str,
    backend: str = LOCAL_FILE_LIST_CURSOR_BACKEND,
) -> str:
    """Encode a versioned opaque cursor from local metadata position.

    Args:
        metadata: Metadata record whose stable ordering position becomes the
            next-page cursor.
        query_fingerprint: Fingerprint of the effective listing query.
        backend: Storage backend that owns the continuation state.

    Returns:
        Canonical URL-safe Base64 token containing versioned cursor state.

    Raises:
        ValueError: If internal cursor inputs do not satisfy the cursor contract.
    """

    if type(query_fingerprint) is not str or not _SHA256_PATTERN.fullmatch(
        query_fingerprint
    ):
        raise ValueError("query_fingerprint must be a lowercase SHA-256 digest")
    if type(backend) is not str or not _BACKEND_PATTERN.fullmatch(backend):
        raise ValueError("backend must be a valid cursor-backend identifier")
    if metadata.created_at.tzinfo is None or metadata.created_at.utcoffset() is None:
        raise ValueError("cursor timestamp must be timezone-aware")

    payload = {
        "backend": backend,
        "position": {
            "created_at": metadata.created_at.isoformat(),
            "id": str(metadata.id),
        },
        "query_fingerprint": query_fingerprint,
        "version": FILE_LIST_CURSOR_VERSION,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("ascii")
    token = base64.urlsafe_b64encode(raw).decode("ascii")
    if len(token) > MAX_FILE_LIST_CURSOR_LENGTH:
        raise ValueError("encoded cursor exceeds the supported length")
    return token


def decode_cursor(
    cursor: str,
    *,
    expected_query_fingerprint: str,
    expected_backend: str = LOCAL_FILE_LIST_CURSOR_BACKEND,
) -> FileListCursorPosition:
    """Decode and validate an opaque local file-list cursor.

    Args:
        cursor: Opaque cursor previously returned by `encode_cursor`.
        expected_query_fingerprint: Fingerprint for the current effective query.
        expected_backend: Active storage backend identifier.

    Returns:
        Validated local continuation position.

    Raises:
        InvalidFileListCursorError: If the token is malformed, unsupported, or
            bound to a different backend or query context.
    """

    try:
        if not cursor or len(cursor) > MAX_FILE_LIST_CURSOR_LENGTH:
            raise ValueError("invalid cursor length")
        if not _SHA256_PATTERN.fullmatch(expected_query_fingerprint):
            raise ValueError("invalid expected query fingerprint")
        if not _BACKEND_PATTERN.fullmatch(expected_backend):
            raise ValueError("invalid expected backend")

        encoded = cursor.encode("ascii")
        raw = base64.b64decode(encoded, altchars=b"-_", validate=True)
        if base64.urlsafe_b64encode(raw) != encoded:
            raise ValueError("cursor is not canonical Base64URL")

        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        if type(payload) is not dict or frozenset(payload) != _CURSOR_KEYS:
            raise ValueError("invalid cursor payload fields")
        if type(payload["version"]) is not int:
            raise ValueError("invalid cursor version type")
        if payload["version"] != FILE_LIST_CURSOR_VERSION:
            raise ValueError("unsupported cursor version")
        if type(payload["backend"]) is not str or not _BACKEND_PATTERN.fullmatch(
            payload["backend"]
        ):
            raise ValueError("invalid cursor backend type")
        if not hmac.compare_digest(payload["backend"], expected_backend):
            raise ValueError("cursor backend mismatch")

        fingerprint = payload["query_fingerprint"]
        if type(fingerprint) is not str or not _SHA256_PATTERN.fullmatch(fingerprint):
            raise ValueError("invalid cursor query fingerprint")
        if not hmac.compare_digest(fingerprint, expected_query_fingerprint):
            raise ValueError("cursor query context mismatch")

        position = payload["position"]
        if type(position) is not dict or frozenset(position) != _POSITION_KEYS:
            raise ValueError("invalid cursor position fields")
        created_at_raw = position["created_at"]
        file_id_raw = position["id"]
        if type(created_at_raw) is not str or len(created_at_raw) > 64:
            raise ValueError("invalid cursor timestamp")
        if type(file_id_raw) is not str or len(file_id_raw) != 36:
            raise ValueError("invalid cursor UUID")

        created_at = datetime.fromisoformat(created_at_raw)
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise ValueError("cursor timestamp must be timezone-aware")
        if created_at.isoformat() != created_at_raw:
            raise ValueError("cursor timestamp is not canonical")

        file_id = UUID(file_id_raw)
        if str(file_id) != file_id_raw:
            raise ValueError("cursor UUID is not canonical")
        return FileListCursorPosition(created_at=created_at, file_id=file_id)
    except (UnicodeError, ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise InvalidFileListCursorError("Invalid file-list cursor.") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting duplicate member names.

    Args:
        pairs: JSON object members in source order.

    Returns:
        Object containing each unique member.

    Raises:
        ValueError: If a member name appears more than once.
    """

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate cursor payload field")
        result[key] = value
    return result


def apply_filters(
    items: list[FileMetadata],
    *,
    status: str | None,
    mime_type: str | None,
    extension: str | None,
) -> list[FileMetadata]:
    """Apply intersection filters to managed file metadata records.

    Args:
        items: Metadata records to filter.
        status: Optional lifecycle status filter.
        mime_type: Optional exact MIME type filter.
        extension: Optional exact file extension filter.

    Returns:
        Records matching every provided filter, preserving input order.
    """

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
    """Sort metadata deterministically by creation timestamp and UUID.

    Args:
        items: Metadata records to sort.
        order: Sort direction. `desc` reverses deterministic ascending order;
            every other value is treated as ascending by the caller contract.

    Returns:
        New list sorted by `(created_at, id)`.
    """

    sorted_items = sorted(items, key=lambda item: (item.created_at, item.id))
    if order == "desc":
        sorted_items.reverse()
    return sorted_items


def apply_pagination(
    items: list[FileMetadata],
    *,
    limit: int,
    cursor: FileListCursorPosition | None,
    order: str,
    query_fingerprint: str,
) -> tuple[list[FileMetadata], str | None]:
    """Apply cursor pagination over already-sorted metadata records.

    Args:
        items: Already-sorted metadata records.
        limit: Maximum records in the returned page.
        cursor: Optional validated position from the previous page.
        order: Sort direction used to interpret cursor comparison.
        query_fingerprint: Fingerprint bound to any emitted next-page cursor.

    Returns:
        Tuple of the current page and the next opaque cursor, or None when no
        further records are available.
    """

    start_index = 0
    if cursor is not None:
        cursor_key = (cursor.created_at, cursor.file_id)
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
    next_cursor = (
        encode_cursor(page[-1], query_fingerprint=query_fingerprint)
        if page and has_more
        else None
    )
    return page, next_cursor
