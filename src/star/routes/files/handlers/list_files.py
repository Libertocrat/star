"""GET /v1/files route handler."""

from __future__ import annotations

import uuid

from star.core.config import Settings, get_settings
from star.core.errors import FILE_NOT_FOUND, INTERNAL_ERROR, INVALID_REQUEST, StarError
from star.core.utils.file_listing import (
    apply_filters,
    apply_pagination,
    apply_sort,
    decode_cursor,
)
from star.core.utils.file_storage import get_meta_path, logger
from star.routes.files.schemas import FileListData, FileMetadata, Pagination
from star.routes.files.utils import safe_load_metadata


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

    cfg = settings or get_settings()

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
        meta_dir = get_meta_path(uuid.uuid4(), cfg).parent

        items: list[FileMetadata] = []
        for meta_path in sorted(meta_dir.glob("file_*.json")):
            stem = meta_path.stem
            prefix = "file_"
            if not stem.startswith(prefix):
                continue

            raw_id = stem[len(prefix) :]
            try:
                file_id = uuid.UUID(raw_id)
            except ValueError:
                logger.warning(
                    "file.list.skipped_metadata",
                    extra={"reason": "invalid_filename", "meta_path": str(meta_path)},
                )
                continue

            try:
                metadata = safe_load_metadata(file_id, cfg)
            except StarError as exc:
                if exc.code == FILE_NOT_FOUND.code:
                    continue
                if exc.code == INVALID_REQUEST.code:
                    logger.warning(
                        "file.list.skipped_metadata",
                        extra={"reason": "invalid_metadata", "file_id": str(file_id)},
                    )
                    continue
                raise

            items.append(metadata)

        filtered = apply_filters(
            items,
            status=status,
            mime_type=mime_type,
            extension=extension,
        )
        sorted_items = apply_sort(filtered, order=order)
        page, next_cursor = apply_pagination(
            sorted_items,
            limit=limit,
            cursor=cursor_tuple,
            order=order,
        )

        logger.info(
            "file.list.succeeded",
            extra={
                "count": len(page),
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
            files=page,
            pagination=Pagination(
                count=len(page),
                next_cursor=next_cursor,
            ),
        )
    except StarError:
        raise
    except Exception as exc:
        logger.exception("file.list.failed")
        raise StarError(INTERNAL_ERROR, "Failed to list files.") from exc
