"""Shared Pydantic schemas for STAR-managed file metadata."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class FileMetadata(BaseModel):
    """Typed metadata persisted for each STAR-managed file.

    This model validates JSON metadata sidecars at the storage boundary and is
    also projected through the current public file and action response
    contracts.

    Attributes:
        id: Stable UUID assigned by STAR for the managed file.
        original_filename: Client or producer filename after basename
            normalization.
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
    stored_filename: str = Field(..., min_length=1)
    mime_type: str = Field(..., pattern=r"^[a-z0-9.+-]+/[a-z0-9.+-]+$")
    extension: str = Field(..., pattern=r"^\.[a-z0-9]+$")
    size_bytes: int = Field(..., ge=0)
    sha256: str = Field(..., min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    created_at: datetime
    updated_at: datetime
    status: Literal["pending", "unverified", "ready"] = "ready"
