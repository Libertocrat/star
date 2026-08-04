"""Tests for managed file listing cursor, filter, sort, and pagination helpers."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from star.core.files import (
    apply_filters,
    apply_pagination,
    apply_sort,
    decode_cursor,
    encode_cursor,
)


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


def test_cursor_round_trips_metadata_ordering_key(make_file_metadata):
    """
    GIVEN a managed file metadata record
    WHEN its cursor is encoded and decoded
    THEN the original creation timestamp and file id are recovered
    """

    metadata = make_file_metadata()

    created_at, file_id = decode_cursor(encode_cursor(metadata))

    assert created_at == metadata.created_at
    assert file_id == metadata.id


def test_decode_cursor_accepts_utc_z_suffix():
    """
    GIVEN an opaque cursor containing a UTC Z timestamp
    WHEN the cursor is decoded
    THEN the timestamp is normalized to a timezone-aware UTC datetime
    """

    file_id = UUID("00000000-0000-0000-0000-000000000001")
    payload = {"created_at": "2026-01-01T00:00:00Z", "id": str(file_id)}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    cursor = base64.urlsafe_b64encode(raw).decode("ascii")

    created_at, decoded_id = decode_cursor(cursor)

    assert created_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert decoded_id == file_id


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

    ascending_items = apply_sort(_metadata_items(make_file_metadata), order="asc")
    first_page, next_cursor = apply_pagination(
        ascending_items,
        limit=2,
        cursor=None,
        order="asc",
    )
    second_page, final_cursor = apply_pagination(
        ascending_items,
        limit=2,
        cursor=decode_cursor(next_cursor or ""),
        order="asc",
    )

    descending_items = apply_sort(_metadata_items(make_file_metadata), order="desc")
    desc_first_page, desc_next_cursor = apply_pagination(
        descending_items,
        limit=2,
        cursor=None,
        order="desc",
    )
    desc_second_page, desc_final_cursor = apply_pagination(
        descending_items,
        limit=2,
        cursor=decode_cursor(desc_next_cursor or ""),
        order="desc",
    )

    assert [item.id.int for item in first_page] == [1, 2]
    assert [item.id.int for item in second_page] == [3]
    assert final_cursor is None
    assert [item.id.int for item in desc_first_page] == [3, 2]
    assert [item.id.int for item in desc_second_page] == [1]
    assert desc_final_cursor is None
