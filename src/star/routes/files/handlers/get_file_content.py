"""GET /v1/files/{file_id}/content route handler."""

from __future__ import annotations

import uuid

from star.core.config import Settings, get_settings
from star.core.files import FileContentDescriptor
from star.routes.files.utils import get_file_store, map_managed_file_error


async def get_file_content_handler(
    file_id: uuid.UUID,
    settings: Settings | None = None,
) -> FileContentDescriptor:
    """Resolve and validate metadata and blob path for content streaming.

    Args:
        file_id: Target file UUID.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Descriptor containing validated streaming metadata.

    Raises:
        StarError: If metadata/blob validation or stream preparation fails.
    """

    cfg = settings if settings is not None else get_settings()
    try:
        return get_file_store(cfg).resolve_content(file_id)
    except Exception as exc:
        raise map_managed_file_error(exc, file_id=file_id) from exc
