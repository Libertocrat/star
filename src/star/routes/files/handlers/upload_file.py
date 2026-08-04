"""POST /v1/files route handler."""

from __future__ import annotations

from typing import Annotated

from fastapi import Form, UploadFile

from star.core.config import Settings, get_settings
from star.core.files import UploadChecksum
from star.core.files.layout import logger
from star.core.schemas.files import FileMetadata
from star.routes.files.schemas import (
    UploadFileRequest,
    VerifyChecksumParams,
)
from star.routes.files.utils import get_file_store, map_managed_file_error


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
    checksum = (
        UploadChecksum(
            expected=verify_checksum.expected,
            algorithm=verify_checksum.algorithm,
        )
        if verify_checksum is not None
        else None
    )

    try:
        return await get_file_store(cfg).upload_stream(
            upload,
            original_filename=upload.filename,
            checksum=checksum,
        )
    except Exception as exc:
        raise map_managed_file_error(exc) from exc
    finally:
        try:
            await upload.close()
        except Exception:
            logger.exception("Failed to close uploaded file stream")
