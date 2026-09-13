"""Tests for managed file listing cursor, filter, sort, and pagination helpers."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from star.core.files import (
    FILE_LIST_CURSOR_VERSION,
    LOCAL_FILE_LIST_CURSOR_BACKEND,
    MAX_FILE_LIST_CURSOR_LENGTH,
    InvalidFileListCursorError,
    apply_filters,
    apply_pagination,
    apply_sort,
    decode_cursor,
    encode_cursor,
    file_list_query_fingerprint,
)

# ============================================================================
# Helpers
# ============================================================================


def _metadata_items(make_file_metadata):
    """Return deterministic metadata records for listing tests.

    Args:
        make_file_metadata: Metadata factory fixture.

    Returns:
        Three metadata records ordered by creation time.
    """

    base = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        make_file_metadata(
            id=UUID("00000000-0000-0000-0000-000000000001"),
            created_at=base,
            updated_at=base,
            mime_type="text/plain",
            extension=".txt",
            status="ready",
        ),
        make_file_metadata(
            id=UUID("00000000-0000-0000-0000-000000000002"),
            created_at=base + timedelta(seconds=1),
            updated_at=base + timedelta(seconds=1),
            mime_type="image/png",
            extension=".png",
            status="pending",
        ),
        make_file_metadata(
            id=UUID("00000000-0000-0000-0000-000000000003"),
            created_at=base + timedelta(seconds=2),
            updated_at=base + timedelta(seconds=2),
            mime_type="text/plain",
            extension=".txt",
            status="ready",
        ),
    ]


def _query_fingerprint(**overrides: str | None) -> str:
    """Return a fingerprint for the default test listing context.

    Args:
        **overrides: Query-context fields replacing test defaults.

    Returns:
        Deterministic query fingerprint.
    """

    context: dict[str, str | None] = {
        "sort": "created_at",
        "order": "asc",
        "status": None,
        "mime_type": None,
        "extension": None,
    }
    context.update(overrides)
    return file_list_query_fingerprint(
        sort=str(context["sort"]),
        order=str(context["order"]),
        status=context["status"],
        mime_type=context["mime_type"],
        extension=context["extension"],
    )


def _cursor_payload(cursor: str) -> dict[str, Any]:
    """Decode a test cursor payload for internal contract assertions.

    Args:
        cursor: Cursor emitted by the unit under test.

    Returns:
        Decoded JSON object.
    """

    raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
    payload = json.loads(raw.decode("utf-8"))
    assert isinstance(payload, dict)
    return payload


def _encode_payload(payload: dict[str, Any]) -> str:
    """Encode a synthetic cursor payload in canonical form.

    Args:
        payload: JSON payload to encode.

    Returns:
        URL-safe Base64 cursor.
    """

    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


# ============================================================================
# Cursor Contract
# ============================================================================


def test_cursor_round_trips_versioned_local_position(make_file_metadata):
    """
    GIVEN a managed file metadata record and effective query fingerprint
    WHEN its versioned cursor is encoded and decoded
    THEN the local ordering position and cursor envelope remain deterministic
    """

    metadata = make_file_metadata()
    fingerprint = _query_fingerprint()

    cursor = encode_cursor(metadata, query_fingerprint=fingerprint)
    position = decode_cursor(cursor, expected_query_fingerprint=fingerprint)
    payload = _cursor_payload(cursor)

    assert position.created_at == metadata.created_at
    assert position.file_id == metadata.id
    assert payload == {
        "backend": LOCAL_FILE_LIST_CURSOR_BACKEND,
        "position": {
            "created_at": metadata.created_at.isoformat(),
            "id": str(metadata.id),
        },
        "query_fingerprint": fingerprint,
        "version": FILE_LIST_CURSOR_VERSION,
    }
    assert fingerprint == (
        "9a5afaffb694cf469b4fe34fbacddc316eda7ba04a27c74fd9f9796a92431991"
    )
    assert encode_cursor(metadata, query_fingerprint=fingerprint) == cursor


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sort", "updated_at"),
        ("order", "desc"),
        ("status", "ready"),
        ("mime_type", "text/plain"),
        ("extension", ".txt"),
    ],
    ids=["sort", "order", "status", "mime_type", "extension"],
)
def test_query_fingerprint_changes_with_effective_query_field(field, value):
    """
    GIVEN a canonical file-list query context
    WHEN one effective filter or ordering field changes
    THEN its deterministic query fingerprint changes
    """

    baseline = _query_fingerprint()

    changed = _query_fingerprint(**{field: value})

    assert changed != baseline
    assert changed == _query_fingerprint(**{field: value})


def test_query_fingerprint_normalizes_empty_optional_filters():
    """
    GIVEN absent and empty optional filters with equivalent listing behavior
    WHEN their effective query fingerprints are calculated
    THEN both contexts produce the same canonical fingerprint
    """

    absent = _query_fingerprint()
    empty = _query_fingerprint(status="", mime_type="", extension="")

    assert empty == absent


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.pop("version"),
        lambda payload: payload.update(extra="field"),
        lambda payload: payload.update(version=2),
        lambda payload: payload.update(version=True),
        lambda payload: payload.update(backend="s3"),
        lambda payload: payload.update(query_fingerprint="0" * 64),
        lambda payload: payload["position"].update(created_at="2026-01-01"),
        lambda payload: payload["position"].update(
            created_at=payload["position"]["created_at"].replace("+00:00", "Z")
        ),
        lambda payload: payload["position"].update(id="not-a-uuid"),
        lambda payload: payload["position"].update(extra="field"),
    ],
    ids=[
        "missing_field",
        "extra_field",
        "unknown_version",
        "boolean_version",
        "backend_payload_mismatch",
        "query_payload_mismatch",
        "naive_timestamp",
        "noncanonical_timestamp",
        "invalid_uuid",
        "extra_position_field",
    ],
)
def test_decode_cursor_rejects_invalid_or_incompatible_payload(
    make_file_metadata,
    mutation: Callable[[dict[str, Any]], object],
):
    """
    GIVEN a versioned cursor with invalid or incompatible payload state
    WHEN the local cursor decoder validates it
    THEN a focused cursor-domain error is raised
    """

    fingerprint = _query_fingerprint()
    valid_cursor = encode_cursor(
        make_file_metadata(),
        query_fingerprint=fingerprint,
    )
    payload = _cursor_payload(valid_cursor)
    mutation(payload)
    cursor = _encode_payload(payload)

    with pytest.raises(InvalidFileListCursorError):
        decode_cursor(
            cursor,
            expected_query_fingerprint=fingerprint,
        )


@pytest.mark.parametrize(
    "cursor",
    [
        "not_base64",
        base64.urlsafe_b64encode(b"not-json").decode("ascii"),
        "A" * (MAX_FILE_LIST_CURSOR_LENGTH + 1),
        base64.urlsafe_b64encode(b'{"backend":"local","backend":"local"}').decode(
            "ascii"
        ),
    ],
    ids=["invalid_base64", "invalid_json", "oversized", "duplicate_field"],
)
def test_decode_cursor_rejects_malformed_or_ambiguous_token(cursor):
    """
    GIVEN a malformed, oversized, or ambiguous cursor token
    WHEN the local cursor decoder validates it
    THEN a focused cursor-domain error is raised before pagination
    """

    with pytest.raises(InvalidFileListCursorError):
        decode_cursor(cursor, expected_query_fingerprint=_query_fingerprint())


def test_decode_cursor_rejects_legacy_unversioned_shape():
    """
    GIVEN a cursor using STAR's legacy unversioned tuple payload
    WHEN the versioned local cursor decoder validates it
    THEN the incompatible cursor is rejected
    """

    payload = {
        "created_at": "2026-01-01T00:00:00+00:00",
        "id": "00000000-0000-0000-0000-000000000001",
    }

    with pytest.raises(InvalidFileListCursorError):
        decode_cursor(
            _encode_payload(payload),
            expected_query_fingerprint=_query_fingerprint(),
        )


# ============================================================================
# Listing Transforms
# ============================================================================


def test_filters_apply_intersection_without_reordering(make_file_metadata):
    """
    GIVEN metadata records with mixed status, MIME type, and extensions
    WHEN listing filters are applied together
    THEN only records matching every filter are returned in input order
    """

    items = _metadata_items(make_file_metadata)

    filtered = apply_filters(
        items,
        status="ready",
        mime_type="text/plain",
        extension=".txt",
    )

    assert [item.id.int for item in filtered] == [1, 3]


def test_sort_orders_by_created_at_and_uuid(make_file_metadata):
    """
    GIVEN metadata records in arbitrary order
    WHEN ascending and descending sorts are applied
    THEN deterministic ordering uses creation timestamp and UUID
    """

    items = list(reversed(_metadata_items(make_file_metadata)))

    ascending = apply_sort(items, order="asc")
    descending = apply_sort(items, order="desc")

    assert [item.id.int for item in ascending] == [1, 2, 3]
    assert [item.id.int for item in descending] == [3, 2, 1]


def test_pagination_traverses_ascending_and_descending_pages(make_file_metadata):
    """
    GIVEN sorted metadata records and a page size smaller than the collection
    WHEN pagination continues from returned cursors in both sort directions
    THEN each direction traverses the remaining records without duplication
    """

    fingerprint = _query_fingerprint()
    ascending_items = apply_sort(_metadata_items(make_file_metadata), order="asc")
    first_page, next_cursor = apply_pagination(
        ascending_items,
        limit=2,
        cursor=None,
        order="asc",
        query_fingerprint=fingerprint,
    )
    second_page, final_cursor = apply_pagination(
        ascending_items,
        limit=2,
        cursor=decode_cursor(
            next_cursor or "",
            expected_query_fingerprint=fingerprint,
        ),
        order="asc",
        query_fingerprint=fingerprint,
    )

    descending_items = apply_sort(_metadata_items(make_file_metadata), order="desc")
    desc_fingerprint = _query_fingerprint(order="desc")
    desc_first_page, desc_next_cursor = apply_pagination(
        descending_items,
        limit=2,
        cursor=None,
        order="desc",
        query_fingerprint=desc_fingerprint,
    )
    desc_second_page, desc_final_cursor = apply_pagination(
        descending_items,
        limit=2,
        cursor=decode_cursor(
            desc_next_cursor or "",
            expected_query_fingerprint=desc_fingerprint,
        ),
        order="desc",
        query_fingerprint=desc_fingerprint,
    )

    assert [item.id.int for item in first_page] == [1, 2]
    assert [item.id.int for item in second_page] == [3]
    assert final_cursor is None
    assert [item.id.int for item in desc_first_page] == [3, 2]
    assert [item.id.int for item in desc_second_page] == [1]
    assert desc_final_cursor is None
