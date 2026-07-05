"""Tests for STAR-managed file metadata schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from star.core.schemas.files import FileMetadata
from star.routes.files.schemas import FileMetadata as RouteFileMetadata


def _valid_metadata_payload() -> dict[str, object]:
    """Build a valid persisted file metadata payload.

    Returns:
        Dictionary payload matching the current STAR metadata contract.
    """

    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    file_id = UUID("11111111-1111-4111-8111-111111111111")
    return {
        "id": file_id,
        "original_filename": "sample.txt",
        "stored_filename": f"file_{file_id}.bin",
        "mime_type": "text/plain",
        "extension": ".txt",
        "size_bytes": 12,
        "sha256": "a" * 64,
        "created_at": now,
        "updated_at": now,
        "status": "ready",
    }


def test_file_metadata_preserves_current_persisted_shape():
    """
    GIVEN a valid STAR-managed file metadata payload
    WHEN the core FileMetadata schema validates and serializes it
    THEN the persisted JSON shape remains unchanged
    """

    metadata = FileMetadata.model_validate(_valid_metadata_payload())

    assert metadata.model_dump(mode="json") == {
        "id": "11111111-1111-4111-8111-111111111111",
        "original_filename": "sample.txt",
        "stored_filename": "file_11111111-1111-4111-8111-111111111111.bin",
        "mime_type": "text/plain",
        "extension": ".txt",
        "size_bytes": 12,
        "sha256": "a" * 64,
        "created_at": "2026-01-02T03:04:05Z",
        "updated_at": "2026-01-02T03:04:05Z",
        "status": "ready",
    }


def test_file_metadata_rejects_unknown_lifecycle_status():
    """
    GIVEN persisted file metadata with an unsupported lifecycle status
    WHEN the core FileMetadata schema validates it
    THEN validation fails closed
    """

    payload = _valid_metadata_payload()
    payload["status"] = "deleted"

    with pytest.raises(ValidationError):
        FileMetadata.model_validate(payload)


def test_file_route_schema_reexports_core_file_metadata():
    """
    GIVEN existing route-level imports of FileMetadata
    WHEN the file route schema module is imported
    THEN it exposes the core-owned metadata schema for compatibility
    """

    assert RouteFileMetadata is FileMetadata
