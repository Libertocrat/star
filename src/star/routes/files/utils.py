"""Shared helpers for file route handlers."""

from __future__ import annotations

import uuid

from star.core.config import Settings, get_settings
from star.core.errors import (
    FILE_EXTENSION_MISSING,
    FILE_NOT_FOUND,
    FILE_TOO_LARGE,
    INTERNAL_ERROR,
    INVALID_ALGORITHM,
    INVALID_REQUEST,
    MIME_MAPPING_NOT_DEFINED,
    UNSUPPORTED_MEDIA_TYPE,
    StarError,
)
from star.core.files import (
    ChecksumMismatchError,
    EmptyManagedFileError,
    FileExtensionMissingError,
    InvalidChecksumAlgorithmError,
    InvalidManagedFileMetadataError,
    LocalManagedFileStore,
    ManagedFileError,
    ManagedFileNotFoundError,
    ManagedFileStorageError,
    ManagedFileTooLargeError,
    MimeMappingNotDefinedError,
    UnsupportedMediaTypeValidationError,
)
from star.core.schemas.files import FileMetadata


def get_file_store(settings: Settings | None = None) -> LocalManagedFileStore:
    """Build the local managed file store for route handlers.

    Args:
        settings: Optional pre-loaded runtime settings.

    Returns:
        Local managed file store bound to the active settings.
    """

    cfg = settings if settings is not None else get_settings()
    return LocalManagedFileStore(cfg)


def map_managed_file_error(
    exc: Exception,
    *,
    file_id: uuid.UUID | None = None,
) -> StarError:
    """Map storage-domain failures to stable STAR public errors.

    Args:
        exc: Domain exception raised by `star.core.files`.
        file_id: Optional file UUID used for safe public details.

    Returns:
        Transport-ready STAR error.
    """

    details = {"file_id": str(file_id)} if file_id is not None else None

    if isinstance(exc, ManagedFileNotFoundError):
        return StarError(FILE_NOT_FOUND, details=details)
    if isinstance(exc, InvalidManagedFileMetadataError):
        return StarError(
            INVALID_REQUEST,
            str(exc) or "Invalid file metadata.",
            details=details,
        )
    if isinstance(exc, ManagedFileStorageError):
        return StarError(INTERNAL_ERROR, str(exc) or "Failed to process file.")
    if isinstance(exc, ManagedFileTooLargeError):
        return StarError(FILE_TOO_LARGE)
    if isinstance(exc, EmptyManagedFileError):
        return StarError(INVALID_REQUEST, "Empty file is not allowed.")
    if isinstance(exc, InvalidChecksumAlgorithmError):
        return StarError(INVALID_ALGORITHM)
    if isinstance(exc, ChecksumMismatchError):
        return StarError(
            INVALID_REQUEST,
            "Checksum mismatch.",
            details={
                "algorithm": exc.algorithm,
                "expected": exc.expected,
                "actual": exc.actual,
            },
        )
    if isinstance(exc, FileExtensionMissingError):
        return StarError(FILE_EXTENSION_MISSING)
    if isinstance(exc, MimeMappingNotDefinedError):
        return StarError(
            MIME_MAPPING_NOT_DEFINED,
            details={"extension": exc.extension},
        )
    if isinstance(exc, UnsupportedMediaTypeValidationError):
        return StarError(
            UNSUPPORTED_MEDIA_TYPE,
            message=str(exc),
            details={
                "extension": exc.extension,
                "detected_mime": exc.detected_mime,
            },
        )
    if isinstance(exc, ManagedFileError):
        return StarError(INTERNAL_ERROR, "Failed to process file.")
    return StarError(INTERNAL_ERROR)


def safe_load_metadata(
    file_id: uuid.UUID,
    settings: Settings | None = None,
) -> FileMetadata:
    """Safely load and validate file metadata from STAR storage.

    Args:
        file_id: UUID of the file whose metadata should be loaded.
        settings: Optional pre-loaded runtime settings.

    Returns:
        A validated FileMetadata instance.

    Raises:
        StarError: If metadata is missing, invalid, or cannot be loaded.
    """

    try:
        return get_file_store(settings).require_metadata(file_id)
    except Exception as exc:
        raise map_managed_file_error(exc, file_id=file_id) from exc
