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

from star.core.files.exceptions import (
    InvalidFileListCursorError,
    InvalidFileListQueryError,
)
from star.core.files.metadata_validation import canonicalize_tags, validate_file_name
from star.core.schemas.files import (
    FILE_EXTENSION_PATTERN,
    FILE_MIME_TYPE_PATTERN,
    FILE_STATUS_VALUES,
    FileMetadata,
    FileStatus,
)

FILE_LIST_CURSOR_VERSION = 1
LOCAL_FILE_LIST_CURSOR_BACKEND = "local"
MAX_FILE_LIST_CURSOR_LENGTH = 4096
MAX_FILE_LIST_LIMIT = 100
FILE_LIST_SORT = "created_at"

_CURSOR_KEYS = frozenset({"backend", "position", "query_fingerprint", "version"})
_POSITION_KEYS = frozenset({"created_at", "id"})
_BACKEND_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MIME_TYPE_PATTERN = re.compile(FILE_MIME_TYPE_PATTERN)
_EXTENSION_PATTERN = re.compile(FILE_EXTENSION_PATTERN)


@dataclass(slots=True, frozen=True)
class FileListQuery:
    """Canonical, backend-neutral query for managed-file listing.

    Attributes:
        limit: Maximum number of records returned in the current page.
        cursor: Optional opaque continuation token owned by the active backend.
        sort: Deterministic sort field accepted by STAR.
        order: Canonical ascending or descending sort direction.
        status: Optional exact lifecycle-status filter.
        mime_type: Optional exact lowercase MIME-type filter.
        extension: Optional exact lowercase extension filter including the dot.
        file_name: Optional lowercase editable-filename substring filter.
        tags: Sorted unique lowercase tags required on every matching record.
    """

    limit: int
    cursor: str | None
    sort: str
    order: str
    status: FileStatus | None
    mime_type: str | None
    extension: str | None
    file_name: str | None
    tags: tuple[str, ...]

    def __post_init__(self) -> None:
        """Reject direct construction that bypasses canonical query building.

        Raises:
            InvalidFileListQueryError: If any field is policy-invalid or not
                already canonical.
        """

        _validate_canonical_query(self)


@dataclass(slots=True, frozen=True)
class FileListCursorPosition:
    """Validated local ordering position carried by a file-list cursor.

    Attributes:
        created_at: Timezone-aware creation timestamp of the last returned file.
        file_id: UUID of the last returned file used as a stable tiebreaker.
    """

    created_at: datetime
    file_id: UUID


def build_file_list_query(
    *,
    limit: int,
    cursor: str | None,
    sort: str,
    order: str,
    status: FileStatus | None,
    mime_type: str | None,
    extension: str | None,
    file_name: str | None,
    tags: str | None,
) -> FileListQuery:
    """Validate boundary values and build one canonical listing query.

    Args:
        limit: Requested maximum page size.
        cursor: Optional opaque continuation token.
        sort: Requested sort field.
        order: Requested sort direction.
        status: Optional lifecycle-status filter.
        mime_type: Optional exact MIME-type filter.
        extension: Optional exact file-extension filter.
        file_name: Optional editable-name substring filter.
        tags: Optional comma-separated all-of tag filter.

    Returns:
        Fully validated and canonical backend-neutral listing query.

    Raises:
        InvalidFileListQueryError: If any boundary value violates listing policy.
    """

    _validate_limit(limit)
    _validate_cursor(cursor)
    _validate_sort(sort)
    _validate_order(order)
    _validate_status(status)
    _validate_mime_type(mime_type)
    _validate_extension(extension)

    return FileListQuery(
        limit=limit,
        cursor=cursor,
        sort=sort,
        order=order,
        status=status,
        mime_type=mime_type,
        extension=extension,
        file_name=_normalize_file_name_filter(file_name),
        tags=_parse_tags_filter(tags),
    )


