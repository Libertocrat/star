"""Typed descriptors for STAR-managed file storage operations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from star.core.schemas.files import FileMetadata


@dataclass(slots=True, frozen=True)
class UploadChecksum:
    """Checksum expectation supplied for an upload.

    Attributes:
        expected: Expected lowercase or uppercase checksum hex string.
        algorithm: Algorithm identifier. Currently only `sha256` is supported.
    """

    expected: str
    algorithm: str = "sha256"


@dataclass(slots=True, frozen=True)
class FileContentDescriptor:
    """Transport-neutral descriptor for streamed file content.

    Attributes:
        file_id: File UUID associated with the blob.
        blob_path: Filesystem path to the persisted local blob.
        mime_type: Response media type used for streaming.
        filename: Download-safe filename for Content-Disposition.
        size_bytes: Optional blob size used for Content-Length.
    """

    file_id: uuid.UUID
    blob_path: Path
    mime_type: str
    filename: str
    size_bytes: int | None


@dataclass(slots=True, frozen=True)
class FileListPage:
    """Storage-level listing page for managed file metadata.

    Attributes:
        files: Metadata records in deterministic page order.
        next_cursor: Opaque cursor for the next page, or None when exhausted.
    """

    files: list[FileMetadata]
    next_cursor: str | None


@dataclass(slots=True, frozen=True)
class FileMetadataUpdateResult:
    """Result of a successful conditional metadata replacement.

    Attributes:
        metadata: Persisted metadata after the replacement.
        etag: Strong opaque validator for the current metadata representation.
    """

    metadata: FileMetadata
    etag: str
