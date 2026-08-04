"""Shared fixtures for core managed file tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from star.core.schemas.files import FileMetadata


@pytest.fixture
def make_file_metadata() -> Callable[..., FileMetadata]:
    """Return a factory for valid managed file metadata records."""

    def create(**overrides: Any) -> FileMetadata:
        """Build valid `FileMetadata` with explicit field overrides.

        Args:
            **overrides: Field values that should replace the default metadata.

        Returns:
            Validated managed file metadata.
        """

        file_id = overrides.pop("id", uuid4())
        now = datetime(2026, 1, 1, tzinfo=UTC)
        payload: dict[str, Any] = {
            "id": file_id,
            "original_filename": "report.txt",
            "stored_filename": f"file_{file_id}.bin",
            "mime_type": "text/plain",
            "extension": ".txt",
            "size_bytes": 12,
            "sha256": "a" * 64,
            "created_at": now,
            "updated_at": now,
            "status": "ready",
        }
        payload.update(overrides)
        return FileMetadata.model_validate(payload)

    return create