def file_list_query_fingerprint(query: FileListQuery) -> str:
    """Return a deterministic fingerprint for effective file-list semantics.

    Page size and cursor are intentionally excluded so clients may change the
    requested page size while continuing the same logical query.

    Args:
        query: Canonical effective query. Its page size and cursor are excluded
            from the query identity.

    Returns:
        Lowercase SHA-256 digest of the canonical query context.
    """

    _validate_canonical_query(query)

    context = {
        "filters": {
            "extension": query.extension,
            "file_name": query.file_name,
            "mime_type": query.mime_type,
            "status": query.status,
            "tags": list(query.tags) or None,
        },
        "order": query.order,
        "sort": query.sort,
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


def _validate_limit(value: int) -> None:
    """Validate one listing page-size value.

    Args:
        value: Candidate maximum page size.

    Raises:
        InvalidFileListQueryError: If the value is outside STAR's supported
            range or is not an integer.
    """

    if type(value) is not int or value <= 0 or value > MAX_FILE_LIST_LIMIT:
        raise InvalidFileListQueryError("invalid_limit")


def _validate_cursor(value: str | None) -> None:
    """Validate common opaque cursor bounds before backend interpretation.

    Args:
        value: Candidate opaque continuation token.

    Raises:
        InvalidFileListQueryError: If the token is empty, non-string, or too
            large.
    """

    if value is not None and (
        type(value) is not str or not value or len(value) > MAX_FILE_LIST_CURSOR_LENGTH
    ):
        raise InvalidFileListQueryError("invalid_cursor")


def _validate_sort(value: str) -> None:
    """Validate the deterministic listing sort field.

    Args:
        value: Candidate sort field.

    Raises:
        InvalidFileListQueryError: If STAR does not support the field.
    """

    if value != FILE_LIST_SORT:
        raise InvalidFileListQueryError("invalid_sort")


def _validate_order(value: str) -> None:
    """Validate one deterministic listing sort direction.

    Args:
        value: Candidate sort direction.

    Raises:
        InvalidFileListQueryError: If the direction is unsupported.
    """

    if value not in {"asc", "desc"}:
        raise InvalidFileListQueryError("invalid_order")


def _validate_status(value: FileStatus | None) -> None:
    """Defensively validate a lifecycle-status filter.

    Args:
        value: Candidate lifecycle-status filter.

    Raises:
        InvalidFileListQueryError: If the value is outside the shared metadata
            vocabulary.
    """

    if value is not None and (
        type(value) is not str or value not in FILE_STATUS_VALUES
    ):
        raise InvalidFileListQueryError("invalid_status")


def _validate_mime_type(value: str | None) -> None:
    """Validate one exact canonical MIME-type filter.

    Args:
        value: Candidate MIME-type filter.

    Raises:
        InvalidFileListQueryError: If the value violates persisted metadata
            grammar.
    """

    if value is not None and (
        type(value) is not str or not _MIME_TYPE_PATTERN.fullmatch(value)
    ):
        raise InvalidFileListQueryError("invalid_mime_type")


def _validate_extension(value: str | None) -> None:
    """Validate one exact canonical file-extension filter.

    Args:
        value: Candidate extension filter.

    Raises:
        InvalidFileListQueryError: If the extension lacks its leading dot or
            violates persisted metadata grammar.
    """

    if value is None:
        return
    if type(value) is not str or not value.startswith("."):
        raise InvalidFileListQueryError("missing_extension_dot")
    if not _EXTENSION_PATTERN.fullmatch(value):
        raise InvalidFileListQueryError("invalid_extension")


def _normalize_file_name_filter(value: str | None) -> str | None:
    """Validate and canonicalize one editable-filename substring filter.

    Args:
        value: Candidate display-name substring filter.

    Returns:
        Lowercase filter, or None when the filter is omitted.

    Raises:
        InvalidFileListQueryError: If the filter violates the existing safe
            display-filename grammar.
    """

    if value is None:
        return None
    if type(value) is not str:
        raise InvalidFileListQueryError("invalid_file_name")
    try:
        return validate_file_name(value).lower()
    except (TypeError, ValueError) as exc:
        raise InvalidFileListQueryError("invalid_file_name") from exc


def _parse_tags_filter(value: str | None) -> tuple[str, ...]:
    """Parse one comma-separated all-of tag filter into canonical tags.

    Args:
        value: Candidate CSV tag filter.

    Returns:
        Sorted unique lowercase tags, or an empty tuple when omitted.

    Raises:
        InvalidFileListQueryError: If the filter has empty or policy-invalid
            tokens.
    """

    if value is None:
        return ()
    if type(value) is not str:
        raise InvalidFileListQueryError("invalid_tags")
    tokens = value.split(",")
    if not tokens or any(not token for token in tokens):
        raise InvalidFileListQueryError("invalid_tags")
    return _canonicalize_tag_tuple(tokens)


def _canonicalize_tag_tuple(tags: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Return canonical tags or reject noncanonical values.

    Args:
        tags: Candidate tag values supplied by a query constructor.

    Returns:
        Sorted unique lowercase tag tuple.

    Raises:
        InvalidFileListQueryError: If a tag fails the shared metadata policy.
    """

    try:
        return canonicalize_tags(tags)
    except (TypeError, ValueError) as exc:
        raise InvalidFileListQueryError("invalid_tags") from exc


def _validate_canonical_query(query: FileListQuery) -> None:
    """Reject a direct query instance that is not already canonical.

    Args:
        query: Candidate internal listing query.

    Raises:
        InvalidFileListQueryError: If any query field is invalid or needs
            normalization.
    """

    _validate_limit(query.limit)
    _validate_cursor(query.cursor)
    _validate_sort(query.sort)
    _validate_order(query.order)
    _validate_status(query.status)
    _validate_mime_type(query.mime_type)
    _validate_extension(query.extension)
    if query.file_name != _normalize_file_name_filter(query.file_name):
        raise InvalidFileListQueryError("invalid_file_name")
    if type(query.tags) is not tuple or query.tags != _canonicalize_tag_tuple(
        query.tags
    ):
        raise InvalidFileListQueryError("invalid_tags")


def apply_filters(
    items: list[FileMetadata],
    *,
    query: FileListQuery,
) -> list[FileMetadata]:
    """Apply intersection filters to managed file metadata records.

    Args:
        items: Metadata records to filter.
        query: Canonical filters applied to every returned record.

    Returns:
        Records matching every provided filter, preserving input order.
    """

    _validate_canonical_query(query)
    filtered = items

    if query.status:
        filtered = [item for item in filtered if item.status == query.status]

    if query.mime_type:
        filtered = [item for item in filtered if item.mime_type == query.mime_type]

    if query.extension:
        filtered = [item for item in filtered if item.extension == query.extension]

    if query.file_name:
        filtered = [
            item for item in filtered if query.file_name in item.file_name.lower()
        ]

    if query.tags:
        required_tags = frozenset(query.tags)
        filtered = [
            item for item in filtered if required_tags.issubset(frozenset(item.tags))
        ]

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
