"""Integration tests for the /v1/files/{id}/content endpoint.

These tests validate streamed binary download semantics, STAR error-envelope
mapping, and storage-state validation behavior.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from star.core.errors import (
    FILE_NOT_FOUND,
    INTERNAL_ERROR,
    INVALID_REQUEST,
    UNAUTHORIZED,
)

# ============================================================================
# Helpers
# ============================================================================


def _upload_file_and_get_id(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    filename: str,
    content: bytes,
    content_type: str,
) -> UUID:
    """Upload a file through the HTTP API and return its file UUID.

    Args:
            client: Test client bound to the STAR application.
            auth_headers: Authorization headers for protected endpoints.
            filename: Name to send for the multipart file field.
            content: Raw bytes to upload.
            content_type: MIME type passed in the multipart request.

    Returns:
            Parsed file UUID returned by the upload response.
    """

    response = client.post(
        "/v1/files",
        headers=auth_headers,
        files={"file": (filename, content, content_type)},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    return UUID(body["data"]["file"]["id"])


def _meta_path_for(tmp_path: Path, file_id: UUID) -> Path:
    """Return metadata JSON path for a test file id."""

    return tmp_path / "data" / "files" / "meta" / f"file_{file_id}.json"


def _blob_path_for(tmp_path: Path, file_id: UUID) -> Path:
    """Return blob path for a test file id."""

    return tmp_path / "data" / "files" / "blobs" / f"file_{file_id}.bin"


@pytest.fixture
def force_path_stat_failure(monkeypatch):
    """Patch blob path resolution to simulate an OSError on stat().

    This fixture replaces the `get_blob_path` function used by the file content
    handler so that it returns a wrapped Path-like object. The wrapped object:

    - Preserves normal behavior for:
        - exists()
        - is_file()
        - name
        - string conversion
    - Raises OSError ONLY when `.stat()` is called

    This ensures:
    - The handler passes all validation checks (exists, is_file)
    - The failure occurs exactly at the size resolution step:
        `blob_path.stat().st_size`
    - The handler's try/except block is exercised correctly

    Args:
        monkeypatch: pytest fixture used to patch runtime behavior.
    """

    from star.routes.files.handlers import get_file_content as files_handler

    original_get_blob_path = files_handler.get_blob_path

    def _patched_get_blob_path(file_id, cfg):
        """Return a wrapped Path object that fails on stat()."""

        real_path = original_get_blob_path(file_id, cfg)

        class WrappedPath:
            """Minimal Path-like wrapper that injects stat() failure."""

            def __init__(self, path):
                """Initialize wrapped path proxy.

                Args:
                    path: Real path instance to proxy.
                """

                self._path = path

            def exists(self) -> bool:
                """Delegate to real Path.exists()."""
                return self._path.exists()

            def is_file(self) -> bool:
                """Delegate to real Path.is_file()."""
                return self._path.is_file()

            def stat(self):
                """Simulate OS-level failure when retrieving file metadata."""
                raise OSError("stat failure")

            @property
            def name(self) -> str:
                """Expose filename for metadata validation."""
                return self._path.name

            def __str__(self) -> str:
                """String representation used in logging."""
                return str(self._path)

            def __fspath__(self):
                """Support os.fspath compatibility if needed."""
                return self._path.__fspath__()

        return WrappedPath(real_path)

    monkeypatch.setattr(
        files_handler,
        "get_blob_path",
        _patched_get_blob_path,
    )


# ============================================================================
# SECTION 1 — Happy path
# ============================================================================


def test_files_content_get_streams_existing_file_successfully(
    create_upload_app,
    auth_headers,
):
    """
    GIVEN a previously uploaded file
    WHEN GET /v1/files/{id}/content is called with a valid file UUID
    THEN it returns HTTP 200 with correct streaming headers and the full binary payload
    """

    app = create_upload_app()
    payload = b"hello STAR content\n"

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(
            client,
            auth_headers,
            filename="sample.txt",
            content=payload,
            content_type="text/plain",
        )

        response = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)

    assert response.status_code == 200
    assert "x-request-id" in response.headers
    assert response.headers["content-type"].startswith("text/plain")
    assert "content-disposition" in response.headers
    assert "attachment" in response.headers["content-disposition"]
    assert "sample.txt" in response.headers["content-disposition"]
    assert "content-length" in response.headers
    assert int(response.headers["content-length"]) == len(payload)
    assert response.content == payload
    assert response.content


def test_files_content_get_stream_integrity_matches_uploaded_hash(
    create_upload_app,
    auth_headers,
):
    """
    GIVEN a previously uploaded file
    WHEN the file is downloaded through GET /v1/files/{id}/content
    THEN the SHA256 hash of the streamed response matches the original uploaded payload
    """

    app = create_upload_app()
    payload = b"deterministic STAR content hash test\n"
    expected_sha = hashlib.sha256(payload).hexdigest()

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(
            client,
            auth_headers,
            filename="hash.txt",
            content=payload,
            content_type="text/plain",
        )

        response = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)

    assert response.status_code == 200
    assert "x-request-id" in response.headers
    assert response.content == payload
    assert hashlib.sha256(response.content).hexdigest() == expected_sha
    assert len(response.content) == len(payload)


@pytest.mark.parametrize(
    "file_type, filename, expected_mime",
    [
        ("text", "file.txt", "text/plain"),
        ("pdf", "file.pdf", "application/pdf"),
        ("png", "file.png", "image/png"),
        ("zip", "file.zip", "application/zip"),
    ],
    ids=["text_file", "pdf_file", "png_file", "zip_file"],
)
def test_files_content_get_supports_multiple_file_types(
    create_upload_app,
    auth_headers,
    file_factory,
    file_type,
    filename,
    expected_mime,
):
    """
    GIVEN previously uploaded files of different supported types
    WHEN each file is downloaded through GET /v1/files/{id}/content
    THEN the endpoint returns HTTP 200, the correct MIME type, and the exact
         original binary content
    """

    app = create_upload_app()
    sf = file_factory(file_type, filename)
    payload = sf.abs_path.read_bytes()

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(
            client,
            auth_headers,
            filename=filename,
            content=payload,
            content_type="application/octet-stream",
        )

        response = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)

    assert response.status_code == 200
    assert "x-request-id" in response.headers
    assert response.headers["content-type"].startswith(expected_mime)
    assert "content-disposition" in response.headers
    assert "attachment" in response.headers["content-disposition"]
    assert filename in response.headers["content-disposition"]
    assert "content-length" in response.headers
    assert int(response.headers["content-length"]) == len(payload)
    assert response.content == payload
    assert response.content


# ============================================================================
# SECTION 2 — Headers validation
# ============================================================================


def test_files_content_get_sets_content_disposition_header(
    create_upload_app,
    auth_headers,
):
    """
    GIVEN a valid previously uploaded file
    WHEN GET /v1/files/{id}/content is called
    THEN the response includes a Content-Disposition header with attachment semantics
         and the original filename
    """

    app = create_upload_app()
    payload = b"report content\n"

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(
            client,
            auth_headers,
            filename="report.txt",
            content=payload,
            content_type="text/plain",
        )

        response = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)

    assert response.status_code == 200
    assert "x-request-id" in response.headers
    assert "content-disposition" in response.headers
    header = response.headers["content-disposition"]
    assert header
    assert "attachment" in header
    assert "filename=" in header
    assert "report.txt" in header


def test_files_content_get_sets_content_length(
    create_upload_app,
    auth_headers,
):
    """
    GIVEN a valid previously uploaded file
    WHEN GET /v1/files/{id}/content is called
    THEN the response includes a Content-Length header that matches
         the exact file size in bytes
    """

    app = create_upload_app()
    payload = b"exact-size-check-123\n"

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(
            client,
            auth_headers,
            filename="length.txt",
            content=payload,
            content_type="text/plain",
        )

        response = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)

    assert response.status_code == 200
    assert "x-request-id" in response.headers
    assert "content-length" in response.headers
    assert int(response.headers["content-length"]) == len(payload)
    assert len(response.content) == len(payload)


# ============================================================================
# SECTION 3 — Authorization
# ============================================================================


def test_files_content_get_requires_auth(
    create_upload_app,
):
    """
    GIVEN no Authorization header
    WHEN GET /v1/files/{id}/content is called
    THEN the endpoint returns HTTP 401 with the standard UNAUTHORIZED response envelope
    """

    app = create_upload_app()

    with TestClient(app) as client:
        response = client.get(f"/v1/files/{uuid4()}/content")

    assert response.status_code == UNAUTHORIZED.http_status
    assert response.headers["content-type"].startswith("application/json")
    if "x-request-id" in response.headers:
        assert response.headers["x-request-id"]

    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"] is not None
    assert body["error"]["code"] == UNAUTHORIZED.code


def test_files_content_get_rejects_invalid_token(
    create_upload_app,
):
    """
    GIVEN an invalid Authorization token
    WHEN GET /v1/files/{id}/content is called
    THEN the endpoint returns HTTP 401 with the standard UNAUTHORIZED response envelope
    """

    app = create_upload_app()
    headers = {"Authorization": "Bearer invalid-token"}

    with TestClient(app) as client:
        response = client.get(f"/v1/files/{uuid4()}/content", headers=headers)

    assert response.status_code == UNAUTHORIZED.http_status
    assert response.headers["content-type"].startswith("application/json")
    if "x-request-id" in response.headers:
        assert response.headers["x-request-id"]

    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"] is not None
    assert body["error"]["code"] == UNAUTHORIZED.code


# ============================================================================
# SECTION 4 — Validation paths
# ============================================================================


def test_files_content_get_returns_file_not_found_when_metadata_missing(
    create_upload_app,
    auth_headers,
):
    """
    GIVEN a random UUID that does not exist in STAR metadata storage
    WHEN GET /v1/files/{id}/content is called
    THEN the endpoint returns FILE_NOT_FOUND in the standard error envelope
    """

    app = create_upload_app()
    missing_id = uuid4()

    with TestClient(app) as client:
        response = client.get(f"/v1/files/{missing_id}/content", headers=auth_headers)

    assert response.status_code == FILE_NOT_FOUND.http_status
    assert response.headers["content-type"].startswith("application/json")
    if "x-request-id" in response.headers:
        assert response.headers["x-request-id"]

    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"] is not None
    assert body["error"]["code"] == FILE_NOT_FOUND.code
    assert body["error"]["message"] == FILE_NOT_FOUND.default_message


def test_files_content_get_returns_file_not_found_when_blob_missing(
    create_upload_app,
    auth_headers,
    tmp_path,
):
    """
    GIVEN valid file metadata exists but the persisted blob has been deleted
    WHEN GET /v1/files/{id}/content is called
    THEN the endpoint returns FILE_NOT_FOUND in the standard error envelope
    """

    app = create_upload_app()
    payload = b"blob missing check\n"

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(
            client,
            auth_headers,
            filename="blob.txt",
            content=payload,
            content_type="text/plain",
        )

        blob_path = _blob_path_for(tmp_path, file_id)
        blob_path.unlink()

        response = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)

    assert response.status_code == FILE_NOT_FOUND.http_status
    assert response.headers["content-type"].startswith("application/json")
    if "x-request-id" in response.headers:
        assert response.headers["x-request-id"]

    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == FILE_NOT_FOUND.code


def test_files_content_get_rejects_invalid_uuid_format(
    create_upload_app,
    auth_headers,
):
    """
    GIVEN an invalid non-UUID identifier in the route path
    WHEN GET /v1/files/{id}/content is called
    THEN FastAPI returns HTTP 422 validation failure
    """

    app = create_upload_app()

    with TestClient(app) as client:
        response = client.get("/v1/files/invalid-id/content", headers=auth_headers)

    assert response.status_code == 422
    if "x-request-id" in response.headers:
        assert response.headers["x-request-id"]


# ============================================================================
# SECTION 5 — Metadata corruption
# ============================================================================


def test_files_content_get_invalid_json_metadata(
    create_upload_app,
    auth_headers,
    tmp_path,
):
    """
    GIVEN a metadata file containing invalid JSON
    WHEN GET /v1/files/{id}/content is called
    THEN the endpoint returns INVALID_REQUEST in the standard error envelope
    """

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(
            client,
            auth_headers,
            filename="invalid-json.txt",
            content=b"invalid-json\n",
            content_type="text/plain",
        )

        meta_path = _meta_path_for(tmp_path, file_id)
        meta_path.write_text("INVALID JSON", encoding="utf-8")

        response = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)

    assert response.status_code == INVALID_REQUEST.http_status
    assert response.headers["content-type"].startswith("application/json")
    if "x-request-id" in response.headers:
        assert response.headers["x-request-id"]

    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"] is not None
    assert body["error"]["code"] == INVALID_REQUEST.code


def test_files_content_get_invalid_schema_metadata(
    create_upload_app,
    auth_headers,
    tmp_path,
):
    """
    GIVEN a metadata file containing valid JSON with an invalid schema
    WHEN GET /v1/files/{id}/content is called
    THEN the endpoint returns INVALID_REQUEST in the standard error envelope
    """

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(
            client,
            auth_headers,
            filename="invalid-schema.txt",
            content=b"invalid-schema\n",
            content_type="text/plain",
        )

        meta_path = _meta_path_for(tmp_path, file_id)
        meta_path.write_text(json.dumps({"invalid": "structure"}), encoding="utf-8")

        response = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)

    assert response.status_code == INVALID_REQUEST.http_status
    assert response.headers["content-type"].startswith("application/json")
    if "x-request-id" in response.headers:
        assert response.headers["x-request-id"]

    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == INVALID_REQUEST.code


def test_files_content_get_metadata_id_mismatch(
    create_upload_app,
    auth_headers,
    tmp_path,
):
    """
    GIVEN metadata JSON exists with embedded id not matching the requested file UUID
    WHEN GET /v1/files/{id}/content is called
    THEN the endpoint returns INVALID_REQUEST in the standard error envelope
    """

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(
            client,
            auth_headers,
            filename="id-mismatch.txt",
            content=b"id-mismatch\n",
            content_type="text/plain",
        )

        meta_path = _meta_path_for(tmp_path, file_id)
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        metadata["id"] = str(uuid4())
        meta_path.write_text(json.dumps(metadata), encoding="utf-8")

        response = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)

    assert response.status_code == INVALID_REQUEST.http_status
    assert response.headers["content-type"].startswith("application/json")
    if "x-request-id" in response.headers:
        assert response.headers["x-request-id"]

    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == INVALID_REQUEST.code


def test_files_content_get_missing_stored_filename(
    create_upload_app,
    auth_headers,
    tmp_path,
):
    """
    GIVEN metadata JSON is otherwise valid but missing stored_filename
    WHEN GET /v1/files/{id}/content is called
    THEN the endpoint returns INVALID_REQUEST in the standard error envelope
    """

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(
            client,
            auth_headers,
            filename="missing-stored.txt",
            content=b"missing-stored\n",
            content_type="text/plain",
        )

        meta_path = _meta_path_for(tmp_path, file_id)
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        metadata["stored_filename"] = ""
        meta_path.write_text(json.dumps(metadata), encoding="utf-8")

        response = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)

    assert response.status_code == INVALID_REQUEST.http_status
    assert response.headers["content-type"].startswith("application/json")
    if "x-request-id" in response.headers:
        assert response.headers["x-request-id"]

    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == INVALID_REQUEST.code


def test_files_content_get_stored_filename_mismatch(
    create_upload_app,
    auth_headers,
    tmp_path,
):
    """
    GIVEN metadata JSON exists but stored_filename does not match the STAR-managed blob
          naming convention for the requested file
    WHEN GET /v1/files/{id}/content is called
    THEN the endpoint returns INVALID_REQUEST in the standard error envelope
    """

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(
            client,
            auth_headers,
            filename="stored-mismatch.txt",
            content=b"stored-mismatch\n",
            content_type="text/plain",
        )

        meta_path = _meta_path_for(tmp_path, file_id)
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        metadata["stored_filename"] = "wrong.bin"
        meta_path.write_text(json.dumps(metadata), encoding="utf-8")

        response = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)

    assert response.status_code == INVALID_REQUEST.http_status
    assert response.headers["content-type"].startswith("application/json")
    if "x-request-id" in response.headers:
        assert response.headers["x-request-id"]

    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == INVALID_REQUEST.code


# ============================================================================
# SECTION 6 — File state validation
# ============================================================================


def test_files_content_get_rejects_non_ready_file(
    create_upload_app,
    auth_headers,
    tmp_path,
):
    """
    GIVEN metadata JSON exists but the file status is not ready
    WHEN GET /v1/files/{id}/content is called
    THEN the endpoint returns INVALID_REQUEST in the standard error envelope
    """

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(
            client,
            auth_headers,
            filename="not-ready.txt",
            content=b"not-ready\n",
            content_type="text/plain",
        )

        meta_path = _meta_path_for(tmp_path, file_id)
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        metadata["status"] = "processing"
        meta_path.write_text(json.dumps(metadata), encoding="utf-8")

        response = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)

    assert response.status_code == INVALID_REQUEST.http_status
    assert response.headers["content-type"].startswith("application/json")
    if "x-request-id" in response.headers:
        assert response.headers["x-request-id"]

    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == INVALID_REQUEST.code


def test_files_content_get_rejects_non_regular_file(
    create_upload_app,
    auth_headers,
    tmp_path,
):
    """
    GIVEN the resolved blob path exists but is not a regular file
    WHEN GET /v1/files/{id}/content is called
    THEN the endpoint returns INVALID_REQUEST in the standard error envelope
    """

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(
            client,
            auth_headers,
            filename="non-regular.txt",
            content=b"non-regular\n",
            content_type="text/plain",
        )

        blob_path = _blob_path_for(tmp_path, file_id)
        blob_path.unlink()
        blob_path.mkdir(parents=False, exist_ok=False)

        response = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)

    assert response.status_code == INVALID_REQUEST.http_status
    assert response.headers["content-type"].startswith("application/json")
    if "x-request-id" in response.headers:
        assert response.headers["x-request-id"]

    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == INVALID_REQUEST.code


# ============================================================================
# SECTION 7 — IO failures
# ============================================================================


def test_files_content_get_handles_metadata_read_os_error(
    create_upload_app,
    auth_headers,
    monkeypatch,
):
    """
    GIVEN reading the metadata file raises an unexpected OSError
    WHEN GET /v1/files/{id}/content is called
    THEN the endpoint returns INTERNAL_ERROR in the standard error envelope
    """

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(
            client,
            auth_headers,
            filename="metadata-oserror.txt",
            content=b"metadata-oserror\n",
            content_type="text/plain",
        )

        def _raise_os_error(*_args, **_kwargs):
            """Raise deterministic OS error for metadata-read failure tests."""
            raise OSError("read failure")

        monkeypatch.setattr(
            "star.routes.files.utils.load_file_metadata",
            _raise_os_error,
        )

        response = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)

    assert response.status_code == INTERNAL_ERROR.http_status
    assert response.headers["content-type"].startswith("application/json")
    if "x-request-id" in response.headers:
        assert response.headers["x-request-id"]

    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == INTERNAL_ERROR.code


def test_files_content_get_handles_stat_failure(
    create_upload_app,
    auth_headers,
    force_path_stat_failure,
):
    """
    GIVEN the handler encounters an unexpected OS-level failure while resolving
          file content
    WHEN GET /v1/files/{id}/content is called
    THEN the endpoint returns INTERNAL_ERROR in the standard error envelope
    """

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(
            client,
            auth_headers,
            filename="stat-oserror.txt",
            content=b"stat-oserror\n",
            content_type="text/plain",
        )

        response = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)

    assert response.status_code == INTERNAL_ERROR.http_status
    assert response.headers["content-type"].startswith("application/json")
    if "x-request-id" in response.headers:
        assert response.headers["x-request-id"]

    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == INTERNAL_ERROR.code


# ============================================================================
# SECTION 8 — Streaming behavior
# ============================================================================


def test_files_content_get_streams_large_file_without_memory_issues(
    create_upload_app,
    auth_headers,
):
    """
    GIVEN a previously uploaded large file
    WHEN GET /v1/files/{id}/content is called
    THEN the endpoint returns HTTP 200 and the full payload without truncation
         or content corruption
    """

    app = create_upload_app()
    payload = b"A" * (1024 * 1024)

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(
            client,
            auth_headers,
            filename="large.txt",
            content=payload,
            content_type="text/plain",
        )

        response = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)

    assert response.status_code == 200
    assert "x-request-id" in response.headers
    assert "content-length" in response.headers
    assert int(response.headers["content-length"]) == len(payload)
    assert len(response.content) == len(payload)
    assert response.content == payload


def test_files_content_get_stream_iterator_produces_full_content(
    create_upload_app,
    auth_headers,
):
    """
    GIVEN a previously uploaded file
    WHEN GET /v1/files/{id}/content is called
    THEN the streamed response body contains the complete original payload
         from first byte to last byte
    """

    app = create_upload_app()
    payload = b"STAR stream iterator content test\nwith multiple lines\n"

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(
            client,
            auth_headers,
            filename="iterator.txt",
            content=payload,
            content_type="text/plain",
        )

        response = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)

    assert response.status_code == 200
    assert "x-request-id" in response.headers
    assert len(response.content) == len(payload)
    assert response.content == payload
    assert response.content[0] == payload[0]
    assert response.content[-1] == payload[-1]
