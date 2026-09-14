"""GET /v1/files application handler."""

from __future__ import annotations

from star.core.config import Settings, get_settings
from star.core.errors import INTERNAL_ERROR
from star.core.files import (
    InvalidFileListCursorError,
    InvalidFileListQueryError,
    build_file_list_query,
)
from star.core.files.layout import logger
from star.routes.files.schemas import FileListData, ListFilesRequest, Pagination
from star.routes.files.utils import get_file_store, map_managed_file_error


async def list_files_handler(
    request: ListFilesRequest,
    *,
    settings: Settings | None = None,
) -> FileListData:
    """List persisted file metadata using one canonical listing query.

    Args:
        request: Typed HTTP boundary values for the file-list operation.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Typed file list payload with pagination metadata.

    Raises:
        StarError: If query validation or the listing workflow fails.
    """

    cfg = settings if settings is not None else get_settings()

    try:
        query = build_file_list_query(
            limit=request.limit,
            cursor=request.cursor,
            sort=request.sort,
            order=request.order,
            status=request.status,
            mime_type=request.mime_type,
            extension=request.extension,
            file_name=request.file_name,
            tags=request.tags,
        )
        page = get_file_store(cfg).list_files(query)

        logger.info(
            "file.list.succeeded",
            extra={
                "count": len(page.files),
                "limit": query.limit,
                "has_cursor": query.cursor is not None,
                "has_status_filter": query.status is not None,
                "has_mime_type_filter": query.mime_type is not None,
                "has_extension_filter": query.extension is not None,
                "has_file_name_filter": query.file_name is not None,
                "tag_filter_count": len(query.tags),
            },
        )

        return FileListData(
            files=page.files,
            pagination=Pagination(
                count=len(page.files),
                next_cursor=page.next_cursor,
            ),
        )
    except Exception as exc:
        mapped = map_managed_file_error(exc)
        if isinstance(exc, InvalidFileListQueryError):
            logger.warning("file.list.invalid_request", extra={"reason": exc.reason})
        elif isinstance(exc, InvalidFileListCursorError):
            logger.warning(
                "file.list.invalid_request", extra={"reason": "invalid_cursor"}
            )
        elif mapped.code == INTERNAL_ERROR.code:
            logger.exception("file.list.failed")
        raise mapped from exc
