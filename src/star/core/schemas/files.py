"""Shared Pydantic schemas for STAR-managed file metadata."""

from __future__ import annotations

from datetime import datetime
from typing import Final, Literal, TypeAlias, get_args
from uuid import UUID

from pydantic import BaseModel, Field

FileStatus: TypeAlias = Literal["pending", "unverified", "ready"]
"""Permitted lifecycle values for persisted managed-file metadata."""

FILE_STATUS_VALUES: Final[frozenset[FileStatus]] = frozenset(get_args(FileStatus))
"""Immutable runtime vocabulary for managed-file lifecycle validation."""

FILE_MIME_TYPE_PATTERN: Final = r"^[a-z0-9.+-]+/[a-z0-9.+-]+$"
"""Canonical lowercase grammar for persisted MIME types."""

FILE_EXTENSION_PATTERN: Final = r"^\.[a-z0-9]+$"
"""Canonical lowercase grammar for persisted file extensions."""


class FileMetadata(BaseModel):
    """Typed metadata persisted for each STAR-managed file.

    This model validates JSON metadata sidecars at the storage boundary and is
    also projected through the current public file and action response
    contracts.

    Attributes:
        id: Stable UUID assigned by STAR for the managed file.
        original_filename: Client or producer filename after basename
            normalization.
        file_name: Editable ASCII display name used for downloads.
        tags: Canonical mutable labels used for file organization.
        stored_filename: Internal blob filename persisted by STAR.
        mime_type: Server-detected MIME type in `type/subtype` form.
        extension: Normalized lowercase extension including leading dot.
        size_bytes: Persisted file size in bytes.
        sha256: Lowercase SHA-256 digest as 64 hex characters.
        created_at: UTC timestamp when the record was created.
        updated_at: UTC timestamp when the record was last updated.
        status: Lifecycle state of the file metadata. Defaults to `ready`.
    """

    id: UUID
    original_filename: str = Field(..., min_length=1)
    file_name: str = Field(..., min_length=1, max_length=255)
    tags: list[str] = Field(..., max_length=50)
    stored_filename: str = Field(..., min_length=1)
    mime_type: str = Field(..., pattern=FILE_MIME_TYPE_PATTERN)
    extension: str = Field(..., pattern=FILE_EXTENSION_PATTERN)
    size_bytes: int = Field(..., ge=0)
    sha256: str = Field(..., min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    created_at: datetime
    updated_at: datetime
    status: FileStatus = "ready"
