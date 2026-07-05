"""Shared helpers for file route handlers."""

from __future__ import annotations

import json
import uuid

from pydantic import ValidationError

from star.core.config import Settings, get_settings
from star.core.errors import FILE_NOT_FOUND, INTERNAL_ERROR, INVALID_REQUEST, StarError
from star.core.schemas.files import FileMetadata
from star.core.utils.file_storage import load_file_metadata, logger


def safe_load_metadata(
    file_id: uuid.UUID,
    settings: Settings | None = None,
) -> FileMetadata:
    """Safely load and validate file metadata from STAR storage.

    This helper centralizes the metadata loading and validation pipeline used
    across multiple file handlers (e.g., delete, content, metadata retrieval).

    It enforces a consistent error mapping strategy aligned with STAR's
    structured error model (`StarError`), ensuring that:

    - Missing metadata is mapped to FILE_NOT_FOUND
    - Corrupted JSON is mapped to INVALID_REQUEST
    - Schema validation failures are mapped to INVALID_REQUEST
    - Unexpected system errors are mapped to INTERNAL_ERROR

    This function should be used by all handlers that require metadata access
    to avoid duplication and ensure consistent behavior.

    Args:
        file_id: UUID of the file whose metadata should be loaded.
        settings: Optional pre-loaded runtime settings.

    Returns:
        A validated FileMetadata instance.

    Raises:
        StarError:
            - FILE_NOT_FOUND: If metadata file does not exist.
            - INVALID_REQUEST: If metadata is corrupted or invalid.
            - INTERNAL_ERROR: If an unexpected system error occurs.
    """

    cfg = settings or get_settings()

    try:
        metadata = load_file_metadata(file_id, cfg)

    except OSError as exc:
        logger.exception(
            "file.metadata.prepare_failed",
            extra={"file_id": str(file_id)},
        )
        raise StarError(
            INTERNAL_ERROR,
            "Failed to read file metadata.",
        ) from exc

    except json.JSONDecodeError as exc:
        logger.warning(
            "file.metadata.invalid_json",
            extra={"file_id": str(file_id), "reason": "invalid_json"},
        )
        raise StarError(
            INVALID_REQUEST,
            "Invalid file metadata (corrupted JSON).",
            details={"file_id": str(file_id)},
        ) from exc

    except ValidationError as exc:
        logger.warning(
            "file.metadata.invalid_schema",
            extra={"file_id": str(file_id), "reason": "invalid_schema"},
        )
        raise StarError(
            INVALID_REQUEST,
            "Invalid file metadata schema.",
            details={"file_id": str(file_id)},
        ) from exc

    except Exception as exc:
        logger.exception(
            "file.metadata.prepare_failed",
            extra={"file_id": str(file_id), "reason": "unexpected_error"},
        )
        raise StarError(
            INTERNAL_ERROR,
            "Unexpected error while loading file metadata.",
        ) from exc

    if metadata is None:
        logger.warning(
            "file.metadata.not_found",
            extra={"file_id": str(file_id)},
        )
        raise StarError(
            FILE_NOT_FOUND,
            details={"file_id": str(file_id)},
        )

    return metadata
