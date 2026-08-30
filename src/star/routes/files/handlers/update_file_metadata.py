"""PUT /v1/files/{id} metadata route handler."""

from __future__ import annotations

import uuid

from star.core.config import Settings, get_settings
from star.core.files import FileMetadataUpdateResult
from star.routes.files.utils import get_file_store, map_managed_file_error


async def update_file_metadata_handler(
    *,
    file_id: uuid.UUID,
    file_name: str,
    tags: tuple[str, ...],
    expected_etag: str,
    settings: Settings | None = None,
) -> FileMetadataUpdateResult:
    """Conditionally replace editable metadata for a managed file.

    Args:
        file_id: Target managed-file UUID.
        file_name: Validated replacement display filename.
        tags: Validated complete replacement tag set.
        expected_etag: Strong current metadata validator from `If-Match`.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Updated metadata plus its current ETag.

    Raises:
        StarError: If validation, precondition, or persistence fails.
    """

    cfg = settings if settings is not None else get_settings()
    try:
        return get_file_store(cfg).update_metadata(
            file_id,
            file_name=file_name,
            tags=tags,
            expected_etag=expected_etag,
        )
    except Exception as exc:
        raise map_managed_file_error(exc, file_id=file_id) from exc
