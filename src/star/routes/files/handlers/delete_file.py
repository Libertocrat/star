"""DELETE /v1/files/{file_id} route handler."""

from __future__ import annotations

import uuid

from star.core.config import Settings, get_settings
from star.routes.files.schemas import DeleteFileResult
from star.routes.files.utils import get_file_store, map_managed_file_error


async def delete_file_handler(
    file_id: uuid.UUID,
    settings: Settings | None = None,
) -> DeleteFileResult:
    """Delete a file metadata record and clean its blob best-effort.

    Args:
        file_id: Target file UUID.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Typed delete result with deleted flag.

    Raises:
        StarError: If metadata validation or metadata deletion fails.
    """

    cfg = settings if settings is not None else get_settings()
    try:
        get_file_store(cfg).delete_file(file_id)
    except Exception as exc:
        raise map_managed_file_error(exc, file_id=file_id) from exc

    return DeleteFileResult(id=file_id, deleted=True)
