"""DELETE /v1/files/{file_id} route handler."""

from __future__ import annotations

import uuid

from star.core.config import Settings, get_settings
from star.core.errors import FILE_NOT_FOUND, INTERNAL_ERROR, INVALID_REQUEST, StarError
from star.core.utils.file_storage import (
    delete_blob_file,
    delete_metadata_file,
    get_blob_path,
    get_meta_path,
    logger,
)
from star.routes.files.schemas import DeleteFileResult
from star.routes.files.utils import safe_load_metadata


async def delete_file_handler(
    file_id: uuid.UUID,
    settings: Settings | None = None,
) -> DeleteFileResult:
    """Delete a previously uploaded file and its metadata.

    Args:
        file_id: Target file UUID.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Typed delete result with deleted flag.

    Raises:
        StarError: If metadata/blob validation or deletion fails.
    """

    cfg = settings if settings is not None else get_settings()
    metadata = safe_load_metadata(file_id, cfg)

    if metadata is None:
        logger.warning(
            "file.delete.metadata_not_found",
            extra={"file_id": str(file_id)},
        )
        raise StarError(
            FILE_NOT_FOUND,
            details={"file_id": str(file_id)},
        )

    if metadata.id != file_id:
        logger.warning(
            "file.delete.invalid_metadata",
            extra={"file_id": str(file_id)},
        )
        raise StarError(
            INVALID_REQUEST,
            "File metadata does not match requested file id.",
            details={"file_id": str(file_id)},
        )

    if metadata.status != "ready":
        logger.warning(
            "file.delete.not_ready",
            extra={"file_id": str(file_id), "status": metadata.status},
        )
        raise StarError(
            INVALID_REQUEST,
            "File is not in deletable state.",
            details={"file_id": str(file_id), "status": metadata.status},
        )

    if not metadata.stored_filename or not metadata.stored_filename.strip():
        logger.warning(
            "file.delete.invalid_metadata",
            extra={"file_id": str(file_id)},
        )
        raise StarError(
            INVALID_REQUEST,
            "Stored file reference is missing from metadata.",
            details={"file_id": str(file_id)},
        )

    blob_path = get_blob_path(file_id, cfg)
    meta_path = get_meta_path(file_id, cfg)

    if metadata.stored_filename != blob_path.name:
        logger.warning(
            "file.delete.invalid_metadata",
            extra={"file_id": str(file_id)},
        )
        raise StarError(
            INVALID_REQUEST,
            "Stored file reference does not match expected blob path.",
            details={"file_id": str(file_id)},
        )

    if not blob_path.exists():
        logger.warning(
            "file.delete.blob_not_found",
            extra={"file_id": str(file_id), "blob_path": str(blob_path)},
        )
        raise StarError(
            FILE_NOT_FOUND,
            details={"file_id": str(file_id)},
        )

    if not blob_path.is_file():
        logger.warning(
            "file.delete.invalid_metadata",
            extra={"file_id": str(file_id)},
        )
        raise StarError(
            INVALID_REQUEST,
            "Stored file path is not a regular file.",
            details={"file_id": str(file_id)},
        )

    try:
        delete_blob_file(file_id, cfg)
    except FileNotFoundError as exc:
        logger.warning(
            "file.delete.blob_not_found",
            extra={"file_id": str(file_id), "blob_path": str(blob_path)},
        )
        raise StarError(
            FILE_NOT_FOUND,
            details={"file_id": str(file_id)},
        ) from exc
    except OSError as exc:
        logger.exception(
            "file.delete.blob_delete_failed",
            extra={"file_id": str(file_id), "blob_path": str(blob_path)},
        )
        raise StarError(
            INTERNAL_ERROR,
            "Failed to delete file blob.",
        ) from exc

    try:
        delete_metadata_file(file_id, cfg)
    except FileNotFoundError as exc:
        logger.exception(
            "file.delete.metadata_delete_failed",
            extra={"file_id": str(file_id), "meta_path": str(meta_path)},
        )
        raise StarError(
            INTERNAL_ERROR,
            "Failed to delete file metadata.",
        ) from exc
    except OSError as exc:
        logger.exception(
            "file.delete.metadata_delete_failed",
            extra={"file_id": str(file_id), "meta_path": str(meta_path)},
        )
        raise StarError(
            INTERNAL_ERROR,
            "Failed to delete file metadata.",
        ) from exc

    logger.info(
        "file.delete.succeeded",
        extra={
            "file_id": str(file_id),
            "blob_path": str(blob_path),
            "meta_path": str(meta_path),
            "original_filename": metadata.original_filename,
            "stored_filename": metadata.stored_filename,
            "mime_type": metadata.mime_type,
            "size_bytes": metadata.size_bytes,
        },
    )

    return DeleteFileResult(id=file_id, deleted=True)
