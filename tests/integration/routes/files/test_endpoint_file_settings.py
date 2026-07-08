"""Route wiring tests for Files API runtime settings propagation and validation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import Request, UploadFile
from fastapi.responses import JSONResponse

from star.core.schemas.files import FileMetadata
from star.routes.files import router as files_router
from star.routes.files.handlers.get_file_content import FileContentDescriptor
from star.routes.files.schemas import (
    DeleteFileResult,
    FileListData,
    Pagination,
    UploadFileRequest,
)


def _request_with_settings(settings: object) -> Request:
    """Build a lightweight request object with app state settings.

    Args:
        settings: Runtime dependency value to expose through app state.

    Returns:
        Object with the `request.app.state.settings` shape used by routes.
    """

    return cast(
        Request,
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings))),
    )


def _metadata(file_id: UUID | None = None) -> FileMetadata:
    """Build valid managed file metadata for route wiring tests.

    Args:
        file_id: Optional UUID to use for the metadata record.

    Returns:
        Validated file metadata.
    """

    resolved_id = file_id or uuid4()
    now = datetime.now(tz=UTC)
    return FileMetadata(
        id=resolved_id,
        original_filename="sample.txt",
        stored_filename=f"file_{resolved_id}.bin",
        mime_type="text/plain",
        extension=".txt",
        size_bytes=12,
        sha256="a" * 64,
        created_at=now,
        updated_at=now,
        status="ready",
    )


@pytest.mark.asyncio
async def test_post_file_passes_runtime_settings_to_upload_handler(
    monkeypatch,
    settings,
):
    """
    GIVEN a Files API upload route and runtime settings
    WHEN the endpoint delegates to the upload handler
    THEN it passes the exact runtime settings snapshot
    """

    captured: dict[str, object] = {}
    metadata = _metadata()
    upload = cast(UploadFile, object())

    async def _fake_upload_handler(upload_arg, *, verify_checksum=None, settings=None):
        captured["upload"] = upload_arg
        captured["verify_checksum"] = verify_checksum
        captured["settings"] = settings
        return metadata

    monkeypatch.setattr(files_router, "upload_file_handler", _fake_upload_handler)

    response = await files_router.post_file(
        file=upload,
        upload_request=UploadFileRequest(),
        request=_request_with_settings(settings),
    )

    assert not isinstance(response, JSONResponse)
    assert captured == {
        "upload": upload,
        "verify_checksum": None,
        "settings": settings,
    }
    assert response.data is not None
    assert response.data.file is metadata


@pytest.mark.asyncio
async def test_get_file_passes_runtime_settings_to_metadata_handler(
    monkeypatch,
    settings,
):
    """
    GIVEN a Files API metadata route and runtime settings
    WHEN the endpoint delegates to the metadata handler
    THEN it passes the exact runtime settings snapshot
    """

    captured: dict[str, object] = {}
    metadata = _metadata()

    async def _fake_metadata_handler(*, file_id, settings=None):
        captured["file_id"] = file_id
        captured["settings"] = settings
        return metadata

    monkeypatch.setattr(
        files_router, "get_file_metadata_handler", _fake_metadata_handler
    )

    response = await files_router.get_file(
        id=metadata.id,
        request=_request_with_settings(settings),
    )

    assert not isinstance(response, JSONResponse)
    assert captured == {"file_id": metadata.id, "settings": settings}
    assert response.data is not None
    assert response.data.file is metadata


@pytest.mark.asyncio
async def test_list_files_passes_runtime_settings_to_list_handler(
    monkeypatch,
    settings,
):
    """
    GIVEN a Files API listing route and runtime settings
    WHEN the endpoint delegates to the list handler
    THEN it passes the exact runtime settings snapshot
    """

    captured: dict[str, object] = {}
    data = FileListData(files=[], pagination=Pagination(count=0))

    async def _fake_list_handler(**kwargs):
        captured.update(kwargs)
        return data

    monkeypatch.setattr(files_router, "list_files_handler", _fake_list_handler)

    response = await files_router.list_files(
        request=_request_with_settings(settings),
        limit=5,
        cursor="cursor",
        sort="created_at",
        order="desc",
        status="ready",
        mime_type="text/plain",
        extension=".txt",
    )

    assert not isinstance(response, JSONResponse)
    assert captured == {
        "limit": 5,
        "cursor": "cursor",
        "sort": "created_at",
        "order": "desc",
        "status": "ready",
        "mime_type": "text/plain",
        "extension": ".txt",
        "settings": settings,
    }
    assert response.data is data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings_value",
    [None, object()],
    ids=["none", "wrong_type"],
)
async def test_list_files_returns_internal_error_when_runtime_settings_are_invalid(
    monkeypatch,
    settings_value,
):
    """
    GIVEN a Files API route with an invalid runtime settings dependency
    WHEN the endpoint is called
    THEN it returns INTERNAL_ERROR without delegating to the handler
    """

    async def _unexpected_list_handler(**_kwargs):
        raise AssertionError("list_files_handler should not be called")

    monkeypatch.setattr(files_router, "list_files_handler", _unexpected_list_handler)

    response = await files_router.list_files(
        request=_request_with_settings(settings_value),
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 500
    body = json.loads(response.body)
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"] == "Runtime settings are not available."


@pytest.mark.asyncio
async def test_get_file_content_passes_runtime_settings_to_content_handler(
    monkeypatch,
    settings,
    tmp_path: Path,
):
    """
    GIVEN a Files API content route and runtime settings
    WHEN the endpoint delegates to the content handler
    THEN it passes the exact runtime settings snapshot
    """

    captured: dict[str, object] = {}
    file_id = uuid4()
    blob_path = tmp_path / "blob.txt"
    blob_path.write_text("content", encoding="utf-8")
    descriptor = FileContentDescriptor(
        file_id=file_id,
        blob_path=blob_path,
        mime_type="text/plain",
        filename="sample.txt",
        size_bytes=7,
    )

    async def _fake_content_handler(*, file_id, settings=None):
        captured["file_id"] = file_id
        captured["settings"] = settings
        return descriptor

    monkeypatch.setattr(files_router, "get_file_content_handler", _fake_content_handler)

    response = await files_router.get_file_content(
        id=file_id,
        request=_request_with_settings(settings),
    )

    assert not isinstance(response, JSONResponse)
    assert captured == {"file_id": file_id, "settings": settings}
    assert response.headers["content-disposition"] == (
        'attachment; filename="sample.txt"'
    )
    assert response.headers["content-length"] == "7"


@pytest.mark.asyncio
async def test_delete_file_passes_runtime_settings_to_delete_handler(
    monkeypatch,
    settings,
):
    """
    GIVEN a Files API delete route and runtime settings
    WHEN the endpoint delegates to the delete handler
    THEN it passes the exact runtime settings snapshot
    """

    captured: dict[str, object] = {}
    file_id = uuid4()
    result = DeleteFileResult(id=file_id, deleted=True)

    async def _fake_delete_handler(*, file_id, settings=None):
        captured["file_id"] = file_id
        captured["settings"] = settings
        return result

    monkeypatch.setattr(files_router, "delete_file_handler", _fake_delete_handler)

    response = await files_router.delete_file(
        id=file_id,
        request=_request_with_settings(settings),
    )

    assert not isinstance(response, JSONResponse)
    assert captured == {"file_id": file_id, "settings": settings}
    assert response.data is not None
    assert response.data.file is result
