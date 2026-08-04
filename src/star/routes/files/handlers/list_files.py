"""GET /v1/files route handler."""

from __future__ import annotations

from star.core.config import Settings, get_settings
from star.core.errors import INTERNAL_ERROR, INVALID_REQUEST, StarError
from star.core.files import decode_cursor
from star.core.files.layout import logger
from star.routes.files.schemas import FileListData, Pagination
from star.routes.files.utils import get_file_store, map_managed_file_error


async def list_files_handler(
    limit: int,
    cursor: str | None,
    sort: str,
    order: str,
    status: str | None,
    mime_type: str | None,
    extension: str | None,
    settings: Settings | None = None,
) -> FileListData:
    """List persisted file metadata with filters and cursor pagination.

    Args:
        limit: Maximum number of records to return.
        cursor: Optional opaque pagination cursor.
        sort: Sort field name.
        order: Sort order, asc or desc.
        status: Optional status filter.
        mime_type: Optional MIME type filter.
        extension: Optional file extension filter.
        settings: Optional pre-loaded runtime settings.

    Returns:
        Typed file list payload with pagination metadata.

    Raises:
        StarError: If input validation or listing workflow fails.
    """

    cfg = settings if settings is not None else get_settings()

    if limit <= 0 or limit > 100:
        logger.warning("file.list.invalid_request", extra={"reason": "invalid_limit"})
        raise StarError(INVALID_REQUEST, "Invalid limit. Must be between 1 and 100.")

    if sort != "created_at":
        logger.warning("file.list.invalid_request", extra={"reason": "invalid_sort"})
        raise StarError(
            INVALID_REQUEST,
            "Invalid sort field. Only 'created_at' is supported.",
        )

    if order not in {"asc", "desc"}:
        logger.warning("file.list.invalid_request", extra={"reason": "invalid_order"})
        raise StarError(
            INVALID_REQUEST, "Invalid order. Allowed values: 'asc', 'desc'."
        )

    cursor_tuple = None
    if cursor:
        try:
            cursor_tuple = decode_cursor(cursor)
        except Exception as exc:
            logger.warning(
                "file.list.invalid_request",
                extra={"reason": "invalid_cursor"},
            )
            raise StarError(INVALID_REQUEST, "Invalid cursor.") from exc

    try:
        page = get_file_store(cfg).list_files(
            limit=limit,
            cursor=cursor_tuple,
            order=order,
            status=status,
            mime_type=mime_type,
            extension=extension,
        )

        logger.info(
            "file.list.succeeded",
            extra={
                "count": len(page.files),
                "limit": limit,
                "cursor": cursor,
                "filters": {
                    "status": status,
                    "mime_type": mime_type,
                    "extension": extension,
                },
            },
        )

        return FileListData(
            files=page.files,
            pagination=Pagination(
                count=len(page.files),
                next_cursor=page.next_cursor,
            ),
        )
    except StarError:
        raise
    except Exception as exc:
        mapped = map_managed_file_error(exc)
        if mapped.code == INTERNAL_ERROR.code:
            logger.exception("file.list.failed")
        raise mapped from exc
