"""Pydantic schemas for STAR file upload responses.

This module defines strongly validated HTTP contracts for the STAR-managed
file API. Shared file metadata is owned by `star.core.schemas.files` and
re-exported here for route-contract compatibility.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from star.core.schemas.files import FileMetadata


class UploadFileData(BaseModel):
    """Success payload for `POST /v1/files`.

    Attributes:
        file: Persisted metadata for the uploaded file.
    """

    file: FileMetadata


class UploadFileRequest(BaseModel):
    """Input schema for `POST /v1/files` multipart form fields.

    Attributes:
        checksum: Optional SHA-256 checksum provided by the client.
    """

    checksum: str | None = Field(
        default=None,
        description="Optional SHA-256 checksum provided by the client.",
    )


class DeleteFileResult(BaseModel):
    """Delete result payload for a previously stored file.

    Attributes:
        id: UUID of the deleted file.
        deleted: Deletion success flag.
    """

    id: UUID
    deleted: bool


class DeleteFileData(BaseModel):
    """Success payload for `DELETE /v1/files/{id}`.

    Attributes:
        file: Structured delete outcome.
    """

    file: DeleteFileResult


class Pagination(BaseModel):
    """Cursor pagination metadata for file listing responses.

    Attributes:
        count: Number of files returned in the current page.
        next_cursor: Opaque cursor for the next page, if available.
    """

    count: int = Field(..., ge=0)
    next_cursor: str | None = None


class FileListData(BaseModel):
    """Success payload for `GET /v1/files`.

    Attributes:
        files: File metadata records included in the current page.
        pagination: Cursor pagination metadata.
    """

    files: list[FileMetadata]
    pagination: Pagination


# Lightweight enum for algorithms supported in v1. Expand as needed.
Algorithm = Literal["sha256", "md5", "sha1"]


class VerifyChecksumParams(BaseModel):
    """Optional checksum validation parameters for `file_verify`.

    Attributes:
        expected: Expected checksum value in hexadecimal representation.
        algorithm: Hash algorithm used for checksum verification.
    """

    expected: str = Field(..., description="Expected checksum (hex string).")
    algorithm: Algorithm = Field(
        "sha256",
        description="Hash algorithm (sha256, md5, sha1).",
    )
