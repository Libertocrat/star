"""Integration tests for the /v1/files upload endpoint.

These tests validate the HTTP contract and persistence behavior for file uploads.
They ensure STAR stores validated files as blob + metadata JSON and rejects invalid
uploads with stable response envelopes.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import UUID

from fastapi.testclient import TestClient

from star.core.errors import (
    FILE_TOO_LARGE,
    INVALID_REQUEST,
    MIME_MAPPING_NOT_DEFINED,
    UNAUTHORIZED,
    UNSUPPORTED_MEDIA_TYPE,
)
from star.core.schemas.files import FileMetadata
from star.core.utils.file_storage import (
    get_blob_dir,
    get_blob_path,
    get_meta_dir,
    get_meta_path,
    get_secret_tmp_dir,
    get_tmp_dir,
    load_file_metadata,
)

# ============================================================================
# Startup / initialization
# ============================================================================


def test_files_startup_creates_storage_directories(
    create_upload_app,
):
    """Validate startup initialization of STAR-managed storage directories.

    GIVEN an isolated STAR_ROOT_DIR for the test app
    WHEN the application is created
    THEN storage and runtime secret directories exist.
    """

    app = create_upload_app()
    settings = app.state.settings

    assert get_blob_dir(settings).exists()
    assert get_blob_dir(settings).is_dir()

    assert get_meta_dir(settings).exists()
    assert get_meta_dir(settings).is_dir()

    assert get_tmp_dir(settings).exists()
    assert get_tmp_dir(settings).is_dir()

    assert get_secret_tmp_dir(settings).exists()
    assert get_secret_tmp_dir(settings).is_dir()


# ============================================================================
# Happy path
# ============================================================================


def test_files_upload_persists_blob_and_metadata_and_returns_envelope(
    create_upload_app,
    auth_headers,
):
    """Validate successful upload persistence and response envelope.

    GIVEN a valid multipart upload request
    WHEN the /v1/files endpoint is called
    THEN it returns HTTP 201 and persists blob + typed metadata JSON.
    """

    app = create_upload_app()
    payload = b"Hello STAR upload\n"
    expected_sha = hashlib.sha256(payload).hexdigest()

    with TestClient(app) as client:
        response = client.post(
            "/v1/files",
            headers=auth_headers,
            files={"file": ("sample.txt", payload, "text/plain")},
        )

    assert response.status_code == 201
    assert "x-request-id" in response.headers
    assert response.headers["content-type"].startswith("application/json")

    body = response.json()

    assert body["success"] is True
    assert body["error"] is None
    assert body["data"] is not None

    file_data = body["data"]["file"]
    file_id = UUID(file_data["id"])
    settings = app.state.settings

    assert file_data["stored_filename"] == f"file_{file_id}.bin"
    assert file_data["original_filename"] == "sample.txt"
    assert file_data["mime_type"] == "text/plain"
    assert file_data["extension"] == ".txt"
    assert file_data["size_bytes"] == len(payload)
    assert file_data["sha256"] == expected_sha
    assert file_data["status"] == "ready"

    created = datetime.fromisoformat(file_data["created_at"].replace("Z", "+00:00"))
    updated = datetime.fromisoformat(file_data["updated_at"].replace("Z", "+00:00"))
    assert created == updated
    assert created.tzinfo is not None

    blob_path = get_blob_path(file_id, settings)
    meta_path = get_meta_path(file_id, settings)

    assert blob_path.exists()
    assert blob_path.read_bytes() == payload

    assert meta_path.exists()
    stored_metadata = load_file_metadata(file_id, settings=settings)
    assert isinstance(stored_metadata, FileMetadata)
    assert stored_metadata.id == file_id
    assert stored_metadata.sha256 == expected_sha
    assert stored_metadata.stored_filename == blob_path.name
    assert stored_metadata.model_dump(mode="json") == file_data


def test_files_upload_accepts_matching_checksum(
    create_upload_app,
    auth_headers,
):
    """Validate checksum verification when checksum matches uploaded payload.

    GIVEN a valid upload with a matching optional checksum
    WHEN the /v1/files endpoint is called
    THEN it returns HTTP 201 and persists the uploaded file.
    """

    app = create_upload_app()
    payload = b"checksum-ok\n"
    checksum = hashlib.sha256(payload).hexdigest()

    with TestClient(app) as client:
        response = client.post(
            "/v1/files",
            headers=auth_headers,
            files={"file": ("checksum.txt", payload, "text/plain")},
            data={"checksum": checksum},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"]["file"]["sha256"] == checksum


# ============================================================================
# Authorization paths
# ============================================================================


def test_files_upload_requires_auth(
    create_upload_app,
):
    """
    GIVEN no Authorization header
    WHEN POST /v1/files is called
    THEN it returns 401 UNAUTHORIZED
    """

    app = create_upload_app()

    with TestClient(app) as client:
        response = client.post(
            "/v1/files",
            files={"file": ("sample.txt", b"missing auth\n", "text/plain")},
        )

    assert response.status_code == UNAUTHORIZED.http_status

    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == UNAUTHORIZED.code


def test_files_upload_rejects_invalid_token(
    create_upload_app,
):
    """
    GIVEN an invalid Authorization token
    WHEN POST /v1/files is called
    THEN it returns 401 UNAUTHORIZED
    """

    app = create_upload_app()
    headers = {"Authorization": "Bearer invalid-token"}

    with TestClient(app) as client:
        response = client.post(
            "/v1/files",
            headers=headers,
            files={"file": ("sample.txt", b"invalid token\n", "text/plain")},
        )

    assert response.status_code == UNAUTHORIZED.http_status

    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == UNAUTHORIZED.code


# ============================================================================
# Validation / rejection paths
# ============================================================================


def test_files_upload_rejects_checksum_mismatch_without_persisting_artifacts(
    create_upload_app,
    auth_headers,
):
    """Validate mismatch checksum rejection and storage cleanup behavior.

    GIVEN a valid upload with a non-matching checksum
    WHEN the /v1/files endpoint is called
    THEN it returns INVALID_REQUEST and no blob/meta/tmp artifact remains.
    """

    app = create_upload_app()
    wrong_checksum = "0" * 64

    with TestClient(app) as client:
        response = client.post(
            "/v1/files",
            headers=auth_headers,
            files={"file": ("checksum.txt", b"checksum-fail\n", "text/plain")},
            data={"checksum": wrong_checksum},
        )

    assert response.status_code == INVALID_REQUEST.http_status
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == INVALID_REQUEST.code

    settings = app.state.settings
    assert list(get_blob_dir(settings).iterdir()) == []
    assert list(get_meta_dir(settings).iterdir()) == []
    assert list(get_tmp_dir(settings).iterdir()) == []


def test_files_upload_rejects_empty_file_without_persisting_artifacts(
    create_upload_app,
    auth_headers,
):
    """Validate empty upload rejection and strict cleanup behavior.

    GIVEN an empty multipart file payload
    WHEN the /v1/files endpoint is called
    THEN it returns INVALID_REQUEST and no blob/meta/tmp artifact remains.
    """

    app = create_upload_app()

    with TestClient(app) as client:
        response = client.post(
            "/v1/files",
            headers=auth_headers,
            files={"file": ("empty.txt", b"", "text/plain")},
        )

    assert response.status_code == INVALID_REQUEST.http_status
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == INVALID_REQUEST.code

    settings = app.state.settings
    assert list(get_blob_dir(settings).iterdir()) == []
    assert list(get_meta_dir(settings).iterdir()) == []
    assert list(get_tmp_dir(settings).iterdir()) == []


def test_files_upload_rejects_invalid_checksum_format_without_persisting_artifacts(
    create_upload_app,
    auth_headers,
):
    """Validate malformed checksum input rejection and cleanup behavior.

    GIVEN a multipart upload with malformed checksum input
    WHEN the /v1/files endpoint is called
    THEN it returns INVALID_REQUEST and no blob/meta/tmp artifact remains.
    """

    app = create_upload_app()

    with TestClient(app) as client:
        response = client.post(
            "/v1/files",
            headers=auth_headers,
            files={"file": ("checksum.txt", b"checksum-fail\n", "text/plain")},
            data={"checksum": "invalid_sha256"},
        )

    assert response.status_code == INVALID_REQUEST.http_status
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == INVALID_REQUEST.code

    settings = app.state.settings
    assert list(get_blob_dir(settings).iterdir()) == []
    assert list(get_meta_dir(settings).iterdir()) == []
    assert list(get_tmp_dir(settings).iterdir()) == []


def test_files_upload_rejects_unknown_extension_without_persisting_artifacts(
    create_upload_app,
    auth_headers,
):
    """Validate unknown extension rejection and strict cleanup behavior.

    GIVEN a multipart upload with extension that has no MIME mapping
    WHEN the /v1/files endpoint is called
    THEN it returns MIME_MAPPING_NOT_DEFINED and no artifact is persisted.
    """

    app = create_upload_app()

    with TestClient(app) as client:
        response = client.post(
            "/v1/files",
            headers=auth_headers,
            files={"file": ("payload.unknown", b"plain content\n", "text/plain")},
        )

    assert response.status_code == MIME_MAPPING_NOT_DEFINED.http_status
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == MIME_MAPPING_NOT_DEFINED.code

    settings = app.state.settings
    assert list(get_blob_dir(settings).iterdir()) == []
    assert list(get_meta_dir(settings).iterdir()) == []
    assert list(get_tmp_dir(settings).iterdir()) == []


def test_files_upload_rejects_mime_extension_mismatch_and_cleans_temp(
    create_upload_app,
    auth_headers,
    file_factory,
):
    """Validate MIME/extension mismatch rejection and artifact cleanup.

    GIVEN a payload whose content MIME does not match the filename extension
    WHEN the /v1/files endpoint is called
    THEN it returns UNSUPPORTED_MEDIA_TYPE and persists nothing.
    """

    app = create_upload_app()
    png_file = file_factory("png", "image.png")

    with TestClient(app) as client:
        response = client.post(
            "/v1/files",
            headers=auth_headers,
            files={
                "file": (
                    "image.txt",
                    png_file.abs_path.read_bytes(),
                    "application/octet-stream",
                )
            },
        )

    assert response.status_code == UNSUPPORTED_MEDIA_TYPE.http_status
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == UNSUPPORTED_MEDIA_TYPE.code

    settings = app.state.settings
    assert list(get_blob_dir(settings).iterdir()) == []
    assert list(get_meta_dir(settings).iterdir()) == []
    assert list(get_tmp_dir(settings).iterdir()) == []


def test_files_upload_rejects_executable_content_by_default(
    create_upload_app,
    auth_headers,
    file_factory,
):
    """Validate executable upload rejection policy.

    GIVEN a multipart upload containing executable content
    WHEN the /v1/files endpoint is called
    THEN it returns UNSUPPORTED_MEDIA_TYPE and no artifact is persisted.
    """

    app = create_upload_app()
    exe_file = file_factory("exe", "malware.exe")

    with TestClient(app) as client:
        response = client.post(
            "/v1/files",
            headers=auth_headers,
            files={
                "file": (
                    "malware.exe",
                    exe_file.abs_path.read_bytes(),
                    "application/octet-stream",
                )
            },
        )

    assert response.status_code == UNSUPPORTED_MEDIA_TYPE.http_status
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == UNSUPPORTED_MEDIA_TYPE.code

    settings = app.state.settings
    assert list(get_blob_dir(settings).iterdir()) == []
    assert list(get_meta_dir(settings).iterdir()) == []
    assert list(get_tmp_dir(settings).iterdir()) == []


def test_files_upload_enforces_max_size_limit(
    create_upload_app,
    auth_headers,
):
    """Validate max upload size enforcement for oversized payloads.

    GIVEN a STAR configuration with very small max body size
    WHEN an oversized multipart upload is sent to /v1/files
    THEN the request is rejected with FILE_TOO_LARGE behavior.
    """

    app = create_upload_app(max_file_bytes=32)

    with TestClient(app) as client:
        response = client.post(
            "/v1/files",
            headers=auth_headers,
            files={
                "file": (
                    "too-large.txt",
                    b"A" * 8192,
                    "text/plain",
                )
            },
        )

    assert response.status_code == FILE_TOO_LARGE.http_status
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == FILE_TOO_LARGE.code

    settings = app.state.settings
    assert list(get_blob_dir(settings).iterdir()) == []
    assert list(get_meta_dir(settings).iterdir()) == []
    assert list(get_tmp_dir(settings).iterdir()) == []
