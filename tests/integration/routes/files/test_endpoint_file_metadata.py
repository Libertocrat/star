"""Integration tests for the /v1/files/{id} metadata endpoint.

These tests validate the HTTP contract and metadata retrieval behavior for
previously uploaded files.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from star.core.errors import FILE_NOT_FOUND, INVALID_REQUEST, UNAUTHORIZED

# ============================================================================
# Helpers
# ============================================================================


def _upload_text_file_and_get_id(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    filename: str = "sample.txt",
    content: bytes = b"STAR metadata test\n",
) -> UUID:
    """Upload a text file through the HTTP API and return its file UUID.

    Args:
        client: Test client bound to the STAR application.
        auth_headers: Authorization headers for protected endpoints.
        filename: Name to send for the multipart file field.
        content: Raw bytes to upload.

    Returns:
        Parsed file UUID returned by the upload response.
    """

    response = client.post(
        "/v1/files",
        headers=auth_headers,
        files={"file": (filename, content, "text/plain")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    return UUID(body["data"]["file"]["id"])


# ============================================================================
# Happy Path
# ============================================================================


def test_files_metadata_get_returns_metadata_for_existing_file(
    create_upload_app,
    auth_headers,
):
    """
    GIVEN a previously uploaded file
    WHEN GET /v1/files/{id} is called with the valid file UUID
    THEN it returns HTTP 200 with a valid metadata response envelope
    """

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_text_file_and_get_id(client, auth_headers)

        response = client.get(f"/v1/files/{file_id}", headers=auth_headers)

    assert response.status_code == 200
    assert "x-request-id" in response.headers
    assert response.headers["content-type"].startswith("application/json")

    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"] is not None

    file_data = body["data"]["file"]
    assert file_data["id"] == str(file_id)
    assert file_data["original_filename"] == "sample.txt"
    assert file_data["stored_filename"] == f"file_{file_id}.bin"
    assert file_data["mime_type"] == "text/plain"
    assert file_data["extension"] == ".txt"
    assert file_data["size_bytes"] == len(b"STAR metadata test\n")
    assert file_data["status"] == "ready"

    expected_hash = hashlib.sha256(b"STAR metadata test\n").hexdigest()
    assert file_data["sha256"] == expected_hash
    assert len(file_data["sha256"]) == 64

    created = datetime.fromisoformat(file_data["created_at"].replace("Z", "+00:00"))
    updated = datetime.fromisoformat(file_data["updated_at"].replace("Z", "+00:00"))
    assert created == updated
    assert created.tzinfo is not None


# ============================================================================
# Validation and Rejection Paths
# ============================================================================


def test_files_metadata_get_returns_invalid_request_for_missing_file(
    create_upload_app,
    auth_headers,
):
    """
    GIVEN a random UUID that does not exist in STAR metadata storage
    WHEN GET /v1/files/{id} is called
    THEN it returns FILE_NOT_FOUND in the standard error envelope
    """

    app = create_upload_app()
    missing_id = uuid4()

    with TestClient(app) as client:
        response = client.get(f"/v1/files/{missing_id}", headers=auth_headers)

    assert response.status_code == FILE_NOT_FOUND.http_status

    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"] is not None
    assert body["error"]["code"] == FILE_NOT_FOUND.code
    assert body["error"]["message"] == "File not found."


def test_files_metadata_get_rejects_invalid_uuid_format(
    create_upload_app,
    auth_headers,
):
    """
    GIVEN an invalid non-UUID identifier in the route path
    WHEN GET /v1/files/{id} is called
    THEN FastAPI returns HTTP 422 validation failure
    """

    app = create_upload_app()

    with TestClient(app) as client:
        response = client.get("/v1/files/invalid-id", headers=auth_headers)

    assert response.status_code == 422


# ============================================================================
# Authorization Paths
# ============================================================================


def test_files_metadata_get_requires_auth(
    create_upload_app,
):
    """
    GIVEN no Authorization header
    WHEN GET /v1/files/{id} is called
    THEN it returns 401 UNAUTHORIZED
    """

    app = create_upload_app()

    with TestClient(app) as client:
        response = client.get(f"/v1/files/{uuid4()}")

    assert response.status_code == UNAUTHORIZED.http_status

    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == UNAUTHORIZED.code


def test_files_metadata_get_rejects_invalid_token(
    create_upload_app,
):
    """
    GIVEN an invalid Authorization token
    WHEN GET /v1/files/{id} is called
    THEN it returns 401 UNAUTHORIZED
    """

    app = create_upload_app()
    headers = {"Authorization": "Bearer invalid-token"}

    with TestClient(app) as client:
        response = client.get(f"/v1/files/{uuid4()}", headers=headers)

    assert response.status_code == UNAUTHORIZED.http_status

    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == UNAUTHORIZED.code


# ============================================================================
# Invalid JSON Metadata Paths
# ============================================================================


def test_files_metadata_get_invalid_json_metadata(
    create_upload_app,
    tmp_path,
    auth_headers,
):
    """
    GIVEN a metadata file with invalid JSON
    WHEN GET /v1/files/{id} is called
    THEN it returns INVALID_REQUEST
    """

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_text_file_and_get_id(client, auth_headers)

        meta_path = tmp_path / "data" / "files" / "meta" / f"file_{file_id}.json"
        meta_path.write_text("INVALID JSON", encoding="utf-8")

        response = client.get(f"/v1/files/{file_id}", headers=auth_headers)

    assert response.status_code == INVALID_REQUEST.http_status

    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == INVALID_REQUEST.code


def test_files_metadata_get_invalid_schema(
    create_upload_app,
    tmp_path,
    auth_headers,
):
    """
    GIVEN a metadata file with invalid schema
    WHEN GET /v1/files/{id} is called
    THEN it returns INVALID_REQUEST
    """

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_text_file_and_get_id(client, auth_headers)

        meta_path = tmp_path / "data" / "files" / "meta" / f"file_{file_id}.json"
        meta_path.write_text(json.dumps({"invalid": "structure"}), encoding="utf-8")

        response = client.get(f"/v1/files/{file_id}", headers=auth_headers)

    assert response.status_code == INVALID_REQUEST.http_status

    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == INVALID_REQUEST.code
