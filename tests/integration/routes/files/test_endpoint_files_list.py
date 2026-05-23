"""Integration tests for the /v1/files list endpoint.

These tests validate cursor pagination, deterministic sorting, filtering,
error mapping, and resilience to malformed metadata entries.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from star.core.errors import INVALID_REQUEST, UNAUTHORIZED

# ============================================================================
# Helpers
# ============================================================================


def _upload_file_and_get_data(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    filename: str,
    content: bytes,
    content_type: str,
) -> dict:
    """Upload a file through HTTP API and return the file payload from response.

    Args:
            client: Test client bound to the STAR app.
            auth_headers: Authorization headers for private endpoints.
            filename: Multipart filename to send.
            content: Raw file bytes.
            content_type: Multipart content type.

    Returns:
            The `data.file` object from successful upload response.
    """

    response = client.post(
        "/v1/files",
        headers=auth_headers,
        files={"file": (filename, content, content_type)},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    return body["data"]["file"]


def _meta_path_for(tmp_path: Path, file_id: UUID) -> Path:
    """Return metadata path for a test file id.

    Args:
            tmp_path: Per-test root temporary directory.
            file_id: File UUID.

    Returns:
            Metadata json file path under STAR data root.
    """

    return tmp_path / "data" / "files" / "meta" / f"file_{file_id}.json"


def _list_files(
    client: TestClient,
    auth_headers: dict[str, str],
    **params,
):
    """Call GET /v1/files with auth headers and query params.

    Args:
            client: Test client bound to the STAR app.
            auth_headers: Authorization headers.
            **params: Query parameters forwarded to endpoint.

    Returns:
            Raw HTTP response.
    """

    return client.get("/v1/files", headers=auth_headers, params=params)


# ============================================================================
# Happy path
# ============================================================================


def test_list_files_basic_success(create_upload_app, auth_headers):
    """
    GIVEN three previously uploaded files
    WHEN GET /v1/files is called
    THEN it returns 200 with all files and no next cursor
    """

    app = create_upload_app()

    with TestClient(app) as client:
        _upload_file_and_get_data(
            client,
            auth_headers,
            filename="a.txt",
            content=b"a\n",
            content_type="text/plain",
        )
        _upload_file_and_get_data(
            client,
            auth_headers,
            filename="b.txt",
            content=b"b\n",
            content_type="text/plain",
        )
        _upload_file_and_get_data(
            client,
            auth_headers,
            filename="c.txt",
            content=b"c\n",
            content_type="text/plain",
        )

        response = _list_files(client, auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert len(body["data"]["files"]) == 3
    assert body["data"]["pagination"]["count"] == 3
    assert body["data"]["pagination"]["next_cursor"] is None


def test_list_files_with_limit(create_upload_app, auth_headers):
    """
    GIVEN five uploaded files
    WHEN GET /v1/files is called with limit=2
    THEN it returns two files and a non-empty next cursor
    """

    app = create_upload_app()

    with TestClient(app) as client:
        for index in range(5):
            _upload_file_and_get_data(
                client,
                auth_headers,
                filename=f"limit-{index}.txt",
                content=f"{index}\n".encode("utf-8"),
                content_type="text/plain",
            )

        response = _list_files(client, auth_headers, limit=2)

    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]["files"]) == 2
    assert body["data"]["pagination"]["count"] == 2
    assert body["data"]["pagination"]["next_cursor"] is not None


def test_list_files_cursor_pagination(create_upload_app, auth_headers):
    """
    GIVEN five uploaded files in creation order
    WHEN two paginated requests are executed with limit=3
    THEN pages do not overlap and ordering remains stable
    """

    app = create_upload_app()

    with TestClient(app) as client:
        uploaded_ids: list[str] = []
        for index in range(5):
            file_data = _upload_file_and_get_data(
                client,
                auth_headers,
                filename=f"cursor-{index}.txt",
                content=f"cursor-{index}\n".encode("utf-8"),
                content_type="text/plain",
            )
            uploaded_ids.append(file_data["id"])
            time.sleep(0.01)

        first = _list_files(client, auth_headers, limit=3)
        assert first.status_code == 200
        first_body = first.json()
        first_files = first_body["data"]["files"]
        cursor = first_body["data"]["pagination"]["next_cursor"]

        second = _list_files(client, auth_headers, limit=3, cursor=cursor)
        assert second.status_code == 200
        second_body = second.json()
        second_files = second_body["data"]["files"]

    first_ids = [item["id"] for item in first_files]
    second_ids = [item["id"] for item in second_files]

    assert first_ids != second_ids
    assert set(first_ids).isdisjoint(set(second_ids))
    assert first_ids == uploaded_ids[:3]
    assert second_ids == uploaded_ids[3:]


def test_list_files_no_more_pages(create_upload_app, auth_headers):
    """
    GIVEN two uploaded files
    WHEN GET /v1/files is called with limit larger than total
    THEN next_cursor is null
    """

    app = create_upload_app()

    with TestClient(app) as client:
        for index in range(2):
            _upload_file_and_get_data(
                client,
                auth_headers,
                filename=f"end-{index}.txt",
                content=b"end\n",
                content_type="text/plain",
            )

        response = _list_files(client, auth_headers, limit=5)

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["pagination"]["next_cursor"] is None


def test_list_files_cursor_pagination_full_walk(create_upload_app, auth_headers):
    """
    GIVEN multiple uploaded files exceeding page size
    WHEN iterating through all pages using next_cursor
    THEN all files are returned exactly once without duplication
    """

    app = create_upload_app()

    with TestClient(app) as client:
        total_files = 6

        for index in range(total_files):
            _upload_file_and_get_data(
                client,
                auth_headers,
                filename=f"walk-{index}.txt",
                content=b"walk\n",
                content_type="text/plain",
            )

        seen_ids = set()
        cursor = None

        while True:
            params = {"limit": 2}
            if cursor:
                params["cursor"] = cursor

            response = _list_files(client, auth_headers, **params)
            assert response.status_code == 200

            body = response.json()
            files = body["data"]["files"]

            for item in files:
                assert item["id"] not in seen_ids
                seen_ids.add(item["id"])

            cursor = body["data"]["pagination"]["next_cursor"]

            if not cursor:
                break

    assert len(seen_ids) == total_files


# ============================================================================
# Sorting
# ============================================================================


def test_list_files_sort_ascending(create_upload_app, auth_headers):
    """
    GIVEN uploaded files with different creation timestamps
    WHEN GET /v1/files?order=asc is called
    THEN returned timestamps are strictly increasing
    """

    app = create_upload_app()

    with TestClient(app) as client:
        for index in range(4):
            _upload_file_and_get_data(
                client,
                auth_headers,
                filename=f"asc-{index}.txt",
                content=b"asc\n",
                content_type="text/plain",
            )
            time.sleep(0.01)

        response = _list_files(client, auth_headers, order="asc")

    assert response.status_code == 200
    files = response.json()["data"]["files"]
    timestamps = [
        datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
        for item in files
    ]
    assert timestamps == sorted(timestamps)


def test_list_files_sort_descending(create_upload_app, auth_headers):
    """
    GIVEN uploaded files with different creation timestamps
    WHEN GET /v1/files?order=desc is called
    THEN returned timestamps are strictly decreasing
    """

    app = create_upload_app()

    with TestClient(app) as client:
        for index in range(4):
            _upload_file_and_get_data(
                client,
                auth_headers,
                filename=f"desc-{index}.txt",
                content=b"desc\n",
                content_type="text/plain",
            )
            time.sleep(0.01)

        response = _list_files(client, auth_headers, order="desc")

    assert response.status_code == 200
    files = response.json()["data"]["files"]
    timestamps = [
        datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
        for item in files
    ]
    assert timestamps == sorted(timestamps, reverse=True)


# ============================================================================
# Filters
# ============================================================================


def test_list_files_filter_status(create_upload_app, auth_headers):
    """
    GIVEN uploaded files in ready state
    WHEN GET /v1/files?status=ready is called
    THEN all returned items match the status filter
    """

    app = create_upload_app()

    with TestClient(app) as client:
        for index in range(3):
            _upload_file_and_get_data(
                client,
                auth_headers,
                filename=f"status-{index}.txt",
                content=b"status\n",
                content_type="text/plain",
            )

        response = _list_files(client, auth_headers, status="ready")

    assert response.status_code == 200
    files = response.json()["data"]["files"]
    assert files
    assert all(item["status"] == "ready" for item in files)


@pytest.mark.parametrize(
    "target_file_type",
    ["text", "pdf", "png", "zip"],
    ids=["mime_text", "mime_pdf", "mime_png", "mime_zip"],
)
def test_list_files_filter_mime_type(
    create_upload_app,
    auth_headers,
    file_factory,
    target_file_type,
):
    """
    GIVEN uploaded files with different MIME types
    WHEN GET /v1/files?mime_type=<exact uploaded mime> is called
    THEN all returned items match the requested mime_type
    """

    app = create_upload_app()

    upload_matrix = [
        ("text", "multi-a.txt"),
        ("pdf", "multi-b.pdf"),
        ("png", "multi-c.png"),
        ("zip", "multi-d.zip"),
    ]

    with TestClient(app) as client:
        observed_mimes: dict[str, str] = {}

        for file_type, filename in upload_matrix:
            sf = file_factory(file_type, filename)
            file_data = _upload_file_and_get_data(
                client,
                auth_headers,
                filename=filename,
                content=sf.abs_path.read_bytes(),
                content_type="application/octet-stream",
            )
            observed_mimes[file_type] = file_data["mime_type"]

        target_mime = observed_mimes[target_file_type]
        response = _list_files(client, auth_headers, mime_type=target_mime)

    assert response.status_code == 200
    files = response.json()["data"]["files"]
    assert files
    assert all(item["mime_type"] == target_mime for item in files)


@pytest.mark.parametrize(
    "target_file_type",
    ["text", "pdf", "png", "zip"],
    ids=["ext_txt", "ext_pdf", "ext_png", "ext_zip"],
)
def test_list_files_filter_extension(
    create_upload_app,
    auth_headers,
    file_factory,
    target_file_type,
):
    """
    GIVEN uploaded files with different extensions
    WHEN GET /v1/files?extension=<ext> is called
    THEN all returned items match the extension filter
    """

    app = create_upload_app()

    upload_matrix = [
        ("text", "ext-a.txt"),
        ("pdf", "ext-b.pdf"),
        ("png", "ext-c.png"),
        ("zip", "ext-d.zip"),
    ]

    with TestClient(app) as client:
        observed_extensions: dict[str, str] = {}

        for file_type, filename in upload_matrix:
            sf = file_factory(file_type, filename)
            file_data = _upload_file_and_get_data(
                client,
                auth_headers,
                filename=filename,
                content=sf.abs_path.read_bytes(),
                content_type="application/octet-stream",
            )
            observed_extensions[file_type] = file_data["extension"]

        target_extension = observed_extensions[target_file_type]
        response = _list_files(client, auth_headers, extension=target_extension)

    assert response.status_code == 200
    files = response.json()["data"]["files"]
    assert files
    assert all(item["extension"] == target_extension for item in files)


def test_list_files_filter_combined(create_upload_app, auth_headers):
    """
    GIVEN uploaded files with different mime types
    WHEN GET /v1/files with status and mime_type filters is called
    THEN results satisfy both filters in intersection mode
    """

    app = create_upload_app()

    with TestClient(app) as client:
        _upload_file_and_get_data(
            client,
            auth_headers,
            filename="combined.txt",
            content=b"combined\n",
            content_type="text/plain",
        )
        _upload_file_and_get_data(
            client,
            auth_headers,
            filename="combined2.txt",
            content=b"combined2\n",
            content_type="text/plain",
        )

        response = _list_files(
            client,
            auth_headers,
            status="ready",
            mime_type="text/plain",
        )

    assert response.status_code == 200
    files = response.json()["data"]["files"]
    assert files
    assert all(item["status"] == "ready" for item in files)
    assert all(item["mime_type"] == "text/plain" for item in files)


def test_list_files_filter_extension_without_dot_returns_empty(
    create_upload_app, auth_headers
):
    """
    GIVEN uploaded files with extensions
    WHEN GET /v1/files?extension without leading dot is called
    THEN response succeeds but no items match the filter
    """

    app = create_upload_app()

    with TestClient(app) as client:
        _upload_file_and_get_data(
            client,
            auth_headers,
            filename="no-dot.txt",
            content=b"no-dot\n",
            content_type="text/plain",
        )

        response = _list_files(client, auth_headers, extension="txt")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["files"] == []
    assert body["data"]["pagination"]["count"] == 0
    assert body["data"]["pagination"]["next_cursor"] is None


def test_list_files_filter_combined_with_extension(
    create_upload_app,
    auth_headers,
    file_factory,
):
    """
    GIVEN uploaded files with different mime types and extensions
    WHEN GET /v1/files with status, mime_type and extension filters is called
    THEN results satisfy all filters in intersection mode
    """

    app = create_upload_app()

    upload_matrix = [
        ("text", "combo-a.txt"),
        ("pdf", "combo-b.pdf"),
    ]

    with TestClient(app) as client:
        observed: dict[str, dict[str, str]] = {}

        # Arrange: upload files using real content via file_factory
        for file_type, filename in upload_matrix:
            sf = file_factory(file_type, filename)
            file_data = _upload_file_and_get_data(
                client,
                auth_headers,
                filename=filename,
                content=sf.abs_path.read_bytes(),
                content_type="application/octet-stream",
            )
            observed[file_type] = {
                "mime_type": file_data["mime_type"],
                "extension": file_data["extension"],
            }

        target = observed["text"]

        # Act
        response = _list_files(
            client,
            auth_headers,
            status="ready",
            mime_type=target["mime_type"],
            extension=target["extension"],
        )

    # Assert
    assert response.status_code == 200
    files = response.json()["data"]["files"]
    assert files

    assert all(item["status"] == "ready" for item in files)
    assert all(item["mime_type"] == target["mime_type"] for item in files)
    assert all(item["extension"] == target["extension"] for item in files)


# ============================================================================
# Invalid params
# ============================================================================


@pytest.mark.parametrize("limit", [0, 1000], ids=["limit_zero", "limit_too_high"])
def test_list_files_invalid_limit(create_upload_app, auth_headers, limit):
    """
    GIVEN an invalid limit value
    WHEN GET /v1/files is called with that limit
    THEN endpoint returns INVALID_REQUEST with HTTP 400
    """

    app = create_upload_app()

    with TestClient(app) as client:
        response = _list_files(client, auth_headers, limit=limit)

    assert response.status_code == INVALID_REQUEST.http_status
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == INVALID_REQUEST.code


def test_list_files_invalid_sort(create_upload_app, auth_headers):
    """
    GIVEN an unsupported sort field
    WHEN GET /v1/files?sort=name is called
    THEN endpoint returns INVALID_REQUEST with HTTP 400
    """

    app = create_upload_app()

    with TestClient(app) as client:
        response = _list_files(client, auth_headers, sort="name")

    assert response.status_code == INVALID_REQUEST.http_status
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == INVALID_REQUEST.code


def test_list_files_invalid_order(create_upload_app, auth_headers):
    """
    GIVEN an unsupported order value
    WHEN GET /v1/files?order=invalid is called
    THEN endpoint returns INVALID_REQUEST with HTTP 400
    """

    app = create_upload_app()

    with TestClient(app) as client:
        response = _list_files(client, auth_headers, order="invalid")

    assert response.status_code == INVALID_REQUEST.http_status
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == INVALID_REQUEST.code


def test_list_files_invalid_cursor(create_upload_app, auth_headers):
    """
    GIVEN a malformed cursor token
    WHEN GET /v1/files?cursor=not_base64 is called
    THEN endpoint returns INVALID_REQUEST with HTTP 400
    """

    app = create_upload_app()

    with TestClient(app) as client:
        response = _list_files(client, auth_headers, cursor="not_base64")

    assert response.status_code == INVALID_REQUEST.http_status
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == INVALID_REQUEST.code


# ============================================================================
# Authorization and contract
# ============================================================================


def test_list_files_requires_auth(create_upload_app):
    """
    GIVEN no Authorization header
    WHEN GET /v1/files is called
    THEN endpoint returns UNAUTHORIZED with HTTP 401
    """

    app = create_upload_app()

    with TestClient(app) as client:
        response = client.get("/v1/files")

    assert response.status_code == UNAUTHORIZED.http_status
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == UNAUTHORIZED.code


def test_list_files_response_structure(create_upload_app, auth_headers):
    """
    GIVEN a valid list request
    WHEN GET /v1/files is called
    THEN response follows STAR list envelope structure
    """

    app = create_upload_app()

    with TestClient(app) as client:
        _upload_file_and_get_data(
            client,
            auth_headers,
            filename="structure.txt",
            content=b"structure\n",
            content_type="text/plain",
        )

        response = _list_files(client, auth_headers)

    assert response.status_code == 200
    body = response.json()

    assert body["success"] is True
    assert body["error"] is None
    assert isinstance(body["data"], dict)
    assert isinstance(body["data"]["files"], list)
    assert isinstance(body["data"]["pagination"], dict)
    assert isinstance(body["data"]["pagination"]["count"], int)
    assert body["data"]["pagination"]["next_cursor"] is None or isinstance(
        body["data"]["pagination"]["next_cursor"],
        str,
    )


def test_list_files_cursor_stability(create_upload_app, auth_headers):
    """
    GIVEN a paginated list flow across multiple pages
    WHEN next_cursor is used on subsequent request
    THEN there are no duplicate ids across pages
    """

    app = create_upload_app()

    with TestClient(app) as client:
        for index in range(6):
            _upload_file_and_get_data(
                client,
                auth_headers,
                filename=f"stable-{index}.txt",
                content=f"stable-{index}\n".encode("utf-8"),
                content_type="text/plain",
            )
            time.sleep(0.01)

        first = _list_files(client, auth_headers, limit=2)
        assert first.status_code == 200
        first_body = first.json()
        first_ids = [item["id"] for item in first_body["data"]["files"]]
        cursor = first_body["data"]["pagination"]["next_cursor"]
        assert cursor is not None

        second = _list_files(client, auth_headers, limit=2, cursor=cursor)
        assert second.status_code == 200
        second_ids = [item["id"] for item in second.json()["data"]["files"]]

    assert set(first_ids).isdisjoint(set(second_ids))


# ============================================================================
# Edge cases
# ============================================================================


def test_list_files_empty_result(create_upload_app, auth_headers):
    """
    GIVEN no uploaded files
    WHEN GET /v1/files is called
    THEN response returns empty list with valid pagination structure
    """

    app = create_upload_app()

    with TestClient(app) as client:
        response = _list_files(client, auth_headers)

    assert response.status_code == 200
    body = response.json()

    assert body["success"] is True
    assert body["data"]["files"] == []
    assert body["data"]["pagination"]["count"] == 0
    assert body["data"]["pagination"]["next_cursor"] is None


# ============================================================================
# Metadata edge cases
# ============================================================================


def test_list_files_skips_corrupted_metadata(create_upload_app, auth_headers, tmp_path):
    """
    GIVEN one metadata JSON file corrupted among valid uploads
    WHEN GET /v1/files is called
    THEN request succeeds and corrupted entry is skipped
    """

    app = create_upload_app()

    with TestClient(app) as client:
        records = [
            _upload_file_and_get_data(
                client,
                auth_headers,
                filename=f"corrupt-{index}.txt",
                content=b"corrupt\n",
                content_type="text/plain",
            )
            for index in range(3)
        ]

        corrupted_id = UUID(records[1]["id"])
        meta_path = _meta_path_for(tmp_path, corrupted_id)
        meta_path.write_text("INVALID JSON", encoding="utf-8")

        response = _list_files(client, auth_headers)

    assert response.status_code == 200
    files = response.json()["data"]["files"]
    returned_ids = {item["id"] for item in files}
    assert str(corrupted_id) not in returned_ids


def test_list_files_skips_invalid_schema(create_upload_app, auth_headers, tmp_path):
    """
    GIVEN one metadata JSON with invalid schema among valid uploads
    WHEN GET /v1/files is called
    THEN request succeeds and invalid-schema entry is skipped
    """

    app = create_upload_app()

    with TestClient(app) as client:
        records = [
            _upload_file_and_get_data(
                client,
                auth_headers,
                filename=f"schema-{index}.txt",
                content=b"schema\n",
                content_type="text/plain",
            )
            for index in range(3)
        ]

        invalid_id = UUID(records[0]["id"])
        meta_path = _meta_path_for(tmp_path, invalid_id)
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        payload.pop("mime_type", None)
        meta_path.write_text(json.dumps(payload), encoding="utf-8")

        response = _list_files(client, auth_headers)

    assert response.status_code == 200
    files = response.json()["data"]["files"]
    returned_ids = {item["id"] for item in files}
    assert str(invalid_id) not in returned_ids


def test_list_files_skips_missing_metadata(create_upload_app, auth_headers, tmp_path):
    """
    GIVEN one metadata file removed after successful upload
    WHEN GET /v1/files is called
    THEN request succeeds and missing entry is not returned
    """

    app = create_upload_app()

    with TestClient(app) as client:
        records = [
            _upload_file_and_get_data(
                client,
                auth_headers,
                filename=f"missing-{index}.txt",
                content=b"missing\n",
                content_type="text/plain",
            )
            for index in range(3)
        ]

        missing_id = UUID(records[2]["id"])
        _meta_path_for(tmp_path, missing_id).unlink()

        response = _list_files(client, auth_headers)

    assert response.status_code == 200
    files = response.json()["data"]["files"]
    returned_ids = {item["id"] for item in files}
    assert str(missing_id) not in returned_ids
