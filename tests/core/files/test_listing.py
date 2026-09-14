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
    FileListQuery,
    InvalidFileListCursorError,
    InvalidFileListQueryError,
    apply_filters,
    apply_pagination,
    apply_sort,
    build_file_list_query,
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
            original_filename="source-one.txt",
            file_name="Quarterly Report.txt",
            tags=["finance", "q3"],
        ),
        make_file_metadata(
            id=UUID("00000000-0000-0000-0000-000000000002"),
            created_at=base + timedelta(seconds=1),
            updated_at=base + timedelta(seconds=1),
            mime_type="image/png",
            extension=".png",
            status="pending",
            original_filename="source-two.png",
            file_name="Image Draft.png",
            tags=["q3"],
        ),
        make_file_metadata(
            id=UUID("00000000-0000-0000-0000-000000000003"),
            created_at=base + timedelta(seconds=2),
            updated_at=base + timedelta(seconds=2),
            mime_type="text/plain",
            extension=".txt",
            status="ready",
            original_filename="source-three.txt",
            file_name="Annual Report.txt",
            tags=["approved", "finance"],
        ),
    ]


def _query_fingerprint(**overrides: Any) -> str:
    """Return a fingerprint for the default test listing context.

    Args:
        **overrides: Query-context fields replacing test defaults.

    Returns:
        Deterministic query fingerprint.
    """

    return file_list_query_fingerprint(_query(**overrides))


def _query(**overrides: Any):
    """Build a canonical default listing query for unit tests.

    Args:
        **overrides: Boundary values replacing the default query fields.

    Returns:
        Canonical query accepted by the file-listing core.
    """

    context: dict[str, Any] = {
        "limit": 20,
        "cursor": None,
        "sort": "created_at",
        "order": "asc",
        "status": None,
        "mime_type": None,
        "extension": None,
        "file_name": None,
        "tags": None,
    }
    context.update(overrides)
    return build_file_list_query(
        limit=context["limit"],
        cursor=context["cursor"],
        sort=context["sort"],
        order=context["order"],
        status=context["status"],
        mime_type=context["mime_type"],
        extension=context["extension"],
        file_name=context["file_name"],
        tags=context["tags"],
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
        "9fce7d73db4918632c638b8d22b544cfa5dd1cd33fa32e0f95cfd6c42a149f57"
    )
    assert encode_cursor(metadata, query_fingerprint=fingerprint) == cursor


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("order", "desc"),
        ("status", "ready"),
        ("mime_type", "text/plain"),
        ("extension", ".txt"),
        ("file_name", "report"),
        ("tags", "finance"),
    ],
    ids=["order", "status", "mime_type", "extension", "file_name", "tags"],
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


def test_query_builder_rejects_empty_optional_filters():
    """
    GIVEN empty optional filter values
    WHEN a canonical listing query is constructed
    THEN the ambiguous values are rejected rather than normalized silently
    """

    with pytest.raises(InvalidFileListQueryError):
        _query(file_name="")


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"limit": 0}, "invalid_limit"),
        ({"limit": 101}, "invalid_limit"),
        ({"cursor": ""}, "invalid_cursor"),
        ({"sort": "name"}, "invalid_sort"),
        ({"order": "sideways"}, "invalid_order"),
        ({"status": "deleted"}, "invalid_status"),
        ({"mime_type": "textplain"}, "invalid_mime_type"),
        ({"extension": "txt"}, "missing_extension_dot"),
        ({"extension": ".TXT"}, "invalid_extension"),
        ({"file_name": "../report.txt"}, "invalid_file_name"),
        ({"file_name": " report.txt"}, "invalid_file_name"),
        ({"file_name": "r\u00e9port.txt"}, "invalid_file_name"),
        ({"file_name": "report\x00.txt"}, "invalid_file_name"),
        ({"file_name": "report\x1f.txt"}, "invalid_file_name"),
        ({"tags": "finance,"}, "invalid_tags"),
        ({"tags": "finance,Finance"}, "invalid_tags"),
        ({"tags": "finance, q3"}, "invalid_tags"),
    ],
    ids=[
        "limit-zero",
        "limit-too-high",
        "empty-cursor",
        "unsupported-sort",
        "unsupported-order",
        "unsupported-status",
        "invalid-mime",
        "missing-extension-dot",
        "uppercase-extension",
        "path-like-name",
        "leading-whitespace-name",
        "unicode-name",
        "nul-name",
        "control-name",
        "empty-tag",
        "duplicate-tag",
        "whitespace-tag",
    ],
)
def test_query_builder_rejects_policy_invalid_values(overrides, reason):
    """
    GIVEN one policy-invalid file-list boundary value
    WHEN the canonical query builder receives it
    THEN it raises the focused domain reason without an HTTP dependency
    """

    with pytest.raises(InvalidFileListQueryError) as exc_info:
        _query(**overrides)

    assert exc_info.value.reason == reason


