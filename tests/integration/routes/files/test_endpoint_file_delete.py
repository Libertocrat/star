"""Integration tests for the /v1/files/{id} delete endpoint.

These tests validate metadata-first deletion semantics for STAR-managed files and
standardized STAR error-envelope mappings.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from star.core.errors import (
    FILE_NOT_FOUND,
    INTERNAL_ERROR,
    INVALID_REQUEST,
    UNAUTHORIZED,
)
from star.routes.files.handlers import delete_file as delete_file_handler_module

# ============================================================================
# Helpers
# ============================================================================


def _upload_file_and_get_id(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    filename: str = "delete-me.txt",
    content: bytes = b"delete endpoint payload\n",
) -> UUID:
    """Upload a file through HTTP API and return its UUID."""

    response = client.post(
        "/v1/files",
        headers=auth_headers,
        files={"file": (filename, content, "text/plain")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    return UUID(body["data"]["file"]["id"])


def _meta_path_for(tmp_path: Path, file_id: UUID) -> Path:
    """Return metadata path for a test file id."""

    return tmp_path / "data" / "files" / "meta" / f"file_{file_id}.json"


def _blob_path_for(tmp_path: Path, file_id: UUID) -> Path:
    """Return blob path for a test file id."""

    return tmp_path / "data" / "files" / "blobs" / f"file_{file_id}.bin"


# ============================================================================
# Happy Path
# ============================================================================


def test_files_delete_removes_blob_and_metadata_and_returns_success(
    create_upload_app,
    auth_headers,
    tmp_path,
):
    """
    GIVEN a previously uploaded file
    WHEN DELETE /v1/files/{id} is called with a valid file UUID
    THEN it returns HTTP 200 and removes both blob and metadata artifacts
    """

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(client, auth_headers)

        blob_path = _blob_path_for(tmp_path, file_id)
        meta_path = _meta_path_for(tmp_path, file_id)
        assert blob_path.exists()
        assert meta_path.exists()

        response = client.delete(f"/v1/files/{file_id}", headers=auth_headers)

    assert response.status_code == 200
    assert "x-request-id" in response.headers

    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"]["file"]["id"] == str(file_id)
    assert body["data"]["file"]["deleted"] is True

    assert not blob_path.exists()
    assert not meta_path.exists()


def test_files_delete_returns_not_found_when_metadata_missing(
    create_upload_app,
    auth_headers,
):
    """
    GIVEN a random UUID with no metadata persisted
    WHEN DELETE /v1/files/{id} is called
    THEN it returns FILE_NOT_FOUND
    """

    app = create_upload_app()
    missing_id = uuid4()

    with TestClient(app) as client:
        response = client.delete(f"/v1/files/{missing_id}", headers=auth_headers)

    assert response.status_code == FILE_NOT_FOUND.http_status

    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == FILE_NOT_FOUND.code


def test_files_delete_cleans_metadata_when_blob_missing(
    create_upload_app,
    auth_headers,
    tmp_path,
):
    """
    GIVEN valid metadata but missing blob artifact
    WHEN DELETE /v1/files/{id} is called
    THEN it returns success and removes metadata
    """

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(client, auth_headers)

        blob_path = _blob_path_for(tmp_path, file_id)
        meta_path = _meta_path_for(tmp_path, file_id)
        blob_path.unlink()

        response = client.delete(f"/v1/files/{file_id}", headers=auth_headers)

    assert response.status_code == 200

    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"]["file"]["id"] == str(file_id)
    assert body["data"]["file"]["deleted"] is True

    assert not blob_path.exists()
    assert not meta_path.exists()


def test_files_delete_preserves_artifacts_when_metadata_delete_fails(
    create_upload_app,
    auth_headers,
    tmp_path,
    monkeypatch,
):
    """
    GIVEN a stored file whose metadata cannot be deleted
    WHEN DELETE /v1/files/{id} is called
    THEN it returns INTERNAL_ERROR and preserves both artifacts for retry
    """

    def _fail_delete_metadata(_file_id: UUID, _settings: object = None) -> None:
        """Raise an OS error before metadata is removed."""

        raise OSError("metadata delete failed")

    monkeypatch.setattr(
        delete_file_handler_module,
        "delete_metadata_file",
        _fail_delete_metadata,
    )

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(client, auth_headers)

        blob_path = _blob_path_for(tmp_path, file_id)
        meta_path = _meta_path_for(tmp_path, file_id)

        response = client.delete(f"/v1/files/{file_id}", headers=auth_headers)

    assert response.status_code == INTERNAL_ERROR.http_status

    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == INTERNAL_ERROR.code

    assert blob_path.exists()
    assert meta_path.exists()


def test_files_delete_succeeds_when_blob_cleanup_fails_after_metadata_delete(
    create_upload_app,
    auth_headers,
    tmp_path,
    monkeypatch,
):
    """
    GIVEN a stored file whose blob cleanup fails after metadata deletion
    WHEN DELETE /v1/files/{id} is called
    THEN it returns success and leaves only an internal blob residue
    """

    def _fail_delete_blob(_file_id: UUID, _settings: object = None) -> None:
        """Raise an OS error without removing the blob."""

        raise OSError("blob delete failed")

    monkeypatch.setattr(
        delete_file_handler_module,
        "delete_blob_file",
        _fail_delete_blob,
    )

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(client, auth_headers)

        blob_path = _blob_path_for(tmp_path, file_id)
        meta_path = _meta_path_for(tmp_path, file_id)

        response = client.delete(f"/v1/files/{file_id}", headers=auth_headers)

    assert response.status_code == 200

    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"]["file"]["id"] == str(file_id)
    assert body["data"]["file"]["deleted"] is True

    assert blob_path.exists()
    assert not meta_path.exists()


def test_files_delete_returns_invalid_request_for_corrupted_metadata_json(
    create_upload_app,
    auth_headers,
    tmp_path,
):
    """
    GIVEN metadata JSON exists but is corrupted
    WHEN DELETE /v1/files/{id} is called
    THEN it returns INVALID_REQUEST
    """

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(client, auth_headers)

        meta_path = _meta_path_for(tmp_path, file_id)
        meta_path.write_text("INVALID JSON", encoding="utf-8")

        response = client.delete(f"/v1/files/{file_id}", headers=auth_headers)

    assert response.status_code == INVALID_REQUEST.http_status

    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == INVALID_REQUEST.code


def test_files_delete_returns_invalid_request_for_invalid_metadata_schema(
    create_upload_app,
    auth_headers,
    tmp_path,
):
    """
    GIVEN metadata JSON exists but schema is invalid
    WHEN DELETE /v1/files/{id} is called
    THEN it returns INVALID_REQUEST
    """

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_file_and_get_id(client, auth_headers)

        meta_path = _meta_path_for(tmp_path, file_id)
        meta_path.write_text(json.dumps({"invalid": "schema"}), encoding="utf-8")

        response = client.delete(f"/v1/files/{file_id}", headers=auth_headers)

    assert response.status_code == INVALID_REQUEST.http_status

    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == INVALID_REQUEST.code


# ============================================================================
# Authorization And Path Validation
# ============================================================================


def test_files_delete_requires_auth(
    create_upload_app,
):
    """
    GIVEN no Authorization header
    WHEN DELETE /v1/files/{id} is called
    THEN it returns 401 UNAUTHORIZED
    """

    app = create_upload_app()

    with TestClient(app) as client:
        response = client.delete(f"/v1/files/{uuid4()}")

    assert response.status_code == UNAUTHORIZED.http_status

    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == UNAUTHORIZED.code


def test_files_delete_rejects_invalid_uuid_format(
    create_upload_app,
    auth_headers,
):
    """
    GIVEN an invalid non-UUID identifier
    WHEN DELETE /v1/files/{id} is called
    THEN FastAPI returns 422 validation failure
    """

    app = create_upload_app()

    with TestClient(app) as client:
        response = client.delete("/v1/files/not-a-uuid", headers=auth_headers)

    assert response.status_code == 422
