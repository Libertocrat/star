"""GET /v1/files/{file_id} metadata route handler."""

from __future__ import annotations

import uuid

from star.core.config import Settings, get_settings
from star.core.utils.file_storage import logger
from star.routes.files.schemas import FileMetadata
from star.routes.files.utils import safe_load_metadata


async def get_file_metadata_handler(
    file_id: uuid.UUID,
    settings: Settings | None = None,
) -> FileMetadata:
    """Load metadata for a previously uploaded file.

    Args:
        file_id: Target file UUID.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Validated file metadata model.

    Raises:
        StarError: If metadata is missing, invalid, or cannot be loaded.
    """

    cfg = settings or get_settings()
    metadata = safe_load_metadata(file_id, cfg)

    logger.info(
        "file.metadata.retrieved",
        extra={"file_id": str(file_id)},
    )

    return metadata