def test_query_builder_rejects_more_than_the_maximum_tag_count():
    """
    GIVEN a CSV filter containing more tags than managed metadata allows
    WHEN the canonical query builder parses it
    THEN the shared tag-count policy rejects the request
    """

    tags = ",".join(f"tag{index}" for index in range(51))

    with pytest.raises(InvalidFileListQueryError) as exc_info:
        _query(tags=tags)

    assert exc_info.value.reason == "invalid_tags"


def test_query_builder_canonicalizes_name_and_tags():
    """
    GIVEN valid mixed-case editable-name and tag filters
    WHEN the canonical query builder receives the boundary values
    THEN it lowercases the name and returns sorted unique canonical tags
    """

    query = _query(file_name="REPORT", tags="q3,finance")

    assert query.file_name == "report"
    assert query.tags == ("finance", "q3")


def test_direct_query_construction_rejects_noncanonical_state():
    """
    GIVEN an internal listing query built without the boundary constructor
    WHEN its filename or tag state requires normalization
    THEN the value object rejects the noncanonical state defensively
    """

    with pytest.raises(InvalidFileListQueryError) as exc_info:
        FileListQuery(
            limit=20,
            cursor=None,
            sort="created_at",
            order="asc",
            status=None,
            mime_type=None,
            extension=None,
            file_name="REPORT",
            tags=("q3", "finance"),
        )

    assert exc_info.value.reason == "invalid_file_name"


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
        query=_query(
            status="ready",
            mime_type="text/plain",
            extension=".txt",
        ),
    )

    assert [item.id.int for item in filtered] == [1, 3]


def test_filters_match_editable_file_name_and_required_tags(make_file_metadata):
    """
    GIVEN metadata with distinct original names, editable names, and tag sets
    WHEN file-name and tag filters are applied with existing filters
    THEN only records satisfying their full intersection are returned in order
    """

    items = _metadata_items(make_file_metadata)

    filtered = apply_filters(
        items,
        query=_query(
            status="ready",
            mime_type="text/plain",
            extension=".txt",
            file_name="REPORT",
            tags="finance",
        ),
    )

    assert [item.id.int for item in filtered] == [1, 3]

    all_of = apply_filters(
        items,
        query=_query(tags="finance,q3"),
    )

    assert [item.id.int for item in all_of] == [1]


def test_file_name_filter_does_not_match_original_filename(make_file_metadata):
    """
    GIVEN metadata whose original and editable names differ
    WHEN listing filters by text found only in the original filename
    THEN no record is returned
    """

    filtered = apply_filters(
        _metadata_items(make_file_metadata),
        query=_query(file_name="source-one"),
    )

    assert filtered == []


def test_query_fingerprint_accepts_equivalent_canonical_name_and_tag_contexts():
    """
    GIVEN logically equivalent file-name and tag filter contexts
    WHEN their fingerprints are calculated
    THEN case and tag ordering do not change the continuation context
    """

    first = _query_fingerprint(file_name="report", tags="finance,q3")
    equivalent = _query_fingerprint(file_name="REPORT", tags="q3,finance")

    assert first == equivalent


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
