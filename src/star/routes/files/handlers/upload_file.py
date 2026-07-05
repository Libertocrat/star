"""POST /v1/files route handler."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Form, UploadFile

from star.core.config import Settings, get_settings
from star.core.errors import (
    FILE_EXTENSION_MISSING,
    FILE_TOO_LARGE,
    INTERNAL_ERROR,
    INVALID_ALGORITHM,
    INVALID_REQUEST,
    MIME_MAPPING_NOT_DEFINED,
    UNSUPPORTED_MEDIA_TYPE,
    StarError,
)
from star.core.schemas.files import FileMetadata
from star.core.utils.file_storage import (
    FileExtensionMissingError,
    MimeMappingNotDefinedError,
    UnsupportedMediaTypeValidationError,
    _detect_mime,
    _validate_extension_and_mime,
    get_blob_path,
    get_tmp_dir,
    logger,
    save_file_metadata,
)
from star.routes.files.schemas import (
    UploadFileRequest,
    VerifyChecksumParams,
)


def parse_post_file_request(
    checksum: Annotated[str | None, Form()] = None,
) -> UploadFileRequest:
    """Build typed request payload for POST /v1/files form fields.

    Args:
        checksum: Optional client-provided SHA-256 checksum.

    Returns:
        Typed upload request model.
    """

    return UploadFileRequest(checksum=checksum)


async def upload_file_handler(
    upload: UploadFile,
    verify_checksum: VerifyChecksumParams | None = None,
    settings: Settings | None = None,
) -> FileMetadata:
    """Validate and persist an uploaded file under STAR-managed storage.

    Args:
        upload: Incoming FastAPI multipart file stream.
        verify_checksum: Optional checksum constraint provided by the client.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Persisted file metadata.

    Raises:
        StarError: If validation or persistence fails.
    """

    cfg = settings if settings is not None else get_settings()
    file_id = uuid.uuid4()
    tmp_path = get_tmp_dir(cfg) / f"upload_{file_id}.tmp"
    blob_path = get_blob_path(file_id, cfg)

    hasher = hashlib.sha256()
    size_bytes = 0
    max_bytes = cfg.star_max_file_bytes
    moved_to_blob = False

    try:
        # Note: UploadFile stream is consumed during read; cannot be reused.
        with tmp_path.open("wb") as temp_f:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break

                size_bytes += len(chunk)
                if max_bytes is not None and size_bytes > max_bytes:
                    raise StarError(FILE_TOO_LARGE)

                hasher.update(chunk)
                temp_f.write(chunk)

            if size_bytes == 0:
                raise StarError(INVALID_REQUEST, "Empty file is not allowed.")

        sha256 = hasher.hexdigest()

        if verify_checksum is not None:
            if verify_checksum.algorithm != "sha256":
                raise StarError(INVALID_ALGORITHM)
            if sha256.lower() != verify_checksum.expected.strip().lower():
                raise StarError(
                    INVALID_REQUEST,
                    "Checksum mismatch.",
                    details={
                        "algorithm": "sha256",
                        "expected": verify_checksum.expected,
                        "actual": sha256,
                    },
                )

        detected_mime = _detect_mime(tmp_path)
        original_filename = Path(upload.filename or "uploaded_file").name
        try:
            extension = _validate_extension_and_mime(original_filename, detected_mime)
        except FileExtensionMissingError as exc:
            raise StarError(FILE_EXTENSION_MISSING) from exc
        except MimeMappingNotDefinedError as exc:
            raise StarError(
                MIME_MAPPING_NOT_DEFINED,
                details={"extension": exc.extension},
            ) from exc
        except UnsupportedMediaTypeValidationError as exc:
            raise StarError(
                UNSUPPORTED_MEDIA_TYPE,
                message=str(exc),
                details={
                    "extension": exc.extension,
                    "detected_mime": exc.detected_mime,
                },
            ) from exc

        os.replace(tmp_path, blob_path)
        moved_to_blob = True

        now_utc = datetime.now(UTC)
        metadata = FileMetadata(
            id=file_id,
            original_filename=original_filename,
            stored_filename=blob_path.name,
            mime_type=detected_mime,
            extension=extension,
            size_bytes=size_bytes,
            sha256=sha256,
            created_at=now_utc,
            updated_at=now_utc,
            status="ready",
        )

        try:
            save_file_metadata(metadata, cfg)
        except Exception as exc:
            if moved_to_blob and blob_path.exists():
                try:
                    blob_path.unlink()
                except OSError:
                    logger.exception(
                        "Failed to cleanup blob after metadata write error"
                    )
            raise StarError(
                INTERNAL_ERROR,
                "Failed to persist file metadata.",
            ) from exc

        logger.info(
            "File stored",
            extra={
                "file_id": str(file_id),
                "size": size_bytes,
                "mime": detected_mime,
                "original_filename": original_filename,
            },
        )

        return metadata

    except StarError:
        raise
    except Exception as exc:
        raise StarError(INTERNAL_ERROR) from exc
    finally:
        try:
            await upload.close()
        except Exception:
            logger.exception("Failed to close uploaded file stream")

        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                logger.exception("Failed to cleanup temporary upload file")
